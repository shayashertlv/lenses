"""Mesh geometry for lens seating (WP4_DESIGN.md §3). numpy only (Blender 5.2's bundled Python has no scipy).

Units are world millimetres throughout. Triangles are (M, 3) integer arrays into a vertex array.

- `weld_exact` merges bit-identical positions only. A tolerance weld collapses Astra's near-coincident construction
  vertices (q001's central 128-gon fan) into degenerate triangles (WP4_DESIGN §2 'Weld').
- `split_caps` is the dihedral cap/wall split of §3 with R's guard: faces more than 75 degrees from the local surface
  normal (the least-spread direction of the face centres around them) never join a cap, so a rounded rim cannot merge
  the front and back caps.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

CAP_THRESHOLDS_DEG = (20.0, 30.0, 40.0, 50.0, 60.0)      # §3: theta = 20, 30 ... 60
CAP_GATE_DEG = 75.0                                       # §3: never enter a face > 75 deg from the local normal
CAP_OPPOSITE_DOT = -0.5                                   # §3: the two largest patches face opposite ways
CAP_AREA_SHARE = 0.75                                     # §3: ... and hold >= 75% of the area
NORMAL_FIELD_CELL_MM = 1.0                                # local surface normal: area-weighted face centres in a
NORMAL_FIELD_HALF = 3                                     # (2*3+1) mm box around each face (R's moment grid)
DEGENERATE_AREA_MM2 = 1e-6                                # slivers (q001's central 128-gon fan: ~1e-11 mm2) have
                                                          # meaningless normals: they join their neighbours' patch


class SeatError(Exception):
    """A geometry problem with a stable code (E_NO_CAPS, E_OPEN, ...). The tools turn these into refusals."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


# ------------------------------------------------------------------------------------------------------ welding
def weld_exact(V) -> tuple[np.ndarray, np.ndarray]:
    """(unique positions, raw -> welded index). Only bit-identical positions merge (-0.0 and 0.0 compare equal)."""
    V = np.ascontiguousarray(np.asarray(V, dtype=np.float64).reshape(-1, 3))
    if len(V) == 0:
        return V.copy(), np.zeros(0, np.int64)
    Vw, inv = np.unique(V, axis=0, return_inverse=True)
    return Vw, np.asarray(inv, np.int64).reshape(-1)


def drop_degenerate_index(T) -> np.ndarray:
    """Triangles whose three indices are distinct (a weld can make a sliver's corners coincide)."""
    T = np.asarray(T, np.int64).reshape(-1, 3)
    return T[(T[:, 0] != T[:, 1]) & (T[:, 1] != T[:, 2]) & (T[:, 0] != T[:, 2])]


# ------------------------------------------------------------------------------------------------------ edges
@dataclass(frozen=True)
class EdgeTable:
    edges: np.ndarray      # (E, 2) undirected edges, lo < hi, sorted
    f0: np.ndarray         # (E,) first incident face
    f1: np.ndarray         # (E,) second incident face, -1 when the edge has one face
    count: np.ndarray      # (E,) number of incident faces
    misoriented: int       # manifold edges whose two faces traverse it in the same direction


def edge_table(T) -> EdgeTable:
    T = np.asarray(T, np.int64).reshape(-1, 3)
    F = len(T)
    if F == 0:
        z = np.zeros(0, np.int64)
        return EdgeTable(np.zeros((0, 2), np.int64), z, z, z, 0)
    E = np.concatenate([T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]])
    fid = np.tile(np.arange(F), 3)
    lo, hi = E.min(1), E.max(1)
    n = int(T.max()) + 1
    key = lo * n + hi
    order = np.argsort(key, kind="stable")
    ks = key[order]
    starts = np.flatnonzero(np.r_[True, ks[1:] != ks[:-1]])
    cnt = np.diff(np.r_[starts, len(ks)])
    edges = np.stack([lo[order][starts], hi[order][starts]], 1)
    f0 = fid[order][starts]
    f1 = np.where(cnt >= 2, fid[order][np.minimum(starts + 1, len(ks) - 1)], -1)
    # orientation: a consistently oriented manifold edge is traversed a->b by one face and b->a by the other
    d0 = E[order][starts]
    d1 = E[order][np.minimum(starts + 1, len(ks) - 1)]
    mis = int(((cnt == 2) & (d0[:, 0] == d1[:, 0])).sum())
    return EdgeTable(edges, f0, f1, cnt, mis)


# ------------------------------------------------------------------------------------------------ components
def label_components(n: int, a, b) -> np.ndarray:
    """Connected-component label (the smallest member index) of n nodes joined by edges (a[i], b[i]).
    Vectorised union-find: hook larger roots onto smaller ones, then pointer-jump until stable."""
    parent = np.arange(n, dtype=np.int64)
    a = np.asarray(a, np.int64).reshape(-1)
    b = np.asarray(b, np.int64).reshape(-1)
    if len(a) == 0:
        return parent
    while True:
        ra, rb = parent[a], parent[b]
        lo, hi = np.minimum(ra, rb), np.maximum(ra, rb)
        m = lo != hi
        if not m.any():
            return parent
        np.minimum.at(parent, hi[m], lo[m])
        while True:
            pp = parent[parent]
            if np.array_equal(pp, parent):
                break
            parent = pp


def components(T, nv: int) -> tuple[np.ndarray, np.ndarray]:
    """(vertex labels, face labels) of the connected components of triangles T over nv vertices. Vertices no face
    uses keep their own label."""
    T = np.asarray(T, np.int64).reshape(-1, 3)
    lab = label_components(nv, np.r_[T[:, 0], T[:, 1]], np.r_[T[:, 1], T[:, 2]])
    return lab, (lab[T[:, 0]] if len(T) else np.zeros(0, np.int64))


def topology(T, nv: int | None = None) -> dict:
    """Closedness, manifoldness, orientation, components and Euler characteristic (over the used vertices)."""
    T = np.asarray(T, np.int64).reshape(-1, 3)
    if nv is None:
        nv = int(T.max()) + 1 if len(T) else 0
    et = edge_table(T)
    used = np.unique(T) if len(T) else np.zeros(0, np.int64)
    vlab, _ = components(T, nv)
    ncomp = len(np.unique(vlab[used])) if len(used) else 0
    boundary = int((et.count == 1).sum())
    nonman = int((et.count > 2).sum())
    return {"vertices": int(len(used)), "edges": int(len(et.edges)), "faces": int(len(T)),
            "boundary_edges": boundary, "nonmanifold_edges": nonman, "misoriented_edges": et.misoriented,
            "components": int(ncomp), "euler": int(len(used) - len(et.edges) + len(T)),
            "closed": boundary == 0 and nonman == 0 and len(T) > 0}


def weld_seams(V, T) -> np.ndarray:
    """raw -> welded index that merges bit-identical positions ONLY among vertices on boundary edges of the mesh's
    own connectivity (split UV / normal seams), so the topology of a mesh that is closed by its own indices is never
    changed: a Boolean result holding two distinct vertices at one position (a pinch) stays closed, where
    `weld_exact` everywhere made 4 non-manifold edges of it (WP4 proof review: the crystal control cutter)."""
    V = np.asarray(V, np.float64).reshape(-1, 3)
    T = np.asarray(T, np.int64).reshape(-1, 3)
    vid = np.arange(len(V), dtype=np.int64)
    if len(T) == 0:
        return vid
    et = edge_table(T)
    bverts = np.unique(et.edges[et.count == 1])
    if len(bverts) == 0:
        return vid
    _, inv = np.unique(V[bverts], axis=0, return_inverse=True)
    inv = np.asarray(inv, np.int64).reshape(-1)
    rep = np.full(int(inv.max()) + 1, len(V), np.int64)
    np.minimum.at(rep, inv, bverts)
    vid[bverts] = rep[inv]
    return vid


# ------------------------------------------------------------------------------------------- self-intersection
SELF_X_REL_TOL = 1e-9          # a crossing on a triangle's edge or at a segment end counts (closed test, this slack)
SELF_X_PARALLEL = 1e-12        # |det| below this x |e1||e2||d|: the edge is parallel to the triangle (no crossing)
SELF_X_MAX_CELLS = 1 << 21     # grid size bound: the cell grows when a mesh would need more cells


def _grid_cells(mn: np.ndarray, mx: np.ndarray, lo: np.ndarray, h: float, dims: np.ndarray):
    """(item index, cell key, first-cell bits) for every grid cell the boxes [mn, mx] overlap; bit a of the third is
    set where the cell is the box's first cell along axis a."""
    i0 = np.floor((mn - lo) / h).astype(np.int64)
    i1 = np.floor((mx - lo) / h).astype(np.int64)
    ext = i1 - i0 + 1
    cnt = ext.prod(1)
    rep = np.repeat(np.arange(len(mn), dtype=np.int64), cnt)
    k = np.arange(int(cnt.sum()), dtype=np.int64) - np.repeat(np.cumsum(cnt) - cnt, cnt)
    e = ext[rep]
    dx = k % e[:, 0]
    k = k // e[:, 0]
    dy = k % e[:, 1]
    dz = k // e[:, 1]
    ix = i0[rep] + np.stack([dx, dy, dz], 1)
    first = ((dx == 0).astype(np.uint8) | ((dy == 0).astype(np.uint8) << 1) | ((dz == 0).astype(np.uint8) << 2))
    return rep, (ix[:, 0] * dims[1] + ix[:, 1]) * dims[2] + ix[:, 2], first


_SELF_X_CACHE: dict = {}
SELF_X_CACHE_SIZE = 16


def self_intersections_cached(V, T) -> np.ndarray:
    """`self_intersections` memoised on the exact bytes of V and T (a seat call, its in-tool second call and the
    check recognise the same lens several times)."""
    import hashlib
    V = np.ascontiguousarray(np.asarray(V, np.float64).reshape(-1, 3))
    T = np.ascontiguousarray(np.asarray(T, np.int64).reshape(-1, 3))
    h = hashlib.sha1(V.tobytes())
    h.update(T.tobytes())
    key = (len(V), len(T), h.hexdigest())
    if key not in _SELF_X_CACHE:
        if len(_SELF_X_CACHE) >= SELF_X_CACHE_SIZE:
            _SELF_X_CACHE.pop(next(iter(_SELF_X_CACHE)))
        _SELF_X_CACHE[key] = self_intersections(V, T)
    return _SELF_X_CACHE[key].copy()


def self_intersections(V, T, chunk: int = 1 << 16) -> np.ndarray:
    """Edges of a triangle mesh that cross one of its own triangles sharing neither end vertex: (k, 2) rows
    (edge index into edge_table(T).edges, triangle index), sorted. Two non-coplanar triangles intersect exactly when
    an edge of one crosses the other, so a surface that passes through itself has such rows, and a clean closed mesh
    has none (shared vertices and edges are adjacency, not crossings; positions should be welded exactly first).
    Coplanar overlaps are not detected. The test is closed (a crossing ON a triangle's edge counts): two caps
    built on one (x, y) grid cross only edge-on-edge (P4's self_intersecting fixture: 204 such crossings, 0 strictly
    inside a triangle); non-adjacent elements of a clean mesh are never in contact. Uniform-grid broad phase (cell =
    the median edge length; a pair is tested once, in the cell holding the low corner of its boxes' overlap), exact
    float64 segment-triangle test. numpy only, so Blender and the host run the same test."""
    V = np.asarray(V, np.float64).reshape(-1, 3)
    T = np.asarray(T, np.int64).reshape(-1, 3)
    none = np.zeros((0, 2), np.int64)
    if len(T) < 2:
        return none
    E = edge_table(T).edges
    a, b, c = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
    tmin, tmax = np.minimum(np.minimum(a, b), c), np.maximum(np.maximum(a, b), c)
    p, q = V[E[:, 0]], V[E[:, 1]]
    emin, emax = np.minimum(p, q), np.maximum(p, q)
    lo = V.min(0) - 1e-6
    span = np.maximum(V.max(0) + 1e-6 - lo, 1e-9)
    elen = np.linalg.norm(q - p, axis=1)
    h = float(np.median(elen[elen > 0])) if (elen > 0).any() else 1.0
    h = max(h, float(np.cbrt(span.prod() / SELF_X_MAX_CELLS)), 1e-6)
    dims = np.floor(span / h).astype(np.int64) + 2
    ti, tk, tf = _grid_cells(tmin, tmax, lo, h, dims)
    order = np.argsort(tk, kind="stable")
    ti, tk, tf = ti[order], tk[order], tf[order]
    ei, ek, ef = _grid_cells(emin, emax, lo, h, dims)
    found = []
    for s in range(0, len(ei), chunk):
        e_, k_, f_ = ei[s:s + chunk], ek[s:s + chunk], ef[s:s + chunk]
        l0 = np.searchsorted(tk, k_, "left")
        n = np.searchsorted(tk, k_, "right") - l0
        m = n > 0
        e_, f_, l0, n = e_[m], f_[m], l0[m], n[m]
        if not len(e_):
            continue
        row = np.repeat(l0, n) + (np.arange(int(n.sum()), dtype=np.int64) - np.repeat(np.cumsum(n) - n, n))
        # test a pair once: in the cell holding the low corner of its boxes' overlap, i.e. where along every axis
        # the cell is the first of the edge's box or of the triangle's
        keep = (np.repeat(f_, n) | tf[row]) == 7
        pe, pt = np.repeat(e_, n)[keep], ti[row[keep]]
        keep = ((emin[pe] <= tmax[pt]) & (emax[pe] >= tmin[pt])).all(1)
        pe, pt = pe[keep], pt[keep]
        tri, ed = T[pt], E[pe]
        keep = ~((tri == ed[:, :1]) | (tri == ed[:, 1:])).any(1)
        pe, pt = pe[keep], pt[keep]
        if not len(pe):
            continue
        A = a[pt]
        e1, e2 = b[pt] - A, c[pt] - A
        d = q[pe] - p[pe]
        pv = np.cross(d, e2)
        det = (e1 * pv).sum(1)
        scale = np.linalg.norm(e1, axis=1) * np.linalg.norm(e2, axis=1) * np.linalg.norm(d, axis=1)
        ok = np.abs(det) > SELF_X_PARALLEL * np.maximum(scale, 1e-300)
        inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
        sv = p[pe] - A
        u = (sv * pv).sum(1) * inv
        qv = np.cross(sv, e1)
        v = (d * qv).sum(1) * inv
        tt = (e2 * qv).sum(1) * inv
        tol = SELF_X_REL_TOL
        hit = ok & (u >= -tol) & (v >= -tol) & (u + v <= 1 + tol) & (tt >= -tol) & (tt <= 1 + tol)
        if hit.any():
            found.append(np.stack([pe[hit], pt[hit]], 1))
    if not found:
        return none
    out = np.unique(np.vstack(found), axis=0)
    return out


# ---------------------------------------------------------------------------------------------- measurements
def face_geometry(V, T) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(unit normals, areas, centroids) per triangle. Degenerate triangles get a zero normal."""
    V = np.asarray(V, np.float64)
    T = np.asarray(T, np.int64).reshape(-1, 3)
    a, b, c = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
    fn = np.cross(b - a, c - a)
    ar2 = np.linalg.norm(fn, axis=1)
    nrm = np.where(ar2[:, None] > 0, fn / np.where(ar2 > 0, ar2, 1.0)[:, None], 0.0)
    return nrm, 0.5 * ar2, (a + b + c) / 3.0


def signed_volume(V, T) -> float:
    V = np.asarray(V, np.float64)
    T = np.asarray(T, np.int64).reshape(-1, 3)
    if len(T) == 0:
        return 0.0
    return float(np.einsum("ij,ij->i", V[T[:, 0]], np.cross(V[T[:, 1]], V[T[:, 2]])).sum() / 6.0)


def surface_area(V, T) -> float:
    return float(face_geometry(V, T)[1].sum())


def effective_thickness(V, T) -> float:
    """2|V|/A: the thickness of a plate with this volume and surface (§3 body rule >= 0.3 mm)."""
    A = surface_area(V, T)
    return 2.0 * abs(signed_volume(V, T)) / A if A > 0 else 0.0


def local_surface_normals(C, W, cell: float = NORMAL_FIELD_CELL_MM, half: int = NORMAL_FIELD_HALF) -> np.ndarray:
    """Per point, the least-spread direction (smallest eigenvector of the W-weighted covariance) of the points in the
    (2*half+1) cell box around it. On a lens this is the surface normal whatever the construction (sphere, toric,
    extruded, bullnose); the sign is arbitrary (callers use |dot|)."""
    C = np.asarray(C, np.float64)
    W = np.asarray(W, np.float64)
    if len(C) == 0:
        return np.zeros((0, 3))
    lo = C.min(0) - cell * (half + 1)
    ijk = np.floor((C - lo) / cell).astype(np.int64)
    shape = tuple(int(s) for s in ijk.max(0) + half + 2)
    X = C - lo
    feats = (W, W * X[:, 0], W * X[:, 1], W * X[:, 2], W * X[:, 0] * X[:, 0], W * X[:, 0] * X[:, 1],
             W * X[:, 0] * X[:, 2], W * X[:, 1] * X[:, 1], W * X[:, 1] * X[:, 2], W * X[:, 2] * X[:, 2])
    flat = np.ravel_multi_index(ijk.T, shape)
    G = np.zeros((10,) + shape)
    size = int(np.prod(shape))
    for k, f in enumerate(feats):
        G[k].reshape(-1)[:] = np.bincount(flat, weights=f, minlength=size)
    for ax in (1, 2, 3):                                   # box filter by prefix sums along each axis
        c = np.cumsum(G, axis=ax)
        c = np.concatenate([np.zeros_like(np.take(c, [0], axis=ax)), c], axis=ax)
        n = G.shape[ax]
        hi = np.clip(np.arange(n) + half + 1, 0, n)
        lo_ = np.clip(np.arange(n) - half, 0, n)
        G = np.take(c, hi, axis=ax) - np.take(c, lo_, axis=ax)
    cells, inv = np.unique(flat, return_inverse=True)
    g = G.reshape(10, -1)[:, cells]
    w = np.maximum(g[0], 1e-300)
    mu = g[1:4] / w
    S = np.empty((len(cells), 3, 3))
    S[:, 0, 0] = g[4] / w - mu[0] * mu[0]
    S[:, 0, 1] = S[:, 1, 0] = g[5] / w - mu[0] * mu[1]
    S[:, 0, 2] = S[:, 2, 0] = g[6] / w - mu[0] * mu[2]
    S[:, 1, 1] = g[7] / w - mu[1] * mu[1]
    S[:, 1, 2] = S[:, 2, 1] = g[8] / w - mu[1] * mu[2]
    S[:, 2, 2] = g[9] / w - mu[2] * mu[2]
    _, evec = np.linalg.eigh(S)
    return evec[:, :, 0][np.asarray(inv).reshape(-1)]


# ------------------------------------------------------------------------------------------------ cap / wall
@dataclass(frozen=True)
class CapSplit:
    cls: np.ndarray            # (F,) 0 front cap, 1 back cap, 2 wall
    theta_deg: float           # dihedral threshold accepted
    area_share: float          # share of the area held by the two caps
    front_normal: np.ndarray   # area-weighted mean normal of the front cap (unit)


def split_caps(V, T, thresholds=CAP_THRESHOLDS_DEG, gate_deg: float = CAP_GATE_DEG,
               face_normals=None, local_normals=None) -> CapSplit:
    """Front cap / back cap / wall of a closed lens solid (§3). Union-find over edges whose dihedral is below theta,
    among faces within gate_deg of the local surface normal; accept the first theta whose two largest patches face
    opposite ways and hold >= 75% of the area. The front cap is the one facing +Z (the asset's front).
    Raises SeatError('E_NO_CAPS') otherwise. Expects outward winding (callers flip inside-out solids first)."""
    V = np.asarray(V, np.float64)
    T = np.asarray(T, np.int64).reshape(-1, 3)
    fn, fa, fc = face_geometry(V, T)
    if face_normals is not None:
        fn = face_normals
    M = local_surface_normals(fc, fa) if local_normals is None else local_normals
    tiny = fa < DEGENERATE_AREA_MM2
    gate = (np.abs((fn * M).sum(1)) > np.cos(np.radians(gate_deg))) | tiny
    et = edge_table(T)
    inner = et.f1 >= 0
    a, b = et.f0[inner], et.f1[inner]
    dots = (fn[a] * fn[b]).sum(1)
    total = float(fa.sum())
    F = len(T)
    for th in thresholds:
        ok = gate[a] & gate[b] & ((dots > np.cos(np.radians(th))) | tiny[a] | tiny[b])
        lab = label_components(F, a[ok], b[ok])
        area = np.bincount(lab, weights=fa * gate, minlength=F)
        order = np.lexsort((np.arange(F), -area))          # by area desc, ties by label
        big = order[:2]
        if len(big) < 2 or area[big[1]] <= 0:
            continue
        nz = []
        for k in big:
            m = lab == k
            v = (fn[m] * fa[m, None]).sum(0)
            nz.append(v / max(np.linalg.norm(v), 1e-300))
        share = float(area[big].sum() / total) if total > 0 else 0.0
        if float(np.dot(nz[0], nz[1])) < CAP_OPPOSITE_DOT and share >= CAP_AREA_SHARE:
            fi = 0 if nz[0][2] >= nz[1][2] else 1
            cls = np.full(F, 2, np.int64)
            cls[(lab == big[fi]) & gate] = 0
            cls[(lab == big[1 - fi]) & gate] = 1
            return CapSplit(cls, float(th), share, nz[fi])
    raise SeatError("E_NO_CAPS", "no dihedral threshold in 20-60 deg gives two opposite caps holding >= 75% of "
                                 "the area (not a lens body: rounded all over, or not a plate)")


# ---------------------------------------------------------------------------------------------------- loops
def chain_loops(edges) -> list[np.ndarray]:
    """Chain undirected edges into closed loops (vertex arrays, no repeated end). Raises SeatError('E_RIM_LOOP')
    when a vertex has other than two loop edges (a pinched or open rim)."""
    edges = np.asarray(edges, np.int64).reshape(-1, 2)
    if len(edges) == 0:
        return []
    verts, deg = np.unique(edges.reshape(-1), return_counts=True)
    if (deg != 2).any():
        bad = verts[deg != 2]
        raise SeatError("E_RIM_LOOP", f"{len(bad)} rim vertices have {sorted(set(deg[deg != 2].tolist()))} rim "
                                      f"edges instead of 2 (pinched or open rim)")
    nbr = {}
    for u, v in edges.tolist():
        nbr.setdefault(u, []).append(v)
        nbr.setdefault(v, []).append(u)
    seen = set()
    loops = []
    for u0 in verts.tolist():
        if u0 in seen:
            continue
        loop = [u0]
        seen.add(u0)
        prev, cur = u0, min(nbr[u0])
        while cur != u0:
            loop.append(cur)
            seen.add(cur)
            a, b = nbr[cur]
            nxt = b if a == prev else a
            prev, cur = cur, nxt
        loops.append(np.array(loop, np.int64))
    return loops


def loop_perimeter(V, loop) -> float:
    P = np.asarray(V)[np.asarray(loop)]
    return float(np.linalg.norm(np.roll(P, -1, 0) - P, axis=1).sum())


def rim_loops(V, T, cls) -> dict[int, list[np.ndarray]]:
    """Cap/wall boundary loops per cap (0 front, 1 back), longest first. Raises SeatError('E_NO_WALL') when the two
    caps touch directly."""
    T = np.asarray(T, np.int64).reshape(-1, 3)
    cls = np.asarray(cls)
    et = edge_table(T)
    inner = et.f1 >= 0
    c0 = cls[et.f0]
    c1 = np.where(inner, cls[np.maximum(et.f1, 0)], -1)
    if (inner & (((c0 == 0) & (c1 == 1)) | ((c0 == 1) & (c1 == 0)))).any():
        raise SeatError("E_NO_WALL", "the front and back caps share edges (no wall between them)")
    out = {}
    for k in (0, 1):
        sel = inner & ((c0 == k) != (c1 == k))
        loops = chain_loops(et.edges[sel])
        loops.sort(key=lambda lp: (-loop_perimeter(V, lp), int(lp.min())))
        out[k] = loops
    return out


def rungs(V, front_loop, back_loop, edges) -> np.ndarray:
    """Back-loop partner of each front-loop vertex through the given edges (pass POLYGON edges when available:
    triangulation diagonals are not rungs). The shortest such edge wins; -1 where none exists (a multi-ring wall,
    mapped by arc instead)."""
    V = np.asarray(V, np.float64)
    edges = np.asarray(edges, np.int64).reshape(-1, 2)
    fpos = {int(v): i for i, v in enumerate(np.asarray(front_loop).tolist())}
    bset = set(np.asarray(back_loop).tolist())
    best = np.full(len(fpos), -1, np.int64)
    bestd = np.full(len(fpos), np.inf)
    for a, b in edges.tolist():
        for x, y in ((a, b), (b, a)):
            i = fpos.get(x)
            if i is not None and y in bset:
                d = float(np.linalg.norm(V[x] - V[y]))
                if d < bestd[i] or (d == bestd[i] and y < best[i]):
                    best[i], bestd[i] = y, d
    return best


# ------------------------------------------------------------------------------------------------- polylines
def nearest_on_polyline(P, X, closed: bool = True, chunk: int = 2048,
                        max_dist: float | None = None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """For each X: (arc parameter of the nearest point on the polyline P, distance, segment index).

    `max_dist`: only points within this distance are resolved (the same values, bit for bit); the others come back
    as (nan, inf, -1). A uniform grid hands each point the segments whose box grown by max_dist covers its cell, so
    the work is ~ points x a few segments instead of points x all segments (INVU's lens: 10.8 s of a 17 s seat_lens
    was the full product here)."""
    P = np.asarray(P, np.float64)
    X = np.asarray(X, np.float64).reshape(-1, 3)
    A = P if closed else P[:-1]
    B = np.roll(P, -1, 0) if closed else P[1:]
    AB = B - A
    L2 = (AB * AB).sum(1)
    seg = np.sqrt(L2)
    s0 = np.r_[0.0, np.cumsum(seg)][:-1]
    if max_dist is not None:
        return _nearest_on_segments_near(A, AB, L2, seg, s0, X, float(max_dist))
    out_s, out_d, out_k = np.empty(len(X)), np.empty(len(X)), np.empty(len(X), np.int64)
    for c0 in range(0, len(X), chunk):
        x = X[c0:c0 + chunk]
        w = np.clip(((x[:, None, :] - A[None]) * AB[None]).sum(-1) / np.maximum(L2[None], 1e-300), 0.0, 1.0)
        Q = A[None] + w[..., None] * AB[None]
        d = np.linalg.norm(x[:, None, :] - Q, axis=-1)
        k = d.argmin(1)
        r = np.arange(len(x))
        out_s[c0:c0 + chunk] = s0[k] + w[r, k] * seg[k]
        out_d[c0:c0 + chunk] = d[r, k]
        out_k[c0:c0 + chunk] = k
    return out_s, out_d, out_k


def _nearest_on_segments_near(A, AB, L2, seg, s0, X, r: float, pair_chunk: int = 1 << 20):
    """nearest_on_polyline restricted to points within r of the segments (A, A + AB): exact for those (every segment
    within r of a point is among its candidates, and the same arithmetic picks the same segment, the lowest index on
    a tie), (nan, inf, -1) for the rest."""
    n = len(X)
    out_s = np.full(n, np.nan)
    out_d = np.full(n, np.inf)
    out_k = np.full(n, -1, np.int64)
    if not n or not len(A):
        return out_s, out_d, out_k
    h = max(r / 4.0, 1e-6)                             # cell size: candidates lie within ~r + 1.7 h of a point
    lo = np.minimum(A, A + AB).min(0) - r - h
    B = A + AB
    c0 = np.floor((np.minimum(A, B) - r - lo) / h).astype(np.int64)
    c1 = np.floor((np.maximum(A, B) + r - lo) / h).astype(np.int64)
    dims = np.maximum(c1.max(0), np.floor((X.max(0) - lo) / h).astype(np.int64)) + 2
    key_pairs, seg_pairs = [], []
    for j in range(len(A)):                                     # segments are few (rim loops: <= ~700)
        g = np.stack(np.meshgrid(*[np.arange(c0[j, a], c1[j, a] + 1) for a in range(3)], indexing="ij"), -1)
        g = g.reshape(-1, 3)
        key_pairs.append((g[:, 0] * dims[1] + g[:, 1]) * dims[2] + g[:, 2])
        seg_pairs.append(np.full(len(g), j, np.int64))
    keys = np.concatenate(key_pairs)
    segs = np.concatenate(seg_pairs)
    order = np.lexsort((segs, keys))
    keys, segs = keys[order], segs[order]
    cx = np.floor((X - lo) / h).astype(np.int64)
    inside = (cx >= 0).all(1) & (cx < dims).all(1)
    pk = np.where(inside, (cx[:, 0] * dims[1] + cx[:, 1]) * dims[2] + cx[:, 2], -1)
    a = np.searchsorted(keys, pk, side="left")
    b = np.searchsorted(keys, pk, side="right")
    cnt = np.where(inside, b - a, 0)
    pts = np.flatnonzero(cnt > 0)
    if not len(pts):
        return out_s, out_d, out_k
    # flat (point, segment) pairs in point order, segments ascending within a point, in chunks of whole points
    reps = cnt[pts]
    cum = np.cumsum(reps)
    best_d = np.full(n, np.inf)
    best_k = np.full(n, -1, np.int64)
    best_w = np.zeros(n)
    g0 = 0
    while g0 < len(pts):
        base = cum[g0] - reps[g0]
        g1 = max(g0 + 1, int(np.searchsorted(cum, base + pair_chunk, side="right")))
        pp, rr = pts[g0:g1], reps[g0:g1]
        within = np.arange(int(rr.sum())) - np.repeat(np.cumsum(rr) - rr, rr)
        pi = np.repeat(pp, rr)
        sj = segs[np.repeat(a[pp], rr) + within]
        x = X[pi]
        w = np.clip(((x - A[sj]) * AB[sj]).sum(-1) / np.maximum(L2[sj], 1e-300), 0.0, 1.0)
        Q = A[sj] + w[:, None] * AB[sj]
        d = np.linalg.norm(x - Q, axis=-1)
        # per point: the smallest d, then the smallest segment index among equal d (the full search's argmin)
        st = np.cumsum(rr) - rr
        dmin = np.minimum.reduceat(d, st)
        tie = d == np.repeat(dmin, rr)
        kmin = np.minimum.reduceat(np.where(tie, sj, np.iinfo(np.int64).max), st)
        pick = tie & (sj == np.repeat(kmin, rr))
        best_d[pp] = dmin
        best_k[pp] = kmin
        best_w[pi[pick]] = w[pick]
        g0 = g1
    ok = best_d <= r
    k = best_k[ok]
    out_d[ok] = best_d[ok]
    out_k[ok] = k
    out_s[ok] = s0[k] + best_w[ok] * seg[k]
    return out_s, out_d, out_k


# ---------------------------------------------------------------------------------------------------- fitting
def fit_quadric(P, origin, t, u, n, intercept: bool = False) -> np.ndarray:
    """Least-squares height field z = a t^2 + b t u + c u^2 + d t + e u (+ f with `intercept`) over the (t, u) plane at
    `origin`, z along n (§4 step 6). Returns (a, b, c, d, e) or (a, b, c, d, e, f). Without the intercept the surface
    passes through `origin` (a point on it); with it, `origin` may lie off the fitted surface (a chamfer's or a
    bullnose's rim ring above the cap) and `quadric_rise` keeps that offset."""
    D = np.asarray(P, np.float64) - origin
    x, y, z = D @ t, D @ u, D @ n
    cols = [x * x, x * y, y * y, x, y] + ([np.ones_like(x)] if intercept else [])
    coef, *_ = np.linalg.lstsq(np.stack(cols, 1), z, rcond=None)
    return coef


def quadric_height(coef, x, y):
    a, b, c, d, e = coef[:5]
    f = coef[5] if len(coef) > 5 else 0.0
    return a * x * x + b * x * y + c * y * y + d * x + e * y + f


def quadric_rise(coef, y):
    """z(0, y) - z(0, 0): how far the fitted surface rises along n over a move y along u from the origin (the move
    follows the surface's shape from wherever the origin sits, on it or off it)."""
    a, b, c, d, e = coef[:5]
    return c * y * y + e * y


def smoothstep(x):
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3.0 - 2.0 * x)
