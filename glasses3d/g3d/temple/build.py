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
    border band; every face is labelled (facet or base, chamfer, roll band) and checked (fades: E_FADE_NEIGHBOUR);
 4. planes (planes.py), then vertex values: a facet vertex takes its facet (faded: (1 - s) plane + s base); a base
    vertex at distance delta < band from a border takes base + (1 - delta / band) x the mismatch at the nearest border
    point (the ramp), else the base;
 5. depths: chamfers (v1) and the core roll: r = roll_mm as 8 stacked chamfer sub-bands along the top and bottom
    outline edges with depth c(s) = r - sqrt(r^2 - (r - s)^2) at equal arc angles (s = vertical offset from the edge),
    so the strip's outer edge moves in to X - r and the side silhouette is unchanged. Limit:
    r <= min(min thickness - t_min, 0.45 x outline height - chamfer widths), else E_ROLL;
 6. polygons: facet cells (non-faded facets, chamfered or rolled too) as planar n-gons; every other outer cell (base,
    faded, ramp) as the arrangement's ear-clip triangles, never an n-gon, so Blender and the GLB share the core's
    triangulation; inner polygons (planar: x_in is linear between stations), one planar quad per outline edge (top /
    bottom strips), a planar hinge-end cap and tip cap;
 7. self-checks: closed oriented 2-manifold, Euler 2, volume > 0, every polygon planar (<= 1e-9 mm), thickness >=
    t_min (E_TOO_THIN), outer triangles with positive (d, y) area (no self-intersection);
 8. sharp edges: non-faded facet/facet and facet/base always; faded (inside a faded facet, faded/faded and
    faded/base), ramp and chamfer edges at a dihedral >= 6 deg;
    roll edges (sub-band / sub-band, roll / outer face, roll / strip) smooth; face-to-strip and face-to-cap sharp;
    strip / strip and strip / cap at kinks > kink_sharp_deg. Custom normals: face normals on non-faded facet polygons;
 9. creases: every facet/facet and facet/base pair with its length-weighted dihedral and ridge/valley shares; each
    declared pair must share an edge inside the outline and be built with its declared shape (E_CREASE_SHAPE); pairs
    below 2 deg are "flat" (a declaration on them is refused, an undeclared one warns W_FLAT_CREASE).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from . import planes as PL_
from .arrange import Arrangement, build_arrangement, clip_segment, ear_clip, poly_area, simple_polygon
from .spec import (ARM_NORMAL_SPREAD_DEG, BORDER_RAMP_WARN_DEG, DEDUPE_MM, FLAT_CREASE_DEG, ROLL_BANDS,
                   SHARP_DIHEDRAL_DEG, TILE_TOL_MM2, ParsedSpec, TempleError, _facet_crossings, chamfer_breaks,
                   chamfer_width, parse, roll_height_limit, roll_profile, smoothstep)

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
    flat_polys: list                  # polygons whose corners take the face normal (non-faded facet cells)
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


def facet_values(ps: ParsedSpec, sol, label: str, d, y):
    """X of a facet at (d, y): its plane, or for a faded facet (1 - s) plane + s base, s = smoothstep over the fade."""
    X = sol.planes[label].X(d, y)
    fade = ps.facets[label]["fade"]
    if fade is None:
        return X
    s = smoothstep((np.asarray(d, np.float64) - fade["d0"]) / (fade["d1"] - fade["d0"]))
    return (1.0 - s) * X + s * ps.x_base(d, y)


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
    _check_fades(ps, arr, labels)
    sol = PL_.solve(ps, arr, labels)
    V2 = arr.V
    n = len(V2)
    band = ps.options["border_band_mm"]
    B = ps.x_base(V2[:, 0], V2[:, 1])
    vlab = [[] for _ in range(n)]
    for fi, f in enumerate(arr.faces):
        if labels[fi] != "base":
            for v in f:
                if labels[fi] not in vlab[v]:
                    vlab[v].append(labels[fi])
    S = B.copy()
    on_facet = np.array([bool(l) for l in vlab])
    for lab in sorted({l for ls in vlab for l in ls}):
        idx = np.array([v for v in range(n) if vlab[v] and min(vlab[v]) == lab], np.int64)
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
            cells.append("faded" if ps.facets[lab]["fade"] is not None else "facet")
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
    sharp, edge_info, warnings = _sharp_edges(ps, mesh_topology(polys), kind, region, pcell, normals)
    flat = [i for i, (k_, c_) in enumerate(zip(kind, pcell)) if k_ == "outer" and c_ == "facet"]
    tb = TempleBuild(spec=ps, arr=arr, sol=sol, face_label=labels, face_chamfer=chamf, face_roll=froll,
                     face_cell=cells, X=Xo, X_surface=S, thickness=thick, borders=prep.borders, stations=stations,
                     V=V, polys=polys, poly_kind=kind, poly_region=region, poly_face=pface, poly_cell=pcell, tris=tris,
                     tri_poly=tri_poly, sharp=sharp, flat_polys=flat, normals=normals, checks=checks, tables={},
                     warnings=list(sol.warnings) + warnings)
    tb.tables = _tables(tb, edge_info, B, prep, roll_info, areas)
    _check_creases(tb)
    return tb


def _check_fades(ps: ParsedSpec, arr: Arrangement, labels: list) -> None:
    """E_FADE_NEIGHBOUR: a faded facet's crease neighbours are base or carry the identical fade; no vertex is shared
    by two facets with different fades."""
    if not any(f["fade"] is not None for f in ps.facets.values()):
        return
    fade = {l: ps.facets[l]["fade"] for l in ps.facets}
    ef = arr.edge_faces()
    for (a, b), f1 in ef.items():
        f2 = ef.get((b, a))
        if f2 is None:
            continue
        l1, l2 = labels[f1], labels[f2]
        if l1 != l2 and "base" not in (l1, l2) and fade[l1] != fade[l2]:
            p = 0.5 * (arr.V[a] + arr.V[b])
            raise TempleError("E_FADE_NEIGHBOUR", f"facets {l1} and {l2} share a crease but fade differently "
                              f"({fade[l1]} vs {fade[l2]}): give both the same fade, or make the neighbour base",
                              facets=sorted([l1, l2]), fades=[fade[min(l1, l2)], fade[max(l1, l2)]], at=p.tolist())
    seen: dict = {}
    for fi, f in enumerate(arr.faces):
        if labels[fi] == "base":
            continue
        for v in f:
            seen.setdefault(v, set()).add(labels[fi])
    for v, ls in seen.items():
        ls = sorted(ls)
        if len(ls) > 1 and any(fade[l] != fade[ls[0]] for l in ls[1:]):
            raise TempleError("E_FADE_NEIGHBOUR", f"facets {ls} meet at (d, y) ({arr.V[v, 0]:.3f}, "
                              f"{arr.V[v, 1]:.3f}) with different fades", facets=ls, at=arr.V[v].tolist(),
                              fades=[fade[l] for l in ls])


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


def _sharp_edges(ps, topo, kind, region, cell, normals):
    """Sharp edges (module docstring step 8) and the crease / chamfer edge records."""
    sharp, info, warnings = [], [], []
    kink = ps.options["kink_sharp_deg"]
    spread = 0.0
    D = topo["directed"]
    edges = [(a, b, p1, D[(b, a)]) for (a, b), p1 in D.items() if a < b]
    P1 = np.fromiter((e[2] for e in edges), np.int64, len(edges))
    P2 = np.fromiter((e[3] for e in edges), np.int64, len(edges))
    dihs = np.degrees(np.arccos(np.clip(np.einsum("ij,ij->i", normals[P1], normals[P2]), -1.0, 1.0))).tolist()
    for (a, b, p1, p2), dih in zip(edges, dihs):
        k1, k2 = kind[p1], kind[p2]
        s = False
        if k1 in OUTER_KINDS and k2 in OUTER_KINDS:
            l1, _, c1 = region[p1].partition("/")
            l2, _, c2 = region[p2].partition("/")
            if l1 != l2:
                info.append(("crease", _pair(l1, l2), a, b, p1, p2, dih))
                if "base" not in (l1, l2):                  # always, but two faded facets only where still bent
                    s = "faded" not in (cell[p1], cell[p2]) or dih >= SHARP_DIHEDRAL_DEG
                else:
                    fc = cell[p1] if l1 != "base" else cell[p2]
                    s = fc == "facet" or dih >= SHARP_DIHEDRAL_DEG
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


def _check_creases(tb: TempleBuild) -> None:
    """E_CREASE_SHAPE for every declared pair that shares no edge inside the outline, is flat (< 2 deg) or is built
    with the other shape."""
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
        b = bad[0]
        dih = "" if b["dihedral_deg"] is None else f" {b['dihedral_deg']:.1f} deg"
        raise TempleError("E_CREASE_SHAPE", f"crease {'|'.join(b['facets'])} is declared {b['declared']} but built "
                          f"{b['built']}{dih} ({b['why']}); fix the node values, do not redeclare",
                          facets=b["facets"], declared=b["declared"], built=b["built"], dihedral_deg=b["dihedral_deg"],
                          failures=bad)

