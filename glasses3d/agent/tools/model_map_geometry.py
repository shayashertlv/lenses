"""Actual-mesh map selectors and frozen-camera projections. No scene mutations."""
from __future__ import annotations

from collections import defaultdict
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from scipy.spatial import cKDTree


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _normal(vertices):
    n = np.cross(vertices, np.roll(vertices, -1, axis=0)).sum(axis=0)
    length = np.linalg.norm(n)
    return n / length if length > 1e-12 else np.zeros(3)


def _chains(edges, ids):
    by_vertex = defaultdict(list)
    for e in ids:
        for v in edges[e]:
            by_vertex[v].append(e)
    todo, out = set(ids), []
    while todo:
        starts = sorted(v for v, es in by_vertex.items() if len(es) != 2 and any(e in todo for e in es))
        start = starts[0] if starts else min(edges[min(todo)])
        v, chain, vertices = start, [], [start]
        while True:
            choices = sorted(e for e in by_vertex[v] if e in todo)
            if not choices:
                break
            e = choices[0]
            todo.remove(e)
            chain.append(e)
            a, b = edges[e]
            v = b if v == a else a
            vertices.append(v)
            if v == start or len(by_vertex[v]) != 2:
                break
        out.append((chain, vertices))
    return out


def catalogue(snapshot):
    """Discover regions, shared boundary chains, and physical corners without hand-picked indices.

    Coplanar tolerance is a displayed segmentation choice, never a modelling/finish gate.
    Verified specialist polygon lineage gives names even after the actual vertices moved.
    """
    targets, objects = {}, {}
    counts = defaultdict(int)

    def add(kind, obj, **values):
        prefix = {"object": "O", "face_region": "F", "edge_chain": "E", "point": "P"}[kind]
        if kind == "object":
            key = obj["id"]
        else:
            counts[prefix] += 1
            key = f"{prefix}{counts[prefix]:04d}"
        targets[key] = {"id": key, "kind": kind, "object_id": obj["id"], "object_name": obj["name"],
                        "side": obj["side"], "source_editable": bool(obj["evaluated"]["positions_match_source"] and
                            not obj.get("unsupported_edit_state")), **values}
        return key

    for obj in snapshot["objects"]:
        oid, polygons, edges = obj["id"], obj["polygons"], obj["edges"]
        v = np.asarray(obj["vertices_world_mm"], float)
        objects[oid] = {k: obj[k] for k in ("id", "name", "mesh_name", "role", "side", "topology_sha256", "positions_sha256")}
        edge_index = {tuple(sorted(edge)): i for i, edge in enumerate(edges)}
        vp, ep = defaultdict(set), defaultdict(list)
        for p, poly in enumerate(polygons):
            for x in poly:
                vp[x].add(p)
            for a, b in zip(poly, poly[1:] + poly[:1]):
                ep[edge_index[tuple(sorted((a, b)))]] .append(p)
        aliases = obj.get("aliases", {})
        regions, assigned = [], set()
        if aliases.get("status") == "verified_topology":
            for name, ids in sorted(aliases.get("facets", {}).items()):
                regions.append((sorted(ids), [name]))
                assigned.update(ids)
        normals = np.asarray([_normal(v[poly]) for poly in polygons])
        adjacency = defaultdict(set)
        for pairs in ep.values():
            for p in pairs:
                adjacency[p].update(set(pairs) - {p})
        for seed in range(len(polygons)):
            if seed in assigned:
                continue
            group, queue = [], [seed]
            assigned.add(seed)
            n, origin = normals[seed], v[polygons[seed][0]]
            while queue:
                p = queue.pop()
                group.append(p)
                for q in sorted(adjacency[p]):
                    if q in assigned:
                        continue
                    if (np.dot(n, normals[q]) >= 1 - 1e-8 and
                            np.max(np.abs((v[polygons[q]] - origin) @ n)) <= 1e-4):
                        assigned.add(q)
                        queue.append(q)
            regions.append((sorted(group), []))
        regions.sort(key=lambda row: min(row[0]))
        pgroup, face_ids = {}, []
        for ps, names in regions:
            pset = set(ps)
            vids = sorted({x for p in ps for x in polygons[p]})
            eids = sorted({edge_index[tuple(sorted((a, b)))] for p in ps
                           for a, b in zip(polygons[p], polygons[p][1:] + polygons[p][:1])})
            boundary = [e for e in eids if len(ep[e]) != 2 or any(p not in pset for p in ep[e])]
            # Adjacent faces are factual influence of shared vertices, not permission to move them.
            adjacent = sorted(set().union(*(vp[x] for x in vids)) - set(ps))
            fid = add("face_region", obj, polygon_ids=ps, vertex_ids=vids, edge_ids=eids,
                      boundary_edge_ids=boundary, adjacent_polygon_ids=adjacent, aliases=names,
                      centroid_mm=v[vids].mean(0).tolist(), normal=normals[ps].mean(0).tolist())
            face_ids.append(fid)
            pgroup.update({p: fid for p in ps})
        boundary_groups = defaultdict(list)
        for e, ps in ep.items():
            groups = tuple(sorted({pgroup[p] for p in ps}))
            if len(groups) != 1 or len(ps) != 2:
                boundary_groups[groups].append(e)
        point_ids, edge_ids = set(), []
        point_names = defaultdict(list)
        for name, vi in aliases.get("points", {}).items():
            point_names[int(vi)].append(name)
            point_ids.add(int(vi))
        for groups, eids in sorted(boundary_groups.items()):
            for chain, ordered in _chains(edges, eids):
                endpoints = [ordered[0], ordered[-1]]
                point_ids.update(endpoints)
                names = sorted({a for f in groups for a in targets[f]["aliases"]})
                alias = ["|".join(names)] if len(names) > 1 else []
                endpoint_names = ["/".join(sorted(point_names.get(i, []))) for i in endpoints]
                if all(endpoint_names):
                    alias.append("-".join(endpoint_names))
                eid = add("edge_chain", obj, edge_ids=chain, vertex_ids=sorted(set(ordered)),
                          ordered_vertex_ids=ordered, endpoint_vertex_ids=endpoints,
                          polygon_ids=sorted({p for e in chain for p in ep[e]}),
                          adjacent_polygon_ids=sorted(set().union(*(vp[x] for x in ordered))),
                          face_ids=list(groups), aliases=alias, centroid_mm=v[ordered].mean(0).tolist())
                edge_ids.append(eid)
        points = []
        for vi in sorted(point_ids):
            points.append(add("point", obj, vertex_ids=[vi], polygon_ids=sorted(vp[vi]), edge_ids=[],
                              adjacent_polygon_ids=sorted(vp[vi]), aliases=sorted(point_names.get(vi, [])),
                              centroid_mm=v[vi].tolist()))
        add("object", obj, vertex_ids=list(range(len(v))), polygon_ids=list(range(len(polygons))),
            edge_ids=list(range(len(edges))), adjacent_polygon_ids=[], aliases=[obj["name"]],
            face_ids=face_ids, boundary_ids=edge_ids, point_ids=points, centroid_mm=v.mean(0).tolist())
    return {"objects": objects, "targets": targets,
            "segmentation": {"coplanar_distance_mm": 1e-4, "normal_dot_min": 1 - 1e-8,
                             "note": "Segmentation only; all raw elements preserved in the snapshot. Historical aliases require exact connectivity."}}


def support(snapshot, targets, target_ids, *, vertex_weights=None, fixed_vertex_ids=()):
    if not target_ids:
        raise ValueError("Support selection is empty; select actual catalogue target ids")
    missing = [key for key in target_ids if key not in targets]
    if missing:
        raise ValueError("Support names unknown catalogue target ids: " + ", ".join(missing))
    chosen = [targets[key] for key in target_ids]
    if len({t["object_id"] for t in chosen}) != 1:
        raise ValueError("Support targets must belong to one actual object")
    if any(not t["source_editable"] for t in chosen):
        raise ValueError("Support requires editable targets with source mesh correspondence")
    obj = next(o for o in snapshot["objects"] if o["id"] == chosen[0]["object_id"])
    allowed = set().union(*(set(t["vertex_ids"]) for t in chosen))
    fixed = sorted(set(fixed_vertex_ids))
    weights = [{"vertex_id": v, "weight": 0.0 if v in fixed else 1.0} for v in sorted(allowed)] if vertex_weights is None else vertex_weights
    ids = [w["vertex_id"] for w in weights]
    if len(ids) != len(set(ids)) or any(type(v) is not int or v not in allowed for v in ids):
        raise ValueError("Support cannot add vertices beyond its displayed targets")
    if any(not np.isfinite(w["weight"]) or not 0 <= w["weight"] <= 1 for w in weights):
        raise ValueError("Support weights must be finite in [0,1]")
    if any(type(v) is not int or v not in allowed for v in fixed):
        raise ValueError("Fixed vertices must belong to the displayed support")
    if any(w["vertex_id"] in fixed and w["weight"] != 0 for w in weights):
        raise ValueError("Fixed vertices must have zero support weight")
    moved = {w["vertex_id"] for w in weights if w["weight"] > 0} - set(fixed)
    affected = [i for i, p in enumerate(obj["polygons"]) if moved.intersection(p)]
    normal_vertices = {v for i in affected for v in obj["polygons"][i]}
    normal_affected = [i for i, p in enumerate(obj["polygons"]) if normal_vertices.intersection(p)]
    return {"object_id": obj["id"], "vertex_weights": sorted(weights, key=lambda w: w["vertex_id"]),
            "fixed_vertex_ids": fixed, "affected_polygon_ids": affected,
            "normal_affected_polygon_ids": normal_affected, "target_ids": list(target_ids)}


def export_correspondence(snapshot, geometry):
    """Map actual exported triangles to raw polygon indices by unique coordinates and connectivity."""
    by_name = defaultdict(list)
    export_nodes = defaultdict(set)
    for obj in snapshot["objects"]:
        by_name[obj["name"]].append(obj)
    for part in geometry.parts:
        export_nodes[part["name"]].add(part.get("node_index", part["name"]))
    source_poly = np.full(len(geometry.faces), -1, np.int64)
    source_object = np.full(len(geometry.faces), "", dtype=object)
    reports = {}
    for pi, part in enumerate(geometry.parts):
        candidates = by_name.get(part["name"], [])
        if len(candidates) != 1 or len(export_nodes[part["name"]]) != 1:
            reports[str(pi)] = {"matched": 0, "reason": "Export node name has no unique source object"}
            continue
        obj = candidates[0]
        triangle_ids = np.flatnonzero(geometry.face_parts == pi)
        source_object[triangle_ids] = obj["id"]
        raw = np.asarray(obj["vertices_world_mm"], float)
        if not len(raw):
            continue
        points = geometry.vertices[np.unique(geometry.faces[triangle_ids])] * 1000
        tree = cKDTree(raw)
        distances, nearest = tree.query(points, k=2 if len(raw) > 1 else 1)
        if len(raw) == 1:
            distances, nearest = distances[:, None], nearest[:, None]
        valid = distances[:, 0] <= 1e-4
        if distances.shape[1] > 1:
            valid &= distances[:, 1] > 1e-4
        global_vertices = np.unique(geometry.faces[triangle_ids])
        lookup = {int(i): int(j) if okay else -1 for i, j, okay in zip(global_vertices, nearest[:, 0], valid)}
        vp = defaultdict(set)
        for p, poly in enumerate(obj["polygons"]):
            for vi in poly:
                vp[vi].add(p)
        for ti in triangle_ids:
            a, b, c = [lookup[int(i)] for i in geometry.faces[ti]]
            if min(a, b, c) < 0:
                continue
            candidates = vp[a] & vp[b] & vp[c]
            if len(candidates) == 1:
                source_poly[ti] = next(iter(candidates))
        reports[str(pi)] = {"object_id": obj["id"], "triangles": len(triangle_ids),
                            "matched": int((source_poly[triangle_ids] >= 0).sum()),
                            "max_vertex_error_mm": float(distances[:, 0].max()),
                            "ambiguous_or_unmatched_vertices": int((~valid).sum())}
    return source_object, source_poly, reports


def capture_correspondence(snapshot, source_glb):
    """Index the original GLB identities, never a re-export's triangle ordering.

    This uses the same unique vertex/connectivity proof as the ordinary model map,
    without a raycasting scene. Browser primitive-local triangles are converted to
    global rows only here, from the original GLB parser's order.
    """
    from ...g3d_host import glb
    model = glb.load(source_glb) if isinstance(source_glb, (str, Path)) else source_glb
    vertices, faces, owners, parts, primitives = [], [], [], [], {}
    offset = 0
    for primitive in model.primitives:
        identity = (primitive.node_index, primitive.mesh_index, primitive.primitive_index)
        if identity in primitives:
            raise ValueError("The captured model has an ambiguous primitive identity")
        record = {"primitive": primitive, "part": None}
        primitives[identity] = record
        if primitive.compressed or primitive.error or not len(primitive.triangles):
            continue
        record["part"] = len(parts)
        parts.append({"name": primitive.node_name, "node_index": primitive.node_index,
                      "mesh_index": primitive.mesh_index, "primitive_index": primitive.primitive_index})
        vertices.append(primitive.positions)
        faces.append(primitive.triangles + offset)
        owners.append(np.full(len(primitive.triangles), len(parts)-1, int))
        offset += len(primitive.positions)
    geometry = SimpleNamespace(vertices=np.vstack(vertices) if vertices else np.empty((0, 3)),
        faces=np.vstack(faces) if faces else np.empty((0, 3), int),
        face_parts=np.concatenate(owners) if owners else np.empty(0, int), parts=parts)
    objects, polygons, reports = export_correspondence(snapshot, geometry)
    for record in primitives.values():
        rows = np.flatnonzero(geometry.face_parts == record["part"]) if record["part"] is not None else []
        record["source_objects"] = objects[rows]
        record["source_polygons"] = polygons[rows]
    return {"primitives": primitives, "reports": reports}


def resolve_capture_marks(snapshot, source_glb, capture, targets=None, *, correspondence=None):
    """Resolve owner evidence to source polygons; return candidates, never edit support.

    A mark is resolved only when every vertex and footprint triangle has a unique
    source polygon. Background and partial/ambiguous matches remain unresolved.
    A candidate may extend beyond the footprint; the ordinary map/support delivery
    checks remain the only way to select and authenticate an edit's influence.
    """
    correspondence = correspondence or capture_correspondence(snapshot, source_glb)
    targets = catalogue(snapshot)["targets"] if targets is None else targets
    objects = {obj["id"]: obj for obj in snapshot["objects"]}

    def triangle(reference, index):
        identity = {key: reference[key] for key in ("node_index", "mesh_index", "primitive_index")}
        identity["triangle_index"] = index
        result = {"identity": identity, "status": "unresolved"}
        row = correspondence["primitives"].get(tuple(identity[key] for key in ("node_index", "mesh_index", "primitive_index")))
        if row is None:
            return {**result, "reason": "The captured GLB primitive is unavailable"}
        primitive = row["primitive"]
        if "node_name" in reference and reference["node_name"] != primitive.node_name:
            return {**result, "reason": "The captured node name differs from its exact GLB identity"}
        if type(index) is not int or index < 0 or index >= len(row["source_polygons"]):
            return {**result, "reason": "The captured triangle is unavailable or cannot be decoded"}
        oid, polygon = row["source_objects"][index], int(row["source_polygons"][index])
        if not oid or polygon < 0:
            return {**result, "reason": "No unique export-to-source polygon correspondence"}
        obj = objects[oid]
        return {**result, "status": "resolved", "object_id": oid, "object_name": obj["name"],
                "polygon_id": polygon, "source_editable": bool(obj["evaluated"]["positions_match_source"]
                    and not obj.get("unsupported_edit_state"))}

    def anchor(value):
        if value["kind"] == "background":
            return {"status": "unresolved", "reason": "Background has no model surface"}
        result = triangle(value, value["triangle_index"])
        result.update(position_mm=value["position_mm"], barycentric=value["barycentric"], is_lens=value["is_lens"])
        if value.get("behind") is not None:
            result["behind"] = anchor(value["behind"])
        return result

    def polygon_groups(rows):
        groups = defaultdict(set)
        for row in rows:
            if row["status"] == "resolved":
                groups[row["object_id"]].add(row["polygon_id"])
        return [{"object_id": oid, "object_name": objects[oid]["name"], "polygon_ids": sorted(polygons)}
                for oid, polygons in sorted(groups.items())]

    def candidates(groups):
        result = {"parts": [], "faces": [], "edges": [], "points": []}
        kinds = {"object": "parts", "face_region": "faces", "edge_chain": "edges", "point": "points"}
        for group in groups:
            polygons = set(group["polygon_ids"])
            for target in targets.values():
                if target["object_id"] == group["object_id"] and polygons.intersection(target["polygon_ids"]):
                    result[kinds[target["kind"]]].append(target["id"])
        return result

    result = []
    for mark in capture["marks"]:
        anchors = [{"vertex_index": index, "xy_px": vertex["xy_px"], **anchor(vertex["anchor"])}
                   for index, vertex in enumerate(mark["vertices"])]
        footprint, unresolved, total, resolved = {}, [], 0, 0
        for face in mark["footprint"]["faces"]:
            for first, last in face["triangles"]:
                for index in range(first, last+1):
                    row = triangle(face, index)
                    total += 1
                    if row["status"] == "resolved":
                        footprint[(row["object_id"], row["polygon_id"])] = row
                        resolved += 1
                    else:
                        base = {**row["identity"]}
                        del base["triangle_index"]
                        if (unresolved and unresolved[-1]["identity"] == base and
                                unresolved[-1]["reason"] == row["reason"] and unresolved[-1]["triangles"][1]+1 == index):
                            unresolved[-1]["triangles"][1] = index
                        else:
                            unresolved.append({"identity": base, "triangles": [index, index], "reason": row["reason"]})
        primary = polygon_groups([*anchors, *footprint.values()])
        behind = polygon_groups([row["behind"] for row in anchors if "behind" in row])
        all_resolved = bool(primary) and total > 0 and not unresolved and all(row["status"] == "resolved" and
            row.get("behind", {"status": "resolved"})["status"] == "resolved" for row in anchors)
        result.append({"capture_id": capture["capture_id"], "label": mark["label"], "kind": mark["kind"],
            "status": "resolved" if all_resolved else "unresolved", "anchors": anchors,
            "footprint": {"status": "resolved" if total and not unresolved else "unresolved",
                "triangle_count": total, "resolved_triangle_count": resolved,
                "polygons": polygon_groups(footprint.values()), "unresolved": unresolved},
            "resolved_polygons": primary, "behind_polygons": behind,
            "candidate_target_ids": candidates(primary), "behind_candidate_target_ids": candidates(behind),
            "edit_authority": False,
            "note": "Candidates can extend beyond the mark. Show the chosen targets and exact support with labelled_model_map before editing."})
    return result


def project(points_mm, camera):
    points = np.asarray(points_mm, float) / 1000
    matrix = np.asarray(camera["view"], float).reshape(4, 4)
    q = points @ matrix[:3, :3].T + matrix[:3, 3]
    depth = -q[:, 2]
    return np.column_stack((camera["fx"] * q[:, 0] / depth + camera["cx"],
                            camera["cy"] - camera["fy"] * q[:, 1] / depth)), depth


def first_hits(geometry, camera, mask):
    """Exact full-scene first-hit triangle at requested native pixels; hidden targets stay hidden."""
    import open3d as o3d
    y, x = np.where(mask)
    out = np.full(mask.shape, -1, np.int64)
    matrix = np.asarray(camera["view"], float).reshape(4, 4)
    rotation, translation = matrix[:3, :3], matrix[:3, 3]
    origin = -rotation.T @ translation
    for start in range(0, len(x), 200000):
        xx, yy = x[start:start + 200000], y[start:start + 200000]
        local = np.column_stack(((xx - camera["cx"]) / camera["fx"],
                                 -(yy - camera["cy"]) / camera["fy"], -np.ones(len(xx))))
        direction = local @ rotation
        rays = np.column_stack((np.broadcast_to(origin, direction.shape), direction)).astype(np.float32)
        hits = geometry.scene.cast_rays(o3d.core.Tensor(rays), nthreads=1)
        ids = hits["primitive_ids"].numpy().astype(np.int64)
        ids[~np.isfinite(hits["t_hit"].numpy())] = -1
        out[yy, xx] = ids
    return out


def visible_points(points_mm, camera, geometry):
    """Continuous rays to actual points; a visible adjacent face cannot reveal a hidden edge/corner."""
    import open3d as o3d
    points = np.asarray(points_mm, float) / 1000
    matrix = np.asarray(camera["view"], float).reshape(4, 4)
    origin = -matrix[:3, :3].T @ matrix[:3, 3]
    direction = points - origin
    rays = np.column_stack((np.broadcast_to(origin,direction.shape),direction)).astype(np.float32)
    hits = geometry.scene.cast_rays(o3d.core.Tensor(rays),nthreads=1)["t_hit"].numpy()
    distance_mm = np.linalg.norm(direction,axis=1)*1000
    tolerance_mm = np.maximum(.03, distance_mm*2e-7)
    xy,depth = project(points_mm,camera)
    return ((np.abs(hits-1)*distance_mm <= tolerance_mm) & (depth>0) &
            (xy[:,0]>=0) & (xy[:,0]<camera["width"]) & (xy[:,1]>=0) & (xy[:,1]<camera["height"]))
