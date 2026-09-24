import asyncio
import json
import shutil
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from playground import HostedRunner
from local_playground import LocalPlayground
from remote_yam.providers import ScriptedAdapter
from remote_yam.session import MockSessionAPI


class LauncherTests(unittest.TestCase):
    def test_normal_startup_does_not_install_or_require_a_provider(self):
        from run import main
        with patch('sys.argv', ['run.py']), patch('local_playground.serve') as serve, \
                patch('run.ensure_codex_runtime') as install, patch('run.login_codex') as codex_login, \
                patch('run.login_claude') as claude_login, patch('run.claude_status') as claude:
            main()
        self.assertEqual(serve.call_args.args[0].provider, 'auto')
        for action in (install, codex_login, claude_login, claude):
            action.assert_not_called()

    def test_local_launcher_passes_external_groot_key_path(self):
        from local_playground import serve
        args = SimpleNamespace(port=8787, session_api=None, camera_origin=None,
                               allow_hardware_control=False, provider='codex', no_browser=True)
        with patch.dict('os.environ', {'YAM_GROOT_KEY_FILE':'/private/service/runpod-key'}), \
                patch('multi_robot.fleet') as fleet, patch('uvicorn.Config') as config, \
                patch('uvicorn.Server'):
            serve(args)
        self.assertEqual(fleet.call_args.args[1]['groot_key_file'], '/private/service/runpod-key')

    def test_command_submission_defaults_on_with_explicit_monitor_opt_out(self):
        from run import main
        for flags, enabled in [([], True), (['--read-only'], False),
                               (['--allow-hardware-control'], True)]:
            with self.subTest(flags=flags), patch('sys.argv', ['run.py', *flags]), \
                    patch('local_playground.serve') as serve:
                main()
                self.assertEqual(serve.call_args.args[0].allow_hardware_control, enabled)

    def test_occupied_port_does_not_open_existing_runner(self):
        from local_playground import serve
        args = SimpleNamespace(port=8787, session_api=None, camera_origin=None,
                               allow_hardware_control=False, provider='codex', no_browser=False)
        with patch('local_playground.LocalPlayground'), patch('uvicorn.Config') as config, \
                patch('webbrowser.open') as browser, patch('uvicorn.Server') as server:
            config.return_value.bind_socket.side_effect = SystemExit(1)
            with self.assertRaises(SystemExit):
                serve(args)
            browser.assert_not_called()
            server.assert_not_called()


class LocalPlaygroundTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        probe = patch('remote_yam.subscription_setup.probe_subscription', return_value='test-model')
        self.probe = probe.start()
        self.addCleanup(probe.stop)
        for provider in ('codex', 'claude'):
            status = patch(f'remote_yam.{provider}_policy.{provider}_status',
                           return_value={'ready': True, 'state': 'ready', 'message': 'Signed in'})
            status.start()
            self.addCleanup(status.stop)
        self.providers = []
        def provider(name, key, model, directory):
            self.providers.append((name, key, model))
            return ScriptedAdapter([])
        self.app = LocalPlayground(public_origin='http://127.0.0.1:8791', session_api=None,
            camera_origin='http://127.0.0.1:8089', development=True, local_codex=True, local_claude=True,
            default_provider='codex', api_factory=lambda: MockSessionAPI(auto_activate=False),
            provider_factory=provider)
        self.cookie = ''; self.csrf = ''

    async def asyncTearDown(self):
        for visitor in self.app.visitors.values():
            await asyncio.to_thread(visitor.close)
        shutil.rmtree(self.app.root, ignore_errors=True)

    async def call(self, path, payload=None):
        messages = []
        async def receive():
            return {'type':'http.request', 'body':json.dumps(payload or {}).encode()}
        async def send(message):
            messages.append(message)
        await self.app({'type':'http', 'method':'POST' if payload is not None else 'GET',
            'path':path, 'query_string':b'', 'headers':[(k.encode(),v.encode()) for k,v in {
                'host':'127.0.0.1:8791', 'origin':'http://127.0.0.1:8791',
                'content-type':'application/json', 'cookie':self.cookie,
                'x-yam-runner-token':self.csrf}.items()]},receive,send)
        status=messages[0]['status'];headers=dict(messages[0]['headers'])
        body=b''.join(m.get('body',b'') for m in messages[1:])
        if b'set-cookie' in headers:
            self.cookie=headers[b'set-cookie'].decode().split(';')[0]
        return status,json.loads(body) if headers[b'content-type'].startswith(b'application/json') else body

    async def test_subscription_joins_same_queue_without_key_and_rejects_duplicate(self):
        with patch('remote_yam.codex_policy.codex_status',return_value={'ready':True,'message':'Signed in'}):
            code,session=await self.call('/api/session',{})
        self.assertEqual(code,200)
        self.assertTrue(session['local_runner']);self.csrf=session['csrf']
        self.assertEqual(session['default_provider'],'codex')
        payload={'provider':'codex','model':'gpt-6-astra','prompt':'test task','runner_name':'Local test'}
        code,_=await self.call('/api/run',payload)
        self.assertEqual(code,200)
        self.assertEqual(self.providers,[('codex','','gpt-6-astra')])
        _,state=await self.call('/api/status')
        self.assertEqual(state['status'],'queued')
        self.assertEqual(state['saved_key_providers'],[])
        code,_=await self.call('/api/run',payload)
        self.assertEqual(code,409)
        code,_=await self.call('/api/stop',{})
        self.assertEqual(code,200)

    async def test_shared_page_and_video_library_are_served(self):
        code,body=await self.call('/')
        self.assertEqual(code,200)
        self.assertIn(b'Prompt the arms',body)
        self.assertIn(b'liveConversationPanel',body)
        self.assertNotIn(b'YOUR LOCAL',body)
        code,_=await self.call('/static/hls.light.min.js')
        self.assertEqual(code,200)

    async def test_unavailable_subscription_never_joins_and_can_be_rechecked(self):
        _, session = await self.call('/api/session', {})
        self.csrf = session['csrf']
        for provider in ('codex', 'claude'):
            for reason in ('missing', 'upgrade_required', 'login_required', 'api_key_login', 'storage_unwritable'):
                with self.subTest(provider=provider, reason=reason), \
                     patch(f'remote_yam.{provider}_policy.{provider}_status',
                           return_value={'ready': False, 'state': reason, 'message': 'Setup required'}):
                    code, result = await self.call('/api/run', {'provider': provider,
                        'model': '', 'prompt': 'Move apple', 'runner_name': 'Tester'})
                    self.assertEqual(code, 409)
                    setup = result['subscription_setup']
                    self.assertEqual(setup['provider'], provider)
                    self.assertEqual(setup['state'], reason)
                    self.assertIn('README.md', setup['setup_prompt'])
                    self.assertFalse(self.providers)
                    _, state = await self.call('/api/status')
                    self.assertEqual(state['status'], 'idle')
            code, setup = await self.call(f'/api/{provider}/check', {})
            self.assertEqual(code, 200)
            self.assertTrue(setup['ready'])
            self.assertFalse(self.providers)

    async def test_expired_saved_login_is_blocked_by_live_check_before_queue(self):
        _, session = await self.call('/api/session', {})
        self.csrf = session['csrf']
        self.probe.assert_not_called()  # Loading the page uses no model usage.
        self.probe.side_effect = RuntimeError('Claude Code needs sign-in.')
        code, result = await self.call('/api/run', {'provider': 'claude',
            'model': 'claude-opus-5-5', 'prompt': 'Move apple', 'runner_name': 'Tester'})
        self.assertEqual(code, 409)
        self.assertEqual(result['subscription_setup']['state'], 'login_required')
        self.assertIn('claude auth login', result['subscription_setup']['message'])
        self.assertFalse(self.providers)
        _, state = await self.call('/api/status')
        self.assertEqual(state['status'], 'idle')
        self.probe.side_effect = None
        _, setup = await self.call('/api/claude/check', {})
        self.assertTrue(setup['verified'])
        self.assertFalse(self.providers)  # Rechecking never starts a run.

    def test_subscription_cannot_be_enabled_on_public_server(self):
        with self.assertRaisesRegex(ValueError,'restricted to the local runner'):
            HostedRunner(public_origin='https://robot.example',session_api='https://session.example',
                camera_origin='https://session.example',local_codex=True)

    async def test_runtime_auth_failure_shows_owner_setup_without_polling_model(self):
        _, session = await self.call('/api/session', {})
        self.csrf = session['csrf']
        visitor = next(iter(self.app.visitors.values()))
        visitor.subscription_prompts['claude'] = 'Reconnect Claude for this playground.'
        state = visitor.controller.status()
        state.update(status='error', provider={'provider': 'claude'},
                     error='RuntimeError: Claude Code needs sign-in.')
        with patch.object(visitor.controller, 'status', return_value=state):
            _, result = await self.call('/api/status')
        self.assertEqual(result['subscription_setup']['state'], 'login_required')
        self.assertTrue(result['subscription_setup']['run_started'])
        self.assertEqual(result['subscription_setup']['setup_prompt'], visitor.subscription_prompts['claude'])
        self.probe.assert_not_called()

    async def test_past_runs_proxy_preserves_selected_robot(self):
        from unittest.mock import MagicMock
        self.app.robot_id = 'robot-maker'
        response = MagicMock()
        response.__enter__.return_value = response
        response.status = 200
        response.headers.get.return_value = 'application/json'
        response.read.return_value = b'{"runs":[]}'
        with patch('local_playground.request.urlopen', return_value=response) as fetch:
            code, body = await self.call('/api/past-runs')
        self.assertEqual(code, 200)
        self.assertIn('robot_id=robot-maker', fetch.call_args.args[0].full_url)
