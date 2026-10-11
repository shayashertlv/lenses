"""Facet planes by exact propagation in world X (plan/WP6_FACETS.md §2.2 step 1). numpy only.

Every facet carries a plane X = a + b d + c y (X = world x of the +X temple, mm). Known points:

- nodes with a given value: a number t (X = x_in(d) + t) or "base" (X = the base surface at (d, y)); a node may lie
  outside the outline (a virtual node);
- values a solved facet hands to the arrangement vertices it shares with other facets. A null (solved) node gets the
  value of the first facet that solves it (reported); it binds another facet only through the arrangement vertex it
  sits on, so a shape-only node outside the outline never couples two facets.

Base-border vertices are never known points (v1 seeded them with r = 0; v2 deletes that): where a facet meets the base,
the border band ramp of build.py absorbs any mismatch.

A facet's plane comes from its crease + rise (`{crease: [n1, n2], rise_per_mm: s}`: the plane holds the 3D crease line
and gains s mm of X per mm of (d, y) distance from the crease into the facet) or from the three known points spanning
the widest triangle. Every other known point must lie on the plane within planar_tol_mm, else the facet is refused:
E_FACET_NOT_PLANAR for its own given nodes, E_RELIEF_STEP for a value a neighbour facet set. Facets are visited in
label order, repeated to a fixed point; a facet still without a plane is E_FACET_UNDERDETERMINED.

Conditioning = the largest tilt change (deg) of the facet per mm of error in one defining value:
> ILL_CONDITIONED_DEG_PER_MM refuses, > CONDITIONING_WARN_DEG_PER_MM warns.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .arrange import poly_area
from .spec import COLLINEAR_AREA_MM2, CONDITIONING_WARN_DEG_PER_MM, ILL_CONDITIONED_DEG_PER_MM, TempleError

NODE_ON_VERTEX_MM = 1e-6


@dataclass
class FacetPlane:
    label: str
    coef: np.ndarray                 # (a, b, c): X = a + b d + c y
    kind: str                        # "nodes" | "crease"
    defining: list                   # [{"node"?, "d", "y", "X", "source"}]
    conditioning_deg_per_mm: float
    crease: dict | None = None       # crease planes: {"nodes", "rise_per_mm", "n_in", "mid"}

    def X(self, d, y):
        return self.coef[0] + self.coef[1] * np.asarray(d) + self.coef[2] * np.asarray(y)


@dataclass
class PlaneSolution:
    planes: dict                     # label -> FacetPlane
    X_vertex: np.ndarray             # (n,) plane X at every facet vertex of the arrangement (nan elsewhere)
    source_vertex: list              # provenance of each vertex value
    node_X: dict                     # node name -> X (given and solved)
    node_vertex: dict                # node name -> vertex id (nodes on an arrangement vertex)
    warnings: list = field(default_factory=list)
    order: list = field(default_factory=list)


def _src(s) -> str:
    kind, who = s
    return kind if who is None else f"{kind} {who}"


def ccw_poly(names: list, nodes: dict) -> list:
    P = np.array([nodes[n][:2] for n in names], np.float64)
    return list(names) if poly_area(P) > 0 else list(names)[::-1]


def crease_frame(n1, n2, interior_left: bool) -> tuple[np.ndarray, np.ndarray, float]:
    """(unit direction n1 -> n2, unit normal into the facet, length) of a crease in (d, y)."""
    p1, p2 = np.asarray(n1[:2], np.float64), np.asarray(n2[:2], np.float64)
    v = p2 - p1
    L = float(np.hypot(*v))
    u = v / L
    nl = np.array([-u[1], u[0]])
    return u, (nl if interior_left else -nl), L


def crease_plane(n1, n2, X1, X2, rise, interior_left: bool) -> tuple[np.ndarray, float]:
    """Plane holding the crease line (n1, X1) -> (n2, X2) and gaining `rise` mm of X per mm into the facet.
    Returns (coef, conditioning deg/mm)."""
    u, n_in, L = crease_frame(n1, n2, interior_left)
    p1 = np.asarray(n1[:2], np.float64)
    grad = (X2 - X1) / L * u + rise * n_in
    a = X1 - float(grad @ p1)
    return np.array([a, grad[0], grad[1]]), math.degrees(math.atan(1.0 / L))


def interior_left_of(poly: list, n1: str, n2: str, nodes: dict) -> bool:
    """True when the facet lies on the left of n1 -> n2 (poly taken counter-clockwise)."""
    p = ccw_poly(poly, nodes)
    return p[(p.index(n1) + 1) % len(p)] == n2


def solve(ps, arr, face_label: list) -> PlaneSolution:
    """Planes for every facet of the arrangement (`face_label[i]` = facet label or 'base' of face i)."""
    V = arr.V
    tol = ps.options["planar_tol_mm"]
    n = len(V)
    labels = sorted({l for l in face_label if l != "base"})
    fverts = {l: sorted({v for fi, f in enumerate(arr.faces) if face_label[fi] == l for v in f}) for l in labels}
    node_vertex = {}
    for name, (d, y, _) in ps.nodes.items():
        dist = np.hypot(V[:, 0] - d, V[:, 1] - y)
        k = int(np.argmin(dist)) if n else -1
        if n and dist[k] <= NODE_ON_VERTEX_MM:
            node_vertex[name] = k
    vr: dict[int, tuple[float, tuple]] = {}
    nr: dict[str, tuple[float, tuple]] = {}
    for name in ps.nodes:
        X = ps.node_X(name)
        if X is None:
            continue
        nr[name] = (X, ("node", name))
        v = node_vertex.get(name)
        if v is not None:
            if v in vr and abs(vr[v][0] - X) > tol:
                raise TempleError("E_SPEC", f"nodes {vr[v][1][1]} and {name} sit at the same point with different "
                                  f"values (X {vr[v][0]:g} vs {X:g})", nodes=[vr[v][1][1], name])
            vr[v] = (X, ("node", name))
    planes: dict[str, FacetPlane] = {}
    order, warnings = [], []
    changed = True
    while changed:
        changed = False
        for label in labels:
            if label in planes:
                continue
            fac = ps.facets[label]
            poly = ccw_poly(fac["poly"], ps.nodes)
            pts = []                                   # (d, y, X, source, id)
            for name in poly:                         # GIVEN nodes only: a solved (null) node off the outline
                if name in nr and nr[name][1][0] == "node":   # binds nothing; on the outline it is a vertex
                    d, y, _ = ps.nodes[name]
                    pts.append((d, y, nr[name][0], nr[name][1], ("node", name)))
            for v in fverts[label]:
                if v in vr:
                    pts.append((float(V[v, 0]), float(V[v, 1]), vr[v][0], vr[v][1], ("vertex", v)))
            pts = _dedupe(pts, tol, label)
            plane = fac["plane"]
            crease = None
            if plane != "nodes":
                n1, n2 = plane["crease"]
                if n1 not in nr or n2 not in nr:
                    continue
                left = poly[(poly.index(n1) + 1) % len(poly)] == n2
                coef, cond = crease_plane(ps.nodes[n1], ps.nodes[n2], nr[n1][0], nr[n2][0], plane["rise_per_mm"], left)
                u, n_in, _ = crease_frame(ps.nodes[n1], ps.nodes[n2], left)
                mid = 0.5 * (np.asarray(ps.nodes[n1][:2]) + np.asarray(ps.nodes[n2][:2]))
                crease = {"nodes": [n1, n2], "rise_per_mm": plane["rise_per_mm"], "n_in": n_in.tolist(),
                          "mid": mid.tolist()}
                defining = [_pt(ps.nodes[n1][:2], nr[n1][0], nr[n1][1], ("node", n1)),
                            _pt(ps.nodes[n2][:2], nr[n2][0], nr[n2][1], ("node", n2))]
                kind = "crease"
                def_keys = {("node", n1), ("node", n2)}
            else:
                if len(pts) < 3:
                    continue
                tri = _widest(pts)
                if tri is None:
                    continue
                M = np.array([[1.0, pts[k][0], pts[k][1]] for k in tri])
                coef = np.linalg.solve(M, np.array([pts[k][2] for k in tri]))
                Minv = np.linalg.inv(M)
                cond = max(math.degrees(math.atan(math.hypot(Minv[1, j], Minv[2, j]))) for j in range(3))
                defining = [_pt(pts[k][:2], pts[k][2], pts[k][3], pts[k][4]) for k in tri]
                kind = "nodes"
                def_keys = {pts[k][4] for k in tri}
            misfits = []
            for d, y, X, src, key in pts:
                m = X - float(coef[0] + coef[1] * d + coef[2] * y)
                if abs(m) > tol and key not in def_keys:
                    misfits.append({"at": [d, y], "known_X": X, "plane_X": X - m, "misfit_mm": m,
                                    "source": _src(src)})
            if misfits:
                own = [m for m in misfits if m["source"].startswith("node ") and m["source"][5:] in fac["poly"]]
                if kind == "nodes":                    # the facet's own nodes alone: coplanar or not
                    own_pts = [p for p in pts if p[3][0] == "node"]
                    code = "E_RELIEF_STEP" if _coplanar(own_pts, tol) else "E_FACET_NOT_PLANAR"
                else:
                    code = "E_FACET_NOT_PLANAR" if own and len(own) == len(misfits) else "E_RELIEF_STEP"
                worst = max(misfits, key=lambda m: abs(m["misfit_mm"]))
                fields = {"facet": label, "misfits": misfits, "defining_points": defining}
                if code == "E_RELIEF_STEP":
                    nb = sorted({_src(p[3])[6:] for p in pts if p[3][0] == "facet"})
                    fields["neighbours"] = nb
                    what = (f"cannot meet facet(s) {nb} without a step: {worst['misfit_mm']:+.3g} mm at (d, y) "
                            f"({worst['at'][0]:.3f}, {worst['at'][1]:.3f}) ({worst['source']})")
                else:
                    what = (f"has {len(pts)} known heights that are not coplanar (worst {worst['misfit_mm']:+.3g} mm "
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
            planes[label] = FacetPlane(label, coef, kind, defining, cond, crease)
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
        known = [{"node": name, "X": nr[name][0]} for name in fac["poly"] if name in nr]
        known += [{"at": V[v].tolist(), "X": vr[v][0], "source": _src(vr[v][1])} for v in fverts[l] if v in vr]
        why = ("its crease nodes have no value" if fac["plane"] != "nodes"
               else "it has fewer than 3 non-collinear known values (base borders are never known points)")
        raise TempleError("E_FACET_UNDERDETERMINED", f"facet {l}: {why}", facet=l, facets=missing, known=known)
    X_vertex = np.full(n, np.nan)
    src = [None] * n
    for v, (X, s) in vr.items():
        X_vertex[v] = X
        src[v] = _src(s)
    return PlaneSolution(planes=planes, X_vertex=X_vertex, source_vertex=src,
                         node_X={k: v[0] for k, v in sorted(nr.items())}, node_vertex=node_vertex,
                         warnings=warnings, order=order)


def _pt(dy, X, src, key) -> dict:
    out = {"d": float(dy[0]), "y": float(dy[1]), "X": float(X), "source": _src(src)}
    if key[0] == "node":
        out["node"] = key[1]
    return out


def _dedupe(pts, tol, label):
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
            code = "E_RELIEF_STEP" if "facet" in (dup[3][0], p[3][0]) else "E_FACET_NOT_PLANAR"
            raise TempleError(code, f"facet {label}: two values meet at (d, y) ({p[0]:.3f}, {p[1]:.3f}): "
                              f"{dup[2]:g} ({_src(dup[3])}) vs {p[2]:g} ({_src(p[3])})", facet=label,
                              at=[p[0], p[1]], values=[dup[2], p[2]], sources=[_src(dup[3]), _src(p[3])])
    return out


def _coplanar(pts, tol: float) -> bool:
    """True when the given points (d, y, X, ...) lie on one plane within tol (fewer than 4: always)."""
    if len(pts) < 4:
        return True
    tri = _widest(pts)
    if tri is None:
        return True
    M = np.array([[1.0, pts[k][0], pts[k][1]] for k in tri])
    coef = np.linalg.solve(M, np.array([pts[k][2] for k in tri]))
    return all(abs(p[2] - (coef[0] + coef[1] * p[0] + coef[2] * p[1])) <= tol for p in pts)


def _widest(pts) -> tuple | None:
    """Indices (i < j < k) of the known points spanning the largest triangle (the first in index order on ties);
    None when every triple is collinear."""
    P0 = np.array([p[:2] for p in pts], np.float64)
    if len(P0) < 3:
        return None
    keep = _hull(P0)                                               # the widest triangle has its corners on the hull
    P = P0[keep]
    m = len(P)
    if m < 3:
        return None
    E = P[None, :, :] - P[:, None, :]                              # E[i, j] = P_j - P_i
    A = 0.5 * np.abs(E[:, :, None, 0] * E[:, None, :, 1] - E[:, :, None, 1] * E[:, None, :, 0])
    i, j, k = np.ogrid[:m, :m, :m]
    A = np.where((i < j) & (j < k), A, -1.0)
    flat = int(np.argmax(A))
    if A.flat[flat] <= COLLINEAR_AREA_MM2:
        return None
    return tuple(int(keep[x]) for x in np.unravel_index(flat, A.shape))


def _hull(P: np.ndarray) -> list:
    """Sorted indices of the points on the 2D convex hull (monotone chain; collinear boundary points kept)."""
    order = sorted(range(len(P)), key=lambda i: (P[i, 0], P[i, 1]))

    def half(idx):
        out = []
        for i in idx:
            while len(out) >= 2:
                o, a = P[out[-2]], P[out[-1]]
                if (a[0] - o[0]) * (P[i, 1] - o[1]) - (a[1] - o[1]) * (P[i, 0] - o[0]) < 0:
                    out.pop()
                else:
                    break
            out.append(i)
        return out
    return sorted(set(half(order)) | set(half(order[::-1])))
