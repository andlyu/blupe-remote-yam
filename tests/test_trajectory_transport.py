import math
import pathlib
import sys
import time
import unittest
from unittest import mock

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from remote_yam.controller import RunnerController
from remote_yam.providers import RaiseLowerSimulationAdapter, PolicyComplete
from remote_yam.session import HttpSessionAPI, MockSessionAPI


class TinyTrajectoryProvider:
    provider_name = "tiny_trajectory"

    def __init__(self) -> None:
        self.final_validated = False

    def public_config(self):
        return {"provider": self.provider_name, "model": "test", "api_key_configured": False}

    def infer(self, prompt, observation):
        raise AssertionError("atomic provider must not use infer")

    def build_trajectory(self, prompt, observation, first_step_id):
        del prompt
        if self.final_validated:
            raise PolicyComplete("done")
        left = list(observation["left_joints_deg"])
        right = list(observation["right_joints_deg"])
        raised_left = list(left)
        raised_right = list(right)
        raised_left[0] += 0.25
        raised_right[0] -= 0.25
        pairs = ((left, right), (raised_left, raised_right), (left, right))
        return [
            {
                "step_id": first_step_id + offset,
                "left_joints_deg": left_target,
                "right_joints_deg": right_target,
                "left_gripper": observation["left_gripper"],
                "right_gripper": observation["right_gripper"],
            }
            for offset, (left_target, right_target) in enumerate(pairs)
        ]

    def validate_trajectory_final(self, observation):
        if observation.get("settled") is not True:
            raise RuntimeError("final observation was not settled")
        self.final_validated = True


class LostTrajectoryAckSession(MockSessionAPI):
    def __init__(self):
        super().__init__()
        self.failed_once = False

    def submit_trajectory(self, *args, **kwargs):
        receipt = super().submit_trajectory(*args, **kwargs)
        if not self.failed_once:
            self.failed_once = True
            raise ConnectionError("lost trajectory dispatch acknowledgement")
        return receipt


class ProgressBeforeAcceptSession(MockSessionAPI):
    def submit_trajectory(
        self, session_id, episode_id, lease_id, trajectory_id, cadence_hz, waypoints
    ):
        record = self._record(session_id)
        dispatched_at = time.time()
        self._event(record, "trajectory_progress", {
            "episode_id": episode_id,
            "lease_id": lease_id,
            "trajectory_id": trajectory_id,
            "step_id": waypoints[0]["step_id"],
            "executed_at": dispatched_at,
        })
        return {
            "schema_version": 1,
            "accepted": True,
            "duplicate": False,
            "session_id": session_id,
            "episode_id": episode_id,
            "lease_id": lease_id,
            "trajectory_id": trajectory_id,
            "first_step_id": waypoints[0]["step_id"],
            "last_step_id": waypoints[-1]["step_id"],
            "waypoint_count": len(waypoints),
            "dispatched_at": dispatched_at,
        }


class TrajectoryTransportTests(unittest.TestCase):
    @staticmethod
    def run_controller(provider, session=None, submit_attempts=2):
        session = session or MockSessionAPI()
        controller = RunnerController(session, submit_attempts=submit_attempts)
        controller.update_monitor_observation(session.get_robot_observation("yam-1"))
        controller.join(provider, "Raise then return")
        controller._run_loop(None)
        return session, controller

    def test_http_transport_sends_exact_capability_scoped_body(self):
        api = HttpSessionAPI("https://session.invalid")
        api._session_capabilities["sess_1"] = "private-capability"
        waypoints = [{
            "step_id": 4,
            "left_joints_deg": [0, 1, 2, 3, 4, 5],
            "right_joints_deg": [5, 4, 3, 2, 1, 0],
            "left_gripper": 0.4,
        }]
        with mock.patch.object(api, "_request", return_value={}) as request_call:
            api.submit_trajectory("sess_1", "ep_1", "lease_1", "traj_1", 10.0, waypoints)

        method, path, body = request_call.call_args.args
        self.assertEqual("POST", method)
        self.assertEqual("/v1/sessions/sess_1/trajectories", path)
        self.assertEqual(
            {"schema_version", "episode_id", "lease_id", "trajectory_id", "cadence_hz", "waypoints"},
            set(body),
        )
        self.assertNotIn("session_id", body)
        self.assertEqual(10.0, body["cadence_hz"])
        self.assertEqual("private-capability", request_call.call_args.kwargs["bearer"])

    def test_controller_enforces_complete_atomic_lifecycle(self):
        provider = TinyTrajectoryProvider()
        session, controller = self.run_controller(provider)

        status = controller.status()
        event_types = [item["type"] for item in status["run_summary"]["events"]]
        accepted = event_types.index("trajectory_result")
        progress = [index for index, value in enumerate(event_types) if value == "trajectory_progress"]
        final_observation = max(index for index, value in enumerate(event_types) if value == "observation")
        completed = max(index for index, value in enumerate(event_types) if value == "trajectory_result")
        self.assertEqual("stopped", status["status"])
        self.assertEqual([0, 1, 2], status["run_summary"]["action_step_ids"])
        self.assertEqual([0, 1, 2], [item["step_id"] for item in session.action_log])
        self.assertEqual([], session.action_attempts)
        self.assertTrue(provider.final_validated)
        self.assertLess(accepted, progress[0])
        self.assertLess(progress[-1], final_observation)
        self.assertLess(final_observation, completed)
        self.assertEqual("completed", status["trajectory"]["state"])
        self.assertEqual(3, status["trajectory"]["progress_count"])
        self.assertTrue(status["trajectory"]["final_observation_received"])

    def test_ambiguous_dispatch_retries_same_trajectory_without_reexecution(self):
        provider = TinyTrajectoryProvider()
        session, controller = self.run_controller(provider, LostTrajectoryAckSession())

        self.assertEqual(2, len(session.trajectory_attempts))
        self.assertEqual(session.trajectory_attempts[0], session.trajectory_attempts[1])
        self.assertEqual(1, len(session.trajectory_log))
        self.assertEqual([0, 1, 2], controller.status()["run_summary"]["action_step_ids"])

    def test_progress_before_acceptance_fails_closed_and_stops(self):
        provider = TinyTrajectoryProvider()
        session, controller = self.run_controller(provider, ProgressBeforeAcceptSession())

        status = controller.status()
        self.assertEqual("stopped", status["status"])
        self.assertIn("before Jetson acceptance", status["error"])
        self.assertTrue(session.state(status["session_id"])["return_to_rest_requested"])

    def test_real_policy_builds_atomic_20cm_batch_from_captured_baseline(self):
        observation = {
            "source": "hardware",
            "homed": True,
            "settled": True,
            "safety": {"ok": True, "estop_engaged": False, "contact_count": 0},
            "left_joints_deg": [
                -1.1037754121500394, 45.38593353128693, 35.41916941365295,
                -21.474442424507203, -1.14748928985896, -1.0819184732959863,
            ],
            "right_joints_deg": [
                -1.3004878618403852, 45.42964740899585, 35.52845410792525,
                -21.73672569076093, -0.8633490847511802, -0.5136380630800196,
            ],
            "left_gripper": 0.9958308851519609,
            "right_gripper": 0.9899399290728813,
        }
        provider = RaiseLowerSimulationAdapter()
        waypoints = provider.build_trajectory("raise", observation, 0)
        max_delta = max(
            math.radians(abs(current[arm][joint] - previous[arm][joint]))
            for previous, current in zip(waypoints, waypoints[1:])
            for arm in ("left_joints_deg", "right_joints_deg")
            for joint in range(6)
        )

        self.assertEqual(87, len(waypoints))
        self.assertEqual(list(range(87)), [item["step_id"] for item in waypoints])
        self.assertLessEqual(max_delta, 0.03)
        self.assertEqual(waypoints[0]["left_joints_deg"], waypoints[-1]["left_joints_deg"])
        self.assertEqual(waypoints[0]["right_joints_deg"], waypoints[-1]["right_joints_deg"])
        self.assertAlmostEqual(0.2, provider.public_config()["trajectory"]["achieved_apex_m"], places=6)


if __name__ == "__main__":
    unittest.main()
