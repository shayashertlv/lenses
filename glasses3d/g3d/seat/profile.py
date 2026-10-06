"""Edge profiles (WP4_DESIGN.md §5): re-wall plans for the crystal V-bevel and the nylor groove, the groove cutter's
inflation and the nylor cord tube. numpy only; seat_bpy applies the plans with bmesh and the Boolean modifier.

Re-wall builder: the outer wall faces are deleted and, for every front-loop vertex, a profile is built on the
segment from its front corner to its rung partner's back corner and bridged ring by ring:
- V-bevel (translucent capturing part, PLAN §3 'crystal'): b = t_edge - 2 * 0.10, h = (b / 2) / tan 60 deg (120 deg
  included angle, h <= 0.8): front corner -> shoulder (0.10 along the wall) -> apex at mid-depth + h u -> shoulder ->
  back corner. The corners are grown to target - h first, so the apex sits at the target;
- nylor groove (lower_edge='nylor', PLAN §3): a 0.60 x 0.60 mm rectangular notch centred at mid-depth, all round
  (the top is hidden in the channel): corner -> notch top -> 0.60 inward -> notch bottom -> back corner.
The wall must be one ring of quads with a rung partner for every front-loop vertex (every real lens and fixture is);
anything else is refused (E_PROFILE_WALL) rather than guessed.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass

import numpy as np

from . import geom, rules, stations

FLAT_MM = 0.10                 # §7 bevel flats
BEVEL_ANGLE_DEG = 120.0        # PLAN §3: ~120 deg V-bevel
BEVEL_H_MAX_MM = 0.8           # §5: h <= 0.8
GROOVE_WIDTH_MM = 0.60         # PLAN §3: ~0.6 x 0.6 mm groove
GROOVE_DEPTH_MM = 0.60
CLEARANCE_MM = 0.03            # §7 groove clearance (cutter inflation)
CORD_DIAMETER_MM = 0.60        # §5 cord
CORD_SIDES = 12
CORD_END_MM = 1.0              # §5: cord ends 1.0 mm into the channel
GAP_RANGE_MM = (0.02, 0.06)    # §5 groove gap
FRAME_LEFT_MM = 0.5            # §5: >= 0.5 mm of frame left on station lines
OVERLAP_MAX_MM3 = 0.01         # §5 / §8
REMOVED_TOLERANCE = 0.01       # §5: removed volume = vol(cutter ^ frame) +- 1%


def bevel_height(t_edge: float) -> float:
    b = t_edge - 2.0 * FLAT_MM
    return float(min(BEVEL_H_MAX_MM, (b / 2.0) / np.tan(np.radians(BEVEL_ANGLE_DEG / 2.0))))


def growth_type_for(ft: rules.FrameType, kind: str, bodies) -> tuple[rules.FrameType, dict]:
    """The frame type the corners are grown with before the profile (§4 step 8): V-bevel corners to target - h (the
    apex adds h); the groove keeps the type (its rule reads the flats, outside the notch)."""
    edges = []
    for b in bodies:
        fr = stations.station_frames(b)
        edges += fr.edge_mm[fr.ok].tolist()
    t_edge = float(np.median(edges)) if edges else 0.0
    info = {"kind": kind, "edge_mm_p50": round(t_edge, 4)}
    if kind == "vbevel":
        h = bevel_height(t_edge)
        info.update(h_mm=round(h, 4), angle_deg=BEVEL_ANGLE_DEG, flats_mm=FLAT_MM)
        if ft.pass_mm is None:
            return ft, info
        ft2 = dataclasses.replace(ft, pass_mm=ft.pass_mm - h, target_mm=ft.target_mm - h)
        info["corner_pass_target_mm"] = [round(ft2.pass_mm, 4), round(ft2.target_mm, 4)]
        return ft2, info
    if kind == "groove":
        info.update(width_mm=GROOVE_WIDTH_MM, depth_mm=GROOVE_DEPTH_MM)
        return ft, info
    raise ValueError(kind)


@dataclass
class RewallPlan:
    kind: str
    new_V: np.ndarray              # (k, 3) new vertices (asset mm); ids n_raw + j
    faces: list                    # new faces (tuples of raw / new ids), outward winding
    loops: list                    # closed vertex-id loops to mark sharp (front, profile rings, back)
    front: np.ndarray              # oriented front-loop raw ids
    back: np.ndarray               # back-loop raw ids (partners of `front`)
    info: dict


def rewall_plan(V_raw: np.ndarray, front: np.ndarray, back: np.ndarray, u: np.ndarray, kind: str,
                cap_edge_dir: tuple | None = None) -> RewallPlan:
    """Profile vertices and faces for the wall between `front` (oriented loop, raw ids) and `back` (each front
    vertex's rung partner, raw ids), with outward directions u per front vertex. `cap_edge_dir`: (a, b) raw ids of
    one front-cap edge on the loop in the direction a cap face traverses it, to orient the new faces (the wall face
    on that edge must traverse it b -> a)."""
    V = np.asarray(V_raw, np.float64)
    front = np.asarray(front, np.int64)
    back = np.asarray(back, np.int64)
    n_raw = len(V)
    pf, pb = V[front], V[back]
    rung = pb - pf
    t_edge = np.linalg.norm(rung, axis=1)
    d = rung / np.maximum(t_edge, 1e-30)[:, None]
    mid = 0.5 * (pf + pb)
    if kind == "vbevel":
        h = np.array([bevel_height(t) for t in t_edge])
        prof = [pf + FLAT_MM * d, mid + h[:, None] * u, pb - FLAT_MM * d]
        info = {"h_mm": [round(float(h.min()), 4), round(float(h.max()), 4)],
                "edge_mm": [round(float(t_edge.min()), 4), round(float(t_edge.max()), 4)]}
    elif kind == "groove":
        w2 = GROOVE_WIDTH_MM / 2.0
        a = mid - w2 * d
        b = mid + w2 * d
        prof = [a, a - GROOVE_DEPTH_MM * u, b - GROOVE_DEPTH_MM * u, b]
        info = {"edge_mm": [round(float(t_edge.min()), 4), round(float(t_edge.max()), 4)]}
    else:
        raise ValueError(kind)
    nF = len(front)
    k = len(prof)
    new_V = np.concatenate(prof)                       # ring r occupies rows r*nF .. (r+1)*nF-1
    rings = [front] + [n_raw + r * nF + np.arange(nF) for r in range(k)] + [back]
    faces = []
    for r in range(len(rings) - 1):
        A, B = rings[r], rings[r + 1]
        for i in range(nF):
            j = (i + 1) % nF
            faces.append((int(A[i]), int(B[i]), int(B[j]), int(A[j])))
    if cap_edge_dir is not None:
        a_, b_ = cap_edge_dir
        f0 = None
        for f in faces[:nF]:
            if a_ in f and b_ in f:
                f0 = f
                break
        if f0 is not None:
            ia, ib = f0.index(a_), f0.index(b_)
            if (ib - ia) % 4 == 1:                     # the wall face traverses a -> b like the cap: flip all
                faces = [tuple(reversed(f)) for f in faces]
    info["front_vertices"] = int(nF)
    info["new_vertices"] = int(len(new_V))
    return RewallPlan(kind, new_V, faces, rings, front, back, info)


def inflate(V: np.ndarray, T: np.ndarray, d: float) -> np.ndarray:
    """V moved d along angle-weighted vertex normals (over the welded mesh, so duplicates move together)."""
    V = np.asarray(V, np.float64)
    T = np.asarray(T, np.int64).reshape(-1, 3)
    Vw, vid = geom.weld_exact(V)
    Tw = vid[T]
    fn, _, _ = geom.face_geometry(Vw, Tw)
    vn = np.zeros_like(Vw)
    for i in range(3):
        p0, p1, p2 = Vw[Tw[:, i]], Vw[Tw[:, (i + 1) % 3]], Vw[Tw[:, (i + 2) % 3]]
        e1, e2 = p1 - p0, p2 - p0
        cosang = (e1 * e2).sum(1) / np.maximum(np.linalg.norm(e1, axis=1) * np.linalg.norm(e2, axis=1), 1e-30)
        np.add.at(vn, Tw[:, i], fn * np.arccos(np.clip(cosang, -1, 1))[:, None])
    vn /= np.maximum(np.linalg.norm(vn, axis=1, keepdims=True), 1e-30)
    return V + d * vn[vid]


def tube(C: np.ndarray, U: np.ndarray, N: np.ndarray, radius: float = CORD_DIAMETER_MM / 2,
         sides: int = CORD_SIDES) -> tuple[np.ndarray, np.ndarray]:
    """A closed tube of `sides` around the open centreline C (sections in the (U, N) planes), capped at both ends,
    outward winding."""
    C = np.asarray(C, np.float64)
    m = len(C)
    th = 2 * np.pi * (np.arange(sides) + 0.5) / sides
    V = (C[:, None, :] + radius * (np.cos(th)[None, :, None] * U[:, None, :]
                                   + np.sin(th)[None, :, None] * N[:, None, :])).reshape(-1, 3)
    T = []
    for i in range(m - 1):
        for a in range(sides):
            b = (a + 1) % sides
            p0, p1, q0, q1 = i * sides + a, i * sides + b, (i + 1) * sides + a, (i + 1) * sides + b
            T += [(p0, q0, q1), (p0, q1, p1)]
    c0, c1 = len(V), len(V) + 1
    V = np.vstack([V, C[0], C[-1]])
    for a in range(sides):
        b = (a + 1) % sides
        T.append((c0, b, a))
        T.append((c1, (m - 1) * sides + a, (m - 1) * sides + b))
    T = np.array(T, np.int64)
    if geom.signed_volume(V, T) < 0:
        T = T[:, ::-1].copy()
    return V, T


def exposed_arcs(frames: stations.Frames, visible: np.ndarray) -> list[np.ndarray]:
    """Point-index runs of the visible-by-design (exposed) sector, in loop order."""
    from .plan import runs
    return [r for r in runs(np.asarray(visible, bool)) if len(r) < len(visible)]


def cord_centreline(frames: stations.Frames, run: np.ndarray, extend: float = CORD_END_MM,
                    spacing: float = stations.POINT_SPACING_MM) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(centres, U, N) of the groove centreline over a run of exposed points, extended `extend` mm along the loop
    into the channel at each end: centre = mid-depth edge point - (depth / 2) u."""
    from .plan import interp_loop
    E = frames.depth_points()[:, stations.MID] - (GROOVE_DEPTH_MM / 2.0) * frames.u
    s = frames.s
    L = frames.loop_mm
    s0, s1 = float(s[run[0]]) - extend, float(s[run[-1]]) + extend
    length = (s1 - s0) % L if s1 < s0 else s1 - s0
    nseg = max(2, int(np.ceil(length / spacing)))
    sq = s0 + length * np.arange(nseg + 1) / nseg
    order = np.argsort(s)
    ss, EE, UU, NN = s[order], E[order], frames.u[order], frames.n[order]
    C = interp_loop(EE, ss, L, sq)
    k = np.searchsorted(ss, np.mod(sq, L), side="right") - 1
    k = np.clip(k, 0, len(ss) - 1)
    U = UU[k]
    N = NN[k]
    # make U, N orthonormal and perpendicular to the centreline tangent
    tg = np.gradient(C, axis=0)
    tg /= np.maximum(np.linalg.norm(tg, axis=1, keepdims=True), 1e-30)
    N = N - (N * tg).sum(1, keepdims=True) * tg
    N /= np.maximum(np.linalg.norm(N, axis=1, keepdims=True), 1e-30)
    U = np.cross(tg, N)
    return C, U, N
