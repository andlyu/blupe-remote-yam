import json
from unittest.mock import patch

import pytest

from remote_yam.codex_check import SyntheticObservation, check_observation
from remote_yam.codex_policy import use_codex_executor
from remote_yam.providers import PolicyComplete, ScriptedAdapter
from remote_yam.robocurve_policy import OpenAIAdapter
from test_codex_policy import decision, events


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
