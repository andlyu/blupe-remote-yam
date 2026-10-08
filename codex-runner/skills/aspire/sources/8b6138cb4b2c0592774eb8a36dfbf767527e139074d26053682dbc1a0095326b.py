import numpy as np
from scipy.spatial.transform import Rotation
from scene_geometry import (TaskBlocked, world_points, mask_points, fit_support,
    plane_z, grasp_pose, rotation_from_rpy, rpy_from_rotation, place_on_support)


QUERIES = {
    'green_top': 'Only the upward-facing flat top surface of the solid green rectangular cuboid block, excluding side faces, pen and towel.',
    'red': 'The red rectangular block on the white table; exclude the round red token and pink figure. Segment all visible block surfaces.',
    'red_top': 'Only the upward-facing flat top surface of the red rectangular block, excluding its side faces and any object covering it.',
    'source_support': 'Exposed supporting surface immediately surrounding the bottom of the green rectangular block: towel fabric if it rests on the towel, otherwise white tabletop. Exclude the block, other objects and robot parts.',
    'table': 'The exposed white tabletop surface, excluding objects, towel, robot parts, writing, and background.'
}


def plain(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    return value


def observe(tools, names):
    frame = tools['get_camera_rgbd']('top')
    shape = np.asarray(frame['depth_m']).shape
    if np.asarray(frame['rgb']).shape[:2] != shape:
        raise TaskBlocked('Paired top RGB/depth raster mismatch')
    coefficients = np.asarray(frame.get('metadata', {}).get(
        'factory_color_intrinsics', {}).get('coeffs', [0.] * 5), dtype=float)
    if not np.isfinite(coefficients).all() or np.any(coefficients != 0):
        raise TaskBlocked('This deprojection requires the reported zero-distortion top capture')
    points = world_points(frame)
    proposals = {}
    for name in names:
        proposal = tools['segment_camera_rgb'](frame['rgb'], QUERIES[name])
        mask = np.asarray(proposal['mask'])
        if mask.shape != shape or mask.dtype != np.bool_:
            raise TaskBlocked('Malformed exact mask: ' + name)
        proposals[name] = proposal
    evidence = {'camera': 'top', 'metadata': frame.get('metadata', {}),
                'K': frame['K'], 'T_world_camera': frame['T_world_camera'],
                'proposals': proposals}
    return points, proposals, evidence


def cloud(points, proposals, name):
    return mask_points(points, proposals[name]['mask'])


def surface(points, proposals, name):
    return fit_support(cloud(points, proposals, name))


def top_box(points, proposals, name, support):
    # The mask already selects the top face. Use its complete finite footprint,
    # rather than selecting an upper-Z strip that can truncate a sloping face.
    xyz = cloud(points, proposals, name)
    if len(xyz) < 3:
        raise TaskBlocked('Insufficient measured top-face geometry: ' + name)
    top_plane = fit_support(xyz)
    origin = np.median(xyz[:, :2], axis=0)
    covariance = np.cov((xyz[:, :2] - origin).T)
    _, axes = np.linalg.eigh(covariance)
    long_axis = axes[:, -1]
    basis = np.column_stack([long_axis, [-long_axis[1], long_axis[0]]])
    uv = (xyz[:, :2] - origin) @ basis
    lo, hi = np.quantile(uv, [.01, .99], axis=0)
    xy = origin + basis @ ((lo + hi) / 2.)
    z = float(plane_z(top_plane, xy))
    height = z - float(plane_z(support, xy))
    dimensions = hi - lo
    if not np.isfinite(np.r_[dimensions, height]).all() or np.any(np.r_[dimensions, height] <= 0):
        raise TaskBlocked('Invalid measured box: ' + name)
    return {'top_center': np.r_[xy, z], 'dimensions': dimensions,
            'height_m': height, 'basis': basis, 'top_plane': top_plane,
            'visible_samples': len(xyz)}


def move(stage, side, position, rotation):
    return {'stage': stage, 'kind': 'move', 'arguments': {
        side + '_target_pos': np.asarray(position).tolist(),
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
            jaws[step['side']] = 1. if step['kind'] == 'open' else 0.
        else:
            result.append({'stage': step['stage'], 'arguments': step['arguments'],
                           'gripper_state': dict(jaws)})
    return result


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
    points, proposals, observation = observe(tools, list(QUERIES))
    source_support = surface(points, proposals, 'source_support')
    table = surface(points, proposals, 'table')
    support = surface(points, proposals, 'red_top')
    green = top_box(points, proposals, 'green_top', source_support)
    red = top_box(points, proposals, 'red_top', table)
    geometry = station['gripper_geometry']
    _, base_rpy = grasp_pose(green, geometry)
    base = rotation_from_rpy(base_rpy)
    closing_index = 0 if geometry.get('closing_axis', 'grasp_x') == 'grasp_x' else 1
    offset = np.asarray(geometry.get('grasp_to_planner_offset_m', [0., 0., 0.]), dtype=float)
    if offset.shape != (3,) or not np.isfinite(offset).all():
        raise TaskBlocked('Invalid grasp-site offset')
    center = np.asarray(green['top_center'])
    sides = sorted(('left', 'right'), key=lambda side: float(
        np.linalg.norm(np.asarray(state['arms'][side]['ee_pos']) - center) +
        np.linalg.norm(np.asarray(state['arms'][side]['ee_pos']) - red['top_center'])))
    trials, source_failures = [], set()
    best, best_progress, found = None, -1, False
    # Preserve the operator-established supporting face. A smaller measured
    # top width is not evidence that the block should be rolled onto that face.
    # Each final object rotation is WORLD YAW ONLY, so its initial bottom stays
    # the bottom and measured height remains the vertical extent.
    candidates = [(side, heading, tilt, depth)
                  for tilt in (30., -30., 0., 45., -45., 60., -60.)
                  for heading in (0., 180., 90., -90.)
                  for side in sides for depth in (.40, .25)]
    for side, heading, tilt, depth_fraction in candidates:
        source_key = (side, heading, tilt, depth_fraction)
        if source_key in source_failures:
            continue
        heading_base = base @ Rotation.from_euler('z', heading, degrees=True).as_matrix()
        pickup = Rotation.from_rotvec(heading_base[:, closing_index] * np.deg2rad(tilt)).as_matrix() @ heading_base
        contact = center.copy()
        contact[2] -= depth_fraction * green['height_m']
        grasp = contact + pickup @ offset
        approach = grasp - .05 * pickup[:, 2]
        for yaw in (0., 180., 90., -90., 45., -45.):
            delta = Rotation.from_euler('z', yaw, degrees=True).as_matrix()
            final = delta @ pickup
            place = place_on_support(green, grasp, rpy_from_rotation(pickup),
                red['top_center'][:2], support, rpy_from_rotation(final), clearance_m=.002)
            radius = float(np.linalg.norm(np.r_[green['dimensions'], green['height_m']]))
            high = max(float(approach[2] + .04), float(place[2] + .06),
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
            retreats = [('vertical_4cm', place + [0., 0., .04]),
                        ('reverse_4cm', place - .04 * final[:, 2])]
            for retreat_name, retreat in retreats:
                steps = prefix + [move('retreat_after_release', side, retreat, final)]
                feedback = tools['plan_freespace_sequence'](segments(steps, state))
                pose = {'side': side, 'heading_deg': heading, 'tilt_deg': tilt,
                        'depth_fraction': depth_fraction, 'yaw_deg': yaw,
                        'grasp': grasp, 'place': place, 'carry': carry, 'retreat': retreat,
                        'retreat_variant': retreat_name, 'pickup_rotation': pickup,
                        'final_rotation': final, 'object_rotation': delta}
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
            if found or source_key in source_failures or len(trials) >= 96:
                break
        if found or len(trials) >= 96:
            break
    if best is None:
        raise TaskBlocked('No complete candidate constructed')
    steps, pose, feedback = best
    return plain({'steps': steps, 'task': 'Stack green on red preserving its initial supporting face',
        'station': station, 'planning_start': state, 'initial_observation': observation,
        'planning_trials': trials, 'candidate_preview_success': feedback.get('success') is True,
        'geometry': {'green': green, 'red': red, 'source_support': source_support,
                     'table_plane': table, 'red_top_plane': support, **pose},
        'reasoning': [
            'Previous physical attempt retained green through carry, then stopped at placement on right_joint_4 settle timeout. Release and retreat did not execute. The supplied retained-block views do not establish a stack.',
            'The previous minimum-dimension rule introduced an approximately 90-degree object roll despite the operator placing green flat initially. Remove that rule; preserve the initial bottom and allow free placement yaw.',
            'Measure the entire segmented top footprint and fit its plane; avoid measuring only an upper-Z strip of an already segmented top.',
            'Placement accounts for rotation about the grasp and measured red support. Neither pose coordinates nor block dimensions are copied from the failed attempt.',
            'Contact, grasp slip and loaded tracking remain possible timeout causes, not confirmed diagnoses. Controller tolerances, timeout, collision model and calibration remain unchanged.',
            'Preview all six paths with commanded jaw states, including open-jaw retreat. Runtime owns fresh complete planning before motion, native cache execution, evidence recording and shutdown.',
            'This is an offline revision for review. The external operator/queue flow must establish genuine reset readiness before one new attended physical attempt. This program contains no physical replay or recovery loop.',
            'Held-object contact is not modeled by native planning. Successful planning alone is not physical success.'
        ]})


def evaluate(tools, task):
    evidence = {'outcome': 'UNVERIFIED', 'operator_observation': None}
    try:
        points, proposals, observation = observe(tools, ['green_top', 'red', 'red_top'])
        evidence['final_observation'] = plain(observation)
        support = surface(points, proposals, 'red_top')
        green = top_box(points, proposals, 'green_top', support)
        red_cloud = cloud(points, proposals, 'red')
        if len(red_cloud) < 3:
            raise TaskBlocked('Insufficient visible red depth')
        old = task['geometry']
        red_center = np.asarray(old['red']['top_center'])
        red_basis = np.asarray(old['red']['basis'])
        red_size = np.asarray(old['red']['dimensions'])
        center = np.asarray(green['top_center'])
        uv = (center[:2] - red_center[:2]) @ red_basis
        red_uv = (red_cloud[:, :2] - red_center[:2]) @ red_basis
        gap = float(plane_z(green['top_plane'], center[:2]) - old['green']['height_m']
                    - plane_z(support, center[:2]))
        checks = {
            'centered_on_support': bool(np.all(np.abs(uv) < red_size * .25)),
            'contact_height_consistent': bool(abs(gap) <= .008),
            'flat_top': bool(np.linalg.norm(green['top_plane'][:2]) < np.tan(np.deg2rad(10))),
            'original_face_preserved': bool(np.allclose(np.sort(green['dimensions']),
                np.sort(old['green']['dimensions']), rtol=.20, atol=.005)),
            'red_at_target': bool(np.mean(np.all(np.abs(red_uv) <= red_size / 2 + .008, axis=1)) > .8),
            'support_height_consistent': bool(abs(float(plane_z(support, red_center[:2]) -
                plane_z(np.asarray(old['red_top_plane']), red_center[:2]))) <= .008)}
        success = bool(all(checks.values()))
        evidence.update(plain({'outcome': 'PHYSICAL_SUCCESS' if success else 'UNVERIFIED',
            'checks': checks, 'green_final': green, 'red_top_plane': support,
            'center_offset_m': uv, 'inferred_bottom_gap_m': gap,
            'limitations': 'Assessment uses fresh post-retreat depth. Contact is inferred, not force-measured. Occluded support is UNVERIFIED. Calibration and contours remain provisional; sparse observations cannot establish long-term stability. These tolerances only assess outcome and never gate execution.'}))
        return {'success': success, 'evidence': evidence}
    except (TaskBlocked, ValueError, KeyError, np.linalg.LinAlgError) as exc:
        evidence['reason'] = str(exc)
        return {'success': False, 'evidence': plain(evidence)}
