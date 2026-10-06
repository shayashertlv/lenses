"""Seating report schema and summaries (WP4_DESIGN.md §8 'Report per lens'). numpy only; plain JSON types out.

Every number is rounded to 1e-4 mm so a report is stable text (two runs are compared byte for byte, §10 P1).
"""
from __future__ import annotations

import numpy as np

from .rules import FrameType, Rules, VERDICTS
from .stations import CLASSES, MID, SECTORS, Band, Frames, Measurement

ROUND = 4


def r(x, nd: int = ROUND):
    """JSON-safe rounding: None for nan/inf."""
    if x is None:
        return None
    x = float(x)
    return round(x, nd) if np.isfinite(x) else None


def pct(a, q) -> list | None:
    a = np.asarray(a, np.float64)
    a = a[np.isfinite(a)]
    return [r(v) for v in np.percentile(a, q)] if len(a) else None


def rule_label(rule: str) -> str:
    return {"deep": "min over depths 0.05-0.95", "thin": "mid-depth 0.5", "flats": "min of depths 0.05/0.95",
            "apex": "V-bevel apex (mid-depth) beyond the groove's lips, every depth beyond them",
            "report": "mid-depth 0.5"}.get(rule, rule)


def lens_report(name: str, ft: FrameType, frames: Frames, meas: Measurement, rules: Rules, verdict: np.ndarray,
                check: dict, part_names: list, *, band: Band | None = None, rim_split: dict | None = None,
                extra: dict | None = None, stations: bool = False) -> dict:
    cls = meas.cls.astype(str)
    v = verdict.astype(str)
    sec = frames.sector
    req = rules.required
    val = rules.value
    out = {"lens": name, "frame_type": ft.name, "lower_edge": ft.lower_edge, "rule": ft.rule,
           "pass_mm": ft.pass_mm, "target_mm": ft.target_mm, "pass": check["pass"], "reasons": check["reasons"],
           "points": int(len(cls)), "required": int(req.sum()), "loop_mm": r(frames.loop_mm, 3),
           "classes": {c: int((cls == c).sum()) for c in CLASSES},
           "verdicts": {k: int((v == k).sum()) for k in VERDICTS if (v == k).any()},
           "recast_points": int((meas.recast > 0).sum())}
    if ft.in_range_rule:
        out["in_ranges"] = {"points": int(rules.in_range.sum()), "rule": ft.in_range_rule,
                            "pass_mm": ft.in_range_pass_mm, "target_mm": ft.in_range_target_mm}
    out["sectors"] = sector_summary(sec, cls, rules, meas)
    if req.any() and ft.pass_mm is not None:
        vr = val[req]
        out["share_ge_pass"] = r(np.mean(np.nan_to_num(vr, nan=-9) >= rules.pass_mm[req] - 0.005))
        out["share_ge_target"] = r(np.mean(np.nan_to_num(vr, nan=-9) >= rules.target_mm[req] - 0.005))
        out["hidden_front_share"] = r(np.mean(meas.covered_front[req]))
        out["hidden_back_share"] = r(np.mean(meas.covered_back[req]))
    # worst gap (most negative mid-depth capture) and worst deficit (pass - value) among required points
    if req.any():
        mid = np.where(req, meas.cap[:, MID], np.nan)
        if np.isfinite(mid).any() and np.nanmin(mid) < 0:
            k = int(np.nanargmin(mid))
            out["worst_gap"] = {"mm": r(-mid[k]), "xyz": [r(x, 3) for x in frames.P[k]], "sector": str(sec[k])}
        deficit = np.where(req, rules.pass_mm - np.nan_to_num(val, nan=-np.inf), -np.inf)
        if np.isfinite(rules.pass_mm).any() and deficit.max() > 0:
            k = int(np.argmax(deficit))
            out["worst_deficit"] = {"mm": r(deficit[k]) if np.isfinite(val[k]) else None,
                                    "value_mm": r(val[k]), "xyz": [r(x, 3) for x in frames.P[k]],
                                    "sector": str(sec[k]), "class": cls[k]}
    if ft.range_mm is not None:
        over = req & np.isfinite(val) & (val > ft.range_mm[1])
        if over.any():
            out["over_capture"] = {"points": int(over.sum()), "max_mm": r(val[over].max()),
                                   "limit_mm": ft.range_mm[1], "note": "over-capture is a warning; seating never "
                                                                       "shrinks a lens"}
    edge = frames.edge_mm[frames.ok]
    out["edge_mm"] = {"min": r(edge.min()) if len(edge) else None, "p50": r(np.median(edge)) if len(edge) else None}
    owners = meas.owner[:, MID]
    out["innermost_part_mid"] = {(part_names[i] if i >= 0 else "none"): int((owners == i).sum())
                                 for i in sorted(set(owners.tolist()))}
    if rim_split is not None:
        out["rim_split_deg"] = rim_split
    if band is not None:
        out["band"] = band_summary(band, frames, part_names, rules.in_range)
    if extra:
        out.update(extra)
    if stations:
        out["stations"] = station_rows(frames, meas, rules, verdict, part_names)
    return out


def sector_summary(sec, cls, rules: Rules, meas: Measurement) -> dict:
    """§8 capture p5/p50/p95 per sector with the depth named. Points graded by different rules (browline: deep in
    the hidden ranges, thin elsewhere) are summarised separately, each under its own rule label: a min over depths
    is never labelled 'mid-depth'. A sector with one rule keeps the flat form {"rule", "value_p5_p50_p95"}; a mixed
    one has "rule": null and "by_rule": {"in_ranges" | "outside_ranges": {"rule", "points", "value_p5_p50_p95"}}."""
    out = {}
    rule = np.asarray(rules.rule).astype(str)
    for s in SECTORS:
        m = sec == s
        if not m.any():
            continue
        d = {"points": int(m.sum()), "classes": {c: int((cls[m] == c).sum()) for c in CLASSES if (cls[m] == c).any()}}
        groups = []
        for key, g in (("in_ranges", m & rules.in_range), ("outside_ranges", m & ~rules.in_range)):
            if g.any():
                rs = sorted(set(rule[g].tolist()))
                groups.append((key, g, rs[0] if len(rs) == 1 else "mixed"))
        if len(groups) == 1:
            _, g, rn = groups[0]
            d.update(rule=rule_label(rn), value_p5_p50_p95=pct(rules.value[g], [5, 50, 95]))
        else:
            d["rule"] = None
            d["by_rule"] = {key: {"rule": rule_label(rn), "points": int(g.sum()),
                                  "value_p5_p50_p95": pct(rules.value[g], [5, 50, 95])} for key, g, rn in groups}
        d["mid_depth_p5_p50_p95"] = pct(meas.cap[m, MID], [5, 50, 95])
        out[s] = d
    return out


BAND_AREA_ZERO_MM2 = 0.005        # "0.00 mm2" (§8) at the report's two decimals


def band_summary(band: Band, frames: Frames, part_names: list, in_range: np.ndarray) -> dict:
    """§8 buried-part band, per (buried part, outer part) pair: the width of the buried part visible along +n
    (front) and -n (back), and its section area outside every other part. Graded only inside the hidden ranges."""
    c = band.candidate
    out = {"stations": int(c.sum()), "held_parts": [part_names[i] for i in band.held], "pairs": {}}
    if not c.any():
        return out
    keys = np.array([f"{part_names[b]} under {part_names[o]}" if ok else ""
                     for b, o, ok in zip(band.buried.tolist(), band.outer.tolist(), c.tolist())])
    for key in sorted(set(keys[c].tolist())):
        m = keys == key
        d = {"stations": int(m.sum()), "in_ranges": int((m & in_range).sum())}
        for name, arr in (("front", band.front_mm), ("back", band.back_mm)):
            a = arr[m]
            d[name] = {"p50_mm": r(np.median(a)), "max_mm": r(a.max()), "share_gt_0.05": r(np.mean(a > 0.05))}
        area = band.area_outside_mm2[m]
        d["area_outside_mm2"] = {"p50": r(np.median(area)), "max": r(area.max())}
        out["pairs"][key] = d
    g = c & in_range
    out["graded_stations"] = int(g.sum())
    if g.any():
        out["graded"] = {"front_max_mm": r(band.front_mm[g].max()), "back_max_mm": r(band.back_mm[g].max()),
                         "area_outside_max_mm2": r(band.area_outside_mm2[g].max()),
                         "ok": bool(band.front_mm[g].max() <= 0.05 and band.back_mm[g].max() <= 0.05
                                    and band.area_outside_mm2[g].max() <= BAND_AREA_ZERO_MM2)}
    return out


def station_rows(frames: Frames, meas: Measurement, rules: Rules, verdict, part_names) -> list:
    """Per point. "node" is the owning rim vertex as a body-vertex id (Frames.node_vertex: a row of the welded
    LensBody.V, not an index into body.fl, whose order may be reversed); -1 for external frames."""
    rows = []
    sec = frames.sector
    nv = frames.node_vertex
    for k in range(len(frames)):
        o = int(meas.owner[k, MID])
        rows.append({"s": r(frames.s[k], 3), "node": int(nv[k]), "xyz": [r(x, 3) for x in frames.P[k]],
                     "sector": str(sec[k]), "class": str(meas.cls[k]), "verdict": str(verdict[k]),
                     "rule": str(rules.rule[k]), "value": r(rules.value[k]), "required": bool(rules.required[k]),
                     "cap": [r(c) for c in meas.cap[k]], "room": [r(c) for c in meas.room[k]],
                     "owner": part_names[o] if o >= 0 else None, "cover_front": r(meas.cover_front[k]),
                     "cover_back": r(meas.cover_back[k]), "edge_mm": r(frames.edge_mm[k])})
    return rows

