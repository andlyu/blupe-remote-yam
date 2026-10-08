import json
import numpy as np
from scipy import ndimage
from scipy.spatial import ConvexHull
from scipy.spatial.transform import Rotation

Q = {
    'block': 'the thick green rectangular cuboid block, excluding the thin green rod',
    'chip': 'the DARK BLUE round poker chip with white rim markings on the right half of the white tabletop, below the teal cloth; exclude the turquoise GREEN chip on the left and the red chip; return only visible pixels of this dark blue chip, including white markings, and no substitute if occluded',
    'source_cloth': 'visible TEAL CLOTH immediately surrounding the thick green cuboid at its current location, if the cuboid rests on that cloth; exclude the cuboid, thin green rod, white tabletop and robot parts; return empty if no cloth immediately surrounds the cuboid',
    'source_table': 'visible WHITE TABLETOP immediately surrounding the thick green cuboid at its current location, if the cuboid rests directly on the white tabletop; exclude the cuboid, all chips, thin green rod, teal cloth and robot parts; return empty if the cuboid is surrounded by cloth instead',
    'target': 'visible WHITE tabletop immediately surrounding the DARK BLUE round poker chip on the right half of the table below the teal cloth; select the local destination surface, excluding every chip, cuboid, rod, cloth and robot parts'
}


def plain(x):
    if isinstance(x, np.ndarray): return x.tolist()
    if isinstance(x, np.generic): return x.item()
    if isinstance(x, dict): return {k: plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)): return [plain(v) for v in x]
    return x


def log(name, value):
    print(name + ' ' + json.dumps(plain(value), default=str))


def observe(tools):
    f = tools['get_camera_rgbd']('top')
    d = np.asarray(f['depth_m'], float)
    v, u = np.indices(d.shape)
    K = np.asarray(f['K'], float)
    T = np.asarray(f['T_world_camera'], float)
    rays = np.stack((u, v, np.ones_like(u)), -1) @ np.linalg.inv(K).T
    p = (rays*d[..., None]) @ T[:3, :3].T + T[:3, 3]
    return f, p, (d > 0) & np.isfinite(d) & np.isfinite(p).all(-1)


def segment(tools, f, key, ev, optional=False):
    r = tools['segment_camera_rgb'](f['rgb'], Q[key])
    m = np.asarray(r['mask'], bool)
    if m.shape != np.asarray(f['depth_m']).shape:
        raise RuntimeError('Mask shape mismatch: ' + key)
    if key in ('block', 'chip') and m.any():
        labels, _ = ndimage.label(m)
        counts = np.bincount(labels.ravel())
        counts[0] = 0
        m = labels == counts.argmax()
    ev.append({'name': key, 'pixels': int(m.sum()), 'proposal': {k: v for k, v in r.items() if k != 'mask'}})
    if not m.any() and not optional:
        raise RuntimeError('Missing observed object: ' + key)
    return m


def points(p, valid, m):
    for region in (ndimage.binary_erosion(m), m):
        a = p[valid & region]
        if len(a) >= 12: return a
    raise RuntimeError('Insufficient measured depth in selected mask')


def fit_plane(p):
    A = np.c_[p[:, :2], np.ones(len(p))]
    keep = np.ones(len(p), bool)
    for _ in range(6):
        c, _, rank, _ = np.linalg.lstsq(A[keep], p[keep, 2], rcond=None)
        if rank < 3: raise RuntimeError('Unresolved measured plane')
        r = p[:, 2] - A@c
        noise = max(0.0005, float(1.4826*np.median(np.abs(r[keep]-np.median(r[keep])))))
        keep = np.abs(r) <= 3*noise
        if keep.sum() < 12: raise RuntimeError('Insufficient plane inliers')
    return c, noise


def z_at(c, xy):
    return float(np.asarray(xy)@np.asarray(c)[:2]+c[2])


def boundary(p):
    return p[ConvexHull(p[:, :2]).vertices, :2]


def support(p, valid, surface, obj):
    for radius in (25, 45, 75):
        m = surface & ndimage.binary_dilation(obj, iterations=radius) & ~ndimage.binary_dilation(obj, iterations=3)
        if (m & valid).sum() >= 12:
            a = points(p, valid, m)
            c, noise = fit_plane(a)
            return c, noise, boundary(a)
    raise RuntimeError('Selected local support lacks measured depth')


def block_geometry(bp, sp):
    h = bp[:, 2]-bp[:, :2]@np.asarray(sp)[:2]-sp[2]
    plane, noise = fit_plane(bp[h >= np.percentile(h, 55)])
    residual = bp[:, 2]-bp[:, :2]@plane[:2]-plane[2]
    face = bp[np.abs(residual) <= 3*noise]
    _, axes = np.linalg.eigh(np.cov(face[:, :2].T))
    axes = axes[:, ::-1]
    lo, hi = np.percentile(face[:, :2]@axes, [1, 99], axis=0)
    xy = ((lo+hi)/2)@axes.T
    dims = hi-lo
    base, top = z_at(sp, xy), z_at(plane, xy)
    if top <= base or np.any(dims <= 0):
        raise RuntimeError('Unresolved cuboid dimensions')
    return xy, axes, dims, base, top, noise


def chip_geometry(p, valid, cm, bm):
    cp = points(p, valid, cm)
    plane, noise = fit_plane(cp)
    rim = cm & ~ndimage.binary_erosion(cm) & ~ndimage.binary_dilation(bm, iterations=3)
    xy = p[valid & rim][:, :2]
    if len(xy) < 8: raise RuntimeError('Insufficient visible chip rim')
    origin = xy.mean(0)
    q = xy-origin
    A, b = np.c_[2*q, np.ones(len(q))], (q*q).sum(1)
    keep = np.ones(len(q), bool)
    for _ in range(5):
        sol, _, rank, _ = np.linalg.lstsq(A[keep], b[keep], rcond=None)
        if rank < 3: raise RuntimeError('Chip arc does not determine a circle')
        center = origin+sol[:2]
        radius = float(np.sqrt(max(0, sol[2]+sol[:2]@sol[:2])))
        r = np.linalg.norm(xy-center, axis=1)-radius
        sigma = max(0.0005, float(1.4826*np.median(np.abs(r[keep]-np.median(r[keep])))))
        keep = np.abs(r) <= 3*sigma
        if keep.sum() < 8: raise RuntimeError('Unresolved chip rim')
    return center, radius, plane, max(noise, sigma), boundary(cp)


def measure(tools):
    ev = []
    f, p, valid = observe(tools)
    masks = {k: segment(tools, f, k, ev, k.startswith('source_')) for k in Q}
    distance = ndimage.distance_transform_edt(~masks['block'])
    scores = {}
    for key in ('source_cloth', 'source_table'):
        adjacent = masks[key] & ~masks['chip'] & (distance > 3) & (distance <= 15)
        scores[key] = float(np.sum(1/distance[adjacent]))
    source = max(scores, key=scores.get)
    if scores[source] == 0:
        raise RuntimeError('Neither support proposal surrounds the current block')
    sp, ns, sb = support(p, valid, masks[source] & ~masks['chip'], masks['block'])
    tp, nt, tb = support(p, valid, masks['target'] & ~masks['block'], masks['chip'])
    xy, axes, dims, base, top, nb = block_geometry(points(p, valid, masks['block']), sp)
    target, radius, cp, nc, cb = chip_geometry(p, valid, masks['chip'], masks['block'])
    g = dict(source_xy=xy, axes=axes, dimensions=dims, base=base, top=top,
             height=top-base, target_xy=target, chip_radius=radius,
             chip_plane=cp, chip_z=z_at(cp, target), noise=max(ns, nt, nb, nc),
             source_plane=sp, table_plane=tp, source_support_kind=source,
             source_support_scores=scores, source_support_boundary_xy=sb,
             target_support_boundary_xy=tb, visible_chip_boundary_xy=cb,
             capture_metadata=f.get('metadata', {}), perception_evidence=ev)
    log('MEASURED_GEOMETRY', g)
    return g


def display(rot):
    x, y, z = rot.as_euler('xyz', degrees=True)
    return ((np.array([y, -x, -z-90])+180)%360-180).tolist()


def rotation(rpy):
    # This is already R_world_aspire, as are measured tool quaternions.
    return Rotation.from_euler('xyz', [-rpy[1], rpy[0], -rpy[2]-90], degrees=True)


def contact(station, side, width):
    tips = {m['mesh']: m for m in station['planner_validation']['native_gripper_meshes']['arms'][side]}
    centers, slides, facets = [], [], []
    for name in ('tip_left', 'tip_right'):
        m = tips[name]
        facet = max(m['opening_axis_planar_facets'], key=lambda f: f['max_grasp_m'][2])
        facets.append(facet)
        centers.append((np.asarray(facet['min_grasp_m'])+np.asarray(facet['max_grasp_m']))/2)
        slides.append(np.asarray(m['aperture']['open']['center_grasp_m'])-np.asarray(m['aperture']['closed']['center_grasp_m']))
    centers, slides = np.asarray(centers), np.asarray(slides)
    gap = centers[1, 0]-centers[0, 0]
    travel = slides[1, 0]-slides[0, 0]
    jaw = float((width-gap)/travel)
    contacts = centers+jaw*slides
    return contacts.mean(0), dict(facets_closed_native=facets,
        slide_vectors_native_m=slides, contact_points_native=contacts,
        predicted_enclosing_jaw_fraction=jaw, closed_gap_m=gap,
        open_gap_m=gap+travel, width_m=width,
        physical_contact_calibration_measured=False)


def segments(steps, state, station):
    jaws, assumptions = {}, []
    for side in ('left', 'right'):
        value = state['arms'][side].get('gripper_pos')
        if value is None:
            if station.get('physical_motion_enabled', False):
                raise RuntimeError('Missing measured jaw: '+side)
            value = 1.0
        jaws[side] = float(np.asarray(value).reshape(-1)[0])
        if not station.get('physical_motion_enabled', False):
            assumptions.append(side+': offline jaw geometry, not contact evidence')
    out = []
    for step in steps:
        if step['kind'] in ('open', 'close'):
            jaws[step['side']] = float(step['kind'] == 'open')
        else:
            for side in jaws:
                key = side+'_gripper_target_width'
                if key in step['arguments']: jaws[side] = float(step['arguments'][key])
            out.append(dict(stage=step['stage'], arguments=step['arguments'], gripper_state=dict(jaws)))
    return out, assumptions


def endpoint_diagnostics(result, steps, side, cad, desired_opening):
    # Read native returned endpoint poses when exposed. Never substitute a
    # requested pose or fabricate FK from a successful planning status.
    requested = {s['stage']: s['arguments'] for s in steps if s['kind'] == 'move'}
    records = []
    def visit(node, path='', stage=None, arm_context=None):
        if isinstance(node, dict):
            candidate_stage = node.get('stage')
            if isinstance(candidate_stage, str) and candidate_stage in requested:
                stage = candidate_stage
            if arm_context == side and 'ee_pos' in node and 'ee_quat' in node:
                try:
                    pos = np.asarray(node['ee_pos'], float).reshape(3)
                    rot = Rotation.from_quat(np.asarray(node['ee_quat'], float).reshape(4))
                    contacts = rot.apply(np.asarray(cad['contact_points_native']))+pos
                    rec = dict(path=path, stage=stage, native_endpoint_position=pos,
                               native_opening_world=rot.as_matrix()[:, 0],
                               enclosing_contacts_world=contacts,
                               opening_alignment_abs=float(abs(rot.as_matrix()[:, 0]@desired_opening)),
                               contact_aperture_basis='CAD predicted enclosing aperture; not measured held-object contact')
                    if stage in requested:
                        a = requested[stage]
                        want = rotation(a[side+'_target_rpy'])
                        rec['rotation_error_deg'] = float((want.inv()*rot).magnitude()*180/np.pi)
                        rec['position_error_m'] = float(np.linalg.norm(pos-np.asarray(a[side+'_target_pos'])))
                        rec['contacts_vs_requested_max_error_m'] = float(np.max(np.linalg.norm(contacts-(want.apply(cad['contact_points_native'])+np.asarray(a[side+'_target_pos'])), axis=1)))
                    records.append(rec)
                except (ValueError, TypeError) as exc:
                    records.append(dict(path=path, diagnostic_parse_error=str(exc)))
            for k, v in node.items():
                visit(v, path+'/'+str(k), stage, k if k in ('left', 'right') else arm_context)
        elif isinstance(node, (list, tuple)):
            for i, v in enumerate(node): visit(v, path+'/'+str(i), stage, arm_context)
    visit(result)
    return dict(records=records, status='RETURNED_NATIVE_POSES_INSPECTED' if records else 'ENDPOINT_FK_NOT_EXPOSED_IN_RETURN_VALUE',
                scope='Diagnostic only; original native result retained without additional admission cutoffs')


def build_task(tools):
    station = tools['get_station_info']()
    state = tools['get_planning_state']()
    g = measure(tools)
    opening = np.r_[g['axes'][:, np.argmin(g['dimensions'])], 0.0]
    down = np.array([0.0, 0.0, -1.0])
    native = np.column_stack((opening, np.cross(down, opening), down))
    # No API_TO_ASPIRE_GRASP multiplication: every tool pose and CAD point
    # below already shares the native aspire_grasp frame.
    yaw = display(Rotation.from_matrix(native))[2]
    failures = []
    for side in ('right', 'left'):
        idle = 'left' if side == 'right' else 'right'
        midpoint, cad = contact(station, side, float(min(g['dimensions'])))
        start = np.asarray(state['arms'][side]['ee_pos'], float)
        sr = Rotation.from_quat(state['arms'][side]['ee_quat'])
        idle_rpy = display(Rotation.from_quat(state['arms'][idle]['ee_quat']))
        for fraction in (0.65, 0.50, 0.70):
            detection = {'position_3d': np.r_[g['source_xy'], g['base']+fraction*g['height']].tolist(), 'score': 1.0}
            grasps = tools['sample_topdown_geometric'](
                'green block', detection=detection, camera='top',
                yaws=[(yaw+360)%360-180, yaw], pitches=[180.0],
                z_offsets=[0.0], width=float(cad['open_gap_m']), max_grasps=2)
            for tilt in (30.0, -30.0, 0.0, 15.0, -15.0):
                for grasp in grasps:
                    # Local native X rotation preserves the actual opening axis.
                    R = rotation(grasp.rpy)*Rotation.from_euler('x', tilt, degrees=True)
                    rpy = display(R)
                    site = np.asarray(grasp.position, float)-R.apply(midpoint)
                    base_world = np.r_[g['source_xy'], g['base']]
                    local_base = R.inv().apply(base_world-site)
                    offset = -R.apply(local_base)
                    target_base = np.r_[g['target_xy'], g['chip_z']]
                    drop = np.asarray(tools['estimate_drop'](
                        'blue poker chip', detection={'position_3d': target_base.tolist()},
                        camera='top', z_offset=float(offset[2])), float)
                    drop[:2] += offset[:2]
                    for scale in (0.75, 1.25):
                        approach = site+np.array([0, 0, scale*g['height']])
                        lift = site+np.array([0, 0, g['height']])
                        retract = lift.copy()
                        retract[:2] += 0.35*(drop[:2]-site[:2])
                        transfer_z = max(g['top'], lift[2], drop[2])+g['height']
                        raised = np.r_[retract[:2], transfer_z]
                        above = np.r_[drop[:2], transfer_z]
                        stage = 0.5*(start+approach)
                        stage[2] = max(start[2], approach[2])
                        def move(name, pos, orient):
                            return dict(stage=name, kind='move', arguments={
                                side+'_target_pos': np.asarray(pos).tolist(),
                                side+'_target_rpy': list(orient),
                                idle+'_target_pos': list(state['arms'][idle]['ee_pos']),
                                idle+'_target_rpy': idle_rpy, 'planning_speed': 0.5})
                        half = sr*Rotation.from_rotvec(0.5*(sr.inv()*R).as_rotvec())
                        steps = [
                            dict(stage='open_for_center_grasp', kind='open', side=side),
                            move('stage_near_arm', stage, display(half)),
                            move('orient_before_extension', stage, rpy),
                            move('approach_above_block', approach, rpy),
                            move('descend_to_center_grasp', site, rpy),
                            dict(stage='close_around_block', kind='close', side=side),
                            move('lift_block', lift, rpy),
                            move('retract_holding_block', retract, rpy),
                            move('raise_in_transfer_lane', raised, rpy),
                            move('transport_above_chip', above, rpy),
                            move('place_on_chip', drop, rpy),
                            dict(stage='release_block', kind='open', side=side),
                            move('withdraw_from_block', above, rpy)]
                        contacts_world = R.apply(cad['contact_points_native'])+site
                        diag = dict(side=side, tilt_deg=tilt, tilt_axis='native X',
                            height_fraction=fraction, approach_scale=scale,
                            site=site, release=drop, rpy=rpy,
                            native_opening_world=R.as_matrix()[:, 0],
                            opening_alignment_abs=float(abs(R.as_matrix()[:, 0]@opening)),
                            proposed_contacts_world=contacts_world,
                            contacts_in_block_planar_axes=(contacts_world[:, :2]-g['source_xy'])@g['axes'],
                            object_base_in_native_grasp=local_base,
                            predicted_released_object_base=drop+R.apply(local_base), cad=cad)
                        seq, assumptions = segments(steps, state, station)
                        result = tools['plan_freespace_sequence'](seq)
                        fk = endpoint_diagnostics(result, steps, side, cad, opening)
                        log('NATIVE_CANDIDATE', dict(configuration=diag, native_result=result, endpoint_diagnostics=fk))
                        if result.get('success') is True:
                            geometry = dict(g, side=side,
                                object_base_in_native_grasp=local_base,
                                configuration=diag, contact_geometry=cad,
                                jaw_assumptions=assumptions,
                                gripper_provenance=station['gripper_geometry'],
                                tool_pose_frame='aspire_grasp',
                                extra_API_conversion_applied=False)
                            return plain(dict(steps=steps, geometry=geometry,
                                preview=result, preview_segments=seq,
                                endpoint_diagnostics=fk, measured_start_state=state,
                                planning_failures=failures, queries=Q,
                                evidence_scope='Native preview only; final perception must establish physical completion'))
                        failures.append(plain(dict(configuration=diag, native_result=result, endpoint_diagnostics=fk)))
    log('ALL_NATIVE_FAILURES', failures)
    raise RuntimeError('No complete native sequence: '+json.dumps([
        dict(side=f['configuration']['side'], tilt=f['configuration']['tilt_deg'],
             fraction=f['configuration']['height_fraction'], rpy=f['configuration']['rpy'],
             failing_stage=f['native_result'].get('failing_stage'),
             reason=f['native_result'].get('reason')) for f in failures]))


def evaluate(tools, task):
    ev = []
    try:
        station = tools['get_station_info']()
        f, p, valid = observe(tools)
        g = task['geometry']
        bm = segment(tools, f, 'block', ev)
        cm = segment(tools, f, 'chip', ev, optional=True)
        xy, axes, dims, _, top, noise = block_geometry(points(p, valid, bm), np.asarray(g['chip_plane']))
        target = np.asarray(g['target_xy'])
        center_error = float(np.linalg.norm(xy-target))
        bottom_error = float(abs(top-g['height']-g['chip_z']))
        visible = p[valid & cm]
        chip_ok = True
        if len(visible):
            distance = float(np.median(np.linalg.norm(visible[:, :2]-target, axis=1)))
            chip_ok = distance < 1.5*g['chip_radius']
            ev.append(dict(visible_chip_reference_distance_m=distance))
        state = tools['get_planning_state']()
        arm = state['arms'][g['side']]
        # The measured quaternion and stored object transform are native.
        predicted_base = np.asarray(arm['ee_pos'])+Rotation.from_quat(arm['ee_quat']).apply(g['object_base_in_native_grasp'])
        separation = float(np.linalg.norm(predicted_base-np.r_[xy, top-g['height']]))
        jaw = arm.get('gripper_pos') if station.get('physical_motion_enabled', False) else None
        jaw = None if jaw is None else float(np.asarray(jaw).reshape(-1)[0])
        checks = dict(
            centered_over_chip=center_error < min(0.012, 0.65*g['chip_radius']),
            chip_center_inside_block=bool(np.all(np.abs((target-xy)@axes) < dims/2)),
            bottom_at_chip=bottom_error < max(0.006, 3*max(noise, g['noise'])),
            dimensions_consistent=bool(np.all(np.abs(np.sort(dims)-np.sort(g['dimensions'])) < 0.012)),
            visible_chip_consistent=bool(chip_ok),
            withdrawn=separation > max(g['dimensions']),
            released=bool((jaw is not None and jaw > 0.8) or
                (not station.get('physical_motion_enabled', False) and separation > max(g['dimensions']))))
        return plain(dict(success=bool(all(checks.values())), evidence=dict(
            checks=checks, block_xy=xy, target_xy=target,
            center_error_m=center_error, inferred_bottom_error_m=bottom_error,
            grasp_to_object_separation_m=separation, measured_jaw=jaw,
            capture=f.get('metadata', {}), perception=ev,
            hidden_chip_basis='Pre-placement measured circle; hidden location remains inferred',
            release_basis='Measured jaw or spatially separated parked gripper and supported block; offline jaw assumptions excluded')))
    except Exception as exc:
        return plain(dict(success=False, evidence=dict(verification_error=str(exc), perception=ev)))
