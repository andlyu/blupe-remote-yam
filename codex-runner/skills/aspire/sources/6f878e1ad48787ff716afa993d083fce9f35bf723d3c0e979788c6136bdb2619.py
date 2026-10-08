import numpy as np
from scipy.spatial.transform import Rotation
from scene_geometry import (TaskBlocked, world_points, mask_points, fit_support,
    measure_block, grasp_pose, rotation_from_rpy, rpy_from_rotation,
    place_on_support, plane_z)


def queries():
    return {
        'green': 'The solid green rectangular cuboid block; exclude the green pen and towel. Segment all visible block surfaces.',
        'green_top': 'Only the upward-facing flat top surface of the solid green rectangular cuboid block, excluding side faces, pen and towel.',
        'red': 'The red rectangular block on the white table; exclude the round red token and pink figure. Segment all visible block surfaces.',
        'red_top': 'Only the upward-facing flat top surface of the red rectangular block, excluding its side faces and any object covering it.',
        'towel': 'The exposed green towel fabric surface, excluding every object resting on it.',
        'table': 'The exposed white tabletop surface, excluding objects, towel, robot parts, writing, and background.'}


def plain(x):
    if isinstance(x, np.ndarray):
        return x.tolist()
    if isinstance(x, np.generic):
        return x.item()
    if isinstance(x, dict):
        return {k: plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [plain(v) for v in x]
    return x


def observe(tools, names):
    frame = tools['get_camera_rgbd']('top')
    shape = np.asarray(frame['depth_m']).shape
    if np.asarray(frame['rgb']).shape[:2] != shape:
        raise TaskBlocked('Paired RGB/depth raster mismatch')
    coeffs = frame.get('metadata', {}).get('factory_color_intrinsics', {}).get('coeffs', [])
    if np.any(np.asarray(coeffs, dtype=float) != 0):
        raise TaskBlocked('Nonzero top distortion needs distortion-aware deprojection')
    proposals = {}
    for name in names:
        proposal = tools['segment_camera_rgb'](frame['rgb'], queries()[name])
        mask = np.asarray(proposal['mask'])
        if mask.shape != shape or mask.dtype != np.bool_:
            raise TaskBlocked('Malformed exact mask: ' + name)
        proposals[name] = proposal
    return world_points(frame), proposals, {
        'metadata': frame.get('metadata', {}), 'K': frame['K'],
        'T_world_camera': frame['T_world_camera'], 'proposals': proposals}


def measure(points, proposal, support):
    block = measure_block(points, proposal['mask'], support)
    size = np.r_[block['dimensions'], block['height_m']]
    if not np.isfinite(size).all() or np.any(size <= 0):
        raise TaskBlocked('Invalid measured box dimensions')
    return block


def move(stage, side, pos, rotation):
    return {'stage': stage, 'kind': 'move', 'arguments': {
        side + '_target_pos': np.asarray(pos).tolist(),
        side + '_target_rpy': rpy_from_rotation(rotation)}}


def segments(steps, state):
    jaws = {}
    for side in ('left', 'right'):
        value = np.asarray(state['arms'][side]['gripper_pos'], dtype=float).reshape(-1)
        if value.size != 1 or not np.isfinite(value[0]):
            raise TaskBlocked('Invalid planning jaw state')
        jaws[side] = float(value[0])
    result = []
    for step in steps:
        if step['kind'] in ('open', 'close'):
            jaws[step['side']] = 1.0 if step['kind'] == 'open' else 0.0
        else:
            result.append({'stage': step['stage'], 'arguments': step['arguments'],
                           'gripper_state': dict(jaws)})
    return result


def build_task(tools):
    station = tools['get_station_info']()
    state = tools['get_planning_state']()
    points, masks, observation = observe(tools, ['green', 'red', 'red_top', 'towel', 'table'])
    table = fit_support(mask_points(points, masks['table']['mask']))
    towel = fit_support(mask_points(points, masks['towel']['mask']))
    green = measure(points, masks['green'], towel)
    red = measure(points, masks['red'], table)
    support = fit_support(mask_points(points, masks['red_top']['mask']))
    geometry = station['gripper_geometry']
    _, base_rpy = grasp_pose(green, geometry)
    base = rotation_from_rpy(base_rpy)
    closing_index = 0 if geometry.get('closing_axis', 'grasp_x') == 'grasp_x' else 1
    legacy = 'tcp_to_contact_center_m' in geometry and 'grasp_to_planner_offset_m' not in geometry
    offset = np.asarray(geometry.get('tcp_to_contact_center_m') if legacy else
        geometry.get('grasp_to_planner_offset_m', [0., 0., 0.]), dtype=float)
    if offset.shape != (3,) or not np.isfinite(offset).all():
        raise TaskBlocked('Malformed grasp offset')
    offset = offset * (-1 if legacy else 1)

    # Native diagnosis identifies finger/table overlap at the previous goal,
    # not an obstructed approach. Move the grasp site up within the measured
    # block and test nearby oblique approaches without altering the table model.
    pickup_options = [
        ('right', 45., 0.02), ('right', 55., 0.02),
        ('right', 60., 0.05), ('left', 45., 0.02),
        ('left', -45., 0.02), ('right', -45., 0.02),
        ('left', 60., 0.05), ('left', -60., 0.05)]
    trials, best, best_progress = [], None, -1
    found = False
    for side, tilt, depth_fraction in pickup_options:
        rotation = Rotation.from_rotvec(base[:, closing_index] * np.deg2rad(tilt)).as_matrix() @ base
        contact = np.asarray(green['top_center']).copy()
        contact[2] -= depth_fraction * green['height_m']
        grasp = contact + rotation @ offset
        approach = grasp - 0.04 * rotation[:, 2]
        for yaw in (0., 180., -90., 90., -45., 45.):
            delta = Rotation.from_euler('z', yaw, degrees=True).as_matrix()
            final_rotation = delta @ rotation
            place = place_on_support(green, grasp, rpy_from_rotation(rotation),
                red['top_center'][:2], support, rpy_from_rotation(final_rotation), clearance_m=0.002)
            high = max(float(approach[2]), float(place[2] + 0.04),
                       float(red['top_center'][2] + green['height_m'] + 0.04))
            steps = [
                {'stage': 'open_before_pickup', 'kind': 'open', 'side': side},
                move('source_approach', side, approach, rotation),
                move('source_grasp', side, grasp, rotation),
                {'stage': 'close_on_green', 'kind': 'close', 'side': side},
                move('vertical_lift', side, np.r_[grasp[:2], high], rotation),
                move('carry_above_red', side, np.r_[place[:2], high], final_rotation),
                move('place_upright_on_red', side, place, final_rotation),
                {'stage': 'release_green', 'kind': 'open', 'side': side},
                move('retreat_after_release', side, place - 0.06 * final_rotation[:, 2], final_rotation)]
            feedback = tools['plan_freespace_sequence'](segments(steps, state))
            pose = {'side': side, 'tilt_deg': tilt, 'depth_fraction': depth_fraction,
                    'object_yaw_deg': yaw, 'contact': contact, 'grasp': grasp,
                    'place': place, 'pickup_rpy': rpy_from_rotation(rotation),
                    'place_rpy': rpy_from_rotation(final_rotation), 'object_rotation': delta}
            trials.append({'configuration': pose, 'native_feedback': feedback})
            progress = sum(s.get('status') == 'Success' for s in feedback.get('stages', []))
            if best is None or progress > best_progress or feedback.get('success') is True:
                best = (steps, pose, feedback)
                best_progress = progress
            if feedback.get('success') is True:
                found = True
                break
            failed_stage = feedback.get('failing_stage')
            if failed_stage is None:
                failed = [s for s in feedback.get('stages', []) if s.get('status') != 'Success']
                failed_stage = failed[0].get('stage') if failed else None
            # Destination yaw cannot repair a source failure; change pickup.
            if failed_stage in ('source_approach', 'source_grasp', 'vertical_lift'):
                break
        if found:
            break
    steps, pose, feedback = best
    return plain({'steps': steps, 'task': 'Stack the green block on the red block',
        'side': pose['side'], 'station': station, 'planning_start': state,
        'initial_observation': observation, 'planning_trials': trials,
        'candidate_preview_success': feedback.get('success') is True,
        'geometry': {'green': green, 'red': red, 'table_plane': table,
                     'towel_plane': towel, 'red_top_plane': support, **pose},
        'reasoning': [
            'Native source-grasp diagnosis reported play_table collisions with right_lf_down and right_rf_down, approximately 1.8 to 2.1 mm overlap. No physical commands occurred.',
            'Reduce grasp depth from 15 percent to 2 percent of measured block height, then test nearby tilt and arm alternatives. This changes the grasp, not calibration or collision validation.',
            'Each tested candidate includes all six paths and commanded jaw states; destination yaw is varied only after source feasibility.',
            'World-yaw-only object rotation preserves upright placement despite an oblique grasp. Placement uses rotated measured corners and measured red support.',
            'Runtime must freshly plan all six paths before opening and owns native cache execution, motion enablement, recording and shutdown.',
            'One physical attempt only. No automatic replay or recovery. The held object is not attached as a native collision body.',
            'Native planning success is FULL_PLAN_ONLY evidence; physical stacking requires post-release assessment.'
        ]})


def evaluate(tools, task):
    evidence = {'outcome': 'UNVERIFIED', 'operator_observation': None}
    try:
        points, masks, observation = observe(tools, ['green', 'green_top', 'red', 'red_top'])
        evidence['final_observation'] = plain(observation)
        red_cloud = mask_points(points, masks['red']['mask'])
        red_top = mask_points(points, masks['red_top']['mask'])
        green_top = mask_points(points, masks['green_top']['mask'])
        if min(len(red_cloud), len(red_top), len(green_top)) < 3:
            evidence['reason'] = 'Insufficient visible support or top depth; stacking is unverified.'
            return {'success': False, 'evidence': evidence}
        support = fit_support(red_top)
        top_plane = fit_support(green_top)
        green = measure(points, masks['green'], support)
        old = task['geometry']
        red_center = np.asarray(old['red']['top_center'])
        red_basis = np.asarray(old['red']['basis'])
        red_size = np.asarray(old['red']['dimensions'])
        center = np.asarray(green['top_center'])
        uv = (center[:2] - red_center[:2]) @ red_basis
        gap = float(plane_z(top_plane, center[:2]) - old['green']['height_m'] - plane_z(support, center[:2]))
        red_uv = (red_cloud[:, :2] - red_center[:2]) @ red_basis
        checks = {
            'center_supported': bool(np.all(np.abs(uv) < red_size / 2)),
            'contact_height_consistent': bool(abs(gap) <= 0.008),
            'upright_top': bool(np.linalg.norm(top_plane[:2]) < np.tan(np.deg2rad(12))),
            'footprint_consistent': bool(np.allclose(np.sort(green['dimensions']),
                np.sort(old['green']['dimensions']), rtol=0.25, atol=0.006)),
            'red_at_original_target': bool(np.mean(np.all(np.abs(red_uv) <= red_size / 2 + 0.008, axis=1)) > 0.8),
            'red_support_height_consistent': bool(abs(float(plane_z(support, red_center[:2]) - red_center[2])) <= 0.008)}
        success = bool(all(checks.values()))
        evidence.update(plain({'outcome': 'PHYSICAL_SUCCESS' if success else 'UNVERIFIED',
            'checks': checks, 'green_final': green, 'red_top_plane': support,
            'green_top_plane': top_plane, 'center_offset_m': uv,
            'inferred_bottom_gap_m': gap,
            'limitations': 'Contact is inferred from initial measured height and post-retreat depth. Calibration and model contours remain provisional. Sparse observations do not prove long-term stability. These evaluation tolerances do not gate execution.'}))
        return {'success': success, 'evidence': evidence}
    except (TaskBlocked, ValueError, KeyError, np.linalg.LinAlgError) as exc:
        evidence['reason'] = str(exc)
        return {'success': False, 'evidence': evidence}
