"""WP5 step 3a: gains (WP5_DESIGN §6.3), single-source mode.

Every photo has its own exposure and white balance; a region is painted from its anchor alone, so neighbouring
regions painted from different photos must be brought to one reference. Per pair of label views (a, b):
- **same-surface overlaps** (``overlap``): texels both views see as donors (ok in both: cos >= 0.5, past the whole
  scene, valid acetate pixel, not clipped) and neither sees as specular, >= ``MIN_OVERLAP`` pairs;
- **across a region boundary** (``boundary``), where no overlap edge exists: the texels painted by a and by b within
  ``BOUNDARY_MM`` (3D distance) of the other label, unpaired, >= 200 on each side.
Each edge's gain is the per-channel ratio of MEDIANS in linear light (b -> a), clipped to [0.5, 2]: an exposure and
white-balance correction only. **Deviation from §6.3 (measured, FT1119):** ``quantile_gain`` (median-anchored gain +
free offset >= 0 fitted to the 10-90 % quantiles) turned spread differences between photos (sheen and view-dependent
highlights) into contrast changes: 01 -> 06 on 15,233 same-surface pairs whose medians agree within 3 levels gave
R gain 0.38 with a lifted black (offset 0.025 linear), and chains of such edges greyed the havana (an atlas of grey-
purple print). The print's contrast must stay the photo's (B3 energy), so the gain is median-only.
The per-view gains are a weighted least-squares solution of the edges' log gains (per channel, the reference fixed at
1; weights = pairs, boundary edges at half weight), so no single long chain decides a view. Reference = the front view
(view kind 'front') when it paints, else the view painting the most texels. Gains run before the highlight test (§6.3:
it needs gained colours).

**Fix round 2 (support and saturation are reported, not silent):** ``solve_gains`` gives each view its SUPPORT (the
accepted edges it rests on, their kinds and pairs; ``thin`` when it rests on a single edge of fewer than
``THIN_SUPPORT_PAIRS`` pairs, i.e. just above ``MIN_OVERLAP``); ``group_gains`` lists every group whose gain sits at
the [0.67, 1.5] clip in any channel (``saturated``); ``support_warnings`` turns both into report warnings. Measured on
FT1119 (fix round 1 bake): 03's stage-1 gain [1.18, 1.32, 1.74] rested on one 03-05 edge of 1,078 pairs (its other
edges were rejected), and the stage-2 gains of every temple group and of region 13 sat at the 1.5 clip, so the -x
temple's outer face carried a net linear gain of about [1.77, 1.97, 2.22] and rendered L* 27.3 against the photo's
20.6 at 03. ``clip_evidence`` reports the spread of the accepted edges' gains (what the clips should be judged
against: the measured exposure differences between real photos of the same surface).
"""
from __future__ import annotations

import numpy as np
from scipy.spatial import cKDTree

from .. import texture_utils as T
from . import signals as S

BOUNDARY_MM = 2.0
MIN_OVERLAP = 1000
MIN_SIDE = 200
GAIN_CLIP = (0.5, 2.0)
BOUNDARY_WEIGHT = 0.5
THIN_SUPPORT_PAIRS = 2 * MIN_OVERLAP


def specular(col: np.ndarray) -> np.ndarray:
    f = np.asarray(col, float)
    return (f.mean(-1) > S.SPECULAR_LUM) & (f.max(-1) - f.min(-1) < S.SPECULAR_SAT)


def median_gain(src: np.ndarray, dst: np.ndarray) -> np.ndarray | None:
    """Per-channel linear-light median ratio dst / src, or None when a channel leaves ``GAIN_CLIP``."""
    ls = np.median(T.srgb_to_linear(src).reshape(-1, 3), 0)
    ld = np.median(T.srgb_to_linear(dst).reshape(-1, 3), 0)
    if np.any(ls <= 1e-6):
        return None
    g = ld / ls
    if np.any(g < GAIN_CLIP[0]) or np.any(g > GAIN_CLIP[1]):
        return None
    return g


def pair_gains(tex, samples: dict, views: list[str], label: np.ndarray, *, boundary_mm: float = BOUNDARY_MM) -> list[dict]:
    """Gain edges between label views: [{a, b, kind, pairs, gain}] (a ~ gain x b, linear light); rejected edges are
    listed with 'rejected'."""
    used = [i for i in range(len(views)) if (label == i).any()]
    edges = []
    for ia in used:
        for ib in used:
            if ib <= ia:
                continue
            sa, sb = samples[views[ia]], samples[views[ib]]
            both = sa.ok & sb.ok & ~specular(sa.col) & ~specular(sb.col)
            n = int(both.sum())
            if n >= MIN_OVERLAP:
                g = median_gain(sb.col[both], sa.col[both])
                rec = {"a": views[ia], "b": views[ib], "kind": "overlap", "pairs": n}
                if g is None:
                    edges.append({**rec, "rejected": "gain outside [0.5, 2]"})
                else:
                    edges.append({**rec, "gain": [float(x) for x in g]})
                continue
            la, lb = label == ia, label == ib
            if la.sum() < MIN_SIDE or lb.sum() < MIN_SIDE:
                continue
            da, _ = cKDTree(tex.P[lb]).query(tex.P[la], k=1, distance_upper_bound=boundary_mm)
            db, _ = cKDTree(tex.P[la]).query(tex.P[lb], k=1, distance_upper_bound=boundary_mm)
            side_a = np.nonzero(la)[0][np.isfinite(da)]
            side_b = np.nonzero(lb)[0][np.isfinite(db)]
            ca = sa.col[side_a][~specular(sa.col[side_a])]
            cb = sb.col[side_b][~specular(sb.col[side_b])]
            if len(ca) >= MIN_SIDE and len(cb) >= MIN_SIDE:
                g = median_gain(cb, ca)
                rec = {"a": views[ia], "b": views[ib], "kind": "boundary", "pairs": int(min(len(ca), len(cb)))}
                edges.append({**rec, "rejected": "gain outside [0.5, 2]"} if g is None
                             else {**rec, "gain": [float(x) for x in g]})
    return edges


def reference_view(job_views: dict, views: list[str], label: np.ndarray) -> str | None:
    counts = {v: int((label == i).sum()) for i, v in enumerate(views)}
    used = [v for v in views if counts[v] > 0]
    if not used:
        return None
    front = [v for v in used if getattr(job_views.get(v), "kind", "") == "front"]
    if front:
        return max(front, key=lambda v: counts[v])
    return max(used, key=lambda v: (counts[v], -views.index(v)))


def solve_gains(views: list[str], label: np.ndarray, edges: list[dict], ref: str | None) -> dict:
    """{view: {'gain', 'reference', 'connected'}}: weighted least squares of log gains, the reference fixed."""
    used = [v for i, v in enumerate(views) if (label == i).any()]
    if ref is None or not used:
        return {}
    good = [e for e in edges if "gain" in e]
    # connectivity from the reference
    conn = {ref}
    changed = True
    while changed:
        changed = False
        for e in good:
            if (e["a"] in conn) != (e["b"] in conn):
                conn |= {e["a"], e["b"]}
                changed = True
    var = [v for v in used if v in conn and v != ref]
    col = {v: k for k, v in enumerate(var)}
    out = {ref: {"gain": [1.0, 1.0, 1.0], "reference": True, "connected": True}}
    if var:
        rows = [e for e in good if e["a"] in conn and e["b"] in conn]
        A = np.zeros((len(rows), len(var)))
        Y = np.zeros((len(rows), 3))
        w = np.zeros(len(rows))
        for k, e in enumerate(rows):
            # a ~ g b  ->  log G_b - log G_a = log g   (G maps a view onto the reference)
            if e["b"] in col:
                A[k, col[e["b"]]] += 1.0
            if e["a"] in col:
                A[k, col[e["a"]]] -= 1.0
            Y[k] = np.log(np.asarray(e["gain"], float))
            w[k] = np.sqrt(e["pairs"] * (1.0 if e["kind"] == "overlap" else BOUNDARY_WEIGHT))
        X, *_ = np.linalg.lstsq(A * w[:, None], Y * w[:, None], rcond=None)
        for v, k in col.items():
            g = np.clip(np.exp(X[k]), GAIN_CLIP[0], GAIN_CLIP[1])
            out[v] = {"gain": [float(x) for x in g], "reference": False, "connected": True}
    for v in used:
        out.setdefault(v, {"gain": [1.0, 1.0, 1.0], "reference": False, "connected": False})
    for v in out:
        sup = [e for e in good if v in (e["a"], e["b"]) and e["a"] in conn and e["b"] in conn]
        out[v]["support"] = {"edges": [f"{e['a'][-6:-4]}-{e['b'][-6:-4]} {e['kind']} {e['pairs']}" for e in sup],
                             "n_edges": len(sup), "pairs": int(sum(e["pairs"] for e in sup)),
                             "max_edge_pairs": int(max((e["pairs"] for e in sup), default=0)),
                             "rejected_edges": [f"{e['a'][-6:-4]}-{e['b'][-6:-4]} {e['kind']} {e['pairs']}"
                                                for e in edges if "gain" not in e and v in (e["a"], e["b"])]}
        out[v]["support"]["thin"] = bool(not out[v].get("reference") and out[v].get("connected")
                                         and len(sup) == 1 and sup[0]["pairs"] < THIN_SUPPORT_PAIRS)
        g_ = np.asarray(out[v]["gain"], float)
        out[v]["at_clip"] = bool(np.any(np.isclose(g_, GAIN_CLIP[0])) or np.any(np.isclose(g_, GAIN_CLIP[1])))
    return out


def clip_evidence(edges: list[dict]) -> dict:
    """Spread of the accepted edges' per-channel gains (overlap edges = same surface in two real photos): the measured
    exposure / white-balance differences the clips are judged against."""
    ov = np.array([e["gain"] for e in edges if "gain" in e and e["kind"] == "overlap"], float).reshape(-1, 3)
    bd = np.array([e["gain"] for e in edges if "gain" in e and e["kind"] == "boundary"], float).reshape(-1, 3)
    out = {"overlap_edges": int(len(ov)), "boundary_edges": int(len(bd)),
           "rejected_edges": int(sum(1 for e in edges if "gain" not in e)), "stage1_clip": list(GAIN_CLIP),
           "stage2_clip": list(GROUP_CLIP)}
    if len(ov):
        out["overlap_gain_min"] = [float(x) for x in ov.min(0)]
        out["overlap_gain_max"] = [float(x) for x in ov.max(0)]
        out["overlap_log_gain_abs_p90"] = float(np.percentile(np.abs(np.log(ov)), 90))
    return out


def support_warnings(chain: dict, grp: dict) -> list[str]:
    """Report warnings: views whose gain rests on one thin edge or is not connected, gains at the clip, and stage-2
    groups at the clip (fix round 2)."""
    w = []
    for v, r in chain.items():
        sup = r.get("support") or {}
        if sup.get("thin"):
            w.append(f"gains: {v[-6:-4]}'s gain {[round(x, 2) for x in r['gain']]} rests on ONE edge "
                     f"({sup['edges'][0]}; < {THIN_SUPPORT_PAIRS} pairs; rejected: {sup.get('rejected_edges')})")
        if not r.get("connected"):
            w.append(f"gains: {v[-6:-4]} is not connected to the reference (gain 1; rejected edges: "
                     f"{sup.get('rejected_edges')})")
        if r.get("at_clip"):
            w.append(f"gains: {v[-6:-4]}'s gain {[round(x, 2) for x in r['gain']]} sits at the clip {list(GAIN_CLIP)}")
    sat = [g for g in grp.get("groups", []) if g.get("saturated")]
    if sat:
        w.append(f"gains: {len(sat)} stage-2 groups sit at the clip {list(GROUP_CLIP)}: " + ", ".join(
            f"region {g['region']} x view {g['view_index']} {[round(x, 2) for x in g['gain']]}" for g in sat))
    return w


def apply(col: np.ndarray, g: dict) -> np.ndarray:
    """Gained sRGB float (linear-light gain; the identity returns the input exactly)."""
    gg = np.asarray(g["gain"], float)
    if np.allclose(gg, 1.0):
        return np.asarray(col, float)
    return np.clip(T.linear_to_srgb(T.srgb_to_linear(col) * gg), 0, 255)


# ----------------------------------------------------------------------------- stage 2: across region boundaries
GROUP_CLIP = (0.67, 1.5)
GROUP_PRIOR = 200.0
GROUP_BAND_MM = 2.0


def group_gains(tex, painted: np.ndarray, label: np.ndarray, col: np.ndarray, ref_label: int | None, *,
                band_mm: float = GROUP_BAND_MM) -> dict:
    """Gain matching ACROSS REGION BOUNDARIES (stage 2, on the stage-1 gained colours): one linear-light gain per
    painted group (region x painting view), so that the texels within ``band_mm`` of a neighbouring group with a
    different view match its median there. Weighted least squares of log gains per channel; groups painted by the
    reference view are fixed at 1 and every group is pulled weakly toward 1 (weight sqrt(200)); clipped to
    [0.67, 1.5]. Photos light each facing differently (the top of a brow is lit more than its front), and the
    single-source seams are hard: this is what keeps the colour step at a label seam small. Returns {'gain': (n, 3)
    per texel, 'groups': [...], 'edges': [...]}."""
    keys = np.where(painted, tex.region * 64 + np.maximum(label, 0), -1)
    groups = [int(k) for k in np.unique(keys[keys >= 0])]
    gi = {k: i for i, k in enumerate(groups)}
    edges = []
    pts = {k: np.nonzero(keys == k)[0] for k in groups}
    trees = {k: cKDTree(tex.P[pts[k]]) for k in groups}
    for i, ka in enumerate(groups):
        for kb in groups[i + 1:]:
            if (ka % 64) == (kb % 64):
                continue                                      # same view on both sides: no photo step to match
            da, _ = trees[kb].query(tex.P[pts[ka]], k=1, distance_upper_bound=band_mm)
            sa = pts[ka][np.isfinite(da)]
            if len(sa) < MIN_SIDE:
                continue
            db, _ = trees[ka].query(tex.P[pts[kb]], k=1, distance_upper_bound=band_mm)
            sb = pts[kb][np.isfinite(db)]
            if len(sb) < MIN_SIDE:
                continue
            ca, cb = col[sa][~specular(col[sa])], col[sb][~specular(col[sb])]
            if len(ca) < MIN_SIDE or len(cb) < MIN_SIDE:
                continue
            la = np.median(T.srgb_to_linear(ca), 0)
            lb = np.median(T.srgb_to_linear(cb), 0)
            if np.any(la <= 1e-6) or np.any(lb <= 1e-6):
                continue
            edges.append({"a": ka, "b": kb, "pairs": int(min(len(ca), len(cb))), "ratio": [float(x) for x in la / lb]})
    fixed = {k for k in groups if ref_label is not None and (k % 64) == ref_label}
    var = [k for k in groups if k not in fixed]
    vi = {k: j for j, k in enumerate(var)}
    g = np.ones((len(groups), 3))
    if var and edges:
        rows, Y, w = [], [], []
        for e in edges:
            r = np.zeros(len(var))
            if e["b"] in vi:
                r[vi[e["b"]]] += 1.0                          # a ~ ratio x b -> log G_b - log G_a = log ratio
            if e["a"] in vi:
                r[vi[e["a"]]] -= 1.0
            if not r.any():
                continue
            rows.append(r)
            Y.append(np.log(e["ratio"]))
            w.append(np.sqrt(e["pairs"]))
        for k in var:
            r = np.zeros(len(var))
            r[vi[k]] = 1.0
            rows.append(r)
            Y.append(np.zeros(3))
            w.append(np.sqrt(GROUP_PRIOR))
        A = np.asarray(rows) * np.asarray(w)[:, None]
        X, *_ = np.linalg.lstsq(A, np.asarray(Y) * np.asarray(w)[:, None], rcond=None)
        for k, j in vi.items():
            g[gi[k]] = np.clip(np.exp(X[j]), GROUP_CLIP[0], GROUP_CLIP[1])
    per = np.ones((len(col), 3))
    for k in groups:
        per[pts[k]] = g[gi[k]]
    return {"gain": per, "edges": edges,
            "groups": [{"region": int(k // 64), "view_index": int(k % 64), "texels": int(len(pts[k])),
                        "fixed": k in fixed, "gain": [float(x) for x in g[gi[k]]],
                        "saturated": bool(np.any(np.isclose(g[gi[k]], GROUP_CLIP[0]))
                                          or np.any(np.isclose(g[gi[k]], GROUP_CLIP[1])))} for k in groups]}


def apply_per_texel(col: np.ndarray, gain: np.ndarray) -> np.ndarray:
    return np.clip(T.linear_to_srgb(T.srgb_to_linear(col) * gain), 0, 255)
