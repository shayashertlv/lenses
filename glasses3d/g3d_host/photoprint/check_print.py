"""check_print: the held-out pixel proof of WP5_DESIGN §7, graded by the independent instrument.

Ported from the scratch `design_reg/p2_parallax.eval_stripes` (held-out stripes, single-source bake, Astra on the same
pixels), `wp5lib.sample_sources/source_gains/combine/astra_colours` and `step1_baseline` (E, nearest fill);
PORT_REVIEW section 10.

For each region with >= 2 registered views, each registered held-out view h and each fold k:
- the registration fitted on the OTHER fold (it used every view's pixels of that fold, h's included) gives the
  cameras, hinges and S';
- a texture is made from every registered view except h and rendered at h through h's registration on S'. **Stage-2
  stand-in for steps 2-7** (texels, tiers, gains, labels, fill; build step 3): a direct per-pixel bake, i.e. at each of
  h's pixels the body point is coloured by the source with the largest tier-1 weight cos^2 x px/mm (cos >= 0.5, first
  surface, ``valid`` pixel), after a quantile_gain from same-surface overlaps to the anchor (>= 200 pairs);
- Astra's texture (the GLB's own images and UVs) is rendered at the same camera on S (h's hinges, no parallax);
- both are graded on identical pixels: E = (S' render & S render of the region) eroded 2 px & h's acetate eroded 1 px
  & projected & the fold k pixels; the photo's filters read (S' & S render) & acetate, the RENDERS' filters read that
  & the fold k pixels only (fix round 1, as in the gate: a held-out render's band-pass must not read the fitted fold;
  the 1-4 px DoG reaches about 2.5 mm at 4.8 px/mm against 4 mm stripes). Unprojected pixels inside the region take
  the nearest projected colour so the filters see no holes (they are not graded).
- **Never a pixel the fit read** (fix round 2; ``reads`` = the fit's ``RegResult.reads``): E drops h's pixels in
  ``reads[h]``, and a source sample whose bilinear footprint touches ``reads[w]`` is not used (the pixel takes another
  source or stays unprojected), so every graded value comes from photo pixels the fit never read.

**B0** (always reported, never gating): strict leave-one-view-out. The registration is refitted WITHOUT h (no pixel
of h anywhere), h stays at its WP3b camera, and the same sources are rendered at h.

Runtime metrics come from ``metrics.py``. The independent proof retains its separate implementation in
``tests/instruments/print_metrics.py``; runtime verdicts alone are never proof of correctness.
"""
from __future__ import annotations

import numpy as np
from PIL import Image
from scipy import ndimage

from .. import texture_utils as T
from .gate import region_pixels, sample_source
from .inputs import ppm_local
from .pose import Deformer, Pose
from .regions import FOLD_A, FOLD_B

BAKE_MIN_COS = 0.5
SHEET_MAX = (1600, 1200)


def _instrument():
    from . import metrics
    return metrics


def direct_bake(job, deformer: Deformer, render_pose: Pose, sample_pose: Pose, region, h: str, sources: list[str], *,
                ref: str | None = None, reads: dict | None = None) -> dict:
    """h's pixels of the region (``render_pose``: h's camera and S') coloured by the best tier-1 source
    (``sample_pose``: the sources' cameras and S'); a source never samples its pixels in ``reads``."""
    reads = reads or {}
    px = region_pixels(job, deformer, render_pose, region, h)
    H, W = job.views[h].shape
    K = len(px["face"])
    smp = {}
    for w in sources:
        s = sample_source(job, deformer, sample_pose, w, px["face"], px["bary"], min_cos=BAKE_MIN_COS,
                          forbidden=reads.get(w))
        ppm = ppm_local(sample_pose.cameras[w], job.frame, s["P"])
        s["w"] = np.where(s["ok"], np.clip(s["cos"], 0, 1) ** 2 * ppm, 0.0)
        smp[w] = s
    gains = {}
    if sources:
        ref = ref if ref in sources else max(sources, key=lambda w: (smp[w]["w"].sum(), -sources.index(w)))
        for w in sources:
            if w == ref:
                gains[w] = {"ref": True}
                continue
            both = smp[w]["ok"] & smp[ref]["ok"]
            if both.sum() >= T.GAIN_MIN_PAIRS:
                g, o, _ = T.quantile_gain(smp[w]["col"][both], smp[ref]["col"][both])
                smp[w]["col"] = np.clip(T.apply_gain(smp[w]["col"], g, o), 0, 255)
                gains[w] = {"gain": [float(x) for x in g], "offset": [float(x) for x in o], "pairs": int(both.sum())}
            else:
                gains[w] = {"skipped": "too_few_overlaps", "pairs": int(both.sum())}
    ren = np.zeros((H, W, 3))
    proj = np.zeros((H, W), bool)
    src = np.full((H, W), -1, np.int16)
    if sources:
        Wm = np.stack([smp[w]["w"] for w in sources], 1)
        best = Wm.max(1)
        got = best > 0
        k = Wm.argmax(1)
        col = np.zeros((K, 3))
        for i, w in enumerate(sources):
            m = got & (k == i)
            col[m] = smp[w]["col"][m]
        ii, jj = px["ii"][got], px["jj"][got]
        ren[ii, jj] = col[got]
        proj[ii, jj] = True
        src[ii, jj] = k[got]
    fold = np.full((H, W), -2, np.int8)
    fold[px["ii"], px["jj"]] = px["fold"]
    return {"ren": ren, "projected": proj, "source": src, "sel": px["sel"], "fold": fold, "gains": gains,
            "sources": list(sources),
            "source_samples_read_by_fit": {w: int(smp[w]["forbidden_hit"].sum()) for w in sources}}


def astra_render(job, deformer: Deformer, pose: Pose, region, h: str) -> dict:
    """Astra's texture at h's camera on S (h's hinges kept, no parallax)."""
    if job.astra is None:
        raise ValueError("the job carries no Astra texture (load_job(with_astra=True))")
    pose_s = Pose(pose.cameras, pose.hinges, {})
    px = region_pixels(job, deformer, pose_s, region, h)
    H, W = job.views[h].shape
    ren = np.zeros((H, W, 3))
    b2 = px["bary"][:, 1:]
    ren[px["ii"], px["jj"]] = job.astra.colours(job.mesh.part, px["face"], b2)
    return {"ren": ren, "sel": px["sel"]}


def case_images(job, deformer: Deformer, pose: Pose, region, h: str, sources: list[str], fold: str | None, *,
                ref: str | None = None, reads: dict | None = None) -> dict:
    """Renders, masks and E of one (region, held-out view, fold) case; ``reads`` = the photo pixels the fit read
    (never graded, never sampled)."""
    reads = reads or {}
    P = direct_bake(job, deformer, pose, pose, region, h, sources, ref=ref, reads=reads)
    A = astra_render(job, deformer, pose, region, h)
    v = job.views[h]
    photo = v.image.astype(np.float64)
    sel = P["sel"] & A["sel"]
    p_img = P["ren"].copy()
    if P["projected"].any():
        filled, _ = T.nearest_fill(p_img, P["projected"])
        hole = P["sel"] & ~P["projected"]
        p_img[hole] = filled[hole]
    a_img = A["ren"].copy()
    if A["sel"].any():
        filled, _ = T.nearest_fill(a_img, A["sel"])
        hole = P["sel"] & ~A["sel"]
        a_img[hole] = filled[hole]
    E = (ndimage.binary_erosion(sel, iterations=2) & ndimage.binary_erosion(v.acetate, iterations=1)
         & P["projected"])
    support = sel & v.acetate
    ren_support = support
    if fold is not None:
        in_fold = P["fold"] == {"A": FOLD_A, "B": FOLD_B}[fold]
        E &= in_fold
        ren_support = support & in_fold
    e_before = int(E.sum())
    if h in reads:
        E &= ~np.asarray(reads[h], bool)
    return {"P": P, "A": A, "p_img": p_img, "a_img": a_img, "photo": photo, "E": E, "support": support,
            "ren_support": ren_support, "sel": sel, "ppm": job.ppm(h, pose.cameras[h]),
            "E_px_before_reads": e_before, "E_px_read_by_fit": e_before - int(E.sum())}


def grade_case(ci: dict, *, seed: int = 0, tolerant: bool = False) -> dict:
    pm = _instrument()
    return pm.evaluate_case(ci["p_img"], ci["a_img"], ci["photo"], ci["support"], ci["E"], ci["ppm"], seed=seed,
                            tolerant=tolerant, ren_support=ci.get("ren_support"))


def check_region(job, regions, groups, axes, region, registered: list[str], regs: dict, *, anchor: str | None,
                 seed: int = 0, sheets_dir=None, wp3b_pose: Pose | None = None) -> list[dict]:
    """Stripes protocol rows for one region: every registered held-out view x evaluated fold (B with the fold-A fit,
    A with the fold-B fit)."""
    rows = []
    deformer = Deformer(job, regions, groups, axes)
    for h in registered:
        sources = [w for w in registered if w != h]
        for ev, fit in (("B", "A"), ("A", "B")):
            reg = regs[fit]
            ci = case_images(job, deformer, reg.pose, region, h, sources, ev, ref=anchor, reads=reg.reads)
            m = grade_case(ci, seed=seed)
            row = {"region": region.name, "rid": region.rid, "held_out": h, "sources": sources, "eval_fold": ev,
                   "fit_fold": fit, "gains": ci["P"]["gains"], "E_px_read_by_fit": ci["E_px_read_by_fit"],
                   "E_px_before_reads": ci["E_px_before_reads"],
                   "source_samples_read_by_fit": ci["P"]["source_samples_read_by_fit"], **m}
            if sheets_dir is not None:
                row["sheet"] = write_sheet(job, deformer, reg.pose, region, h, ci, m, sheets_dir,
                                           f"{region.name}_{job.views[h].short}_eval{ev}", wp3b_pose=wp3b_pose)
            rows.append(row)
    return rows


def check_b0(job, regions, groups, region, h: str, sources: list[str], reg_wo_h, axes_wo_h, *, anchor: str | None,
             seed: int = 0) -> list[dict]:
    """B0: the registration without h (h at its WP3b camera), graded on each fold's pixels and on all."""
    deformer = Deformer(job, regions, groups, axes_wo_h)
    pose = reg_wo_h.pose.copy()
    pose.cameras[h] = job.views[h].camera
    pose.hinges = {k: a for k, a in pose.hinges.items() if k[0] != h}
    rows = []
    for ev in ("B", "A", None):
        ci = case_images(job, deformer, pose, region, h, sources, ev, ref=anchor)
        m = grade_case(ci, seed=seed)
        rows.append({"region": region.name, "rid": region.rid, "held_out": h, "sources": sources,
                     "eval_fold": ev or "all", "protocol": "B0", **m})
    return rows


def _header_lines(stem: str, m: dict) -> list[str]:
    lines = [f"{stem}  E={m.get('pixels')} px  rows: photo | held-out render | AR (S, WP3b camera) | Astra | "
             f"|dE00| 0-20 | source | E"]
    c = m.get("criteria") or {}
    if c:
        lines.append("C1 dE00 P {:.2f} vs A {:.2f} (margin {:+.2f}, CI hi {:+.2f}) {}".format(
            c["C1"]["projection"], c["C1"]["astra"], c["C1"]["margin"], c["C1"]["ci95_hi"],
            "PASS" if c["C1"]["pass"] else "FAIL"))
        if c["C2"].get("margin") is None:
            lines.append("C2 bp P {:.3f} vs A {:.3f}: no null ({}) FAIL".format(
                c["C2"]["projection"], c["C2"]["astra"], c["C2"].get("reason")))
        else:
            lines.append("C2 bp P {:.3f} vs A {:.3f} null {:.3f}+/-{:.3f} (margin {:+.3f}) {}".format(
                c["C2"]["projection"], c["C2"]["astra"], c["C2"]["null_mean"], c["C2"]["null_std"],
                c["C2"]["margin"], "PASS" if c["C2"]["pass"] else "FAIL"))
        lines.append("C3 energy 0.4-1.6mm {:.2f} [0.8,1.25], 1-4px {:.2f} >=0.7 {} | C4 contamination {:.3f} {}".format(
            c["C3"]["energy_0.4_1.6mm"], c["C3"]["energy_1_4px"], "PASS" if c["C3"]["pass"] else "FAIL",
            c["C4"]["contamination"], "PASS" if c["C4"]["pass"] else "FAIL"))
    return lines


def _with_header(im: Image.Image, lines: list[str]) -> Image.Image:
    from PIL import ImageDraw
    h = 14 * len(lines) + 6
    out = Image.new("RGB", (max(im.size[0], 640), im.size[1] + h), (255, 255, 255))
    out.paste(im, (0, h))
    dr = ImageDraw.Draw(out)
    for i, t in enumerate(lines):
        dr.text((4, 3 + 14 * i), t, fill=(0, 0, 0))
    return out


def _crop_box(mask, pad=10):
    ys, xs = np.nonzero(mask)
    if not len(ys):
        return 0, mask.shape[0], 0, mask.shape[1]
    return (max(ys.min() - pad, 0), min(ys.max() + pad + 1, mask.shape[0]), max(xs.min() - pad, 0),
            min(xs.max() + pad + 1, mask.shape[1]))


def write_sheet(job, deformer: Deformer, pose: Pose, region, h: str, ci: dict, m: dict, out_dir, stem: str, *,
                wp3b_pose: Pose | None = None) -> str:
    """Rows: photo; held-out render; the same sources on S at the WP3b camera (what AR shows); Astra; |dE00| 0-20
    (gained projection vs photo, on E); source; E (the graded stripe pixels)."""
    from pathlib import Path
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    photo = ci["photo"]
    y0, y1, x0, x1 = _crop_box(ci["P"]["sel"], 8)

    def lay(x, mask):
        o = photo.copy()
        o[mask] = x[mask]
        return o[y0:y1, x0:x1]

    rows = [photo[y0:y1, x0:x1], lay(ci["p_img"], ci["P"]["sel"])]
    if wp3b_pose is not None:
        ar_pose = Pose(dict(pose.cameras), {k: a for k, a in pose.hinges.items() if k[0] != h}, {})
        ar_pose.cameras[h] = wp3b_pose.cameras[h]
        ar = direct_bake(job, deformer, ar_pose, pose, region, h, ci["P"]["sources"])
        rows.append(lay(ar["ren"], ar["projected"]))
    rows.append(lay(ci["a_img"], ci["A"]["sel"]))
    E = ci["E"]
    de = np.zeros(E.shape)
    if E.any():
        g, o, _ = T.quantile_gain(ci["p_img"][E], photo[E])
        de[E] = T.de00(np.clip(T.apply_gain(ci["p_img"][E], g, o), 0, 255), photo[E])
    heat = np.repeat(np.clip(de / 20.0 * 255, 0, 255)[..., None], 3, -1)
    heat[~E] = 40
    rows.append(heat[y0:y1, x0:x1])
    palette = np.array([[230, 80, 60], [60, 150, 230], [90, 200, 90], [220, 200, 60], [180, 90, 200]], float)
    src = np.full(photo.shape, 40.0)
    s = ci["P"]["source"]
    for k in range(len(palette)):
        src[s == k] = palette[k]
    rows.append(src[y0:y1, x0:x1])
    ev = photo.copy()
    ev[~E] = ev[~E] * 0.25 + 190
    rows.append(ev[y0:y1, x0:x1])
    sheet = np.concatenate([np.clip(r, 0, 255).astype(np.uint8) for r in rows], 0)
    scale = 2 if ci["ppm"] < 6.0 else 1
    im = Image.fromarray(sheet)
    im = im.resize((im.size[0] * scale, im.size[1] * scale), Image.NEAREST)
    im = _with_header(im, _header_lines(stem, m))
    scale = 1
    w, hgt = im.size[0] * scale, im.size[1] * scale
    k = min(1.0, SHEET_MAX[0] / w, SHEET_MAX[1] / hgt)
    im = im.resize((max(1, int(w * k)), max(1, int(hgt * k))), Image.NEAREST)
    p = out_dir / f"{stem.replace('/', '_').replace('[', '').replace(']', '')}.png"
    im.save(p)
    return f"{out_dir.name}/{p.name}"          # relative to the run's output dir: two runs must match byte for byte
