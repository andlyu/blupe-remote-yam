"""Retained physical tracking residual -> synthetic regression, no hardware."""
import importlib.util
import json
from pathlib import Path
from unittest import TestCase, skipUnless
from unittest.mock import patch
import numpy as np
from remote_yam.robocurve_trajectory import RoboCurveTrajectory
from remote_yam.roboskills_trajectory import RoboSkillsTrajectory
from remote_yam.roboskills_policy import _tracking_offset
import test_roboskills_commands as contract
from test_roboskills_policy import observation


@skipUnless(importlib.util.find_spec('roboskills'),'Install the pinned RoboSkills runtime')
class TrackingTests(TestCase):
    setUp=contract.CommandContract.setUp
    tearDown=contract.CommandContract.tearDown
    plant=contract.CommandContract.plant

    def test_recorded_approach_residual_converges_within_original_bounds(self):
        self.exercise_fixture('roboskills-approach-tracking.json')

    def test_recorded_reverse_response_discards_obsolete_bracket(self):
        fixture=json.loads((Path(__file__).parent/'fixtures/roboskills-tilted-tracking.json').read_text())
        rows=fixture['samples'][:3]
        history=[(np.array(r['command_offset_m']),np.array(r['measured_error_m'])) for r in rows]
        # Actual X/Z responses reversed sign relative to the completed command.
        # An older opposite residual no longer describes this travel branch.
        correction=_tracking_offset(history,range(3))
        for axis in (0,2):
            self.assertLess(correction[axis],history[-1][0][axis]-.49*history[-1][1][axis])
        self.assertLessEqual(np.max(np.abs(correction)),.008)

    def test_recorded_tilted_approach_with_explicit_reverse_travel_model(self):
        fixture=json.loads((Path(__file__).parent/'fixtures/roboskills-tilted-tracking.json').read_text())
        self.p._geometry=RoboSkillsTrajectory(base_spacing_m=.5,preserve_uninstructed=True)
        self.p._geometry.observe(fixture['reference']);self.p._state={**fixture['reference'],'episode_id':'ep_unit','lease_id':'lease_unit'}
        samples=fixture['samples'];goal=None;commands=[]
        # Past completed samples are measured. Subsequent unit-gain bias is an
        # explicit model of the changed travel branch, not a physical forecast.
        bias=np.array(samples[2]['measured_error_m'])-samples[2]['command_offset_m']
        def measured_plant(points):
            nonlocal goal
            wire_end=self.plant(points)
            _,cmd,_=self.p._geometry.observe(wire_end)
            if goal is None:goal=cmd[:3].copy()
            offset=cmd[:3]-goal;commands.append(offset)
            index=len(commands)-1
            if index<3:
                measured=samples[index]['measured']
            else:
                xyz=goal+offset+bias
                synthetic=self.p._geometry.build(dict(zip(('left_x','left_y','left_z'),xyz.tolist())),self.p._state,self.p._state['step_id'])
                measured={k:synthetic[-1][k] for k in ('left_joints_deg','right_joints_deg')}
            return {**wire_end,**measured,'settled':True}
        with patch.object(self.p,'_execute',side_effect=measured_plant):
            self.bot.move_to(**fixture['targets'],rotation=fixture['rotation'])
        self.assertLessEqual(len(commands),6)
        self.assertLessEqual(np.max(np.abs(commands)),.008+1e-7)
        self.assertLessEqual(self.p._last_move['position_error_m']['left'],.003)
        self.assertLessEqual(self.p._last_move['orientation_error_deg']['left'],2.)
        self.assertIsNone(self.bot.motion_failure)

    def exercise_fixture(self, name, deadband_breakout=None):
        fixture=json.loads((Path(__file__).parent/'fixtures'/name).read_text())
        self.p._geometry=RoboCurveTrajectory(base_spacing_m=.5,preserve_uninstructed=True)
        self.p._geometry.observe(fixture['reference']);self.p._state=fixture['start']
        goal=None;commands=[]
        samples=fixture['samples']
        def measured_plant(points):
            nonlocal goal
            # Validate/serialize the actual generated command before supplying
            # synthetic feedback interpolated from retained completed samples.
            wire_end=self.plant(points)
            _,cmd,_=self.p._geometry.observe(wire_end)
            if goal is None:goal=cmd[7:10].copy()
            offset=cmd[7:10]-goal;commands.append(offset)
            error=[]
            for axis in range(3):
                ordered=sorted((s['command_offset_m'][axis],s['measured_error_m'][axis]) for s in samples)
                error.append(np.interp(offset[axis],*[np.array(v) for v in zip(*ordered)]))
            if deadband_breakout is not None and len(commands)>1:
                # Measured reverse commands over about1mm gave almost no Y
                # response. Model several possible breakout offsets; these are
                # explicit regression assumptions, not physical measurements.
                error[1]=(samples[-1]['measured_error_m'][1] if offset[1]<deadband_breakout
                          else samples[0]['measured_error_m'][1]+offset[1])
            xyz=goal+error
            synthetic=self.p._geometry.build(dict(zip(('right_x','right_y','right_z'),xyz.tolist())),self.p._state,self.p._state['step_id'])
            return {**wire_end,**{k:synthetic[-1][k] for k in ('left_joints_deg','right_joints_deg')},'settled':True}
        with patch.object(self.p,'_execute',side_effect=measured_plant):
            self.bot.move_to(**fixture['targets'])
        self.assertLessEqual(len(commands),6)
        self.assertLessEqual(np.max(np.abs(commands)),.008+1e-7)
        self.assertLessEqual(self.p._last_move['position_error_m']['right'],.003)
        self.assertIsNone(self.bot.motion_failure)

    def test_recorded_transfer_does_not_shrink_an_unresponsive_bracket(self):
        for breakout in (0.,.0005,.001):
            with self.subTest(modeled_breakout_m=breakout):
                self.p=type(self.p)(camera_source=contract.Cameras(),recording_root=self.root.name,llm=lambda s,m:'')
                self.p._identity=('ep_unit','lease_unit')
                self.bot=type(self.bot)(self.p,'offline transfer regression')
                self.exercise_fixture('roboskills-transfer-tracking.json',breakout)

    def test_gripper_only_command_reports_pose_drift_without_inventing_a_pose_target(self):
        before=observation();self.p._state=before
        pose=self.bot.status()['coordinates']
        def measured(points):
            end=self.plant(points)
            drift=self.p._geometry.build({'right_z':pose['right_z']+.005},before,0)[-1]
            return {**end,'right_joints_deg':drift['right_joints_deg']}
        with patch.object(self.p,'_execute',side_effect=measured) as execute:
            result=self.bot.move_to(right_gripper=0.)
        execute.assert_called_once()
        self.assertGreater(self.p._last_move['position_error_m']['right'],.004)
        self.assertEqual(self.p._last_move['pose_target_arms'],[])
        self.assertIn('coordinates',result)
        for waypoint in self.sent[0]['waypoints']:
            for side in ('left','right'):self.assertEqual(waypoint[side+'_joints_deg'],before[side+'_joints_deg'])
