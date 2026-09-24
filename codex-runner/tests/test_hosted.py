"""Real controller lifecycle tests through the hosted ASGI HTTP boundary."""
import asyncio
import io
import json
from pathlib import Path
import shutil
import sys
import time
import unittest
from unittest.mock import patch
from urllib import error, request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import playground as hosted
from remote_yam.providers import ScriptedAdapter, PolicyComplete, OpenAIAdapter
from remote_yam.session import MockSessionAPI
from remote_yam.cameras import CameraFrame


class HeldProvider(ScriptedAdapter):
    def __init__(self, key):
        super().__init__([])
        self._api_key = key


class HostedTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.apis, self.providers = [], []
        def api_factory():
            api = MockSessionAPI(auto_activate=False)
            self.apis.append(api)
            return api
        def provider_factory(name, key, model, directory):
            provider = HeldProvider(key)
            self.providers.append(provider)
            return provider
        self.app = hosted.HostedRunner(public_origin="https://robot.example", session_api="https://session.example",
                                       camera_origin="https://session.example", api_factory=api_factory,
                                       provider_factory=provider_factory)
        from remote_yam.past_runs import RunNames
        self.app.run_names = RunNames(self.app.root/'names.sqlite3')

    async def test_groot_joins_same_queue_without_visitor_key_and_starts_preparation(self):
        keyfile = self.app.root/'groot-test-key'
        keyfile.write_text('service-secret')
        self.app.groot_key_file = str(keyfile)
        capability, visitor = self.app.new_visitor()
        prepared = []
        from remote_yam.groot_policy import GrootAdapter
        def prepare(provider, cancelled):
            prepared.append((visitor.controller.status()['status'], cancelled))
        with patch.object(GrootAdapter, 'start_preparation', prepare):
            result = self.app.launch(visitor, {'provider':'groot', 'model':'groot-reviewed-step10000',
                'prompt':'Put the orange block in the purple bin', 'runner_name':'Tester', 'run_duration_s':300})
        self.assertTrue(result['ok'])
        self.assertEqual(prepared[0][0], 'queued')
        self.assertNotIn('groot', visitor.saved_keys)
        self.assertNotIn('service-secret', str(visitor.controller.status()))
        self.assertEqual(visitor.controller.status()['provider']['provider'], 'groot')
        visitor.controller.stop()
        self.assertTrue(prepared[0][1]())


    async def test_public_robot_selector_catalog(self):
        self.app.robots = [
            {"id": "yam-1", "name": "YAM", "url": "https://robot.example/", "private": "excluded"},
            {"id": "isaac", "name": "Isaac", "url": "https://isaac.example/"}]
        code, data, _ = await self.call('/api/robots')
        self.assertEqual(code, 200)
        self.assertEqual(data['selected'], 'yam-1')
        self.assertEqual(len(data['robots']), 2)
        self.assertNotIn('private', data['robots'][0])
        self.assertEqual(data['robots'][1]['url'], 'https://isaac.example/')

    async def test_contact_save_failure_keeps_accepted_session_queued(self):
        browser = await self.session()
        with patch.object(hosted, 'save_handles', side_effect=OSError('read-only filesystem')):
            code, data, _ = await self.launch(browser)
        self.assertEqual(code, 200)
        visitor = list(self.app.visitors.values())[-1]
        state = visitor.controller.status()
        self.assertEqual(state['status'], 'queued')
        self.assertIsNotNone(state['session_id'])
        self.assertTrue(visitor.controller.busy())

    async def test_past_runs_public_names_and_invalid_page(self):
        from unittest.mock import Mock
        self.app.past_runs.page = Mock(return_value={'runs':[{'episode_id':'ep_known'}], 'next_offset':None})
        self.app.run_names.remember('ep_known', 'Alex')
        code, data, _ = await self.call('/api/past-runs')
        self.assertEqual(code, 200)
        self.assertEqual(data['runs'][0]['runner_name'], 'Alex')

    async def test_name_storage_failure_does_not_block_stop(self):
        from unittest.mock import Mock
        _, visitor = self.app.new_visitor()
        visitor.runner_session_id = 'session-test'
        visitor.controller.status = Mock(return_value={'episode_id':'ep_test', 'session_id':'session-test'})
        self.app.run_names.remember = Mock(side_effect=PermissionError('read only'))
        self.app.remember_runner(visitor)

    async def asyncTearDown(self):
        for visitor in list(self.app.visitors.values()):
            await asyncio.to_thread(visitor.close)
        if self.app.cleanups:
            await asyncio.gather(*self.app.cleanups)
        shutil.rmtree(self.app.root, ignore_errors=True)

    async def call(self, path, *, method="GET", payload=None, cookie="", csrf="", origin="https://robot.example", extra=None, raw=None):
        headers = {"host": "robot.example", "origin": origin, "content-type": "application/json",
                   "cookie": cookie, "x-yam-runner-token": csrf}
        headers.update(extra or {})
        scope = {"type": "http", "method": method, "path": path,
                 "headers": [(k.encode(), v.encode()) for k, v in headers.items()]}
        received = False
        async def receive():
            nonlocal received
            if received:
                return {"type": "http.disconnect"}
            received = True
            return {"type": "http.request", "body": raw if raw is not None else json.dumps(payload or {}).encode()}
        sent = []
        async def send(message):
            sent.append(message)
        await self.app(scope, receive, send)
        status = sent[0]["status"]
        result_headers = dict(sent[0]["headers"])
        body = b"".join(m.get("body", b"") for m in sent[1:])
        result = json.loads(body) if result_headers[b"content-type"] == b"application/json" else body
        return status, result, result_headers

    async def session(self):
        code, config, headers = await self.call("/api/session", method="POST")
        self.assertEqual(code, 200)
        cookie = headers[b"set-cookie"].decode().split(";", 1)[0]
        return {"cookie": cookie, "csrf": config["csrf"]}

    async def launch(self, browser, secret="test-visitor-key"):
        return await self.call("/api/run", method="POST", payload={
            "provider": "openai", "model": "test-model", "api_key": secret, "prompt": "Private task"}, **browser)

    async def test_claude_api_joins_queue_and_retains_only_its_own_key(self):
        browser = await self.session()
        code, result, _ = await self.call('/api/run', method='POST', payload={
            'provider':'anthropic', 'model':'claude-opus-5-5', 'api_key':'claude-test-key',
            'prompt':'Move block'}, **browser)
        self.assertEqual(code, 200, result)
        visitor = next(iter(self.app.visitors.values()))
        self.assertEqual(visitor.controller.status()['status'], 'queued')
        self.assertEqual(visitor.saved_keys, {'anthropic':'claude-test-key'})
        self.assertFalse(self.app.local_claude)
        other = await self.session()
        code, _, _ = await self.call('/api/run', method='POST', payload={
            'provider':'anthropic', 'model':'claude-opus-5-5', 'prompt':'Move block'}, **other)
        self.assertEqual(code, 400)

    async def test_legacy_controller_without_analytics_keeps_queued_run(self):
        browser = await self.session()
        visitor = next(iter(self.app.visitors.values()))
        controller = visitor.controller

        class LegacyController:
            def __getattr__(self, name):
                if name == '_analytics_run':
                    raise AttributeError(name)
                return getattr(controller, name)

        visitor.controller = LegacyController()
        code, result, _ = await self.launch(browser)
        self.assertEqual(code, 200, result)
        self.assertEqual(controller.status()['status'], 'queued')
        self.assertTrue(visitor.runner_session_id)
        self.assertFalse(any(e['kind'] == 'disconnect' for e in
                             controller._interactions.snapshot()['events']))




    async def test_builtin_cannot_publish_an_unrelated_object_task(self):
        browser = await self.session()
        code, _, _ = await self.call('/api/run', method='POST', payload={
            'provider':'local_raise_lower', 'prompt':'move the apple out of the plate',
            'runner_name':'Fixture'}, **browser)
        self.assertEqual(code, 200)
        visitor = next(v for v in self.app.visitors.values() if v.runner_session_id)
        self.assertEqual(visitor.runner_task, 'Raise and lower both arms for three cycles.')
        self.assertEqual(visitor.controller._prompt, visitor.runner_task)

    async def test_named_run_is_visible_to_other_visitors(self):
        a, b = await self.session(), await self.session()
        payload = dict(provider='openai', model='test-model', api_key='test-visitor-key',
                       prompt='Place green block', runner_name='Andrew')
        self.assertEqual((await self.call('/api/run', method='POST', payload={**payload, 'runner_name':'x'*33}, **a))[0], 400)
        self.assertEqual((await self.call('/api/run', method='POST', payload=payload, **a))[0], 200)
        owner = next(v for v in self.app.visitors.values() if v.runner_session_id)
        owner.controller._interactions.add('model_response', 'Picking up the block', private_path='/private/test', api_key='never-share')
        current = owner.controller.status()
        with patch.object(owner.controller, 'status', return_value={**current, 'status':'running'}):
            code, state, _ = await self.call('/api/status', **b)
        self.assertEqual(code, 200)
        self.assertEqual(state['whats_running'], [dict(runner_name='Andrew', task='Place green block', status='running')])
        self.assertEqual(state['public_run']['task'], 'Place green block')
        self.assertEqual(state['public_run']['events'][-1]['message'], 'Picking up the block')
        self.assertNotIn('never-share', json.dumps(state))
        self.assertNotIn('/private/test', json.dumps(state))
        self.assertNotIn('test-visitor-key', json.dumps(state))
        self.assertEqual((await self.call('/api/status', **b))[1]['whats_running'], [])

    async def test_run_error_is_shared_with_all_spectators_and_clears(self):
        owner_browser, b, c = await self.session(), await self.session(), await self.session()
        await self.launch(owner_browser)
        owner = next(v for v in self.app.visitors.values() if v.runner_session_id)
        current = owner.controller.status()
        for phase in ('running', 'stopped'):
            with patch.object(owner.controller, 'status', return_value={
                    **current, 'status':phase, 'error':'RuntimeError: private-key /private/path'}):
                for viewer in (b,c):
                    state = (await self.call('/api/status', **viewer))[1]
                    self.assertEqual(state['public_run']['error'], 'The run encountered a model or runner error.')
                    self.assertNotIn('private-key', json.dumps(state))
                    self.assertNotIn('/private/path', json.dumps(state))
        with patch.object(owner.controller, 'status', return_value={**current, 'status':'running', 'error':None}):
            self.assertIsNone((await self.call('/api/status', **b))[1]['public_run']['error'])

    async def test_stream_health_endpoint_validates_and_logs_no_private_fields(self):
        browser = await self.session()
        report = {'state':'stalled', 'transport':'webrtc', 'fps':0, 'frame_age_ms':8000, 'delay_ms':None}
        with self.assertLogs('remote_yam.stream_health', level='WARNING') as captured:
            self.assertEqual((await self.call('/api/stream-health', method='POST', payload=report, **browser))[0], 200)
        self.assertIn('state=stalled', captured.output[0])
        self.assertEqual((await self.call('/api/stream-health', method='POST', payload={**report,'private':'secret'}, **browser))[0],400)
        self.assertEqual((await self.call('/api/stream-health', method='POST', payload={**report,'state':{}}, **browser))[0],400)
        self.assertEqual((await self.call('/api/stream-health', method='POST', payload=report, cookie=browser['cookie'],csrf='wrong'))[0],403)
        self.assertEqual((await self.call('/static/stream-health.js'))[0],200)

    async def test_social_handles_are_private_and_retained_after_session_cleanup(self):
        import sqlite3
        self.app.social_database = self.app.root / 'socials.sqlite3'
        a, b = await self.session(), await self.session()
        payload = dict(provider='local_raise_lower', prompt='Raise arms', runner_name='Runner',
                       x_handle=' @runner_x ', instagram_handle='@runner.insta')
        for invalid in ('https://example.com', '<script>', ['name']):
            self.assertEqual((await self.call('/api/run', method='POST', payload={**payload, 'x_handle':invalid}, **a))[0], 400)
        self.assertEqual((await self.call('/api/run', method='POST', payload=payload, **a))[0], 200)
        owner = next(v for v in self.app.visitors.values() if v.runner_session_id)
        session_id = owner.runner_session_id
        public = json.dumps((await self.call('/api/status', **b))[1])
        self.assertNotIn('runner_x', public)
        self.assertNotIn('runner.insta', public)
        await asyncio.to_thread(owner.close)
        with sqlite3.connect(self.app.social_database) as db:
            self.assertEqual(db.execute('SELECT session_id, runner_name, x_handle, instagram_handle FROM run_socials').fetchone(),
                             (session_id, 'Runner', 'runner_x', 'runner.insta'))

    async def test_shared_chat_is_bounded_and_session_protected(self):
        a, b = await self.session(), await self.session()
        payload = {"name": "Andrew", "text": "Hello <script>world</script>"}
        self.assertEqual((await self.call('/api/chat', method='POST', payload=payload))[0], 401)
        self.assertEqual((await self.call('/api/chat', method='POST', payload=payload, cookie=a['cookie']))[0], 403)
        self.assertEqual((await self.call('/api/chat', method='POST', payload=payload, **a))[0], 200)
        data = (await self.call('/api/chat', **b))[1]
        self.assertEqual(data['messages'][0]['text'], payload['text'])
        self.assertNotEqual(data['messages'][0]['visitor'], data['visitor'])
        self.assertNotIn(a['csrf'], json.dumps(data))
        self.assertEqual((await self.call('/api/chat', method='POST', payload=payload, **a))[0], 429)
        self.assertEqual((await self.call('/api/chat', method='POST', payload={'name':'x','text':'x'*1001}, **b))[0], 400)
        visitor = self.app.visitors[a['cookie'].split('=',1)[1]]
        for _ in range(101):
            visitor.last_chat = -float('inf')
            self.assertEqual((await self.call('/api/chat', method='POST', payload=payload, **a))[0], 200)
        self.assertEqual(len((await self.call('/api/chat', **b))[1]['messages']), 100)

    async def test_sessions_and_keys_never_cross_visitors(self):
        a, b = await self.session(), await self.session()
        secret = "test-secret-not-in-browser-responses"
        self.assertEqual((await self.launch(a, secret))[0], 200)
        code, state, _ = await self.call("/api/status", **a)
        self.assertEqual(state["status"], "queued")
        self.assertTrue(state["key_configured"])
        self.assertNotIn(secret, json.dumps(state))
        self.assertEqual((await self.call("/api/status", **b))[1]["status"], "idle")
        self.assertEqual((await self.call("/api/recordings", **b))[1]["runs"], [])
        self.assertEqual((await self.call("/api/stop", method="POST", **b))[0], 200)
        self.assertEqual(self.providers[0]._api_key, secret)
        self.assertEqual((await self.call("/api/stop", method="POST", cookie=b["cookie"], csrf=a["csrf"]))[0], 403)
        records = (await self.call("/api/recordings", **a))[1]["runs"]
        self.assertTrue(records)
        other_run = records[0]["run_id"]
        self.assertEqual((await self.call(f"/api/recordings/{other_run}/interactions", **b))[0], 404)
        self.assertEqual((await self.call(f"/api/recordings/{other_run}/log.zip", **b))[0], 404)
        for path in self.app.root.rglob("*.jsonl"):
            self.assertNotIn(secret, path.read_text())
        self.assertNotIn(secret, json.dumps(self.apis[1].create_requests))

    async def test_key_reused_after_stop_refresh_and_replaced(self):
        browser = await self.session()
        other = await self.session()
        visitor = self.app.visitors[browser['cookie'].split('=', 1)[1]]
        for key, expected in [('first-test-key', 'first-test-key'), ('', 'first-test-key'), ('replacement-test-key', 'replacement-test-key')]:
            visitor.last_launch = -float('inf')
            code, data, _ = await self.launch(browser, key)
            self.assertEqual(code, 200, data)
            self.assertEqual(self.providers[-1]._api_key, expected)
            await self.call('/api/stop', method='POST', **browser)
            for _ in range(100):
                if not visitor.controller.busy(): break
                await asyncio.sleep(.01)
            state = (await self.call('/api/status', **browser))[1]
            self.assertEqual(state['saved_key_providers'], ['openai'])
            self.assertNotIn(expected, json.dumps(state))
        self.assertEqual((await self.launch(other, ''))[0], 400)
        await self.call('/api/credentials/clear', method='POST', **browser)
        visitor.last_launch = -float('inf')
        self.assertEqual((await self.launch(browser, ''))[0], 400)
        self.assertEqual(visitor.saved_keys, {})


    async def test_forget_stops_run_and_clears_retained_key(self):
        browser = await self.session()
        await self.launch(browser)
        self.assertEqual((await self.call("/api/credentials/clear", method="POST", **browser))[0], 200)
        self.assertEqual(self.providers[0]._api_key, "")
        state = (await self.call("/api/status", **browser))[1]
        self.assertFalse(state["key_configured"])
        self.assertNotIn(state["status"], hosted.ACTIVE)

    async def test_leave_queue_cancels_only_own_waiting_run(self):
        first, second = await self.session(), await self.session()
        await self.launch(first)
        await self.launch(second)
        self.assertEqual((await self.call('/api/stop', method='POST', **first))[0], 200)
        a = (await self.call('/api/status', **first))[1]
        b = (await self.call('/api/status', **second))[1]
        self.assertNotIn(a['status'], hosted.ACTIVE)
        self.assertEqual(b['status'], 'queued')
        self.assertTrue(a['key_configured'])
        self.assertEqual(list(self.app.visitors.values())[0].controller._session_api.get_queue_snapshot()['entries'], [])

    async def test_startup_fault_reaches_waiting_visitor_and_clears(self):
        browser = await self.session()
        # Public Session API fixture; controller-side formatting is tested in blupe-evals.
        reason = ('Right gripper feedback 1.020001 is outside the accepted range '
                  '[-0.02, 1.02]. Robot unavailable; operator attention needed.')
        observation = self.app.monitor_api.get_robot_observation('yam-1')
        payload = observation.get('observation') or observation.get('payload') or observation
        payload.update(mode='FAULT', safety={'ok':False, 'estop_engaged':False,
                                             'reason':reason})
        self.app.observation = observation
        state = (await self.call('/api/status', **browser))[1]
        self.assertIn('Right gripper feedback 1.020001', state['robot_fault'])
        payload['safety']['reason'] = 'private traceback /home/secret'
        self.assertIsNone((await self.call('/api/status', **browser))[1]['robot_fault'])
        payload['mode'] = 'DISABLED'
        self.assertIsNone((await self.call('/api/status', **browser))[1]['robot_fault'])

    async def test_worker_completion_and_exception_release_key(self):
        for fail in (False, True):
            browser = await self.session()
            controller = list(self.app.visitors.values())[-1].controller
            controller._session_api._auto_activate = True
            outcome = RuntimeError("simulated provider failure") if fail else PolicyComplete("done")
            with patch.object(HeldProvider, "infer", side_effect=outcome):
                self.assertEqual((await self.launch(browser))[0], 200)
                deadline = time.monotonic() + 2
                while controller.busy() and time.monotonic() < deadline:
                    await asyncio.sleep(.01)
                self.assertFalse(controller.busy())
            self.assertEqual(controller._provider._api_key, "")
            self.assertEqual(controller.status()["status"], "stopped")
            self.assertEqual(controller._session_api.action_log, [])

    async def test_reload_preserves_session_but_expiry_cancels_and_releases(self):
        browser = await self.session()
        await self.launch(browser)
        config = (await self.call("/api/session", method="POST", **browser))[1]
        self.assertEqual(config["csrf"], browser["csrf"])
        self.assertEqual(len(self.app.visitors), 1)
        visitor = next(iter(self.app.visitors.values()))
        visitor.born -= self.app.lifetime_seconds + 1
        self.assertEqual((await self.call("/api/status", **browser))[0], 401)
        self.assertEqual(self.providers[0]._api_key, "")
        await asyncio.gather(*self.app.cleanups)
        self.assertFalse(visitor.directory.exists())

    async def test_launch_failure_releases_key_without_echoing_upstream_error(self):
        browser = await self.session()
        visitor = next(iter(self.app.visitors.values()))
        with patch.object(visitor.controller, "join_and_run", side_effect=RuntimeError("secret-upstream-token")):
            status, result, _ = await self.launch(browser)
        self.assertEqual(status, 503)
        self.assertNotIn("secret-upstream-token", json.dumps(result))
        self.assertEqual(self.providers[0]._api_key, "")

    async def test_duplicate_launch_keeps_original_key_and_one_queue_entry(self):
        browser = await self.session()
        await self.launch(browser, "first-visitor-key")
        self.assertEqual((await self.launch(browser, "second-visitor-key"))[0], 409)
        self.assertEqual(len(self.providers), 1)
        self.assertEqual(len(self.apis[1].create_requests), 1)
        self.assertEqual(self.providers[0]._api_key, "first-visitor-key")

    async def test_auth_origin_host_body_limits_and_endpoint_restrictions(self):
        browser = await self.session()
        self.assertEqual((await self.call("/api/status"))[0], 401)
        self.assertEqual((await self.call("/api/session", method="POST", origin="https://evil.example"))[0], 403)
        self.assertEqual((await self.call("/", extra={"host": "evil.example"}))[0], 421)
        self.assertEqual((await self.call("/", extra={"sec-fetch-site": "cross-site"}))[0], 200)
        self.assertEqual((await self.call("/api/run", method="POST", raw=b"x"*24001, **browser))[0], 413)
        self.assertEqual((await self.call("/api/run", method="POST", raw=b"[]", **browser))[0], 400)
        self.assertEqual((await self.call("/api/run", method="POST", payload={"provider":"openai", "api_key":"key-value", "model":"test", "prompt":"task", "endpoint":"http://169.254.169.254"}, **browser))[0], 400)
        self.assertEqual((await self.call("/api/run", method="POST", payload={"provider":"openai", "api_key":"key\r\nvalue", "prompt":"task"}, **browser))[0], 400)
        self.assertEqual(self.providers, [])

    async def test_cookie_and_cache_security_and_session_cap(self):
        status, data, headers = await self.call("/api/session", method="POST")
        cookie = headers[b"set-cookie"].decode()
        for value in ("__Host-", "HttpOnly", "Secure", "SameSite=Strict", "Path=/"):
            self.assertIn(value, cookie)
        self.assertEqual(headers[b"cache-control"], b"no-store")
        self.app.max_sessions = 1
        self.assertEqual((await self.call("/api/session", method="POST"))[0], 503)

    async def test_camera_uses_trusted_named_route_and_shared_cache(self):
        browser = await self.session()
        self.app.observation = {"images": {"left": {"url": "https://session.example/left.jpg"}}}
        frame = CameraFrame("left", b"\xff\xd8test\xff\xd9", time.time())
        with patch.object(self.app.camera_source, "fetch", return_value=frame) as fetch:
            for _ in range(2):
                status, body, _ = await self.call("/api/monitor/cameras/left", **browser)
                self.assertEqual(status, 200)
                self.assertEqual(body, frame.jpeg)
            self.assertEqual(fetch.call_count, 1)
        with self.assertRaises(ValueError):
            self.app.camera_source.fetch("left", "http://169.254.169.254/latest/meta-data")

    async def test_no_key_is_loaded_from_host_environment(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY":"host-secret", "ASTRA_API_KEY":"host-secret"}):
            browser = await self.session()
            result = await self.call("/api/run", method="POST", payload={"provider":"openai", "prompt":"task"}, **browser)
            self.assertEqual(result[0], 400)
            self.assertEqual(self.providers, [])

    def test_inflight_error_redacts_request_key_after_revocation(self):
        provider = OpenAIAdapter("old-request-key", "test-model")
        req = request.Request("https://api.openai.com/v1/responses", headers={"Authorization":"Bearer old-request-key"})
        provider._api_key = ""
        response = error.HTTPError(req.full_url, 401, "Unauthorized", {}, io.BytesIO(b'{"error":{"message":"Invalid old-request-key"}}'))
        with patch("remote_yam.providers.request.urlopen", side_effect=response):
            with self.assertRaises(RuntimeError) as caught:
                provider._send_json(req)
        self.assertNotIn("old-request-key", str(caught.exception))

    def test_failed_event_connection_cancels_known_queue_entry(self):
        api = hosted.HostedSessionAPI("https://session.example")
        with patch.object(hosted.HttpSessionAPI, "open_events", side_effect=ConnectionError()), patch.object(api, "stop_session") as stop:
            with self.assertRaises(ConnectionError):
                api.open_events("sess_known")
            stop.assert_called_once_with("sess_known")


if __name__ == "__main__":
    unittest.main()
