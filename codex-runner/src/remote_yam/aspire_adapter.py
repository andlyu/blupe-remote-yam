"""ASPIRE f4c8939 real-YAM adapter, initially restricted to observation runs.

Uses upstream robot.adapter._target_, make_namespace and run_script.py. See
https://github.com/NVlabs/ASPIRE/tree/f4c8939aab0af9b97690c561bd80e282940f7886/aspire/real
No follower client, camera device, session creation or motor owner is created.
The packet conversion is testable separately; live execution is deliberately
unbound until station geometry, perception, recording and supervision pass.
"""
from __future__ import annotations

import json
import base64
import hashlib
import io
import math
import os
import threading
import time
from pathlib import Path

import numpy as np

from .api_depth import ApiDepth, depth_robot_stopped
from .camera_calibration import rigid
from .session import HttpSessionAPI

ASPIRE_COMMIT = 'f4c8939aab0af9b97690c561bd80e282940f7886'
CAMERAS = ('top', 'left', 'right')
TASK = 'Pick up the green block and place it on the green towel.'


class AspireNotReady(RuntimeError):
    pass


def vector(value, size, label):
    # numpy casts None to NaN; reject absent feedback explicitly.
    data = np.asarray(value, dtype=float)
    if data.shape != (size,) or not np.isfinite(data).all():
        raise AspireNotReady(f'{label}: expected {size} finite measured values')
    return data


def arm_pose(report, observation, side):
    """Published POE chain already includes model offsets; never add twice.

    Return a grasp pose in BluPe's base-midpoint frame, metres, Z=base height.
    This is NOT the calibrated ASPIRE station's floor/world frame.
    """
    from scipy.spatial.transform import Rotation

    kin = report['kinematics']
    if kin['joint_units'] != 'degrees':
        raise AspireNotReady('Unrecognized published kinematic units')
    q = np.deg2rad(vector(observation[side+'_joints_deg'], 6, side+' joints'))
    chain = kin[side]
    if len(chain['joints']) != 6:
        raise AspireNotReady('Expected six published joint axes')
    result = np.eye(4)
    for angle, joint in zip(q, chain['joints']):
        axis = vector(joint['axis'], 3, 'joint axis')
        if not np.isclose(np.linalg.norm(axis), 1, atol=1e-5):
            raise AspireNotReady('Non-unit published joint axis')
        center = vector(joint['position_m'], 3, 'joint position')
        rotation = Rotation.from_rotvec(axis*angle).as_matrix()
        transform = np.eye(4)
        transform[:3, :3] = rotation
        transform[:3, 3] = center-rotation@center
        result = result@transform
    result = result@rigid(chain['T_base_grasp_zero'])
    spacing = float(report['base_geometry']['spacing_m'])
    if not math.isfinite(spacing) or spacing <= 0:
        raise AspireNotReady('Invalid base spacing')
    result[1, 3] += spacing/2 if side == 'left' else -spacing/2
    return result


def paired_trajectory(timestamps, left, right, *, measured_left, measured_right,
                      playback_speed=1., start_interp_s=1., first_step_id=0):
    """Convert complete upstream radian joint7 paths to one 10 Hz packet.

    No submission or retry. Keep the original duration. The existing API and
    gateway validate packet budgets, velocity, adjacent changes and joint limits.
    """
    ts = np.asarray(timestamps, dtype=float)
    if ts.ndim != 1 or not len(ts) or not np.isfinite(ts).all():
        raise ValueError('Invalid timestamps')
    if np.any(np.diff(ts) <= 0):
        raise ValueError('Timestamps must strictly increase')
    if not math.isfinite(playback_speed) or playback_speed <= 0:
        raise ValueError('Invalid playback speed')
    if not math.isfinite(start_interp_s) or start_interp_s < 0:
        raise ValueError('Invalid start interpolation')
    targets = []
    measured = []
    for name, values, initial in [('left', left, measured_left), ('right', right, measured_right)]:
        arr = np.asarray(values, dtype=float)
        if arr.shape != (len(ts), 7) or not np.isfinite(arr).all():
            raise ValueError(f'{name}: require finite (N,7) joint/gripper targets')
        cur = vector(initial, 7, name+' measured joint7')
        if np.any((arr[:, 6] < 0) | (arr[:, 6] > 1)) or not 0 <= cur[6] <= 1:
            raise ValueError('Grippers must be normalized [0,1]')
        targets.append(arr)
        measured.append(cur)
    ts = (ts-ts[0])/playback_speed + start_interp_s
    duration = float(ts[-1])
    count = max(1, math.ceil(duration*10-1e-9))
    if not isinstance(first_step_id, int) or first_step_id < 0:
        raise ValueError('Invalid first step ID')
    if start_interp_s:
        ts = np.r_[0., ts]
        targets = [np.vstack([cur, arr]) for cur, arr in zip(measured, targets)]
    sample_times = np.arange(1, count+1)/10
    samples = [np.column_stack([np.interp(sample_times, ts, a[:, j]) for j in range(7)]) for a in targets]
    return [dict(step_id=first_step_id+i, left_joints_deg=np.rad2deg(l[:6]).tolist(),
                 right_joints_deg=np.rad2deg(r[:6]).tolist(), left_gripper=float(l[6]),
                 right_gripper=float(r[6])) for i, (l, r) in enumerate(zip(*samples))]


class ObservationEnvironment:
    """Read-only ASPIRE env. RGB/depth/intrinsics share one per-thread capture.

    Every new RGB request starts a new paired capture; subsequent depth and
    intrinsics calls refer to it. Recorder and policy threads cannot overwrite
    each other's snapshot. Hardware feedback with missing grippers is rejected.
    """
    camera_names = CAMERAS
    execution_mode = 'observation_only'

    def __init__(self, *, origin, robot_id, output_dir, api=None, depth_factory=ApiDepth):
        self.robot_id = robot_id
        self._camera_origin = origin
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.api = api or HttpSessionAPI(origin, robot_id=robot_id)
        self.depth = {c: depth_factory(origin, robot_id, camera=c) for c in CAMERAS}
        self._depth_locks = {c: threading.Lock() for c in CAMERAS}
        from .cameras import CameraFrameSource
        self.preview_source = CameraFrameSource(origin, camera_names=CAMERAS)
        self._local = threading.local()
        self._lock = threading.Lock()
        self._sequence = 0
        self._profile = None  # never borrow upstream station home/gain geometry
        self._motion_calls = 0
        self.cancelled = lambda: False
        self._calibration_report = None
        self._calibration_lock = threading.Lock()

    def status(self):
        obs = self.api.get_robot_observation(self.robot_id)
        if obs.get('robot_id', obs.get('jetson_id')) != self.robot_id or obs.get('source') != 'hardware':
            raise AspireNotReady('Observation robot/source mismatch')
        return obs

    def calibration(self):
        with self._calibration_lock:
            if self._calibration_report is not None:
                return json.loads(json.dumps(self._calibration_report))
            for attempt in range(3):
                if self.cancelled():
                    raise AspireNotReady('Calibration read cancelled')
                try:
                    with self._depth_locks['top']:
                        report = json.loads(self.depth['top'].read('/calibration', 256_000))
                    self._calibration_report = report
                    return json.loads(json.dumps(report))
                except (TimeoutError, ConnectionError):
                    if attempt == 2:
                        raise

    def station_info(self):
        """Agent-visible contract; configured roles are not calibration/readiness proof."""
        try:
            report = self.calibration()
            calibration_error = None
        except Exception as exc:
            report = {}
            calibration_error = type(exc).__name__
        cameras = report.get('cameras', {})
        result = dict(
            robot_id=self.robot_id, aspire_commit=ASPIRE_COMMIT,
            execution_mode=self.execution_mode, physical_motion_enabled=getattr(self, 'physical_motion_enabled', False),
            camera_roles=list(self.camera_names), camera_count=len(self.camera_names),
            absent_camera_roles=['bottom'], camera_availability='not_probed_by_this_query',
            camera_calibration={role: {
                'present_in_report': role in cameras,
                'serial': cameras.get(role, {}).get('serial'),
                'distortion': cameras.get(role, {}).get('distortion'),
                'distortion_model': cameras.get(role, {}).get('distortion_model'),
            } for role in self.camera_names},
            world_frame='blupe_base_midpoint',
            frame_origin='midpoint between arm bases, Z=0 at base height',
            base_spacing_m=report.get('base_geometry', {}).get('spacing_m'),
            table_height_m=None, gripper_geometry_status='UNVERIFIED',
            gripper_geometry=getattr(self, 'selected_gripper_geometry', None) or report.get('gripper_geometry'),
            planner_world_frame=report.get('aspire_planner', {}).get('world_frame'),
            joint_units='radians', position_units='metres',
            quaternion_order='xyzw', gripper_units='normalized_0_to_1',
            camera_optical_convention='OpenCV; adapter extrinsics need no optical flip',
            calibration_id=report.get('calibration_id'),
            calibration_quality=report.get('quality'), calibration_error=calibration_error,
            planner_status='not_commissioned', perception_status='not_connected',
            packet_hz=10, command_validation_owner='Existing API/controller/gateway',
            upstream_station_defaults_validated=False,
            warning='Upstream four-camera examples, floor/table coordinates, gripper geometry, '
                    'home poses and green-block size prior are not Robo-house facts.')
        result['segmentation_backend'] = getattr(self, 'segmentation_backend', 'bundlesdf')
        if getattr(self, 'selected_gripper_geometry', None):
            result['gripper_geometry_status'] = ('INSTALLED_MODEL_HASH_VERIFIED_CONTACT_UNMEASURED'
                if self.selected_gripper_geometry.get('source') == 'installed_i2rt_linear_4310'
                else 'UPSTREAM_DEFAULT_SELECTED_UNMEASURED')
        if getattr(self, 'planner_validation', None):
            result.update(planner_world_frame='blupe_base_midpoint',
                end_effector_frame='aspire_grasp', planner_backend='rrtconnect',
                tool_pose_frame='aspire_grasp', native_jaw_opening_axis='X',
                tool_pose_contract='Tool ee_quat, display RPY, predicted endpoints and native CAD contacts '
                    'already use aspire_grasp. API_TO_ASPIRE_GRASP is applied by the adapter; '
                    'do not apply it again to a native tool pose.',
                planner_status='native_model_exported', planner_validation=self.planner_validation)
        if result['segmentation_backend'] == 'astra':
            result.update(perception_status='configured_contour_proposals', segmentation_model='gpt-6-astra',
                segmentation_accuracy='unverified_model_proposals')
        if result['segmentation_backend'] == 'runpod_sam3':
            result.update(perception_status='configured_native_masks', segmentation_model='facebook/sam3',
                segmentation_accuracy='unverified_model_predictions')
        self._save_json('station_contract', result)
        return result

    def preview_rgb(self, camera):
        """Rectified public JPEG, exclusively for visual review, never depth pairing."""
        import io
        from PIL import Image
        if camera not in CAMERAS:
            raise AspireNotReady('Unknown preview camera')
        observation = self.recording_status()
        url = observation['images'][camera]['url']
        frame = self.preview_source.fetch(camera, url)
        path = self._save_json(camera+'_preview', frame.summary())
        path.with_suffix('.jpg').write_bytes(frame.jpeg)
        return np.asarray(Image.open(io.BytesIO(frame.jpeg)).convert('RGB'))

    def recording_status(self):
        return self.status()

    def get_observations(self, side):
        from scipy.spatial.transform import Rotation
        if side not in ('left', 'right'):
            raise ValueError('Unknown arm')
        obs = self.status()
        self._save_json('hardware_observation', obs)
        gripper = obs.get(side+'_gripper')
        if type(gripper) not in (float, int) or not math.isfinite(gripper) or not 0 <= gripper <= 1:
            raise AspireNotReady(f'{side} gripper feedback unavailable; mode={obs.get("mode")}')
        pose = arm_pose(self.calibration(), obs, side)
        if getattr(self, 'planner_validation', None):
            from .aspire_planning import API_TO_ASPIRE_GRASP
            pose[:3, :3] = pose[:3, :3]@API_TO_ASPIRE_GRASP
        return dict(joint_pos=np.deg2rad(vector(obs[side+'_joints_deg'], 6, side)),
                    gripper_pos=np.array([gripper]), ee_pos=pose[:3, 3],
                    ee_quat=Rotation.from_matrix(pose[:3, :3]).as_quat())

    def get_planning_observations(self, side):
        """Read encoder state; an idle unknown grip uses full-open collision geometry.

        This explicit preview assumption never substitutes for leased measured
        feedback during execution. Do not expose it as get_robot_state().
        """
        from scipy.spatial.transform import Rotation
        from .aspire_planning import API_TO_ASPIRE_GRASP
        if side not in ('left', 'right'):
            raise ValueError('Unknown arm')
        obs = self.status()
        grip = obs.get(side+'_gripper')
        assumed = grip is None
        if assumed:
            grip = 1.
        if type(grip) not in (float, int) or not math.isfinite(grip) or not 0<=grip<=1:
            raise AspireNotReady('Invalid planning gripper state')
        pose = arm_pose(self.calibration(), obs, side)
        pose[:3, :3] = pose[:3, :3]@API_TO_ASPIRE_GRASP
        self._save_json(side+'_planning_state', dict(observation=obs,
            assumed_gripper_for_preview=assumed, collision_gripper=grip,
            note='Only plan preview may assume full-open collision geometry; live execution requires measured grippers'))
        return dict(joint_pos=np.deg2rad(vector(obs[side+'_joints_deg'], 6, side)),
            gripper_pos=np.array([grip]), ee_pos=pose[:3, 3],
            ee_quat=Rotation.from_matrix(pose[:3, :3]).as_quat())

    def get_planning_state(self):
        from cap.agent.tools.base import ArmState, RobotState
        arms = {}
        for side in ('left', 'right'):
            obs = self.get_planning_observations(side)
            arms[side] = ArmState(obs['joint_pos'], float(obs['gripper_pos'][0]),
                obs['ee_pos'], obs['ee_quat'], [0., 0., 0.])
        return RobotState(arms=arms)

    def _save_json(self, label, value):
        with self._lock:
            self._sequence += 1
            path = self.output_dir/f'{self._sequence:05d}_{label}.json'
            path.write_text(json.dumps(value, indent=2)+'\n')
        return path

    def render_rgb(self, camera):
        if camera not in self.depth:
            raise AspireNotReady(f'Camera {camera!r} not present on this station')
        return self._capture_rgb(camera, self.status())

    def _capture_rgb(self, camera, observation):
        if camera not in self.depth:
            raise AspireNotReady(f'Camera {camera!r} not present on this station')
        if not hasattr(self._local, 'frames'):
            self._local.frames = {}
        self._local.frames.pop(camera, None)
        try:
            def camera_event(kind, message, metadata=None, **details):
                self._save_json('camera_recovery', dict(kind=kind, message=message,
                    **(metadata or {}), **details))
            from .latency_spans import timed_span
            spans = getattr(self._local, 'capture_spans', [])
            # A batch uses one selected calibration identity. Every bundle is
            # still validated against that identity and its factory profile.
            with timed_span(spans, 'calibration'):
                report = self.calibration()
            with timed_span(spans, 'depth_lock_wait'):
                self._depth_locks[camera].acquire()
            try:
                with timed_span(spans, 'paired_capture'):
                    snapshot = self.depth[camera].capture(stopped=depth_robot_stopped(observation),
                        report=report, wait_for_image=True, cancelled=self.cancelled, on_event=camera_event)
            finally:
                self._depth_locks[camera].release()
                spans.extend(getattr(self.depth[camera], 'last_capture_timing', {}).get('spans', []))
        except Exception as exc:
            detail = exc.detail() if callable(getattr(exc, 'detail', None)) else str(exc)
            self._save_json(camera+'_depth_rejected', dict(error=type(exc).__name__, detail=detail))
            raise
        self._local.frames[camera] = (snapshot, observation)
        with timed_span(spans, 'save_capture_metadata'):
            path = self._save_json(camera, dict(metadata=snapshot.metadata, observation=observation,
                                                calibration=snapshot.calibration))
        from PIL import Image
        with timed_span(spans, 'save_rgb_png'):
            Image.fromarray(snapshot.rgb).save(path.with_suffix('.png'))
        with timed_span(spans, 'save_depth_npy'):
            np.save(path.with_suffix('.depth.npy'), snapshot.depth)
        return snapshot.rgb.copy()

    def _frame(self, camera):
        frames = getattr(self._local, 'frames', {})
        if camera not in frames:
            self.render_rgb(camera)
        snapshot, obs = self._local.frames[camera]
        from .api_depth import require_depth_age
        require_depth_age(snapshot.age(), stopped=depth_robot_stopped(obs))
        return snapshot, obs

    def render_depth(self, camera):
        return self._frame(camera)[0].depth.astype(np.float32).copy()

    def camera_rgbd(self, camera='top'):
        """Wait for a paired frame newer than one fixed settled wrist pose.

        Refreshing feedback on each retry moves the cutoff ahead of the camera
        relay indefinitely. Reuse the existing bounded camera recovery loop,
        retaining this feedback and discarding each rejected snapshot.
        """
        from .cameras import CameraFrameSource, CameraUnavailable
        from .api_depth import NoDepthImage
        from urllib.error import HTTPError
        if camera not in self.depth:
            raise AspireNotReady(f'Camera {camera!r} not present on this station')
        environment = self

        class PairedCapture(CameraFrameSource):
            def capture(self, observation):
                try:
                    return environment._camera_rgbd_once(camera, observation)
                except NoDepthImage as exc:
                    # The accepted snapshot can expire during subsequent
                    # RGB/depth/pose access. Recapture the whole pair through
                    # the existing bounded recovery, with the same pose and
                    # unchanged freshness limit; never reuse its old RGB.
                    getattr(environment._local,'frames',{}).pop(camera,None)
                    raise CameraUnavailable(camera,exc.detail(),exc.http_status) from exc
                except RuntimeError as exc:
                    # ApiDepth wraps transport failures. Only its direct,
                    # transient HTTP cause belongs in camera recovery; malformed
                    # calibration, parsing, cancellation and other errors still
                    # fail immediately. Retain the same pose and freshness rules.
                    cause = exc.__cause__
                    if not isinstance(cause, HTTPError) or cause.code not in (408, 429, 500, 502, 503, 504):
                        raise
                    getattr(environment._local,'frames',{}).pop(camera,None)
                    raise CameraUnavailable(camera,str(cause),cause.code) from exc

        source = PairedCapture(self._camera_origin,
            camera_names=(camera,), max_attempts=self.preview_source.max_attempts,
            recovery_s=self.preview_source.recovery_s)
        from .latency_spans import timed_span
        spans = self._local.capture_spans = []
        started_at, started = time.time(), time.perf_counter()
        status = 'error'
        try:
            with timed_span(spans, 'observation_rpc'):
                observation = self.status()
            pose = observation.get('settled_pose')
            if pose is not None:
                boundary = observation.get('settled_after')
                if (type(boundary) not in (int, float) or not math.isfinite(boundary)
                        or boundary <= 0 or pose.get('observed_at') != boundary
                        or boundary > observation['observed_at']):
                    raise AspireNotReady('Camera completion pose does not match its settled boundary')
                for side in ('left', 'right'):
                    vector(pose[side+'_joints_deg'], 6, side+' settled camera pose')
                observation = {**observation, **pose,
                    'feedback_observed_at': observation['observed_at']}
            result = source.capture_for_policy(observation, cancelled=self.cancelled,
                on_event=lambda kind, message, metadata: self._save_json('camera_recovery',
                    dict(kind=kind, message=message, **metadata)))
            status = 'ok'
            return result
        except Exception:
            getattr(self._local, 'frames', {}).pop(camera, None)
            raise
        finally:
            try:
                self._save_json(camera+'_capture_timing', dict(camera=camera,
                    started_at=started_at, duration_s=time.perf_counter()-started,
                    status=status, spans=spans))
            except OSError:
                pass  # Timing receipts must not mask the original capture result.
            finally:
                del self._local.capture_spans

    def _camera_rgbd_once(self, camera, observation):
        rgb = self._capture_rgb(camera, observation)
        from .latency_spans import timed_span
        with timed_span(getattr(self._local, 'capture_spans', []), 'frame_geometry'):
            depth = self.render_depth(camera)
            fx, fy, cx, cy = self.get_camera_intrinsics(camera)
            extrinsics = self.get_camera_extrinsics(camera)
            transform = np.eye(4)
            transform[:3, :3] = extrinsics['rotation']
            transform[:3, 3] = extrinsics['position']
            snapshot, _ = self._frame(camera)
        return dict(camera=camera, rgb=rgb, depth_m=depth,
                    K=np.array([[fx, 0., cx], [0., fy, cy], [0., 0., 1.]]),
                    T_world_camera=transform, metadata=snapshot.metadata,
                    calibration_id=snapshot.calibration.get('calibration_id'))

    def segment_rgb(self, image, query, *, server_url):
        """Use ASPIRE BundleSDF's own SAM3 /segment with this exact image.

        Protocol: pinned tools/vision/serve_bundlesdf.py, SegmentRequest/Response.
        No portal/device fallback and no replacement color detector.
        """
        from urllib import error, request
        from PIL import Image
        rgb = np.asarray(image)
        if rgb.ndim != 3 or rgb.shape[2] != 3 or rgb.dtype != np.uint8:
            raise ValueError('Segmentation requires uint8 HxWx3 RGB')
        buffer = io.BytesIO()
        Image.fromarray(rgb).save(buffer, format='PNG')
        payload = json.dumps(dict(text=query,
            image_base64=base64.b64encode(buffer.getvalue()).decode())).encode()
        req = request.Request(server_url.rstrip('/')+'/segment', data=payload,
            headers={'Content-Type': 'application/json'}, method='POST')
        try:
            with request.urlopen(req, timeout=20) as response:
                raw = response.read(16_000_001)
        except error.URLError as exc:
            self._save_json('sam3_segment_failed', dict(query=query,
                endpoint=req.full_url, error=str(exc),
                image_sha256=hashlib.sha256(rgb.tobytes()).hexdigest()))
            raise AspireNotReady('ASPIRE BundleSDF/SAM3 segmentation unavailable at '
                                 +req.full_url+'; no mask returned') from exc
        if len(raw)>16_000_000:
            raise ValueError('Oversized ASPIRE segmentation response')
        data = json.loads(raw)
        mask = np.load(io.BytesIO(base64.b64decode(data['mask_b64'], validate=True)), allow_pickle=False)
        score = float(data['score'])
        if (mask.shape != rgb.shape[:2] or not np.isin(mask, [0, 1]).all()
                or not math.isfinite(score) or not 0<=score<=1 or not mask.any()):
            raise ValueError('Invalid ASPIRE SAM3 mask/score')
        path = self._save_json('sam3_segment', dict(query=query, score=score,
            bbox_xywh=data.get('bbox_xywh'), image_sha256=hashlib.sha256(rgb.tobytes()).hexdigest()))
        np.save(path.with_suffix('.mask.npy'), mask)
        return dict(mask=mask.astype(bool), score=score, bbox_xywh=data.get('bbox_xywh'))

    def get_camera_intrinsics(self, camera):
        snapshot, _ = self._frame(camera)
        config = snapshot.calibration['cameras'][camera]
        # Return the capture's intrinsics; retain distortion in saved metadata.
        k = config['K']
        return [k[0][0], k[1][1], k[0][2], k[1][2]]

    def get_camera_extrinsics(self, camera):
        snapshot, observation = self._frame(camera)
        report = snapshot.calibration
        if camera == 'top':
            transform = rigid(report['cameras'][camera]['T_base_camera']['left']).copy()
            transform[1, 3] += float(report['base_geometry']['spacing_m'])/2
        else:
            from .api_depth_set import ApiDepthSet
            # Existing RGB-D pairing retains its post-settle wrist convention.
            ApiDepthSet.require_post_settle({camera: snapshot}, observation)
            transform = arm_pose(report, observation, camera)@rigid(report['cameras'][camera]['T_grasp_camera'])
        return dict(position=transform[:3, 3].tolist(), rotation=transform[:3, :3].tolist(),
                    needs_optical_flip=False)

    def readiness(self):
        report = self.calibration()
        obs = self.status()
        result = dict(task=TASK, status='REPORTED', physical_commands_sent=self._motion_calls,
                      calibration_id=report.get('calibration_id'),
                      physical_motion_enabled=getattr(self, 'physical_motion_enabled', False),
                      controller_safety=obs.get('safety'), calibration_quality=report.get('quality'),
                      world_frame='blupe_base_midpoint', camera_roles=list(CAMERAS))
        self._save_json('readiness', result)
        return result

    def deny_motion(self, *args, **kwargs):
        raise AspireNotReady('This environment method has no API binding; no command sent')

    command_joint_pos = deny_motion
    command_joint_state = deny_motion
    move_bimanual_joint_keypoints = deny_motion
    _move_bimanual_joint_keypoints = deny_motion
    set_gripper = deny_motion
    go_home = deny_motion

    def set_recorder(self, recorder):
        pass  # upstream ScriptRecorder owns its capture thread


class RecordingView:
    """ASPIRE recorder uses RGB previews; perception retains atomic RGB-D."""
    camera_names = CAMERAS

    def __init__(self, env):
        self.env = env

    def render_rgb(self, camera):
        return self.env.preview_rgb(camera)

    def set_recorder(self, recorder):
        pass


class BlupeAspireAdapter:
    """Hydra extension of upstream ASPIRE using the existing API transport."""
    def __init__(self, origin, robot_id, output_dir, bridge_socket=None):
        self.options = dict(origin=origin, robot_id=robot_id, output_dir=output_dir)
        self.bridge_socket = bridge_socket

    def create_environment(self):
        if self.bridge_socket:
            from .aspire_api_policy import ApiBridgeEnvironment
            return ApiBridgeEnvironment(**self.options, bridge_socket=self.bridge_socket)
        return ObservationEnvironment(**self.options)

    def create_runtime(self, *, cfg=None, runtime_role='script', **kwargs):
        from cap.env.real_bimanual_yam.skills import make_namespace
        from cap.agent.tool_handle import make_tool_runner_namespace
        from cap.agent.robot_adapters.base import cfg_select
        if runtime_role != 'script':
            raise AspireNotReady('Only the upstream saved-script runtime is supported')
        env = self.create_environment()
        geometry_file = cfg_select(cfg, 'robot.gripper_geometry_file', None)
        if geometry_file:
            from .aspire_gripper_model import load_gripper_profile
            geometry = load_gripper_profile(geometry_file)
            if not isinstance(geometry, dict):
                raise ValueError('Gripper geometry must be an object')
            env.selected_gripper_geometry = geometry
        namespace = make_namespace(env, cfg=cfg)
        namespace.update(make_tool_runner_namespace())
        # The planner needs our frame conversion. Other native tools retain
        # their own behavior and reach the environment's actual API bindings.
        namespace['freespace_move'] = env.deny_motion
        namespace['select_best_grasp'] = env.deny_motion
        if cfg_select(cfg, 'robot.native_planner', False):
            from .aspire_planning import bind_native_planner
            namespace['freespace_move'] = bind_native_planner(env,
                repair_rrt_path=cfg_select(cfg, 'robot.native_rrt_path_repair', True))
            namespace['get_planning_state'] = env.get_planning_state
            namespace['plan_freespace_sequence'] = env.plan_freespace_sequence
            def select_best_grasp(grasp_candidates, side='right', **arguments):
                arguments.setdefault('batch_side', side)
                return namespace['freespace_move'](grasp_candidates=grasp_candidates, **arguments)
            namespace['select_best_grasp'] = select_best_grasp
        namespace['aspire_readiness'] = env.readiness
        namespace['get_station_info'] = env.station_info
        namespace['get_camera_rgbd'] = env.camera_rgbd
        if cfg_select(cfg, 'robot.upstream_geometry_skills', False):
            from .aspire_upstream_skills import bind_upstream_geometry
            namespace.update(bind_upstream_geometry(env._save_json))
        from .runpod_sam3 import selected_backend
        backend = selected_backend(cfg_select(cfg, 'robot.segmentation_backend', 'bundlesdf'))
        env.segmentation_backend = backend
        if backend == 'astra':
            from .astra_segmentation import AstraContourSegmenter
            query_file = cfg_select(cfg, 'robot.astra_queries_file', None)
            if not query_file:
                raise AspireNotReady('Supply the explicit Astra station target-query file')
            segmenter = AstraContourSegmenter(json.loads(Path(query_file).read_text()), env.output_dir/'astra')
            namespace['segment_camera_rgb'] = segmenter.segment
        elif backend == 'runpod_sam3':
            from .runpod_sam3 import RunpodSam3Client, RunpodSam3Segmenter
            query_file = cfg_select(cfg, 'robot.segmentation_queries_file',
                cfg_select(cfg, 'robot.astra_queries_file', None))
            queries = json.loads(Path(query_file).read_text()) if query_file else {}
            client = RunpodSam3Client.from_env(cancelled=env.cancelled,
                timeout_s=float(cfg_select(cfg, 'robot.runpod_timeout_s',
                    os.environ.get('RUNPOD_SAM3_TIMEOUT_S', 600))))
            segmenter = RunpodSam3Segmenter(queries, env.output_dir/'runpod-sam3',
                client=client, cancelled=env.cancelled)
            namespace['segment_camera_rgb'] = segmenter.segment
        elif backend == 'bundlesdf':
            host = cfg_select(cfg, 'robot.bundlesdf_host', '127.0.0.1')
            port = int(cfg_select(cfg, 'robot.bundlesdf_port', 8119))
            namespace['segment_camera_rgb'] = lambda image, query: env.segment_rgb(
                image, query, server_url=f'http://{host}:{port}')
        else:
            raise AspireNotReady('Unknown segmentation backend; no silent fallback')
        namespace['get_preview_image'] = env.preview_rgb
        if self.bridge_socket:
            namespace['execute_joint_trajectory'] = env.move_bimanual_joint_keypoints
        namespace['get_task_info'] = lambda: dict(success=False, reward=0., status='UNVERIFIED',
            task=TASK, reason='Task program must report its outcome; API completion alone is not task success')
        return RecordingView(env), namespace
