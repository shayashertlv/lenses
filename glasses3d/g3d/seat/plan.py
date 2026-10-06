"""Growth and lift planning (WP4_DESIGN.md §4 steps 2-5, §6 steps 1-4). numpy only.

`plan_growth` turns one measurement into per-node growth along u:

- verdict per point (§4 step 2, shared with the check: rules.verdicts without TRANSITION);
- need in each run of failing points: target_i = pass + 0.05 + (target - pass - 0.05) * smoothstep(delta_i / 2 mm),
  delta = arc distance to the nearer run end; need_i = target_i - v_i (§4 step 3);
- clamps per point, each recorded (§4 step 4): NEEDS_GROWTH (g > 1.5), EXITS_MATERIAL (room - g < 0.20 at a required
  depth), OTHER_PART (the grown edge would enter a non-frame part or the other lens; clearance OTHER_PART_CLEARANCE_MM),
  CONCAVE (g > 0.5 rho at a concave outline node), UNHIDEABLE (the grown front corner is neither inside frame nor
  covered along +n with the 0.10 margin, trying g, 0.9 g ... 0.1 g); a clamp that leaves v below pass makes the point
  BLOCKED(reason). Two reasons the design does not list, both refusals of a growth that cannot be planned: NO_FRAME
  (a required depth has no frame within REACH, so there is nothing to grow into) and, on a node that also owns a
  visible-by-design point, UNHIDEABLE with limit 0 (growing that node would extend an exposed edge, §12 'never
  extend an exposed edge');
- on semi_rimless an unhideable point is VISIBLE_BY_DESIGN; within 3 mm of a visible-by-design point a blocked point
  with v >= 0 and the front covered is TRANSITION;
- node growth g = the largest growth of its non-blocked failing points, never more than the smallest limit of any
  point it owns; every other node gets exactly 0 (§2 'Growth taper': strict). A point exactly halfway along its
  segment is owned by both ends (stations.Frames.owners): its limits cap both, its growth is taken in full by an
  end with no growth of its own, else met by the mean of the two (`halfway_growth`), and as a visible-by-design or
  UNMEASURED point it holds neither end (it is nearer to neither), so the plan does not depend on the loop's
  direction (a lens and its mirror grow alike, §10 P2 'left = mirrored right within 0.01 mm');
- gates (§4 step 5): > 20% of required points BLOCKED -> E_TOO_MANY_BLOCKED; a full-rim type with > 2% EXPOSED/BACKED
  -> E_FULLRIM_EXPOSED. Nothing moves on a gate.

`plan_lift` is §6 steps 1-4 for hide_eyewire: selected stations, rings, the per-ring lift lambda* and the lift field
L(s) = max_j lambda*_j (1 - smoothstep(|s - s_j| / ramp)). Lifted rings are the ones nearest to a selected station
and every ring holding an end of an eyewire edge that spans one (the eyewire between rigid rings is their blend).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import geom, rules
from .geom import smoothstep
from .intervals import line_intervals, merge
from .stations import (COVER_NUDGE_MM, COVER_REACH_MM, DEPTHS, HIDDEN_MARGIN_MM, MID, UNDER_WINDOW_MM, Frames,
                       Measurement, cover_along)

G_MAX_MM = 1.5                     # §7 g_max
RUN_END_RAMP_MM = 2.0              # §7 run-end ramp
RUN_END_MARGIN_MM = 0.05           # §4 step 3: run ends reach pass + 0.05
MATERIAL_LEFT_MM = 0.20            # §7 material left
OTHER_PART_CLEARANCE_MM = 0.10     # not in §7: the grown edge stays this far from a non-frame part (the hidden margin)
CONCAVE_SHARE = 0.5                # §4 step 4: g > 0.5 rho
CURVATURE_HALF_MM = 0.3            # outline curvature from the loop points 0.3 mm before and after the node (§3's
                                   # chord rule for t; 1.0 mm read a 0.54 mm notch radius as 0.88 and let the grown
                                   # rim fold over itself)
UNHIDE_FRACTIONS = (1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1)
BLOCKED_GATE_SHARE = 0.20          # §4 step 5
TOL_MM = rules.PASS_TOLERANCE_MM
REASONS = ("NEEDS_GROWTH", "EXITS_MATERIAL", "OTHER_PART", "CONCAVE", "UNHIDEABLE", "NO_FRAME")
LIMIT_KEYS = REASONS + ("HELD",)
REQUIRED_DEPTHS = {"deep": tuple(range(len(DEPTHS))), "thin": (MID,), "flats": (0, len(DEPTHS) - 1), "report": ()}

LIFT_MARGIN_MM = 0.10              # §7 eyewire margin
LIFT_RAMP_MM = 3.0                 # §7 eyewire ramp
LIFT_MAX_MM = 2.5                  # §7 lift_max
RING_TANGENT_DOT = 0.5             # §6 step 2: ring edges have |direction . t| < 0.5
LIFT_EPS_MM = rules.PASS_TOLERANCE_MM   # a ring short of the 0.10 mm margin by <= 0.005 mm is buried: the capture
                                        # verdict's own tolerance. A second call re-tests along the MOVED lens's
                                        # frames (u, n differ by ~1e-3), so an exact margin left q001's lifted rings
                                        # 0.0005-0.0014 mm 'short' and the call was not a no-op
LIFT_PATH_ITERATIONS = 4           # fixed-point iterations of lambda* along the curved path (dn changes ~1e-3 per step)


# ------------------------------------------------------------------------------------------------------ helpers
def runs(mask: np.ndarray) -> list[np.ndarray]:
    """Maximal runs of True in a circular boolean array (index arrays in order); the whole array when all True."""
    mask = np.asarray(mask, bool)
    n = len(mask)
    if not mask.any():
        return []
    if mask.all():
        return [np.arange(n)]
    start = int(np.flatnonzero(~mask)[0])
    out, cur = [], []
    for i in ((np.arange(n) + start + 1) % n).tolist():
        if mask[i]:
            cur.append(i)
        elif cur:
            out.append(np.array(cur, np.int64))
            cur = []
    if cur:
        out.append(np.array(cur, np.int64))
    return out


def run_targets(fail: np.ndarray, s: np.ndarray, loop_mm: float, pass_mm: np.ndarray, target_mm: np.ndarray,
                ramp: float = RUN_END_RAMP_MM, margin: float = RUN_END_MARGIN_MM) -> np.ndarray:
    """§4 step 3 target per point (nan outside failing runs). A run covering the whole loop has no ends: target."""
    out = np.full(len(fail), np.nan)
    for run in runs(fail):
        p, t = pass_mm[run], target_mm[run]
        if len(run) == len(fail):
            out[run] = t
            continue
        ss = s[run]
        a = (ss - ss[0]) % loop_mm
        b = (ss[-1] - ss) % loop_mm
        delta = np.minimum(a, b)
        out[run] = p + margin + (t - p - margin) * smoothstep(delta / ramp)
    return out


def node_points(frames: Frames, body_V: np.ndarray) -> np.ndarray:
    """Index of the measurement point sitting exactly on each node (the first point of its segment)."""
    nv = len(frames.loop)
    d = np.linalg.norm(frames.P - body_V[frames.loop][frames.node], axis=1)
    out = np.full(nv, -1, np.int64)
    best = np.full(nv, np.inf)
    for i in np.argsort(d, kind="stable").tolist():
        k = int(frames.node[i])
        if d[i] < best[k]:
            best[k], out[k] = d[i], i
    return out


def interp_loop(P: np.ndarray, s_loop: np.ndarray, L: float, s: np.ndarray) -> np.ndarray:
    """Points on the closed polyline P (arc parameters s_loop, length L) at arc parameters s."""
    s = np.mod(s, L)
    k = np.searchsorted(s_loop, s, side="right") - 1
    k = np.clip(k, 0, len(P) - 1)
    k1 = (k + 1) % len(P)
    seg = np.where(k1 == 0, L - s_loop[k], s_loop[np.minimum(k + 1, len(P) - 1)] - s_loop[k])
    w = np.where(seg > 0, (s - s_loop[k]) / np.where(seg > 0, seg, 1.0), 0.0)
    return P[k] + w[:, None] * (P[k1] - P[k])


def concave_radius(frames: Frames, body_V: np.ndarray, half: float = CURVATURE_HALF_MM) -> np.ndarray:
    """Per node: the outline's radius of curvature where the loop turns concave (inward), else inf. From the
    circle through the loop points `half` mm before and after the node (arc-length based, so Prada's 0.019 mm
    segments do not read as corners)."""
    loop = frames.loop
    P = body_V[loop]
    seg = np.linalg.norm(np.roll(P, -1, 0) - P, axis=1)
    sl = np.r_[0.0, np.cumsum(seg)][:-1]
    L = float(seg.sum())
    A = interp_loop(P, sl, L, sl - half)
    C = interp_loop(P, sl, L, sl + half)
    np_ = node_points(frames, body_V)
    n = frames.n[np.maximum(np_, 0)]
    ab, bc = P - A, C - P
    turn = (np.cross(ab, bc) * n).sum(1)               # > 0: counter-clockwise about n (convex on a CCW loop)
    ac = np.linalg.norm(C - A, axis=1)
    la, lb = np.linalg.norm(ab, axis=1), np.linalg.norm(bc, axis=1)
    sin_b = np.abs(turn) / np.maximum(la * lb, 1e-30)
    rho = np.where(sin_b > 1e-9, ac / (2.0 * np.maximum(sin_b, 1e-30)), np.inf)
    return np.where(turn < 0, rho, np.inf)


def _first_entry(U: np.ndarray, edge: float) -> float:
    """Distance from `edge` to the first merged interval reaching beyond it (0 when the edge is inside one), inf
    when none."""
    for a, b in U:
        if b > edge:
            return max(0.0, float(a - edge))
    return np.inf


# ------------------------------------------------------------------------------------------------- growth plan
@dataclass
class SeatPlan:
    verdict: np.ndarray            # (N,) PASS / FAIL / BLOCKED / TRANSITION / VISIBLE_BY_DESIGN / UNMEASURED / ...
    reason: np.ndarray             # (N,) binding clamp ("" when none)
    clamped: np.ndarray            # (N,) a clamp reduced the growth
    need: np.ndarray               # (N,) target - value (nan outside failing runs)
    target: np.ndarray             # (N,) §4 step 3 target (nan outside failing runs)
    limit: np.ndarray              # (N,) the smallest clamp limit (inf when none)
    growth: np.ndarray             # (N,) planned growth of the point (before the node aggregation)
    node_g: np.ndarray             # (nv,) growth per node (index into frames.loop)
    node_target: np.ndarray        # (nv,) largest planned target among the node's growing points (nan: none)
    node_point: np.ndarray         # (nv,) the measurement point on each node
    gate: str | None = None
    gate_message: str = ""
    limits: dict = field(default_factory=dict)       # per reason: (N,) limit arrays (inf: no limit)

    @property
    def moves(self) -> bool:
        return self.gate is None and bool((self.node_g > 0).any())

    def counts(self) -> dict:
        v = self.verdict.astype(str)
        out = {k: int((v == k).sum()) for k in rules.VERDICTS if (v == k).any()}
        r = self.reason.astype(str)
        blocked = {k: int(((v == "BLOCKED") & (r == k)).sum()) for k in REASONS}
        out["blocked_by"] = {k: c for k, c in blocked.items() if c}
        return out


def plan_growth(frames: Frames, meas: Measurement, rl: rules.Rules, ft: rules.FrameType, backend, lens_part: int,
                body_V: np.ndarray, obstacles=None, g_max: float = G_MAX_MM, already: np.ndarray | None = None,
                fail_only: np.ndarray | None = None, target_override: np.ndarray | None = None) -> SeatPlan:
    """Plan growth for one lens body.

    backend: the measuring backend (frame parts, then the lens as part `lens_part`); obstacles: a backend over the
    non-frame parts the edge must not enter (other lenses, temples), or None. already: (nv,) growth applied in
    earlier passes (the correction pass counts it against g_max). fail_only: restrict growth to these points (the
    correction pass grows only the stations it moved). target_override: per-point targets (correction pass)."""
    N = len(frames)
    nv = len(frames.loop)
    base = rules.verdicts(ft, meas, rl, frames.s, frames.loop_mm, transition=False).astype(object)
    req = np.asarray(rl.required, bool)
    v = np.asarray(rl.value, float)
    p_mm = np.asarray(rl.pass_mm, float)
    t_mm = np.asarray(rl.target_mm, float)
    fail = (base.astype(str) == "FAIL") & req
    if fail_only is not None:
        fail &= np.asarray(fail_only, bool)
    target = run_targets(fail, frames.s, frames.loop_mm, p_mm, t_mm)
    if target_override is not None:
        target = np.where(fail & np.isfinite(target_override), target_override, target)
    need = np.where(fail, target - v, np.nan)
    np_pt = node_points(frames, body_V)
    already = np.zeros(nv) if already is None else np.asarray(already, float)
    # a point exactly halfway along its loop segment belongs to both nodes (Frames.owners): its limits and target
    # count for both, a node-derived limit of the point is the tighter of the two, its growth goes through
    # halfway_growth and it holds neither node (below), so a lens and its mirror (whose loop runs the other way) are
    # planned alike
    o1, o2 = frames.owners

    # --- limits ---------------------------------------------------------------------------------------------
    inf = np.full(N, np.inf)
    lim = {k: inf.copy() for k in LIMIT_KEYS}
    lim["NEEDS_GROWTH"] = np.maximum(g_max - np.maximum(already[o1], already[o2]), 0.0)
    rule_names = np.asarray(rl.rule).astype(str)
    no_frame = np.zeros(N, bool)
    for i in range(N):
        depths = REQUIRED_DEPTHS.get(rule_names[i], (MID,))
        if not depths:
            continue
        room = meas.room[i, list(depths)]
        cap = meas.cap[i, list(depths)]
        if not np.isfinite(cap).all():
            no_frame[i] = True
            continue
        lim["EXITS_MATERIAL"][i] = max(0.0, float(np.min(room)) - MATERIAL_LEFT_MM)
    lim["NO_FRAME"][no_frame] = 0.0
    rho = concave_radius(frames, body_V)
    lim["CONCAVE"] = np.maximum(np.minimum(CONCAVE_SHARE * rho[o1] - already[o1], CONCAVE_SHARE * rho[o2] - already[o2]),
                                0.0)
    if obstacles is not None and obstacles.n_parts:
        E = frames.depth_points()
        edge = meas.edge if meas.edge is not None else np.zeros((N, len(DEPTHS)))
        idx = np.flatnonzero(fail)
        if len(idx):
            O = E[idx].reshape(-1, 3)
            D = np.repeat(frames.u[idx], len(DEPTHS), axis=0)
            res = line_intervals(obstacles, O, D, np.repeat(frames.t[idx], len(DEPTHS), axis=0))
            for q, i in enumerate(idx.tolist()):
                best = np.inf
                for j in range(len(DEPTHS)):
                    per = res.intervals[q * len(DEPTHS) + j]
                    U = merge(per)
                    best = min(best, _first_entry(U, float(edge[i, j])))
                if np.isfinite(best):
                    lim["OTHER_PART"][i] = max(0.0, best - OTHER_PART_CLEARANCE_MM)
    bstr = base.astype(str)
    visible = bstr == "VISIBLE_BY_DESIGN"
    unm = bstr == "UNMEASURED"
    # a node that owns a visible-by-design point never grows (it would extend an exposed edge), nor one that owns an
    # UNMEASURED point (never a pass, never moved). 'Owns' = is the NEARER node: a halfway point is nearer to neither,
    # so it holds neither end, and a point is held when every node owning it is (review fix round 2: holding both
    # ends of a halfway visible point made the nylor fixture's channel-end nodes stop short, TRANSITION 6/161 > 3%,
    # where the stage-1 rule held one end depending on the loop's direction)
    node_held = np.zeros(nv, bool)
    node_held[o1[(visible | unm) & (o1 == o2)]] = True
    lim["HELD"] = np.where(node_held[o1] & node_held[o2], 0.0, np.inf)

    # --- point growth and UNHIDEABLE ------------------------------------------------------------------------
    pre_keys = ("NEEDS_GROWTH", "EXITS_MATERIAL", "OTHER_PART", "CONCAVE", "NO_FRAME", "HELD")
    pre_stack = np.stack([lim[k] for k in pre_keys])
    pre = pre_stack.min(0)
    pre_reason = np.asarray(pre_keys, dtype=object)[pre_stack.argmin(0)]
    node_cap = np.full(nv, np.inf)
    node_cap_reason = np.full(nv, "", dtype=object)
    for i in np.argsort(pre, kind="stable").tolist():           # smallest limit of each node, with its reason
        for k in {int(o1[i]), int(o2[i])}:
            if pre[i] < node_cap[k]:
                node_cap[k], node_cap_reason[k] = pre[i], pre_reason[i]
    cap_i = np.minimum(pre, np.minimum(node_cap[o1], node_cap[o2]))
    g_try = np.where(fail, np.clip(np.minimum(np.nan_to_num(need, nan=0.0), cap_i), 0.0, None), 0.0)
    hide_lim = _unhide_limits(frames, backend, lens_part, fail, g_try)
    lim["UNHIDEABLE"] = hide_lim
    allowed = np.minimum(cap_i, hide_lim)
    growth = np.where(fail, np.clip(np.minimum(np.nan_to_num(need, nan=0.0), allowed), 0.0, None), 0.0)
    limit = np.minimum(np.stack([lim[k] for k in pre_keys]).min(0), hide_lim)
    clamped = fail & (np.nan_to_num(need, nan=np.inf) > allowed + 1e-12)

    # --- verdicts -------------------------------------------------------------------------------------------
    verdict = base.copy()
    reason = np.full(N, "", dtype=object)
    reached = np.isfinite(v) & (v + growth >= p_mm - TOL_MM)
    front_need = (np.asarray(rl.front_required, bool) & ~np.asarray(rl.front_ok, bool)
                  if rl.front_required is not None else np.zeros(N, bool))
    blocked = fail & (~reached | (front_need & ~(growth > 0)) | no_frame)
    for i in np.flatnonzero(blocked).tolist():
        if no_frame[i]:
            reason[i] = "NO_FRAME"
            continue
        # the first clamp in §4 step 4's order that alone leaves the value below pass names the cause (an aperture
        # 2.5 mm too large is NEEDS_GROWTH, although its grown corner is also unhideable); else the binding one
        order = (("NEEDS_GROWTH", lim["NEEDS_GROWTH"][i]), ("EXITS_MATERIAL", lim["EXITS_MATERIAL"][i]),
                 ("OTHER_PART", lim["OTHER_PART"][i]), ("CONCAVE", lim["CONCAVE"][i]),
                 ("UNHIDEABLE", min(hide_lim[i], lim["HELD"][i])))
        named = [k for k, lv in order if np.isfinite(v[i]) and v[i] + lv < p_mm[i] - TOL_MM]
        if named:
            reason[i] = named[0]
            continue
        cands = [(lim[k][i], k) for k in pre_keys] + [(hide_lim[i], "UNHIDEABLE")]
        for k in sorted({int(o1[i]), int(o2[i])}):
            if np.isfinite(node_cap[k]):
                cands.append((node_cap[k], str(node_cap_reason[k])))
        r_ = min(cands, key=lambda c: c[0])[1]
        reason[i] = "UNHIDEABLE" if r_ == "HELD" else r_
    if ft.exposed == "visible_by_design":
        held = lim["HELD"] == 0.0
        unh = blocked & (reason.astype(str) == "UNHIDEABLE") & ~held
        verdict[unh] = "VISIBLE_BY_DESIGN"
        blocked &= ~unh
    verdict[blocked] = "BLOCKED"
    vis_idx = np.flatnonzero(verdict.astype(str) == "VISIBLE_BY_DESIGN")
    if len(vis_idx):
        cand = np.flatnonzero(blocked & np.isfinite(v) & (v >= 0) & np.asarray(meas.covered_front, bool))
        if len(cand):
            d = np.abs(frames.s[cand, None] - frames.s[None, vis_idx])
            d = np.minimum(d, frames.loop_mm - d).min(1)
            tr = cand[d <= rules.TRANSITION_MM]
            verdict[tr] = "TRANSITION"
            blocked[tr] = False
    grow_pts = fail & ~blocked & (verdict.astype(str) == "FAIL")
    node_g = halfway_growth(nv, o1, o2, grow_pts, growth)
    node_target = np.full(nv, -np.inf)
    for o in (o1, o2):
        np.maximum.at(node_target, o[grow_pts], target[grow_pts])
    node_g = np.minimum(node_g, node_cap)
    node_g = np.where(node_g > 0, node_g, 0.0)
    node_target = np.where(np.isfinite(node_target), node_target, np.nan)

    # --- gates ----------------------------------------------------------------------------------------------
    gate, msg = None, ""
    nreq = int(req.sum())
    nblk = int((verdict.astype(str) == "BLOCKED").sum())
    cls = meas.cls.astype(str)
    if ft.full_rim:
        eb = int(np.isin(cls, ("EXPOSED", "BACKED")).sum())
        if eb > rules.FULLRIM_GATE_SHARE * N:
            gate = "E_FULLRIM_EXPOSED"
            msg = (f"{eb} of {N} stations are EXPOSED or BACKED on a full-rim frame type (limit 2%): the outline, "
                   f"the parts passed or the frame type is wrong")
    if gate is None and nreq and nblk > BLOCKED_GATE_SHARE * nreq:
        gate = "E_TOO_MANY_BLOCKED"
        by = {k: int(((verdict.astype(str) == "BLOCKED") & (reason.astype(str) == k)).sum()) for k in REASONS}
        msg = (f"{nblk} of {nreq} required stations are BLOCKED (limit 20%): "
               + ", ".join(f"{k} {c}" for k, c in by.items() if c))
    if gate is not None:
        node_g[:] = 0.0
    return SeatPlan(verdict, reason, clamped, need, target, limit, growth, node_g, node_target, np_pt, gate, msg,
                    lim)


def halfway_growth(nv: int, o1: np.ndarray, o2: np.ndarray, grow: np.ndarray, growth: np.ndarray) -> np.ndarray:
    """Node growth from point growth (§4 step 3), independent of the loop's direction (every step below treats the
    two ends of a segment alike and runs on all points at once, so no order matters).

    1. A point owned by one node sets that node to at least its growth (the largest wins): 'max need of its points'.
    2. A point exactly halfway along its segment (o1 != o2) is owned by both ends. A node none of whose own points
       grows takes it in full: it is that node's only reason to move, as for any point it owns (a failing run that
       starts at a halfway point starts growing at that node, the §4 step 3 run-end ramp).
    3. Where both ends grow for points of their own, the halfway point moves with their mean, so it needs
       (g_a + g_b) / 2 >= its growth; both ends are raised by any shortfall (each by the larger of its two segments').

    Measured (review fix round 2): the stage-1 rule (a halfway point belonged to the segment's START) grew FT q001's
    left lens and its mirrored right differently by up to 0.054 mm (16 vertices > 1e-3 mm) and seated the metal
    fixture while refusing its mirror (a wall face rotated 35.5 deg at the nasal run end). Full growth to both ends
    everywhere overshoots the metal fixture's -0.30 / 0.10 mm junction inside the run (a node with 0.348 mm of need
    grew 0.536 and read 0.611, beyond §10 P4's 0.44-0.47); the mean alone leaves the run-end node at 0 and refuses
    the metal fixture on both sides (37.0 deg). This rule: left = mirrored right within 6e-5 mm on q001 (the hide's
    own difference), metal and its mirror seated identically (former failures 0.370-0.455)."""
    node_g = np.zeros(nv)
    one = grow & (o1 == o2)
    np.maximum.at(node_g, o1[one], growth[one])
    tie = np.flatnonzero(grow & (o1 != o2))
    if not len(tie):
        return node_g
    own = node_g.copy()
    for o in (o1, o2):
        alone = own[o[tie]] == 0
        np.maximum.at(node_g, o[tie][alone], growth[tie][alone])
    both = tie[(own[o1[tie]] > 0) & (own[o2[tie]] > 0)]
    short = np.maximum(growth[both] - 0.5 * (node_g[o1[both]] + node_g[o2[both]]), 0.0)
    raise_ = np.zeros(nv)
    np.maximum.at(raise_, o1[both], short)
    np.maximum.at(raise_, o2[both], short)
    return node_g + raise_


def _unhide_limits(frames: Frames, backend, lens_part: int, fail: np.ndarray, g_try: np.ndarray) -> np.ndarray:
    """§4 step 4 UNHIDEABLE: the largest of g, 0.9 g ... 0.1 g at which the grown front corner (moved g along u,
    the cover line starting 0.10 mm inside it) is covered along +n by frame within 6 mm, or inside frame; 0 when
    none (or when g = 0 and the corner is not covered). inf for points that do not grow."""
    N = len(frames)
    out = np.full(N, np.inf)
    idx = np.flatnonzero(fail)
    if not len(idx):
        return out
    fr = np.asarray(UNHIDE_FRACTIONS)
    inset = HIDDEN_MARGIN_MM + COVER_NUDGE_MM
    g = g_try[idx]
    G = g[:, None] * fr[None, :]
    G[g <= 0] = 0.0
    O = (frames.P[idx, None, :] + (G[..., None] - inset) * frames.u[idx, None, :]).reshape(-1, 3)
    D = np.repeat(frames.n[idx], len(fr), axis=0)
    Ax = np.repeat(frames.t[idx], len(fr), axis=0)
    res = line_intervals(backend, O, D, Ax)
    for q, i in enumerate(idx.tolist()):
        best = 0.0
        for j in range(len(fr)):
            per = res.intervals[q * len(fr) + j][:lens_part]
            c = cover_along(per)
            if np.isfinite(c) and c <= COVER_REACH_MM:
                best = float(G[q, j])
                break
        if g[q] <= 0:
            per = res.intervals[q * len(fr)][:lens_part]
            c = cover_along(per)
            out[i] = np.inf if (np.isfinite(c) and c <= COVER_REACH_MM) else 0.0
        else:
            out[i] = best
    return out


# --------------------------------------------------------------------------------------------------- lift plan
@dataclass
class LiftPlan:
    selected: np.ndarray           # (N,) stations in a range with `under` material in the window
    rigid: bool                    # uniform rings: rigid ring moves
    ring_of: np.ndarray            # (nv_e,) ring index per eyewire (welded) vertex, -1 when none
    ring_s: np.ndarray             # (R,) ring arc parameter on the lens loop
    ring_station: np.ndarray       # (R,) nearest measurement point
    lam: np.ndarray                # (R,) lambda* (nan: not selected)
    blocked: np.ndarray            # (R,) bool: no lift <= lift_max buries the ring
    vertex_s: np.ndarray           # (nv_e,) arc parameter used for each vertex (its ring's, or its own)
    lift_vertex: np.ndarray        # (nv_e,) L at each vertex
    disp: np.ndarray               # (nv_e, 3) eyewire displacement
    node_g: np.ndarray             # (nv,) lens node growth = L(s_node)
    slope: float                   # max |dL/ds| between consecutive rings / nodes
    strain: float | None = None    # per-vertex mode: largest relative edge-length change
    gate: str | None = None
    gate_message: str = ""
    cover_rings: int = 0           # rings lifted only because an eyewire edge from them spans a selected station
    ring_lift: np.ndarray | None = None    # (R,) L at each ring
    node_s: np.ndarray | None = None       # (nv,) arc parameter of each lens node
    loop_mm: float = 0.0


def ring_components(Vw: np.ndarray, edges: np.ndarray, t_of_edge: np.ndarray) -> tuple[np.ndarray, bool]:
    """(ring index per vertex (-1 none), uniform) from edges with |direction . t| < 0.5 (§6 step 2)."""
    E = np.asarray(edges, np.int64).reshape(-1, 2)
    d = Vw[E[:, 1]] - Vw[E[:, 0]]
    d /= np.maximum(np.linalg.norm(d, axis=1, keepdims=True), 1e-30)
    ring = np.abs((d * t_of_edge).sum(1)) < RING_TANGENT_DOT
    lab = geom.label_components(len(Vw), E[ring, 0], E[ring, 1])
    used = np.zeros(len(Vw), bool)
    used[E[ring].reshape(-1)] = True
    roots, inv, cnt = np.unique(lab, return_inverse=True, return_counts=True)
    ring_of = np.where(used, inv, -1)
    # renumber rings 0..R-1 over used vertices only
    ids = np.unique(ring_of[ring_of >= 0])
    remap = np.full(int(ring_of.max()) + 2 if len(ids) else 1, -1, np.int64)
    remap[ids] = np.arange(len(ids))
    ring_of = np.where(ring_of >= 0, remap[np.maximum(ring_of, 0)], -1)
    sizes = np.bincount(ring_of[ring_of >= 0]) if len(ids) else np.zeros(0, np.int64)
    uniform = bool(len(sizes) >= 3 and (ring_of >= 0).all() and (sizes == sizes[0]).all() and sizes[0] >= 3)
    return ring_of, uniform


def circular_mean(s: np.ndarray, L: float) -> float:
    a = 2 * np.pi * np.asarray(s) / L
    m = np.arctan2(np.sin(a).mean(), np.cos(a).mean())
    return float((m % (2 * np.pi)) * L / (2 * np.pi))


def circular_interp(s_query: np.ndarray, s_src: np.ndarray, values: np.ndarray, L: float) -> np.ndarray:
    """Piecewise-linear interpolation of values given at arc parameters s_src on a closed loop of length L."""
    order = np.argsort(s_src, kind="stable")
    xs = np.asarray(s_src, float)[order]
    ys = np.asarray(values, float)[order]
    xs = np.r_[xs[-1] - L, xs, xs[0] + L]
    ys = np.r_[ys[-1], ys, ys[0]]
    return np.interp(np.mod(s_query, L), xs, ys)


def lift_field(s_query: np.ndarray, s_src: np.ndarray, lam: np.ndarray, L: float,
               ramp: float = LIFT_RAMP_MM) -> np.ndarray:
    """§6 step 4: L(s) = max_j lam_j (1 - smoothstep(|s - s_j| / ramp)), circular arc distance."""
    out = np.zeros(len(s_query))
    ok = np.isfinite(lam) & (lam > 0)
    if not ok.any():
        return out
    d = np.abs(np.asarray(s_query)[:, None] - np.asarray(s_src)[ok][None, :])
    d = np.minimum(d, L - d)
    return (lam[ok][None, :] * (1.0 - smoothstep(d / ramp))).max(1)


def _iv_intersect(A: list, B: list) -> list:
    out, i, j = [], 0, 0
    while i < len(A) and j < len(B):
        lo, hi = max(A[i][0], B[j][0]), min(A[i][1], B[j][1])
        if lo <= hi:
            out.append((lo, hi))
        if A[i][1] < B[j][1]:
            i += 1
        else:
            j += 1
    return out


def plan_lift(frames: Frames, meas: Measurement, body_V: np.ndarray, x: np.ndarray, ranges, under_idx: list,
              eyewire_V: np.ndarray, eyewire_edges: np.ndarray, under_backend, *, margin: float = LIFT_MARGIN_MM,
              ramp: float = LIFT_RAMP_MM, lift_max: float = LIFT_MAX_MM, path_dn=None) -> LiftPlan:
    """§6 steps 1-4. `meas` measures the held lens against the frame parts (`under_idx`: the indices of the `under`
    parts among them); `eyewire_V`/`eyewire_edges`: the eyewire's welded vertices and polygon edges (asset mm);
    `under_backend`: a backend over the `under` parts only."""
    N = len(frames)
    loop = frames.loop
    P = body_V[loop]
    seg = np.linalg.norm(np.roll(P, -1, 0) - P, axis=1)
    L = float(seg.sum())
    # 1. selected stations: x in a range and `under` material in the §6 window of the edge on the mid-depth line
    inr = rules.in_ranges(x, ranges)
    lo, hi = UNDER_WINDOW_MM
    selected = np.zeros(N, bool)
    for k in np.flatnonzero(inr).tolist():
        per = meas.mid_parts[k]
        if per is None:
            continue
        e = float(meas.edge[k, MID]) if meas.edge is not None else 0.0
        for ui in under_idx:
            iv = per[ui]
            if iv is not None and len(iv) and ((iv[:, 1] >= e + lo) & (iv[:, 0] <= e + hi)).any():
                selected[k] = True
                break
    # 2. rings
    E = np.asarray(eyewire_edges, np.int64).reshape(-1, 2)
    mid = 0.5 * (eyewire_V[E[:, 0]] + eyewire_V[E[:, 1]])
    s_mid, _, _ = geom.nearest_on_polyline(P, mid)
    k_mid = _nearest_station(frames, s_mid)
    ring_of, uniform = ring_components(eyewire_V, E, frames.t[k_mid])
    s_v, _, _ = geom.nearest_on_polyline(P, eyewire_V)
    if uniform:
        R = int(ring_of.max()) + 1
        ring_s = np.array([circular_mean(s_v[ring_of == r], L) for r in range(R)])
        vertex_s = ring_s[ring_of]
        members = [np.flatnonzero(ring_of == r) for r in range(R)]
    else:                                          # per vertex: every vertex is its own 'ring'
        R = len(eyewire_V)
        ring_s = s_v.copy()
        vertex_s = s_v.copy()
        members = [np.array([i]) for i in range(R)]
        ring_of = np.arange(R)
    ring_station = _nearest_station(frames, ring_s)
    # 3. lambda* per lifted ring, along the path the ring moves: p + tau u + dn(tau) n (path_dn: the lens surface's
    #    own normal offset, §4 step 6; None = straight along u). Solved by fixed-point iteration on dn.
    #    Lifted rings: those nearest to a selected station, AND every ring holding an end of an eyewire edge that
    #    spans a selected station (review fix round 1): the eyewire between two rigid rings is their linear blend,
    #    so a selected station past the last selected ring (q001 [11.6, 30.0]: x 29.38 and 29.82) sat on a section
    #    lifted only part way and still showed 0.2-0.375 mm of eyewire. Both bracketing rings get their own lambda*,
    #    so the lift is full across the whole range.
    lam = np.full(R, np.nan)
    blocked = np.zeros(R, bool)
    near_r = np.flatnonzero(selected[ring_station])
    cover_r = _covering_rings(vertex_s, ring_of, E, frames.s[selected], L)
    sel_r = np.union1d(near_r, cover_r).astype(np.int64)
    extra_r = np.setdiff1d(cover_r, near_r)

    def solve(shift: np.ndarray):
        lam_ = np.full(R, np.nan)
        blk = np.zeros(R, bool)
        O, D, Ax, owner = [], [], [], []
        for q, r in enumerate(sel_r.tolist()):
            k = ring_station[r]
            u, n, t = frames.u[k], frames.n[k], frames.t[k]
            for vi in members[r].tolist():
                p = eyewire_V[vi] + shift[q] * n
                for dn in (0.0, margin, -margin):
                    O.append(p + dn * n)
                    D.append(u)
                    Ax.append(t)
                    owner.append(r)
        res = line_intervals(under_backend, np.array(O), np.array(D), np.array(Ax))
        owner = np.array(owner)
        for r in sel_r.tolist():
            feas = [(0.0, lift_max)]
            for li in np.flatnonzero(owner == r).tolist():
                U = merge(res.intervals[li])
                shrunk = [(float(a) + margin, float(b) - margin) for a, b in U if b - a > 2 * margin]
                feas = _iv_intersect(feas, shrunk)
                if not feas:
                    break
            if feas:
                lam_[r] = feas[0][0] if feas[0][0] > LIFT_EPS_MM else 0.0
            else:
                blk[r] = True
        return lam_, blk
    if len(sel_r):
        shift = np.zeros(len(sel_r))
        for _ in range(LIFT_PATH_ITERATIONS):
            lam, blocked = solve(shift)
            if path_dn is None:
                break
            tau = np.nan_to_num(lam[sel_r], nan=0.0)
            new_shift = np.asarray(path_dn(ring_s[sel_r], tau), float)
            if np.max(np.abs(new_shift - shift)) < 1e-5:
                break
            shift = new_shift
    # 4. the lift field at the rings / vertices and at the lens nodes
    lam_src = np.where(blocked, np.nan, lam)
    lift_ring = lift_field(ring_s, ring_s, lam_src, L, ramp)
    lift_vertex = lift_ring[ring_of] if uniform else lift_field(vertex_s, ring_s, lam_src, L, ramp)
    st_v = _nearest_station(frames, vertex_s)
    disp = lift_vertex[:, None] * frames.u[st_v]
    if path_dn is not None and (lift_vertex > 0).any():
        mv = lift_vertex > 0
        if uniform:
            dn_r = np.zeros(R)
            rr = np.flatnonzero(lift_ring > 0)
            dn_r[rr] = path_dn(ring_s[rr], lift_ring[rr])
            dn_v = dn_r[ring_of]
        else:
            dn_v = np.zeros(len(vertex_s))
            dn_v[mv] = path_dn(vertex_s[mv], lift_vertex[mv])
        disp = disp + dn_v[:, None] * frames.n[st_v]
    disp[lift_vertex <= 0] = 0.0
    np_pt = node_points(frames, body_V)
    s_node = frames.s[np_pt]
    if uniform:
        # the lens follows the eyewire as it actually moves: between two rigid rings the eyewire surface is the
        # linear blend of their lifts, so a node takes the ring lifts interpolated at its arc parameter (the smooth
        # L(s) itself differs from that blend by up to 0.025 mm on q001's ramps, beyond §6 step 5's 0.02)
        node_g = circular_interp(s_node, ring_s, lift_ring, L)
    else:
        node_g = lift_field(s_node, ring_s, lam_src, L, ramp)
    slope = lift_slope(ring_s, lift_ring, s_node, node_g, L)
    strain = None
    if not uniform:
        e0 = np.linalg.norm(eyewire_V[E[:, 1]] - eyewire_V[E[:, 0]], axis=1)
        V1 = eyewire_V + disp
        e1 = np.linalg.norm(V1[E[:, 1]] - V1[E[:, 0]], axis=1)
        strain = float(np.max(np.abs(e1 - e0) / np.maximum(e0, 1e-12))) if len(E) else 0.0
    gate, msg = None, ""
    if not selected.any():
        gate, msg = "E_NOT_UNDER", "no station in the hidden ranges has `under` material within [-1.5, 4] mm of the edge"
    elif blocked.any():
        gate = "E_BLOCKED"
        msg = (f"{int(blocked.sum())} eyewire rings cannot be buried with a lift <= {lift_max} mm (the brow does not "
               f"cover the eyewire there): check the ranges and the `under` part")
        edge_blk = np.intersect1d(np.flatnonzero(blocked), extra_r)
        if len(edge_blk):
            xs = sorted({round(float(x), 2) for x in interp_loop(P, np.r_[0.0, np.cumsum(seg)][:-1], L,
                                                                  ring_s[edge_blk])[:, 0]})
            msg += (f"; {len(edge_blk)} of them are the rings just beyond the end of a range (asset x {xs}): the "
                    f"range ends where the brow no longer covers the eyewire's next ring, so end it inside the brow")
    return LiftPlan(selected, uniform, ring_of, ring_s, ring_station, lam, blocked, vertex_s, lift_vertex, disp,
                    node_g, slope, strain, gate, msg, cover_rings=int(len(extra_r)), ring_lift=lift_ring,
                    node_s=s_node, loop_mm=L)


def lift_slope(ring_s, ring_lift, s_node, node_g, L: float) -> float:
    """max |dL/ds| between consecutive rings and nodes along the loop (§2 'Eyewire margin / ramp')."""
    order = np.argsort(np.r_[ring_s, s_node], kind="stable")
    sv = np.r_[ring_s, s_node][order]
    lv = np.r_[ring_lift, node_g][order]
    if len(sv) < 2:
        return 0.0
    ds = np.diff(np.r_[sv, sv[0] + L])
    dl = np.abs(np.diff(np.r_[lv, lv[0]]))
    return float(np.max(np.where(ds > 1e-9, dl / np.maximum(ds, 1e-9), 0.0)))


def _covering_rings(vertex_s: np.ndarray, ring_of: np.ndarray, edges: np.ndarray, s_sel: np.ndarray,
                    L: float, eps: float = 1e-6) -> np.ndarray:
    """Rings holding an end of an eyewire edge whose (shorter) arc span contains a selected station strictly inside
    it: the stations whose eyewire section that edge forms (ring edges span nothing)."""
    E = np.asarray(edges, np.int64).reshape(-1, 2)
    s_sel = np.asarray(s_sel, float)
    if not len(E) or not len(s_sel):
        return np.zeros(0, np.int64)
    a, b = vertex_s[E[:, 0]], vertex_s[E[:, 1]]
    d = np.mod(b - a, L)
    fwd = d <= 0.5 * L
    start = np.where(fwd, a, b)
    length = np.where(fwd, d, L - d)
    span = np.zeros(len(E), bool)
    for c0 in range(0, len(s_sel), 256):
        q = np.mod(s_sel[None, c0:c0 + 256] - start[:, None], L)
        span |= ((q > eps) & (q < length[:, None] - eps)).any(1)
    return np.unique(ring_of[E[span].reshape(-1)])


def _nearest_station(frames: Frames, s: np.ndarray) -> np.ndarray:
    s = np.asarray(s, float)
    d = np.abs(s[:, None] - frames.s[None, :])
    d = np.minimum(d, frames.loop_mm - d)
    return d.argmin(1)
