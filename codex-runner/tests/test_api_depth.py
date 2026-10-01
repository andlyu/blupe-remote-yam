import copy
import io
import json
import unittest
from unittest.mock import patch

import numpy as np

from remote_yam.api_depth import ApiDepth, StaleDepth
from remote_yam.codex_depth_policy import CodexDepthAdapter
from remote_yam.codex_policy import DECISION_SCHEMA


def fixture():
    report=dict(schema_version=1,robot_id='yam-1',calibration_id='test',quality={'status':'rejected','operator_selected':True},
        cameras={'top':dict(image_size=[8,6],K=[[100.,0,4],[0,100.,3],[0,0,1]],
        distortion=[0.1,-0.01,0.001,0.002,0.],distortion_model='opencv_brown_conrady',
        T_base_camera={s:np.eye(4).tolist() for s in ('left','right')})})
    meta=dict(schema_version=1,robot_id='yam-1',camera='top',calibration_id='test',captured_at=100.,
        depth_units='meters',depth_aligned_to='color',depth_semantics='optical_z',
        depth_coordinate_frame='color_optical')
    return report,meta,np.full((6,8),.6,dtype='float32')


def bundle(meta,depth):
    out=io.BytesIO()
    np.savez_compressed(out,rgb=np.zeros((6,8,3),dtype='uint8'),depth_m=depth,
                        metadata=np.array(json.dumps(meta)))
    return out.getvalue()


def compact_bundle(meta,depth,jpeg_size=(8,6)):
    from PIL import Image
    jpeg=io.BytesIO();Image.new('RGB',jpeg_size,color='red').save(jpeg,format='JPEG',quality=90)
    planes=depth.astype('<f4',copy=False).view('uint8').reshape(*depth.shape,4).transpose(2,0,1).copy()
    out=io.BytesIO()
    meta=dict(meta,rgb_encoding='jpeg',transport_format='rgbd-compact-npz-v1',depth_dtype='<f4')
    np.savez_compressed(out,rgb_jpeg=np.frombuffer(jpeg.getvalue(),dtype='uint8'),
                        depth_bytes=planes,metadata=np.array(json.dumps(meta)))
    return out.getvalue()


class DepthTests(unittest.TestCase):
    def setUp(self):
        self.api=ApiDepth('https://api.example',clock=lambda:101.)
        self.report,self.meta,self.depth=fixture()

    def snapshot(self):
        return self.api.decode(bundle(self.meta,self.depth),self.report)

    def test_metric_surface_preserves_z_and_inverts_distortion(self):
        snap=self.snapshot()
        m=snap.measure(6,4,0)
        x,y,z=m['point_base_m']
        self.assertAlmostEqual(z,.6,places=6)
        x,y=x/z,y/z
        k1,k2,p1,p2,k3=self.report['cameras']['top']['distortion']
        r=x*x+y*y
        distorted=[x*(1+k1*r+k2*r*r+k3*r*r*r)+2*p1*x*y+p2*(r+2*x*x),
                   y*(1+k1*r+k2*r*r+k3*r*r*r)+p1*(r+2*y*y)+2*p2*x*y]
        np.testing.assert_allclose(distorted,[.02,.01],atol=1e-8)
        self.assertEqual(m['quality']['status'],'rejected')

    def test_stale_identity_dtype_invalid_depth_and_changed_calibration(self):
        for field,value in [('robot_id','other'),('calibration_id','other'),('depth_units','mm')]:
            bad=dict(self.meta,**{field:value})
            with self.assertRaises(ValueError):self.api.decode(bundle(bad,self.depth),self.report)
        with self.assertRaises(StaleDepth):self.api.decode(bundle(dict(self.meta,captured_at=90.),self.depth),self.report)
        with self.assertRaises(ValueError):self.api.decode(bundle(self.meta,self.depth.astype('float64')),self.report)
        self.snapshot()
        bad=copy.deepcopy(self.report);bad['calibration_id']='changed'
        with self.assertRaisesRegex(ValueError,'changed'):
            self.api.decode(bundle(dict(self.meta,calibration_id='changed'),self.depth),bad)

    def test_holes_edges_bounds_and_preview(self):
        self.depth[:]=0
        self.depth[3,4]=.6
        snap=self.snapshot()
        with self.assertRaises(ValueError):snap.measure(0,0,0)
        with self.assertRaises(ValueError):snap.measure(-1,0,0)
        self.assertTrue(snap.image(True).startswith(b'\x89PNG'))
        snap.depth[3,5]=.8
        with self.assertRaisesRegex(ValueError,'crosses'):snap.measure(4,3,2)

    def test_api_url_and_capture_request_use_validated_origin(self):
        from unittest.mock import MagicMock
        response=MagicMock()
        response.status=200
        response.read1.side_effect=[json.dumps(self.report).encode(),b'',bundle(self.meta,self.depth),b'']
        with patch.object(self.api.connection,'getresponse',return_value=response), patch.object(self.api.connection,'request') as opened:
            snap=self.api.capture()
        self.assertEqual(opened.call_args_list[1].args[1],'/v1/robots/yam-1/cameras/top.rgbd.npz')
        self.assertEqual(snap.metadata['calibration_id'],'test')

    def test_private_depth_override_keeps_public_calibration_and_validation(self):
        from unittest.mock import MagicMock
        api=ApiDepth('https://api.example',depth_url='http://127.0.0.1:18089/16/rgbd.npz',clock=lambda:101.)
        report_response=MagicMock(status=200)
        report_response.read1.side_effect=[json.dumps(self.report).encode(),b'']
        depth_response=MagicMock(status=200)
        depth_response.read1.side_effect=[bundle(self.meta,self.depth),b'']
        with patch.object(api.connection,'getresponse',return_value=report_response), \
             patch.object(api.connection,'request') as public, \
             patch.object(api.depth_connection,'getresponse',return_value=depth_response), \
             patch.object(api.depth_connection,'request') as private:
            snap=api.capture()
        self.assertEqual(public.call_args.args[1],'/v1/robots/yam-1/calibration')
        self.assertEqual(private.call_args.args[1],'/16/rgbd.npz')
        self.assertEqual(snap.metadata['calibration_id'],'test')
        with self.assertRaises(StaleDepth):
            api.decode(bundle(dict(self.meta,captured_at=90.),self.depth),self.report)
        for url in ('http://user:password@localhost/rgbd.npz','http://localhost/rgbd.npz?token=abc',
                    'http://localhost/rgbd.npz#x','ftp://localhost/rgbd.npz'):
            with self.assertRaises(ValueError):ApiDepth('https://api.example',depth_url=url)

    def test_compact_transport_preserves_depth_bits_holes_geometry_and_age(self):
        api=ApiDepth('https://api.example',depth_url='http://127.0.0.1:18090/16/rgbd.npz',clock=lambda:101.)
        self.depth=np.linspace(.55,.65,48,dtype='float32').reshape(6,8)
        self.depth[0,:2]=[0,np.nan]
        data=compact_bundle(self.meta,self.depth)
        snap=api.decode(data,self.report)
        self.assertEqual(snap.depth.tobytes(),self.depth.tobytes())
        self.assertEqual(snap.rgb.shape,(6,8,3))
        self.assertEqual(snap.metadata['captured_at'],100.)
        self.assertAlmostEqual(snap.measure(4,3,0)['optical_z_m'],float(self.depth[3,4]))
        with self.assertRaises(ValueError):self.api.decode(data,self.report)
        with self.assertRaisesRegex(ValueError,'calibration'):
            api.decode(compact_bundle(self.meta,self.depth,jpeg_size=(7,6)),self.report)
        with self.assertRaises(StaleDepth):
            api.decode(compact_bundle(dict(self.meta,captured_at=90.),self.depth),self.report)

    def test_slow_chunked_body_has_total_deadline(self):
        from unittest.mock import MagicMock
        response=MagicMock(status=200)
        response.read1.return_value=b'ab'
        with patch.object(self.api.connection,'getresponse',return_value=response), \
             patch.object(self.api.connection,'request'), \
             patch.object(self.api.connection,'close') as closed, \
             patch('remote_yam.api_depth.time.monotonic',side_effect=[0,1,6]):
            with self.assertRaises(TimeoutError):self.api.read('/calibration')
        self.assertEqual(response.read1.call_count,1)
        closed.assert_called_once()

    def test_transient_observation_timeout_retries_read_not_motion(self):
        with patch.object(self.api,'read',side_effect=[TimeoutError(),json.dumps(self.report).encode(),
                         bundle(self.meta,self.depth)]) as read, patch('remote_yam.api_depth.time.sleep'):
            snap=self.api.capture()
        self.assertEqual(read.call_count,3)
        self.assertEqual(snap.metadata['calibration_id'],'test')

    def test_millimeter_transport_has_explicit_precision_holes_and_conservative_patch(self):
        from PIL import Image
        api=ApiDepth('https://api.example',depth_url='http://127.0.0.1:18090/16/rgbd.npz',clock=lambda:101.)
        jpeg=io.BytesIO();Image.new('RGB',(8,6)).save(jpeg,format='JPEG')
        meta=dict(self.meta,transport_format='rgbd-mm-npz-v1',rgb_encoding='jpeg',depth_dtype='<u2',
                  wire_depth_units='millimeters',depth_quantization_m=.001,depth_max_error_m=.0005)
        mm=np.full((6,8),600,dtype='<u2');mm[0,0]=0
        def encode():
            out=io.BytesIO();np.savez_compressed(out,rgb_jpeg=np.frombuffer(jpeg.getvalue(),dtype='uint8'),
                depth_mm=mm,metadata=np.array(json.dumps(meta)));return out.getvalue()
        snap=api.decode(encode(),self.report)
        measured=snap.measure(4,3,0)
        self.assertAlmostEqual(measured['optical_z_m'],.6,places=6)
        self.assertEqual(measured['depth_max_error_m'],.0005)
        with self.assertRaises(ValueError):snap.measure(0,0,0)
        snap.depth[3,5]=.625
        with self.assertRaisesRegex(ValueError,'crosses'):snap.measure(4,3,2)
        meta['depth_quantization_m']=-.001
        with self.assertRaisesRegex(ValueError,'millimeter'):api.decode(encode(),self.report)
        with self.assertRaisesRegex(ValueError,'quantization'):
            self.api.decode(bundle(dict(self.meta,depth_quantization_m=-.1),self.depth),self.report)

    def test_read_only_depth_query_then_validated_decision(self):
        with patch('remote_yam.codex_policy.codex_status',return_value={'ready':True}):
            provider=CodexDepthAdapter('https://api.example',active_arm='left')
        self.addCleanup(provider._workspace.cleanup)
        provider._snapshot=self.snapshot()
        value=dict(action='measure_depth',targets=dict.fromkeys(DECISION_SCHEMA['properties']['targets']['required']),
                   note='',summary='',reason='',hindsight='',pixel=dict(u=4,v=3,radius=0))
        query=provider._decision_response(value)
        terminal=provider._decision_response(dict(value,action='done',pixel=dict(u=None,v=None,radius=None)))
        provider._history=[]
        with patch('remote_yam.codex_policy.CodexAdapter._post_json',side_effect=[query,terminal]):
            result=provider._post_json(dict(input=[],tools=[]))
        self.assertEqual(result['output'][0]['name'],'done')
        measured=json.loads(provider._history[-1]['output'])
        self.assertAlmostEqual(measured['optical_z_m'],.6,places=6)
        value.update(action='move_to',pixel=dict(u=None,v=None,radius=None),note='move')
        value['targets']['right_x']=.3
        with self.assertRaisesRegex(RuntimeError,'Other-arm'):provider._decision_response(value)

    def test_codex_image_transport_and_query_resume_keep_same_capture(self):
        import base64
        from PIL import Image
        with patch('remote_yam.codex_policy.codex_status',return_value={'ready':True}):
            provider=CodexDepthAdapter('https://api.example')
        self.addCleanup(provider._workspace.cleanup)
        provider._snapshot=self.snapshot()
        out=io.BytesIO();Image.new('RGB',(8,6)).save(out,format='PNG')
        img='data:image/png;base64,'+base64.b64encode(out.getvalue()).decode()
        provider._history=[dict(role='user',content=[dict(type='input_image',image_url=img)]*4)]
        value=dict(action='measure_depth',targets=dict.fromkeys(DECISION_SCHEMA['properties']['targets']['required']),
                   note='',summary='',reason='',hindsight='',pixel=dict(u=4,v=3,radius=0))
        counts=[]
        def execute(command,prompt,root):
            counts.append(command.count('--image'))
            self.assertIn('features.shell_tool=false',command)
            if len(counts)>1:
                self.assertIn('optical_z_m',prompt)
                result=dict(value,action='done',summary='Observed only',pixel=dict(u=None,v=None,radius=None))
            else:result=value
            return [dict(type='thread.started',thread_id='12345678-1234-1234-1234-123456789abc'),
                    dict(type='item.completed',item=dict(type='agent_message',text=json.dumps(result))),
                    dict(type='turn.completed')]
        with patch.object(provider,'_execute',side_effect=execute):
            result=provider._post_json(dict(input=copy.deepcopy(provider._history),tools=[]))
        self.assertEqual(counts,[4,0])
        self.assertEqual(result['output'][0]['name'],'done')

    def test_inactive_arm_packet_holds_measured_joints_and_gripper(self):
        from remote_yam.codex_depth_policy import SingleArmTrajectory
        from remote_yam.robocurve_trajectory import InvalidMove
        trajectory=SingleArmTrajectory('left')
        obs=dict(settled=True,left_joints_deg=[0]*6,right_joints_deg=[0]*6,
                 left_gripper=.5,right_gripper=.8)
        points=trajectory.build({'left_gripper':.6},obs,0)
        self.assertTrue(points)
        for p in points:
            self.assertEqual(p['right_joints_deg'],obs['right_joints_deg'])
            self.assertEqual(p['right_gripper'],.8)
        with self.assertRaises(InvalidMove):trajectory.build({'right_gripper':.2},obs,0)

    def test_default_example_can_act_with_either_arm_and_holds_unnamed_arm(self):
        from remote_yam.codex_depth_policy import DepthTrajectory
        obs=dict(settled=True,left_joints_deg=[0]*6,right_joints_deg=[0]*6,
                 left_gripper=.5,right_gripper=.8)
        for arm,other in [('left','right'),('right','left')]:
            with self.subTest(arm=arm):
                points=DepthTrajectory().build({arm+'_gripper':.6},obs,0)
                self.assertTrue(points)
                self.assertAlmostEqual(points[-1][arm+'_gripper'],.6)
                for point in points:
                    self.assertEqual(point[other+'_joints_deg'],obs[other+'_joints_deg'])
                    self.assertEqual(point[other+'_gripper'],obs[other+'_gripper'])


if __name__=='__main__':unittest.main()
