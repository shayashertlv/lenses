"""Surface-relative marking attachments for confirmed edits (numpy only).

The existing geometric decal rule selects candidates without reading product names.
Bindings are made once from the captured, shown source and sealed with understanding.
Each vertex follows its surface triangle, retaining its barycentric foot and normal
offset. Nothing is rebuilt from an obsolete temple specification.
"""
from __future__ import annotations

import copy
import numpy as np

from .temple import measure


def _error(message):
    from .owner_mesh import OwnerMeshError
    raise OwnerMeshError("E_OWNER_MARKING", message)


def nearest(points, vertices, triangles):
    """Exact closest points on nondegenerate triangles; deterministic identity ties."""
    triangles = np.asarray(triangles, int)
    xyz = np.asarray(vertices, float)[triangles]
    a, ab, ac = xyz[:, 0], xyz[:, 1]-xyz[:, 0], xyz[:, 2]-xyz[:, 0]
    cross = np.cross(ab, ac)
    area = np.linalg.norm(cross, axis=1)
    valid = np.flatnonzero(area > 1e-10)
    if not len(valid):
        _error("the attachment surface has no nondegenerate triangle")
    a, ab, ac, cross, area = a[valid], ab[valid], ac[valid], cross[valid], area[valid]
    normals = cross/area[:, None]
    d00, d01, d11 = np.einsum('ij,ij->i', ab, ab), np.einsum('ij,ij->i', ab, ac), np.einsum('ij,ij->i', ac, ac)
    den = area*area  # cross-product form avoids cancellation for very slender triangles
    result = []
    for p in np.asarray(points, float):
        ap = p-a
        d20, d21 = np.einsum('ij,ij->i', ap, ab), np.einsum('ij,ij->i', ap, ac)
        u, v = (d11*d20-d01*d21)/den, (d00*d21-d01*d20)/den
        bary = np.c_[1-u-v, u, v]
        q = a+ab*u[:, None]+ac*v[:, None]
        distance = np.einsum('ij,ij->i', p-q, p-q)
        distance[np.any(bary < 0, axis=1)] = np.inf
        # An outside projection's nearest point lies on an edge.
        for start, end in ((0, 1), (1, 2), (2, 0)):
            s, e = xyz[valid, start], xyz[valid, end]
            edge = e-s
            t = np.clip(np.einsum('ij,ij->i', p-s, edge)/np.einsum('ij,ij->i', edge, edge), 0, 1)
            candidate = s+t[:, None]*edge
            d = np.einsum('ij,ij->i', p-candidate, p-candidate)
            take = d < distance
            q[take], distance[take] = candidate[take], d[take]
            bary[take] = 0
            bary[take, start], bary[take, end] = 1-t[take], t[take]
        i = int(np.argmin(distance))
        sd = float(np.dot(p-q[i], normals[i]))
        result.append((int(valid[i]), bary[i].tolist(), float(np.sqrt(distance[i])), sd))
    return result


def _open(obj):
    counts = {}
    for face in obj['polygons']:
        for a, b in zip(face, face[1:]+face[:1]):
            key = tuple(sorted((a, b)))
            counts[key] = counts.get(key, 0)+1
    return any(n == 1 for n in counts.values())


def _thin(points):
    centered = points-points.mean(0)
    axes = np.linalg.svd(centered, full_matrices=False)[2]
    return float(np.min(np.ptp(centered @ axes.T, axis=0))) < measure.DECAL_THIN_MM


def discover(snapshot, operations):
    """Return host-computed bindings, only where a confirmed surface may move."""
    from . import owner_mesh as M
    indexed = M.objects(snapshot)
    moving = {}
    for op in operations:
        oid = op['scope']['object_id']
        weights, _ = M.scope_vertices(op['scope'], indexed[oid])
        moving.setdefault(oid, set()).update(i for i, w in weights.items() if w)
    candidates = {}
    for oid, support in moving.items():
        surface = indexed[oid]
        v = np.asarray(surface['vertices_world_mm'], float)
        triangles = np.asarray(surface['triangles'], int)
        xyz = v[triangles]
        hinge = surface.get('aliases', {}).get('hinge', {})
        axis = hinge.get('axis_xz_mm')
        for unsupported in snapshot.get('unsupported_rendered_geometry', []):
            lo, hi = np.asarray(unsupported['bbox_world_mm'])
            near = np.all(lo <= v.max(0)+measure.DECAL_NEAR_MM) and np.all(hi >= v.min(0)-measure.DECAL_NEAR_MM)
            if near or unsupported.get('parent_name') == surface['name']:
                _error(f"possible marking {unsupported['name']} uses unsupported {unsupported['type']} geometry near or attached to the edited surface")
        for mid, mark in indexed.items():
            if mid in moving or not mark['vertices_world_mm']:
                continue
            points = np.asarray(mark['vertices_world_mm'], float)
            lo, hi = points.min(0), points.max(0)
            attached = mark.get('parent_name') == surface['name'] and (_open(mark) or _thin(points))
            if (np.any(lo > v.max(0)+measure.DECAL_NEAR_MM) or
                    np.any(hi < v.min(0)-measure.DECAL_NEAR_MM)):
                if attached:
                    _error(f"attached marking {mark['name']} is outside the supported surface distance; cannot follow safely")
                continue
            if axis and measure.hinge_part_kind(lo, hi, mark.get('role'), surface.get('side'),
                    axis[0], hinge['z_mm'], len(points)):
                continue
            # Every vertex must lie near the same actual surface. The bounding box
            # cull is exact for the existing maximum attachment distance.
            eligible = np.flatnonzero(np.all(xyz.max(1) >= lo-measure.DECAL_NEAR_MM, axis=1) &
                                      np.all(xyz.min(1) <= hi+measure.DECAL_NEAR_MM, axis=1))
            if not len(eligible):
                if attached:
                    _error(f"attached marking {mark['name']} has no nearby surface attachment")
                continue
            found = nearest(points, v, triangles[eligible])
            if max(row[2] for row in found) > measure.DECAL_NEAR_MM:
                if attached:
                    _error(f"attached marking {mark['name']} exceeds the supported surface distance; cannot follow safely")
                continue
            sd = [row[3] for row in found]
            if not measure.is_surface_decal(sd, float(np.min(hi-lo)), _open(mark)):
                continue
            bindings = []
            for vid, (tid, bary, _, _) in enumerate(found):
                tri = triangles[eligible[tid]].tolist()
                if support.intersection(tri):
                    bindings.append({'vertex_id': vid, 'surface_vertex_ids': tri, 'barycentric': bary})
            if not bindings:
                continue
            ev = mark.get('evaluated', {})
            users = mark.get('shared_mesh_users', 1)
            if (not ev.get('positions_match_source') or not ev.get('topology_matches_source') or
                    (len(users) if isinstance(users, list) else users) != 1 or mark.get('unsupported_edit_state')):
                _error(f"marking {mark['name']} has no uniquely editable evaluated mesh; cannot follow safely")
            row = {'object_id': mid, 'object_name': mark['name'], 'surface_object_id': oid,
                   'surface_object_name': surface['name'], 'bindings': bindings,
                   'source_topology_sha256': mark['topology_sha256'],
                   'surface_topology_sha256': surface['topology_sha256'],
                   'source_standoff_mm': [min(sd), max(sd)],
                   'method': 'barycentric surface foot and triangle-frame offset'}
            if mid in candidates:
                _error(f"marking {mark['name']} lies on more than one edited surface; attachment is ambiguous")
            candidates[mid] = row
    return list(candidates.values())


def seal(snapshot, operations):
    from . import owner_mesh as M
    result = copy.deepcopy(operations)
    attachments = discover(snapshot, operations)
    for op in result:
        weights, _ = M.scope_vertices(
            op['scope'], next(o for o in snapshot['objects'] if o['id'] == op['scope']['object_id']))
        moving = {i for i, w in weights.items() if w}
        rows = []
        for attachment in attachments:
            if attachment['surface_object_id'] != op['scope']['object_id']:
                continue
            row = copy.deepcopy(attachment)
            row['bindings'] = [b for b in row['bindings'] if moving.intersection(b['surface_vertex_ids'])]
            if row['bindings']:
                rows.append(row)
        # Never trust caller-supplied attachment authority.
        op['marking_attachments'] = rows
    return result


def require_bound(snapshot, operations):
    """Legacy confirmations cannot silently retain the old keep-lettering-fixed behavior."""
    if any('marking_attachments' not in op for op in operations) and discover(snapshot, operations):
        _error('this older understanding did not list surface markings; create a new understanding before editing')


def summary(operations):
    rows = {}
    for op in operations:
        for attachment in op.get('marking_attachments', []):
            oid = attachment['object_id']
            row = rows.setdefault(oid, {k: copy.deepcopy(attachment[k]) for k in
                ('object_id', 'object_name', 'surface_object_id', 'surface_object_name', 'method', 'source_standoff_mm')})
            row.setdefault('operation_ids', []).append(op['id'])
    return list(rows.values())


def support(indexed, operations):
    """Exact additional moving vertices and incident faces, from sealed bindings."""
    vertices, faces = {}, {}
    for op in operations:
        for attachment in op.get('marking_attachments', []):
            oid = attachment['object_id']
            obj = indexed[oid]
            ids = {row['vertex_id'] for row in attachment['bindings']}
            vertices.setdefault(oid, set()).update(ids)
            faces.setdefault(oid, set()).update(i for i, p in enumerate(obj['polygons']) if ids.intersection(p))
    return vertices, faces


def _frames(triangles):
    x = triangles[:, 1]-triangles[:, 0]
    n = np.cross(x, triangles[:, 2]-triangles[:, 0])
    xl, nl = np.linalg.norm(x, axis=1), np.linalg.norm(n, axis=1)
    if np.any(xl <= 1e-10) or np.any(nl <= 1e-10):
        _error('a marking attachment triangle collapsed')
    x, n = x/xl[:, None], n/nl[:, None]
    return np.stack([x, np.cross(n, x), n], axis=2)


def follow(indexed, world, operation, before_surface, after_surface):
    """Advance only dependent vertices; unchanged attachment triangles stay exact."""
    for attachment in operation.get('marking_attachments', []):
        oid = attachment['object_id']
        surface = indexed[operation['scope']['object_id']]
        mark = indexed[oid]
        if (attachment['surface_object_id'] != surface['id'] or
                attachment['source_topology_sha256'] != mark['topology_sha256'] or
                attachment['surface_topology_sha256'] != surface['topology_sha256']):
            _error('the sealed marking or surface topology changed')
        bindings = attachment['bindings']
        ids = np.asarray([b['vertex_id'] for b in bindings], int)
        tri = np.asarray([b['surface_vertex_ids'] for b in bindings], int)
        bary = np.asarray([b['barycentric'] for b in bindings], float)
        a, b = before_surface[tri], after_surface[tri]
        changed = np.any(a != b, axis=(1, 2))
        if not changed.any():
            continue
        ids, bary, a, b = ids[changed], bary[changed], a[changed], b[changed]
        old_frame, new_frame = _frames(a), _frames(b)
        foot = np.einsum('ni,nij->nj', bary, a)
        offset = world[oid][ids]-foot
        local = np.einsum('ni,nij->nj', offset, old_frame)
        transported = np.einsum('nij,nj->ni', new_frame, local)
        world[oid][ids] = np.einsum('ni,nij->nj', bary, b)+transported


def validate_marking(obj, before, after):
    """Open decals stay open; solid lettering keeps the existing solid checks."""
    from . import owner_mesh as M
    if not np.isfinite(after).all():
        _error('marking positions must be finite')
    tri = np.asarray(obj['triangles'], int)
    a, b = np.asarray(before)[tri], np.asarray(after)[tri]
    na, nb = np.cross(a[:, 1]-a[:, 0], a[:, 2]-a[:, 0]), np.cross(b[:, 1]-b[:, 0], b[:, 2]-b[:, 0])
    if np.any((np.linalg.norm(na, axis=1) > 1e-10) & (np.linalg.norm(nb, axis=1) <= 1e-10)):
        _error('surface-following would collapse a marking triangle')
    if _open(obj):
        return {'open_surface_marking': True, 'topology_preserved': True}
    return M.physical(obj, after)


def _pierced(mark_vertices, mark_triangles, surface_vertices, surface_triangles):
    """Marking triangles whose edges cross a surface (or vice versa), excluding coplanar contact."""
    marks = np.asarray(mark_vertices, float)[np.asarray(mark_triangles, int)]
    surface = np.asarray(surface_vertices, float)[np.asarray(surface_triangles, int)]
    lo, hi = surface.min(1), surface.max(1)
    pierced = set()

    def crosses(starts, ends, triangles):
        edge1, edge2 = triangles[:, 1]-triangles[:, 0], triangles[:, 2]-triangles[:, 0]
        direction = ends-starts
        cross = np.cross(direction, edge2)
        det = np.einsum('ij,ij->i', edge1, cross)
        good = abs(det) > 1e-10
        inv = np.divide(1., det, out=np.zeros_like(det), where=good)
        delta = starts-triangles[:, 0]
        u = inv*np.einsum('ij,ij->i', delta, cross)
        q = np.cross(delta, edge1)
        v = inv*np.einsum('ij,ij->i', direction, q)
        t = inv*np.einsum('ij,ij->i', edge2, q)
        return good & (u >= -1e-8) & (v >= -1e-8) & (u+v <= 1+1e-8) & (t > 1e-8) & (t < 1-1e-8)

    for i, mark in enumerate(marks):
        ids = np.flatnonzero(np.all(hi >= mark.min(0), axis=1) & np.all(lo <= mark.max(0), axis=1))
        if not len(ids):
            continue
        triangles = surface[ids]
        for k in range(3):
            if (crosses(np.broadcast_to(mark[k], (len(ids), 3)),
                        np.broadcast_to(mark[(k+1) % 3], (len(ids), 3)), triangles).any() or
                    crosses(triangles[:, k], triangles[:, (k+1) % 3], np.broadcast_to(mark, triangles.shape)).any()):
                pierced.add(i)
                break
    return pierced


def surface_checks(source, world, operations):
    """Refuse newly buried vertices or new marking/surface triangle intersections.

    This is a geometric attachment check, not a guarantee of legibility in every
    camera. Full-size after images still show other-object occlusion and lighting.
    """
    from . import owner_mesh as M
    indexed = M.objects(source)
    reports = []
    for row in summary(operations):
        mark, surface = indexed[row['object_id']], indexed[row['surface_object_id']]
        old_mark = np.asarray(mark['vertices_world_mm'], float)
        new_mark = world.get(mark['id'], old_mark)
        old_surface = np.asarray(surface['vertices_world_mm'], float)
        new_surface = world.get(surface['id'], old_surface)
        # Restrict only by the query boxes and the original maximum attachment
        # distance. Distant triangles cannot bury a near-surface marking.
        tri = np.asarray(surface['triangles'], int)
        xyz = np.asarray(new_surface)[tri]
        lo, hi = np.asarray(new_mark).min(0)-measure.DECAL_NEAR_MM, np.asarray(new_mark).max(0)+measure.DECAL_NEAR_MM
        nearby = np.flatnonzero(np.all(xyz.max(1) >= lo, axis=1) & np.all(xyz.min(1) <= hi, axis=1))
        if not len(nearby):
            _error(f"marking {mark['name']} no longer lies on its edited surface")
        old_sd = np.asarray([r[3] for r in nearest(old_mark, old_surface, tri[nearby])])
        new_sd = np.asarray([r[3] for r in nearest(new_mark, new_surface, tri[nearby])])
        buried = np.flatnonzero((old_sd > M.POSITION_TOL_MM) & (new_sd < -M.POSITION_TOL_MM))
        old_cross = _pierced(old_mark, mark['triangles'], old_surface, tri)
        new_cross = _pierced(new_mark, mark['triangles'], new_surface, tri)
        if len(buried) or new_cross-old_cross:
            _error(f"marking {mark['name']} would become buried by its edited surface; revise the operation")
        reports.append({'object_id': mark['id'], 'object_name': mark['name'],
                        'surface_object_id': surface['id'], 'newly_buried_vertices': 0,
                        'new_surface_intersections': 0, 'existing_surface_intersections': len(old_cross),
                        'after_standoff_mm': [float(new_sd.min()), float(new_sd.max())]})
    return reports
