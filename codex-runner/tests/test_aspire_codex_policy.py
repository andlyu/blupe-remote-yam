import json
from pathlib import Path
import tempfile
import unittest
import base64
import hashlib
import os
from types import SimpleNamespace
import threading
import numpy as np

from remote_yam.aspire_codex_policy import AspireCodexPolicy, coding_feedback, model_retrieval_catalog
from remote_yam.aspire_api_policy import BridgeClient
from remote_yam.controller import RunnerController
from remote_yam.session import MockSessionAPI

SOURCE = "def build_task(tools):\n return {'steps': []}\ndef evaluate(tools, task):\n return {'success': False}\n"


class CodingLoopTests(unittest.TestCase):
    def test_inference_defaults_and_recorded_configuration_use_high_standard(self):
        policy=self.policy([])
        self.assertEqual(policy.reasoning_effort,'high')
        self.assertEqual(policy.response_speed,'standard')
        configuration=policy.public_config()
        self.assertEqual(configuration['reasoning_effort'],'high')
        self.assertEqual(configuration['response_speed'],'standard')
        self.assertEqual(self.calls,[])
        self.assertEqual(self.requests,[])

    def test_task_updates_preserve_failure_revision_and_plan_without_claiming_fixture_authorship(self):
        policy=self.policy([dict(status='PLAN_FAILED',planning_success=False,reason='Measured rod overlap'),
            dict(status='PLAN_ONLY',planning_success=True)])
        result=policy._coding_loop()
        progress=policy.public_config()['task_progress']
        self.assertEqual(result['status'],'PLAN_ONLY')
        self.assertEqual(progress['task'],policy.task)
        updates=progress['updates']
        for update in updates:
            self.assertEqual(set(update),{'timestamp','happened','changed','next_action'})
        failed=next(i for i,row in enumerate(updates) if row['happened']=='Complete plan failed.')
        revised=next(i for i,row in enumerate(updates) if row['happened']=='Revising code from harness feedback.')
        passed=next(i for i,row in enumerate(updates) if row['happened']=='Complete plan passed.')
        self.assertLess(failed,revised);self.assertLess(revised,passed)
        self.assertIn('Measured rod overlap',updates[failed]['changed'])
        self.assertFalse(progress['lineage']['authorship']['generated_by_codex'])
        saved=json.loads((policy.directory/'coding-loop.json').read_text())
        self.assertEqual(saved['task_updates'],updates)
        self.assertEqual([call['mode'] for call in self.calls],['observe','plan','plan'])

    def test_new_skill_creation_persists_planning_evidence_without_physical_success(self):
        reply=dict(action='program',source=SOURCE,summary='New fixture behavior',lesson='Plan only',
            queries=[dict(name='block',query='green block')],lineage=dict(used=[],new_skills=[
                dict(id='fixture',title='Fixture behavior',behavior='Build a new fixture task',
                    reason='No compatible executable',start_line=1,end_line=2)]))
        policy=self.policy([dict(status='PLAN_ONLY',planning_success=True,success=False)],replies=[reply])
        result=policy._coding_loop()
        trace=json.loads((policy.directory/'attempt-01/lineage.json').read_text())
        self.assertTrue(trace['new_skills'][0]['planning_success'])
        self.assertIsNone(trace['new_skills'][0]['physical_success'])
        self.assertEqual(trace['new_skills'][0]['creation'],'recorded_code')
        manifest=json.loads(next(policy.skill_directory.glob('*.json')).read_text())
        self.assertEqual(manifest['lineage'],trace)
        self.assertEqual(result['physical_motion_calls'],0)
        self.assertEqual([c['mode'] for c in self.calls],['observe','plan'])

    def test_invalid_ancestry_is_repaired_before_any_harness_plan(self):
        invalid=dict(action='program',source=SOURCE,summary='Invented source',lesson='',
            queries=[dict(name='block',query='green block')],lineage=dict(used=[dict(
                id='invented',version='invented',usage='unchanged',changed='',source_refs=[])],new_skills=[]))
        legacy=dict(action='program',source=SOURCE,summary='Legacy fixture',lesson='',
            queries=[dict(name='block',query='green block')])
        policy=self.policy([dict(planning_success=True,status='PLAN_ONLY')],replies=[invalid,legacy])
        policy._coding_loop()
        self.assertEqual(self.requests[1]['feedback']['status'],'CODE_ERROR')
        self.assertEqual([c['mode'] for c in self.calls],['observe','plan'])
        self.assertFalse(json.loads((policy.directory/'attempt-02/lineage.json').read_text())['usage_recorded'])

    def test_large_native_trace_is_bounded_without_losing_code_or_mutating_evidence(self):
        trace='native failure '+('joint state '*30000)+'final failing stage'
        original=dict(reason=trace,previous_source=trace,
            native_sequence_diagnostics={'native_failure_counts':{'approach':32}})
        feedback=coding_feedback(original)
        self.assertLess(len(feedback['reason']),4300)
        self.assertIn('final failing stage',feedback['reason'])
        self.assertEqual(feedback['previous_source'],trace)
        self.assertEqual(original['reason'],trace)
        self.assertEqual(feedback['native_sequence_diagnostics'],original['native_sequence_diagnostics'])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.calls, self.requests = [], []

    def test_retrieval_model_view_preserves_code_citations_and_validation_without_recursive_history(self):
        entry=dict(id='saved',version='hash',kind='saved_program',validation='FULL_PLAN_ONLY',
            source=SOURCE,lineage={'old_recursive_code':SOURCE*100000},
            executable_reuse={'prior_retrieval':SOURCE*100000},
            snippets=[dict(source='/recorded.py',source_sha256='source-hash',start_line=1,end_line=4,
                code=SOURCE,original_code=SOURCE)],
            validation_history=[dict(validation='PHYSICAL_SUCCESS',evidence='/prior-run',lineage={'code':SOURCE*100000})])
        compact=model_retrieval_catalog([entry])[0]
        self.assertEqual(compact['snippets'][0]['code'],SOURCE)
        self.assertEqual(compact['snippets'][0]['source_sha256'],'source-hash')
        self.assertEqual(compact['version'],'hash')
        self.assertEqual(compact['validation_history'],[dict(validation='PHYSICAL_SUCCESS',evidence='/prior-run')])
        self.assertLess(len(json.dumps(compact)),1000)
        self.assertEqual(entry['snippets'][0]['original_code'],SOURCE)

    def test_revision_transport_failure_preserves_last_native_failure_for_next_human_run(self):
        first=self.policy([dict(status='PLAN_FAILED',planning_success=False,reason='Native withdrawal collision')])
        original=first.generator
        def generator(**request):
            if first._attempts:raise RuntimeError('Codex input too large')
            return original(**request)
        first.generator=generator
        with self.assertRaisesRegex(RuntimeError,'input too large'):first._coding_loop()
        feedback=json.loads(first._coding_feedback_path().read_text())
        self.assertEqual(feedback['previous_source'],SOURCE)
        self.assertEqual(feedback['reason'],'Native withdrawal collision')
        self.assertEqual(feedback['physical_commands_sent'],0)
        error=json.loads((first.directory/'coding-request-error.json').read_text())
        self.assertFalse(error['queue_session_created'])
        self.assertEqual([call['mode'] for call in self.calls],['observe','plan'])

    def policy(self, outcomes, *, plan_only=True, replies=None):
        replies = list(replies or [dict(action='program', source=SOURCE, summary='A generated fixture',
            lesson='Native planning evidence', queries=[dict(name='block',query='green cuboid')])]*5)
        def generate(**request):
            self.requests.append(request)
            return replies.pop(0)
        def harness(**request):
            self.calls.append(request)
            if request['mode']=='observe':
                return dict(status='OBSERVED',context={'fixture':True},images={},snapshot='scene-fixture')
            return outcomes.pop(0)
        return AspireCodexPolicy(harness=harness, calibration={},robot_id='fixture',task='Stack green on red',
            directory=self.root/'run',instructions='Station instructions',skill_directory=self.root/'skills',
            generator=generate, plan_only=plan_only)

    def test_failed_native_plan_is_sent_to_generator_then_replanned_without_motion(self):
        policy=self.policy([dict(status='PLAN_FAILED',planning_success=False,failing_stage='carry',reason='IK failed'),
            dict(status='PLAN_ONLY',planning_success=True,success=False)])
        result=policy._coding_loop()
        self.assertEqual(result['status'],'PLAN_ONLY')
        self.assertFalse(result['success'])
        self.assertEqual([c['mode'] for c in self.calls],['observe','plan','plan'])
        self.assertEqual(self.requests[1]['feedback']['failing_stage'],'carry')
        self.assertIn('IK failed',self.requests[1]['prompt'])
        self.assertEqual(Path(self.calls[1]['program']).read_text(),SOURCE)
        manifests=list((self.root/'skills').glob('*.json'))
        self.assertEqual(json.loads(manifests[0].read_text())['validation'],'FULL_PLAN_ONLY')
        self.assertIn('FULL_PLAN_ONLY',self.requests[1]['prompt'] if len(manifests)>1 else json.dumps(policy._skills()))

    def test_exhausted_offline_revision_survives_next_ui_preparation(self):
        first=self.policy([dict(status='PLAN_FAILED',planning_success=False,
            reason='Native terminal pose failed')]*5)
        result=first._coding_loop()
        self.assertEqual(result['status'],'BLOCKED')
        saved=json.loads(first._coding_feedback_path().read_text())
        self.assertEqual(saved['previous_source'],SOURCE)
        self.assertEqual(saved['physical_commands_sent'],0)
        second=self.policy([dict(status='PLAN_ONLY',planning_success=True)])
        second.directory=self.root/'retry';second.directory.mkdir()
        second.prepare_before_session(second.task)
        self.assertEqual(self.requests[-1]['feedback']['reason'],'Native terminal pose failed')
        self.assertEqual(self.requests[-1]['feedback']['previous_source'],SOURCE)
        self.assertTrue(second._preparation_result['planning_success'])

    def test_recovered_coding_feedback_resumes_exact_conversation(self):
        policy=self.policy([dict(status='PLAN_ONLY',planning_success=True)])
        path=policy._coding_feedback_path();path.parent.mkdir(parents=True)
        thread='12345678-1234-1234-1234-123456789abc'
        path.write_text(json.dumps(dict(status='CODE_ERROR',reason='SyntaxError at line 216',
            previous_source=SOURCE,model_conversation_id=thread)))
        policy.prepare_before_session(policy.task)
        self.assertEqual(policy.resume_conversation,thread)
        self.assertEqual(policy._preparation.resume_conversation,thread)
        self.assertEqual(self.requests[0]['feedback']['reason'],'SyntaxError at line 216')
        self.assertEqual([c['mode'] for c in self.calls],['observe','plan'])

    def test_execution_uses_fresh_harness_and_never_recorded_snapshot(self):
        policy=self.policy([dict(planning_success=True,status='PLAN_ONLY'),
            dict(planning_success=True,status='SUCCESS',success=True,physical_motion_calls=9)],plan_only=False)
        result=policy._coding_loop('private-fixture-socket')
        self.assertTrue(result['success'])
        execute=self.calls[-1]
        self.assertEqual(execute['mode'],'execute')
        self.assertNotIn('snapshot',execute)
        self.assertEqual(execute['bridge_socket'],'private-fixture-socket')
        self.assertEqual(policy._skills()[0]['validation'],'PHYSICAL_SUCCESS')

    def test_failure_after_physical_action_returns_without_replay(self):
        policy=self.policy([dict(planning_success=True),
            dict(planning_success=True,status='FAILED',success=False,physical_motion_calls=3)],plan_only=False)
        result=policy._coding_loop('socket')
        self.assertEqual(result['status'],'FAILED')
        self.assertEqual(len(self.requests),1)
        self.assertEqual([c['mode'] for c in self.calls],['observe','plan','execute'])
        saved=json.loads(policy._physical_feedback_path().read_text())
        self.assertEqual(saved['result']['status'],'FAILED')
        self.assertEqual(saved['previous_source'],SOURCE)

    def test_controller_reason_survives_in_persisted_abort_feedback(self):
        policy=self.policy([dict(planning_success=True)],plan_only=False)
        original=policy.harness
        def harness(**request):
            if request['mode']!='execute':return original(**request)
            policy.trajectory_failed('joint_settle_timeout','fixture controller',1)
            return dict(status='CANCELLED',reason='Missing task_result.json',
                planning_success=True,physical_motion_calls=1)
        policy.harness=harness
        result=policy._coding_loop('socket')
        self.assertEqual(result['reason'],'joint_settle_timeout: fixture controller')
        self.assertEqual(result['harness_reason'],'Missing task_result.json')
        self.assertFalse(result['success'])
        saved=json.loads(policy._physical_feedback_path().read_text())
        self.assertEqual(saved['result']['reason'],result['reason'])
        self.assertEqual([c['mode'] for c in self.calls],['observe','plan'])

    def test_exhausted_scene_capture_does_not_rewrite_robot_program_or_relaunch_harness(self):
        policy=self.policy([dict(planning_success=True),dict(status='SCENE_CAPTURE_FAILED',
            planning_success=False,physical_motion_calls=0,reason='NoDepthImage: expired pair')],plan_only=False)
        result=policy._coding_loop('socket')
        self.assertEqual(result['status'],'SCENE_CAPTURE_FAILED')
        self.assertEqual(len(self.requests),1)
        self.assertEqual([c['mode'] for c in self.calls],['observe','plan','execute'])

    def test_previously_planned_agent_program_uses_one_fresh_live_harness(self):
        policy=self.policy([dict(planning_success=True,status='SUCCESS',success=True,physical_motion_calls=9)],plan_only=False)
        policy.validated_program=dict(action='program',source=SOURCE,summary='Previously generated',lesson='Actual prior full-plan pass',
            queries=[dict(name='block',query='green cuboid')])
        result=policy._coding_loop('socket')
        self.assertTrue(result['success'])
        self.assertEqual(self.requests,[])
        self.assertEqual([c['mode'] for c in self.calls],['execute'])
        self.assertNotIn('snapshot',self.calls[0])

    def test_saved_revision_is_freshly_planned_before_queue_without_rewriting_its_source(self):
        policy=self.policy([dict(planning_success=True,status='PLAN_ONLY')],plan_only=False)
        reply=dict(action='program',source=SOURCE,summary='Prior model revision',lesson='Recorded-scene plan only',
            queries=[dict(name='block',query='green cuboid')])
        policy.validated_program=reply
        policy.prepare_before_session(policy.task)
        self.assertEqual(self.requests,[])
        self.assertEqual([c['mode'] for c in self.calls],['observe','plan'])
        self.assertEqual(Path(self.calls[-1]['program']).read_text(),SOURCE)
        handoff=json.loads((policy.directory/'handoff.json').read_text())
        self.assertEqual(handoff['source_sha256'],hashlib.sha256(SOURCE.encode()).hexdigest())
        self.assertFalse(handoff['queue_session_created'])
        self.assertTrue(handoff['live_replanning_required'])

    def test_initialized_scene_failure_queues_once_without_task_packets_or_source_rewrite(self):
        policy=self.policy([],plan_only=False)
        policy.validated_program=dict(action='program',source=SOURCE,summary='Prior model program',
            lesson='Recorded full plan only',queries=[dict(name='block',query='green cuboid')])
        policy.initialize_before_observation=True
        policy.max_revisions=0
        api=MockSessionAPI()
        order=[]
        create=api.create_session
        def create_session(*args,**kwargs):
            order.append('queue')
            self.assertEqual(policy._preparation_result['status'],'AWAITING_INITIALIZED_SCENE')
            self.assertIsNone(policy._preparation_result['planning_success'])
            return create(*args,**kwargs)
        api.create_session=create_session
        def harness(**request):
            order.append(request['mode'])
            self.assertEqual(request['mode'],'execute')
            self.assertNotIn('snapshot',request)
            self.assertEqual(Path(request['program']).read_text(),SOURCE)
            observation=BridgeClient(request['bridge_socket']).call('observation')
            self.assertTrue(observation['homed'])
            self.assertTrue(observation['settled'])
            return dict(status='PLAN_FAILED',planning_success=False,physical_motion_calls=0,
                reason='Fresh initialized target pose has no complete native path',
                scene=dict(status='OBSERVED',context={},images={},snapshot='fresh-initialized-scene'))
        policy.harness=harness
        controller=RunnerController(api,robot_id='fixture',hardware_control_enabled=False,
            share_conversation=False,submit_attempts=1)
        controller.join_and_run(policy,policy.task,run_duration_s=300)
        controller._worker.join(10)
        if controller._worker.is_alive():controller.stop()
        self.assertFalse(controller._worker.is_alive())
        self.assertEqual(order,['queue','execute'])
        self.assertEqual(len(api.create_requests),1)
        self.assertEqual(api.trajectory_log,[])
        self.assertEqual(self.requests,[])
        handoff=json.loads((policy.directory/'handoff.json').read_text())
        self.assertEqual(handoff['scene_capture'],'after_normal_queue_initialization')
        self.assertTrue(handoff['live_replanning_required'])
        self.assertEqual(policy._outcome['physical_motion_calls'],0)

    def test_initialized_scene_option_rejects_unplanned_program_or_offline_mode(self):
        args=dict(harness=lambda **kwargs:None,calibration={},robot_id='fixture',
            directory=self.root/'invalid',instructions='',skill_directory=self.root/'skills',
            initialize_before_observation=True)
        with self.assertRaisesRegex(ValueError,'previously planned'):
            AspireCodexPolicy(**args)
        reply=dict(action='program',source=SOURCE,summary='fixture',lesson='',
            queries=[dict(name='block',query='green cuboid')])
        with self.assertRaisesRegex(ValueError,'execution mode'):
            AspireCodexPolicy(**args,validated_program=reply,plan_only=True)

    def test_changed_live_scene_can_return_to_coding_before_motion(self):
        scene=dict(status='OBSERVED',context={'changed':True},images={},snapshot='fresh-scene')
        policy=self.policy([dict(planning_success=False,status='PLAN_FAILED',physical_motion_calls=0,scene=scene),
            dict(planning_success=True),dict(planning_success=True,success=True)],plan_only=False)
        policy.validated_program=dict(action='program',source=SOURCE,summary='Previously generated',lesson='',
            queries=[dict(name='block',query='green cuboid')])
        self.assertTrue(policy._coding_loop('socket')['success'])
        self.assertEqual([c['mode'] for c in self.calls],['execute','plan','execute'])
        self.assertEqual(self.requests[0]['scene'],scene)

    def test_fresh_planning_failure_before_motion_can_revise(self):
        policy=self.policy([dict(planning_success=True),
            dict(planning_success=False,status='PLAN_FAILED',physical_motion_calls=0),
            dict(planning_success=True),dict(planning_success=True,success=True,status='SUCCESS')],plan_only=False)
        self.assertTrue(policy._coding_loop('socket')['success'])
        self.assertEqual(len(self.requests),2)

    def test_program_error_is_revised_before_harness_execution(self):
        bad=dict(action='program',source='invalid syntax !!!',summary='fixture',lesson='',queries=[])
        good=dict(action='program',source=SOURCE,summary='fixture',lesson='',queries=[dict(name='x',query='green')])
        policy=self.policy([dict(planning_success=True)],replies=[bad,good])
        policy._coding_loop()
        self.assertEqual(self.requests[1]['feedback']['status'],'CODE_ERROR')
        self.assertEqual([c['mode'] for c in self.calls],['observe','plan'])

    def test_observation_error_does_not_call_generator(self):
        policy=self.policy([])
        policy.harness=lambda **request:dict(status='HARNESS_ERROR',reason='depth unavailable')
        self.assertEqual(policy._coding_loop()['status'],'HARNESS_ERROR')
        self.assertEqual(self.requests,[])

    def test_recorded_failure_images_are_sent_to_the_coding_model(self):
        policy=self.policy([])
        policy.generator=None
        image=self.root/'review.png'
        image.write_bytes(b'fixture-recorded-image')
        bodies=[]
        reply=dict(action='give_up',source='',summary='fixture',lesson='',queries=[])
        policy._coding_provider=SimpleNamespace(_calls=0,
            _post_json=lambda body:(bodies.append(body),reply)[1])
        scene=dict(context={},images={role:str(image) for role in ('top','left','right')})
        policy._generate(scene,dict(review_images=[str(image)],recorded_review={'outcome':'FAILED'}))
        parts=bodies[0]['input'][0]['content']
        images=[part for part in parts if part['type']=='input_image']
        self.assertEqual(len(images),4)
        self.assertEqual(policy._coding_provider._expected_camera_count,4)
        self.assertEqual(base64.b64decode(images[-1]['image_url'].split(',')[1]),image.read_bytes())
        self.assertIn('FAILED',parts[0]['text'])

    def test_fresh_prompt_repairs_before_queue_then_hands_program_to_simulated_api(self):
        order=[]
        api=MockSessionAPI()
        create=api.create_session
        def create_session(*args,**kwargs):
            order.append('queue')
            self.assertIsNotNone(policy.validated_program)
            self.assertEqual(policy._preparation.coding_timeout_s,360.)
            self.assertEqual(policy._preparation.public_config()['display_name'],'Aspire Code integration Map')
            self.assertEqual(len(self.requests),2)
            return create(*args,**kwargs)
        api.create_session=create_session
        revised_source=SOURCE+'\nREVISION = 2\n'
        replies=[dict(action='program',source=source,summary='fixture',lesson='fixture',
            queries=[dict(name='block',query='green cuboid')]) for source in (SOURCE,revised_source)]
        policy=self.policy([],plan_only=False,replies=replies)
        policy.coding_timeout_s=360.
        policy.display_name='Aspire Code integration Map'
        def harness(**request):
            order.append(request['mode'])
            if request['mode']=='observe':
                return dict(status='OBSERVED',context={},images={},snapshot='recorded-fixture')
            if request['mode']=='plan':
                if order.count('plan')==1:
                    return dict(planning_success=False,status='PLAN_FAILED',reason='native fixture IK failure')
                return dict(planning_success=True,status='PLAN_ONLY')
            self.assertNotIn('snapshot',request)
            self.assertEqual(Path(request['program']).read_text(),revised_source)
            client=BridgeClient(request['bridge_socket'])
            for delta in (.01,-.01):
                obs=client.call('observation');paths=[]
                for side in ('left','right'):
                    start=np.r_[np.deg2rad(obs[side+'_joints_deg']),obs[side+'_gripper']]
                    end=start.copy();end[0]+=delta
                    paths.append([start.tolist(),end.tolist()])
                completion=client.call('trajectory',timestamps=[0.,1.],left=paths[0],right=paths[1],start_interp_s=0.)
                self.assertTrue(completion['success'])
            return dict(planning_success=True,status='SIMULATION_COMPLETE',success=False,physical_motion_calls=0)
        policy.harness=harness
        controller=RunnerController(api,robot_id='fixture',hardware_control_enabled=False,
            submit_attempts=1,share_conversation=False)
        controller.join_and_run(policy,policy.task,run_duration_s=300)
        controller._worker.join(10)
        if controller._worker.is_alive():controller.stop()
        self.assertFalse(controller._worker.is_alive())
        self.assertIsNone(controller.status()['error'])
        self.assertEqual(order,['observe','plan','plan','queue','execute'])
        self.assertEqual(len(api.create_requests),1)
        self.assertEqual(api.create_requests[0]['run_duration_s'],300)
        self.assertEqual(len(api.trajectory_log),2)
        self.assertIn('native fixture IK failure',self.requests[1]['prompt'])
        handoff=json.loads((policy.directory/'handoff.json').read_text())
        self.assertEqual(handoff['source_sha256'],hashlib.sha256(revised_source.encode()).hexdigest())
        self.assertTrue(handoff['live_replanning_required'])
        self.assertFalse(handoff['queue_session_created'])
        (self.root/'single-prompt-receipt.json').write_text(json.dumps(dict(
            scope='orchestration_and_simulated_api_fixture',prior_task_receipt=None,
            order=order,generated_revisions=len(self.requests),
            queue_sessions=len(api.create_requests),run_duration_s=300,
            simulated_packets=len(api.trajectory_log),
            trajectory_results=api.trajectory_log,handoff=handoff,
            physical_packets=0,physical_success=False,
            limitations='Generator/native-plan/execution-harness fixtures; real RunnerController, packet bridge and MockSessionAPI. Native ASPIRE and actual Codex generation have separate recorded tests.'),indent=2)+'\n')

    def test_failed_preparation_never_creates_a_session(self):
        policy=self.policy([dict(planning_success=False,status='PLAN_FAILED',reason='no feasible sequence')])
        policy.plan_only=False;policy.max_revisions=0
        api=MockSessionAPI()
        controller=RunnerController(api,robot_id='fixture',hardware_control_enabled=False,share_conversation=False)
        controller.join_and_run(policy,policy.task)
        controller._worker.join(2)
        self.assertEqual(api.create_requests,[])
        self.assertEqual(controller.status()['status'],'failed')
        self.assertIn('no feasible sequence',controller.status()['error'])

    def test_stop_during_preparation_prevents_queue_entry(self):
        policy=self.policy([],plan_only=False)
        started=threading.Event()
        def harness(**request):
            started.set()
            self.assertTrue(controller._stop_event.wait(2))
            return dict(status='HARNESS_ERROR',reason='cancelled')
        policy.harness=harness
        api=MockSessionAPI()
        controller=RunnerController(api,robot_id='fixture',hardware_control_enabled=False,share_conversation=False)
        controller.join_and_run(policy,policy.task)
        self.assertTrue(started.wait(1))
        self.assertEqual(controller.stop()['status'],'stopped')
        controller._worker.join(2)
        self.assertEqual(api.create_requests,[])
        self.assertEqual(controller.status()['status'],'stopped')

    def test_saved_skill_review_publishes_current_task_and_phase_before_model_work(self):
        from types import SimpleNamespace
        policy=self.policy([]);policy.task=None
        def review(coordinator,*,current_task):
            progress=policy.public_config()['task_progress']
            self.assertEqual(progress['task'],'New fixture task')
            self.assertEqual(current_task,progress['task'])
            self.assertEqual(progress['updates'][-1]['happened'],'Reviewing your prompt…')
            self.assertEqual(progress['updates'][-1]['changed'],'Working through your prompt and preparing the next steps.')
            raise RuntimeError('Fixture stops before a model or robot request')
        policy.skill_learning=SimpleNamespace(review_pending=review)
        with self.assertRaisesRegex(RuntimeError,'Fixture stops'):
            policy.prepare_before_session('New fixture task')
        self.assertEqual(self.calls,[]);self.assertEqual(self.requests,[])

    def test_same_prompt_automatically_recovers_failed_physical_feedback(self):
        previous=self.policy([])
        previous._save_physical_failure(dict(source=SOURCE),
            dict(status='FAILED',reason='grasp shifted during carry'),self.root/'previous-attempt')
        policy=self.policy([dict(planning_success=True,status='PLAN_ONLY')],plan_only=False)
        policy.prepare_before_session(policy.task)
        self.assertIn('grasp shifted during carry',self.requests[0]['prompt'])
        self.assertEqual(policy._preparation.initial_feedback['previous_source'],SOURCE)
        self.assertIsNotNone(policy.validated_program)

    def test_abort_feedback_uses_last_saved_frames_instead_of_initial_scene(self):
        policy=self.policy([])
        attempt=self.root/'previous-attempt'
        frames=attempt/'execute/observations';frames.mkdir(parents=True)
        for role in ('top','left','right'):
            (frames/('00001_'+role+'_preview.jpg')).write_bytes(b'early fixture')
            (frames/('00009_'+role+'_preview.jpg')).write_bytes(b'last fixture')
        policy._save_physical_failure(dict(source=SOURCE),dict(status='FAILED',
            reason='joint_settle_timeout',scene={'images':{'top':'initial-scene.png'}}),attempt)
        saved=json.loads(policy._physical_feedback_path().read_text())
        self.assertEqual(saved['review_image_provenance'],'last_recorded_previews_before_shutdown')
        self.assertEqual(len(saved['review_images']),3)
        self.assertTrue(all(Path(p).name.startswith('00009_') for p in saved['review_images']))

    def test_latest_validated_revision_is_retrieved_before_equal_relevance_older_programs(self):
        policy=self.policy([])
        policy.skill_directory.mkdir()
        for i in range(4):
            path=policy.skill_directory/str(i)
            path.with_suffix('.py').write_text(SOURCE+'\n# revision '+str(i))
            manifest=path.with_suffix('.json')
            manifest.write_text(json.dumps(dict(task=policy.task,lesson='',validation='FULL_PLAN_ONLY')))
            os.utime(manifest,(100+i,100+i))
        skills=policy._skills()
        self.assertEqual(len(skills),3)
        self.assertTrue(skills[0]['source'].endswith('# revision 3'))
        self.assertTrue(skills[2]['source'].endswith('# revision 1'))
        self.assertTrue(all(s['validation']=='FULL_PLAN_ONLY' for s in skills))
