"""Finite edits to captured meshes, with no fitting or product-specific geometry.

The caller authenticates the owner confirmation. This pure module validates its finite
selection, computes the exact displacement, and checks actual closed graph solids.
Photo readings are observations to report; they are never silently rewritten here.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math

import numpy as np

from .temple.build import mesh_topology
from .temple.spec import DEFAULT_OPTIONS
from . import owner_surface

OPERATION_KINDS = frozenset({'translate_region', 'rotate_region', 'taper_region'}) | owner_surface.OPERATION_KINDS

POSITION_TOL_MM = 2e-5  # float32 Blender positions; never a thickness allowance
AREA_EPS_MM2 = 1e-10


class OwnerMeshError(ValueError):
    def __init__(self, code, message, **facts):
        super().__init__(message)
        self.code, self.facts = code, facts

    def result(self):
        return {"status": "refused", "code": self.code, "message": str(self), **self.facts}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()


def objects(snapshot):
    if snapshot.get("schema") != "g3d.mesh_snapshot/1":
        raise OwnerMeshError("E_OWNER_SNAPSHOT", "unsupported actual-mesh snapshot")
    rows = snapshot.get("objects")
    if not isinstance(rows, list) or len({r.get("id") for r in rows}) != len(rows):
        raise OwnerMeshError("E_OWNER_SNAPSHOT", "snapshot object identities are not distinct")
    return {r["id"]: r for r in rows}


def _array(value, shape, label):
    try:
        result = np.asarray(value, dtype=float)
    except (TypeError, ValueError):
        raise OwnerMeshError("E_OWNER_ARGS", f"{label} must be finite numbers") from None
    if result.shape != shape or not np.isfinite(result).all():
        raise OwnerMeshError("E_OWNER_ARGS", f"{label} must have shape {shape} with finite numbers")
    return result


def scope_vertices(scope, obj):
    """Validate an already authenticated support; never grow it from adjacency."""
    count = len(obj["vertices_world_mm"])
    weights = {}
    for row in scope.get("vertex_weights", []):
        vid, w = row.get("vertex_id"), row.get("weight")
        if type(vid) is not int or not 0 <= vid < count or vid in weights:
            raise OwnerMeshError("E_OWNER_SCOPE", "vertex support is invalid or repeated")
        if isinstance(w, bool) or not isinstance(w, (int, float)) or not math.isfinite(w) or not 0 <= w <= 1:
            raise OwnerMeshError("E_OWNER_SCOPE", "support weights must be finite, from zero through one")
        weights[vid] = float(w)
    if not weights or not any(weights.values()):
        raise OwnerMeshError("E_OWNER_SCOPE", "the confirmed operation has no moving support")
    fixed = scope.get("fixed_vertex_ids", [])
    if any(type(i) is not int or not 0 <= i < count for i in fixed):
        raise OwnerMeshError("E_OWNER_SCOPE", "fixed vertex identity is invalid")
    if any(weights.get(i, 0) != 0 for i in fixed):
        raise OwnerMeshError("E_OWNER_SCOPE", "a fixed vertex also has nonzero movement weight")
    moving = {i for i, w in weights.items() if w}
    affected = {i for i, p in enumerate(obj["polygons"]) if moving.intersection(p)}
    if affected != set(scope.get("affected_polygon_ids", [])):
        raise OwnerMeshError("E_OWNER_SCOPE", "the confirmed affected faces do not equal the actual support adjacency",
                             actual_affected_polygon_ids=sorted(affected))
    return weights, affected


def displaced(vertices, operation, obj):
    """Apply exactly one sealed operation; result amounts are never clamped."""
    scope, params = operation["scope"], operation["params"]
    if scope.get("object_id") != obj["id"]:
        raise OwnerMeshError("E_OWNER_SCOPE", "operation names another object")
    weights, affected = scope_vertices(scope, obj)
    ids = np.array(sorted(weights), dtype=int)
    w = np.array([weights[i] for i in ids])
    source = vertices[ids]
    kind = operation["kind"]
    if kind == "translate_region":
        if set(params) != {"delta_mm"}:
            raise OwnerMeshError("E_OWNER_ARGS", "translation takes only delta_mm")
        change = np.broadcast_to(_array(params["delta_mm"], (3,), "delta_mm"), source.shape).copy()
    elif kind in ("rotate_region", "taper_region"):
        required = {"axis_points_mm", "delta_deg" if kind == "rotate_region" else "delta_mm"}
        if set(params) != required:
            raise OwnerMeshError("E_OWNER_ARGS", "operation parameters differ from its declared kind")
        line = _array(params["axis_points_mm"], (2, 3), "axis_points_mm")
        vector = line[1] - line[0]
        length = np.linalg.norm(vector)
        if length <= 1e-8:
            raise OwnerMeshError("E_OWNER_ARGS", "operation axis has no length")
        axis = vector / length
        if kind == "rotate_region":
            amount = params["delta_deg"]
            if isinstance(amount, bool) or not isinstance(amount, (int, float)) or not math.isfinite(amount):
                raise OwnerMeshError("E_OWNER_ARGS", "rotation angle must be finite")
            r = math.radians(amount)
            v = source - line[0]
            rotated = v * math.cos(r) + np.cross(axis, v) * math.sin(r) + \
                (v @ axis)[:, None] * axis * (1 - math.cos(r))
            change = rotated - v
        else:
            along = np.clip((source - line[0]) @ axis / length, 0, 1)
            change = along[:, None] * _array(params["delta_mm"], (3,), "delta_mm")
    else:
        raise OwnerMeshError("E_OWNER_ARGS", f"unknown owner operation {kind!r}")
    after = vertices.copy()
    after[ids] += change * w[:, None]
    return after, affected


def normal_faces(obj, affected):
    """Conservative normal-only fan influence; does not authorize any extra vertex movement."""
    vertices = {v for p in affected for v in obj["polygons"][p]}
    return {p for p, ids in enumerate(obj["polygons"]) if vertices.intersection(ids)}


def _cross(a, b):
    return a[..., 0]*b[..., 1] - a[..., 1]*b[..., 0]


def _intersection(subject, clip):
    """Intersection of two CCW triangles; vertices suffice for a linear thickness minimum."""
    result = list(subject)
    for a, b in zip(clip, np.roll(clip, -1, axis=0)):
        if not result:
            break
        old, result = result, []
        previous = old[-1]
        dp = float(_cross(b-a, previous-a))
        for current in old:
            dc = float(_cross(b-a, current-a))
            if (dc >= 0) != (dp >= 0):
                result.append(previous + (current-previous) * (dp/(dp-dc)))
            if dc >= 0:
                result.append(current)
            previous, dp = current, dc
    return np.asarray(result).reshape(-1, 2)


def _area(poly):
    return abs(float(np.sum(_cross(poly, np.roll(poly, -1, axis=0)))))/2 if len(poly) >= 3 else 0.


def graph_thickness(vertices, triangles, side, minimum_mm):
    """Exact PL graph difference on every positive-area outer/inner overlap polygon.

    Each projected triangle must be covered exactly once by the opposite surface.
    This verifies the same graph-solid premise used by the temple builder; it does
    not substitute sparse rays or a stale fitted specification for actual geometry.
    """
    if side not in (-1, 1) or not math.isfinite(minimum_mm) or minimum_mm <= 0:
        raise OwnerMeshError("E_OWNER_PHYSICAL", "a temple graph needs its signed side and positive t_min")
    v = np.c_[-vertices[:, 2], vertices[:, 1], side*vertices[:, 0]]
    tri = np.asarray(triangles, dtype=int)
    if side < 0:
        tri = tri[:, ::-1]
    points = v[tri]
    area2 = _cross(points[:, 1, :2]-points[:, 0, :2], points[:, 2, :2]-points[:, 0, :2])
    outer, inner = np.flatnonzero(area2 > AREA_EPS_MM2), np.flatnonzero(area2 < -AREA_EPS_MM2)
    if not len(outer) or not len(inner):
        raise OwnerMeshError("E_OWNER_GRAPH", "the actual mesh has no two opposing graph surfaces")
    p, q = points[outer], points[inner][:, ::-1]
    p_area, q_area = abs(area2[outer])/2, abs(area2[inner])/2
    pmn, pmx = p[:, :, :2].min(1), p[:, :, :2].max(1)
    qmn, qmx = q[:, :, :2].min(1), q[:, :, :2].max(1)
    # A spatial grid is only an exact candidate accelerator; every touching cell is visited.
    spans = np.r_[pmx-pmn, qmx-qmn]
    cell = max(float(np.median(np.max(spans, axis=1))), .1)
    bins = {}
    for k, (lo, hi) in enumerate(zip(qmn, qmx)):
        a, b = np.floor(lo/cell).astype(int), np.floor(hi/cell).astype(int)
        for x in range(a[0], b[0]+1):
            for y in range(a[1], b[1]+1):
                bins.setdefault((x, y), []).append(k)
    pcoef = np.linalg.solve(np.concatenate([np.ones((*p.shape[:2], 1)), p[:, :, :2]], axis=2), p[:, :, 2, None])[..., 0]
    qcoef = np.linalg.solve(np.concatenate([np.ones((*q.shape[:2], 1)), q[:, :, :2]], axis=2), q[:, :, 2, None])[..., 0]
    covered_p, covered_q = np.zeros(len(p)), np.zeros(len(q))
    smallest, where, overlaps = math.inf, None, 0
    for i, (lo, hi) in enumerate(zip(pmn, pmx)):
        a, b = np.floor(lo/cell).astype(int), np.floor(hi/cell).astype(int)
        candidates = set()
        for x in range(a[0], b[0]+1):
            for y in range(a[1], b[1]+1):
                candidates.update(bins.get((x, y), []))
        for j in sorted(candidates):
            if np.any(lo > qmx[j]) or np.any(hi < qmn[j]):
                continue
            poly = _intersection(p[i, :, :2], q[j, :, :2])
            area = _area(poly)
            if area <= AREA_EPS_MM2:
                continue
            covered_p[i] += area
            covered_q[j] += area
            values = np.c_[np.ones(len(poly)), poly] @ (pcoef[i]-qcoef[j])
            k = int(np.argmin(values))
            if values[k] < smallest:
                smallest, where = float(values[k]), poly[k].tolist()
            overlaps += 1
    # Arithmetic tolerance scales by triangle area, not physical thickness.
    mismatch_p = abs(covered_p-p_area) > np.maximum(1e-7, p_area*1e-7)
    mismatch_q = abs(covered_q-q_area) > np.maximum(1e-7, q_area*1e-7)
    if np.any(mismatch_p) or np.any(mismatch_q):
        raise OwnerMeshError("E_OWNER_GRAPH", "the actual surfaces do not form one non-overlapping pair of graphs",
                             outer_uncovered_or_overlapping=int(mismatch_p.sum()),
                             inner_uncovered_or_overlapping=int(mismatch_q.sum()),
                             outer_triangle_ids=outer[mismatch_p].tolist(), inner_triangle_ids=inner[mismatch_q].tolist(),
                             outer_area_error_mm2=(covered_p-p_area)[mismatch_p].tolist(),
                             inner_area_error_mm2=(covered_q-q_area)[mismatch_q].tolist(),
                             minimum_graph_difference_mm=smallest)
    if smallest < minimum_mm:
        raise OwnerMeshError("E_TOO_THIN", "the actual edited mesh is below its minimum graph thickness",
                             thickness_mm=smallest, t_min_mm=minimum_mm, at_d_y_mm=where)
    return {"minimum_mm": smallest, "t_min_mm": minimum_mm, "at_d_y_mm": where,
            "overlap_polygons": overlaps, "method": "exact piecewise-linear graph intersection"}


def paired_thickness(obj, vertices, minimum_mm, reference):
    """Use verified builder pairs only when their actual projected chart is unchanged.

    Blender may triangulate a nearly collinear polygon into float32 slivers. Such
    triangles cannot identify a surface by their area sign. The verified builder
    topology stores corresponding outer/inner vertices; with exactly unchanged
    Y/Z, checking their X differences is the builder's original linear thickness
    check, and the edit cannot introduce a projected fold or overlap.
    """
    old = np.asarray(reference["vertices_world_mm"], float)
    n = len(vertices)//2
    side = obj.get("side")
    if (len(vertices) != 2*n or side not in (-1, 1) or old.shape != vertices.shape
            or not np.array_equal(vertices[:, 1:], old[:, 1:])
            or not np.array_equal(vertices[:n, 1:], vertices[n:, 1:])):
        return None
    thickness = side*(vertices[:n, 0]-vertices[n:, 0])
    k = int(np.argmin(thickness))
    if thickness[k] < minimum_mm:
        raise OwnerMeshError("E_TOO_THIN", "the actual edited mesh is below its minimum graph thickness",
                             thickness_mm=float(thickness[k]), t_min_mm=minimum_mm, vertex_id=k)
    return {"minimum_mm": float(thickness[k]), "t_min_mm": minimum_mm, "vertex_id": k,
            "method": "verified actual outer/inner pairs; exact unchanged Y/Z chart",
            "projected_chart_unchanged": True}


def physical(obj, vertices=None, *, reference=None):
    vertices = np.asarray(obj["vertices_world_mm"] if vertices is None else vertices, dtype=float)
    if vertices.ndim != 2 or vertices.shape[1] != 3 or not np.isfinite(vertices).all():
        raise OwnerMeshError("E_OWNER_PHYSICAL", "mesh positions are not finite triples")
    polys, tris = obj["polygons"], np.asarray(obj["triangles"], dtype=int)
    if tris.ndim != 2 or tris.shape[1] != 3 or np.any(tris < 0) or np.any(tris >= len(vertices)):
        raise OwnerMeshError("E_OWNER_PHYSICAL", "mesh triangle indices are invalid")
    topo = mesh_topology(polys)
    edges = {(min(a, b), max(a, b)) for a, b in topo["directed"]}
    used = {v for p in polys for v in p}
    euler = len(used)-len(edges)+len(polys)
    a, b, c = vertices[tris[:, 0]], vertices[tris[:, 1]], vertices[tris[:, 2]]
    volume = float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum()/6)
    if topo["open"] or topo["dup"] or volume <= 0:
        raise OwnerMeshError("E_NOT_CLOSED", "the actual mesh is not a closed oriented positive solid",
                             open_edges=len(topo["open"]), duplicate_edges=len(topo["dup"]),
                             euler=euler, volume_mm3=volume)
    # A positive total must not hide a separately reversed component.
    parent = list(range(len(vertices)))
    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for p in polys:
        for i in p[1:]:
            parent[find(i)] = find(p[0])
    component_volumes = {}
    amounts = np.einsum("ij,ij->i", a, np.cross(b, c))/6
    for triangle, amount in zip(tris, amounts):
        key = find(int(triangle[0]))
        component_volumes[key] = component_volumes.get(key, 0.) + float(amount)
    if any(v <= 0 for v in component_volumes.values()):
        raise OwnerMeshError("E_NOT_CLOSED", "a physical mesh component is reversed or has zero volume",
                             component_volumes_mm3=list(component_volumes.values()))
    aliases = obj.get("aliases", {})
    if obj.get("role") == "temple" and aliases.get("status") == "verified_topology":
        if euler != 2:
            raise OwnerMeshError("E_NOT_CLOSED", "the captured temple construction requires Euler characteristic 2")
        tmin = aliases.get("options", {}).get("t_min_mm", DEFAULT_OPTIONS["t_min_mm"])
        thickness = paired_thickness(obj, vertices, float(tmin), reference or obj)
        if thickness is None:
            thickness = graph_thickness(vertices, tris, obj.get("side"), float(tmin))
    else:
        thickness = {"status": "not_applicable", "reason": "No verified temple graph construction; no new graph or genus rule imposed on other parts"}
    return {"closed": True, "euler": euler, "volume_mm3": volume,
            "component_volumes_mm3": list(component_volumes.values()), "thickness": thickness}


def plan(snapshot, operations):
    """Compute a local patch from captured raw topology; input arrays remain untouched."""
    if owner_surface.active(operations):
        target, reports = owner_surface.state(snapshot, operations)
        return {'schema': 'g3d.owner_surface_patch/1', 'snapshot_sha256': digest(snapshot),
                'target': target, 'operations': reports}
    indexed = objects(snapshot)
    if not operations or len({r.get("id") for r in operations}) != len(operations):
        raise OwnerMeshError("E_OWNER_ARGS", "operation IDs must be nonempty and distinct")
    positions, affected, receipts = deformation(snapshot, operations)
    output = []
    from . import owner_markings as markings
    marking_ids = set(markings.support(indexed, operations)[0])
    for oid, xyz in positions.items():
        obj = indexed[oid]
        initial = np.asarray(obj["vertices_world_mm"], dtype=float)
        checks = (markings.validate_marking(obj, initial, xyz) if oid in marking_ids else physical(obj, xyz))
        changed = np.flatnonzero(np.any(xyz != initial, axis=1))
        output.append({"object_id": oid, "object_name": obj["name"],
                       "source_topology_sha256": obj["topology_sha256"],
                       "source_positions_sha256": obj["positions_sha256"],
                       "vertex_ids": changed.tolist(), "positions_world_mm": xyz[changed].tolist(),
                       "affected_polygon_ids": sorted(affected[oid]),
                       "normal_affected_polygon_ids": sorted(normal_faces(obj, affected[oid])), "physical": checks})
    return {"schema": "g3d.owner_mesh_patch/1", "snapshot_sha256": digest(snapshot),
            "patches": output, "operations": receipts, "following_markings": markings.summary(operations),
            "marking_surface_checks": markings.surface_checks(snapshot, positions, operations)}


def deformation(snapshot, operations):
    """Replay geometry and its sealed marking dependencies without changing source arrays."""
    from . import owner_markings as markings
    markings.require_bound(snapshot, operations)
    indexed = objects(snapshot)
    positions = {oid: np.asarray(obj['vertices_world_mm'], float).copy() for oid, obj in indexed.items()}
    dependent, affected = markings.support(indexed, operations)
    touched, receipts = set(dependent), []
    for op in operations:
        oid = op["scope"]["object_id"]
        if oid not in indexed:
            raise OwnerMeshError("E_OWNER_SCOPE", "confirmed object is absent from the actual snapshot")
        obj = indexed[oid]
        ev = obj.get("evaluated", {})
        users = obj.get("shared_mesh_users", 1)
        users = len(users) if isinstance(users, list) else users
        if not ev.get("topology_matches_source") or not ev.get("positions_match_source") or users != 1:
            raise OwnerMeshError("E_OWNER_CORRESPONDENCE", "this object has no unique editable source/evaluated mesh correspondence")
        initial = np.asarray(obj["vertices_world_mm"], dtype=float)
        old = positions[oid]
        new, faces = displaced(old, op, obj)
        markings.follow(indexed, positions, op, old, new)
        positions[oid] = new
        touched.add(oid)
        affected.setdefault(oid, set()).update(faces)
        distance = np.linalg.norm(new-old, axis=1)
        receipts.append({"id": op["id"], "kind": op["kind"], "target_ids": op.get("target_ids", []),
                         "params": copy.deepcopy(op["params"]), "object_id": oid,
                         "changed_vertex_ids": np.flatnonzero(distance > 0).tolist(),
                         "affected_polygon_ids": sorted(faces), "maximum_move_mm": float(distance.max()),
                         "marking_attachments": copy.deepcopy(op.get('marking_attachments', []))})
    return {oid: positions[oid] for oid in sorted(touched)}, affected, receipts


def between(source, target, operations):
    """Transfer a verified private child's actual local mesh, never a reconstructed specification."""
    old, new = objects(source), objects(target)
    if set(old) != set(new) or source["origin_mm"] != target["origin_mm"]:
        raise OwnerMeshError("E_OWNER_SCOPE", "private mesh objects or origin differ")
    from . import owner_markings as markings
    markings.require_bound(source, operations)
    allowed, affected = markings.support(old, operations)
    marking_ids = set(allowed)
    for op in operations:
        oid = op["scope"]["object_id"]
        weights, faces = scope_vertices(op["scope"], old[oid])
        allowed.setdefault(oid, set()).update(i for i, w in weights.items() if w)
        affected.setdefault(oid, set()).update(faces)
    patches = []
    for oid, obj in old.items():
        final = new[oid]
        if oid not in allowed:
            if obj != final:
                raise OwnerMeshError("E_OWNER_SCOPE", "private stage changed an unrequested object")
            continue
        if obj["topology_sha256"] != final["topology_sha256"] or obj["polygons"] != final["polygons"]:
            raise OwnerMeshError("E_OWNER_SCOPE", "private stage changed captured topology")
        a, b = np.asarray(obj["vertices_world_mm"]), np.asarray(final["vertices_world_mm"])
        if a.shape != b.shape:
            raise OwnerMeshError("E_OWNER_SCOPE", "private stage changed vertex count")
        ids = np.flatnonzero(np.any(a != b, axis=1))
        if set(ids)-allowed[oid]:
            raise OwnerMeshError("E_OWNER_SCOPE", "private stage moved vertices outside confirmed support")
        checks = (markings.validate_marking(final, a, b) if oid in marking_ids else physical(final, reference=obj))
        if len(ids) or obj["corner_normals"] != final["corner_normals"]:
            patches.append({"object_id": oid, "object_name": obj["name"],
                "vertex_ids": ids.tolist(), "positions_world_mm": b[ids].tolist(),
                "positions_local": np.asarray(final["vertices_local"])[ids].tolist(),
                "affected_polygon_ids": sorted(affected[oid]),
                "normal_affected_polygon_ids": sorted(normal_faces(obj, affected[oid])),
                "physical": checks})
    return {"schema": "g3d.owner_mesh_patch/1", "snapshot_sha256": digest(source),
            "target_snapshot_sha256": digest(target), "patches": patches, "operations": []}
