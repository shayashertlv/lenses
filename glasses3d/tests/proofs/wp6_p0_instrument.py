"""WP6 P0 instrument (plan/WP6_DESIGN.md §8 P0): grades faceted temples in an exported GLB WITHOUT importing the tool
core (g3d/temple): everything is measured again from the file's own triangles and normals.

    grade_glb(glb, *, hinge_z, zone_end, outline=None, reference_glb=None, analytic_dihedrals=None) -> report

Per temple (role temple, reaching >= 60 mm back; side = sign of x):
- closed 2-manifold after welding positions (the GLB splits vertices at sharp edges), Euler 2, 0 open / duplicate
  edges, volume > 0;
- facets: region growing on the welded outer face (normals within 0.5 deg, area >= 4 mm^2): planarity <= 2e-3 mm;
  the GLB corner normals of every facet triangle within 0.1 deg of its face normal (with `facet_polys` = the spec's
  facet polygons in (d, y), only regions inside one are graded: the base and the arm are smooth surfaces);
- zone inner flatness (inner face between d 2 and zone_end - 1): one plane within 2e-3 mm (float32);
- dihedrals between facet regions (compared with `analytic_dihedrals` when given, +-0.01 deg);
- outline: every spec outline knot within 0.01 mm of a vertex in (d, y), every vertex inside the outline + 0.01 mm;
- sharp edges: an edge whose dihedral is >= 6 deg (or > 25 deg between strip faces) must have split normals; smooth
  edges bend <= 30 deg (arm normal spread);
- -X = mirror of +X (sorted vertex sets, float32 tolerance);
- decals (`reference_glb` = the GLB before the tool): every decal vertex moved <= 1.0 mm, lies within 0.05 mm (+ float32)
  of the temple surface it sits on, and on one facet region.
"""
from __future__ import annotations

import math

import numpy as np

from glasses3d.g3d_host import glb as glbmod

WELD_MM = 1e-3
FACET_DEG = 0.5
FACET_MIN_AREA = 4.0
PLANAR_GLB_MM = 2e-3
NORMAL_DEV_DEG = 0.1
SHARP_DEG = 6.0
KINK_DEG = 25.0
SPREAD_DEG = 30.0
OUTLINE_MM = 0.01
DECAL_MOVE_MM = 1.0
DECAL_LIFT_MM = 0.05
F32_MM = 2e-3


def _nodes(g):
    out = {}
    for p in g.primitives:
        if p.mode != 4 or len(p.triangles) == 0:
            continue
        node = g.nodes[p.node_index]
        role = (node.get("extras") or {}).get("partRole")
        rec = out.setdefault(p.node_index, {"name": p.node_name, "role": role, "W": [], "T": [], "N": [], "n": 0})
        rec["T"].append(p.triangles + rec["n"])
        rec["W"].append(p.positions * 1000.0)
        rec["N"].append(p.normals if p.normals is not None else np.full_like(p.positions, np.nan))
        rec["n"] += len(p.positions)
    for r in out.values():
        r["W"], r["T"], r["N"] = np.vstack(r["W"]), np.vstack(r["T"]), np.vstack(r["N"])
    return out


def temples(g) -> dict:
    best = {1: None, -1: None}
    for ni, r in _nodes(g).items():
        if r["role"] != "temple":
            continue
        W = r["W"]
        reach = float(W[:, 2].max() - W[:, 2].min())
        cx = float((W[:, 0].max() + W[:, 0].min()) / 2)
        if reach < 60.0 or abs(cx) < 20.0:
            continue
        s = 1 if cx > 0 else -1
        if best[s] is None or reach > best[s][0]:
            best[s] = (reach, r)
    return {s: b[1] for s, b in best.items() if b is not None}


def _weld(W, T):
    key = np.round(W / WELD_MM).astype(np.int64)
    _, inv = np.unique(key, axis=0, return_inverse=True)
    inv = inv.reshape(-1)
    Vw = np.zeros((inv.max() + 1, 3))
    Vw[inv] = W
    return Vw, inv[T]


def _tri_normals(V, T):
    a, b, c = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
    n = np.cross(b - a, c - a)
    ar = 0.5 * np.linalg.norm(n, axis=1)
    return n / np.maximum(2 * ar[:, None], 1e-300), ar


def _chart(W, side, hz):
    return np.stack([hz - W[:, 2], W[:, 1], side * W[:, 0]], axis=1)


def _inside(pt, P) -> bool:
    x, y = pt
    inside = False
    for i in range(len(P)):
        (x0, y0), (x1, y1) = P[i], P[(i + 1) % len(P)]
        if (y0 > y) != (y1 > y) and x0 + (y - y0) * (x1 - x0) / (y1 - y0) > x:
            inside = not inside
    return inside


def grade_temple(rec, side, hinge_z, zone_end, outline=None, analytic_dihedrals=None, facet_polys=None) -> dict:
    W, T, N = rec["W"], rec["T"], rec["N"]
    Vw, Tw = _weld(W, T)
    rep = {"node": rec["name"], "checks": {}}
    E = {}
    for t in Tw:
        for a, b in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0])):
            E[(int(a), int(b))] = E.get((int(a), int(b)), 0) + 1
    open_ = sum(1 for (a, b) in E if (b, a) not in E)
    dup = sum(1 for c in E.values() if c > 1)
    und = {(min(a, b), max(a, b)) for a, b in E}
    used = np.unique(Tw)
    chi = len(used) - len(und) + len(Tw)
    a, b, c = Vw[Tw[:, 0]], Vw[Tw[:, 1]], Vw[Tw[:, 2]]
    vol = float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6.0)
    rep["checks"]["closed_manifold"] = {"pass": open_ == 0 and dup == 0, "open": open_, "duplicate": dup}
    rep["checks"]["euler_2"] = {"pass": chi == 2, "euler": int(chi)}
    rep["checks"]["volume_positive"] = {"pass": vol > 0, "volume_mm3": vol}
    L = _chart(Vw, side, hinge_z)
    n, ar = _tri_normals(Vw, Tw)
    nl = np.stack([-n[:, 2], n[:, 1], side * n[:, 0]], axis=1)          # normals in (d, y, X)
    cen = L[Tw].mean(axis=1)
    outer = (nl[:, 2] > 0.6) & (cen[:, 0] <= zone_end) & (ar > 0)
    # region growing on the welded outer face
    edge_tris = {}
    for ti in np.nonzero(outer)[0]:
        t = Tw[ti]
        for e in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0])):
            edge_tris.setdefault((min(e), max(e)), []).append(int(ti))
    nbr = {int(t): set() for t in np.nonzero(outer)[0]}
    for ts in edge_tris.values():
        for t in ts:
            nbr[t].update(x for x in ts if x != t)
    cosmax = math.cos(math.radians(FACET_DEG))
    region = {}
    regions = []
    for seed in sorted(nbr, key=lambda t: (-ar[t], t)):
        if seed in region:
            continue
        rid = len(regions)
        stack, mem = [seed], []
        region[seed] = rid
        while stack:
            t = stack.pop()
            mem.append(t)
            for u in nbr[t]:
                if u not in region and float(n[u] @ n[seed]) >= cosmax:
                    region[u] = rid
                    stack.append(u)
        regions.append(mem)
    facets = []
    worst_plan, worst_nrm = 0.0, 0.0
    for rid, mem in enumerate(regions):
        A = float(ar[mem].sum())
        if A < FACET_MIN_AREA:
            continue
        nm = (n[mem] * ar[mem, None]).sum(axis=0)
        nm /= np.linalg.norm(nm)
        P = Vw[np.unique(Tw[mem])]
        cc = float(np.mean(P @ nm))
        plan = float(np.max(np.abs(P @ nm - cc)))
        # GLB corner normals (raw, unwelded) vs the face normal, on every triangle whose (d, y) centroid lies in a
        # spec facet polygon (all of the region's triangles when no polygons are given)
        dev = 0.0
        labels = set()
        for t in mem:
            lab_t = None
            if facet_polys is not None:
                c2 = L[Tw[t]].mean(axis=0)[:2]
                lab_t = next((lab for lab, P2 in facet_polys.items() if _inside(c2, P2)), None)
                if lab_t is None:
                    continue
                labels.add(lab_t)
            cn = N[T[t]]
            if np.all(np.isfinite(cn)):
                cs = np.clip(cn @ n[t] / np.maximum(np.linalg.norm(cn, axis=1), 1e-30), -1, 1)
                dev = max(dev, float(np.degrees(np.arccos(cs)).max()))
        label = sorted(labels)[0] if labels else None
        is_facet = facet_polys is None or bool(labels)
        worst_plan = max(worst_plan, plan)
        if is_facet:
            worst_nrm = max(worst_nrm, dev)
        facets.append({"id": rid, "area_mm2": A, "normal": nm.tolist(), "planarity_mm": plan, "normal_dev_deg": dev,
                       "spec_facet": label, "graded_normals": bool(is_facet)})
    rep["facets"] = facets
    rep["checks"]["facet_planarity"] = {"pass": worst_plan <= PLANAR_GLB_MM, "max_mm": worst_plan,
                                        "facets": len(facets)}
    rep["checks"]["facet_corner_normals"] = {"pass": worst_nrm <= NORMAL_DEV_DEG, "max_deg": worst_nrm}
    inner = (nl[:, 2] < -0.6) & (cen[:, 0] >= 2.0) & (cen[:, 0] <= zone_end - 1.0)
    if inner.any():
        P = Vw[np.unique(Tw[inner])]
        ctr = P.mean(0)
        nn = np.linalg.svd(P - ctr)[2][2]
        flat = float(np.max(np.abs((P - ctr) @ nn)))
    else:
        flat = float("nan")
    rep["checks"]["inner_flat_zone"] = {"pass": bool(np.isfinite(flat) and flat <= PLANAR_GLB_MM), "max_mm": flat}
    # dihedrals between big facet regions
    ids = {f["id"]: f for f in facets}
    dih = {}
    for e, ts in edge_tris.items():
        rs = sorted({region[t] for t in ts if region.get(t) in ids})
        if len(rs) == 2:
            n1, n2 = np.array(ids[rs[0]]["normal"]), np.array(ids[rs[1]]["normal"])
            dih[tuple(rs)] = float(np.degrees(np.arccos(np.clip(n1 @ n2, -1, 1))))
    rep["dihedrals_deg"] = sorted(dih.values())
    if analytic_dihedrals is not None:
        got = sorted(dih.values())
        want = sorted(analytic_dihedrals)
        ok = len(got) >= len(want) and all(any(abs(g - w) <= 0.01 for g in got) for w in want)
        rep["checks"]["dihedral_analytic"] = {"pass": ok, "got": got, "want": want}
    # outline
    if outline is not None:
        top, bot = np.asarray(outline["top"], float), np.asarray(outline["bottom"], float)
        miss = 0.0
        for ch in (top, bot):
            for d, y in ch:
                miss = max(miss, float(np.min(np.hypot(L[:, 0] - d, L[:, 1] - y))))
        over = float(np.max(np.r_[L[:, 1] - np.interp(L[:, 0], top[:, 0], top[:, 1]),
                                  np.interp(L[:, 0], bot[:, 0], bot[:, 1]) - L[:, 1]]))
        rep["checks"]["outline_reproduced"] = {"pass": miss <= OUTLINE_MM and over <= OUTLINE_MM,
                                               "knot_miss_mm": miss, "overshoot_mm": over}
    # sharp edges: split normals where the dihedral is large; smooth edges bend <= 30 deg
    na, _ = _tri_normals(Vw, Tw)
    corner_of = {}
    for ti, t in enumerate(T):
        for k in range(3):
            corner_of[(ti, int(Tw[ti, k]))] = N[t[k]]
    E2T = {}
    for ti, t in enumerate(Tw):
        for a_, b_ in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0])):
            E2T.setdefault((min(a_, b_), max(a_, b_)), []).append(ti)
    unsplit_sharp, spread = 0, 0.0
    for (a_, b_), ts in E2T.items():
        if len(ts) != 2:
            continue
        t1, t2 = ts
        d_ = float(np.degrees(np.arccos(np.clip(na[t1] @ na[t2], -1, 1))))
        # split = the corner normals jump across the edge at either end (a hard edge in the GLB; a crease that dies
        # into a smooth patch can only split at one end); on a curved patch the smooth side's corner normals are
        # fan averages, so the jump need not equal the dihedral
        jumps = []
        for v_ in (a_, b_):
            n1a, n2a = corner_of.get((t1, v_)), corner_of.get((t2, v_))
            if n1a is None or n2a is None or not (np.all(np.isfinite(n1a)) and np.all(np.isfinite(n2a))):
                continue
            jumps.append(float(np.degrees(np.arccos(np.clip(n1a @ n2a / max(np.linalg.norm(n1a) * np.linalg.norm(n2a),
                                                                             1e-30), -1, 1)))))
        if not jumps:
            continue
        split = max(jumps) > 0.5
        if d_ > KINK_DEG and not split:
            unsplit_sharp += 1
        if not split:
            spread = max(spread, d_)
    rep["checks"]["sharp_edges_split"] = {"pass": unsplit_sharp == 0, "unsplit_edges_over_25deg": unsplit_sharp}
    rep["checks"]["smooth_spread"] = {"pass": spread <= SPREAD_DEG, "max_deg": spread}
    rep["_Vw"], rep["_regions"], rep["_region_of"], rep["_Tw"] = Vw, regions, region, Tw
    return rep


def grade_glb(glb_path, *, hinge_z: float, zone_end: float, outline: dict | None = None,
              reference_glb=None, analytic_dihedrals=None, facet_polys: dict | None = None) -> dict:
    g = glbmod.load(glb_path)
    tp = temples(g)
    out = {"glb": str(glb_path), "temples": {}, "checks": {}}
    for s, rec in tp.items():
        out["temples"]["+x" if s > 0 else "-x"] = grade_temple(rec, s, hinge_z, zone_end, outline, analytic_dihedrals,
                                                               facet_polys)
    if set(tp) == {1, -1}:
        from scipy.spatial import cKDTree
        A = _weld(tp[1]["W"], tp[1]["T"])[0]
        B = _weld(tp[-1]["W"], tp[-1]["T"])[0] * np.array([-1.0, 1.0, 1.0])
        if A.shape != B.shape:
            out["checks"]["mirror_exact"] = {"pass": False, "vertices": [len(A), len(B)]}
        else:
            d1 = cKDTree(B).query(A)[0]
            d2 = cKDTree(A).query(B)[0]
            worst = float(max(d1.max(), d2.max()))
            out["checks"]["mirror_exact"] = {"pass": worst <= F32_MM, "max_mm": worst}
    if reference_glb is not None:
        out["checks"]["decals"] = _decals(g, glbmod.load(reference_glb), out)
    for s, t in out["temples"].items():
        for k in [k for k in t if k.startswith("_")]:
            del t[k]
    allc = [c["pass"] for t in out["temples"].values() for c in t["checks"].values()] + \
           [c["pass"] for c in out["checks"].values()]
    out["pass"] = bool(all(allc)) and len(tp) == 2
    return out


def _decals(g, gref, out) -> dict:
    """Decal nodes = thin non-temple-body parts within 1.5 mm of a temple, matched by name to the reference GLB."""
    new, ref = _nodes(g), _nodes(gref)
    by_name_ref = {r["name"]: r for r in ref.values()}
    res = {"decals": [], "pass": True}
    for ni, r in new.items():
        if r["role"] != "temple":
            continue
        W = r["W"]
        ext = W.max(0) - W.min(0)
        if float(ext.min()) > 0.3 and float(W[:, 2].max() - W[:, 2].min()) > 60:
            continue                                                   # a temple body
        rr = by_name_ref.get(r["name"])
        if rr is None or rr["W"].shape != W.shape:
            continue
        move = float(np.max(np.linalg.norm(W - rr["W"], axis=1)))
        if move == 0.0:
            continue
        side = "+x" if W[:, 0].mean() > 0 else "-x"
        t = out["temples"].get(side)
        if t is None:
            continue
        Vw, Tw, regions, region_of = t["_Vw"], t["_Tw"], t["_regions"], t["_region_of"]
        big = {f["id"]: f for f in t["facets"]}
        dist, regs = [], set()
        for p in W:
            best = None
            for fid, f in big.items():
                nrm = np.array(f["normal"])
                P = Vw[np.unique(Tw[regions[fid]])]
                c = float(np.mean(P @ nrm))
                d = float(p @ nrm - c)
                lo, hi = P.min(0) - 1.0, P.max(0) + 1.0
                if np.all(p >= lo) and np.all(p <= hi) and (best is None or abs(d) < abs(best[1])):
                    best = (fid, d)
            if best is not None:
                dist.append(best[1])
                regs.add(best[0])
        ok = move <= DECAL_MOVE_MM and dist and max(abs(d) for d in dist) <= DECAL_LIFT_MM + F32_MM and len(regs) == 1
        res["decals"].append({"name": r["name"], "max_move_mm": move,
                              "max_plane_distance_mm": max(abs(d) for d in dist) if dist else None,
                              "regions": sorted(regs), "pass": bool(ok)})
        res["pass"] &= bool(ok)
    return res
