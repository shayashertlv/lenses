"""Step 0 registration: the joint photometric fit of WP5_DESIGN §5 (camera per non-anchor view, hinge per temple,
per-region linear parallax; warmth + luminance; silhouette guards; stripes and folds).

Ported from the scratch `refine.py` (view_values, pack/unpack, band schedule), `design_reg/p2_parallax.py` (stripe
fit, pair NCC), `design_reg/p2g_joint.py` (joint camera + parallax fit with guards), `design_reg/p1_guarded.py`
(FrontIoU) and `design_reg/p8_temple_joint.py` (hinge); PORT_REVIEW section 10 lists every change.

**Plan** (``plan_registration``). Per region and view, at the WP3b cameras on S: the region's samples the view sees
(first surface within 0.5 mm, facing it by cos >= 0.5 = the tier-1/2 donor rule of §6.2, on a ``valid`` photo pixel)
and their weight sum cos^2 x px/mm. Regions under 100 mm^2 (end caps) are not registered.
- anchor = the admitted view (edge gate per (view, part), D2) with the largest weight (§5.1), among the views that
  see at least max(200, 5 % of the samples); a region no admitted view sees that well is unseen;
- partners = the other views that share at least max(500, 10 % of the samples) seen samples with the anchor. 10 %,
  not 5 %: on A0's perfect synthetic photos the -x temple top's 04-06 pair (8.6 % shared) still ended 0.62 mm off in
  06, because a thin grazing strip does not constrain a parallax; on FT1119 no pair lies between 5 and 10 %. A partner
  must also give the §5.4 gate at least 400 graded pixels (``gate.MIN_GATE_PX``) in both directions and both folds at
  the start: a pair the gate can never test can never be registered or mixed, and only adds noise to the shared
  cameras (FT1119's first proof: the brow tops' 01/05 pairs had 0-440 gate pixels while the brow-front fold
  residuals came out 1.1-2.6 mm, against the probe's 0.00-0.21 mm with 01 alone). The edge gate does NOT filter
  partners: it decides colour donors (§3) and is re-measured after registration, so a view whose start camera fails
  it (A0's perturbed view 01) must still be registered; membership of the registered set is the gate's photometric
  decision;
- fixed views = every anchor (§5.2 "per non-anchor view": the anchors keep their WP3b cameras and fix the gauge);
  free cameras = the partners that anchor nothing. FT1119: 02, 03, 04, 05, 06 fixed, 01 free (the design's list).
  ``gauge='partnered_anchors'`` (fix only anchors of regions with a partner; FT1119 then frees 04 too) was measured
  worse on FT1119's folds (Brow_Right front 0.70-1.33 mm against 0.14-0.91 mm; first build, LEAKY protocol, not
  re-measured), so it is not the default;
- objective pairs = (anchor, partner) for EVERY partner of every parallax region: the design's "sum over view pairs"
  (§5.3; fix round 2 made it the default). The first build fitted only the free partner 01 when a region had one; on
  A0 that left the brow fronts 0.60 mm off at the gated-only view 04, against <= 0.054 mm at every view with every pair
  (data/wp5/diagnostics/leakfree_variants.json), and on FT1119 both forms gave the same Brow_Right gate pattern.
  ``Plan.fit_fixed_partners = False`` keeps the free-partner form as a diagnostic;
- free hinges = (free-camera view, temple group) when that temple shows in the view (it matters once a camera is
  refitted, [R P5]);
- parallax = every region with a partner, along its anchor's view axis.

**Objective** (§5.3): over the (anchor, partner) pairs of every parallax region, sum w (1 - NCC) / sum w, averaged
over warmth and luminance, on band-passed photo values (``signals.BandImages``) sampled bilinearly at the SAME body
samples projected through each view's camera onto its own S'; w = the pair's co-seen samples at the band's start
(fixed, so a pair cannot leave the loss by losing its overlap). Bands 1.6-3.2, 0.8-1.6, 0.4-0.8 mm; visibility is
recomputed at the start of each band; unbounded Powell with a wall at the parameter box (``Objective.__call__``).
**Convergence** (fix round 2): a Powell run is capped at ``maxfev`` evaluations (§5.3's 2,500); the band then
RESTARTS Powell (fresh directions) from its best exactly evaluated point until a run lowers the band's best loss by
less than ``CONVERGE_TOL``, at most ``MAX_RUNS`` runs. The first build stopped 7 of 12 bands at the cap while the loss
was still falling (fold A's settle round: 0.526 -> 0.490), so its end point was not a property of the data. Each run's
end (loss, parallax) is in ``info['history'][k]['runs']``; ``info['converged']`` is False if any band hit MAX_RUNS.

**Perturbed starts** (``start_seed``): the free parameters start at a seeded offset from WP3b (cameras +/-1 deg and
+/-1 mm, hinges +/-1 deg, parallax a and b +/-2 mm, clipped into the box) while the box and the guards stay referred to
WP3b. step0 runs a few per fold and reports the spread of the converged parallax (stability, never gating).

**Guards** (§5.3): whole-view IoU of every view on its S', front-group IoU of every free-camera view (temple pixels
removed, the probe's FrontIoU) and each free hinge's temple IoU (its own window), at stride 3. Each may fall at most
0.02 below its value at WP3b (``pose0``, also for a perturbed start); beyond that the loss gains 0.5 per 0.01.

**Folds**: ``fold='A'`` fits on the even 4 mm stripes, ``'B'`` on the odd ones, ``'all'`` on every sample (the bake's
refit). The fold a fit did NOT see is where the gate and check_print evaluate it. At the start of each band every
fitted view is rendered at the current pose (its camera, its S') and its band images are built on ``valid`` & the
pixels whose first hit is a region face of the FITTED fold (``gate.fold_label_image``; guard bands, the other fold and
non-region pixels are left out), so the objective of a band is a function of the photo on that support only.
**The fit records every photo pixel it read** (fix round 2): ``RegResult.reads[view]`` = the union of the supports of
all its bands and runs. The labels follow the pose, so a pixel read by band 1 can carry the OTHER fold at the final pose
(the first leak-free proof: 17 / 23 / 11 % of view 01's band supports); the gate and check_print therefore never grade
a pixel the fit read, nor render from one (``gate.gate(reads=...)``, ``check_print.case_images(reads=...)``). By
induction over the bands a fold fit is then a function of the photo on ``reads`` only, so permuting any other valid
pixels leaves its pose bit-identical (tests/photoprint/test_fold_leak.py, end to end). ``info['reads']`` reports per
view the pixels read, the held-out-fold pixels at the final pose and how many of those the fit read (removed from
grading). (Fix round 1's "settle" rounds, which re-ran the last band until its support matched the final labels,
cleaned only the last band and are gone.) The 'all' fit reads ``valid`` (``reads`` = None).

**Parallax limit** (``parallax_limit``, default None = the §5.2 box |a|, |b| <= 4 mm): when given, the wall also holds
max over s of |a + b s| = |a| + |b| <= limit (B1's 4 mm; a diagnostic variant, not the design's box).
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage, optimize

from .. import cameras as C
from .. import raster
from .. import texture_utils as T
from ..camera import project_mm
from . import signals as S
from .inputs import ppm_local
from .pose import (CAM_BOUND, CAM_FREE, HINGE_BOUND_DEG, PARALLAX_BOUND_MM, PIVOT_BOUND_MM, Deformer, Pose, cam_delta,
                   pack, points, unpack)
from .procs import Progress

REG_MIN_COS = 0.5                     # §6.2 donor rule; the probe registered with 0.2
REG_TOL_MM = 0.5
MIN_ANCHOR_SAMPLES = 200
MIN_ANCHOR_SHARE = 0.05
MIN_PAIR_SAMPLES = 500
MIN_PAIR_SHARE = 0.10                 # see the plan docstring (A0: a 8.6 % pair misregistered by 0.62 mm)
MAXFEV = 2500                         # per Powell run (§5.3's per-band cap); a band restarts until converged
MAX_RUNS = 6                          # Powell runs per band at most
CONVERGE_TOL = 1e-4                   # a run that lowers the band's best loss by less than this ends the band
GUARD_DROP = 0.02
GUARD_PER = 0.01
GUARD_WEIGHT = 0.5
GUARD_STRIDE = 3
GUARD_DILATE = 3
MIN_GROUP_PX = 50                     # stride-3 grid px of a temple for a free hinge / temple guard
STEP_CAM = (0.5, 0.5, 0.5, 2.0, 2.0)  # Powell initial direction lengths: deg, deg, deg, px, px
STEP_HINGE = 0.5
STEP_PARALLAX = 0.5
OUTSIDE_LOSS = 1000.0
GAUGE = "all_anchors"
START_CAM_DEG = 1.0                   # perturbed starts (``start_seed``): uniform offsets of this half-width
START_PIVOT_MM = 1.0
START_HINGE_DEG = 1.0
START_PARALLAX_MM = 2.0


# ----------------------------------------------------------------------------- plan
@dataclass
class Plan:
    views: list[str]
    regions: list[int]
    anchors: dict[int, str]
    partners: dict[int, list[str]]
    free_cameras: list[str]
    free_hinges: list[tuple[str, str]]
    parallax: list[int]
    axes: dict[int, np.ndarray]
    stats: dict = field(default_factory=dict)
    excluded: list[str] = field(default_factory=list)
    fixed: list[str] = field(default_factory=list)
    gauge: str = "all_anchors"

    fit_fixed_partners: bool = True

    def fit_partners(self, rid: int) -> list[str]:
        """Partners whose pair enters the objective: every partner (§5.3, the default), or with
        ``fit_fixed_partners = False`` (diagnostic, the first build) only the FREE-camera partners when the region has
        one. Every partner is gated either way."""
        ps = self.partners.get(rid, [])
        if self.fit_fixed_partners:
            return list(ps)
        free = [p for p in ps if p in self.free_cameras]
        return free or list(ps)

    def pairs(self) -> list[tuple[int, str, str]]:
        return [(rid, self.anchors[rid], p) for rid in self.parallax for p in self.fit_partners(rid)]

    def to_json(self, regions) -> dict:
        by = {r.rid: r for r in regions}
        return {"views": self.views, "excluded_views": self.excluded, "gauge": self.gauge, "fixed_views": self.fixed,
                "free_cameras": self.free_cameras,
                "free_hinges": [{"view": v, "group": g} for v, g in self.free_hinges],
                "fit_fixed_partners": self.fit_fixed_partners,
                "regions": [{"rid": rid, "name": by[rid].name, "anchor": self.anchors.get(rid),
                             "partners": self.partners.get(rid, []),
                             "fit_partners": self.fit_partners(rid) if rid in self.parallax else [],
                             "parallax": rid in self.parallax,
                             "parallax_axis": ([round(float(x), 6) for x in self.axes[rid]] if rid in self.axes
                                               else None),
                             "views": self.stats.get(rid, {})} for rid in self.regions]}


def seen_samples(job, deformer: Deformer, pose: Pose, region, view: str, idx: np.ndarray | None = None,
                 min_cos: float = REG_MIN_COS) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(seen, uv, cos) of a region's samples in a view through ``pose`` on the view's S'."""
    smp = region.samples
    idx = np.arange(len(smp)) if idx is None else idx
    v = job.views[view]
    V = deformer.vertices(pose, view)
    P, N = points(V, job.mesh.F, smp.face[idx], smp.bary[idx])
    cam = pose.cameras[view]
    vis, uv = T.visible_points(P, N, cam, job.frame, deformer.scene(pose, view), v.shape, min_cos=min_cos,
                               tol_mm=REG_TOL_MM)
    H, W = v.shape
    inb = (uv[:, 0] > 1) & (uv[:, 0] < W - 2) & (uv[:, 1] > 1) & (uv[:, 1] < H - 2)
    ui = np.clip(np.round(uv[:, 0]).astype(int), 0, W - 1)
    vi = np.clip(np.round(uv[:, 1]).astype(int), 0, H - 1)
    seen = vis & inb & v.valid[vi, ui]
    cos = N @ raster.camera_basis(cam)[2]
    return seen, uv, cos


def gate_pixels(job, deformer: Deformer, pose: Pose, region, a: str, p: str) -> dict:
    """E pixels the §5.4 gate would grade for (a -> p) and (p -> a) on each fold at ``pose``."""
    from scipy import ndimage as nd
    from .gate import single_source_render
    from .regions import FOLD_A, FOLD_B
    out = {}
    for w, v in ((a, p), (p, a)):
        rr = single_source_render(job, deformer, pose, region, v, w)
        E = nd.binary_erosion(rr["ok"], iterations=2) & nd.binary_erosion(job.views[v].acetate, iterations=1)
        for name, f in (("A", FOLD_A), ("B", FOLD_B)):
            out[f"{job.views[w].short}->{job.views[v].short} {name}"] = int((E & (rr["fold"] == f)).sum())
    return out


def plan_registration(job, regions, groups, admitted: dict[tuple[str, str], bool], *, exclude=(),
                      min_pair: int = MIN_PAIR_SAMPLES, min_share: float = MIN_PAIR_SHARE,
                      min_gate_px: int | None = None, gauge: str | None = None) -> Plan:
    gauge = gauge or GAUGE
    if min_gate_px is None:
        from .gate import MIN_GATE_PX
        min_gate_px = MIN_GATE_PX
    views = [n for n in job.views if n not in set(exclude)]
    pose0 = Pose({n: job.views[n].camera for n in job.views})
    deformer = Deformer(job, regions, groups, {})
    anchors, partners, stats, axes = {}, {}, {}, {}
    rids = [r.rid for r in regions if not r.minor]
    by = {r.rid: r for r in regions}
    for rid in rids:
        r = by[rid]
        seen, wsum, st = {}, {}, {}
        P_body, _ = points(job.mesh.V, job.mesh.F, r.samples.face, r.samples.bary)
        for n in views:
            s, _uv, cos = seen_samples(job, deformer, pose0, r, n)
            ppm = ppm_local(job.views[n].camera, job.frame, P_body)
            seen[n] = s
            wsum[n] = float(np.sum((np.clip(cos, 0, 1) ** 2 * ppm)[s]))
            st[n] = {"seen": int(s.sum()), "weight": round(wsum[n], 3),
                     "admitted": bool(admitted.get((n, r.part_name), False))}
        need_a = max(MIN_ANCHOR_SAMPLES, int(MIN_ANCHOR_SHARE * len(r.samples)))
        cands = [n for n in views if st[n]["admitted"] and st[n]["seen"] >= need_a]
        if not cands:
            stats[rid] = st
            continue
        a = max(cands, key=lambda n: (wsum[n], -views.index(n)))
        anchors[rid] = a
        need = max(min_pair, int(min_share * len(r.samples)))
        ps = []
        for n in views:
            if n == a:
                continue
            both = int((seen[n] & seen[a]).sum())
            st[n]["with_anchor"] = both
            if both < need:
                continue
            gpx = gate_pixels(job, deformer, pose0, r, a, n)
            st[n]["gate_px"] = gpx
            if min(gpx.values()) >= min_gate_px:
                ps.append(n)
        partners[rid] = ps
        stats[rid] = st
        if ps:
            axes[rid] = raster.camera_basis(job.views[a].camera)[2]
    if gauge == "all_anchors":
        fixed = set(anchors.values())
    elif gauge == "partnered_anchors":
        fixed = {anchors[rid] for rid, ps in partners.items() if ps}
    else:
        raise ValueError(f"unknown gauge {gauge!r}")
    free = [n for n in views if n not in fixed and any(n in ps for ps in partners.values())]
    free_h = []
    temple_groups = [g for g in groups if g.side != 0]
    for n in free:
        v = job.views[n]
        r = job.scene().render(v.camera, v.shape, GUARD_STRIDE)
        fid = r["face_id"]
        hit = fid >= 0
        for g in temple_groups:
            if int(g.face_mask[fid[hit]].sum()) >= MIN_GROUP_PX:
                free_h.append((n, g.name))
    par = [rid for rid in rids if partners.get(rid)]
    return Plan(views=views, regions=rids, anchors=anchors, partners=partners, free_cameras=free, free_hinges=free_h,
                parallax=par, axes=axes, stats=stats, excluded=sorted(set(exclude)), fixed=sorted(fixed), gauge=gauge)


# ----------------------------------------------------------------------------- guards
class Guards:
    """Silhouette guards (stride 3) relative to their values at ``pose0``."""

    def __init__(self, job, plan: Plan, deformer: Deformer, groups, pose0: Pose, stride: int = GUARD_STRIDE):
        self.job, self.plan, self.deformer, self.stride = job, plan, deformer, stride
        self.label = np.zeros(len(job.mesh.F), np.int16)          # 0 front, k temple group k
        self.gidx = {}
        for k, g in enumerate([g for g in groups if g.side != 0], start=1):
            self.label[g.face_mask] = k
            self.gidx[g.name] = k
        self.view = {}
        for n in plan.views:
            v = job.views[n]
            roi = C.matte_roi(v.matte)
            fg = raster.downsample_mask(v.matte, stride, roi) > 0.5
            self.view[n] = {"roi": roi, "fg": fg}
        self.items: list[tuple[str, str, str]] = [(n, "whole", "") for n in plan.views]
        self.items += [(n, "front", "") for n in plan.free_cameras]
        self.items += [(n, "temple", g) for n, g in plan.free_hinges]
        self._cache: dict = {}
        self._base_lab = {}
        for n in plan.views:
            self._base_lab[n] = self._render(pose0, n)[1]
        self.base = self.measure(pose0)

    def _render(self, pose: Pose, n: str):
        d = self.view[n]
        scene = self.deformer.scene(pose, n)
        r = scene.render(pose.cameras[n], self.job.views[n].shape, self.stride, d["roi"])
        lab = np.full(r["mask"].shape, -1, np.int16)
        m = r["mask"]
        lab[m] = self.label[r["face_id"][m]]
        return m, lab

    def _view_values(self, pose: Pose, n: str) -> dict:
        key = (n, tuple(round(float(x), 10) for x in pack(self.job, pose.cameras[n])), pose.hinge_state(n),
               pose.parallax_state())
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        m, lab = self._render(pose, n)
        fg = self.view[n]["fg"]
        out = {("whole", ""): _iou(m, fg)}
        if n in self.plan.free_cameras:
            b = self._base_lab[n]
            excl = ndimage.binary_dilation((b > 0) | (lab > 0), iterations=GUARD_DILATE)
            out[("front", "")] = _iou(m & ~excl, fg & ~excl)
        for (vn, g) in self.plan.free_hinges:
            if vn != n:
                continue
            k = self.gidx[g]
            b = self._base_lab[n]
            win = ndimage.binary_dilation((b == k) | (lab == k), iterations=GUARD_DILATE)
            win &= ~(((b >= 0) & (b != k)) | ((lab >= 0) & (lab != k)))
            out[("temple", g)] = _iou((lab == k) & win, fg & win)
        if len(self._cache) > 64:
            self._cache.clear()
        self._cache[key] = out
        return out

    def measure(self, pose: Pose) -> dict:
        vals = {}
        for n in self.plan.views:
            for (kind, g), x in self._view_values(pose, n).items():
                vals[(n, kind, g)] = x
        return vals

    def penalty(self, vals: dict) -> float:
        pen = 0.0
        for k in self.items:
            drop = self.base[k] - vals[k] - GUARD_DROP
            if drop > 0:
                pen += GUARD_WEIGHT * drop / GUARD_PER
        return pen

    def to_json(self, vals: dict) -> list:
        return [{"view": n, "guard": kind, "group": g or None, "start": round(self.base[(n, kind, g)], 5),
                 "final": round(vals[(n, kind, g)], 5),
                 "drop": round(self.base[(n, kind, g)] - vals[(n, kind, g)], 5),
                 "within": bool(self.base[(n, kind, g)] - vals[(n, kind, g)] <= GUARD_DROP + 1e-12)}
                for (n, kind, g) in self.items]


def _iou(a: np.ndarray, b: np.ndarray) -> float:
    return float((a & b).sum() / max((a | b).sum(), 1))


# ----------------------------------------------------------------------------- objective
class Objective:
    """Loss of a parameter vector (layout: free cameras x 5, free hinges, parallax regions x 2)."""

    def __init__(self, job, regions, plan: Plan, deformer: Deformer, pose0: Pose, fold: str | None,
                 bands: BandImagesLike = None, guards: Guards | None = None, signals=S.OBJECTIVE_SIGNALS,
                 min_cos: float = REG_MIN_COS, parallax_limit: float | None = None):
        self.job, self.plan, self.deformer, self.pose0, self.guards = job, plan, deformer, pose0, guards
        self.signals, self.min_cos = tuple(signals), float(min_cos)
        self.regions = list(regions)
        self.by = {r.rid: r for r in regions}
        self.fold = fold if fold in ("A", "B") else None
        self.parallax_limit = None if parallax_limit is None else float(parallax_limit)
        self.bands = bands if bands is not None else S.BandImages(job)
        self.supports: dict[str, np.ndarray] = {}
        self.reads: dict[str, np.ndarray] = {}         # fold fits: union of every band's support (pixels read)
        self.band_support_px: list[dict] = []
        self.idx = {rid: self.by[rid].samples.select(fold) for rid in plan.parallax}
        self.pair_views = sorted({n for _rid, a, p in plan.pairs() for n in (a, p)}, key=plan.views.index)
        self.layout = []
        z0, lo, hi, step = [], [], [], []
        for n in plan.free_cameras:
            x = pack(job, pose0.cameras[n])
            ppm = job.ppm(n)
            b = list(CAM_BOUND) + [PIVOT_BOUND_MM * ppm] * 2
            for k, i in enumerate(CAM_FREE):
                self.layout.append(("cam", n, i))
                z0.append(x[i]); lo.append(x[i] - b[k]); hi.append(x[i] + b[k]); step.append(STEP_CAM[k])
        self.par_index = []
        for n, g in plan.free_hinges:
            self.layout.append(("hinge", n, g))
            a0 = pose0.hinges.get((n, g), 0.0)
            z0.append(a0); lo.append(-HINGE_BOUND_DEG); hi.append(HINGE_BOUND_DEG); step.append(STEP_HINGE)
        for rid in plan.parallax:
            a0, b0 = pose0.parallax.get(rid, (0.0, 0.0))
            self.par_index.append(len(z0))
            self.layout.append(("par", rid, 0)); z0.append(a0)
            self.layout.append(("par", rid, 1)); z0.append(b0)
            lo += [-PARALLAX_BOUND_MM] * 2; hi += [PARALLAX_BOUND_MM] * 2; step += [STEP_PARALLAX] * 2
        self.z0 = np.asarray(z0, float)
        self.bounds = list(zip(lo, hi))
        self.step = np.asarray(step, float)
        self.band = None
        self.vis = {}
        self.stack = {}
        self._pairs: dict = {}
        self.nfev = 0
        self.skipped = 0
        self.best = math.inf
        # regions whose parallax moves this region's samples (shared vertices: displacements are averaged)
        self.neigh = {rid: [k for k in plan.parallax if np.intersect1d(self.by[rid].vidx, self.by[k].vidx).size]
                      for rid in plan.parallax}

    def pose(self, z) -> Pose:
        z = np.asarray(z, float)
        cams = dict(self.pose0.cameras)
        hinges = dict(self.pose0.hinges)
        par = dict(self.pose0.parallax)
        packed = {}
        for (kind, a, b), x in zip(self.layout, z):
            if kind == "cam":
                if a not in packed:
                    packed[a] = pack(self.job, self.pose0.cameras[a])
                packed[a][b] = x
            elif kind == "hinge":
                hinges[(a, b)] = float(x)
            else:
                ab = list(par.get(a, (0.0, 0.0)))
                ab[b] = float(x)
                par[a] = (ab[0], ab[1])
        for n, x in packed.items():
            cams[n] = unpack(self.job, x)
        return Pose(cams, hinges, par)

    def fold_supports(self, pose: Pose) -> dict[str, np.ndarray]:
        """Per fitted view: ``valid`` & the pixels whose first hit (at ``pose``) is a region face of the fitted fold."""
        from .gate import FOLD_CODE, fold_label_image
        code = FOLD_CODE[self.fold]
        return {n: self.job.views[n].valid & (fold_label_image(self.job, self.deformer, pose, self.regions, n) == code)
                for n in self.pair_views}

    def start_band(self, band_mm, z, *, record: bool = True) -> None:
        """Fix the band and recompute visibility (per region pair) at ``z``; a fold fit also rebuilds its band images
        on the fitted fold's pixels at ``z`` and (``record``) adds that support to ``reads``."""
        self.band = tuple(band_mm)
        pose = self.pose(z)
        if self.fold is not None:
            self.supports = self.fold_supports(pose)
            self.bands = S.BandImages(self.job, self.supports)
            if record:
                for n, m in self.supports.items():
                    self.reads[n] = m.copy() if n not in self.reads else (self.reads[n] | m)
                self.band_support_px.append({self.job.views[n].short: int(m.sum()) for n, m in self.supports.items()})
        self.vis = {}
        for rid in self.plan.parallax:
            r = self.by[rid]
            for n in [self.plan.anchors[rid]] + self.plan.fit_partners(rid):
                self.vis[(rid, n)] = seen_samples(self.job, self.deformer, pose, r, n, self.idx[rid],
                                                  min_cos=self.min_cos)[0]
        self.stack = {}
        for n in self.pair_views:
            self.stack[n] = np.ascontiguousarray(
                np.stack([self.bands.get(n, s, self.band) for s in self.signals], -1), np.float32)
        self._pairs = {}
        self.best = math.inf
        self.best_z = None
        # pair weights are FIXED for the band (seen by both at the band start): a pair cannot leave the loss by
        # losing its overlap (with weights = the current overlap, the probe's form, a fit could drop a pair to n = 0)
        self.weight = {}
        for rid, a, p in self.plan.pairs():
            self.weight[(rid, p)] = int((self.vis[(rid, a)] & self.vis[(rid, p)]).sum())

    def pair_values(self, pose: Pose, rid: int, n: str, Vcache: dict) -> tuple[np.ndarray, np.ndarray]:
        """(ok, values (K, 2)) of a region's fold samples in view n."""
        r = self.by[rid]
        idx = self.idx[rid]
        key = (rid, pose.hinge_state(n))
        P = Vcache.get(key)
        if P is None:
            V = self.deformer.vertices(pose, n)
            P = np.einsum("kj,kjd->kd", r.samples.bary[idx], V[self.job.mesh.F[r.samples.face[idx]]])
            Vcache[key] = P
        v = self.job.views[n]
        uv = project_mm(P, pose.cameras[n], self.job.frame)
        H, W = v.shape
        inb = (uv[:, 0] > 1) & (uv[:, 0] < W - 2) & (uv[:, 1] > 1) & (uv[:, 1] < H - 2)
        ui = np.clip(np.round(uv[:, 0]).astype(int), 0, W - 1)
        vi = np.clip(np.round(uv[:, 1]).astype(int), 0, H - 1)
        ok = self.vis[(rid, n)] & inb & v.valid[vi, ui]
        vals = S.sample_bilinear(self.stack[n], uv[:, 0], uv[:, 1])
        return ok, vals

    def _pair_key(self, pose: Pose, rid: int, a: str, p: str) -> tuple:
        grp = self.by[rid].group
        cams = tuple(tuple(np.round(pack(self.job, pose.cameras[n]), 12)) if n in self.plan.free_cameras else n
                     for n in (a, p))
        hin = tuple(round(pose.hinges.get((n, grp), 0.0), 12) for n in (a, p))
        par = tuple((k, *np.round(pose.parallax.get(k, (0.0, 0.0)), 12)) for k in self.neigh[rid])
        return (rid, a, p, cams, hin, par)

    def pair_scores(self, pose: Pose) -> list[dict]:
        out = []
        Vcache: dict = {}
        for rid, a, p in self.plan.pairs():
            key = self._pair_key(pose, rid, a, p)
            row = self._pairs.get(key)
            if row is None:
                oka, va = self.pair_values(pose, rid, a, Vcache)
                okp, vp = self.pair_values(pose, rid, p, Vcache)
                ok = oka & okp
                n = int(ok.sum())
                rs = [S.ncc(va[ok, k], vp[ok, k]) for k in range(len(self.signals))]
                row = {"rid": rid, "anchor": a, "partner": p, "n": n,
                       **{f"ncc_{s}": r for s, r in zip(self.signals, rs)}}
                if len(self._pairs) > 4096:
                    self._pairs.clear()
                self._pairs[key] = row
            out.append(row)
        return out

    def photometric(self, pose: Pose) -> float:
        """sum_pairs w (1 - NCC) / sum w, averaged over warmth and luminance; w fixed at the band start."""
        tot, nn = 0.0, 0
        for row in self.pair_scores(pose):
            w = self.weight.get((row["rid"], row["partner"]), row["n"])
            loss = sum(1.0 - row[f"ncc_{s}"] for s in self.signals) / len(self.signals)
            tot += w * loss
            nn += w
        return tot / nn if nn else 2.0

    def start_offset(self, seed: int) -> np.ndarray:
        """A seeded offset of every free parameter (``START_*`` half-widths), for perturbed starts."""
        rng = np.random.default_rng(int(seed))
        u = rng.uniform(-1.0, 1.0, len(self.layout))
        off = np.zeros(len(self.layout))
        for k, (kind, a, i) in enumerate(self.layout):
            if kind == "cam":
                off[k] = u[k] * (START_CAM_DEG if i in CAM_FREE[:3] else START_PIVOT_MM * self.job.ppm(a))
            elif kind == "hinge":
                off[k] = u[k] * START_HINGE_DEG
            else:
                off[k] = u[k] * START_PARALLAX_MM
        return off

    def clip_into_box(self, z) -> np.ndarray:
        lo = np.array([b[0] for b in self.bounds])
        hi = np.array([b[1] for b in self.bounds])
        z = np.clip(np.asarray(z, float), lo, hi)
        if self.parallax_limit is not None:
            for i in self.par_index:
                tot = abs(z[i]) + abs(z[i + 1])
                if tot > self.parallax_limit:
                    z[i:i + 2] *= self.parallax_limit / tot
        return z

    def outside(self, z) -> float:
        """How far z lies outside its box (0 inside), plus the excess of |a| + |b| over ``parallax_limit``."""
        lo = np.array([b[0] for b in self.bounds])
        hi = np.array([b[1] for b in self.bounds])
        out = float(np.sum(np.clip(lo - z, 0, None) + np.clip(z - hi, 0, None)))
        if self.parallax_limit is not None:
            for i in self.par_index:
                out += max(0.0, abs(float(z[i])) + abs(float(z[i + 1])) - self.parallax_limit)
        return out

    def __call__(self, z) -> float:
        """Loss at z (outside the box: a wall of ``OUTSIDE_LOSS`` + distance, so Powell's unbounded line searches turn
        back). The guard penalty is >= 0, so when the photometric part alone is already no lower than the best exact
        loss of this band the guards are skipped and that lower bound is returned. With UNBOUNDED Powell every line
        search starts from the current iterate (scipy's bracket evaluates alpha = 0) and moves only to a strictly
        lower value, so an iterate is always a point with an exact loss; ``best_z`` tracks the best exact point and
        the band returns it. (scipy's BOUNDED Powell moves to the best point inside the line's box even when it is
        worse than the start, which is why bounds are a wall here.)"""
        self.nfev += 1
        z = np.asarray(z, float)
        out = self.outside(z)
        if out > 0:
            return float(OUTSIDE_LOSS + out)
        pose = self.pose(z)
        loss = self.photometric(pose)
        if self.guards is not None:
            if loss >= self.best:
                self.skipped += 1
                return float(loss)
            loss += self.guards.penalty(self.guards.measure(pose))
        if loss < self.best:
            self.best = loss
            self.best_z = z.copy()
        return float(loss)


BandImagesLike = S.BandImages | None


# ----------------------------------------------------------------------------- the fit
@dataclass
class RegResult:
    pose: Pose
    info: dict
    reads: dict | None = None          # fold fits: view -> bool mask of every photo pixel the fit read


def start_pose(job) -> Pose:
    return Pose({n: v.camera for n, v in job.views.items()})


def register(job, regions, groups, plan: Plan, *, fold: str | None = "all", bands=S.REG_BANDS_MM,
             maxfev: int = MAXFEV, max_runs: int = MAX_RUNS, converge_tol: float = CONVERGE_TOL,
             guards: bool = True, pose0: Pose | None = None, start_seed: int | None = None, tag: str = "reg",
             log=print, signals=S.OBJECTIVE_SIGNALS, min_cos: float = REG_MIN_COS,
             parallax_limit: float | None = None) -> RegResult:
    """The joint fit on one fold ('A', 'B' or 'all'). Deterministic for given inputs. ``pose0`` = the reference (box
    centre and guard baseline; default WP3b); ``start_seed`` starts the free parameters at a seeded offset from it."""
    t0 = time.perf_counter()
    pose0 = pose0 or start_pose(job)
    deformer = Deformer(job, regions, groups, plan.axes)
    prog = Progress(tag, log)
    g = Guards(job, plan, deformer, groups, pose0) if guards else None
    obj = Objective(job, regions, plan, deformer, pose0, fold, guards=g, signals=signals, min_cos=min_cos,
                    parallax_limit=parallax_limit)
    z = obj.z0.copy()
    if start_seed is not None and len(z):
        z = obj.clip_into_box(z + obj.start_offset(start_seed))
    z_start = z.copy()
    hist = []

    def par_now(zz) -> dict:
        p = obj.pose(zz).parallax
        return {obj.by[rid].name: [float(p.get(rid, (0.0, 0.0))[0]), float(p.get(rid, (0.0, 0.0))[1])]
                for rid in plan.parallax}

    def run_band(band, z):
        obj.start_band(band, z)
        best = {"f": math.inf}
        nfev0, skipped0 = obj.nfev, obj.skipped

        def f(zz):
            val = obj(zz)
            if val < best["f"]:
                best["f"] = val
            prog(f"fold {fold} {band[0]}-{band[1]} mm eval {obj.nfev - nfev0} best {best['f']:.5f}")
            return val

        f_start = f(z)
        zb, fb = z.copy(), float(f_start)
        runs, converged = [], False
        for k in range(int(max_runs)):
            n0 = obj.nfev
            res = optimize.minimize(f, zb, method="Powell",
                                    options={"maxfev": int(maxfev), "xtol": 1e-3, "ftol": 1e-5,
                                             "direc": np.diag(obj.step)})
            gain = 0.0
            if obj.best_z is not None and obj.best < fb:
                gain = fb - float(obj.best)
                zb, fb = obj.best_z.copy(), float(obj.best)
            runs.append({"run": k + 1, "nfev": int(obj.nfev - n0), "powell_status": int(res.status),
                         "loss": fb, "gain": gain, "parallax_mm": par_now(zb)})
            prog(f"fold {fold} {band[0]}-{band[1]} mm run {k + 1}: loss {fb:.5f} (gain {gain:.2e}, "
                 f"{runs[-1]['nfev']} evals, Powell status {int(res.status)})", force=True)
            if gain < converge_tol:
                converged = True
                break
        hist.append({"band_mm": list(band), "loss_start": float(f_start), "loss_end": fb, "converged": converged,
                     "runs": runs, "nfev": int(obj.nfev - nfev0), "guards_skipped": int(obj.skipped - skipped0)})
        prog(f"fold {fold} {band[0]}-{band[1]} mm done: {f_start:.5f} -> {fb:.5f} ({hist[-1]['nfev']} evals, "
             f"{len(runs)} runs, {'converged' if converged else 'NOT converged'})", force=True)
        return zb

    if len(z) == 0:
        prog("nothing to fit (no region has a partner)", force=True)
    for band in bands if len(z) else ():
        z = run_band(band, z)
    pose = obj.pose(z)
    if obj.band is None:
        obj.start_band(bands[-1], z, record=False)
    pairs = obj.pair_scores(pose)
    gv = g.measure(pose) if g is not None else {}
    reads = None
    reads_info = {"rule": "valid (every photo pixel the matte and acetate admit)"}
    if obj.fold is not None:
        reads = {n: m for n, m in obj.reads.items()}
        reads_info = {"rule": f"union of every band's support (valid & fold-{obj.fold} region pixels at the band's "
                              f"start pose); never graded or rendered from by the held-out evaluations",
                      "band_support_px": obj.band_support_px,
                      "views": reads_report(job, deformer, pose, regions, reads, obj.fold)}
    info = {"fold": fold or "all", "bands_mm": [list(b) for b in bands], "maxfev": int(maxfev),
            "max_runs": int(max_runs), "converge_tol": float(converge_tol),
            "converged": bool(all(h["converged"] for h in hist)),
            "signals": list(signals), "min_cos": float(min_cos), "parallax_limit_mm": parallax_limit,
            "fit_partners": "every partner" if plan.fit_fixed_partners else "free partner when present",
            "start_seed": start_seed,
            "start_offset": ([float(x) for x in (z_start - obj.z0)] if start_seed is not None else None),
            "layout": [[k, str(a), (int(i) if not isinstance(i, str) else i)] for k, a, i in obj.layout],
            "reads": reads_info,
            "parameters": len(z), "history": hist, "pairs_final": pairs,
            "photometric_final": float(obj.photometric(pose)) if obj.plan.parallax else None,
            "loss_final": float(hist[-1]["loss_end"]) if hist else None,
            "guards": g.to_json(gv) if g is not None else [],
            "guard_penalty": float(g.penalty(gv)) if g is not None else 0.0,
            "pose": pose_json(job, pose, pose0, regions, plan),
            "seconds": round(time.perf_counter() - t0, 1)}
    return RegResult(pose=pose, info=info, reads=reads)


def reads_report(job, deformer: Deformer, pose: Pose, regions, reads: dict, fold: str) -> dict:
    """Per view (short name): the pixels the fit read, the held-out fold's valid pixels at ``pose`` and how many of
    those the fit read (they are removed from every held-out grading; per region name), and a sha256 of the mask."""
    import hashlib
    from .gate import FOLD_CODE, fold_label_image
    other = FOLD_CODE["B" if fold == "A" else "A"]
    names = {r.rid: r.name for r in regions}
    out = {}
    for n, m in reads.items():
        lab, rid = fold_label_image(job, deformer, pose, regions, n, with_region=True)
        held = job.views[n].valid & (lab == other)
        bad = held & m
        ids, cnt = np.unique(rid[bad], return_counts=True)
        out[job.views[n].short] = {
            "read_px": int(m.sum()), "held_out_px_at_final_pose": int(held.sum()),
            "held_out_px_read_by_fit": int(bad.sum()), "held_out_px_clean": int((held & ~m).sum()),
            "read_by_region": {names[int(i)]: int(c) for i, c in zip(ids, cnt) if i >= 0},
            "sha256": hashlib.sha256(np.packbits(m).tobytes()).hexdigest()}
    return out


def pose_json(job, pose: Pose, pose0: Pose, regions, plan: Plan) -> dict:
    by = {r.rid: r for r in regions}
    cams = {}
    for n in plan.views:
        cams[n] = {"camera": {k: float(v) for k, v in pose.cameras[n].to_dict().items()},
                   "free": n in plan.free_cameras,
                   "delta_from_start": cam_delta(job, pose0.cameras[n], pose.cameras[n], n)}
    par = {}
    for rid in plan.parallax:
        a, b = pose.parallax.get(rid, (0.0, 0.0))
        par[by[rid].name] = {"rid": rid, "a_mm": float(a), "b_mm": float(b), "d_at_s-1_mm": float(a - b),
                             "d_at_s+1_mm": float(a + b), "max_abs_mm": float(abs(a) + abs(b)),
                             "anchor": plan.anchors[rid]}
    return {"cameras": cams, "hinges": [{"view": v, "group": gname, "alpha_deg": float(pose.hinges.get((v, gname), 0))}
                                        for v, gname in plan.free_hinges],
            "parallax": par}


def pose_from_json(job, d: dict) -> Pose:
    from ..camera import Camera
    cams = {n: Camera.from_dict(c["camera"]) for n, c in d["cameras"].items()}
    for n, v in job.views.items():
        cams.setdefault(n, v.camera)
    hinges = {(h["view"], h["group"]): float(h["alpha_deg"]) for h in d.get("hinges", [])}
    par = {int(p["rid"]): (float(p["a_mm"]), float(p["b_mm"])) for p in d.get("parallax", {}).values()}
    return Pose(cams, hinges, par)
