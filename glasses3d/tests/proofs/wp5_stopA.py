"""WP5 STOP A on FT1119 (WP5_DESIGN §8 A0, B1, B2, A7; §9): writes data/wp5/stopA.json and data/wp5/sheets/.

    glasses3d/.venv/Scripts/python -B -m glasses3d.tests.proofs.wp5_stopA [--root data/wp5/stopA] [--no-sheets]

Nothing is fitted here; it reads what the proof drivers wrote under ROOT:
- ``a0/a0.json``: ``tests/proofs/wp5_ft1119.py --a0-only --out ROOT`` (synthetic A0);
- ``ft1119/``: ``wp5_ft1119.py --skip-a0 --out ROOT --name ft1119`` then ``--finalize-only`` (step 0 on the six
  FT1119 photos: registration.json, check_print.json, report.json with B1 / B2 / STOP A);
- ``ft1119_run2/``: the same command again with another worker count (A7 on the real job: byte-identical
  registration.json, check_print.json and sheets);
- ``brow_left_warmth/variants.json``: ``tests/proofs/wp5_brow_left_warmth.py`` (Brow_Left with the warmth objective);
- ``pytest.xml`` (optional): junit of ``pytest tests/photoprint tests/instruments`` (A7's synthetic test is in it).

STOP A is re-derived here from §9's four conditions and must agree with report.json's ``stop_a`` (an assertion).
Sheets (photo | projection | Astra, one row per view, ``sheets/registered/``; regions with a partner that did NOT
register go to ``sheets/not_registered/`` and say so in their header): the projection is rendered at the row's view
from the OTHER views only (never its own photo), through the 'all' registration (every stripe; the bake's refit) on S';
Astra's texture at the same camera on S. Raw colours (the sources are gained to the anchor; no gain to the photo).
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

from glasses3d.g3d_host.photoprint import inputs as IN
from glasses3d.g3d_host.photoprint import report as RP

WP5 = IN.REPO / "glasses3d" / "data" / "wp5"
ROOT = WP5 / "stopA"
SHEETS = WP5 / "sheets"
PANEL_W = 480


def _load(p: Path):
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _s(name: str) -> str:
    return IN.short_name(name)


# ----------------------------------------------------------------------------- sections
def views_section(reg: dict) -> dict:
    """Per view: its kind and the regions it anchors or partners (the plan's x-sign roles)."""
    plan = reg["plan"]
    out = {}
    for n, rec in reg["inputs"]["images"].items():
        out[_s(n)] = {"file": n, "view_kind": rec.get("view_kind"), "sha256": rec.get("sha256"),
                      "anchors": [r["name"] for r in plan["regions"] if r["anchor"] == n],
                      "partner_of": [r["name"] for r in plan["regions"] if n in (r.get("partners") or [])]}
    return out


def six_view_section(reg: dict) -> dict:
    """The six-view correction (PROGRESS 'WP5 resumed'): which views see each temple face at donor angles, how many
    samples they share with the anchor, and which pairs the plan made partners. Sides are x signs (q001's
    Temple_Left is at -x)."""
    rows = {}
    for r in reg["plan"]["regions"]:
        if not r["name"].startswith("Temple"):
            continue
        rows[r["name"]] = {
            "anchor": _s(r["anchor"]) if r["anchor"] else None,
            "partners": [_s(p) for p in r.get("partners") or []],
            "views": {_s(n): {k: v[k] for k in ("seen", "with_anchor", "gate_px") if k in v}
                      for n, v in (r.get("views") or {}).items() if v.get("seen")}}
    pairs = [f"{n}: {v['anchor']}<->{p}" for n, v in rows.items() for p in v["partners"]]
    return {
        "roles": {"03": "wearer's RIGHT side: the -x temple's OUTER face (anchor of Temple_Left[-x]/outer)",
                  "06": "wearer's LEFT side from ~19 deg above: the +x temple's OUTER face (anchor of "
                        "Temple_Right[+x]/outer) and the -x temple's INNER face with 'TOM FORD MADE IN ITALY CE' "
                        "(anchor of Temple_Left[-x]/inner)",
                  "01": "angled from the +x side: sees the +x temple's outer face and the -x temple's inner face "
                        "(partner of both 06 anchors)"},
        "anchors_by_x_sign": {n: v["anchor"] for n, v in rows.items()},
        "temple_pairs": pairs,
        "new_pairs_from_the_correction": [],
        "why_no_03_pair": ("03 alone sees the -x temple's outer face at donor angles; the next views (02, 04) see it on "
                           f"{_seen(rows, 'Temple_Left[-x]/outer')} samples, below the plan's partner need "
                           "(max(500, 10 % of the region's samples) shared with the anchor), and 01 / 06 see that "
                           "temple's INNER face, not its outer one: no second photo shows the same print, so the face "
                           "is single-view (B3 only)"),
        "regions": rows}


def _seen(rows, name):
    v = rows.get(name, {}).get("views", {})
    return ", ".join(f"{k}: {x.get('with_anchor', x.get('seen'))}" for k, x in v.items() if k != rows[name]["anchor"])


def a0_section(a0: dict | None) -> dict:
    if a0 is None:
        return {"present": False, "pass": None}
    rec = a0.get("recovery", {})
    return {"present": True, "pass": bool(a0["pass"]), "criteria": a0["criteria"], "verdict_line": a0["verdict_line"],
            "recovery": {"camera_01_vs_truth": rec.get("camera_01_vs_truth"),
                         "camera_01_start_vs_truth": rec.get("camera_01_start_vs_truth"),
                         "parallax": rec.get("parallax")},
            "residuals_over_0.2mm": [r for r in a0.get("residuals", []) if not r["pass"]],
            "unperturbed": {k: a0["unperturbed"][k] for k in ("bp_1_4px_r", "null_mean_abs", "null_abs_of_mean",
                                                               "pixels")},
            "blurred": a0.get("blurred"),
            "gates": {k: {"registered": g.get("registered"), "significant": g.get("significant"),
                          "reason": g.get("reason"), "residual_mm": g.get("residual_mm"), "decoys": g.get("decoys"),
                          "peak": ((g.get("signals") or {}).get("warmth") or {}).get("peak")}
                      for k, g in a0.get("gates", {}).items()},
            "gates_floor_only": a0.get("gates_floor_only"),
            "waiver_candidates": a0.get("waiver_candidates"),
            "fit_converged": a0.get("fit", {}).get("converged"), "seconds": a0.get("seconds")}


def _gate_row(g: dict) -> dict:
    w = (g.get("signals") or {}).get("warmth") or {}
    lum = (g.get("signals") or {}).get("lum") or {}
    return {"pair": f"{_s(g['source'])}->{_s(g['target'])}", "eval_fold": g["fold"], "fit_fold": g.get("fit_fold"),
            "E_px": g.get("E_px"), "E_px_read_by_fit": g.get("E_px_read_by_fit"),
            "warmth_peak": w.get("peak"), "decoy_max": w.get("decoy_max"), "decoys": g.get("decoys"),
            "decoy_overlaps": [round(r["overlap_at_offset"], 3) for r in (g.get("decoy_rows") or [])],
            "irange_agrees": g.get("irange_agrees"), "lum_peak": lum.get("peak"),
            "lum_residual_mm": lum.get("residual_mm"),
            "significant": g.get("significant"), "reason": g.get("reason"), "residual_mm": g.get("residual_mm"),
            "registered": g.get("registered"),
            "floor_only_registered": bool(w.get("peak") is not None and w["peak"] >= 0.45 and g.get("irange_agrees")
                                          and g.get("residual_mm") is not None and g["residual_mm"] <= 0.8)}


def region_gates(gates: list[dict], plan_regions: list[dict]) -> dict:
    """Per parallax region and partner: the four fold-protocol rows (both directions, both folds), whether all four
    register, the residuals, and the 'all'-fit rows (every pixel; reported only)."""
    out = {}
    for r in plan_regions:
        if not r["parallax"]:
            continue
        ent = {"anchor": _s(r["anchor"]), "partners": {}}
        for p in r["partners"]:
            rows = [g for g in gates if g["rid"] == r["rid"] and g.get("partner") == p]
            fold_rows = [_gate_row(g) for g in rows if g.get("fit_fold") in ("A", "B")]
            all_rows = [_gate_row(g) for g in rows if g.get("fit_fold") == "all"]
            res = [x["residual_mm"] for x in fold_rows if x["residual_mm"] is not None]
            ent["partners"][_s(p)] = {
                "registered_both_directions_both_folds": bool(len(fold_rows) == 4
                                                              and all(x["registered"] for x in fold_rows)),
                "rows_registered": sum(1 for x in fold_rows if x["registered"]),
                "rows_significant": sum(1 for x in fold_rows if x["significant"]),
                "residual_mm_rows": res, "residual_mm_max": max(res) if res else None,
                "floor_only_rows_registered": sum(1 for x in fold_rows if x["floor_only_registered"]),
                "rows": fold_rows, "all_fit_rows": all_rows}
        out[r["name"]] = ent
    return out


def guards_section(reg: dict) -> dict:
    out = {}
    for f, fit in reg["fits"].items():
        out[f] = {"rows": fit["guards"], "broken": [g for g in fit["guards"] if not g["within"]],
                  "view01_whole": next((g for g in fit["guards"] if g["view"] == "reference-01.png"
                                        and g["guard"] == "whole"), None),
                  "hinges": fit["pose"]["hinges"], "camera_01": fit["pose"]["cameras"]["reference-01.png"]
                  ["delta_from_start"], "converged": fit.get("converged"), "loss_final": fit.get("loss_final")}
    return out


def _crit(c: dict | None) -> dict | None:
    if not c:
        return None
    k = c
    return {"C1": {"dE00_gain_P": k["C1"]["projection"], "dE00_gain_A": k["C1"]["astra"], "margin": k["C1"]["margin"],
                   "ci95_hi": k["C1"]["ci95_hi"], "pass": k["C1"]["pass"]},
            "C2": {"bp_P": k["C2"]["projection"], "bp_A": k["C2"]["astra"], "null_mean": k["C2"].get("null_mean"),
                   "null_std": k["C2"].get("null_std"), "need": k["C2"].get("need"), "margin": k["C2"].get("margin"),
                   "pass": k["C2"]["pass"], **({"reason": k["C2"]["reason"]} if k["C2"].get("reason") else {})},
            "C3": {"energy_0.4_1.6mm": k["C3"]["energy_0.4_1.6mm"], "energy_1_4px": k["C3"]["energy_1_4px"],
                   "pass": k["C3"]["pass"]},
            "C4": {"contamination": k["C4"]["contamination"], "pass": k["C4"]["pass"]}}


def check_rows(rows: list[dict]) -> list[dict]:
    return [{"region": c["region"], "held_out": _s(c["held_out"]), "sources": [_s(w) for w in c["sources"]],
             "eval_fold": c["eval_fold"], "fit_fold": c.get("fit_fold"), "pixels": c["pixels"],
             "graded": bool(c.get("b2_eligible")), "b2_pass": bool(c.get("b2_pass")),
             "criteria": _crit(c.get("criteria")),
             "bp_P": (c.get("projection") or {}).get("bp_1_4px_r"), "bp_A": (c.get("astra") or {}).get("bp_1_4px_r"),
             "b3_pass": c.get("b3_pass"), "sheet": c.get("sheet")} for c in rows]


def variants_section(var: dict | None, main_gates: dict) -> dict:
    if var is None:
        return {"present": False}
    tgt = var["target"]
    out = {"present": True, "target": tgt,
           "question": "WP5_DESIGN §0 (b): does the chroma (warmth) objective register Brow_Left's front?",
           "design_objective_run": {"signals": ["warmth", "lum"], "plan": "joint (step 0)",
                                    "gates": main_gates.get(tgt)},
           "variants": {}}
    for k, v in var["variants"].items():
        gates = region_gates(v["gates"], [r for r in var["plan"]["regions"] if r["name"] in v["parallax_regions"]
                                          and r["parallax"]])
        fits = {}
        for f, fit in v["fits"].items():
            par = fit["pose"]["parallax"].get(tgt)
            fits[f] = {"loss_final": fit.get("loss_final"), "converged": fit.get("converged"),
                       "brow_left_parallax": par, "camera_01": fit["pose"]["cameras"]["reference-01.png"]
                       ["delta_from_start"],
                       "guards_broken": [g for g in fit["guards"] if not g["within"]],
                       "view01_whole": next((g for g in fit["guards"] if g["view"] == "reference-01.png"
                                             and g["guard"] == "whole"), None)}
        out["variants"][k] = {"signals": v["signals"], "single_region": v["single_region"],
                              "parallax_regions": v["parallax_regions"], "fits": fits,
                              "brow_left_gates": gates.get(tgt), "registered": v["registered"],
                              "other_region_gates": {n: g for n, g in gates.items() if n != tgt},
                              "check_rows": check_rows(v.get("check_rows") or []),
                              "diagnostic_rows": check_rows(v.get("diagnostic_rows") or []),
                              "seconds": v.get("seconds")}
    reg_any = {k: len(v["registered"].get(tgt) or []) >= 2 for k, v in var["variants"].items()}
    out["brow_left_registered_by"] = reg_any
    return out


def a7_section(root: Path, junit: Path | None) -> dict:
    r1, r2 = root / "ft1119", root / "ft1119_run2"
    out = {"criterion": "two runs give identical registration.json and check_print.json (no PNG bake yet at stage 2; "
                        "the check sheets are compared instead)"}
    files = {}
    for name in ("registration.json", "check_print.json"):
        a, b = r1 / name, r2 / name
        files[name] = {"run1": _sha(a) if a.is_file() else None, "run2": _sha(b) if b.is_file() else None}
        files[name]["identical"] = bool(files[name]["run1"] and files[name]["run1"] == files[name]["run2"])
    sheets = {}
    for sub in ("sheets", "sheets_diagnostic"):
        s1 = {p.name: _sha(p) for p in sorted((r1 / sub).glob("*.png"))}
        s2 = {p.name: _sha(p) for p in sorted((r2 / sub).glob("*.png"))}
        sheets[sub] = {"files": len(s1), "identical": s1 == s2}
    prev = WP5 / "pre_stopA" / "ft1119" / "registration.json"
    out["ft1119"] = {"files": files, "sheets": sheets,
                     "workers": {"run1": 7, "run2": 6},
                     "pass": bool(all(v["identical"] for v in files.values())
                                  and all(v["identical"] for v in sheets.values())),
                     "previous_run_2026-10-05": {"registration_sha256": _sha(prev) if prev.is_file() else None,
                                                 "same_as_run1": bool(prev.is_file() and files["registration.json"]
                                                                      ["run1"] == _sha(prev))}}
    if junit is not None and junit.is_file():
        t = ET.parse(junit).getroot()
        cases = {}
        for tc in t.iter("testcase"):
            name = f"{tc.get('classname')}::{tc.get('name')}"
            st = "passed"
            for child in tc:
                if child.tag in ("failure", "error"):
                    st = child.tag
                elif child.tag == "skipped":
                    st = "xfailed" if "xfail" in (child.get("type", "") + child.get("message", "")) else "skipped"
            cases[name] = st
        det = {k: v for k, v in cases.items() if "test_determinism" in k}
        out["synthetic_test"] = {"cases": det, "pass": bool(det) and all(v == "passed" for v in det.values())}
        out["suite"] = {s: sum(1 for v in cases.values() if v == s) for s in sorted(set(cases.values()))}
    out["pass"] = bool(out["ft1119"]["pass"] and (out.get("synthetic_test") or {}).get("pass", True))
    return out


def stop_a(a0: dict, rep: dict, b1_gates: dict, guards: dict) -> dict:
    """§9: STOP A fires if A0 fails, if no brow front registers in both folds, if a registered region fails C1-C4 in
    either fold, or if a guard must be broken (including view 01's whole silhouette with the hinge)."""
    statuses = rep["regions"]
    fronts = [n for n in statuses if n.startswith("Brow") and n.endswith("/front")]
    reasons = []
    if not a0.get("pass"):
        reasons.append({"condition": "A0 fails", "fired": True, "detail": a0.get("verdict_line")})
    else:
        reasons.append({"condition": "A0 fails", "fired": False})
    reg_fronts = [n for n in fronts if statuses[n]["status"] == "registered"]
    gate_fronts = [n for n in fronts if len(statuses[n].get("registered_views") or []) >= 2]
    reasons.append({"condition": "no brow front registers in both folds", "fired": not reg_fronts,
                    "detail": {n: {p: {"rows_registered": f"{v['rows_registered']}/4",
                                       "residual_mm_rows": [round(x, 3) for x in v["residual_mm_rows"]],
                                       "reasons": [x["reason"] for x in v["rows"]]}
                                   for p, v in b1_gates.get(n, {}).get("partners", {}).items()} for n in fronts},
                    "gate_registered": gate_fronts, "check_registered": reg_fronts})
    failed = {n: st.get("check_failed_rows") for n, st in statuses.items() if st["status"] == "check_failed"}
    reasons.append({"condition": "a registered region fails C1-C4 in either fold", "fired": bool(failed),
                    "detail": failed if failed else "no region failed check_print"
                    + ("" if gate_fronts or any(len(st.get("registered_views") or []) >= 2 for st in statuses.values())
                       else " (none was registered by the gate, so none reached it)")})
    broken = {f: [f"{_s(g['view'])} {g['guard']}{(' ' + g['group']) if g.get('group') else ''} drop {g['drop']:.5f}"
                  for g in v["broken"]] for f, v in guards.items()}
    broken = {f: v for f, v in broken.items() if v}
    reasons.append({"condition": "a guard must be broken (incl. view 01's whole silhouette with the hinge)",
                    "fired": bool(broken), "detail": broken or None,
                    "view01_whole_drop": {f: v["view01_whole"]["drop"] for f, v in guards.items()
                                          if v["view01_whole"]}})
    fired = any(r["fired"] for r in reasons)
    return {"fired": fired, "conditions": reasons}


# ----------------------------------------------------------------------------- sheets
def make_sheets(root: Path, reg: dict, rep: dict, chk: dict, out_dir: Path, *, var: dict | None = None) -> list[dict]:
    from PIL import Image, ImageDraw

    from glasses3d.g3d_host import raster
    from glasses3d.g3d_host.photoprint import regions as RG
    from glasses3d.g3d_host.photoprint import register as REG
    from glasses3d.g3d_host.photoprint.check_print import astra_render, direct_bake
    from glasses3d.g3d_host.photoprint.pose import Deformer

    job = IN.load_job(IN.FT1119["cameras"])
    groups = RG.rigid_groups(job)
    regions = RG.build_regions(job, IN.FT1119_PARTS, groups, n_samples=reg["n_samples"], seed=reg["seed"])
    by_name = {r.name: r for r in regions}
    plan = {r["name"]: r for r in reg["plan"]["regions"]}
    axes = {by_name[n].rid: raster.camera_basis(job.views[p["anchor"]].camera)[2]
            for n, p in plan.items() if p["parallax"]}
    deformer = Deformer(job, regions, groups, axes)
    pose = REG.pose_from_json(job, reg["fits"]["all"]["pose"])
    index = []
    for sub in ("registered", "not_registered"):
        (out_dir / sub).mkdir(parents=True, exist_ok=True)
    for n, p in plan.items():
        if not p["parallax"]:
            continue
        st = rep["regions"][n]
        status = st["status"]
        regd = status == "registered"
        views = list(st.get("registered_views") or []) if regd else [p["anchor"]] + list(p["partners"])
        region = by_name[n]
        rows, labels = [], []
        for h in views:
            sources = [w for w in views if w != h]
            P = direct_bake(job, deformer, pose, pose, region, h, sources, ref=p["anchor"])
            A = astra_render(job, deformer, pose, region, h)
            photo = job.views[h].image.astype(np.float64)
            ys, xs = np.nonzero(P["sel"] | A["sel"])
            pad = 12
            y0, y1 = max(ys.min() - pad, 0), min(ys.max() + pad + 1, photo.shape[0])
            x0, x1 = max(xs.min() - pad, 0), min(xs.max() + pad + 1, photo.shape[1])
            proj = photo.copy()
            proj[P["sel"]] = (128, 128, 128)                    # region pixels no source projects onto
            proj[P["projected"]] = P["ren"][P["projected"]]
            ast = photo.copy()
            ast[A["sel"]] = A["ren"][A["sel"]]
            panels = [np.clip(x[y0:y1, x0:x1], 0, 255).astype(np.uint8) for x in (photo, proj, ast)]
            ims = []
            for a in panels:
                im = Image.fromarray(a)
                k = PANEL_W / im.size[0]
                im = im.resize((PANEL_W, max(1, int(round(im.size[1] * k)))),
                               Image.NEAREST if k >= 1 else Image.LANCZOS)
                ims.append(im)
            row = Image.new("RGB", (PANEL_W * 3 + 8, ims[0].size[1]), (255, 255, 255))
            for i, im in enumerate(ims):
                row.paste(im, (i * (PANEL_W + 4), 0))
            rows.append(row)
            share = float(P["projected"].sum() / max(P["sel"].sum(), 1))
            labels.append(f"view {_s(h)}: photo | projection from {', '.join(_s(w) for w in sources)} only "
                          f"(projected {share:.0%} of the region's pixels; grey = no source) | Astra")
        head = [f"{n}  status: {status.upper()}" + ("" if regd else "  (NOT REGISTERED: diagnostic, never gating)"),
                "pose: registration fit 'all' (every stripe) on S'; raw colours (sources gained to the anchor "
                f"{_s(p['anchor'])}); Astra on S at the same camera"]
        gates = region_gates(reg["gates"], [p]).get(n, {}).get("partners", {})
        for q, g in gates.items():
            head.append(f"gate {_s(p['anchor'])}<->{q}: {g['rows_registered']}/4 rows registered; residual mm "
                        + ", ".join(f"{x['pair']} {x['eval_fold']}: "
                                    + (f"{x['residual_mm']:.2f}" if x["residual_mm"] is not None else "-")
                                    + f" ({x['reason'] or 'ok'})" for x in g["rows"]))
        lh = 13
        H = lh * len(head) + 6 + sum(r.size[1] + lh + 6 for r in rows)
        sheet = Image.new("RGB", (PANEL_W * 3 + 8, H), (255, 255, 255))
        dr = ImageDraw.Draw(sheet)
        y = 3
        for t in head:
            dr.text((4, y), t, fill=(0, 0, 0))
            y += lh
        y += 3
        for lab, r in zip(labels, rows):
            dr.text((4, y), lab, fill=(0, 0, 0))
            y += lh + 2
            sheet.paste(r, (0, y))
            y += r.size[1] + 4
        stem = n.replace("/", "_").replace("[", "").replace("]", "")
        path = out_dir / ("registered" if regd else "not_registered") / f"{stem}.png"
        sheet.save(path)
        index.append({"region": n, "status": status, "registered": regd, "views": [_s(v) for v in views],
                      "sheet": str(path.relative_to(out_dir)).replace("\\", "/")})
    return index


# ----------------------------------------------------------------------------- main
def build(root: Path = ROOT, *, sheets: bool = True, junit: Path | None = None, out_json: Path | None = None,
          sheets_dir: Path | None = None) -> dict:
    out_json = Path(out_json) if out_json is not None else WP5 / "stopA.json"
    sheets_dir = Path(sheets_dir) if sheets_dir is not None else SHEETS
    a0 = _load(root / "a0" / "a0.json")
    reg = _load(root / "ft1119" / "registration.json")
    chk = _load(root / "ft1119" / "check_print.json")
    rep = _load(root / "ft1119" / "report.json")
    var = _load(root / "brow_left_warmth" / "variants.json")
    if reg is None or chk is None or rep is None:
        raise FileNotFoundError(f"{root / 'ft1119'}: registration/check_print/report.json missing")
    if rep.get("A0_passed") is None and a0 is not None:
        raise RuntimeError("report.json has no A0 verdict: run wp5_ft1119 --finalize-only --out ROOT first")
    gates = region_gates(reg["gates"], reg["plan"]["regions"])
    guards = guards_section(reg)
    a0s = a0_section(a0)
    stop = stop_a(a0s, rep, gates, guards)
    if bool(stop["fired"]) != bool(rep["stop_a"]["fired"]):
        raise AssertionError(f"STOP A disagrees: §9 re-derivation {stop['fired']} vs report.json "
                             f"{rep['stop_a']['fired']}")
    b1 = {"pass": rep["B1"]["pass"], "brow_fronts_gate_registered": rep["B1"]["brow_fronts_gate_registered"],
          "regions": gates, "guards": guards,
          "parallax": {"per_fit_max_abs_mm": rep["B1"]["parallax_max_abs_mm_every_region"],
                       "per_fit_ab_mm": rep.get("parallax"), "within_4mm_where_registered":
                       rep["B1"]["parallax_within_4mm"], "over_4mm": rep["B1"]["parallax_over_4mm"],
                       "stability": rep["B1"]["parallax_stability"]},
          "gate_stability": rep.get("gate_stability"),
          "statuses": {n: {"status": s["status"], "anchor": _s(s["anchor"]) if s.get("anchor") else None,
                           "partners": [_s(p) for p in s.get("partners") or []]} for n, s in rep["regions"].items()},
          "pixel_verifiable": rep.get("pixel_verifiable")}
    b2 = {"pass": rep["B2"]["pass"], "graded": rep["B2"]["graded"], "passed": rep["B2"]["passed"],
          "rows": check_rows(chk["rows"]),
          "b0_rows": check_rows(chk.get("b0_rows") or []),
          "diagnostic_rows_never_gating": check_rows(chk.get("diagnostic_rows") or []),
          "note": ("B2 grades every registered region x registered held-out view x fold (>= 1000 E px). Rows under "
                   "'diagnostic_rows_never_gating' are the regions the gate did not register (anchor + fit partners): "
                   "what the pixels say where registration fell short; they decide nothing.")}
    out = {"written_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "what": "WP5 STOP A measurements on FT1119 with all six views (WP5_DESIGN §8 A0, B1, B2, A7; §9 STOP A)",
           "inputs": {"glb": reg["inputs"]["glb"], "glb_sha256": reg["inputs"]["glb_sha256"],
                      "cameras_sha256": reg["inputs"]["cameras_sha256"],
                      "photos": {_s(n): v["sha256"] for n, v in reg["inputs"]["images"].items()},
                      "scipy": rep.get("scipy"), "numpy": rep.get("numpy"), "python": rep.get("python")},
           "settings": {"print_reg_mm": reg["print_reg_mm"], "min_decoys": reg["min_decoys"],
                        "n_samples": reg["n_samples"], "maxfev_per_powell_run": reg["maxfev"], "seed": reg["seed"],
                        "plan": {k: reg["plan"][k] for k in ("views", "fixed_views", "free_cameras", "free_hinges",
                                                              "gauge", "fit_fixed_partners")}},
           "views": views_section(reg), "six_view_correction": six_view_section(reg),
           "A0": a0s, "B1": b1, "B2": b2,
           "brow_left_chroma": variants_section(var, gates),
           "A7": a7_section(root, junit),
           "stop_a": stop, "report_stop_a": rep["stop_a"],
           "status": rep["status"], "status_area_mm2": rep.get("status_area_mm2"),
           "warnings": rep.get("warnings"), "timings_s": rep.get("timings"),
           "run_dirs": {"ft1119": str(root / "ft1119"), "ft1119_run2": str(root / "ft1119_run2"),
                        "a0": str(root / "a0"), "brow_left_warmth": str(root / "brow_left_warmth")}}
    if sheets:
        out["sheets"] = make_sheets(root, reg, rep, chk, sheets_dir, var=var)
        RP.write_json(sheets_dir / "index.json", out["sheets"])
    RP.write_json(out_json, out)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--no-sheets", action="store_true")
    ap.add_argument("--junit", default=str(ROOT / "pytest.xml"))
    ap.add_argument("--out-json", default=str(WP5 / "stopA.json"))
    ap.add_argument("--sheets-dir", default=str(SHEETS))
    a = ap.parse_args(argv)
    out = build(Path(a.root), sheets=not a.no_sheets, junit=Path(a.junit) if a.junit else None,
                out_json=Path(a.out_json), sheets_dir=Path(a.sheets_dir))
    print(json.dumps({"stop_a_fired": out["stop_a"]["fired"],
                      "conditions": [(c["condition"], c["fired"]) for c in out["stop_a"]["conditions"]],
                      "A7": out["A7"]["pass"]}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
