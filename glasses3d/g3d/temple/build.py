"""Build the faceted temple solid (WP6_DESIGN.md §3 construction steps 2-6). numpy only, float64, deterministic.

The solid is {y_bot(d) <= y <= y_top(d), x_in(d) <= X <= x_in(d) + T(d) + r(d, y) - c(d, y)}: r = the facet relief
(planes.py, 0 on the base), c = the chamfer depth (spec.chamfer_width). The (d, y) outline is cut by every facet
edge, the zone-end chord, chamfer lines (+ split segments at every knot inside a chamfer, so it stays planar) and the
station chords of the arm (every knot of the outline, inner face and thickness beyond the zone, subdivided to
station_step_mm); every face of that arrangement is lifted onto its own plane:

    outer  X = x_in + T + r - c       (one polygon per arrangement face, CCW: normal +X)
    inner  X = x_in                   (the same polygon reversed: the inner face is flat where x_in is linear)
    top / bottom strips               (one planar quad per outline edge)
    hinge-end cap, tip cap            (one planar n-gon each)

Sharp edges: facet creases and facet/base edges; chamfer lines whose dihedral >= crease_sharp_deg; every edge where
the outer or inner face meets a strip or a cap; strip/strip and strip/cap edges at outline kinks > kink_sharp_deg.
Facet corners get the facet's face normal (custom normals); every other corner is auto-smoothed.

Self-checks: closed oriented 2-manifold (every directed edge once, its reverse once), Euler characteristic 2,
volume > 0, every polygon planar, thickness >= t_min at every vertex (E_TOO_THIN names the point, the facet and its
defining points), no self-intersection (a graph over a simple outline with positive thickness, verified by every
outer triangle having positive (d, y) area).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from . import planes as PL_
from .arrange import Arrangement, build_arrangement, ear_clip, point_in_poly, poly_area
from .spec import (ARM_NORMAL_SPREAD_DEG, FLAT_CREASE_DEG, TILE_TOL_MM2, ParsedSpec, TempleError, chamfer_breaks,
                   chamfer_width, parse)

WALL_KINDS = ("top", "bottom", "cap_hinge", "cap_tip")


@dataclass
class TempleBuild:
    spec: ParsedSpec
    arr: Arrangement
    sol: object                       # planes.PlaneSolution
    face_label: list                  # per arrangement face: facet label or "base"
    face_chamfer: list                # per arrangement face: chamfer index or None
    V: np.ndarray                     # (2n, 3) local (d, y, X): outer 0..n-1, inner n..2n-1
    polys: list                       # outward polygons (vertex id lists)
    poly_kind: list                   # outer | inner | top | bottom | cap_hinge | cap_tip
    poly_region: list                 # outer: facet label / "base" (+ "/chamfer<i>"); others: their kind
    poly_face: list                   # arrangement face of an outer/inner polygon, else -1
    tris: np.ndarray                  # (m, 3) triangles of the polygons
    tri_poly: np.ndarray              # (m,) polygon of each triangle
    sharp: list                       # sorted (a, b) a < b
    flat_polys: list                  # polygons whose corners take the face normal (the facets)
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


def station_list(ps: ParsedSpec) -> list[float]:
    """Arm stations: every knot of the outline, inner face, thickness and chamfer breaks beyond the zone end,
    subdivided so no gap exceeds station_step_mm. The zone end itself is always a station."""
    ze, d1, step = ps.zone_end, ps.d1, ps.options["station_step_mm"]
    ks = {ze}
    for arr in (ps.top[:, 0], ps.bottom[:, 0], ps.inner.d, ps.thickness.d):
        ks |= {float(k) for k in arr if ze < k < d1}
    for ch in ps.chamfers:
        ks |= {float(k) for k in chamfer_breaks(ch) if ze < k < d1}
    ks = sorted(ks)
    out = []
    bounds = ks + [d1]
    for a, b in zip(bounds[:-1], bounds[1:]):
        out.append(a)
        nsub = int(math.ceil((b - a) / step - 1e-9))
        for k in range(1, nsub):
            out.append(a + (b - a) * k / nsub)
    out = sorted(set(round(x, 12) for x in out))
    return [x for x in out if ze - 1e-12 <= x < d1 - 1e-9]


def segments(ps: ParsedSpec) -> list:
    segs = []
    for label, fac in ps.facets.items():
        P = [ps.nodes[n][:2] for n in fac["poly"]]
        for k in range(len(P)):
            segs.append((np.array(P[k]), np.array(P[(k + 1) % len(P)]), ("facet", label)))
    for d in station_list(ps):
        p, q = chord(ps, d)
        segs.append((p, q, ("station", float(d))))
    for i, ch in enumerate(ps.chamfers):
        a, b = ch["d"]
        edge = ps.top if ch["edge"] == "top" else ps.bottom
        ds = sorted({a, b} | set(chamfer_breaks(ch)) | {float(k) for k in edge[:, 0] if a < k < b})
        yl = (lambda d: float(ps.y_top(d)) - float(chamfer_width(ch, d))) if ch["edge"] == "top" else \
            (lambda d: float(ps.y_bot(d)) + float(chamfer_width(ch, d)))
        ye = (lambda d: float(ps.y_top(d))) if ch["edge"] == "top" else (lambda d: float(ps.y_bot(d)))
        pts = [np.array([d, yl(d)]) for d in ds]
        for p, q in zip(pts[:-1], pts[1:]):
            segs.append((p, q, ("chamfer", i)))
        for d in ds[1:-1]:
            segs.append((np.array([d, yl(d)]), np.array([d, ye(d)]), ("chamfer_split", i)))
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


# ------------------------------------------------------------------------------------------- the build
def build_temple(spec) -> TempleBuild:
    """Plan and check one +X temple from a spec dict (or a ParsedSpec). Raises TempleError (nothing built)."""
    ps = spec if isinstance(spec, ParsedSpec) else parse(spec)
    arr = build_arrangement(ps.boundary(), segments(ps), chord_fn=lambda d: chord(ps, d))
    labels, chamf = _label_faces(ps, arr)
    sol = PL_.solve(ps, arr, labels)
    V2 = arr.V
    n = len(V2)
    r = sol.r_vertex.copy()
    if np.any(~np.isfinite(r)):
        k = int(np.nonzero(~np.isfinite(r))[0][0])
        raise TempleError("E_TRIANGULATE", f"vertex at (d, y) ({V2[k, 0]:.3f}, {V2[k, 1]:.3f}) got no relief",
                          at=V2[k].tolist())
    c = chamfer_depth(ps, V2[:, 0], V2[:, 1])
    x_in = ps.inner(V2[:, 0])
    T = ps.thickness(V2[:, 0])
    thick = T + r - c
    tmin = ps.options["t_min_mm"]
    if np.any(thick < tmin):
        k = int(np.argmin(thick))
        regions = sorted({labels[fi] for fi, f in enumerate(arr.faces) if k in f})
        fac = next((l for l in regions if l != "base"), "base")
        defining = [] if fac == "base" else sol.planes[fac].defining
        raise TempleError("E_TOO_THIN", f"thickness {thick[k]:.3f} mm < t_min {tmin:g} mm at (d, y) "
                          f"({V2[k, 0]:.3f}, {V2[k, 1]:.3f}) on {fac}", at=V2[k].tolist(), thickness_mm=float(thick[k]),
                          t_min_mm=tmin, facet=fac, regions=regions, relief_mm=float(r[k]), chamfer_mm=float(c[k]),
                          base_thickness_mm=float(T[k]), defining_points=defining)
    V = np.zeros((2 * n, 3))
    V[:n, :2] = V2
    V[n:, :2] = V2
    V[:n, 2] = x_in + thick
    V[n:, 2] = x_in
    polys, kind, region, pface = [], [], [], []
    tris, tri_poly = [], []
    for fi, f in enumerate(arr.faces):
        reg = labels[fi] + ("" if chamf[fi] is None else f"/chamfer{chamf[fi]}")
        et = arr.face_tris[fi]
        po = len(polys)
        polys.append(list(f))
        kind.append("outer")
        region.append(reg)
        pface.append(fi)
        pi = len(polys)
        polys.append([n + v for v in f[::-1]])
        kind.append("inner")
        region.append("inner")
        pface.append(fi)
        for a, b, cc in et:
            tris.append((a, b, cc))
            tri_poly.append(po)
            tris.append((n + a, n + cc, n + b))
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
        e1, e2 = (2, 1) if nm == "cap_hinge" else (1, 2)       # (X, y) for the -d cap, (y, X) for the +d cap
        P2 = V[cap][:, [e1, e2]]
        if poly_area(P2) <= 0:
            raise TempleError("E_TRIANGULATE", f"{nm} polygon is not counter-clockwise seen from outside")
        for a, b, cc in ear_clip(P2, list(range(len(cap)))):
            tris.append((cap[a], cap[b], cap[cc]))
            tri_poly.append(pid)
    tris = np.asarray(tris, np.int64)
    tri_poly = np.asarray(tri_poly, np.int64)
    normals = np.array([_newell(V[p]) for p in polys])
    checks = _self_checks(V, polys, tris, tri_poly, kind, V2, arr)
    if not checks["closed"]:
        raise TempleError("E_NOT_CLOSED", "the assembled solid is not a closed oriented 2-manifold",
                          open_edges=checks["open_edges"], duplicate_directed_edges=checks["duplicate_directed_edges"])
    if checks["euler"] != 2 or checks["volume_mm3"] <= 0:
        raise TempleError("E_NOT_CLOSED", f"Euler characteristic {checks['euler']} (expected 2), volume "
                          f"{checks['volume_mm3']:.3f} mm^3", euler=checks["euler"], volume_mm3=checks["volume_mm3"])
    sharp, edge_info, warnings = _sharp_edges(ps, polys, kind, region, normals, V)
    flat = [i for i, (k, rg) in enumerate(zip(kind, region)) if k == "outer" and not rg.startswith("base")]
    tb = TempleBuild(spec=ps, arr=arr, sol=sol, face_label=labels, face_chamfer=chamf, V=V, polys=polys,
                     poly_kind=kind, poly_region=region, poly_face=pface, tris=tris, tri_poly=tri_poly, sharp=sharp,
                     flat_polys=flat, normals=normals, checks=checks, tables={}, warnings=list(sol.warnings) + warnings)
    tb.tables = _tables(tb, edge_info, thick, r, c)
    return tb


def _label_faces(ps: ParsedSpec, arr: Arrangement) -> tuple[list, list]:
    polys = {l: np.array([ps.nodes[n][:2] for n in f["poly"]]) for l, f in ps.facets.items()}
    for l, P in polys.items():
        if abs(poly_area(P)) <= TILE_TOL_MM2:
            raise TempleError("E_SPEC", f"facet {l}: its polygon has no area", facet=l)
        ok, bad = _simple(P)
        if not ok:
            raise TempleError("E_SPEC", f"facet {l}: its polygon crosses itself (edges {bad})", facet=l)
    labels, chamf = [], []
    area = {l: 0.0 for l in polys}
    for fi, p in enumerate(arr.interior_pts):
        inside = [l for l, P in polys.items() if point_in_poly(p, P)]
        if len(inside) > 1:
            raise TempleError("E_FACET_OVERLAP", f"facets {inside} overlap at (d, y) ({p[0]:.3f}, {p[1]:.3f})",
                              facets=inside, at=p.tolist())
        if inside and p[0] > ps.zone_end:
            raise TempleError("E_FACET_ON_BEND", f"facet {inside[0]} reaches d {p[0]:.3f} beyond zone_end_d "
                              f"{ps.zone_end:g} (facets only where the base is linear)", facet=inside[0],
                              at=p.tolist(), zone_end_d=ps.zone_end)
        lab = inside[0] if inside else "base"
        labels.append(lab)
        chamf.append(face_chamfer_of(ps, p))
        if inside:
            area[lab] += float(arr.areas[fi])
    out = [l for l, a in area.items() if a <= TILE_TOL_MM2]
    if out:
        raise TempleError("E_FACET_OUTSIDE", f"facets {out} lie outside the outline (no area inside it)", facets=out)
    return labels, chamf


def _simple(P):
    from .arrange import simple_polygon
    return simple_polygon(P)


def _newell(P: np.ndarray) -> np.ndarray:
    Q = P[np.r_[1:len(P), 0]]
    n = np.array([np.sum((P[:, 1] - Q[:, 1]) * (P[:, 2] + Q[:, 2])),
                  np.sum((P[:, 2] - Q[:, 2]) * (P[:, 0] + Q[:, 0])),
                  np.sum((P[:, 0] - Q[:, 0]) * (P[:, 1] + Q[:, 1]))])
    L = float(np.linalg.norm(n))
    return n / L if L > 0 else n


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


def _self_checks(V, polys, tris, tri_poly, kind, V2, arr) -> dict:
    topo = mesh_topology(polys)
    und = {(min(a, b), max(a, b)) for a, b in topo["directed"]}
    used = sorted({v for p in polys for v in p})
    chi = len(used) - len(und) + len(polys)
    a, b, c = V[tris[:, 0]], V[tris[:, 1]], V[tris[:, 2]]
    vol = float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6.0)
    plan = []
    for p in polys:
        P = V[p]
        nrm = _newell(P)
        ctr = P.mean(axis=0)
        plan.append(float(np.max(np.abs((P - ctr) @ nrm))) if len(p) > 3 else 0.0)
    outer = np.array([k == "outer" for k in kind])[tri_poly]
    t2 = tris[outer]
    q0, q1, q2 = V2[t2[:, 0]], V2[t2[:, 1]], V2[t2[:, 2]]
    ar2 = (q1[:, 0] - q0[:, 0]) * (q2[:, 1] - q0[:, 1]) - (q1[:, 1] - q0[:, 1]) * (q2[:, 0] - q0[:, 0])
    return {"closed": not topo["open"] and not topo["dup"], "open_edges": len(topo["open"]),
            "duplicate_directed_edges": len(topo["dup"]), "euler": int(chi), "volume_mm3": vol,
            "vertices": len(used), "edges": len(und), "polygons": len(polys), "triangles": int(len(tris)),
            "planarity_max_mm": float(max(plan)), "min_outer_tri_area2_mm2": float(ar2.min()),
            "self_intersection": "none: a graph over a simple outline with positive thickness"
                                 if float(ar2.min()) > 0 else "outer triangle with non-positive (d, y) area"}


def _dihedral(n1, n2) -> float:
    return math.degrees(math.acos(max(-1.0, min(1.0, float(n1 @ n2)))))


def _sharp_edges(ps, polys, kind, region, normals, V):
    topo = mesh_topology(polys)
    sharp, info, warnings = [], [], []
    crease_deg = ps.options["crease_sharp_deg"]
    kink = ps.options["kink_sharp_deg"]
    spread = 0.0
    for (a, b), p1 in topo["directed"].items():
        if a > b:
            continue
        p2 = topo["directed"][(b, a)]
        k1, k2 = kind[p1], kind[p2]
        dih = _dihedral(normals[p1], normals[p2])
        s = False
        if k1 == "outer" and k2 == "outer":
            l1, l2 = region[p1].split("/")[0], region[p2].split("/")[0]
            c1, c2 = region[p1].partition("/")[2], region[p2].partition("/")[2]
            if l1 != l2 and (l1 != "base" or l2 != "base"):
                s = True
                info.append(("crease", tuple(sorted((l1, l2))), a, b, p1, p2, dih))
            elif c1 != c2:
                s = dih >= crease_deg
                info.append(("chamfer", tuple(sorted((region[p1], region[p2]))), a, b, p1, p2, dih))
            elif c1 and dih >= crease_deg:                  # a fold inside a chamfer (its taper ends, outline knots)
                s = True
            else:
                spread = max(spread, dih)
        elif k1 == "inner" and k2 == "inner":
            s = False
        elif "outer" in (k1, k2) or "inner" in (k1, k2):
            s = True
        else:                                               # strip / cap junctions
            s = dih > kink
        if s:
            sharp.append((a, b))
    if spread > ARM_NORMAL_SPREAD_DEG:
        warnings.append({"code": "W_ARM_NORMAL_SPREAD", "deg": spread,
                         "message": f"smooth outer edges bend up to {spread:.1f} deg (> {ARM_NORMAL_SPREAD_DEG})"})
    return sorted(sharp), {"edges": info, "smooth_spread_deg": spread}, warnings


def _tables(tb: TempleBuild, edge_info: dict, thick, r, c) -> dict:
    ps, V, n = tb.spec, tb.V, tb.n2
    out = {}
    facets = []
    slope = ps.inner.slope(ps.d0, ps.zone_end)
    inner_out = np.array([-slope, 0.0, 1.0]) / math.hypot(slope, 1.0)      # minus the inner face normal
    for label in sorted(ps.facets):
        pl = tb.sol.planes[label]
        ids = [i for i, (k, rg) in enumerate(zip(tb.poly_kind, tb.poly_region)) if k == "outer" and rg == label]
        cids = [i for i, (k, rg) in enumerate(zip(tb.poly_kind, tb.poly_region))
                if k == "outer" and rg.startswith(label + "/")]
        area = sum(_area3(V[tb.polys[i]]) for i in ids)
        ch_area = sum(_area3(V[tb.polys[i]]) for i in cids)
        nrm = tb.normals[ids[0]] if ids else tb.normals[cids[0]]
        vs = sorted({v for i in ids for v in tb.polys[i]})
        # exactness: every vertex of the facet on X = x_base + r_plane
        Xp = ps.x_base(V[vs, 0]) + pl.r(V[vs, 0], V[vs, 1])
        facets.append({"label": label, "area_mm2": area, "chamfered_area_mm2": ch_area,
                       "normal_local": nrm.tolist(), "lean_deg": _dihedral(nrm, inner_out),
                       "plane": {"kind": pl.kind, "r_coef": [float(x) for x in pl.coef]},
                       "defining_points": pl.defining, "conditioning_deg_per_mm": pl.conditioning_deg_per_mm,
                       "planarity_mm": float(np.max(np.abs(V[vs, 2] - Xp))) if vs else 0.0})
    out["facets"] = facets
    pairs = {}
    for kind_, key, a, b, p1, p2, dih in edge_info["edges"]:
        rec = pairs.setdefault((kind_,) + key, {"kind": kind_, "regions": list(key), "length_mm": 0.0,
                                                 "dihedral_deg": [dih, dih], "ridge": 0.0, "valley": 0.0})
        L = float(np.linalg.norm(V[a] - V[b]))
        rec["length_mm"] += L
        rec["dihedral_deg"] = [min(rec["dihedral_deg"][0], dih), max(rec["dihedral_deg"][1], dih)]
        cen2 = V[tb.polys[p2]].mean(axis=0)
        s = float(tb.normals[p1] @ (cen2 - 0.5 * (V[a] + V[b])))
        rec["ridge" if s < 0 else "valley"] += L
    creases = []
    for rec in pairs.values():
        rec["shape"] = "ridge" if rec["ridge"] >= rec["valley"] else "valley"
        if rec["dihedral_deg"][1] < 1e-9:
            rec["shape"] = "flat"
        creases.append(rec)
        if rec["kind"] == "crease" and rec["dihedral_deg"][1] < FLAT_CREASE_DEG:
            tb.warnings.append({"code": "W_FLAT_CREASE", "regions": rec["regions"], "deg": rec["dihedral_deg"][1],
                                "message": f"crease {rec['regions']} bends only {rec['dihedral_deg'][1]:.2f} deg "
                                           f"(< {FLAT_CREASE_DEG})"})
    out["creases"] = sorted(creases, key=lambda r_: (r_["kind"], r_["regions"]))
    k0, k1 = int(np.argmin(thick)), int(np.argmax(thick))
    out["thickness"] = {"min_mm": float(thick[k0]), "min_at": tb.arr.V[k0].tolist(),
                        "max_mm": float(thick[k1]), "max_at": tb.arr.V[k1].tolist()}
    out["smooth_spread_deg"] = edge_info["smooth_spread_deg"]
    out["nodes_solved"] = tb.sol.node_r
    out["arrangement"] = {"vertices": int(n), "faces": len(tb.arr.faces), "stations": len(station_list(ps)),
                          "joined_chords": tb.arr.chords_added}
    return out


def _area3(P: np.ndarray) -> float:
    Q = np.roll(P, -1, axis=0)
    return 0.5 * float(np.linalg.norm(np.cross(P, Q).sum(axis=0)))
