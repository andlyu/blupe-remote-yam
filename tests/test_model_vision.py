import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import unittest
from unittest.mock import patch

from remote_yam.cameras import CameraFrameSource, MAX_FRAME_BYTES
from remote_yam.providers import OpenAIAdapter


class Source(BaseHTTPRequestHandler):
    requests = []
    sequence = 0

    def do_GET(self):
        type(self).sequence += 1
        type(self).requests.append((self.path, dict(self.headers)))
        if self.path == '/offline':
            self.send_error(503)
            return
        if self.path == '/redirect':
            self.send_response(302)
            self.send_header('Location', '/left')
            self.end_headers()
            return
        # JPEG transport marker fixture; model HTTP is mocked in these tests.
        jpeg = b'\xff\xd8' + f'{self.path}:{self.sequence}'.encode() + b'\xff\xd9'
        length = MAX_FRAME_BYTES + 1 if self.path == '/oversized' else len(jpeg)
        if self.path == '/truncated':
            jpeg = jpeg[:-2]
        if self.path == '/malformed':
            jpeg = b'bad!' + jpeg[4:]
        self.send_response(200)
        self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=frame')
        self.end_headers()
        body = b'--frame\r\nContent-Type: image/jpeg\r\n' + f'Content-Length: {length}\r\n\r\n'.encode() + jpeg + b'\r\n'
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *args):
        pass


class ModelVisionTests(unittest.TestCase):
    def setUp(self):
        Source.requests = []
        Source.sequence = 0
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Source)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = f'http://127.0.0.1:{self.server.server_port}'
        self.source = CameraFrameSource(self.origin)
        self.observation = {
            'source': 'hardware', 'settled': True,
            'left_joints_deg': [1,2,3,4,5,6], 'right_joints_deg': [6,5,4,3,2,1],
            'images': {name: {'url': self.origin+'/'+name} for name in ('left','top','right')},
        }

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_each_inference_sends_three_new_labeled_image_inputs(self):
        provider = OpenAIAdapter('private-key', 'gpt-6-astra', camera_source=self.source)
        result = {'output_text': json.dumps({arm: {'mode':'joints','values': self.observation[arm+'_joints_deg']} for arm in ('left','right')})}
        with patch.object(provider, '_post_json', return_value=result) as post:
            provider.infer('place green block on plate', self.observation)
            provider.infer('place green block on plate', self.observation)
        images_by_call = []
        for call in post.call_args_list:
            payload = call.args[0]
            self.assertEqual(payload['model'], 'gpt-6-astra')
            self.assertFalse(payload['store'])
            content = payload['input'][0]['content']
            self.assertEqual(json.loads(content[0]['text'])['observation']['left_joints_deg'], [1,2,3,4,5,6])
            self.assertNotIn(self.origin, json.dumps(payload))
            images = [part['image_url'] for part in content if part['type']=='input_image']
            self.assertEqual(len(images), 3)
            for index, name in enumerate(('left','top','right')):
                self.assertIn('Camera view: '+name, content[1+index*2]['text'])
                self.assertIn(('/'+name+':').encode(), base64.b64decode(images[index].split(',',1)[1]))
            images_by_call.append(images)
        self.assertNotEqual(images_by_call[0], images_by_call[1])
        self.assertEqual(len(Source.requests), 6)
        self.assertTrue(all('Authorization' not in headers for _,headers in Source.requests))
        vision = provider.public_config()['vision']
        self.assertEqual(vision['state'], 'response_received')
        self.assertEqual(len(vision['frames']), 3)
        self.assertNotIn('data:image', json.dumps(provider.public_config()))

    def test_untrusted_origin_rejected_before_any_fetch(self):
        self.observation['images']['right']['url'] = 'http://example.invalid/right'
        with self.assertRaisesRegex(RuntimeError, 'configured relay'):
            self.source.capture(self.observation)
        self.assertEqual(Source.requests, [])

    def test_camera_failure_never_sends_a_text_only_request(self):
        provider = OpenAIAdapter('private-key', 'gpt-6-astra', camera_source=self.source)
        for path in ('offline','redirect','oversized','truncated','malformed'):
            with self.subTest(path=path), patch.object(provider, '_post_json') as post:
                self.observation['images']['left']['url'] = self.origin+'/'+path
                with self.assertRaisesRegex(RuntimeError, 'left camera frame unavailable'):
                    provider.infer('place block', self.observation)
                post.assert_not_called()
                self.assertEqual(provider.public_config()['vision']['state'], 'camera_error')

    def test_hardware_inference_requires_camera_source(self):
        provider = OpenAIAdapter('test', 'gpt-6-astra')
        with patch.object(provider, '_post_json') as post:
            with self.assertRaisesRegex(RuntimeError, 'requires a camera frame source'):
                provider.infer('place block', self.observation)
            post.assert_not_called()

    def test_model_failure_reports_request_error_with_frame_metadata(self):
        provider = OpenAIAdapter('test', 'gpt-6-astra', camera_source=self.source)
        with patch.object(provider, '_post_json', side_effect=RuntimeError('HTTP 503')):
            with self.assertRaisesRegex(RuntimeError, 'HTTP 503'):
                provider.infer('place block', self.observation)
        vision = provider.public_config()['vision']
        self.assertEqual(vision['state'], 'request_error')
        self.assertEqual(len(vision['frames']), 3)
