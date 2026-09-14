"""MakerArm position IK from the pinned vendor URDF; no assumed motor zero.

See docs/refs/maker-arm/INDEX.md. FK uses the URDF's native Y-up base_link.
Gripper is normalized actuator travel, NOT a calibrated jaw width.
"""
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET
import numpy as np
from .robocurve_trajectory import InvalidMove
from .session import MAX_COMMANDS, MAX_WAYPOINTS_PER_PACKET

NAMES = ('x', 'y', 'z', 'gripper')
JOINTS = tuple(f'link_{i:03d}_joint' for i in range(2, 8))


def rotation(axis, angle):
    axis = np.asarray(axis, dtype=float)
    axis = axis / np.linalg.norm(axis)
    x, y, z = axis
    skew = np.array([[0, -z, y], [z, 0, -x], [-y, x, 0]])
    return np.eye(3) + math.sin(angle)*skew + (1-math.cos(angle))*(skew@skew)


def origin(node):
    result = np.eye(4)
    result[:3, 3] = np.fromstring(node.get('xyz', '0 0 0'), sep=' ')
    r, p, y = np.fromstring(node.get('rpy', '0 0 0'), sep=' ')
    result[:3, :3] = rotation([0,0,1], y) @ rotation([0,1,0], p) @ rotation([1,0,0], r)
    return result


class MakerArmTrajectory:
    def __init__(self, calibration_path):
        if not calibration_path:
            raise RuntimeError('MakerArm requires a verified motor-to-URDF mapping file')
        config = json.loads(Path(calibration_path).read_text())
        if config.get('verified') is not True:
            raise RuntimeError('MakerArm motor-to-URDF mapping has not been verified')
        self.signs = np.asarray(config['signs'], dtype=float)
        self.offsets = np.asarray(config['offsets_rad'], dtype=float)
        hardware = np.asarray(config['joint_limits_deg'], dtype=float)
        if (self.signs.shape != (6,) or not np.all(np.isin(self.signs, [-1,1]))
                or self.offsets.shape != (6,) or not np.all(np.isfinite(self.offsets))
                or hardware.shape != (6,2) or not np.all(np.isfinite(hardware))
                or not np.all(hardware[:,0] < hardware[:,1])):
            raise RuntimeError('Invalid MakerArm joint mapping/limits')
        tree = ET.parse(Path(__file__).resolve().parents[2] / 'models/makerarm/robot.urdf')
        nodes = {j.get('name'):j for j in tree.findall('joint')}
        self.origins = [origin(nodes[n].find('origin')) for n in JOINTS]
        self.axes = [np.fromstring(nodes[n].find('axis').get('xyz'), sep=' ') for n in JOINTS]
        self.tip = origin(nodes['grasp_center_joint'].find('origin'))
        model_limits = np.array([[float(nodes[n].find('limit').get(k)) for k in ('lower','upper')] for n in JOINTS])
        mapped = np.sort((model_limits-self.offsets[:,None])/self.signs[:,None], axis=1)
        self.limits = np.column_stack((np.maximum(mapped[:,0], np.deg2rad(hardware[:,0])),
                                       np.minimum(mapped[:,1], np.deg2rad(hardware[:,1]))))
        if not np.all(self.limits[:,0] < self.limits[:,1]):
            raise RuntimeError('MakerArm hardware and model limits do not overlap')
        workspace = np.asarray(config['workspace_m'], dtype=float)
        if workspace.shape != (3,2) or not np.all(np.isfinite(workspace)) or not np.all(workspace[:,0] < workspace[:,1]):
            raise RuntimeError('Invalid MakerArm workspace')
        self.low = np.r_[workspace[:,0], 0.]
        self.high = np.r_[workspace[:,1], 1.]

    def forward(self, q):
        transform = np.eye(4)
        for frame, axis, angle in zip(self.origins, self.axes, self.signs*np.asarray(q)+self.offsets):
            motion = np.eye(4)
            motion[:3,:3] = rotation(axis, angle)
            transform = transform @ frame @ motion
        return (transform @ self.tip)[:3,3]

    def observe(self, observation):
        values = observation.get('left_joints_deg')
        if (observation.get('settled') is not True or observation.get('right_joints_deg') != []
                or not isinstance(values, (list,tuple)) or len(values) != 6
                or any(type(v) not in (int,float) or not math.isfinite(v) for v in values)):
            raise RuntimeError('MakerArm requires six finite settled measured joints and no right arm')
        q = np.deg2rad(values)
        grip = observation.get('left_gripper')
        if type(grip) not in (int,float) or not math.isfinite(grip) or not 0 <= grip <= 1:
            raise RuntimeError('MakerArm requires normalized gripper feedback')
        if np.any(q < self.limits[:,0]) or np.any(q > self.limits[:,1]):
            raise RuntimeError('Measured MakerArm joints exceed configured limits; check mapping')
        return q, np.r_[self.forward(q), grip], [*q, grip]

    def solve(self, seed, position):
        q = seed.copy()
        for _ in range(240):
            current = self.forward(q)
            error = position-current
            if np.linalg.norm(error) < 0.00005:
                return q
            jac = np.column_stack([(self.forward(q+np.eye(6)[i]*1e-6)-current)/1e-6 for i in range(6)])
            delta = jac.T @ np.linalg.solve(jac@jac.T+np.eye(3)*1e-6, error)
            delta *= min(1., .04/max(np.max(np.abs(delta)), 1e-12))
            q = np.clip(q+delta, self.limits[:,0], self.limits[:,1])
        raise InvalidMove('MakerArm position is unreachable; choose a nearer target', 'unreachable_target')

    def build(self, targets, observation, first_step_id):
        if not isinstance(targets, dict) or not targets:
            raise InvalidMove('targets must be a nonempty map')
        seed, start, _ = self.observe(observation)
        goal = start.copy()
        for name, value in targets.items():
            if name not in NAMES:
                raise InvalidMove(f'Unknown MakerArm dimension: {name}', 'unknown_dimension')
            i = NAMES.index(name)
            if type(value) not in (int,float) or not math.isfinite(value) or not self.low[i] <= value <= self.high[i]:
                raise InvalidMove(f'{name} exceeds the configured workspace', 'target_out_of_bounds')
            goal[i] = value
        if np.any(start < self.low) or np.any(start > self.high):
            raise InvalidMove('Measured MakerArm pose is outside the configured workspace', 'workspace')
        count = max(1, math.ceil(float(np.max(np.abs(goal-start)/[.002,.002,.002,.01]))))
        limit = min(MAX_WAYPOINTS_PER_PACKET, max(0, MAX_COMMANDS-first_step_id))
        if count > limit:
            raise InvalidMove('Request a smaller move', 'packet_waypoint_budget')
        points = []
        def extend(a, b, previous, depth=0):
            target = start+b*(goal-start)
            q = self.solve(previous, target[:3])
            if np.max(np.abs(q-previous)) > .01+1e-12:
                if depth >= 10:
                    raise InvalidMove('MakerArm path exceeds joint pacing', 'joint_pacing_limit')
                mid = (a+b)/2
                return extend(mid,b,extend(a,mid,previous,depth+1),depth+1)
            if len(points) >= limit:
                raise InvalidMove('Request a smaller move', 'packet_waypoint_budget')
            points.append(dict(step_id=first_step_id+len(points), left_joints_deg=np.rad2deg(q).tolist(),
                               right_joints_deg=[], left_gripper=float(target[3]), right_gripper=None))
            return q
        for i in range(count):
            seed = extend(i/count, (i+1)/count, seed)
        return points
