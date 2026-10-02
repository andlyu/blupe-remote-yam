"""Read-only RGB-D observations from the public YAM API.

Contract: docs/api-depth-contract.md, rgbd-npz-v1 (2026-10-01).
AGP reference: agent-as-policy commit c6875d0, interface-bare.md / frames.
No camera ownership or motor access; failed capture never falls back to RGB.
"""
import io
import http.client
import json
import math
import time
import zipfile
from urllib import error
from urllib.parse import quote, urlsplit

import numpy as np
from PIL import Image

from .cameras import trusted_origin
from .camera_calibration import matrix, rigid

MAX_BYTES = 16_000_000
MAX_DEPTH_AGE_S = 5.0
MOVING_DEPTH_AGE_S = 2.0


def depth_robot_stopped(observation):
    return (observation.get('settled') is True
            and observation.get('mode') not in ('EXECUTING', 'HOMING'))


def require_depth_age(age, *, stopped=False):
    if not math.isfinite(age) or age < 0:
        raise StaleDepth('Invalid depth capture age')
    if stopped is True:
        if age > MAX_DEPTH_AGE_S:
            raise NoDepthImage()
    elif age >= MOVING_DEPTH_AGE_S:
        raise NoDepthImage()


def depth_image_event(on_event, camera, *, recovered=False):
    state = 'recovered' if recovered else 'retrying'
    message = 'Camera feeds recovered. Continuing the run.' if recovered else 'No image: waiting for depth.'
    on_event('camera_recovered' if recovered else 'camera_retry', message,
             dict(camera=camera, state=state, cause=None if recovered else 'No image'))


def validate_factory_profile(profile, image_size):
    if (not isinstance(profile, dict) or
            [profile.get('width'), profile.get('height')] != image_size or
            any(type(profile.get(k)) not in (int, float) or not math.isfinite(profile[k])
                for k in ('fx','fy','ppx','ppy')) or min(profile['fx'],profile['fy']) <= 0 or
            profile.get('model') not in ('distortion.none','distortion.inverse_brown_conrady',
                                         'distortion.brown_conrady')):
        raise ValueError('Invalid aligned-depth factory profile')
    matrix(profile.get('coeffs'), (5,), 'factory distortion')
    if profile['model'] == 'distortion.none' and any(profile['coeffs']):
        raise ValueError('Undistorted profile has nonzero distortion')


def factory_ray(profile, u, v):
    """SDK optical-Z ray, librealsense v2.58.1 src/rs.cpp:4294–4339.

    Inverse Brown's tangential terms use xq/yq, unlike OpenCV Brown.
    Oracle: tests/fixtures/realsense-deprojection-2.58.1.json; see API contract.
    """
    x, y = (u-profile['ppx'])/profile['fx'], (v-profile['ppy'])/profile['fy']
    xo, yo = x, y
    k1,k2,p1,p2,k3 = profile['coeffs']
    if profile['model'] != 'distortion.none' and any(profile['coeffs']):
        for _ in range(10):
            r2 = x*x+y*y
            radial = 1+((k3*r2+k2)*r2+k1)*r2
            if not math.isfinite(radial) or abs(radial) < 1e-8:
                raise ValueError('Factory deprojection failed')
            xq,yq = (x*radial,y*radial) if profile['model']=='distortion.inverse_brown_conrady' else (x,y)
            dx,dy = 2*p1*xq*yq+p2*(r2+2*xq*xq), 2*p2*xq*yq+p1*(r2+2*yq*yq)
            x,y = (xo-dx)/radial, (yo-dy)/radial
    if not math.isfinite(x) or not math.isfinite(y):
        raise ValueError('Factory deprojection failed')
    return np.array([x,y,1.])


class ApiDepth:
    def __init__(self, origin, robot_id='yam-1', *, camera='top', depth_url=None,
                 monotonic_freshness=False, timeout=5, clock=time.time):
        if camera not in ('top', 'left', 'right'):
            raise ValueError('Unknown depth camera role')
        if monotonic_freshness and depth_url is None:
            raise ValueError('Monotonic source age requires an explicit private API')
        trusted_origin(origin)
        self.camera, self.monotonic_freshness = camera, monotonic_freshness
        self.origin = origin.rstrip('/')
        self.robot_id, self.timeout, self.clock = robot_id, timeout, clock
        self.route = '/v1/robots/' + quote(robot_id, safe='')
        target = urlsplit(self.origin)
        connection = http.client.HTTPSConnection if target.scheme == 'https' else http.client.HTTPConnection
        self.connection = connection(target.hostname,target.port,timeout=timeout)
        self.depth_connection = self.connection
        self.depth_suffix = '/cameras/' + camera + '.rgbd.npz'
        self.depth_path = self.route + self.depth_suffix
        self.depth_url = self.origin + self.depth_path
        self.private_depth = depth_url is not None
        if depth_url is not None:
            target = urlsplit(depth_url)
            # Explicit private camera API override. Never follow redirects or
            # attach credentials; the bundle identity/calibration checks remain.
            trusted_origin(target.scheme + '://' + target.netloc)
            if not target.path.startswith('/') or target.query or target.fragment:
                raise ValueError('Depth URL requires an absolute path without query or fragment')
            connection = http.client.HTTPSConnection if target.scheme == 'https' else http.client.HTTPConnection
            self.depth_connection = connection(target.hostname,target.port,timeout=timeout)
            self.depth_path, self.depth_url = target.path, depth_url
        self.calibration_id = None

    def read(self, suffix, limit=MAX_BYTES):
        # Python http.client: read the whole bounded response before reusing TLS.
        # No redirects; http.client returns the original response status.
        depth = suffix == self.depth_suffix
        connection = self.depth_connection if depth else self.connection
        path = self.depth_path if depth else self.route + suffix
        url = self.depth_url if depth else self.origin + path
        try:
            started = time.monotonic()
            deadline = started + self.timeout
            connection.request('GET',path,headers={'Cache-Control':'no-cache'})
            response=connection.getresponse()
            parts, size = [], 0
            while True:
                if time.monotonic() >= deadline:
                    raise TimeoutError('Depth API transfer deadline exceeded')
                chunk=response.read1(min(65536,limit+1-size))
                if not chunk:
                    break
                parts.append(chunk)
                size += len(chunk)
                if size > limit:
                    raise ValueError('Depth API response exceeds its size limit')
            data=b''.join(parts)
            status=response.status
            headers = {k.lower(): v for k,v in response.getheaders()} if self.monotonic_freshness and depth else {}
            response.close()
            if len(data)>limit:
                raise ValueError('Depth API response exceeds its size limit')
            if depth and status in (204, 503):
                self._depth_receipt = None
                raise NoDepthImage(self.camera)
            if status != 200:
                raise error.HTTPError(url,status,'Depth API read failed',{},None)
            if self.monotonic_freshness and depth:
                source_age = float(headers['x-source-capture-age-s'])
                elapsed = time.monotonic() - started
                if not math.isfinite(source_age) or source_age < 0 or not 0 <= elapsed:
                    raise StaleDepth('Invalid private source age')
                self._depth_receipt = (headers, source_age + elapsed, time.monotonic())
        except NoDepthImage:
            raise  # The complete empty response can reuse its connection.
        except Exception:
            connection.close()
            raise
        return data

    def capture(self, *, cancelled=lambda: False, report=None, stopped=False,
                wait_for_image=False, on_event=lambda *args: None):
        # Retry observation reads only, never motion. Cloud depth has brief gaps.
        deadline = time.monotonic() + 8
        waiting = False
        while True:
            if cancelled():
                raise RuntimeError('Depth capture cancelled')
            try:
                calibration = report if report is not None else json.loads(self.read('/calibration', 256_000))
                data = self.read(self.depth_suffix)
                receipt = self._depth_receipt if self.monotonic_freshness else None
                snapshot = self.decode(data, calibration, receipt=receipt, stopped=stopped)
                if cancelled():
                    raise RuntimeError('Depth capture cancelled')
                if waiting:
                    depth_image_event(on_event, self.camera, recovered=True)
                return snapshot
            except NoDepthImage as exc:
                exc.camera = self.camera
                if not wait_for_image:
                    raise
                if not waiting:
                    depth_image_event(on_event, self.camera)
                waiting = True
                # No image is a recoverable observation gap, not a failed run.
                # Keep the ordinary timeout budget for separate transport errors.
                deadline = time.monotonic() + 8
            except error.HTTPError as exc:
                if exc.code != 503 or time.monotonic() >= deadline:
                    raise RuntimeError('Fresh API depth unavailable; no motion proposed') from exc
            except StaleDepth:
                if time.monotonic() >= deadline:
                    raise RuntimeError('API depth aged during transfer; no motion proposed') from None
            except (TimeoutError, ConnectionError):
                if time.monotonic() >= deadline:
                    raise RuntimeError('API depth transfer failed; no motion proposed') from None
            time.sleep(.1)

    def decode(self, data, report, *, receipt=None, stopped=False):
        if len(data) > MAX_BYTES:
            raise ValueError('RGB-D bundle exceeds its size limit')
        if report.get('schema_version') != 1 or report.get('robot_id') != self.robot_id:
            raise ValueError('API calibration robot/schema mismatch')
        camera = report['cameras'][self.camera]
        quality = report['quality']
        if (quality.get('status') == 'rejected' or quality.get('failures')) and quality.get('operator_selected') is not True:
            raise ValueError('Failed calibration requires explicit operator selection')
        matrix(camera['K'], (3, 3), 'K')
        matrix(camera['distortion'], (5,), 'distortion')
        if camera['distortion_model'] != 'opencv_brown_conrady':
            raise ValueError('Unsupported depth camera distortion')
        if min(camera['K'][0][0], camera['K'][1][1]) <= 0:
            raise ValueError('Invalid camera focal length')
        if self.camera == 'top':
            for transform in camera['T_base_camera'].values():
                rigid(transform)
        else:
            rigid(camera['T_grasp_camera'])
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            entries = z.infolist()
            names = set(z.namelist())
            compact = names == {'rgb_jpeg.npy', 'depth_bytes.npy', 'metadata.npy'}
            millimeters = names == {'rgb_jpeg.npy', 'depth_mm.npy', 'metadata.npy'}
            if (len(entries) != 3 or
                    (names != {'rgb.npy', 'depth_m.npy', 'metadata.npy'}
                     and not ((compact or millimeters) and self.private_depth))
                    or sum(x.file_size for x in entries) > MAX_BYTES):
                raise ValueError('Invalid RGB-D archive')
            arrays = {}
            for entry in entries:
                raw = z.read(entry)
                stream = io.BytesIO(raw)
                version = np.lib.format.read_magic(stream)
                if version not in ((1, 0), (2, 0)):
                    raise ValueError('Unsupported NPY header')
                reader = (np.lib.format.read_array_header_1_0 if version == (1, 0)
                          else np.lib.format.read_array_header_2_0)
                shape, _, dtype = reader(stream)
                if (dtype.hasobject or not dtype.itemsize or any(n < 0 for n in shape)
                        or math.prod(shape) * dtype.itemsize != len(raw) - stream.tell()):
                    raise ValueError('Invalid bounded NPY payload')
                arrays[entry.filename] = np.load(io.BytesIO(raw), allow_pickle=False)
        meta = arrays['metadata.npy']
        if meta.shape != () or meta.dtype.kind != 'U' or meta.nbytes > 65536:
            raise ValueError('Invalid depth metadata')
        meta = json.loads(meta.item())
        w, h = camera['image_size']
        if type(w) is not int or type(h) is not int or min(w,h) <= 0 or w*h*4 > MAX_BYTES:
            raise ValueError('Invalid bounded depth image dimensions')
        if compact or millimeters:
            # docs/compact-depth-contract.md: lossless float byte planes,
            # JPEG color from the same frameset, with unchanged pixel geometry.
            format_name = 'rgbd-mm-npz-v1' if millimeters else 'rgbd-compact-npz-v1'
            dtype_name = '<u2' if millimeters else '<f4'
            if (not isinstance(meta,dict) or meta.get('transport_format') != format_name
                    or meta.get('rgb_encoding') != 'jpeg' or meta.get('depth_dtype') != dtype_name):
                raise ValueError('Invalid compact depth contract')
            jpeg = arrays['rgb_jpeg.npy']
            if jpeg.dtype != np.uint8 or jpeg.ndim != 1 or not 4 <= jpeg.size <= 4_000_000:
                raise ValueError('Invalid compact RGB-D dimensions/dtype')
            if millimeters:
                mm = arrays['depth_mm.npy']
                if (mm.dtype != np.dtype('<u2') or mm.shape != (h,w)
                        or meta.get('wire_depth_units') != 'millimeters'
                        or meta.get('depth_quantization_m') != .001
                        or meta.get('depth_max_error_m') != .0005):
                    raise ValueError('Invalid millimeter depth contract')
                depth = mm.astype('float32') / np.float32(1000)
            else:
                planes = arrays['depth_bytes.npy']
                if planes.dtype != np.uint8 or planes.shape != (4,h,w):
                    raise ValueError('Invalid compact RGB-D dimensions/dtype')
                depth = planes.transpose(1,2,0).copy().reshape(-1).view('<f4').reshape(h,w)
            with Image.open(io.BytesIO(jpeg.tobytes())) as color:
                if color.format != 'JPEG' or color.size != (w,h):
                    raise ValueError('Compact RGB image differs from calibration')
                rgb = np.asarray(color.convert('RGB')).copy()
        else:
            rgb, depth = arrays['rgb.npy'], arrays['depth_m.npy']
        if not millimeters and any(k in meta for k in ('depth_quantization_m','depth_max_error_m')):
            raise ValueError('Unrecognized depth quantization')
        if rgb.shape != (h, w, 3) or rgb.dtype != np.uint8 or depth.shape != (h, w) or depth.dtype != np.float32:
            raise ValueError('Depth dimensions/dtype differ from calibration')
        expected = dict(schema_version=1, robot_id=self.robot_id, camera=self.camera,
                        calibration_id=report['calibration_id'], depth_units='meters',
                        depth_aligned_to='color', depth_semantics='optical_z',
                        depth_coordinate_frame='color_optical')
        if not isinstance(meta, dict) or any(meta.get(k) != v for k, v in expected.items()):
            raise ValueError('Depth identity, calibration, frame or units mismatch')
        if 'depth_intrinsics' in camera:
            profile = camera['depth_intrinsics']
            validate_factory_profile(profile, camera['image_size'])
            if (meta.get('factory_color_intrinsics') != profile or
                    meta.get('sensor_serial') != camera['depth_sensor_serial']):
                raise ValueError('Depth sensor/factory profile differs from the calibrated package')
        captured = meta.get('captured_at')
        if type(captured) not in (int, float) or not math.isfinite(captured):
            raise ValueError('Invalid depth capture time')
        age_upper = None
        if self.monotonic_freshness:
            if receipt is None:
                raise ValueError('Private source age receipt required')
            headers, age_at_receipt, received_mono = receipt
            if (captured <= 0 or float(headers['x-captured-at']) != captured
                    or headers['x-robot-id'] != self.robot_id
                    or headers['x-camera-role'] != self.camera
                    or headers['x-camera-serial'] != meta.get('sensor_serial')
                    or headers['x-calibration-id'] != report['calibration_id']):
                raise ValueError('Private depth response identity mismatch')
            age_upper = age_at_receipt + time.monotonic() - received_mono
            require_depth_age(age_upper, stopped=stopped)
        else:
            require_depth_age(self.clock() - captured, stopped=stopped)
        if np.isinf(depth).any() or (depth < 0).any() or not (np.isfinite(depth) & (depth > 0)).any():
            raise ValueError('Depth is invalid or entirely missing')
        if self.calibration_id not in (None, report['calibration_id']):
            raise ValueError('API calibration changed during this trial')
        self.calibration_id = report['calibration_id']
        return DepthSnapshot(rgb, depth, meta, report, data, age_upper_s=age_upper)


class StaleDepth(ValueError):
    pass


class NoDepthImage(StaleDepth):
    def __init__(self, camera=None):
        super().__init__('No image')
        self.camera = camera


class DepthSnapshot:
    def __init__(self, rgb, depth, metadata, calibration, bundle, *, age_upper_s=None):
        self.rgb, self.depth, self.metadata = rgb, depth, metadata
        self.calibration, self.bundle = calibration, bundle
        self.age_upper_s, self.received_monotonic = age_upper_s, time.monotonic()

    def age(self):
        return (time.time() - self.metadata['captured_at'] if self.age_upper_s is None
                else self.age_upper_s + time.monotonic() - self.received_monotonic)

    def image(self, depth=False):
        if not depth:
            image = Image.fromarray(self.rgb)
        else:
            # Fixed scale: 0–2 m, near white / far blue; missing is magenta.
            valid = np.isfinite(self.depth) & (self.depth > 0)
            value = np.clip(self.depth / 2., 0, 1)
            value = np.nan_to_num(value)
            pixels = np.stack([255*(1-value), 255*(1-value), np.full_like(value, 255)], -1).astype('uint8')
            pixels[~valid] = [255, 0, 255]
            image = Image.fromarray(pixels)
        out = io.BytesIO()
        image.save(out, format='PNG')
        return out.getvalue()

    def measure(self, u, v, radius=2, arm='left', *, T_base_camera=None):
        h, w = self.depth.shape
        if (type(u) is not int or type(v) is not int or type(radius) is not int
                or not 0 <= u < w or not 0 <= v < h or not 0 <= radius <= 5
                or arm not in ('left', 'right')):
            raise ValueError('Pixel query requires in-bounds integer u/v, radius 0–5, and an arm')
        patch = self.depth[max(0,v-radius):min(h,v+radius+1), max(0,u-radius):min(w,u+radius+1)]
        values = patch[np.isfinite(patch) & (patch > 0)]
        if not len(values):
            raise ValueError('No measured depth at this pixel; choose an interior pixel')
        z = float(np.median(values))
        spread = float(np.max(values) - np.min(values))
        quantization = self.metadata.get('depth_quantization_m',0)
        if quantization and len(values)>1:
            spread += quantization
        if spread > .025:
            raise ValueError('Depth patch crosses surfaces or is noisy; choose an interior pixel')
        role = self.metadata['camera']
        c = self.calibration['cameras'][role]
        if 'depth_intrinsics' in c:
            x,y,_ = factory_ray(c['depth_intrinsics'], u,v)
            intrinsic_source = 'factory_aligned_color'
        else:
            ray = np.linalg.solve(np.asarray(c['K']), [u, v, 1.])[:2]
            x, y = ray
            k1, k2, p1, p2, k3 = c['distortion']
            # Legacy board-fitted RGB profile; keep its explicit convention.
            for _ in range(50):
                r2 = x*x + y*y
                radial = 1 + k1*r2 + k2*r2*r2 + k3*r2*r2*r2
                dx, dy = 2*p1*x*y + p2*(r2+2*x*x), p1*(r2+2*y*y)+2*p2*x*y
                if not math.isfinite(radial) or abs(radial) < 1e-8:
                    raise ValueError('Distortion inversion failed')
                x, y = (ray[0]-dx)/radial, (ray[1]-dy)/radial
            r2 = x*x+y*y
            predicted = np.array([x*(1+k1*r2+k2*r2*r2+k3*r2*r2*r2)+2*p1*x*y+p2*(r2+2*x*x),
                                  y*(1+k1*r2+k2*r2*r2+k3*r2*r2*r2)+p1*(r2+2*y*y)+2*p2*x*y])
            if not np.isfinite(predicted).all() or np.linalg.norm(predicted-ray) > 1e-7:
                raise ValueError('Distortion inversion did not converge')
            intrinsic_source = 'board_fitted_rgb_legacy'
        if T_base_camera is None:
            if role != 'top':
                raise ValueError('Wrist depth requires the current measured camera pose')
            T_base_camera = c['T_base_camera'][arm]
        point = rigid(T_base_camera) @ [x*z, y*z, z, 1.]
        return dict(pixel=[u,v], optical_z_m=z, valid_samples=int(len(values)),
                    intrinsics_source=intrinsic_source,
                    patch_spread_m=spread, point_base_m=point[:3].tolist(), arm=arm, camera=role,
                    depth_quantization_m=quantization,
                    depth_max_error_m=self.metadata.get('depth_max_error_m',0),
                    captured_at=self.metadata['captured_at'], calibration_id=self.metadata['calibration_id'],
                    quality=self.calibration['quality'],
                    warning='Measured surface point, not a gripper target. Extrinsics quality still applies.')
