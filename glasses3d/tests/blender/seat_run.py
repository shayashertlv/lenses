"""Run WP4 seating steps inside headless Blender and dump what the host tests compare (tests only).

    blender -b --factory-startup --disable-autoexec <copy>.blend --python tests/blender/seat_run.py -- <job.json> <out.json>

job.json: {"steps": [{"op": ..., ...}, ...]}; out.json: {"results": [{"op", "key", "result"}...], "error"?}. Ops:
- measure_dump {lens, npz, frame_parts?}: per lens body, the frames, the frame solids near it, the body, and the BVH
  measurement (cap, room, cover, edge, classes, owners) plus the BVH timing -> npz (the host re-measures the same
  arrays with numpy and open3d: P1);
- check {kwargs}: g3d.check_lens_seating(**kwargs, stations_table=True);
- seat {kwargs} / hide {kwargs} / unseat {lens} / unhide {eyewire, lens?};
- arrays {objects, npz}: raw local co (float32 as stored), asset V, triangles, edges, corner normals, sharp edges;
- digest {objects}: mesh digests (positions, topology, attributes) and object property values;
- export {glb}: the WP3 exporter;
- save_copy {path};
- exec {code}: test set-up code run with bpy in scope (add a modifier, share a mesh), result = its `result` variable.
"""
import json
import sys
import time
import traceback
from pathlib import Path

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True        # tests/fixtures/ is re-included in .gitignore: no __pycache__ there
from glasses3d.g3d import export_glb as EX            # noqa: E402
from glasses3d.g3d import seat_bpy as SB              # noqa: E402
from glasses3d.g3d.seat import check as CK            # noqa: E402
from glasses3d.g3d.seat import stations              # noqa: E402


def _jsonable(x):
    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    if isinstance(x, np.ndarray):
        return _jsonable(x.tolist())
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.floating,)):
        f = float(x)
        return f if np.isfinite(f) else None
    if isinstance(x, float) and not np.isfinite(x):
        return None
    if isinstance(x, (np.bool_,)):
        return bool(x)
    return x


def measure_dump(lens, npz, frame_parts=None):
    ob = SB._obj(lens)
    scene = bpy.context.scene
    warnings = []
    origin = SB.asset_origin(scene, warnings)
    dg = bpy.context.evaluated_depsgraph_get()
    nodes, col, fobjs, extra = SB._tool_context(ob, frame_parts, scene, dg, origin, warnings)
    bodies = [b for b, n in zip(col["bodies"], col["body_node"]) if n["name"] == ob.name]
    out = {}
    summary = []
    for bi, body in enumerate(bodies):
        near = CK.parts_near(body, col["solids"])
        t0 = time.time()
        be = SB.BVHBackend([(c.V, c.T) for _, c in near] + [(body.V, body.T)])
        t1 = time.time()
        fr, meas = stations.measure_body(body, be, len(near))
        t2 = time.time()
        band = stations.buried_band(fr, meas, be, len(near))
        t3 = time.time()
        p = f"b{bi}_"
        out.update({p + "P": fr.P, p + "Pb": fr.Pb, p + "n": fr.n, p + "t": fr.t, p + "u": fr.u, p + "s": fr.s,
                    p + "body_V": body.V, p + "body_T": body.T, p + "cap": meas.cap, p + "room": meas.room,
                    p + "owner": meas.owner, p + "cover_front": meas.cover_front, p + "cover_back": meas.cover_back,
                    p + "edge": meas.edge, p + "cls": np.asarray(meas.cls, dtype=str),
                    p + "unmeasured": meas.unmeasured, p + "recast": meas.recast,
                    p + "band_cand": band.candidate, p + "band_front": band.front_mm, p + "band_back": band.back_mm,
                    p + "nparts": np.array(len(near))})
        for k, (_, c) in enumerate(near):
            out[p + f"part{k}_V"] = c.V
            out[p + f"part{k}_T"] = c.T
        summary.append({"body": body.name, "points": int(len(fr)), "parts": [c.name for _, c in near],
                        "build_s": round(t1 - t0, 4), "measure_s": round(t2 - t1, 4), "band_s": round(t3 - t2, 4),
                        "lines": int(len(fr) * 7)})
    np.savez(npz, **out)
    return {"bodies": summary, "warnings": warnings}


def arrays(objects, npz):
    out = {}
    origin = SB.asset_origin(bpy.context.scene)
    for i, name in enumerate(objects):
        ob = SB._obj(name)
        A = SB.MeshArrays(ob, origin, corners=True)
        me = ob.data
        sharp = SB._attr_values(me, "sharp_edge", len(me.edges))
        out.update({f"o{i}_co": A.co, f"o{i}_V": A.V, f"o{i}_T": A.T, f"o{i}_E": A.E, f"o{i}_N": A.N,
                    f"o{i}_loop_v": A.loop_v, f"o{i}_sharp": sharp})
    np.savez(npz, **out)
    return {"objects": list(objects)}


def digest(objects):
    out = {}
    for name in objects:
        ob = bpy.data.objects.get(name)
        if ob is None:
            out[name] = None
            continue
        out[name] = {"mesh": ob.data.name, "digest": EX._mesh_digest(ob.data),
                     "props": {k: (ob[k] if isinstance(ob[k], (str, int, float)) else repr(ob[k])) for k in ob.keys()},
                     "modifiers": [m.name for m in ob.modifiers]}
    out["_meshes"] = sorted(m.name for m in bpy.data.meshes)
    out["_objects"] = sorted(o.name for o in bpy.data.objects)
    return out


def main():
    args = sys.argv[sys.argv.index("--") + 1:]
    job = json.loads(Path(args[0]).read_text(encoding="utf-8"))
    out = {"results": [], "blend": bpy.data.filepath}
    try:
        for st in job["steps"]:
            op = st["op"]
            t0 = time.time()
            if op == "measure_dump":
                r = measure_dump(st["lens"], st["npz"], st.get("frame_parts"))
            elif op == "check":
                r = SB.check_lens_seating(**dict(st.get("kwargs", {}), stations_table=True))
            elif op == "seat":
                r = SB.seat_lens(**st["kwargs"])
            elif op == "hide":
                r = SB.hide_eyewire(**st["kwargs"])
            elif op == "unseat":
                r = SB.unseat_lens(st["lens"])
            elif op == "unhide":
                r = SB.unhide_eyewire(st["eyewire"], st.get("lens"))
            elif op == "arrays":
                r = arrays(st["objects"], st["npz"])
            elif op == "digest":
                r = digest(st["objects"])
            elif op == "export":
                try:
                    r = EX.export_glb(st["glb"])
                except Exception as exc:
                    r = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
            elif op == "exec":
                ns = {"bpy": bpy, "np": np, "result": None}
                exec(st["code"], ns)
                r = ns["result"]
            elif op == "save_copy":
                bpy.ops.wm.save_as_mainfile(filepath=st["path"], copy=True, check_existing=False)
                r = {"path": st["path"]}
            else:
                raise ValueError(f"unknown op {op}")
            out["results"].append({"op": op, "key": st.get("key", op), "seconds": round(time.time() - t0, 3),
                                   "result": _jsonable(r)})
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
        out["traceback"] = traceback.format_exc()
    Path(args[1]).write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")


main()
