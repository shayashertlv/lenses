"""Region statuses, STOP A and the JSON outputs of WP5 step 0 (WP5_DESIGN §4 outputs, §9 statuses and stop logic).

- ``registered``: >= 2 registered views (the gate passed against the anchor in both directions and both folds) and
  every check_print row of the region passed B2 (C1-C4) in both folds; the only status called real, verified print.
- ``check_failed``: the gate registered >= 2 views but a gradable check_print row (>= 1000 E px, §8 B2) failed, or a
  fold had no gradable row. Rows under 1000 E px are listed as ungraded and neither pass nor fail.
- ``anchor-only``: the region has partners, none registered (flagged with the residual and the parallax).
- ``single-view``: only the anchor sees it at donor angles (pixel-unverifiable; B3 only).
- ``unseen``: no admitted view sees it (filled later).
Overall: ``check_failed`` if any region is, ``ok`` if every region with partners is registered, else ``partial``.

STOP A (§9; ``finalize``) fires if A0 failed OR WAS NOT SUPPLIED (fix round 2: §9 requires A0 to pass, so a run
without A0's verdict cannot clear STOP A; ``stop_a['a0']`` says which), if no brow front registers in both folds, if a registered region fails
C1-C4 in either fold, or whenever B1 fails: no brow front registered by the gate, any guard of the A, B or 'all' fit
beyond 0.02 (including view 01's whole silhouette with its hinges), or a registered region whose parallax reaches more
than 4 mm (max over s of |a + b s| = |a| + |b|) in any fit. B2 covers every registered held-out view of every
registered region (not a fixed view list: with all six FT1119 views in use, the -x temple inner face is anchored on
06). B1 also lists the parallax of every parallax region, registered or not, against the 4 mm limit, so a proposal to
loosen the gate shows what B1 would then say. ``pixel_verifiable`` gives the largest share of the print area that
could be pixel-verified at all (the regions with a partner) beside the share that was. JSON writing is deterministic:
floats as Python reprs, numpy converted, timing fields only in report.json.

**Stability** (fix round 2): ``parallax_stability`` gives, per parallax region and fit, the parallax from the WP3b start
and from each perturbed start (``register(start_seed=k)``), whether each fit converged, its final loss, and the spread
max over s = +/-1 of (max - min of d(s)) across the starts; ``restart_spread_mm`` = how far the WP3b-start fit's
parallax moved after its first Powell run of the last band (what a fit stopped at maxfev would have reported).
``gate_stability`` lists each fold-protocol gate row's verdict at every start. B1 copies each region's spread beside
its |a| + |b|: a parallax is a property of the photos only where the starts agree.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

TIMING_KEYS = ("seconds",)


def jsonable(o):
    if isinstance(o, dict):
        return {str(k): jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [jsonable(x) for x in o]
    if isinstance(o, (np.floating,)):
        o = float(o)
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.ndarray):
        return jsonable(o.tolist())
    return o


def strip_timing(o):
    if isinstance(o, dict):
        return {k: strip_timing(v) for k, v in o.items() if k not in TIMING_KEYS}
    if isinstance(o, list):
        return [strip_timing(x) for x in o]
    return o


def write_json(path, obj) -> str:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(jsonable(obj), indent=1, ensure_ascii=False, allow_nan=False)
    p.write_text(text + "\n", encoding="utf-8", newline="\n")
    return str(p)


def region_statuses(regions, plan, registered: dict[int, list[str]], check_rows: list[dict], gate_rows: list[dict]):
    by = {r.rid: r for r in regions}
    out = {}
    for r in regions:
        if r.minor:
            out[r.rid] = {"name": r.name, "status": "minor", "anchor": None}
            continue
        anchor = plan.anchors.get(r.rid)
        if anchor is None:
            out[r.rid] = {"name": r.name, "status": "unseen", "anchor": None}
            continue
        partners = plan.partners.get(r.rid, [])
        reg = registered.get(r.rid, [anchor])
        row = {"name": r.name, "anchor": anchor, "partners": partners, "registered_views": reg}
        if not partners:
            row["status"] = "single-view"
        elif len(reg) < 2:
            row["status"] = "anchor-only"
            gs = [g for g in gate_rows if g["rid"] == r.rid]
            row["gate_residual_mm"] = sorted({round(g["residual_mm"], 3) for g in gs if g.get("residual_mm") is not None
                                              and g.get("fit_fold") in ("A", "B")})
            row["gates"] = [{"pair": f"{_short(g['source'])}->{_short(g['target'])}", "eval_fold": g["fold"],
                             "fit_fold": g.get("fit_fold"), "E_px": g.get("E_px"), "decoys": g.get("decoys"),
                             "significant": g.get("significant"), "reason": g.get("reason"),
                             "residual_mm": g.get("residual_mm"), "registered": g.get("registered")} for g in gs]
        else:
            rows = [c for c in check_rows if c["rid"] == r.rid]
            graded = [c for c in rows if c.get("b2_eligible")]
            failed = [c for c in graded if not c.get("b2_pass")]
            folds_graded = {c["eval_fold"] for c in graded}
            missing = sorted({"A", "B"} - folds_graded)
            row["status"] = "registered" if (graded and not failed and not missing) else "check_failed"
            row["check_rows"] = len(rows)
            row["ungraded_rows"] = [{"held_out": c["held_out"], "eval_fold": c["eval_fold"], "pixels": c["pixels"]}
                                    for c in rows if not c.get("b2_eligible")]
            row["check_failed_rows"] = [{"held_out": c["held_out"], "eval_fold": c["eval_fold"],
                                         "pixels": c["pixels"],
                                         "failed": [k for k, v in (c.get("criteria") or {}).items() if not v["pass"]]}
                                        for c in failed]
            if missing:
                row["no_gradable_row_in_folds"] = missing
        out[r.rid] = row
    return out


def _short(name: str) -> str:
    from .inputs import short_name
    return short_name(name)


def overall(statuses: dict) -> str:
    st = [s["status"] for s in statuses.values()]
    if "check_failed" in st:
        return "check_failed"
    if any(s == "anchor-only" for s in st):
        return "partial"
    return "ok" if any(s == "registered" for s in st) else "partial"


def status_areas(statuses: dict, regions) -> dict:
    by = {r.rid: r for r in regions}
    out: dict = {}
    for rid, st in statuses.items():
        out[st["status"]] = out.get(st["status"], 0.0) + by[rid].area_mm2
    total = sum(out.values())
    return {k: {"mm2": round(v, 1), "share": round(v / total, 4) if total else 0.0} for k, v in sorted(out.items())}


def pixel_verifiable(statuses: dict, regions, plan) -> dict:
    """The largest share of the print area that could be pixel-verified (regions with a partner: only they can be
    gated and checked on held-out pixels) and the share that was (status ``registered``)."""
    total = float(sum(r.area_mm2 for r in regions))
    rows = [{"region": r.name, "mm2": round(r.area_mm2, 1), "share": round(r.area_mm2 / total, 4) if total else 0.0,
             "status": statuses[r.rid]["status"]} for r in regions if r.rid in set(plan.parallax)]
    reg = float(sum(r.area_mm2 for r in regions if statuses[r.rid]["status"] == "registered"))
    mx = float(sum(x["mm2"] for x in rows))
    return {"total_mm2": round(total, 1), "regions_with_a_partner": rows,
            "max_verifiable_mm2": round(mx, 1), "max_verifiable_share": round(mx / total, 4) if total else 0.0,
            "verified_mm2": round(reg, 1), "verified_share": round(reg / total, 4) if total else 0.0}


def parallax_summary(regs: dict, plan, regions) -> dict:
    """Per parallax region: (a, b) per fit, the fold spread max_s |d_A(s) - d_B(s)| at s = +/-1 and whether the
    all-stripes parallax is a discrepancy Astra should hear of (|d| > 2 x the fold spread, WP5_DESIGN §11 risk 2)."""
    by = {r.rid: r for r in regions}
    out = {}
    for rid in plan.parallax:
        row = {"anchor": plan.anchors[rid]}
        d = {}
        for f in ("A", "B", "all"):
            if f in regs:
                a, b = regs[f].pose.parallax.get(rid, (0.0, 0.0))
                row[f] = {"a_mm": a, "b_mm": b}
                d[f] = np.array([a - b, a + b])
        if "A" in d and "B" in d:
            spread = float(np.abs(d["A"] - d["B"]).max())
            row["fold_spread_mm"] = spread
            if "all" in d:
                mx = float(np.abs(d["all"]).max())
                row["max_abs_all_mm"] = mx
                row["discrepancy_for_astra"] = bool(mx > 2 * spread)
        out[by[rid].name] = row
    return out


def _d(a: float, b: float) -> np.ndarray:
    return np.array([a - b, a + b])            # d(s) at s = -1, +1


def parallax_stability(regs: dict, sregs: dict, plan, regions) -> dict:
    """Per parallax region and fit fold: the parallax from every start, the spread across starts and the restart
    spread of the WP3b-start fit (see the module docstring)."""
    by = {r.rid: r for r in regions}
    out = {}
    for rid in plan.parallax:
        name = by[rid].name
        row = {}
        for f, reg in regs.items():
            starts = [("wp3b", reg)] + [(k, r) for k, r in sorted(sregs.items()) if r.info["fold"] == f]
            ent = []
            for key, r in starts:
                a, b = r.pose.parallax.get(rid, (0.0, 0.0))
                ent.append({"start": key, "a_mm": float(a), "b_mm": float(b), "max_abs_mm": float(abs(a) + abs(b)),
                            "loss_final": r.info.get("loss_final"), "converged": bool(r.info.get("converged")),
                            "start_parallax_mm": _start_par(r, rid)})
            ds = np.array([_d(e["a_mm"], e["b_mm"]) for e in ent])
            spread = float((ds.max(0) - ds.min(0)).max()) if len(ds) > 1 else None
            losses = [e["loss_final"] for e in ent if e["loss_final"] is not None]
            best = min(ent, key=lambda e: (math.inf if e["loss_final"] is None else e["loss_final"]))
            hist = reg.info.get("history") or []
            rs = None
            if hist and hist[-1].get("runs"):
                dfin = _d(*reg.pose.parallax.get(rid, (0.0, 0.0)))
                rs = float(max(np.abs(_d(*run["parallax_mm"][name]) - dfin).max() for run in hist[-1]["runs"]))
            row[f] = {"starts": ent, "spread_mm": spread, "restart_spread_mm": rs,
                      "max_abs_range_mm": [float(min(e["max_abs_mm"] for e in ent)),
                                           float(max(e["max_abs_mm"] for e in ent))],
                      "all_converged": all(e["converged"] for e in ent),
                      "lowest_loss_start": best["start"],
                      "loss_range": [float(min(losses)), float(max(losses))] if losses else None}
        out[name] = row
    return out


def _start_par(r, rid: int):
    off = r.info.get("start_offset")
    lay = r.info.get("layout") or []
    if off is None:
        return None
    ab = [0.0, 0.0]
    for (kind, a, i), x in zip(lay, off):
        if kind == "par" and int(a) == rid:
            ab[int(i)] = float(x)
    return ab


def gate_stability(gate_rows: list[dict], stab_rows: list[dict], by: dict) -> list[dict]:
    """Per fold-protocol gate (region, pair, evaluated fold): the verdict at the WP3b start and at each perturbed
    start of the same fit fold."""
    from .inputs import short_name
    out = []
    for g in gate_rows:
        if g.get("fit_fold") not in ("A", "B"):
            continue
        same = [s for s in stab_rows if s["rid"] == g["rid"] and s["source"] == g["source"]
                and s["target"] == g["target"] and s["fold"] == g["fold"]]

        def v(x):
            return {"fit": x["fit_fold"], "registered": x.get("registered"), "significant": x.get("significant"),
                    "reason": x.get("reason"), "residual_mm": x.get("residual_mm"), "decoys": x.get("decoys"),
                    "E_px": x.get("E_px")}
        rows = [v(g)] + [v(x) for x in sorted(same, key=lambda x: x["fit_fold"])]
        out.append({"region": by[g["rid"]].name, "pair": f"{short_name(g['source'])}->{short_name(g['target'])}",
                    "eval_fold": g["fold"], "starts": rows,
                    "registered_at": sum(1 for r in rows if r["registered"]), "of": len(rows)})
    return out


def camera_summary(regs: dict, plan) -> dict:
    return {n: {f: regs[f].info["pose"]["cameras"][n]["delta_from_start"] for f in regs}
            for n in plan.free_cameras}


def warnings(statuses: dict, regs: dict, plan, regions, edges_after: dict, sregs: dict | None = None) -> list[str]:
    out = []
    by_name = {r.name: r for r in regions}
    for st in statuses.values():
        if st["status"] in ("registered", "check_failed"):
            r = by_name[st["name"]]
            for v in st["registered_views"]:
                row = edges_after.get(v, {}).get(r.part_name)
                if row is not None and not row["admitted"]:
                    out.append(f"{st['name']}: registered view {v} fails the edge gate after registration "
                               f"({row['mean_mm']}, {row['p90_mm']} mm): it may not donate colour")
    for f, reg in list(regs.items()) + sorted((sregs or {}).items()):
        for h in reg.info.get("history", []):
            if not h.get("converged", True):
                out.append(f"fit {f}: band {h['band_mm']} not converged after {len(h.get('runs') or [])} Powell runs "
                           f"of up to {reg.info['maxfev']} evaluations (last gain "
                           f"{(h.get('runs') or [{}])[-1].get('gain', float('nan')):.2e})")
    for f, reg in regs.items():
        lf = reg.info.get("loss_final")
        for k, r in sorted((sregs or {}).items()):
            if r.info["fold"] == reg.info["fold"] and lf is not None and r.info.get("loss_final") is not None \
                    and r.info["loss_final"] < lf - LOSS_WARN:
                out.append(f"fit {f}: perturbed start {k} ended at a lower loss ({r.info['loss_final']:.4f} vs "
                           f"{lf:.4f}): the WP3b-start fit is not the lowest minimum found")
    return out



PARALLAX_B1_MM = 4.0
LOSS_WARN = 0.002


def finalize(out_dir, a0_passed: bool | None = None, a0_summary: str | None = None) -> dict:
    """B1, B2 and STOP A (§8, §9) from the written registration.json, check_print.json and report.json, folded into
    report.json. Works on the JSON only, so A0's verdict can be folded in after both runs."""
    out = Path(out_dir)
    reg = json.loads((out / "registration.json").read_text(encoding="utf-8"))
    chk = json.loads((out / "check_print.json").read_text(encoding="utf-8"))
    rep = json.loads((out / "report.json").read_text(encoding="utf-8"))
    regions = {r["name"]: r for r in reg["regions"]}
    statuses = rep["regions"]
    fits = reg["fits"]

    fronts = {n: st for n, st in statuses.items()
              if regions[n]["facing"] == "front" and regions[n]["part"].startswith("Brow") and st.get("anchor")}
    gate_reg = {n: len(st.get("registered_views") or []) >= 2 for n, st in fronts.items()}
    guards = {f: [g for g in fit["guards"] if not g["within"]] for f, fit in fits.items()}
    # max over s in [-1, 1] of |a + b s| = |a| + |b|, per parallax region and fit
    par_all: dict = {}
    for f, fit in fits.items():
        for n, pp in fit["pose"]["parallax"].items():
            par_all.setdefault(n, {})[f] = abs(pp["a_mm"]) + abs(pp["b_mm"])
    gate_registered = {n for n, st in statuses.items() if len(st.get("registered_views") or []) >= 2}
    par = {n: row for n, row in par_all.items() if n in gate_registered}
    over = {n: {f: v for f, v in row.items() if v > PARALLAX_B1_MM} for n, row in par_all.items()}
    over = {n: row for n, row in over.items() if row}
    par_ok = not any(n in gate_registered for n in over)
    stab = rep.get("parallax_stability") or {}

    def spread(n):
        return {f: {"spread_mm": v.get("spread_mm"), "max_abs_range_mm": v.get("max_abs_range_mm"),
                    "all_converged": v.get("all_converged")} for f, v in (stab.get(n) or {}).items()}
    b1 = {"brow_fronts_gate_registered": gate_reg, "guards_broken": guards,
          "registered_parallax_max_abs_mm": par, "parallax_within_4mm": bool(par_ok),
          "parallax_max_abs_mm_every_region": par_all,
          "parallax_stability": {n: spread(n) for n in par_all},
          "parallax_over_4mm": {n: {"fits": row, "gate_registered": n in gate_registered, "stability": spread(n)}
                                for n, row in over.items()},
          "pass": bool(any(gate_reg.values()) and not any(guards.values()) and par_ok)}

    rows = list(chk["rows"])          # every registered held-out view of every registered region
    graded = [c for c in rows if c.get("b2_eligible")]
    b2 = {"rows": [{"region": c["region"], "held_out": c["held_out"], "eval_fold": c["eval_fold"],
                    "pixels": c["pixels"], "graded": bool(c.get("b2_eligible")), "pass": bool(c.get("b2_pass")),
                    "criteria": {k: {"pass": v["pass"], **({"margin": v["margin"]} if "margin" in v else {})}
                                 for k, v in (c.get("criteria") or {}).items()}} for c in rows],
          "graded": len(graded), "passed": sum(1 for c in graded if c.get("b2_pass")),
          "pass": bool(graded) and all(c.get("b2_pass") for c in graded)}

    reasons = []
    if a0_passed is False:
        reasons.append("A0 failed" + (f": {a0_summary}" if a0_summary else ""))
    elif a0_passed is None:
        reasons.append("A0 verdict not supplied (tests/photoprint/test_register_synthetic.py, data/wp5/a0/a0.json)")
    if not any(statuses[n]["status"] == "registered" for n in fronts):
        reasons.append("no brow front registered (gate in both directions and both folds, then check_print C1-C4)")
    for n, st in statuses.items():
        if st["status"] == "check_failed":
            reasons.append(f"{n}: registered by the gate but failed check_print: {st.get('check_failed_rows')}"
                           + (f"; no gradable row in folds {st['no_gradable_row_in_folds']}"
                              if st.get("no_gradable_row_in_folds") else ""))
    if not b1["pass"]:
        parts = []
        if not any(gate_reg.values()):
            parts.append("no brow front registered by the gate (both directions, both folds)")
        for f in ("A", "B", "all"):
            if guards.get(f):
                parts.append(f"fit {f} broke guards: " + ", ".join(
                    f"{g['view']} {g['guard']}{(' ' + g['group']) if g.get('group') else ''} drop {g['drop']:.3f}"
                    for g in guards[f]))
        for n, row in over.items():
            if n in gate_registered:
                parts.append(f"{n}: parallax over {PARALLAX_B1_MM:g} mm (" + ", ".join(
                    f"fit {f} {v:.2f} mm" for f, v in row.items()) + ")")
        reasons.append("B1 failed: " + "; ".join(parts))
    fired = bool(reasons)                    # a missing A0 verdict blocks too (fix round 2)
    rep["A0_passed"] = a0_passed
    rep["A0_summary"] = a0_summary
    rep["B1"], rep["B2"] = b1, b2
    rep["diagnostic_check"] = [{"region": c["region"], "held_out": c["held_out"], "eval_fold": c["eval_fold"],
                                "pixels": c["pixels"], "b2_eligible": c.get("b2_eligible"), "b2_pass": c.get("b2_pass"),
                                "criteria": {k: {"pass": v["pass"], **({"margin": v["margin"]} if "margin" in v else {})}
                                             for k, v in (c.get("criteria") or {}).items()},
                                "bp": [c["projection"].get("bp_1_4px_r"), c["astra"].get("bp_1_4px_r")]
                                if c.get("projection") else None,
                                "de00_gain": [c["projection"].get("de00_gain_mean"), c["astra"].get("de00_gain_mean")]
                                if c.get("projection") else None}
                               for c in chk.get("diagnostic_rows", [])]
    rep["stop_a"] = {"fired": bool(fired), "reasons": reasons,
                     "a0": "passed" if a0_passed else ("failed" if a0_passed is False else "missing")}
    write_json(out / "report.json", rep)
    return rep
