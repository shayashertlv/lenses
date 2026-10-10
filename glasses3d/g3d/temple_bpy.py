"""Blender layer of WP6 v2 faceted temples (plan/WP6_FACETS.md §2.5, §2.6). Runs inside Blender 5.2.

    faceted_temple(spec, *, side="both", targets=None, decals="auto", dry_run=False) -> receipt
    unfaceted_temple(obj) -> report
    build_variant_meshes(spec, *, side="both", targets=None, decals="auto") -> {"status", "meshes": {side: mesh name},
        "overrides": {object name: mesh name}, ...}; remove_variant_meshes(names=None) -> report
    check_relief(objects=None, *, photo_creases=None) -> report
    polygon_section(points, corners) / loft_sections(stations, path=None, caps=True, name=None, ...) /
    check_round_corners(obj)

Thin by design: the numpy core (g3d/temple/) plans and checks the solid, roll included (§2.5); this layer reads the
scene, places the temples, writes once, and reports. Conventions:
- the spec is g3d.temple/2 only; a /1 spec is converted by the caller (g3d.temple.convert.v1_to_v2);
- coordinates: the spec is in ASSET millimetres (scene mm minus the scene's `mdl_bridge_underside`, the exporter's
  origin; Prada's is (0, 0, 0)), +Y up, +Z front; the +X temple is the spec, the -X temple its exact mirror;
- targets by the sign of x, never by name: per side, the temple-role mesh reaching furthest back (>= 60 mm), or the
  `targets` given ({"+x": name, "-x": name} or names whose x sign picks the side). Not a temple-role mesh, shape keys,
  or a modifier other than WeightedNormal: E_TARGET; more than one material slot: E_MULTI_MATERIAL (multi-material
  temples are deferred);
- the mesh is the core's polygons as they are: facet cells as planar n-gons, every other outer cell (base, faded, ramp)
  as the core's own triangles, so Blender and the GLB share the core's triangulation; sharp_edge = the core's sharp
  set; the non-faded facet polygons (TempleBuild.flat_polys) carry their face normal as custom normals, and so do the
  roll polygons' corners on the line where the roll leaves such a facet (the arc is tangent there; one smooth fan
  holds one custom normal), every other corner is auto-smoothed (zero custom vector);
- plan fully (core, decals, hinge report), then write once: each target's mesh datablock is swapped in place (object
  name, transform, materials and other extras unchanged); the previous datablock stays as `<mesh> | g3d pre-temple`
  with a fake user (the earliest backup wins), WeightedNormal modifiers are removed and their settings kept in
  `g3d_temple_backup`; `unfaceted_temple` puts the very same datablocks back (byte-identical);
- decals="auto": parts lying within 1 mm of the old temple surface that are open or thin (measure.is_surface_decal)
  move by CHART TRANSFER: each decal vertex keeps the chart (d, y) of its foot point on the old temple surface and its
  signed standoff from it, and lands at the new surface X(d, y) plus that standoff along the new surface normal (the
  -X side mirrored). Motion is reported per decal; W_DECAL_SPANS_CREASE when one decal lands on more than one region;
- extras: partRole temple, region temple_+x / temple_-x, g3d_temple = canonical /2 spec + core version + sha256 +
  facet table (the exporter copies it to the GLB only once WP4's allowlist names it);
- hinge parts are never moved (measure.hinge_part_kind: knuckles and temple leaves are excluded from decal planning,
  however close and thin they are); the receipt reports the leaf-to-inner-face gap, temple-knuckle contact and frame-
  knuckle penetration (W_HINGE_LEAF, W_KNUCKLE_PENETRATION);
- build_variant_meshes plans like faceted_temple but writes nothing to the objects: it leaves new, unlinked mesh
  datablocks (temples and transferred decals, in each object's local frame, tagged `g3d_variant`) for render
  `mesh_overrides` (§3.2); remove_variant_meshes deletes them again.
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
VARIANT_SUFFIX = " | g3d variant"
PROP_TEMPLE = "g3d_temple"
PROP_BACKUP = "g3d_temple_backup"
PROP_VARIANT = "g3d_variant"
DECAL_NEAR_MM = MS.DECAL_NEAR_MM   # a decal lies within this of the old temple surface (the shared rule:
DECAL_THIN_MM = MS.DECAL_THIN_MM   # measure.is_surface_decal, also the host's photo-crease decal mask)
EDGE_SLACK_MM = 1e-3               # a (d, y) this near the outer triangles is inside (float32 BVH, ~1e-4 mm drift)
HINGE_NEAR_MM = MS.HINGE_NEAR_MM   # hinge parts (the shared rule: measure.hinge_part_kind, also the host's decal mask)
HINGE_X_MM = MS.HINGE_X_MM
KNUCKLE_MAX_MM = MS.KNUCKLE_MAX_MM
TEMPLE_MIN_REACH_MM = 60.0
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


def _side_key(s: int) -> str:
    return "+x" if s > 0 else "-x"


def mesh_arrays(ob, origin, evaluated: bool = False):
    """(V asset mm, triangles, polygons as lists) of an object's base (or evaluated) mesh."""
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
                raise TempleError("E_TARGET", f"{ob.name} lies on the {_side_key(s)} side, not {key}",
                                  target=ob.name, x_sign=s)
            chosen[s] = ob
    for s in sides:
        if s not in chosen:
            if not cands[s]:
                raise TempleError("E_TARGET", f"no temple-role mesh reaching >= {TEMPLE_MIN_REACH_MM:g} mm back on "
                                  f"the {_side_key(s)} side", side=s)
            if len(cands[s]) > 1 and cands[s][1][0] > 0.9 * cands[s][0][0]:
                raise TempleError("E_TARGET", f"two temple-role meshes on the {_side_key(s)} side reach back alike: "
                                  f"{[o.name for _, o in cands[s][:2]]}; pass targets", side=s,
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
        if len(ob.material_slots) > 1:
            raise TempleError("E_MULTI_MATERIAL", f"{ob.name} has {len(ob.material_slots)} material slots; "
                              f"faceted temples support one material (multi-material temples are deferred)",
                              target=ob.name, materials=[sl.material.name if sl.material else None
                                                         for sl in ob.material_slots])
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


class OuterSurface:
    """The core's outer surface (outer and roll polygons) as a function of the chart (d, y): a BVH over its triangles
    flattened to (d, y, 0) finds the triangle containing (or nearest to) a point, whose plane X = a + b d + c y gives
    the value (extended beyond the outline by that plane), its polygon the outward normal and the region label."""

    def __init__(self, tb):
        kinds = np.array([k in TB.OUTER_KINDS for k in tb.poly_kind])
        sel = kinds[tb.tri_poly]
        self.T = tb.tris[sel]
        self.tri_poly = tb.tri_poly[sel]
        P = tb.V[self.T]
        M = np.stack([np.ones(P.shape[:2]), P[..., 0], P[..., 1]], axis=-1)
        self.coef = np.linalg.solve(M, P[..., 2:3])[..., 0]
        self.normals = tb.normals
        self.region = [r.split("/")[0] for r in tb.poly_region]
        verts = [(float(d), float(y), 0.0) for d, y in tb.V[:, :2]]
        self.bvh = BVHTree.FromPolygons(verts, [tuple(int(i) for i in t) for t in self.T], all_triangles=True,
                                        epsilon=0.0)

    def at(self, d: float, y: float) -> tuple[float, np.ndarray, str, bool]:
        """(X, unit outward normal (local d, y, X), region label, inside the outline). A vertical ray finds the
        containing triangle (find_nearest's float32 closest point drifts ~1e-4 mm on long thin triangles); outside the
        outline the nearest triangle extends its plane."""
        hit = self.bvh.ray_cast(Vector((float(d), float(y), 1.0)), Vector((0.0, 0.0, -1.0)))
        inside = hit[0] is not None
        if inside:
            k = hit[2]
        else:                                         # outside, or a ray slipping between two triangles at an edge
            _, _, k, dist = self.bvh.find_nearest(Vector((float(d), float(y), 0.0)))
            inside = dist <= EDGE_SLACK_MM
        c = self.coef[k]
        p = self.tri_poly[k]
        return float(c[0] + c[1] * d + c[2] * y), self.normals[p], self.region[p], inside


def find_decals(old_bvh, side, scene, origin, exclude) -> list:
    """[(object, V asset mm, signed distances to the old temple)] of the surface decals on one side: every vertex
    within DECAL_NEAR_MM of the old temple surface, and measure.is_surface_decal (open, or thin by its box or along
    the surface normal)."""
    out = []
    for ob in scene.objects:
        if ob in exclude or ob.type != "MESH" or len(ob.data.vertices) == 0:
            continue
        V, _, polys = mesh_arrays(ob, origin)
        lo, hi = _bbox(V)
        if side * (lo[0] + hi[0]) <= 0:
            continue
        far = False
        for v in V:                                   # cheap rejection first: any vertex beyond DECAL_NEAR_MM
            loc, _, _, dist = old_bvh.find_nearest(Vector(tuple(map(float, v))))
            if loc is None or dist > DECAL_NEAR_MM:
                far = True
                break
        if far:
            continue
        E = {}
        for p in polys:
            for k in range(len(p)):
                e = (min(p[k], p[(k + 1) % len(p)]), max(p[k], p[(k + 1) % len(p)]))
                E[e] = E.get(e, 0) + 1
        open_ = any(c == 1 for c in E.values())
        sd = _signed_distance(old_bvh, V, side)
        if MS.is_surface_decal(sd, float(np.min(hi - lo)), open_):
            out.append((ob, V, sd))
    return out


def transfer_decal(V, old_bvh, surf: OuterSurface, side: int, hinge: dict) -> dict:
    """Chart transfer of one decal (module docstring): per vertex the foot point Q on the old temple (nearest), the
    signed standoff s = +-|P - Q| (+ on the outward side of the old face), Q's chart (d, y) -> the new surface point
    S(d, y) + s n(d, y), mirrored for side -1. Returns {"V_new", "standoff", "moves", "regions", "outside"}."""
    n = len(V)
    Q = np.zeros((n, 3))
    s = np.zeros(n)
    for i, p in enumerate(V):
        loc, nrm, _, dist = old_bvh.find_nearest(Vector(tuple(map(float, p))))
        Q[i] = np.array(loc)
        s[i] = dist if float(np.dot(p - Q[i], np.array(nrm))) >= 0 else -dist
    Lq = TB.to_local(Q, hinge["z_mm"], hinge["axis_xz_mm"], hinge["opening_deg"], side=side)
    P = np.zeros((n, 3))
    regions, outside = set(), 0
    for i in range(n):
        d, y = float(Lq[i, 0]), float(Lq[i, 1])
        X, nrm, reg, inside = surf.at(d, y)
        P[i] = np.array([d, y, X]) + s[i] * nrm
        regions.add(reg)
        outside += not inside
    W = TB.to_world(P, hinge["z_mm"], hinge["axis_xz_mm"], hinge["opening_deg"])
    if side < 0:
        W[:, 0] = -W[:, 0]
    return {"V_new": W, "standoff": s, "moves": np.linalg.norm(W - V, axis=1), "regions": sorted(regions),
            "outside": int(outside)}


def hinge_parts(side, axis, hinge_z, scene, origin, exclude) -> list:
    """[(object, kind)] of one side's hinge parts (measure.hinge_part_kind: "temple_knuckle", "frame_knuckle",
    "leaf"), in scene order; empty without a hinge axis. These are never moved: decal planning never takes one as a
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


def build_mesh(name, V_local, polys, sharp_pairs, flat_polys, normals_local, materials, uv=None, corners=None):
    """A new mesh datablock: the given polygons, sharp_edge, smooth shading, custom normals on the flat polygons (or,
    with `corners`, the given per-corner custom normals in polygon order, zero = auto-smoothed)."""
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
    if corners is not None:
        loops = np.asarray(corners, np.float64)
    else:
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


def corner_normals(tb, polys, n_poly) -> np.ndarray:
    """Per corner of `polys` (the core's polygons in the order and winding written) the custom normal: the face normal
    on the flat polygons (tb.flat_polys); on a roll polygon, at a vertex it shares with a flat polygon of the same
    region, that polygon's normal (the quarter arc is tangent to the face there, and one smooth fan holds one custom
    normal: a different one would make Blender split the fan with a sharp edge); zero (auto-smoothed) elsewhere."""
    flat = set(tb.flat_polys)
    at = {}
    for p in tb.flat_polys:
        for v in polys[p]:
            at[(v, tb.poly_region[p])] = n_poly[p]
    zero = np.zeros(3)
    out = []
    for i, poly in enumerate(polys):
        if i in flat:
            out += [n_poly[i]] * len(poly)
        elif tb.poly_kind[i] == "roll":
            out += [at.get((v, tb.poly_region[i]), zero) for v in poly]
        else:
            out += [zero] * len(poly)
    return np.asarray(out, np.float64).reshape(-1, 3)


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


def core_digest(tb) -> dict:
    """sha256 of the core's vertices (float64 bytes) and polygons: the host computes the same from build_temple."""
    h = hashlib.sha256()
    h.update(np.ascontiguousarray(tb.V).tobytes())
    h.update(json.dumps(tb.polys).encode())
    return {"sha256": h.hexdigest(), "vertices": int(len(tb.V)), "polygons": len(tb.polys)}


def _plan(spec, side, targets, decals, scene) -> tuple:
    """Load, pick targets, fill a null hinge axis from the knuckles, build the core and plan every side (meshes in the
    targets' local frames, decal transfers, hinge report). Returns (tb, plans, receipt, planned meshes)."""
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
    plans: dict = {}
    planned: list = []
    surf = OuterSurface(tb)
    try:
        for s in sides:
            plans[s] = _plan_side(tb, surf, s, tg[s], origin, scene, decals, set(tg.values()), planned)
    except Exception:
        _cleanup_planned(plans, planned)
        raise
    rec["sides"] = {_side_key(s): _side_receipt(plans[s]) for s in sides}
    side_lines = [[f"{w['code']}: {w['message']}" for w in plans[s]["warnings"]] for s in sides]
    rec["lines"] = RP.merge_lines(rec["lines"], *side_lines)
    rec["warnings"] = [w["code"] for w in tb.warnings] + [w["code"] for s in sides for w in plans[s]["warnings"]]
    return tb, plans, rec, planned


def faceted_temple(spec, *, side="both", targets=None, decals="auto", dry_run: bool = False) -> dict:
    """Build faceted temples from a g3d.temple/2 spec and swap them into the scene (module docstring)."""
    t0 = time.perf_counter()
    c0 = time.process_time()
    scene = bpy.context.scene
    digest0 = scene_digest(scene)
    plans, planned = None, []
    try:
        tb, plans, rec, planned = _plan(spec, side, targets, decals, scene)
        if dry_run:
            _cleanup_planned(plans, planned)
            rec["status"] = "planned"
            rec["scene_unchanged"] = scene_digest(scene) == digest0
        else:
            _write(plans, tb)
            rec["status"] = "ok"
        rec["budget"]["total_ms"] = 1000.0 * (time.perf_counter() - t0)
        rec["budget"]["total_cpu_ms"] = 1000.0 * (time.process_time() - c0)
        return TS.plain(rec)
    except TempleError as e:
        _cleanup_planned(plans, planned)
        return _refused(e.code, e.message, e.fields, scene_unchanged=scene_digest(scene) == digest0)
    except Exception as exc:                                  # noqa: BLE001 - report, never raise
        _cleanup_planned(plans, planned)
        return {"status": "failed", "error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc(limit=8),
                "scene_unchanged": scene_digest(scene) == digest0}


def _cleanup_planned(plans, meshes=None):
    for me in list(meshes or []) + [m for p in (plans or {}).values()
                                    for m in [p.get("mesh")] + [d.get("mesh") for d in p.get("decals", [])]]:
        try:
            if me is not None and me.users == 0 and me.name in bpy.data.meshes:
                bpy.data.meshes.remove(me)
        except ReferenceError:                                # already removed
            pass


def _plan_side(tb, surf, side, ob, origin, scene, decals, targets, planned: list) -> dict:
    W, polys, _ = tb.world(side)
    Wn = _world_normals(tb, side)
    Vloc = _local(ob, W, origin)
    M3, _ = _matrix(ob)
    pl = polys if np.linalg.det(M3) > 0 else [p[::-1] for p in polys]
    nloc = _local_normals(ob, Wn)
    old_V, _, old_polys = mesh_arrays(ob, origin)
    old_bvh = _bvh(old_V, old_polys)
    new_bvh = _bvh(W, polys)
    excl = set(targets)
    plan = {"side": side, "object": ob, "origin": origin, "warnings": [], "decals": []}
    corners = corner_normals(tb, pl, nloc)
    me, nsharp = build_mesh(ob.data.name + " | g3d faceted (planned)", Vloc, pl, tb.sharp, tb.flat_polys, nloc,
                            list(ob.data.materials), uv=_uv(tb) if len(ob.data.uv_layers) else None, corners=corners)
    planned.append(me)
    plan["mesh"] = me
    plan["sharp_edges"] = nsharp
    plan["custom_corners"] = int(np.count_nonzero(np.any(corners != 0.0, axis=1)))
    h = tb.spec.hinge
    axis = h["axis_xz_mm"]
    hparts = hinge_parts(side, axis, h["z_mm"], scene, origin, excl)
    plan["hinge_parts"] = [{"name": o.name, "kind": kind} for o, kind in hparts]
    if decals == "auto":                          # hinge parts are never moved, so never a decal
        for dob, V, _ in find_decals(old_bvh, side, scene, origin, excl | {o for o, _ in hparts}):
            tr = transfer_decal(V, old_bvh, surf, side, h)
            dm = dob.data.copy()
            dm.name = dob.data.name + " | g3d decal (planned)"
            planned.append(dm)
            dm.vertices.foreach_set("co", _local(dob, tr["V_new"], origin).astype(np.float32).ravel())
            dm.update()
            rec = {"object": dob, "name": dob.name, "mesh": dm, "vertices": len(V), "outside": tr["outside"],
                   "max_move_mm": float(tr["moves"].max()), "mean_move_mm": float(tr["moves"].mean()),
                   "standoff_mm": [float(tr["standoff"].min()), float(tr["standoff"].max())],
                   "regions": tr["regions"]}
            plan["decals"].append(rec)
            if len(tr["regions"]) > 1:
                plan["warnings"].append({"code": "W_DECAL_SPANS_CREASE", "decal": dob.name, "regions": tr["regions"],
                                         "message": f"decal {dob.name} spans {'|'.join(tr['regions'])}"})
    excl_h = excl | {d["object"] for d in plan["decals"]}
    plan["hinge"] = hinge_report(new_bvh, old_bvh, side, axis, h["z_mm"], scene, origin, excl_h, parts=hparts)
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


def _side_receipt(plan) -> dict:
    ob = plan["object"]
    return {"target": ob.name, "mesh": ob.data.name, "sharp_edges": plan["sharp_edges"],
            "custom_normal_corners": plan["custom_corners"],
            "decals": [{k: d[k] for k in ("name", "vertices", "outside", "max_move_mm", "mean_move_mm", "standoff_mm",
                                          "regions")} for d in plan["decals"]],
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
                                                   "spec_sha256": tb.spec.sha256, "side": _side_key(s),
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


# --------------------------------------------------------------------------------------------- variants
def build_variant_meshes(spec, *, side="both", targets=None, decals="auto") -> dict:
    """Plan like faceted_temple and keep the planned meshes as new, unlinked datablocks (no object changes): the
    temples (`meshes` = {side: mesh name}) and the transferred decals, each in its object's local frame, named
    `<mesh> | g3d variant <sha8>` and tagged `g3d_variant`. `overrides` = {object name: mesh name} is what a render's
    mesh_overrides takes (§3.2). remove_variant_meshes(names) deletes them."""
    t0 = time.perf_counter()
    scene = bpy.context.scene
    plans, planned = None, []
    try:
        tb, plans, rec, planned = _plan(spec, side, targets, decals, scene)
        tag = tb.spec.sha256[:8]
        meshes, overrides = {}, {}
        for s, plan in plans.items():
            items = [(plan["object"], plan["mesh"])] + [(d["object"], d["mesh"]) for d in plan["decals"]]
            for ob, me in items:
                me.name = f"{ob.data.name}{VARIANT_SUFFIX} {tag}"
                me[PROP_VARIANT] = json.dumps({"spec_sha256": tb.spec.sha256, "side": _side_key(s),
                                               "object": ob.name}, sort_keys=True)
                overrides[ob.name] = me.name
            meshes[_side_key(s)] = plan["mesh"].name
        rec.update({"status": "ok", "meshes": meshes, "overrides": overrides})
        rec["budget"]["total_ms"] = 1000.0 * (time.perf_counter() - t0)
        return TS.plain(rec)
    except TempleError as e:
        _cleanup_planned(plans, planned)
        return _refused(e.code, e.message, e.fields)
    except Exception as exc:                                  # noqa: BLE001 - report, never raise
        _cleanup_planned(plans, planned)
        return {"status": "failed", "error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc(limit=8)}


def remove_variant_meshes(names=None) -> dict:
    """Delete variant meshes: the given names (str or list; each must be a g3d_variant mesh no object uses), or
    every unused g3d_variant mesh when names is None."""
    if isinstance(names, str):
        names = [names]
    todo = [me for me in bpy.data.meshes if me.get(PROP_VARIANT) is not None] if names is None else \
        [bpy.data.meshes.get(n) for n in names]
    removed, kept = [], []
    for name, me in zip(names or [m.name for m in todo], todo):
        if me is None or me.get(PROP_VARIANT) is None:
            kept.append({"name": name, "why": "no such g3d variant mesh"})
        elif me.users > 0:
            kept.append({"name": name, "why": f"in use ({me.users} users)"})
        else:
            bpy.data.meshes.remove(me)
            removed.append(name)
    return {"status": "ok" if not kept else "partial", "removed": removed, "kept": kept}


# --------------------------------------------------------------------------------------------- check_relief
CHECK_ZONE_END_D = 50.0          # the measure window behind the hinge (information; /2 has no zone)


def check_relief(objects=None, *, photo_creases: int | None = None, hinge_z: float | None = None) -> dict:
    """Geometry numbers (measure.measure_temple, information) of the evaluated temples (both sides): from their
    g3d_temple extras (chart, facet planes) when present, else the world chart (d = hinge_z - z; hinge_z defaults to
    the temple's front end)."""
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
            spec = json.loads(ext)["canonical_spec"]
            h = spec["hinge"]
            L = TB.to_local(V, h["z_mm"], h["axis_xz_mm"], h["opening_deg"], side=side)
            F = MS.chart_faces(T, side)
            planes = RP.facet_planes_chart(TB.build_temple(spec))
            chart = "g3d_temple extras"
        else:
            hz = float(V[:, 2].max()) if hinge_z is None else float(hinge_z)
            L, F = MS.chart_mesh(V, T, side, hz)
            chart = f"world (hinge z {hz:.2f})"
        m = MS.measure_temple(L, F, zone_end=CHECK_ZONE_END_D, facet_planes=planes, photo_creases=photo_creases)
        m["chart"] = chart
        m["object"] = ob.name
        out["temples"][_side_key(side)] = TS.plain(m)
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
