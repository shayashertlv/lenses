"""Blender layer of WP4 lens seating (WP4_DESIGN.md §4, §6, §8, §9). Runs inside Blender 5.2.

Thin by design: it reads world arrays, runs the numpy core (g3d/seat/), and writes the result back. Everything that
decides WHAT moves is in the core, shared with the host check, so the tool and the check cannot disagree.

    seat_lens(lens, frame_parts, frame_type, capture_mm=None, *, lower_edge=None, translucent=None, uv='keep',
              dry_run=False)
    hide_eyewire(eyewire, under, hidden_x_range_mm, *, lens=None, margin_mm=0.10, ramp_mm=3.0, lift_max_mm=2.5,
                 dry_run=False)
    check_lens_seating(lenses=None, frame_parts=None, frame_type=None, hidden_x_range_mm=None)
    unseat_lens(lens)  /  unhide_eyewire(eyewire, lens=None)

Conventions:
- the core works in ASSET millimetres: world mm (`co @ M^T + t`) minus the scene's bridge-underside marker
  `mdl_bridge_underside` (the exporter's origin), so asset x is what the host check sees in the GLB; deltas go back
  as M3^-1 delta. The lens is read from its base mesh (a lens with geometry modifiers or shape keys is refused);
  frame parts are read evaluated (Oakley's latch housings and INVU's endpieces carry Bevels);
- writes: before the first write `<mesh> | g3d pre-seat` (seat_lens) or `<mesh> | g3d pre-hide` (hide_eyewire) is
  kept as a copy of the mesh datablock with a fake user (the earliest backup of each kind wins); every write is
  also covered by a per-call rollback copy. Only rows with a non-zero delta change, and every raw duplicate of a
  welded vertex gets the same delta;
- the planner verifies what Blender will store (float32 local coordinates round-tripped), and after writing the
  tool re-reads the mesh and runs itself again: a seated lens must move 0 vertices and come back already_seated;
  anything else rolls back (E_POSTCHECK);
- custom normals: with custom normals and any moved vertex (or a rim to sharpen) every corner normal is saved first
  and all are set after the move and the sharp edges (unmoved = saved; moved cap corners rotated with the fitted
  cap normal; moved wall corners = their new face normal; on a rim being sharpened, cap corners = the cap's own
  normal there and wall corners = their face normal, because a sharp edge does not re-derive custom normals);
  unmoved corners off that rim must stay within 0.1 deg (W_NORMALS_REENCODED). Nothing to do = untouched;
- rim sharpening: `sharp_edge` on both outer rim loops, unless the rim is already split (>= 95% of rim edges sharp,
  >= 95% of wall faces flat, or custom normals split >= 60 deg at >= 95% of rim corners) or rounded (rim dihedral
  < 30 deg on most rim edges). g3d_seat `sharpened` (which makes the check demand a GLB rim split p5 >= 60 deg) is
  written only when the stored rim has that split, measured as the check does (`rim_split_now`); else
  W_RIM_SPLIT_LOW;
- an edge profile (V-bevel, groove) cannot be re-planned, so after it the original vertices are verified (stored =
  planned, unmoved bit-identical) and the binding check grades the lens: a failing check makes the call `partial`
  with W_CHECK_FAILS (the crystal groove today, WP4_DESIGN open item B);
- hide_eyewire records the lens it moved in the eyewire's g3d_backups (`held_lens`, and `hidden_by` on the lens)
  and the declared ranges as they were before the first lift; unhide_eyewire restores that lens and the eyewire
  only (a different `lens=` is refused) and puts the ranges back. Each backup records its place in the object's
  order (`seq`): a seat done after the hide is undone by unhide_eyewire, one done before it is kept (and
  unseat_lens then refuses E_ORDER until the eyewire is unhidden). The declared hidden ranges are the union of
  every call's (a sub-range call never un-declares a lifted span);
- seat_lens's status also needs the binding check's verdict on the result (deform.state_check: a lens with no
  required station is refused E_NOT_HELD; UNMEASURED > 1% or TRANSITION > 3% make it partial).
Geometry problems never raise: every function returns a report dict with "status" and, when refused, "code".
"""
from __future__ import annotations

import json
import time
import traceback

import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from . import export_glb as EX
from .seat import check as CK
from .seat import deform, geom, rules, stations
from .seat.intervals import DEDUPE_MM, line_intervals

BACKUP_SEAT = " | g3d pre-seat"
BACKUP_HIDE = " | g3d pre-hide"
PROP_SEAT = "g3d_seat"
PROP_RANGES = "g3d_hidden_x_ranges_mm"
PROP_BACKUPS = "g3d_backups"
HELD_LENS = "held_lens"            # key in an eyewire's g3d_backups: the lens hide_eyewire moved with it
HIDDEN_BY = "hidden_by"            # key in a lens's g3d_backups: the eyewire whose hide_eyewire moved it
RANGES_BEFORE = "hide_ranges_before"   # g3d_hidden_x_ranges_mm as it was before the first lift (restored by unhide)
SEQ = "seq"                        # {kind: n}: the order the backups were taken in (seat before / after hide)
NORMALS_MAX_DEG = 0.1              # §4 step 10
SPLIT_SHARE = 0.95                 # §4 step 9
SPLIT_DEG = 60.0
ROUNDED_DEG = 30.0
STORE_TOL_MM = 1e-9                # stored vs planned positions (both float32-derived; float64 a + (b - a) != b)


# ------------------------------------------------------------------------------------------------- BVH backend
class BVHBackend:
    """Line intervals by mathutils BVHTree (float32): ONE tree over every part, a triangle -> part table, and per
    line one cast loop through the parts' common box (padded 1e-3 mm), stepping DEDUPE_MM past each hit (a hit on a
    shared edge is reported once). Coordinates are centred on that box before the float32 conversion; hit
    parameters are computed in float64 from the hit points.

    Two things make a part's count odd here where an exact test would not: BVHTree's ray-triangle test is not
    watertight (a ray through a shared edge can miss both triangles: Oakley's latch housing, 1 line of 6216), and
    the step past a hit skips another part's crossing within DEDUPE_MM (touching parts). Every (line, part) with an
    odd count is therefore recomputed exactly, float64 Moller-Trumbore over that part (the NumpyBackend's test),
    before the core's parity re-cast sees it. (BVHTree's `epsilon` is no fix: 1e-6 turned 379 Oakley and 1209 INVU
    lines odd.)"""

    PAD_MM = 1e-3

    def __init__(self, parts):
        self.n_parts = len(parts)
        Vs, Ts, lut = [], [], []
        off = 0
        boxes = [np.asarray(V, np.float64).reshape(-1, 3)[np.unique(np.asarray(T, np.int64))]
                 for V, T in parts if len(T)]
        allv = np.concatenate(boxes) if boxes else np.zeros((1, 3))
        self.centre = 0.5 * (allv.min(0) + allv.max(0))
        self.lo = allv.min(0) - self.centre - self.PAD_MM
        self.hi = allv.max(0) - self.centre + self.PAD_MM
        self._parts = []
        for i, (V, T) in enumerate(parts):
            V = np.asarray(V, np.float64).reshape(-1, 3) - self.centre
            T = np.asarray(T, np.int64).reshape(-1, 3)
            self._parts.append((V, T))
            if len(T) == 0:
                continue
            Vs.append(V)
            Ts.append(T + off)
            lut.append(np.full(len(T), i, np.int64))
            off += len(V)
        self.tree = None
        if Ts:
            self.tree = BVHTree.FromPolygons(np.concatenate(Vs).tolist(), np.concatenate(Ts).tolist(),
                                             all_triangles=True, epsilon=0.0)
            self.tri_part = np.concatenate(lut)
        self._exact = {}
        self.exact_recomputes = 0

    def _exact_hits(self, p: int, o: np.ndarray, d: np.ndarray) -> list:
        """All crossings of the line o + t d with part p, float64 Moller-Trumbore (as NumpyBackend._mt)."""
        if p not in self._exact:
            V, T = self._parts[p]
            a = V[T[:, 0]]
            e1, e2 = V[T[:, 1]] - a, V[T[:, 2]] - a
            self._exact[p] = (a, e1, e2, np.linalg.norm(e1, axis=1) * np.linalg.norm(e2, axis=1))
        a, e1, e2, scale = self._exact[p]
        pv = np.cross(d, e2)
        det = (e1 * pv).sum(1)
        ok = np.abs(det) > 1e-12 * scale
        inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
        tv = o - a
        u = (tv * pv).sum(1) * inv
        q = np.cross(tv, e1)
        v = (q @ d) * inv
        t = (e2 * q).sum(1) * inv
        eps = 1e-9
        hit = ok & (u >= -eps) & (v >= -eps) & (u + v <= 1.0 + eps)
        tt = np.sort(t[hit])
        if len(tt) > 1:
            tt = tt[np.r_[True, np.diff(tt) > DEDUPE_MM]]
        return tt.tolist()

    def hits(self, origins, dirs):
        O = np.asarray(origins, np.float64).reshape(-1, 3) - self.centre
        D = np.asarray(dirs, np.float64).reshape(-1, 3)
        if self.tree is None or len(O) == 0:
            z = np.zeros(0, np.int64)
            return z, z, np.zeros(0)
        with np.errstate(divide="ignore", invalid="ignore"):
            inv = np.where(D != 0, 1.0 / np.where(D != 0, D, 1.0), np.inf)
            ta = (self.lo - O) * inv
            tb = (self.hi - O) * inv
        zero = D == 0
        inside = (O >= self.lo) & (O <= self.hi)
        tmin = np.where(zero, np.where(inside, -np.inf, np.inf), np.minimum(ta, tb)).max(1)
        tmax = np.where(zero, np.where(inside, np.inf, -np.inf), np.maximum(ta, tb)).min(1)
        tree, lut = self.tree, self.tri_part.tolist()
        L, P, Tt = [], [], []
        step = DEDUPE_MM
        Ol, Dl, t0s, t1s = O.tolist(), D.tolist(), tmin.tolist(), tmax.tolist()
        for i in np.flatnonzero(tmin <= tmax).tolist():
            ox, oy, oz = Ol[i]
            dx, dy, dz = Dl[i]
            dv = Vector((dx, dy, dz))
            t_cur, t_end = t0s[i] - self.PAD_MM, t1s[i] + self.PAD_MM
            per: dict = {}
            while t_cur < t_end:
                h = tree.ray_cast(Vector((ox + t_cur * dx, oy + t_cur * dy, oz + t_cur * dz)), dv, t_end - t_cur)
                if h[0] is None:
                    break
                hx, hy, hz = h[0]
                t_hit = (hx - ox) * dx + (hy - oy) * dy + (hz - oz) * dz
                per.setdefault(lut[h[2]], []).append(t_hit)
                t_cur = (t_hit if t_hit > t_cur else t_cur) + step
            for p, found in per.items():
                if len(found) % 2:
                    found = self._exact_hits(p, O[i], D[i])
                    self.exact_recomputes += 1
                L += [i] * len(found)
                P += [p] * len(found)
                Tt += found
        if not L:
            z = np.zeros(0, np.int64)
            return z, z, np.zeros(0)
        return np.asarray(L, np.int64), np.asarray(P, np.int64), np.asarray(Tt, np.float64)


def bvh_factory(parts):
    return BVHBackend(parts)


# ------------------------------------------------------------------------------------------------------ arrays
def asset_origin(scene=None, warnings: list | None = None) -> np.ndarray:
    scene = scene or bpy.context.scene
    v = scene.get(EX.ORIGIN_KEY)
    if v is None:
        if warnings is not None:
            warnings.append(f"W_NO_ORIGIN: scene has no {EX.ORIGIN_KEY}; asset x = world x (sectors and hidden "
                            f"ranges assume the bridge at x = 0)")
        return np.zeros(3)
    return np.asarray([float(x) for x in v], np.float64)


class MeshArrays:
    """World/asset arrays of one object's mesh (base mesh, or evaluated when `depsgraph` is given)."""

    def __init__(self, ob, origin, depsgraph=None, corners: bool = False):
        self.ob = ob
        M = np.array(ob.matrix_world, np.float64)
        self.M3, self.t = M[:3, :3], M[:3, 3]
        self.origin = np.asarray(origin, np.float64)
        ev = None
        if depsgraph is not None:
            ev = ob.evaluated_get(depsgraph)
            me = ev.to_mesh(preserve_all_data_layers=False, depsgraph=depsgraph)
        else:
            me = ob.data
        try:
            nv, nl = len(me.vertices), len(me.loops)
            co = np.zeros(nv * 3, np.float32)
            me.vertices.foreach_get("co", co)
            self.co = co.reshape(-1, 3).astype(np.float64)
            self.V = self.co @ self.M3.T + self.t - self.origin
            me.calc_loop_triangles()
            m = len(me.loop_triangles)
            tv = np.zeros(m * 3, np.int32)
            me.loop_triangles.foreach_get("vertices", tv)
            self.T = tv.reshape(-1, 3).astype(np.int64)
            tl = np.zeros(m * 3, np.int32)
            me.loop_triangles.foreach_get("loops", tl)
            self.TL = tl.reshape(-1, 3).astype(np.int64)
            tp = np.zeros(m, np.int32)
            me.loop_triangles.foreach_get("polygon_index", tp)
            self.tri_poly = tp.astype(np.int64)
            ev_ = np.zeros(len(me.edges) * 2, np.int32)
            me.edges.foreach_get("vertices", ev_)
            self.E = ev_.reshape(-1, 2).astype(np.int64)
            lv = np.zeros(nl, np.int32)
            me.loops.foreach_get("vertex_index", lv)
            self.loop_v = lv.astype(np.int64)
            self.N = None
            if corners:
                n = np.zeros(nl * 3, np.float32)
                me.corner_normals.foreach_get("vector", n)
                nw = n.reshape(-1, 3).astype(np.float64) @ np.linalg.inv(self.M3)
                self.N = nw / np.maximum(np.linalg.norm(nw, axis=1, keepdims=True), 1e-30)
        finally:
            if ev is not None:
                ev.to_mesh_clear()

    def corner_node(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(V, T, N) per corner: the GLB's vertex model (one vertex per corner; the weld merges equal positions)."""
        return self.V[self.loop_v], self.TL, self.N

    def to_local(self, D_asset: np.ndarray) -> np.ndarray:
        return D_asset @ np.linalg.inv(self.M3).T

    def roundtrip_rows(self, rows, Vb, base):
        """What Blender stores and reads back for planned asset positions Vb of the raw rows (float32 local)."""
        d = Vb - base
        co = (self.co[rows] + self.to_local(d)).astype(np.float32).astype(np.float64)
        out = co @ self.M3.T + self.t - self.origin
        return np.where(np.any(d != 0, axis=1)[:, None], out, base)

    def roundtrip_all(self, V_new):
        rows = np.arange(len(self.V))
        return self.roundtrip_rows(rows, V_new, self.V)


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


def material_translucent(mat) -> bool:
    """§4: transmission > 0.05, alpha < 0.98 or a volume (what the glTF exporter writes as transmission, alpha or
    KHR_materials_volume)."""
    if mat is None or mat.node_tree is None:
        return False
    for n in mat.node_tree.nodes:
        if n.bl_idname == "ShaderNodeBsdfPrincipled":
            tw = n.inputs.get("Transmission Weight")
            al = n.inputs.get("Alpha")
            if tw is not None and (tw.is_linked or float(tw.default_value) > 0.05):
                return True
            if al is not None and (al.is_linked or float(al.default_value) < 0.98):
                return True
        if n.bl_idname == "ShaderNodeBsdfGlass":
            return True
        if n.bl_idname == "ShaderNodeOutputMaterial" and n.inputs["Volume"].is_linked:
            return True
    return False


def object_translucent(ob) -> bool:
    return any(material_translucent(s.material) for s in ob.material_slots if s.material is not None)


def _extras(ob) -> dict:
    out = {}
    for k in (PROP_SEAT, PROP_RANGES):
        if ob.get(k) is not None:
            out[k] = ob.get(k)
    return out


def _render_objects(scene):
    return [o for o in EX.render_enabled_objects(scene) if o.type in EX.SURFACE_TYPES]


def _node(ob, origin, depsgraph, idx: int, corners: bool, base: bool = False) -> dict:
    A = MeshArrays(ob, origin, None if base else depsgraph, corners=corners)
    if corners:
        V, T, N = A.corner_node()
    else:
        V, T, N = A.V, A.T, np.full_like(A.V, np.nan)
    role = _role(ob)
    return {"node": idx, "name": ob.name, "V": V, "T": T, "N": N, "roles": [role] if role else [],
            "lens": role == "lens", "translucent": object_translucent(ob), "extras": _extras(ob), "_arrays": A,
            "_object": ob}


# ------------------------------------------------------------------------------------------------- the check
def check_lens_seating(lenses=None, frame_parts=None, frame_type=None, hidden_x_range_mm=None, *, lower_edge=None,
                       stations_table: bool = False, scene=None) -> dict:
    """§8 geometric check in Blender (BVH, evaluated meshes, asset mm): the same nodes the host reads from the
    exported GLB (one vertex per corner, corner normals for the rim split) through the same g3d/seat/check.py.
    lenses / frame_parts: objects or names (default: render-visible partRole lens / frame objects)."""
    t0 = time.time()
    try:
        scene = scene or bpy.context.scene
        warnings: list = []
        origin = asset_origin(scene, warnings)
        dg = bpy.context.evaluated_depsgraph_get()
        objs = sorted(_render_objects(scene), key=lambda o: o.name)
        lens_objs = ([_obj(x) for x in _as_list(lenses)] if lenses is not None
                     else [o for o in objs if _role(o) == "lens"])
        frame_objs = ([_obj(x) for x in _as_list(frame_parts)] if frame_parts is not None
                      else [o for o in objs if _role(o) == "frame"])
        nodes = []
        for i, ob in enumerate(sorted(set(lens_objs) | set(frame_objs), key=lambda o: o.name)):
            nd = _node(ob, origin, dg, i, corners=True)
            nd["lens"] = ob in lens_objs
            if ob in frame_objs and "frame" not in nd["roles"]:
                nd["roles"] = nd["roles"] + ["frame"]
            nodes.append(nd)
        rep = CK.check_nodes([_plain_node(n) for n in nodes], [], warnings, bvh_factory, frame_type, lower_edge,
                             hidden_x_range_mm, stations_table, "bvh")
        _stale_cutters(rep)
        rep["seconds"] = round(time.time() - t0, 3)
        rep["origin_mm"] = origin.tolist()
        return rep
    except Exception as exc:                                       # never raise into Astra's session
        err = f"{type(exc).__name__}: {exc}"
        return {"ok": False, "error": err, "reasons": [err], "lenses": [],
                "traceback": traceback.format_exc(limit=8)}


def _stale_cutters(rep: dict) -> None:
    """§8 'stale-cutter flag': a lens with a groove cutter (`g3d_groove_cutter | <lens>`) whose stored lens digest
    differs from the lens now was edited after its groove was cut. Reported per lens (`stale_cutter`) with a warning;
    the lens is graded on what the evaluated frame holds (crystal overlap, the groove reading), as the host grades
    the GLB, which carries no cutter."""
    for L in rep.get("lenses", []):
        ob = bpy.data.objects.get(L.get("node") or "")
        cut = bpy.data.objects.get(CUTTER_PREFIX + ob.name) if ob is not None and ob.type == "MESH" else None
        if cut is None:
            continue
        stale = cut.get("g3d_lens_digest") != _mesh_digest(ob)
        L["stale_cutter"] = bool(stale)
        if stale:
            rep.setdefault("warnings", []).append(
                f"{ob.name}: stale groove cutter {cut.name!r} (cut for another version of the lens: its stored digest "
                f"differs); call unseat_lens and seat_lens again")


def _as_list(x) -> list:
    """One object or name, or a list / tuple of them (a str is one name, never its characters)."""
    if isinstance(x, (str, bpy.types.Object)):
        return [x]
    return list(x)


def _plain_node(n: dict) -> dict:
    return {k: v for k, v in n.items() if not k.startswith("_")}


# ----------------------------------------------------------------------------------------------- shared tools
def _refuse(rep: dict, code: str, message: str) -> dict:
    rep.update(status="refused", code=code, message=message)
    return rep


def _lens_refusal(ob) -> tuple[str, str] | None:
    if ob.type != "MESH":
        return "E_ARGS", f"{ob.name} is a {ob.type}, not a mesh"
    if bpy.context.mode != "OBJECT":
        return "E_MODE", "leave Edit/Sculpt mode first (no mode was changed)"
    if ob.modifiers:
        return ("E_LENS_MODIFIERS", f"{ob.name} has modifiers {[m.name for m in ob.modifiers]}: the tool writes the "
                                    f"base mesh, so apply or remove them first")
    if ob.data.shape_keys is not None:
        return "E_SHAPE_KEYS", f"{ob.name} has shape keys: apply or remove them first"
    users = ob.data.users - (1 if ob.data.use_fake_user else 0)
    if users > 1:
        return "E_SHARED_MESH", f"{ob.name}'s mesh {ob.data.name!r} is used by {users} objects: make it single-user"
    return None


def _backups(ob) -> dict:
    v = ob.get(PROP_BACKUPS)
    try:
        return dict(json.loads(v)) if isinstance(v, str) else {}
    except ValueError:
        return {}


def _set_backups(ob, bk: dict) -> None:
    """Write g3d_backups (dropped when nothing is left in it)."""
    bk = dict(bk)
    if not bk.get(SEQ):
        bk.pop(SEQ, None)
    if bk:
        ob[PROP_BACKUPS] = json.dumps(bk, sort_keys=True)
    elif PROP_BACKUPS in ob:
        del ob[PROP_BACKUPS]


def _has_backup(ob, kind: str) -> bool:
    name = _backups(ob).get(kind)
    return bool(name) and bpy.data.meshes.get(name) is not None


def _seat_after_hide(bk: dict) -> bool:
    """The lens's seat backup was taken after its hide backup (the P2 order: hide_eyewire, then seat_lens). Backups
    recorded before the order was kept count as that order."""
    seq = bk.get(SEQ) or {}
    if "seat" not in seq or "hide" not in seq:
        return True
    return int(seq["seat"]) > int(seq["hide"])


def _keep_backup(ob, kind: str, extra: dict | None = None) -> str:
    """Keep the mesh as `<mesh> | g3d pre-<kind>` (fake user) unless one is kept already (the earliest wins). A new
    backup records its place in the order of this object's backups (`seq`) and the `extra` keys."""
    bk = _backups(ob)
    name = bk.get(kind)
    if name and bpy.data.meshes.get(name) is not None:
        return name
    copy = ob.data.copy()
    copy.name = f"{ob.data.name}{BACKUP_SEAT if kind == 'seat' else BACKUP_HIDE}"
    copy.use_fake_user = True
    bk[kind] = copy.name
    seq = dict(bk.get(SEQ) or {})
    seq[kind] = max([int(v) for v in seq.values()] + [0]) + 1
    bk[SEQ] = seq
    bk.update(extra or {})
    _set_backups(ob, bk)
    return copy.name


def _undo_backup(ob, kind: str, undo) -> None:
    """Undo what _keep_backup did in a call that rolled back: drop a backup it created, restore the property."""
    prop, created = undo
    if created:
        name = _backups(ob).get(kind)
        mesh = bpy.data.meshes.get(name) if name else None
        if mesh is not None:
            bpy.data.meshes.remove(mesh)
    if prop is None:
        if PROP_BACKUPS in ob:
            del ob[PROP_BACKUPS]
    else:
        ob[PROP_BACKUPS] = prop


def _swap_mesh(ob, new_mesh, discard_old: bool = True) -> None:
    """Give `ob` the datablock `new_mesh` under the current mesh's name; the old datablock is removed."""
    old = ob.data
    name = old.name
    ob.data = new_mesh
    new_mesh.use_fake_user = False
    old.name = name + " (g3d discarded)"
    new_mesh.name = name
    if discard_old and old.users == 0:
        bpy.data.meshes.remove(old)


def _write_deltas(ob, A: MeshArrays, delta_asset: np.ndarray) -> int:
    rows = np.flatnonzero(np.any(delta_asset != 0, axis=1))
    if not len(rows):
        return 0
    co = A.co.copy()
    co[rows] = co[rows] + A.to_local(delta_asset[rows])
    ob.data.vertices.foreach_set("co", co.astype(np.float32).ravel())
    ob.data.update()
    return int(len(rows))


def _corner_normals(me) -> np.ndarray:
    n = np.zeros(len(me.loops) * 3, np.float32)
    me.corner_normals.foreach_get("vector", n)
    return n.reshape(-1, 3).astype(np.float64)


def _poly_normals(me) -> np.ndarray:
    n = np.zeros(len(me.polygons) * 3, np.float32)
    me.polygons.foreach_get("normal", n)
    return n.reshape(-1, 3).astype(np.float64)


def _poly_classes(A: MeshArrays, rec) -> np.ndarray:
    """Per polygon: 0 front cap, 1 back cap, 2 wall (from the bodies' triangle classes), -1 none."""
    tri_cls = np.full(len(A.T), -1, np.int64)
    for b in rec.bodies:
        tri_cls[b.source_faces] = b.cls
    npoly = int(A.tri_poly.max()) + 1 if len(A.tri_poly) else 0
    out = np.full(npoly, -1, np.int64)
    out[A.tri_poly] = tri_cls
    return out


def _loop_structure(me) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(polygon of each corner, previous corner, next corner) in the polygon's loop order."""
    npoly = len(me.polygons)
    ls = np.zeros(npoly, np.int32)
    lt = np.zeros(npoly, np.int32)
    me.polygons.foreach_get("loop_start", ls)
    me.polygons.foreach_get("loop_total", lt)
    ls, lt = ls.astype(np.int64), lt.astype(np.int64)
    loop_poly = np.repeat(np.arange(npoly), lt)
    pos = np.arange(len(loop_poly)) - ls[loop_poly]
    prev = ls[loop_poly] + (pos - 1) % lt[loop_poly]
    nxt = ls[loop_poly] + (pos + 1) % lt[loop_poly]
    return loop_poly, prev, nxt


def _rim_vertices(A: MeshArrays, rec) -> np.ndarray:
    """(n_raw,) bool: raw vertices on the outer front or back rim loop of a body (every raw duplicate)."""
    _, vid = geom.weld_exact(A.V)
    g = np.zeros(int(vid.max()) + 1 if len(vid) else 0, bool)
    for b in rec.bodies:
        g[b.source_verts[np.r_[b.fl, b.bl]]] = True
    return g[vid] if len(vid) else np.zeros(0, bool)


def _split_cap_normals(me, A: MeshArrays, poly_cls: np.ndarray, corners: np.ndarray) -> np.ndarray:
    """(len(corners), 3) local normals for the given cap corners: per rim vertex and cap, the corner-angle weighted
    mean of the normals of that cap's faces around it (what a sharp rim edge gives a smooth cap without custom
    normals), from the mesh as stored now."""
    co = np.zeros(len(me.vertices) * 3, np.float32)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3).astype(np.float64)
    loop_poly, prev, nxt = _loop_structure(me)
    lv = A.loop_v
    pn = _poly_normals(me)
    a = co[lv[prev]] - co[lv]
    b = co[lv[nxt]] - co[lv]
    ang = np.arccos(np.clip((a * b).sum(1) / np.maximum(np.linalg.norm(a, axis=1) * np.linalg.norm(b, axis=1), 1e-30),
                            -1.0, 1.0))
    _, vid = geom.weld_exact(A.V)
    cls_c = poly_cls[loop_poly]
    key = 2 * vid[lv] + np.where(cls_c == 1, 1, 0)
    capc = np.isin(cls_c, (0, 1))
    acc = np.zeros((2 * (int(vid.max()) + 1), 3))
    np.add.at(acc, key[capc], pn[loop_poly[capc]] * ang[capc, None])
    out = acc[key[corners]]
    return out / np.maximum(np.linalg.norm(out, axis=1, keepdims=True), 1e-30)


def _restore_normals(ob, A: MeshArrays, N0_local: np.ndarray, moved_v: np.ndarray, rot: dict,
                     poly_cls: np.ndarray, split_v: np.ndarray | None = None) -> dict:
    """§4 step 10: set every corner normal (unmoved = saved; moved cap corners rotated; moved wall corners = the new
    face normal). Rotations are asset-space; local normals map through M3.

    split_v (n_raw bool, §4 step 9 on a lens with custom normals): the rim vertices being sharpened. A sharp edge
    does not re-derive custom normals (Blender keeps the stored ones), so their corners are set here: cap corners =
    the cap's own normal at that vertex (`_split_cap_normals`), wall corners = their face normal, so the rim splits by
    the cap/wall dihedral like a lens without custom normals (review fix round 2: the custom-normals fixture was
    marked sharpened with a 38.5 deg GLB rim split, and the check failed it). Those corners are a deliberate change,
    outside the 0.1 deg rule for unmoved corners."""
    me = ob.data
    loop_poly, _, _ = _loop_structure(me)
    pc = np.full(len(me.polygons), -1, np.int64)               # polygons without loop triangles: class -1
    k = min(len(pc), len(poly_cls))
    pc[:k] = poly_cls[:k]
    poly_cls = pc
    N1 = N0_local.copy()
    pn = _poly_normals(me)
    M3, M3i = A.M3, np.linalg.inv(A.M3)
    moved_c = moved_v[A.loop_v]
    for c in np.flatnonzero(moved_c).tolist():
        v = int(A.loop_v[c])
        cls = poly_cls[loop_poly[c]]
        if cls in (0, 1) and v in rot:
            nw = N0_local[c] @ M3i                      # local -> asset (inverse transpose, row form)
            nw = rot[v] @ (nw / np.linalg.norm(nw))
            nl = nw @ M3                                # asset -> local: n_l = M3^T n_w (row form n_w @ M3)
            N1[c] = nl / np.linalg.norm(nl)
        elif cls == 2 or v not in rot:
            N1[c] = pn[loop_poly[c]]
    split_c = np.zeros(len(N1), bool)
    if split_v is not None and split_v.any():
        split_c = split_v[A.loop_v]
        cls_c = poly_cls[loop_poly]
        cap = np.flatnonzero(split_c & np.isin(cls_c, (0, 1)))
        wall = np.flatnonzero(split_c & (cls_c == 2))
        N1[cap] = _split_cap_normals(me, A, poly_cls, cap)
        N1[wall] = pn[loop_poly[wall]]
        split_c[:] = False
        split_c[cap] = True
        split_c[wall] = True
    me.normals_split_custom_set([tuple(x) for x in N1])
    me.update()
    N2 = _corner_normals(me)
    un = ~moved_c & ~split_c
    ang = np.degrees(np.arccos(np.clip((N2[un] * N0_local[un]).sum(1)
                                       / np.maximum(np.linalg.norm(N2[un], axis=1)
                                                    * np.linalg.norm(N0_local[un], axis=1), 1e-30), -1, 1)))
    return {"unmoved_corners": int(un.sum()), "unmoved_max_change_deg": round(float(ang.max()), 5) if len(ang) else 0.0,
            "moved_corners": int(moved_c.sum()), "split_rim_corners": int(split_c.sum())}


def _rim_edges(A: MeshArrays, rec) -> np.ndarray:
    """Raw edge indices on the outer front and back rim loops of every body."""
    _, vid = geom.weld_exact(A.V)
    pairs = set()
    for b in rec.bodies:
        gv = b.source_verts
        for lp in (b.fl, b.bl):
            g = gv[lp]
            for a, c in zip(g.tolist(), np.roll(g, -1).tolist()):
                pairs.add((min(a, c), max(a, c)))
    we = np.sort(vid[A.E], axis=1)
    return np.array([i for i, (a, c) in enumerate(we.tolist()) if (a, c) in pairs], np.int64)


def _attr_values(me, name: str, domain_len: int) -> np.ndarray:
    att = me.attributes.get(name)
    out = np.zeros(domain_len, bool)
    if att is not None and att.data_type == "BOOLEAN":
        att.data.foreach_get("value", out)
    return out


def sharpen_decision(ob, A: MeshArrays, rec) -> dict:
    """§4 step 9: which rim edges to mark sharp, or why not."""
    me = ob.data
    rim = _rim_edges(A, rec)
    out = {"rim_edges": int(len(rim)), "sharpen": False, "reason": None, "edges": rim}
    if not len(rim):
        out["reason"] = "no rim edges"
        return out
    sharp = _attr_values(me, "sharp_edge", len(me.edges))
    poly_cls = _poly_classes(A, rec)
    flat = _attr_values(me, "sharp_face", len(me.polygons))
    wall = poly_cls == 2
    share_sharp = float(sharp[rim].mean())
    share_flat = float(flat[wall].mean()) if wall.any() else 0.0
    out.update(share_sharp=round(share_sharp, 4), share_flat_wall=round(share_flat, 4))
    if share_sharp >= SPLIT_SHARE:
        out["reason"] = f"already split: {share_sharp:.0%} of rim edges sharp"
        return out
    if share_flat >= SPLIT_SHARE:
        out["reason"] = f"already split: {share_flat:.0%} of wall faces flat"
        return out
    if me.has_custom_normals:
        split = _custom_split_share(me, A, rec, poly_cls)
        out["custom_split_share"] = round(split, 4)
        if split >= SPLIT_SHARE:
            out["reason"] = f"already split: custom normals split >= 60 deg at {split:.0%} of rim corners"
            return out
    # rounded: dihedral between the cap face and the wall face across each rim edge
    pn = _poly_normals(me)
    ek = {tuple(sorted(e)): i for i, e in enumerate(A.E.tolist())}
    edge_polys = {}
    for p, poly in enumerate(me.polygons):
        vs = list(poly.vertices)
        for a, c in zip(vs, vs[1:] + vs[:1]):
            i = ek.get((min(a, c), max(a, c)))
            if i is not None:
                edge_polys.setdefault(i, []).append(p)
    dih = []
    for i in rim.tolist():
        ps = edge_polys.get(i, [])
        caps = [p for p in ps if poly_cls[p] in (0, 1)]
        walls = [p for p in ps if poly_cls[p] == 2]
        if caps and walls:
            dih.append(np.degrees(np.arccos(np.clip(np.dot(pn[caps[0]], pn[walls[0]]), -1, 1))))
    if dih and np.mean(np.asarray(dih) < ROUNDED_DEG) > 0.5:
        out["reason"] = f"rounded rim: dihedral < {ROUNDED_DEG:g} deg on {np.mean(np.asarray(dih) < ROUNDED_DEG):.0%}"
        return out
    out["sharpen"] = True
    out["new_edges"] = int((~sharp[rim]).sum())
    return out


def rim_split_now(ob, origin) -> dict:
    """The check's rim split from the mesh as stored: check_lens_seating's node for the lens (one vertex per corner,
    asset-space corner normals) through the same CK.rim_split; `ok` when every lens body's front p5 reaches §8's 60
    deg (what a g3d_seat 'sharpened' makes the check demand)."""
    A = MeshArrays(ob, origin, corners=True)
    V, T, N = A.corner_node()
    rec = stations.recognise(V, T, ob.name)
    per = []
    for b in rec.bodies:
        rs = CK.rim_split(b, {"V": V, "T": T, "N": N}) or {}
        per.append({"body": b.name, "front": rs.get("front"), "back": rs.get("back")})
    p5 = [x["front"]["p5"] for x in per if x["front"]]
    ok = bool(per) and len(p5) == len(per) and min(p5) >= rules.RIM_SPLIT_MIN_DEG
    return {"bodies": per, "front_p5_min": min(p5) if p5 else None, "ok": bool(ok)}


def _custom_split_share(me, A, rec, poly_cls) -> float:
    N = _corner_normals(me)
    loop_poly = np.repeat(np.arange(len(me.polygons)), [p.loop_total for p in me.polygons])
    _, vid = geom.weld_exact(A.V)
    rimg = set()
    for b in rec.bodies:
        rimg.update(b.source_verts[np.r_[b.fl, b.bl]].tolist())
    acc = {}
    for c, v in enumerate(A.loop_v.tolist()):
        g = int(vid[v])
        if g not in rimg:
            continue
        cls = poly_cls[loop_poly[c]]
        k = "w" if cls == 2 else ("c" if cls in (0, 1) else None)
        if k:
            acc.setdefault(g, {"c": np.zeros(3), "w": np.zeros(3)})[k] += N[c]
    ok = [d for d in acc.values() if np.linalg.norm(d["c"]) > 0 and np.linalg.norm(d["w"]) > 0]
    if not ok:
        return 0.0
    ang = [np.degrees(np.arccos(np.clip(np.dot(d["c"] / np.linalg.norm(d["c"]), d["w"] / np.linalg.norm(d["w"])),
                                        -1, 1))) for d in ok]
    return float(np.mean(np.asarray(ang) >= SPLIT_DEG))


def apply_sharp(ob, edges: np.ndarray) -> int:
    me = ob.data
    att = me.attributes.get("sharp_edge")
    if att is None:
        att = me.attributes.new("sharp_edge", "BOOLEAN", "EDGE")
    cur = np.zeros(len(me.edges), bool)
    att.data.foreach_get("value", cur)
    new = cur.copy()
    new[edges] = True
    att.data.foreach_set("value", new)
    me.update()
    return int((new & ~cur).sum())


def _ranges_of(objs) -> list | None:
    out = []
    for ob in objs:
        v = ob.get(PROP_RANGES)
        if isinstance(v, str):
            try:
                v = json.loads(v)
            except ValueError:
                v = None
        if v:
            out += [(float(a), float(b)) for a, b in v]
    return sorted(set(out)) or None


merge_ranges = rules.merge_ranges


def _mesh_digest(ob) -> str:
    me = ob.data
    co = np.zeros(len(me.vertices) * 3, np.float32)
    me.vertices.foreach_get("co", co)
    lv = np.zeros(len(me.loops), np.int32)
    me.loops.foreach_get("vertex_index", lv)
    return deform.lens_digest(co.astype(np.float64), lv.astype(np.int64))


def _tool_context(ob, frame_parts, scene, dg, origin, warnings):
    """Nodes for a tool call on lens `ob`: the lens (base mesh), its frame parts (given, else the render-visible
    partRole frame objects; evaluated), the other render-visible lenses; then the shared component logic."""
    objs = sorted(_render_objects(scene), key=lambda o: o.name)
    if frame_parts is not None:
        fobjs = [_obj(x) for x in _as_list(frame_parts)]
    else:
        fobjs = [o for o in objs if _role(o) == "frame" and o != ob]
    others = [o for o in objs if o != ob and o not in fobjs]
    nodes = [_node(ob, origin, dg, 0, corners=False, base=True)]
    nodes[0]["lens"] = True
    for i, o in enumerate(sorted(fobjs, key=lambda x: x.name)):
        nd = _node(o, origin, dg, i + 1, corners=False)
        nd["lens"] = False
        nd["roles"] = ["frame"]
        nodes.append(nd)
    other_lenses = [o for o in others if _role(o) == "lens"]
    for j, o in enumerate(other_lenses):
        nd = _node(o, origin, dg, len(nodes) + j, corners=False)
        nodes.append(nd)
    col = CK.collect([_plain_node(n) for n in nodes], [], warnings, bvh_factory)
    return nodes, col, fobjs, [o for o in others if _role(o) != "lens"]


def _obstacles(col, lens_name, extra_objs, origin, dg, boxes) -> list:
    """(name, V, T) closed components the grown edge must not enter: the other lenses and the render-visible
    non-frame objects (temples) near the lens."""
    out = []
    for b, n in zip(col["bodies"], col["body_node"]):
        if n["name"] != lens_name:
            out.append((b.name, b.V, b.T))
    for o in extra_objs:
        A = MeshArrays(o, origin, dg)
        for c in stations.split_components(A.V, A.T, o.name):
            if c.closed and any(CK.boxes_touch(c.box, lo, hi) for lo, hi in boxes):
                out.append((c.name, c.V, c.T))
    return out


# ------------------------------------------------------------------------------------------------- seat_lens
def seat_lens(lens, frame_parts=None, frame_type=None, capture_mm=None, *, lower_edge=None, translucent=None,
              uv: str = "keep", dry_run: bool = False) -> dict:
    """§4. Returns a report: status seated | already_seated | partial | refused (with code and message)."""
    t0 = time.time()
    rep = {"tool": "seat_lens", "lens": str(getattr(lens, "name", lens)), "status": "refused", "code": None,
           "message": "", "warnings": [], "dry_run": bool(dry_run)}
    rollback = None
    ob = None
    profile_started = False
    seat_before = None
    try:
        ob = _obj(lens)
        rep["lens"] = ob.name
        bad = _lens_refusal(ob)
        if bad:
            return _refuse(rep, *bad)
        if frame_type is None:
            return _refuse(rep, "E_ARGS", "frame_type is required: acetate, metal, browline, semi_rimless or "
                                          "rimless")
        if uv != "keep":
            return _refuse(rep, "E_ARGS", f"uv={uv!r}: only 'keep' is implemented (UV coordinates stay; "
                                          f"'extrapolate' for image-textured lenses is not built yet)")
        try:
            ft = rules.frame_type(frame_type, lower_edge, capture_mm)
        except ValueError as exc:
            return _refuse(rep, "E_ARGS", str(exc))
        scene = bpy.context.scene
        warnings = rep["warnings"]
        origin = asset_origin(scene, warnings)
        dg = bpy.context.evaluated_depsgraph_get()
        nodes, col, fobjs, extra = _tool_context(ob, frame_parts, scene, dg, origin, warnings)
        lensA = nodes[0]["_arrays"]
        bodies = [b for b, n in zip(col["bodies"], col["body_node"]) if n["name"] == ob.name]
        if col["refused"]:
            x = [r for r in col["refused"] if r["node"] == ob.name]
            if x:
                return _refuse(rep, x[0]["code"], f"{x[0]['component']}: {x[0]['message']}")
        probs = [p for p in col["problems"] if p["node"] == ob.name
                 or any(b.name in p.get("lenses", ()) for b in bodies)]
        if probs:
            return _refuse(rep, probs[0]["code"], probs[0]["message"])
        if not bodies:
            return _refuse(rep, "E_NO_BODY", f"{ob.name} has no closed lens body at least 0.3 mm thick")
        near, seen = [], set()
        for b in bodies:
            for n, c in CK.parts_near(b, col["solids"]):
                if (n["name"], c.name) not in seen:
                    seen.add((n["name"], c.name))
                    near.append((n, c))
        parts = [(c.name, c.V, c.T) for _, c in near]
        rep["frame_parts"] = [c.name for _, c in near]
        rep["riders"] = [r["component"] for r in col["riders"]]
        boxes = [CK.rim_box(b) for b in bodies]
        obstacles = _obstacles(col, ob.name, extra, origin, dg, boxes)
        rep["obstacles"] = [o[0] for o in obstacles]
        ranges = _ranges_of([ob] + fobjs) if ft.in_range_rule else None
        # translucent capturing parts (auto) decide the V-bevel; nylor decides the groove (§4 step 8, §5)
        auto_tr = _capturing_translucent(bodies, near, ft, ranges)
        is_tr = auto_tr["any"] if translucent is None else bool(translucent)
        rep["translucent"] = {"value": is_tr, "auto": translucent is None, "capturing": auto_tr["names"]}
        profile_kind = "vbevel" if (is_tr and ft.profile == "flat_or_vbevel") else (
            "groove" if ft.profile == "groove" else None)
        if is_tr and ft.profile == "flat":
            warnings.append(f"translucent capturing part on a {ft.name} frame: no V-bevel profile for this type")
        rep["profile"] = profile_kind
        prev = CK.seat_extras({"extras": _extras(ob)})
        if prev.get("profile"):
            return _refuse(rep, "E_ALREADY_PROFILED", f"{ob.name} already carries a {prev['profile']} edge profile "
                                                      f"from seat_lens: call unseat_lens first to seat it again")
        min_edge = ft.min_edge_mm if profile_kind else None
        ft_grow = ft
        if profile_kind:
            from .seat import profile as PR
            ft_grow, prof_info = PR.growth_type_for(ft, profile_kind, bodies)
            rep["profile_plan"] = prof_info
        res = deform.seat_arrays(lensA.V, lensA.T, parts, ft_grow, bvh_factory, ranges=ranges, obstacles=obstacles,
                                 poly_edges_raw=lensA.E, min_edge_mm=min_edge, roundtrip=lensA.roundtrip_rows,
                                 name=ob.name)
        rep.update(res.as_dict())
        rep["warnings"] = warnings + res.warnings
        if res.status == "refused":
            rep["seconds"] = round(time.time() - t0, 3)
            return rep
        status = res.status
        if profile_kind:
            # the core's check verdict reads the grown edge BEFORE the profile; a profiled lens is graded by the
            # binding check after it (below), so the plan's own status stands until then
            status = res.plan_status
            pre = [w for w in res.warnings if w.startswith("W_CHECK_FAILS")]
            rep["warnings"] = warnings + [w for w in res.warnings if not w.startswith("W_CHECK_FAILS")]
            if pre:
                rep["pre_profile_check"] = pre
        rep["status"] = status
        rec = stations.recognise(lensA.V, lensA.T, ob.name)
        sh = sharpen_decision(ob, lensA, rec)
        rep["sharpen"] = {k: v for k, v in sh.items() if k != "edges"}
        writes = res.moved_raw > 0 or sh["sharpen"] or profile_kind is not None
        if dry_run:
            rep["seconds"] = round(time.time() - t0, 3)
            return rep
        digest0 = _mesh_digest(ob)
        prev_seat = CK.seat_extras({"extras": _extras(ob)})
        if writes:
            seat_before = ob.get(PROP_SEAT)
            rollback = ob.data.copy()
            rollback.name = ob.data.name + " | g3d rollback"
            undo_backups = (ob.get(PROP_BACKUPS), "seat" not in _backups(ob))
            rep["backup"] = _keep_backup(ob, "seat")
        custom = bool(ob.data.has_custom_normals)
        N0 = _corner_normals(ob.data) if custom and (res.moved_raw or sh["sharpen"]) else None
        if res.moved_raw:
            _write_deltas(ob, lensA, res.raw_delta)
        if sh["sharpen"]:
            rep["sharpen"]["marked"] = apply_sharp(ob, sh["edges"])
        if N0 is not None:
            # every corner normal is set after the move and the sharp edges (§4 steps 9-10); with the rim sharpened,
            # its corners are split by the cap/wall dihedral (a sharp edge alone leaves custom normals as stored)
            moved_v = np.any(res.raw_delta != 0, axis=1)
            normals = _restore_normals(ob, lensA, N0, moved_v, res.rot, _poly_classes(lensA, rec),
                                       split_v=_rim_vertices(lensA, rec) if sh["sharpen"] else None)
            rep["normals"] = normals
            if normals["unmoved_max_change_deg"] > NORMALS_MAX_DEG:
                raise _Rollback("E_POSTCHECK", f"unmoved corner normals changed by "
                                               f"{normals['unmoved_max_change_deg']:.3f} deg (limit 0.1)")
            if normals["unmoved_max_change_deg"] > 0:
                rep["warnings"].append(f"W_NORMALS_REENCODED: unmoved corners within "
                                       f"{normals['unmoved_max_change_deg']:.4f} deg")
        if profile_kind:
            # A profile can create scene objects/modifiers before returning (or raising).
            profile_started = True
            prep = _apply_profile(ob, profile_kind, ft, near, nodes, origin, dg, rep)
            rep["profile_result"] = prep
            if prep.get("status") != "ok":
                raise _Rollback(prep.get("code", "E_PROFILE"), prep.get("message", "edge profile failed"))
        # post-write verification: re-read, the planned positions are what is stored, and a second call moves 0
        if writes:
            verify = _verify_seat(ob, lensA, res, parts, ft_grow, ranges, obstacles, min_edge, origin,
                                  profile_kind is not None)
            rep["verify"] = verify
            if verify["failures"]:
                raise _Rollback("E_POSTCHECK", "; ".join(verify["failures"]))
        # g3d_seat 'sharpened' makes the check demand a rim split >= 60 deg in the GLB (it catches an export that lost
        # the sharp edges), so it is recorded only when the stored rim has that split, measured as the check does
        sharpened = False
        if sh["sharpen"] or prev_seat.get("sharpened"):
            split = rim_split_now(ob, origin)
            rep["rim_split"] = split
            sharpened = split["ok"]
            if not sharpened:
                rep["warnings"].append(f"W_RIM_SPLIT_LOW: the rim edges are marked sharp but the rim split p5 is "
                                       f"{split['front_p5_min']} deg (< {rules.RIM_SPLIT_MIN_DEG:g}): the cap/wall "
                                       f"dihedral is shallow there, so g3d_seat does not claim a sharpened rim")
        if writes and status == "already_seated":
            status = "seated"                   # nothing grew, but the rim was sharpened / profiled
        rep["status"] = status
        seat_prop = {"tool": deform.TOOL_VERSION, "frame_type": ft.name, "lower_edge": ft.lower_edge,
                     "pass_mm": ft.pass_mm, "target_mm": ft.target_mm, "capture_mm": capture_mm,
                     "translucent": is_tr, "profile": profile_kind, "ranges": [list(r) for r in ranges]
                     if ranges else None, "status": status, "digest_before": digest0,
                     "digest_after": _mesh_digest(ob), "sharpened": bool(sharpened),
                     "verdicts": res.bodies[-1].get("after", res.bodies[-1].get("before")) if res.bodies else None}
        ob[PROP_SEAT] = json.dumps(seat_prop, sort_keys=True)
        rep["g3d_seat"] = seat_prop
        if profile_kind:
            # a profiled lens cannot be re-planned (a second call refuses), so the binding check grades the result;
            # until it passes the tool says partial, never seated (review fix round 1: the crystal fixture came back
            # 'seated' while the check failed 352/352 points, open item B)
            chk = check_lens_seating(lenses=[ob.name], frame_parts=fobjs if frame_parts is not None else None,
                                     hidden_x_range_mm=ranges)
            Ls = [x for x in chk.get("lenses", []) if x.get("node") == ob.name]
            ok = bool(Ls) and all(x.get("pass") for x in Ls)
            rep["check"] = {"pass": ok, "reasons": [y for x in Ls for y in x.get("reasons", [])] or chk.get("reasons"),
                            "verdicts": [x.get("verdicts") for x in Ls]}
            if not ok:
                rep["status"] = "partial"
                rep["warnings"].append("W_CHECK_FAILS: the binding check fails the profiled lens: "
                                       + "; ".join(str(x) for x in (rep["check"]["reasons"] or [])[:3]))
                seat_prop["status"] = "partial"
                ob[PROP_SEAT] = json.dumps(seat_prop, sort_keys=True)
            cov = (rep.get("profile_result") or {}).get("cord_lens_overlap_mm3")
            if cov is not None and cov > PR.OVERLAP_MAX_MM3:
                pr_ = rep["profile_result"]
                who = ", ".join(sorted({r["object"] for r in pr_.get("cord_parts", [])})) or pr_.get("cord")
                rep["status"] = "partial"
                rep["warnings"].append(
                    f"W_CORD_OVERLAP: the {'existing ' if pr_.get('cord') == 'existing' else ''}cord ({who}) overlaps "
                    f"the grooved lens by {cov:.4f} mm3 (> {PR.OVERLAP_MAX_MM3:g}); an existing cord is never moved "
                    f"(§5): move it into the groove, or remove it so seat_lens builds one")
                seat_prop["status"] = "partial"
                ob[PROP_SEAT] = json.dumps(seat_prop, sort_keys=True)
        rep["seconds"] = round(time.time() - t0, 3)
        return rep
    except _Rollback as rb:
        if rollback is not None and ob is not None:
            if profile_started:
                remove_profile_bpy(ob)
            _swap_mesh(ob, rollback)
            rollback = None
            _undo_backup(ob, "seat", undo_backups)
            if seat_before is None:
                if PROP_SEAT in ob:
                    del ob[PROP_SEAT]
            else:
                ob[PROP_SEAT] = seat_before
        rep.update(status="refused", code=rb.code, message=rb.message, rolled_back=True)
        rep["seconds"] = round(time.time() - t0, 3)
        return rep
    except Exception as exc:
        # A missing input object is an argument refusal. A lookup failure after writing is a failed transaction
        # like any other exception and must restore the mesh and every newly-created profile artifact.
        args_error = isinstance(exc, LookupError) and rollback is None
        if rollback is not None and ob is not None:
            if profile_started:
                remove_profile_bpy(ob)
            _swap_mesh(ob, rollback)
            rollback = None
            _undo_backup(ob, "seat", undo_backups)
            if seat_before is None:
                if PROP_SEAT in ob:
                    del ob[PROP_SEAT]
            else:
                ob[PROP_SEAT] = seat_before
            rep["rolled_back"] = True
        rep.update(status="refused", code="E_ARGS" if args_error else "E_INTERNAL",
                   message=str(exc).strip("'\"") if args_error else f"{type(exc).__name__}: {exc}",
                   traceback=traceback.format_exc(limit=8))
        return rep
    finally:
        if rollback is not None:
            bpy.data.meshes.remove(rollback)


class _Rollback(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def _capturing_translucent(bodies, near, ft, ranges) -> dict:
    """Translucent parts among the innermost mid-depth owners of the lens's stations (§4 translucent=None)."""
    names = set()
    if not any(n["translucent"] for n, _ in near):
        return {"any": False, "names": []}
    for b in bodies:
        be = bvh_factory([(c.V, c.T) for _, c in near] + [(b.V, b.T)])
        fr, meas = stations.measure_body(b, be, len(near))
        for o in set(meas.owner[:, stations.MID].tolist()):
            if o >= 0 and near[o][0]["translucent"]:
                names.add(near[o][1].name)
    return {"any": bool(names), "names": sorted(names)}


def _verify_seat(ob, A0: MeshArrays, res, parts, ft, ranges, obstacles, min_edge, origin, profiled) -> dict:
    """Re-read the lens: the stored positions of the original vertices are the planned ones and every unmoved one
    is bit-identical (also after a profile appended its wall), and a second call plans nothing (moved 0, status
    already_seated) when the first one seated the lens without a profile (a profiled lens refuses a second call;
    seat_lens runs the binding check on it instead)."""
    fails = []
    A1 = MeshArrays(ob, origin)
    out = {}
    planned = A0.V + res.raw_delta
    unmoved = ~np.any(res.raw_delta != 0, axis=1)
    n0 = len(A0.V)
    # a profile appends its wall vertices after the original ones and keeps every original vertex (bmesh deletes the
    # wall FACES only), so the original rows are checked either way (review fix round 1: a profiled lens was not
    # verified at all)
    bad_count = len(A1.V) < n0 if profiled else len(A1.V) != n0
    if bad_count:
        fails.append(f"the stored mesh has {len(A1.V)} vertices for {n0} planned")
    else:
        V1 = A1.V[:n0]
        dmax = float(np.abs(V1 - planned).max()) if n0 else 0.0
        out["stored_vs_planned_max_mm"] = dmax
        if dmax > STORE_TOL_MM:
            fails.append(f"stored positions differ from the plan (max {dmax} mm)")
        if not np.array_equal(V1[unmoved], A0.V[unmoved]):
            fails.append("a vertex outside the moved nodes changed")
    if res.status in ("seated", "already_seated") and not profiled:
        again = deform.seat_arrays(A1.V, A1.T, parts, ft, bvh_factory, ranges=ranges, obstacles=obstacles,
                                   poly_edges_raw=A1.E, min_edge_mm=min_edge, roundtrip=A1.roundtrip_rows,
                                   name=ob.name)
        out["second_call"] = {"status": again.status, "moved_vertices": again.moved_raw, "code": again.code}
        if again.moved_raw or again.status != "already_seated":
            fails.append(f"a second call would move {again.moved_raw} vertices (status {again.status}, "
                         f"{again.code})")
    out["failures"] = fails
    return out


# ------------------------------------------------------------------------------------------------ edge profile
CUTTER_PREFIX = "g3d_groove_cutter | "
CUTTER_COLLECTION = "g3d_cutters"
GROOVE_MODIFIER = "g3d_groove"
CORD_SUFFIX = " | nylor cord"


def _apply_profile(ob, kind, ft, near, nodes, origin, dg, rep) -> dict:
    """§5 on the grown lens: re-wall (V-bevel or groove) with bmesh, then the crystal groove (cutter + live EXACT
    Boolean on each translucent capturing part, MANIFOLD fallback, volume check) or the nylor cord."""
    import bmesh
    from .seat import profile as PR
    out = {"status": "failed", "kind": kind}
    A = MeshArrays(ob, origin)
    rec = stations.recognise(A.V, A.T, ob.name)
    if len(rec.bodies) != 1:
        return dict(out, code="E_PROFILE_WALL", message=f"edge profiles need exactly one lens body ({len(rec.bodies)})")
    if ob.data.has_custom_normals:
        return dict(out, code="E_PROFILE_NORMALS", message="the lens has custom normals; an edge profile rebuilds the "
                                                           "wall and cannot keep them (clear them first)")
    body = rec.bodies[0]
    Vw, vid = geom.weld_exact(A.V)
    cnt = np.bincount(vid, minlength=len(Vw))
    rim_g = body.source_verts[np.r_[body.fl, body.bl]]
    if (cnt[rim_g] != 1).any():
        return dict(out, code="E_PROFILE_WALL", message="the rim has duplicate vertices (a UV seam split): weld the "
                                                        "rim before an edge profile")
    raw_of = np.full(len(Vw), -1, np.int64)
    raw_of[vid] = np.arange(len(vid))
    fr = stations.station_frames(body)
    from .seat.plan import node_points
    npt = node_points(fr, body.V)
    loop = fr.loop
    g2b = np.full(len(Vw), -1, np.int64)
    g2b[body.source_verts] = np.arange(len(body.V))
    pe = g2b[vid[A.E]]
    pe = pe[(pe >= 0).all(1)]
    partner = geom.rungs(body.V, loop, body.bl, pe)
    if (partner < 0).any() or len(set(partner.tolist())) != len(partner) or len(body.bl) != len(loop):
        return dict(out, code="E_PROFILE_WALL", message="the wall is not one ring of quads with a rung for every rim "
                                                        "vertex")
    front_raw = raw_of[body.source_verts[loop]]
    back_raw = raw_of[body.source_verts[partner]]
    u = fr.u[npt]
    me = ob.data
    poly_cls = _poly_classes(A, rec)
    rimset = set(front_raw.tolist()) | set(back_raw.tolist())
    wall_polys = [p.index for p in me.polygons if poly_cls[p.index] == 2 and set(p.vertices) <= rimset]
    if len(wall_polys) != len(loop):
        return dict(out, code="E_PROFILE_WALL", message=f"{len(wall_polys)} outer wall faces for {len(loop)} rim "
                                                        f"vertices (not one quad ring)")
    cap_dir = None
    a0, b0 = int(front_raw[0]), int(front_raw[1])
    for p in me.polygons:
        if poly_cls[p.index] == 0:
            vs = list(p.vertices)
            if a0 in vs and b0 in vs:
                i, j = vs.index(a0), vs.index(b0)
                cap_dir = (a0, b0) if (j - i) % len(vs) == 1 else (b0, a0)
                break
    plan = PR.rewall_plan(A.V, front_raw, back_raw, u, kind, cap_dir)
    out["rewall"] = plan.info
    mat_index = me.polygons[wall_polys[0]].material_index
    smooth = me.polygons[wall_polys[0]].use_smooth
    M3i = np.linalg.inv(A.M3)
    newV_local = (plan.new_V + A.origin - A.t) @ M3i.T
    n_raw = len(A.V)
    bm = bmesh.new()
    bm.from_mesh(me)
    bm.faces.ensure_lookup_table()
    bmesh.ops.delete(bm, geom=[bm.faces[i] for i in wall_polys], context="FACES_ONLY")
    bm.verts.ensure_lookup_table()
    new_verts = [bm.verts.new(tuple(map(float, p))) for p in newV_local]
    bm.verts.ensure_lookup_table()

    def vert(i):
        return bm.verts[i] if i < n_raw else new_verts[i - n_raw]
    for f in plan.faces:
        nf = bm.faces.new([vert(i) for i in f])
        nf.material_index = mat_index
        nf.smooth = smooth
    bm.edges.ensure_lookup_table()
    sharp_pairs = set()
    for lp in plan.loops:
        for a, b in zip(lp.tolist(), np.roll(lp, -1).tolist()):
            sharp_pairs.add((min(a, b), max(a, b)))
    bm.verts.index_update()
    for e in bm.edges:
        a, b = e.verts[0].index, e.verts[1].index
        if (min(a, b), max(a, b)) in sharp_pairs:
            e.smooth = False
    bm.to_mesh(me)
    bm.free()
    me.update()
    A2 = MeshArrays(ob, origin)
    topo = geom.topology(geom.weld_exact(A2.V)[1][A2.T])
    out["lens_topology"] = {k: topo[k] for k in ("closed", "euler", "components", "misoriented_edges")}
    if not topo["closed"] or topo["euler"] != body.topo["euler"] or topo["components"] != 1 \
            or topo["misoriented_edges"]:
        return dict(out, code="E_PROFILE_WALL", message=f"the re-walled lens is not a closed oriented solid with the "
                                                        f"same Euler characteristic: {out['lens_topology']}")
    out["lens_volume_mm3"] = round(geom.signed_volume(A2.V, A2.T), 4)
    if kind == "vbevel":
        res = _crystal_groove(ob, A2, near, nodes, origin)
    else:
        res = _nylor_cord(ob, A2, near, nodes, origin, ft)
    out.update(res)
    if res.get("status") == "ok":
        out["status"] = "ok"
    return out


def _cutter_collection():
    coll = bpy.data.collections.get(CUTTER_COLLECTION)
    if coll is None:
        coll = bpy.data.collections.new(CUTTER_COLLECTION)
        bpy.context.scene.collection.children.link(coll)
    coll.hide_render = True
    return coll


def _volume_of(ob, depsgraph=None) -> float:
    A = MeshArrays(ob, np.zeros(3), depsgraph)
    return geom.signed_volume(A.V, A.T)


def _static_copy(ob, name: str):
    """A modifier-free object holding `ob`'s evaluated mesh in world space (temporary)."""
    dg = bpy.context.evaluated_depsgraph_get()
    me = bpy.data.meshes.new_from_object(ob.evaluated_get(dg), preserve_all_data_layers=False, depsgraph=dg)
    me.transform(ob.matrix_world)
    tmp = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(tmp)
    return tmp


def _component_object(comp, origin, name: str):
    """A temporary world-space object holding one closed component (asset mm arrays + the asset origin)."""
    me = bpy.data.meshes.new(name)
    me.from_pydata((np.asarray(comp.V, np.float64) + origin).tolist(), [], np.asarray(comp.T, np.int64).tolist())
    me.update()
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def _bool_volume(a, b, op: str = "INTERSECT", solver: str = "EXACT") -> float:
    """Volume of a (op) b, both evaluated, through a temporary object."""
    tmp = _static_copy(a, "g3d tmp boolean")
    try:
        mod = tmp.modifiers.new("tmp", "BOOLEAN")
        mod.operation, mod.solver, mod.object = op, solver, b
        bpy.context.view_layer.update()
        return _volume_of(tmp, bpy.context.evaluated_depsgraph_get())
    finally:
        me = tmp.data
        bpy.data.objects.remove(tmp, do_unlink=True)
        bpy.data.meshes.remove(me)


def solid_topology(A: "MeshArrays") -> dict:
    """Topology of a Blender mesh by its own connectivity, welding only split seams (geom.weld_seams): a Boolean
    result may hold two distinct vertices at one position, and an exact weld everywhere turned that closed result
    into 4 non-manifold edges and a false E_BOOLEAN (WP4 proof review, the crystal control cutter)."""
    return geom.topology(geom.weld_seams(A.V, A.T)[A.T], len(A.V))


def groove_attempt(fob, cutter, lens_ob, origin, solver: str, topo_before: dict, vol0: float,
                   expect: float) -> tuple[dict, bool]:
    """One §5 groove attempt on frame part `fob`: the live DIFFERENCE modifier `g3d_groove` with `solver` (before
    any WeightedNormal / Bevel), evaluated, and the acceptance test: closed, chi and components unchanged, removed
    volume = vol(cutter ^ frame) +- 1%, lens ^ frame <= 0.01 mm3. Returns (record, accepted); the modifier stays."""
    from .seat import profile as PR
    mod = fob.modifiers.get(GROOVE_MODIFIER) or fob.modifiers.new(GROOVE_MODIFIER, "BOOLEAN")
    mod.operation, mod.solver, mod.object = "DIFFERENCE", solver, cutter
    mod.show_viewport = mod.show_render = True
    idx = [i for i, m in enumerate(fob.modifiers) if m.type in ("WEIGHTED_NORMAL", "BEVEL")]
    cur = list(fob.modifiers).index(mod)
    if idx and idx[0] < cur:
        fob.modifiers.move(cur, idx[0])
    t0 = time.time()
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    after = MeshArrays(fob, origin, dg)
    dt = time.time() - t0
    ta = solid_topology(after)
    removed = vol0 - geom.signed_volume(after.V, after.T)
    overlap = _bool_volume(lens_ob, fob, "INTERSECT")
    chk = {"solver": solver, "seconds": round(dt, 3), "removed_mm3": round(removed, 4),
           "closed": ta["closed"], "euler": [topo_before["euler"], ta["euler"]],
           "components": [topo_before["components"], ta["components"]], "lens_overlap_mm3": round(overlap, 5),
           "boundary_edges": ta["boundary_edges"], "nonmanifold_edges": ta["nonmanifold_edges"]}
    good = bool(ta["closed"] and ta["euler"] == topo_before["euler"]
                and ta["components"] == topo_before["components"]
                and expect > 0 and abs(removed - expect) <= PR.REMOVED_TOLERANCE * expect
                and overlap <= PR.OVERLAP_MAX_MM3)
    return chk, good


def _crystal_groove(lens_ob, A, near, nodes, origin) -> dict:
    """§5 'Groove in a translucent frame'."""
    from .seat import profile as PR
    out = {"status": "failed"}
    by_name = {n["name"]: n for n in nodes}
    targets = []
    for n, c in near:
        ob = by_name.get(n["name"], {}).get("_object")
        if ob is not None and n["translucent"] and ob not in targets:
            targets.append(ob)
    if not targets:
        return dict(out, code="E_BOOLEAN", message="no translucent frame part to groove")
    Vc = PR.inflate(A.V, A.T, PR.CLEARANCE_MM) + A.origin
    name = CUTTER_PREFIX + lens_ob.name
    old = bpy.data.objects.get(name)
    if old is not None:
        me_old = old.data
        bpy.data.objects.remove(old, do_unlink=True)
        if me_old.users == 0:
            bpy.data.meshes.remove(me_old)
    me = bpy.data.meshes.new(name)
    me.from_pydata(Vc.tolist(), [], A.T.tolist())
    me.update()
    cutter = bpy.data.objects.new(name, me)
    _cutter_collection().objects.link(cutter)
    cutter.hide_render = True
    cutter.display_type = "WIRE"
    cutter["g3d_lens_digest"] = _mesh_digest(lens_ob)
    cutter["g3d_created_by"] = lens_ob.name
    out["cutter"] = name
    out["parts"] = []
    for fob in targets:
        before = MeshArrays(fob, origin, bpy.context.evaluated_depsgraph_get())
        tb = solid_topology(before)
        vol0 = geom.signed_volume(before.V, before.T)
        expect = _bool_volume(fob, cutter, "INTERSECT")
        rec = {"part": fob.name, "expected_removed_mm3": round(expect, 4)}
        ok = False
        for solver in ("EXACT", "MANIFOLD"):
            chk, good = groove_attempt(fob, cutter, lens_ob, origin, solver, tb, vol0, expect)
            rec.setdefault("attempts", []).append(chk)
            if good:
                gap = _groove_gap(lens_ob, fob, origin)
                chk.update(gap)
                good = gap["gap_ok"] and gap["frame_left_ok"]
            if good:
                ok = True
                break
        out["parts"].append(rec)
        if not ok:
            remove_profile_bpy(lens_ob)
            return dict(out, code="E_BOOLEAN", message=f"the groove in {fob.name} failed its checks with EXACT and "
                                                       f"MANIFOLD: {rec['attempts']}")
    out["status"] = "ok"
    return out


def _groove_gap(lens_ob, frame_ob, origin) -> dict:
    """Mid-depth gap between the profiled lens and the grooved frame along every station's u, and the frame left
    beyond the groove (§5: gap 0.02-0.06 mm, >= 0.5 mm of frame on the station lines)."""
    from .seat import profile as PR
    dg = bpy.context.evaluated_depsgraph_get()
    L = MeshArrays(lens_ob, origin)
    F = MeshArrays(frame_ob, origin, dg)
    rec = stations.recognise(L.V, L.T, lens_ob.name)
    body = rec.bodies[0]
    comps = [c for c in stations.split_components(F.V, F.T, frame_ob.name) if c.closed]
    be = bvh_factory([(c.V, c.T) for c in comps] + [(body.V, body.T)])
    fr, meas = stations.measure_body(body, be, len(comps))
    held = np.isfinite(meas.cap[:, stations.MID]) & (meas.cls.astype(str) != "EXPOSED")
    gap = -meas.cap[held, stations.MID]
    room = meas.room[held, stations.MID]
    g = [round(float(x), 4) for x in np.percentile(gap, [0, 50, 100])] if len(gap) else None
    return {"gap_min_p50_max_mm": g, "frame_left_min_mm": round(float(room.min()), 4) if len(room) else None,
            "gap_ok": bool(len(gap) and gap.min() >= PR.GAP_RANGE_MM[0] - 1e-4 and gap.max() <= PR.GAP_RANGE_MM[1] + 1e-4),
            "frame_left_ok": bool(len(room) and room.min() >= PR.FRAME_LEFT_MM)}


def _nylor_cord(lens_ob, A, near, nodes, origin, ft) -> dict:
    """§5: if no frame part lies in the groove along the exposed sector, add `<lens> | nylor cord` (closed 12-sided
    0.60 mm tube on the groove centreline, ends 1.0 mm into the channel, partRole frame, the channel's material).
    An existing cord is never moved. Either way the cord-lens overlap (EXACT Boolean on the grooved lens) is returned
    as `cord_lens_overlap_mm3` (per object in `cord_parts` for an existing cord); seat_lens says partial with
    W_CORD_OVERLAP above 0.01 mm3."""
    from .seat import profile as PR
    out = {"status": "failed"}
    rec = stations.recognise(A.V, A.T, lens_ob.name)
    body = rec.bodies[0]
    comps = [c for _, c in near]
    be = bvh_factory([(c.V, c.T) for c in comps] + [(body.V, body.T)])
    fr, meas = stations.measure_body(body, be, len(comps))
    cls = meas.cls.astype(str)
    visible = np.isin(cls, ("BACKED", "NEAR", "EXPOSED"))
    arcs = PR.exposed_arcs(fr, visible)
    out["exposed_points"] = int(visible.sum())
    if not arcs:
        out.update(status="ok", cord=None, note="no exposed sector")
        return out
    idx = np.flatnonzero(visible)
    G = fr.depth_points()[idx, stations.MID] - (PR.GROOVE_DEPTH_MM / 2) * fr.u[idx]
    res = line_intervals(be, G, fr.n[idx], fr.t[idx])
    inside = 0
    held_by = np.zeros(len(comps), np.int64)
    for q in range(len(idx)):
        per = res.intervals[q][:len(comps)]
        hit = [p for p, iv in enumerate(per)
               if iv is not None and len(iv) and ((iv[:, 0] <= 0) & (iv[:, 1] >= 0)).any()]
        held_by[hit] += 1
        inside += bool(hit)
    out["groove_points_with_frame"] = int(inside)
    if inside >= 0.5 * len(idx):
        # an existing cord is never moved (§5); its overlap with the grooved lens is measured here with the EXACT
        # Boolean, for every closed component holding the groove centre on the exposed sector, on its own (a cord
        # merged into the channel's object must not add the channel's buried edge) (WP4 proof review round 2: the
        # stage-proof cord overlapped the grooved lens by 0.74 mm3 and the tool said 'seated' without a word)
        recs = []
        for p in np.flatnonzero(held_by).tolist():
            node, comp = near[p]
            tmp = _component_object(comp, origin, "g3d tmp cord")
            try:
                ov = float(_bool_volume(tmp, lens_ob, "INTERSECT"))
            finally:
                me_t = tmp.data
                bpy.data.objects.remove(tmp, do_unlink=True)
                bpy.data.meshes.remove(me_t)
            recs.append({"object": node["name"], "component": comp.name, "groove_points": int(held_by[p]),
                         "overlap_mm3": round(ov, 5)})
        out.update(status="ok", cord="existing", cord_parts=recs,
                   cord_lens_overlap_mm3=round(sum(r["overlap_mm3"] for r in recs), 5))
        return out
    owners = meas.owner[:, stations.MID]
    owners = owners[owners >= 0]
    channel = None
    if len(owners):
        ch = int(np.bincount(owners).argmax())
        nd = {n["name"]: n for n in nodes}.get(near[ch][0]["name"])
        channel = nd.get("_object") if nd else None
    Vs, Ts = [], []
    for run in arcs:
        C, U, N = PR.cord_centreline(fr, run)
        V, T = PR.tube(C, U, N)
        Ts.append(T + sum(len(v) for v in Vs))
        Vs.append(V)
    V = np.vstack(Vs) + A.origin
    T = np.vstack(Ts)
    name = lens_ob.name + CORD_SUFFIX
    me = bpy.data.meshes.new(name)
    me.from_pydata(V.tolist(), [], T.tolist())
    me.update()
    cord = bpy.data.objects.new(name, me)
    (lens_ob.users_collection[0] if lens_ob.users_collection else bpy.context.scene.collection).objects.link(cord)
    cord["partRole"] = "frame"
    cord["g3d_created_by"] = lens_ob.name
    if channel is not None and channel.material_slots and channel.material_slots[0].material is not None:
        me.materials.append(channel.material_slots[0].material)
    overlap = _bool_volume(cord, lens_ob, "INTERSECT")
    out.update(status="ok", cord=name, arcs=len(arcs), cord_lens_overlap_mm3=round(overlap, 5),
               channel=channel.name if channel is not None else None)
    return out


def remove_profile_bpy(lens_ob) -> dict:
    """Remove the groove cutter, every `g3d_groove` modifier that uses it, and a cord seat_lens created."""
    out = {"modifiers": [], "cutter": None, "cord": None}
    name = CUTTER_PREFIX + lens_ob.name
    cutter = bpy.data.objects.get(name)
    if cutter is not None:
        for o in bpy.data.objects:
            for m in list(o.modifiers):
                if m.type == "BOOLEAN" and m.name.startswith(GROOVE_MODIFIER) and m.object == cutter:
                    o.modifiers.remove(m)
                    out["modifiers"].append(o.name)
        me = cutter.data
        bpy.data.objects.remove(cutter, do_unlink=True)
        if me.users == 0:
            bpy.data.meshes.remove(me)
        out["cutter"] = name
    cord = bpy.data.objects.get(lens_ob.name + CORD_SUFFIX)
    if cord is not None and cord.get("g3d_created_by") == lens_ob.name:
        me = cord.data
        bpy.data.objects.remove(cord, do_unlink=True)
        if me.users == 0:
            bpy.data.meshes.remove(me)
        out["cord"] = lens_ob.name + CORD_SUFFIX
    return out


# ------------------------------------------------------------------------------------------------ hide_eyewire
def hide_eyewire(eyewire, under, hidden_x_range_mm, *, lens=None, margin_mm: float = 0.10, ramp_mm: float = 3.0,
                 lift_max_mm: float = 2.5, dry_run: bool = False) -> dict:
    """§6. Buries the eyewire inside or behind `under` over the asset-x ranges and moves the held lens's band with
    it. The held lens must be a partRole 'lens' object (guessed by box overlap when lens=None) and, once an eyewire
    was lifted with a lens, the same lens. Afterwards the eyewire and the lens declare the union of this call's
    ranges and the ones declared before (`ranges_effective` in the report), and the post-check grades all of them.
    Returns a report: status lifted | already_hidden | refused."""
    t0 = time.time()
    rep = {"tool": "hide_eyewire", "eyewire": str(getattr(eyewire, "name", eyewire)), "status": "refused",
           "code": None, "message": "", "warnings": [], "dry_run": bool(dry_run)}
    rb_e = rb_l = None
    eob = lob = None
    try:
        from .calls import hide_argument_error
        error = hide_argument_error(margin_mm, ramp_mm, lift_max_mm)
        if error:
            return _refuse(rep, "E_ARGS", f"{rep['eyewire']}: {error}")
        eob = _obj(eyewire)
        rep["eyewire"] = eob.name
        bad = _lens_refusal(eob)
        if bad:
            return _refuse(rep, bad[0].replace("LENS", "EYEWIRE"), bad[1])
        uobs = [_obj(x) for x in _as_list(under)]
        ranges = [(float(min(r)), float(max(r))) for r in (hidden_x_range_mm or [])]
        rep["ranges"] = [list(r) for r in ranges]
        if not ranges:
            return _refuse(rep, "E_NO_RANGES", "hidden_x_range_mm is empty")
        scene = bpy.context.scene
        warnings = rep["warnings"]
        origin = asset_origin(scene, warnings)
        dg = bpy.context.evaluated_depsgraph_get()
        objs = sorted(_render_objects(scene), key=lambda o: o.name)
        EA = MeshArrays(eob, origin)
        if lens is not None:
            lob = _obj(lens)
        else:
            lob = _guess_held_lens(eob, EA, [o for o in objs if _role(o) == "lens"], origin)
            if lob is None:
                return _refuse(rep, "E_NO_HELD_LENS", "no render-visible partRole lens near the eyewire: pass lens=")
        rep["lens"] = lob.name
        if _role(lob) != "lens":
            return _refuse(rep, "E_ARGS", f"{lob.name} is not a partRole 'lens' object (role {_role(lob)!r}): pass the "
                                          f"lens the eyewire holds")
        bad = _lens_refusal(lob)
        if bad:
            return _refuse(rep, *bad)
        held_before = _backups(eob).get(HELD_LENS)
        if held_before and held_before != lob.name:
            return _refuse(rep, "E_ARGS", f"{eob.name} was lifted with lens {held_before!r} (g3d_backups): pass "
                                          f"lens={held_before!r}, or call unhide_eyewire first")
        by_before = _backups(lob).get(HIDDEN_BY)
        if by_before and by_before != eob.name and _has_backup(lob, "hide"):
            return _refuse(rep, "E_ARGS", f"{lob.name} was moved by hide_eyewire({by_before!r}) (g3d_backups): one "
                                          f"eyewire holds a lens; call unhide_eyewire({by_before!r}) first")
        # the hidden ranges the scene declares afterwards: this call's plus the ones declared before (a call over a
        # sub-range must not un-declare a lifted span, review fix round 1)
        declared = merge_ranges((_ranges_of([eob, lob]) or []) + ranges)
        rep["ranges_effective"] = [list(r) for r in declared]
        LA = MeshArrays(lob, origin)
        under_parts = []
        for u in uobs:
            A = MeshArrays(u, origin, dg)
            comps = [c for c in stations.split_components(A.V, A.T, u.name) if c.closed]
            if not comps:
                return _refuse(rep, "E_OPEN", f"`under` part {u.name} has no closed component")
            under_parts += [(c.name, c.V, c.T) for c in comps]
        # the rest of the frame near the lens (closed components, riders excluded)
        nodes = [_node(lob, origin, dg, 0, corners=False, base=True)]
        nodes[0]["lens"] = True
        rest = [o for o in objs if _role(o) == "frame" and o not in uobs and o != eob]
        for i, o in enumerate(rest):
            nd = _node(o, origin, dg, i + 1, corners=False)
            nd["lens"] = False
            nodes.append(nd)
        col = CK.collect([_plain_node(n) for n in nodes], [], warnings, bvh_factory)
        bodies = [b for b, n in zip(col["bodies"], col["body_node"]) if n["name"] == lob.name]
        others = []
        for b in bodies:
            others += [(c.name, c.V, c.T) for _, c in CK.parts_near(b, col["solids"])]
        res = deform.lift_arrays(EA.V, EA.T, EA.E, under_parts, others, LA.V, LA.T, bvh_factory, ranges,
                                 lens_poly_edges_raw=LA.E, margin=margin_mm, ramp=ramp_mm, lift_max=lift_max_mm,
                                 roundtrip_ew=EA.roundtrip_all, roundtrip_lens=LA.roundtrip_rows,
                                 lens_name=lob.name, eyewire_name=eob.name, declared_ranges=declared)
        rep.update(res.as_dict())
        if res.status == "refused" or dry_run:
            rep["seconds"] = round(time.time() - t0, 3)
            return rep
        if res.status == "lifted":
            rb_e = eob.data.copy()
            rb_l = lob.data.copy()
            undo_e = (eob.get(PROP_BACKUPS), "hide" not in _backups(eob))
            undo_l = (lob.get(PROP_BACKUPS), "hide" not in _backups(lob))
            # the first lift also records the declared ranges as they were, so unhide_eyewire puts them back
            rep["backups"] = {"eyewire": _keep_backup(eob, "hide", {RANGES_BEFORE: eob.get(PROP_RANGES)}),
                              "lens": _keep_backup(lob, "hide", {RANGES_BEFORE: lob.get(PROP_RANGES)})}
            bk = _backups(eob)
            bk[HELD_LENS] = lob.name                   # unhide_eyewire restores this lens and no other
            _set_backups(eob, bk)
            bk = _backups(lob)
            bk[HIDDEN_BY] = eob.name
            _set_backups(lob, bk)
            _write_deltas(eob, EA, res.eyewire_delta)
            lr = res.lens_result
            custom = bool(lob.data.has_custom_normals)
            N0 = _corner_normals(lob.data) if custom else None
            _write_deltas(lob, LA, lr.raw_delta)
            if custom and lr.moved_raw:
                recl = stations.recognise(LA.V, LA.T, lob.name)
                nrm = _restore_normals(lob, LA, N0, np.any(lr.raw_delta != 0, axis=1), lr.rot,
                                       _poly_classes(LA, recl))
                rep["lens_normals"] = nrm
                if nrm["unmoved_max_change_deg"] > NORMALS_MAX_DEG:
                    raise _Rollback("E_POSTCHECK", f"unmoved lens corner normals changed by "
                                                   f"{nrm['unmoved_max_change_deg']:.3f} deg")
            # verify: stored = planned, and a second call lifts nothing
            EA1, LA1 = MeshArrays(eob, origin), MeshArrays(lob, origin)
            fails = []
            if float(np.abs(EA1.V - (EA.V + res.eyewire_delta)).max()) > STORE_TOL_MM:
                fails.append("stored eyewire positions differ from the plan")
            if float(np.abs(LA1.V - (LA.V + lr.raw_delta)).max()) > STORE_TOL_MM:
                fails.append("stored lens positions differ from the plan")
            again = deform.lift_arrays(EA1.V, EA1.T, EA1.E, under_parts, others, LA1.V, LA1.T, bvh_factory,
                                       ranges, lens_poly_edges_raw=LA1.E, margin=margin_mm, ramp=ramp_mm,
                                       lift_max=lift_max_mm, lens_name=lob.name, eyewire_name=eob.name,
                                       declared_ranges=declared)
            rep["second_call"] = {"status": again.status, "code": again.code,
                                  "lift_max_mm": again.report.get("lift_max_mm")}
            if again.status != "already_hidden":
                fails.append(f"a second call would not be a no-op (status {again.status}, {again.code}: "
                             f"{again.message[:200]})")
            if fails:
                raise _Rollback("E_POSTCHECK", "; ".join(fails))
        rj = json.dumps([list(r) for r in declared])
        eob[PROP_RANGES] = rj
        lob[PROP_RANGES] = rj
        rep["seconds"] = round(time.time() - t0, 3)
        return rep
    except LookupError as exc:
        return _refuse(rep, "E_ARGS", str(exc).strip("'\""))
    except _Rollback as rb:
        if rb_e is not None:
            _swap_mesh(eob, rb_e)
            rb_e = None
            _undo_backup(eob, "hide", undo_e)
        if rb_l is not None:
            _swap_mesh(lob, rb_l)
            rb_l = None
            _undo_backup(lob, "hide", undo_l)
        rep.update(status="refused", code=rb.code, message=rb.message, rolled_back=True)
        return rep
    except Exception as exc:
        if rb_e is not None:
            _swap_mesh(eob, rb_e)
            rb_e = None
            _undo_backup(eob, "hide", undo_e)
        if rb_l is not None:
            _swap_mesh(lob, rb_l)
            rb_l = None
            _undo_backup(lob, "hide", undo_l)
        rep.update(status="refused", code="E_INTERNAL", message=f"{type(exc).__name__}: {exc}",
                   traceback=traceback.format_exc(limit=8))
        return rep
    finally:
        for m in (rb_e, rb_l):
            if m is not None:
                bpy.data.meshes.remove(m)


def _guess_held_lens(eob, EA: MeshArrays, lenses, origin):
    """The lens whose box overlaps the eyewire's box most (the core then checks the 60% innermost share)."""
    lo, hi = EA.V.min(0), EA.V.max(0)
    best, score = None, 0.0
    for o in lenses:
        A = MeshArrays(o, origin)
        l2, h2 = A.V.min(0), A.V.max(0)
        inter = np.clip(np.minimum(hi, h2) - np.maximum(lo, l2), 0, None)
        s = float(inter[0] * inter[1])
        if s > score:
            best, score = o, s
    return best


# ------------------------------------------------------------------------------------------------------ revert
def _restore_backup(ob, kind: str) -> bool:
    """Swap the `kind` backup in as the object's mesh and forget it (its name and place in the order). False, and
    nothing changed, when there is none."""
    bk = _backups(ob)
    name = bk.get(kind)
    mesh = bpy.data.meshes.get(name) if name else None
    if mesh is None:
        return False
    _swap_mesh(ob, mesh)
    bk.pop(kind)
    seq = dict(bk.get(SEQ) or {})
    seq.pop(kind, None)
    bk[SEQ] = seq
    _set_backups(ob, bk)
    return True


def _drop_backup(ob, kind: str) -> None:
    bk = _backups(ob)
    name = bk.pop(kind, None)
    mesh = bpy.data.meshes.get(name) if name else None
    if mesh is not None and mesh.users <= (1 if mesh.use_fake_user else 0):
        bpy.data.meshes.remove(mesh)
    seq = dict(bk.get(SEQ) or {})
    seq.pop(kind, None)
    bk[SEQ] = seq
    _set_backups(ob, bk)


def _restore_ranges(ob) -> None:
    """Put g3d_hidden_x_ranges_mm back as it was before the first lift (absent when it was), and forget the record."""
    bk = _backups(ob)
    had = RANGES_BEFORE in bk
    before = bk.pop(RANGES_BEFORE, None)
    if before is not None:
        ob[PROP_RANGES] = before
    elif PROP_RANGES in ob:
        del ob[PROP_RANGES]
    if had:
        _set_backups(ob, bk)


def unseat_lens(lens) -> dict:
    """Restore the lens mesh kept before the first seat_lens (exact, also after a profile rebuilt the wall), and
    remove what seat_lens created (groove cutter, Boolean modifiers, cord) and the g3d_seat property.

    Refused (E_ORDER, nothing changed) when the lens was seated BEFORE hide_eyewire moved it: its pre-seat mesh
    predates the lift, so restoring it would pull the lens out of the lifted eyewire; unhide_eyewire first (it
    restores the seated lens), then unseat_lens."""
    rep = {"tool": "unseat_lens", "lens": str(getattr(lens, "name", lens)), "status": "refused"}
    try:
        ob = _obj(lens)
        rep["lens"] = ob.name
        bk = _backups(ob)
        if _has_backup(ob, "seat") and _has_backup(ob, "hide") and not _seat_after_hide(bk):
            by = bk.get(HIDDEN_BY)
            who = f"({by!r})" if by else ""
            return _refuse(rep, "E_ORDER", f"{ob.name} was seated before hide_eyewire{who} moved it: its pre-seat "
                                           f"mesh predates the lift; call unhide_eyewire{who} first (it restores the "
                                           f"seated lens), then unseat_lens")
        rep["profile_removed"] = remove_profile_bpy(ob)
        restored = _restore_backup(ob, "seat")
        if PROP_SEAT in ob:
            del ob[PROP_SEAT]
        rep.update(status="restored" if restored else "nothing_to_restore")
        return rep
    except LookupError as exc:
        return _refuse(rep, "E_ARGS", str(exc).strip("'\""))
    except Exception as exc:
        rep.update(code="E_INTERNAL", message=f"{type(exc).__name__}: {exc}", traceback=traceback.format_exc(limit=8))
        return rep


def unhide_eyewire(eyewire, lens=None) -> dict:
    """Restore the eyewire and the lens it held as they were before hide_eyewire (meshes and declared ranges). The
    held lens is the one recorded in the eyewire's g3d_backups (`held_lens`) when hide_eyewire lifted it; `lens=` may
    name it (a partRole lens). A lens that differs from the recorded one, or any lens when this eyewire has no hide
    backup, is refused (E_ARGS) with nothing changed (review fix round 2: unhide_eyewire('Eyewire_L', lens='Lens_R')
    reverted Lens_R and left Lens_L lifted; unhide on an eyewire never lifted reverted the lens it was given). No
    other object is touched, and the lens only when this eyewire's own backup is restored.

    A seat_lens done on the lens AFTER the hide is undone with it (the pre-hide mesh predates it: its backup is
    dropped, its g3d_seat and profile removed). One done BEFORE the hide is kept: the pre-hide mesh is the seated
    lens, so its pre-seat backup and g3d_seat stay and unseat_lens still works afterwards."""
    rep = {"tool": "unhide_eyewire", "eyewire": str(getattr(eyewire, "name", eyewire)), "status": "refused"}
    try:
        eob = _obj(eyewire)
        rep["eyewire"] = eob.name
        bk = _backups(eob)
        has_hide = _has_backup(eob, "hide")
        held = bk.get(HELD_LENS)
        warnings = []
        if lens is not None:
            lob = _obj(lens)
            if lob is eob or _role(lob) != "lens":
                return _refuse(rep, "E_ARGS", f"{lob.name} is not a partRole 'lens' object (role {_role(lob)!r})")
            if not has_hide:
                return _refuse(rep, "E_ARGS", f"{eob.name} has no hide_eyewire backup (never lifted, or unhidden "
                                              f"already): nothing to restore, and {lob.name} is not touched")
            if held and held != lob.name:
                return _refuse(rep, "E_ARGS", f"{eob.name} was lifted with lens {held!r}, not {lob.name!r} "
                                              f"(g3d_backups): pass lens={held!r} or leave lens unset")
        elif held:
            lob = bpy.data.objects.get(held)
            if lob is None:
                warnings.append(f"the held lens {held!r} recorded on {eob.name} no longer exists")
        else:
            cands = [o for o in bpy.data.objects if o is not eob and _role(o) == "lens" and _has_backup(o, "hide")
                     and _backups(o).get(HIDDEN_BY) in (None, eob.name)]
            if len(cands) > 1 and has_hide:
                return _refuse(rep, "E_ARGS", f"{eob.name} records no held lens and {len(cands)} lenses carry a "
                                              f"hide backup ({[o.name for o in cands]}): pass lens=")
            lob = cands[0] if (len(cands) == 1 and has_hide) else None
        if lob is not None:
            by = _backups(lob).get(HIDDEN_BY)
            if by and by != eob.name:
                return _refuse(rep, "E_ARGS", f"{lob.name} was moved by hide_eyewire({by!r}), not {eob.name!r}")
        if warnings:
            rep["warnings"] = warnings
        if not has_hide:
            rep.update(status="nothing_to_restore", eyewire_restored=False, lenses=[])
            return rep
        done = {"eyewire_restored": _restore_backup(eob, "hide"), "lenses": []}
        bk = _backups(eob)
        bk.pop(HELD_LENS, None)
        _set_backups(eob, bk)
        _restore_ranges(eob)
        if lob is not None:
            if _has_backup(lob, "hide"):
                lbk = _backups(lob)
                if _has_backup(lob, "seat") and not _seat_after_hide(lbk):
                    done["seat_kept"] = True               # seated before the hide: the pre-hide mesh is seated
                elif _has_backup(lob, "seat") or PROP_SEAT in lob:
                    remove_profile_bpy(lob)
                    _drop_backup(lob, "seat")
                    if PROP_SEAT in lob:
                        del lob[PROP_SEAT]
                    done["seat_undone"] = True
                if _restore_backup(lob, "hide"):
                    done["lenses"].append(lob.name)
                lbk = _backups(lob)
                lbk.pop(HIDDEN_BY, None)
                _set_backups(lob, lbk)
                _restore_ranges(lob)
            else:
                rep.setdefault("warnings", []).append(f"{lob.name} has no hide backup: the lens was left as it is")
        rep.update(done, status="restored" if done["eyewire_restored"] else "nothing_to_restore")
        return rep
    except LookupError as exc:
        return _refuse(rep, "E_ARGS", str(exc).strip("'\""))
    except Exception as exc:
        rep.update(code="E_INTERNAL", message=f"{type(exc).__name__}: {exc}", traceback=traceback.format_exc(limit=8))
        return rep
