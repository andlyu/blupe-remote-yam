"""Observation-relative, simulation-only bimanual YAM trajectory generation."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
from pathlib import Path
from typing import Sequence

import mujoco
import numpy as np


SCENE_SHA256 = "7f82c3ff37df703978064b74c0dfb637449613640211e2e27a25dc6b10072f1b"
ROBOT_SHA256 = "273d44b2829b433f8913abaf883419ca88170762bda3d50faa33fe886b73b42c"
LOW_RAD = np.array([-2.61799, 0.0, 0.0, -1.5708, -1.5708, -2.0944])
HIGH_RAD = np.array([3.05433, 3.65, 3.66519, 1.5708, 1.5708, 2.0944])
POSITION_TOLERANCE_M = 0.002
ORIENTATION_TOLERANCE_RAD = 0.01
INTERNAL_POSITION_TOLERANCE_M = 2e-6
INTERNAL_ORIENTATION_TOLERANCE_RAD = 2e-5
BASELINE_BOUND_TOLERANCE_RAD = 0.002
MAX_WAYPOINT_DELTA_RAD = 0.06
HARD_WAYPOINT_DELTA_RAD = 0.2
TARGET_WAYPOINT_DELTA_RAD = 0.03
REQUESTED_APEX_M = 0.20
MINIMUM_APEX_M = 0.19
COARSE_CARTESIAN_INCREMENT_M = 0.01
MINIMUM_CARTESIAN_INCREMENT_M = 0.00025
MAX_SUBDIVISION_DEPTH = 8
MAX_TRAJECTORY_COMMANDS = 300


def default_scene_path() -> Path:
    return Path(__file__).resolve().parents[2] / "models" / "yam_bimanual" / "scene.xml"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rotation_log(rotation: np.ndarray) -> np.ndarray:
    cosine = float(np.clip((np.trace(rotation) - 1.0) * 0.5, -1.0, 1.0))
    angle = math.acos(cosine)
    skew = np.array(
        [rotation[2, 1] - rotation[1, 2], rotation[0, 2] - rotation[2, 0], rotation[1, 0] - rotation[0, 1]]
    )
    if angle < 1e-8:
        return 0.5 * skew
    return angle * skew / (2.0 * math.sin(angle))


@dataclass(frozen=True)
class SubdivisionDiagnostic:
    arm: str
    requested_waypoint: int
    joint_index: int
    attempted_delta_rad: float
    subdivision_depth: int


@dataclass(frozen=True)
class ReachLimitDiagnostic:
    last_solved_apex_m: float
    attempted_apex_m: float
    subdivision_depth: int


@dataclass(frozen=True)
class RelativeTrajectory:
    left_waypoints_deg: tuple[tuple[float, ...], ...]
    right_waypoints_deg: tuple[tuple[float, ...], ...]
    baseline_left_deg: tuple[float, ...]
    baseline_right_deg: tuple[float, ...]
    baseline_positions: tuple[tuple[float, float, float], tuple[float, float, float]]
    baseline_rotations: tuple[tuple[float, ...], tuple[float, ...]]
    requested_apex_m: float
    achieved_apex_m: float
    max_accepted_delta_rad: float
    max_delta_arm: str
    max_delta_joint_index: int
    max_delta_waypoint: int
    minimum_cartesian_increment_m: float
    subdivisions: tuple[SubdivisionDiagnostic, ...]
    reach_limit: ReachLimitDiagnostic | None

    @property
    def length(self) -> int:
        return len(self.left_waypoints_deg)

    def diagnostics(self) -> dict[str, object]:
        return {
            "requested_apex_m": self.requested_apex_m,
            "achieved_apex_m": self.achieved_apex_m,
            "shortfall_m": self.requested_apex_m - self.achieved_apex_m,
            "final_waypoint_count": self.length,
            "max_accepted_delta_rad": self.max_accepted_delta_rad,
            "max_delta_arm": self.max_delta_arm,
            "max_delta_joint_index": self.max_delta_joint_index,
            "max_delta_waypoint": self.max_delta_waypoint,
            "minimum_cartesian_subdivision_m": self.minimum_cartesian_increment_m,
            "endpoint_reach_limit": self.reach_limit is not None,
            "reach_limit": None if self.reach_limit is None else {
                "last_solved_apex_m": self.reach_limit.last_solved_apex_m,
                "attempted_apex_m": self.reach_limit.attempted_apex_m,
                "subdivision_depth": self.reach_limit.subdivision_depth,
            },
            "subdivisions": [
                {
                    "arm": item.arm,
                    "requested_waypoint": item.requested_waypoint,
                    "joint_index": item.joint_index,
                    "attempted_delta_rad": item.attempted_delta_rad,
                    "subdivision_depth": item.subdivision_depth,
                }
                for item in self.subdivisions
            ],
        }


class IKConvergenceError(RuntimeError):
    pass


class BimanualRelativeIK:
    """Build a +20 cm world-Z path from the observed pose, then exactly reverse it."""

    def __init__(self, scene_path: Path | str | None = None) -> None:
        self.scene_path = Path(scene_path) if scene_path else default_scene_path()
        robot_path = self.scene_path.with_name("yam_bimanual.xml")
        if _sha256(self.scene_path) != SCENE_SHA256 or _sha256(robot_path) != ROBOT_SHA256:
            raise RuntimeError("Canonical YAM simulation model hash mismatch")
        self.model = mujoco.MjModel.from_xml_path(str(self.scene_path))
        self.data = mujoco.MjData(self.model)
        joint_names = [f"{side}_joint{index}" for side in ("left", "right") for index in range(1, 7)]
        joint_ids = [mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, name) for name in joint_names]
        if any(joint_id < 0 for joint_id in joint_ids):
            raise RuntimeError("Canonical YAM joints are missing")
        self.qpos_indices = np.array([self.model.jnt_qposadr[joint_id] for joint_id in joint_ids], dtype=int)
        self.dof_indices = np.array([self.model.jnt_dofadr[joint_id] for joint_id in joint_ids], dtype=int)
        if not np.array_equal(self.qpos_indices, np.arange(12)):
            raise RuntimeError("Canonical YAM qpos order is not explicit left-then-right 6+6")
        self.body_ids = tuple(
            mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, name)
            for name in ("left_grasp", "right_grasp")
        )
        if any(body_id < 0 for body_id in self.body_ids):
            raise RuntimeError("Canonical YAM end-effector bodies are missing")
        model_bounds = self.model.jnt_range[joint_ids]
        expected_bounds = np.column_stack((np.tile(LOW_RAD, 2), np.tile(HIGH_RAD, 2)))
        if not np.allclose(model_bounds, expected_bounds, atol=1e-8, rtol=0.0):
            raise RuntimeError("Canonical YAM XML joint limits do not match the runner contract")
        self.low = model_bounds[:, 0].copy()
        self.high = model_bounds[:, 1].copy()

    @staticmethod
    def _six(values: Sequence[float], label: str) -> np.ndarray:
        result = np.asarray(values, dtype=float)
        if result.shape != (6,) or not np.all(np.isfinite(result)):
            raise RuntimeError(f"{label} must contain six finite joint values")
        return result

    def _forward(self, joints_rad: np.ndarray) -> None:
        self.data.qpos[self.qpos_indices] = joints_rad
        self.data.qvel[:] = 0.0
        mujoco.mj_forward(self.model, self.data)

    def _poses(self) -> tuple[tuple[np.ndarray, np.ndarray], tuple[np.ndarray, np.ndarray]]:
        return tuple(
            (self.data.xpos[body_id].copy(), self.data.xmat[body_id].copy().reshape(3, 3))
            for body_id in self.body_ids
        )  # type: ignore[return-value]

    def _safe_baseline(self, left_deg: Sequence[float], right_deg: Sequence[float]) -> np.ndarray:
        observed = np.deg2rad(np.concatenate((self._six(left_deg, "left_joints_deg"), self._six(right_deg, "right_joints_deg"))))
        clipped = np.clip(observed, self.low, self.high)
        if float(np.max(np.abs(observed - clipped))) > BASELINE_BOUND_TOLERANCE_RAD:
            raise RuntimeError("Observed baseline exceeds canonical YAM joint bounds")
        return clipped

    def _solve(self, seed: np.ndarray, targets: tuple[tuple[np.ndarray, np.ndarray], ...]) -> np.ndarray:
        q = seed.copy()
        for _ in range(250):
            self._forward(q)
            current = self._poses()
            errors: list[np.ndarray] = []
            jacobian = np.zeros((12, 12), dtype=float)
            for arm, body_id in enumerate(self.body_ids):
                target_position, target_rotation = targets[arm]
                position, rotation = current[arm]
                errors.extend((target_position - position, _rotation_log(target_rotation @ rotation.T)))
                jacp = np.zeros((3, self.model.nv), dtype=float)
                jacr = np.zeros((3, self.model.nv), dtype=float)
                mujoco.mj_jacBody(self.model, self.data, jacp, jacr, body_id)
                columns = self.dof_indices[arm * 6 : arm * 6 + 6]
                jacobian[arm * 6 : arm * 6 + 3, arm * 6 : arm * 6 + 6] = jacp[:, columns]
                jacobian[arm * 6 + 3 : arm * 6 + 6, arm * 6 : arm * 6 + 6] = jacr[:, columns]
            error = np.concatenate(errors)
            if all(np.linalg.norm(error[arm * 6 : arm * 6 + 3]) < INTERNAL_POSITION_TOLERANCE_M for arm in range(2)) and all(
                np.linalg.norm(error[arm * 6 + 3 : arm * 6 + 6]) < INTERNAL_ORIENTATION_TOLERANCE_RAD for arm in range(2)
            ):
                if self.data.ncon:
                    raise RuntimeError("Predictive YAM model contact detected")
                return q
            system = jacobian @ jacobian.T + 2e-3 * np.eye(12)
            dq = jacobian.T @ np.linalg.solve(system, error)
            q = np.clip(q + np.clip(dq, -0.05, 0.05), self.low, self.high)
        raise IKConvergenceError("Observation-relative bimanual IK did not converge")

    def _targets(
        self,
        baseline_poses: tuple[tuple[np.ndarray, np.ndarray], tuple[np.ndarray, np.ndarray]],
        lift_m: float,
    ) -> tuple[tuple[np.ndarray, np.ndarray], ...]:
        return tuple(
            (position + np.array([0.0, 0.0, lift_m]), rotation)
            for position, rotation in baseline_poses
        )

    def _validate_path(
        self,
        path: list[np.ndarray],
        lifts: list[float],
        baseline: np.ndarray,
        baseline_poses: tuple[tuple[np.ndarray, np.ndarray], tuple[np.ndarray, np.ndarray]],
    ) -> None:
        if not path or len(path) != len(lifts) or len(path) > MAX_TRAJECTORY_COMMANDS:
            raise RuntimeError("Observation-relative IK trajectory exceeds max_steps")
        packed = np.asarray(path, dtype=float)
        if packed.shape != (len(path), 12) or not np.all(np.isfinite(packed)):
            raise RuntimeError("Observation-relative IK trajectory contains malformed joint values")
        if np.any(packed < self.low - 1e-10) or np.any(packed > self.high + 1e-10):
            raise RuntimeError("Observation-relative IK trajectory exceeds XML joint limits")
        if not np.array_equal(path[0], baseline) or not np.array_equal(path[-1], baseline):
            raise RuntimeError("Observation-relative IK trajectory does not return exactly to baseline")
        midpoint = (len(path) + 1) // 2
        for index in range(midpoint):
            if not np.array_equal(path[index], path[-1 - index]):
                raise RuntimeError("Observation-relative IK downward path is not an exact reverse")
        if any(abs(lifts[index] - lifts[-1 - index]) > 1e-12 for index in range(midpoint)):
            raise RuntimeError("Observation-relative IK Cartesian path is not an exact reverse")

        for waypoint, (joints, lift_m) in enumerate(zip(path, lifts)):
            self._forward(joints)
            if self.data.ncon:
                raise RuntimeError(f"Predictive YAM model contact detected at waypoint {waypoint}")
            current = self._poses()
            for arm, ((baseline_position, baseline_rotation), (position, rotation)) in enumerate(
                zip(baseline_poses, current)
            ):
                target_position = baseline_position + np.array([0.0, 0.0, lift_m])
                if float(np.linalg.norm(target_position - position)) > POSITION_TOLERANCE_M:
                    raise RuntimeError(f"Observation-relative IK workspace error at {'left' if arm == 0 else 'right'} waypoint {waypoint}")
                if float(np.linalg.norm(_rotation_log(baseline_rotation @ rotation.T))) > ORIENTATION_TOLERANCE_RAD:
                    raise RuntimeError(f"Observation-relative IK orientation error at {'left' if arm == 0 else 'right'} waypoint {waypoint}")

        for waypoint, (previous, current) in enumerate(zip(path, path[1:]), start=1):
            delta = float(np.max(np.abs(current - previous)))
            if delta > MAX_WAYPOINT_DELTA_RAD or delta > HARD_WAYPOINT_DELTA_RAD:
                raise RuntimeError(f"Observation-relative IK hard continuity failure at waypoint {waypoint}: {delta:.9f} rad")
            if delta > TARGET_WAYPOINT_DELTA_RAD + 1e-12:
                raise RuntimeError(f"Observation-relative IK adaptive target missed at waypoint {waypoint}: {delta:.9f} rad")

    def build(self, left_deg: Sequence[float], right_deg: Sequence[float]) -> RelativeTrajectory:
        baseline = self._safe_baseline(left_deg, right_deg)
        self._forward(baseline)
        if self.data.ncon:
            raise RuntimeError("Observed YAM baseline has predictive model contacts")
        baseline_poses = self._poses()
        upward = [baseline.copy()]
        upward_lifts = [0.0]
        subdivisions: list[SubdivisionDiagnostic] = []

        def extend(
            start_lift: float,
            end_lift: float,
            seed: np.ndarray,
            depth: int,
            requested_waypoint: int,
        ) -> tuple[np.ndarray, float, ReachLimitDiagnostic | None]:
            try:
                solved = self._solve(seed, self._targets(baseline_poses, end_lift))
            except IKConvergenceError:
                midpoint = (start_lift + end_lift) * 0.5
                if depth >= MAX_SUBDIVISION_DEPTH or midpoint - start_lift < MINIMUM_CARTESIAN_INCREMENT_M:
                    return seed, start_lift, ReachLimitDiagnostic(start_lift, end_lift, depth)
                midpoint_solution, reached, limit = extend(
                    start_lift, midpoint, seed, depth + 1, requested_waypoint
                )
                if limit is not None:
                    return midpoint_solution, reached, limit
                return extend(midpoint, end_lift, midpoint_solution, depth + 1, requested_waypoint)

            deltas = np.abs(solved - seed)
            flat_joint = int(np.argmax(deltas))
            attempted_delta = float(deltas[flat_joint])
            if attempted_delta <= TARGET_WAYPOINT_DELTA_RAD:
                if 2 * (len(upward) + 1) - 1 > MAX_TRAJECTORY_COMMANDS:
                    raise RuntimeError("Observation-relative IK adaptive trajectory exceeds max_steps")
                upward.append(solved.copy())
                upward_lifts.append(end_lift)
                return solved, end_lift, None

            diagnostic = SubdivisionDiagnostic(
                arm="left" if flat_joint < 6 else "right",
                requested_waypoint=requested_waypoint,
                joint_index=flat_joint % 6 + 1,
                attempted_delta_rad=attempted_delta,
                subdivision_depth=depth,
            )
            subdivisions.append(diagnostic)
            midpoint = (start_lift + end_lift) * 0.5
            if depth >= MAX_SUBDIVISION_DEPTH or midpoint - start_lift < MINIMUM_CARTESIAN_INCREMENT_M:
                raise RuntimeError(
                    "Observation-relative IK continuity cannot be subdivided: "
                    f"arm={diagnostic.arm} waypoint={diagnostic.requested_waypoint} "
                    f"joint_index={diagnostic.joint_index} attempted_delta_rad={diagnostic.attempted_delta_rad:.9f} "
                    f"subdivision_depth={diagnostic.subdivision_depth}"
                )
            midpoint_solution, reached, limit = extend(
                start_lift, midpoint, seed, depth + 1, requested_waypoint
            )
            if limit is not None:
                return midpoint_solution, reached, limit
            return extend(midpoint, end_lift, midpoint_solution, depth + 1, requested_waypoint)

        seed = baseline.copy()
        reached_lift = 0.0
        reach_limit: ReachLimitDiagnostic | None = None
        coarse_waypoints = int(round(REQUESTED_APEX_M / COARSE_CARTESIAN_INCREMENT_M))
        for index in range(1, coarse_waypoints + 1):
            target_lift = min(REQUESTED_APEX_M, COARSE_CARTESIAN_INCREMENT_M * index)
            seed, reached_lift, reach_limit = extend(reached_lift, target_lift, seed, 0, index)
            if reach_limit is not None:
                break
        if reached_lift < MINIMUM_APEX_M or reached_lift > REQUESTED_APEX_M + 1e-12:
            detail = "none" if reach_limit is None else (
                f"attempted_apex_m={reach_limit.attempted_apex_m:.9f} "
                f"subdivision_depth={reach_limit.subdivision_depth}"
            )
            raise RuntimeError(
                "Observation-relative IK cannot reach the accepted apex: "
                f"requested_apex_m={REQUESTED_APEX_M:.5f} achieved_apex_m={reached_lift:.9f} {detail}"
            )
        path = upward + list(reversed(upward[:-1]))
        lifts = upward_lifts + list(reversed(upward_lifts[:-1]))
        self._validate_path(path, lifts, baseline, baseline_poses)
        accepted_deltas = [np.abs(current - previous) for previous, current in zip(upward, upward[1:])]
        max_delta = max(float(np.max(delta)) for delta in accepted_deltas)
        max_segment = next(index for index, delta in enumerate(accepted_deltas, start=1) if float(np.max(delta)) == max_delta)
        max_flat_joint = int(np.argmax(accepted_deltas[max_segment - 1]))
        minimum_increment = min(float(current - previous) for previous, current in zip(upward_lifts, upward_lifts[1:]))
        degrees = [np.rad2deg(waypoint) for waypoint in path]
        return RelativeTrajectory(
            left_waypoints_deg=tuple(tuple(float(value) for value in waypoint[:6]) for waypoint in degrees),
            right_waypoints_deg=tuple(tuple(float(value) for value in waypoint[6:]) for waypoint in degrees),
            baseline_left_deg=tuple(float(value) for value in degrees[0][:6]),
            baseline_right_deg=tuple(float(value) for value in degrees[0][6:]),
            baseline_positions=tuple(tuple(float(value) for value in pose[0]) for pose in baseline_poses),
            baseline_rotations=tuple(tuple(float(value) for value in pose[1].reshape(-1)) for pose in baseline_poses),
            requested_apex_m=REQUESTED_APEX_M,
            achieved_apex_m=reached_lift,
            max_accepted_delta_rad=max_delta,
            max_delta_arm="left" if max_flat_joint < 6 else "right",
            max_delta_joint_index=max_flat_joint % 6 + 1,
            max_delta_waypoint=max_segment,
            minimum_cartesian_increment_m=minimum_increment,
            subdivisions=tuple(subdivisions),
            reach_limit=reach_limit,
        )

    def pose_errors(
        self,
        left_deg: Sequence[float],
        right_deg: Sequence[float],
        trajectory: RelativeTrajectory,
    ) -> tuple[tuple[float, float], tuple[float, float]]:
        joints = np.deg2rad(np.concatenate((self._six(left_deg, "left_joints_deg"), self._six(right_deg, "right_joints_deg"))))
        self._forward(joints)
        current = self._poses()
        metrics = []
        for arm in range(2):
            baseline_position = np.asarray(trajectory.baseline_positions[arm])
            baseline_rotation = np.asarray(trajectory.baseline_rotations[arm]).reshape(3, 3)
            position, rotation = current[arm]
            metrics.append((float(position[2] - baseline_position[2]), float(np.linalg.norm(_rotation_log(baseline_rotation @ rotation.T)))))
        return tuple(metrics)  # type: ignore[return-value]

    def endpoint_errors(self, left_deg, right_deg, target_left_deg, target_right_deg):
        """Cartesian distance and orientation error relative to a packet endpoint."""
        self._forward(np.deg2rad(np.concatenate((self._six(target_left_deg, 'left'), self._six(target_right_deg, 'right')))))
        targets = [(p.copy(), r.copy()) for p, r in self._poses()]
        self._forward(np.deg2rad(np.concatenate((self._six(left_deg, 'left'), self._six(right_deg, 'right')))))
        return tuple((float(np.linalg.norm(p - goal_p)), float(np.linalg.norm(_rotation_log(goal_r @ r.T))))
                     for (p, r), (goal_p, goal_r) in zip(self._poses(), targets))
