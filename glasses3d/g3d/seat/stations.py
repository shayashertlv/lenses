"""Lens bodies, measurement points, station frames and the per-point measurement (WP4_DESIGN.md §3). numpy only.

Body: one closed manifold component with effective thickness 2V/A >= 0.3 mm; an open or thinner component is a RIDER
(a marking: Prada etch 0.022 mm, OAKLEY 0.003, PRIZM 0.002, INVU print) and is excluded from bodies and frame solids.
A body whose surface passes through itself is refused (E_SELF_INTERSECTING, §10 P4), in the tool and the check.
(§3 also names chi = 2; a drilled rimless lens is genus 1, so chi is recorded and inner rim loops are reported, never
grown, instead of turning a drilled lens into a rider.)

Points: the front rim vertices are the move NODES; measurement points every <= 0.5 mm along the outer front loop,
each owned by the nearer node (a point exactly halfway along its segment by both: `Frames.owners`). Station frame per point: n = area-weighted normal of front-cap faces within 5 mm and
>= 0.8 mm inside the edge; t = the loop chord to the first vertices >= 0.3 mm away each way, made perpendicular to n;
u = t x n with the loop oriented counter-clockwise about the mean front normal (outward also in nose notches);
back corner = station plane (normal t) cut with the back loop, nearest to the front corner.

Per point, lines along u through the 5 depth points (fractions of the front->back corner segment; the edge on each
line is where it leaves the lens body, given as the backend's last part, so a chamfer or bullnose counts what it
buries) and along +n / -n from the corners moved 0.10 mm inward give capture c(f), room(f), owner, cover front/back
and the CLASS:

    UNMEASURED  no back corner, or a line whose parity stayed odd after the re-casts
    EXPOSED     no frame within REACH (3.0 mm) along any depth line, nor covering within 3.0 mm
    ENCLOSED    c >= 0 at all five depths
    LIPPED      c(0.5) >= -1.0 and the FRONT of the edge is held: the front corner covered along +n, or frame at the
                front depth (0.05) within the captured gap (-1.0 mm), so growth buries the front corner (the second
                alternative is not 'lipped' in §3's words; see below)
    BACKED      front not covered and frame only at the back: back corner covered, or c(0.95) >= -1.0 (a rear seat)
    NEAR        anything else within reach (a 1-3 mm gap at mid-depth)

§3 words LIPPED as 'c(0.5) >= -1.0, front covered' and BACKED as 'frame within reach, front not covered'. Read
literally, a lens edge beside a rail with a 0.1-0.45 mm gap and no lip (INVU's top at |x| 50-65: frame at every
depth, nothing over either face) is BACKED, i.e. visible by design on a semi-rimless frame and never grown: exactly the
edge-to-edge failure WP4 exists to remove (§2 'Shields'). Holding the front depth separates it from a true rear seat
(Oakley's latch housings and nose saddle, INVU's nose trim: no frame at the front depth), measured on all four
finals (WP4_DESIGN 'Implementation notes'). The relaxation only decides which semi_rimless stations are REQUIRED:
the thin rule on a full rim (metal, browline outside its ranges) still needs the front corner covered along +n, as
§7 states (rules.py), so an eyewire without a front lip fails there whatever its class.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field

import numpy as np

from . import geom
from .geom import SeatError
from .intervals import LineIntervals, LineResult, line_intervals, merge

DEPTHS = (0.05, 0.25, 0.5, 0.75, 0.95)     # §2 'Depth samples': fractions of the rung from the front corner
MID = 2                                    # index of the 0.5 depth
REACH_MM = 3.0                             # §7 REACH
CAPTURED_GAP_MM = 1.0                      # §7 captured/exposed gap
COVER_REACH_MM = 6.0                       # §3 cover: the +n line meets frame within 6 mm
HIDDEN_MARGIN_MM = 0.10                    # §7 hidden margin (the cover lines start 0.10 mm inside the edge)
COVER_NUDGE_MM = 1e-4                      # ... plus this, so a frame face lying exactly 0.10 mm inside the edge (P4's
                                           # flat-lens acetate ring) never holds the cover line, where backends differ
CLASS_EPS_MM = 1e-4                        # class thresholds (c >= 0, c >= -1.0) tolerate float32 backend noise
POINT_SPACING_MM = 0.5                     # §7 point spacing <= 0.5
NORMAL_RADIUS_MM = 5.0                     # §3 station normal: front-cap faces within 5 mm ...
NORMAL_INSET_MM = 0.8                      # ... and >= 0.8 mm inside the edge
CHORD_MM = 0.3                             # §3 t: loop chord walking >= 0.3 mm each way
BACK_CORNER_MAX_MM = 6.0                   # back corner farther than this from the front corner: none
BODY_MIN_THICKNESS_MM = 0.3                # §3 body: effective thickness 2V/A >= 0.3 mm
CLASSES = ("ENCLOSED", "LIPPED", "BACKED", "NEAR", "EXPOSED", "UNMEASURED")
SECTORS = ("top", "bottom", "nasal", "temporal")


# ------------------------------------------------------------------------------------------------------- bodies
@dataclass
class LensBody:
    name: str
    V: np.ndarray                  # welded positions, world mm
    T: np.ndarray                  # triangles into V, outward winding
    source_faces: np.ndarray       # index of each triangle in the triangle array given to recognise()
    source_verts: np.ndarray       # welded-global index of each V row (recognise()'s weld)
    cls: np.ndarray                # per triangle: 0 front cap, 1 back cap, 2 wall
    theta_deg: float
    cap_share: float
    front_loops: list              # cap/wall loops of the front cap, longest first (outer edge = [0])
    back_loops: list
    topo: dict
    volume_mm3: float
    area_mm2: float
    thickness_eff_mm: float
    flipped: bool                  # the input winding was inside out (fixed here)

    @property
    def fl(self) -> np.ndarray:
        return self.front_loops[0]

    @property
    def bl(self) -> np.ndarray:
        return self.back_loops[0]

    @property
    def perimeter_mm(self) -> float:
        return geom.loop_perimeter(self.V, self.fl)

    @property
    def inner_loops(self) -> int:
        return len(self.front_loops) - 1

    def cap_triangles(self, nondegenerate: bool = False) -> tuple[int, int]:
        """Triangles per cap; `nondegenerate` leaves out slivers below geom.DEGENERATE_AREA_MM2 (the §3 counts)."""
        keep = geom.face_geometry(self.V, self.T)[1] >= geom.DEGENERATE_AREA_MM2 if nondegenerate else True
        return int(((self.cls == 0) & keep).sum()), int(((self.cls == 1) & keep).sum())

    def summary(self) -> dict:
        nf, nb = self.cap_triangles()
        P = self.V[self.fl]
        return {"name": self.name, "vertices": int(len(self.V)), "triangles": int(len(self.T)),
                "cap_triangles": [nf, nb], "cap_triangles_nondegenerate": list(self.cap_triangles(True)),
                "wall_triangles": int((self.cls == 2).sum()),
                "theta_deg": self.theta_deg, "cap_area_share": round(self.cap_share, 4),
                "front_loop_mm": round(self.perimeter_mm, 3),
                "front_loops": [round(geom.loop_perimeter(self.V, lp), 3) for lp in self.front_loops],
                "back_loops": [round(geom.loop_perimeter(self.V, lp), 3) for lp in self.back_loops],
                "outline_mm": [round(float(np.ptp(P[:, 0])), 3), round(float(np.ptp(P[:, 1])), 3)],
                "volume_mm3": round(self.volume_mm3, 3), "thickness_eff_mm": round(self.thickness_eff_mm, 4),
                "euler": self.topo["euler"], "drilled_holes": self.inner_loops, "winding_flipped": self.flipped}


@dataclass
class Component:
    """A connected component that is not a lens body (a rider, an open part) or a closed frame solid."""
    name: str
    V: np.ndarray
    T: np.ndarray
    closed: bool
    thickness_eff_mm: float
    topo: dict

    @property
    def box(self) -> np.ndarray:
        return np.stack([self.V.min(0), self.V.max(0)])


@dataclass
class Recognition:
    bodies: list = field(default_factory=list)       # LensBody
    riders: list = field(default_factory=list)       # Component (open or thinner than 0.3 mm)
    refused: list = field(default_factory=list)      # {"component": i, "code": ..., "message": ...}


def split_components(V, T, name: str) -> list[Component]:
    """Exact weld, then one Component per connected component (deterministic order: by smallest vertex)."""
    Vw, vid = geom.weld_exact(V)
    Tw = geom.drop_degenerate_index(vid[np.asarray(T, np.int64).reshape(-1, 3)])
    if len(Tw) == 0:
        return []
    _, flab = geom.components(Tw, len(Vw))
    out = []
    for k, lab in enumerate(np.unique(flab).tolist()):
        Tk = Tw[flab == lab]
        used = np.unique(Tk)
        remap = np.full(len(Vw), -1, np.int64)
        remap[used] = np.arange(len(used))
        Vk, Tk = Vw[used], remap[Tk]
        topo = geom.topology(Tk, len(Vk))
        out.append(Component(f"{name}#{k}" if k else name, Vk, Tk, topo["closed"],
                             geom.effective_thickness(Vk, Tk) if topo["closed"] else 0.0, topo))
    return out


def is_solid(c: Component) -> bool:
    return c.closed and c.thickness_eff_mm >= BODY_MIN_THICKNESS_MM


def recognise(V, T, name: str = "lens") -> Recognition:
    """Bodies and riders of one lens object (§3 'Lens body')."""
    V = np.asarray(V, np.float64).reshape(-1, 3)
    T = np.asarray(T, np.int64).reshape(-1, 3)
    rec = Recognition()
    Vw, vid = geom.weld_exact(V)
    Tw_all = vid[T]
    keep = (Tw_all[:, 0] != Tw_all[:, 1]) & (Tw_all[:, 1] != Tw_all[:, 2]) & (Tw_all[:, 0] != Tw_all[:, 2])
    src = np.flatnonzero(keep)
    Tw = Tw_all[keep]
    if len(Tw) == 0:
        return rec
    _, flab = geom.components(Tw, len(Vw))
    for k, lab in enumerate(np.unique(flab).tolist()):
        m = flab == lab
        Tk, srck = Tw[m], src[m]
        used = np.unique(Tk)
        remap = np.full(len(Vw), -1, np.int64)
        remap[used] = np.arange(len(used))
        Vk, Tk = Vw[used], remap[Tk]
        cname = f"{name}#{k}" if k else name
        topo = geom.topology(Tk, len(Vk))
        thick = geom.effective_thickness(Vk, Tk) if topo["closed"] else 0.0
        comp = Component(cname, Vk, Tk, topo["closed"], thick, topo)
        if not is_solid(comp):
            rec.riders.append(comp)
            continue
        try:
            rec.bodies.append(build_body(Vk, Tk, cname, srck, used, topo))
        except SeatError as exc:
            rec.refused.append({"component": cname, "code": exc.code, "message": exc.message})
    return rec


def build_body(V, T, name, source_faces, source_verts, topo) -> LensBody:
    vol = geom.signed_volume(V, T)
    flipped = vol < 0
    if flipped:
        T = T[:, ::-1].copy()
        vol = -vol
    if topo["misoriented_edges"]:
        raise SeatError("E_WINDING", f"{topo['misoriented_edges']} edges join faces of opposite winding "
                                     f"(recalculate normals outside)")
    sx = geom.self_intersections_cached(V, T)
    if len(sx):
        # §10 P4 refusal 'self-intersection': a lens surface that passes through itself has no inside to measure
        # from (parity), so nothing about its edge can be graded or grown
        E = geom.edge_table(T).edges
        at = 0.5 * (V[E[sx[0, 0], 0]] + V[E[sx[0, 0], 1]])
        raise SeatError("E_SELF_INTERSECTING",
                        f"the lens passes through itself: {len(np.unique(sx[:, 0]))} edges cross its own surface "
                        f"(first near x {at[0]:.2f}, y {at[1]:.2f}, z {at[2]:.2f} mm); rebuild the lens")
    split = geom.split_caps(V, T)
    loops = geom.rim_loops(V, T, split.cls)
    if not loops[0] or not loops[1]:
        raise SeatError("E_NO_CAPS", "a cap has no boundary with the wall")
    area = geom.surface_area(V, T)
    return LensBody(name, V, T, np.asarray(source_faces), np.asarray(source_verts), split.cls, split.theta_deg,
                    split.area_share, loops[0], loops[1], topo, vol, area, 2 * vol / area, flipped)


# ------------------------------------------------------------------------------------------------------- frames
@dataclass
class Frames:
    """Station frames along the outer front loop.

    `loop` holds the body-vertex ids (rows of LensBody.V) of that loop in the ORIENTED order used here
    (counter-clockwise about the mean front normal: body.fl itself, or body.fl reversed). `node[k]` indexes `loop`,
    never body.fl; `node_vertex[k] = loop[node[k]]` is the owning rim vertex as a body-vertex id. External frames
    (`frames_from_arrays`) have an empty loop and node -1."""
    P: np.ndarray          # (N, 3) front corner (measurement point on the outer front loop)
    Pb: np.ndarray         # (N, 3) back corner (nan when none)
    n: np.ndarray          # (N, 3) station normal (front-cap side)
    t: np.ndarray          # (N, 3) tangent
    u: np.ndarray          # (N, 3) outward in the lens surface
    s: np.ndarray          # (N,) arc parameter along the oriented loop
    node: np.ndarray       # (N,) owning node = index into `loop` (-1 for external frames)
    ok: np.ndarray         # (N,) has a back corner
    loop_mm: float = 0.0
    loop: np.ndarray = field(default_factory=lambda: np.zeros(0, np.int64))   # body-vertex ids, oriented
    node2: np.ndarray | None = None    # (N,) second owner: the segment's END node for a point exactly at its middle
                                       # (w = 0.5), else = node. None: node.

    def __len__(self) -> int:
        return len(self.P)

    @property
    def owners(self) -> tuple[np.ndarray, np.ndarray]:
        """(node, node2): the nodes owning each point. A point exactly halfway along its segment belongs to BOTH
        ends; any other point to the nearer one (node2 = node). The loop runs counter-clockwise about the front
        normal, so a mirrored lens's loop runs the other way along the mirrored segments: giving a halfway point to
        the segment's start alone grew a lens and its mirror differently (review fix round 2: FT q001 left vs
        mirrored right differed by up to 0.054 mm, 68 tie points per lens)."""
        return self.node, (self.node if self.node2 is None else self.node2)

    @property
    def node_vertex(self) -> np.ndarray:
        """(N,) body-vertex id of each point's owning rim vertex (-1 for external frames)."""
        if len(self.loop) == 0:
            return np.full(len(self.node), -1, np.int64)
        return np.where(self.node >= 0, self.loop[np.maximum(self.node, 0)], -1)

    @property
    def sector(self) -> np.ndarray:
        return sectors(self.u, self.P)

    @property
    def edge_mm(self) -> np.ndarray:
        return np.linalg.norm(self.Pb - self.P, axis=1)

    def depth_points(self) -> np.ndarray:
        """(N, 5, 3) edge points at the five depths."""
        f = np.asarray(DEPTHS)[None, :, None]
        return self.P[:, None, :] + f * (self.Pb - self.P)[:, None, :]


def sectors(u, P) -> np.ndarray:
    """§3: sector by the direction of u (asset frame: +Y up, x = 0 at the bridge)."""
    u = np.asarray(u)
    P = np.asarray(P)
    side = np.where(np.sign(u[:, 0]) == -np.sign(P[:, 0]), "nasal", "temporal")
    return np.where(u[:, 1] > 0.707, "top", np.where(u[:, 1] < -0.707, "bottom", side))


def oriented_front_loop(body: LensBody) -> tuple[np.ndarray, np.ndarray]:
    """(outer front loop, mean front normal), the loop counter-clockwise about the mean front-cap normal."""
    fn, fa, _ = geom.face_geometry(body.V, body.T)
    sel = body.cls == 0
    nm = (fn[sel] * fa[sel, None]).sum(0)
    nm /= np.linalg.norm(nm)
    loop = body.fl
    P = body.V[loop]
    e1 = np.cross(nm, [1.0, 0.0, 0.0]) if abs(nm[0]) < 0.9 else np.cross(nm, [0.0, 1.0, 0.0])
    e1 /= np.linalg.norm(e1)
    e2 = np.cross(nm, e1)
    q = np.c_[P @ e1, P @ e2]
    area2 = float(np.sum(q[:, 0] * np.roll(q[:, 1], -1) - np.roll(q[:, 0], -1) * q[:, 1]))
    return (loop if area2 > 0 else loop[::-1].copy()), nm


def station_frames(body: LensBody, spacing: float = POINT_SPACING_MM) -> Frames:
    loop, _ = oriented_front_loop(body)
    FL = body.V[loop]
    nv = len(loop)
    seg = np.linalg.norm(np.roll(FL, -1, 0) - FL, axis=1)
    sv = np.r_[0.0, np.cumsum(seg)][:-1]
    L = float(seg.sum())
    m = np.maximum(1, np.ceil(seg / spacing - 1e-9).astype(np.int64))
    seg_of = np.repeat(np.arange(nv), m)
    j = np.arange(int(m.sum())) - np.repeat(np.cumsum(m) - m, m)
    w = j / m[seg_of]
    P = FL[seg_of] + w[:, None] * (np.roll(FL, -1, 0)[seg_of] - FL[seg_of])
    s = sv[seg_of] + w * seg[seg_of]
    node = np.where(w <= 0.5, seg_of, (seg_of + 1) % nv)
    node2 = np.where(2 * j == m[seg_of], (seg_of + 1) % nv, node)      # halfway points: both ends (Frames.owners)
    # normals from the cap interior near each point
    fn, fa, fc = geom.face_geometry(body.V, body.T)
    fsel = np.flatnonzero(body.cls == 0)
    reach = NORMAL_RADIUS_MM + 2 * float(seg.max())
    _, dl, _ = geom.nearest_on_polyline(FL, fc[fsel], max_dist=reach)      # farther ones: inf (never near)
    near = dl < reach
    fsel, dl = fsel[near], dl[near]
    C, Nf, A = fc[fsel], fn[fsel], fa[fsel]
    far = dl >= NORMAL_INSET_MM
    n = np.zeros_like(P)
    NA = Nf * A[:, None]
    # distances in blocks of points (the same values as one point at a time; the per-point sums stay row sums over
    # the same faces, so the normals are bit-identical). Only faces within 2 x NORMAL_RADIUS_MM of a point can be
    # selected, so each block measures the faces inside its points' box grown by that much, kept in their original
    # order (the selected rows, and so the sums, are the same; proof review fix round 1, speed: INVU's 24k near
    # faces against all 1494 points took ~1 s of every call)
    blk = 32
    r2 = 2 * NORMAL_RADIUS_MM
    for b0 in range(0, len(P), blk):
        Pk = P[b0:b0 + blk]
        cand = np.flatnonzero(((C >= Pk.min(0) - r2) & (C <= Pk.max(0) + r2)).all(1))
        Cc, farc, NAc = C[cand], far[cand], NA[cand]
        DD = np.linalg.norm(Cc[None, :, :] - Pk[:, None, :], axis=2)
        for kk in range(len(DD)):
            d = DD[kk]
            sel = farc & (d < NORMAL_RADIUS_MM)
            if sel.sum() < 3:
                sel = farc & (d < r2)
            if sel.sum() < 3:
                sel = d < r2
            v = NAc[sel].sum(0)
            n[b0 + kk] = v / max(np.linalg.norm(v), 1e-300)
    missing = np.linalg.norm(n, axis=1) < .5
    if missing.any():
        # A broad cap fan can have no face centroid near a rim station. Its boundary still has an exact adjacent
        # cap triangle: use that surface normal instead of the zero vector (which reports false exposed edges).
        cap_edges = {}
        for face in np.flatnonzero(body.cls == 0):
            tri = body.T[face]
            for a, b in zip(tri, np.roll(tri, -1)):
                cap_edges[tuple(sorted((int(a), int(b))))] = fn[face]
        for k in np.flatnonzero(missing):
            seg_k = seg_of[k]
            n[k] = cap_edges[tuple(sorted((int(loop[seg_k]), int(loop[(seg_k + 1) % nv]))))]
    # tangent: chord between the first loop vertices >= CHORD_MM away on each side
    s2 = np.r_[sv, sv + L]
    kf = np.searchsorted(s2, s + CHORD_MM, side="left") % nv
    kb = (np.searchsorted(s2, s - CHORD_MM + L, side="right") - 1) % nv
    t = FL[kf] - FL[kb]
    t -= (t * n).sum(1, keepdims=True) * n
    t /= np.linalg.norm(t, axis=1, keepdims=True)
    u = np.cross(t, n)
    Pb, ok = back_corners(body.V[body.bl], P, t)
    return Frames(P, Pb, n, t, u, s, node, ok, L, np.asarray(loop, np.int64), node2)


def back_corners(BL, P, t, max_mm: float = BACK_CORNER_MAX_MM, chunk: int = 512) -> tuple[np.ndarray, np.ndarray]:
    """Station plane (through P, normal t) cut with the closed back loop: the crossing nearest to P."""
    A, B = BL, np.roll(BL, -1, 0)
    Pb = np.full_like(P, np.nan)
    ok = np.zeros(len(P), bool)
    for c0 in range(0, len(P), chunk):
        p, tt = P[c0:c0 + chunk], t[c0:c0 + chunk]
        sa = ((A[None] - p[:, None]) * tt[:, None]).sum(-1)
        sb = ((B[None] - p[:, None]) * tt[:, None]).sum(-1)
        cr = (sa > 0) != (sb > 0)
        with np.errstate(divide="ignore", invalid="ignore"):
            w = np.where(cr, sa / np.where(cr, sa - sb, 1.0), 0.0)
        X = A[None] + w[..., None] * (B - A)[None]
        d = np.where(cr, np.linalg.norm(X - p[:, None], axis=-1), np.inf)
        k = d.argmin(1)
        r = np.arange(len(p))
        good = d[r, k] <= max_mm
        Pb[c0:c0 + chunk][good] = X[r, k][good]
        ok[c0:c0 + chunk] = good
    return Pb, ok


def frames_from_arrays(P, Pb, n, t, u, s=None) -> Frames:
    """Frames given from outside (the independent instrument's station frames in P0)."""
    P = np.asarray(P, np.float64)
    N = len(P)
    Pb = np.asarray(Pb, np.float64)
    return Frames(P, Pb, np.asarray(n, np.float64), np.asarray(t, np.float64), np.asarray(u, np.float64),
                  np.zeros(N) if s is None else np.asarray(s, np.float64), np.full(N, -1, np.int64),
                  np.isfinite(Pb).all(1))


# ------------------------------------------------------------------------------------------------- measurement
@dataclass
class Measurement:
    cap: np.ndarray            # (N, 5) capture at the depths (+ buried, - gap), nan = no frame within REACH
    room: np.ndarray           # (N, 5) far side of that frame interval
    owner: np.ndarray          # (N, 5) innermost part index, -1 none
    cover_front: np.ndarray    # (N,) distance along +n from P - 0.10u to frame (<= 0: inside), nan if none in 6 mm
    cover_back: np.ndarray     # (N,) same along -n from Pb - 0.10u
    unmeasured: np.ndarray     # (N,) bool
    recast: np.ndarray         # (N,) most re-casts any of the point's lines needed
    cls: np.ndarray            # (N,) class name
    mid_parts: list            # per point: per frame part intervals along the mid-depth line
    edge: np.ndarray | None = None    # (N, 5) lens edge along each depth line from the rung point (0 = on the rung)
    depth_parts: list | None = None   # per point, per depth: per frame part intervals (keep_depths=True)
    groove: np.ndarray | None = None  # (N,) bool: the point is groove-seated (groove_seat; None = not read): its caps
                                      # are read from the groove's lips, and only such a point is graded at the apex

    @property
    def covered_front(self) -> np.ndarray:
        return np.isfinite(self.cover_front) & (self.cover_front <= COVER_REACH_MM)

    @property
    def covered_back(self) -> np.ndarray:
        return np.isfinite(self.cover_back) & (self.cover_back <= COVER_REACH_MM)


def capture_along(per_part, reach: float = REACH_MM, edge: float = 0.0) -> tuple[float, float, int]:
    """(capture, room, owner) on one line through the lens edge at `edge` (mm along the line). capture = edge - a for
    the merged frame interval [a, b] containing the edge, else for the first interval starting within `reach`
    beyond it (negative = gap); room = b - edge; owner = the part whose own such interval starts innermost."""
    U = merge(per_part)
    c = r = np.nan
    for a, b in U:
        if a <= edge <= b:
            c, r = edge - a, b - edge
            break
        if a > edge:
            if a - edge <= reach:
                c, r = edge - a, b - edge
            break
    owner, best = -1, np.inf
    if np.isfinite(c):
        for p, iv in enumerate(per_part):
            if iv is None:
                continue
            for a, b in iv:
                if (a <= edge <= b or 0.0 < a - edge <= reach) and a < best:
                    owner, best = p, a
    return float(c), float(r), owner


def lens_edge(iv, reach: float = REACH_MM) -> float:
    """Where the line leaves the lens: the end of the lens interval containing the origin (a chamfer or a bullnose
    bulges beyond the corner-to-corner rung), else of the last one ending within `reach` before it (a groove);
    0.0 (the rung point) when the lens part was not given or not found."""
    if iv is None or not len(iv):
        return 0.0
    inside = iv[(iv[:, 0] <= 0.0) & (iv[:, 1] >= 0.0)]
    if len(inside):
        return float(inside[:, 1].max())
    before = iv[(iv[:, 1] < 0.0) & (iv[:, 1] >= -reach)]
    return float(before[:, 1].max()) if len(before) else 0.0


def cover_along(per_part, reach: float = COVER_REACH_MM) -> float:
    U = merge(per_part)
    for a, b in U:
        if b > 0.0:
            return float(a) if a <= reach else np.nan
    return np.nan


def measure(frames: Frames, backend: LineIntervals, lens_part: int | None = None,
            keep_depths: bool = False) -> Measurement:
    """Measure every point. `lens_part`: index of the lens body among the backend's parts (it must be the LAST part,
    so frame part indices are unchanged); its intervals place the edge on each depth line (`lens_edge`) and it is
    never frame material. Without it the edge is the rung point."""
    N = len(frames)
    nd = len(DEPTHS)
    if lens_part is not None and lens_part != backend.n_parts - 1:
        raise ValueError("the lens must be the backend's last part")
    E = frames.depth_points()
    ok = frames.ok.copy()
    Pb = np.where(ok[:, None], frames.Pb, frames.P)
    E = np.where(ok[:, None, None], E, frames.P[:, None, :])
    inset = HIDDEN_MARGIN_MM + COVER_NUDGE_MM
    O = np.concatenate([E.reshape(-1, 3), frames.P - inset * frames.u, Pb - inset * frames.u])
    D = np.concatenate([np.repeat(frames.u, nd, axis=0), frames.n, -frames.n])
    Ax = np.concatenate([np.repeat(frames.t, nd, axis=0), frames.t, frames.t])
    res: LineResult = line_intervals(backend, O, D, Ax)

    def frame_of(per):
        return per if lens_part is None else per[:lens_part]
    cap = np.full((N, nd), np.nan)
    room = np.full((N, nd), np.nan)
    owner = np.full((N, nd), -1, np.int64)
    edge = np.zeros((N, nd))
    cf = np.full(N, np.nan)
    cb = np.full(N, np.nan)
    unm = ~ok
    rec = np.zeros(N, np.int64)
    mid_parts = [None] * N
    depth_parts = [None] * N if keep_depths else None
    for k in range(N):
        lines = [k * nd + j for j in range(nd)] + [N * nd + k, N * nd + N + k]
        rec[k] = max(int(res.recast[i]) for i in lines)
        if any(res.unmeasured(i) for i in lines):
            unm[k] = True
        if not ok[k]:
            continue
        for j in range(nd):
            per = res.intervals[k * nd + j]
            if lens_part is not None:
                edge[k, j] = lens_edge(per[lens_part])
            cap[k, j], room[k, j], owner[k, j] = capture_along(frame_of(per), edge=edge[k, j])
        mid_parts[k] = frame_of(res.intervals[k * nd + MID])
        if keep_depths:
            depth_parts[k] = [frame_of(res.intervals[k * nd + j]) for j in range(nd)]
        cf[k] = cover_along(frame_of(res.intervals[N * nd + k]))
        cb[k] = cover_along(frame_of(res.intervals[N * nd + N + k]))
    m = Measurement(cap, room, owner, cf, cb, unm, rec, np.empty(N, dtype=object), mid_parts, edge, depth_parts)
    m.cls = classify(m)
    return m


def classify(m: Measurement) -> np.ndarray:
    cap = m.cap
    fin = np.isfinite(cap)
    covf, covb = m.covered_front, m.covered_back
    near_cover = ((np.isfinite(m.cover_front) & (m.cover_front <= REACH_MM))
                  | (np.isfinite(m.cover_back) & (m.cover_back <= REACH_MM)))
    within = fin.any(1) | near_cover
    held = np.where(fin, cap, -np.inf) >= -CAPTURED_GAP_MM - CLASS_EPS_MM   # frame within the captured gap
    enclosed = fin.all(1) & (np.where(fin, cap, -1.0) >= -CLASS_EPS_MM).all(1)
    lipped = held[:, MID] & (covf | held[:, 0])
    backed = ~covf & (covb | held[:, -1])
    cls = np.where(m.unmeasured, "UNMEASURED",
                   np.where(~within, "EXPOSED",
                            np.where(enclosed, "ENCLOSED",
                                     np.where(lipped, "LIPPED", np.where(backed, "BACKED", "NEAR")))))
    return cls.astype(object)


# ------------------------------------------------------------------------------------------------ groove seat
GROOVE_GAP_MAX_MM = 0.06           # §5 / §10 P4: the groove gap is 0.02-0.06 mm by design (clearance 0.03 inflated
                                   # along the bevel's normals: 0.03 on the flats, 0.035 along u on the 60 deg flanks)
GROOVE_GAP_MIN_MM = 0.02           # ... and at least 0.02 mm: an edge in frame material (gap 0, a lens buried in an
                                   # ungrooved crystal rim) or closer than this to the groove wall is not groove-seated


@dataclass
class GrooveSeat:
    seated: np.ndarray         # (N,) the edge sits in a groove: at every depth the frame begins GROOVE_GAP_MIN_MM to
                               # GROOVE_GAP_MAX_MM beyond it (never in frame material), and frame holds the lens over
                               # both faces (the lips)
    aperture: np.ndarray       # (N,) the aperture at mid-depth along u from the front corner, nan when not seated
    gap_mm: np.ndarray         # (N, 5) clearance to the groove wall per depth (0 inside frame), nan where not seated


def groove_seat(frames: Frames, backend: LineIntervals, lens_part: int, meas: Measurement,
                gap_max: float = GROOVE_GAP_MAX_MM,
                gap_min: float = GROOVE_GAP_MIN_MM) -> tuple[Measurement, GrooveSeat]:
    """§3 capture for a V-beveled edge seated in a groove (§5): the groove is cut with a clearance, so on every depth
    line the frame begins 0.02-0.06 mm beyond the lens edge and §3's 'interval containing the edge' reads that
    clearance as a gap at every depth (the stage-proof crystal fixture: 352/352 points FAIL with exact bevel and groove
    numbers). In a groove the lens is held between the lips; its capture is how far each depth's edge reaches beyond
    the lips' inner face (the visible aperture, PLAN §3 'the lens extends 0.8 mm beyond the visible aperture'). The
    lips are read on lines along u just outside the lens faces (front corner + gap_max n, back corner - gap_max n:
    beyond the clearance, inside the lip): the frame interval holding the corner's projection begins at the lip's
    inner face, and between the two lips the aperture face is taken linear in depth (a planar face: exactly where §3
    reads it on each depth line before the groove is cut). A point is groove-seated when every depth line has a real
    clearance, the frame beginning gap_min to gap_max beyond its edge (§5's gap band; an edge in frame material is
    not seated: review fix round 1, a lens buried in an ungrooved crystal ring, or cutting 0.05 mm into the walls of
    a groove too small for it, read as seated and passed 344/344), and both lip lines hold frame over the corner; its
    caps become edge - aperture per depth and `groove` marks it (the 'apex' rule grades only such points at the apex),
    every other point keeps §3's reading. Overlap of the lens with the lips is not seen on the depth lines: the check
    measures it (check.crystal_overlap). Returns (measurement with those caps and re-derived classes, GrooveSeat)."""
    N = len(frames)
    nd = len(DEPTHS)
    seated = np.zeros(N, bool)
    aperture = np.full(N, np.nan)
    gap = np.full((N, nd), np.nan)
    cap = meas.cap.copy()
    if N == 0:
        return dataclasses.replace(meas, groove=seated.copy()), GrooveSeat(seated, aperture, gap)
    with np.errstate(invalid="ignore"):
        near = (np.isfinite(cap).all(1) & (cap >= -gap_max - CLASS_EPS_MM).all(1)
                & (cap <= -gap_min + CLASS_EPS_MM).all(1) & frames.ok)
    idx = np.flatnonzero(near)
    if len(idx):
        off = gap_max + COVER_NUDGE_MM
        O = np.concatenate([frames.P[idx] + off * frames.n[idx], frames.Pb[idx] - off * frames.n[idx]])
        D = np.concatenate([frames.u[idx], frames.u[idx]])
        Ax = np.concatenate([frames.t[idx], frames.t[idx]])
        res = line_intervals(backend, O, D, Ax)
        m = len(idx)
        for q, k in enumerate(idx.tolist()):
            lips = []
            for line in (q, m + q):
                U = merge(res.intervals[line][:lens_part])
                hold = [(a, b) for a, b in U if a <= 0.0 <= b]
                lips.append(hold[0][0] if hold else np.nan)
            if not np.isfinite(lips).all():
                continue
            a_front = lips[0]
            a_back = lips[1] + float((frames.Pb[k] - frames.P[k]) @ frames.u[k])
            f = np.asarray(DEPTHS)
            ap = (1.0 - f) * a_front + f * a_back             # the aperture face at each depth's height
            E = frames.depth_points()[k]
            edge_abs = (E - frames.P[k]) @ frames.u[k] + meas.edge[k]
            seated[k] = True
            aperture[k] = ap[MID]
            gap[k] = np.clip(-meas.cap[k], 0.0, None)
            cap[k] = edge_abs - ap
    m2 = Measurement(cap, meas.room, meas.owner, meas.cover_front, meas.cover_back, meas.unmeasured, meas.recast,
                     np.empty(N, dtype=object), meas.mid_parts, meas.edge, meas.depth_parts, seated.copy())
    m2.cls = classify(m2)
    return m2, GrooveSeat(seated, aperture, gap)


UNDER_WINDOW_MM = (-1.5, 4.0)               # §6 step 1: 'under' material along +u starting within [-1.5, 4] mm


def layered(meas: Measurement, window=UNDER_WINDOW_MM) -> np.ndarray:
    """Per point: at least two distinct frame parts along the mid-depth line within `window` of the edge (an eyewire
    with a brow over it). With the asset x inside a hidden range this is §6's 'selected station' (the loop bottom
    at the same x has only the eyewire)."""
    lo, hi = window
    out = np.zeros(len(meas.cls), bool)
    for k, per in enumerate(meas.mid_parts):
        if per is None:
            continue
        e = float(meas.edge[k, MID]) if meas.edge is not None else 0.0
        hit = sum(1 for iv in per if iv is not None and len(iv)
                  and ((iv[:, 1] >= e + lo) & (iv[:, 0] <= e + hi)).any())
        out[k] = hit >= 2
    return out


def measure_body(body: LensBody, backend: LineIntervals, lens_part: int | None = None,
                 spacing: float = POINT_SPACING_MM):
    fr = station_frames(body, spacing)
    return fr, measure(fr, backend, lens_part)


# ------------------------------------------------------------------------------------------------ buried band
BAND_COLUMN_MM = 0.025
# u offsets from the mid-depth edge point, -2 to +4 mm (the instrument's u range: an eyewire lip can reach more
# than 1 mm inside the lens edge, the P4 C section to -1.05), at half-column positions so no column sits on a
# round dimension
BAND_COLUMNS_MM = np.round(-2.0 + BAND_COLUMN_MM * (np.arange(240) + 0.5), 6)
BAND_WINDOW_MM = 6.0                                               # material farther along n is ignored


HELD_SHARE = 0.60                          # §6 step 1: the eyewire is the innermost owner at >= 60% of the stations


@dataclass
class Band:
    candidate: np.ndarray      # (N,) a held part lies in the window and another frame part reaches farther out
    buried: np.ndarray         # (N,) index of that held (buried) part, -1
    outer: np.ndarray          # (N,) index of the outward part, -1
    front_mm: np.ndarray       # (N,) width along u where the buried part is the frontmost frame material
    back_mm: np.ndarray        # (N,) ... the backmost
    area_outside_mm2: np.ndarray   # (N,) section area of the buried part outside every other frame part
    held: tuple = ()           # part indices that hold the lens (innermost mid-depth owner at >= HELD_SHARE)


def held_parts(meas: Measurement, share: float = HELD_SHARE, mask=None) -> tuple:
    """Parts that are the innermost mid-depth owner at >= `share` of the stations that have an owner (§6 step 1's
    held-lens rule read from the lens side): the eyewire of a browline, never the brow at a corner it happens to
    reach first. `mask` restricts the count to those stations (the check passes the stations OUTSIDE the hidden
    ranges: hide_eyewire lifts the eyewire under the brow there, so the brow becomes innermost at every in-range
    station of a seated browline; on q001 that alone would drop the eyewire's share from 0.94 to 0.61); when no
    masked station has an owner, all stations count."""
    own = np.asarray(meas.owner[:, MID])
    has = own >= 0
    if mask is not None and (has & np.asarray(mask, bool)).any():
        has &= np.asarray(mask, bool)
    if not has.any():
        return ()
    parts, counts = np.unique(own[has], return_counts=True)
    return tuple(int(p) for p, c in zip(parts, counts) if c >= share * has.sum())


OUTWARD_EPS_MM = 1e-4      # the outer part must reach this much farther than the held part (coincident far faces
                           # are not 'outward'; float32 backends differ by ~1e-5)


def _in_window(iv, e: float, window=UNDER_WINDOW_MM) -> list:
    """Intervals of one part on the mid-depth line that overlap [e + window[0], e + window[1]] (§6 step 1)."""
    if iv is None or not len(iv):
        return []
    lo, hi = e + window[0], e + window[1]
    return [(float(a), float(b)) for a, b in iv if b >= lo and a <= hi]


def band_candidate(per, e: float, held) -> tuple[int, int]:
    """(buried, outer) part indices at one station, or (-1, -1). Membership, not innermost: a HELD part with
    material on the mid-depth line within the §6 window of the edge, and another frame part in the same window
    whose interval reaches farther outward along u (b_other > b_held), whichever of the two starts first. The held
    part whose window interval starts first wins; the outer part is the qualifying one that starts first."""
    if per is None:
        return -1, -1
    for h in sorted(held, key=lambda h: min(_in_window(per[h], e), default=(np.inf, np.inf))):
        mine = _in_window(per[h], e)
        if not mine:
            continue
        b_h = min(mine)[1]
        best = None
        for p, iv in enumerate(per):
            if p == h:
                continue
            for a, b in _in_window(iv, e):
                if b > b_h + OUTWARD_EPS_MM and (best is None or (a, p) < best):
                    best = (a, p)
        if best is not None:
            return int(h), int(best[1])
    return -1, -1


def buried_band(frames: Frames, meas: Measurement, backend: LineIntervals, lens_part: int | None = None,
                held_from=None, only=None) -> Band:
    """§8 'Buried-part band, always: for every eyewire with another frame part outward along u'. The eyewire is a
    part holding the lens (`held_parts`, counted over `held_from` when given); a station is a candidate when that
    part has material on the mid-depth line within the §6 window and another frame part reaches farther outward
    (`band_candidate`), whether the held part or the other one starts first: after hide_eyewire lifts the eyewire
    the brow is innermost and the eyewire's back can still stick out of it. Measured: the width of the held part
    visible along +-n and its section area outside the others. FT's brow under the saddle bridge is no candidate
    (the brow is not held). `only`: measure the widths at these candidates only (the others stay nan; hide_eyewire's
    post-check grades the in-range candidates, the check's graded set, without paying for the advisory rest)."""
    N = len(frames)
    cand = np.zeros(N, bool)
    buried = np.full(N, -1, np.int64)
    outer = np.full(N, -1, np.int64)
    held = held_parts(meas, mask=held_from)
    for k in range(N):
        per = meas.mid_parts[k]
        if per is None or not held:
            continue
        e = float(meas.edge[k, MID]) if meas.edge is not None else 0.0
        h, o = band_candidate(per, e, held)
        if h >= 0:
            cand[k], buried[k], outer[k] = True, h, o
    fm = np.full(N, np.nan)
    bm = np.full(N, np.nan)
    ar = np.full(N, np.nan)
    idx = np.flatnonzero(cand if only is None else cand & np.asarray(only, bool))
    if len(idx):
        fm[idx], bm[idx], ar[idx] = band_widths(frames, idx, buried[idx], backend, lens_part)
    return Band(cand, buried, outer, fm, bm, ar, held)


def band_widths(frames: Frames, idx, buried, backend: LineIntervals, lens_part: int | None = None):
    """(front_mm, back_mm, area_outside_mm2) of part `buried[q]` at stations idx[q]: columns along n every
    BAND_COLUMN_MM over BAND_COLUMNS_MM (u offsets from the mid-depth edge point); a column counts toward `front`
    when that part is the frontmost frame material on it, `back` when the backmost; the area is its section
    outside every other frame part (the lens is never frame material)."""
    idx = np.asarray(idx, np.int64)
    buried = np.broadcast_to(np.asarray(buried, np.int64), idx.shape)
    fm = np.zeros(len(idx))
    bm = np.zeros(len(idx))
    ar = np.zeros(len(idx))
    if not len(idx):
        return fm, bm, ar
    cols = BAND_COLUMNS_MM
    du = BAND_COLUMN_MM
    Em = frames.depth_points()[idx, MID]
    O = (Em[:, None, :] + cols[None, :, None] * frames.u[idx, None, :]).reshape(-1, 3)
    D = np.repeat(frames.n[idx], len(cols), axis=0)
    Ax = np.repeat(frames.t[idx], len(cols), axis=0)
    res = line_intervals(backend, O, D, Ax)

    def win(iv):
        return np.clip(iv, -BAND_WINDOW_MM, BAND_WINDOW_MM) if iv is not None and len(iv) else np.zeros((0, 2))
    for q in range(len(idx)):
        o = int(buried[q])
        vf = vb = 0
        area = 0.0
        for c in range(len(cols)):
            per = res.intervals[q * len(cols) + c]
            if lens_part is not None:
                per = per[:lens_part]
            if per[o] is None:
                continue
            mine = win(per[o])
            mine = mine[mine[:, 1] > mine[:, 0]]
            if not len(mine):
                continue
            others = merge([win(iv) for p, iv in enumerate(per) if p != o])
            others = others[others[:, 1] > others[:, 0]] if len(others) else others
            if not len(others) or mine[:, 1].max() > others[:, 1].max() + 1e-9:
                vf += 1
            if not len(others) or mine[:, 0].min() < others[:, 0].min() - 1e-9:
                vb += 1
            area += _outside_length(mine, others) * du
        fm[q], bm[q], ar[q] = vf * du, vb * du, area
    return fm, bm, ar


def _outside_length(mine: np.ndarray, others: np.ndarray) -> float:
    """Total length of `mine` not covered by the (merged) `others`."""
    total = 0.0
    for a, b in mine:
        cov = 0.0
        for c, d in others:
            lo, hi = max(a, c), min(b, d)
            if hi > lo:
                cov += hi - lo
        total += (b - a) - cov
    return float(total)


# --------------------------------------------------------------------------------------------- lens-part overlap
# §5 'the check reports cord-lens overlap' (limit 0.01 mm3, §10 P4). Measured in the station planes: columns along n
# through the mid-depth edge point + a u, every OVERLAP_STEP_MM of a over OVERLAP_WINDOW_MM; each column's overlap is
# the length where the lens's and the part's inside intervals coincide (exact along n), the section is their sum
# times the step, and the volume sums the sections times the station's arc share ds with the outline's Pappus factor
# (1 + kappa a). Columns are cast only where a coarse probe every OVERLAP_PROBE_MM finds the part (+- one probe).
OVERLAP_STEP_MM = 0.01            # a 0.01 mm3 limit along a 75 mm arc is a 1.3e-4 mm2 mean section: sub-0.01 mm columns
OVERLAP_PROBE_MM = 0.10           # a cord or a frame lip is thicker than this (the thinnest frame solid is 0.12 mm)
OVERLAP_WINDOW_MM = (-REACH_MM, 1.0)   # u offsets from the mid-depth edge point: REACH inward, 1.0 outward (a V-bevel
                                       # apex sits <= 0.8 beyond its corners, §5)


def outline_curvature(frames: Frames) -> np.ndarray:
    """Signed curvature of the outline at each point (> 0 convex): -(dt/ds) . u from the neighbouring points."""
    t, s, L = frames.t, frames.s, frames.loop_mm
    dt = np.roll(t, -1, 0) - np.roll(t, 1, 0)
    ds = (np.roll(s, -1) - np.roll(s, 1)) % L if L > 0 else np.roll(s, -1) - np.roll(s, 1)
    return -(dt * frames.u).sum(1) / np.maximum(ds, 1e-9)


def arc_share(frames: Frames) -> np.ndarray:
    """Each point's share of the loop length: half the arc to each neighbour."""
    s, L = frames.s, frames.loop_mm
    if len(s) < 2 or L <= 0:
        return np.zeros(len(s))
    return 0.5 * (((np.roll(s, -1) - s) % L) + ((s - np.roll(s, 1)) % L))


def _overlap_length(a, b) -> float:
    """Total length where the interval lists a and b coincide."""
    if a is None or b is None or not len(a) or not len(b):
        return 0.0
    lo = np.maximum(a[:, None, 0], b[None, :, 0])
    hi = np.minimum(a[:, None, 1], b[None, :, 1])
    return float(np.clip(hi - lo, 0.0, None).sum())


def parts_in_groove(frames: Frames, backend: LineIntervals, lens_part: int, depth_mm: float,
                    only=None) -> np.ndarray:
    """(n_frame_parts,) number of points (of `only`, default all) whose groove centre (the mid-depth edge point moved
    depth_mm / 2 inward along u, §5) lies inside each frame part."""
    nf = lens_part
    out = np.zeros(nf, np.int64)
    idx = np.flatnonzero(frames.ok if only is None else frames.ok & np.asarray(only, bool))
    if not len(idx) or nf == 0:
        return out
    G = frames.depth_points()[idx, MID] - (depth_mm / 2.0) * frames.u[idx]
    res = line_intervals(backend, G, frames.n[idx], frames.t[idx])
    for q in range(len(idx)):
        for p, iv in enumerate(res.intervals[q][:nf]):
            if iv is not None and len(iv) and ((iv[:, 0] <= 0.0) & (iv[:, 1] >= 0.0)).any():
                out[p] += 1
    return out


def part_overlap(frames: Frames, backend: LineIntervals, lens_part: int, parts, window=OVERLAP_WINDOW_MM,
                 step: float = OVERLAP_STEP_MM, probe: float = OVERLAP_PROBE_MM, only=None) -> dict:
    """vol(lens ^ part) in mm3 for each part index in `parts` (block comment above), summed over the station planes
    of the points in `only` (default all). Returns {"parts": {p: {"mm3", "points", "max_section_mm2",
    "worst_xyz"}}, "columns", "unmeasured_columns"}: a column whose lens or part parity stayed odd is not counted
    (reported)."""
    parts = [int(p) for p in parts]
    out = {"parts": {p: {"mm3": 0.0, "points": 0, "max_section_mm2": 0.0, "worst_xyz": None} for p in parts},
           "columns": 0, "unmeasured_columns": 0}
    idx = np.flatnonzero(frames.ok if only is None else frames.ok & np.asarray(only, bool))
    if not len(idx) or not parts:
        return out
    w0, w1 = float(window[0]), float(window[1])
    E = frames.depth_points()[idx, MID]
    U, Nn, Tt = frames.u[idx], frames.n[idx], frames.t[idx]
    ap = np.arange(w0, w1 + 1e-9, probe)
    O = (E[:, None, :] + ap[None, :, None] * U[:, None, :]).reshape(-1, 3)
    res = line_intervals(backend, O, np.repeat(Nn, len(ap), 0), np.repeat(Tt, len(ap), 0))
    nf = int(round((w1 - w0) / step))
    lo = np.full(len(idx), nf, np.int64)
    hi = np.full(len(idx), -1, np.int64)
    for q in range(len(idx)):
        for c in range(len(ap)):
            per = res.intervals[q * len(ap) + c]
            if any(per[p] is None or len(per[p]) for p in parts):
                a0 = max(w0, ap[c] - probe)
                a1 = min(w1, ap[c] + probe)
                lo[q] = min(lo[q], int(np.floor((a0 - w0) / step)))
                hi[q] = max(hi[q], int(np.ceil((a1 - w0) / step)) - 1)
    hi = np.minimum(hi, nf - 1)
    sel = np.flatnonzero(hi >= lo)
    if not len(sel):
        return out
    cols = [np.arange(lo[q], hi[q] + 1) for q in sel]
    owner = np.repeat(sel, [len(c) for c in cols])
    a = w0 + step * (np.concatenate(cols) + 0.5)                         # column centres (midpoint rule)
    O = E[owner] + a[:, None] * U[owner]
    res = line_intervals(backend, O, Nn[owner], Tt[owner])
    kap = outline_curvature(frames)[idx]
    ds = arc_share(frames)[idx]
    out["columns"] = int(len(a))
    sec = {p: np.zeros(len(idx)) for p in parts}
    for i in range(len(a)):
        per = res.intervals[i]
        lens_iv = per[lens_part]
        q = int(owner[i])
        for p in parts:
            if lens_iv is None or per[p] is None:
                out["unmeasured_columns"] += 1
                continue
            ln = _overlap_length(lens_iv, per[p])
            if ln > 0.0:
                w = max(0.0, 1.0 + kap[q] * a[i])
                sec[p][q] += ln * step
                out["parts"][p]["mm3"] += ln * step * ds[q] * w
    for p in parts:
        s = sec[p]
        rec = out["parts"][p]
        rec["points"] = int((s > 0).sum())
        if rec["points"]:
            k = int(np.argmax(s))
            rec["max_section_mm2"] = float(s[k])
            rec["worst_xyz"] = [float(x) for x in E[k]]
    return out
