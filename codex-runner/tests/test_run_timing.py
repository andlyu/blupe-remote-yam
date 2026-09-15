"""Run timing stays attached to one session while event connections open."""
from unittest.mock import patch
import pytest
from remote_yam.controller import RunnerController
from remote_yam.providers import ScriptedAdapter
from remote_yam.session import MockSessionAPI


@pytest.mark.parametrize('previous_status', ['stopped', 'completed'])
def test_status_poll_during_next_join_cannot_freeze_new_timer(previous_status):
    api = MockSessionAPI(auto_activate=False)
    controller = RunnerController(api)
    controller._status = previous_status
    controller._run_started_at = 10.
    controller._run_ended_at = 20.
    controller._run_duration_s = 300
    create, open_events = api.create_session, api.open_events
    polled = []

    def create_started(prompt, run_duration_s):
        return {**create(prompt, run_duration_s), 'status': 'running', 'run_started_at': 100.}

    def open_with_status_poll(session_id):
        # HTTP polling can happen while the next WebSocket handshake is blocked.
        with patch('remote_yam.controller.time.time', return_value=100.2):
            polled.append(controller.status())
        return open_events(session_id)

    api.create_session = create_started
    api.open_events = open_with_status_poll
    with patch('remote_yam.controller.time.time', return_value=101.):
        state = controller.join(ScriptedAdapter([]), 'next run', run_duration_s=60)
    assert state['run_ended_at'] is None
    assert state['run_elapsed_s'] == 1.
    assert polled[0]['run_elapsed_s'] == 10.
    assert polled[0]['run_duration_s'] == 300
    with patch('remote_yam.controller.time.time', return_value=102.):
        assert controller.status()['run_elapsed_s'] == 2.
    with patch('remote_yam.controller.time.time', return_value=118.):
        stopped = controller.stop()
    assert stopped['run_elapsed_s'] == 18.
    with patch('remote_yam.controller.time.time', return_value=160.):
        assert controller.status()['run_elapsed_s'] == 18.
