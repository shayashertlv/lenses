"""Deterministic synthetic lens-seating fixtures (WP4_DESIGN.md §10 P4). numpy only, so the same builder runs on the
host and inside Blender (tests/blender/seat_build.py imports it). Units: millimetres, asset frame +Y up, +Z front,
x = 0 at the bridge. Nothing is random: every number is a parameter.

Lens solids (Studio construction unless a variant says otherwise): an elliptical (or given) outline, a spherical,
toric or flat front cap, the back cap = the front cap translated -t along Z, concentric rings plus a centre vertex,
one wall ring of quads. Closed, outward winding. Variants: solidified back (offset along the cap normal), 0.3 mm
chamfers, a bullnose (rounded) rim, flipped winding, coarse 32 segments, a drilled hole, two fused lenses, an open lens.

Frames are swept along the lens outline in vertical sections (r = the horizontal outward normal of the outline,
w = +Z, both measured from the lens edge at mid-thickness), so a capture set in a section is the capture the core
measures along u divided by the horizontal component of u (the lens surface tilt): `expected_capture()`.
- ring_frame: a flat extruded acetate ring (vertical aperture wall at r = -inset), front/back faces level;
- swept C channel: a web whose inner face (the channel bottom) sits at r = -capture, lips 0.05 mm thick over both
  lens faces reaching `lip` mm inward: metal eyewires (closed loop) and semi-rimless channels (capped arc);
- cord: a capped round tube (nylor).
`write_glb` writes any set of these as a GLB (metres, partRole extras, optional g3d_seat extras).
"""
from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field

import numpy as np


@dataclass
class Mesh:
    V: np.ndarray                         # (n, 3) float64 mm
    T: np.ndarray                         # (m, 3) int64, outward winding
    poly_edges: np.ndarray | None = None  # (e, 2) polygon (not triangulation) edges, when the builder knows them
    meta: dict = field(default_factory=dict)

    def flipped(self) -> "Mesh":
        return Mesh(self.V.copy(), self.T[:, ::-1].copy(), self.poly_edges, dict(self.meta, flipped=True))


# --------------------------------------------------------------------------------------------------- outlines
def ellipse(a: float = 25.0, b: float = 19.0, n: int = 128, cx: float = -32.0, cy: float = 0.0) -> np.ndarray:
    th = 2 * np.pi * np.arange(n) / n
    return np.c_[cx + a * np.cos(th), cy + b * np.sin(th)]


def outline_normals(P2: np.ndarray) -> np.ndarray:
    """Outward unit normals of a CCW closed polyline (central differences)."""
    t = np.roll(P2, -1, 0) - np.roll(P2, 1, 0)
    t /= np.linalg.norm(t, axis=1, keepdims=True)
    return np.c_[t[:, 1], -t[:, 0]]


def resample(P2: np.ndarray, k: int) -> np.ndarray:
    """k points per segment of a closed polyline (k = 1: unchanged)."""
    Q = np.roll(P2, -1, 0)
    w = np.arange(k) / k
    return (P2[:, None, :] + w[None, :, None] * (Q - P2)[:, None, :]).reshape(-1, 2)


# ------------------------------------------------------------------------------------------------------ lens
def cap_z(x, y, kind: str = "sphere", R: float = 120.0, Rx: float = 147.9, Ry: float = 94.25, apex=(-32.0, 0.0)):
    """Front-cap height, apex z = 0 at `apex`, concave toward -Z."""
    dx, dy = np.asarray(x) - apex[0], np.asarray(y) - apex[1]
    if kind == "sphere":
        return np.sqrt(R * R - dx * dx - dy * dy) - R
    if kind == "toric":
        return -(Rx - np.sqrt(Rx * Rx - dx * dx)) - (Ry - np.sqrt(Ry * Ry - dy * dy))
    if kind == "flat":
        return np.zeros_like(dx, dtype=float)
    raise ValueError(kind)


def cap_normal(x, y, kind="sphere", R=120.0, Rx=147.9, Ry=94.25, apex=(-32.0, 0.0)):
    h = 1e-4
    zx = (cap_z(x + h, y, kind, R, Rx, Ry, apex) - cap_z(x - h, y, kind, R, Rx, Ry, apex)) / (2 * h)
    zy = (cap_z(x, y + h, kind, R, Rx, Ry, apex) - cap_z(x, y - h, kind, R, Rx, Ry, apex)) / (2 * h)
    n = np.stack([-zx, -zy, np.ones_like(zx)], -1)
    return n / np.linalg.norm(n, axis=-1, keepdims=True)


def lens(outline: np.ndarray | None = None, t: float = 1.6, cap: str = "sphere", R: float = 120.0,
         Rx: float = 147.9, Ry: float = 94.25, nring: int = 10, back: str = "translate", rim: str = "flat",
         chamfer: float = 0.3, nround: int = 8, apex=None, hole: float | None = None) -> Mesh:
    """`hole`: instead of the centre fan, the innermost ring (at this scale of the outline) is a through hole with
    its own wall (a drilled lens, chi = 0)."""
    P2 = ellipse() if outline is None else np.asarray(outline, float)
    n = len(P2)
    c = P2.mean(0) if apex is None else np.asarray(apex, float)
    scales = np.linspace(1.0, hole if hole else 1.0 / nring, nring)
    rings = [c + (P2 - c) * s for s in scales]                      # ring 0 = the rim
    Q = np.vstack(rings + ([] if hole else [c[None]]))
    zf = cap_z(Q[:, 0], Q[:, 1], cap, R, Rx, Ry, c)
    Vf = np.c_[Q, zf]
    nv = len(Vf)
    if back == "translate":
        Vb = Vf - np.array([0.0, 0.0, t])
    elif back == "solidify":
        Vb = Vf - t * cap_normal(Q[:, 0], Q[:, 1], cap, R, Rx, Ry, c)
    else:
        raise ValueError(back)
    V = np.vstack([Vf, Vb])

    def rid(r, i):
        return r * n + (i % n)
    F, PE = [], []
    for r in range(nring - 1):
        for i in range(n):
            a0, a1, b0, b1 = rid(r, i), rid(r, i + 1), rid(r + 1, i), rid(r + 1, i + 1)
            F += [(a0, a1, b1), (a0, b1, b0)]
            PE += [(a0, a1), (a1, b1), (b1, b0), (b0, a0)]
    if not hole:
        ci = nv - 1
        for i in range(n):
            F.append((rid(nring - 1, i), rid(nring - 1, i + 1), ci))
            PE += [(rid(nring - 1, i), rid(nring - 1, i + 1)), (rid(nring - 1, i + 1), ci)]
    Ff = np.array(F, np.int64)
    PEf = np.array(PE, np.int64)
    T = [Ff, Ff[:, ::-1] + nv]
    PEs = [PEf, PEf + nv]
    if hole:
        H, HE = [], []
        for i in range(n):
            f0, f1 = rid(nring - 1, i), rid(nring - 1, i + 1)
            b0, b1 = f0 + nv, f1 + nv
            H += [(f0, b1, b0), (f0, f1, b1)]                       # faces the hole axis (outward of the solid)
            HE += [(f0, f1), (f1, b1), (b1, b0), (b0, f0)]
        T.append(np.array(H, np.int64))
        PEs.append(np.array(HE, np.int64))
    if rim == "flat":
        W, WE = [], []
        for i in range(n):
            f0, f1 = rid(0, i), rid(0, i + 1)
            b0, b1 = f0 + nv, f1 + nv
            W += [(f0, b0, b1), (f0, b1, f1)]
            WE += [(f0, f1), (f1, b1), (b1, b0), (b0, f0)]
        T.append(np.array(W, np.int64))
        PEs.append(np.array(WE, np.int64))
    else:
        V, Wt, WE = _rim_profile(V, nv, n, P2, t, rim, chamfer, nround)
        T.append(Wt)
        PEs.append(WE)
    pe = np.unique(np.sort(np.vstack(PEs), 1), axis=0)
    return Mesh(V, np.vstack(T), pe, {"kind": "lens", "t": t, "cap": cap, "R": R, "back": back, "rim": rim,
                                      "nseg": n, "nring": nring, "apex": c.tolist(), "nfront": nv,
                                      "hole": hole})


def _rim_profile(V, nv, n, P2, t, rim, ch, nround):
    """Replace the single wall ring by a chamfer (two 45 deg facets) or a half-round bullnose."""
    nrm = np.c_[outline_normals(P2), np.zeros(n)]
    V = V.copy()
    newV, prof = [], []
    for i in range(n):
        pf, pb = V[i].copy(), V[i + nv].copy()
        if rim == "chamfer":
            V[i] = pf - ch * nrm[i]
            V[i + nv] = pb - ch * nrm[i]
            pts = [pf - np.array([0, 0, ch]), pb + np.array([0, 0, ch])]
        elif rim == "bullnose":
            mid = 0.5 * (pf + pb)
            pts = [mid + (pf - mid) * np.cos(np.pi * j / nround) + nrm[i] * (t / 2) * np.sin(np.pi * j / nround)
                   for j in range(1, nround)]
        else:
            raise ValueError(rim)
        ids = []
        for p in pts:
            newV.append(p)
            ids.append(len(V) + len(newV) - 1)
        prof.append([i] + ids + [i + nv])
    V2 = np.vstack([V, np.array(newV)])
    F, E = [], []
    for i in range(n):
        j = (i + 1) % n
        for k in range(len(prof[i]) - 1):
            a0, a1, b0, b1 = prof[i][k], prof[i][k + 1], prof[j][k], prof[j][k + 1]
            F += [(a0, a1, b1), (a0, b1, b0)]
            E += [(a0, a1), (a1, b1), (b1, b0), (b0, a0)]
    return V2, np.array(F, np.int64), np.array(E, np.int64)


def drilled(nseg: int = 128, hole: float = 0.06, **kw) -> Mesh:
    """A drilled (rimless) lens: the 50 x 38 lens with a concentric through hole (outline scaled by `hole`)."""
    return lens(ellipse(LENS_50x38["a"], LENS_50x38["b"], nseg), hole=hole, **kw)


def open_lens(lens_mesh: Mesh, drop: int = 4) -> Mesh:
    """The lens with its first `drop` front-cap triangles removed (boundary edges)."""
    return Mesh(lens_mesh.V.copy(), lens_mesh.T[drop:].copy(), None, dict(lens_mesh.meta, open=True))


def fused(a: Mesh, b: Mesh) -> Mesh:
    return Mesh(np.vstack([a.V, b.V]), np.vstack([a.T, b.T + len(a.V)]), None, {"kind": "fused"})


def rim_points(m: Mesh) -> tuple[np.ndarray, np.ndarray]:
    """(front rim vertices, back rim vertices) of a lens built here: ring 0 and its back copy."""
    n, nf = m.meta["nseg"], m.meta["nfront"]
    return m.V[:n], m.V[nf:nf + n]


# ---------------------------------------------------------------------------------------------------- frames
def _sweep(O, R, W, section, closed: bool) -> Mesh:
    """Sweep a CCW (r, w) section polygon along origins O with horizontal outward axes R and vertical axes W.
    `section` is (k, 2) or a callable i -> (k, 2) (same k). Capped ends when not closed (convex fan or the
    C-section triangulation below)."""
    m = len(O)
    secs = [np.asarray(section(i) if callable(section) else section, float) for i in range(m)]
    k = len(secs[0])
    V = np.array([O[i] + s[:, 0:1] * R[i] + s[:, 1:2] * W[i] for i, s in enumerate(secs)]).reshape(-1, 3)
    T = []
    nseg = m if closed else m - 1
    for i in range(nseg):
        j = (i + 1) % m
        for a in range(k):
            b = (a + 1) % k
            p0, p1, q0, q1 = i * k + a, i * k + b, j * k + a, j * k + b
            T += [(p0, q0, q1), (p0, q1, p1)]
    if not closed:
        # the side faces run section 0 backwards (p1 -> p0) and the last section forwards, so the start cap keeps
        # the section's order and the end cap reverses it
        cap = _section_triangles(k)
        T += [tuple(t_) for t_ in cap]
        T += [tuple(np.asarray(t_[::-1]) + (m - 1) * k) for t_ in cap]
    T = np.array(T, np.int64)
    mesh = Mesh(V, T)
    # orientation: sweep direction decides the sign; make the volume positive
    from glasses3d.g3d.seat.geom import signed_volume
    if signed_volume(V, T) < 0:
        mesh = Mesh(V, T[:, ::-1].copy())
    return mesh


def _section_triangles(k):
    if k == 8:                         # the C section of c_section(): three convex quads
        A, B, C, D, E, F, G, H = range(8)
        return [(A, B, G), (A, G, H), (B, C, F), (B, F, G), (C, D, E), (C, E, F)]
    return [(0, i, i + 1) for i in range(1, k - 1)]


def c_section(capture: float, t: float, lip: float = 0.6, web: float = 0.9, height: float = 1.8,
              clearance: float = 0.05) -> np.ndarray:
    """CCW C section (r, w): web from the channel bottom r = -capture outward by `web`, lips over both lens faces
    reaching `lip` inward of the bottom; slot height t + 2*clearance, outer height `height`."""
    g = t / 2 + clearance
    h = max(height / 2, g + 0.05)
    r0, r1, rl = -capture, -capture + web, -capture - lip
    return np.array([(rl, -h), (r1, -h), (r1, h), (rl, h), (rl, g), (r0, g), (r0, -g), (rl, -g)])


def oval_section(r_centre: float, w_centre: float, rr: float, rw: float, k: int = 16,
                 phase: float = 0.0) -> np.ndarray:
    """CCW polygon; phase 0 puts vertex 0 at the inner apex (r = r_centre - rr), 0.5 rotates by half a step."""
    th = 2 * np.pi * (np.arange(k) + phase) / k + np.pi
    return np.c_[r_centre + rr * np.cos(th), w_centre + rw * np.sin(th)]


def outline_frame_axes(lens_mesh: Mesh, oversample: int = 1):
    """Per (resampled) outline point: origin at the lens edge mid-thickness, horizontal outward R, vertical W."""
    meta = lens_mesh.meta
    n = meta["nseg"]
    P2 = lens_mesh.V[:n, :2]
    Q = resample(P2, oversample)
    nr = outline_normals(Q)
    z = cap_z(Q[:, 0], Q[:, 1], meta["cap"], meta["R"], apex=meta["apex"]) - meta["t"] / 2
    O = np.c_[Q, z]
    R = np.c_[nr, np.zeros(len(Q))]
    W = np.tile([0.0, 0.0, 1.0], (len(Q), 1))
    return O, R, W, Q


def ring_frame(lens_mesh: Mesh, inset: float = 0.10, width: float = 6.0, front_gap: float = 1.6,
               back_gap: float = 1.9, oversample: int = 4) -> Mesh:
    """Flat extruded acetate ring: aperture = the lens outline offset inward by `inset` (vertical walls), outer
    edge `width` beyond the outline, front face `front_gap` above the highest front rim point, back face `back_gap`
    below the lowest back rim point."""
    O, R, W, Q = outline_frame_axes(lens_mesh, oversample)
    t = lens_mesh.meta["t"]
    zf = O[:, 2] + t / 2
    z1 = float(zf.max()) + front_gap
    z0 = float(zf.min()) - t - back_gap
    inner = Q - inset * R[:, :2]
    outer = Q + width * R[:, :2]
    m = len(Q)
    V = np.vstack([np.c_[inner, np.full(m, z1)], np.c_[outer, np.full(m, z1)],
                   np.c_[inner, np.full(m, z0)], np.c_[outer, np.full(m, z0)]])
    I1, O1, I0, O0 = 0, m, 2 * m, 3 * m
    T = []
    for i in range(m):
        j = (i + 1) % m
        T += [(I1 + i, O1 + i, O1 + j), (I1 + i, O1 + j, I1 + j)]
        T += [(I0 + i, I0 + j, O0 + j), (I0 + i, O0 + j, O0 + i)]
        T += [(O1 + i, O0 + i, O0 + j), (O1 + i, O0 + j, O1 + j)]
        T += [(I1 + i, I1 + j, I0 + j), (I1 + i, I0 + j, I0 + i)]
    mesh = Mesh(V, np.array(T, np.int64), None, {"kind": "ring", "inset": inset, "z": [z0, z1]})
    from glasses3d.g3d.seat.geom import signed_volume
    if signed_volume(mesh.V, mesh.T) < 0:
        mesh.T = mesh.T[:, ::-1].copy()
    return mesh


def arc_mask(lens_mesh: Mesh, sector: str, share: float, oversample: int = 1) -> np.ndarray:
    """Outline points of the contiguous arc holding `share` of the outline LENGTH, centred on the point whose
    outward normal is closest to the sector's direction (top: +Y, bottom: -Y, nasal: toward x = 0, temporal: away)."""
    O, R, W, Q = outline_frame_axes(lens_mesh, oversample)
    cx = Q[:, 0].mean()
    d = {"top": (0.0, 1.0), "bottom": (0.0, -1.0), "nasal": (1.0 if cx < 0 else -1.0, 0.0),
         "temporal": (-1.0 if cx < 0 else 1.0, 0.0)}[sector]
    k0 = int(np.argmax(R[:, 0] * d[0] + R[:, 1] * d[1]))
    seg = np.linalg.norm(np.roll(Q, -1, 0) - Q, axis=1)
    s = np.r_[0.0, np.cumsum(seg)][:-1]
    L = float(seg.sum())
    ds = np.abs(s - s[k0])
    ds = np.minimum(ds, L - ds)
    return ds <= share * L / 2


def eyewire(lens_mesh: Mesh, capture, lip: float = 0.6, web: float = 0.9, height: float = 1.8,
            clearance: float = 0.05) -> Mesh:
    """Closed metal eyewire, C section, capture per outline vertex (array or scalar)."""
    O, R, W, Q = outline_frame_axes(lens_mesh, 1)
    cap = np.broadcast_to(np.asarray(capture, float), (len(Q),))
    t = lens_mesh.meta["t"]
    mesh = _sweep(O, R, W, lambda i: c_section(cap[i], t, lip, web, height, clearance), closed=True)
    mesh.meta = {"kind": "eyewire", "capture": cap.tolist()}
    return mesh


def channel(lens_mesh: Mesh, mask: np.ndarray, capture: float, lip: float = 0.6, web: float = 1.0,
            height: float = 2.6, clearance: float = 0.05, extend: float = 0.37) -> Mesh:
    """Semi-rimless channel (C section) over the contiguous outline arc `mask`, capped ends; `extend` adds that
    many mm beyond each end along the outline tangent."""
    O, R, W, Q = outline_frame_axes(lens_mesh, 1)
    idx = _arc_indices(mask)
    O, R, W = O[idx], R[idx], W[idx]
    if extend:
        O, R, W = _extend(O, R, W, extend)
    t = lens_mesh.meta["t"]
    mesh = _sweep(O, R, W, c_section(capture, t, lip, web, height, clearance), closed=False)
    mesh.meta = {"kind": "channel", "capture": capture, "points": len(idx)}
    return mesh


def cord(lens_mesh: Mesh, mask: np.ndarray, diameter: float = 0.6, r_centre: float = -0.3, extend: float = 1.0,
         k: int = 12) -> Mesh:
    """Nylor cord: a capped round tube along the arc `mask` at r_centre (negative = inside the lens edge, where the
    groove will be), mid-thickness, extended `extend` mm beyond each arc end."""
    O, R, W, Q = outline_frame_axes(lens_mesh, 1)
    idx = _arc_indices(mask)
    O, R, W = _extend(O[idx], R[idx], W[idx], extend)
    # half-step phase: no section vertex exactly on the lens wall (r = 0) when r_centre = -diameter/2
    mesh = _sweep(O, R, W, oval_section(r_centre, 0.0, diameter / 2, diameter / 2, k, 0.5), closed=False)
    mesh.meta = {"kind": "cord", "diameter": diameter}
    return mesh


def station_axes(lens_mesh: Mesh, radius: float = 5.0, inset: float = 0.8, chord: float = 0.3):
    """WP4_DESIGN §3 station frame at every front rim vertex of a lens built here, computed from its own mesh
    (independent of g3d/seat): n = area-weighted normal of the front-cap triangles whose centroid lies within
    `radius` mm of the vertex and >= `inset` mm from the outline (3 mm margins as the definition says nothing finer);
    t = the chord between the first rim vertices >= `chord` mm away on each side, made perpendicular to n; u = t x n,
    outward. Returns (front rim P, back rim partner Pb, n, u)."""
    m = lens_mesh.meta
    nseg, nf = m["nseg"], m["nfront"]
    V, T = lens_mesh.V, lens_mesh.T
    P, Pb = V[:nseg], V[nf:nf + nseg]
    front = T[(T < nf).all(1)]
    a, b, c = V[front[:, 0]], V[front[:, 1]], V[front[:, 2]]
    cr = np.cross(b - a, c - a)
    fa = 0.5 * np.linalg.norm(cr, axis=1)
    fn = cr / np.maximum(2 * fa, 1e-300)[:, None]
    fc = (a + b + c) / 3.0
    A, B = P, np.roll(P, -1, 0)
    AB = B - A
    w = np.clip((((fc[:, None, :] - A[None]) * AB[None]).sum(-1)) / (AB * AB).sum(1)[None], 0.0, 1.0)
    dl = np.linalg.norm(fc[:, None, :] - (A[None] + w[..., None] * AB[None]), axis=2).min(1)
    N = np.zeros_like(P)
    for i in range(nseg):
        sel = (np.linalg.norm(fc - P[i], axis=1) < radius) & (dl >= inset)
        v = (fn[sel] * fa[sel, None]).sum(0)
        N[i] = v / np.linalg.norm(v)
    seg = np.linalg.norm(AB, axis=1)
    s = np.r_[0.0, np.cumsum(seg)]
    L = s[-1]
    s = s[:-1]
    U = np.zeros_like(P)
    for i in range(nseg):
        ds_f = (s - s[i]) % L
        ds_b = (s[i] - s) % L
        kf = int(np.argmin(np.where(ds_f >= chord, ds_f, np.inf)))
        kb = int(np.argmin(np.where(ds_b >= chord, ds_b, np.inf)))
        t = P[kf] - P[kb]
        t -= (t @ N[i]) * N[i]
        t /= np.linalg.norm(t)
        u = np.cross(t, N[i])
        U[i] = u
    out = np.c_[outline_normals(P[:, :2]), np.zeros(nseg)]
    U *= np.sign((U * out).sum(1))[:, None]
    return P, Pb, N, U


def cord_station(lens_mesh: Mesh, mask: np.ndarray, diameter: float = 0.6, depth: float = 0.6, extend: float = 1.0,
                 k: int = 12) -> Mesh:
    """Nylor cord seated where §5 cuts the groove: centred `depth`/2 inside the mid-depth edge point along the lens
    surface's own u (`station_axes`, not the horizontal outline normal), its 12-gon section in the (u, n) plane with
    flats facing +-n (the groove's flanks), extended `extend` mm beyond each arc end. The horizontal `cord()` sits
    ~9 deg off the groove on the R120 cap and overlaps the grooved lens by 0.74 mm3 (WP4 proof review)."""
    P, Pb, N, U = station_axes(lens_mesh)
    C = 0.5 * (P + Pb) - (depth / 2) * U
    idx = _arc_indices(mask)
    C, U, N = _extend(C[idx], U[idx], N[idx], extend)
    tg = np.gradient(C, axis=0)
    tg /= np.linalg.norm(tg, axis=1, keepdims=True)
    N = N - (N * tg).sum(1, keepdims=True) * tg
    N /= np.linalg.norm(N, axis=1, keepdims=True)
    U = np.cross(tg, N)
    mesh = _sweep(C, U, N, oval_section(0.0, 0.0, diameter / 2, diameter / 2, k, 0.5), closed=False)
    mesh.meta = {"kind": "cord", "diameter": diameter, "frame": "station"}
    return mesh


def rect_section(r0: float, r1: float, w0: float, w1: float) -> np.ndarray:
    """CCW rectangle in (r, w)."""
    return np.array([(r0, w0), (r1, w0), (r1, w1), (r0, w1)], float)


ARC_END_MM = 0.37        # capped arcs end this far past their last outline point: an end cap lying exactly in a
                         # station plane would hold the station's u line in its face (a degenerate,
                         # backend-dependent cut)


def swept(lens_mesh: Mesh, section: np.ndarray, mask: np.ndarray | None = None, kind: str = "swept",
          extend: float = ARC_END_MM) -> Mesh:
    """Any (r, w) section swept along the lens outline: closed loop, or the arc `mask` with capped ends."""
    O, R, W, Q = outline_frame_axes(lens_mesh, 1)
    if mask is None:
        mesh = _sweep(O, R, W, section, closed=True)
    else:
        idx = _arc_indices(mask)
        mesh = _sweep(*_extend(O[idx], R[idx], W[idx], extend), section, closed=False)
    mesh.meta = {"kind": kind}
    return mesh


def _arc_indices(mask):
    m = len(mask)
    if mask.all():
        raise ValueError("arc covers the whole outline (use a closed sweep)")
    start = int(np.flatnonzero(mask & ~np.roll(mask, 1))[0])
    n = int(mask.sum())
    return (start + np.arange(n)) % m


def _extend(O, R, W, d):
    t0 = O[0] - O[1]
    t1 = O[-1] - O[-2]
    t0 /= np.linalg.norm(t0)
    t1 /= np.linalg.norm(t1)
    return (np.vstack([O[0] + d * t0, O, O[-1] + d * t1]), np.vstack([R[:1], R, R[-1:]]), np.vstack([W[:1], W, W[-1:]]))


# ----------------------------------------------------------------------------------------------------- scenes
LENS_50x38 = dict(a=25.0, b=19.0)


def base_lens(nseg: int = 128, t: float = 1.6, cap: str = "sphere", **kw) -> Mesh:
    return lens(ellipse(LENS_50x38["a"], LENS_50x38["b"], nseg), t=t, cap=cap, **kw)


def acetate_scene(construction: str = "sphere", inset: float = 0.10) -> dict:
    """§10 P4 robustness matrix: an acetate ring (inset 0.10) around one lens construction."""
    kw = {"sphere": {}, "toric": {"cap": "toric"}, "flat": {"cap": "flat"}, "solidified": {"back": "solidify"},
          "chamfer": {"rim": "chamfer"}, "flipped": {}, "rounded": {"rim": "bullnose"},
          "coarse": {"nseg": 32}}[construction]
    L = base_lens(**kw)
    if construction == "flipped":
        L = L.flipped()
    return {"lens": L, "parts": {"Rim": ring_frame(L, inset)}, "frame_type": "acetate", "inset": inset}


def metal_scene(radial_web: float = 0.9) -> dict:
    """§10 P4 metal: lens 50 x 38, R120, t 1.6; eyewire C section 1.8 mm high; capture 0.10 on the top 40%, 0.45
    elsewhere, -0.30 on the nasal 10%. radial_web 0.5: the narrow-eyewire variant (EXITS_MATERIAL)."""
    L = base_lens()
    top = arc_mask(L, "top", 0.40)
    nasal = arc_mask(L, "nasal", 0.10)
    cap = np.where(top, 0.10, np.where(nasal, -0.30, 0.45))
    return {"lens": L, "parts": {"Eyewire": eyewire(L, cap, web=radial_web)}, "frame_type": "metal",
            "capture": cap, "top": top, "nasal": nasal}


def nylor_scene(with_cord: bool = True, t: float = 2.0, cord_frame: str = "station") -> dict:
    """§10 P4 nylor: top 45% in a channel at 0.20, 0.6 mm cord along the rest, lens t 2.0 (1.4: E_THICKNESS).
    `cord_frame`: 'station' seats the cord where §5 cuts the groove (`cord_station`); 'outline' is the stage-proof
    cord (horizontal outline normal, 0.30 mm inside)."""
    L = base_lens(t=t)
    top = arc_mask(L, "top", 0.45)
    parts = {"Channel": channel(L, top, 0.20)}
    if with_cord:
        parts["Cord"] = (cord_station(L, ~top, 0.6, 0.6, 1.0) if cord_frame == "station"
                         else cord(L, ~top, 0.6, -0.30, 1.0))
    return {"lens": L, "parts": parts, "frame_type": "semi_rimless", "lower_edge": "nylor", "top": top}


def short_channel_scene(share: float = 0.20, capture: float = 0.20) -> dict:
    """A semi_rimless lens (t 2.0) held by a channel over the top `share` only, nothing below (review fix round 2):
    seated as a plain edge, every failing station grows, but the channel ends leave 4 stations TRANSITION, 4 of 67
    required at share 0.20 (> 3%: the check fails, seat_lens says partial) and none at 0.30."""
    L = base_lens(t=2.0)
    return {"lens": L, "parts": {"Channel": channel(L, arc_mask(L, "top", share), capture)},
            "frame_type": "semi_rimless", "lower_edge": "plain"}


def crystal_scene() -> dict:
    """§10 P4 crystal: translucent ring frame, aperture inset 0.10, lens R120 t 1.6."""
    L = base_lens()
    return {"lens": L, "parts": {"Crystal rim": ring_frame(L, 0.10)}, "frame_type": "acetate",
            "translucent": ["Crystal rim"]}


def v_ridge_section(apex_r: float = -0.4, half_height: float = 0.9, outer: float = 1.4) -> np.ndarray:
    """CCW (r, w) section of a frame whose inner face is a 90 deg V ridge: apex at (apex_r, 0) entering the lens
    edge, faces at 45 deg, NO lip over either lens face (review r4 c: a metal eyewire that must fail)."""
    return np.array([(apex_r, 0.0), (apex_r + half_height, -half_height), (outer, -half_height),
                     (outer, half_height), (apex_r + half_height, half_height)], float)


def round_wire(lens_mesh: Mesh, diameter: float = 0.55, r_centre: float = 0.0, k: int = 12) -> Mesh:
    """A closed round wire eyewire (12-gon, 2V/A ~ diameter/2) on the lens outline at mid-thickness."""
    m = swept(lens_mesh, oval_section(r_centre, 0.0, diameter / 2, diameter / 2, k, 0.5))
    m.meta = {"kind": "wire", "diameter": diameter}
    return m


def translated(m: Mesh, d) -> Mesh:
    return Mesh(m.V + np.asarray(d, float), m.T.copy(), m.poly_edges, dict(m.meta, translated=list(map(float, d))))


def mirrored_lens(nseg: int = 128, t: float = 1.6, **kw) -> Mesh:
    """The 50 x 38 lens on the other side of the bridge (outline centre x = +32)."""
    return lens(ellipse(LENS_50x38["a"], LENS_50x38["b"], nseg, cx=32.0), t=t, apex=(32.0, 0.0), **kw)


def decal(lens_mesh: Mesh, centre=(-42.0, 4.0), size=(6.0, 3.0), thickness: float = 0.01, lift: float = 0.005,
          n: int = 7) -> Mesh:
    """A closed marking slab following the front cap (Oakley's OAKLEY / PRIZM: closed, 2V/A ~ 0.003): an n x n
    height-field patch `lift` above the cap, extruded `thickness` up, with side walls."""
    meta = lens_mesh.meta
    xs = np.linspace(centre[0] - size[0] / 2, centre[0] + size[0] / 2, n)
    ys = np.linspace(centre[1] - size[1] / 2, centre[1] + size[1] / 2, n)
    X, Y = np.meshgrid(xs, ys, indexing="ij")
    Z = cap_z(X, Y, meta["cap"], meta["R"], apex=meta["apex"]) + lift
    bot = np.c_[X.ravel(), Y.ravel(), Z.ravel()]
    V = np.vstack([bot, bot + [0.0, 0.0, thickness]])
    top = n * n

    def g(i, j):
        return i * n + j
    T = []
    for i in range(n - 1):
        for j in range(n - 1):
            a, b, c, d = g(i, j), g(i + 1, j), g(i + 1, j + 1), g(i, j + 1)
            T += [(a, c, b), (a, d, c), (a + top, b + top, c + top), (a + top, c + top, d + top)]
    ring = [g(i, 0) for i in range(n)] + [g(n - 1, j) for j in range(1, n)] + \
           [g(i, n - 1) for i in range(n - 2, -1, -1)] + [g(0, j) for j in range(n - 2, 0, -1)]
    for a, b in zip(ring, ring[1:] + ring[:1]):
        T += [(a, b, b + top), (a, b + top, a + top)]
    T = np.array(T, np.int64)
    from glasses3d.g3d.seat.geom import signed_volume
    if signed_volume(V, T) < 0:
        T = T[:, ::-1].copy()
    return Mesh(V, T, None, {"kind": "decal", "thickness": thickness})


def expected_capture(lens_mesh: Mesh, section_capture: float, u: np.ndarray, R_axis: np.ndarray) -> np.ndarray:
    """Capture along u of a vertical wall set `section_capture` inward along the horizontal outline normal."""
    return section_capture / np.abs((u * R_axis).sum(1))


# ------------------------------------------------------------------------------------------------------- GLB
def write_glb(path, nodes: list[dict]) -> None:
    """nodes: [{"name", "mesh": Mesh, "role", "material": {"name", "transmission", "alpha"}, "extras": {...},
    "normals": "none" | "flat" | "smooth"}] -> GLB (positions in metres, float32; uint32 indices)."""
    bin_parts, views, accessors, meshes, gnodes, materials = [], [], [], [], [], []
    mat_index = {}

    def add(arr: np.ndarray, target: int | None, comp: int, typ: str, minmax: bool = False) -> int:
        data = arr.tobytes()
        off = sum(len(b) for b in bin_parts)
        pad = (-len(data)) % 4
        bin_parts.append(data + b"\x00" * pad)
        v = {"buffer": 0, "byteOffset": off, "byteLength": len(data)}
        if target:
            v["target"] = target
        views.append(v)
        acc = {"bufferView": len(views) - 1, "componentType": comp, "count": int(arr.shape[0]) if typ != "SCALAR"
               else int(arr.size), "type": typ}
        if minmax:
            acc["min"] = arr.min(0).tolist()
            acc["max"] = arr.max(0).tolist()
        accessors.append(acc)
        return len(accessors) - 1

    for nd in nodes:
        m: Mesh = nd["mesh"]
        V = np.asarray(m.V, np.float64) / 1000.0
        T = np.asarray(m.T, np.int64)
        mode = nd.get("normals", "none")
        if mode == "flat":
            P = V[T].reshape(-1, 3)
            fn = np.cross(V[T[:, 1]] - V[T[:, 0]], V[T[:, 2]] - V[T[:, 0]])
            fn /= np.maximum(np.linalg.norm(fn, axis=1, keepdims=True), 1e-30)
            N = np.repeat(fn, 3, axis=0)
            I = np.arange(len(P), dtype=np.uint32)
        else:
            P, I = V, T.reshape(-1).astype(np.uint32)
            N = None
            if mode == "smooth":
                fn = np.cross(V[T[:, 1]] - V[T[:, 0]], V[T[:, 2]] - V[T[:, 0]])
                N = np.zeros_like(V)
                for k in range(3):
                    np.add.at(N, T[:, k], fn)
                N /= np.maximum(np.linalg.norm(N, axis=1, keepdims=True), 1e-30)
        attrs = {"POSITION": add(P.astype(np.float32), 34962, 5126, "VEC3", True)}
        if N is not None:
            attrs["NORMAL"] = add(N.astype(np.float32), 34962, 5126, "VEC3")
        ia = add(I, 34963, 5125, "SCALAR")
        md = nd.get("material") or {}
        key = json.dumps(md, sort_keys=True)
        if key not in mat_index:
            mat = {"name": md.get("name", "mat"), "pbrMetallicRoughness": {"baseColorFactor":
                   [0.5, 0.5, 0.5, float(md.get("alpha", 1.0))]}}
            if md.get("alpha", 1.0) < 1.0:
                mat["alphaMode"] = "BLEND"
            if md.get("transmission"):
                mat["extensions"] = {"KHR_materials_transmission": {"transmissionFactor": float(md["transmission"])}}
            materials.append(mat)
            mat_index[key] = len(materials) - 1
        role = nd.get("role")
        extras = dict(nd.get("extras") or {})
        if role:
            extras["partRole"] = role
        meshes.append({"name": nd["name"], "primitives": [{"attributes": attrs, "indices": ia,
                                                            "material": mat_index[key], "mode": 4}],
                       "extras": {"partRole": role} if role else {}})
        gnodes.append({"name": nd["name"], "mesh": len(meshes) - 1, "extras": extras})
    binary = b"".join(bin_parts)
    doc = {"asset": {"version": "2.0", "generator": "glasses3d tests seat_fixtures"}, "scene": 0,
           "scenes": [{"nodes": list(range(len(gnodes)))}], "nodes": gnodes, "meshes": meshes,
           "materials": materials, "accessors": accessors, "bufferViews": views,
           "buffers": [{"byteLength": len(binary)}]}
    if any("extensions" in m for m in materials):
        doc["extensionsUsed"] = ["KHR_materials_transmission"]
    js = json.dumps(doc, separators=(",", ":")).encode("utf-8")
    js += b" " * ((-len(js)) % 4)
    out = struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(binary))
    out += struct.pack("<II", len(js), 0x4E4F534A) + js + struct.pack("<II", len(binary), 0x004E4942) + binary
    with open(path, "wb") as f:
        f.write(out)


def patch_glb(src, dst, fn) -> None:
    """Copy a GLB written by write_glb with its JSON chunk edited in place by `fn(doc)` (the BIN chunk is kept)."""
    b = open(src, "rb").read()
    jl, = struct.unpack("<I", b[12:16])
    doc = json.loads(b[20:20 + jl])
    rest = b[20 + jl:]
    fn(doc)
    js = json.dumps(doc, separators=(",", ":")).encode("utf-8")
    js += b" " * ((-len(js)) % 4)
    with open(dst, "wb") as f:
        f.write(struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(js) + len(rest))
                + struct.pack("<II", len(js), 0x4E4F534A) + js + rest)


def browline_lifted_scene(proud: float = 0.2) -> dict:
    """A browline after hide_eyewire (review round 2, finding 1): over the top 20% the eyewire is lifted out of
    the lens edge (r 0.3-1.0) under a brow that is now the INNERMOST part (r -0.7-3.5, w -1.0-2.1; capture 0.70)
    and sticks out `proud` mm behind the brow's back face; elsewhere it holds the lens at 0.45 with lips over both
    faces. Flat caps (w = n). `range` = the x extent of the lifted arc minus 2 mm each side (asset x)."""
    L = base_lens(cap="flat")
    lifted = arc_mask(L, "top", 0.20)
    TOP = rect_section(0.3, 1.0, -1.0 - proud, 0.6)
    REST = rect_section(-0.45, 0.45, -1.0, 1.0)
    eyewire = swept(L, lambda i: TOP if lifted[i] else REST)
    brow = swept(L, rect_section(-0.7, 3.5, -1.0, 2.1), arc_mask(L, "top", 0.25))
    x = L.V[:L.meta["nseg"], 0][lifted]
    return {"lens": L, "parts": {"Eyewire": eyewire, "Brow": brow}, "frame_type": "browline",
            "range": (float(x.min()) + 2.0, float(x.max()) - 2.0), "proud": proud}


def scene_nodes(scene: dict, lens_name: str = "Lens_L", normals: str = "smooth", seat_extras: dict | None = None,
                extra_nodes: list | None = None) -> list[dict]:
    """GLB node list for a scene dict (lens + parts)."""
    lens_extras = {"g3d_seat": seat_extras} if seat_extras else {}
    out = [{"name": lens_name, "mesh": scene["lens"], "role": "lens", "normals": normals, "extras": lens_extras,
            "material": {"name": "Lens", "transmission": 1.0}}]
    for name, mesh in scene["parts"].items():
        tr = name in scene.get("translucent", ())
        out.append({"name": name, "mesh": mesh, "role": "frame", "normals": "smooth",
                    "material": {"name": "Crystal" if tr else "Frame", "transmission": 0.9 if tr else 0.0}})
    return out + list(extra_nodes or [])


def digest(*meshes: Mesh) -> str:
    import hashlib
    h = hashlib.sha256()
    for m in meshes:
        h.update(np.ascontiguousarray(m.V, np.float64).tobytes())
        h.update(np.ascontiguousarray(m.T, np.int64).tobytes())
    return h.hexdigest()


def browline_scene(brow_share: float = 0.25) -> dict:
    """A browline BEFORE hide_eyewire (WP4 tools stage): flat caps, a closed eyewire holding the lens at 0.45 all
    round (r -0.45..0.45, w -1.0..1.0) and a brow over the top `brow_share` (r 0.2..3.5, w -1.5..2.1) that covers the
    eyewire only from r 0.2 outward, so the eyewire's inner 0.65 mm shows below the brow from the front. Burying
    it with the 0.10 mm margin needs a lift of 0.75 mm. `range` = the x extent of the brow arc minus 2 mm each side
    (asset x)."""
    L = base_lens(cap="flat")
    arc = arc_mask(L, "top", brow_share)
    eyewire = swept(L, rect_section(-0.45, 0.45, -1.0, 1.0))
    brow = swept(L, rect_section(0.2, 3.5, -1.5, 2.1), arc)
    x = L.V[:L.meta["nseg"], 0][arc]
    return {"lens": L, "parts": {"Eyewire": eyewire, "Brow": brow}, "frame_type": "browline",
            "range": (float(x.min()) + 2.0, float(x.max()) - 2.0), "lift_mm": 0.75}


def notched_outline(a: float = 25.0, b: float = 19.0, n: int = 256, depth: float = 2.0, width: float = 0.08,
                    theta0: float = 0.0, cx: float = -32.0) -> np.ndarray:
    """The 50 x 38 ellipse with a narrow inward notch (Gaussian dent of `depth` mm, angular sigma `width` rad) at
    angle theta0 (0 = the nasal side for a lens at x < 0): a concave outline with a small radius of curvature."""
    th = 2 * np.pi * np.arange(n) / n
    d = th - theta0
    d = (d + np.pi) % (2 * np.pi) - np.pi
    r = 1.0 - (depth / a) * np.exp(-(d / width) ** 2)
    return np.c_[cx + a * r * np.cos(th), b * r * np.sin(th)]


def browline_pair_scene(brow_share: float = 0.25) -> dict:
    """Two browline sides (review fix round 1: unhide_eyewire must restore only the eyewire it is given and the lens
    that eyewire held): `browline_scene` on the left lens (x < 0) and its mirror on the right lens (outline centre
    x = +32). `ranges` per side: the x extent of each brow arc minus 2 mm at each end (asset x)."""
    left = browline_scene(brow_share)
    R = mirrored_lens(cap="flat")
    arc = arc_mask(R, "top", brow_share)
    ew_r = swept(R, rect_section(-0.45, 0.45, -1.0, 1.0))
    brow_r = swept(R, rect_section(0.2, 3.5, -1.5, 2.1), arc)
    x = R.V[:R.meta["nseg"], 0][arc]
    return {"lenses": {"Lens_L": left["lens"], "Lens_R": R},
            "parts": {"Eyewire_L": left["parts"]["Eyewire"], "Brow_L": left["parts"]["Brow"],
                      "Eyewire_R": ew_r, "Brow_R": brow_r},
            "ranges": {"L": left["range"], "R": (float(x.min()) + 2.0, float(x.max()) - 2.0)},
            "frame_type": "browline", "lift_mm": left["lift_mm"]}


def self_intersecting(lens_mesh: Mesh, depth: float = 2.4, radius: float = 8.0) -> Mesh:
    """§10 P4 refusal 'self-intersection': the lens with its front cap pushed back through its back cap around the
    apex (a smooth dent `depth` mm deep, deeper than the lens is thick, within `radius` mm of the apex). The mesh stays
    closed, manifold and consistently wound (connectivity unchanged) and its rim is untouched, but the front cap
    crosses the back cap on a ring."""
    V = lens_mesh.V.copy()
    nf = lens_mesh.meta["nfront"]
    ax, ay = lens_mesh.meta["apex"]
    r = np.hypot(V[:nf, 0] - ax, V[:nf, 1] - ay)
    w = np.clip(1.0 - (r / radius) ** 2, 0.0, None) ** 2
    V[:nf, 2] -= depth * w
    return Mesh(V, lens_mesh.T.copy(), lens_mesh.poly_edges, dict(lens_mesh.meta, self_intersecting=depth))


def fused_with_edges(a: Mesh, b: Mesh) -> Mesh:
    """Two lenses in one mesh (§10 P4 'two fused components are analysed separately'), polygon edges kept."""
    return Mesh(np.vstack([a.V, b.V]), np.vstack([a.T, b.T + len(a.V)]),
                np.vstack([a.poly_edges, b.poly_edges + len(a.V)]), {"kind": "fused"})


def vbevel_lens(lens_mesh: Mesh) -> Mesh:
    """A flat-walled lens built here, re-walled with the §5 V-bevel (g3d/seat/profile.rewall_plan on its ring 0 and
    back copy, outward = the horizontal outline normal): flats 0.10 mm, 120 deg, apex h = (t - 0.2) / 2 / tan 60 at
    mid-depth. For a flat cap the station u is that normal, so the bevel is the one seat_lens builds."""
    from glasses3d.g3d.seat import profile as PR
    m = lens_mesh.meta
    if m.get("rim") != "flat":
        raise ValueError("vbevel_lens needs a flat wall")
    n, nf = m["nseg"], m["nfront"]
    front = np.arange(n)
    back = nf + np.arange(n)
    u = np.c_[outline_normals(lens_mesh.V[:n, :2]), np.zeros(n)]
    plan = PR.rewall_plan(lens_mesh.V, front, back, u, "vbevel", cap_edge_dir=(0, 1))
    caps = lens_mesh.T[:-2 * n]                                  # lens(): the flat wall is the last 2n triangles
    quads = np.array(plan.faces, np.int64)
    T = np.vstack([caps, quads[:, [0, 1, 2]], quads[:, [0, 2, 3]]])
    V = np.vstack([lens_mesh.V, plan.new_V])
    from glasses3d.g3d.seat.geom import signed_volume
    if signed_volume(V, T) < 0:
        raise ValueError("re-walled lens is inside out")
    return Mesh(V, T, None, dict(m, rim="vbevel", h=float(PR.bevel_height(m["t"]))))


def grooved_ring(lens_mesh: Mesh, corner_capture: float = 0.396, clearance: float = 0.03, lip: float = 1.0,
                 width: float = 3.0) -> Mesh:
    """A ring frame whose inner face is the §5 groove for `vbevel_lens(lens_mesh)` on a flat cap: lips over both lens
    faces from the aperture (`corner_capture` mm inside the lens corners) outward, and between them a V-groove
    following the bevel (corner, shoulder 0.10 mm in, apex h) offset `clearance` mm outward along u. Swept along the
    outline in the horizontal / vertical frame, which on a flat cap is the lens's own (u, n)."""
    from glasses3d.g3d.seat import profile as PR
    t = lens_mesh.meta["t"]
    h = PR.bevel_height(t)
    T2, s2, c = t / 2.0, t / 2.0 - PR.FLAT_MM, clearance
    ra = -corner_capture
    sec = np.array([(ra, -T2 - lip), (width, -T2 - lip), (width, T2 + lip), (ra, T2 + lip), (ra, T2 + c), (c, T2 + c),
                    (c, s2), (h + c, 0.0), (c, -s2), (c, -T2 - c), (ra, -T2 - c)], float)
    O, R, W, _ = outline_frame_axes(lens_mesh, 1)
    mesh = _sweep(O, R, W, sec, closed=True)
    mesh.meta = {"kind": "grooved_ring", "corner_capture": corner_capture, "clearance": clearance, "h": float(h)}
    return mesh


def solid_ring(lens_mesh: Mesh, corner_capture: float = 0.396, lip: float = 1.0, width: float = 3.0) -> Mesh:
    """`grooved_ring` without the groove (WP4 proof review fix round 1): a solid ring whose inner face is the aperture
    `corner_capture` mm inside the lens corners, so a `vbevel_lens(lens_mesh)` sits buried in frame material (a deleted
    g3d_groove Boolean, or a crystal rim never cut). Same sweep and frame as `grooved_ring`."""
    t = lens_mesh.meta["t"]
    T2, ra = t / 2.0, -corner_capture
    sec = np.array([(ra, -T2 - lip), (width, -T2 - lip), (width, T2 + lip), (ra, T2 + lip)], float)
    O, R, W, _ = outline_frame_axes(lens_mesh, 1)
    mesh = _sweep(O, R, W, sec, closed=True)
    mesh.meta = {"kind": "solid_ring", "corner_capture": corner_capture}
    return mesh
