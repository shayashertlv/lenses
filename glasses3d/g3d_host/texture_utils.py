"""Texture helpers for photo projection (WP5), ported verbatim from automation/bsa/texture.py (WP3b;
plan/PORT_REVIEW.md section 9): ``de00`` (:176), ``quantile_gain`` (:185), ``rasterize_triangles`` (:237),
``nearest_fill`` (:270), ``sample_bilinear`` (:383), ``visible_points`` (:1744), plus the colour conversions they need
(``srgb_to_linear``, ``linear_to_srgb``, ``lab_of``) and ``apply_gain``, the inverse partner of ``quantile_gain``.

Conventions: colours are sRGB 0..255; native image coordinates have integer = pixel CENTRE (the camera convention);
texel coordinates in ``rasterize_triangles`` have texel (i, j) centre at (j + 0.5, i + 0.5).
"""
from __future__ import annotations

import numpy as np
from scipy import ndimage
from skimage.color import deltaE_ciede2000, rgb2lab

from . import raster, pose
from .camera import Camera, NormFrame, project_mm

GAIN_BOUNDS = (0.25, 4.0)
GAIN_QUANTILES = np.arange(10, 91, 10)
GAIN_MIN_PAIRS = 200
VIS_MIN_COS = 0.15       # bsa WALL_MIN_COS: a surface point must face the camera by more than this ...
VIS_TOL_MM = 0.5         # bsa WALL_VIS_TOL_MM: ... and be the first surface on its ray within this


def srgb_to_linear(c) -> np.ndarray:
    c = np.asarray(c, np.float64) / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(c) -> np.ndarray:
    c = np.clip(np.asarray(c, np.float64), 0.0, 1.0)
    return 255.0 * np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055)


def lab_of(rgb) -> np.ndarray:
    """CIELAB (D65) of sRGB 0..255 values, any leading shape."""
    a = np.asarray(rgb, np.float64)
    return rgb2lab((a.reshape(-1, 1, 3) / 255.0)).reshape(a.shape)


def de00(rgb_a, rgb_b) -> np.ndarray:
    """CIEDE2000 colour difference of sRGB 0..255 colours (elementwise over the leading shape)."""
    return deltaE_ciede2000(lab_of(rgb_a), lab_of(rgb_b))


def quantile_gain(gen_rgb: np.ndarray, photo_rgb: np.ndarray, bounds=GAIN_BOUNDS) -> tuple[np.ndarray, np.ndarray, dict]:
    """Per-channel linear-light photo = gain * gen + offset, MEDIAN-ANCHORED (gain * gen_q50 + offset =
    photo_q50 exactly) with a NON-NEGATIVE offset; the one free parameter (the offset) is fitted to the
    matched 10..90 % quantiles. Why constrained: the photo's spread is lighting (sheen, speculars, shading)
    as much as albedo; an unconstrained fit then drives the gain to its bound with a large negative offset
    that maps darker colours to black. With offset >= 0 the map is gain-only unless the photo's black level
    is lifted, and it never produces negative light. ``gen_rgb`` and ``photo_rgb`` are matched pairs (same
    surface points); fewer than ``GAIN_MIN_PAIRS`` pairs return the identity with ``info['skipped']``."""
    g_lin = srgb_to_linear(gen_rgb).reshape(-1, 3)
    p_lin = srgb_to_linear(photo_rgb).reshape(-1, 3)
    gain, off, info = np.ones(3), np.zeros(3), {"pairs": int(len(g_lin)), "channels": [],
                                                "model": "median-anchored gain + offset >= 0, quantiles 10..90 %",
                                                "gain_bounds": list(bounds)}
    if len(g_lin) < GAIN_MIN_PAIRS:
        info["skipped"] = "too_few_pairs"
        return gain, off, info
    for c in range(3):
        qg = np.percentile(g_lin[:, c], GAIN_QUANTILES)
        qp = np.percentile(p_lin[:, c], GAIN_QUANTILES)
        mg, mp = float(np.median(g_lin[:, c])), float(np.median(p_lin[:, c]))
        if mg < 1e-6:
            g, o = 1.0, max(mp - mg, 0.0)
        else:
            # offset o in [0, mp): gain(o) = (mp - o) / mg, least squares on the central quantile pairs
            dq = qg - mg
            den = float(np.sum(dq * dq))
            if den < 1e-12:
                o = 0.0
            else:
                # residual r(o) = qp - (mp - o) (qg / mg) - o = (qp - mp qg/mg) + o (qg/mg - 1); linear in o
                a = qg / mg - 1.0
                bq = qp - mp * qg / mg
                o = float(-np.sum(a * bq) / max(np.sum(a * a), 1e-12))
            o = float(np.clip(o, 0.0, max(mp - bounds[0] * mg, 0.0)))     # keeps gain >= the lower bound
            g = (mp - o) / mg
            if not (bounds[0] <= g <= bounds[1]):
                g = float(np.clip(g, *bounds))
                o = max(mp - g * mg, 0.0)
        gain[c], off[c] = g, o
        info["channels"].append({"gen_q10_q50_q90": [round(float(x), 5) for x in (qg[0], mg, qg[-1])],
                                 "photo_q10_q50_q90": [round(float(x), 5) for x in (qp[0], mp, qp[-1])],
                                 "gain": round(float(g), 4), "offset": round(float(o), 5)})
    return gain, off, info


def apply_gain(rgb, gain, offset) -> np.ndarray:
    """sRGB 0..255 (float or uint8) -> gained sRGB float 0..255 (linear-light affine per channel)."""
    lin = srgb_to_linear(rgb) * np.asarray(gain) + np.asarray(offset)
    return linear_to_srgb(lin)


def rasterize_triangles(tri: np.ndarray, shape: tuple[int, int]) -> tuple[np.ndarray, np.ndarray]:
    """Texel-centre rasterisation of triangles given in TEXEL coordinates (texel (i, j) centre = (j+.5, i+.5)).
    Returns face id (H,W) int32 (-1 empty; later triangles win) and barycentrics (H,W,3) float32."""
    H, W = shape
    fid = np.full((H, W), -1, np.int32)
    bary = np.zeros((H, W, 3), np.float32)
    tri = np.asarray(tri, np.float64)
    lo = np.floor(tri.min(1) - 0.5).astype(int)
    hi = np.ceil(tri.max(1) - 0.5).astype(int)
    for k in range(len(tri)):
        x0, y0 = max(lo[k, 0], 0), max(lo[k, 1], 0)
        x1, y1 = min(hi[k, 0], W - 1), min(hi[k, 1], H - 1)
        if x1 < x0 or y1 < y0:
            continue
        (ax, ay), (bx, by), (cx, cy) = tri[k]
        den = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
        if abs(den) < 1e-12:
            continue
        xs = np.arange(x0, x1 + 1) + 0.5
        ys = np.arange(y0, y1 + 1) + 0.5
        X, Y = np.meshgrid(xs, ys)
        l0 = ((by - cy) * (X - cx) + (cx - bx) * (Y - cy)) / den
        l1 = ((cy - ay) * (X - cx) + (ax - cx) * (Y - cy)) / den
        l2 = 1.0 - l0 - l1
        m = (l0 >= -1e-9) & (l1 >= -1e-9) & (l2 >= -1e-9)
        if not m.any():
            continue
        sub = (slice(y0, y1 + 1), slice(x0, x1 + 1))
        fid[sub][m] = k
        bary[sub][m] = np.stack([l0[m], l1[m], l2[m]], -1)
    return fid, bary


def nearest_fill(img: np.ndarray, valid: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Every pixel takes the value of its nearest valid pixel; also returns that distance (px)."""
    if not valid.any():
        return img.copy(), np.full(valid.shape, np.inf)
    dist, (iy, ix) = ndimage.distance_transform_edt(~valid, return_indices=True)
    return img[iy, ix], dist


def sample_bilinear(img: np.ndarray, u: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Bilinear sample at native pixel coordinates (integer = pixel centre), clamped at the border.
    Returns (K, C) float32 (C = 1 for a 2D image). The image must be at least 2 x 2."""
    H, W = img.shape[:2]
    u = np.clip(np.asarray(u, float), 0, W - 1)
    v = np.clip(np.asarray(v, float), 0, H - 1)
    x0 = np.minimum(np.floor(u).astype(int), W - 2)
    y0 = np.minimum(np.floor(v).astype(int), H - 2)
    fx, fy = (u - x0)[:, None], (v - y0)[:, None]
    t = img.reshape(H, W, -1).astype(np.float32)
    return (t[y0, x0] * (1 - fx) * (1 - fy) + t[y0, x0 + 1] * fx * (1 - fy)
            + t[y0 + 1, x0] * (1 - fx) * fy + t[y0 + 1, x0 + 1] * fx * fy)


def viewing_direction(P: np.ndarray, cam: Camera, frame: NormFrame) -> np.ndarray:
    """Unit direction to the physical eye for off-axis cameras; the legacy axial approximation otherwise.

    Returns (K, 3) for an off-axis camera and the historical (3,) vector when both eye offsets are zero.
    Keeping that branch preserves existing print donor decisions for frozen legacy calibrations.
    """
    if cam.eye_x == 0 and cam.eye_y == 0:
        return raster.camera_basis(cam)[2]
    toward = pose.toward_camera(np.asarray(P, float).reshape(-1, 3), cam, frame)
    return toward / np.maximum(np.linalg.norm(toward, axis=-1, keepdims=True), 1e-12)


def viewing_cosine(P: np.ndarray, N: np.ndarray, cam: Camera, frame: NormFrame) -> np.ndarray:
    toward = viewing_direction(P, cam, frame)
    normals = np.asarray(N, float)
    return normals @ toward if toward.ndim == 1 else np.sum(normals * toward, axis=1)


def visible_points(P: np.ndarray, N: np.ndarray, cam: Camera, frame: NormFrame, scene, shape: tuple[int, int],
                   min_cos: float = VIS_MIN_COS, tol_mm: float = VIS_TOL_MM) -> tuple[np.ndarray, np.ndarray]:
    """Surface points (model mm, outward unit normals ``N``) the camera sees DIRECTLY: inside the image, turned toward
    the camera (cos > ``min_cos``) and the first surface of ``scene`` (a ``raster.RasterScene``) on their ray (within
    ``tol_mm``). Returns (visible, projected native px)."""
    P = np.asarray(P, float).reshape(-1, 3)
    uv = project_mm(P, cam, frame)
    if not len(P):
        return np.zeros(0, bool), uv
    H, W = shape
    toward = raster.camera_basis(cam)[2]
    inb = (uv[:, 0] > -0.5) & (uv[:, 0] < W - 0.5) & (uv[:, 1] > -0.5) & (uv[:, 1] < H - 0.5)
    d = -(frame.to_norm(P) @ toward) * frame.extent
    hit = scene.cast(cam, uv[:, 0], uv[:, 1])
    first = ~hit["hit"] | (hit["depth"] >= d - tol_mm)
    return inb & (viewing_cosine(P, N, cam, frame) > min_cos) & first, uv
