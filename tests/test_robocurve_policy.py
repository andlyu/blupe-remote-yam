import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from remote_yam.cameras import CameraFrame
from remote_yam.controller import RunnerController
from remote_yam.providers import PolicyComplete
from remote_yam.robocurve_contract import SYSTEM_PROMPT, TOOLS
from remote_yam.robocurve_policy import OpenAIAdapter
from remote_yam.robocurve_trajectory import RoboCurveTrajectory, InvalidMove, relative_rotation
from remote_yam.session import MockSessionAPI


def observation():
    return {'source': 'simulation', 'episode_id': 'ep_test', 'lease_id': 'lease_test',
            'step_id': 0, 'settled': True, 'left_gripper': .9994, 'right_gripper': .9947,
            'left_joints_deg': np.rad2deg([-.0311, .794, .6167, -.3748, -.0364, -.0246]).tolist(),
            'right_joints_deg': np.rad2deg([-.0227, .7929, .6193, -.3794, -.033, -.028]).tolist()}


class Cameras:
    def __init__(self):
        self.calls = 0

    def capture(self, obs):
        self.calls += 1
        return [CameraFrame(name, b'\xff\xd8' + f'{name}:{self.calls}'.encode() + b'\xff\xd9', 123.)
                for name in ('left', 'top', 'right')]


def response(name, args, call_id='call_1'):
    return {'output': [
        {'type': 'reasoning', 'id': 'rs_' + call_id, 'summary': [], 'encrypted_content': 'opaque-' + call_id},
        {'type': 'function_call', 'id': 'fc_' + call_id, 'call_id': call_id,
         'name': name, 'arguments': json.dumps(args)},
    ]}


def finished(obs, points):
    return {**obs, **points[-1], 'step_id': points[-1]['step_id']+1, 'settled': True}


class GeometryTests(unittest.TestCase):
    def test_packet_limit_explains_remaining_session_budget(self):
        fixture = json.loads((Path(__file__).parent/'fixtures/robocurve-orientation-drift.json').read_text())
        g = RoboCurveTrajectory()
        g.observe(fixture['start'])
        with patch('remote_yam.robocurve_trajectory.STEP', np.full(14, 1e-5)):
            with self.assertRaises(InvalidMove) as caught:
                g.build(fixture['rejected_targets'][0], fixture['after_first_motion'], 98)
        self.assertEqual(caught.exception.code, 'packet_waypoint_budget')
        self.assertIn('per-packet waypoint budget of 300', str(caught.exception))
        self.assertIn('2902 waypoints available', str(caught.exception))
        self.assertIn('multiple packets', str(caught.exception))
        with self.assertRaises(InvalidMove) as caught:
            RoboCurveTrajectory().build({'left_gripper': 0}, observation(), 2999)
        self.assertEqual(caught.exception.code, 'session_waypoint_budget')
        self.assertIn('remaining session waypoint budget of 1.', str(caught.exception))

    def test_recorded_tracking_drift_is_corrected_without_an_instant_joint_jump(self):
        fixture = json.loads((Path(__file__).parent/'fixtures/robocurve-orientation-drift.json').read_text())
        g = RoboCurveTrajectory()
        g.observe(fixture['start'])
        obs = fixture['after_first_motion']
        _, initial, _ = g.observe(obs)
        self.assertLess(initial[11], -.024)  # measured right pitch drift after packet 1
        for target in fixture['rejected_targets'][1:]:
            with self.subTest(target=target):
                points = g.build(target, obs, 48)
                self.assertGreater(len(points), 1)
                self.assertLessEqual(len(points), 100)
                previous = np.deg2rad(obs['left_joints_deg']+obs['right_joints_deg'])
                states = []
                for point in points:
                    q, state, _ = g.observe({**obs, **point})
                    self.assertLessEqual(float(np.max(np.abs(q-previous))), .01+1e-12)
                    states.append(state)
                    previous = q
                self.assertGreater(abs(states[0][11]), .01)  # correction is spread over time
                np.testing.assert_allclose(states[-1][[4,5,11,12]], 0, atol=3e-5)
                for key,value in target.items():
                    from remote_yam.robocurve_trajectory import NAMES
                    self.assertAlmostEqual(states[-1][NAMES.index(key)], value, delta=3e-6)
        points = g.build(fixture['rejected_targets'][0], obs, 48)
        self.assertGreater(len(points), 100)
        self.assertLessEqual(len(points), 300)
        path = [obs['left_joints_deg']+obs['right_joints_deg']] + [p['left_joints_deg']+p['right_joints_deg'] for p in points]
        self.assertLessEqual(float(np.max(np.abs(np.diff(np.deg2rad(path), axis=0)))), .01+1e-12)

    def test_recorded_no_demo_fk_and_joint_order(self):
        geometry = RoboCurveTrajectory()
        _, state, joints = geometry.observe(observation())
        np.testing.assert_allclose(state[:3], [.296971492931, .000782457548, .186951611416], atol=1e-10)
        np.testing.assert_allclose(state[7:10], [.296574503729, .002685195093, .187479255574], atol=1e-10)
        np.testing.assert_allclose(state[[3,4,5,10,11,12]], 0, atol=1e-12)
        self.assertEqual(joints[6], .9994)
        self.assertEqual(joints[13], .9947)
        self.assertAlmostEqual(joints[7], -.0227)

    def test_cartesian_line_joint_pacing_and_unnamed_arm(self):
        g, obs = RoboCurveTrajectory(), observation()
        q, state, _ = g.observe(obs)
        points = g.build({'left_z': float(state[2]+.015)}, obs, 9)
        self.assertGreater(len(points), 1)
        self.assertEqual([p['step_id'] for p in points], list(range(9, 9+len(points))))
        zs = [state[2]]
        for point in points:
            current, actual, _ = g.observe({**obs, **point})
            self.assertLessEqual(float(np.max(np.abs(current-q))), .01+1e-12)
            np.testing.assert_allclose(actual[:2], state[:2], atol=3e-6)
            np.testing.assert_allclose(actual[3:6], 0, atol=3e-5)
            np.testing.assert_allclose(current[6:], np.deg2rad(obs['right_joints_deg']), atol=1e-10)
            zs.append(actual[2])
            q = current
        self.assertTrue(all(b > a for a,b in zip(zs,zs[1:])))
        self.assertAlmostEqual(zs[-1], state[2]+.015, delta=3e-6)

    def test_yaw_reference_stays_at_trial_start(self):
        g, obs = RoboCurveTrajectory(), observation()
        g.observe(obs)
        original = [r.copy() for r in g.start_rotations]
        points = g.build({'left_yaw': .05}, obs, 0)
        _, state, _ = g.observe(finished(obs, points))
        self.assertAlmostEqual(state[3], .05, delta=3e-5)
        np.testing.assert_array_equal(g.start_rotations, original)
        # RoboCurve positive pitch rotates forward using negative standard Y.
        np.testing.assert_allclose(relative_rotation(0, .1, 0) @ [0,0,-1], [np.sin(.1),0,-np.cos(.1)])

    def test_gripper_headroom_and_invalid_targets(self):
        g, obs = RoboCurveTrajectory(), observation()
        obs['left_gripper'] = 1.
        points = g.build({'left_gripper': 0.}, obs, 0)
        self.assertEqual(len(points), 11)
        self.assertEqual(points[-1]['left_gripper'], 0)
        for value in ({}, {'left_pitch': .1}, {'left_x': True}, {'left_x': float('nan')},
                      {'left_x': 5}, {'wrong': 0}):
            with self.subTest(value=value), self.assertRaises(InvalidMove):
                g.build(value, obs, 0)
        with self.assertRaisesRegex(InvalidMove, 'budget'):
            g.build({'left_gripper': 0}, obs, 2999)


class PolicyTests(unittest.TestCase):
    def test_no_demos_full_history_fresh_cameras_and_completion_boundary(self):
        cameras, obs = Cameras(), observation()
        provider = OpenAIAdapter('private-key', 'gpt-6-astra', camera_source=cameras)
        first = response('move_to', {'targets': {'left_z': .195}, 'note': 'Raise slightly.'})
        last = response('done', {'summary': 'Finished.', 'hindsight': 'none'}, 'call_2')
        with patch.object(provider, '_post_json', side_effect=[first, last]) as post:
            points = provider.build_trajectory('place green block on plate', obs, 0)
            initial = copy.deepcopy(post.call_args.args[0])
            self.assertEqual(initial['input'][:2], [
                {'role':'system', 'content':SYSTEM_PROMPT},
                {'role':'user', 'content':'Goal: place green block on plate'}])
            self.assertEqual(len(initial['input']), 3)
            self.assertEqual(initial['tools'], TOOLS)
            self.assertEqual(initial['include'], ['reasoning.encrypted_content'])
            self.assertFalse(initial['store'])
            content = initial['input'][2]['content']
            self.assertIn('state[eef_state]: left_x=0.2970', content[0]['text'])
            self.assertIn('state[joint_pos]: [-0.0311, 0.794', content[0]['text'])
            self.assertEqual([p['text'].split("'")[1] for p in content[1:] if p['type']=='input_text'], ['top_cam','left_cam','right_cam'])
            self.assertNotIn('private-key', json.dumps(initial))
            self.assertFalse(any(i.get('type')=='function_call_output' for i in provider._history))
            with self.assertRaisesRegex(RuntimeError, 'before its motion completes'):
                provider.build_trajectory('place green block on plate', finished(obs,points), len(points))
            self.assertEqual(post.call_count, 1)
            provider.trajectory_completed(finished(obs, points), len(points))
            with self.assertRaises(PolicyComplete):
                provider.build_trajectory('place green block on plate', finished(obs,points), len(points))
            second = post.call_args.args[0]
            self.assertEqual(second['input'][:3], initial['input'])
            self.assertEqual(second['input'][3:5], first['output'])
            result = second['input'][5]
            self.assertEqual(result['call_id'], 'call_1')
            self.assertEqual(json.loads(result['output'])['steps'], len(points))
            self.assertNotEqual(second['input'][-1]['content'][2], content[2])
            self.assertEqual(provider.public_config()['outcome']['status'], 'done')
        self.assertEqual(cameras.calls, 2)

    def test_invalid_tool_is_corrected_before_motion_and_limit_is_bounded(self):
        provider = OpenAIAdapter('test', 'astra', camera_source=Cameras())
        invalid = response('move_to', {'targets': {'right_pitch': .1}, 'note': 'Tilt.'})
        good = response('move_to', {'targets': {'left_gripper': .9}, 'note': 'Close a little.'}, 'call_2')
        with patch.object(provider, '_post_json', side_effect=[invalid,good]) as post:
            points = provider.build_trajectory('pick', observation(), 0)
        self.assertTrue(points)
        result = next(i for i in post.call_args.args[0]['input'] if i.get('type')=='function_call_output')
        self.assertEqual(json.loads(result['output'])['steps'], 0)
        self.assertFalse(json.loads(result['output'])['ok'])
        bad = OpenAIAdapter('test', 'astra', camera_source=Cameras())
        with patch.object(bad, '_post_json', return_value=invalid) as post:
            with self.assertRaisesRegex(RuntimeError, 'three consecutive'):
                bad.build_trajectory('pick', observation(), 0)
            self.assertEqual(post.call_count, 3)

    def test_multiple_calls_do_not_execute_either_arm(self):
        provider = OpenAIAdapter('test', 'astra', camera_source=Cameras())
        raw = response('move_to', {'targets': {'left_z': .2}, 'note':'up'})
        raw['output'] += response('move_to', {'targets':{'right_z': .2}, 'note':'up'}, 'call_2')['output']
        done = response('give_up', {'reason':'Cannot determine a safe move.', 'hindsight':'none'}, 'call_3')
        with patch.object(provider, '_post_json', side_effect=[raw, done]) as post:
            with self.assertRaises(PolicyComplete):
                provider.build_trajectory('pick', observation(), 0)
        errors = [i for i in post.call_args.args[0]['input'] if i.get('type')=='function_call_output']
        self.assertEqual(len(errors), 2)
        self.assertTrue(all(json.loads(i['output'])['steps']==0 for i in errors))
        self.assertEqual(provider.public_config()['outcome']['status'], 'give_up')

    def test_new_trial_requires_new_history_and_camera_failure_blocks_model(self):
        provider = OpenAIAdapter('test', 'astra', camera_source=Cameras())
        with patch.object(provider, '_post_json', return_value=response('move_to', {'targets':{'left_z':.195}, 'note':'up'})):
            points = provider.build_trajectory('pick', observation(), 0)
        provider.trajectory_completed(finished(observation(),points), len(points))
        with self.assertRaisesRegex(RuntimeError, 'fresh RoboCurve'):
            provider.build_trajectory('pick', {**observation(),'episode_id':'ep_new'}, len(points))
        fresh = OpenAIAdapter('test', 'astra', camera_source=Cameras())
        self.assertEqual(fresh.public_config()['history_items'], 0)
        with patch.object(fresh._camera_source, 'capture', side_effect=RuntimeError('camera unavailable')), patch.object(fresh, '_post_json') as post:
            with self.assertRaisesRegex(RuntimeError, 'camera unavailable'):
                fresh.build_trajectory('pick', observation(), 0)
            post.assert_not_called()

    def test_exact_local_recording_restores_camera_bytes_without_credentials(self):
        with tempfile.TemporaryDirectory() as root:
            provider = OpenAIAdapter('private-key', 'astra', camera_source=Cameras(), recording_root=root)
            with patch.object(provider, '_post_json', return_value=response('give_up', {'reason':'test','hindsight':'none'})):
                with self.assertRaises(PolicyComplete):
                    provider.build_trajectory('pick', observation(), 0)
            path = Path(provider.public_config()['recording_path'])
            text = (path/'calls.jsonl').read_text()
            self.assertNotIn('private-key', text)
            rows = [json.loads(line) for line in text.splitlines()]
            self.assertEqual([r['kind'] for r in rows], ['request','response','tool_result'])
            images = [p['image_url'] for p in rows[0]['request']['input'][-1]['content'] if p['type']=='input_image']
            for image in images:
                digest = image.split('$blob:')[1]
                self.assertEqual(hashlib.sha256((path/'blobs'/digest).read_bytes()).hexdigest(), digest)

    def test_stop_during_model_request_never_submits_late_packet(self):
        session = MockSessionAPI()
        controller = RunnerController(session)
        provider = OpenAIAdapter('test', 'astra', camera_source=Cameras())
        # Use a real model/FK observation while isolating the cancellation race.
        controller._provider, controller._status = provider, 'running'
        controller._session_id, controller._episode_id, controller._lease_id = 'sess', 'ep_test', 'lease_test'
        def model(_):
            controller._stop_event.set()
            controller._status = 'stopped'
            return response('move_to', {'targets':{'left_z':.195},'note':'up'})
        with patch.object(provider, '_post_json', side_effect=model), patch.object(session, 'submit_trajectory') as submit:
            controller._dispatch_trajectory(provider.build_trajectory, provider, 'pick', observation(), 'sess','ep_test','lease_test',0)
            submit.assert_not_called()
        self.assertIsNone(controller._trajectory)
