import json
from unittest.mock import patch

import pytest

from remote_yam.codex_check import SyntheticObservation, check_observation
from remote_yam.codex_policy import use_codex_executor
from remote_yam.controller import RunnerController
from remote_yam.providers import PolicyComplete, ScriptedAdapter
from remote_yam.robocurve_policy import OpenAIAdapter
from remote_yam.session import MockSessionAPI
from test_codex_policy import decision, events
from test_robocurve_policy import Cameras, observation


class RemoteSynthetic(SyntheticObservation, OpenAIAdapter):
    pass


def test_executor_keeps_geometry_and_cannot_call_api_or_read_local_auth():
    provider = RemoteSynthetic('placeholder', 'gpt-6-astra')
    geometry = provider._geometry
    captured = []
    def execute(**values):
        captured.append(values)
        return events(decision())
    with patch('remote_yam.codex_policy.codex_status', side_effect=AssertionError('Local auth must stay private')):
        use_codex_executor(provider, execute)
    try:
        with patch.object(provider, '_send_json', side_effect=AssertionError('API billing attempted')):
            with pytest.raises(PolicyComplete):
                provider.build_trajectory('No motion; finish transport check', check_observation(), 0)
        assert provider._geometry is geometry
        assert len(captured) == 1 and len(captured[0]['images']) == 3
        assert captured[0]['thread_id'] is None
        assert captured[0]['model'] == 'gpt-6-astra'
        assert provider._api_key == ''
        assert provider.public_config()['funding'] == 'sponsored_codex_subscription'
        assert provider.public_config()['api_key_configured'] is False
    finally:
        provider.close_transport()


@pytest.mark.parametrize('result', [[], events(decision())[:-1], events(decision(targets={}))])
def test_partial_and_invalid_remote_decisions_fail_without_api_fallback(result):
    provider = RemoteSynthetic('placeholder', 'gpt-6-astra')
    use_codex_executor(provider, lambda **kw: result)
    try:
        with patch.object(provider, '_send_json', side_effect=AssertionError('API billing attempted')):
            with pytest.raises(RuntimeError):
                provider.build_trajectory('No motion', check_observation(), 0)
        assert provider._pending is None
    finally:
        provider.close_transport()


def test_unsupported_policy_rejected_before_transport_is_attached():
    with pytest.raises(ValueError, match='structured Codex'):
        use_codex_executor(ScriptedAdapter([]), lambda **kw: [])


@pytest.mark.parametrize('settled', [True, False])
def test_subscription_run_dances_before_first_model_call_and_gates_policy_motion(monkeypatch, settled):
    """Regression: changing hosted transports must retain the first-call hook."""
    trace = []

    class Gateway(MockSessionAPI):
        def _capability(self, session_id):
            return 'private-test-capability'

        def _request(self, method, path, payload=None, **kwargs):
            assert method == 'POST'
            assert kwargs['bearer'] == 'private-test-capability'
            session_id = path.split('/')[3]
            active = self._sessions[session_id]
            assert payload['executor'] == 'yam_first_call'
            assert payload['episode_id'] == active['episode_id']
            assert payload['lease_id'] == active['lease_id']
            operation = payload['operation']
            trace.append(operation)
            return {'status': 'completed', 'result': {
                'state': 'planning' if operation == 'start' else 'returned',
                'settled': operation == 'finish' and settled,
            }}

        def submit_trajectory(self, *args, **kwargs):
            assert trace == ['start', 'model', 'finish']
            trace.append('policy_motion')
            return super().submit_trajectory(*args, **kwargs)

    gateway = Gateway(observations=[observation()])
    controller = RunnerController(gateway, share_conversation=False)
    provider = OpenAIAdapter('placeholder', 'gpt-6-astra', camera_source=Cameras())
    calls = []

    def execute(**values):
        calls.append(values)
        if len(calls) == 1:
            assert trace == ['start']
            assert provider._camera_source.calls == 1
            trace.append('model')
            move = decision('move_to', note='Close the left gripper slightly.')
            move['targets']['left_gripper'] = .8
            return events(move)
        assert trace == ['start', 'model', 'finish', 'policy_motion']
        trace.append('second_model')
        return events(decision())

    monkeypatch.setenv('YAM_FIRST_CALL_WANDER_ROBOTS', 'yam-1')
    use_codex_executor(provider, execute)
    try:
        state = controller.join(provider, 'Pick')
        assert state['first_call_wander_enabled'] is True
        assert state['provider']['funding'] == 'sponsored_codex_subscription'
        with patch.object(provider, '_send_json', side_effect=AssertionError('API billing attempted')):
            if settled:
                for _ in range(30):
                    if len(calls) == 2:
                        break
                    controller.process_next_event(.01)
                assert trace == ['start', 'model', 'finish', 'policy_motion', 'second_model']
                assert len(gateway.trajectory_log) == 1
            else:
                with pytest.raises(RuntimeError, match='return not verified'):
                    for _ in range(15):
                        controller.process_next_event(.01)
                assert trace == ['start', 'model', 'finish']
                assert gateway.trajectory_attempts == []
    finally:
        controller.disconnect()
        provider.close_transport()
