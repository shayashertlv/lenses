"""S6 `s6_assembly`: the front PLATE and the lenses, built from the S2 polygons lifted onto S4.

Everything is constructed in the S2 OUTLINE-SOURCE pixel plane (the front photo, or the mirrored back
photo for INVU), then lifted to 3D by the exact ray of the source camera (``depth.lift_px``). The plate is only
what the photo outline determines; the 3D parts behind it (endpiece / hinge blocks, pads) and all rimless
hardware come from the generator as S5 donor geometry (``bsa.donor``), delivered in the temple nodes.

1. Frame region = S2 frame mask (+ lens raster) vectorised, minus the EXACT lens polygons (the frame hole is
   the unbuffered lens polygon), minus a 2 px band along free/rimless lens edges (no rim there). Removed first
   (``behind_front_mask``): pixels whose ray carries no generator geometry between 3 mm in front of the S4 front
   surface and 1 mm behind the plate back (8 mm for hardware) although the generator has geometry elsewhere on
   the ray, and pixels whose front point lies > 0.75 mm outside another fit view's silhouette where the generator
   too is empty at the front (temples seen above/behind the front: S5 geometry covers them).
2. Front cap: shapely constrained Delaunay (lens rings at 0.5 mm, other boundaries simplified and cut to <= 3 mm),
   long interior edges split (red-green) to <= 3 mm; lifted onto the S4 front surface.
   Back cap (``back_points``): the S4 plate thickness deeper along the S4 DEPTH AXIS (-Z planar, radial when
   cylinder: the axis S4 measured the generator's thickness along), so the plate is a prism of the generator's own
   depth, not a solid sheared along a pitched camera's rays (that put the top-front edge ~1 mm too high and the
   bottom-back edge 1-2 mm too low, the main off-axis error of M1). The extrusion direction blends to the EXACT
   source ray (w = 1) at lens-hole boundaries so every hole stays the exact S2 polygon (lens seam unchanged), also
   on a wrapped shield whose ends the ray meets 40-70 deg off the axis: beyond ``EXTRUDE_MAX_DEG`` (70 deg, the graze
   limit) the wall LENGTH is capped (the rim is locally thinner), its direction is never bent into the hole.
   Outer silhouette rule (``silhouette_rule``): seen from a pitched/perspective source camera the photo outline
   is the union of the plate's front and back edges; where the back edge projects further out (m.delta > 0) the
   front edge moves inward so the BACK edge lands on the outline (harmonic interpolation inside; never folds). The
   outline normal and the shift are smoothed along the outline in mm (``SIL_SIGMA_MM``): a pixel-jagged outline
   no longer turns the shift into spikes and fold-halvings.
   Visual-hull carving (``carve_plate``) by the other fit views' mattes (dilated max(2 px, 0.25 mm)): a front
   point outside slides back along its source ray (<= 3 mm, source pixel unchanged), a back point outside comes
   forward along its extrusion (never thinner than 1 mm, nor than lens + margins in the band that holds a lens).
   Both amounts are corrections of the S4 surface/thickness and are smoothed like it (``metric_smooth``,
   ``CARVE_SIGMA_MM`` in mm, area-weighted): per-vertex decisions made the pleated rims and lens-hole notches.
   Hardware parts (``donor.hardware_mask``: frame components that are not a rim band) are only thin CORES
   (``hardware_core``): outline eroded 0.5 mm, placed inside the generator hardware along the source ray; the
   visible hardware is the S5 donor. Walls from the boundary edges. Watertight by construction.
3. Lenses: outline grown outward by max(0.6 mm, 35 % rim width) on frame-bounded vertices (tapered toward
   free/rimless vertices, never below 0.3 mm), and by 0.3 mm on free/rimless vertices whose outward neighbour is
   photo material or S5 geometry (``tuck_material``: a rimless edge meeting a drill mount, a shield edge meeting its
   temple root; the lens's own matte halo is not material and the march stops at the first backdrop pixel), exact
   elsewhere; clipped to lens + frame region + that tuck allowance. Mid-surface = S4 lens model + offset/tilt fitted
   so that it passes through the mid-depth of the rim AS BUILT (``make_rim_depth``: after the silhouette rule and
   the carve) on the frame-bounded ring; 1.4-2 mm thick (half the median rim thickness); front = mid + t/2, back
   along the lens normal; the containment correction also uses the rim as built. Delaunay with interior points
   (1.5 mm hex grid), boundary edges enforced.
   Vents (S2 ``carve_class`` 1, openings the glasses surround) keep their walls along the source ray like a lens
   hole (``build``: fixed vertices of the silhouette rule), so a vent stays see-through in the source view (the prism
   wall put oakley's brow back across its outer brow vents). The plate back meets the S5 donors directly behind it
   (``meet_donors``: within ``MEET_DONOR_MM``, ``donor.OVERLAP_MM`` into them; vb's nose pads left a slot at +-35 deg).
   Frame pieces that are a temple crossing a lens (``temple_crossings``: touching only a lens, explained by the S5
   arm's projection or hugging the lens outline like its own edge band) are not built.
3b. The S5 donors' anchoring is re-tested on the delivered geometry with the S10 integrity rule (``donor_anchoring``):
   a donor component that lost contact by <= ``donor.SNAP_MAX_MM`` is moved into contact, a farther one dropped; the
   decision is stored (``donor_offset_<s>``, ``donor_keep_<s>``) and applied wherever S5's temples are delivered
   (``export.temple_arrays``).
4. Checks: watertightness per part, seam gaps in a render through the source camera (native and 3x
   supersampled), lens-in-rim containment, silhouette vs S2, tuck band area; bridge underside point.

Reads S1, S2, S3 (cameras, the other views' mattes via S0), S4 and S5 (tuck material, arms, donors).
"""
from __future__ import annotations

import math
import time

import cv2
import numpy as np
import open3d as o3d
import shapely
from scipy import ndimage
from scipy.spatial import Delaunay, cKDTree
from shapely.geometry import LineString, MultiPolygon, Polygon

from reconstruction.camera import Camera

from . import core, depth, donor, raster
from .donor import HullView, hull_outside, hull_outside_mm, hull_views  # noqa: F401  (re-exported)
from .core import NormFrame, stage_dir

STAGE = "s6_assembly"
LENS_RING_MM = 0.5
OUTER_MAX_SEG_MM = 3.0
INTERIOR_MAX_EDGE_MM = 3.0
OUTER_SIMPLIFY_PX = 0.35
MASK_SMOOTH_SIGMA_PX = 0.6
FREE_BAND_PX = 2.0
GROW_MIN_MM = 0.6
GROW_RIM_SHARE = 0.35
GROW_TAPER_MM = 2.0
TUCK_FREE_MM = 0.3              # a free/rimless lens edge meeting photo material tucks this far under it
TUCK_PROBE_PX = 1.5
TUCK_REACH_MM = 1.5             # ... when that material is at most this far beyond the edge (bridging a sliver)
TUCK_JUNCTION_MM = 0.5          # free/rimless vertices this close (along the ring) to a frame-bounded one tuck too
LENS_T_RANGE_MM = (1.4, 2.0)
LENS_T_NO_RIM_MM = 1.8
LENS_INTERIOR_MM = 1.5
FRONT_BAND_BEHIND_MM = 1.0      # a frame pixel's ray must hit the generator within the plate (+1 mm) ...
FRONT_BAND_FRONT_MM = 3.0       # ... or 3 mm in front of the S4 front surface
HARDWARE_BAND_MM = 8.0          # (hardware pixels: within 8 mm behind the front)
HULL_EMPTY_MM = 0.75            # front point this far outside another fit view's dilated silhouette = empty
TEMPLE_MIN_AREA_MM2 = 2.0
SPARE_NEAR_FRONT_MM = 0.6       # camera/generator misregistration at the rim edge: never removed
MEET_DONOR_MM = 1.5             # a plate back point with S5 donor geometry this close behind it meets it (``meet_donors``)
CROSSING_ARM_MM = 2.0           # a frame piece within this of the S5 arm's projection is explained by it (the arm refine's
                                # translation bound 1.5 mm + half a millimetre of matte edge)
CROSSING_SHARE = 0.5            # ... on at least this share of its pixels
CROSSING_TOUCH_MM = 1.0         # ... and touching no S5 geometry in 3D (its plate prism farther than this from all of it)
# a hole wall runs along the exact source ray; its length is capped only beyond the angle at which the source photo
# no longer determines the plate at all (donor.GRAZE_COS, 70 deg: S6 removes plate seen more obliquely). A 25 deg
# cap thinned the lateral brow of a wrapped shield so much that the lens tuck no longer met the rim (oakley c4).
EXTRUDE_MAX_DEG = math.degrees(math.acos(donor.GRAZE_COS))
MIN_PART_AREA_MM2 = 1.0
SEAM_BAND_PX = 2.0
SUPERSAMPLE = 3
CONTAIN_MARGIN_MM = 0.2
HOLD_EXTRA_MM = 1.0
TILT_MAX = 0.05                 # lens placement tilt correction bound (rad-ish slope)
SIL_ITERS = 3                   # fixed-point rounds of the outer silhouette rule
LENS_ADJ_PX = 3.0               # a boundary vertex this close to a lens ring (or LENS_ADJ_MM) is a lens-hole vertex
LENS_ADJ_MM = 0.3
SIL_MAX_MM = 3.0                # the silhouette rule never moves a front edge further than this
CARVE_FRONT_MAX_MM = 3.0
CARVE_STEP_MM = 0.25
CARVE_MIN_T_MM = 1.0
CARVE_ITERS = 14
# the carve corrects the S4 surface/thickness, a B-spline with depth.KNOT_MM knots: its Gaussian smoothing has its
# half-power wavelength (2 pi sigma / sqrt(2 ln 2) ~ 5.34 sigma) at one knot spacing (``metric_smooth``)
CARVE_SIGMA_MM = depth.KNOT_MM / (2.0 * math.pi / math.sqrt(2.0 * math.log(2.0)))
SIL_SIGMA_MM = CARVE_SIGMA_MM
CORE_INSET_MM = 0.3             # a hardware core sits this far inside the generator hardware (front and back)
CORE_MIN_T_MM = 0.6
CORE_MAX_DEPTH_MM = 8.0
CORE_NEAR_FRONT_MM = 2.5
CORE_SMOOTH_ITERS = 3
CORE_ERODE_MM = 0.5
FRAME, LENS = 0, 1
TYPE_FRAME, TYPE_FREE, TYPE_RIMLESS = 0, 1, 2


# =========================================================================== 2D geometry
def mask_to_geometry(mask: np.ndarray, sigma: float = MASK_SMOOTH_SIGMA_PX, simplify_px: float = OUTER_SIMPLIFY_PX):
    """Polygons (with holes) of a bool raster at the 0.5 iso-line of the lightly smoothed mask
    (pixel centre = integer coordinate), even-odd composition of all contours."""
    from skimage import measure
    if not mask.any():
        return Polygon()
    pad = 2
    f = ndimage.gaussian_filter(np.pad(mask.astype(np.float32), pad), sigma) if sigma > 0 else \
        np.pad(mask.astype(np.float32), pad)
    polys = []
    for c in measure.find_contours(f, 0.5):
        if len(c) < 4:
            continue
        P = Polygon(c[:, ::-1] - pad)
        if not P.is_valid:
            P = shapely.make_valid(P)
        for q in shapely.get_parts(P):
            if isinstance(q, Polygon) and q.area > 0:
                polys.append(q)
    polys.sort(key=lambda q: -q.area)
    g = Polygon()
    for q in polys:
        g = g.symmetric_difference(q)
    g = shapely.make_valid(g.buffer(0))
    if simplify_px > 0:
        g = g.simplify(simplify_px, preserve_topology=True)
    return _polygonal(g)


def _polygonal(g):
    parts = [p for p in shapely.get_parts(g) if isinstance(p, Polygon) and not p.is_empty]
    parts += [q for p in shapely.get_parts(g) if isinstance(p, MultiPolygon) for q in p.geoms]
    return MultiPolygon(parts) if len(parts) != 1 else parts[0]


def ring_resample(poly: np.ndarray, spacing: float) -> tuple[np.ndarray, np.ndarray]:
    """Closed polyline resampled at ~``spacing`` by arc length; also returns the index of the nearest
    source vertex for every output vertex (to carry per-vertex attributes)."""
    P = np.asarray(poly, float)
    Q = np.vstack([P, P[:1]])
    seg = np.hypot(*np.diff(Q, axis=0).T)
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    L = cum[-1]
    n = max(8, int(round(L / spacing)))
    t = np.arange(n) * (L / n)
    x = np.interp(t, cum, Q[:, 0])
    y = np.interp(t, cum, Q[:, 1])
    k = np.clip(np.searchsorted(cum, t, side="right") - 1, 0, len(P) - 1)
    frac = (t - cum[k]) / np.maximum(seg[k], 1e-12)
    near = np.where(frac < 0.5, k, (k + 1) % len(P))
    return np.stack([x, y], 1), near


def outward_normals(P: np.ndarray) -> np.ndarray:
    """Unit outward normals of a closed polygon (any orientation)."""
    t = np.roll(P, -1, axis=0) - np.roll(P, 1, axis=0)
    n = np.stack([t[:, 1], -t[:, 0]], 1)
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
    area = 0.5 * np.sum(P[:, 0] * np.roll(P[:, 1], -1) - np.roll(P[:, 0], -1) * P[:, 1])
    return n if area > 0 else -n


def circular_smooth(x: np.ndarray, half: int) -> np.ndarray:
    if half < 1:
        return x.copy()
    k = np.ones(2 * half + 1) / (2 * half + 1)
    return np.convolve(np.concatenate([x[-half:], x, x[:half]]), k, mode="valid")


def ring_distance_to(mask_idx: np.ndarray, spacing: float) -> np.ndarray:
    """Arc distance (in ``spacing`` units x spacing) from every ring vertex to the nearest True vertex."""
    n = len(mask_idx)
    if not mask_idx.any():
        return np.full(n, np.inf)
    pos = np.nonzero(mask_idx)[0]
    i = np.arange(n)
    d = np.abs(i[:, None] - pos[None, :])
    d = np.minimum(d, n - d).min(axis=1)
    return d * spacing


def grow_lens(ring: np.ndarray, types: np.ndarray, rimw_px: np.ndarray, mm_per_px: float, spacing_px: float,
              clip_geom, material: np.ndarray | None = None, matte: np.ndarray | None = None,
              vent: np.ndarray | None = None) -> tuple[Polygon, np.ndarray]:
    """The lens outline grown outward on frame-bounded vertices by max(0.6 mm, 35 % rim width), tapered to 0
    over 2 mm toward free/rimless vertices, clipped to ``clip_geom`` (lens + frame region + tuck allowance).

    A free/rimless vertex with material (``material``: the S2 matte and the S5 geometry seen by the source camera:
    a rimless lens meeting a drill mount or hinge block, a shield's lateral edge meeting its temple root) within
    ``TUCK_REACH_MM`` beyond it reaches that material and tucks ``TUCK_FREE_MM`` under it, so no sliver of backdrop
    stays enclosed between the lens sheet and the hardware in another camera. Where nothing is within reach the
    edge stays exact. With ``matte`` (the source photo's matte) the march is blocked by the first BACKDROP pixel:
    material seen beyond a measured gap (a shield's brow vents, a free edge that ends short of its temple root) is
    not reached - the photo shows backdrop between them, so there is no sliver to hide. Free/rimless vertices within
    ``TUCK_JUNCTION_MM`` of a frame-bounded or tucking vertex (a corner where the edge leaves what it goes under)
    always tuck. A free edge whose outward neighbour is a VENT (``vent``: S2 openings) reaches ``TUCK_FREE_MM`` into it: the
    vent's lower rim is the lens's own edge, and a vent kept open by S6 (walls along the source ray) would otherwise show
    a sliver of backdrop under the lens edge wherever a camera registers the edge a pixel off (oakley's inner vents in
    the gate's refined front camera: 21 px of seam gap)."""
    fb = types == TYPE_FRAME
    g_mm = np.where(fb, np.maximum(GROW_MIN_MM, GROW_RIM_SHARE * rimw_px * mm_per_px), 0.0)
    d_nf = ring_distance_to(~fb, spacing_px * mm_per_px)
    g_mm = g_mm * np.clip(d_nf / GROW_TAPER_MM, 0.0, 1.0)
    g_mm = circular_smooth(g_mm, max(1, int(round(1.0 / (spacing_px * mm_per_px)))))
    g_mm = np.where(fb, g_mm, 0.0)
    if material is not None and (~fb).any():
        # march outward: the first material within TUCK_REACH_MM decides; the lens reaches TUCK_FREE_MM under it
        nrm = outward_normals(ring)
        H, W = material.shape
        hit = np.full(len(ring), np.inf)
        blocked = np.zeros(len(ring), bool)
        mat_b = np.asarray(material, bool)
        matte_b = None if matte is None else np.asarray(matte, bool)
        for dmm in np.arange(0.0, TUCK_REACH_MM + 1e-9, 0.1):
            probe = ring + (dmm / mm_per_px + TUCK_PROBE_PX) * nrm
            ui = np.clip(np.round(probe[:, 0]).astype(int), 0, W - 1)
            vi = np.clip(np.round(probe[:, 1]).astype(int), 0, H - 1)
            here = mat_b[vi, ui]
            new = np.isinf(hit) & ~blocked & here
            hit[new] = dmm
            if matte_b is not None:
                blocked |= np.isinf(hit) & ~here & ~matte_b[vi, ui]
        touch = ~fb & np.isfinite(hit)
        g_mm = np.where(touch, np.maximum(g_mm, np.where(touch, hit, 0.0) + TUCK_FREE_MM), g_mm)
        if vent is not None and np.asarray(vent, bool).any():
            probe = ring + TUCK_PROBE_PX * nrm
            ui = np.clip(np.round(probe[:, 0]).astype(int), 0, W - 1)
            vi = np.clip(np.round(probe[:, 1]).astype(int), 0, H - 1)
            at_vent = ~fb & ~touch & np.asarray(vent, bool)[vi, ui]
            g_mm = np.where(at_vent, np.maximum(g_mm, TUCK_FREE_MM), g_mm)
            touch = touch | at_vent
        g_mm = np.where(fb, np.maximum(g_mm, TUCK_FREE_MM), g_mm)    # no taper below the tuck at a free/frame junction
        held = fb | touch
        if held.any():
            # a free/rimless vertex within TUCK_JUNCTION_MM (along the ring) of one that goes under something (a
            # frame-bounded vertex, or a free one that tucks under hardware) is the corner where the edge leaves it:
            # it tucks too, else a hairline opens at the corner (miu's drill-mount ends)
            near = ~held & (ring_distance_to(held, spacing_px * mm_per_px) <= TUCK_JUNCTION_MM)
            g_mm = np.where(near, np.maximum(g_mm, TUCK_FREE_MM), g_mm)
    pts = ring + (g_mm / mm_per_px)[:, None] * outward_normals(ring)
    exact = Polygon(ring)
    grown = shapely.make_valid(Polygon(pts))
    grown = shapely.union(grown, exact)
    grown = shapely.intersection(grown, clip_geom)
    parts = [p for p in shapely.get_parts(_polygonal(grown)) if isinstance(p, Polygon)]
    grown = max(parts, key=lambda p: p.area) if parts else exact
    grown = clean_polygons(Polygon(grown.exterior), 0.05, 0.0)       # no holes in a lens
    grown = max(grown, key=lambda p: p.area) if grown else exact
    return grown, g_mm


# =========================================================================== triangulation
def _clean_ring(c: np.ndarray, tol: float) -> np.ndarray | None:
    c = np.asarray(c, float)
    if len(c) > 1 and np.allclose(c[0], c[-1]):
        c = c[:-1]
    keep = [0]
    for k in range(1, len(c)):
        if np.hypot(*(c[k] - c[keep[-1]])) > tol:
            keep.append(k)
    c = c[keep]
    if len(c) > 2 and np.hypot(*(c[-1] - c[0])) <= tol:
        c = c[:-1]
    return c if len(c) >= 3 else None


def clean_polygons(geom, tol: float, min_area: float) -> list[Polygon]:
    """Polygon parts with near-duplicate ring vertices removed; rings/parts that degenerate are dropped."""
    out = []
    for p in shapely.get_parts(_polygonal(geom)):
        if not isinstance(p, Polygon) or p.is_empty:
            continue
        ext = _clean_ring(np.asarray(p.exterior.coords), tol)
        if ext is None:
            continue
        holes = [h for h in (_clean_ring(np.asarray(r.coords), tol) for r in p.interiors) if h is not None]
        q = Polygon(ext, holes)
        if not q.is_valid:
            q = shapely.make_valid(q)
        for r in shapely.get_parts(_polygonal(q)):
            if isinstance(r, Polygon) and r.area >= min_area:
                out.append(r)
    return out


def _ring_coords(poly: Polygon) -> list[np.ndarray]:
    rings = [np.asarray(poly.exterior.coords)[:-1]]
    rings += [np.asarray(r.coords)[:-1] for r in poly.interiors]
    return rings


def triangulate_cdt(poly: Polygon) -> tuple[np.ndarray, np.ndarray]:
    """shapely constrained Delaunay of a polygon (with holes): vertices (n,2) and triangles (m,3)."""
    rings = _ring_coords(poly)
    P = np.vstack(rings)
    index = {}
    for k, (x, y) in enumerate(map(tuple, P)):
        index.setdefault((x, y), k)
    tris = []
    for t in shapely.get_parts(shapely.constrained_delaunay_triangles(poly)):
        c = np.asarray(t.exterior.coords)[:3]
        try:
            tri = [index[(float(x), float(y))] for x, y in c]
        except KeyError:                          # a coordinate CDT created (should not happen)
            continue
        if len(set(tri)) == 3:
            tris.append(tri)
    T = np.asarray(tris, np.int64).reshape(-1, 3)
    return P, T


def orient_triangles(P: np.ndarray, T: np.ndarray, sign: float = -1.0) -> np.ndarray:
    """Make every triangle's (u, v) shoelace area have ``sign`` (-1: counter-clockwise on screen with v down,
    i.e. +Z toward a front camera). Drops zero-area triangles."""
    a = P[T[:, 0]]
    b = P[T[:, 1]]
    c = P[T[:, 2]]
    area = 0.5 * ((b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (c[:, 0] - a[:, 0]) * (b[:, 1] - a[:, 1]))
    T = T[np.abs(area) > 1e-12].copy()
    area = area[np.abs(area) > 1e-12]
    flip = np.sign(area) != sign
    T[flip] = T[flip][:, [0, 2, 1]]
    return T


def refine_long_edges(P: np.ndarray, T: np.ndarray, max_len: float, max_iter: int = 10) -> tuple[np.ndarray, np.ndarray]:
    """Conforming red-green refinement: split every edge longer than ``max_len`` at its midpoint (boundary
    edges shorter than ``max_len`` are never touched). Orientation is preserved."""
    P = [tuple(p) for p in P]
    T = np.asarray(T, np.int64)
    for _ in range(max_iter):
        Pa = np.asarray(P)
        E = np.concatenate([T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]])
        Es = np.sort(E, axis=1)
        L = np.linalg.norm(Pa[Es[:, 0]] - Pa[Es[:, 1]], axis=1)
        long_e = np.unique(Es[L > max_len], axis=0)
        if not len(long_e):
            break
        mid = {}
        for a, b in long_e:
            mid[(int(a), int(b))] = len(P)
            P.append(tuple((Pa[a] + Pa[b]) / 2.0))

        def m(a, b):
            return mid.get((a, b) if a < b else (b, a))
        out = []
        for a, b, c in T.tolist():
            s = [m(a, b), m(b, c), m(c, a)]
            k = sum(x is not None for x in s)
            if k == 0:
                out.append((a, b, c))
                continue
            # rotate so the pattern is canonical
            for _r in range(3):
                s = [m(a, b), m(b, c), m(c, a)]
                if k == 1 and s[0] is not None:
                    break
                if k == 2 and s[0] is not None and s[1] is not None:
                    break
                if k == 3:
                    break
                a, b, c = b, c, a
            mab, mbc, mca = s
            if k == 1:
                out += [(a, mab, c), (mab, b, c)]
            elif k == 2:
                out += [(mab, b, mbc), (a, mab, mbc), (a, mbc, c)]
            else:
                out += [(a, mab, mca), (mab, b, mbc), (mca, mbc, c), (mab, mbc, mca)]
        T = np.asarray(out, np.int64)
    return np.asarray(P, float), T


def triangulate_with_interior(poly: Polygon, ring_spacing: float, interior_spacing: float,
                              max_iter: int = 8) -> tuple[np.ndarray, np.ndarray]:
    """Conforming Delaunay of a simple polygon with a hexagonal interior point grid; every boundary edge is
    present (missing ones are split at their midpoint and the triangulation redone)."""
    ring = np.asarray(poly.exterior.coords)[:-1]
    x0, y0, x1, y1 = poly.bounds
    hs = interior_spacing
    ys = np.arange(y0 + hs / 2, y1, hs * math.sqrt(3) / 2)
    pts = []
    for r, yy in enumerate(ys):
        xs = np.arange(x0 + (hs / 2 if r % 2 else 0.0), x1, hs)
        pts.append(np.stack([xs, np.full(len(xs), yy)], 1))
    G = np.vstack(pts) if pts else np.zeros((0, 2))
    if len(G):
        inside = shapely.contains_xy(poly, G[:, 0], G[:, 1])
        G = G[inside]
        if len(G):
            dist = shapely.distance(shapely.points(G), poly.exterior)
            G = G[dist >= 0.6 * hs]
    for _ in range(max_iter):
        n = len(ring)
        P = np.vstack([ring, G])
        tri = Delaunay(P)
        T = tri.simplices.astype(np.int64)
        c = P[T].mean(axis=1)
        T = T[shapely.contains_xy(poly, c[:, 0], c[:, 1])]
        E = np.sort(np.concatenate([T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]]), axis=1)
        have = set(map(tuple, E.tolist()))
        missing = [k for k in range(n) if tuple(sorted((k, (k + 1) % n))) not in have]
        if not missing:
            return P, T
        new = []
        miss = set(missing)
        for k in range(n):
            new.append(ring[k])
            if k in miss:
                new.append((ring[k] + ring[(k + 1) % n]) / 2.0)
        ring = np.asarray(new)
        if len(G):
            d = cKDTree(ring).query(G)[0]
            G = G[d >= 0.5 * np.median(np.hypot(*np.diff(np.vstack([ring, ring[:1]]), axis=0).T))]
    raise RuntimeError("Boundary edges still missing after refinement")


def boundary_edges(T: np.ndarray) -> np.ndarray:
    """Directed edges used by exactly one triangle (in the triangles' own direction)."""
    E = np.concatenate([T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]])
    Es = np.sort(E, axis=1)
    _, inv, cnt = np.unique(Es, axis=0, return_inverse=True, return_counts=True)
    return E[cnt[inv.ravel()] == 1]


def solid_from_cap(front: np.ndarray, back: np.ndarray, T: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Closed solid from a front cap (triangles T, normals toward the viewer) and a back cap with the same
    vertex order: V = [front; back], faces = front cap, reversed back cap, walls. Region per face."""
    n = len(front)
    V = np.vstack([front, back])
    Fb = T[:, [0, 2, 1]] + n
    B = boundary_edges(T)
    a, b = B[:, 0], B[:, 1]
    W = np.concatenate([np.stack([b, a, a + n], 1), np.stack([b, a + n, b + n], 1)])
    F = np.vstack([T, Fb, W]).astype(np.int32)
    region = np.concatenate([np.zeros(len(T)), np.ones(len(Fb)), np.full(len(W), 2)]).astype(np.int8)
    return V, F, region


def mesh_topology(F: np.ndarray, n_vertices: int | None = None) -> dict:
    """Closed + consistently oriented check: every directed edge has exactly one reverse partner."""
    F = np.asarray(F, np.int64)
    E = np.concatenate([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]])
    key = E[:, 0] * (int(E.max()) + 1) + E[:, 1]
    rkey = E[:, 1] * (int(E.max()) + 1) + E[:, 0]
    uk, cnt = np.unique(key, return_counts=True)
    dup_directed = int((cnt > 1).sum())
    has_rev = np.isin(rkey, uk)
    Es = np.sort(E, axis=1)
    _, ucnt = np.unique(Es, axis=0, return_counts=True)
    out = {"faces": int(len(F)), "boundary_edges": int((ucnt == 1).sum()), "nonmanifold_edges": int((ucnt > 2).sum()),
           "unpaired_directed_edges": int((~has_rev).sum()), "duplicate_directed_edges": dup_directed}
    out["watertight"] = bool(out["boundary_edges"] == 0 and out["nonmanifold_edges"] == 0
                             and out["unpaired_directed_edges"] == 0 and dup_directed == 0)
    if n_vertices is not None:
        m = o3d.geometry.TriangleMesh(o3d.utility.Vector3dVector(np.zeros((n_vertices, 3))),
                                      o3d.utility.Vector3iVector(F.astype(np.int32)))
        out["vertex_manifold_o3d"] = bool(m.is_vertex_manifold())
    return out


def signed_volume(V: np.ndarray, F: np.ndarray) -> float:
    a, b, c = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
    return float(np.sum(np.einsum("ij,ij->i", a, np.cross(b, c))) / 6.0)


# =========================================================================== lifting helpers
def lift_with_fallback(px: np.ndarray, src: depth.SourceView, frame: NormFrame, surface) -> tuple[np.ndarray, int]:
    """Lift; a vertex whose ray does not cross the surface (just outside the surface's silhouette, e.g. a
    photo outline a little wider than the generator at a shield's endpiece) takes the ray's grazing point,
    where |signed distance| is smallest."""
    P = depth.lift_px(px, src.camera, frame, surface, mirror_width=src.mirror_width)
    bad = ~np.all(np.isfinite(P), axis=1)
    if bad.any():
        O, D = depth.camera_rays_mm(src.camera, frame, px[bad], src.mirror_width)
        Q, _ = depth.closest_on_rays(surface, O, D)
        P[bad] = Q
    return P, int(bad.sum())


def back_points(P_front: np.ndarray, px: np.ndarray, src: depth.SourceView, frame: NormFrame, base: depth.Base,
                thickness, w_ray=1.0, max_deg: float = EXTRUDE_MAX_DEG) -> np.ndarray:
    """Back points of the plate: ``thickness`` deeper along the S4 depth axis (param depth d -> d - t, the axis S4
    measured the generator's entry/exit thickness along: -Z when planar, radial toward the axis when cylinder).

    ``w_ray`` in [0, 1] blends the extrusion DIRECTION between that depth axis (0: the plate's walls are the
    generator's walls, a prism along its own depth; the outer silhouette rule decides which edge meets the photo
    outline) and the source camera's EXACT ray (1: the wall is edge-on in the source photo, so a lens hole stays
    the exact S2 polygon - also on a wrapped shield, whose lateral ends the source ray meets 40-70 deg off the
    radial axis). The param depth drop is ``t`` (length t / cos(angle to the axis)) while that angle is at most
    ``max_deg`` (70 deg, the graze limit beyond which S6 builds no plate); beyond it the direction is kept and the
    LENGTH is capped at t / cos(max_deg): the rim is locally thinner along the axis rather than its wall bent into the
    hole (the old 25 deg direction clamp displaced the back edge of 188 of oakley's 399 hole vertices by up to 4.8 mm
    into the lens in the source view)."""
    P_front = np.asarray(P_front, float).reshape(-1, 3)
    s, _, _ = base.to_param(P_front)
    nb = -base.outward(s)
    t = np.broadcast_to(np.asarray(thickness, float), (len(P_front),))
    w = np.broadcast_to(np.asarray(w_ray, float), (len(P_front),))
    e = nb
    if np.any(w > 0):
        _, D = depth.camera_rays_mm(src.camera, frame, px, src.mirror_width)
        r = np.where(np.sum(D * nb, axis=1, keepdims=True) >= 0, D, -D)          # away from the front
        e = (1.0 - w)[:, None] * nb + w[:, None] * r
        e /= np.maximum(np.linalg.norm(e, axis=1, keepdims=True), 1e-12)
    cosa = np.clip(np.sum(e * nb, axis=1), 1e-6, 1.0)
    length = np.minimum(t / cosa, t / math.cos(math.radians(max_deg)))
    return P_front + length[:, None] * e


def tuck_material(product: str, run: str, a2: dict, src: depth.SourceView, frame: NormFrame,
                  hardware: np.ndarray | None = None) -> tuple[np.ndarray, dict]:
    """Source-px mask of what a free/rimless lens edge may tuck under: the S2 matte (photo material) plus the S5
    geometry (arms and donor blocks, rendered through the source camera) - the lens tucks under whatever the
    delivered model puts next to it, so no hairline of backdrop opens between them.

    The lens's OWN matte is not material: S0 grows the matte ``intake.GROW_PX`` px by hysteresis (+ 1 px of
    anti-aliasing) around everything, the lens included, so the matte within that halo of the lens polygons is
    removed first (else every free edge "found material" at the first probe and grew into the backdrop). The S2
    HARDWARE components (``hardware``: frame parts that are not a rim band - drill mounts, hinge blocks) are real
    material right up to the lens edge and are added back whole (else a thin mount end no longer tucked: miu's
    corner seam)."""
    from . import intake
    mat = np.asarray(a2["fg_sym"], bool).copy()
    polys = depth.lens_polys(a2)
    halo = intake.GROW_PX + 1
    if polys:
        lens = depth.poly_mask(polys, mat.shape)
        mat &= ~ndimage.binary_dilation(lens, iterations=halo)
    info = {"s5_pixels": 0, "lens_halo_removed_px": halo}
    if hardware is not None:
        hw = np.asarray(hardware, bool)
        info["hardware_pixels_kept"] = int((hw & ~mat).sum())
        mat |= hw
    sd5 = stage_dir(run, product, "s5_temples")
    if sd5.done() and (sd5.root / "arrays.npz").exists():
        a5 = dict(np.load(sd5.root / "arrays.npz", allow_pickle=False))
        parts = [(np.asarray(a5[f"temple_{s}_V"], float), np.asarray(a5[f"temple_{s}_F"], np.int64))
                 for s in ("R", "L") if f"temple_{s}_V" in a5]
        if parts:
            r = render_parts(parts, src.camera, frame, src.shape, src.mirror_width)
            m5 = ndimage.binary_dilation(r["mask"], iterations=1)
            info["s5_pixels"] = int((m5 & ~mat).sum())
            mat |= m5
    return mat, info


def part_share(poly, mask: np.ndarray, step: float = 1.0) -> float:
    """Share of a polygon's pixel-centre grid points (``step`` px) that lie in ``mask``."""
    x0, y0, x1, y1 = poly.bounds
    xs, ys = np.meshgrid(np.arange(math.floor(x0), math.ceil(x1) + 1, step), np.arange(math.floor(y0), math.ceil(y1) + 1, step))
    inside = shapely.contains_xy(poly, xs.ravel(), ys.ravel())
    if not inside.any():
        return 0.0
    H, W = mask.shape
    u = np.clip(np.round(xs.ravel()[inside]).astype(int), 0, W - 1)
    v = np.clip(np.round(ys.ravel()[inside]).astype(int), 0, H - 1)
    return float(mask[v, u].mean())


def hardware_core(px: np.ndarray, T: np.ndarray, idx: np.ndarray, scene, src: depth.SourceView, frame: NormFrame,
                  df: depth.DepthField, fallback_front: np.ndarray) -> tuple[np.ndarray, np.ndarray, dict]:
    """Front/back points of a hardware CORE. The generator's hardware is the visible part (S5 donor); the plate there
    is only a thin core inside it (it keeps the frame node's front/back-cap topology for S7 and a bridge on the
    symmetry axis for the origin rule). Along the source ray through each vertex: the generator's first two
    crossings, each inset ``CORE_INSET_MM`` (>= ``CORE_MIN_T_MM`` thick, centred where the hardware is thinner);
    a vertex whose ray misses the generator keeps the S4 front and a ``CORE_MIN_T_MM`` depth. The depths are
    smoothed over the cap graph (``CORE_SMOOTH_ITERS`` neighbour averages): no spikes where a ray grazes an edge."""
    O, D = depth.camera_rays_mm(src.camera, frame, px, src.mirror_width)
    t1, thick = depth._cast(scene, O, D)                # entry distance, entry-to-exit distance
    hit = np.isfinite(t1) & np.isfinite(thick) & (thick < CORE_MAX_DEPTH_MM)
    tf0 = np.where(hit, t1, 0.0)
    tb0 = np.where(hit, t1 + thick, 0.0)
    # only a crossing near the plate's own front surface is the hardware (not a temple far behind it)
    tl0 = np.sum((fallback_front - O) * D, axis=1)
    hit &= np.minimum(np.abs(tf0 - tl0), np.abs(tb0 - tl0)) <= CORE_NEAR_FRONT_MM
    d1 = df.base.to_param(O + tf0[:, None] * D)[2]
    d2 = df.base.to_param(O + tb0[:, None] * D)[2]
    tf = np.where(d1 >= d2, tf0, tb0)                   # the front crossing has the larger param depth
    tb = np.where(d1 >= d2, tb0, tf0)
    sgn = np.sign(tb - tf + 1e-12)                       # ray parameter direction from front to back
    tfront = tf + sgn * CORE_INSET_MM
    tback = tb - sgn * CORE_INSET_MM
    thin = hit & ((tback - tfront) * sgn < CORE_MIN_T_MM)
    mid = 0.5 * (tf + tb)
    tfront = np.where(thin, mid - sgn * 0.5 * CORE_MIN_T_MM, tfront)
    tback = np.where(thin, mid + sgn * 0.5 * CORE_MIN_T_MM, tback)
    tl = np.sum((fallback_front - O) * D, axis=1)
    s_fb = df.base.to_param(fallback_front)[0]
    sgn_m = np.sign(np.sum(-df.base.outward(s_fb) * D, axis=1) + 1e-12)
    tfront = np.where(hit, tfront, tl)
    tback = np.where(hit, tback, tl + sgn_m * CORE_MIN_T_MM)
    pos = -np.ones(int(T.max()) + 1, np.int64)
    pos[idx] = np.arange(len(idx))
    E = np.concatenate([T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]])
    E = E[(pos[E[:, 0]] >= 0) & (pos[E[:, 1]] >= 0)]
    a_, b_ = pos[E[:, 0]], pos[E[:, 1]]
    for _ in range(CORE_SMOOTH_ITERS):
        for arr in (tfront, tback):
            acc = arr.copy()
            cnt = np.ones(len(arr))
            np.add.at(acc, a_, arr[b_])
            np.add.at(cnt, a_, 1.0)
            arr[:] = acc / cnt
    Fp = O + tfront[:, None] * D
    Bp = O + tback[:, None] * D
    return Fp, Bp, {"ray_hits": int(hit.sum()), "misses": int((~hit).sum()), "thin": int(thin.sum()),
                    "depth_mm_median": round(float(np.median(np.abs(tback - tfront))), 3), "_hit": hit}


def graph_smooth(values: np.ndarray, T: np.ndarray, iters: int, dilate: int = 0) -> np.ndarray:
    """Per-vertex scalar over the triangle graph: ``dilate`` neighbour-max rounds (keeps the amplitude of an
    isolated value), then ``iters`` Jacobi averages (each vertex with its neighbours)."""
    v = np.asarray(values, float).copy()
    if T is None or not len(T) or (iters <= 0 and dilate <= 0):
        return v
    E = np.unique(np.sort(np.concatenate([T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]]), axis=1), axis=0)
    for _ in range(dilate):
        m = v.copy()
        np.maximum.at(m, E[:, 0], v[E[:, 1]])
        np.maximum.at(m, E[:, 1], v[E[:, 0]])
        v = m
    deg = np.ones(len(v))
    np.add.at(deg, E[:, 0], 1.0)
    np.add.at(deg, E[:, 1], 1.0)
    for _ in range(iters):
        acc = v.copy()
        np.add.at(acc, E[:, 0], v[E[:, 1]])
        np.add.at(acc, E[:, 1], v[E[:, 0]])
        v = acc / deg
    return v


def vertex_areas(P: np.ndarray, T: np.ndarray) -> np.ndarray:
    """One third of the area of every triangle incident to each vertex (a density-independent quadrature weight)."""
    P = np.asarray(P, float)
    T = np.asarray(T, np.int64)
    A = 0.5 * np.linalg.norm(np.cross(P[T[:, 1]] - P[T[:, 0]], P[T[:, 2]] - P[T[:, 0]]), axis=1)
    out = np.zeros(len(P))
    for k in range(3):
        np.add.at(out, T[:, k], A / 3.0)
    return out


def metric_smooth(values: np.ndarray, P: np.ndarray, T: np.ndarray | None, sigma_mm: float,
                  dilate_mm: float = 0.0, weights: np.ndarray | None = None) -> np.ndarray:
    """A per-vertex scalar smoothed in MILLIMETRES, independent of the mesh density: ``dilate_mm`` neighbourhood
    max (an isolated value keeps its amplitude), then an area-weighted Gaussian of ``sigma_mm`` (truncated at 3
    sigma) over the vertices' 3D positions (``weights`` overrides the areas, e.g. arc length on an outline). A graph
    average is no substitute: the cap is 0.5 mm dense along a lens
    ring and 3 mm coarse elsewhere, so a fixed number of neighbour rounds smooths 1.5 mm at the ring (where
    per-vertex carve decisions made the pleats and notches) and 9 mm in the middle of a rim."""
    v = np.asarray(values, float).copy()
    P = np.asarray(P, float)
    n = len(v)
    if n == 0 or sigma_mm <= 0:
        return v
    tree = cKDTree(P)
    if dilate_mm > 0:
        D = tree.sparse_distance_matrix(tree, dilate_mm, output_type="coo_matrix")
        m = v.copy()
        np.maximum.at(m, D.row, v[D.col])
        v = m
    from scipy import sparse
    D = tree.sparse_distance_matrix(tree, 3.0 * sigma_mm, output_type="coo_matrix")
    if weights is not None:
        a = np.asarray(weights, float)
    else:
        a = vertex_areas(P, T) if T is not None and len(T) else np.ones(n)
    a = np.maximum(a, 1e-9)
    w = np.exp(-0.5 * (D.data / sigma_mm) ** 2) * a[D.col]
    W = sparse.coo_matrix((w, (D.row, D.col)), shape=(n, n)).tocsr() + sparse.diags(a)   # + self (distance 0)
    return np.asarray(W @ v).ravel() / np.asarray(W.sum(axis=1)).ravel()


def meet_donors(F3: np.ndarray, B3: np.ndarray, donors: list[tuple[np.ndarray, np.ndarray]] | None,
                skip: np.ndarray | None = None) -> tuple[np.ndarray, dict]:
    """The plate's back meets the S5 donor geometry directly behind it: a back-cap point whose extrusion ray (front ->
    back) hits a donor surface within ``MEET_DONOR_MM`` behind it moves back to ``donor.OVERLAP_MM`` inside it, like
    every donor reaches ``OVERLAP_MM`` into the plate back. The S4 thickness is a smooth field (8 mm knots): where the
    generator's own back sits a millimetre deeper (a nose pad joining the back of a bridge, vb), the donor cut there is
    not on the plate back and a slot opened between the two, seen through at +/-35 deg yaw. ``skip``: hardware cores
    (they sit inside the donor already). Returns (B3, info)."""
    B3 = np.asarray(B3, float).copy()
    info = {"moved": 0}
    if not donors:
        return B3, info
    Vs = [np.asarray(v, float) for v, f in donors if len(f)]
    Fs = [np.asarray(f, np.int64) for v, f in donors if len(f)]
    if not Vs:
        return B3, info
    off = np.cumsum([0] + [len(v) for v in Vs[:-1]])
    scene = o3d.t.geometry.RaycastingScene()
    scene.add_triangles(o3d.core.Tensor(np.vstack(Vs).astype(np.float32)),
                        o3d.core.Tensor(np.vstack([f + o for f, o in zip(Fs, off)]).astype(np.uint32)))
    e = B3 - np.asarray(F3, float)
    e /= np.maximum(np.linalg.norm(e, axis=1, keepdims=True), 1e-12)
    t = scene.cast_rays(o3d.core.Tensor(np.hstack([B3, e]).astype(np.float32)))["t_hit"].numpy().astype(float)
    hit = np.isfinite(t) & (t <= MEET_DONOR_MM)
    if skip is not None:
        hit &= ~np.asarray(skip, bool)
    B3[hit] += e[hit] * (t[hit] + donor.OVERLAP_MM)[:, None]
    info.update({"moved": int(hit.sum()), "reach_mm": MEET_DONOR_MM,
                 "moved_mm_max": round(float(t[hit].max() + donor.OVERLAP_MM), 3) if hit.any() else 0.0})
    return B3, info


def carve_plate(F3: np.ndarray, B3: np.ndarray, px: np.ndarray, t_min: np.ndarray, hulls: list[HullView],
                src: depth.SourceView, frame: NormFrame, iters: int = CARVE_ITERS,
                skip: np.ndarray | None = None, T: np.ndarray | None = None,
                hold: np.ndarray | None = None, t_hold: float = 0.0) -> tuple[np.ndarray, np.ndarray, dict]:
    """Visual-hull carving of the plate by the OTHER fit views (never the outline source, whose outline the plate
    already is). The plate is inside every fit photo's silhouette or it is wrong there:

    - a front point outside some view's (dilated) matte slides back along its SOURCE ray (its source-photo pixel is
      unchanged) by the smallest step <= ``CARVE_FRONT_MAX_MM`` that brings it inside; its back point moves with it;
    - a back point outside while its front point is inside comes forward along the plate's own extrusion segment
      to the deepest inside point (bisection), never thinner than ``t_min`` (else ``CARVE_MIN_T_MM``).
    A point whose front stays outside is left alone (its excess is not a depth error: the outline or the camera).
    Both amounts (slide, removed depth) are a correction of the S4 front surface and thickness, which S4 represents
    with ``depth.KNOT_MM`` B-spline knots: the correction is kept as smooth as what it corrects (``metric_smooth``,
    sigma ``CARVE_SIGMA_MM`` = the Gaussian whose half-power wavelength is one knot, after a max over the same
    radius). Per-vertex decisions against a noisy matte otherwise leave pleats on the rim and notches along a lens
    hole that show in every off-axis view.
    ``hold`` (with ``t_hold`` = lens thickness + 2 x the containment margin): the rim band that holds a lens is never
    carved thinner than ``t_hold`` (the lens is placed afterwards at the mid-depth of the rim AS CARVED, ``build``)."""
    F3, B3 = np.asarray(F3, float).copy(), np.asarray(B3, float).copy()
    info = {"views": [h.view for h in hulls], "tol_px": [round(h.tol_px, 2) for h in hulls]}
    if not hulls or not len(F3):
        return F3, B3, info
    n = len(F3)
    skip = np.zeros(n, bool) if skip is None else np.asarray(skip, bool)
    t_old = np.linalg.norm(B3 - F3, axis=1)
    e = (B3 - F3) / np.maximum(t_old[:, None], 1e-12)                  # extrusion direction (front -> back)
    _, D = depth.camera_rays_mm(src.camera, frame, px, src.mirror_width)
    r = np.where(np.sum(D * e, axis=1, keepdims=True) >= 0, D, -D)      # source ray, front -> back
    slide = np.zeros(n)
    of = (hull_outside(hulls, F3) > 0) & ~skip
    if of.any():
        idx = np.nonzero(of)[0]
        best = np.full(len(idx), np.nan)
        for step in np.arange(CARVE_STEP_MM, CARVE_FRONT_MAX_MM + 1e-9, CARVE_STEP_MM):
            todo = np.isnan(best)
            if not todo.any():
                break
            ok = hull_outside(hulls, F3[idx[todo]] + step * r[idx[todo]]) <= 0
            best[np.nonzero(todo)[0][ok]] = step
        slide[idx] = np.nan_to_num(best, nan=0.0)
    Fs = F3 + slide[:, None] * r
    fin = hull_outside(hulls, Fs) <= 0
    ob = fin & (hull_outside(hulls, Fs + t_old[:, None] * e) > 0) & ~skip
    removed = np.zeros(n)
    if ob.any():
        idx = np.nonzero(ob)[0]
        lo = np.minimum(t_min[idx], t_old[idx])
        hi = t_old[idx].copy()
        for _ in range(iters):
            mid = 0.5 * (lo + hi)
            ins = hull_outside(hulls, Fs[idx] + mid[:, None] * e[idx]) <= 0
            lo = np.where(ins, mid, lo)
            hi = np.where(ins, hi, mid)
        removed[idx] = t_old[idx] - lo
    slide_raw, removed_raw = slide.copy(), removed.copy()
    live = ~skip
    if live.any():
        # smoothed over the carvable plate only (a hardware core is never carved and must not dilute its neighbours)
        idx = np.nonzero(live)[0]
        pos = -np.ones(n, np.int64)
        pos[idx] = np.arange(len(idx))
        Tl = None
        if T is not None and len(T):
            Tl = pos[np.asarray(T, np.int64)]
            Tl = Tl[(Tl >= 0).all(axis=1)]
        slide[idx] = metric_smooth(slide[idx], F3[idx], Tl, CARVE_SIGMA_MM, CARVE_SIGMA_MM)
        removed[idx] = metric_smooth(removed[idx], F3[idx], Tl, CARVE_SIGMA_MM, CARVE_SIGMA_MM)
    slide[skip] = 0.0
    removed[skip] = 0.0
    slide = np.clip(slide, 0.0, CARVE_FRONT_MAX_MM)
    t_floor = np.asarray(t_min, float).copy()
    held = np.zeros(n, bool)
    if hold is not None and t_hold > 0:
        held = np.asarray(hold, bool) & live
        t_floor = np.where(held, np.maximum(t_floor, t_hold), t_floor)
    removed = np.clip(removed, 0.0, np.maximum(t_old - np.minimum(t_floor, t_old), 0.0))
    F3 = F3 + slide[:, None] * r
    B3 = F3 + (t_old - removed)[:, None] * e
    carved = removed > 1e-3
    info.update({"front_moved_vertices": int((slide > 1e-3).sum()), "front_outside_left": int((~fin & ~skip).sum()),
                 "back_carved_vertices": int(carved.sum()),
                 "carved_depth_mm_max": round(float(removed.max()), 3) if n else 0.0,
                 "carved_depth_mm_median_of_carved": round(float(np.median(removed[carved])), 3) if carved.any() else 0.0,
                 "raw": {"front_moved": int((slide_raw > 1e-3).sum()), "back_carved": int((removed_raw > 1e-3).sum()),
                         "carved_depth_mm_max": round(float(removed_raw.max()), 3) if n else 0.0},
                 "smooth": {"sigma_mm": CARVE_SIGMA_MM, "dilate_mm": CARVE_SIGMA_MM, "weights": "vertex area"},
                 "lens_hold_vertices": int(held.sum()), "t_hold_mm": round(float(t_hold), 3)})
    return F3, B3, info


def boundary_frame(P2: np.ndarray, T: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Boundary vertices of a 2D triangulation and their unit OUTWARD normals (average of the adjacent boundary
    edges' normals, each pointing away from its own triangle)."""
    E = np.concatenate([T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]])
    third = np.concatenate([T[:, 2], T[:, 0], T[:, 1]])
    Es = np.sort(E, axis=1)
    _, inv, cnt = np.unique(Es, axis=0, return_inverse=True, return_counts=True)
    b = cnt[inv.ravel()] == 1
    E, third = E[b], third[b]
    d = P2[E[:, 1]] - P2[E[:, 0]]
    nrm = np.stack([d[:, 1], -d[:, 0]], 1)
    nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-12)
    flip = np.sum(nrm * (P2[third] - P2[E[:, 0]]), axis=1) > 0
    nrm[flip] *= -1.0
    acc = np.zeros((len(P2), 2))
    np.add.at(acc, E[:, 0], nrm)
    np.add.at(acc, E[:, 1], nrm)
    verts = np.unique(E.ravel())
    m = acc[verts]
    m /= np.maximum(np.linalg.norm(m, axis=1, keepdims=True), 1e-12)
    return verts, m


def silhouette_rule(P2: np.ndarray, T: np.ndarray, lens_rings: list[np.ndarray], src: depth.SourceView,
                    frame: NormFrame, surf: depth.ParamSurface, base: depth.Base, thick_fn, ppm: float,
                    iters: int = SIL_ITERS, fixed: np.ndarray | None = None) -> dict:
    """Where each front-cap vertex goes in the source image so that the SOLID's silhouette is the photo outline.

    The plate is a prism along the S4 depth axis (``back_points`` with w = 0) on its outer boundary. Seen by a
    pitched or perspective camera its back edge projects displaced by delta from its front edge: the photo outline
    is the union of the two, i.e. at an outer boundary point with outward image normal m the outline is made by
    the FRONT edge when m.delta <= 0 and by the BACK edge when m.delta > 0 (a camera 10 deg above sees the top
    of a 6 mm rim: its outline there is the back-top edge, ~1 mm above the front-top edge). There the front edge
    moves inward by a = m.delta (fixed point, ``iters`` rounds), so the back edge lands on the outline. Lens-hole
    boundaries (within ``LENS_ADJ_PX`` / 0.3 mm of a lens ring) stay exact (a = 0) and their walls follow the
    source ray (w = 1: the hole stays the exact S2 polygon and the lens seam is unchanged). Interior vertices get
    the harmonic interpolation of both the displacement and w. Returns {q (n,2) image position of the front-cap
    vertices, w (n,), shift_px (n,), outer (n,) bool, lens_adj (n,) bool, info}."""
    n = len(P2)
    verts, m = boundary_frame(P2, T)
    tol = max(LENS_ADJ_PX, LENS_ADJ_MM * ppm)
    if lens_rings:
        lines = shapely.union_all([shapely.LinearRing(r) for r in lens_rings])
        dist = shapely.distance(shapely.points(P2[verts]), lines)
    else:
        dist = np.full(len(verts), np.inf)
    adj = dist <= tol
    if fixed is not None:                     # e.g. hardware cores: exact in the source view, walls along the ray
        adj |= np.asarray(fixed, bool)[verts]
    outer_v, m_out = verts[~adj], m[~adj]
    w = harmonic_fill(T, n, verts, np.where(adj, 1.0, 0.0)) if len(verts) else np.zeros(n)
    w = np.clip(w, 0.0, 1.0)
    a = np.zeros(len(outer_v))
    info = {"outer_vertices": int(len(outer_v)), "lens_vertices": int(adj.sum())}
    if len(outer_v):
        # outline geometry in mm (image plane at the source scale) and each outer vertex's arc-length weight
        E = np.concatenate([T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]])
        Es = np.sort(E, axis=1)
        _, inv_, cnt_ = np.unique(Es, axis=0, return_inverse=True, return_counts=True)
        Eb = Es[cnt_[inv_.ravel()] == 1]
        pos = -np.ones(n, np.int64)
        pos[outer_v] = np.arange(len(outer_v))
        Eb = Eb[(pos[Eb[:, 0]] >= 0) & (pos[Eb[:, 1]] >= 0)]
        Pmm = np.c_[P2[outer_v] / ppm, np.zeros(len(outer_v))]
        arc = np.full(len(outer_v), 1e-3)
        if len(Eb):
            L = np.linalg.norm(P2[Eb[:, 0]] - P2[Eb[:, 1]], axis=1) / ppm
            np.add.at(arc, pos[Eb[:, 0]], 0.5 * L)
            np.add.at(arc, pos[Eb[:, 1]], 0.5 * L)
        # the outward normal of a pixel-jagged outline (a temple removed above an endpiece) flips from vertex to
        # vertex: the shift direction is the normal of the outline smoothed at the same scale as the shift itself
        m_s = np.stack([metric_smooth(m_out[:, k], Pmm, None, SIL_SIGMA_MM, weights=arc) for k in range(2)], 1)
        nn = np.linalg.norm(m_s, axis=1, keepdims=True)
        m_out = np.where(nn > 1e-6, m_s / np.maximum(nn, 1e-12), m_out)
        for _ in range(iters):
            q = P2[outer_v] - a[:, None] * m_out
            Pf, _ = lift_with_fallback(q, src, frame, surf)
            Pb = back_points(Pf, q, src, frame, base, thick_fn(Pf, P2[outer_v]), 0.0)
            delta = src.project(Pb, frame) - q
            a = np.clip(np.sum(m_out * delta, axis=1), 0.0, SIL_MAX_MM * ppm)
        # smooth the shift along the outline in mm (arc-length weighted): a is m.delta of the S4 surface/thickness
        # (``depth.KNOT_MM`` knots), as smooth as what it derives from; where the outline turns from a front-edge
        # to a back-edge silhouette the shift ramps instead of stepping (no jagged edge, no folds)
        a = metric_smooth(a, Pmm, None, SIL_SIGMA_MM, weights=arc)
    D = np.zeros((n, 2))
    if len(verts):
        vals = np.zeros((len(verts), 2))
        vals[~adj] = -a[:, None] * m_out
        D[:, 0] = harmonic_fill(T, n, verts, vals[:, 0])
        D[:, 1] = harmonic_fill(T, n, verts, vals[:, 1])
    q = P2 + D

    def signed_area(P):
        A, B, C = P[T[:, 0]], P[T[:, 1]], P[T[:, 2]]
        return (B[:, 0] - A[:, 0]) * (C[:, 1] - A[:, 1]) - (C[:, 0] - A[:, 0]) * (B[:, 1] - A[:, 1])
    ref = np.sign(signed_area(P2))
    halvings = 0
    for _ in range(16):              # never fold the cap: halve the displacement at the vertices of flipped triangles
        bad = np.sign(signed_area(q)) != ref
        if not bad.any():
            break
        vb = np.unique(T[bad].ravel())
        D[vb] *= 0.5
        q = P2 + D
        halvings += int(len(vb))
    if np.any(np.sign(signed_area(q)) != ref):
        D[:] = 0.0
        q = P2.copy()
        info["fold_fallback"] = True
    shift = np.linalg.norm(D, axis=1)
    outer = np.zeros(n, bool)
    outer[outer_v] = True
    lens_adj = np.zeros(n, bool)
    lens_adj[verts[adj]] = True
    info.update({"shifted_vertices": int(np.sum(shift > 1e-3)),
                 "shift_mm_max": round(float(shift.max() / ppm), 3) if n else 0.0,
                 "shift_mm_median_outer": round(float(np.median(shift[outer_v]) / ppm), 3) if len(outer_v) else 0.0,
                 "back_edge_share_outer": round(float(np.mean(a > 1e-3)), 4) if len(outer_v) else 0.0,
                 "fold_vertex_halvings": halvings})
    return {"q": q, "w": w, "shift_px": shift, "outer": outer, "lens_adj": lens_adj, "info": info}


def harmonic_fill(T: np.ndarray, n: int, fixed: np.ndarray, values: np.ndarray) -> np.ndarray:
    """Uniform-graph-Laplacian (harmonic) interpolation on a triangle mesh with Dirichlet ``values`` at the
    ``fixed`` vertex indices."""
    from scipy import sparse
    from scipy.sparse.linalg import spsolve
    E = np.concatenate([T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]])
    E = np.unique(np.sort(E, axis=1), axis=0)
    A = sparse.coo_matrix((np.ones(2 * len(E)), (np.r_[E[:, 0], E[:, 1]], np.r_[E[:, 1], E[:, 0]])), shape=(n, n)).tocsr()
    A.data[:] = 1.0
    L = sparse.diags(np.asarray(A.sum(axis=1)).ravel()) - A
    h = np.zeros(n)
    h[fixed] = values
    free = np.ones(n, bool)
    free[fixed] = False
    if free.any():
        Lff = L[free][:, free].tocsc()
        rhs = -(L[free][:, ~free] @ h[~free])
        h[free] = spsolve(Lff, rhs)
    return h


def contain_lens(LF3: np.ndarray, LP2: np.ndarray, LT: np.ndarray, t_lens: float, frame_geom, src: depth.SourceView,
                 frame: NormFrame, surf: depth.ParamSurface, df: depth.DepthField, rim_t=None,
                 margin: float = CONTAIN_MARGIN_MM, rim_depth=None) -> tuple[np.ndarray, dict]:
    """Minimal correction keeping the lens tuck band inside the rim: on boundary vertices that lie over the
    frame, the lens mid-depth is clamped into [frame back + t/2 + margin, frame front - t/2 - margin] (the rim
    middle when the rim is thinner than that); the correction is exact there and fades to 0 over 3 mm along the
    ring beyond them (free/rimless edges), interpolated harmonically inside, and applied along each vertex's camera
    ray (so the projection is unchanged). ``rim_depth`` (``build``): the front/back param depths of the rim AS BUILT
    (after the silhouette rule and the carve), so the lens sits in the rim that is actually delivered; the uncarved
    S4 prism is the fallback where no plate vertex is near."""
    B = boundary_edges(LT)
    ring = []
    nxt = {int(a): int(b) for a, b in B}
    if not nxt:
        return LF3, {"applied": False}
    start = min(nxt)
    k = start
    for _ in range(len(nxt)):
        ring.append(k)
        k = nxt.get(k)
        if k is None or k == start:
            break
    ring = np.asarray(ring)
    gp = LP2[ring]
    over = shapely.contains_xy(frame_geom.buffer(1.0), gp[:, 0], gp[:, 1])
    r = np.zeros(len(ring))
    base = df.base
    if over.any():
        Pff = depth.lift_px(gp[over], src.camera, frame, surf, mirror_width=src.mirror_width)
        ok = np.all(np.isfinite(Pff), axis=1)
        idx = np.nonzero(over)[0][ok]
        tt = rim_t(Pff[ok], gp[over][ok]) if rim_t is not None else df.thickness_at(Pff[ok])
        Pfb = back_points(Pff[ok], gp[over][ok], src, frame, df.base, tt, 1.0)
        dff = base.to_param(Pff[ok])[2]
        dfb = base.to_param(Pfb)[2]
        if rim_depth is not None:
            rf, rb, valid = rim_depth(gp[over][ok])
            dff, dfb = np.where(valid, rf, dff), np.where(valid, rb, dfb)
        dmid = base.to_param(LF3[ring[idx]])[2] - t_lens / 2.0
        hi = dff - t_lens / 2.0 - margin
        lo = dfb + t_lens / 2.0 + margin
        target = np.where(lo <= hi, np.clip(dmid, lo, hi), 0.5 * (dff + dfb))
        r[idx] = target - dmid
    step = np.hypot(*np.diff(np.vstack([gp, gp[:1]]), axis=0).T)
    mm_px = float(np.linalg.norm(np.diff(LF3[ring], axis=0), axis=1).sum() / max(step[:-1].sum(), 1e-9))
    # exact on over-frame vertices; beyond them the nearest over-frame value fades out over 3 mm along the ring
    if over.any() and (~over).any():
        n = len(ring)
        pos = np.nonzero(over)[0]
        dd = np.abs(np.arange(n)[:, None] - pos[None, :])
        dd = np.minimum(dd, n - dd)
        j = np.argmin(dd, axis=1)
        dist_mm = dd[np.arange(n), j] * float(np.median(step)) * mm_px
        r = np.where(over, r, r[pos[j]] * np.clip(1.0 - dist_mm / 3.0, 0.0, 1.0))
    if not np.any(np.abs(r) > 1e-6):
        return LF3, {"applied": False, "boundary_vertices": int(len(ring)), "over_frame": int(over.sum())}
    h = harmonic_fill(LT, len(LF3), ring, r)
    O, D = depth.camera_rays_mm(src.camera, frame, LP2, src.mirror_width)
    s_, _, _ = base.to_param(LF3)
    dd = np.sum(D * base.outward(s_), axis=1)
    move = np.where(np.abs(dd) > 1e-3, h / np.where(np.abs(dd) > 1e-3, dd, 1.0), 0.0)
    out = LF3 + move[:, None] * D
    return out, {"applied": True, "boundary_vertices": int(len(ring)), "over_frame": int(over.sum()),
                 "corrected_boundary_vertices": int(np.sum(np.abs(r) > 1e-3)),
                 "max_abs_mm": round(float(np.max(np.abs(h))), 3),
                 "boundary_max_abs_mm": round(float(np.max(np.abs(r))), 3)}


# =========================================================================== temples seen through the front
def behind_front_mask(frame_mask: np.ndarray, gen, df: depth.DepthField, src: depth.SourceView, frame: NormFrame,
                      ppm: float, stride: int = 2, hardware: np.ndarray | None = None,
                      hulls: list | None = None) -> tuple[np.ndarray, dict]:
    """Frame-mask pixels whose source-camera ray carries NO generator geometry between 3 mm in front of the S4
    front surface and 1 mm behind the plate back (S4 thickness) while the generator has geometry elsewhere on the
    ray: temples seen above/behind the front (a camera 10 deg above sees the first centimetres of each temple
    above its endpiece). They are not plate; the S5 donor/arm geometry behind the plate covers them.

    Also removed: pixels whose S4 front point the source camera sees at grazing incidence (``donor.facing_cos`` <
    ``donor.GRAZE_COS``, > 70 deg: a wrapped temple root seen edge-on), and (``hulls``) pixels where two
    independent sources agree the front is empty - the point on the S4
    front surface projects more than ``HULL_EMPTY_MM`` outside another fit view's (dilated) silhouette AND the
    generator's first hit along the ray is deeper than half the plate thickness (e.g. the top of a temple root seen
    above a thick endpiece, which the plate band above still counts as plate)."""
    ys, xs = np.nonzero(frame_mask[::stride, ::stride])
    px = np.stack([xs * float(stride), ys * float(stride)], 1)
    surf = df.front_surface()
    P = depth.lift_px(px, src.camera, frame, surf, mirror_width=src.mirror_width)
    ok = np.all(np.isfinite(P), axis=1)
    O, D = depth.camera_rays_mm(src.camera, frame, px, src.mirror_width)
    n = np.zeros_like(P)
    n[ok] = surf.normal(P[ok])
    Dfb = np.where(np.sum(D * n, axis=1, keepdims=True) > 0, -D, D)       # front -> back along the ray
    scene = o3d.t.geometry.RaycastingScene()
    scene.add_triangles(o3d.core.Tensor(np.ascontiguousarray(gen.V, np.float32)),
                        o3d.core.Tensor(np.ascontiguousarray(gen.F, np.uint32)))
    start = np.where(ok[:, None], P - FRONT_BAND_FRONT_MM * Dfb, 0.0)
    near = scene.cast_rays(o3d.core.Tensor(np.hstack([start, Dfb]).astype(np.float32)))["t_hit"].numpy()
    t_plate = np.zeros(len(P))
    t_plate[ok] = df.thickness_at(P[ok])
    if hardware is not None:              # hardware is 3D generator geometry: the old 8 mm band
        t_plate = np.where(np.asarray(hardware, bool)[ys * stride, xs * stride],
                           np.maximum(t_plate, HARDWARE_BAND_MM - FRONT_BAND_BEHIND_MM), t_plate)
    in_band = ok & (near <= FRONT_BAND_FRONT_MM + t_plate + FRONT_BAND_BEHIND_MM)
    hull_empty = np.zeros(len(P), bool)
    if hulls:
        deep = near > FRONT_BAND_FRONT_MM + 0.5 * t_plate
        hull_empty[ok] = deep[ok] & (hull_outside_mm(hulls, P[ok]) > HULL_EMPTY_MM)
        if hardware is not None:
            hull_empty &= ~np.asarray(hardware, bool)[ys * stride, xs * stride]
    in_band &= ~hull_empty
    # seen edge-on by the source camera: not plate (the S5 donor takes the generator's geometry there)
    graze = np.zeros(len(P), bool)
    graze[ok] = donor.facing_cos(P[ok], df, src, frame) < donor.GRAZE_COS
    in_band &= ~graze
    anyhit = scene.cast_rays(o3d.core.Tensor(np.hstack([O, D]).astype(np.float32)))["t_hit"].numpy()
    remove = ok & ~in_band & (np.isfinite(anyhit) | graze)
    small = np.zeros(frame_mask[::stride, ::stride].shape, bool)
    small[ys[remove], xs[remove]] = True
    front_px = np.zeros_like(small)
    front_px[ys[in_band], xs[in_band]] = True
    spare = ndimage.distance_transform_edt(~front_px) * stride <= SPARE_NEAR_FRONT_MM * ppm
    small &= ~spare
    big = np.kron(small, np.ones((stride, stride), bool))[:frame_mask.shape[0], :frame_mask.shape[1]] & frame_mask
    big = ndimage.binary_opening(big, iterations=1)
    lab, nl = ndimage.label(big)
    out = np.zeros_like(big)
    min_px = TEMPLE_MIN_AREA_MM2 * ppm ** 2
    if nl:
        sizes = ndimage.sum(big, lab, np.arange(1, nl + 1))
        keep_ids = np.nonzero(sizes >= min_px)[0] + 1
        out = np.isin(lab, keep_ids)
    return out, {"pixels": int(out.sum()), "components": int(len(np.unique(lab[out])) if out.any() else 0),
                 "rays_without_front_geometry": int((ok & ~in_band).sum()), "rays_missing_surface": int((~ok).sum()),
                 "rays_hull_empty": int(hull_empty.sum()), "rays_grazing": int(graze.sum())}


def temple_crossings(frame_clean: np.ndarray, hardware: np.ndarray | None, polys: list[np.ndarray], a5: dict | None,
                     df: depth.DepthField, src: depth.SourceView, frame: NormFrame, ppm: float) -> tuple[np.ndarray, dict]:
    """Frame pieces that are a TEMPLE seen across a lens, not front-piece material: an 8-connected component of the
    (temple-cleaned) frame mask, not the largest one (on a rimless front ``donor.hardware_mask`` calls such a piece
    hardware too; real hardware touches its S5 donor, below), that
    - touches a lens (within 2 px of an S2 lens polygon) and, lifted to the plate S6 would build (S4 front surface to
      plate back), lies farther than ``CROSSING_TOUCH_MM`` from every S5 surface (arms and donors): it touches ONLY a
      lens, so as a plate piece it would hang on the lens edge (miu's gold flecks where the tortoise tips cross the lens
      top in the front photo; INVU's dark bits where its temples pass the lens's outer corners in the back photo);
    - and is explained: by the S5 temple projection (at least ``CROSSING_SHARE`` of its pixels within
      ``CROSSING_ARM_MM`` of the S5 ARMS rendered through the source camera: the photo shows the temple there), or as
      the lens's own edge band (a fragment below ``donor.HW_FRAGMENT_MM2`` hugging the lens outline like a rim band,
      ``donor.RIM_HUG_SHARE`` of it within ``donor.RIM_HUG_MM``: INVU's dark lens edge at its outer corners, which the
      back view sees beside the temples - a rim fragment with nothing to hold it).
    ``behind_front_mask`` misses them: at the lens edge the generator's lens plate is inside the front band. Returns
    (mask of the pieces to remove, info)."""
    out = np.zeros_like(frame_clean)
    info = {"components": 0, "dropped": []}
    if a5 is None or not polys or not frame_clean.any():
        return out, info
    lab, n = ndimage.label(frame_clean, np.ones((3, 3), int))
    info["components"] = int(n)
    if n < 2:
        return out, info
    sizes = ndimage.sum(frame_clean, lab, np.arange(1, n + 1))
    main = int(np.argmax(sizes)) + 1
    arms, s5 = [], []
    for sd in ("R", "L"):
        if f"temple_{sd}_V" not in a5:
            continue
        V = np.asarray(a5[f"temple_{sd}_V"], float)
        F = np.asarray(a5[f"temple_{sd}_F"], np.int64)
        d0 = int(a5[f"donor_{sd}_faces"][0]) if f"donor_{sd}_faces" in a5 else len(F)
        if d0 > 0:
            arms.append((V, F[:d0]))
        s5.append((V, F))
    if not arms:
        return out, info
    arm = render_parts(arms, src.camera, frame, src.shape, src.mirror_width)["mask"]
    if not arm.any():
        return out, info
    near_arm = ndimage.distance_transform_edt(~arm) / ppm <= CROSSING_ARM_MM
    near_lens = ndimage.binary_dilation(depth.poly_mask(polys, frame_clean.shape), iterations=2)
    ring = np.zeros(frame_clean.shape, np.uint8)
    for poly in polys:
        cv2.polylines(ring, [np.round(np.asarray(poly) * 16).astype(np.int32)], True, 1, shift=4)
    hug_px = ndimage.distance_transform_edt(~ring.astype(bool)) <= donor.RIM_HUG_MM * ppm
    scene = o3d.t.geometry.RaycastingScene()
    scene.add_triangles(o3d.core.Tensor(np.vstack([v for v, _ in s5]).astype(np.float32)),
                        o3d.core.Tensor(np.vstack([f + o for (_, f), o in zip(s5, np.cumsum([0] + [len(v) for v, _ in s5[:-1]]))])
                                        .astype(np.uint32)))
    surf = df.front_surface()
    for c in range(1, n + 1):
        if c == main:
            continue
        m = lab == c
        if not (m & near_lens).any():
            continue
        share = float(near_arm[m].mean())
        hug = float(hug_px[m].mean())
        fragment = hug >= donor.RIM_HUG_SHARE and float(m.sum()) / ppm ** 2 < donor.HW_FRAGMENT_MM2
        if share < CROSSING_SHARE and not fragment:
            continue
        ys, xs = np.nonzero(m)
        step = max(1, len(xs) // 400)
        px = np.stack([xs[::step], ys[::step]], 1).astype(float)
        P = depth.lift_px(px, src.camera, frame, surf, mirror_width=src.mirror_width)
        P = P[np.all(np.isfinite(P), axis=1)]
        if not len(P):
            continue
        Pb = back_points(P, None, src, frame, df.base, df.thickness_at(P), 0.0)
        Q = np.vstack([P, 0.5 * (P + Pb), Pb])
        dmin = float(scene.compute_distance(o3d.core.Tensor(Q.astype(np.float32))).numpy().min())
        if dmin <= CROSSING_TOUCH_MM:
            continue
        out |= m
        info["dropped"].append({"pixels": int(m.sum()), "area_mm2": round(float(m.sum()) / ppm ** 2, 2),
                                "arm_share": round(share, 3), "lens_hug": round(hug, 3), "s5_distance_mm": round(dmin, 2),
                                "bbox_px": [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())]})
    info["dropped_area_mm2"] = round(float(out.sum()) / ppm ** 2, 2)
    return out, info


def donor_anchoring(frame_V: np.ndarray, frame_F: np.ndarray, lens_parts: list[tuple[np.ndarray, np.ndarray]],
                    a5: dict | None, weld_mm: float = 1e-3) -> tuple[dict[str, np.ndarray], dict]:
    """The S5 donors' anchoring re-tested on the DELIVERED geometry with the S10 integrity check's own rule and measure
    (``gate.floating_parts``): the frame as built here, the lenses, both S5 arms and every donor component, welded at
    ``weld_mm``; a donor component (>= 1 mm2) is anchored when some vertex lies within ``donor.ANCHOR_GAP_MM`` (=
    ``gate.FLOAT_GAP_MM``) of another component's SURFACE. S5 tested its donors before decimation, point to vertex,
    against a plate it could only predict; the decimation, the caps and this stage's plate (silhouette rule, carve)
    move surfaces by tenths of a millimetre (INVU's 65 mm2 brow piece ended 0.509 mm from everything). A component
    within ``donor.ANCHOR_GAP_MM + donor.SNAP_MAX_MM`` is MOVED into contact (along the direction to its nearest
    surface point until its closest vertex lies ``donor.OVERLAP_MM`` inside it, like every donor cut; verified), a
    farther one is dropped. Returns ({donor_offset_<s> (per S5 temple vertex, mm), donor_keep_<s> (per S5 temple
    face)}, info); ``export.temple_arrays`` applies them, so S7/S8/S9 deliver the corrected donors."""
    arrays: dict[str, np.ndarray] = {}
    info = {"snapped": [], "dropped": [], "components_checked": 0,
            "rule": f"gate.floating_parts: >= 1 mm2, some vertex within {donor.ANCHOR_GAP_MM} mm of another component's "
                    f"surface; else moved into contact when within {donor.ANCHOR_GAP_MM + donor.SNAP_MAX_MM} mm, else dropped"}
    if a5 is None:
        return arrays, info
    sides = [sd for sd in ("R", "L") if f"temple_{sd}_V" in a5 and f"donor_{sd}_faces" in a5]
    if not sides:
        return arrays, info
    parts = [(np.asarray(frame_V, float), np.asarray(frame_F, np.int64), None)]
    parts += [(np.asarray(v, float), np.asarray(f, np.int64), None) for v, f in lens_parts]
    for sd in sides:
        parts.append((np.asarray(a5[f"temple_{sd}_V"], float), np.asarray(a5[f"temple_{sd}_F"], np.int64), sd))
    voffs = np.cumsum([0] + [len(p[0]) for p in parts[:-1]])
    foffs = np.cumsum([0] + [len(p[1]) for p in parts[:-1]])
    V = np.vstack([p[0] for p in parts])
    F = np.vstack([p[1] + o for p, o in zip(parts, voffs)])
    part_of_face = np.concatenate([np.full(len(p[1]), k) for k, p in enumerate(parts)])
    donor_face = np.zeros(len(F), bool)
    for k, (_, _, sd) in enumerate(parts):
        if sd is not None:
            d0, d1 = (int(x) for x in a5[f"donor_{sd}_faces"])
            donor_face[int(foffs[k]) + d0:int(foffs[k]) + d1] = True
    key = np.round(V / weld_mm).astype(np.int64)
    _, inv = np.unique(key, axis=0, return_inverse=True)
    inv = inv.ravel()
    Fw = inv[F]
    n = int(inv.max()) + 1
    Vw = np.zeros((n, 3))
    Vw[inv] = V
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    E = np.concatenate([Fw[:, [0, 1]], Fw[:, [1, 2]]])
    lab_v = connected_components(coo_matrix((np.ones(len(E)), (E[:, 0], E[:, 1])), shape=(n, n)), directed=False)[1]
    lab_f = lab_v[Fw[:, 0]]
    A = 0.5 * np.linalg.norm(np.cross(Vw[Fw[:, 1]] - Vw[Fw[:, 0]], Vw[Fw[:, 2]] - Vw[Fw[:, 0]]), axis=1)
    shift = np.zeros((n, 3))
    drop_c = set()
    for c in np.unique(lab_f[donor_face]):
        own = lab_f == c
        if not donor_face[own].all():                     # welded to an arm or the plate: part of it
            continue
        area = float(A[own].sum())
        if area < 1.0:
            continue
        info["components_checked"] += 1
        scene = o3d.t.geometry.RaycastingScene()
        scene.add_triangles(o3d.core.Tensor(Vw.astype(np.float32)), o3d.core.Tensor(Fw[~own].astype(np.uint32)))
        vs = np.unique(Fw[own])
        q = o3d.core.Tensor(Vw[vs].astype(np.float32))
        d = scene.compute_distance(q).numpy().astype(float)
        k = int(np.argmin(d))
        g = float(d[k])
        if g <= donor.ANCHOR_GAP_MM:
            continue
        side = parts[int(part_of_face[np.nonzero(own)[0][0]])][2]
        rec = {"side": side, "area_mm2": round(area, 2), "gap_mm": round(g, 3),
               "centroid_mm": np.round(Vw[vs].mean(0), 2).tolist()}
        if g <= donor.ANCHOR_GAP_MM + donor.SNAP_MAX_MM:
            target = scene.compute_closest_points(q)["points"].numpy().astype(float)[k]
            dv = target - Vw[vs[k]]
            t = dv / max(np.linalg.norm(dv), 1e-9) * (g + donor.OVERLAP_MM)
            g2 = float(scene.compute_distance(o3d.core.Tensor((Vw[vs] + t).astype(np.float32))).numpy().min())
            if g2 <= donor.ANCHOR_GAP_MM:
                shift[vs] = t
                rec.update({"moved_mm": round(float(np.linalg.norm(t)), 3), "gap_after_mm": round(g2, 3)})
                info["snapped"].append(rec)
                continue
            rec["gap_after_move_mm"] = round(g2, 3)
        drop_c.add(int(c))
        info["dropped"].append(rec)
    for k, (pV, pF, sd) in enumerate(parts):
        if sd is None:
            continue
        o, fo = int(voffs[k]), int(foffs[k])
        arrays[f"donor_offset_{sd}"] = shift[inv[o:o + len(pV)]].astype(np.float32)
        arrays[f"donor_keep_{sd}"] = ~np.isin(lab_f[fo:fo + len(pF)], np.asarray(sorted(drop_c), np.int64))
    return arrays, info


# =========================================================================== rendering
def render_parts(parts: list[tuple[np.ndarray, np.ndarray]], cam: Camera, frame: NormFrame, shape, src_mirror: int | None,
                 roi=None, **kw) -> dict:
    """Render several meshes together; ``part`` image = index of the part hit (-1 miss). With ``src_mirror``
    the camera is the back camera and the output is mirrored into the source pixel frame."""
    V = np.vstack([p[0] for p in parts])
    offs = np.cumsum([0] + [len(p[0]) for p in parts])
    F = np.vstack([p[1] + offs[i] for i, p in enumerate(parts)])
    fpart = np.concatenate([np.full(len(p[1]), i) for i, p in enumerate(parts)])
    r_roi = roi
    if src_mirror is not None and roi is not None:
        x0, y0, x1, y1 = roi
        r_roi = (src_mirror - x1, y0, src_mirror - x0, y1)
    r = raster.render(V, F, cam, frame, shape, 1, r_roi, **kw)
    part = np.where(r["face_id"] >= 0, fpart[np.maximum(r["face_id"], 0)], -1)
    r["part"] = part
    if src_mirror is not None:
        for k in ("mask", "depth", "face_id", "part", "normal", "points", "bary"):
            if k in r:
                r[k] = r[k][:, ::-1]
    r["faces"] = F
    r["fpart"] = fpart
    return r


# =========================================================================== stage
def build(frame_clean: np.ndarray, polys: list[np.ndarray], types_in: list[np.ndarray], rimw_in: list[np.ndarray],
          sides: list[str], source_to_front, df: depth.DepthField, src: depth.SourceView, frame: NormFrame,
          ppm: float, hulls: list | None = None, hardware: np.ndarray | None = None, gen_scene=None,
          material: np.ndarray | None = None, matte: np.ndarray | None = None, vent: np.ndarray | None = None,
          donors: list[tuple[np.ndarray, np.ndarray]] | None = None) -> dict:
    """The geometry of S6 from the (temple-cleaned) frame mask and the S2 lens polygons in source px: frame solid,
    lens solids, UVs and the 2D design (see the module docstring). ``vent``: the S2 openings (``carve_class`` 1): the
    frame walls around them run along the source ray like a lens hole's, so a vent stays see-through in the source
    view (a prism wall along the depth axis put the brow's back edge across oakley's outer brow vents). Pure: reads
    nothing from disk."""
    flags: list[str] = []
    H, W = src.shape
    surf = df.front_surface()
    n_lens = len(polys)
    lens_raster = depth.poly_mask(polys, (H, W))
    region = mask_to_geometry(frame_clean | lens_raster)
    lens_rings, lens_types, lens_rimw = [], [], []
    spacing_px = LENS_RING_MM * ppm
    for i, p in enumerate(polys, start=1):
        ring, near = ring_resample(p, spacing_px)
        lens_rings.append(ring)
        lens_types.append(np.asarray(types_in[i - 1])[near])
        lens_rimw.append(np.asarray(rimw_in[i - 1], float)[near])
    exact_polys = [shapely.make_valid(Polygon(r)) for r in lens_rings]
    holes = shapely.union_all(exact_polys)
    frame_geom = shapely.difference(region, holes)
    bands = []
    for ring, ty in zip(lens_rings, lens_types):
        nf = ty != TYPE_FRAME
        for k in np.nonzero(nf)[0]:
            bands.append(LineString([ring[k], ring[(k + 1) % len(ring)]]))
    if bands:
        frame_geom = shapely.difference(frame_geom, shapely.union_all(bands).buffer(FREE_BAND_PX, cap_style="flat"))
    frame_geom = shapely.segmentize(shapely.make_valid(frame_geom), OUTER_MAX_SEG_MM * ppm)
    min_area = MIN_PART_AREA_MM2 * ppm ** 2
    area_before = float(sum(p.area for p in shapely.get_parts(_polygonal(frame_geom)) if isinstance(p, Polygon)))
    parts2d = clean_polygons(frame_geom, 0.05, min_area)
    dropped_area = area_before - float(sum(p.area for p in parts2d))
    frame_geom = MultiPolygon(parts2d) if len(parts2d) != 1 else parts2d[0]

    # ---- 1b. per-lens scale, thickness and grown outline; the rim-hold rule: frame within the tuck band of a
    # frame-bounded lens edge is at least as deep as the lens + 2 x 0.2 mm (+0.1), so the lens fits inside it
    clip_geom = shapely.union(frame_geom.buffer(0.01), holes.buffer(0.01))
    if vent is not None and np.asarray(vent, bool).any():         # a free edge reaches TUCK_FREE_MM into a vent
        clip_geom = shapely.union(clip_geom, shapely.intersection(holes.buffer((TUCK_FREE_MM + 0.05) * ppm),
                                                                  mask_to_geometry(np.asarray(vent, bool))))
    if material is not None:                       # the free/rimless tuck may reach and go under nearby material
        # the clip ALLOWANCE uses the whole matte (halo included): the halo only must not TRIGGER a tuck
        # (``grow_lens``); clipping the grown outline to the halo-free material cut the lens short of the rim at the
        # corners of a vent (oakley) and opened seam hairlines there
        allow = np.asarray(material, bool) if matte is None else (np.asarray(material, bool) | np.asarray(matte, bool))
        near_mat = ndimage.binary_dilation(allow,
                                           iterations=max(1, int(math.ceil(TUCK_REACH_MM * ppm + TUCK_PROBE_PX))))
        clip_geom = shapely.union(clip_geom, shapely.intersection(
            holes.buffer((TUCK_REACH_MM + TUCK_FREE_MM + 0.1) * ppm), mask_to_geometry(near_mat)))
    pre = []
    for i in range(1, n_lens + 1):
        ring, ty, rw = lens_rings[i - 1], lens_types[i - 1], lens_rimw[i - 1]
        Pr = depth.lift_px(ring, src.camera, frame, surf, mirror_width=src.mirror_width)
        okr = np.all(np.isfinite(Pr), axis=1)
        per3 = np.linalg.norm(np.diff(np.vstack([Pr[okr], Pr[okr][:1]]), axis=0), axis=1).sum()
        per2 = np.linalg.norm(np.diff(np.vstack([ring[okr], ring[okr][:1]]), axis=0), axis=1).sum()
        mm_per_px = float(per3 / per2) if per2 > 0 else 1.0 / ppm
        fb = (ty == TYPE_FRAME) & okr
        rimmed = bool(fb.sum() >= max(10, int(0.05 * len(ring))))
        t_raw = df.thickness_at(Pr[fb]) if fb.any() else np.zeros(0)
        t_lens = float(np.clip(0.5 * np.median(t_raw), *LENS_T_RANGE_MM)) if rimmed else LENS_T_NO_RIM_MM
        grown, g_mm = grow_lens(ring, ty, rw, mm_per_px, spacing_px, clip_geom, material, matte, vent)
        pre.append({"Pr": Pr, "okr": okr, "mm_per_px": mm_per_px, "fb": fb, "rimmed": rimmed, "t_lens": t_lens,
                    "grown": grown, "g_mm": g_mm})
    fb_lines = [LineString([r[k], r[(k + 1) % len(r)]]) for r, ty in zip(lens_rings, lens_types)
                for k in np.nonzero(ty == TYPE_FRAME)[0]]
    fb_geom = shapely.union_all(fb_lines) if fb_lines else None
    t_hold = max([q["t_lens"] for q in pre], default=0.0) + 2 * CONTAIN_MARGIN_MM + 0.1
    hold_px = max([float(q["g_mm"].max()) / q["mm_per_px"] for q in pre], default=0.0) + HOLD_EXTRA_MM * ppm

    def rim_t(P: np.ndarray, px: np.ndarray) -> np.ndarray:
        t = df.thickness_at(P)
        if fb_geom is None or not len(px):
            return t
        dist = shapely.distance(shapely.points(np.asarray(px, float)), fb_geom)
        return np.where(dist <= hold_px, np.maximum(t, t_hold), t)

    # ---- 2. frame mesh (a hardware part is a CORE: its outline eroded CORE_ERODE_MM so it hides inside the
    # generator hardware that S5 delivers there)
    P2_all, T_all, hw_flags = [], [], []
    off = 0
    hw_parts = 0
    hwm = np.asarray(hardware, bool) if hardware is not None else None
    for p in parts2d:
        is_hw = hwm is not None and part_share(p, hwm) > 0.5
        if is_hw:
            # eroded away from the lenses only: where the hardware meets a lens the core keeps the exact outline, so
            # the lens tuck and the core overlap in every view (no sliver between lens and hardware)
            er = CORE_ERODE_MM * ppm
            keep_at_lens = shapely.intersection(p, holes.buffer(1.5 * er))
            q = clean_polygons(shapely.union(p.buffer(-er, join_style=2), keep_at_lens), 0.05, min_area)
            if not q:
                q = clean_polygons(p.buffer(-0.5 * er, join_style=2), 0.05, min_area) or [p]
            q = [shapely.segmentize(r, OUTER_MAX_SEG_MM * ppm) for r in q]
            hw_parts += 1
        else:
            q = [p]
        for r in q:
            P2, T = triangulate_cdt(r)
            if not len(T):
                continue
            T = orient_triangles(P2, T, -1.0)
            P2, T = refine_long_edges(P2, T, INTERIOR_MAX_EDGE_MM * ppm)
            P2_all.append(P2)
            T_all.append(T + off)
            hw_flags.append(np.full(len(P2), is_hw))
            off += len(P2)
    fP2 = np.vstack(P2_all)
    fT = np.vstack(T_all)
    hw_v = np.concatenate(hw_flags)
    vent_v = np.zeros(len(fP2), bool)
    if vent is not None and np.asarray(vent, bool).any():
        # boundary vertices on a vent's rim (as close as a lens-hole vertex to its ring): exact in the source view, wall
        # along the source ray (``silhouette_rule`` fixed vertices)
        d_vent = ndimage.distance_transform_edt(~np.asarray(vent, bool))
        d_v = ndimage.map_coordinates(d_vent, [fP2[:, 1], fP2[:, 0]], order=1, mode="nearest")
        vent_v = d_v <= max(LENS_ADJ_PX, LENS_ADJ_MM * ppm)
    sil = silhouette_rule(fP2, fT, lens_rings, src, frame, surf, df.base, rim_t, ppm, fixed=hw_v | vent_v)
    sil["info"]["vent_vertices"] = int(vent_v.sum())
    fQ2 = sil["q"]                                   # where each front-cap vertex actually projects (source px)
    fP3, frame_fallback = lift_with_fallback(fQ2, src, frame, surf)
    t_gen = df.thickness_at(fP3)
    t_front = rim_t(fP3, fP2)
    fB3 = back_points(fP3, fQ2, src, frame, df.base, t_front, sil["w"])
    core_info = {"parts": hw_parts, "vertices": int(hw_v.sum())}
    if hw_v.any() and gen_scene is not None:
        fP3[hw_v], fB3[hw_v], ci = hardware_core(fQ2[hw_v], fT, np.nonzero(hw_v)[0], gen_scene, src, frame, df,
                                                 fP3[hw_v])
        ci.pop("_hit", None)                           # per-vertex diagnostic, not a result field
        core_info.update(ci)
    t_min = np.where(t_front > t_gen + 1e-9, t_front, np.minimum(t_front, CARVE_MIN_T_MM))
    # a core is never carved; the rim band that holds a lens keeps the lens groove (mid-rim +/- t_hold / 2) inside
    hold_v = np.zeros(len(fP2), bool)
    if fb_geom is not None:
        hold_v = shapely.distance(shapely.points(fP2), fb_geom) <= hold_px
    fP3, fB3, carve_info = carve_plate(fP3, fB3, fQ2, t_min, hulls or [], src, frame, skip=hw_v, T=fT,
                                       hold=hold_v, t_hold=t_hold)
    # the lens sits at the mid-depth of the rim as carved (before the back meets the donors behind it)
    rim_depth = make_rim_depth(fQ2, fP3, fB3, ~hw_v, df, ppm)
    fB3, meet_info = meet_donors(fP3, fB3, donors, skip=hw_v)
    frame_V, frame_F, frame_region = solid_from_cap(fP3, fB3, fT)
    if frame_fallback:
        flags.append("frame_lift_fallback")
    uv_src = np.full((len(frame_V), 2), -1.0)
    uv_src[:len(fP2)] = fQ2
    M = np.asarray(source_to_front, float)
    uv_front = np.full((len(frame_V), 2), -1.0)
    uv_front[:len(fP2)] = fQ2 @ M[:, :2].T + M[:, 2]

    # ---- 3. lenses
    lens_out = []
    arrays: dict[str, np.ndarray] = {}
    for i in range(1, n_lens + 1):
        ring, ty, rw = lens_rings[i - 1], lens_types[i - 1], lens_rimw[i - 1]
        q = pre[i - 1]
        Pr, fb, mm_per_px, t_lens, grown, g_mm = q["Pr"], q["fb"], q["mm_per_px"], q["t_lens"], q["grown"], q["g_mm"]
        # rim mid-depth targets on the frame-bounded ring: the rim AS BUILT (silhouette rule + carve) where the plate
        # has vertices there, else the S4 prism
        tf = rim_t(Pr[fb], ring[fb]) if fb.any() else np.zeros(0)
        if fb.any():
            Pb = back_points(Pr[fb], ring[fb], src, frame, df.base, tf, 1.0)
            Mid = 0.5 * (Pr[fb] + Pb)
            rf_, rb_, rv_ = rim_depth(ring[fb])
            if rv_.any():
                sM, yM, dM = df.base.to_param(Mid)
                Mid = df.base.from_param(sM, yM, np.where(rv_, 0.5 * (rf_ + rb_), dM))
                tf = np.where(rv_, rf_ - rb_, tf)
        lm = df.lenses[i - 1]
        ls = df.lens_surface(i)
        info = {"side": sides[i - 1], "mm_per_px_local": round(mm_per_px, 5), "ring_vertices": int(len(ring)),
                "frame_bounded_share": round(float(np.mean(ty == TYPE_FRAME)), 4), "lens_model": lm.kind}
        if q["rimmed"]:
            s_m, y_m, d_m = df.base.to_param(Mid)
            # lift the targets' pixels onto the lens model to get the model value on the same rays
            r0 = d_m - lm(s_m, y_m)
            s0_, y0_ = float(np.median(s_m)), float(np.median(y_m))
            A = np.stack([np.ones(len(r0)), s_m - s0_, y_m - y0_], 1)
            w = np.ones(len(r0))
            lam = 50.0 * len(r0)                                   # ridge on the tilt terms (mm^2 units)
            for _ in range(12):
                AtW = A.T * w
                Mx = AtW @ A + np.diag([0.0, lam, lam])
                c = np.linalg.solve(Mx, AtW @ r0)
                w = depth.soft_l1_weights(r0 - A @ c, 0.2)
            c[1:] = np.clip(c[1:], -TILT_MAX, TILT_MAX)
            corr = (float(c[0]), float(c[1]), float(c[2]), s0_, y0_)
            res_rim = r0 - A @ c
            info["placement"] = {"method": "mid-rim on frame-bounded ring", "offset_mm": round(corr[0], 3),
                                 "tilt_s": round(corr[1], 5), "tilt_y": round(corr[2], 5),
                                 "rim_residual": depth.residual_stats(res_rim),
                                 "rim_thickness_median_mm": round(float(np.median(tf)), 3)}
        else:
            corr = (-t_lens / 2.0, 0.0, 0.0, 0.0, 0.0)
            info["placement"] = {"method": "plate front = lens front (no rim)", "offset_mm": round(corr[0], 3)}
        a0, b0, c0, s0_, y0_ = corr
        mid_surf = ls.shifted(lambda s, y, a0=a0, b0=b0, c0=c0, s0_=s0_, y0_=y0_:
                              a0 + b0 * (np.asarray(s, float) - s0_) + c0 * (np.asarray(y, float) - y0_), f"lens{i}_mid")
        front_surf = mid_surf.shifted(t_lens / 2.0, f"lens{i}_front")
        info["thickness_mm"] = round(t_lens, 3)
        nf_ = ty != TYPE_FRAME
        info["grow_mm"] = {"frame_bounded_median": round(float(np.median(g_mm[ty == TYPE_FRAME])), 3)
                           if (ty == TYPE_FRAME).any() else 0.0, "max": round(float(g_mm.max()), 3),
                           "free_rimless_tucked_share": round(float(np.mean(g_mm[nf_] > 0)), 4) if nf_.any() else None}
        exact = exact_polys[i - 1]
        info["area_mm2"] = {"hole": round(exact.area * mm_per_px ** 2, 2), "lens": round(grown.area * mm_per_px ** 2, 2),
                            "tuck_band": round((grown.area - exact.area) * mm_per_px ** 2, 2),
                            "tuck_band_outside_frame": round(shapely.difference(shapely.difference(grown, exact),
                                                                                frame_geom.buffer(0.02)).area
                                                             * mm_per_px ** 2, 4)}
        LP2, LT = triangulate_with_interior(grown, spacing_px, LENS_INTERIOR_MM / mm_per_px)
        LT = orient_triangles(LP2, LT, -1.0)
        LF3, lfb = lift_with_fallback(LP2, src, frame, front_surf)
        if lfb:
            flags.append(f"lens{i}_lift_fallback")
        nL = front_surf.normal(LF3)
        LF3, corr_info = contain_lens(LF3, LP2, LT, t_lens, frame_geom, src, frame, surf, df, rim_t,
                                      rim_depth=rim_depth)
        info["containment_correction"] = corr_info
        LB3 = LF3 - t_lens * nL
        LV, LF, LR = solid_from_cap(LF3, LB3, LT)
        u0, v0, u1, v1 = grown.bounds
        luv2 = np.stack([(LP2[:, 0] - u0) / max(u1 - u0, 1e-9), (LP2[:, 1] - v0) / max(v1 - v0, 1e-9)], 1)
        luv = np.vstack([luv2, luv2])
        arrays[f"lens{i}_V"] = LV
        arrays[f"lens{i}_F"] = LF
        arrays[f"lens{i}_uv"] = luv.astype(np.float32)
        arrays[f"lens{i}_region"] = LR
        arrays[f"lens{i}_hole_px"] = ring
        arrays[f"lens{i}_hole_type"] = ty.astype(np.int8)
        arrays[f"lens{i}_grown_px"] = np.asarray(grown.exterior.coords)[:-1]
        arrays[f"lens{i}_P2"] = LP2
        lens_out.append({"info": info, "front_surf": front_surf, "mid_surf": mid_surf, "t": t_lens, "grown": grown,
                         "V": LV, "F": LF, "P2": LP2, "mm_per_px": mm_per_px, "exact": exact, "ring": ring, "types": ty})

    out = {"lens_raster": lens_raster, "frame_geom": frame_geom, "parts2d": parts2d, "dropped_area": dropped_area, "holes": holes, "frame_V": frame_V, "frame_F": frame_F, "frame_region": frame_region, "uv_front": uv_front, "uv_src": uv_src, "fP2": fP2, "fT": fT, "t_front": t_front, "t_gen": t_gen, "t_hold": t_hold, "hold_px": hold_px, "frame_fallback": frame_fallback, "lens_out": lens_out, "arrays": arrays, "rim_t": rim_t, "rim_depth": rim_depth, "spacing_px": spacing_px,
           "fQ2": fQ2, "silhouette": sil, "carve": carve_info, "core": core_info, "hardware_vertices": hw_v,
           "meet_donors": meet_info}
    out["flags"] = flags
    return out


def make_rim_depth(fQ2: np.ndarray, fP3: np.ndarray, fB3: np.ndarray, live: np.ndarray, df: depth.DepthField,
                   ppm: float, reach_mm: float = 2.0):
    """px -> (front param depth, back param depth, valid) of the plate AS BUILT at source pixels: inverse-distance
    weights over the 4 nearest plate cap vertices (by their source-px position; hardware cores excluded), valid
    within ``reach_mm`` of one."""
    live = np.asarray(live, bool)
    idx = np.nonzero(live)[0]
    if not len(idx):
        return lambda px: (np.zeros(len(px)), np.zeros(len(px)), np.zeros(len(px), bool))
    tree = cKDTree(fQ2[idx])
    dF = df.base.to_param(fP3[idx])[2]
    dB = df.base.to_param(fB3[idx])[2]
    k = min(4, len(idx))

    def rim_depth(px):
        px = np.asarray(px, float).reshape(-1, 2)
        d, j = tree.query(px, k=k)
        d, j = d.reshape(len(px), -1), j.reshape(len(px), -1)
        w = 1.0 / np.maximum(d, 0.05 * ppm) ** 2
        w /= w.sum(axis=1, keepdims=True)
        return (w * dF[j]).sum(1), (w * dB[j]).sum(1), d[:, 0] <= reach_mm * ppm
    return rim_depth


def containment_check(lens_out: list[dict], frame_geom, src: depth.SourceView, frame: NormFrame,
                      surf: depth.ParamSurface, df: depth.DepthField, rim_t, rim_depth=None) -> list[dict]:
    """Is each lens's tuck band inside the rim solid? At grown-ring vertices over the frame (eroded 0.5 px):
    the lens mesh's front must not be ahead of the frame front and its back not behind the frame back
    (compared along the base normal; the rim as built when ``rim_depth`` is given)."""
    # containment of the tuck band inside the rim (at grown-ring vertices that lie over the frame)
    contain = []
    for i, lo in enumerate(lens_out, start=1):
        g = np.asarray(lo["grown"].exterior.coords)[:-1]
        over = shapely.contains_xy(frame_geom.buffer(-0.5), g[:, 0], g[:, 1])
        if not over.any():
            contain.append({"lens": i, "vertices": 0})
            continue
        gp = g[over]
        Pff = depth.lift_px(gp, src.camera, frame, surf, mirror_width=src.mirror_width)
        ok = np.all(np.isfinite(Pff), axis=1)
        gp, Pff = gp[ok], Pff[ok]
        Pfb = back_points(Pff, gp, src, frame, df.base, rim_t(Pff, gp), 1.0)
        # the lens mesh's own front/back vertices at these grown-ring pixels (after any correction)
        kk = cKDTree(lo["P2"]).query(gp)[1]
        nfront = len(lo["P2"])
        Plf = lo["V"][kk]
        Plb = lo["V"][kk + nfront]
        dff = df.base.to_param(Pff)[2]
        dfb = df.base.to_param(Pfb)[2]
        if rim_depth is not None:
            rf, rb, valid = rim_depth(gp)
            dff, dfb = np.where(valid, rf, dff), np.where(valid, rb, dfb)
        dlf = df.base.to_param(Plf)[2]
        dlb = df.base.to_param(Plb)[2]
        front_out = dlf - dff
        back_out = dfb - dlb
        contain.append({"lens": i, "vertices": int(len(gp)),
                        "lens_front_ahead_of_frame_front": int(np.sum(front_out > 0)),
                        "max_front_poke_mm": round(float(np.nanmax(front_out)), 3),
                        "lens_back_behind_frame_back": int(np.sum(back_out > 0)),
                        "max_back_poke_mm": round(float(np.nanmax(back_out)), 3),
                        "median_front_clearance_mm": round(float(np.nanmedian(-front_out)), 3),
                        "median_back_clearance_mm": round(float(np.nanmedian(-back_out)), 3)})
    return contain


def run(product: str, run: str = "m1", force: bool = False, log=print) -> dict:
    sd = stage_dir(run, product, STAGE)
    if sd.done() and not force:
        return sd.load()[0]
    from . import cameras, generator
    t0 = time.time()
    gen = generator.load(product, run)
    frame = gen.frame
    s2, a2 = stage_dir(run, product, "s2_front").load()
    df = depth.load_depth(product, run)
    src = depth.source_view(product, run)
    ppm = cameras.px_per_mm_at(src.camera, frame, cameras.front_piece_centre(gen))
    surf = df.front_surface()
    flags: list[str] = []
    H, W = src.shape
    frame_mask = np.asarray(a2["frame_mask"], bool)
    polys = depth.lens_polys(a2)
    n_lens = len(polys)
    sides = [l["side"] for l in s2["lenses"]]

    # ---- 1. temples seen through the front, then the frame region
    hulls = hull_views(product, run, src, gen, frame)
    hardware, hw_info = donor.hardware_mask(frame_mask, polys, [a2[f"lens{i}_type"] for i in range(1, n_lens + 1)],
                                            ppm=ppm)
    material, material_info = tuck_material(product, run, a2, src, frame, hardware)
    gen_scene = o3d.t.geometry.RaycastingScene()
    gen_scene.add_triangles(o3d.core.Tensor(np.ascontiguousarray(gen.V, np.float32)),
                            o3d.core.Tensor(np.ascontiguousarray(gen.F, np.uint32)))
    temple, temple_info = behind_front_mask(frame_mask, gen, df, src, frame, ppm, hardware=hardware, hulls=hulls)
    frame_clean = frame_mask & ~temple
    if temple.any():
        # swallow the 1-2 px rims the strided test leaves along a removed temple, then drop slivers there
        near = ndimage.distance_transform_edt(~temple) <= 2.5
        frame_clean &= ~(near & ~ndimage.binary_opening(frame_clean, iterations=2))
        temple = frame_mask & ~frame_clean
        temple_info["pixels_after_sliver_cleanup"] = int(temple.sum())
    # temples seen across a lens edge (touching only the lens, explained by the S5 arm projection): not plate
    a5 = dict(np.load(stage_dir(run, product, "s5_temples").root / "arrays.npz", allow_pickle=False)) \
        if (stage_dir(run, product, "s5_temples").root / "arrays.npz").exists() else None
    crossing, crossing_info = temple_crossings(frame_clean, hardware, polys, a5, df, src, frame, ppm)
    if crossing.any():
        frame_clean = frame_clean & ~crossing
        temple = temple | crossing
    temple_info["temple_crossings"] = crossing_info
    b = build(frame_clean, polys, [a2[f"lens{i}_type"] for i in range(1, n_lens + 1)],
              [a2[f"lens{i}_rimw_px"] for i in range(1, n_lens + 1)], sides, s2["source_to_front"], df, src, frame, ppm,
              hulls, hardware, gen_scene, material, np.asarray(a2["fg_sym"], bool),
              vent=np.asarray(a2["carve_class"]) == 1 if "carve_class" in a2 else None,
              donors=None if a5 is None else [(a5[f"temple_{sd}_V"], np.asarray(a5[f"temple_{sd}_F"])[int(a5[f"donor_{sd}_faces"][0]):])
                                              for sd in ("R", "L") if f"donor_{sd}_faces" in a5])
    flags += b["flags"]
    (lens_raster, frame_geom, parts2d, dropped_area, frame_V, frame_F, frame_region, uv_front, uv_src, fP2, fT, t_front,
     t_gen, t_hold, hold_px, frame_fallback, lens_out, arrays, rim_t) = (
        b[k] for k in ("lens_raster", "frame_geom", "parts2d", "dropped_area", "frame_V", "frame_F", "frame_region",
                       "uv_front", "uv_src", "fP2", "fT", "t_front", "t_gen", "t_hold", "hold_px", "frame_fallback",
                       "lens_out", "arrays", "rim_t"))

    # ---- 3b. the donors' anchoring on the delivered geometry (the S10 integrity rule): snap or drop, applied at export
    anchor_arrays, anchor_info = donor_anchoring(frame_V, frame_F, [(lo["V"], lo["F"]) for lo in lens_out], a5)
    arrays.update(anchor_arrays)

    # ---- 4. checks
    topo = {"frame": mesh_topology(frame_F, len(frame_V))}
    topo["frame"]["signed_volume_mm3"] = round(signed_volume(frame_V, frame_F), 2)
    topo["frame"]["components"] = int(len(parts2d))
    for i, lo in enumerate(lens_out, start=1):
        topo[f"lens{i}"] = mesh_topology(lo["F"], len(lo["V"]))
        topo[f"lens{i}"]["signed_volume_mm3"] = round(signed_volume(lo["V"], lo["F"]), 2)
    contain = containment_check(lens_out, frame_geom, src, frame, surf, df, rim_t, b["rim_depth"])
    # seam render through the source camera
    ys_, xs_ = np.nonzero(frame_mask | lens_raster)
    roi = (max(0, int(xs_.min()) - 12), max(0, int(ys_.min()) - 12), min(W, int(xs_.max()) + 13), min(H, int(ys_.max()) + 13))
    mesh_parts = [(frame_V, frame_F)] + [(lo["V"], lo["F"]) for lo in lens_out]
    seam = seam_check(mesh_parts, lens_out, frame_geom, src, frame, roi, 1)
    seam_ss = seam_check(mesh_parts, lens_out, frame_geom, src, frame, roi, SUPERSAMPLE)
    gaps_total = seam["gap_pixels"] + seam_ss["gap_pixels"]
    if gaps_total:
        flags.append("seam_gaps")
    if any(c.get("lens_front_ahead_of_frame_front", 0) or c.get("lens_back_behind_frame_back", 0) for c in contain):
        flags.append("lens_pokes_out_of_rim")
    if not all(t["watertight"] for t in topo.values()):
        flags.append("not_watertight")
    # silhouette vs S2 (after the temple removal)
    rr = seam["render"]
    x0, y0, x1, y1 = roi
    target = (frame_clean | lens_raster)[y0:y1, x0:x1]
    s2_all = (frame_mask | lens_raster)[y0:y1, x0:x1]
    sil = {"iou_vs_s2_minus_temples": round(float((rr & target).sum() / max((rr | target).sum(), 1)), 4),
           "iou_vs_s2": round(float((rr & s2_all).sum() / max((rr | s2_all).sum(), 1)), 4),
           "missing_px": int((target & ~rr).sum()), "extra_px": int((rr & ~target).sum())}
    # ---- 5. bridge underside
    from .export import bridge_underside_mm
    parts_front = {"frame": {"V": frame_V, "F": frame_F}}
    names = {"R": "lens_R", "L": "lens_L", "C": "lens_C"}
    for i, lo in enumerate(lens_out, start=1):
        parts_front[names.get(sides[i - 1], f"lens_{i}")] = {"V": lo["V"], "F": lo["F"]}
    axis_px = float(s2["axis_x_px"])
    v_axis = _axis_bottom_v(frame_geom, [lo["grown"] for lo in lens_out], axis_px)
    bridge2d = None
    axis_x_mm = None
    if v_axis is not None:
        bridge2d = depth.lift_px(np.array([[axis_px, v_axis]]), src.camera, frame, surf,
                                 mirror_width=src.mirror_width)[0]
        axis_x_mm = float(bridge2d[0]) if np.all(np.isfinite(bridge2d)) else None
    try:
        origin, oinfo = bridge_underside_mm(parts_front, axis_x_mm)
    except ValueError as e:
        origin, oinfo = None, {"error": str(e)}
        flags.append("bridge_underside_failed")
    n_tri = int(len(frame_F) + sum(len(lo["F"]) for lo in lens_out))
    result = {
        "stage": STAGE, "product": product, "run": run,
        "bridge_underside_mm": None if origin is None else [round(float(v), 4) for v in origin],
        "source": {"view": src.view, "mirrored": src.mirrored, "shape": list(src.shape), "px_per_mm": round(ppm, 4)},
        "triangles": {"frame": int(len(frame_F)), **{f"lens{i}": int(len(lo["F"])) for i, lo in enumerate(lens_out, 1)},
                      "total_front": n_tri},
        "vertices": {"frame": int(len(frame_V)), **{f"lens{i}": int(len(lo["V"])) for i, lo in enumerate(lens_out, 1)}},
        "watertight": {k: v["watertight"] for k, v in topo.items()},
        "topology": topo,
        "seam": {"gap_pixels_native": seam["gap_pixels"], "gap_pixels_supersampled": seam_ss["gap_pixels"],
                 "supersample": SUPERSAMPLE, "band_pixels_native": seam["band_pixels"],
                 "band_pixels_supersampled": seam_ss["band_pixels"],
                 "uncovered_design_pixels_native": seam["uncovered_design"],
                 "definition": "pixels within 2 px (native) of a frame-bounded lens-hole edge, inside the designed "
                               "frame region or grown lens, not hit by the assembled front in a render through the "
                               "source camera"},
        "containment": contain,
        "silhouette": sil,
        "temples_removed_from_front": {**temple_info, "area_mm2": round(float(temple.sum()) / ppm ** 2, 2)},
        "donor_anchoring": anchor_info,
        "frame": {"parts": int(len(parts2d)), "dropped_small_area_mm2": round(dropped_area / ppm ** 2, 3),
                  "front_cap_vertices": int(len(fP2)), "lift_fallback_vertices": int(frame_fallback),
                  "rim_hold": {"min_thickness_mm": round(t_hold, 3), "within_px": round(hold_px, 2),
                               "thickened_vertices": int(np.sum(t_front > t_gen + 1e-9)),
                               "max_added_mm": round(float(np.max(t_front - t_gen)), 3) if len(t_gen) else 0.0},
                  "thickness_mm": {"median": round(float(np.median(t_front)), 3),
                                   "p05": round(float(np.percentile(t_front, 5)), 3),
                                   "p95": round(float(np.percentile(t_front, 95)), 3)},
                  "extrude_max_deg": EXTRUDE_MAX_DEG,
                  "silhouette_rule": b["silhouette"]["info"], "hull_carving": b["carve"], "meet_donors": b["meet_donors"],
                  "hardware": {**hw_info, "core": b["core"]}, "tuck_material": material_info},
        "lenses": [lo["info"] for lo in lens_out],
        "bridge": {"rule": "bsa.export.bridge_underside_mm on frame + lenses at the lifted S2 axis",
                   "axis_x_mm": axis_x_mm, "axis_bottom_v_px": v_axis,
                   "lifted_2d_point_mm": None if bridge2d is None else np.round(bridge2d, 4).tolist(), "receipt": oinfo},
        "uv": {"frame_uv_px": "FRONT-photo px of front-cap vertices (source px mapped by S2 source_to_front); -1 elsewhere",
               "frame_uv_src_px": "outline-source px of front-cap vertices (mirrored back photo for back_mirrored); -1 elsewhere",
               "lens_uv": "u across, v top->bottom, 0..1 over the grown lens bbox in source px (front and back caps)"},
        "conventions": {"frame_region": "0 front cap, 1 back cap, 2 wall", "lens_region": "0 front, 1 back, 2 edge",
                        "units": "model mm (S1 MODEL frame)"},
        "flags": sorted(set(flags + [f for f in s2.get("flags", []) if f.startswith("outline_from")])),
    }
    arrays.update({"frame_V": frame_V, "frame_F": frame_F, "frame_region": frame_region,
                   "frame_uv_px": uv_front.astype(np.float32), "frame_uv_src_px": uv_src.astype(np.float32),
                   "frame_P2": fP2, "frame_T2": fT.astype(np.int32), "temple_removed": temple})
    result["seconds"] = round(time.time() - t0, 1)
    sd.save(result, arrays)
    try:
        make_sheet(product, run, gen, result, arrays, lens_out, src, frame, roi, seam, frame_geom, temple)
        views_sheet(product, run, gen, arrays, lens_out)
    except Exception as e:
        result["flags"] = sorted(set(result["flags"] + ["sheet_failed"]))
        result["sheet_error"] = repr(e)
        sd.save(result)
    log(f"[s6 {product}] {result['seconds']} s tri {n_tri} watertight {result['watertight']} gaps "
        f"{seam['gap_pixels']}/{seam_ss['gap_pixels']} flags {result['flags']}")
    return result


def _axis_bottom_v(frame_geom, grown, axis_px: float) -> float | None:
    """Lowest (max v) point of the front parts on the vertical line u = axis_px."""
    geom = shapely.union_all([frame_geom] + list(grown))
    x0, y0, x1, y1 = geom.bounds
    line = LineString([(axis_px, y0 - 10), (axis_px, y1 + 10)])
    inter = shapely.intersection(geom, line)
    if inter.is_empty:
        return None
    ys = [c[1] for g in shapely.get_parts(inter) for c in (g.coords if hasattr(g, "coords") else [])]
    return float(max(ys)) if ys else None


def seam_check(mesh_parts, lens_out, frame_geom, src: depth.SourceView, frame: NormFrame, roi, ss: int) -> dict:
    """Gap pixels on frame-bounded seams in a render through the source camera (``ss`` x supersampled)."""
    x0, y0, x1, y1 = roi
    cam = src.camera
    H, W = src.shape
    if ss > 1:
        # native pixel (u, v) -> (ss u + (ss-1)/2, ss v + (ss-1)/2) on the fine grid
        from .cameras import resized_camera
        cam = resized_camera(cam, float(ss))
    shape = (H * ss, W * ss)
    mirror = W * ss if src.mirrored else None
    roi_s = (x0 * ss, y0 * ss, x1 * ss, y1 * ss)
    r = render_parts(mesh_parts, cam, frame, shape, mirror, roi_s)
    hit = r["mask"]
    gh, gw = hit.shape
    # design coverage and the seam band on the same grid (pixel centres in native units)
    us = x0 + (np.arange(gw) - (ss - 1) / 2.0) / ss
    vs = y0 + (np.arange(gh) - (ss - 1) / 2.0) / ss
    UU, VV = np.meshgrid(us, vs)
    design = shapely.union_all([frame_geom] + [lo["grown"] for lo in lens_out])
    inside = shapely.contains_xy(design, UU.ravel(), VV.ravel()).reshape(UU.shape)
    segs = []
    for lo in lens_out:
        ring, ty = lo["ring"], lo["types"]
        for k in np.nonzero(ty == TYPE_FRAME)[0]:
            segs.append(LineString([ring[k], ring[(k + 1) % len(ring)]]))
    if segs:
        band_geom = shapely.union_all(segs).buffer(SEAM_BAND_PX)
        band = shapely.contains_xy(band_geom, UU.ravel(), VV.ravel()).reshape(UU.shape) & inside
    else:
        band = np.zeros_like(inside)
    gap = band & ~hit
    core_design = shapely.contains_xy(design.buffer(-1.0), UU.ravel(), VV.ravel()).reshape(UU.shape)
    return {"gap_pixels": int(gap.sum()), "band_pixels": int(band.sum()),
            "uncovered_design": int((core_design & ~hit).sum()), "render": hit, "gap": gap, "part": r["part"]}


# =========================================================================== sheet
def _shade(parts, cam, frame, shape, colours):
    r = render_parts(parts, cam, frame, shape, None, None, want_normal=True)
    img = np.full(shape + (3,), 255, np.uint8)
    m = r["mask"]
    if m.any():
        toward = raster.camera_basis(cam)[2]
        lam = np.abs(r["normal"][m] @ toward)
        col = np.asarray(colours, float)[r["part"][m]]
        img[m] = np.clip(col * (0.35 + 0.65 * lam)[:, None], 0, 255).astype(np.uint8)
    return img


def make_sheet(product, run, gen, result, arrays, lens_out, src, frame, roi, seam, frame_geom, temple) -> str:
    from PIL import Image, ImageDraw
    from .generator import _font
    parts = [(arrays["frame_V"], arrays["frame_F"])] + [(lo["V"], lo["F"]) for lo in lens_out]
    colours = [(150, 120, 95)] + [(120, 170, 230)] * len(lens_out)
    allV = np.vstack([p[0] for p in parts])
    ctr = 0.5 * (allV.min(0) + allV.max(0))
    ext = float(np.max(allV.max(0) - allV.min(0)))
    tiles = []
    font = _font(18)
    for name, yaw, pitch in (("front (yaw 0)", 0.0, 0.0), ("yaw 30", 30.0, 0.0), ("yaw -30, pitch 15", -30.0, 15.0),
                             ("top (pitch 90)", 0.0, 89.9)):
        shape = (420, 620)
        cam = raster.view_camera(frame, yaw, pitch, 560.0 / ext, shape, center_mm=ctr)
        img = _shade(parts, cam, frame, shape, colours)
        im = Image.fromarray(img)
        ImageDraw.Draw(im).text((8, 6), name, fill=(0, 0, 0), font=font)
        tiles.append(np.asarray(im))
    row1 = np.concatenate(tiles[:2], 1)
    row2 = np.concatenate(tiles[2:], 1)
    # overlay on the source photo through the source camera
    photo = core.load_photo(core.PRODUCTS[product], src.view)
    if src.mirrored:
        photo = photo[:, ::-1]
    x0, y0, x1, y1 = roi
    crop = photo[y0:y1, x0:x1].astype(float)
    rr, part = seam["render"], seam["part"]
    ov = crop.copy()
    ov[part == 0] = 0.55 * ov[part == 0] + 0.45 * np.array([255, 150, 0])
    ov[part >= 1] = 0.6 * ov[part >= 1] + 0.4 * np.array([0, 120, 255])
    edge = rr & ~ndimage.binary_erosion(rr)
    ov[edge] = (255, 0, 0)
    tm = temple[y0:y1, x0:x1]
    ov[tm] = 0.5 * ov[tm] + 0.5 * np.array([255, 0, 255])
    gap = seam["gap"]
    ov[ndimage.binary_dilation(gap, iterations=3)] = (255, 0, 0)
    ov = Image.fromarray(np.clip(ov, 0, 255).astype(np.uint8))
    d = ImageDraw.Draw(ov)
    for lo in lens_out:
        g = np.asarray(lo["grown"].exterior.coords) - [x0, y0]
        d.line([tuple(p) for p in g], fill=(0, 200, 0), width=1)
        e = np.vstack([lo["ring"], lo["ring"][:1]]) - [x0, y0]
        d.line([tuple(p) for p in e], fill=(255, 255, 0), width=1)
    wtot = row1.shape[1]
    ov = ov.resize((wtot, max(1, int(round(ov.size[1] * wtot / ov.size[0])))), Image.LANCZOS)
    # zoom insets on two seam spots (left/right-most frame-bounded lens vertices)
    insets = []
    for lo in lens_out[:2]:
        ring, ty = lo["ring"], lo["types"]
        idx = np.nonzero(ty == TYPE_FRAME)[0]
        if not len(idx):
            continue
        k = idx[np.argmin(ring[idx, 1])]                       # top-most frame-bounded vertex
        cu, cv = ring[k] - [x0, y0]
        hs = 40
        a0, b0 = int(max(0, cv - hs)), int(max(0, cu - hs))
        sub = np.asarray(Image.fromarray(np.clip(crop, 0, 255).astype(np.uint8)))[a0:a0 + 2 * hs, b0:b0 + 2 * hs].astype(float)
        pr = part[a0:a0 + 2 * hs, b0:b0 + 2 * hs]
        sub[pr == 0] = 0.5 * sub[pr == 0] + 0.5 * np.array([255, 150, 0])
        sub[pr >= 1] = 0.55 * sub[pr >= 1] + 0.45 * np.array([0, 120, 255])
        sub[gap[a0:a0 + 2 * hs, b0:b0 + 2 * hs]] = (255, 0, 0)
        insets.append(np.asarray(Image.fromarray(sub.astype(np.uint8)).resize((310, 310), Image.NEAREST)))
    if src.mirrored:
        # the outline source is the mirrored back photo: also show the assembly through the FRONT camera
        from .cameras import load_cameras
        _, cams, _ = load_cameras(product, run)
        fph = core.load_photo(core.PRODUCTS[product], "front")
        rf = render_parts(parts, cams["front"], frame, fph.shape[:2], None, None)
        o2 = fph.astype(float)
        pf = rf["part"]
        o2[pf == 0] = 0.55 * o2[pf == 0] + 0.45 * np.array([255, 150, 0])
        o2[pf >= 1] = 0.6 * o2[pf >= 1] + 0.4 * np.array([0, 120, 255])
        e2 = rf["mask"] & ~ndimage.binary_erosion(rf["mask"])
        o2[e2] = (255, 0, 0)
        im2 = Image.fromarray(np.clip(o2, 0, 255).astype(np.uint8))
        k2 = 310.0 / im2.size[1]
        insets.append(np.asarray(im2.resize((min(620, int(im2.size[0] * k2)), 310), Image.NEAREST)))
    info = result
    lines = [f"S6 assembly {product}: source {src.view}{' mirrored' if src.mirrored else ''}; triangles {info['triangles']}",
             f"watertight {info['watertight']}; seam gap px native {info['seam']['gap_pixels_native']} / "
             f"{SUPERSAMPLE}x {info['seam']['gap_pixels_supersampled']} (band {info['seam']['band_pixels_native']} px)",
             f"silhouette {info['silhouette']}; temples removed {info['temples_removed_from_front']['area_mm2']} mm2",
             f"containment {[{k: c.get(k) for k in ('lens', 'max_front_poke_mm', 'max_back_poke_mm')} for c in info['containment']]}",
             f"bridge underside {info['bridge_underside_mm']}; flags {info['flags']}",
             "overlay: orange frame render, blue lens render, red render edge / gaps, yellow hole = S2 lens, green grown lens, magenta temple-through removed"]
    txt = Image.new("RGB", (wtot, 26 * len(lines) + 12), (255, 255, 255))
    dt = ImageDraw.Draw(txt)
    for k, ln in enumerate(lines):
        dt.text((8, 6 + 26 * k), ln, fill=(0, 0, 0), font=font)
    blocks = [np.asarray(txt), np.asarray(ov)]
    if insets:
        strip = np.full((310, wtot, 3), 255, np.uint8)
        xk = 0
        for a in insets:
            w_ = min(a.shape[1], wtot - xk)
            if w_ <= 0:
                break
            strip[:, xk:xk + w_] = a[:, :w_]
            xk += w_ + 10
        blocks.append(strip)
    blocks += [row1, row2]
    sheet = np.concatenate(blocks, 0)
    path = stage_dir(run, product, STAGE).root / "sheet.png"
    Image.fromarray(sheet).save(path)
    return str(path)


def views_sheet(product: str, run: str, gen, arrays: dict, lens_out: list[dict], width: int = 2400) -> str:
    """``views.png``: per fit view (front/back/left/right; the held-out angled view is not shown) the photo | the
    assembly through the frozen S3 camera (plate flat-shaded orange-grey, lenses blue, S5 arms + donor geometry in
    the generator texture) | the generator; black = the S0 matte contour, blue = the generator's contour."""
    from PIL import Image, ImageDraw
    from . import cameras, temples as T
    from .generator import _font
    frame, cams, _ = cameras.load_cameras(product, run)
    s0, a0 = stage_dir(run, product, "s0_intake").load()
    tex = gen.texture("basecolor")
    parts = [(arrays["frame_V"], arrays["frame_F"], None, None, (190, 140, 100))]
    parts += [(lo["V"], lo["F"], None, None, (150, 200, 245)) for lo in lens_out]
    sd5 = stage_dir(run, product, "s5_temples")
    if (sd5.root / "arrays.npz").exists():
        a5 = dict(np.load(sd5.root / "arrays.npz", allow_pickle=False))
        for sd in ("R", "L"):
            if f"temple_{sd}_V" in a5:
                parts.append((a5[f"temple_{sd}_V"].astype(float), a5[f"temple_{sd}_F"], a5.get(f"temple_{sd}_UV"), tex,
                              (120, 120, 200)))
    gparts = [(gen.V.astype(float), gen.F, gen.UV, tex, (170, 170, 170))] if gen.UV is not None else         [(gen.Vd.astype(float), gen.Fd, None, None, (170, 170, 170))]
    font = _font(18)
    rows = []
    for v in core.FIT_VIEWS:
        photo = core.load_photo(core.PRODUCTS[product], v)
        H, W = photo.shape[:2]
        x0, y0, x1, y1 = s0["views"][v]["bbox_xyxy"]
        m = int(0.06 * (x1 - x0))
        x0, y0, x1, y1 = max(0, x0 - m), max(0, y0 - m), min(W, x1 + m), min(H, y1 + m)
        cam = cameras.resized_camera(cams[v], 1.0, x0, y0)
        shape = (y1 - y0, x1 - x0)
        a = T.textured_render(parts, cam, frame, shape)
        b = T.textured_render(gparts, cam, frame, shape)
        fg = np.asarray(a0[f"fg_{v}"], bool)[y0:y1, x0:x1]
        gm = (b != 255).any(axis=2)
        for img in (a, b):
            img[fg & ~ndimage.binary_erosion(fg)] = (0, 0, 0)
            img[gm & ~ndimage.binary_erosion(gm)] = (30, 60, 255)
        row = np.concatenate([photo[y0:y1, x0:x1], a, b], 1)
        k = min(1.0, width / row.shape[1])
        im = Image.fromarray(row).resize((int(row.shape[1] * k), int(row.shape[0] * k)), Image.LANCZOS)
        ImageDraw.Draw(im).text((6, 4), f"{product} {v}: photo | BSA (plate orange, lens blue, S5 arms + donors "
                                        f"textured) | generator; black = photo matte, blue = generator contour",
                                fill=(0, 0, 0), font=font)
        rows.append(im)
    Wt = max(r.width for r in rows)
    sheet = Image.new("RGB", (Wt, sum(r.height for r in rows)), "white")
    yy = 0
    for r in rows:
        sheet.paste(r, (0, yy))
        yy += r.height
    path = stage_dir(run, product, STAGE).root / "views.png"
    sheet.save(path)
    return str(path)


def contact_sheet(run: str = "m1", products=None, width: int = 900) -> str:
    from PIL import Image
    ims = []
    for p in products or list(core.PRODUCTS):
        f = stage_dir(run, p, STAGE).root / "sheet.png"
        if f.exists():
            im = Image.open(f).convert("RGB")
            ims.append(im.resize((width, int(im.size[1] * width / im.size[0]))))
    if not ims:
        return ""
    H = max(i.size[1] for i in ims)
    out = Image.new("RGB", (width * len(ims), H), (255, 255, 255))
    for k, im in enumerate(ims):
        out.paste(im, (k * width, 0))
    path = core.BSA_DATA / "runs" / run / "s6_contact.png"
    out.save(path)
    return str(path)


def main(argv: list[str] | None = None) -> None:
    import argparse
    ap = argparse.ArgumentParser(description="S6 assembly: front frame + lenses from S2 polygons lifted onto S4")
    ap.add_argument("--product", choices=sorted(core.PRODUCTS))
    ap.add_argument("--run", default="m1")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    for p in ([a.product] if a.product else list(core.PRODUCTS)):
        run(p, a.run, a.force)
    contact_sheet(a.run)


if __name__ == "__main__":
    main()
