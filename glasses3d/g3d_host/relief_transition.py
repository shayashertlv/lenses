"""Opt-in surface-copy tools for a tangent transition from a facet into a plain arm.

No fitter, proof, spec parser or production exporter imports this module. The patch
is a cubic Hermite strip: it retains the facet boundary positions and its tangent,
returns to the original surface at the upper boundary, and fades out before a
specified protected distal section. Coordinates are local (d, y, X), in mm.
"""
from __future__ import annotations

import numpy as np


def tangent_strip(points, *, lower, upper, top, slope, start, fade_start, end):
    """Return X displacement and its (d, y) gradient, without editing inputs.

    lower/upper are (intercept, slope) of the strip's straight y(d) boundaries.
    The upper boundary is clipped to the supplied piecewise-linear top outline.
    At the lower boundary before fade_start, the gradient equals that of the
    plane ``slope * (y - lower(d))``. At the upper boundary it is zero. The
    original surface is recovered with zero displacement/gradient at ``end``.
    The meeting point at ``start`` can remain a genuine multi-facet corner.
    """
    q = np.asarray(points, dtype=float)
    lo, hi, chain = np.asarray(lower, float), np.asarray(upper, float), np.asarray(top, float)
    if q.ndim != 2 or q.shape[1] != 2 or not np.isfinite(q).all():
        raise ValueError('points must be finite (n, 2) coordinates')
    if lo.shape != (2,) or hi.shape != (2,) or not np.isfinite(np.r_[lo, hi]).all():
        raise ValueError('boundary lines must be finite intercept/slope pairs')
    if (chain.ndim != 2 or chain.shape[1] != 2 or len(chain) < 2
            or not np.isfinite(chain).all() or np.any(np.diff(chain[:, 0]) <= 0)):
        raise ValueError('top must have finite, strictly increasing stations')
    if not np.isfinite([slope, start, fade_start, end]).all() or not start < fade_start < end:
        raise ValueError('require finite start < fade_start < end')
    if chain[0, 0] > start or chain[-1, 0] < end:
        raise ValueError('top must cover the complete transition')
    d, y = q.T
    idx = np.clip(np.searchsorted(chain[:, 0], d, side='right') - 1, 0, len(chain) - 2)
    top_y = np.interp(d, chain[:, 0], chain[:, 1])
    top_d = np.diff(chain[:, 1])[idx] / np.diff(chain[:, 0])[idx]
    lower_y = lo[0] + lo[1] * d
    upper_line = hi[0] + hi[1] * d
    upper_y = np.minimum(upper_line, top_y)
    upper_d = np.where(upper_line < top_y, hi[1], top_d)
    width = upper_y - lower_y
    active = (d > start) & (d < end) & (width > 1e-10) & (y >= lower_y-1e-9) & (y <= upper_y+1e-9)
    displacement = np.zeros(len(q))
    gradient = np.zeros_like(q)
    a = np.flatnonzero(active)
    w = width[a]
    v = np.clip(y[a] - lower_y[a], 0, w)
    t = v / w
    td = (-lo[1] * w - v * (upper_d[a] - lo[1])) / w**2
    value = slope * v * (1-t)**2
    dy = slope * (1-t) * (1-3*t)
    dd = slope * (-lo[1]*(1-t)**2 - 2*v*(1-t)*td)
    s = np.clip((d[a]-fade_start)/(end-fade_start), 0, 1)
    fade = 1 - 3*s*s + 2*s*s*s
    fade_d = (-6*s+6*s*s)/(end-fade_start)
    displacement[a] = value*fade
    gradient[a, 0] = dd*fade + value*fade_d
    gradient[a, 1] = dy*fade
    return displacement, gradient


def subdivide_copy(vertices, triangles, labels, levels=3):
    """Conforming midpoint subdivision; retains the exact original surface."""
    v = np.array(vertices, float, copy=True)
    f = np.array(triangles, np.int64, copy=True)
    lab = np.asarray(labels).copy()
    if (v.ndim != 2 or v.shape[1] != 3 or not np.isfinite(v).all()
            or f.ndim != 2 or f.shape[1] != 3 or len(f) != len(lab)
            or f.size == 0 or f.min() < 0 or f.max() >= len(v)):
        raise ValueError('invalid indexed triangle mesh')
    if isinstance(levels, bool) or not isinstance(levels, int) or not 0 <= levels <= 4:
        raise ValueError('levels must be an integer from zero to four')
    for _ in range(levels):
        edge = np.concatenate([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]])
        unique, inverse = np.unique(np.sort(edge, axis=1), axis=0, return_inverse=True)
        mids = len(v) + inverse.reshape(3, len(f)).T
        v = np.vstack([v, v[unique].mean(axis=1)])
        a, b, c = f.T
        ab, bc, ca = mids.T
        f = np.stack([np.c_[a, ab, ca], np.c_[ab, b, bc], np.c_[ca, bc, c], np.c_[ab, bc, ca]], axis=1).reshape(-1, 3)
        lab = np.repeat(lab, 4)
    return v, f, lab


def topology_report(vertices, triangles):
    """Independent triangle-edge/volume checks for a generated local copy."""
    v, f = np.asarray(vertices, float), np.asarray(triangles, np.int64)
    edge = np.concatenate([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]])
    undirected, count = np.unique(np.sort(edge, axis=1), axis=0, return_counts=True)
    directed, dc = np.unique(edge, axis=0, return_counts=True)
    area = np.cross(v[f[:, 1]]-v[f[:, 0]], v[f[:, 2]]-v[f[:, 0]])
    volume = np.einsum('ij,ij->i', v[f[:, 0]], np.cross(v[f[:, 1]], v[f[:, 2]])).sum()/6
    return dict(vertices=len(np.unique(f)), triangles=len(f),
                closed=bool(np.all(count == 2) and np.all(dc == 1)),
                euler=int(len(np.unique(f))-len(undirected)+len(f)),
                volume_mm3=float(volume), min_double_area_mm2=float(np.linalg.norm(area, axis=1).min()),
                finite=bool(np.isfinite(v).all()))
