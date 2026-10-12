"""Facet surfaces by exact propagation in world X (plan/WP6_FACETS.md §2.2 step 1; WP6_V3.md §2). numpy only.

Every facet carries a surface object with X(d, y), grad(d, y) -> (dX/dd, dX/dy), kind ("nodes" | "crease" | "coef" |
"warp"), coef and defining. The flat kinds (FacetPlane) are the plane X = a + b d + c y (X = world x of the +X temple,
mm): "nodes" and "crease" as below; "coef" (v3: the fit's output) is the given plane itself, every known point must lie
on it (no conditioning check). A "warp" facet (v3, WarpSurface) is the mean value coordinates interpolation of its
corner values (mvc): it waits until every corner has a value (given, "base", or handed on by another facet at the
arrangement vertex the corner sits on: a solved corner outside the outline never has one) and is then exact at its
corners, linear along its edges (straight 3D edges: a neighbour that shares the corner values meets it without a step),
smooth inside, and the plane when the corner values are coplanar. Its `coef` is the least-squares plane through the
corners (display only: lean, charts); exact values always come from X(). Known points:

- nodes with a given value: a number t (X = x_in(d) + t) or "base" (X = the base surface at (d, y)); a node may lie
  outside the outline (a virtual node);
- values a solved facet hands to the arrangement vertices it shares with other facets. A null (solved) node gets the
  value of the first facet that solves it (reported); it binds another facet only through the arrangement vertex it
  sits on, so a shape-only node outside the outline never couples two facets.

Base-border vertices are never known points (v1 seeded them with r = 0; v2 deletes that): where a facet meets the base,
the border band ramp of build.py absorbs any mismatch.

A facet's plane comes from its crease + rise (`{crease: [n1, n2], rise_per_mm: s}`: the plane holds the 3D crease line
n1-n2, an edge of the facet or the ends of a straight run of its edges (spec.crease_run), and gains s mm of X per mm of
(d, y) distance from that line into the facet), from its coef, or from the three known points spanning
the widest triangle. Every other known point must lie on the surface within planar_tol_mm, else the facet is refused:
E_FACET_NOT_PLANAR for its own given nodes, E_RELIEF_STEP for a value a neighbour facet set (on a warp facet every
misfit is E_RELIEF_STEP: its own corners define it exactly, so a known value on its boundary that is not a corner must
equal the edge's linear interpolation). Facets are visited in label order, repeated to a fixed point; a facet still
without a surface is E_FACET_UNDERDETERMINED.

Conditioning (nodes and crease planes only) = the largest tilt change (deg) of the facet per mm of error in one
defining value: > ILL_CONDITIONED_DEG_PER_MM refuses, > CONDITIONING_WARN_DEG_PER_MM warns.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .arrange import poly_area
from .spec import (COLLINEAR_AREA_MM2, CONDITIONING_WARN_DEG_PER_MM, ILL_CONDITIONED_DEG_PER_MM, TempleError,
                   crease_run, plane_kind)

NODE_ON_VERTEX_MM = 1e-6
MVC_VERTEX_MM = 1e-12              # a point this close to a warp corner takes the corner's value exactly
MVC_EDGE_SIN = 1e-12               # ... and one whose edge subtends sin(angle) below this (angle ~180 deg) the edge's
#                                    linear interpolation (the general formula's tan(angle / 2) is infinite there)
GRAD_H_MM = 1e-5                   # warp gradients: central differences (MVC is C-infinity off the corners, also
#                                    across the edges: Hormann & Floater 2006), h shrunk to r / 10 within 1e-4 of one
CORNER_AT_MM = 1e-6                # a gradient asked within this of a warp corner takes the corner's (corner_grad)
CORNER_RAYS = 32                   # corner_grad: rays over the corner's interior angle (mid-angles) ...
CORNER_RAY_MM = 1e-4               # ... this far from the corner (the cone's limit; MVC adds O(r) to its slope)


@dataclass
class FacetPlane:
    """A flat facet surface: X = a + b d + c y."""
    label: str
    coef: np.ndarray                 # (a, b, c): X = a + b d + c y
    kind: str                        # "nodes" | "crease" | "coef"
    defining: list                   # [{"node"?, "d", "y", "X", "source"}] ([] for coef: the plane is given)
    conditioning_deg_per_mm: float   # 0.0 for coef (no conditioning check)
    crease: dict | None = None       # crease planes: {"nodes", "rise_per_mm", "n_in", "mid"}

    def X(self, d, y):
        return self.coef[0] + self.coef[1] * np.asarray(d) + self.coef[2] * np.asarray(y)

    def grad(self, d, y) -> tuple[np.ndarray, np.ndarray]:
        """(dX/dd, dX/dy) at the points (constant)."""
        shp = np.broadcast(np.asarray(d), np.asarray(y)).shape
        return np.full(shp, float(self.coef[1])), np.full(shp, float(self.coef[2]))


def mvc(P, F, d, y) -> np.ndarray:
    """Mean value coordinates interpolation (Floater 2003) of the values F (n,) at the corners P (n, 2) of a simple
    polygon (CCW), at the points (d, y), in Hormann & Floater's (2006) form: with s_i = P_i - x, r_i = |s_i|, A_i =
    det(s_i, s_i+1) / 2, D_i = s_i . s_i+1 and t_i = tan(alpha_i / 2) = 2 A_i / (r_i r_i+1 + D_i) (D_i > 0) or
    (r_i r_i+1 - D_i) / (2 A_i), the weights w_i = (t_i-1 + t_i) / r_i give X = sum w_i F_i / sum w_i. Exact at a corner
    (r_i <= MVC_VERTEX_MM) and linear on an edge (sin alpha_i <= MVC_EDGE_SIN, D_i < 0): straight 3D edges. Smooth
    (C-infinity) everywhere but at the corners, across the edges too; outside the polygon it is that smooth extension,
    meaningful only near the polygon (sum w_i can vanish far from it)."""
    P = np.asarray(P, np.float64)
    F = np.asarray(F, np.float64)
    d, y = np.asarray(d, np.float64), np.asarray(y, np.float64)
    shp = np.broadcast(d, y).shape
    x = np.stack([np.broadcast_to(d, shp).ravel(), np.broadcast_to(y, shp).ravel()], axis=1)
    S = P[None, :, :] - x[:, None, :]
    r = np.hypot(S[..., 0], S[..., 1])
    S1, r1 = np.roll(S, -1, axis=1), np.roll(r, -1, axis=1)
    A = 0.5 * (S[..., 0] * S1[..., 1] - S[..., 1] * S1[..., 0])
    D = np.einsum("mnk,mnk->mn", S, S1)
    at_v = r <= MVC_VERTEX_MM
    on_e = (np.abs(2.0 * A) <= MVC_EDGE_SIN * r * r1) & (D < 0) & ~at_v & ~np.roll(at_v, -1, axis=1)
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        t = np.where(D > 0, 2.0 * A / (r * r1 + D), (r * r1 - D) / (2.0 * A))
        w = (np.roll(t, 1, axis=1) + t) / r
        out = (w * F[None]).sum(axis=1) / w.sum(axis=1)
    he = on_e.any(axis=1)
    if he.any():
        k = np.argmax(on_e[he], axis=1)
        rr, rr1 = r[he, k], r1[he, k]
        out[he] = (rr1 * F[k] + rr * F[(k + 1) % len(F)]) / (rr + rr1)
    hv = at_v.any(axis=1)
    if hv.any():
        out[hv] = F[np.argmax(at_v[hv], axis=1)]
    return out.reshape(shp)


@dataclass
class WarpSurface:
    """A "warp" facet (spec "warp"): X = mvc over its corners (CCW), corners = [(name, d, y, X)] in that order; coef =
    the least-squares plane through the corners (display only: lean, charts), warp_mm = the largest corner distance
    from it (along X)."""
    label: str
    corners: list
    coef: np.ndarray
    defining: list
    warp_mm: float
    kind: str = "warp"
    conditioning_deg_per_mm: float = 0.0
    crease: dict | None = None

    def __post_init__(self):
        self.P = np.array([[c[1], c[2]] for c in self.corners], np.float64)
        self.F = np.array([c[3] for c in self.corners], np.float64)

    def X(self, d, y):
        return mvc(self.P, self.F, d, y)

    def corner_grad(self, k: int) -> np.ndarray:
        """The gradient used at corner k, a shading normal: MVC is only C0 there (a cone: its slope depends on the
        direction, 4.2 deg around run 7's K), so the mean of the unit normals of that cone over the corner's interior
        angle (CORNER_RAYS rays at mid-angles, CORNER_RAY_MM out: the limit of the angle-weighted mean of a fine fan's
        triangle normals), as a gradient. Never the plane through the corner and its two neighbours: at a corner a few
        degrees from straight that bends in X that plane is nearly vertical (run 7's K, 1.7 deg from straight: 50-110
        deg off the triangles around it, review-builder-correctness P1-1)."""
        n = len(self.P)
        p, a, b = self.P[k], self.P[k - 1], self.P[(k + 1) % n]
        t0 = math.atan2(b[1] - p[1], b[0] - p[0])               # CCW polygon: the interior sweeps CCW from p -> b
        span = (math.atan2(a[1] - p[1], a[0] - p[0]) - t0) % (2.0 * math.pi)     # ... to p -> a
        th = t0 + span * (np.arange(CORNER_RAYS) + 0.5) / CORNER_RAYS
        gd, gy = self.grad(p[0] + CORNER_RAY_MM * np.cos(th), p[1] + CORNER_RAY_MM * np.sin(th))
        nv = np.stack([-gd, -gy, np.ones(CORNER_RAYS)], axis=1)
        m = (nv / np.linalg.norm(nv, axis=1, keepdims=True)).mean(axis=0)
        return np.array([-m[0] / m[2], -m[1] / m[2]])

    def grad(self, d, y) -> tuple[np.ndarray, np.ndarray]:
        """(dX/dd, dX/dy) at the points: central differences of X (h = GRAD_H_MM, or r / 10 nearer a corner than
        10 h); at a corner (within CORNER_AT_MM) corner_grad."""
        d, y = np.asarray(d, np.float64), np.asarray(y, np.float64)
        shp = np.broadcast(d, y).shape
        dd, yy = np.broadcast_to(d, shp).ravel(), np.broadcast_to(y, shp).ravel()
        r = np.hypot(dd[:, None] - self.P[None, :, 0], yy[:, None] - self.P[None, :, 1])
        rmin = r.min(axis=1) if len(dd) else np.zeros(0)
        h = np.clip(rmin / 10.0, 1e-9, GRAD_H_MM)
        gd = (self.X(dd + h, yy) - self.X(dd - h, yy)) / (2.0 * h)
        gy = (self.X(dd, yy + h) - self.X(dd, yy - h)) / (2.0 * h)
        at = rmin <= CORNER_AT_MM
        for i in np.nonzero(at)[0]:
            gd[i], gy[i] = self.corner_grad(int(np.argmin(r[i])))
        return gd.reshape(shp), gy.reshape(shp)


def warp_surface(label: str, corners: list, defining: list) -> WarpSurface:
    """The WarpSurface through corners [(name, d, y, X)] (CCW) with its least-squares plane."""
    M = np.array([[1.0, c[1], c[2]] for c in corners])
    F = np.array([c[3] for c in corners])
    coef = np.linalg.lstsq(M, F, rcond=None)[0]
    return WarpSurface(label=label, corners=[tuple(c) for c in corners], coef=coef, defining=defining,
                       warp_mm=float(np.max(np.abs(M @ coef - F))))


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
    """True when the facet lies on the left of n1 -> n2 (poly taken counter-clockwise; n1-n2 an edge or the ends of
    a straight run of edges, spec.crease_run)."""
    run = crease_run(ccw_poly(poly, nodes), n1, n2, nodes)
    if run is None:
        raise TempleError("E_SPEC", f"crease {[n1, n2]} is neither an edge nor a straight run of {list(poly)}")
    return run[0] > 0


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
            pkind = plane_kind(plane)
            crease = None
            if pkind == "warp":
                surf = _warp(ps, label, poly, nr, vr, node_vertex)
                if surf is None:
                    continue
                misfits = _misfits(pts, surf.X, tol, set())
                if misfits:
                    worst = max(misfits, key=lambda m: abs(m["misfit_mm"]))
                    nb = sorted({_src(p[3])[6:] for p in pts if p[3][0] == "facet"})
                    raise TempleError("E_RELIEF_STEP", f"facet {label} (warp) cannot meet facet(s) {nb} without a "
                                      f"step: {worst['misfit_mm']:+.3g} mm at (d, y) ({worst['at'][0]:.3f}, "
                                      f"{worst['at'][1]:.3f}) ({worst['source']}); a value on a warp facet's edge "
                                      f"that is not its corner must equal the edge's linear interpolation",
                                      facet=label, misfits=misfits, defining_points=surf.defining, neighbours=nb)
                planes[label] = surf
                order.append(label)
                vs = [v for v in fverts[label] if v not in vr]
                for v, x in zip(vs, surf.X(V[vs, 0], V[vs, 1]) if vs else ()):
                    vr[v] = (float(x), ("facet", label))
                for name in poly:
                    if name not in nr:
                        nr[name] = vr[node_vertex[name]]
                changed = True
                continue
            if pkind == "coef":
                coef = np.asarray(plane["coef"], np.float64)
                misfits = _misfits(pts, lambda d, y: coef[0] + coef[1] * d + coef[2] * y, tol, set())
                if misfits:
                    own = [m for m in misfits if m["source"].startswith("node ") and m["source"][5:] in fac["poly"]]
                    code = "E_FACET_NOT_PLANAR" if own and len(own) == len(misfits) else "E_RELIEF_STEP"
                    worst = max(misfits, key=lambda m: abs(m["misfit_mm"]))
                    fields = {"facet": label, "misfits": misfits, "defining_points": []}
                    if code == "E_RELIEF_STEP":
                        fields["neighbours"] = sorted({_src(p[3])[6:] for p in pts if p[3][0] == "facet"})
                    raise TempleError(code, f"facet {label}: {len(misfits)} known value(s) off its plane X = "
                                      f"{coef[0]:.6g} + {coef[1]:.6g} d + {coef[2]:.6g} y (worst "
                                      f"{worst['misfit_mm']:+.3g} mm at {worst['source']}, (d, y) "
                                      f"({worst['at'][0]:.3f}, {worst['at'][1]:.3f}))", **fields)
                planes[label] = FacetPlane(label, coef, "coef", [], 0.0, None)
                order.append(label)
                _hand_on(planes[label], label, poly, ps, V, fverts, vr, nr, node_vertex)
                changed = True
                continue
            if plane != "nodes":
                n1, n2 = plane["crease"]
                if n1 not in nr or n2 not in nr:
                    continue
                run = crease_run(poly, n1, n2, ps.nodes)       # poly is CCW: the facet lies left of its run
                if run is None:                                 # a node snap bent the run past RUN_LINE_MM
                    moved = [f"{x['what']} {x['field']} {x['from']:g}->{x['to']:g}" for x in ps.snaps
                             if str(x.get("what", "")).startswith("node ") and x["what"][5:] in poly]
                    raise TempleError("E_SPEC", f"facet {label}: crease {[n1, n2]} is no longer a straight run of its "
                                      f"edges once its nodes snap to the outline and knots ({'; '.join(moved)}); move "
                                      f"the corners between onto the line", facet=label)
                left = run[0] > 0
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
            if cond > ILL_CONDITIONED_DEG_PER_MM:            # facts only (WP6_V3 P6): no remedy offered
                names = ", ".join(p.get("node") or f"({p['d']:.1f}, {p['y']:.1f})" for p in defining)
                raise TempleError("E_ILL_CONDITIONED", f"facet {label}: a 1 mm error in one defining value tilts it "
                                  f"{cond:.1f} deg (> {ILL_CONDITIONED_DEG_PER_MM}); its plane is defined by {names}",
                                  facet=label, deg_per_mm=cond, defining_points=defining)
            if cond > CONDITIONING_WARN_DEG_PER_MM:
                warnings.append({"code": "W_CONDITIONING", "facet": label, "deg_per_mm": cond,
                                 "message": f"facet {label} tilts {cond:.1f} deg per mm of node error"})
            planes[label] = FacetPlane(label, coef, kind, defining, cond, crease)
            order.append(label)
            _hand_on(planes[label], label, poly, ps, V, fverts, vr, nr, node_vertex)
            changed = True
    missing = [l for l in labels if l not in planes]
    if missing:
        l = missing[0]
        fac = ps.facets[l]
        known = [{"node": name, "X": nr[name][0]} for name in fac["poly"] if name in nr]
        known += [{"at": V[v].tolist(), "X": vr[v][0], "source": _src(vr[v][1])} for v in fverts[l] if v in vr]
        extra = {}
        if fac["plane"] == "warp":
            extra["corners_without_value"] = [n for n in fac["poly"] if _corner_value(n, nr, vr, node_vertex) is None]
            why = (f"a warp facet needs every corner's value (a number, base, or one another facet hands on at the "
                   f"corner); corners without one: {', '.join(extra['corners_without_value'])}")
        elif fac["plane"] != "nodes":
            why = "its crease nodes have no value"
        else:
            why = "it has fewer than 3 non-collinear known values (base borders are never known points)"
        raise TempleError("E_FACET_UNDERDETERMINED", f"facet {l}: {why}", facet=l, facets=missing, known=known,
                          **extra)
    X_vertex = np.full(n, np.nan)
    src = [None] * n
    for v, (X, s) in vr.items():
        X_vertex[v] = X
        src[v] = _src(s)
    return PlaneSolution(planes=planes, X_vertex=X_vertex, source_vertex=src,
                         node_X={k: v[0] for k, v in sorted(nr.items())}, node_vertex=node_vertex,
                         warnings=warnings, order=order)


def _hand_on(pl: FacetPlane, label, poly, ps, V, fverts, vr, nr, node_vertex) -> None:
    """A solved flat facet's values: every vertex of its faces without one, and every corner of its poly without one
    (a solved node; on an arrangement vertex it binds that vertex too)."""
    coef = pl.coef
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


def _corner_value(name, nr, vr, node_vertex):
    """(X, source) of a warp corner: its given value (a number or "base"), else the value another facet handed on at
    the arrangement vertex it sits on; None without either (a solved corner outside the outline never has one: a
    solved node binds another facet only through its vertex)."""
    if name in nr and nr[name][1][0] == "node":
        return nr[name]
    v = node_vertex.get(name)
    return vr.get(v) if v is not None else None


def _warp(ps, label: str, poly: list, nr, vr, node_vertex) -> WarpSurface | None:
    """The warp facet's surface once every corner (poly, CCW) has a value, else None."""
    vals = [_corner_value(n, nr, vr, node_vertex) for n in poly]
    if any(v is None for v in vals):
        return None
    corners = [(n, float(ps.nodes[n][0]), float(ps.nodes[n][1]), float(v[0])) for n, v in zip(poly, vals)]
    defining = [_pt(ps.nodes[n][:2], v[0], v[1], ("node", n)) for n, v in zip(poly, vals)]
    return warp_surface(label, corners, defining)


def _misfits(pts, fn, tol: float, def_keys: set) -> list:
    """Known points (d, y, X, source, key) off the surface fn(d, y) (evaluated on all points at once) by more than
    tol (defining keys exempt)."""
    if not pts:
        return []
    S = np.broadcast_to(np.asarray(fn(np.array([p[0] for p in pts]), np.array([p[1] for p in pts])), np.float64),
                        (len(pts),))
    out = []
    for (d, y, X, src, key), s in zip(pts, S):
        m = X - float(s)
        if abs(m) > tol and key not in def_keys:
            out.append({"at": [d, y], "known_X": X, "plane_X": X - m, "misfit_mm": m, "source": _src(src)})
    return out


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
