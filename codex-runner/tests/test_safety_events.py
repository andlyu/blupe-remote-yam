from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from remote_yam.controller import RunnerController
from remote_yam.session import SCHEMA_VERSION


class SafetyEventTests(unittest.TestCase):
    def controller(self) -> RunnerController:
        controller = RunnerController(object())  # type: ignore[arg-type]
        controller._session_id = "sess_test"
        controller._status = "running"
        return controller

    def test_terminal_hardware_error_stops_and_exposes_sanitized_context(self) -> None:
        controller = self.controller()
        controller._handle_event({
            "schema_version": SCHEMA_VERSION,
            "session_id": "sess_test",
            "type": "error",
            "timestamp": 123.0,
            "payload": {
                "category": "hardware_safety",
                "code": "joint_limit",
                "message": "Left joint exceeded its limit",
                "terminal": True,
                "step_id": 7,
                "observed_at": 122.9,
                "details": {"arm": "left", "joint": 2},
            },
        })
        status = controller.status()
        self.assertEqual(status["status"], "safety_aborted")
        self.assertEqual(status["error"], "joint_limit: Left joint exceeded its limit")
        self.assertEqual(status["safety_error"]["step_id"], 7)
        self.assertEqual(status["safety_error"]["details"], {"arm": "left", "joint": 2})
        self.assertTrue(controller._stop_event.is_set())

    def test_legacy_reason_normalizes_and_safety_lifecycle_is_terminal(self) -> None:
        controller = self.controller()
        controller._handle_event({
            "schema_version": SCHEMA_VERSION,
            "session_id": "sess_test",
            "type": "error",
            "payload": {"reason": "controller_fault"},
        })
        status = controller.status()
        self.assertEqual(status["safety_error"]["code"], "safety_abort")
        self.assertEqual(status["safety_error"]["message"], "controller_fault")
        self.assertEqual(status["status"], "safety_aborted")

        lifecycle = self.controller()
        lifecycle._handle_event({
            "schema_version": SCHEMA_VERSION,
            "session_id": "sess_test",
            "type": "lifecycle",
            "payload": {"state": "safety_aborted", "reason": "collision"},
        })
        self.assertEqual(lifecycle.status()["status"], "safety_aborted")
        self.assertTrue(lifecycle._stop_event.is_set())

    def test_public_observation_preserves_only_strict_home_and_settled_booleans(self) -> None:
        payload = {
            "source": "hardware",
            "homed": True,
            "settled": True,
            "left_joints_deg": [0.0] * 6,
            "right_joints_deg": [0.0] * 6,
            "safety": {"ok": True, "estop_engaged": False},
        }
        observation = RunnerController._public_observation(payload)
        self.assertIs(observation["homed"], True)
        self.assertIs(observation["settled"], True)
        malformed = RunnerController._public_observation({**payload, "homed": 1, "settled": "true"})
        self.assertIsNone(malformed["homed"])
        self.assertIsNone(malformed["settled"])


if __name__ == "__main__":
    unittest.main()
