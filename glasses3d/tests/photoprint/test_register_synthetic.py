"""A0 (WP5_DESIGN §8): synthetic registration on q001 with a known print (tests/photoprint/a0.py, synth.py).

One registration (fold 'all', 10k samples per region, 1500 evaluations per band) is shared by every assertion; the
whole A0 result is also written to ``data/wp5/a0/a0.json`` when the proof runs (tests/proofs/wp5_ft1119.py).

A0's verdict is the design's LITERAL criteria (``a0.run_a0``: ``pass`` = all of ``criteria``). Three of them fail on
the current build (fix round 2) and are marked ``xfail(strict=True)`` with the measured reason, so the suite stays honest both ways:
it cannot report them as passing, and a change that makes them pass turns the xfail into a failure that must be
looked at. The remaining tests check what the code guarantees (the fit recovers the truth at the views it fits, the
gate refuses what it must) and are not A0's verdict."""
import pytest

from glasses3d.tests.photoprint import a0

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def result(synthetic):
    return a0.run_a0(job_truth=synthetic, log=None)


def test_plan_frees_only_the_angled_view(result):
    plan = result["plan"]
    assert plan["free_cameras"] == ["reference-01.png"]
    assert "reference-05.png" in plan["fixed_views"]


def test_objective_has_every_partner_pair(result):
    """§5.3 "sum over view pairs" (fix round 2 default): the brow fronts fit their fixed partner 04 as well."""
    assert result["plan"]["fit_fixed_partners"] is True
    fronts = [r for r in result["plan"]["regions"] if r["parallax"] and r["name"].endswith("/front")]
    assert fronts and all(r["fit_partners"] == r["partners"] and "reference-04.png" in r["fit_partners"]
                          for r in fronts)


def test_verdict_is_the_literal_conjunction(result):
    crit = result["criteria"]
    for k in ("residual_every_view", "unperturbed_bp", "null_mean_abs", "target_registered_both_directions",
              "refuses_astra_as_photo", "refuses_camera_plus_2mm", "refuses_swapped_photos", "blurred_fails_C3",
              "guards_within"):
        assert k in crit, k
    assert result["pass"] == all(crit.values())
    assert set(result["waiver_candidates"]) == {"residual_except_thin_views", "null_abs_of_mean"}
    assert "mean |null r|" in result["verdict_line"] and result["verdict_line"].startswith(
        "A0 PASS" if result["pass"] else "A0 FAIL")


def test_code_recovers_the_truth_at_the_fitted_views(result):
    """Not the A0 criterion (that is every view, below): the objective's own views end within 0.2 mm."""
    fitted = [r for r in result["residuals"] if r["in_fit"]]
    bad = [r for r in fitted if not r["pass"]]
    assert fitted and not bad, bad


@pytest.mark.xfail(strict=True, reason="A0 literal criterion fails (measured 2026-10-05, fix round 2, every partner "
                                       "pair, converged): only thin grazing views outside the objective, Brow_Left top "
                                       "at 03 (312 samples, 0.33 mm), -x temple inner at 04 (199, 0.30), +x temple "
                                       "outer at 04 (274, 0.24); owner waiver not given")
def test_a0_literal_residual_at_every_view(result):
    bad = [r for r in result["residuals"] if not r["pass"]]
    assert result["residuals"] and not bad, bad


def test_residual_holds_except_at_thin_views(result):
    """Not the A0 criterion: every row but the waiver candidate's thin views (outside the objective, fewer samples
    than a partner needs) is within 0.2 mm."""
    w = result["waiver_candidates"]["residual_except_thin_views"]
    assert w["pass"], w
    assert all(r["samples"] < 1000 for r in w["rows_waived"])


@pytest.mark.xfail(strict=True, reason="A0 literal criterion fails (measured 2026-10-05, fix round 2): with a decoy "
                                       "counted only where its own displacement keeps 60 % overlap (§5.4 step 5), the "
                                       "brow front seen from 01 keeps 1 decoy (05 -> 01; overlaps 0.69 / 0.50 / 0.41 / "
                                       "0.32 / 0.28 / 0.20) and 0 (01 -> 05) on PERFECT photos, so the 4-decoy rule "
                                       "refuses a 0.02 mm match with peak 0.995; owner decision on the decoy rule")
def test_target_region_registers_both_ways(result):
    assert result["criteria"]["target_registered_both_directions"], {
        k: (g.get("reason"), g.get("residual_mm"), g.get("decoys")) for k, g in result["gates"].items()
        if k.startswith("registered")}


def test_gate_without_decoys_registers_the_target_and_refuses_the_rest(result):
    """Diagnostic for the decoy decision: the 0.45 floor, irange agreement and the 0.8 mm residual alone register the
    truth both ways and refuse Astra's texture, the +2 mm camera and the swapped photos."""
    f = result["gates_floor_only"]
    assert f["registered_05_to_01"]["registered"] and f["registered_01_to_05"]["registered"], f
    for k in ("astra_as_photo_05_to_01", "camera_plus_2mm_05_to_01", "photo06_as_01_05_to_01",
              "photo06_as_01_01_to_05"):
        assert not f[k]["registered"], (k, f[k])


def test_unperturbed_round_trip_bp(result):
    u = result["unperturbed"]
    assert u["bp_1_4px_r"] >= a0.BP_MIN, u


@pytest.mark.xfail(strict=True, reason="A0 literal criterion fails (measured 2026-10-05): mean |null r| 0.057 > 0.05 "
                                       "on a perfect render (|mean r| 0.004); owner waiver not given")
def test_a0_literal_null_mean_abs(result):
    u = result["unperturbed"]
    assert u["null_mean_abs"] <= a0.NULL_MAX, u


@pytest.mark.parametrize("case", ["astra_as_photo_05_to_01", "camera_plus_2mm_05_to_01", "photo06_as_01_05_to_01",
                                  "photo06_as_01_01_to_05"])
def test_gate_refuses(result, case):
    g = result["gates"][case]
    assert not g.get("registered"), (case, g.get("reason"), g.get("residual_mm"))


def test_blurred_bake_fails_c3(result):
    assert not result["blurred"]["C3_pass"], result["blurred"]


def test_guards_hold(result):
    assert result["criteria"]["guards_within"], [g for g in result["fit"]["guards"] if not g["within"]]
