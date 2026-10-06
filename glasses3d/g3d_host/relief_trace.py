"""WP6 tracing helpers (plan/WP6_DESIGN.md §7): photo evidence -> SUGGESTED temple parameters. They never write a
scene; Astra (or, for the $0 tool test, Claude) decides what goes into the spec.

    trace_outline(job, spec, builder, views=("05", "01"))   render-and-compare: per image column the photo's top /
                        bottom edge vs the model's (within +-60 px), lifted at the model's own hit; each knot moves by
                        the median residual within +-2.5 mm of its station; 3 iterations; knots where 05 and 01 differ
                        by > 1 mm are flagged and taken from the view with the smaller spread
    trace_creases(job, guides, model, fits)               guide endpoints (px) snapped to the L* gradient ridge
                        (perpendicular profiles, line fit), corners as line intersections, lifted onto the current
                        model surface ((d, y) of the +X chart; 01 mirrored), sigma from the line fits and 05 vs 01
    trace_plan(job, model, fits, views=("03", "06"))     inner line and arm thickness corrections from each temple's
                        own arm band (d 60-100) envelopes, per view and side
    trace_temple(job, guides, params, fits)               the three above + theta_k + a starting spec

`guides` = {view: {crease name: [[u0, v0], [u1, v1]]}} in native px; corners = {name: (crease, crease)}. The Prada
creases are "AB" (F1 / F2), "AC" (F2 / F3) and "BC" (F2 / F4); A = AB x AC, B = AB x BC, C = AC x BC.
"""
from __future__ import annotations

import copy
import math

import cv2
import numpy as np
from scipy import ndimage

from ..g3d.temple import build as TB
from . import cameras as C
from . import raster
from . import relief_rules as RR
from .relief_check import chart_of, lightness
from .relief_views import Model, render_model, side_outline

PRADA_CORNERS = {"A": ("AB", "AC"), "B": ("AB", "BC"), "C": ("AC", "BC")}
OUTLINE_WINDOW_MM = 2.5
OUTLINE_MIN_SAMPLES = 5
OUTLINE_FLAG_MM = 1.0
PEAK_MIN = 0.3            # a crease ridge: |grad L*| above this and PEAK_RATIO x the profile median
PEAK_RATIO = 3.0
END_TRIM = 0.15          # crease snapping skips the outer 15 % of a guide (other creases meet there)


# --------------------------------------------------------------------------------------------- outline
def outline_residuals(job, model, key: str, fit: dict) -> dict:
    near = 1 if math.sin(math.radians(fit["camera"].yaw)) > 0 else -1
    r = render_model(job, model, key, fit["camera"], fit["theta"], points=True)
    return side_outline(job, key, r, fit["camera"], near)


def _knot_moves(chain, samples, sign) -> tuple[list, list]:
    """Per knot (d, move, spread, n): the median residual within +-2.5 mm; a knot without enough samples takes the
    move interpolated from its neighbours along d (the nearest one at the ends), flagged with n = 0 / spread None."""
    out, moves = [], []
    A = np.asarray(samples, float).reshape(-1, 2)
    for d, y in chain:
        sel = np.abs(A[:, 0] - d) <= OUTLINE_WINDOW_MM if len(A) else np.zeros(0, bool)
        if sel.sum() >= OUTLINE_MIN_SAMPLES:
            moves.append((d, float(np.median(A[sel, 1])), float(np.std(A[sel, 1])), int(sel.sum())))
        else:
            moves.append((d, None, None, int(sel.sum())))
    have = [(m[0], m[1]) for m in moves if m[1] is not None]
    if have:
        hd, hv = np.array([h[0] for h in have]), np.array([h[1] for h in have])
        moves = [m if m[1] is not None else (m[0], float(np.interp(m[0], hd, hv)), 99.0, 0) for m in moves]
    for (d, y), m in zip(chain, moves):
        out.append([d, y + sign * (m[1] or 0.0)])
    return out, moves


def trace_outline(job, spec: dict, builder, fits: dict, views=("05", "01"), iters: int = 3, log=None) -> dict:
    """Suggested top / bottom chains. `builder(top, bottom)` returns the spec for a new outline (so nodes that sit on
    the outline follow it); `fits` = {view: {"camera", "theta"}} (cameras stay fixed while tracing)."""
    top = [list(p) for p in spec["outline"]["top"]]
    bot = [list(p) for p in spec["outline"]["bottom"]]
    hist = []
    per_view_last = {}
    for it in range(iters):
        sp = builder(top, bot)
        model = Model.from_spec(job, sp, f"trace it{it}")
        per = {k: outline_residuals(job, model, k, fits[k]) for k in views}
        per_view_last = per
        new_top, new_bot = [], []
        diag = {"iter": it, "views": {}}
        moves_v = {}
        for k in views:
            tt, mt = _knot_moves(top, per[k]["top"]["samples"], +1)
            bb, mb = _knot_moves(bot, per[k]["bottom"]["samples"], -1)
            moves_v[k] = (mt, mb)
            diag["views"][k] = {"top_mean_mm": per[k]["top"]["mean_abs_mm"],
                                "bottom_mean_mm": per[k]["bottom"]["mean_abs_mm"]}
        flagged = []
        for chain, idx, sign, out in ((top, 0, +1, new_top), (bot, 1, -1, new_bot)):
            for i, (d, y) in enumerate(chain):
                ms = [(k, moves_v[k][idx][i]) for k in views if moves_v[k][idx][i][1] is not None]
                if not ms:
                    out.append([d, y])
                    continue
                vals = [m[1][1] for m in ms]
                if len(ms) > 1 and max(vals) - min(vals) > OUTLINE_FLAG_MM:
                    k_best, m_best = min(ms, key=lambda km: km[1][2])
                    flagged.append({"edge": "top" if idx == 0 else "bottom", "d": d, "by_view":
                                    {k: m[1] for k, m in ms}, "taken_from": k_best})
                    dv = m_best[1]
                else:
                    dv = float(np.mean(vals))
                out.append([d, y + sign * dv])
        top, bot = new_top, new_bot
        diag["flagged"] = flagged
        hist.append(diag)
        if log:
            log(f"    trace_outline it {it}: " + ", ".join(f"{k} top {v['top_mean_mm']:.3f} bot {v['bottom_mean_mm']:.3f}"
                                                         for k, v in diag["views"].items()))
    # residual of the final outline
    sp = builder(top, bot)
    model = Model.from_spec(job, sp, "trace final")
    final = {k: outline_residuals(job, model, k, fits[k]) for k in views}
    return {"top": top, "bottom": bot, "history": hist, "flagged": hist[-1]["flagged"] if hist else [],
            "final": {k: {e: {kk: vv for kk, vv in v[e].items() if kk != "samples"} for e in ("top", "bottom")}
                      for k, v in final.items()}}


# --------------------------------------------------------------------------------------------- creases
def refine_line(L: np.ndarray, p0, p1, half: float = 7.0) -> dict | None:
    """Sub-pixel crease line from guide endpoints: perpendicular profiles of |grad L*| (every 2 px), the peak of each,
    a least-squares line through the peaks. None with fewer than 5 peaks."""
    gy, gx = np.gradient(ndimage.gaussian_filter(L, 1.0))
    G = np.hypot(gx, gy)
    p0, p1 = np.asarray(p0, float), np.asarray(p1, float)
    t = p1 - p0
    n_len = float(np.hypot(*t))
    t /= n_len
    nrm = np.array([-t[1], t[0]])
    offs = np.arange(-half, half + 0.01, 0.25)
    peaks, steps = [], []
    s0, s1 = max(3.0, END_TRIM * n_len), min(n_len - 3.0, (1 - END_TRIM) * n_len)
    step = float(np.clip((s1 - s0) / 12.0, 0.5, 2.0))           # short creases: denser profiles
    for s in np.arange(s0, s1, step):
        c = p0 + t * s
        P = c[None] + offs[:, None] * nrm[None]
        prof = ndimage.map_coordinates(G, [P[:, 1], P[:, 0]], order=1)
        k = int(np.argmax(prof))
        if 0 < k < len(offs) - 1 and prof[k] > max(PEAK_MIN, PEAK_RATIO * float(np.median(prof))):
            q = c + offs[k] * nrm
            peaks.append(q)
            a = ndimage.map_coordinates(L, [[q[1] + 3 * nrm[1]], [q[0] + 3 * nrm[0]]], order=1)[0]
            b = ndimage.map_coordinates(L, [[q[1] - 3 * nrm[1]], [q[0] - 3 * nrm[0]]], order=1)[0]
            steps.append(a - b)
    if len(peaks) < 5:
        return None
    Pk = np.array(peaks)
    keep = np.ones(len(Pk), bool)
    for _ in range(3):                          # robust: peaks that hop to a neighbouring crease are dropped
        ctr = Pk[keep].mean(0)
        _, _, vt = np.linalg.svd(Pk[keep] - ctr)
        r_all = np.abs((Pk - ctr) @ vt[1])
        rms = float(np.sqrt(np.mean(r_all[keep] ** 2)))
        new = r_all <= max(1.0, 2.5 * rms)
        if new.sum() < 5 or np.array_equal(new, keep):
            break
        keep = new
    Pk = Pk[keep]
    steps = list(np.asarray(steps)[keep])
    ctr = Pk.mean(0)
    _, _, vt = np.linalg.svd(Pk - ctr)
    d = vt[0]
    res = (Pk - ctr) @ vt[1]
    proj = (Pk - ctr) @ d
    a, b = ctr + d * proj.min(), ctr + d * proj.max()
    return {"p0": a.tolist(), "p1": b.tolist(), "rms_px": float(np.sqrt((res ** 2).mean())), "n": int(len(Pk)),
            "step_L": float(np.mean(steps)), "len_px": float(np.hypot(*(b - a)))}


def line_isect(a0, a1, b0, b1):
    a0, a1, b0, b1 = (np.asarray(v, float) for v in (a0, a1, b0, b1))
    r, s = a1 - a0, b1 - b0
    den = r[0] * s[1] - r[1] * s[0]
    if abs(den) < 1e-12:
        return None
    t = ((b0 - a0)[0] * s[1] - (b0 - a0)[1] * s[0]) / den
    return (a0 + t * r).tolist()


def lift_px(job, model, fit: dict, uv, side: int) -> dict | None:
    """(d, y) of a pixel lifted onto the model's temple `side` (ray cast), else the ray's meeting with that temple's
    outer-face plane is not used: None when the ray misses the temple."""
    V, F, tag = model.composite(job, fit["theta"])
    sc = raster.get_scene(V, F, job.frame)
    h = sc.cast(fit["camera"], np.array([uv[0]]), np.array([uv[1]]), want_points=True)
    if not h["hit"][0] or tag[h["face_id"][0]] != side:
        return None
    ch = chart_of(h["points"], side, job.hinge["z_mm"], fit["theta"].get(side, 0.0), job.hinge["axis_xz_mm"])[0]
    return {"d": float(ch[0]), "y": float(ch[1]), "X": float(ch[2])}


def _lift_line_extended(job, model, fit, p0, p1, side, n=40):
    """(d, y) samples of a crease line lifted onto the model surface (for corners outside the outline, the line is
    extended in (d, y) by a fit through the lifted samples)."""
    V, F, tag = model.composite(job, fit["theta"])
    sc = raster.get_scene(V, F, job.frame)
    t = np.linspace(0.0, 1.0, n)[:, None]
    uv = np.asarray(p0, float)[None] * (1 - t) + np.asarray(p1, float)[None] * t
    h = sc.cast(fit["camera"], uv[:, 0], uv[:, 1], want_points=True)
    ok = h["hit"] & (tag[np.maximum(h["face_id"], 0)] == side)
    if ok.sum() < 4:
        return None
    ch = chart_of(h["points"][ok], side, job.hinge["z_mm"], fit["theta"].get(side, 0.0), job.hinge["axis_xz_mm"])
    return ch[:, :2]


def trace_creases(job, guides: dict, model, fits: dict, corners=PRADA_CORNERS, lift_views=("05", "01")) -> dict:
    """Snap every guide to its gradient ridge, intersect for corners, lift the corners onto the model surface in the
    side views (lines extended in (d, y) where a corner lies off the temple, e.g. Prada's C)."""
    out = {"views": {}, "corners": {}}
    for key, gl in guides.items():
        L = lightness(job.photo(key))
        lines = {nm: refine_line(L, *pts) for nm, pts in gl.items()}
        cpx = {}
        for c, (l1, l2) in corners.items():
            if lines.get(l1) and lines.get(l2):
                cpx[c] = line_isect(lines[l1]["p0"], lines[l1]["p1"], lines[l2]["p0"], lines[l2]["p1"])
        out["views"][key] = {"lines": lines, "corners_px": cpx}
    for key in lift_views:
        if key not in out["views"] or key not in fits:
            continue
        side = 1 if math.sin(math.radians(fits[key]["camera"].yaw)) > 0 else -1
        lifted_lines = {}
        for nm, ln in out["views"][key]["lines"].items():
            if ln is None:
                continue
            P = _lift_line_extended(job, model, fits[key], ln["p0"], ln["p1"], side)
            if P is not None:
                c, d = P.mean(0), np.linalg.svd(P - P.mean(0))[2][0]
                lifted_lines[nm] = {"centre": c.tolist(), "dir": d.tolist(),
                                    "rms_mm": float(np.sqrt(np.mean(((P - c) @ np.array([-d[1], d[0]])) ** 2)))}
        out["views"][key]["lifted_lines"] = lifted_lines
        dy = {}
        for c, (l1, l2) in corners.items():
            if l1 in lifted_lines and l2 in lifted_lines:
                a, b = lifted_lines[l1], lifted_lines[l2]
                p = line_isect(a["centre"], np.add(a["centre"], a["dir"]), b["centre"], np.add(b["centre"], b["dir"]))
                if p is not None:
                    dy[c] = p
        out["views"][key]["corners_dy"] = dy
    for c in corners:
        vals = [out["views"][k]["corners_dy"][c] for k in lift_views
                if k in out["views"] and c in out["views"][k].get("corners_dy", {})]
        if vals:
            A = np.array(vals)
            out["corners"][c] = {"d": float(A[:, 0].mean()), "y": float(A[:, 1].mean()),
                                 "by_view": {k: out["views"][k]["corners_dy"][c] for k in lift_views
                                             if c in out["views"].get(k, {}).get("corners_dy", {})},
                                 "spread_mm": float(np.max(np.linalg.norm(A - A.mean(0), axis=1)) * 2) if len(A) > 1
                                 else None,
                                 "sigma_mm": float(np.linalg.norm(A[0] - A[-1]) / 2) if len(A) > 1 else None}
    return out


# --------------------------------------------------------------------------------------------- plan
def trace_plan(job, model, fits: dict, views=("03", "06")) -> dict:
    """Suggested inner-line and thickness corrections from each temple's arm band (d 60-100): photo - model inner
    edge (outward +) and width, per view and side, and their mean."""
    from .relief_views import envelopes
    per = {}
    inner, width = [], []
    for k in views:
        if k not in fits:
            continue
        f = fits[k]
        r = render_model(job, model, k, f["camera"], f["theta"], points=True)
        env = envelopes(job, k, r, f["camera"])
        per[k] = {}
        for s in (1, -1):
            arm = env[s].get("arm")
            if not arm:
                continue
            per[k][str(s)] = {"inner_offset_mm": arm["inner_signed_median_mm"],
                              "outer_offset_mm": arm["outer_signed_median_mm"],
                              "width_offset_mm": arm["width_photo_median_mm"] - arm["width_model_median_mm"],
                              "n": arm["n"]}
            inner.append(arm["inner_signed_median_mm"])
            width.append(arm["width_photo_median_mm"] - arm["width_model_median_mm"])
    return {"per_view": per, "inner_offset_mm": float(np.mean(inner)) if inner else None,
            "thickness_offset_mm": float(np.mean(width)) if width else None,
            "note": "suggestions: inner x moves by -inner_offset (inward +), thickness by width_offset"}


# --------------------------------------------------------------------------------------------- all of it
def trace_temple(job, guides: dict, params: dict, fits: dict, builder, log=None) -> dict:
    """Outline, creases, plan and theta_k from the photos, and a starting spec (`builder(**params)`)."""
    spec0 = builder(**params)
    model0 = Model.from_spec(job, spec0, "trace start")
    ol = trace_outline(job, spec0, lambda t, b: builder(**{**params, "top": t, "bottom": b}), fits, log=log)
    p1 = {**params, "top": ol["top"], "bottom": ol["bottom"]}
    model1 = Model.from_spec(job, builder(**p1), "trace outline")
    cr = trace_creases(job, guides, model1, fits)
    for c, key in (("A", "A"), ("B", "B"), ("C", "C")):
        if c in cr["corners"] and key in ("B", "C"):
            p1[key] = (cr["corners"][c]["d"], cr["corners"][c]["y"])
    pl = trace_plan(job, model1, fits)
    theta = {k: {str(s): float(f["theta"].get(s, 0.0)) for s in (1, -1)} for k, f in fits.items()}
    return {"outline": ol, "creases": cr, "plan": pl, "theta": theta, "params": p1, "spec": builder(**p1),
            "start_model_outline_error": None if model0 is None else True}
