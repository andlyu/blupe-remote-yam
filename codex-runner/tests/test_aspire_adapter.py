import copy
from concurrent.futures import ThreadPoolExecutor
import base64
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

import numpy as np

from remote_yam.aspire_adapter import (AspireNotReady, BlupeAspireAdapter,
    ObservationEnvironment, arm_pose, paired_trajectory)


def calibration():
    camera = dict(K=[[100., 0., 10.], [0., 110., 8.], [0., 0., 1.]],
                  distortion=[0.]*5, T_base_camera={s: np.eye(4).tolist() for s in ('left', 'right')},
                  T_grasp_camera=np.eye(4).tolist())
    chain = dict(joints=[dict(axis=[0., 0., 1.], position_m=[0., 0., 0.]) for _ in range(6)],
                 T_base_grasp_zero=np.eye(4).tolist())
    return dict(kinematics=dict(joint_units='degrees', left=chain, right=chain),
        base_geometry=dict(spacing_m=.62), calibration_id='fixture',
        cameras={c: copy.deepcopy(camera) for c in ('top', 'left', 'right')},
        quality=dict(extrinsics_provisional=True, unverified=['fixture']))


class ReadOnlyAPI:
    def __init__(self):
        self.observation = dict(jetson_id='test', source='hardware', mode='DISABLED',
            observed_at=time.time(), left_joints_deg=[0.]*6, right_joints_deg=[0.]*6,
            left_gripper=None, right_gripper=None, settled=False,
            safety=dict(ok=False, reason='hardware_not_initialized'))

    def get_robot_observation(self, robot_id):
        return copy.deepcopy(self.observation)


class Depth:
    def __init__(self, origin, robot_id, camera):
        self.report = calibration()
        self.calls = 0

    def read(self, suffix, limit):
        return json.dumps(self.report)

    def capture(self, *, stopped, **kwargs):
        self.calls += 1
        from types import SimpleNamespace
        return SimpleNamespace(rgb=np.full((16, 20, 3), self.calls, np.uint8),
            depth=np.full((16, 20), self.calls/10, np.float32),
            calibration=copy.deepcopy(self.report), metadata=dict(captured_at=time.time()),
            age=lambda: .1)


class PacketTests(unittest.TestCase):
    def packet(self, **kwargs):
        params = dict(timestamps=[0., 1.], left=[[0.]*6+[.5], [.1]*6+[.5]],
            right=[[0.]*6+[.5], [-.1]*6+[.5]], measured_left=[0.]*6+[.5],
            measured_right=[0.]*6+[.5], start_interp_s=0., first_step_id=9)
        params.update(kwargs)
        return paired_trajectory(**params)

    def test_paired_units_endpoint_and_cadence(self):
        points = self.packet()
        self.assertEqual(len(points), 10)
        self.assertEqual([p['step_id'] for p in points], list(range(9, 19)))
        np.testing.assert_allclose(points[-1]['left_joints_deg'], np.rad2deg([.1]*6))
        np.testing.assert_allclose(points[-1]['right_joints_deg'], np.rad2deg([-.1]*6))

    def test_velocity_policy_belongs_to_gateway_not_converter(self):
        points = self.packet(timestamps=[0., .01, 1.], left=[[0.]*6+[.5], [.1]*6+[.5], [0.]*6+[.5]],
                             right=[[0.]*6+[.5]]*3)
        self.assertGreater(np.deg2rad(points[0]['left_joints_deg'][0]), .035)

    def test_reject_bad_paths_and_do_not_clip_grippers(self):
        cases = [dict(timestamps=[0., 0.]), dict(playback_speed=float('nan')),
            dict(left=[[0.]*6+[1.01]]*2), dict(left=[[0.]*6+[float('nan')]]*2),
            dict(left=[[0.]*6]*2)]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ValueError):
                self.packet(**case)

    def test_delta_and_packet_budget_are_left_to_existing_api(self):
        points = self.packet(timestamps=[0., .1], left=[[0.]*6+[.5], [0.]*6+[1.]],
                             right=[[0.]*6+[.5]]*2)
        self.assertEqual(points[-1]['left_gripper'], 1.)
        self.assertEqual(len(self.packet(timestamps=[0., 31.])), 310)

    def test_non_integral_duration_reaches_final_target(self):
        points = self.packet(timestamps=[0., 1.05])
        self.assertEqual(len(points), 11)
        np.testing.assert_allclose(points[-1]['left_joints_deg'], np.rad2deg([.1]*6))


class ObservationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.api = ReadOnlyAPI()
        self.env = ObservationEnvironment(origin='https://example.invalid', robot_id='test',
            output_dir=self.temp.name, api=self.api, depth_factory=Depth)

    def test_station_contract_does_not_adopt_upstream_or_extra_camera_facts(self):
        self.env.depth['top'].report['cameras']['bottom'] = {'serial': 'not-installed'}
        info = self.env.station_info()
        self.assertEqual(info['camera_roles'], ['top', 'left', 'right'])
        self.assertEqual(info['camera_count'], 3)
        self.assertNotIn('bottom', info['camera_calibration'])
        self.assertEqual(info['absent_camera_roles'], ['bottom'])
        self.assertEqual(info['base_spacing_m'], .62)
        self.assertEqual(info['world_frame'], 'blupe_base_midpoint')
        self.assertIsNone(info['table_height_m'])
        self.assertFalse(info['physical_motion_enabled'])
        self.assertTrue(info['calibration_quality']['extrinsics_provisional'])
        saved = list(Path(self.temp.name).glob('*_station_contract.json'))
        self.assertEqual(json.loads(saved[0].read_text()), info)

    def test_station_contract_keeps_camera_roles_when_calibration_is_unavailable(self):
        self.env.calibration = lambda: (_ for _ in ()).throw(ConnectionError('offline'))
        info = self.env.station_info()
        self.assertEqual(info['camera_count'], 3)
        self.assertIsNone(info['base_spacing_m'])
        self.assertEqual(info['calibration_error'], 'ConnectionError')
        self.assertFalse(info['camera_calibration']['top']['present_in_report'])

    def test_image_recovery_accepts_the_real_retry_and_recovered_metadata_callback(self):
        from remote_yam.api_depth import depth_image_event
        depth = self.env.depth['top']
        original_capture = depth.capture
        def recover(**kwargs):
            self.assertTrue(kwargs['wait_for_image'])
            self.assertFalse(kwargs['cancelled']())
            depth_image_event(kwargs['on_event'], 'top')
            depth_image_event(kwargs['on_event'], 'top', recovered=True)
            return original_capture(**kwargs)
        depth.capture = recover
        self.env.render_rgb('top')
        events = [json.loads(p.read_text()) for p in sorted(Path(self.temp.name).glob('*_camera_recovery.json'))]
        self.assertEqual([event['kind'] for event in events], ['camera_retry', 'camera_recovered'])
        self.assertTrue(all(event['camera'] == 'top' for event in events))

    def test_explicit_gripper_defaults_do_not_claim_measurement_or_enable_planner_motion(self):
        self.env.selected_gripper_geometry = dict(source='upstream_default', validated=False,
            user_selected=True, target_frame='grasp', closing_axis='grasp_x')
        info = self.env.station_info()
        self.assertEqual(info['gripper_geometry'], self.env.selected_gripper_geometry)
        self.assertEqual(info['gripper_geometry_status'], 'UPSTREAM_DEFAULT_SELECTED_UNMEASURED')
        self.assertFalse(info['physical_motion_enabled'])
        self.assertFalse(info['upstream_station_defaults_validated'])
        self.assertIsNone(info['planner_world_frame'])
        with self.assertRaises(AspireNotReady):
            self.env.deny_motion()

    def test_upstream_profile_respects_explicit_three_camera_override(self):
        try:
            from robot import station_profiles
        except ImportError:
            self.skipTest('Set PYTHONPATH to pinned ASPIRE real checkout')
        with patch.dict(os.environ, {'OPENFORGE_REAL_YAM_CAMERAS': 'top,left,right'}):
            with patch.object(station_profiles, 'resolve_station_key', return_value='default'):
                self.assertEqual(station_profiles.active_station_cameras().names, ('top', 'left', 'right'))

    def test_null_gripper_is_not_fabricated(self):
        with self.assertRaisesRegex(AspireNotReady, 'gripper feedback unavailable'):
            self.env.get_observations('left')

    def test_robot_identity_is_kept_without_an_extra_observation_age_cutoff(self):
        self.api.observation['jetson_id'] = 'wrong'
        with self.assertRaises(AspireNotReady):
            self.env.status()
        self.api.observation['jetson_id'] = 'test'
        stamp = time.time()-9
        self.api.observation['observed_at'] = stamp
        self.assertEqual(self.env.status()['observed_at'], stamp)

    def test_rgb_depth_share_one_capture(self):
        image = self.env.render_rgb('top')
        depth = self.env.render_depth('top')
        self.assertEqual(self.env.depth['top'].calls, 1)
        self.assertEqual(image[0, 0, 0], 1)
        self.assertAlmostEqual(float(depth[0, 0]), .1)
        self.assertEqual(self.env.get_camera_intrinsics('top'), [100., 110., 10., 8.])

    def test_parallel_cameras_share_one_calibration_read(self):
        self.env.depth['top'].read=MagicMock(wraps=self.env.depth['top'].read)
        with ThreadPoolExecutor(max_workers=3) as pool:
            frames=list(pool.map(self.env.camera_rgbd,('top','left','right')))
        self.assertEqual(len(frames),3)
        self.env.depth['top'].read.assert_called_once_with('/calibration',256_000)
        self.assertEqual([self.env.depth[c].calls for c in ('top','left','right')],[1,1,1])

    def test_saved_policy_rgbd_is_one_atomic_capture_with_the_same_transform(self):
        frame = self.env.camera_rgbd('top')
        self.assertEqual(self.env.depth['top'].calls, 1)
        self.assertEqual(frame['rgb'][0, 0, 0], 1)
        self.assertAlmostEqual(float(frame['depth_m'][0, 0]), .1)
        self.assertAlmostEqual(frame['T_world_camera'][1, 3], .31)
        self.assertEqual(frame['K'][1, 1], 110.)

    def test_three_parallel_rgbd_calls_keep_pair_metadata_and_wrist_transforms(self):
        barrier=threading.Barrier(3)
        roles=('top','left','right')
        for value,camera in enumerate(roles,1):
            original=self.env.depth[camera].capture
            def capture(*,original=original,value=value,**kwargs):
                barrier.wait(timeout=5)
                frame=original(**kwargs)
                frame.rgb[:]=value
                frame.depth[:]=value/10
                frame.metadata['sequence']=value
                return frame
            self.env.depth[camera].capture=capture
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures=[pool.submit(self.env.camera_rgbd,camera) for camera in roles]
            frames=[future.result() for future in futures]
        for value,(camera,frame) in enumerate(zip(roles,frames),1):
            self.assertEqual(frame['camera'],camera)
            self.assertEqual(self.env.depth[camera].calls,1)
            self.assertEqual(int(frame['rgb'][0,0,0]),value)
            self.assertAlmostEqual(float(frame['depth_m'][0,0]),value/10)
            self.assertEqual(frame['metadata']['sequence'],value)
            self.assertEqual(frame['calibration_id'],'fixture')
            self.assertEqual(frame['K'][1,1],110.)
            # Fixture top calibration is expressed relative to the left base.
            expected_y={'top':.31,'left':.31,'right':-.31}[camera]
            self.assertAlmostEqual(frame['T_world_camera'][1,3],expected_y)
        artifacts=list(Path(self.temp.name).glob('*.json'))
        timings=[path for path in artifacts if path.name.endswith('_capture_timing.json')]
        self.assertEqual(len(timings),3)
        for path in timings:
            timing=json.loads(path.read_text())
            self.assertEqual(timing['status'],'ok')
            self.assertTrue(all(span['duration_s']>=0 for span in timing['spans']))
        self.assertEqual(len(artifacts),6)
        self.assertEqual(len({path.name.split('_',1)[0] for path in artifacts}),6)

    def test_wrist_retry_retains_one_feedback_cutoff_and_pose(self):
        self.api.observation.update(mode='API_ACTIVE', settled=True,
            observed_at=100., left_joints_deg=[90., 0., 0., 0., 0., 0.])
        original_status = self.api.get_robot_observation
        status = MagicMock(side_effect=original_status)
        self.api.get_robot_observation = status
        depth = self.env.depth['left']
        original_capture = depth.capture

        def capture(**kwargs):
            frame = original_capture(**kwargs)
            frame.metadata['captured_at'] = 99.5 if depth.calls == 1 else 100.1
            # Refreshing status would move the cutoff and use the wrong pose.
            self.api.observation.update(observed_at=101., left_joints_deg=[0.]*6)
            return frame

        depth.capture = capture
        frame = self.env.camera_rgbd('left')
        status.assert_called_once_with('test')
        self.assertEqual(depth.calls, 2)
        self.assertEqual(frame['metadata']['captured_at'], 100.1)
        np.testing.assert_allclose(frame['T_world_camera'][:3, 0], [0., 1., 0.], atol=1e-9)
        events = [json.loads(p.read_text())['kind'] for p in sorted(
            Path(self.temp.name).glob('*_camera_recovery.json'))]
        self.assertEqual(events, ['camera_retry', 'camera_recovered'])

    def test_cancelled_wrist_recovery_discards_rejected_pair(self):
        self.api.observation.update(mode='API_ACTIVE', settled=True, observed_at=100.)
        cancelled = threading.Event()
        self.env.cancelled = cancelled.is_set
        depth = self.env.depth['right']
        original_capture = depth.capture

        def capture(**kwargs):
            frame = original_capture(**kwargs)
            frame.metadata['captured_at'] = 99.
            cancelled.set()
            return frame

        depth.capture = capture
        with self.assertRaisesRegex(RuntimeError, 'Camera capture cancelled'):
            self.env.camera_rgbd('right')
        self.assertEqual(depth.calls, 1)
        self.assertNotIn('right', self.env._local.frames)

    def test_pair_expiring_after_acquisition_recaptures_rgb_depth_and_retains_pose(self):
        self.api.observation.update(mode='API_ACTIVE',settled=True,observed_at=100.,
            left_joints_deg=[90.,0.,0.,0.,0.,0.])
        status=MagicMock(side_effect=self.api.get_robot_observation)
        self.api.get_robot_observation=status
        depth=self.env.depth['left'];capture=depth.capture;ages={}
        def accepted(**kwargs):
            frame=capture(**kwargs);number=depth.calls
            ages[number]=4.9 if number==1 else .1
            frame.age=lambda:ages[number]
            self.api.observation.update(observed_at=101.,left_joints_deg=[0.]*6)
            return frame
        depth.capture=accepted
        save=np.save
        def delayed_save(*args,**kwargs):
            save(*args,**kwargs)
            if depth.calls==1:ages[1]=5.2
        with patch('numpy.save',side_effect=delayed_save):
            frame=self.env.camera_rgbd('left')
        status.assert_called_once_with('test')
        self.assertEqual(depth.calls,2)
        self.assertEqual(frame['rgb'][0,0,0],2)
        self.assertAlmostEqual(frame['depth_m'][0,0],.2)
        np.testing.assert_allclose(frame['T_world_camera'][:3,0],[0.,1.,0.],atol=1e-9)
        events=[json.loads(p.read_text()) for p in sorted(Path(self.temp.name).glob('*_camera_recovery.json'))]
        self.assertEqual([e['kind'] for e in events],['camera_retry','camera_recovered'])
        self.assertIn('5s freshness limit',events[0]['cause'])

    def test_expired_pair_recovery_cancels_without_retaining_or_reusing_old_rgb(self):
        self.api.observation.update(mode='API_ACTIVE',settled=True)
        cancelled=threading.Event();self.env.cancelled=cancelled.is_set
        depth=self.env.depth['right'];capture=depth.capture
        def expired(**kwargs):
            frame=capture(**kwargs);frame.age=lambda:5.2;cancelled.set();return frame
        depth.capture=expired
        with self.assertRaisesRegex(RuntimeError,'Camera capture cancelled'):
            self.env.camera_rgbd('right')
        self.assertEqual(depth.calls,1)
        self.assertNotIn('right',self.env._local.frames)

    def test_unrecoverable_stale_pairs_keep_existing_freshness_limit(self):
        self.api.observation.update(mode='API_ACTIVE',settled=True)
        self.env.preview_source.max_attempts=1
        depth=self.env.depth['right'];capture=depth.capture
        def expired(**kwargs):
            frame=capture(**kwargs);frame.age=lambda:5.2;return frame
        depth.capture=expired
        with self.assertRaisesRegex(RuntimeError,'5s freshness limit'):
            self.env.camera_rgbd('right')
        self.assertEqual(depth.calls,1)
        self.assertNotIn('right',self.env._local.frames)

    def test_wrapped_transient_depth_http_recaptures_pair_with_one_pose(self):
        from urllib.error import HTTPError
        self.api.observation.update(mode='API_ACTIVE',settled=True,observed_at=100.,
            right_joints_deg=[90.,0.,0.,0.,0.,0.])
        status=MagicMock(side_effect=self.api.get_robot_observation)
        self.api.get_robot_observation=status
        capture=self.env._camera_rgbd_once
        calls=[]
        def interrupted(camera,observation):
            calls.append(observation)
            if len(calls)==1:
                self.env._local.frames={camera:object()}
                self.api.observation.update(observed_at=101.,right_joints_deg=[0.]*6)
                try:
                    raise HTTPError('https://example.invalid',502,'Bad Gateway',{},None)
                except HTTPError as exc:
                    raise RuntimeError('Fresh API depth unavailable; no motion proposed') from exc
            self.assertNotIn(camera,self.env._local.frames)
            return capture(camera,observation)
        with patch.object(self.env,'_camera_rgbd_once',side_effect=interrupted):
            frame=self.env.camera_rgbd('right')
        status.assert_called_once_with('test')
        self.assertIs(calls[0],calls[1])
        np.testing.assert_allclose(frame['T_world_camera'][:3,0],[0.,1.,0.],atol=1e-9)
        events=[json.loads(p.read_text()) for p in sorted(Path(self.temp.name).glob('*_camera_recovery.json'))]
        self.assertEqual([e['kind'] for e in events],['camera_retry','camera_recovered'])
        self.assertEqual(events[0]['http_status'],502)

    def test_wrapped_nontransient_depth_http_does_not_retry(self):
        from urllib.error import HTTPError
        try:
            raise HTTPError('https://example.invalid',401,'Unauthorized',{},None)
        except HTTPError as exc:
            failure=RuntimeError('Fresh API depth unavailable; no motion proposed')
            failure.__cause__=exc
        with patch.object(self.env,'_camera_rgbd_once',side_effect=failure) as capture:
            with self.assertRaisesRegex(RuntimeError,'Fresh API depth unavailable'):
                self.env.camera_rgbd('right')
        self.assertEqual(capture.call_count,1)

    def test_untyped_capture_error_does_not_retry(self):
        with patch.object(self.env,'_camera_rgbd_once',side_effect=RuntimeError('malformed depth')) as capture:
            with self.assertRaisesRegex(RuntimeError,'malformed depth'):
                self.env.camera_rgbd('right')
        self.assertEqual(capture.call_count,1)

    def test_segment_uses_aspires_exact_image_protocol_and_validates_the_mask(self):
        rgb = np.full((16, 20, 3), 25, np.uint8)
        mask = np.zeros((16, 20), np.uint8)
        mask[4:12, 4:15] = 1
        buffer = io.BytesIO()
        np.save(buffer, mask)
        payload = dict(mask_b64=base64.b64encode(buffer.getvalue()).decode(), score=.9,
                       bbox_xywh=[4, 4, 11, 8])
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(payload).encode()
        with patch('urllib.request.urlopen', return_value=response) as post:
            result = self.env.segment_rgb(rgb, 'green cuboid', server_url='http://127.0.0.1:8119')
        request = post.call_args.args[0]
        self.assertEqual(request.full_url, 'http://127.0.0.1:8119/segment')
        body = json.loads(request.data)
        self.assertEqual(body['text'], 'green cuboid')
        from PIL import Image
        np.testing.assert_array_equal(np.asarray(Image.open(io.BytesIO(base64.b64decode(body['image_base64'])))), rgb)
        np.testing.assert_array_equal(result['mask'], mask.astype(bool))
        buffer = io.BytesIO()
        np.save(buffer, np.ones((4, 4), np.uint8))
        payload['mask_b64'] = base64.b64encode(buffer.getvalue()).decode()
        response.__enter__.return_value.read.return_value = json.dumps(payload).encode()
        with patch('urllib.request.urlopen', return_value=response), self.assertRaisesRegex(ValueError, 'mask/score'):
            self.env.segment_rgb(rgb, 'green cuboid', server_url='http://127.0.0.1:8119')

    def test_unavailable_aspires_segment_service_has_a_receipt_and_is_not_retried(self):
        from urllib.error import URLError
        rgb = np.full((16, 20, 3), 25, np.uint8)
        with patch('urllib.request.urlopen', side_effect=URLError('Connection refused')) as post:
            with self.assertRaisesRegex(AspireNotReady, 'BundleSDF/SAM3 segmentation unavailable'):
                self.env.segment_rgb(rgb, 'green cuboid', server_url='http://127.0.0.1:8119')
        post.assert_called_once()
        paths = list(Path(self.temp.name).glob('*_sam3_segment_failed.json'))
        self.assertEqual(len(paths), 1)
        receipt = json.loads(paths[0].read_text())
        self.assertEqual(receipt['endpoint'], 'http://127.0.0.1:8119/segment')
        self.assertEqual(receipt['query'], 'green cuboid')
        self.assertIn('Connection refused', receipt['error'])
        self.assertEqual(len(receipt['image_sha256']), 64)

    def test_each_thread_retains_its_own_rgb_depth_pair(self):
        captured = threading.Event()
        resume = threading.Event()
        result = []

        def first_reader():
            result.append(int(self.env.render_rgb('top')[0, 0, 0]))
            captured.set()
            resume.wait(timeout=2)
            result.append(float(self.env.render_depth('top')[0, 0]))

        thread = threading.Thread(target=first_reader)
        thread.start()
        self.assertTrue(captured.wait(timeout=2))
        self.assertEqual(self.env.render_rgb('top')[0, 0, 0], 2)
        resume.set()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result[0], 1)
        self.assertAlmostEqual(result[1], .1)

    def test_http_capture_does_not_overlap_calibration_read(self):
        depth = self.env.depth['top']
        entered = threading.Event()
        release = threading.Event()
        finished = threading.Event()
        capture = depth.capture

        def slow_capture(**kwargs):
            entered.set()
            release.wait(timeout=2)
            return capture(**kwargs)

        depth.capture = slow_capture
        first = threading.Thread(target=lambda: self.env.render_rgb('top'))
        second = threading.Thread(target=lambda: (self.env.calibration(), finished.set()))
        first.start()
        self.assertTrue(entered.wait(timeout=2))
        # The capture has already obtained its shared calibration. Force an
        # actual HTTP calibration read here; cached reads need no depth lock.
        self.env._calibration_report = None
        second.start()
        self.assertFalse(finished.wait(timeout=.05))
        release.set()
        first.join(timeout=2)
        second.join(timeout=2)
        self.assertTrue(finished.is_set())

    def test_distortion_is_recorded_without_an_extra_intrinsics_rejection(self):
        self.env.depth['left'].report['cameras']['left']['distortion'][0] = -.05
        self.env.render_rgb('left')
        self.assertEqual(self.env.get_camera_intrinsics('left'), [100., 110., 10., 8.])
        self.assertEqual(self.env._frame('left')[0].calibration['cameras']['left']['distortion'][0], -.05)

    def test_failed_capture_discards_previous_pair(self):
        self.env.render_rgb('top')
        self.env.depth['top'].capture = lambda **kwargs: (_ for _ in ()).throw(RuntimeError('gone'))
        with self.assertRaisesRegex(RuntimeError, 'gone'):
            self.env.render_rgb('top')
        with self.assertRaisesRegex(RuntimeError, 'gone'):
            self.env.render_depth('top')

    def test_unknown_camera_and_motion_rejected(self):
        with self.assertRaises(AspireNotReady):
            self.env.render_rgb('bottom')
        for name in ['command_joint_pos', 'command_joint_state', 'set_gripper', 'go_home',
                     'move_bimanual_joint_keypoints', '_move_bimanual_joint_keypoints']:
            with self.subTest(name=name), self.assertRaises(AspireNotReady):
                getattr(self.env, name)()

    def test_corrected_poe_and_midpoint_frame(self):
        self.api.observation['left_joints_deg'][0] = 90.
        pose = arm_pose(calibration(), self.api.observation, 'left')
        np.testing.assert_allclose(pose[:3, 0], [0., 1., 0.], atol=1e-9)
        self.assertAlmostEqual(pose[1, 3], .31)
        self.assertAlmostEqual(arm_pose(calibration(), self.api.observation, 'right')[1, 3], -.31)

    def test_upstream_namespace_without_follower_constructor(self):
        # Integration test against the real pinned ASPIRE sources, not mocks
        # of make_namespace. Portal hardware construction must never occur.
        try:
            from omegaconf import OmegaConf
            from robot.yam.yam_real_env import FollowerRobotClient
        except ImportError:
            self.skipTest('Set PYTHONPATH to pinned ASPIRE real checkout')
        cfg = OmegaConf.create(dict(robot=dict(dashboard=False, await_exit=False, go_home_on_exit=False)))
        with patch.object(FollowerRobotClient, '__init__', side_effect=AssertionError('hardware opened')):
            with patch('remote_yam.aspire_adapter.ObservationEnvironment', return_value=self.env):
                adapter = BlupeAspireAdapter(origin='https://example.invalid', robot_id='test', output_dir=self.temp.name)
                env, namespace = adapter.create_runtime(cfg=cfg)
        self.assertIs(env.env, self.env)
        self.assertIn('detect_objects_oneshot', namespace)
        self.assertIn('run_in_background', namespace)
        self.assertEqual(namespace['get_station_info']()['camera_roles'], ['top', 'left', 'right'])
        self.assertFalse(namespace['get_task_info']()['success'])
        with self.assertRaisesRegex(RuntimeError, 'gripper feedback unavailable'):
            namespace['get_robot_state']()
        with self.assertRaises(RuntimeError):
            namespace['open_gripper']('left')


if __name__ == '__main__':
    unittest.main()
