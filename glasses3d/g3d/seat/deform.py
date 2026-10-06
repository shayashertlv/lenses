"""Band displacement, the correction pass, riders, array post-checks and the array-level tools (WP4_DESIGN.md §4 steps
6-7 and 11, §6 steps 5-6). numpy only: `seat_arrays` and `lift_arrays` are the whole of seat_lens / hide_eyewire up
to the write, so the host tests run them on fixtures with the numpy backend and Blender runs them unchanged.

Band (§3): the outer front loop (the move nodes), the back loop and the wall vertices between them; a back-loop
vertex follows its rung partner (polygon edges when known, else the shortest edge: triangulation diagonals are never
rungs), unpartnered back-loop and interior wall vertices map to the nearest node by arc. Wall faces of a drilled
hole are not in the band.

Displacement of a band vertex (§4 step 6): delta = g u + dn n, with dn from the quadric z = a t^2 + b t u + c u^2 +
d t + e u + f fitted to that cap's vertices within 4 mm (>= 12 points; widen to 6 mm; else the 12 nearest,
W_LOW_CONFIDENCE_FIT; fewer than 6: the tangent plane), front fit for front-loop vertices, back fit for back-loop
vertices, depth-fraction blend on the wall; dn = z(0, g) - z(0, 0). The fit uses the cap's own vertices outside the
band (never the rim loops: a chamfer's or a bullnose's rim ring lies off the cap, and fitting it with the cap bent the
quadric so the grown edge left the cap by 0.031 / 0.0135 mm; the intercept lets the rim vertex keep its offset), and
the 12 nearest replace the tangent plane (a 32-segment lens has fewer than 12 cap vertices within 6 mm: moving along
the plane normal to the station normal left the cap by 0.0174 mm). Riders lying on a moved face follow it
barycentrically.
"""
from __future__ import annotations

import dataclasses
import hashlib
from dataclasses import dataclass, field

import numpy as np

from . import geom, rules, stations
from .geom import SeatError
from .intervals import Stacked
from .plan import FACE_ROTATION_MAX_DEG, G_MAX_MM, LiftPlan, SeatPlan, plan_growth, plan_lift
from .stations import MID, Frames, LensBody, Measurement

FIT_RADII_MM = (4.0, 6.0)          # §4 step 6
FIT_MIN_POINTS = 12
CORRECTION_PASSES = 2              # §4 step 7
CORRECTION_AIM_MM = stations.CLASS_EPS_MM   # the correction aims this far beyond the planned target: the float32
                                            # noise of stored positions and backends (CLASS_EPS_MM), so a corrected
                                            # point lands at or above its target, not 1e-5 mm under it
AREA_RATIO = (0.5, 2.5)
VOLUME_TOLERANCE = 0.15
VOLUME_FLOOR_MM3 = 1e-3            # absolute floor on the volume check (float noise on a tiny growth)
RIDER_SURFACE_MM = 0.5
LIFT_SECTION_TOLERANCE = 0.01      # §6 step 6: section area +-1%
LIFT_SLOPE_MAX = 0.5               # §2 'Eyewire margin / ramp': slope <= 0.5 mm/mm
LIFT_CAPTURE_TOLERANCE_MM = 0.02   # §6 step 5: lens-in-eyewire capture within +-0.02 mm
TOOL_VERSION = "wp4-tools-1"


# ---------------------------------------------------------------------------------------------------------- band
@dataclass
class LensBand:
    node: np.ndarray       # (nvb,) node (index into the oriented front loop) owning each body vertex, -1 not band
    role: np.ndarray       # (nvb,) 0 front loop, 1 back loop, 2 wall interior, -1 none
    depth: np.ndarray      # (nvb,) depth fraction 0 (front corner) .. 1 (back corner)
    partner: np.ndarray    # (nv,) back-loop body vertex of each node's rung (-1 none)


def body_edges(body: LensBody, poly_edges=None) -> np.ndarray:
    if poly_edges is not None and len(poly_edges):
        return np.asarray(poly_edges, np.int64).reshape(-1, 2)
    return geom.edge_table(body.T).edges


def lens_band(body: LensBody, frames: Frames, node_pt: np.ndarray, poly_edges=None) -> LensBand:
    V = body.V
    nvb = len(V)
    loop = frames.loop
    nv = len(loop)
    node = np.full(nvb, -1, np.int64)
    role = np.full(nvb, -1, np.int64)
    depth = np.zeros(nvb)
    node[loop] = np.arange(nv)
    role[loop] = 0
    E = body_edges(body, poly_edges)
    partner = geom.rungs(V, loop, body.bl, E)
    best = {}
    for k, b in enumerate(partner.tolist()):
        if b < 0:
            continue
        d = float(np.linalg.norm(V[loop[k]] - V[b]))
        if b not in best or d < best[b][0]:
            best[b] = (d, k)
    for b, (_, k) in best.items():
        node[b], role[b], depth[b] = k, 1, 1.0
    PL = V[loop]
    rest = np.array([b for b in body.bl.tolist() if role[b] < 0], np.int64)
    if len(rest):
        node[rest] = _nearest_node(PL, V[rest])
        role[rest], depth[rest] = 1, 1.0
    # interior wall vertices: wall faces connected (through wall-wall edges) to the outer loops
    wall = np.flatnonzero(body.cls == 2)
    if len(wall):
        et = geom.edge_table(body.T)
        inner = et.f1 >= 0
        a, b = et.f0[inner], et.f1[inner]
        ok = (body.cls[a] == 2) & (body.cls[b] == 2)
        lab = geom.label_components(len(body.T), a[ok], b[ok])
        rim = np.zeros(nvb, bool)
        rim[loop] = True
        rim[body.bl] = True
        touch = (body.cls == 2) & rim[body.T].any(1)
        keep = np.isin(lab, np.unique(lab[touch])) & (body.cls == 2)
        wv = np.unique(body.T[keep])
        wv = wv[role[wv] < 0]
        if len(wv):
            kk = _nearest_node(PL, V[wv])
            node[wv], role[wv] = kk, 2
            n = frames.n[node_pt[kk]]
            pb = np.where((partner[kk] >= 0)[:, None], V[np.maximum(partner[kk], 0)], PL[kk])
            span = ((PL[kk] - pb) * n).sum(1)
            f = ((PL[kk] - V[wv]) * n).sum(1) / np.where(np.abs(span) > 1e-12, span, 1.0)
            depth[wv] = np.clip(f, 0.0, 1.0)
    _rounded_zone(body, frames, node_pt, node, role, depth, partner)
    return LensBand(node, role, depth, partner)


def station_reach(frames: Frames, V: np.ndarray, band: LensBand, back_loop: np.ndarray) -> dict:
    """{node point: node indices}: the nodes whose rungs bound the lens wall a station's depth lines cross. Its depth
    points run from its front corner (node k) to its back corner, which lies on the back loop nearest to the rung
    partner of node j; the wall between moves with the nodes from k to j (the shorter way round), with the owners of
    those rungs' back vertices. Where the wall lies in the station plane that is node k alone (FT and the fixtures:
    back corners within 0.13 mm of the rung partner on 1 mm segments). INVU's wall is extruded along z under station
    planes leaning up to 31 deg, so a back corner sits up to 0.93 mm (1.7 segments) along the loop from its node's rung
    partner: growing the station's own node by 0.01 mm raised its mid-depth capture by 0.0006-0.0012 mm, all its
    neighbours too by 0.00998, and a correction growing only the short nodes converged by about half per pass (12-36
    of 261 inside-run former failures still below 0.45 after two passes)."""
    out = {}
    loop = frames.loop
    nv = len(loop)
    bl = np.asarray(back_loop, np.int64)
    if nv == 0 or len(bl) < 2:
        return out
    np_pt = plan_node_points(frames, V)
    of_back = {}
    for k, b in enumerate(np.asarray(band.partner).tolist()):
        if b >= 0:
            of_back.setdefault(int(b), []).append(k)
    ok = frames.ok[np.maximum(np_pt, 0)] & (np_pt >= 0)
    ks = np.flatnonzero(ok)
    if not len(ks):
        return out
    sb, _, seg = geom.nearest_on_polyline(V[bl], frames.Pb[np_pt[ks]])
    BL = V[bl]
    blen = np.linalg.norm(np.roll(BL, -1, 0) - BL, axis=1)
    s0 = np.r_[0.0, np.cumsum(blen)][:-1]
    for k, m, sv in zip(ks.tolist(), seg.tolist(), sb.tolist()):
        w = (sv - s0[m]) / max(blen[m], 1e-30)
        near = int(bl[m]) if w <= 0.5 else int(bl[(m + 1) % len(bl)])    # the rung nearest to the back corner
        js = of_back.get(near, [])
        rungs = {k}
        for j in js:
            fwd = (j - k) % nv
            if fwd <= nv - fwd:
                rungs.update(((k + np.arange(fwd + 1)) % nv).tolist())
            else:
                rungs.update(((j + np.arange(nv - fwd + 1)) % nv).tolist())
        # the wall quads between those rungs move with their front nodes and with the owners of their back vertices
        nodes = set(rungs)
        for r in rungs:
            b = int(band.partner[r])
            if b >= 0 and band.node[b] >= 0:
                nodes.add(int(band.node[b]))
        out[int(np_pt[k])] = np.array(sorted(nodes), np.int64)
    return out


def _rounded_zone(body: LensBody, frames: Frames, node_pt, node, role, depth, partner) -> None:
    """Rounded rims (§2 'Vertices moved': the band must cover rounded walls). On a bullnose the first facet
    deviates from the cap by less than the accepted dihedral threshold, so the split puts it in the CAP and the rim
    loop one ring out; growing the loop alone would stretch that facet and move the rim loop of a fresh split (a
    second call then grows again). Cap vertices inside the rim zone (within §3's 0.8 mm normal inset of their loop)
    whose cap faces deviate from the station normal by more than half the accepted threshold belong to the rounded
    profile: they move with their node (front: depth 0, back: depth 1). On a cap that follows its surface the outer
    ring deviates ~1 deg from the station normal (FT, Prada, Oakley, INVU, the sphere fixtures), so nothing changes
    there."""
    V = body.V
    loop = frames.loop
    half = 0.5 * float(body.theta_deg)
    fn, fa, _ = geom.face_geometry(V, body.T)
    for cap, lp, f in ((0, loop, 0.0), (1, body.bl, 1.0)):
        faces = np.flatnonzero(body.cls == cap)
        PL = V[lp]
        # only vertices within the inset can become candidates, so only faces holding one matter (the same set as
        # testing every cap face: INVU's 28k-triangle caps made this 7.8 s of a 17 s seat_lens)
        _, dist, _ = geom.nearest_on_polyline(PL, V, max_dist=stations.NORMAL_INSET_MM)
        near = dist < stations.NORMAL_INSET_MM
        T = body.T[faces]
        hold = near[T].any(1)
        faces, T = faces[hold], T[hold]
        if not len(faces):
            continue
        v0 = T[:, 0]
        k0 = _nearest_node(V[loop], V[v0])                     # node of each face's first vertex, by arc
        n_v = frames.n[node_pt[k0]] * (1.0 if cap == 0 else -1.0)
        cand = np.zeros(len(V), bool)
        dev = np.degrees(np.arccos(np.clip((fn[faces] * n_v).sum(1), -1.0, 1.0)))
        steep = (dev > half) & (fa[faces] >= geom.DEGENERATE_AREA_MM2)
        cand[np.unique(T[steep])] = True
        cand &= (role < 0) & near
        idx = np.flatnonzero(cand)
        if len(idx):
            node[idx] = _nearest_node(V[loop], V[idx])
            role[idx] = 2
            depth[idx] = f


def _nearest_node(PL: np.ndarray, X: np.ndarray) -> np.ndarray:
    """Nearest node by arc: the closer end of the loop segment nearest to each point."""
    s, _, k = geom.nearest_on_polyline(PL, X)
    seg = np.linalg.norm(np.roll(PL, -1, 0) - PL, axis=1)
    s0 = np.r_[0.0, np.cumsum(seg)][:-1]
    w = (s - s0[k]) / np.maximum(seg[k], 1e-30)
    return np.where(w <= 0.5, k, (k + 1) % len(PL))


# -------------------------------------------------------------------------------------------------- displacement
@dataclass
class Displacement:
    D: np.ndarray                  # (nvb, 3) per body vertex
    rot_front: dict                # node -> 3x3 rotation of the front cap normal (old -> new)
    rot_back: dict                 # back body vertex -> 3x3 rotation of the back cap normal
    low_confidence: list           # nodes whose fit fell back to the tangent plane
    dn: dict                       # node -> (dn_front, dn_back)


QUADRIC_TERMS = 6                  # a t^2 + b t u + c u^2 + d t + e u + f


def _fit(V, pool, origin, t, u, n):
    """(quadric coefficients at `origin` with an intercept, low_confidence): the pool vertices within 4 mm (>= 12;
    widen to 6 mm), else the 12 nearest (low confidence); (None, True) when the pool has fewer than 6 vertices."""
    if not len(pool):
        return None, True
    d = np.linalg.norm(V[pool] - origin, axis=1)
    for r in FIT_RADII_MM:
        sel = pool[d < r]
        if len(sel) >= FIT_MIN_POINTS:
            return geom.fit_quadric(V[sel], origin, t, u, n, intercept=True), False
    if len(pool) < QUADRIC_TERMS:
        return None, True
    sel = pool[np.argsort(d, kind="stable")[:FIT_MIN_POINTS]]
    return geom.fit_quadric(V[sel], origin, t, u, n, intercept=True), True


def fit_pools(body: LensBody, band: LensBand) -> tuple[np.ndarray, np.ndarray]:
    """(front pool, back pool): each cap's vertices outside the band (the rim loops and rounded-rim zones are band)."""
    in_band = band.role >= 0
    front_pool = np.unique(body.T[body.cls == 0])
    back_pool = np.unique(body.T[body.cls == 1])
    return front_pool[~in_band[front_pool]], back_pool[~in_band[back_pool]]


def _rotation(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Rotation taking unit a to unit b (Rodrigues)."""
    a = a / np.linalg.norm(a)
    b = b / np.linalg.norm(b)
    v = np.cross(a, b)
    c = float(np.dot(a, b))
    s = float(np.linalg.norm(v))
    if s < 1e-15:
        return np.eye(3)
    K = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + K + K @ K * ((1 - c) / (s * s))


def _surface_normal(coef, n, t, u, x, y):
    if coef is None:
        return n
    a, b, c, d, e = coef[:5]
    zt = 2 * a * x + b * y + d
    zu = b * x + 2 * c * y + e
    v = n - zt * t - zu * u
    return v / np.linalg.norm(v)


def node_displacement(body: LensBody, band: LensBand, frames: Frames, node_pt: np.ndarray,
                      g_node: np.ndarray) -> Displacement:
    V = body.V
    D = np.zeros_like(V)
    loop = frames.loop
    front_pool, back_pool = fit_pools(body, band)
    rot_f, rot_b, low, dns = {}, {}, [], {}
    moving = np.flatnonzero(g_node > 0)
    backs_of = {}
    for vb in np.flatnonzero(band.role == 1).tolist():
        backs_of.setdefault(int(band.node[vb]), []).append(vb)
    walls_of = {}
    for vw in np.flatnonzero(band.role == 2).tolist():
        walls_of.setdefault(int(band.node[vw]), []).append(vw)
    for k in moving.tolist():
        g = float(g_node[k])
        i = int(node_pt[k])
        t, u, n = frames.t[i], frames.u[i], frames.n[i]
        pf = V[loop[k]]
        cf, lowf = _fit(V, front_pool, pf, t, u, n)
        if lowf:
            low.append(k)
        dnf = float(geom.quadric_rise(cf, g)) if cf is not None else 0.0
        D[loop[k]] = g * u + dnf * n
        rot_f[k] = _rotation(_surface_normal(cf, n, t, u, 0.0, 0.0), _surface_normal(cf, n, t, u, 0.0, g))
        dnb_partner = None
        for vb in backs_of.get(k, []):
            cb, lowb = _fit(V, back_pool, V[vb], t, u, n)
            if lowb:
                low.append(k)
            dnb = float(geom.quadric_rise(cb, g)) if cb is not None else 0.0
            D[vb] = g * u + dnb * n
            rot_b[vb] = _rotation(_surface_normal(cb, n, t, u, 0.0, 0.0), _surface_normal(cb, n, t, u, 0.0, g))
            if vb == band.partner[k] or dnb_partner is None:
                dnb_partner = dnb
        dnb_partner = 0.0 if dnb_partner is None else dnb_partner
        for vw in walls_of.get(k, []):
            f = band.depth[vw]
            D[vw] = g * u + ((1 - f) * dnf + f * dnb_partner) * n
        dns[k] = (dnf, dnb_partner)
    return Displacement(D, rot_f, rot_b, sorted(set(low)), dns)


def surface_dn(body: LensBody, band: LensBand, frames: Frames, node_pt: np.ndarray, nodes, lifts) -> np.ndarray:
    """Mid-depth normal offset (mean of the front and back cap quadrics, §4 step 6) of a move by lifts[q] along u at
    nodes[q]: the path the lens edge takes along its own curved surface. Fits are cached per node."""
    V = body.V
    loop = frames.loop
    front_pool, back_pool = fit_pools(body, band)
    cache: dict = {}
    out = np.zeros(len(nodes))
    for q, (k, g) in enumerate(zip(np.asarray(nodes).tolist(), np.asarray(lifts, float).tolist())):
        if g <= 0:
            continue
        if k not in cache:
            i = int(node_pt[k])
            t, u, n = frames.t[i], frames.u[i], frames.n[i]
            cf = _fit(V, front_pool, V[loop[k]], t, u, n)[0]
            b = int(band.partner[k])
            cb = _fit(V, back_pool, V[b], t, u, n)[0] if b >= 0 else None
            cache[k] = (cf, cb)
        cf, cb = cache[k]
        dnf = float(geom.quadric_rise(cf, g)) if cf is not None else 0.0
        dnb = float(geom.quadric_rise(cb, g)) if cb is not None else dnf
        out[q] = 0.5 * (dnf + dnb)
    return out


def rider_follow(rider_V: np.ndarray, body: LensBody, D: np.ndarray) -> np.ndarray:
    """Barycentric displacement of rider vertices lying (within 0.5 mm) on body cap faces with a moved vertex."""
    out = np.zeros_like(rider_V)
    moved = np.linalg.norm(D, axis=1) > 0
    if not len(rider_V) or not moved.any():
        return out
    faces = np.flatnonzero((body.cls != 2) & moved[body.T].any(1))
    if not len(faces):
        return out
    T = body.T[faces]
    A, B, C = body.V[T[:, 0]], body.V[T[:, 1]], body.V[T[:, 2]]
    for r, X in enumerate(rider_V):
        w, dist, f = _closest_on_triangles(X, A, B, C)
        if dist <= RIDER_SURFACE_MM:
            out[r] = w[0] * D[T[f, 0]] + w[1] * D[T[f, 1]] + w[2] * D[T[f, 2]]
    return out


def _closest_on_triangles(X, A, B, C):
    """(barycentric weights, distance, face) of the closest point to X on triangles ABC (clamped projection)."""
    ab, ac = B - A, C - A
    nrm = np.cross(ab, ac)
    nn = np.maximum((nrm * nrm).sum(1), 1e-30)
    ap = X - A
    v = (np.cross(ap, ac) * nrm).sum(1) / nn
    w = (np.cross(ab, ap) * nrm).sum(1) / nn
    u = 1 - v - w
    W = np.clip(np.stack([u, v, w], 1), 0.0, None)
    W /= np.maximum(W.sum(1, keepdims=True), 1e-30)
    Q = W[:, 0:1] * A + W[:, 1:2] * B + W[:, 2:3] * C
    d = np.linalg.norm(Q - X, axis=1)
    f = int(np.argmin(d))
    return W[f], float(d[f]), f


# ------------------------------------------------------------------------------------------------- post-checks
def face_changes(body: LensBody, V0: np.ndarray, V1: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(face ids, rotation deg, area ratio) of the non-degenerate faces with a moved vertex: §4 step 11's measure,
    shared by the post-check and the planner's fold refinement."""
    moved = np.any(V1 != V0, axis=1)
    fm = np.flatnonzero(moved[body.T].any(1))
    n0, a0, _ = geom.face_geometry(V0, body.T[fm])
    n1, a1, _ = geom.face_geometry(V1, body.T[fm])
    ok = a0 >= geom.DEGENERATE_AREA_MM2
    ang = np.degrees(np.arccos(np.clip((n0[ok] * n1[ok]).sum(1), -1, 1)))
    return fm[ok], ang, a1[ok] / a0[ok]


FOLD_REFINE_PASSES = 8             # replans of the fold limit at most (each lowers the slope of the segments whose
                                   # faces the exact displacement still turns past the limit)
STORE_REL_EPS = 2.0 ** -23         # float32 relative precision: Blender stores positions as float32, and on a short
                                   # rim segment that alone turns a wall face by atan(2 eps |x| / segment)


def fold_ratios(body: LensBody, band: LensBand, loop: np.ndarray, V0: np.ndarray, V1: np.ndarray,
                limit: float = FACE_ROTATION_MAX_DEG) -> tuple[dict, int]:
    """Faces the move V0 -> V1 turns past `limit` less their float32 storage margin, as {segment k (nodes k, k + 1
    of the oriented loop): factor to scale its fold slope by}, and the number of such faces that span no single rim
    segment (the slope cannot help them: the post-check then refuses)."""
    nv = len(loop)
    fids, ang, _ = face_changes(body, V0, V1)
    if not len(fids):
        return {}, 0
    tri = body.T[fids]
    P = V0[tri]
    edge = np.min(np.linalg.norm(P - np.roll(P, -1, axis=1), axis=2), axis=1)
    eps = 2.0 * STORE_REL_EPS * np.abs(V0).max()
    margin = np.degrees(np.arctan2(2.0 * eps, np.maximum(edge, 1e-300)))
    lim = limit - margin
    bad = np.flatnonzero(ang > lim)
    out, lost = {}, 0
    for q in bad.tolist():
        nodes = np.unique(band.node[tri[q]])
        nodes = nodes[nodes >= 0]
        if len(nodes) != 2:
            lost += 1
            continue
        a, b = int(nodes[0]), int(nodes[1])
        if (a + 1) % nv == b:
            k = a
        elif (b + 1) % nv == a:
            k = b
        else:
            lost += 1
            continue
        target = max(lim[q], 0.5 * limit)
        f = float(np.tan(np.radians(target)) / np.tan(np.radians(min(ang[q], 89.0))))
        out[k] = min(out.get(k, 1.0), f)
    return out, lost


def array_checks(body: LensBody, V0: np.ndarray, V1: np.ndarray, loop: np.ndarray, band: LensBand,
                 g_total: np.ndarray, edge_t: np.ndarray, frames0: Frames) -> list[str]:
    """§4 step 11 on planned arrays: faces rotate <= 30 deg and keep area ratio 0.5-2.5; no reversed outline
    segment; vertices outside the band of moved nodes bit-identical; volume gain = sum g t ds +- 15%."""
    bad = []
    moved = np.any(V1 != V0, axis=1)
    if not moved.any():
        return bad
    in_band = (band.node >= 0) & (g_total[np.maximum(band.node, 0)] > 0)
    if (moved & ~in_band).any():
        bad.append(f"{int((moved & ~in_band).sum())} vertices outside the moved nodes changed")
    _, ang, ratio = face_changes(body, V0, V1)
    if len(ang):
        if ang.max() > FACE_ROTATION_MAX_DEG:
            bad.append(f"a moved face rotated {ang.max():.1f} deg (limit {FACE_ROTATION_MAX_DEG:g})")
        if ratio.min() < AREA_RATIO[0] or ratio.max() > AREA_RATIO[1]:
            bad.append(f"a moved face's area ratio is {ratio.min():.3f}-{ratio.max():.3f} (limit 0.5-2.5)")
    for name, lp in (("front", loop), ("back", body.bl)):
        s0 = np.roll(V0[lp], -1, 0) - V0[lp]
        s1 = np.roll(V1[lp], -1, 0) - V1[lp]
        keep = np.linalg.norm(s0, axis=1) > 1e-9
        if ((s0[keep] * s1[keep]).sum(1) <= 0).any():
            bad.append(f"the {name} outline has a reversed segment")
    vol0 = geom.signed_volume(V0, body.T)
    vol1 = geom.signed_volume(V1, body.T)
    P = V0[loop]
    seg = np.linalg.norm(np.roll(P, -1, 0) - P, axis=1)
    ds = 0.5 * (seg + np.roll(seg, 1))
    expect = float((g_total * edge_t * ds).sum())
    gain = vol1 - vol0
    if abs(gain - expect) > max(VOLUME_TOLERANCE * abs(expect), VOLUME_FLOOR_MM3):
        bad.append(f"volume gain {gain:.3f} mm3 vs sum g t ds {expect:.3f} mm3 (limit +-15%)")
    return bad


def node_margins(frames: Frames, rl: rules.Rules, meas: Measurement) -> np.ndarray:
    """Per node: the smallest (value - pass) over its required, measured points (+inf when none); a full-rim thin
    point without its front held counts as failing."""
    nv = len(frames.loop)
    out = np.full(nv, np.inf)
    m = np.asarray(rl.required, bool) & (meas.cls.astype(str) != "UNMEASURED") & np.isfinite(rl.pass_mm)
    margin = np.where(np.isfinite(rl.value), rl.value - rl.pass_mm, -np.inf)
    if rl.front_required is not None:
        margin = np.where(rl.front_required & ~rl.front_ok, np.minimum(margin, -1.0), margin)
    for o in frames.owners:                         # a halfway point counts for both ends of its segment
        np.minimum.at(out, o[m], margin[m])
    return out


# ------------------------------------------------------------------------------------------------- seat arrays
@dataclass
class BodyState:
    body: LensBody
    frames: Frames
    meas: Measurement
    rl: rules.Rules
    plan: SeatPlan | None
    band: LensBand | None = field(default=None, repr=False, compare=False)   # measure_state's band (refine_fold)


@dataclass
class SeatResult:
    status: str                    # seated | already_seated | partial | refused
    code: str | None = None
    message: str = ""
    raw_delta: np.ndarray | None = None        # (n_raw, 3) asset-mm displacement per raw vertex
    rot: dict = field(default_factory=dict)    # raw vertex -> 3x3 normal rotation (moved cap corners)
    cap_of_raw: np.ndarray | None = None       # (n_raw,) 0 front-cap vertex, 1 back-cap, -1 other (for normals)
    bodies: list = field(default_factory=list) # per body: report dicts
    states: list = field(default_factory=list) # per body: (before BodyState, after BodyState)
    warnings: list = field(default_factory=list)
    moved_raw: int = 0
    plan_status: str | None = None             # the status before the check verdict on the result (state_check):
                                               # seat_lens uses it for a profiled lens, whose real check runs after
                                               # the profile changed the edge

    def as_dict(self) -> dict:
        return {"status": self.status, "code": self.code, "message": self.message, "moved_vertices": self.moved_raw,
                "bodies": self.bodies, "warnings": self.warnings}


def measure_only(body: LensBody, frame_backend, factory, ft, ranges):
    """(frames, meas, rules, backend, lens_part): the lens in its own backend, stacked after the frame parts."""
    be = Stacked([frame_backend, factory([(body.V, body.T)])])
    lens_part = frame_backend.n_parts
    fr, meas = stations.measure_body(body, be, lens_part)
    rl = rules.rule_values(ft, meas, fr.P[:, 0], ranges)
    return fr, meas, rl, be, lens_part


def measure_state(body: LensBody, frame_backend, factory, ft, ranges, obstacles=None, poly_edges=None,
                  **plan_kw) -> BodyState:
    """Measure and plan one body; a plan that moves is refined against the exact displacement (`refine_fold`).
    `poly_edges`: the body's polygon edges (rungs, as grow_body's band uses them)."""
    fr, meas, rl, be, lens_part = measure_only(body, frame_backend, factory, ft, ranges)
    cache: dict = {}                # the point plan, shared by the replans (plan_growth `cache`)
    plan = plan_growth(fr, meas, rl, ft, be, lens_part, body.V, obstacles, cache=cache, **plan_kw)
    band = None
    if plan.moves:
        band = lens_band(body, fr, plan_node_points(fr, body.V), poly_edges)
        plan = refine_fold(body, fr, poly_edges, plan, lambda S: plan_growth(
            fr, meas, rl, ft, be, lens_part, body.V, obstacles, fold_slope=S, cache=cache, **plan_kw), band=band)
    return BodyState(body, fr, meas, rl, plan, band)


def refine_fold(body: LensBody, frames: Frames, poly_edges, plan: SeatPlan, replan, V_cur=None, frames_cur=None,
                roundtrip=None, band: LensBand | None = None) -> SeatPlan:
    """The fold limit (plan.fold_slopes) is first order in the growth step; the exact displacement (quadric normal
    offsets, the segment shortening where the outward directions converge) turned Prada's limit-bound wall faces by
    30.1-31.0 deg. Displace with the plan, measure every moved face as the post-check does, scale the slope of each
    segment whose faces exceed the limit (less the float32 storage margin) by tan(limit) / tan(angle), and replan
    (`replan(S)`), at most FOLD_REFINE_PASSES times. A face that spans no single segment is left to the post-check.
    V_cur / frames_cur: a correction pass (the move is added to V_cur; faces compare with body.V); roundtrip:
    what Blender stores for the planned arrays; band: lens_band(body, frames, ...) when the caller has it. The plan
    returned when no face exceeds the limit carries the displacement it was measured with (`SeatPlan.disp`), so
    grow_body does not compute it again."""
    loop = frames.loop
    if band is None:
        band = lens_band(body, frames, plan_node_points(frames, body.V), poly_edges)
    V_base = body.V if V_cur is None else V_cur
    fr_cur = frames if frames_cur is None else frames_cur
    S = np.asarray(plan.fold_slope, float).copy()
    for _ in range(FOLD_REFINE_PASSES):
        if not plan.moves:
            return plan
        disp = node_displacement(_with_V(body, V_base), band, fr_cur, plan_node_points(fr_cur, V_base), plan.node_g)
        V1 = V_base + disp.D
        if roundtrip is not None:
            V1 = roundtrip(V1)
        ratios, _ = fold_ratios(body, band, loop, body.V, V1)
        if not ratios:
            plan.disp = (fr_cur, V_base, plan.node_g.copy(), disp)
            return plan
        for k, f in ratios.items():
            S[k] *= f
        plan = replan(S)
    return plan


def _planned_disp(plan: SeatPlan | None, frames: Frames, V: np.ndarray, g: np.ndarray):
    """The displacement refine_fold measured `plan` with, when it is the one grow_body is about to compute (same
    frames object, equal V and growth), else None."""
    pd = getattr(plan, "disp", None) if plan is not None else None
    if pd is None:
        return None
    fr, Vp, gp, disp = pd
    if fr is frames and np.array_equal(Vp, V) and np.array_equal(gp, g):
        return disp
    return None


def _with_V(body: LensBody, V: np.ndarray) -> LensBody:
    return dataclasses.replace(body, V=np.asarray(V, np.float64))


def _ident(V):
    return V


def grow_body(body: LensBody, frame_backend, factory, ft: rules.FrameType, ranges=None, obstacles=None,
              poly_edges=None, roundtrip=_ident, g_max: float = G_MAX_MM, state0: BodyState | None = None,
              g_node_override: np.ndarray | None = None) -> dict:
    """Plan, displace, correct (<= 2 passes, §4 step 7) and post-check (§4 step 11) one body. Returns
    {"state0", "state1", "V1", "g_total", "disp", "band", "postcheck" (failures), "passes", "low_confidence"}.
    state1's plan is a FRESH plan on the result (what a second call would do).

    `g_node_override`: growth per node given from outside (hide_eyewire moves the held lens by L(s)): no plan
    decides the growth and no correction pass runs."""
    st0 = state0 or measure_state(body, frame_backend, factory, ft, ranges, obstacles, poly_edges, g_max=g_max)
    nv = len(st0.frames.loop)
    g_total = np.zeros(nv)
    V = body.V.copy()
    disps, low = [], []
    band = st0.band if st0.band is not None else lens_band(body, st0.frames, plan_node_points(st0.frames, body.V),
                                                           poly_edges)
    if g_node_override is not None:
        g = np.asarray(g_node_override, float)
    else:
        g = st0.plan.node_g if st0.plan.gate is None else np.zeros(nv)
    passes = 0
    frames_cur, nodept_cur = st0.frames, plan_node_points(st0.frames, body.V)
    node_target = st0.plan.node_target.copy() if st0.plan is not None else np.full(nv, np.nan)
    fold_cur = st0.plan.fold_slope if st0.plan is not None else None    # the fold limit of the ORIGINAL lens
    inside = None                   # nodes >= the run-end ramp inside a failing run of the first plan (residual)
    if st0.plan is not None:
        from .plan import RUN_END_RAMP_MM, run_delta
        f0 = (rules.verdicts(ft, st0.meas, st0.rl, st0.frames.s, st0.frames.loop_mm, transition=False).astype(str)
              == "FAIL") & np.asarray(st0.rl.required, bool)
        d0 = run_delta(f0, st0.frames.s, st0.frames.loop_mm)[plan_node_points(st0.frames, body.V)]
        inside = np.nan_to_num(d0, nan=-1.0) >= RUN_END_RAMP_MM
    last = None                     # the measurement of the current V (reused for state1 when nothing changed)
    reach0 = None                   # station_reach by node, from the original lens (computed once)
    plan = st0.plan
    while (g > 0).any():
        disp = _planned_disp(plan, frames_cur, V, g) if g_node_override is None else None
        if disp is None:
            disp = node_displacement(_with_V(body, V), band, frames_cur, nodept_cur, g)
        disps.append(disp)
        low += disp.low_confidence
        V = roundtrip(V + disp.D)
        g_total = g_total + g
        passes += 1
        last = None
        if g_node_override is not None or passes > CORRECTION_PASSES:
            break
        fr, meas, rl, be, lp = measure_only(_with_V(body, V), frame_backend, factory, ft, ranges)
        if not np.array_equal(fr.loop, st0.frames.loop):
            raise SeatError("E_POSTCHECK", "the oriented front loop changed during the correction pass")
        last = (fr, meas, rl, be, lp)
        o1, o2 = fr.owners                          # a halfway point belongs to both ends of its segment
        with np.errstate(invalid="ignore"):
            tgt = np.fmax(node_target[o1], node_target[o2])
        # the residual to the planned target, not only to pass (proof fix round: moving the edge g along the curved
        # surface raised the capture by 0.96-1.06 g, so INVU's former failures stopped at 0.4249-0.4856 for 0.45),
        # spread over the nodes each short station's depth lines cross (station_reach)
        moved_pts = (g_total[o1] > 0) | (g_total[o2] > 0)
        # the wall a station's depth lines cross is the lens's own topology, which the passes do not change: read it
        # once on the original lens (proof review fix round 1, speed) and key it by node, so it maps onto each pass's
        # points (a pass may re-sample a segment). Per pass on the moved lens it was identical on every fixture and
        # INVU; on Prada 2 of 355 nodes (40, 351) sat on a back-corner knife edge (w = 0.5 between two 0.02 mm rungs)
        if reach0 is None:
            r0 = station_reach(st0.frames, body.V, band, body.bl)
            np0 = plan_node_points(st0.frames, body.V)
            k_of = {int(p): k for k, p in enumerate(np0.tolist()) if p >= 0}
            reach0 = {k_of[p]: ks for p, ks in r0.items()}
        npc = plan_node_points(fr, V)
        reach = {int(npc[k]): ks for k, ks in reach0.items() if npc[k] >= 0}
        cache: dict = {}            # the point plan, shared by refine_fold's replans (plan_growth `cache`)

        def replan(S, fr=fr, meas=meas, rl=rl, be=be, lp=lp, V=V, tgt=tgt, moved_pts=moved_pts, g_total=g_total,
                   reach=reach, cache=cache):
            return plan_growth(fr, meas, rl, ft, be, lp, V, obstacles, g_max=g_max, already=g_total,
                               fail_only=moved_pts, target_override=tgt, residual=True, aim_mm=CORRECTION_AIM_MM,
                               fold_slope=S, concave_rho=st0.plan.concave_rho, reach=reach,
                               residual_nodes=inside, cache=cache)
        plan = replan(fold_cur)
        if plan.moves:
            plan = refine_fold(body, st0.frames, poly_edges, plan, replan, V_cur=V, frames_cur=fr, roundtrip=roundtrip,
                               band=band)
        fold_cur = plan.fold_slope
        g = plan.node_g if plan.gate is None else np.zeros(nv)
        frames_cur, nodept_cur = fr, plan.node_point
    body1 = _with_V(body, V)
    if passes and last is not None:     # nothing moved since the last measurement: a fresh plan on it
        fr, meas, rl, be, lp = last
        cache1: dict = {}
        p1 = plan_growth(fr, meas, rl, ft, be, lp, V, obstacles, g_max=g_max, cache=cache1)
        if p1.moves:                    # what a second call plans (measure_state refines a moving plan)
            p1 = refine_fold(body1, fr, poly_edges, p1, lambda S: plan_growth(fr, meas, rl, ft, be, lp, V, obstacles,
                                                                              g_max=g_max, fold_slope=S, cache=cache1))
        st1 = BodyState(body1, fr, meas, rl, p1)
    elif passes:
        st1 = measure_state(body1, frame_backend, factory, ft, ranges, obstacles, poly_edges, g_max=g_max)
    else:
        st1 = st0
    pb = np.where((band.partner >= 0)[:, None], body.V[np.maximum(band.partner, 0)], np.nan)
    edge_t = np.linalg.norm(body.V[st0.frames.loop] - pb, axis=1)
    edge_t = np.where(np.isfinite(edge_t), edge_t, float(np.nanmedian(st0.frames.edge_mm)))
    post = array_checks(body, body.V, V, st0.frames.loop, band, g_total, edge_t, st0.frames) if passes else []
    if passes:
        m0 = node_margins(st0.frames, st0.rl, st0.meas)
        m1 = node_margins(st1.frames, st1.rl, st1.meas)
        pf = (m0 >= -rules.PASS_TOLERANCE_MM) & (m1 < -rules.PASS_TOLERANCE_MM)
        if pf.any():
            post.append(f"{int(pf.sum())} nodes went PASS -> FAIL")
    return {"state0": st0, "state1": st1, "V1": V, "g_total": g_total, "disp": disps, "band": band,
            "postcheck": post, "passes": passes, "low_confidence": sorted(set(low))}


def lens_digest(V: np.ndarray, T: np.ndarray) -> str:
    h = hashlib.sha256()
    h.update(np.ascontiguousarray(V, np.float64).tobytes())
    h.update(np.ascontiguousarray(T, np.int64).tobytes())
    return h.hexdigest()[:16]


def seat_arrays(Vr: np.ndarray, Tr: np.ndarray, frame_parts: list, ft: rules.FrameType, factory, *, ranges=None,
                obstacles: list | None = None, poly_edges_raw=None, min_edge_mm: float | None = None,
                roundtrip=_ident, g_max: float = G_MAX_MM, name: str = "lens") -> SeatResult:
    """seat_lens on arrays (§4 steps 1-7 and 11 on the planned arrays). Vr/Tr: the lens object's raw vertices
    (asset mm) and triangles; frame_parts: [(name, V, T)] closed frame components; obstacles: [(name, V, T)]
    non-frame parts the edge must not enter; poly_edges_raw: the mesh's polygon edges (raw ids); min_edge_mm: the
    profile's minimum edge (V-bevel 1.3, groove 1.8; None: none); roundtrip: what Blender stores for a planned
    asset-mm array (float32), so the planner verifies what it will write. Geometry problems never raise.

    Status: refused (code) | partial | seated | already_seated. A lens with no required station (a captured type with
    nothing ENCLOSED or LIPPED) is refused E_NOT_HELD; seated / already_seated also need the binding check's verdict
    on the result to pass (`state_check`), else partial with W_CHECK_FAILS."""
    res = SeatResult("refused")
    rec = stations.recognise(Vr, Tr, name)
    if rec.refused:
        x = rec.refused[0]
        res.code, res.message = x["code"], f"{x['component']}: {x['message']}"
        return res
    if not rec.bodies:
        open_ = [c for c in rec.riders if not c.closed]
        res.code = "E_OPEN" if open_ else "E_NO_BODY"
        res.message = ("the lens is not a closed solid: " + ", ".join(f"{c.name} ({c.topo['boundary_edges']} "
                                                                       f"boundary edges)" for c in open_)
                       if open_ else "no closed lens body at least 0.3 mm thick")
        return res
    if ft.in_range_rule and not ranges:
        res.code = "E_NO_RANGES"
        res.message = ("browline needs the hidden x-ranges (g3d_hidden_x_ranges_mm from hide_eyewire, or "
                       "hidden_x_range_mm)")
        return res
    Vw, vid = geom.weld_exact(Vr)
    fb = factory([(V, T) for _, V, T in frame_parts])
    ob = factory([(V, T) for _, V, T in obstacles]) if obstacles else None
    poly_w = vid[np.asarray(poly_edges_raw, np.int64)] if poly_edges_raw is not None else None
    raw_delta = np.zeros_like(np.asarray(Vr, np.float64))
    rot, cap_of_raw = {}, np.full(len(Vr), -1, np.int64)
    any_move, any_fail, any_blocked, any_check_fail = False, False, False, False
    for body in rec.bodies:
        g2b = np.full(len(Vw), -1, np.int64)
        g2b[body.source_verts] = np.arange(len(body.V))
        pe = None
        if poly_w is not None:
            pe = g2b[poly_w]
            pe = pe[(pe >= 0).all(1) & (pe[:, 0] != pe[:, 1])]
        st0 = measure_state(body, fb, factory, ft, ranges, ob, pe, g_max=g_max)
        edge = float(np.median(st0.frames.edge_mm[st0.frames.ok])) if st0.frames.ok.any() else 0.0
        if min_edge_mm is not None and edge < min_edge_mm - 1e-9:
            res.code = "E_THICKNESS"
            res.message = f"{body.name}: edge thickness {edge:.3f} mm < {min_edge_mm} mm needed by the edge profile"
            return res
        if ft.required != "none" and not np.asarray(st0.rl.required, bool).any():
            # the binding check fails a lens with no required station ('nothing holds this lens'); nothing could be
            # grown either, so the tool refuses instead of calling it seated (review fix round 2: a semi_rimless lens
            # with no frame part came back already_seated / seated, rim sharpened, while the check failed it)
            cls = st0.meas.cls.astype(str)
            res.code = "E_NOT_HELD"
            res.message = (f"{body.name}: nothing holds this lens: no ENCLOSED or LIPPED station among "
                           f"{len(cls)} ({', '.join(f'{k} {int((cls == k).sum())}' for k in stations.CLASSES if (cls == k).any())}) "
                           f"against the frame parts passed ({len(frame_parts)}): pass the parts that hold it, or "
                           f"the right frame type")
            res.bodies.append(body_report(body, st0, None, ft, ranges))
            return res
        if st0.plan.gate is not None:
            res.code, res.message = st0.plan.gate, f"{body.name}: {st0.plan.gate_message}"
            res.bodies.append(body_report(body, st0, None, ft, ranges))
            return res
        out = grow_body(body, fb, factory, ft, ranges, ob, pe, _roundtrip_for(roundtrip, body, Vr, vid), g_max,
                        st0)
        if out["postcheck"]:
            res.code = "E_POSTCHECK"
            res.message = f"{body.name}: " + "; ".join(out["postcheck"])
            blk = st0.plan.counts().get("blocked_by") or {}
            if blk:
                fb = st0.plan.fold_blocks()
                res.message += ("; the plan blocks " + ", ".join(f"{k} {v}" for k, v in blk.items())
                                + " points" + (f" (FOLD: {fb['direct']} direct, {fb['knock_on']} knock-on)" if fb
                                               else "")
                                + ", and growth next to a blocked point steps from its run's need to 0")
            res.bodies.append(body_report(body, st0, out, ft, ranges))
            return res
        D = out["V1"] - body.V
        moved_b = np.any(D != 0, axis=1)
        any_move |= bool(moved_b.any())
        rows = np.flatnonzero(moved_b[g2b[vid]] & (g2b[vid] >= 0))
        raw_delta[rows] = D[g2b[vid[rows]]]
        if moved_b.any():
            # riders of this lens object follow the moved cap faces
            for c in rec.riders:
                rid_rows = _rows_of(c.V, Vr)
                dr = rider_follow(np.asarray(Vr)[rid_rows], body, D)
                m = np.linalg.norm(dr, axis=1) > 0
                raw_delta[rid_rows[m]] = dr[m]
        caps_front = np.zeros(len(body.V), bool)
        caps_front[np.unique(body.T[body.cls == 0])] = True
        caps_back = np.zeros(len(body.V), bool)
        caps_back[np.unique(body.T[body.cls == 1])] = True
        bidx = g2b[vid]
        has = bidx >= 0
        cap_of_raw[has & caps_front[np.maximum(bidx, 0)]] = 0
        cap_of_raw[has & caps_back[np.maximum(bidx, 0)] & ~caps_front[np.maximum(bidx, 0)]] = 1
        R = _compose_rotations(out["disp"], out["band"], st0.frames.loop)
        for r in np.flatnonzero(has).tolist():
            j = int(bidx[r])
            if j in R:
                rot[r] = R[j]
        st1 = out["state1"]
        v1 = st1.plan.verdict.astype(str)
        any_fail |= bool(((v1 == "FAIL") & np.asarray(st1.rl.required, bool)).any() or st1.plan.moves)
        any_blocked |= bool((v1 == "BLOCKED").any())
        res.states.append((st0, st1))
        brep = body_report(body, st0, out, ft, ranges)
        chk = state_check(ft, st1)
        brep["check"] = chk
        if not chk["pass"]:
            any_check_fail = True
            res.warnings.append(f"W_CHECK_FAILS: {body.name}: the binding check's verdict on the result fails: "
                                + "; ".join(chk["reasons"]))
        res.bodies.append(brep)
    res.raw_delta = raw_delta
    res.rot = rot
    res.cap_of_raw = cap_of_raw
    res.moved_raw = int(np.any(raw_delta != 0, axis=1).sum())
    res.plan_status = "partial" if (any_fail or any_blocked) else ("seated" if any_move else "already_seated")
    res.status = "partial" if any_check_fail else res.plan_status
    return res


def state_check(ft: rules.FrameType, st: BodyState) -> dict:
    """The binding check's verdict (rules.check_verdict over rules.verdicts, §8) on one measured state: the counts
    and limits the check applies (0 FAIL / BLOCKED, UNMEASURED <= 1%, TRANSITION <= 3%, a required station at all,
    full-rim 0 EXPOSED / BACKED). The parts of §8 that need the scene or the GLB (the browline band, the rim split,
    the crystal overlap) are graded by the check itself. seat_arrays reports `partial` whenever this fails (review fix
    round 2: the tool said seated / already_seated on lenses the check failed for these reasons)."""
    v = rules.verdicts(ft, st.meas, st.rl, st.frames.s, st.frames.loop_mm)
    if st.plan is not None:                             # the planner's BLOCKED points are failures to the check too
        v = np.where(st.plan.verdict.astype(str) == "BLOCKED", "BLOCKED", v).astype(object)
    c = rules.check_verdict(ft, st.meas, st.rl, v)
    return {"pass": bool(c["pass"]), "reasons": list(c["reasons"]), "required": c["required"],
            "verdicts": {k: n for k, n in c["verdicts"].items() if n}}


def _roundtrip_for(roundtrip, body, Vr, vid):
    """Turn a raw-array roundtrip into a body-array one (every raw duplicate gets the same position)."""
    if roundtrip is _ident:
        return _ident
    first = {}
    gv = body.source_verts
    order = np.argsort(vid, kind="stable")
    for r in order.tolist():
        first.setdefault(int(vid[r]), r)
    rows = np.array([first[int(g)] for g in gv], np.int64)
    base = np.asarray(Vr, np.float64)[rows]

    def rt(Vb):
        return roundtrip(rows, Vb, base)
    return rt


def _rows_of(Vc: np.ndarray, Vr: np.ndarray) -> np.ndarray:
    """Raw rows whose positions are among the component's (welded) positions."""
    key = {tuple(v) for v in np.asarray(Vc).tolist()}
    return np.array([i for i, v in enumerate(np.asarray(Vr).tolist()) if tuple(v) in key], np.int64)


def _compose_rotations(disps, band: LensBand, loop) -> dict:
    """Body vertex -> composed normal rotation over all passes (front-loop vertices: front cap; back-loop: back)."""
    R = {}
    for d in disps:
        for k, M in d.rot_front.items():
            v = int(loop[k])
            R[v] = M @ R.get(v, np.eye(3))
        for vb, M in d.rot_back.items():
            R[int(vb)] = M @ R.get(int(vb), np.eye(3))
    return R


def body_report(body: LensBody, st0: BodyState, out: dict | None, ft: rules.FrameType, ranges) -> dict:
    p0 = st0.plan
    rep = {"body": body.name, "frame_type": ft.name, "lower_edge": ft.lower_edge, "pass_mm": ft.pass_mm,
           "target_mm": ft.target_mm, "points": int(len(st0.frames)), "nodes": int(len(st0.frames.loop)),
           "edge_mm_p50": round(float(np.median(st0.frames.edge_mm[st0.frames.ok])), 4)
           if st0.frames.ok.any() else None,
           "before": p0.counts(), "gate": p0.gate, "gate_message": p0.gate_message or None,
           "clamped_points": int(p0.clamped.sum()), "ranges": [list(r) for r in ranges] if ranges else None}
    if out is not None:
        g = out["g_total"]
        st1 = out["state1"]
        rep.update({"after": st1.plan.counts(), "passes": out["passes"], "nodes_moved": int((g > 0).sum()),
                    "growth_max_mm": round(float(g.max()), 4) if len(g) else 0.0,
                    "growth_p50_moved_mm": round(float(np.median(g[g > 0])), 4) if (g > 0).any() else None,
                    "low_confidence_fit_nodes": len(out["low_confidence"]),
                    "postcheck": out["postcheck"] or None})
        if out["low_confidence"]:
            rep.setdefault("warnings", []).append(f"W_LOW_CONFIDENCE_FIT at {len(out['low_confidence'])} nodes")
        req = np.asarray(st1.rl.required, bool)
        val = st1.rl.value
        if req.any():
            rep["after_value_p5_p50_p95"] = [round(float(x), 4) for x in
                                             np.nanpercentile(np.where(req, val, np.nan), [5, 50, 95])]
    return rep


# ------------------------------------------------------------------------------------------------- lift arrays
@dataclass
class LiftResult:
    status: str                            # lifted | already_hidden | refused
    code: str | None = None
    message: str = ""
    eyewire_delta: np.ndarray | None = None    # (n_raw_eyewire, 3) asset-mm displacement per raw eyewire vertex
    lens_result: SeatResult | None = None      # the held lens's band move (raw deltas, rotations)
    report: dict = field(default_factory=dict)
    plan: LiftPlan | None = None

    def as_dict(self) -> dict:
        return {"status": self.status, "code": self.code, "message": self.message, **self.report}


def plan_node_points(frames, V):
    from .plan import node_points
    return node_points(frames, V)


def _lens_result(body, rec, lens_Vr, out, loop) -> SeatResult:
    """Raw deltas, normal rotations and cap membership of a body move (riders follow)."""
    Vw, vid = geom.weld_exact(lens_Vr)
    g2b = np.full(len(Vw), -1, np.int64)
    g2b[body.source_verts] = np.arange(len(body.V))
    D = out["V1"] - body.V
    res = SeatResult("seated")
    res.raw_delta = np.zeros_like(np.asarray(lens_Vr, np.float64))
    bidx = g2b[vid]
    has = bidx >= 0
    res.raw_delta[has] = D[bidx[has]]
    for c in rec.riders:
        rid_rows = _rows_of(c.V, lens_Vr)
        dr = rider_follow(np.asarray(lens_Vr, np.float64)[rid_rows], body, D)
        m = np.linalg.norm(dr, axis=1) > 0
        res.raw_delta[rid_rows[m]] = dr[m]
    R = _compose_rotations(out["disp"], out["band"], loop)
    res.rot = {r: R[int(bidx[r])] for r in np.flatnonzero(has).tolist() if int(bidx[r]) in R}
    cf = np.zeros(len(body.V), bool)
    cf[np.unique(body.T[body.cls == 0])] = True
    cb = np.zeros(len(body.V), bool)
    cb[np.unique(body.T[body.cls == 1])] = True
    b = np.maximum(bidx, 0)
    res.cap_of_raw = np.where(has & cf[b], 0, np.where(has & cb[b], 1, -1))
    res.moved_raw = int(np.any(res.raw_delta != 0, axis=1).sum())
    return res


def lift_arrays(ew_Vr, ew_Tr, ew_edges_raw, under: list, others: list, lens_Vr, lens_Tr, factory, ranges, *,
                lens_poly_edges_raw=None, margin=None, ramp=None, lift_max=None, roundtrip_ew=_ident,
                roundtrip_lens=_ident, lens_name="lens", eyewire_name="eyewire",
                declared_ranges=None) -> LiftResult:
    """hide_eyewire on arrays (§6). ew_Vr/ew_Tr/ew_edges_raw: the eyewire's raw vertices (asset mm), triangles and
    polygon edges; under / others: [(name, V, T)] closed frame components (the brow; the rest of the frame near the
    lens); lens_Vr/lens_Tr: the held lens. The eyewire must be one closed component. Geometry problems never
    raise: the result says refused with a code. `ranges` are this call's (they decide what is lifted);
    `declared_ranges` the hidden ranges the scene will declare afterwards (the union with earlier calls', default
    `ranges`): the band post-check grades all of them, as the check will."""
    from .plan import LIFT_MARGIN_MM, LIFT_MAX_MM, LIFT_RAMP_MM
    margin = LIFT_MARGIN_MM if margin is None else margin
    ramp = LIFT_RAMP_MM if ramp is None else ramp
    lift_max = LIFT_MAX_MM if lift_max is None else lift_max
    res = LiftResult("refused")
    ew_Vr = np.asarray(ew_Vr, np.float64)
    ewc = stations.split_components(ew_Vr, ew_Tr, eyewire_name)
    if len(ewc) != 1 or not ewc[0].closed:
        res.code = "E_OPEN"
        res.message = (f"the eyewire must be one closed component ({len(ewc)} components, closed "
                       f"{[c.closed for c in ewc]})")
        return res
    for nm, V, T in under:
        if not geom.topology(T, len(V))["closed"]:
            res.code, res.message = "E_OPEN", f"`under` part {nm} is not closed"
            return res
    if not ranges:
        res.code, res.message = "E_NO_RANGES", "hidden_x_range_mm is empty"
        return res
    rec = stations.recognise(lens_Vr, lens_Tr, lens_name)
    if rec.refused or len(rec.bodies) != 1:
        res.code = "E_NO_HELD_LENS"
        res.message = f"the held lens {lens_name} is not one lens body ({len(rec.bodies)} bodies, {rec.refused})"
        return res
    body = rec.bodies[0]
    ew = ewc[0]                                    # one component: its vertices are the eyewire's weld, in order
    Vw_e, vid_e = geom.weld_exact(ew_Vr)
    if len(Vw_e) != len(ew.V) or not np.array_equal(Vw_e, ew.V):
        res.code, res.message = "E_OPEN", "the eyewire has loose vertices outside its surface"
        return res
    parts = [(eyewire_name, ew.V, ew.T)] + list(under) + list(others)
    under_idx = list(range(1, 1 + len(under)))
    fb = factory([(V, T) for _, V, T in parts])
    fr, meas, _, be, lp = measure_only(body, fb, factory, rules.frame_type("metal"), None)
    own = meas.owner[:, MID]
    out_rng = ~rules.in_ranges(fr.P[:, 0], ranges)
    m = (own >= 0) & out_rng
    share = float(np.mean(own[m] == 0)) if m.any() else 0.0
    ub = factory([(V, T) for _, V, T in under])
    E = (vid_e[np.asarray(ew_edges_raw, np.int64)] if ew_edges_raw is not None and len(ew_edges_raw)
         else geom.edge_table(ew.T).edges)
    E = E[E[:, 0] != E[:, 1]]
    # the eyewire moves along the path the lens edge takes (§6 step 5): L u plus the lens surface's own normal offset
    # at that lift (the mean of its cap quadrics, §4 step 6), so a uniformly lifted ring keeps the lens-in-eyewire
    # capture (L u alone shifted q001's by -0.009 mm everywhere), and lambda* is solved along that path
    np_pt = plan_node_points(fr, body.V)
    band0 = lens_band(body, fr, np_pt, None)
    s_node = fr.s[np_pt]

    def path_dn(s_q, tau):
        d = np.abs(np.asarray(s_q, float)[:, None] - s_node[None, :])
        nn = np.minimum(d, fr.loop_mm - d).argmin(1)
        return surface_dn(body, band0, fr, np_pt, nn, tau)
    plan = plan_lift(fr, meas, body.V, fr.P[:, 0], ranges, under_idx, Vw_e, E, ub, margin=margin, ramp=ramp,
                     lift_max=lift_max, path_dn=path_dn)
    res.plan = plan
    loopV = body.V[fr.loop]
    centre_y = float(0.5 * (loopV[:, 1].min() + loopV[:, 1].max()))
    lam = plan.lam[np.isfinite(plan.lam)]
    res.report = {"held_share_outside_ranges": round(share, 4), "selected_stations": int(plan.selected.sum()),
                  "rigid_rings": plan.rigid, "rings": int(len(plan.ring_s)), "cover_rings": plan.cover_rings,
                  "lambda_p50_max_mm": [round(float(np.median(lam)), 4), round(float(lam.max()), 4)]
                  if len(lam) else None,
                  "lift_max_mm": round(float(plan.lift_vertex.max()), 4) if len(plan.lift_vertex) else 0.0,
                  "slope_max": round(plan.slope, 4), "strain": plan.strain,
                  "eyewire_vertices_moved": int((plan.lift_vertex > 0).sum()),
                  "lens_nodes_moved": int((plan.node_g > 0).sum()), "lens_centre_y": round(centre_y, 4)}
    if share < stations.HELD_SHARE:
        res.code = "E_NO_HELD_LENS"
        res.message = (f"the eyewire is the innermost part at {share:.0%} of {lens_name}'s stations outside the "
                       f"ranges (< 60%): pass the lens it holds")
        return res
    if plan.gate:
        res.code, res.message = plan.gate, plan.gate_message
        return res
    if not (plan.lift_vertex > 0).any():
        res.status = "already_hidden"
        res.eyewire_delta = np.zeros_like(ew_Vr)
        return res
    ew_delta = plan.disp[vid_e]
    ew_delta = roundtrip_ew(ew_Vr + ew_delta) - ew_Vr
    # the held lens's band follows by L(s) (§6 step 5)
    _, vid_l = geom.weld_exact(lens_Vr)
    g2b = np.full(int(vid_l.max()) + 1, -1, np.int64)
    g2b[body.source_verts] = np.arange(len(body.V))
    pe = None
    if lens_poly_edges_raw is not None and len(lens_poly_edges_raw):
        pe = g2b[vid_l[np.asarray(lens_poly_edges_raw, np.int64)]]
        pe = pe[(pe >= 0).all(1) & (pe[:, 0] != pe[:, 1])]
    ft = rules.frame_type("metal")
    st0 = BodyState(body, fr, meas, rules.rule_values(ft, meas, fr.P[:, 0], None), None)
    rt_lens = _roundtrip_for(roundtrip_lens, body, lens_Vr, vid_l)
    out = grow_body(body, fb, factory, ft, None, None, pe, rt_lens, state0=st0, g_node_override=plan.node_g)
    ew1 = ew_Vr + ew_delta
    ew2 = stations.split_components(ew1, ew_Tr, eyewire_name)[0]
    parts2 = [(eyewire_name, ew2.V, ew2.T)] + list(under) + list(others)
    fb2 = factory([(V, T) for _, V, T in parts2])
    # one correction of the follow (§6 step 5): the lens node lift is the ring lifts interpolated at its arc
    # parameter, but on a steep ramp the rings move along their own u, so the eyewire's face at a node's station
    # moves a few micrometres more or less than that (q001 [-50, -36]: +0.022 mm at the ramp foot x -51.8). The
    # capture change is linear in the node's move along u, so g - dc restores it.
    moved0 = np.flatnonzero(plan.node_g > 0)
    if len(moved0):
        pts0 = np_pt[moved0]
        dc = _capture_change(fr, be, fb2, factory, body, _with_V(body, out["V1"]), out["band"].partner, pts0, lp)
        fix = np.isfinite(dc) & (np.abs(dc) > 1e-6)
        res.report["follow_correction_max_mm"] = round(float(np.abs(dc[fix]).max()), 4) if fix.any() else 0.0
        if fix.any():
            g2 = plan.node_g.copy()
            g2[moved0[fix]] = np.maximum(g2[moved0[fix]] - dc[fix], 0.0)
            plan.node_g = g2
            plan.slope = plan_lift_slope(plan)
            out = grow_body(body, fb, factory, ft, None, None, pe, rt_lens, state0=st0, g_node_override=g2)
            res.report.update(lens_nodes_moved=int((g2 > 0).sum()), slope_max=round(plan.slope, 4))
    lens_res = _lens_result(body, rec, lens_Vr, out, fr.loop)
    post = [x for x in out["postcheck"] if "PASS -> FAIL" not in x]       # the lens follows: no rule applies
    body2 = _with_V(body, out["V1"])
    fr2, meas2, _, be2, _ = measure_only(body2, fb2, factory, rules.frame_type("metal"), None)
    checks = lift_postchecks(fr, be, fr2, be2, lp, plan, body, body2, ew_Vr, ew_delta, centre_y,
                             out["band"].partner, meas2=meas2,
                             ranges=declared_ranges if declared_ranges else ranges, ew_mesh0=(ew.V, ew.T),
                             ew_mesh1=(ew2.V, ew2.T))
    res.report.update(checks["report"])
    post += checks["failures"]
    if post:
        res.code = "E_POSTCHECK"
        res.message = "; ".join(post)
        return res
    res.status = "lifted"
    res.eyewire_delta = ew_delta
    res.lens_result = lens_res
    return res


SECTION_SPAN_MM = (-2.0, 4.0)      # the section window along u from the mid-depth edge point (the band's u range)


def section_area(frames: Frames, idx, V: np.ndarray, T: np.ndarray, span=SECTION_SPAN_MM,
                 half_n: float = stations.BAND_WINDOW_MM) -> np.ndarray:
    """Exact area (mm2) of the closed mesh (V, T) cut by each station plane (through the mid-depth edge point,
    normal t), counting the section curves whose segments lie in the window u in `span`, |n| <= half_n (the eyewire
    near the station; the far side of the loop is outside it). Each triangle crossing the plane gives one segment,
    oriented by the triangle's outward normal, and the area is the shoelace sum over them.

    Column sampling (lines along n every 0.025 mm, the band's grid) cannot resolve the +-1% of §6 step 6: a wall of
    the 1.8 mm high eyewire parallel to n gains or loses a whole column under a sub-column shift, 0.045 mm2 = 4% of
    q001's 1.06 mm2 section (the review's 0.8% P2 figure was that and the turned post-move frames)."""
    idx = np.asarray(idx, np.int64)
    V = np.asarray(V, np.float64)
    T = np.asarray(T, np.int64).reshape(-1, 3)
    out = np.zeros(len(idx))
    if not len(idx) or not len(T):
        return out
    Em = frames.depth_points()[idx, MID]
    A, B, C = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
    Nf = np.cross(B - A, C - A)
    for q in range(len(idx)):
        o, t, u, n = Em[q], frames.t[idx[q]], frames.u[idx[q]], frames.n[idx[q]]
        h = (V - o) @ t
        pos = h >= 0.0
        pts = []
        for i, j in ((0, 1), (1, 2), (2, 0)):
            a, b = T[:, i], T[:, j]
            m = pos[a] != pos[b]
            X = np.full((len(T), 3), np.nan)
            w = h[a[m]] / (h[a[m]] - h[b[m]])
            X[m] = V[a[m]] + w[:, None] * (V[b[m]] - V[a[m]])
            pts.append(X)
        X = np.stack(pts, 1)                                 # (m, 3 edges, 3)
        cut = np.isfinite(X[:, :, 0]).sum(1) == 2
        if not cut.any():
            continue
        Xc = X[cut]
        first = np.isfinite(Xc[:, :, 0])
        k0 = first.argmax(1)
        k1 = 2 - first[:, ::-1].argmax(1)
        r = np.arange(len(Xc))
        p, s = Xc[r, k0] - o, Xc[r, k1] - o
        d = s - p
        flip = (np.cross(d, t) * Nf[cut]).sum(1) < 0
        p, s = np.where(flip[:, None], s, p), np.where(flip[:, None], p, s)
        mid = 0.5 * (p + s)
        inw = (mid @ u >= span[0]) & (mid @ u <= span[1]) & (np.abs(mid @ n) <= half_n)
        out[q] = abs(0.5 * float((np.cross(p[inw], s[inw]) @ t).sum()))
    return out


def plan_lift_slope(plan: LiftPlan) -> float:
    from .plan import lift_slope
    return lift_slope(plan.ring_s, plan.ring_lift, plan.node_s, plan.node_g, plan.loop_mm)


def _capture_change(fr0: Frames, be0, fb_post, factory, body0: LensBody, body1: LensBody, partner, pts,
                    lens_part: int) -> np.ndarray:
    """Mid-depth lens-in-eyewire (part 0) capture after minus before at points `pts`, along the pre-move u (the
    carried frames); nan where either has none."""
    be1 = Stacked([fb_post, factory([(body1.V, body1.T)])])
    frm = carried_frames(fr0, body0, body1, partner, pts)
    c0 = _part_capture(fr0, be0, pts, 0, lens_part)
    c1 = _part_capture(frm, be1, np.arange(len(pts)), 0, lens_part)
    return c1 - c0


def _part_capture(fr: Frames, be, pts, part: int, lens_part: int) -> np.ndarray:
    """Mid-depth capture by one frame part at the given points (nan when none within reach)."""
    from .intervals import line_intervals
    pts = np.asarray(pts, np.int64)
    E = fr.depth_points()[pts, MID]
    res = line_intervals(be, E, fr.u[pts], fr.t[pts])
    out = np.full(len(pts), np.nan)
    for q in range(len(pts)):
        per = res.intervals[q]
        e = stations.lens_edge(per[lens_part])
        c, _, _ = stations.capture_along([per[part]], edge=e)
        out[q] = c
    return out


def carried_frames(fr0: Frames, body0: LensBody, body1: LensBody, partner: np.ndarray, idx) -> Frames:
    """The pre-move station frames at points `idx`, carried along by the lens move: each point's front corner moves
    with the two loop vertices of its segment (linear in its position on it), its back corner with their rung
    partners (the front move when a node has none); n, t and u stay the pre-move ones. On a lift ramp the post-move
    frames turn with the outline (q001's temporal ramp end: ~20-24 deg), so a rigidly translated eyewire ring is cut
    at an angle there; in the carried plane it is the same section, translated."""
    idx = np.asarray(idx, np.int64)
    loop = fr0.loop
    nv = len(loop)
    D = body1.V - body0.V
    FL = body0.V[loop]
    seg = np.linalg.norm(np.roll(FL, -1, 0) - FL, axis=1)
    sv = np.r_[0.0, np.cumsum(seg)][:-1]
    s = fr0.s[idx]
    k = np.clip(np.searchsorted(sv, s, side="right") - 1, 0, nv - 1)
    k1 = (k + 1) % nv
    w = np.clip((s - sv[k]) / np.maximum(seg[k], 1e-30), 0.0, 1.0)[:, None]
    Df = (1 - w) * D[loop[k]] + w * D[loop[k1]]
    pb = np.asarray(partner, np.int64)
    Db0 = np.where((pb[k] >= 0)[:, None], D[np.maximum(pb[k], 0)], D[loop[k]])
    Db1 = np.where((pb[k1] >= 0)[:, None], D[np.maximum(pb[k1], 0)], D[loop[k1]])
    Db = (1 - w) * Db0 + w * Db1
    return stations.frames_from_arrays(fr0.P[idx] + Df, fr0.Pb[idx] + Db, fr0.n[idx], fr0.t[idx], fr0.u[idx],
                                       fr0.s[idx])


def lift_postchecks(fr0, be0, fr1, be1, lens_part: int, plan: LiftPlan, body0, body1, ew_Vr, ew_delta,
                    centre_y: float, band_partner: np.ndarray | None = None, meas2: Measurement | None = None,
                    ranges=None, ew_mesh0=None, ew_mesh1=None) -> dict:
    """§6 step 6 on the planned arrays:
    - the eyewire (part 0) visible along +-n 0 and its area outside every other frame part 0 (< 0.005 mm2), at
      EVERY selected measurement point (review fix round 1; nodes alone missed the interpolated stations past the
      last selected ring): in the pre-move planes carried along by the move, and at the check's graded set on the
      moved scene (in-range band candidates of the post-move measurement `meas2`, over `ranges`), so the tool never
      reports a lift the binding check fails;
    - its section area +-1%, compared in the carried pre-move planes (the post-move frames turn up to ~24 deg on a
      ramp and read a shear that does not exist: q001 [-56.2, -40] read 4.06%, 0.055% in the carried planes);
    - lift slope <= 0.5 mm/mm; nothing below the lens centre moves; lens-in-eyewire capture within +-0.02 mm at
      every moved node (carried frames as well)."""
    fails, rep = [], {}
    np0 = plan_node_points(fr0, body0.V)
    part_b = band_partner if band_partner is not None else np.full(len(fr0.loop), -1)
    sel = np.flatnonzero(plan.selected)
    if len(sel):
        frc = carried_frames(fr0, body0, body1, part_b, sel)
        f, b, a = stations.band_widths(frc, np.arange(len(sel)), 0, be1, lens_part)
        band = {"points": int(len(sel)), "front_max_mm": round(float(f.max()), 4),
                "back_max_mm": round(float(b.max()), 4), "area_outside_max_mm2": round(float(a.max()), 4)}
        visible = (f > 0) | (b > 0) | (a >= 0.005)
        if meas2 is not None and ranges:
            in2 = rules.in_ranges(fr1.P[:, 0], ranges) & stations.layered(meas2)
            bd = stations.buried_band(fr1, meas2, be1, lens_part, held_from=~in2, only=in2)
            g = bd.candidate & in2
            band["graded_points"] = int(g.sum())
            band["graded_other_buried"] = int((g & (bd.buried != 0)).sum())
            if g.any():
                f2, b2, a2 = bd.front_mm[g], bd.back_mm[g], bd.area_outside_mm2[g]
                band.update(graded_front_max_mm=round(float(f2.max()), 4), graded_back_max_mm=round(float(b2.max()), 4),
                            graded_area_outside_max_mm2=round(float(a2.max()), 4))
                vis2 = (f2 > 0) | (b2 > 0) | (a2 >= 0.005)
                band["graded_visible_points"] = int(vis2.sum())
                if vis2.any():
                    xs = fr1.P[g][vis2, 0]
                    fails.append(f"eyewire still visible at {int(vis2.sum())} of {int(g.sum())} graded stations of the "
                                 f"moved scene (asset x {xs.min():.2f}..{xs.max():.2f}; front {f2.max():.3f}, back "
                                 f"{b2.max():.3f} mm, area outside {a2.max():.3f} mm2)")
        rep["band_after"] = band
        if visible.any():
            xs = frc.P[visible, 0]
            fails.append(f"eyewire still visible at {int(visible.sum())} of {len(sel)} selected stations (asset x "
                         f"{xs.min():.2f}..{xs.max():.2f}; front {f.max():.3f}, back {b.max():.3f} mm, area outside "
                         f"{a.max():.3f} mm2)")
        if ew_mesh0 is not None and ew_mesh1 is not None:
            s0 = section_area(fr0, sel, *ew_mesh0)
            s1 = section_area(frc, np.arange(len(sel)), *ew_mesh1)
        else:
            s0 = s1 = np.zeros(0)
        ok = s0 > 1e-6
        rel = np.abs(s1[ok] - s0[ok]) / s0[ok]
        rep["section_area_rel_max"] = round(float(rel.max()), 5) if len(rel) else None
        if len(rel) and rel.max() > LIFT_SECTION_TOLERANCE:
            fails.append(f"eyewire section area changed by {rel.max():.2%} (limit 1%)")
    rep["slope_max"] = round(plan.slope, 4)
    if plan.slope > LIFT_SLOPE_MAX + 1e-9:
        fails.append(f"lift slope {plan.slope:.3f} mm/mm > {LIFT_SLOPE_MAX}")
    low_ew = int(((np.linalg.norm(ew_delta, axis=1) > 0) & (np.asarray(ew_Vr)[:, 1] < centre_y)).sum())
    low_lens = int(((plan.node_g > 0) & (body0.V[fr0.loop][:, 1] < centre_y)).sum())
    rep["moved_below_centre"] = {"eyewire_vertices": low_ew, "lens_nodes": low_lens}
    if low_ew or low_lens:
        fails.append(f"vertices below the lens centre moved (eyewire {low_ew}, lens nodes {low_lens})")
    moved = np.flatnonzero(plan.node_g > 0)
    if len(moved):
        # along the direction both moved (the pre-move u), from the moved node: on a ramp the post-move station
        # frame rotates with the outline (q001's temporal ramp end: ~20 deg), which is no relative motion
        pts = np0[moved]
        fr_m = carried_frames(fr0, body0, body1, part_b, pts)
        c0 = _part_capture(fr0, be0, pts, 0, lens_part)
        c1 = _part_capture(fr_m, be1, np.arange(len(moved)), 0, lens_part)
        ok = np.isfinite(c0) & np.isfinite(c1)
        dc = np.abs(c1[ok] - c0[ok])
        rep["eyewire_capture_change_max_mm"] = round(float(dc.max()), 4) if len(dc) else None
        if len(dc) and dc.max() > LIFT_CAPTURE_TOLERANCE_MM:
            fails.append(f"lens-in-eyewire capture changed by {dc.max():.3f} mm at a moved node (limit 0.02)")
    return {"failures": fails, "report": rep}
