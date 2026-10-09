"""WP6 $0 proof, the stages after S1 (tests/proofs/wp6_prada.py runs them in the §9 order): P2 tracing, the joint fit
of the candidate and of the fitted slab with S2 (STOP-S) and S3 (STOP-C), P3 build on a scene copy, the gates (G1-G5,
R1-R4, V; STOP-P) and the six-view sheet. Every output is a TOOL TEST."""
from __future__ import annotations

import copy
import hashlib
import json
import pickle
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from glasses3d.g3d.temple import build_temple, report
from glasses3d.g3d.temple import fixtures as FX
from glasses3d.g3d_host import relief_check as RC
from glasses3d.g3d_host import relief_cue as CU
from glasses3d.g3d_host import relief_fit as RF
from glasses3d.g3d_host import relief_rules as RR
from glasses3d.g3d_host import relief_trace as RT
from glasses3d.g3d_host import relief_views as RV
from glasses3d.g3d_host.spec import validate_spec


def _spec_sha(spec: dict) -> str:
    return hashlib.sha256(json.dumps(spec, sort_keys=True, default=float).encode("utf-8")).hexdigest()


def _side_fits(fits):
    return {k: {"camera": f["camera"], "theta": f["theta"]} for k, f in fits.items()}


RETRACE_ITERS = 2


def make_retrace(job, log):
    """The joint fit's outline re-trace (§7: "the outline is re-traced by trace_outline each round, not optimised"),
    shared by stage_fit and S1 (wp6_prada.stage_s1), so S1 grades the procedure the real fit runs: the current family's
    outline traced on 05 / 01 (RETRACE_ITERS iterations) at the cameras relief_fit.fit_temple passes (the previous
    camera block's); returns a deep-copied family with the new outline (the caller's family is never mutated)."""
    def retrace(family, x, fits):
        kw = family.kwargs(x)
        builder = lambda tt, bb: (family.builder(**{**kw, "top": tt, "bottom": bb}) if not family.slab_mode
                                  else RF.slab_spec(**{**kw, "top": tt, "bottom": bb}))
        ol = RT.trace_outline(job, family.spec(x), builder, _side_fits({k: fits[k] for k in ("05", "01")}),
                              ("05", "01"), iters=RETRACE_ITERS, log=log)
        fam2 = copy.deepcopy(family)
        fam2.base["top"], fam2.base["bottom"] = ol["top"], ol["bottom"]
        return fam2
    return retrace


S1_CASE_B = "b fitted cameras, fitted theta"


def _s1_case_b(rec: dict) -> float | None:
    """S1 case (b) (wp6_s1_theta_check.transfer_cases): the R3 transfer of the exact TRUE temple, with its true crease
    lines, at the S1-fitted cameras and theta: what R3 measures on a perfect model. From the proof's S1 record, or the
    proof record a diagnostic copied (rec["proof_record"]["s1_case_b_mm"])."""
    s1 = (rec.get("stages") or {}).get("S1") or {}
    # fit-4: what a perfect model transfers at the S1-fitted cameras is the instrument floor's truth
    if "instrument_floor" in s1:
        v = (((s1.get("instrument_floor") or {}).get("models") or {}).get("truth") or {}).get("R3_transfer_mm")
        return None if v is None else float(v)  # an unmeasurable floor must not borrow ideal-line evidence
    v = ((s1.get("theta_cases") or {}).get("cases") or {}).get(S1_CASE_B, {}).get("mean_mm")
    if v is None:
        v = (rec.get("proof_record") or {}).get("s1_case_b_mm")
    return None if v is None else float(v)


def open_owner_decisions(rec: dict) -> list:
    """The rule-set questions only the owner can decide (relief_rules.py is frozen; changing a threshold or a gate's
    definition is a new rule set, §8 / §9): R1's share limit and R3's "delivered - 0.5" clause. Recorded in every proof
    and diagnostic record, whatever stop fires, with the evidence on record."""
    b = _s1_case_b(rec)
    g3 = ((rec.get("stages") or {}).get("GATES") or {}).get("gates", {}).get("R3") or {}
    dl = (g3.get("transfer_mm") or {}).get("delivered")
    return [
        {"gate": "R1", "rule": f"single-plane share <= {RR.R1_SHARE_MAX} (relief_rules.R1_SHARE_MAX)",
         "why_open": "the synthetic faceted truths fail it with the shipped measure core (FX.prada_default 0.668, the "
                     "S1-like truth 0.708; minimum 0.596 over the Prada fit band): every facet plane is a share "
                     "candidate (§5) while the 0.5 came from least-squares-plane-only prototype numbers",
         "evidence": "tests/test_temple_slab_control.py::test_synthetic_faceted_truths_pass_r1 (xfail strict: it turns "
                     "into a failure (XPASS) once R1 becomes passable)",
         "decision": "owner: keep R1, change its threshold, or change the share definition (a new rule set)"},
        {"gate": "R3", "rule": f"05 -> 02 transfer <= {RR.R3_TRANSFER_MAX_MM} mm AND <= delivered - "
                               f"{RR.R3_TRANSFER_MARGIN_MM} AND <= fitted slab - {RR.R3_TRANSFER_MARGIN_MM}",
         "why_open": "the 'delivered - 0.5' clause looks impossible on the project's own numbers: a perfect model "
                     "fails it whenever the delivered model's transfer is below the true temple's own transfer + 0.5 "
                     "mm (evidence: this record's S1 true-temple transfer and its delivered transfer). Fix round 3 "
                     "adds that the 0.7 mm limit itself sits inside what a perfect model transfers once its lines "
                     "carry the real 02 tracer's 3-7 px endpoint moves (data/wp6/s1_floor_check.json)",
         "evidence": {"s1_case_b_true_temple_mm": b, "delivered_transfer_mm_this_record": dl,
                      "delivered_minus_margin_mm_this_record": None if dl is None else dl - RR.R3_TRANSFER_MARGIN_MM,
                      "note": "s1_case_b is this record's S1 (or the proof's, for a diagnostic); the delivered value "
                              "is measured at the gates (GATES R3), so a proof that stops before the gates has none"},
         "decision": "owner: keep the clause, drop it, or change the margin (a new rule set)"},
    ]


def stage_p2(pf, job, fits_d, start, guides, topology: str = "f1") -> dict:
    """P2: outline (05 / 01, held-out other temple) and creases (A, B <= 0.5 mm, C <= 1.5 mm between 05 and 01). The
    outline is traced on Astra's topology (`topology`: the pyramid builds F1 = the base; before fix round 3 P2 traced
    every topology on the F1 builder, which moved the pyramid's bottom knots by up to 0.13 mm)."""
    fits = _side_fits(fits_d)
    bld = FX.prada_pyramid_spec if topology == "pyramid" else FX.prada_spec
    builder = lambda t, b: bld(**{**start, "top": t, "bottom": b})
    spec0 = bld(**start)
    both = RT.trace_outline(job, spec0, builder, fits, ("05", "01"), log=pf.hb.log)
    res = {"both": {"final": both["final"], "flagged": both["flagged"]}, "held_out": {}}
    flagged = {(f["edge"], f["d"]) for f in both["flagged"]}
    for tr, ho in (("05", "01"), ("01", "05")):
        one = RT.trace_outline(job, spec0, builder, fits, (tr,), log=pf.hb.log)
        model = RV.Model.from_spec(job, builder(one["top"], one["bottom"]), f"traced on {tr}")
        hr = RT.outline_residuals(job, model, ho, fits[ho])
        rec = {}
        for e in ("top", "bottom"):
            A = np.array(hr[e]["samples"]).reshape(-1, 2)
            near = np.zeros(len(A), bool)
            for (fe, fd) in flagged:
                if fe == e:
                    near |= np.abs(A[:, 0] - fd) <= RT.OUTLINE_WINDOW_MM
            keep = A[~near]
            rec[e] = {"n": int(len(A)), "n_outside_flagged": int(len(keep)),
                      "mean_abs_mm": float(np.mean(np.abs(keep[:, 1]))) if len(keep) else None,
                      "mean_abs_all_mm": hr[e]["mean_abs_mm"]}
        res["held_out"][f"{tr}->{ho}"] = rec
    model = RV.Model.from_spec(job, builder(both["top"], both["bottom"]), "traced")
    cr = RT.trace_creases(job, guides, model, fits)
    res["creases"] = cr
    # in-view residual outside the flagged knots (there the knot was taken from the other, cleaner view on purpose)
    inv = {}
    for k in ("05", "01"):
        rr = RT.outline_residuals(job, model, k, fits[k])
        for e in ("top", "bottom"):
            A = np.array(rr[e]["samples"]).reshape(-1, 2)
            near = np.zeros(len(A), bool)
            for (fe, fd) in flagged:
                if fe == e:
                    near |= np.abs(A[:, 0] - fd) <= RT.OUTLINE_WINDOW_MM
            inv[f"{k}_{e}"] = {"mean_abs_mm": float(np.mean(np.abs(A[~near, 1]))) if (~near).any() else None,
                               "mean_abs_all_mm": rr[e]["mean_abs_mm"]}
    res["in_view"] = inv
    inview = max(v["mean_abs_mm"] for v in inv.values() if v["mean_abs_mm"] is not None)
    held = max(v[e]["mean_abs_mm"] for v in res["held_out"].values() for e in ("top", "bottom"))
    corner = {}
    for c in ("A", "B", "C"):
        bv = cr["corners"].get(c, {}).get("by_view", {})
        corner[c] = float(np.hypot(*np.subtract(bv["05"], bv["01"]))) if {"05", "01"} <= set(bv) else None
    res["summary"] = {"in_view_mean_mm": inview, "held_out_mean_mm": held, "corner_05_vs_01_mm": corner}
    ok = (inview <= RR.P2_TRACE["in_view_mean_mm"] and held <= RR.P2_TRACE["held_out_mm"]
          and all(corner[c] is not None and corner[c] <= RR.P2_TRACE["A_B_mm"] for c in ("A", "B"))
          and corner["C"] is not None and corner["C"] <= RR.P2_TRACE["C_mm"])
    res["pass"] = bool(ok)
    res["traced"] = {"top": both["top"], "bottom": both["bottom"],
                     "B": [cr["corners"]["B"]["d"], cr["corners"]["B"]["y"]] if "B" in cr["corners"] else None,
                     "C": [cr["corners"]["C"]["d"], cr["corners"]["C"]["y"]] if "C" in cr["corners"] else None}
    res["guide_lines"] = {k: {cn: (ln["p0"], ln["p1"]) for cn, ln in cr["views"][k]["lines"].items() if ln}
                          for k in cr["views"]}
    # the evidence behind Astra's topology reading, and how far the snapper moved each line from its click (fit-4)
    corners_px = {k: cr["views"][k]["corners_px"] for k in ("05", "01") if k in cr["views"]}
    res["topology_evidence"] = CU.topology_evidence(job, res["guide_lines"], corners_px,
                                                    {k: RC.decal_mask(job, k, fits[k]["camera"]) for k in ("05", "01")})
    res["line_endpoint_moves_px"] = {f"{k[0]} {k[1]} end {k[2]}": v for k, v in
                                     CU.endpoint_sigmas(guides, res["guide_lines"], views=("05", "01", "02")).items()}
    return res


def _compare_cache(fit_res, label):
    """The gates' cameras for a fitted model: the fit's FINAL camera block (relief_fit.fit_temple, FIT_VERSION fit-2 on):
    every view's §6 refit of the final shape from its base camera and the rear family computed on the final shape
    (refused for a fit without that block: its cameras were fitted to an earlier shape)."""
    if not fit_res.get("final_camera_block"):
        raise ValueError(f"{label}: the fit has no final camera block (fit {fit_res.get('fit_version')!r}); its "
                         "cameras were not refit to the graded shape")
    out = {}
    for k, f in fit_res["fits"].items():
        out[k] = {"camera": f["camera"], "theta": f["theta"], "refit": f.get("refit"),
                  "theta_fit": f.get("theta_fit")}
        for field in ("alignment", "initial_refit", "initial_theta_fit"):
            if field in f:
                out[k][field] = f[field]
        if k == "06" and "family" in f:
            out["06_family"] = f["family"]
            out["06"]["alpha"] = f.get("alpha")
    return out


def fit_family(start: dict, p2: dict, topology: str):
    """The family the joint fit starts from: the guides' start with P2's traced outline and corners (B, C; and A for
    the pyramid topology, whose A-B crease runs from the traced A), Astra's topology reading."""
    params = copy.deepcopy(start)
    t = p2.get("traced") or {}
    if t.get("top"):
        params["top"], params["bottom"] = t["top"], t["bottom"]
    for k in ("B", "C"):
        if t.get(k):
            params[k] = tuple(t[k])
    if topology == "pyramid":
        a = ((p2.get("creases") or {}).get("corners") or {}).get("A")
        if a:
            params["A"] = (a["d"], a["y"])
    return RF.prada_family(params, topology=topology)


def cue_endpoints(guides: dict, glines: dict) -> dict:
    """The triangulation's line-endpoint uncertainties: the tracer's own move from each guide click to its snapped
    line (relief_cue.endpoint_sigmas), never below relief_cue.TRI_ENDPOINT_PX."""
    return CU.endpoint_sigmas(guides, glines)


def stage_fit(pf, job, start, p2, quick, topology: str = "f1", guides: dict | None = None) -> dict:
    fam = fit_family(start, p2, topology)
    glines = {k: {cn: tuple(v) for cn, v in d.items()} for k, d in p2.get("guide_lines", {}).items()}
    eps = cue_endpoints(guides or {}, glines)
    retrace = make_retrace(job, pf.hb.log)
    rounds = 2 if quick else RR.FIT_MAX_ROUNDS
    out = {}
    for label, family in (("candidate", fam), ("fitted slab", fam.slab())):
        pf.hb.stage = f"fit {label}"
        prev = pf.out / f"fit_{label.replace(' ', '_')}.pkl"
        prev = pickle.loads(prev.read_bytes()) if prev.exists() else None
        res = RF.fit_temple(job, family, guides_lines=glines, rounds=rounds, rear=True, retrace=retrace,
                            log=pf.hb.log, budget_s=90 * 60, pool=pf.pool, cue_endpoint_px=eps)
        if prev is not None:                       # a resumed run refits: record whether it reproduced the record
            res["reproduced_previous_record"] = bool(prev.get("x") == res["x"] and prev.get("loss") == res["loss"])
            pf.hb.log(f"  {label}: reproduces the previously recorded fit exactly: {res['reproduced_previous_record']}")
        out[label] = res
        (pf.out / f"fit_{label.replace(' ', '_')}.pkl").write_bytes(pickle.dumps(
            {k: v for k, v in res.items() if k not in ("context", "family")}))
        (pf.out / f"spec_{label.replace(' ', '_')}.json").write_text(json.dumps(res["spec"], indent=1),
                                                                     encoding="utf-8")
    cand = out["candidate"]
    ctx = cand["context"]
    # S2 representability: relief-seeing views
    s2 = {}
    stop_s = []
    for k in ("02", "03", "06"):
        f = cand["fits"][k]
        model = RV.Model.from_spec(job, cand["spec"], "candidate")
        r = RV.render_model(job, model, k, f["camera"], f["theta"], points=False)
        m = RV.roi_metrics(job.matte(k), r["mask"], ctx.rois[k], job.hinge_ppm(f["camera"]))
        fc = RV.front_contour_mm(job, k, r, f["camera"])
        lim = RR.STOP_S_RATIO * fc + RR.STOP_S_OFFSET_MM
        s2[k] = {"temple_roi_contour_mm": m["contour_mm"], "front_contour_mm": fc, "limit_mm": lim,
                 "fires": bool(m["contour_mm"] > lim)}
        if s2[k]["fires"]:
            stop_s.append(k)
    pf.hb.stage = "S3 consistency"
    x = np.array([cand["x"][n] for n in cand["family"].names])
    s3 = RF.s3_table(ctx, cand["family"], x, cand["sigma"])
    fb = cand["details"].get("front_bound") or {}
    s3["front_bound_active_mm"] = fb.get("hinge_zone_max_mm", 0.0)
    s3["front_bound_stop"] = bool(s3["front_bound_active_mm"] > RR.STOP_C_FRONT_ACTIVE_MM)
    prof = RF.profiles(ctx, cand["family"], x, [n for n in ("x_in0", "r_C", "rise", "t_zone")
                                                 if n in cand["family"].names], n=7, pool=pf.pool)
    conflicts = RF.conflict_table(prof, cand["sigma"])
    summ = {label: {"x": r["x"], "loss": r["loss"], "sigma": r["sigma"], "sigma_status": r.get("sigma_status"),
                    "sigma_flags": r.get("sigma_flags"), "sigma_penalty": r.get("sigma_penalty"),
                    "penalty_at_fit": r.get("penalty_at_fit"),
                    "history": r["history"], "final_outline_retraced": r.get("final_outline_retraced"),
                    "details": {k: v for k, v in r["details"].items() if k != "front_bound"},
                    "front_bound": r["details"].get("front_bound"),
                    "front_bound_at_own_cameras": r.get("front_bound_at_own_cameras"),
                    "termination": r.get("termination"), "rounds_run": r.get("rounds_run"),
                    "final_camera_block": r.get("final_camera_block"), "rear_family_final": r.get("rear_family_final"),
                    "fit_version": r.get("fit_version"), "seconds": r["seconds"],
                    "over_budget": r.get("over_budget"),
                    "reproduced_previous_record": r.get("reproduced_previous_record"),
                    "unobserved": r.get("unobserved"),
                    "cue": {k: v for k, v in (r.get("cue") or {}).items() if k != "photo_steps"},
                    "cue_at_fit": (r["details"].get("cue") or {}),
                    "hinge_violation_mm": r["details"].get("hinge_violation_mm"),
                    "spec_sha": _spec_sha(r["spec"])} for label, r in out.items()}
    return {"fits": summ, "s2": s2, "stop_s": stop_s, "s3": s3, "profiles": prof, "conflicts": conflicts,
            "topology": topology, "photo_steps": cand.get("cue", {}).get("photo_steps"),
            "cue_endpoint_px": {f"{k[0]} {k[1]} end {k[2]}": v for k, v in eps.items()}, "_results": out}


def stage_p3(pf, spec) -> dict:
    from glasses3d.tests.proofs.wp6_prada import PRADA_SCENE, blender_steps, scene_copy, sha
    out = pf.out / "p3"
    out.mkdir(parents=True, exist_ok=True)
    blend = scene_copy(out, "prada_copy_p3.blend")
    glb = out / "prada_tool_test_faceted.glb"
    st = blender_steps(out, blend, [{"op": "faceted_temple", "spec": spec}, {"op": "check_relief", "photo_creases": 4},
                                    {"op": "export", "path": str(glb)},
                                    {"op": "save_copy", "path": str(out / "prada_tool_test_faceted.blend")}], "p3")
    ft = st[0]
    res = {"tool": {k: ft.get(k) for k in ("status", "code", "message", "warnings", "budget", "geometry", "facets",
                                           "creases", "thickness")},
           "sides": ft.get("sides"), "check_relief": st[1], "export": {k: st[2].get(k) for k in ("status", "error")}}
    if st[2].get("status") == "exported":
        vs = validate_spec(glb)
        res["validate_spec"] = {"ok": vs["ok"]}
        from glasses3d.tests.proofs.wp6_p0_instrument import grade_glb
        g = grade_glb(glb, hinge_z=spec["hinge"]["z_mm"], zone_end=spec["zone_end_d"], outline=spec["outline"],
                      facet_polys={l: [spec["nodes"][n][:2] for n in (f["poly"] if isinstance(f, dict) else f)]
                                   for l, f in spec["facets"].items()})
        res["instrument"] = {"pass": g["pass"], "failed": {s: {k: v for k, v in t["checks"].items() if not v["pass"]}
                                                           for s, t in g["temples"].items()}}
    res["glb"] = str(glb)
    res["spec_sha256"] = _spec_sha(spec)
    res["source_sha256_unchanged"] = sha(PRADA_SCENE) == pf.rec["source_scene"]["sha256_before"]
    warn = [w.get("code") for w in (ft.get("warnings") or [])]
    res["pass"] = (ft.get("status") == "ok" and res.get("validate_spec", {}).get("ok", False)
                   and res["source_sha256_unchanged"])
    res["hinge_warnings"] = [w for w in warn if w in ("W_HINGE_LEAF", "W_KNUCKLE_PENETRATION")]
    return res


def stage_gates(pf, job, fits_d, fit_out, p2, guides, p3=None) -> dict:
    cand_res, slab_res = fit_out["_results"]["candidate"], fit_out["_results"]["fitted slab"]
    models = {"candidate (TOOL TEST)": RV.Model.from_spec(job, cand_res["spec"], "candidate (TOOL TEST)"),
              "fitted slab": RV.Model.from_spec(job, slab_res["spec"], "fitted slab"),
              "delivered": RV.Model.delivered(job)}
    cache = {"candidate (TOOL TEST)": _compare_cache(cand_res, "candidate"),
             "fitted slab": _compare_cache(slab_res, "slab"),
             "delivered": {k: (dict(f) if k != "06" else {kk: vv for kk, vv in f.items() if kk != "family"})
                           for k, f in fits_d.items()}}
    cache["delivered"]["06_family"] = fits_d["06"]["family"]
    rep = RV.compare_temple_views(job, models, "all", out_dir=pf.out / "views", fits=cache,
                                  cross_pair=("candidate (TOOL TEST)", "delivered"), log=pf.hb.log)
    C, S, D = "candidate (TOOL TEST)", "fitted slab", "delivered"
    V = rep["views"]
    g = {}
    # G1 top 03
    v = V["03"]["models"]
    off = [v[C]["envelopes"][s]["hinge"]["outer_signed_median_mm"] for s in ("1", "-1")
           if v[C]["envelopes"][s].get("hinge")]
    g["G1"] = {"roi_iou": (v[C]["roi"]["iou"], v[D]["roi"]["iou"]), "whole_iou": (v[C]["whole_iou"], v[D]["whole_iou"]),
               "hinge_offset_mm": off,
               "pass": bool(v[C]["roi"]["iou"] >= v[D]["roi"]["iou"] and v[C]["whole_iou"] >= v[D]["whole_iou"]
                            and off and max(abs(o) for o in off) <= RR.G1_TOP_OFFSET_MM)}
    # G2 rear 06, every alpha of the family
    fam = V["06"].get("family", {})
    rows_c = fam.get(C, {}).get("rows", [])
    d_cont = V["06"]["models"][D]["roi"]["contour_mm"]
    ok2 = bool(rows_c) and all(r["roi"]["contour_mm"] <= RR.G2_REAR_CONTOUR_RATIO * d_cont for r in rows_c)
    cr = V["06"].get("cross", {})
    ok2 &= bool(cr) and cr[f"{C}@{D}"]["roi"]["contour_mm"] < V["06"]["models"][D]["roi"]["contour_mm"] and \
        cr[f"{D}@{C}"]["roi"]["contour_mm"] > V["06"]["models"][C]["roi"]["contour_mm"]
    env = lambda lab: V["06"]["models"][lab]["envelopes"]
    envc = []
    for s in ("1", "-1"):
        ec, ed = env(C)[s], env(D)[s]
        if ec.get("hinge") and ed.get("hinge"):
            envc.append(ec["hinge"]["outer_abs_median_mm"] <= ed["hinge"]["outer_abs_median_mm"])
        if ec.get("arm") and ed.get("arm"):
            for kk in ("outer_abs_median_mm", "inner_abs_median_mm", "width_abs_median_mm"):
                envc.append(ec["arm"][kk] <= ed["arm"][kk])
    ok2 &= bool(envc) and all(envc)
    g["G2"] = {"family_alphas": fam.get(C, {}).get("alphas"), "contours": [r["roi"]["contour_mm"] for r in rows_c],
               "delivered_contour_mm": d_cont, "cross": {k: c["roi"]["contour_mm"] for k, c in cr.items()},
               "envelopes_ok": envc, "pass": bool(ok2)}
    # G3 sides
    ok3 = True
    g3 = {}
    for k in ("01", "05"):
        vm = V[k]["models"]
        o = vm[C]["outline"]
        g3[k] = {"roi_contour": (vm[C]["roi"]["contour_mm"], vm[D]["roi"]["contour_mm"]),
                 "outline_top_mm": o["top"]["mean_abs_mm"], "outline_bottom_mm": o["bottom"]["mean_abs_mm"]}
        ok3 &= vm[C]["roi"]["contour_mm"] <= vm[D]["roi"]["contour_mm"] and \
            o["top"]["mean_abs_mm"] <= RR.G3_OUTLINE_TOP_MM and o["bottom"]["mean_abs_mm"] <= RR.G3_OUTLINE_BOTTOM_MM
    g["G3"] = {**g3, "pass": bool(ok3)}
    vm = V["04"]["models"]
    g["G4"] = {"roi_contour": (vm[C]["roi"]["contour_mm"], vm[D]["roi"]["contour_mm"]),
               "front_bound": vm[C]["front_bound"],
               "pass": bool(vm[C]["roi"]["contour_mm"] <= vm[D]["roi"]["contour_mm"] + RR.G4_FRONT_CONTOUR_MARGIN_MM
                            and vm[C]["front_bound"]["clear"])}
    vm = V["02"]["models"]
    g["G5"] = {"roi_iou": (vm[C]["roi"]["iou"], vm[D]["roi"]["iou"]),
               "pass": bool(vm[C]["roi"]["iou"] >= vm[D]["roi"]["iou"] - RR.G5_ANGLED_IOU_MARGIN)}
    # R1
    tbc = build_temple(cand_res["spec"])
    geo = report.geometry_numbers(tbc, photo_creases=4)
    g["R1"] = {"share": geo["single_plane_share"], "pass": bool(geo["single_plane_share"] <= RR.R1_SHARE_MAX),
               "vbulge_mm": geo["vbulge_mm"], "pbulge_mm": geo["pbulge_mm"]}
    # R2: crease views must be registered <= 1 mm at the temple (§5); R2 is judged only with BOTH 05 and 01 usable
    # (relief_check.r2_verdict): a view the candidate is misregistered in never shrinks what it is graded on
    segs = {}
    cache_c = cache[C]
    reg = {}
    reports = {"invented_creases": {}, "shading_residual": {}, "crease_crops": {}}   # report only, never gated
    for key, near in (("05", 1), ("01", -1)):
        f = RV.crease_fit(cache_c, key)       # the side view's camera; its theta is 0 (relief_views.SIDE_THETA_WHY)
        r = RV.render_model(job, models[C], key, f["camera"], f["theta"], points=True, normals=True)
        reg[key] = RC.temple_registration_mm(job, key, r, f["camera"], near)
        segs[key] = RC.photo_crease_segments(job, key, r, f["camera"], f["theta"], near,
                                             decal_mask=RC.decal_mask(job, key, f["camera"]),
                                             registration_mm=reg[key]["registration_mm"])
        reports["invented_creases"][key] = RC.invented_creases(job, key, r, near, f["camera"])
        reports["shading_residual"][key] = RC.shading_residual(job, key, r, near, f["camera"])
        crop = pf.out / "views" / f"creases_{key}_candidate_TOOL_TEST.png"
        RC.crease_crop(job, key, r, segs[key], near, crop, label="TOOL TEST candidate")
        reports["crease_crops"][key] = str(crop)
    both = segs["05"]["usable"] and segs["01"]["usable"]
    conf = RC.confirm_creases(segs["05"]["segments"], segs["01"]["segments"]) if both else []
    r2 = RC.r2_verdict(segs, conf, segs["05"]["segments"], required=("05", "01"))
    g["R2"] = {**r2, "registration": reg, "frame": "each side view's refit camera, theta 0 (no hinge angle read)",
               "confirmation": "05 and 01" if both else "not judged: a side view is unusable (R2 fails)",
               "by_crease": r2_by_crease(cand_res["spec"], segs, r2.get("confirmed") or []),
               "pass": bool(r2["pass"])}
    # R3 transfer: candidate, delivered, slab (each at its own 05 / 02 cameras)
    glines = {k: {cn: tuple(v) for cn, v in d.items()} for k, d in p2.get("guide_lines", {}).items()}
    tr, transfer_details = {}, {}
    for lab in (C, S, D):
        f = cache[lab]
        ctx = RF.Context(job, {k: f[k] for k in ("05", "02")}, {}, (), glines)
        transfer_details[lab] = RF.transfer_all(ctx, models[lab])
        tr[lab] = transfer_details[lab]["mean_mm"]
    lim = {"max_mm": RR.R3_TRANSFER_MAX_MM,
           "delivered_minus_margin_mm": None if tr[D] is None else tr[D] - RR.R3_TRANSFER_MARGIN_MM,
           "slab_minus_margin_mm": None if tr[S] is None else tr[S] - RR.R3_TRANSFER_MARGIN_MM}
    binding = min((v for v in lim.values() if v is not None), default=None)
    s1b = _s1_case_b(pf.rec)
    g["R3"] = {"transfer_mm": tr, "transfer_details": transfer_details, "limits": lim, "binding_limit_mm": binding,
               "frame": "05 refit camera (theta 0) -> 02 refit camera and theta (graded by S1 in a common frame)",
               # the comparison the owner's R3 decision needs (open_owner_decisions): what a PERFECT model transfers
               "s1_case_b_true_temple_mm": s1b,
               "true_temple_within_binding_limit": None if (s1b is None or binding is None) else bool(s1b <= binding),
               "open_owner_decision": "R3's 'delivered - 0.5' clause (see the record's open_owner_decisions)",
               "pass": bool(tr[C] is not None and tr[C] <= RR.R3_TRANSFER_MAX_MM and
                            tr[D] is not None and tr[C] <= tr[D] - RR.R3_TRANSFER_MARGIN_MM and
                            tr[S] is not None and tr[C] <= tr[S] - RR.R3_TRANSFER_MARGIN_MM)}
    # R4
    r4 = {k: (V[k]["models"][C]["roi"]["contour_mm"], V[k]["models"][S]["roi"]["contour_mm"]) for k in V}
    g["R4"] = {"contours": r4, "pass": all(c <= s + RR.R4_SLAB_MARGIN_MM for c, s in r4.values())}
    g["V"] = v_gate(p3)
    # the relief sheet for Astra (§5): sections at the photo nodes, fitted values, sigma, front bound, UNOBSERVED
    sheet = pf.out / "views" / "relief_sheet_candidate_TOOL_TEST.png"
    rs = RC.relief_sheet(cand_res["spec"], sheet, x=cand_res["x"], sigma=cand_res["sigma"],
                         sigma_status=cand_res.get("sigma_status"),
                         front_bound=V["04"]["models"][C].get("front_bound"), unobserved=cand_res.get("unobserved"),
                         label="TOOL TEST candidate")
    reports["relief_sheet"] = {"path": str(sheet), "stations": rs["stations"], "sigma_shown": rs["sigma_shown"]}
    return {"gates": g, "views": rep, "reports": reports, "pass": all(v["pass"] for v in g.values()),
            "failed": [k for k, v in g.items() if not v["pass"]]}


R2_NAME_FAR_MM = 1.0              # a segment whose lift lies farther than this from every node line is named "none"
CREASE_NODES = {"AB": ("A", "B"), "AC": ("A", "C"), "BC": ("B", "C")}


def r2_by_crease(spec: dict, segs: dict, confirmed: list) -> dict:
    """R2's photo segments named by the crease of the graded spec they lie on (report only; R2 itself is unchanged):
    each segment's lifted (d, y) points (relief_check.photo_crease_segments' lift_dy, both side views in the +X chart)
    against the spec's A-B / A-C / B-C node lines (chart mm, the median distance; "none" beyond R2_NAME_FAR_MM). The
    confirmed 05 / 01 pairs are grouped by crease; pairs that reuse one 01 segment are merged (one confirmation)."""
    nodes = (spec or {}).get("nodes") or {}
    lines = {cn: (np.asarray(nodes[a][:2], float), np.asarray(nodes[b][:2], float))
             for cn, (a, b) in CREASE_NODES.items() if a in nodes and b in nodes}

    def name(seg):
        if not seg.get("lift_dy") or not lines:
            return None, None
        P = np.asarray(seg["lift_dy"], float)
        best = None
        for cn, (a, b) in lines.items():
            u = b - a
            L = float(np.linalg.norm(u))
            u = u / L
            t_ = np.clip((P - a) @ u, 0.0, L)
            d = float(np.median(np.linalg.norm(P - (a[None] + t_[:, None] * u[None]), axis=1)))
            if best is None or d < best[1]:
                best = (cn, d)
        return (best[0] if best[1] <= R2_NAME_FAR_MM else "none"), best[1]
    views, names = {}, {}
    for key, vs in segs.items():
        rows = []
        for i, s in enumerate(vs.get("segments", [])):
            cn, dist = name(s)
            names[(key, i)] = cn
            rows.append({"segment": i, "crease": cn, "dist_mm": dist, "verdict": s["verdict"], "dn_deg": s["dn_deg"],
                         "len_mm": s["len_mm"]})
        views[key] = {"segments": rows, "creases_seen": sorted({r["crease"] for r in rows if r["crease"]})}
    by = {}
    for c in confirmed:
        cn = names.get(("05", c["seg05"]))
        e = by.setdefault(cn or "unnamed", {"pairs": [], "segments_01": set(), "creases_01": set()})
        e["pairs"].append([c["seg05"], c["seg01"], c.get("dn_deg")])
        e["segments_01"].add(c["seg01"])
        e["creases_01"].add(names.get(("01", c["seg01"])))
    conf = {cn: {"pairs_05_01_dn": e["pairs"], "distinct_01_segments": sorted(e["segments_01"]),
                 "confirmations_after_merging": len(e["segments_01"]),
                 "01_segment_creases": sorted(x for x in e["creases_01"] if x)} for cn, e in by.items()}
    return {"views": views, "confirmed_by_crease": conf,
            "confirmed_creases_tested": sorted(k for k in conf if k not in ("none", "unnamed")),
            "untested_by_confirmation": sorted(k for k in lines if k not in conf),
            "rule": f"nearest node line of the graded spec, median distance of the segment's lift; > {R2_NAME_FAR_MM} mm "
                    "= none"}


def v_gate(p3: dict | None) -> dict:
    """§8 V on the P3 build of the candidate: validate_spec passes (spec.py, which WP1 proved equal to arview's own
    refusals on 143 cases and the real GLBs, so "the arview capture loads with no new refusal" is this check; the
    owner's webcam session comes after the gates) and W_HINGE_LEAF / W_KNUCKLE_PENETRATION are clear."""
    if not p3 or p3.get("error"):
        return {"pass": False, "why": "P3 (the candidate built on a scene copy) has no result to grade",
                "p3_error": (p3 or {}).get("error")}
    vs = (p3.get("validate_spec") or {}).get("ok", False)
    hw = p3.get("hinge_warnings") or []
    return {"validate_spec_ok": bool(vs), "hinge_warnings": hw, "tool_status": (p3.get("tool") or {}).get("status"),
            "pass": bool(vs and not hw and (p3.get("tool") or {}).get("status") == "ok")}


def label_sheet(path: Path, text: str) -> None:
    if not path.exists():
        return
    im = Image.open(path).convert("RGB")
    banner = Image.new("RGB", (im.width, 34), (180, 0, 0))
    ImageDraw.Draw(banner).text((10, 10), text, fill=(255, 255, 255))
    out = Image.new("RGB", (im.width, im.height + 34), (255, 255, 255))
    out.paste(banner, (0, 0))
    out.paste(im, (0, 34))
    out.save(path)


def run_rest(pf, job, fits_d, start, gp, stages, args, p2_done: dict | None = None):
    guides = gp["guides"]
    topology = gp.get("start", {}).get("topology", "f1")
    p2 = {}
    if p2_done is not None:                          # P2 already ran in this run (before S1: wp6_prada._run)
        p2 = p2_done
    elif "p2" in stages:
        p2 = pf.stage("P2", lambda: stage_p2(pf, job, fits_d, start, guides, topology))
        if p2.get("error"):
            return pf.stop("STOP-T", "P2 tracing crashed", p2["error"])
        pf.rec["p2_pass"] = p2.get("pass")
    elif "fit" in stages:
        # the fit starts from P2's traced outline, corners and crease lines: never silently without them
        p2 = pf.rec["stages"].get("P2") or {}
        if not p2.get("guide_lines") or p2.get("error"):
            return pf.stop("STOP-T", "the fit needs P2's traced outline and crease lines and none are on record "
                           "(add p2 to --stages)", p2.get("error"))
        pf.hb.log("P2 not in --stages: the fit starts from the recorded P2 (traced outline, corners, crease lines)")
    if "fit" not in stages:
        return "stopped after P2 (stages)"
    fo = pf.stage("FIT", lambda: stage_fit(pf, job, start, p2, args.quick, topology, guides))
    if fo.get("error"):
        return pf.stop("STOP-S", "the joint fit crashed", fo["error"])
    res_obj = fo.pop("_results", None)
    # §5 / §7 UNOBSERVED (what Astra keeps at its default, §12): the candidate fit's list only (F4 tilt; parameters
    # with a flat objective). S1's failures are not UNOBSERVED parameters: they are recorded under "s1_failures"
    # (wp6_prada.py, with their STOP-I status)
    pf.rec["unobserved"] = {"from_fit": (fo.get("fits", {}).get("candidate") or {}).get("unobserved")}
    pf.save()
    fo["_results"] = res_obj
    # STOP-R holds for ANY model (§9): the candidate's and the fitted slab's rear families too, not only the delivered
    for lab, r in (res_obj or {}).items():
        fam = (r.get("fits", {}).get("06") or {}).get("family")
        if fam and fam.get("stop_r"):
            return pf.stop("STOP-R", f"every alpha of the {lab}'s rear family sits at a pitch bound or its frame guard "
                           "exceeds 2 mm", {"model": lab, "family": {k: fam[k] for k in ("family", "loss_min",
                                                                                        "stop_r")}})
    if fo["stop_s"]:
        return pf.stop("STOP-S", "a relief-seeing view's temple contour exceeds 1.5 x its front contour + 0.3 mm",
                       fo["s2"])
    if fo["s3"]["stop_c"] or fo["s3"]["front_bound_stop"]:
        return pf.stop("STOP-C", "an observable's own optimum lies outside max(1 mm, 2 sigma) of the joint optimum, "
                       "or the joint optimum needs the front bound active", {"s3": fo["s3"], "conflicts": fo["conflicts"]})
    spec_c = res_obj["candidate"]["spec"]
    p3 = pf.rec["stages"].get("P3")                 # a resumed run grades V on the recorded P3 of this spec
    if p3 is not None and p3.get("spec_sha256") != _spec_sha(spec_c):
        p3 = None
    if "p3" in stages:
        p3 = pf.stage("P3", lambda: stage_p3(pf, spec_c))
        if not p3.get("pass"):
            return pf.stop("STOP-T", "P3 build on the scene copy failed", p3)
    if "gates" in stages:
        gt = pf.stage("GATES", lambda: stage_gates(pf, job, fits_d, fo, p2, guides, p3))
        label_sheet(pf.out / "views" / "six_view_sheet.png", "TOOL TEST - WP6 $0 proof on a Prada scene copy; "
                    "not a delivered model")
        if gt.get("error"):
            return pf.stop("STOP-P", "the gate stage crashed", gt["error"])
        if not gt["pass"]:
            return pf.stop("STOP-P", f"gates failed: {gt['failed']}", gt["gates"])
        # P2 has no early stop (diagnostic fits remain useful), but its required
        # tracing proof must pass before this can be a complete WP6 proof.
        if not p2.get("pass"):
            return pf.stop("STOP-P", "P2 tracing proof did not pass", p2)
        return "PASS (owner judges delivered vs candidate in arview next)"
    return "stopped after FIT/P3 (stages)"
