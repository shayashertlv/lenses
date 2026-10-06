"""Grade the WP4 §10 proof runs (tests/proofs/wp4_proof.py) into proof.json (tests only).

Every §10 criterion of P2, P3, P4 and P5 becomes one or more records {"id", "criterion", "threshold", "by",
"measured", "pass", "note"}; `by` names who measured it:
- "core":       the tool's own report, the Blender check, or the host check_glb on the exported GLB (the binding core);
- "instrument": the independent occupancy-grid instrument on the exported GLB (seat_grid.py, seat_proof_metrics.py);
- "arrays":     the raw Blender vertex arrays before / after (bit comparison), or mesh digests;
- "spec":       the WP3 spec validator.
Thresholds are §10's numbers as written; where §10 writes '≈' the record says how it was read. Nothing here changes
a tool constant (WP4_DESIGN §10 stop rule: never tune constants to pass).
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from glasses3d.g3d.seat import check as CK
from glasses3d.g3d.seat import rules, stations
from glasses3d.g3d_host import seat_check as SC
from glasses3d.g3d_host.glb import load as load_glb
from glasses3d.g3d_host.spec import validate_spec
from glasses3d.tests.fixtures import seat_fixtures as SF
from glasses3d.tests.instruments import seat_grid as IG
from glasses3d.tests.instruments import seat_proof_metrics as M
from glasses3d.tests.proofs.wp4_proof import (FT_ALL_RANGES, FT_RANGES, INVU_LENS, INVU_PRINT, MATRIX, OAKLEY_LENS,
                                              OUT, PRADA)

DEPTHS = stations.DEPTHS
TOL = rules.PASS_TOLERANCE_MM


# ------------------------------------------------------------------------------------------------------ plumbing
def out(case: str) -> dict:
    d = json.loads((OUT / "outs" / f"{case}.json").read_text(encoding="utf-8"))
    if d.get("error"):
        raise RuntimeError(f"{case}: {d['error']}\n{d.get('traceback', '')}")
    return {r["key"]: r["result"] for r in d["results"]}


def arr(case: str, key: str):
    return np.load(OUT / "npz" / f"{case}-{key}.npz")


def glb(case: str, key: str) -> Path:
    return OUT / "glb" / f"{case}-{key}.glb"


def r4(x):
    if x is None:
        return None
    if isinstance(x, (list, tuple, np.ndarray)):
        return [r4(v) for v in x]
    if isinstance(x, (bool, np.bool_)):
        return bool(x)
    if isinstance(x, (int, np.integer)):
        return int(x)
    x = float(x)
    return round(x, 4) if np.isfinite(x) else None


class Case:
    def __init__(self, cid: str, title: str):
        self.id, self.title, self.records, self.notes, self.info = cid, title, [], [], {}

    def add(self, rid: str, criterion: str, threshold: str, by: str, measured, ok, note: str | None = None):
        """ok None: not applicable (the criterion has nothing to measure, e.g. 'moved vertices <= 200' on a lens the
        tool refused): status 'not_applicable', pass null, never counted as passing."""
        rec = {"id": rid, "criterion": criterion, "threshold": threshold, "by": by, "measured": measured,
               "pass": None if ok is None else bool(ok),
               "status": "not_applicable" if ok is None else ("pass" if ok else "fail")}
        if note:
            rec["note"] = note
        self.records.append(rec)
        return ok

    def result(self) -> dict:
        graded = [r for r in self.records if r["pass"] is not None]
        return {"case": self.id, "title": self.title, "pass": bool(graded) and all(r["pass"] for r in graded),
                "failed": [f"{r['id']} ({r['by']})" for r in graded if not r["pass"]],
                "not_applicable": [f"{r['id']} ({r['by']})" for r in self.records if r["pass"] is None],
                "counts": {"criteria": len(self.records), "pass": sum(1 for r in graded if r["pass"]),
                           "fail": sum(1 for r in graded if not r["pass"]),
                           "not_applicable": len(self.records) - len(graded)},
                "criteria": self.records, "notes": self.notes, "info": self.info}


def moved_rows(a0, a1, i: int) -> np.ndarray:
    return np.flatnonzero(np.any(a0[f"o{i}_co"] != a1[f"o{i}_co"], axis=1))


def node_join(ns0: dict, ns1: dict) -> list:
    """Per body: [(node raw row, before node-point index, after node-point index)], joined on raw rows."""
    out_ = []
    for b0, b1 in zip(ns0["bodies"], ns1["bodies"]):
        pos1 = {r: k for k, r in enumerate(b1["loop_raw"])}
        rows = []
        for k, r in enumerate(b0["loop_raw"]):
            if r in pos1:
                rows.append((r, b0["node_point"][k], b1["node_point"][pos1[r]], k, pos1[r]))
        out_.append(rows)
    return out_


def pts(b: dict, key: str) -> np.ndarray:
    v = b["point"][key]
    return np.array([np.nan if x is None else x for x in v], dtype=float) if key in (
        "value", "pass", "target", "cover_front", "cover_back", "delta_run_end", "growth") else np.asarray(v)


def failing_nodes_of(b: dict, verdict_key: str = "verdict_check", sector=None) -> np.ndarray:
    v = np.asarray(b["point"][verdict_key])
    m = v == "FAIL"
    if sector is not None:
        m &= np.isin(np.asarray(b["point"]["sector"]), sector)
    n1 = np.asarray(b["point"]["node"])
    n2 = np.asarray(b["point"]["node2"])
    return np.unique(np.r_[n1[m], n2[m]])


def check_lens(rep: dict, name_part: str) -> dict:
    hits = [L for L in rep.get("lenses", []) if name_part in L["lens"] or name_part in L.get("node", "")]
    if not hits:
        raise KeyError(f"{name_part}: not in {[L['lens'] for L in rep.get('lenses', [])]}")
    return hits[0]


def st_arrays(L: dict) -> dict:
    S = L["stations"]
    f = lambda v: np.nan if v is None else v      # noqa: E731
    return {"sector": np.array([s["sector"] for s in S]), "cls": np.array([s["class"] for s in S]),
            "verdict": np.array([s["verdict"] for s in S]), "required": np.array([s["required"] for s in S]),
            "value": np.array([f(s["value"]) for s in S], float),
            "cap": np.array([[f(c) for c in s["cap"]] for s in S], float),
            "cover_front": np.array([f(s["cover_front"]) for s in S], float),
            "cover_back": np.array([f(s["cover_back"]) for s in S], float),
            "xyz": np.array([s["xyz"] for s in S], float), "s": np.array([s["s"] for s in S], float),
            "owner": [s["owner"] for s in S], "rule": np.array([s["rule"] for s in S])}


def pct(a, q):
    a = np.asarray(a, float)
    a = a[np.isfinite(a)]
    return r4(np.percentile(a, q)) if len(a) else None


# -------------------------------------------------------------------------------------------- instrument inputs
_SC: dict = {}


def inst_scene(path: Path) -> dict:
    key = str(path)
    if key not in _SC:
        _SC[key] = M.read_glb(path)
    return _SC[key]


def inst_lens(path: Path, lens_node: str):
    """(lens V, T, frame solids near it) from a GLB, by the instrument's own reading."""
    sc = inst_scene(path)
    bodies, solids = M.bodies_and_parts(sc)
    b = [x for x in bodies if x[0] == lens_node]
    if not b:
        raise KeyError(f"{lens_node} not a lens body in {path.name}: {[x[0] for x in bodies]}")
    _, V, T = b[0]
    return V, T, M.near_parts(V, solids)


def inst_part(path: Path, node: str):
    sc = inst_scene(path)
    comps = [c for c in M.components(sc[node]["V"], sc[node]["T"]) if c["closed"]]
    comps.sort(key=lambda c: -c["volume"])
    return comps[0]["V"], comps[0]["T"]


def mirror_pair(L0, R0, L1, R1) -> float:
    """Max distance between the left mesh mirrored in x and the right mesh, vertices paired on the input pair."""
    from scipy.spatial import cKDTree
    m = np.array([-1.0, 1.0, 1.0])
    tree = cKDTree(R0)
    d0, j = tree.query(L0 * m)
    if d0.max() > 1e-3:
        return np.inf
    return float(np.linalg.norm(L1 * m - R1[j], axis=1).max())


# ------------------------------------------------------------------------------------- moved nodes (stop rule)
LAYER_WINDOW_MM = (-1.5, 4.0)    # §6 step 1, restated: 'under' material along +u starting within [-1.5, 4] mm


def instrument_layered(F: dict, R: dict, part_scenes) -> np.ndarray:
    """Per instrument station: at least two distinct frame parts have material on the mid-depth row within the §6
    window of the lens edge (the brow over the eyewire; the loop bottom at the same x has the eyewire only). The
    instrument's own reading (occupancy samples every 0.01 mm), restated from §6 step 1."""
    j = DEPTHS.index(0.5)
    e = R["edge"][:, j]
    ok = np.isfinite(e)
    out = np.zeros(len(e), bool)
    if not ok.any():
        return out
    idx = np.flatnonzero(ok)
    O = F["Pm"][idx] + R["v"][idx, j, None] * F["n"][idx] + e[idx, None] * F["u"][idx]
    D = F["u"][idx]
    hits = np.zeros(len(idx), int)
    for sc in part_scenes:
        runs = M._line_runs([sc], O, D, LAYER_WINDOW_MM[0], LAYER_WINDOW_MM[1])
        hits += np.array([len(r) > 0 for r in runs], int)
    out[idx] = hits >= 2
    return out


def instrument_failing(path: Path, lens_node: str, frame_type: str, ranges=None) -> dict:
    """The instrument's own verdict per 1 mm station of a lens on a GLB (no code shared with g3d/seat): FAIL where
    the §7 rule value is below pass - 0.005 (acetate: min over the 5 depths < 0.60; metal: c(0.5) < 0.30 or the
    front corner not covered; semi_rimless: held (c(0.5) >= -1.0 and the front covered or frame on the 0.05 row
    within 1.0 mm) and c(0.5) < 0.30; browline: the acetate rule where the station lies inside an x-range AND is
    layered (two frame parts on its mid-depth row within the §6 window: the brow over the eyewire), the metal rule
    elsewhere, the loop bottom at the same x included (proof review round 2: the deep rule over the whole x-range
    marked 59 below-centre stations of ft-mid Lens_Left failing, so a wrongly moved bottom node would pass this
    record))."""
    V, T, parts = inst_lens(path, lens_node)
    F = M.frames(V, T, 1.0)
    R = M.rows(F, F["scene"], M.scenes(parts), DEPTHS)
    cv = M.corner_cover(F, F["scene"], M.scenes(parts))["front"]
    c = np.where(R["cap"] >= 90, np.nan, R["cap"])
    mid, f05 = c[:, stations.MID], c[:, 0]
    deep = _min_depth(R["cap"])
    thin_fail = ~(np.isfinite(mid) & (mid >= 0.30 - TOL) & np.isfinite(cv))
    deep_fail = ~(np.isfinite(deep) & (deep >= 0.60 - TOL))
    if frame_type == "acetate":
        fail = deep_fail
    elif frame_type == "metal":
        fail = thin_fail
    elif frame_type == "semi_rimless":
        held = np.isfinite(mid) & (mid >= -1.0) & (np.isfinite(cv) | (np.isfinite(f05) & (f05 >= -1.0)))
        fail = held & ~(mid >= 0.30 - TOL)
    elif frame_type == "browline":
        inr = np.zeros(len(F["s"]), bool)
        for a, b in ranges:
            inr |= (F["Pm"][:, 0] >= a) & (F["Pm"][:, 0] <= b)
        lay = instrument_layered(F, R, M.scenes(parts))
        fail = np.where(inr & lay, deep_fail, thin_fail)
        return {"F": F, "fail": fail, "in_range": inr, "layered": lay}
    else:
        raise ValueError(frame_type)
    return {"F": F, "fail": fail}


def moved_nodes_records(C: "Case", rid: str, title: str, b0: dict, moved_raw, V0: np.ndarray, before_glb: Path,
                        lens_node: str, frame_type: str, ranges=None):
    """Two stop-rule records per seat call, both independent of the planner's own FAIL list (which defines
    expected_raw): (core) every moved front-rim node is a node of a point the CHECK's verdict failed before the call;
    (instrument) every moved node's nearest instrument station on the before-GLB fails by the instrument's own rule,
    or one of its two neighbours does (a run end: the node owns points between stations)."""
    loop_raw = np.asarray(b0["loop_raw"], int)
    mv = set(np.asarray(list(moved_raw), int).tolist())
    moved_nodes = np.array([k for k, r in enumerate(loop_raw) if int(r) in mv], int)
    fail_chk = set(failing_nodes_of(b0, "verdict_check").tolist())
    outside = [int(k) for k in moved_nodes if int(k) not in fail_chk]
    C.add(f"{rid}.moved_set.nodes_failing_by_check", f"{title}: (stop rule) every moved node failed the check's "
          "verdict before the call", "moved front-rim nodes subset of the nodes of check-FAIL points (before-state)",
          "core", {"moved_nodes": int(len(moved_nodes)), "check_failing_nodes": len(fail_chk),
                   "moved_not_failing": outside[:20], "moved_not_failing_n": len(outside)}, not outside)
    if not len(moved_nodes):
        C.add(f"{rid}.moved_set.nodes_failing_by_instrument", f"{title}: (stop rule) every moved node fails by the "
              "instrument on the before-GLB", "nearest station (or a neighbour) FAIL", "instrument",
              {"moved_nodes": 0}, None, note="not applicable: nothing moved")
        return
    I = instrument_failing(before_glb, lens_node, frame_type, ranges)
    F, fail = I["F"], I["fail"]
    order = np.argsort(F["s"])
    rank = np.empty(len(order), int)
    rank[order] = np.arange(len(order))
    nearest, at_end, bad = 0, 0, []
    for k in moved_nodes:
        p = V0[loop_raw[k]]
        j = int(np.argmin(np.linalg.norm(F["Pm"] - p, axis=1)))
        if fail[j]:
            nearest += 1
            continue
        r = rank[j]
        nb = [order[(r - 1) % len(order)], order[(r + 1) % len(order)]]
        if fail[nb].any():
            at_end += 1
        else:
            bad.append({"node": int(k), "xyz": r4(p), "station_mm": r4(float(np.linalg.norm(F["Pm"][j] - p)))})
    meas_ = {"moved_nodes": int(len(moved_nodes)), "nearest_fails": nearest, "run_end": at_end,
             "violations": bad[:20], "violations_n": len(bad), "stations": int(len(fail)),
             "stations_failing": int(fail.sum())}
    if "layered" in I:
        cy = 0.5 * (V0[loop_raw, 1].min() + V0[loop_raw, 1].max())
        meas_["deep_rule_stations"] = int((I["in_range"] & I["layered"]).sum())
        meas_["in_range_not_layered"] = int((I["in_range"] & ~I["layered"]).sum())
        meas_["failing_below_centre"] = int((fail & (F["Pm"][:, 1] < cy)).sum())
    C.add(f"{rid}.moved_set.nodes_failing_by_instrument", f"{title}: (stop rule) every moved node fails by the "
          "instrument on the before-GLB", "nearest instrument station FAIL, or a neighbour FAIL (run end)",
          "instrument", meas_, not bad)


# ======================================================================================================= P2
def grade_p2() -> dict:
    C = Case("P2", "FT1119 q001 browline: hide_eyewire + seat_lens(browline), export")
    o = out("ft")
    a0, am, a1 = arr("ft", "a0"), arr("ft", "amid"), arr("ft", "a1")
    gb, ga = glb("ft", "before"), glb("ft", "after")
    host_b = SC.check_glb(gb, frame_type="browline", hidden_x_range_mm=FT_ALL_RANGES, stations_table=True)
    host_a = SC.check_glb(ga, stations_table=True)
    C.info["host_check_after"] = {"ok": host_a["ok"], "reasons": host_a["reasons"]}
    C.info["tool"] = {k: {kk: o[k].get(kk) for kk in ("status", "code", "lift_max_mm", "eyewire_vertices_moved",
                                                       "lens_nodes_moved", "slope_max", "section_area_rel_max",
                                                       "moved_vertices", "seconds")}
                      for k in ("hide_Left", "hide_Right", "seat_Left", "seat_Right")}
    sides = (("Left", 0, 2), ("Right", 1, 3))
    inst = {}
    for side, li, ei in sides:
        lens = f"Lens_{side}"
        V, T, parts = inst_lens(ga, lens)
        full = IG.analyse(V, T, [(n, pv, pt) for n, pv, pt in parts], 1.0, DEPTHS, full_grid=True)
        inst[side] = full
    # --- top sector (§10 P2 bullet 1)
    for side, li, ei in sides:
        L = st_arrays(check_lens(host_a, f"Lens_{side}"))
        top = L["sector"] == "top"
        enc = float(np.mean(L["cls"][top] == "ENCLOSED"))
        C.add(f"P2.top_whole_edge_inside.{side}", "top whole edge inside >=98% [100%]", ">= 0.98", "core",
              {"share": r4(enc), "top_points": int(top.sum())}, enc >= 0.98)
        rows = inst[side]["rows"]
        trow = [r for r in rows if r.get("sector") == "top"]
        share = float(np.mean([r["rung_inside_share"] == 1.0 for r in trow])) if trow else 0.0
        C.add(f"P2.top_whole_edge_inside.{side}", "top whole edge inside >=98% [100%]", ">= 0.98", "instrument",
              {"share": r4(share), "top_stations": len(trow)}, share >= 0.98)
        mind = np.nanmin(np.where(np.isfinite(L["cap"][top]), L["cap"][top], np.nan), 1)
        p5 = pct(mind, 5)
        C.add(f"P2.top_min_depth_p5.{side}", "top min-over-depth capture p5 >= 0.55 (instrument grid 0.05) [0.63-0.64]",
              ">= 0.55", "core", {"p5": p5, "p50": pct(mind, 50)}, p5 is not None and p5 >= 0.55)
        ktop = np.array([i for i, r in enumerate(rows) if r.get("sector") == "top"])
        cmin9 = np.array([np.nan if rows[i]["cap_min"] is None else rows[i]["cap_min"] for i in ktop], float)
        cg = inst[side]["cap_graded"][ktop]
        cg_min = np.where(np.isfinite(cg).all(1), np.nanmin(np.where(cg >= 90, np.nan, cg), 1), np.nan)
        p5a, p5b = pct(cmin9, 5), pct(cg_min, 5)
        C.add(f"P2.top_min_depth_p5.{side}", "top min-over-depth capture p5 >= 0.55 (instrument grid 0.05) [0.63-0.64]",
              ">= 0.55", "instrument", {"grid_9_depths_p5": p5a, "refined_5_depths_p5": p5b},
              p5a is not None and p5b is not None and p5a >= 0.55 and p5b >= 0.55)
        mid = pct(L["cap"][top, 2], 50)
        C.add(f"P2.top_mid_p50.{side}", "top mid-depth p50 0.75-1.00 [0.80]", "0.75..1.00", "core", {"p50": mid},
              mid is not None and 0.75 <= mid <= 1.00)
        m9 = np.array([np.nan if rows[i]["cap_mid"] is None else rows[i]["cap_mid"] for i in ktop], float)
        ma, mb = pct(m9, 50), pct(cg[:, 2], 50)
        C.add(f"P2.top_mid_p50.{side}", "top mid-depth p50 0.75-1.00 [0.80]", "0.75..1.00", "instrument",
              {"grid_p50": ma, "refined_p50": mb}, all(v is not None and 0.75 <= v <= 1.0 for v in (ma, mb)))
    # --- the check fails before, passes after
    bb, ba = o["check_before"], o["check_after"]
    C.add("P2.check_fails_before_passes_after", "the check FAILS on the input with the same ranges and PASSES after",
          "before FAIL, after PASS", "core", {"blender_before_ok": bb["ok"], "blender_after_ok": ba["ok"],
                                              "host_before_ok": host_b["ok"], "host_after_ok": host_a["ok"],
                                              "host_after_frame_type_source": [L["frame_type_source"] for L in
                                                                               host_a["lenses"]],
                                              "before_reasons": host_b["reasons"]},
          (not bb["ok"]) and ba["ok"] and (not host_b["ok"]) and host_a["ok"])
    # --- band (instrument: the designers' eyewire-band instrument; core: buried_band at the check's graded stations)
    band = {}
    for side, li, ei in sides:
        for when, path in (("before", gb), ("after", ga)):
            V, T, _ = inst_lens(path, f"Lens_{side}")
            brow = inst_part(path, f"Brow_{side}")
            ew = inst_part(path, f"Eyewire_{side}")
            band[(side, when)] = IG.eyewire_band(V, T, brow, ew)["rows"]
    core_band = {w: core_bands(p, "browline", FT_ALL_RANGES) for w, p in (("before", gb), ("after", ga))}
    for side, li, ei in sides:
        rb = band[(side, "before")]
        fb = max(r["front_mm"] for r in rb) if rb else 0.0
        C.add(f"P2.check_fails_before.{side}", "the check FAILS on the input (the defect is there: eyewire band "
              "visible under the brow)", "instrument front band max > 0.05 on the input", "instrument",
              {"upper_stations": len(rb), "front_max_mm": r4(fb), "front_share_gt_0.05":
               r4(np.mean([r["front_mm"] > 0.05 for r in rb])) if rb else None}, fb > 0.05)
        ra = band[(side, "after")]
        ok_share = float(np.mean([(r["front_mm"] <= 0.05) and (r["back_mm"] <= 0.05) for r in ra])) if ra else 0.0
        fmax = max((r["front_mm"] for r in ra), default=0.0)
        bmax = max((r["back_mm"] for r in ra), default=0.0)
        amax = max((r["outside_mm2"] for r in ra), default=0.0)
        rng = FT_RANGES[side][0]
        in_r = [r for r in ra if rng[0] <= r["x"] <= rng[1]]
        C.add(f"P2.band_share.{side}", "eyewire band <= 0.05 mm at >= 98% of upper stations", ">= 0.98", "instrument",
              {"share": r4(ok_share), "upper_stations": len(ra), "in_ranges": len(in_r)}, ok_share >= 0.98 and len(ra) > 0)
        C.add(f"P2.band_worst.{side}", "band worst <= 0.20 front / 0.34 back [0]", "front <= 0.20, back <= 0.34",
              "instrument", {"front_max_mm": r4(fmax), "back_max_mm": r4(bmax)}, fmax <= 0.20 and bmax <= 0.34)
        C.add(f"P2.eyewire_area_outside.{side}", "eyewire area outside the brow 0.00 mm2",
              "<= 0.005 mm2 (0.00 at two decimals)", "instrument", {"max_mm2": r4(amax)}, amax <= 0.005)
        cb = core_band["after"][f"Lens_{side}"]
        share = float(np.mean((cb["front"] <= 0.05) & (cb["back"] <= 0.05))) if len(cb["front"]) else 0.0
        C.add(f"P2.band_share.{side}", "eyewire band <= 0.05 mm at >= 98% of upper stations", ">= 0.98", "core",
              {"share": r4(share), "graded_stations": int(len(cb["front"]))}, share >= 0.98 and len(cb["front"]) > 0)
        cf = float(cb["front"].max()) if len(cb["front"]) else 0.0
        cbk = float(cb["back"].max()) if len(cb["back"]) else 0.0
        C.add(f"P2.band_worst.{side}", "band worst <= 0.20 front / 0.34 back [0]", "front <= 0.20, back <= 0.34", "core",
              {"front_max_mm": r4(cf), "back_max_mm": r4(cbk)}, cf <= 0.20 and cbk <= 0.34)
        ca = float(cb["area"].max()) if len(cb["area"]) else 0.0
        C.add(f"P2.eyewire_area_outside.{side}", "eyewire area outside the brow 0.00 mm2",
              "<= 0.005 mm2 (0.00 at two decimals)", "core", {"max_mm2": r4(ca)}, ca <= 0.005)
    # --- section area, lift, below centre, outside unchanged
    for side, li, ei in sides:
        h = o[f"hide_{side}"]
        C.add(f"P2.section_area.{side}", "eyewire section area +-1%", "<= 0.01", "core",
              {"rel_max": h.get("section_area_rel_max")}, (h.get("section_area_rel_max") or 1) <= 0.01)
        Vb, Tb, _ = inst_lens(gb, f"Lens_{side}")
        F = M.frames(Vb, Tb, 1.0)
        rng = FT_RANGES[side][0]
        idx = [k for k in range(len(F["s"])) if F["u"][k, 1] >= 0.5 and rng[0] <= F["Pm"][k, 0] <= rng[1]]
        e0 = IG.build_scene(*inst_part(gb, f"Eyewire_{side}"))
        e1 = IG.build_scene(*inst_part(ga, f"Eyewire_{side}"))
        A0 = M.tube_sections(F, idx, e0)["area_mm2"]
        A1 = M.tube_sections(F, idx, e1)["area_mm2"]
        ok = np.isfinite(A0) & np.isfinite(A1) & (A0 > 0)
        rel = np.abs(A1[ok] / A0[ok] - 1.0)
        C.add(f"P2.section_area.{side}", "eyewire section area +-1%", "<= 0.01", "instrument",
              {"rel_max": r4(rel.max()) if len(rel) else None, "stations": int(ok.sum()),
               "area_p50_mm2": r4(np.median(A0[ok])) if ok.any() else None},
              bool(len(rel)) and rel.max() <= 0.01,
              note="cross-section perpendicular to the tube's own axis (centroids at the neighbouring 1 mm "
                   "stations), before and after, in the input lens's station planes")
        d_ew = np.linalg.norm(a1[f"o{ei}_V"] - a0[f"o{ei}_V"], axis=1)
        C.add(f"P2.lift.{side}", "lift <= 1.1 mm [1.023]", "<= 1.1", "core", {"lift_max_mm": h.get("lift_max_mm")},
              (h.get("lift_max_mm") or 9) <= 1.1)
        C.add(f"P2.lift.{side}", "lift <= 1.1 mm [1.023]", "<= 1.1", "arrays", {"eyewire_max_move_mm": r4(d_ew.max())},
              d_ew.max() <= 1.1)
        gV0, gV1 = inst_scene(gb)[f"Eyewire_{side}"]["V"], inst_scene(ga)[f"Eyewire_{side}"]["V"]
        lift_g = float(np.linalg.norm(gV1 - gV0, axis=1).max()) if gV0.shape == gV1.shape else None
        C.add(f"P2.lift.{side}", "lift <= 1.1 mm [1.023]", "<= 1.1", "instrument", {"glb_eyewire_max_move_mm": r4(lift_g)},
              lift_g is not None and lift_g <= 1.1)
        yl = a0[f"o{li}_V"][:, 1]
        cy = 0.5 * (yl.min() + yl.max())
        below_l = (np.any(a0[f"o{li}_co"] != a1[f"o{li}_co"], axis=1) & (a0[f"o{li}_V"][:, 1] < cy)).sum()
        below_e = (np.any(a0[f"o{ei}_co"] != a1[f"o{ei}_co"], axis=1) & (a0[f"o{ei}_V"][:, 1] < cy)).sum()
        C.add(f"P2.below_centre.{side}", "no vertex below the lens centre moved", "0", "arrays",
              {"lens_vertices": int(below_l), "eyewire_vertices": int(below_e), "centre_y_mm": r4(cy)},
              below_l == 0 and below_e == 0)
        C.add(f"P2.below_centre.{side}", "no vertex below the lens centre moved", "0", "core",
              h.get("moved_below_centre"), h.get("moved_below_centre") == {"eyewire_vertices": 0, "lens_nodes": 0})
        # outside the ranges (+ the 3 mm ramp) or below the centre: bit-identical vertices, unchanged capture
        out_sets = []
        for i in (li, ei):
            Vv = a0[f"o{i}_V"]
            far = (Vv[:, 0] < rng[0] - 3.0) | (Vv[:, 0] > rng[1] + 3.0) | (Vv[:, 1] < cy)
            ch = np.any(a0[f"o{i}_co"] != a1[f"o{i}_co"], axis=1) & far
            out_sets.append((int(far.sum()), int(ch.sum())))
        C.add(f"P2.outside_bit_identical.{side}", "outside the ranges: those vertices bit-identical",
              "0 changed (outside = below the lens centre, or > 3 mm (the ramp) outside the x-range)", "arrays",
              {"lens_outside_changed": out_sets[0], "eyewire_outside_changed": out_sets[1]},
              out_sets[0][1] == 0 and out_sets[1][1] == 0)
    for side, li, ei in sides:
        rng = FT_RANGES[side][0]
        cb_ = core_state(gb, "browline", FT_ALL_RANGES)[f"Lens_{side}"]
        ca_ = core_state(ga, "browline", FT_ALL_RANGES)[f"Lens_{side}"]
        yl = a0[f"o{li}_V"][:, 1]
        cy = 0.5 * (yl.min() + yl.max())
        key_b = {tuple(np.round(p, 9)): k for k, p in enumerate(cb_["P"])}
        dd, dd_all, n, worst = [], [], 0, None
        for k, p in enumerate(ca_["P"]):
            j = key_b.get(tuple(np.round(p, 9)))
            if j is None or ca_["in_range"][k] or cb_["in_range"][j]:
                continue
            d = abs(ca_["cap"][k, 2] - cb_["cap"][j, 2])
            dd_all.append(d)
            if worst is None or d > worst[0]:
                worst = (d, p)
            if p[1] < cy or p[0] < rng[0] - 3.0 or p[0] > rng[1] + 3.0:
                n += 1
                dd.append(d)
        sec = {}
        for s_ in ("bottom", "nasal"):
            mb = (cb_["sector"] == s_) & ~cb_["in_range"]
            ma = (ca_["sector"] == s_) & ~ca_["in_range"]
            sec[s_] = [pct(cb_["cap"][mb, 2], 50), pct(ca_["cap"][ma, 2], 50)]
        dmax = float(np.nanmax(dd)) if dd else None
        ramp_core = core_ramp(cb_, ca_, rng, cy)
        C.add(f"P2.outside_capture_unchanged.{side}", "outside the ranges capture unchanged (0.40 bottom / 0.35 nasal)",
              "|delta c(0.5)| <= 1e-4 mm at every station outside (below the lens centre, or > 3 mm (the ramp) "
              "outside the x-range: the set whose vertices are graded bit-identical)", "core",
              {"stations_compared": n, "max_abs_delta_mm": r4(dmax), "mid_p50_before_after": sec,
               "all_unmoved_not_in_range_stations": {"n": len(dd_all), "max_abs_delta_mm": r4(max(dd_all))
                                                     if dd_all else None,
                                                     "worst_at_xyz": r4(worst[1]) if worst else None},
               "excluded_ramp_stations": ramp_core},
              n > 0 and dmax is not None and dmax <= 1e-4,
              note="READING (owner to confirm): 'outside the ranges' excludes the 3 mm lift ramp beyond each x-range "
                   "end (§6 step 4 moves the eyewire and the held lens there, so the literal reading cannot hold). "
                   "'excluded_ramp_stations' records what changes inside that ramp above the lens centre, both ends "
                   "of each delta (min, max): at MOVED stations (lens edge moved by the lift and the seat) the "
                   "capture rises at most stations, but the core's min also goes NEGATIVE there (mid-depth and "
                   "min-over-depth): the core rebuilds its frames on the moved lens, so before and after are read "
                   "on differently oriented lines; the instrument's record, on fixed station lines, shows the "
                   "actual change. At unmoved stations the frame re-orients too (its normal averages cap faces "
                   "within 5 mm, some of them moved), e.g. 0.41 deg and 0.00016 mm at x -57.29")
        Vb, Tb, pb = inst_lens(gb, f"Lens_{side}")
        Va, Ta, pa = inst_lens(ga, f"Lens_{side}")
        F = M.frames(Vb, Tb, 1.0)
        yl = a0[f"o{li}_V"][:, 1]
        cy = 0.5 * (yl.min() + yl.max())
        far = (F["Pm"][:, 1] < cy) | (F["Pm"][:, 0] < rng[0] - 3.0) | (F["Pm"][:, 0] > rng[1] + 3.0)
        idx = np.flatnonzero(far)
        Fs = {k: (v[idx] if isinstance(v, np.ndarray) and len(v) == len(F["s"]) else v) for k, v in F.items()}
        R0 = M.rows(Fs, F["scene"], M.scenes(pb), [0.5])
        R1 = M.rows(Fs, IG.build_scene(Va, Ta), M.scenes(pa), [0.5])
        d = np.abs(R1["cap"][:, 0] - R0["cap"][:, 0])
        ok = np.isfinite(d)
        bott = Fs["sector"] == "bottom"
        ramp_inst = instrument_ramp(F, pb, Va, Ta, pa, rng, cy)
        C.add(f"P2.outside_capture_unchanged.{side}", "outside the ranges capture unchanged (0.40 bottom / 0.35 nasal)",
              "|delta c(0.5)| <= 1e-4 mm at the same stations", "instrument",
              {"stations": int(len(idx)), "max_abs_delta_mm": r4(d[ok].max()) if ok.any() else None,
               "bottom_mid_p50_before_after": [pct(R0["cap"][bott, 0], 50), pct(R1["cap"][bott, 0], 50)],
               "undefined": int((~ok).sum()), "excluded_ramp_stations": ramp_inst},
              ok.any() and d[ok].max() <= 1e-4 and ok.all(),
              note="same READING as the core record (owner to confirm); 'excluded_ramp_stations' = the instrument's "
                   "1 mm stations in the 3 mm ramp above the lens centre, before/after on the same station lines, "
                   "'moved' where the lens edge on the mid-depth line moved")
        C.info.setdefault("ramp_exclusion", {})[side] = {"core": ramp_core, "instrument": ramp_inst}
    # --- rim split, outline, counts, mirror, spec
    for side, li, ei in sides:
        L = check_lens(host_a, f"Lens_{side}")
        rs = L.get("rim_split_deg") or {}
        p5 = min(rs.get("front", {}).get("p5", -1), rs.get("back", {}).get("p5", -1))
        p95 = max(rs.get("front", {}).get("p95", 999), rs.get("back", {}).get("p95", 999))
        C.add(f"P2.rim_split.{side}", "GLB rim split p5 >= 75 deg, p95 <= 105 deg [78.2 / 101.8]", "p5 >= 75, p95 <= 105",
              "core", {"front": rs.get("front"), "back": rs.get("back")}, p5 >= 75 and p95 <= 105)
        nd = inst_scene(ga)[f"Lens_{side}"]
        ir = M.rim_split(nd["V"], nd["T"], nd["N"])
        C.add(f"P2.rim_split.{side}", "GLB rim split p5 >= 75 deg, p95 <= 105 deg [78.2 / 101.8]", "p5 >= 75, p95 <= 105",
              "instrument", ir, ir["both"][0] >= 75 and ir["both"][2] <= 105)
        summ = L["body"]
        w, hgt = summ["outline_mm"]
        per = summ["front_loop_mm"]
        C.add(f"P2.outline.{side}", "outline height 36.0-36.6 mm [36.35], width <= 47.5 [47.40], perimeter <= 140.5 "
              "[140.2]", "36.0 <= h <= 36.6, w <= 47.5, perimeter <= 140.5", "core",
              {"height": r4(hgt), "width": r4(w), "perimeter": r4(per)}, 36.0 <= hgt <= 36.6 and w <= 47.5 and per <= 140.5)
        Va, Ta, _ = inst_lens(ga, f"Lens_{side}")
        lz = IG.lens_structure(Va, Ta)
        FL = lz["Vw"][lz["fl"]]
        iw, ih = float(np.ptp(FL[:, 0])), float(np.ptp(FL[:, 1]))
        ip = float(np.linalg.norm(np.roll(FL, -1, 0) - FL, axis=1).sum())
        C.add(f"P2.outline.{side}", "outline height 36.0-36.6 mm [36.35], width <= 47.5 [47.40], perimeter <= 140.5 "
              "[140.2]", "36.0 <= h <= 36.6, w <= 47.5, perimeter <= 140.5", "instrument",
              {"height": r4(ih), "width": r4(iw), "perimeter": r4(ip)}, 36.0 <= ih <= 36.6 and iw <= 47.5 and ip <= 140.5)
        nl = len(moved_rows(a0, a1, li))
        ne = len(moved_rows(a0, a1, ei))
        C.add(f"P2.lens_moved.{side}", "lens vertices moved <= 100 per lens [74]", "<= 100", "arrays",
              {"raw_vertices_moved": nl, "by_hide": len(moved_rows(a0, am, li)), "by_seat": len(moved_rows(am, a1, li))},
              nl <= 100)
        C.add(f"P2.eyewire_moved.{side}", "eyewire vertices moved <= 460 [444]", "<= 460", "arrays",
              {"raw_vertices_moved": ne}, ne <= 460)
        for what, node, lim in (("lens", f"Lens_{side}", 100), ("eyewire", f"Eyewire_{side}", 460)):
            w0 = M.weld_exact(inst_scene(gb)[node]["V"])[0]
            w1 = M.weld_exact(inst_scene(ga)[node]["V"])[0]
            s1 = {tuple(p) for p in w1.tolist()}
            nmov = sum(1 for p in w0.tolist() if tuple(p) not in s1)
            C.add(f"P2.{what}_moved.{side}", f"{what} vertices moved <= {lim}", f"<= {lim}", "instrument",
                  {"glb_positions_moved": nmov, "glb_positions": len(w0)}, nmov <= lim)
        exp = set(o[f"ns_mid_{side}"]["bodies"][0]["expected_raw"])
        mv = set(moved_rows(am, a1, li).tolist())
        C.add(f"P2.seat_moves_only_failing_nodes.{side}", "(stop rule) seat_lens moves only the band of FAIL nodes "
              "not BLOCKED, recomputed from its before-state (after the lift)", "moved == expected", "arrays",
              {"moved": len(mv), "expected": len(exp), "moved_outside_expected": len(mv - exp),
               "expected_not_moved": len(exp - mv)}, mv == exp)
        moved_nodes_records(C, f"P2.seat.{side}", f"seat_lens(Lens_{side}, browline)", o[f"ns_mid_{side}"]["bodies"][0],
                            mv, am[f"o{li}_V"], glb("ft", "mid"), f"Lens_{side}", "browline", FT_ALL_RANGES)
    dL = mirror_pair(a0["o0_V"], a0["o1_V"], a1["o0_V"], a1["o1_V"])
    dE = mirror_pair(a0["o2_V"], a0["o3_V"], a1["o2_V"], a1["o3_V"])
    C.add("P2.mirror", "left = mirrored right within 0.01 mm", "<= 0.01 (lenses; eyewires reported)", "arrays",
          {"lens_max_mm": r4(dL), "eyewire_max_mm": r4(dE)}, dL <= 0.01)
    vs = validate_spec(ga)
    C.add("P2.validate_spec", "validate_spec passes", "ok", "spec", {"ok": vs["ok"], "errors": vs.get("errors")},
          bool(vs["ok"]))
    return C.result()


def core_state(path: Path, frame_type: str, ranges=None, lower_edge=None) -> dict:
    """The core's measurement on a GLB the way check_glb measures it (open3d backend), per lens body: frames,
    capture, rule values, in-range flags."""
    g = load_glb(path)
    nodes, warnings, skipped = SC.read_nodes(g)
    col = CK.collect(nodes, skipped, warnings, SC.factory("open3d"))
    outd = {}
    ft = rules.frame_type(frame_type, lower_edge)
    for body in col["bodies"]:
        parts = CK.parts_near(body, col["solids"])
        be = SC.make_backend("open3d", [(c.V, c.T) for _, c in parts] + [(body.V, body.T)])
        fr, meas = stations.measure_body(body, be, len(parts))
        rl = rules.rule_values(ft, meas, fr.P[:, 0], ranges)
        outd[body.name] = {"P": fr.P, "cap": meas.cap, "sector": fr.sector, "in_range": np.asarray(rl.in_range),
                           "value": rl.value, "fr": fr, "meas": meas, "rl": rl, "be": be, "nparts": len(parts),
                           "parts": [c.name for _, c in parts], "body": body}
    return outd


def core_bands(path: Path, frame_type: str, ranges) -> dict:
    """check_body's buried band at its graded stations (in-range band candidates), per lens body."""
    outd = {}
    for name, s in core_state(path, frame_type, ranges).items():
        band = stations.buried_band(s["fr"], s["meas"], s["be"], s["nparts"], held_from=~s["in_range"])
        g = band.candidate & s["in_range"]
        outd[name] = {"front": band.front_mm[g], "back": band.back_mm[g], "area": band.area_outside_mm2[g],
                      "x": s["P"][g, 0]}
    return outd


def in_ramp(P: np.ndarray, rng, cy: float) -> np.ndarray:
    """Stations above the lens centre within the 3 mm lift ramp beyond either end of the x-range (§6 step 4)."""
    x, y = P[:, 0], P[:, 1]
    return (y >= cy) & (((x >= rng[0] - 3.0) & (x < rng[0])) | ((x > rng[1]) & (x <= rng[1] + 3.0)))


def _min_depth(cap: np.ndarray) -> np.ndarray:
    c = np.where(np.asarray(cap, float) >= 90, np.nan, np.asarray(cap, float))
    out = np.full(len(c), np.nan)
    ok = np.isfinite(c).any(1)
    out[ok] = np.nanmin(c[ok], 1)
    return out


def _ramp_summary(P, moved, dmid, dmin) -> dict:
    out = {"stations": int(len(moved))}
    for key, m in (("moved", moved), ("unmoved", ~moved)):
        if not m.any():
            out[key] = {"n": 0}
            continue
        out[key] = {"n": int(m.sum()), "x_min_max": r4([P[m, 0].min(), P[m, 0].max()]),
                    "delta_mid_min_max_mm": r4([np.nanmin(dmid[m]), np.nanmax(dmid[m])])
                    if np.isfinite(dmid[m]).any() else None,
                    "delta_min_over_depth_min_max_mm": r4([np.nanmin(dmin[m]), np.nanmax(dmin[m])])
                    if np.isfinite(dmin[m]).any() else None}
    return out


def core_ramp(cb: dict, ca: dict, rng, cy: float) -> dict:
    """The core's capture before / after at the excluded ramp stations: each before station joined to the after
    station in its own station plane (smallest offset along its t within 2 mm; unmoved stations join exactly)."""
    Pb, Pa, tb = cb["P"], ca["P"], cb["fr"].t
    kk, jj = [], []
    for k in np.flatnonzero(in_ramp(Pb, rng, cy)):
        d = Pa - Pb[k]
        dist = np.linalg.norm(d, axis=1)
        cand = np.flatnonzero(dist < 2.0)
        if len(cand):
            kk.append(k)
            jj.append(int(cand[np.argmin(np.abs(d[cand] @ tb[k]))]))
    kk, jj = np.array(kk, int), np.array(jj, int)
    if not len(kk):
        return {"stations": 0}
    moved = np.linalg.norm(Pa[jj] - Pb[kk], axis=1) > 1e-6
    dmid = ca["cap"][jj, stations.MID] - cb["cap"][kk, stations.MID]
    dmin = _min_depth(ca["cap"][jj]) - _min_depth(cb["cap"][kk])
    return _ramp_summary(Pb[kk], moved, dmid, dmin)


def instrument_ramp(F: dict, pb, Va, Ta, pa, rng, cy: float) -> dict:
    """The instrument's capture before / after on the same station lines (frames of the input lens) at the ramp
    stations; 'moved' where the lens edge on the mid-depth line moved by > 1e-4 mm."""
    idx = np.flatnonzero(in_ramp(F["Pm"], rng, cy))
    if not len(idx):
        return {"stations": 0}
    Fs = {k: (v[idx] if isinstance(v, np.ndarray) and len(v) == len(F["s"]) else v) for k, v in F.items()}
    R0 = M.rows(Fs, F["scene"], M.scenes(pb), DEPTHS)
    R1 = M.rows(Fs, IG.build_scene(Va, Ta), M.scenes(pa), DEPTHS)
    moved = np.abs(R1["edge"][:, stations.MID] - R0["edge"][:, stations.MID]) > 1e-4
    dmid = R1["cap"][:, stations.MID] - R0["cap"][:, stations.MID]
    dmin = _min_depth(R1["cap"]) - _min_depth(R0["cap"])
    return _ramp_summary(F["Pm"][idx], moved, dmid, dmin)


# ======================================================================================================= P3
def volume_sum(a0, a1, i, b: dict) -> tuple[float, float]:
    """(volume gain of the raw lens mesh, sum g t ds over its nodes) with g = the front rim vertex's move along the
    node's u, t = the rung length, ds = half the two adjacent front-loop segments."""
    V0, V1, T = a0[f"o{i}_V"], a1[f"o{i}_V"], a0[f"o{i}_T"]
    gain = M.signed_volume(V1, T) - M.signed_volume(V0, T)
    if M.signed_volume(V0, T) < 0:
        gain = -gain
    lr = np.asarray(b["loop_raw"])
    pr = np.asarray(b["partner_raw"])
    npt = np.asarray(b["node_point"])
    U = np.asarray(b["point"]["u"], float)[npt]
    g = ((V1[lr] - V0[lr]) * U).sum(1)
    t = np.where(pr >= 0, np.linalg.norm(V0[lr] - V0[np.maximum(pr, 0)], axis=1), np.nan)
    P = V0[lr]
    seg = np.linalg.norm(np.roll(P, -1, 0) - P, axis=1)
    ds = 0.5 * (seg + np.roll(seg, 1))
    return float(gain), float(np.nansum(g * t * ds))


def grade_p3() -> dict:
    C = Case("P3", "Prada delivery: seat_lens(Lens L/R, [Front], 'acetate')")
    o = out("prada")
    a0, a1 = arr("prada", "a0"), arr("prada", "a1")
    gb, ga = glb("prada", "before"), glb("prada", "after")
    host_b = SC.check_glb(gb, frame_type="acetate", stations_table=True)
    host_a = SC.check_glb(ga, frame_type="acetate", stations_table=True)
    C.info["tool"] = {s: {k: o[f"seat_{s}"].get(k) for k in ("status", "code", "message", "moved_vertices")}
                      for s in ("L", "R")}
    C.info["tool_plan_not_written"] = {s: o[f"seat_{s}"].get("bodies") for s in ("L", "R")}
    C.info["host_check_after"] = {"ok": host_a["ok"], "reasons": host_a["reasons"]}
    refused = [s for s in ("L", "R") if o[f"seat_{s}"]["status"] == "refused"]
    if refused:
        C.notes.append("seat_lens REFUSED both lenses: " + "; ".join(f"{s}: {o[f'seat_{s}']['code']} "
                                                                    f"{o[f'seat_{s}']['message']}" for s in refused)
                       + ". Nothing was written, so every 'after' criterion is measured on the unchanged lens (the "
                         "refusal is WP4_DESIGN open item A, an owner decision; no constant was changed).")
    for s, i in (("L", 0), ("R", 1)):
        nm = PRADA[s]
        b0 = o[f"ns_before_{s}"]["bodies"][0]
        fn = failing_nodes_of(b0)
        x = np.abs(np.asarray(b0["point"]["P"], float)[np.asarray(b0["node_point"])[fn], 0]) if len(fn) else np.zeros(0)
        inb = ((x >= 10) & (x <= 14)) | ((x >= 57.4) & (x <= 59))
        C.add(f"P3.before_failing_nodes.{s}", "before ~93 of 355 nodes fail per lens [M]",
              "'~93' read as 84..102 (+-10%) of 355", "core", {"failing_nodes": int(len(fn)), "nodes": b0["nodes"]},
              84 <= len(fn) <= 102 and b0["nodes"] == 355)
        C.add(f"P3.before_failing_where.{s}", "the failing nodes lie at nasal |x| 10-14 and temporal |x| 57.4-59 [M]",
              "share of failing nodes in those |x| bands = 1.0", "core",
              {"share": r4(inb.mean()) if len(x) else None, "abs_x_min_max": r4([x.min(), x.max()]) if len(x) else None},
              bool(len(x)) and inb.all())
        Vb, Tb, pb = inst_lens(gb, nm)
        F = M.frames(Vb, Tb, 1.0)
        R0 = M.rows(F, F["scene"], M.scenes(pb), DEPTHS)
        mn = np.where(np.isfinite(R0["cap"]).all(1), np.nanmin(np.where(R0["cap"] >= 90, np.nan, R0["cap"]), 1), np.nan)
        fail = mn < 0.60 - TOL
        xi = np.abs(F["Pm"][fail, 0])
        inb_i = ((xi >= 10) & (xi <= 14)) | ((xi >= 57.4) & (xi <= 59))
        C.add(f"P3.before_failing_where.{s}", "the failing nodes lie at nasal |x| 10-14 and temporal |x| 57.4-59 [M]",
              "share of failing instrument stations in those |x| bands = 1.0", "instrument",
              {"failing_stations": int(fail.sum()), "stations": int(len(fail)), "share": r4(inb_i.mean()) if len(xi)
               else None}, bool(len(xi)) and inb_i.all())
        # ---- after
        L = st_arrays(check_lens(host_a, nm))
        vmin = float(np.nanmin(L["value"]))
        C.add(f"P3.after_min_all_depths.{s}", "after: every point >= 0.60 at all depths (instrument >= 0.55)",
              ">= 0.60 (the §4 step 2 verdict tolerance 0.005)", "core", {"min_mm": r4(vmin)}, vmin >= 0.60 - TOL)
        Va, Ta, pa = inst_lens(ga, nm)
        full = IG.analyse(Va, Ta, [(n_, v_, t_) for n_, v_, t_ in pa], 1.0, DEPTHS, full_grid=True)
        cg = full["cap_graded"]
        cmin = np.where(np.isfinite(cg).all(1), np.nanmin(np.where(cg >= 90, np.nan, cg), 1), np.nan)
        c9 = np.array([np.nan if r["cap_min"] is None else r["cap_min"] for r in full["rows"]], float)
        C.add(f"P3.after_min_all_depths.{s}", "after: every point >= 0.60 at all depths (instrument >= 0.55)", ">= 0.55",
              "instrument", {"refined_5_depths_min": r4(np.nanmin(cmin)), "grid_9_depths_min": r4(np.nanmin(c9)),
                             "undefined": int((~np.isfinite(cmin)).sum())},
              np.isfinite(cmin).all() and np.nanmin(cmin) >= 0.55 and np.nanmin(c9) >= 0.55)
        enc = float(np.mean(L["cls"] == "ENCLOSED"))
        cov = float(np.mean(np.isfinite(L["cover_front"]) & np.isfinite(L["cover_back"])
                            & (L["cover_front"] <= 6) & (L["cover_back"] <= 6)))
        C.add(f"P3.whole_edge_inside.{s}", "whole edge inside 100%", "1.0", "core", {"share": r4(enc)}, enc == 1.0)
        C.add(f"P3.front_back_covered.{s}", "front/back covered 100%", "1.0", "core", {"share": r4(cov)}, cov == 1.0)
        ri = float(np.mean([r["rung_inside_share"] == 1.0 for r in full["rows"]]))
        ci = float(np.mean([r["cover_front"] is not None and r["cover_back"] is not None for r in full["rows"]]))
        C.add(f"P3.whole_edge_inside.{s}", "whole edge inside 100%", "1.0", "instrument", {"share": r4(ri)}, ri == 1.0)
        C.add(f"P3.front_back_covered.{s}", "front/back covered 100%", "1.0", "instrument", {"share": r4(ci)}, ci == 1.0)
        # cover unchanged
        b1 = o[f"ns_after_{s}"]["bodies"][0]
        J = node_join(o[f"ns_before_{s}"], o[f"ns_after_{s}"])[0]
        cf0, cf1 = pts(b0, "cover_front"), pts(b1, "cover_front")
        cb0, cb1 = pts(b0, "cover_back"), pts(b1, "cover_back")
        dmax = 0.0
        for _, p0, p1, _, _ in J:
            for x0, x1 in ((cf0[p0], cf1[p1]), (cb0[p0], cb1[p1])):
                if np.isfinite(x0) != np.isfinite(x1):
                    dmax = np.inf
                elif np.isfinite(x0):
                    dmax = max(dmax, abs(x1 - x0))
        C.add(f"P3.cover_unchanged.{s}", "cover unchanged +-0.05", "<= 0.05 at every node", "core",
              {"max_abs_delta_mm": r4(dmax), "nodes": len(J)}, dmax <= 0.05)
        cc0 = M.corner_cover(F, F["scene"], M.scenes(pb))
        cc1 = M.corner_cover(F, IG.build_scene(Va, Ta), M.scenes(pa))
        dd = [np.abs(cc1[k] - cc0[k]) for k in ("front", "back")]
        undefined = sum(int((np.isfinite(cc1[k]) != np.isfinite(cc0[k])).sum()) for k in ("front", "back"))
        dm = max(float(np.nanmax(d)) if np.isfinite(d).any() else 0.0 for d in dd)
        C.add(f"P3.cover_unchanged.{s}", "cover unchanged +-0.05", "<= 0.05 at every station", "instrument",
              {"max_abs_delta_mm": r4(dm), "definedness_changes": undefined}, dm <= 0.05 and undefined == 0)
        mv = moved_rows(a0, a1, i)
        dmv = np.linalg.norm(a1[f"o{i}_V"] - a0[f"o{i}_V"], axis=1).max()
        # on a refused lens nothing was written: these four have nothing to measure (not applicable, never a pass)
        vac = s in refused and len(mv) == 0          # a refusal that moved anything is graded (and fails the stop rule)
        na = "not applicable: the tool refused this lens and wrote nothing" if vac else None

        def okna(ok):
            return None if vac else ok
        C.add(f"P3.max_delta.{s}", "max delta <= 0.45 mm [0.434]", "<= 0.45", "arrays", {"max_mm": r4(dmv)},
              okna(dmv <= 0.45), note=na)
        C.add(f"P3.moved.{s}", "moved vertices <= 200 per lens [186]", "<= 200", "arrays", {"moved": int(len(mv))},
              okna(len(mv) <= 200), note=na)
        exp = set(b0["expected_raw"])
        C.add(f"P3.moved_in_failing_nodes.{s}", "all moved vertices in failing nodes", "moved subset of the band of "
              "FAIL nodes (before-state)", "arrays", {"moved": int(len(mv)), "outside": len(set(mv.tolist()) - exp),
                                                      "expected_band": len(exp)},
              okna(set(mv.tolist()) <= exp), note=na)
        gain, expect = volume_sum(a0, a1, i, b0)
        okv = abs(gain - expect) <= 0.15 * abs(expect) if abs(expect) > 1e-9 else abs(gain) <= 1e-6
        C.add(f"P3.volume_gain.{s}", "volume gain within 15% of sum g t ds [~20 mm3]", "|gain - sum| <= 0.15 sum",
              "arrays", {"gain_mm3": r4(gain), "sum_g_t_ds_mm3": r4(expect)}, okna(okv), note=na)
        moved_nodes_records(C, f"P3.{s}", f"seat_lens({nm}, acetate)", b0, mv.tolist(), a0[f"o{i}_V"], gb, nm,
                            "acetate")
        rsd = check_lens(host_a, nm).get("rim_split_deg") or {}
        fp5 = (rsd.get("front") or {}).get("p5")
        C.add(f"P3.rim_split.{s}", "GLB rim split p5 >= 75 deg", ">= 75 (front loop)", "core", rsd,
              fp5 is not None and fp5 >= 75)
        nd = inst_scene(ga)[nm]
        ir = M.rim_split(nd["V"], nd["T"], nd["N"])
        C.add(f"P3.rim_split.{s}", "GLB rim split p5 >= 75 deg", ">= 75 (front loop)", "instrument", ir,
              ir["front"][0] >= 75)
    d0, d1 = o["digest0"][PRADA["etch"]]["digest"], o["digest1"][PRADA["etch"]]["digest"]
    C.add("P3.etch", "etch bit-identical", "digest and raw coordinates identical", "arrays",
          {"digest_equal": d0 == d1, "raw_equal": bool(np.array_equal(a0["o2_co"], a1["o2_co"]))},
          d0 == d1 and np.array_equal(a0["o2_co"], a1["o2_co"]))
    ge0 = inst_scene(gb)[PRADA["etch"]]["V"]
    ge1 = inst_scene(ga)[PRADA["etch"]]["V"]
    C.add("P3.etch", "etch bit-identical", "GLB positions identical", "instrument",
          {"equal": bool(ge0.shape == ge1.shape and np.array_equal(ge0, ge1))},
          ge0.shape == ge1.shape and np.array_equal(ge0, ge1))
    C.info["host_check_before_failing"] = [{"lens": L["lens"], "verdicts": L["verdicts"]} for L in host_b["lenses"]]
    return C.result()


# ======================================================================================================= P4
def runs_of(mask, s, L):
    """Circular runs of True over points ordered by s; per point: arc distance to the nearer run end (nan outside)."""
    order = np.argsort(s)
    m = np.asarray(mask, bool)[order]
    ss = np.asarray(s, float)[order]
    delta = np.full(len(s), np.nan)
    from glasses3d.g3d.seat.plan import runs as _runs      # loop-run splitting only (no seating logic)
    for run in _runs(m):
        if len(run) == len(m):
            delta[order[run]] = np.inf
            continue
        a = (ss[run] - ss[run[0]]) % L
        b = (ss[run[-1]] - ss[run]) % L
        delta[order[run]] = np.minimum(a, b)
    return delta


def former_failures(o: dict, ok_inside, ok_end, allow_transition=False) -> dict:
    """Core: node points the check FAILED before, their value after (joined on the node's raw rim row); inside a run
    when the point lies >= 2 mm (the §4 run-end ramp) from the nearer end of its failing run."""
    b0, b1 = o["ns_before"]["bodies"][0], o["ns_after"]["bodies"][0]
    J = node_join(o["ns_before"], o["ns_after"])[0]
    vc0 = np.asarray(b0["point"]["verdict_check"])
    req0 = np.asarray(b0["point"]["required"])
    delta = pts(b0, "delta_run_end")
    v1 = pts(b1, "value")
    vc1 = np.asarray(b1["point"]["verdict_check"])
    inside, ends, trans, bad = [], [], 0, []
    for raw, p0, p1, _, _ in J:
        if not (req0[p0] and vc0[p0] == "FAIL"):
            continue
        if allow_transition and vc1[p1] == "TRANSITION":
            trans += 1
            continue
        if delta[p0] >= 2.0:
            inside.append(v1[p1])
            if not ok_inside(v1[p1]):
                bad.append(("inside", raw, r4(v1[p1])))
        else:
            ends.append(v1[p1])
            if not ok_end(v1[p1]):
                bad.append(("end", raw, r4(v1[p1])))
    return {"inside": np.array(inside, float), "ends": np.array(ends, float), "transition": trans, "bad": bad}


def grade_p4() -> dict:
    C = Case("P4", "synthetic fixtures (fixtures/seat_fixtures.py, built by blender/seat_build.py)")
    # ---------------- metal
    o = out("metal")
    a0, a1 = arr("metal", "a0"), arr("metal", "a1")
    ff = former_failures(o, lambda v: 0.44 <= v <= 0.47, lambda v: v >= 0.35)
    ins, en = ff["inside"], ff["ends"]
    C.add("P4.metal.inside_runs", "metal: former failures 0.44-0.47 inside runs", "0.44..0.47 at every node point "
          ">= 2 mm from its run's end", "core", {"n": int(len(ins)), "min_p50_max": r4([ins.min(), np.median(ins),
                                                                                       ins.max()]) if len(ins) else None,
                                                 "outside_band": [b for b in ff["bad"] if b[0] == "inside"][:10]},
          bool(len(ins)) and 0.44 <= ins.min() and ins.max() <= 0.47)
    C.add("P4.metal.run_ends", "metal: >= 0.35 at run ends", ">= 0.35", "core",
          {"n": int(len(en)), "min": r4(en.min()) if len(en) else None}, bool(len(en)) and en.min() >= 0.35)
    gb, ga = glb("metal", "before"), glb("metal", "after")
    Vb, Tb, pb = inst_lens(gb, "Lens_L")
    Va, Ta, pa = inst_lens(ga, "Lens_L")
    F = M.frames(Vb, Tb, 1.0)
    R0 = M.rows(F, F["scene"], M.scenes(pb), [0.5])
    R1 = M.rows(F, IG.build_scene(Va, Ta), M.scenes(pa), [0.5])
    cv0 = M.corner_cover(F, F["scene"], M.scenes(pb))["front"]
    cv1 = M.corner_cover(F, IG.build_scene(Va, Ta), M.scenes(pa))["front"]
    c0, c1 = R0["cap"][:, 0], R1["cap"][:, 0]
    fail0 = ~(np.isfinite(c0) & (c0 >= 0.30 - TOL) & np.isfinite(cv0))
    delta = runs_of(fail0, F["s"], F["L"])
    inside = fail0 & (delta >= 2.0)
    ends = fail0 & (delta < 2.0)
    vi, ve = c1[inside], c1[ends]
    C.add("P4.metal.inside_runs", "metal: former failures 0.44-0.47 inside runs", "0.44..0.47", "instrument",
          {"n": int(inside.sum()), "min_p50_max": r4([np.nanmin(vi), np.nanmedian(vi), np.nanmax(vi)]) if len(vi) else None},
          bool(len(vi)) and np.isfinite(vi).all() and 0.44 <= vi.min() and vi.max() <= 0.47)
    C.add("P4.metal.run_ends", "metal: >= 0.35 at run ends", ">= 0.35", "instrument",
          {"n": int(ends.sum()), "min": r4(np.nanmin(ve)) if len(ve) else None},
          bool(len(ve)) and np.isfinite(ve).all() and ve.min() >= 0.35)
    mv = set(moved_rows(a0, a1, 0).tolist())
    exp = set(o["ns_before"]["bodies"][0]["expected_raw"])
    ew_same = o["digest0"]["Eyewire"]["digest"] == o["digest1"]["Eyewire"]["digest"]
    C.add("P4.metal.all_else_bit_identical", "metal: all else bit-identical", "moved == band of FAIL nodes (before); "
          "eyewire digest unchanged", "arrays", {"moved": len(mv), "expected": len(exp), "outside": len(mv - exp),
                                                  "missing": len(exp - mv), "eyewire_unchanged": ew_same},
          mv == exp and ew_same)
    moved_nodes_records(C, "P4.metal", "seat_lens(metal fixture)", o["ns_before"]["bodies"][0], mv, a0["o0_V"], gb,
                        "Lens_L", "metal")
    b1 = o["ns_after"]["bodies"][0]
    req1 = np.asarray(b1["point"]["required"])
    cfr = np.asarray(b1["point"]["covered_front"])[req1]
    C.add("P4.metal.front_covered", "metal: front covered 100%", "1.0 of required points", "core",
          {"share": r4(cfr.mean())}, cfr.all())
    C.add("P4.metal.front_covered", "metal: front covered 100%", "1.0 of stations", "instrument",
          {"share": r4(np.isfinite(cv1).mean())}, np.isfinite(cv1).all())
    # narrow eyewire
    o = out("metal_narrow")
    b0 = o["ns_before"]["bodies"][0]
    top = np.asarray(b0["point"]["sector"]) == "top"
    base_fail = np.asarray(b0["point"]["verdict_check"]) == "FAIL"
    vp = np.asarray(b0["point"]["verdict_plan"])
    rs = np.asarray(b0["point"]["reason"])
    tf = top & base_fail
    ex = tf & (vp == "BLOCKED") & (rs == "EXITS_MATERIAL")
    reasons = {f"{v}/{r}": int(((vp == v) & (rs == r) & tf).sum()) for v, r in set(zip(vp[tf], rs[tf]))}
    C.add("P4.metal_narrow.exits_material", "metal, 0.5 mm radial eyewire: every top station EXITS_MATERIAL",
          "all failing top points BLOCKED(EXITS_MATERIAL)", "core",
          {"top_failing": int(tf.sum()), "exits_material": int(ex.sum()), "plan_verdict/reason": reasons,
           "tool": {k: o["seat1"].get(k) for k in ("status", "code", "message")}}, tf.any() and ex.sum() == tf.sum())
    d0, d1 = o["digest0"]["Lens_L"]["digest"], o["digest1"]["Lens_L"]["digest"]
    C.add("P4.metal_narrow.zero_moved", "metal, 0.5 mm radial eyewire: 0 moved", "digest unchanged, 0 moved", "arrays",
          {"digest_equal": d0 == d1, "moved_vertices": o["seat1"].get("moved_vertices")},
          d0 == d1 and not o["seat1"].get("moved_vertices"))
    grade_nylor(C)
    grade_crystal(C)
    grade_matrix(C)
    return C.result()


# ------------------------------------------------------------------------------------- core edge profiles
PROFILE_DV = 0.005           # mm between the profile lines along the corner-to-corner rung
PROFILE_PAD = 0.02           # mm kept clear of each corner


def core_profiles(path: Path, lens_node: str) -> list:
    """The profiled lens's edge as the CORE sees it on the exported GLB: check_glb's reading (read_nodes, collect,
    the lens body), the core's station frames (stations.station_frames) and backend (open3d), and the core's edge
    rule (stations.lens_edge: where the line along u leaves the lens, or the last lens interval ending within reach
    before the rung point, a groove). Per station: rung points every PROFILE_DV mm from the front to the back corner
    and the edge offset e along u from each (proof review round 2: the core records repeated the tool's plan)."""
    from glasses3d.g3d.seat.intervals import line_intervals
    nodes, warnings, skipped = SC.read_nodes(load_glb(path))
    col = CK.collect(nodes, skipped, warnings, SC.factory("open3d"))
    bodies = [b for b, n in zip(col["bodies"], col["body_node"]) if n["name"] == lens_node]
    if not bodies:
        raise KeyError(f"{lens_node}: no lens body in {path.name}")
    body = bodies[0]
    fr = stations.station_frames(body)
    be = SC.make_backend("open3d", [(body.V, body.T)])
    out = []
    idx = np.flatnonzero(fr.ok)
    O, D, Ax, own, fs = [], [], [], [], []
    for k in idx:
        t_edge = float(np.linalg.norm(fr.Pb[k] - fr.P[k]))
        f = np.arange(PROFILE_PAD, t_edge - PROFILE_PAD + 1e-9, PROFILE_DV) / t_edge
        O.append(fr.P[k] + f[:, None] * (fr.Pb[k] - fr.P[k]))
        D.append(np.repeat(fr.u[k][None], len(f), 0))
        Ax.append(np.repeat(fr.t[k][None], len(f), 0))
        own.append(np.full(len(f), k))
        fs.append(f)
    if not O:
        return out
    res = line_intervals(be, np.vstack(O), np.vstack(D), np.vstack(Ax))
    e_all = np.array([stations.lens_edge(iv[0]) if iv[0] is not None else np.nan for iv in res.intervals])
    own = np.concatenate(own)
    f_all = np.concatenate(fs)
    for k in idx:
        m = own == k
        rung = fr.Pb[k] - fr.P[k]
        f, e = f_all[m], e_all[m]
        X = fr.P[k] + f[:, None] * rung + e[:, None] * fr.u[k]
        Pm = 0.5 * (fr.P[k] + fr.Pb[k])
        out.append({"k": int(k), "xyz": fr.P[k], "f": f, "e": e, "t_edge": float(np.linalg.norm(rung)),
                    "xu": (X - Pm) @ fr.u[k], "xn": (X - Pm) @ fr.n[k]})
    return out


def core_groove_dims(pr: dict, depth: float = 0.60) -> dict | None:
    """Width (along the rung, between the points where the edge crosses half the depth), depth (deepest e inside)
    and centre offset (from the rung midpoint, along the rung) of the notch in one core profile."""
    f, e, L = pr["f"], pr["e"], pr["t_edge"]
    if not np.isfinite(e).all():
        return None
    inside = e < -depth / 2
    if not inside.any() or inside[0] or inside[-1]:
        return None
    i = np.flatnonzero(inside)
    i0, i1 = i[0], i[-1]

    def cross(a, b):
        return f[a] + (-depth / 2 - e[a]) / (e[b] - e[a]) * (f[b] - f[a])
    fa, fb = cross(i0 - 1, i0), cross(i1 + 1, i1)
    return {"width_mm": float((fb - fa) * L), "depth_mm": float(-e[i0:i1 + 1].min()),
            "centre_offset_mm": float((0.5 * (fa + fb) - 0.5) * L)}


def core_bevel_dims(pr: dict) -> dict | None:
    """V-bevel apex height (largest e) and the included angle between the two flank lines, each fitted (principal
    axis) to the profile points between 20% and 80% of the apex height, in the station plane (u, n)."""
    e, xu, xn = pr["e"], pr["xu"], pr["xn"]
    if not np.isfinite(e).all():
        return None
    k = int(np.argmax(e))
    h = float(e[k])
    if h <= 0:
        return None
    dirs = []
    for side in (np.arange(0, k), np.arange(k + 1, len(e))):
        m = side[(e[side] > 0.2 * h) & (e[side] < 0.8 * h)]
        if len(m) < 5:
            return None
        A = np.c_[xu[m], xn[m]]
        _, _, vt = np.linalg.svd(A - A.mean(0))
        d = vt[0]
        away = A[np.argmin(e[m])] - np.array([xu[k], xn[k]])
        dirs.append(d if d @ away > 0 else -d)
    return {"h_mm": h, "angle_deg": float(np.degrees(np.arccos(np.clip(dirs[0] @ dirs[1], -1.0, 1.0))))}


def grade_nylor(C: Case):
    for case in ("nylor", "nylor_nocord"):
        o = out(case)
        s1 = o["seat1"]
        C.info[f"{case}.tool"] = {k: s1.get(k) for k in ("status", "code", "moved_vertices", "profile",
                                                           "profile_plan")}
        C.info[f"{case}.profile_result"] = {k: v for k, v in (s1.get("profile_result") or {}).items()
                                             if k not in ("parts",)}
        gb, ga = glb(case, "before"), glb(case, "after")
        host = SC.check_glb(ga, stations_table=True)
        L = st_arrays(check_lens(host, "Lens_L"))
        req = L["required"]
        vals = L["value"][req]
        # distance (arc) of each required point to the nearest non-required point: run ends of the channel
        s_all = L["s"]
        loop = float(check_lens(host, "Lens_L")["loop_mm"])
        nr = s_all[~req]
        dist = np.array([np.min(np.minimum(np.abs(x - nr), loop - np.abs(x - nr))) if len(nr) else np.inf
                         for x in s_all[req]])
        inside = dist >= 2.0
        tag = "nylor" if case == "nylor" else "nylor (no cord)"
        if case == "nylor":
            C.add("P4.nylor.top_flats", "nylor: top >= 0.44 on the flats", ">= 0.44 at every required (channel) point",
                  "core", {"min": r4(vals.min()), "p5": pct(vals, 5), "points": int(req.sum()),
                           "min_inside_runs_ge_2mm_from_channel_end": r4(vals[inside].min()) if inside.any() else None,
                           "min_within_2mm_of_channel_end": r4(vals[~inside].min()) if (~inside).any() else None},
                  vals.min() >= 0.44)
        Va, Ta, pa = inst_lens(ga, "Lens_L")
        Fa = M.frames(Va, Ta, 1.0)
        Ra = M.rows(Fa, Fa["scene"], M.scenes(pa), [0.05, 0.95])
        held = np.isfinite(Ra["cap"]).all(1) & (Ra["cap"] < 90).all(1)
        fl = np.min(Ra["cap"], 1)
        if case == "nylor":
            sh = Fa["s"][held]
            snh = Fa["s"][~held]
            dch = np.array([np.min(np.minimum(np.abs(x - snh), Fa["L"] - np.abs(x - snh))) if len(snh) else np.inf
                            for x in sh])
            C.add("P4.nylor.top_flats", "nylor: top >= 0.44 on the flats", ">= 0.44 at every held station", "instrument",
                  {"min": r4(fl[held].min()) if held.any() else None, "stations": int(held.sum()),
                   "min_ge_2mm_from_channel_end": r4(fl[held][dch >= 2.0].min()) if (dch >= 2.0).any() else None,
                   "min_within_2mm_of_channel_end": r4(fl[held][dch < 2.0].min()) if (dch < 2.0).any() else None},
                  held.any() and fl[held].min() >= 0.44)
        # groove geometry, every station
        dims = [M.groove_dims(M.edge_profile(Fa, k, Fa["scene"])) for k in range(len(Fa["s"]))]
        good = [d for d in dims if d is not None]
        W = np.array([d["width_mm"] for d in good])
        D = np.array([d["depth_mm"] for d in good])
        Cn = np.array([d["centre_offset_mm"] for d in good])
        okg = (len(good) == len(dims) and np.all(np.abs(W - 0.60) <= 0.02) and np.all(np.abs(D - 0.60) <= 0.02)
               and np.all(np.abs(Cn) <= 0.05))
        C.add(f"P4.{case}.groove", f"{tag}: groove 0.60 +-0.02 wide and deep, centred +-0.05",
              "width, depth 0.58..0.62; |centre offset| <= 0.05 at every station", "instrument",
              {"stations": len(dims), "measured": len(good), "width_min_max": r4([W.min(), W.max()]) if len(W) else None,
               "depth_min_max": r4([D.min(), D.max()]) if len(D) else None,
               "centre_abs_max": r4(np.abs(Cn).max()) if len(Cn) else None}, okg)
        pp = s1.get("profile_plan") or {}
        cd = [core_groove_dims(p) for p in core_profiles(ga, "Lens_L")]
        cg = [d for d in cd if d is not None]
        cW = np.array([d["width_mm"] for d in cg])
        cD = np.array([d["depth_mm"] for d in cg])
        cC = np.array([d["centre_offset_mm"] for d in cg])
        C.add(f"P4.{case}.groove", f"{tag}: groove 0.60 +-0.02 wide and deep, centred +-0.05",
              "width, depth 0.58..0.62; |centre offset| <= 0.05 at every core station", "core",
              {"stations": len(cd), "measured": len(cg), "width_min_max": r4([cW.min(), cW.max()]) if len(cW) else None,
               "depth_min_max": r4([cD.min(), cD.max()]) if len(cD) else None,
               "centre_abs_max": r4(np.abs(cC).max()) if len(cC) else None,
               "plan": {k: pp.get(k) for k in ("width_mm", "depth_mm")}},
              bool(len(cd)) and len(cg) == len(cd) and bool(np.all(np.abs(cW - 0.60) <= 0.02))
              and bool(np.all(np.abs(cD - 0.60) <= 0.02)) and bool(np.all(np.abs(cC) <= 0.05)),
              note="measured on the exported lens through the core's station frames, backend and edge rule "
                   "(core_profiles), not the tool's plan")
        topo_t = (s1.get("profile_result") or {}).get("lens_topology") or {}
        C.add(f"P4.{case}.closed", f"{tag}: lens closed chi = 2", "closed, chi 2", "core", topo_t,
              bool(topo_t.get("closed")) and topo_t.get("euler") == 2)
        nd = inst_scene(ga)["Lens_L"]
        tp = M.topology(nd["V"], nd["T"])
        C.add(f"P4.{case}.closed", f"{tag}: lens closed chi = 2", "closed, chi 2", "instrument", tp,
              tp["closed"] and tp["euler"] == 2)
        # exposed outline unchanged
        a0, a1 = arr(case, "a0"), arr(case, "a1")
        b0 = o["ns_before"]["bodies"][0]
        vis = np.asarray(b0["point"]["verdict_check"]) == "VISIBLE_BY_DESIGN"
        nodes_vis = np.unique(np.asarray(b0["point"]["node"])[vis])
        lr = np.asarray(b0["loop_raw"])[nodes_vis]
        prr = np.asarray(b0["partner_raw"])[nodes_vis]
        rows_ = np.r_[lr, prr[prr >= 0]]
        n0 = len(a0["o0_V"])
        dmax = float(np.linalg.norm(a1["o0_V"][:n0][rows_] - a0["o0_V"][rows_], axis=1).max()) if len(rows_) else None
        C.add(f"P4.{case}.exposed_outline", f"{tag}: exposed outline unchanged +-0.01",
              "exposed nodes' rim corners move <= 0.01", "arrays", {"nodes": int(len(nodes_vis)), "max_mm": r4(dmax)},
              dmax is not None and dmax <= 0.01)
        Vb, Tb, pb = inst_lens(gb, "Lens_L")
        Fb = M.frames(Vb, Tb, 1.0)
        E0 = M.rows(Fb, Fb["scene"], M.scenes(pb), [0.05, 0.95])
        E1 = M.rows(Fb, IG.build_scene(Va, Ta), M.scenes(pa), [0.05, 0.95])
        expo = ~(np.isfinite(E0["cap"]) & (E0["cap"] < 90)).any(1)
        dE = np.abs(E1["edge"][expo] - E0["edge"][expo])
        C.add(f"P4.{case}.exposed_outline", f"{tag}: exposed outline unchanged +-0.01", "lens edge on the 0.05 / 0.95 "
              "rows <= 0.01 at every exposed station", "instrument",
              {"stations": int(expo.sum()), "max_mm": r4(np.nanmax(dE)) if expo.any() else None},
              expo.any() and np.isfinite(dE).all() and np.nanmax(dE) <= 0.01)
        cord = "Cord" if case == "nylor" else "Lens_L | nylor cord"
        sc_a = inst_scene(ga)
        if cord in sc_a:
            cV, cT = inst_part(ga, cord)
            ov = M.pair_overlap_mm3(Fa, IG.build_scene(cV, cT), Fa["scene"])
            core = cord_overlap_core(s1, o["check_after"], host)
            C.add(f"P4.{case}.cord_overlap", f"{tag}: cord-lens overlap <= 0.01 mm3", "<= 0.01 by the tool (EXACT "
                  "Boolean on the grooved lens), the Blender check and the host check (station-plane columns)", "core",
                  core, all(v is not None and v <= 0.01 for v in (core["tool_mm3"], core["blender_check_mm3"],
                                                                 core["host_check_mm3"])),
                  note="the tool's own report and both checks' reports (proof review round 2: the stage-proof record "
                       "came from the proof's own Boolean)")
            C.add(f"P4.{case}.cord_overlap", f"{tag}: cord-lens overlap <= 0.01 mm3", "<= 0.01", "instrument",
                  {"mm3": r4(ov)}, ov <= 0.01)
        else:
            C.add(f"P4.{case}.cord_overlap", f"{tag}: cord in the GLB", "present", "instrument", {"nodes": sorted(sc_a)},
                  False)
        if case == "nylor_nocord":
            C.add("P4.nylor_nocord.created", "without a cord: a cord is created", "tool created one", "core",
                  {"cord": (s1.get("profile_result") or {}).get("cord")},
                  (s1.get("profile_result") or {}).get("cord") == "Lens_L | nylor cord")
            if cord in sc_a:
                cV, cT = inst_part(ga, cord)
                cs = IG.build_scene(cV, cT)
                expo_a = ~(np.isfinite(Ra["cap"]) & (Ra["cap"] < 90)).any(1)
                diam, deq = [], []
                for k in np.flatnonzero(expo_a)[::3]:
                    mid = (Fa["Pm"][k] + 0.5 * (Fa["uf"][k] + Fa["ub"][k]) * Fa["u"][k]
                           + 0.5 * (Fa["vf"][k] + Fa["vb"][k]) * Fa["n"][k])
                    P, A_, B_ = M._plane_grid(mid - 0.3 * Fa["u"][k], Fa["u"][k], Fa["n"][k], 0.6, 0.0025)
                    occ = IG._occupancy(cs, P)
                    if occ.sum() < 50:
                        continue
                    c = P[occ].mean(0)
                    diam.append(2 * np.linalg.norm(P[occ] - c, axis=1).max())
                    deq.append(2 * np.sqrt(occ.sum() * 0.0025 ** 2 / np.pi))
                diam = np.array(diam)
                C.add("P4.nylor_nocord.cord_diameter", "created cord diameter 0.60 +-0.02",
                      "vertex (circumscribed) diameter 0.58..0.62 at every sampled station", "instrument",
                      {"stations": int(len(diam)), "min_max": r4([diam.min(), diam.max()]) if len(diam) else None,
                       "area_equivalent_p50": r4(np.median(deq)) if deq else None},
                      bool(len(diam)) and np.all(np.abs(diam - 0.60) <= 0.02))
                lz = IG.lens_structure(Va, Ta)
                loopP = lz["Vw"][lz["fl"]]
                chV, chT = inst_part(ga, "Channel")
                ac = M.arc_extent(loopP, cV)
                ah = M.arc_extent(loopP, chV)
                ovl = M.arc_overlaps(ac, ah)
                C.add("P4.nylor_nocord.cord_ends", "created cord ends 1.0 +-0.1 mm in the channel",
                      "two overlaps of the cord's and the channel's outline arcs, each 0.9..1.1 mm", "instrument",
                      {"overlaps_mm": r4(ovl), "cord_arc_mm": r4(ac[2]), "channel_arc_mm": r4(ah[2])},
                      len(ovl) == 2 and all(0.9 <= x <= 1.1 for x in ovl))
    # review control (not a §10 line): an existing cord off the §5 groove (the stage-proof cord) is reported
    o = out("nylor_outline")
    s1 = o["seat1"]
    ga = glb("nylor_outline", "after")
    host = SC.check_glb(ga)
    core = cord_overlap_core(s1, o["check_after"], host)
    hl = check_lens(host, "Lens_L")
    bl = check_lens(o["check_after"], "Lens_L")

    def named(L):
        return any("cord-lens overlap" in x for x in L.get("reasons", []))
    core.update(tool_status=s1.get("status"), tool_warning=[w[:160] for w in s1.get("warnings", [])
                                                             if w.startswith("W_CORD_OVERLAP")],
                blender_check_fails_on_it=bool(not bl["pass"] and named(bl)),
                host_check_fails_on_it=bool(not hl["pass"] and named(hl)))
    C.info["nylor_outline.tool"] = {k: s1.get(k) for k in ("status", "code", "warnings", "check")}
    C.add("P4.nylor_outline.cord_overlap_reported", "review control (not a §10 line): an existing cord off the §5 "
          "groove (horizontal outline normal, 0.30 mm inside) is reported, never seated silently",
          "tool partial with W_CORD_OVERLAP and overlap > 0.01; both checks FAIL the lens on the cord overlap", "core",
          core, s1.get("status") == "partial" and bool(core["tool_warning"]) and (core["tool_mm3"] or 0) > 0.01
          and core["blender_check_fails_on_it"] and core["host_check_fails_on_it"])
    Va, Ta, _ = inst_lens(ga, "Lens_L")
    Fo = M.frames(Va, Ta, 1.0)
    cV, cT = inst_part(ga, "Cord")
    ov = M.pair_overlap_mm3(Fo, IG.build_scene(cV, cT), Fo["scene"])
    tool = core["tool_mm3"] or 0.0
    C.add("P4.nylor_outline.cord_overlap_reported", "review control: the overlap the tool reports is there",
          "instrument overlap > 0.01 and within 10% of the tool's EXACT Boolean", "instrument",
          {"mm3": r4(ov), "tool_mm3": r4(tool)}, ov > 0.01 and tool > 0 and abs(ov - tool) <= 0.10 * tool)
    o = out("nylor_thin")
    C.add("P4.nylor_thin.thickness", "nylor t 1.4: E_THICKNESS", "refused E_THICKNESS, nothing written", "core",
          {"status": o["seat1"]["status"], "code": o["seat1"]["code"],
           "digest_equal": o["digest0"]["Lens_L"]["digest"] == o["digest1"]["Lens_L"]["digest"]},
          o["seat1"]["code"] == "E_THICKNESS" and o["digest0"]["Lens_L"]["digest"] == o["digest1"]["Lens_L"]["digest"])


def cord_overlap_core(s1: dict, blender_check: dict, host_check: dict) -> dict:
    """The cord-lens overlap by the core: the tool's report (seat_lens profile_result, EXACT Boolean) and the two
    checks' reports (cord_lens_overlap_mm3, station-plane columns)."""
    pr = s1.get("profile_result") or {}
    bl = check_lens(blender_check, "Lens_L") if blender_check.get("lenses") else {}
    hl = check_lens(host_check, "Lens_L")
    return {"tool_mm3": pr.get("cord_lens_overlap_mm3"), "tool_cord": pr.get("cord"),
            "tool_cord_parts": pr.get("cord_parts"), "blender_check_mm3": bl.get("cord_lens_overlap_mm3"),
            "host_check_mm3": hl.get("cord_lens_overlap_mm3"),
            "check_cord_parts": [p.get("part") for p in ((hl.get("cord") or {}).get("parts") or [])]}


def grade_crystal(C: Case):
    o = out("crystal")
    s1 = o["seat1"]
    pr = s1.get("profile_result") or {}
    part = (pr.get("parts") or [{}])[0]
    att = (part.get("attempts") or [{}])[-1]
    C.info["crystal.tool"] = {k: s1.get(k) for k in ("status", "code", "message", "moved_vertices", "warnings",
                                                       "profile_plan", "check")}
    C.info["crystal.attempts"] = part.get("attempts")
    gb, ga = glb("crystal", "before"), glb("crystal", "after")
    Va, Ta, pa = inst_lens(ga, "Lens_L")
    Fa = M.frames(Va, Ta, 1.0)
    bd = [M.bevel_dims(M.edge_profile(Fa, k, Fa["scene"])) for k in range(len(Fa["s"]))]
    good = [d for d in bd if d is not None]
    ang = np.array([d["angle_deg"] for d in good])
    hh = np.array([d["h_mm"] for d in good])
    pp = s1.get("profile_plan") or {}
    cb = [core_bevel_dims(p) for p in core_profiles(ga, "Lens_L")]
    cgood = [d for d in cb if d is not None]
    cang = np.array([d["angle_deg"] for d in cgood])
    chh = np.array([d["h_mm"] for d in cgood])
    C.add("P4.crystal.bevel_angle", "crystal: bevel 120 +-1 deg", "119..121 deg at every core station", "core",
          {"stations": len(cb), "measured": len(cgood),
           "min_p50_max": r4([cang.min(), np.median(cang), cang.max()]) if len(cang) else None,
           "planned_deg": pp.get("angle_deg")},
          bool(len(cb)) and len(cgood) == len(cb) and bool(np.all(np.abs(cang - 120) <= 1)),
          note="measured on the exported lens through the core's station frames, backend and edge rule "
               "(core_profiles), not the tool's plan")
    C.add("P4.crystal.bevel_angle", "crystal: bevel 120 +-1 deg", "119..121 deg at every station", "instrument",
          {"stations": len(bd), "measured": len(good), "min_p50_max": r4([ang.min(), np.median(ang), ang.max()])
           if len(ang) else None}, len(good) == len(bd) and np.all(np.abs(ang - 120) <= 1))
    hp = (pr.get("rewall") or {}).get("h_mm")
    C.add("P4.crystal.bevel_h", "crystal: h 0.404 +-0.005", "0.399..0.409 at every core station", "core",
          {"stations": len(cb), "measured": len(cgood),
           "min_p50_max": r4([chh.min(), np.median(chh), chh.max()]) if len(chh) else None,
           "planned_h_mm": pp.get("h_mm"), "rewall_h_min_max": hp},
          bool(len(cb)) and len(cgood) == len(cb) and bool(np.all(np.abs(chh - 0.404) <= 0.005)))
    C.add("P4.crystal.bevel_h", "crystal: h 0.404 +-0.005", "0.399..0.409 at every station", "instrument",
          {"min_p50_max": r4([hh.min(), np.median(hh), hh.max()]) if len(hh) else None},
          len(good) == len(bd) and np.all(np.abs(hh - 0.404) <= 0.005))
    # apex capture against the ORIGINAL aperture (the frame before the groove)
    Vb_f = inst_part(gb, "Crystal rim")
    Rap = M.rows(Fa, Fa["scene"], [IG.build_scene(*Vb_f)], [0.5])
    capx = Rap["cap"][:, 0]
    C.add("P4.crystal.apex", "crystal: apex 0.80 +-0.02 (beyond the original aperture)", "0.78..0.82 at every station",
          "instrument", {"min_p50_max": r4([np.nanmin(capx), np.nanmedian(capx), np.nanmax(capx)]),
                         "undefined": int((~np.isfinite(capx)).sum())},
          np.isfinite(capx).all() and np.all(np.abs(capx - 0.80) <= 0.02))
    cs = core_apex(ga, gb)
    C.add("P4.crystal.apex", "crystal: apex 0.80 +-0.02 (beyond the original aperture)", "0.78..0.82 at every point",
          "core", {"min_p50_max": r4([np.nanmin(cs), np.nanmedian(cs), np.nanmax(cs)])},
          np.isfinite(cs).all() and np.all(np.abs(cs - 0.80) <= 0.02))
    g = att.get("gap_min_p50_max_mm")
    C.add("P4.crystal.gap", "crystal: groove gap 0.02-0.06", "0.02..0.06", "core", {"gap_min_p50_max_mm": g},
          g is not None and g[0] >= 0.02 and g[2] <= 0.06)
    fa = inst_part(ga, "Crystal rim")
    Rg = M.rows(Fa, Fa["scene"], [IG.build_scene(*fa)], [0.25, 0.5, 0.75])
    gap = -Rg["cap"]
    C.add("P4.crystal.gap", "crystal: groove gap 0.02-0.06", "0.02..0.06 on the 0.25 / 0.5 / 0.75 rows", "instrument",
          {"min_max": r4([np.nanmin(gap), np.nanmax(gap)]), "undefined": int((~np.isfinite(gap)).sum())},
          np.isfinite(gap).all() and gap.min() >= 0.02 and gap.max() <= 0.06)
    lov = att.get("lens_overlap_mm3")
    C.add("P4.crystal.overlap", "crystal: lens ^ frame <= 0.01 mm3", "<= 0.01", "core",
          {"mm3": lov}, lov is not None and lov <= 0.01)
    ov = M.pair_overlap_mm3(Fa, Fa["scene"], IG.build_scene(*fa), window=(-1.5, 2.0, -1.6, 1.6))
    C.add("P4.crystal.overlap", "crystal: lens ^ frame <= 0.01 mm3", "<= 0.01", "instrument", {"mm3": r4(ov)}, ov <= 0.01)
    C.add("P4.crystal.frame_topology", "crystal: frame closed, chi unchanged", "closed, chi equal", "core",
          {"closed": att.get("closed"), "euler": att.get("euler")},
          bool(att.get("closed")) and att.get("euler", [0, 1])[0] == att.get("euler", [0, 1])[1])
    scb, sca = inst_scene(gb)["Crystal rim"], inst_scene(ga)["Crystal rim"]
    t0, t1 = M.topology(scb["V"], scb["T"]), M.topology(sca["V"], sca["T"])
    C.add("P4.crystal.frame_topology", "crystal: frame closed, chi unchanged", "closed, chi equal", "instrument",
          {"before": t0, "after": t1}, t1["closed"] and t0["euler"] == t1["euler"] and t0["components"] == t1["components"])
    rem, exp = att.get("removed_mm3"), part.get("expected_removed_mm3")
    C.add("P4.crystal.removed", "crystal: removed = cutter ^ frame +-1%", "|removed - expected| <= 1%", "core",
          {"removed_mm3": rem, "cutter_and_frame_mm3": exp},
          rem is not None and exp and abs(rem - exp) <= 0.01 * exp)
    vb_ = abs(M.signed_volume(scb["V"], scb["T"]))
    va_ = abs(M.signed_volume(sca["V"], sca["T"]))
    est = M.removed_estimate_mm3(Fa, IG.build_scene(*Vb_f), Va, Ta, 0.03)
    C.add("P4.crystal.removed", "crystal: removed = cutter ^ frame +-1%", "|removed - estimate| <= 1%", "instrument",
          {"removed_mm3 (frame volume before - after)": r4(vb_ - va_),
           "frame_before ^ rebuilt cutter (station-plane integral)": r4(est)},
          abs((vb_ - va_) - est) <= 0.01 * est,
          note="the cutter is not exported; the instrument rebuilds it from the exported profiled lens by the §5 "
               "definition (inflated 0.03 mm along angle-weighted vertex normals) and integrates frame_before ^ cutter "
               "over its 1 mm station planes with the outline's Pappus factor")
    C.add("P4.crystal.exact_time", "crystal: EXACT <= 2 s", "EXACT solver, <= 2 s", "core",
          {"solver": att.get("solver"), "seconds": att.get("seconds")},
          att.get("solver") == "EXACT" and att.get("seconds") is not None and att["seconds"] <= 2)
    om = out("crystal_manifold")["open_cutter"]
    op = om["open"]
    mismatch = op["expected_removed_mm3"] <= 0 or abs(op["removed_mm3"] - op["expected_removed_mm3"]) > \
        0.01 * op["expected_removed_mm3"]
    controls = [om.get(k) or {} for k in ("closed", "closed_exact")]
    ctrl_ok = all(c.get("accepted_by_the_tool_check") is True and c.get("cutter_closed") for c in controls)
    C.add("P4.crystal.open_cutter_manifold", "crystal: an open cutter through MANIFOLD is caught by the volume check",
          "the tool's acceptance test (seat_bpy.groove_attempt) rejects the open cutter, its removed volume differing "
          "from cutter ^ frame by > 1%, AND accepts the closed control cutter with MANIFOLD and with EXACT", "core", om,
          (not op["accepted_by_the_tool_check"]) and mismatch and not op["cutter_closed"] and ctrl_ok,
          note="without an accepted control the rejection shows nothing (proof review: the stage-proof control was "
               "rejected too, by an exact weld that merged two distinct coincident vertices of the Boolean result)")
    # the binding verdict on the grooved lens (proof review: P4 recorded only the geometry)
    host = SC.check_glb(ga)
    hl = check_lens(host, "Lens_L")
    bc = o["check_after"]
    bl = check_lens(bc, "Lens_L") if bc.get("lenses") else {}
    C.add("P4.crystal.check", "crystal: the grooved lens passes the binding check (tool status, the Blender check, the "
          "host check_glb on the exported GLB)", "tool 'seated', both checks PASS", "core",
          {"tool": {k: s1.get(k) for k in ("status", "code", "warnings")},
           "blender_check": {"ok": bc.get("ok"), "reasons": bc.get("reasons"), "verdicts": bl.get("verdicts"),
                             "classes": bl.get("classes")},
           "host_check": {"ok": host["ok"], "reasons": host["reasons"], "verdicts": hl.get("verdicts"),
                          "classes": hl.get("classes"), "frame_type": hl.get("frame_type"),
                          "frame_type_source": hl.get("frame_type_source")}},
          s1.get("status") == "seated" and bool(bc.get("ok")) and bool(host["ok"]),
          note="FAILS until WP4_DESIGN open item B is decided (owner): §3's capture reads the 0.03 mm groove clearance "
               "as a ~0.028 mm gap at every depth, so every station of a correctly grooved crystal lens FAILS acetate")


def core_apex(ga: Path, gb: Path) -> np.ndarray:
    """Core capture at mid-depth of the profiled lens (after) against the frame BEFORE the groove."""
    nodes_a, w, sk = SC.read_nodes(load_glb(ga))
    nodes_b, wb, skb = SC.read_nodes(load_glb(gb))
    lens = [n for n in nodes_a if n["lens"]]
    frame = [n for n in nodes_b if not n["lens"]]
    col = CK.collect(lens + frame, [], [], SC.factory("open3d"))
    body = col["bodies"][0]
    parts = CK.parts_near(body, col["solids"])
    be = SC.make_backend("open3d", [(c.V, c.T) for _, c in parts] + [(body.V, body.T)])
    fr, meas = stations.measure_body(body, be, len(parts))
    return meas.cap[:, stations.MID]


def grade_matrix(C: Case):
    kinds = {"sphere": "sphere", "toric": "toric", "flat": "flat", "solidified": "sphere", "chamfer": "sphere",
             "flipped": "sphere", "rounded": "sphere", "coarse": "sphere"}
    for c in MATRIX:
        case = f"acetate_{c}"
        o = out(case)
        ga = glb(case, "after")
        host = SC.check_glb(ga, stations_table=True)
        L = check_lens(host, "Lens_L")
        A = st_arrays(L)
        v = A["value"][A["required"]]
        p5 = pct(v, 5)
        C.add(f"P4.matrix.{c}.p5", f"acetate matrix {c}: min-over-depth p5 >= 0.78", ">= 0.78", "core",
              {"p5": p5, "check_ok": host["ok"], "tool": o["seat1"]["status"]}, p5 is not None and p5 >= 0.78)
        Va, Ta, pa = inst_lens(ga, "Lens_L")
        Fm = M.frames(Va, Ta, 1.0)
        Rm = M.rows(Fm, Fm["scene"], M.scenes(pa), DEPTHS)
        cg = Rm["cap"]
        cmin = np.where(np.isfinite(cg).all(1), np.nanmin(np.where(cg >= 90, np.nan, cg), 1), np.nan)
        pa_ = pct(cmin, 5)
        grid = None
        if c not in ("chamfer", "rounded"):                    # the port's rungs need a one-ring wall
            full = IG.analyse(Va, Ta, [(n_, v_, t_) for n_, v_, t_ in pa], 1.0, DEPTHS, full_grid=True)
            c9 = np.array([np.nan if r["cap_min"] is None else r["cap_min"] for r in full["rows"]], float)
            grid = pct(c9, 5)
        C.add(f"P4.matrix.{c}.p5", f"acetate matrix {c}: min-over-depth p5 >= 0.78", ">= 0.78 (min over the §2 depths "
              "0.05..0.95, refined rows)", "instrument",
              {"refined_5_depths_p5": pa_, "undefined": int((~np.isfinite(cmin)).sum()),
               "grid_9_levels_p5_reported": grid},
              pa_ is not None and pa_ >= 0.78 and bool(np.isfinite(cmin).all()),
              note="the port's 9-level full grid (reported, not graded) quantises capture to its 0.05 mm samples "
                   "and includes the exact corners (fractions 0 and 1, outside §2's depths): a 0.80 capture reads "
                   "0.75 there (§10 P2 allows 0.05 for that grid)")
        # grown front edge vs the analytic cap: the core's front-loop vertices (raw rows of its oriented loop)
        a0, a1 = arr(case, "a0"), arr(case, "a1")
        fr_rows = np.asarray(o["ns_before"]["bodies"][0]["loop_raw"], int)
        V0, V1 = a0["o0_V"][fr_rows], a1["o0_V"][fr_rows]
        moved = np.any(a0["o0_co"][fr_rows] != a1["o0_co"][fr_rows], axis=1)
        k = kinds[c]
        dev0 = np.abs(V0[:, 2] - SF.cap_z(V0[:, 0], V0[:, 1], k, apex=(-32.0, 0.0)))
        dev1 = np.abs(V1[:, 2] - SF.cap_z(V1[:, 0], V1[:, 1], k, apex=(-32.0, 0.0)))
        on_cap = bool(dev0.max() <= 1e-3)
        d1 = float(dev1[moved].max()) if moved.any() else None
        dch = float(np.abs(dev1 - dev0)[moved].max()) if moved.any() else None
        val = d1 if on_cap else dch
        C.add(f"P4.matrix.{c}.cap", f"acetate matrix {c}: grown front edge within 0.01 mm of the true cap",
              "<= 0.01 mm (z) from the analytic front cap at every moved front-loop vertex; where the construction's "
              "front loop is not on that cap before seating, its distance may change by <= 0.01", "arrays",
              {"moved_front_loop": int(moved.sum()), "front_loop_on_cap_before": on_cap,
               "max_after_mm": r4(d1), "max_before_mm": r4(dev0.max()), "max_change_mm": r4(dch)},
              bool(moved.any()) and val is not None and val <= 0.01,
              note={"chamfer": "the chamfer's front loop (ring 0) is pulled 0.3 mm inward at constant z by the fixture, "
                               "0.064 mm off the sphere before seating",
                    "rounded": "the core's front loop on the bullnose is the first rounded ring (one ring outside the "
                               "cap boundary), off the sphere by construction"}.get(c))
        mv = set(moved_rows(a0, a1, 0).tolist())
        exp = set(o["ns_before"]["bodies"][0]["expected_raw"])
        C.add(f"P4.matrix.{c}.moved_in_failing_nodes", f"acetate matrix {c}: (stop rule) moved vertices = band of "
              "FAIL nodes (before-state)", "moved == expected", "arrays",
              {"moved": len(mv), "expected": len(exp), "outside": len(mv - exp)}, mv == exp)
        moved_nodes_records(C, f"P4.matrix.{c}", f"acetate matrix {c}", o["ns_before"]["bodies"][0], mv, a0["o0_V"],
                            glb(case, "before"), "Lens_L", "acetate")
    # refusals
    want = {"acetate_open": ("E_OPEN", None), "acetate_selfx": ("E_SELF_INTERSECTING", None),
            "acetate_bevel": ("E_LENS_MODIFIERS", None),
            "acetate_notch": (None, "CONCAVE"), "acetate_wide": ("E_TOO_MANY_BLOCKED", "NEEDS_GROWTH")}
    text = {"acetate_open": "open lens", "acetate_selfx": "self-intersection", "acetate_bevel": "Bevel modifier on the "
            "lens", "acetate_notch": "notch CONCAVE", "acetate_wide": "aperture 2.5 mm too large NEEDS_GROWTH -> "
            "E_TOO_MANY_BLOCKED"}
    for case, (code, word) in want.items():
        o = out(case)
        s1 = o["seat1"]
        same = o["digest0"]["Lens_L"]["digest"] == o["digest1"]["Lens_L"]["digest"]
        blk = ((s1.get("bodies") or [{}])[0].get("before") or {}).get("blocked_by") or {}
        ok = s1["status"] == "refused" and same and (code is None or s1["code"] == code) and \
            (word is None or word in (s1.get("message") or "") or word in blk)
        note, how = None, ""
        if case == "acetate_notch":
            # the refusal must come from the CONCAVE clamp or the gate, not from the array post-check catching what
            # the plan produced around the clamped points (proof review round 2)
            ok = ok and s1.get("code") != "E_POSTCHECK"
            how = " by the CONCAVE clamp or the gate (not E_POSTCHECK)"
            note = ("§10 'notch CONCAVE' is met only by a refusal from the CONCAVE clamp / gate; a post-check refusal "
                    "(E_POSTCHECK: a moved face folded / its area ratio out of range around the clamped points, whose "
                    "neighbours kept their full growth) is graded FAIL until WP4_DESIGN open item D is decided")
        C.add(f"P4.refusal.{case[8:]}", f"refusal: {text[case]}", f"refused{' ' + code if code else ''}"
              f"{', names ' + word if word else ''}{how}, nothing written", "core",
              {"status": s1["status"], "code": s1.get("code"), "message": (s1.get("message") or "")[:240],
               "blocked_by": blk or None, "digest_unchanged": same, "moved_vertices": s1.get("moved_vertices")}, ok,
              note=note)
    o = out("acetate_fused")
    s1 = o["seat1"]
    host = SC.check_glb(glb("acetate_fused", "after"), stations_table=True)
    p5s = []
    for L in host["lenses"]:
        A = st_arrays(L)
        p5s.append(pct(A["value"][A["required"]], 5))
    C.add("P4.fused", "two fused components are analysed separately", "2 bodies, both seated, each p5 >= 0.78", "core",
          {"tool_bodies": len(s1.get("bodies") or []), "status": s1["status"], "check_lenses": len(host["lenses"]),
           "p5": p5s, "check_ok": host["ok"]},
          len(s1.get("bodies") or []) == 2 and s1["status"] == "seated" and len(p5s) == 2
          and all(p is not None and p >= 0.78 for p in p5s))
    o = out("acetate_drilled")
    s1 = o["seat1"]
    a0, a1 = arr("acetate_drilled", "a0"), arr("acetate_drilled", "a1")
    b0 = o["ns_before"]["bodies"][0]
    hole = np.asarray(b0["hole_raw"], int)
    hole_moved = int(np.any(a0["o0_co"][hole] != a1["o0_co"][hole], axis=1).sum()) if len(hole) else None
    host = SC.check_glb(glb("acetate_drilled", "after"), stations_table=True)
    dh = check_lens(host, "Lens_L")["body"].get("drilled_holes")
    C.add("P4.drilled", "a drilled hole is reported, not grown", "hole reported (1), 0 hole vertices moved", "core",
          {"drilled_holes_check": dh, "drilled_holes_tool": b0["summary"].get("drilled_holes"),
           "hole_vertices": int(len(hole)), "hole_vertices_moved": hole_moved, "status": s1["status"]},
          dh == 1 and len(hole) > 0 and hole_moved == 0 and s1["status"] == "seated")


# ======================================================================================================= P5
def grade_p5() -> dict:
    C = Case("P5", "regression on the accepted finals (semi_rimless, 'plain')")
    for case, lens in (("oakley", OAKLEY_LENS), ("invu", INVU_LENS)):
        o = out(case)
        a0, a1 = arr(case, "a0"), arr(case, "a1")
        gb, ga = glb(case, "before"), glb(case, "after")
        s1 = o["seat1"]
        C.info[f"{case}.tool"] = {k: s1.get(k) for k in ("status", "code", "moved_vertices", "seconds", "bodies",
                                                           "verify", "normals", "sharpen", "warnings")}
        mv = set(moved_rows(a0, a1, 0).tolist())
        b0 = o["ns_before"]["bodies"][0]
        exp = set(b0["expected_raw"])
        C.add(f"P5.{case}.moved_set", f"{case}: the moved set equals the band of FAIL nodes not BLOCKED, recomputed from "
              "the before-state", "moved == expected", "arrays",
              {"moved": len(mv), "expected": len(exp), "moved_outside_expected": len(mv - exp),
               "expected_not_moved": len(exp - mv)}, mv == exp)
        moved_nodes_records(C, f"P5.{case}", f"{case}: seat_lens(semi_rimless)", b0, mv, a0["o0_V"], gb, lens,
                            "semi_rimless")
        d0, d1 = o["digest0"], o["digest1"]
        others = [k for k in d0 if k != lens]
        diff = [k for k in others if d0[k] != d1.get(k)]
        C.add(f"P5.{case}.others_identical", f"{case}: every other vertex and every non-lens object bit-identical",
              "every non-lens object's digest and matrix identical; lens rows outside the moved set identical",
              "arrays", {"objects": len(others), "changed": diff,
                         "lens_rows_changed_outside_moved_set": len(mv - exp)}, not diff and not (mv - exp))
        J = node_join(o["ns_before"], o["ns_after"])[0]
        bb, ba = o["ns_before"]["bodies"][0], o["ns_after"]["bodies"][0]
        f0 = set(failing_nodes_of(bb).tolist())
        f1 = set(failing_nodes_of(ba).tolist())
        pf = [raw for raw, _, _, k0, k1 in J if k0 not in f0 and k1 in f1]
        C.add(f"P5.{case}.no_pass_to_fail", f"{case}: no PASS -> FAIL", "0 nodes", "core",
              {"nodes": len(J), "pass_to_fail": len(pf), "failing_after": len(f1)}, not pf)
        hb = SC.check_glb(gb, frame_type="semi_rimless", stations_table=True)
        ha = SC.check_glb(ga, stations_table=True)
        Lb, La = st_arrays(hb["lenses"][0]), st_arrays(ha["lenses"][0])
        keyb = {tuple(np.round(p, 3)): v for p, v in zip(Lb["xyz"], Lb["verdict"])}
        pf2 = sum(1 for p, v in zip(La["xyz"], La["verdict"]) if keyb.get(tuple(np.round(p, 3))) == "PASS" and v == "FAIL")
        C.add(f"P5.{case}.no_pass_to_fail", f"{case}: no PASS -> FAIL", "0 stations (same position)", "core",
              {"host_check_pass_to_fail": pf2}, pf2 == 0)
        if case == "oakley":
            C.add("P5.oakley.zero_moved", "Oakley: 0 vertices moved", "0", "arrays",
                  {"raw_moved": len(mv), "tool_status": s1["status"], "tool_moved": s1.get("moved_vertices")},
                  len(mv) == 0 and s1.get("moved_vertices") == 0)
            sh0, sh1 = o["sharp0"], o["sharp1"]
            C.add("P5.oakley.digest_normals_sharp", "Oakley: lens digest, custom_normal and the 496 sharp rim edges "
                  "identical", "digest equal, custom normals kept, 496 sharp rim edges before and after", "arrays",
                  {"digest_equal": d0[lens]["digest"] == d1[lens]["digest"], "sharp_before": sh0, "sharp_after": sh1},
                  d0[lens]["digest"] == d1[lens]["digest"] and sh0 == sh1 and sh0["rim_sharp"] == 496
                  and sh0["custom_normals"])
            b0_ = M.accessor_bytes(gb, lens)
            b1_ = M.accessor_bytes(ga, lens)
            same = b0_ == b1_
            C.add("P5.oakley.byte_identical", "Oakley: lens primitive re-exports byte-identical",
                  "every accessor of the lens primitive byte-identical", "instrument",
                  {"accessors": [s for s, _ in b0_], "bytes": sum(len(b) for _, b in b0_), "identical": same}, same)
            C.add("P5.oakley.check_pass", "Oakley: check PASS", "Blender and host checks pass", "core",
                  {"blender_ok": o["check_after"]["ok"], "host_ok": ha["ok"], "host_reasons": ha["reasons"]},
                  o["check_after"]["ok"] and ha["ok"])
            top = La["sector"] == "top"
            m = La["cap"][top, 2]
            cls = {c: int((La["cls"][top] == c).sum()) for c in set(La["cls"][top].tolist())}
            p5, p95 = pct(m, 5), pct(m, 95)
            C.add("P5.oakley.top_lipped", "Oakley: (check PASS with) top LIPPED 0.65-0.80", "top classes LIPPED, mid-depth "
                  "capture p5 >= 0.65 and p95 <= 0.80", "core",
                  {"classes": cls, "mid_p5_p50_p95": [p5, pct(m, 50), p95]},
                  set(cls) == {"LIPPED"} and p5 >= 0.65 and p95 <= 0.80,
                  note="§10 writes this as the description of the passing state [P]")
            Va, Ta, pa = inst_lens(ga, lens)
            F = M.frames(Va, Ta, 1.0)
            R = M.rows(F, F["scene"], M.scenes(pa), [0.5])
            tt = F["sector"] == "top"
            mi = R["cap"][tt, 0]
            ip5, ip95 = pct(mi, 5), pct(mi, 95)
            C.add("P5.oakley.top_lipped", "Oakley: (check PASS with) top LIPPED 0.65-0.80", "top mid-depth capture p5 >= "
                  "0.65 and p95 <= 0.80", "instrument", {"stations": int(tt.sum()), "mid_p5_p50_p95":
                                                         [ip5, pct(mi, 50), ip95]},
                  ip5 is not None and ip5 >= 0.65 and ip95 <= 0.80)
            vis = {}
            for own, ver in zip(La["owner"], La["verdict"]):
                if own and ("nose saddle" in own or "latch housing" in own):
                    vis.setdefault(own.split("#")[0], {}).setdefault(ver, 0)
                    vis[own.split("#")[0]][ver] += 1
            okv = bool(vis) and all(set(d) <= {"VISIBLE_BY_DESIGN", "PASS"} for d in vis.values())
            C.add("P5.oakley.visible_by_design", "Oakley: nose saddle and latch housings visible by design",
                  "no station they hold fails (VISIBLE_BY_DESIGN or PASS)", "core", vis, okv)
        else:
            fnt = failing_nodes_of(b0, sector=["top"])
            C.add("P5.invu.before_top_failing", "INVU: ~267 top nodes fail [P]", "'~267' read as 240..294 (+-10%)",
                  "core", {"top_failing_nodes": int(len(fnt)), "all_failing_nodes": int(len(failing_nodes_of(b0)))},
                  240 <= len(fnt) <= 294)
            sec = np.asarray(b0["point"]["sector"])
            cls0 = np.asarray(b0["point"]["cls"])
            vc0 = np.asarray(b0["point"]["verdict_check"])
            bm = (sec == "bottom") & (cls0 == "BACKED")
            bn = np.unique(np.asarray(b0["point"]["node"])[bm])
            lr = set(np.asarray(b0["loop_raw"])[bn].tolist())
            C.add("P5.invu.bottom_backed", "INVU: 18 bottom BACKED nodes visible by design [P]",
                  "every bottom BACKED point VISIBLE_BY_DESIGN, none of their nodes moved (count reported)", "core",
                  {"bottom_backed_points": int(bm.sum()), "their_nodes": int(len(bn)),
                   "verdicts": sorted(set(vc0[bm].tolist())), "nodes_moved": len(lr & mv)},
                  bm.any() and set(vc0[bm].tolist()) == {"VISIBLE_BY_DESIGN"} and not (lr & mv))
            ff = former_failures(o, lambda v: v >= 0.45, lambda v: v >= 0.35, allow_transition=True)
            req1 = np.asarray(o["ns_after"]["bodies"][0]["point"]["required"])
            vc1 = np.asarray(o["ns_after"]["bodies"][0]["point"]["verdict_check"])
            tr_share = float(((vc1 == "TRANSITION") & req1).sum() / max(req1.sum(), 1))
            ins, en = ff["inside"], ff["ends"]
            C.add("P5.invu.former_failures", "INVU: former failures >= 0.45 inside runs, >= 0.35 at run ends, or "
                  "TRANSITION (<= 3%)", "inside >= 0.45, ends >= 0.35, else TRANSITION; TRANSITION <= 3% of required",
                  "core", {"inside_n_min": [int(len(ins)), r4(ins.min()) if len(ins) else None],
                           "inside_below_0.45": int((ins < 0.45).sum()), "inside_below_0.445": int((ins < 0.445).sum()),
                           "inside_p5_p50": [pct(ins, 5), pct(ins, 50)],
                           "ends_n_min": [int(len(en)), r4(en.min()) if len(en) else None],
                           "transition_former": ff["transition"], "transition_share_required": r4(tr_share),
                           "misses": ff["bad"][:12], "misses_n": len(ff["bad"])},
                  not ff["bad"] and tr_share <= 0.03)
            Vb, Tb, pb = inst_lens(gb, lens)
            Va, Ta, pa = inst_lens(ga, lens)
            F = M.frames(Vb, Tb, 1.0)
            sa = IG.build_scene(Va, Ta)
            R0 = M.rows(F, F["scene"], M.scenes(pb), [0.05, 0.5])
            R1 = M.rows(F, sa, M.scenes(pa), [0.05, 0.5])
            cv0 = M.corner_cover(F, F["scene"], M.scenes(pb))["front"]
            cv1 = M.corner_cover(F, sa, M.scenes(pa))["front"]
            c0, c1 = R0["cap"][:, 1], R1["cap"][:, 1]
            f0 = R0["cap"][:, 0]
            # held (the core's LIPPED, WP4_DESIGN implementation note 1): c(0.5) >= -1.0 and the front held: covered
            # along +n, or frame on the 0.05 row within the 1.0 mm captured gap
            heldi = (np.isfinite(c0) & (c0 >= -1.0) & (c0 < 90)
                     & (np.isfinite(cv0) | (np.isfinite(f0) & (f0 >= -1.0))))
            fail0 = heldi & (c0 < 0.30 - TOL)
            delta = runs_of(fail0, F["s"], F["L"])
            okp = np.where(delta >= 2.0, c1 >= 0.45, c1 >= 0.35) & np.isfinite(c1)
            share = float(okp[fail0].mean()) if fail0.any() else None
            C.add("P5.invu.former_failures", "INVU: former failures >= 0.45 inside runs, >= 0.35 at run ends, or "
                  "TRANSITION (<= 3%)", "share of former failing stations meeting inside/end thresholds >= 0.97 (the "
                  "instrument cannot tell TRANSITION; <= 3% may miss)", "instrument",
                  {"former_failing_stations": int(fail0.sum()), "share_ok": r4(share),
                   "inside_min": r4(np.nanmin(c1[fail0 & (delta >= 2)])) if (fail0 & (delta >= 2)).any() else None,
                   "ends_min": r4(np.nanmin(c1[fail0 & (delta < 2)])) if (fail0 & (delta < 2)).any() else None},
                  share is not None and share >= 0.97)
            # grown front corners covered
            g = np.asarray(b0["node_g"], float)
            grown = np.flatnonzero(g > 0)
            npt0, npt1 = np.asarray(bb["node_point"]), None
            pos1 = {r: k for k, r in enumerate(ba["loop_raw"])}
            cfa = np.asarray(ba["point"]["covered_front"])
            cfb = np.asarray(bb["point"]["covered_front"])
            after_cov = [bool(cfa[ba["node_point"][pos1[bb["loop_raw"][k]]]]) for k in grown if bb["loop_raw"][k] in pos1]
            before_cov = [bool(cfb[npt0[k]]) for k in grown]
            C.add("P5.invu.grown_covered", "INVU: grown front corners covered 100% [81% before]", "1.0", "core",
                  {"grown_nodes": int(len(grown)), "after_share": r4(np.mean(after_cov)) if after_cov else None,
                   "before_share": r4(np.mean(before_cov)) if before_cov else None},
                  bool(after_cov) and all(after_cov))
            e0, e1 = R0["edge"][:, 0], R1["edge"][:, 0]
            gst = np.isfinite(e0) & np.isfinite(e1) & (e1 - e0 > 0.005)
            C.add("P5.invu.grown_covered", "INVU: grown front corners covered 100% [81% before]", "1.0", "instrument",
                  {"grown_stations": int(gst.sum()), "after_share": r4(np.isfinite(cv1[gst]).mean()) if gst.any() else None,
                   "before_share": r4(np.isfinite(cv0[gst]).mean()) if gst.any() else None},
                  gst.any() and np.isfinite(cv1[gst]).all())
            dmax = float(np.linalg.norm(a1["o0_V"] - a0["o0_V"], axis=1).max())
            C.add("P5.invu.max_delta", "INVU: max delta <= 0.95 mm [0.885]", "<= 0.95", "arrays", {"max_mm": r4(dmax)},
                  dmax <= 0.95)
            C.add("P5.invu.max_delta", "INVU: max delta <= 0.95 mm [0.885]", "<= 0.95", "core",
                  {"growth_max_mm": [x.get("growth_max_mm") for x in s1.get("bodies") or []]},
                  all((x.get("growth_max_mm") or 9) <= 0.95 for x in s1.get("bodies") or [{}]))
            C.add("P5.invu.moved", "INVU: moved vertices <= 600", "<= 600", "arrays", {"moved": len(mv)}, len(mv) <= 600)
            ver = s1.get("verify") or {}
            Mx0, Mx1 = np.array(d0[lens]["matrix"]), np.array(d1[lens]["matrix"])
            C.add("P5.invu.write_back", "INVU: exact write-back through the (0, -0.06, -2.2) translation",
                  "stored = planned (tool verify), object matrix unchanged with translation (0, -0.06, -2.2)", "core",
                  {"verify": ver, "translation": r4(Mx1[:3, 3]), "matrix_unchanged": bool(np.array_equal(Mx0, Mx1))},
                  ver.get("failures") == [] and ver.get("stored_vs_planned_max_mm") is not None
                  and ver["stored_vs_planned_max_mm"] <= 1e-6
                  and np.array_equal(Mx0, Mx1) and np.allclose(Mx1[:3, 3], [0.0, -0.06, -2.2], atol=0.005))
            from scipy.spatial import cKDTree
            gV = inst_scene(ga)[lens]["V"]
            dA = cKDTree(gV).query(a1["o0_V"])[0]
            dB = cKDTree(a1["o0_V"]).query(gV)[0]
            C.add("P5.invu.write_back", "INVU: exact write-back through the (0, -0.06, -2.2) translation",
                  "every stored (Blender asset mm) lens vertex has a GLB vertex within 1e-4 mm and back", "instrument",
                  {"max_blender_to_glb_mm": float(dA.max()), "max_glb_to_blender_mm": float(dB.max())},
                  dA.max() <= 1e-4 and dB.max() <= 1e-4)
            w0, w1 = o["wall0"], o["wall1"]
            C.add("P5.invu.walls_flat", "INVU: walls flat", "0 smooth wall faces before and after", "arrays",
                  {"before": w0, "after": w1}, w0["smooth_wall_polygons"] == 0 and w1["smooth_wall_polygons"] == 0)
            nd = inst_scene(ga)[lens]
            wf = M.wall_flatness_deg(nd["V"], nd["T"], nd["N"])
            C.add("P5.invu.walls_flat", "INVU: walls flat", "GLB wall corner normals = face normal within 1 deg",
                  "instrument", {"max_deg": r4(wf)}, wf <= 1.0)
            p0, p1 = d0.get(INVU_PRINT), d1.get(INVU_PRINT)
            C.add("P5.invu.print", "INVU: print untouched", "digest and matrix identical", "arrays",
                  {"present": p0 is not None, "equal": p0 == p1}, p0 is not None and p0 == p1)
            q0 = inst_scene(gb).get(INVU_PRINT)
            q1 = inst_scene(ga).get(INVU_PRINT)
            eq = q0 is not None and q1 is not None and q0["V"].shape == q1["V"].shape and np.array_equal(q0["V"], q1["V"])
            C.add("P5.invu.print", "INVU: print untouched", "GLB positions identical", "instrument",
                  {"present": q0 is not None, "equal": bool(eq)}, eq)
            C.add("P5.invu.check_after", "INVU: (reported) check after seating", "Blender and host checks pass", "core",
                  {"blender_ok": o["check_after"]["ok"], "host_ok": ha["ok"], "host_reasons": ha["reasons"]},
                  o["check_after"]["ok"] and ha["ok"])
    return C.result()


# ======================================================================================================= all
def grade_all() -> dict:
    state = json.loads((OUT / "run_state.json").read_text(encoding="utf-8"))
    results = {}
    for name, fn in (("P2", grade_p2), ("P3", grade_p3), ("P4", grade_p4), ("P5", grade_p5)):
        results[name] = fn()
    src = state["sources"]
    sha_ok = all(v["sha256_before"] == v["sha256_after"] for v in src.values())
    stop = []
    for cid, res in results.items():
        for rec in res["criteria"]:
            if rec["pass"] is False and (cid == "P2" or rec["id"].startswith("P5.oakley.zero_moved")
                                         or "moved_in_failing" in rec["id"] or "moved_set" in rec["id"]
                                         or "seat_moves_only" in rec["id"]):
                stop.append(f"{rec['id']} ({rec['by']})")
    summary = {cid: {"pass": r["pass"], "criteria": len(r["criteria"]), "counts": r["counts"],
                     "failed": r["failed"], "not_applicable": r["not_applicable"]} for cid, r in results.items()}
    summary["sources_sha256_unchanged"] = sha_ok
    summary["stop_rule_triggered"] = stop
    from glasses3d.tests.proofs.wp4_proof import tool_hashes
    recorded = state.get("tools_sha256") or {}
    now = tool_hashes()
    summary["tools"] = {"recorded_combined_sha256": recorded.get("*"), "consistent_across_cases":
                        bool(state.get("tools_consistent")), "current_equals_recorded": recorded == now,
                        "changed_since_run": sorted(k for k in set(recorded) | set(now)
                                                    if k != "*" and recorded.get(k) != now.get(k))}
    summary["owner_readings"] = [
        "P2 'outside the ranges capture unchanged / vertices bit-identical' is graded outside the 3 mm lift ramp "
        "beyond each x-range end (and below the lens centre); inside the ramp the lift and the seat move the lens "
        "edge: see P2.outside_capture_unchanged.*.measured.excluded_ramp_stations (owner to confirm the reading)",
        "§10 '~N' read as +-10%"]
    return {"design": "glasses3d/plan/WP4_DESIGN.md §10 P2-P5", "out_dir": str(OUT), "sources": src,
            "summary": summary, "cases": results, "run": {c: {"seconds": m["seconds"], "error": m.get("error")}
                                                          for c, m in state["cases"].items()},
            "fixture_sha256": state.get("fixture_sha256"), "tools_sha256": recorded}
