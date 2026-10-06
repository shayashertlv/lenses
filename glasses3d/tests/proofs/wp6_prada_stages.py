"""WP6 $0 proof, the stages after S1 (tests/proofs/wp6_prada.py runs them in the §9 order): P2 tracing, the joint fit
of the candidate and of the fitted slab with S2 (STOP-S) and S3 (STOP-C), P3 build on a scene copy, the gates (G1-G5,
R1-R4, V; STOP-P) and the six-view sheet. Every output is a TOOL TEST."""
from __future__ import annotations

import copy
import json
import math
import pickle
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from glasses3d.g3d.temple import build_temple, report
from glasses3d.g3d.temple import fixtures as FX
from glasses3d.g3d_host import relief_check as RC
from glasses3d.g3d_host import relief_fit as RF
from glasses3d.g3d_host import relief_rules as RR
from glasses3d.g3d_host import relief_trace as RT
from glasses3d.g3d_host import relief_views as RV
from glasses3d.g3d_host.camera import Camera
from glasses3d.g3d_host.spec import validate_spec


def _side_fits(fits):
    return {k: {"camera": f["camera"], "theta": f["theta"]} for k, f in fits.items()}


def stage_p2(pf, job, fits_d, start, guides) -> dict:
    """P2: outline (05 / 01, held-out other temple) and creases (A, B <= 0.5 mm, C <= 1.5 mm between 05 and 01)."""
    fits = _side_fits(fits_d)
    builder = lambda t, b: FX.prada_spec(**{**start, "top": t, "bottom": b})
    spec0 = FX.prada_spec(**start)
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
    return res


def _compare_cache(fit_res, label):
    out = {}
    for k, f in fit_res["fits"].items():
        out[k] = {"camera": f["camera"], "theta": f["theta"], "refit": f.get("refit"),
                  "theta_fit": f.get("theta_fit")}
        if k == "06" and "family" in f:
            out["06_family"] = f["family"]
            out["06"]["alpha"] = f.get("alpha")
    return out


def stage_fit(pf, job, start, p2, quick) -> dict:
    params = copy.deepcopy(start)
    t = p2.get("traced") or {}
    if t.get("top"):
        params["top"], params["bottom"] = t["top"], t["bottom"]
    for k in ("B", "C"):
        if t.get(k):
            params[k] = tuple(t[k])
    fam = RF.prada_family(params)
    glines = {k: {cn: tuple(v) for cn, v in d.items()} for k, d in p2.get("guide_lines", {}).items()}

    def retrace(family, x, fits):
        kw = family.kwargs(x)
        builder = lambda tt, bb: (family.builder(**{**kw, "top": tt, "bottom": bb}) if not family.slab_mode
                                  else RF.slab_spec(**{**kw, "top": tt, "bottom": bb}))
        ol = RT.trace_outline(job, family.spec(x), builder, _side_fits({k: fits[k] for k in ("05", "01")}),
                              ("05", "01"), iters=2, log=pf.hb.log)
        fam2 = copy.deepcopy(family)
        fam2.base["top"], fam2.base["bottom"] = ol["top"], ol["bottom"]
        return fam2
    rounds = 2 if quick else RR.FIT_MAX_ROUNDS
    out = {}
    for label, family in (("candidate", fam), ("fitted slab", fam.slab())):
        pf.hb.stage = f"fit {label}"
        res = RF.fit_temple(job, family, guides_lines=glines, rounds=rounds, rear=True, retrace=retrace,
                            log=pf.hb.log, budget_s=90 * 60)
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
    prof = RF.profiles(ctx, cand["family"], x, [n for n in ("x_in0", "r_C", "rise") if n in cand["family"].names],
                       n=7)
    conflicts = RF.conflict_table(prof, cand["sigma"])
    summ = {label: {"x": r["x"], "loss": r["loss"], "sigma": r["sigma"], "history": r["history"],
                    "details": {k: v for k, v in r["details"].items() if k != "front_bound"},
                    "front_bound": r["details"].get("front_bound"), "seconds": r["seconds"],
                    "spec_sha": None} for label, r in out.items()}
    return {"fits": summ, "s2": s2, "stop_s": stop_s, "s3": s3, "profiles": prof, "conflicts": conflicts,
            "_results": out}


def stage_p3(pf, spec) -> dict:
    from glasses3d.tests.proofs.wp6_prada import PRADA_SCENE, LEFT, RIGHT, blender_steps, scene_copy, sha
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
    res["source_sha256_unchanged"] = sha(PRADA_SCENE) == pf.rec["source_scene"]["sha256_before"]
    warn = [w.get("code") for w in (ft.get("warnings") or [])]
    res["pass"] = (ft.get("status") == "ok" and res.get("validate_spec", {}).get("ok", False)
                   and res["source_sha256_unchanged"])
    res["hinge_warnings"] = [w for w in warn if w in ("W_HINGE_LEAF", "W_KNUCKLE_PENETRATION")]
    return res


def stage_gates(pf, job, fits_d, fit_out, p2, guides) -> dict:
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
    # R2
    segs = {}
    cache_c = cache[C]
    for key, near in (("05", 1), ("01", -1)):
        f = cache_c[key]
        r = RV.render_model(job, models[C], key, f["camera"], f["theta"], points=True, normals=True)
        segs[key] = RC.photo_crease_segments(job, key, r, f["camera"], f["theta"], near,
                                             decal_mask=RC.decal_mask(job, key, f["camera"]))
    conf = RC.confirm_creases(segs["05"]["segments"], segs["01"]["segments"])
    r2 = RC.r2_verdict(segs, conf, segs["05"]["segments"])
    g["R2"] = {**r2, "pass": bool(r2["pass"])}
    # R3 transfer: candidate, delivered, slab (each at its own 05 / 02 cameras)
    glines = {k: {cn: tuple(v) for cn, v in d.items()} for k, d in p2.get("guide_lines", {}).items()}
    tr = {}
    for lab in (C, S, D):
        f = cache[lab]
        ctx = RF.Context(job, {k: f[k] for k in ("05", "02")}, {}, (), glines)
        tr[lab] = RF.transfer_all(ctx, models[lab])["mean_mm"]
    g["R3"] = {"transfer_mm": tr, "pass": bool(tr[C] is not None and tr[C] <= RR.R3_TRANSFER_MAX_MM and
                                               tr[D] is not None and tr[C] <= tr[D] - RR.R3_TRANSFER_MARGIN_MM and
                                               tr[S] is not None and tr[C] <= tr[S] - RR.R3_TRANSFER_MARGIN_MM)}
    # R4
    r4 = {k: (V[k]["models"][C]["roi"]["contour_mm"], V[k]["models"][S]["roi"]["contour_mm"]) for k in V}
    g["R4"] = {"contours": r4, "pass": all(c <= s + RR.R4_SLAB_MARGIN_MM for c, s in r4.values())}
    return {"gates": g, "views": rep, "pass": all(v["pass"] for v in g.values()),
            "failed": [k for k, v in g.items() if not v["pass"]]}


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


def run_rest(pf, job, fits_d, start, gp, stages, args):
    guides = gp["guides"]
    p2 = {}
    if "p2" in stages:
        p2 = pf.stage("P2", lambda: stage_p2(pf, job, fits_d, start, guides))
        if p2.get("error"):
            return pf.stop("STOP-T", "P2 tracing crashed", p2["error"])
        pf.rec["p2_pass"] = p2.get("pass")
    if "fit" not in stages:
        return "stopped after P2 (stages)"
    fo = pf.stage("FIT", lambda: stage_fit(pf, job, start, p2, args.quick))
    if fo.get("error"):
        return pf.stop("STOP-S", "the joint fit crashed", fo["error"])
    res_obj = fo.pop("_results", None)
    pf.save()
    fo["_results"] = res_obj
    if fo["stop_s"]:
        return pf.stop("STOP-S", "a relief-seeing view's temple contour exceeds 1.5 x its front contour + 0.3 mm",
                       fo["s2"])
    if fo["s3"]["stop_c"] or fo["s3"]["front_bound_stop"]:
        return pf.stop("STOP-C", "an observable's own optimum lies outside max(1 mm, 2 sigma) of the joint optimum, "
                       "or the joint optimum needs the front bound active", {"s3": fo["s3"], "conflicts": fo["conflicts"]})
    spec_c = res_obj["candidate"]["spec"]
    if "p3" in stages:
        p3 = pf.stage("P3", lambda: stage_p3(pf, spec_c))
        if not p3.get("pass"):
            return pf.stop("STOP-T", "P3 build on the scene copy failed", p3)
    if "gates" in stages:
        gt = pf.stage("GATES", lambda: stage_gates(pf, job, fits_d, fo, p2, guides))
        label_sheet(pf.out / "views" / "six_view_sheet.png", "TOOL TEST - WP6 $0 proof on a Prada scene copy; "
                    "not a delivered model")
        if gt.get("error"):
            return pf.stop("STOP-P", "the gate stage crashed", gt["error"])
        if not gt["pass"]:
            return pf.stop("STOP-P", f"gates failed: {gt['failed']}", gt["gates"])
        return "PASS (owner judges delivered vs candidate in arview next)"
    return "stopped after FIT/P3 (stages)"
