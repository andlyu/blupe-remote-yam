import numpy as np
from scipy.spatial.transform import Rotation
from scene_geometry import (TaskBlocked, world_points, mask_points, fit_support,
    plane_z, grasp_pose, rotation_from_rpy, rpy_from_rotation)


QUERIES = {
 'green_top': 'Only the upward-facing flat top surface of the solid green rectangular cuboid block, wherever it is now, including if stacked on red. Exclude side faces, pen, towel and robot.',
 'red': 'All currently visible surfaces of the red rectangular cuboid, including exposed side faces if its top is hidden under green. Exclude the round red token and pink figure.',
 'red_top': 'Only the currently visible upward-facing top surface of the red rectangular cuboid. Do not include green or infer hidden pixels.',
 'source_support': 'Exposed supporting surface immediately surrounding the bottom of the green rectangular block: towel fabric if it rests on the towel, otherwise white tabletop. Exclude blocks, other objects and robot.',
 'table': 'The exposed white tabletop surface, excluding objects, towel, robot parts, writing, and background.'
}


def plain(v):
    if isinstance(v, np.ndarray):
        return v.tolist()
    if isinstance(v, np.generic):
        return v.item()
    if isinstance(v, dict):
        return {k: plain(x) for k, x in v.items()}
    if isinstance(v, (tuple, list)):
        return [plain(x) for x in v]
    return v


def calibrated_points(frame):
    points = world_points(frame)
    intr = frame.get('metadata', {}).get('factory_color_intrinsics', {})
    c = np.asarray(intr.get('coeffs', [0.] * 5), dtype=float)
    if c.shape != (5,) or not np.isfinite(c).all():
        raise TaskBlocked('Malformed distortion metadata')
    if not np.any(c):
        return points
    if 'inverse_brown_conrady' not in str(intr.get('model', '')).lower():
        raise TaskBlocked('Unsupported unrectified camera model')
    depth = np.asarray(frame['depth_m'], dtype=float)
    k = np.asarray(frame['K'], dtype=float)
    t = np.asarray(frame['T_world_camera'], dtype=float)
    v, u = np.indices(depth.shape)
    xo, yo = (u-k[0, 2])/k[0, 0], (v-k[1, 2])/k[1, 1]
    x, y = xo.copy(), yo.copy()
    k1, k2, p1, p2, k3 = c
    for _ in range(10):
        r2 = x*x+y*y
        inv = 1./(1.+((k3*r2+k2)*r2+k1)*r2)
        xq, yq = x/inv, y/inv
        dx = 2*p1*xq*yq+p2*(r2+2*xq*xq)
        dy = 2*p2*xq*yq+p1*(r2+2*yq*yq)
        x, y = (xo-dx)*inv, (yo-dy)*inv
    points = np.stack([x*depth, y*depth, depth], axis=-1) @ t[:3, :3].T+t[:3, 3]
    points[~np.isfinite(depth) | (depth <= 0)] = np.nan
    return points


def observe(tools, camera, names):
    frame = tools['get_camera_rgbd'](camera)
    if np.asarray(frame['rgb']).shape[:2] != np.asarray(frame['depth_m']).shape:
        raise TaskBlocked('Paired raster mismatch: '+camera)
    points = calibrated_points(frame)
    proposals = {}
    for name in names:
        proposal = tools['segment_camera_rgb'](frame['rgb'], QUERIES[name])
        mask = np.asarray(proposal['mask'])
        if mask.dtype != np.bool_ or mask.shape != points.shape[:2]:
            raise TaskBlocked('Malformed exact mask: '+camera+'/'+name)
        proposals[name] = proposal
    return points, proposals, {'camera': camera, 'metadata': frame.get('metadata', {}),
        'K': frame['K'], 'T_world_camera': frame['T_world_camera'], 'proposals': proposals}


def cloud(points, proposals, name):
    return mask_points(points, proposals[name]['mask'])


def box(xyz, support):
    if len(xyz) < 3:
        raise TaskBlocked('Insufficient visible top depth')
    plane = fit_support(xyz)
    origin = np.median(xyz[:, :2], axis=0)
    _, axes = np.linalg.eigh(np.cov((xyz[:, :2]-origin).T))
    a = axes[:, -1]
    basis = np.column_stack([a, [-a[1], a[0]]])
    uv = (xyz[:, :2]-origin) @ basis
    lo, hi = np.quantile(uv, [.01, .99], axis=0)
    xy = origin+basis @ ((lo+hi)/2)
    z = float(plane_z(plane, xy))
    height = z-float(plane_z(support, xy))
    if not np.isfinite(np.r_[hi-lo, height]).all() or np.any(np.r_[hi-lo, height] <= 0):
        raise TaskBlocked('Invalid measured box')
    return {'top_center': np.r_[xy, z], 'dimensions': hi-lo, 'height_m': height,
            'basis': basis, 'top_plane': plane, 'visible_samples': len(xyz)}


def rectangle(center, basis, size):
    signs = np.array([[-1., -1.], [1., -1.], [1., 1.], [-1., 1.]])
    return np.asarray(center)+ (signs*np.asarray(size)/2) @ np.asarray(basis).T


def overlap_polygon(subject, center, basis, size):
    # Clip a convex footprint against the finite measured red rectangle.
    polygon = (np.asarray(subject)-center) @ basis
    for axis in (0, 1):
        for sign in (-1., 1.):
            output = []
            if len(polygon) == 0:
                raise TaskBlocked('No finite support overlap')
            previous = polygon[-1]
            dp = size[axis]/2-sign*previous[axis]
            for current in polygon:
                dc = size[axis]/2-sign*current[axis]
                if (dp >= 0) != (dc >= 0):
                    output.append(previous+(current-previous)*dp/(dp-dc))
                if dc >= 0:
                    output.append(current)
                previous, dp = current, dc
            polygon = np.asarray(output)
    if len(polygon) < 3:
        raise TaskBlocked('Degenerate support overlap')
    return polygon @ basis.T+center


def finite_place(green, red, support, grasp, delta):
    center = np.asarray(green['top_center'])-[0., 0., green['height_m']/2]
    target_xy = np.asarray(red['top_center'])[:2]
    final_basis = delta[:2, :2] @ green['basis']
    footprint = rectangle(target_xy, final_basis, green['dimensions'])
    overlap = overlap_polygon(footprint, target_xy, red['basis'], red['dimensions'])
    # Only actual overlap can support the object. Never extrapolate a tilted
    # red plane to green corners that overhang red.
    bottom = float(np.max(plane_z(support, overlap)))+.001
    desired_center = np.r_[target_xy, bottom+green['height_m']/2]
    place = desired_center-delta @ (center-grasp)
    return place, {'footprint': footprint, 'support_overlap': overlap,
                   'bottom_z': bottom, 'desired_center': desired_center,
                   'center_gap_m': bottom-float(plane_z(support, target_xy))}


def move(stage, side, pos, rotation):
    return {'stage': stage, 'kind': 'move', 'arguments': {
        side+'_target_pos': np.asarray(pos).tolist(),
        side+'_target_rpy': rpy_from_rotation(rotation)}}


def segments(steps, state):
    jaws = {}
    for side in ('left', 'right'):
        value = np.asarray(state['arms'][side]['gripper_pos'], dtype=float).reshape(-1)
        if value.size != 1 or not np.isfinite(value[0]):
            raise TaskBlocked('Malformed planning jaw state')
        jaws[side] = float(value[0])
    out = []
    for step in steps:
        if step['kind'] in ('open', 'close'):
            jaws[step['side']] = 1. if step['kind'] == 'open' else 0.
        else:
            out.append({'stage': step['stage'], 'arguments': step['arguments'],
                        'gripper_state': dict(jaws)})
    return out


def failed_stage(feedback):
    if feedback.get('failing_stage') is not None:
        return feedback['failing_stage']
    return next((s.get('stage') for s in feedback.get('stages', [])
                 if s.get('status') != 'Success'), None)


def build_task(tools):
    station = tools['get_station_info']()
    state = tools['get_planning_state']()
    points, masks, observation = observe(tools, 'top', list(QUERIES))
    source = fit_support(cloud(points, masks, 'source_support'))
    table = fit_support(cloud(points, masks, 'table'))
    support = fit_support(cloud(points, masks, 'red_top'))
    green = box(cloud(points, masks, 'green_top'), source)
    red = box(cloud(points, masks, 'red_top'), table)
    jaw = station['gripper_geometry']
    _, rpy = grasp_pose(green, jaw)
    base = rotation_from_rpy(rpy)
    closing = 0 if jaw.get('closing_axis', 'grasp_x') == 'grasp_x' else 1
    offset = np.asarray(jaw.get('grasp_to_planner_offset_m', [0., 0., 0.]), dtype=float)
    pads = np.asarray(jaw['grip_pad_full_dimensions_m'], dtype=float)
    aperture = float(jaw['open_width_m'])
    if offset.shape != (3,) or pads.shape != (3,) or not np.isfinite(np.r_[offset, pads, aperture]).all():
        raise TaskBlocked('Malformed selected gripper geometry')
    center = np.asarray(green['top_center'])
    sides = sorted(('left', 'right'), key=lambda s: float(
        np.linalg.norm(np.asarray(state['arms'][s]['ee_pos'])-center)+
        np.linalg.norm(np.asarray(state['arms'][s]['ee_pos'])-red['top_center'])))
    trials, blocked_sources = [], set()
    best, best_progress = None, -1
    found = False
    candidates = [(side, heading, tilt, depth)
        for tilt in (30., -30., 0., 45., -45.)
        for heading in (0., 180., 90., -90.)
        for side in sides for depth in (.25, .40)]
    for side, heading, tilt, depth in candidates:
        key = (side, heading, tilt, depth)
        if key in blocked_sources:
            continue
        hb = base @ Rotation.from_euler('z', heading, degrees=True).as_matrix()
        pickup = Rotation.from_rotvec(hb[:, closing]*np.deg2rad(tilt)).as_matrix() @ hb
        contact = center.copy()
        contact[2] -= depth*green['height_m']
        grasp = contact+pickup @ offset
        approach = grasp-.05*pickup[:, 2]
        for yaw in (0., 180., 90., -90., 45., -45.):
            delta = Rotation.from_euler('z', yaw, degrees=True).as_matrix()
            final = delta @ pickup
            place, placement = finite_place(green, red, support, grasp, delta)
            # Upstream nominal pad geometry determines a conservative withdrawal
            # distance, not an inferred contact width. Native model validates arm.
            pad_vertical = float(np.sum(np.abs(final[2, :])*pads)/2)
            open_vertical = abs(float(final[2, closing]))*aperture/2
            withdrawal = green['height_m']+pads[2]+pad_vertical+open_vertical+.015
            clear = place+[0., 0., withdrawal]
            high = max(float(approach[2]+.04), float(clear[2]),
                       float(red['top_center'][2]+np.linalg.norm(np.r_[green['dimensions'], green['height_m']])+.04))
            lift, carry = np.r_[grasp[:2], high], np.r_[place[:2], high]
            start = np.asarray(state['arms'][side]['ee_pos'])
            lane = np.r_[start[:2], max(high, float(start[2]))]
            start_rotation = Rotation.from_quat(state['arms'][side]['ee_quat']).as_matrix()
            steps = [
                {'stage': 'open_before_pickup', 'kind': 'open', 'side': side},
                move('source_approach', side, approach, pickup),
                move('source_grasp', side, grasp, pickup),
                {'stage': 'close_on_green', 'kind': 'close', 'side': side},
                move('vertical_lift', side, lift, pickup),
                move('carry_above_red', side, carry, final),
                move('place_on_finite_red_support', side, place, final),
                {'stage': 'release_green', 'kind': 'open', 'side': side},
                move('withdraw_above_block', side, clear, final),
                move('clear_target_view', side, lane, final),
                move('return_to_measured_start', side, start, start_rotation)]
            feedback = tools['plan_freespace_sequence'](segments(steps, state))
            pose = {'side': side, 'heading_deg': heading, 'tilt_deg': tilt,
                    'depth_fraction': depth, 'yaw_deg': yaw, 'grasp': grasp,
                    'place': place, 'pickup_rotation': pickup, 'final_rotation': final,
                    'object_rotation': delta, 'placement': placement,
                    'withdrawal_m': withdrawal, 'withdrawal_pose': clear,
                    'view_clearance_pose': lane, 'open_aperture_nominal_m': aperture,
                    'pad_dimensions_nominal_m': pads}
            trials.append({'configuration': pose, 'native_feedback': feedback})
            progress = sum(s.get('status') == 'Success' for s in feedback.get('stages', []))
            if best is None or progress > best_progress or feedback.get('success') is True:
                best, best_progress = (steps, pose, feedback), progress
            if feedback.get('success') is True:
                found = True
                break
            if failed_stage(feedback) in ('source_approach', 'source_grasp', 'vertical_lift'):
                blocked_sources.add(key)
                break
            if len(trials) >= 72:
                break
        if found or len(trials) >= 72:
            break
    steps, pose, feedback = best
    return plain({'steps': steps, 'station': station, 'planning_start': state,
        'initial_observation': observation, 'planning_trials': trials,
        'candidate_preview_success': feedback.get('success') is True,
        'geometry': {'green': green, 'red': red, 'source_support': source,
                     'table_plane': table, 'red_top_plane': support, **pose},
        'previous_operator_observation': {'report': 'It failed',
            'clarification': 'It fell off the red... while the arm was moving back.',
            'scope': 'Previous v5 trial only; task retreat versus parking remains unresolved.'},
        'reasoning': [
            'Previous nine commands completed, including release and retreat, but the operator confirmed a physical failure. Pickup is not diagnosed as failed.',
            'Fix infinite-plane extrapolation: constrain support-height calculation to intersection of measured green and red footprints. Preserve initial bottom face; yaw remains free.',
            'Replace the 4 cm retreat with geometry-derived vertical withdrawal, lateral clearance and return to the measured initial arm pose. All eight paths are planned together, including full-open jaw states.',
            'Nominal jaw dimensions are selected upstream values, not independently measured contact geometry. Collision/snags and instability remain hypotheses.',
            'Evaluate fresh top and wrist views after the arm clears the target. Fresh visible red sides may corroborate a hidden red top; historical plane alone cannot prove success.',
            'Operator reset confirmation is pending in supplied review. External operator/queue flow owns readiness and admission; no automatic physical retry or recovery is implemented.',
            'Runtime owns fresh complete planning before motion, cache execution, controller checks, recording and shutdown. Recorded-scene plans are not live execution evidence.'],
        'evidence_requests': [
            'Replay this evaluator against the saved v5 after-retreat RGB-D and later metric scene; those files are not accessible through this policy tool contract.',
            'Keep timestamped release, each withdrawal stage and runner parking evidence separate. Extend external recording through normal parking; policy has no recording or shutdown API.']} )


def evaluate(tools, task):
    evidence = {'outcome': 'UNVERIFIED', 'operator_observation': None, 'views': [],
        'limitations': 'Fresh geometry supports inferred contact only. Partial faces and calibration remain uncertain. No command-completion or historical-plane-only success. Parking evidence is owned by the runner.'}
    old = task['geometry']
    red = old['red']
    rc = np.asarray(red['top_center'])
    rb = np.asarray(red['basis'])
    rs = np.asarray(red['dimensions'])
    old_support = np.asarray(old['red_top_plane'])
    green_candidates, red_candidates = [], []
    contradictions = []
    for camera in ('top', 'right', 'left'):
        try:
            points, proposals, observation = observe(tools, camera, ['green_top', 'red', 'red_top', 'table'])
            view = {'observation': observation}
            evidence['views'].append(view)
            gp = cloud(points, proposals, 'green_top')
            rp = cloud(points, proposals, 'red')
            rt = cloud(points, proposals, 'red_top')
            if len(gp) >= 3:
                green = box(gp, old_support)
                gc = np.asarray(green['top_center'])
                uv = (gc[:2]-rc[:2]) @ rb
                checks = {'centered': bool(np.all(np.abs(uv) < rs*.25)),
                    'flat': bool(np.linalg.norm(green['top_plane'][:2]) < np.tan(np.deg2rad(10))),
                    'complete_face': bool(np.allclose(np.sort(green['dimensions']),
                        np.sort(old['green']['dimensions']), rtol=.20, atol=.005))}
                view['green'] = green
                view['green_checks'] = checks
                if checks['complete_face'] and not checks['centered']:
                    contradictions.append(camera+': complete green face is away from target')
                if all(checks.values()):
                    green_candidates.append((camera, green))
            if len(rp) >= 3:
                uv = (rp[:, :2]-rc[:2]) @ rb
                near = np.all(np.abs(uv) <= rs/2+.008, axis=1)
                side_consistent = float(np.mean(near)) > .8
                fresh_support = None
                method = None
                if len(rt) >= 3:
                    try:
                        fresh_support = fit_support(rt)
                        method = 'fresh visible red top'
                    except (TaskBlocked, ValueError, np.linalg.LinAlgError):
                        pass
                if fresh_support is None and side_consistent:
                    # A hidden top can be corroborated by a visible upper side
                    # edge at its known height, plus fresh depth along that side.
                    boundary_distance = np.min(np.abs(np.abs(uv)-rs/2), axis=1)
                    upper_residual = rp[:, 2]-plane_z(old_support, rp[:, :2])
                    span = float(np.max(np.ptp(uv, axis=0)))
                    edge_seen = bool(abs(float(np.quantile(upper_residual, .95))) <= .006)
                    wall_seen = bool(np.ptp(rp[:, 2]) >= .35*red['height_m'])
                    boundary_seen = bool(np.mean(boundary_distance <= .008) > .6)
                    view['red_side_checks'] = {'upper_edge': edge_seen, 'wall': wall_seen,
                        'boundary': boundary_seen, 'span_m': span}
                    if edge_seen and wall_seen and boundary_seen and span >= .5*min(rs):
                        fresh_support = old_support.copy()
                        fresh_support[2] += float(np.quantile(upper_residual, .95))
                        method = 'historical slope corroborated by fresh red side and upper edge'
                if fresh_support is not None:
                    stable_height = abs(float(plane_z(fresh_support, rc[:2])-plane_z(old_support, rc[:2]))) <= .008
                    view['red_support'] = {'method': method, 'plane': fresh_support,
                                           'at_target': side_consistent, 'height_consistent': stable_height}
                    if side_consistent and stable_height:
                        red_candidates.append((camera, fresh_support, method))
        except (TaskBlocked, ValueError, KeyError, np.linalg.LinAlgError) as exc:
            evidence['views'].append({'camera': camera, 'measurement_error': str(exc)})
    checks = []
    for gcamera, green in green_candidates:
        for rcamera, support, method in red_candidates:
            gc = np.asarray(green['top_center'])
            footprint = rectangle(gc[:2], green['basis'], green['dimensions'])
            overlap = overlap_polygon(footprint, rc[:2], rb, rs)
            bottom = float(gc[2]-old['green']['height_m'])
            gap = bottom-float(np.max(plane_z(support, overlap)))
            checks.append({'green_camera': gcamera, 'red_camera': rcamera,
                'support_method': method, 'finite_support_gap_m': gap,
                'contact_consistent': bool(abs(gap) <= .006)})
    success = bool(any(c['contact_consistent'] for c in checks) and not contradictions)
    evidence.update({'outcome': 'PHYSICAL_SUCCESS' if success else 'UNVERIFIED',
        'contact_checks': checks, 'contradictions': contradictions,
        'reason': 'Fresh centered green and corroborated red support required; missing or partial evidence remains unverified.'})
    return {'success': success, 'evidence': plain(evidence)}
