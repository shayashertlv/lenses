"""WP5 build step 3 run on FT1119 (single-source mode): the production bake and everything an owner or Astra reads
about it, written to ``data/wp5/ft1119_bake/``.

    glasses3d/.venv/Scripts/python -B -m glasses3d.tests.photoprint.bake_ft1119 [--out DIR] [--workers 4]
        [--heldout-workers 3] [--skip-checks] [--skip-heldout] [--a7-serial] [--sheets-only] [--a4-seeds 5]

Inputs: q001 (``data/reference/projection/q001.glb``), the six FT1119 photos, the WP3b cameras
(``data/wp3b/ft1119/cameras.json``), step 0's report (``data/wp5/ft1119_strip_null/report.json``: nothing registers,
so every region is painted from its anchor photo only, tier 2). Thresholds D1/D2: print 0.8 mm, edge 1.0 / 2.5 mm.

Writes DIR/ (default ``data/wp5/ft1119_bake``):
- the production outputs of ``project_photo_texture``: ``print.png`` (sRGB, with the PNG sRGB chunk), ``classes.png``,
  ``source.png`` (view index + 1 of the photo that painted each texel; 0 = not a photo texel), ``tier.png``,
  ``layout.json`` (the UV atlas layout), ``registration.json``, ``report.json`` (region statuses, ``area_table``:
  registered / anchor-only / single-view / unseen-filled with how each is painted, mirrored share, PatchMatch share
  per part, warnings) and ``report_stable.json``;
- human-readable maps: ``layout.png`` (charts by part and facing class), ``classes_vis.png``, ``source_vis.png`` and
  ``legend.json``;
- ``heldout/<view>.png`` + ``heldout/<view>_mask.png``: the production bake made WITHOUT that photo, rendered at its
  WP3b camera (the B3 / B2 renders, kept for the sheets);
- ``sheets/<part kind>_<x sign>.png`` (brow_-x, brow_+x, temple_-x, temple_+x): per photo that shows the part, cropped
  to the part: the photo | the single-source print on q001 at that photo's WP3b camera (what AR shows) | the held-out
  print (the bake without that photo, same camera: the honest view, since a photo's own row is a round trip) |
  Astra's texture | what painted each pixel (photo by view colour, mirror, PatchMatch, text / highlight fill, seam
  band), with the shares in the row header;
- ``bake.json``: the area table, the checks (A1 from the layout, A2 round trip per view, A3 read back, A4 knockout, A5
  off the seam, A7 determinism, the single-source audit from the outputs), ``A4_fillers`` (fix round 1 of step 4: the
  production filler and PyPatchMatch, EACH at the radius its own knockout chose in this bake, on the same holes: the
  A4 holes (seed 0) and pooled over ``--a4-seeds`` seeds, 8 holes each), ``A4_vs_7ac7c8f`` (the A4 verdicts and
  medians of the 7ac7c8f bake, ``data/wp5/pre_step4_bake``, beside this one's), B3 per held-out view, the B2 diagnostic
  (this bake's held-out renders in the strict leave-one-view-out form, plus step 0's stripes rows by reference), the
  sheet paths and STOP B (§9) with its reasons.

Every A2-A5 / B2 / B3 number is the independent instrument's (``tests/instruments/print_metrics.py`` via
``printproof``). Sides are the x sign, never part names (q001's Temple_Left is the -x temple = the wearer's right).
A line is printed at least every 30 s.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from glasses3d.g3d import printuv as PU
from glasses3d.g3d_host.photoprint import inputs as IN
from glasses3d.g3d_host.photoprint import project as PR
from glasses3d.g3d_host.photoprint import regions as RG
from glasses3d.g3d_host.photoprint import texels as TX
from glasses3d.g3d_host.photoprint.compose import CLASS_CODES
from glasses3d.g3d_host.photoprint.procs import Progress, opt_out_ecoqos, pool_map
from glasses3d.g3d_host.photoprint.report import jsonable, write_json
from glasses3d.tests.instruments import print_metrics as PM
from glasses3d.tests.photoprint import print_proof as PPF
from glasses3d.tests.photoprint import printproof as PP

OUT = IN.REPO / "glasses3d" / "data" / "wp5" / "ft1119_bake"
STEP0 = PPF.STEP0
PREV = IN.REPO / "glasses3d" / "data" / "wp5" / "ft1119_print" / "43aea1bb"     # earlier pool + serial bakes
PRE_STEP4 = IN.REPO / "glasses3d" / "data" / "wp5" / "pre_step4_bake"            # the 7ac7c8f bake (PyPatchMatch)
COMPARE = ("patchmatch",)                                                        # fillers compared with the production one
TH = PPF.TH
PIXEL_FILES = ("print.png", "classes.png", "source.png", "tier.png")

VIEW_RGB = [(230, 159, 0), (86, 180, 233), (0, 158, 115), (240, 228, 66), (0, 114, 178), (204, 121, 167)]
CLASS_RGB = {"mirror": (130, 70, 170), "patchmatch": (45, 45, 45), "seam_band": (150, 150, 20),
             "highlight_fill": (120, 255, 120), "text_fill": (220, 30, 30), "chart_seam": (255, 0, 255),
             "no_texture": (128, 128, 128), "padding": (255, 255, 255), "empty": (255, 255, 255)}
BG, OTHER = 246.0, 205.0
SIDE_WORDS = {-1: "-x (the wearer's right)", 1: "+x (the wearer's left)"}
MIN_PART_PX = 600
PANEL_W = 1400          # a wide crop's panels: at most this wide ...
WIDE_H = 300            # ... and this tall (before the caption column)
TALL_H = 760            # a tall crop's panels side by side: at most this tall ...
ROW_W = 1800            # ... and the row at most this wide


def _font(size: int = 16):
    try:
        return ImageFont.load_default(size=size)
    except Exception:                                   # noqa: BLE001 (older Pillow: bitmap font)
        return ImageFont.load_default()


def _short(view: str) -> str:
    return IN.short_name(view)


# ----------------------------------------------------------------------------- held-out jobs (B3 + B2 diagnostic)
def _heldout_job(view, tx, radii):
    opt_out_ecoqos()
    job = IN.load_job()
    return PP.heldout_b3(job, IN.FT1119_PARTS, view, radii_mm=radii, tx=tx, b2=True, keep_render=True)


# ----------------------------------------------------------------------------- human-readable maps
def write_maps(res: dict, out: Path, regions) -> dict:
    tex, lay = res["tex"], res["layout"]
    H, W = lay.height, lay.width
    views = res["views"]
    # classes and sources in false colour
    cls_img = tex.image(res["classes"], 0, np.uint8)
    src_img = tex.image(res["source"], 0, np.uint8)
    vis = np.full((H, W, 3), 255, np.uint8)
    names = {v: k for k, v in CLASS_CODES.items()}
    for code, name in names.items():
        if name in CLASS_RGB:
            vis[cls_img == code] = CLASS_RGB[name]
    for k in range(len(views)):
        vis[(src_img == k + 1)] = VIEW_RGB[k % len(VIEW_RGB)]
    Image.fromarray(vis).save(out / "classes_vis.png")
    svis = np.full((H, W, 3), 255, np.uint8)
    used = tex.image(np.ones(len(tex), bool), False, bool)
    svis[used] = (60, 60, 60)
    for k in range(len(views)):
        svis[src_img == k + 1] = VIEW_RGB[k % len(VIEW_RGB)]
    Image.fromarray(svis).save(out / "source_vis.png")
    # layout: charts coloured by part x facing class, chart outlines, region labels
    facing = sorted({r.facing for r in regions})
    parts = list(res["report"]["parts"])
    base = {p: c for p, c in zip(parts, [(70, 110, 200), (200, 90, 70), (60, 160, 90), (170, 120, 30)])}
    reg_img = tex.image(tex.region, -1, np.int64)
    lim = np.full((H, W, 3), 255, np.uint8)
    by = {r.rid: r for r in regions}
    for r in regions:
        c = np.array(base.get(r.part_name, (120, 120, 120)), float)
        f = facing.index(r.facing) / max(len(facing) - 1, 1)
        lim[reg_img == r.rid] = np.clip(c * (0.55 + 0.75 * f), 0, 255).astype(np.uint8)
    chart_img = tex.image(lay.chart[tex.row] if hasattr(tex, "row") else np.zeros(len(tex), np.int64), -1, np.int64)
    edge = np.zeros((H, W), bool)
    edge[:-1] |= chart_img[:-1] != chart_img[1:]
    edge[:, :-1] |= chart_img[:, :-1] != chart_img[:, 1:]
    lim[edge] = (0, 0, 0)
    im = Image.fromarray(lim)
    dr = ImageDraw.Draw(im)
    fnt = _font(14)
    for rid in np.unique(reg_img[reg_img >= 0]):
        ys, xs = np.nonzero(reg_img == rid)
        if len(ys) < 4000:
            continue
        r = by[int(rid)]
        dr.text((int(np.median(xs)) - 40, int(np.median(ys)) - 7), f"{r.part_name[:5]}{'+' if r.side > 0 else '-'}x {r.facing}",
                fill=(0, 0, 0), font=fnt)
    im.save(out / "layout.png")
    legend = {"source_vis / classes_vis view colours": {_short(v): VIEW_RGB[k % len(VIEW_RGB)] for k, v in enumerate(views)},
              "classes_vis fill colours": CLASS_RGB,
              "source.png values": {str(k + 1): v for k, v in enumerate(views)} | {"0": "not a photo texel"},
              "classes.png values": CLASS_CODES,
              "layout.png": "charts coloured by part (blue Brow_Left, red Brow_Right, green Temple_Left, ochre "
                            "Temple_Right; q001 names, sides are the x signs in the labels), lighter = later facing "
                            f"class in {facing}; black = chart edges"}
    write_json(out / "legend.json", legend)
    return {"layout_png": str(out / "layout.png"), "classes_vis": str(out / "classes_vis.png"),
            "source_vis": str(out / "source_vis.png"), "legend": str(out / "legend.json")}


# ----------------------------------------------------------------------------- comparison sheets
def _view_hits(job, layout, scene, view: str) -> dict:
    v = job.views[view]
    H, W = v.shape
    r = scene.render(v.camera, (H, W), 1, want_bary=True)
    fid = r["face_id"]
    nF = len(job.mesh.F)
    lrow = layout.face_index(nF)
    lay_hit = (fid >= 0) & (fid < nF)
    row = np.full((H, W), -1, np.int64)
    row[lay_hit] = lrow[fid[lay_hit]]
    m = row >= 0
    ii, jj = np.nonzero(m)
    b = r["bary"][ii, jj].astype(np.float64)
    w = np.stack([1.0 - b[:, 0] - b[:, 1], b[:, 0], b[:, 1]], 1)
    uv = (w[:, :, None] * np.asarray(layout.uv, np.float64)[row[ii, jj]]).sum(1)
    face = np.full((H, W), -1, np.int64)
    face[lay_hit] = fid[lay_hit]
    return {"hit": fid >= 0, "m": m, "ii": ii, "jj": jj, "uv": uv, "bary2": r["bary"][ii, jj], "face": face}


def _nearest(img: np.ndarray, uv: np.ndarray) -> np.ndarray:
    H, W = img.shape[:2]
    x = np.clip(np.floor(uv[:, 0]).astype(np.int64), 0, W - 1)
    y = np.clip(np.floor(uv[:, 1]).astype(np.int64), 0, H - 1)
    return img[y, x]


def _caption(img: np.ndarray, text: str, w_cap: int = 170) -> np.ndarray:
    cap = Image.new("RGB", (w_cap, img.shape[0]), (255, 255, 255))
    ImageDraw.Draw(cap).multiline_text((6, 4), text, fill=(0, 0, 0), font=_font(15), spacing=2)
    return np.concatenate([np.asarray(cap), img], 1)


def _header(text: str, width: int, size: int = 16) -> np.ndarray:
    lines = text.split("\n")
    h = 8 + len(lines) * (size + 4)
    im = Image.new("RGB", (width, h), (255, 255, 255))
    ImageDraw.Draw(im).multiline_text((6, 4), text, fill=(0, 0, 0), font=_font(size), spacing=4)
    return np.asarray(im)


def _wrap(text: str, width_px: int, char_px: float = 7.6) -> str:
    """Greedy word wrap of each line to about ``width_px`` (default font at 15-17 px)."""
    n = max(20, int(width_px / char_px))
    out = []
    for line in text.split("\n"):
        cur = ""
        for w in line.split(" "):
            if cur and len(cur) + 1 + len(w) > n:
                out.append(cur)
                cur = w
            else:
                cur = f"{cur} {w}" if cur else w
        out.append(cur)
    return "\n".join(out)


def _arrange(panels: list, w: int, h: int) -> np.ndarray:
    """Panels of one photo: a wide crop (w >= 1.5 h) stacks them vertically (each <= PANEL_W wide, <= WIDE_H tall);
    a tall crop puts them side by side (each <= TALL_H tall, the row <= ROW_W wide). Integer nearest upscaling only
    (pixels stay pixels), 1-4x."""
    if w >= 1.5 * h:
        k = int(np.clip(min(PANEL_W // max(w, 1), WIDE_H // max(h, 1)), 1, 4))
        rows = []
        for cap, a in panels:
            big = np.asarray(Image.fromarray(a).resize((a.shape[1] * k, a.shape[0] * k), Image.NEAREST))
            rows.append(_caption(big, cap))
            rows.append(np.full((3, rows[-1].shape[1], 3), 255, np.uint8))
        return np.concatenate(rows, 0)
    k = int(np.clip(min(TALL_H // max(h, 1), (ROW_W // len(panels)) // max(w, 1)), 1, 4))
    cols = []
    for cap, a in panels:
        big = np.asarray(Image.fromarray(a).resize((a.shape[1] * k, a.shape[0] * k), Image.NEAREST))
        capim = _header(_wrap(cap.replace("\n", " "), big.shape[1], 7.2), big.shape[1], 14)
        col = np.concatenate([capim, big], 0)
        cols.append(col)
        cols.append(np.full((col.shape[0], 4, 3), 255, np.uint8))
    Hm = max(c.shape[0] for c in cols)
    cols = [np.pad(c, ((0, Hm - c.shape[0]), (0, 0), (0, 0)), constant_values=255) for c in cols]
    return np.concatenate(cols, 1)


def sheets(job, res_like: dict, heldout: dict, out: Path, prog) -> list[str]:
    """One sheet per print part (named by kind and x sign); see the module docstring."""
    lay, atlas = res_like["layout"], res_like["atlas"].astype(np.float64)
    cls_img, src_img = res_like["classes_img"], res_like["source_img"]
    views = res_like["views"]
    scene = res_like["scene"]
    regions = res_like["regions"]
    side_of = {}
    for r in regions:
        side_of[r.part_name] = r.side
    names = job.mesh.names
    code_name = {v: k for k, v in CLASS_CODES.items()}
    hits = {n: _view_hits(job, lay, scene, n) for n in views}
    paths = []
    for part in IN.FT1119_PARTS:
        pid = names.index(part)
        kind = "brow" if part.lower().startswith("brow") else "temple"
        sgn = side_of.get(part, 0)
        blocks = []
        for n in views:
            hv = hits[n]
            v = job.views[n]
            H, W = v.shape
            pf = np.zeros((H, W), bool)
            fm = hv["face"] >= 0
            pf[fm] = job.mesh.part[hv["face"][fm]] == pid
            pm = pf & hv["m"]
            if pm.sum() < MIN_PART_PX:
                continue
            ys, xs = np.nonzero(pm)
            pad = 10
            y0, y1 = max(ys.min() - pad, 0), min(ys.max() + pad + 1, H)
            x0, x1 = max(xs.min() - pad, 0), min(xs.max() + pad + 1, W)
            sel = pm[hv["ii"], hv["jj"]]
            ii, jj, uv = hv["ii"][sel], hv["jj"][sel], hv["uv"][sel]
            base = np.full((H, W, 3), BG)
            base[hv["hit"]] = OTHER
            prn = base.copy()
            prn[ii, jj] = PM.sample_gltf(atlas, uv)
            ast = base.copy()
            if job.astra is not None:
                ast[ii, jj] = job.astra.colours(job.mesh.part, hv["face"][ii, jj], hv["bary2"][sel])
            hold = None
            if n in heldout:
                hold = base.copy()
                hold[ii, jj] = heldout[n]["render"][ii, jj]
            pb = base.copy()
            c = _nearest(cls_img, uv).astype(np.int64)
            s = _nearest(src_img, uv).astype(np.int64)
            col = np.full((len(c), 3), 255.0)
            for code, nm in code_name.items():
                if nm in CLASS_RGB:
                    col[c == code] = CLASS_RGB[nm]
            for k in range(len(views)):
                col[s == k + 1] = VIEW_RGB[k % len(VIEW_RGB)]
            pb[ii, jj] = col
            # shares on the part's pixels in this view
            npx = len(c)
            own = int((s == views.index(n) + 1).sum())
            other = {(_short(views[k - 1])): int((s == k).sum()) for k in range(1, len(views) + 1)
                     if k != views.index(n) + 1 and (s == k).any()}
            fills = {code_name[k]: int((c == k).sum()) for k in np.unique(c)
                     if code_name.get(int(k)) not in ("photo_anchor", "photo_tier1")}
            pct = lambda x: f"{100.0 * x / max(npx, 1):.0f}%"
            fill_txt = ", ".join(f"{k} {pct(x)}" for k, x in sorted(fills.items(), key=lambda t: -t[1]) if x)
            other_txt = ", ".join(f"{k} {pct(x)}" for k, x in sorted(other.items(), key=lambda t: -t[1]))
            head = (f"photo {_short(n)} ({v.kind}), {npx} part px at {job.ppm(n):.1f} px/mm.  Painted from THIS photo "
                    f"{pct(own)} (that share of row 2 is a round trip)"
                    + (f"; other photos {other_txt}" if other_txt else "") + (f"; fill: {fill_txt}" if fill_txt else ""))
            crop = lambda a: np.clip(a[y0:y1, x0:x1], 0, 255).astype(np.uint8)
            panels = [("photo", crop(v.image.astype(float))), ("single-source\nprint on q001\n(production)", crop(prn))]
            if hold is not None:
                panels.append(("held-out print\n(bake without\nthis photo)", crop(hold)))
            panels += [("Astra's\ntexture", crop(ast)), ("painted by\n(view colours,\nfills)", crop(pb))]
            blk = _arrange(panels, x1 - x0, y1 - y0)
            blk = np.concatenate([_header(_wrap(head, blk.shape[1]), blk.shape[1], 15), blk], 0)
            blocks.append(blk)
            bp = out / "sheets" / "blocks" / f"{kind}_{'+' if sgn > 0 else '-'}x_{_short(n)}.png"
            bp.parent.mkdir(parents=True, exist_ok=True)
            Image.fromarray(blk).save(bp)
        if not blocks:
            continue
        Wm = max(b.shape[1] for b in blocks)
        legend = ("painted-by colours: " + ", ".join(f"{_short(v)} {VIEW_RGB[i]}" for i, v in enumerate(views))
                  + "; mirror purple, PatchMatch dark grey, text fill red, highlight fill light green, seam band olive, "
                    "chart seam magenta.  Renders: first hit on q001 + decals through each photo's WP3b camera (S, no "
                    "parallax); light grey = other model surfaces (lenses, metal, decals, the other parts).")
        title = (f"FT1119 {kind}, side {SIDE_WORDS.get(sgn, sgn)}, q001 part '{part}'.  Single-source print (each "
                 f"region from its anchor photo only; nothing registers on FT1119).\n" + legend)
        blocks = [np.pad(b, ((0, 12), (0, Wm - b.shape[1]), (0, 0)), constant_values=255) for b in blocks]
        sheet = np.concatenate([_header(_wrap(title, Wm, 8.4), Wm, 17)] + blocks, 0)
        p = out / "sheets" / f"{kind}_{'+' if sgn > 0 else '-'}x.png"
        p.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(sheet).save(p)
        paths.append(str(p))
        prog(f"sheet {p.name}: {len(blocks)} photos, {sheet.shape[1]} x {sheet.shape[0]}", force=True)
    return paths


# ----------------------------------------------------------------------------- determinism (A7) without a rerun
def pixel_identity(out: Path, prev: Path) -> dict:
    rows = {}
    for d in (prev, prev / "a7_serial"):
        if not (d / "print.png").is_file():
            continue
        rows[str(d)] = {n: bool(np.array_equal(np.asarray(Image.open(out / n)), np.asarray(Image.open(d / n))))
                        for n in PIXEL_FILES}
    return {"compared_with": rows, "pass": bool(rows) and all(all(r.values()) for r in rows.values()),
            "method": "decoded pixels of print/classes/source/tier equal to the earlier pool bake (workers=4) and the "
                      "earlier serial bake (workers=1) of the same pipeline (this run differs only by the report's "
                      "area_table and print.png's sRGB chunk, so file sha256 cannot be compared)"}


# ----------------------------------------------------------------------------- A4 per filler (fix round 1 of step 4)
A4_KEYS = ("median_log_energy", "median_seam_ratio", "median_mean_colour_de00", "mean_colour_limit", "criteria",
           "pass", "holes")


def _a4_brief(a: dict) -> dict:
    out = {k: a.get(k) for k in A4_KEYS}
    f = a.get("real_vs_real_floor")
    if f:
        out["real_vs_real_floor"] = {"median_log_energy": f["median_log_energy"], "holes": f["holes"],
                                     "spread_0.25_1mm": f["spread"]["log_energy_0.25_1mm"]}
    return out


def a4_fillers(res: dict, parts, seeds: int, prog) -> dict:
    """Every filler (the production one and ``COMPARE``) at the radius ITS OWN knockout chose in this bake
    (``res['radii_by_method']``), graded by the instrument on the same holes: the A4 holes (seed 0, what A4 grades) and
    the holes of ``seeds`` seeds pooled (8 per seed). The comparison behind the production choice; graded, not chosen
    from (the production filler is a visual decision, ``report['fill']['choice']``)."""
    radii_by = res.get("radii_by_method") or {}
    out = {"seeds": int(seeds), "holes_per_seed": PP.A4_HOLES, "radii_by_method": radii_by,
           "note": "each filler at its own knockout-chosen radius, same holes and seeds; the production verdict is A4"}
    rows = {}
    for m, radii in radii_by.items():
        rows[m] = {}
        for p in parts:
            per, pooled, t = [], [], time.perf_counter()
            for sd in range(int(seeds)):
                k = PP.knockout(res, p, seed=sd, method=m, radius_mm=radii[p])
                per.append(k)
                pooled += k["holes"]
            rows[m][p] = {"radius_mm": radii[p], "a4_holes": _a4_brief(per[0]["a4"]),
                          "pooled": _a4_brief(PM.a4_criteria(pooled)),
                          "per_seed_fine_energy": [k["a4"].get("median_log_energy", [None])[0] for k in per],
                          "skipped": sum(len(k["skipped"]) for k in per)}
            prog(f"A4 {m} {p} r {radii[p]} mm: holes {rows[m][p]['a4_holes']['median_log_energy']} pooled "
                 f"{rows[m][p]['pooled']['median_log_energy']} seam {rows[m][p]['pooled']['median_seam_ratio']} "
                 f"({time.perf_counter() - t:.0f} s)", force=True)
    out["by_method"] = rows
    prod = (res.get("report") or {}).get("fill", {}).get("method")
    if prod in rows:
        better = {}
        for p in parts:
            def key(m):
                a = rows[m][p]["pooled"]
                return max(a["median_log_energy"])
            best = min(rows, key=key)
            better[p] = {"closest_to_a4_pooled_fine_energy": best, "production": prod,
                         "production_costs_a4_here": best != prod,
                         "pooled_worst_energy": {m: key(m) for m in rows},
                         "pooled_seam_ratio": {m: rows[m][p]["pooled"]["median_seam_ratio"] for m in rows},
                         "a4_holes_pass": {m: rows[m][p]["a4_holes"]["pass"] for m in rows}}
        out["per_part"] = better
    return out


def a4_vs_7ac7c8f(a4_now: dict) -> dict:
    """The 7ac7c8f bake's A4 (PyPatchMatch at its knockout radii, holes drawn then) beside this bake's, per part; a
    pass that turned into a fail is listed as a regression. The 8 texels/mm pins of tests/photoprint/test_print_bake.py
    are recorded there."""
    f = PRE_STEP4 / "bake.json"
    if not f.is_file():
        return {"available": False}
    d = json.loads(f.read_text(encoding="utf-8"))
    rows, reg = {}, []
    for p, r in (d.get("A4") or {}).items():
        a0, a1 = r.get("a4") or {}, (a4_now.get(p) or {}).get("a4") or {}
        rows[p] = {"7ac7c8f": {"radius_mm": r.get("radius_mm"), "filler": "patchmatch", **_a4_brief(a0)},
                   "now": {"radius_mm": (a4_now.get(p) or {}).get("radius_mm"),
                           "filler": (a4_now.get(p) or {}).get("method"), **_a4_brief(a1)}}
        if a0.get("pass") and not a1.get("pass"):
            reg.append(p)
    return {"available": True, "source": str(f), "parts": rows, "regressions_16_tx_per_mm": reg,
            "regressions_8_tx_per_mm": ["Brow_Right"],
            "note": "16 tx/mm from the two bakes' bake.json; 8 tx/mm from test_print_bake's A4_KNOWN_AT_8 pin (7ac7c8f: "
                    "Brow_Right passed; since step 4 it fails). The holes differ: each bake draws them among its own "
                    "photo texels (step 4 punched metal / glint holes into them)."}


# ----------------------------------------------------------------------------- summaries
def b3_by_view(heldout_rows: dict) -> dict:
    out = {}
    for h, rows in heldout_rows.items():
        el = [r for r in rows if r.get("eligible")]
        out[_short(h)] = {"rows": len(rows), "eligible": len(el), "passing": sum(1 for r in el if r.get("pass")),
                          "verdict": ("no eligible row" if not el else
                                      "pass" if all(r.get("pass") for r in el) else "fail"),
                          "regions": [{"region": r["region"], "pixels": r["pixels"],
                                       "projected_share": round(r["projected_share"], 3), "eligible": r["eligible"],
                                       "pass": r.get("pass"), "criteria": r.get("criteria"),
                                       "anchor_in_bake": r.get("anchor_in_bake"),
                                       "projection": r.get("projection"), "astra": r.get("astra")} for r in rows]}
    return out


def b2_summary(heldout_rows: dict) -> dict:
    rows = []
    for h, rr in heldout_rows.items():
        for r in rr:
            b = r.get("b2")
            if not b:
                continue
            cr = b.get("criteria") or {}
            rows.append({"held_out": _short(h), "region": r["region"], "pixels": b["pixels"],
                         "b2_eligible": b["b2_eligible"], "b2_pass": b["b2_pass"],
                         "C1_margin": (cr.get("C1") or {}).get("margin"), "C1": (cr.get("C1") or {}).get("pass"),
                         "C2_margin": (cr.get("C2") or {}).get("margin"), "C2": (cr.get("C2") or {}).get("pass"),
                         "C3": (cr.get("C3") or {}).get("pass"), "C4": (cr.get("C4") or {}).get("pass"),
                         "de00_gain": [(b.get("projection") or {}).get("de00_gain_mean"),
                                       (b.get("astra") or {}).get("de00_gain_mean")],
                         "bp_1_4px": [(b.get("projection") or {}).get("bp_1_4px_r"),
                                      (b.get("astra") or {}).get("bp_1_4px_r")],
                         "energy": [(b.get("projection") or {}).get("energy_0.4_1.6mm"),
                                    (b.get("projection") or {}).get("energy_1_4px")],
                         "contamination": (b.get("projection") or {}).get("contamination")})
    el = [r for r in rows if r["b2_eligible"]]
    step0 = None
    if STEP0.is_file():
        d0 = json.loads(STEP0.read_text(encoding="utf-8"))
        step0 = {"path": str(STEP0), "sha256": hashlib.sha256(STEP0.read_bytes()).hexdigest(),
                 "protocol": "step 0's stripes protocol: registration fitted on one fold, graded on the other; direct "
                             "per-pixel bake from the region's other admitted views (stage-2 stand-in)",
                 "rows": d0.get("diagnostic_check"), "sheets": str(STEP0.parent / "sheets_diagnostic")}
    return {"role": "DIAGNOSTIC for Astra, never a gate (owner decision: single-source mode; nothing registers)",
            "protocol": "strict leave-one-view-out (§7's B0 form) on this bake: the production pipeline without photo h, "
                        "rendered at h's WP3b camera on S; C1-C4 of §8 on E = region eroded 2 px & acetate eroded "
                        "1 px & painted from a photo texel; >= 1000 E px for eligibility",
            "rows": rows, "eligible": len(el), "passing": sum(1 for r in el if r["b2_pass"]),
            "step0_stripes": step0}


# ----------------------------------------------------------------------------- main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--heldout-workers", type=int, default=3)
    ap.add_argument("--skip-checks", action="store_true", help="skip A2-A5 and the output audit")
    ap.add_argument("--skip-heldout", action="store_true", help="skip B3 / B2 (and the held-out sheet column)")
    ap.add_argument("--a7-serial", action="store_true", help="also rerun the bake serially (workers=1) for A7")
    ap.add_argument("--sheets-only", action="store_true",
                    help="remake the maps and sheets from the written outputs (no bake)")
    ap.add_argument("--a4-seeds", type=int, default=5,
                    help="seeds of 8 A4 holes pooled per part in the filler comparison (A4_fillers)")
    a = ap.parse_args(argv)
    opt_out_ecoqos()
    prog = Progress("bake_ft1119")
    with prog.heartbeat():
        return _sheets_only(a, prog) if a.sheets_only else _main(a, prog)


def _occ(job):
    return TX.OcclusionScene(job, job.part_ids(IN.FT1119_PARTS), TX.decal_geometry(PR.job_glb(job), job.mesh.skipped))


def _read_heldout(out: Path, views) -> dict:
    hd = {}
    for n in views:
        p = out / "heldout" / f"{_short(n)}.png"
        if p.is_file():
            hd[n] = {"render": np.asarray(Image.open(p).convert("RGB")),
                     "mask": np.asarray(Image.open(out / "heldout" / f"{_short(n)}_mask.png")) > 0}
    return hd


def _sheets_only(a, prog) -> int:
    out = Path(a.out)
    job = IN.load_job()
    rep = json.loads((out / "report.json").read_text(encoding="utf-8"))
    tx = float(rep["layout"]["tx_per_mm"])
    lay = PU.layout(job.mesh.V, job.mesh.F, job.mesh.part, job.mesh.names, list(IN.FT1119_PARTS), tx,
                    atlas_max=PR.ATLAS_MAX, mirror=True)
    assert (lay.width, lay.height) == (rep["layout"]["width"], rep["layout"]["height"]), "layout differs from the bake"
    regions = RG.build_regions(job, list(IN.FT1119_PARTS), RG.rigid_groups(job), n_samples=64)
    occ = _occ(job)
    res_like = {"layout": lay, "atlas": np.asarray(Image.open(out / "print.png").convert("RGB")),
                "classes_img": np.asarray(Image.open(out / "classes.png")),
                "source_img": np.asarray(Image.open(out / "source.png")), "views": rep["views"],
                "scene": occ.scene(job.mesh.V), "regions": regions}
    paths = sheets(job, res_like, _read_heldout(out, rep["views"]), out, prog)
    ov = PPF.overview(job, {"layout": lay, "atlas": res_like["atlas"], "occ": occ}, out / "sheets" / "overview.png")
    bj = out / "bake.json"
    if bj.is_file():
        d = json.loads(bj.read_text(encoding="utf-8"))
        d["sheets"] = paths
        d["overview"] = ov
        write_json(bj, jsonable(d))
    print(json.dumps({"sheets": paths}), flush=True)
    return 0


def _main(a, prog) -> int:
    t0 = time.perf_counter()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    job = IN.load_job()
    prog(f"production bake -> {out} (q001 sha {job.sources['glb_sha256'][:12]}, {len(job.views)} photos)", force=True)
    res = PR.project_photo_texture(job, IN.FT1119_PARTS, out_dir=out, workers=a.workers, step0_report=STEP0,
                                   compare_fill=COMPARE, **TH)
    tx = float(res["layout"].tx_per_mm)
    rep = res["report"]
    bake = {"out": str(out), "inputs": rep["inputs"], "mode": rep["mode"], "tx_per_mm": tx,
            "atlas": [res["layout"].width, res["layout"].height], "thresholds": rep["thresholds"],
            "area_table": rep["area_table"], "status_area_tool": rep["status_area"],
            "texture_parts": rep["texture_parts"], "below_min_cover": rep["below_min_cover"],
            "warnings": rep["warnings"], "decisions": PPF.DECISIONS, "A1": rep["checks"]["A1"]}
    bake["maps"] = write_maps(res, out, res["regions"])
    # step 4: the pipeline changed (metal model, fill, gains, seams), so the earlier bakes are no reference for A7;
    # A7 is the serial rerun of THIS pipeline (--a7-serial), the earlier comparison is kept as a record only
    bake["A7_vs_step3_record"] = (pixel_identity(out, PREV) if PREV.is_dir() else {"pass": None}) | {
        "note": "expected to differ: step 4 changed the pipeline; not a verdict"}
    if not a.skip_checks:
        bake["audit"] = PPF.audit(res)
        bake["audit_outputs"] = PP.audit_outputs(job, res, out)
        bake["A3_readback"] = PP.readback_a3(job, res, out)
        prog(f"A3 read back pass {bake['A3_readback']['pass']}; audit from outputs pass {bake['audit_outputs']['pass']}",
             force=True)
        bake["A5"] = PP.seams(job, res, out)
        prog(f"A5 pass {bake['A5']['pass']}", force=True)
        bake["A4"] = {p: PP.knockout(res, p) for p in IN.FT1119_PARTS}
        prog("A4 " + json.dumps({p: r["a4"].get("pass") for p, r in bake["A4"].items()}), force=True)
        bake["A4_vs_7ac7c8f"] = a4_vs_7ac7c8f(bake["A4"])
        # fix round 1: every filler at ITS OWN knockout radius, same holes (the comparison behind the choice)
        bake["A4_fillers"] = a4_fillers(res, IN.FT1119_PARTS, a.a4_seeds, prog)
        bake["fill_choice"] = rep["fill"].get("choice")
        views = sorted(job.views)
        bake["A2"] = dict(zip(views, pool_map(PPF._a2_job, [(v, tx) for v in views], min(a.workers, 6))))
        prog("A2 " + json.dumps({_short(v): r["pass"] for v, r in bake["A2"].items()}), force=True)
    if a.a7_serial:
        prog("A7: serial bake (workers=1)", force=True)
        PR.project_photo_texture(job, IN.FT1119_PARTS, out_dir=out / "a7_serial", tx_per_mm=tx, workers=1,
                                 step0_report=STEP0, compare_fill=COMPARE, log=print, **TH)
        names = ("print.png", "classes.png", "source.png", "tier.png", "registration.json", "report_stable.json")
        sha = {n: (hashlib.sha256((out / n).read_bytes()).hexdigest(),
                   hashlib.sha256((out / "a7_serial" / n).read_bytes()).hexdigest()) for n in names}
        bake["A7_serial"] = {"files": {n: {"pool": x, "serial": y, "equal": x == y} for n, (x, y) in sha.items()},
                             "pass": all(x == y for x, y in sha.values())}
        bake["A7"] = bake["A7_serial"]
        prog(f"A7 serial {bake['A7_serial']['pass']}", force=True)
    heldout = {}
    if not a.skip_heldout:
        views = sorted(job.views)
        prog(f"held-out bakes (B3 + B2 diagnostic): {len(views)} jobs, {a.heldout_workers} workers", force=True)
        hres = pool_map(_heldout_job, [(v, tx, res["radii"]) for v in views], a.heldout_workers, progress=prog)
        (out / "heldout").mkdir(exist_ok=True)
        rows = {}
        for r in hres:
            h = r["held_out"]
            rows[h] = r["rows"]
            Image.fromarray(r["render"]).save(out / "heldout" / f"{_short(h)}.png")
            Image.fromarray((r["mask"] * 255).astype(np.uint8)).save(out / "heldout" / f"{_short(h)}_mask.png")
            heldout[h] = {"render": r["render"], "mask": r["mask"]}
        bake["B3"] = b3_by_view(rows)
        el = [x for rr in rows.values() for x in rr if x["eligible"]]
        bake["B3_summary"] = {"eligible_rows": len(el), "passing": sum(1 for x in el if x["pass"]),
                              "by_view": {k: v["verdict"] for k, v in bake["B3"].items()}}
        bake["B2_diagnostic"] = b2_summary(rows)
        prog(f"B3 {bake['B3_summary']}; B2 diagnostic {bake['B2_diagnostic']['passing']} / "
             f"{bake['B2_diagnostic']['eligible']} eligible rows pass", force=True)
    # sheets
    res_like = {"layout": res["layout"], "atlas": res["atlas"],
                "classes_img": res["tex"].image(res["classes"], 0, np.uint8),
                "source_img": res["tex"].image(res["source"], 0, np.uint8), "views": res["views"],
                "scene": res["occ"].scene(job.mesh.V), "regions": res["regions"]}
    bake["sheets"] = sheets(job, res_like, heldout, out, prog)
    bake["overview"] = PPF.overview(job, res, out / "sheets" / "overview.png")
    if not a.skip_checks and not a.skip_heldout:
        proof_like = {"A1": rep["checks"]["A1"], "A2": bake["A2"], "A3_readback": bake["A3_readback"],
                      "A4": bake["A4"], "audit_outputs": bake["audit_outputs"], "A5": bake["A5"],
                      "A7": bake.get("A7_serial") or {"pass": None}, "B3_summary": bake["B3_summary"]}
        bake["STOP_B"] = PPF.stop_b(proof_like)
        prog(("STOP B FIRED: " + " | ".join(bake["STOP_B"]["reasons"])) if bake["STOP_B"]["fired"]
             else "STOP B not fired", force=True)
    bake["seconds"] = round(time.perf_counter() - t0, 1)
    write_json(out / "bake.json", jsonable(bake))
    print(json.dumps({"out": str(out), "tx_per_mm": tx, "atlas": bake["atlas"], "sheets": bake["sheets"],
                      "B3": bake.get("B3_summary"), "area": {k: v["share"] for k, v in
                                                             bake["area_table"]["groups"].items()}}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
