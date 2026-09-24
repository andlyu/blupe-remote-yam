import json
from unittest.mock import patch
import numpy as np
import pytest
from remote_yam.so101_trajectory import SO101Trajectory, NAMES
from remote_yam.so101_policy import SO101OpenAIAdapter, SO101CodexAdapter, SYSTEM_PROMPT
from remote_yam.robocurve_trajectory import InvalidMove
from remote_yam.cameras import CameraFrame

def obs():
    return {'left_joints_deg': [-3.,-42.,17.,90.,-68.], 'right_joints_deg': [],
            'left_gripper': .1, 'right_gripper': None, 'settled': True,
            'episode_id': 'test', 'lease_id': 'test', 'step_id': 0}
class Camera:
    camera_names = ('front',)
    def capture(self, observation):
        return [CameraFrame('front', b'\xff\xd8test\xff\xd9', 123.)]

def test_fk_ik_and_pacing():
    g=SO101Trajectory(); q,state,_=g.observe(obs())
    target=state[:3]+[0.,.004,.002]
    points=g.build(dict(zip(NAMES[:3],map(float,target))),obs(),0)
    previous=q
    for p in points:
        current=np.deg2rad(p['left_joints_deg'])
        assert np.max(np.abs(current-previous))<=.01+1e-12
        assert np.all(current>=g.limits[:,0]) and np.all(current<=g.limits[:,1])
        assert p['right_joints_deg']==[] and p['right_gripper'] is None
        previous=current
    assert np.linalg.norm(g.forward(previous)-target)<.00005
    assert abs(previous[4]-q[4])<1e-12

def test_roll_preserves_position():
    g=SO101Trajectory(); q,state,_=g.observe(obs())
    points=g.build({'wrist_roll':float(q[4]+.02)},obs(),0)
    end=np.deg2rad(points[-1]['left_joints_deg'])
    assert abs(end[4]-q[4]-.02)<1e-10
    assert np.linalg.norm(g.forward(end)-state[:3])<.00005

@pytest.mark.parametrize('target',[{'left_x':.2},{'x':float('nan')},{'x':True},{'gripper':1.1},{'yaw':0}])
def test_invalid_targets(target):
    with pytest.raises(InvalidMove): SO101Trajectory().build(target,obs(),0)

def test_unreachable_and_budget():
    g=SO101Trajectory()
    with pytest.raises(InvalidMove): g.solve(np.deg2rad(obs()['left_joints_deg']),np.array([1.,1.,1.]),0.)
    from remote_yam.session import MAX_COMMANDS
    with pytest.raises(InvalidMove): g.build({'gripper':.11},obs(),MAX_COMMANDS)

def test_model_limit_mismatch():
    o=obs();o['left_joints_deg'][3]=96.
    with pytest.raises(RuntimeError,match='calibration/model'): SO101Trajectory().observe(o)

def test_prompt_camera_and_tool_loop():
    p=SO101OpenAIAdapter('test','test',camera_source=Camera())
    assert 'Two identical' not in SYSTEM_PROMPT and 'One SO101' in SYSTEM_PROMPT
    def reply(payload):
        assert payload['input'][0]['content']==SYSTEM_PROMPT
        assert len([c for c in payload['input'][-1]['content'] if c['type']=='input_image'])==1
        assert set(payload['tools'][0]['parameters']['properties']['targets']['properties'])==set(NAMES)
        return {'output':[{'type':'function_call','call_id':'move1','name':'move_to',
            'arguments':json.dumps({'targets':{'gripper':.12},'note':'Open slightly.'})}]}
    p._post_json=reply
    points=p.build_trajectory('Test',obs(),0)
    assert points[-1]['left_gripper']==.12
    p.trajectory_completed({**obs(),**points[-1],'step_id':len(points)},len(points))
    assert p._pending is None

def test_codex_contract():
    with patch('remote_yam.codex_policy.codex_status',return_value={'ready':True}),patch('remote_yam.codex_policy.codex_binary',return_value='/unused'):
        p=SO101CodexAdapter(camera_source=Camera())
    assert p._expected_camera_count==1
    assert set(p._decision_schema['properties']['targets']['required'])==set(NAMES)
    value={'action':'move_to','targets':{n:None for n in NAMES},'note':'Open','summary':'','reason':'','hindsight':''}
    value['targets']['gripper']=.2
    result=p._decision_response(value)
    assert json.loads(result['output'][0]['arguments'])['targets']=={'gripper':.2}
    p._workspace.cleanup()

def test_calibrated_limits_accept_saved_home_and_match_tools(tmp_path):
    from remote_yam.so101_trajectory import JOINTS
    ranges=[(663,3426),(775,3190),(886,3137),(817,3276),(0,4095)]
    path=tmp_path/'calibration.json'
    path.write_text(json.dumps({name:{'id':i+1,'range_min':lo,'range_max':hi}
        for i,(name,(lo,hi)) in enumerate(zip(JOINTS,ranges))}))
    g=SO101Trajectory(path)
    home={**obs(),'left_joints_deg':[-3.6483516483516483,-41.97802197802198,16.923076923076923,95.51648351648352,-68.35164835164835]}
    g.observe(home)
    assert np.rad2deg(g.limits[3,1])==pytest.approx(108.08791208791209)
    points=g.build({'gripper':.11},home,0)
    assert points[-1]['left_joints_deg']==pytest.approx(home['left_joints_deg'])
    p=SO101OpenAIAdapter('test','test',camera_source=Camera(),calibration_path=path)
    tool=p._tools[0]['parameters']['properties']['targets']['properties']['wrist_roll']
    assert tool['minimum']==pytest.approx(-np.pi) and tool['maximum']==pytest.approx(np.pi)
    bad={**home,'left_joints_deg':home['left_joints_deg'].copy()};bad['left_joints_deg'][3]=109.
    with pytest.raises(RuntimeError):g.observe(bad)
    path.write_text('{}')
    with pytest.raises(RuntimeError):SO101Trajectory(path)

def test_so101_packet_passes_http_client_boundary():
    from remote_yam.session import HttpSessionAPI
    g=SO101Trajectory(); points=g.build({'gripper':.12},obs(),0)
    api=HttpSessionAPI('https://example.com',joint_counts=(5,0))
    captured=[]
    api._capability=lambda session:'test'
    api._request=lambda *args,**kw:captured.append(args) or {'accepted':True}
    api.submit_trajectory('s','e','l','t',10,points)
    assert captured[0][2]['waypoints']==[{k:v for k,v in point.items() if v is not None} for point in points]
    bad=[{**points[0],'left_joints_deg':[0]*6}]
    with pytest.raises(ValueError,match='exactly 5'):api.submit_trajectory('s','e','l','t2',10,bad)
    assert len(captured)==1

def test_bundled_robot_limits_allow_ik_without_calibration_file():
    from remote_yam.so101_trajectory import load_joint_profile
    profile=load_joint_profile('robot-abecb4cd868ab24b')
    assert load_joint_profile('some-other-so101') is None
    g=SO101Trajectory(joint_profile=profile)
    home={**obs(),'left_joints_deg':[-3.6483516483516483,-41.97802197802198,16.923076923076923,95.51648351648352,-68.35164835164835]}
    q,start,_=g.observe(home)
    points=g.build({'z':float(start[2]+.001)},home,0)
    assert np.linalg.norm(g.forward(np.deg2rad(points[-1]['left_joints_deg']))-(start[:3]+[0,0,.001]))<.00005
    assert g.calibration_path is None
    assert np.rad2deg(g.limits[3,1])==pytest.approx(108.08791208791209)
    p=SO101OpenAIAdapter('test','test',camera_source=Camera(),joint_profile=profile)
    roll=p._tools[0]['parameters']['properties']['targets']['properties']['wrist_roll']
    assert roll['minimum']==pytest.approx(-np.pi) and roll['maximum']==pytest.approx(np.pi)
    bad={**home,'left_joints_deg':[0,0,0,109,0]}
    with pytest.raises(RuntimeError):g.observe(bad)

@pytest.mark.parametrize('change',[
    {'convention':'raw_encoder_ticks'}, {'joint_names':['wrong']*5},
    {'joint_limits_deg':[[0,0]]*5}, {'joint_limits_deg':[[False,1]]*5},
    {'joint_limits_deg':[[float('nan'),1]]*5}, {'joint_limits_deg':[[-181,181]]*5},
])
def test_invalid_joint_profile_is_rejected(change):
    from remote_yam.so101_trajectory import load_joint_profile
    profile=load_joint_profile('robot-abecb4cd868ab24b')
    with pytest.raises(RuntimeError):SO101Trajectory(joint_profile={**profile,**change})

@pytest.mark.parametrize('provider_name',['openai','codex'])
def test_hosted_so101_uses_bundled_limits_without_local_calibration(tmp_path,provider_name):
    from playground import HostedRunner
    from remote_yam.session import MockSessionAPI
    from unittest.mock import Mock
    app=HostedRunner(public_origin='http://127.0.0.1:8791',session_api='https://api.example',
        camera_origin='https://camera.example',robot_id='robot-abecb4cd868ab24b',
        joint_counts=(5,0),camera_names=('front',),local_codex=True,development=True,
        api_factory=lambda:MockSessionAPI(auto_activate=False))
    _,visitor=app.new_visitor()
    app.remember_runner=Mock()
    visitor.controller.join_and_run=Mock()
    try:
        with patch('playground.ROOT',tmp_path), patch('remote_yam.codex_policy.codex_status',return_value={'ready':True}):
            app.launch(visitor,{'provider':provider_name,'api_key':'test-key-123','prompt':'test','runner_name':'test'})
        provider=visitor.controller.join_and_run.call_args.args[0]
        assert isinstance(provider,SO101CodexAdapter if provider_name=='codex' else SO101OpenAIAdapter)
        assert provider._geometry.calibration_path is None
        assert np.rad2deg(provider._geometry.limits[3,1])==pytest.approx(108.08791208791209)
    finally:
        visitor.close()
        import shutil
        shutil.rmtree(app.root,ignore_errors=True)


def test_so101_workspace_allows_minus_three_centimeters():
    geometry = SO101Trajectory()
    assert geometry.low[2] == -.03
    schema = SO101OpenAIAdapter('test', 'test', camera_source=Camera())._tools[0]['parameters']['properties']['targets']['properties']
    assert schema['z']['minimum'] == -.03
