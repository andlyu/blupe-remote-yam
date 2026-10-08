"""Real scene capture and explicitly recorded, motion-disabled planning replay."""
import copy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, is_dataclass
import json
from pathlib import Path
import time

import numpy as np
from PIL import Image
from remote_yam.aspire_adapter import BlupeAspireAdapter, ObservationEnvironment
from remote_yam.aspire_code_runtime import plain
from remote_yam.astra_segmentation import immutable_scene_contour_request


class SnapshotEnvironment(ObservationEnvironment):
    execution_mode = 'recorded_plan_only'

    def __init__(self, *, snapshot, **kwargs):
        super().__init__(**kwargs)
        self.snapshot = Path(snapshot)
        self.scene = json.loads((self.snapshot/'snapshot.json').read_text())

    def status(self):
        return copy.deepcopy(self.scene['observation'])

    def calibration(self):
        return copy.deepcopy(self.scene['calibration'])

    def camera_rgbd(self, camera='top'):
        if camera not in self.camera_names:
            raise ValueError('Camera unavailable: '+camera)
        frame = dict(self.scene['frames'][camera])
        frame['rgb'] = np.asarray(Image.open(self.snapshot/(camera+'.png')).convert('RGB'))
        frame['depth_m'] = np.load(self.snapshot/(camera+'.depth.npy'))
        frame['K'] = np.asarray(frame['K'])
        frame['T_world_camera'] = np.asarray(frame['T_world_camera'])
        return frame

    def preview_rgb(self, camera):
        return self.camera_rgbd(camera)['rgb']

    def render_rgb(self, camera):
        return self.preview_rgb(camera)


class AgentSnapshotAdapter(BlupeAspireAdapter):
    def __init__(self, *, snapshot=None, cache_root=None, **kwargs):
        super().__init__(**kwargs)
        if snapshot and self.bridge_socket:
            raise ValueError('Recorded planning cannot use a live bridge')
        self.snapshot, self.cache_root = snapshot, cache_root

    def create_environment(self):
        self.environment = (SnapshotEnvironment(**self.options, snapshot=self.snapshot)
            if self.snapshot else super().create_environment())
        return self.environment

    def create_runtime(self, **kwargs):
        recording, namespace = super().create_runtime(**kwargs)
        namespace['snapshot_context'] = lambda: dict(
            observation=self.environment.status(), calibration=self.environment.calibration())
        # Replay only a validated proposal for the exact image AND target
        # registry. The normal segmenter rerasterizes and saves provenance.
        segmenter = getattr(namespace['segment_camera_rgb'], '__self__', None)
        if self.cache_root and self.snapshot and segmenter is not None:
            # Include the immutable capture's owning run so a fresh coding
            # conversation retains its original proposals. Live execution has
            # no recorded snapshot and always segments its fresh capture anew.
            segmenter.request = immutable_scene_contour_request(
                [self.cache_root, Path(self.snapshot).parents[1]],
                self.environment.scene['frames'], segmenter.queries)
        return recording, namespace


def capture_scene(namespace, path, *, directory, record_name='scene.json'):
    path, directory = Path(path), Path(directory)
    path.mkdir(parents=True, exist_ok=False)
    context = namespace['snapshot_context']()
    station = namespace['get_station_info']()
    state = namespace['get_planning_state']()
    state = plain(asdict(state) if is_dataclass(state) else state)
    cameras = ('top', 'left', 'right')

    def fetch(camera):
        started_at, started = time.time(), time.perf_counter()
        try:
            # Keep all RGB/depth/calibration accesses in this worker: the
            # environment's accepted paired frame is thread-local.
            frame = namespace['get_camera_rgbd'](camera)
        except Exception as exc:
            if hasattr(exc, 'add_note'):
                exc.add_note('While capturing '+camera+' RGB-D for the ASPIRE scene')
            raise
        return frame, dict(started_at=started_at, completed_at=time.time(),
            duration_s=time.perf_counter()-started)

    capture_started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix='aspire-rgbd') as pool:
        futures = {camera: pool.submit(fetch, camera) for camera in cameras}
        captures = {camera: future.result() for camera, future in futures.items()}
    capture_timing = dict(mode='parallel', wall_time_s=time.perf_counter()-capture_started,
        cameras={camera: captures[camera][1] for camera in cameras})
    frames, images = {}, {}
    # Only the caller assembles/writes the scene, in station order. A failed
    # view cannot publish a partial snapshot or reuse a previously saved view.
    for camera in cameras:
        frame = captures[camera][0]
        Image.fromarray(frame['rgb']).save(path/(camera+'.png'))
        np.save(path/(camera+'.depth.npy'), frame['depth_m'])
        frames[camera] = plain({k:v for k,v in frame.items() if k not in ('rgb','depth_m')})
        images[camera] = str((path/(camera+'.png')).resolve())
    snapshot = dict(**context, frames=frames)
    (path/'snapshot.json').write_text(json.dumps(plain(snapshot), indent=2)+'\n')
    scene = dict(status='OBSERVED', snapshot=str(path.resolve()), images=images,
        context=dict(station=station, planning_state=state,
            measured_observation=context['observation'], camera_metadata=frames,
            camera_capture=capture_timing,
            snapshot_mode=station['execution_mode'],
            idle_unknown_gripper_geometry='assumed full-open in planning only'))
    serialized=json.dumps(plain(scene), indent=2)+'\n'
    (path/'scene.json').write_text(serialized)
    with (directory/record_name).open('x') as record:
        record.write(serialized)
    # Compatibility pointer is always the initial observation. Outcome capture
    # has its own immutable record and cannot overwrite the initial reference.
    if record_name=='initial_scene.json':
        with (directory/'scene.json').open('x') as record:
            record.write(serialized)
    return scene


def read_only_tools(namespace, scene):
    snapshot = Path(scene['snapshot'])
    metadata = json.loads((snapshot/'snapshot.json').read_text())
    def frame(camera='top'):
        if camera not in metadata['frames']:
            raise ValueError('Robo-house has top, left and right cameras only')
        result = copy.deepcopy(metadata['frames'][camera])
        result.update(rgb=np.asarray(Image.open(snapshot/(camera+'.png')).convert('RGB')),
            depth_m=np.load(snapshot/(camera+'.depth.npy')),
            K=np.asarray(result['K']), T_world_camera=np.asarray(result['T_world_camera']))
        return result
    return dict(get_camera_rgbd=frame, segment_camera_rgb=namespace['segment_camera_rgb'],
        get_station_info=lambda:copy.deepcopy(scene['context']['station']),
        get_planning_state=lambda:copy.deepcopy(scene['context']['planning_state']),
        plan_freespace_sequence=namespace['plan_freespace_sequence'],
        **{name:namespace[name] for name in ('sample_topdown_geometric','estimate_drop','birdseye_pose')
           if name in namespace})


def task_diagnostics(task):
    """Expose saved candidate/geometry evidence without embedding image rasters.

    A final native error alone hides an untried fallback. Keep actual strategy
    counts and proposal bounds so the coding loop can diagnose its own search.
    This is read-only evidence, not another planning or execution check.
    """
    def bounded(value):
        value = plain(value)
        if isinstance(value, dict):
            return {key: bounded(item) for key, item in list(value.items())[:40]
                    if key not in ('mask', 'rgb', 'depth_m')}
        if isinstance(value, list):
            if len(value) > 32:
                return dict(items=len(value), preview=[bounded(item) for item in value[:4]])
            return [bounded(item) for item in value]
        if isinstance(value, str):
            return value[:2000]
        return value
    proposals = task.get('initial_observation', {}).get('proposals', {})
    summary = dict(saved_artifact='task_definition.json',
        geometry=bounded(task.get('geometry', {})),
        aperture_evidence=bounded(task.get('aperture_evidence', {})),
        local_support_evidence=bounded(task.get('local_support_evidence', {})),
        staging_targets=bounded(task.get('staging_targets', [])),
        staging_geometry_error=bounded(task.get('staging_geometry_error')),
        target_identity=bounded(task.get('target_identity')),
        proposals={name: {key: bounded(value) for key, value in proposal.items()
                         if key in ('bbox_xywh', 'status', 'score', 'explanation', 'image_sha256')}
                   for name, proposal in proposals.items()})
    def choices(config):
        if not isinstance(config, dict):
            return dict(parameters=bounded(config))
        result = {key: config[key] for key in (
            'strategy', 'side', 'sign', 'short_axis_sign', 'pickup_tilt_deg',
            'yaw_deg', 'transport_tilt_deg', 'transit_tilt_deg', 'clearance_m',
            'pickup_tilt', 'tilt', 'yaw', 'transport_tilt', 'transit_tilt', 'clearance') if key in config}
        for key in ('first', 'second'):
            if isinstance(config.get(key), dict):
                result[key] = choices(config[key])
        return bounded(result)
    def trial_summary(trials):
        strategies, failures, rows = {}, {}, []
        for trial in trials:
            config = trial.get('configuration', {})
            strategy = config.get('strategy', 'unspecified') if isinstance(config, dict) else 'parameterized'
            strategies[strategy] = strategies.get(strategy, 0) + 1
            feedback = trial.get('native_feedback', {})
            stages = feedback.get('stages', [])
            failure = feedback.get('failing_stage')
            if failure:
                failures[failure] = failures.get(failure, 0) + 1
            failed = next((stage for stage in stages if stage.get('status') != 'Success'), {})
            rows.append(dict(strategy=strategy, configuration=choices(config),
                complete_task=trial.get('complete_task'), scope=trial.get('scope'),
                classification=trial.get('classification'),
                success=feedback.get('success'),
                failing_stage=failure, reason=bounded(feedback.get('reason')),
                successful_paths=sum(stage.get('status') == 'Success' for stage in stages),
                attempted_native_paths=len(stages),
                failed_arguments=bounded(failed.get('arguments', {})),
                native_diagnostic=bounded(failed.get('native_diagnostic')),
                geometry_error=bounded(trial.get('staging_geometry_error'))))
        return dict(candidate_count=len(trials), strategies_tried=strategies,
            native_failure_counts=failures, success_count=sum(row['success'] is True for row in rows),
            successful_candidates=[row for row in rows if row['success'] is True][:8],
            candidate_samples=rows if len(rows) <= 16 else rows[:8] + rows[-8:])
    summary.update(trial_summary(task.get('planning_trials', [])))
    summary['diagnostic_groups'] = {key: trial_summary(task[key])
        for key in ('terminal_pose_trials', 'source_prefix_trials') if key in task}
    return summary


def native_sequence_diagnostics(directory):
    """Bound model feedback from actual saved native sequence results."""
    root=Path(directory)
    rows=[];failures={};successes=0
    for path in sorted((root/'observations').glob('*_full_sequence_plan.json')):
        plan=json.loads(path.read_text())
        stages=plan.get('stages',[])
        failed=next((s for s in stages if s.get('status')!='Success'),{})
        failure=plan.get('failing_stage')
        if failure:failures[failure]=failures.get(failure,0)+1
        successes+=plan.get('success') is True
        rows.append(dict(artifact=str(path.resolve()),success=plan.get('success'),
            failing_stage=failure,reason=plan.get('reason'),
            successful_paths=sum(s.get('status')=='Success' for s in stages),
            attempted_paths=len(stages),failed_arguments=failed.get('arguments',{}),
            native_diagnostic=failed.get('native_diagnostic')))
    return dict(artifact_directory=str(root.resolve()),native_sequence_calls=len(rows),
        complete_plan_successes=successes,native_failure_counts=failures,
        samples=rows if len(rows)<=16 else rows[:8]+rows[-8:])
