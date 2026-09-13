import copy
import json
from unittest.mock import Mock
import pytest
from remote_yam.controller import RunnerController
from remote_yam.robocurve_policy import OpenAIAdapter
from remote_yam.session import MockSessionAPI


def setup():
    session = MockSessionAPI()
    controller = RunnerController(session)
    provider = OpenAIAdapter('test', 'astra')
    provider._pending = {'call_id': 'move', 'waypoint_count': 136, 'last_step_id': 1087}
    controller._provider = provider
    controller._status = 'running'
    target = {'left_joints_deg': [0.] * 6,
              'right_joints_deg': [71.15, 75.46, 42.21, -26.99, 52.80, 54.65]}
    obs = dict(copy.deepcopy(target), step_id=1088, settled=True,
               left_gripper=.38, right_gripper=.12)
    obs['right_joints_deg'][4] = 49.80
    controller._trajectory = dict(trajectory_id='traj', state='accepted', progress_count=136,
        waypoint_count=136, last_step_id=1087, final_target=target,
        final_observation_received=False, final_payload=obs)
    return controller, provider, obs


def test_recorded_three_degree_miss_returns_failed_tool_and_same_run_continues():
    c, p, obs = setup()
    c._trajectory_final_observation(p, obs, 1088)
    c._observation = Mock()
    c._trajectory_result(dict(trajectory_id='traj', status='completed', reported_at=1., step_id=1087))
    result = json.loads(p._history[-1]['output'])
    assert result['ok'] is False and result['code'] == 'final_position_mismatch'
    joint = result['feedback']['joints'][0]
    assert joint['joint'] == 'right_joint_4' and joint['error_deg'] == 3.
    assert result['feedback']['measured']['right_joints_deg'][4] == 49.8
    assert p._pending is None and c._trajectory is None and c._status == 'running'
    c._observation.assert_called_once_with({}, obs)


@pytest.mark.parametrize('invalid', ['nonfinite', 'unsettled', 'missing', 'incomplete'])
def test_invalid_or_incomplete_feedback_cannot_resume(invalid):
    c, p, obs = setup()
    if invalid == 'nonfinite': obs['right_joints_deg'][4] = float('nan')
    if invalid == 'unsettled': obs['settled'] = False
    if invalid == 'missing': obs.pop('right_joints_deg')
    if invalid == 'incomplete': c._trajectory['progress_count'] = 135
    with pytest.raises(RuntimeError): c._trajectory_final_observation(p, obs, 1088)
    assert p._pending is not None and not c._trajectory['final_observation_received']
