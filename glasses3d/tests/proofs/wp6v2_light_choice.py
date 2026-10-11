"""Lighting choice sheet (WP6_FACETS §3.2 amended 2026-10-11): the owner chooses the rig by eye.

Three rigs, each a frozen candidate: ``photo_env`` (the environment fitted on the ORIGINAL model's front acetate,
data/wp6v2/light_fit/job_original/work/compare/env-basis-01.json, weights sha b245643a9d48710f, never refitted here),
``studio_dome_v1`` and ``catalog_band_v1`` (the catalogue horizon-band prior, g3d/render.py CATALOG_SPEC).

Two models, each a byte copy in its own worker job with the WP6 v2 pose basis (data/wp6v2/basis):
- PREFERRED: the owner-preferred v1 build data/wp6/preferred_goal_20261010/photo_proof_coverage_checked/preferred/p3/
  prada_tool_test_faceted.blend (sha 45964428...), page 1 (index.html);
- ORIGINAL: the accepted Prada Studio scene (sha 56274deb...), page 2 (original.html), so the owner can see the light
  is not shape-specific.

Each model is compared with the photo-matched compare (agent/seeing.py) at 01-06 (native_linear: white-balanced
per-channel gain from 04's front acetate, under that rig) and black-side (gloss_black only: the black photo is the
variant colourway). Per rig the gains are the ORIGINAL's, shared with the preferred; per photo the crop (the whole
object, the union of both models' boxes, cells <= CELL_MAX_PX, never upscaled) and the photo's acetate p2-p98
stretch are shared by every rig and model, so the panels of a row differ only by the light. Each PNG is PHOTO | A | B
| C; the rig -> letter mapping is drawn once by a fixed seed and written only to key.json (never on an image or a
page).

The lenses are one fixed material in every panel: the jobs' renders (gains included) give every lens-role copy
g3d/render.py's LENS_MATERIAL (``dark_tint_v1``, unlit emission: no rig's reflections or transmission reach it), so
the owner compares frames, not lenses (the scene's own lens showed a white band under one rig and lavender stripes
under another). It is dark enough to sit below every stretch floor after any of the three gains, so the lens pixels of
a row are identical across A, B and C; run.json ``lens_check`` measures that on the panels (lens mask from the host
raster, eroded LENS_ERODE_PX). This override is for this sheet only.

Numbers per rig and model (information only, never shown on the pages; numbers.json), measured on float composites
with the gain only (no stretch), on the facet polygons of the preferred spec cfc11127 built by the v1 builder
(data/wp6v2/t2/base/build.json, T2), posed and projected at each crop camera, restricted to the visible temple body
and eroded ERODE_PX (for the original model the same polygons are image regions):
- facet brightness order: Spearman of the per-facet median luma, photo vs render, at black-side (gloss_black) and
  at 05 / 01 (native; ``side_spearman``);
- the upper-crease step (F2 - F1, bands 3-20 px either side of their shared edge) at 05 / 01 and black-side, photo
  and render;
- per photo the temple bodies' median |render - photo| luma (L8; tests/proofs/wp6v2_light_fit.py's "temples").
They are held-out except where IN_SAMPLE says otherwise (numbers.json ``in_sample``): catalog_band_v1's black-side
values (facet order and logo step) are IN-SAMPLE. Its knots were declared from the black photo's logo panel against
the facet mirroring about -2 deg, at the preferred build's mirror angles (g3d/render.py, the CATALOG_SPEC comment), so
on the preferred build they match by construction and on the original they come from the same photo; they are not
evidence for the light. The black-side gloss gain is fitted on the original's largest acetate region, its temple: on
the original page the black-side levels are partly in-sample for every rig (the Spearman is not affected by a gain).

``--calibrate`` measures the front acetate gain of catalog_band_v1 as currently frozen (one fresh ORIGINAL job, the
scene's own lenses) and writes calibration.json; world_strength in g3d/render.py is chosen once from it so that gain
is ~1.

Writes data/wp6v2/light_choice/{index.html, original.html, <model>_<photo>.png, key.json, numbers.json, run.json}
and owner/ (only the two pages and the 14 PNGs, the folder to show the owner).
``--numbers`` recomputes numbers.json from a finished run (host only; each report's own head export, its ``glb``).
Run: glasses3d/.venv/Scripts/python.exe -m glasses3d.tests.proofs.wp6v2_light_choice [--calibrate | --numbers]
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import random
import shutil
import stat
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy import stats

from glasses3d.agent.journal import BlenderSession
from glasses3d.agent.seeing import Seeing
from glasses3d.g3d_host import compare as C
from glasses3d.g3d_host import fitmesh
from glasses3d.g3d_host import pose as PO
from glasses3d.g3d_host.camera import Camera
from glasses3d.tests.proofs.wp6v2_rig_check import bands, facet_labels

G3D = Path(__file__).resolve().parents[2]
REPO = G3D.parent
DATA = G3D / "data"
OUT = DATA / "wp6v2" / "light_choice"
BASIS_JSON = DATA / "wp6v2" / "basis" / "cameras.json"
MODELS = {
    "preferred": ("PREFERRED (v1 build)", DATA / "wp6/preferred_goal_20261010/photo_proof_coverage_checked/preferred/"
                                               "p3/prada_tool_test_faceted.blend",
                  "45964428a9d38910048477cd037a04f6cec0b17531136ab227bed4a354826e90"),
    "original": ("ORIGINAL", REPO / "automation/data/blender_agent/studio/jobs/8c82d986e6c14d26b4b38f3807712ea0/agent/"
                                    "scene.blend", "56274debb901fb28dea1f93f0a6b6ae8401501f7145573f8dedb68c7d2436c06"),
}
PAGES = {"preferred": "index.html", "original": "original.html"}
ENV = DATA / "wp6v2" / "light_fit" / "job_original" / "work" / "compare" / "env-basis-01.json"
ENV_WEIGHTS_SHA = "b245643a9d48710f"
SPEC = DATA / "wp6/pose_arm_resume_20261010/distal_all_views/before/spec.json"
SPEC_SHA = "cfc11127d085c4a642f4a77fb2d6daae676470ffabe699af55b16fc49ec908ce"
BUILD = DATA / "wp6v2" / "t2" / "base" / "build.json"
RIGS = ("photo_env", "studio_dome_v1", "catalog_band_v1")
PHOTOS = ("reference-01", "reference-02", "reference-03", "reference-04", "reference-05", "reference-06", "black-side")
STEP_PHOTOS = ("reference-05", "reference-01")
SEED = 20261011
CELL_MAX_PX = 720
ERODE_PX = 10
MIN_FACET_PX = 200
LENS_MATERIAL = "dark_tint_v1"       # g3d/render.py LENS_MATERIALS
LENS_ERODE_PX = 2
OWNER = OUT / "owner"
QUESTION = "Which light (A, B or C) makes the glasses look most like the photos?"
IN_SAMPLE = {"catalog_band_v1": {
    "values": ["blackside_spearman", "facets.black-side", "upper_crease_step_raw.black-side"],
    "why": "catalog_band_v1's knots were declared from the black photo (black-side): its logo panel (mirror direction "
           "~+2.8 deg) ~3x brighter than the facet mirroring ~-2 deg, at the preferred build's mirror angles "
           "(g3d/render.py, the CATALOG_SPEC comment). On the preferred build these values match by construction; on "
           "the original they come from the same photo. They are not evidence for the light."}}


def sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _rw(func, path, _exc):
    os.chmod(path, stat.S_IWRITE)
    func(path)


def letters() -> dict:
    """{rig: letter}: one shuffle of RIGS by the fixed SEED, shared by every photo and both pages."""
    order = list(RIGS)
    random.Random(SEED).shuffle(order)
    return {rig: "ABC"[i] for i, rig in enumerate(order)}


class TintedSeeing(Seeing):
    """Seeing whose worker renders (compares and gains) give every lens-role copy LENS_MATERIAL, for this sheet only.
    The compare cache key does not carry it: every job here is fresh (open_job removes its root), so no cached
    compare can hold a render without it, and main() checks every panel's render record for it."""

    def _render_params(self, rig: str, env: dict | None, light_frame: dict | None) -> dict:
        return {**super()._render_params(rig, env, light_frame), "lens_material": LENS_MATERIAL}


def open_job(name: str, blend: Path, photos: dict, tag: str = "", *, tinted: bool = False):
    root = OUT / f"job_{name}{tag}"
    if root.exists():
        shutil.rmtree(root, onerror=_rw)
    root.mkdir(parents=True)
    seed = root / "seed.blend"
    shutil.copy2(blend, seed)
    os.chmod(seed, stat.S_IWRITE | stat.S_IREAD)
    if sha(seed) != MODELS[name][2]:
        raise RuntimeError(f"{name}: the job's copy differs from {blend}")
    C.import_cameras_json(root, BASIS_JSON, photos, reason="lighting choice sheet")
    s = BlenderSession(root, seed_blend=seed, calls_dir=root / "tools")
    s.start()
    return s, (TintedSeeing if tinted else Seeing)(s)


def head(see: Seeing) -> tuple:
    bs = C.load_basis(see.root)
    exp = see.export_head()
    assert exp["status"] == "exported", exp
    mesh = see.fit_mesh(exp["path"], exp["sha256"], skip=bs.skip, frame=bs.frame)
    return bs, exp, mesh, PO.part_roles(exp["path"])


def calibrate(photos: dict) -> dict:
    """The catalog_band_v1 front acetate gain (04, per channel and its luminance) at the frozen world_strength."""
    s, see = open_job("original", MODELS["original"][1], photos, "_calibrate")
    try:
        bs, _exp, mesh, roles = head(see)
        origin = np.asarray(see.bounds("cal")["origin_mm"] or (0.0, 0.0, 0.0), float)
        out_dir = see._prepare_work_dir(see.work / "calibrate")
        g = see._matched_gains(bs, mesh, roles, "catalog_band_v1", origin, "cal", out_dir, C.geometry_key(mesh))
        assert s.fingerprint() == s.head["fingerprint"]
    finally:
        s.close()
    tg = g["colourways"]["target"]
    rec = {"rig": "catalog_band_v1", "photo": tg["photo"], "region": tg["region"],
           "native_gain_rgb": tg["native_linear"]["gain"], "native_gain_Y": tg["native_linear"]["gain_Y"],
           "gloss_gain": g["gloss_black"]["gain"], "created": C._utc(),
           "rule": "world_strength_new = world_strength_used x native_gain_Y (EEVEE world light scales linearly)"}
    C.write_json(OUT / "calibration.json", rec)
    return rec


def full_box(g: dict, matte: np.ndarray) -> tuple:
    return C._box(g["mask"] | matte, C.CROP_PAD, g["shape"]) or (0, 0, g["shape"][1], g["shape"][0])


def job_head(root: Path, report: dict) -> tuple:
    """(basis, host mesh, roles) of the head export a compare report used (its ``glb`` path and sha, checked) and the
    job's basis."""
    bs = C.load_basis(root)
    glb = report["glb"]
    if sha(glb["path"]) != glb["sha256"]:
        raise RuntimeError(f"{glb['path']} is not the export compare {report['compare_id']} used")
    return bs, fitmesh.load_fit_mesh(glb["path"], skip=bs.skip, frame=bs.frame), PO.part_roles(glb["path"])


def numbers(root: Path, report: dict, build: dict) -> dict:
    """The numbers of one model (job ``root``) under one rig (module doc; IN_SAMPLE marks the in-sample ones)."""
    bs, mesh, roles = job_head(root, report)
    gains = report["gains"]
    g_nat = np.asarray(gains["colourways"]["target"]["native_linear"]["gain"], float)
    g_glo = float(gains["gloss_black"]["gain"])
    out = {"temples_median_abs_L8": {}, "gains": {"native_rgb": g_nat.tolist(),
                                                  "native_Y": float(gains["colourways"]["target"]["native_linear"]
                                                                    ["gain_Y"]), "gloss": g_glo}}
    for rid in PHOTOS:
        rec = report["photos"][rid]
        ps = "native_linear" if bs.colourway(rid) == "target" else "gloss_black"
        cam = Camera.from_dict(rec["crop"]["camera"])
        shape = tuple(rec["crop"]["shape"])
        mp = PO.posed(mesh, rec["pose"], roles)
        ras = mp.scene().render(cam, shape, 1)
        part = np.full(shape, -1, np.int64)
        part[ras["mask"]] = mp.part[ras["face_id"][ras["mask"]]]
        pc, mc = Seeing.photo_at(bs, rid, tuple(rec["crop"]["box"]), rec["crop"]["k"], shape)
        rgb, a = C.read_linear(rec["renders"][ps]["path"])
        bg = C.photo_background(bs.photo(rid), bs.matte(rid))
        Lr = C.luma8(C.composite_render(rgb, a, g_nat if ps == "native_linear" else g_glo, bg, as_float=True))
        Lp = C.luma8(pc.astype(float))
        ok = mc & (a > 0.999)
        temples = C.erode_px(np.isin(part, [PO.temple_body(mp, s, roles) for s in (1, -1)]) & ok, 3)
        if temples.sum() >= 100:
            out["temples_median_abs_L8"][rid] = round(float(np.median(np.abs(Lr[temples] - Lp[temples]))), 2)
        if rid not in STEP_PHOTOS + ("black-side",):
            continue
        side = 1 if bs.camera(rid).yaw > 0 else -1
        body = PO.temple_body(mp, side, roles)
        labels = facet_labels(build["sides"][str(side)], rec["pose"], side, cam, bs.frame, shape, (part == body) & ok)
        er = {lab: C.erode_px(m, ERODE_PX) for lab, m in labels.items()}
        regs = [lab for lab in sorted(er) if er[lab].sum() >= MIN_FACET_PX]
        ph = [float(np.median(Lp[er[lab]])) for lab in regs]
        rn = [float(np.median(Lr[er[lab]])) for lab in regs]
        rho = stats.spearmanr(ph, rn).statistic if len(regs) >= 3 else None
        out.setdefault("facets", {})[rid] = {"pass": ps, "regions": regs, "photo_L8": [round(x, 1) for x in ph],
                                             "render_L8": [round(x, 1) for x in rn],
                                             "spearman": None if rho is None else round(float(rho), 3)}
        if "F1" in labels and "F2" in labels:
            b1, b2 = bands(C.erode_px(labels["F1"], 1), C.erode_px(labels["F2"], 1))
            if b1.sum() >= 50 and b2.sum() >= 50:
                out.setdefault("upper_crease_step_raw", {})[rid] = {
                    "pass": ps, "photo": round(float(np.median(Lp[b2]) - np.median(Lp[b1])), 2),
                    "render": round(float(np.median(Lr[b2]) - np.median(Lr[b1])), 2),
                    "band_px": [int(b1.sum()), int(b2.sum())]}
    t = [v for r, v in out["temples_median_abs_L8"].items() if r != "black-side"]
    out["temples_median_abs_L8_01_06_median"] = round(float(np.median(t)), 2) if t else None
    out["blackside_spearman"] = (out.get("facets", {}).get("black-side") or {}).get("spearman")
    out["side_spearman"] = {rid: (out.get("facets", {}).get(rid) or {}).get("spearman") for rid in STEP_PHOTOS}
    return out


def mark_in_sample(nums: dict) -> dict:
    """Each model's record of an IN_SAMPLE rig names its in-sample values (module doc)."""
    for per_rig in nums.values():
        for rig, rec in per_rig.items():
            if rig in IN_SAMPLE:
                rec["in_sample"] = IN_SAMPLE[rig]["values"]
    return nums


def summary_rows(nums: dict) -> dict:
    """{model: {rig: the headline numbers}} (render values; the photo's own values under "photo")."""
    out = {}
    for name, per_rig in nums.items():
        for rig, rec in per_rig.items():
            step = rec.get("upper_crease_step_raw") or {}
            row = {"blackside_spearman": rec.get("blackside_spearman"),
                   **{f"side_spearman_{rid[-2:]}": v for rid, v in (rec.get("side_spearman") or {}).items()},
                   **{f"upper_crease_step_{rid.replace('reference-', '')}": (step.get(rid) or {}).get("render")
                      for rid in STEP_PHOTOS + ("black-side",)},
                   "temples_median_abs_L8_01_06_median": rec.get("temples_median_abs_L8_01_06_median"),
                   "temples_median_abs_L8_black-side": (rec.get("temples_median_abs_L8") or {}).get("black-side"),
                   "in_sample": rec.get("in_sample") or []}
            out.setdefault(name, {})[rig] = row
            out[name].setdefault("photo", {f"upper_crease_step_{rid.replace('reference-', '')}":
                                           (step.get(rid) or {}).get("photo") for rid in STEP_PHOTOS + ("black-side",)})
    return out


def lens_check(root: Path, reps: dict) -> dict:
    """Per photo of one model (job ``root``, reports ``reps`` by rig): every panel render carries LENS_MATERIAL, and
    the panel row images' lens pixels (host raster at the crop camera, eroded LENS_ERODE_PX): their count, the largest
    RGB difference between any two lights and each light's median RGB."""
    rep0 = reps[RIGS[0]]
    bs, mesh, roles = job_head(root, rep0)
    out = {}
    for rid in PHOTOS:
        rec = rep0["photos"][rid]
        ps = "native_linear" if bs.colourway(rid) == "target" else "gloss_black"
        cam = Camera.from_dict(rec["crop"]["camera"])
        shape = tuple(rec["crop"]["shape"])
        mp = PO.posed(mesh, rec["pose"], roles)
        ras = mp.scene().render(cam, shape, 1)
        part = np.full(shape, -1, np.int64)
        part[ras["mask"]] = mp.part[ras["face_id"][ras["mask"]]]
        lens = C.erode_px(np.isin(part, [i for i, n in enumerate(mp.node_names) if roles.get(n) == "lens"]),
                          LENS_ERODE_PX)
        vals, covered = {}, {}
        for rig in RIGS:
            r = reps[rig]["photos"][rid]
            if [r["crop"][k] for k in ("box", "k", "shape")] != [rec["crop"][k] for k in ("box", "k", "shape")]:
                raise RuntimeError(f"{rid}: the {rig} panel has another crop")
            lm = r["renders"][ps].get("lens_material") or {}
            if lm.get("name") != LENS_MATERIAL or not lm.get("objects"):
                raise RuntimeError(f"{rid}/{rig}: the panel render has no {LENS_MATERIAL} lens ({lm})")
            covered[rig] = lm["objects"]
            vals[rig] = np.asarray(Image.open(r["rows"][ps]).convert("RGB")).astype(int)[lens]
        px = int(lens.sum())
        diff = max((int(np.abs(vals[a] - vals[b]).max()) for a in RIGS for b in RIGS), default=0) if px else None
        out[rid] = {"pass": ps, "lens_px": px, "max_abs_diff_between_lights": diff,
                    "median_rgb": {rig: np.median(v, axis=0).tolist() if px else None for rig, v in vals.items()},
                    "lens_objects": covered[RIGS[0]]}
    return out


def owner_folder(pages: dict) -> list:
    """OWNER = exactly the two pages and their PNGs (no key, numbers or run records); returns its file names."""
    if OWNER.exists():
        shutil.rmtree(OWNER, onerror=_rw)
    OWNER.mkdir(parents=True)
    names = [PAGES[name] for name in MODELS] + [r["png"] for name in MODELS for r in pages[name]]
    for n in names:
        shutil.copy2(OUT / n, OWNER / n)
    for name in MODELS:
        text = (OWNER / PAGES[name]).read_text(encoding="utf-8")
        leaks = [w for w in ("key.json", "numbers.json", "run.json", *RIGS) if w in text]
        if leaks:
            raise RuntimeError(f"{PAGES[name]} names {leaks}")
    got = sorted(os.listdir(OWNER))
    assert got == sorted(names), got
    return got


def sheet(rid: str, panels: list, title: str) -> Image.Image:
    g = C.grid(panels, len(panels), max_px=4000, gap=8)
    headr = Image.new("RGBA", (g.width, 34), (255, 255, 255, 255))
    ImageDraw.Draw(headr).text((8, 6), title, fill=(10, 10, 10, 255), font=C._font(18))
    out = Image.new("RGB", (g.width, g.height + headr.height), (255, 255, 255))
    out.paste(headr.convert("RGB"), (0, 0))
    out.paste(g.convert("RGB"), (0, headr.height))
    return out


def main() -> dict:
    t0 = time.perf_counter()
    for name, (_l, path, want) in MODELS.items():
        if sha(path) != want:
            raise RuntimeError(f"{name}: {path} changed")
    env = json.loads(ENV.read_text(encoding="utf-8"))
    assert env["weights_sha"] == ENV_WEIGHTS_SHA, env["weights_sha"]
    assert sha(SPEC) == SPEC_SHA and json.loads((BUILD.parent / "spec.json").read_text(encoding="utf-8")) == \
        json.loads(SPEC.read_text(encoding="utf-8")), "the T2 build is not of spec cfc11127"
    build = json.loads(BUILD.read_text(encoding="utf-8"))
    OUT.mkdir(parents=True, exist_ok=True)
    doc = json.loads(BASIS_JSON.read_text(encoding="utf-8"))
    photos = {n: Path(r["image"]) for n, r in doc["images"].items()}
    key = letters()
    jobs, timings, reports, results = {}, {}, {}, {}
    try:
        for name, (_l, path, _w) in MODELS.items():
            t = time.perf_counter()
            jobs[name] = open_job(name, path, photos, tinted=True)
            timings[f"start_{name}"] = round(time.perf_counter() - t, 1)
        boxes = {}
        for name, (_s, see) in jobs.items():
            bs, _exp, mesh, roles = head(see)
            for rid in PHOTOS:
                g = Seeing._matched_geometry(bs, rid, mesh, roles)
                boxes.setdefault(rid, []).append(full_box(g, bs.matte(rid)))
        crops = {}
        for rid, bl in boxes.items():
            a = np.array(bl)
            box = (int(a[:, 0].min()), int(a[:, 1].min()), int(a[:, 2].max()), int(a[:, 3].max()))
            crops[rid] = {"box": list(box), "k": min(1.0, CELL_MAX_PX / max(box[2] - box[0], box[3] - box[1]))}
        for rig in RIGS:
            kw = {"env": env} if rig == "photo_env" else {}
            t = time.perf_counter()
            r = jobs["original"][1].compare_to_photos(list(PHOTOS), passes=("native_linear",), rig=rig,
                                                      crop="full", boxes=crops, **kw)
            assert r["status"] == "ok", r
            rep = json.loads(Path(r["report"]).read_text(encoding="utf-8"))
            timings[f"original_{rig}"] = round(time.perf_counter() - t, 1)
            if "stretch" not in crops[PHOTOS[0]]:                       # the first compare's photo stretches
                for rid in PHOTOS:
                    crops[rid]["stretch"] = rep["photos"][rid]["stretch"]
            reports[("original", rig)], results[("original", rig)] = rep, r
            t = time.perf_counter()
            r = jobs["preferred"][1].compare_to_photos(list(PHOTOS), passes=("native_linear",), rig=rig,
                                                       crop="full", boxes=crops, gains=rep["gains"], **kw)
            assert r["status"] == "ok", r
            reports[("preferred", rig)] = json.loads(Path(r["report"]).read_text(encoding="utf-8"))
            results[("preferred", rig)] = r
            timings[f"preferred_{rig}"] = round(time.perf_counter() - t, 1)
        nums = {}
        for (name, rig), rep in reports.items():
            nums.setdefault(name, {})[rig] = numbers(jobs[name][1].root, rep, build)
        lens = {name: lens_check(jobs[name][1].root, {rig: reports[(name, rig)] for rig in RIGS}) for name in MODELS}
        for name, (s, _see) in jobs.items():
            assert s.fingerprint() == s.head["fingerprint"], f"{name}: the compare moved the scene"
    finally:
        for s, _see in jobs.values():
            s.close()
    # the first compare (original, RIGS[0]) chose the stretch inside its own call: every later one was given it
    for (name, rig), rep in reports.items():
        for rid in PHOTOS:
            assert rep["photos"][rid]["stretch"] == crops[rid]["stretch"], (name, rig, rid)
    unequal = {f"{name}/{rid}": c["max_abs_diff_between_lights"] for name, per in lens.items()
               for rid, c in per.items() if c["max_abs_diff_between_lights"]}
    by_letter = {v: k for k, v in key.items()}
    pages = {}
    for name in MODELS:
        rows = []
        for rid in PHOTOS:
            ps = "native_linear" if rid != "black-side" else "gloss_black"
            recs = {rig: reports[(name, rig)]["photos"][rid] for rig in RIGS}
            panels = [C.labelled(Image.open(recs[RIGS[0]]["rows"]["photo"]), f"PHOTO {rid}", 18)]
            for letter in "ABC":
                panels.append(C.labelled(Image.open(recs[by_letter[letter]]["rows"][ps]), letter, 18))
            what = "the black photo; the same shapes in black glossy acetate" if rid == "black-side" else \
                "the photo, then the same model under lights A, B and C"
            p = OUT / f"{name}_{rid}.png"
            sheet(rid, panels, f"{rid}: {what}").save(p)
            rows.append({"photo": rid, "png": p.name})
        pages[name] = rows
        write_page(name, rows)
    C.write_json(OUT / "key.json", {
        "seed": SEED, "letters": {letter: by_letter[letter] for letter in "ABC"}, "rigs": list(RIGS),
        "note": "the rig -> letter mapping of every PNG and both pages; never shown on them"})
    rig_specs = {rig: reports[("original", rig)]["photos"][PHOTOS[0]]["render"]["rig_spec"] for rig in RIGS}
    write_numbers(nums)
    run = {"created": C._utc(), "question": QUESTION,
           "inputs": {name: {"label": l, "path": str(pth), "sha256": w} for name, (l, pth, w) in MODELS.items()},
           "basis": {"path": str(BASIS_JSON), "sha256": sha(BASIS_JSON)},
           "photo_env": {"path": str(ENV), "file_sha256": sha(ENV), "weights_sha": env["weights_sha"],
                         "lambda": env["lambda"], "fitted_on": "the ORIGINAL model's front acetate (02/03/04/06)"},
           "facets_from": {"build": str(BUILD), "build_sha256": sha(BUILD), "spec_sha256": SPEC_SHA},
           "rig_specs": rig_specs, "crops": crops,
           "compare_ids": {f"{n}/{r}": res["compare_id"] for (n, r), res in results.items()},
           "gains": {rig: reports[("original", rig)]["gains"] for rig in RIGS}, "pages": pages,
           "lens_material": {"name": LENS_MATERIAL, "spec": "g3d/render.py LENS_MATERIALS",
                             "applied_to": "every worker render of both jobs (gains and compares)",
                             "erode_px": LENS_ERODE_PX},
           "lens_check": lens, "owner_folder": {"path": str(OWNER), "files": owner_folder(pages)},
           "timings_s": {**timings, "total": round(time.perf_counter() - t0, 1)}}
    C.write_json(OUT / "run.json", run)
    if unequal:
        raise RuntimeError(f"the lens differs between the lights in {unequal} (run.json lens_check)")
    return {"run": run, "numbers": nums, "key": key}


def write_numbers(nums: dict) -> None:
    nums = mark_in_sample(nums)
    C.write_json(OUT / "numbers.json", {
        "note": "information only (module doc), never shown on the pages; held-out except the values each record's "
                "in_sample lists (why: in_sample_why)",
        "in_sample_why": {rig: rec["why"] for rig, rec in IN_SAMPLE.items()},
        "lenses": f"every render here gives the lenses {LENS_MATERIAL} (g3d/render.py LENS_MATERIALS); the numbers "
                  "are on the temples",
        "summary": summary_rows(nums), "numbers": nums,
        "photo_values_note": "the photo values are the same for every rig (one photo, one crop)"})


def renumber() -> dict:
    """numbers.json again from a finished run (run.json's compare reports; host only, no worker)."""
    run = json.loads((OUT / "run.json").read_text(encoding="utf-8"))
    build = json.loads(BUILD.read_text(encoding="utf-8"))
    nums = {}
    for key, cid in run["compare_ids"].items():
        name, rig = key.split("/")
        root = OUT / f"job_{name}"
        report = json.loads((root / "work" / "compare" / cid / "report.json").read_text(encoding="utf-8"))
        nums.setdefault(name, {})[rig] = numbers(root, report, build)
    write_numbers(nums)
    return nums


NOTES = (
    "In every row the three panels show the same model, crop and contrast, each colour-balanced to the front of "
    "photo 04; only the light differs.",
    "The black-side row: the model is ivory, so it is shown in black glossy acetate beside the black photo, its "
    "brightness matched to that photo.",
    "The lenses are drawn in one fixed dark tint that does not react to light, identical in A, B and C (for this page "
    "only), so the lights differ only on the frame. Judge the frame and the temples.",
    "The PRADA / MILANO lettering is a separate open item (its colour does not follow the photos'): ignore it.",
)


def write_page(name: str, rows: list) -> None:
    other = "original" if name == "preferred" else "preferred"
    imgs = "".join(f"<section><h2>{html.escape(r['photo'])}</h2><img src=\"{html.escape(r['png'])}\" "
                   f"alt=\"{html.escape(r['photo'])}: photo, A, B, C\"></section>" for r in rows)
    notes = "".join(f"<li>{html.escape(t)}</li>" for t in NOTES)
    lead = ("These glasses are the preferred faceted build." if name == "preferred" else
            "Page 2: the same three lights on the original model, to show the light is not tied to one shape.")
    link = (f"<p><a href=\"{PAGES[other]}\">Page 2: the same lights on the original model</a></p>"
            if name == "preferred" else f"<p><a href=\"{PAGES[other]}\">Back to page 1</a></p>")
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Light choice</title>
<style>
:root {{ --bg: #ffffff; --fg: #1a1a1a; --muted: #555; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg: #16181b; --fg: #e8e8e8; --muted: #a0a0a0; }} }}
body {{ background: var(--bg); color: var(--fg); font: 15px/1.45 system-ui, sans-serif; margin: 0 16px 48px; }}
h1 {{ font-size: 22px; }} h2 {{ font-size: 16px; margin-top: 28px; }}
p, li {{ color: var(--muted); max-width: 70em; }}
img {{ max-width: 100%; height: auto; border: 1px solid #8884; background: #fff; }}
</style></head><body>
<h1>{html.escape(QUESTION)}</h1>
<p>{html.escape(lead)} Each image: the photo, then lights A, B and C.</p>
<ul>{notes}</ul>
{link}
{imgs}
{link}
</body></html>
"""
    (OUT / PAGES[name]).write_text(page, encoding="utf-8")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--calibrate", action="store_true", help="measure catalog_band_v1's front gain only")
    ap.add_argument("--numbers", action="store_true", help="recompute numbers.json from the finished run")
    a = ap.parse_args()
    if a.numbers:
        print(json.dumps(renumber(), indent=1))
    elif a.calibrate:
        OUT.mkdir(parents=True, exist_ok=True)
        d = json.loads(BASIS_JSON.read_text(encoding="utf-8"))
        print(json.dumps(calibrate({n: Path(r["image"]) for n, r in d["images"].items()}), indent=1))
    else:
        res = main()
        print(json.dumps({"timings_s": res["run"]["timings_s"], "numbers": res["numbers"]}, indent=1))
