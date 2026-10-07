"""Offline launch routing: source selection precedes all perception and motion."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from remote_yam.aspire_codex_policy import AspireCodexPolicy
from remote_yam.aspire_executable_skills import ExecutableSkillLibrary
from remote_yam.aspire_api_policy import BridgeClient
from remote_yam.controller import RunnerController
from remote_yam.session import MockSessionAPI

SOURCE = "def build_task(tools):\n return {'steps': []}\ndef evaluate(tools, task):\n return {'success': False}\n"
TASK = 'Pick up the green block and place it on the green towel.'


def saved_library(root):
    (root/'core.py').write_text(SOURCE)
    manifest = root/'manifest.json'
    manifest.write_text(json.dumps(dict(skill='pick_place_block', version=1, source='core.py',
        source_sha256=hashlib.sha256(SOURCE.encode()).hexdigest(),
        development_evidence=['fixture only'], validation_scope='Offline test fixture')))
    return ExecutableSkillLibrary(manifest)


class SavedLaunchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.selected = saved_library(self.root).select(TASK)
        self.calls = []
        self.generator = Mock(side_effect=AssertionError('Launch must not generate ASPIRE code'))

    def policy(self, result=None):
        def harness(**call):
            self.calls.append(call)
            self.assertEqual(call['mode'], 'execute')
            self.assertNotIn('snapshot', call)
            self.assertEqual(Path(call['program']).read_text(), self.selected['response']['source'])
            return result or dict(status='UNVERIFIED', planning_success=True,
                success=False, physical_motion_calls=0)
        return AspireCodexPolicy(harness=harness, calibration={}, robot_id='fixture', task=TASK,
            directory=self.root/'run', instructions='', skill_directory=self.root/'skills',
            selected_executable=self.selected, generator=self.generator, allow_hardware=False)

    def test_match_skips_all_prequeue_harness_calls_and_runs_one_live_pass(self):
        policy = self.policy()
        policy.prepare_before_session(TASK)
        self.assertEqual(self.calls, [])
        handoff = json.loads((policy.directory/'handoff.json').read_text())
        self.assertEqual(handoff['validation'], 'SAVED_EXECUTABLE_FRESH_PLAN_REQUIRED')
        self.assertEqual(handoff['source_sha256'], self.selected['provenance']['bound_source_sha256'])
        self.assertEqual(handoff['executable']['inputs'], self.selected['provenance']['inputs'])
        self.assertTrue(handoff['live_replanning_required'])
        self.assertFalse(handoff['code_generation_requested'])
        policy._coding_loop('fresh-lease-bridge')
        self.assertEqual([call['mode'] for call in self.calls], ['execute'])
        self.assertEqual(self.calls[0]['bridge_socket'], 'fresh-lease-bridge')
        trace = json.loads((policy.directory/'attempt-01/lineage.json').read_text())
        self.assertEqual(trace['used'][0]['verification'], 'exact_source_match')
        self.assertEqual(trace['executable'], self.selected['provenance'])
        self.generator.assert_not_called()

    def test_saved_launch_does_not_wait_for_vision_before_queue_home(self):
        policy = self.policy()
        vision = policy.vision_session = Mock()
        vision.wait_ready.side_effect = AssertionError('Cold worker blocked queue/Home')
        policy.prepare_before_session(TASK)
        vision.start.assert_called_once()
        vision.wait_ready.assert_not_called()
        self.assertEqual(policy.public_config()['phase'], 'awaiting_lease')
        self.assertEqual(self.calls, [])

    def test_saved_live_harness_waits_for_vision(self):
        policy = self.policy()
        vision = policy.vision_session = Mock()
        entered, release = threading.Event(), threading.Event()
        def wait_ready():
            entered.set()
            if not release.wait(2):
                raise RuntimeError('Fixture warmup was not released')
        vision.wait_ready.side_effect = wait_ready
        outcome, errors = [], []
        def run():
            try:
                outcome.append(policy._coding_loop('fixture-bridge'))
            except Exception as exc:
                errors.append(exc)
        worker = threading.Thread(target=run)
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            self.assertEqual(self.calls, [])
            self.assertTrue(worker.is_alive())
        finally:
            release.set()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(outcome[0]['status'], 'UNVERIFIED')
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0]['bridge_socket'], 'fixture-bridge')
        vision.close.assert_called_once()

    def test_saved_live_vision_failure_prevents_execution(self):
        failed = self.policy()
        failed.vision_session = Mock()
        failed.vision_session.wait_ready.side_effect = RuntimeError('GPU unavailable')
        with self.assertRaisesRegex(RuntimeError, 'GPU unavailable'):
            failed._coding_loop('fixture-bridge')
        self.assertEqual(self.calls, [])
        failed.vision_session.close.assert_called_once()

    def test_native_failure_is_preserved_without_second_capture_repair_or_retry(self):
        failure = dict(status='PLAN_FAILED', planning_success=False, success=False,
            physical_motion_calls=0, reason='Native carry IK failed')
        policy = self.policy(failure)
        policy.prepare_before_session(TASK)
        self.assertEqual(policy._coding_loop('bridge'), failure)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(json.loads(policy._coding_feedback_path().read_text())['reason'], failure['reason'])
        self.generator.assert_not_called()

    def test_selected_source_tampering_fails_before_harness_or_queue(self):
        self.selected = copy.deepcopy(self.selected)
        self.selected['response']['source'] += '\n# changed after selection\n'
        with self.assertRaisesRegex(ValueError, 'source hash'):
            self.policy()
        self.assertEqual(self.calls, [])
        self.generator.assert_not_called()

    def test_cancellation_before_preparation_creates_no_harness_pass(self):
        policy = self.policy();policy.cancelled = lambda: True
        with self.assertRaises(Exception):
            policy.prepare_before_session(TASK)
        self.assertEqual(self.calls, [])
        self.assertFalse((policy.directory/'handoff.json').exists())
        self.generator.assert_not_called()

    def test_runner_orders_queue_home_then_single_live_harness(self):
        policy = self.policy(dict(status='PLAN_FAILED', planning_success=False,
            success=False, physical_motion_calls=0, reason='fixture native failure'))
        api = MockSessionAPI(auto_activate=True)
        order = [];create = api.create_session
        def create_session(*args, **kwargs):
            self.assertEqual(self.calls, [])
            self.assertEqual(policy._preparation_result['status'], 'AWAITING_INITIALIZED_SCENE')
            order.append('queue');return create(*args, **kwargs)
        api.create_session = create_session
        original = policy.harness
        def live(**call):
            state = BridgeClient(call['bridge_socket']).call('observation')
            self.assertTrue(state['homed']);self.assertTrue(state['settled'])
            order.extend(['home', 'execute']);return original(**call)
        policy.harness = live
        controller = RunnerController(api, robot_id='fixture', hardware_control_enabled=False,
            share_conversation=False, submit_attempts=1)
        self.addCleanup(controller.stop)
        controller.join_and_run(policy, TASK, run_duration_s=300)
        controller._worker.join(10)
        self.assertFalse(controller._worker.is_alive())
        self.assertEqual(order, ['queue', 'home', 'execute'])
        self.assertEqual(len(api.create_requests), 1)
        self.assertEqual(api.trajectory_log, [])
        self.generator.assert_not_called()


class PromptRouteTests(unittest.TestCase):
    def test_exact_task_repair_outside_template_grammar_is_fast_reused_with_context_binding(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);library=saved_library(root)
            task='Move the green block onto the blue chip using the right arm.'
            self.assertIsNone(library.select(task))
            repairs=root/'skills/.repaired-programs';repairs.mkdir(parents=True)
            source=root/'exact.py';source.write_text(SOURCE)
            response=dict(action='program',source=SOURCE,summary='Right arm exact task',lesson='Fixture',
                queries=[dict(name='block',query='green block')])
            receipt=dict(task=task,status='PLAN_VALIDATED',response=response,source=str(source),
                source_sha256=hashlib.sha256(SOURCE.encode()).hexdigest(),base_core_sha256=library.digest,
                base_context_sha256=library.context_sha256,base_inputs=None,parent_run=str(root/'run'),
                parent_attempt_id='aspire-1',original_outcome={'status':'UNVERIFIED'},diagnosis='Fixture')
            path=repairs/(hashlib.sha256(task.casefold().encode()).hexdigest()+'.json')
            path.write_text(json.dumps(receipt))
            library=ExecutableSkillLibrary(library.path,repairs=repairs)
            selected=library.select(task)
            self.assertEqual(selected['response']['source'],SOURCE)
            policy=AspireCodexPolicy(harness=Mock(side_effect=AssertionError('No extra offline pass')),
                calibration={},robot_id='fixture',task=task,directory=root/'next',instructions='',
                skill_directory=root/'skills',selected_executable=selected,execution_environment='local',
                generator=Mock(side_effect=AssertionError('No coding request')))
            policy.prepare_before_session(task)
            self.assertEqual(policy._coding_requests,0)
            self.assertTrue(policy.initialize_before_observation)
            self.assertIsNone(library.select(task+' Keep the left arm parked.'))
            for change in ({'task':task+' extra'}, {'source_sha256':'0'*64}, {'base_core_sha256':'0'*64},
                    {'base_context_sha256':'0'*64}, {'status':'FAILED'}):
                with self.subTest(change=change):
                    path.write_text(json.dumps(dict(receipt,**change)))
                    self.assertIsNone(library.select(task))
            path.write_text(json.dumps(receipt))
            manifest=json.loads(library.path.read_text());manifest['additional_occupants']={'rod':'green rod'}
            library.path.write_text(json.dumps(manifest))
            self.assertIsNone(ExecutableSkillLibrary(library.path,repairs=repairs).select(task))
            source.write_text(SOURCE+'# tampered')
            self.assertIsNone(library.select(task))

    def test_safe_default_web_and_explicit_local_keep_full_unsupported_prompt(self):
        from remote_yam.aspire_executable_skills import select_aspire_launch
        prompt='Stack three blocks; checkpoint every grasp and use the right arm.'
        config={'code_revision_loop':True,'automatic_recovery':True}
        web=select_aspire_launch(config,prompt)
        local=select_aspire_launch(config,prompt,execution_environment='local')
        self.assertEqual(web['route']['actual_policy'],'astra')
        self.assertEqual(web['route']['execution_environment'],'web')
        self.assertEqual(local['route']['actual_policy'],'aspire')
        for result in (web,local):self.assertEqual(result['route']['prompt'],prompt)
        with self.assertRaises(ValueError):select_aspire_launch(config,prompt,execution_environment='typo')

    def test_local_missing_program_generates_and_plans_before_queue_then_fresh_live_pass(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);order=[]
            library=saved_library(root)
            prompt='Move the block with the right arm while preserving the explicit target condition.'
            response=dict(action='program',source=SOURCE,summary='Fixture full task',lesson='Fixture',
                queries=[dict(name='block',query='green block')])
            def generate(**request):
                self.assertIn(prompt,request['prompt']);order.append('generate');return response
            def harness(**request):
                order.append(request['mode'])
                if request['mode']=='observe':return dict(status='OBSERVED',context={},images={},snapshot='fixture')
                if request['mode']=='plan':
                    self.assertIsNone(request['bridge_socket'])
                    return dict(status='PLAN_ONLY',planning_success=True,physical_motion_calls=0)
                self.assertTrue(BridgeClient(request['bridge_socket']).call('observation')['homed'])
                return dict(status='UNVERIFIED',planning_success=True,physical_motion_calls=0)
            policy=AspireCodexPolicy(harness=harness,calibration={},robot_id='fixture',task=prompt,
                directory=root/'run',instructions='',skill_directory=root/'skills',generator=generate,
                execution_environment='local',allow_hardware=False,executable_skills=library)
            api=MockSessionAPI(auto_activate=True);create=api.create_session
            def queued(*args,**kwargs):order.append('queue');return create(*args,**kwargs)
            api.create_session=queued
            controller=RunnerController(api,robot_id='fixture',hardware_control_enabled=False,share_conversation=False)
            controller.join_and_run(policy,prompt);controller._worker.join(8)
            self.assertFalse(controller._worker.is_alive());self.assertIsNone(controller.status()['error'])
            self.assertEqual(order,['observe','generate','plan','queue','execute'])
            self.assertEqual(policy._coding_requests,1)
            self.assertEqual(len(api.create_requests),1)
            saved=ExecutableSkillLibrary(library.path,repairs=root/'skills/.repaired-programs').select(prompt)
            self.assertEqual(saved['response']['source'],SOURCE)
            self.assertEqual(saved['provenance']['selection'],'reused_exact_task_program')
            fast=AspireCodexPolicy(harness=Mock(side_effect=AssertionError('No initial offline pass')),
                calibration={},robot_id='fixture',task=prompt,directory=root/'next',instructions='',
                skill_directory=root/'skills',selected_executable=saved,execution_environment='local',
                generator=Mock(side_effect=AssertionError('No coding request')))
            fast.prepare_before_session(prompt)
            self.assertEqual(fast._coding_requests,0)
            controller.stop()

    def test_full_conditions_route_before_any_provider_or_scene_work(self):
        from local_playground import LocalPlayground
        with tempfile.TemporaryDirectory() as temporary:
            library=saved_library(Path(temporary))
            app=LocalPlayground.__new__(LocalPlayground)
            app.aspire_station_config={'executable_skills':{'enabled':True,'manifest':str(library.path)}}
            with patch('remote_yam.aspire_codex_policy.configured_aspire_policy') as create, \
                    patch('remote_yam.aspire_codex_policy.AspireCodexPolicy._generate') as generate, \
                    patch('remote_yam.api_depth.ApiDepth.read') as read:
                matched=app.resolve_api_depth_launch(TASK)
                self.assertEqual(matched['route']['actual_policy'],'aspire')
                self.assertEqual(matched['program'],library.select(TASK))
                for prompt in ('Do not '+TASK, TASK+' Use the right arm.',
                        'Move the green block from the blue chip onto the green towel.',
                        'Move the green block onto the red towel.',
                        'Stack three available blocks; checkpoint each grasp and placement.'):
                    with self.subTest(prompt=prompt):
                        route=app.resolve_api_depth_launch(prompt)
                        self.assertEqual(route['route']['actual_policy'],'aspire')
                        self.assertTrue(route['route']['code_generation_requested'])
                        self.assertEqual(route['route']['prompt'],prompt)
                        self.assertIsNone(route['program'])
                create.assert_not_called();generate.assert_not_called();read.assert_not_called()

    def test_corrupt_library_is_a_concrete_failure_not_a_silent_fallback(self):
        from local_playground import LocalPlayground
        with tempfile.TemporaryDirectory() as temporary:
            library=saved_library(Path(temporary))
            app=LocalPlayground.__new__(LocalPlayground)
            app.aspire_station_config={'executable_skills':{'enabled':True,'manifest':str(library.path)}}
            library.source_path.write_text(SOURCE+'# tampered\n')
            with self.assertRaisesRegex(ValueError,'recorded hash'):
                app.resolve_api_depth_launch(TASK)
