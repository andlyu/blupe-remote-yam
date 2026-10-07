from copy import deepcopy
import threading
import time
import unittest
from unittest.mock import Mock

import numpy as np

from remote_yam.aspire_adapter import AspireNotReady
from remote_yam.aspire_api_policy import AspireApiPolicy, BridgeClient, BridgeServer, _read_message, _write_message
from remote_yam.controller import RunnerController
from remote_yam.session import HttpSessionAPI, MockSessionAPI


def feedback(**changes):
    state = dict(episode_id='episode', lease_id='lease', step_id=0,
        jetson_id='test', source='simulation', settled=True, homed=True,
        observed_at=time.time(), safety=dict(ok=True),
        left_joints_deg=[0.]*6, right_joints_deg=[0.]*6,
        left_gripper=.5, right_gripper=.5)
    state.update(changes)
    return state


def fixture_policy(program=lambda socket_path: {'status': 'complete'}):
    return AspireApiPolicy(run_harness=program, calibration={'fixture': True},
        robot_id='test', task='Test ASPIRE API', allow_hardware=False)


class ApiBridgeTests(unittest.TestCase):
    def test_bridge_receipt_separates_handler_and_socket_wait_without_changing_result(self):
        receipts=[]
        with BridgeServer(lambda operation,arguments:arguments) as server:
            client=BridgeClient(server.path,on_timing=receipts.append)
            self.assertEqual(client.call('observation',fixture=True),dict(fixture=True))
        timing=receipts[0]
        self.assertEqual(timing['operation'],'observation')
        self.assertGreaterEqual(timing['duration_s'],0)
        self.assertGreaterEqual(timing['server']['handler_s'],0)
        self.assertGreaterEqual(timing['server']['send_to_handler_s'],0)

    def test_three_camera_connections_can_wait_before_first_accept_and_rpc_stays_serial(self):
        import socket
        clients=[];calls=[]
        server=BridgeServer(lambda operation,arguments:calls.append(operation) or arguments)
        try:
            # Deterministic burst: all three camera requests connect before the
            # serving thread runs. A backlog of one refuses the second on macOS.
            for role in ('top','left','right'):
                sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);sock.settimeout(2)
                clients.append(sock);sock.connect(server.path)
            with server:
                for index,(role,sock) in enumerate(zip(('top','left','right'),clients)):
                    with sock.makefile('rwb') as stream:
                        _write_message(stream,dict(operation=role,arguments={'camera':role}))
                        self.assertEqual(_read_message(stream),dict(ok=True,result={'camera':role}))
                    self.assertEqual(calls,list(('top','left','right')[:index+1]))
        finally:
            for sock in clients:sock.close()
            if not server._closed.is_set():
                server._socket.close();server._directory.cleanup()

    def test_harness_shutdown_exception_does_not_mask_controller_failure(self):
        def failed_harness(socket_path):
            raise RuntimeError('secondary cancellation')
        policy=fixture_policy(failed_harness)
        policy.trajectory_failed('joint_settle_timeout','fixture controller',1)
        policy._run_program()
        self.assertEqual(policy._outcome['reason'],'joint_settle_timeout: fixture controller')
        self.assertFalse(policy._outcome['success'])
        with self.assertRaisesRegex(RuntimeError,'joint_settle_timeout'):
            policy._execute([dict(step_id=0)])
        self.assertTrue(policy._packets.empty())

    def test_default_mode_uses_hardware_api_without_preview_admission(self):
        api = HttpSessionAPI('https://example.invalid', robot_id='test', supports_trajectories=True)
        api.create_session = Mock(side_effect=AssertionError('offline test must not create a live queue'))
        policy = AspireApiPolicy(run_harness=lambda socket_path: {}, calibration={},
            robot_id='test', task='Test ASPIRE API')
        policy.validate_session_admission(api)
        self.assertTrue(policy.allow_hardware)
        self.assertEqual(policy._expected_source, 'hardware')
        api.create_session.assert_not_called()

    def test_hardware_uses_measured_feedback_without_matching_preview_receipt(self):
        task = 'Test ASPIRE API'
        policy = AspireApiPolicy(run_harness=lambda socket_path: {},
            calibration=dict(robot_id='test', calibration_id='calibration'),
            robot_id='test', task=task, allow_hardware=True)
        api = HttpSessionAPI('https://example.invalid', robot_id='test', supports_trajectories=True)
        api.create_session = Mock(side_effect=AssertionError('test must not enter queue'))
        policy.validate_session_admission(api)
        api.create_session.assert_not_called()
        policy._identity = ('episode', 'lease')
        policy._feedback_state = feedback(source='hardware')
        self.assertEqual(policy._feedback()['source'], 'hardware')
        for changes in (dict(lease_id='other'), dict(left_gripper=None), dict(source='simulation')):
            with self.subTest(changes=changes), self.assertRaises(AspireNotReady):
                policy._feedback_state = feedback(source='hardware', **{k:v for k,v in changes.items() if k!='source'}) if 'source' not in changes else feedback(**changes)
                policy._feedback()

    def test_lease_robot_source_and_measured_state_still_match_transport(self):
        policy = fixture_policy()
        policy._identity = ('episode', 'lease')
        cases = [dict(lease_id='other'), dict(jetson_id='other'),
                 dict(source='hardware'), dict(left_gripper=None)]
        for changes in cases:
            with self.subTest(changes=changes):
                policy._feedback_state = feedback(**changes)
                with self.assertRaises(AspireNotReady):
                    policy._feedback()

    def test_feedback_has_no_extra_freshness_settle_or_safety_rejection(self):
        policy = fixture_policy()
        policy._identity = ('episode', 'lease')
        policy._feedback_state = feedback(observed_at=time.time()-3, settled=False, safety=dict(ok=False))
        state = policy._feedback()
        self.assertFalse(state['settled'])
        self.assertFalse(state['safety']['ok'])

    def test_ready_feedback_never_exposes_api_authorization(self):
        policy = fixture_policy()
        policy._identity = ('episode', 'lease')
        policy._feedback_state = feedback(session_capability='private test capability')
        reply = policy._rpc('observation', {})
        self.assertNotIn('session_capability', reply)
        self.assertNotIn('lease_id', reply)
        self.assertNotIn('episode_id', reply)

    def test_stale_refresh_cannot_change_step_lease_or_source(self):
        for changes in (dict(step_id=1), dict(lease_id='other'), dict(source='hardware')):
            policy = fixture_policy()
            policy._identity = ('episode', 'lease')
            policy._feedback_state = feedback(observed_at=time.time()-3)
            policy.refresh_observation = lambda: feedback(**changes)
            with self.subTest(changes=changes), self.assertRaises(AspireNotReady):
                policy._feedback()

    def test_shared_packet_handoff_waits_for_matching_completion_and_cancels(self):
        policy = fixture_policy()
        packet = [dict(step_id=0)]
        replies = []
        finished = threading.Event()

        def execute():
            try:
                replies.append(policy._execute(packet))
            except Exception as exc:
                replies.append(exc)
            finally:
                finished.set()

        worker = threading.Thread(target=execute)
        worker.start()
        self.assertEqual(policy._packets.get(timeout=1), packet)
        policy._pending = dict(last_step_id=0, waypoint_count=1)
        self.assertFalse(finished.wait(.05))
        with self.assertRaisesRegex(RuntimeError, 'does not match'):
            policy.trajectory_completed(feedback(step_id=9), 1)
        self.assertFalse(finished.is_set())
        policy.trajectory_failed('timeout', 'No measured completion', 1)
        worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertIsInstance(replies[0], RuntimeError)
        self.assertIn('timeout', str(replies[0]))
        self.assertTrue(policy._packets.empty())

    def test_aspires_two_paths_use_the_existing_five_minute_api_session(self):
        completions = []

        def program(socket_path):
            client = BridgeClient(socket_path)
            for delta in (.01, -.01):
                obs = client.call('observation')
                paths = []
                for side, sign in [('left', 1), ('right', -1)]:
                    start = np.r_[np.deg2rad(obs[side+'_joints_deg']), obs[side+'_gripper']]
                    end = start.copy()
                    end[0] += sign*delta
                    paths.append([start.tolist(), end.tolist()])
                completions.append(client.call('trajectory', timestamps=[0., 1.],
                    left=paths[0], right=paths[1], start_interp_s=0.))
            return dict(status='complete')

        api = MockSessionAPI()
        policy = fixture_policy(program)
        controller = RunnerController(api, robot_id='test', submit_attempts=1,
            hardware_control_enabled=False, share_conversation=False)
        controller.join_and_run(policy, policy.task, run_duration_s=300)
        controller._worker.join(10)
        if controller._worker.is_alive():
            controller.stop('user_requested')
        self.assertFalse(controller._worker.is_alive())
        if policy._thread:
            policy._thread.join(2)
        self.assertIsNone(controller.status()['error'])
        self.assertEqual(api.create_requests[0]['run_duration_s'], 300)
        self.assertEqual(len(api.create_requests), 1)
        self.assertEqual(len(api.trajectory_log), 2)
        self.assertEqual(len(completions), 2)
        self.assertEqual([p['waypoints'][0]['step_id'] for p in api.trajectory_log], [0, 10])
        np.testing.assert_allclose(api.trajectory_log[-1]['waypoints'][-1]['left_joints_deg'],
                                   api.DEFAULT_OBSERVATIONS[0]['left_joints_deg'])
        self.assertTrue(all(result['success'] for result in completions))


if __name__ == '__main__':
    unittest.main()
