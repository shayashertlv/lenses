"""Build the faceted temple solid, g3d.temple/2 (plan/WP6_FACETS.md §2.2, §2.5 core roll; WP6_V3.md §2). numpy
only, float64, deterministic.

The solid is {y_bot(d) <= y <= y_top(d), x_in(d) <= X <= S(d, y) - c(d, y)}: S = the outer surface (a facet plane,
a warp facet's surface, a border-band ramp or the base), c = the chamfer depth plus the roll depth.

Steps:
 1. facet polygons with no area or crossing themselves: E_SPEC (before any arrangement); the facet-only arrangement
    of the outline (A0): facet overlap / outside checks, the facet/base borders, the roll proximity check (E_ROLL), the
    border band: a line band_of(facet) into the base parallel to every facet/base border (border_band_mm; a soft
    facet's SOFT_BAND_MM), mitred where the base is convex, bevelled where it is reflex, closed onto the outline or a
    chord at a run end, clipped to the base;
 2. stations (full-height chords) at every table knot, node d, facet-edge/outline crossing, chamfer break and
    border-band end, subdivided to <= station_step_mm; kept with a roll;
 3. the full arrangement: outline, facet edges, stations, chamfer lines, the 8 roll lines per outline edge and the
    border band; every face is labelled (facet or base, chamfer, roll band); with a warp facet, a second
    arrangement adds its refinement lines (warp_segments, from the first one's surfaces: chord error <=
    WARP_CHORD_MM per cell, measured after the build as facets[].chord_mm; each line kept WARP_CLEAR_MM clear of the
    facet's vertices and off any edge it would graze, so no sliver triangle), retried on two more lattices and else
    left out (W_WARP_CHORD, also above WARP_CHORD_WARN_MM); an arrangement face that would visit a vertex twice (a
    facet corner touching the outline or another facet at one point) gets a chord through that vertex;
 4. surfaces (planes.py: planes, coef planes, warps), then vertex values: a facet vertex takes its facet's surface; a
    vertex shared by facets takes the first label's (their planes agree there, planes.py; one value per vertex:
    continuous, never a step); a base vertex at distance delta < band from a border takes base + (1 - delta / band) x
    the mismatch at that border's nearest point (the ramp; the border with the largest 1 - delta / band, the nearest
    one when every band is alike), else the base;
 5. depths: chamfers (v1) and the core roll: r = roll_mm as 8 stacked chamfer sub-bands along the top and bottom
    outline edges with depth c(s) = r - sqrt(r^2 - (r - s)^2) at equal arc angles (s = vertical offset from the edge),
    so the strip's outer edge moves in to X - r and the side silhouette is unchanged. Limit:
    r <= min(min thickness - t_min, 0.45 x outline height - chamfer widths), else E_ROLL;
 6. cells and polygons: a facet cell is a face of a facet; a warp facet's faces are warp cells. Facet cells
    (chamfered or rolled too) are planar n-gons; every other outer cell (warp, base, ramp) is the arrangement's
    ear-clip triangles, never an n-gon, so Blender and the GLB share the core's triangulation; inner polygons
    (planar: x_in is linear between stations), one planar quad per outline edge (top / bottom strips), a planar
    hinge-end cap and tip cap;
 7. self-checks: closed oriented 2-manifold, Euler 2, volume > 0, every polygon planar within planar_tol_mm (one
    tolerance: a spec planes.solve accepts never fails E_TRIANGULATE on its own rounding; it was 1e-9), thickness >=
    t_min (E_TOO_THIN), outer triangles with positive (d, y) area (no self-intersection);
 8. sharp edges: a crease edge (two different regions, facet/facet or facet/base) between facet cells always; one
    with a warp cell on either side while the crease still stands: its design dihedral (the angle between the two
    regions' surfaces, plane / warp / base, at the edge midpoint; chamfers, roll and triangulation aside) >=
    CREASE_STANDS_DEG, or its built dihedral >= 6 deg. A soft
    facet's edges with the base per border run (a connected chain): all sharp when half its length folds by
    SOFT_SHARP_DEG or more (built dihedral, length-weighted median), else all smooth; the edges inside its ramp (a ramp
    cell all of whose ramped vertices ramp to soft borders) and where that ramp meets the base only at a built
    dihedral >= SOFT_SHARP_DEG; creases between two facets are never soft. Edges inside one warp facet smooth; ramp
    and chamfer edges at a dihedral >= 6 deg;
    roll edges (sub-band / sub-band, roll / outer face, roll / strip) smooth; face-to-strip and face-to-cap sharp;
    strip / strip and strip / cap at kinks > kink_sharp_deg. Custom normals: face normals on facet-cell polygons;
    on a warp facet's fans its design surface's, at a corner the mean of its cone's normals
    (planes.WarpSurface.corner_grad); on a soft border's fans the facet's (design_corner_normals); a fan reaches the
    roll band only on its start line (tangent there); auto-smoothed elsewhere;
 9. creases: every facet/facet and facet/base pair with its length-weighted angle and ridge/valley shares, one
    per-edge rule (edge_folds) for the tables, the chart, the sections and model_edges: a facet|facet edge its built
    dihedral, a facet|base edge its fold across the border, the facet's design surface against the BASE surface at
    its midpoint (_base_border_design: WP6_V3 §3's angle, the fit's; never the ramp cell, never the slope along the
    border); the mean (mean_dihedral_deg, and so the flat rule), range_deg, the shape's ridge/valley vote and its
    shares (ridge_share, valley_share) over the face-to-face edges (face_edge: the roll and chamfer bands left out,
    face_length_mm, so the printed mean lies inside the printed range and the word beside it is the same edges'; every
    edge only for a pair that lies in those bands alone), length_mm, ridge and valley (mm) over every edge, as the
    chart draws them (the receipt prints the range beside a warp; printed angles never round across the flat line:
    deg_round, and E_CREASE_SHAPE cuts a flat angle: flat_cut); soft and the W_BORDER_RAMP step beside a facet|base
    pair; model_edges = the same face-to-face edges per connected run with the signed angle per point, for the
    compares (WP6_V3 §5, interface I1; soft only where the export shades the run smooth); each
    declared pair must share an edge inside the outline and be built with its declared shape (E_CREASE_SHAPE: facts
    only, no remedy); pairs below 2 deg, or with no edge that folds, are "flat" (a declaration on them is refused, an
    undeclared one warns W_FLAT_CREASE, but a facet|base pair with a step or a soft border). Where a facet runs
    straight (within STRAIGHT_NODE_DEG) through an inside node, or bends back there, and three or more creases meet
    there all declared with one shape, no node values build it (a convex corner holding a straight line is a prism
    along it): the refusal says one of them has the other shape or two of those faces are one (field
    straight_node).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from . import planes as PL_
from .arrange import Arrangement, build_arrangement, clip_segment, ear_clip, poly_area, simple_polygon
from .spec import (ARM_NORMAL_SPREAD_DEG, BORDER_RAMP_WARN_DEG, CREASE_STANDS_DEG, DEDUPE_MM, FLAT_CREASE_DEG,
                   ROLL_BANDS, SHARP_DIHEDRAL_DEG, SOFT_BAND_MM, SOFT_SHARP_DEG, TILE_TOL_MM2, ParsedSpec, TempleError,
                   _facet_crossings, chamfer_breaks, chamfer_width, parse, roll_height_limit, roll_profile)

WALL_KINDS = ("top", "bottom", "cap_hinge", "cap_tip")
OUTER_KINDS = ("outer", "roll")
INSIDE_KINDS = ("station", "roll", "chamfer")    # segments inside the outline by construction (never clipped)
WARP_CHORD_MM = 0.005             # warp refinement target per cell: twice the largest residual of its samples from
#                                   their least-squares plane (a linear triangle's chord error is at most about twice
#                                   the best plane's); the build measures the result (tables facets[].chord_mm)
WARP_MIN_CELL_MM = 0.125          # ... a cell side is split only while both halves keep this
WARP_SAMPLES = 6                  # ... sampled on a 6 x 6 cell-centred lattice
CHORD_SAMPLES = 4                 # chord_mm: barycentric samples per triangle side (15 points per triangle)


@dataclass
class TempleBuild:
    spec: ParsedSpec
    arr: Arrangement
    sol: object                       # planes.PlaneSolution
    face_label: list                  # per arrangement face: facet label or "base"
    face_chamfer: list                # per arrangement face: chamfer index or None
    face_roll: list                   # per arrangement face: inside the roll band
    face_cell: list                   # per arrangement face: "facet" | "warp" | "ramp" | "base"
    X: np.ndarray                     # (n,) outer X of every arrangement vertex (after chamfers and roll)
    X_surface: np.ndarray             # (n,) outer surface X before chamfers and roll
    thickness: np.ndarray             # (n,) X - x_in
    borders: list                     # [{"facet", "p", "q"}] facet/base border segments (merged runs, from A0)
    stations: list
    V: np.ndarray                     # (2n, 3) local (d, y, X): outer 0..n-1, inner n..2n-1
    polys: list                       # outward polygons (vertex id lists)
    poly_kind: list                   # outer | roll | inner | top | bottom | cap_hinge | cap_tip
    poly_region: list                 # outer / roll: facet label or "base" (+ "/chamfer<i>"); others: their kind
    poly_face: list                   # arrangement face of an outer / roll / inner polygon, else -1
    poly_cell: list                   # outer / roll: the face's cell kind; others: their kind
    tris: np.ndarray                  # (m, 3) triangles of the polygons
    tri_poly: np.ndarray              # (m,) polygon of each triangle
    sharp: list                       # sorted (a, b) a < b
    flat_polys: list                  # polygons whose corners take the face normal (outer facet cells)
    normals: np.ndarray               # (polys, 3) unit outward normals (local)
    checks: dict
    tables: dict
    warnings: list = field(default_factory=list)
    face_ramp: list = field(default_factory=list)  # per arrangement face: None, or a ramp cell's "soft" (every
    #                                                ramped vertex ramps to a soft facet's border) | "hard"

    @property
    def n2(self) -> int:
        return len(self.arr.V)

    # ------------------------------------------------------------------------------------------- world frame
    def world(self, side: int = +1, opening_deg: float | None = None) -> tuple[np.ndarray, list, np.ndarray]:
        """(V world mm, polys, tris) of the +X (side +1) or -X (side -1) temple at the spec's opening (or the given
        one). The -X temple is the exact mirror: x negated bit for bit, every polygon and triangle reversed."""
        h = self.spec.hinge
        th = h["opening_deg"] if opening_deg is None else float(opening_deg)
        W = to_world(self.V, h["z_mm"], h["axis_xz_mm"], th)
        if side > 0:
            return W, [list(p) for p in self.polys], self.tris.copy()
        if side != -1:
            raise ValueError("side must be +1 or -1")
        M = W.copy()
        M[:, 0] = -M[:, 0]
        return M, [list(p)[::-1] for p in self.polys], self.tris[:, ::-1].copy()


def to_world(V_local: np.ndarray, hinge_z: float, axis_xz, opening_deg: float = 0.0) -> np.ndarray:
    """Local (d, y, X) of the +X temple -> world mm (+Y up, +Z front): x = X, z = hinge_z - d, then the opening
    (+ = tip outward) about the vertical axis through (axis_x, axis_z)."""
    d, y, X = V_local[:, 0], V_local[:, 1], V_local[:, 2]
    x, z = X.astype(np.float64).copy(), hinge_z - d
    if opening_deg:
        a = math.radians(opening_deg)
        cx, cz = float(axis_xz[0]), float(axis_xz[1])
        dx, dz = x - cx, z - cz
        x = cx + dx * math.cos(a) - dz * math.sin(a)
        z = cz + dx * math.sin(a) + dz * math.cos(a)
    return np.stack([x, y.astype(np.float64), z], axis=1)


def to_local(W: np.ndarray, hinge_z: float, axis_xz, opening_deg: float = 0.0, side: int = +1) -> np.ndarray:
    """Inverse of to_world (for a -X temple pass side -1: its x is mirrored first)."""
    x, y, z = side * W[:, 0], W[:, 1], W[:, 2]
    if opening_deg:
        a = -math.radians(opening_deg)
        cx, cz = float(axis_xz[0]), float(axis_xz[1])
        dx, dz = x - cx, z - cz
        x = cx + dx * math.cos(a) - dz * math.sin(a)
        z = cz + dx * math.sin(a) + dz * math.cos(a)
    return np.stack([hinge_z - z, y, x], axis=1)


# ------------------------------------------------------------------------------------------- segments
def chord(ps: ParsedSpec, d: float) -> tuple[np.ndarray, np.ndarray]:
    return np.array([d, float(ps.y_bot(d))]), np.array([d, float(ps.y_top(d))])


def facet_segments(ps: ParsedSpec) -> list:
    segs = []
    for label, fac in ps.facets.items():
        P = [np.array(ps.nodes[n][:2], np.float64) for n in fac["poly"]]
        for k in range(len(P)):
            segs.append((P[k], P[(k + 1) % len(P)], ("facet", label)))
    return segs


def facet_values(ps: ParsedSpec, sol, label: str, d, y):
    """X of a facet at (d, y): its plane (or warp surface)."""
    return sol.planes[label].X(d, y)


def region_values(ps: ParsedSpec, sol, region: str, d, y):
    """X of a region's design surface at (d, y): a facet's values or the base (chamfers and roll aside)."""
    return ps.x_base(d, y) if region == "base" else facet_values(ps, sol, region, d, y)


def _warp_unfaded(ps: ParsedSpec, sol, region: str) -> bool:
    return region != "base" and sol.planes[region].kind == "warp"


def region_grad(ps: ParsedSpec, sol, region: str, d, y, h: float = 1e-3) -> tuple:
    """(dX/dd, dX/dy) of a region's design surface (region_values) at the points (d, y): a warp facet's own gradient
    (WarpSurface.grad: its corner gradient at a corner, where mean value coordinates are only C0), else central
    differences (h), so the base gradient counts."""
    if _warp_unfaded(ps, sol, region):
        return sol.planes[region].grad(d, y)
    gd = (region_values(ps, sol, region, d + h, y) - region_values(ps, sol, region, d - h, y)) / (2 * h)
    gy = (region_values(ps, sol, region, d, y + h) - region_values(ps, sol, region, d, y - h)) / (2 * h)
    return gd, gy


def design_dihedral(ps: ParsedSpec, sol, r1: str, r2: str, d, y, h: float = 1e-3) -> np.ndarray:
    """Angle (deg) between the design surfaces of two regions at the points (d, y) (region_grad); chamfers, roll and
    the triangulation do not count."""
    d, y = np.asarray(d, np.float64), np.asarray(y, np.float64)
    n = []
    for r in (r1, r2):
        gd, gy = region_grad(ps, sol, r, d, y, h)
        v = np.stack([-gd, -gy, np.ones(np.broadcast(gd, gy).shape)], axis=-1)
        n.append(v / np.linalg.norm(v, axis=-1, keepdims=True))
    cross = np.linalg.norm(np.cross(n[0], n[1]), axis=-1)
    return np.degrees(np.arctan2(cross, np.einsum("...i,...i->...", n[0], n[1])))


@dataclass
class Prep:
    """The facet-only arrangement and what the stations and the full arrangement need from it."""
    arr0: Arrangement
    labels0: list
    borders: list                     # [{"facet", "p", "q"}]
    band: list                        # [(p, q)] border-band segments (clipped to the base)
    crossings: list                   # [(d, y, chain)]


def prepare(ps: ParsedSpec) -> Prep:
    _check_polygons(ps)
    arr0 = build_arrangement(ps.boundary(), facet_segments(ps), chord_fn=lambda d: chord(ps, d))
    labels0, _ = _label_faces(ps, arr0)
    _check_roll_proximity(ps, arr0)
    borders, band = _border_band(ps, arr0, labels0)
    return Prep(arr0, labels0, borders, band, _facet_crossings(ps.snapped, ps.top, ps.bottom))


def _subdivide(a: float, b: float, step: float) -> list:
    nsub = int(math.ceil((b - a) / step - 1e-9))
    return [a + (b - a) * k / nsub for k in range(1, nsub)]


def station_list(ps: ParsedSpec, prep: Prep | None = None) -> list[float]:
    """Station chords over the whole temple (module docstring step 2). Derived border-band ends closer than snap_mm
    (1e-6 mm without snapping) to another station are left out: their outline vertex exists anyway."""
    prep = prepare(ps) if prep is None else prep
    d0, d1, step = ps.d0, ps.d1, ps.options["station_step_mm"]
    src = set()
    for arr in (ps.top[:, 0], ps.bottom[:, 0], ps.inner.d, ps.t_top.d, ps.t_bot.d, [n[0] for n in ps.nodes.values()],
                [c[0] for c in prep.crossings], [k for ch in ps.chamfers for k in chamfer_breaks(ch)]):
        src |= {float(k) for k in arr if d0 < k < d1}
    near = max(ps.options["snap_mm"], 1e-6)
    for k in sorted({float(e[0]) for seg in prep.band for e in seg}):
        if d0 < k < d1 and (not src or min(abs(x - k) for x in src) > near):
            src.add(k)
    bounds = [d0] + sorted(src) + [d1]
    out = []
    for a, b in zip(bounds[:-1], bounds[1:]):
        out += _subdivide(a, b, step)
        out.append(b)
    out = sorted(set(round(x, 12) for x in out))
    return [x for x in out if d0 + 1e-9 < x < d1 - 1e-9]


def segments(ps: ParsedSpec, prep: Prep, stations: list) -> list:
    segs = facet_segments(ps)
    for d in stations:
        p, q = chord(ps, d)
        segs.append((p, q, ("station", float(d))))
    for i, ch in enumerate(ps.chamfers):
        a, b = ch["d"]
        edge = ps.top if ch["edge"] == "top" else ps.bottom
        ds = sorted({a, b} | set(chamfer_breaks(ch)) | {float(k) for k in edge[:, 0] if a < k < b})
        if ch["edge"] == "top":
            pts = [np.array([d, float(ps.y_top(d)) - float(chamfer_width(ch, d))]) for d in ds]
        else:
            pts = [np.array([d, float(ps.y_bot(d)) + float(chamfer_width(ch, d))]) for d in ds]
        for p, q in zip(pts[:-1], pts[1:]):
            segs.append((p, q, ("chamfer", i)))
    if ps.roll_mm > 0:
        s, _ = roll_profile(ps.roll_mm)
        for k in range(1, ROLL_BANDS + 1):
            for name, chain, sgn in (("top", ps.top, -1.0), ("bottom", ps.bottom, 1.0)):
                pts = chain + np.array([0.0, sgn * s[k]])
                for p, q in zip(pts[:-1], pts[1:]):
                    segs.append((p, q, ("roll", name, k)))
    for p, q in prep.band:
        segs.append((p, q, ("band",)))
    return segs


def chamfer_depth(ps: ParsedSpec, d, y):
    """c(d, y): total chamfer depth (mm removed from the outer surface)."""
    d, y = np.asarray(d, np.float64), np.asarray(y, np.float64)
    c = np.zeros(np.broadcast(d, y).shape)
    for ch in ps.chamfers:
        w = np.asarray(chamfer_width(ch, d))
        k = ch["depth_mm"] / ch["width_mm"]
        if ch["edge"] == "top":
            s = y - (ps.y_top(d) - w)
        else:
            s = (ps.y_bot(d) + w) - y
        inside = (d >= ch["d"][0]) & (d <= ch["d"][1]) & (w > 0)
        c = c + np.where(inside, k * np.maximum(s, 0.0), 0.0)
    return c


def roll_depth(ps: ParsedSpec, d, y):
    """The core roll's depth at (d, y): the chord of the quarter arc through the sub-band lines, from r at the outline
    edge to 0 at s = r (top and bottom edges add)."""
    d, y = np.asarray(d, np.float64), np.asarray(y, np.float64)
    if ps.roll_mm <= 0:
        return np.zeros(np.broadcast(d, y).shape)
    s, c = roll_profile(ps.roll_mm)
    return np.interp(ps.y_top(d) - y, s, c, right=0.0) + np.interp(y - ps.y_bot(d), s, c, right=0.0)


def face_chamfer_of(ps: ParsedSpec, p) -> int | None:
    d, y = float(p[0]), float(p[1])
    for i, ch in enumerate(ps.chamfers):
        if not (ch["d"][0] < d < ch["d"][1]):
            continue
        w = float(chamfer_width(ch, d))
        if ch["edge"] == "top" and y > float(ps.y_top(d)) - w:
            return i
        if ch["edge"] == "bottom" and y < float(ps.y_bot(d)) + w:
            return i
    return None


# ------------------------------------------------------------------------------------------- facet-only arrangement
def _check_polygons(ps: ParsedSpec) -> None:
    """E_SPEC for a facet polygon of no area or crossing itself, before any arrangement is built (a crossing used to
    reach the arrangement first and come back as E_TRIANGULATE, "a face pinches at a vertex")."""
    for l, f in ps.facets.items():
        P = np.array([ps.nodes[n][:2] for n in f["poly"]])
        if abs(poly_area(P)) <= TILE_TOL_MM2:
            raise TempleError("E_SPEC", f"facet {l}: its polygon has no area", facet=l)
        ok, bad = simple_polygon(P)
        if not ok:
            raise TempleError("E_SPEC", f"facet {l}: its polygon crosses itself (edges {bad})", facet=l)


def _label_faces(ps: ParsedSpec, arr: Arrangement) -> tuple[list, list]:
    polys = {l: np.array([ps.nodes[n][:2] for n in f["poly"]]) for l, f in ps.facets.items()}
    labels, chamf = [], []
    area = {l: 0.0 for l in polys}
    ins = {l: points_in_poly(arr.interior_pts, P) for l, P in polys.items()}
    for fi, p in enumerate(arr.interior_pts):
        inside = [l for l in polys if ins[l][fi]]
        if len(inside) > 1:
            raise TempleError("E_FACET_OVERLAP", f"facets {inside} overlap at (d, y) ({p[0]:.3f}, {p[1]:.3f})",
                              facets=inside, at=p.tolist())
        lab = inside[0] if inside else "base"
        labels.append(lab)
        chamf.append(face_chamfer_of(ps, p))
        if inside:
            area[lab] += float(arr.areas[fi])
    out = [l for l, a in area.items() if a <= TILE_TOL_MM2]
    if out:
        raise TempleError("E_FACET_OUTSIDE", f"facets {out} lie outside the outline (no area inside it)", facets=out)
    return labels, chamf


def _check_roll_proximity(ps: ParsedSpec, arr0: Arrangement) -> None:
    """E_ROLL: a node or facet-edge crossing within roll_mm (vertically) of the top or bottom outline edge, off it."""
    r = ps.roll_mm
    if r <= 0:
        return
    V = arr0.V
    used = sorted({v for f in arr0.faces for v in f})
    st = ps.y_top(V[used, 0]) - V[used, 1]
    sb = V[used, 1] - ps.y_bot(V[used, 0])
    near = [(v, a, b) for v, a, b in zip(used, st, sb) if (1e-9 < a < r) or (1e-9 < b < r)]
    nodes = {}
    for name, (d, y, _) in ps.nodes.items():
        for v, a, b in near:
            if abs(V[v, 0] - d) <= 1e-6 and abs(V[v, 1] - y) <= 1e-6:
                nodes[name] = round(float(min(a if a > 1e-9 else np.inf, b if b > 1e-9 else np.inf)), 4)
    on_facet_edges = [(v, a, b) for v, a, b in near if _on_facet_edge(ps, V[v])]
    if not on_facet_edges:
        return
    pts = [[round(float(V[v, 0]), 4), round(float(V[v, 1]), 4)] for v, _, _ in on_facet_edges]
    gaps = [min(a if a > 1e-9 else np.inf, b if b > 1e-9 else np.inf) for _, a, b in on_facet_edges]
    k = int(np.argmin(gaps))
    # facts and every way out (v3p3-review-e2e P3-2: one remedy, "move them onto the outline ... or at least roll_mm
    # away"; the roll itself is a way, and the brief invites it, 39 of 2,000 fuzz designs)
    at = ", ".join(f"{n} {g:.2f} mm" for n, g in sorted(nodes.items()))
    raise TempleError("E_ROLL", f"roll_mm {r:g}: {len(pts)} facet corner(s) or edge crossing(s) lie off the top or "
                      f"bottom outline but within roll_mm of it (closest {gaps[k]:.2f} mm at d {pts[k][0]:.2f}, y "
                      f"{pts[k][1]:.2f}" + (f"; nodes {at}" if at else "") + "): the roll band holds no facet corner; "
                      f"ways: each onto the outline (within snap_mm {ps.options['snap_mm']:g}) or at least roll_mm "
                      f"from it, roll_mm at most {math.floor(gaps[k] * 100.0) / 100.0:.2f}, or a layout without them",
                      roll_mm=r, nodes=nodes, points=pts[:12], reason="proximity")


def _on_facet_edge(ps: ParsedSpec, p) -> bool:
    for fac in ps.facets.values():
        P = np.array([ps.nodes[n][:2] for n in fac["poly"]], np.float64)
        Q = np.roll(P, -1, axis=0)
        s = Q - P
        L2 = np.einsum("ij,ij->i", s, s)
        t = np.clip(np.einsum("ij,ij->i", p - P, s) / np.maximum(L2, 1e-30), 0.0, 1.0)
        if np.min(np.hypot(*(P + t[:, None] * s - p).T)) <= 1e-6:
            return True
    return False


def _left(u) -> np.ndarray:
    return np.array([-u[1], u[0]])


def _cross2(a, b) -> float:
    return float(a[0] * b[1] - a[1] * b[0])


def band_of(ps: ParsedSpec, label: str) -> float:
    """The border band width of a facet's borders with the base: SOFT_BAND_MM for a soft facet, else
    border_band_mm."""
    return SOFT_BAND_MM if ps.facets[label].get("soft") else ps.options["border_band_mm"]


def _border_band(ps: ParsedSpec, arr0: Arrangement, labels0: list) -> tuple[list, list]:
    """Facet/base borders (merged runs, each with its band_mm: band_of its facet) and the band segments that far into
    the base (module docstring)."""
    ef = arr0.edge_faces()
    V = arr0.V
    borders, segs = [], []
    for fi, cyc in enumerate(arr0.faces):
        if labels0[fi] != "base":
            continue
        m = len(cyc)
        across = []
        for k in range(m):
            g = ef.get((cyc[(k + 1) % m], cyc[k]))
            across.append(labels0[g] if g is not None and labels0[g] != "base" else None)
        if not any(across):
            continue
        if all(across):
            runs = [(0, m, True)]
        else:
            runs = []
            for k in range(m):
                if across[k] and not across[k - 1]:
                    n = 0
                    while across[(k + n) % m]:
                        n += 1
                    runs.append((k, n, False))
        for k0, n, closed in runs:
            pieces = []                                   # merged collinear edges: [P, Q, facet]
            for j in range(n):
                k = (k0 + j) % m
                P, Q, lab = V[cyc[k]], V[cyc[(k + 1) % m]], across[k]
                if pieces and pieces[-1][2] == lab:
                    a, b = pieces[-1][0], pieces[-1][1]
                    u1, u2 = (b - a) / np.hypot(*(b - a)), (Q - P) / np.hypot(*(Q - P))
                    if abs(_cross2(u1, u2)) < 1e-12 and float(u1 @ u2) > 0:
                        pieces[-1][1] = Q
                        continue
                pieces.append([P, Q, lab])
            if closed and len(pieces) > 1 and pieces[0][2] == pieces[-1][2]:
                a, b, P, Q = pieces[-1][0], pieces[-1][1], pieces[0][0], pieces[0][1]
                u1, u2 = (b - a) / np.hypot(*(b - a)), (Q - P) / np.hypot(*(Q - P))
                if abs(_cross2(u1, u2)) < 1e-12 and float(u1 @ u2) > 0:
                    pieces[0][0] = a
                    pieces.pop()
            for P, Q, lab in pieces:
                borders.append({"facet": lab, "p": P.copy(), "q": Q.copy(), "band_mm": band_of(ps, lab)})
            dirs = [(Q - P) / np.hypot(*(Q - P)) for P, Q, _ in pieces]
            offs = [[P + band_of(ps, lab) * _left(u), Q + band_of(ps, lab) * _left(u)]
                    for (P, Q, lab), u in zip(pieces, dirs)]
            bevels = []
            joints = range(len(pieces)) if closed else range(1, len(pieces))
            for i in joints:
                h = i - 1
                O1, O2 = offs[h], offs[i]
                turn = _cross2(dirs[h], dirs[i])
                if turn > 0:                                   # the base is convex here: offsets overlap, mitre them
                    r1, r2 = O1[1] - O1[0], O2[1] - O2[0]
                    den = _cross2(r1, r2)
                    if abs(den) > 1e-15:
                        w = O2[0] - O1[0]
                        t1, t2 = _cross2(w, r2) / den, _cross2(w, r1) / den
                        if 0.0 <= t1 <= 1.0 and 0.0 <= t2 <= 1.0:
                            X = O1[0] + t1 * r1
                            O1[1], O2[0] = X, X.copy()
                            continue
                if np.hypot(*(O2[0] - O1[1])) > DEDUPE_MM:
                    bevels.append((O1[1], O2[0]))
            if not closed:
                q_end = _walk(V, cyc, (k0 + n) % m, band_of(ps, pieces[-1][2]), +1, across)
                q_start = _walk(V, cyc, k0, band_of(ps, pieces[0][2]), -1, across)
                # an offset that already runs past the walked point (it crosses the face's boundary just before its
                # end: a chord at 90.0000 deg to a border at 0.0003 deg left a 9e-6 mm stub that the closing segment
                # doubled back over, a dangling vertex, E_TRIANGULATE; fuzz, flat facets too) ends there instead
                if _runs_past(offs[-1][1], q_end, dirs[-1]):
                    offs[-1][1] = np.asarray(q_end, np.float64).copy()
                if _runs_past(offs[0][0], q_start, -dirs[0]):
                    offs[0][0] = np.asarray(q_start, np.float64).copy()
            segs += [(a, b) for a, b in offs] + bevels
            if not closed:
                segs.append((offs[-1][1], q_end))
                segs.append((q_start, offs[0][0]))
    return borders, _clip_to_base(ps, segs)


def _runs_past(end, q, u) -> bool:
    """True when the band offset ending at `end` (direction u) already passed the run's walked end point q: q lies
    behind `end` on the offset's own line (within 1 deg), so the closing segment end -> q would double back over it."""
    w = np.asarray(q, np.float64) - np.asarray(end, np.float64)
    L = float(np.hypot(*w))
    return L > 0.0 and float(w @ u) < 0.0 and abs(_cross2(w, u)) <= math.sin(math.radians(1.0)) * L


def _walk(V, cyc, k: int, dist: float, direction: int, across) -> np.ndarray:
    """The point `dist` along the face cycle from vertex cyc[k], forward (+1) or backward (-1), over non-border edges
    (stopping at the next border run)."""
    m = len(cyc)
    left = dist
    P = V[cyc[k]]
    for _ in range(m):
        e = k if direction > 0 else (k - 1) % m
        if across[e]:
            return P
        Q = V[cyc[(k + direction) % m]]
        L = float(np.hypot(*(Q - P)))
        if L >= left:
            return P + (Q - P) * (left / L)
        left -= L
        k = (k + direction) % m
        P = Q
    return P


def _clip_to_base(ps: ParsedSpec, segs: list) -> list:
    """The parts of the band segments inside the outline and outside every facet."""
    boundary = ps.boundary()
    fpolys = [np.array([ps.nodes[n][:2] for n in f["poly"]], np.float64) for f in ps.facets.values()]
    out = []
    for p, q in segs:
        p, q = np.asarray(p, np.float64), np.asarray(q, np.float64)
        L = float(np.hypot(*(q - p)))
        if L < DEDUPE_MM:
            continue
        keep = clip_segment(p, q, boundary)
        for P in fpolys:
            cut = clip_segment(p, q, P)
            if not cut:
                continue
            nk = []
            for a, b in keep:
                for c0, c1 in cut:
                    if c1 <= a or c0 >= b:
                        continue
                    if c0 > a:
                        nk.append((a, c0))
                    a = max(a, c1)
                if b > a:
                    nk.append((a, b))
            keep = nk
        for a, b in keep:
            if (b - a) * L > 10 * DEDUPE_MM:
                out.append((p + a * (q - p), p + b * (q - p)))
    return out


# ------------------------------------------------------------------------------------------- the build
def _plan(ps: ParsedSpec):
    """A build's first steps: prep, the stations, the arrangement, its face labels (+ chamfer ids) and the planes.
    With warp facets, a second arrangement adds their refinement lines (warp_segments, from the first one's surfaces)
    and the surfaces are solved again on it (the same corner values; the new vertices take their values from them).
    The last item says whether a needed refinement was left out (every lattice failed: the build keeps the unrefined
    arrangement, W_WARP_CHORD)."""
    prep = prepare(ps)
    stations = station_list(ps, prep)
    segs = segments(ps, prep, stations)
    arr = build_arrangement(ps.boundary(), segs, chord_fn=lambda d: chord(ps, d), inside=INSIDE_KINDS)
    labels, chamf = _label_faces(ps, arr)
    sol = PL_.solve(ps, arr, labels)
    for shift in range(WARP_TRIES):
        extra = warp_segments(ps, sol, stations, shift, arr, labels)
        if not extra:
            return prep, stations, arr, labels, chamf, sol, False
        try:                         # a refinement line can graze a vertex (a sliver of no area): try another lattice
            arr2 = build_arrangement(ps.boundary(), segs + extra, chord_fn=lambda d: chord(ps, d),
                                     inside=INSIDE_KINDS)
            labels2, chamf2 = _label_faces(ps, arr2)
            sol2 = PL_.solve(ps, arr2, labels2)
        except TempleError:
            continue
        return prep, stations, arr2, labels2, chamf2, sol2, False
    return prep, stations, arr, labels, chamf, sol, WARP_TRIES > 0   # none built: chord_mm says what it costs


WARP_TRIES = 3                    # lattices warp_segments tries before the build keeps the unrefined arrangement
WARP_CHORD_WARN_MM = 2.0 * WARP_CHORD_MM   # W_WARP_CHORD above this measured chord error (or with no refinement)


def warp_segments(ps: ParsedSpec, sol, stations: list, shift: int = 0, arr: Arrangement | None = None,
                  labels: list | None = None) -> list:
    """Refinement lines inside every warp facet (WP6_V3 §2; the 2 mm station strips alone left 0.044-0.058 mm of
    chord error on a 20 x 10 mm face with one corner 1.5-2 mm off, and a strip's triangles span the whole face; mean
    value coordinates are only C0 at the corners, so the error there falls only linearly with the cell size). Root
    cells: each station strip [s, s'] crossing the facet's (d, y) box times rows station_step_mm high on one y lattice
    (y = k step), a lattice row left out of a strip where it breaks the clearance below (the strip's root cell is then
    taller). A cell is split (in d and / or y: a side only while both parts keep WARP_MIN_CELL_MM and it is at least
    half the other side) while its WARP_SAMPLES^2 samples inside the facet polygon and the outline (a cell-centred
    lattice; fewer than 3, or on one line: split while the cell touches the facet) leave 2 x their largest residual
    from the least-squares plane above WARP_CHORD_MM. Clearance (with `arr`, the unrefined arrangement and its face
    `labels`; review-builder-design F2: on run 7's crown the row y = 10.0 met the crown|logo edge A-K at 0.6 deg and
    cut triangles of 0.002 deg beside it, and the exported GLB's normals there were 5.6 deg off Blender's): a split
    line (a row over the cell's d range, a column over its y range) never comes within WARP_CLEAR_MM of a vertex of
    the facet's faces in `arr`, of a point where one of their edges crosses the cell's sides, nor of an edge of theirs
    within WARP_GRAZE_DEG of the line's direction (a facet edge, the outline, a roll or chamfer line it would graze);
    the split is the free position nearest the cell's middle on one lattice per axis (rows: the facet's row lattice
    cut to steps of at least WARP_MIN_CELL_MM / 2; columns: the strip's, likewise), none when no lattice position is
    free, so any two lines meeting a station or a leaf side meet it at one point or that step apart (arbitrary free
    positions left 0.0005 mm edges on the stations: a row at 11.0 in one strip, 11.0005 in the next). When any root cell
    needs a split, every leaf (root cells too) gives its edges (vertical ones on the strip's own stations left out),
    merged per line and clipped to the facet polygon (the arrangement clips them to the outline); otherwise nothing,
    so a warp whose corner values are coplanar (or nearly) builds the very arrangement of its flat facet. Tag
    ("warp", label). `shift` > 0 (a retry, _plan) moves the y lattice by frac(0.371 shift) of a row and prefers splits
    at 0.5 + 0.043 shift instead of halves."""
    out = []
    bnd = ps.boundary()
    st = [ps.d0] + [float(s) for s in stations] + [ps.d1]
    row = ps.options["station_step_mm"]
    off = (0.371 * shift) % 1.0 * row
    frac = 0.5 + 0.043 * shift
    k = WARP_SAMPLES
    lat = (np.arange(k) + 0.5) / k
    U = np.stack(np.meshgrid(lat, lat, indexing="ij"), axis=-1).reshape(-1, 2)          # (k^2, 2) in the unit cell
    m_ = WARP_MIN_CELL_MM
    for label in sorted(sol.planes):
        surf = sol.planes[label]
        if surf.kind != "warp":
            continue
        Pg = surf.P
        lo, hi = Pg.min(axis=0), Pg.max(axis=0)
        G = _facet_features(arr, labels, label) if arr is not None else None
        ys = off + row * np.arange(math.floor((lo[1] - off) / row), math.ceil((hi[1] - off) / row) + 1)
        cells = []                                                                    # (d0, y0, wd, hy, strip)
        for j, (a, b) in enumerate(zip(st[:-1], st[1:])):
            if not (b > lo[0] and a < hi[0]):
                continue
            keep = [ys[0]] + [y for y in ys[1:-1] if G is None or _free(_forbidden(G, a, b, 0), y, y, y) is not None] \
                + [ys[-1]]
            cells += [(a, y0, b - a, y1 - y0, j) for y0, y1 in zip(keep[:-1], keep[1:])]
        leaves, refine = [], False
        while cells:
            C = np.array([c[:4] for c in cells], np.float64)
            pts = C[:, None, :2] + C[:, None, 2:4] * U[None]                            # (m, k^2, 2)
            flat = pts.reshape(-1, 2)
            in_out = points_in_poly(flat, bnd).reshape(len(cells), -1)
            ins = points_in_poly(flat, Pg).reshape(len(cells), -1) & in_out
            X = np.zeros(ins.shape)
            if ins.any():
                X[ins] = surf.X(flat[ins.ravel(), 0], flat[ins.ravel(), 1])
            err = _cell_plane_error(pts, X, ins, C)
            touch = ins.any(axis=1)                       # or a sliver of the facet the samples miss
            maybe = np.nonzero(~touch & in_out.any(axis=1))[0]
            touch[maybe] = _cells_touch(C[maybe], Pg)
            nxt = []
            for i, (d0, y0, wd, hy, j) in enumerate(cells):
                if not touch[i]:
                    continue
                # split a side only while both parts keep WARP_MIN_CELL_MM, and the longer one first: a thin station
                # strip is cut across only (cells far thinner than the strip made 1e-4 mm edges, whose float32
                # directions moved the exported normals by up to 0.9 deg); at the free position nearest the preferred
                a = b = None
                if err[i] > WARP_CHORD_MM:
                    if wd >= 2.0 * m_ and wd >= 0.5 * hy:
                        a = d0 + frac * wd if G is None else \
                            _free(_forbidden(G, y0, y0 + hy, 1), d0 + m_, d0 + wd - m_, d0 + frac * wd,
                                  (st[j], _lattice_step(st[j + 1] - st[j])))
                    if hy >= 2.0 * m_ and hy >= 0.5 * wd:
                        b = y0 + frac * hy if G is None else \
                            _free(_forbidden(G, d0, d0 + wd, 0), y0 + m_, y0 + hy - m_, y0 + frac * hy,
                                  (off, _lattice_step(row)))
                if a is None and b is None:
                    leaves.append((d0, y0, wd, hy, j))
                    continue
                ds = [(d0, wd)] if a is None else [(d0, a - d0), (a, d0 + wd - a)]
                yy = [(y0, hy)] if b is None else [(y0, b - y0), (b, y0 + hy - b)]
                nxt += [(dd, y_, w_, h_, j) for dd, w_ in ds for y_, h_ in yy]
                refine = True
            cells = nxt
        if not refine:
            continue
        horiz: dict = {}
        vert: dict = {}
        for d0, y0, wd, hy, j in leaves:
            for yy in (y0, y0 + hy):
                horiz.setdefault(round(yy, 12), []).append((d0, d0 + wd))
            for dd in (d0, d0 + wd):
                if abs(dd - st[j]) > 1e-12 and abs(dd - st[j + 1]) > 1e-12:
                    vert.setdefault(round(dd, 12), []).append((y0, y0 + hy))
        lines = [(np.array([a, yy]), np.array([b, yy])) for yy, iv in sorted(horiz.items()) for a, b in _merge(iv)]
        lines += [(np.array([dd, a]), np.array([dd, b])) for dd, iv in sorted(vert.items()) for a, b in _merge(iv)]
        for p, q in lines:
            for s0, s1 in clip_segment(p, q, Pg):
                if (s1 - s0) * float(np.hypot(*(q - p))) > 10 * DEDUPE_MM:
                    out.append((p + s0 * (q - p), p + s1 * (q - p), ("warp", label)))
    return out


WARP_CLEAR_MM = WARP_MIN_CELL_MM  # a refinement line keeps this clear of the facet's arrangement vertices, of edge
#                                   crossings with its cell's sides and of edges it would graze ...
WARP_GRAZE_DEG = 10.0             # ... an edge within this of the line's direction (it would cut a wedge this thin)


def _facet_features(arr: Arrangement, labels: list, label: str) -> dict:
    """The unrefined arrangement's vertices (k, 2) and edges (m, 2, 2) of the faces labelled `label` (warp_segments'
    clearance)."""
    vs, es = set(), set()
    for fi, f in enumerate(arr.faces):
        if labels[fi] == label:
            vs.update(f)
            es.update((min(f[i], f[i - 1]), max(f[i], f[i - 1])) for i in range(len(f)))
    es = sorted(es)
    return {"V": arr.V[sorted(vs)], "E": arr.V[np.array(es, np.int64).reshape(-1, 2)]}


def _forbidden(G: dict, lo: float, hi: float, ax: int) -> np.ndarray:
    """(n, 2) open intervals of the line coordinate where a split line breaks warp_segments' clearance: a row (ax 0:
    y = const over d in [lo, hi]) or a column (ax 1: d = const over y in [lo, hi]); each within WARP_CLEAR_MM of a
    vertex (along the line's range grown by WARP_CLEAR_MM), of an edge's crossing with the range's ends (the cell's
    sides), or of an edge within WARP_GRAZE_DEG of the line's direction over the grown range."""
    c, o = WARP_CLEAR_MM, 1 - ax
    V, E = G["V"], G["E"]
    out = []
    near = (V[:, ax] >= lo - c) & (V[:, ax] <= hi + c)
    out.append(np.stack([V[near, o] - c, V[near, o] + c], axis=1))
    P, Q = E[:, 0], E[:, 1]
    da, do = Q[:, ax] - P[:, ax], Q[:, o] - P[:, o]
    run = da != 0.0
    with np.errstate(divide="ignore", invalid="ignore"):
        for x in (lo, hi):                                   # crossings with the cell's sides
            t = (x - P[:, ax]) / da
            hit = run & (t >= 0.0) & (t <= 1.0)
            v = P[hit, o] + t[hit] * do[hit]
            out.append(np.stack([v - c, v + c], axis=1))
        graze = run & (np.abs(do) <= math.tan(math.radians(WARP_GRAZE_DEG)) * np.abs(da))
        t0 = np.clip((lo - c - P[:, ax]) / da, 0.0, 1.0)
        t1 = np.clip((hi + c - P[:, ax]) / da, 0.0, 1.0)
        ok = graze & (np.minimum(t0, t1) < np.maximum(t0, t1))
        v0, v1 = P[ok, o] + t0[ok] * do[ok], P[ok, o] + t1[ok] * do[ok]
    out.append(np.stack([np.minimum(v0, v1) - c, np.maximum(v0, v1) + c], axis=1))
    return np.concatenate(out, axis=0)


def _lattice_step(length: float) -> float:
    """length / 2^n for the largest n keeping the step >= WARP_MIN_CELL_MM / 2 (halving a cell of that length lands
    on it)."""
    step = length
    while step / 2.0 >= 0.5 * WARP_MIN_CELL_MM:
        step /= 2.0
    return step


def _free(iv: np.ndarray, a: float, b: float, pref: float, lattice: tuple | None = None) -> float | None:
    """The position in [a, b] nearest `pref` outside every open interval of iv, None when none is (a > b: None);
    with lattice = (origin, step) only origin + k step (ties: the lower)."""
    if a > b:
        return None
    iv = iv[(iv[:, 1] > a) & (iv[:, 0] < b)]
    inside = lambda x: bool(np.any((iv[:, 0] < x) & (x < iv[:, 1])))       # noqa: E731
    if lattice is not None:
        o, q = lattice
        ks = np.arange(math.ceil((a - o) / q - 1e-9), math.floor((b - o) / q + 1e-9) + 1)
        cand = [float(o + q * k) for k in ks]
        cand = sorted((x for x in cand if not inside(x)), key=lambda x: (abs(x - pref), x))
        return cand[0] if cand else None
    if not inside(pref):
        return pref
    cand = np.concatenate([[a, b], iv[:, 0], iv[:, 1]])
    cand = sorted({float(x) for x in cand if a <= x <= b and not inside(x)}, key=lambda x: (abs(x - pref), x))
    return cand[0] if cand else None


def _merge(iv: list) -> list:
    """Overlapping or touching intervals [(a, b)] merged, ascending."""
    out = []
    for a, b in sorted(iv):
        if out and a <= out[-1][1] + 1e-12:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def _cell_plane_error(pts: np.ndarray, X: np.ndarray, ins: np.ndarray, C: np.ndarray) -> np.ndarray:
    """Per cell: 2 x the largest residual of its inside samples (ins) from their least-squares plane (cell-local
    coordinates); inf for fewer than 3 samples or samples on one line."""
    out = np.full(len(C), np.inf)
    cnt = ins.sum(axis=1)
    loc = (pts - (C[:, None, :2] + 0.5 * C[:, None, 2:4])) / C[:, None, 2:4]
    B = np.stack([np.ones(ins.shape), loc[..., 0], loc[..., 1]], axis=-1)                     # (m, s, 3)
    w = ins.astype(np.float64)
    M = np.einsum("ms,msi,msj->mij", w, B, B)
    ok = (cnt >= 3) & (np.abs(np.linalg.det(M)) > 1e-6 * np.maximum(cnt, 1) ** 3)
    if not ok.any():
        return out
    rhs = np.einsum("ms,msi,ms->mi", w[ok], B[ok], X[ok])
    coef = np.linalg.solve(M[ok], rhs[..., None])[..., 0]
    res = (X[ok] - np.einsum("msi,mi->ms", B[ok], coef)) * w[ok]
    out[ok] = 2.0 * np.abs(res).max(axis=1)
    return out


def _cells_touch(C: np.ndarray, Pg: np.ndarray) -> np.ndarray:
    """Per cell (d0, y0, wd, hy): a polygon edge meets the closed cell (Liang-Barsky slabs, all cells x edges)."""
    if not len(C):
        return np.zeros(0, bool)
    lo, hi = C[:, None, :2], C[:, None, :2] + C[:, None, 2:4]                      # (m, 1, 2)
    A = Pg[None]
    D = (np.roll(Pg, -1, axis=0) - Pg)[None]                                        # (1, e, 2)
    par = D == 0.0
    with np.errstate(divide="ignore", invalid="ignore"):
        ta, tb = (lo - A) / D, (hi - A) / D
    inside = (A >= lo) & (A <= hi)
    tmin = np.where(par, np.where(inside, -np.inf, np.inf), np.minimum(ta, tb))
    tmax = np.where(par, np.where(inside, np.inf, -np.inf), np.maximum(ta, tb))
    t0 = np.maximum(tmin.max(axis=-1), 0.0)
    t1 = np.minimum(tmax.min(axis=-1), 1.0)
    return (t0 <= t1).any(axis=1)


def build_temple(spec) -> TempleBuild:
    """Plan and check one +X temple from a spec dict (or a ParsedSpec). Raises TempleError (nothing built)."""
    ps = spec if isinstance(spec, ParsedSpec) else parse(spec)
    prep, stations, arr, labels, chamf, sol, unrefined = _plan(ps)
    V2 = arr.V
    n = len(V2)
    band = ps.options["border_band_mm"]
    B = ps.x_base(V2[:, 0], V2[:, 1])
    flabs = sorted({l for l in labels if l != "base"})
    col = {l: j for j, l in enumerate(flabs)}
    W = np.full((n, len(flabs)), np.inf)          # 0 where a facet has the vertex (inf: not on it)
    for fi, f in enumerate(arr.faces):
        if labels[fi] != "base":
            W[f, col[labels[fi]]] = 0.0
    S = B.copy()
    on_facet = np.isfinite(W).any(axis=1) if flabs else np.zeros(n, bool)
    if flabs:
        pick = np.argmin(W, axis=1)                # the vertex's first facet label
        for j, lab in enumerate(flabs):
            idx = np.nonzero(on_facet & (pick == j))[0]
            if len(idx):
                S[idx] = facet_values(ps, sol, lab, V2[idx, 0], V2[idx, 1])
    delta = np.full(n, np.inf)
    vband = np.full(n, band)                      # the band of the border a vertex ramps to
    ramp_of = np.full(n, None, object)            # ... and that border's facet (None: no ramp)
    if prep.borders:
        P = np.array([b["p"] for b in prep.borders])
        Q = np.array([b["q"] for b in prep.borders])
        bands = np.array([b["band_mm"] for b in prep.borders])
        base_v = np.nonzero(~on_facet)[0]
        if len(base_v):
            X2 = V2[base_v]
            s = Q - P
            L2 = np.maximum(np.einsum("ij,ij->i", s, s), 1e-30)
            t = np.clip(((X2[:, None, :] - P[None]) * s[None]).sum(-1) / L2[None], 0.0, 1.0)
            C = P[None] + t[..., None] * s[None]
            dist = np.hypot(X2[:, None, 0] - C[..., 0], X2[:, None, 1] - C[..., 1])
            # the border whose ramp weight 1 - dist / band is largest: the nearest one when every band is alike
            j = np.argmin(dist if np.all(bands == band) else dist / bands[None], axis=1)
            dmin = dist[np.arange(len(base_v)), j]
            delta[base_v] = dmin
            vband[base_v] = bands[j]
            ramp = dmin < bands[j]
            for k in np.nonzero(ramp)[0]:
                q = C[k, j[k]]
                lab = prep.borders[j[k]]["facet"]
                ramp_of[base_v[k]] = lab
                mis = float(facet_values(ps, sol, lab, q[0], q[1])) - float(ps.x_base(q[0], q[1]))
                S[base_v[k]] = B[base_v[k]] + (1.0 - dmin[k] / bands[j[k]]) * mis
    delta[on_facet] = 0.0
    warp = {lab for lab in flabs if sol.planes[lab].kind == "warp"}
    cells, ramp_kind = [], []
    for fi, f in enumerate(arr.faces):
        lab = labels[fi]
        if lab != "base":
            # a warp facet's faces are "warp" cells (triangulated, smooth)
            cells.append("warp" if lab in warp else "facet")
            ramp_kind.append(None)
        else:
            cells.append("ramp" if np.any(delta[f] < vband[f] * (1.0 - 1e-9)) else "base")
            owners = {ramp_of[v] for v in f if ramp_of[v] is not None}
            ramp_kind.append(None if cells[-1] != "ramp" else
                             ("soft" if owners and all(ps.facets[o].get("soft") for o in owners) else "hard"))
    froll = []
    if ps.roll_mm > 0:
        ip = arr.interior_pts
        froll = list(((ps.y_top(ip[:, 0]) - ip[:, 1]) < ps.roll_mm) | ((ip[:, 1] - ps.y_bot(ip[:, 0])) < ps.roll_mm))
    else:
        froll = [False] * len(arr.faces)
    x_in = ps.inner(V2[:, 0])
    c_ch = chamfer_depth(ps, V2[:, 0], V2[:, 1])
    tmin = ps.options["t_min_mm"]
    roll_info = None
    if ps.roll_mm > 0:
        pre = S - c_ch - x_in
        k = int(np.argmin(pre))
        lim_h, at_h = roll_height_limit(ps)
        lim = min(float(pre[k]) - tmin, lim_h)
        roll_info = {"roll_mm": ps.roll_mm, "sub_bands": ROLL_BANDS, "limit_mm": lim,
                     "thickness_limit_mm": float(pre[k]) - tmin, "thickness_at": V2[k].tolist(),
                     "height_limit_mm": lim_h, "height_at_d": at_h,
                     "s_mm": roll_profile(ps.roll_mm)[0].tolist(), "depth_mm": roll_profile(ps.roll_mm)[1].tolist()}
        if ps.roll_mm > lim:
            raise TempleError("E_ROLL", f"roll_mm {ps.roll_mm:g} does not fit: the limit is {lim:.3f} mm (thinnest "
                              f"section {float(pre[k]):.3f} mm - t_min {tmin:g} at (d, y) ({V2[k, 0]:.2f}, "
                              f"{V2[k, 1]:.2f}); {lim_h:.3f} mm from the outline height at d {at_h:g})",
                              roll_mm=ps.roll_mm, limit_mm=lim, min_thickness_mm=float(pre[k]), at=V2[k].tolist(),
                              height_limit_mm=lim_h, reason="thickness" if lim < lim_h else "height")
    depth = c_ch + roll_depth(ps, V2[:, 0], V2[:, 1])
    Xo = S - depth
    thick = Xo - x_in
    if np.any(thick < tmin):
        k = int(np.argmin(thick))
        regions = sorted({labels[fi] for fi, f in enumerate(arr.faces) if k in f})
        fac = next((l for l in regions if l != "base"), "base")
        defining = [] if fac == "base" else sol.planes[fac].defining
        raise TempleError("E_TOO_THIN", f"thickness {thick[k]:.3f} mm < t_min {tmin:g} mm at (d, y) "
                          f"({V2[k, 0]:.3f}, {V2[k, 1]:.3f}) on {fac}", at=V2[k].tolist(), thickness_mm=float(thick[k]),
                          t_min_mm=tmin, facet=fac, regions=regions, surface_X=float(S[k]), depth_mm=float(depth[k]),
                          base_thickness_mm=float(B[k] - x_in[k]), defining_points=defining)
    V = np.zeros((2 * n, 3))
    V[:n, :2] = V2
    V[n:, :2] = V2
    V[:n, 2] = Xo
    V[n:, 2] = x_in
    polys, kind, region, pface, pcell, tris, tri_poly = _assemble(ps, arr, labels, chamf, froll, cells, V)
    normals, planarity, areas = _newell_all(V, polys)
    checks = _self_checks(V, polys, tris, tri_poly, kind, V2, planarity)
    if not checks["closed"]:
        raise TempleError("E_NOT_CLOSED", "the assembled solid is not a closed oriented 2-manifold",
                          open_edges=checks["open_edges"], duplicate_directed_edges=checks["duplicate_directed_edges"])
    if checks["euler"] != 2 or checks["volume_mm3"] <= 0:
        raise TempleError("E_NOT_CLOSED", f"Euler characteristic {checks['euler']} (expected 2), volume "
                          f"{checks['volume_mm3']:.3f} mm^3", euler=checks["euler"], volume_mm3=checks["volume_mm3"])
    if checks["planarity_max_mm"] > ps.options["planar_tol_mm"]:      # one tolerance: what planes.solve accepts
        k = int(np.argmax(planarity))
        raise TempleError("E_TRIANGULATE", f"polygon {k} ({kind[k]} {region[k]}) is {planarity[k]:.3g} mm off its "
                          f"plane", polygon=k, kind=kind[k], region=region[k], planarity_mm=float(planarity[k]))
    pramp = [ramp_kind[f] if f >= 0 else None for f in pface]
    sharp, edge_info, warnings = _sharp_edges(ps, sol, V, mesh_topology(polys), kind, region, pcell, normals, pramp)
    flat = [i for i, (k_, c_) in enumerate(zip(kind, pcell)) if k_ == "outer" and c_ == "facet"]
    tb = TempleBuild(spec=ps, arr=arr, sol=sol, face_label=labels, face_chamfer=chamf, face_roll=froll,
                     face_cell=cells, X=Xo, X_surface=S, thickness=thick, borders=prep.borders, stations=stations,
                     V=V, polys=polys, poly_kind=kind, poly_region=region, poly_face=pface, poly_cell=pcell, tris=tris,
                     tri_poly=tri_poly, sharp=sharp, flat_polys=flat, normals=normals, checks=checks, tables={},
                     warnings=list(sol.warnings) + warnings, face_ramp=ramp_kind)
    tb.tables = _tables(tb, edge_info, B, prep, roll_info, areas, unrefined)
    _check_creases(tb)
    return tb


def _assemble(ps, arr, labels, chamf, froll, cells, V):
    n = len(arr.V)
    V2 = arr.V
    polys, kind, region, pface, pcell = [], [], [], [], []
    tris, tri_poly = [], []
    for fi, f in enumerate(arr.faces):
        reg = labels[fi] + ("" if chamf[fi] is None else f"/chamfer{chamf[fi]}")
        k = "roll" if froll[fi] else "outer"
        et = arr.face_tris[fi]
        if cells[fi] == "facet":
            po = len(polys)
            polys.append(list(f))
            kind.append(k)
            region.append(reg)
            pface.append(fi)
            pcell.append("facet")
            for a, b, c in et:
                tris.append((a, b, c))
                tri_poly.append(po)
        else:
            for a, b, c in et:
                tri_poly.append(len(polys))
                tris.append((a, b, c))
                polys.append([a, b, c])
                kind.append(k)
                region.append(reg)
                pface.append(fi)
                pcell.append(cells[fi])
        pi = len(polys)
        polys.append([n + v for v in f[::-1]])
        kind.append("inner")
        region.append("inner")
        pface.append(fi)
        pcell.append("inner")
        for a, b, c in et:
            tris.append((n + a, n + c, n + b))
            tri_poly.append(pi)
    loop = arr.boundary_loop
    m = len(loop)
    caps = {"cap_hinge": ps.d0, "cap_tip": ps.d1}
    on_cap = [None] * m
    for i in range(m):
        u, v = loop[i], loop[(i + 1) % m]
        for nm, dc in caps.items():
            if V2[u, 0] == dc and V2[v, 0] == dc:
                on_cap[i] = nm
    for i in range(m):
        if on_cap[i] is not None:
            continue
        u, v = loop[i], loop[(i + 1) % m]
        k = "bottom" if V2[v, 0] > V2[u, 0] else "top"
        pid = len(polys)
        polys.append([u, n + u, n + v, v])
        kind.append(k)
        region.append(k)
        pface.append(-1)
        pcell.append(k)
        tris.append((u, n + u, n + v))
        tri_poly.append(pid)
        tris.append((u, n + v, v))
        tri_poly.append(pid)
    for nm in caps:
        idx = [i for i in range(m) if on_cap[i] == nm]
        if not idx:
            raise TempleError("E_TRIANGULATE", f"the outline has no {nm} edge")
        start = next(i for i in idx if on_cap[(i - 1) % m] != nm)
        chain = [loop[start]]
        i = start
        while on_cap[i % m] == nm:
            chain.append(loop[(i + 1) % m])
            i += 1
        cap = [chain[0]] + [n + v for v in chain] + chain[::-1][:-1]
        pid = len(polys)
        polys.append(cap)
        kind.append(nm)
        region.append(nm)
        pface.append(-1)
        pcell.append(nm)
        e1, e2 = (2, 1) if nm == "cap_hinge" else (1, 2)       # (X, y) for the -d cap, (y, X) for the +d cap
        P2 = V[cap][:, [e1, e2]]
        if poly_area(P2) <= 0:
            raise TempleError("E_TRIANGULATE", f"{nm} polygon is not counter-clockwise seen from outside")
        for a, b, cc in ear_clip(P2, list(range(len(cap)))):
            tris.append((cap[a], cap[b], cap[cc]))
            tri_poly.append(pid)
    return (polys, kind, region, pface, pcell, np.asarray(tris, np.int64), np.asarray(tri_poly, np.int64))


def _newell_all(V: np.ndarray, polys: list) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(unit Newell normal, planarity, area) of every polygon at once: Newell's sums by bincount over the polygons'
    directed edges; planarity = the largest distance of a polygon's vertices from the plane through their centroid
    along its normal (0 for triangles); area = half the Newell vector's length."""
    lens = np.fromiter((len(p) for p in polys), np.int64, len(polys))
    idx = np.fromiter((v for p in polys for v in p), np.int64, int(lens.sum()))
    starts = np.r_[0, np.cumsum(lens)[:-1]]
    nxt = np.arange(len(idx)) + 1
    nxt[starts + lens - 1] = starts
    P, Q = V[idx], V[idx[nxt]]
    pid = np.repeat(np.arange(len(polys)), lens)
    comp = ((P[:, 1] - Q[:, 1]) * (P[:, 2] + Q[:, 2]), (P[:, 2] - Q[:, 2]) * (P[:, 0] + Q[:, 0]),
            (P[:, 0] - Q[:, 0]) * (P[:, 1] + Q[:, 1]))
    n = np.stack([np.bincount(pid, weights=c, minlength=len(polys)) for c in comp], axis=1)
    L = np.linalg.norm(n, axis=1)
    n = np.where(L[:, None] > 0, n / np.where(L > 0, L, 1.0)[:, None], n)
    ctr = np.stack([np.bincount(pid, weights=P[:, k], minlength=len(polys)) for k in range(3)], axis=1) / lens[:, None]
    dist = np.abs(np.einsum("ij,ij->i", P - ctr[pid], n[pid]))
    plan = np.maximum.reduceat(dist, starts)
    plan[lens <= 3] = 0.0
    return n, plan, 0.5 * L


def points_in_poly(pts, P) -> np.ndarray:
    """arrange.point_in_poly for many points at once (the same even-odd rule, edge by edge)."""
    pts = np.asarray(pts, np.float64).reshape(-1, 2)
    P = np.asarray(P, np.float64)
    x, y = pts[:, 0][:, None], pts[:, 1][:, None]
    x0, y0 = P[:, 0][None], P[:, 1][None]
    x1, y1 = np.roll(P[:, 0], -1)[None], np.roll(P[:, 1], -1)[None]
    cond = (y0 > y) != (y1 > y)
    with np.errstate(divide="ignore", invalid="ignore"):
        xc = x0 + (y - y0) * (x1 - x0) / (y1 - y0)
    return (np.count_nonzero(cond & (xc > x), axis=1) % 2).astype(bool)


def mesh_topology(polys: list) -> dict:
    """Directed-edge bookkeeping of a polygon mesh: {'directed': {(a, b): poly}, 'dup': [...], 'open': [...]}."""
    directed, dup = {}, []
    for pi, p in enumerate(polys):
        for k in range(len(p)):
            e = (p[k], p[(k + 1) % len(p)])
            if e in directed:
                dup.append(e)
            directed[e] = pi
    opn = [e for e in directed if (e[1], e[0]) not in directed]
    return {"directed": directed, "dup": dup, "open": opn}


def _self_checks(V, polys, tris, tri_poly, kind, V2, planarity) -> dict:
    topo = mesh_topology(polys)
    und = {(min(a, b), max(a, b)) for a, b in topo["directed"]}
    used = sorted({v for p in polys for v in p})
    chi = len(used) - len(und) + len(polys)
    a, b, c = V[tris[:, 0]], V[tris[:, 1]], V[tris[:, 2]]
    vol = float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6.0)
    outer = np.array([k in OUTER_KINDS for k in kind])[tri_poly]
    t2 = tris[outer]
    q0, q1, q2 = V2[t2[:, 0]], V2[t2[:, 1]], V2[t2[:, 2]]
    ar2 = (q1[:, 0] - q0[:, 0]) * (q2[:, 1] - q0[:, 1]) - (q1[:, 1] - q0[:, 1]) * (q2[:, 0] - q0[:, 0])
    return {"closed": not topo["open"] and not topo["dup"], "open_edges": len(topo["open"]),
            "duplicate_directed_edges": len(topo["dup"]), "euler": int(chi), "volume_mm3": vol,
            "vertices": len(used), "edges": len(und), "polygons": len(polys), "triangles": int(len(tris)),
            "planarity_max_mm": float(np.max(planarity)), "min_outer_tri_area2_mm2": float(ar2.min()),
            "self_intersection": "none: a graph over a simple outline with positive thickness"
                                 if float(ar2.min()) > 0 else "outer triangle with non-positive (d, y) area"}


def _dihedral(n1, n2) -> float:
    return math.degrees(math.acos(max(-1.0, min(1.0, float(n1 @ n2)))))


def _sharp_edges(ps, sol, V, topo, kind, region, cell, normals, pramp=None):
    """Sharp edges (module docstring step 8) and the crease / chamfer edge records. `pramp` = per polygon its face's
    ramp kind (None | "soft" | "hard", TempleBuild.face_ramp)."""
    sharp, info, warnings = [], [], []
    kink = ps.options["kink_sharp_deg"]
    spread = 0.0
    D = topo["directed"]
    pramp = pramp or [None] * len(kind)
    edges = [(a, b, p1, D[(b, a)]) for (a, b), p1 in D.items() if a < b]
    P1 = np.fromiter((e[2] for e in edges), np.int64, len(edges))
    P2 = np.fromiter((e[3] for e in edges), np.int64, len(edges))
    dihs = np.degrees(np.arccos(np.clip(np.einsum("ij,ij->i", normals[P1], normals[P2]), -1.0, 1.0))).tolist()
    stands = _faded_creases_standing(ps, sol, V, edges, kind, region, cell)
    soft_border: dict = {}                  # soft facet -> [(a, b, built dihedral)]: decided per border run below
    for (a, b, p1, p2), dih in zip(edges, dihs):
        k1, k2 = kind[p1], kind[p2]
        s = False
        if k1 in OUTER_KINDS and k2 in OUTER_KINDS:
            l1, _, c1 = region[p1].partition("/")
            l2, _, c2 = region[p2].partition("/")
            if l1 != l2:                    # always; beside a warp cell while the crease stands
                info.append(("crease", _pair(l1, l2), a, b, p1, p2, dih))
                f = l2 if l1 == "base" else (l1 if l2 == "base" else None)
                if f is not None and ps.facets[f].get("soft"):
                    soft_border.setdefault(f, []).append((a, b, dih))     # a soft border: per run, below
                    continue
                s = "warp" not in (cell[p1], cell[p2]) or stands[(a, b)] or dih >= SHARP_DIHEDRAL_DEG
            elif "roll" in (k1, k2):
                s = False                                   # the rounded band: tangent sub-bands
            elif c1 != c2:
                s = dih >= SHARP_DIHEDRAL_DEG
                info.append(("chamfer", tuple(sorted((region[p1], region[p2]))), a, b, p1, p2, dih))
            elif {pramp[p1], pramp[p2]} - {None} == {"soft"}:
                s = dih >= SOFT_SHARP_DEG                   # inside a soft border's ramp (and where it meets the base)
            elif "ramp" in (cell[p1], cell[p2]):
                s = dih >= SHARP_DIHEDRAL_DEG
            elif c1 and dih >= SHARP_DIHEDRAL_DEG:          # a fold inside a chamfer (its taper ends, outline knots)
                s = True
            else:
                spread = max(spread, dih)
        elif k1 == "inner" and k2 == "inner":
            s = False
        elif {k1, k2} in ({"roll", "top"}, {"roll", "bottom"}):
            s = False                                       # the roll meets the strip tangentially
        elif k1 in OUTER_KINDS + ("inner",) or k2 in OUTER_KINDS + ("inner",):
            s = True
        else:                                               # strip / cap junctions
            s = dih > kink
        if s:
            sharp.append((a, b))
    # a soft border run (a connected chain of a soft facet's edges with the base) is sharp, all its edges, when half
    # its length folds by SOFT_SHARP_DEG or more (built dihedral, length-weighted median), else smooth: one line or
    # none (edge by edge, a fold straddling 25 deg drew a broken sharp line: review-builder-design F6; the median, not
    # the largest: one long ramp triangle at a band corner folds ~30 deg on a 0.5 mm step)
    for f, es in soft_border.items():
        dih_of = {(a, b): x for a, b, x in es}
        for run in _edge_chains([(a, b) for a, b, _ in es]):
            if _median_by_length(V, run, dih_of) >= SOFT_SHARP_DEG:
                sharp += run
    if spread > ARM_NORMAL_SPREAD_DEG:
        warnings.append({"code": "W_ARM_NORMAL_SPREAD", "deg": spread,
                         "message": f"smooth outer edges bend up to {spread:.1f} deg (> {ARM_NORMAL_SPREAD_DEG})"})
    return sorted(sharp), {"edges": info, "smooth_spread_deg": spread}, warnings


def design_normal(ps: ParsedSpec, sol, region: str, d, y, h: float = 1e-3) -> np.ndarray:
    """Unit outward normals (local d, y, X) of a region's design surface at the points (d, y): region_values minus
    the chamfer depth on a chamfer region ("<label>/chamfer<i>"); the roll is left out (tangent where it starts)."""
    lab, _, ch = region.partition("/")
    d, y = np.asarray(d, np.float64), np.asarray(y, np.float64)

    def X(dd, yy):
        x = region_values(ps, sol, lab, dd, yy)
        return x - chamfer_depth(ps, dd, yy) if ch else x
    if _warp_unfaded(ps, sol, lab):                 # its own gradient (corner gradients at the corners)
        gd, gy = sol.planes[lab].grad(d, y)
        if ch:
            gd = gd - (chamfer_depth(ps, d + h, y) - chamfer_depth(ps, d - h, y)) / (2 * h)
            gy = gy - (chamfer_depth(ps, d, y + h) - chamfer_depth(ps, d, y - h)) / (2 * h)
    else:
        gd = (X(d + h, y) - X(d - h, y)) / (2 * h)
        gy = (X(d, y + h) - X(d, y - h)) / (2 * h)
    n = np.stack([-gd, -gy, np.ones(np.broadcast(gd, gy).shape)], axis=-1)
    return n / np.linalg.norm(n, axis=-1, keepdims=True)


def _vertex_fans(tb, seeds: list) -> list:
    """[(vertex, [fan = [polygon ids]])] around each seed vertex (ascending): the polygons at it joined across its
    non-sharp edges (a smooth fan holds one shading normal)."""
    polys = tb.polys
    want = set(seeds)
    at_v: dict = {}
    for i, p in enumerate(polys):
        for k, v in enumerate(p):
            if v in want:
                at_v.setdefault(v, []).append((i, k))
    sharp = set(tb.sharp)
    out = []
    for v in seeds:
        items = at_v[v]
        root = {i: i for i, _ in items}
        edge: dict = {}
        for i, k in items:
            p = polys[i]
            for w in (p[k - 1], p[(k + 1) % len(p)]):
                edge.setdefault((min(v, w), max(v, w)), []).append(i)
        for e, ps_ in edge.items():
            if len(ps_) == 2 and e not in sharp:
                root[_find(root, ps_[0])] = _find(root, ps_[1])
        groups: dict = {}
        for i, _ in items:
            groups.setdefault(_find(root, i), []).append(i)
        out.append((v, list(groups.values())))
    return out


def design_corner_normals(tb) -> dict:
    """{(polygon, vertex): unit local normal} of every corner whose custom normal is a design surface's (the Blender
    layer writes them; zero elsewhere): the warp fans (a smooth fan of one warp region's outer / roll polygons around
    a vertex of its outer polygons: the warp's design normal at the vertex, its corner gradient's at a corner: WP6_V3
    §2, warp cells shade smooth from the design surface); the soft fans (a smooth fan around a vertex of a soft
    facet's outer polygons holding that facet's outer / roll polygons and the base's only: the facet's normal there, a
    flat polygon's face normal in the fan else its design normal, on every polygon of the fan, so the ramp leaves the
    facet tangentially in shading and Blender splits no fan). These reach the roll band only on its start line
    (tangent there): deeper roll corners stay auto-smoothed. Empty without warp or soft facets."""
    out = {}
    ps, sol = tb.spec, tb.sol
    warp = {lab for lab, pl in sol.planes.items() if pl.kind == "warp"}
    soft = {lab for lab, f in ps.facets.items() if f.get("soft")}
    if not warp and not soft:
        return out
    polys, kind, region, cell = tb.polys, tb.poly_kind, tb.poly_region, tb.poly_cell
    lab_of = [r.partition("/")[0] for r in region]
    # seeds: the vertices of OUTER polygons only: a fan reaches the roll band only on the roll's start line, where the
    # quarter arc is tangent; deeper roll corners stay auto-smoothed, so the rolled edge keeps its highlight (seeding
    # the roll polygons too gave every roll corner of a warp facet its un-rolled normal, up to 88.6 deg off:
    # review-builder-correctness P1-2)
    seeds = {v for i, p in enumerate(polys) if kind[i] == "outer" and lab_of[i] in warp | soft for v in p}
    flat = set(tb.flat_polys)
    jobs: dict = {}                                   # design-normal region -> [(vertex, fan)]
    for v, groups in _vertex_fans(tb, sorted(seeds)):
        for g in groups:
            if any((i, v) in out for i in g) or not all(kind[i] in OUTER_KINDS for i in g):
                continue
            labs = {lab_of[i] for i in g}
            if len(labs) == 1 and labs <= warp and len({region[i] for i in g}) == 1:
                jobs.setdefault(region[g[0]], []).append((v, g))
            elif len(labs) == 2 and "base" in labs and (labs - {"base"}) <= soft:
                own = [i for i in g if lab_of[i] != "base"]
                f = next((i for i in own if i in flat), None)
                if f is not None:
                    for i in g:
                        if i not in flat:
                            out[(i, v)] = tb.normals[f]
                else:
                    jobs.setdefault(region[own[0]], []).append((v, g))
    for reg, items in jobs.items():
        vs = [v for v, _ in items]
        N = design_normal(ps, sol, reg, tb.V[vs, 0], tb.V[vs, 1])
        for (v, g), n in zip(items, N):
            for i in g:
                if i not in flat:
                    out[(i, v)] = n
    return out


def _find(root: dict, a):
    while root[a] != a:
        root[a] = root[root[a]]
        a = root[a]
    return a


def _faded_creases_standing(ps, sol, V, edges, kind, region, cell) -> dict:
    """{(a, b): design dihedral >= CREASE_STANDS_DEG} for every crease edge (two different regions) with a faded or
    warp cell on either side, at the edge midpoint: the crease still stands there (module docstring step 8)."""
    groups: dict = {}
    for a, b, p1, p2 in edges:
        if kind[p1] in OUTER_KINDS and kind[p2] in OUTER_KINDS and ("faded" in (cell[p1], cell[p2])
                                                                    or "warp" in (cell[p1], cell[p2])):
            l1, l2 = region[p1].partition("/")[0], region[p2].partition("/")[0]
            if l1 != l2:
                groups.setdefault(_pair(l1, l2), []).append((a, b))
    out = {}
    for (r1, r2), es in groups.items():
        M = 0.5 * (V[[a for a, _ in es], :2] + V[[b for _, b in es], :2])
        dd = design_dihedral(ps, sol, r1, r2, M[:, 0], M[:, 1])
        out.update({e: bool(x >= CREASE_STANDS_DEG) for e, x in zip(es, dd)})
    return out


def _pair(l1: str, l2: str) -> tuple:
    if l1 == "base":
        return (l2, "base")
    if l2 == "base":
        return (l1, "base")
    return tuple(sorted((l1, l2)))


def base_normal(ps: ParsedSpec, d: float, y: float, h: float = 1e-3) -> np.ndarray:
    """Unit outward normal (local d, y, X) of the base surface at (d, y) (central differences)."""
    gd = (ps.x_base(d + h, y) - ps.x_base(d - h, y)) / (2 * h)
    gy = (ps.x_base(d, y + h) - ps.x_base(d, y - h)) / (2 * h)
    n = np.array([-gd, -gy, 1.0])
    return n / np.linalg.norm(n)


def _tables(tb: TempleBuild, edge_info: dict, B: np.ndarray, prep: Prep, roll_info, areas: np.ndarray,
            unrefined: bool = False) -> dict:
    ps, V, n = tb.spec, tb.V, tb.n2
    out = {}
    facets = []
    for label in sorted(ps.facets):
        pl = tb.sol.planes[label]
        ids = [i for i, (k, rg) in enumerate(zip(tb.poly_kind, tb.poly_region))
               if k in OUTER_KINDS and rg.split("/")[0] == label]
        area = float(areas[ids].sum())
        faces = [fi for fi, l in enumerate(tb.face_label) if l == label]
        A = np.array([tb.arr.areas[fi] for fi in faces])
        C = np.array([tb.arr.V[tb.arr.faces[fi]].mean(axis=0) for fi in faces])
        cen = (A[:, None] * C).sum(axis=0) / A.sum()
        nrm = np.array([-pl.coef[1], -pl.coef[2], 1.0])
        nrm /= np.linalg.norm(nrm)
        bn = base_normal(ps, float(cen[0]), float(cen[1]))
        plain_ids = [i for i in ids if tb.poly_kind[i] == "outer" and "/" not in tb.poly_region[i]
                     and tb.poly_cell[i] in ("facet", "warp")]
        vs = sorted({v for i in plain_ids for v in tb.polys[i]})
        rec = {"label": label, "area_mm2": area, "centroid": cen.tolist(), "normal_local": nrm.tolist(),
               "lean_deg": _dihedral(nrm, bn), "plane": {"kind": pl.kind, "X_coef": [float(x) for x in pl.coef]},
               "defining_points": pl.defining, "conditioning_deg_per_mm": pl.conditioning_deg_per_mm,
               "planarity_mm": float(np.max(np.abs(V[vs, 2] - pl.X(V[vs, 0], V[vs, 1])))) if vs else 0.0}
        if pl.kind == "warp":                         # lean and X_coef: its least-squares plane (display only)
            rec["warp_mm"] = pl.warp_mm
            rec["corners"] = [{"node": c[0], "d": c[1], "y": c[2], "X": c[3]} for c in pl.corners]
            if len(pl.corners) == 4:
                rec["corner_off"] = _corner_off(pl)
            rec["chord_mm"] = _chord_error(tb, label)
            if unrefined or rec["chord_mm"] > WARP_CHORD_WARN_MM:   # never silent (review-builder-design F7)
                tb.warnings.append({"code": "W_WARP_CHORD", "facet": label, "chord_mm": rec["chord_mm"],
                                    "unrefined": bool(unrefined),
                                    "message": f"warp facet {label}: its triangles lie up to {rec['chord_mm']:.3f} mm "
                                               f"off its surface (> {WARP_CHORD_WARN_MM:g})" if not unrefined else
                                               f"warp facet {label}: its refinement could not be built; its triangles "
                                               f"lie up to {rec['chord_mm']:.3f} mm off its surface"})
        if ps.facets[label].get("soft"):
            rec["soft"] = True
            rec["band_mm"] = band_of(ps, label)
        if pl.crease is not None:
            mid, n_in = np.array(pl.crease["mid"]), np.array(pl.crease["n_in"])
            h = 1e-3
            g = np.array([(ps.x_base(mid[0] + h, mid[1]) - ps.x_base(mid[0] - h, mid[1])) / (2 * h),
                          (ps.x_base(mid[0], mid[1] + h) - ps.x_base(mid[0], mid[1] - h)) / (2 * h)])
            rec["rise_per_mm"] = pl.crease["rise_per_mm"]
            rec["rise_rel_base_per_mm"] = pl.crease["rise_per_mm"] - float(g @ n_in)
            rec["crease_nodes"] = pl.crease["nodes"]
        facets.append(rec)
    out["facets"] = facets
    pairs = {}
    ramps = _border_ramps(tb, B)
    for (kind_, key, a, b, p1, p2, _d), (dih, ridge) in zip(edge_info["edges"], edge_folds(tb, edge_info["edges"])):
        rec = pairs.setdefault((kind_,) + tuple(key), {"kind": kind_, "regions": list(key), "length_mm": 0.0,
                                                        "dihedral_deg": [dih, dih], "ridge": 0.0, "valley": 0.0,
                                                        "_wsum": 0.0, "_fsum": 0.0, "_flen": 0.0,
                                                        "_fvote": [0.0, 0.0], "_range": None})
        L = float(np.linalg.norm(V[a] - V[b]))
        rec["length_mm"] += L
        rec["_wsum"] += L * dih
        rec["dihedral_deg"] = [min(rec["dihedral_deg"][0], dih), max(rec["dihedral_deg"][1], dih)]
        if face_edge(tb, p1, p2):                     # face to face: the roll and chamfer bands left out
            rec["_fsum"] += L * dih
            rec["_flen"] += L
            r = rec["_range"]
            rec["_range"] = [dih, dih] if r is None else [min(r[0], dih), max(r[1], dih)]
            if ridge is not None:
                rec["_fvote"][0 if ridge else 1] += L
        if ridge is not None:                         # a facet|base edge without a fold votes for neither
            rec["ridge" if ridge else "valley"] += L
    declared = {tuple(c["facets"]): c["shape"] for c in ps.creases}
    creases = []
    for rec in pairs.values():
        # the mean, the shape's vote and its shares over the edges the range covers (face to face), so the printed
        # mean lies inside the printed range and the word beside it is those edges' word: over every edge the roll
        # band's sub-band edges (their dihedral falls to ~0 at the outline) pulled run 7's crown|shoulder to 36.9
        # beside its face-to-face 37.7-39.3 (v3 probe1 §5.8), and a facet|base border running inside the band (a
        # ridge there) voted "ridge 4.9" over face-to-face edges that are one 11.3 deg valley and no fold, so a
        # declared ridge built and a declared valley was refused as a ridge (review2-builder2-1 P2, band_vote); every
        # edge only when the pair has no face-to-face edge (it lies in the roll or chamfer band alone: range_deg None,
        # face_length_mm 0). length_mm, ridge and valley stay every edge's (the chart draws every edge, each by its
        # own fold: draw.crease_edges)
        wsum, fsum, flen, fvote = rec.pop("_wsum"), rec.pop("_fsum"), rec.pop("_flen"), rec.pop("_fvote")
        face = flen > 0
        r_mm, v_mm, length = (fvote[0], fvote[1], flen) if face else (rec["ridge"], rec["valley"], rec["length_mm"])
        rec["face_length_mm"] = flen
        rec["mean_dihedral_deg"] = fsum / flen if face else (wsum / length if length > 0 else 0.0)
        if rec["mean_dihedral_deg"] < FLAT_CREASE_DEG or r_mm + v_mm <= 0.0:
            rec["shape"] = "flat"                     # (no edge with a fold: never a ridge by the tie)
        else:
            rec["shape"] = "ridge" if r_mm >= v_mm else "valley"
        rec["ridge_share"] = r_mm / length if length > 0 else 0.0
        rec["valley_share"] = v_mm / length if length > 0 else 0.0
        rng = rec.pop("_range")
        if rec["kind"] == "crease":
            rec["range_deg"] = rng                    # the per-edge angle's range face to face (None: roll only)
            rec["warp_beside"] = any(_warp_unfaded(ps, tb.sol, r) for r in rec["regions"])
            if rec["regions"][1] == "base":
                f = rec["regions"][0]
                if ps.facets[f].get("soft"):
                    rec["soft"] = True
                steep = [r for r in ramps if r["facet"] == f and r["slope_deg"] > BORDER_RAMP_WARN_DEG]
                if steep:                             # the step beside the fold (W_BORDER_RAMP's runs)
                    s = max(steep, key=lambda r: abs(r["height_mm"]))
                    rec["step"] = {"height_mm": s["height_mm"], "slope_deg": s["slope_deg"], "d_mm": s["d_mm"]}
            rec["declared"] = declared.get(tuple(rec["regions"]))
            # a facet|base pair with a step (its crease line states it: "flat 0.2° (proud 2.00 mm: a 45.0° ramp)")
            # is a plateau, not a crease that fails to read: no "bends only 0.18 deg" beside a 45 deg ramp; a soft
            # border is meant to read as no line (v3p3-review-astra P3-1: session2's steps 06-16 printed "bends only
            # 1.23 deg" beside "shoulder|base flat 1.2° soft", once soft had removed the step, read as a request for
            # more angle at a border the photos show soft)
            if (rec["shape"] == "flat" and rec["declared"] is None and not rec.get("step")
                    and not rec.get("soft")):
                tb.warnings.append({"code": "W_FLAT_CREASE", "regions": rec["regions"],
                                    "deg": rec["mean_dihedral_deg"],
                                    "message": f"crease {'|'.join(rec['regions'])} bends only "
                                               f"{deg_round(rec['mean_dihedral_deg'], 2):.2f} deg "
                                               f"(< {FLAT_CREASE_DEG})"})
        creases.append(rec)
    out["creases"] = sorted(creases, key=lambda r_: (r_["kind"], r_["regions"]))
    thick = tb.thickness
    k0, k1 = int(np.argmin(thick)), int(np.argmax(thick))
    out["thickness"] = {"min_mm": float(thick[k0]), "min_at": tb.arr.V[k0].tolist(),
                        "max_mm": float(thick[k1]), "max_at": tb.arr.V[k1].tolist()}
    out["border_ramps"] = ramps
    for r in out["border_ramps"]:
        if r["slope_deg"] > BORDER_RAMP_WARN_DEG:
            a, b = (f"{x:.1f}" for x in r["d_mm"])
            over = "its soft band" if ps.facets[r["facet"]].get("soft") else "border_band_mm"
            tb.warnings.append({"code": "W_BORDER_RAMP", "facet": r["facet"], "slope_deg": r["slope_deg"],
                                "height_mm": r["height_mm"], "d_mm": r["d_mm"],
                                "message": f"facet {r['facet']} stands {'proud of' if r['height_mm'] > 0 else 'below'} "
                                           f"the base by {abs(r['height_mm']):.2f} mm "
                                           f"{f'at d {a}' if a == b else f'along d {a}-{b}'}: a "
                                           f"{r['slope_deg']:.1f} deg ramp over {over} "
                                           f"{band_of(ps, r['facet']):g} (> {BORDER_RAMP_WARN_DEG})"})
    out["base_planarity"] = _base_planarity(tb)
    out["smooth_spread_deg"] = edge_info["smooth_spread_deg"]
    nodes = {}
    for name, (d, y, t) in ps.nodes.items():
        X = tb.sol.node_X.get(name)
        nodes[name] = {"d": d, "y": y, "given": t, "X": X,
                       "t": None if X is None else float(X - ps.inner(d))}
    out["nodes"] = nodes
    out["arrangement"] = {"vertices": int(n), "faces": len(tb.arr.faces), "stations": len(tb.stations),
                          "joined_chords": tb.arr.chords_added, "band_segments": len(prep.band),
                          "borders": len(prep.borders)}
    out["roll"] = roll_info
    out["snaps"] = ps.snaps
    return out


def _base_border_design(tb: TempleBuild, edges: list) -> dict:
    """{edge index: (fold deg, ridge)} of every facet|base crease edge of the build's edge records (WP6_V3 §2, the
    review's build.py:1254: the 2 mm ramp cell is the border band's, not the face against the arm): at the edge
    midpoint, with n the unit (d, y) normal of the edge pointing from the facet into the base, t its unit direction
    and g = region_grad (the facet's plane or warp and the base surface; chamfers, roll, the ramp and the
    triangulation aside), the fold ACROSS the border: the dihedral between the facet and the base surface given the
    facet's slope along the border (g_base' = g_base + ((g_facet - g_base) . t) t: the two then share the border's 3D
    line, as two facets share a crease, so the number is a facet|facet dihedral's kind; with no slope s along it,
    |atan(g_facet . n) - atan(g_base . n)|, WP6_V3 §3's section angle and the fit's deg, else about that over
    sqrt(1 + s^2), as a facet|facet dihedral against the fit's section angle: run 7's shoulder|base 2.6 here, 2.9 in
    the fit, lower|shoulder 21.3 and 21.6); ridge <=> (g_facet - g_base) . n > 0 (the base falls away below the
    facet's surface carried across the border), None when |(g_facet - g_base) . n| <= FOLD_TIE (no fold there: the
    edge votes for neither shape). Not the angle between the two design normals as they are (v3's first build): that
    also counts their slope difference ALONG the border, which is the step height changing along it, not a fold (a
    facet tilted 0.1 along its border with no fold read "ridge 5.7 deg"; run 7's shoulder|base 19.2 deg against a
    fold of 2.9: review-builder-design F1). Where the facet meets the base without a step the two agree."""
    ps, sol, V, region = tb.spec, tb.sol, tb.V, tb.poly_region
    by_facet: dict = {}
    for k, (kind_, key, *_rest) in enumerate(edges):
        if kind_ == "crease" and key[1] == "base":
            by_facet.setdefault(key[0], []).append(k)
    out = {}
    for lab, ks in by_facet.items():
        A = np.array([V[edges[k][2], :2] for k in ks])
        Bp = np.array([V[edges[k][3], :2] for k in ks])
        M = 0.5 * (A + Bp)
        t = (Bp - A) / np.linalg.norm(Bp - A, axis=1, keepdims=True)
        nl = np.stack([-t[:, 1], t[:, 0]], axis=1)                     # left of a -> b: into the polygon p1
        p1_facet = np.array([region[edges[k][4]].partition("/")[0] == lab for k in ks])
        n = np.where(p1_facet[:, None], -nl, nl)
        gf = np.stack(region_grad(ps, sol, lab, M[:, 0], M[:, 1]), axis=1)
        gb = np.stack(region_grad(ps, sol, "base", M[:, 0], M[:, 1]), axis=1)
        gb = gb + np.einsum("ij,ij->i", gf - gb, t)[:, None] * t      # the facet's slope along the border
        fold = np.einsum("ij,ij->i", gf - gb, n)
        nf = np.concatenate([-gf, np.ones((len(ks), 1))], axis=1)
        nb = np.concatenate([-gb, np.ones((len(ks), 1))], axis=1)
        nf /= np.linalg.norm(nf, axis=1, keepdims=True)
        nb /= np.linalg.norm(nb, axis=1, keepdims=True)
        deg = np.degrees(np.arctan2(np.linalg.norm(np.cross(nf, nb), axis=1), np.einsum("ij,ij->i", nf, nb)))
        out.update({k: (float(x), None if abs(f) <= FOLD_TIE else bool(f > 0)) for k, x, f in zip(ks, deg, fold)})
    return out


FOLD_TIE = 1e-6                   # a facet|base slope difference across the border (mm/mm; 6e-5 deg) this small is no
#                                   fold: above the warp gradient's float noise (central differences, h 1e-5: up to
#                                   2.3e-9; at 1e-9 a coplanar warp cast random votes, review-builder-correctness P2-4)


def edge_folds(tb: TempleBuild, edges: list) -> list:
    """Per edge record (kind, pair, a, b, p1, p2, built dihedral) of _sharp_edges / crease_edges: (deg, ridge), the
    core's one per-edge rule that the crease tables, the chart and the section strip share: a facet|base edge its
    fold across the border against the BASE surface (_base_border_design; ridge None when it has no fold), any other
    its built dihedral, a ridge when the second polygon's centroid lies below the first one's plane."""
    design = _base_border_design(tb, edges)
    out = []
    for k, (_kind, _key, a, b, p1, p2, dih) in enumerate(edges):
        if k in design:
            out.append(design[k])
        else:
            cen2 = tb.V[tb.polys[p2]].mean(axis=0)
            out.append((dih, float(tb.normals[p1] @ (cen2 - 0.5 * (tb.V[a] + tb.V[b]))) < 0))
    return out


def crease_edges(tb: TempleBuild) -> list:
    """[(a, b, pair, deg, ridge, p1, p2)] of every edge between outer / roll polygons of two different regions (labels
    before '/'; a < b vertex ids of tb.V, p1 the polygon holding the directed edge a -> b, pair as the crease tables
    name it), deg and ridge by edge_folds (the tables' rule: draw.crease_edges colours by it)."""
    topo = mesh_topology(tb.polys)
    recs = []
    for (a, b), p1 in topo["directed"].items():
        p2 = topo["directed"].get((b, a))
        if a > b or p2 is None or tb.poly_kind[p1] not in OUTER_KINDS or tb.poly_kind[p2] not in OUTER_KINDS:
            continue
        l1, l2 = tb.poly_region[p1].partition("/")[0], tb.poly_region[p2].partition("/")[0]
        if l1 != l2:
            recs.append(("crease", _pair(l1, l2), a, b, p1, p2, _dihedral(tb.normals[p1], tb.normals[p2])))
    return [(a, b, key, deg, ridge, p1, p2) for (_k, key, a, b, p1, p2, _d), (deg, ridge)
            in zip(recs, edge_folds(tb, recs))]


def face_edge(tb: TempleBuild, p1: int, p2: int) -> bool:
    """An edge between two plain outer polygons: neither in the roll band (poly_kind "roll") nor in a chamfer band
    (region "<label>/chamfer<i>"). A crease's built dihedral changes across those bands (the roll's sub-bands fold
    it down to ~0 at the outline), so the crease tables' mean and range_deg and model_edges count these edges only."""
    return (tb.poly_kind[p1] == "outer" and tb.poly_kind[p2] == "outer" and "/" not in tb.poly_region[p1]
            and "/" not in tb.poly_region[p2])


MODEL_EDGE_MM_DIGITS = 6          # model_edges: points rounded to 1e-6 mm (seeing's on-model check is 0.05 mm) ...
MODEL_EDGE_DEG_DIGITS = 3         # ... angles to 0.001 deg (deg_round: monotone, so deg stays inside its range, and
#                                   never across FLAT_CREASE_DEG, so the rounded deg keeps the unrounded one's shape)


def _run_paths(edges: list, V2: np.ndarray) -> list:
    """The edges [(a, b)] of one pair cut into runs: [(vertices, edge indices, closed)], each a path between two
    vertices of degree != 2 (an end, or a branch where 3+ of the pair's edges meet), or a closed loop (a facet
    whose whole border is this one pair). Deterministic: an open run starts at its end of smaller (d, y), a loop at
    its vertex of smaller (d, y) and runs counter-clockwise in (d, y)."""
    adj: dict = {}
    for k, (a, b) in enumerate(edges):
        adj.setdefault(a, []).append(k)
        adj.setdefault(b, []).append(k)

    def key(v):
        return (float(V2[v, 0]), float(V2[v, 1]), v)

    def other(k, v):
        return edges[k][1] if edges[k][0] == v else edges[k][0]
    used = [False] * len(edges)
    runs = []
    for v0 in sorted((v for v in adj if len(adj[v]) != 2), key=key):
        for k0 in sorted(adj[v0], key=lambda k: key(other(k, v0))):
            if used[k0]:
                continue
            path, ks, v, k = [v0], [], v0, k0
            while k is not None:
                used[k] = True
                ks.append(k)
                v = other(k, v)
                path.append(v)
                k = next((j for j in adj[v] if not used[j]), None) if len(adj[v]) == 2 else None
            if key(path[-1]) < key(path[0]):
                path, ks = path[::-1], ks[::-1]
            runs.append((path, ks, False))
    for k0 in range(len(edges)):                      # what is left: loops, every vertex of degree 2
        if used[k0]:
            continue
        path, ks, v, k = [edges[k0][0]], [], edges[k0][0], k0
        while k is not None:
            used[k] = True
            ks.append(k)
            v = other(k, v)
            path.append(v)
            k = next((j for j in adj[v] if not used[j]), None)
        loop, kl = path[:-1], ks
        i = min(range(len(loop)), key=lambda j: key(loop[j]))
        loop, kl = loop[i:] + loop[:i], kl[i:] + kl[:i]
        P = V2[loop]
        if float(np.sum(P[:, 0] * np.roll(P[:, 1], -1) - np.roll(P[:, 0], -1) * P[:, 1])) < 0:
            loop, kl = [loop[0]] + loop[1:][::-1], kl[::-1]
        runs.append((loop + [loop[0]], kl, True))
    return runs


def model_edges(tb: TempleBuild) -> list:
    """The build's creases as the compares draw them (WP6_V3 §5, interface I1): one entry per connected run of the
    edges between two different regions (crease_edges), face to face only (face_edge: the roll and chamfer bands
    left out; edges inside a ramp or a warp are no creases: one region on both sides):
        pair     "F1|F2" as the crease tables name it (facets sorted, "base" last)
        shape    "ridge" | "valley" | "flat": the sign of deg, "flat" below FLAT_CREASE_DEG either way (for a run
                 whose edges all fold one way this is the tables' rule; on a mixed run the signed mean decides, so
                 shape and deg never contradict each other), decided on the unrounded angle (a run at 1.9997 is
                 flat, as the tables say, and its deg reads 1.999, never 2.0: deg_round)
        deg      the run's length-weighted signed angle over its own edges (+ ridge, - valley; per edge the
                 tables' rule, edge_folds: a facet|facet edge its built dihedral, a facet|base edge its fold
                 across the border against the base surface, 0 for an edge without a fold)
        range    [lo, hi] the signed per-edge angle's range over the same edges (deg lies inside it)
        deg_at   per point: the signed angle there (an end: its edge's; inside: the mean of the two edges at it;
                 inside the range)
        pts      [[d, y, X], ...] the run's vertices in the +X temple's chart (tb.V: outer surface, opening 0),
                 ordered along the crease; a closed loop repeats its first point at the end
        soft     a facet|base run of a soft facet that the export shades smooth: none of its edges is in tb.sharp.
                 A soft border run whose built dihedral's length-weighted median is >= SOFT_SHARP_DEG is built sharp,
                 its whole chain at once (_sharp_edges), and is then a crease line like any other: the compares dash
                 a soft run grey and never label it, which a crisp exported edge is not (review2-builder2-1 P3:
                 soft_spec(4.5)'s S|base run was flagged soft with every edge sharp, built at 33-76 deg)
    Sorted by pair, then by the run's first point. Points are rounded to 1e-6 mm, angles to 0.001 deg (deg_round:
    non-decreasing and never across FLAT_CREASE_DEG, so deg and deg_at stay inside the rounded range)."""
    V2 = tb.arr.V
    by_pair: dict = {}
    for a, b, pair, deg, ridge, p1, p2 in crease_edges(tb):
        if face_edge(tb, p1, p2):
            by_pair.setdefault(tuple(pair), []).append((a, b, 0.0 if ridge is None else (deg if ridge else -deg)))
    sharp = set(tb.sharp)
    nd = MODEL_EDGE_DEG_DIGITS
    out = []
    for pair in sorted(by_pair):
        es = by_pair[pair]
        soft_key = pair[1] == "base" and bool(tb.spec.facets[pair[0]].get("soft"))
        runs = _run_paths([(a, b) for a, b, _ in es], V2)
        for path, ks, closed in sorted(runs, key=lambda r: (float(V2[r[0][0], 0]), float(V2[r[0][0], 1]), r[0][0],
                                                            r[0][1])):
            s = np.array([es[k][2] for k in ks])
            L = np.linalg.norm(tb.V[path[1:]] - tb.V[path[:-1]], axis=1)
            deg = float(L @ s / L.sum()) if L.sum() > 0 else float(s.mean())
            deg = min(max(deg, float(s.min())), float(s.max()))         # float rounding never leaves the range
            ends = [0.5 * (s[-1] + s[0])] * 2 if closed else [s[0], s[-1]]
            at = np.concatenate([[ends[0]], 0.5 * (s[:-1] + s[1:]), [ends[1]]])
            shape = "ridge" if deg >= FLAT_CREASE_DEG else ("valley" if deg <= -FLAT_CREASE_DEG else "flat")
            soft = soft_key and not any((es[k][0], es[k][1]) in sharp for k in ks)
            out.append({"pair": "|".join(pair), "shape": shape, "deg": deg_round(deg, nd),
                        "range": [deg_round(s.min(), nd), deg_round(s.max(), nd)],
                        "deg_at": [deg_round(x, nd) for x in at],
                        "pts": [[_rd(x, MODEL_EDGE_MM_DIGITS) for x in tb.V[v]] for v in path], "soft": soft})
    return out


def _rd(x, nd: int = MODEL_EDGE_DEG_DIGITS) -> float:
    return round(float(x), nd) + 0.0                  # + 0.0: no "-0.0"


def deg_round(x, nd: int = 1) -> float:
    """An angle (deg, either sign) rounded to nd digits that stays on its side of FLAT_CREASE_DEG: a magnitude below
    it never rounds up to it but stops one unit of the last digit short (1.9997 -> 1.999 at 3 digits, 1.97 -> 1.9 at
    1), so the rounded angle has the unrounded one's shape (review2-builder2-1 P3: L|R at 1.9997 read "flat 2.0°" in
    its crease line and "valley -2.0" in model_edges; package_007 r0004 logo|nose 1.998 read "flat 2.0°").
    Non-decreasing in x, so a value inside [lo, hi] stays inside [deg_round(lo), deg_round(hi)]: the crease lines'
    mean and range (report.crease_line) and model_edges' deg, deg_at and range keep the containment they have
    unrounded; W_FLAT_CREASE's "bends only 1.99 deg (< 2.0)" (it printed "2.00" at 1.9997). Elsewhere it is
    round(x, nd), so every other printed angle is unchanged; no "-0.0"."""
    x = float(x)
    r = round(x, nd)
    if abs(x) < FLAT_CREASE_DEG <= abs(r):
        r = math.copysign(round(FLAT_CREASE_DEG - 10.0 ** -nd, nd), x)
    return r + 0.0


def flat_cut(x, nd: int = 2) -> float:
    """A flat crease's angle (|x| < FLAT_CREASE_DEG) as E_CREASE_SHAPE states it with nd digits: cut toward zero, not
    rounded ("is flat 0.68 deg", "flat 1.99", never "flat 2.00" < 2.0; the refusal's rule since phase 1, named here).
    The cut adds 1e-6 of the last digit first (0.29 is 0.28999... in binary and prints 0.29, not 0.28), and a
    magnitude below the flat line stays at least one unit of the last digit below it."""
    x = float(x)
    k = 10.0 ** nd
    v = math.floor(abs(x) * k + 1e-6) / k
    if abs(x) < FLAT_CREASE_DEG:
        v = min(v, round(FLAT_CREASE_DEG - 1.0 / k, nd))
    return math.copysign(v, x) + 0.0


def _border_ramps(tb: TempleBuild, B: np.ndarray) -> list:
    """Per border run (a connected chain of final arrangement edges between one facet and the base): its d range, the
    ramp height = the signed facet - base mismatch of largest size on it (+ = the facet stands proud of the base,
    - = sunk below it), where, the ramp slope over border_band_mm and the run length. Sorted by facet, then d."""
    ps = tb.spec
    V2 = tb.arr.V
    ef = tb.arr.edge_faces()
    edges: dict = {}
    for (a, b), f1 in ef.items():
        f2 = ef.get((b, a))
        if f2 is not None and tb.face_label[f1] != "base" and tb.face_label[f2] == "base":
            edges.setdefault(tb.face_label[f1], []).append((a, b))
    out = []
    for lab in sorted(edges):
        for es in _edge_chains(edges[lab]):
            vs = sorted({v for e in es for v in e})
            m = tb.X_surface[vs] - B[vs]
            k = int(np.argmax(np.abs(m)))
            h = float(m[k])
            out.append({"facet": lab, "d_mm": [float(V2[vs, 0].min()), float(V2[vs, 0].max())], "height_mm": h,
                        "at": V2[vs[k]].tolist(), "slope_deg": math.degrees(math.atan2(abs(h), band_of(ps, lab))),
                        "length_mm": float(sum(np.hypot(*(V2[a] - V2[b])) for a, b in es))})
    return sorted(out, key=lambda r: (r["facet"], r["d_mm"][0], r["d_mm"][1]))


def _median_by_length(V: np.ndarray, edges: list, value: dict) -> float:
    """The length-weighted median of value[(a, b)] over the edges (a, b) of V: the value half their length reaches."""
    x = np.array([value[e] for e in edges])
    L = np.array([float(np.linalg.norm(V[a] - V[b])) for a, b in edges])
    o = np.argsort(x, kind="stable")
    return float(x[o][np.searchsorted(np.cumsum(L[o]), 0.5 * L.sum())])


def _edge_chains(edges: list) -> list:
    """The edges grouped into connected components (edges sharing a vertex), by union-find."""
    root: dict = {}

    def find(v):
        while root.setdefault(v, v) != v:
            root[v] = root[root[v]]
            v = root[v]
        return v
    for a, b in edges:
        root[find(a)] = find(b)
    out: dict = {}
    for a, b in edges:
        out.setdefault(find(a), []).append((a, b))
    return list(out.values())


def _corner_off(pl) -> dict | None:
    """A 4-corner warp facet's corner offset as the fit's W_FACE_NOT_FLAT names it: per corner its X minus the plane
    of the other three there, and of those the one the others' plane predicts best (the smallest size: every
    leave-one-out is the same misfit in sigma, and a corner beside a near-collinear three is extrapolated, H 180 mm
    off the plane of A, K, E): {"node", "mm", "plane_of"}. Run 7's crown: K +1.52 mm from the plane of A, E, H, where
    warp_mm (the largest residual from the plane of all four) reads 0.99 (review-builder-design F8). None with more
    corners (no one three-corner measure: warp_mm alone)."""
    if len(pl.corners) != 4:
        return None
    M = np.array([[1.0, c[1], c[2]] for c in pl.corners])
    F = np.array([c[3] for c in pl.corners])
    best = None
    for k, c in enumerate(pl.corners):
        keep = np.arange(len(F)) != k
        mm = float(F[k] - M[k] @ np.linalg.lstsq(M[keep], F[keep], rcond=None)[0])
        if best is None or abs(mm) < abs(best["mm"]):
            best = {"node": c[0], "mm": mm, "plane_of": [x[0] for j, x in enumerate(pl.corners) if j != k]}
    return best


def _chord_error(tb: TempleBuild, label: str) -> float:
    """The largest distance (along X) between a warp facet's triangles (their vertices' design-surface values,
    X_surface) and its surface X() inside them: CHORD_SAMPLES barycentric samples per side of every outer / roll
    triangle of its warp cells (information; the refinement's target is WARP_CHORD_MM, warp_segments)."""
    k = CHORD_SAMPLES
    bc = np.array([[i, j, k - i - j] for i in range(k + 1) for j in range(k + 1 - i)], np.float64) / k
    T = np.array([tb.polys[i] for i, (kd, c, r) in enumerate(zip(tb.poly_kind, tb.poly_cell, tb.poly_region))
                  if kd in OUTER_KINDS and c == "warp" and r.partition("/")[0] == label], np.int64).reshape(-1, 3)
    if not len(T):
        return 0.0
    P = np.einsum("sk,tkj->tsj", bc, tb.arr.V[T])                     # (t, samples, 2)
    lin = np.einsum("sk,tk->ts", bc, tb.X_surface[T])
    return float(np.max(np.abs(lin - tb.sol.planes[label].X(P[..., 0], P[..., 1]))))


def _base_planarity(tb: TempleBuild) -> dict:
    """Information: how far each triangulated (base, ramp) cell's outer vertices are from one plane (its Newell plane
    through the centroid)."""
    faces = [f for fi, f in enumerate(tb.arr.faces) if tb.face_cell[fi] in ("base", "ramp") and len(f) > 3]
    if not faces:
        return {"max_mm": 0.0, "at": None, "cells": 0}
    _, plan, _ = _newell_all(tb.V, faces)
    k = int(np.argmax(plan))
    return {"max_mm": float(plan[k]), "at": tb.arr.V[faces[k]].mean(axis=0).tolist(), "cells": len(faces)}


STRAIGHT_NODE_DEG = 2.0           # E_CREASE_SHAPE hint: a facet's corner at a node within this of straight, or reflex
NODE_AT_MM = 1e-6                 # a node is the arrangement vertex within this distance of it


def _node_regions(arr: Arrangement, labels: list, v: int, corners: list) -> list:
    """[[label, angle_deg]] of the regions around the interior arrangement vertex v, counter-clockwise: each face's
    corner angle at v (faces are CCW, so a corner runs CCW from the outgoing edge to the incoming one), consecutive
    faces of one label merged (stations and band lines cut one region into several faces). [] when the corners do not
    close around v."""
    V = arr.V
    items = []
    for fi, k in corners:
        cyc = arr.faces[fi]
        o, i = V[cyc[(k + 1) % len(cyc)]] - V[v], V[cyc[k - 1]] - V[v]
        a0 = math.atan2(o[1], o[0])
        items.append((a0, math.degrees((math.atan2(i[1], i[0]) - a0) % (2.0 * math.pi)), labels[fi]))
    if abs(sum(s for _, s, _ in items) - 360.0) > 1e-6:
        return []
    regions = []
    for _, s, lab in sorted(items):
        if regions and regions[-1][0] == lab:
            regions[-1][1] += s
        else:
            regions.append([lab, s])
    if len(regions) > 1 and regions[0][0] == regions[-1][0]:
        regions[0][1] += regions.pop()[1]
    return regions


def _straight_node(tb: TempleBuild, bad: list) -> dict | None:
    """The first node (by name) strictly inside the outline where three or more creases meet, every one of them is
    declared and all with one shape, one of them is refused (bad), and a facet's corner there is straight within
    STRAIGHT_NODE_DEG or reflex. Such a node cannot be built as declared: the surface around it would be convex (all
    ridges) or concave (all valleys), and a convex corner whose facet holds a straight line through it is a prism along
    that line, so the facets on the other side are coplanar (flat creases, which a declaration refuses); a reflex corner
    makes them all coplanar with the facet. One crease there must have the other shape. None when no node qualifies."""
    ps, arr = tb.spec, tb.arr
    declared = {tuple(c["facets"]): c["shape"] for c in ps.creases}
    refused = [tuple(b["facets"]) for b in bad]
    outline = set(arr.boundary_loop)
    at_v: dict = {}
    for fi, cyc in enumerate(arr.faces):
        for k, u in enumerate(cyc):
            at_v.setdefault(u, []).append((fi, k))
    for name in sorted(ps.nodes):
        p = np.asarray(ps.nodes[name][:2], np.float64)
        dist = np.hypot(arr.V[:, 0] - p[0], arr.V[:, 1] - p[1])
        v = int(np.argmin(dist))
        if dist[v] > NODE_AT_MM or v in outline or v not in at_v:
            continue
        regions = _node_regions(arr, tb.face_label, v, at_v[v])
        n = len(regions)
        if n < 3:
            continue
        pairs = list(dict.fromkeys(_pair(regions[k][0], regions[(k + 1) % n][0]) for k in range(n)))
        shapes = {declared.get(pr) for pr in pairs}
        hit = next((pr for pr in refused if pr in pairs), None)
        if None in shapes or len(shapes) != 1 or hit is None:
            continue
        lab, ang = max(((lab, ang) for lab, ang in regions if lab != "base"), key=lambda r: r[1], default=(None, 0.0))
        if lab is not None and ang >= 180.0 - STRAIGHT_NODE_DEG:
            return {"node": name, "facet": lab, "angle_deg": ang, "shape": shapes.pop(),
                    "creases": [list(pr) for pr in sorted(pairs)], "refused": list(hit)}
    return None


def _check_creases(tb: TempleBuild) -> None:
    """E_CREASE_SHAPE for every declared pair that shares no edge inside the outline, is flat (< 2 deg) or is built
    with the other shape. Facts only (WP6_V3 P6: a refusal states what is so and offers no remedy; "fix the node
    values, do not redeclare" was one, FINDINGS §3): what was declared, what was built (a facet|base pair: the face
    against the arm, its fold across the border, and the step beside it when W_BORDER_RAMP reports one), how much of
    its length. At a node that cannot be built as declared (_straight_node: a facet runs straight through it or bends
    back there, and every crease meeting there is declared with one shape) the message leads with a refused crease
    there and states why, with both ways it can be built: one of those creases has the other shape, or two of those
    faces are one."""
    built = {tuple(r["regions"]): r for r in tb.tables["creases"] if r["kind"] == "crease"}
    bad = []
    for c in tb.spec.creases:
        key = tuple(c["facets"])
        r = built.get(key)
        if r is None:
            bad.append({"facets": list(key), "declared": c["shape"], "built": "none", "dihedral_deg": None,
                        "why": "the pair shares no edge inside the outline"})
        elif r["shape"] != c["shape"]:
            # the share and the length over the edges the shape and the angle come from (face to face; every edge
            # only for a pair inside the roll or chamfer band alone: _tables), never the band's
            share = r["ridge_share"] if r["shape"] == "ridge" else r["valley_share"]
            why = ("flat (< 2 deg): no ridge or valley" if r["shape"] == "flat"
                   else f"a {r['shape']} along {share:.0%} of its {r['face_length_mm'] or r['length_mm']:.1f} mm")
            if key[1] == "base" and r.get("step"):
                st = r["step"]
                why += (f"; the facet stands {'proud' if st['height_mm'] > 0 else 'sunk'} "
                        f"{abs(st['height_mm']):.2f} mm there: a {st['slope_deg']:.1f} deg ramp")
            bad.append({"facets": list(key), "declared": c["shape"], "built": r["shape"],
                        "dihedral_deg": r["mean_dihedral_deg"], "ridge_share": r["ridge_share"], "why": why})
    if bad:
        node = _straight_node(tb, bad)
        b = bad[0] if node is None else next(x for x in bad if x["facets"] == node["refused"])
        dih = "" if b["dihedral_deg"] is None else (f" {b['dihedral_deg']:.1f} deg" if b["built"] != "flat" else
                                                     f" {flat_cut(b['dihedral_deg']):.2f} deg")
        #                                             (flat: cut, not rounded: "flat 1.99", never "flat 2.0" < 2)
        extra, tail = {}, ""
        if node is not None:
            x, c, shape = node["facet"], node["node"], node["shape"]
            how = (f"runs straight through node {c}" if node["angle_deg"] <= 180.0 + STRAIGHT_NODE_DEG
                   else f"has a {node['angle_deg']:.0f} deg corner at node {c}")
            tail = (f"; facet {x} {how}, so the {len(node['creases'])} creases there cannot all be {shape}s: one is a "
                    f"{'valley' if shape == 'ridge' else 'ridge'} or two of those faces are one")
            extra["straight_node"] = node
        what = ("the face against the arm is" if b["facets"][1] == "base" and b["built"] != "none" else "built")
        raise TempleError("E_CREASE_SHAPE", f"crease {'|'.join(b['facets'])} is declared {b['declared']} but {what} "
                          f"{b['built']}{dih} ({b['why']}){tail}",
                          facets=b["facets"], declared=b["declared"], built=b["built"], dihedral_deg=b["dihedral_deg"],
                          failures=bad, **extra)

