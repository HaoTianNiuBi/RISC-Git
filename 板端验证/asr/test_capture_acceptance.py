"""Synthetic collector tests: never capture audio or write actual board scores."""
import copy
import http.client
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import capture_acceptance as capture


DEFINITIONS = Path(__file__).resolve().parents[2] / 'RV-Doctor/docs/edge/acceptance-cases.json'


class FakeEngine:
    def __init__(self, text='', error=None):
        self.text = text
        self.error = error
        self.calls = []

    def recognize_file(self, path):
        self.calls.append(path)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(text=self.text, audio_duration_ms=1000,
                               processing_time_ms=100, rtf=0.1)


class UnfinishedWorker:
    """An old worker is still in cleanup even after phase becomes idle/error."""
    def is_alive(self):
        return True

    def join(self, timeout=None):
        pass


class CollectorFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='synthetic-capture-tests-')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.definitions_path = self.base / 'acceptance-cases.json'
        self.definitions_path.write_bytes(DEFINITIONS.read_bytes())
        self.engine = FakeEngine()
        self.app = capture.Collector(self.base, self.definitions_path, self.engine)
        self.addCleanup(self.app.close)
        self.no_capture = patch.object(capture.subprocess, 'Popen',
                                       side_effect=AssertionError('Real capture is forbidden in tests'))
        self.popen = self.no_capture.start()
        self.addCleanup(self.no_capture.stop)
        self.cid = self.app.required[0]

    def disk_snapshot(self):
        return {p.relative_to(self.base).as_posix(): p.read_bytes()
                for p in self.base.rglob('*') if p.is_file()}

    def put_audio_record(self, status='error', hypothesis=None):
        audio = self.base / 'synthetic-audio-fixture'
        audio.mkdir(exist_ok=True)
        raw, prepared = audio / 'raw.wav', audio / 'prepared.wav'
        # Not WAV content: this test exercises retry/provenance, never DSP or capture.
        raw.write_bytes(b'synthetic raw bytes, not a recording')
        prepared.write_bytes(b'synthetic prepared bytes, not a recording')
        record = {
            'status': status, 'case_id': self.cid,
            'reference_text': self.app.references[self.cid],
            'dataset_sha256': self.app.definitions_hash,
            'config_sha256': self.app.config_hash,
            'raw_audio_path': str(raw), 'raw_audio_sha256': capture.digest(raw),
            'prepared_audio_path': str(prepared), 'prepared_audio_sha256': capture.digest(prepared),
            'audio_preparation': {'fixture_only': True},
            'error': 'synthetic previous recognition failure',
        }
        if hypothesis is not None:
            record['hypothesis'] = hypothesis
        self.app.results['cases'][self.cid] = record
        self.app._save()
        return raw, prepared, copy.deepcopy(record)

    def wait_worker(self):
        self.app.worker.join(timeout=3)
        self.assertFalse(self.app.worker.is_alive(), 'Synthetic recognition worker did not finish')


class CollectorStateTests(CollectorFixture):
    def test_construct_and_state_never_start_recording_or_recognition(self):
        before = self.disk_snapshot()
        for _ in range(3):
            state = self.app.state()
            self.assertEqual(state['phase'], 'idle')
            self.assertEqual(state['completed_count'], 0)
            self.assertFalse(state['score']['passed'])
            self.assertEqual(state['score']['status'], 'pending')
        self.assertIsNone(self.app.worker)
        self.assertEqual(self.engine.calls, [])
        self.popen.assert_not_called()
        self.assertEqual(self.disk_snapshot(), before)

    def test_unknown_case_never_launches_worker(self):
        for cid in ('not-a-case', None, [], {'case_id': self.cid}):
            with self.subTest(cid=cid), self.assertRaises((ValueError, TypeError)):
                self.app.start(cid)
        self.assertIsNone(self.app.worker)
        self.popen.assert_not_called()

    def test_phase_busy_blocks_start_and_round_switch(self):
        before = self.disk_snapshot()
        for phase in ('preparing', 'recording', 'processing'):
            self.app.phase = phase
            with self.subTest(phase=phase):
                self.assertTrue(self.app.busy())
                with self.assertRaises(ValueError):
                    self.app.start(self.cid)
                with self.assertRaises(ValueError):
                    self.app.new_round()
        self.app.phase = 'idle'
        self.assertEqual(self.disk_snapshot(), before)
        self.popen.assert_not_called()

    def test_old_worker_cleanup_blocks_start_even_if_phase_looks_idle(self):
        self.app.worker = UnfinishedWorker()
        for phase in ('idle', 'error'):
            self.app.phase = phase
            with self.subTest(phase=phase):
                self.assertTrue(self.app.busy())
                with self.assertRaises(ValueError):
                    self.app.start(self.cid)
        self.popen.assert_not_called()

    def test_old_worker_cleanup_blocks_round_switch(self):
        self.app.worker = UnfinishedWorker()
        self.app.phase = 'idle'
        before = self.disk_snapshot()
        with self.assertRaises(ValueError):
            self.app.new_round()
        self.assertEqual(self.disk_snapshot(), before)

    def test_start_launches_only_explicit_request_and_enters_preparing(self):
        with patch.object(capture.threading, 'Thread') as thread_class:
            self.app.start(self.cid)
            thread_class.assert_called_once()
            self.assertTrue(thread_class.call_args.kwargs['daemon'])
            self.assertEqual(thread_class.call_args.kwargs['args'][0], self.cid)
            thread_class.return_value.start.assert_called_once_with()
        self.assertEqual(self.app.phase, 'preparing')
        self.assertEqual(self.app.case_id, self.cid)
        self.assertEqual(self.engine.calls, [])
        self.popen.assert_not_called()

    def test_completed_case_is_immutable(self):
        self.put_audio_record(status='recognized', hypothesis=self.app.references[self.cid])
        before = self.disk_snapshot()
        with self.assertRaisesRegex(ValueError, '不能覆盖'):
            self.app.start(self.cid)
        self.assertEqual(self.disk_snapshot(), before)
        self.assertIsNone(self.app.worker)
        self.popen.assert_not_called()

    def test_new_round_preserves_old_files_and_does_not_capture(self):
        self.put_audio_record(status='recognized', hypothesis=self.app.references[self.cid])
        old_dir = self.app.round_dir
        old_files = {p.name: p.read_bytes() for p in old_dir.iterdir() if p.is_file()}
        old_round = self.app.results['round_id']
        self.app.new_round()
        self.assertNotEqual(self.app.results['round_id'], old_round)
        self.assertEqual(self.app.results['cases'], {})
        self.assertEqual({p.name: p.read_bytes() for p in old_dir.iterdir() if p.is_file()}, old_files)
        marker = json.loads(self.app.current_path.read_text())
        self.assertEqual(marker['round_id'], self.app.results['round_id'])
        self.assertFalse(self.app.state()['score']['passed'])
        self.popen.assert_not_called()

    def test_stop_only_sets_event_during_recording(self):
        for phase in ('idle', 'preparing', 'processing', 'error'):
            self.app.phase = phase
            with self.subTest(phase=phase), self.assertRaises(ValueError):
                self.app.stop()
        self.app.phase = 'recording'
        self.app.stop()
        self.assertTrue(self.app.stop_event.is_set())
        self.app.phase = 'idle'
        self.popen.assert_not_called()

    def test_existing_valid_audio_retry_uses_exact_same_files(self):
        raw, prepared, original = self.put_audio_record()
        raw_bytes, prepared_bytes = raw.read_bytes(), prepared.read_bytes()
        self.engine.text = self.app.references[self.cid]
        self.app.start(self.cid)
        self.wait_worker()
        self.assertEqual(self.engine.calls, [str(prepared)])
        result = self.app.results['cases'][self.cid]
        self.assertEqual(result['status'], 'recognized')
        self.assertNotIn('error', result)
        self.assertEqual(result['raw_audio_sha256'], original['raw_audio_sha256'])
        self.assertEqual(result['prepared_audio_sha256'], original['prepared_audio_sha256'])
        self.assertEqual(raw.read_bytes(), raw_bytes)
        self.assertEqual(prepared.read_bytes(), prepared_bytes)
        self.popen.assert_not_called()

    def test_recognition_failure_keeps_audio_for_later_retry(self):
        raw, prepared, original = self.put_audio_record()
        self.engine.error = RuntimeError('synthetic engine unavailable')
        self.app.start(self.cid)
        self.wait_worker()
        failed = copy.deepcopy(self.app.results['cases'][self.cid])
        self.assertEqual(failed['status'], 'error')
        self.assertEqual(failed['prepared_audio_sha256'], original['prepared_audio_sha256'])
        self.assertEqual(self.app.phase, 'error')
        self.engine.error = None
        self.engine.text = self.app.references[self.cid]
        self.app.start(self.cid)
        self.wait_worker()
        self.assertEqual(self.engine.calls, [str(prepared), str(prepared)])
        self.assertEqual(self.app.results['cases'][self.cid]['status'], 'recognized')
        self.assertEqual(capture.digest(raw), original['raw_audio_sha256'])
        self.popen.assert_not_called()

    def test_modified_existing_audio_is_rejected_without_recapture(self):
        raw, prepared, original = self.put_audio_record()
        prepared.write_bytes(b'tampered synthetic audio')
        self.app.start(self.cid)
        self.wait_worker()
        self.assertEqual(self.engine.calls, [])
        self.assertEqual(self.app.phase, 'error')
        self.assertIn('哈希不一致', self.app.message)
        self.assertEqual(self.app.results['cases'][self.cid]['prepared_audio_sha256'],
                         original['prepared_audio_sha256'])
        self.assertEqual(capture.digest(raw), original['raw_audio_sha256'])
        self.popen.assert_not_called()

    def test_nine_completed_cases_cannot_pass(self):
        for cid in self.app.required[:9]:
            self.app.results['cases'][cid] = {
                'status': 'recognized', 'reference_text': self.app.references[cid],
                'hypothesis': self.app.references[cid],
                'raw_audio_path': '/synthetic/' + cid + '-raw.wav', 'raw_audio_sha256': 'a'*64,
                'prepared_audio_path': '/synthetic/' + cid + '-prepared.wav', 'prepared_audio_sha256': 'b'*64,
            }
        state = self.app.state()
        self.assertEqual(state['completed_count'], 9)
        self.assertEqual(state['total_cases'], 10)
        self.assertFalse(state['score']['passed'])
        self.assertEqual(state['score']['status'], 'pending')
        self.assertIsNone(state['score']['corpus_cer'])

    def test_resume_requires_same_frozen_definitions(self):
        changed = json.loads(self.definitions_path.read_text(encoding='utf-8'))
        changed['asr_cases'][0]['reference_text'] += '篡改'
        self.definitions_path.write_text(json.dumps(changed), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'different definitions/configuration'):
            capture.Collector(self.base, self.definitions_path, FakeEngine())


class CollectorHTTPTests(CollectorFixture):
    def setUp(self):
        super().setUp()
        self.page = self.base / 'collector.html'
        self.page.write_text('<html>synthetic collector page</html>', encoding='utf-8')
        self.server = capture.ThreadingHTTPServer(('127.0.0.1', 0), capture.make_handler(self.app, self.page))
        self.port = self.server.server_address[1]
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.addCleanup(self.close_server)

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=2)

    def request(self, method, path, body=None, headers=None):
        client = http.client.HTTPConnection('127.0.0.1', self.port, timeout=2)
        try:
            client.request(method, path, body=body, headers=headers or {})
            response = client.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            client.close()

    def post(self, path, payload=None, headers=None):
        return self.request('POST', path, json.dumps(payload or {}).encode(), headers)

    def test_get_page_and_state_are_read_only_and_never_record(self):
        before = self.disk_snapshot()
        status, headers, body = self.request('GET', '/')
        self.assertEqual(status, 200)
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(body, self.page.read_bytes())
        status, headers, body = self.request('GET', '/api/state')
        self.assertEqual(status, 200)
        state = json.loads(body)
        self.assertEqual(state['phase'], 'idle')
        self.assertEqual(state['completed_count'], 0)
        self.assertFalse(state['score']['passed'])
        self.assertEqual(self.disk_snapshot(), before)
        self.assertEqual(self.engine.calls, [])
        self.popen.assert_not_called()

    def test_unknown_get_path_does_not_serve_files(self):
        for path in ('/missing', '/../acceptance-cases.json', '/api/start'):
            with self.subTest(path=path):
                self.assertEqual(self.request('GET', path)[0], 404)
        self.popen.assert_not_called()

    def test_cross_origin_start_rejected_before_action(self):
        before = self.disk_snapshot()
        for origin in ('http://example.invalid', 'null', 'https://127.0.0.1:%d' % self.port):
            status, _, body = self.post('/api/start', {'case_id': self.cid}, {'Origin': origin})
            with self.subTest(origin=origin):
                self.assertEqual(status, 403)
                self.assertEqual(json.loads(body)['error'], 'Origin mismatch')
        self.assertEqual(self.disk_snapshot(), before)
        self.assertIsNone(self.app.worker)
        self.popen.assert_not_called()

    def test_matching_origin_still_validates_case_before_recording(self):
        status, _, body = self.post('/api/start', {'case_id': 'invalid'},
                                    {'Origin': 'http://127.0.0.1:%d' % self.port})
        self.assertEqual(status, 409)
        self.assertIn('未知样本', json.loads(body)['error'])
        self.popen.assert_not_called()

    def test_malformed_json_and_non_objects_rejected(self):
        for body in (b'{', b'[]', b'null', b'"text"', b'\xff'):
            with self.subTest(body=body):
                self.assertEqual(self.request('POST', '/api/start', body)[0], 409)
        self.assertIsNone(self.app.worker)
        self.popen.assert_not_called()

    def test_invalid_content_length_rejected(self):
        for length in ('-1', '2049', 'not-an-integer'):
            with self.subTest(length=length):
                status, _, body = self.request('POST', '/api/start', b'', {'Content-Length': length})
                self.assertEqual(status, 409)
                self.assertIn('error', json.loads(body))
        self.popen.assert_not_called()

    def test_busy_start_and_round_switch_rejected_over_http(self):
        self.app.phase = 'processing'
        before = self.disk_snapshot()
        self.assertEqual(self.post('/api/start', {'case_id': self.cid})[0], 409)
        self.assertEqual(self.post('/api/new-round')[0], 409)
        self.assertEqual(self.disk_snapshot(), before)
        self.app.phase = 'idle'
        self.popen.assert_not_called()

    def test_immutable_case_rejected_over_http(self):
        self.put_audio_record(status='recognized', hypothesis=self.app.references[self.cid])
        before = self.disk_snapshot()
        self.assertEqual(self.post('/api/start', {'case_id': self.cid})[0], 409)
        self.assertEqual(self.disk_snapshot(), before)
        self.popen.assert_not_called()

    def test_unknown_post_and_idle_stop_have_no_side_effects(self):
        before = self.disk_snapshot()
        self.assertEqual(self.post('/api/missing')[0], 404)
        self.assertEqual(self.post('/api/stop')[0], 409)
        self.assertEqual(self.disk_snapshot(), before)
        self.popen.assert_not_called()


if __name__ == '__main__':
    unittest.main()
