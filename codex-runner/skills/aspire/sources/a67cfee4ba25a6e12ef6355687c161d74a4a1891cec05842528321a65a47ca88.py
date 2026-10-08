import json
import numpy as np
from scipy import ndimage
from scipy.spatial import ConvexHull, cKDTree
from scipy.spatial.transform import Rotation

# All queries in the image batch request hole-free visible regions. An
# edge-connected occlusion is a notch in the outer contour, never a hole.
Q = {
 'block': 'The thick solid green rectangular cuboid, all visible surfaces; exclude rod, towel, chips and robot. Use simple outer polygons with no holes.',
 'block_top': 'Only the upward-facing flat top face of the thick green cuboid, excluding sides, rod, towel and chips. Use a simple outer polygon with no holes.',
 'source_chip': 'Visible top surface of the dark blue poker chip directly underneath the green cuboid, including white markings. Exclude cuboid and chip side edge. Trace the visible crescent itself as a simple concave outer polygon with no holes; do not draw a full circle with a block-shaped hole. Separate disconnected visible pieces into separate hole-free polygons. Return empty if absent.',
 'source_table': 'Select several separate unobstructed patches of white tabletop near the green cuboid, within two block lengths. Each patch must be a simple hole-free polygon containing only visible tabletop. Do not select a surrounding ring or a large polygon with object holes. Exclude all objects, chips, towel, writing and robot.',
 'towel': 'Visible fabric of the large teal green towel on the tabletop; exclude blocks, rods, chips and robot. Represent visible fabric with simple hole-free polygons, splitting around occlusions if needed. Edge-connected occlusions must be outer-contour notches, never holes. Do not include hidden fabric.'
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
    try:
        r = tools['segment_camera_rgb'](f['rgb'], Q[key])
    except ValueError as exc:
        # Rasterization validates the entire image batch. Do not mislabel a
        # failed batch as an absent optional chip or fabricate a replacement.
        log('SEGMENTATION_BATCH_ERROR', dict(requested_key=key, reason=str(exc), capture=f.get('metadata', {})))
        raise
    m = np.asarray(r['mask'], bool)
    if m.shape != np.asarray(f['depth_m']).shape: raise RuntimeError('Mask shape mismatch: '+key)
    if key in ('block', 'block_top') and m.any():
        labels, _ = ndimage.label(m)
        counts = np.bincount(labels.ravel()); counts[0] = 0
        m = labels == counts.argmax()
    # Keep all towel pieces: segmentation can split fabric around occlusions.
    ev.append(dict(name=key, pixels=int(m.sum()), proposal={k:v for k,v in r.items() if k != 'mask'}))
    if not m.any() and not optional: raise RuntimeError('Missing mask: '+key)
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
    c[2] -= origin@c[:2]
    return c, noise


def z_at(c, xy):
    return np.asarray(xy)@np.asarray(c)[:2]+c[2]


def boundary(a):
    return a[ConvexHull(a[:, :2]).vertices, :2]


def face_geometry(bp):
    plane, noise = fit_plane(bp)
    residual = bp[:, 2]-z_at(plane, bp[:, :2])
    face = bp[np.abs(residual) <= 3*noise]
    if len(face) < 12: raise RuntimeError('Insufficient top-face inliers')
    _, axes = np.linalg.eigh(np.cov(face[:, :2].T))
    axes = axes[:, ::-1]
    lo, hi = np.percentile(face[:, :2]@axes, [1, 99], axis=0)
    xy = ((lo+hi)/2)@axes.T
    dims = hi-lo
    if not np.isfinite(dims).all() or np.any(dims <= 0):
        raise RuntimeError('Invalid measured top-face dimensions: '+str(dims))
    return xy, axes, dims, plane, noise


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


def measure(tools):
    ev = []
    f, p, valid = observe(tools)
    masks = {k:segment(tools, f, k, ev, k in ('source_chip', 'source_table')) for k in Q}
    bp = points(p, valid, masks['block_top'] & masks['block'])
    xy, axes, dims, top_plane, nb = face_geometry(bp)
    radius = 2*np.linalg.norm(dims)
    local = np.linalg.norm(p[:, :, :2]-xy, axis=-1) < radius
    table_points = points(p, valid, masks['source_table'] & local)
    table, ns = fit_plane(table_points)
    source, source_kind = table.copy(), 'table'
    chip = masks['source_chip'] & local & ~masks['block']
    chip_evidence = None
    if chip.any():
        cp = points(p, valid, chip)
        offsets = cp[:, 2]-z_at(table, cp[:, :2])
        rise = float(np.median(offsets))
        source[2] += rise
        source_kind = 'chip_on_table'
        chip_evidence = dict(samples=len(cp), rise_m=rise,
            residual_quantiles_m=np.quantile(offsets-rise, [.05, .5, .95]),
            visible_boundary_xy=boundary(cp), slope_basis='measured local table')
    else:
        cloth_near = masks['towel'] & local & ~masks['block']
        dist = ndimage.distance_transform_edt(~masks['block'])
        if np.sum(cloth_near & (dist < 12)) > np.sum(masks['source_table'] & (dist < 12)):
            source, ns = fit_plane(points(p, valid, cloth_near))
            source_kind = 'towel'
    base, top = float(z_at(source, xy)), float(z_at(top_plane, xy))
    height = top-base
    diag = dict(source_xy=xy, dimensions=dims, source_plane=source,
        top_plane=top_plane, base=base, top=top, height=height,
        source_kind=source_kind, chip_evidence=chip_evidence)
    log('BLOCK_SUPPORT_DIAGNOSTICS', diag)
    if not np.isfinite(height) or height <= 0:
        raise RuntimeError('Nonpositive measured block thickness: '+json.dumps(plain(diag)))
    tp = points(p, valid, masks['towel'])
    towel_plane, nt = fit_plane(tp)
    interior = ndimage.distance_transform_edt(masks['towel'])
    candidates = np.argwhere(valid & masks['towel'])
    order = np.argsort(interior[candidates[:, 0], candidates[:, 1]])[::-1]
    targets = []
    for idx in order[::max(1, len(order)//400)]:
        v, u = candidates[idx]
        target = p[v, u, :2]
        fp = footprint(target, axes, dims)
        if not covered(f, masks['towel'], fp, towel_plane).all(): continue
        if any(np.linalg.norm(target-t['xy']) < min(dims) for t in targets): continue
        patch = tp[np.linalg.norm(tp[:, :2]-target, axis=1) < np.linalg.norm(dims)]
        plane, noise = fit_plane(patch)
        relative_bottom = z_at(source, xy+fp-target)-base
        target_z = float(np.max(z_at(plane, fp)-relative_bottom))
        targets.append(dict(xy=target, z=target_z, plane=plane, noise=noise,
                            footprint_xy=fp, coverage=1.0))
        if len(targets) == 3: break
    if not targets: raise RuntimeError('No measured towel interior contains block footprint')
    g = dict(diag, axes=axes, noise=max(nb, ns, nt), targets=targets,
        towel_boundary_xy=boundary(tp), source_support_boundary_xy=boundary(table_points),
        capture_metadata=f.get('metadata', {}), perception_evidence=ev,
        towel_reference=dict(mask=masks['towel'], K=f['K'], T_world_camera=f['T_world_camera'], plane=towel_plane))
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
                grasps = tools['sample_topdown_geometric']('green block', detection=detection,
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
        tm = segment(tools, f, 'block_top', ev)
        cloth = segment(tools, f, 'towel', ev)
        g = task['geometry']
        xy, axes, dims, top_plane, noise = face_geometry(points(p, valid, tm & bm))
        cp = points(p, valid, cloth & ~bm)
        local = cp[np.linalg.norm(cp[:, :2]-xy, axis=1) < 2*np.linalg.norm(dims)]
        plane, ns = fit_plane(local)
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
        checks = dict(
            finite_towel_support=bool(prior_coverage.all()),
            fresh_cloth_surrounds_block=bool(np.all(distance < .4*min(dims))),
            supported_height=bool(abs(float(np.min(gaps))) < max(.006, 3*max(noise, ns, g['noise'])) and np.max(gaps) < max(.010, 4*max(noise, ns, g['noise']))),
            dimensions_consistent=bool(np.allclose(np.sort(dims), np.sort(g['dimensions']), rtol=.20, atol=.005)),
            near_selected_target=bool(np.linalg.norm(xy-np.asarray(g['target']['xy'])) < np.linalg.norm(dims)),
            withdrawn=bool(separation > max(dims)))
        return plain(dict(success=all(checks.values()), evidence=dict(checks=checks,
            block_xy=xy, bottom_gap_range_m=[np.min(gaps), np.max(gaps)],
            fresh_support_plane=plane, surrounding_cloth_distances_m=distance,
            inferred_grasp_object_separation_m=separation, perception=ev,
            capture=f.get('metadata', {}), measured_arm=arm,
            support_basis='Fresh surrounding cloth plane and pre-placement finite towel mask; hidden contact inferred',
            release_basis='Supported block spatially separated from withdrawn gripper; no command-only success')))
    except Exception as exc:
        return plain(dict(success=False, evidence=dict(verification_error=str(exc), perception=ev)))
