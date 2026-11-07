"""Manual, local-only recording collector for the frozen stage-1 ASR dataset."""
from __future__ import annotations

import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
import uuid
import wave

import numpy as np

from acceptance_eval import evaluate_asr
from prepare_audio import prepare


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def atomic_json(path, value):
    temporary = Path(str(path) + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


class Collector:
    WARMUP_SECONDS = 0.75
    CAPTURE_MAX_SECONDS = 12

    def __init__(self, base, definitions, engine, capture_command='arecord', runtime_info=None):
        self.base = Path(base)
        self.definitions_path = Path(definitions)
        data = self.definitions_path.read_bytes()
        self.definitions_hash = hashlib.sha256(data).hexdigest()
        self.definitions = json.loads(data)
        self.required = self.definitions['acceptance']['asr']['required_case_ids']
        self.references = {c['case_id']: c['reference_text'] for c in self.definitions['asr_cases']}
        self.engine = engine
        self.capture_command = capture_command
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.phase = 'idle'
        self.message = '选择一句，点击开始录音。'
        self.case_id = None
        self.last_result = None
        self.recording_started = None
        self.process = None
        self.worker = None
        self.rounds_dir = self.base / 'results' / 'rounds'
        self.rounds_dir.mkdir(parents=True, exist_ok=True)
        self.current_path = self.base / 'results' / 'current-round.json'
        self.config = {
            'device': 'hw:CARD=sndes8326,DEV=0', 'source_rate': 48000, 'source_channels': 2,
            'source_pcm': 'S16_LE', 'warmup_trim_seconds': self.WARMUP_SECONDS,
            'capture_max_seconds': self.CAPTURE_MAX_SECONDS,
            'gain_policy': 'up to +24dB, reduced if source peak would exceed 25000',
            'target_rate': 16000, 'target_channels': 1, 'asr_provider': 'cpu', 'threads': 4,
            'retake_policy': 'recognized cases are immutable in a round; all rounds are retained',
            'runtime': runtime_info or {},
        }
        config_bytes = json.dumps(self.config, sort_keys=True).encode()
        self.config_hash = hashlib.sha256(config_bytes).hexdigest()
        if self.current_path.exists():
            marker = json.loads(self.current_path.read_text())
            candidate = self.rounds_dir / marker['round_id'] / 'results.json'
            loaded = json.loads(candidate.read_text())
            if loaded['dataset_sha256'] != self.definitions_hash or loaded['config_sha256'] != self.config_hash:
                raise ValueError('Existing round uses different definitions/configuration; preserve it and choose another validation base.')
            self.results = loaded
            self.round_dir = candidate.parent
        else:
            self._new_round()

    def _new_round(self):
        # The board's clock is not synchronized with the client's date.
        round_id = 'round-' + uuid.uuid4().hex[:12]
        self.round_dir = self.rounds_dir / round_id
        self.round_dir.mkdir()
        self.results = {
            'dataset_id': self.definitions['dataset_id'], 'dataset_sha256': self.definitions_hash,
            'round_id': round_id, 'config': self.config, 'config_sha256': self.config_hash,
            'board_clock_at_creation_utc': datetime.now(timezone.utc).isoformat(),
            'clock_note': 'Board clock is unsynchronized; monotonic time is used for durations.',
            'recording_source': 'real_board_microphone', 'cases': {},
        }
        self._save()
        atomic_json(self.current_path, {'round_id': round_id})

    def _save(self):
        atomic_json(self.round_dir / 'results.json', self.results)
        assessment = evaluate_asr(self.definitions, self.results, expected_dataset_sha256=self.definitions_hash)
        atomic_json(self.round_dir / 'assessment.json', assessment)

    def busy(self):
        return (self.phase in ('preparing', 'recording', 'processing')
                or (self.worker is not None and self.worker.is_alive()))

    def state(self):
        with self.lock:
            assessment = evaluate_asr(self.definitions, self.results, expected_dataset_sha256=self.definitions_hash)
            rows = []
            for cid in self.required:
                item = self.results['cases'].get(cid, {})
                scored = assessment['cases'].get(cid, {})
                rows.append({
                    'case_id': cid, 'reference_text': self.references[cid],
                    'recorded': item.get('status') == 'recognized',
                    'has_audio': bool(item.get('prepared_audio_path')),
                    'hypothesis': item.get('hypothesis'), 'case_cer': scored.get('cer'),
                })
            return {
                'phase': self.phase, 'message': self.message, 'case_id': self.case_id,
                'round_id': self.results['round_id'], 'total_cases': len(self.required),
                'completed_count': assessment['completed_cases'], 'cases': rows,
                'score': assessment, 'last_result': self.last_result,
                'elapsed_seconds': round(time.monotonic() - self.recording_started, 1) if self.phase == 'recording' else 0,
            }

    def start(self, case_id):
        with self.lock:
            if case_id not in self.required:
                raise ValueError('未知样本编号。')
            if self.busy():
                raise ValueError('当前录音或识别尚未完成。')
            previous = self.results['cases'].get(case_id, {})
            if previous.get('status') == 'recognized':
                raise ValueError('本轮已完成此句，不能覆盖；复测请另开一轮。')
            self.stop_event.clear()
            self.case_id = case_id
            self.last_result = None
            self.phase = 'processing' if previous.get('prepared_audio_path') else 'preparing'
            self.message = '重试识别同一段录音。' if previous.get('prepared_audio_path') else '正在准备麦克风，请先不要说话。'
            self.worker = threading.Thread(target=self._capture_and_recognize, args=(case_id, previous), daemon=True)
            self.worker.start()

    def stop(self):
        with self.lock:
            if self.phase != 'recording':
                raise ValueError('当前不是录音状态。')
            self.stop_event.set()

    def new_round(self):
        with self.lock:
            if self.busy():
                raise ValueError('请先等待当前操作完成。')
            self._new_round()
            self.phase, self.message, self.case_id, self.last_result = 'idle', '新一轮已准备，旧轮结果保留。', None, None

    def _capture_and_recognize(self, case_id, previous):
        attempt = self.round_dir / (case_id + '-' + uuid.uuid4().hex[:8])
        attempt.mkdir()
        record = dict(previous) if previous.get('prepared_audio_path') else {
            'status': 'recording', 'case_id': case_id, 'reference_text': self.references[case_id],
            'dataset_sha256': self.definitions_hash, 'config_sha256': self.config_hash,
        }
        try:
            if not previous.get('prepared_audio_path'):
                raw, prepared = attempt / 'raw.wav', attempt / 'prepared.wav'
                with (attempt / 'capture.log').open('wb') as log:
                    process = subprocess.Popen([
                        self.capture_command, '-q', '-D', self.config['device'], '-f', 'S16_LE',
                        '-r', '48000', '-c', '2', '-d', str(self.CAPTURE_MAX_SECONDS), str(raw),
                    ], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log)
                    with self.lock:
                        self.process = process
                    time.sleep(self.WARMUP_SECONDS)
                    if process.poll() is not None:
                        raise RuntimeError('录音未能启动：' + (attempt / 'capture.log').read_text(errors='replace')[-600:])
                    with self.lock:
                        self.phase, self.message = 'recording', '现在朗读屏幕上的句子，读完点击停止。'
                        self.recording_started = time.monotonic()
                    while process.poll() is None:
                        if self.stop_event.wait(0.05):
                            process.send_signal(signal.SIGINT)
                            break
                    try:
                        code = process.wait(timeout=4)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                        raise RuntimeError('录音进程未能正常停止。')
                    if code not in (0, -signal.SIGINT, 128 + signal.SIGINT):
                        raise RuntimeError('录音失败：' + (attempt / 'capture.log').read_text(errors='replace')[-600:])
                with self.lock:
                    self.process = None
                    self.phase, self.message = 'processing', '录音完成，正在本地识别。'
                with wave.open(str(raw), 'rb') as w:
                    if w.getnframes() / w.getframerate() < self.WARMUP_SECONDS + 0.5:
                        raise ValueError('录音过短，请重新采集。')
                    samples = np.frombuffer(w.readframes(w.getnframes()), dtype='<i2').reshape(-1, w.getnchannels())
                mono = samples[int(48000 * self.WARMUP_SECONDS):].astype(np.float64).mean(axis=1)
                peak = float(np.max(np.abs(mono)))
                rms = float(np.sqrt(np.mean(mono ** 2)))
                if rms < 2:
                    raise ValueError('录音接近静音，请检查麦克风并重新采集。')
                gain = min(24.0, 20 * math.log10(25000 / max(peak, 1)))
                audio_info = prepare(raw, prepared, target_rate=16000, trim_start=self.WARMUP_SECONDS, gain_db=gain)
                record.update({
                    'status': 'prepared', 'raw_audio_path': str(raw), 'raw_audio_sha256': digest(raw),
                    'prepared_audio_path': str(prepared), 'prepared_audio_sha256': audio_info['output_sha256'],
                    'audio_preparation': audio_info, 'raw_rms_after_warmup': rms,
                })
                # Commit valid audio before ASR; retries use this exact audio, never recapture it silently.
                with self.lock:
                    self.results['cases'][case_id] = record.copy()
                    self._save()
            else:
                for key in ('raw_audio', 'prepared_audio'):
                    if digest(record[key + '_path']) != record[key + '_sha256']:
                        raise ValueError('既有录音哈希不一致，不能继续本轮。')
            started = time.perf_counter()
            result = self.engine.recognize_file(record['prepared_audio_path'])
            wall = time.perf_counter() - started
            record.update({
                'status': 'recognized', 'hypothesis': result.text, 'recognition_wall_seconds': wall,
                'audio_duration_ms_sdk': result.audio_duration_ms,
                'processing_time_ms_sdk': result.processing_time_ms, 'rtf_sdk': result.rtf,
            })
            record.pop('error', None)
            atomic_json(attempt / 'result.json', record)
            with self.lock:
                self.results['cases'][case_id] = record
                self._save()
                self.phase, self.message = 'idle', '此句已记录，请查看转写结果并继续下一句。'
                self.last_result = dict(record)
        except Exception as exc:
            record.update({'status': 'error', 'error': str(exc)})
            atomic_json(attempt / 'result.json', record)
            with self.lock:
                if record.get('prepared_audio_path'):
                    self.results['cases'][case_id] = record
                    self._save()
                self.phase, self.message = 'error', str(exc)
                self.last_result = dict(record)
        finally:
            with self.lock:
                process, self.process = self.process, None
            if process is not None and process.poll() is None:
                process.send_signal(signal.SIGINT)
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()

    def close(self):
        self.stop_event.set()
        process = self.process
        if process is not None and process.poll() is None:
            process.send_signal(signal.SIGINT)
        if self.worker is not None:
            self.worker.join(timeout=10)


def make_handler(app, page):
    class Handler(BaseHTTPRequestHandler):
        def json_response(self, value, status=200):
            body = json.dumps(value, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == '/api/state':
                self.json_response(app.state())
            elif self.path == '/':
                body = Path(page).read_bytes()
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.json_response({'error': 'Not found'}, 404)

        def do_POST(self):
            origin = self.headers.get('Origin')
            host = self.headers.get('Host', '')
            if origin and origin != 'http://' + host:
                self.json_response({'error': 'Origin mismatch'}, 403)
                return
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 <= size <= 2048:
                    raise ValueError('Invalid request size')
                payload = json.loads(self.rfile.read(size) or b'{}')
                if not isinstance(payload, dict):
                    raise ValueError('Expected an object')
                if self.path == '/api/start':
                    app.start(payload.get('case_id'))
                elif self.path == '/api/stop':
                    app.stop()
                elif self.path == '/api/new-round':
                    app.new_round()
                else:
                    self.json_response({'error': 'Not found'}, 404)
                    return
                self.json_response(app.state())
            except (ValueError, TypeError) as exc:
                self.json_response({'error': str(exc)}, 409)

        def log_message(self, *_args):
            pass
    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', type=Path, default=Path('/home/haotian/rv-doctor-edge-validation/asr'))
    parser.add_argument('--port', type=int, default=18791)
    args = parser.parse_args()
    import spacemit_asr as asr
    resources = json.loads((args.base / 'models/sensevoice/manifest.json').read_text())
    for resource in resources:
        path = args.base / 'models/sensevoice' / resource['file']
        if path.stat().st_size != resource['bytes'] or digest(path) != resource['sha256']:
            raise RuntimeError('Model resource verification failed: ' + resource['file'])
    cfg = asr.Config(model_dir=str(args.base / 'models/sensevoice'), backend='sensevoice')
    cfg.language = asr.Language.ZH
    cfg._config.extra_params = {'provider': 'cpu'}
    cfg._config.num_threads = 4
    engine = asr.Engine(cfg)
    engine.initialize()
    app = Collector(args.base, args.base / 'acceptance-cases.json', engine,
                    runtime_info={'sdk_version': asr.__version__, 'python_version': sys.version,
                                  'model_resources': resources})
    server = ThreadingHTTPServer(('127.0.0.1', args.port), make_handler(app, args.base / 'collector.html'))
    print('COLLECTOR_READY http://127.0.0.1:{}'.format(args.port), flush=True)
    def stop_signal(_signum, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop_signal)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        app.close()
        if app.worker is None or not app.worker.is_alive():
            engine.shutdown()


if __name__ == '__main__':
    main()
