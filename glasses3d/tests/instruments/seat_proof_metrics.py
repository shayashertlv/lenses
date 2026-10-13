"""Independent measurements for the WP4 §10 proof (P2-P5) on exported GLBs (TESTS ONLY).

Shares NO code with glasses3d/g3d/seat (WP4_DESIGN §10: every case is graded by the core AND by an instrument the tool
does not share). The GLB is read with the generic WP3 reader (g3d_host/glb.py: positions, triangles, normals and
extras, nothing about seating); lens structure, stations and occupancy rows come from the designers' grid instrument
port (tests/instruments/seat_grid.py); everything else is here:

- `read_glb`: per node world-mm positions, triangles, world normals, partRole;
- `topology`, `components`, `signed_volume`: own exact weld, closed / chi / components, volume;
- `bodies_and_parts`: lens bodies (closed lens components, 2V/A >= 0.3 mm) and frame solids (closed frame
  components, 2V/A >= 0.05 mm, within 6 mm of the lens box);
- `frames`: the instrument's station frames (1 mm stations, u flipped when Pm - 0.4 u is outside the lens);
- `rows`: WP4_DESIGN §3 capture and lens edge on exact depth rows at GIVEN frames (refined occupancy runs), so a
  before and an after file are compared at the same stations;
- `corner_cover`: frame along +n from the front corner (-n from the back corner) moved 0.10 mm inward, the corner
  found on the 0.05 / 0.95 rows of the file measured;
- `rim_split`: the angle between the cap-side and wall-side GLB normals at the outer rim loops;
- `tube_sections`: an eyewire's cross-section area in planes perpendicular to its own axis (axis from section
  centroids at the neighbouring stations), so a lift on a ramp does not read as a shear;
- `edge_profile`: the lens edge along u on fine depth rows (groove and V-bevel geometry);
- `pair_overlap_mm3`, `removed_estimate_mm3`: volumes integrated over station planes (area x arc spacing);
- `arc_extent`: the arc of the lens outline a part covers (the nylor cord's ends in the channel);
- `accessor_bytes`: the raw bytes of a node's primitive accessors (re-export byte identity).

Units: world mm. Every function takes plain arrays or a GLB path.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

from glasses3d.g3d_host import glb as GL
from glasses3d.tests.instruments import seat_grid as IG

BODY_MIN_MM = 0.3          # §3 lens body: 2V/A >= 0.3 mm (restated, not imported)
MARKING_MAX_MM = 0.05      # closed frame components thinner than this are markings (§3 riders 0.002-0.028 mm)
NEAR_MM = 6.0              # §8 frame parts within 6 mm of the rim
MARGIN_MM = 0.10           # §7 hidden margin (cover lines start this far inside the corner)
COVER_REACH_MM = 6.0       # §3 cover reach


# --------------------------------------------------------------------------------------------------- reading
def _role(doc: dict, p) -> str | None:
    node = doc["nodes"][p.node_index]
    mesh = doc["meshes"][p.mesh_index]
    mat = doc.get("materials", [])[p.material_index] if p.material_index is not None else {}
    for ex in ((mat or {}).get("extras"), mesh.get("extras"), node.get("extras")):
        if isinstance(ex, dict) and isinstance(ex.get("partRole"), str):
            return ex["partRole"].strip().lower()
    return None


def read_glb(path) -> dict:
    """{node name: {"V", "T", "N" (world unit normals or None), "role", "extras", "node_index"}}."""
    g = GL.load(path)
    out: dict = {}
    for p in g.primitives:
        if p.error or p.compressed or len(p.triangles) == 0:
            continue
        d = out.setdefault(p.node_name, {"V": [], "T": [], "N": [], "role": None, "n": 0, "has_n": True,
                                         "extras": g.doc["nodes"][p.node_index].get("extras") or {},
                                         "node_index": p.node_index})
        V = np.asarray(p.positions, np.float64) * 1000.0
        d["V"].append(V)
        d["T"].append(np.asarray(p.triangles, np.int64) + d["n"])
        if p.normals is not None and len(p.normals) == len(V):
            n = np.asarray(p.normals, np.float64) @ np.linalg.inv(np.asarray(p.world, np.float64)[:3, :3])
            d["N"].append(n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-30))
        else:
            d["has_n"] = False
            d["N"].append(np.full_like(V, np.nan))
        d["n"] += len(V)
        d["role"] = d["role"] or _role(g.doc, p)
    for d in out.values():
        d["V"] = np.concatenate(d["V"])
        d["T"] = np.concatenate(d["T"])
        d["N"] = np.concatenate(d["N"]) if d["has_n"] else None
    return out


# ------------------------------------------------------------------------------------------------- topology
def weld_exact(V):
    Vw, inv = np.unique(np.asarray(V, np.float64), axis=0, return_inverse=True)
    return Vw, np.asarray(inv).reshape(-1)


def _edges(T):
    e = np.sort(np.concatenate([T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]]), 1)
    u, cnt = np.unique(e, axis=0, return_counts=True)
    return u, cnt


def topology(V, T) -> dict:
    Vw, vid = weld_exact(V)
    Tw = vid[np.asarray(T, np.int64)]
    Tw = Tw[(Tw[:, 0] != Tw[:, 1]) & (Tw[:, 1] != Tw[:, 2]) & (Tw[:, 0] != Tw[:, 2])]
    E, cnt = _edges(Tw)
    used = np.unique(Tw)
    nv = len(Vw)
    g = coo_matrix((np.ones(len(E)), (E[:, 0], E[:, 1])), shape=(nv, nv))
    ncomp, lab = connected_components(g, directed=False)
    comps = len(np.unique(lab[used]))
    return {"closed": bool((cnt == 2).all()), "boundary_edges": int((cnt == 1).sum()),
            "nonmanifold_edges": int((cnt > 2).sum()), "euler": int(len(used) - len(E) + len(Tw)),
            "components": int(comps), "triangles": int(len(Tw))}


def signed_volume(V, T) -> float:
    V = np.asarray(V, np.float64)
    T = np.asarray(T, np.int64)
    a, b, c = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
    return float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6.0)


def area(V, T) -> float:
    V = np.asarray(V, np.float64)
    T = np.asarray(T, np.int64)
    return float(0.5 * np.linalg.norm(np.cross(V[T[:, 1]] - V[T[:, 0]], V[T[:, 2]] - V[T[:, 0]]), axis=1).sum())


def components(V, T) -> list[dict]:
    """Connected components after the exact weld: {"V", "T", "closed", "thick" (2V/A), "volume"}."""
    Vw, vid = weld_exact(V)
    Tw = vid[np.asarray(T, np.int64)]
    Tw = Tw[(Tw[:, 0] != Tw[:, 1]) & (Tw[:, 1] != Tw[:, 2]) & (Tw[:, 0] != Tw[:, 2])]
    if not len(Tw):
        return []
    nv = len(Vw)
    E = np.sort(np.concatenate([Tw[:, [0, 1]], Tw[:, [1, 2]]]), 1)
    g = coo_matrix((np.ones(len(E)), (E[:, 0], E[:, 1])), shape=(nv, nv))
    _, lab = connected_components(g, directed=False)
    out = []
    for c in np.unique(lab[Tw[:, 0]]):
        Tk = Tw[lab[Tw[:, 0]] == c]
        used = np.unique(Tk)
        remap = np.full(nv, -1, np.int64)
        remap[used] = np.arange(len(used))
        Vk, Tk = Vw[used], remap[Tk]
        _, cnt = _edges(Tk)
        closed = bool((cnt == 2).all())
        vol = signed_volume(Vk, Tk)
        if vol < 0:
            Tk = Tk[:, ::-1].copy()
            vol = -vol
        A = area(Vk, Tk)
        out.append({"V": Vk, "T": Tk, "closed": closed, "volume": vol, "thick": 2 * vol / A if A > 0 else 0.0})
    return out


def bodies_and_parts(sc: dict, lens_nodes=None, frame_nodes=None) -> tuple[list, list]:
    """([(node, V, T)] lens bodies, [(name, V, T)] frame solids near any body). Roles from partRole; `lens_nodes` /
    `frame_nodes` restrict by node name."""
    bodies, solids = [], []
    for name, d in sorted(sc.items()):
        if d["role"] != "lens" or (lens_nodes is not None and name not in lens_nodes):
            continue
        for k, c in enumerate(components(d["V"], d["T"])):
            if c["closed"] and c["thick"] >= BODY_MIN_MM:
                bodies.append((name if k == 0 else f"{name}#{k}", c["V"], c["T"]))
    if not bodies:
        return bodies, solids
    lo = np.min([b[1].min(0) for b in bodies], 0) - NEAR_MM
    hi = np.max([b[1].max(0) for b in bodies], 0) + NEAR_MM
    for name, d in sorted(sc.items()):
        if d["role"] != "frame" or (frame_nodes is not None and name not in frame_nodes):
            continue
        for k, c in enumerate(components(d["V"], d["T"])):
            if not c["closed"] or c["thick"] < MARKING_MAX_MM:
                continue
            if (c["V"].max(0) >= lo).all() and (c["V"].min(0) <= hi).all():
                solids.append((f"{name}#{k}", c["V"], c["T"]))
    return bodies, solids


def near_parts(body_V, solids, pad: float = NEAR_MM) -> list:
    lo, hi = body_V.min(0) - pad, body_V.max(0) + pad
    return [s for s in solids if (s[1].max(0) >= lo).all() and (s[1].min(0) <= hi).all()]


# --------------------------------------------------------------------------------------------------- frames
def _back_by_plane(BL: np.ndarray, P: np.ndarray, t: np.ndarray, max_mm: float = 6.0) -> np.ndarray:
    """Per station: the back loop cut by the station plane (through P, normal t), the crossing nearest to P."""
    A, B = BL, np.roll(BL, -1, 0)
    out = np.full_like(P, np.nan)
    for k in range(len(P)):
        sa = (A - P[k]) @ t[k]
        sb = (B - P[k]) @ t[k]
        cr = (sa > 0) != (sb > 0)
        if not cr.any():
            continue
        w = sa[cr] / (sa[cr] - sb[cr])
        X = A[cr] + w[:, None] * (B[cr] - A[cr])
        d = np.linalg.norm(X - P[k], axis=1)
        j = int(np.argmin(d))
        if d[j] <= max_mm:
            out[k] = X[j]
    return out


def frames(lens_V, lens_T, spacing: float = 1.0) -> dict:
    """The grid instrument's stations (seat_grid.analyse's frame construction) plus the lens scene. Where the port's
    rung (a wall triangle joining a front and a back rim vertex) does not exist, as on a re-walled V-bevel or grooved
    edge, the back corner is the back loop cut by the station plane."""
    lz = IG.lens_structure(lens_V, lens_T)
    ss, Pf, Pb, n, t, uo, L = IG.stations(lz, spacing)
    bad = np.linalg.norm(Pb - Pf, axis=1) < 1e-9
    if bad.any():
        Pb = Pb.copy()
        Pb[bad] = _back_by_plane(lz["Vw"][lz["bl"]], Pf[bad], t[bad])
    Pm = 0.5 * (Pf + Pb)
    scene = IG.build_scene(lz["Vw"], lz["Tw"])
    # u points outward when a point 0.4 mm inward of the edge is inside the lens; tested at 20% of the depth (on the
    # flat beside a groove: at mid-depth a 0.6 mm nylor notch leaves Pm - 0.4 u outside the lens and the port's
    # mid-depth test flips u inward)
    inside = IG._occupancy(scene, Pf + 0.2 * (Pb - Pf) - 0.4 * uo)
    uo[~inside] *= -1
    t[~inside] *= -1
    uf, vf = ((Pf - Pm) * uo).sum(1), ((Pf - Pm) * n).sum(1)
    ub, vb = ((Pb - Pm) * uo).sum(1), ((Pb - Pm) * n).sum(1)
    sector = np.where(uo[:, 1] > 0.707, "top", np.where(uo[:, 1] < -0.707, "bottom",
                      np.where(np.sign(uo[:, 0]) == -np.sign(Pm[:, 0]), "nasal", "temporal")))
    return {"lz": lz, "s": ss, "Pf": Pf, "Pb": Pb, "Pm": Pm, "n": n, "t": t, "u": uo, "uf": uf, "vf": vf, "ub": ub,
            "vb": vb, "L": float(L), "sector": sector, "scene": scene}


def scenes(parts) -> list:
    return [IG.build_scene(V, T) for _, V, T in parts]


def rows(F: dict, lens_scene, part_scenes, fractions) -> dict:
    """§3 capture and the lens edge (u from Pm) on the rows at rung `fractions` (from the front corner) of the GIVEN
    frames; capture 99 = frame from before the grid, nan = none within reach."""
    fr = np.asarray(fractions, float)
    nS = len(F["s"])
    vv = F["vf"][:, None] + fr[None, :] * (F["vb"] - F["vf"])[:, None]
    ee = F["uf"][:, None] + fr[None, :] * (F["ub"] - F["uf"])[:, None]
    us = np.arange(IG.U0, IG.U1 + 1e-9, IG.DU)
    base = (F["Pm"][:, None, :] + vv[:, :, None] * F["n"][:, None, :]).reshape(-1, 3)
    dirs = np.repeat(F["u"], len(fr), axis=0)
    flat = (base[:, None, :] + us[None, :, None] * dirs[:, None, :]).reshape(-1, 3)
    lens_runs = IG._refined_runs(lens_scene, IG._occupancy(lens_scene, flat).reshape(-1, len(us)), base, dirs, us)
    part_runs = [IG._refined_runs(s, IG._occupancy(s, flat).reshape(-1, len(us)), base, dirs, us)
                 for s in part_scenes]
    cap = np.full(nS * len(fr), np.nan)
    edge = np.full(nS * len(fr), np.nan)
    for r in range(nS * len(fr)):
        edge[r], cap[r] = IG._graded(lens_runs[r], IG._union([pr[r] for pr in part_runs]), float(ee.reshape(-1)[r]))
    return {"cap": cap.reshape(nS, len(fr)), "edge": edge.reshape(nS, len(fr)), "rung": ee, "v": vv,
            "fractions": fr.tolist()}


def _line_runs(scene_list, O, D, t0: float, t1: float, step: float = 0.01) -> list:
    ts = np.arange(t0, t1 + 1e-9, step)
    flat = (O[:, None, :] + ts[None, :, None] * D[:, None, :]).reshape(-1, 3)
    runs = []
    for s in scene_list:
        runs.append(IG._refined_runs(s, IG._occupancy(s, flat).reshape(len(O), len(ts)), O, D, ts))
    return [IG._union([r[i] for r in runs]) for i in range(len(O))]


def corner_cover(F: dict, lens_scene, part_scenes) -> dict:
    """Cover of the front / back corner of the file measured at the given stations: the corner is where the 0.05 /
    0.95 row leaves the lens (at the corner's depth); the cover line starts 0.10 mm inside it and runs along +n
    (front) or -n (back): the start of the first frame run reaching beyond the origin (<= 0: inside), nan when none
    within 6 mm."""
    R = rows(F, lens_scene, [], (0.05, 0.95))
    out = {}
    for key, j, vkey, sign in (("front", 0, "vf", 1.0), ("back", 1, "vb", -1.0)):
        e = R["edge"][:, j]
        P = F["Pm"] + e[:, None] * F["u"] + F[vkey][:, None] * F["n"]
        O = P - MARGIN_MM * F["u"]
        D = sign * F["n"]
        U = _line_runs(part_scenes, O, D, -COVER_REACH_MM, COVER_REACH_MM)
        c = np.full(len(O), np.nan)
        for i, runs in enumerate(U):
            for lo, hi in runs:
                if hi > 0:
                    c[i] = lo if lo <= COVER_REACH_MM else np.nan
                    break
        out[key] = c
        out[key + "_corner"] = P
    return out


# -------------------------------------------------------------------------------------------------- rim split
def rim_split(V, T, N) -> dict:
    """Angle (deg) between the mean cap-side and mean wall-side GLB normals at each outer rim vertex."""
    V = np.asarray(V, np.float64)
    T = np.asarray(T, np.int64)
    lz = IG.lens_structure(V, T)
    vid = IG.weld(V)
    Tw = vid[T]
    keep = (Tw[:, 0] != Tw[:, 1]) & (Tw[:, 1] != Tw[:, 2]) & (Tw[:, 0] != Tw[:, 2])
    cls = np.full(len(T), -1)
    cls[keep] = lz["Tcls"]
    out = {}
    allang = []
    for key, cap, loop in (("front", 0, lz["fl"]), ("back", 1, lz["bl"])):
        pos = np.full(int(vid.max()) + 1, -1)
        pos[loop] = np.arange(len(loop))
        cs, ws = np.zeros((len(loop), 3)), np.zeros((len(loop), 3))
        for c, acc in ((cap, cs), (2, ws)):
            corners = T[cls == c].reshape(-1)
            k = pos[vid[corners]]
            m = k >= 0
            np.add.at(acc, k[m], N[corners[m]])
        good = (np.linalg.norm(cs, axis=1) > 0) & (np.linalg.norm(ws, axis=1) > 0)
        a = cs[good] / np.linalg.norm(cs[good], axis=1, keepdims=True)
        b = ws[good] / np.linalg.norm(ws[good], axis=1, keepdims=True)
        ang = np.degrees(np.arccos(np.clip((a * b).sum(1), -1, 1)))
        allang.append(ang)
        out[key] = [round(float(x), 2) for x in np.percentile(ang, [5, 50, 95])] if len(ang) else None
    a = np.concatenate(allang) if allang else np.zeros(0)
    out["both"] = [round(float(x), 2) for x in np.percentile(a, [5, 50, 95])] if len(a) else None
    return out


def wall_flatness_deg(V, T, N) -> float:
    """Largest angle between a wall triangle's corner normals (GLB) and its face normal: 0 for flat-shaded walls."""
    V = np.asarray(V, np.float64)
    T = np.asarray(T, np.int64)
    lz = IG.lens_structure(V, T)
    vid = IG.weld(V)
    Tw = vid[T]
    keep = (Tw[:, 0] != Tw[:, 1]) & (Tw[:, 1] != Tw[:, 2]) & (Tw[:, 0] != Tw[:, 2])
    cls = np.full(len(T), -1)
    cls[keep] = lz["Tcls"]
    W = T[cls == 2]
    fn = np.cross(V[W[:, 1]] - V[W[:, 0]], V[W[:, 2]] - V[W[:, 0]])
    fn /= np.maximum(np.linalg.norm(fn, axis=1, keepdims=True), 1e-30)
    worst = 0.0
    for k in range(3):
        ang = np.degrees(np.arccos(np.clip((N[W[:, k]] * fn).sum(1), -1, 1)))
        worst = max(worst, float(ang.max()) if len(ang) else 0.0)
    return worst


# ----------------------------------------------------------------------------------------------- tube sections
def _plane_grid(O, e1, e2, half: float, step: float):
    g = np.arange(-half, half + 1e-9, step)
    A, B = np.meshgrid(g, g, indexing="xy")
    return O + A.reshape(-1, 1) * e1 + B.reshape(-1, 1) * e2, A.reshape(-1), B.reshape(-1)


def tube_sections(F: dict, idx, tube_scene, window=(-2.0, 4.0, -4.0, 4.0), coarse: float = 0.02,
                  fine: float = 0.01, half: float = 1.6) -> dict:
    """Per station in `idx`: the tube's section centroid in the station plane (u, n), then its cross-section area
    in the plane through that centroid perpendicular to the tube axis (centroids at the neighbouring stations k-1,
    k+1). nan where the tube is not in the window or a neighbour is missing."""
    u0, u1, v0, v1 = window
    us = np.arange(u0, u1 + 1e-9, coarse)
    vs = np.arange(v0, v1 + 1e-9, coarse)
    UU, VV = np.meshgrid(us, vs)
    nS = len(F["s"])
    need = sorted({(k + d) % nS for k in idx for d in (-1, 0, 1)})
    cen = {}
    for k in need:
        P = (F["Pm"][k] + UU.reshape(-1, 1) * F["u"][k] + VV.reshape(-1, 1) * F["n"][k])
        occ = IG._occupancy(tube_scene, P)
        cen[k] = P[occ].mean(0) if occ.any() else None
    area = np.full(len(idx), np.nan)
    for q, k in enumerate(idx):
        a, c, b = cen.get((k - 1) % nS), cen.get(k), cen.get((k + 1) % nS)
        if a is None or b is None or c is None:
            continue
        ax = b - a
        ax /= np.linalg.norm(ax)
        e1 = F["u"][k] - (F["u"][k] @ ax) * ax
        e1 /= np.linalg.norm(e1)
        e2 = np.cross(ax, e1)
        P, _, _ = _plane_grid(c, e1, e2, half, fine)
        area[q] = float(IG._occupancy(tube_scene, P).sum() * fine * fine)
    return {"area_mm2": area, "centroids": cen}


# ----------------------------------------------------------------------------------------------- edge profiles
def edge_profile(F: dict, k: int, lens_scene, dv: float = 0.005, pad: float = 0.05) -> dict:
    """The lens edge along u (from Pm) on rows every `dv` mm of depth (v along n) from the back corner to the front
    corner of station k: the row's lens run containing the corner-to-corner rung, else the last one ending within
    3 mm before it (a groove)."""
    vb, vf = F["vb"][k], F["vf"][k]
    vs = np.arange(vb + pad, vf - pad + 1e-9, dv)
    frac = (vs - vb) / (vf - vb)
    rung = F["ub"][k] + frac * (F["uf"][k] - F["ub"][k])
    us = np.arange(IG.U0, IG.U1 + 1e-9, IG.DU)
    base = F["Pm"][k] + vs[:, None] * F["n"][k]
    dirs = np.repeat(F["u"][k][None], len(vs), 0)
    flat = (base[:, None, :] + us[None, :, None] * dirs[:, None, :]).reshape(-1, 3)
    runs = IG._refined_runs(lens_scene, IG._occupancy(lens_scene, flat).reshape(len(vs), len(us)), base, dirs, us,
                            iters=14)
    e = np.array([IG._graded(r, np.zeros((0, 2)), float(x))[0] for r, x in zip(runs, rung)])
    return {"v": vs, "e": e, "rung": rung}


def groove_dims(prof: dict, depth_mm: float = 0.60) -> dict | None:
    """Width (along the corner-to-corner rung), depth (along u, below the rung) and centre offset (along the rung,
    from the rung midpoint) of a notch in an edge profile. The side walls are where the edge crosses half the notch
    depth (interpolated between rows); they are placed on the rung line at their depth, so the slant of the rung in
    the station plane does not bias the centre."""
    v, e, rung = prof["v"], prof["e"], prof["rung"]
    rec = e - rung
    inside = rec < -depth_mm / 2
    if not inside.any() or inside[0] or inside[-1]:
        return None
    idx = np.flatnonzero(inside)
    i0, i1 = idx[0], idx[-1]

    def cross(a, b):
        w = (-depth_mm / 2 - rec[a]) / (rec[b] - rec[a])
        return v[a] + w * (v[b] - v[a])
    va, vb_ = cross(i0 - 1, i0), cross(i1 + 1, i1)
    slope = (rung[-1] - rung[0]) / (v[-1] - v[0])
    pa = np.array([rung[0] + slope * (va - v[0]), va])
    pb = np.array([rung[0] + slope * (vb_ - v[0]), vb_])
    d = np.array([rung[-1] - rung[0], v[-1] - v[0]])
    d /= np.linalg.norm(d)
    mid = np.array([0.5 * (rung[0] + rung[-1]), 0.5 * (v[0] + v[-1])])
    return {"width_mm": float(np.linalg.norm(pb - pa)), "depth_mm": float(-rec[i0:i1 + 1].min()),
            "centre_offset_mm": float((0.5 * (pa + pb) - mid) @ d)}


def bevel_dims(prof: dict, flats_mm: float = 0.10) -> dict | None:
    """V-bevel apex height above the corner-to-corner rung (along u) and the included angle between the two flank
    lines (fitted to the profile between 20% and 80% of the apex height on each side)."""
    v, e, rung = prof["v"], prof["e"], prof["rung"]
    h = e - rung
    k = int(np.argmax(h))
    hmax = float(h[k])
    if hmax <= 0:
        return None
    dirs = []
    for side in (np.arange(0, k), np.arange(k + 1, len(v))):
        m = side[(h[side] > 0.2 * hmax) & (h[side] < 0.8 * hmax)]
        if len(m) < 5:
            return None
        A = np.c_[e[m], v[m]]
        c = A.mean(0)
        _, _, vt = np.linalg.svd(A - c)
        dv_ = vt[0]
        far = A[np.argmax(np.abs(h[m] - hmax))] - np.array([e[k], v[k]])
        if dv_ @ far < 0:
            dv_ = -dv_
        dirs.append(dv_)
    ang = float(np.degrees(np.arccos(np.clip(dirs[0] @ dirs[1], -1, 1))))
    return {"h_mm": hmax, "angle_deg": ang, "apex_u": float(e[k]), "apex_v": float(v[k])}


# ------------------------------------------------------------------------------------------------ volumes
def _station_ds(F: dict) -> np.ndarray:
    P = F["Pm"]
    seg = np.linalg.norm(np.roll(P, -1, 0) - P, axis=1)
    return 0.5 * (seg + np.roll(seg, 1))


def pair_overlap_mm3(F: dict, scene_a, scene_b, window=(-1.5, 1.5, -1.6, 1.6), step: float = 0.01,
                     idx=None) -> float:
    """Volume where both solids are occupied, integrated over the station planes (u, n) around the mid-depth edge
    point: sum(area_k * ds_k). Coincident faces never count (a point is inside one solid or the other)."""
    u0, u1, v0, v1 = window
    us, vs = np.arange(u0, u1 + 1e-9, step), np.arange(v0, v1 + 1e-9, step)
    UU, VV = np.meshgrid(us, vs)
    ds = _station_ds(F)
    idx = range(len(F["s"])) if idx is None else idx
    tot = 0.0
    for k in idx:
        mid = F["Pm"][k] + 0.5 * (F["uf"][k] + F["ub"][k]) * F["u"][k] + 0.5 * (F["vf"][k] + F["vb"][k]) * F["n"][k]
        P = mid + UU.reshape(-1, 1) * F["u"][k] + VV.reshape(-1, 1) * F["n"][k]
        both = IG._occupancy(scene_a, P) & IG._occupancy(scene_b, P)
        tot += float(both.sum() * step * step * ds[k])
    return tot


def inflate(V, T, d: float) -> tuple[np.ndarray, np.ndarray]:
    """The groove cutter as WP4_DESIGN §5 defines it, rebuilt here: the closed mesh moved `d` along angle-weighted
    vertex normals (exact weld). Returns (V, T) of the welded, inflated mesh."""
    Vw, vid = weld_exact(V)
    Tw = vid[np.asarray(T, np.int64)]
    Tw = Tw[(Tw[:, 0] != Tw[:, 1]) & (Tw[:, 1] != Tw[:, 2]) & (Tw[:, 0] != Tw[:, 2])]
    if signed_volume(Vw, Tw) < 0:
        Tw = Tw[:, ::-1].copy()
    fn = np.cross(Vw[Tw[:, 1]] - Vw[Tw[:, 0]], Vw[Tw[:, 2]] - Vw[Tw[:, 0]])
    fn /= np.maximum(np.linalg.norm(fn, axis=1, keepdims=True), 1e-30)
    vn = np.zeros_like(Vw)
    for i in range(3):
        a, b, c = Vw[Tw[:, i]], Vw[Tw[:, (i + 1) % 3]], Vw[Tw[:, (i + 2) % 3]]
        e1, e2 = b - a, c - a
        cosang = (e1 * e2).sum(1) / np.maximum(np.linalg.norm(e1, axis=1) * np.linalg.norm(e2, axis=1), 1e-30)
        np.add.at(vn, Tw[:, i], fn * np.arccos(np.clip(cosang, -1, 1))[:, None])
    vn /= np.maximum(np.linalg.norm(vn, axis=1, keepdims=True), 1e-30)
    return Vw + d * vn, Tw


def _curvature(F: dict) -> np.ndarray:
    """Signed outline curvature per station along u (> 0 convex): -(dt/ds) . u from the neighbouring stations."""
    t, s, L = F["t"], F["s"], F["L"]
    dt = np.roll(t, -1, 0) - np.roll(t, 1, 0)
    ds = (np.roll(s, -1) - np.roll(s, 1)) % L
    return -(dt * F["u"]).sum(1) / np.maximum(ds, 1e-9)


def removed_estimate_mm3(F: dict, frame_before_scene, lens_V, lens_T, clearance: float,
                         window=(-1.0, 2.5, -1.6, 1.6), step: float = 0.01) -> float:
    """vol(frame_before ^ cutter), the cutter = the profiled lens inflated by `clearance` along angle-weighted vertex
    normals (§5), integrated over the station planes with the Pappus factor (1 + kappa u) of the outline's curvature
    (material outward of the station's reference point sweeps a longer arc)."""
    cV, cT = inflate(lens_V, lens_T, clearance)
    cut = IG.build_scene(cV, cT)
    u0, u1, v0, v1 = window
    us, vs = np.arange(u0, u1 + 1e-9, step), np.arange(v0, v1 + 1e-9, step)
    UU, VV = np.meshgrid(us, vs)
    ds = _station_ds(F)
    kap = _curvature(F)
    tot = 0.0
    for k in range(len(F["s"])):
        mid = F["Pm"][k] + 0.5 * (F["uf"][k] + F["ub"][k]) * F["u"][k] + 0.5 * (F["vf"][k] + F["vb"][k]) * F["n"][k]
        P = mid + UU.reshape(-1, 1) * F["u"][k] + VV.reshape(-1, 1) * F["n"][k]
        both = IG._occupancy(frame_before_scene, P) & IG._occupancy(cut, P)
        w = 1.0 + kap[k] * UU.reshape(-1)[both]
        tot += float(w.sum() * step * step * ds[k])
    return tot


# ---------------------------------------------------------------------------------------------------- arcs
def _arc_param(loop_P: np.ndarray, X: np.ndarray) -> np.ndarray:
    A, B = loop_P, np.roll(loop_P, -1, 0)
    seg = np.linalg.norm(B - A, axis=1)
    s0 = np.r_[0.0, np.cumsum(seg)][:-1]
    out = np.zeros(len(X))
    for c0 in range(0, len(X), 256):
        x = X[c0:c0 + 256]
        AB = B - A
        w = np.clip(((x[:, None, :] - A[None]) * AB[None]).sum(-1) / np.maximum((AB * AB).sum(1), 1e-30)[None], 0, 1)
        Q = A[None] + w[..., None] * AB[None]
        d = np.linalg.norm(x[:, None, :] - Q, axis=-1)
        j = d.argmin(1)
        out[c0:c0 + 256] = s0[j] + w[np.arange(len(x)), j] * seg[j]
    return out


def arc_extent(loop_P: np.ndarray, X: np.ndarray) -> tuple[float, float, float, float]:
    """(start, end, length, loop length) of the arc of the closed outline `loop_P` that the points X cover: the
    complement of the largest gap between their arc parameters (start -> end runs with the loop)."""
    seg = np.linalg.norm(np.roll(loop_P, -1, 0) - loop_P, axis=1)
    L = float(seg.sum())
    s = np.sort(_arc_param(loop_P, X))
    gaps = np.diff(np.r_[s, s[0] + L])
    j = int(np.argmax(gaps))
    start, end = s[(j + 1) % len(s)], s[j]
    return float(start), float(end), float((end - start) % L), L


def arc_overlaps(a: tuple, b: tuple) -> list[float]:
    """Lengths of the overlaps of two arcs (start, end, length, L) of one loop (0, 1 or 2 pieces)."""
    L = a[3]
    grid = np.arange(0.0, L, 0.005)

    def cover(x):
        st, _, ln, _ = x
        return ((grid - st) % L) <= ln
    both = cover(a) & cover(b)
    if not both.any():
        return []
    if both.all():
        return [L]
    k = int(np.flatnonzero(~both)[0])
    rolled = np.roll(both, -k)
    out, run = [], 0
    for x in rolled:
        if x:
            run += 1
        elif run:
            out.append(run * 0.005)
            run = 0
    if run:
        out.append(run * 0.005)
    return out


# ---------------------------------------------------------------------------------------------- GLB bytes
def accessor_bytes(path, node_name: str) -> list:
    """For every primitive of the named node: [(semantic, bytes)] of its attribute and index accessors."""
    data = Path(path).read_bytes()
    doc, binc = GL.split_chunks(data)
    out = []
    for node in doc.get("nodes", []):
        if node.get("name") != node_name or "mesh" not in node:
            continue
        mesh = doc["meshes"][node["mesh"]]
        for prim in mesh["primitives"]:
            items = sorted(prim.get("attributes", {}).items())
            if "indices" in prim:
                items.append(("INDICES", prim["indices"]))
            for sem, ai in items:
                acc = doc["accessors"][ai]
                bv = doc["bufferViews"][acc["bufferView"]]
                comp = {5120: 1, 5121: 1, 5122: 2, 5123: 2, 5125: 4, 5126: 4}[acc["componentType"]]
                ncomp = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}[acc["type"]]
                size = comp * ncomp
                stride = bv.get("byteStride", size)
                off = bv.get("byteOffset", 0) + acc.get("byteOffset", 0)
                chunk = b"".join(binc[off + i * stride: off + i * stride + size] for i in range(acc["count"]))
                out.append((sem, chunk))
    return out
