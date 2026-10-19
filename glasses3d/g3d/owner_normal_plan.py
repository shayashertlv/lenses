"""Deterministic local normal fans and independently derived encoding authority.

"""
from __future__ import annotations

import copy

import numpy as np

MANAGED_ATTRIBUTE = 'g3d_owner_normal_managed'
AUTO_ATTRIBUTE = 'g3d_owner_normal_automatic'


def sharp_edges(obj):
    if '_surface_sharp_edges' in obj:
        return {tuple(sorted(e)) for e in obj['_surface_sharp_edges']}
    values = next((a['values'] for a in obj.get('attributes', [])
                   if a['name'] == 'sharp_edge'), None)
    return {tuple(sorted(e)) for i, e in enumerate(obj.get('edges', []))
            if values is not None and values[i][0]}


def shading_influence(obj, operations, *, target=None):
    """Declared corner influence for exclusively shading edits, or full-write None."""
    from . import owner_mesh as M
    own = [op for op in operations if op['scope']['object_id'] == obj['id']]
    # Internal geometry reconstruction also supplies scope-only records. They
    # retain full-write planning and never acquire partial shading authority.
    if not own or any(op.get('kind') != 'repair_shading' for op in own):
        return None
    if target is not None and any(obj[key] != target[key]
                                  for key in ('vertices_local', 'polygons')):
        return None
    faces = set().union(*(M.normal_faces(obj, op['scope']['affected_polygon_ids'])
                          for op in own))
    return {loop for face, corners in enumerate(_corners(obj)[0]) if face in faces
            for loop in corners.values()}


def managed_loops(obj):
    """Explicit encoded corners; old unmasked intent represents a full encoding."""
    from . import owner_surface as S
    count = len(obj['corner_normals'])
    rows = [row for row in obj.get('attributes', []) if row['name'] == MANAGED_ATTRIBUTE]
    if not rows:
        witness = obj.get('normal_intent')
        if witness is None:
            return set()
        storage = [row for row in obj.get('normal_attributes', [])
                   if row['name'] == 'custom_normal']
        intent = [row for row in obj.get('attributes', [])
                  if row['name'] == 'g3d_owner_normal_intent']
        valid = (isinstance(witness, dict)
                 and all(witness.get(flag) is True for flag in
                         ('encoded_matches', 'normal_space_matches', 'decoded_within_quantization'))
                 and len(storage) == 1 and storage[0].get('domain') == 'CORNER'
                 and storage[0].get('data_type') in ('INT16_2D', 'FLOAT_VECTOR')
                 and len(intent) == 1 and intent[0].get('domain') == 'CORNER'
                 and intent[0].get('data_type') == 'FLOAT_VECTOR'
                 and witness.get('intent_normals') == intent[0].get('values'))
        if valid:
            try:
                vectors = np.asarray(witness['intent_normals'], dtype=np.float32)
                codes = np.asarray(storage[0].get('values'))
                width = 2 if storage[0]['data_type'] == 'INT16_2D' else 3
                valid = (vectors.shape == (count, 3) and np.isfinite(vectors).all()
                         and np.all(np.linalg.norm(vectors, axis=1) > 1e-10)
                         and codes.shape == (count, width) and np.isfinite(codes).all())
            except (ValueError, TypeError):
                valid = False
        if not valid:
            S.error('legacy normal authority needs stored normals and a valid exact intent witness',
                    'E_OWNER_NORMALS')
        return set(range(count))
    row = rows[0]
    values = row.get('values')
    if (len(rows) != 1 or row.get('domain') != 'CORNER' or row.get('data_type') != 'BOOLEAN'
            or not isinstance(values, list) or len(values) != count
            or any(not isinstance(v, list) or len(v) != 1 or type(v[0]) is not bool
                   for v in values)):
        S.error('normal encoding mask must be one boolean per corner', 'E_OWNER_NORMALS')
    return {i for i, value in enumerate(values) if value[0]}


def _corners(obj):
    faces, edges, incident = [], {}, {}
    cursor = 0
    for face, poly in enumerate(obj['polygons']):
        corners = {vertex: cursor + i for i, vertex in enumerate(poly)}
        cursor += len(poly)
        faces.append(corners)
        for vertex in poly:
            incident.setdefault(vertex, set()).add(face)
        for a, b in zip(poly, poly[1:] + poly[:1]):
            edges.setdefault(tuple(sorted((a, b))), []).append(face)
    return faces, edges, incident


def normal_fans(obj):
    """Sorted corner tuples joined by smooth faces across non-sharp manifold edges.

    FLOAT_VECTOR storage has independent corner vectors; these groups describe
    topology only. Its encoding authority is computed per corner below.
    """
    faces, edges, _ = _corners(obj)
    count = sum(len(poly) for poly in obj['polygons'])
    parents = list(range(count))

    def root(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    sharp = sharp_edges(obj)
    smooth = obj.get('polygon_smooth', [False] * len(faces))
    for edge, adjacent in edges.items():
        if len(adjacent) != 2 or edge in sharp:
            continue
        left, right = adjacent
        if not smooth[left] or not smooth[right]:
            continue
        for vertex in edge:
            a, b = root(faces[left][vertex]), root(faces[right][vertex])
            if a != b:
                parents[max(a, b)] = min(a, b)
    groups = {}
    for loop in range(count):
        groups.setdefault(root(loop), []).append(loop)
    return tuple(sorted(tuple(group) for group in groups.values()))


def free_storage(obj):
    """Actual storage, not the has_custom_normals claim, chooses representation."""
    from . import owner_surface as S
    rows = [row for row in obj.get('normal_attributes', []) if row['name'] == 'custom_normal']
    if not rows:
        return True
    row = rows[0]
    if (len(rows) != 1 or row.get('domain') != 'CORNER'
            or row.get('data_type') not in ('INT16_2D', 'FLOAT_VECTOR')):
        S.error('unsupported Blender custom normal encoding', 'E_OWNER_NORMALS')
    return row['data_type'] == 'FLOAT_VECTOR'


def _store_sharp(obj, wanted, before):
    """Carry the exact planned native edge flags in pure snapshot representation."""
    _, edges, _ = _corners(obj)
    wanted = set(wanted).intersection(edges)
    obj['_surface_sharp_edges'] = [list(edge) for edge in sorted(wanted)]
    ordered = obj.get('edges')
    if ordered is None or {tuple(sorted(edge)) for edge in ordered} != set(edges):
        ordered = [list(edge) for edge in sorted(edges)]
        obj['edges'] = ordered
    attributes = [row for row in obj.get('attributes', []) if row['name'] != 'sharp_edge']
    if wanted or any(row['name'] == 'sharp_edge' for row in before.get('attributes', [])):
        attributes.append({'name': 'sharp_edge', 'domain': 'EDGE', 'data_type': 'BOOLEAN',
                           'values': [[tuple(sorted(edge)) in wanted] for edge in ordered]})
    obj['attributes'] = attributes


def _shading_step(before, after, operation, *, free):
    """Plan one operation's spaces and return only genuinely re-encoded corners."""
    from . import owner_mesh as M, owner_surface as S
    wanted = sharp_edges(before)
    selected = set(operation['scope']['affected_polygon_ids'])
    scope = M.normal_faces(before, selected)
    corners, edges, incident = _corners(after)
    influence = {loop for face, rows in enumerate(corners) if face in scope for loop in rows.values()}
    old = np.asarray(S.authored_normals(before), dtype=np.float32)
    new = np.asarray(after['corner_normals'], dtype=np.float32)
    if free:
        # Free corner directions do not require changes to automatic face/edge
        # normal domains. Retain every original flag, including flat faces.
        after['polygon_smooth'] = copy.deepcopy(before['polygon_smooth'])
    if np.array_equal(old, new) and after['polygon_smooth'] == before['polygon_smooth']:
        return set()
    if not free:
        eligible = {vertex for vertex, faces in incident.items() if faces <= scope}
        for edge, adjacent in edges.items():
            if (len(adjacent) == 2 and edge not in wanted and selected.intersection(adjacent)
                    and all(vertex in eligible for vertex in edge)):
                wanted.add(edge)
    _store_sharp(after, wanted, before)
    if free:
        rewritten = set(np.flatnonzero(np.any(old != new, axis=1)).tolist())
    else:
        previous_fans = set(normal_fans(before))
        rewritten = set()
        for fan in normal_fans(after):
            rows = list(fan)
            if fan not in previous_fans or not np.array_equal(old[rows], new[rows]):
                rewritten.update(fan)
    if not rewritten <= influence:
        S.error('normal encoding changed a fan outside the declared influence', 'E_OWNER_NORMALS')
    return rewritten


def _replay_shading(obj, operations):
    """Per-operation planning is shared by the native flag plan and mask proof."""
    from . import owner_surface as S
    current = copy.deepcopy(obj)
    rewritten = set()
    free = free_storage(obj)
    for operation in operations:
        if operation['scope']['object_id'] != obj['id']:
            continue
        after, _ = S.plan_object(current, operation)
        rewritten.update(_shading_step(current, after, operation, free=free))
        # As in owner_surface.state, the preceding witness must not override the
        # new pure plan when the next operation reads authored_normals.
        if any(after[key] != current[key] for key in ('corner_normals', 'polygons')):
            after.pop('normal_intent', None)
        current = after
    return current, rewritten


def encoding_loops(source_obj, operations, *, target=None):
    """Corners actually rewritten by this batch, or None for a full geometry write.

    This excludes prior managed corners preserved by this batch. Callers form the
    cumulative mask as managed_loops(source_obj) | encoding_loops(...).
    """
    if shading_influence(source_obj, operations, target=target) is None:
        return None
    _, rewritten = _replay_shading(source_obj, operations)
    return rewritten


def plan(source, target, operations):
    from . import owner_mesh as M, owner_surface as S
    old, current, allowed = M.objects(source), M.objects(target), {}
    for operation in operations:
        oid = operation['scope']['object_id']
        allowed.setdefault(oid, set()).update(M.normal_faces(old[oid], operation['scope']['affected_polygon_ids']))
        for attachment in operation.get('marking_attachments', []):
            mid = attachment['object_id']
            ids = {binding['vertex_id'] for binding in attachment['bindings']}
            faces = {i for i, poly in enumerate(old[mid]['polygons']) if ids.intersection(poly)}
            allowed.setdefault(mid, set()).update(M.normal_faces(old[mid], faces))
    reports = []
    for oid, scope in allowed.items():
        before, after = old[oid], current[oid]
        if shading_influence(before, operations, target=after) is not None:
            planned, _ = _replay_shading(before, operations)
            original = sharp_edges(before)
            wanted = sharp_edges(planned)
            after['polygon_smooth'] = copy.deepcopy(planned['polygon_smooth'])
            _store_sharp(after, wanted, before)
            added = [list(edge) for edge in sorted(wanted - original)]
            reports.append({'object_id': oid, 'added_sharp_edges': added,
                'method': 'preserve unchanged normal fans and exact source flags outside the shown influence',
                'scope': 'selected shading edges with both endpoint fans inside each operation influence; free corner normals retain source flags'})
            continue
        # Existing full-write behavior for non-shading/geometry operations. Their
        # actual normal footprint remains subject to existing scope verification.
        if all(before.get(key) == after.get(key) for key in
               ('vertices_local', 'polygons', 'corner_normals', 'polygon_smooth')):
            continue
        _, edges, _ = _corners(after)
        wanted = sharp_edges(after)
        if '_surface_sharp_edges' not in after:
            mapped = S.split_edge_values({edge: True for edge in wanted}, after.get('_surface_edge_splits', []))
            remap = after.get('_surface_vertex_remap')
            wanted = {tuple(sorted((remap[a], remap[b]) if remap is not None else (a, b)))
                      for a, b in mapped if remap is None or remap[a] is not None and remap[b] is not None}
        wanted.intersection_update(edges)
        parents = after.get('_surface_polygon_parents', list(range(len(after['polygons']))))
        added = []
        for edge, adjacent in edges.items():
            if len(adjacent) != 2 or edge in wanted:
                continue
            if not any(parents[face] in scope for face in adjacent):
                continue
            wanted.add(edge)
            added.append(list(edge))
        _store_sharp(after, wanted, before)
        reports.append({'object_id': oid, 'added_sharp_edges': added,
            'method': 'independent face normal spaces inside shown influence; intended directions retained',
            'scope': 'edges incident to the shown normal-influence faces; geometry unchanged by fan splitting'})
    return reports
