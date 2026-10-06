"""WP5 STOP A: does the chroma (warmth) objective register Brow_Left's front? (WP5_DESIGN §0 point (b), §11 risk 4)

    glasses3d/.venv/Scripts/python -B -m glasses3d.tests.proofs.wp5_brow_left_warmth [--workers N] [--out DIR]

The design's objective is warmth + luminance (§5.3), which the step-0 proof already runs. The probe saw Brow_Left's
offset in warmth only (A AP2: 1.48-1.78 mm, NCC 0.50-0.53 against a null of 0.22-0.27, luminance at its null), so
this driver fits the objective on warmth ALONE and gates Brow_Left exactly as step 0 does (§5.4: both directions
against the anchor 05, both folds, the fold fit's read pixels excluded). Variants, each fitted on fold A and on fold B
from the WP3b start (no perturbed starts):

- ``joint_warmth``: the design's plan (every parallax region, camera 01 free with a hinge per temple), warmth only;
- ``brow_left_warmth``: only Brow_Left's front carries a parallax and enters the objective (camera 01 and its hinges
  are fitted to that region alone), warmth only: Brow_Left's best case;
- ``brow_left_warmth_lum``: the same single-region plan with the design's warmth + luminance objective, so a change
  between the two single-region variants is the signal's, not the plan's.

Every gate row is reported. A region the gate registers (>= 2 views, both directions, both folds) gets check_print's
C1-C4 rows (``rows``); otherwise Brow_Left gets diagnostic rows (anchor + fit partners; never gating).
Writes DIR/brow_left_warmth/variants.json (+ sheets_diagnostic/, sheets/). Nothing here gates STOP A by itself: these
are measurements beside the design's run, reported in data/wp5/stopA.json.
"""
from __future__ import annotations

import argparse
import dataclasses
import sys
import time
from pathlib import Path

from glasses3d.g3d_host.photoprint import edges as ED
from glasses3d.g3d_host.photoprint import inputs as IN
from glasses3d.g3d_host.photoprint import procs
from glasses3d.g3d_host.photoprint import regions as RG
from glasses3d.g3d_host.photoprint import register as REG
from glasses3d.g3d_host.photoprint import report as RP
from glasses3d.g3d_host.photoprint import step0
from glasses3d.g3d_host.photoprint.check_print import check_region

OUT = IN.REPO / "glasses3d" / "data" / "wp5" / "stopA"
TARGET = "Brow_Left[-x]/front"
VARIANTS = {
    "joint_warmth": {"single_region": False, "signals": ("warmth",)},
    "brow_left_warmth": {"single_region": True, "signals": ("warmth",)},
    "brow_left_warmth_lum": {"single_region": True, "signals": ("warmth", "lum")},
}


def _fit(job, regions, groups, plan, fold, signals, maxfev, tag):
    return REG.register(job, regions, groups, plan, fold=fold, maxfev=maxfev, tag=tag, log=print,
                        signals=tuple(signals))


def variant_plan(plan, rid: int, single_region: bool):
    if not single_region:
        return plan
    return dataclasses.replace(plan, parallax=[rid], axes={rid: plan.axes[rid]})


def run(out_root: Path, workers: int | None, maxfev: int = REG.MAXFEV, n_samples: int = 30000, seed: int = 0,
        variants=tuple(VARIANTS)) -> dict:
    t0 = time.perf_counter()
    procs.opt_out_ecoqos()
    out = Path(out_root) / "brow_left_warmth"
    job = IN.load_job()
    groups = RG.rigid_groups(job)
    regions = RG.build_regions(job, IN.FT1119_PARTS, groups, n_samples=n_samples, seed=seed)
    by = {r.rid: r for r in regions}
    rid = next(r.rid for r in regions if r.name == TARGET)
    adm = ED.admitted(ED.edge_table(job, job.part_ids(IN.FT1119_PARTS)))
    plan0 = REG.plan_registration(job, regions, groups, adm)
    plans = {k: variant_plan(plan0, rid, VARIANTS[k]["single_region"]) for k in variants}
    print(f"[variants] plan: anchor {plan0.anchors[rid]}, partners {plan0.partners[rid]}, free {plan0.free_cameras}, "
          f"hinges {plan0.free_hinges}", flush=True)

    fjobs, fkeys = [], []
    for k in variants:
        for f in ("A", "B"):
            fjobs.append((job, regions, groups, plans[k], f, VARIANTS[k]["signals"], maxfev, f"{k}-{f}"))
            fkeys.append((k, f))
    res = procs.pool_map(_fit, fjobs, workers)
    regs = {}
    for (k, f), r in zip(fkeys, res):
        regs.setdefault(k, {})[f] = r
    print(f"[variants {time.perf_counter() - t0:7.1f}s] fits done", flush=True)

    gjobs, gkeys = [], []
    for k in variants:
        j, kk = step0._gate_jobs(job, regions, groups, plans[k], regs[k], step0.PRINT_REG_MM, step0.GT.MIN_DECOYS,
                                 (("B", "A"), ("A", "B")))
        gjobs += j
        gkeys += [(k, *x) for x in kk]
    gres = procs.pool_map(step0._gate, gjobs, workers)
    print(f"[variants {time.perf_counter() - t0:7.1f}s] gates done", flush=True)

    result = {"target": TARGET, "print_reg_mm": step0.PRINT_REG_MM, "min_decoys": step0.GT.MIN_DECOYS,
              "n_samples": n_samples, "maxfev": maxfev, "seed": seed, "inputs": job.sources,
              "plan": plan0.to_json(regions), "variants": {}}
    wp3b = REG.start_pose(job)
    for k in variants:
        plan = plans[k]
        rows = []
        for (kk, rr, p, w, v, ev, fit), g in zip(gkeys, gres):
            if kk != k:
                continue
            g = dict(g)
            g.update({"partner": p, "fit_fold": fit})
            rows.append(g)
        registered = {}
        for r_ in plan.parallax:
            reg = [plan.anchors[r_]]
            for p in plan.partners.get(r_, []):
                rr_ = [g for g in rows if g["rid"] == r_ and g["partner"] == p]
                if len(rr_) == 4 and all(g.get("registered") for g in rr_):
                    reg.append(p)
            registered[r_] = reg
        check_rows, diag_rows = [], []
        for r_ in plan.parallax:
            if len(registered[r_]) >= 2:
                check_rows += check_region(job, regions, groups, plan.axes, by[r_], registered[r_], regs[k],
                                           anchor=plan.anchors[r_], seed=seed, sheets_dir=out / "sheets" / k,
                                           wp3b_pose=wp3b)
        if len(registered[rid]) < 2:
            views = [plan.anchors[rid]] + plan.fit_partners(rid)
            diag_rows = check_region(job, regions, groups, plan.axes, by[rid], views, regs[k], anchor=plan.anchors[rid],
                                     seed=seed, sheets_dir=out / "sheets_diagnostic" / k, wp3b_pose=wp3b)
            for r_ in diag_rows:
                r_["diagnostic"] = True
        result["variants"][k] = {
            "signals": list(VARIANTS[k]["signals"]), "single_region": VARIANTS[k]["single_region"],
            "parallax_regions": [by[r_].name for r_ in plan.parallax],
            "fits": {f: RP.strip_timing(regs[k][f].info) for f in ("A", "B")},
            "gates": rows, "gate_table": step0.gate_table(rows, by),
            "registered": {by[r_].name: v for r_, v in registered.items()},
            "check_rows": check_rows, "diagnostic_rows": diag_rows,
            "seconds": {f: regs[k][f].info.get("seconds") for f in ("A", "B")}}
        print(f"[variants {time.perf_counter() - t0:7.1f}s] {k}: registered "
              + "; ".join(f"{by[r_].name}: {registered[r_]}" for r_ in plan.parallax), flush=True)
    result["seconds_total"] = round(time.perf_counter() - t0, 1)
    RP.write_json(out / "variants.json", result)
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--maxfev", type=int, default=REG.MAXFEV)
    ap.add_argument("--samples", type=int, default=30000)
    ap.add_argument("--variants", nargs="+", default=list(VARIANTS))
    a = ap.parse_args(argv)
    r = run(Path(a.out), a.workers, maxfev=a.maxfev, n_samples=a.samples, variants=tuple(a.variants))
    for k, v in r["variants"].items():
        print(k, v["registered"].get(TARGET), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
