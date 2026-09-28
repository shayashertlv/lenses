"""S8 lens appearance: photometry of the lens in the front and back photos -> class + glTF material.

Model (linear light, per channel, in units of the backdrop behind the lens):
    back  ~= T(v)                      the coat faces away; the back photo is ~pure transmission
    front ~= kappa * T(v) + rho(v)     rho = what the front surface reflects

Transmission is reciprocal, so the transmitted part of the front equals the back up to the
photos' exposure difference kappa. An UNCOATED surface reflects neutrally (Fresnel at n = 1.5 is
achromatic) and at most 2 x 4 % = 0.08 of an environment no brighter than the backdrop. So:

* a COAT shows as a front that is not explained by the back under ANY exposure plus a neutral
  reflection 0 <= rho(v) <= 0.08 (a chromatic residual), or as a front brighter than the back by
  more than the exposure band allows (kappa above the band). This one rule separates the Oakley
  (chroma: green/purple front vs pink back) and INVU (the front is ~2x the back) mirrors from the
  uncoated tints without a per-product rule.
* the transmission profile T(v) (back view) decides gradient (bottom/top > 1.4), clear
  (T > 0.75 and saturation < 0.15) or tint.

Pixels: S2 lens polygons (front) and the S2 polygon registered into the back photo intersected
with the S0 back-view lens proposal; eroded 2 mm; minus near-white highlights, minus outliers of a
robust smooth lens model (logos, reflection streaks), minus see-through structure. See-through
structure comes from the S3 cameras when ``s3_cameras/result.json`` exists (generator faces
behind the lens plate rendered through the frozen camera) and always from colour: a pixel far
from the lens model and closer to one of the frame's own colours, or a dark outlier; every
excluded pixel grows by 0.5 mm so structure edges go too.

Outputs (DESIGN.md S8): class, tint_linear_rgb, gradient (1x64 on lens uv v, row 0 = top), mirror, edge_ring (clear
lenses: the frosted outline band measured from the front photo, built by S9 with its own descriptor), and the
``lens_appearance`` LensAppearance v1 descriptor that S9 writes as the runtime's canonical ``LENSES_lens_appearance``
(unblurred background; density over the lens-local height v from the transmission profile; reflectance R(0) =
0.04 with Schlick for an uncoated lens, or for a mirror coat the front photo's reflected light per incidence angle on
the constructed lens (``coat_angular_table``) scaled for the runtime's room environment in the actual AR runtime
(``calibrate_canonical``; interim M1, the studio_v1 lighting contract is M3)). ``gltf_material`` is only the flat
transmissive fallback for other viewers. Plus evidence and flags.
S8 reads S5/S6 (the calibration renders the assembled lens) besides S0-S3.
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage, optimize

from . import core, intake
from .front import rasterize

STAGE = "s8_lens"

ERODE_MM = 2.0                    # lens polygon eroded by this before any photometry
GROW_MM = 0.5                     # every excluded pixel grows by this (structure edges)
HIGHLIGHT_V = 0.97                # near-white: HSV value above this ...
HIGHLIGHT_S = 0.15                # ... and saturation below this
HIGHLIGHT_MIN_EXCESS = 0.05       # ... and brighter than the lens model by this much (else: a clear lens)
OUTLIER_K = 4.0                   # luminance outliers of the robust lens model: K sigma ...
OUTLIER_FLOOR = 0.03              # ... but never closer than this (ratio units)
FRAME_DE_MIN = 6.0                # see-through colour rule: at least this far (dE76) from the lens model
FRAME_K = 4                       # frame colour clusters (k-means, deterministic init)
FRAME_BACKDROP_DE = 10.0          # frame clusters this close to the backdrop are matte bleed, dropped
N_BINS = 64                       # vertical profile / gradient texture bins on lens-local v
COAT_BINS = 32                    # coarser bins for the front/back coat test
COAT_SHIFT_BINS = 1               # registration slack of the coat test (+/- bins)
MIN_BIN_PX = 12                   # bins with fewer kept pixels are empty
MIN_VIEW_PX = 400                 # a view with fewer kept pixels is unusable
UNCOATED_R = 0.04                 # Fresnel reflectance of one uncoated n = 1.5 surface
UNCOATED_MAX = 2 * UNCOATED_R     # both faces
EXPOSURE_BAND = 0.10              # |ln exposure ratio| between two photos with unclipped backdrops
EXPOSURE_BAND_CLIPPED = math.log(2.0) / 2.0   # half a stop when a backdrop is saturated
BACKDROP_CLIPPED_LIN = 0.955      # linear backdrop >= this (sRGB >= 250) counts as saturated
COAT_RESID_FLOOR = 0.02           # chromatic residual floor (ratio units) ...
COAT_RESID_REL = 0.05             # ... or this fraction of the front level, whichever is larger
CLEAR_T = 0.75
CLEAR_SAT = 0.15
GRADIENT_RATIO = 1.4
GRADIENT_MIN_DELTA = 0.05
IOR = 1.5
LENS_ROUGHNESS = 0.05
OCCLUDER_BEHIND_MM = 2.0          # generator faces this far behind the lens back surface are structure
OCCLUDER_GROW_MM = 1.0
PLATE_MIN_HIT = 0.6

# CIE / sRGB D65 (the same primaries and white as OpenCV's Lab, which S0 used)
_M = np.array([[0.412453, 0.357580, 0.180423],
               [0.212671, 0.715160, 0.072169],
               [0.019334, 0.119193, 0.950227]])
_M_INV = np.linalg.inv(_M)
_WHITE = _M.sum(axis=1)
LUMA = _M[1]


# --------------------------------------------------------------------------- colour
def srgb_to_linear(x) -> np.ndarray:
    x = np.asarray(x, float)
    return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(x) -> np.ndarray:
    x = np.clip(np.asarray(x, float), 0.0, 1.0)
    return np.where(x <= 0.0031308, 12.92 * x, 1.055 * x ** (1 / 2.4) - 0.055)


def linear_to_lab(lin) -> np.ndarray:
    xyz = (np.asarray(lin, float) @ _M.T) / _WHITE
    f = np.where(xyz > 0.008856, np.cbrt(np.maximum(xyz, 0)), 7.787 * xyz + 16.0 / 116.0)
    L = np.where(xyz[..., 1] > 0.008856, 116.0 * f[..., 1] - 16.0, 903.3 * xyz[..., 1])
    return np.stack([L, 500.0 * (f[..., 0] - f[..., 1]), 200.0 * (f[..., 1] - f[..., 2])], -1)


def lab_to_linear(lab) -> np.ndarray:
    lab = np.asarray(lab, float)
    L = lab[..., 0]
    fy = (L + 16.0) / 116.0
    fx = fy + lab[..., 1] / 500.0
    fz = fy - lab[..., 2] / 200.0

    def finv(f):
        return np.where(f > 0.206893, f ** 3, (f - 16.0 / 116.0) / 7.787)
    Y = np.where(L > 7.9996, fy ** 3, L / 903.3)
    xyz = np.stack([finv(fx), Y, finv(fz)], -1) * _WHITE
    return xyz @ _M_INV.T


def delta_e00(lab1, lab2) -> np.ndarray:
    from skimage.color import deltaE_ciede2000
    return deltaE_ciede2000(np.asarray(lab1, float), np.asarray(lab2, float))


def backdrop_linear(coef, shape) -> np.ndarray:
    """S0 quadratic Lab backdrop model -> linear RGB (H, W, 3), clipped to the recordable range."""
    lab = intake.backdrop_lab(np.asarray(coef, float), shape)
    return np.clip(lab_to_linear(lab), 1e-4, 1.0).astype(np.float32)


# --------------------------------------------------------------------------- geometry helpers
def affine_apply(M, pts) -> np.ndarray:
    M = np.asarray(M, float)
    return np.asarray(pts, float) @ M[:, :2].T + M[:, 2]


def affine_invert(M) -> np.ndarray:
    M = np.asarray(M, float)
    Ai = np.linalg.inv(M[:, :2])
    return np.hstack([Ai, -(Ai @ M[:, 2])[:, None]])


def erode_px(mask: np.ndarray, r_px: float) -> np.ndarray:
    """Pixels farther than r_px (Euclidean) from the outside of ``mask``."""
    m = np.asarray(mask, bool)
    if r_px <= 0 or not m.any():
        return m.copy()
    pad = np.pad(m, 1)
    return (ndimage.distance_transform_edt(pad) > r_px)[1:-1, 1:-1]


def grow_px(mask: np.ndarray, r_px: float) -> np.ndarray:
    m = np.asarray(mask, bool)
    if r_px <= 0 or not m.any():
        return m.copy()
    return ndimage.distance_transform_edt(~m) <= r_px


def pav_increasing(y: np.ndarray, w: np.ndarray | None = None) -> np.ndarray:
    """Weighted isotonic (non-decreasing) regression, pool-adjacent-violators."""
    y = np.asarray(y, float)
    w = np.ones_like(y) if w is None else np.asarray(w, float)
    vals, wts, lens = [], [], []
    for yi, wi in zip(y, w):
        vals.append(yi)
        wts.append(wi)
        lens.append(1)
        while len(vals) > 1 and vals[-2] > vals[-1]:
            v2, w2, l2 = vals.pop(), wts.pop(), lens.pop()
            v1, w1, l1 = vals.pop(), wts.pop(), lens.pop()
            ws = w1 + w2
            vals.append((v1 * w1 + v2 * w2) / ws if ws > 0 else (v1 + v2) / 2)
            wts.append(ws)
            lens.append(l1 + l2)
    return np.repeat(vals, lens)


def kmeans_lab(lab: np.ndarray, k: int = FRAME_K, iters: int = 25) -> tuple[np.ndarray, np.ndarray]:
    """Deterministic Lloyd k-means: init at L-quantile samples. Returns (centres, shares)."""
    lab = np.asarray(lab, float)
    if len(lab) < k:
        return lab.copy(), np.full(len(lab), 1.0 / max(1, len(lab)))
    order = np.argsort(lab[:, 0], kind="stable")
    C = lab[order[((np.arange(k) + 0.5) / k * len(lab)).astype(int)]].copy()
    for _ in range(iters):
        d = ((lab[:, None, :] - C[None]) ** 2).sum(-1)
        a = np.argmin(d, axis=1)
        newC = np.array([lab[a == j].mean(0) if np.any(a == j) else C[j] for j in range(k)])
        if np.allclose(newC, C, atol=1e-6):
            C = newC
            break
        C = newC
    a = np.argmin(((lab[:, None, :] - C[None]) ** 2).sum(-1), axis=1)
    shares = np.bincount(a, minlength=k) / len(lab)
    return C, shares


def frame_colours(rgb: np.ndarray, frame_mask: np.ndarray, backdrop_lab_px: np.ndarray,
                  max_samples: int = 20000) -> list[list[float]]:
    """The frame's own colour clusters (Lab): k-means on frame pixels, minus clusters that are
    backdrop bleed or tiny (< 3 %)."""
    ys, xs = np.nonzero(frame_mask)
    if len(ys) < 50:
        return []
    step = max(1, len(ys) // max_samples)
    lin = srgb_to_linear(rgb[ys[::step], xs[::step]] / 255.0)
    lab = linear_to_lab(lin)
    C, shares = kmeans_lab(lab)
    keep = [j for j in range(len(C)) if shares[j] >= 0.03
            and np.linalg.norm(C[j] - np.asarray(backdrop_lab_px, float)) > FRAME_BACKDROP_DE]
    return [[round(float(x), 3) for x in C[j]] for j in keep]


# --------------------------------------------------------------------------- per-view sampling
@dataclass
class ViewSamples:
    """Pixels of one view inside the eroded lens region, with lens-local coordinates."""
    view: str
    shape: tuple[int, int]
    ys: np.ndarray
    xs: np.ndarray
    lens_id: np.ndarray          # 1-based
    u: np.ndarray                # lens-local across (-1..1; + = temple side for R/L lenses)
    v: np.ndarray                # lens-local vertical, 0 = top, 1 = bottom (front-photo lens bbox)
    lin: np.ndarray              # (K, 3) linear RGB of the photo
    bg: np.ndarray               # (K, 3) linear backdrop model behind the pixel
    lab: np.ndarray              # (K, 3)
    hsv_v: np.ndarray
    hsv_s: np.ndarray
    mm_px: float
    region: np.ndarray           # bool HxW (eroded region, all lenses)
    info: dict = field(default_factory=dict)

    @property
    def ratio(self) -> np.ndarray:
        return self.lin / np.maximum(self.bg, 1e-4)


def sample_view(view: str, rgb: np.ndarray, bg_lin: np.ndarray, lens_masks: list[np.ndarray],
                lens_boxes_front: list[tuple[float, float, float, float]], sides: list[str],
                to_front, mm_px: float, erode_mm: float = ERODE_MM) -> ViewSamples:
    """Erode each lens mask by ``erode_mm`` and collect its pixels. ``to_front(xs, ys)`` maps this
    view's pixels to front-photo px, where ``lens_boxes_front`` (x0, y0, x1, y1) define u, v."""
    H, W = rgb.shape[:2]
    r_px = erode_mm / mm_px
    region = np.zeros((H, W), bool)
    lid = np.zeros((H, W), np.int16)
    for i, m in enumerate(lens_masks, start=1):
        e = erode_px(m, r_px) & ~region
        region |= e
        lid[e] = i
    ys, xs = np.nonzero(region)
    ids = lid[ys, xs].astype(int)
    fx, fy = to_front(xs.astype(float), ys.astype(float))
    u = np.zeros(len(ys))
    v = np.zeros(len(ys))
    for i, (box, side) in enumerate(zip(lens_boxes_front, sides), start=1):
        s = ids == i
        x0, y0, x1, y1 = box
        v[s] = (fy[s] - y0) / max(1e-6, y1 - y0)
        ux = (fx[s] - x0) / max(1e-6, x1 - x0) * 2.0 - 1.0
        u[s] = -ux if side == "L" else ux
    px = rgb[ys, xs].astype(float) / 255.0
    lin = srgb_to_linear(px)
    mx, mn = px.max(1), px.min(1)
    return ViewSamples(view, (H, W), ys, xs, ids, np.clip(u, -1.5, 1.5), np.clip(v, -0.1, 1.1), lin,
                       bg_lin[ys, xs].astype(float), linear_to_lab(lin), mx,
                       np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0.0), mm_px, region,
                       {"erode_px": round(r_px, 2), "region_px": int(len(ys))})


MODEL_V_KNOTS = 16               # robust lens model: piecewise-linear in v (a gradient is sigmoid-like)


def _basis(u: np.ndarray, v: np.ndarray, n_knots: int = MODEL_V_KNOTS) -> np.ndarray:
    """Hat functions on v (partition of unity on [0, 1]) plus u, u^2, u*v across the lens.
    A cubic in v cannot follow a gradient that rises steeply near the bottom: the overshoot there
    marks good lens pixels as dark outliers (seen on the VB sheet)."""
    vc = np.clip(v, 0.0, 1.0)
    knots = np.linspace(0.0, 1.0, n_knots)
    H = np.maximum(0.0, 1.0 - np.abs(vc[:, None] - knots[None]) / (knots[1] - knots[0]))
    return np.hstack([H, np.stack([u, u * u, u * vc], 1)])


def _mad_sigma(x: np.ndarray) -> float:
    if len(x) == 0:
        return 0.0
    return float(1.4826 * np.median(np.abs(x - np.median(x))))


def filter_samples(s: ViewSamples, frame_lab: list, occluder: np.ndarray | None = None,
                   iterations: int = 4, grow_mm: float = GROW_MM) -> dict:
    """Robust smooth lens model + exclusion rules. Returns keep (K,) and per-rule counts."""
    K = len(s.ys)
    B = _basis(s.u, s.v)
    ratio = s.ratio
    Y = ratio @ LUMA
    near_white = (s.hsv_v > HIGHLIGHT_V) & (s.hsv_s < HIGHLIGHT_S)
    occ = np.zeros(K, bool) if occluder is None else np.asarray(occluder, bool)[s.ys, s.xs]
    C = np.asarray(frame_lab, float).reshape(-1, 3)
    dE_f = (np.sqrt(((s.lab[:, None, :] - C[None]) ** 2).sum(-1)).min(1) if len(C) else np.full(K, np.inf))
    keep = ~occ
    y0, x0 = s.ys.min(initial=0), s.xs.min(initial=0)
    h = int(s.ys.max(initial=0) - y0 + 1)
    w = int(s.xs.max(initial=0) - x0 + 1)
    g = max(1.0, grow_mm / s.mm_px)
    rules = {}
    coef = np.zeros((B.shape[1], 3))
    zero = np.zeros(K, bool)
    masks = {"highlight": zero, "bright": zero, "structure": occ}
    for it in range(iterations):
        idx = np.flatnonzero(keep)
        if len(idx) < 50:
            break
        sub = idx[::max(1, len(idx) // 60000)]
        coef, *_ = np.linalg.lstsq(B[sub], ratio[sub], rcond=None)
        pred = np.clip(B @ coef, 0.0, 1.5)
        rY = Y - pred @ LUMA
        sY = _mad_sigma(rY[keep])
        thr = max(OUTLIER_K * sY, OUTLIER_FLOOR)
        highlight = near_white & (rY > max(3.0 * sY, HIGHLIGHT_MIN_EXCESS))
        bright = (rY > thr) & ~highlight
        dark = rY < -thr
        lab_pred = linear_to_lab(np.clip(pred * s.bg, 0.0, 1.0))
        dE_m = np.sqrt(((s.lab - lab_pred) ** 2).sum(-1))
        tau = max(FRAME_DE_MIN, float(np.median(dE_m[keep])) + 3.0 * _mad_sigma(dE_m[keep]))
        framelike = (dE_m > tau) & (dE_f < dE_m)
        excl = highlight | bright | dark | framelike | occ
        img = np.zeros((h, w), bool)
        img[s.ys[excl] - y0, s.xs[excl] - x0] = True
        grown = grow_px(img, g)[s.ys - y0, s.xs - x0]
        new_keep = ~grown
        if new_keep.sum() < 0.15 * K:       # the rules would eat the lens: keep the last state
            rules["stopped"] = f"iteration {it}: rules would keep {int(new_keep.sum())}/{K}"
            break
        rules = {"highlight": int(highlight.sum()), "bright_outlier": int(bright.sum()),
                 "dark_outlier": int(dark.sum()), "frame_colour": int(framelike.sum()),
                 "s3_occluder": int(occ.sum()), "excluded_after_grow": int(grown.sum()), "iterations": it + 1,
                 "sigma_Y": round(sY, 4), "outlier_threshold": round(thr, 4), "tau_dE": round(tau, 2)}
        masks = {"highlight": highlight, "bright": bright, "structure": dark | framelike | occ}
        converged = np.array_equal(new_keep, keep)
        keep = new_keep
        if converged:
            break
    return {"keep": keep, "rules": rules, "model_coef": coef, "masks": masks}


# --------------------------------------------------------------------------- photometry
def bin_profile(v: np.ndarray, values: np.ndarray, n_bins: int, min_px: int = MIN_BIN_PX) -> tuple[np.ndarray, np.ndarray]:
    """Per-bin median of ``values`` (K, C) on v in [0, 1]; NaN where fewer than min_px."""
    k = np.clip((np.asarray(v) * n_bins).astype(int), 0, n_bins - 1)
    order = np.argsort(k, kind="stable")
    ks, vs = k[order], np.asarray(values, float)[order]
    counts = np.bincount(ks, minlength=n_bins)
    starts = np.r_[0, np.cumsum(counts)[:-1]]
    out = np.full((n_bins, vs.shape[1]), np.nan)
    for b in range(n_bins):
        if counts[b] >= min_px:
            out[b] = np.median(vs[starts[b]:starts[b] + counts[b]], axis=0)
    return out, counts


def view_stats(s: ViewSamples, keep: np.ndarray) -> dict:
    r = s.ratio[keep]
    v = s.v[keep]
    if len(r) == 0:
        return {"kept_px": 0}
    med = np.median(r, axis=0)
    Yp = r @ LUMA
    lo, hi = np.quantile(v, [0.0, 1.0])
    span = max(1e-6, hi - lo)
    top = Yp[v <= lo + 0.2 * span]
    bot = Yp[v >= hi - 0.2 * span]
    bg_med = np.median(s.bg[keep], axis=0)
    per_lens = {}
    for i in np.unique(s.lens_id[keep]):
        rr = r[s.lens_id[keep] == i]
        per_lens[str(int(i))] = {"px": int(len(rr)), "ratio_rgb": np.round(np.median(rr, axis=0), 4).tolist()}
    return {"kept_px": int(keep.sum()), "ratio_rgb": np.round(med, 4).tolist(),
            "ratio_Y": round(float(med @ LUMA), 4), "saturation": round(float((med.max() - med.min()) / max(med.max(), 1e-6)), 4),
            "Y_top20": round(float(np.median(top)), 4) if len(top) else None,
            "Y_bottom20": round(float(np.median(bot)), 4) if len(bot) else None,
            "v_coverage": [round(float(lo), 3), round(float(hi), 3)],
            "backdrop_linear_rgb": np.round(bg_med, 4).tolist(),
            "backdrop_clipped": bool(bg_med.max() >= BACKDROP_CLIPPED_LIN), "per_lens": per_lens}


def coat_test(F: np.ndarray, Bk: np.ndarray, front_level: float, clipped: bool,
              shift_bins: int = COAT_SHIFT_BINS) -> dict:
    """Is the front explained by the back (transmission) under an exposure factor kappa plus a
    neutral uncoated reflection 0 <= rho_k <= UNCOATED_MAX per bin? F, Bk: (n, 3) bin profiles.

    coat_chroma: the best fit with kappa FREE leaves a residual above the floor (no exposure and
    no neutral reflection explains it: a coloured coat). coat_lum: the fitted kappa exceeds the
    exposure band (the front is brighter than any exposure difference plus uncoated reflection)."""
    n = len(F)
    best = None
    for s in range(-shift_bins, shift_bins + 1):
        idx = [k for k in range(n) if 0 <= k + s < n and np.isfinite(F[k]).all() and np.isfinite(Bk[k + s]).all()]
        if len(idx) < 4:
            continue
        f = F[idx]
        b = Bk[[k + s for k in idx]]
        m = len(idx)
        A = np.zeros((3 * m, 1 + m))
        A[:, 0] = b.ravel()
        for j in range(m):
            A[3 * j:3 * j + 3, 1 + j] = 1.0
        lo = np.r_[0.0, np.zeros(m)]
        hi = np.r_[np.inf, np.full(m, UNCOATED_MAX)]
        sol = optimize.lsq_linear(A, f.ravel(), bounds=(lo, hi), method="bvls")
        res = (f.ravel() - A @ sol.x).reshape(m, 3)
        rms = float(np.sqrt(np.mean(res ** 2)))
        if best is None or rms < best["rms"] - 1e-12:
            best = {"rms": rms, "shift": s, "kappa": float(sol.x[0]), "rho_median": float(np.median(sol.x[1:])),
                    "bins": m, "residual_rgb": np.round(np.median(res, axis=0), 4).tolist()}
    if best is None:
        return {"testable": False, "coat": False, "reason": "fewer than 4 common bins"}
    band = EXPOSURE_BAND_CLIPPED if clipped else EXPOSURE_BAND
    thr = max(COAT_RESID_FLOOR, COAT_RESID_REL * front_level)
    coat_chroma = best["rms"] > thr
    # kappa is not identifiable against a flat neutral rho, so each luminance decision takes the
    # conservative end: brighter-than-band uses the full uncoated allowance (rho = max), darker-than-
    # band uses none (rho = 0). Same bins and shift as the best fit.
    s = best["shift"]
    idx = [k for k in range(n) if 0 <= k + s < n and np.isfinite(F[k]).all() and np.isfinite(Bk[k + s]).all()]
    f, b = F[idx].ravel(), Bk[[k + s for k in idx]].ravel()
    bb = max(float(b @ b), 1e-12)
    kappa_lo = max(float((f - UNCOATED_MAX) @ b) / bb, 1e-6)
    kappa_hi = max(float(f @ b) / bb, 1e-6)
    coat_lum = math.log(kappa_lo) > band
    darker = math.log(kappa_hi) < -band
    return {"testable": True, "coat": bool(coat_chroma or coat_lum), "coat_chroma": bool(coat_chroma),
            "coat_luminance": bool(coat_lum), "front_darker_than_exposure_band": bool(darker),
            "residual_rms": round(best["rms"], 4), "residual_threshold": round(thr, 4),
            "kappa": round(best["kappa"], 4), "ln_kappa": round(math.log(max(best["kappa"], 1e-6)), 4),
            "ln_kappa_lo": round(math.log(kappa_lo), 4), "ln_kappa_hi": round(math.log(kappa_hi), 4),
            "exposure_band_ln": round(band, 4), "rho_median": round(best["rho_median"], 4),
            "registration_shift_bins": best["shift"], "bins": best["bins"], "median_residual_rgb": best["residual_rgb"],
            "model": "front = kappa * back + rho_k, 0 <= rho_k <= %.2f (neutral uncoated reflection)" % UNCOATED_MAX}


def transmission_scale(F: np.ndarray, Bk: np.ndarray) -> float:
    """kappa = min(1, min over channels of the median over common bins of F / B): the largest
    scale of the back profile that the front never falls below (bins with B < 0.01 skipped)."""
    ok = np.isfinite(F).all(axis=1) & np.isfinite(Bk).all(axis=1) & (np.min(Bk, axis=1) >= 0.01)
    if ok.sum() < 3:
        return 1.0
    q = np.median(F[ok] / Bk[ok], axis=0)
    return float(min(1.0, q.min()))


def _fill_nan_nearest(P: np.ndarray) -> np.ndarray:
    P = P.copy()
    ok = np.isfinite(P).all(axis=1)
    if not ok.any():
        return P
    idx = np.flatnonzero(ok)
    for k in np.flatnonzero(~ok):
        P[k] = P[idx[np.argmin(np.abs(idx - k))]]
    return P


def gradient_fit(T: np.ndarray, counts: np.ndarray) -> dict:
    """Two-colour blend of the transmission profile T (n, 3), NaN = empty bin.
    Returns top/bottom colours, per-bin blend weights (0 = top, 1 = bottom), fit quality."""
    n = len(T)
    ok = np.isfinite(T).all(axis=1)
    idx = np.flatnonzero(ok)
    lo, hi = idx.min(), idx.max()
    span = hi - lo + 1
    t_bins = idx[idx <= lo + max(1, int(round(0.15 * span))) - 1]
    b_bins = idx[idx >= hi - max(1, int(round(0.15 * span))) + 1]
    top = np.median(T[t_bins], axis=0)
    bot = np.median(T[b_bins], axis=0)
    d = bot - top
    dd = float(d @ d)
    Tf = _fill_nan_nearest(T)
    t_raw = ((Tf - top) @ d) / dd if dd > 1e-12 else np.zeros(n)
    w = np.where(ok, np.sqrt(np.maximum(counts, 1)), 1e-3)
    t_iso = pav_increasing(t_raw, w)
    # the 15 % medians fix a robust DIRECTION; the ends of the isotonic fit fix the range, so the
    # texture reaches the measured extremes instead of flattening at the band medians
    t0, t1 = float(t_iso[lo]), float(t_iso[hi])
    if t1 - t0 > 1e-6:
        top, bot = top + t0 * d, top + t1 * d
        t_iso = (t_iso - t0) / (t1 - t0)
        d = bot - top
    t_iso = np.clip(t_iso, 0.0, 1.0)
    fitted = top[None] * (1 - t_iso[:, None]) + bot[None] * t_iso[:, None]
    resid = float(np.sqrt(np.mean((T[ok] - fitted[ok]) ** 2)))
    viol = int(np.sum(np.diff(t_raw[ok]) < -0.05))
    return {"top_linear_rgb": np.clip(top, 0, 1), "bottom_linear_rgb": np.clip(bot, 0, 1), "profile": t_iso,
            "profile_raw": t_raw, "blend_rms": resid, "raw_decreasing_steps": viol,
            "coverage_bins": [int(lo), int(hi)]}


def classify(T_stats: dict, T_prof: np.ndarray, coat: dict) -> dict:
    """Class from the transmission (back) statistics, then the coat test on top."""
    Yt, Yb = T_stats.get("Y_top20"), T_stats.get("Y_bottom20")
    ratio_bt = (Yb / Yt) if (Yt and Yb and Yt > 1e-6) else 1.0
    delta = abs((Yb or 0) - (Yt or 0))
    grad = (max(ratio_bt, 1.0 / max(ratio_bt, 1e-6)) > GRADIENT_RATIO) and delta > GRADIENT_MIN_DELTA
    clear = T_stats["ratio_Y"] > CLEAR_T and T_stats["saturation"] < CLEAR_SAT
    base = "gradient" if grad else ("clear" if clear else "tint")
    cls = "mirror" if coat.get("coat") else base
    return {"class": cls, "base_class": base, "bottom_over_top": round(ratio_bt, 4), "top_bottom_delta": round(delta, 4),
            "gradient_inverted": bool(grad and ratio_bt < 1.0),
            "rules": {"gradient": f"max(bottom/top, top/bottom) > {GRADIENT_RATIO} and |bottom - top| > {GRADIENT_MIN_DELTA}",
                      "clear": f"transmission Y > {CLEAR_T} and saturation < {CLEAR_SAT}",
                      "mirror": "coat test: front not explained by the back under exposure + neutral uncoated reflection"}}


# --------------------------------------------------------------------------- material
def gradient_png(top, bot, profile) -> np.ndarray:
    """(64, 1, 3) uint8 sRGB: row k = blend(top, bottom, profile[k]) in linear light, row 0 = top."""
    t = np.asarray(profile, float)[:, None]
    col = np.asarray(top, float)[None] * (1 - t) + np.asarray(bot, float)[None] * t
    return np.round(linear_to_srgb(col) * 255.0).astype(np.uint8)[:, None, :]


def smooth_profile(P: np.ndarray, sigma_bins: float = 1.5) -> np.ndarray:
    """NaN bins filled from the nearest valid bin, then a Gaussian along v."""
    return ndimage.gaussian_filter1d(_fill_nan_nearest(np.asarray(P, float)), sigma_bins, axis=0, mode="nearest")


BASE_F0 = ((IOR - 1) / (IOR + 1)) ** 2


def profile_png(rgb_linear: np.ndarray) -> np.ndarray:
    """(n, 1, 3) uint8 sRGB texture from a (n, 3) linear profile, row 0 = lens top (uv v = 0)."""
    return np.round(linear_to_srgb(np.asarray(rgb_linear, float)) * 255.0).astype(np.uint8)[:, None, :]


def mirror_textures(T_uv: np.ndarray, R_uv: np.ndarray, spec_scale: float = 1.0) -> dict:
    """Base colour, specular colour and baked-reflection profiles of the mirror approximation.

    three.js / Khronos: F0 = min(((ior-1)/(ior+1))^2 * specularColor, 1) * specularFactor, and the
    transmitted light is scaled by (1 - F); so specularColor = F0 / 0.04 (a factor S times a
    [0, 1] texture) and the base colour is T / (1 - F0_used), keeping the transmission T at normal incidence.
    ``spec_scale`` scales the coat's F0 actually rendered (F0_used = spec_scale * R; legacy KHR path, kept for tests);
    the remaining reflected radiance is carried by the emissive profile ``emissive_linear`` = R."""
    F0 = np.clip(np.asarray(R_uv, float), 0.0, 0.95)
    spec = F0 / BASE_F0
    S = float(max(spec.max(), 1e-6))
    F0_used = np.clip(F0 * float(spec_scale), 0.0, 0.95)
    base = np.clip(np.asarray(T_uv, float) / (1.0 - F0_used), 0.0, 1.0)
    return {"base_linear": base, "specular_scale": S, "specular_texture_linear": spec / S,
            "emissive_linear": np.clip(np.asarray(R_uv, float), 0.0, 1.0), "f0_used_max": float(F0_used.max())}


def gltf_material(name: str, cls: str, tint, base_texture: str | None = None,
                  specular: dict | None = None, emissive: dict | None = None) -> dict:
    """glTF 2.0 material dict (S9 builds it; {"image": path} placeholders become textures).
    ``specular``: {"scale": S, "texture": path} for the mirror approximation (KHR_materials_specular);
    ``emissive``: {"factor": e, "texture": path}, a baked reflection (legacy KHR path; the stage uses canonical optics)."""
    tint = np.clip(np.asarray(tint, float), 0.0, 1.0)
    pbr = {"baseColorFactor": [round(float(c), 5) for c in tint] + [1.0], "metallicFactor": 0.0,
           "roughnessFactor": LENS_ROUGHNESS}
    ext = {"KHR_materials_transmission": {"transmissionFactor": 1.0}, "KHR_materials_ior": {"ior": IOR}}
    extras = {"bsa_s8_class": cls}
    m = {"name": name, "pbrMetallicRoughness": pbr, "extensions": ext, "doubleSided": False, "extras": extras}
    if base_texture is not None:
        pbr["baseColorFactor"] = [1.0, 1.0, 1.0, 1.0]
        pbr["baseColorTexture"] = {"image": str(base_texture), "texCoord": 0}
        extras["base_color_texture"] = "1x64 on lens uv v, row 0 = lens top (v = 0)"
    if specular is not None:
        S = round(float(specular["scale"]), 4)
        ext["KHR_materials_specular"] = {"specularFactor": 1.0, "specularColorFactor": [S, S, S],
                                         "specularColorTexture": {"image": str(specular["texture"]), "texCoord": 0}}
        extras["mirror_approximation"] = ("M1 interim (studio_v1 coat fitting is M3): the coat's F0(v) as "
                                          "KHR_materials_specular (1x64 on lens uv v), scaled so the runtime's room "
                                          "lights reflect no larger than bare glass or the photo's own highlights")
    if emissive is not None:
        e = round(float(emissive["factor"]), 4)
        m["emissiveFactor"] = [e, e, e]
        m["emissiveTexture"] = {"image": str(emissive["texture"]), "texCoord": 0}
        extras["baked_reflection"] = ("M1 interim: the coat's reflected radiance over the photo's backdrop R(v) "
                                      "(1x64 on lens uv v) as emission x factor fitted in the actual AR runtime; "
                                      "view-independent, i.e. a uniformly lit environment")
    return m


def lens_appearance_descriptor(T_profile_uv: np.ndarray, reflect_rgb, angular=None, rear_rgb=None,
                               roughness: float = LENS_ROUGHNESS) -> dict | None:
    """LensAppearance v1 (reconstruction.lens_appearance; the runtime's canonical LENSES_lens_appearance) from the
    total normal-incidence transmission profile on lens uv v (row 0 = top). Knots use the descriptor's own convention
    (v = 0 bottom, 1 top). ``reflect_rgb`` = R(0); the runtime's transmission is (1 - R) exp(-density), so the density
    is -ln(T / (1 - R(0))). ``angular``: [(angle_deg, rgb), ...] starting at 0 (= R(0)) and ending at 90, or None
    (Schlick from R(0)); ``rear_rgb``: the optional rear reflection fraction."""
    from reconstruction.lens_appearance import (DensityKeyframe, LensAppearance, ReflectanceKeyframe)
    R = np.clip(np.asarray(reflect_rgb, float), 0.0, 0.95)
    T = np.asarray(T_profile_uv, float)
    n = len(T)
    if n == 1:
        picks = [0]
    else:
        picks = sorted(set(np.round(np.linspace(0, n - 1, min(16, n))).astype(int).tolist()))
    keys = []
    for k in reversed(picks):                          # bottom first
        v_desc = 1.0 - k / (n - 1) if n > 1 else 0.0   # a single knot must sit at v = 0
        Ti = np.clip(T[k] / (1.0 - R), 1e-4, 1.0)
        keys.append(DensityKeyframe(round(v_desc, 6), tuple(round(float(-math.log(t)), 6) for t in Ti)))
    if len(keys) > 1:
        keys[0] = DensityKeyframe(0.0, keys[0].optical_density_rgb)
        keys[-1] = DensityKeyframe(1.0, keys[-1].optical_density_rgb)
    R0 = tuple(round(float(r), 6) for r in R)
    ang = None
    if angular is not None:
        ang = []
        for a, rgb in angular:
            rgb = R0 if float(a) == 0.0 else tuple(round(float(x), 6) for x in np.clip(np.asarray(rgb, float), 0.0, 1.0))
            ang.append(ReflectanceKeyframe(round(float(a), 4), rgb))
        ang = tuple(ang)
    rear = None if rear_rgb is None else tuple(round(float(x), 6) for x in np.clip(rear_rgb, 0.0, 1.0))
    la = LensAppearance(tuple(keys), R0, IOR, float(roughness), ang, rear)
    d = la.to_dict()
    LensAppearance.from_dict(json.loads(json.dumps(d)))      # schema round trip
    return d


# --------------------------------------------------------------------------- S3 occluders
def occluder_mask(V_mm: np.ndarray, F: np.ndarray, frame: core.NormFrame, camera, shape: tuple[int, int],
                  region: np.ndarray, plate_camera, plate_region: np.ndarray, mm_px: float) -> tuple[np.ndarray, dict]:
    """Pixels of ``region`` (view of ``camera``) where generator structure lies behind the lens.

    The lens is bounded by two robust quadratics z(x, y) fitted through ``plate_camera`` (the front
    camera) inside ``plate_region``: its front surface (first hits of the whole mesh) and its back
    surface (first hits of the faces that face AWAY from that camera: a closed lens solid's rear).
    Faces whose centroid projects into the plate region and lies more than OCCLUDER_BEHIND_MM behind
    the back surface are structure (temples, nose pads, hinges). Without the back surface a thick
    generator lens counted its own rear as structure (Miu: half of each lens). With no plate at all
    (empty rims) every face projecting into the plate region is structure."""
    from . import raster
    info = {}
    ys, xs = np.nonzero(plate_region)
    roi = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
    C = V_mm[F].mean(axis=1)
    pc = core.project_mm(C, plate_camera, frame)
    pi = np.clip(np.round(pc).astype(int), 0, [plate_region.shape[1] - 1, plate_region.shape[0] - 1])
    footprint = grow_px(plate_region, 2.0 / mm_px)[pi[:, 1], pi[:, 0]]

    def surface(faces: np.ndarray, name: str):
        r = raster.render(V_mm, F[faces], plate_camera, frame, plate_region.shape, stride=2, roi=roi, want_points=True)
        us, vs = r["grid"]
        inside = plate_region[np.clip(np.round(vs).astype(int), 0, plate_region.shape[0] - 1)][
            :, np.clip(np.round(us).astype(int), 0, plate_region.shape[1] - 1)]
        hits = r["mask"] & inside
        frac = float(hits.sum() / max(1, inside.sum()))
        info[f"{name}_hit_fraction"] = round(frac, 4)
        if frac < PLATE_MIN_HIT:
            return None
        P = r["points"][hits]
        A = np.stack([np.ones(len(P)), P[:, 0], P[:, 1], P[:, 0] ** 2, P[:, 1] ** 2, P[:, 0] * P[:, 1]], 1)
        keep = np.ones(len(P), bool)
        for _ in range(3):
            coef, *_ = np.linalg.lstsq(A[keep], P[keep, 2], rcond=None)
            res = P[:, 2] - A @ coef
            keep = np.abs(res) < max(1.0, 3.0 * _mad_sigma(res[keep]))
        info[f"{name}_rms_mm"] = round(float(np.sqrt(np.mean(res[keep] ** 2))), 3)
        return coef

    Ac = np.stack([np.ones(len(C)), C[:, 0], C[:, 1], C[:, 0] ** 2, C[:, 1] ** 2, C[:, 0] * C[:, 1]], 1)
    front_coef = surface(np.ones(len(F), bool), "plate")
    if front_coef is not None:
        T = V_mm[F]
        n = np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0])
        away = (n @ raster.camera_basis(plate_camera)[2]) < 0
        back_coef = surface(away, "plate_back")
        z_front = Ac @ front_coef
        z_back = z_front if back_coef is None else np.minimum(Ac @ back_coef, z_front)
        if back_coef is not None:
            info["lens_thickness_median_mm"] = round(float(np.median((z_front - Ac @ back_coef)[footprint])), 3) \
                if footprint.any() else None
        behind = footprint & (C[:, 2] < z_back - OCCLUDER_BEHIND_MM)
        info["plate"] = "quadratic front + back surfaces" if back_coef is not None else "quadratic front only"
    else:
        behind = footprint
        info["plate"] = "none (empty rims): every face in the lens footprint is structure"
    info["faces_behind"] = int(behind.sum())
    out = np.zeros(shape, bool)
    if behind.any():
        ys2, xs2 = np.nonzero(region)
        roi2 = (int(xs2.min()), int(ys2.min()), int(xs2.max()) + 1, int(ys2.max()) + 1)
        r2 = raster.render(V_mm, F[behind], camera, frame, shape, stride=1, roi=roi2)
        out[roi2[1]:roi2[3], roi2[0]:roi2[2]] = r2["mask"]
        out = grow_px(out, OCCLUDER_GROW_MM / mm_px) & region
    info["occluded_px"] = int(out.sum())
    return out, info


def _s3_result(product: str, run: str) -> dict | None:
    p = core.run_dir(run, product) / "s3_cameras" / "result.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


# --------------------------------------------------------------------------- stage
def _lens_boxes(polys):
    return [(float(p[:, 0].min()), float(p[:, 1].min()), float(p[:, 0].max()), float(p[:, 1].max())) for p in polys]


def _mask(poly, shape):
    return rasterize([poly], shape) > 0


def measure(product: str, run: str = "m1") -> dict:
    """All photometry for one product (no files written). Returns a dict of samples/filters/stats."""
    prod = core.PRODUCTS[product]
    s0, a0 = core.stage_dir(run, product, "s0_intake").load()
    s2, a2 = core.stage_dir(run, product, "s2_front").load()
    n_lens = len(s2["lenses"])
    sides = [l["side"] for l in s2["lenses"]]
    polys_f = [np.asarray(a2[f"lens{i}_poly_front"], float) for i in range(1, n_lens + 1)]
    boxes = _lens_boxes(polys_f)
    M_sf = np.asarray(s2["source_to_front"], float)
    mm_front = float(s2["mm_per_px_provisional"]) / float(M_sf[0, 0])
    Mb = s2["registration"].get("back_mirrored_to_front")
    rgb = {v: core.load_photo(prod, v) for v in ("front", "back")}
    flags, notes = [], []
    views = {}
    # ---------------- geometry per view
    geo = {}
    Hf, Wf = rgb["front"].shape[:2]
    geo["front"] = {"masks": [_mask(p, (Hf, Wf)) for p in polys_f], "to_front": lambda x, y: (x, y), "mm_px": mm_front}
    if Mb is not None:
        Mb = np.asarray(Mb, float)
        Mi = affine_invert(Mb)
        Hb, Wb = rgb["back"].shape[:2]
        lens_b = a0["lens_back"].astype(bool)
        masks_b = []
        for p in polys_f:
            pm = affine_apply(Mi, p)
            pb = np.stack([Wb - 1 - pm[:, 0], pm[:, 1]], 1)
            masks_b.append(_mask(pb, (Hb, Wb)) & lens_b)

        def to_front_b(x, y, Mb=Mb, Wb=Wb):
            q = affine_apply(Mb, np.stack([Wb - 1 - x, y], 1))
            return q[:, 0], q[:, 1]
        geo["back"] = {"masks": masks_b, "to_front": to_front_b, "mm_px": mm_front * float(Mb[0, 0])}
    else:
        flags.append("back_unregistered")
    # ---------------- S3 occluders (when the cameras exist)
    s3 = _s3_result(product, run)
    occ = {}
    if s3 is not None and "cameras" in s3:
        try:
            from . import generator
            gen = generator.load(product, run)
            cams = {v: core.camera_from_dict(s3["cameras"][v]["camera"]) for v in geo if v in s3["cameras"]}
            plate_region = np.zeros((Hf, Wf), bool)
            for m in geo["front"]["masks"]:
                plate_region |= erode_px(m, 1.5 / mm_front)
            for v in geo:
                if v not in cams:
                    continue
                reg = np.zeros(rgb[v].shape[:2], bool)
                for m in geo[v]["masks"]:
                    reg |= m
                occ[v] = occluder_mask(gen.Vd, gen.Fd, gen.frame, cams[v], reg.shape, reg, cams["front"], plate_region,
                                       geo[v]["mm_px"])
            see_through = "s3_render+colour"
        except Exception as e:   # noqa: BLE001 - fall back to the colour rule, loudly
            occ = {}
            see_through = "colour"
            flags.append("s3_occluder_failed")
            notes.append(f"S3 occluder render failed: {type(e).__name__}: {e}")
    else:
        see_through = "colour"
        flags.append("see_through_colour_filter_only")
        notes.append("s3_cameras/result.json absent: see-through structure removed by the colour/outlier rules only")
    # ---------------- per view photometry
    for v, g in geo.items():
        res0 = s0["views"][v]
        bg = backdrop_linear(res0["backdrop_lab_coef"], res0["shape"])
        s = sample_view(v, rgb[v], bg, g["masks"], boxes, sides, g["to_front"], g["mm_px"])
        union = np.zeros(rgb[v].shape[:2], bool)
        for m in g["masks"]:
            union |= m
        fm = a0[f"matte_{v}"].astype(bool) & ~grow_px(union | a0[f"lens_{v}"].astype(bool), 1.0 / g["mm_px"])
        bg_lab = linear_to_lab(np.median(s.bg, axis=0)) if len(s.ys) else np.array([100.0, 0, 0])
        fc = frame_colours(rgb[v], fm, bg_lab)
        o = occ.get(v)
        filt = filter_samples(s, fc, None if o is None else o[0])
        st = view_stats(s, filt["keep"])
        st.update({"region_px": s.info["region_px"], "erode_px": s.info["erode_px"], "mm_per_px": round(g["mm_px"], 5),
                   "filter": filt["rules"], "frame_colours_lab": fc})
        if o is not None:
            st["s3_occluder"] = o[1]
        views[v] = {"samples": s, "filter": filt, "stats": st}
    return {"product": product, "run": run, "views": views, "flags": flags, "notes": notes, "see_through": see_through,
            "sides": sides, "boxes": boxes, "rgb": rgb, "s0": s0, "s2": s2,
            # the cameras payload, not result.json (whose timings change on an identical recomputation)
            "s3_sha": None if s3 is None else __import__("hashlib").sha256(
                json.dumps(s3.get("cameras"), sort_keys=True).encode()).hexdigest()}


def analyse(meas: dict) -> dict:
    """Class, transmission, gradient, mirror and material from ``measure`` output (no I/O)."""
    views = meas["views"]
    flags = list(meas["flags"])
    usable = {v: views[v]["stats"].get("kept_px", 0) >= MIN_VIEW_PX for v in views}
    for v in ("front", "back"):
        if v in views and not usable[v]:
            flags.append(f"{v}_few_pixels")
    prof = {}
    for v in views:
        s, keep = views[v]["samples"], views[v]["filter"]["keep"]
        P64, c64 = bin_profile(s.v[keep], s.ratio[keep], N_BINS)
        P32, c32 = bin_profile(s.v[keep], s.ratio[keep], COAT_BINS)
        prof[v] = {"p64": P64, "c64": c64, "p32": P32, "c32": c32}
    t_view = "back" if usable.get("back") else "front"
    if t_view == "front":
        flags.append("transmission_from_front")
    T_stats = views[t_view]["stats"]
    if usable.get("front") and usable.get("back"):
        clipped = views["front"]["stats"]["backdrop_clipped"] or views["back"]["stats"]["backdrop_clipped"]
        coat = coat_test(prof["front"]["p32"], prof["back"]["p32"], views["front"]["stats"]["ratio_Y"], clipped)
    else:
        coat = {"testable": False, "coat": False, "reason": "front or back unusable"}
        flags.append("coat_untestable")
    if any(views[v]["stats"].get("backdrop_clipped") for v in views):
        flags.append("backdrop_clipped")
    if coat.get("front_darker_than_exposure_band"):
        flags.append("front_darker_than_back")
    # Both photos are upper bounds on the transmission: each adds non-negative light (a surface
    # reflection; a saturated backdrop inflates the ratio). The chroma of T comes from the back (no
    # coat); its level is the largest kappa <= 1 that keeps the front >= kappa * back in every channel.
    kappa_e = transmission_scale(prof["front"]["p32"], prof["back"]["p32"]) \
        if (usable.get("front") and usable.get("back") and t_view == "back") else 1.0
    if kappa_e < 0.999:
        flags.append("transmission_scaled_to_front")
    # classify the transmission the material will carry (the scaled one)
    T_cls = dict(T_stats)
    for k in ("ratio_Y", "Y_top20", "Y_bottom20"):
        if T_cls.get(k) is not None:
            T_cls[k] = T_cls[k] * kappa_e
    cl = classify(T_cls, prof[t_view]["p64"], coat)
    cls, base = cl["class"], cl["base_class"]
    T64 = prof[t_view]["p64"]
    T_pooled = np.clip(np.asarray(T_stats["ratio_rgb"], float), 0.0, 1.0)
    mirror = None
    reflected = None
    R_uv = None
    T_mirror_uv = None
    if cls == "mirror":
        Fm = np.asarray(views["front"]["stats"]["ratio_rgb"], float)
        reflected = np.clip(Fm - kappa_e * T_pooled, 0.0, 1.0)
        hue = reflected / max(float(reflected.max()), 1e-6)
        # the coat's look varies over the lens (Oakley: green top, purple bottom): per-v profiles
        T_mirror_uv = np.clip(kappa_e * smooth_profile(prof[t_view]["p64"]), 0.0, 1.0)
        R_uv = np.clip(smooth_profile(prof["front"]["p64"]) - T_mirror_uv, 0.0, 0.95)
        mirror = {"reflected_linear_rgb": np.round(reflected, 4).tolist(), "reflected_hue": np.round(hue, 4).tolist(),
                  "front_linear_rgb": np.round(Fm, 4).tolist(), "exposure_scale_kappa": round(kappa_e, 4),
                  "base_class": base,
                  "reflected_profile_linear_rgb": np.round(R_uv, 4).tolist(),
                  "transmission_profile_linear_rgb": np.round(T_mirror_uv, 4).tolist(),
                  "method": "reflected = front - kappa * transmission, kappa = the largest <= 1 that keeps "
                            "front >= kappa * back per channel (median over common v bins); profiles per lens uv v "
                            "(64 rows, row 0 = top), smoothed 1.5 bins",
                  "approximation": "M3: KHR_materials_specular F0(v) = reflected radiance under an environment as bright "
                                   "as the backdrop, baked on lens uv v; the real coat (angle dependence, "
                                   "iridescence) needs studio_v1 fitting"}
        flags.append("mirror_approximation_m3")
    T_mat = np.clip(kappa_e * T_pooled, 0.0, 1.0)
    gradient = None
    if base == "gradient":
        gf = gradient_fit(np.clip(kappa_e * T64, 0, 1), prof[t_view]["c64"])
        gradient = {"top_linear_rgb": np.round(gf["top_linear_rgb"], 5).tolist(),
                    "bottom_linear_rgb": np.round(gf["bottom_linear_rgb"], 5).tolist(),
                    "profile": np.round(gf["profile"], 5).tolist(),
                    "profile_semantics": "blend weight per row, 0 = top colour, 1 = bottom colour, linear light; "
                                         "row k samples lens uv v = (k + 0.5) / 64, v = 0 at the lens top",
                    "measured_linear_rgb": [None if not np.isfinite(r).all() else np.round(r, 4).tolist() for r in np.clip(kappa_e * T64, 0, 1)],
                    "blend_rms": round(gf["blend_rms"], 4), "raw_decreasing_steps": gf["raw_decreasing_steps"],
                    "coverage_bins": gf["coverage_bins"], "texture_file": "gradient.png", "texture_size": [1, N_BINS]}
        if cl["gradient_inverted"]:
            flags.append("gradient_inverted")
        if gf["raw_decreasing_steps"] > 2:
            flags.append("gradient_not_monotone_raw")
        T_uv = gf["top_linear_rgb"][None] * (1 - gf["profile"][:, None]) + gf["bottom_linear_rgb"][None] * gf["profile"][:, None]
    else:
        T_uv = T_mat[None]
    if T_mirror_uv is not None:
        T_uv = T_mirror_uv
    return {"class": cls, "classification": cl, "coat": coat, "tint_linear_rgb": np.round(T_mat, 5).tolist(),
            "transmission_view": t_view, "kappa_e": kappa_e, "reflected": reflected, "R_uv": R_uv, "gradient": gradient,
            "mirror": mirror, "T_uv": T_uv, "profiles": prof, "flags": flags}


def predict_look(T_rgb, reflected, bg_lin) -> np.ndarray:
    """Linear colour of the lens over a background: T * bg + reflected."""
    out = np.asarray(T_rgb, float) * np.asarray(bg_lin, float)
    if reflected is not None:
        out = out + np.asarray(reflected, float)
    return np.clip(out, 0.0, 1.0)


def swatches(meas: dict, ana: dict, n_bands: int = 8) -> dict:
    """Per vertical band: photo median colour vs the predicted look (front/back over their backdrops)."""
    out = {}
    T_uv = ana["T_uv"]
    for v, d in meas["views"].items():
        s, keep = d["samples"], d["filter"]["keep"]
        rows = []
        for b in range(n_bands):
            lo, hi = b / n_bands, (b + 1) / n_bands
            sel = keep & (s.v >= lo) & (s.v < hi)
            if sel.sum() < MIN_BIN_PX:
                rows.append(None)
                continue
            photo = np.median(s.lin[sel], axis=0)
            bg = np.median(s.bg[sel], axis=0)
            k = min(len(T_uv) - 1, int((lo + hi) / 2 * len(T_uv)))
            R_uv = ana.get("R_uv")
            refl_k = None if R_uv is None else R_uv[min(len(R_uv) - 1, int((lo + hi) / 2 * len(R_uv)))]
            pred = predict_look(T_uv[k], refl_k if v == "front" else None, bg)
            white = predict_look(T_uv[k], refl_k, np.ones(3))
            de = float(delta_e00(linear_to_lab(photo), linear_to_lab(pred)))
            rows.append({"band": b, "photo": photo, "pred": pred, "white": white, "bg": bg, "dE00": de})
        des = [r["dE00"] for r in rows if r]
        out[v] = {"bands": rows, "dE00_median": round(float(np.median(des)), 2) if des else None,
                  "dE00_max": round(float(np.max(des)), 2) if des else None}
    return out


# --------------------------------------------------------------------------- AR calibration (mirror) + edge ring
BLOB_CLIP = 250                   # a blob pixel is clipped in some channel ...
BLOB_EXCESS_L = 10.0              # ... and brighter than the lens's median L* by this much
EDGE_RING_WIDTH_MM = (0.3, 0.5)   # clear-lens frosted edge ring: width bounds (task spec) ...
EDGE_RING_TRANSMISSION = (0.3, 0.5)
EDGE_RING_BASE = (0.8, 0.95)      # ... base colour (slightly white) bounds
EDGE_RING_ROUGHNESS = 0.5
EDGE_PROFILE_MM = 2.0


def blob_areas_mm2(img: np.ndarray, region: np.ndarray, mm_per_px: float) -> tuple[float, int]:
    """Largest 8-connected blob (clipped in some channel AND >= BLOB_EXCESS_L above the region's median L*)
    inside ``region``, in mm^2; and the blob pixel count."""
    from skimage.color import rgb2lab
    if region.sum() < 20:
        return 0.0, 0
    L = rgb2lab(img[region][None].astype(np.float64) / 255.0)[0][:, 0]
    hit = np.zeros(region.shape, bool)
    hit[region] = (img[region] >= BLOB_CLIP).any(-1) & (L >= np.median(L) + BLOB_EXCESS_L)
    if not hit.any():
        return 0.0, 0
    lab, n = ndimage.label(hit, structure=np.ones((3, 3), bool))
    big = int(np.bincount(lab.ravel())[1:].max())
    return float(big * mm_per_px ** 2), int(hit.sum())


def _lens_calibration_parts(product: str, run: str) -> tuple[dict, list | None]:
    """S6 frame + accepted S5 temples (flat dark material) + S6 lens sheets, as S9 exports them."""
    s2, _ = core.stage_dir(run, product, "s2_front").load()
    s5, a5 = core.stage_dir(run, product, "s5_temples").load()
    s6, a6 = core.stage_dir(run, product, "s6_assembly").load()
    from . import export
    parts = {"frame": {"V": a6["frame_V"], "F": a6["frame_F"], "material": "cal_frame"}}
    for side in ("R", "L"):
        ta = export.temple_arrays(a5, s5, side, a6)      # exactly what S9 exports (a rejected arm: its donors only)
        if ta is not None:
            parts[f"temple_{side}"] = {"V": ta[0], "F": ta[1], "material": "cal_frame"}
    sides = [l.get("side") for l in s2.get("lenses", [])]
    i = 1
    while f"lens{i}_V" in a6:
        side = sides[i - 1] if i - 1 < len(sides) and sides[i - 1] in ("R", "L", "C") else (
            "C" if (i == 1 and f"lens{i + 1}_V" not in a6) else ("R" if a6[f"lens{i}_V"][:, 0].mean() > 0 else "L"))
        parts[f"lens_{side}"] = {"V": a6[f"lens{i}_V"], "F": a6[f"lens{i}_F"], "material": "lens"}
        if f"lens{i}_uv" in a6:
            parts[f"lens_{side}"]["UV"] = a6[f"lens{i}_uv"]
        i += 1
    return parts, export._find_vec3(s6, "bridge")


# --------------------------------------------------------------------------- canonical optics (the runtime's LensAppearance)
ANGLE_BIN_DEG = 5.0               # incidence-angle bins of the front photo's reflected light (mirror coats)
ANGLE_MIN_PX = 200                # a bin needs this many kept front pixels
ANGLE_MIN_SPREAD_DEG = 15.0       # below this measured spread the coat has no angular table (Schlick from R(0))
ANGLE_MAX_KNOTS = 14              # + the 0 and 90 deg knots <= the runtime's 16
MIRROR_REAR_FRACTION = UNCOATED_R # the uncoated rear face of a front-coated mirror
CANON_SCALE_STEPS = 8            # scale grid on the measured coat R(angle): s_max * k / 8, k = 0..8
CANON_R_MAX = 0.95                # ... where s_max puts the coat's largest reflectance at this (< 1: some transmission)


def front_incidence_deg(product: str, run: str, s: "ViewSamples") -> np.ndarray:
    """Incidence angle (deg, from the surface normal) of the front camera's ray on the constructed S6 lens front at
    each front-photo sample (NaN where the ray misses the lens). The frozen S3 front camera; the S6 lens solids'
    +Z caps (the surface S9 exports)."""
    from . import cameras, depth, raster
    frame, cams, _ = cameras.load_cameras(product, run)
    _, a6 = core.stage_dir(run, product, "s6_assembly").load()
    Vs, Fs, off = [], [], 0
    i = 1
    while f"lens{i}_V" in a6:
        Vs.append(np.asarray(a6[f"lens{i}_V"], float))
        Fs.append(np.asarray(a6[f"lens{i}_F"], np.int64) + off)
        off += len(Vs[-1])
        i += 1
    out = np.full(len(s.ys), np.nan)
    if not Vs:
        return out
    V, F = np.vstack(Vs), np.vstack(Fs)
    scene = raster.RasterScene(V, F, frame)
    res = scene.cast(cams["front"], s.xs.astype(float), s.ys.astype(float), want_normal=True)
    hit = res["hit"]
    if not hit.any():
        return out
    _, D = depth.camera_rays_mm(cams["front"], frame, np.c_[s.xs, s.ys].astype(float)[hit], None)
    n = res["normal"][hit].astype(float)
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
    D /= np.maximum(np.linalg.norm(D, axis=1, keepdims=True), 1e-12)
    out[hit] = np.degrees(np.arccos(np.clip(np.abs(np.sum(n * D, axis=1)), 0.0, 1.0)))
    return out


def coat_angular_table(meas: dict, ana: dict, angle_deg: np.ndarray) -> dict:
    """The coat's reflectance per incidence angle from the FRONT photo (fit view): at each kept front pixel
    R = front ratio - kappa T(v) (the same model as ``analyse``: the back photo's transmission, exposure-scaled), and
    the median per ``ANGLE_BIN_DEG`` bin of the incidence angle on the constructed lens (``front_incidence_deg``). A
    wrapped shield is seen from 0 deg at its centre to 50-60 deg at its ends in the front photo itself, which is
    what the canonical angular table describes (a coat's colour shifts with angle); a flat lens has no spread and
    gets R(0) with Schlick. Returns {r0, table [(deg, rgb)] incl. 0 and 90 (= 1: grazing), bins, spread_deg}."""
    fv = meas["views"]["front"]
    s, keep = fv["samples"], fv["filter"]["keep"]
    T_uv = np.asarray(ana["T_uv"], float)
    n = len(T_uv)
    k = np.clip(np.round(np.clip(s.v, 0.0, 1.0) * n - 0.5).astype(int), 0, n - 1)
    R_px = s.ratio - T_uv[k]
    ok = keep & np.isfinite(angle_deg)
    bins = []
    if ok.sum() >= ANGLE_MIN_PX:
        edges = np.arange(0.0, 90.0 + ANGLE_BIN_DEG, ANGLE_BIN_DEG)
        for lo_, hi_ in zip(edges[:-1], edges[1:]):
            sel = ok & (angle_deg >= lo_) & (angle_deg < hi_)
            if sel.sum() >= ANGLE_MIN_PX:
                bins.append({"deg": float(np.median(angle_deg[sel])), "lo": lo_, "px": int(sel.sum()),
                             "rgb": np.clip(np.median(R_px[sel], axis=0), 0.0, 0.95)})
    if not bins:
        r0 = np.clip(np.median(R_px[keep], axis=0), 0.0, 0.95) if keep.any() else np.full(3, UNCOATED_R)
        return {"r0": r0, "table": None, "bins": [], "spread_deg": 0.0}
    while len(bins) > ANGLE_MAX_KNOTS:                       # merge the two least populated neighbours
        j = int(np.argmin([bins[i]["px"] + bins[i + 1]["px"] for i in range(len(bins) - 1)]))
        a, b = bins[j], bins[j + 1]
        w = a["px"] + b["px"]
        bins[j:j + 2] = [{"deg": (a["deg"] * a["px"] + b["deg"] * b["px"]) / w, "lo": a["lo"], "px": w,
                          "rgb": (a["rgb"] * a["px"] + b["rgb"] * b["px"]) / w}]
    spread = bins[-1]["deg"] - bins[0]["deg"]
    rep_ = [{"deg": round(b["deg"], 2), "px": b["px"], "rgb": np.round(b["rgb"], 4).tolist()} for b in bins]
    if spread < ANGLE_MIN_SPREAD_DEG:
        w = np.array([b["px"] for b in bins], float)
        r0 = np.sum([b["rgb"] * wi for b, wi in zip(bins, w)], axis=0) / w.sum()
        return {"r0": r0, "table": None, "bins": rep_, "spread_deg": round(spread, 2)}
    r0 = bins[0]["rgb"]
    table = [(0.0, r0)] + [(b["deg"], b["rgb"]) for b in bins if 0.0 < b["deg"] < 90.0] + [(90.0, np.ones(3))]
    return {"r0": r0, "table": table, "bins": rep_, "spread_deg": round(spread, 2)}


def scaled_descriptor(T_uv: np.ndarray, coat: dict, scale, rear=None) -> dict:
    """The canonical descriptor with the coat's R scaled by ``scale`` (a scalar or per RGB channel; the 90 deg grazing
    knot stays 1): the transmission T is kept where energy allows (density = -ln(T / (1 - s R(0))), T <= 1 - R)."""
    sc = np.broadcast_to(np.asarray(scale, float), (3,))
    r0 = np.clip(sc * np.asarray(coat["r0"], float), 0.0, CANON_R_MAX)
    table = None
    if coat.get("table"):
        table = [(a, r0 if a == 0.0 else (np.ones(3) if a >= 90.0 else np.clip(sc * np.asarray(rgb, float), 0, CANON_R_MAX)))
                 for a, rgb in coat["table"]]
    return lens_appearance_descriptor(T_uv, r0, table, rear)


FIT_R_NEUTRAL = 0.5               # neutral coat of the second calibration render (the first reflects nothing)
FIT_MIN_BIN_PX = 150              # an angle bin is fitted when both the photo and the render have this many pixels
FIT_ENERGY_MARGIN = 0.005         # R <= 1 - max T(v) - this: the measured transmission stays exact (no energy clip)
FIT_SMOOTH = 4.0                  # Lab units per unit of R between neighbouring angle knots: a coat's colour turns
                                  # smoothly with angle; unregularised, knots between the photo's bins flipped 0 <-> 0.9
LENS_BANDS = 8                    # lens-local height bands of the rendered-lens colour check (``band_de00``)
FIT_STRENGTH_MAX = 4.0            # a knot's strength: at most 4x the measured coat (grid-scaled) ...
FIT_COLOUR_SHIFT = 0.5            # ... plus one colour shift common to all knots, |shift| <= this per channel
FIT_PRIOR = 8.0                   # stage 2: per-knot RGB, Lab units per unit of R away from the stage-1 coat. Over the
                                  # photo's white backdrop a dim room reflection trades against the transmission, and a
                                  # free knot was tinted against the room part it reflects in the calibration view
                                  # (INVU's grazing knots went yellow, R 0.74/0.68/0 at 3: invisible there, a yellow rim
                                  # in every other view; at 8 a faint warm edge)


def _appearance(T_uv: np.ndarray, r0, table=None, rear=None):
    """``LensAppearance`` of ``lens_appearance_descriptor`` without its 6-decimal rounding (a least-squares fit needs a
    smooth model); same density and angular conventions."""
    from reconstruction.lens_appearance import DensityKeyframe, LensAppearance, ReflectanceKeyframe
    R0 = np.clip(np.asarray(r0, float), 0.0, 0.95)
    T = np.asarray(T_uv, float)
    n = len(T)
    picks = [0] if n == 1 else sorted(set(np.round(np.linspace(0, n - 1, min(16, n))).astype(int).tolist()))
    keys = []
    for k in reversed(picks):
        v_desc = 1.0 - k / (n - 1) if n > 1 else 0.0
        Ti = np.clip(T[k] / (1.0 - R0), 1e-4, 1.0)
        keys.append(DensityKeyframe(float(v_desc), tuple(float(-math.log(t)) for t in Ti)))
    if len(keys) > 1:
        keys[0] = DensityKeyframe(0.0, keys[0].optical_density_rgb)
        keys[-1] = DensityKeyframe(1.0, keys[-1].optical_density_rgb)
    ang = None
    if table is not None:
        ang = tuple(ReflectanceKeyframe(float(a), tuple(float(x) for x in (R0 if float(a) == 0.0 else np.clip(rgb, 0, 1))))
                    for a, rgb in table)
    return LensAppearance(tuple(keys), tuple(float(x) for x in R0), IOR, LENS_ROUGHNESS, ang,
                          None if rear is None else tuple(float(x) for x in rear))


def table_descriptor(T_uv: np.ndarray, knots: list[float], R: np.ndarray, rear=None) -> dict:
    """The canonical descriptor whose coat reflectance is ``R[k]`` at incidence ``knots[k]`` (R(0) = R[0]; grazing
    90 deg = 1, like the measured table)."""
    R = np.clip(np.asarray(R, float), 0.0, CANON_R_MAX)
    table = [(0.0, R[0])] + [(float(a), R[k]) for k, a in enumerate(knots) if 0.0 < a < 90.0] + [(90.0, np.ones(3))]
    return lens_appearance_descriptor(T_uv, R[0], table, rear)


def render_lens_pixels(meta: dict, prims: list[dict]) -> dict:
    """Lens pixels of an actual-AR render with nothing behind the lens (eroded 1 px): their position, the incidence
    angle on the lens sheet (hit face normal vs the ray from the render camera, as the runtime shader measures it) and
    the lens-local height v (TEXCOORD_0.y) at the hit."""
    from . import texture
    lab = texture.ar_label(meta, prims)
    lens_ids = [i for i, p in enumerate(prims) if str(p["node"]).startswith("lens")]
    lens_px = texture.erode_px(np.isin(lab["first"], lens_ids), 1)
    clear = lens_px & (lab["second"] < 0)
    ys, xs = np.nonzero(clear)
    first, face, pts = lab["first"][ys, xs], lab["face"][ys, xs], lab["points"][ys, xs]
    theta, v = np.full(len(ys), np.nan), np.full(len(ys), np.nan)
    cam = np.asarray(meta["camera_in_asset"], float)
    for pid in np.unique(first):
        sel = first == pid
        prim = prims[int(pid)]
        Fp = prim["F"][face[sel]]
        A, B, C = (prim["V"][Fp[:, k]] for k in range(3))
        n = np.cross(B - A, C - A)
        n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-18)
        d = pts[sel] - cam
        d /= np.maximum(np.linalg.norm(d, axis=1, keepdims=True), 1e-18)
        theta[sel] = np.degrees(np.arccos(np.clip(np.abs(np.sum(n * d, axis=1)), 0.0, 1.0)))
        if "UV" in prim:
            v[sel] = np.clip(texture.hit_uv(prim, face[sel], pts[sel])[:, 1], 0.0, 1.0)
    return {"ys": ys, "xs": xs, "theta": theta, "v": v, "lens_px": lens_px, "clear": clear}


def angle_bins(theta: np.ndarray, min_px: int = FIT_MIN_BIN_PX) -> list[tuple[float, float]]:
    """``ANGLE_BIN_DEG`` bins of an incidence-angle sample with at least ``min_px`` members."""
    out = []
    for lo in np.arange(0.0, 90.0, ANGLE_BIN_DEG):
        if int(np.sum((theta >= lo) & (theta < lo + ANGLE_BIN_DEG))) >= min_px:
            out.append((float(lo), float(lo + ANGLE_BIN_DEG)))
    return out


def render_highlight(px: np.ndarray) -> np.ndarray:
    """Render lens pixels that are a highlight (the room light reflected): clipped in some channel AND
    ``BLOB_EXCESS_L`` above the lens pixels' median L* (``blob_areas_mm2``'s definition). A clear lens over a saturated
    white backdrop is clipped everywhere and is no highlight: it is the colour to compare."""
    from skimage.color import rgb2lab
    px = np.asarray(px)
    if not len(px):
        return np.zeros(0, bool)
    L = rgb2lab(px[None].astype(np.float64) / 255.0)[0][:, 0]
    return (px >= BLOB_CLIP).any(-1) & (L >= np.median(L) + BLOB_EXCESS_L)


def band_de00(photo_lin: np.ndarray, photo_v: np.ndarray, render_lin: np.ndarray, render_v: np.ndarray,
              n: int = LENS_BANDS) -> dict:
    """The rendered lens against the photo, band by band over the lens-local height (``n`` bands, top to bottom, both
    in the photo's convention v = 0 at the top): dE00 of the MEAN colours per band, and their photo-pixel-weighted
    mean - the lens-colour check of S8 and of the gate (a gradient's top and bottom, a shield's jade top and violet
    bottom are different bands; one pooled median would call a two-colour lens grey)."""
    rows, w = [], []
    for b in range(n):
        lo, hi = b / n, (b + 1) / n
        a = (photo_v >= lo) & (photo_v < hi)
        c = (render_v >= lo) & (render_v < hi)
        if a.sum() < MIN_BIN_PX or c.sum() < MIN_BIN_PX:
            continue
        d = float(delta_e00(linear_to_lab(photo_lin[a].mean(0)), linear_to_lab(render_lin[c].mean(0))))
        rows.append({"band": b, "photo_px": int(a.sum()), "render_px": int(c.sum()), "dE00": round(d, 2),
                     "photo_srgb": np.round(linear_to_srgb(photo_lin[a].mean(0)) * 255).astype(int).tolist(),
                     "render_srgb": np.round(linear_to_srgb(render_lin[c].mean(0)) * 255).astype(int).tolist()})
        w.append(a.sum())
    mean = float(np.average([r["dE00"] for r in rows], weights=w)) if rows else float("inf")
    return {"bands": rows, "mean": round(mean, 2)}


def binned_de00(photo_lin: np.ndarray, photo_theta: np.ndarray, render_lin: np.ndarray, render_theta: np.ndarray,
                bins: list[tuple[float, float]]) -> dict:
    """Per incidence-angle bin: dE00 between the photo's and the render's MEAN lens colour; the pixel-weighted mean
    over the bins (the photo's pixel counts) is the angular colour match (a wrapped shield's jade centre and violet
    sides are different bins)."""
    rows, w = [], []
    for lo, hi in bins:
        a = (photo_theta >= lo) & (photo_theta < hi)
        b = (render_theta >= lo) & (render_theta < hi)
        if a.sum() < FIT_MIN_BIN_PX or b.sum() < FIT_MIN_BIN_PX:
            continue
        d = float(delta_e00(linear_to_lab(photo_lin[a].mean(0)), linear_to_lab(render_lin[b].mean(0))))
        rows.append({"deg": [lo, hi], "photo_px": int(a.sum()), "render_px": int(b.sum()), "dE00": round(d, 2)})
        w.append(a.sum())
    mean = float(np.average([r["dE00"] for r in rows], weights=w)) if rows else float("inf")
    return {"bins": rows, "mean": round(mean, 2)}


def fit_coat(photo_lin: np.ndarray, photo_theta: np.ndarray, rp: dict, lin0: np.ndarray, lin1: np.ndarray,
             unclipped: np.ndarray, bg_lin: np.ndarray, T_uv: np.ndarray, knots: list[float], r_start: np.ndarray,
             rear=None, photo_v: np.ndarray | None = None) -> dict:
    """The coat's reflectance per incidence angle FITTED so the actual AR render of the front lens over the photo's
    backdrop has the photo's colour in every angle bin.

    The runtime's lens is linear per pixel: render = T(v, theta) bg + R(theta) E + X (``LensAppearance``; E the room
    environment the pixel reflects, X what does not depend on the coat, e.g. the rear reflection). Two calibration
    renders fix E and X per pixel: the coat reflecting nothing (R = 0 at every knot) and a neutral coat (R =
    ``FIT_R_NEUTRAL``); T is the descriptor's own, evaluated exactly. The knot values R[k] (per channel) then minimise
    the pixel-weighted squared Lab difference between the photo's mean colour and the predicted render's mean colour per
    ``ANGLE_BIN_DEG`` bin and (with ``photo_v``) per lens-local height band (``LENS_BANDS``: the check S10 reads; the
    descriptor's R depends on the angle only, so a coat that also changes from top to bottom - oakley's jade top and
    violet bottom - is matched as well as its angles allow), the two sets weighted alike, plus ``FIT_SMOOTH`` x the
    step between neighbouring knots (bounded least squares), in two stages. 1: the reflected COLOUR and its STRENGTH,
    R[k] = s[k] (M[k] + c), M the measured table (``r_start``: grid-scaled), s[k] a strength per angle knot
    (0..``FIT_STRENGTH_MAX``) and c one colour shift for every angle (|c| <= ``FIT_COLOUR_SHIFT``): the measurement's
    angular hue (a jade centre turning violet at the sides) kept, its colour error corrected once. 2: each knot's RGB,
    anchored to stage 1 by ``FIT_PRIOR`` and bounded to [0.5, 2] x stage 1 (+0.15) per channel (a channel may rise where
    the others have no headroom left - INVU's blue at the energy cap - but a knot cannot be re-tinted freely against the
    calibration room). Within the physical range
    0 <= R <= min(``CANON_R_MAX``,
    1 - max_v
    T - ``FIT_ENERGY_MARGIN``): the measured transmission is kept exact. The reflected COLOUR is fitted, not only its
    strength: ``front - kappa T`` zeroed a channel wherever the transmission was high in it (oakley's red: a teal
    render of a violet lens), and a channel that cannot rise further (the energy cap) leaves the others free to trade
    lightness for hue in Lab (INVU's sky blue). Returns {R (K,3), descriptor inputs, predicted per-bin fit}."""
    ok = np.isfinite(rp["theta"]) & np.isfinite(rp["v"]) & np.asarray(unclipped, bool)
    theta, v = rp["theta"][ok], rp["v"][ok]
    L0, L1 = lin0[ok], lin1[ok]
    K = len(knots)
    zero = np.zeros((K, 3))
    neutral = np.full((K, 3), FIT_R_NEUTRAL)

    def table(R):
        return [(0.0, R[0])] + [(float(a), R[k]) for k, a in enumerate(knots) if 0.0 < a < 90.0] + [(90.0, np.ones(3))]
    e0 = _appearance(T_uv, zero[0], table(zero), rear).evaluate(v, theta)
    e1 = _appearance(T_uv, neutral[0], table(neutral), rear).evaluate(v, theta)
    dR = e1.reflectance_rgb - e0.reflectance_rgb
    good = dR.min(axis=1) > 0.05
    E = np.zeros_like(L0)
    E[good] = (L1[good] - L0[good] - (e1.transmission_rgb[good] - e0.transmission_rgb[good]) * bg_lin) / dR[good]
    X = L0 - e0.transmission_rgb * bg_lin - e0.reflectance_rgb * E
    theta, v, E, X = theta[good], v[good], E[good], X[good]
    band_sel = []
    if photo_v is not None:
        vr = 1.0 - v                                          # the photo's convention: 0 at the lens top
        for b in range(LENS_BANDS):
            a_ = (photo_v >= b / LENS_BANDS) & (photo_v < (b + 1) / LENS_BANDS)
            c_ = (vr >= b / LENS_BANDS) & (vr < (b + 1) / LENS_BANDS)
            if a_.sum() >= MIN_BIN_PX and c_.sum() >= MIN_BIN_PX:
                band_sel.append((linear_to_lab(photo_lin[a_].mean(0)), float(a_.sum()), c_))
    bins = angle_bins(photo_theta)
    sel_p = [(photo_theta >= lo) & (photo_theta < hi) for lo, hi in bins]
    sel_r = [(theta >= lo) & (theta < hi) for lo, hi in bins]
    use = [k for k in range(len(bins)) if sel_p[k].sum() >= FIT_MIN_BIN_PX and sel_r[k].sum() >= FIT_MIN_BIN_PX]
    if not use:
        return {"ok": False, "reason": "no angle bin with enough photo and render pixels"}
    P_lab = np.array([linear_to_lab(photo_lin[sel_p[k]].mean(0)) for k in use])
    wts = np.sqrt(np.array([sel_p[k].sum() for k in use], float) / sum(sel_p[k].sum() for k in use))
    T_max = np.asarray(T_uv, float).reshape(-1, 3).max(axis=0)
    ub = np.minimum(CANON_R_MAX, 1.0 - T_max - FIT_ENERGY_MARGIN)
    ub = np.maximum(ub, 1e-3)

    def predict(R):
        e = _appearance(T_uv, R[0], table(R), rear).evaluate(v, theta)
        return e.transmission_rgb * bg_lin + e.reflectance_rgb * E + X

    bw = np.sqrt(np.array([n_ for _, n_, _ in band_sel]) / max(sum(n_ for _, n_, _ in band_sel), 1.0))

    M = np.clip(np.asarray(r_start, float).reshape(K, 3), 0.0, None)

    def coat_of(x):
        return np.clip(x[:K, None] * (M + x[K:][None]), 0.0, ub[None])

    def resid(x):
        R_ = coat_of(x)
        pred = np.clip(predict(R_), 0.0, 1.0)
        lab = np.array([linear_to_lab(pred[sel_r[k]].mean(0)) for k in use])
        smooth = FIT_SMOOTH * np.diff(R_, axis=0).ravel() if K > 1 else np.zeros(0)
        bands = [(Pb - linear_to_lab(pred[c_].mean(0))) * w_ for (Pb, _, c_), w_ in zip(band_sel, bw)]
        return np.concatenate([((P_lab - lab) * wts[:, None]).ravel(), np.ravel(bands), smooth])
    x0 = np.r_[np.ones(K), np.zeros(3)]
    lo = np.r_[np.zeros(K), np.full(3, -FIT_COLOUR_SHIFT)]
    hi = np.r_[np.full(K, FIT_STRENGTH_MAX), np.full(3, FIT_COLOUR_SHIFT)]
    sol = optimize.least_squares(resid, x0, bounds=(lo, hi), diff_step=1e-3, max_nfev=400)
    R1 = coat_of(sol.x)
    stage1 = {"strength": np.round(sol.x[:K], 4).tolist(), "colour_shift": np.round(sol.x[K:], 4).tolist(),
              "cost": round(float(sol.cost), 4)}

    def resid2(x):                              # stage 2: each knot's RGB, anchored to the stage-1 coat
        R_ = x.reshape(K, 3)
        return np.concatenate([resid_rgb(R_), FIT_PRIOR * (R_ - R1).ravel()])

    def resid_rgb(R_):
        pred = np.clip(predict(R_), 0.0, 1.0)
        lab = np.array([linear_to_lab(pred[sel_r[k]].mean(0)) for k in use])
        smooth = FIT_SMOOTH * np.diff(R_, axis=0).ravel() if K > 1 else np.zeros(0)
        bands = [(Pb - linear_to_lab(pred[c_].mean(0))) * w_ for (Pb, _, c_), w_ in zip(band_sel, bw)]
        return np.concatenate([((P_lab - lab) * wts[:, None]).ravel(), np.ravel(bands), smooth])
    # stage 2 stays within a factor 2 of stage 1 per channel (plus 0.15 where stage 1 is dark): a channel may rise or
    # fall, not vanish or appear (INVU's violet grazing edge turned salmon when its blue could go to 0)
    lo2 = np.clip(0.5 * R1, 0.0, ub[None] * 0.998)
    hi2 = np.minimum(np.maximum(2.0 * R1, R1 + 0.15), ub[None])
    hi2 = np.maximum(hi2, lo2 + 1e-4)
    sol2 = optimize.least_squares(resid2, np.clip(R1, lo2, hi2).ravel(),
                                  bounds=(lo2.ravel(), hi2.ravel()), diff_step=1e-3, max_nfev=400)
    R = sol2.x.reshape(K, 3)
    pred = np.clip(predict(R), 0.0, 1.0)
    rep = [{"deg": list(bins[k]), "photo_lab": np.round(P_lab[i], 2).tolist(),
            "predicted_dE00": round(float(delta_e00(P_lab[i], linear_to_lab(pred[sel_r[k]].mean(0)))), 2)}
           for i, k in enumerate(use)]
    return {"ok": True, "R": R, "upper_bound": np.round(ub, 4).tolist(), "bins": rep, "cost": round(float(sol.cost), 4),
            "nfev": int(sol.nfev), "pixels": int(len(theta)), "bands_fitted": len(band_sel),
            "stage1": stage1, "stage1_R": np.round(R1, 4).tolist(), "stage2_cost": round(float(sol2.cost), 4)}


def calibrate_canonical(product: str, run: str, meas: dict, ana: dict, coat: dict, out_dir: Path, angle_deg=None,
                        log=print) -> dict:
    """The mirror coat for the actual AR runtime (canonical optics, front view, the photo's own backdrop colour,
    native room lighting): the runtime reflects its dim room environment where the photo reflected a bright white
    studio, so the coat measured on the photo (``coat_angular_table``: R = front - kappa T per incidence angle) is only
    a starting point. Candidates, each RENDERED and scored against the FRONT photo's kept lens pixels (no highlights,
    no see-through structure) over the render's lens pixels with nothing behind them, minus clipped pixels:
    - the measured coat scaled by s on a grid up to the s that puts its largest reflectance at ``CANON_R_MAX``;
    - that coat scaled per channel (a line fit of each channel over the grid, verified by a render);
    - the coat FITTED per incidence angle and channel (``fit_coat``: two calibration renders fix the environment each
      lens pixel reflects, then a bounded least squares over R(angle) matches the photo's colour in every angle bin;
      verified by a render): the measured coat's colour is wrong wherever the transmission is high in a channel
      (``front - kappa T`` zeroed oakley's red: a teal lens for a violet one), and a scale cannot restore a channel it
      has zeroed.
    Score (``score``): ``dE00_bands`` = the rendered lens against the photo band by band over the lens-local height
    (``band_de00``: the lens-colour check S10 reads as ``rendered_dE00``); also reported: ``dE00`` of the pooled
    median colours and ``dE00_bins`` per incidence-angle bin (the fit's own measure). The chosen candidate minimises
    ``dE00_bands``. Clipped highlight blobs are reported against bare glass
    (R(0) 0.04, Schlick) and the photo's own largest highlight, but do not constrain the choice (a room light clips the
    same way on bare glass). Interim M1 procedure; the studio_v1 lighting contract is M3."""
    import tempfile
    from . import export, texture
    out_dir = Path(out_dir)
    fv = meas["views"]["front"]
    s, keep = fv["samples"], fv["filter"]["keep"]
    photo_lin = np.median(s.lin[keep], axis=0)
    bg_lin = np.median(s.bg[keep], axis=0)
    bg_rgb = np.round(linear_to_srgb(bg_lin) * 255.0)
    bg_used = srgb_to_linear(bg_rgb / 255.0)                  # the render's solid backdrop, exactly
    photo_lab = linear_to_lab(photo_lin)
    photo_blob_mm2, _ = blob_areas_mm2(meas["rgb"]["front"], s.region, s.mm_px)
    T_uv = np.asarray(ana["T_uv"], float)
    rear = [MIRROR_REAR_FRACTION] * 3
    parts, origin = _lens_calibration_parts(product, run)
    for n in parts:
        parts[n].pop("UV", None) if n.startswith("lens") else None
    rmax = max([float(np.max(coat["r0"]))] + [float(np.max(v)) for a, v in (coat.get("table") or []) if a < 90.0])
    s_max = CANON_R_MAX / max(rmax, 1e-6)
    grid = [round(s_max * k / CANON_SCALE_STEPS, 4) for k in range(CANON_SCALE_STEPS + 1)]
    tags = {sc: f"s{k}" for k, sc in enumerate(grid)}
    descs = {tags[sc]: scaled_descriptor(T_uv, coat, sc, rear) for sc in grid}
    descs["bare"] = lens_appearance_descriptor(T_uv, np.full(3, UNCOATED_R))
    # the fit's knots: the measured table's angles (or R(0) alone for a coat without angular spread)
    knots = [float(a) for a, _ in (coat.get("table") or []) if 0.0 < float(a) < 90.0] or [0.0]
    photo_theta = None if angle_deg is None else np.asarray(angle_deg, float)
    do_fit = photo_theta is not None and np.isfinite(photo_theta[keep]).sum() >= FIT_MIN_BIN_PX
    if do_fit:
        descs["cal0"] = table_descriptor(T_uv, knots, np.zeros((len(knots), 3)), rear)
        descs["cal1"] = table_descriptor(T_uv, knots, np.full((len(knots), 3), FIT_R_NEUTRAL), rear)
    base_mat = {"cal_frame": {"base_color": [0.05, 0.05, 0.05, 1.0], "metallic": 0.0, "roughness": 0.6},
                "lens": {"base_color": list(np.clip(np.asarray(ana["tint_linear_rgb"], float), 0, 1)) + [1.0],
                         "metallic": 0.0, "roughness": LENS_ROUGHNESS, "transmission": 1.0, "ior": IOR,
                         "double_sided": False, "lens_appearance": descs["bare"]}}
    with tempfile.TemporaryDirectory() as td:
        export.write_glb(parts, base_mat, Path(td) / "base.glb", origin_mm=origin)
        data = (Path(td) / "base.glb").read_bytes()
    ext_key = export.LENS_APPEARANCE_EXTENSION

    def variant(d):
        return texture.glb_patch_materials(data, {"lens": {"ext": {ext_key: {"schema_version": 1, "texcoord": 0,
                                                                                "appearance": d}}}})
    variants = {tag: variant(d) for tag, d in descs.items()}
    t0 = time.time()
    res = texture.ar_render(variants, out_dir, views=({"id": "front", "yaw_degrees": 0},), background_rgb=bg_rgb)
    log(f"[s8 {product}] canonical calibration: {len(variants)} renders in {time.time() - t0:.1f} s ({res.get('harness_status')})")
    bad = [n for n, m in res["models"].items() if m.get("status") != "runtime_compatible" or not m.get("views")]
    val = res.get("validation") or {}         # the whole run: status, errors, snapshot, every variant's front render hashed
    if not res["models"] or bad or set(res["models"]) != set(variants) or not val.get("ok"):
        raise HarnessError(f"harness {res.get('harness_status')} rc {res.get('returncode')}; failed {bad[:4]}; "
                           f"validation {val.get('reasons', ['absent'])[:3]}; {(res.get('stderr_tail') or '')[-300:]}")
    meta = res["models"]["bare"]["views"]["front"]
    prims = texture.glb_primitives(variants["bare"])
    lens_ids = [i for i, p in enumerate(prims) if str(p["node"]).startswith("lens")]
    lab = texture.ar_label(meta, prims)
    lens_px = texture.erode_px(np.isin(lab["first"], lens_ids), 1)
    clear_px = lens_px & (lab["second"] < 0)
    rmm = 1.0 / texture.ar_px_per_mm(meta)
    rp = render_lens_pixels(meta, prims)
    ph_ok = keep & np.isfinite(photo_theta) if do_fit else None
    bins = angle_bins(photo_theta[ph_ok]) if do_fit else []

    def img_of(png):
        return np.asarray(Image.open(png).convert("RGB"))

    def score(im) -> dict:
        blob, _ = blob_areas_mm2(im, lens_px, rmm)
        okp = clear_px & ~(im >= BLOB_CLIP).any(-1)
        med = np.median(srgb_to_linear(im[okp] / 255.0), axis=0) if okp.any() else np.full(3, np.nan)
        d = float(delta_e00(photo_lab, linear_to_lab(med))) if okp.any() else float("inf")
        out = {"dE00": round(d, 2), "largest_blob_mm2": round(blob, 3), "render_median_linear": np.round(med, 4).tolist()}
        px = im[rp["ys"], rp["xs"]]
        okv = ~render_highlight(px) & np.isfinite(rp["v"])
        bd = band_de00(s.lin[keep], s.v[keep], srgb_to_linear(px[okv] / 255.0), 1.0 - rp["v"][okv])
        out["dE00_bands"], out["bands"] = bd["mean"], bd["bands"]
        if do_fit:
            ok = ~(px >= BLOB_CLIP).any(-1) & np.isfinite(rp["theta"])
            b = binned_de00(s.lin[ph_ok], photo_theta[ph_ok], srgb_to_linear(px[ok] / 255.0), rp["theta"][ok], bins)
            out["dE00_bins"] = b["mean"]
            out["bins"] = b["bins"]
        out["objective"] = out["dE00_bands"]
        return out

    def img(name):
        return img_of(res["models"][name]["views"]["front"]["png"])
    bare_blob, _ = blob_areas_mm2(img("bare"), lens_px, rmm)
    allowed = max(photo_blob_mm2, bare_blob)
    rows, chosen = [], None
    for sc in grid:
        row = {"scale": sc, "r_max": round(min(sc * rmax, 0.95), 4), **score(img(tags[sc]))}
        row["blob_vs_allowed"] = round(row["largest_blob_mm2"] / max(allowed, 1e-9), 3)
        rows.append(row)
        if chosen is None or row["objective"] < chosen["objective"] - 1e-9:
            chosen = {**row, "kind": "grid"}
    # per-channel scale (a line fit over the grid scales whose reflectance stays below 0.9) and the per-angle fit, both
    # verified by an actual render
    verify, per_channel, fitted = {}, None, None
    lin_rows = [r for r in rows if np.isfinite(r["render_median_linear"]).all() and r["r_max"] < 0.9]
    s_c = None
    if len(lin_rows) >= 3:
        S = np.array([r["scale"] for r in lin_rows])
        M = np.array([r["render_median_linear"] for r in lin_rows])
        rmax_c = np.array([max([float(coat["r0"][c])] + [float(v[c]) for a, v in (coat.get("table") or []) if a < 90.0])
                           for c in range(3)])
        s_c = np.zeros(3)
        for c in range(3):
            k, b = np.polyfit(S, M[:, c], 1)
            smax_c = CANON_R_MAX / max(rmax_c[c], 1e-6)
            s_c[c] = float(np.clip((photo_lin[c] - b) / k, 0.0, smax_c)) if k > 1e-6 else float(chosen["scale"])
        s_c = np.round(s_c, 4)
        verify["per_channel"] = variant(scaled_descriptor(T_uv, coat, s_c, rear))
    fit = None
    if do_fit:
        im0, im1 = img("cal0"), img("cal1")
        p0, p1 = im0[rp["ys"], rp["xs"]], im1[rp["ys"], rp["xs"]]
        unclipped = ~(p0 >= BLOB_CLIP).any(-1) & ~(p1 >= BLOB_CLIP).any(-1)
        start = np.array([np.asarray(coat["r0"], float)] + [np.asarray(v, float) for a, v in coat["table"] if 0.0 < a < 90.0]
                         if coat.get("table") else [np.asarray(coat["r0"], float)])
        start = start[-len(knots):] * (float(chosen["scale"]) if not isinstance(chosen["scale"], list) else 1.0)
        fit = fit_coat(s.lin[ph_ok], photo_theta[ph_ok], rp, srgb_to_linear(p0 / 255.0), srgb_to_linear(p1 / 255.0),
                       unclipped, bg_used, T_uv, knots, start, rear, photo_v=s.v[ph_ok])
        if fit.get("ok"):
            verify["bin_fit"] = variant(table_descriptor(T_uv, knots, fit["R"], rear))
    if verify:
        ver = texture.ar_render(verify, out_dir / "verify", views=({"id": "front", "yaw_degrees": 0},), background_rgb=bg_rgb)
        vval = ver.get("validation") or {}
        if not vval.get("ok"):
            raise HarnessError(f"verification harness {ver.get('harness_status')} rc {ver.get('returncode')}: "
                               f"{vval.get('reasons', ['absent'])[:3]}")
        for name in verify:
            vm = ((ver["models"].get(name) or {}).get("views") or {}).get("front")
            if vm is None:
                raise HarnessError(f"{name} verification render failed: {ver.get('harness_status')}")
            sc_ = score(img_of(vm["png"]))
            if name == "per_channel":
                per_channel = {"scale_rgb": s_c.tolist(), **sc_, "png": vm["png"]}
                cand = {"scale": s_c.tolist(), **sc_, "kind": "per_channel"}
            else:
                fitted = {"R_knots": np.round(fit["R"], 4).tolist(), "knots_deg": knots, **sc_, "png": vm["png"],
                          "fit": {k: v for k, v in fit.items() if k != "R"}}
                cand = {"scale": None, **sc_, "kind": "bin_fit", "R_knots": np.round(fit["R"], 4).tolist()}
            if cand["objective"] < chosen["objective"] - 1e-9:
                chosen = cand
    if chosen["kind"] == "bin_fit":
        descriptor = table_descriptor(T_uv, knots, np.asarray(chosen["R_knots"], float), rear)
    else:
        descriptor = scaled_descriptor(T_uv, coat, chosen["scale"], rear)
    out = {"ok": True, "method": "canonical LensAppearance; candidates: coat R(angle) x scale (grid to R_max 0.95), per "
                                 "channel scale, R(angle) fitted per incidence bin (fit_coat); every one rendered; "
                                 "choice = min dE00 over lens-local height bands (band_de00)",
           "per_channel": None if per_channel is None else {k: v for k, v in per_channel.items() if k != "png"},
           "bin_fit": None if fitted is None else {k: v for k, v in fitted.items() if k != "png"},
           "fit_failed": None if (fit is None or fit.get("ok")) else fit.get("reason"),
           "measured_r_max": round(rmax, 4), "scale_max": round(s_max, 4),
           "photo_median_linear": np.round(photo_lin, 4).tolist(), "backdrop_rgb": bg_rgb.tolist(),
           "photo_largest_highlight_mm2": round(photo_blob_mm2, 3), "bare_glass_largest_blob_mm2": round(bare_blob, 3),
           "allowed_blob_mm2": round(allowed, 3), "grid": rows, "chosen": chosen,
           "rendered_dE00": chosen["dE00_bands"], "rendered_dE00_pooled": chosen["dE00"],
           "rendered_dE00_bins": chosen.get("dE00_bins"),
           "renders_px_per_mm": round(1.0 / rmm, 3), "lens_px": int(lens_px.sum()), "lens_px_clear_behind": int(clear_px.sum()),
           "_descriptor": descriptor}
    one = min(grid, key=lambda g: abs(g - 1.0))                  # the measured coat as is (s ~ 1)
    after = {"per_channel": (per_channel or {}).get("png"), "bin_fit": (fitted or {}).get("png")}.get(chosen["kind"]) or \
        res["models"][tags[chosen["scale"]]]["views"]["front"]["png"]
    out["_renders"] = {"before": res["models"][tags[one]]["views"]["front"]["png"],
                       "bare": res["models"]["bare"]["views"]["front"]["png"], "after": after}
    keep_png = {Path(p).name for p in out["_renders"].values()}
    for f in list(out_dir.glob("*.png")) + list((out_dir / "verify").glob("*.png")):
        if f.name not in keep_png:
            f.unlink()
    for d_ in (out_dir, out_dir / "verify"):
        for f in list(d_.glob("*card*.json")) + list(d_.glob("contact-sheets.json")):
            f.unlink()
    return out


def lens_render_check(product: str, run: str, meas: dict, ana: dict, la: dict, out_dir: Path, log=print) -> dict:
    """The delivered lens (its canonical descriptor ``la``) rendered in the actual AR runtime, front view, over the front
    photo's own backdrop colour, room lighting - the same render as the mirror calibration - and compared with the
    FRONT photo's kept lens pixels band by band over the lens-local height (``band_de00``): ``rendered_dE00``, the
    lens-colour check S10 turns into a REVIEW rule (a wrong lens colour is never READY). A harness failure raises
    ``HarnessError``."""
    import tempfile
    from . import export, texture
    fv = meas["views"]["front"]
    s, keep = fv["samples"], fv["filter"]["keep"]
    bg_rgb = np.round(linear_to_srgb(np.median(s.bg[keep], axis=0)) * 255.0)
    parts, origin = _lens_calibration_parts(product, run)
    for n in parts:
        parts[n].pop("UV", None) if n.startswith("lens") else None
    base_mat = {"cal_frame": {"base_color": [0.05, 0.05, 0.05, 1.0], "metallic": 0.0, "roughness": 0.6},
                "lens": {"base_color": list(np.clip(np.asarray(ana["tint_linear_rgb"], float), 0, 1)) + [1.0],
                         "metallic": 0.0, "roughness": LENS_ROUGHNESS, "transmission": 1.0, "ior": IOR,
                         "double_sided": False, "lens_appearance": la}}
    with tempfile.TemporaryDirectory() as td:
        export.write_glb(parts, base_mat, Path(td) / "check.glb", origin_mm=origin)
        data = (Path(td) / "check.glb").read_bytes()
    t0 = time.time()
    res = texture.ar_render({"lens_check": data}, out_dir, views=({"id": "front", "yaw_degrees": 0},), background_rgb=bg_rgb)
    val = res.get("validation") or {}
    vm = ((res["models"].get("lens_check") or {}).get("views") or {}).get("front")
    if vm is None or not val.get("ok"):
        raise HarnessError(f"lens check render failed: {res.get('harness_status')} rc {res.get('returncode')}; "
                           f"validation {val.get('reasons', ['absent'])[:3]}")
    log(f"[s8 {product}] lens check render in {time.time() - t0:.1f} s")
    prims = texture.glb_primitives(data)
    rp = render_lens_pixels(vm, prims)
    im = np.asarray(Image.open(vm["png"]).convert("RGB"))
    px = im[rp["ys"], rp["xs"]]
    okv = ~render_highlight(px) & np.isfinite(rp["v"])
    if okv.sum() < MIN_BIN_PX:
        raise HarnessError("lens check render: no clear lens pixels")
    lin = srgb_to_linear(px[okv] / 255.0)
    bd = band_de00(s.lin[keep], s.v[keep], lin, 1.0 - rp["v"][okv])
    pooled = float(delta_e00(linear_to_lab(np.median(s.lin[keep], axis=0)), linear_to_lab(np.median(lin, axis=0))))
    return {"ok": True, "rendered_dE00": bd["mean"], "rendered_dE00_pooled": round(pooled, 2), "bands": bd["bands"],
            "png": vm["png"], "backdrop_rgb": bg_rgb.tolist(),
            "method": "actual AR runtime, front view, the photo's backdrop colour; dE00 of the mean colours per "
                      f"{LENS_BANDS} lens-local height bands, photo-pixel weighted (band_de00)"}


class HarnessError(RuntimeError):
    """The AR harness did not produce a usable render (an external process), as opposed to a code defect."""


def edge_ring_appearance(ring: dict) -> dict:
    """Canonical descriptor of a clear lens's frosted edge band: the photo's transmission T over the band and the
    rest of its dip-minimum ratio as a diffuse (rough) reflection, R = clip(base - T, 0.02, 0.6)."""
    T = float(ring["transmission"])
    base = float(np.asarray(ring["base_color"], float)[:3].mean())
    R = float(np.clip(base - T, 0.02, 0.6))
    return lens_appearance_descriptor(np.full((1, 3), T), np.full(3, R), roughness=EDGE_RING_ROUGHNESS)


def edge_ring_from_photo(meas: dict, product: str, run: str) -> dict:
    """Clear lens: the photo's lens outline is a faint line (the edge scatters). Median luminance profile across
    the S2 outline in the FRONT photo (ratio to the S0 backdrop), on free/rimless points (frame-bounded ones sit
    under a rim), from -2 to +2 mm: dip depth, FWHM and integrated dip. The ring's width is the FWHM, its
    transmission the one that gives the photo's integrated dip over that width, its base colour the dip's
    minimum ratio (a light grey-white), each clamped to the task's ranges."""
    s2, a2 = core.stage_dir(run, product, "s2_front").load()
    s0, _ = core.stage_dir(run, product, "s0_intake").load()
    rgb = meas["rgb"]["front"]
    Y = srgb_to_linear(rgb / 255.0) @ LUMA
    res0 = s0["views"]["front"]
    bgY = backdrop_linear(res0["backdrop_lab_coef"], res0["shape"]) @ LUMA
    mm_px = meas["views"]["front"]["samples"].mm_px
    ts = np.arange(-EDGE_PROFILE_MM, EDGE_PROFILE_MM + 1e-9, 0.05)
    profs = []                                            # per lens: (len(ts), points)
    n_pts = 0
    for i in range(1, len(s2["lenses"]) + 1):
        P = np.asarray(a2[f"lens{i}_poly_front"], float)
        typ = np.asarray(a2.get(f"lens{i}_type", np.zeros(len(P))), int)
        sel = typ >= 1 if (typ >= 1).sum() >= 10 else np.ones(len(P), bool)
        tg = np.roll(P, -1, 0) - np.roll(P, 1, 0)
        tg /= np.maximum(np.linalg.norm(tg, axis=1, keepdims=True), 1e-12)
        nrm = np.c_[tg[:, 1], -tg[:, 0]]
        c = P.mean(0)
        if np.mean(np.einsum("ij,ij->i", nrm, P - c)) < 0:
            nrm = -nrm                                    # outward
        rows = []
        for t in ts:
            q = P[sel] + nrm[sel] * (t / mm_px)
            v = ndimage.map_coordinates(Y, [q[:, 1], q[:, 0]], order=1, mode="nearest")
            bb = ndimage.map_coordinates(bgY, [q[:, 1], q[:, 0]], order=1, mode="nearest")
            rows.append(v / np.maximum(bb, 1e-4))
        profs.append(np.asarray(rows))
        n_pts += int(sel.sum())
    prof = np.median(np.concatenate(profs, axis=1), axis=1)
    outside = np.median(prof[ts >= 1.0]) if (ts >= 1.0).any() else 1.0
    r = prof / max(outside, 1e-6)
    near = np.abs(ts) <= 1.0
    rmin = float(r[near].min())
    depth = max(0.0, 1.0 - rmin)
    half = 1.0 - depth / 2.0
    below = near & (r <= half)
    fwhm = float(ts[below].max() - ts[below].min() + 0.05) if below.any() else 0.0
    integ = float(np.sum(np.clip(1.0 - r[near], 0, None)) * 0.05)
    width = float(np.clip(fwhm, *EDGE_RING_WIDTH_MM))
    trans = float(np.clip(1.0 - integ / max(width, 1e-6), *EDGE_RING_TRANSMISSION))
    base = float(np.clip(rmin, *EDGE_RING_BASE))
    return {"width_mm": round(width, 3), "transmission": round(trans, 3), "roughness": EDGE_RING_ROUGHNESS,
            "base_color": [round(base, 3)] * 3 + [1.0],
            "evidence": {"points": n_pts, "profile_mm": [round(float(t), 2) for t in ts[::4]],
                         "profile_ratio": [round(float(x), 4) for x in r[::4]], "dip_min_ratio": round(rmin, 4),
                         "fwhm_mm": round(fwhm, 3), "integrated_dip_mm": round(integ, 4),
                         "segments": "free/rimless outline points of the S2 polygons (front photo)"},
            "rule": "width = FWHM of the photo's edge dip, transmission = 1 - integrated dip / width, base = dip "
                    f"minimum; clamped to width {EDGE_RING_WIDTH_MM} mm, transmission {EDGE_RING_TRANSMISSION}, "
                    f"base {EDGE_RING_BASE}; roughness {EDGE_RING_ROUGHNESS} (frosted)"}


def calibration_sheet(cal: dict, meas: dict, path: Path, title: str) -> str:
    """photo lens (front) | before (s = 1, no baked reflection) | bare glass | after, with the numbers."""
    from . import texture
    f = _font(15)
    ph = meas["rgb"]["front"]
    reg = meas["views"]["front"]["samples"].region
    tiles = [texture._label(texture._fit(texture._crop_box(ph, reg, 20), 380, 200), "photo front", f)]
    full = min(cal.get("grid", []) or [{}], key=lambda r: abs((r.get("scale") or 0.0) - 1.0))
    ch = cal.get("chosen") or {}
    lab = {"before": f"measured coat (s {full.get('scale')}): blob {full.get('largest_blob_mm2')} mm2, dE bands "
                     f"{full.get('dE00_bands')}",
           "bare": f"bare glass: blob {cal['bare_glass_largest_blob_mm2']} mm2",
           "after": f"chosen {ch.get('kind')}: dE bands {ch.get('dE00_bands')} (angle bins {ch.get('dE00_bins')}, "
                    f"pooled {ch.get('dE00')}), blob {ch.get('largest_blob_mm2')} mm2"}
    for key in ("before", "bare", "after"):
        p = cal.get("_renders", {}).get(key)
        if p and Path(p).exists():
            im = texture._crop_content(np.asarray(Image.open(p).convert("RGB")), 8)
            tiles.append(texture._label(texture._fit(im, 380, 200), lab[key], f))
    body = np.hstack(tiles)
    top = Image.new("RGB", (body.shape[1], 30), "white")
    ImageDraw.Draw(top).text((6, 6), title, fill=(0, 0, 0), font=f)
    Image.fromarray(np.vstack([np.asarray(top), body])).save(path)
    return str(path)


def run(product: str, run: str = "m1", force: bool = False) -> dict:
    sd = core.stage_dir(run, product, STAGE)
    if sd.done() and not force:
        return sd.load()[0]
    t0 = time.time()
    meas = measure(product, run)
    ana = analyse(meas)
    sw = swatches(meas, ana)
    flags = list(ana["flags"])
    s2 = meas["s2"]
    if "front_low_resolution" in s2.get("flags", []) or "low_resolution" in meas["s0"]["views"]["front"]["flags"]:
        flags.append("front_low_resolution")
    arrays = {}
    for stale in ("gradient.png", "mirror_base.png", "mirror_specular.png", "mirror_emissive.png"):
        if (sd.root / stale).exists():
            (sd.root / stale).unlink()
    base_tex, specular, emissive = None, None, None
    calibration, edge_ring = None, None
    if ana["gradient"] is not None:
        g = ana["gradient"]
        png = gradient_png(g["top_linear_rgb"], g["bottom_linear_rgb"], g["profile"])
        Image.fromarray(png).save(sd.root / "gradient.png", optimize=False)
        arrays["gradient_rgb8"] = png
        base_tex = sd.root / "gradient.png"
    la, coat = None, None
    if ana["class"] == "mirror":
        # canonical optics: the coat's R per incidence angle from the front photo, scaled in the actual AR runtime.
        # A harness failure is flagged (a REVIEW rule in S10) and leaves the unscaled measured coat; a code defect
        # raises and fails the stage.
        angle = front_incidence_deg(product, run, meas["views"]["front"]["samples"])
        coat = coat_angular_table(meas, ana, angle)
        try:
            calibration = calibrate_canonical(product, run, meas, ana, coat, sd.root / "ar_fit", angle)
            sc = calibration["chosen"]["scale"]
            la = calibration["_descriptor"]
        except HarnessError as e:
            calibration = {"ok": False, "reason": str(e)}
            flags.append("mirror_calibration_failed")
            sc = 1.0
            la = scaled_descriptor(ana["T_uv"], coat, sc, [MIRROR_REAR_FRACTION] * 3)
        ana["mirror"].update({"coat_angular": {"r0_measured": np.round(coat["r0"], 4).tolist(), "bins": coat["bins"],
                                               "spread_deg": coat["spread_deg"],
                                               "table": None if coat["table"] is None else
                                               [[round(a, 2), np.round(v, 4).tolist()] for a, v in coat["table"]]},
                              "calibrated_scale": sc,
                              "calibrated_kind": (calibration.get("chosen") or {}).get("kind"),
                              "calibrated_R_knots": (calibration.get("chosen") or {}).get("R_knots"),
                              "approximation": "canonical LensAppearance: coat R(angle) measured on the front photo per "
                                               "incidence angle (a 1D table; no position dependence), scaled for the "
                                               "runtime's room environment; studio_v1 fitting is M3"})
        if calibration.get("ok"):
            try:
                calibration["sheet"] = calibration_sheet(calibration, meas, sd.root / "ar_fit" / "sheet.png",
                                                         f"S8 mirror calibration {product} ({run}): actual TryOnRenderer, "
                                                         f"canonical optics, room lighting, photo backdrop colour, front view")
            except (OSError, ValueError, KeyError) as e:
                calibration["sheet_error"] = repr(e)
    # the rendered lens-colour check (S10 REVIEW rule): the mirror calibration's chosen render is that render
    if calibration is not None and calibration.get("ok"):
        colour_check = {"ok": True, "rendered_dE00": calibration["rendered_dE00"],
                        "rendered_dE00_pooled": calibration.get("rendered_dE00_pooled"),
                        "bands": (calibration.get("chosen") or {}).get("bands"), "source": "mirror calibration (chosen)"}
    else:
        try:
            la_check = la if la is not None else lens_appearance_descriptor(ana["T_uv"], np.full(3, UNCOATED_R))
            colour_check = lens_render_check(product, run, meas, ana, la_check, sd.root / "ar_check")
            colour_check.pop("png", None)
        except HarnessError as e:
            colour_check = {"ok": False, "reason": str(e)}
            flags.append("lens_colour_check_failed")
    if ana["class"] == "clear":
        edge_ring = edge_ring_from_photo(meas, product, run)
        edge_ring["lens_appearance"] = edge_ring_appearance(edge_ring)
    name = f"{product}_lens"
    # the glTF material is the flat transmissive FALLBACK (S9 adds the canonical descriptor the runtime uses)
    mat = gltf_material(name, ana["class"], ana["tint_linear_rgb"], None, None, None)
    if la is None:
        la = lens_appearance_descriptor(ana["T_uv"], np.full(3, UNCOATED_R))   # uncoated front: R(0) 0.04, Schlick
    for v, d in meas["views"].items():
        arrays[f"keep_{v}_yx"] = np.stack([d["samples"].ys[d["filter"]["keep"]], d["samples"].xs[d["filter"]["keep"]]], 1).astype(np.int32)
        arrays[f"profile64_{v}"] = ana["profiles"][v]["p64"].astype(np.float32)
        arrays[f"count64_{v}"] = ana["profiles"][v]["c64"].astype(np.int32)
    evidence = {"views": {v: d["stats"] for v, d in meas["views"].items()}, "coat_test": ana["coat"],
                "classification": ana["classification"], "transmission_view": ana["transmission_view"],
                "transmission_scale_kappa": round(ana["kappa_e"], 4),
                "see_through": meas["see_through"], "s3_cameras_sha256": meas["s3_sha"],
                "swatch_dE00": {v: {k: sw[v][k] for k in ("dE00_median", "dE00_max")} for v in sw},
                "notes": meas["notes"]}
    fb = {}
    if "front" in meas["views"] and "back" in meas["views"]:
        f, b = meas["views"]["front"]["stats"], meas["views"]["back"]["stats"]
        if f.get("kept_px") and b.get("kept_px"):
            cf = np.asarray(f["ratio_rgb"]) / max(1e-6, sum(f["ratio_rgb"]))
            cb = np.asarray(b["ratio_rgb"]) / max(1e-6, sum(b["ratio_rgb"]))
            fb = {"chroma_distance": round(float(np.linalg.norm(cf - cb)), 4),
                  "front_minus_back_Y": round(f["ratio_Y"] - b["ratio_Y"], 4)}
    evidence["front_back_probe_style"] = fb
    result = {"stage": STAGE, "product": product, "run": run, "class": ana["class"],
              "tint_linear_rgb": ana["tint_linear_rgb"], "gradient": ana["gradient"], "mirror": ana["mirror"],
              "gltf_material": mat, "lens_appearance": la, "evidence": evidence,
              "mirror_calibration": None if calibration is None else {k: v for k, v in calibration.items()
                                                                       if not k.startswith("_")},
              "edge_ring": edge_ring,
              "lens_colour_check": colour_check,
              "flags": sorted(set(flags)),
              "conventions": {"ratio": "linear-light photo / S0 backdrop model at the same pixel",
                              "v": "lens-local v = (y - top) / (bottom - top) of the S2 lens polygon in front px; 0 = top",
                              "tint_linear_rgb": "transmission: back-view chroma, scaled by kappa <= 1 so the front never falls "
                                                 "below it (both photos are upper bounds on T)",
                              "lens_appearance": "LensAppearance v1 dict = the canonical LENSES_lens_appearance S9 writes; its "
                                                 "knots use v = 0 bottom, 1 top (descriptor convention), NOT the S6 uv "
                                                 "convention; gltf_material is only the flat fallback"},
              "policy": {"erode_mm": ERODE_MM, "grow_mm": GROW_MM, "highlight": [HIGHLIGHT_V, HIGHLIGHT_S],
                         "outlier": [OUTLIER_K, OUTLIER_FLOOR], "uncoated_max": UNCOATED_MAX,
                         "exposure_band_ln": [EXPOSURE_BAND, round(EXPOSURE_BAND_CLIPPED, 4)],
                         "coat_residual": [COAT_RESID_FLOOR, COAT_RESID_REL], "clear": [CLEAR_T, CLEAR_SAT],
                         "gradient": [GRADIENT_RATIO, GRADIENT_MIN_DELTA]}}
    sd.save(result, arrays)
    render_sheet(meas, ana, sw, result, sd.root / "sheet.png")
    print(f"[s8] {product}: {ana['class']} in {time.time() - t0:.1f}s", flush=True)
    return result


# --------------------------------------------------------------------------- sheet
def _font(size: int):
    for name in ("arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _srgb8(lin) -> tuple[int, int, int]:
    return tuple(int(round(c)) for c in linear_to_srgb(np.asarray(lin, float)) * 255)


def _overlay(meas: dict, v: str, width: int, height: int) -> Image.Image:
    d = meas["views"][v]
    s, f = d["samples"], d["filter"]
    rgb = meas["rgb"][v]
    ov = rgb.astype(np.float32).copy()
    region = s.region
    ov[~region] = ov[~region] * 0.55 + 255 * 0.45
    cols = {"structure": (230, 20, 20), "bright": (230, 0, 230), "highlight": (255, 210, 0)}
    excluded = ~f["keep"]
    for key, c in cols.items():
        m = f["masks"][key]
        ov[s.ys[m], s.xs[m]] = np.array(c, np.float32)
    grown_only = excluded & ~(f["masks"]["structure"] | f["masks"]["bright"] | f["masks"]["highlight"])
    ov[s.ys[grown_only], s.xs[grown_only]] = ov[s.ys[grown_only], s.xs[grown_only]] * 0.4 + np.array([0, 160, 255]) * 0.6
    edge = region & ~ndimage.binary_erosion(region)
    ov[grow_px(edge, max(1, rgb.shape[1] // 900))] = (0, 170, 0)
    ov = ov.astype(np.uint8)
    ys, xs = np.nonzero(region)
    pad = int(0.08 * (xs.max() - xs.min() + 1))
    x0, x1 = max(0, xs.min() - pad), min(rgb.shape[1], xs.max() + pad)
    y0, y1 = max(0, ys.min() - pad), min(rgb.shape[0], ys.max() + pad)
    im = Image.fromarray(ov[y0:y1, x0:x1])
    k = min(width / im.width, height / im.height)
    im = im.resize((max(1, int(im.width * k)), max(1, int(im.height * k))),
                   Image.NEAREST if k > 1 else Image.LANCZOS)
    tile = Image.new("RGB", (width, height), (255, 255, 255))
    tile.paste(im, ((width - im.width) // 2, (height - im.height) // 2))
    return tile


def _profile_chart(ana: dict, width: int, height: int) -> Image.Image:
    im = Image.new("RGB", (width, height), (255, 255, 255))
    dr = ImageDraw.Draw(im)
    f = _font(13)
    L, R, T, B = 44, width - 10, 22, height - 30
    dr.rectangle([L, T, R, B], outline=(170, 170, 170))
    series = []
    for v, col in (("front", (40, 90, 220)), ("back", (220, 90, 30))):
        if v in ana["profiles"]:
            series.append((f"{v} photo Y", ana["profiles"][v]["p64"] @ LUMA, col, 2))
    Ty = np.asarray(ana["T_uv"]) @ LUMA if len(ana["T_uv"]) > 1 else np.full(N_BINS, float(ana["T_uv"][0] @ LUMA))
    series.append(("material T Y", Ty, (0, 150, 0), 3))
    if ana.get("R_uv") is not None:
        series.append(("material front Y (T + reflected)", Ty + np.asarray(ana["R_uv"]) @ LUMA, (150, 0, 180), 2))
    ymax = max(0.2, max(float(np.nanmax(s[1])) for s in series if np.isfinite(s[1]).any()) * 1.1)
    for yt in np.linspace(0, ymax, 5):
        yy = B - (B - T) * yt / ymax
        dr.line([L, yy, R, yy], fill=(230, 230, 230))
        dr.text((4, yy - 7), f"{yt:.2f}", fill=(80, 80, 80), font=f)
    for i, (lab, y, col, wdt) in enumerate(series):
        pts = [(L + (R - L) * (k + 0.5) / len(y), B - (B - T) * float(y[k]) / ymax) for k in range(len(y)) if np.isfinite(y[k])]
        if len(pts) > 1:
            dr.line(pts, fill=col, width=wdt)
        dr.text((L + 6, T + 2 + 15 * i), lab, fill=col, font=f)
    dr.text((L, B + 6), "v: lens top  ->  bottom      (linear ratio to backdrop)", fill=(60, 60, 60), font=f)
    return im


def render_sheet(meas: dict, ana: dict, sw: dict, result: dict, path: Path) -> None:
    Wt = 1560
    f_big, f = _font(22), _font(14)
    tiles = [_overlay(meas, v, 500, 330) for v in ("front", "back") if v in meas["views"]]
    top = Image.new("RGB", (Wt, 360), (255, 255, 255))
    dr = ImageDraw.Draw(top)
    for i, t in enumerate(tiles):
        top.paste(t, (10 + i * 510, 26))
        dr.text((12 + i * 510, 4), ("front" if i == 0 else "back") + " : kept pixels (red structure, magenta bright, "
                "yellow highlight, blue grown)", fill=(0, 0, 0), font=f)
    top.paste(_profile_chart(ana, 520, 330), (1030, 26))
    # swatches
    nb = 8
    cw, ch = 118, 44
    rows = []
    for v in ("front", "back"):
        if v in sw:
            rows.append((f"{v} photo", [b and b["photo"] for b in sw[v]["bands"]], None))
            rows.append((f"{v} predicted / backdrop", [b and b["pred"] for b in sw[v]["bands"]],
                         [b and b["dE00"] for b in sw[v]["bands"]]))
    wrow = sw.get("front") or sw.get("back")
    rows.append(("predicted over white", [b and b["white"] for b in wrow["bands"]], None))
    mid = Image.new("RGB", (Wt, 30 + len(rows) * (ch + 6) + 10), (255, 255, 255))
    dm = ImageDraw.Draw(mid)
    dm.text((10, 6), "swatches by vertical band (lens top -> bottom); numbers = dE00 photo vs predicted", fill=(0, 0, 0), font=f)
    for r, (label, cols, des) in enumerate(rows):
        y = 30 + r * (ch + 6)
        dm.text((10, y + 14), label, fill=(0, 0, 0), font=f)
        for b in range(nb):
            x = 230 + b * (cw + 6)
            c = cols[b] if b < len(cols) else None
            if c is None:
                dm.rectangle([x, y, x + cw, y + ch], outline=(200, 200, 200))
                continue
            dm.rectangle([x, y, x + cw, y + ch], fill=_srgb8(c), outline=(120, 120, 120))
            if des and des[b] is not None:
                txt = f"{des[b]:.1f}"
                lum = float(np.asarray(c) @ LUMA)
                dm.text((x + 4, y + 4), txt, fill=(255, 255, 255) if lum < 0.25 else (0, 0, 0), font=f)
    texs = [n for n in ("gradient.png", "mirror_base.png", "mirror_specular.png") if (path.parent / n).exists()]
    for j, n in enumerate(texs):
        png = np.asarray(Image.open(path.parent / n).convert("RGB"))
        strip = Image.fromarray(np.repeat(png, 40, axis=1)).resize((40, len(rows) * (ch + 6) - 6), Image.NEAREST)
        x = 230 + nb * (cw + 6) + 10 + j * 105
        mid.paste(strip, (x, 30))
        dm.text((x + 44, 34), n.replace(".png", "").replace("_", "\n") + "\n1x64\ntop->\nbottom", fill=(0, 0, 0), font=f)
    # text
    ev = result["evidence"]
    c = ev["coat_test"]
    lines = [f"{meas['product']}   class = {result['class'].upper()}"
             + (f"  (base {result['mirror']['base_class']})" if result["mirror"] else ""),
             f"tint (linear, transmission) = {result['tint_linear_rgb']}   material baseColorFactor = "
             f"{result['gltf_material']['pbrMetallicRoughness']['baseColorFactor']}"]
    for v in ("front", "back"):
        if v in ev["views"]:
            s = ev["views"][v]
            fl = s.get("filter", {})
            lines.append(f"{v}: kept {s.get('kept_px')}/{s.get('region_px')} px, ratio {s.get('ratio_rgb')} Y {s.get('ratio_Y')} "
                         f"sat {s.get('saturation')} top/bottom Y {s.get('Y_top20')}/{s.get('Y_bottom20')}  removed: "
                         f"highlight {fl.get('highlight')} bright {fl.get('bright_outlier')} dark {fl.get('dark_outlier')} "
                         f"frame-colour {fl.get('frame_colour')} s3 {fl.get('s3_occluder')}  clipped bg {s.get('backdrop_clipped')}")
    if c.get("testable"):
        lines.append(f"coat test: residual {c['residual_rms']} vs {c['residual_threshold']} (chroma {c['coat_chroma']}); "
                     f"kappa {c['kappa']} ln {c['ln_kappa']} vs band {c['exposure_band_ln']} (lum {c['coat_luminance']}); "
                     f"shift {c['registration_shift_bins']}  -> coat {c['coat']}")
    if result["gradient"]:
        g = result["gradient"]
        lines.append(f"gradient: top {g['top_linear_rgb']} bottom {g['bottom_linear_rgb']} blend rms {g['blend_rms']} "
                     f"bottom/top {ev['classification']['bottom_over_top']}")
    if result["mirror"]:
        m = result["mirror"]
        lines.append(f"mirror (M3 approximation): reflected {m['reflected_linear_rgb']} hue {m['reflected_hue']} "
                     f"exposure kappa {m['exposure_scale_kappa']}")
    lines.append(f"dE00 swatch median: " + ", ".join(f"{v} {d['dE00_median']}" for v, d in ev["swatch_dE00"].items())
                 + f"   see-through: {ev['see_through']}")
    lines.append("flags: " + ", ".join(result["flags"]))
    bot = Image.new("RGB", (Wt, 20 + 20 * len(lines) + 10), (255, 255, 255))
    db = ImageDraw.Draw(bot)
    for i, t in enumerate(lines):
        db.text((10, 8 + 20 * i), t, fill=(0, 0, 0), font=f_big if i == 0 else f)
    sheet = Image.new("RGB", (Wt, top.height + mid.height + bot.height), (255, 255, 255))
    sheet.paste(top, (0, 0))
    sheet.paste(mid, (0, top.height))
    sheet.paste(bot, (0, top.height + mid.height))
    sheet.save(path, optimize=False)


def contact_sheet(run: str = "m1", products=None) -> str:
    tiles = []
    for p in list(products or core.PRODUCTS):
        f = core.stage_dir(run, p, STAGE).root / "sheet.png"
        if f.exists():
            im = Image.open(f).convert("RGB")
            tiles.append(np.asarray(im.resize((1560, int(im.height * 1560 / im.width)))))
    out = core.BSA_DATA / "runs" / run / "s8_contact.png"
    if tiles:
        Image.fromarray(np.vstack(tiles)).save(out, optimize=False)
    return str(out)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="m1")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--product", action="append")
    a = ap.parse_args()
    for p in a.product or list(core.PRODUCTS):
        r = run(p, a.run, a.force)
        ev = r["evidence"]
        print(p, json.dumps({"class": r["class"], "tint": r["tint_linear_rgb"], "flags": r["flags"],
                             "coat": {k: ev["coat_test"].get(k) for k in ("residual_rms", "residual_threshold", "kappa",
                                                                          "ln_kappa", "coat_chroma", "coat_luminance")},
                             "views": {v: {k: ev["views"][v].get(k) for k in ("kept_px", "region_px", "ratio_rgb", "Y_top20",
                                                                              "Y_bottom20")} for v in ev["views"]},
                             "dE00": ev["swatch_dE00"]}), flush=True)
    print(contact_sheet(a.run, a.product))
