"""Two independent calibrated SO101 IK solvers, joined into one atomic packet."""
import json
from pathlib import Path
import numpy as np
from .so101_trajectory import SO101Trajectory, NAMES as ARM_NAMES
from .robocurve_trajectory import InvalidMove

SIDES = ('left', 'right')
NAMES = tuple(f'{side}_{name}' for side in SIDES for name in ARM_NAMES)


class BimanualSO101Trajectory:
    def __init__(self, calibration_path=None, *, joint_profile=None):
        if calibration_path is not None:
            if joint_profile is not None:
                raise RuntimeError('Provide bimanual joint limits or calibration, not both')
            manifest = Path(calibration_path)
            paths = json.loads(manifest.read_text())
            if set(paths) != set(SIDES) or any(not isinstance(p, str) or not p for p in paths.values()):
                raise RuntimeError('Bimanual SO101 calibration must name left and right calibration files')
            self.arms = {side: SO101Trajectory(manifest.parent / paths[side]) for side in SIDES}
        else:
            if not isinstance(joint_profile, dict) or set(joint_profile) != set(SIDES) or any(not isinstance(v, dict) for v in joint_profile.values()):
                raise RuntimeError('Bimanual SO101 joint limits must specify both left and right arms')
            self.arms = {side: SO101Trajectory(joint_profile=joint_profile[side]) for side in SIDES}

    @staticmethod
    def arm_observation(observation, side):
        return {**observation, 'left_joints_deg': observation.get(side+'_joints_deg'),
                'right_joints_deg': [], 'left_gripper': observation.get(side+'_gripper'),
                'right_gripper': None}

    def observe(self, observation):
        states = [self.arms[s].observe(self.arm_observation(observation, s)) for s in SIDES]
        return np.concatenate([s[0] for s in states]), np.concatenate([s[1] for s in states]), [v for s in states for v in s[2]]

    def build(self, targets, observation, first_step_id):
        if not isinstance(targets, dict) or not targets:
            raise InvalidMove('targets must be a nonempty map')
        if set(targets)-set(NAMES):
            raise InvalidMove('Unknown bimanual SO101 target dimension', 'unknown_dimension')
        # Validate feedback and solve BOTH complete paths before returning any packet.
        self.observe(observation)
        paths = {}
        for side in SIDES:
            selected = {k[len(side)+1:]: v for k,v in targets.items() if k.startswith(side+'_')}
            arm_obs = self.arm_observation(observation, side)
            paths[side] = (self.arms[side].build(selected, arm_obs, first_step_id) if selected else
                [{'left_joints_deg': list(arm_obs['left_joints_deg']), 'left_gripper': arm_obs['left_gripper']}])
        count = max(map(len, paths.values()))
        # An unrequested arm holds measured joints. A shorter path holds its endpoint.
        points = []
        for i in range(count):
            point = {'step_id': first_step_id+i}
            for side in SIDES:
                source = paths[side][min(i, len(paths[side])-1)]
                point[side+'_joints_deg'] = list(source['left_joints_deg'])
                point[side+'_gripper'] = source['left_gripper']
            points.append(point)
        return points
