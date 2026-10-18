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
- every clamp keeps §4 step 4's threshold: a clamp that leaves v below pass (less the verdict's 0.005 mm tolerance)
  makes the point BLOCKED. (Proof review fix round 1: the fix round before raised the capacity clamps' floor to
  pass + 0.05 so that the §10 P4 narrow eyewire, whose 0.5 mm web holds at most web - 0.20 = 0.30 mm = pass, read
  BLOCKED(EXITS_MATERIAL); that was a binding-rule change shaped by fixture outcomes, so it is withdrawn and the
  fixture's knife edge is reported to the owner instead);
- fold limit (§4 step 11, `fold_slopes` / `fold_total`): node growth may differ between neighbouring nodes by at most
  tan(30 deg) x the segment's length across its rung, the rotation the post-check allows a wall face between them;
  growing nodes are raised (never past their limits) or, next to a node that does not grow, lowered to meet it, and
  a point the lowering leaves below pass is BLOCKED(FOLD) (a reason code §4 step 4 does not name: the fold limit is
  the post-check's 30 deg turned into a clamp). Measured (proof fix round): Prada's growth stepped 0 -> 0.068 mm
  across a 0.044 mm rim segment at a run start (a wall face rotated 56.8 deg) and 0.4348 -> 0.4304 mm across a
  0.0046 mm one inside a run (42.8 deg): the 22 sub-0.05 mm segments of its outline. FOLD blocks SPREAD (proof review
  fix round 2): a point BLOCKED(FOLD) gets no growth, so its node may stop, and the next node is then lowered to one
  segment's slope, and so on: where a run's need exceeds the slopes between a node that cannot grow and the rest of
  the run, the whole run is left ungrown. The plan records the fold pass that blocked each point
  (`SeatPlan.fold_generation`; `counts()['fold_blocks']`: `direct` = the first pass, `knock_on` = later passes), so a
  report never presents a cascade as independent blocks (the narrow-eyewire fixture: 2 EXITS_MATERIAL blocks at the
  nasal/top junction leave the entire nasal run BLOCKED(FOLD), most of them knock-on);
- gates (§4 step 5): > 20% of required points BLOCKED -> E_TOO_MANY_BLOCKED; a full-rim type with > 2% EXPOSED/BACKED
  -> E_FULLRIM_EXPOSED; any required point BLOCKED(CONCAVE) -> E_CONCAVE (not in §4 step 5, which names two gates:
  §10 P4 lists 'notch CONCAVE' among the refusals; the outline turns inward tighter than the growth it needs, so the
  grown rim would fold over itself or, with its neighbours grown and the notch not, leave a pinched step; owner to
  confirm). Nothing moves on a gate.
- correction plans (deform.grow_body, `residual=True`): the node points of moved nodes inside their runs that read
  below their planned target grow by the residual, aiming `aim_mm` beyond it, spread over the nodes whose wall the
  station's depth lines cross (`reach`), so the deformation reaches the target along the curved surface (proof fix
  round: §4 step 7 corrected only points below pass, and INVU's former failures stopped at 0.4249 for 0.45).

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
FACE_ROTATION_MAX_DEG = 30.0       # §4 step 11: a moved face rotates <= 30 deg (deform's post-check; the planner keeps
                                   # node growth inside it, `fold_slopes`)
TOL_MM = rules.PASS_TOLERANCE_MM
REASONS = ("NEEDS_GROWTH", "EXITS_MATERIAL", "OTHER_PART", "CONCAVE", "UNHIDEABLE", "NO_FRAME", "FOLD")
LIMIT_KEYS = REASONS + ("HELD",)
REQUIRED_DEPTHS = {"deep": tuple(range(len(DEPTHS))), "thin": (MID,), "flats": (0, len(DEPTHS) - 1),
                   "apex": tuple(range(len(DEPTHS))), "report": ()}

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


def run_delta(fail: np.ndarray, s: np.ndarray, loop_mm: float) -> np.ndarray:
    """Per point: arc distance to the nearer end of its failing run (§4 step 3's delta; inf for a run covering the
    whole loop, nan outside runs)."""
    out = np.full(len(fail), np.nan)
    for run in runs(fail):
        if len(run) == len(fail):
            out[run] = np.inf
            continue
        ss = s[run]
        out[run] = np.minimum((ss - ss[0]) % loop_mm, (ss[-1] - ss) % loop_mm)
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
    fold_slope: np.ndarray | None = None             # (nv,) largest growth difference across each rim segment
                                                     # (node k -> k + 1) the face-rotation limit allows
    concave_rho: np.ndarray | None = None            # (nv,) the outline's concave radius per node (inf: convex)
    fold_generation: np.ndarray | None = None        # (N,) the fold pass that blocked the point (FOLD), 0 otherwise:
                                                     # 1 = DIRECT (the fold limit against the plan's node growth, beside
                                                     # a node that does not grow or grows less for another reason);
                                                     # >= 2 = KNOCK-ON (blocked only because an earlier FOLD block
                                                     # stopped its neighbour node: FOLD blocks spread along a run)
    disp: object = field(default=None, repr=False, compare=False)   # deform.refine_fold: (frames, V, node_g,
                                                     # Displacement) it measured this plan with (grow_body reuses it)

    @property
    def moves(self) -> bool:
        return self.gate is None and bool((self.node_g > 0).any())

    def counts(self) -> dict:
        v = self.verdict.astype(str)
        out = {k: int((v == k).sum()) for k in rules.VERDICTS if (v == k).any()}
        r = self.reason.astype(str)
        blocked = {k: int(((v == "BLOCKED") & (r == k)).sum()) for k in REASONS}
        out["blocked_by"] = {k: c for k, c in blocked.items() if c}
        fb = self.fold_blocks()
        if fb is not None:
            out["fold_blocks"] = fb
        return out

    def fold_blocks(self) -> dict | None:
        """BLOCKED(FOLD) points split by how they arose (proof review fix round 2): `direct` = blocked in the first
        fold pass (the fold limit against the plan's node growth), `knock_on` = blocked in a later pass only because
        an earlier FOLD block stopped a neighbour node, `passes` = the last pass that blocked one. None when no
        point is BLOCKED(FOLD)."""
        if self.fold_generation is None:
            return None
        m = (self.verdict.astype(str) == "BLOCKED") & (self.reason.astype(str) == "FOLD")
        if not m.any():
            return None
        g = np.asarray(self.fold_generation)[m]
        return {"direct": int((g == 1).sum()), "knock_on": int((g >= 2).sum()), "passes": int(g.max())}


def plan_growth(frames: Frames, meas: Measurement, rl: rules.Rules, ft: rules.FrameType, backend, lens_part: int,
                body_V: np.ndarray, obstacles=None, g_max: float = G_MAX_MM, already: np.ndarray | None = None,
                fail_only: np.ndarray | None = None, target_override: np.ndarray | None = None,
                residual: bool = False, aim_mm: float = 0.0, fold_slope: np.ndarray | None = None,
                concave_rho: np.ndarray | None = None, reach: dict | None = None,
                residual_nodes: np.ndarray | None = None, cache: dict | None = None) -> SeatPlan:
    """Plan growth for one lens body.

    backend: the measuring backend (frame parts, then the lens as part `lens_part`); obstacles: a backend over the
    non-frame parts the edge must not enter (other lenses, temples), or None. already: (nv,) growth applied in
    earlier passes (the correction pass counts it against g_max). fail_only: restrict growth to these points (the
    correction pass grows only the stations it moved). target_override: per-point targets (correction pass).
    residual: the correction pass also grows the node points of `fail_only` that read below their target (never
    BLOCKED: they already pass) by target + aim_mm - value. fold_slope: (nv,) the fold limit per rim segment of the
    ORIGINAL lens (the correction pass passes the first plan's: the post-check compares with the original faces).
    concave_rho: (nv,) the ORIGINAL outline's concave radius per node (correction pass: the moved outline's kinks are
    the growth's own, and its radius minus the growth already applied counted that growth twice; the metal fixture's
    nasal/top junction read 0.74 mm and blocked its correction).
    reach: {point: node indices} (correction pass, deform.station_reach): the nodes whose rungs bound the wall a
    node point's depth lines cross; a residual grows all of them (`_spread`) that are in `residual_nodes`.
    residual_nodes: (nv,) the nodes whose points the residual corrects: those >= RUN_END_RAMP_MM inside a failing run
    of the first plan (§10 grades the target there; a run-end point's ramp target sits next to a node that does not
    grow, and correcting it to that target pushed the metal fixture's nasal run-start node to 0.94 mm and 0.617).
    cache: a dict shared by calls that differ only in `fold_slope` (deform.refine_fold's replans): the point plan
    (`_point_plan`: verdicts, clamps, the unhide test, point growth) is computed once and only the fold limit and the
    node aggregation run again (proof review fix round 1, speed: each replan was a full plan). Keyed on the identity
    of every other argument, so a call with different inputs recomputes; results are bit-identical either way."""
    key = (id(frames), id(meas), id(rl), id(ft), id(backend), lens_part, id(body_V), id(obstacles), g_max, id(already),
           id(fail_only), id(target_override), residual, aim_mm, id(concave_rho), id(residual_nodes))
    if cache is not None and cache.get("key") == key:
        pp = cache["pre"]
    else:
        pp = _point_plan(frames, meas, rl, ft, backend, lens_part, body_V, obstacles, g_max, already, fail_only,
                         target_override, residual, aim_mm, concave_rho, residual_nodes)
        if cache is not None:
            # the cache holds references to the inputs it is keyed on (ids stay unique while they live)
            cache.update(key=key, pre=pp, inputs=(frames, meas, rl, ft, backend, body_V, obstacles, already,
                                                  fail_only, target_override, concave_rho, residual_nodes))
    hide_cache = pp.hide_cache
    pp = pp.fresh()
    N, nv, req, v, p_mm, fail_base, short, np_pt = pp.N, pp.nv, pp.req, pp.v, pp.p_mm, pp.fail_base, pp.short, pp.np_pt
    o1, o2, target, need, already, lim = pp.o1, pp.o2, pp.target, pp.need, pp.already, pp.lim
    rho, node_cap, growth, limit, clamped = pp.rho, pp.node_cap, pp.growth, pp.limit, pp.clamped
    verdict, reason, blocked, grow_pts = pp.verdict, pp.reason, pp.blocked, pp.grow_pts

    # --- node growth and the fold limit ---------------------------------------------------------------------
    S = fold_slopes(frames, body_V, np_pt) if fold_slope is None else np.asarray(fold_slope, float)
    cap_n = np.minimum(node_cap, np.maximum(g_max - already, 0.0))
    reach = reach if residual else None
    fold_gen = np.zeros(N, np.int64)
    fold_pass = 0
    while True:
        fold_pass += 1
        node_g = np.minimum(halfway_growth(nv, o1, o2, grow_pts, growth), node_cap)
        if reach is not None:
            sp = _spread(reach, grow_pts & short, growth, nv)
            if residual_nodes is not None:
                sp = np.where(np.asarray(residual_nodes, bool), sp, 0.0)
            raised = sp > node_g + 1e-12
            if raised.any():                         # a raised node's own grown corner must stay hidden (§4 step 4)
                pts_r = np.full(N, False)
                pts_r[np_pt[raised]] = True
                g_r = np.where(pts_r, sp[np.maximum(frames.node, 0)], 0.0)
                hk = (pts_r.tobytes(), g_r.tobytes())
                if hk not in hide_cache:
                    hide_cache[hk] = _unhide_limits(frames, backend, lens_part, pts_r, g_r)
                hl = hide_cache[hk]
                sp[raised] = np.minimum(sp[raised], hl[np_pt[raised]])
            node_g = np.minimum(np.maximum(node_g, sp), node_cap)
        node_g = np.where(node_g > 0, node_g, 0.0)
        # growing now; in a correction plan (fail_only) only the nodes an earlier pass moved: a correction never moves
        # a new node (a halfway point between a moved and an unmoved node would otherwise hand the unmoved one growth)
        movable = (already > 0) if fail_only is not None else (node_g > 0)
        hi = np.where(movable, already + cap_n, 0.0)
        total = fold_total(already + node_g, hi, S)
        new_g = np.maximum(total - already, 0.0)
        ach = np.where(o1 == o2, new_g[o1], 0.5 * (new_g[o1] + new_g[o2]))
        cut = grow_pts & (ach < growth - 1e-12)
        lim["FOLD"] = np.where(cut, ach, np.inf)
        newly = cut & fail_base & (v + ach < p_mm - TOL_MM)
        if not newly.any():
            node_g = new_g
            break
        fold_gen[newly] = fold_pass
        verdict[newly] = "BLOCKED"
        reason[newly] = "FOLD"
        blocked |= newly
        blocked &= ~_transition(verdict, newly, frames, v, meas)
        grow_pts &= ~newly
    limit = np.minimum(limit, lim["FOLD"])
    clamped |= np.isfinite(lim["FOLD"])
    node_target = np.full(nv, -np.inf)
    for o in (o1, o2):
        np.maximum.at(node_target, o[grow_pts], target[grow_pts])
    node_target = np.where(np.isfinite(node_target), node_target, np.nan)
    node_target = np.where(node_g > 0, node_target, np.nan)

    # --- gates ----------------------------------------------------------------------------------------------
    gate, msg = None, ""
    nreq = int(req.sum())
    vs = verdict.astype(str)
    rs = reason.astype(str)
    nblk = int((vs == "BLOCKED").sum())
    cls = meas.cls.astype(str)
    if ft.full_rim:
        eb = int(np.isin(cls, ("EXPOSED", "BACKED")).sum())
        if eb > rules.FULLRIM_GATE_SHARE * N:
            gate = "E_FULLRIM_EXPOSED"
            msg = (f"{eb} of {N} stations are EXPOSED or BACKED on a full-rim frame type (limit 2%): the outline, "
                   f"the parts passed or the frame type is wrong")
    if gate is None and nreq and nblk > BLOCKED_GATE_SHARE * nreq:
        gate = "E_TOO_MANY_BLOCKED"
        by = {k: int(((vs == "BLOCKED") & (rs == k)).sum()) for k in REASONS}
        msg = (f"{nblk} of {nreq} required stations are BLOCKED (limit 20%): "
               + ", ".join(f"{k} {c}" for k, c in by.items() if c))
        fb = _fold_split(vs, rs, fold_gen)
        if fb:
            msg += f" ({fb})"
    cc = (vs == "BLOCKED") & (rs == "CONCAVE") & req
    if gate is None and cc.any():
        gate = "E_CONCAVE"
        k = np.flatnonzero(cc)
        rmin = float(np.min(np.minimum(rho[o1[k]], rho[o2[k]])))
        need_k = np.nan_to_num(need[k], nan=0.0)
        xs = frames.P[k, 0]
        msg = (f"{len(k)} required stations are BLOCKED by CONCAVE (asset x {xs.min():.2f}..{xs.max():.2f}): the "
               f"outline turns inward with a radius of {rmin:.3f} mm, under twice the {need_k.max():.3f} mm of growth "
               f"they need, so the grown rim would fold over itself there (or step around a notch its neighbours "
               f"grow past): fix the lens outline, or the frame")
    if gate is not None:
        node_g[:] = 0.0
    return SeatPlan(verdict, reason, clamped, need, target, limit, growth, node_g, node_target, np_pt, gate, msg,
                    lim, S, rho, fold_gen)


def _fold_split(vs: np.ndarray, rs: np.ndarray, fold_gen: np.ndarray) -> str:
    """'FOLD: a direct, b knock-on over c passes' for a message ('' without FOLD blocks)."""
    m = (vs == "BLOCKED") & (rs == "FOLD")
    if not m.any():
        return ""
    g = fold_gen[m]
    return (f"FOLD: {int((g == 1).sum())} direct, {int((g >= 2).sum())} knock-on over {int(g.max())} fold passes "
            f"(a knock-on block is caused by an earlier FOLD block stopping its neighbour node)")


@dataclass
class _PointPlan:
    """The part of plan_growth that does not depend on the fold limit (`fold_slope`): verdicts, clamps, limits and
    point growth before the node aggregation. Cached across replans that differ only in `fold_slope`
    (deform.refine_fold); `fresh()` copies what the fold section changes in place."""
    N: int
    nv: int
    req: np.ndarray
    v: np.ndarray
    p_mm: np.ndarray
    fail_base: np.ndarray
    short: np.ndarray
    np_pt: np.ndarray
    o1: np.ndarray
    o2: np.ndarray
    target: np.ndarray
    need: np.ndarray
    already: np.ndarray
    lim: dict
    rho: np.ndarray
    node_cap: np.ndarray
    growth: np.ndarray
    limit: np.ndarray
    clamped: np.ndarray
    verdict: np.ndarray
    reason: np.ndarray
    blocked: np.ndarray
    grow_pts: np.ndarray
    hide_cache: dict = field(default_factory=dict)  # (points, growth) -> _unhide_limits result (the spread's test)

    def fresh(self) -> "_PointPlan":
        import dataclasses as _dc
        return _dc.replace(self, lim={k: a.copy() for k, a in self.lim.items()}, clamped=self.clamped.copy(),
                           verdict=self.verdict.copy(), reason=self.reason.copy(), blocked=self.blocked.copy(),
                           grow_pts=self.grow_pts.copy(), limit=self.limit.copy())


def _point_plan(frames, meas, rl, ft, backend, lens_part, body_V, obstacles, g_max, already, fail_only,
                target_override, residual, aim_mm, concave_rho, residual_nodes) -> _PointPlan:
    N = len(frames)
    nv = len(frames.loop)
    base = rules.verdicts(ft, meas, rl, frames.s, frames.loop_mm, transition=False).astype(object)
    req = np.asarray(rl.required, bool)
    v = np.asarray(rl.value, float)
    p_mm = np.asarray(rl.pass_mm, float)
    t_mm = np.asarray(rl.target_mm, float)
    fail_base = (base.astype(str) == "FAIL") & req
    if fail_only is not None:
        fail_base &= np.asarray(fail_only, bool)
    fail = fail_base.copy()
    short = np.zeros(N, bool)
    np_pt = node_points(frames, body_V)
    o1, o2 = frames.owners
    if residual and fail_only is not None and target_override is not None:
        # node points only (the point on each moved rim vertex measures that vertex's own move): a point between two
        # nodes moves with their interpolation, which the node rule (§4 step 3: a node takes the largest need of its
        # points) does not plan for; correcting those to the target raised the metal fixture's -0.30 / 0.10 junction
        # nodes to 0.477 / 0.468 (§10 P4: 0.44-0.47). Points below pass are corrected anywhere, as §4 step 7 says.
        on_node = np.zeros(N, bool)
        on_node[np_pt[np_pt >= 0]] = True
        if residual_nodes is not None:
            rn = np.asarray(residual_nodes, bool)
            on_node &= rn[o1] & rn[o2]
        with np.errstate(invalid="ignore"):
            short = (req & on_node & np.asarray(fail_only, bool) & np.isfinite(v) & np.isfinite(target_override)
                     & (v < target_override) & (base.astype(str) == "PASS"))
        fail |= short
    target = run_targets(fail, frames.s, frames.loop_mm, p_mm, t_mm)
    if target_override is not None:
        target = np.where(fail & np.isfinite(target_override), target_override, target)
    need = np.where(fail, target + (aim_mm if residual else 0.0) - v, np.nan)
    already = np.zeros(nv) if already is None else np.asarray(already, float)
    # a point exactly halfway along its loop segment belongs to both nodes (Frames.owners): its limits and target
    # count for both, a node-derived limit of the point is the tighter of the two, its growth goes through
    # halfway_growth and it holds neither node (below), so a lens and its mirror (whose loop runs the other way) are
    # planned alike

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
    rho = concave_radius(frames, body_V) if concave_rho is None else np.asarray(concave_rho, float)
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
    # §4 step 4: a point is reached when its growth takes it to pass (module docstring). Points the residual
    # correction grows already pass: never blocked
    reached = np.isfinite(v) & (v + growth >= p_mm - TOL_MM)
    front_need = (np.asarray(rl.front_required, bool) & ~np.asarray(rl.front_ok, bool)
                  if rl.front_required is not None else np.zeros(N, bool))
    blocked = fail_base & (~reached | (front_need & ~(growth > 0)) | no_frame)
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
    blocked &= ~_transition(verdict, blocked, frames, v, meas)
    grow_pts = fail & ~blocked & ((verdict.astype(str) == "FAIL") | short)
    return _PointPlan(N, nv, req, v, p_mm, fail_base, short, np_pt, o1, o2, target, need, already, lim, rho, node_cap,
                      growth, limit, clamped, verdict, reason, blocked, grow_pts)


def _spread(reach: dict, mask: np.ndarray, growth: np.ndarray, nv: int) -> np.ndarray:
    """(nv,) the largest growth of the points in `mask` whose reach (deform.station_reach) includes each node."""
    out = np.zeros(nv)
    for i, ks in reach.items():
        if mask[i] and growth[i] > 0:
            out[ks] = np.maximum(out[ks], growth[i])
    return out


def _transition(verdict: np.ndarray, blocked: np.ndarray, frames: Frames, v: np.ndarray,
                meas: Measurement) -> np.ndarray:
    """§4 step 4: a blocked point with v >= 0 and the front covered, within 3 mm (arc) of a visible-by-design point,
    is TRANSITION (set in `verdict`); returns the mask of those points."""
    out = np.zeros(len(verdict), bool)
    vis_idx = np.flatnonzero(verdict.astype(str) == "VISIBLE_BY_DESIGN")
    if not len(vis_idx):
        return out
    cand = np.flatnonzero(blocked & np.isfinite(v) & (v >= 0) & np.asarray(meas.covered_front, bool))
    if not len(cand):
        return out
    d = np.abs(frames.s[cand, None] - frames.s[None, vis_idx])
    d = np.minimum(d, frames.loop_mm - d).min(1)
    tr = cand[d <= rules.TRANSITION_MM]
    verdict[tr] = "TRANSITION"
    out[tr] = True
    return out


# ------------------------------------------------------------------------------------------------- fold limit
def fold_slopes(frames: Frames, body_V: np.ndarray, np_pt: np.ndarray,
                limit_deg: float = FACE_ROTATION_MAX_DEG) -> np.ndarray:
    """(nv,) the largest growth difference |g[k + 1] - g[k]| across each rim segment k -> k + 1 at which the wall
    face spanning it (front corners, rung to the back corners) rotates by `limit_deg`: growing node k + 1 by d more
    than node k turns the segment's edge e about the rung r by atan(d (u . m) / |e_perp|), with e_perp the part of e
    across the rung and m the face normal (e_perp x r). First order in d: deform's post-check measures the result."""
    loop = frames.loop
    nv = len(loop)
    if nv == 0:
        return np.zeros(0)
    P = body_V[loop]
    e = np.roll(P, -1, 0) - P
    i0 = np.maximum(np.asarray(np_pt, np.int64), 0)
    i1 = np.roll(i0, -1)

    def rung(i):
        r = frames.Pb[i] - frames.P[i]
        ln = np.linalg.norm(r, axis=1)
        bad = ~np.isfinite(ln) | (ln < 1e-12)
        r = np.where(bad[:, None], -frames.n[i], r / np.where(bad, 1.0, ln)[:, None])
        return r
    r = rung(i0) + rung(i1)
    r /= np.maximum(np.linalg.norm(r, axis=1, keepdims=True), 1e-300)
    ep = e - (e * r).sum(1, keepdims=True) * r
    L = np.linalg.norm(ep, axis=1)
    m = np.cross(ep, r)
    m /= np.maximum(np.linalg.norm(m, axis=1, keepdims=True), 1e-300)
    um = frames.u[i0] + frames.u[i1]
    um /= np.maximum(np.linalg.norm(um, axis=1, keepdims=True), 1e-300)
    w = np.abs((um * m).sum(1))
    with np.errstate(divide="ignore", invalid="ignore"):
        S = np.where(w > 1e-9, np.tan(np.radians(limit_deg)) * L / w, np.inf)
    return S


def _lipschitz(x: np.ndarray, S: np.ndarray, upper: bool) -> np.ndarray:
    """On the closed loop of nodes with segment weights S (S[k] between k and k + 1): upper=False, the largest
    function <= x with |f[k + 1] - f[k]| <= S[k] (min over j of x[j] + d(j, k)); upper=True, the smallest one >= x
    (max over j of x[j] - d(j, k)). d is the S-weighted arc distance; a shortest path on a cycle runs one way, so
    one sweep each way over two laps is exact."""
    f = np.asarray(x, float).copy()
    n = len(f)
    if n == 0:
        return f
    S = np.asarray(S, float)
    sign = -1.0 if upper else 1.0
    for k in range(2 * n):                                   # forward: k -> k + 1 over segment k
        i, j = k % n, (k + 1) % n
        c = f[i] + sign * S[i]
        if (c > f[j]) if upper else (c < f[j]):
            f[j] = c
    for k in range(2 * n, 0, -1):                            # backward: k + 1 -> k over segment k
        i, j = k % n, (k - 1) % n
        c = f[i] + sign * S[j]
        if (c > f[j]) if upper else (c < f[j]):
            f[j] = c
    return f


def fold_total(G: np.ndarray, hi: np.ndarray, S: np.ndarray) -> np.ndarray:
    """Total growth per node inside the fold limit: lower G to the largest S-Lipschitz function under `hi` (0 at
    nodes that do not move, their limits elsewhere), then raise it to the smallest S-Lipschitz function over that.
    The result lies in [min(G, U), U] with U = that largest function, so it never passes a node's limit, never moves
    a node with hi = 0, and differs from G only where G steps more steeply than the faces allow."""
    U = _lipschitz(np.minimum(hi, np.where(np.isfinite(hi), hi, np.inf)), S, upper=False)
    Gp = np.minimum(np.asarray(G, float), U)
    return np.minimum(_lipschitz(Gp, S, upper=True), U)


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
    from ..calls import hide_argument_error
    error = hide_argument_error(0., ramp, 1.)
    if error:
        raise ValueError(error)
    if not np.isfinite(L) or L <= 0:
        raise ValueError("lens loop length must be finite and positive")
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
