"""One offline SenseVoice probe; it does not score the formal acceptance set."""
import hashlib
import json
import platform
import sys
import time
import traceback
import wave
from pathlib import Path

BASE = Path('/home/haotian/rv-doctor-edge-validation/asr')
MODEL = BASE / 'models/sensevoice'
INPUT = BASE / 'input/mic-smoke-16k-mono.wav'
OUTPUT = BASE / 'results/sensevoice-smoke-cpu.json'


def file_hash(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


report = {
    'purpose': 'stage1_component_smoke_not_formal_acceptance',
    'formal_acceptance': 'not_run', 'provider_requested': 'cpu', 'num_threads': 4,
    'platform': platform.platform(), 'python_version': sys.version,
    'model_dir': str(MODEL), 'input_file': str(INPUT),
    'expected_input_sha256': '546bcf1aa64dfaa71b30405bf3e76771da58fe22c2edd26d03abbefd64deb837',
    'note': 'Existing microphone check clip; exact reference/count was not frozen, so CER cannot be scored.',
}
engine = None
stage = 'verify_input'
exit_code = 1
try:
    input_hash = file_hash(INPUT)
    report['input_sha256'] = input_hash
    if input_hash != report['expected_input_sha256']:
        raise ValueError('Input hash mismatch')
    with wave.open(str(INPUT), 'rb') as w:
        report['input_format'] = {'sample_rate': w.getframerate(), 'channels': w.getnchannels(),
                                  'sample_width_bytes': w.getsampwidth(), 'frames': w.getnframes()}
        if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (16000, 1, 2):
            raise ValueError('Expected 16 kHz mono PCM16')
        duration = w.getnframes() / w.getframerate()
    stage = 'verify_model'
    manifest = json.loads((MODEL / 'manifest.json').read_text())
    verified = []
    for item in manifest:
        path = MODEL / item['file']
        actual = file_hash(path)
        if path.stat().st_size != item['bytes'] or actual != item['sha256']:
            raise ValueError('Model resource hash mismatch: ' + item['file'])
        verified.append({'file': item['file'], 'sha256': actual, 'bytes': path.stat().st_size})
    report['model_resources'] = verified
    stage = 'import_sdk'
    started = time.perf_counter()
    import spacemit_asr as asr
    report['import_seconds'] = time.perf_counter() - started
    report['sdk_version'] = asr.__version__
    cfg = asr.Config(model_dir=str(MODEL), backend='sensevoice')
    cfg.language = asr.Language.ZH
    cfg._config.extra_params = {'provider': 'cpu'}
    cfg._config.num_threads = 4
    report['native_provider_params'] = dict(cfg._config.extra_params)
    engine = asr.Engine(cfg)
    stage = 'initialize_model'
    started = time.perf_counter()
    engine.initialize()
    report['model_initialize_seconds'] = time.perf_counter() - started
    report['backend_name'] = engine.backend_name
    stage = 'recognize_file'
    started = time.perf_counter()
    result = engine.recognize_file(str(INPUT))
    wall = time.perf_counter() - started
    report.update({
        'component_status': 'inference_completed', 'recognized_text': result.text,
        'text_nonempty': not result.is_empty, 'audio_duration_ms_sdk': result.audio_duration_ms,
        'processing_time_ms_sdk': result.processing_time_ms, 'rtf_sdk': result.rtf,
        'recognition_wall_seconds': wall, 'audio_duration_seconds': duration,
        'wall_rtf': wall / duration,
    })
    exit_code = 0
except Exception as exc:
    report.update({'component_status': 'failed', 'failed_stage': stage,
                   'error_type': type(exc).__name__, 'error': str(exc),
                   'traceback': traceback.format_exc()})
finally:
    if engine is not None:
        try:
            engine.shutdown()
        except Exception as exc:
            report['shutdown_error'] = str(exc)
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
raise SystemExit(exit_code)
