"""Foreground matte of a product photo on a plain studio background (make_uv_test.py ``matte()``, WP3b;
glasses3d/plan/archive/20261019/PORT_REVIEW.md section 9; ported from the pre-WP0 prototype, deleted 2026-10-13; tag review-20261013-pre-cleanup).

The background is modelled per channel as a quadratic surface in the normalized image coordinates
(1, x, y, x^2, y^2, xy), least-squares fitted to a ``border``-pixel frame round the image, so a grey gradient or a soft
vignette is background too. Foreground = any channel differs from that surface by more than ``threshold`` (8-bit
levels), then one opening, two closings, hole filling, and components of ``min_component`` px or fewer dropped.

The defaults are the prototype's numbers; with them the result is identical to the prototype's matte (the FT1119
cameras were fitted on it). It assumes the product does not touch the border frame and that the backdrop is smooth;
a clear lens is foreground only where it differs from the backdrop by more than ``threshold`` (it usually does not,
and holes inside the rims are then filled only if closed).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageOps
from scipy import ndimage

BORDER_PX = 10
THRESHOLD = 18           # 8-bit levels, max over the channels
OPEN_ITERS = 1
CLOSE_ITERS = 2
MIN_COMPONENT_PX = 300   # components of this size or smaller are dropped (only when there is more than one)


def load_photo(path: str | Path) -> np.ndarray:
    """8-bit RGB (H, W, 3) in the same EXIF-oriented photo coordinates used by intake and image tools."""
    with Image.open(path) as image:
        upright = ImageOps.exif_transpose(image)
        if 'A' in upright.getbands() or 'transparency' in upright.info:
            rgba = upright.convert('RGBA')
            upright = Image.alpha_composite(Image.new('RGBA', rgba.size, 'white'), rgba)
        return np.asarray(upright.convert("RGB"))


def border_mask(shape: tuple[int, int], border: int = BORDER_PX) -> np.ndarray:
    H, W = shape
    b = np.zeros((H, W), bool)
    b[:border, :] = b[-border:, :] = b[:, :border] = b[:, -border:] = True
    return b


def _background_coefficients(f, border):
    H, W = f.shape[:2]
    b = border_mask((H, W), border)
    yy, xx = np.nonzero(b)
    x, y = xx / W, yy / H
    X = np.column_stack([np.ones_like(x), x, y, x*x, y*y, x*y])
    return [np.linalg.lstsq(X, f[..., c][b], rcond=None)[0] for c in range(f.shape[2])]


def _background_rows(shape, first, last, coefficients):
    H, W = shape[:2]
    yy, xx = np.mgrid[first:last, 0:W]
    x, y = xx / W, yy / H
    X = np.stack([np.ones_like(x), x, y, x*x, y*y, x*y], -1).reshape(-1, 6)
    # Keep the original float64 per-channel dot product, including threshold-boundary rounding.
    return np.stack([X @ coef for coef in coefficients], -1).reshape(last-first, W, -1)


def background_model(img: np.ndarray, border: int = BORDER_PX) -> np.ndarray:
    """Float64 quadratic background without a full-image six-column design matrix."""
    f = np.asarray(img)
    if f.ndim == 2:
        f = f[..., None]
    coefficients = _background_coefficients(f, border)
    bg = np.empty(f.shape, np.float64)
    for first in range(0, f.shape[0], 128):
        last = min(first+128, f.shape[0])
        bg[first:last] = _background_rows(f.shape, first, last, coefficients)
    return bg


MODES = ("default", "glossy_floor", "open_frame", "glossy_open_frame")
# WP6 (plan/archive/20261010/WP6_DESIGN.md §6): the glossy-floor mode, ported from the WP6 scratch matte2.matte3. On a glossy floor the
# reflections and soft shadows sit 12-32 levels off the backdrop while the product is 40+, and the default threshold
# plus unconditional hole filling can close the gap between the temples. Every step below is monotone and
# starts from a subset of the default's mask, so the result can only REMOVE pixels from the default matte.
GLOSSY_THRESHOLD = 36             # 8-bit levels
GLOSSY_HOLE_BG = 18               # a hole whose median difference is at or below this is background-like ...
GLOSSY_HOLE_MAX_FRAC = 0.002      # ... and stays open unless it is smaller than this share of the image
GLOSSY_VLINE_PX = 11              # final opening with a vertical line: removes floor contact slivers thinner than this


def matte(img: np.ndarray, *, border: int = BORDER_PX, threshold: float = THRESHOLD, open_iters: int = OPEN_ITERS,
          close_iters: int = CLOSE_ITERS, min_component: int = MIN_COMPONENT_PX, mode: str = "default") -> np.ndarray:
    """Foreground = differs from a quadratic background fitted to the image border (handles grey gradients).

    mode="default": the WP3b matte (unchanged; WP3b/WP5 numbers stay reproducible).
    mode="open_frame": the same threshold, opening, closing and component filter, without hole filling. For
    rimless/semi-rimless construction an enclosed background patch can be a real opening between mounts or temples;
    enclosure alone is not evidence of product foreground. Camera fitting selects this mode only from its existing
    optional-lens construction evidence when the photo has no explicit mode. No geometry mask is used in the matte.
    mode="glossy_floor" (WP6):
    threshold 36 levels (the `threshold` argument is ignored), the same opening and closings, a hole is filled only
    if its inside is not background-like (median difference > 18) or it is smaller than 0.2 % of the image,
    components <= min_component px dropped, then an opening with an 11 px vertical line (and small components
    dropped again). It only removes pixels relative to the default.
    mode="glossy_open_frame": the same floor removal, without any hole filling; the automatic floor
    alternative for rimless/semi-rimless construction. No model silhouette is painted into either matte."""
    if mode not in MODES:
        raise ValueError(f"unknown matte mode {mode!r}; known: {MODES}")
    f = np.asarray(img)
    if f.ndim == 2:
        f = f[..., None]
    coefficients = _background_coefficients(f, border)
    D = np.empty(f.shape[:2], np.float64)
    for first in range(0, f.shape[0], 128):
        last = min(first+128, f.shape[0])
        section = slice(first, last)
        diff = f[section] - _background_rows(f.shape, first, last, coefficients)
        np.abs(diff, out=diff)
        D[section] = diff.max(2)
    del diff
    if mode in ("glossy_floor", "glossy_open_frame"):
        return _glossy_floor(D, open_iters, close_iters, min_component, fill_holes=mode == 'glossy_floor')
    fg = D > threshold
    fg = ndimage.binary_opening(fg, iterations=open_iters)
    fg = ndimage.binary_closing(fg, iterations=close_iters)
    if mode == "default":
        fg = ndimage.binary_fill_holes(fg)
    lab, n = ndimage.label(fg)
    if n > 1:
        sizes = ndimage.sum(fg, lab, range(1, n + 1))
        fg = np.isin(lab, 1 + np.nonzero(sizes > min_component)[0])
    return fg


def _drop_small(fg: np.ndarray, min_component: int) -> np.ndarray:
    lab, n = ndimage.label(fg)
    if n > 1:
        sizes = ndimage.sum(fg, lab, range(1, n + 1))
        fg = np.isin(lab, 1 + np.nonzero(sizes > min_component)[0])
    return fg


def _glossy_floor(D: np.ndarray, open_iters: int, close_iters: int, min_component: int, *, fill_holes=True) -> np.ndarray:
    fg = D > GLOSSY_THRESHOLD
    fg = ndimage.binary_opening(fg, iterations=open_iters)
    fg = ndimage.binary_closing(fg, iterations=close_iters)
    holes = ndimage.binary_fill_holes(fg) & ~fg
    lab, n = ndimage.label(holes)
    if n and fill_holes:
        idx = np.arange(1, n + 1)
        area = ndimage.sum(holes, lab, idx)
        med = ndimage.median(D, lab, idx)
        fill = (med > GLOSSY_HOLE_BG) | (area < GLOSSY_HOLE_MAX_FRAC * fg.size)
        fg = fg | np.isin(lab, idx[fill])
    fg = _drop_small(fg, min_component)
    fg = ndimage.binary_opening(fg, structure=np.ones((GLOSSY_VLINE_PX, 1), bool))
    return _drop_small(fg, min_component)
