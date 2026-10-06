"""The STOP A assembler (tests/proofs/wp5_stopA.py): gate rows per region and partner, and §9's four conditions."""
from glasses3d.tests.proofs import wp5_stopA as SA


def _gate(rid, p, w, v, fold, fit, reg, res=0.3, reason=None, peak=0.7, agree=True):
    return {"rid": rid, "partner": p, "source": w, "target": v, "fold": fold, "fit_fold": fit, "E_px": 900,
            "registered": reg, "significant": reason is None, "reason": reason, "residual_mm": res,
            "irange_agrees": agree, "decoys": 0 if reason == "too_few_decoys" else 5,
            "signals": {"warmth": {"peak": peak, "decoy_max": 0.3}, "lum": {"peak": 0.5, "residual_mm": res}},
            "decoy_rows": [{"overlap_at_offset": 0.5}] * 6}


A, P = "reference-05.png", "reference-01.png"
PLAN = [{"rid": 0, "name": "Brow_Right[+x]/front", "anchor": A, "partners": [P], "parallax": True},
        {"rid": 1, "name": "Brow_Right[+x]/top", "anchor": "reference-04.png", "partners": [], "parallax": False}]


def _four(reg_last=True, res_last=0.3, reason_last=None):
    return [_gate(0, P, A, P, "B", "A", True), _gate(0, P, A, P, "A", "B", True),
            _gate(0, P, P, A, "B", "A", True), _gate(0, P, P, A, "A", "B", reg_last, res_last, reason_last),
            _gate(0, P, A, P, "all", "all", True)]


def test_region_gates_needs_all_four_fold_rows():
    g = SA.region_gates(_four(), PLAN)
    assert list(g) == ["Brow_Right[+x]/front"]               # regions without a partner have no gate rows
    e = g["Brow_Right[+x]/front"]["partners"]["01"]
    assert e["registered_both_directions_both_folds"] and e["rows_registered"] == 4 and len(e["all_fit_rows"]) == 1
    g2 = SA.region_gates(_four(False, 1.2, None), PLAN)["Brow_Right[+x]/front"]["partners"]["01"]
    assert not g2["registered_both_directions_both_folds"] and g2["residual_mm_max"] == 1.2
    assert g2["floor_only_rows_registered"] == 3              # 1.2 mm is over 0.8 mm without decoys too
    g3 = SA.region_gates(_four(False, 0.4, "too_few_decoys"), PLAN)["Brow_Right[+x]/front"]["partners"]["01"]
    assert g3["rows_registered"] == 3 and g3["floor_only_rows_registered"] == 4


def _guards(drop01=0.01, broken=()):
    rows = [{"view": P, "guard": "whole", "group": None, "start": 0.87, "final": 0.87 - drop01, "drop": drop01,
             "within": drop01 <= 0.02}]
    rows += [{"view": P, "guard": "temple", "group": g, "start": 0.7, "final": 0.67, "drop": 0.03, "within": False}
             for g in broken]
    return {f: {"rows": rows, "broken": [r for r in rows if not r["within"]], "view01_whole": rows[0]}
            for f in ("A", "B", "all")}


def _rep(status="registered", views=(A, P), failed=None):
    st = {"status": status, "anchor": A, "registered_views": list(views)}
    if failed:
        st["check_failed_rows"] = failed
    return {"regions": {"Brow_Right[+x]/front": st,
                        "Brow_Left[-x]/front": {"status": "anchor-only", "anchor": A, "registered_views": [A]}}}


def test_stop_a_conditions():
    gates = SA.region_gates(_four(), PLAN)
    ok = SA.stop_a({"pass": True}, _rep(), gates, _guards())
    assert not ok["fired"] and [c["fired"] for c in ok["conditions"]] == [False, False, False, False]
    assert SA.stop_a({"pass": False, "verdict_line": "x"}, _rep(), gates, _guards())["conditions"][0]["fired"]
    none = SA.stop_a({"pass": True}, _rep("anchor-only", (A,)), gates, _guards())
    assert none["fired"] and none["conditions"][1]["fired"] and "none was registered" in none["conditions"][2]["detail"]
    cf = SA.stop_a({"pass": True}, _rep("check_failed", failed=[{"held_out": "01", "failed": ["C2"]}]), gates,
                   _guards())
    assert cf["conditions"][1]["fired"] and cf["conditions"][2]["fired"]      # a check_failed front is not registered
    gb = SA.stop_a({"pass": True}, _rep(), gates, _guards(0.025))
    assert gb["fired"] and gb["conditions"][3]["fired"] and gb["conditions"][3]["view01_whole_drop"]["A"] == 0.025
    tb = SA.stop_a({"pass": True}, _rep(), gates, _guards(broken=("temple-x",)))
    assert tb["conditions"][3]["fired"] and "01 temple temple-x" in tb["conditions"][3]["detail"]["A"][0]
