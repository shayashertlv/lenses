"""Shared rest geometry and source-bound per-photograph temple articulation.

This module does not infer anatomical part identity from provider names. A binding
is an explicit hypothesis over every source triangle, pinned to numeric geometry.
Posed meshes are observation-time views; export the shared rest mesh for AR.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

import numpy as np

from .mesh import TriangleMesh


def _array(value, shape, name):
    raw = np.asarray(value)
    if raw.dtype.kind not in 'fiu' or raw.shape != shape or not np.isfinite(raw).all():
        raise ValueError(f'{name} must be finite with shape {shape}')
    result = raw.astype(float, copy=True)
    result.setflags(write=False)
    return result


def mesh_geometry_sha256(mesh: TriangleMesh) -> str:
    """Bind face ordinals to positions AND topology, independent of GLB layout."""
    vertices, faces = np.asarray(mesh.vertices), np.asarray(mesh.faces)
    if (vertices.ndim != 2 or vertices.shape[1] != 3 or not len(vertices)
            or not np.isfinite(vertices).all() or faces.ndim != 2 or faces.shape[1] != 3
            or not len(faces) or faces.dtype.kind not in 'iu'
            or faces.min() < 0 or faces.max() >= len(vertices)):
        raise ValueError('An intact finite triangle mesh is required')
    digest = hashlib.sha256(json.dumps([vertices.shape, faces.shape]).encode())
    digest.update(np.ascontiguousarray(vertices, dtype='<f8').tobytes())
    digest.update(np.ascontiguousarray(faces, dtype='<i8').tobytes())
    return digest.hexdigest()


@dataclass(frozen=True)
class Hinge:
    side: str
    origin: np.ndarray
    axis: np.ndarray
    minimum_degrees: float = -105.
    maximum_degrees: float = 105.

    def __post_init__(self):
        if self.side not in ('left', 'right'):
            raise ValueError('Hinge side must be left or right')
        object.__setattr__(self, 'origin', _array(self.origin, (3,), 'hinge origin'))
        axis = _array(self.axis, (3,), 'hinge axis')
        if not np.isclose(np.linalg.norm(axis), 1., atol=1e-8):
            raise ValueError('Hinge axis must have unit length')
        object.__setattr__(self, 'axis', axis)
        if (not np.isfinite([self.minimum_degrees, self.maximum_degrees]).all()
                or not -180 <= self.minimum_degrees < 0 < self.maximum_degrees <= 180):
            raise ValueError('Hinge bounds must contain rest angle zero inside [-180, 180]')

    def to_dict(self):
        return {'side': self.side, 'origin': self.origin.tolist(), 'axis': self.axis.tolist(),
                'minimum_degrees': self.minimum_degrees, 'maximum_degrees': self.maximum_degrees}


@dataclass(frozen=True)
class PartBinding:
    source_geometry_sha256: str
    face_roles: tuple[str, ...]
    hinges: tuple[Hinge, ...]
    provenance: str

    def __post_init__(self):
        digest = self.source_geometry_sha256
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
            raise ValueError('Binding requires source geometry SHA-256')
        roles = tuple(self.face_roles)
        if not roles or any(role not in ('front', 'left_temple', 'right_temple', 'unresolved') for role in roles):
            raise ValueError('Each source face requires one supported role')
        hinges = tuple(self.hinges)
        if any(not isinstance(h, Hinge) for h in hinges) or len({h.side for h in hinges}) != len(hinges):
            raise ValueError('Hinges must be unique typed left/right hypotheses')
        if {h.side for h in hinges} != {side for side in ('left', 'right') if f'{side}_temple' in roles}:
            raise ValueError('Every bound temple requires exactly one shared hinge')
        if not isinstance(self.provenance, str) or not self.provenance.strip():
            raise ValueError('Binding provenance is required')
        object.__setattr__(self, 'face_roles', roles)
        object.__setattr__(self, 'hinges', hinges)

    def validate_scene(self, scene):
        if mesh_geometry_sha256(scene) != self.source_geometry_sha256 or len(scene.faces) != len(self.face_roles):
            raise ValueError('Part binding does not match source geometry')

    def to_dict(self):
        return {'schema_version': 1, 'source_geometry_sha256': self.source_geometry_sha256,
                'face_roles': list(self.face_roles), 'hinges': [h.to_dict() for h in self.hinges],
                'provenance': self.provenance, 'semantic_identity_verified': False}


def bind_parts(scene, *, front_faces, left_temple_faces=(), right_temple_faces=(),
               hinges=(), provenance):
    """Make a complete disjoint partition; undeclared faces remain unresolved."""
    roles = np.full(len(scene.faces), 'unresolved', dtype='<U12')
    assigned = set()
    for role, values in (('front', front_faces), ('left_temple', left_temple_faces),
                         ('right_temple', right_temple_faces)):
        values = list(values)
        if (any(type(i) not in (int, np.int32, np.int64) or not 0 <= i < len(roles) for i in values)
                or len(set(values)) != len(values) or assigned.intersection(values)):
            raise ValueError('Face bindings must contain disjoint unique source face ordinals')
        roles[values] = role
        assigned.update(values)
    return PartBinding(mesh_geometry_sha256(scene), tuple(roles), tuple(hinges), provenance)


@dataclass(frozen=True)
class ViewState:
    view_id: str
    left_degrees: float = 0.
    right_degrees: float = 0.

    def __post_init__(self):
        if not isinstance(self.view_id, str) or not self.view_id.strip():
            raise ValueError('A photo ID is required')
        if not np.isfinite([self.left_degrees, self.right_degrees]).all():
            raise ValueError('Temple angles must be finite')

    def to_dict(self):
        return {'view_id': self.view_id, 'left_degrees': float(self.left_degrees),
                'right_degrees': float(self.right_degrees)}


def hinge_rotation(axis, degrees):
    angle = np.radians(degrees)
    x, y, z = axis
    skew = np.array([[0., -z, y], [z, 0., -x], [-y, x, 0.]])
    return np.eye(3) + np.sin(angle) * skew + (1 - np.cos(angle)) * (skew @ skew)


def pose_points(points, roles, binding, view_state):
    """Apply the same forward kinematics to samples and full observation meshes."""
    points = _array(points, (len(points), 3), 'rest points')
    roles = np.asarray(roles)
    if roles.shape != (len(points),) or any(role not in binding.face_roles for role in set(roles)):
        raise ValueError('Each point requires a bound face role')
    for side in ('left', 'right'):
        if side not in {hinge.side for hinge in binding.hinges} and getattr(view_state, f'{side}_degrees') != 0:
            raise ValueError('A nonzero photo angle requires a bound hinge')
    moved = points.copy()
    for hinge in binding.hinges:
        angle = float(getattr(view_state, f'{hinge.side}_degrees'))
        if not hinge.minimum_degrees <= angle <= hinge.maximum_degrees:
            raise ValueError('Photo temple angle is outside its shared hinge bounds')
        selected = roles == f'{hinge.side}_temple'
        moved[selected] = (points[selected] - hinge.origin) @ hinge_rotation(hinge.axis, angle).T + hinge.origin
    return moved


@dataclass(frozen=True)
class PosedScene:
    mesh: TriangleMesh
    source_face_ids: np.ndarray
    source_vertex_ids: np.ndarray
    source_geometry_sha256: str
    view_state: ViewState


def pose_scene(rest_scene, binding, view_state, *, compact=False):
    """Keep source face order and corner lineage, including shared-vertex seams.

    Observation vertices are intentionally expanded per triangle. A vertex shared
    by a front face and a temple face must not move the front when the arm opens.
    No GLB material, UV, normal or export claim is made by this geometry adapter.
    """
    binding.validate_scene(rest_scene)
    source_vertices = np.asarray(rest_scene.faces, dtype=np.int64).ravel().copy()
    roles = np.repeat(binding.face_roles, 3)
    faces = np.arange(len(source_vertices), dtype=np.int64).reshape(-1, 3)
    if compact:
        role_names, codes = np.unique(roles, return_inverse=True)
        pairs, inverse = np.unique(np.column_stack((source_vertices, codes)), axis=0, return_inverse=True)
        source_vertices, roles = pairs[:, 0], role_names[pairs[:, 1]]
        faces = inverse.reshape(-1, 3)
    vertices = pose_points(rest_scene.vertices[source_vertices], roles, binding, view_state)
    parts = []
    for part in rest_scene.parts:
        used = np.unique(faces[part['face_start']:part['face_start'] + part['face_count']])
        parts.append({**part, 'vertex_start': int(used.min()), 'vertex_count': int(used.max() - used.min() + 1)})
    source_faces = np.arange(len(faces), dtype=np.int64)
    for array in (vertices, faces, source_vertices, source_faces):
        array.setflags(write=False)
    return PosedScene(TriangleMesh(vertices, faces, parts), source_faces, source_vertices,
                      binding.source_geometry_sha256, view_state)


def make_view_scene_contract(scene, binding, states, *, source_model, source_sha256, photo_sha256, evidence):
    """Pin automatic roles and per-photo articulation to an immutable rest asset.

    Compact face runs preserve source ordinals. The source snapshot supplies
    geometry when a later verified partition or optical export changes ordering.
    No provider part names or material identity are used for transfer.
    """
    binding.validate_scene(scene)
    runs, start = [], 0
    for stop in range(1, len(binding.face_roles) + 1):
        if stop == len(binding.face_roles) or binding.face_roles[stop] != binding.face_roles[start]:
            runs.append([start, stop - start, binding.face_roles[start]])
            start = stop
    views = {key: {**state.to_dict(), 'source_image_sha256': photo_sha256[key]} for key, state in states.items()}
    value = {'schema_version': 1, 'method': 'source_bound_articulated_view_scene_v1',
        'source_model': str(Path(source_model).resolve()), 'source_sha256': source_sha256,
        'source_geometry_sha256': binding.source_geometry_sha256,
        'face_roles': {'encoding': 'complete_contiguous_runs', 'count': len(binding.face_roles), 'runs': runs},
        'hinges': [h.to_dict() for h in binding.hinges], 'views': views,
        'provenance': binding.provenance, 'evidence': evidence,
        'accepted': False, 'semantic_identity_verified': False,
        'scope': 'Photo-only articulated scene; canonical AR export retains the unchanged rest geometry'}
    value['contract_sha256'] = hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()
    return value


def read_view_scene_contract(contract, *, view_id=None, photo_sha256=None):
    """Validate the contract, original captured bytes and complete face partition."""
    from .mesh import load_glb_bytes
    value = dict(contract)
    digest = value.pop('contract_sha256', None)
    if (value.get('schema_version') != 1 or value.get('method') != 'source_bound_articulated_view_scene_v1'
            or value.get('accepted') is not False or digest != hashlib.sha256(
                json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()):
        raise ValueError('Articulated view contract hash or schema mismatch')
    raw = Path(value['source_model']).read_bytes()
    if hashlib.sha256(raw).hexdigest() != value['source_sha256']:
        raise ValueError('Articulated rest-model snapshot changed')
    scene = load_glb_bytes(raw)
    if mesh_geometry_sha256(scene) != value['source_geometry_sha256']:
        raise ValueError('Articulation roles refer to different source geometry')
    record = value['face_roles']
    if record.get('encoding') != 'complete_contiguous_runs' or record.get('count') != len(scene.faces):
        raise ValueError('Articulation face role inventory is incomplete')
    roles, cursor = [], 0
    for start, count, role in record['runs']:
        if type(start) is not int or type(count) is not int or start != cursor or count < 1:
            raise ValueError('Articulation face role runs must form a complete partition')
        roles.extend([role] * count); cursor += count
    if cursor != len(scene.faces):
        raise ValueError('Articulation face role runs omit source faces')
    binding = PartBinding(value['source_geometry_sha256'], tuple(roles),
                          tuple(Hinge(**row) for row in value['hinges']), value['provenance'])
    state = None
    if view_id is not None:
        row = value['views'].get(view_id)
        if row is None or row.get('view_id') != view_id or (photo_sha256 is not None and row['source_image_sha256'] != photo_sha256):
            raise ValueError('Articulation state is bound to a different photograph')
        state = ViewState(view_id, row['left_degrees'], row['right_degrees'])
    return scene, binding, state


def transfer_face_roles(source_scene, source_binding, target_scene, *, maximum_relative_error=2e-6):
    """Transfer roles by complete triangle geometry, independent of node ordering.

    Float32 world baking receives an explicit small distance allowance. Multiple
    matching source triangles with conflicting roles remain static/unresolved.
    New construction triangles also remain static and are reported; they never
    acquire moving-part identity by proximity to a temple.
    """
    from scipy.spatial import cKDTree
    source_binding.validate_scene(source_scene)
    if mesh_geometry_sha256(target_scene) == source_binding.source_geometry_sha256:
        return source_binding, {'method': 'identical_source_geometry', 'matched_faces': len(target_scene.faces),
                                'unmatched_faces': 0, 'ambiguous_faces': 0}
    a, b = source_scene.vertices[source_scene.faces], target_scene.vertices[target_scene.faces]
    extent = float(np.ptp(source_scene.vertices, axis=0).max())
    tolerance = max(extent * maximum_relative_error, 1e-12)
    tree = cKDTree(a.mean(axis=1))
    # Match centroids first, then every corner under all six permutations.
    distance, nearest = tree.query(b.mean(axis=1), k=min(8, len(a)), distance_upper_bound=tolerance)
    if distance.ndim == 1:
        distance, nearest = distance[:, None], nearest[:, None]
    labels = np.asarray(source_binding.face_roles)
    result = np.full(len(b), 'unresolved', dtype='<U12')
    role_codes = {'front': 1, 'left_temple': 2, 'right_temple': 4, 'unresolved': 8}
    source_codes = np.array([role_codes[str(role)] for role in labels], np.uint8)
    matching_roles = np.zeros(len(b), np.uint8)
    import itertools
    permutations = list(itertools.permutations(range(3)))
    for column in range(nearest.shape[1]):
        selected = np.flatnonzero(np.isfinite(distance[:, column]))
        if not len(selected):
            continue
        reference = a[nearest[selected, column]]
        best = np.full(len(selected), np.inf)
        for order in permutations:
            error = np.linalg.norm(reference[:, order] - b[selected], axis=2).max(axis=1)
            best = np.minimum(best, error)
        target = selected[best <= tolerance]
        matching_roles[target] |= source_codes[nearest[target, column]]
    matched = ambiguous = 0
    crowded = np.isfinite(distance[:, -1]) if distance.shape[1] == 8 else np.zeros(len(b), bool)
    for role, code in role_codes.items():
        selected = (matching_roles == code) & ~crowded
        result[selected] = role; matched += int(selected.sum())
    ambiguous = int(np.count_nonzero((matching_roles != 0) & (~np.isin(matching_roles, list(role_codes.values())) | crowded)))
    hinges = tuple(h for h in source_binding.hinges if f'{h.side}_temple' in result)
    binding = PartBinding(mesh_geometry_sha256(target_scene), tuple(result), hinges,
                          source_binding.provenance + '; source triangle geometry transfer')
    return binding, {'method': 'source_triangle_corner_correspondence', 'matched_faces': matched,
                     'unmatched_faces': len(b) - matched - ambiguous, 'ambiguous_faces': ambiguous,
                     'maximum_world_corner_error': tolerance, 'unmatched_policy': 'static_unresolved',
                     'reference_source_geometry_sha256': source_binding.source_geometry_sha256}


def pose_mesh_from_contract(mesh, contract, view_id, *, photo_sha256=None, normalization=None,
                            uv=None, normals=None):
    """One adapter for projections, region priors, composition and optical rays.

    Mesh input is world space unless normalization explicitly describes its
    normalized coordinates. Returned geometry uses the same coordinates; face
    order is unchanged and per-vertex attributes follow expanded source corners.
    """
    source, binding, state = read_view_scene_contract(contract, view_id=view_id, photo_sha256=photo_sha256)
    world = mesh
    if normalization is not None:
        center, extent = np.asarray(normalization['center'], float), float(normalization['extent'])
        if center.shape != (3,) or not np.isfinite(center).all() or not np.isfinite(extent) or extent <= 0:
            raise ValueError('Invalid articulation coordinate normalization')
        world = TriangleMesh(mesh.vertices * extent + center, mesh.faces, mesh.parts)
    target_binding, transfer = transfer_face_roles(source, binding, world)
    # A missing arm in a construction subset has no points to transform. Keep
    # its state in provenance while posing only represented hinges.
    present = {h.side for h in target_binding.hinges}
    represented = ViewState(view_id, state.left_degrees if 'left' in present else 0.,
                           state.right_degrees if 'right' in present else 0.)
    posed = pose_scene(world, target_binding, represented, compact=True)
    output = posed.mesh
    if normalization is not None:
        output = TriangleMesh((output.vertices - center) / extent, output.faces, output.parts)
    result = {'mesh': output, 'source_vertex_ids': posed.source_vertex_ids,
        'report': {'contract_sha256': contract['contract_sha256'], 'view_state': state.to_dict(),
                   'role_transfer': transfer, 'source_geometry_sha256': binding.source_geometry_sha256}}
    if uv is not None:
        result['uv'] = np.asarray(uv)[posed.source_vertex_ids].copy()
    if normals is not None:
        n = np.asarray(normals)[posed.source_vertex_ids].copy()
        role_by_vertex = np.full(len(output.vertices), 'unresolved', dtype='<U12')
        for role in ('front', 'left_temple', 'right_temple', 'unresolved'):
            selection = np.asarray(target_binding.face_roles) == role
            role_by_vertex[np.unique(output.faces[selection])] = role
        roles = role_by_vertex
        for hinge in target_binding.hinges:
            selected = roles == f'{hinge.side}_temple'
            n[selected] = n[selected] @ hinge_rotation(hinge.axis, getattr(state, f'{hinge.side}_degrees')).T
        result['normals'] = n
    return result
