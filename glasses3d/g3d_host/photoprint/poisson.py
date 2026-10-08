"""Unscreened chart-seam stitching with observed photo texels held fixed.

Solve for a log-radiance correction on the connected texture surface. Within a
chart the correction has zero target gradient; across a chart seam it cancels
the guide's jump. Components without observations keep their mean correction
at zero. No quality samples, fixture identities or fitted blend radius enter
the solve. This is the owner-selected Poisson experiment's objective.
"""
from __future__ import annotations

import numpy as np
from scipy import sparse
from scipy.sparse.csgraph import connected_components
from scipy.sparse.linalg import cg

from .. import texture_utils as T


def stitch(tex, colours: np.ndarray, protected: np.ndarray, pairs: np.ndarray) -> tuple[np.ndarray, dict]:
    """Return RGB bytes and solver diagnostics without changing protected bytes.

    Raster neighbours connect only within the same chart and part. Explicit
    seam pairs connect charts of the same part. Repeated pairs retain their
    original weights, matching the saved experiment. Failure to converge is an
    error, never an implicit fallback to a different seam method.
    """
    colours = np.asarray(colours, dtype=float)
    protected = np.asarray(protected, dtype=bool)
    pairs = np.asarray(pairs, dtype=np.int64).reshape(-1, 2)
    n = len(colours)
    if colours.shape != (n, 3) or protected.shape != (n,):
        raise ValueError("Poisson requires RGB rows and one protected flag per texel")
    if not np.isfinite(colours).all() or np.any((colours < 0) | (colours > 255)):
        raise ValueError("Poisson guide must contain finite sRGB values in [0, 255]")
    if pairs.size and (pairs.min() < 0 or pairs.max() >= n):
        raise ValueError("Poisson seam pair is outside the texture")
    original = np.rint(colours).astype(np.uint8)
    unknown = ~protected
    nu = int(unknown.sum())
    report = {"method": "unscreened_poisson", "pairs": int(len(pairs)), "unknown_texels": nu,
              "protected_changed": 0, "channels": [], "components": 0, "unanchored_components": 0,
              "above_255": 0, "below_zero": 0, "relative_tolerance": 1e-7, "absolute_tolerance": 1e-8,
              "same_point_equality": "not imposed; independent off-seam proof remains required"}
    if nu == 0:
        return original, report
    values = np.log(np.maximum(T.srgb_to_linear(colours), 1e-6))
    ids = np.full(n, -1, np.int64)
    ids[unknown] = np.arange(nu)
    diagonal = np.zeros(nu)
    rhs = np.zeros((nu, 3))
    rows, cols = [], []
    anchored = np.zeros(nu, bool)

    def edge(a, b, seam=False):
        ia, ib = ids[a], ids[b]
        for x, y, p, q in ((ia, ib, a, b), (ib, ia, b, a)):
            m = x >= 0
            np.add.at(diagonal, x[m], 1.0)
            anchored[x[m & (y < 0)]] = True
            if seam:
                np.add.at(rhs, x[m], values[q[m]] - values[p[m]])
            both = m & (y >= 0)
            rows.append(x[both])
            cols.append(y[both])

    index = tex.index
    for a, b in ((index[:-1], index[1:]), (index[:, :-1], index[:, 1:])):
        m = (a >= 0) & (b >= 0)
        a, b = a[m], b[m]
        m = (tex.chart[a] == tex.chart[b]) & (tex.part[a] == tex.part[b])
        edge(a[m], b[m])
    a, b = pairs.T
    m = (tex.part[a] == tex.part[b]) & (a != b)
    edge(a[m], b[m], seam=True)
    adjacency = sparse.csr_matrix((np.ones(sum(len(r) for r in rows)),
                                   (np.concatenate(rows), np.concatenate(cols))), shape=(nu, nu))
    nc, component = connected_components(adjacency, directed=False)
    has_anchor = np.bincount(component, weights=anchored, minlength=nc) > 0
    matrix = sparse.diags(diagonal) - adjacency + sparse.diags((diagonal == 0).astype(float))
    preconditioner = sparse.diags(1 / np.maximum(diagonal, 1))
    counts = np.bincount(component, minlength=nc)
    result = values.copy()
    for channel in range(3):
        iterations = [0]

        def progress(_):
            iterations[0] += 1

        delta, status = cg(matrix, rhs[:, channel], M=preconditioner, rtol=1e-7, atol=1e-8, callback=progress)
        if status or not np.isfinite(delta).all():
            raise RuntimeError(f"Poisson channel {channel} did not converge: {status}")
        means = np.bincount(component, weights=delta, minlength=nc) / counts
        delta -= np.where(has_anchor[component], 0, means[component])
        result[unknown, channel] += delta
        residual = float(np.linalg.norm(matrix @ delta - rhs[:, channel]) /
                         max(np.linalg.norm(rhs[:, channel]), 1e-12))
        report["channels"].append({"iterations": iterations[0], "relative_residual": residual,
                                   "log_gain_min": float(delta.min()), "log_gain_max": float(delta.max())})
    linear = np.exp(result[unknown])
    if not np.isfinite(linear).all():
        raise RuntimeError("Poisson produced non-finite radiance")
    out = colours.copy()
    out[unknown] = T.linear_to_srgb(linear)
    if not np.isfinite(out).all():
        raise RuntimeError("Poisson produced non-finite colours")
    report.update(components=int(nc), unanchored_components=int((~has_anchor).sum()),
                  above_255=int(np.any(linear > 1, axis=1).sum()),
                  below_zero=int(np.any(linear < 0, axis=1).sum()),
                  gamut_note="Counts are before linear_to_srgb clips radiance to [0, 1].")
    out = np.clip(np.rint(out), 0, 255).astype(np.uint8)
    assert np.array_equal(out[protected], original[protected])
    return out, report
