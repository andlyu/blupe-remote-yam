"""RoboCurve per-arm Cartesian move_to -> canonical YAM joint packets.

Conventions and pacing: docs/refs/robocurve/INDEX.md, inspect-robots-agent
0.26.0 _tools.py and inspect-robots-yam 0.36.0 kinematics.py.
"""
import math
import numpy as np

from .session import MAX_COMMANDS, MAX_WAYPOINTS_PER_PACKET
from .mujoco_ik import BimanualRelativeIK, IKConvergenceError

ARMS = ('left', 'right')
AXES = ('x', 'y', 'z', 'yaw', 'pitch', 'roll', 'gripper')
NAMES = tuple(f'{arm}_{axis}' for arm in ARMS for axis in AXES)
LOW = np.tile([.15, -.25, .03, -math.pi, 0, 0, 0], 2)
HIGH = np.tile([.48, .25, .4, math.pi, 0, 0, 1], 2)
STEP = .01 * (HIGH - LOW)  # .1 of range/second, sampled at 10 Hz
STEP[[6, 13]] = .1        # one second full gripper stroke
BASES = (np.array([0., .35, 0.]), np.array([0., -.35, 0.]))


class InvalidMove(ValueError):
    """A model target that can be corrected without sending any arm command."""
    def __init__(self, message, code='invalid_tool'):
        super().__init__(message)
        self.code = code


def relative_rotation(yaw, pitch, roll):
    cy, sy, cp, sp, cr, sr = (math.cos(yaw), math.sin(yaw), math.cos(pitch),
                             math.sin(pitch), math.cos(roll), math.sin(roll))
    return (np.array([[cy, -sy, 0], [sy, cy, 0], [0, 0, 1]])
            @ np.array([[cp, 0, -sp], [0, 1, 0], [sp, 0, cp]])
            @ np.array([[1, 0, 0], [0, cr, -sr], [0, sr, cr]]))


class RoboCurveTrajectory:
    def __init__(self):
        self.ik = BimanualRelativeIK()
        self.start_rotations = None

    def observe(self, observation):
        if observation.get('settled') is not True:
            raise RuntimeError('RoboCurve policy requires measured settled feedback')
        q = self.ik._safe_baseline(*(observation[f'{arm}_joints_deg'] for arm in ARMS))
        # FK/reference use the actual measurement, not its limit-clipped IK seed.
        measured = np.deg2rad(np.concatenate([observation[f'{arm}_joints_deg'] for arm in ARMS]))
        self.ik._forward(measured)
        poses = self.ik._poses()
        if self.start_rotations is None:
            self.start_rotations = tuple(r.copy() for _, r in poses)
        state, joints = [], []
        for i, (p, r) in enumerate(poses):
            grip = observation.get(f'{ARMS[i]}_gripper')
            if isinstance(grip, bool) or not isinstance(grip, (int, float)) or not math.isfinite(grip) or not 0 <= grip <= 1:
                raise RuntimeError('RoboCurve policy requires measured normalized gripper feedback')
            d = r @ self.start_rotations[i].T
            state.extend([*(p - BASES[i]), math.atan2(d[1, 0], d[0, 0]),
                          math.asin(float(np.clip(d[2, 0], -1, 1))),
                          math.atan2(d[2, 1], d[2, 2]), grip])
            joints.extend([*measured[i*6:i*6+6], grip])
        return q, np.asarray(state), joints

    def build(self, targets, observation, first_step_id):
        if not isinstance(targets, dict) or not targets:
            raise InvalidMove('targets must be a nonempty map of named dimensions')
        seed, start, _ = self.observe(observation)
        measured_start = np.deg2rad(np.concatenate([observation[f'{arm}_joints_deg'] for arm in ARMS]))
        goal = start.copy()
        named = []
        for name, value in targets.items():
            if name not in NAMES:
                raise InvalidMove(f'Unknown target dimension: {name}', 'unknown_dimension')
            i = NAMES.index(name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise InvalidMove(f'{name} must be a finite number', 'invalid_target_type')
            if not LOW[i] <= value <= HIGH[i]:
                raise InvalidMove(f'{name} must be in [{LOW[i]:.6g}, {HIGH[i]:.6g}]', 'target_out_of_bounds')
            goal[i] = value
            named.append(i)
        # Pin the endpoint, not every intermediate sample. Measured tracking
        # error on a fixed pitch/roll axis must converge gradually; clipping each
        # sample makes an instantaneous correction that subdivision cannot shrink.
        goal = np.clip(goal, LOW, HIGH)
        ratio = max((abs(goal[i]-start[i])/STEP[i] for i in named if STEP[i] > 0), default=0)
        ratio /= 1 - 1e-6  # RoboCurve's numerical headroom
        remaining = max(0, MAX_COMMANDS-first_step_id)

        def budget_error():
            if remaining < MAX_WAYPOINTS_PER_PACKET:
                return InvalidMove(
                    f'This move exceeds the remaining session waypoint budget of {remaining}. '
                    'No motion was sent. Request a smaller intermediate target.',
                    'session_waypoint_budget')
            return InvalidMove(
                f'This move exceeds the per-packet waypoint budget of {MAX_WAYPOINTS_PER_PACKET}. '
                f'The session still has {remaining} waypoints available. '
                'No motion was sent. Request a smaller intermediate target; '
                'the remaining session budget can be used across multiple packets.',
                'packet_waypoint_budget')

        if ratio > MAX_WAYPOINTS_PER_PACKET:
            raise budget_error()
        count = max(1, math.ceil(ratio))
        points = []
        limit = min(MAX_WAYPOINTS_PER_PACKET, remaining)
        if limit < 1:
            raise InvalidMove('Session waypoint budget exhausted', 'session_waypoint_budget')

        def state_at(alpha):
            return start + alpha*(goal-start)

        def extend(a, b, previous, depth=0):
            state = state_at(b)
            poses = tuple((state[i*7:i*7+3] + BASES[i],
                           relative_rotation(*state[i*7+3:i*7+6]) @ self.start_rotations[i])
                          for i in range(2))
            try:
                solved = self.ik._solve(previous, poses)
            except IKConvergenceError as exc:
                raise InvalidMove('Cartesian target is unreachable; request a smaller move', 'unreachable_target') from exc
            previous_command = previous if points else measured_start
            if np.max(np.abs(solved-previous_command)) > .01 + 1e-12:
                if depth >= 10:
                    raise InvalidMove('Cartesian path cannot meet joint pacing; request a smaller move', 'joint_pacing_limit')
                mid = (a+b)/2
                return extend(mid, b, extend(a, mid, previous, depth+1), depth+1)
            if len(points) >= limit:
                raise budget_error()
            degrees = np.rad2deg(solved).reshape(2, 6)
            points.append({'step_id': first_step_id+len(points),
                           'left_joints_deg': degrees[0].tolist(),
                           'right_joints_deg': degrees[1].tolist(),
                           'left_gripper': float(state[6]), 'right_gripper': float(state[13])})
            return solved

        for i in range(count):
            seed = extend(i/count, (i+1)/count, seed)
        return points
