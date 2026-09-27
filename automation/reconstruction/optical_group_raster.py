"""Experimental geometry contract for one effective interaction per optical group.

This preserves closed/disconnected source meshes. A semantic group is supplied
by the caller; it is never inferred from connectedness, transparency or material
sharing. Its closest hit supplies the full effective response, even when an
opaque receiver is inside a closed mesh. This is not volume optics.

The module is a CPU experiment, not a new accepted production asset profile.
It deliberately leaves cross-group depth ties, capacity overflow and undefined
interpolated normals unsupported. Same-group coincident faces use the existing
raster's source-face tie break; attribute agreement at those ties is UNVERIFIED.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .camera import Camera, project
from .mesh import TriangleMesh
from .raster import DEPTH_UNITS, Raster, rasterize


PROFILE = 'effective_optical_group_v1_experiment'


@dataclass
class GroupRaster:
    """Front-to-back events; each face index refers to the original mesh.

    Arrays have one leading entry per scene group, not a truncated layer cap.
    Missing/inactive entries use -1 for group/face and infinity for depth.
    ``ambiguous`` includes ties to opaque or receiver depth as well as ties
    between optical groups. It is separate from observed layer count.
    """
    group: np.ndarray
    face_index: np.ndarray
    barycentric: np.ndarray
    depth: np.ndarray
    count: np.ndarray
    ambiguous: np.ndarray
    overflow: np.ndarray
    opaque_depth: np.ndarray
    receiver_depth: np.ndarray
    report: dict


def _toward(camera):
    yaw, pitch = np.radians([camera.yaw, camera.pitch])
    return np.array([np.cos(pitch)*np.sin(yaw), np.sin(pitch), np.cos(pitch)*np.cos(yaw)])


def rasterize_optical_groups(mesh: TriangleMesh, face_groups, camera: Camera, shape,
                             *, receiver_depth=None, separation=1e-9,
                             maximum_groups=8, maximum_layers=4,
                             maximum_pixels=1_048_576):
    """Nearest hit per group, then opaque/receiver stop and depth ordering.

    Positions and receiver depths must share input mesh coordinates. ``-1``
    denotes opaque faces; nonnegative integer IDs denote caller-defined groups.
    Receiver depth may be +inf for no receiver; NaN and -inf are invalid. No
    camera normalization, optical identity or material acceptance is inferred.
    """
    vertices, faces = np.asarray(mesh.vertices), np.asarray(mesh.faces)
    ids = np.asarray(face_groups)
    if (vertices.ndim != 2 or vertices.shape[1] != 3 or not len(vertices)
            or not np.isfinite(vertices).all() or faces.ndim != 2 or faces.shape[1] != 3
            or faces.dtype.kind not in 'iu' or not len(faces)
            or np.any(faces < 0) or np.any(faces >= len(vertices))):
        raise ValueError('A finite nonempty indexed triangle mesh is required')
    if ids.shape != (len(faces),) or ids.dtype.kind not in 'iu' or np.any(ids < -1):
        raise ValueError('Every face needs integer group identity (-1 means opaque)')
    if np.any(ids > np.iinfo(np.int64).max):
        raise ValueError('Group identities must fit the signed int64 output representation')
    if (len(shape) != 2 or any(type(n) is not int or n < 2 for n in shape)
            or type(maximum_pixels) is not int or maximum_pixels < 4
            or shape[0]*shape[1] > maximum_pixels):
        raise ValueError('Image dimensions exceed the explicit pixel capacity')
    shape = tuple(shape)
    if any(type(n) is not int or n < 1 for n in (maximum_groups, maximum_layers)):
        raise ValueError('Group and layer capacities must be positive integers')
    if not np.isfinite(separation) or separation < 0:
        raise ValueError('Depth separation must be finite and nonnegative')
    if (not np.isfinite(list(camera.to_dict().values())).all()
            or camera.scale <= 0 or camera.perspective < 0):
        raise ValueError('Camera must be finite with positive scale and nonnegative perspective')
    # Bound projection before raster bbox integer conversion. Also rejects near
    # plane crossings using the existing camera's explicit domain restriction.
    screen = project(vertices, camera)
    if not np.isfinite(screen).all() or np.max(np.abs(screen)) > 1e7:
        raise ValueError('Projected geometry is outside the supported domain')
    groups = np.unique(ids[ids >= 0])
    if len(groups) > maximum_groups:
        raise ValueError('Semantic optical group count exceeds explicit capacity')
    receiver = np.full(shape, np.inf) if receiver_depth is None else np.asarray(receiver_depth, dtype=float)
    if receiver.shape != shape or np.isnan(receiver).any() or np.isneginf(receiver).any():
        raise ValueError('Receiver depth must match image grid without NaN or negative infinity')

    def selected_raster(selection):
        chosen = np.flatnonzero(selection)
        if not len(chosen):
            return Raster(np.full(shape, -1, np.int32), np.zeros((*shape, 3)), np.full(shape, np.inf))
        local = rasterize(TriangleMesh(vertices, faces[chosen], []), camera, shape)
        source_faces = np.full(shape, -1, np.int64)
        source_faces[local.mask] = chosen[local.face_index[local.mask]]
        return Raster(source_faces, local.barycentric, local.depth)

    opaque = selected_raster(ids == -1)
    stop = np.minimum(opaque.depth, receiver)
    rasters = [selected_raster(ids == group) for group in groups]
    shape3 = (len(groups), *shape)
    depth = np.full(shape3, np.inf)
    face = np.full(shape3, -1, np.int64)
    barycentric = np.zeros((*shape3, 3))
    group_map = np.full(shape3, -1, np.int64)
    ambiguous = np.zeros(shape, bool)
    for index, (group, result) in enumerate(zip(groups, rasters)):
        finite_stop = result.mask & np.isfinite(stop)
        delta = np.full(shape, np.inf)
        np.subtract(result.depth, stop, out=delta, where=finite_stop)
        ambiguous |= finite_stop & (np.abs(delta) <= separation)
        active = result.mask & (result.depth < stop - separation)
        depth[index, active] = result.depth[active]
        face[index, active] = result.face_index[active]
        barycentric[index, active] = result.barycentric[active]
        group_map[index, active] = group
    order = np.argsort(depth, axis=0, kind='stable')
    depth = np.take_along_axis(depth, order, axis=0)
    face = np.take_along_axis(face, order, axis=0)
    group_map = np.take_along_axis(group_map, order, axis=0)
    barycentric = np.take_along_axis(barycentric, order[..., None], axis=0)
    if len(groups) > 1:
        both = np.isfinite(depth[1:]) & np.isfinite(depth[:-1])
        delta = np.full(depth[1:].shape, np.inf)
        np.subtract(depth[1:], depth[:-1], out=delta, where=both)
        ambiguous |= np.any(both & (delta <= separation), axis=0)
    count = np.sum(face >= 0, axis=0)
    overflow = count > maximum_layers
    report = {'schema_version': 1, 'profile': PROFILE, 'accepted': False,
              'quality_verdict': 'unmeasured', 'semantic_identity': 'caller_supplied_unverified',
              'depth_units': DEPTH_UNITS, 'depth_separation': float(separation),
              'group_ids': [int(n) for n in groups], 'maximum_groups': maximum_groups,
              'maximum_layers': maximum_layers, 'maximum_pixels': maximum_pixels,
              'maximum_observed_layers': int(count.max()), 'overflow_pixels': int(overflow.sum()),
              'ambiguous_depth_pixels': int(ambiguous.sum()),
              'same_group_tie_policy': 'lowest source face index; optical attribute agreement unverified',
              'limitations': ['Nearest group event is an effective interface, not volume or rear-coating optics.',
                              'Pixel-center geometry does not establish antialiasing or GPU raster-edge parity.',
                              'Same-group coplanar attribute agreement requires a separate asset check.',
                              'Input component labels and per-photo articulation are not verified.']}
    return GroupRaster(group_map, face, barycentric, depth, count, ambiguous, overflow, opaque.depth, receiver.copy(), report)


def sample_single_group_fields(mesh: TriangleMesh, uv, normals, events: GroupRaster, camera: Camera):
    """Actual nearest attributes for single-group rays; retain all unknowns.

    Normals follow vertex normalization, interpolation, renormalization. The
    effective group's response is explicitly symmetric: incidence is abs(dot),
    with a face-forward normal. Reflection is unchanged by normal sign.
    """
    uv, normals = np.asarray(uv, float), np.asarray(normals, float)
    if uv.shape != (len(mesh.vertices), 2) or normals.shape != mesh.vertices.shape:
        raise ValueError('UV and normal arrays must correspond to input vertices')
    shape = events.count.shape
    supported = (events.count == 1) & ~events.ambiguous & ~events.overflow
    group = np.full(shape, -1, np.int64)
    v = np.full(shape, np.nan)
    incidence = np.full(shape, np.nan)
    reflected = np.full((*shape, 3), np.nan)
    invalid_attributes = np.zeros(shape, bool)
    rows, cols = np.nonzero(supported)
    if len(rows):
        faces = mesh.faces[events.face_index[0, rows, cols]]
        weights = events.barycentric[0, rows, cols]
        values = normals[faces]
        lengths = np.linalg.norm(values, axis=2)
        valid_vertex = np.isfinite(values).all(axis=(1, 2)) & np.all(lengths > 0, axis=1)
        unit = np.divide(values, lengths[..., None], out=np.zeros_like(values), where=lengths[..., None] > 0)
        normal = np.einsum('ni,nij->nj', weights, unit)
        length = np.linalg.norm(normal, axis=1)
        local_uv = np.einsum('ni,nij->nj', weights, uv[faces])
        valid = valid_vertex & np.isfinite(local_uv).all(axis=1) & (length > 1e-12)
        valid &= (local_uv[:, 1] >= 0) & (local_uv[:, 1] <= 1)
        invalid_attributes[rows[~valid], cols[~valid]] = True
        supported[rows[~valid], cols[~valid]] = False
        rows, cols, faces, weights, normal, length, local_uv = (
            a[valid] for a in (rows, cols, faces, weights, normal, length, local_uv))
        normal /= length[:, None]
        xyz = np.einsum('ni,nij->nj', weights, mesh.vertices[faces])
        direction = _toward(camera)[None, :] - camera.perspective*xyz
        direction /= np.linalg.norm(direction, axis=1)[:, None]
        cosine = np.sum(normal*direction, axis=1)
        reflection = 2*cosine[:, None]*normal - direction
        yaw, pitch, roll = np.radians([camera.yaw, camera.pitch, camera.roll])
        right = np.array([np.cos(yaw), 0., -np.sin(yaw)])
        up = np.array([-np.sin(pitch)*np.sin(yaw), np.cos(pitch), -np.sin(pitch)*np.cos(yaw)])
        basis = np.column_stack((np.cos(roll)*right-np.sin(roll)*up,
                                 np.sin(roll)*right+np.cos(roll)*up, _toward(camera)))
        group[rows, cols] = events.group[0, rows, cols]
        v[rows, cols] = local_uv[:, 1]
        incidence[rows, cols] = np.degrees(np.arccos(np.clip(np.abs(cosine), 0, 1)))
        reflected[rows, cols] = reflection @ basis
    return {'group': group, 'v': v, 'incidence': incidence, 'reflected': reflected,
            'supported': supported, 'invalid_attributes': invalid_attributes,
            'stacked': events.count > 1, 'ambiguous': events.ambiguous,
            'overflow': events.overflow,
            # An explicit receiver terminates the ray. An opaque object beyond
            # that receiver must not become transmitted rear-image content.
            'rear_weight': (supported & (events.opaque_depth < events.receiver_depth)).astype(float),
            'normal_contract': 'symmetric effective response; vertex normalized then interpolated then face-forward'}
