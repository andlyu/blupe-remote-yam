from unittest.mock import patch
import pytest
from remote_yam.robocurve_policy import OpenAIAdapter
from remote_yam.providers import PolicyComplete
from remote_yam.past_runs import RunNames
from test_robocurve_policy import Cameras, observation, response


def test_model_request_duration_excludes_local_work_and_sums_retries():
    p = OpenAIAdapter('test', 'astra', camera_source=Cameras())
    replies = [response('move_to', {'targets': {'left_x':99}, 'note':'invalid'}),
               response('give_up', {'reason':'done', 'hindsight':'none'}, 'c2')]
    with patch.object(p, '_post_json', side_effect=replies), patch('remote_yam.robocurve_policy.time.monotonic', side_effect=[100, 106, 110, 113]):
        with pytest.raises(PolicyComplete):
            p.build_trajectory('test', observation(), 0)
    assert p.last_step_model_s == 9


def test_history_persists_only_numeric_metrics(tmp_path):
    path = tmp_path/'history.db'
    rows = [dict(step=1,model_s=3,arm_motion_s=2,left_displacement_m=.05,right_displacement_m=None,secret='PRIVATE')]
    RunNames(path).remember_result('ep_test', {'error':'failed', 'step_timings':rows})
    saved = RunNames(path).results()['ep_test']['step_timings']
    assert saved == [dict(step=1,model_s=3,arm_motion_s=2,left_displacement_m=.05)]
    RunNames(path).remember_result('ep_test', {'error':'failed'})
    assert RunNames(path).results()['ep_test']['step_timings'] == saved
