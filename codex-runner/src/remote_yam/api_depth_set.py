"""Three fresh atomic RGB-D pairs from an explicit private API origin.

See docs/compact-depth-contract.md. No hardware ownership or caller URLs.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import json
import math
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
    def __init__(self, calibration_origin, depth_origin, *, robot_id="yam-1", private=False):
        super().__init__(depth_origin, camera_names=ROLES)
        self.robot_id = robot_id
        public_depth = not private and depth_origin.rstrip('/') == calibration_origin.rstrip('/')
        self.apis = {role: ApiDepth(calibration_origin, robot_id=robot_id, camera=role,
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
        return self.capture({'robot_id': self.robot_id}, stopped=stopped)

    def capture(self, observation, *, stopped=None):
        if observation.get('robot_id', observation.get('jetson_id', self.robot_id)) != self.robot_id:
            raise ValueError('Depth observation robot differs from configured robot')
        # A failed/rejected set must not expose a preceding successful set.
        self.snapshots = {}
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
        self.require_fresh(snapshots, stopped=stopped)
        self.require_post_settle(snapshots, observation)
        frames = [PairedDepthFrame(role, snapshot.image(), time.time(),
                  snapshot.metadata['captured_at'], snapshot.metadata['sensor_serial'],
                  snapshot.metadata['calibration_id']) for role, snapshot in snapshots.items()]
        self.require_fresh(snapshots, stopped=stopped)
        self.snapshots = snapshots
        return frames

    @staticmethod
    def require_post_settle(snapshots, observation):
        """Do not combine in-motion images with a later settled wrist pose.

        Source capture and hardware observation timestamps use the robot's
        Unix clock. The age ceiling alone does not establish pose consistency:
        a 3-second-old image can precede the just-completed movement. Keep the
        same fixed feedback cutoff throughout the bounded whole-set retries.
        Preflight has no moving wrist projection; offline simulation owns its
        own capture/pose synchronization and does not use this hardware gate.
        """
        if observation.get('source') != 'hardware' or not depth_robot_stopped(observation):
            return
        # ASPIRE retains the correlated Home/packet completion epoch separately
        # from newer stationary telemetry, under the same lease and step cursor.
        settled_at = observation.get('settled_after', observation.get('observed_at'))
        if type(settled_at) not in (int, float) or not math.isfinite(settled_at) or settled_at <= 0:
            raise ValueError('Settled hardware feedback needs a valid source timestamp for RGB-D')
        observed_at = observation.get('observed_at')
        if (type(observed_at) not in (int, float) or not math.isfinite(observed_at)
                or settled_at != observed_at):
            raise ValueError('Settled capture boundary requires its associated measured wrist pose')
        for role, snapshot in snapshots.items():
            times = [snapshot.metadata['captured_at']]
            times.extend(snapshot.metadata[key] for key in ('color_captured_at', 'depth_captured_at')
                         if key in snapshot.metadata)
            if any(type(t) not in (int, float) or not math.isfinite(t) for t in times):
                raise ValueError('Invalid paired capture timestamp for ' + role)
            captured_at = min(times)
            if captured_at <= settled_at:
                raise CameraUnavailable(role, f'RGB-D precedes settled feedback by '
                    f'{settled_at-captured_at:.3f}s; waiting for a post-motion frame')

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
