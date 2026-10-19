"""Strip parametrisation of an elongated part (brow, temple) on its ORIGINAL triangles (per-corner UVs, no
subdivision). numpy only (it runs inside Blender 5.2 as well as on the host).

Ported from the scratch ``g3d-wp5/cov/stripuv.py`` (``strip_param``, ``_rmf``, ``distortion``, ``wquant``);
glasses3d/plan/archive/20261019/PORT_REVIEW.md section 10. Changes: the scipy ``cKDTree`` nearest-centreline query is a chunked brute-force search (the
centreline has about 1-3 thousand samples), the cut is given as a 3D DIRECTION (``cut_dir``, the design's "brow back
face" / "temple inner-bottom edge") instead of an angle, the per-vertex profile interpolation is vectorised, and the
result carries the vertex ids it was computed on.

- s = arc length along a smooth centreline: a polynomial of degree <= 5 (``len(slices) // 4``) through the 2 mm slice
  bounding-box centres, offset from the principal axis; a vertex takes the nearest point of the densely sampled
  centreline (0.05 mm), refined on the segment.
- t = arc length AROUND the cross-section, from the cut: a per-slice radius profile r(theta) (max radius per 5 deg bin,
  periodic interpolation over empty bins) gives the perimeter arc length L(theta); profiles are interpolated between
  slices. theta is measured in a rotation-minimising frame (double reflection, Wang et al. 2008) seeded with ``ref``
  at the middle of the curve.
- A triangle whose corners straddle the cut gets its low corners shifted by the perimeter P(s), so it stays whole (the
  cut is a UV seam).
"""
from __future__ import annotations

import math

import numpy as np

NBINS = 72
SLICE_MM = 2.0
CL_STEP_MM = 0.05


def rmf(C: np.ndarray, ref: np.ndarray, mid: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Rotation-minimising frame (T, R, B) along the polyline C, seeded with ``ref`` at index ``mid``."""
    T = np.gradient(C, axis=0)
    T /= np.linalg.norm(T, axis=1, keepdims=True)
    n = len(C)
    R = np.zeros((n, 3))
    r0 = ref - (ref @ T[mid]) * T[mid]
    R[mid] = r0 / np.linalg.norm(r0)

    def step(i, j):
        v1 = C[j] - C[i]
        c1 = v1 @ v1
        if c1 < 1e-18:
            R[j] = R[i]
            return
        rL = R[i] - (2 / c1) * (v1 @ R[i]) * v1
        tL = T[i] - (2 / c1) * (v1 @ T[i]) * v1
        v2 = T[j] - tL
        c2 = v2 @ v2
        rj = rL - (2 / c2) * (v2 @ rL) * v2 if c2 > 1e-18 else rL
        rj = rj - (rj @ T[j]) * T[j]
        R[j] = rj / np.linalg.norm(rj)

    for i in range(mid, n - 1):
        step(i, i + 1)
    for i in range(mid, 0, -1):
        step(i, i - 1)
    return T, R, np.cross(T, R)


def nearest_on_polyline(P: np.ndarray, C: np.ndarray, chunk: int = 2048) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """For each point: segment index k, fraction f in [0, 1] and distance to the polyline C (nearest sample, then the
    better of its two adjacent segments)."""
    n = len(C)
    j = np.empty(len(P), np.int64)
    c2 = (C * C).sum(1)
    for a in range(0, len(P), chunk):
        p = P[a:a + chunk]
        d2 = (p * p).sum(1)[:, None] - 2.0 * p @ C.T + c2[None]
        j[a:a + chunk] = np.argmin(d2, axis=1)
    j = np.clip(j, 1, n - 2)
    best_k = j.copy()
    best_f = np.zeros(len(P))
    dbest = np.full(len(P), np.inf)
    for off in (-1, 0):
        a = C[j + off]
        b = C[j + off + 1]
        ab = b - a
        f = np.clip(((P - a) * ab).sum(1) / np.maximum((ab * ab).sum(1), 1e-12), 0, 1)
        q = a + f[:, None] * ab
        d = np.linalg.norm(P - q, axis=1)
        better = d < dbest
        dbest[better] = d[better]
        best_k[better] = (j + off)[better]
        best_f[better] = f[better]
    return best_k, best_f, dbest


def strip_param(V: np.ndarray, F: np.ndarray, faces: np.ndarray, ref: np.ndarray, cut_dir: np.ndarray | None = None,
                slice_mm: float = SLICE_MM) -> dict:
    """Per-corner (s, t) in mm for ``faces`` of the mesh (V, F); ``ref`` seeds the frame (theta = 0), ``cut_dir`` is
    the 3D direction (from the centreline) where t is cut (default: -ref)."""
    V = np.asarray(V, np.float64)
    F = np.asarray(F, np.int64)
    faces = np.asarray(faces, np.int64)
    ref = np.asarray(ref, np.float64)
    ref = ref / np.linalg.norm(ref)
    cut_dir = -ref if cut_dir is None else np.asarray(cut_dir, np.float64) / np.linalg.norm(cut_dir)
    vid = np.unique(F[faces])
    P0 = V[vid]
    mu = P0.mean(0)
    a1 = np.linalg.svd(P0 - mu, full_matrices=False)[2][0]
    # a deterministic axis sign: the largest coordinate of a1 positive (SVD signs are arbitrary)
    if a1[int(np.argmax(np.abs(a1)))] < 0:
        a1 = -a1
    e1 = ref - (ref @ a1) * a1
    e1 /= np.linalg.norm(e1)
    e2 = np.cross(a1, e1)
    tau_v = (P0 - mu) @ a1
    edges = np.arange(tau_v.min(), tau_v.max() + slice_mm, slice_mm)
    cs, ts = [], []
    for k in range(len(edges) - 1):
        sel = (tau_v >= edges[k]) & (tau_v < edges[k + 1])
        if sel.sum() < 6:
            continue
        Q = P0[sel]
        q1 = (Q - mu) @ e1
        q2 = (Q - mu) @ e2
        tm = 0.5 * (edges[k] + edges[k + 1])
        cs.append(mu + 0.5 * (q1.min() + q1.max()) * e1 + 0.5 * (q2.min() + q2.max()) * e2 + tm * a1)
        ts.append(tm)
    if len(ts) < 2:
        raise ValueError("part too short for a strip chart")
    cs = np.array(cs)
    ts = np.array(ts)
    deg = int(min(5, max(1, len(ts) // 4)))
    coefs = [np.polyfit(ts, cs[:, d] - ts * a1[d], deg) for d in range(3)]   # the offset from the axis
    tg = np.arange(tau_v.min() - 2.0, tau_v.max() + 2.0, CL_STEP_MM)
    tcl = np.clip(tg, ts.min(), ts.max())
    C = np.stack([np.polyval(coefs[d], tcl) for d in range(3)], -1) + tg[:, None] * a1
    seg = np.linalg.norm(np.diff(C, axis=0), axis=1)
    S = np.concatenate([[0.0], np.cumsum(seg)])
    mid = len(C) // 2
    T, R, B = rmf(C, ref, mid)
    k, f, dist = nearest_on_polyline(P0, C)
    s_v = S[k] * (1 - f) + S[k + 1] * f
    Cq = C[k] * (1 - f)[:, None] + C[k + 1] * f[:, None]
    Rq = R[k] * (1 - f)[:, None] + R[k + 1] * f[:, None]
    Bq = B[k] * (1 - f)[:, None] + B[k + 1] * f[:, None]
    d = P0 - Cq
    th_v = np.arctan2((d * Bq).sum(1), (d * Rq).sum(1))          # 0 = ref direction
    r_v = np.hypot((d * Bq).sum(1), (d * Rq).sum(1))
    cut_theta = float(math.atan2(cut_dir @ B[mid], cut_dir @ R[mid]))
    # per-slice radius profile -> arc length L(theta)
    sl = np.arange(s_v.min(), s_v.max() + slice_mm, slice_mm)
    if len(sl) < 2:
        sl = np.array([s_v.min(), s_v.min() + slice_mm])
    nsl = len(sl) - 1
    bins = np.linspace(-math.pi, math.pi, NBINS + 1)
    bc = 0.5 * (bins[1:] + bins[:-1])
    Lprof = np.zeros((nsl, NBINS + 1))
    Pslice = np.zeros(nsl)
    sc = 0.5 * (sl[1:] + sl[:-1])
    for i in range(nsl):
        sel = (s_v >= sl[i] - 0.5 * slice_mm) & (s_v < sl[i + 1] + 0.5 * slice_mm)
        rb = np.full(NBINS, -1.0)
        if sel.sum() >= 8:
            idx = np.clip(np.digitize(th_v[sel], bins) - 1, 0, NBINS - 1)
            np.maximum.at(rb, idx, r_v[sel])
        rb[rb < 0] = np.nan
        good = np.isfinite(rb)
        if good.sum() < 4:
            rb[:] = float(np.nanmedian(r_v)) if not good.any() else float(np.nanmedian(rb))
        else:
            xs = np.concatenate([bc[good] - 2 * math.pi, bc[good], bc[good] + 2 * math.pi])
            ys = np.tile(rb[good], 3)
            rb = np.interp(bc, xs, ys)
        x = rb * np.cos(bc)
        y = rb * np.sin(bc)
        xx = np.concatenate([x, x[:1]])
        yy = np.concatenate([y, y[:1]])
        Lc = np.concatenate([[0.0], np.cumsum(np.hypot(np.diff(xx), np.diff(yy)))])   # L at bc[0] .. bc[0] + 2 pi
        Pslice[i] = Lc[-1]
        Lprof[i] = Lc
    bce = np.concatenate([bc, [bc[0] + 2 * math.pi]])

    def L_of(s, th):
        thw = np.where(th < bc[0], th + 2 * math.pi, th)
        fi = np.clip(np.interp(s, sc, np.arange(nsl)) if nsl > 1 else np.zeros(len(s)), 0, nsl - 1)
        i0 = np.floor(fi).astype(int)
        i1 = np.minimum(i0 + 1, nsl - 1)
        w = fi - i0
        # vectorised per-row interpolation on the shared abscissa bce
        pos = np.clip(np.searchsorted(bce, thw, side="right") - 1, 0, len(bce) - 2)
        fr = (thw - bce[pos]) / (bce[pos + 1] - bce[pos])
        L0 = Lprof[i0, pos] * (1 - fr) + Lprof[i0, pos + 1] * fr
        L1 = Lprof[i1, pos] * (1 - fr) + Lprof[i1, pos + 1] * fr
        Pp = Pslice[i0] * (1 - w) + Pslice[i1] * w
        return L0 * (1 - w) + L1 * w, Pp

    Lv, Pv = L_of(s_v, th_v)
    Lcut, _ = L_of(s_v, np.full(len(s_v), cut_theta))
    t_v = np.mod(Lv - Lcut, Pv)
    vmap = -np.ones(len(V), np.int64)
    vmap[vid] = np.arange(len(vid))
    tri = vmap[F[faces]]
    s_c = s_v[tri]
    t_c = t_v[tri].copy()
    P_c = Pv[tri]
    span = t_c.max(1) - t_c.min(1)
    wrap = span > 0.5 * P_c.mean(1)
    lo = t_c < 0.5 * P_c
    t_c[wrap[:, None] & lo] += P_c[wrap[:, None] & lo]
    return {"s": s_c, "t": t_c, "wrap": wrap, "cut_theta": cut_theta, "length": float(S[-1]),
            "perim_med": float(np.median(Pslice)), "perim_max": float(Pslice.max()), "theta_v": th_v, "vid": vid,
            "s_v": s_v, "t_v": t_v, "P_v": Pv, "centre_dist": dist, "axis": a1}


def distortion(V: np.ndarray, F: np.ndarray, faces: np.ndarray, u: np.ndarray, v: np.ndarray):
    """Per-triangle singular values of the 3D -> UV map (UV in the units of ``u``, ``v`` per mm; ideal (1, 1) for UV in
    mm), the 3D area and the signed UV area. Returns (sig (K, 2) descending, area3d (K,), signed uv area (K,))."""
    V = np.asarray(V, np.float64)
    Pt = V[np.asarray(F)[np.asarray(faces)]]
    e1 = Pt[:, 1] - Pt[:, 0]
    e2 = Pt[:, 2] - Pt[:, 0]
    n = np.cross(e1, e2)
    a3 = 0.5 * np.linalg.norm(n, axis=1)
    x = e1 / np.maximum(np.linalg.norm(e1, axis=1, keepdims=True), 1e-12)
    y = np.cross(n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12), x)
    X = np.stack([np.stack([(e1 * x).sum(1), (e1 * y).sum(1)], 1),
                  np.stack([(e2 * x).sum(1), (e2 * y).sum(1)], 1)], 1)
    U = np.stack([np.stack([u[:, 1] - u[:, 0], v[:, 1] - v[:, 0]], 1),
                  np.stack([u[:, 2] - u[:, 0], v[:, 2] - v[:, 0]], 1)], 1)
    ok = np.abs(np.linalg.det(X)) > 1e-10
    J = np.zeros((len(Pt), 2, 2))
    J[ok] = np.linalg.solve(X[ok], U[ok])          # edges_local @ J = edges_uv
    sig = np.linalg.svd(J, compute_uv=False)
    auv = 0.5 * (U[:, 0, 0] * U[:, 1, 1] - U[:, 0, 1] * U[:, 1, 0])
    return sig, a3, auv


def wquant(x: np.ndarray, w: np.ndarray, qs) -> list[float]:
    """Weighted quantiles (q in [0, 1])."""
    x = np.asarray(x, np.float64)
    w = np.asarray(w, np.float64)
    if not len(x) or w.sum() <= 0:
        return [float("nan") for _ in qs]
    o = np.argsort(x, kind="stable")
    x, w = x[o], w[o]
    c = np.cumsum(w) / w.sum()
    return [float(np.interp(q, c, x)) for q in qs]
