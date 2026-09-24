import unittest
from unittest.mock import patch

from remote_yam.subscription_setup import probe_subscription, verify_subscription
from remote_yam.run_errors import public_run_error


class SubscriptionVerificationTests(unittest.TestCase):
    def test_probe_uses_selected_subscription_adapter_and_no_robot_input(self):
        for name, adapter_name, model in [('claude', 'ClaudeAdapter', 'claude-opus-5-5'),
                                          ('codex', 'CodexAdapter', 'gpt-6-astra')]:
            with self.subTest(provider=name), patch(f'remote_yam.{name}_policy.{adapter_name}') as cls:
                adapter = cls.return_value
                adapter.model = model
                adapter._post_json.return_value = {'output': [{'name': 'done'}]}
                self.assertEqual(probe_subscription(name, model), model)
                cls.assert_called_once_with(model, timeout_s=45)
                self.assertEqual(adapter._expected_camera_count, 0)
                payload = adapter._post_json.call_args.args[0]
                self.assertEqual(payload['tools'], [])
                self.assertEqual([item['type'] for item in payload['input'][0]['content']], ['input_text'])
                adapter._workspace.cleanup.assert_called_once()

    def test_probe_refuses_noncompletion_and_cleans_workspace_on_failure(self):
        with patch('remote_yam.claude_policy.ClaudeAdapter') as cls:
            adapter = cls.return_value
            adapter._post_json.return_value = {'output': [{'name': 'move_to'}]}
            with self.assertRaisesRegex(RuntimeError, 'required completion'):
                probe_subscription('claude')
            adapter._workspace.cleanup.assert_called_once()

    def test_saved_login_is_not_live_verification(self):
        with patch('remote_yam.claude_policy.claude_status', return_value={
                'ready': True, 'state': 'ready', 'message': 'Signed in'}), \
             patch('remote_yam.subscription_setup.probe_subscription') as probe:
            for error, state in [('Claude Code needs sign-in.', 'login_required'),
                                 ('Claude subscription usage limit reached.', 'usage_limit'),
                                 ('private service diagnostic', 'verification_failed')]:
                probe.side_effect = RuntimeError(error)
                result = verify_subscription('claude')
                self.assertFalse(result['ready'])
                self.assertFalse(result['verified'])
                self.assertEqual(result['state'], state)
                self.assertNotIn('private service diagnostic', result['message'])
            probe.side_effect = None
            probe.return_value = 'claude-opus-5-5'
            self.assertTrue(verify_subscription('claude')['verified'])

    def test_known_subscription_errors_have_explicit_public_labels(self):
        for error in ['Claude Code needs sign-in.', 'Codex needs ChatGPT sign-in.']:
            label = public_run_error({'error': 'RuntimeError: ' + error})
            self.assertIn('subscription sign-in', label)
            self.assertNotIn('API credentials', label)
