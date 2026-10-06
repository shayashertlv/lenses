"""A0 (WP5_DESIGN §8): the synthetic registration proof, shared by the pytest test and the proof run.

q001 with a known print rendered at the six WP3b cameras (tests/photoprint/synth.py); registration is handed q001
undisplaced and view 01's camera off by +1 deg yaw and a 2 mm pivot shift, while the photos carry a 2 mm parallax on
Brow_Right's front.

**Pass criteria** (fix round 2: the design's §8 list as written, plus one measured estimator correction):
- ``residual_every_view`` (the LITERAL §8 rule): photo-space residual <= 0.2 mm (mean over the region's samples a view
  sees at donor angles, against the TRUE cameras and geometry) for every parallax region at EVERY view that sees it
  (>= 50 samples). It FAILS on q001 (measured 2026-10-06: Brow_Left top at 03, 312 samples, 0.33 mm; -x temple inner
  at 04, 199, 0.30; +x temple outer at 04, 274, 0.24), so **A0 FAILS**. Fix round 1 had exempted exactly these rows
  ("known limits": views outside the objective seeing the region on fewer samples than a partner needs) and called the
  result a pass; that exemption is neither an owner waiver nor a decision recorded in PROGRESS.md, so it is no longer
  a criterion. It is reported as ``reported.residual_except_known_limits`` with ``exemption_status`` 'unrecorded',
  and the verdict line says A0 would pass only under it.
- **Parameter recovery** (§8: "parameter recovery reported", risk 2), reported in ``recovery.parallax_errors`` and in
  the verdict line: per parallax region, the fitted (a, b) against the truth (Brow_Right front 2 mm, every other 0) and
  max over s in [-1, 1] of |da + db s| = |da| + |db|. The known-limit rows are the regions whose depth the fitted
  views cannot see: a = -0.49, b = +0.51 mm on the -x temple inner and a = -0.49, b = +0.53 mm on the +x temple outer
  (about 1 mm of depth error at the strip ends) where the truth is 0. That depth error is what the every-view rule
  catches (risk 2: camera and parallax trade off).
- ``unperturbed_bp``: unperturbed (true cameras and geometry) 05 -> 01 bp 1-4 px >= 0.9 (instrument);
- ``null_bias``: the same case's |mean null r| <= 0.05 (the instrument's ``null_bp_1_4px_bias``). **Estimator
  correction (recorded in PROGRESS 2026-10-06 with the decoy-null redesign):** §8 wrote mean |null r|; for a centred
  null that estimates sqrt(2 / pi) x the null's spread (about 0.8 sigma), and the spread is set by E's effective sample
  count (about 0.07 for this case), not by any bias, so the literal estimator fails for a PERFECT render (0.057). The
  bound is kept at 0.05 and applied to what it is meant to test, the null's bias. mean |r| stays reported
  (``null_mean_abs``);
- ``target_registered_both_directions`` (the registered Brow_Right front, 05 <-> 01), with the gate's strip null
  (``gate.NULL = 'strip'``, the decoy-null redesign recorded in PROGRESS 2026-10-06);
- the gate refuses (not registered): Astra's texture as the photo, a +2 mm camera, photos 01 and 06 swapped (both
  roles); a sigma = 4 px blurred bake fails C3 (instrument); every guard within 0.02;
- ``refuses_by_null_alone``: on ``synth.StripPrint`` (a havana elongated along the strip, part periodic with the null's
  own 8 mm period along its own surface coordinate), view 01's photo shifted 8 mm along the strip clears the floor and
  must be refused by the decoy rule alone, in both directions.

**Refusal margins** (fix round 2, ``refusal_margins``): every gate case carries how far it is from registering: the
warmth peak against the floor 0.45 and against decoy max + 0.15, the residual against 0.8 mm, the decoy count against
4, and which tests refuse it. Measured at fix round 1: no foreign-photo refusal has a comfortable margin (06-as-01
05 -> 01 refused by the floor at 0.417 vs 0.45; 01 -> 05 by 0 counted decoys, its residual 0.999 vs 0.8 mm; the 8 mm
strip print 01 -> 05 by 0.037 below the decoy rule). FT1119's output does not depend on the gate (nothing registers).

**False-registration sweep** (fix round 2, ``false_registration``; reported, never in ``pass``): the gate on several
WRONG correspondences: on the target region, StripPrint shifted 8 and 16 mm (two print seeds, both directions), A0's
isotropic print shifted 2, 4 and 8 mm along x (both directions), photo 03 as 01 (both roles); on a second region (the
-x brow front) its own StripPrint shifted 8 and 16 mm (both directions); plus the KNOWN FALSE
POSITIVE ``StripPrint(periodic='x3d')`` (p periodic in 3D x, not along the null's surface coordinate: the brow's
curvature breaks it under the null's shift, so an 8 mm-wrong photo registered at fix round 1, peak 0.64 against decoy
max 0.30) shifted 8 mm, both directions; the rate (registered by the rule / cases) is reported with and without the
known false positive, and the x3d cases are pinned as xfail(strict) in test_register_synthetic.py.

The registration uses the design's objective (every partner pair, §5.3) and converged bands. Residuals of the regions
without a partner (the fit does not move them) are reported in ``residual_other_regions``. ``gates_floor_only``
(diagnostic, never in ``pass``): each gate case decided WITHOUT decoys (warmth peak >= 0.45, irange agreement,
residual <= 0.8 mm). ``gates_displace_null`` (diagnostic): the target pair under the legacy null.
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

V01, V03, V05, V06 = "reference-01.png", "reference-03.png", "reference-05.png", "reference-06.png"
RESIDUAL_MAX_MM = 0.2
BP_MIN = 0.9
NULL_MAX = 0.05            # applied to |mean null r| (the bias), see the module docstring
REG_MM = 0.8
TARGET = "Brow_Right[+x]/front"
OTHER_REGION = "Brow_Left[-x]/front"     # the false-registration sweep's second region
EXEMPTION_STATUS = "unrecorded"   # the known-limit exemption: no owner waiver, no PROGRESS decision (fix round 2)


def _floor_only(g: dict) -> dict:
    """The gate decided without decoys: warmth peak >= 0.45, irange agreement, residual <= 0.8 mm."""
    w = (g.get("signals") or {}).get("warmth") or {}
    sig = bool(w.get("peak") is not None and w["peak"] >= GT.PEAK_MIN and g.get("irange_agrees"))
    return {"significant": sig, "registered": bool(sig and g.get("residual_mm") is not None
                                                   and g["residual_mm"] <= REG_MM),
            "peak": w.get("peak"), "residual_mm": g.get("residual_mm"), "decoys": g.get("decoys"),
            "decoy_overlaps": [round(r["overlap_at_offset"], 3) for r in (g.get("decoy_rows") or [])]}


def refusal_margins(g: dict, reg_mm: float = REG_MM, min_decoys: int = GT.MIN_DECOYS) -> dict:
    """How far one gate case is from registering (fix round 2). Positive margins refuse: ``floor_margin`` = 0.45 -
    peak, ``decoy_margin`` = decoy max + 0.15 - peak (when >= ``min_decoys`` decoys count), ``residual_margin_mm`` =
    residual - 0.8 mm, ``decoy_count_margin`` = 4 - counted decoys. ``refused_by`` lists the tests that refuse;
    ``margin`` = the largest refusing margin in its own unit (None when registered)."""
    w = (g.get("signals") or {}).get("warmth") or {}
    peak, dmax, nd = w.get("peak"), w.get("decoy_max"), g.get("decoys") or 0
    res = g.get("residual_mm")
    out = {"registered": bool(g.get("registered")), "reason": g.get("reason"), "peak": peak, "decoy_max": dmax,
           "decoys": int(nd), "residual_mm": res, "irange_agrees": g.get("irange_agrees")}
    m = {}
    if peak is not None:
        m["floor_margin"] = float(GT.PEAK_MIN - peak)
        if dmax is not None and nd >= min_decoys:
            m["decoy_margin"] = float(dmax + GT.DECOY_MARGIN - peak)
    if res is not None:
        m["residual_margin_mm"] = float(res - reg_mm)
    m["decoy_count_margin"] = int(min_decoys - nd)
    out.update(m)
    refusing = [k for k, v in m.items() if v is not None and v > 0]
    if g.get("irange_agrees") is False:
        refusing.append("irange_disagrees")
    if peak is None:
        refusing.append("no_valid_shift")
    out["refused_by"] = refusing
    return _margin_summary(out)


def _margin_summary(row: dict) -> dict:
    """``margin`` / ``margin_unit`` of a refusal row (in place): the peak margin (max of the refusing floor / decoy
    margins) when the peak refuses, else the residual margin (mm), else the decoy-count margin; ``applicable`` is False
    when the gate had too few pixels to test anything (a swapped photo that misses the region)."""
    ref = set(row.get("refused_by") or [])
    peak = [row[k] for k in ("floor_margin", "decoy_margin") if k in ref and row.get(k) is not None]
    row["peak_margin"] = max(peak) if peak else None
    row["applicable"] = row.get("reason") != "too_few_pixels"
    if row.get("registered"):
        row["margin"], row["margin_unit"] = None, None
    elif peak:
        row["margin"], row["margin_unit"] = max(peak), "peak"
    elif "residual_margin_mm" in ref:
        row["margin"], row["margin_unit"] = row["residual_margin_mm"], "mm"
    elif "decoy_count_margin" in ref:
        row["margin"], row["margin_unit"] = row["decoy_count_margin"], "decoys"
    else:
        row["margin"], row["margin_unit"] = None, None
    return row


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


def parallax_errors(parallax: dict) -> dict:
    """Per parallax region: fitted (a, b), truth, da, db and max over s in [-1, 1] of |da + db s| = |da| + |db|."""
    out = {}
    for n, p in parallax.items():
        ta = synth.TRUE_PARALLAX_MM if n == TARGET else 0.0
        da, db = p["a_mm"] - ta, p["b_mm"] - 0.0
        out[n] = {"a_mm": p["a_mm"], "b_mm": p["b_mm"], "true_a_mm": ta, "true_b_mm": 0.0, "da_mm": float(da),
                  "db_mm": float(db), "max_abs_error_mm": float(abs(da) + abs(db))}
    return out


def _sweep_cases(job, truth, regions, groups, plan, oracle, target) -> dict:
    """The false-registration sweep (see the module docstring): {case: refusal_margins row + 'known_false_positive'}."""
    out = {}
    other = next(r for r in regions if r.name == OTHER_REGION)

    def run(key, imgs, w, v, kfp=False, region=None):
        jw = job.with_images(imgs)
        g = GT.gate(jw, Deformer(jw, regions, groups, plan.axes), oracle, region or target, w, v, None, reg_mm=REG_MM)
        out[key] = {**refusal_margins(g), "known_false_positive": bool(kfp), "region": (region or target).name}

    # a second region (the -x brow front): its own strip print, shifted 8 and 16 mm along its strip
    sl = synth.strip_print_images(job, truth, [V05, V01], part="Brow_Left")
    for shift in (8.0, 16.0):
        wl = synth.strip_print_images(job, truth, [V01], shift_x_mm={V01: shift}, part="Brow_Left")
        imgs = {V05: sl[V05], V01: wl[V01]}
        run(f"strip_left_shift{shift:g}_05_to_01", imgs, V05, V01, region=other)
        run(f"strip_left_shift{shift:g}_01_to_05", imgs, V01, V05, region=other)

    sp_true = synth.strip_print_images(job, truth, [V05, V01])
    for seed in (23, 31):
        base = sp_true if seed == 23 else synth.strip_print_images(job, truth, [V05, V01], seed=seed)
        for shift in (8.0, 16.0):
            wrong = synth.strip_print_images(job, truth, [V01], shift_x_mm={V01: shift}, seed=seed)
            imgs = {V05: base[V05], V01: wrong[V01]}
            run(f"strip_seed{seed}_shift{shift:g}_05_to_01", imgs, V05, V01)
            run(f"strip_seed{seed}_shift{shift:g}_01_to_05", imgs, V01, V05)
    for shift in (2.0, 4.0, 8.0):
        wrong = synth.shifted_print_images(job, truth, [V01], shift)
        imgs = {V01: wrong[V01]}
        run(f"isotropic_shift{shift:g}_05_to_01", imgs, V05, V01)
        run(f"isotropic_shift{shift:g}_01_to_05", imgs, V01, V05)
    img03 = synth.fit_shape(job.views[V03].image, job.views[V01].shape)
    run("photo03_as_01_05_to_01", {V01: img03}, V05, V01)
    run("photo03_as_01_01_to_05", {V01: img03, V03: synth.fit_shape(job.views[V01].image, job.views[V03].shape)},
        V01, V05)
    x3 = synth.strip_print_images(job, truth, [V05, V01], periodic="x3d")
    x3w = synth.strip_print_images(job, truth, [V01], shift_x_mm={V01: synth.STRIP_PERIOD_MM}, periodic="x3d")
    imgs = {V05: x3[V05], V01: x3w[V01]}
    run("x3d_periodic_shift8_05_to_01", imgs, V05, V01, kfp=True)
    run("x3d_periodic_shift8_01_to_05", imgs, V01, V05, kfp=True)
    return out


def sweep_summary(cases: dict) -> dict:
    """Rate and smallest margins (per unit) of the false-registration sweep; cases the gate could not test (too few
    pixels) are listed and left out of the rate."""
    for c in cases.values():
        _margin_summary(c)
    app = {k: c for k, c in cases.items() if c["applicable"]}
    ordinary = {k: c for k, c in app.items() if not c["known_false_positive"]}
    kfp = {k: c for k, c in cases.items() if c["known_false_positive"]}
    small = {}
    for unit in ("peak", "mm"):
        rows = [(c["margin"], k) for k, c in ordinary.items() if c["margin_unit"] == unit]
        if rows:
            small[unit] = min(rows)
    return {"cases": cases,
            "not_applicable": sorted(k for k, c in cases.items() if not c["applicable"]),
            "rate_without_known_false_positive": float(sum(c["registered"] for c in ordinary.values())
                                                       / max(len(ordinary), 1)),
            "registered_without_known_false_positive": sorted(k for k, c in ordinary.items() if c["registered"]),
            "rate_with_known_false_positive": float(sum(c["registered"] for c in app.values()) / max(len(app), 1)),
            "known_false_positive_registered": {k: c["registered"] for k, c in kfp.items()},
            "smallest_refusal_margin": small, "n_cases": len(cases), "n_ordinary": len(ordinary),
            "margins_note": "peak margin = how far the warmth peak sits below the binding rule (floor 0.45 or decoy "
                            "max + 0.15); mm = residual above 0.8 mm; see refusal_margins"}


def regrade(a0: dict) -> dict:
    """Re-derive the margin summaries and the verdict of a stored A0 result (grading only; no registration is
    re-run)."""
    for k, g in a0["gates"].items():
        a0["refusal_margins"][k] = refusal_margins(g)
    for k, c in a0["strip_print"]["cases"].items():
        _margin_summary(c["margins"])
        a0["refusal_margins"][f"strip_{k}"] = c["margins"]
    if "false_registration" in a0:
        a0["false_registration"] = sweep_summary(a0["false_registration"]["cases"])
    return grade(a0)


def run_a0(*, n_samples: int = 10000, maxfev: int = 1500, log=print, job_truth=None,
           fit_fixed_partners: bool = True, sweep: bool = True) -> dict:
    """A0 on the synthetic job. ``fit_fixed_partners`` (default True, the design's "sum over view pairs", §5.3) puts
    every partner pair in the objective; False = the first build's free-partner-only form (diagnostic). ``sweep``
    runs the false-registration sweep (reported only)."""
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
    rec["parallax_errors"] = parallax_errors(rec["parallax"])
    out["recovery"] = rec

    # 2. unperturbed (oracle) round trip 05 -> 01, graded by the instrument
    oracle = Pose(dict(truth["cameras"]), {}, {target.rid: (synth.TRUE_PARALLAX_MM, 0.0)})
    d = Deformer(job, regions, groups, plan.axes)
    ci = CP.case_images(job, d, oracle, target, V01, [V05], None)
    m = CP.grade_case(ci)
    out["unperturbed"] = {"bp_1_4px_r": m["projection"]["bp_1_4px_r"],
                          "null_abs_of_mean": abs(m["projection"]["null_bp_1_4px"][0]),
                          "null_bias": m["projection"]["null_bp_1_4px_bias"],
                          "null_n": m["projection"]["null_bp_1_4px_n"],
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
    gates = {"registered_05_to_01": GT.gate(job, d, reg.pose, target, V05, V01, None, reg_mm=REG_MM),
             "registered_01_to_05": GT.gate(job, d, reg.pose, target, V01, V05, None, reg_mm=REG_MM)}
    astra01 = synth.render_photo(job, truth["V"], truth["cameras"][V01], job.views[V01].shape,
                                 synth.make_astra_colour(job))
    gates["astra_as_photo_05_to_01"] = GT.gate(job, d, reg.pose, target, V05, V01, None, reg_mm=REG_MM, photo=astra01)
    off = reg.pose.copy()
    x = pack(job, off.cameras[V01])
    x[5] += 2.0 * job.ppm(V01)
    off.cameras[V01] = unpack(job, x)
    gates["camera_plus_2mm_05_to_01"] = GT.gate(job, d, off, target, V05, V01, None, reg_mm=REG_MM)
    img06_as_01 = synth.fit_shape(job.views[V06].image, job.views[V01].shape)
    gates["photo06_as_01_05_to_01"] = GT.gate(job, d, reg.pose, target, V05, V01, None, reg_mm=REG_MM,
                                              photo=img06_as_01)
    swapped = job.with_images({V01: img06_as_01, V06: synth.fit_shape(job.views[V01].image, job.views[V06].shape)})
    gates["photo06_as_01_01_to_05"] = GT.gate(swapped, Deformer(swapped, regions, groups, plan.axes), reg.pose,
                                              target, V01, V05, None, reg_mm=REG_MM)
    # 5. the null where it binds: strip-elongated print, oracle pose
    sp = synth.strip_print_images(job, truth, [V05, V01])
    sp_wrong = synth.strip_print_images(job, truth, [V01], shift_x_mm={V01: synth.STRIP_PERIOD_MM})
    js = job.with_images(sp)
    jw = job.with_images({V05: sp[V05], V01: sp_wrong[V01]})
    ds, dw = Deformer(js, regions, groups, plan.axes), Deformer(jw, regions, groups, plan.axes)
    strip = {"true_05_to_01": GT.gate(js, ds, oracle, target, V05, V01, None, reg_mm=REG_MM),
             "true_01_to_05": GT.gate(js, ds, oracle, target, V01, V05, None, reg_mm=REG_MM),
             "wrong_shift8_05_to_01": GT.gate(jw, dw, oracle, target, V05, V01, None, reg_mm=REG_MM),
             "wrong_shift8_01_to_05": GT.gate(jw, dw, oracle, target, V01, V05, None, reg_mm=REG_MM)}
    out["strip_print"] = {
        "cases": {k: {**_floor_only(g), "registered_by_rule": bool(g.get("registered")), "reason": g.get("reason"),
                      "margins": refusal_margins(g)}
                  for k, g in strip.items()},
        "power": {"true_registered_both_directions": bool(strip["true_05_to_01"].get("registered")
                                                         and strip["true_01_to_05"].get("registered"))},
        "print": {"alpha_periodic": synth.STRIP_ALPHA, "period_mm": synth.STRIP_PERIOD_MM, "band": synth.STRIP_BAND}}
    out["strip_print"]["null_alone_refuses"] = all(
        out["strip_print"]["cases"][k]["registered"] and not out["strip_print"]["cases"][k]["registered_by_rule"]
        for k in ("wrong_shift8_05_to_01", "wrong_shift8_01_to_05"))
    out["gates"] = gates
    out["refusal_margins"] = {k: refusal_margins(g) for k, g in gates.items()}
    out["refusal_margins"].update({f"strip_{k}": v["margins"] for k, v in out["strip_print"]["cases"].items()})
    out["gates_displace_null"] = {}
    for k, a, b in (("registered_05_to_01", V05, V01), ("registered_01_to_05", V01, V05)):
        gd = GT.gate(job, d, reg.pose, target, a, b, None, reg_mm=REG_MM, null="displace")
        out["gates_displace_null"][k] = {**_floor_only(gd), "registered_by_rule": gd.get("registered"),
                                         "reason": gd.get("reason")}
    # 6. false-registration sweep (fix round 2; reported, never in ``pass``)
    if sweep:
        out["false_registration"] = sweep_summary(_sweep_cases(job, truth, regions, groups, plan, oracle, target))

    out["thin_need"] = {r.name: max(REG.MIN_PAIR_SAMPLES, int(REG.MIN_PAIR_SHARE * len(r.samples))) for r in regions}
    out["seconds_before_grade"] = round(time.perf_counter() - t0, 1)
    grade(out)
    out["seconds"] = round(time.perf_counter() - t0, 1)
    return out


def grade(out: dict) -> dict:
    """Criteria, verdict and the reported readings of an A0 result (in place; also returned)."""
    res = out["residuals"]
    gates = out["gates"]
    u = out["unperturbed"]
    thin_need = out["thin_need"]
    known = [r for r in res if not r["in_fit"] and r["samples"] < thin_need[r["region"]]]
    rest = [r for r in res if r not in known]
    out["known_limits"] = {
        "rows": [{k: r[k] for k in ("region", "view", "samples", "mean_mm", "p90_mm", "pass")} for r in known],
        "rule": "views outside the objective seeing the region on fewer samples than a partner needs "
                "(max(500, 10 % of the region's samples))",
        "statement": "the fit does not constrain a region at a thin grazing view it neither fits nor gates; the photo "
                     "residual there is not controlled. Fix round 2: these rows are GRADED (the literal §8 rule); the "
                     "exemption that skipped them is unrecorded (no owner waiver, no PROGRESS decision)",
        "exemption_status": EXEMPTION_STATUS}
    perr = out["recovery"]["parallax_errors"]
    crit = {
        "residual_every_view": bool(res and all(r["pass"] for r in res)),
        "target_registered_both_directions": bool(gates["registered_05_to_01"].get("registered")
                                                  and gates["registered_01_to_05"].get("registered")),
        "unperturbed_bp": bool(u["bp_1_4px_r"] >= BP_MIN),
        "null_bias": bool(u["null_bias"] is not None and u["null_bias"] <= NULL_MAX),
        "refuses_astra_as_photo": not gates["astra_as_photo_05_to_01"].get("registered"),
        "refuses_camera_plus_2mm": not gates["camera_plus_2mm_05_to_01"].get("registered"),
        "refuses_swapped_photos": not (gates["photo06_as_01_05_to_01"].get("registered")
                                       or gates["photo06_as_01_01_to_05"].get("registered")),
        "blurred_fails_C3": not out["blurred"]["C3_pass"],
        "refuses_by_null_alone": out["strip_print"]["null_alone_refuses"],
        "guards_within": all(g["within"] for g in out["fit"]["guards"]),
    }
    out["criteria"] = crit
    out["pass"] = all(crit.values())
    except_known = bool(rest and all(r["pass"] for r in rest))
    others_pass = all(v for k, v in crit.items() if k != "residual_every_view")
    out["reported"] = {
        "residual_except_known_limits": except_known,
        "would_pass_under_known_limit_exemption": bool(except_known and others_pass),
        "exemption_status": EXEMPTION_STATUS,
        "null_mean_abs": bool(u["null_mean_abs"] is not None and u["null_mean_abs"] <= NULL_MAX),
        "parallax_max_abs_error_mm": {n: round(v["max_abs_error_mm"], 3) for n, v in perr.items()}}
    out.pop("waiver_candidates", None)
    out["gates_floor_only"] = {k: _floor_only(g) for k, g in gates.items()}
    failing = [k for k, v in crit.items() if not v]
    over = [r for r in res if not r["pass"]]
    over_txt = "; ".join(f"{r['region']} at {r['view'][-6:-4]} {r['mean_mm']:.3f} mm ({r['samples']} samples, "
                         f"{'in' if r['in_fit'] else 'outside'} the fit)" for r in over) or "none"
    fitted = [r for r in res if r["in_fit"]]
    worst_fit = max(fitted, key=lambda r: r["mean_mm"]) if fitted else None
    par_txt = "; ".join(f"{n} a {v['a_mm']:+.2f} b {v['b_mm']:+.2f} (truth {v['true_a_mm']:+.1f}, 0): error up to "
                        f"{v['max_abs_error_mm']:.2f} mm" for n, v in sorted(perr.items(),
                                                                          key=lambda kv: -kv[1]["max_abs_error_mm"]))
    tg = gates["registered_05_to_01"], gates["registered_01_to_05"]
    rm = out.get("refusal_margins") or {}
    ref_txt = ", ".join(f"{k} {v['margin']:.3f} {v['margin_unit']} by {'/'.join(v['refused_by'])}"
                        for k, v in rm.items() if not v["registered"] and v.get("margin") is not None
                        and not k.startswith("strip_true"))
    fr = out.get("false_registration")
    fr_txt = ""
    if fr:
        sm = fr["smallest_refusal_margin"]
        fr_txt = (f"; false-registration sweep: {fr['rate_without_known_false_positive']:.2f} of "
                  f"{fr['n_ordinary']} wrong correspondences registered"
                  + (f" ({', '.join(fr['registered_without_known_false_positive'])})"
                     if fr["registered_without_known_false_positive"] else "")
                  + (", smallest refusal margins " + ", ".join(f"{m_:.3f} {u_} ({k_})" for u_, (m_, k_) in sm.items())
                     if sm else "")
                  + (f" ({len(fr['not_applicable'])} swapped-photo cases untestable: too few pixels)"
                     if fr.get("not_applicable") else "")
                  + "; KNOWN FALSE POSITIVE (3D-x periodic strip print, 8 mm wrong): "
                  + ", ".join(f"{k[-8:]} {'REGISTERED' if v else 'refused'}"
                              for k, v in fr["known_false_positive_registered"].items()))
    out["verdict_line"] = (
        f"A0 {'PASS' if out['pass'] else 'FAIL'}" + (f" (failing: {', '.join(failing)})" if failing else "") + ": "
        + f"photo residual over {RESIDUAL_MAX_MM} mm at: {over_txt}"
        + (f"; fitted views max {worst_fit['mean_mm']:.3f} mm" if worst_fit else "")
        + (f"; A0 would pass only under the known-limit exemption (thin grazing views outside the fit), which is "
           f"{EXEMPTION_STATUS} (no owner waiver, no PROGRESS decision)"
           if out["reported"]["would_pass_under_known_limit_exemption"] and not out["pass"] else "")
        + f"; parallax recovery: {par_txt}"
        + "; target 05<->01 " + ("registered" if crit["target_registered_both_directions"] else "NOT registered")
        + " (strip null: " + ", ".join(
            f"peak {(g.get('signals') or {}).get('warmth', {}).get('peak') or float('nan'):.3f} vs decoy max "
            f"{(g.get('signals') or {}).get('warmth', {}).get('decoy_max') or float('nan'):.3f} over "
            f"{g.get('decoys')} decoys, residual {g.get('residual_mm') or float('nan'):.3f} mm" for g in tg) + "); "
        + f"unperturbed bp {u['bp_1_4px_r']:.4f} (>= {BP_MIN}); null bias |mean r| {u['null_bias']:.4f} "
        f"(limit {NULL_MAX}; mean |r| {u['null_mean_abs']:.4f} reported, see the estimator correction); "
        f"refusals {'all hold' if crit['refuses_astra_as_photo'] and crit['refuses_camera_plus_2mm'] and crit['refuses_swapped_photos'] else 'BROKEN'}"
        + (f" (margins: {ref_txt})" if ref_txt else "") + "; "
        + "strip print (null where it binds): wrong 8 mm match " + (
            "clears the floor and is refused by the null alone" if crit["refuses_by_null_alone"] else
            "NOT a null-alone refusal ({})".format({k: v for k, v in out["strip_print"]["cases"].items()
                                                    if k.startswith("wrong")}))
        + ", true match " + ("registers both ways" if out["strip_print"]["power"]["true_registered_both_directions"]
                             else "is REFUSED (the null blocks a perfect match on strip print)")
        + fr_txt + "; "
        f"blurred bake C3 {'fails (as required)' if crit['blurred_fails_C3'] else 'PASSES (wrong)'}; "
        f"guards {'within' if crit['guards_within'] else 'BROKEN'}")
    return out


def summary_for_step0(a0: dict) -> tuple[bool, str, str]:
    """(A0 pass, verdict line, STOP A note) for ``report.finalize``: the note says whether the verdict rests on the
    literal rule and what an exemption would change (fix round 2)."""
    rep = a0.get("reported") or {}
    if a0.get("pass"):
        note = "passed under the literal §8 rule"
    elif rep.get("would_pass_under_known_limit_exemption"):
        note = ("FAILED under the literal §8 rule (residual_every_view); it would pass only under the known-limit "
                f"exemption, which is {rep.get('exemption_status', EXEMPTION_STATUS)} (no owner waiver, no PROGRESS "
                "decision)")
    else:
        note = "FAILED: " + ", ".join(k for k, v in (a0.get("criteria") or {}).items() if not v)
    return bool(a0.get("pass")), a0.get("verdict_line"), note

