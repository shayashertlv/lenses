"""Shelf packing of charts into one atlas (texel units), 8-texel gutters, atlas side <= ``atlas_max``. numpy only.

Ported from the scratch ``g3d-wp5/cov/charts.pack`` (PORT_REVIEW section 10). Changes: deterministic order (height,
then width, then the caller's order), the atlas width is chosen among candidates (the one giving the smallest
max(width, height), then the smallest area), and when even that exceeds ``atlas_max`` the density is lowered by the
square root of the overflow and the layout repacked (``fit_density``).

Texel convention (``texture_utils.rasterize_triangles``): texel (i, j) has its centre at (x, y) = (j + 0.5, i + 0.5);
a chart's uv (mm) maps to (uv - lo) x tx + (x0, y0) + gutter, so every chart texel lies inside its own box and boxes
are separated by at least ``gutter`` empty texels.
"""
from __future__ import annotations

import math

import numpy as np

GUTTER = 8
ATLAS_MAX = 4096
WIDTH_STEP = 64


def chart_boxes(chart_uvs: list[np.ndarray], tx: float) -> list[tuple[np.ndarray, int, int]]:
    """(lo (2,), width, height) in texels of each chart (uv in mm)."""
    out = []
    for uv in chart_uvs:
        p = uv.reshape(-1, 2)
        lo = p.min(0)
        hi = p.max(0)
        out.append((lo, int(math.ceil((hi[0] - lo[0]) * tx)) + 1, int(math.ceil((hi[1] - lo[1]) * tx)) + 1))
    return out


def shelf(boxes: list[tuple[np.ndarray, int, int]], width: int, gutter: int = GUTTER):
    """Shelf packing at a fixed atlas width: (offsets (n, 2) texel x/y of each box's corner, atlas height) or None when
    a box is wider than the atlas."""
    order = sorted(range(len(boxes)), key=lambda i: (-boxes[i][2], -boxes[i][1], i))
    x = y = gutter
    shelf_h = 0
    off = np.zeros((len(boxes), 2), np.int64)
    for i in order:
        w, h = boxes[i][1], boxes[i][2]
        if w + 2 * gutter > width:
            return None
        if x + w + gutter > width:
            x = gutter
            y += shelf_h + gutter
            shelf_h = 0
        off[i] = (x, y)
        x += w + gutter
        shelf_h = max(shelf_h, h)
    return off, int(y + shelf_h + gutter)


def best_width(boxes, atlas_max: int = ATLAS_MAX, gutter: int = GUTTER):
    """(offsets, width, height) with the smallest max(width, height), then area, over candidate widths."""
    wmin = max(b[1] for b in boxes) + 2 * gutter
    area = sum((b[1] + gutter) * (b[2] + gutter) for b in boxes)
    lo = max(wmin, WIDTH_STEP)
    hi = max(lo, min(atlas_max, int(math.ceil(math.sqrt(area) * 2.0))))
    cands = sorted(set([lo] + list(range(int(math.ceil(lo / WIDTH_STEP)) * WIDTH_STEP, hi + 1, WIDTH_STEP))))
    best = None
    for w in cands:
        r = shelf(boxes, w, gutter)
        if r is None:
            continue
        off, h = r
        width = int(w)
        key = (max(width, h), width * h, width)
        if best is None or key < best[0]:
            best = (key, off, width, h)
    if best is None:
        return None
    return best[1], best[2], best[3]


def pack(chart_uvs: list[np.ndarray], tx: float, *, atlas_max: int = ATLAS_MAX, gutter: int = GUTTER) -> dict:
    """Charts (uv in mm) -> {'uv': [texel uv (k, 3, 2)], 'offsets', 'boxes', 'width', 'height', 'tx_per_mm'}; raises
    when the atlas exceeds ``atlas_max`` (use ``fit_density``)."""
    boxes = chart_boxes(chart_uvs, tx)
    r = best_width(boxes, atlas_max, gutter)
    if r is None:
        raise ValueError("a chart is wider than the atlas")
    off, width, height = r
    if width > atlas_max or height > atlas_max:
        raise ValueError(f"atlas {width} x {height} exceeds {atlas_max}")
    uvs = [(uv - boxes[i][0]) * tx + off[i].astype(np.float64) for i, uv in enumerate(chart_uvs)]
    return {"uv": uvs, "offsets": off, "boxes": [(b[1], b[2]) for b in boxes], "width": int(width),
            "height": int(height), "tx_per_mm": float(tx)}


def fit_density(chart_uvs: list[np.ndarray], tx: float, *, atlas_max: int = ATLAS_MAX, gutter: int = GUTTER,
                max_tries: int = 12) -> dict:
    """``pack`` at ``tx``, lowering the density until the atlas fits ``atlas_max`` x ``atlas_max``."""
    t = float(tx)
    for _ in range(max_tries):
        boxes = chart_boxes(chart_uvs, t)
        r = best_width(boxes, atlas_max, gutter)
        if r is not None:
            off, w, h = r
            if w <= atlas_max and h <= atlas_max:
                out = pack(chart_uvs, t, atlas_max=atlas_max, gutter=gutter)
                out["tx_requested"] = float(tx)
                return out
            over = max(w, h) / atlas_max
        else:
            over = 1.25
        t = t / max(math.sqrt(over), 1.02) * 0.99
    raise ValueError(f"cannot fit the charts into {atlas_max} x {atlas_max}")
