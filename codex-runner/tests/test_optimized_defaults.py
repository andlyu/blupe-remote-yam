"""Check real provider wire requests and hosted launch defaults without motion."""
import io
import json
import unittest
from unittest.mock import patch

import pytest

from remote_yam.providers import PolicyComplete
from remote_yam.robocurve_policy import OpenAIAdapter
from remote_yam.run_changes import snapshot
from test_robocurve_policy import Cameras, observation, response
import test_codex_policy as codex_tests
from test_codex_policy import decision, events
import test_claude_policy as claude_tests
from test_claude_policy import report
from test_anthropic_policy import payload
from remote_yam.anthropic_policy import AnthropicAdapter
import test_hosted as hosted_tests


@pytest.mark.parametrize('speed, tier', [(None, 'priority'), ('standard', 'default')])
def test_openai_default_and_override_reach_request(speed, tier):
    adapter = OpenAIAdapter('test-key', 'gpt-6-astra', camera_source=Cameras())
    if speed:
        adapter.response_speed = speed
    result = {**response('done', {'summary': 'Done', 'hindsight': 'none'}), 'service_tier': tier}
    with patch.object(adapter, '_post_json', return_value=result) as post:
        with pytest.raises(PolicyComplete):
            adapter.build_trajectory('Place block', observation(), 0)
    wire = post.call_args.args[0]
    assert wire['service_tier'] == tier
    config = snapshot(adapter, 'Place block', 300)
    assert config['prompt_version'] == 'with-haste'
    assert config['system_prompt_sha256'] == '724dba416a8a722f5a52dfe5f125a34e8eed5c4ebb4d7e5e74aed5a56559d559'
    assert config['trajectory_speed'] == 4.0
    assert config['response_speed'] == (speed or 'fast')
    assert config['actual_response_speed'] == (speed or 'fast')


@pytest.mark.parametrize('speed', ['fast', 'standard'])
def test_anthropic_speed_on_wire_and_reported(speed):
    adapter = AnthropicAdapter('test-key')
    assert adapter.response_speed == 'fast'
    adapter.response_speed = speed
    calls = []
    def send(req, timeout):
        calls.append(req)
        return io.BytesIO(json.dumps({'stop_reason': 'tool_use', 'usage': {'speed': speed},
            'content': [{'type': 'tool_use', 'id': 't1', 'name': 'done', 'input': {'summary': 'done'}}]}).encode())
    adapter._urlopen = send
    adapter._post_json(payload())
    wire = json.loads(calls[0].data)
    assert wire.get('speed', 'standard') == speed
    assert bool(calls[0].get_header('Anthropic-beta')) == (speed == 'fast')
    assert adapter.public_config()['actual_response_speed'] == speed


class FastCodexTests(unittest.TestCase):
    provider = codex_tests.CodexPolicyTests.provider

    def test_speed_override_preserves_effort_and_resume(self):
        from remote_yam.codex_check import check_observation
        for speed in ('fast', 'standard'):
            provider = self.provider()
            self.assertEqual(provider.response_speed, 'fast')
            provider.response_speed = speed
            provider.reasoning_effort = 'high'
            with patch.object(provider, '_execute', return_value=events(decision())) as execute:
                with self.assertRaises(PolicyComplete):
                    provider.build_trajectory('test', check_observation(), 0)
            command = execute.call_args.args[0]
            self.assertIn('service_tier="' + ('fast' if speed == 'fast' else 'default') + '"', command)
            self.assertIn('model_reasoning_effort="high"', command)


class FastClaudeTests(unittest.TestCase):
    provider = claude_tests.ClaudePolicyTests.provider
    run_once = claude_tests.ClaudePolicyTests.run_once

    def test_speed_default_and_override_do_not_change_effort(self):
        for speed in ('fast', 'standard'):
            provider = self.provider()
            self.assertEqual(provider.response_speed, 'fast')
            provider.response_speed = speed
            provider.reasoning_effort = 'high'
            result = report()
            result['usage'] = {'speed': speed}
            _, seen = self.run_once(provider, result)
            command = seen['command']
            self.assertEqual(json.loads(command[command.index('--settings')+1]), {'fastMode': speed == 'fast'})
            self.assertEqual(command[command.index('--effort')+1], 'high')
            self.assertEqual(provider.actual_response_speed, speed)


class FastHostedTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = hosted_tests.HostedTests.asyncSetUp
    asyncTearDown = hosted_tests.HostedTests.asyncTearDown

    async def test_omitted_speed_defaults_fast_and_explicit_standard_survives(self):
        for paid in (False, True):
            for speed in (None, 'standard'):
                _, visitor = self.app.new_visitor()
                data = dict(provider='openai', api_key='test-key', model='gpt-6-astra', prompt='Place block')
                if speed:
                    data['response_speed'] = speed
                self.app.launch(visitor, data, paid=paid)
                self.assertEqual(self.providers[-1].response_speed, speed or 'fast')
                visitor.controller.stop()

    async def test_invalid_speed_rejected_before_queue(self):
        from playground import RequestError
        _, visitor = self.app.new_visitor()
        with self.assertRaises(RequestError):
            self.app.launch(visitor, dict(provider='openai', api_key='test-key', model='gpt-6-astra',
                                         prompt='Place block', response_speed='turbo'))
        self.assertEqual(self.providers, [])
