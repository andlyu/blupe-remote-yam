"""Map ASPIRE's native YAM collision planner onto published encoder kinematics.

The upstream checkout and meshes remain separate. The generated station XML
uses the API midpoint world and POE axes, not a second IK implementation.
"""
import hashlib
import json
import os
from pathlib import Path
import xml.etree.ElementTree as ET
from copy import deepcopy

import numpy as np
from scipy.spatial.transform import Rotation

from .aspire_adapter import AspireNotReady, arm_pose, vector
from .camera_calibration import rigid

# The API model's grasp X/Y convention differs from upstream YAM. This changes
# orientation only; published grasp positions already include the finger reach.
API_TO_ASPIRE_GRASP = Rotation.from_euler('z', -90, degrees=True).as_matrix()


def _numbers(values):
    return ' '.join(format(float(v), '.17g') for v in values)


def native_finger_boxes(model, *, joints=None):
    """Read modeled finger boxes in the planner-facing grasp frame.

    This is geometry evidence, not a contact calibration or collision filter.
    Use a separate MjData and forward kinematics only; no simulation stepping.
    Both aperture endpoints expose the actual slide convention without treating
    the grasp site as the pad centroid. Native collision checks are unchanged.
    """
    import mujoco
    data = mujoco.MjData(model)
    if joints is not None:
        for side, values in joints.items():
            for j, value in enumerate(values):
                data.qpos[model.joint(f'{side}_joint{j+1}').qposadr[0]] = value
    result = dict(frame='aspire_grasp', units='metres',
        physical_geometry_measured=False,
        scope='Selected native finger box primitives only; native planner still checks every collision shape',
        arms={})
    for side in ('left', 'right'):
        rows = {}
        for aperture, label in ((0., 'closed'), (1., 'open')):
            from .aspire_gripper_model import set_jaws
            set_jaws(model, data, side, aperture)
            mujoco.mj_forward(model, data)
            site = model.site(side+'_grasp_site').id
            position = data.site_xpos[site].copy()
            rotation = data.site_xmat[site].reshape(3, 3).copy()
            for gid in range(model.ngeom):
                body = model.body(int(model.geom_bodyid[gid])).name
                if (body not in (side+'_lf_down', side+'_rf_down')
                        or model.geom_type[gid] != mujoco.mjtGeom.mjGEOM_BOX):
                    continue
                row = rows.setdefault(gid, dict(geom_id=gid, body=body,
                    half_sizes_m=model.geom_size[gid].tolist(),
                    rotation_grasp=(rotation.T@data.geom_xmat[gid].reshape(3, 3)).tolist()))
                row['center_'+label+'_grasp_m'] = (rotation.T@(data.geom_xpos[gid]-position)).tolist()
        result['arms'][side] = list(rows.values())
    return result


def prepare_native_model(report, upstream_xml, directory, *, gripper_geometry=None):
    """Export native collision geometry with API encoder-zero POE joints/sites.

    Verify all twelve axes, both grasp poses and encoder limits over a fixed
    pose set. Never add model joint offsets again: they are already in POE.
    """
    import mujoco
    upstream_xml, directory = Path(upstream_xml).resolve(), Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    tree = ET.parse(upstream_xml)
    root = tree.getroot()
    compiler = root.find('compiler')
    if compiler is None or compiler.get('angle') != 'radian':
        raise AspireNotReady('Expected pinned ASPIRE radian station XML')
    meshdir = (upstream_xml.parent/compiler.get('meshdir', '')).resolve()
    compiler.set('meshdir', str(meshdir))
    for asset in root.findall('./asset/texture'):
        if asset.get('file'):
            asset.set('file', str((upstream_xml.parent/asset.get('file')).resolve()))
    model = mujoco.MjModel.from_xml_path(str(upstream_xml))
    data = mujoco.MjData(model)
    data.qpos[:] = 0
    mujoco.mj_forward(model, data)
    spacing = float(report['base_geometry']['spacing_m'])
    left = model.body('left_arm').pos.copy()
    right = model.body('right_arm').pos.copy()
    if (not np.allclose(left[[0, 2]], right[[0, 2]], atol=1e-9)
            or not np.isclose(left[1]-right[1], spacing, atol=1e-9)
            or not np.isclose(left[1]+right[1], 0, atol=1e-9)):
        raise AspireNotReady('Native station arm bases do not match the reported parallel midpoint setup')
    shift = (left+right)/2
    world = root.find('worldbody')
    for element in list(world):
        if element.tag in ('body', 'geom', 'site', 'light', 'camera'):
            element.set('pos', _numbers(np.fromstring(element.get('pos', '0 0 0'), sep=' ')-shift))
    parents = {child: parent for parent in root.iter() for child in parent}
    zero_obs = {side+'_joints_deg': [0.]*6 for side in ('left', 'right')}
    for side in ('left', 'right'):
        base = np.array([0., spacing/2 if side == 'left' else -spacing/2, 0.])
        for joint in report['kinematics'][side]['joints']:
            name = joint['name']
            jid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name)
            element = root.find('.//joint[@name="'+name+'"]')
            if jid < 0 or element is None:
                raise AspireNotReady('Native model lacks published joint '+name)
            body = int(model.jnt_bodyid[jid])
            rotation = data.xmat[body].reshape(3, 3)
            body_pos = data.xpos[body]-shift
            axis = vector(joint['axis'], 3, name+' axis')
            if not np.isclose(np.linalg.norm(axis), 1, atol=1e-6):
                raise AspireNotReady('Published axis is not unit length')
            element.set('axis', _numbers(rotation.T@axis))
            element.set('pos', _numbers(rotation.T@(vector(joint['position_m'], 3, name+' anchor')+base-body_pos)))
            element.set('ref', '0')
        site = root.find('.//site[@name="'+side+'_grasp_site"]')
        body = model.body(parents[site].get('name')).id
        parent_rotation = data.xmat[body].reshape(3, 3)
        pose = arm_pose(report, zero_obs, side)
        site.set('pos', _numbers(parent_rotation.T@(pose[:3, 3]-(data.xpos[body]-shift))))
        quat = Rotation.from_matrix(parent_rotation.T@pose[:3, :3]@API_TO_ASPIRE_GRASP).as_quat()
        site.set('quat', _numbers(np.r_[quat[3], quat[:3]]))
    # The changed top-camera placement comes from the API report. It is not
    # used as an alternative to paired RGB-D extrinsics in perception.
    camera = root.find('.//body[@name="top_camera_d405"]')
    if camera is not None and 'top' in report.get('cameras', {}):
        top = report['cameras']['top']
        if 'T_base_camera' in top and 'left' in top['T_base_camera']:
            target = rigid(top['T_base_camera']['left']).copy()
            target[1, 3] += spacing/2
            parent_body = model.body(parents[camera].get('name')).id
            parent_rotation = data.xmat[parent_body].reshape(3, 3)
            camera.set('pos', _numbers(parent_rotation.T@(target[:3, 3]-(data.xpos[parent_body]-shift))))
            quat = Rotation.from_matrix(parent_rotation.T@target[:3, :3]).as_quat()
            camera.set('quat', _numbers(np.r_[quat[3], quat[:3]]))
    gripper_model = None
    if isinstance(gripper_geometry, dict) and gripper_geometry.get('source') == 'installed_i2rt_linear_4310':
        from .aspire_gripper_model import install_gripper
        gripper_model = install_gripper(root, gripper_geometry, API_TO_ASPIRE_GRASP)
    path = directory/'station.xml'
    tree.write(path, encoding='unicode')
    calibrated = mujoco.MjModel.from_xml_path(str(path))
    calibrated_data = mujoco.MjData(calibrated)
    samples = [np.zeros((2, 6)), np.array([[0., 1.3, 1.6, 0., 0., 0.]]*2),
        np.array([[-.3, 1.35, 1.6, -.8, .3, -.25], [.3, 1.35, 1.6, -.8, -.3, .25]])]
    generator = np.random.default_rng(41)
    for _ in range(12):
        samples.append(generator.uniform([-.5, .5, .5, -.5, -.5, -.5], [.5, 2., 2., .5, .5, .5], (2, 6)))
    errors = []
    for sample in samples:
        observation = {side+'_joints_deg': np.rad2deg(sample[i]).tolist() for i, side in enumerate(('left', 'right'))}
        for i, side in enumerate(('left', 'right')):
            for j in range(6):
                calibrated_data.qpos[calibrated.joint(f'{side}_joint{j+1}').qposadr[0]] = sample[i, j]
        mujoco.mj_forward(calibrated, calibrated_data)
        for side in ('left', 'right'):
            site_id = calibrated.site(side+'_grasp_site').id
            expected = arm_pose(report, observation, side)
            position_error = np.linalg.norm(calibrated_data.site_xpos[site_id]-expected[:3, 3])
            rotation_error = Rotation.from_matrix((expected[:3, :3]@API_TO_ASPIRE_GRASP).T@calibrated_data.site_xmat[site_id].reshape(3, 3)).magnitude()
            errors.append(dict(side=side, position_error_m=float(position_error), rotation_error_rad=float(rotation_error)))
    receipt = dict(status='MEASURED', calibration_id=report['calibration_id'],
        world_frame='blupe_base_midpoint', end_effector_frame='aspire_grasp',
        API_TO_ASPIRE_GRASP=API_TO_ASPIRE_GRASP.tolist(),
        encoder_units='radians', joint_offsets_applied_again=False,
        upstream_xml=str(upstream_xml), upstream_sha256=hashlib.sha256(upstream_xml.read_bytes()).hexdigest(),
        model_xml=str(path.resolve()), model_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        fk_checks=errors, physical_commands_sent=0,
        collision_geometry='installed I2RT linear_4310 gripper and upstream arms; static station shapes remain nominal' if gripper_model else 'selected upstream YAM geometry; static station shapes remain nominal',
        gripper_model=gripper_model,
        native_finger_boxes=native_finger_boxes(calibrated))
    if gripper_model:
        from .aspire_gripper_model import mesh_geometry
        receipt['native_gripper_meshes'] = mesh_geometry(calibrated)
    (directory/'model-validation.json').write_text(json.dumps(receipt, indent=2)+'\n')
    return receipt


class NativePlanningEnvironment:
    def __init__(self, env):
        self.env = env
        self.preview_only = True
        self.predicted_state = None
        self.rejected_prefixes = {}

    def get_observations(self, side):
        if self.preview_only:
            if self.predicted_state is not None:
                return deepcopy(self.predicted_state[side])
            return self.env.get_planning_observations(side)
        return self.env.get_observations(side)

    def move_bimanual_joint_keypoints(self, *args, **kwargs):
        if self.preview_only:
            raise AspireNotReady('A planner preview cannot submit a trajectory')
        return self.env.move_bimanual_joint_keypoints(*args, **kwargs)


def plan_native_sequence(planning_env, native, move, segments):
    """Preview full native paths, seeding each from its predicted predecessor.

    Predicted state is confined to this preview environment. Live feedback is
    never overwritten, and this routine never dispatches a trajectory.
    """
    if planning_env.predicted_state is not None:
        raise ValueError('Nested sequence previews are unsupported')
    planning_env.preview_only = True
    state = {side: planning_env.get_observations(side) for side in ('left', 'right')}
    def snapshot():
        return {side: {key: np.asarray(value).tolist() for key, value in observation.items()}
            for side, observation in state.items()}
    report = dict(success=False, status='PLANNING', physical_commands_sent=0,
        state_provenance='Measured/recorded start followed by native predicted endpoints',
        initial_state=snapshot(), stages=[],
        limitation='Native arm/jaw and station collision geometry; held block is not attached as a collision body')
    # Exact measured seeds and exact prefixes only. Never cache an accepted
    # path or infer mathematical unreachability from solver non-convergence.
    prefixes = getattr(planning_env, 'rejected_prefixes', {})
    def prefix_key(index):
        return json.dumps(dict(initial_state=report['initial_state'],
            stage_start_state=snapshot(),
            segments=segments[:index+1]),sort_keys=True,default=lambda v:np.asarray(v).tolist())
    try:
        for index, segment in enumerate(segments):
            stage = dict(stage=segment['stage'], arguments=dict(segment['arguments']))
            # Opening, closing and releasing are modeled as separate jaw changes
            # at the preceding arm endpoint, matching the task's gripper calls.
            for side, grip in segment.get('gripper_state', {}).items():
                if side not in state or not np.isfinite(grip) or not 0 <= grip <= 1:
                    raise ValueError('Invalid predicted gripper state')
                state[side]['gripper_pos'] = np.array([float(grip)])
            stage['start_state'] = snapshot()
            key = prefix_key(index)
            prior = prefixes.get(key)
            if prior and prior['count'] >= 2:
                cached = deepcopy(prior['report'])
                cached['repeated_rejection'] = dict(native_calls=prior['count'],
                    reason='Same native IK inputs and measured seed already rejected twice in this harness')
                return cached
            planning_env.predicted_state = state
            result = move(**dict(stage['arguments'], preview_only=True))
            stage.update(status=result.status, reason=result.reason,
                final_pos_error_m=result.final_pos_error_m,
                final_rot_error_deg=result.final_rot_error_deg,
                trajectory_steps=result.trajectory_steps,
                trajectory_cache_key=result.trajectory_cache_key, executed=result.executed)
            if getattr(result, 'native_diagnostic', None):
                stage['native_diagnostic'] = result.native_diagnostic
            report['stages'].append(stage)
            if result.status != 'Success':
                report.update(status='FAILED', failing_stage=segment['stage'], reason=result.reason)
                if result.status == 'IK_Failed':
                    previous = prefixes.get(key, {})
                    prefixes[key] = dict(count=previous.get('count',0)+1, report=deepcopy(report))
                return report
            # Native already-at-target success intentionally has no cached path.
            if (result.trajectory_steps == 0 and result.trajectory_cache_key is None
                    and result.executed is False):
                stage.update(native_noop=True, predicted_endpoint=snapshot())
                continue
            entry = native._get_cached_trajectory(result.trajectory_cache_key)
            if entry is None:
                raise AspireNotReady('Native successful nonempty trajectory is missing its cache entry')
            joints = {side: np.asarray(entry[side+'_positions'][-1]) for side in ('left', 'right')}
            planner = native._get_planner(planner_backend=stage['arguments'].get('planner_backend', 'rrtconnect'))
            lp, lq, rp, rq = planner._kin.forward_kinematics(joints['left'], joints['right'])
            for side, pos, quat in [('left', lp, lq), ('right', rp, rq)]:
                state[side].update(joint_pos=joints[side], ee_pos=np.asarray(pos), ee_quat=np.asarray(quat))
                grips = entry.get(side+'_gripper_positions')
                if grips is not None:
                    state[side]['gripper_pos'] = np.array([float(np.asarray(grips)[-1])])
            stage['predicted_endpoint'] = snapshot()
        report.update(success=True, status='FULL_PLAN_READY')
        return report
    finally:
        planning_env.predicted_state = None
        planning_env.preview_only = True


def orient_native_rrt_path(path, start, goal):
    """Repair the pinned RRT's recognized swapped-tree join.

    That branch returns connection→start, goal→connection. Reverse each half
    to get start→connection→goal. Other paths retain native behavior.
    """
    path = [np.asarray(q, dtype=float).copy() for q in path]
    if not path or any(q.shape!=np.asarray(start).shape or not np.isfinite(q).all() for q in path):
        raise AspireNotReady('Native RRT path is empty or contains invalid joint configurations')
    same = lambda a, b: np.allclose(a, b, atol=1e-6, rtol=0.)
    if same(path[0], start) and same(path[-1], goal):
        return path, False
    joins = [i for i in range(len(path)-1) if same(path[i], start) and same(path[i+1], goal)]
    if len(joins)!=1 or not same(path[0], path[-1]):
        return path, False
    index = joins[0]
    fixed = list(reversed(path[:index+1]))+list(reversed(path[index+1:]))
    return fixed, True


def bind_native_planner(env, *, repair_rrt_path=True):
    from robot.models.station.paths import get_station_xml
    receipt = prepare_native_model(env.calibration(), get_station_xml(), env.output_dir/'planner',
        gripper_geometry=getattr(env, 'selected_gripper_geometry', None))
    os.environ['YAM_STATION_CALIBRATED_XML'] = receipt['model_xml']
    from cap.agent.tools.freespace_move import FreespaceMoveTool
    planning_env = NativePlanningEnvironment(env)
    native = FreespaceMoveTool(env=planning_env)
    env.native_planner_tool = native
    env.planner_validation = receipt
    get_planner = native._get_planner
    def checked_planner(*args, **kwargs):
        planner = get_planner(*args, **kwargs)
        if not getattr(planner, '_blupe_rrt_checked', False):
            if receipt.get('gripper_model'):
                from .aspire_gripper_model import adapt_native_planner_grippers
                adapt_native_planner_grippers(planner)
            original_ik = planner._kin.inverse_kinematics
            def observed_ik(left_pos, left_quat, right_pos, right_quat, **options):
                left, right = original_ik(left_pos,left_quat,right_pos,right_quat,**options)
                try:
                    lp,lq,rp,rq = planner._kin.forward_kinematics(left,right)
                    native._blupe_ik_samples.append(dict(
                        left_target_pos=np.asarray(left_pos).tolist(),right_target_pos=np.asarray(right_pos).tolist(),
                        left_position_error_m=float(np.linalg.norm(lp-left_pos)),
                        right_position_error_m=float(np.linalg.norm(rp-right_pos)),
                        left_rotation_error_deg=float(np.rad2deg((Rotation.from_quat(left_quat).inv()*Rotation.from_quat(lq)).magnitude())),
                        right_rotation_error_deg=float(np.rad2deg((Rotation.from_quat(right_quat).inv()*Rotation.from_quat(rq)).magnitude())),
                        left_joints_rad=np.asarray(left).tolist(),right_joints_rad=np.asarray(right).tolist()))
                except Exception:
                    pass  # A diagnostic cannot change the native solver result.
                return left,right
            planner._kin.inverse_kinematics = observed_ik
            original_rrt = planner.rrt_connect
            def checked_rrt(start, goal, side, **options):
                path = original_rrt(start, goal, side, **options)
                if path is None:
                    # Surface the native planner's existing configuration
                    # diagnosis. This does not change validation or paths.
                    try:
                        lo, hi = planner._get_limits(side)
                        refs = (options['ref_left'], options['ref_right'])
                        native._blupe_failure_detail = dict(side=side,
                            start=planner._diagnose_config(np.clip(start,lo,hi),side,*refs,'Start'),
                            goal=planner._diagnose_config(np.clip(goal,lo,hi),side,*refs,'Goal'),
                            source='Pinned ASPIRE YamMotionPlanner._diagnose_config')
                        env._save_json('native_rrt_failure', native._blupe_failure_detail)
                    except Exception as exc:
                        env._save_json('native_diagnostic_unavailable', dict(error=type(exc).__name__))
                    return None
                lo, hi = planner._get_limits(side)
                expected_start, expected_goal = np.clip(start, lo, hi), np.clip(goal, lo, hi)
                raw = [np.asarray(q).copy() for q in path]
                if repair_rrt_path:
                    path, repaired = orient_native_rrt_path(raw, expected_start, expected_goal)
                else:
                    repaired = False
                env._save_json('native_rrt_path', dict(repair_enabled=repair_rrt_path,
                    repaired_swapped_tree_join=repaired, side=side,
                    start=expected_start.tolist(), goal=expected_goal.tolist(),
                    raw_path=[q.tolist() for q in raw], path=[q.tolist() for q in path]))
                return path
            planner.rrt_connect = checked_rrt
            planner._blupe_rrt_checked = True
        return planner
    native._get_planner = checked_planner

    def freespace_move(**arguments):
        preview = arguments.get('preview_only', False)
        if not preview and not getattr(env, 'physical_motion_enabled', False):
            raise AspireNotReady('Native execution requires the leased API bridge')
        planning_env.preview_only = bool(preview)
        # Preserve native direct execution, explicit preview and cache semantics.
        # Endpoint residuals remain native diagnostics, not an adapter cutoff.
        arguments.setdefault('planner_backend', 'rrtconnect')
        native._blupe_failure_detail = None
        native._blupe_ik_samples = []
        result = native.execute(**arguments)
        if not result.success and native._blupe_ik_samples:
            samples=native._blupe_ik_samples
            best=min(samples,key=lambda s:max(s['left_position_error_m'],s['right_position_error_m']))
            native._blupe_failure_detail=dict(native._blupe_failure_detail or {},
                backend='native_rrtconnect_mink',ik_samples=len(samples),best_ik_sample=best,
                evidence='Actual native solver outputs/FK; diagnostics add no acceptance cutoff')
            env._save_json('native_failed_ik_samples',dict(samples=samples,diagnosis=native._blupe_failure_detail))
        if result.data is not None and native._blupe_failure_detail:
            result.data.native_diagnostic = native._blupe_failure_detail
        env._save_json('native_plan_result', dict(success=result.success, error=result.error,
            status=getattr(result.data, 'status', None), preview_only=bool(preview)))
        for key, entry in native._trajectory_cache.items():
            # Save actual native complete paired paths, not just a success label.
            def plain(value):
                if isinstance(value, np.ndarray): return value.tolist()
                if isinstance(value, np.generic): return value.item()
                if isinstance(value, dict): return {k: plain(v) for k, v in value.items()}
                if isinstance(value, (tuple, list)): return [plain(v) for v in value]
                return value
            (env.output_dir/'planner'/('trajectory-'+key+'.json')).write_text(json.dumps(plain(entry), indent=2, allow_nan=False)+'\n')
        return result.data
    def plan_sequence(segments):
        result = plan_native_sequence(planning_env, native, freespace_move, segments)
        env._save_json('full_sequence_plan', result)
        return result
    env.plan_freespace_sequence = plan_sequence
    return freespace_move
