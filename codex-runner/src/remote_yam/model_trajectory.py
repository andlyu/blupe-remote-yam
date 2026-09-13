"""Resolve model targets, then apply Robocurve's linear joint-ramp formula."""
import math
import numpy as np
from .session import MAX_COMMANDS, MAX_WAYPOINTS_PER_PACKET
from .ik import IKSolver, IKCommand, ArmIKCommand
from .mujoco_ik import BimanualRelativeIK


def build_model_trajectory(command, observation, first_step_id, solver):
    start = np.array([observation[f'{arm}_joints_deg'] for arm in ('left', 'right')], dtype=float)
    if start.shape != (2, 6) or not np.isfinite(start).all():
        raise ValueError('Model trajectory requires finite 6+6 joint feedback')
    if command.left.mode == 'pose' or command.right.mode == 'pose':
        ik = BimanualRelativeIK()
        seed = ik._safe_baseline(*start)
        ik._forward(seed)
        targets = [(p.copy(), r.copy()) for p, r in ik._poses()]
        for i, arm in enumerate((command.left, command.right)):
            values = np.asarray(arm.values, dtype=float)
            if values.shape != (6,) or not np.isfinite(values).all():
                raise ValueError('Each arm target requires six finite values')
            if arm.mode == 'pose':
                x, y, z, roll, pitch, yaw = values
                cr, sr, cp, sp, cy, sy = math.cos(roll), math.sin(roll), math.cos(pitch), math.sin(pitch), math.cos(yaw), math.sin(yaw)
                rotation = np.array([[cy*cp, cy*sp*sr-sy*cr, cy*sp*cr+sy*sr], [sy*cp, sy*sp*sr+cy*cr, sy*sp*cr-cy*sr], [-sp, cp*sr, cp*cr]])
                targets[i] = (np.array([x, y, z]), rotation)
            elif arm.mode != 'joints':
                raise ValueError('Unsupported arm target mode')
        solved = np.rad2deg(ik._solve(seed, tuple(targets))).reshape(2, 6)
        arms = [ArmIKCommand('joints', tuple(solved[i])) if arm.mode == 'pose' else arm for i, arm in enumerate((command.left, command.right))]
        command = IKCommand(*arms, command.left_gripper, command.right_gripper)
    resolved = solver.resolve(command)
    target = np.array([resolved.left_joints_deg, resolved.right_joints_deg])
    if not np.isfinite(target).all():
        raise ValueError('Model target joints must be finite')
    # Robocurve ramp_waypoints: (1-alpha)*start + alpha*target.
    # Bound each 100 ms step to 0.01 rad; never truncate to fit a packet.
    count = max(1, math.ceil(float(np.max(np.abs(target-start))) / math.degrees(.01)))
    if count > MAX_WAYPOINTS_PER_PACKET:
        raise ValueError('Model trajectory exceeds per-packet waypoint limit')
    if first_step_id + count > MAX_COMMANDS:
        raise ValueError('Model trajectory exceeds session command limit')
    points = []
    for index in range(1, count + 1):
        alpha = index / count
        q = (1-alpha)*start + alpha*target
        point = {'step_id': first_step_id+index-1, 'left_joints_deg': q[0].tolist(), 'right_joints_deg': q[1].tolist()}
        for arm in ('left', 'right'):
            end = getattr(resolved, arm+'_gripper')
            if end is not None:
                initial = observation.get(arm+'_gripper')
                if initial is None:
                    raise ValueError('Gripper target requires measured gripper feedback')
                point[arm+'_gripper'] = (1-alpha)*float(initial)+alpha*end
        points.append(point)
    return points
