import numpy as np
from scipy.spatial.transform import Rotation
from scene_geometry import (TaskBlocked, world_points, mask_points, fit_support,
    measure_block, grasp_pose, rotation_from_rpy, rpy_from_rotation,
    place_on_support, plane_z)


def queries():
    return {
        'green_top': 'Only the upward-facing flat top surface of the solid green rectangular cuboid block, excluding side faces, pen and towel.',
        'red': 'The red rectangular block on the white table; exclude the round red token and pink figure. Segment all visible block surfaces.',
        'red_top': 'Only the upward-facing flat top surface of the red rectangular block, excluding its side faces and any object covering it.',
        'source_support': 'Exposed supporting surface immediately surrounding the bottom of the green rectangular block: towel fabric if it rests on the towel, otherwise white tabletop. Exclude the block, other objects and robot parts.',
        'table': 'The exposed white tabletop surface, excluding objects, towel, robot parts, writing, and background.'}


def plain(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [plain(v) for v in value]
    return value


def observe(tools, names):
    frame = tools['get_camera_rgbd']('top')
    shape = np.asarray(frame['depth_m']).shape
    if np.asarray(frame['rgb']).shape[:2] != shape:
        raise TaskBlocked('Paired top raster mismatch')
    coefficients = np.asarray(frame.get('metadata', {}).get(
        'factory_color_intrinsics', {}).get('coeffs', [0.] * 5), dtype=float)
    if not np.isfinite(coefficients).all() or np.any(coefficients != 0):
        raise TaskBlocked('Top deprojection requires the reported zero-distortion capture')
    points = world_points(frame)
    proposals = {}
    for name in names:
        proposal = tools['segment_camera_rgb'](frame['rgb'], queries()[name])
        mask = np.asarray(proposal['mask'])
        if mask.shape != shape or mask.dtype != np.bool_:
            raise TaskBlocked('Malformed exact mask: ' + name)
        proposals[name] = proposal
    return points, proposals, {'camera': 'top', 'metadata': frame.get('metadata', {}),
        'K': frame['K'], 'T_world_camera': frame['T_world_camera'], 'proposals': proposals}


def plane(points, proposals, name):
    cloud = mask_points(points, proposals[name]['mask'])
    try:
        return fit_support(cloud)
    except (TaskBlocked, ValueError, np.linalg.LinAlgError) as exc:
        raise TaskBlocked('%s: %d finite samples; %s' % (name, len(cloud), exc)) from exc


def box(points, proposals, name, support):
    result = measure_block(points, proposals[name]['mask'], support)
    size = np.r_[result['dimensions'], result['height_m']]
    if not np.isfinite(size).all() or np.any(size <= 0):
        raise TaskBlocked('Invalid measured box: ' + name)
    return result


def move(stage, side, position, rotation):
    return {'stage': stage, 'kind': 'move', 'arguments': {
        side + '_target_pos': np.asarray(position).tolist(),
        side + '_target_rpy': rpy_from_rotation(rotation)}}


def segments(steps, state):
    jaws = {}
    for side in ('left', 'right'):
        value = np.asarray(state['arms'][side]['gripper_pos'], dtype=float).reshape(-1)
        if value.size != 1 or not np.isfinite(value[0]):
            raise TaskBlocked('Malformed planning jaw state')
        jaws[side] = float(value[0])
    result = []
    for step in steps:
        if step['kind'] in ('open', 'close'):
            jaws[step['side']] = 1. if step['kind'] == 'open' else 0.
        else:
            result.append({'stage': step['stage'], 'arguments': step['arguments'],
                           'gripper_state': dict(jaws)})
    return result


def flat_rotations(block):
    sizes = np.r_[block['dimensions'], block['height_m']]
    basis = np.eye(3)
    basis[:2, :2] = block['basis']
    index = int(np.argmin(sizes))
    normal = basis[:, index]
    variants = []
    for sign in (1., -1.):
        axis = np.cross(normal, [0., 0., sign])
        length = np.linalg.norm(axis)
        dot = float(normal[2] * sign)
        if length > 1e-10:
            delta = Rotation.from_rotvec(axis / length * np.arctan2(length, dot)).as_matrix()
        elif dot > 0:
            delta = np.eye(3)
        else:
            delta = Rotation.from_rotvec(basis[:, (index + 1) % 3] * np.pi).as_matrix()
        variants.append((sign, delta))
    return variants, float(sizes[index]), np.sort(np.delete(sizes, index))


def failure(feedback):
    if feedback.get('failing_stage') is not None:
        return feedback['failing_stage']
    for stage in feedback.get('stages', []):
        if stage.get('status') != 'Success':
            return stage.get('stage')
    return None


def build_task(tools):
    station = tools['get_station_info']()
    state = tools['get_planning_state']()
    points, proposals, observation = observe(tools, list(queries()))
    source_support = plane(points, proposals, 'source_support')
    table = plane(points, proposals, 'table')
    support = plane(points, proposals, 'red_top')
    green = box(points, proposals, 'green_top', source_support)
    red = box(points, proposals, 'red_top', table)
    geometry = station['gripper_geometry']
    _, base_rpy = grasp_pose(green, geometry)
    base = rotation_from_rpy(base_rpy)
    closing_index = 0 if geometry.get('closing_axis', 'grasp_x') == 'grasp_x' else 1
    offset = np.asarray(geometry.get('grasp_to_planner_offset_m', [0., 0., 0.]), dtype=float)
    if offset.shape != (3,) or not np.isfinite(offset).all():
        raise TaskBlocked('Invalid grasp-site offset')
    flats, final_height, final_dimensions = flat_rotations(green)
    center = np.asarray(green['top_center'])
    sides = sorted(('left', 'right'), key=lambda side: float(np.linalg.norm(
        np.asarray(state['arms'][side]['ee_pos']) - center)))
    candidates = []
    for heading in (0., 180., 90., -90.):
        heading_base = base @ Rotation.from_euler('z', heading, degrees=True).as_matrix()
        for tilt in (0., 30., -30., 45., -45., 60., -60.):
            pickup = Rotation.from_rotvec(heading_base[:, closing_index] * np.deg2rad(tilt)).as_matrix() @ heading_base
            for sign, flat in flats:
                for yaw in (0., 180., 90., -90., 45., -45.):
                    delta = Rotation.from_euler('z', yaw, degrees=True).as_matrix() @ flat
                    final = delta @ pickup
                    rank = (4. * abs(float(final[2, closing_index])) + float(final[2, 2])
                            + .25 * abs(tilt) / 60. + .05 * (heading in (90., -90.)))
                    for side in sides:
                        candidates.append((rank, side, heading, tilt, sign, yaw, pickup, final, delta))
    candidates.sort(key=lambda item: item[0])
    trials, source_failures = [], set()
    best, best_progress, found = None, -1, False
    for _, side, heading, tilt, sign, yaw, pickup, final, delta in candidates:
        for depth_fraction in (.40, .25):
            source_key = (side, heading, tilt, depth_fraction)
            if source_key in source_failures:
                continue
            contact = center.copy()
            contact[2] -= depth_fraction * green['height_m']
            grasp = contact + pickup @ offset
            place = place_on_support(green, grasp, rpy_from_rotation(pickup),
                red['top_center'][:2], support, rpy_from_rotation(final), clearance_m=.002)
            approach = grasp - .05 * pickup[:, 2]
            radius = float(np.linalg.norm(np.r_[green['dimensions'], green['height_m']]))
            high = max(float(grasp[2] + .06), float(place[2] + .06),
                       float(red['top_center'][2] + radius + .04))
            lift, carry = np.r_[grasp[:2], high], np.r_[place[:2], high]
            prefix = [
                {'stage': 'open_before_pickup', 'kind': 'open', 'side': side},
                move('source_approach', side, approach, pickup),
                move('source_grasp', side, grasp, pickup),
                {'stage': 'close_on_green', 'kind': 'close', 'side': side},
                move('vertical_lift', side, lift, pickup),
                move('carry_above_red', side, carry, final),
                move('place_flat_on_red', side, place, final),
                {'stage': 'release_green', 'kind': 'open', 'side': side}]
            # Latest failure is retreat GOAL IK, with joint 4 at -1.5708.
            # Keep pickup, support height, release opening and first five goals.
            # Replace the 8 cm oblique retreat with nearby upward goals before
            # changing the grasp. Every alternative is a full six-path plan.
            retreats = [
                ('vertical_4cm', place + [0., 0., .04]),
                ('vertical_6cm', place + [0., 0., .06]),
                ('reverse_4cm', place - .04 * final[:, 2]),
                ('up_and_reverse', place + [0., 0., .04] - .02 * final[:, 2]),
                ('return_to_carry', carry)]
            for retreat_name, retreat in retreats:
                steps = prefix + [move('retreat_after_release', side, retreat, final)]
                feedback = tools['plan_freespace_sequence'](segments(steps, state))
                pose = {'side': side, 'grasp_heading_deg': heading, 'pickup_tilt_deg': tilt,
                    'flat_face_sign': sign, 'yaw_deg': yaw, 'depth_fraction': depth_fraction,
                    'grasp': grasp, 'place': place, 'carry': carry, 'retreat': retreat,
                    'retreat_variant': retreat_name, 'pickup_rotation': pickup,
                    'final_rotation': final, 'object_rotation': delta,
                    'final_opening_axis': final[:, closing_index]}
                trials.append({'configuration': pose, 'native_feedback': feedback})
                progress = sum(s.get('status') == 'Success' for s in feedback.get('stages', []))
                if best is None or progress > best_progress or feedback.get('success') is True:
                    best, best_progress = (steps, pose, feedback), progress
                if feedback.get('success') is True:
                    found = True
                    break
                failed = failure(feedback)
                if failed in ('source_approach', 'source_grasp', 'vertical_lift'):
                    source_failures.add(source_key)
                if failed != 'retreat_after_release' or len(trials) >= 96:
                    break
            if found or len(trials) >= 96:
                break
        if found or len(trials) >= 96:
            break
    if best is None:
        raise TaskBlocked('No complete candidate constructed')
    steps, pose, feedback = best
    return plain({'steps': steps, 'task': 'Stack green flat on red',
        'station': station, 'planning_start': state, 'initial_observation': observation,
        'planning_trials': trials, 'candidate_preview_success': feedback.get('success') is True,
        'geometry': {'green': green, 'red': red, 'source_support': source_support,
            'table_plane': table, 'red_top_plane': support,
            'expected_final_height': final_height, 'expected_final_dimensions': final_dimensions,
            **pose},
        'reasoning': [
            'Latest attempt sent zero physical commands. First five paths passed; the 8 cm oblique retreat failed goal IK. Native diagnostics show right joint 4 at -1.5708 with unresolved pose error.',
            'Try upward or shorter reverse-axis retreat goals with unchanged release pose and full-open jaws. Unlike the older collision diagnosis, the latest feedback does not establish an invalid release start.',
            'Retain centered 25-40 percent depth pickup. The earlier shallow pinch produced a physically failed stack and is not a fallback.',
            'Measure current source, destination and supports. Compute flat placement from rotated measured corners; do not change calibration, model geometry, target location or native validation.',
            'All six paths are planned with successive predicted endpoints and commanded jaw states. Runtime freshly plans before opening and owns cache execution, physical enablement, evidence and shutdown.',
            'One physical attempt only; no automatic recovery or replay. Native planning does not model held-object contact and does not prove stacking success.'
        ]})


def evaluate(tools, task):
    evidence = {'outcome': 'UNVERIFIED', 'operator_observation': None}
    try:
        points, proposals, observation = observe(tools, ['green_top', 'red', 'red_top'])
        evidence['final_observation'] = plain(observation)
        support = plane(points, proposals, 'red_top')
        green_plane = plane(points, proposals, 'green_top')
        green = box(points, proposals, 'green_top', support)
        red_cloud = mask_points(points, proposals['red']['mask'])
        if len(red_cloud) < 3:
            raise TaskBlocked('Insufficient visible red depth')
        old = task['geometry']
        red_center = np.asarray(old['red']['top_center'])
        red_basis = np.asarray(old['red']['basis'])
        red_size = np.asarray(old['red']['dimensions'])
        center = np.asarray(green['top_center'])
        uv = (center[:2] - red_center[:2]) @ red_basis
        red_uv = (red_cloud[:, :2] - red_center[:2]) @ red_basis
        gap = float(plane_z(green_plane, center[:2]) - old['expected_final_height']
                    - plane_z(support, center[:2]))
        checks = {
            'center_supported': bool(np.all(np.abs(uv) < red_size / 2)),
            'centered': bool(np.all(np.abs(uv) < red_size * .25)),
            'contact_height_consistent': bool(abs(gap) <= .008),
            'flat_top': bool(np.linalg.norm(green_plane[:2]) < np.tan(np.deg2rad(10))),
            'broad_face': bool(np.allclose(np.sort(green['dimensions']),
                old['expected_final_dimensions'], rtol=.20, atol=.005)),
            'red_at_target': bool(np.mean(np.all(np.abs(red_uv) <= red_size / 2 + .008, axis=1)) > .8),
            'support_height': bool(abs(float(plane_z(support, red_center[:2])
                - plane_z(np.asarray(old['red_top_plane']), red_center[:2]))) <= .008)}
        success = bool(all(checks.values()))
        evidence.update(plain({'outcome': 'PHYSICAL_SUCCESS' if success else 'UNVERIFIED',
            'checks': checks, 'green_final': green, 'green_top_plane': green_plane,
            'red_top_plane': support, 'center_offset_m': uv, 'inferred_bottom_gap_m': gap,
            'limitations': 'Contact is inferred from post-release depth and measured dimensions. Calibration and contours remain provisional. Occluded support is UNVERIFIED; sparse observations do not establish long-term stability. Evaluation tolerances never gate execution.'}))
        return {'success': success, 'evidence': evidence}
    except (TaskBlocked, ValueError, KeyError, np.linalg.LinAlgError) as exc:
        evidence['reason'] = str(exc)
        return {'success': False, 'evidence': plain(evidence)}
