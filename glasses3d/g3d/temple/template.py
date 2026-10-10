"""Revision 0 of a temple: a g3d.temple/2 spec measured from an existing temple mesh (plan/WP6_FACETS.md §2.4). numpy,
host (the GLB's arrays: g3d_host/glb.load, positions x 1000).

    template_from_mesh(V, F, side, hinge_z, axis_xz) -> (spec2, report)

V (world mm, +Y up, +Z front) and F are ONE temple body; `side` (+1 / -1, or "+x" / "-x") picks its chart: d = hinge_z
- z, y, X = side x (measure.chart_mesh). The spec has no facets, no creases and roll_mm 0; the coding agent records a
suggested roll from the section strip and Astra sets it.

Stations every STATION_STEP_MM (integer d, plus one END_EPS_MM inside each end). Per station:
- outline y_top / y_bot = the section's extent;
- x_in = min X over the section, the top and bottom EDGE_SHARE (12 %) of its height excluded;
- t_top / t_bot = a robust (Huber) line fit of the outer X profile against y over the middle 76 %, evaluated at y_top
  and y_bot, minus x_in (the /2 wedge base).
Ends: the tip ends at the last station whose height is >= END_HEIGHT_SHARE of the median height over the last
END_WINDOW_MM (the hinge end symmetrically at the first); the length lost is reported. The five chains (outline top,
bottom, inner, base top, base bottom) share ONE knot set chosen by a greedy joint Ramer-Douglas-Peucker split at
RDP_TOL_MM, at most MAX_KNOTS knots (the simplification error per chain is reported). tip_round_mm is fitted on a
0.05 mm grid: for a radius r > 0 the outline knots within r of the tip are replaced by the corner where the last kept
segment, extended, meets the tip cap, and the core's arc rounds that corner; the objective is the two-sided mean
distance between the rebuilt and the original side silhouettes over the last TIP_FIT_WINDOW_MM, and the smallest radius
within TIP_FIT_TIE_MM of the lowest objective wins.

Report (information, never a gate): the one-sided original -> rebuild distance (area-weighted surface samples of the
original to the /2 core's mesh) as mean / p95 / max per region (hinge zone: the first 15 mm; bend: +-10 mm around the
outline mid-line's sharpest downward turn; middle: between them; tip: behind the bend), the inner-face deviation (the
original's inner face against the rebuilt x_in), the simplification error, the knots and the lengths lost.
"""
from __future__ import annotations

import heapq
import math
import time

import numpy as np

from . import SCHEMA
from . import measure as MS
from .build import build_temple
from .spec import MIN_HEIGHT_MM, TempleError, _outline

STATION_STEP_MM = 1.0
END_EPS_MM = 0.01                  # the end stations are measured this far inside the mesh's d range
EDGE_SHARE = 0.12                  # x_in and the outer-profile fit leave out the top and bottom 12 % of the height
PROFILE_STEP_MM = 0.05             # outer-profile sample spacing in y
HUBER_K = 1.345
END_WINDOW_MM = 5.0
END_HEIGHT_SHARE = 0.5
RDP_TOL_MM = 0.03
MAX_KNOTS = 40
TIP_FIT_STEP_MM = 0.05
TIP_FIT_MAX_MM = 3.0
TIP_FIT_WINDOW_MM = 5.0
TIP_FIT_SAMPLE_MM = 0.02
TIP_FIT_TIE_MM = RDP_TOL_MM / 6    # objectives this close are a tie (the smaller radius wins): below the outline noise
SAMPLES = 40000                    # original surface samples for the distance report
SAMPLE_SEED = 0
SLAB_MM = 1.0                      # distance search: point slabs in d ...
SEARCH_MM = 2.0                    # ... against triangles within this of the slab (exact fallback beyond)
HINGE_ZONE_MM = 15.0
BEND_HALF_MM = 10.0
BEND_ARM_MM = 5.0
CHAINS = ("outline.top", "outline.bottom", "inner", "base.top", "base.bottom")
ROUND = 4


# --------------------------------------------------------------------------------------------- stations
def robust_line(y: np.ndarray, x: np.ndarray, iters: int = 50) -> tuple[float, float, np.ndarray]:
    """Huber IRLS fit x = a + b y (scale from the MAD). Returns (a, b, residuals)."""
    A = np.stack([np.ones_like(y), y], axis=1)
    w = np.ones_like(y)
    coef = np.zeros(2)
    for _ in range(iters):
        sw = np.sqrt(w)
        new, *_ = np.linalg.lstsq(A * sw[:, None], x * sw, rcond=None)
        r = x - A @ new
        s = 1.4826 * float(np.median(np.abs(r - np.median(r))))
        k = HUBER_K * max(s, 1e-6)
        a = np.abs(r)
        w = np.where(a <= k, 1.0, k / np.maximum(a, 1e-300))
        done = np.allclose(new, coef, rtol=0.0, atol=1e-10)
        coef = new
        if done:
            break
    return float(coef[0]), float(coef[1]), x - A @ coef


def _band_min_x(seg: np.ndarray, lo: float, hi: float) -> float:
    """Min X over the section segments clipped to lo <= y <= hi (exact)."""
    y0, x0, y1, x1 = seg[:, 0, 0], seg[:, 0, 1], seg[:, 1, 0], seg[:, 1, 1]
    best = np.inf
    for ya, xa, yb, xb in ((y0, x0, y1, x1), (y1, x1, y0, x0)):
        inside = (ya >= lo) & (ya <= hi)
        if inside.any():
            best = min(best, float(xa[inside].min()))
    dy = y1 - y0
    for yc in (lo, hi):
        cross = ((y0 - yc) * (y1 - yc) < 0) & (dy != 0)
        if cross.any():
            t = (yc - y0[cross]) / dy[cross]
            best = min(best, float((x0[cross] + t * (x1[cross] - x0[cross])).min()))
    return best


def station(L: np.ndarray, F: np.ndarray, d: float) -> dict | None:
    """One station's measures (module docstring); None when the plane d cuts nothing."""
    seg = MS.slice_d(L, F, float(d))
    if len(seg) == 0:
        return None
    ys = seg[..., 0]
    yb, yt = float(ys.min()), float(ys.max())
    h = yt - yb
    rec = {"d": float(d), "y_top": yt, "y_bot": yb, "height": h}
    if h < MIN_HEIGHT_MM:
        return rec
    lo, hi = yb + EDGE_SHARE * h, yt - EDGE_SHARE * h
    x_in = _band_min_x(seg, lo, hi)
    yg = np.linspace(lo, hi, max(20, int(math.ceil((hi - lo) / PROFILE_STEP_MM)) + 1))
    xo, _ = MS.section_profile(seg, yg)
    ok = np.isfinite(xo)
    a, b, res = robust_line(yg[ok], xo[ok])
    rec.update({"x_in": x_in, "t_top": a + b * yt - x_in, "t_bot": a + b * yb - x_in,
                "fit_resid_p95_mm": float(np.percentile(np.abs(res), 95)),
                "fit_resid_max_mm": float(np.abs(res).max())})
    return rec


def station_ds(dlo: float, dhi: float, step: float = STATION_STEP_MM) -> list[float]:
    """Integer-d stations at least half a step from the ends, plus one END_EPS_MM inside each end."""
    k0 = int(math.ceil((dlo + END_EPS_MM + 0.5 * step) / step))
    k1 = int(math.floor((dhi - END_EPS_MM - 0.5 * step) / step))
    return [dlo + END_EPS_MM] + [k * step for k in range(k0, k1 + 1)] + [dhi - END_EPS_MM]


def end_cut(H: np.ndarray, D: np.ndarray, at_tip: bool) -> int:
    """Index of the last (tip) / first (hinge end) station whose height is >= END_HEIGHT_SHARE x the median height
    over the END_WINDOW_MM at that end."""
    if at_tip:
        win = D >= D[-1] - END_WINDOW_MM
    else:
        win = D <= D[0] + END_WINDOW_MM
    ref = END_HEIGHT_SHARE * float(np.median(H[win]))
    ok = np.nonzero(H >= max(ref, MIN_HEIGHT_MM))[0]
    return int(ok[-1] if at_tip else ok[0])


# --------------------------------------------------------------------------------------------- shared knots
def _seg_err(D, Y, i: int, j: int):
    if j - i < 2:
        return 0.0, None
    k = np.arange(i + 1, j)
    t = (D[k] - D[i]) / (D[j] - D[i])
    e = np.abs(Y[:, k] - (Y[:, i:i + 1] + t * (Y[:, j:j + 1] - Y[:, i:i + 1]))).max(axis=0)
    m = int(np.argmax(e))
    return float(e[m]), int(k[m])


def shared_knots(D: np.ndarray, Y: np.ndarray, tol: float = RDP_TOL_MM, max_knots: int = MAX_KNOTS) -> list[int]:
    """Greedy joint RDP: one knot set for every row of Y (chains sampled at D). Splits the segment with the largest
    deviation of any chain first, until every deviation is <= tol or max_knots knots exist."""
    n = len(D)
    knots = {0, n - 1}
    e, k = _seg_err(D, Y, 0, n - 1)
    heap = [(-e, 0, n - 1, k)]
    while heap and len(knots) < max_knots:
        e, i, j, k = heapq.heappop(heap)
        if -e <= tol or k is None:
            break
        knots.add(k)
        for a, b in ((i, k), (k, j)):
            ea, ka = _seg_err(D, Y, a, b)
            heapq.heappush(heap, (-ea, a, b, ka))
    return sorted(knots)


def chain_errors(D: np.ndarray, Y: np.ndarray, knots: list[int]) -> np.ndarray:
    """Per chain: the largest |sample - knot interpolation| over the stations."""
    out = np.zeros(len(Y))
    for c in range(len(Y)):
        out[c] = float(np.max(np.abs(Y[c] - np.interp(D, D[knots], Y[c, knots]))))
    return out


# --------------------------------------------------------------------------------------------- tip rounding
def _seg_dist(P: np.ndarray, A: np.ndarray, B: np.ndarray) -> np.ndarray:
    """(p,) distance of 2D points to the polyline made of segments A[i] -> B[i]."""
    s = B - A
    L2 = np.maximum(np.einsum("ij,ij->i", s, s), 1e-30)
    t = np.clip(((P[:, None, :] - A[None]) * s[None]).sum(-1) / L2[None], 0.0, 1.0)
    C = A[None] + t[..., None] * s[None]
    return np.hypot(P[:, None, 0] - C[..., 0], P[:, None, 1] - C[..., 1]).min(axis=1)


def _densify(P: np.ndarray, step: float) -> np.ndarray:
    out = [P[0]]
    for a, b in zip(P[:-1], P[1:]):
        n = max(1, int(math.ceil(np.hypot(*(b - a)) / step)))
        out += [a + (b - a) * k / n for k in range(1, n + 1)]
    return np.asarray(out)


def tip_silhouette(L: np.ndarray, F: np.ndarray, d_from: float, d_to: float) -> np.ndarray:
    """The original's side-silhouette boundary over [d_from, d_to] as one polyline: the top edge forward, the end,
    the bottom edge back (from slices every TIP_FIT_SAMPLE_MM)."""
    top, bot = [], []
    for d in np.arange(d_from, d_to, TIP_FIT_SAMPLE_MM):
        seg = MS.slice_d(L, F, float(d))
        if len(seg):
            top.append([d, float(seg[..., 0].max())])
            bot.append([d, float(seg[..., 0].min())])
    return np.asarray(top + bot[::-1])


def corner_chain(chain: np.ndarray, r: float) -> np.ndarray | None:
    """The chain with its knots within r of its last d replaced by the corner where the last kept segment, extended,
    reaches that d (None when fewer than two knots stay)."""
    d1 = float(chain[-1, 0])
    if r <= 0:
        return chain
    keep = chain[chain[:, 0] <= d1 - r]
    if len(keep) < 2:
        return None
    (da, ya), (db, yb) = keep[-2], keep[-1]
    return np.vstack([keep, [[d1, yb + (yb - ya) / (db - da) * (d1 - db)]]])


def _from(chain: np.ndarray, d: float) -> np.ndarray:
    """The part of a chain at or behind d, starting with its point at d."""
    keep = chain[chain[:, 0] > d]
    return np.vstack([[[d, float(np.interp(d, chain[:, 0], chain[:, 1]))]], keep])


def fit_tip_round(top: np.ndarray, bot: np.ndarray, sil: np.ndarray, d_from: float) -> dict:
    """tip_round_mm on a TIP_FIT_STEP_MM grid (module docstring). top / bot: the emitted outline chains. Returns
    {"tip_round_mm", "top", "bottom", "table": [[r, objective]], "objective_mm"} with the chains the winner needs."""
    cap = float(top[-1, 1] - bot[-1, 1])
    rmax = min(TIP_FIT_MAX_MM, 0.45 * cap)
    table = []
    for r in np.arange(0.0, rmax + 1e-9, TIP_FIT_STEP_MM):
        r = round(float(r), 6)
        t2, b2 = corner_chain(top, r), corner_chain(bot, r)
        if t2 is None or b2 is None:
            continue
        try:
            T, B = _outline({"outline": {"top": t2.tolist(), "bottom": b2.tolist(), "tip_round_mm": r}})
        except TempleError:
            if r == 0.0:
                raise
            continue
        reb = np.vstack([_from(T, d_from), _from(B, d_from)[::-1]])
        o2r = _seg_dist(sil, reb[:-1], reb[1:])
        dens = _densify(reb, TIP_FIT_SAMPLE_MM)
        r2o = _seg_dist(dens, sil[:-1], sil[1:])
        obj = 0.5 * (float(o2r.mean()) + float(r2o.mean()))
        table.append([r, obj, t2, b2])
    lowest = min(o for _, o, _, _ in table)
    r, obj, t2, b2 = next(row for row in table if row[1] <= lowest + TIP_FIT_TIE_MM)
    return {"tip_round_mm": r, "objective_mm": obj, "top": t2, "bottom": b2, "lowest_mm": lowest,
            "table": [[row[0], row[1]] for row in table]}


# --------------------------------------------------------------------------------------------- distances
def _tri_dist(P: np.ndarray, A: np.ndarray, B: np.ndarray, C: np.ndarray) -> np.ndarray:
    """(p, t) distances of points P (p, 3) to triangles (A, B, C: (t, 3)): the plane distance when the projection lies
    inside, else the nearest edge."""
    E0, E1 = B - A, C - A
    N = np.cross(E0, E1)
    nn = np.linalg.norm(N, axis=1)
    W = P[:, None, :] - A[None]
    d00, d01, d11 = (E0 * E0).sum(1), (E0 * E1).sum(1), (E1 * E1).sum(1)
    d20, d21 = (W * E0[None]).sum(-1), (W * E1[None]).sum(-1)
    den = d00 * d11 - d01 * d01
    ok = den > 1e-24
    den = np.where(ok, den, 1.0)
    v = (d11 * d20 - d01 * d21) / den
    w = (d00 * d21 - d01 * d20) / den
    inside = (v >= 0) & (w >= 0) & (v + w <= 1) & ok[None]
    plane = np.abs((W * N[None]).sum(-1)) / np.where(nn > 0, nn, 1.0)[None]

    def seg(a, b):
        s = b - a
        L2 = np.maximum((s * s).sum(1), 1e-30)
        X = P[:, None, :] - a[None]
        t = np.clip((X * s[None]).sum(-1) / L2[None], 0.0, 1.0)
        return np.linalg.norm(X - t[..., None] * s[None], axis=-1)
    edge = np.minimum(np.minimum(seg(A, B), seg(B, C)), seg(C, A))
    return np.where(inside, plane, edge)


def point_mesh_distance(P: np.ndarray, V: np.ndarray, T: np.ndarray) -> np.ndarray:
    """Exact unsigned distance of points P to the triangle mesh (V, T) in the chart: points in SLAB_MM slabs of d
    against the triangles within SEARCH_MM of the slab; points farther than SEARCH_MM from everything found there are
    measured against every triangle."""
    P = np.asarray(P, np.float64)
    tri = V[T]
    tlo, thi = tri[..., 0].min(axis=1), tri[..., 0].max(axis=1)
    out = np.full(len(P), np.inf)
    lo0 = math.floor(float(P[:, 0].min()) / SLAB_MM) * SLAB_MM
    slab = np.floor((P[:, 0] - lo0) / SLAB_MM).astype(np.int64)
    for s in np.unique(slab):
        idx = np.nonzero(slab == s)[0]
        lo, hi = lo0 + s * SLAB_MM - SEARCH_MM, lo0 + (s + 1) * SLAB_MM + SEARCH_MM
        cand = np.nonzero((thi >= lo) & (tlo <= hi))[0]
        if len(cand):
            for c0 in range(0, len(idx), 512):
                j = idx[c0:c0 + 512]
                out[j] = _tri_dist(P[j], tri[cand, 0], tri[cand, 1], tri[cand, 2]).min(axis=1)
    far = np.nonzero(out > SEARCH_MM)[0]
    for c0 in range(0, len(far), 128):
        j = far[c0:c0 + 128]
        best = np.full(len(j), np.inf)
        for t0 in range(0, len(T), 2048):
            sl = slice(t0, t0 + 2048)
            best = np.minimum(best, _tri_dist(P[j], tri[sl, 0], tri[sl, 1], tri[sl, 2]).min(axis=1))
        out[j] = best
    return out


def surface_samples(V: np.ndarray, F: np.ndarray, n: int = SAMPLES, seed: int = SAMPLE_SEED):
    """(points, unit normals) of n area-weighted uniform samples (fixed seed: deterministic)."""
    A, B, C = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
    N = np.cross(B - A, C - A)
    ar = 0.5 * np.linalg.norm(N, axis=1)
    rng = np.random.default_rng(seed)
    t = rng.choice(len(F), size=n, p=ar / ar.sum())
    u, v = rng.random(n), rng.random(n)
    flip = u + v > 1
    u, v = np.where(flip, 1 - u, u), np.where(flip, 1 - v, v)
    P = A[t] + u[:, None] * (B[t] - A[t]) + v[:, None] * (C[t] - A[t])
    nrm = N[t] / np.maximum(2 * ar[t], 1e-300)[:, None]
    return P, nrm


def bend_d(D: np.ndarray, YT: np.ndarray, YB: np.ndarray, d_from: float, d_to: float) -> float | None:
    """d of the outline mid-line's sharpest downward turn (angle after minus angle before over BEND_ARM_MM) within
    [d_from, d_to]; None when nothing turns down."""
    mid = 0.5 * (YT + YB)
    best, at = 0.0, None
    for d in D[(D >= d_from) & (D <= d_to)]:
        y0, y1, y2 = np.interp([d - BEND_ARM_MM, d, d + BEND_ARM_MM], D, mid)
        turn = math.degrees(math.atan2(y2 - y1, BEND_ARM_MM) - math.atan2(y1 - y0, BEND_ARM_MM))
        if turn < best:
            best, at = turn, float(d)
    return at


def _stats(x: np.ndarray) -> dict:
    if not len(x):
        return {"n": 0, "mean_mm": None, "p95_mm": None, "max_mm": None}
    return {"n": int(len(x)), "mean_mm": float(x.mean()), "p95_mm": float(np.percentile(x, 95)),
            "max_mm": float(x.max())}


# --------------------------------------------------------------------------------------------- the template
def _side(side) -> int:
    if side in (1, -1):
        return int(side)
    if isinstance(side, str) and side.lower() in ("+x", "-x"):
        return 1 if side.lower() == "+x" else -1
    raise TempleError("E_SPEC", f"side must be +1, -1, '+x' or '-x', got {side!r}")


def template_from_mesh(V, F, side, hinge_z: float, axis_xz, *, samples: int = SAMPLES) -> tuple[dict, dict]:
    """Revision 0 spec and its report (module docstring). Raises TempleError (E_TARGET) on a mesh that is not one
    temple body reaching back from the hinge, and passes the core's refusals through when the spec does not build."""
    t0 = time.perf_counter()
    s = _side(side)
    V = np.asarray(V, np.float64)
    F = np.asarray(F, np.int64)
    if V.ndim != 2 or V.shape[1] != 3 or F.ndim != 2 or F.shape[1] != 3 or not len(F) or not np.all(np.isfinite(V)):
        raise TempleError("E_TARGET", "template_from_mesh needs finite vertices (n, 3) and triangles (m, 3)")
    L, Fc = MS.chart_mesh(V, F, s, float(hinge_z))
    dlo, dhi = float(L[:, 0].min()), float(L[:, 0].max())
    if dhi - dlo < 20.0:
        raise TempleError("E_TARGET", f"the mesh reaches only {dhi - dlo:.2f} mm back from the hinge: not a temple",
                          length_mm=dhi - dlo)
    ds = station_ds(dlo, dhi)
    recs = [station(L, Fc, d) for d in ds]
    H = np.array([0.0 if r is None else r["height"] for r in recs])
    D = np.array(ds)
    i0, i1 = end_cut(H, D, at_tip=False), end_cut(H, D, at_tip=True)
    keep = recs[i0:i1 + 1]
    bad = [ds[i0 + k] for k, r in enumerate(keep) if r is None or "x_in" not in r]
    if bad:
        raise TempleError("E_TARGET", f"the mesh has no usable section at d {bad[:5]} (height < {MIN_HEIGHT_MM} mm "
                          f"between its ends)", d=bad[:20])
    D = np.array([r["d"] for r in keep])
    Y = np.array([[r[k] for r in keep] for k in ("y_top", "y_bot", "x_in", "t_top", "t_bot")])
    knots = shared_knots(D, Y)
    err = chain_errors(D, Y, knots)
    Dk = D[knots]
    top = np.stack([Dk, Y[0, knots]], axis=1)
    bot = np.stack([Dk, Y[1, knots]], axis=1)
    d1 = float(D[-1])
    sil = tip_silhouette(L, Fc, d1 - TIP_FIT_WINDOW_MM, dhi)
    tip = fit_tip_round(top, bot, sil, d1 - TIP_FIT_WINDOW_MM)

    def rows(P):
        return [[round(float(a), ROUND), round(float(b), ROUND)] for a, b in P]
    spec = {"schema": SCHEMA,
            "hinge": {"z_mm": float(hinge_z), "axis_xz_mm": None if axis_xz is None else [float(v) for v in axis_xz],
                      "opening_deg": 0.0},
            "outline": {"top": rows(tip["top"]), "bottom": rows(tip["bottom"]), "tip_round_mm": tip["tip_round_mm"]},
            "inner": rows(np.stack([Dk, Y[2, knots]], axis=1)),
            "base": {"top": rows(np.stack([Dk, Y[3, knots]], axis=1)),
                     "bottom": rows(np.stack([Dk, Y[4, knots]], axis=1))},
            "nodes": {}, "facets": {}, "creases": [], "chamfers": [], "roll_mm": 0.0, "options": {}}
    t_fit = time.perf_counter()
    tb = build_temple(spec)
    t_build = time.perf_counter()
    ps = tb.spec
    P, N = surface_samples(L, Fc, samples)
    dist = point_mesh_distance(P, tb.V, tb.tris)
    bend = bend_d(D, Y[0], Y[1], ps.d0 + HINGE_ZONE_MM + BEND_HALF_MM, ps.d1 - BEND_HALF_MM)
    edges = {"hinge_zone": (-np.inf, ps.d0 + HINGE_ZONE_MM)}
    if bend is None:
        edges["middle"] = (ps.d0 + HINGE_ZONE_MM, np.inf)
    else:
        edges.update({"middle": (ps.d0 + HINGE_ZONE_MM, bend - BEND_HALF_MM),
                      "bend": (bend - BEND_HALF_MM, bend + BEND_HALF_MM), "tip": (bend + BEND_HALF_MM, np.inf)})
    regions = {}
    for name, (a, b) in edges.items():
        sel = (P[:, 0] >= a) & (P[:, 0] < b)
        regions[name] = {"d_mm": [None if not np.isfinite(a) else round(float(a), 2),
                                  None if not np.isfinite(b) else round(float(b), 2)], **_stats(dist[sel])}
    yt, yb = ps.y_top(P[:, 0]), ps.y_bot(P[:, 0])
    h = yt - yb
    inner = ((N[:, 2] < -MS.OUTER_NORMAL_MIN) & (P[:, 0] >= ps.d0) & (P[:, 0] <= ps.d1)
             & (P[:, 1] >= yb + EDGE_SHARE * h) & (P[:, 1] <= yt - EDGE_SHARE * h))
    dev = P[inner, 2] - ps.inner(P[inner, 0])
    resid = np.array([r["fit_resid_p95_mm"] for r in keep])
    report = {
        "tool": "g3d.temple.template_from_mesh", "schema": SCHEMA, "side": "+x" if s > 0 else "-x",
        "hinge_z_mm": float(hinge_z), "spec_sha256": ps.sha256,
        "mesh": {"vertices": int(len(V)), "triangles": int(len(F)), "d_range_mm": [dlo, dhi]},
        "stations": {"count": len(keep), "step_mm": STATION_STEP_MM, "d0": float(D[0]), "d1": d1,
                     "edge_share": EDGE_SHARE},
        "length_lost_mm": {"hinge_end": float(D[0] - dlo), "tip": float(dhi - d1),
                           "rule": f"end station height >= {END_HEIGHT_SHARE} x median over {END_WINDOW_MM:g} mm"},
        "knots": {"count": len(knots), "max": MAX_KNOTS, "tol_mm": RDP_TOL_MM,
                  "outline_top": len(spec["outline"]["top"]), "outline_bottom": len(spec["outline"]["bottom"])},
        "simplification_error_mm": {**{c: float(e) for c, e in zip(CHAINS, err)},
                                    "note": "knot interpolation vs the 1 mm stations, before the tip-round fit"},
        "tip_round": {"tip_round_mm": tip["tip_round_mm"], "objective_mm": tip["objective_mm"],
                      "objective_at_0_mm": tip["table"][0][1] if tip["table"][0][0] == 0 else None,
                      "lowest_mm": tip["lowest_mm"], "tie_mm": TIP_FIT_TIE_MM,
                      "window_mm": TIP_FIT_WINDOW_MM, "candidates": len(tip["table"])},
        "outer_fit_resid_p95_mm": {"median": float(np.median(resid)), "max": float(resid.max()),
                                   "max_at_d": float(D[int(np.argmax(resid))])},
        "distance_original_to_rebuild": {"samples": int(samples), "all": _stats(dist), "regions": regions,
                                         "bend_d": bend},
        "inner_deviation_mm": {**_stats(np.abs(dev)), "signed_min_mm": float(dev.min()) if len(dev) else None,
                               "signed_max_mm": float(dev.max()) if len(dev) else None,
                               "note": "original inner-face samples (normal X < -0.6, middle 76 % of the height) "
                                       "minus the rebuilt x_in(d); + = outward"},
        "rebuild": {"thickness": tb.tables["thickness"], "checks": {k: tb.checks[k] for k in
                                                                   ("closed", "euler", "volume_mm3", "triangles")}},
        "roll_mm": 0.0,
        "budget_s": {"fit": t_fit - t0, "build": t_build - t_fit, "distances": time.perf_counter() - t_build}}
    return spec, report
