import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from remote_yam.cameras import CameraFrameSource
from remote_yam.providers import OpenAIAdapter


class Frames(BaseHTTPRequestHandler):
    counts = {}
    failures = 0
    status = 503

    def do_GET(self):
        cls = type(self)
        cls.counts[self.path] = cls.counts.get(self.path, 0) + 1
        if self.path == '/right' and cls.counts[self.path] <= cls.failures:
            self.send_error(cls.status)
            return
        body = b'\xff\xd8' + f'{self.path}:{cls.counts[self.path]}'.encode() + b'\xff\xd9'
        self.send_response(200)
        self.send_header('Content-Type', 'image/jpeg')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class CameraRetryTests(unittest.TestCase):
    def setUp(self):
        Frames.counts, Frames.failures, Frames.status = {}, 0, 503
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Frames)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        origin = f'http://127.0.0.1:{self.server.server_port}'
        self.source = CameraFrameSource(origin)
        self.observation = {'images': {name: {'url': origin+'/'+name} for name in ('left','top','right')}}

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_recovers_on_third_frame_and_refreshes_all_views(self):
        Frames.failures = 2
        events = []
        frames = self.source.capture_for_policy(self.observation, on_event=lambda *args: events.append(args))
        self.assertEqual(Frames.counts, {'/left':3, '/top':3, '/right':3})
        self.assertTrue(all(b':3' in frame.jpeg for frame in frames))
        self.assertEqual([e[0] for e in events], ['camera_retry', 'camera_retry', 'camera_recovered'])
        self.assertEqual(events[0][2]['http_status'], 503)

    def test_exhaustion_names_camera_and_never_calls_model(self):
        Frames.failures = 100
        provider = OpenAIAdapter('private-test-key', 'test', camera_source=self.source)
        with patch.object(provider, '_post_json') as model:
            with self.assertRaisesRegex(RuntimeError, 'Camera failure: right camera frame unavailable after 3 attempt.*HTTP 503'):
                provider.infer('task', self.observation)
        model.assert_not_called()
        self.assertEqual(Frames.counts['/right'], 3)
        self.assertEqual(provider.public_config()['vision']['retry']['state'], 'failed')
        self.assertNotIn('private-test-key', json.dumps(provider.public_config()))

    def test_stop_during_retry_sends_no_further_requests(self):
        Frames.failures = 100
        stopped = threading.Event()
        with self.assertRaisesRegex(RuntimeError, 'cancelled'):
            self.source.capture_for_policy(self.observation, cancelled=stopped.is_set,
                                           on_event=lambda *args: stopped.set())
        self.assertEqual(Frames.counts['/right'], 1)

    def test_permission_failure_is_not_retried(self):
        Frames.failures, Frames.status = 100, 403
        with self.assertRaisesRegex(RuntimeError, 'Camera failure: right.*HTTP 403'):
            self.source.capture_for_policy(self.observation)
        self.assertEqual(Frames.counts['/right'], 1)

    def test_cancelled_success_discards_frames(self):
        stopped = threading.Event()
        original = self.source.capture
        def capture(observation):
            frames = original(observation)
            stopped.set()
            return frames
        with patch.object(self.source, 'capture', side_effect=capture):
            with self.assertRaisesRegex(RuntimeError, 'cancelled'):
                self.source.capture_for_policy(self.observation, cancelled=stopped.is_set)
