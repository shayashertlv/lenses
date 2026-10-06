"""A0 (WP5_DESIGN §8): the synthetic registration proof, shared by the pytest test and the proof run.

q001 with a known print rendered at the six WP3b cameras (tests/photoprint/synth.py); registration is handed q001
undisplaced and view 01's camera off by +1 deg yaw and a 2 mm pivot shift, while the photos carry a 2 mm parallax on
Brow_Right's front.

**Pass criteria are the design's literal §8 wording** (fix round 1; the first build passed A0 on two readings of its
own that the owner never approved):
- ``residual_every_view``: photo-space residual <= 0.2 mm (mean over the region's samples a view sees at donor angles,
  against the TRUE cameras and geometry) for every parallax region at EVERY view that sees it (>= 50 samples), not only
  at the views the objective fits;
- ``unperturbed_bp``: unperturbed (true cameras and geometry) 05 -> 01 bp 1-4 px >= 0.9 (instrument);
- ``null_mean_abs``: the same case's mean |null r| <= 0.05 (the mean of the 18 shifts' |r|);
- ``target_registered_both_directions`` (the registered Brow_Right front, 05 <-> 01);
- the gate refuses (not registered): Astra's texture as the photo, a +2 mm camera, photos 01 and 06 swapped (both
  roles); a sigma = 4 px blurred bake fails C3 (instrument); every guard within 0.02.

The registration uses the design's objective (every partner pair, §5.3; fix round 2) and converged bands.

**Waiver candidates** (``out['waiver_candidates']``: reported with their values, never counted in ``pass``; only the
owner can accept them):
- ``residual_except_thin_views``: the residual rule at every view EXCEPT views outside the objective that see the
  region on fewer samples than the plan requires of a partner (max(500, 10 % of the region's samples)): views the plan
  itself judged too thin to constrain a parallax. With every partner pair in the objective (fix round 2) these thin
  grazing rows are the only ones that fail (the first build's free-partner-only objective also failed the brow fronts at
  04, 0.60 mm); the rows covered are listed with their sample counts;
- |mean null r| <= 0.05 (no bias) instead of mean |r|: the 18-shift null's spread is about 0.08-0.1 for any render,
  perfect or not (probe: std 0.07-0.11 on real print), and mean |r| is about 0.8 std for a centred null, so the literal
  bound fails even for a perfect render. Both values are in the verdict line.
Residuals of the regions without a partner (the fit does not move them) are reported in ``residual_other_regions``.

``gates_floor_only`` (diagnostic, never in ``pass``): each gate case decided WITHOUT decoys (warmth peak >= 0.45,
irange agreement, residual <= 0.8 mm), because the design's decoy rule (each decoy's own displacement keeps 60 %
overlap) leaves the brow fronts seen from 01 with 0-1 decoys even on these perfect photos, so every gate refuses and
the refusal criteria no longer test anything.
"""
from __future__ import annotations

import time

import numpy as np

from glasses3d.g3d_host.camera import project_mm
from glasses3d.g3d_host.photoprint import check_print as CP
from glasses3d.g3d_host.photoprint import edges as ED
from glasses3d.g3d_host.photoprint import gate as GT
from glasses3d.g3d_host.photoprint import regions as RG
from glasses3d.g3d_host.photoprint import register as REG
from glasses3d.g3d_host.photoprint.pose import Deformer, Pose, cam_delta, pack, points, unpack
from glasses3d.tests.instruments import print_metrics as PM
from glasses3d.tests.photoprint import synth

V01, V05, V06 = "reference-01.png", "reference-05.png", "reference-06.png"
RESIDUAL_MAX_MM = 0.2
BP_MIN = 0.9
NULL_MAX = 0.05
TARGET = "Brow_Right[+x]/front"


def _floor_only(g: dict) -> dict:
    """The gate decided without decoys: warmth peak >= 0.45, irange agreement, residual <= 0.8 mm."""
    w = (g.get("signals") or {}).get("warmth") or {}
    sig = bool(w.get("peak") is not None and w["peak"] >= GT.PEAK_MIN and g.get("irange_agrees"))
    return {"significant": sig, "registered": bool(sig and g.get("residual_mm") is not None
                                                   and g["residual_mm"] <= 0.8),
            "peak": w.get("peak"), "residual_mm": g.get("residual_mm"), "decoys": g.get("decoys"),
            "decoy_overlaps": [round(r["overlap_at_offset"], 3) for r in (g.get("decoy_rows") or [])]}


def photo_residuals(job, truth, regions, groups, plan, pose, rids=None) -> list[dict]:
    """Per (region, view that sees it at donor angles, >= 50 samples): |pi_registered - pi_true| / px/mm over those
    samples. ``rids`` default: the parallax regions; every view of ``plan.views`` is tried."""
    by = {r.rid: r for r in regions}
    d = Deformer(job, regions, groups, plan.axes)
    rows = []
    for rid in (plan.parallax if rids is None else rids):
        r = by[rid]
        fit = {plan.anchors[rid], *plan.fit_partners(rid)} if rid in plan.parallax else set()
        gated = {plan.anchors.get(rid), *plan.partners.get(rid, [])} - {None}
        for v in plan.views:
            seen, uv, _ = REG.seen_samples(job, d, pose, r, v)
            if seen.sum() < 50:
                continue
            Pt, _ = points(truth["V"], job.mesh.F, r.samples.face[seen], r.samples.bary[seen])
            ut = project_mm(Pt, truth["cameras"][v], job.frame)
            e = np.linalg.norm(uv[seen] - ut, axis=1) / job.ppm(v, truth["cameras"][v])
            rows.append({"region": r.name, "view": v, "in_fit": v in fit, "gated": v in gated,
                         "samples": int(seen.sum()), "mean_mm": float(e.mean()), "p90_mm": float(np.percentile(e, 90)),
                         "pass": bool(e.mean() <= RESIDUAL_MAX_MM)})
    return rows


def run_a0(*, n_samples: int = 10000, maxfev: int = 1500, log=print, job_truth=None,
           fit_fixed_partners: bool = True) -> dict:
    """A0 on the synthetic job. ``fit_fixed_partners`` (default True, the design's "sum over view pairs", §5.3) puts
    every partner pair in the objective; False = the first build's free-partner-only form (diagnostic)."""
    t0 = time.perf_counter()
    job, truth = job_truth or synth.synthetic_job()
    groups = RG.rigid_groups(job)
    regions = RG.build_regions(job, synth.PRINT_PARTS, groups, n_samples=n_samples)
    by_name = {r.name: r for r in regions}
    adm = ED.admitted(ED.edge_table(job, job.part_ids(synth.PRINT_PARTS)))
    plan = REG.plan_registration(job, regions, groups, adm)
    plan.fit_fixed_partners = bool(fit_fixed_partners)
    reg = REG.register(job, regions, groups, plan, fold="all", maxfev=maxfev, tag="A0", log=log)
    out = {"plan": plan.to_json(regions), "fit": reg.info}
    target = by_name[TARGET]

    # 1. photo-space residuals and parameter recovery
    res = photo_residuals(job, truth, regions, groups, plan, reg.pose)
    out["residuals"] = res
    out["residual_unfitted_views"] = [r for r in res if not r["in_fit"]]
    others = [r.rid for r in regions if not r.minor and r.rid not in set(plan.parallax) and r.rid in plan.anchors]
    out["residual_other_regions"] = photo_residuals(job, truth, regions, groups, plan, reg.pose, rids=others)
    rec = {"camera_01_vs_truth": cam_delta(job, truth["cameras"][V01], reg.pose.cameras[V01], V01),
           "camera_01_start_vs_truth": cam_delta(job, truth["cameras"][V01], job.views[V01].camera, V01),
           "parallax": {by_name[n].name: {"a_mm": p["a_mm"], "b_mm": p["b_mm"],
                                          "true_a_mm": synth.TRUE_PARALLAX_MM if n == TARGET else 0.0}
                        for n, p in reg.info["pose"]["parallax"].items()},
           "hinges": reg.info["pose"]["hinges"]}
    out["recovery"] = rec

    # 2. unperturbed (oracle) round trip 05 -> 01, graded by the instrument
    oracle = Pose(dict(truth["cameras"]), {}, {target.rid: (synth.TRUE_PARALLAX_MM, 0.0)})
    d = Deformer(job, regions, groups, plan.axes)
    ci = CP.case_images(job, d, oracle, target, V01, [V05], None)
    m = CP.grade_case(ci)
    out["unperturbed"] = {"bp_1_4px_r": m["projection"]["bp_1_4px_r"],
                          "null_abs_of_mean": abs(m["projection"]["null_bp_1_4px"][0]),
                          "null_mean_abs": m["projection"]["null_bp_1_4px_mean_abs"],
                          "null_bp_1_4px": m["projection"]["null_bp_1_4px"], "pixels": m["pixels"],
                          "criteria": m["criteria"]}
    # 3. blurred bake fails C3
    blur = ci["p_img"].copy()
    sel = ci["P"]["sel"]
    for c in range(3):
        blur[..., c] = np.where(sel, PM.masked_gauss(ci["p_img"][..., c], sel, 4.0), ci["p_img"][..., c])
    mb = PM.evaluate_case(blur, ci["a_img"], ci["photo"], ci["support"], ci["E"], ci["ppm"],
                          ren_support=ci["ren_support"])
    out["blurred"] = {"energy_0.4_1.6mm": mb["projection"]["energy_0.4_1.6mm"],
                      "energy_1_4px": mb["projection"]["energy_1_4px"], "C3_pass": mb["criteria"]["C3"]["pass"]}

    # 4. gates on the registered pose, and the refusals
    gates = {"registered_05_to_01": GT.gate(job, d, reg.pose, target, V05, V01, None, reg_mm=0.8),
             "registered_01_to_05": GT.gate(job, d, reg.pose, target, V01, V05, None, reg_mm=0.8)}
    astra01 = synth.render_photo(job, truth["V"], truth["cameras"][V01], job.views[V01].shape,
                                 synth.make_astra_colour(job))
    gates["astra_as_photo_05_to_01"] = GT.gate(job, d, reg.pose, target, V05, V01, None, reg_mm=0.8, photo=astra01)
    off = reg.pose.copy()
    x = pack(job, off.cameras[V01])
    x[5] += 2.0 * job.ppm(V01)
    off.cameras[V01] = unpack(job, x)
    gates["camera_plus_2mm_05_to_01"] = GT.gate(job, d, off, target, V05, V01, None, reg_mm=0.8)
    img06_as_01 = synth.fit_shape(job.views[V06].image, job.views[V01].shape)
    gates["photo06_as_01_05_to_01"] = GT.gate(job, d, reg.pose, target, V05, V01, None, reg_mm=0.8,
                                              photo=img06_as_01)
    swapped = job.with_images({V01: img06_as_01, V06: synth.fit_shape(job.views[V01].image, job.views[V06].shape)})
    gates["photo06_as_01_01_to_05"] = GT.gate(swapped, Deformer(swapped, regions, groups, plan.axes), reg.pose,
                                              target, V01, V05, None, reg_mm=0.8)
    out["gates"] = gates

    u = out["unperturbed"]
    crit = {
        "residual_every_view": bool(res and all(r["pass"] for r in res)),
        "target_registered_both_directions": bool(gates["registered_05_to_01"].get("registered")
                                                  and gates["registered_01_to_05"].get("registered")),
        "unperturbed_bp": bool(u["bp_1_4px_r"] >= BP_MIN),
        "null_mean_abs": bool(u["null_mean_abs"] is not None and u["null_mean_abs"] <= NULL_MAX),
        "refuses_astra_as_photo": not gates["astra_as_photo_05_to_01"].get("registered"),
        "refuses_camera_plus_2mm": not gates["camera_plus_2mm_05_to_01"].get("registered"),
        "refuses_swapped_photos": not (gates["photo06_as_01_05_to_01"].get("registered")
                                       or gates["photo06_as_01_01_to_05"].get("registered")),
        "blurred_fails_C3": not out["blurred"]["C3_pass"],
        "guards_within": all(g["within"] for g in reg.info["guards"]),
    }
    out["criteria"] = crit
    out["pass"] = all(crit.values())
    fitted = [r for r in res if r["in_fit"]]
    worst = max(res, key=lambda r: r["mean_mm"]) if res else None
    worst_fit = max(fitted, key=lambda r: r["mean_mm"]) if fitted else None
    thin_need = {r.name: max(REG.MIN_PAIR_SAMPLES, int(REG.MIN_PAIR_SHARE * len(r.samples))) for r in regions}
    thin = [r for r in res if not r["in_fit"] and r["samples"] < thin_need[r["region"]]]
    rest = [r for r in res if r not in thin]
    out["waiver_candidates"] = {
        "residual_except_thin_views": {
            "pass": bool(rest and all(r["pass"] for r in rest)),
            "max_mean_mm_elsewhere": max(r["mean_mm"] for r in rest) if rest else None,
            "rows_waived": [{k: r[k] for k in ("region", "view", "samples", "mean_mm", "p90_mm", "pass")}
                            for r in thin],
            "rule": "views outside the objective seeing the region on fewer samples than a partner needs "
                    "(max(500, 10 % of the region's samples))",
            "why": "the plan judged these views too thin to constrain a parallax, so they are neither fitted nor "
                   "gated; with every partner pair in the objective they are the only rows over 0.2 mm"},
        "null_abs_of_mean": {
            "value": u["null_abs_of_mean"], "pass": bool(u["null_abs_of_mean"] <= NULL_MAX),
            "why": "mean |r| of a centred null is about 0.8 x its std (0.08-0.1 for any render), so the literal bound "
                   "fails even for a perfect render; |mean r| tests the null's bias"}}
    out["gates_floor_only"] = {k: _floor_only(g) for k, g in gates.items()}
    failing = [k for k, v in crit.items() if not v]
    out["verdict_line"] = (
        f"A0 {'PASS' if out['pass'] else 'FAIL'} (literal WP5_DESIGN section 8 criteria"
        + (f"; failing: {', '.join(failing)}" if failing else "") + "): "
        + (f"photo residual max {worst['mean_mm']:.3f} mm (p90 {worst['p90_mm']:.3f}) at "
           f"{worst['view']} {worst['region']} on {worst['samples']} samples (limit {RESIDUAL_MAX_MM}; fitted views "
           f"max {worst_fit['mean_mm']:.3f} mm; except thin views max "
           f"{out['waiver_candidates']['residual_except_thin_views']['max_mean_mm_elsewhere']:.3f} mm); "
           if worst and worst_fit else "no residual rows; ")
        + "target 05<->01 " + ("registered" if crit["target_registered_both_directions"] else
                               "NOT registered (" + ", ".join(
                                   f"{k[11:]} {gates[k].get('reason')} ({gates[k].get('decoys')} decoys)"
                                   for k in ("registered_05_to_01", "registered_01_to_05")) + ")") + "; "
        + f"unperturbed bp {u['bp_1_4px_r']:.4f} (>= {BP_MIN}); mean |null r| {u['null_mean_abs']:.4f} "
        f"(limit {NULL_MAX}; |mean null r| {u['null_abs_of_mean']:.4f}); "
        f"refusals {'all hold' if crit['refuses_astra_as_photo'] and crit['refuses_camera_plus_2mm'] and crit['refuses_swapped_photos'] else 'BROKEN'}; "
        f"blurred bake C3 {'fails (as required)' if crit['blurred_fails_C3'] else 'PASSES (wrong)'}; "
        f"guards {'within' if crit['guards_within'] else 'BROKEN'}")
    out["seconds"] = round(time.perf_counter() - t0, 1)
    return out
