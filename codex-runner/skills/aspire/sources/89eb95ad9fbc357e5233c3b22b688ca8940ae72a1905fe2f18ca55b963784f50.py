import json
import numpy as np
from scipy import ndimage
from scipy.spatial import ConvexHull, cKDTree
from scipy.spatial.transform import Rotation

Q = {
    'block': 'black rectangular block',
    'occupied_green': 'green rectangular block',
    'source_table': 'white tabletop',
    'towel': 'large teal fabric towel'
}



def plain(x):
    if isinstance(x, np.ndarray): return x.tolist()
    if isinstance(x, np.generic): return x.item()
    if isinstance(x, dict): return {k:plain(v) for k,v in x.items()}
    if isinstance(x, (tuple, list)): return [plain(v) for v in x]
    return x


def log(name, value):
    print(name+' '+json.dumps(plain(value), default=str), flush=True)


def observe(tools):
    f = tools['get_camera_rgbd']('top')
    d = np.asarray(f['depth_m'], float)
    K, T = np.asarray(f['K'], float), np.asarray(f['T_world_camera'], float)
    coeffs = f.get('metadata', {}).get('factory_color_intrinsics', {}).get('coeffs', [0]*5)
    if np.any(np.asarray(coeffs, float)):
        raise RuntimeError('Top capture requires published zero-distortion intrinsics')
    v, u = np.indices(d.shape)
    rays = np.stack((u, v, np.ones_like(u)), -1)@np.linalg.inv(K).T
    p = (rays*d[..., None])@T[:3, :3].T+T[:3, 3]
    return f, p, (d > 0)&np.isfinite(d)&np.isfinite(p).all(-1)


def segment(tools, f, key, ev, optional=False):
    r = tools['segment_camera_rgb'](f['rgb'], Q[key])
    m = np.asarray(r['mask'], bool)
    if m.shape != np.asarray(f['depth_m']).shape:
        raise RuntimeError('Mask shape mismatch: '+key)
    if key == 'block' and m.any():
        labels, _ = ndimage.label(m)
        counts = np.bincount(labels.ravel()); counts[0] = 0
        m = labels == counts.argmax()
    rgb = np.asarray(f['rgb'])
    pixels = rgb[m]
    ev.append(dict(name=key, query=Q[key], pixels=int(m.sum()),
        rgb_median=np.median(pixels, axis=0) if len(pixels) else None,
        proposal={k:v for k,v in r.items() if k != 'mask'},
        interpretation='SAM3 instance proposal; geometric faces are derived from paired depth'))
    if not m.any() and not optional:
        raise RuntimeError('Missing mask: '+key)
    return m


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
        r = p[:, 2]-A@c
        noise = max(.0005, float(1.4826*np.median(np.abs(r[keep]-np.median(r[keep])))))
        new = np.abs(r-np.median(r[keep])) <= 3*noise
        if new.sum() < 12: break
        keep = new
    c, _, rank, _ = np.linalg.lstsq(A[keep], p[keep, 2], rcond=None)
    if rank < 3: raise RuntimeError('Unresolved final plane rank')
    c[2] -= origin@c[:2]
    return c, noise


def z_at(c, xy):
    return np.asarray(xy)@np.asarray(c)[:2]+c[2]


def boundary(a):
    return a[ConvexHull(a[:, :2]).vertices, :2]


def upward_face(bp, support, depth_scale):
    # Flat upward face: its normal follows the measured nearby support.
    # A whole-instance mask includes vertical sides. A free plane refit
    # previously selected those mixed surfaces and invented a steep bottom.
    bp = np.asarray(bp, float)
    heights = bp[:, 2]-z_at(support, bp[:, :2])
    upper = np.sort(heights[heights >= np.median(heights)])
    if len(upper) < 12: raise RuntimeError('Insufficient upper block depth')
    tolerance = max(.0015, 2.5*float(depth_scale))
    ends = np.searchsorted(upper, upper+2*tolerance, side='right')
    counts = ends-np.arange(len(upper))
    start = int(np.argmax(counts))
    layer = upper[start:ends[start]]
    offset = float(np.median(layer))
    keep = np.abs(heights-offset) <= tolerance
    if keep.sum() < 12: raise RuntimeError('Unresolved current upward surface')
    face = bp[keep]
    plane = np.asarray(support, float).copy()
    plane[2] += float(np.median(heights[keep]))
    residual = face[:, 2]-z_at(plane, face[:, :2])
    noise = max(.0005, float(1.4826*np.median(np.abs(residual-np.median(residual)))))
    # The upper layer determines height, but its partial noisy sampling
    # must not move the box center toward one edge. Use the full current
    # instance footprint for XY center/yaw; all faces share the box footprint.
    _, axes = np.linalg.eigh(np.cov(bp[:, :2].T))
    axes = axes[:, ::-1]
    lo, hi = np.percentile(bp[:, :2]@axes, [1, 99], axis=0)
    xy = ((lo+hi)/2)@axes.T
    observed_dims = hi-lo
    # This is a different object: all dimensions come from this capture.
    dims = observed_dims.copy()
    if not np.isfinite(xy).all() or not np.isfinite(observed_dims).all() or np.any(observed_dims <= 0):
        raise RuntimeError('Invalid current upward-face geometry')
    diag = dict(method='Measured support normal with dominant upper depth layer',
        instance_samples=len(bp), face_samples=len(face),
        inlier_tolerance_m=tolerance, plane=plane, noise_m=noise,
        face_boundary_xy=boundary(face), observed_face_dimensions_m=observed_dims,
        measured_dimensions_m=dims, dimension_provenance='Current paired RGB-D instance footprint',
        instance_height_quantiles_m=np.quantile(heights, [.05, .25, .5, .75, .95]),
        residual_quantiles_m=np.quantile(bp[:, 2]-z_at(plane, bp[:, :2]), [.05, .5, .95]),
        normal_basis='Fresh measured tabletop; flat supported block, no side-face refit')
    return xy, axes, dims, plane, noise, diag


def footprint(xy, axes, dims, n=9):
    a, b = np.meshgrid(np.linspace(-.5, .5, n), np.linspace(-.5, .5, n))
    return np.asarray(xy)+(np.c_[a.ravel(), b.ravel()]*dims)@axes.T


def project_surface(f, xy, plane):
    xyz = np.c_[xy, z_at(plane, xy)]
    T, K = np.asarray(f['T_world_camera']), np.asarray(f['K'])
    cam = (xyz-T[:3, 3])@T[:3, :3]
    uv = cam@K.T
    return np.rint(uv[:, :2]/uv[:, 2:3]).astype(int)


def covered(f, mask, xy, plane):
    uv = project_surface(f, xy, plane)
    h, w = mask.shape
    good = (uv[:, 0]>=0)&(uv[:, 0]<w)&(uv[:, 1]>=0)&(uv[:, 1]<h)
    out = np.zeros(len(xy), bool)
    out[good] = mask[uv[good, 1], uv[good, 0]]
    return out


def depth_scale(f):
    return float(f.get('metadata', {}).get('depth_scale_m', .001))


def polygon_overlap(a, b):
    # Separating-axis test for measured convex footprints, with no added gap.
    a, b = np.asarray(a, float), np.asarray(b, float)
    for poly in (a, b):
        edges = np.roll(poly, -1, axis=0)-poly
        for edge in edges:
            axis = np.array([-edge[1], edge[0]])
            pa, pb = a@axis, b@axis
            if pa.max() < pb.min() or pb.max() < pa.min(): return False
    return True


def measure(tools):
    ev = []
    f, p, valid = observe(tools)
    masks = {k:segment(tools, f, k, ev, optional=k == 'occupied_green') for k in Q}
    # Remove instance overlaps before measuring local table or towel surfaces.
    bm = masks['block']
    cloth = masks['towel'] & ~bm & ~masks['occupied_green']
    table_mask = masks['source_table'] & ~ndimage.binary_dilation(bm, iterations=2) & ~cloth
    bp = points(p, valid, bm)
    center0 = np.median(bp[:, :2], axis=0)
    extent0 = np.linalg.norm(np.ptp(bp[:, :2], axis=0))
    local = np.linalg.norm(p[:, :, :2]-center0, axis=-1) < 2*extent0
    table_points = points(p, valid, table_mask & local)
    table, ns = fit_plane(table_points)
    xy, axes, dims, top_plane, nb, face_ev = upward_face(bp, table, depth_scale(f))
    top = float(z_at(top_plane, xy))
    # The black block rests on the visible tabletop in this scene. Use the
    # freshly measured local surface; no semantic support identity is needed.
    source = table.copy()
    base = float(z_at(source, xy))
    height = top-base
    if not np.isfinite(height) or height <= 0:
        raise RuntimeError('Nonpositive measured black-block thickness')
    height_provenance = dict(method='Current upper depth layer minus current local tabletop',
        height_m=height, camera='top', calibration_id=f.get('metadata', {}).get('calibration_id'),
        capture=f.get('metadata', {}), physical_metrology=False,
        transferred_object_dimensions=False)
    occupied = []
    if masks['occupied_green'].any():
        occupied_points = points(p, valid, masks['occupied_green'])
        occupied.append(boundary(occupied_points))
    diag = dict(source_xy=xy, dimensions=dims, source_plane=source,
        table_plane=table, top_plane=top_plane, base=base, top=top, height=height,
        source_kind='directly_measured_local_tabletop',
        height_provenance=height_provenance, face_evidence=face_ev,
        local_support_evidence=dict(samples=len(table_points), plane=table, noise_m=ns),
        occupied_object_footprints_xy=occupied,
        support_inference='Fresh local tabletop depth, no previous object dimensions or support labels')
    log('BLOCK_SUPPORT_DIAGNOSTICS', diag)
    tp = points(p, valid, cloth)
    towel_plane, nt = fit_plane(tp)
    interior = ndimage.distance_transform_edt(cloth)
    candidates = np.argwhere(valid & cloth)
    order = np.argsort(interior[candidates[:, 0], candidates[:, 1]])[::-1]
    targets = []
    for idx in order[::max(1, len(order)//400)]:
        v, u = candidates[idx]
        target = p[v, u, :2]
        fp = footprint(target, axes, dims)
        if not covered(f, cloth, fp, towel_plane).all(): continue
        if any(polygon_overlap(boundary(fp), obstacle) for obstacle in occupied): continue
        if any(np.linalg.norm(target-t['xy']) < min(dims) for t in targets): continue
        patch = tp[np.linalg.norm(tp[:, :2]-target, axis=1) < np.linalg.norm(dims)]
        if len(patch) < 12: continue
        plane, noise = fit_plane(patch)
        if not covered(f, cloth, fp, plane).all(): continue
        relative_bottom = z_at(source, xy+fp-target)-base
        target_z = float(np.max(z_at(plane, fp)-relative_bottom))
        targets.append(dict(xy=target, z=target_z, plane=plane, noise=noise,
            footprint_xy=fp, coverage=1.0,
            support_basis='Fresh local cloth depth; bottom-face contact without an added release gap'))
        if len(targets) == 3: break
    if not targets: raise RuntimeError('No measured towel interior contains block footprint')
    g = dict(diag, axes=axes, noise=max(nb, ns, nt), targets=targets,
        target_identity='large teal fabric towel',
        towel_boundary_xy=boundary(tp), source_support_boundary_xy=boundary(table_points),
        capture_metadata=f.get('metadata', {}), perception_evidence=ev,
        towel_reference=dict(mask=cloth, K=f['K'], T_world_camera=f['T_world_camera'], plane=towel_plane))
    log('MEASURED_GEOMETRY', {k:v for k,v in g.items() if k != 'towel_reference'})
    return g


def display(rot):
    x, y, z = rot.as_euler('xyz', degrees=True)
    return ((np.array([y, -x, -z-90])+180)%360-180).tolist()


def rotation(rpy):
    return Rotation.from_euler('xyz', [-rpy[1], rpy[0], -rpy[2]-90], degrees=True)


def contact(station, side, width):
    # Retained native-frame CAD contact construction; no extra API rotation.
    tips = {m['mesh']:m for m in station['planner_validation']['native_gripper_meshes']['arms'][side]}
    centers, slides, facets = [], [], []
    for name in ('tip_left', 'tip_right'):
        m = tips[name]
        facet = max(m['opening_axis_planar_facets'], key=lambda f:f['max_grasp_m'][2])
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
        predicted_enclosing_jaw_fraction=jaw, open_gap_m=gap+travel,
        physical_contact_calibration_measured=False)


def segments(steps, state, station):
    jaws, assumptions = {}, []
    for side in ('left', 'right'):
        value = state['arms'][side].get('gripper_pos')
        if value is None:
            if station.get('physical_motion_enabled', False): raise RuntimeError('Missing measured jaw: '+side)
            value = 1.0
            assumptions.append(side+': assumed open for offline preview only')
        jaws[side] = float(np.asarray(value).reshape(-1)[0])
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


def build_task(tools):
    station, state = tools['get_station_info'](), tools['get_planning_state']()
    g = measure(tools)
    opening = np.r_[g['axes'][:, np.argmin(g['dimensions'])], 0.0]
    down = np.array([0., 0., -1.])
    native = np.column_stack((opening, np.cross(down, opening), down))
    yaw = display(Rotation.from_matrix(native))[2]
    failures = []
    for fraction in (.65, .50, .70):
        for tilt in (30., -30., 0., 15., -15.):
            for side in ('right', 'left'):
                idle = 'left' if side == 'right' else 'right'
                midpoint, cad = contact(station, side, float(min(g['dimensions'])))
                start = np.asarray(state['arms'][side]['ee_pos'], float)
                sr = Rotation.from_quat(state['arms'][side]['ee_quat'])
                idle_rpy = display(Rotation.from_quat(state['arms'][idle]['ee_quat']))
                detection = {'position_3d':np.r_[g['source_xy'], g['base']+fraction*g['height']].tolist(), 'score':1.0}
                grasps = tools['sample_topdown_geometric']('black block', detection=detection,
                    camera='top', yaws=[(yaw+360)%360-180, yaw], pitches=[180.],
                    z_offsets=[0.], width=float(cad['open_gap_m']), max_grasps=2)
                for grasp in grasps:
                    R = rotation(grasp.rpy)*Rotation.from_euler('x', tilt, degrees=True)
                    rpy = display(R)
                    site = np.asarray(grasp.position)-R.apply(midpoint)
                    local_base = R.inv().apply(np.r_[g['source_xy'], g['base']]-site)
                    for target in g['targets']:
                        offset = -R.apply(local_base)
                        drop = np.asarray(tools['estimate_drop']('green towel',
                            detection={'position_3d':np.r_[target['xy'], target['z']].tolist()},
                            z_offset=float(offset[2]), camera='top'), float)
                        drop[:2] += offset[:2]
                        approach = site+[0, 0, .75*g['height']]
                        lift = site+[0, 0, g['height']]
                        retract = lift.copy(); retract[:2] += .35*(drop[:2]-site[:2])
                        transfer_z = max(g['top'], lift[2], drop[2])+g['height']
                        raised, above = np.r_[retract[:2], transfer_z], np.r_[drop[:2], transfer_z]
                        stage = .5*(start+approach); stage[2] = max(start[2], approach[2])
                        half = sr*Rotation.from_rotvec(.5*(sr.inv()*R).as_rotvec())
                        def move(name, pos, orient):
                            return dict(stage=name, kind='move', arguments={
                                side+'_target_pos':np.asarray(pos).tolist(), side+'_target_rpy':list(orient),
                                idle+'_target_pos':list(state['arms'][idle]['ee_pos']),
                                idle+'_target_rpy':idle_rpy, 'planning_speed':.5})
                        steps = [dict(stage='open_for_grasp', kind='open', side=side),
                            move('stage_near_arm', stage, display(half)),
                            move('orient_before_extension', stage, rpy),
                            move('approach_block', approach, rpy),
                            move('grasp_block', site, rpy),
                            dict(stage='close_on_block', kind='close', side=side),
                            move('lift_block', lift, rpy),
                            move('retract_holding_block', retract, rpy),
                            move('raise_in_transfer_lane', raised, rpy),
                            move('transport_above_towel', above, rpy),
                            move('place_on_towel', drop, rpy),
                            dict(stage='release_block', kind='open', side=side),
                            move('withdraw_vertically', above, rpy)]
                        seq, assumptions = segments(steps, state, station)
                        result = tools['plan_freespace_sequence'](seq)
                        config = dict(side=side, tilt_deg=tilt, height_fraction=fraction,
                            site=site, release=drop, rpy=rpy, target=target,
                            native_opening_world=R.as_matrix()[:, 0],
                            object_base_in_native_grasp=local_base,
                            proposed_contacts_world=R.apply(cad['contact_points_native'])+site,
                            predicted_released_base=drop+R.apply(local_base))
                        log('NATIVE_CANDIDATE', dict(configuration=config, native_result=result))
                        if result.get('success') is True:
                            return plain(dict(steps=steps, geometry=dict(g, **config),
                                preview=result, preview_segments=seq, measured_start_state=state,
                                contact_geometry=cad, jaw_assumptions=assumptions,
                                planning_failures=failures, gripper_provenance=station['gripper_geometry'],
                                tool_pose_frame='aspire_grasp', extra_API_conversion_applied=False,
                                evidence_scope='Full native preview; physical success requires final perception'))
                        failures.append(plain(dict(configuration=config, native_result=result)))
    log('ALL_NATIVE_FAILURES', failures)
    raise RuntimeError('No complete native sequence: '+json.dumps([
        dict(side=f['configuration']['side'], tilt=f['configuration']['tilt_deg'],
             failing_stage=f['native_result'].get('failing_stage'), reason=f['native_result'].get('reason'))
        for f in failures], default=str))


def evaluate(tools, task):
    ev = []
    try:
        f, p, valid = observe(tools)
        bm = segment(tools, f, 'block', ev)
        cloth = segment(tools, f, 'towel', ev) & ~bm
        g = task['geometry']
        bp = points(p, valid, bm)
        cp = points(p, valid, cloth)
        initial_xy = np.median(bp[:, :2], axis=0)
        local = cp[np.linalg.norm(cp[:, :2]-initial_xy, axis=1) < 2*np.linalg.norm(g['dimensions'])]
        plane, ns = fit_plane(local)
        xy, axes, dims, top_plane, noise, face_ev = upward_face(bp, plane, depth_scale(f))
        fp = footprint(xy, axes, dims)
        gaps = z_at(top_plane, fp)-g['height']-z_at(plane, fp)
        ref = g['towel_reference']
        reference_frame = {'K':ref['K'], 'T_world_camera':ref['T_world_camera']}
        prior_coverage = covered(reference_frame, np.asarray(ref['mask'], bool), fp, ref['plane'])
        offsets = np.array([[.7, 0], [-.7, 0], [0, .7], [0, -.7]])*dims
        distance = cKDTree(local[:, :2]).query(xy+offsets@axes.T)[0]
        arm = tools['get_planning_state']()['arms'][g['side']]
        predicted_base = np.asarray(arm['ee_pos'])+Rotation.from_quat(arm['ee_quat']).apply(g['object_base_in_native_grasp'])
        bottom_center = np.r_[xy, float(z_at(top_plane, xy)-g['height'])]
        separation = float(np.linalg.norm(predicted_base-bottom_center))
        before = g.get('capture_metadata', {}).get('captured_at')
        after = f.get('metadata', {}).get('captured_at')
        fresh = before is not None and after is not None and float(after) > float(before)
        checks = dict(
            fresh_post_task_capture=bool(fresh),
            finite_towel_support=bool(prior_coverage.all()),
            fresh_cloth_surrounds_block=bool(np.all(distance < .4*min(dims))),
            supported_height=bool(abs(float(np.min(gaps))) < max(.006, 3*max(noise, ns, g['noise'])) and np.max(gaps) < max(.010, 4*max(noise, ns, g['noise']))),
            dimensions_consistent=bool(np.allclose(np.sort(dims), np.sort(g['dimensions']), rtol=.20, atol=.005)),
            near_selected_target=bool(np.linalg.norm(xy-np.asarray(g['target']['xy'])) < np.linalg.norm(dims)),
            moved_from_source=bool(np.linalg.norm(xy-np.asarray(g['source_xy'])) > min(dims)),
            withdrawn=bool(separation > max(dims)))
        return plain(dict(success=all(checks.values()), evidence=dict(checks=checks,
            block_xy=xy, bottom_gap_range_m=[np.min(gaps), np.max(gaps)],
            fresh_support_plane=plane, surrounding_cloth_distances_m=distance,
            inferred_grasp_object_separation_m=separation, perception=ev,
            face_evidence=face_ev, height_provenance=g['height_provenance'],
            capture=f.get('metadata', {}), measured_arm=arm,
            support_basis='Fresh surrounding cloth plane and pre-placement finite towel mask; hidden contact inferred using documented same-object thickness',
            release_basis='Supported block spatially separated from withdrawn gripper; no command-only success')))
    except Exception as exc:
        return plain(dict(success=False, evidence=dict(verification_error=str(exc), perception=ev)))
