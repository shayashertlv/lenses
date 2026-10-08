"""Observed-slope boundary join for quilt fills (owner-selected 2026-10-08).

Solve a screened correction inside the hole, continuing the normal slope from
two observed donor pixels at its edge. With only one donor, use zero slope.
The screen length is the quilt radius. Neither hidden observations nor pixels
outside the hole are changed. This is independent of the later chart Poisson
solve, which works across UV charts in log radiance.
"""
from __future__ import annotations

import numpy as np
from scipy import sparse
from scipy.sparse.linalg import cg


def match(filled, observed, hole, donors, radius: int) -> np.ndarray:
    """Return the joined uint8 RGB fill; preserve all non-hole pixels exactly."""
    hole = np.asarray(hole, bool)
    donors = np.asarray(donors, bool) & ~hole
    yy, xx = np.nonzero(hole)
    n = len(yy)
    out = np.array(filled, copy=True)
    if not n:
        return out
    lut = np.full(hole.shape, -1, np.int64)
    lut[yy, xx] = np.arange(n)
    diagonal = np.full(n, 1 / max(float(radius), 1)**2)
    rhs = np.zeros((n, 3))
    rows, cols = [], []
    for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        y, x = yy + dy, xx + dx
        inside = (y >= 0) & (y < hole.shape[0]) & (x >= 0) & (x < hole.shape[1])
        i = np.flatnonzero(inside)
        y, x = y[i], x[i]
        j = lut[y, x]
        unknown = j >= 0
        rows.append(i[unknown])
        cols.append(j[unknown])
        diagonal[i[unknown]] += 1
        known = donors[y, x]
        ik = i[known]
        diagonal[ik] += 1
        qy, qx = y[known], x[known]
        boundary = observed[qy, qx].astype(float)
        ry, rx = qy + dy, qx + dx
        valid = (ry >= 0) & (ry < hole.shape[0]) & (rx >= 0) & (rx < hole.shape[1])
        valid[valid] &= donors[ry[valid], rx[valid]]
        boundary[valid] += (observed[qy[valid], qx[valid]].astype(float)
                            - observed[ry[valid], rx[valid]].astype(float))
        rhs[ik] += boundary - filled[yy[ik], xx[ik]].astype(float)
    rows.append(np.arange(n))
    cols.append(np.arange(n))
    matrix = sparse.csr_matrix(
        (np.r_[-np.ones(sum(len(r) for r in rows[:-1])), diagonal],
         (np.concatenate(rows), np.concatenate(cols))), shape=(n, n))
    preconditioner = sparse.diags(1 / diagonal)
    delta = np.zeros((n, 3))
    for channel in range(3):
        delta[:, channel], status = cg(matrix, rhs[:, channel], M=preconditioner,
                                      rtol=1e-7, atol=1e-8)
        if status or not np.isfinite(delta[:, channel]).all():
            raise RuntimeError(f"observed-slope boundary solve failed: channel={channel}, status={status}")
    out[hole] = np.clip(np.rint(filled[hole].astype(float) + delta), 0, 255).astype(np.uint8)
    return out
