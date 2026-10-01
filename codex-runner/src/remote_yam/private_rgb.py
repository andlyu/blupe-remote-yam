"""Bounded wrist snapshots from the explicit loopback YAM API exporter.

Contract: docs/compact-depth-contract.md, Local wrist RGB snapshots.
No camera ownership, caller-selected routes, credentials or redirects.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import http.client
import io
import math
import time
from urllib.parse import urlsplit

from PIL import Image

from .cameras import CameraFrame, CameraFrameSource, CameraUnavailable

SERIALS = {'left': '402323071768', 'right': '402323071268'}


@dataclass(frozen=True)
class PrivateRgbFrame(CameraFrame):
    captured_at: float
    serial: str

    def summary(self):
        return {**super().summary(), 'captured_at': self.captured_at,
                'serial': self.serial, 'source': 'explicit_local_rgb_api'}


class PrivateRgbSource(CameraFrameSource):
    def __init__(self, rgb_origin, *, timeout_s=2, clock=time.time):
        super().__init__(rgb_origin, timeout_s=timeout_s, camera_names=('left', 'right'))
        target = urlsplit(rgb_origin)
        cls = http.client.HTTPSConnection if target.scheme == 'https' else http.client.HTTPConnection
        self.connections = {name: cls(target.hostname, target.port, timeout=timeout_s)
                            for name in self.camera_names}
        self.clock, self.calibration = clock, None

    def set_calibration(self, report):
        if report.get('robot_id') != 'yam-1' or report.get('schema_version') != 1:
            raise ValueError('Local RGB calibration identity mismatch')
        self.calibration = report

    def capture(self, observation):
        if observation.get('robot_id', observation.get('jetson_id', 'yam-1')) != 'yam-1':
            raise ValueError('Local wrist RGB is configured only for YAM')
        if self.calibration is None:
            raise ValueError('Local RGB requires API calibration')
        # Fixed named routes; observation URLs cannot redirect this transport.
        with ThreadPoolExecutor(max_workers=2) as pool:
            frames = list(pool.map(self._capture_one, self.camera_names))
        for frame in frames:
            if not 0 <= self.clock() - frame.captured_at <= 2:
                raise CameraUnavailable(frame.name, 'Frame aged while capturing other view')
        return frames

    def _decode(self, name, body, headers):
        if (headers.get('x-robot-id') != 'yam-1'
                or headers.get('x-camera-role') != name
                or headers.get('x-camera-serial') != SERIALS[name]):
            raise ValueError('Local RGB camera identity mismatch')
        if (headers.get('content-type') != 'image/jpeg'
                or not 4 <= len(body) <= 4_000_000
                or not body.startswith(b'\xff\xd8') or not body.endswith(b'\xff\xd9')):
            raise ValueError('Local RGB requires a bounded complete JPEG')
        with Image.open(io.BytesIO(body)) as image:
            if image.format != 'JPEG' or list(image.size) != self.calibration['cameras'][name]['image_size']:
                raise ValueError('Local RGB dimensions differ from API calibration')
            image.verify()
        captured = float(headers['x-captured-at'])
        received = self.clock()
        if not math.isfinite(captured) or not 0 <= received - captured <= 2:
            raise CameraUnavailable(name, 'Local RGB is stale or has an invalid capture time')
        return PrivateRgbFrame(name, body, received, captured, SERIALS[name])

    def _capture_one(self, name):
        connection = self.connections[name]
        try:
            deadline = time.monotonic() + self.timeout_s
            connection.request('GET', '/cameras/' + name + '.jpg', headers={'Cache-Control': 'no-cache'})
            response = connection.getresponse()
            headers = {key.lower(): value for key, value in response.getheaders()}
            if response.status != 200:
                raise CameraUnavailable(name, 'Local RGB API unavailable', response.status,
                                        response.status in (408, 429, 500, 502, 503, 504))
            length = headers.get('content-length', '')
            if not length.isdigit() or not 4 <= int(length) <= 4_000_000:
                raise ValueError('Local RGB frame size is invalid')
            body = bytearray()
            while len(body) < int(length):
                if time.monotonic() >= deadline:
                    raise TimeoutError('Local RGB transfer deadline exceeded')
                part = response.read1(min(65536, int(length) - len(body)))
                if not part:
                    raise ValueError('Local RGB frame is truncated')
                body.extend(part)
            response.close()
            return self._decode(name, bytes(body), headers)
        except Exception as exc:
            connection.close()
            if isinstance(exc, CameraUnavailable):
                raise
            raise CameraUnavailable(name, type(exc).__name__, retryable=isinstance(exc, (TimeoutError, ConnectionError))) from None
