"""Geometry measures of a temple (WP6_DESIGN.md §5 "Geometry"), shared by g3d.faceted_temple's receipt, g3d.check_relief
in Blender and g3d_host.relief_check on a GLB, so the tool and the check cannot disagree. numpy only.

Every function takes a triangle mesh in the temple CHART: local (d, y, X) mm, d = z_h - z behind the hinge plane, y up,
X = |x| outward (the tool's own local frame; a GLB temple is mapped by `chart_from_world`).

- `slice_d`            exact section of the mesh by the plane d = const: segments in (y, X)
- `section_profile`    outer x_out(y) = max X and inner x_in(y) = min X of a section on a y grid
- `vbulge`             max over stations of the outer profile's bulge over its chord after a 1 mm trim at the top and
                       bottom (the WP6 scratch sections2 definition behind the PLAN's 0.28-0.66; report only)
- `pbulge`             plan-view bulge: max over heights of the outer x(d) profile's bulge over its chord (report only)
- `single_plane_share` the largest area fraction of the outer face (normal X > 0.6) between d 2 and 50 within 0.3 mm of
                       ONE plane; candidates = the area-weighted least-squares plane and every facet plane (exact areas:
                       each triangle clipped to the slab)
- `facet_table`        given facet planes, or region-grown triangles (normals within 1 deg, area >= 4 mm^2): normal,
                       lean against the inner face, planarity, dihedral and ridge/valley with each neighbour
- `inner_flatness`     largest distance of the zone's inner face from one plane
- `stations`           width, height, x_in, x_out, outer tilt, inner flatness, bulge every 2.5 mm
- `measure_temple`     all of the above plus the SLAB / INNER_NOT_FLAT / BULGE_SANITY flags
"""
from __future__ import annotations

import math

import numpy as np

# measurement definitions (the gate thresholds live in g3d_host/relief_rules.py, which asserts these values)
SHARE_D_RANGE = (2.0, 50.0)
SHARE_TOL_MM = 0.3
OUTER_NORMAL_MIN = 0.6
VBULGE_D_RANGE = (0.25, 50.0)
VBULGE_STEP_MM = 0.25
BULGE_TRIM_MM = 1.0
PROFILE_Y_STEP_MM = 0.1
PBULGE_Y_STEP_MM = 0.25
PBULGE_MIN_SAMPLES = 40
FACET_NORMAL_DEG = 1.0
FACET_MIN_AREA_MM2 = 4.0
STATION_STEP_MM = 2.5
SLAB_SHARE = 0.8
SLAB_MIN_PHOTO_CREASES = 2
INNER_FLAT_MM = 0.05
BULGE_SANITY_MM = (1.9, 4.3)
WELD_MM = 1e-4


def chart_from_world(W: np.ndarray, side: int, hinge_z: float) -> np.ndarray:
    """World mm -> chart (d, y, X) for a temple at opening 0 (the WP3 frame: temples run back along -Z). The map is
    a mirror for side -1: pair it with `chart_faces` (or use `chart_mesh`) so normals keep pointing outward."""
    return np.stack([hinge_z - W[:, 2], W[:, 1], side * W[:, 0]], axis=1)


def chart_faces(F: np.ndarray, side: int) -> np.ndarray:
    """(d, y, X) = (-z, y, x) keeps orientation for side +1 and reverses it for side -1 (X = -x)."""
    F = np.asarray(F, np.int64)
    return F.copy() if side > 0 else F[:, ::-1].copy()


def chart_mesh(W: np.ndarray, F: np.ndarray, side: int, hinge_z: float) -> tuple[np.ndarray, np.ndarray]:
    return chart_from_world(W, side, hinge_z), chart_faces(F, side)


def tri_normals(V: np.ndarray, F: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    a, b, c = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
    n = np.cross(b - a, c - a)
    ar = 0.5 * np.linalg.norm(n, axis=1)
    return n / np.maximum(2 * ar[:, None], 1e-300), ar


# ------------------------------------------------------------------------------------------- sections
def slice_d(V: np.ndarray, F: np.ndarray, d: float) -> np.ndarray:
    """Segments (k, 2, 2) in (y, X) where the plane d = const cuts the triangles."""
    P = V[F]                                            # (m, 3, 3)
    s = P[..., 0] - d
    pos = s > 0
    cnt = pos.sum(axis=1)
    sel = (cnt == 1) | (cnt == 2)
    P, s, pos = P[sel], s[sel], pos[sel]
    if len(P) == 0:
        return np.zeros((0, 2, 2))
    pts = []
    for a, b in ((0, 1), (1, 2), (2, 0)):
        cross = pos[:, a] != pos[:, b]
        den = s[:, a] - s[:, b]
        t = np.where(cross, s[:, a] / np.where(den == 0, 1.0, den), np.nan)
        q = P[:, a, 1:] + t[:, None] * (P[:, b, 1:] - P[:, a, 1:])
        pts.append(np.where(cross[:, None], q, np.nan))
    Q = np.stack(pts, axis=1)                           # (k, 3, 2) two finite rows per triangle
    ok = np.isfinite(Q[..., 0])
    order = np.argsort(~ok, axis=1, kind="stable")[:, :2]
    seg = np.take_along_axis(Q, order[..., None], axis=1)
    return seg


def section_profile(seg: np.ndarray, ys: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(x_out, x_in) at heights ys from section segments (nan where nothing is cut)."""
    if len(seg) == 0:
        nan = np.full(len(ys), np.nan)
        return nan, nan.copy()
    y0, x0, y1, x1 = seg[:, 0, 0], seg[:, 0, 1], seg[:, 1, 0], seg[:, 1, 1]
    lo, hi = np.minimum(y0, y1), np.maximum(y0, y1)
    Y = ys[None, :]
    inside = (Y >= lo[:, None]) & (Y <= hi[:, None]) & (hi > lo)[:, None]
    dy = np.where(y1 != y0, y1 - y0, 1.0)
    t = (Y - y0[:, None]) / dy[:, None]
    X = x0[:, None] + t * (x1 - x0)[:, None]
    xo = np.where(inside, X, -np.inf).max(axis=0)
    xi = np.where(inside, X, np.inf).min(axis=0)
    xo[~np.isfinite(xo)] = np.nan
    xi[~np.isfinite(xi)] = np.nan
    return xo, xi


def station_bulge(seg: np.ndarray, trim: float = BULGE_TRIM_MM, step: float = PROFILE_Y_STEP_MM) -> dict | None:
    """Outer-profile bulge over its chord at one station (sections2): the profile x_out(y) between y_min + trim and
    y_max - trim, chord between its ends; bulge = max(x_out - chord), sag = min; tilt = the chord's lean (deg,
    + = top leans inward); also width, height and the inner face's straightness."""
    if len(seg) < 3:
        return None
    ys_all = seg[..., 0]
    y0, y1 = float(np.min(ys_all)) + trim, float(np.max(ys_all)) - trim
    if y1 - y0 < 4 * step:
        return None
    ys = np.arange(y0, y1 + 1e-9, step)
    xo, xi = section_profile(seg, ys)
    ok = np.isfinite(xo) & np.isfinite(xi)
    if ok.sum() < 5:
        return None
    ys, xo, xi = ys[ok], xo[ok], xi[ok]
    c = xo[0] + (xo[-1] - xo[0]) * (ys - ys[0]) / (ys[-1] - ys[0])
    dev = xo - c
    k = np.polyfit(ys, xi, 1)
    return {"bulge": float(dev.max()), "sag": float(dev.min()),
            "tilt_deg": float(math.degrees(math.atan2(xo[0] - xo[-1], ys[-1] - ys[0]))),
            "width": float(np.max(xo - xi)), "height": float(np.max(ys_all) - np.min(ys_all)),
            "y_bot": float(np.min(ys_all)), "y_top": float(np.max(ys_all)),
            "x_out": float(np.max(xo)), "x_in": float(np.median(xi)),
            "inner_straightness": float(np.max(np.abs(xi - np.polyval(k, ys))))}


def vbulge(V, F, d_range=VBULGE_D_RANGE, step=VBULGE_STEP_MM) -> tuple[float, float]:
    best, at = -np.inf, float("nan")
    for d in np.arange(d_range[0], d_range[1] + 1e-9, step):
        b = station_bulge(slice_d(V, F, float(d)))
        if b is not None and b["bulge"] > best:
            best, at = b["bulge"], float(d)
    return (float(best) if np.isfinite(best) else float("nan")), at


def pbulge(V, F, d_range=VBULGE_D_RANGE, d_step=VBULGE_STEP_MM, y_step=PBULGE_Y_STEP_MM,
           min_samples=PBULGE_MIN_SAMPLES) -> tuple[float, float]:
    ds = np.arange(d_range[0], d_range[1] + 1e-9, d_step)
    segs = [slice_d(V, F, float(d)) for d in ds]
    ylo = min((float(s[..., 0].min()) for s in segs if len(s)), default=0.0)
    yhi = max((float(s[..., 0].max()) for s in segs if len(s)), default=0.0)
    ys = np.arange(math.floor(ylo / y_step) * y_step, yhi + 1e-9, y_step)
    X = np.full((len(ds), len(ys)), np.nan)
    for i, s in enumerate(segs):
        X[i] = section_profile(s, ys)[0]
    best, at = -np.inf, float("nan")
    for j in range(len(ys)):
        ok = np.isfinite(X[:, j])
        if ok.sum() < min_samples:
            continue
        dd, x = ds[ok], X[ok, j]
        c = x[0] + (x[-1] - x[0]) * (dd - dd[0]) / (dd[-1] - dd[0])
        b = float(np.max(x - c))
        if b > best:
            best, at = b, float(ys[j])
    return (float(best) if np.isfinite(best) else float("nan")), at


def bulges(V, F, d_range=VBULGE_D_RANGE, step=VBULGE_STEP_MM, y_step=PBULGE_Y_STEP_MM,
           min_samples=PBULGE_MIN_SAMPLES) -> tuple[float, float, float, float]:
    """(vbulge, at d, pbulge, at y) from ONE set of station slices (the two measures of `vbulge` and `pbulge`)."""
    ds = np.arange(d_range[0], d_range[1] + 1e-9, step)
    segs = [slice_d(V, F, float(d)) for d in ds]
    best, at = -np.inf, float("nan")
    for d, sg in zip(ds, segs):
        b = station_bulge(sg)
        if b is not None and b["bulge"] > best:
            best, at = b["bulge"], float(d)
    vb = (float(best) if np.isfinite(best) else float("nan")), at
    ylo = min((float(s[..., 0].min()) for s in segs if len(s)), default=0.0)
    yhi = max((float(s[..., 0].max()) for s in segs if len(s)), default=0.0)
    ys = np.arange(math.floor(ylo / y_step) * y_step, yhi + 1e-9, y_step)
    X = np.full((len(ds), len(ys)), np.nan)
    for i, sg in enumerate(segs):
        X[i] = section_profile(sg, ys)[0]
    pbest, pat = -np.inf, float("nan")
    for j in range(len(ys)):
        ok = np.isfinite(X[:, j])
        if ok.sum() < min_samples:
            continue
        dd, x = ds[ok], X[ok, j]
        c = x[0] + (x[-1] - x[0]) * (dd - dd[0]) / (dd[-1] - dd[0])
        b = float(np.max(x - c))
        if b > pbest:
            pbest, pat = b, float(ys[j])
    return vb[0], vb[1], (float(pbest) if np.isfinite(pbest) else float("nan")), pat


def stations(V, F, d_from: float, d_to: float, step: float = STATION_STEP_MM) -> list[dict]:
    out = []
    for d in np.arange(d_from, d_to + 1e-9, step):
        b = station_bulge(slice_d(V, F, float(d)))
        if b is not None:
            out.append({"d": float(d), **{k: b[k] for k in ("width", "height", "y_bot", "y_top", "x_in", "x_out",
                                                            "tilt_deg", "inner_straightness", "bulge")}})
    return out


# ------------------------------------------------------------------------------------------- planes and areas
def clip_poly_halfspace(P: np.ndarray, n: np.ndarray, c: float) -> np.ndarray:
    """Part of the convex polygon P (k, 3) where n . x <= c (Sutherland-Hodgman)."""
    if len(P) == 0:
        return P
    s = P @ n - c
    out = []
    k = len(P)
    for i in range(k):
        a, b = P[i], P[(i + 1) % k]
        sa, sb = s[i], s[(i + 1) % k]
        if sa <= 0:
            out.append(a)
        if (sa < 0 < sb) or (sb < 0 < sa):
            out.append(a + (b - a) * (sa / (sa - sb)))
    return np.asarray(out).reshape(-1, 3)


def poly_area3(P: np.ndarray) -> float:
    if len(P) < 3:
        return 0.0
    return 0.5 * float(np.linalg.norm(np.cross(P, np.roll(P, -1, axis=0)).sum(axis=0)))


def outer_triangles(V, F, d_range=SHARE_D_RANGE, normal_min=OUTER_NORMAL_MIN) -> list[np.ndarray]:
    """Outer-face triangles (unit normal X > normal_min) clipped to d_range: list of polygons (k, 3)."""
    n, ar = tri_normals(V, F)
    sel = np.nonzero((n[:, 2] > normal_min) & (ar > 0))[0]
    out = []
    ex = np.array([1.0, 0.0, 0.0])
    for t in sel:
        P = V[F[t]]
        if P[:, 0].max() < d_range[0] or P[:, 0].min() > d_range[1]:
            continue
        Q = clip_poly_halfspace(P, ex, d_range[1])
        Q = clip_poly_halfspace(Q, -ex, -d_range[0])
        if len(Q) >= 3 and poly_area3(Q) > 0:
            out.append(Q)
    return out


def lsq_plane(polys: list[np.ndarray]) -> tuple[np.ndarray, float]:
    """Area-weighted least-squares plane (unit normal n, offset c: n . x = c) of polygons, from exact second moments
    (each polygon fanned into triangles)."""
    A = 0.0
    S1 = np.zeros(3)
    S2 = np.zeros((3, 3))
    for P in polys:
        for k in range(1, len(P) - 1):
            a, b, c = P[0], P[k], P[k + 1]
            ar = 0.5 * float(np.linalg.norm(np.cross(b - a, c - a)))
            if ar <= 0:
                continue
            g = (a + b + c) / 3.0
            A += ar
            S1 += ar * g
            S2 += ar / 12.0 * (np.outer(a, a) + np.outer(b, b) + np.outer(c, c) + 9.0 * np.outer(g, g))
    mu = S1 / A
    C = S2 / A - np.outer(mu, mu)
    w, U = np.linalg.eigh(C)
    n = U[:, 0]
    if n[2] < 0:
        n = -n
    return n, float(n @ mu)


def slab_share(polys: list[np.ndarray], n: np.ndarray, c: float, tol: float = SHARE_TOL_MM) -> float:
    """Exact area fraction of the polygons within tol of the plane n . x = c."""
    tot = 0.0
    inside = 0.0
    for P in polys:
        a = poly_area3(P)
        tot += a
        Q = clip_poly_halfspace(P, n, c + tol)
        Q = clip_poly_halfspace(Q, -n, -(c - tol))
        inside += poly_area3(Q)
    return inside / tot if tot > 0 else float("nan")


def single_plane_share(V, F, planes: list | None = None, d_range=SHARE_D_RANGE, tol=SHARE_TOL_MM) -> dict:
    """{share, best: name, per_plane: {name: share}, lsq: {normal, offset}}. `planes` = [(name, n, c)] extra
    candidates (facet planes in the chart: n . x = c)."""
    polys = outer_triangles(V, F, d_range)
    if not polys:
        return {"share": float("nan"), "best": None, "per_plane": {}, "area_mm2": 0.0}
    n0, c0 = lsq_plane(polys)
    cands = [("least_squares", n0, c0)] + [(nm, np.asarray(n, np.float64), float(c)) for nm, n, c in (planes or [])]
    per = {nm: slab_share(polys, n, c, tol) for nm, n, c in cands}
    best = max(per, key=lambda k: (per[k], k == "least_squares"))
    return {"share": per[best], "best": best, "per_plane": per, "area_mm2": float(sum(poly_area3(P) for P in polys)),
            "lsq": {"normal": n0.tolist(), "offset": c0}}


# ------------------------------------------------------------------------------------------- facets
def weld(V: np.ndarray, F: np.ndarray, tol: float = WELD_MM) -> np.ndarray:
    """Face indices into position-welded vertices (split normals in a GLB duplicate positions)."""
    key = np.round(V / tol).astype(np.int64)
    _, inv = np.unique(key, axis=0, return_inverse=True)
    return inv.reshape(-1)[F]


def facet_table(V, F, d_range=(0.0, 50.0), angle_deg=FACET_NORMAL_DEG, min_area=FACET_MIN_AREA_MM2,
                inner_normal: np.ndarray | None = None) -> list[dict]:
    """Region-grown planar regions of the outer face in d_range: every neighbour joins while its normal is within
    angle_deg of the region's seed. Regions >= min_area mm^2 are facets."""
    n, ar = tri_normals(V, F)
    cen = V[F].mean(axis=1)
    sel = (n[:, 2] > OUTER_NORMAL_MIN) & (cen[:, 0] >= d_range[0]) & (cen[:, 0] <= d_range[1]) & (ar > 0)
    idx = np.nonzero(sel)[0]
    if len(idx) == 0:
        return []
    Fw = weld(V, F)
    edge_tris: dict = {}
    for t in idx:
        a, b, c = Fw[t]
        for e in ((a, b), (b, c), (c, a)):
            edge_tris.setdefault((min(e), max(e)), []).append(int(t))
    nbr: dict = {int(t): set() for t in idx}
    for ts in edge_tris.values():
        for t in ts:
            nbr[t].update(x for x in ts if x != t)
    cosmax = math.cos(math.radians(angle_deg))
    region = {}
    regions = []
    for seed in sorted(idx.tolist(), key=lambda t: (-ar[t], t)):
        if seed in region:
            continue
        rid = len(regions)
        stack, members = [seed], []
        region[seed] = rid
        while stack:
            t = stack.pop()
            members.append(t)
            for u in sorted(nbr[t]):
                if u not in region and float(n[u] @ n[seed]) >= cosmax:
                    region[u] = rid
                    stack.append(u)
        regions.append(sorted(members))
    inn = np.array([0.0, 0.0, 1.0]) if inner_normal is None else -np.asarray(inner_normal, np.float64)
    out = []
    keep = {}
    for rid, mem in enumerate(regions):
        A = float(ar[mem].sum())
        if A < min_area:
            continue
        nm = (n[mem] * ar[mem, None]).sum(axis=0)
        nm /= np.linalg.norm(nm)
        P = V[np.unique(F[mem])]
        c = float(nm @ (V[F[mem]].reshape(-1, 3) * np.repeat(ar[mem], 3)[:, None]).sum(axis=0) / (3 * A))
        keep[rid] = len(out)
        out.append({"id": len(out), "area_mm2": A, "normal": nm.tolist(), "offset": c,
                    "lean_deg": float(math.degrees(math.acos(max(-1.0, min(1.0, float(nm @ inn)))))),
                    "planarity_mm": float(np.max(np.abs(P @ nm - c))), "triangles": len(mem),
                    "d_range": [float(V[F[mem]][..., 0].min()), float(V[F[mem]][..., 0].max())],
                    "neighbours": []})
    seen = set()
    for (a, b), ts in edge_tris.items():
        rs = sorted({region[t] for t in ts if t in region and region[t] in keep})
        if len(rs) != 2 or tuple(rs) in seen:
            continue
        seen.add(tuple(rs))
        f1, f2 = out[keep[rs[0]]], out[keep[rs[1]]]
        n1, n2 = np.array(f1["normal"]), np.array(f2["normal"])
        dih = math.degrees(math.acos(max(-1.0, min(1.0, float(n1 @ n2)))))
        cen2 = V[F[regions[rs[1]]]].reshape(-1, 3).mean(axis=0)
        mid = 0.5 * (V[F[ts[0]]].mean(axis=0) + V[F[ts[-1]]].mean(axis=0))
        shape = "ridge" if float(n1 @ (cen2 - mid)) < 0 else "valley"
        f1["neighbours"].append({"id": f2["id"], "dihedral_deg": dih, "shape": shape})
        f2["neighbours"].append({"id": f1["id"], "dihedral_deg": dih, "shape": shape})
    return out


def inner_flatness(V, F, d_range) -> dict:
    """Largest distance of the inner face (unit normal X < -0.6) between d_range from its least-squares plane."""
    n, ar = tri_normals(V, F)
    sel = np.nonzero((n[:, 2] < -OUTER_NORMAL_MIN) & (ar > 0))[0]
    polys = []
    ex = np.array([1.0, 0.0, 0.0])
    for t in sel:
        P = V[F[t]]
        Q = clip_poly_halfspace(clip_poly_halfspace(P, ex, d_range[1]), -ex, -d_range[0])
        if len(Q) >= 3 and poly_area3(Q) > 0:
            polys.append(Q)
    if not polys:
        return {"flatness_mm": float("nan"), "normal": None, "area_mm2": 0.0}
    nn, c = lsq_plane([P[:, :] * np.array([1, 1, 1]) for P in polys])
    allp = np.vstack(polys)
    if nn[2] > 0:
        nn, c = -nn, -c
    return {"flatness_mm": float(np.max(np.abs(allp @ nn - c))), "normal": nn.tolist(),
            "area_mm2": float(sum(poly_area3(P) for P in polys))}


# ------------------------------------------------------------------------------------------- the check
def measure_temple(V, F, *, zone_end: float = 50.0, d_last: float | None = None, facet_planes: list | None = None,
                   photo_creases: int | None = None, inner_flat: bool = True, station_step: float = STATION_STEP_MM,
                   with_facets: bool = True) -> dict:
    """The §5 geometry numbers and flags of one temple in its chart. `facet_planes` = [(name, n, c)] known facet
    planes (from the spec / extras); else facets are region-grown. `photo_creases` = how many creases the photos show
    on this temple (SLAB needs >= 2; None = unknown, SLAB is then reported without that condition)."""
    V = np.asarray(V, np.float64)
    F = np.asarray(F, np.int64)
    d_last = float(V[:, 0].max()) if d_last is None else d_last
    vb, vb_at, pb, pb_at = bulges(V, F)
    facets = facet_table(V, F, (0.0, zone_end))
    cands = list(facet_planes or []) + [(f"region{f['id']}", np.array(f["normal"]), f["offset"]) for f in facets]
    share = single_plane_share(V, F, cands)
    if not with_facets:
        facets = []
    inner = inner_flatness(V, F, (2.0, zone_end - 1.0))
    st = stations(V, F, 0.5, d_last - 0.5, station_step)
    flags = []
    slab_cond = share["share"] >= SLAB_SHARE
    if slab_cond and (photo_creases is None or photo_creases >= SLAB_MIN_PHOTO_CREASES):
        flags.append({"flag": "SLAB", "share": share["share"],
                      "note": None if photo_creases is not None else "photo crease count not given"})
    if inner_flat and np.isfinite(inner["flatness_mm"]) and inner["flatness_mm"] > INNER_FLAT_MM:
        flags.append({"flag": "INNER_NOT_FLAT", "flatness_mm": inner["flatness_mm"]})
    warnings = []
    if not (BULGE_SANITY_MM[0] <= vb <= BULGE_SANITY_MM[1]):
        warnings.append({"flag": "BULGE_SANITY", "vbulge_mm": vb, "range_mm": list(BULGE_SANITY_MM),
                         "note": "report only: the bulge band does not measure facets (WP6_DESIGN §0.2)"})
    return {"vbulge_mm": vb, "vbulge_at_d": vb_at, "pbulge_mm": pb, "pbulge_at_y": pb_at,
            "single_plane_share": share["share"], "share_best_plane": share["best"],
            "share_per_plane": share["per_plane"], "outer_area_d2_50_mm2": share.get("area_mm2"),
            "inner_flatness_mm": inner["flatness_mm"], "inner_normal": inner["normal"],
            "facets": facets, "stations": st, "flags": flags, "warnings": warnings}
