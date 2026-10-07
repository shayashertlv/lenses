"""WP5 build step 4 (host): before / after close-up sheets and measurements of the four bake defects, from the WRITTEN
outputs of two bakes with the same layout (default: ``data/wp5/pre_step4_bake`` = the 7ac7c8f bake, and
``data/wp5/ft1119_bake`` = this step's bake).

    glasses3d/.venv/Scripts/python -B -m glasses3d.tests.photoprint.step4_sheets [--before DIR] [--after DIR]
        [--out DIR]

Every number is computed HERE from print.png / classes.png / source.png read back (the tool's own report is not the
measure), with rules written here on purpose:
- **metal** (defect 1): texels within ``ZONE_MM`` (3D) of a small metallic part (hinges, pad stalks and mounts) on the
  print parts; per zone the share of 'metal-coloured' texels (luminance >= 90 and B/R >= 0.5: the photo-02 hinge's
  shaded gold is B/R 0.55-0.8, the havana's amber at that luminance B/R 0.35-0.45 on 02) and of 'white highlight'
  texels (luminance >= 90 and B/R >= 0.85). Zones: endpiece backs (brow texels near the hinges), brow backs near the
  pad stalks (the rivets), temple fronts near the hinges.
- **fill** (defect 2): per part, on its strip chart (atlas x = arc length s along the part), the streak index of
  fill texels (classes 'patchmatch' / mirror ring) against photo texels: mean |dL/dt| / mean |dL/ds| of the luminance
  (sigma 1 texel), where t (atlas y) runs around the section; a smear along the strip has a high index. The fill's
  fine-band energy (std of the 0.25-1 mm DoG) over the photo's is reported beside it.
- **colour steps** (defect 3): CIEDE2000 between the median Lab of the texels within 1 mm (3D) on the two sides of
  every boundary between two groups (region x painting photo, from source.png) on the same part, weighted by the
  boundary's texels; plus the median L* per (part, facing) and the -x / +x differences of mirrored regions.
- **glints / A5** (defect 4): the white-highlight share over each part's photo texels; A5 from each bake's bake.json.

Sheets (``data/wp5/step4_sheets/``): ``closeups_zones.png`` (each metal zone rendered at the photo that shows it,
photo | before | after, nearest 3x: endpiece backs and rivet zones at 02, temple front / top near the hinges at 04),
``closeups.png`` (atlas crops, before | after, nearest 2x: the middle of each temple strip, photo + fill, and the -x
brow's top band), ``renders_<view>.png`` (the parts at a photo camera: photo | before | after) and
``step4_measures.json``.
Sides are always the x sign (q001's Temple_Left is the -x temple).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage
from scipy.spatial import cKDTree

from glasses3d.g3d import printuv as PU
from glasses3d.g3d_host import texture_utils as T
from glasses3d.g3d_host.photoprint import inputs as IN
from glasses3d.g3d_host.photoprint import metal as MT
from glasses3d.g3d_host.photoprint import project as PR
from glasses3d.g3d_host.photoprint import regions as RG
from glasses3d.g3d_host.photoprint import texels as TX
from glasses3d.g3d_host.photoprint.compose import CLASS_CODES
from glasses3d.g3d_host.photoprint.report import jsonable, write_json
from glasses3d.tests.photoprint import printproof as PP

DATA = IN.REPO / "glasses3d" / "data" / "wp5"
BEFORE = DATA / "pre_step4_bake"
AFTER = DATA / "ft1119_bake"
OUT = DATA / "step4_sheets"
ZONE_MM = 6.0
METAL_LUM, METAL_BR, WHITE_BR = 90.0, 0.5, 0.85
STEP_BAND_MM = 1.0
CAPTIONS = ["before (7ac7c8f)", "after (step 4)"]       # panel captions (--labels)
MIN_SIDE = 100


def _font(size=15):
    try:
        return ImageFont.load_default(size=size)
    except Exception:                                   # noqa: BLE001
        return ImageFont.load_default()


def _read(d: Path) -> dict:
    return {"print": np.asarray(Image.open(d / "print.png").convert("RGB")).astype(np.float64),
            "classes": np.asarray(Image.open(d / "classes.png")), "source": np.asarray(Image.open(d / "source.png")),
            "report": json.loads((d / "report.json").read_text(encoding="utf-8")),
            "bake": json.loads((d / "bake.json").read_text(encoding="utf-8")) if (d / "bake.json").is_file() else {}}


def _br(c):
    c = np.asarray(c, float)
    return c[..., 2] / np.maximum(c[..., 0], 1.0)


# ----------------------------------------------------------------------------- measures
def zones(job, tex, regions) -> dict:
    """{zone name: texel rows} (3D distance to small metallic parts, see the module docstring)."""
    pids = job.part_ids(IN.FT1119_PARTS)
    hw = MT.hardware_faces(job, pids)[:len(job.mesh.F)]
    names = job.mesh.names
    out = {}
    by_part = {}
    for p in np.unique(job.mesh.part[hw == 2]):
        by_part[names[p]] = cKDTree(job.mesh.V[np.unique(job.mesh.F[job.mesh.part == p])])
    rid = {r.rid: r for r in regions}
    side_of = {r.part_name: r.side for r in regions}
    for zone, hw_kind, part_kind, facing in (("endpiece back (brow near the hinge)", "hinge", "Brow", "back"),
                                             ("brow back near the pad stalk (rivet)", "Pad_", "Brow", "back"),
                                             ("temple front / top near the hinge", "hinge", "Temple", None)):
        for sgn in (-1, 1):
            parts = [p for p in IN.FT1119_PARTS if p.startswith(part_kind) and side_of.get(p) == sgn]
            hws = [h for h in by_part if hw_kind in h and np.sign(job.mesh.V[np.unique(job.mesh.F[job.mesh.part ==
                                                                                                 names.index(h)])][:, 0].mean()) == sgn]
            if not parts or not hws:
                continue
            sel = np.isin(tex.part, [names.index(p) for p in parts])
            if facing:
                sel &= np.array([rid[int(r)].facing == facing if r >= 0 else False for r in range(max(rid) + 1)]
                                + [False])[np.where(tex.region >= 0, tex.region, -1)]
            d = np.min([by_part[h].query(tex.P[sel])[0] for h in hws], axis=0)
            rows = np.nonzero(sel)[0][d <= ZONE_MM]
            out[f"{zone}, {'-x' if sgn < 0 else '+x'}"] = rows
    return out


def metal_measure(img_px: np.ndarray, rows: np.ndarray) -> dict:
    c = img_px[rows]
    lum = c.mean(1)
    br = _br(c)
    return {"texels": int(len(rows)), "metal_coloured_share": float(((lum >= METAL_LUM) & (br >= METAL_BR)).mean()),
            "white_highlight_share": float(((lum >= METAL_LUM) & (br >= WHITE_BR)).mean())}


def strip_box(layout, part: str):
    st = [c for c in layout.charts if c["part"] == part and c["kind"] == "strip"]
    if not st:
        return None
    c = max(st, key=lambda c: c["w"] * c["h"])
    return c["chart"], (c["y0"], c["y0"] + c["h"], c["x0"], c["x0"] + c["w"])


def fill_measure(img: np.ndarray, classes: np.ndarray, tex, layout, part: str, tx: float) -> dict:
    sb = strip_box(layout, part)
    if sb is None:
        return {}
    ch, (y0, y1, x0, x1) = sb
    chart = tex.image(tex.chart, -1, np.int64)[y0:y1, x0:x1] == ch
    L = img[y0:y1, x0:x1].mean(-1)
    cls = classes[y0:y1, x0:x1]
    fill = chart & np.isin(cls, [CLASS_CODES["patchmatch"], CLASS_CODES["seam_band"], CLASS_CODES["highlight_fill"],
                                 CLASS_CODES["text_fill"]])
    photo = chart & np.isin(cls, [CLASS_CODES["photo_anchor"], CLASS_CODES["photo_tier1"]])
    Ls = ndimage.gaussian_filter(L, 1.0)
    gx = np.abs(np.diff(Ls, axis=1))[:-1]
    gy = np.abs(np.diff(Ls, axis=0))[:, :-1]
    out = {}
    s1, s2 = 0.25 * tx, 1.0 * tx
    sup = chart.astype(float)
    g1 = ndimage.gaussian_filter(L * sup, s1) / np.maximum(ndimage.gaussian_filter(sup, s1), 1e-6)
    g2 = ndimage.gaussian_filter(L * sup, s2) / np.maximum(ndimage.gaussian_filter(sup, s2), 1e-6)
    bp = g1 - g2
    for nm, m in (("fill", fill), ("photo", photo)):
        mm = ndimage.binary_erosion(m, iterations=2)
        k = mm[:-1, :-1] & mm[1:, :-1] & mm[:-1, 1:]
        if k.sum() < 500:
            continue
        out[nm] = {"texels": int(mm.sum()), "streak_index": float(gy[k].mean() / max(gx[k].mean(), 1e-6)),
                   "fine_band_std": float(bp[mm].std())}
    if "fill" in out and "photo" in out:
        out["streak_index_fill_over_photo"] = out["fill"]["streak_index"] / out["photo"]["streak_index"]
        out["fine_energy_fill_over_photo"] = out["fill"]["fine_band_std"] / max(out["photo"]["fine_band_std"], 1e-6)
    return out


def colour_steps(img_px: np.ndarray, src: np.ndarray, tex, regions) -> dict:
    from skimage.color import deltaE_ciede2000
    by = {r.rid: r for r in regions}
    photo = src > 0
    keys = np.where(photo & (tex.region >= 0), tex.region * 64 + src.astype(np.int64), -1)
    gs = [int(k) for k in np.unique(keys[keys >= 0])]
    pts = {k: np.nonzero(keys == k)[0] for k in gs}
    rows = []
    for i, ka in enumerate(gs):
        for kb in gs[i + 1:]:
            if ka % 64 == kb % 64:
                continue
            for q in np.intersect1d(np.unique(tex.part[pts[ka]]), np.unique(tex.part[pts[kb]])):
                pa = pts[ka][tex.part[pts[ka]] == q]
                pb = pts[kb][tex.part[pts[kb]] == q]
                if len(pa) < MIN_SIDE or len(pb) < MIN_SIDE:
                    continue
                da, _ = cKDTree(tex.P[pb]).query(tex.P[pa], k=1, distance_upper_bound=STEP_BAND_MM)
                db, _ = cKDTree(tex.P[pa]).query(tex.P[pb], k=1, distance_upper_bound=STEP_BAND_MM)
                sa, sb = pa[np.isfinite(da)], pb[np.isfinite(db)]
                if len(sa) < MIN_SIDE or len(sb) < MIN_SIDE:
                    continue
                la = np.median(T.lab_of(img_px[sa]), 0)
                lb = np.median(T.lab_of(img_px[sb]), 0)
                rows.append({"a": f"{by[ka // 64].name} <- {ka % 64}", "b": f"{by[kb // 64].name} <- {kb % 64}",
                             "texels": int(min(len(sa), len(sb))), "de00": float(deltaE_ciede2000(la[None], lb[None])[0]),
                             "dL": float(la[0] - lb[0])})
    big = [r for r in rows if r["texels"] >= 300]
    w = np.array([r["texels"] for r in big], float)
    d = np.array([r["de00"] for r in big], float)
    return {"boundaries": len(big), "weighted_mean_de00": float(np.average(d, weights=w)) if len(d) else None,
            "median_de00": float(np.median(d)) if len(d) else None, "max_de00": float(d.max()) if len(d) else None,
            "rows": sorted(big, key=lambda r: -r["texels"] * r["de00"])}


def region_levels(img_px: np.ndarray, classes: np.ndarray, tex, regions) -> dict:
    """Median L*, a*, b* per region over its textured texels (photo and fill), and the mirrored-region differences."""
    out = {}
    for r in regions:
        m = (tex.region == r.rid) & (classes != CLASS_CODES["no_texture"])
        if m.sum() < 500:
            continue
        out[r.name] = [round(float(x), 2) for x in np.median(T.lab_of(img_px[m]), 0)]
    pairs = {}
    for r in regions:
        if r.side < 0:
            mate = next((q for q in regions if q.side > 0 and q.facing == r.facing
                         and q.part_name.split("_")[0] == r.part_name.split("_")[0]), None)
            if mate is not None and r.name in out and mate.name in out:
                pairs[f"{r.part_name.split('_')[0]}/{r.facing}"] = round(out[r.name][0] - out[mate.name][0], 2)
    return {"median_lab": out, "minus_x_minus_plus_x_dL": pairs,
            "mean_abs_mirror_dL": float(np.mean(np.abs(list(pairs.values())))) if pairs else None}


def cross_view_colour(job, layout, occ, d: dict, view: str, part_names) -> dict:
    """Per part: the bake rendered at ``view``'s WP3b camera against the photo, on the part's pixels whose texel was
    painted by ANOTHER photo or filled (source.png != this view), the render's pixels eroded 2 px and on the photo's
    acetate: CIEDE2000 between the median Lab of render and photo, and the L* difference (render - photo). A colour
    step a photo shows between parts is what this sees (e.g. the -x temple painted from 03, seen in 01)."""
    from glasses3d.g3d_host.photoprint import signals as S
    v = job.views[view]
    sc = occ.scene(job.mesh.V)
    r = PP.render_atlas(job, layout, d["print"], v.camera, v.shape, sc)
    rr = sc.render(v.camera, v.shape, 1, want_bary=True)
    fid = rr["face_id"]
    nf = len(job.mesh.F)
    lrow = layout.face_index(nf)
    hit = (fid >= 0) & (fid < nf)
    row = np.full(v.shape, -1, np.int64)
    row[hit] = lrow[fid[hit]]
    m = row >= 0
    ii, jj = np.nonzero(m)
    b = rr["bary"][ii, jj].astype(np.float64)
    w = np.stack([1.0 - b[:, 0] - b[:, 1], b[:, 0], b[:, 1]], 1)
    uv = (w[:, :, None] * np.asarray(layout.uv, np.float64)[row[ii, jj]]).sum(1)
    H, W = d["source"].shape
    src = np.zeros(v.shape, np.int64)
    src[ii, jj] = d["source"][np.clip(np.floor(uv[:, 1]).astype(int), 0, H - 1), np.clip(np.floor(uv[:, 0]).astype(int), 0, W - 1)]
    k = d["report"]["views"].index(view) + 1
    E = ndimage.binary_erosion(m, iterations=2) & ndimage.binary_erosion(S.acetate_mask(v.image) & v.matte, iterations=1)
    out = {}
    from skimage.color import deltaE_ciede2000
    for p in part_names:
        pm = np.zeros(v.shape, bool)
        pm[hit] = job.mesh.part[fid[hit]] == job.mesh.names.index(p)
        sel = E & pm & (src != k)
        if sel.sum() < 300:
            continue
        lr = np.median(T.lab_of(r["ren"][sel]), 0)
        lp = np.median(T.lab_of(v.image[sel].astype(float)), 0)
        out[p] = {"pixels": int(sel.sum()), "de00_of_medians": round(float(deltaE_ciede2000(lr[None], lp[None])[0]), 2),
                  "dL": round(float(lr[0] - lp[0]), 2), "render_lab": [round(float(x), 1) for x in lr],
                  "photo_lab": [round(float(x), 1) for x in lp]}
    return out


# ----------------------------------------------------------------------------- sheets
def _label(img: np.ndarray, text: str) -> np.ndarray:
    head = Image.new("RGB", (img.shape[1], 22), (255, 255, 255))
    ImageDraw.Draw(head).text((4, 3), text, fill=(0, 0, 0), font=_font(14))
    return np.concatenate([np.asarray(head), img], 0)


def _pad_to(a: np.ndarray, w: int) -> np.ndarray:
    return np.pad(a, ((0, 0), (0, max(0, w - a.shape[1])), (0, 0)), constant_values=255)


def closeups(before: dict, after: dict, tex, boxes: list, scale: int = 2) -> np.ndarray:
    rows = []
    for title, (y0, y1, x0, x1) in boxes:
        a = before["print"][y0:y1, x0:x1].astype(np.uint8)
        b = after["print"][y0:y1, x0:x1].astype(np.uint8)
        a = np.asarray(Image.fromarray(a).resize((a.shape[1] * scale, a.shape[0] * scale), Image.NEAREST))
        b = np.asarray(Image.fromarray(b).resize((b.shape[1] * scale, b.shape[0] * scale), Image.NEAREST))
        sep = np.full((a.shape[0], 6, 3), 255, np.uint8)
        row = np.concatenate([_label(a, CAPTIONS[0]), _label(sep, ""), _label(b, CAPTIONS[1])], 1)
        rows.append(_label(row, title))
        rows.append(np.full((10, row.shape[1], 3), 255, np.uint8))
    W = max(r.shape[1] for r in rows)
    return np.concatenate([_pad_to(r, W) for r in rows], 0)


def atlas_box(tex, rows: np.ndarray, pad: int = 12, max_w: int = 700, max_h: int = 260):
    if not len(rows):
        return None
    ii, jj = tex.ii[rows], tex.jj[rows]
    cy, cx = int(np.median(ii)), int(np.median(jj))
    h = min(max_h, int(ii.max() - ii.min()) + 2 * pad)
    w = min(max_w, int(jj.max() - jj.min()) + 2 * pad)
    y0 = max(0, cy - h // 2)
    x0 = max(0, cx - w // 2)
    return (y0, min(tex.shape[0], y0 + h), x0, min(tex.shape[1], x0 + w))


ZONE_VIEW = {"endpiece back": "reference-02.png", "brow back near the pad stalk": "reference-02.png",
             "temple front / top near the hinge": "reference-04.png"}


def zone_closeup(job, layout, occ, before: dict, after: dict, title: str, rows: np.ndarray, tex, view: str,
                 pad: int = 18, scale: int = 3) -> np.ndarray | None:
    """photo | before | after at ``view``'s camera, cropped to where the zone's texels project (first hit), x3."""
    from glasses3d.g3d_host.camera import project_mm
    v = job.views[view]
    uv = project_mm(tex.P[rows], v.camera, job.frame)
    okp = (uv[:, 0] >= 0) & (uv[:, 0] < v.shape[1]) & (uv[:, 1] >= 0) & (uv[:, 1] < v.shape[0])
    if okp.sum() < 50:
        return None
    x0, x1 = int(np.percentile(uv[okp, 0], 2)) - pad, int(np.percentile(uv[okp, 0], 98)) + pad
    y0, y1 = int(np.percentile(uv[okp, 1], 2)) - pad, int(np.percentile(uv[okp, 1], 98)) + pad
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(v.shape[1], x1), min(v.shape[0], y1)
    sc = occ.scene(job.mesh.V)
    r = sc.render(v.camera, v.shape, 1)
    fid = r["face_id"]
    base = np.full(v.shape + (3,), 246.0)
    base[fid >= 0] = 205.0
    panels = []
    for nm, d in (("photo " + IN.short_name(view), None), (CAPTIONS[0], before), (CAPTIONS[1], after)):
        if d is None:
            im = v.image.astype(float)
        else:
            rr = PP.render_atlas(job, layout, d["print"], v.camera, v.shape, sc)
            im = base.copy()
            im[rr["mask"]] = rr["ren"][rr["mask"]]
        c = np.clip(im[y0:y1, x0:x1], 0, 255).astype(np.uint8)
        c = np.asarray(Image.fromarray(c).resize((c.shape[1] * scale, c.shape[0] * scale), Image.NEAREST))
        panels.append(_label(c, nm))
        panels.append(np.full((panels[-1].shape[0], 6, 3), 255, np.uint8))
    row = np.concatenate(panels[:-1], 1)
    return _label(row, f"{title}  (photo {IN.short_name(view)} camera, px {x0}-{x1} x {y0}-{y1}, x{scale})")


def render_rows(job, layout, occ, before: dict, after: dict, view: str, part_names, pad=14) -> np.ndarray | None:
    v = job.views[view]
    sc = occ.scene(job.mesh.V)
    rb = PP.render_atlas(job, layout, before["print"], v.camera, v.shape, sc)
    ra = PP.render_atlas(job, layout, after["print"], v.camera, v.shape, sc)
    r = sc.render(v.camera, v.shape, 1)
    fid = r["face_id"]
    nf = len(job.mesh.F)
    pid = [job.mesh.names.index(p) for p in part_names]
    m = (fid >= 0) & (fid < nf)
    m[m] = np.isin(job.mesh.part[fid[m]], pid)
    if m.sum() < 200:
        return None
    ys, xs = np.nonzero(m)
    y0, y1 = max(0, ys.min() - pad), min(v.shape[0], ys.max() + pad)
    x0, x1 = max(0, xs.min() - pad), min(v.shape[1], xs.max() + pad)
    base = np.full(v.shape + (3,), 246.0)
    base[fid >= 0] = 205.0
    outs = []
    for nm, rr in (("photo " + IN.short_name(view), None), ("before", rb), ("after", ra)):
        if rr is None:
            im = v.image.astype(float)
        else:
            im = base.copy()
            im[m] = rr["ren"][m]
        c = np.clip(im[y0:y1, x0:x1], 0, 255).astype(np.uint8)
        k = int(np.clip(1200 // max(c.shape[1], 1), 1, 3))
        c = np.asarray(Image.fromarray(c).resize((c.shape[1] * k, c.shape[0] * k), Image.NEAREST))
        outs.append(_label(c, nm))
    W = max(o.shape[1] for o in outs)
    return np.concatenate([_pad_to(o, W) for o in outs], 0)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--before", default=str(BEFORE))
    ap.add_argument("--after", default=str(AFTER))
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--labels", nargs=2, default=list(CAPTIONS), metavar=("BEFORE", "AFTER"),
                    help="panel captions of the two bakes")
    a = ap.parse_args(argv)
    CAPTIONS[:] = a.labels
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    job = IN.load_job(IN.FT1119["cameras"])
    B, A = _read(Path(a.before)), _read(Path(a.after))
    tx = float(A["report"]["layout"]["tx_per_mm"])
    assert abs(tx - float(B["report"]["layout"]["tx_per_mm"])) < 1e-9, "the two bakes have different layouts"
    layout = PU.layout(job.mesh.V, job.mesh.F, job.mesh.part, job.mesh.names, list(IN.FT1119_PARTS), tx,
                       atlas_max=PR.ATLAS_MAX, mirror=True)
    assert (layout.width, layout.height) == tuple(A["print"].shape[1::-1])
    regions = RG.build_regions(job, list(IN.FT1119_PARTS), RG.rigid_groups(job), n_samples=64)
    tex = TX.atlas_texels(job, layout, regions)
    px = {k: d["print"][tex.ii, tex.jj] for k, d in (("before", B), ("after", A))}
    cl = {k: d["classes"][tex.ii, tex.jj] for k, d in (("before", B), ("after", A))}
    sr = {k: d["source"][tex.ii, tex.jj] for k, d in (("before", B), ("after", A))}
    Z = zones(job, tex, regions)
    meas = {"before": str(a.before), "after": str(a.after), "rules": __doc__.split("Sheets")[0].strip()}
    meas["metal"] = {z: {k: metal_measure(px[k], rows) for k in ("before", "after")} for z, rows in Z.items()}
    meas["fill"] = {p: {k: fill_measure(d["print"], d["classes"], tex, layout, p, tx) for k, d in
                        (("before", B), ("after", A))} for p in IN.FT1119_PARTS}
    meas["colour_steps"] = {k: colour_steps(px[k], sr[k], tex, regions) for k in ("before", "after")}
    meas["region_levels"] = {k: region_levels(px[k], cl[k], tex, regions) for k in ("before", "after")}
    names = job.mesh.names
    meas["white_highlight_share_of_photo_texels"] = {
        k: {p: float(((px[k][m].mean(1) >= METAL_LUM) & (_br(px[k][m]) >= WHITE_BR)).mean())
            for p in IN.FT1119_PARTS
            for m in [np.isin(tex.part, [names.index(p)]) & np.isin(cl[k], [CLASS_CODES["photo_anchor"]])]}
        for k in ("before", "after")}
    meas["white_highlight_share_of_all_texels"] = {
        k: {p: float(((px[k][m].mean(1) >= METAL_LUM) & (_br(px[k][m]) >= WHITE_BR)).mean())
            for p in IN.FT1119_PARTS for m in [np.isin(tex.part, [names.index(p)])]} for k in ("before", "after")}
    meas["A5"] = {k: {kk: d["bake"].get("A5", {}).get(kk) for kk in ("offsets", "criteria", "pass")}
                  for k, d in (("before", B), ("after", A))}
    # close-ups: the zones rendered at the photo that shows them, then atlas crops of the strip fills
    occ = TX.OcclusionScene(job, job.part_ids(IN.FT1119_PARTS), TX.decal_geometry(PR.job_glb(job), job.mesh.skipped))
    zrows = []
    for z, rows in Z.items():
        view = next(vw for k, vw in ZONE_VIEW.items() if z.startswith(k))
        zr = zone_closeup(job, layout, occ, B, A, z, rows, tex, view)
        if zr is not None:
            zrows += [zr, np.full((10, zr.shape[1], 3), 255, np.uint8)]
    if zrows:
        Wz = max(r_.shape[1] for r_ in zrows)
        Image.fromarray(np.concatenate([_pad_to(r_, Wz) for r_ in zrows], 0)).save(out / "closeups_zones.png")
    boxes = []
    side_of = {r.part_name: r.side for r in regions}            # the x sign of each part's regions, never its name
    sx = lambda p: "-x" if side_of.get(p, 0) < 0 else "+x"
    temples = [q for q in IN.FT1119_PARTS if q.lower().startswith("temple")]
    brows = [q for q in IN.FT1119_PARTS if q.lower().startswith("brow")]
    for p in sorted(temples, key=lambda q: side_of.get(q, 0)):
        sb = strip_box(layout, p)
        if sb:
            _ch, (y0, y1, x0, x1) = sb
            xm = (x0 + x1) // 2
            boxes.append((f"{p} ({sx(p)} temple) strip, middle 44 mm: photo + fill",
                          (y0, y1, max(x0, xm - 350), min(x1, xm + 350))))
    for p in [q for q in brows if side_of.get(q, 0) < 0][:1]:   # the -x brow
        rid = next(r.rid for r in regions if r.part_name == p and r.facing == "top")
        rows = np.nonzero(tex.region == rid)[0]
        bx = atlas_box(tex, rows, max_w=700, max_h=200)
        if bx:
            boxes.append((f"{p} ({sx(p)} brow) top band (photo 04) and the front edge", bx))
    sheet = closeups(B, A, tex, boxes)
    Image.fromarray(sheet).save(out / "closeups.png")
    meas["closeup_boxes"] = {t: list(map(int, b)) for t, b in boxes}
    rnd = {}
    for view, parts in (("reference-02.png", ("Brow_Left", "Brow_Right")),
                        ("reference-01.png", ("Temple_Left", "Temple_Right", "Brow_Left", "Brow_Right")),
                        ("reference-06.png", ("Temple_Left", "Temple_Right")),
                        ("reference-05.png", ("Brow_Left", "Brow_Right")),
                        ("reference-03.png", ("Temple_Left",))):
        r = render_rows(job, layout, occ, B, A, view, parts)
        if r is not None:
            p_ = out / f"renders_{IN.short_name(view)}.png"
            Image.fromarray(r).save(p_)
            rnd[IN.short_name(view)] = str(p_)
    meas["sheets"] = {"closeups_zones": str(out / "closeups_zones.png"), "closeups_atlas": str(out / "closeups.png"),
                      "renders": rnd}
    meas["cross_view_colour"] = {}
    for view in sorted(job.views):
        meas["cross_view_colour"][IN.short_name(view)] = {
            k: cross_view_colour(job, layout, occ, d, view, IN.FT1119_PARTS) for k, d in (("before", B), ("after", A))}
    allb = [x["de00_of_medians"] for v_ in meas["cross_view_colour"].values() for x in v_["before"].values()]
    alla = [x["de00_of_medians"] for v_ in meas["cross_view_colour"].values() for x in v_["after"].values()]
    meas["cross_view_colour_summary"] = {"before_mean_de00": float(np.mean(allb)) if allb else None,
                                         "after_mean_de00": float(np.mean(alla)) if alla else None,
                                         "rows": len(alla)}
    write_json(out / "step4_measures.json", jsonable(meas))
    print(json.dumps(jsonable({"metal": meas["metal"], "fill": {p: {k: {kk: v.get(kk) for kk in (
        "streak_index_fill_over_photo", "fine_energy_fill_over_photo")} for k, v in d.items()} for p, d in
        meas["fill"].items()}, "steps": {k: {kk: v[kk] for kk in ("weighted_mean_de00", "median_de00", "max_de00")}
                                       for k, v in meas["colour_steps"].items()},
        "mirror_dL": {k: v["mean_abs_mirror_dL"] for k, v in meas["region_levels"].items()},
        "white": meas["white_highlight_share_of_photo_texels"], "sheets": meas["sheets"],
        "cross_view": meas["cross_view_colour_summary"]}), indent=1), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
