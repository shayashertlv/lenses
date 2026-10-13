"""Runtime print measurements, migrated from the frozen WP5 test instrument.

The independent proof remains in tests/instruments/print_metrics.py and imports none of this package.
Do not use these runtime verdicts alone as proof of correctness. Numeric criteria are unchanged.
What remains is what the AR level calibration reads (``ar_levels`` / ``ar_calibrate``: L* and chroma levels, hue,
the AR frame mask, ``band_energy``). check_print's held-out grading (``grade``, ``evaluate_case``, ``criteria``,
``b3`` and their helpers, ``tolerant_bp``) and the copied A2-A5 / B3 / B5 constants no runtime code read were deleted
on 2026-10-13 with the step-0 experiment; see git tag review-20261013-pre-cleanup.
"""
from __future__ import annotations

import math

import numpy as np

from scipy import ndimage

from glasses3d.g3d_host import texture_utils as T

def masked_gauss(x, m, s: float) -> np.ndarray:
    x = np.asarray(x, np.float64)
    if s <= 0:
        return x
    mf = np.asarray(m, np.float64)
    return ndimage.gaussian_filter(x * mf, s) / np.maximum(ndimage.gaussian_filter(mf, s), 1e-6)

def bandpass(l, sup, s1: float, s2: float) -> np.ndarray:
    return masked_gauss(l, sup, s1) - masked_gauss(l, sup, s2)

LEVEL_QUANTILES = (10, 50, 90)

LEVEL_TOL = 2.0

def lstar(rgb) -> np.ndarray:
    """CIE L* (D65 white, Y from linearised sRGB with the Rec. 709 weights) of sRGB 0..255 colours (..., 3)."""
    c = np.asarray(rgb, np.float64) / 255.0
    lin = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    Y = lin[..., 0] * 0.2126 + lin[..., 1] * 0.7152 + lin[..., 2] * 0.0722
    f = np.where(Y > (6 / 29) ** 3, np.cbrt(Y), Y / (3 * (6 / 29) ** 2) + 4 / 29)
    return 116.0 * f - 16.0

def level_stats(rgb_px) -> dict:
    """{'n', 'p10', 'p50', 'p90', 'mean'} of L* over an (N, 3) set of sRGB pixels."""
    L = lstar(np.asarray(rgb_px, np.float64).reshape(-1, 3))
    if not len(L):
        return {"n": 0}
    q = np.percentile(L, LEVEL_QUANTILES)
    return {"n": int(len(L)), **{f"p{k}": round(float(v), 2) for k, v in zip(LEVEL_QUANTILES, q)},
            "mean": round(float(L.mean()), 2)}

def part_name_matches(name, parts):
    """Exact names take priority; tolerate only Blender's numeric duplicate suffix, preserving embedded dots."""
    name = str(name)
    base, dot, suffix = name.rpartition('.')
    return name in parts or bool(dot and len(suffix) == 3 and suffix.isdigit() and base in parts)


def ar_frame_mask(part_id_rgb, palette: list, parts, *, tol: int = 24, erode: int = 1) -> np.ndarray:
    """(H, W) bool: pixels of an arview part-id render whose colour is one of ``parts``' palette colours
    (``palette`` = the capture report's ``id_palette`` rows {'rgb', 'name'}; a Blender duplicate suffix '.001' is
    ignored, q001.glb's own export carries it), within ``tol`` per channel, eroded ``erode`` px (the beauty render's
    anti-aliased silhouette pixels are mixtures with the background)."""
    a = np.asarray(part_id_rgb, np.int32)[..., :3]
    m = np.zeros(a.shape[:2], bool)
    want = set(parts)
    for row in palette:
        if part_name_matches(row.get("name", ""), want):
            m |= (np.abs(a - np.asarray(row["rgb"], np.int32)[None, None]) <= tol).all(-1)
    return ndimage.binary_erosion(m, iterations=erode) if erode > 0 else m

HIGHLIGHT_L = 35.0

HIGHLIGHT_C = 10.0

def achromatic_highlight(rgb_px) -> np.ndarray:
    """(N,) bool: sRGB pixels that are white / grey reflections (L* > 35 and C*ab < 10), not havana pigment."""
    lab = T.lab_of(np.asarray(rgb_px, np.float64).reshape(-1, 3))
    return (lab[:, 0] > HIGHLIGHT_L) & (np.hypot(lab[:, 1], lab[:, 2]) < HIGHLIGHT_C)

def _wq(x, w, qs):
    o = np.argsort(x)
    x, w = x[o], w[o]
    c = np.cumsum(w) - 0.5 * w
    c /= w.sum()
    return np.interp(np.asarray(qs, float) / 100.0, c, x)

def level_stats_w(L, w) -> dict:
    q = _wq(L, w, LEVEL_QUANTILES)
    return {"n": int(len(L)), **{f"p{k}": round(float(v), 2) for k, v in zip(LEVEL_QUANTILES, q)},
            "mean": round(float((L * w).sum() / w.sum()), 2)}

def level_compare(ar_px, photo_px, *, tol: float = LEVEL_TOL, ar_group=None, photo_group=None,
                  exclude_highlights: bool = False, ar_highlight=None) -> dict:
    """AR frame pixels against the photo's: L* deciles of each, the differences (AR - photo) and the task-item-1
    verdict (darkest decile p10 and median p50 both within ``tol``). ``ar_group`` / ``photo_group`` (per-pixel group
    labels, e.g. brows vs temples): the photo's pixels are WEIGHTED so each group has the AR's share (a photo shows
    parts at other angles than an AR pose, e.g. photo 05's temple tops at grazing angle, 1/3 of its frame pixels,
    which the front pose hides). ``exclude_highlights``: highlights left out on both sides: on the photo side its own
    achromatic pixels (``achromatic_highlight``: the studio's reflections, fixed per photo); on the AR side
    ``ar_highlight`` when given, a per-pixel mask that must NOT depend on the print (e.g. the pixels a capture of the
    same GLB with a black albedo shows as achromatic highlights: the renderer's reflections alone), else the AR
    pixels' own colours. Fix round 1 of the correction round: the AR pixels' own test is NOT the renderer's alone; it
    moves with the print (at the front pose 42.1 % of the frame pixels with the 10bf932 print, 33.7 % with the
    calibrated one, 28.6 % with Astra's texture, 29.8 % with a black albedo), because light, desaturated print pixels
    pass it too, so a calibration graded by it can hide a washed-out print; ``ar_highlight_source`` records which."""
    ar_px = np.asarray(ar_px, np.float64).reshape(-1, 3)
    photo_px = np.asarray(photo_px, np.float64).reshape(-1, 3)
    ga = np.zeros(len(ar_px), np.int64) if ar_group is None else np.asarray(ar_group)
    gp = np.zeros(len(photo_px), np.int64) if photo_group is None else np.asarray(photo_group)
    out = {"tol": tol, "composition_matched": ar_group is not None, "highlights_excluded": bool(exclude_highlights)}
    if exclude_highlights:
        if ar_highlight is not None:
            ha = np.asarray(ar_highlight, bool).reshape(-1)
            if len(ha) != len(ar_px):
                raise ValueError(f"ar_highlight has {len(ha)} entries for {len(ar_px)} AR pixels")
        else:
            ha = achromatic_highlight(ar_px)
        hp = achromatic_highlight(photo_px)
        out["ar_highlight_source"] = "mask (independent of the print)" if ar_highlight is not None else             "the AR pixels' own colours (moves with the print)"
        out["highlight_share"] = {"ar": round(float(ha.mean()), 4) if len(ha) else None,
                                  "photo": round(float(hp.mean()), 4) if len(hp) else None}
        ar_px, ga, photo_px, gp = ar_px[~ha], ga[~ha], photo_px[~hp], gp[~hp]
    if not len(ar_px) or not len(photo_px):
        return {**out, "pass": False, "reason": "no pixels"}
    La, Lp = lstar(ar_px), lstar(photo_px)
    wa = np.ones(len(La))
    wp = np.ones(len(Lp))
    if ar_group is not None:
        share = {g: float((ga == g).mean()) for g in np.unique(ga)}
        out["ar_group_share"] = {str(g): round(v, 4) for g, v in share.items()}
        out["photo_group_share_raw"] = {str(g): round(float((gp == g).mean()), 4) for g in np.unique(gp)}
        for g in np.unique(gp):
            m = gp == g
            wp[m] = share.get(g, 0.0) / max(int(m.sum()), 1)
        if wp.sum() <= 0:
            return {**out, "pass": False, "reason": "no shared group"}
    a, p = level_stats_w(La, wa), level_stats_w(Lp, wp)
    d = {k: round(a[k] - p[k], 2) for k in ("p10", "p50", "p90", "mean")}
    # step 6 (NEW): chroma C*ab and contrast (weighted L* standard deviation) on the same pixels and weights; reported
    # beside the level verdict (``appearance`` holds their own verdict, ``pass`` stays the level's)
    ca, cp = chroma_stats_w(ar_px, wa), chroma_stats_w(photo_px, wp)
    sa, sp = _wstd(La, wa), _wstd(Lp, wp)
    app = {"ar_chroma": ca, "photo_chroma": cp,
           "chroma_ar_minus_photo": {k: round(ca[k] - cp[k], 2) for k in ("p50", "p90", "mean")},
           "chroma_ar_over_photo": {k: (round(ca[k] / cp[k], 3) if cp[k] > 0 else None) for k in ("p90", "mean")},
           "ar_lstar_std": round(sa, 2), "photo_lstar_std": round(sp, 2),
           "lstar_std_ratio": round(sa / sp, 3) if sp > 0 else None,
           "chroma_tol": CHROMA_TOL, "chroma_spread_ratio_range": list(CHROMA_SPREAD_RATIO),
           "contrast_ratio_range": list(CONTRAST_RATIO)}
    app["chroma_units"] = chroma_units(app)
    ha_, hp_ = hue_stats_w(ar_px, wa), hue_stats_w(photo_px, wp)       # fix round 2 of step 6: hue
    dh = (None if ha_["p50"] is None or hp_["p50"] is None else round(ha_["p50"] - hp_["p50"], 2))
    app["hue"] = {"ar": ha_, "photo": hp_, "p50_ar_minus_photo": dh,
                  "magenta_share_ar_minus_photo": round(ha_["magenta_share"] - hp_["magenta_share"], 4),
                  "off_band_share_ar_minus_photo": round(ha_["off_band_share"] - hp_["off_band_share"], 4),
                  "c_min": HUE_C_MIN, "tol_deg": HUE_TOL_DEG, "magenta_excess": MAGENTA_EXCESS,
                  "band_deg": list(HAVANA_HUE), "magenta_deg": list(MAGENTA_HUE)}
    app["criteria"] = {"chroma_p50": bool(abs(app["chroma_ar_minus_photo"]["p50"]) <= CHROMA_TOL),
                       **{f"chroma_{k}": _in(app["chroma_ar_over_photo"][k], CHROMA_SPREAD_RATIO) for k in ("p90", "mean")},
                       "contrast": _in(app["lstar_std_ratio"], CONTRAST_RATIO),
                       "hue_p50": bool(dh is not None and abs(dh) <= HUE_TOL_DEG),
                       "hue_magenta": bool(app["hue"]["magenta_share_ar_minus_photo"] <= MAGENTA_EXCESS)}
    app["pass"] = bool(all(app["criteria"].values()))
    return {**out, "ar": a, "photo": p, "ar_minus_photo": d, "appearance": app,
            "pass": bool(abs(d["p10"]) <= tol and abs(d["p50"]) <= tol)}

CHROMA_TOL = 1.0

CONTRAST_RATIO = (0.85, 1.18)

CHROMA_SPREAD_RATIO = (0.85, 1.18)

HAVANA_HUE = (20.0, 100.0)

MAGENTA_HUE = (300.0, 360.0)

HUE_C_MIN = 4.0

HUE_TOL_DEG = 5.0

MAGENTA_EXCESS = 0.02

def _in(x, rng) -> bool:
    return bool(x is not None and rng[0] <= x <= rng[1])

def hue_stats_w(rgb_px, w) -> dict:
    """Fix round 2 of step 6: hue of sRGB pixels, weighted: over the pixels with C*ab > ``HUE_C_MIN``, the median hue
    h_ab in degrees (circular: hues are taken within 180 deg of the havana band's centre, 60 deg, then the weighted
    median), and, as shares of ALL the pixels' weight, the chromatic share, the magenta share (``MAGENTA_HUE``) and the
    share off ``HAVANA_HUE``."""
    lab = T.lab_of(np.asarray(rgb_px, np.float64).reshape(-1, 3))
    w = np.asarray(w, np.float64)
    if not len(lab) or w.sum() <= 0:
        return {"p50": None, "chromatic_share": 0.0, "magenta_share": 0.0, "off_band_share": 0.0}
    C = np.hypot(lab[:, 1], lab[:, 2])
    h = np.degrees(np.arctan2(lab[:, 2], lab[:, 1])) % 360.0
    m = C > HUE_C_MIN
    centre = 0.5 * (HAVANA_HUE[0] + HAVANA_HUE[1])
    rel = (h - centre + 180.0) % 360.0 - 180.0 + centre
    tot = w.sum()
    mag = m & (h >= MAGENTA_HUE[0]) & (h < MAGENTA_HUE[1])
    off = m & ~((h >= HAVANA_HUE[0]) & (h <= HAVANA_HUE[1]))
    return {"p50": round(float(_wq(rel[m], w[m], (50,))[0]), 2) if m.any() and w[m].sum() > 0 else None,
            "chromatic_share": round(float(w[m].sum() / tot), 4), "magenta_share": round(float(w[mag].sum() / tot), 4),
            "off_band_share": round(float(w[off].sum() / tot), 4)}

def chroma_units(app: dict) -> list[float]:
    """[p50, p90, mean] of an ``appearance`` block in units of its own verdict's window (|x| <= 1 passes): the C*ab p50
    difference / ``CHROMA_TOL``; ln(AR / photo) of p90 and of the mean over ln of the ``CHROMA_SPREAD_RATIO`` edge on
    its side (a missing ratio counts 99)."""
    out = [round(app["chroma_ar_minus_photo"]["p50"] / CHROMA_TOL, 3)]
    for k in ("p90", "mean"):
        r = app["chroma_ar_over_photo"][k]
        if not r or r <= 0:
            out.append(99.0)
            continue
        edge = CHROMA_SPREAD_RATIO[1] if r >= 1.0 else 1.0 / CHROMA_SPREAD_RATIO[0]
        out.append(round(math.log(r) / math.log(edge), 3))
    return out

def _wstd(x, w) -> float:
    x = np.asarray(x, np.float64)
    w = np.asarray(w, np.float64)
    if not len(x) or w.sum() <= 0:
        return 0.0
    m = (x * w).sum() / w.sum()
    return float(np.sqrt(((x - m) ** 2 * w).sum() / w.sum()))

def chroma_stats_w(rgb_px, w) -> dict:
    """{'p50', 'p90', 'mean'} of CIELAB chroma C*ab (D65; the shared colour helper) of sRGB pixels, weighted."""
    lab = T.lab_of(np.asarray(rgb_px, np.float64).reshape(-1, 3))
    C = np.hypot(lab[:, 1], lab[:, 2])
    w = np.asarray(w, np.float64)
    if not len(C):
        return {"p50": float("nan"), "p90": float("nan"), "mean": float("nan")}
    q = _wq(C, w, (50, 90))
    return {"p50": round(float(q[0]), 2), "p90": round(float(q[1]), 2), "mean": round(float((C * w).sum() / w.sum()), 2)}

def band_energy(img, mask, ppm: float, band_mm=(0.5, 3.0), L=None) -> float | None:
    """Step 6 (NEW): the std of the masked DoG of L* (sigma ``band_mm`` x ``ppm`` px) over ``mask`` (the print's
    patch contrast at 0.5-3 mm, the scale of havana spots); None below 200 pixels. ``L``: the image's L* when the
    caller already has it (fix round 3 of step 6, speed only). The DoG is computed on the mask's bounding box padded by
    the wider Gaussian's reach (``gaussian_filter`` truncates at 4 sigma): outside the mask both masked inputs are
    zero, so on the mask the result is the whole image's (checked by a test)."""
    m = np.asarray(mask, bool)
    if m.sum() < 200:
        return None
    L = lstar(np.asarray(img, np.float64)) if L is None else np.asarray(L, np.float64)
    r = int(4.0 * max(band_mm) * ppm + 0.5) + 2
    ys, xs = np.nonzero(m)
    y0, y1 = max(int(ys.min()) - r, 0), min(int(ys.max()) + r + 1, m.shape[0])
    x0, x1 = max(int(xs.min()) - r, 0), min(int(xs.max()) + r + 1, m.shape[1])
    mc = m[y0:y1, x0:x1]
    b = bandpass(L[y0:y1, x0:x1], mc, band_mm[0] * ppm, band_mm[1] * ppm)
    return float(b[mc].std())
