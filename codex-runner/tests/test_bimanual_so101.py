import json
from unittest.mock import patch
import numpy as np
import pytest
from remote_yam.bimanual_so101_trajectory import BimanualSO101Trajectory, NAMES
from remote_yam.bimanual_so101_policy import BimanualSO101OpenAIAdapter, BimanualSO101CodexAdapter
from remote_yam.so101_trajectory import JOINTS
from remote_yam.robocurve_trajectory import InvalidMove
from remote_yam.cameras import CameraFrame

@pytest.fixture
def manifest(tmp_path):
    for side in ('left','right'):
        (tmp_path/(side+'.json')).write_text(json.dumps({n:{'id':i+1,'range_min':100,'range_max':3995} for i,n in enumerate(JOINTS)}))
    p=tmp_path/'pair.json';p.write_text(json.dumps({'left':'left.json','right':'right.json'}));return p

def obs():
    return dict(left_joints_deg=[-3.,-42.,17.,90.,-68.],right_joints_deg=[3.,-40.,20.,85.,-60.],
        left_gripper=.1,right_gripper=.3,settled=True,episode_id='test',lease_id='test',step_id=0)

@pytest.mark.parametrize('side',['left','right'])
def test_single_arm_move_holds_other_arm_exactly(manifest,side):
    g=BimanualSO101Trajectory(manifest);o=obs();other='right' if side=='left' else 'left'
    q,start,_=g.arms[side].observe(g.arm_observation(o,side));target=start[:3]+[0,.003,.001]
    points=g.build({side+'_'+n:float(v) for n,v in zip(('x','y','z'),target)},o,0)
    previous=q
    for p in points:
        assert p[other+'_joints_deg']==o[other+'_joints_deg']
        assert p[other+'_gripper']==o[other+'_gripper']
        current=np.deg2rad(p[side+'_joints_deg']);assert np.max(np.abs(current-previous))<=.01+1e-12;previous=current
    assert np.linalg.norm(g.arms[side].forward(previous)-target)<.00005


def test_two_grippers_packet_and_api(manifest):
    from remote_yam.session import HttpSessionAPI
    points=BimanualSO101Trajectory(manifest).build({'left_gripper':.12,'right_gripper':.35},obs(),4)
    assert points[-1]['left_gripper']==.12 and points[-1]['right_gripper']==.35
    assert [p['step_id'] for p in points]==list(range(4,4+len(points)))
    api=HttpSessionAPI('https://example.com',joint_counts=(5,5));api._capability=lambda _: 'test'
    sent=[];api._request=lambda *a,**k:sent.append(a) or {'accepted':True}
    api.submit_trajectory('s','e','l','t',10,points);assert sent


def test_bad_right_rejects_entire_packet(manifest):
    g=BimanualSO101Trajectory(manifest)
    with pytest.raises(InvalidMove): g.build({'left_gripper':.2,'right_x':5},obs(),0)
    with pytest.raises(RuntimeError): g.observe({**obs(),'right_joints_deg':[]})
    with pytest.raises(InvalidMove): g.build({'yaw':0},obs(),0)

class Cameras:
    camera_names=('overhead','side','left','right')
    def capture(self, observation):return [CameraFrame(n,b'\xff\xd8test\xff\xd9',123.) for n in self.camera_names]


def test_openai_prompt_tools_images(manifest):
    source=Cameras()
    p=BimanualSO101OpenAIAdapter('test','test',calibration_path=manifest,camera_source=source, camera_names=source.camera_names)
    assert source.camera_names == ('overhead','side','left','right')
    assert p._camera_source.camera_names == ('overhead','left','right')
    def reply(payload):
        prompt=payload['input'][0]['content'];assert 'Two SO101' in prompt and 'OWN fixed robot base' in prompt
        assert '9.5 cm' not in prompt and 'Two identical 6-DoF' not in prompt
        assert len([v for v in payload['input'][-1]['content'] if v['type']=='input_image'])==3
        assert set(payload['tools'][0]['parameters']['properties']['targets']['properties'])==set(NAMES)
        return {'output':[{'type':'function_call','call_id':'t','name':'move_to','arguments':json.dumps({'targets':{'right_gripper':.32},'note':'Open right slightly.'})}]}
    p._post_json=reply
    points=p.build_trajectory('Test',obs(),0);assert points[-1]['right_gripper']==.32
    p.trajectory_completed({**obs(),**points[-1],'step_id':len(points)},len(points));assert p._pending is None


def test_codex_schema(manifest):
    with patch('remote_yam.codex_policy.codex_status',return_value={'ready':True}),patch('remote_yam.codex_policy.codex_binary',return_value='/unused'):
        p=BimanualSO101CodexAdapter(calibration_path=manifest,camera_source=Cameras())
    assert p._expected_camera_count==3
    value=dict(action='move_to',targets={n:None for n in NAMES},note='Open right',summary='',reason='',hindsight='')
    value['targets']['right_gripper']=.4
    assert json.loads(p._decision_response(value)['output'][0]['arguments'])['targets']=={'right_gripper':.4}
    p._workspace.cleanup()
