"""Distance is measured XYZ displacement, not orientation or jaw opening."""
import unittest
from unittest.mock import Mock
from remote_yam.robocurve_policy import OpenAIAdapter
from test_robocurve_policy import observation


class StepDistanceTests(unittest.TestCase):
    def test_xyz_mapping_across_embodiments(self):
        for names in (
            ('left_x', 'left_y', 'left_z', 'left_yaw', 'right_x', 'right_y', 'right_z'),
            ('left_x', 'left_y', 'left_z', 'left_wrist_roll', 'left_gripper', 'right_x', 'right_y', 'right_z'),
            ('x', 'y', 'z', 'wrist_roll', 'gripper'),
        ):
            with self.subTest(names=names):
                p = object.__new__(OpenAIAdapter)
                p._names = names
                before = [0.0] * len(names)
                after = [0.03, 0.04, 0.0] + [0.0] * (len(names)-3)
                after[3] = 2.0  # Rotation must not contribute to translation.
                p._geometry = Mock()
                p._geometry.observe.side_effect = [(None, before, None), (None, after, None)]
                result = p.measured_step_displacement({}, {})
                self.assertAlmostEqual(result['left_displacement_m'], .05)
                if 'right_x' in names:
                    self.assertEqual(result['right_displacement_m'], 0)
                else:
                    self.assertNotIn('right_displacement_m', result)

    def test_real_yam_kinematics_from_measured_joints(self):
        p = OpenAIAdapter('test', 'astra')
        start = observation()
        end = {**start, 'left_joints_deg': list(start['left_joints_deg'])}
        end['left_joints_deg'][0] += 10
        result = p.measured_step_displacement(start, end)
        self.assertGreater(result['left_displacement_m'], .001)
        self.assertAlmostEqual(result['right_displacement_m'], 0)
        zero = p.measured_step_displacement(start, start)
        self.assertEqual(zero, {'left_displacement_m': 0, 'right_displacement_m': 0})
