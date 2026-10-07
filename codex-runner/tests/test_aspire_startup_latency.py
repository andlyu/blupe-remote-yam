"""Offline regressions for repeated reads and the post-Home capture boundary."""
from copy import deepcopy
import time
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import numpy as np

from remote_yam.aspire_api_policy import AspireApiPolicy
from remote_yam.aspire_adapter import AspireNotReady, ObservationEnvironment, arm_pose
from remote_yam.api_depth_set import ApiDepthSet
from remote_yam.cameras import CameraUnavailable
from remote_yam.controller import RunnerController


def state(stamp, **changes):
    value=dict(episode_id='episode', lease_id='lease', step_id=1, jetson_id='test',
        source='hardware', mode='API_ACTIVE', homed=True, settled=True, observed_at=stamp,
        safety=dict(ok=True, estop_engaged=False), left_joints_deg=[0.]*6,
        right_joints_deg=[0.]*6, left_gripper=.99, right_gripper=.99)
    value.update(changes)
    return value


def policy():
    return AspireApiPolicy(run_harness=Mock(side_effect=AssertionError('No live harness')),
        calibration={}, robot_id='test', task='offline', allow_hardware=True)


class StartupLatencyTests(unittest.TestCase):
    def test_controller_normalized_public_telemetry_has_no_lease_or_cursor(self):
        now=time.time()
        completion=state(now-1.,step_id=0)
        # Real public telemetry supplies measured state, while the session
        # completion supplies the assigned identity and cursor. Normalization
        # adds None placeholders for the absent fields.
        fresh=state(now-.1)
        for field in ('episode_id','lease_id','step_id'):
            fresh.pop(field)
        provider=policy()
        api=Mock(spec=['supports_trajectories','get_robot_observation'])
        api.supports_trajectories=True
        controller=RunnerController(api,ik_solver=object(),robot_id='test',share_conversation=False)
        controller._status='running';controller._provider=provider
        controller._session_id='session';controller._episode_id='episode';controller._lease_id='lease'
        controller._command_step_id=0;controller._prompt='offline'
        controller.update_monitor_observation(fresh)
        self.assertIsNone(controller._monitor_observation['step_id'])
        controller._dispatch_trajectory=Mock()
        controller._observation({},completion)
        observed=controller._dispatch_trajectory.call_args.args[3]
        self.assertEqual(observed['step_id'],0)
        self.assertEqual(observed['settled_after'],completion['observed_at'])
        self.assertEqual(observed['observed_at'],fresh['observed_at'])
        self.assertEqual(observed['episode_id'],'episode')
        self.assertEqual(observed['lease_id'],'lease')
        api.get_robot_observation.assert_not_called()

    def test_supplied_station_lease_episode_and_cursor_must_still_match(self):
        now=time.time();provider=policy()
        for field,value in [('episode_id','other'),('lease_id','other'),('step_id',1)]:
            with self.subTest(field=field),self.assertRaisesRegex(AspireNotReady,'lease/cursor'):
                provider.reconciled_observation(state(now-1.,step_id=0),state(now-.1,**{field:value}))

    def test_controller_carries_fresh_station_pose_with_original_completion_epoch(self):
        now=time.time()
        completion=state(now-3.7)
        fresh=state(now-.6, right_joints_deg=[.1]*6)
        provider=policy()
        api=Mock(spec=['supports_trajectories', 'get_robot_observation'])
        api.supports_trajectories=True
        controller=RunnerController(api, ik_solver=object(), robot_id='test', share_conversation=False)
        controller._status='running';controller._provider=provider
        controller._session_id='session';controller._episode_id='episode';controller._lease_id='lease'
        controller._command_step_id=1;controller._prompt='offline'
        controller.update_monitor_observation(fresh)
        controller._dispatch_trajectory=Mock()
        controller._observation({}, completion)
        observed=controller._dispatch_trajectory.call_args.args[3]
        self.assertEqual(observed['observed_at'],fresh['observed_at'])
        self.assertEqual(observed['right_joints_deg'],[.1]*6)
        self.assertEqual(observed['settled_after'],completion['observed_at'])
        self.assertEqual((observed['episode_id'],observed['lease_id'],observed['step_id']),
                         ('episode','lease',1))
        api.get_robot_observation.assert_not_called()

    def test_matching_completion_resumes_with_fresh_state_without_duplicate_http(self):
        now=time.time();provider=policy()
        completion=state(now-3.7);fresh=state(now-.6)
        observed=provider.reconciled_observation(completion,fresh)
        provider._identity=('episode','lease')
        provider._pending=dict(last_step_id=0,waypoint_count=1)
        provider.trajectory_completed(completion,1)
        provider._thread=object();provider._packets.put_nowait([dict(step_id=1)])
        provider.refresh_observation=Mock(side_effect=AssertionError('Redundant HTTP refresh'))
        provider.build_trajectory('offline',observed,1)
        reply=provider._replies.get_nowait()
        self.assertEqual(reply['observed_at'],fresh['observed_at'])
        self.assertEqual(reply['settled_after'],completion['observed_at'])
        provider.refresh_observation.assert_not_called()

    def test_configuration_and_calibration_do_not_refresh_but_still_check_identity_and_stop(self):
        provider=policy();provider._identity=('episode','lease')
        provider._feedback_state=state(time.time()-4)
        provider.refresh_observation=Mock(side_effect=AssertionError('Hidden network read'))
        provider._rpc('configuration',{});provider._rpc('calibration',{})
        provider.refresh_observation.assert_not_called()
        provider._feedback_state['lease_id']='other'
        with self.assertRaisesRegex(AspireNotReady,'episode/lease'):
            provider._rpc('configuration',{})
        provider.cancelled=lambda:True
        with self.assertRaisesRegex(RuntimeError,'cancelled'):
            provider._rpc('calibration',{})

    def test_observation_and_motion_keep_freshness_refresh(self):
        for operation in ('observation','trajectory'):
            provider=policy();provider._identity=('episode','lease')
            provider._feedback_state=state(time.time()-4)
            provider.refresh_observation=Mock(side_effect=RuntimeError('Required fresh read'))
            with self.subTest(operation=operation),self.assertRaisesRegex(RuntimeError,'Required fresh read'):
                provider._rpc(operation,{})
            provider.refresh_observation.assert_called_once()

    def test_post_home_frames_are_not_rejected_by_newer_stationary_telemetry(self):
        # Exact measured epochs from a277b0ce. Both wrist frames are after Home.
        observed=state(1791324895.3226817,settled_after=1791324895.3226817,
            feedback_observed_at=1791324898.599317)
        frames={role:SimpleNamespace(metadata=dict(captured_at=stamp)) for role,stamp in
            [('left',1791324898.0743718),('right',1791324898.1647305)]}
        ApiDepthSet.require_post_settle(frames,observed)
        for role in frames:
            stale={role:SimpleNamespace(metadata=dict(captured_at=1791324895.2))}
            with self.assertRaises(CameraUnavailable):
                ApiDepthSet.require_post_settle(stale,observed)
        without_proof=state(1791324898.599317)
        with self.assertRaises(CameraUnavailable):
            ApiDepthSet.require_post_settle(frames,without_proof)

    def test_old_boundary_cannot_use_the_later_telemetry_pose(self):
        frames={'left':SimpleNamespace(metadata=dict(captured_at=101.))}
        with self.assertRaisesRegex(ValueError,'associated measured wrist pose'):
            ApiDepthSet.require_post_settle(frames,state(102.,settled_after=100.))

    def test_capture_context_cannot_cross_episode_lease_cursor_robot_or_source(self):
        now=time.time();provider=policy();provider._identity=('episode','lease')
        reconciled=provider.reconciled_observation(state(now-1.),state(now-.1))
        for field,value in [('episode_id','other'),('lease_id','other'),('step_id',2),
                            ('jetson_id','other'),('source','simulation')]:
            provider._feedback_state={**deepcopy(reconciled),field:value}
            with self.subTest(field=field),self.assertRaises(AspireNotReady):
                provider._feedback()

    def test_new_status_read_invalidates_old_completion_pose_even_if_refresh_merges_it(self):
        now=time.time();provider=policy();provider._identity=('episode','lease')
        old=provider.reconciled_observation(state(now-4.),state(now-3.))
        # The controller's refresh closure may merge its previous context. The
        # bridge must drop that proof before returning newly measured state.
        refreshed={**deepcopy(old),**state(now-.1,right_joints_deg=[20.]*6)}
        provider._feedback_state=old
        provider.refresh_observation=Mock(return_value=refreshed)
        result=provider._feedback()
        self.assertEqual(result['right_joints_deg'],[20.]*6)
        self.assertTrue({'settled_pose','settled_after','_capture_context'}.isdisjoint(result))

    def test_later_movement_creates_a_new_boundary_and_rejects_earlier_frames(self):
        now=time.time();provider=policy()
        for changes in (dict(step_id=2),dict(lease_id='other'),dict(settled=False)):
            with self.subTest(changes=changes),self.assertRaises(AspireNotReady):
                provider.reconciled_observation(state(now-1.),state(now-.1,**changes))
        after_move=provider.reconciled_observation(state(now-.5,step_id=2,
            right_joints_deg=[20.]*6),state(now-.1,step_id=2,right_joints_deg=[20.]*6))
        self.assertEqual(after_move['settled_after'],now-.5)
        pose={**after_move,**after_move['settled_pose']}
        with self.assertRaises(CameraUnavailable):
            ApiDepthSet.require_post_settle({'right':SimpleNamespace(
                metadata=dict(captured_at=now-.6))},pose)

    def test_owner_confirmed_stationary_refresh_retains_pose_only_in_same_context(self):
        now=time.time();provider=policy();provider._identity=('episode','lease')
        previous=provider.reconciled_observation(state(now-4.),state(now-3.))
        current=state(now-.1,right_joints_deg=[.1]*6)
        reconciled=provider.refresh_reconciled_observation(previous,current)
        provider._feedback_state=previous
        provider.refresh_observation=Mock(return_value=reconciled)
        result=provider._feedback()
        self.assertEqual(result['settled_after'],now-4.)
        self.assertEqual(result['settled_pose']['right_joints_deg'],[0.]*6)
        self.assertNotIn('_verified_capture_refresh',result)
        for changes in (dict(step_id=2),dict(lease_id='other'),dict(episode_id='other'),
                        dict(jetson_id='other'),dict(source='simulation'),dict(settled=False)):
            updated=provider.refresh_reconciled_observation(previous,{**current,**changes})
            with self.subTest(changes=changes):
                self.assertTrue({'settled_pose','settled_after','_capture_context'}.isdisjoint(updated))

    def test_capture_projects_with_original_measured_wrist_pose(self):
        from test_aspire_adapter import Depth, ReadOnlyAPI, calibration
        now=time.time();provider=policy()
        completion=state(now-1.,right_joints_deg=[0.]*6)
        fresh=state(now-.1,right_joints_deg=[.1]*6)
        observed=provider._public_feedback(provider.reconciled_observation(completion,fresh))
        with tempfile.TemporaryDirectory() as output:
            env=ObservationEnvironment(origin='https://example.invalid',robot_id='test',
                output_dir=output,api=ReadOnlyAPI(),depth_factory=Depth)
            env.status=lambda:deepcopy(observed)
            original_capture=env.depth['right'].capture
            def capture(**kwargs):
                snapshot=original_capture(**kwargs)
                snapshot.metadata['captured_at']=now-.5
                return snapshot
            env.depth['right'].capture=capture
            frame=env.camera_rgbd('right')
            np.testing.assert_allclose(frame['T_world_camera'],arm_pose(calibration(),completion,'right'))
            self.assertFalse(np.allclose(frame['T_world_camera'],arm_pose(calibration(),fresh,'right')))
            self.assertEqual(env._local.frames['right'][1]['observed_at'],completion['observed_at'])
            self.assertEqual(env._local.frames['right'][1]['feedback_observed_at'],fresh['observed_at'])

    def test_required_controller_refresh_preserves_home_boundary_with_live_cursor_confirmation(self):
        now=time.time();provider=policy()
        completion=state(now-4.);station=state(now-3.);fresh=state(now-.1)
        api=Mock(spec=['supports_trajectories','get_session','get_robot_observation'])
        api.supports_trajectories=True
        api.get_session.return_value=dict(status='running',episode_id='episode',lease_id='lease',
            latest_observation_step=1,active_trajectory=None)
        api.get_robot_observation.return_value=fresh
        controller=RunnerController(api,ik_solver=object(),robot_id='test',share_conversation=False)
        controller._status='running';controller._provider=provider
        controller._session_id='session';controller._episode_id='episode';controller._lease_id='lease'
        controller._command_step_id=1;controller._prompt='offline'
        controller.update_monitor_observation(station)
        provider._thread=object();provider._packets.put_nowait([dict(step_id=1)])
        builder=provider.build_trajectory
        def build_then_stop(*args):
            points=builder(*args)
            controller._status='stopped'  # Return before the command dispatch branch.
            return points
        provider.build_trajectory=build_then_stop
        controller._observation({},completion)
        self.assertEqual(provider._feedback_state['observed_at'],fresh['observed_at'])
        self.assertEqual(provider._feedback_state['settled_after'],completion['observed_at'])
        api.get_session.assert_called_once_with('session')
        api.get_robot_observation.assert_called_once_with('test')

    def test_invalid_settle_epoch_cannot_bypass_pairing(self):
        frames={'left':SimpleNamespace(metadata=dict(captured_at=101.))}
        for cutoff in (True,float('nan'),-1.,101.):
            with self.subTest(cutoff=cutoff),self.assertRaises(ValueError):
                ApiDepthSet.require_post_settle(frames,state(100.,settled_after=cutoff))


if __name__=='__main__':
    unittest.main()
