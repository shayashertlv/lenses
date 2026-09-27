"""Source-preserving effective optical-group preparation, independent of renderers.

Explicit membership is a supplied hypothesis, never recovered from a material
name, transmission value, or a normal. The proposed response is symmetric under
normal reversal: normalize the normal and use abs(dot(N, view)) / faceforward.
It does not recover physical outside/inside, coatings, thickness or refraction.
No preparation result grants semantic, photometric, or AR acceptance.
"""
from __future__ import annotations

import hashlib
import json
import re

import numpy as np


_SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_SHA = re.compile(r"[a-f0-9]{64}\Z")


def _json(value, name):
    try:
        return json.loads(json.dumps(value, allow_nan=False, sort_keys=True))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must contain finite JSON values") from error


def _slug(value, name):
    if not isinstance(value, str) or not _SLUG.fullmatch(value):
        raise ValueError(f"{name} must be a safe identifier")
    return value


def _keys(value, required, optional, name):
    if not isinstance(value, dict) or set(value)-required-optional or required-set(value):
        raise ValueError(f"Invalid {name} fields")


def _array_hash(value):
    array = np.ascontiguousarray(value)
    prefix = json.dumps({'dtype': array.dtype.str, 'shape': array.shape}, sort_keys=True).encode()
    return hashlib.sha256(prefix+array.tobytes()).hexdigest()


def _binding(value):
    required = {'asset_sha256', 'node_index', 'mesh_index', 'primitive_index'}
    optional = {'material_index', 'asset_path', 'position_accessor', 'index_accessor', 'normal_accessor'}
    _keys(value, required, optional, 'source_binding')
    if not isinstance(value['asset_sha256'], str) or not _SHA.fullmatch(value['asset_sha256']):
        raise ValueError('source_binding requires a lowercase SHA256')
    for key in required-{'asset_sha256'} | optional-{'asset_path'}:
        if key in value and value[key] is not None and (type(value[key]) is not int or value[key] < 0):
            raise ValueError(f'{key} must be a nonnegative integer')
        if key in required and value[key] is None:
            raise ValueError(f'{key} is required')
    if 'asset_path' in value and not isinstance(value['asset_path'], str):
        raise ValueError('asset_path must be a string')
    return _json(value, 'source_binding')


def _normal_receipt(value):
    _keys(value, {'method', 'source_to_common_matrix', 'provenance'}, set(), 'normal_transform')
    if value['method'] not in ('identity', 'inverse_transpose_normalized'):
        raise ValueError('Unknown normal transformation method')
    matrix = np.asarray(value['source_to_common_matrix'], dtype=np.float64)
    if (matrix.shape != (4, 4) or not np.isfinite(matrix).all() or
            not np.array_equal(matrix[3], [0., 0., 0., 1.]) or np.linalg.det(matrix[:3, :3]) == 0):
        raise ValueError('Normal transformation requires a finite nonsingular affine matrix')
    if value['method'] == 'identity' and not np.array_equal(matrix[:3, :3], np.eye(3)):
        raise ValueError('Identity normal transformation must have identity linear part')
    if not isinstance(value['provenance'], dict) or not value['provenance']:
        raise ValueError('Normal transformation provenance is required')
    return {**_json(value, 'normal_transform'),
            'inverse_transpose_matrix': np.linalg.inv(matrix[:3, :3]).T.tolist(),
            'evidence_scope': 'caller-reported source transformation; input source normals are not re-read or independently verified'}


def faceforward_normals(normals: np.ndarray, view_directions: np.ndarray) -> np.ndarray:
    """Normalize and orient N toward the view; symmetric response uses abs(N.V).

    View directions point from the surface to the viewer. Tangent dot=0 keeps
    the supplied orientation; no outward/volume orientation is inferred.
    """
    n, v = np.asarray(normals, dtype=np.float64), np.asarray(view_directions, dtype=np.float64)
    if n.ndim != 2 or n.shape[1] != 3 or v.shape not in (n.shape, (3,)):
        raise ValueError('Expected Nx3 normals and Nx3 or one 3-vector view direction')
    if not np.isfinite(n).all() or not np.isfinite(v).all():
        raise ValueError('Normals and view directions must be finite')
    n_length, v_length = np.linalg.norm(n, axis=1), np.linalg.norm(v, axis=-1)
    if np.any(n_length == 0) or np.any(v_length == 0) or not np.isfinite(n_length).all() or not np.isfinite(v_length).all():
        raise ValueError('Normals and view directions must be nonzero')
    result = n/n_length[:, None]
    result[np.sum(result*(v/v_length[..., None]), axis=1) < 0] *= -1
    return result


def _topology(positions, indices):
    """Exact-position welding is diagnostic only; output arrays remain untouched."""
    welded, inverse = np.unique(positions, axis=0, return_inverse=True)
    faces = inverse[indices]
    edges = np.sort(np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]]), axis=1)
    unique_edges, counts = np.unique(edges, axis=0, return_counts=True)
    parents = np.arange(len(welded))
    def find(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]; i = parents[i]
        return i
    for a, b in unique_edges:
        first, second = find(a), find(b)
        if first != second:
            parents[second] = first
    components = len({find(i) for i in np.unique(faces)})
    duplicate_count = len(faces)-len(np.unique(np.sort(faces, axis=1), axis=0))
    return {'exact_position_welded_vertex_count': len(welded), 'connected_components': components,
            'boundary_edges': int(np.count_nonzero(counts == 1)),
            'edges_with_more_than_two_faces': int(np.count_nonzero(counts > 2)),
            'duplicate_geometric_faces_ignoring_winding': duplicate_count,
            'scope': 'exact coordinate equality only; counts do not certify a closed volume or semantic identity'}


def prepare_optical_group(group_id: str, primitives: list[dict], *, identity: dict,
                          coordinate_frame: dict, require_verified_identity: bool = False) -> dict:
    """Prepare explicit group members without remeshing or float32 quantization.

    Each primitive supplies id, source_binding, coordinate_frame_id, positions
    Nx3 and indices Mx3, plus optional common-frame normals and normal_transform.
    Separate arrays preserve all source vertices and triangle/vertex ordering.
    Only referenced vertices set common XY bounds. Invalid geometry returns an
    unsupported report with no usable arrays. Schema/provenance errors raise.
    """
    _slug(group_id, 'group_id')
    _keys(identity, {'status', 'provenance'}, set(), 'identity')
    if identity['status'] not in ('unverified', 'externally_verified') or not isinstance(identity['provenance'], dict) or not identity['provenance']:
        raise ValueError('Explicit identity status and provenance are required')
    if type(require_verified_identity) is not bool:
        raise ValueError('require_verified_identity must be boolean')
    if require_verified_identity and identity['status'] != 'externally_verified':
        raise ValueError('Unverified group identity cannot satisfy required identity verification')
    _keys(coordinate_frame, {'id', 'units', 'up_axis', 'forward_axis', 'provenance'}, set(), 'coordinate_frame')
    _slug(coordinate_frame['id'], 'coordinate_frame.id')
    if (coordinate_frame['units'] not in ('meters', 'millimeters', 'centimeters', 'unitless') or
            coordinate_frame['up_axis'] != '+Y' or coordinate_frame['forward_axis'] != '+Z' or
            not isinstance(coordinate_frame['provenance'], dict) or not coordinate_frame['provenance']):
        raise ValueError('Explicit common +Y-up/+Z-front frame, units and provenance are required')
    if not isinstance(primitives, list) or not primitives:
        raise ValueError('At least one explicit primitive is required')
    report = {'schema_version': 1, 'method': 'source_preserving_optical_group_v1', 'group_id': group_id,
              'status': 'unsupported', 'accepted': False, 'quality_verdict': 'unmeasured', 'reasons': [],
              'identity': _json(identity, 'identity'), 'coordinate_frame': _json(coordinate_frame, 'coordinate_frame'),
              'identity_scope': 'supplied assertion; externally_verified is not independently checked and never grants acceptance',
              'membership_source': 'explicit input only', 'runtime_compatibility': 'unmeasured',
              'quantization': 'float64 coordinates retained; float32 export verification pending',
              'normal_response': 'symmetric effective response; normalize N and view, faceforward N toward surface-to-viewer direction, use abs(dot); outward orientation unknown',
              'uv_policy': 'one referenced-group XY extent; v=bottom0_top1; zero X span gives U=.5; unused vertices get (.5,.5)',
              'limitations': ['No physical thickness, separate coatings, refraction or inside/outside orientation is recovered.',
                  'Height coordinates are a geometry-derived hypothesis, not verified intrinsic optical coordinates.',
                  'Membership, source geometry, authored normal intent and material identity can be wrong.',
                  'Source texture UVs/materials are not replaced or interpreted by this array preparation API.'], 'primitives': []}
    prepared, used_ids, bindings = [], set(), set()
    all_referenced = []
    for item in primitives:
        _keys(item, {'id', 'source_binding', 'coordinate_frame_id', 'positions', 'indices'}, {'normals', 'normal_transform'}, 'primitive')
        key = _slug(item['id'], 'primitive.id')
        if key in used_ids:
            raise ValueError('Duplicate primitive id')
        used_ids.add(key)
        if item['coordinate_frame_id'] != coordinate_frame['id']:
            raise ValueError('Primitive coordinate frame does not match the common group frame')
        binding = _binding(item['source_binding'])
        membership = tuple(binding[k] for k in ('asset_sha256', 'node_index', 'mesh_index', 'primitive_index'))
        if membership in bindings:
            raise ValueError('Duplicate source primitive binding')
        bindings.add(membership)
        p, f = np.asarray(item['positions']), np.asarray(item['indices'])
        if p.ndim != 2 or p.shape[1] != 3 or p.dtype.kind != 'f' or len(p) < 3:
            raise ValueError('positions must be floating Nx3 coordinates')
        if f.ndim != 2 or f.shape[1] != 3 or f.dtype.kind not in 'iu' or not len(f) or f.min() < 0 or f.max() >= len(p):
            raise ValueError('indices must be valid integer Mx3 triangle indices')
        p, f = p.astype(np.float64), f.astype(np.int64)
        used = np.unique(f); row = {'id': key, 'source_binding': binding,
            'vertex_count': len(p), 'referenced_vertex_count': len(used), 'unused_vertex_count': len(p)-len(used),
            'triangle_count': len(f), 'positions_sha256': _array_hash(p), 'indices_sha256': _array_hash(f)}
        report['primitives'].append(row)
        if not np.isfinite(p).all():
            report['reasons'].append(f'{key}:nonfinite_positions'); continue
        with np.errstate(over='ignore', invalid='ignore'):
            t = p[f]; cross = np.cross(t[:, 1]-t[:, 0], t[:, 2]-t[:, 0])
            length = np.linalg.norm(cross, axis=1)
        bad = ~np.isfinite(length) | (length == 0)
        row['degenerate_or_nonfinite_area_triangles'] = int(np.count_nonzero(bad))
        if bad.any():
            report['reasons'].append(f'{key}:degenerate_or_nonfinite_triangle_area'); continue
        row['surface_area_source_units_squared'] = float(length.sum()/2)
        geometric = np.zeros(p.shape, np.float64)
        for corner in range(3):
            np.add.at(geometric, f[:, corner], cross)
        n = np.asarray(item['normals']) if item.get('normals') is not None else None
        if n is not None:
            if 'normal_transform' not in item:
                raise ValueError('Supplied normals require source normal transformation provenance')
            row['normal_transform'] = _normal_receipt(item['normal_transform'])
        valid = (n is not None and n.shape == p.shape and n.dtype.kind == 'f' and np.isfinite(n[used]).all()
                 and np.all(np.linalg.norm(n[used], axis=1) > 0) and np.isfinite(np.linalg.norm(n[used], axis=1)).all())
        if valid:
            n = n.astype(np.float64).copy()
            row['normal_policy'] = 'supplied common-frame directions preserved; runtime normalization required'
            # Unused accessor entries have no surface effect. They must not
            # cause valid authored directions on referenced vertices to be
            # replaced by geometric hypotheses.
            lengths = np.linalg.norm(n, axis=1)
            invalid_unused = ~np.isfinite(n).all(axis=1) | ~np.isfinite(lengths) | (lengths == 0)
            n[invalid_unused] = [0., 0., 1.]
            row['invalid_unused_authored_normals_replaced'] = int(np.count_nonzero(invalid_unused))
            row['unused_normal_policy'] = 'valid unused directions preserved; invalid unused directions get +Z sentinel with no surface meaning'
        else:
            row['normal_policy'] = 'derived area-weighted normal hypothesis from unchanged triangle winding'
            row['source_normal_issue'] = 'missing' if n is None else 'invalid shape, nonfinite or zero direction'
            lengths = np.linalg.norm(geometric, axis=1)
            if np.any(lengths[used] == 0) or not np.isfinite(lengths[used]).all():
                report['reasons'].append(f'{key}:zero_or_nonfinite_derived_referenced_normals'); continue
            n = np.zeros(p.shape, np.float64); n[:, 2] = 1.
            n[used] = geometric[used]/lengths[used, None]
            row['unused_normal_policy'] = 'unused vertices receive +Z sentinel, with no surface meaning'
        unit = n/np.linalg.norm(n, axis=1)[:, None]
        referenced_lengths = np.linalg.norm(n[used], axis=1)
        row['referenced_normal_length_range'] = [float(referenced_lengths.min()), float(referenced_lengths.max())]
        dot = np.sum(unit[f]*cross[:, None, :]/length[:, None, None], axis=2)
        row['normals_sha256'] = _array_hash(n)
        row['normal_orientation'] = {'corner_directions_opposing_winding': int(np.count_nonzero(dot < 0)),
            'positive_z_triangles': int(np.count_nonzero(cross[:, 2] > 0)),
            'negative_z_triangles': int(np.count_nonzero(cross[:, 2] < 0)),
            'zero_xy_area_triangles': int(np.count_nonzero(cross[:, 2] == 0)),
            'outward_orientation': 'unverified; no winding or normal sign changes applied'}
        prepared.append({'id': key, 'positions': p.copy(), 'indices': f.copy(), 'normals': n, '_used': used})
        all_referenced.append(p[used])
    if report['reasons']:
        return {'primitives': [], 'report': _json(report, 'report')}
    referenced = np.concatenate(all_referenced)
    lo, hi = referenced.min(axis=0), referenced.max(axis=0)
    span = hi-lo
    if not np.isfinite(span).all() or span[1] <= 0:
        report['reasons'] = ['nonfinite_extent_or_zero_referenced_group_height']
        return {'primitives': [], 'report': _json(report, 'report')}
    report['referenced_bounds'] = {'minimum': lo.tolist(), 'maximum': hi.tolist(), 'extent': span.tolist()}
    report['counts'] = {key: sum(row[key] for row in report['primitives'])
                        for key in ('vertex_count', 'referenced_vertex_count', 'unused_vertex_count', 'triangle_count')}
    report['counts']['primitive_count'] = len(prepared)
    positions, faces, offset = [], [], 0
    for item, row in zip(prepared, report['primitives']):
        used = item.pop('_used'); uv = np.full((len(item['positions']), 2), .5)
        if span[0] > 0:
            uv[used, 0] = (item['positions'][used, 0]-lo[0])/span[0]
        uv[used, 1] = (item['positions'][used, 1]-lo[1])/span[1]
        item['uv'] = uv; row['uv_sha256'] = _array_hash(uv)
        positions.append(item['positions'][used]); faces.append(np.searchsorted(used, item['indices'])+offset)
        offset += len(used)
    report['topology_diagnostics'] = _topology(np.concatenate(positions), np.concatenate(faces))
    report['zero_width_u_policy_applied'] = bool(span[0] == 0)
    report['status'] = 'prepared_candidate'
    canonical = {'group_id': group_id, 'identity': report['identity'], 'coordinate_frame': report['coordinate_frame'],
                 'primitives': sorted(report['primitives'], key=lambda row: row['id'])}
    report['group_sha256'] = hashlib.sha256(json.dumps(canonical, sort_keys=True, allow_nan=False).encode()).hexdigest()
    return {'primitives': prepared, 'report': _json(report, 'report')}
