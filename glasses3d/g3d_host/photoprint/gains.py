"""WP5 step 3a: gains (WP5_DESIGN §6.3), single-source mode.

**Build step 4, fix 3 (colour steps between parts / regions; measured on FT1119, bake 7ac7c8f):** 02 and 03 were NOT
connected to the reference (every edge of theirs rejected by the [0.5, 2] clip: 03's only boundary edge needed a blue
gain about 2), so the -x temple's outer face (03) kept gain 1 and stage 2 then pushed it, and 11 other groups, to the
[0.67, 1.5] clip (the -x temple rendered at 01 was L* +3.9 over photo 01 and warmer); stage-2 edges crossed parts (the
brow back and the temple top 2 mm apart across the hinge, ratios up to 5.0 from hinge metal) and one outlier edge from a
1,076-texel end face outweighed 36,400 pairs in log space. Now: stage 1 clip [0.33, 3], boundary pairs within one part,
and MIRROR edges (the two sides of the symmetric product carry the same havana: region medians of mirrored regions
painted by different views); stage 2 same-part edges only, edges outside [0.5, 2] rejected, mirror equations (one view
painting both sides -> equal gains; different views -> equal medians), a robust second solve without boundary edges
whose residual exceeds log 1.6, clip [0.5, 2]; stage 3 ``level_field``: a smooth (1 mm nodes) linear-light gain field
per group that removes what is left of the step where two photos meet. Same-surface overlap equations to the region's
anchor (``anchor_label`` / ``view_cols`` / ``view_ok``) are implemented but OFF in production: on FT1119 they import the
anchor's view-dependent sheen (05 sees the brow front's top band at 2.0 / 1.62 / 1.31 x 04's colours) and raised the
weighted boundary step 4.52 -> 6.13 dE00. Measured with all three stages (``step4_sheets``): weighted boundary step
3.81 -> 2.54 dE00, mean |dL*| of mirrored regions 2.09 -> 1.15.

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

import math

import numpy as np
from scipy.spatial import cKDTree

from .. import texture_utils as T
from . import signals as S

BOUNDARY_MM = 2.0
MIN_OVERLAP = 1000
MIN_SIDE = 200
GAIN_CLIP = (0.33, 3.0)          # step 4 fix 3: was (0.5, 2.0); see the module docstring
BOUNDARY_WEIGHT = 0.5
MIRROR_WEIGHT = 0.5
MIRROR_MIN = 2000
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


def pair_gains(tex, samples: dict, views: list[str], label: np.ndarray, *, boundary_mm: float = BOUNDARY_MM,
               mirror_region: dict | None = None) -> list[dict]:
    """Gain edges between label views: [{a, b, kind, pairs, gain}] (a ~ gain x b, linear light); rejected edges are
    listed with 'rejected'. ``mirror_region`` = {region id: its mirror partner's region id (same facing, other x
    side)}: step 4 fix 3 adds a 'mirror' edge for every two views painting >= ``MIRROR_MIN`` texels of mirrored
    regions (the region medians, a symmetric product's two sides carry the same havana), so a view that overlaps
    nothing (03 paints only the -x temple's outer face) is tied to the view painting the other side (06)."""
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
                    edges.append({**rec, "rejected": f"gain outside {list(GAIN_CLIP)}"})
                else:
                    edges.append({**rec, "gain": [float(x) for x in g]})
                continue
            la, lb = label == ia, label == ib
            if la.sum() < MIN_SIDE or lb.sum() < MIN_SIDE:
                continue
            # step 4 fix 3: boundary pairs only WITHIN one part (a brow back and a temple top 2 mm apart across the
            # hinge are different surfaces, lit differently, and the gap between them is where metal is)
            sa_, sb_ = [], []
            for q in np.unique(tex.part[la | lb]):
                lqa, lqb = la & (tex.part == q), lb & (tex.part == q)
                if lqa.sum() < 20 or lqb.sum() < 20:
                    continue
                da, _ = cKDTree(tex.P[lqb]).query(tex.P[lqa], k=1, distance_upper_bound=boundary_mm)
                db, _ = cKDTree(tex.P[lqa]).query(tex.P[lqb], k=1, distance_upper_bound=boundary_mm)
                sa_.append(np.nonzero(lqa)[0][np.isfinite(da)])
                sb_.append(np.nonzero(lqb)[0][np.isfinite(db)])
            if not sa_:
                continue
            side_a = np.concatenate(sa_)
            side_b = np.concatenate(sb_)
            ca = sa.col[side_a][~specular(sa.col[side_a])]
            cb = sb.col[side_b][~specular(sb.col[side_b])]
            if len(ca) >= MIN_SIDE and len(cb) >= MIN_SIDE:
                g = median_gain(cb, ca)
                rec = {"a": views[ia], "b": views[ib], "kind": "boundary", "pairs": int(min(len(ca), len(cb)))}
                edges.append({**rec, "rejected": f"gain outside {list(GAIN_CLIP)}"} if g is None
                             else {**rec, "gain": [float(x) for x in g]})
    for ra, rb in sorted((mirror_region or {}).items()):
        if rb <= ra:
            continue
        for ia in used:
            for ib in used:
                if ia == ib:
                    continue
                ma = (tex.region == ra) & (label == ia) & samples[views[ia]].ok
                mb = (tex.region == rb) & (label == ib) & samples[views[ib]].ok
                if ma.sum() < MIRROR_MIN or mb.sum() < MIRROR_MIN:
                    continue
                ca, cb = samples[views[ia]].col[ma], samples[views[ib]].col[mb]
                ca, cb = ca[~specular(ca)], cb[~specular(cb)]
                g = median_gain(cb, ca)
                lo, hi = (ia, ib) if ia < ib else (ib, ia)
                rec = {"a": views[ia], "b": views[ib], "kind": "mirror", "pairs": int(min(len(ca), len(cb))),
                       "regions": [int(ra), int(rb)]}
                edges.append({**rec, "rejected": f"gain outside {list(GAIN_CLIP)}"} if g is None
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
            w[k] = np.sqrt(e["pairs"] * {"overlap": 1.0, "boundary": BOUNDARY_WEIGHT, "mirror": MIRROR_WEIGHT}[e["kind"]])
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


def support_warnings(chain: dict, grp: dict, lev: dict | None = None) -> list[str]:
    """Report warnings: views whose gain rests on one thin edge or is not connected, gains at the clip, stage-2
    groups at the clip (fix round 2), and texels where the stage-3 levelling field sits at its own clip (``lev``, the
    ``level_field`` result; fix round 1 of step 4)."""
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
    if lev and lev.get("texels_at_clip"):
        w.append(f"gains: the stage-3 levelling field sits at its clip (x{math.exp(LEVEL_CLIP):g} / "
                 f"x{math.exp(-LEVEL_CLIP):g}) on {lev['texels_at_clip']} texels "
                 f"({lev['texels_at_clip_share']:.2%} of the painted texels; {lev['nodes_at_clip']} nodes); "
                 f"{lev['texels_gain_large_share']:.2%} get a field gain beyond x{LEVEL_LARGE:g} or x1/{LEVEL_LARGE:g}")
    return w


def apply(col: np.ndarray, g: dict) -> np.ndarray:
    """Gained sRGB float (linear-light gain; the identity returns the input exactly)."""
    gg = np.asarray(g["gain"], float)
    if np.allclose(gg, 1.0):
        return np.asarray(col, float)
    return np.clip(T.linear_to_srgb(T.srgb_to_linear(col) * gg), 0, 255)


# ----------------------------------------------------------------------------- stage 2: per (region, view) group
GROUP_CLIP = (0.5, 2.0)          # step 4 fix 3: was (0.67, 1.5), where 12 of 32 FT1119 groups sat
GROUP_PRIOR = 200.0
GROUP_BAND_MM = 2.0
GROUP_EDGE_RANGE = (0.5, 2.0)    # a boundary edge whose median ratio leaves this range compares different things
OVERLAP_WEIGHT = 4.0             # a same-surface overlap equation counts this many times its pairs' weight
ROBUST_LOG = float(np.log(1.6))  # a boundary edge whose residual exceeds this after the first solve is dropped
MIRROR_GROUP_MIN = 2000
MIRROR_SAME_WEIGHT = 3.0
MIRROR_MEDIAN_WEIGHT = 1.0


def group_gains(tex, painted: np.ndarray, label: np.ndarray, col: np.ndarray, ref_label: int | None, *,
                band_mm: float = GROUP_BAND_MM, anchor_label: np.ndarray | None = None, view_cols=None,
                view_ok=None, mirror_region: dict | None = None) -> dict:
    """One linear-light gain per painted group (region x painting view), on the stage-1 gained colours, so that a
    region painted by several views and neighbouring regions painted by different views show one albedo.

    Equations (weighted least squares of log gains per channel; ``GROUP_CLIP``):
    - **same-surface overlap** (step 4 fix 3, §6.3's "per (view, region) to the anchor, from same-surface
      overlaps"): for a group (R, v) whose region's anchor a (``anchor_label``) also sees >= 200 of its texels
      (``view_ok[a]``, ``view_cols[a]``: a's stage-1 gained colours), G(R, v) / G(R, a) = median_a / median_v on those
      very texels, weight OVERLAP_WEIGHT x sqrt(pairs). This is what fixes the band of a brow front that the label
      moves gave to the top view 04: 05 sees it, so it is matched to what 05 shows there, not to a boundary median;
    - **across a region boundary**: texels within ``band_mm`` of a neighbouring group with a different view, ON THE
      SAME PART only (fix 3: the brow back and the temple top 2 mm apart across the hinge were matched before, with
      ratios up to 5.0 from hinge metal); edges whose median ratio leaves ``GROUP_EDGE_RANGE`` in any channel are
      rejected (listed); weight sqrt(pairs);
    - groups painted by the reference view are fixed at 1, and every group is pulled weakly toward 1 (sqrt(200)).
    After the first solve, boundary edges whose residual exceeds log 1.6 in any channel are dropped and the system is
    solved again (listed in 'dropped'): one outlier edge (a tiny end face) had pulled the -x temple's 163,123-texel
    outer face to the old clip. Returns {'gain': (n, 3) per texel, 'groups': [...], 'edges': [...], 'overlaps': [...],
    'rejected': [...], 'dropped': [...]}."""
    keys = np.where(painted, tex.region * 64 + np.maximum(label, 0), -1)
    groups = [int(k) for k in np.unique(keys[keys >= 0])]
    gi = {k: i for i, k in enumerate(groups)}
    edges, rejected, overlaps, mirrors = [], [], [], []
    pts = {k: np.nonzero(keys == k)[0] for k in groups}
    part_of = {k: np.unique(tex.part[pts[k]]) for k in groups}

    def lin(c):
        return T.srgb_to_linear(np.asarray(c, float))

    for i, ka in enumerate(groups):
        for kb in groups[i + 1:]:
            if (ka % 64) == (kb % 64):
                continue                                      # same view on both sides: no photo step to match
            for q in np.intersect1d(part_of[ka], part_of[kb]):
                pa_ = pts[ka][tex.part[pts[ka]] == q]
                pb_ = pts[kb][tex.part[pts[kb]] == q]
                if len(pa_) < MIN_SIDE or len(pb_) < MIN_SIDE:
                    continue
                da, _ = cKDTree(tex.P[pb_]).query(tex.P[pa_], k=1, distance_upper_bound=band_mm)
                sa = pa_[np.isfinite(da)]
                if len(sa) < MIN_SIDE:
                    continue
                db, _ = cKDTree(tex.P[pa_]).query(tex.P[pb_], k=1, distance_upper_bound=band_mm)
                sb = pb_[np.isfinite(db)]
                if len(sb) < MIN_SIDE:
                    continue
                ca, cb = col[sa][~specular(col[sa])], col[sb][~specular(col[sb])]
                if len(ca) < MIN_SIDE or len(cb) < MIN_SIDE:
                    continue
                la = np.median(lin(ca), 0)
                lb = np.median(lin(cb), 0)
                if np.any(la <= 1e-6) or np.any(lb <= 1e-6):
                    continue
                ratio = la / lb
                rec = {"a": ka, "b": kb, "part": int(q), "pairs": int(min(len(ca), len(cb))),
                       "ratio": [float(x) for x in ratio], "kind": "boundary"}
                if np.any(ratio < GROUP_EDGE_RANGE[0]) or np.any(ratio > GROUP_EDGE_RANGE[1]):
                    rejected.append({**rec, "why": f"ratio outside {list(GROUP_EDGE_RANGE)}"})
                else:
                    edges.append(rec)
    if anchor_label is not None and view_cols is not None and view_ok is not None:
        for k in groups:
            v = k % 64
            p = pts[k]
            a_ = int(anchor_label[p[0]]) if len(p) else -1
            if a_ < 0 or a_ == v or a_ >= len(view_cols):
                continue
            ka = (k // 64) * 64 + a_
            if ka not in gi:
                continue
            both = p[view_ok[a_][p]]
            if len(both):
                both = both[~specular(view_cols[a_][both]) & ~specular(col[both])]
            if len(both) < MIN_SIDE:
                continue
            la = np.median(lin(view_cols[a_][both]), 0)
            lv = np.median(lin(col[both]), 0)
            if np.any(la <= 1e-6) or np.any(lv <= 1e-6):
                continue
            ratio = la / lv                                   # anchor ~ ratio x v on the same texels
            rec = {"a": ka, "b": k, "pairs": int(len(both)), "ratio": [float(x) for x in ratio], "kind": "overlap"}
            if np.any(ratio < GROUP_CLIP[0] / 2) or np.any(ratio > GROUP_CLIP[1] * 2):
                rejected.append({**rec, "why": "overlap ratio beyond twice the clip"})
            else:
                overlaps.append(rec)
    # mirror equations (step 4 fix 3): a group and its mirror group (the mirror region, painted by a view): the SAME
    # view on both sides -> equal gains (one photo lights both sides alike; without this the -x and +x temple tops,
    # both from 04, drifted apart by chains of boundary edges: L* 28.0 vs 19.6); DIFFERENT views -> equal region
    # medians (the symmetric product's two sides carry the same havana)
    for ka in groups:
        rb = (mirror_region or {}).get(ka // 64)
        if rb is None:
            continue
        for kb in groups:
            if kb // 64 != rb or kb <= ka:
                continue
            na_, nb_ = len(pts[ka]), len(pts[kb])
            if min(na_, nb_) < MIRROR_GROUP_MIN:
                continue
            if ka % 64 == kb % 64:
                mirrors.append({"a": ka, "b": kb, "pairs": int(min(na_, nb_)), "ratio": [1.0, 1.0, 1.0],
                                "kind": "mirror_same_view"})
            else:
                ca, cb = col[pts[ka]], col[pts[kb]]
                ca, cb = ca[~specular(ca)], cb[~specular(cb)]
                la, lb = np.median(lin(ca), 0), np.median(lin(cb), 0)
                if np.any(la <= 1e-6) or np.any(lb <= 1e-6):
                    continue
                ratio = la / lb
                rec = {"a": ka, "b": kb, "pairs": int(min(len(ca), len(cb))), "ratio": [float(x) for x in ratio],
                       "kind": "mirror_median"}
                if np.any(ratio < GROUP_EDGE_RANGE[0]) or np.any(ratio > GROUP_EDGE_RANGE[1]):
                    rejected.append({**rec, "why": f"ratio outside {list(GROUP_EDGE_RANGE)}"})
                else:
                    mirrors.append(rec)
    fixed = {k for k in groups if ref_label is not None and (k % 64) == ref_label}
    var = [k for k in groups if k not in fixed]
    vi = {k: j for j, k in enumerate(var)}
    KIND_W = {"overlap": OVERLAP_WEIGHT, "boundary": 1.0, "mirror_same_view": MIRROR_SAME_WEIGHT,
              "mirror_median": MIRROR_MEDIAN_WEIGHT}

    def solve(eqs):
        g_ = np.ones((len(groups), 3))
        if not var:
            return g_, np.zeros((len(eqs), 3))
        rows, Y, w = [], [], []
        for e in eqs:
            r = np.zeros(len(var))
            if e["b"] in vi:
                r[vi[e["b"]]] += 1.0                          # a ~ ratio x b -> log G_b - log G_a = log ratio
            if e["a"] in vi:
                r[vi[e["a"]]] -= 1.0
            rows.append(r)
            Y.append(np.log(e["ratio"]))
            w.append(np.sqrt(e["pairs"]) * KIND_W[e["kind"]])
        for k in var:
            r = np.zeros(len(var))
            r[vi[k]] = 1.0
            rows.append(r)
            Y.append(np.zeros(3))
            w.append(np.sqrt(GROUP_PRIOR))
        A = np.asarray(rows) * np.asarray(w)[:, None]
        X, *_ = np.linalg.lstsq(A, np.asarray(Y) * np.asarray(w)[:, None], rcond=None)
        for k, j in vi.items():
            g_[gi[k]] = np.exp(X[j])
        res_ = np.asarray([np.log(e["ratio"]) - (np.log(g_[gi[e["b"]]]) - np.log(g_[gi[e["a"]]])) for e in eqs]
                          ).reshape(-1, 3)
        return g_, res_

    eqs = edges + overlaps + mirrors
    g, resid = solve(eqs)
    dropped = []
    if len(eqs):
        bad = {i for i, e in enumerate(eqs) if e["kind"] == "boundary" and np.abs(resid[i]).max() > ROBUST_LOG}
        if bad:
            dropped = [{**eqs[i], "residual": [float(x) for x in resid[i]]} for i in sorted(bad)]
            eqs = [e for i, e in enumerate(eqs) if i not in bad]
            g, resid = solve(eqs)
    g = np.clip(g, GROUP_CLIP[0], GROUP_CLIP[1])
    per = np.ones((len(col), 3))
    for k in groups:
        per[pts[k]] = g[gi[k]]
    return {"gain": per, "edges": [e for e in eqs if e["kind"] == "boundary"],
            "overlaps": [e for e in eqs if e["kind"] == "overlap"],
            "mirrors": [e for e in eqs if e["kind"].startswith("mirror")], "rejected": rejected, "dropped": dropped,
            "groups": [{"region": int(k // 64), "view_index": int(k % 64), "texels": int(len(pts[k])),
                        "fixed": k in fixed, "gain": [float(x) for x in g[gi[k]]],
                        "saturated": bool(np.any(np.isclose(g[gi[k]], GROUP_CLIP[0]))
                                          or np.any(np.isclose(g[gi[k]], GROUP_CLIP[1])))} for k in groups]}


def apply_per_texel(col: np.ndarray, gain: np.ndarray) -> np.ndarray:
    return np.clip(T.linear_to_srgb(T.srgb_to_linear(col) * gain), 0, 255)


# ----------------------------------------------------------------------------- stage 3: the levelling gain field
LEVEL_CELL_MM = 1.0              # coarse cell: 1 mm (16 texels at 16 texels/mm)
LEVEL_W_BOUNDARY = 0.05          # per node pair across a boundary (with smoothness 1 the field averages the jump
                                 # along the boundary over a few nodes itself)
LEVEL_TARGET_SIGMA_MM = 0.0      # > 0: the target jump is the median over +-2 sigma along the boundary (measured on
                                 # FT1119 with w_boundary 1: boundary steps 4.13-4.65 dE00 against 3.07 for raw targets
                                 # at 0.05, so off)
LEVEL_MAX_JUMP = float("inf")    # finite: pairs whose mean |jump| exceeds it are not levelled (off: dropping cost)
LEVEL_W_ANCHOR = 0.01            # every node weakly toward no correction (measured: 1e-3 let mirror sides drift
                                 # apart, L* 26.8 vs 21.6 on the brow tops; 0.05 left steps of 3.7 dE00)
LEVEL_W_REFERENCE = 1.0          # nodes of groups painted by the reference view stay put
LEVEL_CLIP = float(np.log(2.0))
LEVEL_LARGE = 1.5                # reported: texels whose field gain is >= x1.5 or <= x1/1.5 in a channel
LEVEL_EPS = 1e-4                 # linear-light floor of the log colours


def level_field(tex, painted: np.ndarray, label: np.ndarray, col: np.ndarray, ref_label: int | None,
                pairs: np.ndarray | None = None, *, tx: float = 16.0, cell: int | None = None,
                w_boundary: float = LEVEL_W_BOUNDARY,
                w_anchor: float = LEVEL_W_ANCHOR, w_reference: float = LEVEL_W_REFERENCE) -> dict:
    """Stage 3 (step 4 fix 3): a SMOOTH linear-light gain field per painted group (region x view), seam levelling
    in the spirit of Lempitsky and Ivanov (2007), so that the colour steps left by one gain per group vanish where two
    groups painted by different photos meet, while each group's texture is kept (the field is smooth: 1 mm nodes).

    Unknowns: c (log linear-light gain, per channel) at nodes = (group, chart, 1 mm cell) holding painted texels; a
    texel's correction is the bilinear interpolation of its own group's nodes in its chart (never another group's).
    Least squares with: smoothness c_i = c_j between 4-adjacent nodes of one group and chart, and between the nodes of
    the two texels of a chart-seam pair of one group (``pairs``: the field is continuous across uv seams), weight 1;
    across a boundary (atlas 4-neighbours in one chart, or a chart-seam pair, of two groups painted by DIFFERENT views
    on the SAME part) c_a - c_b = J, the node pair's jump V_b - V_a (V = the node's mean log colour) AVERAGED along
    the boundary (the median within 8 mm, same pair of groups: the step is matched, not the blotches crossing it;
    pairs whose median jump exceeds log 2 in a channel are not levelled), weight
    ``w_boundary``; every node weakly toward 0 (``LEVEL_W_ANCHOR``: the correction decays about 10 mm into a group),
    and nodes of the reference view's groups held at 0. Clipped to +-log 2. Returns {'gain': (n, 3) per texel (1 off the painted
    set), 'nodes', 'boundary_pairs', 'max_abs_log'}."""
    from scipy import sparse
    from scipy.sparse.linalg import spsolve
    cell = int(cell or max(2, round(LEVEL_CELL_MM * tx)))
    n = len(col)
    out_gain = np.ones((n, 3))
    sel = np.nonzero(painted & (label >= 0))[0]
    if not len(sel):
        return {"gain": out_gain, "nodes": 0, "boundary_pairs": 0, "max_abs_log": 0.0}
    g = (tex.region[sel] * 64 + label[sel]).astype(np.int64)
    ch = tex.chart[sel].astype(np.int64)
    cy, cx = tex.ii[sel] // cell, tex.jj[sel] // cell
    Hc, Wc = tex.shape[0] // cell + 2, tex.shape[1] // cell + 2
    key = ((g * 1024 + ch) * Hc + cy) * Wc + cx
    ukey, node = np.unique(key, return_inverse=True)
    N = len(ukey)
    x = np.log(np.maximum(T.srgb_to_linear(np.asarray(col, float)[sel]), LEVEL_EPS))
    cnt = np.bincount(node, minlength=N).astype(float)
    V = np.stack([np.bincount(node, weights=x[:, k], minlength=N) for k in range(3)], 1) / cnt[:, None]
    node_of = np.full(n, -1, np.int64)
    node_of[sel] = node
    g_of = np.full(n, -1, np.int64)
    g_of[sel] = g
    lookup = {int(k): i for i, k in enumerate(ukey)}
    rows_a, rows_b, kinds, tgts = [], [], [], []
    # smoothness: 4-adjacent cells of one group and chart
    ng = ukey // Wc // Hc
    ncy = (ukey // Wc) % Hc
    ncx = ukey % Wc
    for dy, dx in ((0, 1), (1, 0)):
        nb = ((ng) * Hc + (ncy + dy)) * Wc + (ncx + dx)
        idx = np.searchsorted(ukey, nb)
        ok = (idx < N) & (ukey[np.minimum(idx, N - 1)] == nb)
        rows_a.append(np.nonzero(ok)[0])
        rows_b.append(idx[ok])
        kinds.append(np.zeros(int(ok.sum()), np.int64))
        tgts.append(np.zeros((int(ok.sum()), 3)))
    # texel pairs: atlas 4-neighbours in one chart, and chart-seam pairs
    I = tex.index
    pa, pb = [], []
    for dy, dx in ((0, 1), (1, 0)):
        a = I[: I.shape[0] - dy, : I.shape[1] - dx].ravel()
        b = I[dy:, dx:].ravel()
        m = (a >= 0) & (b >= 0)
        pa.append(a[m])
        pb.append(b[m])
    if pairs is not None and len(pairs):
        pa.append(np.asarray(pairs)[:, 0])
        pb.append(np.asarray(pairs)[:, 1])
    pa = np.concatenate(pa)
    pb = np.concatenate(pb)
    m = (node_of[pa] >= 0) & (node_of[pb] >= 0) & (node_of[pa] != node_of[pb]) & (tex.part[pa] == tex.part[pb])
    pa, pb = pa[m], pb[m]
    same_g = g_of[pa] == g_of[pb]
    diff_view = (g_of[pa] % 64) != (g_of[pb] % 64)
    # same group (seam pairs, or 4-neighbours across a cell edge already covered): smoothness
    na, nb_ = node_of[pa[same_g]], node_of[pb[same_g]]
    rows_a.append(na)
    rows_b.append(nb_)
    kinds.append(np.zeros(len(na), np.int64))
    tgts.append(np.zeros((len(na), 3)))
    bnd = ~same_g & diff_view
    na, nb_ = node_of[pa[bnd]], node_of[pb[bnd]]
    if len(na):
        pk = np.unique(np.stack([np.minimum(na, nb_), np.maximum(na, nb_)], 1), axis=0)
        rows_a.append(pk[:, 0])
        rows_b.append(pk[:, 1])
        kinds.append(np.ones(len(pk), np.int64))
        # the jump (V_b - V_a) of each boundary node pair, the MEDIAN along its boundary within 2 x
        # LEVEL_TARGET_SIGMA_MM (same pair of groups, 3D): the field matches the step, not the blotches crossing it
        Pn = np.stack([np.bincount(node, weights=tex.P[sel][:, k], minlength=N) for k in range(3)], 1) / cnt[:, None]
        J = V[pk[:, 1]] - V[pk[:, 0]]
        mid = (Pn[pk[:, 0]] + Pn[pk[:, 1]]) / 2
        gp = (ukey[pk[:, 0]] // Wc // Hc // 1024) * 100000 + (ukey[pk[:, 1]] // Wc // Hc // 1024)
        Js = J.copy()
        sig = LEVEL_TARGET_SIGMA_MM
        for q in (np.unique(gp) if sig > 0 else []):
            mq = np.nonzero(gp == q)[0]
            tr = cKDTree(mid[mq])
            for i_, nbrs in zip(mq, tr.query_ball_point(mid[mq], 2 * sig)):
                Js[i_] = np.median(J[mq[nbrs]], 0)          # a median: blotches crossing the boundary do not count
        # a boundary whose averaged jump exceeds LEVEL_MAX_JUMP in a channel compares different things (a tiny strip of
        # a grazing photo, glare): it is not levelled (its pairs are dropped and counted)
        okj = np.abs(Js).mean(1) <= LEVEL_MAX_JUMP
        rows_a[-1], rows_b[-1], kinds[-1] = rows_a[-1][okj], rows_b[-1][okj], kinds[-1][okj]
        tgts.append(Js[okj])
        n_bnd = int(okj.sum())
        n_bnd_dropped = int((~okj).sum())
    else:
        n_bnd = 0
        n_bnd_dropped = 0
    ra = np.concatenate(rows_a)
    rb = np.concatenate(rows_b)
    kd = np.concatenate(kinds)
    tg = np.concatenate(tgts)
    keep = ra != rb
    ra, rb, kd, tg = ra[keep], rb[keep], kd[keep], tg[keep]
    # The field lives on 1 mm nodes, not on texels. Rasterized neighbours and
    # sampled chart seams can describe a node edge many times (and overlap the
    # grid adjacency above). Count each undirected constraint once; otherwise
    # smoothness grows with texel density while boundary weights stay fixed.
    edge_key = np.column_stack([np.minimum(ra, rb), np.maximum(ra, rb), kd])
    _, unique = np.unique(edge_key, axis=0, return_index=True)
    ra, rb, kd, tg = ra[unique], rb[unique], kd[unique], tg[unique]
    w = np.where(kd == 1, w_boundary, 1.0)
    m_ = len(ra)
    ref_node = ((ng // 1024) % 64 == ref_label) if ref_label is not None else np.zeros(N, bool)
    wa = np.where(ref_node, w_reference, w_anchor)
    # rows: c_a - c_b = y (smooth: 0; boundary: V_b - V_a), then c_i = 0 (anchors)
    rr = np.concatenate([np.arange(m_), np.arange(m_), m_ + np.arange(N)])
    cc = np.concatenate([ra, rb, np.arange(N)])
    vv = np.concatenate([np.sqrt(w), -np.sqrt(w), np.sqrt(wa)])
    A = sparse.csr_matrix((vv, (rr, cc)), shape=(m_ + N, N))
    AtA = (A.T @ A).tocsc()
    C = np.zeros((N, 3))
    for k in range(3):
        y = np.concatenate([np.where(kd == 1, tg[:, k], 0.0) * np.sqrt(w), np.zeros(N)])
        C[:, k] = spsolve(AtA, A.T @ y)
    C = np.clip(C, -LEVEL_CLIP, LEVEL_CLIP)
    # bilinear interpolation of each texel's own group's nodes (cell centres at (c + 0.5) * cell)
    fy = (tex.ii[sel] + 0.5) / cell - 0.5
    fx = (tex.jj[sel] + 0.5) / cell - 0.5
    y0 = np.floor(fy).astype(np.int64)
    x0 = np.floor(fx).astype(np.int64)
    acc = np.zeros((len(sel), 3))
    wsum = np.zeros(len(sel))
    base = g * 1024 + ch
    for oy in (0, 1):
        for ox in (0, 1):
            yy, xx = y0 + oy, x0 + ox
            wy = np.where(oy == 0, 1 - (fy - y0), fy - y0)
            wx = np.where(ox == 0, 1 - (fx - x0), fx - x0)
            kk = (base * Hc + yy) * Wc + xx
            okk = (yy >= 0) & (xx >= 0)
            idx = np.searchsorted(ukey, kk)
            hit = okk & (idx < N) & (ukey[np.minimum(idx, N - 1)] == kk)
            ww = wy * wx * hit
            acc += ww[:, None] * C[np.minimum(idx, N - 1)]
            wsum += ww
    own = C[node]
    corr = np.where(wsum[:, None] > 1e-9, acc / np.maximum(wsum, 1e-9)[:, None], own)
    out_gain[sel] = np.exp(corr)
    # fix round 1 of step 4: the field's own clip is reported like stage 2's (review: 'groups at the gain limit 12 -> 0'
    # moved the saturation into this field, max |log| = log 2 on 0.15 % of the texels)
    at_clip_node = np.isclose(np.abs(C), LEVEL_CLIP, atol=1e-9).any(1)
    at_clip_tex = (np.abs(corr) >= LEVEL_CLIP - 1e-6).any(1)
    big = ((corr >= np.log(LEVEL_LARGE)) | (corr <= -np.log(LEVEL_LARGE))).any(1)
    return {"gain": out_gain, "nodes": int(N), "boundary_pairs": int(n_bnd), "boundary_pairs_dropped": n_bnd_dropped,
            "max_abs_log": float(np.abs(C).max()) if N else 0.0,
            "p95_abs_log": float(np.percentile(np.abs(C), 95)) if N else 0.0,
            "clip_log": LEVEL_CLIP, "nodes_at_clip": int(at_clip_node.sum()), "texels_at_clip": int(at_clip_tex.sum()),
            "texels_at_clip_share": float(at_clip_tex.mean()), "painted_texels": int(len(sel)),
            f"texels_gain_beyond_x{LEVEL_LARGE:g}": int(big.sum()), "texels_gain_large_share": float(big.mean())}
