"""Local K1 voice terminal and manual collection of missing acceptance clips."""

import argparse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import signal
import threading
import time
import traceback
import uuid

from audio import AudioPipeline, capture
from intent import IntentClient
from tasks import TaskDispatcher


BUSY = {'preparing', 'recording', 'recognizing', 'classifying', 'running'}
ACTIONS = {'start_checkup', 'explain_report', 'compare_configs', 'query_system'}
UNKNOWN_MESSAGE = '请说出需要的操作。支持性能体检、解释报告、对比配置和查询系统状态。'


def read_definitions(base):
    return json.loads((base / 'acceptance-cases.json').read_text(encoding='utf-8'))


def atomic_json(path, value):
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def route(text, client, dispatcher, on_dispatch=lambda intent: None):
    """Only a validated enum value can select one of the four fixed operations."""
    if not any(character.isalnum() for character in text):
        return {'raw_intent_output': None, 'json_valid': False, 'intent': None,
                'first_content_latency_s': None, 'total_latency_s': None,
                'failure_reason': 'empty_asr_text', 'observed_dispatch_intent': None,
                'dispatch_wall_seconds': None, 'task_result': None}
    model = client.classify(text)
    intent = model['intent'] if model['json_valid'] else None
    observed = intent if intent in ACTIONS else None
    result = None
    dispatch_seconds = None
    if observed is not None:
        on_dispatch(observed)
        dispatch_started = time.perf_counter()
        try:
            result = dispatcher.dispatch(observed)
        except (OSError, ValueError, RuntimeError) as exc:
            result = {'ok': False, 'message': str(exc)}
        dispatch_seconds = time.perf_counter() - dispatch_started
    return {**model, 'observed_dispatch_intent': observed, 'dispatch_wall_seconds': dispatch_seconds,
            'task_result': result}


class Terminal:
    def __init__(self, base, audio, client, dispatcher, definitions, *, capture_audio=capture):
        self.base = base
        self.audio = audio
        self.client = client
        self.dispatcher = dispatcher
        self.capture_audio = capture_audio
        self.samples = {row['case_id']: row['reference_text'] for row in definitions['intent_cases']
                        if row['source_asr_case_id'] is None}
        self.attempts = base / 'captures'
        self.attempts.mkdir(parents=True, exist_ok=True)
        self.fixtures = base / 'fixtures'
        self.fixtures.mkdir(parents=True, exist_ok=True)
        self.recorded = {cid for cid in self.samples if (self.fixtures / (cid + '.json')).is_file()}
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.worker = None
        self.recording_started = 0.0
        self.phase = 'idle'
        self.message = '点击开始录音，看到「现在朗读」后再开口。'
        self.active_fixture = None
        self.transcript = None
        self.intent = None
        self.result = None

    def state(self):
        with self.lock:
            return {
                'phase': self.phase, 'message': self.message, 'active_fixture': self.active_fixture,
                'elapsed_seconds': round(time.monotonic() - self.recording_started, 1)
                if self.phase == 'recording' else 0,
                'transcript': self.transcript, 'intent': self.intent, 'result': self.result,
                'fixtures': [{'case_id': cid, 'reference_text': text, 'recorded': cid in self.recorded}
                             for cid, text in self.samples.items()],
            }

    def start(self, case_id):
        with self.lock:
            if case_id is not None and (not isinstance(case_id, str) or case_id not in self.samples):
                raise ValueError('未知补录句子。')
            if case_id in self.recorded:
                raise ValueError('这句已经录好，请选择下一句。')
            if self.phase in BUSY or (self.worker is not None and self.worker.is_alive()):
                raise ValueError('当前操作尚未完成。')
            self.stop.clear()
            self.active_fixture = case_id
            self.transcript = self.intent = self.result = None
            self.phase, self.message = 'preparing', '正在准备麦克风，请稍等。'
            self.worker = threading.Thread(target=self._run, args=(case_id,), daemon=True)
            self.worker.start()

    def finish_recording(self):
        with self.lock:
            if self.phase != 'recording':
                raise ValueError('当前没有正在进行的录音。')
            self.stop.set()

    def _recording_ready(self):
        with self.lock:
            self.recording_started = time.monotonic()
            self.phase = 'recording'
            self.message = '现在朗读。完整读一遍，读完点停止录音。'

    def _before_dispatch(self, intent):
        with self.lock:
            self.phase, self.message, self.intent = 'running', '正在执行固定任务。', intent

    def _run(self, case_id):
        try:
            directory = self.attempts / uuid.uuid4().hex
            directory.mkdir()
            raw = directory / 'raw.wav'
            self.capture_audio(raw, self.stop, self._recording_ready)
            processing_started = time.perf_counter()
            with self.lock:
                self.phase, self.message = 'recognizing', '正在板端识别录音。'
            observation = self.audio.transcribe(raw, directory)
            observation['recorded_at_utc'] = datetime.now(timezone.utc).isoformat()
            text = observation['raw_asr_text']
            if case_id is not None:
                observation.update(case_id=case_id, reference_text=self.samples[case_id])
                atomic_json(self.fixtures / (case_id + '.json'), observation)
                with self.lock:
                    self.transcript = text
                    self.recorded.add(case_id)
                    self.phase, self.message = 'done', '这句已保存，可以补录下一句。'
                return
            with self.lock:
                self.transcript = text
                self.phase, self.message = 'classifying', '千问正在判断意图。'
            outcome = route(text, self.client, self.dispatcher, self._before_dispatch)
            observation.update(outcome)
            observation['processing_wall_seconds'] = time.perf_counter() - processing_started
            atomic_json(directory / 'result.json', observation)
            with self.lock:
                self.intent = outcome['intent']
                self.result = outcome['task_result']
                self.phase = 'done' if outcome['json_valid'] else 'error'
                if outcome['failure_reason'] == 'empty_asr_text':
                    self.message = '没有识别到清晰的语音，请靠近耳麦重试。'
                elif not outcome['json_valid']:
                    self.message = '未收到有效的意图结果，请重试。'
                else:
                    self.message = (self.result or {}).get('message') or UNKNOWN_MESSAGE
        except Exception as exc:
            traceback.print_exc()
            with self.lock:
                self.phase = 'error'
                self.message = str(exc) if isinstance(exc, ValueError) else '处理失败，请重试。'


def handler_for(app, page):
    class Handler(BaseHTTPRequestHandler):
        def send_body(self, body, content_type, status=200):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)

        def send_json(self, value, status=200):
            self.send_body(json.dumps(value, ensure_ascii=False).encode('utf-8'),
                           'application/json; charset=utf-8', status)

        def do_GET(self):
            if self.path == '/':
                self.send_body(page, 'text/html; charset=utf-8')
            elif self.path == '/api/state':
                self.send_json(app.state())
            else:
                self.send_json({'error': '未找到页面。'}, 404)

        def do_POST(self):
            host = self.headers.get('Host')
            origin = self.headers.get('Origin')
            if origin and origin != 'http://' + (host or ''):
                self.send_json({'error': '请求来源不匹配。'}, 403)
                return
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 <= size <= 1024:
                    raise ValueError('请求过大。')
                body = json.loads(self.rfile.read(size) or b'{}')
                if not isinstance(body, dict):
                    raise ValueError('请求必须是 JSON 对象。')
                if self.path == '/api/start':
                    app.start(body.get('case_id'))
                elif self.path == '/api/stop':
                    app.finish_recording()
                else:
                    self.send_json({'error': '未找到操作。'}, 404)
                    return
                self.send_json(app.state())
            except (ValueError, UnicodeError) as exc:
                self.send_json({'error': str(exc)}, 409)

        def log_message(self, *_args):
            pass

    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--model-dir', type=Path, required=True)
    parser.add_argument('--port', type=int, default=18793)
    args = parser.parse_args()
    base = args.base.resolve()
    definitions = read_definitions(base)
    client = IntentClient(definitions['intent_output_schema'])
    audio = AudioPipeline(args.model_dir)
    app = Terminal(base, audio, client, TaskDispatcher(base), definitions)
    page = Path(__file__).with_name('terminal.html').read_bytes()

    def stop_service(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop_service)
    with ThreadingHTTPServer(('127.0.0.1', args.port), handler_for(app, page)) as server:
        print(f'TERMINAL_READY http://127.0.0.1:{args.port}', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            app.stop.set()
            if app.worker is not None:
                app.worker.join(timeout=12)
            if app.worker is None or not app.worker.is_alive():
                audio.close()


if __name__ == '__main__':
    main()
