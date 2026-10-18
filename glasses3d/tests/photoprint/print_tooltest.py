"""WP5 TOOL TEST (Blender side; built in step 4): the bake's FT1119 print attached to a COPY of q001. Not a delivered
model: Astra builds models, Claude builds tools; every output here is labelled TOOL TEST, and the label names the
attached print by its sha256 and its bake (``make_label``; fix round 1 of step 6: the label said "step-4 print" on
every later bake).

    python -m glasses3d.tests.photoprint.print_tooltest [--out DIR] [--bake DIR] [--no-record]

1. copies q001's Studio scene.blend (sha-checked) to DIR/q001_copy.blend and never writes the original;
2. one headless Blender run (tests/blender/print_roundtrip.py) on that copy: export (original.glb), g3d.print_uv with
   bake=<the bake> (printuv.glb), g3d.attach_print with the bake's print manifest (q001_painted_TOOLTEST.glb,
   saved as q001_painted_TOOLTEST.blend), detach_print (detached.glb), remove_print_uv (removed.glb), plus refusal
   probes; the painted GLB, the portable output, then carries the label in its NAME and in ``asset.extras``
   (``g3d_tool_test``: written here by rewriting the JSON chunk, the binary chunk untouched; fix round 1), and every
   check below reads that labelled file;
3. grades on the host with the independent instrument (tests/instruments/print_metrics.py: WP3's GLB reader, never
   the tool's): the copy's export equals the bake's q001.glb triangle for triangle; geometry_sha of q001.glb, of the
   tool and of the manifest agree; print_uv changed only what it may; A6; B5; the painted GLB's TEXCOORD_1 equals the
   bake's layout recomputed independently on q001.glb; validate_spec; the host staleness check; detach and remove give
   the earlier GLBs back; renders of the painted GLB through its own TEXCOORD_1 equal renders through the bake's
   layout at every photo camera;
4. writes DIR/tooltest.json and DIR/tooltest_renders.png;
5. records A6 / B5 in the bake's bake.json (STOP B), only when the bake's print.png is the attached print (sha256) and
   bake.json belongs to it; ``--no-record`` leaves the bake untouched. Fix round 1 of the correction round: the slow
   pytest ran this with the default bake and rewrote the production bake.json to name a pytest temp dir; the test now
   passes ``--bake`` at a temp copy of the bake, and every run says whether (and why not) it recorded.
"""
from __future__ import annotations

from glasses3d.tests.fixtures.legacy_ft1119 import PRINT_PARTS as FT1119_PARTS

import argparse
import hashlib
import json
import shutil
import struct
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from glasses3d.g3d import printuv as PU
from glasses3d.g3d_host import blender
from glasses3d.g3d_host import glb as glbmod
from glasses3d.g3d_host.photoprint import inputs as IN
from glasses3d.tests.fixtures import legacy_ft1119
from glasses3d.g3d_host.photoprint import manifest as MF
from glasses3d.g3d_host.photoprint.compose import render_bake
from glasses3d.g3d_host.photoprint.report import jsonable
from glasses3d.g3d_host.spec import validate_spec
from glasses3d.tests.instruments import print_metrics as PM

G3D = Path(__file__).resolve().parents[2]
SCENE = IN.REPO / "automation/data/blender_agent/studio/jobs/3b8a1c7ae8114868b49b298b681f1b29/original/scene.blend"
BAKE = G3D / "data/wp5/ft1119_bake"
OUT = G3D / "data/wp5/tooltest"
ROUNDTRIP = G3D / "tests/blender/print_roundtrip.py"
PARTS = list(FT1119_PARTS)
LABEL = ("TOOL TEST: the WP5 FT1119 print attached to a COPY of q001 by g3d.print_uv + g3d.attach_print. "
         "Not a delivered model (Astra builds models; Claude builds tools).")      # the template; see make_label
PAINTED = "q001_painted_TOOLTEST.glb"
LABEL_KEY = "g3d_tool_test"


def make_label(man: dict, bake: Path) -> str:
    """The tool-test label of THIS print: the attached print.png's sha256 (the manifest's), its bake directory and the
    bake's AR level as its report records it (no step number: the label is built from the bake, never typed)."""
    lv = {}
    rp = Path(bake) / "report.json"
    if rp.is_file():
        lv = json.loads(rp.read_text(encoding="utf-8")).get("level") or {}
    level = ("AR level " + ", ".join([f"{k} {float(lv[k]):.3f}" for k in ("gain", "gamma", "sat", "chroma_power")
                                      if lv.get(k) is not None]
                                     + (["chroma held >= 1"] if lv.get("chroma_hold") else [])
                                     + ([f"chroma hue {float(lv['chroma_hue'][0]):g}-{float(lv['chroma_hue'][1]):g} deg"]
                                        if lv.get("chroma_hue") else []))
             if lv.get("applied") else "AR level not calibrated")
    return (f"TOOL TEST: the WP5 FT1119 print (print.png sha256 {man['print_sha256'][:16]}, bake "
            f"{Path(bake).resolve().name}, {level}) attached to a COPY of q001 by g3d.print_uv + g3d.attach_print. "
            "Not a delivered model (Astra builds models; Claude builds tools).")


def label_glb(path: Path, label: str = LABEL) -> dict:
    """Write ``label`` into the GLB's ``asset.extras[LABEL_KEY]`` in place: the JSON chunk is re-serialised (keys in
    their order, padded with spaces), the binary chunk is copied byte for byte. Returns the new asset block."""
    data = Path(path).read_bytes()
    doc, binc = PU.glbmesh.chunks(data)
    asset = doc.setdefault("asset", {})
    asset.setdefault("extras", {})[LABEL_KEY] = label
    js = json.dumps(doc, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    js += b" " * ((4 - len(js) % 4) % 4)
    body = struct.pack("<II", len(js), 0x4E4F534A) + js
    if binc:
        b = binc + b"\x00" * ((4 - len(binc) % 4) % 4)
        body += struct.pack("<II", len(b), 0x004E4942) + b
    Path(path).write_bytes(struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body)
    return dict(asset)


def sha(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


class GlbLayout:
    """A layout-like view (face_index, uv) of a GLB's TEXCOORD_k mapped onto the job mesh's faces, so the host
    renderer samples print.png exactly where a glTF viewer of that GLB would."""

    def __init__(self, job, glb_path: Path, width: int, height: int):
        view = PM.glb_view(glb_path)
        faces, uvs = [], []
        for part in PARTS:
            row = view[part][0]
            k = json.loads(row["extras"]["g3d_print_uv"])["texcoord"]
            uv = PM.corner(row, f"TEXCOORD_{k}").astype(np.float64) * np.array([width, height], float)
            f = np.nonzero(np.isin(job.mesh.part, job.mesh.part_ids([part])))[0]
            P_glb = PM.corner(row, "POSITION").astype(np.float64) * 1000.0
            if len(f) != len(uv) or np.abs(job.mesh.V[job.mesh.F[f]] - P_glb).max() > 1e-6:
                raise ValueError(f"{part}: the GLB's triangles are not the job mesh's (order or geometry)")
            faces.append(f)
            uvs.append(uv)
        self.faces = np.concatenate(faces)
        self.uv = np.concatenate(uvs)

    def face_index(self, n: int) -> np.ndarray:
        out = -np.ones(n, np.int64)
        out[self.faces] = np.arange(len(self.faces))
        return out


def run_blender(copy: Path, out: Path, bake: Path, label: str = LABEL) -> dict:
    spec = {"parts": PARTS, "print_uv": {"bake": str(bake)}, "attach": {"report": str(bake)},
            "painted_glb": PAINTED, "save_painted": str(out / "q001_painted_TOOLTEST.blend"),
            "label": label, "probes": True}
    (out / "roundtrip_spec.json").write_text(json.dumps(spec, indent=1), encoding="utf-8")
    t = time.perf_counter()
    proc = blender.run(copy, ROUNDTRIP, [out, out / "roundtrip_spec.json"], timeout_s=600)
    (out / "blender.log").write_text(proc.stdout[-200000:] + "\n--- stderr ---\n" + proc.stderr[-50000:],
                                     encoding="utf-8")
    rt = out / "roundtrip.json"
    if not rt.is_file():
        raise RuntimeError(f"Blender wrote no roundtrip.json (see {out / 'blender.log'})")
    res = json.loads(rt.read_text(encoding="utf-8"))
    res["blender_seconds"] = round(time.perf_counter() - t, 1)
    return res


def arrays_equal(a: Path, b: Path, names) -> dict:
    """Per node: corner positions, normals and TEXCOORD_0 of every triangle identical in order (two exports of the
    same geometry by different exporter runs; vertex order may differ)."""
    A, B = PM.glb_view(a), PM.glb_view(b)

    def node(view, n):                        # a Studio export names its nodes 'Brow_Left.001'
        return view[n] if n in view else view[f"{n}.001"]
    rows = {}
    for n in names:
        ra, rb = node(A, n)[0], node(B, n)[0]
        same = len(ra["triangles"]) == len(rb["triangles"])
        for att in ("POSITION", "NORMAL", "TEXCOORD_0"):
            if att in ra["attributes"] or att in rb["attributes"]:
                same = same and att in ra["attributes"] and att in rb["attributes"] and \
                    np.array_equal(PM.corner(ra, att), PM.corner(rb, att))
        rows[n] = bool(same)
    return {"nodes": rows, "pass": all(rows.values())}


def renders(job, bake: Path, painted: Path, layout, out_png: Path) -> dict:
    atlas = np.asarray(Image.open(bake / "print.png").convert("RGB"))
    man = MF.load_manifest(bake)
    gl = GlbLayout(job, painted, man["width"], man["height"])
    rows, tiles = {}, []
    for view in sorted(job.views):
        v = job.views[view]
        a = render_bake(job, layout, atlas, view, v.camera)
        b = render_bake(job, gl, atlas, view, v.camera)
        same_mask = bool(np.array_equal(a["mask"], b["mask"]))
        d = np.abs(a["ren"] - b["ren"])[a["mask"] & b["mask"]]
        rows[view] = {"pixels": int(a["mask"].sum()), "same_pixels": same_mask,
                      "max_abs_diff_levels": float(d.max()) if d.size else 0.0,
                      "mean_abs_diff_levels": float(d.mean()) if d.size else 0.0}
        photo = v.image.astype(np.float64)
        comp = photo.copy()
        comp[b["mask"]] = b["ren"][b["mask"]]
        ys, xs = np.nonzero(b["mask"])
        if len(ys):
            pad = 12
            y0, y1 = max(ys.min() - pad, 0), min(ys.max() + pad, photo.shape[0])
            x0, x1 = max(xs.min() - pad, 0), min(xs.max() + pad, photo.shape[1])
            pair = np.concatenate([photo[y0:y1, x0:x1], comp[y0:y1, x0:x1]], 1)
            tiles.append((view, Image.fromarray(np.clip(pair, 0, 255).astype(np.uint8))))
    W = 1600
    rows_img = []
    for view, im in tiles:
        s = min(W / im.width, 260 / im.height)
        rows_img.append((view, im.resize((max(1, int(im.width * s)), max(1, int(im.height * s))), Image.LANCZOS)))
    H = 64 + sum(im.height + 22 for _, im in rows_img)
    sheet = Image.new("RGB", (W, H), (32, 32, 32))
    dr = ImageDraw.Draw(sheet)
    dr.text((10, 8), f"TOOL TEST - the WP5 FT1119 print (print.png sha256 {sha(Path(bake) / 'print.png')[:16]}, bake "
                     f"{Path(bake).resolve().name}) on a COPY of q001 (g3d.print_uv + g3d.attach_print); "
                     "not a delivered model", fill=(255, 210, 120))
    dr.text((10, 28), "left: the photo; right: the painted GLB rendered through its OWN TEXCOORD_1 (glTF bilinear, "
                      "no lighting) over the photo", fill=(220, 220, 220))
    y = 64
    for view, im in rows_img:
        dr.text((10, y), f"{view}: max |GLB-uv render - bake-layout render| = {rows[view]['max_abs_diff_levels']:.4f} "
                         f"levels over {rows[view]['pixels']} px", fill=(200, 200, 200))
        sheet.paste(im, (10, y + 18))
        y += im.height + 22
    sheet.save(out_png)
    return {"views": rows, "sheet": str(out_png),
            "pass": all(r["same_pixels"] and r["max_abs_diff_levels"] <= 0.5 for r in rows.values())}


def record_in_bake(bake: Path, man: dict, checks: dict, tooltest_json: Path) -> dict:
    """Correction round (step-4 review finding: STOP B listed A6 / B5 as 'not built' although this tool test builds
    and grades them): when the bake's print.png is the one this tool test attached (sha256), bake.json's STOP B takes
    A6 and B5 from here. Returns {'recorded': bool, 'reason' or 'STOP_B'}."""
    bj = bake / "bake.json"
    if not bj.is_file():
        return {"recorded": False, "reason": f"no {bj} (the bake has not finished writing it?)"}
    if sha(bake / "print.png") != man["print_sha256"]:
        return {"recorded": False, "reason": "the bake's print.png is not the attached print"}
    d = json.loads(bj.read_text(encoding="utf-8"))
    if d.get("print_sha256", man["print_sha256"]) != man["print_sha256"] or (
            "print_sha256" not in d and bj.stat().st_mtime < (bake / "print.png").stat().st_mtime):
        return {"recorded": False, "reason": "bake.json belongs to another print (a bake still running writes it last)"}
    sb = d.get("STOP_B")
    d["tooltest"] = {"json": str(tooltest_json), "print_sha256": man["print_sha256"], "A6": bool(checks["A6"]),
                     "B5": bool(checks["B5"])}
    if sb:
        rs = [r for r in sb["reasons"] if not r.startswith(("A6 ", "B5 "))]
        rs += [f"{k} fails on the tool-test GLB of this print" for k in ("A6", "B5") if not checks[k]]
        sb["reasons"], sb["fired"] = rs, bool(rs)
        sb["A6_B5_from"] = str(tooltest_json)
    bj.write_text(json.dumps(jsonable(d), indent=1), encoding="utf-8")
    return {"recorded": True, "bake_json": str(bj), "STOP_B": sb}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="WP5 tool test (Blender side) on a copy of q001")
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--bake", default=str(BAKE))
    ap.add_argument("--no-record", action="store_true", help="do not record A6 / B5 in the bake's bake.json")
    a = ap.parse_args(argv)
    out, bake = Path(a.out), Path(a.bake)
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    job, bake = legacy_ft1119.bake_inputs(bake, out / 'legacy-bake')
    if not (bake / MF.MANIFEST).is_file():
        MF.write_manifest(bake)
    man = MF.load_manifest(bake)
    label = make_label(man, bake)
    rep = {"label": label, "inputs": {}}
    src_sha = sha(SCENE)
    copy = out / "q001_copy.blend"
    for p in (copy, out / "q001_painted_TOOLTEST.blend"):
        if p.exists():
            p.unlink()
    shutil.copyfile(SCENE, copy)
    rep["inputs"] = {"scene": str(SCENE), "scene_sha256": src_sha, "copy": str(copy), "copy_sha256": sha(copy),
                     "bake": str(bake), "manifest": {k: man[k] for k in ("print_sha256", "geometry_sha", "charts_sha",
                                                                         "width", "height", "tx_per_mm", "glb",
                                                                         "glb_sha256")}}
    rt = run_blender(copy, out, bake, label)
    rep["scene_unchanged"] = sha(SCENE) == src_sha
    rep["copy_unchanged"] = sha(copy) == rep["inputs"]["copy_sha256"]
    st = rt["stages"]
    rep["receipts"] = {k: v.get("receipt") for k, v in st.items()}
    rep["exports"] = {k: {kk: v["export"].get(kk) for kk in ("status", "error", "print_uv_extras", "notes",
                                                              "scene_unchanged")} for k, v in st.items()}
    rep["probes"] = rt.get("probes")
    rep["save_painted"] = rt.get("save_painted")
    rep["blender_seconds"] = rt.get("blender_seconds")
    if rt.get("error"):
        rep["error"] = rt["error"]
        rep["traceback"] = rt.get("traceback")
    G = {k: Path(v["glb"]) for k, v in st.items()}
    q001 = Path(man["glb"])
    stale = out / "q001_painted.glb"                  # an unlabelled painted GLB of an earlier run must not linger
    if stale.exists():
        stale.unlink()
    rep["painted_label"] = {"file": G["attached"].name, "asset": label_glb(G["attached"], label)}
    doc_a, _ = PU.glbmesh.chunks(G["attached"].read_bytes())
    rep["painted_label"]["ok"] = bool("TOOLTEST" in G["attached"].name
                                      and (doc_a["asset"].get("extras") or {}).get(LABEL_KEY) == label
                                      and man["print_sha256"][:16] in label)

    # the copy exports to the bake's GLB; the three geometry identities agree
    rep["copy_export_equals_bake_glb"] = arrays_equal(q001, G["original"], PARTS)
    prims = PU.glbmesh.read_glb(q001)
    V, F, part, names = PU.glbmesh.mesh_arrays(prims)
    geo_q = PU.glbmesh.geometry_sha(V, F, part, names, PARTS)
    pr = rt["stages"]["printuv"]["receipt"] or {}
    rep["geometry_sha"] = {"q001_glb": geo_q, "print_uv": pr.get("geometry_sha"), "manifest": man["geometry_sha"],
                           "match": geo_q == pr.get("geometry_sha") == man["geometry_sha"]}
    rep["charts_sha"] = {"print_uv": pr.get("charts_sha"), "manifest": man["charts_sha"],
                         "match": pr.get("charts_sha") == man["charts_sha"]}

    # what print_uv changed; A6; B5; uv identity with the bake's layout (recomputed here on q001.glb)
    rep["print_uv_invariance"] = PM.print_uv_invariance(G["original"], G["printuv"], PARTS)
    rep["A6"] = PM.a6_round_trip(G["original"], G["attached"], PARTS, print_png=man["print_png"])
    rep["B5"] = PM.b5_material_control(G["printuv"], G["attached"], PARTS)
    lay = PU.layout(job.mesh.V, job.mesh.F, job.mesh.part, job.mesh.names, PARTS, man["tx_requested"])
    rep["host_layout"] = {"charts_sha": PU.glbmesh.charts_sha(lay.charts, lay.width, lay.height),
                          "matches_manifest": PU.glbmesh.charts_sha(lay.charts, lay.width, lay.height) == man["charts_sha"]}
    frow = lay.face_index(len(job.mesh.F))
    expected = {p: lay.uv[frow[PU.glbmesh.part_faces(job.mesh.part, job.mesh.names, p)]] for p in PARTS}
    rep["uv_identity"] = PM.uv_identity(G["attached"], expected, man["width"], man["height"])
    sp = validate_spec(G["attached"])
    rep["validate_spec"] = {"ok": sp["ok"], "errors": sp["errors"], "warnings": sp["warnings"]}
    rep["host_staleness_check"] = MF.check_glb(G["attached"], man)
    rep["host_staleness_check_original"] = {k: MF.check_glb(G["original"], man)[k] for k in ("ok", "reasons")}

    # undo: detach gives the print_uv export back, remove gives the original export back
    rep["detach_restores_printuv_glb"] = PM.b5_material_control(G["printuv"], G["detached"], [])
    rep["remove_restores_original_glb"] = PM.b5_material_control(G["original"], G["removed"], [])

    # renders through the GLB's own uvs vs through the bake's layout, at every photo camera
    rep["renders"] = renders(job, bake, G["attached"], lay, out / "tooltest_renders.png")
    g = glbmod.load(G["attached"])
    rep["painted_glb"] = {"path": str(G["attached"]), "sha256": sha(G["attached"]), "bytes": G["attached"].stat().st_size,
                          "materials": [m.get("name") for m in g.materials], "images": len(g.doc.get("images", []))}
    checks = {"geometry_sha_match": rep["geometry_sha"]["match"], "charts_sha_match": rep["charts_sha"]["match"],
              "host_layout_is_the_bake": rep["host_layout"]["matches_manifest"],
              "copy_export_equals_bake_glb": rep["copy_export_equals_bake_glb"]["pass"],
              "print_uv_invariance": rep["print_uv_invariance"]["pass"], "A6": rep["A6"]["pass"] and sp["ok"],
              "B5": rep["B5"]["pass"], "uv_identity": rep["uv_identity"]["pass"], "validate_spec": sp["ok"],
              "host_staleness_check": rep["host_staleness_check"]["ok"],
              "detach_restores": rep["detach_restores_printuv_glb"]["pass"],
              "remove_restores": rep["remove_restores_original_glb"]["pass"], "renders_equal": rep["renders"]["pass"],
              "scene_never_written": rep["scene_unchanged"] and rep["copy_unchanged"],
              "painted_glb_labelled_tool_test": rep["painted_label"]["ok"]}
    rep["checks"] = checks
    rep["pass"] = all(checks.values())
    rep["seconds"] = round(time.perf_counter() - t0, 1)
    (out / "tooltest.json").write_text(json.dumps(jsonable(rep), indent=1, default=str), encoding="utf-8")
    rep_b = {"recorded": False, "reason": "--no-record"} if a.no_record else         record_in_bake(bake, man, checks, out / "tooltest.json")
    print(json.dumps({"pass": rep["pass"], "checks": checks, "out": str(out), "bake": str(bake), "record": rep_b},
                     indent=1))
    return 0 if rep["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
