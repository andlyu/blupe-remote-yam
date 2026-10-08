import numpy as np
from scipy.spatial.transform import Rotation, Slerp
from scene_geometry import TaskBlocked, world_points, mask_points, fit_support, plane_z, rpy_from_rotation

QUERIES = {
    'green_top': 'Only the currently visible upward-facing flat top face of the solid thick green rectangular cuboid, including after stacking. Exclude side faces, thin green rod, towel and robot. Do not infer hidden pixels.',
    'red_top': 'Only the visible upward-facing top face of the sole center-left red cuboid, initially near pixel (230,265) in the top image. Follow this cuboid after stacking. Exclude sides, round red token, green block and robot. Do not infer hidden pixels.',
    'red': 'All visible surfaces of the sole center-left red cuboid, initially near pixel (230,265), including exposed sides beneath green after stacking. Exclude round tokens and other objects.',
    'source_support': 'Exposed towel immediately surrounding the base of the thick green cuboid. Exclude both green objects, robot and tabletop.',
    'red_local_table': 'Exposed white tabletop immediately surrounding the sole center-left red cuboid within two block lengths. Exclude objects, towel, writing, robot and shadow boundaries.'
}


def plain(x):
    if isinstance(x, np.ndarray): return x.tolist()
    if isinstance(x, np.generic): return x.item()
    if isinstance(x, dict): return {k: plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)): return [plain(v) for v in x]
    return x


def unit(x):
    x = np.asarray(x, float)
    length = np.linalg.norm(x)
    if not np.isfinite(length) or length == 0: raise TaskBlocked('Degenerate axis')
    return x / length


def observe(tools, names):
    f = tools['get_camera_rgbd']('top')
    coeffs = np.asarray(f.get('metadata', {}).get('factory_color_intrinsics', {}).get('coeffs', [0.] * 5))
    if coeffs.shape != (5,) or not np.isfinite(coeffs).all() or np.any(coeffs):
        raise TaskBlocked('Top reconstruction requires the supplied zero-distortion capture')
    p = world_points(f)
    if np.asarray(f['rgb']).shape[:2] != p.shape[:2]: raise TaskBlocked('Paired raster mismatch')
    proposals = {}
    for name in names:
        q = tools['segment_camera_rgb'](f['rgb'], QUERIES[name])
        mask = np.asarray(q['mask'])
        if mask.dtype != np.bool_ or mask.shape != p.shape[:2]: raise TaskBlocked('Malformed mask: ' + name)
        proposals[name] = q
    evidence = dict(metadata=f.get('metadata', {}), K=f['K'], T_world_camera=f['T_world_camera'], proposals=proposals)
    return p, proposals, evidence


def cloud(p, masks, name):
    return mask_points(p, masks[name]['mask'])


def face(xyz):
    if len(xyz) < 3: raise TaskBlocked('Insufficient visible face depth')
    plane = fit_support(xyz)
    normal = unit([-plane[0], -plane[1], 1.])
    xy = np.median(xyz[:, :2], axis=0)
    origin = np.r_[xy, plane_z(plane, xy)]
    _, axes = np.linalg.eigh(np.cov((xyz[:, :2] - xy).T))
    a = np.r_[axes[:, -1], 0.]
    a = unit(a - normal * np.dot(a, normal))
    b = unit(np.cross(normal, a))
    frame = np.column_stack([a, b, normal])
    uv = (xyz - origin) @ frame[:, :2]
    lo, hi = np.quantile(uv, [.01, .99], axis=0)
    center = origin + frame[:, :2] @ ((lo + hi) / 2)
    size = hi - lo
    if size[1] > size[0]: frame, size = np.column_stack([b, -a, normal]), size[::-1]
    if not np.isfinite(size).all() or np.any(size <= 0): raise TaskBlocked('Invalid face dimensions')
    return dict(top_center=center, dimensions=size, frame=frame, top_plane=plane, samples=len(xyz))


def supported(top, xyz):
    radius = 2 * np.linalg.norm(top['dimensions'])
    xyz = xyz[np.linalg.norm(xyz[:, :2] - top['top_center'][:2], axis=1) <= radius]
    support = fit_support(xyz)
    n, c = top['frame'][:, 2], top['top_center']
    denominator = n[2] - np.dot(support[:2], n[:2])
    if denominator <= 0: raise TaskBlocked('Invalid support geometry')
    height = (c[2] - plane_z(support, c[:2])) / denominator
    if not np.isfinite(height) or height <= 0: raise TaskBlocked('Invalid measured thickness')
    evidence = dict(plane=support, samples=len(xyz), radius_m=radius,
        residual_quantiles_m=np.quantile(xyz[:, 2] - plane_z(support, xyz[:, :2]), [.05, .5, .95]))
    return dict(top, height_m=height, center=c - height * n / 2), evidence


def rectangle(center, frame, size):
    signs = np.array([[-1., -1.], [1., -1.], [1., 1.], [-1., 1.]])
    return center + (signs * np.asarray(size) / 2) @ frame[:, :2].T


def overlap(poly, size):
    poly = np.asarray(poly)
    for axis in (0, 1):
        for sign in (-1., 1.):
            if len(poly) == 0: raise TaskBlocked('No finite support overlap')
            out = []
            previous = poly[-1]
            dp = size[axis] / 2 - sign * previous[axis]
            for current in poly:
                dc = size[axis] / 2 - sign * current[axis]
                if (dp >= 0) != (dc >= 0): out.append(previous + (current - previous) * dp / (dp - dc))
                if dc >= 0: out.append(current)
                previous, dp = current, dc
            poly = np.asarray(out)
    if len(poly) < 3: raise TaskBlocked('Degenerate finite support')
    return poly


def area(p):
    return abs(float(np.sum(p[:, 0] * np.roll(p[:, 1], -1) - p[:, 1] * np.roll(p[:, 0], -1)))) / 2


def inside(poly, point):
    edges = np.roll(poly, -1, axis=0) - poly
    delta = point - poly
    cross = edges[:, 0] * delta[:, 1] - edges[:, 1] * delta[:, 0]
    return bool(np.all(cross >= -1e-10) or np.all(cross <= 1e-10))


def vertices(station, side, aperture):
    data = station['planner_validation']['native_finger_boxes']
    if data['frame'] != 'aspire_grasp': raise TaskBlocked('Unexpected native geometry frame')
    signs = np.array([[i, j, k] for i in (-1., 1.) for j in (-1., 1.) for k in (-1., 1.)])
    result = []
    for box in data['arms'][side]:
        half = np.asarray(box['half_sizes_m'], float)
        rot = np.asarray(box['rotation_grasp'], float)
        closed = np.asarray(box['center_closed_grasp_m'], float)
        opened = np.asarray(box['center_open_grasp_m'], float)
        if half.shape != (3,) or rot.shape != (3, 3) or closed.shape != (3,) or opened.shape != (3,):
            raise TaskBlocked('Malformed native finger box')
        result.append(closed * (1 - aperture) + opened * aperture + (signs * half) @ rot.T)
    if not result: raise TaskBlocked('Missing native finger geometry')
    return np.concatenate(result)


def acquire(g, config, station):
    side, sign, tilt, skew, insertion = config
    n = g['frame'][:, 2]
    closing = Rotation.from_rotvec(n * np.deg2rad(skew)).as_matrix() @ (sign * g['frame'][:, 1])
    z = -n
    convention = station['gripper_geometry']['closing_axis']
    if convention == 'grasp_x': base = np.column_stack([closing, np.cross(z, closing), z])
    elif convention == 'grasp_y': base = np.column_stack([np.cross(closing, z), closing, z])
    else: raise TaskBlocked('Unknown closing convention')
    rotation = Rotation.from_rotvec(closing * np.deg2rad(tilt)).as_matrix() @ base
    # Actual v6 pickup supports top-center API XY with shallow insertion.
    # Nominal collision pad centers are deliberately NOT pose corrections.
    site = g['top_center'].copy()
    site[2] -= insertion * g['height_m']
    span = g['dimensions'][1] / abs(np.dot(closing, g['frame'][:, 1]))
    projected_span = np.sum(np.abs(rotation[:, 0 if convention == 'grasp_x' else 1] @ g['frame']) * np.r_[g['dimensions'], g['height_m']])
    if projected_span >= .0752: raise TaskBlocked('Measured short-span grasp exceeds native inner opening')
    relative = rotation.T @ (g['center'] - site)
    return site, rotation, dict(payload_to_grasp_translation=relative,
        payload_to_grasp_rotation=rotation.T @ g['frame'], measured_short_span_m=span,
        projected_closing_span_m=projected_span, native_inner_gap_m=.0752,
        site_minus_top_center_m=site - g['top_center'], nominal_pad_correction_applied=False,
        physical_contact_calibration_measured=False)


def placement(g, r, site, pickup, yaw):
    frame = r['frame'] @ Rotation.from_euler('z', yaw, degrees=True).as_matrix()
    delta = frame @ g['frame'].T
    final = delta @ pickup
    bottom = r['top_center'] + .001 * r['frame'][:, 2]
    center = bottom + g['height_m'] * frame[:, 2] / 2
    place = center - delta @ (g['center'] - site)
    footprint = (rectangle(bottom, frame, g['dimensions']) - r['top_center']) @ r['frame'][:, :2]
    contact = overlap(footprint, r['dimensions'])
    projection = np.r_[center[:2], plane_z(r['top_plane'], center[:2])]
    gravity = (projection - r['top_center']) @ r['frame'][:, :2]
    return place, final, dict(center=center, frame=frame, footprint=footprint, overlap=contact,
        supported_area_fraction=area(contact) / area(footprint), gravity_projection=gravity,
        gravity_over_contact=inside(contact, gravity),
        overhang_m=np.maximum(np.max(np.abs(footprint), axis=0) - r['dimensions'] / 2, 0.))


def move(stage, side, position, rotation):
    return dict(stage=stage, kind='move', arguments={side + '_target_pos': np.asarray(position).tolist(),
        side + '_target_rpy': rpy_from_rotation(rotation)})


def segments(steps, state):
    jaws = {s: float(np.asarray(state['arms'][s]['gripper_pos']).reshape(-1)[0]) for s in ('left', 'right')}
    result = []
    for step in steps:
        if step['kind'] in ('open', 'close'): jaws[step['side']] = float(step['kind'] == 'open')
        else: result.append(dict(stage=step['stage'], arguments=step['arguments'], gripper_state=dict(jaws)))
    return result


def candidate(g, r, station, state, config, yaw, park, carry_tilt=0.):
    side = config[0]
    idle = 'left' if side == 'right' else 'right'
    grasp, pickup, acquisition = acquire(g, config, station)
    relative = acquisition['payload_to_grasp_translation']
    place, final, placed = placement(g, r, grasp, pickup, yaw)
    start = np.asarray(state['arms'][side]['ee_pos'])
    start_R = Rotation.from_quat(state['arms'][side]['ee_quat']).as_matrix()
    idle_start = np.asarray(state['arms'][idle]['ee_pos'])
    idle_R = Rotation.from_quat(state['arms'][idle]['ee_quat']).as_matrix()
    approach = grasp - .05 * pickup[:, 2]
    low = grasp + [0., 0., .035]
    retracted = low + .06 * unit(np.r_[start[:2] - grasp[:2], 0.])
    radius = np.linalg.norm(np.r_[g['dimensions'], g['height_m']]) / 2
    source_center = retracted + pickup @ relative
    source_center[2] = max(source_center[2], max(g['top_center'][2], r['top_center'][2]) + radius + .05)
    raised = source_center - pickup @ relative
    target_center = np.r_[placed['center'][:2], source_center[2]]
    closing_index = 0 if station['gripper_geometry']['closing_axis'] == 'grasp_x' else 1
    transport = Rotation.from_rotvec(final[:, closing_index] * np.deg2rad(carry_tilt)).as_matrix() @ final
    interpolation = Slerp([0., 1.], Rotation.from_matrix(np.stack([pickup, transport])))
    transfers = []
    for t in (.25, .5, .75, 1.):
        rot = interpolation(t).as_matrix()
        center = (1 - t) * source_center + t * target_center
        transfers.append((center - rot @ relative, rot))
    signs = np.array([[a, b, c] for a in (-1., 1.) for b in (-1., 1.) for c in (-1., 1.)])
    corners = placed['center'] + (signs * np.r_[g['dimensions'], g['height_m']] / 2) @ placed['frame'].T
    open_vertices = vertices(station, side, 1.) @ final.T
    withdrawal = max(.07, float(np.max(corners[:, 2]) - place[2] - np.min(open_vertices[:, 2]) + .025))
    clear = place + [0., 0., withdrawal]
    above = target_center - final @ relative
    above[2] = max(above[2], clear[2])
    steps = [dict(stage='open_before_pickup', kind='open', side=side),
        move('source_approach', side, approach, pickup), move('source_grasp', side, grasp, pickup),
        dict(stage='close_on_green', kind='close', side=side),
        move('short_vertical_lift', side, low, pickup), move('retract_before_raise', side, retracted, pickup),
        move('raise_payload', side, raised, pickup)]
    direction = 1. if idle == 'left' else -1.
    idle_park = idle_start + np.array([park[0], direction * park[1], park[2]])
    steps += [dict(stage='open_idle_arm', kind='open', side=idle), move('park_idle_arm_clear', idle, idle_park, idle_R)]
    steps += [move('transfer_' + str(i), side, pos, rot) for i, (pos, rot) in enumerate(transfers)]
    steps += [move('align_original_support_face', side, above, final), move('place_on_red', side, place, final),
        dict(stage='release_green', kind='open', side=side), move('open_geometry_at_release', side, place, final)]
    # Small vertical waypoints constrain the intended withdrawal corridor.
    for i, fraction in enumerate((.25, .5, .75, 1.)):
        steps.append(move('withdraw_vertical_' + str(i), side, place + [0., 0., withdrawal * fraction], final))
    steps.append(move('retreat_above_target', side, above, final))
    # All reverse paths are newly planned with open jaws. Keep the hand above
    # the placed block while leaving its footprint, including during rotation.
    for i, (pos, rot) in enumerate(reversed(transfers)):
        pos = pos.copy()
        minimum_relative_z = np.min((vertices(station, side, 1.) @ rot.T)[:, 2])
        pos[2] = max(pos[2], np.max(corners[:, 2]) - minimum_relative_z + .025)
        steps.append(move('reverse_transfer_' + str(i), side, pos, rot))
    steps += [move('clear_target_view', side, raised, pickup),
        move('return_active_measured_start', side, start, start_R),
        move('return_idle_measured_start', idle, idle_start, idle_R)]
    diagnosis = []
    for aperture in (0., .5, 1.):
        v = place + vertices(station, side, aperture) @ final.T
        diagnosis.append(dict(aperture=aperture, minimum_world_z_m=np.min(v[:, 2]),
            minimum_height_above_red_plane_m=np.min(v[:, 2] - plane_z(r['top_plane'], v[:, :2]))))
    return steps, dict(strategy='site_centered_short_span_direct_stack', side=side, configuration=config,
        grasp=grasp, pickup_rotation=pickup, acquisition=acquisition, place=place, final_rotation=final,
        placement=placed, yaw_deg=yaw, carry_tilt_deg=carry_tilt, idle_parking_offset=park,
        withdrawal_pose=clear, native_finger_support_diagnosis=diagnosis)


def failing_stage(feedback):
    return feedback.get('failing_stage') or next((s.get('stage') for s in feedback.get('stages', []) if s.get('status') != 'Success'), None)


def build_task(tools):
    station = tools['get_station_info']()
    state = tools['get_planning_state']()
    p, masks, observation = observe(tools, list(QUERIES))
    g, source = supported(face(cloud(p, masks, 'green_top')), cloud(p, masks, 'source_support'))
    r, table = supported(face(cloud(p, masks, 'red_top')), cloud(p, masks, 'red_local_table'))
    sides = sorted(('left', 'right'), key=lambda s: np.linalg.norm(np.asarray(state['arms'][s]['ee_pos']) - g['top_center']))
    configs = [(s, sign, tilt, skew, .25) for sign, tilt, skew in
        [(1., 30., 0.), (-1., -40., -8.), (-1., -45., 0.), (1., 15., 0.),
         (-1., -30., 0.), (1., 0., 0.), (-1., -40., 8.), (1., 30., -8.)] for s in sides]
    trials, viable = [], []
    best, best_progress = None, -1
    def attempt(config, yaw, park, carry):
        nonlocal best, best_progress
        steps, geometry = candidate(g, r, station, state, config, yaw, park, carry)
        feedback = tools['plan_freespace_sequence'](segments(steps, state))
        progress = sum(s.get('status') == 'Success' for s in feedback.get('stages', []))
        trials.append(dict(configuration=geometry, complete_task=True, native_feedback=feedback))
        if best is None or progress > best_progress or feedback.get('success') is True:
            best, best_progress = (steps, geometry, feedback), progress
        return feedback, progress
    found = False
    for config in configs:
        feedback, progress = attempt(config, 0., (0., .08, .04), 0.)
        if feedback.get('success') is True:
            found = True
            break
        if failing_stage(feedback) not in ('source_approach', 'source_grasp', 'short_vertical_lift', 'retract_before_raise', 'raise_payload'):
            viable.append((progress, config))
    if not found:
        ranked = sorted(viable, key=lambda item: -item[0])[:4]
        options = [(yaw, park, carry) for yaw in (180., 90., -90., 45., -45., 0.)
            for park, carry in [((0., .08, .04), 0.), ((0., .06, .08), 0.), ((0., .08, .04), 30.)]]
        for yaw, park, carry in options:
            for _, config in ranked:
                if len(trials) >= 64: break
                feedback, _ = attempt(config, yaw, park, carry)
                if feedback.get('success') is True:
                    found = True
                    break
            if found or len(trials) >= 64 or not ranked: break
    if best is None: raise TaskBlocked('No complete task proposal')
    steps, geometry, feedback = best
    return plain(dict(steps=steps, station=station, planning_start=state, initial_observation=observation,
        geometry=dict(green=g, red=r, source_support=source['plane'], table_plane=table['plane'], **geometry),
        local_support_evidence=dict(source=source, red=table), planning_trials=trials,
        candidate_preview_success=feedback.get('success') is True,
        target_identity='Sole current center-left red cuboid',
        previous_review=dict(v11_physical='FAILED: missed thick green cuboid pickup; green remained on towel.',
            v11_native='UNVERIFIED; all 26 packets completed.',
            v5_operator='It fell off the red... while the arm was moving back.',
            causal_limit='Nominal pad registration remains unmeasured; no new physical calibration is claimed.'),
        reasoning=['API grasp-site XY is the fresh measured top center, with shallow vertical insertion. Remove the nominal 54 mm pad-centroid compensation and long-axis contact bias.',
            'Use a measured short-span grasp. Earlier v6 establishes contact-convention evidence only; its overwide long-axis grasp is not replayed.',
            'Retain the rigid object-to-grasp transform, original bottom face, finite support overlap, free placement yaw and planned idle-arm clearance.',
            'Native boxes diagnose model clearance and size withdrawal; they never supply a physical contact offset. Native collision checks remain unchanged.',
            'Every proposal includes opening, pickup, transfer, supported release, open-jaw withdrawal and both measured-start returns. Runtime performs fresh full planning before action.',
            'One physical attempt returns for review. Runtime owns motion enablement, recording through parking and shutdown.']))


def evaluate(tools, task):
    evidence = dict(outcome='UNVERIFIED', operator_observation=None,
        limitations='Depth-based support inference; final runner evaluation after normal parking is required. No command receipt proves retention.')
    try:
        p, masks, obs = observe(tools, ['green_top', 'red', 'red_top'])
        evidence['observation'] = obs
        old = task['geometry']
        red = old['red']
        rc, rf, size = np.asarray(red['top_center']), np.asarray(red['frame']), np.asarray(red['dimensions'])
        green = face(cloud(p, masks, 'green_top'))
        rp, rt = cloud(p, masks, 'red'), cloud(p, masks, 'red_top')
        if len(rp) < 3: raise TaskBlocked('Insufficient fresh red evidence')
        uv = (rp - rc) @ rf[:, :2]
        at_target = bool(np.mean(np.all(np.abs(uv) <= size / 2 + .008, axis=1)) > .8)
        if len(rt) >= 3:
            support = fit_support(rt)
            method = 'Fresh visible red top'
        else:
            support = np.asarray(red['top_plane']).copy()
            edge = float(np.quantile(rp[:, 2] - plane_z(support, rp[:, :2]), .95))
            boundary = np.min(np.abs(np.abs(uv) - size / 2), axis=1)
            corroborated = bool(at_target and abs(edge) <= .006 and np.ptp(rp[:, 2]) >= .35 * red['height_m']
                and np.mean(boundary <= .008) > .6 and np.max(np.ptp(uv, axis=0)) >= .5 * min(size))
            evidence['red_edge_evidence'] = dict(edge_residual_m=edge, corroborated=corroborated)
            if not corroborated: raise TaskBlocked('Hidden support lacks fresh side and upper-edge corroboration')
            support[2] += edge
            method = 'Historical plane slope corroborated by fresh visible red edge'
        height = old['green']['height_m']
        normal = green['frame'][:, 2]
        bottom = green['top_center'] - height * normal
        footprint = (rectangle(bottom, green['frame'], green['dimensions']) - rc) @ rf[:, :2]
        poly = overlap(footprint, size)
        contact = rc + poly @ rf[:, :2].T
        bottom_plane = green['top_plane'].copy()
        bottom_plane[2] -= height / normal[2]
        gaps = plane_z(bottom_plane, contact[:, :2]) - plane_z(support, contact[:, :2])
        center = green['top_center'] - height * normal / 2
        projection = np.r_[center[:2], plane_z(support, center[:2])]
        gravity = (projection - rc) @ rf[:, :2]
        offset = (bottom - rc) @ rf[:, :2]
        checks = dict(red_at_target=at_target,
            centered=bool(np.all(np.abs(offset) < size * .25)),
            original_face=bool(np.allclose(np.sort(green['dimensions']), np.sort(old['green']['dimensions']), rtol=.20, atol=.005)),
            parallel=bool(np.dot(normal, unit([-support[0], -support[1], 1.])) > np.cos(np.deg2rad(8))),
            red_height_consistent=bool(abs(plane_z(support, rc[:2]) - rc[2]) <= .008),
            finite_gravity_support=inside(poly, gravity),
            contact_consistent=bool(abs(float(np.min(gaps))) <= .006 and np.max(gaps) <= .010))
        success = bool(all(checks.values()))
        evidence.update(outcome='PHYSICAL_SUCCESS' if success else 'UNVERIFIED', checks=checks,
            green_final=green, support_plane=support, support_method=method, finite_overlap=poly,
            supported_area_fraction=area(poly) / area(footprint), center_offset_m=offset,
            gap_range_m=[np.min(gaps), np.max(gaps)])
        return dict(success=success, evidence=plain(evidence))
    except (TaskBlocked, ValueError, KeyError, np.linalg.LinAlgError) as exc:
        evidence['reason'] = type(exc).__name__ + ': ' + str(exc)
        return dict(success=False, evidence=plain(evidence))
