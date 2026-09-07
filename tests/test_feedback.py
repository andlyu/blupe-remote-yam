import pathlib
import sys
import time
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from remote_yam.feedback import feedback_decision
from remote_yam.controller import RunnerController
from remote_yam.ik import ArmIKCommand, IKCommand
from remote_yam.providers import ScriptedAdapter
from remote_yam.session import MockSessionAPI


def station(at, settled=True):
    return dict(observed_at=at, source="hardware", mode="API_ACTIVE", homed=True,
                settled=settled, left_joints_deg=[0.] * 6, right_joints_deg=[0.] * 6,
                safety=dict(ok=True, estop_engaged=False, contact_count=0))


class FeedbackTests(unittest.TestCase):
    def test_old_moving_sample_is_wait_not_rejection(self):
        completion = dict(observed_at=100, settled=True, homed=True)
        old = station(99, False)
        # This is the old runner expression that incorrectly rejects completion.
        self.assertFalse(completion["settled"] is True and old["settled"] is True)
        self.assertEqual("wait", feedback_decision(completion, old, now=101)[0])
        self.assertEqual("ready", feedback_decision(completion, station(101), now=101)[0])

    def test_missing_completion_flag_cannot_be_repaired_by_station(self):
        for value in (None, False, 1, "true"):
            self.assertEqual("reject", feedback_decision(
                dict(observed_at=100, settled=value), station(101), now=101)[0])

    def test_fault_estop_contacts_and_bad_timestamps_block(self):
        completion = dict(observed_at=100, settled=True)
        for change in ({"ok": False}, {"estop_engaged": True}, {"contact_count": 1}):
            raw = station(99, False)
            raw["safety"].update(change)
            self.assertEqual("reject", feedback_decision(completion, raw, now=101)[0])
        for at in (True, float("nan"), float("inf"), 104):
            self.assertEqual("reject", feedback_decision(completion, station(at), now=101)[0])
        self.assertEqual("reject", feedback_decision(completion, station(111), now=111)[0])

    def test_reconciliation_times_out_without_submitting(self):
        api = Mock()
        now = time.time()
        api.get_robot_observation.return_value = station(now - 1, False)
        controller = RunnerController(api)
        with self.assertRaisesRegex(RuntimeError, "station_precedes"):
            controller._reconcile_hardware_feedback(
                dict(observed_at=now, settled=True), station(now - 1, False), require_home=False)
        self.assertEqual(3, api.get_robot_observation.call_count)
        api.submit_action.assert_not_called()
        api.submit_trajectory.assert_not_called()
        self.assertEqual(4, len(controller.status()["feedback_checks"]))

    def test_controller_refreshes_race_and_submits_exactly_once(self):
        class HardwareAPI(MockSessionAPI):
            reads = 0

            def get_robot_observation(self, jetson_id):
                self.reads += 1
                return station(time.time())

        api = HardwareAPI()
        api.supports_trajectories = False  # Legacy single-action feedback test.
        controller = RunnerController(api)
        controller.update_monitor_observation(station(time.time() - 1, False))
        command = IKCommand(ArmIKCommand("joints", (0,) * 6), ArmIKCommand("joints", (0,) * 6))
        controller.join(ScriptedAdapter([command]), "single unchanged target")
        for _ in range(10):
            controller.process_next_event(0.01)
            if api.action_log:
                break
        self.assertEqual(1, len(api.action_log))
        self.assertEqual(1, api.reads)
        checks = controller.status()["feedback_checks"]
        self.assertEqual(["wait", "ready"], [row["decision"] for row in checks])
        # Replayed completion cannot cause a duplicate command.
        payload = dict(observed_at=time.time(), settled=True, step_id=0)
        controller._observation({}, payload)
        self.assertEqual(1, len(api.action_log))

    def test_refresh_to_fault_prevents_submission(self):
        api = Mock()
        now = time.time()
        bad = station(now)
        bad["safety"]["ok"] = False
        api.get_robot_observation.return_value = bad
        controller = RunnerController(api)
        with self.assertRaisesRegex(RuntimeError, "station_unsafe"):
            controller._reconcile_hardware_feedback(
                dict(observed_at=now, settled=True), station(now - 1, False), require_home=False)
        api.submit_action.assert_not_called()


if __name__ == "__main__":
    unittest.main()
