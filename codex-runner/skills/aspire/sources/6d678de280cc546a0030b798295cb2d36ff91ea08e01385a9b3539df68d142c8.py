import json
import numpy as np
from scipy import ndimage
from scipy.optimize import least_squares
from scipy.spatial import ConvexHull
from scipy.spatial.transform import Rotation
Q = {'block': 'black rectangular block', 'chip': 'blue round poker chip', 'source_table': 'white tabletop', 'source_cloth': 'large teal fabric towel', 'occupied_red': 'red rectangular block', 'occupied_green': 'green rectangular block', 'occupied_rod': 'thin green rod'}
OCCUPANTS = tuple(k for k in Q if k.startswith('occupied_'))


def plain(x):
    if isinstance(x, np.ndarray): return x.tolist()
    if isinstance(x, np.generic): return x.item()
    if isinstance(x, dict): return {k: plain(v) for k, v in x.items()}
    if isinstance(x, (tuple, list)): return [plain(v) for v in x]
    return x


def log(name, value):
    print(name + ' ' + json.dumps(plain(value), default=str), flush=True)


def observe(tools):
    f = tools['get_camera_rgbd']('top')
    d = np.asarray(f['depth_m'], float)
    K, T = np.asarray(f['K'], float), np.asarray(f['T_world_camera'], float)
    if np.asarray(f['rgb']).shape[:2] != d.shape: raise RuntimeError('Paired RGB-D raster mismatch')
    coeffs = f.get('metadata', {}).get('factory_color_intrinsics', {}).get('coeffs', [0]*5)
    if np.any(np.asarray(coeffs, float)): raise RuntimeError('Unexpected nonzero top-camera distortion')
    v, u = np.indices(d.shape)
    rays = np.stack((u, v, np.ones_like(u)), -1) @ np.linalg.inv(K).T
    p = (rays*d[..., None]) @ T[:3, :3].T + T[:3, 3]
    return f, p, (d > 0) & np.isfinite(d) & np.isfinite(p).all(-1)


def segment(tools, f, key, ev, optional=False):
    result = tools['segment_camera_rgb'](f['rgb'], Q[key])
    mask = np.asarray(result['mask'], bool)
    if mask.shape != np.asarray(f['depth_m']).shape:
        raise RuntimeError('Mask shape mismatch: '+key)
    if key == 'block' and mask.any():
        labels, _ = ndimage.label(mask)
        counts = np.bincount(labels.ravel())
        counts[0] = 0
        mask = labels == counts.argmax()
    v, u = np.where(mask)
    ev.append(dict(name=key, query=Q[key], pixels=int(mask.sum()),
        bbox_xywh=[int(u.min()), int(v.min()), int(np.ptp(u)+1), int(np.ptp(v)+1)] if len(u) else None,
        rgb_median=np.median(np.asarray(f['rgb'])[mask], axis=0) if mask.any() else None,
        proposal={k:v for k,v in result.items() if k != 'mask'},
        interpretation='SAM3 instance proposal; identity and geometry are not ground truth'))
    if not mask.any() and not optional:
        raise RuntimeError('Missing required mask: '+key)
    return mask


def points(p, valid, mask, minimum=12):
    for region in (ndimage.binary_erosion(mask), mask):
        a = p[valid & region]
        if len(a) >= minimum: return a
    raise RuntimeError('Insufficient measured depth: '+str(int((valid & mask).sum())))


def fit_plane(p):
    p = np.asarray(p, float)
    if len(p) < 12: raise RuntimeError('Insufficient plane points')
    origin = np.median(p[:, :2], axis=0)
    A = np.c_[p[:, :2]-origin, np.ones(len(p))]
    keep = np.ones(len(p), bool)
    for _ in range(6):
        c, _, rank, _ = np.linalg.lstsq(A[keep], p[keep, 2], rcond=None)
        if rank < 3: raise RuntimeError('Unresolved plane rank')
        residual = p[:, 2]-A@c
        noise = max(.0005, float(1.4826*np.median(np.abs(residual[keep]-np.median(residual[keep])))))
        new = np.abs(residual-np.median(residual[keep])) <= 3*noise
        if new.sum() < 12: break
        keep = new
    c, _, rank, _ = np.linalg.lstsq(A[keep], p[keep, 2], rcond=None)
    if rank < 3: raise RuntimeError('Unresolved final plane rank')
    c[2] -= origin@c[:2]
    return c, noise


def z_at(c, xy):
    return np.asarray(xy) @ np.asarray(c)[:2] + c[2]


def boundary(a):
    a = np.asarray(a, float)
    return a[ConvexHull(a[:, :2]).vertices, :2]


def upward_face(bp, support, scale):
    heights = bp[:, 2]-z_at(support, bp[:, :2])
    upper = np.sort(heights[heights >= np.median(heights)])
    if len(upper) < 12: raise RuntimeError('Insufficient upper block depth')
    tolerance = max(.0015, 2.5*float(scale))
    ends = np.searchsorted(upper, upper+2*tolerance, side='right')
    start = int(np.argmax(ends-np.arange(len(upper))))
    offset = float(np.median(upper[start:ends[start]]))
    keep = np.abs(heights-offset) <= tolerance
    if keep.sum() < 12: raise RuntimeError('Unresolved upward depth layer')
    face = bp[keep]
    plane = np.asarray(support, float).copy()
    plane[2] += float(np.median(heights[keep]))
    residual = face[:, 2]-z_at(plane, face[:, :2])
    noise = max(.0005, float(1.4826*np.median(np.abs(residual-np.median(residual)))))
    _, axes = np.linalg.eigh(np.cov(bp[:, :2].T))
    axes = axes[:, ::-1]
    lo, hi = np.percentile(bp[:, :2]@axes, [1, 99], axis=0)
    xy, dims = ((lo+hi)/2)@axes.T, hi-lo
    if not np.isfinite(np.r_[xy, dims]).all() or np.any(dims <= 0):
        raise RuntimeError('Invalid measured block geometry')
    return xy, axes, dims, plane, noise, dict(face_boundary_xy=boundary(face),
        instance_samples=len(bp), face_samples=len(face), measured_dimensions_m=dims,
        physical_metrology=False, method='Retained dominant upper layer and RGB-D footprint estimator')


def footprint(xy, axes, dims):
    a, b = np.meshgrid(np.linspace(-.5, .5, 17), np.linspace(-.5, .5, 17))
    return np.asarray(xy)+(np.c_[a.ravel(), b.ravel()]*dims)@np.asarray(axes).T


def polygon_overlap(a, b):
    a, b = np.asarray(a), np.asarray(b)
    for poly in (a, b):
        for edge in np.roll(poly, -1, axis=0)-poly:
            axis = np.array([-edge[1], edge[0]])
            pa, pb = a@axis, b@axis
            if pa.max() < pb.min() or pb.max() < pa.min(): return False
    return True


def chip_geometry(f, p, valid, cm, bm, table):
    cp = points(p, valid, cm & ~bm)
    plane = np.asarray(table, float).copy()
    offsets = cp[:, 2]-z_at(plane, cp[:, :2])
    plane[2] += float(np.median(offsets))
    depth_noise = max(.0005, float(1.4826*np.median(np.abs(offsets-np.median(offsets)))))
    rim = cm & ~ndimage.binary_erosion(cm) & ~ndimage.binary_dilation(bm, iterations=3)
    v, u = np.where(rim)
    if len(u) < 8: raise RuntimeError('Insufficient unoccluded chip rim')
    K, T = np.asarray(f['K']), np.asarray(f['T_world_camera'])
    rays = np.c_[u, v, np.ones(len(u))] @ np.linalg.inv(K).T
    directions = rays @ T[:3, :3].T
    normal = np.r_[-plane[:2], 1.0]
    distances = (plane[2]-T[:3, 3]@normal)/(directions@normal)
    xy = (T[:3, 3]+distances[:, None]*directions)[:, :2]
    if not np.isfinite(xy).all() or np.any(distances <= 0):
        raise RuntimeError('Unresolved chip rim projection')
    origin = xy.mean(0)
    q = xy-origin
    A = np.c_[2*q, np.ones(len(q))]
    sol, _, rank, _ = np.linalg.lstsq(A, (q*q).sum(1), rcond=None)
    if rank < 3: raise RuntimeError('Chip rim does not determine a circle')
    radius = float(np.sqrt(max(0, sol[2]+sol[:2]@sol[:2])))
    fit = least_squares(lambda x: np.linalg.norm(q-x[:2], axis=1)-x[2],
        np.r_[sol[:2], radius], loss='soft_l1', f_scale=depth_noise)
    center, radius = origin+fit.x[:2], float(fit.x[2])
    residual = np.linalg.norm(xy-center, axis=1)-radius
    noise = max(depth_noise, float(np.sqrt(np.mean(residual**2))))
    covariance = np.linalg.pinv(fit.jac.T@fit.jac)*noise**2
    center_sigma = float(np.sqrt(max(0, np.linalg.eigvalsh(covariance[:2, :2]).max())))
    angles = np.sort(np.arctan2(xy[:, 1]-center[1], xy[:, 0]-center[0]))
    span = float(2*np.pi-np.diff(np.r_[angles, angles[0]+2*np.pi]).max())
    bound = min(.012, .65*radius)
    reliable = bool(fit.success and radius > 0 and span >= np.pi/2 and
        np.linalg.matrix_rank(fit.jac) == 3 and 3*center_sigma < bound)
    return dict(xy=center, radius=radius, plane=plane, z=float(z_at(plane, center)),
        noise=noise, center_sigma=center_sigma, observed_arc_radians=span,
        reliable=reliable, visible_boundary_xy=boundary(cp),
        slope_basis='Fresh local tabletop; current chip depth supplies elevation',
        uncertainty_scope='Conditional circle uncertainty excludes calibration bias')


def measure(tools):
    ev = []
    f, p, valid = observe(tools)
    masks = {k:segment(tools, f, k, ev, optional=k != 'block') for k in Q}
    bm = masks['block']
    bp = points(p, valid, bm)
    center0 = np.median(bp[:, :2], axis=0)
    extent = np.linalg.norm(np.ptp(bp[:, :2], axis=0))
    local = np.linalg.norm(p[..., :2]-center0, axis=-1) < 2*extent
    excluded = bm | masks['chip'] | masks['source_cloth']
    for key in OCCUPANTS: excluded |= masks[key]
    tm = masks['source_table'] & ~ndimage.binary_dilation(excluded, iterations=2)
    table, ns = fit_plane(points(p, valid, tm & local))
    source = table.copy()
    source_kind = 'measured_local_table'
    distance = ndimage.distance_transform_edt(~bm)
    near = (distance > 2) & (distance < 15)
    cloth = masks['source_cloth'] & ~bm
    if (cloth & near).sum() > (tm & near).sum():
        source, ns = fit_plane(points(p, valid, cloth & local))
        source_kind = 'measured_local_cloth'
    scale = float(f.get('metadata', {}).get('depth_scale_m', .001))
    xy, axes, dims, top_plane, nb, face_ev = upward_face(bp, source, scale)
    target, target_error = None, None
    try:
        cp = points(p, valid, masks['chip'] & ~bm)
        near_chip = np.linalg.norm(p[..., :2]-np.median(cp[:, :2], axis=0), axis=-1) < 2*extent
        target_table, _ = fit_plane(points(p, valid, tm & near_chip))
        target = chip_geometry(f, p, valid, masks['chip'], bm, target_table)
    except Exception as exc:
        target_error = str(exc)
    if target is not None and target['reliable']:
        if np.all(np.abs((target['xy']-xy)@axes) < dims/2):
            source = np.asarray(target['plane']).copy()
            source_kind = 'visible_measured_chip_under_block'
    base, top = float(z_at(source, xy)), float(z_at(top_plane, xy))
    occupied, context_unknown, obstacle_tops = {}, [], [top]
    for key in OCCUPANTS:
        if masks[key].any():
            try:
                op = points(p, valid, masks[key] & ~bm)
                occupied[key] = boundary(op)
                obstacle_tops.append(float(np.quantile(op[:, 2], .99)))
            except Exception as exc:
                context_unknown.append(dict(name=key, error=str(exc)))
    geometry = dict(source_xy=xy, axes=axes, dimensions=dims, source_plane=source,
        table_plane=table, top_plane=top_plane, base=base, top=top, height=top-base,
        noise=max(ns, nb), source_kind=source_kind, target_measurement=target,
        target_error=target_error, capture_metadata=f.get('metadata', {}),
        perception_evidence=ev, face_evidence=face_ev, occupied=occupied,
        context_unknown=context_unknown, obstacle_top_z=max(obstacle_tops), target_identity=Q['chip'],
        support_provenance='Current surrounding surface; hidden underside is inferred')
    if not np.isfinite(geometry['height']) or geometry['height'] <= 0:
        raise RuntimeError('Unresolved measured block height')
    log('FRESH_GEOMETRY', geometry)
    return geometry


def display(rot):
    x, y, z = rot.as_euler('xyz', degrees=True)
    return ((np.array([y, -x, -z-90])+180)%360-180).tolist()


def rotation(rpy):
    return Rotation.from_euler('xyz', [-rpy[1], rpy[0], -rpy[2]-90], degrees=True)


def contact(station, side, width):
    tips = {m['mesh']:m for m in station['planner_validation']['native_gripper_meshes']['arms'][side]}
    centers, slides, facets = [], [], []
    for name in ('tip_left', 'tip_right'):
        m = tips[name]
        facet = max(m['opening_axis_planar_facets'], key=lambda f:f['max_grasp_m'][2])
        facets.append(facet)
        centers.append((np.asarray(facet['min_grasp_m'])+np.asarray(facet['max_grasp_m']))/2)
        slides.append(np.asarray(m['aperture']['open']['center_grasp_m'])-np.asarray(m['aperture']['closed']['center_grasp_m']))
    centers, slides = np.asarray(centers), np.asarray(slides)
    gap, travel = centers[1, 0]-centers[0, 0], slides[1, 0]-slides[0, 0]
    jaw = float((width-gap)/travel)
    contacts = centers+jaw*slides
    return contacts.mean(0), dict(contact_points_native=contacts, facets_closed_native=facets,
        slide_vectors_native_m=slides, predicted_enclosing_jaw_fraction=jaw,
        open_gap_m=gap+travel, physical_contact_calibration_measured=False)


def segments(steps, state, station):
    jaws, assumptions = {}, []
    for side in ('left', 'right'):
        value = state['arms'][side].get('gripper_pos')
        if value is None:
            if station.get('physical_motion_enabled', False):
                raise RuntimeError('Missing measured jaw: '+side)
            value = 1.0
            assumptions.append(side+': assumed open for offline preview only')
        jaws[side] = float(np.asarray(value).reshape(-1)[0])
    result = []
    for step in steps:
        if step['kind'] in ('open', 'close'):
            jaws[step['side']] = float(step['kind'] == 'open')
        else:
            for side in jaws:
                key = side+'_gripper_target_width'
                if key in step['arguments']: jaws[side] = float(step['arguments'][key])
            result.append(dict(stage=step['stage'], arguments=step['arguments'], gripper_state=dict(jaws)))
    return result, assumptions


def hold_task(tools, station, state, geometry, reason):
    arguments = {'planning_speed': .5}
    for side in ('left', 'right'):
        arguments[side+'_target_pos'] = list(state['arms'][side]['ee_pos'])
        arguments[side+'_target_rpy'] = display(Rotation.from_quat(state['arms'][side]['ee_quat']))
    steps = [dict(stage='retain_measured_pose_for_observation', kind='move', arguments=arguments)]
    seq, assumptions = segments(steps, state, station)
    result = tools['plan_freespace_sequence'](seq)
    log('OBSERVATION_HOLD_NATIVE_RESULT', result)
    if result.get('success') is not True: raise RuntimeError(json.dumps(plain(result)))
    return plain(dict(steps=steps, geometry=geometry, decision=reason,
        observation_only_repair=True, preview=result, preview_segments=seq,
        jaw_assumptions=assumptions, measured_start_state=state,
        evidence_scope='Stationary native preview; unresolved evidence does not justify corrective pickup'))


def build_task(tools):
    station, state = tools['get_station_info'](), tools['get_planning_state']()
    try:
        g = measure(tools)
    except Exception as exc:
        return hold_task(tools, station, state, {'measurement_error':str(exc)}, 'Fresh geometry unresolved')
    target = g['target_measurement']
    if target is None or not target['reliable']:
        return hold_task(tools, station, state, g, 'Blue chip absent or poorly constrained; placement remains unknown')
    error = float(np.linalg.norm(g['source_xy']-target['xy']))
    bound = min(.012, .65*target['radius'])
    if error-3*target['center_sigma'] <= bound:
        return hold_task(tools, station, state, g, 'Fresh evidence does not establish a centering failure; evaluate without repicking')
    log('REPAIR_DIAGNOSIS', dict(center_error_m=error, centering_bound_m=bound,
        basis='Fresh blue-chip circle and block instance establish separation; old saved target is not reused',
        prior_failure='Parked visible chip differed from the saved target despite other checks passing'))
    failures = []
    spans = tuple(dict.fromkeys((int(np.argmax(g['dimensions'])), int(np.argmin(g['dimensions'])))))
    for fraction in (.65, .50, .70):
        for tilt in (30., -30., 0., 15., -15.):
            for axis in spans:
                opening = np.r_[g['axes'][:, axis], 0.0]
                down = np.array([0., 0., -1.])
                native = np.column_stack((opening, np.cross(down, opening), down))
                yaw = display(Rotation.from_matrix(native))[2]
                for side in ('right', 'left'):
                    idle = 'left' if side == 'right' else 'right'
                    midpoint, cad = contact(station, side, float(g['dimensions'][axis]))
                    detection = dict(position_3d=np.r_[g['source_xy'], g['base']+fraction*g['height']].tolist(), score=1.0)
                    grasps = tools['sample_topdown_geometric'](Q['block'], detection=detection,
                        camera='top', yaws=[(yaw+360)%360-180, yaw], pitches=[180.],
                        z_offsets=[0.], width=float(cad['open_gap_m']), max_grasps=2)
                    for grasp in grasps:
                        R = rotation(grasp.rpy)*Rotation.from_euler('x', tilt, degrees=True)
                        rpy = display(R)
                        site = np.asarray(grasp.position)-R.apply(midpoint)
                        local_base = R.inv().apply(np.r_[g['source_xy'], g['base']]-site)
                        fp = footprint(target['xy'], g['axes'], g['dimensions'])
                        relative_bottom = z_at(g['source_plane'], g['source_xy']+fp-target['xy'])-g['base']
                        supported = np.linalg.norm(fp-target['xy'], axis=1) <= target['radius']
                        release_z = float(np.max(z_at(target['plane'], fp[supported])-relative_bottom[supported]))
                        offset = -R.apply(local_base)
                        drop = np.asarray(tools['estimate_drop'](Q['chip'],
                            detection={'position_3d':np.r_[target['xy'], release_z].tolist()},
                            z_offset=float(offset[2]), camera='top'), float)
                        drop[:2] += offset[:2]
                        approach, lift = site+[0, 0, .75*g['height']], site+[0, 0, g['height']]
                        transfer_z = max(g['top'], lift[2], drop[2],
                            g['obstacle_top_z']-float(R.apply(local_base)[2])-float(relative_bottom.min()))+g['height']
                        raised, above = np.r_[site[:2], transfer_z], np.r_[drop[:2], transfer_z]
                        retract = raised.copy()
                        retract[:2] += .35*(drop[:2]-site[:2])
                        start = np.asarray(state['arms'][side]['ee_pos'])
                        sr = Rotation.from_quat(state['arms'][side]['ee_quat'])
                        stage = .5*(start+approach)
                        stage[2] = max(start[2], approach[2])
                        half = sr*Rotation.from_rotvec(.5*(sr.inv()*R).as_rotvec())
                        idle_rpy = display(Rotation.from_quat(state['arms'][idle]['ee_quat']))
                        def move(name, pos, orient):
                            return dict(stage=name, kind='move', arguments={
                                side+'_target_pos':np.asarray(pos).tolist(), side+'_target_rpy':list(orient),
                                idle+'_target_pos':list(state['arms'][idle]['ee_pos']),
                                idle+'_target_rpy':idle_rpy, 'planning_speed':.5})
                        steps = [dict(stage='open_for_grasp', kind='open', side=side),
                            move('stage_near_arm', stage, display(half)),
                            move('orient_before_extension', stage, rpy),
                            move('approach_block', approach, rpy), move('grasp_block', site, rpy),
                            dict(stage='close_on_block', kind='close', side=side),
                            move('lift_block', lift, rpy), move('raise_in_transfer_lane', raised, rpy),
                            move('retract_holding_block', retract, rpy), move('transport_above_chip', above, rpy),
                            move('place_on_chip', drop, rpy), dict(stage='release_block', kind='open', side=side),
                            move('withdraw_vertically', above, rpy)]
                        seq, assumptions = segments(steps, state, station)
                        result = tools['plan_freespace_sequence'](seq)
                        config = dict(side=side, opening_axis_index=axis, height_fraction=fraction,
                            tilt_deg=tilt, site=site, release=drop, rpy=rpy,
                            object_base_in_native_grasp=local_base,
                            proposed_contacts_world=R.apply(cad['contact_points_native'])+site,
                            predicted_released_base=drop+R.apply(local_base),
                            supported_footprint_samples=int(supported.sum()), release_base_z=release_z,
                            block_overhang_m=np.maximum(g['dimensions']/2-target['radius'], 0))
                        log('NATIVE_CANDIDATE', dict(configuration=config, native_result=result))
                        if result.get('success') is True:
                            return plain(dict(steps=steps, geometry=dict(g, **config),
                                observation_only_repair=False, preview=result, preview_segments=seq,
                                measured_start_state=state, contact_geometry=cad, jaw_assumptions=assumptions,
                                planning_failures=failures, gripper_provenance=station['gripper_geometry'],
                                tool_pose_frame='aspire_grasp', extra_API_conversion_applied=False,
                                evidence_scope='Complete native plan only; runner owns execution and fresh parked verification'))
                        failures.append(plain(dict(configuration=config, native_result=result)))
                        if len(failures) >= 60:
                            raise RuntimeError('Native candidate limit reached: '+json.dumps(failures))
    raise RuntimeError('No complete native sequence: '+json.dumps(failures))


def evaluate(tools, task):
    evidence = dict(outcome='UNVERIFIED', diagnosis=
        'The prior parked block was near its saved target, but the visible blue-chip instance was elsewhere. This requires fresh target identity, not a relaxed consistency check.')
    try:
        current = measure(tools)
        g = task.get('geometry', {})
        target = current['target_measurement']
        checks = {}
        evidence.update(fresh_geometry=current, decision=task.get('decision'), checks=checks)
        before = g.get('capture_metadata', {}).get('captured_at')
        after = current['capture_metadata'].get('captured_at')
        checks['fresh_post_task_capture'] = bool(before is not None and after is not None and float(after) > float(before))
        if target is None or not target['reliable']:
            checks.update(centered_over_chip=None, chip_center_inside_block=None,
                bottom_at_chip=None, visible_chip_consistent=None)
            evidence['uncertainty'] = 'Missing or ill-conditioned fresh chip evidence remains unknown, including possible block occlusion.'
            return plain(dict(success=False, evidence=evidence))
        xy, axes, dims = np.asarray(current['source_xy']), np.asarray(current['axes']), np.asarray(current['dimensions'])
        center = np.asarray(target['xy'])
        error = float(np.linalg.norm(xy-center))
        bound = min(.012, .65*target['radius'])
        uncertainty = 3*target['center_sigma']
        checks['centered_over_chip'] = True if error+uncertainty < bound else (False if error-uncertainty >= bound else None)
        checks['chip_center_inside_block'] = bool(np.all(np.abs((center-xy)@axes) < dims/2))
        height = g.get('height')
        bottom_error = None if height is None else float(abs(z_at(current['top_plane'], xy)-height-z_at(target['plane'], xy)))
        checks['bottom_at_chip'] = None if bottom_error is None else bool(bottom_error < max(.006, 3*max(current['noise'], g.get('noise', 0), target['noise'])))
        previous_dims = g.get('dimensions')
        checks['dimensions_consistent'] = None if previous_dims is None else bool(np.all(np.abs(np.sort(dims)-np.sort(previous_dims)) < .012))
        reference = g.get('target_measurement')
        if reference is None and 'target_xy' in g:
            reference = dict(xy=g['target_xy'], radius=g['chip_radius'])
        reference_error = None if reference is None else float(np.linalg.norm(center-np.asarray(reference['xy'])))
        checks['visible_chip_consistent'] = None if reference is None else bool(reference_error < 1.5*reference['radius'])
        poly = boundary(footprint(xy, axes, dims))
        checks['clear_of_observed_occupants'] = None if current['context_unknown'] else bool(not any(polygon_overlap(poly, np.asarray(o)) for o in current['occupied'].values()))
        state = tools['get_planning_state']()
        separation = None
        side = g.get('side')
        if side is not None and height is not None and 'object_base_in_native_grasp' in g:
            arm = state['arms'][side]
            predicted = np.asarray(arm['ee_pos'])+Rotation.from_quat(arm['ee_quat']).apply(g['object_base_in_native_grasp'])
            separation = float(np.linalg.norm(predicted-np.r_[xy, z_at(current['top_plane'], xy)-height]))
            checks['withdrawn'] = bool(separation > max(g['dimensions']))
        else:
            checks['withdrawn'] = None
        success = all(value is True for value in checks.values())
        if success:
            outcome = 'GEOMETRIC_PLACEMENT_SUPPORTED'
        elif checks['centered_over_chip'] is False and checks['chip_center_inside_block'] is False:
            outcome = 'OBSERVED_BLOCK_SEPARATE_FROM_BLUE_CHIP'
        else:
            outcome = 'UNVERIFIED'
        evidence.update(outcome=outcome, center_error_m=error, centering_bound_m=bound,
            conditional_center_uncertainty_m=uncertainty, inferred_bottom_error_m=bottom_error,
            fresh_chip_to_reference_error_m=reference_error, prior_target_reference=reference,
            separation_m=separation, measured_state=state,
            limitations='RGB-D dimensions, CAD contact and hidden underside remain uncalibrated. Unknown evidence is not success; evaluator never commands retries.')
        return plain(dict(success=success, evidence=evidence))
    except Exception as exc:
        evidence['verification_error'] = str(exc)
        return plain(dict(success=False, evidence=evidence))
