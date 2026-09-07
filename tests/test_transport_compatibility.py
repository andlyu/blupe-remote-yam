import pathlib
import sys
import unittest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'src'))
from remote_yam.controller import RunnerController
from remote_yam.providers import ScriptedAdapter
from remote_yam.ik import IKCommand, ArmIKCommand
from remote_yam.session import MockSessionAPI, HttpSessionAPI

class SingleActionAPI(MockSessionAPI):
    supports_trajectories = False
    def submit_trajectory(self, *args, **kwargs):
        raise AssertionError('Unsupported route must never be attempted')

class DualProvider(ScriptedAdapter):
    def build_trajectory(self, *args):
        raise AssertionError('Must choose infer before constructing any batch')

class CompatibilityTests(unittest.TestCase):
    def test_queue_admission_does_not_read_hardware_or_require_enablement(self):
        class Offline(SingleActionAPI):
            def get_robot_observation(self, robot):
                raise AssertionError("Queue admission must not read hardware")
        api = Offline(auto_activate=False)
        controller = RunnerController(api, hardware_control_enabled=False)
        result = controller.join(DualProvider([]), "wait in queue")
        self.assertEqual(result["status"], "queued")
        self.assertEqual(len(api.create_requests), 1)
        self.assertEqual(api.action_log, [])

    def test_early_hardware_observation_waits_without_stop_or_dispatch(self):
        api = SingleActionAPI(observations=[dict(source="hardware", left_joints_deg=[0]*6, right_joints_deg=[0]*6)])
        controller = RunnerController(api, hardware_control_enabled=False)
        controller.join(DualProvider([]), "wait for arms")
        for _ in range(5):
            controller.process_next_event(0.01)
            if controller.status()["execution_blocked_reason"]:
                break
        self.assertEqual(controller.status()["status"], "running")
        self.assertIsNotNone(controller.status()["execution_blocked_reason"])
        self.assertIsNone(controller.status()["error"])
        self.assertEqual(api.action_log, [])

    def test_production_transport_uses_known_single_action_protocol(self):
        self.assertIs(HttpSessionAPI('https://example.invalid').supports_trajectories, False)

    def test_dual_provider_uses_actions_without_attempting_trajectory_route(self):
        api = SingleActionAPI()
        controller = RunnerController(api)
        provider = DualProvider([IKCommand(ArmIKCommand('joints', (0,)*6), ArmIKCommand('joints', (0,)*6))])
        controller.join(provider, 'Hold current target')
        for _ in range(20):
            if controller.status()['status'] == 'resetting':
                break
            controller.process_next_event(0.01)
        self.assertEqual([a["step_id"] for a in api.action_log], [0, 1, 2])
        self.assertIsNone(controller.status()['trajectory'])
        self.assertEqual(controller.status()['command_transport'], 'single_action')

if __name__ == '__main__':
    unittest.main()
