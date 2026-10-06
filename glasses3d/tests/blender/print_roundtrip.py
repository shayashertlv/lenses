"""WP5 A6 / B5 round trip of the print tools in one headless Blender run (tests and the tool test only):

    blender -b --factory-startup <scene>.blend --python tests/blender/print_roundtrip.py -- <out_dir> <spec.json>

spec.json: {"parts": [...], "print_uv": {kwargs but parts}, "attach": {kwargs, "report" a path},
            "painted_glb": "<file name for the attached stage>" (default attached.glb),
            "save_painted": "<new .blend path>" (optional, refused when it exists),
            "label": str, "probes": bool (refusal / dry-run / replace probes on fresh reloads of the scene)}

Stages, each exported with the WP3 exporter into out_dir: original.glb (untouched scene), printuv.glb (after
print_uv), the attached stage, detached.glb (after detach_print), removed.glb (after remove_print_uv). The scene file
itself is never saved. Writes out_dir/roundtrip.json: every receipt, the exporter receipts, per stage the mesh digest of
every part and the node-tree state digest of every material, and the probe results.
"""
import hashlib
import json
import sys
import traceback
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
sys.dont_write_bytecode = True
from glasses3d.g3d import print_bpy as PB             # noqa: E402
from glasses3d.g3d.export_glb import export_glb, snapshot   # noqa: E402

out_dir, spec_path = sys.argv[sys.argv.index("--") + 1:][:2]
OUT = Path(out_dir)
OUT.mkdir(parents=True, exist_ok=True)
SPEC = json.loads(Path(spec_path).read_text(encoding="utf-8"))
SRC = bpy.data.filepath
PARTS = list(SPEC["parts"])
res = {"label": SPEC.get("label"), "source_blend": SRC, "parts": PARTS, "stages": {}, "probes": {}}


def digests() -> dict:
    meshes = {p: PB._mesh_digest(bpy.data.objects[p].data) for p in PARTS}
    mats = {}
    for p in PARTS:
        for s in bpy.data.objects[p].material_slots:
            if s.material is not None and s.material.node_tree is not None:
                mats[s.material.name] = hashlib.sha256(repr(PB._tree_state(s.material.node_tree)).encode()).hexdigest()
    uv = {p: [u.name for u in bpy.data.objects[p].data.uv_layers] for p in PARTS}
    render = {p: next((u.name for u in bpy.data.objects[p].data.uv_layers if u.active_render), None) for p in PARTS}
    active = {p: bpy.data.objects[p].data.uv_layers.active.name if bpy.data.objects[p].data.uv_layers.active else None
              for p in PARTS}
    return {"meshes": meshes, "materials": mats, "uv_layers": uv, "active_render_uv": render, "active_uv": active,
            "polygons": {p: len(bpy.data.objects[p].data.polygons) for p in PARTS},
            "print_uv_prop": {p: bpy.data.objects[p].get(PB.PROP_UV) for p in PARTS}}


def stage(name: str, glb: str, receipt=None) -> None:
    path = OUT / glb
    try:
        ex = export_glb(path)
    except Exception as exc:
        ex = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
    res["stages"][name] = {"glb": str(path), "export": ex, "receipt": receipt, "digests": digests()}


def reload():
    bpy.ops.wm.open_mainfile(filepath=SRC)


try:
    stage("original", "original.glb")
    r = PB.print_uv(PARTS, **SPEC.get("print_uv", {}))
    stage("printuv", "printuv.glb", r)
    if r.get("status") == "ok":
        a = PB.attach_print(**SPEC["attach"])
        stage("attached", SPEC.get("painted_glb", "attached.glb"), a)
        if a.get("status") == "ok" and SPEC.get("save_painted"):
            dst = Path(SPEC["save_painted"]).resolve()
            if dst.exists() or str(dst) == str(Path(SRC).resolve()):
                res["save_painted"] = {"status": "refused", "message": f"{dst} exists or is the source"}
            else:
                bpy.ops.wm.save_as_mainfile(filepath=str(dst), copy=True, check_existing=False)
                res["save_painted"] = {"status": "saved", "path": str(dst),
                                       "sha256": hashlib.sha256(dst.read_bytes()).hexdigest()}
        d = PB.detach_print(PARTS)
        stage("detached", "detached.glb", d)
        rm = PB.remove_print_uv(PARTS)
        stage("removed", "removed.glb", rm)

    if SPEC.get("probes"):
        P = res["probes"]
        kw = dict(SPEC.get("print_uv", {}))
        # dry run writes nothing
        reload()
        before = snapshot(bpy.context.scene)
        r = PB.print_uv(PARTS, **{**kw, "dry_run": True})
        P["dry_run"] = {"status": r.get("status"), "unchanged": snapshot(bpy.context.scene) == before,
                        "polygons_to_split": {k: v.get("polygons_to_split") for k, v in r.get("objects", {}).items()}}
        # a second print_uv needs replace=True; with it the layout is the same
        reload()
        r1 = PB.print_uv(PARTS, **kw)
        r2 = PB.print_uv(PARTS, **kw)
        r3 = PB.print_uv(PARTS, **{**kw, "replace": True})
        P["again"] = {"first": r1.get("status"), "second": r2.get("code"), "replace": r3.get("status"),
                      "same_charts": r1.get("charts_sha") == r3.get("charts_sha"),
                      "same_geometry": r1.get("geometry_sha") == r3.get("geometry_sha"),
                      "backup_kept": bpy.data.objects[PARTS[0]].get(PB.PROP_BACKUP)}
        # a geometry edit after print_uv makes the print stale
        if SPEC.get("attach"):
            ob = bpy.data.objects[PARTS[0]]
            v = ob.data.vertices[0]
            v.co = (v.co[0] + 0.25, v.co[1], v.co[2])
            ob.data.update()
            a = PB.attach_print(**SPEC["attach"])
            P["stale_geometry"] = {"status": a.get("status"), "code": a.get("code"), "message": a.get("message")}
            rm = PB.remove_print_uv(PARTS)
            P["remove_after_edit"] = {"status": rm.get("status"), "warnings": rm.get("warnings"),
                                      "rows": rm.get("objects")}
        # refusals
        reload()
        lens = next((o.name for o in bpy.context.scene.objects if o.type == "MESH" and PB._role(o) == "lens"), None)
        if lens:
            P["lens"] = PB.print_uv([lens], **kw).get("code")
        reload()
        ob = bpy.data.objects[PARTS[0]]
        m = ob.modifiers.new("Mirror", "MIRROR")
        P["mirror"] = PB.print_uv(PARTS, **kw).get("code")
        ob.modifiers.remove(m)
        reload()
        ob = bpy.data.objects[PARTS[0]]
        twin = bpy.data.objects.new("g3d twin", ob.data)
        bpy.context.scene.collection.objects.link(twin)
        P["shared_mesh"] = PB.print_uv(PARTS, **kw).get("code")
        reload()
        P["unknown"] = PB.print_uv(["No such object"], **kw).get("code")
        reload()
        ob = bpy.data.objects[PARTS[0]]
        m = ob.modifiers.new("Bevel", "BEVEL")
        P["topology_modifier"] = PB.print_uv(PARTS, **kw).get("code")
        if "bake" in kw:
            reload()
            P["bake_order"] = PB.print_uv(list(reversed(PARTS)), **kw).get("code")
        reload()
        P["transmission_map"] = PB.attach_print(**{**SPEC.get("attach", {"report": {}}), "transmission_map": True}).get("code")
except Exception as exc:
    res["error"] = f"{type(exc).__name__}: {exc}"
    res["traceback"] = traceback.format_exc()
(OUT / "roundtrip.json").write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
print("PRINT_ROUNDTRIP", OUT / "roundtrip.json")
