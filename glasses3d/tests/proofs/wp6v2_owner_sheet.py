"""Owner sheet #1 (WP6_FACETS §5): do these renders show the temples fairly?

Three models, each a byte copy opened in its own worker job with the WP6 v2 pose basis (data/wp6v2/basis):
- ORIGINAL: the accepted Prada Studio scene (sha 56274deb...);
- ASTRA c0003: the Astra temple-lab checkpoint
  data/wp6/astra_temple_lab_20261010/real-astra-001/checkpoints/c0003.blend;
- PREFERRED: the owner-preferred v1 build data/wp6/preferred_goal_20261010/photo_proof_coverage_checked/preferred/p3/
  prada_tool_test_faceted.blend (sha 45964428...).
Each is compared with the photo-matched compare (agent/seeing.py; ``--rig`` studio_dome_v1 = sheet #1, photo_env =
sheet #1b) at 01-06 and black-side, passes native_linear and gloss_black. All three share one normalisation — the
original's frozen gains (white-balanced per channel) and gloss stretch, with photo_env the environment fitted on the
original, and per photo one crop (the union of the three models' temple crops; cells <= 640 px, never upscaled) and
one photo stretch (the original's) — so the columns differ only by geometry. The black-side photo is the variant
colourway: it gets the gloss_black row only (the models' material is the target colourway).

Writes data/wp6v2/<sheet1 | sheet1b>/ (or ``--out``) {<photo>.png (rows native / gloss_black, columns PHOTO | ORIGINAL |
ASTRA c0003 | PREFERRED), index.html (cover text: what each row is, known limits), summary.json}. The photo_env cover's
limits are written from this run's own measurements (summary.json ``facts``: the calibration photos, the stretch
factor, the preferred build's facet order and upper-crease step and the black-side facet range (the measures of
tests/proofs/wp6v2_light_choice.py on the T2 facet polygons), and the outer-edge offsets at the top and rear photos
as medians per model and side, their range and which side of the photo's edge each model sits on), never typed in.
``--cover DIR`` rewrites DIR/index.html from DIR/summary.json alone (no worker; the offsets are recomputed from the
summary's pages, the same data).
Run: glasses3d/.venv/Scripts/python.exe -m glasses3d.tests.proofs.wp6v2_owner_sheet [--rig photo_env] [--out DIR]
     [--cover DIR]
"""
from __future__ import annotations

import argparse
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
from glasses3d.agent.seeing import Seeing
from glasses3d.g3d_host import compare as C
from glasses3d.g3d_host import pose as PO
from glasses3d.tests.proofs import wp6v2_light_choice as LC

G3D = Path(__file__).resolve().parents[2]
REPO = G3D.parent
DATA = G3D / "data"
OUT = DATA / "wp6v2" / "sheet1"
OUTS = {"studio_dome_v1": DATA / "wp6v2" / "sheet1", "photo_env": DATA / "wp6v2" / "sheet1b"}
BASIS_JSON = DATA / "wp6v2" / "basis" / "cameras.json"
MODELS = {
    "original": ("ORIGINAL", REPO / "automation/data/blender_agent/studio/jobs/8c82d986e6c14d26b4b38f3807712ea0/agent/"
                              "scene.blend", "56274debb901fb28dea1f93f0a6b6ae8401501f7145573f8dedb68c7d2436c06"),
    "c0003": ("ASTRA c0003", DATA / "wp6/astra_temple_lab_20261010/real-astra-001/checkpoints/c0003.blend",
              "e8730c710b237a621a61bf8ded1111d27f26ad435af5ddbc0a706e089f7ed24f"),
    "preferred": ("PREFERRED (v1 build)", DATA / "wp6/preferred_goal_20261010/photo_proof_coverage_checked/preferred/"
                                               "p3/prada_tool_test_faceted.blend",
                  "45964428a9d38910048477cd037a04f6cec0b17531136ab227bed4a354826e90"),
}
PHOTOS = ("reference-01", "reference-02", "reference-03", "reference-04", "reference-05", "reference-06", "black-side")
CELL_MAX_PX = 640
QUESTION = "Do these renders show the temples fairly?"


def sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _rw(func, path, _exc):
    os.chmod(path, stat.S_IWRITE)
    func(path)


def open_job(name: str, blend: Path, photos: dict):
    root = OUT / f"job_{name}"
    if root.exists():
        shutil.rmtree(root, onerror=_rw)
    root.mkdir(parents=True)
    seed = root / "seed.blend"
    shutil.copy2(blend, seed)
    os.chmod(seed, stat.S_IWRITE | stat.S_IREAD)
    C.import_cameras_json(root, BASIS_JSON, photos, reason="owner sheet #1")
    s = BlenderSession(root, seed_blend=seed, calls_dir=root / "tools")
    s.start()
    return s, Seeing(s)


def union_box(boxes: list) -> tuple:
    a = np.array([b for b in boxes if b is not None])
    return int(a[:, 0].min()), int(a[:, 1].min()), int(a[:, 2].max()), int(a[:, 3].max())


def label_cell(im: Image.Image, text: str, size: int = 15) -> Image.Image:
    return C.labelled(im, text, size)


def sheet(rid: str, recs: dict, title: str) -> Image.Image:
    """2 rows (native / gloss_black; gloss_black only where no model has a native row) x 4 columns (PHOTO,
    ORIGINAL, ASTRA c0003, PREFERRED)."""
    cells = []
    rows = [(row, key) for row, key in (("native (white-balanced gain + stretch)", "native_linear"),
                                        ("gloss_black (black glossy acetate)", "gloss_black"))
            if all(key in recs[name]["rows"] for name in MODELS)]
    for row, key in rows:
        cells.append(label_cell(Image.open(recs["original"]["rows"]["photo"]), f"PHOTO {rid}"))
        for name, (label, _p, _s) in MODELS.items():
            cells.append(label_cell(Image.open(recs[name]["rows"][key]), f"{label}: {row}"))
    g = C.grid(cells, 4, max_px=4000, gap=8)
    head = Image.new("RGBA", (g.width, 34), (255, 255, 255, 255))
    ImageDraw.Draw(head).text((8, 6), title, fill=(10, 10, 10, 255), font=C._font(18))
    out = Image.new("RGB", (g.width, g.height + head.height), (255, 255, 255))
    out.paste(head.convert("RGB"), (0, 0))
    out.paste(g.convert("RGB"), (0, head.height))
    return out


def main(rig: str = "studio_dome_v1", out: Path | None = None) -> dict:
    global OUT
    OUT = Path(out) if out else OUTS[rig]
    t0 = time.perf_counter()
    for name, (_l, path, want) in MODELS.items():
        got = sha(path)
        if got != want:
            raise RuntimeError(f"{name}: {path} sha {got[:12]} != {want[:12]}")
    if OUT.exists():
        shutil.rmtree(OUT, onerror=_rw)
    OUT.mkdir(parents=True)
    doc = json.loads(BASIS_JSON.read_text(encoding="utf-8"))
    photos = {n: Path(r["image"]) for n, r in doc["images"].items()}
    jobs, timings = {}, {}
    try:
        for name, (_l, path, _w) in MODELS.items():
            t = time.perf_counter()
            jobs[name] = open_job(name, path, photos)
            timings[f"start_{name}"] = round(time.perf_counter() - t, 1)
        # one crop per photo: the union of the three models' temple crops (host raster of each export)
        boxes = {}
        for name, (_s, see) in jobs.items():
            bs = C.load_basis(see.root)
            exp = see.export_head()
            mesh = see.fit_mesh(exp["path"], exp["sha256"], skip=bs.skip, frame=bs.frame)
            roles = PO.part_roles(exp["path"])
            for rid in PHOTOS:
                g = Seeing._matched_geometry(bs, rid, mesh, roles)
                boxes.setdefault(rid, []).append(C.crop_box(g["temple"], bs.matte(rid)))
        crops = {}
        for rid, bl in boxes.items():
            box = union_box(bl)
            crops[rid] = {"box": list(box), "k": min(1.0, CELL_MAX_PX / max(box[2] - box[0], box[3] - box[1]))}
        # the original first: its gains and photo stretches are shared by the others
        results, reports = {}, {}
        t = time.perf_counter()
        r = jobs["original"][1].compare_to_photos(list(PHOTOS), passes=("native_linear", "gloss_black"),
                                                  rig=rig, boxes=crops)
        assert r["status"] == "ok", r
        timings["compare_original"] = round(time.perf_counter() - t, 1)
        results["original"] = r
        reports["original"] = json.loads(Path(r["report"]).read_text(encoding="utf-8"))
        gains = reports["original"]["gains"]
        env = reports["original"]["environment"]
        for rid in PHOTOS:
            crops[rid]["stretch"] = reports["original"]["photos"][rid]["stretch"]
        for name in ("c0003", "preferred"):
            t = time.perf_counter()
            r = jobs[name][1].compare_to_photos(list(PHOTOS), passes=("native_linear", "gloss_black"),
                                                rig=rig, boxes=crops, gains=gains, env=env)
            assert r["status"] == "ok", r
            timings[f"compare_{name}"] = round(time.perf_counter() - t, 1)
            results[name] = r
            reports[name] = json.loads(Path(r["report"]).read_text(encoding="utf-8"))
        for name, (s, _see) in jobs.items():
            assert s.fingerprint() == s.head["fingerprint"], f"{name}: the compare moved the scene"
    finally:
        for s, _see in jobs.values():
            s.close()
    pages = []
    for rid in PHOTOS:
        recs = {name: reports[name]["photos"][rid] for name in MODELS}
        o = recs["original"]
        pose = o["pose"]
        title = (f"{rid} ({o['view_kind']}, {o['colourway']}): pose +1 {pose['+1']['theta_deg']:.1f} deg, "
                 f"-1 {pose['-1']['theta_deg']:.1f} deg; crop x{o['crop']['k']:.2f}; photo stretch "
                 f"{o['stretch'][0]:.0f}-{o['stretch'][1]:.0f} L8")
        p = OUT / f"{rid}.png"
        sheet(rid, recs, title).save(p)
        pages.append({"photo": rid, "png": p.name, "title": title,
                      "text": {name: recs[name]["text"] for name in MODELS},
                      "temple_iou": {name: round(recs[name]["temple_iou"], 3) for name in MODELS},
                      "outer_edge_offsets_mm": {name: recs[name]["outer_edge_offsets_mm"] for name in MODELS}})
    rig_spec = reports["original"]["photos"][PHOTOS[0]]["render"]["rig_spec"]
    build = json.loads(LC.BUILD.read_text(encoding="utf-8"))
    facts = {"env_calibration_photos": [c["photo"] for c in (env or {}).get("calibration") or []],
             "gain": {k: gains["colourways"]["target"][k] for k in ("photo", "region")},
             "stretch_factor": {rid: 255.0 / (c["stretch"][1] - c["stretch"][0]) for rid, c in crops.items()},
             "outer_edge_offsets_mm": edge_offsets(pages),
             "measures": {name: LC.numbers(OUT / f"job_{name}", reports[name], build) for name in MODELS}}
    summary ={"question": QUESTION, "created": C._utc(), "facts": facts,
               "inputs": {name: {"label": l, "path": str(pth), "sha256": w} for name, (l, pth, w) in MODELS.items()},
               "basis": {"path": str(BASIS_JSON), "sha256": sha(BASIS_JSON)},
               "rig": {"name": rig, "spec": rig_spec}, "environment": env, "gains": gains, "crops": crops,
               "compare_ids": {name: r["compare_id"] for name, r in results.items()},
               "geometry": {name: rep["geometry"] for name, rep in reports.items()},
               "pages": pages, "timings_s": {**timings, "total": round(time.perf_counter() - t0, 1)}}
    C.write_json(OUT / "summary.json", summary)
    if rig == "studio_dome_v1":
        write_index(summary)
    else:
        write_index_env(summary)
    return summary


def edge_offsets(pages: list) -> dict:
    """Per photo with outer-edge offsets (photo minus model, mm; < 0 = the model's edge sits outside the photo's):
    the median of every model and side, their range and the median of all stations pooled."""
    out = {}
    for pg in pages:
        per, pooled = {}, []
        for name, offs in (pg.get("outer_edge_offsets_mm") or {}).items():
            for side, rows in (offs or {}).items():
                vals = [o["offset_mm"] for o in rows if o["offset_mm"] is not None]
                if vals:
                    per.setdefault(name, {})[str(side)] = float(np.median(vals))
                    pooled += vals
        if per:
            meds = [v for sides in per.values() for v in sides.values()]
            out[pg["photo"]] = {"per_model_side": per, "range": [min(meds), max(meds)],
                                "pooled_median": float(np.median(pooled)), "outside": int(sum(v < 0 for v in meds)),
                                "count": len(meds)}
    return out


def _mm(lo: float, hi: float) -> str:
    a, b = sorted((abs(lo), abs(hi)))
    return f"{a:.1f} mm" if f"{a:.1f}" == f"{b:.1f}" else f"{a:.1f}-{b:.1f} mm"


def offsets_clause(rec: dict) -> str:
    """Where the models' outer edges sit against the photo's, from one photo's edge_offsets record: one clause when
    every model and side agrees, else per model (sides that disagree are listed)."""
    per = rec["per_model_side"]
    meds = [v for sides in per.values() for v in sides.values()]
    sides = "both sides" if all(len(s) == 2 for s in per.values()) else "every side measured"
    if rec["outside"] in (0, rec["count"]):
        word = "outside" if rec["outside"] else "inside"
        return f"{word} the photo's on every model and {sides}, by {_mm(min(meds), max(meds))}"
    groups = {}
    for name, s in per.items():
        label = MODELS[name][0].split(" (")[0] if name in MODELS else name
        v = list(s.values())
        if all(x < 0 for x in v) or all(x >= 0 for x in v):
            groups.setdefault("outside" if v[0] < 0 else "inside", []).append(f"{label} ({_mm(min(v), max(v))})")
        else:
            groups.setdefault("mixed", []).append(label + " (" + ", ".join(
                f"side {k} {abs(x):.1f} mm {'outside' if x < 0 else 'inside'}" for k, x in sorted(s.items())) + ")")
    parts = [f"{w} on {' and '.join(groups[w])}" for w in ("inside", "outside") if w in groups]
    if "mixed" in groups:
        parts.append("split on " + " and ".join(groups["mixed"]))
    return ", and ".join(parts)


def write_index(summary: dict, out: Path | None = None) -> None:
    rows = []
    for pg in summary["pages"]:
        rows.append(f"<section><h2>{html.escape(pg['title'])}</h2>"
                    f"<img src=\"{html.escape(pg['png'])}\" alt=\"{html.escape(pg['photo'])}\"></section>")
    g = summary["gains"]
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Owner sheet 1</title>
<style>
:root {{ --bg: #ffffff; --fg: #1a1a1a; --muted: #555; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg: #16181b; --fg: #e8e8e8; --muted: #a0a0a0; }} }}
body {{ background: var(--bg); color: var(--fg); font: 15px/1.45 system-ui, sans-serif; margin: 0 16px 48px; }}
h1 {{ font-size: 22px; }} h2 {{ font-size: 16px; margin-top: 28px; }}
p {{ color: var(--muted); max-width: 70em; }}
img {{ max-width: 100%; height: auto; border: 1px solid #8884; background: #fff; }}
</style></head><body>
<h1>{html.escape(summary['question'])}</h1>
<p>Columns: the photo, then the original model, Astra's c0003 and the preferred v1 build, each rendered at the
photo's fitted camera with the temples opened to the photo's measured angle. Row 1: the model's own material, brightness
matched to the photos with one gain from the front ({g['colourways']['target']['region']}, photo
{g['colourways']['target']['photo']}) and the photo's own contrast stretch on both. Row 2: the same geometry in black
glossy acetate, which shows the facets' reflections; it is exposed like the black photo (black-side). All three models
share the same gains, crops and stretches, so the columns differ only by shape.</p>
{''.join(rows)}
</body></html>
"""
    (Path(out or OUT) / "index.html").write_text(page, encoding="utf-8")


def limits_env(summary: dict) -> list[str]:
    """The cover's known limits, written from the run's own measurements (summary ``facts``); a limit whose numbers
    are missing is left out."""
    f = summary.get("facts") or {}
    out = []
    cal = f.get("env_calibration_photos") or []
    if cal:
        out.append("The lighting is estimated from the photos themselves: a smooth surrounding light that turns with "
                   "the camera (as on a turntable), fitted to the FRONT of the frame in photos "
                   f"{', '.join(c[-2:] for c in cal)} only. The temples were never used to fit it, so how the temples "
                   "look here is a fair test of the lighting.")
    g = f.get("gain") or {}
    if g:
        out.append(f"The colour is matched to photo {g['photo'][-2:]}'s front ({g['region']}), channel by channel. "
                   "The black-side photo is a different colourway: it shows only the black glossy row.")
    sf = f.get("stretch_factor") or {}
    if "reference-05" in sf and "reference-01" in sf:
        out.append("Side photos 05 and 01: the contrast stretch magnifies brightness differences "
                   f"{sf['reference-05']:.1f} and {sf['reference-01']:.1f} times, so small brightness differences "
                   "on the near-white temples look large.")
    m = (f.get("measures") or {}).get("preferred") or {}
    fac, step = m.get("facets") or {}, m.get("upper_crease_step_raw") or {}
    rho = [(rid, (fac.get(rid) or {}).get("spearman")) for rid in ("reference-05", "reference-01", "black-side")]
    if all(r is not None for _rid, r in rho):
        out.append("Measured on the preferred build, the order of the temple facets from dark to bright agrees with "
                   "the photo's by " + ", ".join(f"{r:+.2f} at {rid.replace('reference-', '')}" for rid, r in rho)
                   + " (+1 = the same order, -1 = reversed).")
    if "reference-05" in step and "reference-01" in step:
        out.append("The logo triangle against the band above it (0-255 brightness, before the stretch): "
                   + "; ".join(f"photo {step[rid]['photo']:+.0f}, render {step[rid]['render']:+.0f} at {rid[-2:]}"
                               for rid in ("reference-05", "reference-01")) + ".")
    bsf = fac.get("black-side") or {}
    if bsf.get("photo_L8"):
        out.append(f"Black-side: on the black photo the preferred build's temple facets span "
                   f"{min(bsf['photo_L8']):.0f}-{max(bsf['photo_L8']):.0f} (0-255); its render spans "
                   f"{min(bsf['render_L8']):.0f}-{max(bsf['render_L8']):.0f}. The PRADA / MILANO lettering renders "
                   "gold in the black glossy row, while the photo's lettering is white: ignore the lettering there.")
    out.append("The lenses render light grey or lavender (the bright surroundings show through them); in the photos "
               "they are dark. Ignore the lenses.")
    off = f.get("outer_edge_offsets_mm") or edge_offsets(summary.get("pages") or [])
    if "reference-06" in off:
        rear, top = off["reference-06"], off.get("reference-03")
        text = (f"Outer-edge offsets (medians per model and side): in photo 06 (rear) the models' temple outer edges "
                f"sit {offsets_clause(rear)}")
        text += f"; in photo 03 (top) they sit {offsets_clause(top)}." if top is not None else "."
        rear_abs = [abs(v) for s in rear["per_model_side"].values() for v in s.values()]
        top_abs = [abs(v) for s in (top or {}).get("per_model_side", {}).values() for v in s.values()]
        if rear["outside"] in (0, rear["count"]) and top_abs and max(top_abs) < min(rear_abs):
            text += (" At 06 every model and side is off the same way, and by more than any model at 03, so the 06 "
                     "offset is most likely the rear camera, not the shape.")
        else:
            text += " These numbers do not separate the rear camera from the shape."
        out.append(text)
    return out


def write_index_env(summary: dict, out: Path | None = None) -> None:
    rows = []
    for pg in summary["pages"]:
        rows.append(f"<section><h2>{html.escape(pg['title'])}</h2>"
                    f"<img src=\"{html.escape(pg['png'])}\" alt=\"{html.escape(pg['photo'])}\"></section>")
    g = summary["gains"]
    tg = g["colourways"]["target"]
    limits = "".join(f"<li>{html.escape(t)}</li>" for t in limits_env(summary))
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Owner sheet 1b</title>
<style>
:root {{ --bg: #ffffff; --fg: #1a1a1a; --muted: #555; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg: #16181b; --fg: #e8e8e8; --muted: #a0a0a0; }} }}
body {{ background: var(--bg); color: var(--fg); font: 15px/1.45 system-ui, sans-serif; margin: 0 16px 48px; }}
h1 {{ font-size: 22px; }} h2 {{ font-size: 16px; margin-top: 28px; }}
p, li {{ color: var(--muted); max-width: 70em; }}
img {{ max-width: 100%; height: auto; border: 1px solid #8884; background: #fff; }}
</style></head><body>
<h1>{html.escape(summary['question'])}</h1>
<p>Each image: the photo on the left, then the original model, Astra's c0003 and the preferred v1 build, each rendered
from the photo's own camera with the temples opened to the photo's measured angle, in lighting estimated from the
photos. All three models share the same lighting, colour balance, crops and contrast stretch, so the columns differ
only by shape.</p>
<ul>
<li><b>Row 1 (native):</b> the model's own ivory material, colour-balanced to photo {html.escape(tg['photo'])}'s
front ({html.escape(tg['region'])}), with the photo's own contrast stretch applied to photo and render alike.</li>
<li><b>Row 2 (gloss_black):</b> the same shapes in black glossy acetate, which shows every facet as a reflection;
exposed like the black photo (black-side, one brightness gain over its temple).</li>
</ul>
<h2>Known limits</h2>
<ul>{limits}</ul>
{''.join(rows)}
</body></html>
"""
    (Path(out or OUT) / "index.html").write_text(page, encoding="utf-8")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--rig", default="studio_dome_v1", choices=tuple(OUTS))
    ap.add_argument("--out", default=None, help="output folder (default: data/wp6v2/sheet1 or sheet1b)")
    ap.add_argument("--cover", default=None, help="rewrite DIR/index.html from DIR/summary.json only (no worker)")
    a = ap.parse_args()
    if a.cover:
        d = Path(a.cover)
        sm = json.loads((d / "summary.json").read_text(encoding="utf-8"))
        (write_index if sm["rig"]["name"] == "studio_dome_v1" else write_index_env)(sm, d)
        print(json.dumps(limits_env(sm) if sm["rig"]["name"] != "studio_dome_v1" else [], indent=1))
    else:
        s = main(a.rig, a.out)
        print(json.dumps({"timings_s": s["timings_s"], "pages": [p["png"] for p in s["pages"]]}, indent=1))
