"""g3d.loft_sections (plan/archive/20261010/WP6_DESIGN.md §4; RULES_AUDIT §A row 3): per-station lofts for metal,
wire and tapering temples. numpy only.

loft_sections(stations, path=None, caps=True):
- stations: [{"t": distance along the path (mm), "points": (k, 2) section (u = lateral, v = up), "sharp": (k,) flags
  (optional; a polygon_section result can be passed as the station itself plus "t")}], t strictly increasing;
- path: a 3D polyline [[x, y, z], ...] (default the -Z axis from the origin: P(t) = (0, 0, -t)); each section is placed
  in the plane perpendicular to the path at t, u along B = T x N (lateral), v along N (world +Y made orthogonal to T);
- equal counts: every station must have the same number of sharp anchors (in order); between consecutive anchors each
  station's chain keeps all original vertices and splits its longest segments to the largest count there;
- quads between stations are split along their shorter diagonal; a longitudinal edge at a sharp anchor is sharp while
  its dihedral >= 6 deg; caps are ear-clipped polygons;
- report: per strip (between section vertices j, j+1) the largest quad twist (distance of the 4th corner from the
  plane of the other three), closed / Euler / volume.
"""
from __future__ import annotations

import math

import numpy as np

from .arrange import ear_clip, poly_area

CREASE_SHARP_DEG = 6.0


class LoftError(ValueError):
    pass


def _resample(chain: np.ndarray, m: int) -> np.ndarray:
    """m + 1 points: retain every authored vertex, bisecting the longest segment until counts agree."""
    out = np.asarray(chain, np.float64).copy()
    while len(out) < m + 1:
        i = int(np.argmax(np.linalg.norm(np.diff(out, axis=0), axis=1)))
        out = np.insert(out, i + 1, 0.5 * (out[i] + out[i + 1]), axis=0)
    return out


def equalize(stations: list) -> tuple[list[np.ndarray], np.ndarray]:
    """Subdivide every station to one vertex count between sharp anchors. Returns (sections, sharp flags)."""
    secs = []
    for st in stations:
        P = np.asarray(st["points"], np.float64)
        sh = np.asarray(st.get("sharp", np.ones(len(P), bool)), bool)
        if poly_area(P) < 0:
            P, sh = P[::-1].copy(), sh[::-1].copy()
        if not sh.any():
            sh = sh.copy()
            sh[int(np.argmin(P[:, 0] + 1e-6 * P[:, 1]))] = True
        first = int(np.nonzero(sh)[0][0])
        secs.append((np.roll(P, -first, axis=0), np.roll(sh, -first)))
    na = {int(sh.sum()) for _, sh in secs}
    if len(na) != 1:
        raise LoftError(f"stations have different numbers of sharp anchors {sorted(na)}")
    k = na.pop()
    spans = []
    for P, sh in secs:
        a = list(np.nonzero(sh)[0]) + [len(P)]
        spans.append([np.vstack([P[a[j]:a[j + 1] + 1]] if a[j + 1] < len(P) else [P[a[j]:], P[:1]])
                      for j in range(k)])
    counts = [max(len(sp[j]) - 1 for sp in spans) for j in range(k)]
    out = []
    for sp in spans:
        pts = []
        for j in range(k):
            ch = sp[j]
            r = ch if len(ch) - 1 == counts[j] else _resample(ch, counts[j])
            pts.append(r[:-1])
        out.append(np.vstack(pts))
    flags = np.zeros(len(out[0]), bool)
    pos = 0
    for j in range(k):
        flags[pos] = True
        pos += counts[j]
    return out, flags


def _path_frames(path, ts):
    if path is None:
        P = np.stack([np.zeros(len(ts)), np.zeros(len(ts)), -np.asarray(ts, float)], axis=1)
        T = np.tile([0.0, 0.0, -1.0], (len(ts), 1))
    else:
        Q = np.asarray(path, np.float64)
        s = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(Q, axis=0), axis=1))]
        if np.any(np.asarray(ts) < -1e-9) or np.any(np.asarray(ts) > s[-1] + 1e-9):
            raise LoftError(f"station t outside the path length [0, {s[-1]:.3f}]")
        P = np.stack([np.interp(ts, s, Q[:, k]) for k in range(3)], axis=1)
        h = max(1e-3, 1e-4 * s[-1])
        Pa = np.stack([np.interp(np.clip(np.asarray(ts) - h, 0, s[-1]), s, Q[:, k]) for k in range(3)], axis=1)
        Pb = np.stack([np.interp(np.clip(np.asarray(ts) + h, 0, s[-1]), s, Q[:, k]) for k in range(3)], axis=1)
        T = Pb - Pa
        T /= np.linalg.norm(T, axis=1, keepdims=True)
    up = np.array([0.0, 1.0, 0.0])
    N = up[None, :] - (T @ up)[:, None] * T
    if np.any(np.linalg.norm(N, axis=1) < 1e-9):
        raise LoftError("the path runs vertical: no up direction for the sections")
    N /= np.linalg.norm(N, axis=1, keepdims=True)
    B = np.cross(T, N)
    return P, T, N, B


def loft_sections(stations: list, path=None, caps: bool = True) -> dict:
    if len(stations) < 2:
        raise LoftError("need >= 2 stations")
    ts = np.array([float(s["t"]) for s in stations])
    if np.any(np.diff(ts) <= 0):
        raise LoftError("station t must strictly increase")
    secs, flags = equalize(stations)
    m = len(secs[0])
    P, T, N, B = _path_frames(path, ts)
    V = np.vstack([P[i][None, :] + secs[i][:, 0:1] * B[i][None, :] + secs[i][:, 1:2] * N[i][None, :]
                   for i in range(len(ts))])
    F, quads, twist = [], [], np.zeros(m)
    for i in range(len(ts) - 1):
        for j in range(m):
            a, b = i * m + j, i * m + (j + 1) % m
            c, d = (i + 1) * m + (j + 1) % m, (i + 1) * m + j
            q = [a, d, c, b]                                   # outward for a CCW section looking down -T
            quads.append(q)
            if np.linalg.norm(V[a] - V[c]) <= np.linalg.norm(V[d] - V[b]):
                F += [[a, d, c], [a, c, b]]
            else:
                F += [[a, d, b], [d, c, b]]
            n = np.cross(V[d] - V[a], V[b] - V[a])
            nn = np.linalg.norm(n)
            twist[j] = max(twist[j], abs(float((V[c] - V[a]) @ n)) / nn if nn > 0 else 0.0)
    if caps:
        for i, flip in ((0, True), (len(ts) - 1, False)):
            ids = list(range(i * m, i * m + m))
            for tri in ear_clip(secs[i], list(range(m))):
                t3 = [ids[tri[0]], ids[tri[1]], ids[tri[2]]]
                F.append(t3[::-1] if not flip else t3)
    F = np.asarray(F, np.int64)
    a, b, c = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
    vol = float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6.0)
    if caps and vol < 0:
        F = F[:, ::-1]
        quads = [q[::-1] for q in quads]
        vol = -vol
    sharp = []
    for j in np.nonzero(flags)[0]:
        for i in range(len(ts) - 1):
            e = (i * m + j, (i + 1) * m + j)
            q1, q2 = quads[i * m + (j - 1) % m], quads[i * m + j]
            n1 = np.cross(V[q1[1]] - V[q1[0]], V[q1[3]] - V[q1[0]])
            n2 = np.cross(V[q2[1]] - V[q2[0]], V[q2[3]] - V[q2[0]])
            cosang = float(n1 @ n2 / max(np.linalg.norm(n1) * np.linalg.norm(n2), 1e-300))
            if math.degrees(math.acos(max(-1.0, min(1.0, cosang)))) >= CREASE_SHARP_DEG:
                sharp.append(e)
    E = {}
    for t in F:
        for u, v in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0])):
            E[(int(u), int(v))] = E.get((int(u), int(v)), 0) + 1
    open_ = sum(1 for (u, v) in E if (v, u) not in E)
    und = {(min(u, v), max(u, v)) for (u, v) in E}
    chi = len(np.unique(F)) - len(und) + len(F)
    return {"V": V, "F": F, "quads": quads, "sharp_edges": sharp, "sharp_anchor": flags,
            "strip_twist_mm": twist.tolist(), "closed": open_ == 0, "euler": int(chi), "volume_mm3": vol,
            "vertices_per_section": m}
