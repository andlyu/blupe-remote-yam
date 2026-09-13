import json
from pathlib import Path
import unittest
from remote_yam.providers import RepeatingRaiseLowerAdapter


class ApexFeedbackTests(unittest.TestCase):
    def setup_policy(self):
        data = json.loads((Path(__file__).parent/'fixtures/apex-tracking-error.json').read_text())
        context = {'source':'hardware','settled':True,'safety':{'ok':True,'estop_engaged':False}}
        provider = RepeatingRaiseLowerAdapter()
        points = provider.build_trajectory('', {**data['initial'], **context, 'homed':True}, 0)
        return provider, points, {**data['apex'], **context, 'homed':False}

    def test_measured_apex_allows_down_packet(self):
        provider, up, observed = self.setup_policy()
        provider.validate_trajectory_final(observed)
        down = provider.build_trajectory('', observed, len(up))
        self.assertEqual(len(up), 44)
        self.assertEqual(down[0]['step_id'], 44)
        self.assertEqual(len(down), 43)
        self.assertEqual(provider.completed_cycles, 0)

    def test_apex_outside_cartesian_tolerance_stops(self):
        provider, _, observed = self.setup_policy()
        observed['left_joints_deg'][2] -= 1.0
        with self.assertRaisesRegex(RuntimeError, 'apex outside task tolerance'):
            provider.validate_trajectory_final(observed)

    def test_unsettled_apex_is_still_rejected(self):
        provider, _, observed = self.setup_policy()
        observed['settled'] = False
        with self.assertRaisesRegex(RuntimeError, 'settled=true'):
            provider.validate_trajectory_final(observed)
