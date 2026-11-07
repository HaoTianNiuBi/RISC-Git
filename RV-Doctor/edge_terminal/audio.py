"""Stage-1 audio settings, shared by live capture and audio replay."""

import hashlib
import json
import math
from pathlib import Path
import signal
import subprocess
import time
import wave


WARMUP_SECONDS = 0.75
CAPTURE_SECONDS = 12
DEVICE = 'hw:CARD=sndes8326,DEV=0'
FILTER = ('highpass=f=110:p=2,highpass=f=110:p=2,volume=12dB,'
          'alimiter=limit=0.76:attack=2:release=60:level=0:latency=1')


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def capture(path, stop, ready):
    """Capture one WAV. Only the UI can start a new recording."""
    with path.with_name('capture.log').open('wb') as log:
        with subprocess.Popen(
            ['arecord', '-q', '-D', DEVICE, '-f', 'S16_LE', '-r', '48000', '-c', '2',
             '-d', str(CAPTURE_SECONDS), str(path)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log,
        ) as process:
            if stop.wait(WARMUP_SECONDS):
                process.terminate()
                raise ValueError('录音已取消。')
            if process.poll() is not None:
                raise ValueError('麦克风未能启动，请检查耳麦连接和占用状态。')
            ready()
            while process.poll() is None:
                if stop.wait(0.05):
                    if process.poll() is None:
                        process.send_signal(signal.SIGINT)
                    break
            try:
                code = process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                process.kill()
                raise ValueError('麦克风停止超时，请重试。') from None
            if code not in (0, -signal.SIGINT, 128 + signal.SIGINT):
                # 本板 arecord（Bianbu/LXQt 自带版本）收到 SIGINT 时返回 1 且不写 stderr，
                # 实测 3/3 复现；只接受 0/-2/130 会把「用户点停止」误判为采集失败。
                # 判据改为：退出码为 1 时，只要文件已落盘且长于 WAV 头，就按主动停止处理；
                # 空文件或文件缺失仍然视为真正的采集失败。
                size = path.stat().st_size if path.exists() else 0
                if code != 1 or size <= 44:
                    raise ValueError('录音失败，请检查耳麦连接。')


class AudioPipeline:
    def __init__(self, model_dir):
        import numpy as np
        import spacemit_asr as asr
        from prepare_audio import prepare

        self.np = np
        self.prepare = prepare
        manifest = json.loads((model_dir / 'manifest.json').read_text(encoding='utf-8'))
        for item in manifest:
            path = model_dir / item['file']
            if path.stat().st_size != item['bytes'] or digest(path) != item['sha256']:
                raise ValueError('SenseVoice model verification failed: ' + item['file'])
        config = asr.Config(model_dir=str(model_dir), backend='sensevoice')
        config.language = asr.Language.ZH
        config._config.extra_params = {'provider': 'cpu'}
        config._config.num_threads = 4
        config._config.vad_enabled = False
        self.metadata = {
            'sdk_version': asr.__version__, 'backend': 'sensevoice', 'language': 'zh',
            'provider': 'cpu', 'threads': 4, 'vad_enabled': False, 'model_manifest': manifest,
            'filter': FILTER, 'warmup_trim_seconds': WARMUP_SECONDS, 'device': DEVICE,
            'ffmpeg': subprocess.check_output(['ffmpeg', '-version'], text=True).splitlines()[0],
        }
        self.engine = asr.Engine(config)
        self.engine.initialize()

    def transcribe(self, raw, directory):
        started = time.perf_counter()
        np = self.np
        with wave.open(str(raw), 'rb') as audio:
            rate, channels, width = audio.getframerate(), audio.getnchannels(), audio.getsampwidth()
            if (rate, channels, width) != (48000, 2, 2):
                raise ValueError('录音格式须为 48 kHz 双声道 PCM16。')
            if audio.getnframes() / rate < WARMUP_SECONDS + 0.5:
                raise ValueError('录音太短，请重新读一遍。')
            samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype='<i2').reshape(-1, channels)
        mono = samples[round(rate * WARMUP_SECONDS):].astype(float).mean(axis=1)
        rms = float(np.sqrt(np.mean(mono ** 2)))
        if rms < 2:
            raise ValueError('录音接近静音，请检查麦克风。')
        peak = float(np.max(np.abs(mono)))
        gain = min(24.0, 20 * math.log10(25000 / max(peak, 1)))
        prepared = directory / 'prepared.wav'
        filtered = directory / 'filtered.wav'
        preparation = self.prepare(raw, prepared, target_rate=16000,
                                   trim_start=WARMUP_SECONDS, gain_db=gain)
        subprocess.run(
            ['ffmpeg', '-nostdin', '-v', 'error', '-n', '-i', str(prepared), '-af', FILTER,
             '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', str(filtered)],
            check=True, timeout=15,
        )
        recognition_started = time.perf_counter()
        result = self.engine.recognize_file(str(filtered))
        recognition_seconds = time.perf_counter() - recognition_started
        return {
            'raw_audio_path': str(raw), 'raw_audio_sha256': preparation['source_sha256'],
            'prepared_audio_path': str(filtered), 'prepared_audio_sha256': digest(filtered),
            'audio_preparation': preparation, 'raw_asr_text': result.text,
            'preprocess_wall_seconds': recognition_started - started,
            'recognition_wall_seconds': recognition_seconds,
            'audio_duration_ms': result.audio_duration_ms,
        }

    def close(self):
        self.engine.shutdown()
