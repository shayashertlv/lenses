"""Blender layer of WP5's photo-painted print (WP5_DESIGN.md §4, §6.1, §8 A6 / B5). Runs inside Blender 5.2.

    print_uv(parts, *, density='auto', layer='g3d_print', dry_run=False, replace=False, bake=None)
    attach_print(report, parts=None, *, gain=(1, 1, 1), transmission_map=False)
    detach_print(parts=None)
    remove_print_uv(parts=None)

print_uv
- runs the numpy layout core (``g3d.printuv.layout``) on the triangle arrays of an in-process WP3 export of the
  scene (``g3d.printuv.glbmesh``): the host bakes on the arrays of an export, so the tool and the bake see the same
  arrays and cannot disagree (q001: identical to the host layout of q001.glb chart for chart and texel for texel);
- writes the UV layer ``layer`` LAST and never changes the active or the active-render UV layer (a mesh with no UV
  layer at all gets it as its only layer, which Blender makes active; the receipt says so);
- the core lays out TRIANGLES; a Blender polygon whose triangles fall in different charts cannot hold that in its
  per-corner UVs (q001: 77 of 2306 brow quads, 22 of 2114 temple quads). Those polygons, and only those, are split
  into their own render triangles (the exporter's triangulation, kept in place so the exported triangle order does not
  change); positions and exported triangles stay identical, and every corner normal is written back as a custom
  normal so the shading stays within 0.1 deg (measured on q001: <= 0.033 deg; without it up to 1.5 deg);
- refuses: lens parts, non-mesh objects, a Mirror modifier, any modifier that changes topology (the evaluated mesh
  must have the base mesh's corners), shape keys, a mesh shared by several objects, an existing ``layer`` (unless
  ``replace``), more than 8 UV layers, an export the WP3 exporter refuses, a layout failing A1;
- ``bake`` (a bake directory, its report.json, layout.json or print_manifest.json) reproduces that bake's layout: the
  density, part order, mirror choice and atlas limit come from it and the result must have its atlas size and chart
  boxes (E_BAKE_MISMATCH); bakes predating recorded settings use mirror=True and atlas_max=4096;
- stores ``g3d_print_uv`` (JSON) on each object: layer, texcoord (= uv_layers.find(layer); the exporter re-reads it
  from the exported mesh), tx_per_mm, atlas, parts, geometry_sha, charts_sha, uv_digest, mesh_digest;
- keeps ``<mesh> | g3d pre-print`` (the mesh as it was, fake user; the earliest backup wins) for remove_print_uv;
- after writing, exports again and checks the result: positions per triangle corner and the triangle order identical
  to before, TEXCOORD_k equal to the layout within 1e-3 texel, normals within 0.1 deg; else everything rolls back.

attach_print
- ``report``: a bake directory, its report.json or print_manifest.json (``g3d_host.photoprint.manifest``), or that
  manifest as a dict. Refuses when print.png is missing, its sha256 or size disagrees, or any part's
  ``g3d_print_uv`` (geometry_sha, charts_sha, atlas, tx) or its current geometry (a fresh in-process export) does not
  match the bake: "print stale; re-project" (E_STALE);
- requires explicit ``texture_parts`` / ``no_texture_parts`` partitioning the bake's exact object names; defaults
  to ``texture_parts`` and refuses an excluded part before loading images or changing materials;
- packs print.png (sRGB, Linear, Extend) and wires Image <- UV Map ``layer`` -> Mix Multiply (Factor 1, B = gain) ->
  Base Color of the Principled BSDF feeding the active Material Output. Blender's exporter folds the Mix into
  baseColorFactor. The previous Base Color (its link and value) is kept in the material's ``g3d_print_prev``;
- a material also used by an object outside the attached parts is made single-user first (a copy for the attached
  parts, recorded in the object's ``g3d_print_material``); a material shared only by attached parts stays shared;
- no other BSDF input or node is touched: the tree is compared before / after (E_TOUCHED), and an export after the
  attach must differ from the one before only in baseColorTexture / baseColorFactor (E_POSTCHECK); else roll back;
- ``transmission_map=True`` is refused (alpha / transmission maps are not built; Astra's transmission stays as
  authored).

detach_print first verifies exact restoration and refuses a selected material shared with unselected objects, then
restores every attached material (nodes removed, Base Color link and value back, single-user copies swapped back and
removed when unused) and reports whether the tree equals its pre-attach state. remove_print_uv puts
back the pre-print mesh when the geometry is unchanged since print_uv (else it only removes the layer and the property
and says the split polygons and custom normals stay). It refuses while a print is attached (detach first).

Nothing raises for a scene problem: every function returns a receipt with "status" ("ok", "refused", "failed") and,
when refused, "code". Sides are never read from names (the layout core seeds strips by the x sign).
"""
from __future__ import annotations

import hashlib
import json
import math
import struct
import tempfile
import time
import traceback
from pathlib import Path

import bmesh
import bpy
import numpy as np

from . import export_glb as EX
from . import printuv as PU
from .printuv import glbmesh as GM

LAYER = "g3d_print"
PROP_UV = "g3d_print_uv"                 # object: the layout this mesh's print layer holds (JSON)
PROP_PREV = "g3d_print_prev"             # material: Base Color before attach_print (JSON)
PROP_SWAP = "g3d_print_material"         # object: {slot index: original material} for single-user copies (JSON)
PROP_BACKUP = "g3d_print_backup"         # object: name of the pre-print mesh copy
BACKUP_SUFFIX = " | g3d pre-print"
NODE_UV, NODE_TEX, NODE_MIX = "g3d print uv", "g3d print", "g3d print gain"
AUTO_TX_PER_MM = 10.0                    # 'auto' without photos (WP5_DESIGN §2 C's lossless value); bakes pass theirs
POS_TOL_MM = 1e-3                        # tool triangle corner vs the exported corner (float32 metres ~ 7e-6 mm)
UV_TOL_TEXEL = 1e-3                      # exported TEXCOORD vs the layout (float32 round trip ~ 1e-4 texel)
NORMAL_TOL_DEG = 0.1                     # corner normals after a polygon split (custom normals, int16 encoding)
UV_LIMIT = 8                             # Blender's UV layer limit
MANIFEST = "print_manifest.json"


class Refusal(Exception):
    def __init__(self, code: str, message: str, **extra):
        super().__init__(message)
        self.code, self.message, self.extra = code, message, extra


# ------------------------------------------------------------------------------------------------ small helpers
def _as_list(x) -> list:
    if x is None:
        return []
    if isinstance(x, (str, bpy.types.ID)):
        return [x]
    return list(x)


def _name(x) -> str:
    return x.name if isinstance(x, bpy.types.ID) else str(x)


def _role(ob) -> str | None:
    r = ob.get("partRole")
    if r is None and ob.data is not None:
        r = ob.data.get("partRole")
    return r if isinstance(r, str) else None


def _json_prop(idb, key: str) -> dict | None:
    v = idb.get(key)
    if v is None:
        return None
    try:
        return dict(json.loads(v)) if isinstance(v, str) else dict(v)
    except (TypeError, ValueError):
        return None


def _sha_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _png_size(path: Path) -> tuple[int, int]:
    with open(path, "rb") as f:
        head = f.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        raise Refusal("E_PRINT", f"{path} is not a PNG")
    return struct.unpack(">II", head[16:24])


def _mesh_digest(me) -> str:
    """Positions and corner topology of the stored mesh (what a geometry edit changes)."""
    co = np.zeros(len(me.vertices) * 3, np.float32)
    me.vertices.foreach_get("co", co)
    lv = np.zeros(len(me.loops), np.int32)
    me.loops.foreach_get("vertex_index", lv)
    ls = np.zeros(len(me.polygons), np.int32)
    me.polygons.foreach_get("loop_start", ls)
    h = hashlib.sha256()
    for a in (co, lv, ls):
        h.update(a.tobytes())
    return h.hexdigest()


def _uv_data(me, layer: str) -> np.ndarray:
    a = me.attributes[layer]
    buf = np.zeros(len(me.loops) * 2, np.float32)
    a.data.foreach_get("vector", buf)
    return buf.reshape(-1, 2)


def _corner_normals(me) -> np.ndarray:
    n = np.zeros(len(me.loops) * 3, np.float32)
    me.corner_normals.foreach_get("vector", n)
    return n.reshape(-1, 3)


def _loop_tris(me) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(corner loops (T, 3), polygon (T,), material index (T,)) of the mesh's render triangles."""
    me.calc_loop_triangles()
    nt = len(me.loop_triangles)
    lt = np.zeros(nt * 3, np.int64)
    me.loop_triangles.foreach_get("loops", lt)
    lp = np.zeros(nt, np.int64)
    me.loop_triangles.foreach_get("polygon_index", lp)
    mat = np.zeros(nt, np.int64)
    me.loop_triangles.foreach_get("material_index", mat)
    return lt.reshape(-1, 3), lp, mat


def _loop_verts(me) -> np.ndarray:
    lv = np.zeros(len(me.loops), np.int64)
    me.loops.foreach_get("vertex_index", lv)
    return lv


def _export_prims(tmp: Path, tag: str) -> list:
    """An in-process WP3 export of the live scene (its own invariance check included), read back."""
    path = tmp / f"{tag}.glb"
    try:
        EX.export_glb(path)
    except Exception as exc:                                     # ExportError or the invariance check
        raise Refusal("E_EXPORT", f"the scene does not export ({type(exc).__name__}: {exc}); print_uv lays out "
                                  f"what the exporter writes, so fix the export first")
    return GM.read_glb(path.read_bytes())


def _resolve(parts, scene) -> list:
    obs = []
    for p in _as_list(parts):
        ob = bpy.data.objects.get(_name(p))
        if ob is None or ob.name not in scene.objects:
            raise Refusal("E_ARGS", f"object {_name(p)!r} is not in the scene")
        if ob.type != "MESH":
            raise Refusal("E_NOT_MESH", f"{ob.name} is a {ob.type}; the print layer needs a mesh")
        if _role(ob) == "lens":
            raise Refusal("E_LENS", f"{ob.name} is a lens (partRole lens); lenses never get the print")
        obs.append(ob)
    if not obs:
        raise Refusal("E_ARGS", "no parts given")
    if len({o.name for o in obs}) != len(obs):
        raise Refusal("E_ARGS", "a part is listed twice")
    return obs


def _check_mesh(ob, depsgraph) -> None:
    for m in ob.modifiers:
        if m.type == "MIRROR" and (m.show_render or m.show_viewport):
            raise Refusal("E_MIRROR", f"{ob.name}: Mirror modifier {m.name!r}; apply it first (the print layer is "
                                      f"written on the base mesh, a mirrored half would repeat it)")
    me = ob.data
    if me.shape_keys is not None:
        raise Refusal("E_SHAPE_KEYS", f"{ob.name}: the mesh has shape keys; apply or remove them first")
    users = [o.name for o in bpy.data.objects if o.data == me]       # a fake user is not an object
    if len(users) > 1:
        raise Refusal("E_SHARED_MESH", f"{ob.name}: mesh {me.name!r} is used by {users}; make it single user first "
                                       f"(each object needs its own place in the atlas)")
    ev = ob.evaluated_get(depsgraph)
    em = ev.to_mesh()
    try:
        same = (len(em.vertices) == len(me.vertices) and len(em.loops) == len(me.loops)
                and len(em.polygons) == len(me.polygons))
        if same:
            same = np.array_equal(_loop_verts(em), _loop_verts(me))
    finally:
        ev.to_mesh_clear()
    if not same:
        raise Refusal("E_TOPOLOGY", f"{ob.name}: its modifiers change the topology (the evaluated corners differ "
                                    f"from the base mesh's); apply them first, the print layer is written on the "
                                    f"base mesh's corners")


def _world_tris_mm(ob, depsgraph, origin: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Evaluated render triangles of `ob` in asset mm: (corner positions (T, 3, 3), corner loops, polygon,
    material index)."""
    ev = ob.evaluated_get(depsgraph)
    em = ev.to_mesh()
    try:
        lt, lp, mat = _loop_tris(em)
        co = np.zeros(len(em.vertices) * 3, np.float32)
        em.vertices.foreach_get("co", co)
        lv = _loop_verts(em)
    finally:
        ev.to_mesh_clear()
    M = np.array(ob.matrix_world, np.float64)
    P = co.reshape(-1, 3).astype(np.float64) @ M[:3, :3].T + M[:3, 3] - origin
    return P[lv[lt]], lt, lp, mat


def _bake_layout(bake) -> dict:
    """Layout and identity settings of a bake; old bakes retain their default settings."""
    p = Path(bake)
    cands = [p / "layout.json"] if p.is_dir() else [p.parent / "layout.json"] if p.name != "layout.json" else [p]
    for c in cands:
        if c.is_file():
            lay = json.loads(c.read_text(encoding="utf-8"))
            records = []
            for name in ("print_manifest.json", "report.json"):
                identity = c.parent / name
                if identity.is_file():
                    record = json.loads(identity.read_text(encoding="utf-8"))
                    if name == "report.json":
                        record = record.get("print_uv") or {}
                    if "layout_options" in record:
                        records.append(PU.replay_options(record["layout_options"]))
            if records and any(r != records[0] for r in records):
                raise Refusal("E_BAKE_MISMATCH", "the bake report and manifest disagree on layout_options")
            lay["layout_options"] = records[0] if records else PU.replay_options()
            return lay
    raise Refusal("E_ARGS", f"bake {bake}: no layout.json found")


# ------------------------------------------------------------------------------------------------ polygon split
def _split_polygons(me, polys: list[int], tris_of: dict) -> dict:
    """Split the polygons `polys` of the stored mesh `me` into their render triangles `tris_of[p]` (vertex triples
    in loop-triangle order), each new triangle in its polygon's place, every corner attribute copied from the
    polygon's corner at the same vertex, every corner normal written back as a custom normal. Returns facts; raises
    Refusal when the result is not exactly the plan."""
    n_old = len(me.polygons)
    lv_old = _loop_verts(me)
    ls_old = np.zeros(n_old, np.int64)
    me.polygons.foreach_get("loop_start", ls_old)
    lt_old = np.zeros(n_old, np.int64)
    me.polygons.foreach_get("loop_total", lt_old)
    cn_old = _corner_normals(me).astype(np.float64)
    split = set(int(p) for p in polys)
    for p in split:                                 # a polygon visiting a vertex twice has no unique corner map
        vs = lv_old[ls_old[p]:ls_old[p] + lt_old[p]]
        if len(set(vs.tolist())) != len(vs):
            raise Refusal("E_SPLIT", f"{me.name}: polygon {p} visits a vertex twice; cannot split it")
    bm = bmesh.new()
    try:
        bm.from_mesh(me)
        bm.faces.ensure_lookup_table()
        bm.verts.ensure_lookup_table()
        layers = []
        for coll in ("uv", "float", "int", "color", "float_color", "float_vector", "bool", "string",
                     "float2", "int2", "quaternion"):
            lc = getattr(bm.loops.layers, coll, None)
            if lc is not None:
                layers += list(lc.values())
        key = bm.faces.layers.int.new("g3d print order")
        for f in bm.faces:
            f[key] = f.index * 64
        old = [bm.faces[p] for p in sorted(split)]          # before faces.new invalidates the lookup table
        verts = list(bm.verts)
        for p, f in zip(sorted(split), old):
            corner = {l.vert.index: l for l in f.loops}
            for s, tri in enumerate(tris_of[p]):
                nf = bm.faces.new([verts[int(v)] for v in tri], f)
                nf[key] = p * 64 + 1 + s
                for l in nf.loops:
                    src = corner[l.vert.index]
                    for ly in layers:
                        l[ly] = src[ly]
        bmesh.ops.delete(bm, geom=old, context="FACES_ONLY")
        bm.faces.sort(key=lambda f: f[key])
        bm.faces.layers.int.remove(key)
        bm.to_mesh(me)
    finally:
        bm.free()
    me.update()
    # the plan, polygon by polygon: (old polygon, vertex triple or None for an untouched polygon)
    plan = []
    for p in range(n_old):
        if p in split:
            plan += [(p, tuple(int(v) for v in t)) for t in tris_of[p]]
        else:
            plan.append((p, None))
    if len(me.polygons) != len(plan):
        raise Refusal("E_SPLIT", f"{me.name}: split gave {len(me.polygons)} polygons, planned {len(plan)}")
    lv = _loop_verts(me)
    ls = np.zeros(len(me.polygons), np.int64)
    me.polygons.foreach_get("loop_start", ls)
    tot = np.zeros(len(me.polygons), np.int64)
    me.polygons.foreach_get("loop_total", tot)
    old_loop = np.zeros(len(me.loops), np.int64)
    for i, (p, tri) in enumerate(plan):
        if tri is None:
            same = tot[i] == lt_old[p] and np.array_equal(lv[ls[i]:ls[i] + tot[i]],
                                                          lv_old[ls_old[p]:ls_old[p] + lt_old[p]])
            if not same:
                raise Refusal("E_SPLIT", f"{me.name}: polygon {p} changed although it was not split")
            old_loop[ls[i]:ls[i] + tot[i]] = np.arange(ls_old[p], ls_old[p] + lt_old[p])
        else:
            got = tuple(lv[ls[i]:ls[i] + tot[i]].tolist())
            if got != tri:
                raise Refusal("E_SPLIT", f"{me.name}: polygon {p} split into {got}, planned {tri}")
            where = {int(lv_old[ls_old[p] + j]): ls_old[p] + j for j in range(lt_old[p])}
            old_loop[ls[i]:ls[i] + 3] = [where[v] for v in tri]
    me.normals_split_custom_set([tuple(v) for v in cn_old[old_loop].tolist()])
    me.update()
    cn_new = _corner_normals(me).astype(np.float64)
    a, b = cn_new, cn_old[old_loop]
    cos = np.einsum("ij,ij->i", a, b) / np.maximum(np.linalg.norm(a, axis=1) * np.linalg.norm(b, axis=1), 1e-30)
    worst = float(np.degrees(np.arccos(np.clip(cos.min(initial=1.0), -1.0, 1.0))))
    if worst > NORMAL_TOL_DEG:
        raise Refusal("E_NORMALS", f"{me.name}: corner normals moved {worst:.3f} deg after the split "
                                   f"(> {NORMAL_TOL_DEG})")
    return {"polygons_split": len(split), "polygons_after": len(me.polygons), "custom_normals": True,
            "normal_change_max_deg": round(worst, 5)}


# ------------------------------------------------------------------------------------------------ print_uv
def print_uv(parts, *, density="auto", layer: str = LAYER, dry_run: bool = False, replace: bool = False,
             bake=None) -> dict:
    """See the module docstring. Returns the receipt."""
    t0 = time.perf_counter()
    rep = {"tool": "g3d.print_uv", "status": "failed", "layer": layer, "dry_run": bool(dry_run),
           "parts": [_name(p) for p in _as_list(parts)], "warnings": [], "objects": {}}
    swapped: list = []                                       # (object, original mesh, new mesh)
    tmpdir = tempfile.TemporaryDirectory(prefix="g3d-print-uv-", ignore_cleanup_errors=True)
    tmp = Path(tmpdir.name)
    try:
        if bpy.context.mode != "OBJECT":
            raise Refusal("E_MODE", "leave Edit/Sculpt mode first (no mode was changed)")
        scene = bpy.context.scene
        try:
            _, origin, _ = EX.check_convention(scene)
        except EX.ExportError as exc:
            raise Refusal("E_EXPORT", str(exc))
        origin = np.asarray(origin, np.float64)
        obs = _resolve(parts, scene)
        names = [o.name for o in obs]
        bake_lay = None
        if bake is not None:
            bake_lay = _bake_layout(bake)
            if list(bake_lay["parts"]) != names:
                raise Refusal("E_BAKE_MISMATCH", f"the bake laid out {bake_lay['parts']} in that order; got {names}")
            tx = float(bake_lay.get("tx_requested") or bake_lay["tx_per_mm"])
            rep["bake"] = str(bake)
        elif isinstance(density, (int, float)) and not isinstance(density, bool):
            tx = float(density)
        elif density == "auto":
            tx = AUTO_TX_PER_MM
            rep["warnings"].append(f"W_AUTO_DENSITY: no photos here, density 'auto' = {AUTO_TX_PER_MM} tx/mm; pass "
                                   f"the bake's density (bake= or density=) to match a photo bake")
        else:
            raise Refusal("E_ARGS", f"density must be a number of texels per mm or 'auto', got {density!r}")
        if not (math.isfinite(tx) and tx > 0):
            raise Refusal("E_ARGS", f"density {tx} must be positive")
        rep["tx_requested"] = tx
        dg = bpy.context.evaluated_depsgraph_get()
        for ob in obs:
            _check_mesh(ob, dg)
            if _attached_materials([ob]):
                raise Refusal("E_ATTACHED", f"{ob.name} still shows a print; detach_print before laying out UVs")
            if layer in ob.data.uv_layers and not replace:
                raise Refusal("E_LAYER_EXISTS", f"{ob.name} already has a {layer!r} UV layer; pass replace=True to "
                                                f"lay it out again (the print baked for it becomes stale)")
            n_other = len([u for u in ob.data.uv_layers if u.name != layer])
            if n_other >= UV_LIMIT:
                raise Refusal("E_UV_LIMIT", f"{ob.name} has {n_other} UV layers (Blender's limit is {UV_LIMIT})")

        # work on copies of the meshes, swapped in; the originals come back on any failure or a dry run
        for ob in obs:
            new = ob.data.copy()
            swapped.append((ob, ob.data, new))
            ob.data = new
            if layer in new.uv_layers:
                new.uv_layers.remove(new.uv_layers[layer])
        bpy.context.view_layer.update()
        dg = bpy.context.evaluated_depsgraph_get()

        t = time.perf_counter()
        prims = _export_prims(tmp, "before")
        V, F, part, gnames = GM.mesh_arrays(prims, strip_suffix=False)    # the exporter writes object names
        missing = [n for n in names if n not in gnames]
        if missing:
            raise Refusal("E_NOT_EXPORTED", f"not in the export (hidden from render or excluded): {missing}")
        rep["timings"] = {"export_s": round(time.perf_counter() - t, 2)}
        t = time.perf_counter()
        try:
            options = PU.replay_options(bake_lay.get("layout_options") if bake_lay else None)
            lay = PU.layout(V, F, part, gnames, names, tx, **options)
        except ValueError as exc:                               # the core cannot lay these parts out
            raise Refusal("E_LAYOUT", f"the print layout core refused: {exc}")
        rep["timings"]["layout_s"] = round(time.perf_counter() - t, 2)
        a1 = lay.report
        rep["a1"] = {k: a1.get(k) for k in ("pass", "criteria", "overlap_texels", "flipped_faces",
                                            "zero_area_faces", "anisotropy_p50_p95_p99", "density_p1_share")}
        if not a1.get("pass"):
            raise Refusal("E_LAYOUT", f"the print layout fails A1: {a1.get('criteria')}")
        geo = GM.geometry_sha(V, F, part, gnames, names)
        csha = GM.charts_sha(lay.charts, lay.width, lay.height)
        rep.update(atlas=[lay.width, lay.height], tx_per_mm=lay.tx_per_mm, partners=lay.partners,
                   charts=len(lay.charts), geometry_sha=geo, charts_sha=csha,
                   layout_options=lay.layout_options)
        if bake_lay is not None:
            want = GM.charts_sha(bake_lay["charts"], bake_lay["width"], bake_lay["height"])
            if csha != want:
                diff = next((i for i, (a, b) in enumerate(zip(lay.charts, bake_lay["charts"]))
                             if [a[k] for k in GM.CHART_KEYS] != [b[k] for k in GM.CHART_KEYS]), None)
                raise Refusal("E_BAKE_MISMATCH", f"the layout does not reproduce the bake (atlas {lay.width}x"
                                                 f"{lay.height} vs {bake_lay['width']}x{bake_lay['height']}, "
                                                 f"first differing chart {diff}); re-project",
                              atlas=[lay.width, lay.height])
            rep["bake_match"] = {"charts_sha": csha, "atlas": [lay.width, lay.height], "charts": len(lay.charts)}

        frow = lay.face_index(len(F))
        wh = np.array([lay.width, lay.height], np.float64)
        plans = {}
        for ob in obs:
            gf = GM.part_faces(part, gnames, ob.name)
            Pw, lt, lp, mat = _world_tris_mm(ob, dg, origin)
            if len(lt) != len(gf):
                raise Refusal("E_CORRESPONDENCE", f"{ob.name}: {len(lt)} render triangles, {len(gf)} exported")
            order = np.argsort(mat, kind="stable")             # the exporter buckets triangles by material slot
            err = float(np.abs(Pw[order] - V[F[gf]]).max())
            if err > POS_TOL_MM:
                raise Refusal("E_CORRESPONDENCE", f"{ob.name}: render triangles and exported triangles differ by "
                                                  f"{err:.4f} mm (exporter order assumption broken)")
            uv_t = np.empty((len(lt), 3, 2))
            uv_t[order] = lay.uv[frow[gf]]                      # texel coordinates per Blender render triangle
            loops = lt.ravel()
            uvf = uv_t.reshape(-1, 2)
            _, first, inv = np.unique(loops, return_index=True, return_inverse=True)
            bad = np.abs(uvf - uvf[first][inv]).max(1) > 1e-6
            polys = sorted(set(lp[np.nonzero(bad)[0] // 3].tolist()))
            lv = _loop_verts(ob.data)
            tris_of = {p: [tuple(lv[lt[k]].tolist()) for k in np.nonzero(lp == p)[0]] for p in polys}
            plans[ob.name] = {"gf": gf, "order": order, "polys": polys, "tris_of": tris_of, "pos_err_mm": err}
            rep["objects"][ob.name] = {"triangles": int(len(lt)), "polygons": len(ob.data.polygons),
                                       "polygons_to_split": len(polys), "pos_err_mm": err}
        if dry_run:
            rep["status"] = "ok"
            rep["note"] = "dry run: nothing written"
            return rep

        # ---- write
        for ob, orig, new in swapped:
            me = new
            pl = plans[ob.name]
            if pl["polys"]:
                rep["objects"][ob.name].update(_split_polygons(me, pl["polys"], pl["tris_of"]))
            lt, lp, mat = _loop_tris(me)
            order = np.argsort(mat, kind="stable")
            uv_t = np.empty((len(lt), 3, 2))
            uv_t[order] = lay.uv[frow[pl["gf"]]]
            L = np.full((len(me.loops), 2), np.nan)
            L[lt.ravel()] = uv_t.reshape(-1, 2)
            loops = lt.ravel()
            if np.isnan(L).any() or np.abs(L[loops] - uv_t.reshape(-1, 2)).max() > 1e-6:
                raise Refusal("E_SPLIT", f"{ob.name}: a corner still needs two print uvs after the split")
            act = me.uv_layers.active_index
            render = next((u.name for u in me.uv_layers if u.active_render), None)
            uvl = me.uv_layers.new(name=layer, do_init=False)
            if uvl is None or uvl.name != layer:
                raise Refusal("E_UV_LIMIT", f"{ob.name}: Blender refused a new UV layer {layer!r}")
            if render is not None:
                me.uv_layers[render].active_render = True
                me.uv_layers.active_index = act
            else:
                rep["warnings"].append(f"W_ONLY_UV: {ob.name} had no UV layer; {layer!r} is its only (so active) one")
            blender_uv = np.stack([L[:, 0] / wh[0], 1.0 - L[:, 1] / wh[1]], 1).astype(np.float32)
            me.attributes[layer].data.foreach_set("vector", blender_uv.ravel())
            me.update()
            texcoord = me.uv_layers.find(layer)
            if texcoord != len(me.uv_layers) - 1:
                raise Refusal("E_LAYER_ORDER", f"{ob.name}: {layer!r} landed at index {texcoord}, not last")
            rep["objects"][ob.name].update(texcoord=texcoord, uv_layers=[u.name for u in me.uv_layers],
                                           active_render=next(u.name for u in me.uv_layers if u.active_render))
        bpy.context.view_layer.update()

        # ---- post-check: export again
        t = time.perf_counter()
        after = {(p.node, p.index): p for p in _export_prims(tmp, "after")}
        before = {(p.node, p.index): p for p in prims}
        rep["timings"]["postcheck_export_s"] = round(time.perf_counter() - t, 2)
        post = {}
        for ob, orig, new in swapped:
            k = new.uv_layers.find(layer)
            keys = [key for key in before if key[0] == ob.name]
            if (not keys or set(keys) != {key for key in after if key[0] == ob.name}
                    or any(k not in after[key].texcoords for key in keys)):
                raise Refusal("E_POSTCHECK", f"{ob.name}: TEXCOORD_{k} missing from the export after print_uv")
            pa, pb = [after[key] for key in keys], [before[key] for key in keys]
            same_pos = all(np.array_equal(a.positions_mm[a.triangles], b.positions_mm[b.triangles])
                           for a, b in zip(pa, pb))
            uvt = np.concatenate([GM.uv_texels(p, k, lay.width, lay.height) for p in pa])
            gf = plans[ob.name]["gf"]
            uv_err = float(np.abs(uvt - lay.uv[frow[gf]]).max()) if len(uvt) == len(gf) else math.inf
            if not same_pos:
                raise Refusal("E_POSTCHECK", f"{ob.name}: export after print_uv changed primitive geometry")
            na = np.concatenate([p.normals[p.triangles] for p in pa]).astype(np.float64)
            nb = np.concatenate([p.normals[p.triangles] for p in pb]).astype(np.float64)
            cos = np.einsum("tcj,tcj->tc", na, nb) / np.maximum(np.linalg.norm(na, axis=2) * np.linalg.norm(nb, axis=2),
                                                                 1e-30)
            n_err = float(np.degrees(np.arccos(np.clip(cos.min(), -1, 1))))
            post[ob.name] = {"texcoord": k, "positions_identical_in_order": bool(same_pos),
                             "uv_max_err_texel": uv_err, "normal_max_change_deg": round(n_err, 5)}
            if not same_pos or uv_err > UV_TOL_TEXEL or n_err > NORMAL_TOL_DEG:
                raise Refusal("E_POSTCHECK", f"{ob.name}: export after print_uv differs: {post[ob.name]}")
        rep["postcheck"] = post

        # ---- commit: properties, backups (the original datablock is the backup, the earliest wins)
        for ob, orig, new in swapped:
            name = orig.name
            prev_backup = ob.get(PROP_BACKUP)
            if prev_backup and bpy.data.meshes.get(prev_backup) is not None:
                orig.name = name + " (g3d replaced)"
                new.name = name
                if orig.users == 0:
                    bpy.data.meshes.remove(orig)
                backup = prev_backup
            else:
                orig.name = name + BACKUP_SUFFIX
                orig.use_fake_user = True
                new.name = name
                backup = orig.name
                ob[PROP_BACKUP] = backup
            me = ob.data
            info = {"tool": "g3d.print_uv", "layer": layer, "texcoord": me.uv_layers.find(layer),
                    "tx_per_mm": lay.tx_per_mm, "tx_requested": tx, "atlas": [lay.width, lay.height],
                    "parts": names, "geometry_sha": geo, "charts_sha": csha, "layout_options": lay.layout_options,
                    "uv_digest": _sha_bytes(_uv_data(me, layer).tobytes()), "mesh_digest": _mesh_digest(me),
                    "polygons_split": rep["objects"][ob.name].get("polygons_split", 0)}
            ob[PROP_UV] = json.dumps(info, sort_keys=True)
            rep["objects"][ob.name]["backup"] = backup
        swapped = []
        rep["status"] = "ok"
        return rep
    except Refusal as r:
        rep.update(status="refused", code=r.code, message=r.message, **r.extra)
        return rep
    except Exception as exc:
        rep.update(status="failed", error=f"{type(exc).__name__}: {exc}", traceback=traceback.format_exc(limit=8))
        return rep
    finally:
        for ob, orig, new in swapped:                         # roll back (failure or dry run)
            ob.data = orig
            if new.users == 0:
                bpy.data.meshes.remove(new)
        if swapped:
            bpy.context.view_layer.update()
        tmpdir.cleanup()
        rep.setdefault("timings", {})["total_s"] = round(time.perf_counter() - t0, 2)


# ------------------------------------------------------------------------------------------------ attach / detach
def load_manifest(report) -> dict:
    """The bake's print manifest: a dict, a print_manifest.json, a bake directory or its report.json."""
    if isinstance(report, dict):
        man = dict(report)
        if "print_png" not in man and "out" in man:                 # a bake report dict with its directory
            return load_manifest(man["out"])
        return man
    p = Path(report)
    cands = [p / MANIFEST] if p.is_dir() else [p] if p.name == MANIFEST else [p.parent / MANIFEST]
    for c in cands:
        if c.is_file():
            man = json.loads(c.read_text(encoding="utf-8"))
            man.setdefault("manifest_path", str(c))
            return man
    raise Refusal("E_ARGS", f"no {MANIFEST} for {report} (write it with g3d_host.photoprint.manifest)")


MANIFEST_KEYS = ("print_png", "print_sha256", "width", "height", "tx_per_mm", "parts", "layer", "geometry_sha",
                 "charts_sha", "texture_parts", "no_texture_parts")


def _texture_parts(man: dict) -> list[str]:
    """Explicit bake eligibility; older manifests must be regenerated, never guessed."""
    lists = [man.get(k) for k in ("parts", "texture_parts", "no_texture_parts")]
    if any(not isinstance(v, list) or any(not isinstance(n, str) or not n for n in v)
           or len(set(v)) != len(v) for v in lists):
        raise Refusal("E_ARGS", "parts, texture_parts and no_texture_parts must be lists of unique exact names")
    all_parts, textured, excluded = map(set, lists)
    if textured & excluded or textured | excluded != all_parts:
        raise Refusal("E_ARGS", "texture_parts and no_texture_parts must partition the bake's parts")
    return list(man["texture_parts"])


def _principled(mat):
    tree = mat.node_tree
    if tree is None:
        return None, None
    outs = [n for n in tree.nodes if n.bl_idname == "ShaderNodeOutputMaterial"]
    out = next((n for n in outs if n.is_active_output), outs[0] if outs else None)
    if out is None or not out.inputs["Surface"].is_linked:
        return out, None
    src = out.inputs["Surface"].links[0].from_node
    return out, (src if src.bl_idname == "ShaderNodeBsdfPrincipled" else None)


def _tree_state(tree, skip_nodes=(), skip_links=()) -> tuple:
    """Every node (settings, input values) and link of the tree except `skip_nodes` and the links in `skip_links`
    (tuples from_node, from_socket, to_node, to_socket)."""
    nodes = tuple(sorted((n.name, n.bl_idname, EX._rna_simple(n),
                          tuple(EX._plain(getattr(s, "default_value", None)) for s in n.inputs))
                         for n in tree.nodes if n.name not in skip_nodes))
    links = tuple(sorted(k for k in ((l.from_node.name, l.from_socket.identifier, l.to_node.name,
                                      l.to_socket.identifier) for l in tree.links)
                         if k not in skip_links and k[0] not in skip_nodes and k[2] not in skip_nodes))
    return nodes, links


def _material_users(mat) -> list:
    return [o for o in bpy.data.objects if any(s.material == mat for s in o.material_slots)]


def _print_image(man: dict):
    path = Path(man["print_png"])
    name = f"g3d print {man['print_sha256'][:12]}"
    img = bpy.data.images.get(name)
    packed = img is not None and img.packed_file is not None
    if packed and _sha_bytes(bytes(img.packed_file.data)) == man["print_sha256"]:
        return img, False
    img = bpy.data.images.load(str(path), check_existing=False)
    img.name = name
    img.pack()
    img.colorspace_settings.name = "sRGB"
    if _sha_bytes(bytes(img.packed_file.data)) != man["print_sha256"]:
        bpy.data.images.remove(img)
        raise Refusal("E_PRINT", "the packed print.png differs from the bake's sha256")
    return img, True


def _material_json_diff(a: dict, b: dict) -> list[str]:
    """Keys that differ between two glTF material dicts, ignoring baseColorTexture / baseColorFactor."""
    def strip(m):
        m = json.loads(json.dumps(m))
        pbr = m.get("pbrMetallicRoughness") or {}
        pbr.pop("baseColorTexture", None)
        pbr.pop("baseColorFactor", None)
        if "pbrMetallicRoughness" in m and not pbr:
            m.pop("pbrMetallicRoughness")
        return m
    sa, sb = strip(a), strip(b)
    return sorted(k for k in set(sa) | set(sb) if sa.get(k) != sb.get(k))


def _export_doc(tmp: Path, tag: str) -> tuple[dict, list]:
    """(the export's JSON with every material's texture references resolved to image bytes, its primitives)."""
    path = tmp / f"{tag}.glb"
    try:
        EX.export_glb(path)
    except Exception as exc:
        raise Refusal("E_EXPORT", f"the scene does not export ({type(exc).__name__}: {exc})")
    data = path.read_bytes()
    doc, binc = GM.chunks(data)
    doc["materials"] = [_resolve_textures(doc, binc, m) for m in doc.get("materials", [])]
    return doc, GM.read_glb(data)


def _resolve_textures(doc: dict, binc: bytes, mat: dict) -> dict:
    """The material with every texture reference ({"index": i, ...} under a key ending in 'Texture') replaced by the
    sha256 of the image bytes and the sampler: texture indices shift when another material gains a texture."""
    def image_sha(ti: int) -> str:
        tex = doc["textures"][ti]
        src = tex.get("source")
        if src is None:
            src = next(iter((tex.get("extensions") or {}).values()), {}).get("source")
        img = doc["images"][src]
        if "bufferView" in img:
            v = doc["bufferViews"][img["bufferView"]]
            off = int(v.get("byteOffset", 0))
            return _sha_bytes(binc[off:off + int(v["byteLength"])])
        return "uri:" + str(img.get("uri"))

    def walk(x, key=""):
        if isinstance(x, dict):
            if key.endswith("Texture") and "index" in x:
                y = {k: walk(v, k) for k, v in x.items() if k != "index"}
                tex = doc["textures"][x["index"]]
                y["image_sha256"] = image_sha(x["index"])
                y["sampler"] = doc.get("samplers", [])[tex["sampler"]] if "sampler" in tex else None
                return y
            return {k: walk(v, k) for k, v in x.items()}
        if isinstance(x, list):
            return [walk(v, key) for v in x]
        return x
    return walk(json.loads(json.dumps(mat)))


def attach_print(report, parts=None, *, gain=(1.0, 1.0, 1.0), transmission_map: bool = False) -> dict:
    """See the module docstring. Returns the receipt."""
    t0 = time.perf_counter()
    rep = {"tool": "g3d.attach_print", "status": "failed", "objects": {}, "materials": {}, "warnings": []}
    added: list = []            # (material, [node names], prev link or None, prev default)
    swaps: list = []            # (object, slot index, original material, copy)
    image, image_new = None, False
    tmpdir = tempfile.TemporaryDirectory(prefix="g3d-attach-")
    tmp = Path(tmpdir.name)
    try:
        if bpy.context.mode != "OBJECT":
            raise Refusal("E_MODE", "leave Edit/Sculpt mode first")
        if transmission_map:
            raise Refusal("E_ARGS", "transmission_map=True is not built: alpha / transmission maps are not made; "
                                    "Astra's transmission stays as authored")
        g = [float(x) for x in gain]
        if len(g) != 3 or not all(math.isfinite(x) and x > 0 for x in g):
            raise Refusal("E_ARGS", f"gain must be three positive numbers, got {gain!r}")
        man = load_manifest(report)
        miss = [k for k in MANIFEST_KEYS if k not in man]
        if miss:
            raise Refusal("E_ARGS", f"the print manifest lacks {miss}")
        eligible = _texture_parts(man)
        names = list(eligible) if parts is None else [_name(p) for p in _as_list(parts)]
        excluded = [n for n in names if n not in eligible]
        if excluded:
            raise Refusal("E_NO_TEXTURE", f"the bake does not permit a print on {excluded}; "
                                         f"texture_parts are {eligible}", no_texture_parts=excluded)
        if not names:
            raise Refusal("E_NO_TEXTURE", "the bake has no selected texture_parts")
        rep["manifest"] = {k: man.get(k) for k in ("manifest_path", "print_png", "print_sha256", "geometry_sha",
                                                   "charts_sha", "label", "texture_parts", "no_texture_parts")}
        png = Path(man["print_png"])
        if not png.is_file():
            raise Refusal("E_PRINT", f"print.png not found: {png}")
        if _sha_bytes(png.read_bytes()) != man["print_sha256"]:
            raise Refusal("E_PRINT", f"{png} does not match the bake's sha256 (a different print.png)")
        if tuple(_png_size(png)) != (int(man["width"]), int(man["height"])):
            raise Refusal("E_PRINT", f"{png} is {_png_size(png)}, the bake's atlas is {man['width']}x{man['height']}")
        layer = man["layer"]
        scene = bpy.context.scene
        obs = _resolve(names, scene)
        for ob in obs:
            info = _json_prop(ob, PROP_UV)
            if info is None:
                raise Refusal("E_STALE", f"{ob.name} has no {PROP_UV}: run g3d.print_uv first")
            for k in ("geometry_sha", "charts_sha"):
                if info.get(k) != man[k]:
                    raise Refusal("E_STALE", f"print stale; re-project: {ob.name}'s {k} {str(info.get(k))[:12]} is not "
                                             f"the bake's {str(man[k])[:12]}")
            if list(info.get("atlas", [])) != [int(man["width"]), int(man["height"])] or \
                    abs(float(info.get("tx_per_mm", 0)) - float(man["tx_per_mm"])) > 1e-9:
                raise Refusal("E_STALE", f"print stale; re-project: {ob.name}'s layout atlas / density differ "
                                         f"from the bake")
            if layer not in ob.data.uv_layers or info.get("layer") != layer:
                raise Refusal("E_STALE", f"{ob.name} has no {layer!r} UV layer")
            if _sha_bytes(_uv_data(ob.data, layer).tobytes()) != info.get("uv_digest"):
                raise Refusal("E_STALE", f"print stale; re-project: {ob.name}'s {layer!r} UVs were edited after "
                                         f"print_uv")
        # the geometry as it is now (an edit after print_uv makes the stored sha a lie)
        doc0, prims0 = _export_doc(tmp, "before")
        V, F, part, gnames = GM.mesh_arrays(prims0, strip_suffix=False)
        try:
            now = GM.geometry_sha(V, F, part, gnames, list(man["parts"]))
        except ValueError as exc:                                   # a bake part is gone from the export
            raise Refusal("E_STALE", f"print stale; re-project: {exc}")
        if now != man["geometry_sha"]:
            raise Refusal("E_STALE", "print stale; re-project: the parts' geometry changed since the bake")
        rep["geometry_sha_now"] = now

        attach_set = {o.name for o in obs}
        image, image_new = _print_image(man)
        rep["image"] = {"name": image.name, "size": list(image.size), "new": image_new,
                        "colorspace": image.colorspace_settings.name}
        done: dict = {}
        for ob in obs:
            used = sorted(set(int(p.material_index) for p in ob.data.polygons))
            for si in used:
                if si >= len(ob.material_slots) or ob.material_slots[si].material is None:
                    raise Refusal("E_NO_MATERIAL", f"{ob.name}: material slot {si} is empty")
                mat = ob.material_slots[si].material
                outside = sorted(o.name for o in _material_users(mat) if o.name not in attach_set)
                if outside:
                    cp = done.get(("copy", mat.name))
                    if cp is None:
                        cp = mat.copy()
                        cp.name = mat.name + " | g3d print"
                        done[("copy", mat.name)] = cp
                    slot = ob.material_slots[si]
                    swaps.append((ob, si, mat, cp))
                    slot.material = cp
                    rep["warnings"].append(f"W_SINGLE_USER: {mat.name!r} is also used by {outside}; {ob.name} gets "
                                           f"the copy {cp.name!r}")
                    mat = cp
                if mat.name in done:
                    continue
                prev = _json_prop(mat, PROP_PREV)
                if prev is not None:
                    raise Refusal("E_ATTACHED", f"material {mat.name!r} already carries a print; detach_print first")
                out, bsdf = _principled(mat)
                if bsdf is None:
                    raise Refusal("E_NO_PRINCIPLED", f"material {mat.name!r}: no Principled BSDF feeds the active "
                                                     f"Material Output directly")
                tree = mat.node_tree
                base = bsdf.inputs["Base Color"]
                link = None
                if base.is_linked:
                    lk = base.links[0]
                    link = [lk.from_node.name, lk.from_socket.identifier]
                prev_default = [float(x) for x in base.default_value]
                before = _tree_state(tree, skip_links={(link[0], link[1], bsdf.name, base.identifier)} if link else ())
                full_before = _tree_state(tree)                      # what detach_print must give back
                n_uv = tree.nodes.new("ShaderNodeUVMap")
                n_uv.name = n_uv.label = NODE_UV
                n_uv.uv_map = layer
                n_tex = tree.nodes.new("ShaderNodeTexImage")
                n_tex.name = n_tex.label = NODE_TEX
                n_tex.image = image
                n_tex.interpolation = "Linear"
                n_tex.extension = "EXTEND"
                n_mix = tree.nodes.new("ShaderNodeMix")
                n_mix.name = n_mix.label = NODE_MIX
                n_mix.data_type = "RGBA"
                n_mix.blend_type = "MULTIPLY"
                n_mix.clamp_result = False
                n_mix.clamp_factor = True
                ins = {s.identifier: s for s in n_mix.inputs}
                ins["Factor_Float"].default_value = 1.0
                ins["B_Color"].default_value = (g[0], g[1], g[2], 1.0)
                outs = {s.identifier: s for s in n_mix.outputs}
                x0, y0 = bsdf.location
                n_mix.location = (x0 - 220, y0)
                n_tex.location = (x0 - 520, y0)
                n_uv.location = (x0 - 720, y0)
                added.append((mat, [NODE_UV, NODE_TEX, NODE_MIX], link, prev_default, bsdf.name))
                tree.links.new(n_uv.outputs["UV"], n_tex.inputs["Vector"])
                tree.links.new(n_tex.outputs["Color"], ins["A_Color"])
                tree.links.new(outs["Result_Color"], base)           # replaces the previous Base Color link
                after = _tree_state(tree, skip_nodes={NODE_UV, NODE_TEX, NODE_MIX})
                if after != before:
                    raise Refusal("E_TOUCHED", f"material {mat.name!r}: attaching changed something besides Base Color")
                mat[PROP_PREV] = json.dumps({"base_color_link": link, "base_color_default": prev_default,
                                             "bsdf": bsdf.name, "nodes": [NODE_UV, NODE_TEX, NODE_MIX],
                                             "image": image.name, "layer": layer, "gain": g,
                                             "geometry_sha": man["geometry_sha"],
                                             "tree_digest": _sha_bytes(repr(full_before).encode())}, sort_keys=True)
                done[mat.name] = True
                rep["materials"][mat.name] = {"previous_base_color": link or prev_default, "gain": g}
            rep["objects"][ob.name] = {"materials": [ob.material_slots[si].material.name for si in used]}
        for ob, si, orig, cp in swaps:
            sw = _json_prop(ob, PROP_SWAP) or {}
            sw[str(si)] = orig.name
            ob[PROP_SWAP] = json.dumps(sw, sort_keys=True)

        # ---- post-check: the export differs from the one before only in the base colour
        doc1, prims1 = _export_doc(tmp, "after")
        post = _postcheck_attach(doc0, doc1, prims0, prims1, attach_set, layer, g, swapped=bool(swaps))
        rep["postcheck"] = post
        if not post["pass"]:
            raise Refusal("E_POSTCHECK", f"the export after attach differs beyond the base colour: {post['failures']}")
        added, swaps, image_new = [], [], False
        rep["status"] = "ok"
        return rep
    except Refusal as r:
        rep.update(status="refused", code=r.code, message=r.message, **r.extra)
        return rep
    except Exception as exc:
        rep.update(status="failed", error=f"{type(exc).__name__}: {exc}", traceback=traceback.format_exc(limit=8))
        return rep
    finally:
        for mat, nodes, link, prev_default, bsdf_name in reversed(added):        # roll back
            _restore_material(mat, nodes, link, prev_default, bsdf_name)
        for ob, si, orig, cp in reversed(swaps):
            ob.material_slots[si].material = orig
            sw = _json_prop(ob, PROP_SWAP) or {}
            sw.pop(str(si), None)
            if sw:
                ob[PROP_SWAP] = json.dumps(sw, sort_keys=True)
            elif PROP_SWAP in ob:
                del ob[PROP_SWAP]
            if cp.users == 0:
                bpy.data.materials.remove(cp)
        if image_new and image is not None and image.users == 0:
            bpy.data.images.remove(image)
        tmpdir.cleanup()
        rep["seconds"] = round(time.perf_counter() - t0, 2)


def _postcheck_attach(doc0: dict, doc1: dict, prims0: list, prims1: list, attach_set: set, layer: str, gain,
                      swapped: bool) -> dict:
    """The export after attach vs before: geometry identical (every accessor of every primitive), node extras
    identical, every material identical except baseColorTexture / baseColorFactor of the attached parts' materials,
    which must now sample the print through TEXCOORD_k (k = the node's g3d_print_uv texcoord)."""
    fails = []
    p0 = {(p.node, p.index): p for p in prims0}
    p1 = {(p.node, p.index): p for p in prims1}
    if set(p0) != set(p1):
        fails.append("primitives differ")
    for key in sorted(set(p0) & set(p1)):
        a, b = p0[key], p1[key]
        if a.attributes != b.attributes or not np.array_equal(a.positions_mm, b.positions_mm) or \
                not np.array_equal(a.triangles, b.triangles) or \
                any(not np.array_equal(a.texcoords[k], b.texcoords.get(k)) for k in a.texcoords) or \
                (a.normals is not None and not np.array_equal(a.normals, b.normals)):
            fails.append(f"geometry of {key[0]} changed")
    n0 = {n.get("name"): n for n in doc0.get("nodes", [])}
    n1 = {n.get("name"): n for n in doc1.get("nodes", [])}
    for nm in n0:
        if n0[nm].get("extras") != (n1.get(nm) or {}).get("extras"):
            fails.append(f"node extras of {nm} changed")
    mats0, mats1 = doc0.get("materials", []), doc1.get("materials", [])
    rows = {}
    for key in sorted(set(p0) & set(p1)):
        a, b = p0[key], p1[key]
        if a.material is None or b.material is None:
            continue
        m0, m1 = mats0[a.material], mats1[b.material]
        diff = _material_json_diff(m0, m1)
        names_differ = m0.get("name") != m1.get("name")
        if diff and not (names_differ and swapped and diff == ["name"]):
            fails.append(f"material of {key[0]} differs in {diff}")
        if key[0] in attach_set:
            extras = json.loads((n1.get(key[0]) or {}).get("extras", {}).get("g3d_print_uv", "{}"))
            k = extras.get("texcoord")
            info = (m1.get("pbrMetallicRoughness") or {}).get("baseColorTexture")
            fac = (m1.get("pbrMetallicRoughness") or {}).get("baseColorFactor", [1.0, 1.0, 1.0, 1.0])
            ok = (info is not None and k is not None and info.get("texCoord", 0) == k
                  and f"TEXCOORD_{k}" in b.attributes
                  and np.allclose(fac[:3], gain, atol=1e-6))
            rows[key[0]] = {"texcoord": k, "baseColorTexture": info, "baseColorFactor": fac, "pass": bool(ok)}
            if not ok:
                fails.append(f"{key[0]}: base colour does not sample the print through TEXCOORD_{k} ({info}, {fac})")
        elif m0 != m1:
            fails.append(f"material of non-attached {key[0]} changed")
    return {"pass": not fails, "failures": fails[:20], "attached": rows}


def _restore_material(mat, nodes, link, prev_default, bsdf_name) -> None:
    tree = mat.node_tree
    for nm in nodes:
        n = tree.nodes.get(nm)
        if n is not None:
            tree.nodes.remove(n)
    bsdf = tree.nodes.get(bsdf_name)
    if bsdf is not None:
        base = bsdf.inputs["Base Color"]
        for lk in list(base.links):
            tree.links.remove(lk)
        if link:
            src = tree.nodes.get(link[0])
            sock = next((s for s in src.outputs if s.identifier == link[1]), None) if src is not None else None
            if sock is not None:
                tree.links.new(sock, base)
        base.default_value = prev_default
    if PROP_PREV in mat:
        del mat[PROP_PREV]


def _attached_materials(obs) -> list:
    mats = []
    for ob in obs:
        for s in ob.material_slots:
            if s.material is not None and s.material.get(PROP_PREV) is not None and s.material not in mats:
                mats.append(s.material)
    return mats


def detach_print(parts=None) -> dict:
    """Undo attach_print on `parts` (default: every object whose material carries a print)."""
    rep = {"tool": "g3d.detach_print", "status": "failed", "materials": {}, "objects": {}}
    try:
        scene = bpy.context.scene
        if parts is None:
            obs = [o for o in scene.objects if o.type == "MESH" and (_attached_materials([o]) or o.get(PROP_SWAP))]
        else:
            obs = _resolve(parts, scene)
        selected = {o.name for o in obs}
        mats = _attached_materials(obs)
        # A material can be shared by several printed parts. Restoring it for a
        # subset would silently remove another part's print; refuse before edits.
        for mat in mats:
            outside = sorted(o.name for o in _material_users(mat) if o.name not in selected)
            if outside:
                raise Refusal("E_SHARED_PRINT", f"material {mat.name!r} is also attached to {outside}; "
                                              "detach all of its owners together")
            prev = _json_prop(mat, PROP_PREV)
            if not prev or not prev.get("tree_digest"):
                raise Refusal("E_STALE", f"material {mat.name!r} has no valid saved pre-print state")
            # Verify the restoration on a disposable copy before changing any
            # live material. Edited or missing original nodes fail closed.
            probe = mat.copy()
            try:
                _restore_material(probe, prev.get("nodes", [NODE_UV, NODE_TEX, NODE_MIX]),
                                  prev.get("base_color_link"), prev.get("base_color_default", [0.8, 0.8, 0.8, 1.0]),
                                  prev.get("bsdf", "Principled BSDF"))
                if _sha_bytes(repr(_tree_state(probe.node_tree)).encode()) != prev["tree_digest"]:
                    raise Refusal("E_STALE", f"material {mat.name!r} changed after attach; "
                                             "cannot restore its saved pre-print state exactly")
            finally:
                bpy.data.materials.remove(probe)
        for ob in obs:
            for si, orig_name in (_json_prop(ob, PROP_SWAP) or {}).items():
                if not str(si).isdigit() or int(si) >= len(ob.material_slots) or bpy.data.materials.get(orig_name) is None:
                    raise Refusal("E_STALE", f"{ob.name}: original material for slot {si} is unavailable")
        images = set()
        for mat in mats:
            prev = _json_prop(mat, PROP_PREV) or {}
            n_tex = mat.node_tree.nodes.get(NODE_TEX)
            if n_tex is not None and n_tex.image is not None:
                images.add(n_tex.image.name)
            _restore_material(mat, prev.get("nodes", [NODE_UV, NODE_TEX, NODE_MIX]), prev.get("base_color_link"),
                              prev.get("base_color_default", [0.8, 0.8, 0.8, 1.0]), prev.get("bsdf", "Principled BSDF"))
            state = _tree_state(mat.node_tree)
            rep["materials"][mat.name] = {"tree_restored": _sha_bytes(repr(state).encode()) == prev.get("tree_digest")}
        for ob in obs:
            sw = _json_prop(ob, PROP_SWAP) or {}
            for si, orig_name in sorted(sw.items()):
                orig = bpy.data.materials.get(orig_name)
                cp = ob.material_slots[int(si)].material
                if orig is not None:
                    ob.material_slots[int(si)].material = orig
                    if cp is not None and cp.users == 0:
                        bpy.data.materials.remove(cp)
            if PROP_SWAP in ob:
                del ob[PROP_SWAP]
            rep["objects"][ob.name] = {"materials": [s.material.name if s.material else None
                                                    for s in ob.material_slots]}
        for nm in images:
            img = bpy.data.images.get(nm)
            if img is not None and img.users == 0:
                bpy.data.images.remove(img)
        rep["images_removed"] = sorted(n for n in images if bpy.data.images.get(n) is None)
        rep["status"] = "ok"
    except Refusal as r:
        rep.update(status="refused", code=r.code, message=r.message)
    except Exception as exc:
        rep.update(status="failed", error=f"{type(exc).__name__}: {exc}", traceback=traceback.format_exc(limit=8))
    return rep


def remove_print_uv(parts=None) -> dict:
    """Undo print_uv on `parts` (default: every object with g3d_print_uv)."""
    rep = {"tool": "g3d.remove_print_uv", "status": "failed", "objects": {}, "warnings": []}
    try:
        scene = bpy.context.scene
        obs = ([o for o in scene.objects if o.get(PROP_UV) is not None] if parts is None
               else _resolve(parts, scene))
        for ob in obs:
            if _attached_materials([ob]):
                raise Refusal("E_ATTACHED", f"{ob.name} still shows a print; detach_print first")
        for ob in obs:
            info = _json_prop(ob, PROP_UV) or {}
            layer = info.get("layer", LAYER)
            backup = bpy.data.meshes.get(ob.get(PROP_BACKUP) or "")
            row = {}
            if backup is not None and info.get("mesh_digest") == _mesh_digest(ob.data):
                cur = ob.data
                name = cur.name
                ob.data = backup
                backup.use_fake_user = False
                cur.name = name + " (g3d printed)"
                backup.name = name
                if cur.users == 0:
                    bpy.data.meshes.remove(cur)
                row["restored"] = "pre-print mesh"
            else:
                if layer in ob.data.uv_layers:
                    ob.data.uv_layers.remove(ob.data.uv_layers[layer])
                row["restored"] = "layer removed only"
                if backup is not None:
                    rep["warnings"].append(f"W_GEOMETRY_EDITED: {ob.name} changed after print_uv; its split polygons "
                                           f"and custom normals stay, the pre-print mesh {backup.name!r} is kept")
            for key in (PROP_UV,) + ((PROP_BACKUP,) if row["restored"] == "pre-print mesh" else ()):
                if key in ob:
                    del ob[key]
            rep["objects"][ob.name] = row
        rep["status"] = "ok"
    except Refusal as r:
        rep.update(status="refused", code=r.code, message=r.message)
    except Exception as exc:
        rep.update(status="failed", error=f"{type(exc).__name__}: {exc}", traceback=traceback.format_exc(limit=8))
    return rep


__all__ = ["print_uv", "attach_print", "detach_print", "remove_print_uv", "load_manifest", "LAYER", "PROP_UV"]
