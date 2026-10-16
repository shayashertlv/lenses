"""Read-only, actual Blender mesh snapshot for owner-edit maps.

Stored temple construction is used only for names after exact topology correspondence;
all displayed and editable positions come from the current mesh.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import bpy
import numpy as np

from . import checkpoint, export_glb, rpc, owner_materials_bpy, owner_normals_bpy


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _props(value):
    return {key: rpc.to_jsonable(value[key]) for key in sorted(value.keys())}


def _normal_attributes(me):
    result=[]
    for attr in sorted(me.attributes,key=lambda a:a.name):
        if not _normal_storage(attr):
            continue
        access=export_glb._ATTR_ACCESS.get(attr.data_type)
        if access is None:
            result.append({"name":attr.name,"domain":attr.domain,"data_type":attr.data_type,"values":None})
            continue
        field,width,dtype=access
        values=np.zeros(len(attr.data)*width,dtype=dtype)
        attr.data.foreach_get(field,values)
        result.append({"name":attr.name,"domain":attr.domain,"data_type":attr.data_type,
                       "field":field,"values":values.reshape(-1,width).tolist()})
    return result


def _normal_storage(attr):
    # Blender's encoded split-normal storage, not arbitrary user attributes with similar names.
    return (attr.name, attr.domain, attr.data_type) == ("custom_normal", "CORNER", "INT16_2D")


def _other_attributes(me):
    """Bind every non-position/non-normal typed attribute; unknown types are not editable."""
    digest = hashlib.sha256()
    unsupported = []
    for attr in sorted(me.attributes, key=lambda a: a.name):
        if attr.name == "position" or _normal_storage(attr):
            continue
        digest.update(f"{attr.name}|{attr.domain}|{attr.data_type}|".encode())
        access = export_glb._ATTR_ACCESS.get(attr.data_type)
        if access is None:
            unsupported.append(attr.name)
            continue
        field, width, dtype = access
        values = np.zeros(len(attr.data) * width, dtype=dtype)
        attr.data.foreach_get(field, values)
        digest.update(values.tobytes())
    return digest.hexdigest(), unsupported


def _attributes(me):
    result = []
    for attr in sorted(me.attributes, key=lambda a: a.name):
        if attr.name == 'position' or _normal_storage(attr):
            continue
        access = export_glb._ATTR_ACCESS.get(attr.data_type)
        if access is None:
            continue
        field, width, dtype = access
        values = np.zeros(len(attr.data)*width, dtype=dtype)
        attr.data.foreach_get(field, values)
        result.append({'name': attr.name, 'domain': attr.domain, 'data_type': attr.data_type,
                       'values': values.reshape(-1, width).tolist()})
    return result


def _mesh(me, matrix, origin):
    v = np.asarray([tuple(v.co) for v in me.vertices], float).reshape(-1, 3)
    m = np.asarray(matrix, float)
    world = v @ m[:3, :3].T + m[:3, 3] - origin
    me.calc_loop_triangles()
    return {"vertices_local": v.tolist(), "vertices_world_mm": world.tolist(),
            "edges": [list(e.vertices) for e in me.edges], "loops": [int(l.vertex_index) for l in me.loops],
            "polygons": [list(p.vertices) for p in me.polygons],
            "triangles": [list(t.vertices) for t in me.loop_triangles],
            "triangle_polygons": [int(t.polygon_index) for t in me.loop_triangles]}


def _aliases(obj, raw):
    """A prior builder's connectivity may survive manual vertex edits. Its positions never do."""
    out = {"status": "unavailable", "reason": "No verified specialist topology", "facets": {}, "points": {}}
    try:
        metadata = json.loads(obj.get("g3d_temple", "null"))
        if not isinstance(metadata, dict):
            return out
        from .temple.build import build_temple
        canonical = metadata["canonical_spec"]
        tb = build_temple(json.loads(canonical) if isinstance(canonical,str) else canonical)
        side = 1 if metadata.get("side") == "+x" else -1
        _, polygons, triangles = tb.world(side)
        sources, cursor, matched_polygons = [], 0, 0
        actual = raw["polygons"]
        for pi, polygon in enumerate(polygons):
            if cursor < len(actual) and actual[cursor] == polygon:
                sources.append(pi)
                cursor += 1
                matched_polygons += 1
                continue
            # The producer may replace a degenerate Blender n-gon tessellation
            # with precisely its checked core triangles, in their checked order.
            # No arbitrary triangulation or position-based guessing is accepted.
            expected = triangles[tb.tri_poly == pi].tolist()
            if expected and actual[cursor:cursor+len(expected)] == expected:
                sources.extend([pi]*len(expected))
                cursor += len(expected)
                matched_polygons += 1
                continue
            break
        if (len(raw["vertices_local"]) != len(tb.V) or matched_polygons != len(polygons)
                or cursor != len(actual) or len(sources) != len(actual)):
            return {**out, "reason": "Stored specialist topology differs from the current raw mesh; no guessed aliases"}
        facets = {}
        for i, source in enumerate(sources):
            kind, region = tb.poly_kind[source], tb.poly_region[source]
            if kind in ("outer", "roll"):
                facets.setdefault(region.split("/")[0], []).append(i)
        points = {}
        for name, node in tb.spec.nodes.items():
            # Only a physical outer vertex gets a corner name; virtual/extrapolated corners do not.
            ids = np.flatnonzero(np.linalg.norm(tb.V[:tb.n2, :2] - np.asarray(node[:2]), axis=1) <= 1e-6)
            if len(ids) == 1:
                points[name] = int(ids[0])
        return {"status": "verified_topology", "basis": "Exact stored-builder vertex count and polygon connectivity (or its checked core triangles); current positions",
                "stored_spec_sha256": metadata.get("spec_sha256"), "facets": facets, "points": points,
                "historical_coordinates_used": False, "hinge": dict(tb.spec.hinge),
                "options": dict(tb.spec.options), "manual_refinement": str(obj.get("manual_refinement", ""))}
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError) as error:
        return {**out, "reason": f"Specialist aliases unavailable: {type(error).__name__}: {error}"}


def snapshot(params, root):
    root = Path(root).resolve()
    path = Path(params["path"]).resolve()
    if not path.is_relative_to(root / "work" / "model_maps") or path.suffix != ".json":
        raise PermissionError("Map snapshots must be JSON under this job's work/model_maps")
    if path.exists():
        raise FileExistsError("Map snapshots are immutable; choose a fresh map directory")
    initial_state = checkpoint.scene_state()
    before = checkpoint.fingerprint(initial_state)
    _, origin, _ = export_glb.check_convention(bpy.context.scene)
    origin = np.asarray(origin, float)
    depsgraph = bpy.context.evaluated_depsgraph_get()
    objects = []
    for index, obj in enumerate(sorted((o for o in bpy.context.scene.objects
                                       if o.type == "MESH" and not o.hide_render), key=lambda o: o.name)):
        me = obj.data
        raw = _mesh(me, obj.matrix_world, origin)
        evaluated = obj.evaluated_get(depsgraph)
        em = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
        try:
            ev = _mesh(em, evaluated.matrix_world, origin)
        finally:
            evaluated.to_mesh_clear()
        same_topology = raw["polygons"] == ev["polygons"] and len(raw["vertices_local"]) == len(ev["vertices_local"])
        ev["topology_matches_source"] = same_topology
        ev["positions_match_source"] = same_topology and bool(np.allclose(raw["vertices_world_mm"], ev["vertices_world_mm"], rtol=0, atol=1e-7))
        material_names = [m.name if m else None for m in me.materials]
        material_records = [{"name": m.name, "digest": initial_state["materials"].get(m.name)}
                            for m in me.materials if m]
        other_attributes, unsupported = _other_attributes(me)
        unsupported_edit_state = [f"Unsupported attribute: {name}" for name in unsupported]
        if me.shape_keys is not None:
            unsupported_edit_state.append("Shape-key mesh: raw local edit requires a dedicated shape-key operation")
        record = {"id": f"O{index + 1:04d}", "name": obj.name, "mesh_name": me.name, "type": obj.type,
                  "parent_name": obj.parent.name if obj.parent else None,
                  "role": str(obj.get("partRole", me.get("partRole", ""))),
                  "side": 1 if np.mean(np.asarray(raw["vertices_world_mm"])[:, 0]) > 0 else -1,
                  "matrix_world": [list(row) for row in obj.matrix_world], **raw,
                  "polygon_smooth": [bool(p.use_smooth) for p in me.polygons],
                  "polygon_material": [int(p.material_index) for p in me.polygons],
                  "corner_normals": [list(n.vector) for n in me.corner_normals],
                  "normal_intent": owner_normals_bpy.capture(me),
                  "has_custom_normals": bool(me.has_custom_normals),
                  "normal_attributes": _normal_attributes(me),
                  "attributes": _attributes(me),
                  "other_attributes_sha256": other_attributes,
                  "unsupported_edit_state": unsupported_edit_state,
                  "uv_layers": [{"name": uv.name, "uv": [list(v.uv) for v in uv.data],
                                 "active_render": bool(uv.active_render), "active_clone": bool(uv.active_clone)} for uv in me.uv_layers],
                  "uv_active_index": int(me.uv_layers.active_index),
                  "material_names": material_names,
                  "material_definitions": [owner_materials_bpy.capture(m) for m in me.materials],
                  "material_slot_links": [s.link for s in obj.material_slots],
                  "object_state_sha256": _hash(([(k,v) for k,v in checkpoint._rna(obj) if k not in ('active_material', 'id_data', 'bound_box')],
                                                _props(obj),[checkpoint._rna(m) for m in obj.modifiers])),
                  "topology_sha256": _hash({k: raw[k] for k in ("edges", "loops", "polygons")}),
                  "positions_sha256": _hash(raw["vertices_local"]),
                  "properties_sha256": _hash(_props(obj)), "mesh_properties_sha256": _hash(_props(me)),
                  "mesh_properties": _props(me),
                  "edge_radius_recipe": json.loads(me.get('g3d_owner_edge_recipe','null')),
                  "material_sha256": _hash(material_records),
                  "shared_mesh_users": sorted(o.name for o in bpy.data.objects if o.data == me),
                  "modifiers": [{"name": m.name, "type": m.type, "show_render": bool(m.show_render)} for m in obj.modifiers],
                  "evaluated": ev, "aliases": _aliases(obj, raw)}
        objects.append(record)
    after = checkpoint.fingerprint()
    if after != before:
        return {"status": "failed", "unchanged": False, "fingerprint_before": before, "fingerprint_after": after}
    unsupported = []
    for obj in bpy.context.scene.objects:
        if obj.hide_render or obj.type not in {"FONT", "CURVE", "SURFACE", "META", "CURVES", "VOLUME", "GREASEPENCIL"}:
            continue
        matrix = np.asarray(obj.matrix_world, float)
        corners = np.asarray(obj.bound_box, float) @ matrix[:3, :3].T+matrix[:3, 3]-origin
        unsupported.append({'name': obj.name, 'type': obj.type,
                            'parent_name': obj.parent.name if obj.parent else None,
                            'bbox_world_mm': [corners.min(0).tolist(), corners.max(0).tolist()]})
    document = {"schema": "g3d.mesh_snapshot/1", "fingerprint": before, "scene_state": initial_state, "origin_mm": origin.tolist(),
                "unsupported_rendered_geometry": unsupported,
                "coordinate_system": "Current raw Blender mesh; asset mm = scene world mm minus bridge origin", "objects": objects}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, ensure_ascii=False, separators=(",", ":"), allow_nan=False), encoding="utf-8")
    return {"status": "ok", "path": str(path), "sha256": checkpoint.sha256_file(path),
            "fingerprint_before": before, "fingerprint_after": after, "unchanged": True,
            "objects": len(objects)}
