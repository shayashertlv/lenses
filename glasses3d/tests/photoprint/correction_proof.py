"""WP5 correction round (2026-10-06): the before / after measurements of task items 2, 3 and 4 on a written bake.

    glasses3d/.venv/Scripts/python -B -m glasses3d.tests.photoprint.correction_proof --bake DIR [--before DIR]
        [--previous DIR] [--ar NAME=CAPTURE_DIR ...] [--out JSON]

- **Item 2, brow-front coverage** (``brow_front``): the denominator is every texel of the brows' FRONT facing class
  that photo 05 sees at donor angles (first surface past the whole scene within 0.5 mm, cos > 0.5, inside the image).
  Per texel the FIRST failing donor rule, in the order of ``texels.view_samples``: outside the matte (eroded 1 px:
  model-vs-photo silhouette mismatch), not an acetate pixel, clipped, near an occluder, foreign blob, the metal model's
  parts (pale, hardware margin, edge halo, thin line, dark blob, enclosed), the bicubic footprint; then what the bake
  made of the donor-ok texels (classes.png: photo of any view, highlight fill, seam band, fill...). Computed twice on
  the same geometry: with the step-4 donor rules (``rules='step4'``) and with the bake's (``rules='correction'``); the
  final photo share is read from each bake's own classes.png. Sides by x sign.
- **Item 3, streaks** (``streaks``): ``print_metrics.directional_energy`` on the temples' outer and top faces in strip
  space (atlas x = s, y = t): across/along gradient-energy ratio and contrast of photo texels, quilted fill and text
  fill, read back from print.png and classes.png.
- **Item 4, scratches** (``endpiece``): on the endpiece blocks under the T (brow parts, |x| > 52 mm, y < 4 mm), the texels
  painted from photo 05, binned by their photo pixel's distance to the photo's background (edt of the matte); the share
  of bright outliers (luminance > 15 levels over the 9 x 9-texel median of print.png) per bin. Fix round 1 (review
  finding: the y < 4 mm cut missed the T's own height): the same count over the WHOLE endpiece (|x| > 52 mm, every y)
  and at the T's height (y >= 4 mm; also front-facing only), and (``new_photo_outliers``) the bright-outlier share of
  the photo texels a bake ADDED over the ``--before`` bake (photo now, not photo there) against the texels that were
  photo in both, on the brow front and on the endpieces. Every bake is measured on its photo RADIANCE: a bake's
  declared AR level (report.json ``level``) is undone first (the inverse of rgb_lin x gain x Y^(gamma - 1), written
  here), so bakes with different levels compare at the same contrast.
- **AR streaks** (``ar_streaks``, ``--ar``): ``print_metrics.axis_streakiness`` of each temple's pixels (part-id) in the
  beauty render of every listed arview capture at yaw +45 (-x side near) and yaw -30 / -45 (+x side near): the
  across / along gradient-energy ratio along the arm's principal axis (lines along the arm give a large ratio; it is
  dominated by the arm's own edges and sheen, so it is compared between captures of the same model and pose only).
- ``--previous``: an earlier bake (the correction round's, plain footprint) measured beside ``--bake``.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

from glasses3d.g3d import printuv as PU
from glasses3d.g3d_host import raster
from glasses3d.g3d_host import texture_utils as T
from glasses3d.g3d_host.photoprint import inputs as IN
from glasses3d.g3d_host.photoprint import metal as MT
from glasses3d.g3d_host.photoprint import project as PR
from glasses3d.g3d_host.photoprint import regions as RG
from glasses3d.g3d_host.photoprint import signals as S
from glasses3d.g3d_host.photoprint import texels as TX
from glasses3d.g3d_host.photoprint.compose import CLASS_CODES
from glasses3d.g3d_host.photoprint.pose import points
from glasses3d.tests.instruments import print_metrics as PM

FRONT_VIEW = "reference-05.png"
NAMES = {v: k for k, v in CLASS_CODES.items()}
OUTLIER = 15.0                    # a bright outlier: luminance > this many levels over the 9 x 9-texel median
ENDPIECE_X_MM = 52.0
T_Y_MM = 4.0
PHOTO_CODES = (CLASS_CODES["photo_anchor"], CLASS_CODES["photo_tier1"])
REC709 = np.array([0.2126, 0.7152, 0.0722])
AR_STREAK_POSES = ("yaw_p45", "yaw_m30", "yaw_m45")
TEMPLES = {"-x": "Temple_Left", "+x": "Temple_Right"}       # q001's names, selected by the x sign of the part


def _load(bake: Path):
    job = IN.load_job()
    rep = json.loads((bake / "report.json").read_text(encoding="utf-8"))
    tx = float(rep["layout"]["tx_per_mm"])
    lay = PU.layout(job.mesh.V, job.mesh.F, job.mesh.part, job.mesh.names, list(IN.FT1119_PARTS), tx,
                    atlas_max=PR.ATLAS_MAX, mirror=True)
    regions = RG.build_regions(job, list(IN.FT1119_PARTS), RG.rigid_groups(job), n_samples=64)
    tex = TX.atlas_texels(job, lay, regions)
    occ = TX.OcclusionScene(job, job.part_ids(IN.FT1119_PARTS), TX.decal_geometry(PR.job_glb(job), job.mesh.skipped))
    return job, rep, lay, regions, tex, occ, tx


def unlevel(img: np.ndarray, level: dict | None, keep: np.ndarray | None = None) -> np.ndarray:
    """Photo radiance of a levelled print: the inverse of rgb_lin' = rgb_lin x gain x Y^(gamma - 1) (Y' = gain x
    Y^gamma, so Y = (Y' / gain)^(1 / gamma)), written here; ``keep`` pixels (untextured) stay. Float sRGB 0..255."""
    out = np.asarray(img, np.float64).copy()
    if not level or not level.get("applied", True) or level.get("gain") is None:
        return out
    g, gm = float(level["gain"]), float(level["gamma"])
    c = out / 255.0
    lin = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    Yp = np.maximum(lin @ REC709, 1e-6)
    Y = (Yp / g) ** (1.0 / gm)
    lin = np.clip(lin * (Y / Yp)[..., None], 0, 1)
    srgb = np.where(lin <= 0.0031308, 12.92 * lin, 1.055 * np.power(lin, 1 / 2.4) - 0.055) * 255.0
    if keep is not None:
        srgb[keep] = out[keep]
    return srgb


def _imgs(bake: Path):
    out = {"print_levelled": np.asarray(Image.open(bake / "print.png").convert("RGB")),
           "classes": np.asarray(Image.open(bake / "classes.png")),
           "source": np.asarray(Image.open(bake / "source.png"))}
    rep = json.loads((bake / "report.json").read_text(encoding="utf-8"))
    out["level"] = rep.get("level") or {"applied": False}
    out["print"] = unlevel(out["print_levelled"], out["level"], out["classes"] == CLASS_CODES["no_texture"])
    out["bake"] = str(bake)
    return out


def bright_contrast(imgs, tex) -> np.ndarray:
    """(n,) luminance minus its 9 x 9-texel median, of the print's radiance, at every texel."""
    L = np.asarray(imgs["print"], float).mean(-1)
    return (L - ndimage.median_filter(L, size=9))[tex.ii, tex.jj]


def _facing(regions, tex) -> np.ndarray:
    return np.array([r.facing for r in regions] + ["none"], dtype=object)[np.where(tex.region >= 0, tex.region, -1)]


def first_failing(job, tex, occ, view: str, rules: str, footprint_rule: str = "edges"):
    """Per texel the index of the first failing donor rule (len(names) = donor ok, -1 = not in the denominator), the
    rule names and a check. ``footprint_rule``: the bake's footprint ('edges', fix round 1; 'plain', the correction
    round's)."""
    v = job.views[view]
    cam = v.camera
    V = job.mesh.V
    H, W = v.shape
    P, N = points(V, job.mesh.F, tex.face, tex.bary)
    vis, uv = T.visible_points(P, N, cam, job.frame, occ.scene(V), (H, W), min_cos=TX.DONOR_MIN_COS,
                               tol_mm=TX.VIS_TOL_MM)
    inb = (uv[:, 0] > 0.5) & (uv[:, 0] < W - 1.5) & (uv[:, 1] > 0.5) & (uv[:, 1] < H - 1.5)
    ui = np.clip(np.round(uv[:, 0]).astype(int), 0, W - 1)
    vi = np.clip(np.round(uv[:, 1]).astype(int), 0, H - 1)
    s = TX.view_samples(job, tex, view, occ, rules=rules, footprint_rule=footprint_rule)
    img = np.asarray(v.image, np.float64)
    matte = np.asarray(v.matte, bool)
    inside = ndimage.binary_erosion(matte, iterations=1)
    acet = S.acetate_mask(img)
    clip = TX.clipped_mask(v.image)
    fid = occ.scene(V).render(cam, (H, W), 1)["face_id"]
    ppm_v = job.ppm(view, cam)
    ppm = IN.ppm_local(cam, job.frame, P)
    dist = TX.occluder_distance(job, occ, V, cam, (H, W), face_id=fid)
    near = dist[vi, ui] <= TX.OCCLUDER_MARGIN_MM * ppm
    dh = MT.donor_holes(v.image, v.matte, fid, occ.is_print, occ.is_hardware, ppm_v, rules=rules)
    if rules == "correction":
        nonprint = ~inside | ~acet | clip | dh["pale"]
        near &= ndimage.distance_transform_edt(~nonprint)[vi, ui] <= TX.OCCLUDER_MARGIN_MM * ppm
        foot = TX.footprint_holes(v.image, v.matte, ppm_v, glints=dh["bright_lines"], rule=footprint_rule)[vi, ui]
    else:
        foot = np.zeros(len(tex), bool)
    fb = TX.foreign_blobs(v.image, v.matte, (fid >= 0) & occ.is_print[fid], ppm_v, dilate_all=(rules == "step4"))
    g = lambda m: m[vi, ui]
    rules_ = [("outside_matte", ~g(inside)), ("not_acetate_pixel", ~g(acet)), ("clipped", g(clip)),
              ("near_occluder", near), ("foreign_blob", g(fb["mask"])), ("metal.pale", g(dh["pale"])),
              ("metal.hardware_margin", g(dh["hardware"])), ("metal.edge_halo", g(dh["halo"])),
              ("metal.thin_line", g(dh["lines"])), ("metal.dark_blob", g(dh["dark_blob"])),
              ("metal.enclosed", g(dh["enclosed"])), ("footprint", foot)]
    den = vis & inb
    lab = np.full(len(tex), -1, np.int64)
    rem = den.copy()
    for k, (_nm, bad) in enumerate(rules_):
        hit = rem & bad
        lab[hit] = k
        rem &= ~hit
    lab[rem] = len(rules_)
    agree = int((rem != (s.ok & den)).sum())                 # the order here must reproduce view_samples' ok set
    return lab, [nm for nm, _ in rules_], {"donor_ok_disagreements_with_view_samples": agree}


RULES_OF = {"step4": ("step4", "plain"), "correction_plain": ("correction", "plain"),
            "correction": ("correction", "edges")}


def brow_front(job, regions, tex, occ, imgs_by_rules: dict) -> dict:
    """``imgs_by_rules``: {key of RULES_OF: the bake made with those donor rules}."""
    fac = _facing(regions, tex)
    isb = np.isin(tex.part, job.part_ids(["Brow_Left", "Brow_Right"]))
    out = {"denominator": "brow FRONT-facing texels photo 05 sees at donor angles (first surface, cos > 0.5, in image)"}
    for key, imgs in imgs_by_rules.items():
        rules, frule = RULES_OF[key]
        lab, names, chk = first_failing(job, tex, occ, FRONT_VIEW, rules, frule)
        klass = imgs["classes"][tex.ii, tex.jj]
        src = imgs["source"][tex.ii, tex.jj]
        rows = {"check": chk}
        for sg in (-1, 1):
            base = isb & (fac == "front") & (np.sign(tex.P[:, 0]) == sg) & (lab >= 0)
            n = int(base.sum())
            r = {"texels": n, "first_failing_share": {nm: round(float((base & (lab == k)).sum()) / n, 4)
                                                      for k, nm in enumerate(names)}}
            ok = base & (lab == len(names))
            r["donor_ok_share"] = round(float(ok.sum()) / n, 4)
            photo = np.isin(klass, [CLASS_CODES["photo_anchor"], CLASS_CODES["photo_tier1"]])
            r["final_class_share"] = {NAMES[int(c)]: round(float(k) / n, 4)
                                      for c, k in zip(*np.unique(klass[base], return_counts=True))}
            r["final_photo_share"] = round(float((base & photo).sum()) / n, 4)
            r["final_photo_share_from_05"] = round(float((base & photo & (src == 5)).sum()) / n, 4)
            r["donor_ok_but_not_photo"] = {NAMES[int(c)]: round(float(k) / n, 4)
                                           for c, k in zip(*np.unique(klass[ok & ~photo], return_counts=True))}
            r["final_photo_ge_0.80"] = bool(r["final_photo_share"] >= 0.80)
            rows[f"{'+' if sg > 0 else '-'}x"] = r
        rows["bake"] = imgs["bake"]
        out[key] = rows
    return out


def streaks(regions, tex, imgs, tx: float) -> dict:
    cls = imgs["classes"]
    used = tex.image(np.ones(len(tex), bool), False, bool)
    out = {}
    for r in regions:
        if "Temple" not in r.part_name or r.facing not in ("outer", "top"):
            continue
        sel = tex.image(tex.region == r.rid, False, bool)
        row = {}
        for nm, codes in (("photo", ["photo_anchor", "photo_tier1"]), ("fill", ["patchmatch"]),
                          ("text_fill", ["text_fill"])):
            m = sel & np.isin(cls, [CLASS_CODES[c] for c in codes])
            d = PM.directional_energy(imgs["print"], used, m, tx)
            if d:
                row[nm] = d
        if "photo" in row:
            for nm in ("fill", "text_fill"):
                if nm in row:
                    row[nm]["ratio_vs_photo"] = round(row[nm]["across_over_along"] / row["photo"]["across_over_along"], 3)
                    row[nm]["contrast_vs_photo"] = round(row[nm]["band_std"] / row["photo"]["band_std"], 3)
        side = "+x" if r.side > 0 else "-x"
        out[f"temple {side} {r.facing}"] = row
    return out


def endpiece(job, tex, imgs, regions=None) -> dict:
    """Bright outliers on the endpieces (module docstring, item 4): the block under the T binned by distance to the
    photo-05 silhouette (the correction round's measure), plus the whole endpiece and the T's height."""
    v = job.views[FRONT_VIEW]
    P, _N = points(job.mesh.V, job.mesh.F, tex.face, tex.bary)
    uv = T.project_mm(P, v.camera, job.frame)
    H, W = v.shape
    ui = np.clip(np.round(uv[:, 0]).astype(int), 0, W - 1)
    vi = np.clip(np.round(uv[:, 1]).astype(int), 0, H - 1)
    dm = ndimage.distance_transform_edt(v.matte)
    lc = bright_contrast(imgs, tex)
    src = imgs["source"][tex.ii, tex.jj]
    photo = np.isin(imgs["classes"][tex.ii, tex.jj], PHOTO_CODES)
    isb = np.isin(tex.part, job.part_ids(["Brow_Left", "Brow_Right"]))
    fac = _facing(regions, tex) if regions is not None else None
    out = {}
    for sg in (-1, 1):
        endp = isb & (sg * P[:, 0] > ENDPIECE_X_MM)
        sel = endp & (P[:, 1] < T_Y_MM)
        painted = sel & (src == 5)
        d = dm[vi, ui]
        rows = {}
        for lo, hi in ((0, 1.5), (1.5, 2.5), (2.5, 3.5), (3.5, 99)):
            m = painted & (d >= lo) & (d < hi)
            rows[f"{lo}-{hi}px"] = {"texels": int(m.sum()),
                                    "bright_outlier_share": round(float((lc[m] > 15).mean()), 4) if m.any() else None}
        allm = sel & (src > 0)
        rows["all_photo_texels_bright_outlier_share"] = round(float((lc[allm] > 15).mean()), 4) if allm.any() else None
        rows["block_texels_bright_outlier_share_any_class"] = round(float((lc[sel] > 15).mean()), 4)
        zones = {"whole_endpiece": endp, "T_height": endp & (P[:, 1] >= T_Y_MM)}
        if fac is not None:
            zones["T_height_front"] = endp & (P[:, 1] >= T_Y_MM) & (fac == "front")
        for zn, z in zones.items():
            m = z & photo
            nb = int((lc[m] > OUTLIER).sum())
            rows[zn] = {"photo_texels": int(m.sum()), "bright_outliers": nb,
                        "bright_outlier_share": round(nb / max(int(m.sum()), 1), 4)}
        out[f"{'+' if sg > 0 else '-'}x"] = rows
    return out


def new_photo_outliers(job, regions, tex, imgs, before) -> dict:
    """Bright-outlier share of the photo texels ``imgs`` ADDED over ``before`` (photo here, not photo there) against
    those photo in both, per side, on the brow front and on the whole endpieces (module docstring)."""
    fac = _facing(regions, tex)
    P = tex.P
    isb = np.isin(tex.part, job.part_ids(["Brow_Left", "Brow_Right"]))
    lc = bright_contrast(imgs, tex)
    now = np.isin(imgs["classes"][tex.ii, tex.jj], PHOTO_CODES)
    was = np.isin(before["classes"][tex.ii, tex.jj], PHOTO_CODES)
    out = {"vs": before["bake"], "outlier": f"> {OUTLIER} levels over the 9 x 9 median (radiance)"}
    for sg in (-1, 1):
        rows = {}
        for zn, z in (("brow_front", isb & (fac == "front") & (np.sign(P[:, 0]) == sg)),
                      ("endpiece", isb & (sg * P[:, 0] > ENDPIECE_X_MM))):
            r = {}
            for nm, m in (("added", z & now & ~was), ("kept", z & now & was)):
                nb = int((lc[m] > OUTLIER).sum())
                r[nm] = {"texels": int(m.sum()), "bright_outliers": nb,
                         "bright_outlier_share": round(nb / max(int(m.sum()), 1), 4)}
            r["dropped"] = {"texels": int((z & ~now & was).sum())}
            rows[zn] = r
        out[f"{'+' if sg > 0 else '-'}x"] = rows
    return out


def ar_streaks(captures: dict) -> dict:
    """``print_metrics.axis_streakiness`` of each temple at the AR_STREAK_POSES of every capture {name: dir}."""
    out = {}
    for name, cap in captures.items():
        cap = Path(cap)
        rep = json.loads((cap / "report.json").read_text(encoding="utf-8"))
        rows = {}
        for pose in AR_STREAK_POSES:
            f = cap / f"{pose}__part-id.png"
            if not f.is_file():
                continue
            pid = np.asarray(Image.open(f).convert("RGB"))
            bt = np.asarray(Image.open(cap / f"{pose}__skin__beauty.png").convert("RGB"))
            for side, part in TEMPLES.items():
                r = PM.axis_streakiness(bt, PM.ar_frame_mask(pid, rep["id_palette"], [part]))
                if r is not None:
                    rows[f"{pose} temple {side}"] = r
        out[name] = {"capture": str(cap), "rows": rows}
    return out


def run(bake: Path, before: Path | None = None, previous: Path | None = None, ar: dict | None = None) -> dict:
    job, rep, lay, regions, tex, occ, tx = _load(bake)
    now = _imgs(bake)
    by_rules = {}
    if before is not None:
        by_rules["step4"] = _imgs(before)
    if previous is not None:
        by_rules["correction_plain"] = _imgs(previous)
    by_rules["correction"] = now
    res = {"bake": str(bake), "before": str(before) if before else None,
           "previous": str(previous) if previous else None,
           "levels": {k: v["level"] for k, v in by_rules.items()},
           "brow_front": brow_front(job, regions, tex, occ, by_rules),
           "streaks": {"now": streaks(regions, tex, now, tx)},
           "endpiece": {"now": endpiece(job, tex, now, regions)}}
    if before is not None:
        res["streaks"]["before"] = streaks(regions, tex, by_rules["step4"], tx)
        res["endpiece"]["before"] = endpiece(job, tex, by_rules["step4"], regions)
        res["new_photo_outliers"] = {"now": new_photo_outliers(job, regions, tex, now, by_rules["step4"])}
    if previous is not None:
        res["streaks"]["previous"] = streaks(regions, tex, by_rules["correction_plain"], tx)
        res["endpiece"]["previous"] = endpiece(job, tex, by_rules["correction_plain"], regions)
        if before is not None:
            res["new_photo_outliers"]["previous"] = new_photo_outliers(job, regions, tex, by_rules["correction_plain"],
                                                                       by_rules["step4"])
    if ar:
        res["ar_streaks"] = ar_streaks(ar)
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bake", required=True)
    ap.add_argument("--before", default=None, help="the 10bf932 bake (step-4 rules) for the before columns")
    ap.add_argument("--previous", default=None, help="the correction round's bake (plain footprint) beside this one")
    ap.add_argument("--ar", action="append", default=[], help="NAME=CAPTURE_DIR: arview captures for ar_streaks")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    ar = dict(x.split("=", 1) for x in a.ar)
    res = run(Path(a.bake), Path(a.before) if a.before else None, Path(a.previous) if a.previous else None, ar)
    txt = json.dumps(res, indent=1)
    if a.out:
        Path(a.out).write_text(txt, encoding="utf-8")
    print(txt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
