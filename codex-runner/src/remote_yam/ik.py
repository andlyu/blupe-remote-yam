"""Strict bimanual command and user-owned inverse-kinematics boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Sequence


@dataclass(frozen=True)
class ArmIKCommand:
    mode: str
    values: tuple[float, ...]


@dataclass(frozen=True)
class IKCommand:
    """A policy command must explicitly describe both arms."""

    left: ArmIKCommand
    right: ArmIKCommand
    left_gripper: float | None = None
    right_gripper: float | None = None


@dataclass(frozen=True)
class ResolvedJointCommand:
    left_joints_deg: tuple[float, ...]
    right_joints_deg: tuple[float, ...]
    left_gripper: float | None = None
    right_gripper: float | None = None


@dataclass(frozen=True)
class IKObservation:
    left_joints_deg: tuple[float, ...]
    right_joints_deg: tuple[float, ...]
    episode_id: str
    step_id: int


PoseSolver = Callable[[str, Sequence[float]], Sequence[float]]


class IKSolver:
    """Resolve each arm independently; never infer, mirror, or pad an arm."""

    def __init__(self, pose_solver: PoseSolver | None = None) -> None:
        self._pose_solver = pose_solver

    def resolve(self, command: IKCommand) -> ResolvedJointCommand:
        return ResolvedJointCommand(
            left_joints_deg=tuple(self._resolve_arm("left", command.left)),
            right_joints_deg=tuple(self._resolve_arm("right", command.right)),
            left_gripper=self._gripper(command.left_gripper, "left"),
            right_gripper=self._gripper(command.right_gripper, "right"),
        )

    def _resolve_arm(self, arm: str, command: ArmIKCommand) -> list[float]:
        if len(command.values) != 6:
            raise ValueError(f"{arm} {command.mode} command must have 6 values")
        if command.mode == "joints":
            values = command.values
        elif command.mode == "pose":
            if self._pose_solver is None:
                raise RuntimeError("pose output requires a configured user-owned IK solver")
            values = tuple(float(value) for value in self._pose_solver(arm, command.values))
            if len(values) != 6:
                raise ValueError(f"{arm} IK solver must return 6 joint values")
        else:
            raise ValueError(f"unsupported {arm} IK mode: {command.mode!r}")
        return [float(value) for value in values]

    @staticmethod
    def _gripper(value: float | None, arm: str) -> float | None:
        if value is None:
            return None
        result = float(value)
        if not 0.0 <= result <= 1.0:
            raise ValueError(f"{arm}_gripper must be between 0 and 1")
        return result


def parse_observation(raw: dict) -> IKObservation:
    left = _six_values(raw.get("left_joints_deg"), "left_joints_deg")
    right = _six_values(raw.get("right_joints_deg"), "right_joints_deg")
    return IKObservation(
        left_joints_deg=tuple(left),
        right_joints_deg=tuple(right),
        episode_id=str(raw.get("episode_id", "")),
        step_id=int(raw.get("step_id") or 0),
    )


def _six_values(raw: object, name: str) -> list[float]:
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Iterable):
        raise ValueError(f"observation missing {name}")
    values = [float(value) for value in raw]
    if len(values) != 6:
        raise ValueError(f"observation {name} must have 6 values")
    return values
