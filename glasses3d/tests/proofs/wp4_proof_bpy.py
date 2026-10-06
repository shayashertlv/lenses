"""Blender-side helpers for the WP4 §10 proof (tests only), called through tests/blender/seat_run.py's `exec` op:

    {"op": "exec", "code": "from glasses3d.tests.proofs import wp4_proof_bpy as W; result = W.node_state(...)"}

They read the scene the way seat_lens reads it (the same parts, obstacles and ranges) and return plain data:
- `node_state`: the core's state of one lens BEFORE (or after) a call: per measurement point the check's verdict, the
  planner's verdict, rule value, capture, cover, sector and the arc distance to the nearer end of its failing run
  (§4 step 3), and per node the raw row of its front rim vertex, so a before and an after state are joined on raw
  vertex rows (raw indices are stable; the welded order is not). Also the vertices §10 P5 expects to move: 'the band
  of FAIL nodes not BLOCKED, recomputed from the before-state' (plan points still FAIL after the clamps, both owners
  of a halfway point, deform.lens_band);
- `digest_all`, `objects`: every object's mesh digest (positions, topology, every attribute) and matrix;
- `rim_sharp`, `wall_smooth`: sharp rim edges and smooth wall faces;
- `open_cutter_manifold`: §10 P4 'an open cutter through MANIFOLD is caught by the volume check'.
(The cord-lens overlap is graded from the tool's own report and both checks', proof review round 2.)
"""
from __future__ import annotations

import numpy as np

import bpy

from glasses3d.g3d import export_glb as EX
from glasses3d.g3d import seat_bpy as SB
from glasses3d.g3d.seat import check as CK
from glasses3d.g3d.seat import deform, geom, plan as PL, profile as PR, rules, stations


def objects() -> list:
    return [{"name": o.name, "type": o.type, "role": SB._role(o), "hide_render": bool(o.hide_render),
             "translation": [float(x) for x in o.matrix_world.translation],
             "data": o.data.name if o.data is not None else None,
             "modifiers": [[m.name, m.type] for m in getattr(o, "modifiers", [])]} for o in bpy.data.objects]


def digest_all() -> dict:
    """Every surface object: MESH by its mesh digest; CURVE / FONT / SURFACE / META by a hash of the evaluated
    geometry (and the text of a FONT), plus the object matrix and modifiers."""
    import hashlib
    out = {}
    dg = bpy.context.evaluated_depsgraph_get()
    for o in bpy.data.objects:
        if o.type not in EX.SURFACE_TYPES:
            continue
        rec = {"matrix": [[float(x) for x in r] for r in o.matrix_world], "role": SB._role(o),
               "modifiers": [[m.name, m.type] for m in getattr(o, "modifiers", [])],
               "hide_render": bool(o.hide_render), "type": o.type}
        if o.type == "MESH":
            rec.update(digest=EX._mesh_digest(o.data), vertices=len(o.data.vertices))
        else:
            A = SB.MeshArrays(o, np.zeros(3), dg)
            h = hashlib.sha256()
            h.update(np.ascontiguousarray(A.co, np.float64).tobytes())
            h.update(np.ascontiguousarray(A.T, np.int64).tobytes())
            if o.type == "FONT":
                h.update(str(o.data.body).encode())
            rec.update(digest=h.hexdigest(), vertices=int(len(A.co)))
        out[o.name] = rec
    return out


def _first_raw(vid: np.ndarray, n: int) -> np.ndarray:
    out = np.full(n, -1, np.int64)
    out[vid[::-1]] = np.arange(len(vid))[::-1]
    return out


def node_state(lens, frame_parts=None, frame_type=None, lower_edge=None, capture_mm=None) -> dict:
    ob = SB._obj(lens)
    scene = bpy.context.scene
    warnings: list = []
    origin = SB.asset_origin(scene, warnings)
    dg = bpy.context.evaluated_depsgraph_get()
    nodes, col, fobjs, extra = SB._tool_context(ob, frame_parts, scene, dg, origin, warnings)
    A = nodes[0]["_arrays"]
    ft = rules.frame_type(frame_type, lower_edge, capture_mm)
    bodies = [b for b, n in zip(col["bodies"], col["body_node"]) if n["name"] == ob.name]
    near, seen = [], set()
    for b in bodies:
        for n, c in CK.parts_near(b, col["solids"]):
            if (n["name"], c.name) not in seen:
                seen.add((n["name"], c.name))
                near.append((n, c))
    parts = [(c.name, c.V, c.T) for _, c in near]
    names = [p[0] for p in parts]
    boxes = [CK.rim_box(b) for b in bodies]
    obstacles = SB._obstacles(col, ob.name, extra, origin, dg, boxes)
    ranges = SB._ranges_of([ob] + fobjs) if ft.in_range_rule else None
    fb = SB.bvh_factory([(V, T) for _, V, T in parts])
    obb = SB.bvh_factory([(V, T) for _, V, T in obstacles]) if obstacles else None
    Vw, vid = geom.weld_exact(A.V)
    first = _first_raw(vid, len(Vw))
    poly_w = vid[A.E]
    rec = stations.recognise(A.V, A.T, ob.name)
    out = {"lens": ob.name, "frame_type": ft.name, "ranges": [list(r) for r in ranges] if ranges else None,
           "parts": names, "obstacles": [o[0] for o in obstacles], "warnings": warnings, "bodies": [],
           "raw_vertices": int(len(A.V)), "origin": origin.tolist(),
           "matrix": [[float(x) for x in r] for r in ob.matrix_world]}
    for body in rec.bodies:
        g2b = np.full(len(Vw), -1, np.int64)
        g2b[body.source_verts] = np.arange(len(body.V))
        pe = g2b[poly_w]
        pe = pe[(pe >= 0).all(1) & (pe[:, 0] != pe[:, 1])]
        st = deform.measure_state(body, fb, SB.bvh_factory, ft, ranges, obb, pe)
        fr, meas, rl, plan = st.frames, st.meas, st.rl, st.plan
        nv = len(fr.loop)
        npt = PL.node_points(fr, body.V)
        band = deform.lens_band(body, fr, npt, pe)
        o1, o2 = fr.owners
        grow_pts = plan.verdict.astype(str) == "FAIL"
        fail_nodes = np.zeros(nv, bool)
        fail_nodes[o1[grow_pts]] = True
        fail_nodes[o2[grow_pts]] = True
        exp_body = np.flatnonzero((band.node >= 0) & fail_nodes[np.maximum(band.node, 0)])
        exp_raw = np.flatnonzero(np.isin(vid, body.source_verts[exp_body]))
        check_v = rules.verdicts(ft, meas, rl, fr.s, fr.loop_mm)
        base = rules.verdicts(ft, meas, rl, fr.s, fr.loop_mm, transition=False).astype(str)
        fail = (base == "FAIL") & np.asarray(rl.required, bool)
        delta = np.full(len(fr), np.nan)
        for run in PL.runs(fail):
            if len(run) == len(fail):
                delta[run] = np.inf
                continue
            ss = fr.s[run]
            delta[run] = np.minimum((ss - ss[0]) % fr.loop_mm, (ss[-1] - ss) % fr.loop_mm)
        own = meas.owner[:, stations.MID]
        loop_raw = first[body.source_verts[fr.loop]]
        partner_raw = np.where(band.partner >= 0, first[body.source_verts[np.maximum(band.partner, 0)]], -1)
        hole_raw = []
        for lp in body.front_loops[1:] + body.back_loops[1:]:
            hole_raw += np.flatnonzero(np.isin(vid, body.source_verts[lp])).tolist()
        out["bodies"].append({
            "body": body.name, "summary": body.summary(), "nodes": nv, "points": int(len(fr)),
            "loop_mm": float(fr.loop_mm), "loop_raw": loop_raw, "partner_raw": partner_raw, "node_point": npt,
            "node_g": plan.node_g, "fail_nodes": np.flatnonzero(fail_nodes), "expected_raw": exp_raw,
            "plan_counts": plan.counts(), "gate": plan.gate, "gate_message": plan.gate_message or None,
            "hole_raw": hole_raw,
            "point": {"s": fr.s, "node": o1, "node2": o2, "P": fr.P, "u": fr.u, "n": fr.n,
                      "sector": fr.sector.astype(str), "cls": meas.cls.astype(str),
                      "verdict_check": np.asarray(check_v).astype(str), "verdict_plan": plan.verdict.astype(str),
                      "reason": plan.reason.astype(str), "value": rl.value, "pass": rl.pass_mm,
                      "target": rl.target_mm, "required": np.asarray(rl.required, bool),
                      "in_range": np.asarray(rl.in_range, bool), "rule": np.asarray(rl.rule).astype(str),
                      "cap": meas.cap, "room": meas.room, "cover_front": meas.cover_front,
                      "cover_back": meas.cover_back, "covered_front": meas.covered_front,
                      "covered_back": meas.covered_back,
                      "front_ok": np.asarray(rl.front_ok, bool) if rl.front_ok is not None else None,
                      "owner_mid": [names[i] if i >= 0 else None for i in own.tolist()],
                      "delta_run_end": delta, "growth": plan.growth, "target_plan": plan.target,
                      "limit_exits_material": plan.limits.get("EXITS_MATERIAL")}})
    return out


def rim_sharp(name) -> dict:
    ob = SB._obj(name)
    origin = SB.asset_origin(bpy.context.scene)
    A = SB.MeshArrays(ob, origin)
    rec = stations.recognise(A.V, A.T, ob.name)
    me = ob.data
    sharp = SB._attr_values(me, "sharp_edge", len(me.edges)).astype(bool)
    rim = SB._rim_edges(A, rec)
    return {"edges": len(me.edges), "sharp_edges": int(sharp.sum()), "rim_edges": int(len(rim)),
            "rim_sharp": int(sharp[rim].sum()), "custom_normals": bool(me.has_custom_normals),
            "custom_normal_attribute": "custom_normal" in me.attributes}


def wall_smooth(name) -> dict:
    ob = SB._obj(name)
    origin = SB.asset_origin(bpy.context.scene)
    A = SB.MeshArrays(ob, origin)
    rec = stations.recognise(A.V, A.T, ob.name)
    cls = SB._poly_classes(A, rec)
    me = ob.data
    wall = [p for p in me.polygons if cls[p.index] == 2]
    return {"wall_polygons": len(wall), "smooth_wall_polygons": int(sum(1 for p in wall if p.use_smooth))}


def open_cutter_manifold(lens, frame, drop: int = 8) -> dict:
    """§10 P4: a cutter built as seat_lens builds it (the lens inflated 0.03 mm), opened by deleting `drop`
    triangles, cut from the frame with the MANIFOLD solver and judged by the TOOL's own acceptance test
    (seat_bpy.groove_attempt: closed by the mesh's own connectivity, chi, components, removed = vol(cutter ^ frame)
    +- 1%, lens overlap). The positive control is the closed cutter, with MANIFOLD and with EXACT: it must be
    accepted, or the test shows nothing. The scene is restored afterwards (modifier and cutters removed)."""
    lob, fob = SB._obj(lens), SB._obj(frame)
    origin = SB.asset_origin(bpy.context.scene)
    A = SB.MeshArrays(lob, origin)
    Vc = PR.inflate(A.V, A.T, PR.CLEARANCE_MM) + A.origin
    out = {}
    for label, T, solver in (("closed", A.T, "MANIFOLD"), ("closed_exact", A.T, "EXACT"),
                             ("open", A.T[drop:], "MANIFOLD")):
        me = bpy.data.meshes.new(f"proof cutter {label}")
        me.from_pydata(Vc.tolist(), [], np.asarray(T).tolist())
        me.update()
        cut = bpy.data.objects.new(f"proof cutter {label}", me)
        bpy.context.scene.collection.objects.link(cut)
        cut.hide_render = True
        try:
            topo_c = geom.topology(np.asarray(T), len(Vc))
            before = SB.MeshArrays(fob, origin, bpy.context.evaluated_depsgraph_get())
            tb = SB.solid_topology(before)
            vol0 = geom.signed_volume(before.V, before.T)
            expect = SB._bool_volume(fob, cut, "INTERSECT")
            chk, good = SB.groove_attempt(fob, cut, lob, origin, solver, tb, vol0, expect)
            after = SB.MeshArrays(fob, origin, bpy.context.evaluated_depsgraph_get())
            t_exact = geom.topology(geom.weld_exact(after.V)[1][after.T])
            mod = fob.modifiers.get(SB.GROOVE_MODIFIER)
            if mod is not None:
                fob.modifiers.remove(mod)
            out[label] = {"solver": solver, "cutter_closed": bool(topo_c["closed"]),
                          "cutter_boundary_edges": int(topo_c.get("boundary_edges", 0)),
                          "expected_removed_mm3": round(float(expect), 4), "removed_mm3": chk["removed_mm3"],
                          "frame_closed_after": bool(chk["closed"]), "euler": chk["euler"],
                          "components": chk["components"], "lens_overlap_mm3": chk["lens_overlap_mm3"],
                          "own_boundary_nonmanifold_edges": [chk["boundary_edges"], chk["nonmanifold_edges"]],
                          "exact_weld_everywhere": {k: t_exact[k] for k in ("closed", "euler", "boundary_edges",
                                                                           "nonmanifold_edges")},
                          "accepted_by_the_tool_check": bool(good)}
        finally:
            bpy.data.objects.remove(cut, do_unlink=True)
            bpy.data.meshes.remove(me)
    return out
