from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from remote_yam.aspire_code_runtime import run_generated, task_segments

SOURCE = '''
def build_task(tools):
    return {'steps': [
        {'stage':'open', 'kind':'open', 'side':'left'},
        {'stage':'approach', 'kind':'move', 'arguments':{'left_target_pos':[.2,.1,.2]}},
        {'stage':'close', 'kind':'close', 'side':'left'},
        {'stage':'carry', 'kind':'move', 'arguments':{'left_target_pos':[.3,.1,.3]}},
        {'stage':'release', 'kind':'open', 'side':'left'}]}
def evaluate(tools, task):
    return {'success':True, 'evidence':tools['image_evidence']}
'''


class GeneratedRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name)
        self.tools=dict(read_only={},fresh_read_only=lambda:dict(image_evidence='test fixture only'),
            plan_freespace_sequence=Mock(return_value=dict(success=True,status='PASS',stages=[
                dict(stage='approach',trajectory_cache_key='a'),dict(stage='carry',trajectory_cache_key='b')])),
            freespace_move=Mock(return_value=SimpleNamespace(status='Success',executed=True,reason='',trajectory_cache_key='key')),
            open_gripper=Mock(return_value={'success':True}),close_gripper=Mock(return_value={'success':True}))

    def test_late_plan_failure_sends_no_earlier_jaw_or_motion(self):
        self.tools['plan_freespace_sequence'].return_value=dict(success=False,status='FAIL',failing_stage='carry',reason='IK')
        result=run_generated(SOURCE,self.tools,self.root,execute=True)
        self.assertEqual(result['physical_motion_calls'],0)
        for name in ('freespace_move','open_gripper','close_gripper'):self.tools[name].assert_not_called()

    def test_jaw_states_are_chained_and_cache_keys_used(self):
        result=run_generated(SOURCE,self.tools,self.root,execute=True)
        self.assertTrue(result['success'])
        segments=self.tools['plan_freespace_sequence'].call_args.args[0]
        self.assertEqual(segments[0]['gripper_state'],{'left':1.})
        self.assertEqual(segments[1]['gripper_state'],{'left':0.})
        self.assertEqual(self.tools['freespace_move'].call_args_list[0].kwargs,{'trajectory_cache_key':'a'})
        self.assertEqual(result['physical_motion_calls'],5)

    def test_plan_only_never_reports_physical_success(self):
        result=run_generated(SOURCE,self.tools,self.root)
        self.assertEqual(result['status'],'PLAN_ONLY')
        self.assertFalse(result['success'])
        self.tools['open_gripper'].assert_not_called()

    def test_observation_only_repair_is_fenced_before_any_live_command(self):
        source=SOURCE.replace("return {'steps': [","return {'observation_only_repair': True, 'decision':'Chip geometry unknown', 'steps': [")
        result=run_generated(source,self.tools,self.root,execute=True)
        self.assertEqual(result['status'],'RECOVERY_BLOCKED')
        self.assertTrue(result['planning_success']);self.assertFalse(result['success'])
        self.assertEqual(result['physical_motion_calls'],0)
        for name in ('freespace_move','open_gripper','close_gripper'):self.tools[name].assert_not_called()

    def test_native_noop_is_recorded_without_requesting_a_nonexistent_path(self):
        self.tools['plan_freespace_sequence'].return_value['stages'][0].update(
            native_noop=True,trajectory_cache_key=None,
            reason='Target pose already matches the current robot pose; no move executed.')
        result=run_generated(SOURCE,self.tools,self.root,execute=True)
        self.assertTrue(result['success'])
        self.assertEqual(result['physical_motion_calls'],4)
        self.tools['freespace_move'].assert_called_once_with(trajectory_cache_key='b')
        event=next(e for e in result['events'] if e['stage']=='approach')
        self.assertTrue(event['native_noop'])
        self.assertFalse(event['executed'])

    def test_final_capture_failure_keeps_completed_execution_unverified(self):
        self.tools['fresh_read_only']=Mock(side_effect=RuntimeError('camera unavailable'))
        result=run_generated(SOURCE,self.tools,self.root,execute=True)
        self.assertEqual(result['status'],'UNVERIFIED')
        self.assertFalse(result['success'])
        self.assertEqual(result['physical_motion_calls'],5)

    def test_negative_jaw_result_stops_the_sequence(self):
        self.tools['open_gripper'].return_value={'success':False}
        result=run_generated(SOURCE,self.tools,self.root,execute=True)
        self.assertEqual(result['status'],'FAILED')
        self.assertEqual(result['failing_stage'],'open')
        self.tools['freespace_move'].assert_not_called()

    def test_controller_timeout_saves_failed_stage_without_release_or_replay(self):
        self.tools['freespace_move'].side_effect=RuntimeError('joint_settle_timeout: fixture controller')
        result=run_generated(SOURCE,self.tools,self.root,execute=True)
        self.assertEqual(result['failing_stage'],'approach')
        self.assertIn('joint_settle_timeout',result['reason'])
        self.assertEqual(result['physical_motion_calls'],2)
        self.tools['freespace_move'].assert_called_once()
        self.tools['open_gripper'].assert_called_once()
        self.tools['close_gripper'].assert_not_called()
        self.assertTrue((self.root/'task_result.json').is_file())
