import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

from remote_yam.codex_check import CheckAdapter, check_observation
from remote_yam.codex_policy import (CodexAdapter, DECISION_SCHEMA, codex_environment,
                                    codex_status, decision_response)
from remote_yam.controller import RunnerController
from remote_yam.providers import PolicyComplete
from remote_yam.session import MockSessionAPI

THREAD = '12345678-1234-1234-1234-123456789abc'


def decision(action='done', **changes):
    result = {'action': action, 'targets': dict.fromkeys(DECISION_SCHEMA['properties']['targets']['required']),
              'note': '', 'summary': 'Finished', 'reason': '', 'hindsight': 'none'}
    result.update(changes)
    return result


def events(value, thread=THREAD):
    return [{'type': 'thread.started', 'thread_id': thread},
            {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': json.dumps(value)}},
            {'type': 'turn.completed'}]


class CodexPolicyTests(unittest.TestCase):
    def provider(self, **kwargs):
        with patch('remote_yam.codex_policy.codex_status', return_value={'ready': True}):
            provider = CheckAdapter(**kwargs)
        self.addCleanup(provider._workspace.cleanup)
        return provider

    def test_subscription_detection_never_exposes_account_output(self):
        def result(args, **kwargs):
            return subprocess.CompletedProcess(args, 0, 'codex-cli 0.154.0' if '--version' in args else '',
                                               '' if '--version' in args else 'Logged in using ChatGPT\nprivate-account@example.com')
        with patch('remote_yam.codex_policy.codex_binary', return_value='/bin/codex'), patch('remote_yam.codex_policy.subprocess.run', side_effect=result), patch('remote_yam.codex_policy.check_codex_storage'):
            status = codex_status()
        self.assertTrue(status['ready'])
        self.assertNotIn('private-account', json.dumps(status))

    def test_missing_old_and_api_key_login_are_not_ready(self):
        with patch('remote_yam.codex_policy.codex_binary', return_value=None):
            self.assertEqual(codex_status()['state'], 'missing')
        for version, login, expected in [('0.148.0', '', 'upgrade_required'), ('0.154.0', 'Logged in using an API key: secret', 'login_required')]:
            def result(args, **kwargs):
                return subprocess.CompletedProcess(args, 0, 'codex-cli '+version if '--version' in args else login, '')
            with patch('remote_yam.codex_policy.codex_binary', return_value='/bin/codex'), patch('remote_yam.codex_policy.subprocess.run', side_effect=result):
                status = codex_status()
            self.assertEqual(status['state'], expected)
            self.assertNotIn('secret', json.dumps(status))

    def test_missing_login_rejects_provider_before_any_model_or_queue(self):
        with patch('remote_yam.codex_policy.codex_status', return_value={'ready': False, 'message': 'Sign in first'}):
            with self.assertRaisesRegex(RuntimeError, 'Sign in first'):
                CodexAdapter()

    def test_environment_drops_api_keys_and_session_capabilities(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY':'secret', 'CODEX_API_KEY':'secret2', 'ASTRA_API_KEY':'secret3',
                                     'CODEX_ACCESS_TOKEN':'secret4', 'YAM_LEASE':'secret5', 'HOME':'/tmp/home'}, clear=True):
            self.assertEqual(codex_environment(), {'HOME':'/tmp/home'})

    def test_resume_uses_exact_thread_fresh_images_and_completed_feedback(self):
        provider = self.provider()
        provider.reasoning_effort = 'medium'
        move = decision('move_to', note='Close the left gripper slightly.')
        move['targets']['left_gripper'] = .8
        captured = []
        def execute(command, prompt, root):
            paths = [Path(command[i+1]) for i, arg in enumerate(command) if arg == '--image']
            self.assertEqual(len(paths), 3)
            self.assertTrue(all(p.exists() for p in paths))
            self.assertTrue(all(p.read_bytes().startswith(b'\x89PNG') for p in paths))
            self.assertNotIn('OPENAI_API_KEY', codex_environment())
            captured.append((command, prompt, paths))
            return events(move if len(captured) == 1 else decision())
        obs = check_observation()
        with patch.object(provider, '_execute', side_effect=execute):
            points = provider.build_trajectory('test', obs, 0)
            with self.assertRaisesRegex(RuntimeError, 'before its motion completes'):
                provider.build_trajectory('test', obs, 0)
            final = {**obs, **points[-1], 'step_id': points[-1]['step_id']+1}
            provider.trajectory_completed(final, len(points))
            with self.assertRaises(PolicyComplete):
                provider.build_trajectory('test', final, len(points))
        self.assertNotIn('resume', captured[0][0])
        self.assertEqual(captured[1][0][1:4], ['exec', 'resume', THREAD])
        self.assertIn('completed', captured[1][1])
        self.assertNotIn('Action contract:', captured[1][1])
        self.assertTrue(all(not path.exists() for _, _, paths in captured for path in paths))
        self.assertIn('forced_login_method="chatgpt"', captured[0][0])
        self.assertIn('features.shell_tool=false', captured[0][0])
        for command, _, _ in captured:
            self.assertIn('model_reasoning_effort="medium"', command)
        self.assertEqual(provider.public_config()['reasoning_effort'], 'medium')
        self.assertFalse(provider.public_config()['api_key_configured'])

    def test_invalid_decisions_never_reach_motion(self):
        bad = [None, {}, decision(action='shell'), decision(targets={}), decision(note=None)]
        for value in [True, float('nan'), float('inf'), '0.1']:
            item = decision('move_to')
            item['targets']['left_x'] = value
            bad.append(item)
        completion_move = decision()
        completion_move['targets']['left_x'] = .3
        bad.append(completion_move)
        for value in bad:
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                decision_response(value)

    def test_failed_incomplete_or_wrong_thread_output_is_rejected(self):
        for output in [[{'type':'turn.failed','error':{'message':'private-secret'}}],
                       events(decision())[:-1], events(decision(), 'not-a-thread')]:
            provider = self.provider()
            with patch.object(provider, '_execute', return_value=output):
                with self.assertRaises(RuntimeError) as caught:
                    provider.build_trajectory('test', check_observation(), 0)
                self.assertNotIn('private-secret', str(caught.exception))

    def test_local_bounds_rejections_are_returned_to_codex_without_packet(self):
        provider = self.provider()
        invalid = decision('move_to', note='Try an invalid target')
        invalid['targets']['left_x'] = 99
        with patch.object(provider, '_execute', return_value=events(invalid)) as execute:
            with self.assertRaisesRegex(RuntimeError, 'three consecutive'):
                provider.build_trajectory('test', check_observation(), 0)
        self.assertEqual(execute.call_count, 3)
        self.assertIsNone(provider._pending)
        self.assertIn('target_out_of_bounds', execute.call_args.args[1])

    def test_process_timeout_and_cancel_kill_child(self):
        for cancel in (False, True):
            provider = self.provider(timeout_s=.15 if not cancel else 5)
            stop = threading.Event()
            provider.cancelled = stop.is_set
            timer = threading.Timer(.1, stop.set)
            if cancel:
                timer.start()
            started = time.monotonic()
            with self.assertRaisesRegex(RuntimeError, 'cancelled' if cancel else 'timed out'):
                provider._execute([sys.executable, '-c', 'import time; time.sleep(30)'], '', Path(provider._workspace.name))
            self.assertLess(time.monotonic()-started, 3)
            if cancel:
                timer.join()

    def test_process_errors_do_not_leak_raw_diagnostics(self):
        provider = self.provider()
        with self.assertRaises(RuntimeError) as caught:
            provider._execute([sys.executable, '-c', 'import sys; print("secret", file=sys.stderr); sys.exit(1)'], '', Path(provider._workspace.name))
        self.assertNotIn('secret', str(caught.exception))

    def test_controller_mock_motion_and_resume_feedback(self):
        provider = self.provider()
        session = MockSessionAPI(observations=[check_observation()])
        controller = RunnerController(session)
        self.addCleanup(controller.disconnect)
        controller.update_monitor_observation(session.get_robot_observation('yam-1'))
        move = decision('move_to', note='Close left gripper slightly')
        move['targets']['left_gripper'] = .8
        with patch.object(provider, '_execute', side_effect=[events(move), events(decision())]):
            controller.join(provider, 'test')
            for _ in range(100):
                controller.update_monitor_observation(session.get_robot_observation('yam-1'))
                controller.process_next_event(.01)
                if controller.status()['status'] == 'stopped':
                    break
        self.assertEqual(controller.status()['status'], 'stopped')
        self.assertEqual(len(session.trajectory_log), 1)
        self.assertEqual(provider._calls, 2)
        self.assertEqual(provider._outcome['status'], 'done')
