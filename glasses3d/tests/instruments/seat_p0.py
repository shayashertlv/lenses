"""WP4 P0 and the host half of P1 on the four real GLBs (WP4_DESIGN.md §10). Tests only.

    .venv/Scripts/python -m glasses3d.tests.instruments.seat_p0 [--out glasses3d/data/wp4/p0_p1.json]

P0 (core vs the independent grid instrument, at the INSTRUMENT's station frames), three comparisons per lens:
- `graded` (binding): the capture check_glb grades -- the core's `measure` with the lens as its last part
  (`capture_along` + `lens_edge`, WP4_DESIGN §3) -- against the instrument's occupancy-grid measurement of the same
  §3 definition (`seat_grid` `cap_graded`); |delta| <= 0.075 mm at >= 99% of stations;
- `designers` (continuity with R's prototype numbers, which compared this way): the core's line intervals converted
  to the instrument port's own definition (the first frame material from u = -4 mm, edge on the rung) against the
  port (`cap_fractions`);
- `definition_gap` (reported, not graded): the core's graded capture against the port's own definition, i.e. how
  far the two DEFINITIONS differ on real meshes, with the cause of every disagreeing depth value: frame material
  lying inside the lens body without reaching its edge ('detached': the port counts it as capture, §3 does not).
Also the outer front loop lengths (138.0 / 143.6 / 383.5 / 458.1 mm +- 0.1), the cap triangle counts and the riders
check_glb finds.

P1 (host): numpy vs open3d over the same core frames, every measured quantity within 1e-3 mm, the same classes and
owners; check_glb twice -> byte-identical JSON.

Band: the designers' eyewire-band instrument (`seat_grid.eyewire_band`) on q001 against `stations.buried_band` at
the instrument's frames.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np

from glasses3d.g3d.seat import stations
from glasses3d.g3d.seat.intervals import merge
from glasses3d.g3d_host import seat_check as SC
from glasses3d.tests.instruments import seat_grid as IG

REPO = Path(__file__).resolve().parents[3]
JOBS = REPO / "automation/data/blender_agent/studio/jobs"
GLBS = {
    "ft": JOBS / "3b8a1c7ae8114868b49b298b681f1b29/original/model.glb",
    "prada": JOBS / "8c82d986e6c14d26b4b38f3807712ea0/agent/ar/prada-pr17ws-delivery-3e3ac97b/model.glb",
    "oakley": JOBS / "3dff867466d449c9a6369273f0a14631/agent/ar/h_detailed_closed_shield-6c7d3bd5/model.glb",
    "invu": JOBS / "c8e81c7214a646cfa07e082c59128f10/agent/ar/final-invu-ia22403b-dd80df73/model.glb",
}
LOOP_MM = {"ft": 138.0, "prada": 143.6, "oakley": 383.5, "invu": 458.1}      # §10 P0
RIDERS = {"prada": ["subtle PRADA etch"], "oakley": ["OAKLEY", "PRIZM"], "invu": ["INVU corner print"]}
CAP_TRIANGLES_R = {"ft": 3196, "prada": 11005, "oakley": 4288, "invu": 28224}  # §3 [R]
DECLARED = {"ft": ("browline", [(-56.2, -11.6), (11.6, 56.2)]), "prada": ("acetate", None),
            "oakley": ("semi_rimless", None), "invu": ("semi_rimless", None)}  # §10 P2/P3/P5 frame types
P0_TOL_MM, P0_SHARE = 0.075, 0.99
P1_TOL_MM = 1e-3
BEYOND = 90.0                                  # capture values >= this mean 'frame from beyond the grid' (99)


def available(key: str) -> bool:
    return GLBS[key].is_file()


def lens_inputs(key: str) -> list[tuple]:
    """(body, [frame Components]) per lens body, the same selection check_glb makes."""
    col = SC.collect(SC.load(GLBS[key]))
    return [(b, [c for _, c in SC.parts_near(b, col["solids"])]) for b in col["bodies"]]


def instrument_definition(meas: stations.Measurement, F: dict) -> np.ndarray:
    """Capture as the grid PORT defines it, from the core's own intervals on the instrument's depth rows."""
    N = len(F["uf"])
    out = np.full((N, len(stations.DEPTHS)), np.nan)
    for k in range(N):
        for j, f in enumerate(stations.DEPTHS):
            per = meas.depth_parts[k][j] if meas.depth_parts[k] is not None else None
            if per is None or any(iv is None for iv in per):
                continue
            e = F["uf"][k] + f * (F["ub"][k] - F["uf"][k])
            U = merge(per)
            U = U[(U[:, 1] > IG.U0 - e) & (U[:, 0] < IG.U1 - e)]
            if len(U):
                out[k, j] = 99.0 if U[0, 0] <= IG.U0 - e else -U[0, 0]
    return out


def compare(ci: np.ndarray, cc: np.ndarray, tol: float = P0_TOL_MM) -> dict:
    """Per-station agreement of two (N, 5) capture arrays (nan = no frame within reach, >= 90 = beyond the grid):
    a station is bad when any depth differs by more than `tol` or is defined in one and not the other."""
    fin_i, fin_c = np.isfinite(ci) & (ci < BEYOND), np.isfinite(cc) & (cc < BEYOND)
    both = fin_i & fin_c
    d = np.where(both, np.abs(ci - cc), np.nan)
    mismatch = (np.isfinite(ci) != np.isfinite(cc)) | ((np.nan_to_num(ci) >= BEYOND) != (np.nan_to_num(cc) >= BEYOND))
    bad_depth = (np.nan_to_num(d, nan=0.0) > tol) | mismatch
    bad = bad_depth.any(1)
    q = np.nanpercentile(d, [50, 95, 99]) if both.any() else [np.nan] * 3
    return {"depths_compared": int(both.sum()), "stations_bad": int(bad.sum()),
            "share_agree": round(float(1 - bad.mean()), 4) if len(bad) else None,
            "abs_delta_p50_p95_p99_max": [round(float(x), 4) for x in q]
            + [round(float(np.nanmax(d)), 4) if both.any() else None],
            "signed_mean_first_minus_second": round(float(np.nanmean(np.where(both, ci - cc, np.nan))), 4)
            if both.any() else None,
            "definedness_mismatch_stations": int(mismatch.any(1).sum()), "_bad_depth": bad_depth}


def _beyond_grid(m: stations.Measurement, ee: np.ndarray) -> np.ndarray:
    """The core's graded capture with frame that starts before the instrument's grid marked 99, as the grid does."""
    start = ee + m.edge - m.cap                                     # frame start along u from Pm
    return np.where(np.isfinite(m.cap) & (start <= IG.U0), 99.0, m.cap)


def detached(m: stations.Measurement, k: int, j: int, rung_u: float) -> bool:
    """Frame material on this depth row that ends before reaching the lens edge, inside the port's window (from
    u = -4 mm of the station mid point, where the port starts looking): material inside the lens body."""
    per = m.depth_parts[k][j] if m.depth_parts[k] is not None else None
    if per is None:
        return False
    U = merge(per)
    e = float(m.edge[k, j])
    return bool(len(U) and ((U[:, 1] < e) & (rung_u + U[:, 1] > IG.U0)).any())


def p0_lens(body, parts) -> dict:
    t0 = time.time()
    ins = IG.analyse(body.V, body.T, [(c.name, c.V, c.T) for c in parts], 1.0, stations.DEPTHS, full_grid=False)
    t1 = time.time()
    F = ins["frames"]
    fr = stations.frames_from_arrays(F["Pf"], F["Pb"], F["n"], F["t"], F["u"], F["s"])
    allp = [(c.V, c.T) for c in parts] + [(body.V, body.T)]
    m = stations.measure(fr, SC.make_backend("open3d", allp), len(parts), keep_depths=True)
    t2 = time.time()
    ee = F["uf"][:, None] + np.asarray(stations.DEPTHS)[None, :] * (F["ub"] - F["uf"])[:, None]
    graded_core = _beyond_grid(m, ee)
    graded = compare(ins["cap_graded"], graded_core)
    designers = compare(ins["cap_fractions"], instrument_definition(m, F))
    gap = compare(ins["cap_fractions"], graded_core)
    bad = gap.pop("_bad_depth")
    causes = {"detached_frame_inside_the_lens": 0, "other": 0}
    by_depth = [0] * len(stations.DEPTHS)
    for k, j in zip(*np.nonzero(bad)):
        causes["detached_frame_inside_the_lens" if detached(m, k, j, ee[k, j]) else "other"] += 1
        by_depth[j] += 1
    gap["bad_depth_values"] = int(bad.sum())
    gap["bad_depth_values_by_depth"] = dict(zip([str(f) for f in stations.DEPTHS], by_depth))
    gap["causes"] = causes
    gb = graded.pop("_bad_depth")
    designers.pop("_bad_depth")
    worst = [{"station": int(k), "xyz": np.round(fr.P[k], 2).tolist(),
              "instrument": np.round(ins["cap_graded"][k], 3).tolist(), "core": np.round(graded_core[k], 3).tolist()}
             for k in np.flatnonzero(gb.any(1))[:5]]
    return {"lens": body.name, "stations": int(len(fr)), "instrument_perimeter_mm": round(ins["perimeter"], 3),
            "core_loop_mm": round(body.perimeter_mm, 3), "cap_triangles_core": list(body.cap_triangles()),
            "cap_triangles_instrument": list(ins["cap_tris"]), "parts": [c.name for c in parts],
            "graded": graded, "designers": designers, "definition_gap": gap,
            "edge_core_vs_grid_max_abs_mm": round(float(np.nanmax(np.abs(ee + m.edge - ins["edge_graded"]))), 4),
            "seconds_instrument_core": [round(t1 - t0, 2), round(t2 - t1, 2)], "worst_graded": worst}


def p1_lens(body, parts) -> dict:
    fr = stations.station_frames(body)
    allp = [(c.V, c.T) for c in parts] + [(body.V, body.T)]
    out, times = {}, {}
    for kind in ("open3d", "numpy"):
        t0 = time.time()
        be = SC.make_backend(kind, allp)
        m = stations.measure(fr, be, len(parts))
        band = stations.buried_band(fr, m, be, len(parts))
        times[kind] = round(time.time() - t0, 2)
        out[kind] = (m, band)
    (a, ba), (b, bb) = out["open3d"], out["numpy"]
    res = {"lens": body.name, "points": int(len(fr)), "seconds": times}
    for key in ("cap", "room", "cover_front", "cover_back", "edge"):
        x, y = getattr(a, key), getattr(b, key)
        res[key] = {"finite_mismatch": int((np.isfinite(x) != np.isfinite(y)).sum()),
                    "max_abs_mm": float(np.max(np.abs(np.where(np.isfinite(x) & np.isfinite(y), x - y, 0.0))))}
    res["band_front_max_abs_mm"] = float(np.nanmax(np.abs(np.nan_to_num(ba.front_mm) - np.nan_to_num(bb.front_mm)))) \
        if len(fr) else 0.0
    res["band_candidates_equal"] = bool((ba.candidate == bb.candidate).all())
    res["classes_equal"] = bool((a.cls == b.cls).all())
    res["owners_equal"] = bool((a.owner == b.owner).all())
    res["recast_points"] = [int((a.recast > 0).sum()), int((b.recast > 0).sum())]
    res["unmeasured"] = [int(a.unmeasured.sum()), int(b.unmeasured.sum())]
    res["edge_offset_max_abs_mm"] = float(np.abs(a.edge).max())
    return res


def band_q001() -> list[dict]:
    """The designers' eyewire-band instrument on q001 vs the core's buried_band at the instrument's frames (brow and
    eyewire only, as the designers measured)."""
    col = SC.collect(SC.load(GLBS["ft"]))
    by = {c.name: c for _, c in col["solids"]}
    out = []
    for body in col["bodies"]:
        side = "Left" if "Left" in body.name else "Right"
        brow, ew = by[f"Brow_{side}.001"], by[f"Eyewire_{side}.001"]
        res = IG.eyewire_band(body.V, body.T, (brow.V, brow.T), (ew.V, ew.T))
        A = res["rows"]
        F = res["frames"]
        fr = stations.frames_from_arrays(F["Pf"], F["Pb"], F["n"], F["t"], F["u"], F["s"])
        be = SC.make_backend("open3d", [(brow.V, brow.T), (ew.V, ew.T)])
        m = stations.measure(fr, be)
        band = stations.buried_band(fr, m, be)
        kk = np.array([r["k"] for r in A])
        front = np.array([r["front_mm"] for r in A])
        back = np.array([r["back_mm"] for r in A])
        cand = band.candidate[kk]
        df = band.front_mm[kk[cand]] - front[cand]
        db = band.back_mm[kk[cand]] - back[cand]
        out.append({"lens": body.name, "instrument_stations": int(len(A)),
                    "instrument_front_p50_max": [round(float(np.median(front)), 3), round(float(front.max()), 3)],
                    "instrument_front_share_gt_0.05": round(float(np.mean(front > 0.05)), 3),
                    "instrument_back_p50": round(float(np.median(back)), 3),
                    "core_candidates_at_instrument_stations": int(cand.sum()),
                    "held_parts": [["Brow", "Eyewire"][i] for i in band.held],
                    "front_delta_max_abs": round(float(np.abs(df).max()), 4) if cand.any() else None,
                    "back_delta_max_abs": round(float(np.abs(db).max()), 4) if cand.any() else None,
                    "instrument_at_non_candidates_front_back_max": [round(float(front[~cand].max()), 3),
                                                                     round(float(back[~cand].max()), 3)]
                    if (~cand).any() else None,
                    "non_candidate_x": sorted(round(r["x"], 1) for r, c in zip(A, cand) if not c)})
    return out


def run(keys=("ft", "prada", "oakley", "invu"), p1_keys=("ft", "prada", "oakley", "invu")) -> dict:
    out = {"p0": {}, "p1": {}, "riders": {}, "check_inferred": {}, "check_declared": {}}
    for key in keys:
        if not available(key):
            out["p0"][key] = "missing"
            continue
        inputs = lens_inputs(key)
        out["p0"][key] = [p0_lens(b, parts) for b, parts in inputs]
        if key in p1_keys:
            out["p1"][key] = [p1_lens(b, parts) for b, parts in inputs]
        t0 = time.time()
        r1 = SC.check_glb(GLBS[key], stations_table=True)
        t1 = time.time()
        r2 = SC.check_glb(GLBS[key], stations_table=True)
        ft, rng = DECLARED[key]
        rd = SC.check_glb(GLBS[key], frame_type=ft, hidden_x_range_mm=rng, stations_table=True)
        for name, r, extra in (("check_inferred", r1, {"seconds": round(t1 - t0, 2), "bit_identical_runs":
                                                       json.dumps(r1, sort_keys=True) == json.dumps(r2, sort_keys=True)}),
                               ("check_declared", rd, {"frame_type": ft, "ranges": rng})):
            out[name][key] = dict(extra, ok=r["ok"], reasons=r["reasons"], lenses=[
                {"lens": L["lens"], "frame_type": L["frame_type"], "source": L["frame_type_source"],
                 "pass": L["pass"], "graded_pass": L["graded_pass"], "classes": L["classes"],
                 "verdicts": L["verdicts"], "parts": L["parts"],
                 "failing_nodes": len({s["node"] for s in L["stations"] if s["verdict"] == "FAIL"}),
                 "nodes": len({s["node"] for s in L["stations"]}), "sectors": L["sectors"],
                 "rim_split_deg": L.get("rim_split_deg"), "band": L.get("band")} for L in r["lenses"]])
        out["riders"][key] = sorted({r["node"] for r in r1["riders"]})
    if available("ft"):
        out["band_q001"] = band_q001()
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(REPO / "glasses3d/data/wp4/p0_p1.json"))
    a = ap.parse_args(argv)
    res = run()
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(res, indent=1), encoding="utf-8")
    print(a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
