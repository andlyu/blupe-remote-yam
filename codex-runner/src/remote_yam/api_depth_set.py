"""Three fresh atomic RGB-D pairs from an explicit private API origin.

See docs/compact-depth-contract.md. No hardware ownership or caller URLs.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import json
import time
from urllib import error

from .api_depth import ApiDepth, StaleDepth
from .cameras import CameraFrame, CameraFrameSource, CameraUnavailable

ROLES = ('top', 'left', 'right')


@dataclass(frozen=True)
class PairedDepthFrame(CameraFrame):
    captured_at: float
    sensor_serial: str
    calibration_id: str

    def summary(self):
        return {**super().summary(), 'captured_at': self.captured_at,
                'sensor_serial': self.sensor_serial, 'calibration_id': self.calibration_id,
                'paired_with_depth': True, 'source': 'explicit_private_rgbd_api'}


class ApiDepthSet(CameraFrameSource):
    def __init__(self, calibration_origin, depth_origin):
        super().__init__(depth_origin, camera_names=ROLES)
        public_depth = depth_origin.rstrip('/') == calibration_origin.rstrip('/')
        self.apis = {role: ApiDepth(calibration_origin, camera=role,
                     depth_url=None if public_depth else depth_origin.rstrip('/') + '/cameras/' + role + '.rgbd.npz',
                     monotonic_freshness=not public_depth) for role in ROLES}
        self.snapshots = {}
        self._cancelled = lambda: False

    def capture_for_policy(self, observation, *, cancelled=lambda: False, on_event=lambda *args: None):
        self._cancelled = cancelled
        return super().capture_for_policy(observation, cancelled=cancelled, on_event=on_event)

    def capture(self, observation):
        if observation.get('robot_id', observation.get('jetson_id', 'yam-1')) != 'yam-1':
            raise ValueError('All-camera depth is configured only for YAM')
        report = json.loads(self.apis['top'].read('/calibration', 256_000))
        snapshots = {}
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {role: pool.submit(api.capture, report=report, cancelled=self._cancelled)
                       for role, api in self.apis.items()}
            for role, future in futures.items():
                try:
                    snapshots[role] = future.result()
                except (StaleDepth, TimeoutError, ConnectionError, error.HTTPError, RuntimeError) as exc:
                    raise CameraUnavailable(role, 'Fresh paired RGB-D unavailable') from exc
        frames = [PairedDepthFrame(role, snapshot.image(), time.time(),
                  snapshot.metadata['captured_at'], snapshot.metadata['sensor_serial'],
                  snapshot.metadata['calibration_id']) for role, snapshot in snapshots.items()]
        self.require_fresh(snapshots)
        self.snapshots = snapshots
        return frames

    @staticmethod
    def require_fresh(snapshots):
        for role, snapshot in snapshots.items():
            if not 0 <= snapshot.age() <= 2:
                raise CameraUnavailable(role, 'RGB-D aged while assembling camera set')
