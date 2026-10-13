"""Charts of one part: a strip chart for the tube body plus planar cap charts for everything else, all laid out as
seen from OUTSIDE (non-flipped), in millimetres. numpy only.

Ported from the scratch ``g3d-wp5/cov/charts.py`` (``face_adjacency``, ``planar_uv``, ``count_raster``,
``part_charts``); PORT_REVIEW section 10. Changes: connected components by a numpy union-find (no scipy), the strip's
largest component and the cap components use the same edge adjacency, faces of the strip that overlap other strip
faces at the target density are moved to the caps (iterated), a cap chart that overlaps itself is split by depth along
its axis until it does not, and every cap chart is scaled by 1 / (its 10 % quantile of n . d) (clamped to [1, 1.5])
so that its smallest singular value stays >= 0.6 x the target density (an orthographic chart compresses a face tilted
by n . d >= 0.577 to n . d of the density).

Orientation: a chart is "seen from outside" when the signed UV area (u right, v DOWN, rows) of every outward
counter-clockwise triangle is NEGATIVE; ``planar_uv`` gives that for any face with n . d > 0, and the strip is flipped
in t when its majority orientation is the other one.

A face stays in the strip only if it is unflipped (majority sign), anisotropy < 3, |grad s| > 0.5 (end caps have
|grad s| << 1) and in the largest component (WP5_DESIGN §6.1).
"""
from __future__ import annotations

import numpy as np

from . import strip as SU

AXES = np.array([[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1]], float)
AXIS_NAMES = ("+x", "-x", "+y", "-y", "+z", "-z")
STRIP_MAX_ANISO = 3.0
STRIP_MIN_GRAD = 0.5
CAP_SCALE_MAX = 1.5
CAP_SCALE_Q = 0.10
MAX_RESOLVE = 6


# ----------------------------------------------------------------------------- graph helpers (numpy only)
def face_edges(F_sub: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Edge-sharing face pairs of a triangle list: (a, b, edge vertex pairs (E, 2) sorted)."""
    F_sub = np.asarray(F_sub, np.int64)
    e = np.concatenate([F_sub[:, [0, 1]], F_sub[:, [1, 2]], F_sub[:, [2, 0]]])
    e.sort(1)
    fidx = np.tile(np.arange(len(F_sub)), 3)
    key = e[:, 0] * (np.int64(1) << 32) + e[:, 1]
    o = np.argsort(key, kind="stable")
    key, fidx, e = key[o], fidx[o], e[o]
    same = np.nonzero(key[1:] == key[:-1])[0]
    return fidx[same], fidx[same + 1], e[same]


def components(n: int, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Connected-component labels 0..k-1 (in order of first appearance) of n nodes with undirected edges (a, b)."""
    lab = np.arange(n, dtype=np.int64)
    a = np.asarray(a, np.int64)
    b = np.asarray(b, np.int64)
    if len(a):
        while True:
            la, lb = lab[a], lab[b]
            m = np.minimum(la, lb)
            new = lab.copy()
            np.minimum.at(new, la, m)
            np.minimum.at(new, lb, m)
            while True:                                   # pointer jumping
                nxt = new[new]
                if np.array_equal(nxt, new):
                    break
                new = nxt
            if np.array_equal(new, lab):
                break
            lab = new
    _, first, inv = np.unique(lab, return_index=True, return_inverse=True)
    order = np.argsort(np.argsort(first, kind="stable"), kind="stable")
    return order[inv]


def subgraph_components(sel: np.ndarray, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Component labels of the nodes ``sel`` (indices) under edges (a, b) restricted to them."""
    sel = np.asarray(sel, np.int64)
    pos = -np.ones(int(max(a.max(initial=-1), b.max(initial=-1), sel.max(initial=-1))) + 1, np.int64)
    pos[sel] = np.arange(len(sel))
    keep = (pos[a] >= 0) & (pos[b] >= 0)
    return components(len(sel), pos[a[keep]], pos[b[keep]])


# ----------------------------------------------------------------------------- chart maps
def planar_uv(P: np.ndarray, d: np.ndarray) -> np.ndarray:
    """Orthographic projection along the outward axis d, seen from outside: right = up x d, v rows go down."""
    up = np.array([0.0, 1.0, 0.0]) if abs(d[1]) < 0.9 else np.array([0.0, 0.0, -1.0])
    up = up - (up @ d) * d
    up /= np.linalg.norm(up)
    right = np.cross(up, d)
    return np.stack([P @ right, -(P @ up)], -1)


def signed_area(uv: np.ndarray) -> np.ndarray:
    """Signed area of (K, 3, 2) triangles (u right, v down)."""
    return 0.5 * ((uv[:, 1, 0] - uv[:, 0, 0]) * (uv[:, 2, 1] - uv[:, 0, 1])
                  - (uv[:, 1, 1] - uv[:, 0, 1]) * (uv[:, 2, 0] - uv[:, 0, 0]))


def texel_pairs(tri: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(texel row, texel col, triangle index) of every texel centre strictly inside each triangle (texel coordinates:
    texel (i, j) centre at (j + 0.5, i + 0.5)); shared edges are covered by neither side."""
    tri = np.asarray(tri, np.float64)
    lo = np.floor(tri.min(1) - 0.5).astype(np.int64)
    hi = np.ceil(tri.max(1) - 0.5).astype(np.int64)
    rows, cols, ids = [], [], []
    for k in range(len(tri)):
        x0, y0 = lo[k]
        x1, y1 = hi[k]
        if x1 < x0 or y1 < y0:
            continue
        (ax, ay), (bx, by), (cx, cy) = tri[k]
        den = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
        if abs(den) < 1e-12:
            continue
        X, Y = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
        l0 = ((by - cy) * (X - cx) + (cx - bx) * (Y - cy)) / den
        l1 = ((cy - ay) * (X - cx) + (ax - cx) * (Y - cy)) / den
        m = (l0 > 1e-9) & (l1 > 1e-9) & (1 - l0 - l1 > 1e-9)
        if m.any():
            yy, xx = np.nonzero(m)
            rows.append(yy + y0)
            cols.append(xx + x0)
            ids.append(np.full(len(yy), k, np.int64))
    if not rows:
        z = np.zeros(0, np.int64)
        return z, z, z
    return np.concatenate(rows), np.concatenate(cols), np.concatenate(ids)


def overlapping_triangles(tri: np.ndarray) -> np.ndarray:
    """Indices of triangles that share a covered texel centre with another triangle (texel coordinates)."""
    r, c, k = texel_pairs(tri)
    if not len(k):
        return np.zeros(0, np.int64)
    key = r * (np.int64(1) << 31) + c
    o = np.argsort(key, kind="stable")
    key, k = key[o], k[o]
    dup = np.zeros(len(key), bool)
    same = key[1:] == key[:-1]
    dup[1:] |= same
    dup[:-1] |= same
    return np.unique(k[dup])


def face_gradients(Pt: np.ndarray, s_c: np.ndarray) -> np.ndarray:
    """|grad s| per face (s per corner)."""
    e1 = Pt[:, 1] - Pt[:, 0]
    e2 = Pt[:, 2] - Pt[:, 0]
    ds1 = s_c[:, 1] - s_c[:, 0]
    ds2 = s_c[:, 2] - s_c[:, 0]
    G = np.stack([e1, e2], 1)
    M = np.einsum("kid,kjd->kij", G, G)
    rhs = np.stack([ds1, ds2], 1)
    ok = np.abs(np.linalg.det(M)) > 1e-12
    coef = np.zeros((len(Pt), 2))
    coef[ok] = np.linalg.solve(M[ok], rhs[ok][..., None])[..., 0]
    g = coef[:, :1] * e1 + coef[:, 1:] * e2
    return np.linalg.norm(g, axis=1)


# ----------------------------------------------------------------------------- one part
def cap_charts(Pt: np.ndarray, fn: np.ndarray, area: np.ndarray, cap: np.ndarray, a: np.ndarray, b: np.ndarray,
               tx: float) -> list[tuple[str, np.ndarray, np.ndarray]]:
    """Planar charts of the faces ``cap`` (local ids): nearest axis, connected components, split by depth while a
    chart overlaps itself at ``tx`` texels/mm, scaled for density."""
    out = []
    if not len(cap):
        return out
    lab_axis = np.argmax(fn[cap] @ AXES.T, 1)
    for ax in range(6):
        cs = cap[lab_axis == ax]
        if not len(cs):
            continue
        lb = subgraph_components(cs, a, b)
        for c in range(int(lb.max()) + 1):
            stack = [cs[lb == c]]
            while stack:
                fl = stack.pop()
                d = AXES[ax]
                uv = planar_uv(Pt[fl].reshape(-1, 3), d).reshape(-1, 3, 2)
                ndot = np.clip(fn[fl] @ d, 0.05, 1.0)
                q = SU.wquant(ndot, area[fl], [CAP_SCALE_Q])[0]
                sc = float(np.clip(1.0 / max(q, 1e-6), 1.0, CAP_SCALE_MAX))
                uv = uv * sc
                bad = overlapping_triangles(uv * tx) if len(fl) > 1 else np.zeros(0, np.int64)
                if len(bad) and len(fl) > 1:
                    depth = Pt[fl].mean(1) @ d
                    med = np.median(depth)
                    lo_ = fl[depth <= med]
                    hi_ = fl[depth > med]
                    if len(lo_) == 0 or len(hi_) == 0:      # cannot split by depth: one chart per face
                        stack += [fl[i:i + 1] for i in range(len(fl))]
                        continue
                    for part_ in (lo_, hi_):                 # keep each half's own connected pieces
                        lb2 = subgraph_components(part_, a, b)
                        stack += [part_[lb2 == k] for k in range(int(lb2.max()) + 1)]
                    continue
                out.append((f"cap{AXIS_NAMES[ax]}", fl, uv))
    return out


def part_charts(V: np.ndarray, F: np.ndarray, faces: np.ndarray, *, kind: str, tx: float, ref=None, cut_dir=None,
                max_aniso: float = STRIP_MAX_ANISO, min_grad: float = STRIP_MIN_GRAD) -> tuple[list, dict]:
    """Charts of one part: [(chart kind, LOCAL face indices into ``faces``, uv (k, 3, 2) mm)], info.
    ``kind`` = 'strip' (strip + caps) or 'caps' (planar charts only)."""
    V = np.asarray(V, np.float64)
    F = np.asarray(F, np.int64)
    faces = np.asarray(faces, np.int64)
    if kind not in ("strip", "caps"):
        raise ValueError(f"unknown chart kind {kind!r}")
    Pt = V[F[faces]]
    fn = np.cross(Pt[:, 1] - Pt[:, 0], Pt[:, 2] - Pt[:, 0])
    a3 = 0.5 * np.linalg.norm(fn, axis=1)
    if not np.isfinite(a3).all():
        raise ValueError("non-finite triangle area in print layout")
    # Keep degenerate faces in the index map, but do not ask the strip fit or
    # area-weighted cap scaling to infer a surface from them. Their collapsed
    # UVs remain visible to the unchanged A1 zero-area refusal.
    zero = np.flatnonzero(a3 == 0)
    if len(zero):
        good = np.flatnonzero(a3 > 0)
        charts, info = (part_charts(V, F, faces[good], kind=kind, tx=tx, ref=ref, cut_dir=cut_dir,
                                   max_aniso=max_aniso, min_grad=min_grad) if len(good)
                        else ([], {"kind": kind, "area_mm2": 0.0, "cap_faces": 0}))
        charts = [(label, good[ids], uv) for label, ids, uv in charts]
        charts.append(("zero_area", zero, np.zeros((len(zero), 3, 2), np.float64)))
        info.update(faces=int(len(faces)), zero_area_faces=int(len(zero)),
                    zero_area_face_ids=faces[zero].tolist(), n_charts=len(charts))
        return charts, info
    fn = fn / (np.linalg.norm(fn, axis=1, keepdims=True) + 1e-12)
    a, b, _ = face_edges(F[faces])
    info = {"kind": kind, "faces": int(len(faces)), "area_mm2": float(a3.sum())}
    charts = []
    strip = np.zeros(len(faces), bool)
    if kind == "strip":
        sp = SU.strip_param(V, F, faces, ref, cut_dir)
        sig, _a3, auv = SU.distortion(V, F, faces, sp["s"], sp["t"])
        an = sig[:, 0] / np.maximum(sig[:, 1], 1e-9)
        maj = np.sign(np.sum(np.sign(auv) * a3 * (an < 2)))
        maj = maj if maj != 0 else 1.0
        gn = face_gradients(Pt, sp["s"])
        strip = (np.sign(auv) == maj) & (an < max_aniso) & (gn > min_grad)
        uv_all = np.stack([sp["s"], sp["t"] * (-1.0 if maj > 0 else 1.0)], -1)   # majority -> negative area
        moved = 0
        for _ in range(MAX_RESOLVE):
            sub = np.nonzero(strip)[0]
            if not len(sub):
                break
            lab = subgraph_components(sub, a, b)
            big = int(np.argmax(np.bincount(lab, weights=a3[sub])))
            keep = np.zeros(len(faces), bool)
            keep[sub[lab == big]] = True
            strip = keep
            sub = np.nonzero(strip)[0]
            bad = overlapping_triangles(uv_all[sub] * tx)
            if not len(bad):
                break
            strip[sub[bad]] = False
            moved += int(len(bad))
        sub = np.nonzero(strip)[0]
        if len(sub):
            charts.append(("strip", sub, uv_all[sub]))
        info.update({"strip_area_share": float(a3[strip].sum() / a3.sum()), "strip_overlap_moved": moved,
                     "cut_theta_deg": float(np.degrees(sp["cut_theta"])), "length_mm": sp["length"],
                     "perimeter_median_mm": sp["perim_med"], "axis": [float(x) for x in sp["axis"]]})
    elif kind != "caps":
        raise ValueError(f"unknown chart kind {kind!r}")
    cap = np.nonzero(~strip)[0]
    charts += cap_charts(Pt, fn, a3, cap, a, b, tx)
    info["n_charts"] = len(charts)
    info["cap_faces"] = int(len(cap))
    return charts, info
