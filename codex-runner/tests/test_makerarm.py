import json
import numpy as np
import pytest
from remote_yam.makerarm_trajectory import MakerArmTrajectory, NAMES
from remote_yam.makerarm_policy import MakerArmCodexAdapter, SYSTEM_PROMPT
from remote_yam.robocurve_trajectory import InvalidMove
from playground import HostedRunner
from multi_robot import fleet

@pytest.fixture
def mapping(tmp_path):
    p=tmp_path/'mapping.json'
    # Synthetic model-coordinate fixture, NOT a physical calibration.
    p.write_text(json.dumps(dict(verified=True,signs=[1]*6,offsets_rad=[0]*6,
        joint_limits_deg=[[-160,160],[-179,-1],[1,179],[-90,90],[-89,89],[-150,150]],
        workspace_m=[[-1,1]]*3)))
    return p

def observation():
    return dict(settled=True,left_joints_deg=[10,-60,80,15,5,20],right_joints_deg=[],left_gripper=.5)

def test_mapping_required(tmp_path):
    p=tmp_path/'invalid.json';p.write_text('{"verified":false}')
    with pytest.raises(RuntimeError):MakerArmTrajectory(p)
    with pytest.raises(RuntimeError):MakerArmTrajectory(None)

def test_fk_ik_pacing(mapping):
    g=MakerArmTrajectory(mapping);q,start,_=g.observe(observation())
    goal=g.forward(q+np.array([.006,0,0,0,0,0]))
    points=g.build(dict(zip(NAMES[:3],map(float,goal))),observation(),0)
    for p in points:
        next_q=np.deg2rad(p['left_joints_deg'])
        assert np.max(np.abs(next_q-q)) <= .01000001
        assert p['right_joints_deg']==[]
        q=next_q
    assert np.linalg.norm(g.forward(q)-goal)<.00005

def test_reject_targets(mapping):
    g=MakerArmTrajectory(mapping)
    for target in ({'x':float('nan')},{'roll':0},{'x':2}):
        with pytest.raises(InvalidMove):g.build(target,observation(),0)
    with pytest.raises(InvalidMove):g.solve(np.deg2rad(observation()['left_joints_deg']),np.array([5.,5.,5.]))

def test_nontrivial_mapping(mapping):
    g=MakerArmTrajectory(mapping)
    q=np.deg2rad(observation()['left_joints_deg'])
    expected=g.forward(q)
    data=json.loads(mapping.read_text());data.update(signs=[-1]*6,offsets_rad=[.1]*6,joint_limits_deg=[[-360,360]]*6)
    mapping.write_text(json.dumps(data));g2=MakerArmTrajectory(mapping)
    assert np.allclose(g2.forward(.1-q),expected)

def test_robot_routes_by_hardware():
    app=fleet(HostedRunner,dict(public_origin='https://test.example',session_api='https://api.example',camera_origin='https://api.example'),[
        dict(id='yam-1',name='YAM'),dict(id='test-maker',name='MakerMods MakerArm',hardware='makerarm',joint_counts=[6,0],cameras=['front','wrist'])])
    assert app.apps['test-maker'].hardware=='makerarm'
    assert app.apps['test-maker'].joint_counts==(6,0)
    assert app.apps['test-maker'].camera_names==('front','wrist')
    assert app.apps['yam-1'].hardware=='yam'

def test_prompt_and_codex(mapping):
    adapter=MakerArmCodexAdapter.__new__(MakerArmCodexAdapter)
    adapter._mapping_path=mapping;adapter._maker_cameras=('front',)
    adapter._initialize_policy(None)
    assert tuple(adapter._decision_schema['properties']['targets']['required'])==NAMES
    assert 'Y-up' in adapter._system_prompt
    assert 'Orientation is unconstrained' in SYSTEM_PROMPT

def test_fk_matches_independent_mujoco_model(mapping):
    import mujoco
    import xml.etree.ElementTree as ET
    from pathlib import Path
    from remote_yam.makerarm_trajectory import JOINTS
    root=ET.parse(Path(__file__).resolve().parents[1]/'models/makerarm/robot.urdf').getroot()
    for link in root.findall('link'):
        for kind in ('visual','collision'):
            for node in link.findall(kind):link.remove(node)
    ET.SubElement(ET.SubElement(root,'mujoco'),'compiler',fusestatic='false',discardvisual='true')
    model=mujoco.MjModel.from_xml_string(ET.tostring(root,encoding='unicode'))
    data=mujoco.MjData(model)
    geometry=MakerArmTrajectory(mapping)
    for q in [np.zeros(6),np.deg2rad(observation()['left_joints_deg'])]:
        for name,v in zip(JOINTS,q):data.qpos[model.jnt_qposadr[model.joint(name).id]]=v
        mujoco.mj_forward(model,data)
        assert np.linalg.norm(geometry.forward(q)-data.body('grasp_center').xpos)<1e-8

@pytest.mark.parametrize('provider', ['claude', 'anthropic'])
def test_opus_retains_makerarm_mapping_and_contract(mapping, provider):
    from unittest.mock import patch
    from remote_yam.makerarm_policy import MakerArmClaudeAdapter, MakerArmAnthropicAdapter
    with patch('remote_yam.claude_policy.claude_status', return_value={'ready':True}), patch('remote_yam.claude_policy.claude_binary', return_value='/unused'):
        p = (MakerArmClaudeAdapter(calibration_path=mapping, camera_names=('front','wrist')) if provider=='claude'
             else MakerArmAnthropicAdapter('test', calibration_path=mapping, camera_names=('front','wrist')))
    try:
        assert p._camera_names == ('front','wrist')
        assert p._expected_camera_count == 2
        assert 'Y-up' in p._system_prompt
        assert set(p._decision_schema['properties']['targets']['required']) == set(NAMES)
        assert p._geometry.build({'gripper':.52}, observation(), 0)[-1]['left_gripper'] == .52
        with pytest.raises(InvalidMove):p._geometry.build({'yaw':1}, observation(), 0)
    finally:
        if hasattr(p, '_workspace'):p._workspace.cleanup()
