import asyncio
import json
import shutil
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from playground import HostedRunner, RequestError
from local_playground import LocalPlayground
from remote_yam.providers import ScriptedAdapter
from remote_yam.session import MockSessionAPI


class LauncherTests(unittest.TestCase):
    def test_aspire_flag_selects_station_without_starting_a_run(self):
        from run import main
        with patch('sys.argv', ['run.py','--aspire-config','/tmp/station.json']), \
                patch.dict('os.environ',{},clear=False), patch('local_playground.serve') as serve:
            main()
            import os
            self.assertEqual(os.environ['YAM_ASPIRE_CONFIG'],str(Path('/tmp/station.json').resolve()))
            self.assertEqual(serve.call_args.args[0].aspire_config,'/tmp/station.json')

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
    async def test_lineage_prompt_preview_uses_actual_launch_binding_without_admission(self):
        from test_aspire_launch_routing import saved_library,TASK
        import hashlib
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);library=saved_library(root)
            self.app.aspire_station_config=dict(robot_id='fixture',skill_directory=str(root/'skills'),
                executable_skills=dict(enabled=True,manifest=str(library.path)))
            expected=self.app.resolve_api_depth_launch(TASK)
            code,data=await self.call('/api/aspire-lineage',query={'prompt':TASK})
            self.assertEqual(code,200);binding=data['next_run_binding']
            self.assertEqual(binding['route'],expected['route'])
            self.assertEqual(binding['route']['source_sha256'],hashlib.sha256(expected['program']['response']['source'].encode()).hexdigest())
            self.assertFalse(binding['queue_session_created']);self.assertEqual(binding['physical_motion_calls'],0)
            self.assertEqual(self.providers,[]);self.assertFalse(self.app.visitors)

    async def test_lineage_catalog_is_read_only_and_artifacts_are_allowlisted(self):
        with tempfile.TemporaryDirectory() as directory:
            skills=Path(directory)/'outputs/aspire-skills/robohouse';skills.mkdir(parents=True)
            self.app.aspire_station_config=dict(robot_id='fixture',skill_directory=str(skills))
            code,data=await self.call('/api/aspire-lineage')
            self.assertEqual(code,200)
            self.assertEqual(data['episodes'],[])
            self.assertEqual(data['robot_id'],'fixture')
            self.assertEqual(self.providers,[])
            self.assertFalse(self.app.visitors)
            code,_=await self.call('/api/aspire-lineage',{})
            self.assertEqual(code,403)
            code,_=await self.call('/api/aspire-lineage/artifacts/'+'0'*64)
            self.assertEqual(code,404)
            code,_=await self.call('/api/aspire-lineage/artifacts/../../private.json')
            self.assertEqual(code,404)

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

    async def test_api_depth_is_unchecked_by_default_and_omitted_selection_uses_rgb(self):
        depth_calls = []
        self.app.api_depth_provider_factory = lambda *args: depth_calls.append(args)
        _, session = await self.call('/api/session', {})
        self.csrf = session['csrf']
        self.assertEqual(session['api_depth'], {'available': True, 'enabled': False})
        code, _ = await self.call('/api/run', {'provider': 'codex', 'model': 'gpt-6-astra',
            'prompt': 'Observe the red block', 'runner_name': 'Local test'})
        self.assertEqual(code, 200)
        self.assertEqual(depth_calls, [])
        self.assertEqual(len(self.providers), 1)

    def test_standard_local_yam_registers_depth_without_capture_or_enabling_it(self):
        app = LocalPlayground(public_origin='http://127.0.0.1:8792',
            session_api='https://api.example', camera_origin='https://api.example',
            development=True, local_codex=True, api_factory=MockSessionAPI)
        self.addCleanup(shutil.rmtree, app.root, True)
        self.assertTrue(callable(app.api_depth_provider_factory))
        self.assertFalse(app.use_api_depth)

    def test_aspire_station_routes_codex_and_leaves_other_robots_unchanged(self):
        with tempfile.TemporaryDirectory() as temporary:
            config=Path(temporary)/'station.json'
            config.write_text(json.dumps(dict(robot_id='robohouse',origin='https://station.example')))
            with patch.dict('os.environ',{'YAM_ASPIRE_CONFIG':str(config)}), \
                    patch('remote_yam.aspire_codex_policy.configured_aspire_policy') as configured:
                app=LocalPlayground(public_origin='http://127.0.0.1:8792',robot_id='robohouse',
                    session_api='https://api.example',camera_origin='https://api.example',
                    development=True,local_codex=True,api_factory=MockSessionAPI)
                self.addCleanup(shutil.rmtree,app.root,True)
                self.assertTrue(app.use_api_depth)
                configured.assert_not_called()
                app.api_depth_provider_factory('codex','','gpt-6-astra',temporary)
                self.assertEqual(configured.call_args.kwargs['robot_id'],'robohouse')
                with self.assertRaises(RequestError):app.api_depth_provider_factory('claude','','model',temporary)
                other=LocalPlayground(public_origin='http://127.0.0.1:8793',robot_id='yam-1',
                    session_api='https://api.example',camera_origin='https://api.example',
                    development=True,local_codex=True,api_factory=MockSessionAPI)
                self.addCleanup(shutil.rmtree,other.root,True)
                self.assertFalse(other.use_api_depth)

    async def test_station_session_reports_selected_segmenter_without_loading_or_exposing_key(self):
        self.app.aspire_station_config={'segmentation_backend':'runpod_sam3',
            'runpod_sam3':{'endpoint_id':'fixture-endpoint','api_key_file':'/fixture/private-key'}}
        with patch.dict('os.environ',{},clear=True), \
                patch('remote_yam.runpod_sam3.RunpodSam3Client.from_env') as factory:
            setup, headers=await self.app.session_extension({},None)
        factory.assert_not_called()
        self.assertEqual(setup,{'segmentation':{'backend':'runpod_sam3','model':'facebook/sam3'}})
        self.assertEqual(headers,[])
        self.assertNotIn('private-key',json.dumps(setup))
        self.app.aspire_station_config=None
        self.assertEqual(await self.app.session_extension({},None),({},[]))

    async def test_station_session_reports_skill_workflow_without_loading_or_running_provider(self):
        self.app.aspire_station_config={'segmentation_backend':'runpod_sam3',
            'skill_learning':{'enabled':True,'root':'/fixture/topic-library'}}
        with patch('remote_yam.aspire_codex_policy.configured_aspire_policy') as factory, \
                patch('remote_yam.runpod_sam3.RunpodSam3Client.from_env') as vision:
            setup,_=await self.app.session_extension({},None)
        factory.assert_not_called();vision.assert_not_called()
        self.assertEqual(setup['skill_learning']['coordinator'],'codex_subscription_before_next_task')
        self.app.aspire_station_config['skill_learning']['review_timing']='after_run'
        setup,_=await self.app.session_extension({},None)
        self.assertEqual(setup['skill_learning']['coordinator'],'codex_subscription_after_run')
        self.assertEqual(setup['skill_learning']['review_timing'],'after_run')
        self.assertNotIn('topic-library',json.dumps(setup))

    async def test_explicit_web_route_uses_actual_normal_astra_and_preserves_constraints(self):
        from remote_yam.codex_policy import CodexAdapter
        self.app.aspire_station_config={'executable_skills':{'enabled':False}}
        from remote_yam.aspire_executable_skills import select_aspire_launch
        self.app.resolve_api_depth_launch=lambda task:select_aspire_launch(self.app.aspire_station_config,task,execution_environment='web')
        self.app.api_depth_provider_factory=unittest.mock.Mock(side_effect=AssertionError('No ASPIRE construction'))
        self.app.provider_factory=None
        _, session=await self.call('/api/session',{})
        self.csrf=session['csrf']
        prompt='Move the green block onto the green towel with the right arm. Do not execute.'
        with patch('remote_yam.codex_policy.codex_binary',return_value='/fixture/codex'), \
                patch('remote_yam.aspire_codex_policy.configured_aspire_policy') as aspire, \
                patch('remote_yam.aspire_codex_policy.AspireCodexPolicy._generate') as generate:
            code, response=await self.call('/api/run',{'provider':'codex','model':'gpt-6-astra',
                'prompt':prompt,'use_api_depth':True,'reasoning_effort':'high','response_speed':'standard'})
        self.assertEqual(code,200)
        visitor=next(iter(self.app.visitors.values()))
        provider=visitor.controller._provider
        self.assertIs(type(provider),CodexAdapter)
        self.addCleanup(provider._workspace.cleanup)
        self.assertEqual(visitor.controller._prompt,prompt)
        self.assertEqual(provider.public_config()['launch_route']['actual_policy'],'astra')
        self.assertNotIn('task_progress',provider.public_config())
        self.assertNotIn('ASPIRE',provider.public_config().get('display_name','Astra'))
        self.assertIn('Running Astra instead',response['launch_route']['message'])
        self.assertEqual(response['launch_route']['prompt'],prompt)
        self.assertFalse(response['launch_route']['code_generation_requested'])
        self.assertEqual(visitor.controller.status()['run_configuration']['launch_route'],response['launch_route'])
        aspire.assert_not_called();generate.assert_not_called()
        self.app.api_depth_provider_factory.assert_not_called()

    async def test_matched_prompt_binds_source_before_constructing_aspire(self):
        from test_aspire_launch_routing import saved_library,TASK
        with tempfile.TemporaryDirectory() as temporary:
            library=saved_library(Path(temporary))
            self.app.aspire_station_config={'executable_skills':{'enabled':True,'manifest':str(library.path)}}
            provider=ScriptedAdapter([])
            self.app.api_depth_provider_factory=unittest.mock.Mock(return_value=provider)
            _,session=await self.call('/api/session',{});self.csrf=session['csrf']
            code,response=await self.call('/api/run',{'provider':'codex','model':'gpt-6-astra',
                'prompt':TASK,'use_api_depth':True})
            self.assertEqual(code,200)
            kwargs=self.app.api_depth_provider_factory.call_args.kwargs
            self.assertEqual(kwargs['task'],TASK)
            self.assertEqual(kwargs['selected_executable'],library.select(TASK))
            self.assertEqual(response['launch_route']['actual_policy'],'aspire')
            self.assertIn('Program found',response['launch_route']['message'])
            self.assertEqual(response['launch_route']['source_sha256'],library.select(TASK)['provenance']['bound_source_sha256'])
            self.assertEqual(self.providers,[])

    async def test_depth_option_selects_depth_provider_and_unchecking_selects_rgb(self):
        depth_calls = []
        def depth_provider(name, key, model, directory):
            depth_calls.append(name)
            return ScriptedAdapter([])
        self.app.api_depth_provider_factory = depth_provider
        self.app.use_api_depth = True
        _, session = await self.call('/api/session', {})
        self.csrf = session['csrf']
        self.assertEqual(session['api_depth'], {'available': True, 'enabled': True})
        payload = {'provider': 'codex', 'model': 'gpt-6-astra', 'prompt': 'Move the red block',
                   'runner_name': 'Local test', 'use_api_depth': True}
        code, _ = await self.call('/api/run', payload)
        self.assertEqual(code, 200)
        self.assertEqual(depth_calls, ['codex'])
        self.assertEqual(self.providers, [])
        await self.call('/api/stop', {})
        controller = next(iter(self.app.visitors.values())).controller
        for _ in range(100):
            if not controller.busy() and controller.status()['status'] not in {'queued', 'preparing', 'running', 'stopping'}:
                break
            await asyncio.sleep(.01)
        self.assertFalse(controller.busy())
        next(iter(self.app.visitors.values())).last_launch = 0
        code, _ = await self.call('/api/run', {**payload, 'use_api_depth': False})
        self.assertEqual(code, 200)
        self.assertEqual(len(self.providers), 1)
        self.assertEqual(depth_calls, ['codex'])

    async def test_failed_depth_preflight_never_queues_and_invalid_options_are_rejected(self):
        def fail_depth(*args):
            raise RequestError(503, 'Fresh paired RGB-D unavailable')
        self.app.api_depth_provider_factory = fail_depth
        _, session = await self.call('/api/session', {})
        self.csrf = session['csrf']
        payload = {'provider': 'codex', 'model': 'gpt-6-astra', 'prompt': 'Move block',
                   'runner_name': 'Local test', 'use_api_depth': True}
        for extra, expected in [({}, 503), ({'use_api_depth': 'true'}, 400),
                                ({'provider': 'claude'}, 400)]:
            code, _ = await self.call('/api/run', {**payload, **extra})
            self.assertEqual(code, expected)
            _, state = await self.call('/api/status')
            self.assertEqual(state['status'], 'idle')
            self.assertEqual(self.providers, [])

    async def test_aspire_high_standard_payload_reaches_runner_configuration(self):
        provider = ScriptedAdapter([])
        with patch.object(self.app, 'api_depth_provider_factory', return_value=provider):
            _, session = await self.call('/api/session', {})
            self.csrf = session['csrf']
            code, _ = await self.call('/api/run', {
                'provider': 'codex', 'model': 'gpt-6-astra', 'prompt': 'Move block',
                'reasoning_effort': 'high', 'response_speed': 'standard', 'use_api_depth': True})
        self.assertEqual(code, 200)
        self.assertEqual(provider.reasoning_effort, 'high')
        self.assertEqual(provider.response_speed, 'standard')

    async def call(self, path, payload=None, query=None):
        from urllib.parse import urlencode
        messages = []
        async def receive():
            return {'type':'http.request', 'body':json.dumps(payload or {}).encode()}
        async def send(message):
            messages.append(message)
        await self.app({'type':'http', 'method':'POST' if payload is not None else 'GET',
            'path':path, 'query_string':urlencode(query or {}).encode(), 'headers':[(k.encode(),v.encode()) for k,v in {
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

    async def test_robohouse_session_exposes_configured_continuous_stream(self):
        stream = {'path': 'robo-house', 'cameras': ['top', 'left', 'right']}
        rid = 'robot-ba8413962083809c'
        with patch.dict('os.environ', {'YAM_VIDEO_STREAMS': json.dumps({rid: stream})}):
            app = LocalPlayground(public_origin='http://127.0.0.1:8791',
                robot_id=rid, session_api=None, camera_origin='http://127.0.0.1:8089',
                development=True, api_factory=MockSessionAPI)
        previous, self.app = self.app, app
        try:
            code, body = await self.call('/api/session', {})
            self.assertEqual(code, 200)
            self.assertEqual(body['robot_id'], rid)
            self.assertEqual(body['video_stream'], stream)
        finally:
            for visitor in app.visitors.values():
                await asyncio.to_thread(visitor.close)
            shutil.rmtree(app.root, ignore_errors=True)
            self.app = previous

    async def test_other_robot_stream_is_proxied_without_browser_credentials(self):
        from unittest.mock import MagicMock
        self.app._fleet_apps = {'yam-1': self.app, 'robo': SimpleNamespace(video_stream={'path':'robo-house','cameras':['top','left','right']})}
        response = MagicMock(status=200, headers={'Content-Type':'application/vnd.apple.mpegurl'})
        response.__enter__.return_value = response
        response.read.return_value = b'#EXTM3U\n'
        with patch('local_playground.request.urlopen', return_value=response) as upstream:
            code, body = await self.call('/robo-house/index.m3u8')
        self.assertEqual((code, body), (200, b'#EXTM3U\n'))
        request = upstream.call_args.args[0]
        self.assertEqual(request.full_url, 'https://playground.blupe.io/robo-house/index.m3u8')
        self.assertEqual(request.get_header('Origin'), 'https://playground.blupe.io')
        self.assertIsNone(request.get_header('Cookie'))
        self.assertIsNone(request.get_header('X-yam-runner-token'))

    async def test_shared_page_and_video_library_are_served(self):
        code,body=await self.call('/')
        self.assertEqual(code,200)
        self.assertIn(b'aria-label="Task prompt"',body)
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
