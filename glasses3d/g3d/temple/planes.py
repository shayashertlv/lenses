"""Facet planes by exact propagation (WP6_DESIGN.md §3 construction step 3). numpy only.

Every facet carries a relief plane r(d, y) = a + b d + c y (mm outward from the base surface); its outer surface is
X = x_in(d) + T(d) + r, a plane wherever the base is linear (the facet zone). Known values:

- nodes with a given r (a node may lie outside the outline: a virtual node such as Prada's C);
- every arrangement vertex on the border of a base region: r = 0 (the rest of the zone and the arm are base, so a
  facet meets the base without a step only along its own zero line);
- values a solved facet hands to its arrangement vertices. A null (solved) node gets the value of the first facet
  that solves it (reported); it binds another facet only through the arrangement vertex it sits on, so a shape-only
  node outside the outline (a polygon corner beyond the edge) never couples two facets.

A facet's plane comes from its crease + rise (`{crease: [n1, n2], rise_per_mm: s}`: the plane holds the 3D crease
line and gains s mm of relief per mm of (d, y) distance from the crease into the facet) or from the three known points
spanning the widest triangle. Every other known point must lie on the plane within planar_tol_mm, else the facet is
refused: E_FACET_NOT_PLANAR for its own given nodes, E_RELIEF_STEP for a value a neighbour (or the base) set. Facets
are visited in label order, repeated to a fixed point; a facet still without a plane is E_FACET_UNDERDETERMINED.

Conditioning = the largest tilt change (deg) of the facet per mm of error in one defining value (base zeros are
exact): > ILL_CONDITIONED_DEG_PER_MM refuses, > CONDITIONING_WARN_DEG_PER_MM warns.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .arrange import poly_area
from .spec import (COLLINEAR_AREA_MM2, CONDITIONING_WARN_DEG_PER_MM, ILL_CONDITIONED_DEG_PER_MM, TempleError)

NODE_ON_VERTEX_MM = 1e-6


@dataclass
class FacetPlane:
    label: str
    coef: np.ndarray                 # (a, b, c): r = a + b d + c y
    kind: str                        # "nodes" | "crease"
    defining: list                   # [{"name"/"vertex", "d", "y", "r", "source"}]
    conditioning_deg_per_mm: float

    def r(self, d, y):
        return self.coef[0] + self.coef[1] * np.asarray(d) + self.coef[2] * np.asarray(y)


@dataclass
class PlaneSolution:
    planes: dict                     # label -> FacetPlane
    r_vertex: np.ndarray             # (n,) relief at every arrangement vertex (nan: not on any region)
    source_vertex: list              # provenance of each vertex value
    node_r: dict                     # node name -> solved r (given ones too)
    node_vertex: dict                # node name -> vertex id (nodes on an arrangement vertex)
    warnings: list = field(default_factory=list)
    order: list = field(default_factory=list)


def _src(s) -> str:
    kind, who = s
    return kind if who is None else f"{kind} {who}"


def ccw_poly(names: list, nodes: dict) -> list:
    P = np.array([nodes[n][:2] for n in names], np.float64)
    return list(names) if poly_area(P) > 0 else list(names)[::-1]


def crease_plane(n1, n2, r1, r2, rise, interior_left: bool) -> tuple[np.ndarray, float]:
    """Relief plane holding the crease line (n1, r1) -> (n2, r2) and gaining `rise` mm per mm into the facet.
    Returns (coef, conditioning deg/mm)."""
    p1, p2 = np.asarray(n1[:2], np.float64), np.asarray(n2[:2], np.float64)
    v = p2 - p1
    L = float(np.hypot(*v))
    u = v / L
    nl = np.array([-u[1], u[0]])
    n_in = nl if interior_left else -nl
    grad = (r2 - r1) / L * u + rise * n_in
    a = r1 - float(grad @ p1)
    return np.array([a, grad[0], grad[1]]), math.degrees(math.atan(1.0 / L))


def solve(ps, arr, face_label: list) -> PlaneSolution:
    """Planes for every facet of the arrangement (`face_label[i]` = facet label or 'base' of face i)."""
    V = arr.V
    tol = ps.options["planar_tol_mm"]
    n = len(V)
    labels = sorted({l for l in face_label if l != "base"})
    fverts = {l: sorted({v for fi, f in enumerate(arr.faces) if face_label[fi] == l for v in f}) for l in labels}
    base_v = sorted({v for fi, f in enumerate(arr.faces) if face_label[fi] == "base" for v in f})
    # nodes sitting on arrangement vertices
    node_vertex = {}
    for name, (d, y, _) in ps.nodes.items():
        dist = np.hypot(V[:, 0] - d, V[:, 1] - y)
        k = int(np.argmin(dist)) if n else -1
        if n and dist[k] <= NODE_ON_VERTEX_MM:
            node_vertex[name] = k
    vr: dict[int, tuple[float, tuple]] = {}
    nr: dict[str, tuple[float, tuple]] = {}
    for name, (d, y, r) in ps.nodes.items():
        if r is None:
            continue
        nr[name] = (float(r), ("node", name))
        v = node_vertex.get(name)
        if v is not None:
            if v in vr and abs(vr[v][0] - r) > tol:
                raise TempleError("E_SPEC", f"nodes {vr[v][1][1]} and {name} sit at the same point with different "
                                  f"relief ({vr[v][0]:g} vs {r:g})", nodes=[vr[v][1][1], name])
            vr[v] = (float(r), ("node", name))
    for v in base_v:
        if v in vr and abs(vr[v][0]) > tol:
            name = vr[v][1][1]
            raise TempleError("E_RELIEF_STEP", f"node {name} at (d, y) ({V[v, 0]:.3f}, {V[v, 1]:.3f}) borders the "
                              f"base (r = 0) but has r {vr[v][0]:g}: the surface would step there", node=name,
                              at=V[v].tolist(), r=vr[v][0], facet="base")
        vr[v] = (0.0, ("base", None))
    planes: dict[str, FacetPlane] = {}
    order = []
    warnings = []
    changed = True
    while changed:
        changed = False
        for label in labels:
            if label in planes:
                continue
            fac = ps.facets[label]
            poly = ccw_poly(fac["poly"], ps.nodes)
            pts = []                                   # (d, y, r, source, id)
            for name in poly:                         # GIVEN nodes only: a solved (null) node off the outline
                if name in nr and nr[name][1][0] == "node":   # binds nothing; on the outline it is a vertex
                    d, y, _ = ps.nodes[name]
                    pts.append((d, y, nr[name][0], nr[name][1], ("node", name)))
            for v in fverts[label]:
                if v in vr:
                    pts.append((float(V[v, 0]), float(V[v, 1]), vr[v][0], vr[v][1], ("vertex", v)))
            pts = _dedupe(pts, tol, label, V)
            plane = fac["plane"]
            if plane != "nodes":
                n1, n2 = plane["crease"]
                if n1 not in nr or n2 not in nr:
                    continue
                i1 = poly.index(n1)
                interior_left = poly[(i1 + 1) % len(poly)] == n2
                coef, cond = crease_plane(ps.nodes[n1], ps.nodes[n2], nr[n1][0], nr[n2][0], plane["rise_per_mm"],
                                          interior_left)
                defining = [_pt(ps.nodes[n1][:2], nr[n1][0], nr[n1][1], n1),
                            _pt(ps.nodes[n2][:2], nr[n2][0], nr[n2][1], n2)]
                kind = "crease"
                def_keys = {("node", n1), ("node", n2)}
            else:
                if len(pts) < 3:
                    continue
                tri = _widest(pts)
                if tri is None:
                    continue
                M = np.array([[1.0, pts[k][0], pts[k][1]] for k in tri])
                rr = np.array([pts[k][2] for k in tri])
                coef = np.linalg.solve(M, rr)
                Minv = np.linalg.inv(M)
                cond = 0.0
                for j, k in enumerate(tri):
                    if pts[k][3][0] == "base":
                        continue
                    cond = max(cond, math.degrees(math.atan(math.hypot(Minv[1, j], Minv[2, j]))))
                defining = [_pt(pts[k][:2], pts[k][2], pts[k][3], pts[k][4]) for k in tri]
                kind = "nodes"
                def_keys = {pts[k][4] for k in tri}
            misfits = []
            for d, y, r, src, key in pts:
                m = r - float(coef[0] + coef[1] * d + coef[2] * y)
                if abs(m) > tol and key not in def_keys:
                    misfits.append({"at": [d, y], "known_r": r, "plane_r": r - m, "misfit_mm": m,
                                    "source": _src(src)})
            if misfits:
                own = [m for m in misfits if m["source"].startswith("node ")
                       and m["source"][5:] in fac["poly"]]
                code = "E_FACET_NOT_PLANAR" if own and len(own) == len(misfits) else "E_RELIEF_STEP"
                worst = max(misfits, key=lambda m: abs(m["misfit_mm"]))
                fields = {"facet": label, "misfits": misfits, "defining_points": defining}
                if code == "E_RELIEF_STEP":
                    fields["zero_line"] = zero_line(coef, worst["at"])
                    what = (f"meets {worst['source']} with a {worst['misfit_mm']:+.4f} mm step at (d, y) "
                            f"({worst['at'][0]:.3f}, {worst['at'][1]:.3f})")
                else:
                    what = (f"has {len(pts)} known heights that are not coplanar (worst {worst['misfit_mm']:+.4f} mm "
                            f"at {worst['source']})")
                raise TempleError(code, f"facet {label} {what}", **fields)
            if cond > ILL_CONDITIONED_DEG_PER_MM:
                raise TempleError("E_ILL_CONDITIONED", f"facet {label}: a 1 mm error in one defining value tilts it "
                                  f"{cond:.1f} deg (> {ILL_CONDITIONED_DEG_PER_MM}); use defining points farther "
                                  f"apart or a crease + rise plane", facet=label, deg_per_mm=cond,
                                  defining_points=defining)
            if cond > CONDITIONING_WARN_DEG_PER_MM:
                warnings.append({"code": "W_CONDITIONING", "facet": label, "deg_per_mm": cond,
                                 "message": f"facet {label} tilts {cond:.1f} deg per mm of node error"})
            planes[label] = FacetPlane(label, coef, kind, defining, cond)
            order.append(label)
            for v in fverts[label]:
                if v not in vr:
                    vr[v] = (float(coef[0] + coef[1] * V[v, 0] + coef[2] * V[v, 1]), ("facet", label))
            for name in poly:
                if name not in nr:
                    d, y, _ = ps.nodes[name]
                    nr[name] = (float(coef[0] + coef[1] * d + coef[2] * y), ("facet", label))
                    v = node_vertex.get(name)
                    if v is not None and v not in vr:
                        vr[v] = (nr[name][0], ("facet", label))
            changed = True
    missing = [l for l in labels if l not in planes]
    if missing:
        l = missing[0]
        fac = ps.facets[l]
        known = []
        for name in fac["poly"]:
            if name in nr:
                known.append({"node": name, "r": nr[name][0]})
        for v in fverts[l]:
            if v in vr:
                known.append({"at": V[v].tolist(), "r": vr[v][0], "source": _src(vr[v][1])})
        why = ("its crease nodes have no relief" if fac["plane"] != "nodes"
               else "it has fewer than 3 non-collinear known heights")
        raise TempleError("E_FACET_UNDERDETERMINED", f"facet {l}: {why}", facet=l, facets=missing, known=known)
    r_vertex = np.full(n, np.nan)
    src = [None] * n
    for v, (r, s) in vr.items():
        r_vertex[v] = r
        src[v] = _src(s)
    return PlaneSolution(planes=planes, r_vertex=r_vertex, source_vertex=src,
                         node_r={k: v[0] for k, v in sorted(nr.items())}, node_vertex=node_vertex,
                         warnings=warnings, order=order)


def _pt(dy, r, src, key) -> dict:
    out = {"d": float(dy[0]), "y": float(dy[1]), "r": float(r), "source": _src(src)}
    if key[0] == "node":
        out["node"] = key[1]
    return out


def _dedupe(pts, tol, label, V):
    """One entry per position; two known values at one position must agree."""
    out = []
    for p in pts:
        dup = None
        for q in out:
            if abs(p[0] - q[0]) <= NODE_ON_VERTEX_MM and abs(p[1] - q[1]) <= NODE_ON_VERTEX_MM:
                dup = q
                break
        if dup is None:
            out.append(p)
        elif abs(dup[2] - p[2]) > tol:
            code = "E_RELIEF_STEP" if "base" in (dup[3][0], p[3][0]) or "facet" in (dup[3][0], p[3][0]) \
                else "E_FACET_NOT_PLANAR"
            raise TempleError(code, f"facet {label}: two values meet at (d, y) ({p[0]:.3f}, {p[1]:.3f}): "
                              f"{dup[2]:g} ({_src(dup[3])}) vs {p[2]:g} ({_src(p[3])})", facet=label,
                              at=[p[0], p[1]], values=[dup[2], p[2]], sources=[_src(dup[3]), _src(p[3])])
    return out


def _widest(pts) -> tuple | None:
    best, bt = COLLINEAR_AREA_MM2, None
    m = len(pts)
    for i in range(m):
        for j in range(i + 1, m):
            for k in range(j + 1, m):
                a, b, c = pts[i], pts[j], pts[k]
                ar = 0.5 * abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]))
                if ar > best * (1 + 1e-12):
                    best, bt = ar, (i, j, k)
    return bt


def zero_line(coef, near) -> dict:
    """The facet's r = 0 line in (d, y): the point nearest `near` and the unit direction. A facet borders the base
    without a step only along this line."""
    a, b, c = (float(x) for x in coef)
    g = math.hypot(b, c)
    if g < 1e-15:
        return {"point": None, "direction": None, "note": "the facet plane is parallel to the base (r constant)"}
    p = np.asarray(near, np.float64)
    rp = a + b * p[0] + c * p[1]
    q = p - rp * np.array([b, c]) / (g * g)
    return {"point": [float(q[0]), float(q[1])], "direction": [float(c / g), float(-b / g)]}


def zero_line_point(coef, d_or_chain, which: str = "top", ps=None):
    """Where the facet's zero line meets an outline chain (`ps.top` / `ps.bottom`): the (d, y) a node must take so
    the facet meets the base there without a step (a helper for spec writers; the tool never moves a node)."""
    chain = ps.top if which == "top" else ps.bottom
    a, b, c = (float(x) for x in coef)
    f = a + b * chain[:, 0] + c * chain[:, 1]
    out = []
    for i in range(len(chain) - 1):
        if f[i] == 0:
            out.append(chain[i].tolist())
        elif f[i] * f[i + 1] < 0:
            t = f[i] / (f[i] - f[i + 1])
            out.append((chain[i] + t * (chain[i + 1] - chain[i])).tolist())
    return out
