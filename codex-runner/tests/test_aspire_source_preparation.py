"""Source preparation is motion-free; optional native tests exercise real HTTP."""
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from remote_yam.aspire_source_preparation import SourcePreparation, validate_source_interface

SOURCE = "def build_task(tools):\n return {'steps': []}\ndef evaluate(tools, task):\n return {'success': False}\n"


def reply(source=SOURCE):
    return dict(action='program', source=source, summary='Fixture program', lesson='Fixture only',
                queries=[dict(name='block', query='green block')])


class PreparationTests(unittest.TestCase):
    def policy(self, replies):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        policy = SimpleNamespace(directory=root, task='Full fixture prompt', validated_program=None,
            initial_feedback=None, generator=None, instructions='Fixture contract', max_revisions=2,
            _coding_provider=None, _retrieved_lineage=[], resume_conversation=None, _history=[],
            model='gpt-6-astra', coding_timeout_s=180, reasoning_effort='high', response_speed='standard',
            cancelled=lambda: False, _check_cancelled=Mock(), _task_update=Mock(), _emit=Mock())
        policy._physical_feedback_path = lambda: root/'no-physical-feedback'
        policy._coding_feedback_path = lambda: root/'no-coding-feedback'
        def prepare(prompt):
            (root/'handoff.json').write_text(json.dumps(dict(validation='PREVIOUS_FULL_PLAN_ONLY',
                source_sha256=hashlib.sha256(policy.validated_program['source'].encode()).hexdigest(),
                physical_motion_calls=0, queue_session_created=False, live_replanning_required=True)))
        policy._prepare_program_before_session = Mock(side_effect=prepare)
        policy._generate = Mock(side_effect=replies)
        adapter = SourcePreparation(policy, Mock(), lambda value, _: dict(
            program_sha256=hashlib.sha256(value['source'].encode()).hexdigest()), {})
        return policy, adapter

    def test_source_handoff_never_claims_a_scene_or_native_plan(self):
        policy, adapter = self.policy([reply()])
        adapter.prepare(policy.task)
        self.assertTrue(policy.initialize_before_observation)
        scene = policy._generate.call_args.args[0]
        self.assertEqual(scene['images'], {})
        self.assertIsNone(scene['snapshot'])
        self.assertFalse(scene['context']['current_scene_available'])
        handoff = json.loads((policy.directory/'handoff.json').read_text())
        self.assertEqual(handoff['validation'], 'SOURCE_VALIDATED_FRESH_PLAN_REQUIRED')
        self.assertIsNone(handoff['planning_success'])
        self.assertFalse(handoff['queue_session_created'])
        self.assertEqual(handoff['physical_motion_calls'], 0)
        self.assertEqual(Path(handoff['source']).read_text(), SOURCE)
        self.assertIsNone(policy.generator)

    def test_invalid_interface_revises_and_preserves_original_source(self):
        invalid = SOURCE.replace('build_task(tools)', 'build_task()')
        policy, adapter = self.policy([reply(invalid), reply()])
        adapter.prepare(policy.task)
        self.assertEqual(policy._generate.call_count, 2)
        feedback = policy._generate.call_args.args[1]
        self.assertEqual(feedback['previous_source'], invalid)
        self.assertEqual(feedback['status'], 'CODE_ERROR')
        original = json.loads((policy.directory/'source-preparation/attempt-01/coding-response.json').read_text())
        self.assertEqual(original['source'], invalid)
        self.assertEqual(policy.validated_program['source'], SOURCE)

    def test_exhaustion_blocks_before_handoff(self):
        invalid = reply('def build_task(): pass\ndef evaluate(): pass\n')
        policy, adapter = self.policy([invalid]*3)
        with self.assertRaisesRegex(RuntimeError, 'budget exhausted'):
            adapter.prepare(policy.task)
        self.assertFalse((policy.directory/'handoff.json').exists())
        self.assertIsNone(policy.validated_program)
        failure = json.loads((policy.directory/'source-preparation/failure.json').read_text())
        self.assertEqual(failure['physical_motion_calls'], 0)
        self.assertFalse(failure['queue_session_created'])

    def test_stop_after_generation_prevents_handoff(self):
        policy, adapter = self.policy([reply()])
        policy._check_cancelled.side_effect = [None, RuntimeError('Stopped')]
        with self.assertRaisesRegex(RuntimeError, 'Stopped'):
            adapter.prepare(policy.task)
        self.assertIsNone(policy.validated_program)
        self.assertFalse((policy.directory/'handoff.json').exists())

    def test_saved_program_uses_native_handoff_without_generation(self):
        policy, adapter = self.policy([])
        policy.validated_program = reply()
        adapter.prepare(policy.task)
        policy._generate.assert_not_called()
        self.assertFalse((policy.directory/'source-preparation').exists())

    def test_subscription_request_has_no_camera_attachment_and_keeps_conversation(self):
        policy, adapter = self.policy([])
        provider = Mock(_thread_id='fixture-conversation', _calls=0)
        provider._post_json.return_value = reply()
        with patch('remote_yam.codex_policy.CodexAdapter', return_value=provider):
            adapter.request_text(prompt='Full fixture prompt', scene={}, feedback={})
        self.assertIs(policy._coding_provider, provider)
        self.assertEqual(provider._expected_camera_count, 0)
        payload = provider._post_json.call_args.args[0]
        self.assertEqual(payload['tools'], [])
        self.assertEqual([item['role'] for item in payload['input']], ['user','assistant'])
        self.assertEqual(payload['input'][0]['content'][0]['type'], 'input_text')

    def test_interface_rejects_async_wrong_arity_and_required_keyword_arguments(self):
        for source in (SOURCE.replace('def build_task', 'async def build_task'),
                       SOURCE.replace('build_task(tools)', 'build_task()'),
                       SOURCE.replace('evaluate(tools, task)', 'evaluate(tools, task, extra)'),
                       SOURCE.replace('build_task(tools)', 'build_task(tools, *, required)')):
            with self.subTest(source=source), self.assertRaises(ValueError):
                validate_source_interface(source)
        validate_source_interface(SOURCE)

    def transport(self, generate):
        from remote_yam.aspire_worker import AspireWorkerServer, RemoteAspirePolicy
        policy, adapter = self.policy([])
        policy._generate.side_effect = generate
        policy.public_config = lambda: dict(provider='codex', model='gpt-6-astra', phase=policy._phase)
        policy._phase = 'idle'
        policy.prepare_before_session = adapter.prepare
        policy.close_transport = Mock()
        def check_cancelled():
            if policy.cancelled():
                raise RuntimeError('Preparation cancelled')
        policy._check_cancelled = check_cancelled
        created = []
        def factory(prompt):
            created.append(prompt)
            return policy
        server = AspireWorkerServer(('127.0.0.1', 0), 'fixture-token-'*4, 'fixture', factory)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        remote = RemoteAspirePolicy('http://127.0.0.1:'+str(server.server_port), 'fixture-token-'*4,
                                    robot_id='fixture', poll_s=.02, timeout_s=15)
        self.addCleanup(remote.close_transport)
        return policy, remote, server, created

    def test_slow_source_preparation_exceeds_http_timeout_without_replay(self):
        def generate(scene, feedback):
            time.sleep(5.2)  # Each HTTP request has a five-second timeout.
            return reply()
        policy, remote, server, created = self.transport(generate)
        started = time.monotonic()
        remote.prepare_before_session(policy.task)
        self.assertGreaterEqual(time.monotonic()-started, 5.2)
        self.assertEqual(created, [policy.task])
        self.assertEqual(policy._generate.call_count, 1)
        self.assertEqual(len(server.runs[remote._run_id].calls), 1)
        self.assertTrue((policy.directory/'handoff.json').exists())

    def test_stop_during_slow_preparation_has_no_late_handoff_or_replay(self):
        started, stop = threading.Event(), threading.Event()
        def generate(scene, feedback):
            started.set()
            while not policy.cancelled():
                time.sleep(.01)
            return reply()  # Even a late valid response must not be admitted.
        policy, remote, server, created = self.transport(generate)
        remote.cancelled = stop.is_set
        errors = []
        def prepare():
            try:
                remote.prepare_before_session(policy.task)
            except RuntimeError as exc:
                errors.append(type(exc).__name__)
        thread = threading.Thread(target=prepare)
        thread.start()
        self.assertTrue(started.wait(2))
        stop.set()
        thread.join(3)
        self.assertFalse(thread.is_alive())
        deadline = time.monotonic()+2
        while server.runs[remote._run_id].active and time.monotonic()<deadline:
            time.sleep(.01)
        self.assertEqual(errors, ['RuntimeError'])
        self.assertEqual(created, [policy.task])
        self.assertEqual(policy._generate.call_count, 1)
        self.assertIsNone(policy.validated_program)
        self.assertFalse((policy.directory/'handoff.json').exists())
        self.assertTrue(server.runs[remote._run_id].cancelled.is_set())


try:
    from remote_yam.aspire_codex_policy import AspireCodexPolicy
    from remote_yam.aspire_source_preparation import configure_source_preparation
    from remote_yam.aspire_api_policy import BridgeClient
except ModuleNotFoundError:
    AspireCodexPolicy = None


@unittest.skipIf(AspireCodexPolicy is None, 'Optional native ASPIRE station package is not installed')
class NativeLifecycleTests(unittest.TestCase):
    def test_real_native_policy_through_http_prepares_before_one_queue_and_fresh_execute(self):
        self.run_lifecycle()

    def test_live_planning_failure_revises_source_under_the_same_public_reservation(self):
        self.run_lifecycle(repair=True)

    def test_dispatch_uncertainty_never_revises_or_replays(self):
        self.run_lifecycle(uncertain=True)

    def run_lifecycle(self, *, repair=False, uncertain=False):
        from remote_yam.aspire_worker import AspireWorkerServer, RemoteAspirePolicy
        from remote_yam.controller import RunnerController
        from remote_yam.session import MockSessionAPI
        order = []
        class Session(MockSessionAPI):
            def create_session(self, *args, **kwargs):
                order.append('queue_home')
                return super().create_session(*args, **kwargs)
        session = Session(auto_activate=True)
        def generate(**request):
            if 'generate_source' not in order:
                self.assertEqual(session.create_requests, [])
                self.assertFalse(request['scene']['context']['current_scene_available'])
                order.append('generate_source')
                return reply()
            self.assertTrue(repair)
            self.assertEqual(len(session.create_requests), 1)
            self.assertEqual(request['feedback']['previous_source'], SOURCE)
            order.append('revise_source')
            return reply(SOURCE+'\n# Revised from the native failure\n')
        def harness(**request):
            if request['mode']=='plan':
                self.assertTrue(repair)
                self.assertIsNone(request['bridge_socket'])
                self.assertEqual(request['snapshot'], 'fixture-failed-scene')
                self.assertEqual(session.trajectory_log, [])
                order.append('offline_full_plan')
                return dict(status='PLAN_ONLY', planning_success=True, success=False)
            self.assertEqual(request['mode'], 'execute')
            self.assertNotIn('snapshot', request)
            expected = SOURCE+'\n# Revised from the native failure\n' if 'revise_source' in order else SOURCE
            self.assertEqual(Path(request['program']).read_text(), expected)
            self.assertEqual(len(session.create_requests), 1)
            client = BridgeClient(request['bridge_socket'])
            observation = client.call('observation')
            self.assertTrue(policy._identity[1])
            order.append('fresh_capture')
            if repair and 'revise_source' not in order:
                order.append('native_plan_failed')
                return dict(status='PLAN_FAILED', planning_success=False, success=False,
                    reason='Fixture native IK failure', physical_motion_calls=0,
                    initial_scene=dict(status='OBSERVED', context={}, images={}, snapshot='fixture-failed-scene'))
            order.append('full_native_plan')
            self.assertEqual(session.trajectory_log, [])
            paths = [[([value*3.141592653589793/180 for value in observation[side+'_joints_deg']]
                       +[observation[side+'_gripper']])]*2 for side in ('left','right')]
            result = client.call('trajectory', timestamps=[0., .1], left=paths[0], right=paths[1], start_interp_s=0.)
            self.assertTrue(result['success'])
            order.append('execute')
            if uncertain:
                return dict(status='HARNESS_ERROR', planning_success=False, success=False,
                            reason='Fixture process exited after dispatch', physical_motion_calls=0,
                            failure_kind='process')
            return dict(status='SIMULATION_COMPLETE', planning_success=True, success=False, physical_motion_calls=0)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = AspireCodexPolicy(harness=harness, calibration={}, robot_id='fixture', task='Full fixture prompt',
                directory=root/'run', instructions='Fixture only', skill_directory=root/'skills',
                generator=generate, allow_hardware=False, max_revisions=2)
            configure_source_preparation(policy)
            server = AspireWorkerServer(('127.0.0.1', 0), 'fixture-token-'*4, 'fixture', lambda prompt: policy)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            remote = RemoteAspirePolicy('http://127.0.0.1:'+str(server.server_port),
                                        'fixture-token-'*4, robot_id='fixture', poll_s=.01)
            self.addCleanup(remote.close_transport)
            controller = RunnerController(session, robot_id='fixture', hardware_control_enabled=False,
                                          submit_attempts=1, share_conversation=False)
            controller.join_and_run(remote, policy.task)
            controller._worker.join(10)
            if controller._worker.is_alive():
                controller.stop()
            self.assertFalse(controller._worker.is_alive(), controller.status())
            if not uncertain:
                self.assertIsNone(controller.status()['error'], controller.status())
            expected_order = ['generate_source','queue_home','fresh_capture']
            if repair:
                expected_order += ['native_plan_failed','revise_source','offline_full_plan','fresh_capture']
            expected_order += ['full_native_plan','execute']
            self.assertEqual(order, expected_order)
            self.assertEqual(len(session.create_requests), 1)
            self.assertEqual(len(session.trajectory_log), 1)
            self.assertEqual(policy._attempts[0]['lineage']['program_sha256'], hashlib.sha256(SOURCE.encode()).hexdigest())
            if repair:
                self.assertEqual(policy._attempts[0]['execution']['status'], 'PLAN_FAILED')
                self.assertNotEqual(policy._attempts[0]['lineage']['program_sha256'],
                                    policy._attempts[1]['lineage']['program_sha256'])
            if uncertain:
                self.assertEqual(len(policy._attempts), 1)
                self.assertEqual(policy._saved_motion_requests, 1)
                self.assertEqual(policy._attempts[0]['execution']['status'], 'HARNESS_ERROR')


if __name__ == '__main__':
    unittest.main()
