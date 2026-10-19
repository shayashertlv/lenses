"""g3d.polygon_section and g3d.check_round_corners (plan/archive/20261010/WP6_DESIGN.md §4; glasses3d/plan/archive/20261019/RULES_AUDIT.md §A
rows 1, 3). numpy only.

polygon_section(points, corners) builds a closed 2D section (u, v) whose every corner is `"sharp"`, `("chamfer", w)`
(the corner cut at w mm along both edges: two sharp corners) or `("round", r)` (a tangent arc of radius r with
max(8, ceil(8 theta / 90 deg)) segments, theta = the corner's turning angle; its vertices are smooth). Every output
vertex carries a sharp flag, so lofts keep crisp longitudinal edges only where the section has corners.

check_round_corners(points, sharp=None) applies the only shape rule kept from the old prompt: a rounded corner (a run
of smooth vertices whose turns are each < 45 deg and add up to >= 20 deg) needs >= 8 segments per 90 deg. It reports,
it never refuses. `loops_from_segments` chains mesh-section segments into closed loops for the mesh form.
"""
from __future__ import annotations

import math

import numpy as np

from .spec import ROUND_SEGMENTS_PER_90

ROUND_RUN_KINK_DEG = 45.0      # a single turn at or above this is a corner, not part of a rounded run
ROUND_RUN_MIN_DEG = 20.0       # a smooth run turning less than this in total is not a rounded corner
STRAIGHT_RATIO = 3.0           # a segment this many times longer than the one before ends a rounded run


class SectionError(ValueError):
    pass


def _turn(a, b, c) -> float:
    """Signed turning angle (rad) at b walking a -> b -> c (+ = left)."""
    u, v = b - a, c - b
    return math.atan2(u[0] * v[1] - u[1] * v[0], u[0] * v[0] + u[1] * v[1])


def round_segments(theta_rad: float) -> int:
    return max(ROUND_SEGMENTS_PER_90, int(math.ceil(ROUND_SEGMENTS_PER_90 * math.degrees(abs(theta_rad)) / 90.0
                                                    - 1e-9)))


def polygon_section(points, corners=None) -> dict:
    """{"points": (k, 2), "sharp": (k,) bool, "corner": (k,) index of the input corner each vertex came from,
    "kind": per vertex "sharp" | "chamfer" | "round"}. `corners`: one spec for every corner, a list (one per point)
    or {index: spec} (others sharp). Raises SectionError when a chamfer or round does not fit its edges."""
    P = np.asarray(points, np.float64)
    n = len(P)
    if n < 3 or P.ndim != 2 or P.shape[1] != 2 or not np.all(np.isfinite(P)):
        raise SectionError("points must be >= 3 finite (u, v) pairs")
    if corners is None or isinstance(corners, (str, tuple)):
        spec = [corners or "sharp"] * n
    elif isinstance(corners, dict):
        spec = [corners.get(i, "sharp") for i in range(n)]
    else:
        spec = list(corners)
        if len(spec) != n:
            raise SectionError(f"corners has {len(spec)} entries for {n} points")
    tang = np.zeros(n)                                   # distance consumed along each adjacent edge
    parsed = []
    for i, c in enumerate(spec):
        a, b, cc = P[i - 1], P[i], P[(i + 1) % n]
        th = _turn(a, b, cc)
        if c == "sharp" or c is None:
            parsed.append(("sharp", 0.0, th))
            continue
        if not (isinstance(c, (tuple, list)) and len(c) == 2 and c[0] in ("chamfer", "round")):
            raise SectionError(f"corner {i}: {c!r} must be 'sharp', ('chamfer', w) or ('round', r)")
        kind, val = c[0], float(c[1])
        if val <= 0:
            raise SectionError(f"corner {i}: {kind} size must be > 0")
        if abs(th) < 1e-9:
            parsed.append(("sharp", 0.0, th))
            continue
        t = val if kind == "chamfer" else val * math.tan(abs(th) / 2.0)
        tang[i] = t
        parsed.append((kind, val, th))
    for i in range(n):
        L = float(np.linalg.norm(P[(i + 1) % n] - P[i]))
        if tang[i] + tang[(i + 1) % n] > L + 1e-9:
            raise SectionError(f"edge {i}->{(i + 1) % n} ({L:.3f} mm) is shorter than its corners consume "
                               f"({tang[i]:.3f} + {tang[(i + 1) % n]:.3f} mm)")
    pts, sharp, src, kinds = [], [], [], []
    for i, (kind, val, th) in enumerate(parsed):
        a, b, c = P[i - 1], P[i], P[(i + 1) % n]
        if kind == "sharp":
            pts.append(b)
            sharp.append(True)
            src.append(i)
            kinds.append("sharp")
            continue
        u_in = (b - a) / np.linalg.norm(b - a)
        u_out = (c - b) / np.linalg.norm(c - b)
        t = tang[i]
        T1, T2 = b - u_in * t, b + u_out * t
        if kind == "chamfer":
            pts += [T1, T2]
            sharp += [True, True]
            src += [i, i]
            kinds += ["chamfer", "chamfer"]
            continue
        nl = np.array([-u_in[1], u_in[0]]) * (1.0 if th > 0 else -1.0)
        ctr = T1 + nl * val
        a0 = math.atan2(T1[1] - ctr[1], T1[0] - ctr[0])
        m = round_segments(th)
        for k in range(m + 1):
            ang = a0 + th * k / m
            q = ctr + val * np.array([math.cos(ang), math.sin(ang)])
            if k == m:
                q = T2
            pts.append(q)
            sharp.append(False)
            src.append(i)
            kinds.append("round")
    Q = np.asarray(pts)
    keep = []
    for k in range(len(Q)):
        if keep and np.linalg.norm(Q[k] - Q[keep[-1]]) < 1e-9:
            if sharp[k]:
                keep[-1] = k                     # merged round/chamfer tangent retains its sharp anchor
        else:
            keep.append(k)
    if len(keep) > 1 and np.linalg.norm(Q[keep[0]] - Q[keep[-1]]) < 1e-9:
        if sharp[keep[-1]]:
            keep[0] = keep[-1]
        keep.pop()
    return {"points": Q[keep], "sharp": np.asarray(sharp)[keep], "corner": np.asarray(src)[keep],
            "kind": [kinds[k] for k in keep]}


def check_round_corners(points, sharp=None, closed: bool = True) -> dict:
    """{"ok", "rounds": [{start, count_segments, turn_deg, needed_segments, ok}], "rule"}: report only."""
    P = np.asarray(points, np.float64)
    n = len(P)
    sharp = np.zeros(n, bool) if sharp is None else np.asarray(sharp, bool)
    original = np.arange(n)
    # A mesh section adds collinear points where the cutting plane crosses triangle diagonals. They are not
    # extra arc segments, nor breaks between rounded corners. Retain sharp anchors and open endpoints.
    keep = [i for i in range(n) if sharp[i] or (not closed and i in (0, n - 1))
            or abs(_turn(P[i - 1], P[i], P[(i + 1) % n])) > 1e-9]
    P, sharp, original = P[keep], sharp[keep], original[keep]
    n = len(P)
    idx = range(n) if closed else range(1, n - 1)
    turn = np.zeros(n)
    for i in idx:
        turn[i] = _turn(P[i - 1], P[i], P[(i + 1) % n])
    smooth = (~sharp) & (np.abs(np.degrees(turn)) < ROUND_RUN_KINK_DEG) & (np.abs(turn) > 1e-9)
    if not closed and n:
        smooth[0] = smooth[-1] = False
    rounds = []
    visited = np.zeros(n, bool)
    lengths = np.linalg.norm(np.roll(P, -1, axis=0) - P, axis=1)
    end = lengths > STRAIGHT_RATIO * np.roll(lengths, 1)
    starts = [i for i in range(n) if smooth[i] and (not smooth[i - 1] or end[i - 1])]
    whole_loop = bool(n and closed and smooth.all() and not end.any())
    if whole_loop:
        starts = [0]
    for s0 in starts:
        if visited[s0]:
            continue
        run = []
        i = s0
        while smooth[i % n] and not visited[i % n] and len(run) < n:
            visited[i % n] = True
            run.append(i % n)
            j = i % n
            i += 1
            if not closed and i >= n:
                break
            if end[j]:
                break                                  # a straight edge ends the rounded run
        if len(run) == 1 and not closed:
            pass
        tot = float(np.degrees(np.sum(turn[run])))
        if abs(tot) < ROUND_RUN_MIN_DEG:
            continue
        segs = len(run) if whole_loop else len(run) - 1  # a closed circle also has its closing segment
        need = max(1, int(math.ceil(ROUND_SEGMENTS_PER_90 * abs(tot) / 90.0 - 1e-9)))
        rounds.append({"start": int(original[run[0]]), "vertices": len(run), "segments": segs, "turn_deg": tot,
                       "needed_segments": need, "ok": segs >= need})
    return {"ok": all(r["ok"] for r in rounds), "rounds": rounds,
            "rule": ">= 8 segments per 90 deg on rounded corners (reported, never refused)"}


def loops_from_segments(seg: np.ndarray, tol: float = 1e-6) -> list[np.ndarray]:
    """Chain section segments (k, 2, 2) into closed loops (each (m, 2), not repeating the first point)."""
    seg = np.asarray(seg, np.float64)
    if len(seg) == 0:
        return []
    key = lambda p: (round(p[0] / tol), round(p[1] / tol))
    adj: dict = {}
    pts: dict = {}
    for a, b in seg:
        ka, kb = key(a), key(b)
        if ka == kb:
            continue
        pts[ka], pts[kb] = a, b
        adj.setdefault(ka, []).append(kb)
        adj.setdefault(kb, []).append(ka)
    used = set()
    loops = []
    for start in sorted(adj):
        if all((min(start, nb), max(start, nb)) in used for nb in adj[start]):
            continue
        loop = [start]
        prev, cur = None, start
        closed = False
        while True:
            nxt = None
            for nb in adj[cur]:
                e = (min(cur, nb), max(cur, nb))
                if nb != prev and e not in used:
                    nxt = nb
                    break
            if nxt is None:
                break
            used.add((min(cur, nxt), max(cur, nxt)))
            if nxt == start:
                closed = True
                break
            loop.append(nxt)
            prev, cur = cur, nxt
        if closed and len(loop) >= 3:
            loops.append(np.array([pts[k] for k in loop]))
    return loops


def check_round_corners_mesh(V, F, axis: int = 2, stations=None, n_stations: int = 9) -> dict:
    """The rule on a mesh: sections perpendicular to `axis` (default the arm's z), each loop checked."""
    from .measure import slice_d
    V = np.asarray(V, np.float64)
    order = [axis] + [k for k in range(3) if k != axis]
    W = V[:, order]
    lo, hi = float(W[:, 0].min()), float(W[:, 0].max())
    if stations is None:
        stations = np.linspace(lo, hi, n_stations + 2)[1:-1]
    out = []
    for s in stations:
        for loop in loops_from_segments(slice_d(W, np.asarray(F), float(s))):
            r = check_round_corners(loop)
            out.append({"station": float(s), "points": len(loop), **r})
    return {"ok": all(r["ok"] for r in out), "sections": out,
            "rule": ">= 8 segments per 90 deg on rounded corners (reported, never refused)"}
