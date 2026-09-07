import time
import unittest

from remote_yam.viewer import YamBimanualViewer, _joint_vector


class TwinViewerTests(unittest.TestCase):
    def test_exact_bimanual_mapping_and_canonical_render(self):
        observation = {
            "left_joints_deg": [0.0] * 6,
            "right_joints_deg": [0.0] * 6,
        }
        self.assertEqual((12,), _joint_vector(observation).shape)
        with self.assertRaisesRegex(ValueError, "left and right six-joint"):
            _joint_vector({"left_joints_deg": [0.0] * 6})

        viewer = YamBimanualViewer(lambda: observation)
        viewer.start()
        try:
            deadline = time.monotonic() + 8
            while viewer.frame() is None and viewer.error() is None and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertIsNone(viewer.error())
            self.assertTrue(viewer.frame().startswith(b"\x89PNG\r\n\x1a\n"))
        finally:
            viewer.stop()


if __name__ == "__main__":
    unittest.main()
