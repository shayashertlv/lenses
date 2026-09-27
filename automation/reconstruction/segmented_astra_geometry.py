"""Bounded edits of canonical, reduced *pre-optics* GLBs.

The editor appends selected attributes/materials and clones selected mesh
instances. It never sends the asset through Blender or rewrites image/UV/index
bytes. Part IDs are selected-scene traversal ordinals, exactly as in mesh.py.
The guards are local and sampled; they are not product-accuracy, watertightness,
or complete continuous collision certificates. Optical preparation and actual
AR review must run again after an edit.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import struct

import numpy as np

from .deform_glb import (_read_bytes, _float_accessor, _triangles, _unit,
                         _discrete_geometry)
from .mesh import _node_matrix, load_glb_bytes


GEOMETRY_LIMITS = {
    'maximum_displacement_m': .003,
    'maximum_rotation_degrees': 10.,
    'minimum_bend_radius_m': .002,
    'maximum_bend_radius_m': .08,
    'maximum_displacement_gradient': .35,
    'contact_distance_m': .00015,
    'maximum_contact_motion_m': .00001,
    'minimum_new_clearance_m': .00005,
    'maximum_triangles': 200000,
    'maximum_vertices': 1000000,
}
_VEC3 = {'type': 'array', 'items': {'type': 'number'}, 'minItems': 3, 'maxItems': 3}
_IDS = {'type': 'array', 'items': {'type': 'integer', 'minimum': 0},
        'minItems': 1, 'maxItems': 256, 'uniqueItems': True}


def _edit_schema(operation, fields):
    return {'type': 'object', 'additionalProperties': False,
            'properties': {'operation': {'const': operation}, 'part_ids': deepcopy(_IDS), **fields},
            'required': ['operation', 'part_ids', *fields]}


GEOMETRY_EDIT_SCHEMA = {'oneOf': [
    _edit_schema('translate', {'offset_m': deepcopy(_VEC3)}),
    _edit_schema('rotate', {'axis': deepcopy(_VEC3), 'pivot_m': deepcopy(_VEC3),
                          'angle_degrees': {'type': 'number', 'minimum': -10, 'maximum': 10}}),
    _edit_schema('local_bend', {'center_m': deepcopy(_VEC3),
                              'radius_m': {'type': 'number', 'minimum': .002, 'maximum': .08},
                              'offset_m': deepcopy(_VEC3)}),
]}
FRAME_MATERIAL_SCHEMA = {
    'type': 'object', 'additionalProperties': False, 'required': ['part_ids'],
    'properties': {'part_ids': deepcopy(_IDS),
                   'base_color_linear_rgb': {**deepcopy(_VEC3), 'items': {'type': 'number', 'minimum': 0, 'maximum': 1}},
                   'roughness': {'type': 'number', 'minimum': .04, 'maximum': 1},
                   'metallic': {'type': 'number', 'minimum': 0, 'maximum': 1}},
    'description': 'At least one material field is required. Factors retain and multiply existing textures.'}


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _vector(value, name):
    raw = np.asarray(value)
    if raw.dtype.kind not in 'fiu' or raw.shape != (3,) or not np.isfinite(raw).all():
        raise ValueError(f'{name} must be three finite numbers')
    return raw.astype(float)


def _number(value, name, lower, upper):
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, float, np.integer, np.floating)):
        raise ValueError(f'{name} must be a finite number')
    if not np.isfinite(value) or not lower <= value <= upper:
        raise ValueError(f'{name} is outside [{lower}, {upper}]')
    return float(value)


def _ids(values, count):
    if (not isinstance(values, list) or not 1 <= len(values) <= 256
            or any(type(i) is not int or not 0 <= i < count for i in values)
            or len(set(values)) != len(values)):
        raise ValueError('part_ids must be distinct existing integer part IDs')
    return sorted(values)


def _load(source, groups):
    raw, doc, binary = _read_bytes(Path(source).read_bytes())
    extension = 'LENSES_lens_appearance'
    if (extension in doc.get('extensionsUsed', []) or extension in doc.get('extensionsRequired', [])
            or 'effectiveOpticalGroups' in (doc.get('extras') or {})
            or any(extension in (m.get('extensions') or {}) for m in doc.get('materials', []))
            or any({'opticalGroupId', 'lensSurfaceProfile', 'lensAppearanceSha256'} & set(n.get('extras') or {})
                   for n in doc.get('nodes', []))):
        raise ValueError('Edit the canonical pre-optics source, not an optical export with stale bindings')
    if len(doc.get('scenes', [])) != 1 or doc.get('scene', 0) != 0:
        raise ValueError('A single canonical scene is required')
    nodes, visited = doc.get('nodes', []), set()

    def visit(index, ancestors):
        if type(index) is not int or not 0 <= index < len(nodes) or index in visited or index in ancestors:
            raise ValueError('Canonical nodes require a valid single-parent scene graph')
        visited.add(index)
        node = nodes[index]
        if not np.array_equal(_node_matrix(node), np.eye(4)):
            raise ValueError('Canonical edits require baked identity node transforms in metres')
        if node.get('extensions') or 'skin' in node or node.get('weights'):
            raise ValueError('Untracked node extensions, skins or morphs are unsupported')
        for child in node.get('children', []):
            visit(child, ancestors | {index})
    for root in doc['scenes'][0].get('nodes', []):
        visit(root, set())
    for image in doc.get('images', []):
        if 'uri' in image or 'bufferView' not in image:
            raise ValueError('Pinned embedded image bytes are required')
    mesh = load_glb_bytes(raw)
    if len(mesh.faces) > GEOMETRY_LIMITS['maximum_triangles'] or len(mesh.vertices) > GEOMETRY_LIMITS['maximum_vertices']:
        raise ValueError('Reduce the source before bounded geometry editing')
    # Bounds check units plausibility only; it cannot infer forward direction.
    extent = float(np.max(np.ptp(mesh.vertices, axis=0)))
    if not .01 <= extent <= .5 or np.abs(mesh.vertices).max() > .5:
        raise ValueError('Canonical metre bounds are implausible for eyewear')
    rows = []
    for part_id, part in enumerate(mesh.parts):
        primitive = doc['meshes'][part['mesh_index']]['primitives'][part['primitive_index']]
        if primitive.get('extensions') or doc['meshes'][part['mesh_index']].get('weights'):
            raise ValueError('Untracked primitive extensions or morphs are unsupported')
        attrs = primitive['attributes']
        positions = _float_accessor(doc, binary, attrs['POSITION'], 3)
        triangles = _triangles(doc, binary, primitive, len(positions))
        if 'NORMAL' not in attrs:
            raise ValueError('Prepared source parts must have explicit normals')
        normals = _float_accessor(doc, binary, attrs['NORMAL'], 3)
        _unit(normals)
        for name, index in attrs.items():
            if type(index) is not int or not 0 <= index < len(doc['accessors']):
                raise ValueError('Invalid attribute accessor index')
            if doc['accessors'][index]['count'] != len(positions):
                raise ValueError('Attribute counts must match positions')
        tangent = _float_accessor(doc, binary, attrs['TANGENT'], 4) if 'TANGENT' in attrs else None
        if tangent is not None and (not np.isin(tangent[:, 3], [-1, 1]).all()):
            raise ValueError('Tangent handedness must be +/-1')
        rows.append({'id': part_id, 'part': part, 'primitive': primitive,
                     'positions': positions, 'normals': normals, 'tangents': tangent, 'triangles': triangles})
    # mesh.py omits completely invisible primitives. Disallow those rather than
    # letting an edit/material factor change the meaning of subsequent part IDs.
    primitive_count = sum(len(doc['meshes'][nodes[n]['mesh']]['primitives']) for n in visited if 'mesh' in nodes[n])
    if primitive_count != len(rows):
        raise ValueError('Hidden primitives would make part IDs unstable')
    if not isinstance(groups, dict):
        raise ValueError('groups must map stable group IDs to part ID lists')
    membership = {}
    for group_id, values in groups.items():
        if not isinstance(group_id, str) or not group_id.strip():
            raise ValueError('Optical group IDs must be nonblank strings')
        for part_id in _ids(values, len(rows)):
            if part_id in membership:
                raise ValueError('An optical part cannot belong to two groups')
            membership[part_id] = group_id
    return raw, doc, binary, mesh, rows, membership


def inspect_parts(source: Path, groups: dict[str, list[int]]) -> dict:
    raw, doc, _, mesh, rows, membership = _load(source, groups)
    parts = []
    for row in rows:
        part = row['part']; material = doc.get('materials', [])[part['material_index']] if part['material_index'] is not None else {}
        parts.append({'part_id': row['id'], 'node_index': part['node_index'], 'mesh_index': part['mesh_index'],
                      'primitive_index': part['primitive_index'], 'name': part['name'],
                      'role': 'optical' if row['id'] in membership else 'opaque_candidate',
                      'optical_group_id': membership.get(row['id']),
                      'bounds_m': [row['positions'].min(axis=0).tolist(), row['positions'].max(axis=0).tolist()],
                      'vertices': len(row['positions']), 'triangles': len(row['triangles']),
                      'material_index': part['material_index'], 'material': deepcopy(material)})
    return {'schema_version': 1, 'source_sha256': _sha(raw), 'parts': parts,
            'coordinates': {'units': 'metres', 'forward': '+Z', 'up': '+Y', 'origin': 'bridge_underside',
                            'coordinate_convention_supplied_by_canonical_stage': True},
            'bounds_m': [mesh.vertices.min(axis=0).tolist(), mesh.vertices.max(axis=0).tolist()],
            'groups': deepcopy(groups), 'limits': dict(GEOMETRY_LIMITS), 'accepted': False}


def _field(edit, count, source_bounds):
    if not isinstance(edit, dict):
        raise ValueError('Geometry edit must be an object')
    operation = edit.get('operation')
    fields = {'translate': {'offset_m'}, 'rotate': {'axis', 'pivot_m', 'angle_degrees'},
              'local_bend': {'center_m', 'radius_m', 'offset_m'}}
    if operation not in fields or set(edit) != fields[operation] | {'operation', 'part_ids'}:
        raise ValueError('Unknown geometry operation, missing fields or unknown keys')
    selected = _ids(edit['part_ids'], count)
    gradient_bound = 0.
    if operation in ('translate', 'local_bend'):
        delta = _vector(edit['offset_m'], 'offset_m')
        if not 0 < np.linalg.norm(delta) <= GEOMETRY_LIMITS['maximum_displacement_m']:
            raise ValueError('Offset must be nonzero and at most 3 mm')
    if operation == 'rotate':
        axis = _vector(edit['axis'], 'axis'); length = np.linalg.norm(axis)
        if length <= 1e-12:
            raise ValueError('Rotation axis cannot be zero')
        axis /= length
        angle = _number(edit['angle_degrees'], 'angle_degrees', -10, 10)
        if angle == 0:
            raise ValueError('Rotation angle cannot be zero')
        pivot = _vector(edit['pivot_m'], 'pivot_m')
        if np.any(pivot < source_bounds[0] - .01) or np.any(pivot > source_bounds[1] + .01):
            raise ValueError('Rotation pivot must lie within 10 mm of the model bounds')
    if operation == 'local_bend':
        center = _vector(edit['center_m'], 'center_m')
        radius = _number(edit['radius_m'], 'radius_m', .002, .08)
        if np.any(center < source_bounds[0] - radius) or np.any(center > source_bounds[1] + radius):
            raise ValueError('Bend support must overlap the model bounds')
        # max |grad (1-r^2)^3| = 96/(25 sqrt(5)) / radius.
        gradient_bound = float(96 / (25*np.sqrt(5)) * np.linalg.norm(delta) / radius)
        if gradient_bound > GEOMETRY_LIMITS['maximum_displacement_gradient']:
            raise ValueError('Bend displacement gradient exceeds .35')

    def field(points, fraction=1.):
        points = np.asarray(points, dtype=float)
        jacobian = np.broadcast_to(np.eye(3), (len(points), 3, 3)).copy()
        if operation == 'translate':
            return points + delta*fraction, jacobian
        if operation == 'rotate':
            x, y, z = axis
            cross = np.array([[0, -z, y], [z, 0, -x], [-y, x, 0]])
            theta = np.radians(angle*fraction)
            rotation = np.eye(3) + np.sin(theta)*cross + (1-np.cos(theta))*(cross @ cross)
            return (points-pivot) @ rotation.T+pivot, np.broadcast_to(rotation, jacobian.shape).copy()
        relative = (points-center)/radius
        r2 = np.sum(relative*relative, axis=1)
        a = np.maximum(1-r2, 0)
        gradient = -6*a[:, None]**2*relative/radius
        return points + a[:, None]**3*delta*fraction, jacobian + fraction*np.einsum('i,nj->nij', delta, gradient)

    return selected, field, {'operation': operation, 'continuous_displacement_gradient_bound': gradient_bound,
                             'common_field_preserves_coincident_selected_positions': True}


def _surface_scene(vertices, faces):
    import open3d as o3d
    scene = o3d.t.geometry.RaycastingScene(nthreads=2)
    scene.add_triangles(o3d.core.Tensor(np.asarray(vertices, dtype=np.float32)),
                        o3d.core.Tensor(np.asarray(faces, dtype=np.uint32)))
    return scene


def _distance(scene, points):
    import open3d as o3d
    return np.concatenate([scene.compute_distance(o3d.core.Tensor(np.asarray(points[i:i+65536], dtype=np.float32))).numpy()
                           for i in range(0, len(points), 65536)])


def _contact_and_collision(mesh, rows, selected, proposed, field, operation):
    selected_set = set(selected)
    static_faces = [mesh.faces[p['part']['face_start']:p['part']['face_start']+p['part']['face_count']]
                    for p in rows if p['id'] not in selected_set]
    if not static_faces:
        return {'static_triangles': 0, 'contact_samples': 0, 'surface_samples': 0,
                'swept_segments': 0, 'global_intersections_checked': False,
                'note': 'No untouched geometry. Common rigid/smooth field still has finite-triangle limits.'}
    scene = _surface_scene(mesh.vertices, np.concatenate(static_faces))
    source_samples, target_samples = [], []
    for part_id in selected:
        row = rows[part_id]; old, new, tri = row['positions'], proposed[part_id], row['triangles']
        source_samples.extend([old, old[tri].mean(axis=1), (old[tri]+old[tri[:, [1, 2, 0]]]).reshape(-1, 3)*.5])
        target_samples.extend([new, new[tri].mean(axis=1), (new[tri]+new[tri[:, [1, 2, 0]]]).reshape(-1, 3)*.5])
    old = np.concatenate(source_samples); new = np.concatenate(target_samples)
    d0, d1 = _distance(scene, old), _distance(scene, new)
    motion = np.linalg.norm(new-old, axis=1)
    contacts = d0 <= GEOMETRY_LIMITS['contact_distance_m']
    contact_limit = 1e-8 if operation == 'local_bend' else GEOMETRY_LIMITS['maximum_contact_motion_m']
    if np.any(contacts & (motion > contact_limit)):
        raise ValueError('Edit would move a contact/seam sample; include attached parts or move bend support away')
    # Do not permit UV duplicates shared with an untouched part to separate,
    # even inside the more permissive near-contact tolerance for a rotation.
    from scipy.spatial import cKDTree
    static_vertices = np.concatenate([r['positions'] for r in rows if r['id'] not in selected_set])
    tree = cKDTree(static_vertices)
    for part_id in selected:
        row = rows[part_id]
        exact, _ = tree.query(row['positions'], k=1, workers=1)
        if np.any((exact <= 1e-10) & np.any(proposed[part_id] != row['positions'], axis=1)):
            raise ValueError('Edit would separate a duplicate-position seam from an untouched part')
    moving = motion > 1e-8
    newly_close = moving & (d0 > GEOMETRY_LIMITS['contact_distance_m']) & (d1 < GEOMETRY_LIMITS['minimum_new_clearance_m'])
    if newly_close.any():
        raise ValueError('Edit introduces a new sampled collision/clearance violation')
    # Cast source-to-target sample paths. Rotation uses four arc segments;
    # non-linear bends interpolate the actual retained triangle chords.
    import open3d as o3d
    free = moving & ~contacts
    before, after = old[free], new[free]
    steps = 4 if operation == 'rotate' else 1
    start = before
    rays_count = 0
    for step in range(1, steps+1):
        end = field(before, step/steps)[0] if operation == 'rotate' and step != steps else after
        rays = np.concatenate([start, end-start], axis=1).astype(np.float32)
        for i in range(0, len(rays), 65536):
            hits = scene.cast_rays(o3d.core.Tensor(rays[i:i+65536]))['t_hit'].numpy()
            if np.any((hits > 1e-5) & (hits <= 1.00001)):
                raise ValueError('Edit sweeps a surface sample through untouched geometry')
        rays_count += len(rays); start = end
    return {'static_triangles': sum(len(x) for x in static_faces), 'surface_samples': len(old),
            'sample_definition': 'all vertices, all triangle centroids and all edge midpoints',
            'contact_samples': int(contacts.sum()), 'maximum_contact_motion_m': float(motion[contacts].max(initial=0)),
            'minimum_clearance_before_m': float(d0.min()), 'minimum_clearance_after_m': float(d1.min()),
            'new_sampled_clearance_violations': 0, 'swept_segments': rays_count,
            'swept_surface_sample_hits': 0, 'global_intersections_checked': False}


def _append(doc, binary, values, accessor_index, position=False):
    values = np.asarray(values, dtype='<f4')
    if not np.isfinite(values).all():
        raise ValueError('Edited geometry overflows float32')
    binary.extend(b'\0'*(-len(binary) % 4))
    doc['bufferViews'].append({'buffer': 0, 'byteOffset': len(binary), 'byteLength': values.nbytes, 'target': 34962})
    binary.extend(values.tobytes())
    accessor = deepcopy(doc['accessors'][accessor_index])
    accessor.update(bufferView=len(doc['bufferViews'])-1, byteOffset=0)
    if position or 'min' in accessor:
        accessor['min'] = values.min(axis=0).tolist()
    if position or 'max' in accessor:
        accessor['max'] = values.max(axis=0).tolist()
    doc['accessors'].append(accessor)
    return len(doc['accessors'])-1


def _clone_nodes(doc, rows, selected):
    clones = {}
    for part_id in selected:
        node_index = rows[part_id]['part']['node_index']
        if node_index not in clones:
            node = doc['nodes'][node_index]
            doc['meshes'].append(deepcopy(doc['meshes'][node['mesh']]))
            node['mesh'] = len(doc['meshes'])-1
            clones[node_index] = doc['meshes'][-1]
    return {part_id: clones[rows[part_id]['part']['node_index']]['primitives'][rows[part_id]['part']['primitive_index']]
            for part_id in selected}


def _finish(source, destination, raw, original_doc, original_binary, doc, binary, rows, groups, selected, details):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination or destination.exists():
        raise ValueError('Edits require a fresh destination distinct from the source')
    doc['buffers'][0]['byteLength'] = len(binary)
    payload = json.dumps(doc, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
    payload += b' '*(-len(payload) % 4)
    binary.extend(b'\0'*(-len(binary) % 4))
    encoded = (struct.pack('<4sII', b'glTF', 2, 28+len(payload)+len(binary)) +
               struct.pack('<II', len(payload), 0x4E4F534A)+payload +
               struct.pack('<II', len(binary), 0x004E4942)+binary)
    # Reload the exact candidate bytes before writing. No temporary candidate is
    # published if any unchanged-record or stable-ID check fails.
    reread = load_glb_bytes(encoded)
    if len(reread.parts) != len(rows):
        raise ValueError('Edit changed part enumeration')
    if bytes(binary[:len(original_binary)]) != original_binary:
        raise ValueError('Edit changed source binary bytes')
    for key, value in original_doc.items():
        if key not in ('buffers', 'bufferViews', 'accessors', 'nodes', 'meshes', 'materials') and doc.get(key) != value:
            raise ValueError(f'Edit changed unrelated {key} records')
    for key in ('bufferViews', 'accessors', 'meshes', 'materials'):
        if doc.get(key, [])[:len(original_doc.get(key, []))] != original_doc.get(key, []):
            raise ValueError(f'Edit changed existing {key} records')
    mapping = []
    for old, new in zip(rows, reread.parts):
        old_part = old['part']; old_primitive = old['primitive']
        new_primitive = doc['meshes'][new['mesh_index']]['primitives'][new['primitive_index']]
        if any(old_part[k] != new[k] for k in ('node_index', 'primitive_index', 'vertex_count', 'face_count')):
            raise ValueError('Edit changed stable part identity or topology counts')
        if old_primitive.get('indices') != new_primitive.get('indices'):
            raise ValueError('Edit changed triangle indices')
        old_node, new_node = original_doc['nodes'][old_part['node_index']], doc['nodes'][new['node_index']]
        if {k: v for k, v in old_node.items() if k != 'mesh'} != {k: v for k, v in new_node.items() if k != 'mesh'}:
            raise ValueError('Edit changed node metadata or transforms')
        for name, accessor in old_primitive['attributes'].items():
            if name not in ('POSITION', 'NORMAL', 'TANGENT') or old['id'] not in selected:
                if new_primitive['attributes'].get(name) != accessor:
                    raise ValueError('Edit changed an untouched attribute binding')
        if old['id'] not in selected and new_primitive != old_primitive:
            raise ValueError('Edit changed an untouched part primitive')
        mapping.append({'old_part_id': old['id'], 'new_part_id': old['id'],
                        'node_index': new['node_index'], 'primitive_index': new['primitive_index'],
                        'old_mesh_index': old_part['mesh_index'], 'new_mesh_index': new['mesh_index']})
    if Path(source).read_bytes() != raw:
        raise ValueError('Source changed during editing')
    proof = {'schema_version': 1, 'method': 'segmented_preoptics_bounded_edit_v1',
             'source_sha256': _sha(raw), 'output_sha256': _sha(encoded),
             'source': str(source), 'output': str(destination), 'selected_part_ids': selected,
             'part_mapping': mapping, 'groups': deepcopy(groups), 'part_ids_stable': True,
             'original_binary_prefix_preserved': True, 'image_bytes_preserved': True,
             'indices_uv_and_unselected_attributes_preserved': True,
             'untouched_primitives_preserved': True, 'original_records_preserved': True,
             'accepted': False, 'requires_optical_repreparation': True,
             'limitations': ['Contact and collision checks sample surfaces and motion; they do not prove global intersection freedom.',
                             'Existing self-intersections, photographic fidelity and fit are not certified.',
                             'Canonical units/orientation come from the caller preparation stage; bounds only check plausibility.'],
             **details}
    proof['proof_sha256'] = _sha(json.dumps(proof, sort_keys=True, separators=(',', ':'), allow_nan=False).encode())
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open('xb') as handle:
        handle.write(encoded)
    return proof


def apply_geometry_edit(source: Path, destination: Path, edit: dict, *, groups: dict[str, list[int]]) -> dict:
    raw, original_doc, original_binary, mesh, rows, _ = _load(source, groups)
    selected, field, field_report = _field(edit, len(rows), (mesh.vertices.min(axis=0), mesh.vertices.max(axis=0)))
    proposed, jacobians, reports, maximum_displacement = {}, {}, [], 0.
    for part_id in selected:
        row = rows[part_id]; positions, triangles = row['positions'], row['triangles']
        moved, jacobian = field(positions)
        moved = moved.astype('<f4').astype(float)
        motion = np.linalg.norm(moved-positions, axis=1)
        maximum_displacement = max(maximum_displacement, float(motion.max()))
        if maximum_displacement > GEOMETRY_LIMITS['maximum_displacement_m'] + 2e-8:
            raise ValueError('Actual vertex displacement exceeds 3 mm')
        det = np.linalg.det(jacobian)
        if not np.isfinite(det).all() or np.any(det <= 0):
            raise ValueError('Edit has a nonpositive Jacobian')
        report = _discrete_geometry(positions, moved, jacobian, triangles, field)
        old_tri, new_tri = positions[triangles], moved[triangles]
        old_edges = np.linalg.norm(old_tri-old_tri[:, [1, 2, 0]], axis=2)
        new_edges = np.linalg.norm(new_tri-new_tri[:, [1, 2, 0]], axis=2)
        valid = old_edges > 1e-10
        ratios = new_edges[valid]/old_edges[valid]
        if np.any(ratios < .6) or np.any(ratios > 1.4):
            raise ValueError('Edit stretches a retained edge outside [0.6, 1.4]')
        old_area = np.linalg.norm(np.cross(old_tri[:, 1]-old_tri[:, 0], old_tri[:, 2]-old_tri[:, 0]), axis=1)
        new_area = np.linalg.norm(np.cross(new_tri[:, 1]-new_tri[:, 0], new_tri[:, 2]-new_tri[:, 0]), axis=1)
        floor = max(float(np.max(np.ptp(positions, axis=0)))**2*1e-14, 1e-20)
        valid_area = old_area > floor
        area_ratio = new_area[valid_area]/old_area[valid_area]
        if np.any(area_ratio < .4) or np.any(area_ratio > 1.9):
            raise ValueError('Edit changes retained triangle area outside [0.4, 1.9]')
        if np.any((~valid_area) & (new_area > floor)):
            raise ValueError('Edit turns a source degenerate triangle into geometry')
        proposed[part_id], jacobians[part_id] = moved, jacobian
        reports.append({'part_id': part_id, **report, 'maximum_area_ratio': float(area_ratio.max(initial=0)),
                        'minimum_edge_ratio': float(ratios.min(initial=1)), 'maximum_edge_ratio': float(ratios.max(initial=1)),
                        'jacobian_determinant_min': float(det.min()), 'jacobian_determinant_max': float(det.max())})
    if maximum_displacement <= 1e-9:
        raise ValueError('Edit has no representable effect on selected vertices')
    collision = _contact_and_collision(mesh, rows, selected, proposed, field, edit['operation'])
    doc, binary = deepcopy(original_doc), bytearray(original_binary)
    primitives = _clone_nodes(doc, rows, selected)
    for part_id in selected:
        row, attrs, jacobian = rows[part_id], primitives[part_id]['attributes'], jacobians[part_id]
        normals = _unit(np.linalg.solve(jacobian.transpose(0, 2, 1), row['normals'][..., None])[..., 0])
        attrs['NORMAL'] = _append(doc, binary, normals, attrs['NORMAL'])
        if row['tangents'] is not None:
            tangent = row['tangents'].copy()
            direction = np.einsum('nij,nj->ni', jacobian, tangent[:, :3])
            direction -= np.sum(direction*normals, axis=1)[:, None]*normals
            tangent[:, :3] = _unit(direction)
            attrs['TANGENT'] = _append(doc, binary, tangent, attrs['TANGENT'])
        attrs['POSITION'] = _append(doc, binary, proposed[part_id], attrs['POSITION'], position=True)
    return _finish(source, destination, raw, original_doc, original_binary, doc, binary, rows, groups, selected,
                   {'edit': deepcopy(edit), 'field': field_report, 'geometry': reports, 'contact_collision': collision,
                    'maximum_displacement_m': maximum_displacement, 'materials_preserved': True,
                    'normals_transported_inverse_transpose': True, 'tangents_transported_with_handedness_preserved': True})


def set_frame_material(source: Path, destination: Path, part_ids: list[int], *, base_color_linear_rgb=None,
                       roughness=None, metallic=None, groups: dict[str, list[int]]) -> dict:
    raw, original_doc, original_binary, _, rows, membership = _load(source, groups)
    selected = _ids(part_ids, len(rows))
    if set(selected) & set(membership):
        raise ValueError('Frame material edits cannot target optical group members')
    changes = {}
    if base_color_linear_rgb is not None:
        color = _vector(base_color_linear_rgb, 'base_color_linear_rgb')
        if np.any(color < 0) or np.any(color > 1):
            raise ValueError('Base color factors must be linear RGB in [0,1]')
        changes['base_color_linear_rgb'] = color.tolist()
    if roughness is not None:
        changes['roughness'] = _number(roughness, 'roughness', .04, 1)
    if metallic is not None:
        changes['metallic'] = _number(metallic, 'metallic', 0, 1)
    if not changes:
        raise ValueError('At least one frame material field is required')
    doc, binary = deepcopy(original_doc), bytearray(original_binary)
    doc.setdefault('materials', [])
    primitives = _clone_nodes(doc, rows, selected)
    material_map, edits = {}, []
    for part_id in selected:
        old_index = rows[part_id]['part']['material_index']
        if old_index not in material_map:
            material = deepcopy(original_doc['materials'][old_index]) if old_index is not None else {}
            extensions = material.get('extensions') or {}
            if material.get('alphaMode', 'OPAQUE') != 'OPAQUE' or any(k in extensions for k in (
                    'KHR_materials_transmission', 'KHR_materials_volume', 'KHR_materials_pbrSpecularGlossiness',
                    'KHR_materials_unlit', 'LENSES_lens_appearance')):
                raise ValueError('Frame edit requires an ordinary opaque metallic-roughness material')
            pbr = material.setdefault('pbrMetallicRoughness', {})
            if 'base_color_linear_rgb' in changes:
                alpha = pbr.get('baseColorFactor', [1, 1, 1, 1])[3]
                pbr['baseColorFactor'] = [*changes['base_color_linear_rgb'], alpha]
            if 'roughness' in changes:
                pbr['roughnessFactor'] = changes['roughness']
            if 'metallic' in changes:
                pbr['metallicFactor'] = changes['metallic']
            doc['materials'].append(material)
            material_map[old_index] = len(doc['materials'])-1
            edits.append({'source_material_index': old_index, 'new_material_index': material_map[old_index],
                          'changes': deepcopy(changes), 'textures_preserved': True})
        primitives[part_id]['material'] = material_map[old_index]
    return _finish(source, destination, raw, original_doc, original_binary, doc, binary, rows, groups, selected,
                   {'operation': 'set_frame_material', 'material_edits': edits, 'geometry_unchanged': True,
                    'base_color_factor_is_texture_multiplier': True})
