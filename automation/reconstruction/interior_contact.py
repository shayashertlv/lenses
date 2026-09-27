"""Interior contact faces: exactly coincident triangles shared by two source parts.

A provider split cuts one solid into named closed parts. Where two parts
touch, each carries a copy of the shared face (a lens back against the wall
the frame kept, the two halves of a shield along their cut). Those faces sit
inside the union of the parts: no photograph sees them, no aperture proposal
describes them, and they make coincident surfaces the ladder merges across and
the optical runtime refuses. This module finds exact coincidences (identical
float64 world corner sets between faces of different parts) and returns a
face mask. The stage keeps every face: interior faces are left out of the
projection and of every optical group, and omitted from the runtime candidate.
Exactness is deliberate: near-coincidence is a modelling question this stage
does not decide, and the counts per part pair are reported.
"""
from __future__ import annotations

import math

import numpy as np


METHOD = 'exact_coincident_cross_part_faces_v1'


def interior_contact_faces(mesh, owners=None) -> tuple[np.ndarray, dict]:
    """Return (mask over mesh.faces, receipt). Faces of one part never mark each other.

    ``owners`` gives each face's part (primitive) index; without it the mesh's own
    part records are used, and a mesh with neither has a single part.
    """
    vertices, faces = np.asarray(mesh.vertices, dtype=float), np.asarray(mesh.faces)
    if faces.ndim != 2 or faces.shape[1] != 3 or not len(faces):
        raise ValueError('An indexed triangle mesh is required')
    parts = list(getattr(mesh, 'parts', []) or [])
    if owners is not None:
        owner = np.asarray(owners, np.int64)
        if owner.shape != (len(faces),) or np.any(owner < 0):
            raise ValueError('owners must give one nonnegative part index per face')
        part_count = int(owner.max()) + 1
    else:
        owner = np.zeros(len(faces), np.int64)
        for index, part in enumerate(parts):
            owner[part['face_start']:part['face_start'] + part['face_count']] = index
        part_count = len(parts)
    corners = vertices[faces]                       # (n, 3, 3)
    # Canonical corner order: sort the three corners lexicographically.
    keys = np.zeros((len(faces), 9))
    for fi_block in range(0, len(faces), 262144):
        block = corners[fi_block:fi_block + 262144]
        order = np.lexsort((block[:, :, 2], block[:, :, 1], block[:, :, 0]), axis=1)
        sorted_block = np.take_along_axis(block, order[:, :, None], axis=1)
        keys[fi_block:fi_block + 262144] = sorted_block.reshape(len(block), 9)
    _, inverse, counts = np.unique(keys, axis=0, return_inverse=True, return_counts=True)
    inverse = inverse.ravel()
    mask = np.zeros(len(faces), bool)
    pair_counts = {}
    candidates = np.flatnonzero(counts[inverse] > 1)
    if len(candidates):
        by_key = {}
        for fi in candidates:
            by_key.setdefault(int(inverse[fi]), []).append(int(fi))
        for members in by_key.values():
            owners = {int(owner[m]) for m in members}
            if len(owners) < 2:
                continue  # duplicates within one part are that part's own topology, not a contact
            for m in members:
                mask[m] = True
            key = tuple(sorted(owners))
            pair_counts[key] = pair_counts.get(key, 0) + len(members)
    receipt = {'method': METHOD, 'faces': int(len(faces)), 'interior_faces': int(mask.sum()),
               'part_pairs': [{'parts': list(k), 'faces': v} for k, v in sorted(pair_counts.items(), key=lambda kv: -kv[1])],
               'per_part_interior_faces': [{'part': i, 'faces': int(mask[owner == i].sum())} for i in range(part_count)],
               'rule': 'a face whose sorted world corners equal those of a face in another part is an interior contact face; exact equality only',
               'limitations': ['Near-coincident or partially overlapping contact faces are not detected.',
                               'A part sharing every face with another part would be entirely interior; nothing here prevents that.']}
    return mask, receipt


CUT_METHOD = 'cut_continuity_v1'


def cut_continuity(mesh, interior_mask, owners, *, angle_degrees=20., minimum_edges=8) -> dict:
    """How continuous two bodies' outer surfaces are across the boundary of the contact patch they share.

    ``owners`` gives each face's body index (a source component). A boundary
    edge of a body's contact patch borders exactly one of its contact faces and
    one of its outer faces; where another body's patch has the same edge (the
    same two vertex positions), the two outer faces' normals are compared. Two
    halves of one shield meet at a planar cut: the outer surfaces continue
    across it and nearly every boundary edge is within ``angle_degrees``. A
    lens seated in a frame meets a groove or a bezel: the outer normals turn
    away along most of the boundary. Pairs with fewer than ``minimum_edges``
    shared boundary edges are reported and flagged as too small to judge.
    Exact vertex equality only, like the contact faces themselves.
    """
    vertices, faces = np.asarray(mesh.vertices, dtype=float), np.asarray(mesh.faces, dtype=np.int64)
    mask, owner = np.asarray(interior_mask, bool), np.asarray(owners, np.int64)
    if (faces.ndim != 2 or faces.shape[1] != 3 or not len(faces) or mask.shape != (len(faces),)
            or owner.shape != (len(faces),) or np.any(owner < 0)):
        raise ValueError('An indexed triangle mesh with one interior flag and one nonnegative owner per face is required')
    if isinstance(angle_degrees, bool) or not isinstance(angle_degrees, (int, float)) or not 0 < angle_degrees < 90:
        raise ValueError('angle_degrees must lie in (0, 90)')
    if type(minimum_edges) is not int or minimum_edges < 1:
        raise ValueError('minimum_edges must be a positive integer')
    receipt = {'method': CUT_METHOD, 'angle_degrees': float(angle_degrees), 'minimum_edges': minimum_edges, 'pairs': [],
               'rule': 'per body pair sharing contact-patch boundary edges: the share of those edges whose adjacent outer faces '
                       'have normals within the angle, and the median angle; exact vertex positions only',
               'limitations': ['A boundary edge counts only where both bodies have an outer face on it.',
                               'Continuity says the surfaces continue, not what the bodies are; the composition rank decides membership.']}
    if not mask.any():
        return receipt
    _, position = np.unique(vertices, axis=0, return_inverse=True)
    position = position.ravel()[faces]
    stride = int(position.max()) + 1
    ends = np.stack([position[:, [0, 1]], position[:, [1, 2]], position[:, [2, 0]]], axis=1)
    edge = np.minimum(ends[..., 0], ends[..., 1]) * stride + np.maximum(ends[..., 0], ends[..., 1])
    span = stride * stride
    normal = np.cross(vertices[faces[:, 1]] - vertices[faces[:, 0]], vertices[faces[:, 2]] - vertices[faces[:, 0]])
    normal /= np.maximum(np.linalg.norm(normal, axis=1, keepdims=True), 1e-300)
    contact = np.flatnonzero(mask)
    tagged = np.repeat(owner[contact], 3) * span + edge[contact].ravel()
    unique, counts = np.unique(tagged, return_counts=True)
    boundary = unique[counts == 1]
    outer = np.flatnonzero(~mask)
    outer_tagged = np.repeat(owner[outer], 3) * span + edge[outer].ravel()
    order = np.argsort(outer_tagged, kind='stable')
    outer_tagged, outer_face = outer_tagged[order], np.repeat(outer, 3)[order]
    low = np.searchsorted(outer_tagged, boundary, 'left')
    has = np.searchsorted(outer_tagged, boundary, 'right') > low
    body, key, face = boundary[has] // span, boundary[has] % span, outer_face[low[has]]
    order = np.argsort(key, kind='stable')
    body, key, face = body[order], key[order], face[order]
    threshold = math.cos(math.radians(angle_degrees))
    cosines = {}
    starts = np.flatnonzero(np.r_[True, key[1:] != key[:-1]]) if len(key) else np.zeros(0, np.int64)
    stops = np.r_[starts[1:], len(key)]
    for start, stop in zip(starts, stops):
        for i in range(start, stop):
            for j in range(i + 1, stop):
                a, b = int(body[i]), int(body[j])
                if a != b:
                    cosines.setdefault((min(a, b), max(a, b)), []).append(float(normal[face[i]] @ normal[face[j]]))
    for (a, b), values in sorted(cosines.items()):
        values = np.asarray(values)
        receipt['pairs'].append({'bodies': [a, b], 'boundary_edges': int(len(values)),
                                 'continuous_fraction': float(np.mean(values >= threshold)),
                                 'median_angle_degrees': float(np.degrees(np.arccos(np.clip(np.median(values), -1., 1.)))),
                                 'enough_edges': bool(len(values) >= minimum_edges)})
    return receipt
