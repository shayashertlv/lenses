"""Region statuses and the deterministic JSON writer (WP5_DESIGN §9)."""
import json
from types import SimpleNamespace

import numpy as np

from glasses3d.g3d_host.photoprint import report as RP


def _region(rid, name, minor=False):
    return SimpleNamespace(rid=rid, name=name, minor=minor, area_mm2=100.0, facing=name.split("/")[-1],
                           part_name=name.split("[")[0])


def _plan(anchors, partners):
    return SimpleNamespace(anchors=anchors, partners=partners)


def _row(rid, h, fold, px, ok):
    crit = {k: {"pass": ok, "margin": 0.1 if ok else -0.1} for k in ("C1", "C2")}
    crit.update({k: {"pass": ok} for k in ("C3", "C4")})
    return {"rid": rid, "held_out": h, "eval_fold": fold, "pixels": px, "b2_eligible": px >= 1000,
            "b2_pass": ok and px >= 1000, "criteria": crit}


def test_statuses():
    regions = [_region(0, "Brow_Right[+x]/front"), _region(1, "Brow_Left[-x]/front"), _region(2, "Temple_Left[-x]/outer"),
               _region(3, "Temple_Left[-x]/bottom"), _region(4, "Brow_Right[+x]/outer", minor=True),
               _region(5, "Brow_Right[+x]/top")]
    plan = _plan({0: "05", 1: "05", 2: "03", 5: "04"}, {0: ["01"], 1: ["01"], 2: [], 5: ["01"]})
    registered = {0: ["05", "01"], 1: ["05"], 2: ["03"], 5: ["04", "01"]}
    rows = [_row(0, "01", "A", 1500, True), _row(0, "01", "B", 900, False), _row(0, "05", "B", 1400, True),
            _row(0, "05", "A", 1300, True),
            _row(5, "01", "A", 1500, True), _row(5, "01", "B", 1200, False)]
    gates = [{"rid": 1, "residual_mm": 1.9, "source": "reference-05.png", "target": "reference-01.png", "fold": "B",
              "fit_fold": "A"},
             {"rid": 1, "residual_mm": 2.1, "source": "reference-01.png", "target": "reference-05.png", "fold": "A",
              "fit_fold": "B"}]
    st = RP.region_statuses(regions, plan, registered, rows, gates)
    assert st[0]["status"] == "registered"                # the 900 px row is ungraded, not a failure
    assert st[0]["ungraded_rows"] == [{"held_out": "01", "eval_fold": "B", "pixels": 900}]
    assert st[1]["status"] == "anchor-only" and st[1]["gate_residual_mm"] == [1.9, 2.1]
    assert st[2]["status"] == "single-view"
    assert st[3]["status"] == "unseen" and st[4]["status"] == "minor"
    assert st[5]["status"] == "check_failed" and st[5]["check_failed_rows"][0]["failed"] == ["C1", "C2", "C3", "C4"]
    assert RP.overall(st) == "check_failed"
    rows2 = [_row(0, "01", "A", 1500, True), _row(0, "01", "B", 900, True)]
    st2 = RP.region_statuses(regions[:1], plan, {0: ["05", "01"]}, rows2, [])
    assert st2[0]["status"] == "check_failed" and st2[0]["no_gradable_row_in_folds"] == ["B"]


def test_json_is_deterministic_and_strict(tmp_path):
    obj = {"b": np.float32(1.5), "a": [np.int64(3), np.bool_(True), float("nan")], "c": np.arange(3),
           "seconds": 4.2, "nested": {"seconds": 1.0, "x": 2}}
    p1 = RP.write_json(tmp_path / "a.json", RP.strip_timing(obj))
    p2 = RP.write_json(tmp_path / "b.json", RP.strip_timing(obj))
    t1 = open(p1, "rb").read()
    assert t1 == open(p2, "rb").read()
    d = json.loads(t1)
    assert d == {"b": 1.5, "a": [3, True, None], "c": [0, 1, 2], "nested": {"x": 2}}


def _fit(par, broken=()):
    return {"guards": [{"view": "reference-01.png", "guard": g, "group": None, "start": 0.9, "final": 0.85,
                        "drop": 0.05, "within": False} for g in broken]
            + [{"view": "reference-02.png", "guard": "whole", "group": None, "start": 0.9, "final": 0.9, "drop": 0.0,
                "within": True}],
            "pose": {"parallax": {n: {"a_mm": a, "b_mm": b} for n, (a, b) in par.items()}}}


def _crow(region, h, fold, ok=True):
    crit = {k: {"pass": ok, "margin": 0.2} for k in ("C1", "C2")}
    crit.update({k: {"pass": ok} for k in ("C3", "C4")})
    return {"region": region, "rid": 0, "held_out": h, "eval_fold": fold, "pixels": 1500, "b2_eligible": True,
            "b2_pass": ok, "criteria": crit}


def _write_run(tmp_path, *, par_a, broken_all=(), rows=None):
    regs = [{"name": "Brow_Right[+x]/front", "facing": "front", "part": "Brow_Right"},
            {"name": "Brow_Left[-x]/front", "facing": "front", "part": "Brow_Left"},
            {"name": "Temple_Left[-x]/inner", "facing": "inner", "part": "Temple_Left"}]
    par = {"Brow_Right[+x]/front": par_a, "Brow_Left[-x]/front": (3.3, 3.2), "Temple_Left[-x]/inner": (0.2, 0.1)}
    RP.write_json(tmp_path / "registration.json", {"regions": regs, "fits": {
        "A": _fit(par), "B": _fit(par), "all": _fit(par, broken_all)}})
    RP.write_json(tmp_path / "check_print.json", {"rows": rows or [], "diagnostic_rows": []})
    statuses = {"Brow_Right[+x]/front": {"name": "Brow_Right[+x]/front", "status": "registered", "anchor": "05",
                                         "registered_views": ["05", "01"]},
                "Brow_Left[-x]/front": {"name": "Brow_Left[-x]/front", "status": "anchor-only", "anchor": "05",
                                        "registered_views": ["05"]},
                "Temple_Left[-x]/inner": {"name": "Temple_Left[-x]/inner", "status": "registered", "anchor": "06",
                                          "registered_views": ["reference-06.png", "reference-01.png"]}}
    RP.write_json(tmp_path / "report.json", {"regions": statuses})


def test_finalize_b2_covers_every_registered_held_out_view(tmp_path):
    rows = [_crow("Brow_Right[+x]/front", "reference-01.png", "A"), _crow("Brow_Right[+x]/front", "reference-05.png", "B"),
            _crow("Temple_Left[-x]/inner", "reference-06.png", "A", ok=False)]
    _write_run(tmp_path, par_a=(1.0, 0.5), rows=rows)
    rep = RP.finalize(tmp_path, a0_passed=True)
    assert rep["B2"]["graded"] == 3 and rep["B2"]["passed"] == 2 and not rep["B2"]["pass"]
    assert {r["held_out"] for r in rep["B2"]["rows"]} == {"reference-01.png", "reference-05.png", "reference-06.png"}


def test_finalize_stop_a_fires_on_any_b1_failure(tmp_path):
    rows = [_crow("Brow_Right[+x]/front", "reference-01.png", f) for f in "AB"]
    _write_run(tmp_path, par_a=(3.55, -3.04), rows=rows)          # |a| + |b| = 6.59 mm on a registered region
    rep = RP.finalize(tmp_path, a0_passed=True)
    assert not rep["B1"]["pass"] and not rep["B1"]["parallax_within_4mm"]
    assert rep["B1"]["parallax_over_4mm"]["Brow_Right[+x]/front"]["gate_registered"]
    assert rep["B1"]["parallax_over_4mm"]["Brow_Left[-x]/front"]["gate_registered"] is False   # listed, not failing
    assert rep["stop_a"]["fired"]
    b1 = [r for r in rep["stop_a"]["reasons"] if r.startswith("B1 failed")]
    assert len(b1) == 1 and "Brow_Right[+x]/front: parallax over 4 mm" in b1[0] and "6.59" in b1[0]
    assert "Brow_Left" not in b1[0]
    # guards broken only in the 'all' fit also fail B1 and fire STOP A
    _write_run(tmp_path, par_a=(1.0, 0.5), broken_all=("whole",), rows=rows)
    rep = RP.finalize(tmp_path, a0_passed=True)
    assert not rep["B1"]["pass"] and rep["stop_a"]["fired"]
    assert any(r.startswith("B1 failed") and "fit all broke guards" in r for r in rep["stop_a"]["reasons"])
    # and with everything in order nothing fires
    _write_run(tmp_path, par_a=(1.0, 0.5), rows=rows)
    rep = RP.finalize(tmp_path, a0_passed=True)
    assert rep["B1"]["pass"] and not rep["stop_a"]["fired"], rep["stop_a"]


def test_finalize_names_the_a0_failure(tmp_path):
    _write_run(tmp_path, par_a=(1.0, 0.5), rows=[_crow("Brow_Right[+x]/front", "reference-01.png", f) for f in "AB"])
    rep = RP.finalize(tmp_path, a0_passed=False, a0_summary="A0 FAIL (literal): residual 0.60 mm at 04")
    assert rep["stop_a"]["fired"] and rep["stop_a"]["reasons"][0] == "A0 failed: A0 FAIL (literal): residual 0.60 mm at 04"


def test_pixel_verifiable_share():
    regions = [_region(0, "Brow_Right[+x]/front"), _region(1, "Brow_Left[-x]/front"), _region(2, "Temple_Left[-x]/top")]
    regions[0].area_mm2, regions[1].area_mm2, regions[2].area_mm2 = 255.6, 255.6, 7350 - 511.2
    plan = SimpleNamespace(parallax=[0, 1])
    st = {0: {"status": "registered"}, 1: {"status": "anchor-only"}, 2: {"status": "single-view"}}
    pv = RP.pixel_verifiable(st, regions, plan)
    assert pv["total_mm2"] == 7350.0 and pv["verified_share"] == round(255.6 / 7350, 4) == 0.0348
    assert pv["max_verifiable_share"] == round(511.2 / 7350, 4)
    assert [r["region"] for r in pv["regions_with_a_partner"]] == ["Brow_Right[+x]/front", "Brow_Left[-x]/front"]


def test_finalize_missing_a0_fires_stop_a(tmp_path):
    """§9 requires A0 to pass: a run without A0's verdict cannot clear STOP A (fix round 2)."""
    _write_run(tmp_path, par_a=(1.0, 0.5), rows=[_crow("Brow_Right[+x]/front", "reference-01.png", f) for f in "AB"])
    rep = RP.finalize(tmp_path)
    assert rep["stop_a"]["fired"] and rep["stop_a"]["a0"] == "missing"
    assert rep["stop_a"]["reasons"] == ["A0 verdict not supplied (tests/photoprint/test_register_synthetic.py, "
                                        "data/wp5/a0/a0.json)"]
    assert RP.finalize(tmp_path, a0_passed=True)["stop_a"] == {"fired": False, "reasons": [], "a0": "passed"}


def _reg(fold, par, loss, converged=True, seed=None, runs=None):
    lay = [["par", "0", 0], ["par", "0", 1]]
    info = {"fold": fold, "loss_final": loss, "converged": converged, "layout": lay,
            "start_offset": [0.5, -0.5] if seed else None,
            "history": [{"runs": runs or [{"parallax_mm": {"R/front": list(par)}}]}]}
    return SimpleNamespace(pose=SimpleNamespace(parallax={0: par}), info=info)


def test_parallax_stability_spread_and_lowest_loss():
    regions = [_region(0, "R/front")]
    plan = SimpleNamespace(parallax=[0], anchors={0: "05"})
    runs = [{"parallax_mm": {"R/front": [3.0, -3.0]}}, {"parallax_mm": {"R/front": [1.0, 0.5]}}]
    regs = {"A": _reg("A", (1.0, 0.5), 0.50, runs=runs)}
    sregs = {"A-s1": _reg("A", (1.2, 0.3), 0.49, seed=1), "A-s2": _reg("A", (0.9, 0.6), 0.52, converged=False, seed=2)}
    st = RP.parallax_stability(regs, sregs, plan, regions)["R/front"]["A"]
    assert [e["start"] for e in st["starts"]] == ["wp3b", "A-s1", "A-s2"]
    # d(-1) = a - b: 0.5, 0.9, 0.3 -> 0.6; d(+1) = a + b: 1.5, 1.5, 1.5 -> 0
    assert abs(st["spread_mm"] - 0.6) < 1e-12 and st["lowest_loss_start"] == "A-s1" and not st["all_converged"]
    assert abs(st["restart_spread_mm"] - 5.5) < 1e-12        # the first run ended at d = (6, 0) vs final (0.5, 1.5)
    assert st["starts"][1]["start_parallax_mm"] == [0.5, -0.5] and st["starts"][0]["start_parallax_mm"] is None
    w = RP.warnings({}, regs, plan, regions, {}, sregs)
    assert any("A-s1 ended at a lower loss" in x for x in w)
