"""Build the faceted temple solid, g3d.temple/2 (plan/WP6_FACETS.md §2.2, §2.5 core roll). numpy only, float64,
deterministic.

The solid is {y_bot(d) <= y <= y_top(d), x_in(d) <= X <= S(d, y) - c(d, y)}: S = the outer surface (a facet plane,
a faded facet, a border-band ramp or the base), c = the chamfer depth plus the roll depth.

Steps:
 1. facet-only arrangement of the outline (A0): facet overlap / outside checks, the facet/base borders, the roll
    proximity check (E_ROLL), the border band: a line border_band_mm into the base parallel to every facet/base border
    (mitred where the base is convex, bevelled where it is reflex, closed onto the outline or a chord at a run end),
    clipped to the base;
 2. stations (full-height chords) at every table knot, node d, facet-edge/outline crossing, fade d0 / d1, chamfer
    break and border-band end, subdivided to <= station_step_mm (inside a fade to <= min(step, (d1 - d0) / 6)); kept
    with a roll;
 3. the full arrangement: outline, facet edges, stations, chamfer lines, the 8 roll lines per outline edge and the
    border band; every face is labelled (facet or base, chamfer, roll band);
 4. planes (planes.py), then vertex values: a facet vertex takes its facet (faded: (1 - s) plane + s base, s = the
    fade weight); a vertex shared by facets takes the facet with the smallest fade weight there (ties: the first
    label; their planes agree there, planes.py), so a crease stays where the less-faded facet still stands and a faded
    neighbour blends away from it (one value per vertex: continuous, never a step); a base vertex at distance
    delta < band from a border takes base + (1 - delta / band) x the mismatch at the nearest border point (the ramp),
    else the base. Facets with different fades may share creases and vertices; a less-faded neighbour then holds the
    faded facet at its own height on their shared vertices and the drop to the fade happens inside the first row of
    triangles, with no band (checked in step 7);
 5. depths: chamfers (v1) and the core roll: r = roll_mm as 8 stacked chamfer sub-bands along the top and bottom
    outline edges with depth c(s) = r - sqrt(r^2 - (r - s)^2) at equal arc angles (s = vertical offset from the edge),
    so the strip's outer edge moves in to X - r and the side silhouette is unchanged. Limit:
    r <= min(min thickness - t_min, 0.45 x outline height - chamfer widths), else E_ROLL;
 6. cells and polygons: a facet cell is a face of a facet whose vertices all have fade weight 0 (every face of a
    non-faded facet; the faces of a faded facet before its fade starts, stations sit at d0 so no face straddles it);
    a faded cell is a face of a faded facet with any vertex weight > 0 (a face entirely at weight 1 is base-valued and
    stays a faded cell: triangulated and smooth like the base, keeping its label for the crease tables). Facet cells
    (chamfered or rolled too) are planar n-gons; every other outer cell (base, faded, ramp) is the arrangement's
    ear-clip triangles, never an n-gon, so Blender and the GLB share the core's triangulation; inner polygons
    (planar: x_in is linear between stations), one planar quad per outline edge (top / bottom strips), a planar
    hinge-end cap and tip cap;
 7. self-checks: closed oriented 2-manifold, Euler 2, volume > 0, every polygon planar (<= 1e-9 mm), thickness >=
    t_min (E_TOO_THIN), outer triangles with positive (d, y) area (no self-intersection); the fade hold, over every
    faded outer and roll triangle: a vertex's hold = S minus the faded facet's own value (facet_values), the tilt = the
    angle between the built triangle and the same triangle at its facet's own values (chamfer and roll depth as
    built), so only the hold tilts it; a triangle counts when its largest hold is >= FADE_HOLD_MIN_MM (0.05 mm, under
    one photo pixel: a micron-wide sliver's tilt is no wall). A counted tilt > FADE_CLIFF_DEG (25) is a hidden wall
    (shaded as the fade, seen only in the silhouette): E_FADE_CLIFF naming the faded facet, the less-faded neighbour
    holding it (the holder of the triangle's vertex held farthest from its fade), the d range of those triangles, the
    worst point, the largest hold (mm) and tilt, and the fix (fade the neighbour the same way, or end the crease at a
    node); else the largest counted tilt >= FADE_HOLD_WARN_DEG (10) is one W_FADE_HOLD (one receipt line). Mixed
    fades under the limit build. Fades that move none of their facet's vertices FADE_HOLD_MIN_MM (less-faded
    neighbours hold them all, or the fade lies outside the facet) are one W_FADE_INERT naming those facets; fades that
    start within FADE_EARLY_SHARE of their facet's own d extent and within FADE_EARLY_MM of its end (the facet barely
    stands flat anywhere, so it never reads as a face of its own) are one W_FADE_EARLY naming those facets;
 8. sharp edges: a crease edge (two different regions, facet/facet or facet/base) between facet cells always; one
    with a faded cell on either side while the crease still stands: its design dihedral (the angle between the two
    regions' surfaces, plane / fade / base, at the edge midpoint; chamfers, roll and triangulation aside) >=
    CREASE_STANDS_DEG, or its built dihedral >= 6 deg; so a faded crease is sharp until it has faded out. Edges inside
    one faded facet, ramp and chamfer edges at a dihedral >= 6 deg;
    roll edges (sub-band / sub-band, roll / outer face, roll / strip) smooth; face-to-strip and face-to-cap sharp;
    strip / strip and strip / cap at kinks > kink_sharp_deg. Custom normals: face normals on facet-cell polygons;
    on a smooth fan of one faded region's outer / roll polygons, one normal: the facet cell's where the fade starts,
    else the design surface's (faded_corner_normals; the roll does not tilt it); auto-smoothed elsewhere;
 9. creases: every facet/facet and facet/base pair with its length-weighted dihedral and ridge/valley shares; each
    declared pair must share an edge inside the outline and be built with its declared shape (E_CREASE_SHAPE); pairs
    below 2 deg are "flat" (a declaration on them is refused, an undeclared one warns W_FLAT_CREASE). Where a facet
    runs straight (within STRAIGHT_NODE_DEG) through an inside node, or bends back there, and three or more creases
    meet there all declared with one shape, no node values build it (a convex corner holding a straight line is a
    prism along it): the refusal says one of them has the other shape and that splitting the facet is no way around
    it (field straight_node).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from . import planes as PL_
from .arrange import Arrangement, build_arrangement, clip_segment, ear_clip, poly_area, simple_polygon
from .spec import (ARM_NORMAL_SPREAD_DEG, BORDER_RAMP_WARN_DEG, CREASE_STANDS_DEG, DEDUPE_MM, FADE_CLIFF_DEG,
                   FADE_EARLY_MM, FADE_EARLY_SHARE, FADE_HOLD_MIN_MM, FADE_HOLD_WARN_DEG, FADE_ZERO, FLAT_CREASE_DEG,
                   ROLL_BANDS, SHARP_DIHEDRAL_DEG, TILE_TOL_MM2, ParsedSpec, TempleError, _facet_crossings,
                   chamfer_breaks, chamfer_width, parse, roll_height_limit, roll_profile, smoothstep)

WALL_KINDS = ("top", "bottom", "cap_hinge", "cap_tip")
OUTER_KINDS = ("outer", "roll")
PLANAR_MM = 1e-9                  # the polygon self-check
INSIDE_KINDS = ("station", "roll", "chamfer")    # segments inside the outline by construction (never clipped)


@dataclass
class TempleBuild:
    spec: ParsedSpec
    arr: Arrangement
    sol: object                       # planes.PlaneSolution
    face_label: list                  # per arrangement face: facet label or "base"
    face_chamfer: list                # per arrangement face: chamfer index or None
    face_roll: list                   # per arrangement face: inside the roll band
    face_cell: list                   # per arrangement face: "facet" | "faded" | "ramp" | "base"
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
    flat_polys: list                  # polygons whose corners take the face normal (outer facet cells, weight 0)
    normals: np.ndarray               # (polys, 3) unit outward normals (local)
    checks: dict
    tables: dict
    warnings: list = field(default_factory=list)

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


def fade_weight(ps: ParsedSpec, label: str, d) -> np.ndarray:
    """A facet's fade weight s at d: smoothstep((d - d0) / (d1 - d0)), 0 for a non-faded facet; below FADE_ZERO it is
    0 (a station rounded to 12 decimals still sits at the fade start)."""
    d = np.asarray(d, np.float64)
    fade = ps.facets[label]["fade"]
    if fade is None:
        return np.zeros(d.shape)
    s = smoothstep((d - fade["d0"]) / (fade["d1"] - fade["d0"]))
    return np.where(s < FADE_ZERO, 0.0, s)


def facet_values(ps: ParsedSpec, sol, label: str, d, y):
    """X of a facet at (d, y): its plane, or for a faded facet (1 - s) plane + s base, s = its fade weight."""
    X = sol.planes[label].X(d, y)
    if ps.facets[label]["fade"] is None:
        return X
    s = fade_weight(ps, label, d)
    return (1.0 - s) * X + s * ps.x_base(d, y)


def region_values(ps: ParsedSpec, sol, region: str, d, y):
    """X of a region's design surface at (d, y): a facet's (faded) values or the base (chamfers and roll aside)."""
    return ps.x_base(d, y) if region == "base" else facet_values(ps, sol, region, d, y)


def design_dihedral(ps: ParsedSpec, sol, r1: str, r2: str, d, y, h: float = 1e-3) -> np.ndarray:
    """Angle (deg) between the design surfaces of two regions at the points (d, y): central differences of
    region_values, so a fade's slope term and the base gradient count; chamfers, roll and the triangulation do not."""
    d, y = np.asarray(d, np.float64), np.asarray(y, np.float64)
    n = []
    for r in (r1, r2):
        gd = (region_values(ps, sol, r, d + h, y) - region_values(ps, sol, r, d - h, y)) / (2 * h)
        gy = (region_values(ps, sol, r, d, y + h) - region_values(ps, sol, r, d, y - h)) / (2 * h)
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
    fades = []
    for f in ps.facets.values():
        if f["fade"] is not None:
            a, b = sorted((f["fade"]["d0"], f["fade"]["d1"]))
            src |= {k for k in (a, b) if d0 < k < d1}
            fades.append((a, b, min(step, (b - a) / 6.0)))
    near = max(ps.options["snap_mm"], 1e-6)
    for k in sorted({float(e[0]) for seg in prep.band for e in seg}):
        if d0 < k < d1 and (not src or min(abs(x - k) for x in src) > near):
            src.add(k)
    bounds = [d0] + sorted(src) + [d1]
    out = []
    for a, b in zip(bounds[:-1], bounds[1:]):
        st = step
        for lo, hi, sf in fades:
            if a >= lo - 1e-12 and b <= hi + 1e-12:
                st = min(st, sf)
        out += _subdivide(a, b, st)
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
def _label_faces(ps: ParsedSpec, arr: Arrangement) -> tuple[list, list]:
    polys = {l: np.array([ps.nodes[n][:2] for n in f["poly"]]) for l, f in ps.facets.items()}
    for l, P in polys.items():
        if abs(poly_area(P)) <= TILE_TOL_MM2:
            raise TempleError("E_SPEC", f"facet {l}: its polygon has no area", facet=l)
        ok, bad = simple_polygon(P)
        if not ok:
            raise TempleError("E_SPEC", f"facet {l}: its polygon crosses itself (edges {bad})", facet=l)
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
    raise TempleError("E_ROLL", f"roll_mm {r:g}: {len(pts)} facet corner(s) or crossing(s) lie within the roll band "
                      f"(nodes {sorted(nodes)}); move them onto the outline (within snap_mm) or at least roll_mm away",
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


def _border_band(ps: ParsedSpec, arr0: Arrangement, labels0: list) -> tuple[list, list]:
    """Facet/base borders (merged runs) and the band segments border_band_mm into the base (module docstring)."""
    band = ps.options["border_band_mm"]
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
                borders.append({"facet": lab, "p": P.copy(), "q": Q.copy()})
            dirs = [(Q - P) / np.hypot(*(Q - P)) for P, Q, _ in pieces]
            offs = [[P + band * _left(u), Q + band * _left(u)] for (P, Q, _), u in zip(pieces, dirs)]
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
            segs += [(a, b) for a, b in offs] + bevels
            if not closed:
                q_end = _walk(V, cyc, (k0 + n) % m, band, +1, across)
                q_start = _walk(V, cyc, k0, band, -1, across)
                segs.append((offs[-1][1], q_end))
                segs.append((q_start, offs[0][0]))
    return borders, _clip_to_base(ps, segs)


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
def build_temple(spec) -> TempleBuild:
    """Plan and check one +X temple from a spec dict (or a ParsedSpec). Raises TempleError (nothing built)."""
    ps = spec if isinstance(spec, ParsedSpec) else parse(spec)
    prep = prepare(ps)
    stations = station_list(ps, prep)
    arr = build_arrangement(ps.boundary(), segments(ps, prep, stations), chord_fn=lambda d: chord(ps, d),
                            inside=INSIDE_KINDS)
    labels, chamf = _label_faces(ps, arr)
    sol = PL_.solve(ps, arr, labels)
    V2 = arr.V
    n = len(V2)
    band = ps.options["border_band_mm"]
    B = ps.x_base(V2[:, 0], V2[:, 1])
    flabs = sorted({l for l in labels if l != "base"})
    col = {l: j for j, l in enumerate(flabs)}
    W = np.full((n, len(flabs)), np.inf)          # fade weight of each facet at each of its vertices (inf: not on it)
    for fi, f in enumerate(arr.faces):
        if labels[fi] != "base":
            W[f, col[labels[fi]]] = 0.0
    for j, lab in enumerate(flabs):
        idx = np.nonzero(W[:, j] == 0.0)[0]
        W[idx, j] = fade_weight(ps, lab, V2[idx, 0])
    S = B.copy()
    on_facet = np.isfinite(W).any(axis=1) if flabs else np.zeros(n, bool)
    holder = np.full(n, None, object)             # the facet whose values a vertex takes (None: base or ramp)
    if flabs:
        pick = np.argmin(W, axis=1)                # the least-faded facet; ties: the first label
        holder[on_facet] = np.array(flabs, object)[pick[on_facet]]
        for j, lab in enumerate(flabs):
            idx = np.nonzero(on_facet & (pick == j))[0]
            if len(idx):
                S[idx] = facet_values(ps, sol, lab, V2[idx, 0], V2[idx, 1])
    delta = np.full(n, np.inf)
    if prep.borders:
        P = np.array([b["p"] for b in prep.borders])
        Q = np.array([b["q"] for b in prep.borders])
        base_v = np.nonzero(~on_facet)[0]
        if len(base_v):
            X2 = V2[base_v]
            s = Q - P
            L2 = np.maximum(np.einsum("ij,ij->i", s, s), 1e-30)
            t = np.clip(((X2[:, None, :] - P[None]) * s[None]).sum(-1) / L2[None], 0.0, 1.0)
            C = P[None] + t[..., None] * s[None]
            dist = np.hypot(X2[:, None, 0] - C[..., 0], X2[:, None, 1] - C[..., 1])
            j = np.argmin(dist, axis=1)
            dmin = dist[np.arange(len(base_v)), j]
            delta[base_v] = dmin
            ramp = dmin < band
            for k in np.nonzero(ramp)[0]:
                q = C[k, j[k]]
                lab = prep.borders[j[k]]["facet"]
                mis = float(facet_values(ps, sol, lab, q[0], q[1])) - float(ps.x_base(q[0], q[1]))
                S[base_v[k]] = B[base_v[k]] + (1.0 - dmin[k] / band) * mis
    delta[on_facet] = 0.0
    cells = []
    for fi, f in enumerate(arr.faces):
        lab = labels[fi]
        if lab != "base":
            cells.append("faded" if np.any(W[f, col[lab]] > 0.0) else "facet")     # by the fade weight it has
        else:
            cells.append("ramp" if np.any(delta[f] < band * (1.0 - 1e-9)) else "base")
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
    if checks["planarity_max_mm"] > PLANAR_MM:
        k = int(np.argmax(planarity))
        raise TempleError("E_TRIANGULATE", f"polygon {k} ({kind[k]} {region[k]}) is {planarity[k]:.3g} mm off its "
                          f"plane", polygon=k, kind=kind[k], region=region[k], planarity_mm=float(planarity[k]))
    hold_warnings = _check_fade_holds(ps, sol, V, polys, kind, region, pcell, S, holder)
    hold_warnings += _inert_fades(ps, sol, arr, labels, S)
    hold_warnings += _early_fades(ps, arr, labels)
    sharp, edge_info, warnings = _sharp_edges(ps, sol, V, mesh_topology(polys), kind, region, pcell, normals)
    flat = [i for i, (k_, c_) in enumerate(zip(kind, pcell)) if k_ == "outer" and c_ == "facet"]
    tb = TempleBuild(spec=ps, arr=arr, sol=sol, face_label=labels, face_chamfer=chamf, face_roll=froll,
                     face_cell=cells, X=Xo, X_surface=S, thickness=thick, borders=prep.borders, stations=stations,
                     V=V, polys=polys, poly_kind=kind, poly_region=region, poly_face=pface, poly_cell=pcell, tris=tris,
                     tri_poly=tri_poly, sharp=sharp, flat_polys=flat, normals=normals, checks=checks, tables={},
                     warnings=list(sol.warnings) + warnings + hold_warnings)
    tb.tables = _tables(tb, edge_info, B, prep, roll_info, areas)
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


def _sharp_edges(ps, sol, V, topo, kind, region, cell, normals):
    """Sharp edges (module docstring step 8) and the crease / chamfer edge records."""
    sharp, info, warnings = [], [], []
    kink = ps.options["kink_sharp_deg"]
    spread = 0.0
    D = topo["directed"]
    edges = [(a, b, p1, D[(b, a)]) for (a, b), p1 in D.items() if a < b]
    P1 = np.fromiter((e[2] for e in edges), np.int64, len(edges))
    P2 = np.fromiter((e[3] for e in edges), np.int64, len(edges))
    dihs = np.degrees(np.arccos(np.clip(np.einsum("ij,ij->i", normals[P1], normals[P2]), -1.0, 1.0))).tolist()
    stands = _faded_creases_standing(ps, sol, V, edges, kind, region, cell)
    for (a, b, p1, p2), dih in zip(edges, dihs):
        k1, k2 = kind[p1], kind[p2]
        s = False
        if k1 in OUTER_KINDS and k2 in OUTER_KINDS:
            l1, _, c1 = region[p1].partition("/")
            l2, _, c2 = region[p2].partition("/")
            if l1 != l2:                                    # always; beside a faded cell while the crease stands
                info.append(("crease", _pair(l1, l2), a, b, p1, p2, dih))
                s = "faded" not in (cell[p1], cell[p2]) or stands[(a, b)] or dih >= SHARP_DIHEDRAL_DEG
            elif "roll" in (k1, k2):
                s = False                                   # the rounded band: tangent sub-bands
            elif c1 != c2:
                s = dih >= SHARP_DIHEDRAL_DEG
                info.append(("chamfer", tuple(sorted((region[p1], region[p2]))), a, b, p1, p2, dih))
            elif cell[p1] in ("faded", "ramp") or cell[p2] in ("faded", "ramp"):
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
    gd = (X(d + h, y) - X(d - h, y)) / (2 * h)
    gy = (X(d, y + h) - X(d, y - h)) / (2 * h)
    n = np.stack([-gd, -gy, np.ones(np.broadcast(gd, gy).shape)], axis=-1)
    return n / np.linalg.norm(n, axis=-1, keepdims=True)


def faded_corner_normals(tb) -> dict:
    """{(polygon, vertex): unit local normal} for the corners of every smooth fan (the polygons around a vertex joined
    by non-sharp edges) that holds a faded outer cell and only outer / roll polygons of that one region: the face
    normal of a facet-cell polygon in the fan (where a fade starts), else the region's design-surface normal there
    (design_normal). One fan, one normal: the faded surface shades as designed, the roll sub-bands do not tilt it (the
    roll is tangent at its line) and Blender splits no fan. Facet-cell polygons themselves always carry their face
    normal (TempleBuild.flat_polys); other fans stay auto-smoothed (absent here). Empty without faded cells."""
    polys, kind, region, cell = tb.polys, tb.poly_kind, tb.poly_region, tb.poly_cell
    seeds = sorted({v for i, p in enumerate(polys) if cell[i] == "faded" and kind[i] == "outer" for v in p})
    if not seeds:
        return {}
    want = set(seeds)
    at_v: dict = {}
    for i, p in enumerate(polys):
        for k, v in enumerate(p):
            if v in want:
                at_v.setdefault(v, []).append((i, k))
    sharp = set(tb.sharp)
    flat = set(tb.flat_polys)
    fans, pending = [], []                            # (vertex, [polygons], region, flat polygon or None)
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
        for g in groups.values():
            regs = {region[i] for i in g}
            if len(regs) != 1 or not all(kind[i] in OUTER_KINDS for i in g):
                continue
            if not any(cell[i] == "faded" and kind[i] == "outer" for i in g):
                continue
            reg = regs.pop()
            f = next((i for i in g if i in flat), None)
            fans.append((v, g, reg, f))
            if f is None:
                pending.append(len(fans) - 1)
    N = {}
    by_reg: dict = {}
    for j in pending:
        by_reg.setdefault(fans[j][2], []).append(j)
    for reg, js in by_reg.items():
        vs = [fans[j][0] for j in js]
        n = design_normal(tb.spec, tb.sol, reg, tb.V[vs, 0], tb.V[vs, 1])
        N.update({j: n[k] for k, j in enumerate(js)})
    out = {}
    for j, (v, g, _reg, f) in enumerate(fans):
        n = tb.normals[f] if f is not None else N[j]
        for i in g:
            if i not in flat:
                out[(i, v)] = n
    return out


def _find(root: dict, a):
    while root[a] != a:
        root[a] = root[root[a]]
        a = root[a]
    return a


def _unit_normals(P: np.ndarray) -> np.ndarray:
    """Unit normals (m, 3) of the triangles P (m, 3, 3) by their vertex order (no degenerate triangle here: the outer
    triangles have positive (d, y) area, self-check)."""
    n = np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0])
    return n / np.linalg.norm(n, axis=1, keepdims=True)


def _fade_tilts(ps, sol, V, polys, kind, region, cell, S) -> tuple:
    """(triangles (m, 3), holds (m, 3) mm, tilts deg (m,), facet labels (m,)) of every faded outer and roll triangle.
    A vertex's hold = S minus the faded facet's own value there (facet_values; + = held above its fade by a less-faded
    neighbour, module docstring step 4; 0 where the facet holds itself). The tilt = the angle between the built
    triangle and the same triangle at the facet's own values (every vertex moved by -hold; chamfer and roll depth as
    built): 0 without a hold, so neither the triangulation, the fade's own curvature, the chamfer nor the roll counts,
    only the hold."""
    idx = [i for i, (k, c) in enumerate(zip(kind, cell)) if k in OUTER_KINDS and c == "faded"]
    T = np.array([polys[i] for i in idx], np.int64).reshape(-1, 3)
    labs = np.array([region[i].partition("/")[0] for i in idx], object)
    H = np.zeros(T.shape)
    for lab in sorted(set(labs)):
        m = labs == lab
        vs = T[m].ravel()
        H[m] = (S[vs] - facet_values(ps, sol, lab, V[vs, 0], V[vs, 1])).reshape(-1, 3)
    P = V[T]
    Q = P.copy()
    Q[..., 2] -= H
    nb, nq = _unit_normals(P), _unit_normals(Q)
    tilt = np.degrees(np.arctan2(np.linalg.norm(np.cross(nb, nq), axis=1), np.einsum("ij,ij->i", nb, nq)))
    return T, H, tilt, labs


def _hold_groups(V, holder, T, H, tilt, labs, sel) -> list:
    """The selected faded triangles grouped by (faded facet, neighbour): the neighbour is the facet holding the
    triangle's vertex held farthest from the faded facet's own value (its holder, module docstring step 4). Per group:
    facet, neighbour, d range, triangle count, the largest tilt and where (centroid d, y), the signed hold of largest
    size (+ = held above its fade). Largest tilt first."""
    groups: dict = {}
    for k in np.nonzero(sel)[0]:
        lab, tri = str(labs[k]), T[k]
        j = int(np.argmax(np.abs(H[k])))
        h, v = float(H[k, j]), int(tri[j])
        nb = None if holder[v] is None or holder[v] == lab else str(holder[v])
        g = groups.setdefault((lab, nb), {"facet": lab, "neighbour": nb, "d_mm": [math.inf, -math.inf],
                                          "triangles": 0, "tilt_deg": -1.0, "at": None, "hold_mm": 0.0})
        g["d_mm"] = [min(g["d_mm"][0], float(V[tri, 0].min())), max(g["d_mm"][1], float(V[tri, 0].max()))]
        g["triangles"] += 1
        if tilt[k] > g["tilt_deg"]:
            c = V[tri].mean(axis=0)
            g["tilt_deg"], g["at"] = float(tilt[k]), [float(c[0]), float(c[1])]
        if abs(h) > abs(g["hold_mm"]):
            g["hold_mm"] = h
    return sorted(groups.values(), key=lambda g: (-g["tilt_deg"], g["facet"], g["neighbour"] or ""))


def _check_fade_holds(ps, sol, V, polys, kind, region, cell, S, holder) -> list:
    """E_FADE_CLIFF when a faded outer or roll triangle held >= FADE_HOLD_MIN_MM tilts more than FADE_CLIFF_DEG off
    its own fade: a less-faded neighbour holds the facet's shared vertices at its own height and the drop to the fade
    happens inside one row of triangles (a wall that shades as flat surface). Else one W_FADE_HOLD for the largest
    such tilt at FADE_HOLD_WARN_DEG or more ([] below it). Holds under FADE_HOLD_MIN_MM never count (module docstring
    step 7)."""
    T, H, tilt, labs = _fade_tilts(ps, sol, V, polys, kind, region, cell, S)
    counted = np.abs(H).max(axis=1) >= FADE_HOLD_MIN_MM
    if not counted.any() or float(tilt[counted].max()) < FADE_HOLD_WARN_DEG:
        return []
    cliff = float(tilt[counted].max()) > FADE_CLIFF_DEG
    sel = counted & (tilt > FADE_CLIFF_DEG if cliff else tilt >= FADE_HOLD_WARN_DEG)
    groups = _hold_groups(V, holder, T, H, tilt, labs, sel)
    g = groups[0]
    lab, nb, h, deg = g["facet"], g["neighbour"], g["hold_mm"], g["tilt_deg"]
    who = nb or "a neighbour"
    a, b = (f"{x:.1f}" for x in g["d_mm"])
    if cliff:
        msg = (f"facet {lab} fades but {who} holds it {abs(h):.2f} mm {'up' if h > 0 else 'down'} on their crease at "
               f"d {a}-{b} (worst at d {g['at'][0]:.1f} y {g['at'][1]:.1f}): a {'folded ' if deg > 90 else ''}wall "
               f"{min(deg, 90.0):.0f} deg off its fade (> {FADE_CLIFF_DEG:g}); fade {who} the same way, start the "
               f"fade where the crease ends, or end the crease at a node")
        raise TempleError("E_FADE_CLIFF", msg, facet=lab, neighbour=nb, facets=[lab] + ([nb] if nb else []),
                          d_mm=g["d_mm"], hold_mm=h, tilt_deg=deg, at=g["at"], limit_deg=FADE_CLIFF_DEG,
                          min_hold_mm=FADE_HOLD_MIN_MM, failures=groups)
    msg = (f"{who} holds faded {lab} {abs(h):.2f} mm {'above' if h > 0 else 'below'} its fade at d {a}-{b}: tilt "
           f"{deg:.1f} deg (>= {FADE_HOLD_WARN_DEG:g}; > {FADE_CLIFF_DEG:g} is refused)")
    return [{"code": "W_FADE_HOLD", "facet": lab, "neighbour": nb, "deg": deg, "hold_mm": h, "d_mm": g["d_mm"],
             "at": g["at"], "message": msg}]


INERT_MESSAGE_CHARS = 150         # W_FADE_INERT lists facets up to about this many characters, then "+N more"


def _inert_fades(ps, sol, arr, labels, S) -> list:
    """[] or one W_FADE_INERT (one short receipt line) naming every faded facet whose fade moves none of its vertices
    FADE_HOLD_MIN_MM or more off its plane (S against the plane: without its fade every vertex of the facet takes the
    plane), each with why: less-faded neighbours hold every vertex its own fade would move ("held by less-faded
    neighbours"), its fade weight is 0 on every vertex ("outside it"), or its own fade moves them less: under weight
    0.5 "barely reaches it", else "already on the arm" (the plane lies on the base there). Facets past
    INERT_MESSAGE_CHARS are counted ("+N more"); the record lists them all."""
    items = []
    for lab in sorted(ps.facets):
        fade = ps.facets[lab]["fade"]
        vs = sorted({v for fi, f in enumerate(arr.faces) if labels[fi] == lab for v in f})
        if fade is None or not vs:
            continue
        d, y = arr.V[vs, 0], arr.V[vs, 1]
        plane = sol.planes[lab].X(d, y)
        if float(np.max(np.abs(S[vs] - plane))) >= FADE_HOLD_MIN_MM:
            continue
        w = float(np.max(fade_weight(ps, lab, d)))
        if float(np.max(np.abs(facet_values(ps, sol, lab, d, y) - plane))) >= FADE_HOLD_MIN_MM:
            why = "held by less-faded neighbours"
        elif w == 0.0:
            why = "outside it"
        else:
            why = "barely reaches it" if w < 0.5 else "already on the arm"
        items.append((lab, why))
    if not items:
        return []
    text = f"fades that move no vertex >= {FADE_HOLD_MIN_MM:g} mm: "
    shown = 0
    for lab, why in items:
        part = f"{'; ' if shown else ''}{lab} ({why})"
        if shown and len(text) + len(part) > INERT_MESSAGE_CHARS - len("; +99 more"):
            break
        text += part
        shown += 1
    if shown < len(items):
        text += f"; +{len(items) - shown} more"
    return [{"code": "W_FADE_INERT", "facets": [lab for lab, _ in items], "why": dict(items), "message": text}]


EARLY_ADVICE = "; fade later (alike across a shared crease)"
EARLY_MESSAGE_CHARS = 156         # W_FADE_EARLY's message: "W_FADE_EARLY: " + it fits a 170-character receipt line


def _early_fades(ps, arr, labels) -> list:
    """[] or one W_FADE_EARLY (one short receipt line) naming every faded facet whose fade starts within
    FADE_EARLY_SHARE of the facet's own built d extent and within FADE_EARLY_MM of its end, both counted from the side
    the facet stands on (d0 < d1: its front; else its back): such a facet is faded nearly everywhere, so it never
    reads as a flat face of its own. Each item names the flat length and that end; facets past EARLY_MESSAGE_CHARS
    (with EARLY_ADVICE) are counted ("+N more"); the record lists them all."""
    items = []
    for lab in sorted(ps.facets):
        fade = ps.facets[lab]["fade"]
        vs = sorted({v for fi, f in enumerate(arr.faces) if labels[fi] == lab for v in f})
        if fade is None or not vs:
            continue
        lo, hi = float(arr.V[vs, 0].min()), float(arr.V[vs, 0].max())
        if hi - lo <= DEDUPE_MM:
            continue
        front = fade["d1"] > fade["d0"]
        flat = max((fade["d0"] - lo) if front else (hi - fade["d0"]), 0.0)
        share = min(flat / (hi - lo), 1.0)
        if share < FADE_EARLY_SHARE and flat < FADE_EARLY_MM:
            items.append((lab, share, flat, lo if front else hi, "front" if front else "back"))
    if not items:
        return []
    text = "barely flat before their fade: "
    budget = EARLY_MESSAGE_CHARS - len(EARLY_ADVICE) - len("; +99 more")
    shown = 0
    for lab, share, flat, end, side in items:
        part = f"{'; ' if shown else ''}{lab} flat {flat:.1f} mm at its {side} (d {end:.1f})"
        if shown and len(text) + len(part) > budget:
            break
        text += part
        shown += 1
    if shown < len(items):
        text += f"; +{len(items) - shown} more"
    return [{"code": "W_FADE_EARLY", "facets": [it[0] for it in items],
             "share": {it[0]: round(it[1], 3) for it in items},
             "flat_mm": {it[0]: round(it[2], 2) for it in items}, "message": text + EARLY_ADVICE}]


def _faded_creases_standing(ps, sol, V, edges, kind, region, cell) -> dict:
    """{(a, b): design dihedral >= CREASE_STANDS_DEG} for every crease edge (two different regions) with a faded cell
    on either side, at the edge midpoint: the crease still stands there (module docstring step 8)."""
    groups: dict = {}
    for a, b, p1, p2 in edges:
        if kind[p1] in OUTER_KINDS and kind[p2] in OUTER_KINDS and "faded" in (cell[p1], cell[p2]):
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


def _tables(tb: TempleBuild, edge_info: dict, B: np.ndarray, prep: Prep, roll_info, areas: np.ndarray) -> dict:
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
                     and tb.poly_cell[i] == "facet"]
        vs = sorted({v for i in plain_ids for v in tb.polys[i]})
        rec = {"label": label, "area_mm2": area, "centroid": cen.tolist(), "normal_local": nrm.tolist(),
               "lean_deg": _dihedral(nrm, bn), "plane": {"kind": pl.kind, "X_coef": [float(x) for x in pl.coef]},
               "defining_points": pl.defining, "conditioning_deg_per_mm": pl.conditioning_deg_per_mm,
               "fade": ps.facets[label]["fade"],
               "planarity_mm": float(np.max(np.abs(V[vs, 2] - pl.X(V[vs, 0], V[vs, 1])))) if vs else 0.0}
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
    for kind_, key, a, b, p1, p2, dih in edge_info["edges"]:
        rec = pairs.setdefault((kind_,) + tuple(key), {"kind": kind_, "regions": list(key), "length_mm": 0.0,
                                                        "dihedral_deg": [dih, dih], "ridge": 0.0, "valley": 0.0,
                                                        "_wsum": 0.0})
        L = float(np.linalg.norm(V[a] - V[b]))
        rec["length_mm"] += L
        rec["_wsum"] += L * dih
        rec["dihedral_deg"] = [min(rec["dihedral_deg"][0], dih), max(rec["dihedral_deg"][1], dih)]
        cen2 = V[tb.polys[p2]].mean(axis=0)
        s = float(tb.normals[p1] @ (cen2 - 0.5 * (V[a] + V[b])))
        rec["ridge" if s < 0 else "valley"] += L
    declared = {tuple(c["facets"]): c["shape"] for c in ps.creases}
    creases = []
    for rec in pairs.values():
        rec["mean_dihedral_deg"] = rec.pop("_wsum") / rec["length_mm"] if rec["length_mm"] > 0 else 0.0
        if rec["mean_dihedral_deg"] < FLAT_CREASE_DEG:
            rec["shape"] = "flat"
        else:
            rec["shape"] = "ridge" if rec["ridge"] >= rec["valley"] else "valley"
        rec["ridge_share"] = rec["ridge"] / rec["length_mm"] if rec["length_mm"] > 0 else 0.0
        if rec["kind"] == "crease":
            rec["declared"] = declared.get(tuple(rec["regions"]))
            faded_end = "base" in rec["regions"] and ps.facets[rec["regions"][0]]["fade"] is not None
            if rec["shape"] == "flat" and rec["declared"] is None and not faded_end:
                tb.warnings.append({"code": "W_FLAT_CREASE", "regions": rec["regions"],
                                    "deg": rec["mean_dihedral_deg"],
                                    "message": f"crease {'|'.join(rec['regions'])} bends only "
                                               f"{rec['mean_dihedral_deg']:.2f} deg (< {FLAT_CREASE_DEG})"})
        creases.append(rec)
    out["creases"] = sorted(creases, key=lambda r_: (r_["kind"], r_["regions"]))
    thick = tb.thickness
    k0, k1 = int(np.argmin(thick)), int(np.argmax(thick))
    out["thickness"] = {"min_mm": float(thick[k0]), "min_at": tb.arr.V[k0].tolist(),
                        "max_mm": float(thick[k1]), "max_at": tb.arr.V[k1].tolist()}
    out["border_ramps"] = _border_ramps(tb, B)
    for r in out["border_ramps"]:
        if r["slope_deg"] > BORDER_RAMP_WARN_DEG:
            a, b = (f"{x:.1f}" for x in r["d_mm"])
            tb.warnings.append({"code": "W_BORDER_RAMP", "facet": r["facet"], "slope_deg": r["slope_deg"],
                                "height_mm": r["height_mm"], "d_mm": r["d_mm"],
                                "message": f"facet {r['facet']} stands {'proud of' if r['height_mm'] > 0 else 'below'} "
                                           f"the base by {abs(r['height_mm']):.2f} mm "
                                           f"{f'at d {a}' if a == b else f'along d {a}-{b}'}: a "
                                           f"{r['slope_deg']:.1f} deg ramp over border_band_mm "
                                           f"{ps.options['border_band_mm']:g} (> {BORDER_RAMP_WARN_DEG})"})
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


def _border_ramps(tb: TempleBuild, B: np.ndarray) -> list:
    """Per border run (a connected chain of final arrangement edges between one facet and the base): its d range, the
    ramp height = the signed facet - base mismatch of largest size on it (+ = the facet stands proud of the base,
    - = sunk below it), where, the ramp slope over border_band_mm and the run length. Sorted by facet, then d."""
    ps = tb.spec
    band = ps.options["border_band_mm"]
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
                        "at": V2[vs[k]].tolist(), "slope_deg": math.degrees(math.atan2(abs(h), band)),
                        "length_mm": float(sum(np.hypot(*(V2[a] - V2[b])) for a, b in es))})
    return sorted(out, key=lambda r: (r["facet"], r["d_mm"][0], r["d_mm"][1]))


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


def _base_planarity(tb: TempleBuild) -> dict:
    """Information: how far each triangulated (base, faded, ramp) cell's outer vertices are from one plane (its
    Newell plane through the centroid)."""
    faces = [f for fi, f in enumerate(tb.arr.faces) if tb.face_cell[fi] != "facet" and len(f) > 3]
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
    with the other shape. The fix named: the node values; for a pair with a faded facet also a later fade start or end
    (a fade that flattens the crease over most of its length builds it flat). At a node that cannot be built as
    declared (_straight_node: a facet runs straight through it or bends back there, and every crease meeting there is
    declared with one shape) the message leads with a refused crease there and names the fix instead: one of those
    creases has the other shape (read from the photos), and splitting the facet is no way around it."""
    built = {tuple(r["regions"]): r for r in tb.tables["creases"] if r["kind"] == "crease"}
    bad = []
    for c in tb.spec.creases:
        key = tuple(c["facets"])
        r = built.get(key)
        if r is None:
            bad.append({"facets": list(key), "declared": c["shape"], "built": "none", "dihedral_deg": None,
                        "why": "the pair shares no edge inside the outline"})
        elif r["shape"] != c["shape"]:
            bad.append({"facets": list(key), "declared": c["shape"], "built": r["shape"],
                        "dihedral_deg": r["mean_dihedral_deg"], "ridge_share": r["ridge_share"],
                        "why": "flat (< 2 deg): no ridge or valley" if r["shape"] == "flat"
                        else "built with the other shape"})
    if bad:
        node = _straight_node(tb, bad)
        b = bad[0] if node is None else next(x for x in bad if x["facets"] == node["refused"])
        dih = "" if b["dihedral_deg"] is None else f" {b['dihedral_deg']:.1f} deg"
        extra = {}
        if node is None:
            faded = any(tb.spec.facets[f]["fade"] is not None for f in b["facets"] if f in tb.spec.facets)
            fix = ("fix the node values, or start the fade later / end it later" if faded else "fix the node values") \
                + ", do not redeclare"
        else:
            x, c, shape = node["facet"], node["node"], node["shape"]
            how = (f"runs straight through node {c}" if node["angle_deg"] <= 180.0 + STRAIGHT_NODE_DEG
                   else f"has a {node['angle_deg']:.0f} deg corner at node {c}")
            fix = (f"facet {x} {how}, so the {len(node['creases'])} creases there cannot all be {shape}s: one is a "
                   f"{'valley' if shape == 'ridge' else 'ridge'} or two of those faces are one (check the photos or "
                   f"facet_variants); keep {x} whole")
            extra["straight_node"] = node
        raise TempleError("E_CREASE_SHAPE", f"crease {'|'.join(b['facets'])} is declared {b['declared']} but built "
                          f"{b['built']}{dih} ({b['why']}); {fix}",
                          facets=b["facets"], declared=b["declared"], built=b["built"], dihedral_deg=b["dihedral_deg"],
                          failures=bad, **extra)

