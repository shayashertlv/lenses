"""Independent seating instrument (TESTS ONLY): a port of the WP4 designers' open3d occupancy-grid `seating.py` and
the lens structure it relied on (`lens_struct.py`), scratch `g3d-wp4-design/`, 2026-10-05.

It shares NO code with glasses3d/g3d/seat (WP4_DESIGN §10: every proof is graded by an instrument the tool does not
share). Different on purpose:
- lens structure: 1e-4 mm rounded weld, dihedral segmentation without the 75 deg guard, rungs = nearest back-rim
  vertex through a wall triangle, vertex normals of the front cap, stations every 1 mm interpolated between rim
  vertices, u flipped when Pm - 0.4 u is not inside the lens (open3d occupancy);
- seating: a 0.05 mm occupancy grid of every frame part (open3d `compute_occupancy`, 3-ray majority) in the station
  plane (u, v = n): capture(v) = e(v) - (first occupied u at that row), e(v) = the lens edge on the rung line.

Additions to the port (same occupancy grid, more output):
- `analyse(..., fractions=...)` evaluates occupancy rows exactly at the edge depths of given rung fractions (the
  grid rows are quantised to 0.05 mm in v) and returns the station frames, so the core can be measured at exactly
  the instrument's stations (P0);
- on those rows it also measures capture the way WP4_DESIGN §3 defines it (`cap_graded`), from occupancy only:
  the lens edge = where the row leaves the LENS (its occupied run containing the rung point, else the last run
  ending within REACH before it), then the frame run containing that edge, else the first run starting within
  REACH beyond it. Every run boundary is refined by bisection on occupancy to 5e-5 mm (a 0.05 mm grid alone
  cannot decide whether an edge 0.01 mm short of a frame face is inside it: Oakley's channel ends 0.004-0.038 mm
  short of the lens edge at 13 back-depth rows); runs thinner than the 0.05 mm sampling step can still be missed
  (Oakley: one 0.021 mm sliver of a latch housing). The port's own capture
  (`cap_fractions`, the designers' definition: the first frame material from u = -4 mm, edge on the rung) differs
  from §3 where frame material lies inside the lens body without reaching its edge;
- `eyewire_band`: the designers' `ft_eyewire_hidden.py` (0.02 mm grid of brow and eyewire per upper station; the
  eyewire visible along +n / -n where no brow lies in front of / behind it; section area outside the brow).

Units: world mm. Inputs: lens (V, T) and parts [(name, V, T)], closed triangle meshes.
"""
from __future__ import annotations

from collections import defaultdict

import numpy as np
import open3d as o3d

U0, U1, DU = -4.0, 6.0, 0.05
VPAD, DV = 5.0, 0.05
NLEV = 9                      # the original's 9 edge depths (back corner .. front corner)
REACH = 3.0                   # WP4_DESIGN §7 REACH (restated here: the instrument imports nothing from the core)


# ---------------------------------------------------------------------------------------------- lens structure
def weld(V, tol=1e-4):
    key = np.round(V / tol).astype(np.int64)
    _, inv = np.unique(key, axis=0, return_inverse=True)
    return np.asarray(inv).ravel()


def _tri_normals(V, T):
    fn = np.cross(V[T[:, 1]] - V[T[:, 0]], V[T[:, 2]] - V[T[:, 0]])
    ar = np.linalg.norm(fn, axis=1)
    return fn / np.maximum(ar[:, None], 1e-30), ar / 2


def _adjacency(T):
    """Face pairs sharing an edge (manifold edges only) and those edges."""
    e = np.concatenate([T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]])
    fid = np.tile(np.arange(len(T)), 3)
    es = np.sort(e, 1)
    key = es[:, 0] * (T.max() + 1) + es[:, 1]
    order = np.argsort(key, kind="stable")
    ks, fo, eo = key[order], fid[order], es[order]
    starts = np.flatnonzero(np.r_[True, ks[1:] != ks[:-1]])
    cnt = np.diff(np.r_[starts, len(ks)])
    two = starts[cnt == 2]
    return np.stack([fo[two], fo[two + 1]], 1), eo[two]


def _chain(edges):
    adj = defaultdict(list)
    for u, v in edges:
        adj[u].append(v)
        adj[v].append(u)
    seen = set()
    loops = []
    for u0, v0 in edges:
        k = (min(u0, v0), max(u0, v0))
        if k in seen:
            continue
        loop = [u0, v0]
        seen.add(k)
        cur = v0
        while True:
            nxt = [w for w in adj[cur] if (min(cur, w), max(cur, w)) not in seen]
            if not nxt:
                break
            w = nxt[0]
            seen.add((min(cur, w), max(cur, w)))
            if w == u0:
                break
            loop.append(w)
            cur = w
        loops.append(loop)
    return loops


def lens_structure(V, T) -> dict:
    """Welded lens, front/back/wall triangle classes, longest front/back rim loops, front-cap vertex normals."""
    V = np.asarray(V, float)
    T = np.asarray(T, np.int64)
    vid = weld(V)
    nW = int(vid.max()) + 1
    Vw = np.zeros((nW, 3))
    Vw[vid] = V
    Tw = vid[T]
    Tw = Tw[(Tw[:, 0] != Tw[:, 1]) & (Tw[:, 1] != Tw[:, 2]) & (Tw[:, 0] != Tw[:, 2])]
    pn, parea = _tri_normals(Vw, Tw)
    pairs, edges = _adjacency(Tw)
    dots = np.einsum("ij,ij->i", pn[pairs[:, 0]], pn[pairs[:, 1]])
    best = None
    for th in (20, 30, 40, 50, 60):
        parent = np.arange(len(Tw))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x
        for (p, q), s in zip(pairs, dots > np.cos(np.radians(th))):
            if s:
                rp, rq = find(p), find(q)
                if rp != rq:
                    parent[rp] = rq
        lab = np.array([find(i) for i in range(len(Tw))])
        labs, inv = np.unique(lab, return_inverse=True)
        area = np.bincount(inv, weights=parea)
        big = np.argsort(-area)[:2]
        nz = [np.average(pn[inv == k], axis=0, weights=parea[inv == k]) for k in big]
        if len(big) == 2 and np.dot(nz[0], nz[1]) < -0.5:
            best = (th, inv, big, nz)
            break
    if best is None:
        raise ValueError("instrument: lens segmentation failed")
    th, inv, big, nz = best
    fi = 0 if nz[0][2] > nz[1][2] else 1
    front, back = inv == big[fi], inv == big[1 - fi]
    cls = np.where(front, 0, np.where(back, 1, 2))
    cp = cls[pairs]
    fr_e = ((cp[:, 0] == 0) & (cp[:, 1] == 2)) | ((cp[:, 0] == 2) & (cp[:, 1] == 0))
    bk_e = ((cp[:, 0] == 1) & (cp[:, 1] == 2)) | ((cp[:, 0] == 2) & (cp[:, 1] == 1))
    fl = max(_chain([tuple(e) for e in edges[fr_e]]), key=len)
    bl = max(_chain([tuple(e) for e in edges[bk_e]]), key=len)
    vn = np.zeros((nW, 3))
    tf = Tw[front]
    fn_t, ar_t = _tri_normals(Vw, tf)
    for k in range(3):
        np.add.at(vn, tf[:, k], fn_t * ar_t[:, None])
    vn /= np.maximum(np.linalg.norm(vn, axis=1, keepdims=True), 1e-30)
    return {"Vw": Vw, "Tw": Tw, "Tcls": cls, "fl": np.array(fl), "bl": np.array(bl), "front_vn": vn,
            "threshold": th, "cap_tris": (int(front.sum()), int(back.sum()))}


def build_scene(V, T):
    s = o3d.t.geometry.RaycastingScene()
    s.add_triangles(o3d.core.Tensor(np.asarray(V, np.float32)), o3d.core.Tensor(np.asarray(T, np.uint32)))
    return s


def rungs(lz):
    """front rim vertex -> back rim vertex through the wall triangles (nearest)."""
    Vw, Tw, Tcls, fl, bl = lz["Vw"], lz["Tw"], lz["Tcls"], lz["fl"], lz["bl"]
    fset, bset = set(fl.tolist()), set(bl.tolist())
    pairs = {}
    for t in Tw[Tcls == 2]:
        for i in range(3):
            a, b = t[i], t[(i + 1) % 3]
            for x, y in ((a, b), (b, a)):
                if x in fset and y in bset:
                    d = np.linalg.norm(Vw[x] - Vw[y])
                    if x not in pairs or d < pairs[x][1]:
                        pairs[x] = (y, d)
    return {k: v[0] for k, v in pairs.items()}


def stations(lz, spacing=1.0):
    Vw, fl = lz["Vw"], lz["fl"]
    rg = rungs(lz)
    FL = Vw[fl]
    BL = np.array([Vw[rg[v]] if v in rg else Vw[v] for v in fl])
    N = lz["front_vn"][fl]
    seg = np.linalg.norm(np.roll(FL, -1, 0) - FL, axis=1)
    s = np.r_[0, np.cumsum(seg)]
    L = s[-1]
    ss = np.arange(0, L, spacing)
    FLc, BLc, Nc = np.vstack([FL, FL[:1]]), np.vstack([BL, BL[:1]]), np.vstack([N, N[:1]])

    def interp(A):
        return np.stack([np.interp(ss, s, A[:, k]) for k in range(3)], 1)
    Pf, Pb, n = interp(FLc), interp(BLc), interp(Nc)
    n /= np.linalg.norm(n, axis=1, keepdims=True)
    nxt = np.stack([np.interp((ss + 0.25) % L, s, FLc[:, k]) for k in range(3)], 1)
    prv = np.stack([np.interp((ss - 0.25) % L, s, FLc[:, k]) for k in range(3)], 1)
    t = nxt - prv
    t -= (t * n).sum(1, keepdims=True) * n
    t /= np.linalg.norm(t, axis=1, keepdims=True)
    return ss, Pf, Pb, n, t, np.cross(t, n), L


# ------------------------------------------------------------------------------------------------------ seating
def _occupancy(scene, pts):
    return scene.compute_occupancy(o3d.core.Tensor(np.asarray(pts, np.float32)), nsamples=3).numpy() > 0.5


def analyse(lens_V, lens_T, parts, spacing=1.0, fractions=None, full_grid=True):
    """Per-station seating rows. `fractions`: rung fractions from the FRONT corner at which capture is also
    evaluated on exact rows (the core's depths); `full_grid=False` skips the full (u, v) grid (cover/gap metrics)."""
    lz = lens_structure(lens_V, lens_T)
    ss, Pf, Pb, n, t, uo, L = stations(lz, spacing)
    Pm = 0.5 * (Pf + Pb)
    lens_scene = build_scene(lz["Vw"], lz["Tw"])
    inside = _occupancy(lens_scene, Pm - 0.4 * uo)
    flips = int((~inside).sum())
    uo[~inside] *= -1
    t[~inside] *= -1
    scenes = [build_scene(V, T) for _, V, T in parts]
    uf = ((Pf - Pm) * uo).sum(1)
    vf = ((Pf - Pm) * n).sum(1)
    ub = ((Pb - Pm) * uo).sum(1)
    vb = ((Pb - Pm) * n).sum(1)
    us = np.arange(U0, U1 + 1e-9, DU)
    nS, nP = len(ss), len(parts)
    rows = [dict(s=round(float(ss[k]), 2), xyz=Pm[k].tolist()) for k in range(nS)]
    out = {"perimeter": float(L), "stations": nS, "u_flipped": flips, "threshold": lz["threshold"],
           "cap_tris": lz["cap_tris"], "parts": [p[0] for p in parts],
           "frames": {"Pf": Pm + uf[:, None] * uo + vf[:, None] * n, "Pb": Pm + ub[:, None] * uo + vb[:, None] * n,
                      "n": n, "t": t, "u": uo, "s": ss, "Pm": Pm, "uf": uf, "ub": ub}}
    if fractions is not None:
        fr = np.asarray(fractions, float)
        vv = vf[:, None] + fr[None, :] * (vb - vf)[:, None]          # (nS, nf)
        ee = uf[:, None] + fr[None, :] * (ub - uf)[:, None]
        pts = (Pm[:, None, None, :] + us[None, None, :, None] * uo[:, None, None, :]
               + vv[:, :, None, None] * n[:, None, None, :])          # (nS, nf, nu, 3)
        flat = pts.reshape(-1, 3)
        uap_all = np.full((nP, nS, len(fr)), np.nan)
        base = (Pm[:, None, :] + vv[:, :, None] * n[:, None, :]).reshape(-1, 3)    # row k * nf + j
        dirs = np.repeat(uo, len(fr), axis=0)
        part_runs = []
        for j, scn in enumerate(scenes):
            occ = _occupancy(scn, flat).reshape(nS, len(fr), len(us))
            first = occ.argmax(2)
            has = occ.any(2)
            uap = np.where(first > 0, us[first], -99.0)
            uap_all[j] = np.where(has, uap, np.nan)
            part_runs.append(_refined_runs(scn, occ.reshape(-1, len(us)), base, dirs, us))
        uap_u = np.nanmin(np.where(np.isfinite(uap_all), uap_all, np.inf), 0)
        uap_u[~np.isfinite(uap_u)] = np.nan
        out["cap_fractions"] = ee - uap_u                              # (nS, nf); >= 90 means 'beyond the grid'
        out["fractions"] = fr.tolist()
        lens_runs = _refined_runs(lens_scene, _occupancy(lens_scene, flat).reshape(-1, len(us)), base, dirs, us)
        cg = np.full(nS * len(fr), np.nan)
        eg = np.full(nS * len(fr), np.nan)
        for row in range(nS * len(fr)):
            eg[row], cg[row] = _graded(lens_runs[row], _union([pr[row] for pr in part_runs]),
                                       float(ee.reshape(-1)[row]))
        out["cap_graded"] = cg.reshape(nS, len(fr))                    # 99 = frame from beyond the grid
        out["edge_graded"] = eg.reshape(nS, len(fr))                   # lens edge along the row (u from Pm)
    if not full_grid:
        out["rows"] = rows
        return out
    vmax = float(np.max(vf)) + VPAD
    vmin = float(np.min(vb)) - VPAD
    vs = np.arange(vmin, vmax + 1e-9, DV)
    UU, VV = np.meshgrid(us, vs, indexing="xy")
    occ_parts = np.zeros((nP, nS, len(vs), len(us)), bool)
    for c0 in range(0, nS, 32):
        c1 = min(nS, c0 + 32)
        pts = (Pm[c0:c1, None, None, :] + UU[None, :, :, None] * uo[c0:c1, None, None, :]
               + VV[None, :, :, None] * n[c0:c1, None, None, :])
        flat = pts.reshape(-1, 3)
        for j, scn in enumerate(scenes):
            occ_parts[j, c0:c1] = _occupancy(scn, flat).reshape(c1 - c0, len(vs), len(us))
    occ = occ_parts.any(0)
    for k in range(nS):
        vv = np.linspace(vb[k], vf[k], NLEV)
        ee = ub[k] + (vv - vb[k]) * (uf[k] - ub[k]) / (vf[k] - vb[k])
        ivs = np.clip(np.round((vv - vmin) / DV).astype(int), 0, len(vs) - 1)
        uap_all = np.full((nP, NLEV), np.nan)
        for p in range(nP):
            O = occ_parts[p, k]
            for j, iv in enumerate(ivs):
                ius = np.where(O[iv])[0]
                if len(ius):
                    uap_all[p, j] = us[ius[0]] if ius[0] > 0 else -99.0
        uap_u = np.nanmin(np.where(np.isfinite(uap_all), uap_all, np.inf), 0)
        uap_u[~np.isfinite(uap_u)] = np.nan
        cap = ee - uap_u
        own = [int(np.nanargmin(np.where(np.isfinite(uap_all[:, j]), uap_all[:, j], np.inf)))
               if np.isfinite(uap_u[j]) else -1 for j in range(NLEV)]
        iu_f = int(np.clip(round((uf[k] - U0) / DU), 0, len(us) - 1))
        iu_b = int(np.clip(round((ub[k] - U0) / DU), 0, len(us) - 1))
        col_f, col_b = occ[k, :, iu_f], occ[k, :, iu_b]
        iv_f = int(round((vf[k] - vmin) / DV))
        iv_b = int(round((vb[k] - vmin) / DV))
        inside_rung = occ[k, ivs, np.clip(np.round((ee - U0) / DU).astype(int), 0, len(us) - 1)]
        sector = ("top" if uo[k, 1] > 0.707 else ("bottom" if uo[k, 1] < -0.707 else
                  ("nasal" if np.sign(uo[k, 0]) == -np.sign(Pm[k, 0]) else "temporal")))
        rows[k].update(sector=sector, cap=cap.tolist(), cap_front=_f(cap[-1]), cap_mid=_f(cap[4]),
                       cap_back=_f(cap[0]), cap_min=_f(np.min(cap)) if np.isfinite(cap).all() else None,
                       rung_inside_share=float(inside_rung.mean()),
                       part=parts[own[4]][0] if own[4] >= 0 else None,
                       cover_front=_f(_run_up(col_f, iv_f)), cover_back=_f(_run_down(col_b, iv_b)))
    out["rows"] = rows
    return out


REFINE_ITERS = 10            # bisection steps per run boundary: 0.05 mm / 2**10 = 5e-5 mm
TOUCH_MM = 1e-4              # runs of different parts closer than this are one run (touching parts)


def _refined_runs(scene, occ, base, dirs, us, iters=REFINE_ITERS):
    """Occupied runs per row (rows = lines base + u * dirs sampled at `us`, occupancy `occ` (R, nu)) as a list of
    (R) arrays (k, 2) [lo, hi]; every boundary between two samples of different occupancy is refined by bisection on
    occupancy; -inf / +inf where a run reaches the grid's end. A run that falls between two samples is missed (the
    instrument's resolution: features thinner than DU)."""
    occ = np.asarray(occ, bool)
    R, nu = occ.shape
    d = np.diff(occ.astype(np.int8), axis=1)
    r, i = np.nonzero(d)
    a, b = us[i].astype(float), us[i + 1].astype(float)
    sa = occ[r, i]
    for _ in range(iters):
        if not len(r):
            break
        m = 0.5 * (a + b)
        om = _occupancy(scene, base[r] + m[:, None] * dirs[r])
        same = om == sa
        a = np.where(same, m, a)
        b = np.where(same, b, m)
    ub = 0.5 * (a + b)
    rising = d[r, i] > 0
    out = []
    order = np.lexsort((ub, r))
    r, ub, rising = r[order], ub[order], rising[order]
    starts = np.searchsorted(r, np.arange(R), side="left")
    ends = np.searchsorted(r, np.arange(R), side="right")
    for row in range(R):
        lo = list(ub[starts[row]:ends[row]][rising[starts[row]:ends[row]]])
        hi = list(ub[starts[row]:ends[row]][~rising[starts[row]:ends[row]]])
        if occ[row, 0]:
            lo = [-np.inf] + lo
        if occ[row, -1]:
            hi = hi + [np.inf]
        out.append(np.c_[lo, hi] if lo else np.zeros((0, 2)))
    return out


def _union(runs):
    """Union of (k, 2) run lists, touching runs (gap <= TOUCH_MM) merged."""
    a = [x for x in runs if len(x)]
    if not a:
        return np.zeros((0, 2))
    a = np.concatenate(a)
    a = a[np.argsort(a[:, 0], kind="stable")]
    out = [a[0].copy()]
    for lo, hi in a[1:]:
        if lo <= out[-1][1] + TOUCH_MM:
            out[-1][1] = max(out[-1][1], hi)
        else:
            out.append(np.array([lo, hi]))
    return np.array(out)


def _graded(lens_runs, frame_runs, rung):
    """(lens edge, capture) on one row by WP4_DESIGN §3 (see the module docstring), from refined runs; capture 99 =
    the containing frame run starts before the grid, nan = no frame within REACH (or no edge)."""
    lo, hi = (lens_runs[:, 0], lens_runs[:, 1]) if len(lens_runs) else (np.zeros(0), np.zeros(0))
    inside = (lo <= rung) & (hi >= rung)
    before = (hi < rung) & (hi >= rung - REACH)
    if inside.any():
        e = float(hi[inside].max())
    elif before.any():
        e = float(hi[before].max())
    else:
        e = float(rung)
    if not np.isfinite(e):
        return np.nan, np.nan
    for lo_f, hi_f in frame_runs:
        if lo_f <= e <= hi_f:
            return e, (99.0 if not np.isfinite(lo_f) else e - lo_f)
        if lo_f > e:
            return e, (e - lo_f if lo_f - e <= REACH else np.nan)
    return e, np.nan


def eyewire_band(lens_V, lens_T, brow, eyewire, spacing=1.0, du=0.02, u_range=(-2.0, 4.0), v_range=(-4.0, 4.0),
                 min_up=0.5):
    """Port of the designers' ft_eyewire_hidden.py. Per upper station (u_y >= `min_up`) whose (u, v = n) grid holds
    both brow and eyewire material: eyewire section area, its area outside the brow, and the band widths along u
    where eyewire outside the brow has no brow strictly in front of it (+n, 'front') / behind it (-n, 'back').
    brow, eyewire: (V, T). Returns {"rows": [...], "frames": {...}} (frames as in `analyse`, for the core)."""
    lz = lens_structure(lens_V, lens_T)
    ss, Pf, Pb, n, t, uo, L = stations(lz, spacing)
    Pm = 0.5 * (Pf + Pb)
    ins = _occupancy(build_scene(lz["Vw"], lz["Tw"]), Pm - 0.4 * uo)
    uo[~ins] *= -1
    t[~ins] *= -1
    sb, se = build_scene(*brow), build_scene(*eyewire)
    us = np.arange(u_range[0], u_range[1] + 1e-9, du)
    vs = np.arange(v_range[0], v_range[1] + 1e-9, du)
    UU, VV = np.meshgrid(us, vs)
    rows = []
    for k in range(len(ss)):
        if uo[k, 1] < min_up:
            continue
        P = (Pm[k] + UU[..., None] * uo[k] + VV[..., None] * n[k]).reshape(-1, 3)
        ob = _occupancy(sb, P).reshape(UU.shape)
        if not ob.any():
            continue
        oe = _occupancy(se, P).reshape(UU.shape)
        if not oe.any():
            continue
        outside = oe & ~ob
        above = np.flip(np.cumsum(np.flip(ob, 0), 0), 0) - ob > 0          # brow strictly in front (rows: +v)
        below = np.cumsum(ob, 0) - ob > 0                                   # brow strictly behind
        rows.append({"k": int(k), "x": float(Pm[k, 0]), "area_mm2": float(oe.sum() * du * du),
                     "outside_mm2": float(outside.sum() * du * du),
                     "front_mm": float((outside & ~above).any(0).sum() * du),
                     "back_mm": float((outside & ~below).any(0).sum() * du)})
    uf = ((Pf - Pm) * uo).sum(1)
    vf = ((Pf - Pm) * n).sum(1)
    ub = ((Pb - Pm) * uo).sum(1)
    vb = ((Pb - Pm) * n).sum(1)
    return {"rows": rows, "perimeter": float(L),
            "frames": {"Pf": Pm + uf[:, None] * uo + vf[:, None] * n, "Pb": Pm + ub[:, None] * uo + vb[:, None] * n,
                       "n": n, "t": t, "u": uo, "s": ss}}


def _f(x):
    return None if x is None or not np.isfinite(x) else float(x)


def _run_up(col, i0):
    if not col[i0]:
        above = np.where(col[i0:])[0]
        return -(above[0] * DV) if len(above) else np.nan
    j = i0
    while j + 1 < len(col) and col[j + 1]:
        j += 1
    return (j - i0) * DV if j + 1 < len(col) else 99.0


def _run_down(col, i0):
    if not col[i0]:
        below = np.where(col[: i0 + 1][::-1])[0]
        return -(below[0] * DV) if len(below) else np.nan
    j = i0
    while j - 1 >= 0 and col[j - 1]:
        j -= 1
    return (i0 - j) * DV if j > 0 else 99.0
