"""WP6 joint fit (plan/WP6_DESIGN.md §7 "fit_temple"): SUGGESTED temple parameters from all six photos. It never writes
a scene.

    Family            a temple parameterisation: a spec builder, base keyword arguments, the free parameters with
                      their bands (Prada: x_in at 0 and at the zone end, zone thickness, r_A, r_B, r_C, F1 rise, arm
                      offset, tip offset, two plan knots); `slab()` = the same family with every relief 0, no facets
    objective         sum over views of (1 - ROI IoU + 4 x contour / ROI width) at fixed cameras and theta, + crease-line
                      reprojection on 05 / 01 and 05 -> 02 crease transfer (1 mm = 0.02), + the hard constraints as
                      steep penalties (front bound, dn >= 3 deg at every confirmed crease)
    fit_temple        block coordinates: cameras + theta (relief_views.fit_model_view, the rear family in round 0) | shape
                      (Nelder-Mead then a bounded Powell polish, maxfev 300: shape_step); <= 4 rounds; stops when every parameter moves < 0.1 and the loss < 1 %; the
                      outline is re-traced each round (relief_trace.trace_outline), never optimised; heartbeat >= 60 s
    sigma / profiles  sigma per parameter from the objective's curvature (the change one noise unit buys), per-view
                      profiles, the conflict table (views pulling a parameter apart beyond their noise), the S3 table
                      (each observable's own optimum for the zone level at B and the relief at C vs the joint one)
    synthetic_job     S1: the tool's own temple rendered through the fitted cameras (each perturbed by its view's
                      measured contour error) as Lambert-shaded photos and exact mattes, to trace and fit back
"""
from __future__ import annotations

import copy
import math
import time
from dataclasses import dataclass, field

import numpy as np
from scipy import optimize

from ..g3d.temple import fixtures as FX
from ..g3d.temple.spec import TempleError
from . import cameras as C
from . import raster
from . import relief_rules as RR
from .camera import Camera
from .relief_views import (Job, Model, fit_model_view, fit_theta, front_bound, render_model, roi_metrics,
                           rot_about_hinge, temple_roi, envelopes)

# r_B is FIXED at 0 by default (B on the base surface): a facet meets the base only along its zero line, and with
# r_B != 0 F1's and F4's zero lines must cross behind B, which leaves the family a non-convex feasible set (the
# optimiser hits refusal cliffs). The zone level at B stays free through x_in and the zone thickness, and C - B = r_C.
# Pass free=PRADA_FREE_RB to fit r_B as well.
PRADA_FREE = (("x_in0", 3.0), ("x_in_ze", 3.0), ("t_zone", (3.5, 7.5)), ("r_A", (0.02, 3.0)),
              ("r_C", (0.0, 7.0)), ("rise", (-0.6, -0.02)), ("arm_dx", 4.0), ("tip_dx", 6.0), ("k1", 3.0),
              ("k2", 3.0))
PRADA_FREE_RB = PRADA_FREE[:4] + (("r_B", (-1.0, 3.0)),) + PRADA_FREE[4:]
SLAB_FREE = ("x_in0", "x_in_ze", "t_zone", "arm_dx", "tip_dx", "k1", "k2")
NOISE_LOSS = 0.005            # sigma: the parameter change that raises the objective by this much (0.5 % loss)
PENALTY = 10.0


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
        for k in ("x_in0", "x_in_ze", "t_zone", "r_A", "r_B", "r_C", "rise"):
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
        for k in ("r_A", "r_B", "r_C", "rise"):
            base[k] = 0.0
        free = [f for f in self.free if f[0] in SLAB_FREE]
        return Family(self.builder, base, free, slab_mode=True, arm_ramp=self.arm_ramp, knots=self.knots)


def slab_spec(*, top, bottom, zone_end, x_in0, x_in_ze, t_zone, arm, hinge=None, tip_round_mm=0.0, roll_mm=0.0,
              **_ignored) -> dict:
    """The fitted slab: the family's outline, inner line and thickness, no facets (every r = 0)."""
    d0 = top[0][0]
    inner = [[d0, x_in0], [zone_end, x_in_ze]] + [[float(k[0]), float(k[1])] for k in arm if k[0] > zone_end]
    thick = [[d0, t_zone], [zone_end, t_zone]] + [[float(k[0]), float(k[2])] for k in arm if k[0] > zone_end]
    return {"schema": "g3d.temple/1", "hinge": copy.deepcopy(hinge or FX.HINGE),
            "outline": {"top": [list(p) for p in top], "bottom": [list(p) for p in bottom],
                        "tip_round_mm": tip_round_mm},
            "inner": inner, "thickness": thick, "zone_end_d": float(zone_end), "nodes": {}, "facets": {},
            "chamfers": [], "roll_mm": roll_mm, "options": {}}


def prada_family(base: dict, free=PRADA_FREE) -> Family:
    """Bands: a number = +- that around the start; a pair = absolute bounds."""
    fr = []
    for name, band in free:
        v = float(base.get(name, 0.0))
        lo, hi = (v - band, v + band) if not isinstance(band, tuple) else band
        fr.append((name, lo, hi))
    return Family(FX.prada_spec, base, fr)


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


def _view_loss(job, model, k, fit, roi):
    r = render_model(job, model, k, fit["camera"], fit["theta"], points=(job.kind(k) == "front"))
    m = roi_metrics(job.matte(k), r["mask"], roi, job.hinge_ppm(fit["camera"]))
    ys, xs = np.nonzero(roi)
    width = float(xs.max() - xs.min() + 1) if len(xs) else 1.0
    px = m["contour_mm"] * job.hinge_ppm(fit["camera"])
    return (1.0 - m["iou"]) + 4.0 * min(px, RR.CONTOUR_TRUNC_PX) / width, m, r


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


def objective(ctx: Context, spec: dict | None, *, details: bool = False):
    if spec is None:
        return (INFEASIBLE, {"refused": "builder", "message": "no valid spec for these parameters"}) if details             else INFEASIBLE
    try:
        model = Model.from_spec(ctx.job, spec, "fit")
    except TempleError as e:
        return (INFEASIBLE, {"refused": e.code, "message": e.message}) if details else INFEASIBLE
    total = 0.0
    parts = {}
    rs = {}
    for k in ctx.views:
        lk, m, r = _view_loss(ctx.job, model, k, ctx.fits[k], ctx.rois[k])
        total += lk
        parts[k] = {"loss": lk, "iou": m["iou"], "contour_mm": m["contour_mm"]}
        rs[k] = r
    tb = model.build
    # crease-line reprojection (05 / 01) and 05 -> 02 transfer
    reproj = []
    if tb is not None and tb.spec.facets:
        for k in ("05", "01"):
            if k not in ctx.guides_lines or k not in ctx.fits:
                continue
            side = 1 if math.sin(math.radians(ctx.fits[k]["camera"].yaw)) > 0 else -1
            for cname, line in ctx.guides_lines[k].items():
                nodes = ctx.crease_nodes.get(cname)
                if nodes is None:
                    continue
                q = model_crease_px(ctx.job, tb, ctx.fits[k], side, *nodes)
                if q is not None:
                    reproj.append(_line_dist_mm(ctx.job, ctx.fits[k]["camera"], q, line))
    rp = float(np.mean(reproj)) if reproj else 0.0
    total += RR.CREASE_REPROJ_MM_WEIGHT * rp
    tr = transfer_all(ctx, model) if ("05" in ctx.guides_lines and "02" in ctx.guides_lines) else None
    trm = tr["mean_mm"] if tr and tr["mean_mm"] is not None else 0.0
    total += RR.TRANSFER_MM_WEIGHT * trm
    pen = 0.0
    fb = None
    if "04" in rs:
        fb = front_bound(ctx.job, rs["04"], ctx.fits["04"]["camera"], zone_end=ctx.zone_end)
        pen += PENALTY * fb["hinge_zone_max_mm"]
    dmin = None
    if tb is not None and tb.spec.facets:
        crs = {tuple(c["regions"]): c for c in tb.tables["creases"]}
        for cname in ctx.confirmed:
            n1, n2 = ctx.crease_nodes[cname]
            labs = [l_ for l_, f in tb.spec.facets.items() if n1 in f["poly"] and n2 in f["poly"]]
            if len(labs) == 2 and tuple(sorted(labs)) in crs:
                dn = crs[tuple(sorted(labs))]["dihedral_deg"][0]
                dmin = dn if dmin is None else min(dmin, dn)
                pen += PENALTY * max(0.0, RR.R2_CONFIRMED_DN_DEG - dn) / RR.R2_CONFIRMED_DN_DEG
    total += pen
    if not details:
        return total
    return total, {"views": parts, "crease_reproj_mm": rp, "transfer": tr, "penalty": pen, "front_bound": fb,
                   "min_confirmed_dn_deg": dmin}


def transfer_all(ctx: Context, model) -> dict:
    from .relief_check import crease_transfer
    out = {}
    vals = []
    for cname, l05 in ctx.guides_lines.get("05", {}).items():
        l02 = ctx.guides_lines.get("02", {}).get(cname)
        if l02 is None:
            continue
        t = crease_transfer(ctx.job, model, ctx.fits["05"]["theta"], ctx.fits["05"]["camera"],
                            ctx.fits["02"]["camera"], ctx.fits["02"]["theta"], l05, l02, near=1)
        out[cname] = t
        if t is not None:
            vals.append(t["mean_abs_mm"])
    return {"per_crease": out, "mean_mm": float(np.mean(vals)) if vals else None}


# --------------------------------------------------------------------------------------------- the fit
def refit_views(job, model, fits_prev, views, rear: bool, log=print) -> dict:
    from .relief_views import rear_family, turntable_seed
    fits = {}
    for k in views:
        if k == "06" and rear:
            seeds = [job.base["06"], turntable_seed(job, job.base["04"])]
            if "06" in fits_prev:
                seeds.insert(0, fits_prev["06"]["camera"])
            fam = rear_family(job, model, seeds)
            best = min(fam["rows"], key=lambda r_: r_["loss"])
            cam = Camera.from_dict(best["camera"])
            th = fit_theta(job, model, "06", cam, {1: best["alpha"], -1: best["alpha"]})
            fits["06"] = {"camera": cam, "theta": {s: th[s]["theta"] for s in (1, -1)}, "family": fam,
                          "alpha": best["alpha"]}
            continue
        start = fits_prev.get(k, {}).get("camera") or job.base[k]
        fits[k] = fit_model_view(job, model, k, start)
        for keep in ("family", "alpha"):                    # the rear family found in round 0 stays on record
            if keep in fits_prev.get(k, {}):
                fits[k][keep] = fits_prev[k][keep]
    return fits


def build_rois(job, models_fits: list, views) -> dict:
    rois = {}
    for k in views:
        renders = [render_model(job, m, k, f[k]["camera"], f[k]["theta"], points=False) for m, f in models_fits]
        rois[k] = temple_roi(job, k, renders)
    return rois


def fit_temple(job: Job, family: Family, *, views=("01", "02", "03", "04", "05", "06"), guides_lines=None,
               rounds: int = RR.FIT_MAX_ROUNDS, maxfev: int = RR.FIT_MAXFEV, rear: bool = True, retrace=None,
               fits0: dict | None = None, rois: dict | None = None, log=print, budget_s: float = 90 * 60,
               refit_cameras: bool = True) -> dict:
    """Block-coordinate joint fit; returns the suggested spec and the diagnostics. `retrace(family, x, fits)` returns
    a family with a re-traced outline (or None to keep it)."""
    t0 = time.time()
    last_beat = [t0]

    def beat(msg):
        if time.time() - last_beat[0] >= 0 or True:
            log(f"[fit {time.time() - t0:6.0f} s] {msg}")
            last_beat[0] = time.time()
    x = family.x0()
    fits = dict(fits0 or {})
    hist = []
    loss_prev = None
    for rnd in range(rounds):
        model = Model.from_spec(job, family.spec(x), f"fit r{rnd}")
        if refit_cameras or not fits:
            # the rear family (one camera per opening alpha) in the first round only; later rounds refit every
            # camera, the rear one inside its bounded box around the family's best
            fits = refit_views(job, model, fits, views, rear and "06" not in fits, log=log)
            beat(f"round {rnd}: cameras + theta refit")
        if retrace is not None:
            fam2 = retrace(family, x, fits)
            if fam2 is not None:
                family = fam2
                model = Model.from_spec(job, family.spec(x), f"fit r{rnd} traced")
                beat(f"round {rnd}: outline re-traced")
        rr = build_rois(job, [(model, fits)], views) if rois is None else rois
        ctx = Context(job, fits, rr, tuple(views), guides_lines or {})
        evals = [0]

        def f(v):
            evals[0] += 1
            val = objective(ctx, safe_spec(family, v))
            if time.time() - last_beat[0] > 60:
                beat(f"round {rnd}: {evals[0]} evaluations, loss {val:.5f}")
            return val
        l0 = f(x)
        x_new, l1 = shape_step(f, x, family.bounds(), maxfev)
        if l1 > l0:
            x_new, l1 = x, l0
        move = np.abs(x_new - x)
        hist.append({"round": rnd, "loss_start": float(l0), "loss": float(l1), "evaluations": evals[0],
                     "x": dict(zip(family.names, map(float, x_new))), "max_move": float(move.max())})
        beat(f"round {rnd}: loss {l0:.5f} -> {l1:.5f}, max move {move.max():.3f} ({evals[0]} evaluations)")
        x = x_new
        if move.max() < RR.FIT_STOP_MOVE and loss_prev is not None and abs(loss_prev - l1) < RR.FIT_STOP_LOSS * loss_prev:
            break
        loss_prev = l1
        if time.time() - t0 > budget_s:
            beat("budget reached")
            break
    model = Model.from_spec(job, family.spec(x), "fitted")
    rr = build_rois(job, [(model, fits)], views) if rois is None else rois
    ctx = Context(job, fits, rr, tuple(views), guides_lines or {})
    loss, det = objective(ctx, family.spec(x), details=True)
    sig = sigma(ctx, family, x)
    return {"x": dict(zip(family.names, map(float, x))), "spec": family.spec(x), "family": family, "fits": fits,
            "loss": float(loss), "details": det, "sigma": sig, "history": hist, "seconds": time.time() - t0,
            "context": ctx}


SIMPLEX_FRACTION = 0.15


def shape_step(f, x0, bounds, maxfev: int):
    """The shape block of one round: Nelder-Mead (adaptive, initial simplex 15 % of each band; bounds by clipping plus
    a steep penalty outside) then a bounded Powell polish. Nelder-Mead is used first because the silhouette terms are
    pixel-quantised and the dn >= 3 deg constraint is a penalty kink: Powell's line searches alone stall on them (the
    S1 round trip). Returns (x, f(x))."""
    lo = np.array([b[0] for b in bounds], float)
    hi = np.array([b[1] for b in bounds], float)
    x0 = np.clip(np.asarray(x0, float), lo, hi)
    out = lambda v: float(np.sum(np.maximum(lo - v, 0) + np.maximum(v - hi, 0)))
    g = lambda v: f(np.clip(v, lo, hi)) + 10.0 * out(v)
    steps = SIMPLEX_FRACTION * (hi - lo)
    sim = [x0] + [x0 + np.eye(len(x0))[i] * steps[i] * (-1.0 if x0[i] + steps[i] > hi[i] else 1.0)
                  for i in range(len(x0))]
    r = optimize.minimize(g, x0, method="Nelder-Mead",
                          options={"initial_simplex": np.array(sim), "maxfev": maxfev, "xatol": 0.005,
                                   "fatol": 1e-5, "adaptive": True})
    x1 = np.clip(r.x, lo, hi)
    r2 = optimize.minimize(f, x1, method="Powell", bounds=list(zip(lo, hi)),
                           options={"maxfev": max(40, maxfev // 3), "xtol": 0.005, "ftol": 1e-5})
    cands = [(f(x1), x1), (float(r2.fun), np.clip(r2.x, lo, hi))]
    fx, x = min(cands, key=lambda t: t[0])
    return x, float(fx)


def sigma(ctx: Context, family: Family, x, rel_step: float = 0.05) -> dict:
    """sigma_i = sqrt(2 NOISE_LOSS / f''_i) from a central second difference (inf when the objective is flat)."""
    out = {}
    f0 = objective(ctx, safe_spec(family, x))
    for i, (name, lo, hi) in enumerate(family.free):
        h = max(1e-3, rel_step * (hi - lo))
        xp, xm = x.copy(), x.copy()
        xp[i] = min(hi, x[i] + h)
        xm[i] = max(lo, x[i] - h)
        fp, fm = objective(ctx, safe_spec(family, xp)), objective(ctx, safe_spec(family, xm))
        hp, hm = xp[i] - x[i], x[i] - xm[i]
        if hp <= 0 or hm <= 0:
            out[name] = float("nan")
            continue
        curv = 2.0 * (fp * hm + fm * hp - f0 * (hp + hm)) / (hp * hm * (hp + hm))
        out[name] = float(math.sqrt(2 * NOISE_LOSS / curv)) if curv > 1e-12 else float("inf")
    return out


def profiles(ctx: Context, family: Family, x, names, n: int = 9, span: dict | None = None) -> dict:
    """Per parameter: the total objective and every view's loss along a sweep (others held)."""
    out = {}
    for name in names:
        i = family.names.index(name)
        lo, hi = family.free[i][1:]
        s = (span or {}).get(name, 0.25 * (hi - lo))
        vals = np.linspace(max(lo, x[i] - s), min(hi, x[i] + s), n)
        rows = []
        for v in vals:
            xv = x.copy()
            xv[i] = v
            tot, det = objective(ctx, safe_spec(family, xv), details=True)
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
    vals, obs = np.asarray(vals, float), np.asarray(obs, float)
    ok = np.isfinite(obs)
    if ok.sum() < 3:
        return None
    vals, obs = vals[ok], obs[ok]
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
                for cname, line in ctx.guides_lines["02"].items():
                    nodes = ctx.crease_nodes.get(cname)
                    q = model_crease_px(job, model.build, ctx.fits["02"], 1, *nodes) if nodes else None
                    if q is not None:
                        cr.append(_line_dist_mm(job, ctx.fits["02"]["camera"], q, line))
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
class SyntheticJob:
    """A job whose photos and mattes are renders of a known model (S1, and the synthetic tests): exact mattes and
    Lambert-shaded photos (creases show as L* steps) through the TRUE cameras; `base` holds those cameras perturbed by
    each view's contour error in px (what a fit starts from). Build it from a real Job (`from_job`, its frame, front
    parts and photo sizes) or `standalone` (any front mesh, frame and image sizes: no data needed)."""

    def __init__(self, *, frame, V0, F0, hinge, cams, shapes, views, view_names, truth, theta, perturb_px=None,
                 pivot_mm=None, seed: int = 7, light=(-0.35, 0.55, 0.76), glb=None, cfg=None):
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
        l2 = np.array([0.6, -0.3, 0.74]) / np.linalg.norm([0.6, -0.3, 0.74])     # a second, opposite key light
        lw2 = l2[0] * Rr + l2[1] * Ru + l2[2] * Rt
        shade = 0.6 * np.clip(n @ lw, 0, 1) + 0.4 * np.clip(n @ lw2, 0, 1)
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
