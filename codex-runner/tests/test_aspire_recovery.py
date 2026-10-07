"""Offline regressions: immutable failure, separate Astra recovery, no robot tests."""
import copy
import hashlib
import json
import shutil
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from remote_yam.aspire_code_runtime import run_generated
from remote_yam.aspire_codex_policy import AspireCodexPolicy
from remote_yam.aspire_executable_skills import ExecutableSkillLibrary
from remote_yam.aspire_progress import HarnessProgress
from remote_yam.aspire_lineage_catalog import LineageCatalog
from remote_yam.controller import RunnerController
from remote_yam.providers import PolicyComplete
from remote_yam.session import MockSessionAPI
from test_aspire_launch_routing import saved_library, TASK, SOURCE


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.library=saved_library(self.root)
        self.selected=self.library.select(TASK)
        self.failure=dict(status='PLAN_FAILED',planning_success=False,success=False,
            physical_motion_calls=0,reason='Native IK non-convergence at approach',failing_stage='approach')
        self.generator=Mock(side_effect=AssertionError('No same-attempt rewrite'))
        self.harness=Mock(return_value=self.failure)
        self.policy=AspireCodexPolicy(harness=self.harness,calibration={},robot_id='fixture',task=TASK,
            directory=self.root/'run',instructions='',skill_directory=self.root/'skills',
            selected_executable=self.selected,generator=self.generator,allow_hardware=False,execution_environment='web')

    def test_planning_failure_hands_off_once_without_rewriting_or_new_queue(self):
        recovery=Mock()
        recovery._outcome={'status':'done','summary':'Fixture model claims completion'}
        recovery.public_config.return_value={'model':'fixture','outcome':recovery._outcome}
        recovery.build_trajectory.side_effect=PolicyComplete('done')
        self.policy.recovery_factory=Mock(return_value=recovery)
        api=MockSessionAPI(auto_activate=True)
        controller=RunnerController(api,robot_id='fixture',hardware_control_enabled=False,share_conversation=False)
        self.addCleanup(controller.stop)
        controller.join_and_run(self.policy,TASK,run_duration_s=300)
        controller._worker.join(10)
        self.assertFalse(controller._worker.is_alive())
        self.assertIsNone(controller.status()['error'])
        self.assertEqual(len(api.create_requests),1)
        self.assertEqual(api.trajectory_log,[])
        self.generator.assert_not_called();self.harness.assert_called_once()
        original=Path(self.harness.call_args.kwargs['program']).read_text()
        self.assertEqual(original,self.selected['response']['source'])
        self.assertEqual(self.policy._attempts[0]['execution'],self.failure)
        saved=json.loads((self.policy.directory/'coding-loop.json').read_text())
        linked=saved['recovery_attempts'][0]
        self.assertEqual(linked['parent_attempt_id'],'aspire-1')
        self.assertEqual(linked['status'],'MODEL_REPORTED_DONE')
        self.assertIsNone(linked['physical_success'])
        self.assertIn('non-convergence',recovery.build_trajectory.call_args.args[0])

    def test_stop_and_controller_fault_never_start_recovery(self):
        self.policy.recovery_factory=Mock()
        self.policy.cancelled=lambda:True
        with self.assertRaises(RuntimeError):self.policy.build_trajectory(TASK,{},0)
        self.policy.recovery_factory.assert_not_called()
        self.policy.cancelled=lambda:False
        self.policy.trajectory_failed('joint_settle_timeout','measured hold',1)
        with self.assertRaises(RuntimeError):self.policy.build_trajectory(TASK,{},0)
        self.policy.recovery_factory.assert_not_called()

    def test_harness_crash_after_a_motion_request_never_starts_live_recovery(self):
        policy=self.policy;policy.recovery_factory=Mock()
        with patch('remote_yam.program_packet_bridge.ProgramPacketBridge._execute',return_value={}):
            policy._execute([])
        policy._outcome=dict(status='FAILED',success=False,reason='Harness exited without its final result')
        with patch('remote_yam.aspire_api_policy.AspireApiPolicy.build_trajectory',side_effect=PolicyComplete('failed')):
            with self.assertRaises(PolicyComplete):policy.build_trajectory(TASK,{},0)
        policy.recovery_factory.assert_not_called()

    def test_unverified_outcome_repairs_offline_and_next_run_selects_linked_version(self):
        policy=self.policy
        native=dict(status='UNVERIFIED',success=False,planning_success=True,physical_motion_calls=13)
        policy._outcome=native.copy()
        attempt=policy.directory/'attempt-01';attempt.mkdir()
        (attempt/'coding-response.json').write_text(json.dumps(self.selected['response']))
        (attempt/'generated_program.py').write_text(self.selected['response']['source'])
        policy._attempts=[dict(attempt=1,directory=str(attempt),execution=native)]
        revised=SOURCE+'\n# revised evaluator based on observed support evidence\n'
        reply=dict(action='program',source=revised,summary='Observer contract defect; repair finite support metric',
            lesson='No observed task-success claim',queries=[dict(name='block',query='green block')])
        requests=[]
        policy.generator=lambda **request:(requests.append(request) or reply)
        def offline(**request):
            self.assertIn(request['mode'],('observe','plan'))
            self.assertIsNone(request.get('bridge_socket'))
            if request['mode']=='observe':return dict(status='OBSERVED',context={},images={},snapshot='fresh-parked-scene')
            return dict(status='PLAN_ONLY',planning_success=True,success=False,physical_motion_calls=0)
        policy.harness=offline
        completion=dict(status='UNVERIFIED',success=False,reason='finite_towel_support, fresh_cloth_surrounds_block did not confirm',
            failed_checks=['finite_towel_support','fresh_cloth_surrounds_block'],images=[])
        original=copy.deepcopy(completion)
        policy._repair_outcome(completion,{'status':'EVALUATION_ONLY','success':False},native)
        self.assertEqual(completion,original);self.assertEqual(policy._attempts[0]['execution'],native)
        self.assertEqual(requests[0]['feedback']['previous_source'],self.selected['response']['source'])
        self.assertEqual(requests[0]['feedback']['failed_checks'],completion['failed_checks'])
        repair=json.loads((policy.directory/'offline-repair.json').read_text())
        self.assertEqual(repair['status'],'PLAN_VALIDATED');self.assertTrue(repair['source_changed'])
        self.assertEqual(repair['physical_motion_calls'],0)
        catalog=LineageCatalog({'skill_directory':str(policy.skill_directory),'robot_id':'fixture'})
        episode=catalog.episode(dict(task_id='fixture-run',task=TASK,policy_directory=str(policy.directory),
            coding_loop=str(policy.directory/'coding-loop.json')))
        self.assertEqual(episode['attempts'][0]['execution']['status'],'UNVERIFIED')
        revision=episode['attempts'][-1]
        self.assertEqual(revision['parent_attempt_id'],'astra-repair-1')
        self.assertEqual(revision['code'],revised)
        self.assertEqual(revision['plan']['planning_success'],True)
        # Manual local repairs retain truthful authorship and remain discoverable
        # without a success promotion into the recipe library.
        loop=json.loads((policy.directory/'coding-loop.json').read_text())
        loop['recovery_attempts'][0]['policy']='codex_local'
        (policy.directory/'coding-loop.json').write_text(json.dumps(loop))
        recorded=policy.skill_directory.parent/'runs/aspire-current'
        shutil.copytree(policy.directory,recorded)
        (recorded/'task-completion.json').write_text(json.dumps(dict(completion,task=TASK,native_status='UNVERIFIED')))
        discovered=next(e for e in catalog.build()['episodes'] if e['id']=='aspire-current')
        self.assertEqual(discovered['review_status'],'UNVERIFIED')
        self.assertFalse(discovered['after_parking_success'])
        self.assertEqual(discovered['attempts'][-1]['policy'],'codex_local')
        self.assertIsNotNone(discovered['attempts'][1]['diff'])
        selected=ExecutableSkillLibrary(self.library.path,repairs=policy.skill_directory/'.repaired-programs').select(TASK)
        self.assertEqual(selected['provenance']['selection'],'reused_explicit_linked_repair')
        self.assertEqual(selected['provenance']['recovery_parent']['outcome']['status'],'UNVERIFIED')
        self.assertEqual(selected['response']['source'],revised)
        self.assertEqual(selected['provenance']['bound_source_sha256'],repair['source_sha256'])
        # Exercise policy construction/preparation, beyond merely inspecting a
        # library entry: the Run gate must accept the exact repaired program.
        next_policy=AspireCodexPolicy(harness=Mock(side_effect=AssertionError('No preview harness')),
            calibration={},robot_id='fixture',task=TASK,directory=self.root/'next-run',
            instructions='',skill_directory=policy.skill_directory,selected_executable=selected,
            generator=Mock(side_effect=AssertionError('No launch generation')),allow_hardware=False)
        next_policy.prepare_before_session(TASK)
        self.assertEqual(next_policy._validated_lineage['used'][0]['verification'],'exact_source_match')
        self.assertEqual(next_policy.validated_program['source'],revised)
        handoff=json.loads((next_policy.directory/'handoff.json').read_text())
        self.assertEqual(handoff['source_sha256'],repair['source_sha256'])
        self.assertTrue(handoff['live_replanning_required']);self.assertFalse(handoff['queue_session_created'])
        manifest=json.loads(self.library.path.read_text())
        self.library.path.write_text(json.dumps(dict(manifest,additional_occupants={'rod':'green rod'})))
        incompatible=ExecutableSkillLibrary(self.library.path,repairs=policy.skill_directory/'.repaired-programs').select(TASK)
        self.assertNotEqual(incompatible['provenance']['selection'],'reused_explicit_linked_repair')
        self.library.path.write_text(json.dumps(manifest))
        self.assertEqual(self.library.source,SOURCE)
        # A different prompt and a changed source hash cannot inherit this repair.
        other=ExecutableSkillLibrary(self.library.path,repairs=policy.skill_directory/'.repaired-programs').select('place red block on towel')
        self.assertNotEqual(other['provenance']['selection'],'reused_explicit_linked_repair')
        Path(repair['source']).write_text(revised+'# tampered')
        selected=ExecutableSkillLibrary(self.library.path,repairs=policy.skill_directory/'.repaired-programs').select(TASK)
        self.assertNotEqual(selected['provenance']['selection'],'reused_explicit_linked_repair')

    def test_failed_offline_repair_does_not_install_a_candidate_or_loop_forever(self):
        policy=self.policy;policy.max_revisions=1
        attempt=policy.directory/'attempt-01';attempt.mkdir()
        (attempt/'coding-response.json').write_text(json.dumps(self.selected['response']))
        policy._attempts=[dict(attempt=1,directory=str(attempt),execution=self.failure)]
        generated=[]
        policy.generator=lambda **request:(generated.append(request) or self.selected['response'])
        calls=[]
        def harness(**request):
            calls.append(request['mode'])
            return dict(status='OBSERVED',context={},images={},snapshot='fresh') if request['mode']=='observe' else self.failure
        policy.harness=harness
        completion=dict(status='UNVERIFIED',reason='No fresh support confirmation',images=[])
        policy._repair_outcome(completion,None,self.failure)
        self.assertEqual(calls,['observe','plan','plan'])
        self.assertEqual(len(generated),2)
        self.assertFalse((policy.skill_directory/'.repaired-programs').exists())
        self.assertEqual(policy._recovery_attempts[-1]['status'],'FAILED')
        policy._repair_outcome(completion,None,self.failure)
        self.assertEqual(len(generated),2)

    def test_repair_setup_failure_is_recorded_and_bounded(self):
        policy=self.policy
        attempt=policy.directory/'attempt-01';attempt.mkdir()
        (attempt/'coding-response.json').write_text(json.dumps(self.selected['response']))
        policy._attempts=[dict(attempt=1,directory=str(attempt),execution=self.failure)]
        policy.repair_vision_factory=Mock(side_effect=RuntimeError('Vision setup failed'))
        completion=dict(status='UNVERIFIED',reason='No support confirmation',images=[])
        policy._repair_outcome(completion,None,self.failure)
        policy._repair_outcome(completion,None,self.failure)
        policy.repair_vision_factory.assert_called_once()
        record=json.loads((policy.directory/'offline-repair.json').read_text())
        self.assertEqual(record['status'],'FAILED');self.assertEqual(record['reason'],'Vision setup failed')
        self.assertEqual(policy._review_phase,'complete')
        self.assertFalse((policy.skill_directory/'.repaired-programs').exists())


class CandidateAndProgressTests(unittest.TestCase):
    def test_candidate_budget_is_a_failed_attempt_before_any_motion(self):
        source="def build_task(tools):\n for _ in range(100): tools['plan_freespace_sequence']([])\n return {'steps': []}\ndef evaluate(tools, task): return {'success':False}\n"
        native=Mock(return_value=dict(status='FAILED',success=False,reason='IK_Failed',failing_stage='approach'))
        tools=dict(read_only={'plan_freespace_sequence':native},freespace_move=Mock(),open_gripper=Mock(),close_gripper=Mock())
        with tempfile.TemporaryDirectory() as temporary:
            result=run_generated(source,tools,temporary,execute=True,max_candidate_plans=3)
            self.assertEqual(result['status'],'PLAN_FAILED');self.assertTrue(result['candidate_search_exhausted'])
            self.assertEqual(result['candidate_search']['rejected'],3)
            self.assertEqual(native.call_count,3);self.assertEqual(result['physical_motion_calls'],0)
            self.assertEqual(len((Path(temporary)/'candidate-plans.jsonl').read_text().splitlines()),3)
            tools['freespace_move'].assert_not_called();tools['open_gripper'].assert_not_called()

    def test_progress_tail_keeps_partial_lines_and_reports_stage_rejections_and_errors(self):
        with tempfile.TemporaryDirectory() as temporary:
            updates=[];monitor=HarnessProgress(temporary,updates.append)
            path=Path(temporary)/'debug_events.jsonl'
            event=dict(type='tool_start',name='get_camera_rgbd')
            path.write_text(json.dumps(event))
            monitor.read();self.assertEqual(updates,[])
            with path.open('a') as stream:stream.write('\n')
            monitor.read();self.assertEqual(updates[-1]['stage'],'capture')
            for event in [dict(type='tool_start',name='segment_camera_rgb'),
                dict(type='tool_start',name='plan_freespace_sequence'),
                dict(type='tool_end',name='plan_freespace_sequence',result={'success':False,'reason':'IK non-convergence'}),
                dict(type='tool_end',name='get_camera_rgbd',error='camera transfer failed')]:
                with path.open('a') as stream:stream.write(json.dumps(event)+'\n')
                monitor.read()
            self.assertEqual(updates[-2]['rejected_candidates'],1)
            self.assertEqual(updates[-2]['last_failure'],'IK non-convergence')
            self.assertEqual(updates[-1]['error'],'camera transfer failed')
            count=len(updates);monitor.read();self.assertEqual(len(updates),count)
