"""Geometry measures of a temple (first defined by the archived WP6 v1 design, plan/archive/20261010/WP6_DESIGN.md §5;
information only since the v1 gates were retired, plan/WP6_FACETS.md §0), shared by the core, the Blender layer and the
host so they cannot disagree. numpy only.

Every function takes a triangle mesh in the temple CHART: local (d, y, X) mm, d = z_h - z behind the hinge plane, y up,
X = |x| outward (the tool's own local frame; a GLB temple is mapped by `chart_from_world`).

- `slice_d`            exact section of the mesh by the plane d = const: segments in (y, X)
- `section_profile`    outer x_out(y) = max X and inner x_in(y) = min X of a section on a y grid
- `is_surface_decal`   §3 "Decals": the geometric decal rule of g3d.faceted_temple (Blender): never part names
- `hinge_part_kind`    §3 "Hinge parts are never moved": the hinge-part rule (knuckles, temple leaves) of
                       g3d.faceted_temple's hinge report and its decal planning, which never takes a hinge part as a
                       decal, and of the facet lab's footprints
- `knuckle_axis`       the default hinge axis: bbox centre (|x|, z) of one side's temple-role knuckles
The v1 §5 instrument (vbulge, pbulge, single_plane_share, facet_table, inner_flatness, stations, measure_temple and its
SLAB / INNER_NOT_FLAT / BULGE_SANITY flags) and decal_standoff were deleted 2026-10-13 (v1 gates retired 2026-10-10);
see git tag review-20261013-pre-cleanup.
"""
from __future__ import annotations

import numpy as np

OUTER_NORMAL_MIN = 0.6          # |unit normal X| beyond this: an outer (+) or inner (-) face triangle (template.py)
DECAL_NEAR_MM = 1.0             # a decal lies within this of the old temple surface (every vertex)
DECAL_THIN_MM = 0.3             # ... and is open, or thinner than this
HINGE_NEAR_MM = 4.0             # hinge parts: bbox within this of the hinge plane ...
HINGE_X_MM = 8.0                # ... and of the hinge axis in x
KNUCKLE_MAX_MM = 3.2            # a hinge part smaller than this in x and z is a knuckle
HINGE_LEAF_MAX_Z_MM = 15.0      # a temple-role hinge part shorter than this in z (not a knuckle) is a leaf
HINGE_MAX_VERTICES = 20000      # parts with more vertices (a front, a temple body) are never hinge parts


def is_surface_decal(signed_dist, bbox_min_extent: float, is_open: bool) -> bool:
    """plan/archive/20261010/WP6_DESIGN.md §3 "Decals": thin open or near-zero-thickness parts within 1 mm of the old
    outer face. True when every vertex lies within DECAL_NEAR_MM of the old temple surface (`signed_dist`: the
    vertices' signed distances to it) and the part is open, or thinner than DECAL_THIN_MM: by its bounding box's
    smallest extent, or by the spread of its signed distances, i.e. its thickness along the surface normal. The second
    measure is what finds a closed decal on a tilted or curved face, whose box is not thin (Prada's ribbon and shield:
    box 0.52 mm, 0.020-0.072 mm off the surface); a hinge leaf beside the temple spans -0.82..+1.01 mm and is no
    decal."""
    sd = np.asarray(signed_dist, np.float64)
    if not len(sd) or not np.all(np.isfinite(sd)) or float(np.abs(sd).max()) > DECAL_NEAR_MM:
        return False
    return bool(is_open or bbox_min_extent < DECAL_THIN_MM or float(sd.max() - sd.min()) < DECAL_THIN_MM)


def hinge_part_kind(lo, hi, role, side: int, axis_x: float | None, hinge_z: float, n_vertices: int) -> str | None:
    """plan/archive/20261010/WP6_DESIGN.md §3 "Hinge parts are never moved" (the parts g3d.faceted_temple's hinge
    report grades): a temple- or frame-role part whose bbox (lo, hi: world mm, x, y, z) lies near the hinge of side
    `side` (z within HINGE_NEAR_MM of the hinge plane, extended 4 mm behind it; x within HINGE_X_MM of side * axis_x)
    with at most HINGE_MAX_VERTICES vertices is a "temple_knuckle" / "frame_knuckle" when smaller than KNUCKLE_MAX_MM
    in x and z, else a "leaf" when it is temple-role and shorter than HINGE_LEAF_MAX_Z_MM in z; anything else is None
    (the front, a temple body, a decal away from the hinge). axis_x None (no hinge axis) -> None. Never part names."""
    if axis_x is None or role not in ("temple", "frame"):
        return None
    lo, hi = np.asarray(lo, np.float64), np.asarray(hi, np.float64)
    ax = side * float(axis_x)
    if hi[2] < hinge_z - HINGE_NEAR_MM - 4 or lo[2] > hinge_z + HINGE_NEAR_MM or hi[0] < ax - HINGE_X_MM             or lo[0] > ax + HINGE_X_MM or n_vertices > HINGE_MAX_VERTICES:
        return None
    if (hi[0] - lo[0]) < KNUCKLE_MAX_MM and (hi[2] - lo[2]) < KNUCKLE_MAX_MM:
        return f"{role}_knuckle"
    if role == "temple" and (hi[2] - lo[2]) < HINGE_LEAF_MAX_Z_MM:
        return "leaf"
    return None


def knuckle_axis(boxes, side: int, hinge_z: float) -> list | None:
    """Default hinge axis [|x|, z]: the bbox centre of one side's temple-role knuckles. `boxes` = [(lo, hi, role)]
    (world mm); a knuckle = temple-role, centre on that side, centre z within HINGE_NEAR_MM of the hinge plane, smaller
    than KNUCKLE_MAX_MM in x and z. None when there is none."""
    pts = []
    for lo, hi, role in boxes:
        if role != "temple":
            continue
        lo, hi = np.asarray(lo, np.float64), np.asarray(hi, np.float64)
        c = (lo + hi) / 2
        if side * c[0] <= 0 or abs(c[2] - hinge_z) > HINGE_NEAR_MM:
            continue
        if (hi[0] - lo[0]) < KNUCKLE_MAX_MM and (hi[2] - lo[2]) < KNUCKLE_MAX_MM:
            pts += [lo, hi]
    if not pts:
        return None
    P = np.array(pts)
    lo, hi = P.min(axis=0), P.max(axis=0)
    return [float(abs((lo[0] + hi[0]) / 2)), float((lo[2] + hi[2]) / 2)]


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
