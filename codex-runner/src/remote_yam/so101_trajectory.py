"""SO101 Cartesian IK; model derived from assets/so101/so101_new_calib.xml.

Uses the upstream gripperframe site, not the wrist body origin. This is
kinematics only: workspace bounds do not constitute collision detection.
LeRobot new-calibration degrees map directly to model radians.
"""
from pathlib import Path
import math
import json
import mujoco
import numpy as np
from .robocurve_trajectory import InvalidMove
from .session import MAX_COMMANDS, MAX_WAYPOINTS_PER_PACKET

NAMES = ('x', 'y', 'z', 'wrist_roll', 'gripper')
JOINTS = ('shoulder_pan', 'shoulder_lift', 'elbow_flex', 'wrist_flex', 'wrist_roll')
# Forward in this model is -Y: allow 44 cm forward from each arm's base.
LOW = np.array([-.4, -.44, -.03, -2.7438472969992493, 0.])
HIGH = np.array([.4, .4, .5, 2.841206309382605, 1.])
STEP = np.array([.002, .002, .002, .01, .01])


def load_joint_profile(robot_id):
    """Named public limits only; never apply one arm's limits to an unknown robot."""
    path = Path(__file__).resolve().parents[2] / 'models/so101/joint_profiles.json'
    return json.loads(path.read_text()).get(robot_id)


class SO101Trajectory:
    """Validate and solve a complete packet before returning any joint commands."""
    def __init__(self, calibration_path=None, *, joint_profile=None):
        path = Path(__file__).resolve().parents[2] / 'models/so101/kinematics.xml'
        self.model = mujoco.MjModel.from_xml_path(str(path))
        self.data = mujoco.MjData(self.model)
        self.ids = np.array([self.model.joint(n).id for n in JOINTS])
        self.qids = self.model.jnt_qposadr[self.ids]
        self.dids = self.model.jnt_dofadr[self.ids]
        self.limits = self.model.jnt_range[self.ids]
        self.site = self.model.site('gripperframe').id
        self.calibration_path = str(calibration_path) if calibration_path else None
        if calibration_path:
            calibration = json.loads(Path(calibration_path).read_text())
            limits = []
            for index, name in enumerate(JOINTS, 1):
                motor = calibration.get(name, {})
                lo, hi = motor.get('range_min'), motor.get('range_max')
                if (motor.get('id') != index or type(lo) is not int or type(hi) is not int
                        or not 0 <= lo < hi <= 4095):
                    raise RuntimeError(f'Invalid SO101 calibration range for {name}')
                # LeRobot motors_bus._normalize DEGREES: midpoint-centered,
                # 360/(resolution-1), where STS3215 resolution is 4096.
                half = math.radians((hi-lo)*180/4095)
                limits.append([-half, half])
            self.limits = np.asarray(limits)
            self.model.jnt_range[self.ids] = self.limits
        if joint_profile is not None:
            if calibration_path:
                raise RuntimeError('Provide SO101 joint limits or calibration, not both')
            if (not isinstance(joint_profile, dict)
                    or joint_profile.get('schema_version') != 1
                    or joint_profile.get('joint_names') != list(JOINTS)
                    or joint_profile.get('convention') != 'lerobot_midpoint_degrees'):
                raise RuntimeError('Unsupported SO101 joint convention or joint order')
            limits = joint_profile.get('joint_limits_deg')
            if (not isinstance(limits, list) or len(limits) != 5 or any(
                    not isinstance(pair, list) or len(pair) != 2
                    or any(type(v) not in (int, float) or not math.isfinite(v) for v in pair)
                    or not -180 <= pair[0] < pair[1] <= 180 for pair in limits)):
                raise RuntimeError('Invalid SO101 joint limits')
            self.limits = np.deg2rad(limits)
            self.model.jnt_range[self.ids] = self.limits
        self.low, self.high = LOW.copy(), HIGH.copy()
        self.low[3], self.high[3] = self.limits[4]


    def forward(self, q):
        self.data.qpos[self.qids] = q
        mujoco.mj_forward(self.model, self.data)
        return self.data.site_xpos[self.site].copy()

    def observe(self, observation):
        if observation.get('settled') is not True:
            raise RuntimeError('SO101 requires settled measured feedback')
        values = observation.get('left_joints_deg')
        if not isinstance(values, (list, tuple)) or len(values) != 5 or any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
            raise RuntimeError('SO101 requires five finite measured joint angles')
        if observation.get('right_joints_deg') != []:
            raise RuntimeError('SO101 has no right arm')
        q = np.deg2rad(values)
        if np.any(q < self.limits[:, 0]) or np.any(q > self.limits[:, 1]):
            raise RuntimeError('SO101 measured joints exceed model limits; verify calibration/model alignment before Cartesian control')
        grip = observation.get('left_gripper')
        if type(grip) not in (int, float) or not math.isfinite(grip) or not 0 <= grip <= 1:
            raise RuntimeError('SO101 requires normalized measured gripper feedback')
        return q, np.array([*self.forward(q), q[4], grip]), [*q, grip]

    def solve(self, seed, position, roll):
        q = seed.copy()
        q[4] = roll
        jac = np.zeros((3, self.model.nv))
        for _ in range(180):
            error = position - self.forward(q)
            if np.linalg.norm(error) <= 0.00005:
                return q
            mujoco.mj_jacSite(self.model, self.data, jac, None, self.site)
            j = jac[:, self.dids[:4]]
            delta = j.T @ np.linalg.solve(j @ j.T + np.eye(3)*1e-6, error)
            delta *= min(1., .08/max(np.max(np.abs(delta)), 1e-12))
            q[:4] = np.clip(q[:4]+delta, self.limits[:4, 0], self.limits[:4, 1])
        raise InvalidMove('SO101 target is unreachable with this wrist roll; choose a smaller or different target', 'unreachable_target')

    def build(self, targets, observation, first_step_id):
        if not isinstance(targets, dict) or not targets:
            raise InvalidMove('targets must be a nonempty map')
        seed, start, _ = self.observe(observation)
        goal = start.copy()
        for name, value in targets.items():
            if name not in NAMES:
                raise InvalidMove(f'Unknown SO101 dimension: {name}', 'unknown_dimension')
            i = NAMES.index(name)
            if type(value) not in (int, float) or not math.isfinite(value) or not self.low[i] <= value <= self.high[i]:
                raise InvalidMove(f'{name} must be within [{self.low[i]}, {self.high[i]}]', 'target_out_of_bounds')
            goal[i] = value
        # Refuse paths beginning outside the configured workspace; do not silently
        # clip the measurement or command an unexpected recovery motion.
        if np.any(start[:3] < LOW[:3]) or np.any(start[:3] > HIGH[:3]):
            raise InvalidMove('Measured grasp point is outside the Cartesian workspace; verify setup first', 'workspace')
        count = max(1, math.ceil(float(np.max(np.abs(goal-start)/STEP))))
        limit = min(MAX_WAYPOINTS_PER_PACKET, max(0, MAX_COMMANDS-first_step_id))
        if count > limit:
            raise InvalidMove('Move exceeds waypoint budget; request a smaller intermediate target', 'packet_waypoint_budget')
        points = []
        def extend(a, b, previous, depth=0):
            state = start + b*(goal-start)
            q = self.solve(previous, state[:3], state[3])
            if np.max(np.abs(q-previous)) > .01+1e-12:
                if depth >= 10:
                    raise InvalidMove('SO101 path exceeds joint pacing', 'joint_pacing_limit')
                mid = (a+b)/2
                return extend(mid, b, extend(a, mid, previous, depth+1), depth+1)
            if len(points) >= limit:
                raise InvalidMove('Move exceeds waypoint budget; request a smaller intermediate target', 'packet_waypoint_budget')
            points.append({'step_id': first_step_id+len(points), 'left_joints_deg': np.rad2deg(q).tolist(),
                           'right_joints_deg': [], 'left_gripper': float(state[4]), 'right_gripper': None})
            return q
        for i in range(count):
            seed = extend(i/count, (i+1)/count, seed)
        return points
