"""Headless Blender driver for the WP6 temple tool (tests and the $0 proof). Never saves the .blend it opened.

    blender -b --factory-startup --disable-autoexec <copy>.blend --python tests/proofs/wp6_bpy_run.py -- \
        <steps.json> <receipt.json>

steps.json = [{"op": ..., ...}] run in order; the receipt holds one result per step (also when a step fails):
    faceted_temple {spec, side?, targets?, decals?, dry_run?}   unfaceted_temple {object}
    check_relief {objects?, photo_creases?}                     scene_digest {}
    mesh_digests {objects?}                                      export {path}
    save_copy {path} (refused when the path exists or is the opened file)
    dump_temples {objects?}: world (asset mm) vertices / triangles / polygons / sharp edges / corner normals
    loft_sections {stations, path?, caps?, name?}               check_round_corners {object}
    timing_faceted_temple {spec, repeat}: dry-run timings of the Blender tool
"""
import json
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))      # repo root: `glasses3d` imports

import bpy  # noqa: E402
import numpy as np  # noqa: E402

from glasses3d.g3d import temple_bpy as T  # noqa: E402


def _args():
    a = sys.argv[sys.argv.index("--") + 1:]
    return Path(a[0]), Path(a[1])


def dump(ob, origin):
    V, Tri, polys = T.mesh_arrays(ob, origin, evaluated=True)
    me = ob.data
    sharp = []
    att = me.attributes.get("sharp_edge")
    if att is not None:
        vals = np.zeros(len(me.edges), bool)
        att.data.foreach_get("value", vals)
        ev = np.zeros(len(me.edges) * 2, np.int32)
        me.edges.foreach_get("vertices", ev)
        sharp = ev.reshape(-1, 2)[vals].tolist()
    cn = np.zeros(len(me.loops) * 3, np.float32)
    me.corner_normals.foreach_get("vector", cn)
    lv = np.zeros(len(me.loops), np.int32)
    me.loops.foreach_get("vertex_index", lv)
    M3 = np.array(ob.matrix_world, np.float64)[:3, :3]
    nw = cn.reshape(-1, 3).astype(np.float64) @ np.linalg.inv(M3)
    return {"name": ob.name, "mesh": me.name, "V": V.tolist(), "T": Tri.tolist(), "polys": polys, "sharp": sharp,
            "corner_normals": nw.tolist(), "loop_vertex": lv.tolist(),
            "modifiers": [m.type for m in ob.modifiers], "props": {k: (ob[k] if isinstance(ob[k], (str, int, float))
                                                                       else str(ob[k])) for k in ob.keys()}}


def run_step(st):
    op = st.get("op")
    kw = {k: v for k, v in st.items() if k != "op"}
    scene = bpy.context.scene
    if op == "faceted_temple":
        return T.faceted_temple(kw.pop("spec"), **kw)
    if op == "unfaceted_temple":
        return T.unfaceted_temple(kw["object"])
    if op == "check_relief":
        return T.check_relief(kw.get("objects"), photo_creases=kw.get("photo_creases"))
    if op == "scene_digest":
        return {"status": "ok", "digest": T.scene_digest(scene)}
    if op == "mesh_digests":
        obs = kw.get("objects") or [o.name for o in scene.objects if o.type == "MESH"]
        return {"status": "ok", "digests": {n: T.mesh_digest(bpy.data.objects[n].data) for n in obs},
                "data_names": {n: bpy.data.objects[n].data.name for n in obs}}
    if op == "export":
        from glasses3d.g3d.export_glb import export_glb
        return export_glb(kw["path"])
    if op == "save_copy":
        p = Path(kw["path"]).resolve()
        if p.exists() or p == Path(bpy.data.filepath).resolve():
            return {"status": "refused", "message": f"{p} exists or is the opened file"}
        p.parent.mkdir(parents=True, exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(p), copy=True)
        return {"status": "ok", "path": str(p)}
    if op == "dump_temples":
        origin = T.asset_origin(scene)
        names = kw.get("objects")
        if not names:
            c = T.temple_candidates(scene, origin)
            names = [c[s][0][1].name for s in (1, -1) if c[s]]
        return {"status": "ok", "temples": [dump(bpy.data.objects[n], origin) for n in names]}
    if op == "loft_sections":
        return T.loft_sections(kw["stations"], kw.get("path"), kw.get("caps", True), name=kw.get("name"))
    if op == "check_round_corners":
        return T.check_round_corners(kw["object"])
    if op == "timing_faceted_temple":
        ts = []
        for _ in range(int(kw.get("repeat", 3))):
            t0 = time.perf_counter()
            r = T.faceted_temple(kw["spec"], dry_run=True)
            ts.append(time.perf_counter() - t0)
        return {"status": r.get("status"), "seconds": ts, "core_ms": r.get("budget", {}).get("core_ms")}
    return {"status": "refused", "message": f"unknown op {op!r}"}


def main():
    steps_path, out_path = _args()
    steps = json.loads(steps_path.read_text(encoding="utf-8"))
    res = {"blend": bpy.data.filepath, "blender": bpy.app.version_string, "numpy": np.__version__, "steps": []}
    for st in steps:
        t0 = time.perf_counter()
        try:
            r = run_step(st)
        except Exception as exc:                                       # noqa: BLE001 - report every failure
            r = {"status": "failed", "error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()}
        if isinstance(r, dict):
            r["step_seconds"] = time.perf_counter() - t0
        res["steps"].append(r)
        partial = out_path.with_suffix(".partial.json")             # survives a Blender crash mid-run
        partial.write_text(json.dumps({"done": len(res["steps"]), "last_op": st.get("op")}, default=str),
                           encoding="utf-8")
        print(f"G3D_WP6_STEP {len(res['steps'])} {st.get('op')} {r.get('status') if isinstance(r, dict) else ''}",
              flush=True)
    out_path.write_text(json.dumps(res, default=str), encoding="utf-8")
    print("G3D_WP6_DONE", out_path)


main()
