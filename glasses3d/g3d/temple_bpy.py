"""Blender layer of WP6 faceted temples (plan/WP6_DESIGN.md §3 "Blender layer", §4, §5). Runs inside Blender 5.2.

    faceted_temple(spec, *, side="both", targets=None, decals="auto", dry_run=False) -> receipt
    unfaceted_temple(obj) -> report
    check_relief(objects=None, *, photo_creases=None) -> report
    polygon_section(points, corners) / loft_sections(stations, path=None, caps=True, name=None, ...) /
    check_round_corners(obj)

Thin by design: the numpy core (g3d/temple/) plans and checks the solid; this layer reads the scene, places the
temples, writes once, and reports. Conventions:
- coordinates: the spec is in ASSET millimetres (scene mm minus the scene's `mdl_bridge_underside`, the exporter's
  origin; Prada's is (0, 0, 0)), +Y up, +Z front; the +X temple is the spec, the -X temple its exact mirror;
- targets by the sign of x, never by name: per side, the temple-role mesh reaching furthest back (>= 60 mm), or the
  `targets` given ({"+x": name, "-x": name} or names whose x sign picks the side). Not a temple-role mesh, shape keys,
  or a modifier other than WeightedNormal: E_TARGET;
- plan fully (core, decals, roll, hinge report), then write once: each target's mesh datablock is swapped in place
  (object name, transform, materials and other extras unchanged); the previous datablock stays as
  `<mesh> | g3d pre-temple` with a fake user (the earliest backup wins), WeightedNormal modifiers are removed and
  their settings kept in `g3d_temple_backup`; `unfaceted_temple` puts the very same datablocks back (byte-identical);
- sharp_edge on the core's sharp set; facet corners get the facet's face normal (custom normals), every other corner
  is auto-smoothed (zero custom vector);
- roll_mm > 0: Bevel (limit by weight on the outer long edges, 8 segments, harden normals) applied, then re-checked
  (closed manifold, Euler 2, facet planarity <= 1e-4 mm, side silhouette = outline <= 0.01 mm); failure -> E_ROLL;
- decals="auto": parts lying within 1 mm of the old temple surface that are open or thin (bbox, or thickness along
  the surface normal: measure.is_surface_decal) are ray-projected along their mean surface normal onto the new temple
  + 0.05 mm (moved through the same datablock swap; restored by unfaceted_temple);
- extras: partRole temple, region temple_+x / temple_-x, g3d_temple = canonical spec + core version + sha256 +
  facet table (the exporter copies it to the GLB only once WP4's allowlist names it);
- hinge parts are never moved (measure.hinge_part_kind: knuckles and temple leaves are excluded from decal planning,
  however close and thin they are); the receipt reports the leaf-to-inner-face gap, temple-knuckle contact and frame-
  knuckle penetration (W_HINGE_LEAF, W_KNUCKLE_PENETRATION).
Geometry problems never raise: every function returns a dict with "status" and, when refused, "code".
"""
from __future__ import annotations

import hashlib
import json
import math
import time
import traceback
from pathlib import Path

import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from . import export_glb as EX
from .temple import CORE_VERSION, SCHEMA
from .temple import build as TB
from .temple import loft as LOFT
from .temple import measure as MS
from .temple import report as RP
from .temple import sections as SEC
from .temple import spec as TS
from .temple.spec import TempleError

BACKUP_SUFFIX = " | g3d pre-temple"
PROP_TEMPLE = "g3d_temple"
PROP_BACKUP = "g3d_temple_backup"
DECAL_NEAR_MM = MS.DECAL_NEAR_MM   # a decal lies within this of the old temple surface (the shared rule:
DECAL_THIN_MM = MS.DECAL_THIN_MM   # measure.is_surface_decal, also the host's photo-crease decal mask)
DECAL_LIFT_MM = 0.05            # ... and lands this far above the new one
DECAL_RAY_MM = 12.0
HINGE_NEAR_MM = MS.HINGE_NEAR_MM   # hinge parts (the shared rule: measure.hinge_part_kind, also the host's decal mask)
HINGE_X_MM = MS.HINGE_X_MM
KNUCKLE_MAX_MM = MS.KNUCKLE_MAX_MM
TEMPLE_MIN_REACH_MM = 60.0
ROLL_SEGMENTS = 8
ROLL_PLANAR_MM = 1e-4
ROLL_SILHOUETTE_MM = 0.01
SIDES = {"+x": 1, "-x": -1}


# --------------------------------------------------------------------------------------------- scene helpers
def asset_origin(scene=None) -> np.ndarray:
    scene = scene or bpy.context.scene
    v = scene.get(EX.ORIGIN_KEY)
    return np.zeros(3) if v is None else np.asarray([float(x) for x in v], np.float64)


def _obj(x):
    if isinstance(x, bpy.types.Object):
        return x
    ob = bpy.data.objects.get(str(x))
    if ob is None:
        raise LookupError(f"no object named {x!r}")
    return ob


def _role(ob) -> str | None:
    role, _, conflict = EX._role(ob)
    return None if conflict else role


def _matrix(ob):
    M = np.array(ob.matrix_world, np.float64)
    return M[:3, :3], M[:3, 3]


def mesh_arrays(ob, origin, evaluated: bool = False):
    """(V asset mm, triangles, polygons as lists, loop vertex) of an object's base (or evaluated) mesh."""
    M3, t = _matrix(ob)
    ev = None
    if evaluated:
        dg = bpy.context.evaluated_depsgraph_get()
        ev = ob.evaluated_get(dg)
        me = ev.to_mesh()
    else:
        me = ob.data
    try:
        co = np.zeros(len(me.vertices) * 3, np.float32)
        me.vertices.foreach_get("co", co)
        V = co.reshape(-1, 3).astype(np.float64) @ M3.T + t - origin
        me.calc_loop_triangles()
        tv = np.zeros(len(me.loop_triangles) * 3, np.int32)
        me.loop_triangles.foreach_get("vertices", tv)
        T = tv.reshape(-1, 3).astype(np.int64)
        polys = [list(p.vertices) for p in me.polygons]
    finally:
        if ev is not None:
            ev.to_mesh_clear()
    if np.linalg.det(M3) < 0:
        T = T[:, ::-1]
        polys = [p[::-1] for p in polys]
    return V, T, polys


def mesh_digest(me) -> str:
    """Positions, topology, every attribute and the custom normals of a mesh datablock."""
    h = hashlib.sha256()
    for coll, attr, n, dt in ((me.vertices, "co", 3, np.float32), (me.loops, "vertex_index", 1, np.int32),
                              (me.polygons, "loop_start", 1, np.int32), (me.edges, "vertices", 2, np.int32)):
        a = np.zeros(len(coll) * n, dt)
        coll.foreach_get(attr, a)
        h.update(a.tobytes())
    for at in sorted(me.attributes, key=lambda a: a.name):
        if at.name.startswith(".") and at.name not in (".select_vert",):
            pass
        h.update(f"{at.name}|{at.domain}|{at.data_type}".encode())
        try:
            k = {"FLOAT": 1, "INT": 1, "BOOLEAN": 1, "FLOAT_VECTOR": 3, "FLOAT_COLOR": 4, "BYTE_COLOR": 4,
                 "FLOAT2": 2, "INT32_2D": 2, "INT8": 1, "QUATERNION": 4}.get(at.data_type)
            if k is None:
                continue
            field = {"FLOAT_VECTOR": "vector", "FLOAT_COLOR": "color", "BYTE_COLOR": "color",
                     "QUATERNION": "value"}.get(at.data_type, "value")
            a = np.zeros(len(at.data) * k, np.float64 if at.data_type != "BOOLEAN" else bool)
            at.data.foreach_get(field, a)
            h.update(np.ascontiguousarray(a).tobytes())
        except Exception:                                     # noqa: BLE001 - exotic layer: name only
            continue
    cn = np.zeros(len(me.loops) * 3, np.float32)
    me.corner_normals.foreach_get("vector", cn)
    h.update(cn.tobytes())
    h.update(repr([m.name if m else None for m in me.materials]).encode())
    return h.hexdigest()


def scene_digest(scene=None) -> str:
    return hashlib.sha256(EX.snapshot(scene or bpy.context.scene).encode()).hexdigest()


def _bbox(V):
    return V.min(axis=0), V.max(axis=0)


# --------------------------------------------------------------------------------------------- targets
def temple_candidates(scene, origin) -> dict:
    """{side: [(reach mm, object)]} temple-role meshes reaching >= 60 mm back, by the sign of x."""
    out = {1: [], -1: []}
    for ob in scene.objects:
        if ob.type != "MESH" or _role(ob) != "temple" or len(ob.data.vertices) < 8:
            continue
        V, _, _ = mesh_arrays(ob, origin)
        lo, hi = _bbox(V)
        reach = float(hi[2] - lo[2])
        cx = float((lo[0] + hi[0]) / 2)
        if reach >= TEMPLE_MIN_REACH_MM and abs(cx) > 20.0:
            out[1 if cx > 0 else -1].append((reach, ob))
    for s in out:
        out[s].sort(key=lambda t: (-t[0], t[1].name))
    return out


def _sides(side) -> list[int]:
    if side in ("both", None):
        return [1, -1]
    if side in (1, -1):
        return [int(side)]
    if isinstance(side, str) and side.lower() in SIDES:
        return [SIDES[side.lower()]]
    raise TempleError("E_SPEC", f"side must be 'both', '+x', '-x', 1 or -1, got {side!r}")


def select_targets(sides, targets, scene, origin) -> dict:
    cands = temple_candidates(scene, origin)
    chosen = {}
    if targets:
        items = targets.items() if isinstance(targets, dict) else [(None, t) for t in targets]
        for key, name in items:
            try:
                ob = _obj(name)
            except LookupError as exc:
                raise TempleError("E_TARGET", str(exc), target=str(name))
            V, _, _ = mesh_arrays(ob, origin) if ob.type == "MESH" else (np.zeros((1, 3)), None, None)
            s = 1 if float(V[:, 0].mean()) > 0 else -1
            if key is not None and SIDES.get(str(key).lower()) not in (s, None):
                raise TempleError("E_TARGET", f"{ob.name} lies on the {'+x' if s > 0 else '-x'} side, not {key}",
                                  target=ob.name, x_sign=s)
            chosen[s] = ob
    for s in sides:
        if s not in chosen:
            if not cands[s]:
                raise TempleError("E_TARGET", f"no temple-role mesh reaching >= {TEMPLE_MIN_REACH_MM:g} mm back on "
                                  f"the {'+x' if s > 0 else '-x'} side", side=s)
            if len(cands[s]) > 1 and cands[s][1][0] > 0.9 * cands[s][0][0]:
                raise TempleError("E_TARGET", f"two temple-role meshes on the {'+x' if s > 0 else '-x'} side reach "
                                  f"back alike: {[o.name for _, o in cands[s][:2]]}; pass targets", side=s,
                                  candidates=[o.name for _, o in cands[s][:3]])
            chosen[s] = cands[s][0][1]
    out = {}
    for s in sides:
        ob = chosen[s]
        if ob.type != "MESH" or _role(ob) != "temple":
            raise TempleError("E_TARGET", f"{ob.name} is not a temple-role mesh (partRole {_role(ob)!r})",
                              target=ob.name)
        if ob.data.shape_keys is not None:
            raise TempleError("E_TARGET", f"{ob.name} has shape keys", target=ob.name)
        bad = [m.name for m in ob.modifiers if m.type != "WEIGHTED_NORMAL"]
        if bad:
            raise TempleError("E_TARGET", f"{ob.name} has modifiers {bad} that would change the faceted mesh; "
                              f"remove them first", target=ob.name, modifiers=bad)
        out[s] = ob
    return out


def default_axis(side: int, hinge_z: float, scene, origin, exclude) -> tuple[list | None, list]:
    """Bbox centre (|x|, z) of the temple-role knuckles (hinge parts smaller than KNUCKLE_MAX_MM in x and z near the
    hinge plane) on one side, and the names used (measure.knuckle_axis, shared with the host)."""
    used, boxes = [], []
    for ob in scene.objects:
        if ob in exclude or ob.type != "MESH" or _role(ob) != "temple":
            continue
        V, _, _ = mesh_arrays(ob, origin)
        lo, hi = _bbox(V)
        if MS.knuckle_axis([(lo, hi, "temple")], side, hinge_z) is not None:
            boxes.append((lo, hi, "temple"))
            used.append(ob.name)
    ax = MS.knuckle_axis(boxes, side, hinge_z)
    return (ax, used) if ax is not None else (None, [])


# --------------------------------------------------------------------------------------------- planning
def _bvh(V, polys):
    return BVHTree.FromPolygons([tuple(map(float, v)) for v in V], [tuple(int(i) for i in p) for p in polys],
                                all_triangles=False, epsilon=0.0)


def _signed_distance(bvh, P, side) -> np.ndarray:
    """Signed distance of points to a closed mesh (negative inside): nearest distance, inside by a +side*x ray whose
    first hit faces along the ray."""
    out = np.zeros(len(P))
    d = Vector((float(side), 0.0, 0.0))
    for i, p in enumerate(P):
        v = Vector(tuple(map(float, p)))
        loc, nrm, idx, dist = bvh.find_nearest(v)
        if loc is None:
            out[i] = np.inf
            continue
        hit = bvh.ray_cast(v, d)
        inside = hit[0] is not None and hit[1].dot(d) > 0
        out[i] = -dist if inside else dist
    return out


def plan_decals(old_bvh, new_bvh, region_of_poly, side, scene, origin, exclude) -> list:
    """Decals near the old temple surface and their planned positions on the new one."""
    plans = []
    for ob in scene.objects:
        if ob in exclude or ob.type != "MESH" or len(ob.data.vertices) == 0:
            continue
        V, T, polys = mesh_arrays(ob, origin)
        lo, hi = _bbox(V)
        if side * (lo[0] + hi[0]) <= 0:
            continue
        near, normals = [], []
        for v in V:                                   # cheap rejection first: any vertex beyond DECAL_NEAR_MM
            loc, nrm, idx, dist = old_bvh.find_nearest(Vector(tuple(map(float, v))))
            if loc is None or dist > DECAL_NEAR_MM:
                near = None
                break
            near.append(np.array(loc))
            normals.append(np.array(nrm))
        if near is None:
            continue
        E = {}
        for p in polys:
            for k in range(len(p)):
                e = (min(p[k], p[(k + 1) % len(p)]), max(p[k], p[(k + 1) % len(p)]))
                E[e] = E.get(e, 0) + 1
        open_ = any(c == 1 for c in E.values())
        sd = _signed_distance(old_bvh, V, side)
        if not MS.is_surface_decal(sd, float(np.min(hi - lo)), open_):
            continue
        n = np.mean(normals, axis=0)
        n /= np.linalg.norm(n)
        lift = MS.decal_standoff(sd, DECAL_LIFT_MM)
        new, miss, regions = V.copy(), 0, set()
        for i, v in enumerate(V):
            o = Vector(tuple(map(float, v + n * DECAL_RAY_MM)))
            hit = new_bvh.ray_cast(o, Vector(tuple(map(float, -n))), 2 * DECAL_RAY_MM)
            if hit[0] is None:
                miss += 1
                continue
            new[i] = np.array(hit[0]) + n * lift[i]
            regions.add(region_of_poly[hit[2]])
        moves = np.linalg.norm(new - V, axis=1)
        plans.append({"object": ob, "name": ob.name, "V_old": V, "V_new": new, "normal": n.tolist(), "misses": miss,
                      "max_move_mm": float(moves.max()), "regions": sorted(regions), "vertices": len(V)})
    return plans


def hinge_parts(side, axis, hinge_z, scene, origin, exclude) -> list:
    """[(object, kind)] of one side's hinge parts (measure.hinge_part_kind: "temple_knuckle", "frame_knuckle",
    "leaf"), in scene order; empty without a hinge axis. These are never moved: plan_decals never takes one as a
    decal, and hinge_report grades them against the planned temple."""
    out = []
    if axis is None:
        return out
    for ob in scene.objects:
        if ob in exclude or ob.type != "MESH" or _role(ob) not in ("temple", "frame"):
            continue
        V, _, _ = mesh_arrays(ob, origin)
        lo, hi = _bbox(V)
        kind = MS.hinge_part_kind(lo, hi, _role(ob), side, axis[0], hinge_z, len(V))
        if kind is not None:
            out.append((ob, kind))
    return out


def hinge_report(new_bvh, old_bvh, side, axis, hinge_z, scene, origin, exclude, parts=None) -> dict:
    """Leaf-to-inner-face gap, temple-knuckle contact and frame-knuckle penetration against the planned temple
    (`parts` = hinge_parts(...) when the caller already has them)."""
    rep = {"leaves": [], "temple_knuckles": [], "frame_knuckles": []}
    if axis is None:
        return rep
    for ob, kind in (hinge_parts(side, axis, hinge_z, scene, origin, exclude) if parts is None else parts):
        if ob in exclude:
            continue
        V, _, _ = mesh_arrays(ob, origin)
        sd_new = _signed_distance(new_bvh, V, side)
        sd_old = _signed_distance(old_bvh, V, side)
        rec = {"name": ob.name, "min_signed_mm": float(sd_new.min()), "old_min_signed_mm": float(sd_old.min())}
        if kind == "temple_knuckle":
            rec["contact"] = bool(sd_new.min() <= 0.1)
            rep["temple_knuckles"].append(rec)
        elif kind == "frame_knuckle":
            rec["penetration_mm"] = float(max(0.0, -sd_new.min()))
            rep["frame_knuckles"].append(rec)
        else:
            rec["gap_mm"] = _leaf_gap(new_bvh, V, side)
            rec["old_gap_mm"] = _leaf_gap(old_bvh, V, side)
            rep["leaves"].append(rec)
    return rep


def _leaf_gap(bvh, V, side) -> float:
    """Closest approach of a hinge leaf to the temple's inner face along x: for each leaf vertex whose x-ray meets the
    temple, side * (inner face x - vertex x) (+ = a gap inboard of the inner face, - = embedded); the minimum."""
    best = np.inf
    d = Vector((float(side), 0.0, 0.0))
    for p in V:
        o = Vector((0.0, float(p[1]), float(p[2])))
        hit = bvh.ray_cast(o, d)
        if hit[0] is None or hit[1].dot(d) > 0:
            continue
        best = min(best, side * (hit[0].x - float(p[0])))
    return float(best) if np.isfinite(best) else float("nan")


def _local(ob, W_asset, origin):
    M3, t = _matrix(ob)
    return (W_asset + origin - t) @ np.linalg.inv(M3).T


def _local_normals(ob, N_world):
    M3, _ = _matrix(ob)
    n = N_world @ M3
    return n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-300)


def build_mesh(name, V_local, polys, sharp_pairs, flat_polys, normals_local, materials, uv=None):
    """A new mesh datablock: n-gon polygons, sharp_edge, smooth shading, custom normals on the flat polygons."""
    me = bpy.data.meshes.new(name)
    me.from_pydata([tuple(map(float, v)) for v in V_local], [], [tuple(int(i) for i in p) for p in polys])
    me.update()
    if len(me.vertices) != len(V_local) or len(me.polygons) != len(polys):
        bpy.data.meshes.remove(me)
        raise RuntimeError("from_pydata changed the topology")
    for m in materials:
        me.materials.append(m)
    me.shade_smooth()
    eidx = {tuple(sorted(e.vertices)): e.index for e in me.edges}
    sharp = np.zeros(len(me.edges), bool)
    for a, b in sharp_pairs:
        sharp[eidx[(min(a, b), max(a, b))]] = True
    att = me.attributes.get("sharp_edge") or me.attributes.new("sharp_edge", "BOOLEAN", "EDGE")
    att.data.foreach_set("value", sharp)
    loops = np.zeros((len(me.loops), 3))
    ls = np.zeros(len(me.polygons), np.int32)
    me.polygons.foreach_get("loop_start", ls)
    lt = np.zeros(len(me.polygons), np.int32)
    me.polygons.foreach_get("loop_total", lt)
    for p in flat_polys:
        loops[ls[p]:ls[p] + lt[p]] = normals_local[p]
    me.normals_split_custom_set([tuple(map(float, n)) for n in loops])
    if uv is not None:
        lay = me.uv_layers.new(name="UVMap")
        lv = np.zeros(len(me.loops), np.int32)
        me.loops.foreach_get("vertex_index", lv)
        lay.data.foreach_set("uv", uv[lv].astype(np.float32).ravel())
    me.update()
    return me, int(sharp.sum())


def _roll(me, roll_mm, outer_long, plan, ob):
    """Apply a Bevel on the outer long edges (weights), re-check, and return the new mesh (or raise E_ROLL)."""
    att = me.attributes.get("bevel_weight_edge") or me.attributes.new("bevel_weight_edge", "FLOAT", "EDGE")
    eidx = {tuple(sorted(e.vertices)): e.index for e in me.edges}
    w = np.zeros(len(me.edges), np.float32)
    for a, b in outer_long:
        w[eidx[(min(a, b), max(a, b))]] = 1.0
    att.data.foreach_set("value", w)
    tmp = bpy.data.objects.new("g3d temple roll (temporary)", me)
    tmp.matrix_world = ob.matrix_world
    bpy.context.scene.collection.objects.link(tmp)
    try:
        m = tmp.modifiers.new("g3d roll", "BEVEL")
        m.width = float(roll_mm)
        m.segments = ROLL_SEGMENTS
        m.limit_method = "WEIGHT"
        m.edge_weight = "bevel_weight_edge"
        m.harden_normals = True
        m.affect = "EDGES"
        m.profile = 0.5
        m.use_clamp_overlap = True
        dg = bpy.context.evaluated_depsgraph_get()
        ev = tmp.evaluated_get(dg)
        out = bpy.data.meshes.new_from_object(ev, preserve_all_data_layers=True, depsgraph=dg)
    finally:
        bpy.data.objects.remove(tmp, do_unlink=True)
    _triangulate_nonplanar(out)
    try:
        chk = _roll_checks(out, plan, ob)
    except Exception as exc:                                  # noqa: BLE001
        bpy.data.meshes.remove(out)
        raise TempleError("E_ROLL", f"the rolled mesh could not be checked: {type(exc).__name__}: {exc}")
    if not chk["ok"]:
        bpy.data.meshes.remove(out)
        raise TempleError("E_ROLL", f"the rolled mesh fails {chk['failed']}", **chk)
    if out.attributes.get("bevel_weight_edge") is not None:
        out.attributes.remove(out.attributes["bevel_weight_edge"])
    return out, chk


def _roll_checks(me, plan, ob) -> dict:
    origin, side, tb = plan["origin"], plan["side"], plan["tb"]
    M3, t = _matrix(ob)
    co = np.zeros(len(me.vertices) * 3, np.float32)
    me.vertices.foreach_get("co", co)
    W = co.reshape(-1, 3).astype(np.float64) @ M3.T + t - origin
    h = tb.spec.hinge
    L = TB.to_local(W, h["z_mm"], h["axis_xz_mm"], h["opening_deg"], side=side)
    polys = [list(p.vertices) for p in me.polygons]
    E = {}
    for p in polys:
        for k in range(len(p)):
            e = (p[k], p[(k + 1) % len(p)])
            E[e] = E.get(e, 0) + 1
    open_ = sum(1 for (a, b) in E if (b, a) not in E)
    dup = sum(1 for c in E.values() if c > 1)
    und = {(min(a, b), max(a, b)) for a, b in E}
    chi = len(me.vertices) - len(und) + len(polys)
    planes = RP.facet_planes_chart(tb)
    worst = 0.0
    nfacet = 0
    for p in polys:
        P = L[p]
        for _, n, c in planes:
            d = P @ n - c
            if np.max(np.abs(d)) <= 2e-3:
                worst = max(worst, float(np.max(np.abs(d))))
                nfacet += 1
                break
    ps = tb.spec
    over = float(np.max(np.r_[L[:, 1] - ps.y_top(L[:, 0]), ps.y_bot(L[:, 0]) - L[:, 1]]))
    miss = 0.0
    for chain in (ps.top, ps.bottom):
        for d, y in chain:
            miss = max(miss, float(np.min(np.hypot(L[:, 0] - d, L[:, 1] - y))))
    failed = []
    if open_ or dup:
        failed.append("closed manifold")
    if chi != 2:
        failed.append("Euler characteristic 2")
    if worst > ROLL_PLANAR_MM:
        failed.append(f"facet planarity <= {ROLL_PLANAR_MM} mm")
    if over > ROLL_SILHOUETTE_MM or miss > ROLL_SILHOUETTE_MM:
        failed.append(f"side silhouette = outline <= {ROLL_SILHOUETTE_MM} mm")
    return {"ok": not failed, "failed": failed, "open_edges": open_, "duplicate_edges": dup, "euler": chi,
            "facet_planarity_mm": worst, "facet_polygons": nfacet, "silhouette_overshoot_mm": over,
            "outline_knot_miss_mm": miss}


def _outer_long_edges(tb) -> list:
    topo = TB.mesh_topology(tb.polys)
    out = []
    for (a, b), p1 in topo["directed"].items():
        if a > b:
            continue
        p2 = topo["directed"][(b, a)]
        kinds = {tb.poly_kind[p1], tb.poly_kind[p2]}
        if "outer" in kinds and kinds & {"top", "bottom"}:
            out.append((a, b))
    return out


def _uv(tb) -> np.ndarray:
    """A planar (d, y) projection (outer / inner faces) for meshes whose old datablock had UVs."""
    V = tb.V
    return np.stack([(V[:, 0] - tb.spec.d0) / 150.0, (V[:, 1] + 40.0) / 80.0], axis=1)


# --------------------------------------------------------------------------------------------- the tool
def _load_spec(spec):
    if isinstance(spec, (str, Path)) and not str(spec).lstrip().startswith("{"):
        return json.loads(Path(spec).read_text(encoding="utf-8"))
    if isinstance(spec, str):
        return json.loads(spec)
    return spec


def _refused(code, message, fields=None, **extra) -> dict:
    out = {"status": "refused", "code": code, "message": message, "fields": TS.plain(fields or {})}
    out.update(extra)
    return out


def faceted_temple(spec, *, side="both", targets=None, decals="auto", dry_run: bool = False) -> dict:
    """Build faceted temples from a g3d.temple/1 spec and swap them into the scene (module docstring)."""
    t0 = time.perf_counter()
    c0 = time.process_time()
    scene = bpy.context.scene
    digest0 = scene_digest(scene)
    try:
        spec = _load_spec(spec)
        sides = _sides(side)
        origin = asset_origin(scene)
        tg = select_targets(sides, targets, scene, origin)
        spec = json.loads(json.dumps(spec))
        hinge = spec.get("hinge") or {}
        axis_used = []
        if isinstance(hinge, dict) and hinge.get("axis_xz_mm") is None and "z_mm" in hinge:
            ax, used = default_axis(sides[0], float(hinge["z_mm"]), scene, origin, set(tg.values()))
            if ax is not None:
                hinge["axis_xz_mm"] = ax
                axis_used = used
        tc = time.perf_counter()
        tb = TB.build_temple(spec)
        core_ms = 1000.0 * (time.perf_counter() - tc)
        rec = RP.receipt(tb, core_ms=core_ms)
        rec["axis_from"] = axis_used or ("spec" if tb.spec.hinge["axis_xz_mm"] is not None else None)
        rec["core_digest"] = core_digest(tb)
        plans = {}
        planned_meshes: list = []
        for s in sides:
            plans[s] = _plan_side(tb, s, tg[s], origin, scene, decals, set(tg.values()), planned_meshes)
        rec["sides"] = {("+x" if s > 0 else "-x"): _side_receipt(plans[s]) for s in sides}
        warnings = list(rec["warnings"])
        for s in sides:
            warnings += plans[s]["warnings"]
        rec["warnings"] = warnings
        if dry_run:
            _cleanup_planned(plans)
            rec["status"] = "planned"
            rec["scene_unchanged"] = scene_digest(scene) == digest0
            rec["budget"]["total_ms"] = 1000.0 * (time.perf_counter() - t0)
            rec["budget"]["total_cpu_ms"] = 1000.0 * (time.process_time() - c0)
            return TS.plain(rec)
        _write(plans, tb)
        rec["status"] = "ok"
        rec["budget"]["total_ms"] = 1000.0 * (time.perf_counter() - t0)
        rec["budget"]["total_cpu_ms"] = 1000.0 * (time.process_time() - c0)
        return TS.plain(rec)
    except TempleError as e:
        _cleanup_planned(locals().get("plans"), locals().get("planned_meshes"))
        return _refused(e.code, e.message, e.fields, scene_unchanged=scene_digest(scene) == digest0)
    except Exception as exc:                                  # noqa: BLE001 - report, never raise
        _cleanup_planned(locals().get("plans"), locals().get("planned_meshes"))
        return {"status": "failed", "error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc(limit=8),
                "scene_unchanged": scene_digest(scene) == digest0}


def core_digest(tb) -> dict:
    h = hashlib.sha256()
    h.update(np.ascontiguousarray(tb.V).tobytes())
    h.update(json.dumps(tb.polys).encode())
    return {"sha256": h.hexdigest(), "V": tb.V.tolist()}


def _cleanup_planned(plans, meshes=None):
    for me in meshes or []:
        try:
            if me.users == 0 and me.name in bpy.data.meshes:
                bpy.data.meshes.remove(me)
        except ReferenceError:                                # already removed
            pass
    for p in (plans or {}).values():
        for me in [p.get("mesh")] + list(p.get("decal_meshes", [])):
            try:
                if me is not None and me.users == 0 and me.name in bpy.data.meshes:
                    bpy.data.meshes.remove(me)
            except ReferenceError:
                pass


def _plan_side(tb, side, ob, origin, scene, decals, targets, planned_meshes: list) -> dict:
    W, polys, _ = tb.world(side)
    Wn = _world_normals(tb, side)
    Vloc = _local(ob, W, origin)
    M3, _ = _matrix(ob)
    pl = polys if np.linalg.det(M3) > 0 else [p[::-1] for p in polys]
    nloc = _local_normals(ob, Wn)
    old_V, old_T, old_polys = mesh_arrays(ob, origin)
    old_bvh = _bvh(old_V, old_polys)
    new_bvh = _bvh(W, polys)
    excl = set(targets)
    plan = {"side": side, "object": ob, "origin": origin, "tb": tb, "warnings": [], "W": W}
    mats = list(ob.data.materials)
    me, nsharp = build_mesh(ob.data.name + " | g3d faceted (planned)", Vloc, pl, tb.sharp, tb.flat_polys, nloc,
                            mats, uv=_uv(tb) if len(ob.data.uv_layers) else None)
    plan["mesh"] = me
    planned_meshes.append(me)
    plan["sharp_edges"] = nsharp
    plan["custom_corners"] = int(sum(len(tb.polys[p]) for p in tb.flat_polys))
    if tb.spec.roll_mm > 0:
        tmin = tb.tables["thickness"]["min_mm"]
        ps = tb.spec
        knots = np.unique(np.r_[ps.top[:, 0], ps.bottom[:, 0]])
        hmin = float(np.min(ps.y_top(knots) - ps.y_bot(knots)))
        limit = min(tmin - ps.options["t_min_mm"], 0.5 * hmin)
        if tb.spec.roll_mm >= limit:
            bpy.data.meshes.remove(me)
            plan["mesh"] = None
            raise TempleError("E_ROLL", f"roll_mm {tb.spec.roll_mm:g} does not fit: the thinnest section leaves "
                              f"{limit:.3f} mm (thickness {tmin:.3f} - t_min, or half the {hmin:.3f} mm outline height)",
                              roll_mm=tb.spec.roll_mm, limit_mm=limit, min_thickness_mm=tmin, min_height_mm=hmin)
        out, chk = _roll(me, tb.spec.roll_mm, _outer_long_edges(tb), plan, ob)
        planned_meshes.append(out)
        bpy.data.meshes.remove(me)
        plan["mesh"] = out
        plan["roll"] = chk
        _refacet_normals(out, plan, ob)
    region = list(tb.poly_region)
    plan["decals"] = []
    plan["decal_meshes"] = []
    axis = tb.spec.hinge["axis_xz_mm"]
    hparts = hinge_parts(side, axis, tb.spec.hinge["z_mm"], scene, origin, excl)
    plan["hinge_parts"] = [{"name": ob.name, "kind": kind} for ob, kind in hparts]
    if decals == "auto":                     # §3: hinge parts are never moved, so never a decal
        plan["decals"] = plan_decals(old_bvh, new_bvh, region, side, scene, origin,
                                     excl | {ob for ob, _ in hparts})
        for d in plan["decals"]:
            if len([r for r in d["regions"] if r not in ("top", "bottom", "cap_hinge", "cap_tip")]) > 1:
                plan["warnings"].append({"code": "W_DECAL_SPANS_CREASE", "decal": d["name"], "regions": d["regions"],
                                         "message": f"decal {d['name']} lands on {d['regions']}"})
            dob = d["object"]
            dm = dob.data.copy()
            dm.name = dob.data.name + " | g3d decal (planned)"
            loc = _local(dob, d["V_new"], origin)
            dm.vertices.foreach_set("co", loc.astype(np.float32).ravel())
            dm.update()
            d["mesh"] = dm
            plan["decal_meshes"].append(dm)
    excl_h = excl | {d["object"] for d in plan["decals"]}
    plan["hinge"] = hinge_report(new_bvh, old_bvh, side, axis, tb.spec.hinge["z_mm"], scene, origin, excl_h,
                                 parts=hparts)
    for lf in plan["hinge"]["leaves"]:
        g = lf["gap_mm"]
        if g > TS.HINGE_LEAF_GAP_MM or -g > TS.HINGE_LEAF_EMBED_MM:
            plan["warnings"].append({"code": "W_HINGE_LEAF", "part": lf["name"], "gap_mm": g,
                                     "message": f"hinge leaf {lf['name']} {'gap' if g > 0 else 'embedded'} "
                                                f"{abs(g):.2f} mm"})
    for k in plan["hinge"]["frame_knuckles"]:
        if k["penetration_mm"] > TS.KNUCKLE_PENETRATION_MM:
            plan["warnings"].append({"code": "W_KNUCKLE_PENETRATION", "part": k["name"],
                                     "penetration_mm": k["penetration_mm"],
                                     "message": f"frame knuckle {k['name']} enters the temple by "
                                                f"{k['penetration_mm']:.2f} mm"})
    return plan


def _world_normals(tb, side) -> np.ndarray:
    h = tb.spec.hinge
    a = math.radians(h["opening_deg"])
    # local (d, y, X) normal -> world (x = X, y, z = -d), then the opening rotation about y (x-z plane)
    n = np.stack([tb.normals[:, 2], tb.normals[:, 1], -tb.normals[:, 0]], axis=1)
    c, s_ = math.cos(a), math.sin(a)
    n = np.stack([c * n[:, 0] - s_ * n[:, 2], n[:, 1], s_ * n[:, 0] + c * n[:, 2]], axis=1)
    if side < 0:
        n[:, 0] = -n[:, 0]
    return n


NONPLANAR_MM = 1e-4


def _triangulate_nonplanar(me) -> int:
    """Split every non-planar polygon the Bevel left (its corner patches where the rounded strip meets a cap) into
    triangles, so each of its folds is a real edge that can be marked sharp. Returns how many were split."""
    import bmesh
    bm = bmesh.new()
    bm.from_mesh(me)
    # the Bevel's corner patches can hold coincident vertices and zero-area faces: merge and dissolve them first
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=NONPLANAR_MM)
    bmesh.ops.dissolve_degenerate(bm, dist=NONPLANAR_MM, edges=bm.edges)
    bad = []
    for f in bm.faces:
        if len(f.verts) <= 3:
            continue
        P = np.array([v.co[:] for v in f.verts], np.float64)
        n = np.cross(P, np.roll(P, -1, axis=0)).sum(axis=0)
        L = np.linalg.norm(n)
        if L <= 0:
            continue
        n /= L
        if float(np.max(np.abs((P - P.mean(0)) @ n))) > NONPLANAR_MM:
            bad.append(f)
    if bad:
        bmesh.ops.triangulate(bm, faces=bad)
    bm.to_mesh(me)
    bm.free()
    me.update()
    return len(bad)


def _fan_normals(me) -> np.ndarray:
    """Corner normals (object space) computed in float64: corners around a vertex joined across non-sharp manifold
    edges form a fan; each fan takes the area-weighted mean of its faces' Newell normals."""
    co = np.zeros(len(me.vertices) * 3, np.float32)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3).astype(np.float64)
    sharp = np.zeros(len(me.edges), bool)
    att = me.attributes.get("sharp_edge")
    if att is not None:
        att.data.foreach_get("value", sharp)
    ek = {tuple(sorted(e.vertices)): e.index for e in me.edges}
    nl = len(me.loops)
    parent = list(range(nl))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    fnorm = np.zeros((len(me.polygons), 3))
    loop_at = {}                                               # (edge key, vertex) -> [loops]
    for p in me.polygons:
        vs = list(p.vertices)
        P = co[vs]
        fnorm[p.index] = np.cross(P, np.roll(P, -1, axis=0)).sum(axis=0)          # 2 x area x normal
        k = len(vs)
        for i in range(k):
            li = p.loop_start + i
            for nb in (vs[i - 1], vs[(i + 1) % k]):
                key = (min(vs[i], nb), max(vs[i], nb))
                loop_at.setdefault((key, vs[i]), []).append(li)
    for (key, v), ls in loop_at.items():
        e = ek.get(key)
        if e is not None and not sharp[e] and len(ls) == 2:
            a, b = find(ls[0]), find(ls[1])
            if a != b:
                parent[max(a, b)] = min(a, b)
    lp = np.zeros(nl, np.int64)
    for p in me.polygons:
        lp[p.loop_start:p.loop_start + p.loop_total] = p.index
    roots = np.array([find(i) for i in range(nl)])
    acc = np.zeros((nl, 3))
    np.add.at(acc, roots, fnorm[lp])
    out = acc[roots]
    L = np.linalg.norm(out, axis=1, keepdims=True)
    return out / np.maximum(L, 1e-300)


def _resharpen(me, kink_deg: float) -> int:
    """After the roll: sharp_edge = the edges marked before the Bevel (carried by the modifier) plus every manifold edge
    whose faces bend more than kink_deg (the bevel's ends against the caps), so the rounded strips stay smooth and the
    junctions stay crisp. Returns the number of sharp edges."""
    co = np.zeros(len(me.vertices) * 3, np.float32)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3).astype(np.float64)
    fn = np.zeros((len(me.polygons), 3))
    for p in me.polygons:                                       # float64 Newell normals (tiny bevel faces)
        P = co[list(p.vertices)]
        n = np.cross(P, np.roll(P, -1, axis=0)).sum(axis=0)
        L = np.linalg.norm(n)
        fn[p.index] = n / L if L > 0 else n
    ne = len(me.edges)
    cur = np.zeros(ne, bool)
    att = me.attributes.get("sharp_edge")
    if att is not None:
        att.data.foreach_get("value", cur)
    else:
        att = me.attributes.new("sharp_edge", "BOOLEAN", "EDGE")
    faces_of = [[] for _ in range(ne)]
    ek_index = {tuple(sorted(e.vertices)): e.index for e in me.edges}
    for p in me.polygons:
        for ek in p.edge_keys:
            faces_of[ek_index[tuple(sorted(ek))]].append(p.index)
    cosk = math.cos(math.radians(kink_deg))
    new = cur.copy()
    for e, fs in enumerate(faces_of):
        if len(fs) == 2 and float(fn[fs[0]] @ fn[fs[1]]) < cosk:
            new[e] = True
    att = me.attributes.get("sharp_edge")
    att.data.foreach_set("value", new)
    me.update()
    return int(new.sum())


def _refacet_normals(me, plan, ob):
    """After the roll: every polygon lying on a facet plane takes that plane's normal at its corners; every other
    corner is auto-smoothed again (zero custom vector) against the re-derived sharp edges."""
    tb, side, origin = plan["tb"], plan["side"], plan["origin"]
    M3, t = _matrix(ob)
    co = np.zeros(len(me.vertices) * 3, np.float32)
    me.vertices.foreach_get("co", co)
    W = co.reshape(-1, 3).astype(np.float64) @ M3.T + t - origin
    h = tb.spec.hinge
    L = TB.to_local(W, h["z_mm"], h["axis_xz_mm"], h["opening_deg"], side=side)
    planes = RP.facet_planes_chart(tb)
    _resharpen(me, tb.spec.options["kink_sharp_deg"] - 0.5)     # 0.5 deg margin: float32 polygon normals
    cn = _fan_normals(me)                                       # explicit everywhere: no reliance on auto fans
    for p in me.polygons:
        P = L[list(p.vertices)]
        for _, n, c in planes:
            if np.max(np.abs(P @ n - c)) <= 2e-3:
                nl = np.array([n[2], n[1], -n[0]])
                a = math.radians(h["opening_deg"])
                nl = np.array([math.cos(a) * nl[0] - math.sin(a) * nl[2], nl[1],
                               math.sin(a) * nl[0] + math.cos(a) * nl[2]])
                if side < 0:
                    nl[0] = -nl[0]
                nloc = _local_normals(ob, nl[None, :])[0]
                cn[p.loop_start:p.loop_start + p.loop_total] = nloc
                break
    me.normals_split_custom_set([tuple(map(float, n)) for n in cn])
    me.update()


def _side_receipt(plan) -> dict:
    ob = plan["object"]
    return {"target": ob.name, "mesh": ob.data.name, "sharp_edges": plan["sharp_edges"],
            "custom_normal_corners": plan["custom_corners"], "roll": plan.get("roll"),
            "decals": [{k: d[k] for k in ("name", "vertices", "misses", "max_move_mm", "regions", "normal")}
                       for d in plan["decals"]],
            "hinge": plan["hinge"], "hinge_parts": plan.get("hinge_parts", []), "warnings": plan["warnings"],
            "planned_vertices": len(plan["mesh"].vertices), "planned_polygons": len(plan["mesh"].polygons)}


def _write(plans, tb) -> None:
    done = []
    try:
        for s, plan in plans.items():
            ob = plan["object"]
            bk = _backup_record(ob)
            first = not bk
            old = ob.data
            name = old.name
            if first:
                bk = {"mesh": None, "modifiers": [], "props": {}, "decals": {}}
                for k in ("region", PROP_TEMPLE):
                    v = ob.get(k)
                    bk["props"][k] = None if v is None else (v if isinstance(v, (str, int, float)) else str(v))
                bk["original_name"] = name
            for i, m in enumerate(list(ob.modifiers)):
                if m.type == "WEIGHTED_NORMAL":
                    if first:
                        bk["modifiers"].append({"index": i, "name": m.name, "mode": m.mode, "weight": m.weight,
                                                "thresh": m.thresh, "keep_sharp": m.keep_sharp,
                                                "use_face_influence": m.use_face_influence,
                                                "vertex_group": m.vertex_group,
                                                "invert_vertex_group": m.invert_vertex_group,
                                                "show_viewport": m.show_viewport, "show_render": m.show_render})
                    ob.modifiers.remove(m)
            new = plan["mesh"]
            if first:
                old.name = bk["original_name"] + BACKUP_SUFFIX
                old.use_fake_user = True
                bk["mesh"] = old.name
                ob.data = new
                new.name = bk["original_name"]
            else:
                ob.data = new
                old.name = old.name + " (g3d discarded)"
                new.name = bk["original_name"]
                if old.users == 0:
                    bpy.data.meshes.remove(old)
            for d in plan["decals"]:
                dob = d["object"]
                dold = dob.data
                drec = bk["decals"].get(dob.name)
                if drec is None:
                    dname = dold.name
                    dold.name = dname + BACKUP_SUFFIX
                    dold.use_fake_user = True
                    bk["decals"][dob.name] = {"mesh": dold.name, "original_name": dname}
                    dob.data = d["mesh"]
                    d["mesh"].name = dname
                else:
                    dob.data = d["mesh"]
                    dold.name = dold.name + " (g3d discarded)"
                    d["mesh"].name = drec["original_name"]
                    if dold.users == 0:
                        bpy.data.meshes.remove(dold)
            ob["partRole"] = "temple"
            ob["region"] = "temple_+x" if s > 0 else "temple_-x"
            ob[PROP_TEMPLE] = json.dumps(TS.plain({"schema": SCHEMA, "core_version": CORE_VERSION,
                                                   "spec_sha256": tb.spec.sha256, "side": "+x" if s > 0 else "-x",
                                                   "canonical_spec": tb.spec.canonical,
                                                   "facets": tb.tables["facets"]}), sort_keys=True)
            ob[PROP_BACKUP] = json.dumps(bk, sort_keys=True)
            done.append(ob)
    except Exception:
        for ob in done:
            unfaceted_temple(ob)
        raise


def _backup_record(ob) -> dict:
    v = ob.get(PROP_BACKUP)
    try:
        return dict(json.loads(v)) if isinstance(v, str) else {}
    except ValueError:
        return {}


def unfaceted_temple(obj) -> dict:
    """Put back the datablocks faceted_temple replaced (the temple and its decals), the WeightedNormal modifiers and
    the extras as they were. Byte-identical: the original datablocks themselves return."""
    try:
        ob = _obj(obj)
    except LookupError as exc:
        return _refused("E_TARGET", str(exc))
    bk = _backup_record(ob)
    if not bk or bpy.data.meshes.get(bk.get("mesh") or "") is None:
        return _refused("E_TARGET", f"{ob.name} has no g3d pre-temple backup", target=ob.name)
    cur = ob.data
    old = bpy.data.meshes[bk["mesh"]]
    cur.name = cur.name + " (g3d discarded)"
    ob.data = old
    old.name = bk["original_name"]
    old.use_fake_user = False
    if cur.users == 0:
        bpy.data.meshes.remove(cur)
    restored = []
    for dname, drec in bk.get("decals", {}).items():
        dob = bpy.data.objects.get(dname)
        dm = bpy.data.meshes.get(drec["mesh"])
        if dob is None or dm is None:
            continue
        dc = dob.data
        dc.name = dc.name + " (g3d discarded)"
        dob.data = dm
        dm.name = drec["original_name"]
        dm.use_fake_user = False
        if dc.users == 0:
            bpy.data.meshes.remove(dc)
        restored.append(dname)
    for rec in sorted(bk.get("modifiers", []), key=lambda r: r["index"]):
        m = ob.modifiers.new(rec["name"], "WEIGHTED_NORMAL")
        for k in ("mode", "weight", "thresh", "keep_sharp", "use_face_influence", "vertex_group",
                  "invert_vertex_group", "show_viewport", "show_render"):
            setattr(m, k, rec[k])
        while list(ob.modifiers).index(m) > rec["index"]:
            with bpy.context.temp_override(object=ob):
                bpy.ops.object.modifier_move_up(modifier=m.name)
    for k, v in bk.get("props", {}).items():
        if v is None:
            if k in ob:
                del ob[k]
        else:
            ob[k] = v
    if PROP_BACKUP in ob:
        del ob[PROP_BACKUP]
    return {"status": "ok", "object": ob.name, "mesh": ob.data.name, "decals_restored": restored}


# --------------------------------------------------------------------------------------------- check_relief
def check_relief(objects=None, *, photo_creases: int | None = None, hinge_z: float | None = None) -> dict:
    """§5 geometry numbers of the evaluated temples (both sides): from their g3d_temple extras (chart, facet planes)
    when present, else the world chart (d = hinge_z - z; hinge_z defaults to the temple's front end)."""
    scene = bpy.context.scene
    origin = asset_origin(scene)
    try:
        if objects:
            obs = [_obj(o) for o in objects]
        else:
            c = temple_candidates(scene, origin)
            obs = [c[s][0][1] for s in (1, -1) if c[s]]
    except LookupError as exc:
        return _refused("E_TARGET", str(exc))
    out = {"status": "ok", "temples": {}}
    for ob in obs:
        V, T, _ = mesh_arrays(ob, origin, evaluated=True)
        side = 1 if float(V[:, 0].mean()) > 0 else -1
        ext = ob.get(PROP_TEMPLE)
        planes = None
        if isinstance(ext, str):
            ex = json.loads(ext)
            spec = ex["canonical_spec"]
            h = spec["hinge"]
            L = TB.to_local(V, h["z_mm"], h["axis_xz_mm"], h["opening_deg"], side=side)
            F = MS.chart_faces(T, side)
            tb = TB.build_temple(spec)
            planes = RP.facet_planes_chart(tb)
            ze, chart = spec["zone_end_d"], "g3d_temple extras"
        else:
            hz = float(V[:, 2].max()) if hinge_z is None else float(hinge_z)
            L, F = MS.chart_mesh(V, T, side, hz)
            ze, chart = 50.0, f"world (hinge z {hz:.2f})"
        m = MS.measure_temple(L, F, zone_end=ze, facet_planes=planes, photo_creases=photo_creases)
        m["chart"] = chart
        m["object"] = ob.name
        out["temples"]["+x" if side > 0 else "-x"] = TS.plain(m)
    return out


# --------------------------------------------------------------------------------------------- §4 helpers
polygon_section = SEC.polygon_section


def loft_sections(stations, path=None, caps: bool = True, *, name: str | None = None, material=None,
                  part_role: str | None = None) -> dict:
    """The numpy loft; with `name`, also a new mesh object (sharp longitudinal edges marked, smooth shaded)."""
    try:
        L = LOFT.loft_sections(stations, path, caps)
    except (LOFT.LoftError, SEC.SectionError) as exc:
        return _refused("E_SPEC", str(exc))
    rep = {"status": "ok", **{k: L[k] for k in ("closed", "euler", "volume_mm3", "vertices_per_section",
                                                "strip_twist_mm")}, "sharp_edges": len(L["sharp_edges"])}
    if name:
        me, _ = build_mesh(name, L["V"], L["F"].tolist(), L["sharp_edges"], [], np.zeros((len(L["F"]), 3)),
                           [bpy.data.materials[material]] if isinstance(material, str) else
                           ([material] if material is not None else []))
        ob = bpy.data.objects.new(name, me)
        bpy.context.scene.collection.objects.link(ob)
        if part_role:
            ob["partRole"] = part_role
        rep["object"] = ob.name
    return TS.plain(rep)


def check_round_corners(obj, axis: int = 2) -> dict:
    ob = _obj(obj)
    V, T, _ = mesh_arrays(ob, asset_origin(), evaluated=True)
    return TS.plain(SEC.check_round_corners_mesh(V, T, axis=axis))
