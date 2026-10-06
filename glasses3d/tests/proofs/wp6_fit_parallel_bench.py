"""WP6 joint-fit speed: one objective evaluation serial vs pooled (relief_fit.FitPool), on the Prada proof context, and
the camera block (refit_views) serial vs pooled. Every pooled value is compared with the serial one for EXACT equality
(==, not a tolerance), and the serial values with the pre-pool formula (full-image roi_metrics, np.nonzero width) and
the recorded candidate loss. A TOOL TEST measurement on the proof's recorded data; it writes no scene and no proof
stage.

    glasses3d/.venv/Scripts/python -u -B -m glasses3d.tests.proofs.wp6_fit_parallel_bench [--evals 24]
        [--workers N] [--cameras none|views|all] [--out FILE]

Context: data/wp6/proof/fit_candidate.pkl (final cameras, theta, x), proof.json (start parameters, P2 traced corners
and guide lines). Evaluation points: the recorded x plus seeded random moves of up to 10 % of each band.
"""
from __future__ import annotations

import argparse
import json
import os
import pickle
import sys
import time
from pathlib import Path

import numpy as np
from scipy import ndimage

G3D = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(G3D.parent))

from glasses3d.g3d_host import relief_fit as RF  # noqa: E402
from glasses3d.g3d_host import relief_rules as RR  # noqa: E402
from glasses3d.g3d_host import relief_views as RV  # noqa: E402

PROOF = G3D / "data" / "wp6" / "proof"
LABEL = "TOOL TEST (WP6 parallel-fit benchmark on the Prada proof's recorded data)"


def prada_context(job):
    """The candidate fit's final context, rebuilt from the recorded proof (the family's outline = the spec's)."""
    rec = json.loads((PROOF / "proof.json").read_text(encoding="utf-8"))
    fc = pickle.loads((PROOF / "fit_candidate.pkl").read_bytes())
    params = dict(rec["start_params"])
    for k in ("B", "C"):
        if rec["stages"]["P2"]["traced"].get(k):
            params[k] = tuple(rec["stages"]["P2"]["traced"][k])
    params["top"], params["bottom"] = fc["spec"]["outline"]["top"], fc["spec"]["outline"]["bottom"]
    fam = RF.prada_family(params)
    x = np.array([fc["x"][n] for n in fam.names])
    if json.dumps(fam.spec(x), sort_keys=True) != json.dumps(fc["spec"], sort_keys=True):
        raise SystemExit("the rebuilt family does not reproduce the recorded candidate spec")
    glines = {k: {cn: tuple(v) for cn, v in d.items()} for k, d in rec["stages"]["P2"]["guide_lines"].items()}
    fits = {k: {"camera": f["camera"], "theta": f["theta"]} for k, f in fc["fits"].items()}
    model = RV.Model.from_spec(job, fam.spec(x), "candidate (TOOL TEST)")
    rois = RF.build_rois(job, [(model, fits)], RV.VIEW_ORDER)
    return RF.Context(job, fits, rois, RV.VIEW_ORDER, glines), fam, x, fc


def _old_view_loss(job, model, k, fit, roi):
    """relief_fit._view_loss before the parallel-fit work (render_model, full-image roi_metrics, np.nonzero width;
    the front bound's distance transform is recomputed per call by the caller's patch)."""
    r = RV.render_model(job, model, k, fit["camera"], fit["theta"], points=(job.kind(k) == "front"))
    m = RV.roi_metrics_full(job.matte(k), r["mask"], roi, job.hinge_ppm(fit["camera"]))
    ys, xs = np.nonzero(roi)
    width = float(xs.max() - xs.min() + 1) if len(xs) else 1.0
    px = m["contour_mm"] * job.hinge_ppm(fit["camera"])
    return (1.0 - m["iou"]) + 4.0 * min(px, RR.CONTOUR_TRUNC_PX) / width, m, r


def points(fam, x, n, seed=0):
    rng = np.random.default_rng(seed)
    lo, hi = np.array(fam.bounds()).T
    pts = [x.copy()]
    while len(pts) < n:
        pts.append(np.clip(x + rng.uniform(-0.1, 0.1, len(x)) * (hi - lo), lo, hi))
    return pts


def cpu_load() -> float | None:
    try:
        import subprocess
        out = subprocess.run(["powershell.exe", "-NoProfile", "-Command",
                              "(Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage "
                              "-Average).Average"], capture_output=True, text=True, timeout=60).stdout.strip()
        return float(out)
    except Exception:                                            # noqa: BLE001
        return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--evals", type=int, default=24)
    ap.add_argument("--workers", type=int, default=RF.pool_workers())
    ap.add_argument("--cameras", default="none", choices=("none", "views", "all"))
    ap.add_argument("--out", default=str(G3D / "data" / "wp6" / "fit_parallel_bench.json"))
    a = ap.parse_args(argv)
    t00 = time.time()
    log = lambda m: print(f"[{time.time() - t00:7.1f} s] {m}", flush=True)
    log(f"{LABEL}; os.cpu_count() {os.cpu_count()}, workers {a.workers}, EcoQoS opt-out {RF.opt_out_ecoqos()}")
    rep = {"label": LABEL, "cpu_count": os.cpu_count(), "workers": a.workers, "cpu_load_start_pct": cpu_load()}
    job = RV.Job("prada")
    ctx, fam, x, fc = prada_context(job)
    pts = points(fam, x, a.evals)
    specs = [RF.safe_spec(fam, p) for p in pts]
    RF.objective(ctx, specs[0])                       # warm the front-render cache and the scene cache
    # --- serial, the current code
    ser, t_ser = [], []
    for s in specs:
        t0 = time.perf_counter()
        ser.append(RF.objective(ctx, s))
        t_ser.append(time.perf_counter() - t0)
    log(f"serial: {np.mean(t_ser):.3f} s per evaluation (median {np.median(t_ser):.3f})")
    # --- serial, the pre-pool formula (proves the cropped roi_metrics is the full-image one, bit for bit)
    new_vl, new_op = RF._view_loss, RV.outside_px
    RF._view_loss = _old_view_loss
    RV.outside_px = lambda job_, key: ndimage.distance_transform_edt(~job_.matte(key))
    try:
        old, t_old = [], []
        for s in specs:
            t0 = time.perf_counter()
            old.append(RF.objective(ctx, s))
            t_old.append(time.perf_counter() - t0)
    finally:
        RF._view_loss, RV.outside_px = new_vl, new_op
    log(f"serial, pre-pool formula: {np.mean(t_old):.3f} s per evaluation")
    rep["recorded_candidate_loss"] = fc["loss"]
    rep["serial_at_recorded_x"] = ser[0]
    # --- pooled
    with RF.FitPool(a.workers, log=log) as pool:
        t0 = time.perf_counter()
        info = pool.warm(job)
        rep["pool_start_s"] = time.perf_counter() - t0
        rep["pool_workers_seen"] = len(info)
        rep["pool_workers_ecoqos_opt_out"] = sum(bool(i["ecoqos_opt_out"]) for i in info)
        log(f"pool: {len(info)} workers up in {rep['pool_start_s']:.1f} s, "
            f"{rep['pool_workers_ecoqos_opt_out']} opted out of EcoQoS")
        RF.objective(ctx, specs[0], pool=pool)        # broadcast the context, warm every worker's caches
        RF.objective_many(ctx, specs[:2] * a.workers, pool=pool)
        par, t_par = [], []
        for s in specs:
            t0 = time.perf_counter()
            par.append(RF.objective(ctx, s, pool=pool))
            t_par.append(time.perf_counter() - t0)
        log(f"pooled, one evaluation at a time: {np.mean(t_par):.3f} s per evaluation")
        t0 = time.perf_counter()
        bat = RF.objective_many(ctx, specs, pool=pool)
        t_bat = (time.perf_counter() - t0) / len(specs)
        log(f"pooled, one batch of {len(specs)}: {t_bat:.3f} s per evaluation")
        sig_t = {}
        t0 = time.perf_counter()
        sig_p = RF.sigma(ctx, fam, x, pool=pool)
        sig_t["pooled"] = time.perf_counter() - t0
        cams = None
        if a.cameras != "none":
            model = RV.Model.from_spec(job, fam.spec(x), "candidate (TOOL TEST)")
            views = RV.VIEW_ORDER if a.cameras == "all" else ("01", "02", "03", "04", "05")
            prev = {k: dict(f) for k, f in fc["fits"].items()}
            rear = a.cameras == "all"
            if rear:
                prev.pop("06")
            t0 = time.perf_counter()
            fp = RF.refit_views(job, model, prev, views, rear, pool=pool)
            tp = time.perf_counter() - t0
            log(f"camera block pooled ({'with' if rear else 'without'} the rear family): {tp:.0f} s")
    t0 = time.perf_counter()
    sig_s = RF.sigma(ctx, fam, x)
    sig_t["serial"] = time.perf_counter() - t0
    if a.cameras != "none":
        t0 = time.perf_counter()
        fs = RF.refit_views(job, model, prev, views, rear)
        ts = time.perf_counter() - t0
        log(f"camera block serial: {ts:.0f} s")
        same = {}
        for k in views:
            same[k] = (fs[k]["camera"] == fp[k]["camera"] and fs[k]["theta"] == fp[k]["theta"])
        if rear:
            same["06_family"] = ([r["camera"] for r in fs["06"]["family"]["rows"]] ==
                                 [r["camera"] for r in fp["06"]["family"]["rows"]])
        cams = {"views": list(views), "rear_family": rear, "serial_s": ts, "pooled_s": tp, "speedup": ts / tp,
                "identical": same, "all_identical": all(same.values())}
        log(f"camera block: serial {ts:.0f} s, pooled {tp:.0f} s ({ts / tp:.1f}x), identical {same}")
    rep.update({
        "evaluations": len(specs), "infeasible": int(sum(v == RF.INFEASIBLE for v in ser)),
        "serial_s_per_eval": float(np.mean(t_ser)), "serial_old_formula_s_per_eval": float(np.mean(t_old)),
        "pooled_s_per_eval": float(np.mean(t_par)), "pooled_batch_s_per_eval": float(t_bat),
        "speedup_one_at_a_time": float(np.mean(t_ser) / np.mean(t_par)),
        "speedup_batch": float(np.mean(t_ser) / t_bat),
        "speedup_vs_pre_pool_serial": float(np.mean(t_old) / np.mean(t_par)),
        "identical_pooled_vs_serial": bool(par == ser and bat == ser),
        "identical_serial_vs_pre_pool_formula": bool(old == ser),
        "identical_recorded_loss": bool(ser[0] == fc["loss"]),
        "sigma": {"serial_s": sig_t["serial"], "pooled_s": sig_t["pooled"], "identical": sig_s == sig_p},
        "cameras": cams, "cpu_load_end_pct": cpu_load(), "seconds": time.time() - t00})
    bad = [i for i, (p_, s_) in enumerate(zip(par, ser)) if p_ != s_]
    rep["mismatches"] = [{"i": i, "serial": ser[i], "pooled": par[i]} for i in bad]
    Path(a.out).write_text(json.dumps(rep, indent=1), encoding="utf-8")
    log(json.dumps({k: v for k, v in rep.items() if k not in ("mismatches",)}, indent=1))
    return 0 if rep["identical_pooled_vs_serial"] and rep["identical_serial_vs_pre_pool_formula"] else 1


if __name__ == "__main__":
    sys.exit(main())
