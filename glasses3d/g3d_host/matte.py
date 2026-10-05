"""Foreground matte of a product photo on a plain studio background (ported from the WP0 prototype
plan/prototypes/projection/make_uv_test.py ``matte()``, WP3b; plan/PORT_REVIEW.md section 9).

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
from PIL import Image
from scipy import ndimage

BORDER_PX = 10
THRESHOLD = 18           # 8-bit levels, max over the channels
OPEN_ITERS = 1
CLOSE_ITERS = 2
MIN_COMPONENT_PX = 300   # components of this size or smaller are dropped (only when there is more than one)


def load_photo(path: str | Path) -> np.ndarray:
    """8-bit RGB (H, W, 3) of an image file."""
    return np.asarray(Image.open(path).convert("RGB"))


def border_mask(shape: tuple[int, int], border: int = BORDER_PX) -> np.ndarray:
    H, W = shape
    b = np.zeros((H, W), bool)
    b[:border, :] = b[-border:, :] = b[:, :border] = b[:, -border:] = True
    return b


def background_model(img: np.ndarray, border: int = BORDER_PX) -> np.ndarray:
    """Per-channel quadratic background (H, W, C) float64 fitted to the ``border`` frame."""
    f = img.astype(float)
    if f.ndim == 2:
        f = f[..., None]
    H, W = f.shape[:2]
    b = border_mask((H, W), border)
    yy, xx = np.mgrid[0:H, 0:W]
    x, y = xx / W, yy / H
    X = np.stack([np.ones_like(x), x, y, x * x, y * y, x * y], -1).reshape(-1, 6)
    bg = np.zeros_like(f)
    for c in range(f.shape[2]):
        coef, *_ = np.linalg.lstsq(X[b.ravel()], f[..., c][b], rcond=None)
        bg[..., c] = (X @ coef).reshape(H, W)
    return bg


def matte(img: np.ndarray, *, border: int = BORDER_PX, threshold: float = THRESHOLD, open_iters: int = OPEN_ITERS,
          close_iters: int = CLOSE_ITERS, min_component: int = MIN_COMPONENT_PX) -> np.ndarray:
    """Foreground = differs from a quadratic background fitted to the image border (handles grey gradients)."""
    f = img.astype(float)
    if f.ndim == 2:
        f = f[..., None]
    bg = background_model(f, border)
    fg = np.abs(f - bg).max(2) > threshold
    fg = ndimage.binary_opening(fg, iterations=open_iters)
    fg = ndimage.binary_closing(fg, iterations=close_iters)
    fg = ndimage.binary_fill_holes(fg)
    lab, n = ndimage.label(fg)
    if n > 1:
        sizes = ndimage.sum(fg, lab, range(1, n + 1))
        fg = np.isin(lab, 1 + np.nonzero(sizes > min_component)[0])
    return fg
