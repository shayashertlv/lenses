"""WP6 joint fit (plan/WP6_DESIGN.md §7 "fit_temple"): SUGGESTED temple parameters from all six photos. It never writes
a scene.

    Family            a temple parameterisation: a spec builder, base keyword arguments, the free parameters with
                      their bands (Prada: x_in at 0 and at the zone end, zone thickness, r_A, r_B, r_C, F1 rise, arm
                      offset, tip offset, two plan knots); `slab()` = the same family with every relief 0, no facets
    objective         sum over views of (1 - ROI IoU + 4 x contour / ROI width) at fixed cameras and theta, + crease-line
                      reprojection on 05 / 01 and 05 -> 02 crease transfer (1 mm = 0.02), + the MEASURED RELIEF CUE
                      (fit-4, relief_cue: the photo crease steps against the model's under the matched light, the 05 x
                      02 triangulated X_C - X_B against the model's, each in the noise unit NOISE_LOSS), + the hard
                      constraints as steep penalties (front bound, dn >= 3 deg at every confirmed crease, the hinge:
                      relief_hinge, the tool's own W_HINGE_LEAF / W_KNUCKLE_PENETRATION limits); a side view fits no
                      hinge angle (relief_views.SIDE_THETA_WHY), so the side-view crease terms read none
    fit_temple        block coordinates: cameras + theta (camera_block: every view's §6 per-model refit of the current
                      shape from its BASE camera, and a fresh rear family, exactly what the delivered model gets) | shape
                      (Nelder-Mead then a bounded Powell polish, maxfev 300: shape_step); <= 4 rounds; stops when every
                      parameter moves < 0.1, the loss < 1 % and the round's camera block left the front bound clear;
                      then a FINAL camera block on the final shape: the returned cameras, details, front bound and
                      sigma are the final shape's own refit cameras (FIT_VERSION); the time budget is reported, never
                      a stop; the outline is re-traced (relief_trace.trace_outline), never optimised, BEFORE each
                      camera block (at the previous block's cameras), so every block is fitted to the exact model the
                      shape step and the gates read; heartbeat >= 60 s
    FitPool           persistent worker processes (os.cpu_count() - 2, each opted out of EcoQoS) that run the camera
                      block per view (and the rear family per alpha x seed) and each objective evaluation per view;
                      results return in submission order and every sum runs in the serial order, so a pooled fit is
                      the serial fit bit for bit (tests/test_temple_fit_parallel.py)
    sigma / profiles  sigma per parameter from the curvature of the objective's PHOTO terms (penalties excluded: the
                      change one noise unit buys; a status says why it is undefined or not photo noise: flat, at a band
                      bound, not at a minimum, a hard-constraint penalty acting along it, an infeasible stencil), per-view
                      profiles, the conflict table (views pulling a parameter apart beyond their noise), the S3 table
                      (each observable's own optimum for the zone level at B and the relief at C vs the joint one)
    synthetic_job     S1: the tool's own temple rendered through the fitted cameras (each perturbed by its view's
                      measured contour error) as Lambert-shaded photos and exact mattes, to trace and fit back
"""
from __future__ import annotations

import copy
import hashlib
import math
import os
import pickle
import shutil
import sys
import tempfile
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from scipy import optimize

from ..g3d.temple import fixtures as FX
from ..g3d.temple.spec import TempleError
from . import cameras as C
from . import raster
from . import relief_cue as CU
from . import relief_hinge as RH
from . import relief_rules as RR
from .camera import Camera
from .relief_views import (CREASE_VIEWS, Job, Model, crease_fit, fit_model_view, fit_theta, front_bound, refit_camera,
                           render_mask, render_model, render_tags, roi_box, roi_metrics, rot_about_hinge, temple_roi,
                           envelopes)

# r_B is FIXED at 0 by default (B on the base surface): a facet meets the base only along its zero line, and with
# r_B != 0 F1's and F4's zero lines must cross behind B, which leaves the family a non-convex feasible set (the
# optimiser hits refusal cliffs). The zone level at B stays free through x_in and the zone thickness, and C - B = r_C.
# Pass free=PRADA_FREE_RB to fit r_B as well.
PRADA_FREE = (("x_in0", 3.0), ("x_in_ze", 3.0), ("t_zone", (3.5, 7.5)), ("r_A", (0.02, 3.0)),
              ("r_C", (0.0, 7.0)), ("rise", (-0.6, -0.02)), ("arm_dx", 4.0), ("tip_dx", 6.0), ("k1", 3.0),
              ("k2", 3.0))
PRADA_FREE_RB = PRADA_FREE[:4] + (("r_B", (-1.0, 3.0)),) + PRADA_FREE[4:]
# The pyramid family (F1 = the base: FX.prada_pyramid_spec, the hand-traced tool test's topology): one relief (r_C), the
# zone's thickness linear from t_zone at the hinge end to t_ze at the zone end (the base outer surface stays a plane);
# x_in at the hinge is held by the hinge parts (relief_hinge), not by its band.
PYRAMID_FREE = (("x_in0", 3.0), ("x_in_ze", 3.0), ("t_zone", (1.5, 7.5)), ("t_ze", (3.0, 8.5)), ("r_C", (0.0, 7.0)),
                ("arm_dx", 4.0), ("tip_dx", 6.0), ("k1", 3.0), ("k2", 3.0))
SLAB_FREE = ("x_in0", "x_in_ze", "t_zone", "t_ze", "arm_dx", "tip_dx", "k1", "k2")
NOISE_LOSS = 0.005            # sigma: the parameter change that raises the objective by this much (0.5 % loss)
CURV_FLAT = 1e-12             # sigma: |curvature| at or below this = a flat objective
PENALTY = 10.0
# The fit procedure's version: the proof records it with S1 (which runs fit_temple), so a resumed run never keeps an S1
# graded by a different procedure.
#   fit-1: camera blocks chained from the previous round's cameras (each box centred on them), rear family in round 0
#          only, the returned cameras = the last round's (fitted BEFORE the last shape step)
#   fit-2: every camera block = the §6 per-model refit of the current shape from the base cameras (box around them) and
#          a fresh rear family; a round converges only when its camera block left the front bound clear; a final
#          camera block on the final shape gives the returned cameras, rear family, details, front bound and sigma
#   fit-3: the outline is re-traced BEFORE each camera block (at the previous block's cameras; the final block too), not
#          after it, so a block's cameras are the refit of the exact model the round's front bound and shape step read;
#          sigma and its flat / minimum tests come from the photo terms only (the penalties are excluded), and a
#          penalty acting along a parameter is its own status ("penalty_active"), never "not converged"
#   fit-4: the measured relief cue (relief_cue: photo crease steps under the matched light, 05 x 02 triangulated
#          X_C - X_B) and the hinge constraint (relief_hinge) in the objective; no hinge angle in a side view (theta 0,
#          the camera alone: relief_views.SIDE_THETA_WHY); dn >= 3 deg also at facet / base creases (the pyramid's A-B)
#   fit-5: retain every paired photo crease in R3; a failed transfer makes the measurement incomplete and the
#          shape infeasible instead of averaging the remaining creases or charging zero transfer loss.
#   fit-6: coupled camera / observable hinge alignment selected only by silhouette loss, within the original
#          camera box and front guard. Side hinges and rear-family fixed-alpha refits remain unchanged.
FIT_VERSION = ("fit-11 (2026-10-09): fit-10 + whole-scene silhouette evidence for hinge-angle observability")


def _hat(d, a, b, c):
    return np.interp(d, [a, b, c], [0.0, 1.0, 0.0])


@dataclass
class Family:
    builder: object                       # (**kwargs) -> spec
    base: dict                            # keyword arguments (outline, arm knots, node positions, defaults)
    free: list                            # [(name, lo, hi)]
    slab_mode: bool = False
    arm_ramp: tuple = (10.0, 100.0, 125.0)  # arm_dx ramps in over zone_end .. zone_end + 10; tip over 100 .. 125
    knots: tuple = ((65.0, 80.0, 100.0), (100.0, 110.0, 125.0))
    info: dict = field(default_factory=dict)

    @property
    def names(self):
        return [n for n, _, _ in self.free]

    def x0(self) -> np.ndarray:
        return np.array([float(self.base.get(n, 0.0)) for n in self.names])

    def bounds(self):
        return [(lo, hi) for _, lo, hi in self.free]

    def kwargs(self, x) -> dict:
        kw = copy.deepcopy(self.base)
        vals = dict(zip(self.names, [float(v) for v in x]))
        for k in ("x_in0", "x_in_ze", "t_zone", "t_ze", "r_A", "r_B", "r_C", "rise", "r_H"):
            if k in vals:
                kw[k] = vals[k]
        ze = kw["zone_end"]
        arm = [list(a) for a in kw["arm"]]
        for a in arm:
            d = a[0]
            dx = vals.get("arm_dx", 0.0) * float(np.clip((d - ze) / self.arm_ramp[0], 0.0, 1.0))
            dx += vals.get("tip_dx", 0.0) * float(np.clip((d - self.arm_ramp[1]) / (self.arm_ramp[2] - self.arm_ramp[1]),
                                                          0.0, 1.0))
            dx += vals.get("k1", 0.0) * float(_hat(d, *self.knots[0])) + vals.get("k2", 0.0) * float(_hat(d, *self.knots[1]))
            a[1] += dx
        kw["arm"] = arm
        return kw

    def spec(self, x) -> dict:
        kw = self.kwargs(x)
        if self.slab_mode:
            return slab_spec(**kw)
        return self.builder(**kw)

    def slab(self) -> "Family":
        base = copy.deepcopy(self.base)
        for k in ("r_A", "r_B", "r_C", "rise", "r_H"):
            base[k] = 0.0
        free = [f for f in self.free if f[0] in SLAB_FREE]
        return Family(self.builder, base, free, slab_mode=True, arm_ramp=self.arm_ramp, knots=self.knots)


def slab_spec(*, top, bottom, zone_end, x_in0, x_in_ze, t_zone, arm, hinge=None, tip_round_mm=0.0, roll_mm=0.0,
              t_ze=None, **_ignored) -> dict:
    """The fitted slab: the family's outline, inner line and thickness, no facets (every r = 0)."""
    inner, thick = FX.zone_lines(top, zone_end, x_in0, x_in_ze, t_zone, arm, t_ze)
    return {"schema": "g3d.temple/1", "hinge": copy.deepcopy(hinge or FX.HINGE),
            "outline": {"top": [list(p) for p in top], "bottom": [list(p) for p in bottom],
                        "tip_round_mm": tip_round_mm},
            "inner": inner, "thickness": thick, "zone_end_d": float(zone_end), "nodes": {}, "facets": {},
            "chamfers": [], "roll_mm": roll_mm, "options": {}}


TOPOLOGIES = {"f1": "F1 crease + rise (FX.prada_spec)", "pyramid": "F1 = the base (FX.prada_pyramid_spec)"}


def prada_family(base: dict, free=None, topology: str = "f1") -> Family:
    """Bands: a number = +- that around the start; a pair = absolute bounds. `topology` = Astra's reading of the
    facets (TOPOLOGIES): "f1" (default free PRADA_FREE) or "pyramid" (default free PYRAMID_FREE). A t_ze the base does
    not give starts at t_zone (a constant zone thickness)."""
    if topology not in TOPOLOGIES:
        raise ValueError(f"topology {topology!r}: expected one of {sorted(TOPOLOGIES)}")
    free = (PYRAMID_FREE if topology == "pyramid" else PRADA_FREE) if free is None else free
    if any(n == "t_ze" for n, _ in free) and base.get("t_ze") is None:
        base["t_ze"] = float(base["t_zone"])
    fr = []
    for name, band in free:
        v = float(base.get(name, 0.0))
        lo, hi = (v - band, v + band) if not isinstance(band, tuple) else band
        fr.append((name, lo, hi))
    fam = Family(FX.prada_pyramid_spec if topology == "pyramid" else FX.prada_spec, base, fr)
    fam.info["topology"] = topology
    return fam


# --------------------------------------------------------------------------------------------- objective
@dataclass
class Context:
    job: Job
    fits: dict                     # view -> {"camera", "theta"}
    rois: dict                     # view -> ROI mask
    views: tuple
    guides_lines: dict = field(default_factory=dict)   # view -> {crease: (p0, p1)} snapped photo lines
    crease_nodes: dict = field(default_factory=lambda: {"AB": ("A", "B"), "AC": ("A", "C"), "BC": ("B", "C"),
                                                        "BK": ("B", "K")})
    confirmed: tuple = ("AB", "AC", "BC")
    zone_end: float = 50.0
    photo_steps: dict = field(default_factory=dict)    # view -> relief_cue.photo_steps (the measured cue, once per job)
    tri: dict | None = None                            # relief_cue.triangulate at this context's cameras
    hinge: bool = True                                 # the hinge constraint (relief_hinge) when the job has parts


def _view_loss(job, model, k, fit, roi):
    # render_model's mask (and for the front view its tags and stations, which the front bound reads), bit for bit
    if job.kind(k) == "front":
        r = render_tags(job, model, k, fit["camera"], fit["theta"])
    else:
        r = {"mask": render_mask(job, model, k, fit["camera"], fit["theta"])}
    m = roi_metrics(job.matte(k), r["mask"], roi, job.hinge_ppm(fit["camera"]))
    box = roi_box(roi)
    width = float(box[3] - box[2]) if box is not None else 1.0       # = xs.max() - xs.min() + 1 of the ROI pixels
    px = m["contour_mm"] * job.hinge_ppm(fit["camera"])
    return (1.0 - m["iou"]) + 4.0 * min(px, RR.CONTOUR_TRUNC_PX) / width, m, r


def _view_term(job, model, k, fit, roi, zone_end) -> dict:
    """One view's share of the objective (the unit a pool worker computes): its loss, IoU and contour, and for the
    front view 04 the front bound (it needs that view's render)."""
    lk, m, r = _view_loss(job, model, k, fit, roi)
    fb = front_bound(job, r, fit["camera"], zone_end=zone_end) if k == "04" else None
    return {"loss": lk, "iou": m["iou"], "contour_mm": m["contour_mm"], "front_bound": fb}


def model_crease_px(job, tb, fit, side, n1, n2, n=11) -> np.ndarray | None:
    """Image points of the model crease n1 -> n2 (its part inside the outline) in a view."""
    ps = tb.spec
    a, b = np.array(ps.nodes[n1][:2]), np.array(ps.nodes[n2][:2])
    t = np.linspace(0, 1, 101)[:, None]
    P = a[None] * (1 - t) + b[None] * t
    inside = (P[:, 1] <= ps.y_top(P[:, 0])) & (P[:, 1] >= ps.y_bot(P[:, 0])) & (P[:, 0] >= ps.d0)
    if inside.sum() < 3:
        return None
    P = P[inside][np.linspace(0, inside.sum() - 1, n).astype(int)]
    lab = None
    for l_, f in ps.facets.items():
        if n1 in f["poly"] and n2 in f["poly"]:
            lab = l_
            break
    if lab is None:
        return None
    r = tb.sol.planes[lab].r(P[:, 0], P[:, 1])
    X = ps.x_base(P[:, 0]) + r
    L = np.stack([P[:, 0], P[:, 1], X], axis=1)
    W = TB_to_world(L, ps.hinge["z_mm"])
    W = rot_about_hinge(W * np.array([side, 1, 1]), side, fit["theta"].get(side, 0.0), job.hinge["axis_xz_mm"])
    return C.project(job.frame.to_norm(W), fit["camera"])


NEAR = dict(CREASE_VIEWS)                 # crease view -> its near temple side


def TB_to_world(L, hz):
    return np.stack([L[:, 2], L[:, 1], hz - L[:, 0]], axis=1)


def _line_dist_mm(job, cam, q, line, P3=None):
    a, b = np.asarray(line[0], float), np.asarray(line[1], float)
    dv = (b - a) / np.linalg.norm(b - a)
    nv = np.array([-dv[1], dv[0]])
    d = (q - a) @ nv
    ppm = job.hinge_ppm(cam)
    return float(np.mean(np.abs(d))) / ppm


INFEASIBLE = 1e3


def safe_spec(family: "Family", x):
    """family.spec(x), or None when the parameters admit no valid spec (the builder or the core refuses)."""
    try:
        return family.spec(x)
    except (ValueError, TempleError):
        return None


def objective(ctx: Context, spec: dict | None, *, details: bool = False, pool: "FitPool | None" = None):
    """The joint-fit objective of one spec (module docstring). With a `pool` the six view terms run in its workers
    and the result is bit-identical to the serial one (objective_many)."""
    return objective_many(ctx, [spec], details=details, pool=pool)[0]


def objective_many(ctx: Context, specs: list, *, details: bool = False, pool: "FitPool | None" = None) -> list:
    """`objective` for several specs. Serial (pool None): view by view in this process. Pooled: every (spec, view)
    term is one task, the crease terms run here meanwhile, and each total is then summed in exactly the serial
    order (views in ctx.views order, reprojection, transfer, front-bound penalty, dn penalties), so the values are
    bit-identical to the serial path's."""
    out = [None] * len(specs)
    models = {}
    for i, spec in enumerate(specs):
        if spec is None:
            out[i] = (INFEASIBLE, {"refused": "builder", "message": "no valid spec for these parameters"}) \
                if details else INFEASIBLE
            continue
        try:
            models[i] = Model.from_spec(ctx.job, spec, "fit")
        except TempleError as e:
            out[i] = (INFEASIBLE, {"refused": e.code, "message": e.message}) if details else INFEASIBLE
    if pool is None:
        for i, model in models.items():
            vt = {k: _view_term(ctx.job, model, k, ctx.fits[k], ctx.rois[k], ctx.zone_end) for k in ctx.views}
            out[i] = _reduce(ctx, vt, _extra_terms(ctx, model), details)
        return out
    jr, cr = pool.job_ref(ctx.job), pool.ctx_ref(ctx)
    futs = {(i, k): pool.submit(_task_view, *jr, *cr, model.temples, k) for i, model in models.items()
            for k in ctx.views}
    extras = {i: _extra_terms(ctx, model) for i, model in models.items()}     # here, while the workers render
    res = dict(zip(futs, pool.gather(list(futs.values()), "objective")))
    for i in models:
        out[i] = _reduce(ctx, {k: res[(i, k)] for k in ctx.views}, extras[i], details)
    return out


def _reduce(ctx: Context, vt: dict, ex: dict, details: bool):
    """The objective's sum, in the one fixed order both paths use."""
    if ex["tr"] is not None and ex["tr"]["missing_creases"]:
        return (INFEASIBLE, {"refused": "incomplete_transfer", "transfer": ex["tr"]}) if details else INFEASIBLE
    total = 0.0
    parts = {}
    for k in ctx.views:
        total += vt[k]["loss"]
        parts[k] = {"loss": vt[k]["loss"], "iou": vt[k]["iou"], "contour_mm": vt[k]["contour_mm"]}
    total += RR.CREASE_REPROJ_MM_WEIGHT * ex["rp"]
    total += RR.TRANSFER_MM_WEIGHT * ex["trm"]
    total += ex.get("step_term", 0.0)
    total += ex.get("tri_term", 0.0)
    pen = 0.0
    fb = None
    pen_fb = 0.0
    if "04" in vt:
        fb = vt["04"]["front_bound"]
        pen_fb = PENALTY * fb["hinge_zone_max_mm"]
        pen += pen_fb
    for t in ex["dn_terms"]:
        pen += t
    pen += ex.get("hinge_pen", 0.0)
    total += pen
    if not details:
        return total
    return total, {"views": parts, "crease_reproj_mm": ex["rp"], "transfer": ex["tr"], "penalty": pen,
                   "penalty_front_bound": pen_fb, "penalty_dn": float(sum(ex["dn_terms"])),
                   "penalty_hinge": ex.get("hinge_pen", 0.0), "hinge_violation_mm": ex.get("hinge_mm"),
                   "hinge": ex.get("hinge"),
                   "cue": {"step_term": ex.get("step_term", 0.0), "step_chi2": ex.get("step_chi2"),
                           "steps": ex.get("steps"), "tri_term": ex.get("tri_term", 0.0),
                           "tri_model_offset_CB_mm": ex.get("tri_model"),
                           "tri_photo_offset_CB_mm": None if ctx.tri is None else ctx.tri["offset_CB_mm"],
                           "tri_sigma_mm": None if ctx.tri is None else ctx.tri["sigma_mm"]},
                   "front_bound": fb, "min_confirmed_dn_deg": ex["dmin"]}


def crease_regions(tb, n1: str, n2: str) -> tuple | None:
    """The crease-table key of the crease n1 - n2: the two facets that both contain the nodes, or (facet, "base") when
    only one does (a facet against the base: the pyramid family's A-B, F2 / base)."""
    labs = sorted(l_ for l_, f in tb.spec.facets.items() if n1 in f["poly"] and n2 in f["poly"])
    if len(labs) == 2:
        return tuple(labs)
    if len(labs) == 1:
        return tuple(sorted((labs[0], "base")))
    return None


def _extra_terms(ctx: Context, model) -> dict:
    """The objective's non-silhouette terms: crease-line reprojection (05 / 01), 05 -> 02 transfer, the measured relief
    cue (crease steps, triangulated C - B), the dn >= 3 deg penalty terms at the confirmed creases and the hinge
    penalty (each as the serial loop added it)."""
    tb = model.build
    reproj = []
    if tb is not None and tb.spec.facets:
        for k in ("05", "01"):
            if k not in ctx.guides_lines or k not in ctx.fits:
                continue
            cf = crease_fit(ctx.fits, k)
            side = NEAR[k]
            for cname, line in ctx.guides_lines[k].items():
                nodes = ctx.crease_nodes.get(cname)
                if nodes is None:
                    continue
                q = model_crease_px(ctx.job, tb, cf, side, *nodes)
                if q is not None:
                    reproj.append(_line_dist_mm(ctx.job, cf["camera"], q, line))
    rp = float(np.mean(reproj)) if reproj else 0.0
    tr = transfer_all(ctx, model) if ("05" in ctx.guides_lines and "02" in ctx.guides_lines) else None
    trm = tr["mean_mm"] if tr and tr["mean_mm"] is not None else 0.0
    dmin = None
    dn_terms = []
    if tb is not None and tb.spec.facets:
        crs = {tuple(c["regions"]): c for c in tb.tables["creases"]}
        for cname in ctx.confirmed:
            n1, n2 = ctx.crease_nodes[cname]
            key = crease_regions(tb, n1, n2)
            if key is not None and key in crs:
                dn = crs[key]["dihedral_deg"][0]
                dmin = dn if dmin is None else min(dmin, dn)
                dn_terms.append(PENALTY * max(0.0, RR.R2_CONFIRMED_DN_DEG - dn) / RR.R2_CONFIRMED_DN_DEG)
    out = {"rp": rp, "tr": tr, "trm": trm, "dn_terms": dn_terms, "dmin": dmin}
    out.update(cue_terms(ctx, model))
    if ctx.hinge and model.spec is not None:
        rep_ = RH.hinge_report(ctx.job, model.spec, tb)
        if rep_ is not None:
            v = RH.hinge_violation_mm(rep_)
            out.update({"hinge_mm": v, "hinge_pen": PENALTY * v, "hinge": rep_})
    return out


def cue_terms(ctx: Context, model) -> dict:
    """The measured relief cue (relief_cue) of one model: NOISE_LOSS x the crease-step misfit (in STEP_MIN_L units)
    over the crease views with photo steps, and NOISE_LOSS x ((X_C - X_B)_model - triangulated)^2 / sigma^2."""
    out = {}
    tb = model.build
    if ctx.photo_steps:
        cfs = {k: crease_fit(ctx.fits, k) for k in ctx.photo_steps if k in ctx.fits}
        if cfs:
            ms = {k: CU.model_steps(ctx.job, model, k, cf["camera"], NEAR[k],
                                    {c: (p["p0"], p["p1"]) for c, p in ctx.photo_steps[k].items() if p is not None},
                                    theta=cf["theta"].get(NEAR[k], 0.0))
                  for k, cf in cfs.items()}
            mis = CU.step_misfit(ctx.photo_steps, ms)
            out.update({"step_chi2": mis["chi2"], "step_term": NOISE_LOSS * mis["chi2"], "steps": mis})
    if ctx.tri is not None and tb is not None and tb.spec.facets and "C" in tb.spec.nodes and "B" in tb.spec.nodes:
        om = CU.model_offset_cb(tb)
        sg = ctx.tri.get("sigma_mm") or CU.TRI_ENDPOINT_PX
        out.update({"tri_model": om, "tri_term": NOISE_LOSS * ((om - ctx.tri["offset_CB_mm"]) / sg) ** 2})
    return out


def transfer_all(ctx: Context, model) -> dict:
    """Mean over ALL photo creases observed in both views, in source order.

    Eligibility comes from the photos, never from which rays hit a candidate.
    A missing/invalid transfer invalidates the aggregate, while retaining each
    result for diagnosis. Unpaired lines have no cross-view observation and are
    reported separately; they do not enter R3 for any compared model.
    """
    from .relief_check import crease_transfer
    out = {}
    vals, missing = [], []
    lines05, lines02 = ctx.guides_lines.get("05", {}), ctx.guides_lines.get("02", {})
    f05, f02 = crease_fit(ctx.fits, "05"), crease_fit(ctx.fits, "02")
    for cname, l05 in lines05.items():
        l02 = lines02.get(cname)
        if l02 is None:
            continue
        t = crease_transfer(ctx.job, model, f05["theta"], f05["camera"], f02["camera"], f02["theta"], l05, l02, near=1)
        out[cname] = t
        if t is not None and np.isfinite(t["mean_abs_mm"]) and t["mean_abs_mm"] >= 0:
            vals.append(t["mean_abs_mm"])
        else:
            missing.append(cname)
    complete = bool(out) and not missing
    return {"per_crease": out, "mean_mm": float(np.mean(vals)) if complete else None,
            "complete": complete, "required_creases": list(out), "missing_creases": missing,
            "unpaired_creases": {"05": [c for c in lines05 if lines02.get(c) is None],
                                 "02": [c for c in lines02 if lines05.get(c) is None]}}


# --------------------------------------------------------------------------------------------- the fit
def refit_views(job, model, fits_prev, views, rear: bool, log=print, pool: "FitPool | None" = None) -> dict:
    """The camera + theta block: fit_model_view per view, and with `rear` the rear family for 06. With a `pool` every
    view's refit is one task and the rear family's alpha x seed refits are tasks too (all independent: each starts
    from its own fixed camera), so the cameras are the serial ones."""
    from .relief_views import rear_family, turntable_seed
    plan = [(k, fits_prev.get(k, {}).get("camera") or job.base[k]) for k in views if not (k == "06" and rear)]
    futs = None
    if pool is not None:
        jr = pool.job_ref(job)
        futs = [pool.submit(_task_fit_view, *jr, model.temples, k, start) for k, start in plan]
    rear_fit = None
    if rear and "06" in views:
        seeds = [job.base["06"], turntable_seed(job, job.base["04"])]
        if "06" in fits_prev:
            seeds.insert(0, fits_prev["06"]["camera"])
        fam = rear_family(job, model, seeds, refit_many=None if pool is None else pool.refit_many(job, model))
        best = min(fam["rows"], key=lambda r_: r_["loss"])
        cam = Camera.from_dict(best["camera"])
        th = fit_theta(job, model, "06", cam, {1: best["alpha"], -1: best["alpha"]})
        rear_fit = {"camera": cam, "theta": {s: th[s]["theta"] for s in (1, -1)}, "family": fam,
                    "alpha": best["alpha"], "theta_fit": {str(s): th[s] for s in (1, -1)}, "refit": best["refit"]}
    if pool is None:
        done = [fit_model_view(job, model, k, start) for k, start in plan]
    else:
        done = pool.gather(futs, "cameras")
    done = dict(zip([k for k, _ in plan], done))
    fits = {}
    for k in views:
        if k == "06" and rear:
            fits["06"] = rear_fit
            continue
        fits[k] = done[k]
        for keep in ("family", "alpha"):                    # the rear family found in round 0 stays on record
            if keep in fits_prev.get(k, {}):
                fits[k][keep] = fits_prev[k][keep]
    return fits


def camera_block(job, model, views, rear: bool, log=print, pool: "FitPool | None" = None) -> dict:
    """The §6 per-model refit of `model` (WP6_DESIGN §6, §8 "each at its own refit cameras"): every view from its BASE
    camera (job.base, the box centred on it) and, with `rear`, a fresh rear family from the base and turntable seeds.
    The same computation the delivered model's cameras come from, and a function of the shape alone (no chaining from
    an earlier round's cameras, whose boxes drifted away from the base camera)."""
    return refit_views(job, model, {}, views, rear, log=log, pool=pool)


def _family_summary(f06: dict | None) -> dict | None:
    if not f06 or "family" not in f06:
        return None
    fam = f06["family"]
    return {"family": fam["family"], "alpha": f06.get("alpha"), "loss_min": fam["loss_min"], "stop_r": fam["stop_r"],
            "camera": f06["camera"].to_dict() if isinstance(f06.get("camera"), Camera) else f06.get("camera")}


def build_rois(job, models_fits: list, views) -> dict:
    rois = {}
    for k in views:
        renders = [render_model(job, m, k, f[k]["camera"], f[k]["theta"], points=False) for m, f in models_fits]
        rois[k] = temple_roi(job, k, renders)
    return rois


def fit_temple(job: Job, family: Family, *, views=("01", "02", "03", "04", "05", "06"), guides_lines=None,
               rounds: int = RR.FIT_MAX_ROUNDS, maxfev: int = RR.FIT_MAXFEV, rear: bool = True, retrace=None,
               fits0: dict | None = None, rois: dict | None = None, log=print, budget_s: float | None = 90 * 60,
               refit_cameras: bool = True, pool: "FitPool | None" = None, cue: bool = True,
               cue_endpoint_px: dict | None = None) -> dict:
    """Block-coordinate joint fit; returns the suggested spec and the diagnostics. `retrace(family, x, fits)` returns
    a family with a re-traced outline (or None to keep it). With a `pool` (FitPool) the camera block, every objective
    evaluation (per view), the Nelder-Mead start simplex and the sigma columns run in its workers; the optimiser's
    sequence of evaluations is unchanged and every value is bit-identical, so the result equals the serial fit's.
    Objective values are memoised per round (a repeated point costs nothing; the evaluation count still counts it).
    `budget_s` is reported (`over_budget`), never a stop: the owner's rule is that a progressing run is not stopped
    for time, and a time cut would make the result depend on machine speed (a pooled fit would run more rounds than
    a serial one); the fit ends on the round cap or the convergence test only.

    Cameras (FIT_VERSION fit-3): every camera block is `camera_block` (each view's §6 refit of the current shape from
    its base camera, a fresh rear family), so the cameras are the ones the gates grade that shape at. The outline is
    re-traced (`retrace`) BEFORE each camera block, at the cameras the previous block fitted (fits0 in round 0 when
    given; with no cameras yet, round 0 keeps the caller's outline), and once more before the final block: so every
    block is fitted to the exact model (shape and outline) that the round's front bound, the shape step and the gates
    read. The front bound is a hard constraint, and a camera block can break what the previous shape step left clear
    (the shape optimum hugs the bound at the cameras it was fitted at); so a round counts as converged only when its
    camera block left the front bound clear, and after the last round a FINAL camera block on the final shape gives the
    returned `fits` (the final shape's own refit cameras and rear family), at which the details, the front bound and
    sigma are computed. `termination` records "converged" or "round cap"; `front_bound_at_own_cameras` the bound at the
    final block. With refit_cameras=False (fixed cameras from fits0) there are no camera blocks at all: the outline is
    re-traced at the fixed cameras before each round's shape step."""
    t0 = time.time()
    last_beat = [t0]

    def beat(msg):
        log(f"[fit {time.time() - t0:6.0f} s] {msg}")
        last_beat[0] = time.time()
    photo = photo_cue(job, guides_lines) if cue else {}
    x = family.x0()
    fits = dict(fits0 or {})
    hist = []
    loss_prev = None
    over_budget = False
    termination = "round cap"

    def do_retrace(label) -> bool:
        """Re-trace the outline of the current (family, x) at the current cameras (before a camera block)."""
        nonlocal family
        if retrace is None or not fits:
            return False
        fam2 = retrace(family, x, fits)
        if fam2 is None:
            return False
        family = fam2
        beat(f"{label}: outline re-traced at the previous camera block's cameras")
        return True
    for rnd in range(rounds):
        traced = do_retrace(f"round {rnd}")
        model = Model.from_spec(job, family.spec(x), f"fit r{rnd}")
        fam_rec = None
        if refit_cameras or not fits:
            fits = camera_block(job, model, views, rear, log=log, pool=pool)
            fam_rec = _family_summary(fits.get("06")) if rear else None
            beat(f"round {rnd}: cameras + theta refit (each view from its base camera"
                 f"{', fresh rear family ' + str(fam_rec['family']) if fam_rec else ''})")
        rr = build_rois(job, [(model, fits)], views) if rois is None else rois
        ctx = make_context(job, fits, rr, views, guides_lines, family, x, photo, cue, cue_endpoint_px)
        evals = [0]
        memo = {}

        def batch(points):
            todo = {}
            for p in points:
                p = np.asarray(p, float)
                if p.tobytes() not in memo:
                    todo.setdefault(p.tobytes(), p)
            vals = objective_many(ctx, [safe_spec(family, p) for p in todo.values()], pool=pool)
            memo.update(zip(todo, vals))

        def f(v):
            evals[0] += 1
            v = np.asarray(v, float)
            key = v.tobytes()
            if key not in memo:
                memo[key] = objective(ctx, safe_spec(family, v), pool=pool)
            val = memo[key]
            if time.time() - last_beat[0] > 60:
                beat(f"round {rnd}: {evals[0]} evaluations, loss {val:.5f}")
            return val
        # the round start, with details: the previous shape at the cameras this round's camera block refit for it
        sx = safe_spec(family, x)
        l0d = objective(ctx, sx, details=True, pool=pool)
        memo[np.asarray(x, float).tobytes()] = l0d[0]
        fb0 = l0d[1].get("front_bound")
        fb0_clear = fb0 is None or bool(fb0.get("clear", True))
        l0 = f(x)
        x_new, l1 = shape_step(f, x, family.bounds(), maxfev, prefetch=batch if pool is not None else None)
        if l1 > l0:
            x_new, l1 = x, l0
        move = np.abs(x_new - x)
        hist.append({"round": rnd, "loss_start": float(l0), "loss": float(l1), "evaluations": evals[0],
                     "x": dict(zip(family.names, map(float, x_new))), "max_move": float(move.max()),
                     "front_bound_start": fb0, "front_bound_clear_start": fb0_clear, "rear_family": fam_rec,
                     "outline_retraced_before_block": traced})
        beat(f"round {rnd}: loss {l0:.5f} -> {l1:.5f}, max move {move.max():.3f} ({evals[0]} evaluations); front "
             f"bound at the round's refit cameras {'clear' if fb0_clear else 'ACTIVE %.3f mm' % fb0['hinge_zone_max_mm']}")
        x = x_new
        if (move.max() < RR.FIT_STOP_MOVE and loss_prev is not None and abs(loss_prev - l1) < RR.FIT_STOP_LOSS * loss_prev
                and fb0_clear):
            termination = "converged"
            break
        loss_prev = l1
        if budget_s is not None and not over_budget and time.time() - t0 > budget_s:
            over_budget = True                 # reported, never a stop: see the docstring
            beat(f"over the {budget_s / 60:.0f} min budget after round {rnd}; continuing")
    final_block = False
    final_traced = False
    if refit_cameras or not fits:
        final_traced = do_retrace("final")           # the final block is fitted to the exact returned spec
    model = Model.from_spec(job, family.spec(x), "fitted")
    if refit_cameras or not fits:
        fits = camera_block(job, model, views, rear, log=log, pool=pool)
        final_block = True
        beat("final camera block: the final shape's own refit cameras"
             + (f", rear family {fits['06']['family']['family']}" if rear and "06" in fits else ""))
    rr = build_rois(job, [(model, fits)], views) if rois is None else rois
    ctx = make_context(job, fits, rr, views, guides_lines, family, x, photo, cue, cue_endpoint_px)
    loss, det = objective(ctx, family.spec(x), details=True, pool=pool)
    srep = sigma_report(ctx, family, x, pool=pool)
    fb = det.get("front_bound")
    fbo = None if fb is None else {**fb, "at": "the final shape's own refit cameras" if final_block
                                   else "the caller's fixed cameras"}
    beat(f"done: {termination}; loss {loss:.5f} at the final cameras; front bound "
         f"{'n/a' if fb is None else ('clear' if fb['clear'] else 'ACTIVE %.3f mm' % fb['hinge_zone_max_mm'])}")
    return {"x": dict(zip(family.names, map(float, x))), "spec": family.spec(x), "family": family, "fits": fits,
            "loss": float(loss), "details": det, "sigma": srep["sigma"], "sigma_status": srep["status"],
            "cue": {"photo_steps": photo, "triangulation": ctx.tri, "enabled": bool(cue),
                    "topology": family.info.get("topology")},
            "curvature": srep["curvature"], "unobserved": unobserved(family, srep["sigma"], x, srep["status"]),
            "sigma_flags": sigma_flags(family, srep, x), "sigma_penalty": srep["penalty"],
            "penalty_at_fit": srep.get("penalty_at_x"),
            "termination": termination, "rounds_run": len(hist),
            "final_camera_block": final_block, "final_outline_retraced": final_traced,
            "front_bound_at_own_cameras": fbo,
            "rear_family_final": _family_summary(fits.get("06")) if rear else None, "fit_version": FIT_VERSION,
            "history": hist, "seconds": time.time() - t0, "over_budget": over_budget, "context": ctx}


def photo_cue(job, guides_lines: dict | None) -> dict:
    """relief_cue.photo_steps on every crease view with snapped lines (decals masked at the base camera): the photo
    half of the crease-step cue, a function of the photos alone."""
    from .relief_check import decal_mask
    out = {}
    for k, _ in CREASE_VIEWS:
        lines = (guides_lines or {}).get(k)
        if lines and k in getattr(job, "base", {}):
            out[k] = CU.photo_steps(job, k, lines, decal_mask(job, k, job.base[k]))
    return out


def make_context(job, fits, rois, views, guides_lines, family, x, photo: dict, cue: bool,
                 endpoint_px: dict | None = None) -> Context:
    """A round's objective context: the cameras and ROIs, the crease lines, the photo steps and the triangulation at
    this round's cameras (the nodes' (d, y) are the family's traced layout, fixed over a fit)."""
    ctx = Context(job, fits, rois, tuple(views), guides_lines or {}, photo_steps=photo if cue else {})
    gl = guides_lines or {}
    if cue and "05" in gl and "02" in gl and "05" in fits and "02" in fits:
        f05, f02 = crease_fit(fits, "05"), crease_fit(fits, "02")
        sp = safe_spec(family, x)
        if sp is not None and "B" in sp["nodes"] and "C" in sp["nodes"]:
            ctx.tri = CU.triangulate(job, gl["05"], gl["02"], f05, f02, {n: sp["nodes"][n] for n in ("B", "C")},
                                     sp["hinge"]["z_mm"], endpoint_px=endpoint_px)
    return ctx


def unobserved(family: Family, sig: dict, x=None, status: dict | None = None) -> list:
    """The §7 UNOBSERVED list (Astra keeps these at their defaults, §12): parameters no photo observes. Always F4's tilt
    for a faceted Prada family (F4 = B, C, E with E on the bottom edge at d_E: no observable, WP6_DESIGN §0.4 [Tr]; d_E
    is not a free parameter); and every free parameter whose objective is FLAT at the optimum (|curvature| <=
    CURV_FLAT, on the photo terms, with no penalty acting along it). A parameter at a band bound (the photos push it
    out of the band), at a non-minimum (negative curvature of the photo terms: the fit has not converged along it),
    with a hard-constraint penalty acting along it, or with an infeasible stencil is NOT unobserved: its fitted value
    stands and `sigma_flags` lists it. Without `status` (legacy callers) an infinite sigma means flat and a NaN sigma
    is not listed."""
    out = []
    if not family.slab_mode and family.builder is FX.prada_spec:
        out.append({"parameter": "F4 tilt (d_E: F4's corner E on the bottom edge)",
                    "value": float(family.base.get("d_E", 55.0)),
                    "why": "no observable (WP6_DESIGN 0.4); not fitted, kept at its default"})
    xs = dict(zip(family.names, map(float, x))) if x is not None else {}
    for name in family.names:
        s = sig.get(name)
        st = (status or {}).get(name)
        if st is None:
            st = "flat" if (s is not None and math.isinf(s)) else "ok"
        if st == "flat":
            out.append({"parameter": name, "value": xs.get(name), "sigma": s,
                        "why": "flat objective at the optimum: no photo constrains it here"})
    return out


SIGMA_WHY = {"at_band_bound": "at a band bound: the photos push it out of its band (the fitted value stands)",
             "not_at_minimum": "negative curvature of the photo terms with no penalty acting: the fit is not at a "
                               "minimum along it (not converged)",
             "penalty_active": "a hard-constraint penalty (front bound or dn >= 3 deg at a confirmed crease) acts along "
                               "it at the fit or inside its sigma stencil: the constraint, not photo noise, holds it "
                               "there; its sigma (photo terms only) is not a photo uncertainty and stays off the relief "
                               "sheet",
             "infeasible_stencil": "a sigma stencil point admits no valid spec (a refusal cliff): no sigma"}
SIGMA_PENALTY_TOL = 1e-9          # a penalty that changes by more than this across the stencil acts along the parameter


def sigma_flags(family: Family, srep: dict, x) -> list:
    """Free parameters whose sigma is undefined, or not a photo uncertainty, for a reason other than a flat objective
    (see `unobserved`); a penalty_active entry carries the penalties at (x, x + h, x - h)."""
    xs = dict(zip(family.names, map(float, x)))
    out = []
    for n in family.names:
        st = srep["status"].get(n)
        if st not in SIGMA_WHY:
            continue
        e = {"parameter": n, "value": xs.get(n), "status": st, "curvature": srep["curvature"].get(n),
             "why": SIGMA_WHY[st]}
        if st == "penalty_active":
            e["penalty"] = (srep.get("penalty") or {}).get(n)
            e["held_by"] = held_by(e["penalty"] or {})
        out.append(e)
    return out


def held_by(pen: dict) -> list:
    """What holds a penalty_active parameter: each penalty component (front bound, dn >= 3 deg, hinge) that changes
    across the parameter's stencil (by > SIGMA_PENALTY_TOL), with the stencil points where it is active (> 0): e.g.
    the front bound at "plus" and dn at "minus" = held between two walls, no photo term sets the value."""
    out = []
    for comp in ("front_bound", "dn", "hinge"):
        vals = {k: (pen.get(k) or {}).get(comp) for k in ("x", "plus", "minus")}
        if any(v is None for v in vals.values()):
            continue
        if max(vals.values()) - min(vals.values()) > SIGMA_PENALTY_TOL:
            out.append({"penalty": comp, "active_at": [k for k, v in vals.items() if v > 0.0],
                        "values": vals})
    return out


SIMPLEX_FRACTION = 0.15


def shape_step(f, x0, bounds, maxfev: int, prefetch=None):
    """The shape block of one round: Nelder-Mead (adaptive, initial simplex 15 % of each band; bounds by clipping plus
    a steep penalty outside) then a bounded Powell polish. Nelder-Mead is used first because the silhouette terms are
    pixel-quantised and the dn >= 3 deg constraint is a penalty kink: Powell's line searches alone stall on them (the
    S1 round trip). `prefetch(points)` (a pooled fit's memo filler) receives the start simplex's points exactly as
    Nelder-Mead will evaluate them, so they are computed in one parallel batch. Returns (x, f(x))."""
    lo = np.array([b[0] for b in bounds], float)
    hi = np.array([b[1] for b in bounds], float)
    x0 = np.clip(np.asarray(x0, float), lo, hi)
    out = lambda v: float(np.sum(np.maximum(lo - v, 0) + np.maximum(v - hi, 0)))
    g = lambda v: f(np.clip(v, lo, hi)) + 10.0 * out(v)
    steps = SIMPLEX_FRACTION * (hi - lo)
    sim = [x0] + [x0 + np.eye(len(x0))[i] * steps[i] * (-1.0 if x0[i] + steps[i] > hi[i] else 1.0)
                  for i in range(len(x0))]
    if prefetch is not None:
        prefetch([np.clip(v, lo, hi) for v in np.array(sim)])
    r = optimize.minimize(g, x0, method="Nelder-Mead",
                          options={"initial_simplex": np.array(sim), "maxfev": maxfev, "xatol": 0.005,
                                   "fatol": 1e-5, "adaptive": True})
    x1 = np.clip(r.x, lo, hi)
    r2 = optimize.minimize(f, x1, method="Powell", bounds=list(zip(lo, hi)),
                           options={"maxfev": max(40, maxfev // 3), "xtol": 0.005, "ftol": 1e-5})
    cands = [(f(x1), x1), (float(r2.fun), np.clip(r2.x, lo, hi))]
    fx, x = min(cands, key=lambda t: t[0])
    return x, float(fx)


def sigma(ctx: Context, family: Family, x, rel_step: float = 0.05, pool: "FitPool | None" = None) -> dict:
    """sigma_report(...)["sigma"]."""
    return sigma_report(ctx, family, x, rel_step=rel_step, pool=pool)["sigma"]


def _photo_and_penalty(val):
    """(photo terms, penalty record) of one objective_many(details=True) value; photo None for a refused spec."""
    tot, det = val
    if "penalty" not in det:
        return None, {"refused": det.get("refused", "builder")}
    pen = float(det["penalty"])
    return float(tot) - pen, {"total": pen, "front_bound": float(det.get("penalty_front_bound", 0.0)),
                              "dn": float(det.get("penalty_dn", 0.0)), "hinge": float(det.get("penalty_hinge", 0.0))}


def sigma_report(ctx: Context, family: Family, x, rel_step: float = 0.05, pool: "FitPool | None" = None) -> dict:
    """{"sigma", "status", "curvature", "penalty"} per free parameter, from the PHOTO terms of the objective only (the
    silhouettes, crease reprojection and transfer: `objective` minus its hard-constraint penalties, which measure a
    wall, not the photos). sigma_i = sqrt(2 NOISE_LOSS / f''_i) from a central second difference of the photo terms.
    status:
      "ok"                 finite sigma, no penalty acting along the parameter
      "flat"               |f''| <= CURV_FLAT, no penalty acting: sigma inf, no photo constrains it (UNOBSERVED)
      "not_at_minimum"     f'' < -CURV_FLAT, no penalty acting: sigma NaN (not at a minimum of the photo terms)
      "penalty_active"     a penalty (front bound, dn >= 3 deg) is active at x or at a stencil point and changes across
                           the stencil (by > SIGMA_PENALTY_TOL): the constraint holds the parameter; sigma = the photo
                           terms' sigma when their curvature is positive (else NaN), never shown as photo noise
      "at_band_bound"      on a band bound: no central difference, sigma NaN
      "infeasible_stencil" x or a stencil point has no valid spec: sigma NaN
    "penalty" per parameter: the penalties (total, front bound, dn, hinge) at x, x + h and x - h. A penalty active at x
    but constant along a parameter does not change that parameter's photo curvature, so its status stays the photo one.
    The 1 + 2 n evaluations are independent: one batch (in parallel with a `pool`)."""
    out, status, curvs, pens = {}, {}, {}, {}
    cols = []
    for i, (name, lo, hi) in enumerate(family.free):
        h = max(1e-3, rel_step * (hi - lo))
        xp, xm = x.copy(), x.copy()
        xp[i] = min(hi, x[i] + h)
        xm[i] = max(lo, x[i] - h)
        cols.append((xp, xm))
    vals = objective_many(ctx, [safe_spec(family, v) for v in [x] + [v for c in cols for v in c]], details=True,
                          pool=pool)
    f0, p0 = _photo_and_penalty(vals[0])
    for i, (name, lo, hi) in enumerate(family.free):
        xp, xm = cols[i]
        fp, pp = _photo_and_penalty(vals[1 + 2 * i])
        fm, pm = _photo_and_penalty(vals[2 + 2 * i])
        pens[name] = {"x": p0, "plus": pp, "minus": pm}
        hp, hm = xp[i] - x[i], x[i] - xm[i]
        if hp <= 0 or hm <= 0:
            out[name], status[name], curvs[name] = float("nan"), "at_band_bound", None
            continue
        if f0 is None or fp is None or fm is None:
            out[name], status[name], curvs[name] = float("nan"), "infeasible_stencil", None
            continue
        curv = 2.0 * (fp * hm + fm * hp - f0 * (hp + hm)) / (hp * hm * (hp + hm))
        curvs[name] = float(curv)
        tp = (p0["total"], pp["total"], pm["total"])
        acting = max(tp) > 0.0 and (max(tp) - min(tp)) > SIGMA_PENALTY_TOL
        if acting:
            out[name] = float(math.sqrt(2 * NOISE_LOSS / curv)) if curv > CURV_FLAT else float("nan")
            status[name] = "penalty_active"
        elif curv > CURV_FLAT:
            out[name], status[name] = float(math.sqrt(2 * NOISE_LOSS / curv)), "ok"
        elif curv >= -CURV_FLAT:
            out[name], status[name] = float("inf"), "flat"
        else:
            out[name], status[name] = float("nan"), "not_at_minimum"
    return {"sigma": out, "status": status, "curvature": curvs, "penalty": pens, "penalty_at_x": p0}


def profiles(ctx: Context, family: Family, x, names, n: int = 9, span: dict | None = None,
             pool: "FitPool | None" = None) -> dict:
    """Per parameter: the total objective and every view's loss along a sweep (others held). Every sweep point is
    independent: one batch (in parallel with a `pool`)."""
    out = {}
    sweeps = []
    for name in names:
        i = family.names.index(name)
        lo, hi = family.free[i][1:]
        s = (span or {}).get(name, 0.25 * (hi - lo))
        vals = np.linspace(max(lo, x[i] - s), min(hi, x[i] + s), n)
        for v in vals:
            xv = x.copy()
            xv[i] = v
            sweeps.append((name, v, xv))
    res = objective_many(ctx, [safe_spec(family, xv) for _, _, xv in sweeps], details=True, pool=pool)
    for name in names:
        rows = []
        for (nm, v, _), (tot, det) in zip(sweeps, res):
            if nm != name:
                continue
            rows.append({"value": float(v), "total": float(tot),
                         "views": {k: d["loss"] for k, d in det.get("views", {}).items()},
                         "transfer_mm": (det.get("transfer") or {}).get("mean_mm"),
                         "crease_reproj_mm": det.get("crease_reproj_mm")})
        out[name] = rows
    return out


def conflict_table(prof: dict, sig: dict) -> list:
    """Views whose own optimum of a parameter differs from another view's by more than both their noises."""
    out = []
    for name, rows in prof.items():
        vals = np.array([r["value"] for r in rows])
        views = sorted({k for r in rows for k in r["views"]})
        opt = {k: float(vals[int(np.argmin([r["views"].get(k, np.inf) for r in rows]))]) for k in views}
        s = sig.get(name, float("nan"))
        spread = max(opt.values()) - min(opt.values()) if opt else 0.0
        out.append({"parameter": name, "view_optima": opt, "spread": spread, "sigma": s,
                    "conflict": bool(np.isfinite(s) and spread > max(RR.STOP_C_MIN_MM, RR.STOP_C_SIGMA * s))})
    return out


def own_optimum(vals, obs, signed: bool) -> float | None:
    """An observable's own optimum over a sweep (S3): the zero crossing of a signed observable (else its smallest
    |value|), the minimum of an unsigned one. None when fewer than 3 values are finite, or when the profile is
    constant: an observable that does not change over the sweep does not see the parameter and has no optimum (the
    argmin of a constant picked the sweep's first delta, so a parameter invisible in a view read as an optimum at the
    sweep's edge and raised STOP-C; WP6 resume run 2026-10-06: relief at C in the 06 hinge-zone envelope)."""
    vals, obs = np.asarray(vals, float), np.asarray(obs, float)
    ok = np.isfinite(obs)
    if ok.sum() < 3:
        return None
    vals, obs = vals[ok], obs[ok]
    if float(np.ptp(obs)) <= 1e-12 * max(1.0, float(np.abs(obs).max())):
        return None
    if signed:
        sgn = np.sign(obs)
        idx = np.nonzero(np.diff(sgn) != 0)[0]
        if len(idx):
            i = idx[np.argmin(np.abs(vals[idx] - np.median(vals)))]
            return float(vals[i] - obs[i] * (vals[i + 1] - vals[i]) / (obs[i + 1] - obs[i]))
        return float(vals[int(np.argmin(np.abs(obs)))])
    return float(vals[int(np.argmin(obs))])


def s3_table(ctx: Context, family: Family, x, sig: dict, *, level_names=("x_in0", "x_in_ze"), relief_name="r_C",
             span_level: float = 2.0, span_relief: float = 2.5, n: int = 9) -> dict:
    """For the zone level at B (a lateral shift of the zone's inner line) and the relief at C: each observable's own
    optimum (03 hinge-zone offset zero, 05 / 02 crease lines, 05 -> 02 transfer, 06 hinge-zone envelope zero) vs the
    joint optimum. STOP-C when one lies outside max(1 mm, 2 sigma)."""
    job = ctx.job
    out = {"parameters": {}, "stop_c": False}
    sweeps = {"zone_level_B": ([family.names.index(n_) for n_ in level_names if n_ in family.names], span_level),
              "relief_C": ([family.names.index(relief_name)] if relief_name in family.names else [], span_relief)}
    for pname, (idx, span) in sweeps.items():
        if not idx:
            continue
        deltas = np.linspace(-span, span, n)
        obs = {"03_offset": [], "06_hinge_env": [], "transfer": [], "crease_02": []}
        for dl in deltas:
            xv = x.copy()
            for i in idx:
                xv[i] = np.clip(x[i] + dl, *family.free[i][1:])
            try:
                model = Model.from_spec(job, family.spec(xv), "s3")
            except (TempleError, ValueError):
                for k in obs:
                    obs[k].append(np.nan)
                continue
            for key, name in (("03", "03_offset"), ("06", "06_hinge_env")):
                if key in ctx.fits:
                    f = ctx.fits[key]
                    r = render_model(job, model, key, f["camera"], f["theta"], points=True)
                    env = envelopes(job, key, r, f["camera"])
                    v = [env[s]["hinge"]["outer_signed_median_mm"] for s in (1, -1) if env[s].get("hinge")]
                    obs[name].append(float(np.mean(v)) if v else np.nan)
                else:
                    obs[name].append(np.nan)
            tr = transfer_all(ctx, model) if "02" in ctx.guides_lines else None
            obs["transfer"].append(tr["mean_mm"] if tr and tr["mean_mm"] is not None else np.nan)
            cr = []
            if "02" in ctx.guides_lines and model.build is not None and model.build.spec.facets:
                cf02 = crease_fit(ctx.fits, "02")
                for cname, line in ctx.guides_lines["02"].items():
                    nodes = ctx.crease_nodes.get(cname)
                    q = model_crease_px(job, model.build, cf02, 1, *nodes) if nodes else None
                    if q is not None:
                        cr.append(_line_dist_mm(job, cf02["camera"], q, line))
            obs["crease_02"].append(float(np.mean(cr)) if cr else np.nan)
        s = max(sig.get(family.names[idx[0]], float("nan")), 0.0)
        tol = max(RR.STOP_C_MIN_MM, RR.STOP_C_SIGMA * s) if np.isfinite(s) else RR.STOP_C_MIN_MM
        rows = {}
        for name, vals in obs.items():
            o = own_optimum(deltas, vals, signed=name in ("03_offset", "06_hinge_env"))
            rows[name] = {"own_optimum_delta": o, "values": [None if not np.isfinite(v) else float(v) for v in vals],
                          "outside": bool(o is not None and abs(o) > tol)}
            out["stop_c"] |= rows[name]["outside"]
        out["parameters"][pname] = {"tolerance": tol, "deltas": deltas.tolist(), "observables": rows}
    return out


# --------------------------------------------------------------------------------------------- S1
# The synthetic photos' light, in camera coordinates (right, up, toward the camera): ONE Lambert key light straight
# above the camera, 40 deg up from the view axis. Chosen 2026-10-06 against the real Prada side views (proof.json P2,
# refine_line steps at +-3 px): every traced crease is visible in both 05 and 01 at 4.1-6.0 L* (05 AB 5.94 AC -5.73
# BC 5.98; 01 AB 5.25 AC 4.13 BC -4.08), and |AC| ~ |BC| within each view, which a left/right light component would
# split (05 vs 01 are mirror views). On the default synthetic truth this light gives AB 5.9, AC 3.9, BC 3.9 L* in
# both side views (02: AB 6.7, AC 2.0, BC 6.7; the real 02 has AC untraceable). Two earlier lights were wrong:
#  - the key light (-0.35, 0.55, 0.76) alone: its left component hides AC in 05 (1.6 L*) and BC in 01 (none), so
#    corner C could not be traced in either side view;
#  - that key + 0.4 x an opposite light (0.6, -0.3, 0.74): two unclipped Lambert lights are one light along their
#    weighted sum, here nearly the view axis, so every crease fell to 1.6-2.4 L*, below the frozen STEP_MIN_L (3 L*)
#    and below every real crease: the crease detector then (correctly) found 1 segment per side view.
# SYNTH_RENDER names the photo model; the proof records it with S1 so a resumed run never keeps an S1 graded on
# photos rendered differently.
SYNTH_LIGHT = (0.0, math.sin(math.radians(40.0)), math.cos(math.radians(40.0)))
SYNTH_RENDER = "lambert-1: one key light above the camera at 40 deg; gray 70 + 150 x n.l on the model, 250 background"


class SyntheticJob:
    """A job whose photos and mattes are renders of a known model (S1, and the synthetic tests): exact mattes and
    Lambert-shaded photos (creases show as L* steps; SYNTH_LIGHT) through the TRUE cameras; `base` holds those cameras
    perturbed by each view's contour error in px (what a fit starts from). Build it from a real Job (`from_job`, its
    frame, front parts and photo sizes) or `standalone` (any front mesh, frame and image sizes: no data needed)."""

    def __init__(self, *, frame, V0, F0, hinge, cams, shapes, views, view_names, truth, theta, perturb_px=None,
                 pivot_mm=None, seed: int = 7, light=SYNTH_LIGHT, glb=None, cfg=None):
        self.frame, self.V0, self.F0, self.hinge = frame, V0, F0, dict(hinge)
        self.true_cams, self.true_shape, self.views, self.view_name = dict(cams), dict(shapes), dict(views), dict(view_names)
        self.truth, self.truth_theta = truth, theta
        self.pivot_mm = np.zeros(3) if pivot_mm is None else np.asarray(pivot_mm, float)
        self.pivot = frame.to_norm(self.pivot_mm[None])[0]
        self.glb, self.cfg = glb, cfg or {"skip": ()}
        self.light = np.asarray(light, float) / np.linalg.norm(light)
        self.matte_mode = "synthetic"
        self.delivered_temples = truth.temples
        self._mattes, self._photos, self._front = {}, {}, {}
        rng = np.random.default_rng(seed)
        self.base = {}
        for k, cam in cams.items():
            e = float((perturb_px or {}).get(k, 0.0))
            a = rng.uniform(0, 2 * math.pi)
            self.base[k] = Camera(cam.yaw, cam.pitch, cam.roll, cam.perspective, cam.scale * (1 + 0.002 * e),
                                  cam.center_x + e * math.cos(a), cam.center_y + e * math.sin(a))

    @staticmethod
    def from_job(job: Job, truth: Model, theta: dict, cams: dict, perturb_px: dict, seed: int = 7) -> "SyntheticJob":
        return SyntheticJob(frame=job.frame, V0=job.V0, F0=job.F0, hinge=job.hinge, cams=cams,
                            shapes={k: job.shape(k) for k in cams}, views=job.views, view_names=job.view_name,
                            truth=truth, theta=theta, perturb_px=perturb_px, pivot_mm=job.pivot_mm, seed=seed)

    # the Job interface used by relief_views / relief_trace / relief_check
    def shape(self, key):
        return self.true_shape[key]

    def kind(self, key) -> str:
        return self.views[key][1]

    def front_scene(self):
        return raster.get_scene(self.V0, self.F0, self.frame)

    def hinge_ppm(self, cam, side: int = 1) -> float:
        return Job.hinge_ppm(self, cam, side)

    def front_ppm(self, cam) -> float:
        return float(C.px_per_mm_at(cam, self.frame, self.pivot_mm))

    def _render(self, key):
        cam = self.true_cams[key]
        r = render_model(self, self.truth, key, cam, self.truth_theta[key], points=False, normals=True)
        Rr, Ru, Rt = raster.camera_basis(cam)
        n = r["normal"].copy()
        n[(n @ Rt) < 0] *= -1
        lw = self.light[0] * Rr + self.light[1] * Ru + self.light[2] * Rt
        shade = np.clip(n @ lw, 0, 1)
        val = np.where(r["mask"], 70 + 150 * shade, 250.0)
        img = np.repeat(np.clip(val, 0, 255)[..., None], 3, axis=2).astype(np.uint8)
        return img, r["mask"]

    def photo(self, key):
        if key not in self._photos:
            self._photos[key], self._mattes[key] = self._render(key)
        return self._photos[key]

    def matte(self, key):
        if key not in self._mattes:
            self._photos[key], self._mattes[key] = self._render(key)
        return self._mattes[key]


def synthetic_front(width_mm: float = 128.0) -> tuple[np.ndarray, np.ndarray]:
    """A stand-in front for standalone synthetic jobs: a bar (x +-width/2, y -18..14, z -9..4) closed box."""
    w = width_mm / 2.0
    lo, hi = np.array([-w, -18.0, -9.0]), np.array([w, 14.0, 4.0])
    V = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
    F = np.array([[0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5], [0, 4, 5], [0, 5, 1], [2, 3, 7], [2, 7, 6],
                  [0, 2, 6], [0, 6, 4], [1, 5, 7], [1, 7, 3]])
    return V, F


SYNTH_VIEWS = {"05": ("synthetic", "side", "left", (90.5, 13.6, 0.0, 0.22, 5.5)),
               "01": ("synthetic", "side", "right", (-90.8, 13.7, 0.0, 0.22, 5.5)),
               "02": ("synthetic", "angled", "angled", (40.0, 18.0, 0.0, 0.20, 5.5)),
               "03": ("synthetic", "top", "top", (0.0, 58.0, 0.0, 0.30, 5.0)),
               "04": ("synthetic", "front", "front", (0.0, 10.4, 0.0, 0.09, 5.5)),
               "06": ("synthetic", "rear", "back", (180.4, 10.4, 0.0, 0.09, 5.0))}


def model_from_spec_static(spec: dict, label: str) -> Model:
    from ..g3d.temple import build as TB
    tb = TB.build_temple(spec)
    return Model(label, {s: (tb.world(s, opening_deg=0.0)[0], tb.world(s, opening_deg=0.0)[2]) for s in (1, -1)},
                 spec=spec, build=tb)


def synthetic_setup(truth_spec: dict, theta: dict | None = None, *, shape=(560, 900), perturb_px=None,
                    views=tuple(SYNTH_VIEWS), seed: int = 7) -> SyntheticJob:
    """A standalone synthetic Prada-like job: the stand-in front bar, the truth temples, six cameras placed like the
    Prada photos (`SYNTH_VIEWS`), per-view theta (default 0), perturbed base cameras."""
    from .camera import NormFrame
    truth = model_from_spec_static(truth_spec, "truth")
    V0, F0 = synthetic_front()
    allV = np.vstack([V0] + [truth.temples[s][0] for s in (1, -1)])
    frame = NormFrame.from_bbox(allV)
    cams = {k: raster.view_camera(frame, *SYNTH_VIEWS[k][3][:2], SYNTH_VIEWS[k][3][4], shape,
                                  roll=SYNTH_VIEWS[k][3][2], perspective=SYNTH_VIEWS[k][3][3],
                                  center_mm=np.array([0.0, -2.0, -60.0])) for k in views}
    th = {k: dict((theta or {}).get(k, {1: 0.0, -1: 0.0})) for k in views}
    return SyntheticJob(frame=frame, V0=V0, F0=F0, hinge=FX.HINGE, cams=cams, shapes={k: shape for k in views},
                        views={k: SYNTH_VIEWS[k][:2] for k in views}, view_names={k: SYNTH_VIEWS[k][2] for k in views},
                        truth=truth, theta=th, perturb_px=perturb_px, seed=seed)


def prada_base_params(**over) -> dict:
    """The fixture Prada family parameters (outline, zone, arm knots, node positions, relief) for synthetic runs."""
    inner, thick = np.array(FX.INNER), np.array(FX.THICK)
    arm = [[k, float(np.interp(k, inner[:, 0], inner[:, 1])), float(np.interp(k, thick[:, 0], thick[:, 1]))]
           for k in sorted(set(inner[:, 0]) | set(thick[:, 0])) if k > FX.ZONE_END]
    p = {"top": FX.TOP, "bottom": FX.BOTTOM, "zone_end": FX.ZONE_END, "x_in0": 65.5, "x_in_ze": 61.8, "t_zone": 5.0,
         "arm": arm, "r_A": 0.5, "r_B": 0.0, "r_C": 3.0, "rise": -0.25}
    p.update(over)
    return p


def compass_search(f, x0, bounds, steps0, min_steps, maxfev: int = 300, log=None):
    """Derivative-free polish robust to pixel-quantised objectives: try +- step on each parameter in turn, keep any
    improvement, halve the steps of a sweep that found none; stop at the minimum steps or maxfev."""
    lo = np.array([b[0] for b in bounds], float)
    hi = np.array([b[1] for b in bounds], float)
    x = np.clip(np.asarray(x0, float), lo, hi)
    fx = f(x)
    n = 1
    steps = np.asarray(steps0, float).copy()
    mins = np.asarray(min_steps, float)
    while n < maxfev and np.any(steps > mins):
        improved = False
        for i in range(len(x)):
            if steps[i] <= mins[i]:
                continue
            for sgn in (1.0, -1.0):
                xt = x.copy()
                xt[i] = float(np.clip(x[i] + sgn * steps[i], lo[i], hi[i]))
                if xt[i] == x[i]:
                    continue
                ft = f(xt)
                n += 1
                if ft < fx:
                    x, fx, improved = xt, ft, True
                    break
            if n >= maxfev:
                break
        if not improved:
            steps = np.where(steps > mins, steps * 0.5, steps)
    return x, float(fx), n


# --------------------------------------------------------------------------------------------- the process pool
POOL_RESERVE = 2                 # owner rule: long computations use os.cpu_count() - 2 workers
POOL_HEARTBEAT_S = 30.0          # a pooled batch prints progress at least this often (the owner rule is 2 minutes)


def pool_workers() -> int:
    return max(1, (os.cpu_count() or 4) - POOL_RESERVE)


def opt_out_ecoqos() -> bool:
    """Opt this process out of Windows EcoQoS execution-speed throttling (SetProcessInformation,
    ProcessPowerThrottling; the opt-out is not inherited, so every worker calls it). False off Windows or on failure."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        class _State(ctypes.Structure):
            _fields_ = [("Version", wintypes.ULONG), ("ControlMask", wintypes.ULONG), ("StateMask", wintypes.ULONG)]
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        s = _State(1, 1, 0)                     # version 1, control EXECUTION_SPEED, state 0 = never throttle
        return bool(k32.SetProcessInformation(wintypes.HANDLE(k32.GetCurrentProcess()), 4, ctypes.byref(s),
                                              ctypes.sizeof(s)))
    except Exception:                                            # noqa: BLE001
        return False


_W = {"jobs": OrderedDict(), "ctx": OrderedDict(), "ecoqos": None}


def _worker_init() -> None:
    _W["ecoqos"] = opt_out_ecoqos()


def _w_load(cache: OrderedDict, key: str, path: str, keep: int):
    if key in cache:
        cache.move_to_end(key)
        return cache[key]
    obj = pickle.loads(Path(path).read_bytes())
    if isinstance(obj, tuple) and len(obj) == 2 and isinstance(obj[0], type):   # (class, state): a job
        cls, state = obj
        obj = cls.__new__(cls)
        obj.__dict__.update(state)
    cache[key] = obj
    while len(cache) > keep:
        cache.popitem(last=False)
    return obj


def _w_job(key, path):
    return _w_load(_W["jobs"], key, path, 2)


def _w_model(job, temples):
    return Model.delivered(job) if temples is None else Model("pool", temples)


def _task_ping(jkey, jpath) -> dict:
    _w_job(jkey, jpath)
    time.sleep(0.2)                              # long enough for the pings to spread over every worker
    return {"pid": os.getpid(), "ecoqos_opt_out": _W["ecoqos"]}


def _task_view(jkey, jpath, ckey, cpath, temples, k) -> dict:
    job = _w_job(jkey, jpath)
    c = _w_load(_W["ctx"], ckey, cpath, 4)
    return _view_term(job, Model("pool", temples), k, c["fits"][k], c["rois"][k], c["zone_end"])


def _task_fit_view(jkey, jpath, temples, k, cam0) -> dict:
    job = _w_job(jkey, jpath)
    job._front.clear()                  # memory: a camera task's front renders are for cameras no later task reuses
    return fit_model_view(job, _w_model(job, temples), k, cam0)


def _task_refit_camera(jkey, jpath, temples, key, cam0, theta, kw) -> tuple:
    job = _w_job(jkey, jpath)
    job._front.clear()
    return refit_camera(job, _w_model(job, temples), key, cam0, theta, **kw)


class FitPool:
    """Persistent worker processes for the WP6 fit (owner rule: os.cpu_count() - 2 workers, each opted out of EcoQoS).

    A job (its mattes computed here once, its front-render cache emptied) and each objective context (cameras, theta,
    ROIs) are written once to a private temp folder; a worker loads one on first use and keeps it, so a task carries
    only keys and the temple meshes (from the parent's own build, so every worker renders the parent's exact arrays).
    Results come back in submission order and every reduction runs in the serial order, so a pooled fit is the serial
    fit bit for bit (tests/test_temple_fit_parallel.py). While it waits it prints progress at least every
    POOL_HEARTBEAT_S. A worker exception is raised in the parent, as the serial code would raise it. A Context must
    not be mutated after a pooled evaluation used it (it is broadcast once)."""

    def __init__(self, workers: int | None = None, log=print, heartbeat_s: float = POOL_HEARTBEAT_S):
        import multiprocessing as mp
        from concurrent.futures import ProcessPoolExecutor
        self.workers = pool_workers() if workers is None else max(1, int(workers))
        self.log = log
        self.heartbeat_s = heartbeat_s
        self.dir = Path(tempfile.mkdtemp(prefix="g3d_wp6_pool_"))
        self._ex = ProcessPoolExecutor(max_workers=self.workers, mp_context=mp.get_context("spawn"),
                                       initializer=_worker_init)
        self._jobs, self._ctxs = {}, OrderedDict()
        self.stats = {"tasks": 0, "batches": 0, "wait_s": 0.0}
        opt_out_ecoqos()

    # ---------------------------------------------------------------- broadcast
    def job_ref(self, job) -> tuple:
        ent = self._jobs.get(id(job))
        if ent is None:
            for k in job.views:
                job.matte(k)
            state = dict(job.__dict__)
            state["_front"] = {}
            state.pop("_outside_px", None)
            state.pop("_decals", None)                 # relief_check.decal_mask's cache (parent only)
            state.pop("_cue_L", None)                  # relief_cue's blurred L* (parent only)
            state.pop("_hinge_parts", None)            # relief_hinge's parts (parent only)
            n = len(self._jobs)
            path = self.dir / f"job{n}.pkl"
            path.write_bytes(pickle.dumps((type(job), state), protocol=pickle.HIGHEST_PROTOCOL))
            ent = (job, f"job{n}", str(path))         # holding the job keeps its id unique
            self._jobs[id(job)] = ent
        return ent[1], ent[2]

    def ctx_ref(self, ctx: Context) -> tuple:
        ent = self._ctxs.get(id(ctx))
        if ent is None:
            payload = {"fits": {k: {"camera": ctx.fits[k]["camera"], "theta": dict(ctx.fits[k]["theta"])}
                                for k in ctx.views},
                       "rois": {k: ctx.rois[k] for k in ctx.views}, "zone_end": ctx.zone_end}
            blob = pickle.dumps(payload, protocol=pickle.HIGHEST_PROTOCOL)
            key = "ctx" + hashlib.blake2b(blob, digest_size=12).hexdigest()
            path = self.dir / f"{key}.pkl"
            if not path.exists():
                path.write_bytes(blob)
            ent = (ctx, key, str(path))               # holding the context keeps its id unique
            self._ctxs[id(ctx)] = ent
            while len(self._ctxs) > 8:                  # each holds its ROIs (~8 MB on Prada)
                self._ctxs.popitem(last=False)
        return ent[1], ent[2]

    # ---------------------------------------------------------------- tasks
    def submit(self, fn, *args):
        self.stats["tasks"] += 1
        return self._ex.submit(fn, *args)

    def gather(self, futs: list, tag: str = "pool") -> list:
        from concurrent.futures import FIRST_COMPLETED, wait
        t0 = last = time.time()
        pending = set(futs)
        while pending:
            _, pending = wait(pending, timeout=self.heartbeat_s, return_when=FIRST_COMPLETED)
            if pending and time.time() - last >= self.heartbeat_s:
                last = time.time()
                if self.log:
                    self.log(f"    [pool {tag}] {len(futs) - len(pending)} / {len(futs)} tasks done, "
                             f"{last - t0:.0f} s")
        self.stats["batches"] += 1
        self.stats["wait_s"] += time.time() - t0
        return [f.result() for f in futs]

    def refit_many(self, job, model):
        """A `rear_family(refit_many=...)` hook: [(key, cam0, theta, kwargs)] -> [(camera, info)] in call order."""
        jr = self.job_ref(job)

        def run(calls):
            return self.gather([self.submit(_task_refit_camera, *jr, model.temples, k, cam0, th, kw)
                                for k, cam0, th, kw in calls], "rear family")
        return run

    def warm(self, job) -> list:
        """Start every worker and load `job` in each (so timings exclude start-up); returns the distinct
        (pid, EcoQoS opt-out) of the workers that answered."""
        jr = self.job_ref(job)
        futs = [self.submit(_task_ping, *jr) for _ in range(3 * self.workers)]
        seen = {}
        for r in self.gather(futs, "warm"):
            seen[r["pid"]] = r
        return list(seen.values())

    def close(self) -> None:
        self._ex.shutdown(wait=True, cancel_futures=True)
        shutil.rmtree(self.dir, ignore_errors=True)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False
