"""WP5 step 0 end to end (build order (1)-(2)): edges, regions, plan, the three registrations (fold A, fold B, all),
the gates, the registered sets, check_print (stripes protocol + B0), statuses and STOP A.

Usage (FT1119 proof; writes registration.json, check_print.json, report.json and sheets/ under ``--out``):

    glasses3d/.venv/Scripts/python -m glasses3d.g3d_host.photoprint.step0 --cameras <cameras.json> \
        --parts <part names> --out <new-directory> \
        [--a0 glasses3d/data/wp5/a0/a0.json]

STOP A needs A0's verdict: without ``--a0`` it fires with "A0 verdict
not supplied". ``--refold-a0`` folds a (new) A0 JSON into an existing run's report.json without re-registering.

Registrations run in a process pool (each worker opted out of EcoQoS) and print progress at least every 30 s.
Besides the three fits from the WP3b start, ``stability_starts`` fits per fold start from seeded perturbations
(``register(start_seed=k)``); their converged parallax, loss and (fold fits) gate verdicts are reported as the
stability of the result (``report.json`` ``parallax_stability`` / ``gate_stability``) and never gate.
Every held-out gate and check_print row excludes the photo pixels its fit read (``RegResult.reads``).
``registration.json`` and ``check_print.json`` hold no timings, so two runs must be byte-identical (A7).
"""
from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from pathlib import Path

import numpy as np
import scipy

from . import edges as ED
from . import gate as GT
from . import inputs as IN
from . import regions as RG
from . import register as REG
from . import report as RP
from .check_print import check_b0, check_region
from .pose import Deformer
from .procs import Progress, opt_out_ecoqos, pool_map

PRINT_REG_MM = 0.8           # D1 (target 0.5)
STABILITY_STARTS = 3         # perturbed starts per fold (stability report)


def _fit(job, regions, groups, plan, fold, maxfev, tag, quiet=False, start_seed=None, max_runs=REG.MAX_RUNS):
    return REG.register(job, regions, groups, plan, fold=fold, maxfev=maxfev, tag=tag, log=None if quiet else print,
                        start_seed=start_seed, max_runs=max_runs)


def _gate(job, regions, groups, axes, pose, rid, w, v, fold, reg_mm, min_decoys=GT.MIN_DECOYS, reads=None,
          alt_layout=None):
    """One gate row (the strip null, ``gate.NULL``); ``alt_layout`` also grades the same row with the LEGACY
    displacement-of-the-photo null at that decoy layout (reported, never deciding)."""
    by = {r.rid: r for r in regions}
    d = Deformer(job, regions, groups, axes)
    g = GT.gate(job, d, pose, by[rid], w, v, fold, reg_mm=reg_mm, min_decoys=min_decoys, reads=reads)
    if alt_layout:
        a = GT.gate(job, d, pose, by[rid], w, v, fold, reg_mm=reg_mm, min_decoys=min_decoys, reads=reads,
                    decoy_layout=alt_layout, null="displace")
        wa = (a.get("signals") or {}).get("warmth", {})
        g["alt_decoy_layout"] = {
            "null": "displace", "layout": alt_layout, "decoys": a.get("decoys"), "decoys_usable": wa.get("decoys_usable"),
            "decoy_duplicates": wa.get("decoy_duplicates"), "decoy_max": wa.get("decoy_max"),
            "decoy_max_any_window": wa.get("decoy_max_any_window"),
            "decoy_rows": [{k: r[k] for k in ("offset_px", "overlap_at_offset", "usable_shifts", "peak", "argmax_px",
                                              "duplicate_of", "counted")} for r in (a.get("decoy_rows") or [])],
            "significant": a.get("significant"), "reason": a.get("reason"), "registered": a.get("registered")}
    return g


def _gate_jobs(job, regions, groups, plan, regs: dict, print_reg_mm, min_decoys, protocols):
    """(jobs, keys) of the gates anchor <-> partner, both directions, for each (evaluated fold, fit key) protocol;
    a fold fit's gate excludes the pixels that fit read."""
    gjobs, gkeys = [], []
    for rid in plan.parallax:
        a = plan.anchors[rid]
        for p in plan.partners[rid]:
            for w, v in ((a, p), (p, a)):
                for ev, fit in protocols:
                    reg = regs[fit]
                    gjobs.append((job, regions, groups, plan.axes, reg.pose, rid, w, v, ev, print_reg_mm, min_decoys,
                                  reg.reads if ev is not None else None,
                                  GT.DECOY_LAYOUT if ev is not None else None))
                    gkeys.append((rid, p, w, v, ev, fit))
    return gjobs, gkeys


def run_step0(job, parts, out_dir, *, print_reg_mm: float = PRINT_REG_MM, n_samples: int = 30000,
              maxfev: int = REG.MAXFEV, workers: int | None = None, seed: int = 0, b0: bool = True,
              sheets: bool = True, a0_passed: bool | None = None, a0_summary: str | None = None,
              min_decoys: int = GT.MIN_DECOYS, stability_starts: int = STABILITY_STARTS,
              max_runs: int = REG.MAX_RUNS, a0_note: str | None = None, log=print) -> dict:
    t0 = time.perf_counter()
    opt_out_ecoqos()
    out = Path(out_dir)
    prog = Progress("step0", log)
    timings = {}
    groups = RG.rigid_groups(job)
    regions = RG.build_regions(job, parts, groups, n_samples=n_samples, seed=seed)
    by = {r.rid: r for r in regions}
    pids = job.part_ids(parts)
    edges0 = ED.edge_table(job, pids)
    adm = ED.admitted(edges0)
    plan = REG.plan_registration(job, regions, groups, adm)
    prog(f"plan: fixed {plan.fixed}, free {plan.free_cameras}, hinges {plan.free_hinges}, "
         f"{len(plan.parallax)} parallax regions, {len(plan.pairs())} pairs", force=True)

    # registrations: fold A, fold B, all (the bake's refit) from WP3b, plus the perturbed starts (stability)
    t = time.perf_counter()
    folds = ("A", "B", "all")
    quiet = log is None
    fjobs = [(job, regions, groups, plan, f, maxfev, f"fit{f}", quiet, None, max_runs) for f in folds]
    skeys = [(f, k) for f in folds for k in range(1, int(stability_starts) + 1)] if plan.parallax else []
    fjobs += [(job, regions, groups, plan, f, maxfev, f"fit{f}-s{k}", quiet, k, max_runs) for f, k in skeys]
    res = pool_map(_fit, fjobs, workers, progress=prog)
    regs = dict(zip(folds, res[:len(folds)]))
    sregs = {f"{f}-s{k}": r for (f, k), r in zip(skeys, res[len(folds):])}
    timings["registration_s"] = round(time.perf_counter() - t, 1)
    prog("registrations done", force=True)

    # gates: anchor <-> partner, both directions, both folds (eval B with the fold-A fit, A with the fold-B fit),
    # plus the 'all' fit on every pixel (reported only); the perturbed fold fits' gates (stability, reported only)
    t = time.perf_counter()
    gjobs, gkeys = _gate_jobs(job, regions, groups, plan, regs, print_reg_mm, min_decoys,
                              (("B", "A"), ("A", "B"), (None, "all")))
    sjobs, skeys_g = [], []
    for key, r in sregs.items():
        f = r.info["fold"]
        if f in ("A", "B"):
            j, k = _gate_jobs(job, regions, groups, plan, {key: r}, print_reg_mm, min_decoys,
                              (("B" if f == "A" else "A", key),))
            sjobs += j
            skeys_g += k
    gres = pool_map(_gate, gjobs + sjobs, workers, progress=prog)
    gate_rows, stab_gate_rows = [], []
    for (rid, p, w, v, ev, fit), g in zip(gkeys + skeys_g, gres):
        g = dict(g)
        g.update({"partner": p, "fit_fold": fit})
        (gate_rows if fit in regs else stab_gate_rows).append(g)
    timings["gates_s"] = round(time.perf_counter() - t, 1)
    registered = {}
    for rid in plan.regions:
        a = plan.anchors.get(rid)
        if a is None:
            continue
        reg = [a]
        for p in plan.partners.get(rid, []):
            rows = [g for g in gate_rows if g["rid"] == rid and g["partner"] == p and g["fit_fold"] in ("A", "B")]
            if len(rows) == 4 and all(g.get("registered") for g in rows):
                reg.append(p)
        registered[rid] = reg
    prog("gates done: " + "; ".join(f"{by[rid].name}: {registered[rid]}" for rid in plan.parallax), force=True)

    # check_print, stripes protocol
    t = time.perf_counter()
    check_rows = []
    wp3b_pose = REG.start_pose(job)
    for rid in plan.parallax:
        if len(registered[rid]) >= 2:
            check_rows += check_region(job, regions, groups, plan.axes, by[rid], registered[rid], regs,
                                       anchor=plan.anchors[rid], seed=seed,
                                       sheets_dir=(out / "sheets") if sheets else None, wp3b_pose=wp3b_pose)
    # diagnostic rows (never gating, never in a status): parallax regions the gate did not register, anchor + fit
    # partners, so the owner sees what the pixels say where registration fell short
    diag_rows = []
    for rid in plan.parallax:
        if len(registered[rid]) < 2:
            views = [plan.anchors[rid]] + plan.fit_partners(rid)
            rows = check_region(job, regions, groups, plan.axes, by[rid], views, regs, anchor=plan.anchors[rid],
                                seed=seed, sheets_dir=(out / "sheets_diagnostic") if sheets else None,
                                wp3b_pose=wp3b_pose)
            for r_ in rows:
                r_["diagnostic"] = True
            diag_rows += rows
    timings["check_print_s"] = round(time.perf_counter() - t, 1)

    # B0: strict leave-one-view-out (never gates)
    t = time.perf_counter()
    b0_rows, b0_regs = [], {}
    held = sorted({h for rid in plan.parallax if len(registered[rid]) >= 2 for h in registered[rid]})
    if b0 and held:
        plans = {h: REG.plan_registration(job, regions, groups, adm, exclude=[h]) for h in held}
        res = pool_map(_fit, [(job, regions, groups, plans[h], "all", maxfev, f"b0-{IN.short_name(h)}", quiet)
                              for h in held], workers, progress=prog)
        b0_regs = dict(zip(held, res))
        for rid in plan.parallax:
            if len(registered[rid]) < 2:
                continue
            for h in registered[rid]:
                src = [w for w in registered[rid] if w != h]
                axes = dict(plans[h].axes)
                b0_rows += check_b0(job, regions, groups, by[rid], h, src, b0_regs[h], axes,
                                    anchor=plan.anchors[rid], seed=seed)
    timings["b0_s"] = round(time.perf_counter() - t, 1)

    # edges after registration (the 'all' fit) and statuses
    deformer = Deformer(job, regions, groups, plan.axes)
    edges1 = ED.edge_table(job, pids, pose=regs["all"].pose, deformer=deformer)
    statuses = RP.region_statuses(regions, plan, registered, check_rows, gate_rows)

    registration = {
        "inputs": {k: v for k, v in job.sources.items()},
        "print_reg_mm": print_reg_mm, "min_decoys": min_decoys, "n_samples": n_samples, "maxfev": maxfev,
        "seed": seed,
        "groups": [g.to_json(job.mesh.names) for g in groups],
        "regions": [r.to_json() for r in regions],
        "plan": plan.to_json(regions),
        "edges_wp3b": edges0, "edges_registered": edges1,
        "fits": {f: RP.strip_timing(regs[f].info) for f in folds},
        "gates": gate_rows, "registered": {by[rid].name: v for rid, v in registered.items()},
        "b0_fits": {IN.short_name(h): RP.strip_timing(r.info) for h, r in b0_regs.items()},
        "stability": {"starts_per_fold": int(stability_starts),
                      "fits": {k: RP.strip_timing(r.info) for k, r in sregs.items()},
                      "gates": stab_gate_rows},
    }
    check = {"protocol": "stripes: held-out view x evaluated fold, registration fitted on the other fold; "
                         "direct per-pixel tier-1 bake (stage-2 stand-in for steps 2-7)",
             "instrument": "glasses3d/tests/instruments/print_metrics.py", "rows": check_rows, "b0_rows": b0_rows,
             "diagnostic_rows": diag_rows,
             "diagnostic_note": "regions the gate did not register (anchor + fit partners); reported, never gating"}
    timings["total_s"] = round(time.perf_counter() - t0, 1)
    timings["fits_s"] = {f: regs[f].info.get("seconds") for f in folds}
    timings["stability_fits_s"] = {k: r.info.get("seconds") for k, r in sregs.items()}
    timings["b0_fits_s"] = {IN.short_name(h): r.info.get("seconds") for h, r in b0_regs.items()}
    report = {"status": RP.overall(statuses),
              "regions": {s["name"]: s for s in statuses.values()},
              "status_area_mm2": RP.status_areas(statuses, regions),
              "pixel_verifiable": RP.pixel_verifiable(statuses, regions, plan),
              "gate_table": gate_table(gate_rows, by),
              "parallax": RP.parallax_summary(regs, plan, regions),
              "parallax_stability": RP.parallax_stability(regs, sregs, plan, regions),
              "gate_stability": RP.gate_stability(gate_rows, stab_gate_rows, by),
              "cameras": RP.camera_summary(regs, plan),
              "warnings": RP.warnings(statuses, regs, plan, regions, edges1, sregs),
              "timings": timings, "scipy": scipy.__version__, "numpy": np.__version__,
              "python": platform.python_version(), "inputs": job.sources}
    files = {"registration": RP.write_json(out / "registration.json", registration),
             "check_print": RP.write_json(out / "check_print.json", check),
             "report": RP.write_json(out / "report.json", report)}
    report = RP.finalize(out, a0_passed, a0_summary, a0_note)
    stop = report["stop_a"]
    prog(f"done: {report['status']}, STOP A {'FIRED' if stop['fired'] else 'not fired'}", force=True)
    return {"files": files, "report": report, "registration": registration, "check": check, "regs": regs,
            "stability_regs": sregs, "plan": plan, "regions": regions, "groups": groups}


def gate_table(gate_rows: list[dict], by: dict) -> list[dict]:
    """The fold-protocol gate rows (evaluated fold, fitted on the other), one line each: what the registered sets
    were decided on."""
    out = []
    for g in gate_rows:
        if g.get("fit_fold") not in ("A", "B"):
            continue
        w = (g.get("signals") or {}).get("warmth", {})
        out.append({"region": by[g["rid"]].name, "pair": f"{IN.short_name(g['source'])}->{IN.short_name(g['target'])}",
                    "eval_fold": g["fold"], "fit_fold": g["fit_fold"], "E_px": g.get("E_px"),
                    "E_px_read_by_fit": g.get("E_px_read_by_fit"),
                    "decoys": g.get("decoys"), "decoys_usable": w.get("decoys_usable"),
                    "decoy_overlaps": [round(d["overlap_at_offset"], 3) for d in (g.get("decoy_rows") or [])],
                    "decoy_duplicates": w.get("decoy_duplicates"),
                    "peak": w.get("peak"), "decoy_max": w.get("decoy_max"),
                    "decoy_max_any_window": w.get("decoy_max_any_window"),
                    "significant": g.get("significant"), "reason": g.get("reason"),
                    "residual_mm": g.get("residual_mm"), "registered": g.get("registered")})
    return out


def load_a0(path) -> tuple[bool | None, str | None]:
    """(pass, verdict line) from an A0 result JSON (``tests/photoprint/a0.py`` via the proof run), or (None, None)."""
    if path is None:
        return None, None
    p = Path(path)
    if not p.is_file():
        return None, None
    d = json.loads(p.read_text(encoding="utf-8"))
    return d.get("pass"), d.get("verdict_line")


def load_a0_note(path) -> str | None:
    """What an A0 result's verdict rests on (fix round 2): the literal §8 rule, or the known-limit exemption and its
    status (``reported.would_pass_under_known_limit_exemption`` / ``exemption_status``); None for older files."""
    if path is None:
        return None
    p = Path(path)
    if not p.is_file():
        return None
    d = json.loads(p.read_text(encoding="utf-8"))
    rep = d.get("reported") or {}
    if "would_pass_under_known_limit_exemption" not in rep:
        return None
    if d.get("pass"):
        return "passed under the literal §8 rule"
    if rep["would_pass_under_known_limit_exemption"]:
        return ("FAILED under the literal §8 rule (residual_every_view); it would pass only under the known-limit "
                f"exemption, which is {rep.get('exemption_status')} (no owner waiver, no PROGRESS decision)")
    return "FAILED: " + ", ".join(k for k, v in (d.get("criteria") or {}).items() if not v)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cameras", required=True)
    ap.add_argument("--parts", nargs="+", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--samples", type=int, default=30000)
    ap.add_argument("--maxfev", type=int, default=REG.MAXFEV)
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--print-reg-mm", type=float, default=PRINT_REG_MM)
    ap.add_argument("--no-b0", action="store_true")
    ap.add_argument("--no-sheets", action="store_true")
    ap.add_argument("--stability-starts", type=int, default=STABILITY_STARTS)
    ap.add_argument("--a0", help="A0 result JSON (STOP A fires without an A0 verdict)")
    ap.add_argument("--refold-a0", action="store_true",
                    help="only fold the A0 JSON into an existing --out run (report.finalize; no registration)")
    a = ap.parse_args(argv)
    a0_pass, a0_line = load_a0(a.a0)
    if a.refold_a0:
        rep = RP.finalize(Path(a.out), a0_pass, a0_line, load_a0_note(a.a0))
        print(json.dumps({"A0_passed": rep["A0_passed"], "stop_a": rep["stop_a"]}, indent=1))
        return 0
    job = IN.load_job(a.cameras)
    r = run_step0(job, a.parts, a.out, print_reg_mm=a.print_reg_mm, n_samples=a.samples, maxfev=a.maxfev,
                  workers=a.workers, b0=not a.no_b0, sheets=not a.no_sheets, a0_passed=a0_pass, a0_summary=a0_line,
                  stability_starts=a.stability_starts, a0_note=load_a0_note(a.a0))
    print(r["files"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
