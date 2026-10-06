"""Inside-intervals of closed meshes along lines (WP4_DESIGN.md §3 'Line intervals'). numpy only.

A backend reports every crossing of the WHOLE line (cast from 400 mm behind the origin) with each part; the shared
layer here turns crossings into inside intervals by parity:

- crossings closer than DEDUPE_MM along one line and part are one crossing (a line through a shared edge or vertex is
  reported by every incident triangle in an exact float64 test, once by Embree);
- an odd count re-casts that line rotated by k * 0.05 deg about the station tangent (k = 1, 2, 3); still odd is
  UNMEASURED (None), which is never a pass and never moved.

Backends: `NumpyBackend` here (the reference: exact float64 Moller-Trumbore over a uniform grid), `BVHBackend` in
Blender (g3d/seat_bpy.py), `Open3DBackend` on the host (g3d_host/seat_check.py).

Parts must be closed (parity needs it). Culling is done per closed component by the callers, never per triangle:
dropping triangles outside a box opens the mesh, and a line that leaves the box could cross what was dropped.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

import numpy as np

FAR_MM = 400.0             # §3: lines are cast from 400 mm back
RECAST_DEG = 0.05          # §3: odd counts re-cast rotated 0.05 deg about t ...
RECAST_MAX = 3             # ... at most 3 times
DEDUPE_MM = 1e-4           # crossings this close along one line and part are the same crossing
MERGE_EPS_MM = 1e-4        # intervals of different parts closer than this merge (touching parts; float32 noise)


class LineIntervals(Protocol):
    """A geometric backend over a fixed list of parts (closed triangle meshes, world mm)."""

    n_parts: int

    def hits(self, origins: np.ndarray, dirs: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """All crossings of the infinite lines origins + t * dirs (dirs unit) with every part:
        (line index, part index, t) flat arrays, in any order."""
        ...


@dataclass
class LineResult:
    """Per line, per part: (k, 2) inside intervals [enter, leave] in mm along the line from its origin, or None when
    the part's parity stayed odd (UNMEASURED). `recast[i]` = how many re-casts line i needed (0 = none)."""
    intervals: list[list[np.ndarray | None]]
    recast: np.ndarray
    dirs: np.ndarray           # the directions finally used (re-cast lines are rotated)

    def unmeasured(self, i: int) -> bool:
        return any(iv is None for iv in self.intervals[i])


def rotate_about(d: np.ndarray, axis: np.ndarray, deg: float) -> np.ndarray:
    """Rodrigues rotation of unit vectors d about unit axes (row-wise); an axis parallel to d is replaced by a
    perpendicular one so the rotation always tilts the line."""
    d = np.asarray(d, np.float64)
    a = np.asarray(axis, np.float64).copy()
    a -= (a * d).sum(1, keepdims=True) * d
    ln = np.linalg.norm(a, axis=1)
    bad = ln < 1e-9
    if bad.any():
        alt = np.where(np.abs(d[bad, 0:1]) < 0.9, np.array([[1.0, 0, 0]]), np.array([[0, 1.0, 0]]))
        alt = np.cross(d[bad], alt)
        a[bad] = alt
        ln[bad] = np.linalg.norm(alt, axis=1)
    a /= ln[:, None]
    phi = np.radians(deg)
    out = d * np.cos(phi) + np.cross(a, d) * np.sin(phi) + a * (a * d).sum(1, keepdims=True) * (1 - np.cos(phi))
    return out / np.linalg.norm(out, axis=1, keepdims=True)


def _group(n_lines: int, n_parts: int, li, pi, t) -> list[list[np.ndarray]]:
    """Sorted, deduplicated crossing parameters per (line, part)."""
    out = [[np.zeros(0) for _ in range(n_parts)] for _ in range(n_lines)]
    if len(t) == 0:
        return out
    li = np.asarray(li, np.int64)
    pi = np.asarray(pi, np.int64)
    t = np.asarray(t, np.float64)
    order = np.lexsort((t, pi, li))
    li, pi, t = li[order], pi[order], t[order]
    key = li * n_parts + pi
    starts = np.flatnonzero(np.r_[True, key[1:] != key[:-1]])
    ends = np.r_[starts[1:], len(key)]
    for s, e in zip(starts.tolist(), ends.tolist()):
        tt = t[s:e]
        if len(tt) > 1:
            tt = tt[np.r_[True, np.diff(tt) > DEDUPE_MM]]
        out[int(li[s])][int(pi[s])] = tt
    return out


def line_intervals(backend: LineIntervals, origins, dirs, axes=None) -> LineResult:
    """Inside intervals per line and part, with the parity re-cast of §3 (rotation about `axes`, the station
    tangents; any perpendicular when None)."""
    O = np.asarray(origins, np.float64).reshape(-1, 3)
    D = np.asarray(dirs, np.float64).reshape(-1, 3)
    D = D / np.linalg.norm(D, axis=1, keepdims=True)
    A = np.zeros_like(D) if axes is None else np.asarray(axes, np.float64).reshape(-1, 3)
    n, P = len(O), backend.n_parts
    cross = _group(n, P, *backend.hits(O, D)) if n else []
    result: list[list[np.ndarray | None]] = [[None] * P for _ in range(n)]
    recast = np.zeros(n, np.int64)
    used = D.copy()
    todo = []
    for i in range(n):
        if all(len(c) % 2 == 0 for c in cross[i]):
            result[i] = [c.reshape(-1, 2) for c in cross[i]]
        else:
            todo.append(i)
    for k in range(1, RECAST_MAX + 1):
        if not todo:
            break
        idx = np.array(todo, np.int64)
        Dk = rotate_about(D[idx], A[idx], k * RECAST_DEG)
        ck = _group(len(idx), P, *backend.hits(O[idx], Dk))
        nxt = []
        for j, i in enumerate(todo):
            if all(len(c) % 2 == 0 for c in ck[j]):
                result[i] = [c.reshape(-1, 2) for c in ck[j]]
                recast[i] = k
                used[i] = Dk[j]
            else:
                nxt.append(i)
        todo = nxt
    for i in todo:                                     # still odd: keep the even parts, mark the odd ones None
        recast[i] = RECAST_MAX + 1
        result[i] = [c.reshape(-1, 2) if len(c) % 2 == 0 else None for c in cross[i]]
    return LineResult(result, recast, used)


def merge(ivs: Sequence[np.ndarray | None]) -> np.ndarray:
    """Union of interval lists (None entries ignored), sorted; touching intervals merge."""
    parts = [x for x in ivs if x is not None and len(x)]
    if not parts:
        return np.zeros((0, 2))
    a = np.concatenate(parts)
    a = a[np.lexsort((a[:, 1], a[:, 0]))]
    out = [a[0].copy()]
    for s, e in a[1:]:
        if s <= out[-1][1] + MERGE_EPS_MM:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append(np.array([s, e]))
    return np.array(out)


class Stacked:
    """Several backends as one: part indices of backend k follow those of backends 0..k-1. The seating tool keeps
    the frame parts in one backend and puts the (moving) lens in its own, so a re-measure after a write rebuilds the
    lens only."""

    def __init__(self, backends: Sequence[LineIntervals]):
        self.backends = list(backends)
        self.offsets = np.cumsum([0] + [b.n_parts for b in self.backends])[:-1].tolist()
        self.n_parts = int(sum(b.n_parts for b in self.backends))

    def hits(self, origins, dirs):
        L, P, T = [], [], []
        for off, b in zip(self.offsets, self.backends):
            li, pi, t = b.hits(origins, dirs)
            L.append(np.asarray(li, np.int64))
            P.append(np.asarray(pi, np.int64) + off)
            T.append(np.asarray(t, np.float64))
        if not L:
            z = np.zeros(0, np.int64)
            return z, z, np.zeros(0)
        return np.concatenate(L), np.concatenate(P), np.concatenate(T)


# ------------------------------------------------------------------------------------------------ numpy backend
class NumpyBackend:
    """Reference backend: float64 Moller-Trumbore on candidates from a uniform grid walked by a vectorised 3D DDA.
    Exact up to float64 rounding; numpy only (runs in Blender too)."""

    def __init__(self, parts: Sequence[tuple[np.ndarray, np.ndarray]], cell_mm: float | None = None,
                 max_cells: int = 4_000_000, chunk: int = 1024):
        As, E1s, E2s, pids = [], [], [], []
        for i, (V, T) in enumerate(parts):
            V = np.asarray(V, np.float64).reshape(-1, 3)
            T = np.asarray(T, np.int64).reshape(-1, 3)
            if len(T) == 0:
                continue
            a = V[T[:, 0]]
            As.append(a)
            E1s.append(V[T[:, 1]] - a)
            E2s.append(V[T[:, 2]] - a)
            pids.append(np.full(len(T), i, np.int64))
        self.n_parts = len(parts)
        self.chunk = chunk
        if not As:
            self.ntri = 0
            return
        self.A = np.concatenate(As)
        self.E1 = np.concatenate(E1s)
        self.E2 = np.concatenate(E2s)
        self.part = np.concatenate(pids)
        self.ntri = len(self.A)
        self.scale = np.linalg.norm(self.E1, axis=1) * np.linalg.norm(self.E2, axis=1)
        B, C = self.A + self.E1, self.A + self.E2
        tlo = np.minimum(np.minimum(self.A, B), C)
        thi = np.maximum(np.maximum(self.A, B), C)
        pad = 1e-3
        lo, hi = tlo.min(0) - 2 * pad, thi.max(0) + 2 * pad
        ext = np.maximum(hi - lo, 1e-6)
        vol = float(np.prod(ext))
        if cell_mm is None:
            cell_mm = max(0.25, (vol / (2.0 * self.ntri)) ** (1.0 / 3.0))
        cell_mm = max(cell_mm, (vol / max_cells) ** (1.0 / 3.0))
        self.cell = float(cell_mm)
        self.dims = np.maximum(np.ceil(ext / self.cell).astype(np.int64), 1)
        self.lo = lo
        self.hi = lo + self.dims * self.cell
        i0 = np.clip(np.floor((tlo - pad - lo) / self.cell).astype(np.int64), 0, self.dims - 1)
        i1 = np.clip(np.floor((thi + pad - lo) / self.cell).astype(np.int64), 0, self.dims - 1)
        sz = i1 - i0 + 1
        cnt = sz[:, 0] * sz[:, 1] * sz[:, 2]
        tri = np.repeat(np.arange(self.ntri), cnt)
        j = np.arange(int(cnt.sum())) - np.repeat(np.cumsum(cnt) - cnt, cnt)
        sx, sy = sz[tri, 0], sz[tri, 1]
        ix = i0[tri, 0] + j % sx
        iy = i0[tri, 1] + (j // sx) % sy
        iz = i0[tri, 2] + j // (sx * sy)
        key = (ix * self.dims[1] + iy) * self.dims[2] + iz
        order = np.argsort(key, kind="stable")
        self.cell_tris = tri[order]
        ncell = int(np.prod(self.dims))
        self.cell_ptr = np.r_[0, np.cumsum(np.bincount(key, minlength=ncell))]

    # -- grid walk ---------------------------------------------------------------------------------------------
    def _dda(self, o: np.ndarray, d: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        with np.errstate(divide="ignore", invalid="ignore"):
            inv = np.where(d != 0, 1.0 / np.where(d != 0, d, 1.0), np.inf)
            ta = (self.lo - o) * inv
            tb = (self.hi - o) * inv
        zero = d == 0
        inside_slab = (o >= self.lo) & (o <= self.hi)
        t_lo = np.where(zero, np.where(inside_slab, -np.inf, np.inf), np.minimum(ta, tb))
        t_hi = np.where(zero, np.where(inside_slab, np.inf, -np.inf), np.maximum(ta, tb))
        tnear = np.maximum(t_lo.max(1), -FAR_MM)
        tfar = t_hi.min(1)
        ok = tnear <= tfar
        p0 = o + np.where(ok, tnear, 0.0)[:, None] * d
        idx = np.clip(np.floor((p0 - self.lo) / self.cell).astype(np.int64), 0, self.dims - 1)
        step = np.sign(d).astype(np.int64)
        nb = self.lo + (idx + (step > 0)) * self.cell
        with np.errstate(invalid="ignore"):
            tmax = np.where(zero, np.inf, (nb - o) * inv)
        tdel = np.where(zero, np.inf, self.cell * np.abs(inv))
        lines = np.flatnonzero(ok)
        idx, tmax, tdel, step, tfar = idx[lines], tmax[lines], tdel[lines], step[lines], tfar[lines]
        out_l, out_c = [], []
        r = np.arange(len(lines))
        while len(lines):
            out_l.append(lines)
            out_c.append((idx[:, 0] * self.dims[1] + idx[:, 1]) * self.dims[2] + idx[:, 2])
            ax = tmax.argmin(1)
            rr = r[:len(lines)]
            tn = tmax[rr, ax]
            idx[rr, ax] += step[rr, ax]
            tmax[rr, ax] += tdel[rr, ax]
            keep = (tn <= tfar) & (idx[rr, ax] >= 0) & (idx[rr, ax] < self.dims[ax])
            lines, idx, tmax, tdel, step, tfar = (lines[keep], idx[keep], tmax[keep], tdel[keep], step[keep],
                                                  tfar[keep])
        if not out_l:
            return np.zeros(0, np.int64), np.zeros(0, np.int64)
        return np.concatenate(out_l), np.concatenate(out_c)

    def hits(self, origins, dirs):
        O = np.asarray(origins, np.float64).reshape(-1, 3)
        D = np.asarray(dirs, np.float64).reshape(-1, 3)
        if self.ntri == 0 or len(O) == 0:
            z = np.zeros(0, np.int64)
            return z, z, np.zeros(0)
        out_l, out_p, out_t = [], [], []
        for c0 in range(0, len(O), self.chunk):
            o, d = O[c0:c0 + self.chunk], D[c0:c0 + self.chunk]
            pl, pc = self._dda(o, d)
            if len(pl) == 0:
                continue
            cnt = self.cell_ptr[pc + 1] - self.cell_ptr[pc]
            nz = cnt > 0
            pl, pc, cnt = pl[nz], pc[nz], cnt[nz]
            if len(pl) == 0:
                continue
            rep_l = np.repeat(pl, cnt)
            j = np.arange(int(cnt.sum())) - np.repeat(np.cumsum(cnt) - cnt, cnt)
            tri = self.cell_tris[np.repeat(self.cell_ptr[pc], cnt) + j]
            key = np.unique(rep_l * self.ntri + tri)
            li, ti = key // self.ntri, key % self.ntri
            t, hit = self._mt(o[li], d[li], ti)
            out_l.append(li[hit] + c0)
            out_p.append(self.part[ti[hit]])
            out_t.append(t[hit])
        if not out_l:
            z = np.zeros(0, np.int64)
            return z, z, np.zeros(0)
        return np.concatenate(out_l), np.concatenate(out_p), np.concatenate(out_t)

    def _mt(self, o, d, ti):
        a, e1, e2 = self.A[ti], self.E1[ti], self.E2[ti]
        p = np.cross(d, e2)
        det = (e1 * p).sum(1)
        ok = np.abs(det) > 1e-12 * self.scale[ti]
        inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
        tv = o - a
        u = (tv * p).sum(1) * inv
        q = np.cross(tv, e1)
        v = (d * q).sum(1) * inv
        t = (e2 * q).sum(1) * inv
        eps = 1e-9
        hit = ok & (u >= -eps) & (v >= -eps) & (u + v <= 1.0 + eps) & (t >= -FAR_MM)
        return t, hit
