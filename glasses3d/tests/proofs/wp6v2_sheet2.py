"""Owner sheet #2 (WP6_FACETS §5), one question per page: index.html asks "Does revision 0 look like the original?";
depth.html asks "Can you see the depth change, and does the preferred look like the photos?".

One job: a byte copy of the original Prada scene (sha 56274deb...) with the full pose basis (data/wp6v2/basis: target
photos 01-06 and the black-side variant), inputs/lab.json of the lab schema so the worker's lab-only build and variant
methods answer. Rig = the facet lab's job rig (temple_lab.cli.DEFAULT_RIG: studio_dome_v1, the owner's lighting choice
of 2026-10-11, "c is definitely the best", data/wp6v2/light_choice/owner_choice.json); the lenses are the scene's own,
as Astra sees them. The previous photo_env sheet is kept at data/wp6v2/sheet2-photo_env.
- ORIGINAL: compared at 01-06 and black-side (crops: the temple crop, cells <= CELL_MAX_PX, never upscaled). Its
  gains (white balance from 04's front; the black gloss gain from the black photo), any frozen light (photo_env only)
  and per-photo stretch are shared by every later render, so the columns differ only by shape;
- REVISION 0: template_from_mesh of the original's +X temple (agent/temple_lab/geometry.revision_zero, as the lab's
  prepare), applied to both temples from the original and compared likewise;
- PREFERRED-v2 (TOOL TEST): the owner-preferred spec cfc11127 converted with v1_to_v2 (the /2 default 0.05 mm
  snapping) with roll_mm 0.4, applied from the original and compared likewise;
- DEPTH STRIP (TOOL TEST): preferred-v2 with node C's thickness -2 / -1 / 0 / +1 mm (C = the lower corner of the logo
  triangle), the head and three variants rendered by the worker's temple_variants_render (the scene unchanged) at 05
  and 02 (native) and black-side (black gloss), composited like the compare rows.
Writes data/wp6v2/sheet2/{index.html (page 1: compare_<photo>.png = photo, original, revision 0), depth.html (page
2: strip_<photo>.png, then preferred_<photo>.png = photo, preferred), summary.json, job/}.
Run: glasses3d/.venv/Scripts/python.exe -m glasses3d.tests.proofs.wp6v2_sheet2
"""
from __future__ import annotations

import copy
import hashlib
import html
import json
import os
import shutil
import stat
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from glasses3d.agent.journal import BlenderSession
from glasses3d.agent.seeing import ENV_RIG, Seeing
from glasses3d.agent.temple_lab import geometry as G
from glasses3d.agent.temple_lab.cli import DEFAULT_RIG
from glasses3d.agent.temple_lab.lab import LAB_SCHEMA
from glasses3d.g3d.temple import build_temple
from glasses3d.g3d.temple.convert import v1_to_v2
from glasses3d.g3d.temple.spec import canonical, spec_sha256
from glasses3d.g3d_host import blender_camera as BC
from glasses3d.g3d_host import compare as C
from glasses3d.g3d_host import pose as PO

G3D = Path(__file__).resolve().parents[2]
REPO = G3D.parent
OUT = G3D / "data" / "wp6v2" / "sheet2"
SCENE = REPO / "automation/data/blender_agent/studio/jobs/8c82d986e6c14d26b4b38f3807712ea0/agent/scene.blend"
SCENE_SHA = "56274debb901fb28dea1f93f0a6b6ae8401501f7145573f8dedb68c7d2436c06"
PREFERRED = G3D / "data/wp6/pose_arm_resume_20261010/distal_all_views/before/spec.json"
PREFERRED_SHA = "cfc11127d085c4a642f4a77fb2d6daae676470ffabe699af55b16fc49ec908ce"
BASIS_JSON = G3D / "data" / "wp6v2" / "basis" / "cameras.json"
RIG = DEFAULT_RIG                     # the facet lab's job rig
LIGHT_CHOICE = G3D / "data" / "wp6v2" / "light_choice" / "owner_choice.json"
LIGHT_TEXT = {"studio_dome_v1": "the studio light you chose on the lighting sheet (light C)",
              ENV_RIG: "the light estimated from the photos (photo_env)"}
PHOTOS = ("reference-01", "reference-02", "reference-03", "reference-04", "reference-05", "reference-06", "black-side")
STRIP_PHOTOS = ("reference-05", "reference-02", "black-side")
ROLL_MM = 0.4
STRIP = (("Cm2", -2.0), ("Cm1", -1.0), ("Cp1", 1.0))          # worker-safe labels; the head is C as preferred
CELL_MAX_PX = 640
BUILD_DEADLINE_S = 120.0
QUESTIONS = {"revision0": "Does revision 0 look like the original?",
             "depth": "Can you see the depth change, and does the preferred look like the photos?"}
COLUMNS = {"revision0": {"original": "ORIGINAL", "r0": "REVISION 0 (where Astra starts)"},
           "preferred": {"preferred": f"PREFERRED, edges rounded {ROLL_MM} mm (TOOL TEST)"}}


def sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _rw(func, path, _exc):
    os.chmod(path, stat.S_IWRITE)
    func(path)


def short(rid: str) -> str:
    return rid.replace("reference-", "")


def apply_spec(s: BlenderSession, start: str, spec: dict, label: str) -> dict:
    """Build ``spec`` on both temples from the ORIGINAL (decals transfer once), as the lab's apply does."""
    if s.head["id"] != start:
        r = s.restore_checkpoint(start, call_id=f"restore-{label}")
        assert r["status"] == "ok", r
    out = s.call_mutating("sheet2", "temple_lab_build", {"spec": spec}, deadline_s=BUILD_DEADLINE_S,
                          call_id=f"build-{label}", label=label)
    if out["status"] != "ok":
        raise RuntimeError(f"{label}: the build was refused: {json.dumps(out)[:600]}")
    return out


def compare(see: Seeing, boxes: dict, gains=None, env=None) -> dict:
    r = see.compare_to_photos(list(PHOTOS), passes=("native_linear", "gloss_black"), rig=RIG, boxes=boxes,
                              gains=gains, env=env)
    if r["status"] != "ok":
        raise RuntimeError(f"compare failed: {r}")
    return {"result": r, "report": json.loads(Path(r["report"]).read_text(encoding="utf-8"))}


def page(rid: str, reps: dict, title: str, columns: dict) -> Image.Image:
    """One photo: PHOTO | the columns' renders, the native row (the gloss row for the black photo)."""
    key = "gloss_black" if reps["original"]["report"]["photos"][rid]["colourway"] != "target" else "native_linear"
    rows = reps["original"]["report"]["photos"][rid]["rows"]
    cells = [C.labelled(Image.open(rows["photo"]), f"PHOTO {short(rid)}", 15)]
    for name, label in columns.items():
        cells.append(C.labelled(Image.open(reps[name]["report"]["photos"][rid]["rows"][key]), label, 15))
    return titled(C.grid(cells, len(cells), max_px=2600, gap=8), title)


def titled(g: Image.Image, title: str) -> Image.Image:
    head = Image.new("RGB", (g.width, 32), (255, 255, 255))
    ImageDraw.Draw(head).text((8, 6), title, fill=(10, 10, 10), font=C._font(17))
    out = Image.new("RGB", (g.width, g.height + head.height), (255, 255, 255))
    out.paste(head, (0, 0))
    out.paste(g.convert("RGB"), (0, head.height))
    return out


def strip_views(bs: C.Basis, see: Seeing, boxes: dict) -> tuple[list, dict]:
    origin = np.asarray(see.bounds("sheet2-bounds")["origin_mm"] or (0.0, 0.0, 0.0), float)
    views, geo = [], {}
    for rid in STRIP_PHOTOS:
        x0, y0, x1, y1 = (int(v) for v in boxes[rid]["box"])
        k = float(boxes[rid]["k"])
        cam = BC.crop_camera(bs.camera(rid), x0, y0, k)
        shape = (max(1, round((y1 - y0) * k)), max(1, round((x1 - x0) * k)))
        views.append({"name": short(rid), "camera": BC.to_blender(cam, bs.frame, origin, shape), "pose": bs.pose(rid)})
        geo[rid] = {"box": (x0, y0, x1, y1), "k": k, "shape": shape}
    return views, geo


def strip_image(rid: str, files: dict, geo: dict, rec: dict, gains: dict, bs: C.Basis, depth_title: str):
    """PHOTO | C -2 | C -1 | C as preferred | C +1 mm for one photo, composited like the compare rows (gain,
    background, stretch)."""
    g = geo[rid]
    pc, _mc = Seeing.photo_at(bs, rid, g["box"], g["k"], g["shape"])
    lo, hi = (float(v) for v in rec["stretch"])
    if rec["colourway"] == "target":
        gain, (glo, ghi), ps = gains["colourways"]["target"]["native_linear"]["gain"], (lo, hi), "native_linear"
    else:
        gain, (glo, ghi), ps = gains["gloss_black"]["gain"], rec["gloss"]["stretch"], "gloss_black"
    cells = [C.labelled(Image.fromarray(C.apply_stretch(pc, lo, hi)), f"PHOTO {short(rid)}", 15)]
    for name, label in (("Cm2", "C 2 mm thinner"), ("Cm1", "C 1 mm thinner"), ("head", "C as preferred"),
                        ("Cp1", "C 1 mm thicker")):
        rgb, alpha = C.read_linear(files[f"{short(rid)}--{name}"][ps]["path"])
        im = Image.fromarray(C.apply_stretch(C.composite_render(rgb, alpha, gain, rec["background"]), glo, ghi))
        cells.append(C.labelled(im, f"{label} (TOOL TEST)", 15))
    return titled(C.grid(cells, 5, max_px=3200, gap=8), depth_title)


def main() -> dict:
    t0 = time.perf_counter()
    for path, want in ((SCENE, SCENE_SHA), (PREFERRED, PREFERRED_SHA)):
        if sha(path) != want:
            raise RuntimeError(f"{path}: sha256 changed")
    if OUT.exists():
        shutil.rmtree(OUT, onerror=_rw)
    root = OUT / "job"
    (root / "inputs").mkdir(parents=True)
    shutil.copyfile(SCENE, root / "inputs" / "scene.blend")
    assert sha(root / "inputs" / "scene.blend") == SCENE_SHA
    (root / "inputs" / "lab.json").write_text(json.dumps({"schema": LAB_SCHEMA, "rig": RIG, "assets": [
        {"path": "scene.blend", "sha256": SCENE_SHA}], "note": "owner sheet #2 job, never given to Astra"}),
        encoding="utf-8")
    doc = json.loads(BASIS_JSON.read_text(encoding="utf-8"))
    photos = {n: Path(r["image"]) for n, r in doc["images"].items()}
    C.import_cameras_json(root, BASIS_JSON, photos, reason="owner sheet #2")
    bs = C.load_basis(root)
    s = BlenderSession(root, seed_blend=root / "inputs" / "scene.blend", calls_dir=root / "tools")
    s.start()
    see = Seeing(s)
    timings, reps, specs = {}, {}, {}
    try:
        start = s.head["id"]
        exp = see.export_head("sheet2-original")
        mesh = see.fit_mesh(exp["path"], exp["sha256"], skip=bs.skip, frame=bs.frame)
        roles = PO.part_roles(exp["path"])
        boxes = {}
        for rid in PHOTOS:
            g = Seeing._matched_geometry(bs, rid, mesh, roles)
            x0, y0, x1, y1 = C.crop_box(g["temple"], bs.matte(rid))
            boxes[rid] = {"box": [x0, y0, x1, y1], "k": min(1.0, CELL_MAX_PX / max(x1 - x0, y1 - y0))}
        t = time.perf_counter()
        reps["original"] = compare(see, boxes)
        timings["compare_original"] = round(time.perf_counter() - t, 1)
        rep0 = reps["original"]["report"]
        gains, env = rep0["gains"], rep0["environment"]
        for rid in PHOTOS:
            boxes[rid]["stretch"] = rep0["photos"][rid]["stretch"]
        r0 = G.revision_zero(exp["path"])
        specs["r0"] = canonical(r0["spec"])
        pref = canonical(v1_to_v2(json.loads(PREFERRED.read_text(encoding="utf-8")), snap_mm=None))
        pref["roll_mm"] = ROLL_MM
        specs["preferred"] = canonical(pref)
        tb_pref = build_temple(specs["preferred"])
        for name in ("r0", "preferred"):
            t = time.perf_counter()
            apply_spec(s, start, specs[name], name)
            reps[name] = compare(see, boxes, gains, env)
            timings[f"apply_compare_{name}"] = round(time.perf_counter() - t, 1)
        # the depth strip: the head is preferred-v2; C's thickness -2 / -1 / +1 mm as variants
        c = specs["preferred"]["nodes"]["C"]
        variants = []
        for label, dt in STRIP:
            v = copy.deepcopy(specs["preferred"])
            v["nodes"]["C"] = [c[0], c[1], float(c[2]) + dt]
            v = canonical(v)
            tb = build_temple(v)
            variants.append({"label": label, "spec": v, "sha256": spec_sha256(v),
                             "vs_head_mm": G.geometry_delta(tb_pref, tb)["value_mm"]})
        views, geo = strip_views(bs, see, boxes)
        params = {**see._render_params(RIG, env, gains.get("light_frame")), "views": views,
                  "passes": ["native_linear", "gloss_black"],
                  "variants": [{"label": v["label"], "spec": v["spec"]} for v in variants],
                  "out_dir": str(root / "work" / "strip")}
        fp = s.fingerprint()
        t = time.perf_counter()
        rr = s.call_read("sheet2", "temple_variants_render", params, deadline_s=300, call_id="sheet2-strip")
        timings["strip_render"] = round(time.perf_counter() - t, 1)
        if rr["status"] != "ok" or rr["result"].get("status") != "ok":
            raise RuntimeError(f"strip render failed: {json.dumps(rr)[:800]}")
        res = rr["result"]
        refused = {k: v for k, v in res["variants"].items() if v.get("status") != "ok"}
        if refused:
            raise RuntimeError(f"strip variants refused: {refused}")
        unchanged = bool(res["unchanged"]) and s.fingerprint() == fp
        files = {v["name"]: v["files"] for v in res["render"]["views"]}
    finally:
        s.close()
    pages, preferred_pages = [], []
    for rid in PHOTOS:
        o = reps["original"]["report"]["photos"][rid]
        gloss = " in black gloss (the black photo's colour)" if o["colourway"] != "target" else ""
        title = f"{short(rid)} ({o['view_kind']}, {o['colourway']}): the photo, the original and revision 0{gloss}"
        p = OUT / f"compare_{short(rid)}.png"
        page(rid, reps, title, COLUMNS["revision0"]).save(p)
        pages.append({"photo": rid, "png": p.name, "title": title,
                      "temple_iou": {n: round(reps[n]["report"]["photos"][rid]["temple_iou"], 3)
                                     for n in COLUMNS["revision0"]},
                      "offsets_mm": {n: reps[n]["report"]["photos"][rid]["outer_edge_offsets_mm"]
                                     for n in COLUMNS["revision0"]}})
        title = f"{short(rid)} ({o['view_kind']}, {o['colourway']}): the photo and the preferred shape{gloss}"
        p = OUT / f"preferred_{short(rid)}.png"
        page(rid, reps, title, COLUMNS["preferred"]).save(p)
        preferred_pages.append({"photo": rid, "png": p.name, "title": title,
                                "temple_iou": round(reps["preferred"]["report"]["photos"][rid]["temple_iou"], 3),
                                "offsets_mm": reps["preferred"]["report"]["photos"][rid]["outer_edge_offsets_mm"]})
    strips = []
    for rid in STRIP_PHOTOS:
        rec = reps["original"]["report"]["photos"][rid]
        title = (f"Depth strip at {short(rid)}: the lower corner of the logo triangle (C) 2 mm thinner, 1 mm thinner, "
                 "as preferred, 1 mm thicker")
        p = OUT / f"strip_{short(rid)}.png"
        strip_image(rid, files, geo, rec, gains, bs, title).save(p)
        strips.append({"photo": rid, "png": p.name, "title": title})
    choice = json.loads(LIGHT_CHOICE.read_text(encoding="utf-8"))
    if choice.get("rig") != RIG:
        raise RuntimeError(f"the owner chose {choice.get('rig')}, the lab's rig is {RIG}")
    summary = {"questions": QUESTIONS, "created": C._utc(), "rig": RIG,
               "lighting_choice": {"file": str(LIGHT_CHOICE), "sha256": sha(LIGHT_CHOICE),
                                   **{k: choice.get(k) for k in ("date", "quote", "choice", "rig")}},
               "inputs": {"scene": {"path": str(SCENE), "sha256": SCENE_SHA},
                          "preferred": {"path": str(PREFERRED), "sha256": PREFERRED_SHA},
                          "basis": {"path": str(BASIS_JSON), "sha256": sha(BASIS_JSON)}},
               "specs": {n: {"sha256": spec_sha256(v), "roll_mm": v["roll_mm"]} for n, v in specs.items()},
               "template_report": r0["report"],
               "environment": None if env is None else {k: env.get(k) for k in ("weights_sha", "lambda")},
               "gains": gains, "boxes": boxes,
               "compare_ids": {n: r["result"]["compare_id"] for n, r in reps.items()},
               "strip": {"node_C": c, "variants": [{k: v[k] for k in ("label", "sha256", "vs_head_mm")}
                                                   for v in variants], "scene_unchanged": unchanged},
               "pages": pages, "preferred_pages": preferred_pages, "strips": strips,
               "timings_s": {**timings, "total": round(time.perf_counter() - t0, 1)}}
    C.write_json(OUT / "summary.json", summary)
    write_index(summary)
    return summary


def write_index(summary: dict) -> None:
    """Two pages, one question each: index.html (revision 0 against the original) and depth.html (the depth strip,
    then the preferred shape against the photos)."""
    def imgs(rows):
        return "".join(f"<section><h3>{html.escape(r['title'])}</h3><img src=\"{html.escape(r['png'])}\" "
                       f"alt=\"{html.escape(r['photo'])}\"></section>" for r in rows)

    def doc(title: str, question: str, body: str) -> str:
        return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
:root {{ --bg: #ffffff; --fg: #1a1a1a; --muted: #555; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg: #16181b; --fg: #e8e8e8; --muted: #a0a0a0; }} }}
body {{ background: var(--bg); color: var(--fg); font: 15px/1.45 system-ui, sans-serif; margin: 0 16px 48px; }}
h1 {{ font-size: 22px; }} h2 {{ font-size: 18px; margin-top: 32px; }} h3 {{ font-size: 15px; margin-top: 22px; }}
p, li {{ color: var(--muted); max-width: 70em; }}
a {{ color: inherit; }}
img {{ max-width: 100%; height: auto; border: 1px solid #8884; background: #fff; }}
</style></head><body>
<h1>{html.escape(question)}</h1>
<p>Lighting: {html.escape(LIGHT_TEXT.get(summary['rig'], summary['rig']))}. Every image uses the same light, colour
balance, crop and contrast, so the renders differ only by shape. The black photo (black-side) is a different colour of
the same frame: it shows the shapes in black gloss.</p>
{body}
</body></html>
"""
    limits = """<p>Known limits:</p>
<ul>
<li>The renders' shading does not match the photos everywhere: the temples are a little greyer in the side views,
and in the rear photo (06) the temple faces next to the hinges render black where the photo shows them light. The
photos' own studio light is not known, so shading is only a rough guide.</li>
<li>The lenses render black with dark blue stripes; in the photos they are dark grey-brown. Ignore the lenses.</li>
<li>On the black photo the lettering is white; the renders show it pale cream.</li>
</ul>"""
    page1 = f"""<p>Page 1 of 2.</p>
<ul>
<li><b>ORIGINAL</b>: the model as delivered.</li>
<li><b>REVISION 0</b>: the temples rebuilt from the original by the new tool. This is where Astra starts.</li>
</ul>
{limits}
<ul><li>Revision 0 has no facets (the tool's starting template has none), so the original's faint facets are
gone.</li></ul>
{imgs(summary['pages'])}
<p><a href="depth.html">Page 2 of 2: {html.escape(summary['questions']['depth'])}</a></p>"""
    page2 = f"""<p>Page 2 of 2. <a href="index.html">Page 1: {html.escape(summary['questions']['revision0'])}</a></p>
<ul>
<li><b>THE DEPTH STRIP (TOOL TEST)</b>: the preferred shape with one point made thinner or thicker: the lower corner
of the logo triangle, 2 mm thinner, 1 mm thinner, as preferred and 1 mm thicker.</li>
<li><b>PREFERRED (TOOL TEST)</b>: your preferred temple shape, rebuilt by the new tool with edges rounded {ROLL_MM}
mm, beside each photo. It is a check of the tool, not Astra's work.</li>
</ul>
{limits}
<ul><li>The preferred shape keeps its own outline, which differs from the original along the lower front edge and at
the tip.</li></ul>
<h2>1. The depth strip</h2>
{imgs(summary['strips'])}
<h2>2. The preferred shape beside the photos</h2>
{imgs(summary['preferred_pages'])}"""
    (OUT / "index.html").write_text(doc("Owner sheet 2, page 1", summary["questions"]["revision0"], page1),
                                    encoding="utf-8")
    (OUT / "depth.html").write_text(doc("Owner sheet 2, page 2", summary["questions"]["depth"], page2),
                                    encoding="utf-8")


if __name__ == "__main__":
    s = main()
    print(json.dumps({"timings_s": s["timings_s"], "pages": [p["png"] for p in s["pages"]],
                      "preferred_pages": [p["png"] for p in s["preferred_pages"]],
                      "strips": [p["png"] for p in s["strips"]], "strip": s["strip"]}, indent=1))
