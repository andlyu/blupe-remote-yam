from __future__ import annotations

import math
from pathlib import Path
import sys
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from remote_yam.mujoco_ik import BimanualRelativeIK
from remote_yam.controller import RunnerController
from remote_yam.providers import PolicyComplete, RaiseLowerSimulationAdapter


HOME_RAD = (
    [0.029, 0.0, 0.002, 0.015, -0.041, 0.046],
    [-0.003, 0.001, 0.004, -0.090, -0.196, -0.181],
)
REST_RAD = (
    [-0.02842, 0.00134, 0.0, -0.10014, -0.0555, -0.22717],
    [-0.02842, 0.00134, 0.0, -0.10014, -0.0555, -0.22717],
)
FAILED_HARDWARE_DEG = (
    [-1.1037754121500394, 45.38593353128693, 35.41916941365295, -21.474442424507203, -1.14748928985896, -1.0819184732959863],
    [-1.3004878618403852, 45.42964740899585, 35.52845410792525, -21.73672569076093, -0.8633490847511802, -0.5136380630800196],
)


class RelativeIKTests(unittest.TestCase):
    def test_builds_observation_relative_exact_reverse_from_home_and_rest(self) -> None:
        solver = BimanualRelativeIK()
        for left_rad, right_rad in (HOME_RAD, REST_RAD):
            trajectory = solver.build(np.rad2deg(left_rad), np.rad2deg(right_rad))
            self.assertLessEqual(trajectory.length, 300)
            midpoint = (trajectory.length + 1) // 2
            for index in range(midpoint):
                self.assertEqual(trajectory.left_waypoints_deg[index], trajectory.left_waypoints_deg[-1 - index])
                self.assertEqual(trajectory.right_waypoints_deg[index], trajectory.right_waypoints_deg[-1 - index])
            all_rad = [
                np.deg2rad(left + right)
                for left, right in zip(trajectory.left_waypoints_deg, trajectory.right_waypoints_deg)
            ]
            max_delta = max(float(np.max(np.abs(current - previous))) for previous, current in zip(all_rad, all_rad[1:]))
            self.assertLessEqual(max_delta, 0.03)
            metrics = solver.pose_errors(
                trajectory.left_waypoints_deg[midpoint - 1], trajectory.right_waypoints_deg[midpoint - 1], trajectory
            )
            for lift, orientation_error in metrics:
                self.assertAlmostEqual(lift, 0.20, delta=0.002)
                self.assertLessEqual(orientation_error, 0.01)

    def test_adaptive_subdivision_reproduces_failed_hardware_baseline(self) -> None:
        trajectory = BimanualRelativeIK().build(*FAILED_HARDWARE_DEG)
        diagnostics = trajectory.diagnostics()
        self.assertEqual(87, trajectory.length)
        self.assertAlmostEqual(0.20, trajectory.achieved_apex_m, places=8)
        self.assertAlmostEqual(0.0, diagnostics["shortfall_m"], places=8)
        self.assertAlmostEqual(0.02947884118064059, trajectory.max_accepted_delta_rad, places=9)
        self.assertEqual("right", trajectory.max_delta_arm)
        self.assertEqual(3, trajectory.max_delta_joint_index)
        self.assertEqual(37, trajectory.max_delta_waypoint)
        self.assertAlmostEqual(0.0025, trajectory.minimum_cartesian_increment_m, places=8)
        self.assertFalse(diagnostics["endpoint_reach_limit"])
        first = trajectory.subdivisions[0]
        self.assertEqual(("right", 1, 3, 0), (first.arm, first.requested_waypoint, first.joint_index, first.subdivision_depth))
        self.assertAlmostEqual(0.040904058178420355, first.attempted_delta_rad, places=9)

    def test_provider_generates_once_then_advances_only_on_settled_targets(self) -> None:
        adapter = RaiseLowerSimulationAdapter()
        observation = {
            "source": "simulation",
            "settled": True,
            "left_joints_deg": np.rad2deg(HOME_RAD[0]).tolist(),
            "right_joints_deg": np.rad2deg(HOME_RAD[1]).tolist(),
            "left_gripper": 0.25,
            "right_gripper": None,
            "safety": {"ok": True, "estop": False, "contact_count": 0},
        }
        command_count = 0
        for _ in range(300):
            try:
                command = adapter.infer("unused", observation)
            except PolicyComplete:
                break
            command_count += 1
            self.assertEqual(command.left.mode, "joints")
            self.assertEqual(command.right.mode, "joints")
            self.assertEqual(command.left_gripper, 0.25)
            self.assertIsNone(command.right_gripper)
            observation = {
                **observation,
                "left_joints_deg": list(command.left.values),
                "right_joints_deg": list(command.right.values),
            }
        else:
            self.fail("provider did not finish within the 300-command limit")
        self.assertEqual(adapter._trajectory.length, command_count)

    def test_policy_requires_homed_hardware_and_rejects_large_baseline_bound_error(self) -> None:
        safe = {"ok": True, "estop": False, "contact_count": 0}
        with self.assertRaisesRegex(RuntimeError, "calibrated EEF Home"):
            RaiseLowerSimulationAdapter().infer(
                "", {"source": "hardware", "settled": True, "homed": False, "left_joints_deg": [0.0] * 6, "right_joints_deg": [0.0] * 6, "safety": safe}
            )
        hardware_command = RaiseLowerSimulationAdapter().infer(
            "", {"source": "hardware", "settled": True, "homed": True, "left_joints_deg": np.rad2deg(HOME_RAD[0]).tolist(), "right_joints_deg": np.rad2deg(HOME_RAD[1]).tolist(), "safety": safe}
        )
        self.assertEqual(hardware_command.left.mode, "joints")
        self.assertEqual(hardware_command.right.mode, "joints")
        with self.assertRaisesRegex(RuntimeError, "joint bounds"):
            RaiseLowerSimulationAdapter().infer(
                "", {"source": "simulation", "settled": True, "left_joints_deg": [0.0, math.degrees(-0.003), 0.0, 0.0, 0.0, 0.0], "right_joints_deg": [0.0] * 6, "safety": safe}
            )

    def test_policy_rejects_missing_settled_and_source_change(self) -> None:
        safe = {"ok": True, "estop": False, "contact_count": 0}
        baseline = {"left_joints_deg": np.rad2deg(HOME_RAD[0]).tolist(), "right_joints_deg": np.rad2deg(HOME_RAD[1]).tolist(), "safety": safe}
        with self.assertRaisesRegex(RuntimeError, "settled=true"):
            RaiseLowerSimulationAdapter().infer("", {**baseline, "source": "hardware", "homed": True})
        adapter = RaiseLowerSimulationAdapter()
        command = adapter.infer("", {**baseline, "source": "hardware", "homed": True, "settled": True})
        with self.assertRaisesRegex(RuntimeError, "source changed"):
            adapter.infer("", {**baseline, "source": "simulation", "settled": True, "left_joints_deg": list(command.left.values), "right_joints_deg": list(command.right.values)})

    def test_latency_status_is_numeric_and_secret_free(self) -> None:
        controller = RunnerController(object())  # type: ignore[arg-type]
        controller._note_latency("model", 12.5)
        controller._note_latency("server_action_rtt", 20.0)
        latency = controller.status()["latency"]
        self.assertEqual(latency["model"], {"count": 1, "last_ms": 12.5, "avg_ms": 12.5, "max_ms": 12.5})
        self.assertEqual(latency["server_action_rtt"]["last_ms"], 20.0)
        self.assertNotIn("prompt", repr(latency))
        self.assertNotIn("capability", repr(latency))

    def test_policy_rejects_reported_contacts_and_estop(self) -> None:
        baseline = {
            "source": "simulation",
            "left_joints_deg": np.rad2deg(HOME_RAD[0]).tolist(),
            "right_joints_deg": np.rad2deg(HOME_RAD[1]).tolist(),
        }
        with self.assertRaisesRegex(RuntimeError, "zero active contacts"):
            RaiseLowerSimulationAdapter().infer("", {**baseline, "safety": {"ok": True, "estop": False, "contact_count": 1}})
        with self.assertRaisesRegex(RuntimeError, "estop=false"):
            RaiseLowerSimulationAdapter().infer("", {**baseline, "safety": {"ok": True, "estop_engaged": True, "contact_count": 0}})


if __name__ == "__main__":
    unittest.main()
