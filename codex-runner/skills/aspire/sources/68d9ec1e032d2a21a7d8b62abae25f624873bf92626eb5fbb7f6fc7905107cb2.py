TASK_INPUTS = {'source_description': 'green rectangular block', 'target_description': 'red round poker chip', 'target_kind': 'chip', 'source_support_strategy': 'measured_table_or_cloth', 'occupancy_strategy': 'SAM3_block_masks_and_labelled_local_context_depth', 'queries': {'block': 'green rectangular block', 'source_table': 'white tabletop', 'source_cloth': 'large teal fabric towel', 'chip': 'red round poker chip', 'occupied_red': 'red rectangular block', 'occupied_black': 'black rectangular block'}, 'local_context_objects': {'green_rod': {'method': 'green_elongated_component'}}}

"""Parameterized Robo-house pick/place executable composed from saved evidence.
TASK_INPUTS is supplied by the shared runner. Colors/descriptions are inputs;
all poses, dimensions, support surfaces and paths come from current tools.
Native contact/planner/frame semantics and independent metric failures remain.
"""
import json
import itertools
import numpy as np
from scipy import ndimage
from scipy.optimize import linprog
from scipy.spatial import ConvexHull, cKDTree
from scipy.spatial.transform import Rotation

# Retains the supplied native towel grasp/transport recipe with fresh geometry.
# Revision: use explicit result dictionaries to remove the unclosed expression.
Q = TASK_INPUTS['queries']
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
    K = np.asarray(f['K'], float)
    T = np.asarray(f['T_world_camera'], float)
    if np.asarray(f['rgb']).shape[:2] != d.shape:
        raise RuntimeError('Paired RGB-D raster mismatch')
    coeffs = f.get('metadata', {}).get('factory_color_intrinsics', {}).get('coeffs', [0]*5)
    if np.any(np.asarray(coeffs, float)):
        raise RuntimeError('Top deprojection requires published zero-distortion intrinsics')
    v, u = np.indices(d.shape)
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
    pixels = np.asarray(f['rgb'])[m]
    median = np.median(pixels, axis=0) if len(pixels) else None
    ev.append({
        'name': key, 'query': Q[key], 'pixels': int(m.sum()),
        'rgb_median': median,
        'proposal': {k:v for k,v in r.items() if k != 'mask'},
        'interpretation': 'Whole-instance SAM3 proposal; depth supplies geometric surfaces'
    })
    if not m.any() and not optional:
        raise RuntimeError('Missing required mask: ' + key)
    return m


def points(p, valid, mask, minimum=12):
    for region in (ndimage.binary_erosion(mask), mask):
        a = p[valid & region]
        if len(a) >= minimum: return a
    raise RuntimeError('Insufficient measured depth: ' + str(int((valid & mask).sum())))


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
        raise RuntimeError('Invalid current block geometry')
    ev = {
        'method': 'Fresh support normal and dominant upper depth layer',
        'instance_samples': len(bp), 'face_samples': len(face),
        'inlier_tolerance_m': tolerance, 'plane': plane, 'noise_m': noise,
        'face_boundary_xy': boundary(face), 'measured_dimensions_m': dims,
        'dimension_provenance': 'Current paired RGB-D instance',
        'instance_height_quantiles_m': np.quantile(heights, [.05, .25, .5, .75, .95]),
        'physical_metrology': False
    }
    return xy, axes, dims, plane, noise, ev


def footprint(xy, axes, dims, n=17):
    a, b = np.meshgrid(np.linspace(-.5, .5, n), np.linspace(-.5, .5, n))
    return np.asarray(xy)+(np.c_[a.ravel(), b.ravel()]*dims)@np.asarray(axes).T


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


def finite_patch(f, mask, fp, plane):
    if not covered(f, mask, fp, plane).all(): return False
    uv = project_surface(f, boundary(fp), plane)
    hull = ConvexHull(uv)
    lo, hi = uv.min(0), uv.max(0)
    h, w = mask.shape
    if lo[0] < 0 or lo[1] < 0 or hi[0] >= w or hi[1] >= h: return False
    u, v = np.meshgrid(np.arange(lo[0], hi[0]+1), np.arange(lo[1], hi[1]+1))
    pix = np.c_[u.ravel(), v.ravel()]
    inside = np.all(pix@hull.equations[:, :2].T+hull.equations[:, 2] <= 1e-8, axis=1)
    return bool(inside.any() and mask[pix[inside, 1], pix[inside, 0]].all())


def depth_scale(f):
    return float(f.get('metadata', {}).get('depth_scale_m', .001))


def polygon_overlap(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    for poly in (a, b):
        for edge in np.roll(poly, -1, axis=0)-poly:
            axis = np.array([-edge[1], edge[0]])
            pa, pb = a@axis, b@axis
            if pa.max() < pb.min() or pb.max() < pa.min(): return False
    return True


def combined_occupancy(masks):
    result = np.zeros_like(masks['block'])
    for key, mask in masks.items():
        if key.startswith('occupied_'):
            result |= mask
    return result


def local_context_masks(f, ev):
    """Explicit station supplement, separate from required SAM3 object masks."""
    result = {}
    for name, specification in TASK_INPUTS.get('local_context_objects', {}).items():
        if specification['method'] != 'green_elongated_component':
            raise RuntimeError('Unknown local context proposal method')
        rgb = np.asarray(f['rgb'], float)
        green = ((rgb[..., 1] > 1.20*rgb[..., 0]) &
                 (rgb[..., 1] > 1.10*rgb[..., 2]) & (rgb[..., 1] > 60))
        labels, count = ndimage.label(green)
        proposals = []
        for index in range(1, count+1):
            mask = labels == index
            if mask.sum() < 50:
                continue
            v, u = np.where(mask)
            values = np.linalg.eigvalsh(np.cov(np.c_[u, v].T))
            elongation = float(np.sqrt(max(values[-1], 0)/max(values[0], 1e-12)))
            if elongation >= 4:
                proposals.append((elongation, mask))
        selected = max(proposals, key=lambda x:x[0]) if proposals else None
        mask = selected[1] if selected else np.zeros(green.shape, bool)
        result['occupied_local_'+name] = mask
        v, u = np.where(mask)
        ev.append(dict(name='occupied_local_'+name,
            backend='labelled_local_RGB_shape_plus_current_metric_depth',
            not_a_SAM3_detection=True, method=specification['method'],
            pixels=int(mask.sum()), elongation_ratio=selected[0] if selected else None,
            bbox_xywh=[int(u.min()), int(v.min()), int(np.ptp(u)+1), int(np.ptp(v)+1)] if len(u) else [0, 0, 0, 0],
            proposal_parameters=dict(green_vs_red=1.20, green_vs_blue=1.10,
                green_min=60, minimum_component_pixels=50, shape_elongation=4),
            capture=f.get('metadata', {}), physical_metrology=False))
    return result


def polygon_distance(a, b):
    def point_edge(p, x, y):
        edge = y-x
        if edge@edge == 0:
            return float(np.linalg.norm(p-x))
        return float(np.linalg.norm(p-(x+np.clip((p-x)@edge/(edge@edge), 0, 1)*edge)))
    return min(point_edge(p, x, y)
        for first, second in ((a, b), (b, a)) for p in first
        for x, y in zip(second, np.roll(second, -1, axis=0)))


def finger_sweep_clear(station, side, R, drop, above, occupied):
    """Conservative measured-context planning, not a change to native validation."""
    meshes = station['planner_validation']['native_gripper_meshes']['arms'][side]
    for obstacle in occupied:
        poly = np.asarray(obstacle['footprint_xy'])
        prism = np.vstack([np.c_[poly, np.full(len(poly), z)] for z in obstacle['z_range_m']])
        obstacle_planes = ConvexHull(prism).equations
        for mesh in meshes:
            a = mesh['aperture']
            lo = np.minimum(a['closed']['min_grasp_m'], a['open']['min_grasp_m'])
            hi = np.maximum(a['closed']['max_grasp_m'], a['open']['max_grasp_m'])
            corners = np.array(list(itertools.product(*zip(lo, hi))))
            world = np.vstack([R.apply(corners)+pos for pos in (drop, above)])
            planes = np.vstack([ConvexHull(world).equations, obstacle_planes])
            intersection = linprog(np.zeros(3), A_ub=planes[:, :3], b_ub=-planes[:, 3],
                bounds=[(None, None)]*3, method='highs')
            if intersection.success:
                return False
            if intersection.status != 2:
                raise RuntimeError('Unresolved native mesh/context prism intersection')
    return True


def chip_clearance_options(g, station, side, source_R, local_base):
    center = np.asarray(g['target_xy'])
    occupied = g['occupied_objects']
    axes, dims, radius = np.asarray(g['axes']), np.asarray(g['dimensions']), g['chip_radius']
    # This is the existing evaluator's centering bound, not a relaxed support rule.
    bound = min(.012, .65*radius)
    candidates = []
    for yaw in (0., -90., 90., -45., 45.):
        turn = Rotation.from_euler('z', yaw, degrees=True)
        R = turn*source_R
        rotated_axes = turn.as_matrix()[:2, :2]@axes
        for dx in np.linspace(-bound, bound, 17):
            for dy in np.linspace(-bound, bound, 17):
                xy = center+[dx, dy]
                error = float(np.linalg.norm(xy-center))
                if error >= bound:
                    continue
                fp = boundary(footprint(xy, rotated_axes, dims))
                if not np.all(np.abs((center-xy)@rotated_axes) < dims/2):
                    continue
                if any(polygon_overlap(fp, np.asarray(o['footprint_xy'])) for o in occupied):
                    continue
                z = float(z_at(g['chip_plane'], xy))
                drop = np.r_[xy, z]-R.apply(local_base)
                transfer_z = max(g['top'], drop[2],
                    g['obstacle_top_z']-float(R.apply(local_base)[2]))+g['height']
                above = np.r_[drop[:2], transfer_z]
                if not finger_sweep_clear(station, side, R, drop, above, occupied):
                    continue
                clearance = min((polygon_distance(fp, np.asarray(o['footprint_xy']))
                    for o in occupied), default=radius)
                evidence = dict(chip_center_inside_block=True,
                    block_center_inside_measured_chip=error < radius,
                    block_center_to_chip_edge_m=radius-error,
                    observed_occupant_footprint_clearance_m=clearance,
                    center_error_m=error, placement_yaw_delta_deg=yaw,
                    native_tip_mesh_open_close_vertical_sweep_clear=True,
                    scope='Conservative selected native tip meshes and measured occupant depth; physical metrology unverified')
                candidate = dict(g['targets'][0], xy=xy, z=z, footprint_xy=fp,
                    placement_yaw_delta_deg=yaw, clearance_evidence=evidence)
                candidates.append((min(clearance, radius-error), -error, candidate, R))
                if error == 0 and yaw == 0:
                    return [(candidate, R)]
    candidates.sort(key=lambda x:(x[0], x[1]), reverse=True)
    selected = []
    for _, _, candidate, R in candidates:
        if any(candidate['placement_yaw_delta_deg'] == c['placement_yaw_delta_deg'] and
                np.linalg.norm(candidate['xy']-c['xy']) < .15*min(dims) for c, _ in selected):
            continue
        selected.append((candidate, R))
        if len(selected) == 3:
            break
    log('BOUNDED_CHIP_CLEARANCE', dict(feasible_candidates=len(candidates),
        selected=[c['clearance_evidence'] for c, _ in selected], native_candidate_limit=3))
    return selected


def measure(tools):
    ev = []
    f, p, valid = observe(tools)
    masks = {k: segment(tools, f, k, ev, optional=k.startswith(('occupied_', 'source_'))) for k in Q}
    masks.update(local_context_masks(f, ev))
    bm = masks['block']
    occupancy = combined_occupancy(masks) & ~bm
    bp = points(p, valid, bm)
    center0 = np.median(bp[:, :2], axis=0)
    extent0 = np.linalg.norm(np.ptp(bp[:, :2], axis=0))
    local = np.linalg.norm(p[:, :, :2]-center0, axis=-1) < 2*extent0
    distance = ndimage.distance_transform_edt(~bm)
    support_scores = {}
    surfaces = {}
    excluded = bm | occupancy
    if 'chip' in masks:
        excluded |= masks['chip']
    for key in ('source_table', 'source_cloth'):
        surface = masks[key] & ~ndimage.binary_dilation(excluded, iterations=2)
        if key == 'source_table':
            surface &= ~masks['source_cloth']
        surfaces[key] = surface
        adjacent = surface & valid & (distance > 3) & (distance <= 15)
        support_scores[key] = float(np.sum(1/distance[adjacent]))
    support_key = max(support_scores, key=support_scores.get)
    if support_scores[support_key] == 0:
        raise RuntimeError('Neither measured table nor cloth surrounds the source block')
    support_points = points(p, valid, surfaces[support_key] & local)
    source, ns = fit_plane(support_points)
    xy, axes, dims, top_plane, nb, face_ev = upward_face(bp, source, depth_scale(f))
    base, top = float(z_at(source, xy)), float(z_at(top_plane, xy))
    height = top-base
    if not np.isfinite(height) or height <= 0:
        raise RuntimeError('Nonpositive current measured source thickness')
    occupied, obstacle_tops = [], [top]
    for key in (k for k in masks if k.startswith('occupied_')):
        if not masks[key].any():
            continue
        op = points(p, valid, masks[key] & ~bm)
        occupied.append({'name': key, 'footprint_xy': boundary(op), 'samples': len(op),
            'z_range_m':np.quantile(op[:, 2], [.01, .99])})
        obstacle_tops.append(float(np.quantile(op[:, 2], .99)))
    g = dict(source_xy=xy, dimensions=dims, axes=axes, source_plane=source,
        table_plane=source, top_plane=top_plane, base=base, top=top, height=height,
        source_kind=support_key, source_support_scores=support_scores,
        noise=max(nb, ns), height_provenance=dict(
            method='Current upper depth layer minus currently selected local support',
            height_m=height, physical_metrology=False, transferred_object_dimensions=False),
        local_support_evidence=dict(samples=len(support_points), plane=source, noise_m=ns,
            hidden_underside='Inferred surrounding support, not directly observed'),
        face_evidence=face_ev, occupied_objects=occupied, obstacle_top_z=max(obstacle_tops),
        target_identity=TASK_INPUTS['target_description'], target_kind=TASK_INPUTS['target_kind'],
        task_inputs=TASK_INPUTS, capture_metadata=f.get('metadata', {}), perception_evidence=ev)
    if TASK_INPUTS['target_kind'] == 'chip':
        cm = masks['chip'] & ~bm & ~occupancy
        target, radius, plane, noise, visible_boundary = chip_geometry(p, valid, cm, bm)
        fp = footprint(target, axes, dims)
        relative_bottom = z_at(source, xy+fp-target)-base
        # The measured finite circle is the support reference; block overhang
        # is retained as evidence rather than replacing the chip with a plane.
        g.update(chip_radius=radius, chip_plane=plane,
            chip_z=float(z_at(plane, target)), target_xy=target,
            visible_chip_boundary_xy=visible_boundary, noise=max(g['noise'], noise),
            targets=[dict(xy=target, z=float(z_at(plane, target)), plane=plane,
                noise=noise, footprint_xy=fp, measured_circle_radius_m=radius,
                block_overhang_m=np.maximum(dims/2-radius, 0),
                support_basis='Fresh measured chip circle and visible surface; hidden contact unverified')])
    else:
        cloth = masks['towel'] & ~bm & ~occupancy
        tp = points(p, valid, cloth)
        towel_plane, nt = fit_plane(tp)
        interior = ndimage.distance_transform_edt(cloth)
        candidates = np.argwhere(valid & cloth)
        # Native feedback showed an otherwise feasible pickup failing carry
        # to the deepest interior patch. Try current nearest finite patches;
        # full measured footprint/occupancy checks still decide suitability.
        candidate_xy = p[candidates[:, 0], candidates[:, 1], :2]
        order = np.argsort(np.linalg.norm(candidate_xy-xy, axis=1))
        targets = []
        for idx in order[::max(1, len(order)//600)]:
            v, u = candidates[idx]
            target = p[v, u, :2]
            fp = footprint(target, axes, dims)
            if not finite_patch(f, cloth, fp, towel_plane):
                continue
            if any(polygon_overlap(boundary(fp), o['footprint_xy']) for o in occupied):
                continue
            if any(np.linalg.norm(target-t['xy']) < min(dims) for t in targets):
                continue
            patch = tp[np.linalg.norm(tp[:, :2]-target, axis=1) < np.linalg.norm(dims)]
            if len(patch) < 12:
                continue
            plane, noise = fit_plane(patch)
            if not finite_patch(f, cloth, fp, plane):
                continue
            relative_bottom = z_at(source, xy+fp-target)-base
            targets.append(dict(xy=target, z=float(np.max(z_at(plane, fp)-relative_bottom)),
                plane=plane, noise=noise, footprint_xy=fp, coverage=1.0,
                support_basis='Current finite free cloth patch, no added release gap'))
            if len(targets) == 3:
                break
        if not targets:
            raise RuntimeError('No finite free fabric patch fits this measured source footprint')
        g.update(targets=targets, noise=max(g['noise'], nt), towel_boundary_xy=boundary(tp),
            towel_reference=dict(mask=cloth, K=f['K'], T_world_camera=f['T_world_camera'], plane=towel_plane))
    log('MEASURED_GEOMETRY', {k: v for k, v in g.items() if k != 'towel_reference'})
    return g


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
    gap = centers[1, 0]-centers[0, 0]
    travel = slides[1, 0]-slides[0, 0]
    jaw = float((width-gap)/travel)
    contacts = centers+jaw*slides
    evidence = {
        'facets_closed_native':facets, 'slide_vectors_native_m':slides,
        'contact_points_native':contacts, 'predicted_enclosing_jaw_fraction':jaw,
        'open_gap_m':gap+travel, 'physical_contact_calibration_measured':False
    }
    return contacts.mean(0), evidence


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
    out = []
    for step in steps:
        if step['kind'] in ('open', 'close'):
            jaws[step['side']] = float(step['kind'] == 'open')
        else:
            for side in jaws:
                key = side+'_gripper_target_width'
                if key in step['arguments']: jaws[side] = float(step['arguments'][key])
            out.append({
                'stage':step['stage'], 'arguments':step['arguments'],
                'gripper_state':dict(jaws)
            })
    return out, assumptions


def build_task(tools):
    station = tools['get_station_info']()
    state = tools['get_planning_state']()
    g = measure(tools)
    failures = []
    # Recorded short-span grasps reached lift but exhausted carry IK. Explore
    # both measured in-plane spans; native CAD and planning decide feasibility.
    span_order = tuple(dict.fromkeys((int(np.argmax(g['dimensions'])), int(np.argmin(g['dimensions'])))))
    for opening_axis in span_order:
        opening = np.r_[g['axes'][:, opening_axis], 0.0]
        down = np.array([0., 0., -1.])
        native = np.column_stack((opening, np.cross(down, opening), down))
        yaw = display(Rotation.from_matrix(native))[2]
        for fraction in (.65, .50, .70):
            for tilt in (30., -30., 0., 15., -15.):
                for side in ('right', 'left'):
                    idle = 'left' if side == 'right' else 'right'
                    midpoint, cad = contact(station, side, float(g['dimensions'][opening_axis]))
                    start = np.asarray(state['arms'][side]['ee_pos'], float)
                    sr = Rotation.from_quat(state['arms'][side]['ee_quat'])
                    idle_rpy = display(Rotation.from_quat(state['arms'][idle]['ee_quat']))
                    detection = {
                        'position_3d':np.r_[g['source_xy'], g['base']+fraction*g['height']].tolist(),
                        'score':1.0
                    }
                    # Binding invokes original skill_library.grasp_geometry.
                    grasps = tools['sample_topdown_geometric'](
                        TASK_INPUTS['source_description'], detection=detection, camera='top',
                        yaws=[(yaw+360)%360-180, yaw], pitches=[180.],
                        z_offsets=[0.], width=float(cad['open_gap_m']), max_grasps=2
                    )
                    for grasp in grasps:
                        R = rotation(grasp.rpy)*Rotation.from_euler('x', tilt, degrees=True)
                        rpy = display(R)
                        site = np.asarray(grasp.position)-R.apply(midpoint)
                        local_base = R.inv().apply(np.r_[g['source_xy'], g['base']]-site)
                        options = (chip_clearance_options(g, station, side, R, local_base)
                            if g.get('target_kind') == 'chip' else [(t, R) for t in g['targets']])
                        for target, placement_R in options:
                            offset = -placement_R.apply(local_base)
                            placement_rpy = display(placement_R)
                            # Binding invokes original skill_library.pick_place.
                            estimated = tools['estimate_drop'](
                                TASK_INPUTS['target_description'],
                                detection={'position_3d':np.r_[target['xy'], target['z']].tolist()},
                                z_offset=float(offset[2]), camera='top'
                            )
                            drop = np.asarray(estimated, float)
                            drop[:2] += offset[:2]
                            approach = site+[0, 0, .75*g['height']]
                            lift = site+[0, 0, g['height']]
                            base_offset_z = float(R.apply(local_base)[2])
                            source_fp = footprint(g['source_xy'], g['axes'], g['dimensions'])
                            bottom_variation = float(np.min(z_at(g['source_plane'], source_fp)-g['base']))
                            transfer_z = max(
                                g['top'], lift[2], drop[2],
                                g['obstacle_top_z']-base_offset_z-bottom_variation
                            )+g['height']
                            raised = np.r_[site[:2], transfer_z]
                            retract = raised.copy()
                            retract[:2] += .35*(drop[:2]-site[:2])
                            above = np.r_[drop[:2], transfer_z]
                            stage = .5*(start+approach)
                            stage[2] = max(start[2], approach[2])
                            half = sr*Rotation.from_rotvec(.5*(sr.inv()*R).as_rotvec())

                            def move(name, pos, orient):
                                arguments = {
                                    side+'_target_pos':np.asarray(pos).tolist(),
                                    side+'_target_rpy':list(orient),
                                    idle+'_target_pos':list(state['arms'][idle]['ee_pos']),
                                    idle+'_target_rpy':idle_rpy,
                                    'planning_speed':.5
                                }
                                return {'stage':name, 'kind':'move', 'arguments':arguments}

                            steps = [
                                {'stage':'open_for_grasp', 'kind':'open', 'side':side},
                                move('stage_near_arm', stage, display(half)),
                                move('orient_before_extension', stage, rpy),
                                move('approach_block', approach, rpy),
                                move('grasp_block', site, rpy),
                                {'stage':'close_on_block', 'kind':'close', 'side':side},
                                move('lift_block', lift, rpy),
                                move('raise_in_transfer_lane', raised, rpy),
                                move('retract_holding_block', retract, rpy),
                                move('transport_above_target', above, placement_rpy),
                                move('place_on_target', drop, placement_rpy),
                                {'stage':'release_block', 'kind':'open', 'side':side},
                                move('withdraw_vertically', above, placement_rpy)
                            ]
                            seq, assumptions = segments(steps, state, station)
                            result = tools['plan_freespace_sequence'](seq)
                            config = {
                                'side':side, 'tilt_deg':tilt, 'height_fraction':fraction,
                                'opening_axis_index':int(opening_axis), 'grasp_span_m':float(g['dimensions'][opening_axis]),
                                'site':site, 'release':drop, 'rpy':rpy, 'placement_rpy':placement_rpy, 'target':target,
                                'native_opening_world':R.as_matrix()[:, 0],
                                'object_base_in_native_grasp':local_base,
                                'proposed_contacts_world':R.apply(cad['contact_points_native'])+site,
                                'predicted_released_base':drop+placement_R.apply(local_base),
                                'transfer_z':transfer_z
                            }
                            log('NATIVE_CANDIDATE', {'configuration':config, 'native_result':result})
                            if result.get('success') is True:
                                geometry = dict(g)
                                geometry.update(config)
                                task = {
                                    'steps':steps, 'geometry':geometry,
                                    'preview':result, 'preview_segments':seq,
                                    'measured_start_state':state, 'contact_geometry':cad,
                                    'jaw_assumptions':assumptions,
                                    'planning_failures':failures,
                                    'gripper_provenance':station['gripper_geometry'],
                                    'tool_pose_frame':'aspire_grasp',
                                    'extra_API_conversion_applied':False,
                                    'evidence_scope':'Complete native preview only; runner owns physical enablement, execution, parking and fresh evaluation'
                                }
                                return plain(task)
                            failures.append(plain({'configuration':config, 'native_result':result}))
    log('ALL_NATIVE_FAILURES', failures)
    details = [
        {
            'side':x['configuration']['side'],
            'tilt':x['configuration']['tilt_deg'],
            'failing_stage':x['native_result'].get('failing_stage'),
            'reason':x['native_result'].get('reason')
        }
        for x in failures
    ]
    raise RuntimeError('No complete native sequence: '+json.dumps(details, default=str))


def evaluate_fabric(tools, task):
    # Read-only and repeatable on the runner's fresh post-parking scene.
    # Evaluation thresholds are retained; they are not motion admission gates.
    ev = []
    try:
        f, p, valid = observe(tools)
        masks = {k:segment(tools, f, k, ev, optional=k in OCCUPANTS) for k in ('block', 'towel')+OCCUPANTS}
        masks.update(local_context_masks(f, ev))
        bm = masks['block']
        cloth = masks['towel'] & ~bm & ~combined_occupancy(masks)
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
        current_occupied = {k:boundary(points(p, valid, m)) for k, m in masks.items()
            if k.startswith('occupied_') and m.any()}
        arm = tools['get_planning_state']()['arms'][g['side']]
        predicted_base = np.asarray(arm['ee_pos'])+Rotation.from_quat(arm['ee_quat']).apply(g['object_base_in_native_grasp'])
        bottom_center = np.r_[xy, float(z_at(top_plane, xy)-g['height'])]
        separation = float(np.linalg.norm(predicted_base-bottom_center))
        before = g.get('capture_metadata', {}).get('captured_at')
        after = f.get('metadata', {}).get('captured_at')
        fresh = before is not None and after is not None and float(after) > float(before)
        maximum_noise = max(noise, ns, g['noise'])
        checks = {
            'fresh_post_task_capture':bool(fresh),
            'finite_towel_support':bool(prior_coverage.all()),
            'fresh_cloth_surrounds_block':bool(np.all(distance < .4*min(dims))),
            'supported_height':bool(abs(float(np.min(gaps))) < max(.006, 3*maximum_noise) and np.max(gaps) < max(.010, 4*maximum_noise)),
            'dimensions_consistent':bool(np.allclose(np.sort(dims), np.sort(g['dimensions']), rtol=.20, atol=.005)),
            'near_selected_target':bool(np.linalg.norm(xy-np.asarray(g['target']['xy'])) < np.linalg.norm(dims)),
            'moved_from_source':bool(np.linalg.norm(xy-np.asarray(g['source_xy'])) > min(dims)),
            'clear_of_observed_occupants':bool(not any(polygon_overlap(boundary(fp), o) for o in current_occupied.values())),
            'withdrawn':bool(separation > max(dims))
        }
        success = bool(all(checks.values()))
        evidence = {
            'checks':checks,
            'outcome':'GEOMETRIC_PLACEMENT_SUPPORTED' if success else 'UNVERIFIED',
            'block_xy':xy, 'measured_dimensions_m':dims,
            'bottom_gap_range_m':[np.min(gaps), np.max(gaps)],
            'fresh_support_plane':plane,
            'surrounding_cloth_distances_m':distance,
            'current_occupied_footprints':current_occupied,
            'inferred_grasp_object_separation_m':separation,
            'perception':ev, 'face_evidence':face_ev,
            'height_provenance':g['height_provenance'],
            'capture':f.get('metadata', {}), 'measured_arm':arm,
            'support_basis':'Fresh surrounding cloth plus pre-placement finite free mask; bottom inferred from this task measured height',
            'parking_basis':'Parking is established by the runner; use fresh post-parking invocation for retained placement',
            'limitations':'CAD contact and RGB-D dimensions remain uncalibrated physical metrology. Failed checks remain unresolved; no motion retry is requested.'
        }
        return {'success':success, 'evidence':plain(evidence)}
    except Exception as exc:
        evidence = {
            'outcome':'UNVERIFIED', 'verification_error':str(exc),
            'perception':ev
        }
        return {'success':False, 'evidence':plain(evidence)}


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


def evaluate_chip(tools, task):
    ev = []
    try:
        f, p, valid = observe(tools)
        g = task['geometry']
        bm = segment(tools, f, 'block', ev)
        cm = segment(tools, f, 'chip', ev, optional=True)
        xy, axes, dims, plane, noise, face_ev = upward_face(
            points(p, valid, bm), np.asarray(g['chip_plane']), depth_scale(f))
        target = np.asarray(g['target_xy'])
        center_error = float(np.linalg.norm(xy-target))
        bottom_error = float(abs(z_at(plane, xy)-g['height']-g['chip_z']))
        visible = p[valid & cm]
        chip_ok = True
        if len(visible):
            chip_ok = float(np.median(np.linalg.norm(visible[:, :2]-target, axis=1))) < 1.5*g['chip_radius']
        arm = tools['get_planning_state']()['arms'][g['side']]
        predicted_base = np.asarray(arm['ee_pos'])+Rotation.from_quat(arm['ee_quat']).apply(g['object_base_in_native_grasp'])
        separation = float(np.linalg.norm(predicted_base-np.r_[xy, z_at(plane, xy)-g['height']]))
        before, after = g['capture_metadata'].get('captured_at'), f.get('metadata', {}).get('captured_at')
        checks = dict(fresh_post_task_capture=before is not None and after is not None and after > before,
            centered_over_chip=center_error < min(.012, .65*g['chip_radius']),
            chip_center_inside_block=bool(np.all(np.abs((target-xy)@axes) < dims/2)),
            bottom_at_chip=bottom_error < max(.006, 3*max(noise, g['noise'])),
            dimensions_consistent=bool(np.all(np.abs(np.sort(dims)-np.sort(g['dimensions'])) < .012)),
            visible_chip_consistent=bool(chip_ok), withdrawn=separation > max(g['dimensions']))
        context_masks = {k:segment(tools, f, k, ev, optional=True) for k in OCCUPANTS}
        context_masks.update(local_context_masks(f, ev))
        current_occupied = {k:boundary(points(p, valid, m & ~bm))
            for k, m in context_masks.items() if (m & ~bm).any()}
        checks['clear_of_observed_occupants'] = not any(
            polygon_overlap(boundary(footprint(xy, axes, dims)), poly)
            for poly in current_occupied.values())
        return plain(dict(success=all(checks.values()), evidence=dict(checks=checks,
            block_xy=xy, target_xy=target, center_error_m=center_error,
            inferred_bottom_error_m=bottom_error, grasp_object_separation_m=separation,
            face_evidence=face_ev, perception=ev, capture=f.get('metadata', {}),
            current_occupied_footprints=current_occupied,
            hidden_chip_basis='Fresh pre-placement circle; hidden support remains inferred',
            limitations='No physical validation of all color combinations or intrinsic dimensions.')))
    except Exception as exc:
        return plain(dict(success=False, evidence=dict(verification_error=str(exc), perception=ev)))


def evaluate(tools, task):
    return evaluate_chip(tools, task) if TASK_INPUTS['target_kind'] == 'chip' else evaluate_fabric(tools, task)
