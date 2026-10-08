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
        'source_support': 'Exposed supporting surface immediately surrounding the bottom of the green rectangular block: towel fabric if it rests on the towel, otherwise white tabletop. Exclude the block, other objects and robot parts.',
        'table': 'The exposed white tabletop surface, excluding objects, towel, robot parts, writing, and background.'}


def plain(x):
    if isinstance(x, np.ndarray):
        return x.tolist()
    if isinstance(x, np.generic):
        return x.item()
    if isinstance(x, dict):
        return {k: plain(v) for k, v in x.items()}
    if isinstance(x, (tuple, list)):
        return [plain(v) for v in x]
    return x


def calibrated_points(frame):
    intrinsics = frame.get('metadata', {}).get('factory_color_intrinsics', {})
    c = np.asarray(intrinsics.get('coeffs', [0.] * 5), dtype=float)
    if c.shape != (5,) or not np.isfinite(c).all():
        raise TaskBlocked('Malformed distortion coefficients')
    validated = world_points(frame)
    if not np.any(c):
        return validated
    if 'inverse_brown_conrady' not in str(intrinsics.get('model', '')).lower():
        raise TaskBlocked('Unsupported unrectified camera model')
    depth = np.asarray(frame['depth_m'], dtype=float)
    k = np.asarray(frame['K'], dtype=float)
    transform = np.asarray(frame['T_world_camera'], dtype=float)
    v, u = np.indices(depth.shape)
    xo, yo = (u-k[0, 2])/k[0, 0], (v-k[1, 2])/k[1, 1]
    x, y = xo.copy(), yo.copy()
    k1, k2, p1, p2, k3 = c
    for _ in range(10):
        r2 = x*x+y*y
        icdist = 1./(1.+((k3*r2+k2)*r2+k1)*r2)
        xq, yq = x/icdist, y/icdist
        dx = 2.*p1*xq*yq+p2*(r2+2.*xq*xq)
        dy = 2.*p2*xq*yq+p1*(r2+2.*yq*yq)
        x, y = (xo-dx)*icdist, (yo-dy)*icdist
    points = np.stack([x*depth, y*depth, depth], axis=-1)
    points = points@transform[:3, :3].T+transform[:3, 3]
    points[~np.isfinite(depth) | (depth <= 0)] = np.nan
    return points


def observe(tools, camera, names):
    frame = tools['get_camera_rgbd'](camera)
    shape = np.asarray(frame['depth_m']).shape
    if np.asarray(frame['rgb']).shape[:2] != shape:
        raise TaskBlocked('Paired raster mismatch: '+camera)
    points = calibrated_points(frame)
    proposals, counts = {}, {}
    for name in names:
        proposal = tools['segment_camera_rgb'](frame['rgb'], queries()[name])
        mask = np.asarray(proposal['mask'])
        if mask.shape != shape or mask.dtype != np.bool_:
            raise TaskBlocked('Malformed mask: '+camera+'/'+name)
        proposals[name] = proposal
        counts[name] = {'pixels': int(mask.sum()), 'finite_depth': len(mask_points(points, mask))}
    return points, proposals, {'camera': camera, 'metadata': frame.get('metadata', {}),
        'K': frame['K'], 'T_world_camera': frame['T_world_camera'],
        'proposals': proposals, 'counts': counts}


def plane(points, proposals, name, camera):
    cloud = mask_points(points, proposals[name]['mask'])
    try:
        return fit_support(cloud)
    except (TaskBlocked, ValueError, np.linalg.LinAlgError) as exc:
        raise TaskBlocked('%s/%s: %d finite points; %s' % (camera, name, len(cloud), exc)) from exc


def box(points, proposals, name, support):
    result = measure_block(points, proposals[name]['mask'], support)
    sizes = np.r_[result['dimensions'], result['height_m']]
    if not np.isfinite(sizes).all() or np.any(sizes <= 0):
        raise TaskBlocked('Invalid measured box: '+name)
    return result


def destination(tools, top, observations):
    errors = []
    for camera in ('top', 'right', 'left'):
        try:
            if camera == 'top':
                points, proposals, observation = top
            else:
                points, proposals, observation = observe(tools, camera, ['red', 'red_top', 'table'])
                observations.append(observation)
            support = plane(points, proposals, 'red_top', camera)
            table = plane(points, proposals, 'table', camera)
            return box(points, proposals, 'red_top', table), support, camera, errors
        except (TaskBlocked, ValueError, np.linalg.LinAlgError) as exc:
            errors.append({'camera': camera, 'reason': str(exc)})
    raise TaskBlocked('No measured red support: '+str(errors))


def move(stage, side, position, rotation):
    return {'stage': stage, 'kind': 'move', 'arguments': {
        side+'_target_pos': np.asarray(position).tolist(),
        side+'_target_rpy': rpy_from_rotation(rotation)}}


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
            result.append({'stage': step['stage'], 'arguments': step['arguments'], 'gripper_state': dict(jaws)})
    return result


def flat_rotations(block):
    sizes = np.r_[block['dimensions'], block['height_m']]
    basis = np.eye(3)
    basis[:2, :2] = block['basis']
    index = int(np.argmin(sizes))
    normal = basis[:, index]
    variants = []
    for sign in (1., -1.):
        target = np.array([0., 0., sign])
        axis = np.cross(normal, target)
        length = np.linalg.norm(axis)
        dot = float(np.dot(normal, target))
        if length > 1e-10:
            delta = Rotation.from_rotvec(axis/length*np.arctan2(length, dot)).as_matrix()
        elif dot > 0:
            delta = np.eye(3)
        else:
            delta = Rotation.from_rotvec(basis[:, (index+1) % 3]*np.pi).as_matrix()
        variants.append((sign, delta))
    return variants, float(sizes[index]), np.sort(np.delete(sizes, index))


def failure(feedback):
    name = feedback.get('failing_stage')
    if name is not None:
        return name
    failures = [s for s in feedback.get('stages', []) if s.get('status') != 'Success']
    return failures[0].get('stage') if failures else None


def build_task(tools):
    station = tools['get_station_info']()
    state = tools['get_planning_state']()
    top = observe(tools, 'top', ['green', 'green_top', 'red', 'red_top', 'source_support', 'table'])
    points, proposals, observation = top
    observations = [observation]
    source_support = plane(points, proposals, 'source_support', 'top')
    green = box(points, proposals, 'green_top', source_support)
    red, support, target_camera, view_errors = destination(tools, top, observations)
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
        np.asarray(state['arms'][side]['ee_pos'])-center)))
    candidates = []
    # The latest native diagnostic is an INVALID OPEN-JAW START at release,
    # not a blocked retreat goal. Changing only retreat cannot fix it.
    # Rotate the source grasp across the other horizontal box axis so the
    # closing/opening axis can remain horizontal after flattening the block.
    for grasp_heading in (90., -90., 0., 180.):
        heading_base = base@Rotation.from_euler('z', grasp_heading, degrees=True).as_matrix()
        for tilt in (0., 30., -30., 60., -60.):
            pickup = Rotation.from_rotvec(heading_base[:, closing_index]*np.deg2rad(tilt)).as_matrix()@heading_base
            for face_sign, flat in flats:
                for yaw in (0., 180., 90., -90., 45., -45.):
                    delta = Rotation.from_euler('z', yaw, degrees=True).as_matrix()@flat
                    final = delta@pickup
                    # Ranking only: prefer opening laterally, then a downward
                    # approach. Keep native validation as the feasibility test.
                    rank = 4.*abs(float(final[2, closing_index]))+float(final[2, 2])+.15*abs(tilt)/60.
                    for side in sides:
                        candidates.append((rank, side, grasp_heading, tilt, face_sign, yaw, pickup, final, delta))
    candidates.sort(key=lambda item: item[0])
    trials, best, best_progress = [], None, -1
    source_failures = set()
    found = False
    for candidate in candidates:
        _, side, heading, tilt, face_sign, yaw, pickup, final, delta = candidate
        for depth_fraction in (.40, .25):
            source_key = (side, heading, tilt, depth_fraction)
            if source_key in source_failures:
                continue
            contact = center.copy()
            contact[2] -= depth_fraction*green['height_m']
            grasp = contact+pickup@offset
            place = place_on_support(green, grasp, rpy_from_rotation(pickup),
                red['top_center'][:2], support, rpy_from_rotation(final), clearance_m=.002)
            approach = grasp-.05*pickup[:, 2]
            lift = grasp+np.array([0., 0., .06])
            carry = place+np.array([0., 0., .04])
            steps = [
                {'stage': 'open_before_pickup', 'kind': 'open', 'side': side},
                move('source_approach', side, approach, pickup),
                move('source_grasp', side, grasp, pickup),
                {'stage': 'close_on_green', 'kind': 'close', 'side': side},
                move('vertical_lift', side, lift, pickup),
                move('carry_above_red', side, carry, final),
                move('place_flat_on_red', side, place, final),
                {'stage': 'release_green', 'kind': 'open', 'side': side},
                move('retreat_after_release', side, carry, final)]
            feedback = tools['plan_freespace_sequence'](segments(steps, state))
            pose = {'side': side, 'grasp_heading_deg': heading, 'pickup_tilt_deg': tilt,
                'flat_face_sign': face_sign, 'yaw_deg': yaw, 'depth_fraction': depth_fraction,
                'grasp': grasp, 'place': place, 'carry': carry,
                'pickup_rotation': pickup, 'final_rotation': final, 'object_rotation': delta,
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
            # No retreat-goal sweep: change grasp-to-object relation and thus
            # the open-jaw release configuration on the next candidate.
            if len(trials) >= 96:
                break
        if found or len(trials) >= 96:
            break
    steps, pose, feedback = best
    return plain({'steps': steps, 'task': 'Stack green flat on red',
        'station': station, 'planning_start': state, 'observations': observations,
        'target_camera': target_camera, 'target_view_failures': view_errors,
        'planning_trials': trials, 'candidate_preview_success': feedback.get('success') is True,
        'geometry': {'green': green, 'red': red, 'source_support': source_support,
            'red_top_plane': support, 'expected_final_height': final_height,
            'expected_final_dimensions': final_dimensions, **pose},
        'reasoning': [
            'Latest native retreat diagnostic reports invalid start: open left_rf_rot overlaps play_table by up to 14 mm; retreat goal is valid. No physical motion occurred.',
            'Fix the release configuration by changing the source closing axis and grasp-to-object relation. Prefer a horizontal opening axis at flat placement.',
            'Changing grasp depth remains centered within the measured block. The failed two-percent edge pinch is not a fallback.',
            'Keep measured flat support placement with 2 mm clearance; do not raise the release into an unsupported drop to hide the collision.',
            'Every candidate plans all six paths including retreat from the OPEN release state. Native collision and IK failures remain failures.',
            'No calibration changes, table-model changes, reduced release opening, target movement or arm-channel swaps.',
            'Runtime owns offline/live mode, fresh full planning, cache execution, motion enablement, recording and shutdown. No automatic physical retries.',
            'Held-object collision/contact is not modeled. Physical retention and stable stacking require post-release evidence.']})


def evaluate(tools, task):
    evidence = {'outcome': 'UNVERIFIED', 'operator_observation': None, 'views': []}
    old = task['geometry']
    red_center = np.asarray(old['red']['top_center'])
    red_basis = np.asarray(old['red']['basis'])
    red_dimensions = np.asarray(old['red']['dimensions'])
    for camera in ('top', 'right', 'left'):
        try:
            points, proposals, observation = observe(tools, camera, ['green', 'green_top', 'red', 'red_top'])
            view = {'observation': observation}
            evidence['views'].append(view)
            support = plane(points, proposals, 'red_top', camera)
            green_plane = plane(points, proposals, 'green_top', camera)
            green = box(points, proposals, 'green_top', support)
            red_cloud = mask_points(points, proposals['red']['mask'])
            if len(red_cloud) < 3:
                raise TaskBlocked('Insufficient visible red depth')
            center = np.asarray(green['top_center'])
            uv = (center[:2]-red_center[:2])@red_basis
            gap = float(plane_z(green_plane, center[:2])-old['expected_final_height']-plane_z(support, center[:2]))
            red_uv = (red_cloud[:, :2]-red_center[:2])@red_basis
            checks = {
                'center_supported': bool(np.all(np.abs(uv) < red_dimensions/2)),
                'contact_height': bool(abs(gap) <= .008),
                'flat_top': bool(np.linalg.norm(green_plane[:2]) < np.tan(np.deg2rad(10))),
                'broad_face': bool(np.allclose(np.sort(green['dimensions']), old['expected_final_dimensions'], rtol=.20, atol=.005)),
                'red_at_target': bool(np.mean(np.all(np.abs(red_uv) <= red_dimensions/2+.008, axis=1)) > .8),
                'support_height': bool(abs(float(plane_z(support, red_center[:2])-plane_z(np.asarray(old['red_top_plane']), red_center[:2]))) <= .008)}
            view.update({'checks': checks, 'green_final': green, 'bottom_gap_m': gap, 'center_offset_m': uv})
            success = bool(all(checks.values()))
            evidence['outcome'] = 'PHYSICAL_SUCCESS' if success else 'UNVERIFIED'
            evidence['limitations'] = 'Post-release contact and flatness are inferred; long-term stability is not established. Calibration and contours remain provisional. Evaluation never gates execution.'
            return {'success': success, 'evidence': plain(evidence)}
        except (TaskBlocked, ValueError, KeyError, np.linalg.LinAlgError) as exc:
            evidence['views'].append({'camera': camera, 'measurement_error': str(exc)})
    evidence['reason'] = 'Insufficient visible support geometry in all paired views.'
    return {'success': False, 'evidence': plain(evidence)}
