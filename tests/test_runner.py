import json
import unittest
import pathlib
import sys
import time
from unittest import mock

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from remote_yam.controller import RunnerController
from remote_yam.credentials import CredentialVault
from remote_yam.ik import ArmIKCommand, IKCommand, IKSolver, parse_observation
from remote_yam.providers import (
    OpenAIAdapter, PolicyComplete, RaiseLowerSimulationAdapter, ScriptedAdapter, _parse_action,
)
from remote_yam.session import HttpSessionAPI, MAX_COMMANDS, MockSessionAPI


def process_until(controller: RunnerController, predicate, limit: int = 50) -> None:
    for _ in range(limit):
        if predicate():
            return
        controller.process_next_event(0.01)
    raise AssertionError("event predicate was not reached")


def bimanual(left, right, left_gripper=None, right_gripper=None) -> IKCommand:
    return IKCommand(
        left=ArmIKCommand("joints", tuple(left)),
        right=ArmIKCommand("joints", tuple(right)),
        left_gripper=left_gripper,
        right_gripper=right_gripper,
    )


class PublicYamRunnerTests(unittest.TestCase):
    def test_credential_vault_exposes_only_configuration_state(self) -> None:
        secret = "sk-browser-must-never-see-this"
        vault = CredentialVault.from_local_sources(
            environment={"OPENAI_API_KEY": secret},
            keychain_lookup=lambda provider: None,
        )

        exposed = json.dumps({"status": vault.public_status(), "repr": repr(vault)})
        self.assertNotIn(secret, exposed)
        self.assertEqual(secret, vault.require("openai"))
        self.assertEqual({"openai": True, "astra": False}, vault.public_status())
        vault.clear("openai")
        with self.assertRaises(RuntimeError):
            vault.require("openai")

    def test_provider_key_never_enters_session_or_status(self) -> None:
        secret = "sk-local-super-secret-value"
        session = MockSessionAPI(auto_activate=False)
        controller = RunnerController(session)
        provider = OpenAIAdapter(secret, "test-model")

        controller.join(provider, "Return a safe command")

        exposed = json.dumps({
            "status": controller.status(),
            "creates": session.create_requests,
            "adapter": repr(provider),
        })
        self.assertNotIn(secret, exposed)
        self.assertNotIn("sk-local", exposed)
        self.assertTrue(controller.status()["provider"]["api_key_configured"])

    def test_idle_monitor_reads_feedback_without_creating_session(self) -> None:
        session = MockSessionAPI()
        controller = RunnerController(session)

        controller.update_monitor_observation(session.get_robot_observation("yam-1"))

        status = controller.status()
        self.assertEqual("idle", status["status"])
        self.assertEqual([], session.create_requests)
        self.assertEqual(6, len(status["last_observation"]["left_joints_deg"]))
        self.assertEqual(6, len(status["last_observation"]["right_joints_deg"]))
        self.assertEqual({"left", "top", "right"}, set(status["last_observation"]["images"]))

    def test_public_queue_snapshot_is_visible_and_marks_own_entry(self) -> None:
        session = MockSessionAPI(auto_activate=False)
        controller = RunnerController(session)
        controller.join(ScriptedAdapter([bimanual((0,) * 6, (0,) * 6)]), "Wait")
        snapshot = session.get_queue_snapshot()
        snapshot["entries"][0]["session_capability"] = "must-not-leak"
        snapshot["entries"][0]["prompt"] = "must-not-leak"

        controller.update_queue_snapshot(snapshot)

        public = controller.status()["queue_snapshot"]
        self.assertEqual(1, len(public["entries"]))
        self.assertTrue(public["entries"][0]["is_mine"])
        self.assertNotIn("must-not-leak", json.dumps(public))
        self.assertFalse(public["stations"][0]["available"])


    def test_controller_binds_session_observation_to_fresh_station_safety(self) -> None:
        session = MockSessionAPI()
        session.supports_trajectories = False  # Legacy single-action feedback test.
        controller = RunnerController(session)
        controller.update_monitor_observation(session.get_robot_observation("yam-1"))
        provider = ScriptedAdapter([bimanual((0,) * 6, (0,) * 6)])
        controller.join(provider, "Hold")

        process_until(controller, lambda: len(session.action_log) == 1)

        self.assertEqual(1, len(session.action_log))

    def test_controller_refreshes_stale_station_safety_before_inference(self) -> None:
        session = MockSessionAPI()
        session.supports_trajectories = False  # Legacy single-action feedback test.
        controller = RunnerController(session)
        stale = session.get_robot_observation("yam-1")
        stale["observed_at"] = time.time() - 60
        controller.update_monitor_observation(stale)
        controller.join(ScriptedAdapter([bimanual((0,) * 6, (0,) * 6)]), "Hold")

        process_until(controller, lambda: len(session.action_log) == 1)

        self.assertEqual(1, len(session.action_log))
        self.assertLess(time.time() - controller.status()["last_observation"]["observed_at"], 10)

    def test_event_loop_orders_actions_and_retries_idempotently(self) -> None:
        session = MockSessionAPI(fail_after_commit_once={0})
        session.supports_trajectories = False  # Exercise the legacy action transport explicitly.
        controller = RunnerController(session, submit_attempts=2)
        provider = ScriptedAdapter([
            bimanual((0, 1, 2, 3, 4, 5), (5, 4, 3, 2, 1, 0)),
            bimanual((1, 2, 3, 4, 5, 6), (6, 5, 4, 3, 2, 1)),
            bimanual((2, 3, 4, 5, 6, 7), (7, 6, 5, 4, 3, 2)),
        ])
        controller.join(provider, "Use direct joints")

        process_until(controller, lambda: controller.status()["status"] == "resetting")

        self.assertEqual([0, 0, 1, 2], [item["step_id"] for item in session.action_attempts])
        self.assertEqual([0, 1, 2], [item["step_id"] for item in session.action_log])
        self.assertEqual(
            session.action_attempts[0]["idempotency_key"],
            session.action_attempts[1]["idempotency_key"],
        )
        self.assertEqual(session.action_attempts[0], session.action_attempts[1])
        self.assertTrue(controller.status()["heartbeat_sent"])
        self.assertEqual("complete", controller.status()["episode"]["status"])
        self.assertEqual(2, controller.status()["last_model_command"]["step_id"])
        self.assertEqual("joints", controller.status()["last_model_command"]["left"]["mode"])
        self.assertNotIn("joints_deg", session.action_log[0])
        self.assertEqual(6, len(session.action_log[0]["left_joints_deg"]))
        self.assertEqual(6, len(session.action_log[0]["right_joints_deg"]))
        feedback = controller.status()["last_observation"]
        self.assertEqual(2, feedback["step_id"])
        self.assertEqual({"left", "top", "right"}, set(feedback["images"]))
        self.assertEqual(6, len(feedback["left_joints_deg"]))
        self.assertEqual(6, len(feedback["right_joints_deg"]))

    def test_event_stream_reconnect_resumes_from_ordered_observation(self) -> None:
        class BrokenStream:
            def receive(self, timeout_s=None):
                del timeout_s
                raise ConnectionError("injected reset")

            def close(self):
                pass

        class RecoveringSession(MockSessionAPI):
            def __init__(self):
                super().__init__()
                self.open_count = 0

            def open_events(self, session_id):
                self.open_count += 1
                if self.open_count == 1:
                    return BrokenStream()
                return super().open_events(session_id)

        session = RecoveringSession()
        session.supports_trajectories = False  # Exercise the legacy action transport explicitly.
        controller = RunnerController(session)
        controller.update_monitor_observation(session.get_robot_observation("yam-1"))
        controller.join(ScriptedAdapter([
            bimanual((0,) * 6, (0,) * 6),
            bimanual((1,) * 6, (1,) * 6),
            bimanual((2,) * 6, (2,) * 6),
        ]), "Recover safely")

        controller._run_loop(None)

        self.assertEqual(2, session.open_count)
        self.assertEqual([0, 1, 2], [item["step_id"] for item in session.action_log])
        self.assertEqual("resetting", controller.status()["status"])
        self.assertIsNone(controller.status()["error"])

    def test_snapshot_retains_lease_before_replayed_observation(self) -> None:
        session = MockSessionAPI(auto_activate=False)
        controller = RunnerController(session)
        controller.join(ScriptedAdapter([bimanual((0,) * 6, (0,) * 6)]), "Hold")
        session_id = controller.status()["session_id"]

        controller._handle_event({
            "schema_version": 1,
            "session_id": session_id,
            "type": "snapshot",
            "payload": {
                "status": "running",
                "episode_id": "ep_replayed",
                "lease_id": "lease_replayed",
                "lease_expires_at": 9999999999,
                "latest_observation_step": 0,
            },
        })

        status = controller.status()
        self.assertEqual("running", status["status"])
        self.assertEqual("ep_replayed", status["episode_id"])
        self.assertEqual("lease_replayed", status["lease_id"])

    def test_sanitized_trace_uses_capability_internally_without_leaking_authority(self) -> None:
        session = MockSessionAPI(auto_activate=False)
        controller = RunnerController(session)
        controller.join(ScriptedAdapter([bimanual((0,) * 6, (0,) * 6)]), "secret prompt")
        session.get_episode_trace = mock.Mock(return_value={
            "status": "disconnected",
            "session_capability": "cap_must_not_leak",
            "lease_id": "lease_must_not_leak",
            "prompt": "secret prompt",
            "events": [{
                "type": "observation", "timestamp": 123.5,
                "payload": {
                    "step_id": 0, "replayed": True,
                    "lease_id": "lease_must_not_leak",
                    "left_joints_deg": [1] * 6, "right_joints_deg": [2] * 6,
                    "left_gripper": None, "right_gripper": 0.5,
                    "images": {"top": {"url": "https://must-not-leak.invalid"}},
                },
            }],
            "actions": [{
                "step_id": 0, "left_joints_deg": [9] * 6,
                "idempotency_key": "must-not-leak",
            }],
            "episode": {"status": "disconnected", "ended_at": 124.0},
        })

        trace = controller.sanitized_episode_trace()

        exposed = json.dumps(trace)
        self.assertEqual([0], trace["action_step_ids"])
        self.assertEqual(True, trace["events"][0]["replayed"])
        self.assertEqual([1.0] * 6, trace["observations"][0]["left_joints_deg"])
        self.assertNotIn("must_not_leak", exposed)
        self.assertNotIn("secret prompt", exposed)
        self.assertNotIn("idempotency", exposed)
        self.assertNotIn("images", exposed)

    def test_stop_prevents_actions(self) -> None:
        session = MockSessionAPI()
        controller = RunnerController(session)
        controller.join(ScriptedAdapter([bimanual((0,) * 6, (0,) * 6)]), "Hold")
        session_id = controller.status()["session_id"]

        result = controller.stop()

        self.assertEqual("stopped", result["status"])
        self.assertTrue(result["return_to_rest_requested"])
        self.assertEqual("stopped", session.state(session_id)["status"])
        self.assertTrue(session.state(session_id)["return_to_rest_requested"])
        with self.assertRaises(RuntimeError):
            controller.process_next_event()
        self.assertEqual([], session.action_log)

    def test_disconnect_stops_session_and_clears_provider(self) -> None:
        session = MockSessionAPI()
        controller = RunnerController(session)
        controller.join(ScriptedAdapter([bimanual((0,) * 6, (0,) * 6)]), "Hold")
        session_id = controller.status()["session_id"]

        result = controller.disconnect()

        self.assertEqual("disconnected", result["status"])
        self.assertIsNone(result["provider"])
        self.assertFalse(result["prompt_configured"])
        self.assertEqual("stopped", session.state(session_id)["status"])

    def test_call_operator_halts_autonomy_and_is_idempotent(self) -> None:
        session = MockSessionAPI()
        controller = RunnerController(session)
        controller.join(
            ScriptedAdapter([bimanual((0,) * 6, (0,) * 6)]),
            "Hold",
        )
        session_id = controller.status()["session_id"]

        first = controller.call_operator()
        second = controller.call_operator()

        self.assertEqual("operator_requested", first["status"])
        self.assertEqual(first, second)
        self.assertTrue(first["operator_requested"])
        self.assertFalse(first["return_to_rest_requested"])
        self.assertTrue(session.state(session_id)["operator_requested"])
        self.assertFalse(session.state(session_id)["return_to_rest_requested"])
        self.assertEqual([], session.action_log)

    def test_command_limit_is_enforced_before_command_3001(self) -> None:
        observations = [
            {"left_joints_deg": [0] * 6, "right_joints_deg": [0] * 6}
            for _ in range(MAX_COMMANDS + 1)
        ]
        actions = [bimanual((0,) * 6, (0,) * 6) for _ in observations]
        session = MockSessionAPI(observations=observations)
        session.supports_trajectories = False  # Exercise the legacy action transport explicitly.
        controller = RunnerController(session)
        controller.join(ScriptedAdapter(actions), "Hold")

        with self.assertRaisesRegex(RuntimeError, "command limit"):
            for _ in range(MAX_COMMANDS * 4):
                controller.process_next_event(0.01)

        self.assertEqual(MAX_COMMANDS, len(session.action_log))

    def test_http_and_websocket_client_match_canonical_v1(self) -> None:
        calls = []

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def read(self):
                return b'{"schema_version":1,"session_id":"sess_1","session_capability":"cap_opaque","status":"queued"}'

        def urlopen(req, timeout):
            calls.append((req, timeout))
            return Response()

        class Socket:
            def __init__(self):
                self.timeout = None
                self.closed = False

            def settimeout(self, value):
                self.timeout = value

            def recv(self):
                return '{"schema_version":1,"session_id":"sess_1","type":"snapshot","payload":{"status":"queued"}}'

            def close(self):
                self.closed = True

        socket = Socket()
        websocket_calls = []

        def connect(url, **kwargs):
            websocket_calls.append((url, kwargs))
            return socket

        api = HttpSessionAPI(
            "https://api.blupe.ai/v1",
            websocket_factory=connect,
        )
        with mock.patch("remote_yam.session.request.urlopen", urlopen):
            api.get_queue_snapshot()
            api.get_robot_observation("yam-1")
            api.create_session("pick up the blue block")
            api.submit_action("sess_1", "ep_1", "lease_1", 0, "ep_1:step:0", [1] * 6, [2] * 6, 0.4, 0.6)
        stream = api.open_events("sess_1")
        event = stream.receive(0.25)
        stream.close()

        queue_req = calls[0][0]
        self.assertEqual("https://api.blupe.ai/v1/queue", queue_req.full_url)
        self.assertIsNone(queue_req.get_header("Authorization"))
        monitor_req = calls[1][0]
        self.assertEqual(
            "https://api.blupe.ai/v1/robots/yam-1/observation", monitor_req.full_url
        )
        self.assertIsNone(monitor_req.get_header("Authorization"))
        req = calls[2][0]
        self.assertEqual("https://api.blupe.ai/v1/sessions", req.full_url)
        self.assertEqual("POST", req.method)
        self.assertEqual(
            {"schema_version": 1, "prompt": "pick up the blue block"},
            json.loads(req.data),
        )
        self.assertIsNone(req.get_header("Authorization"))
        self.assertEqual("Bearer cap_opaque", calls[3][0].get_header("Authorization"))
        action = json.loads(calls[3][0].data)
        self.assertNotIn("joints_deg", action)
        self.assertEqual([1.0] * 6, action["left_joints_deg"])
        self.assertEqual([2.0] * 6, action["right_joints_deg"])
        self.assertEqual(0.4, action["left_gripper"])
        self.assertEqual(0.6, action["right_gripper"])
        self.assertEqual("wss://api.blupe.ai/v1/sessions/sess_1/events", websocket_calls[0][0])
        self.assertIn("Authorization: Bearer cap_opaque", websocket_calls[0][1]["header"])
        self.assertEqual("snapshot", event["type"])
        self.assertTrue(socket.closed)

    def test_missing_websocket_dependency_fails_before_session_creation(self) -> None:
        api = HttpSessionAPI("https://api.blupe.ai")
        with mock.patch.dict(sys.modules, {"websocket": None}):
            with mock.patch.object(api, "_request") as request_call:
                with self.assertRaisesRegex(RuntimeError, "relaunch with `./run.sh"):
                    api.create_session("must not create a queue entry")
                request_call.assert_not_called()

    def test_ik_accepts_pose_and_direct_joint_outputs(self) -> None:
        solver = IKSolver(pose_solver=lambda arm, pose: pose)
        observation = parse_observation({
            "episode_id": "ep_1", "step_id": 0,
            "left_joints_deg": [0] * 6, "right_joints_deg": [1] * 6,
        })
        self.assertEqual("ep_1", observation.episode_id)
        result = solver.resolve(IKCommand(
            left=ArmIKCommand("joints", (1, 2, 3, 4, 5, 6)),
            right=ArmIKCommand("pose", (0.1, 0.2, 0.3, 0, 0, 0)),
        ))
        self.assertEqual((1.0, 2.0, 3.0, 4.0, 5.0, 6.0), result.left_joints_deg)
        self.assertEqual((0.1, 0.2, 0.3, 0.0, 0.0, 0.0), result.right_joints_deg)

    def test_missing_arm_fails_closed(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "explicit right arm"):
            _parse_action('{"left":{"mode":"joints","values":[0,0,0,0,0,0]}}')
        with self.assertRaisesRegex(ValueError, "right_joints_deg"):
            parse_observation({"left_joints_deg": [0] * 6})


if __name__ == "__main__":
    unittest.main()
