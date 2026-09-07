import importlib.util
from pathlib import Path
from http.server import ThreadingHTTPServer
import json
import threading
import unittest
from urllib.request import Request, urlopen

from remote_yam.credentials import CredentialVault
from remote_yam.providers import RepeatingRaiseLowerAdapter


class RepeatUITests(unittest.TestCase):
    def test_builtin_policy_creates_three_cycles_through_http(self):
        path = Path(__file__).resolve().parents[1] / 'run.py'
        spec = importlib.util.spec_from_file_location('repeat_ui_run', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        class Controller:
            provider = None
            def join_and_run(self, provider, prompt):
                self.provider = provider
                return {'status': 'queued'}
        controller = Controller()
        handler = module.build_handler(controller, CredentialVault(), 'test-token', 'local_raise_lower', False, 'http://127.0.0.1:8089')
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        url = f'http://127.0.0.1:{server.server_port}'
        try:
            with urlopen(url) as response:
                html = response.read().decode()
            self.assertIn('Runs 3 cycles:', html)
            self.assertIn('Packets submitted', html)
            self.assertIn('Images sent to model', html)
            req = Request(url+'/api/run', data=json.dumps({'provider':'local_raise_lower','prompt':'repeat'}).encode(), headers={'Content-Type':'application/json','X-YAM-Runner-Token':'test-token'})
            with urlopen(req) as response:
                self.assertEqual(json.load(response)['status'], 'queued')
            self.assertIsInstance(controller.provider, RepeatingRaiseLowerAdapter)
            self.assertEqual(controller.provider.cycles, 3)
        finally:
            server.shutdown()
            server.server_close()
            worker.join()

    def test_automatic_provider_and_prompt_defaults(self):
        from unittest.mock import patch
        path = Path(__file__).resolve().parents[1] / 'run.py'
        spec = importlib.util.spec_from_file_location('defaults_ui_run', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        class Controller:
            def join_and_run(self, provider, prompt):
                self.provider, self.prompt = provider, prompt
                return {'status': 'queued'}
        for keys, expected in [({}, 'local_raise_lower'), ({'openai': 'test'}, 'openai'), ({'astra': 'test'}, 'astra')]:
            with self.subTest(expected=expected, keys=list(keys)):
                controller = Controller()
                handler = module.build_handler(controller, CredentialVault(keys), 'test-token', 'auto', False, 'http://127.0.0.1:8089')
                server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
                worker = threading.Thread(target=server.serve_forever, daemon=True)
                worker.start()
                url = f'http://127.0.0.1:{server.server_port}'
                try:
                    with patch.dict(module.os.environ, {'ASTRA_ENDPOINT': 'https://example.com/v1/responses', 'ASTRA_MODEL': 'test-astra'}), patch.object(module, 'AstraAdapter') as astra:
                        with urlopen(url+'/api/config') as response:
                            config = json.load(response)
                        self.assertEqual(config['default_provider'], expected)
                        self.assertEqual(config['default_prompt'], 'place green block on plate')
                        req = Request(url+'/api/run', data=b'{}', headers={'Content-Type':'application/json','X-YAM-Runner-Token':'test-token'})
                        with urlopen(req) as response:
                            self.assertEqual(json.load(response)['status'], 'queued')
                        self.assertEqual(controller.prompt, 'place green block on plate')
                        if expected == 'astra':
                            astra.assert_called_once_with('test', 'test-astra', 'https://example.com/v1/responses', camera_source=unittest.mock.ANY, recording_root=module.PROJECT_ROOT / 'recordings')
                            self.assertIsInstance(astra.call_args.kwargs['camera_source'], module.CameraFrameSource)
                        elif expected == 'openai':
                            self.assertEqual(controller.provider.public_config()['model'], 'gpt-6-astra')
                            self.assertEqual(controller.provider.public_config()['policy'], 'robocurve_no_demo')
                        else:
                            self.assertIsInstance(controller.provider, RepeatingRaiseLowerAdapter)
                finally:
                    server.shutdown()
                    server.server_close()
                    worker.join()
