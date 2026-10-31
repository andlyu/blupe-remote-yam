"""Absolute RoboSkills tool rotations through the real serializer; never hardware."""
from copy import deepcopy
import importlib.util
import json,math
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase,skipUnless
from unittest.mock import patch
import numpy as np
from remote_yam.roboskills_policy import RoboSkillsPolicy,RobotTools
from remote_yam.session import HttpSessionAPI
from remote_yam.api_depth_set import ApiDepthSet
from test_roboskills_policy import Cameras

FIXTURE=json.loads((Path(__file__).parent/'fixtures/roboskills-tilted-reach.json').read_text())

def level_grasp(point,degrees=45):
    phi=np.arctan2(point[1],point[0]);a=np.deg2rad(degrees)
    x=np.array([-np.sin(phi),np.cos(phi),0.])
    z=np.sin(a)*np.array([np.cos(phi),np.sin(phi),0.])-np.array([0.,0.,np.cos(a)])
    return np.column_stack((x,np.cross(z,x),z))

@skipUnless(importlib.util.find_spec('roboskills'),'Install the pinned RoboSkills runtime; required roboskills CI does so')
class OrientationContract(TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();self.p=RoboSkillsPolicy(camera_source=Cameras(),recording_root=self.tmp.name,llm=lambda s,m:'')
        self.p.configure_perception(ApiDepthSet('https://api.example','https://api.example'),FIXTURE['calibration']);self.p._state=deepcopy(FIXTURE['observation']);self.p._identity=('offline','offline')
        self.bot=RobotTools(self.p,'offline orientation contract');self.http=HttpSessionAPI('https://api.example');self.sent=[]
    def tearDown(self):self.tmp.cleanup()
    def target(self,arm):
        point=np.array(FIXTURE['sourcePoints'][arm]);rotation=level_grasp(point)
        return {f'{arm}_{axis}':float(v) for axis,v in zip('xyz',point-rotation[:,2]*.05)},rotation
    def plant(self,points):
        def transport(method,path,payload,**kw):self.sent.append(json.loads(json.dumps(payload,allow_nan=False)));return {'accepted':True}
        with patch.object(self.http,'_request',side_effect=transport),patch.object(self.http,'_capability',return_value='offline'):
            self.http.submit_trajectory('offline','offline','offline','packet',10,points)
        return {**self.p._state,**points[-1],'settled':True,'step_id':points[-1]['step_id']+1}
    def test_recorded_source_both_arms_reachable_with_level_jaws_and_tilt(self):
        for arm in ('left','right'):
            with self.subTest(arm=arm):
                target,rotation=self.target(arm);self.p._geometry.observe(self.p._state)
                before=deepcopy(self.p._geometry.start_rotations);cursor=self.p._state['step_id']
                with patch.object(self.p,'_execute') as execute:
                    result=self.bot.plan_move(**target,rotation={arm:rotation.tolist()})
                    self.assertTrue(result['reachable'],result);execute.assert_not_called()
                self.assertEqual(self.p._state['step_id'],cursor)
                for a,b in zip(before,self.p._geometry.start_rotations):np.testing.assert_array_equal(a,b)
    def test_absolute_rotation_survives_native_serialization_and_measured_feedback(self):
        for arm in ('left','right'):
            with self.subTest(arm=arm):
                self.setUpGeometry();before=deepcopy(self.p._state);target,rotation=self.target(arm);self.sent=[]
                with patch.object(self.p,'_execute',side_effect=self.plant):result=self.bot.move_to(**target,rotation={arm:rotation})
                actual=self.p._geometry.ik._poses()[('left','right').index(arm)][1]
                np.testing.assert_allclose(actual,rotation,atol=1e-5)
                self.assertLess(result['last_move']['orientation_error_deg'][arm],.01)
                other='left' if arm=='right' else 'right'
                for packet in self.sent:
                    for point in packet['waypoints']:
                        np.testing.assert_array_equal(point[other+'_joints_deg'],before[other+'_joints_deg'])
                        for side in ('left','right'):self.assertEqual(point[side+'_gripper'],before[side+'_gripper'])
                self.assertTrue(self.sent)
    def setUpGeometry(self):
        self.p.configure_perception(ApiDepthSet('https://api.example','https://api.example'),FIXTURE['calibration']);self.p._state=deepcopy(FIXTURE['observation'])
    def test_invalid_rotations_reject_without_dispatch_or_reference_change(self):
        identity=np.eye(3)
        invalid=[None,[],[1,0,0],[[1,0],[0,1]],identity*1.0001,np.diag([-1.,1.,1.]),[[True,0,0],[0,1,0],[0,0,1]],identity.astype(str),np.full((3,3),np.nan),np.full((3,3),np.inf),identity.astype(complex)]
        endpoint=identity.copy();endpoint[0,0]=np.nextafter(1.,np.inf);invalid.append(endpoint)
        for value in invalid:
            with self.subTest(value=str(value)[:40]):
                self.p._geometry.observe(self.p._state);before=deepcopy(self.p._geometry.start_rotations);step=self.p._state['step_id']
                with patch.object(self.p,'_execute') as execute:
                    result=self.bot.plan_move(right_x=.3,rotation={'right':value})
                    self.assertFalse(result['reachable']);self.assertEqual(result['code'],'invalid_rotation')
                    with self.assertRaises(ValueError):self.bot.move_to(right_x=.3,rotation={'right':value})
                    execute.assert_not_called()
                self.assertEqual(step,self.p._state['step_id'])
                for a,b in zip(before,self.p._geometry.start_rotations):np.testing.assert_array_equal(a,b)
    def test_ambiguous_or_gripper_only_rotation_is_rejected(self):
        bad=[dict(right_gripper=0,rotation={'right':np.eye(3)}),dict(right_x=.3,rotation={}),dict(right_x=.3,rotation={'other':np.eye(3)}),dict(right_x=.3,rotation={'left':np.eye(3)}),dict(right_x=.3,right_yaw=0,rotation={'right':np.eye(3)}),dict(right_x=.3,attitude='base_vertical',rotation={'right':np.eye(3)})]
        for kwargs in bad:
            with self.subTest(kwargs=str(kwargs)[:100]),patch.object(self.p,'_execute') as execute:
                with self.assertRaises(ValueError):self.bot.move_to(**kwargs)
                execute.assert_not_called()
    def test_valid_matrix_extrema_and_native_limits_preserved(self):
        from remote_yam.roboskills_trajectory import tool_rotation
        from itertools import permutations,product
        for perm in permutations(range(3)):
            for sign in product((-1.,1.),repeat=3):
                matrix=np.eye(3)[:,perm]*sign
                if np.linalg.det(matrix)>0:np.testing.assert_array_equal(tool_rotation(matrix),matrix)
        for arm in ('left','right'):
            self.setUpGeometry();target={arm+'_x':.32,arm+'_y':-.20 if arm=='left' else .20,arm+'_z':.12};r=np.diag([-1.,1.,-1.]);self.sent=[]
            with patch.object(self.p,'_execute',side_effect=self.plant):self.bot.move_to(**target,rotation={arm:r})
            for packet in self.sent:
                previous=np.deg2rad(np.concatenate([FIXTURE['observation'][side+'_joints_deg'] for side in ('left','right')]))
                for point in packet['waypoints']:
                    joints=np.deg2rad(np.concatenate([point[side+'_joints_deg'] for side in ('left','right')]))
                    self.assertTrue(np.all(joints>=self.p._geometry.ik.low-1e-10));self.assertTrue(np.all(joints<=self.p._geometry.ik.high+1e-10))
                    self.assertLessEqual(np.max(np.abs(joints-previous)),np.deg2rad(4)+1e-10);previous=joints
    def test_failed_plan_rolls_back_frame_and_does_not_consume_cursor(self):
        target,r=self.target('right');target['right_x']=3.
        self.p._geometry.observe(self.p._state);refs=deepcopy(self.p._geometry.start_rotations)
        with patch.object(self.p,'_execute') as execute:
            result=self.bot.plan_move(**target,rotation={'right':r});self.assertFalse(result['reachable']);execute.assert_not_called()
        for a,b in zip(refs,self.p._geometry.start_rotations):np.testing.assert_array_equal(a,b)
        self.assertEqual(self.p._state['step_id'],0);self.assertEqual(self.p._geometry.attitude_references['right'],'observed')
    def test_orientation_residual_is_verified_even_when_position_matches(self):
        target,r=self.target('right');original=self.plant
        def drifting(points):
            state=original(points);state['right_joints_deg']=list(state['right_joints_deg']);state['right_joints_deg'][-1]+=3.;return state
        with patch.object(self.p,'_execute',side_effect=drifting) as execute:
            with self.assertRaisesRegex(RuntimeError,'2degrees'):self.bot.move_to(**target,rotation={'right':r})
            self.assertEqual(execute.call_count,6)
            with self.assertRaises(RuntimeError):self.bot.move_to(right_gripper=0.)
            self.assertEqual(execute.call_count,6)
        self.assertGreater(self.p._last_move['orientation_error_deg']['right'],2.)
    def test_rotation_error_is_stable_near_zero_and_threshold(self):
        from remote_yam.roboskills_trajectory import rotation_error_degrees
        for degrees in (0,1e-7,2.-1e-6,2.,2.+1e-6,180):
            a=np.deg2rad(degrees);r=np.array([[np.cos(a),-np.sin(a),0],[np.sin(a),np.cos(a),0],[0,0,1.]])
            self.assertAlmostEqual(rotation_error_degrees(r,np.eye(3)),degrees,places=9)
    def test_reasoning_configuration_matches_actual_codex_calls(self):
        from roboskills.core import llm
        with patch.object(llm,'codex',return_value='```python\nfinish()\n```') as codex:
            self.p._llm=None;self.p._call('system',[]);self.p._call('system',[])
        self.assertEqual([x.kwargs['effort'] for x in codex.call_args_list],['none','low'])
        self.assertEqual(self.p.public_config()['subsequent_reasoning_effort'],'low')
    def test_approach_query_finds_feasible_level_jaw_frame_without_state_change(self):
        self.p._geometry.observe(self.p._state);before=deepcopy(self.p._geometry.start_rotations);cursor=self.p._state['step_id']
        with patch.object(self.p,'_execute') as execute:
            result=self.bot.plan_approach(FIXTURE['sourcePoints'])
            execute.assert_not_called()
        self.assertTrue(result['candidates'])
        self.assertGreater(result['candidates'][0]['tilt_deg'],0)
        self.assertEqual({row['arm'] for row in result['candidates']},{'left','right'})
        for row in result['candidates']:
            rotation=np.asarray(row['rotation'][row['arm']]);self.assertEqual(rotation[2,0],0.)
            self.assertLess(rotation[2,2],0.)
            self.assertGreater(row['targets'][row['arm']+'_z'],FIXTURE['sourcePoints'][row['arm']][2])
        self.assertEqual(cursor,self.p._state['step_id'])
        for a,b in zip(before,self.p._geometry.start_rotations):np.testing.assert_array_equal(a,b)

    def test_approach_batch_uses_one_fresh_snapshot_and_move_rechecks(self):
        state=deepcopy(self.p._state)
        with patch.object(self.p,'_feedback',return_value=state) as feedback,patch.object(self.p,'_execute') as execute:
            options=self.bot.plan_approach(FIXTURE['sourcePoints'])
            self.assertTrue(options['candidates'])
            feedback.assert_called_once()
            execute.assert_not_called()
        chosen=options['candidates'][0]
        with patch.object(self.p,'_feedback',side_effect=RuntimeError('fresh motion feedback required')) as feedback,patch.object(self.p,'_execute') as execute:
            with self.assertRaisesRegex(RuntimeError,'fresh motion feedback required'):
                self.bot.move_to(**chosen['targets'],rotation=chosen['rotation'])
            feedback.assert_called_once()
            execute.assert_not_called()
    def test_approach_clearance_endpoints_and_invalid_neighbours(self):
        for value in (.01,.10):
            result=self.bot.plan_approach(FIXTURE['sourcePoints'],clearance=value)
            self.assertIn('candidates',result)
        for value in (np.nextafter(.01,-np.inf),np.nextafter(.10,np.inf),0.,-.01,float('nan'),float('inf'),True,'0.05'):
            with self.subTest(value=value),patch.object(self.bot,'plan_move') as plan,patch.object(self.p,'_execute') as execute:
                with self.assertRaises(ValueError):self.bot.plan_approach(FIXTURE['sourcePoints'],clearance=value)
                plan.assert_not_called();execute.assert_not_called()
    def test_approach_points_require_consistent_measured_base_coordinates(self):
        bad=[{},[],{'other':[.3,0,.1]},{'right':[True,0,.1]},{'right':[.3,0]},{'right':[.3,float('nan'),.1]},{'right':[.3,0,.1],'left':[.3,0,.1]}]
        for points in bad:
            with self.subTest(points=points),patch.object(self.bot,'plan_move') as plan:
                with self.assertRaises(ValueError):self.bot.plan_approach(points)
                plan.assert_not_called()
    def test_approach_respects_terminal_motion_failure(self):
        self.bot.motion_failure='uncertain motion'
        with patch.object(self.bot,'plan_move') as plan:
            with self.assertRaises(RuntimeError):self.bot.plan_approach(FIXTURE['sourcePoints'])
            plan.assert_not_called()
