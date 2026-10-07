"""Verify frame adaptation independently of live hardware and credentials."""
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from remote_yam.aspire_adapter import AspireNotReady
from remote_yam.aspire_api_policy import ApiBridgeEnvironment
from remote_yam.aspire_planning import API_TO_ASPIRE_GRASP, NativePlanningEnvironment, orient_native_rrt_path, bind_native_planner, plan_native_sequence


class PlanningBoundaryTests(unittest.TestCase):
    def test_native_already_at_target_noop_preserves_predicted_pose_without_cache_or_dispatch(self):
        from cap.agent.tools.base import FreespaceResult
        env = Mock()
        env.get_planning_observations.return_value = dict(joint_pos=np.zeros(6),
            gripper_pos=np.array([1.]),ee_pos=np.zeros(3),ee_quat=np.array([0.,0.,0.,1.]))
        planning = NativePlanningEnvironment(env)
        native = Mock()
        noop = FreespaceResult(status='Success',trajectory_steps=0,executed=False,
            reason='Target pose already matches the current robot pose; no move executed.')
        result = plan_native_sequence(planning,native,lambda **kwargs:noop,
            [dict(stage='already_there',arguments={})])
        self.assertTrue(result['success'])
        stage = result['stages'][0]
        self.assertTrue(stage['native_noop'])
        self.assertEqual(stage['predicted_endpoint'],stage['start_state'])
        self.assertIsNone(planning.predicted_state)
        native._get_cached_trajectory.assert_not_called()
        native._get_planner.assert_not_called()
        env.move_bimanual_joint_keypoints.assert_not_called()

    def test_missing_nonempty_native_cache_remains_an_error(self):
        env = Mock()
        env.get_planning_observations.return_value = dict(joint_pos=np.zeros(6),
            gripper_pos=np.array([1.]),ee_pos=np.zeros(3),ee_quat=np.array([0.,0.,0.,1.]))
        planning = NativePlanningEnvironment(env)
        native = Mock()
        native._get_cached_trajectory.return_value = None
        broken = SimpleNamespace(status='Success',reason='',final_pos_error_m=0.,
            final_rot_error_deg=0.,trajectory_steps=3,trajectory_cache_key='missing',executed=False)
        with self.assertRaises(AspireNotReady):
            plan_native_sequence(planning,native,lambda **kwargs:broken,
                [dict(stage='uncached',arguments={})])
        self.assertIsNone(planning.predicted_state)
        env.move_bimanual_joint_keypoints.assert_not_called()

    def test_sequence_seeds_native_paths_from_predictions_and_models_jaw_changes(self):
        base = dict(joint_pos=np.zeros(6), gripper_pos=np.array([.7]),
            ee_pos=np.zeros(3), ee_quat=np.array([0.,0.,0.,1.]))
        env = Mock()
        env.get_planning_observations.side_effect = lambda side: {k:v.copy() for k,v in base.items()}
        planning = NativePlanningEnvironment(env)
        native = Mock()
        entries, starts = {}, []
        native._get_cached_trajectory.side_effect = entries.get
        native._get_planner.return_value._kin.forward_kinematics.side_effect = lambda l,r: (
            np.r_[l[0],0.,0.], [0.,0.,0.,1.], np.r_[r[0],0.,0.], [0.,0.,0.,1.])
        def move(**arguments):
            self.assertTrue(arguments['preview_only'])
            start = planning.get_observations('left')
            starts.append((start['joint_pos'].copy(), float(start['gripper_pos'][0])))
            key = str(len(starts)); end = start['joint_pos']+.1
            entries[key] = dict(left_positions=[start['joint_pos'],end], right_positions=[np.zeros(6)],
                left_gripper_positions=[start['gripper_pos'][0]], right_gripper_positions=None)
            return SimpleNamespace(status='Success',reason='',final_pos_error_m=0.,final_rot_error_deg=0.,
                trajectory_steps=2,trajectory_cache_key=key,executed=False)
        segments = [dict(stage='approach',arguments={},gripper_state={'left':1.}),
            dict(stage='lift',arguments={},gripper_state={'left':0.})]
        result = plan_native_sequence(planning,native,move,segments)
        self.assertTrue(result['success'])
        np.testing.assert_allclose(starts[0][0],0.)
        np.testing.assert_allclose(starts[1][0],.1)
        self.assertEqual([s[1] for s in starts],[1.,0.])
        self.assertIsNone(planning.predicted_state)
        np.testing.assert_allclose(base['joint_pos'],0.)
        env.move_bimanual_joint_keypoints.assert_not_called()

    def test_failed_sequence_clears_predicted_state_and_never_dispatches(self):
        env = Mock()
        env.get_planning_observations.return_value = dict(joint_pos=np.zeros(6),
            gripper_pos=np.array([1.]),ee_pos=np.zeros(3),ee_quat=np.array([0.,0.,0.,1.]))
        planning=NativePlanningEnvironment(env)
        failure=SimpleNamespace(status='IK_Failed',reason='native failure',final_pos_error_m=0.,
            final_rot_error_deg=0.,trajectory_steps=0,trajectory_cache_key=None,executed=False,
            native_diagnostic={'goal':'Native collision detail'})
        native=Mock()
        result=plan_native_sequence(planning,native,lambda **kwargs:failure,
            [dict(stage='carry',arguments={})])
        self.assertEqual(result['failing_stage'],'carry')
        self.assertFalse(result['success'])
        self.assertEqual(result['stages'][0]['native_diagnostic'],failure.native_diagnostic)
        self.assertIsNone(planning.predicted_state)
        native._get_cached_trajectory.assert_not_called()
        env.move_bimanual_joint_keypoints.assert_not_called()

    def test_only_exact_repeated_ik_seed_and_prefix_are_suppressed_after_two_rejections(self):
        env=Mock()
        seed=dict(joint_pos=np.zeros(6),gripper_pos=np.array([1.]),
            ee_pos=np.zeros(3),ee_quat=np.array([0.,0.,0.,1.]))
        env.get_planning_observations.side_effect=lambda side:{k:v.copy() for k,v in seed.items()}
        planning=NativePlanningEnvironment(env)
        failure=SimpleNamespace(status='IK_Failed',reason='non-convergence',final_pos_error_m=0.,
            final_rot_error_deg=0.,trajectory_steps=0,trajectory_cache_key=None,executed=False)
        move=Mock(return_value=failure);native=Mock()
        segments=[dict(stage='approach',arguments={'right_target_pos':[.2,.1,.1]})]
        for _ in range(2):self.assertNotIn('repeated_rejection',plan_native_sequence(planning,native,move,segments))
        third=plan_native_sequence(planning,native,move,segments)
        self.assertEqual(move.call_count,2);self.assertFalse(third['success'])
        self.assertEqual(third['repeated_rejection']['native_calls'],2)
        seed['joint_pos'][0]=.01
        self.assertNotIn('repeated_rejection',plan_native_sequence(planning,native,move,segments))
        self.assertEqual(move.call_count,3)
        segments[0]['arguments']['right_target_pos'][0]=.3
        plan_native_sequence(planning,native,move,segments);self.assertEqual(move.call_count,4)
        self.assertIsNone(planning.predicted_state)
        env.move_bimanual_joint_keypoints.assert_not_called()

    def test_swapped_tree_path_is_rejoined_and_other_native_paths_are_preserved(self):
        start, goal, connection = np.array([0., 0.]), np.array([1., 1.]), np.array([.4, .6])
        raw = [connection, np.array([.2, .3]), start, goal, np.array([.7, .8]), connection]
        path, repaired = orient_native_rrt_path(raw, start, goal)
        self.assertTrue(repaired)
        np.testing.assert_allclose(path, [start, [.2, .3], connection, connection, [.7, .8], goal])
        unchanged, repaired = orient_native_rrt_path(path, start, goal)
        self.assertFalse(repaired)
        np.testing.assert_allclose(unchanged, path)
        for other in ([goal, start], [start, connection]):
            unchanged, repaired = orient_native_rrt_path(other, start, goal)
            self.assertFalse(repaired)
            np.testing.assert_allclose(unchanged, other)
        for invalid in ([], [start, [np.nan, 0.], goal]):
            with self.subTest(path=invalid), self.assertRaises(AspireNotReady):
                orient_native_rrt_path(invalid, start, goal)

    def test_native_direct_execution_and_cache_keep_residuals_as_diagnostics(self):
        from cap.agent.tools.base import FreespaceResult, ToolResult
        with tempfile.TemporaryDirectory() as output, patch.dict('os.environ'):
            env = Mock(output_dir=Path(output), physical_motion_enabled=True)
            (Path(output)/'planner').mkdir()
            native = Mock()
            native._trajectory_cache = {}
            data = FreespaceResult(status='Success', executed=True,
                final_pos_error_m=.040745, final_rot_error_deg=9.0758)
            native.execute.return_value = ToolResult(success=True, data=data)
            with patch('remote_yam.aspire_planning.prepare_native_model', return_value={'model_xml': output}), \
                    patch('cap.agent.tools.freespace_move.FreespaceMoveTool', return_value=native):
                move = bind_native_planner(env)
                self.assertIs(move(left_target_pos=[.2, 0., .1], planning_speed=1., preview_only=False), data)
                native.execute.assert_called_once_with(left_target_pos=[.2, 0., .1], planning_speed=1.,
                    preview_only=False, planner_backend='rrtconnect')
                native.execute.reset_mock()
                self.assertIs(move(trajectory_cache_key='native-cache'), data)
                native.execute.assert_called_once_with(trajectory_cache_key='native-cache', planner_backend='rrtconnect')
    def test_api_grasp_y_maps_to_native_opening_x_and_preserves_approach_z(self):
        np.testing.assert_allclose(API_TO_ASPIRE_GRASP[:, 0], [0., -1., 0.], atol=1e-12)
        np.testing.assert_allclose(API_TO_ASPIRE_GRASP[:, 2], [0., 0., 1.], atol=1e-12)
        np.testing.assert_allclose(API_TO_ASPIRE_GRASP.T@API_TO_ASPIRE_GRASP, np.eye(3), atol=1e-12)

    def test_preview_cannot_dispatch_and_execution_uses_strict_feedback(self):
        env = Mock()
        planning = NativePlanningEnvironment(env)
        planning.get_observations('left')
        env.get_planning_observations.assert_called_once_with('left')
        env.get_observations.assert_not_called()
        with self.assertRaises(AspireNotReady):
            planning.move_bimanual_joint_keypoints([], [], [])
        env.move_bimanual_joint_keypoints.assert_not_called()
        planning.preview_only = False
        planning.get_observations('left')
        env.get_observations.assert_called_once_with('left')

    def test_gripper_handoff_holds_both_measured_arms_in_one_bounded_path(self):
        env = ApiBridgeEnvironment.__new__(ApiBridgeEnvironment)
        env.physical_motion_enabled = True
        env.status = lambda: dict(left_joints_deg=[10.]*6, right_joints_deg=[-10.]*6,
            left_gripper=.3, right_gripper=.7)
        env._save_json = Mock()
        env._move_bimanual_joint_keypoints = Mock(return_value={'success': True})
        env.set_gripper('left', 1.)
        call = env._move_bimanual_joint_keypoints.call_args
        timestamps, left, right = call.args
        self.assertEqual(call.kwargs['start_interp_s'], 0.)
        np.testing.assert_allclose(left[:, :6], np.tile(np.deg2rad([10.]*6), (len(left), 1)))
        np.testing.assert_allclose(right[:, :6], np.tile(np.deg2rad([-10.]*6), (len(right), 1)))
        np.testing.assert_allclose(right[:, 6], .7)
        self.assertAlmostEqual(left[0, 6], .3)
        self.assertAlmostEqual(left[-1, 6], 1.)
        self.assertLessEqual(np.max(np.diff(left[:, 6])), .3)
        self.assertLess(timestamps[-1], .5)
        env.physical_motion_enabled = False
        env.deny_motion = Mock(side_effect=AspireNotReady('disabled'))
        with self.assertRaises(AspireNotReady):
            env.set_gripper('left', 1.)
        self.assertEqual(env._move_bimanual_joint_keypoints.call_count, 1)


if __name__ == '__main__':
    unittest.main()
