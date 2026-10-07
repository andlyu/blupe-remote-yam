"""Mount an installed I2RT gripper on ASPIRE's calibrated API grasp frame.

This adapts model data and normalized jaw coordinates, never controller gains,
joint bounds, collision acceptance, or the upstream checkout.
"""
from copy import deepcopy
import hashlib
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np
from scipy.spatial.transform import Rotation


def _numbers(values):
    return ' '.join(format(float(v), '.17g') for v in values)


def transform(element):
    matrix = np.eye(4)
    matrix[:3, 3] = np.fromstring(element.get('pos', '0 0 0'), sep=' ')
    quat = np.fromstring(element.get('quat', '1 0 0 0'), sep=' ')
    matrix[:3, :3] = Rotation.from_quat(quat[[1, 2, 3, 0]]).as_matrix()
    return matrix


def install_gripper(root, profile, api_to_planner):
    """Replace the upstream gripper using exact installed XML/mesh files."""
    source = Path(profile['model_xml']).resolve()
    if hashlib.sha256(source.read_bytes()).hexdigest() != profile['model_xml_sha256']:
        raise ValueError('Installed gripper XML differs from its recorded hash')
    stock = ET.parse(source).getroot()
    assets = root.find('asset')
    files = {}
    for mesh in stock.findall('asset/mesh'):
        name = mesh.get('name')
        path = Path(profile['mesh_files'][name]['path']).resolve()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != profile['mesh_files'][name]['sha256']:
            raise ValueError('Installed gripper mesh differs from recorded hash: '+name)
        element = deepcopy(mesh)
        element.set('name', 'i2rt_linear4310_'+name)
        element.set('file', str(path))
        assets.append(element)
        files[name] = dict(path=str(path), sha256=digest)
    base = stock.find('worldbody/body')
    stock_grasp = transform(base.find("site[@name='grasp_site']"))
    planner_to_api = np.eye(4)
    planner_to_api[:3, :3] = np.asarray(api_to_planner).T
    mounts = {}
    for side in ('left', 'right'):
        tcp = root.find(f".//body[@name='{side}_tcp']")
        parent = next(p for p in root.iter() if tcp in list(p))
        for node in list(parent):
            if node.tag == 'geom' or (node.tag == 'body' and node.get('name') in
                    (side+'_left_link_finger', side+'_right_link_finger')):
                parent.remove(node)
        mount = transform(tcp.find(f"site[@name='{side}_grasp_site']")) @ planner_to_api @ np.linalg.inv(stock_grasp)
        mounted = deepcopy(base)
        mounted.set('name', side+'_linear4310_gripper')
        mounted.set('pos', _numbers(mount[:3, 3]))
        quat = Rotation.from_matrix(mount[:3, :3]).as_quat()
        mounted.set('quat', _numbers(quat[[3, 0, 1, 2]]))
        for node in mounted.iter():
            name = node.get('name')
            if node.tag == 'body' and name != side+'_linear4310_gripper':
                node.set('name', side+'_linear4310_'+name)
            elif node.tag == 'joint':
                node.set('name', side+('_left_finger' if name == 'joint7' else '_right_finger'))
                node.set('class', 'finger')
            elif node.tag == 'site':
                node.set('name', side+'_linear4310_'+name)
                node.set('group', '4')
            elif node.tag == 'geom':
                mesh_name = node.get('mesh')
                node.set('name', side+'_linear4310_'+mesh_name+'_collision')
                node.set('mesh', 'i2rt_linear4310_'+mesh_name)
                node.set('class', 'collision')
                node.set('group', '3')
                node.set('contype', '1')
                node.set('conaffinity', '1')
                node.set('rgba', '.15 .16 .17 1')
                node.attrib.pop('material', None)
        for body in mounted.iter('body'):
            for collision in list(body.findall('geom')):
                visual = deepcopy(collision)
                visual.set('name', collision.get('name').replace('_collision', '_visual'))
                visual.set('class', 'visual')
                visual.set('group', '2')
                visual.set('contype', '0')
                visual.set('conaffinity', '0')
                body.append(visual)
        tcp.append(mounted)
        # Preserve the installed same-direction slides and equality constraint.
        equality = root.find(f"./equality/joint[@joint1='{side}_left_finger']")
        equality.set('polycoef', '0 1 0 0 0')
        mounts[side] = mount.tolist()
    # Saved original keyframes encode the replaced jaw coordinates.
    for keyframe in root.findall('keyframe'):
        root.remove(keyframe)
    return dict(type='linear_4310', source='installed_i2rt', model_xml=str(source),
                model_xml_sha256=profile['model_xml_sha256'], meshes=files,
                mounts_in_native_tcp=mounts,
                normalized_mapping='both fingers = 0.0475 * g metres; g=0 closed, g=1 open',
                API_grasp_reference_preserved=True,
                collision_geometry='installed housing and tip mesh convex hulls; native collision rules preserved',
                physical_contact_calibration_measured=False)


def set_jaws(model, data, side, normalized):
    """Use installed endpoints when selected; otherwise preserve old replay mapping."""
    installed = any(model.body(i).name == side+'_linear4310_gripper' for i in range(model.nbody))
    for finger, sign in (('left', 1.), ('right', -1.)):
        joint = model.joint(f'{side}_{finger}_finger')
        data.qpos[joint.qposadr[0]] = .0475*normalized if installed else sign*.0376*normalized


def adapt_native_planner_grippers(planner):
    """Replace native hardcoded jaw coordinates with the installed model range.

    Native RRT, collision/contact rejection, IK and arm joint limits remain
    untouched. Added gripper bodies participate in the same native arm grouping.
    """
    model = planner._model
    if not any(model.body(i).name == 'left_linear4310_gripper' for i in range(model.nbody)):
        return
    for side in ('left', 'right'):
        group = getattr(planner, '_'+side+'_arm_bodies')
        group.update(i for i in range(model.nbody) if model.body(i).name.startswith(side+'_linear4310_'))
    def set_gripper_qpos(left_gripper=None, right_gripper=None):
        for side, value in (('left', left_gripper), ('right', right_gripper)):
            if value is not None:
                g = float(np.clip(value, 0., 1.))
                values = tuple(float(j.range[0]+g*(j.range[1]-j.range[0]))
                               for j in (model.joint(side+'_left_finger'), model.joint(side+'_right_finger')))
                setattr(planner, '_'+side+'_gripper_qpos', values)
    planner.set_gripper_qpos = set_gripper_qpos
    planner.set_gripper_qpos(0., 0.)


def mesh_geometry(model, *, joints=None):
    """Report CAD vertices and planar contact candidates in the native grasp frame."""
    import mujoco
    data = mujoco.MjData(model)
    for side, values in (joints or {}).items():
        for index, value in enumerate(values, 1):
            data.qpos[model.joint(f'{side}_joint{index}').qposadr[0]] = value
    result = dict(frame='aspire_grasp', units='metres', geometry_source='installed_i2rt_CAD',
                  physical_contact_calibration_measured=False, arms={})
    for side in ('left', 'right'):
        rows = []
        for tip in ('tip_left', 'tip_right'):
            gid = model.geom(side+'_linear4310_'+tip+'_collision').id
            mesh = model.geom_dataid[gid]
            start, count = model.mesh_vertadr[mesh], model.mesh_vertnum[mesh]
            vertices = model.mesh_vert[start:start+count]
            row = dict(mesh=tip, mesh_vertices=int(count), aperture={})
            for aperture, label in ((0., 'closed'), (1., 'open')):
                set_jaws(model, data, side, aperture)
                mujoco.mj_forward(model, data)
                site = model.site(side+'_grasp_site').id
                rotation = data.site_xmat[site].reshape(3, 3)
                world = vertices @ data.geom_xmat[gid].reshape(3, 3).T + data.geom_xpos[gid]
                points = (world-data.site_xpos[site]) @ rotation
                row['aperture'][label] = dict(min_grasp_m=points.min(axis=0).tolist(),
                    max_grasp_m=points.max(axis=0).tolist(), center_grasp_m=points.mean(axis=0).tolist())
                if aperture == 0:
                    face_start, face_count = model.mesh_faceadr[mesh], model.mesh_facenum[mesh]
                    triangles = points[model.mesh_face[face_start:face_start+face_count]]
                    normals = np.cross(triangles[:, 1]-triangles[:, 0], triangles[:, 2]-triangles[:, 0])
                    lengths = np.linalg.norm(normals, axis=1)
                    normals /= np.maximum(lengths[:, None], 1e-20)
                    # Expose all near-opening-axis planes as CAD candidates;
                    # do not assert which face contacted the physical object.
                    selected = triangles[np.abs(normals[:, 0]) > .995]
                    groups = {}
                    for triangle in selected:
                        key = round(float(triangle[:, 0].mean()), 4)
                        groups.setdefault(key, []).extend(triangle.tolist())
                    row['opening_axis_planar_facets'] = [dict(x_m=x,
                        min_grasp_m=np.min(points, axis=0).tolist(),
                        max_grasp_m=np.max(points, axis=0).tolist())
                        for x, points in sorted(groups.items())]
            rows.append(row)
        result['arms'][side] = rows
    return result
