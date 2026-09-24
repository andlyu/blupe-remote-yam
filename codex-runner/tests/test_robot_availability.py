import time
import unittest
from types import SimpleNamespace
from playground import HostedRunner

class AvailabilityTests(unittest.TestCase):
    def test_catalog_reads_each_robots_fresh_monitor(self):
        yam=HostedRunner.__new__(HostedRunner);yam.robot_id='yam-1'
        yam.queue={'stations':[{'jetson_id':'yam-1','connected':False}]};yam.queue_observed_at=time.monotonic()
        other=SimpleNamespace(queue={'stations':[{'jetson_id':'other','connected':True}]},queue_observed_at=time.monotonic())
        yam._fleet_apps={'yam-1':yam,'other':other}
        self.assertFalse(yam.robot_connected('yam-1'));self.assertTrue(yam.robot_connected('other'))
        other.queue_observed_at-=11;self.assertIsNone(yam.robot_connected('other'))
        self.assertIsNone(yam.robot_connected('missing'))

    def test_missing_station_offline_but_failed_fetch_unknown(self):
        yam=HostedRunner.__new__(HostedRunner);yam.robot_id='yam-1';yam.queue_observed_at=time.monotonic()
        yam.queue={'stations':[]};self.assertFalse(yam.robot_connected('yam-1'))
        yam.queue=None;self.assertIsNone(yam.robot_connected('yam-1'))
