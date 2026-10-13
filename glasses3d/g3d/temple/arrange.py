"""2D geometry in the temple's (d, y) chart (plan/WP6_FACETS.md §2.2). numpy only.

`build_arrangement(boundary, segments)` cuts the outline polygon by every segment (facet edges, chamfer lines, station
chords), splits edges at every crossing or touching point, merges vertices closer than DEDUPE_MM, and walks the
half-edge structure into faces. Every bounded face is a simple CCW polygon of the planar subdivision; the faces tile
the outline (checked to TILE_TOL_MM2). A component that does not touch the outline (a facet floating inside it) is
joined to it by full-height station chords through its first and last vertex in d, and a face that would visit a
vertex twice (a facet corner touching the outline or another facet at one point) gets a chord through that vertex, so
no face has a hole and none pinches at a single vertex (every face is a simple polygon visiting each vertex once). A
face whose triangulation is empty (corners on one line) is E_TRIANGULATE.

Pairwise crossings are computed only for segments whose bounding boxes overlap (`_box_pairs`, a sweep in d), and
segments whose tag kind the caller lists in `inside` (the builder: stations, roll and chamfer lines) skip the clipping.

`ear_clip` triangulates one simple CCW polygon (collinear vertices allowed), always the fattest ear first; polygons of
up to SMALL_POLY corners run the same rule element by element; when that fails or leaves a boundary edge uncovered,
`_ear_clip_robust` (a distance-scaled containment test) triangulates it instead.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .spec import DEDUPE_MM, TILE_TOL_MM2, TempleError

PARALLEL_SIN = 1e-9      # segments whose directions differ by less than this (sine) are parallel


def poly_area(P) -> float:
    P = np.asarray(P, np.float64)
    x, y = P[:, 0], P[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(np.roll(x, -1), y))


def point_in_poly(pt, P) -> bool:
    """Even-odd test (a point exactly on an edge may go either way; callers pass interior points)."""
    x, y = float(pt[0]), float(pt[1])
    P = np.asarray(P, np.float64)
    x0, y0 = P[:, 0], P[:, 1]
    x1, y1 = np.roll(x0, -1), np.roll(y0, -1)
    cond = (y0 > y) != (y1 > y)
    with np.errstate(divide="ignore", invalid="ignore"):
        xc = x0 + (y - y0) * (x1 - x0) / (y1 - y0)
    return bool(np.count_nonzero(cond & (xc > x)) % 2)


def simple_polygon(P, tol: float = 1e-12) -> tuple[bool, tuple | None]:
    """True when no two non-adjacent edges of the closed polygon touch or cross."""
    P = np.asarray(P, np.float64)
    n = len(P)
    A, B = P, np.roll(P, -1, axis=0)
    for i in range(n):
        for j in range(i + 2, n):
            if i == 0 and j == n - 1:
                continue
            if _touch(A[i], B[i], A[j], B[j], tol):
                return False, (i, j)
    return True, None


def _touch(p, q, a, b, tol) -> bool:
    r, s = q - p, b - a
    den = r[0] * s[1] - r[1] * s[0]
    w = a - p
    if abs(den) < 1e-15:
        if abs(r[0] * w[1] - r[1] * w[0]) > tol * max(1.0, math.hypot(*r)):
            return False
        L = float(r @ r)
        if L == 0.0:                                 # p -> q is a point (two coincident corners): on a -> b or not
            return _touch(a, b, p, p, tol) if float(s @ s) > 0.0 else bool(math.hypot(*w) <= tol)
        t0, t1 = sorted(((a - p) @ r / L, (b - p) @ r / L))
        return t1 >= -tol and t0 <= 1 + tol
    t = (w[0] * s[1] - w[1] * s[0]) / den
    u = (w[0] * r[1] - w[1] * r[0]) / den
    return -tol <= t <= 1 + tol and -tol <= u <= 1 + tol


def clip_segment(p, q, P) -> list[tuple[float, float]]:
    """Parameter intervals [s0, s1] of segment p -> q lying inside the polygon P (boundary included)."""
    p, q = np.asarray(p, np.float64), np.asarray(q, np.float64)
    r = q - p
    L = math.hypot(*r)
    if L < DEDUPE_MM:
        return []
    A = np.asarray(P, np.float64)
    B = np.roll(A, -1, axis=0)
    s = B - A
    den = r[0] * s[:, 1] - r[1] * s[:, 0]
    w = A - p
    ok = np.abs(den) > PARALLEL_SIN * L * np.hypot(s[:, 0], s[:, 1])
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where(ok, (w[:, 0] * s[:, 1] - w[:, 1] * s[:, 0]) / den, np.nan)
        u = np.where(ok, (w[:, 0] * r[1] - w[:, 1] * r[0]) / den, np.nan)
    eps = DEDUPE_MM / L
    hit = ok & (t >= -eps) & (t <= 1 + eps) & (u >= -1e-12) & (u <= 1 + 1e-12)
    ts = set(np.clip(t[hit], 0.0, 1.0).tolist()) | {0.0, 1.0}
    # collinear overlaps contribute their endpoints
    for i in np.nonzero(~ok)[0]:
        if abs(r[0] * w[i, 1] - r[1] * w[i, 0]) / L < DEDUPE_MM:
            for e in (A[i], B[i]):
                tt = float((e - p) @ r) / (L * L)
                if -eps <= tt <= 1 + eps:
                    ts.add(min(max(tt, 0.0), 1.0))
    ts = sorted(ts)
    out = []
    for a, b in zip(ts[:-1], ts[1:]):
        if (b - a) * L < DEDUPE_MM:
            continue
        m = p + 0.5 * (a + b) * r
        if point_in_poly(m, A) or _on_boundary(m, A, B):
            if out and abs(out[-1][1] - a) * L < DEDUPE_MM:
                out[-1] = (out[-1][0], b)
            else:
                out.append((a, b))
    return out


def _on_boundary(m, A, B) -> bool:
    s = B - A
    L2 = np.einsum("ij,ij->i", s, s)
    t = np.clip(np.einsum("ij,ij->i", m - A, s) / np.maximum(L2, 1e-30), 0.0, 1.0)
    d = np.hypot(*(A + t[:, None] * s - m).T)
    return bool(np.min(d) < DEDUPE_MM)


class VertexPool:
    """Vertices merged within DEDUPE_MM (grid hash with neighbour cells; first come keeps its coordinates)."""

    def __init__(self, tol: float = DEDUPE_MM):
        self.tol = tol
        self.pts: list[tuple[float, float]] = []
        self.grid: dict[tuple[int, int], list[int]] = {}

    def add(self, p) -> int:
        x, y = float(p[0]), float(p[1])
        gx, gy = int(math.floor(x / self.tol)), int(math.floor(y / self.tol))
        for i in range(gx - 1, gx + 2):
            for j in range(gy - 1, gy + 2):
                for k in self.grid.get((i, j), ()):
                    q = self.pts[k]
                    if abs(q[0] - x) <= self.tol and abs(q[1] - y) <= self.tol:
                        return k
        self.pts.append((x, y))
        self.grid.setdefault((gx, gy), []).append(len(self.pts) - 1)
        return len(self.pts) - 1

    def array(self) -> np.ndarray:
        return np.asarray(self.pts, np.float64).reshape(-1, 2)


@dataclass
class Arrangement:
    V: np.ndarray                     # (n, 2) vertex (d, y)
    faces: list                       # bounded faces: CCW vertex-id lists
    areas: np.ndarray                 # their areas (mm^2)
    edge_tags: dict                   # (a, b) a < b -> sorted tuple of tags
    boundary_loop: list               # outline vertices in CCW order
    interior_pts: np.ndarray          # (faces, 2) a point strictly inside each face
    face_tris: list = field(default_factory=list)       # each face's ear clipping (computed once)
    chords_added: list = field(default_factory=list)

    def edge_faces(self) -> dict:
        """(a, b) directed half-edge -> face index (faces are CCW: the face lies on the half-edge's left)."""
        out = {}
        for fi, f in enumerate(self.faces):
            for k in range(len(f)):
                out[(f[k], f[(k + 1) % len(f)])] = fi
        return out


def build_arrangement(boundary: np.ndarray, segments: list, chord_fn=None, inside: tuple = ()) -> Arrangement:
    """Planar subdivision of the CCW polygon `boundary` by `segments` [(p, q, tag)]. `chord_fn(d)` returns the
    full-height station segment at d (used to join floating components); without it floating parts are refused."""
    boundary = np.asarray(boundary, np.float64)
    if poly_area(boundary) <= 0:
        raise TempleError("E_OUTLINE", "the outline polygon is not counter-clockwise in (d, y)")
    extra: list = []
    for _ in range(4):
        arr = _build(boundary, list(segments) + extra, inside)
        if isinstance(arr, Arrangement):
            arr.chords_added = [t for _, _, t in extra]
            return arr
        why, pts = arr
        if chord_fn is None:
            raise TempleError("E_TRIANGULATE", "a facet floats inside the outline without touching it"
                              if why == "float" else "a face of the arrangement pinches at a vertex (visits it twice)",
                              at=[float(v) for v in pts[0]])
        for pt in pts:          # floating: chords through the component's first and last vertex in d; a pinch (a
            p, q = chord_fn(float(pt[0]))      # facet corner touching the outline or another facet at one point):
            extra.append((p, q, ("station", float(pt[0]))))     # a chord through it splits the face there
    raise TempleError("E_TRIANGULATE", "could not join every component of the arrangement to the outline")


def _build(boundary: np.ndarray, segments: list, inside: tuple = ()):
    nb = len(boundary)
    edges = [(boundary[i], boundary[(i + 1) % nb], ("boundary",)) for i in range(nb)]
    for p, q, tag in segments:
        p, q = np.asarray(p, np.float64), np.asarray(q, np.float64)
        if tag[0] in inside:                           # the caller guarantees it lies inside: no clipping
            if math.hypot(*(q - p)) >= DEDUPE_MM:
                edges.append((p, q, tuple(tag)))
            continue
        for s0, s1 in clip_segment(p, q, boundary):
            edges.append((p + s0 * (q - p), p + s1 * (q - p), tuple(tag)))
    m = len(edges)
    A = np.array([e[0] for e in edges])
    B = np.array([e[1] for e in edges])
    R = B - A
    L = np.hypot(R[:, 0], R[:, 1])
    params: list[list[float]] = [[0.0, 1.0] for _ in range(m)]
    # pairwise crossings / touchings, vectorised over the pairs whose bounding boxes overlap (both orders)
    ii, jj = _box_pairs(A, B)
    Ri, Rj = R[ii], R[jj]
    den = Ri[:, 0] * Rj[:, 1] - Ri[:, 1] * Rj[:, 0]
    W = A[jj] - A[ii]
    ok = np.abs(den) > PARALLEL_SIN * L[ii] * L[jj]
    with np.errstate(divide="ignore", invalid="ignore"):
        t = (W[:, 0] * Rj[:, 1] - W[:, 1] * Rj[:, 0]) / den      # on edge i
        u = (W[:, 0] * Ri[:, 1] - W[:, 1] * Ri[:, 0]) / den      # on edge j
    ei = DEDUPE_MM / np.maximum(L[ii], 1e-30)
    ej = DEDUPE_MM / np.maximum(L[jj], 1e-30)
    hit = ok & (t >= -ei) & (t <= 1 + ei) & (u >= -ej) & (u <= 1 + ej)
    for i, tt in zip(ii[hit].tolist(), np.clip(t[hit], 0.0, 1.0).tolist()):
        params[i].append(tt)
    # collinear overlaps: endpoints of one inside the other
    for k in np.nonzero(~ok)[0].tolist():
        i, j = int(ii[k]), int(jj[k])
        crossw = R[i, 0] * W[k, 1] - R[i, 1] * W[k, 0]
        if abs(crossw) / max(L[i], 1e-30) > DEDUPE_MM:
            continue
        for e in (A[j], B[j]):
            tt = float((e - A[i]) @ R[i]) / max(L[i] ** 2, 1e-30)
            if -ei[k] <= tt <= 1 + ei[k]:
                params[i].append(min(max(tt, 0.0), 1.0))
    pool = VertexPool()
    for p in boundary:
        pool.add(p)
    und: dict[tuple[int, int], set] = {}
    bl: list[int] = []
    for k, (a, b, tag) in enumerate(edges):
        ts = sorted(set(params[k]))
        ids = []
        for tt in ts:
            v = pool.add(a + tt * (b - a))
            if not ids or ids[-1] != v:
                ids.append(v)
        if k < nb:                                                  # the outline, in CCW order
            for v in ids:
                if not bl or bl[-1] != v:
                    bl.append(v)
        for va, vb in zip(ids[:-1], ids[1:]):
            if va != vb:
                und.setdefault((min(va, vb), max(va, vb)), set()).add(tag)
    if len(bl) > 1 and bl[0] == bl[-1]:
        bl.pop()
    V = pool.array()
    bloop = bl
    # components: every vertex must connect to the boundary
    parent = list(range(len(V)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for a, b in und:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)
    root = find(bloop[0])
    used = sorted({v for e in und for v in e})
    floating = [v for v in used if find(v) != root]
    if floating:
        comps = {}
        for v in floating:
            comps.setdefault(find(v), []).append(v)
        ends = [(min(comp, key=lambda v: (V[v, 0], V[v, 1])),
                 max(comp, key=lambda v: (V[v, 0], V[v, 1]))) for comp in comps.values()]
        ends.sort(key=lambda pair: tuple(V[pair[0]]))
        # Join every component in this pass; the retry budget handles pinches, not a facet-count limit.
        return "float", [V[v] for pair in ends for v in pair]
    # half-edge faces (face on the left of every half-edge)
    adj: dict[int, list[int]] = {}
    for a, b in und:
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, []).append(a)
    for v, nb_ in adj.items():
        if len(nb_) < 2:
            raise TempleError("E_TRIANGULATE", "a segment ends inside the outline without meeting another",
                              at=V[v].tolist())
        p = V[v]
        nb_.sort(key=lambda w: math.atan2(V[w, 1] - p[1], V[w, 0] - p[0]))
    pos = {v: {w: k for k, w in enumerate(nb_)} for v, nb_ in adj.items()}
    used_he = set()
    faces, areas = [], []
    for a, b in sorted(und):
        for u0, v0 in ((a, b), (b, a)):
            if (u0, v0) in used_he:
                continue
            cyc = [u0]
            cu, cv = u0, v0
            guard = 0
            while True:
                used_he.add((cu, cv))
                nb_ = adj[cv]
                k = pos[cv][cu]
                w = nb_[(k - 1) % len(nb_)]
                cu, cv = cv, w
                if (cu, cv) == (u0, v0):
                    break
                cyc.append(cu)
                guard += 1
                if guard > 1_000_000:
                    raise TempleError("E_TRIANGULATE", "face traversal did not close")
            faces.append(cyc)
    areas = _areas(V, faces)
    outer = np.nonzero(areas <= 0)[0]
    dom = poly_area(boundary)
    if len(outer) != 1 or abs(-areas[outer[0]] - dom) > max(TILE_TOL_MM2, 1e-9 * dom):
        raise TempleError("E_TRIANGULATE", f"the arrangement has {len(outer)} unbounded faces (expected 1 with the "
                          f"outline's area)", outer_faces=int(len(outer)))
    keep = [i for i in range(len(faces)) if areas[i] > 0]
    faces = [faces[i] for i in keep]
    pinch = sorted({v for f in faces if len(set(f)) != len(f) for v in f if f.count(v) > 1})
    if pinch:           # build_arrangement adds chords through each and through the first and last vertex in d of
        inner_adj: dict = {}                     # the segments meeting there (a chord through the pinch alone does
        for (a, b), tg in und.items():           # not split a face when it is that component's extreme in d)
            if any(t != ("boundary",) for t in tg):
                inner_adj.setdefault(a, []).append(b)
                inner_adj.setdefault(b, []).append(a)
        pts = []
        for v in pinch:
            comp, todo = {v}, [v]
            while todo:
                for w in inner_adj.get(todo.pop(), ()):
                    if w not in comp:
                        comp.add(w)
                        todo.append(w)
            pts += [V[v], V[min(comp, key=lambda u: (V[u, 0], V[u, 1]))], V[max(comp, key=lambda u: (V[u, 0],
                                                                                                      V[u, 1]))]]
        return "pinch", pts
    areas = areas[keep]
    if abs(float(areas.sum()) - dom) > TILE_TOL_MM2 * max(1.0, len(faces)):
        raise TempleError("E_TRIANGULATE", f"the faces tile {float(areas.sum()):.9f} mm^2 of the outline's "
                          f"{dom:.9f} mm^2", tiled_mm2=float(areas.sum()), outline_mm2=dom)
    face_tris = [ear_clip(V, f) for f in faces]
    for f, t in zip(faces, face_tris):
        if not t:                                  # every corner on one line within 1e-12 (a sliver of no area)
            raise TempleError("E_TRIANGULATE", "a face of the arrangement has no area (its corners are collinear)",
                              at=V[f[0]].tolist(), n=len(f))
    interior = np.array([_fattest_centroid(V, t) for t in face_tris])
    tags = {k: tuple(sorted(v, key=repr)) for k, v in und.items()}
    return Arrangement(V=V, faces=faces, areas=areas, edge_tags=tags, boundary_loop=bl, interior_pts=interior,
                       face_tris=face_tris)


def _box_pairs(A: np.ndarray, B: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Ordered index pairs (i, j), i != j, of segments whose bounding boxes (grown by DEDUPE_MM) overlap: a sweep in d,
    so the cost follows the number of near pairs, not m^2."""
    lo = np.minimum(A, B) - DEDUPE_MM
    hi = np.maximum(A, B) + DEDUPE_MM
    order = np.argsort(lo[:, 0], kind="stable")
    lo_s = lo[order, 0]
    end = np.searchsorted(lo_s, hi[order, 0], side="right")      # sorted j in (k, end) start before i's box ends
    cnt = end - np.arange(len(order)) - 1
    cnt = np.maximum(cnt, 0)
    a = np.repeat(np.arange(len(order)), cnt)
    off = np.arange(int(cnt.sum())) - np.repeat(np.cumsum(cnt) - cnt, cnt)
    b = a + 1 + off
    i, j = order[a], order[b]
    keep = (lo[i, 1] <= hi[j, 1]) & (lo[j, 1] <= hi[i, 1]) & (lo[j, 0] <= hi[i, 0])
    i, j = i[keep], j[keep]
    return np.r_[i, j], np.r_[j, i]


def _areas(V: np.ndarray, faces: list) -> np.ndarray:
    """Signed shoelace areas of many vertex cycles at once."""
    lens = np.fromiter((len(f) for f in faces), np.int64, len(faces))
    idx = np.fromiter((v for f in faces for v in f), np.int64, int(lens.sum()))
    starts = np.r_[0, np.cumsum(lens)[:-1]]
    nxt = np.arange(len(idx)) + 1
    nxt[starts + lens - 1] = starts
    P, Q = V[idx], V[idx[nxt]]
    return 0.5 * np.bincount(np.repeat(np.arange(len(faces)), lens), weights=P[:, 0] * Q[:, 1] - Q[:, 0] * P[:, 1],
                             minlength=len(faces))


def _fattest_centroid(V: np.ndarray, tris: list) -> np.ndarray:
    best, bp = -1.0, None
    for a, b, c in tris:
        ar = abs(_cross(V[a], V[b], V[c]))
        if ar > best:
            best, bp = ar, (V[a] + V[b] + V[c]) / 3.0
    return bp


def _cross(o, a, b) -> float:
    return float((a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]))


def ear_clip(V: np.ndarray, idx: list) -> list[tuple[int, int, int]]:
    """Triangulate a simple CCW polygon V[idx] (collinear vertices allowed): repeatedly cut the ear whose smallest
    angle is largest (ties: the first). Returns index triples (CCW). When that fails (no ear, or triangles that do not
    hold every boundary edge once: a run of collinear vertices whose float noise let an ear's diagonal lie along the
    run, which then fell out as zero-area triangles) the polygon is clipped again by _ear_clip_robust; only its
    failure raises E_TRIANGULATE. Every triangulation that held before is unchanged."""
    try:
        tris = _ear_clip_fast(V, idx)
        if _covers(idx, tris):
            return tris
    except TempleError as e:
        err = e
    else:
        err = TempleError("E_TRIANGULATE", "ear clipping left a boundary edge uncovered", at=V[idx[0]].tolist(),
                          n=len(idx))
    try:
        tris = _ear_clip_robust(V, idx)
    except TempleError:
        raise err
    if not _covers(idx, tris):
        raise err
    return tris


EAR_TOL_MM = 1e-9                 # _ear_clip_robust: a vertex this close to an ear (distance) blocks it


def _covers(idx: list, tris: list) -> bool:
    """Every directed boundary edge of the polygon idx in exactly one triangle (a 3-gon of zero area: none needed)."""
    if len(idx) == 3 and not tris:
        return True
    have: dict = {}
    for a, b, c in tris:
        for e in ((a, b), (b, c), (c, a)):
            have[e] = have.get(e, 0) + 1
    return all(have.get((idx[k], idx[(k + 1) % len(idx)]), 0) == 1 for k in range(len(idx)))


def _ear_clip_robust(V: np.ndarray, idx: list) -> list[tuple[int, int, int]]:
    """ear_clip's rule with a scale-aware containment test: a vertex within EAR_TOL_MM of an ear (each edge function
    >= -EAR_TOL_MM x the edge's length) blocks it, so an ear's diagonal never runs along a run of collinear boundary
    vertices (they fan from the opposite corner instead); strictly convex corners only (cross > 1e-12)."""
    pts = [(float(V[i, 0]), float(V[i, 1])) for i in idx]
    idx = list(idx)
    tris: list[tuple[int, int, int]] = []
    while len(idx) > 3:
        n = len(idx)
        best, bk = -1.0, -1
        for k in range(n):
            a, b, c = pts[k - 1], pts[k], pts[(k + 1) % n]
            abx, aby = b[0] - a[0], b[1] - a[1]
            if abx * (c[1] - a[1]) - aby * (c[0] - a[0]) <= 1e-12:
                continue
            bcx, bcy, cax, cay = c[0] - b[0], c[1] - b[1], a[0] - c[0], a[1] - c[1]
            la, lb, lc = math.hypot(bcx, bcy), math.hypot(cax, cay), math.hypot(abx, aby)
            bad = False
            for j in range(n):
                if j in ((k - 1) % n, k, (k + 1) % n):
                    continue
                x, y = pts[j]
                if any(abs(x - q[0]) < 1e-12 and abs(y - q[1]) < 1e-12 for q in (a, b, c)):
                    continue                                   # a pinch: the same position as an ear corner
                if (abx * (y - a[1]) - aby * (x - a[0]) >= -EAR_TOL_MM * lc
                        and bcx * (y - b[1]) - bcy * (x - b[0]) >= -EAR_TOL_MM * la
                        and cax * (y - c[1]) - cay * (x - c[0]) >= -EAR_TOL_MM * lb):
                    bad = True
                    break
            if bad:
                continue
            angA = math.acos(max(-1.0, min(1.0, (lb * lb + lc * lc - la * la) / (2 * lb * lc))))
            angB = math.acos(max(-1.0, min(1.0, (la * la + lc * lc - lb * lb) / (2 * la * lc))))
            q = min(angA, angB, math.pi - angA - angB)
            if q > best:
                best, bk = q, k
        if bk < 0:
            raise TempleError("E_TRIANGULATE", "no ear found (region not simple?)", at=list(pts[0]), n=n)
        tris.append((idx[(bk - 1) % n], idx[bk], idx[(bk + 1) % n]))
        idx.pop(bk)
        pts.pop(bk)
    a, b, c = pts
    if abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])) > 1e-12:
        tris.append((idx[0], idx[1], idx[2]))
    return tris


def _ear_clip_fast(V: np.ndarray, idx: list) -> list[tuple[int, int, int]]:
    """The ear-clipping rule (ear_clip's docstring), vectorised over the corners above SMALL_POLY."""
    idx = list(idx)
    tris: list[tuple[int, int, int]] = []
    if len(idx) == 3:
        P = V[idx]
        if abs(_cross(P[0], P[1], P[2])) > 1e-12:
            tris.append((idx[0], idx[1], idx[2]))
        return tris
    if len(idx) <= SMALL_POLY:
        return _ear_clip_small(V, idx)
    while len(idx) > 3:
        P = V[idx]
        n = len(idx)
        prev = np.r_[n - 1, 0:n - 1]
        nxt = np.r_[1:n, 0]
        a, b, c = P[prev], P, P[nxt]
        ab, bc, ca = b - a, c - b, a - c
        cr = ab[:, 0] * (c[:, 1] - a[:, 1]) - ab[:, 1] * (c[:, 0] - a[:, 0])
        cand = cr > 1e-12
        if not cand.any():
            raise TempleError("E_TRIANGULATE", "degenerate region (no convex corner left)",
                              at=V[idx[0]].tolist(), n=n)
        X, Y = P[None, :, 0], P[None, :, 1]
        c1 = ab[:, None, 0] * (Y - a[:, None, 1]) - ab[:, None, 1] * (X - a[:, None, 0])
        c2 = bc[:, None, 0] * (Y - b[:, None, 1]) - bc[:, None, 1] * (X - b[:, None, 0])
        c3 = ca[:, None, 0] * (Y - c[:, None, 1]) - ca[:, None, 1] * (X - c[:, None, 0])
        inside = (c1 >= -1e-12) & (c2 >= -1e-12) & (c3 >= -1e-12)
        rows = np.arange(n)
        inside[rows, prev] = inside[rows, rows] = inside[rows, nxt] = False
        same = np.zeros_like(inside)                     # a vertex at an ear corner's position (a pinch)
        for q in (a, b, c):
            same |= (np.abs(X - q[:, None, 0]) < 1e-12) & (np.abs(Y - q[:, None, 1]) < 1e-12)
        good = cand & ~(inside & ~same).any(axis=1)
        if not good.any():
            raise TempleError("E_TRIANGULATE", "no ear found (region not simple?)", at=V[idx[0]].tolist(), n=n)
        la, lb, lc = (np.hypot(*bc.T), np.hypot(*ca.T), np.hypot(*ab.T))      # sides opposite a, b, c
        with np.errstate(invalid="ignore", divide="ignore"):
            angA = np.arccos(np.clip((lb ** 2 + lc ** 2 - la ** 2) / (2 * lb * lc), -1, 1))
            angB = np.arccos(np.clip((la ** 2 + lc ** 2 - lb ** 2) / (2 * la * lc), -1, 1))
        q = np.minimum(np.minimum(angA, angB), np.pi - angA - angB)
        q = np.where(good, np.nan_to_num(q, nan=0.0), -1.0)
        k = int(np.argmax(q))
        tris.append((idx[(k - 1) % n], idx[k], idx[(k + 1) % n]))
        idx.pop(k)
    if abs(_cross(V[idx[0]], V[idx[1]], V[idx[2]])) > 1e-12:
        tris.append((idx[0], idx[1], idx[2]))
    return tris


SMALL_POLY = 16          # ear_clip runs this rule in plain Python up to this many corners (no numpy per-call cost)


def _ear_clip_small(V: np.ndarray, idx: list) -> list[tuple[int, int, int]]:
    """ear_clip's rule, element by element (the same ears, the same order)."""
    pts = [(float(V[i, 0]), float(V[i, 1])) for i in idx]
    idx = list(idx)
    tris: list[tuple[int, int, int]] = []
    while len(idx) > 3:
        n = len(idx)
        best, bk, any_cand = -1.0, -1, False
        for k in range(n):
            a, b, c = pts[k - 1], pts[k], pts[(k + 1) % n]
            abx, aby = b[0] - a[0], b[1] - a[1]
            if abx * (c[1] - a[1]) - aby * (c[0] - a[0]) <= 1e-12:
                continue
            any_cand = True
            bcx, bcy, cax, cay = c[0] - b[0], c[1] - b[1], a[0] - c[0], a[1] - c[1]
            bad = False
            for j in range(n):
                if j in ((k - 1) % n, k, (k + 1) % n):
                    continue
                x, y = pts[j]
                if (abx * (y - a[1]) - aby * (x - a[0]) >= -1e-12 and bcx * (y - b[1]) - bcy * (x - b[0]) >= -1e-12
                        and cax * (y - c[1]) - cay * (x - c[0]) >= -1e-12):
                    if not any(abs(x - q[0]) < 1e-12 and abs(y - q[1]) < 1e-12 for q in (a, b, c)):
                        bad = True
                        break
            if bad:
                continue
            la, lb, lc = math.hypot(bcx, bcy), math.hypot(cax, cay), math.hypot(abx, aby)
            if lb * lc == 0 or la * lc == 0:
                q = 0.0
            else:
                angA = math.acos(max(-1.0, min(1.0, (lb * lb + lc * lc - la * la) / (2 * lb * lc))))
                angB = math.acos(max(-1.0, min(1.0, (la * la + lc * lc - lb * lb) / (2 * la * lc))))
                q = min(angA, angB, math.pi - angA - angB)
            if q > best:
                best, bk = q, k
        if not any_cand:
            raise TempleError("E_TRIANGULATE", "degenerate region (no convex corner left)", at=list(pts[0]), n=n)
        if bk < 0:
            raise TempleError("E_TRIANGULATE", "no ear found (region not simple?)", at=list(pts[0]), n=n)
        tris.append((idx[(bk - 1) % n], idx[bk], idx[(bk + 1) % n]))
        idx.pop(bk)
        pts.pop(bk)
    a, b, c = pts
    if abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])) > 1e-12:
        tris.append((idx[0], idx[1], idx[2]))
    return tris
