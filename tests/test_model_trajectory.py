import unittest
import math
import numpy as np
from remote_yam.ik import IKCommand, ArmIKCommand, IKSolver
from remote_yam.mujoco_ik import BimanualRelativeIK
from remote_yam.model_trajectory import build_model_trajectory

class ModelTrajectoryTests(unittest.TestCase):
    def test_cartesian_target_becomes_bounded_joint_waypoints(self):
        ik = BimanualRelativeIK()
        home = np.array([-.024,.794,.645,-.375,-.021,-.012]*2)
        ik._forward(home)
        targets = []
        for p, r in ik._poses():
            targets.append(ArmIKCommand('pose', (*p[:2], p[2]+.005, math.atan2(r[2,1],r[2,2]), math.asin(-r[2,0]), math.atan2(r[1,0],r[0,0]))))
        degrees = np.rad2deg(home).reshape(2,6)
        obs = dict(zip(('left_joints_deg','right_joints_deg'),degrees.tolist()))
        points = build_model_trajectory(IKCommand(*targets),obs,7,IKSolver())
        self.assertEqual([p['step_id'] for p in points],list(range(7,7+len(points))))
        path = np.array([degrees.tolist()]+[[p['left_joints_deg'],p['right_joints_deg']] for p in points])
        self.assertLessEqual(np.max(np.abs(np.diff(np.deg2rad(path),axis=0))),.010001)
        ik._forward(np.deg2rad(path[-1].reshape(12)))
        for (p,r), target in zip(ik._poses(),targets):
            np.testing.assert_allclose(p,target.values[:3],atol=.001)
        self.assertTrue(all(set(p)=={'step_id','left_joints_deg','right_joints_deg'} for p in points))
