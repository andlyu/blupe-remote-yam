import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from remote_yam.claude_policy import (ClaudeAdapter, DEFAULT_MODEL, claude_binary, claude_environment,
                                      claude_status, decision_response)
from remote_yam.codex_policy import DECISION_SCHEMA

SESSION = 'b2278dc6-641c-4fa7-8d79-a1b0c4ec7d2d'
PIXEL = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==')


def decision(action='done', **changes):
    result = {'action': action, 'targets': dict.fromkeys(DECISION_SCHEMA['properties']['targets']['required']),
              'note': '', 'summary': 'Finished', 'reason': '', 'hindsight': 'none'}
    result.update(changes)
    return result


def report(value=None, session=SESSION, **changes):
    result = {'type': 'result', 'subtype': 'success', 'is_error': False, 'session_id': session,
              'permission_denials': [], 'total_cost_usd': 0.0,
              'usage': {'input_tokens': 10, 'output_tokens': 5},
              'result': json.dumps(decision() if value is None else value)}
    result.update(changes)
    return result


def payload(images=3, prior=0):
    """`prior` stands in for turns the adapter has already sent to Claude."""
    content = [{'type': 'input_text', 'text': 'Measured state: settled'}]
    for _ in range(images):
        content.append({'type': 'input_image',
                        'image_url': 'data:image/png;base64,' + base64.b64encode(PIXEL).decode()})
    history = [{'role': 'user', 'content': [{'type': 'input_text', 'text': f'earlier turn {index}'}]}
               for index in range(prior)]
    return {'input': history + [{'role': 'user', 'content': content}],
            'instructions': 'task', 'tools': []}


class ClaudeStatusTests(unittest.TestCase):
    def test_binary_discovery_with_desktop_path(self):
        with tempfile.TemporaryDirectory() as home, \
             patch('remote_yam.claude_policy.Path.home', return_value=Path(home)), \
             patch.dict(os.environ, {'PATH': '/usr/bin:/bin'}):
            self.assertIsNone(claude_binary())
            binary = Path(home) / '.npm-global/bin/claude'
            binary.parent.mkdir(parents=True)
            binary.write_text('#!/bin/sh\nexit 0\n')
            self.assertIsNone(claude_binary())
            binary.chmod(0o755)
            self.assertEqual(claude_binary(), str(binary))
            native = Path(home) / '.local/bin/claude'
            native.parent.mkdir(parents=True)
            native.write_text('#!/bin/sh\nexit 0\n')
            native.chmod(0o755)
            self.assertEqual(claude_binary(), str(native))
            with patch.dict(os.environ, {'PATH': str(binary.parent)}):
                self.assertEqual(claude_binary(), str(binary))

    def status(self, stdout, returncode=0, version='2.1.263'):
        def result(args, **kwargs):
            if '--version' in args:
                return subprocess.CompletedProcess(args, 0, version + ' (Claude Code)', '')
            return subprocess.CompletedProcess(args, returncode, stdout, '')
        with patch('remote_yam.claude_policy.claude_binary', return_value='/bin/claude'), \
             patch('remote_yam.claude_policy.subprocess.run', side_effect=result):
            return claude_status()

    def test_subscription_login_is_ready_and_hides_the_account(self):
        status = self.status(json.dumps({'loggedIn': True, 'authMethod': 'claude.ai',
                                         'subscriptionType': 'max', 'email': 'private@example.com',
                                         'orgId': 'org_secret', 'orgName': 'Private Org'}))
        self.assertTrue(status['ready'])
        self.assertEqual(status['plan'], 'max')
        serialized = json.dumps(status)
        for secret in ('private@example.com', 'org_secret', 'Private Org'):
            self.assertNotIn(secret, serialized)

    def test_api_key_login_is_refused_so_runs_use_the_subscription(self):
        status = self.status(json.dumps({'loggedIn': True, 'authMethod': 'apiKey'}))
        self.assertFalse(status['ready'])
        self.assertEqual(status['state'], 'api_key_login')

    def test_missing_binary_and_logged_out_and_old_build(self):
        with patch('remote_yam.claude_policy.claude_binary', return_value=None):
            self.assertEqual(claude_status()['state'], 'missing')
        self.assertEqual(self.status(json.dumps({'loggedIn': False}))['state'], 'login_required')
        self.assertEqual(self.status('{}', version='1.9.0')['state'], 'upgrade_required')
        # This build signs in successfully but rejects --restricted at inference.
        self.assertEqual(self.status(json.dumps({'loggedIn': True, 'authMethod': 'claude.ai'}),
                                     version='2.1.185')['state'], 'upgrade_required')

    def test_environment_withholds_anthropic_keys_and_runner_secrets(self):
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'sk-ant-secret',
                                     'ANTHROPIC_AUTH_TOKEN': 'oauth-secret',
                                     'OPENAI_API_KEY': 'sk-openai-secret',
                                     'ASTRA_API_KEY': 'astra-secret',
                                     'HOME': '/Users/tester'}):
            environment = claude_environment()
        self.assertEqual(environment.get('HOME'), '/Users/tester')
        for name in ('ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'OPENAI_API_KEY', 'ASTRA_API_KEY'):
            self.assertNotIn(name, environment)


class ClaudePolicyTests(unittest.TestCase):
    def provider(self, **kwargs):
        with patch('remote_yam.claude_policy.claude_status', return_value={'ready': True}), \
             patch('remote_yam.claude_policy.claude_binary', return_value='/bin/claude'):
            provider = ClaudeAdapter(**kwargs)
        self.addCleanup(provider._workspace.cleanup)
        return provider

    def run_once(self, provider, result, data=None):
        seen = {}

        def execute(command, prompt, root):
            seen['command'], seen['prompt'] = command, prompt
            seen['images'] = sorted(p.name for p in root.iterdir() if p.name.startswith('camera-'))
            return result
        with patch.object(ClaudeAdapter, '_execute', side_effect=execute):
            return provider._post_json(data or payload()), seen

    def test_default_model_is_opus_55_and_is_passed_through(self):
        provider = self.provider()
        self.assertEqual(provider.model, DEFAULT_MODEL)
        _, seen = self.run_once(provider, report())
        self.assertIn(DEFAULT_MODEL, seen['command'])
        self.assertEqual(seen['command'][seen['command'].index('--model') + 1], DEFAULT_MODEL)

    def test_only_the_read_tool_is_granted_and_settings_are_ignored(self):
        provider = self.provider()
        _, seen = self.run_once(provider, report())
        command = seen['command']
        self.assertEqual(command[command.index('--tools') + 1], 'Read')
        for flag in ('--restricted', '--strict-mcp-config', '--disable-slash-commands'):
            self.assertIn(flag, command)
        self.assertEqual(command[command.index('--permission-prompts') + 1], 'none')

    def test_camera_frames_are_written_for_the_model_and_removed_afterwards(self):
        provider = self.provider()
        _, seen = self.run_once(provider, report())
        self.assertEqual(seen['images'], ['camera-0.png', 'camera-1.png', 'camera-2.png'])
        self.assertIn('camera-0.png', seen['prompt'])
        self.assertFalse(list(Path(provider._workspace.name).glob('camera-*')))

    def test_missing_camera_frames_are_refused(self):
        provider = self.provider()
        with self.assertRaises(RuntimeError) as caught:
            self.run_once(provider, report(), payload(images=2))
        self.assertIn('exactly 3 fresh camera images', str(caught.exception))

    def test_denied_image_read_never_produces_motion(self):
        provider = self.provider()
        with self.assertRaises(RuntimeError) as caught:
            self.run_once(provider, report(permission_denials=[{'tool_name': 'Read'}]))
        self.assertIn('no motion sent', str(caught.exception))

    def test_reported_error_result_never_produces_motion(self):
        provider = self.provider()
        with self.assertRaises(RuntimeError) as caught:
            self.run_once(provider, report(is_error=True, result='Model request failed'))
        self.assertIn('no motion sent', str(caught.exception))

    def test_expired_login_is_reported_as_a_sign_in_problem(self):
        provider = self.provider()
        with self.assertRaises(RuntimeError) as caught:
            self.run_once(provider, report(
                is_error=True, result='Failed to authenticate: OAuth session expired'))
        self.assertIn('needs sign-in', str(caught.exception))

    def test_malformed_decision_json_never_produces_motion(self):
        provider = self.provider()
        with self.assertRaises(RuntimeError) as caught:
            self.run_once(provider, report(result='not json'))
        self.assertIn('no motion sent', str(caught.exception))

    def test_conversation_resumes_and_a_switched_conversation_is_refused(self):
        provider = self.provider()
        _, first = self.run_once(provider, report())
        self.assertIn('--session-id', first['command'])
        self.assertNotIn('--resume', first['command'])
        _, second = self.run_once(provider, report(), payload(prior=2))
        self.assertEqual(second['command'][second['command'].index('--resume') + 1], SESSION)
        # Only the turns Claude has not seen are resent.
        self.assertNotIn('earlier turn 0', second['prompt'])
        with self.assertRaises(RuntimeError) as caught:
            self.run_once(provider, report(session='99999999-1234-1234-1234-123456789abc'),
                          payload(prior=4))
        self.assertIn('wrong conversation', str(caught.exception))

    def test_usage_is_reported_for_the_eval_record(self):
        provider = self.provider()
        self.run_once(provider, report())
        self.assertEqual(provider.public_config()['usage'],
                         {'input_tokens': 10, 'output_tokens': 5, 'cost_usd': 0.0})
        self.assertFalse(provider.public_config()['api_key_configured'])

    def test_decision_contract_errors_name_this_provider(self):
        with self.assertRaises(RuntimeError) as caught:
            decision_response({'action': 'fly', 'targets': {}, 'note': '', 'summary': '',
                               'reason': '', 'hindsight': ''})
        self.assertIn('Claude', str(caught.exception))
        self.assertNotIn('Codex', str(caught.exception))


class ClaudeExecutionTests(unittest.TestCase):
    """The unrecognized-model fallback is silent, so it is checked on real output."""

    def provider(self):
        with patch('remote_yam.claude_policy.claude_status', return_value={'ready': True}), \
             patch('remote_yam.claude_policy.claude_binary', return_value='/bin/claude'):
            provider = ClaudeAdapter(model='claude-opus-5-5')
        self.addCleanup(provider._workspace.cleanup)
        return provider

    def execute(self, stdout, returncode=0):
        provider = self.provider()
        script = ('import sys; sys.stdout.write(%r); sys.exit(%d)' % (stdout, returncode))
        command = [sys.executable, '-c', script]
        return provider._execute(command, '', Path(provider._workspace.name))

    def test_unrecognized_model_is_refused_rather_than_silently_downgraded(self):
        line = '[claude-code:unrecognized_model] {"model":"claude-opus-5-5"}\n' + json.dumps(report())
        with self.assertRaises(RuntimeError) as caught:
            self.execute(line)
        self.assertIn('no motion sent', str(caught.exception))
        self.assertIn('does not recognize the requested model', str(caught.exception))

    def test_a_clean_result_line_is_returned(self):
        self.assertEqual(self.execute(json.dumps(report()))['session_id'], SESSION)

    def test_nonzero_exit_is_categorized_without_leaking_diagnostics(self):
        with self.assertRaises(RuntimeError) as caught:
            self.execute('Claude usage limit reached for account owner@example.com', returncode=1)
        self.assertIn('usage limit reached', str(caught.exception))
        self.assertNotIn('owner@example.com', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
