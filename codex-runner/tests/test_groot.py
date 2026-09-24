import io
import math
from pathlib import Path
import sys
import threading
from unittest.mock import patch
import pytest
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from remote_yam.groot_policy import GrootAdapter, MODEL
from remote_yam.controller import RunnerController
from remote_yam.session import MockSessionAPI
from remote_yam.cameras import CameraFrame


def test_warmup_begins_only_after_queue_acceptance_and_stops_on_leave():
    api=MockSessionAPI(auto_activate=False)
    class Policy:
        provider_name='groot'
        def public_config(self): return {'provider':'groot','model':MODEL}
        def start_preparation(self,cancelled):
            assert any(x['status']=='queued' for x in api.get_queue_snapshot()['entries'])
            self.cancelled=cancelled
    p=Policy();c=RunnerController(api);c.join_and_run(p,'bin')
    assert not p.cancelled() and not api.action_log
    c.stop();assert p.cancelled()
    c._worker.join(1)


def test_rejected_queue_does_not_start_gpu():
    class Reject(MockSessionAPI):
        def create_session(self,*args,**kwargs): raise RuntimeError('full')
    p=GrootAdapter('secret',camera_source=None)
    with pytest.raises(RuntimeError): RunnerController(Reject()).join_and_run(p,'bin')
    assert p._thread is None


def test_wait_cancelled_and_public_config_never_exposes_key():
    p=GrootAdapter('private-secret',camera_source=None)
    with pytest.raises(RuntimeError,match='cancelled'): p.wait_ready(lambda:True)
    assert 'private-secret' not in str(p.public_config())


def policy_and_observation():
    b=io.BytesIO();Image.new('RGB',(640,360)).save(b,format='JPEG')
    class Cameras:
        def capture_for_policy(self,*a,**kw):return [CameraFrame(n,b.getvalue(),0) for n in ('left','top','right')]
    p=GrootAdapter('private-secret',camera_source=Cameras());p._ready.set()
    o={'left_joints_deg':[0]*6,'right_joints_deg':[0]*6,'left_gripper':.2,'right_gripper':.8}
    return p,o


def test_model_state_units_chunk_validation_and_gateway_sampling():
    p,o=policy_and_observation();seen=[]
    row=[0]*6+[.3]+[0]*6+[.7]
    row[0]=.005;row[7]=-.005
    def send(path,payload):
        seen.append(payload)
        return {'model':MODEL,'checkpoint_step':10000,'action_units':'radians','actions':[row[:] for _ in range(30)]}
    p._json=send
    points=p.build_trajectory('bin',o,10)
    assert seen[0]['state']==[0]*6+[.2]+[0]*6+[.8]
    assert set(seen[0]['images'])=={'top_cam','left_cam','right_cam'}
    assert points[0]['step_id']==10 and len(points)==10
    assert points[-1]['left_joints_deg'][0]==pytest.approx(math.degrees(.005))
    assert points[-1]['right_joints_deg'][0]==pytest.approx(math.degrees(-.005))
    assert points[-1]['left_gripper']==.3
    p._json=lambda *a:{'model':MODEL,'checkpoint_step':10000,'action_units':'radians','actions':[row]*29}
    with pytest.raises(ValueError,match='chunk'):p.build_trajectory('bin',o,0)


def test_chunk_keeps_one_second_and_gripper_alignment():
    p,o=policy_and_observation()
    rows=[]
    for i in range(30):
        rows.append([.001*(i+1)]+[0]*5+[i/30]+[-.001*(i+1)]+[0]*5+[1-i/30])
    p._json=lambda *a:{'model':MODEL,'checkpoint_step':10000,'action_units':'radians','actions':rows}
    points=p.build_trajectory('bin',o,0)
    assert len(points)/10 == 1
    for point,row in zip(points,rows[2::3]):
        assert point['left_joints_deg'][0] == pytest.approx(math.degrees(row[0]))
        assert point['right_joints_deg'][0] == pytest.approx(math.degrees(row[7]))
        assert point['left_gripper'] == row[6]
        assert point['right_gripper'] == row[13]


@pytest.mark.parametrize('index',[2,29])
def test_oversized_action_rejects_whole_chunk_instead_of_stretching(index):
    p,o=policy_and_observation()
    rows=[[0]*14 for _ in range(30)]
    rows[index][0]=.02
    p._json=lambda *a:{'model':MODEL,'checkpoint_step':10000,'action_units':'radians','actions':rows}
    with pytest.raises(ValueError,match='without retiming'):
        p.build_trajectory('bin',o,0)


def test_late_inference_after_stop_cannot_return_commands():
    p,o=policy_and_observation();stopped=False
    def send(*args):
        nonlocal stopped
        stopped=True
        return {'model':MODEL,'checkpoint_step':10000,'action_units':'radians','actions':[[0]*14 for _ in range(30)]}
    p._json=send;p.cancelled=lambda:stopped
    with pytest.raises(RuntimeError,match='cancelled'):p.build_trajectory('bin',o,0)


@pytest.mark.parametrize('change', [
    {'lease_id':'replacement'}, {'status':'stopped'},
    {'latest_observation_step':1}, {'active_trajectory':{'trajectory_id':'other'}},
])
def test_cold_start_revalidates_lease_and_step_before_reading_or_acting(change):
    from types import SimpleNamespace
    class Policy:
        def wait_ready(self,cancelled): return True
    live={'status':'running','episode_id':'episode','lease_id':'lease',
          'latest_observation_step':0,'active_trajectory':None,**change}
    def unexpected(*args): raise AssertionError('Must reject before fresh camera/robot access')
    api=SimpleNamespace(get_session=lambda sid:live,get_robot_observation=unexpected)
    c=RunnerController(api);c._provider=Policy();c._status='running';c._session_id='session'
    c._episode_id='episode';c._lease_id='lease'
    with pytest.raises(RuntimeError,match='Session changed'):
        c._observation({}, {'step_id':0,'episode_id':'episode','lease_id':'lease'})


def test_cold_start_uses_fresh_feedback_with_same_lease():
    from types import SimpleNamespace
    import time
    seen=[]
    class ReachedInference(Exception): pass
    class Policy:
        def wait_ready(self,cancelled): return True
        def infer(self,prompt,observation):
            seen.append(observation);raise ReachedInference()
    live={'status':'running','episode_id':'episode','lease_id':'lease',
          'latest_observation_step':0,'active_trajectory':None}
    fresh={'source':'simulation','observed_at':time.time(),'left_joints_deg':[1]*6,
           'right_joints_deg':[2]*6,'left_gripper':.2,'right_gripper':.8,'settled':True,'homed':True}
    api=SimpleNamespace(get_session=lambda sid:live,get_robot_observation=lambda rid:fresh)
    c=RunnerController(api);c._provider=Policy();c._status='running';c._session_id='session'
    c._episode_id='episode';c._lease_id='lease'
    with pytest.raises(ReachedInference):
        c._observation({}, {'step_id':0,'episode_id':'episode','lease_id':'lease','observed_at':0,
                            'left_joints_deg':[0]*6,'right_joints_deg':[0]*6})
    assert seen[0]['left_joints_deg']==[1]*6
    assert seen[0]['observed_at']==fresh['observed_at']


@pytest.mark.parametrize('changed', [{'settled':False}, {'homed':False}])
def test_cold_start_rejects_hardware_that_lost_readiness(changed):
    from types import SimpleNamespace
    import time
    class Policy:
        def wait_ready(self, cancelled): return True
        def infer(self, *args): raise AssertionError('Unready hardware must not reach inference')
    live = {'status':'running', 'episode_id':'episode', 'lease_id':'lease',
            'latest_observation_step':0, 'active_trajectory':None}
    original = {'source':'hardware', 'step_id':0, 'episode_id':'episode', 'lease_id':'lease',
                'left_joints_deg':[0]*6, 'right_joints_deg':[0]*6,
                'left_gripper':.5, 'right_gripper':.5, 'settled':True, 'homed':True,
                'observed_at':time.time(), 'mode':'API_ACTIVE',
                'safety':{'ok':True, 'estop_engaged':False}}
    fresh = {**original, **changed}
    api = SimpleNamespace(get_session=lambda sid:live, get_robot_observation=lambda rid:fresh)
    controller = RunnerController(api)
    controller._provider = Policy()
    controller._status = 'running'
    controller._session_id = 'session'
    controller._episode_id = 'episode'
    controller._lease_id = 'lease'
    with pytest.raises(RuntimeError, match='Hardware feedback blocked: completion_not_explicitly_'):
        controller._observation({}, original)
