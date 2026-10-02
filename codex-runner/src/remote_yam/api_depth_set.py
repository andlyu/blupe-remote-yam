"""Three fresh atomic RGB-D pairs from an explicit private API origin.

See docs/compact-depth-contract.md. No hardware ownership or caller URLs.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import json
import time
from urllib import error

from .api_depth import (ApiDepth, NoDepthImage, StaleDepth, depth_image_event,
                        depth_robot_stopped, require_depth_age)
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
        waiting = False
        while True:
            if cancelled():
                raise RuntimeError('Depth capture cancelled')
            try:
                frames = super().capture_for_policy(observation, cancelled=cancelled, on_event=on_event)
                if waiting:
                    depth_image_event(on_event, 'all', recovered=True)
                return frames
            except NoDepthImage as exc:
                self.snapshots = {}
                if not depth_robot_stopped(observation):
                    raise
                if not waiting:
                    depth_image_event(on_event, exc.camera)
                waiting = True
                # Retry the complete set so successful views cannot age while
                # one missing camera recovers. The run remains active and held.
                time.sleep(.1)

    def capture_preflight(self, *, stopped, cancelled=lambda: False):
        self._cancelled = cancelled
        return self.capture({'robot_id': 'yam-1'}, stopped=stopped)

    def capture(self, observation, *, stopped=None):
        if observation.get('robot_id', observation.get('jetson_id', 'yam-1')) != 'yam-1':
            raise ValueError('All-camera depth is configured only for YAM')
        report = json.loads(self.apis['top'].read('/calibration', 256_000))
        if stopped is None:
            stopped = depth_robot_stopped(observation)
        snapshots = {}
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {role: pool.submit(api.capture, report=report, cancelled=self._cancelled, stopped=stopped)
                       for role, api in self.apis.items()}
            for role, future in futures.items():
                try:
                    snapshots[role] = future.result()
                except NoDepthImage as exc:
                    exc.camera = role
                    raise
                except (StaleDepth, TimeoutError, ConnectionError, error.HTTPError, RuntimeError) as exc:
                    raise CameraUnavailable(role, 'Fresh paired RGB-D unavailable') from exc
        frames = [PairedDepthFrame(role, snapshot.image(), time.time(),
                  snapshot.metadata['captured_at'], snapshot.metadata['sensor_serial'],
                  snapshot.metadata['calibration_id']) for role, snapshot in snapshots.items()]
        self.require_fresh(snapshots, stopped=stopped)
        self.snapshots = snapshots
        return frames

    @staticmethod
    def require_fresh(snapshots, *, stopped=False):
        for role, snapshot in snapshots.items():
            try:
                require_depth_age(snapshot.age(), stopped=stopped)
            except NoDepthImage as exc:
                exc.camera = role
                raise
            except StaleDepth as exc:
                raise CameraUnavailable(role, 'RGB-D aged while assembling camera set') from exc
