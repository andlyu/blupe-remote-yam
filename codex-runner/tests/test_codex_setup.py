from http.server import ThreadingHTTPServer
import json
import threading
import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import run
from remote_yam.codex_policy import ensure_codex_runtime, login_codex, check_codex_storage, codex_status, codex_failure
from remote_yam.credentials import CredentialVault


class CodexSetupTests(unittest.TestCase):
    def test_storage_denied_stops_startup_before_serving_or_login(self):
        with patch('sys.argv', ['run.py', '--provider', 'codex', '--codex-login']), \
                patch.object(run, 'ensure_codex_runtime'), \
                patch.object(run, 'codex_status', return_value={'state':'storage_unwritable', 'message':'Cannot write session storage'}), \
                patch.object(run, 'login_codex') as login, \
                patch('local_playground.serve') as serve, patch('sys.stderr'):
            with self.assertRaises(SystemExit):
                run.main()
            login.assert_not_called()
            serve.assert_not_called()

    def test_storage_probe_uses_selected_home_without_changing_existing_files(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict('os.environ', {'CODEX_HOME': folder}):
            database = Path(folder) / 'state_5.sqlite'
            database.write_bytes(b'unchanged')
            check_codex_storage()
            self.assertEqual(database.read_bytes(), b'unchanged')
            self.assertTrue((Path(folder) / 'sessions').is_dir())
            self.assertEqual(list(Path(folder).rglob('.blupe-write-check-*')), [])

    def test_signed_in_but_unwritable_is_not_ready_and_does_not_relogin(self):
        with patch('remote_yam.codex_policy.codex_binary', return_value='/bin/codex'), \
                patch('remote_yam.codex_policy.subprocess.run') as command, \
                patch('remote_yam.codex_policy.check_codex_storage', side_effect=PermissionError('private path')):
            command.side_effect = lambda args, **kw: Mock(returncode=0, stdout='codex-cli 0.154.0' if '--version' in args else 'Logged in using ChatGPT', stderr='')
            status = codex_status()
            self.assertFalse(status['ready'])
            self.assertEqual(status['state'], 'storage_unwritable')
            self.assertNotIn('private path', status['message'])
            with self.assertRaisesRegex(RuntimeError, 'cannot write'):
                login_codex()
            self.assertFalse(any(call.args[0][-1] == 'login' for call in command.call_args_list))
        self.assertIn('cannot write', codex_failure(b'Permission denied: private path'))

    def test_installs_local_pinned_runtime_only_when_needed(self):
        with patch('remote_yam.codex_policy.codex_status', side_effect=[{'state':'upgrade_required'}, {'state':'ready'}]), \
             patch('remote_yam.codex_policy.shutil.which', return_value='/bin/npm'), \
             patch('remote_yam.codex_policy.subprocess.run') as install:
            install.return_value.returncode = 0
            ensure_codex_runtime()
            command = install.call_args.args[0]
            self.assertIn('--prefix', command)
            self.assertIn('@openai/codex@0.154.0', command)
            self.assertNotIn('-g', command)
        with patch('remote_yam.codex_policy.codex_status', return_value={'state':'login_required'}), \
             patch('remote_yam.codex_policy.subprocess.run') as install:
            ensure_codex_runtime()
            install.assert_not_called()

    def test_existing_subscription_does_not_start_another_login(self):
        with patch('remote_yam.codex_policy.codex_binary', return_value='/bin/codex'), \
             patch('remote_yam.codex_policy.codex_status', return_value={'ready':True}), \
             patch('remote_yam.codex_policy.subprocess.run') as login:
            login_codex()
            login.assert_not_called()

    def test_local_ui_setup_and_run_skip_api_credentials(self):
        controller = Mock()
        controller.join_and_run.return_value = {'status':'queued'}
        vault = CredentialVault()
        handler = run.build_handler(controller, vault, 'local-token', 'codex', False, 'http://127.0.0.1:8089')
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        origin = f'http://127.0.0.1:{server.server_port}'

        def post(path, payload=None, token='local-token'):
            req = Request(origin+path, data=json.dumps(payload or {}).encode(),
                          headers={'Content-Type':'application/json','X-YAM-Runner-Token':token})
            with urlopen(req) as response:
                return json.load(response)

        try:
            with patch.object(run, 'codex_status', return_value={'ready':True,'message':'Signed in'}), \
                 patch.object(run, 'CodexAdapter') as adapter, \
                 patch.object(vault, 'prompt_for') as prompt, patch.object(vault, 'require') as require:
                with urlopen(origin+'/api/config') as response:
                    config = json.load(response)
                self.assertEqual(config['default_provider'], 'codex')
                self.assertTrue(config['codex']['ready'])
                self.assertTrue(post('/api/codex/check')['ready'])
                controller.join_and_run.assert_not_called()
                with self.assertRaises(HTTPError) as denied:
                    post('/api/codex/check', token='bad-token')
                self.assertEqual(denied.exception.code, 403)
                with urlopen(origin+'/static/codex-setup.js') as response:
                    self.assertIn(b'codexCheck', response.read())
                self.assertEqual(post('/api/run', {'provider':'codex','prompt':'test'})['status'], 'queued')
                controller.join_and_run.assert_called_once_with(adapter.return_value, 'test')
                prompt.assert_not_called()
                require.assert_not_called()
                controller.join_and_run.reset_mock()
                adapter.side_effect = RuntimeError('Sign in first')
                with self.assertRaises(HTTPError):
                    post('/api/run', {'provider':'codex'})
                controller.join_and_run.assert_not_called()
        finally:
            server.shutdown()
            server.server_close()
            worker.join()
