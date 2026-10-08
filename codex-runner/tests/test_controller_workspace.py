import hashlib
import json
import re
from unittest.mock import patch

import numpy as np
import pytest

from remote_yam.controller import RunnerController
from remote_yam.controller_workspace import PROFILES, PROFILE_ROOT, workspace_for_robot
from remote_yam.mujoco_ik import IKConvergenceError
from remote_yam.providers import PolicyComplete
from remote_yam.robocurve_contract import TOOLS
from remote_yam.robocurve_policy import OpenAIAdapter
from remote_yam.robocurve_prompts import prompt_text
from remote_yam.robocurve_trajectory import HIGH, LOW, NAMES, STEP, InvalidMove, RoboCurveTrajectory
from remote_yam.run_changes import snapshot
from remote_yam.session import MockSessionAPI
from test_robocurve_policy import Cameras, observation, response


EXPECTED = {
    'yam-1': {
        'left': [[.046831991, -.693624194, -.032671465], [.746831991, .406375806, .747328535]],
        'right': [[.046831991, -.393624194, -.032671465], [.746831991, .706375806, .747328535]],
    },
    'robot-ba8413962083809c': {
        'left': [[.146831991, -.493624194, -.012671465], [.746831991, .206375806, .547328535]],
        'right': [[.146831991, -.193624194, -.012671465], [.746831991, .506375806, .547328535]],
    },
}


def provider_for(robot_id, version='with-haste'):
    provider = OpenAIAdapter('test-key', 'gpt-6-astra', camera_source=Cameras())
    provider.set_prompt_version(version)
    provider.configure_robot_workspace(robot_id)
    return provider


def test_binding_preserves_hosted_startup_instructions():
    provider = OpenAIAdapter('test-key', 'gpt-6-astra', camera_source=Cameras())
    provider._system_prompt += '\n\nHosted startup instruction.'
    provider.configure_robot_workspace('yam-1')
    provider.configure_robot_workspace('yam-1')
    assert provider._system_prompt.count('Hosted startup instruction.') == 1
    assert provider._system_prompt.count('Active controller workspace for yam-1') == 1


@pytest.mark.parametrize('robot_id', PROFILES)
def test_saved_sources_and_per_arm_conversion(robot_id):
    profile = workspace_for_robot(robot_id)
    data = (PROFILE_ROOT / (profile.profile + '.safety.json')).read_bytes()
    assert hashlib.sha256(data).hexdigest() == profile.safety_sha256
    for arm in ('left', 'right'):
        np.testing.assert_allclose(profile.bounds(arm), EXPECTED[robot_id][arm], atol=1e-15, rtol=0)
    # Camera/IK world spacing must not be substituted for controller origins.
    geometry = RoboCurveTrajectory(base_spacing_m=.50)
    profile.configure_geometry(geometry)
    np.testing.assert_allclose(geometry.low[1], EXPECTED[robot_id]['left'][0][1], atol=1e-15)
    assert geometry.ik.base_spacing_m == .50


@pytest.mark.parametrize('robot_id', PROFILES)
@pytest.mark.parametrize('version', ['original-robocurve', 'with-haste'])
def test_effective_prompt_tools_enforcement_and_record_share_bounds(robot_id, version):
    provider = provider_for(robot_id, version)
    # Re-selecting a behavioral version must preserve the active workspace.
    provider.set_prompt_version(version)
    with patch.object(provider, '_post_json', return_value=response('done', {'summary': 'Done', 'hindsight': 'none'})) as post:
        with pytest.raises(PolicyComplete):
            provider.build_trajectory('Inspect workspace', observation(), 0)
    payload = post.call_args.args[0]
    prompt = payload['input'][0]['content']
    assert prompt.startswith(prompt_text(version))
    assert 'not an allowed target or guaranteed mechanical reachability' in prompt
    properties = payload['tools'][0]['parameters']['properties']['targets']['properties']
    for name, low, high in zip(NAMES, provider._geometry.low, provider._geometry.high):
        assert properties[name]['minimum'] == low
        assert properties[name]['maximum'] == high
        for text in (prompt, payload['tools'][0]['description']):
            match = re.search(name + r': \[([^,]+), ([^\]]+)\]', text)
            assert match and list(map(float, match.groups())) == [low, high]
    record = snapshot(provider, 'Inspect workspace', 300)
    assert record['prompt_version'] == version
    assert record['system_prompt_sha256'] == hashlib.sha256(prompt.encode()).hexdigest()
    assert record['controller_workspace']['robot_id'] == robot_id
    assert record['controller_workspace']['live_configuration_verified'] is False
    with pytest.raises(ValueError, match='before starting'):
        provider.configure_robot_workspace(robot_id)


@pytest.mark.parametrize('robot_id', PROFILES)
def test_every_xyz_face_accepts_boundary_and_rejects_excess_before_ik(robot_id):
    geometry = provider_for(robot_id)._geometry
    for i in (0, 1, 2, 7, 8, 9):
        for boundary, sign in ((geometry.low[i], -1), (geometry.high[i], 1)):
            with patch.object(geometry.ik, '_solve', side_effect=RuntimeError('reached IK')):
                with pytest.raises(RuntimeError, match='reached IK'):
                    geometry.build({NAMES[i]: float(boundary)}, observation(), 0)
            with patch.object(geometry.ik, '_solve') as solve:
                with pytest.raises(InvalidMove) as caught:
                    geometry.build({NAMES[i]: float(boundary + sign * 1e-6)}, observation(), 0)
                assert caught.value.code == 'target_out_of_bounds'
                solve.assert_not_called()


@pytest.mark.parametrize('robot_id', PROFILES)
def test_forward_above_old_cap_reaches_real_ik_and_keeps_pacing(robot_id):
    geometry = provider_for(robot_id)._geometry
    obs = observation()
    points = geometry.build({'left_x': .50}, obs, 0)
    assert points
    _, end, _ = geometry.observe({**obs, **points[-1]})
    assert end[0] == pytest.approx(.50, abs=3e-6)
    q = np.deg2rad([obs['left_joints_deg'] + obs['right_joints_deg']]
                  + [p['left_joints_deg'] + p['right_joints_deg'] for p in points])
    assert np.max(np.abs(np.diff(q, axis=0))) <= .035 + 1e-12
    with patch.object(geometry.ik, '_solve', side_effect=IKConvergenceError('unreachable')):
        with pytest.raises(InvalidMove) as caught:
            geometry.build({'left_x': .70}, obs, 0)
        assert caught.value.code == 'unreachable_target'


@pytest.mark.parametrize('robot_id', PROFILES)
def test_profile_does_not_change_existing_path_or_pinned_axes(robot_id):
    baseline, configured = RoboCurveTrajectory(), provider_for(robot_id)._geometry
    obs = observation()
    target = {'left_z': .205}
    assert baseline.build(target, obs, 0) == configured.build(target, obs, 0)
    for key in ('left_pitch', 'right_roll'):
        with pytest.raises(InvalidMove) as caught:
            configured.build({key: .01}, obs, 0)
        assert caught.value.code == 'target_out_of_bounds'
    np.testing.assert_array_equal(STEP, np.tile([.04*(.48-.15), .020, .04*(.4-.03), .04*2*np.pi, 0, 0, .1], 2))


@pytest.mark.parametrize('robot_id', PROFILES)
def test_queue_admission_selects_station_before_creating_session(robot_id):
    provider = OpenAIAdapter('test-key', 'gpt-6-astra', camera_source=Cameras())
    api = MockSessionAPI()
    create = api.create_session
    def check_create(*args, **kwargs):
        assert provider.public_config()['controller_workspace']['robot_id'] == robot_id
        return create(*args, **kwargs)
    controller = RunnerController(api, robot_id=robot_id, share_conversation=False)
    with patch.object(api, 'create_session', side_effect=check_create):
        controller.join(provider, 'Inspect workspace')
    assert controller.status()['run_configuration']['controller_workspace']['robot_id'] == robot_id
    controller.stop()


def test_unknown_robot_and_other_instances_keep_default_contract():
    configured = provider_for('yam-1')
    other = provider_for('unknown-yam')
    assert workspace_for_robot('unknown-yam') is None
    np.testing.assert_array_equal(other._geometry.low, LOW)
    np.testing.assert_array_equal(other._geometry.high, HIGH)
    assert other._tools is TOOLS
    assert other._system_prompt == prompt_text('with-haste')
    assert configured._geometry.high[0] > other._geometry.high[0]
    configured.configure_robot_workspace('unknown-yam')
    np.testing.assert_array_equal(configured._geometry.high, HIGH)
    assert configured._tools is TOOLS


def test_profile_hash_mismatch_and_contradictory_report_fail_closed(tmp_path):
    profile = PROFILES['yam-1']
    (tmp_path / (profile.profile + '.safety.json')).write_text('{}')
    with patch('remote_yam.controller_workspace.PROFILE_ROOT', tmp_path):
        with pytest.raises(ValueError, match='hash mismatch'):
            profile.bounds('left')
    with pytest.raises(ValueError, match='robot mismatch'):
        profile.validate_report({'robot_id': 'different-station'})
    hashes = dict(safety=profile.safety_sha256,
                  model='42d5f5b0213513e4bbda4645d4af513147ed492834fdb82ef5658ca3f05de18f',
                  model_include=profile.model_include_sha256)
    report = dict(robot_id='yam-1', provenance=dict(selected_file_sha256=hashes))
    profile.validate_report(report)
    for key in hashes:
        bad = json.loads(json.dumps(report))
        bad['provenance']['selected_file_sha256'][key] = 'unknown-revision'
        with pytest.raises(ValueError, match='does not match'):
            profile.validate_report(bad)
    bad_native = dict(report, native_joint_calibration=dict(calibration_id='other', coordinate_convention='old'))
    with pytest.raises(ValueError, match='native joint convention'):
        profile.validate_report(bad_native)


@pytest.mark.parametrize('robot_id', PROFILES)
def test_chatgpt_subscription_receives_active_workspace_and_tools(robot_id):
    from remote_yam.codex_check import CheckAdapter, check_observation
    from test_codex_policy import decision, events
    with patch('remote_yam.codex_policy.codex_status', return_value={'ready': True}):
        provider = CheckAdapter()
    try:
        provider.configure_robot_workspace(robot_id)
        with patch.object(provider, '_execute', return_value=events(decision())) as execute:
            with pytest.raises(PolicyComplete):
                provider.build_trajectory('Inspect workspace', check_observation(), 0)
        model_prompt = execute.call_args.args[1]
        assert 'Active controller workspace for ' + robot_id in model_prompt
        assert 'Action contract: ' in model_prompt
        assert str(float(provider._geometry.high[0])) in model_prompt
        assert provider.public_config()['authentication'] == 'chatgpt'
    finally:
        provider._workspace.cleanup()


def test_depth_binding_keeps_camera_spacing_and_rejects_a_different_admitted_station():
    from remote_yam.codex_depth_policy import CodexDepthAdapter
    from test_all_camera_depth import fixture
    report, _, _ = fixture()
    report['base_geometry'] = {'spacing_m': .5}
    with patch('remote_yam.codex_policy.codex_status', return_value={'ready': True}):
        provider = CodexDepthAdapter('https://api.example')
    try:
        provider.set_depth_calibration(report)
        provider.configure_robot_workspace('yam-1')
        assert provider._geometry.ik.base_spacing_m == .5
        np.testing.assert_allclose(provider._geometry.low[:3], EXPECTED['yam-1']['left'][0], atol=1e-15)
        api = MockSessionAPI()
        controller = RunnerController(api, robot_id='robot-ba8413962083809c', share_conversation=False)
        with patch.object(api, 'create_session') as create:
            with pytest.raises(ValueError, match='differs from the bound workspace calibration'):
                controller.join(provider, 'Inspect workspace')
            create.assert_not_called()
    finally:
        provider._workspace.cleanup()


def test_other_embodiments_keep_their_geometry_and_tools():
    provider = OpenAIAdapter('test-key', 'gpt-6-astra', camera_source=Cameras())
    other_geometry = object()
    other_tools = [{'name': 'different_robot_tool'}]
    provider._geometry, provider._tools = other_geometry, other_tools
    provider.configure_robot_workspace('yam-1')
    provider.set_prompt_version('original-robocurve')
    assert provider._geometry is other_geometry
    assert provider._tools is other_tools
