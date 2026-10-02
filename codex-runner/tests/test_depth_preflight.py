import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from remote_yam.api_depth import NoDepthImage, depth_robot_stopped, require_depth_age
from remote_yam.api_depth_set import ApiDepthSet
from remote_yam.depth_preflight import capture_before_queue, preflight_stopped


def station(**changes):
    return dict(jetson_id='yam-1', source='hardware', mode='DISABLED',
                observed_at=100., settled=False, **changes)


def test_disabled_gateway_allows_five_seconds_only_for_preflight():
    observation = station()
    with patch('remote_yam.depth_preflight.time.time', return_value=100.):
        assert preflight_stopped(observation)
    assert not depth_robot_stopped(observation)
    def capture(*, stopped, cancelled):
        require_depth_age(3., stopped=stopped)
        assert not cancelled()
        return 'paired images'
    with patch('remote_yam.depth_preflight.time.time', return_value=100.):
        assert capture_before_queue(capture, lambda: observation) == 'paired images'


@pytest.mark.parametrize('changes', [
    {'source': 'simulation'}, {'jetson_id': 'other'}, {'observed_at': 97.},
    {'observed_at': 103.}, {'observed_at': None}, {'observed_at': float('nan')},
    {'observed_at': True}, {'mode': 'EXECUTING', 'settled': True},
    {'mode': 'HOMING', 'settled': True}, {'mode': 'FAULT', 'settled': True},
    {'mode': 'API_ACTIVE', 'settled': False},
])
def test_preflight_does_not_assume_stationary_from_invalid_or_moving_feedback(changes):
    with patch('remote_yam.depth_preflight.time.time', return_value=100.):
        assert not preflight_stopped({**station(), **changes})


def test_missing_image_requeries_station_and_recovers_before_queueing():
    read = MagicMock(side_effect=[{**station(), 'mode': 'EXECUTING'}, station(), station()])
    attempts = []
    def capture(*, stopped, cancelled):
        attempts.append(stopped)
        if len(attempts) < 3:
            raise NoDepthImage('left')
        require_depth_age(3., stopped=stopped)
        return 'ready'
    with patch('remote_yam.depth_preflight.time.time', return_value=100.), \
            patch('remote_yam.depth_preflight.time.sleep'):
        assert capture_before_queue(capture, read) == 'ready'
    assert attempts == [False, True, True]
    assert read.call_count == 3


def test_missing_image_startup_is_bounded_and_cancel_does_not_read():
    clock = [0.]
    capture = MagicMock(side_effect=NoDepthImage())
    with patch('remote_yam.depth_preflight.time.monotonic', side_effect=lambda: clock[0]), \
            patch('remote_yam.depth_preflight.time.time', return_value=100.), \
            patch('remote_yam.depth_preflight.time.sleep', side_effect=lambda _: clock.__setitem__(0, clock[0]+1)):
        with pytest.raises(NoDepthImage):
            capture_before_queue(capture, station, recovery_s=2.)
    assert capture.call_count == 2
    read = MagicMock()
    with pytest.raises(RuntimeError, match='cancelled'):
        capture_before_queue(capture, read, cancelled=lambda: True)
    read.assert_not_called()


def test_calibration_errors_are_not_retried():
    capture = MagicMock(side_effect=ValueError('Calibration mismatch'))
    with patch('remote_yam.depth_preflight.time.time', return_value=100.):
        with pytest.raises(ValueError, match='Calibration mismatch'):
            capture_before_queue(capture, station)
    capture.assert_called_once()


def test_all_three_depths_aged_three_seconds_pass_disabled_startup():
    from test_all_camera_depth import fixture
    source = ApiDepthSet('https://api.example', 'https://api.example')
    report, _, _ = fixture()
    for role, api in source.apis.items():
        _, _, data = fixture(role)
        api.clock = lambda: 103.
        api.read = MagicMock(side_effect=lambda path, limit=None, data=data:
            json.dumps(report).encode() if path == '/calibration' else data)
    with patch('remote_yam.depth_preflight.time.time', return_value=103.):
        frames = capture_before_queue(source.capture_preflight,
            lambda: {**station(), 'observed_at': 103.})
    assert len(frames) == 3
    assert set(source.snapshots) == {'top', 'left', 'right'}


def test_standard_local_factory_reads_measured_feedback_without_queueing(tmp_path):
    from local_playground import LocalPlayground
    app = LocalPlayground.__new__(LocalPlayground)
    app.monitor_api = MagicMock()
    app.monitor_api.get_robot_observation.return_value = station()
    provider = MagicMock()
    provider.cancelled.return_value = False
    provider._camera_source.snapshots = {'top': SimpleNamespace(calibration={'calibration_id': 'test'})}
    with patch('remote_yam.codex_depth_policy.CodexDepthAdapter', return_value=provider), \
            patch('remote_yam.depth_preflight.time.time', return_value=100.):
        assert app.api_depth_provider('https://api.example')('codex', '', '', tmp_path) is provider
    assert provider._camera_source.capture_preflight.call_args.kwargs['stopped'] is True
    app.monitor_api.get_robot_observation.assert_called_once_with('yam-1')
    assert [call[0] for call in app.monitor_api.method_calls] == ['get_robot_observation']
