"""Photo-estimated, camera-relative environment (WP6_FACETS §3.2 amended 2026-10-11: rig ``photo_env``), host side.

Product photos are catalogue shots: the object on a white glossy floor, a white sweep, the lights moving with the
camera while a turntable turns the product. So the environment is camera-relative: elevation e from the scene's up
axis, azimuth a from the camera's horizontal direction (g3d/render.py ENV_SPEC / env_azimuth). It is a sum of smooth
lobes (LOBES, the same layout as g3d/render.py env_lobes):

- 6 elevation tents (linear hats on ELEVATION_KNOTS_DEG) x 3 azimuth sectors, S_front = max(cos a, 0),
  S_side = 1 - |cos a| (left = right), S_back = max(-cos a, 0) (a partition of unity, so equal weights = a uniform
  world);
- one floor term: 1 below FLOOR_DEG[0], falling to 0 at FLOOR_DEG[1] (the sharp horizon of a floor).

Fit (``fit_env``): non-negative least squares of the white-balanced LINEAR photo luminance against the basis
renders, on front acetate body pixels only (eroded >= 12 px at the photo's native scale, inside the matte and the
render's full alpha), Gaussian-blurred within the mask and sampled every ~1 mm (``cell_samples``) so ~1 mm of
registration error does not matter, with a first-difference smoothness penalty (``smoothness``) whose strength is
chosen by leave-one-photo-out cross-validation on the calibration photos only (``cv_lambda``; a declared grid).

EEVEE's world diffuse lighting is homogeneous but NOT additive in the world radiance for strongly one-sided worlds
(a single lobe alone: median 5.8 % error on a diffuse sphere, 2026-10-10 probe), while the change caused by adding a
lobe to a balanced world is additive (0.35 % median). So the basis is differential, B_i = (f(w0 + delta e_i) -
f(w0)) / delta around the current estimate w0, and the fit is re-linearised (``gauss_newton_rhs``: f(w) ~ f(w0) +
B (w - w0)); each iterate is checked by rendering it directly. g3d_host never renders: agent/seeing.py drives the
worker and calls these functions; the frozen environment is ``work/compare/env-<basis>.json``.
"""
from __future__ import annotations

import hashlib
import json
import math

import numpy as np
from scipy import ndimage, optimize

SCHEMA = "g3d.photo_env/1"
ELEVATION_KNOTS_DEG = (-90.0, -35.0, -12.0, 12.0, 35.0, 90.0)       # = g3d/render.py ENV_SPEC
SECTORS = ("front", "side", "back")
FLOOR_DEG = (-1.0, 0.0)
LOBES = [f"{s}{k:+g}" for s in SECTORS for k in ELEVATION_KNOTS_DEG] + ["floor"]
N_LOBES = len(LOBES)
LAMBDA_GRID = (1e-4, 1e-3, 1e-2, 1e-1, 1.0)
LUMA = np.array([0.2126, 0.7152, 0.0722])


# ------------------------------------------------------------------------------------------------ lobes
def tents(e_deg, knots=ELEVATION_KNOTS_DEG) -> np.ndarray:
    """(..., K) linear hat functions of elevation (deg) on the knots (a partition of unity on [-90, 90])."""
    e = np.clip(np.asarray(e_deg, float), knots[0], knots[-1])
    out = np.zeros(e.shape + (len(knots),))
    for k, ek in enumerate(knots):
        if k > 0:
            lo = knots[k - 1]
            sel = (e >= lo) & (e <= ek)
            out[..., k] = np.where(sel, (e - lo) / (ek - lo), out[..., k])
        if k < len(knots) - 1:
            hi = knots[k + 1]
            sel = (e >= ek) & (e <= hi)
            out[..., k] = np.where(sel, (hi - e) / (hi - ek), out[..., k])
    return out


def sector_weights(cos_az) -> np.ndarray:
    """(..., 3) front / side / back weights of the cosine of the azimuth from the camera."""
    c = np.clip(np.asarray(cos_az, float), -1.0, 1.0)
    return np.stack([np.maximum(c, 0.0), 1.0 - np.abs(c), np.maximum(-c, 0.0)], axis=-1)


def floor_term(e_deg) -> np.ndarray:
    f0, f1 = FLOOR_DEG
    return np.clip((f1 - np.asarray(e_deg, float)) / (f1 - f0), 0.0, 1.0)


def lobe_matrix(e_deg, cos_az) -> np.ndarray:
    """(..., N_LOBES) each lobe's radiance at unit weight in the direction (elevation deg, cos azimuth)."""
    T, S = tents(e_deg), sector_weights(cos_az)
    out = (S[..., :, None] * T[..., None, :]).reshape(T.shape[:-1] + (len(SECTORS) * len(ELEVATION_KNOTS_DEG),))
    return np.concatenate([out, floor_term(e_deg)[..., None]], axis=-1)


def radiance(weights, e_deg, cos_az) -> np.ndarray:
    return lobe_matrix(e_deg, cos_az) @ np.asarray(weights, float)


def env_image(weights, width: int = 360, height: int = 180) -> np.ndarray:
    """Equirect preview (camera-relative): column = azimuth -180..180 (0 = toward the camera, centre), row =
    elevation 90..-90."""
    az = np.linspace(-180.0, 180.0, width, endpoint=False) + 180.0 / width
    el = np.linspace(90.0, -90.0, height, endpoint=False) - 90.0 / height
    E, A = np.meshgrid(el, az, indexing="ij")
    return radiance(weights, E, np.cos(np.radians(A)))


def smoothness(knots=ELEVATION_KNOTS_DEG, sectors=SECTORS) -> np.ndarray:
    """First differences between neighbouring elevation knots within a sector and neighbouring sectors (front-side,
    side-back) at one knot; the floor term is not tied."""
    K, rows = len(knots), []
    for s in range(len(sectors)):
        for k in range(K - 1):
            r = np.zeros(N_LOBES)
            r[s * K + k], r[s * K + k + 1] = 1.0, -1.0
            rows.append(r)
    for s in range(len(sectors) - 1):
        for k in range(K):
            r = np.zeros(N_LOBES)
            r[s * K + k], r[(s + 1) * K + k] = 1.0, -1.0
            rows.append(r)
    return np.array(rows)


# ------------------------------------------------------------------------------------------------ samples
def cell_samples(images: list, mask: np.ndarray, sigma_px: float, step_px: float) -> tuple[np.ndarray, list]:
    """Gaussian-blur each 2-D image within ``mask`` (normalised convolution, so pixels outside never leak in) and
    sample it on a grid every ``step_px`` at pixels inside the mask: ((rows, 2) sample pixels (v, u), [values])."""
    m = np.asarray(mask, bool)
    if not m.any():
        return np.zeros((0, 2), int), [np.zeros(0) for _ in images]
    w = ndimage.gaussian_filter(m.astype(np.float64), sigma_px)
    step = max(1, int(round(step_px)))
    vs, us = np.mgrid[step // 2:m.shape[0]:step, step // 2:m.shape[1]:step]
    keep = m[vs, us]
    pts = np.column_stack([vs[keep], us[keep]])
    vals = []
    for img in images:
        b = ndimage.gaussian_filter(np.where(m, np.asarray(img, np.float64), 0.0), sigma_px)
        vals.append(b[pts[:, 0], pts[:, 1]] / np.maximum(w[pts[:, 0], pts[:, 1]], 1e-9))
    return pts, vals


# ------------------------------------------------------------------------------------------------ fit
def nnls_smooth(A: np.ndarray, b: np.ndarray, lam: float, D: np.ndarray | None = None) -> np.ndarray:
    """argmin_w>=0 ||A w - b||^2 + lam * s * ||D w||^2, s = mean squared column norm of A (lam is dimensionless)."""
    A, b = np.asarray(A, float), np.asarray(b, float)
    D = smoothness() if D is None else D
    s = float((A ** 2).sum() / max(1, A.shape[1]))
    M = np.vstack([A, math.sqrt(max(lam, 0.0) * s) * D])
    y = np.concatenate([b, np.zeros(len(D))])
    w, _r = optimize.nnls(M, y, maxiter=50 * M.shape[1])
    return w


def _weighted(A, b, groups):
    """Rows scaled by 1 / sqrt(rows of their group), so every photo weighs the same."""
    groups = np.asarray(groups)
    scale = np.ones(len(b))
    for g in np.unique(groups):
        sel = groups == g
        scale[sel] = 1.0 / math.sqrt(sel.sum())
    return A * scale[:, None], b * scale


def group_residuals(A, b, w, groups) -> dict:
    """{group: {rows, rms, rel_rms (rms / median target), median_abs}} of A w - b."""
    return residual_stats(np.asarray(A, float) @ np.asarray(w, float), b, groups)


def residual_stats(pred, b, groups) -> dict:
    """{group: {rows, rms, rel_rms (rms / median target), median_abs}} of pred - b."""
    groups, b = np.asarray(groups), np.asarray(b, float)
    r = np.asarray(pred, float) - b
    out = {}
    for g in np.unique(groups):
        sel = groups == g
        med = float(np.median(np.abs(b[sel]))) or 1.0
        out[str(g)] = {"rows": int(sel.sum()), "rms": float(np.sqrt(np.mean(r[sel] ** 2))),
                       "rel_rms": float(np.sqrt(np.mean(r[sel] ** 2)) / med),
                       "median_abs": float(np.median(np.abs(r[sel])))}
    return out


def cv_lambda(A, b, groups, grid=LAMBDA_GRID, D=None) -> tuple[float, list]:
    """Leave-one-group-out cross-validation of the smoothness strength: (best lambda, table [{lambda, cv_rel_rms,
    per group}]). The error of a lambda is the mean over held-out groups of their relative RMS; ties go to the larger
    (smoother) lambda. Needs >= 2 groups (else the middle of the grid is returned with an empty table)."""
    groups = np.asarray(groups)
    gs = list(np.unique(groups))
    if len(gs) < 2:
        return float(grid[len(grid) // 2]), []
    table = []
    for lam in grid:
        per = {}
        for g in gs:
            tr = groups != g
            Aw, bw = _weighted(A[tr], b[tr], groups[tr])
            w = nnls_smooth(Aw, bw, lam, D)
            per[str(g)] = group_residuals(A[~tr], b[~tr], w, groups[~tr])[str(g)]["rel_rms"]
        table.append({"lambda": lam, "cv_rel_rms": float(np.mean(list(per.values()))), "held_out": per})
    best = min(table, key=lambda t: (round(t["cv_rel_rms"], 6), -t["lambda"]))
    return float(best["lambda"]), table


def fit_env(A, b, groups, lam: float, D=None) -> dict:
    """{weights, lambda, residuals per group} of the photo-balanced, smoothness-penalised NNLS."""
    A, b = np.asarray(A, float), np.asarray(b, float)
    Aw, bw = _weighted(A, b, groups)
    w = nnls_smooth(Aw, bw, lam, D)
    return {"weights": [float(x) for x in w], "lambda": float(lam), "residuals": group_residuals(A, b, w, groups)}


def gauss_newton_rhs(y, f0, B, w0) -> np.ndarray:
    """The right-hand side of the re-linearised fit: f(w) ~ f(w0) + B (w - w0) = y  ->  B w = y - f(w0) + B w0."""
    return np.asarray(y, float) - np.asarray(f0, float) + np.asarray(B, float) @ np.asarray(w0, float)


# ------------------------------------------------------------------------------------------------ white balance
def chroma(gain_rgb) -> np.ndarray:
    """A per-channel gain normalised to unit luminance (its hue part)."""
    g = np.asarray(gain_rgb, float)
    return g / float(g @ LUMA)


def balanced_luminance(photo_lin: np.ndarray, chroma_rgb) -> np.ndarray:
    """Luminance of the linear photo divided by the white balance's hue (the target the grey environment fits)."""
    return (np.asarray(photo_lin, float)[..., :3] / np.asarray(chroma_rgb, float)) @ LUMA


# ------------------------------------------------------------------------------------------------ record
def weights_sha(weights) -> str:
    return hashlib.sha256(json.dumps([round(float(x), 9) for x in weights]).encode()).hexdigest()[:16]


def env_param(rec: dict) -> dict:
    """The render parameter of a frozen environment record."""
    return {"weights": [float(x) for x in rec["weights"]]}
