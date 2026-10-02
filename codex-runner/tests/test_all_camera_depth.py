import copy
import io
import json
import time
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from remote_yam.api_depth import ApiDepth, NoDepthImage, StaleDepth, depth_robot_stopped
from remote_yam.api_depth_set import ApiDepthSet, PairedDepthFrame
from remote_yam.codex_depth_policy import CodexDepthAdapter
from remote_yam.codex_policy import DECISION_SCHEMA


def fixture(role='top'):
    camera = dict(image_size=[8,6],K=[[100.,0,4],[0,100.,3],[0,0,1]],
                  distortion=[0.]*5,distortion_model='opencv_brown_conrady')
    report = dict(schema_version=1,robot_id='yam-1',calibration_id='test',
                  quality=dict(status='rejected',operator_selected=True),cameras={})
    for name in ('top','left','right'):
        report['cameras'][name] = copy.deepcopy(camera)
        if name == 'top':
            report['cameras'][name]['T_base_camera'] = {arm:np.eye(4).tolist() for arm in ('left','right')}
        else:
            report['cameras'][name]['T_grasp_camera'] = np.eye(4).tolist()
    meta = dict(schema_version=1,robot_id='yam-1',camera=role,calibration_id='test',captured_at=100.,
                depth_units='meters',depth_aligned_to='color',depth_semantics='optical_z',
                depth_coordinate_frame='color_optical',sensor_serial='sensor-'+role)
    buffer=io.BytesIO()
    np.savez_compressed(buffer,rgb=np.zeros((6,8,3),np.uint8),depth_m=np.full((6,8),.6,np.float32),
                        metadata=np.array(json.dumps(meta)))
    return report,meta,buffer.getvalue()


def private_api(role='top'):
    return ApiDepth('https://api.example',camera=role,
                    depth_url='http://127.0.0.1:18090/cameras/'+role+'.rgbd.npz',
                    monotonic_freshness=True,clock=lambda:99.)


def test_public_and_private_sets_use_their_own_routes_and_freshness_contracts():
    public = ApiDepthSet('https://api.example', 'https://api.example/')
    private = ApiDepthSet('https://api.example', 'http://127.0.0.1:18090')
    for role in ('top', 'left', 'right'):
        assert public.apis[role].depth_path == '/v1/robots/yam-1/cameras/' + role + '.rgbd.npz'
        assert not public.apis[role].monotonic_freshness
        assert private.apis[role].depth_path == '/cameras/' + role + '.rgbd.npz'
        assert private.apis[role].monotonic_freshness


def headers(role='top'):
    return {'x-source-capture-age-s':'0.3','x-captured-at':'100',
            'x-robot-id':'yam-1','x-camera-role':role,'x-camera-serial':'sensor-'+role,
            'x-calibration-id':'test'}


def test_private_monotonic_bound_preserves_timestamp_despite_clock_offset():
    api=private_api(); report,meta,data=fixture()
    receipt=(headers(),.6,time.monotonic())
    snap=api.decode(data,report,receipt=receipt)
    assert snap.metadata['captured_at']==100 and snap.age()<1
    assert api.clock()<snap.metadata['captured_at']
    for bound in (-1,5.01,float('nan'),float('inf')):
        with pytest.raises(StaleDepth):api.decode(data,report,receipt=(headers(),bound,time.monotonic()))
    for key,value in [('x-captured-at','101'),('x-camera-role','left'),
                      ('x-camera-serial','wrong'),('x-calibration-id','wrong')]:
        with pytest.raises(ValueError):api.decode(data,report,receipt=(dict(headers(),**{key:value}),.6,time.monotonic()))
    with pytest.raises(ValueError):api.decode(data,report)


def test_private_age_includes_processing_and_accepts_through_five_seconds():
    api = private_api(); report, _, data = fixture()
    with patch('remote_yam.api_depth.time.monotonic', return_value=12.):
        for bound in (1., 3.):
            snap = api.decode(data, report, receipt=(headers(), bound, 10.), stopped=True)
            assert snap.age() == bound + 2.
        with pytest.raises(StaleDepth):
            api.decode(data, report, receipt=(headers(), 3.01, 10.), stopped=True)
        with pytest.raises(StaleDepth):
            api.decode(data, report, receipt=(headers(), 0., 10.))


def test_private_read_includes_full_round_trip_in_bound_and_rejects_invalid_source_age():
    api=private_api(); _,_,data=fixture()
    response=MagicMock(status=200)
    response.getheaders.return_value=list(headers().items())
    response.read1.side_effect=[data,b'']
    with patch.object(api.depth_connection,'request'),patch.object(api.depth_connection,'getresponse',return_value=response), \
            patch('remote_yam.api_depth.time.monotonic',side_effect=[10,10.1,10.2,10.7,10.7]):
        api.read(api.depth_suffix)
    assert api._depth_receipt[1]==pytest.approx(1.)
    for age in ('-1','nan','inf'):
        response.getheaders.return_value=list(dict(headers(),**{'x-source-capture-age-s':age}).items())
        response.read1.side_effect=[data,b'']
        with patch.object(api.depth_connection,'request'),patch.object(api.depth_connection,'getresponse',return_value=response):
            with pytest.raises(StaleDepth):api.read(api.depth_suffix)


def test_wrist_query_requires_current_pose_and_uses_named_camera_intrinsics():
    report,meta,data=fixture('left')
    api=ApiDepth('https://api.example',camera='left',clock=lambda:101.)
    snap=api.decode(data,report)
    with pytest.raises(ValueError,match='current measured'):snap.measure(4,3,0)
    pose=np.eye(4); pose[:3,3]=[.1,.2,.3]
    measured=snap.measure(4,3,0,T_base_camera=pose)
    assert measured['camera']=='left'
    np.testing.assert_allclose(measured['point_base_m'],[.1,.2,.9],atol=1e-6)
    pose[0,3]+=.1
    assert snap.measure(4,3,0,T_base_camera=pose)['point_base_m'][0]==pytest.approx(.2)
    with pytest.raises(ValueError):ApiDepth('https://api.example',camera='observer')
    with pytest.raises(ValueError):ApiDepth('https://api.example',monotonic_freshness=True)


def test_all_camera_observation_has_six_paired_images_and_queries_wrist_pose():
    with patch('remote_yam.codex_policy.codex_status',return_value={'ready':True}):
        provider=CodexDepthAdapter('https://api.example',all_depth_origin='http://127.0.0.1:18090')
    try:
        source=provider._camera_source
        frames=[]
        for role in ('top','left','right'):
            report,meta,data=fixture(role)
            report['base_geometry']={'spacing_m':.5}
            snap=ApiDepth('https://api.example',camera=role,clock=lambda:101.).decode(data,report)
            snap.age_upper_s=.1
            source.snapshots[role]=snap
            frames.append(PairedDepthFrame(role,snap.image(),101.,100.,meta['sensor_serial'],'test'))
        obs=dict(robot_id='yam-1',settled=True,left_joints_deg=[0]*6,right_joints_deg=[0]*6,
                 left_gripper=1.,right_gripper=1.)
        provider.set_depth_calibration(report)
        assert provider._geometry.ik.base_spacing_m==.5
        with patch.object(source,'capture_for_policy',return_value=frames):
            message=provider._observation_message('pick',obs,1)
        images=[part for part in message['content'] if part['type']=='input_image']
        assert len(images)==6 and all(p['image_url'].startswith('data:image/png;') for p in images)
        assert [p['camera'] for p in provider._vision_frames]==['top','left','right']
        provider._history=[message]
        terminal_value=dict(action='done',targets=dict.fromkeys(DECISION_SCHEMA['properties']['targets']['required']),
                   note='',summary='Observation only',reason='',hindsight='',
                   pixel=dict(camera=None,u=None,v=None,radius=None))
        def execute(command,prompt,root):
            assert command.count('--image')==6
            assert 'wrist_T_base_camera' in prompt
            return [dict(type='thread.started',thread_id='12345678-1234-1234-1234-123456789abc'),
                    dict(type='item.completed',item=dict(type='agent_message',text=json.dumps(terminal_value))),
                    dict(type='turn.completed')]
        with patch.object(provider,'_execute',side_effect=execute):
            assert provider._post_json(dict(input=copy.deepcopy(provider._history),tools=[]))['output'][0]['name']=='done'
        value=dict(action='measure_depth',targets=dict.fromkeys(DECISION_SCHEMA['properties']['targets']['required']),
                   note='',summary='',reason='',hindsight='',pixel=dict(camera='left',u=4,v=3,radius=0))
        query=provider._decision_response(value)
        terminal=provider._decision_response(dict(value,action='done',pixel=dict(camera=None,u=None,v=None,radius=None)))
        with patch('remote_yam.codex_policy.CodexAdapter._post_json',side_effect=[query,terminal]):
            provider._post_json(dict(input=[],tools=[]))
        result=json.loads(provider._history[-1]['output'])
        assert result['camera']=='left' and set(result['points_base_m'])=={'left','right'}
        assert 'arm' not in result and 'point_base_m' not in result
        for arm in ('left','right'):
            expected=provider._depth_transforms['left'][arm]@np.array([0,0,.6,1])
            np.testing.assert_allclose(result['points_base_m'][arm],expected[:3],atol=1e-6)
        np.testing.assert_allclose(np.subtract(result['points_base_m']['right'],
                                              result['points_base_m']['left']),[0,.5,0],atol=1e-6)
        assert provider.public_config()['allowed_arms']==['left','right']
        # The default example accepts the model's arm choice rather than
        # silently enforcing the previous left-only policy.
        for arm in ('left','right'):
            targets=dict.fromkeys(DECISION_SCHEMA['properties']['targets']['required'])
            targets[arm+'_x']=.35
            move=provider._decision_response(dict(terminal_value,action='move_to',targets=targets))
            assert move['output'][0]['name']=='move_to'
        value['pixel']['camera']='observer'
        with pytest.raises(RuntimeError):provider._decision_response(value)
    finally:
        provider._workspace.cleanup()


@pytest.mark.parametrize('age', [-.01, 5.01, float('nan'), float('inf')])
def test_assembling_set_rejects_an_aged_view(age):
    from remote_yam.cameras import CameraUnavailable
    good=MagicMock();good.age.return_value=.5
    old=MagicMock();old.age.return_value=age
    expected = NoDepthImage if age == 5.01 else CameraUnavailable
    with pytest.raises(expected):ApiDepthSet.require_fresh({'left':good,'top':old,'right':good}, stopped=True)


def test_assembling_set_accepts_views_up_to_five_seconds_old():
    snapshots = {role: MagicMock() for role in ('left', 'top', 'right')}
    for snapshot, age in zip(snapshots.values(), (0., 3., 5.)):
        snapshot.age.return_value = age
    ApiDepthSet.require_fresh(snapshots, stopped=True)
    with pytest.raises(NoDepthImage):
        ApiDepthSet.require_fresh(snapshots)


def test_depth_stop_confirmation_requires_explicit_settled_feedback():
    assert depth_robot_stopped({'settled': True, 'mode': 'API_ACTIVE'})
    for value in (None, False, 1, 'true'):
        assert not depth_robot_stopped({'settled': value})
    for mode in ('EXECUTING', 'HOMING'):
        assert not depth_robot_stopped({'settled': True, 'mode': mode})


def test_missing_depth_retries_entire_set_and_recovers_after_more_than_ten_attempts():
    source = ApiDepthSet('https://api.example', 'https://api.example')
    counts, events = dict.fromkeys(('top', 'left', 'right'), 0), []
    def capture(role):
        counts[role] += 1
        if role == 'left' and counts[role] <= 12:
            raise NoDepthImage(role)
        snap = MagicMock(metadata=dict(captured_at=counts[role], sensor_serial=role, calibration_id='test'))
        snap.image.return_value = f'{role}:{counts[role]}'.encode()
        snap.age.return_value = 1.
        return snap
    for role in counts:
        api = source.apis[role] = MagicMock()
        api.read.return_value = b'{}'
        api.capture.side_effect = lambda role=role, **kwargs: capture(role)
    with patch('remote_yam.api_depth_set.time.sleep'):
        frames = source.capture_for_policy({'robot_id': 'yam-1', 'settled': True},
                                           on_event=lambda *args: events.append(args))
    assert counts == dict.fromkeys(counts, 13)
    assert all(frame.jpeg == f'{frame.name}:13'.encode() for frame in frames)
    assert [event[0] for event in events] == ['camera_retry', 'camera_recovered']
    assert events[0][2]['cause'] == 'No image'


def test_missing_depth_wait_can_be_cancelled_without_reusing_saved_snapshots():
    source = ApiDepthSet('https://api.example', 'https://api.example')
    source.snapshots = {'top': object()}
    cancelled = [False]
    def event(*args):
        cancelled[0] = True
    with patch.object(source, 'capture', side_effect=NoDepthImage('left')) as capture, \
         patch('remote_yam.api_depth_set.time.sleep'):
        with pytest.raises(RuntimeError, match='cancelled'):
            source.capture_for_policy({'settled': True}, cancelled=lambda: cancelled[0], on_event=event)
    capture.assert_called_once()
    assert source.snapshots == {}


def test_factory_rays_match_installed_sdk_oracle_at_corners_and_task_pixels():
    from pathlib import Path
    from remote_yam.api_depth import factory_ray
    oracle=json.loads((Path(__file__).parent/'fixtures/realsense-deprojection-2.58.1.json').read_text())
    assert oracle['sdk_version']=='2.58.1.10581'
    for row in oracle['rows']:
        np.testing.assert_allclose(factory_ray(row['profile'],*row['pixel']),row['ray'],atol=1e-6,rtol=0)


def test_factory_profile_is_bound_and_used_instead_of_board_rgb_intrinsics():
    report,meta,_=fixture()
    profile=dict(width=8,height=6,fx=50.,fy=50.,ppx=2.,ppy=3.,
                 model='distortion.inverse_brown_conrady',coeffs=[0.]*5)
    report['cameras']['top'].update(depth_intrinsics=profile,depth_sensor_serial='sensor-top')
    meta['factory_color_intrinsics']=profile
    def bundle(metadata):
        out=io.BytesIO()
        np.savez_compressed(out,rgb=np.zeros((6,8,3),np.uint8),
                            depth_m=np.full((6,8),.6,np.float32),metadata=np.array(json.dumps(metadata)))
        return out.getvalue()
    api=ApiDepth('https://api.example',clock=lambda:101.)
    measured=api.decode(bundle(meta),report).measure(4,3,0)
    np.testing.assert_allclose(measured['point_base_m'],[.024,0,.6],atol=1e-6)
    assert measured['intrinsics_source']=='factory_aligned_color'
    for change in ({'sensor_serial':'wrong'},
                   {'factory_color_intrinsics':dict(profile,fx=51.)}):
        with pytest.raises(ValueError,match='profile'):
            api.decode(bundle(dict(meta,**change)),report)
