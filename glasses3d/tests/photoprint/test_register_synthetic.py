"""A0 (WP5_DESIGN §8): synthetic registration on q001 with a known print (tests/photoprint/a0.py, synth.py).

One registration (fold 'all', 10k samples per region, 1500 evaluations per band) is shared by every assertion; the
whole A0 result is also written to ``data/wp5/a0/a0.json`` when the proof runs (tests/proofs/wp5_ft1119.py).

A0's verdict is ``a0.run_a0``'s ``pass`` = all of ``criteria``: the design's §8 list AS WRITTEN (fix round 2: the
residual rule at every view, ``residual_every_view``), the gate's strip null (a perfect match registers, swapped /
shifted / foreign photos are refused) and the null-bias estimator correction recorded in PROGRESS. KNOWN STATE (a
tripwire, update deliberately): A0 FAILS on q001, on ``residual_every_view`` only (three thin grazing views outside the
fit, 0.24-0.33 mm); it would pass only under the known-limit exemption, which is unrecorded (no owner waiver, no PROGRESS
decision). Parameter recovery is reported (about 1 mm of depth error on the two temple regions whose depth no fitted
view sees). The false-registration sweep reports margins and the rate; the 3D-x periodic strip print is a KNOWN FALSE
POSITIVE of the strip null, pinned as xfail(strict)."""
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


A0_FAILING_KNOWN = ["residual_every_view"]


def test_a0_verdict_is_the_literal_rule(result):
    """Fix round 2: the every-view residual rule is the criterion (no unrecorded exemption). KNOWN STATE: A0 fails on
    it alone; the exemption's pass is reported with its status."""
    crit = result["criteria"]
    assert set(crit) == {"residual_every_view", "target_registered_both_directions", "unperturbed_bp",
                         "null_bias", "refuses_astra_as_photo", "refuses_camera_plus_2mm", "refuses_swapped_photos",
                         "blurred_fails_C3", "guards_within", "refuses_by_null_alone"}
    assert result["pass"] == all(crit.values())
    assert [k for k, v in crit.items() if not v] == A0_FAILING_KNOWN, crit
    assert result["verdict_line"].startswith("A0 FAIL (failing: residual_every_view)")
    rep = result["reported"]
    assert rep["residual_except_known_limits"] and rep["would_pass_under_known_limit_exemption"]
    assert rep["exemption_status"] == "unrecorded" and "unrecorded" in result["verdict_line"]
    passed, line, note = a0.summary_for_step0(result)
    assert passed is False and line == result["verdict_line"] and "literal" in note and "unrecorded" in note


def test_parallax_recovery_is_reported(result):
    """§8 'parameter recovery reported' (risk 2): the target's parallax is recovered; the regions whose depth no fitted
    view sees carry about 1 mm of error, which the verdict line states."""
    pe = result["recovery"]["parallax_errors"]
    assert pe[a0.TARGET]["max_abs_error_mm"] < 0.2, pe[a0.TARGET]
    worst = max(pe.values(), key=lambda v: v["max_abs_error_mm"])
    assert worst["max_abs_error_mm"] > 0.5
    assert "parallax recovery:" in result["verdict_line"] and "error up to" in result["verdict_line"]


def test_code_recovers_the_truth_at_the_fitted_views(result):
    fitted = [r for r in result["residuals"] if r["in_fit"]]
    bad = [r for r in fitted if not r["pass"]]
    assert fitted and not bad, bad


def test_every_failing_residual_row_is_a_thin_view_outside_the_fit(result):
    """Every residual row over 0.2 mm is a view outside the objective that sees the region on fewer samples than a
    partner needs (the fit neither fits nor gates it). Fix round 2: these rows are graded (A0 fails on them)."""
    limit_keys = {(r["region"], r["view"]) for r in result["known_limits"]["rows"]}
    over = [r for r in result["residuals"] if not r["pass"]]
    assert over and all((r["region"], r["view"]) in limit_keys and not r["in_fit"] and r["samples"] < 1000
                        for r in over), over
    assert result["criteria"]["residual_every_view"] is False
    assert result["known_limits"]["exemption_status"] == "unrecorded"


def test_target_region_registers_both_ways_with_the_strip_null(result):
    for k in ("registered_05_to_01", "registered_01_to_05"):
        g = result["gates"][k]
        assert g["registered"] and g["null"] == "strip" and g["decoys"] >= 4, (k, g.get("reason"))
        assert g["residual_mm"] <= 0.2
    # the legacy displacement-of-the-photo null cannot (0-1 usable decoys on the thin oblique brow front)
    assert not any(v["registered_by_rule"] for v in result["gates_displace_null"].values())


def test_unperturbed_round_trip_bp(result):
    u = result["unperturbed"]
    assert u["bp_1_4px_r"] >= a0.BP_MIN, u


def test_null_bias_is_bounded_and_mean_abs_is_its_spread(result):
    u = result["unperturbed"]
    assert u["null_bias"] <= a0.NULL_MAX, u
    # the literal estimator: about 0.8 x the null's spread, not a bias (reported only)
    assert u["null_mean_abs"] > u["null_bias"] and u["null_n"] >= 12


@pytest.mark.parametrize("case", ["astra_as_photo_05_to_01", "camera_plus_2mm_05_to_01", "photo06_as_01_05_to_01",
                                  "photo06_as_01_01_to_05"])
def test_gate_refuses(result, case):
    g = result["gates"][case]
    assert not g.get("registered"), (case, g.get("reason"), g.get("residual_mm"))


def test_blurred_bake_fails_c3(result):
    assert not result["blurred"]["C3_pass"], result["blurred"]


def test_guards_hold(result):
    assert result["criteria"]["guards_within"], [g for g in result["fit"]["guards"] if not g["within"]]


def test_null_alone_refuses_a_wrong_match_on_strip_print(result):
    """Fix round 1: a case where ONLY the decoy rule can refuse. On the strip-elongated print (part periodic with the
    null's 8 mm period), a photo whose print is shifted 8 mm along the strip clears the floor (peak >= 0.45, irange,
    residual <= 0.8 mm) in both directions and is refused by peak < decoy max + 0.15."""
    for k in ("wrong_shift8_05_to_01", "wrong_shift8_01_to_05"):
        c = result["strip_print"]["cases"][k]
        assert c["registered"], (k, c)                       # floor-only: would register without the null
        assert c["peak"] >= 0.45 and c["residual_mm"] <= 0.8, (k, c)
        assert not c["registered_by_rule"] and c["reason"] == "warmth_peak_below_decoys", (k, c)
        assert c["decoys"] >= 4
    assert result["criteria"]["refuses_by_null_alone"]


def test_strip_print_power_is_reported(result):
    """Where the null binds (decoy maxima like real havana's 0.41-0.87), whether a PERFECT match still registers."""
    sp = result["strip_print"]
    for k in ("true_05_to_01", "true_01_to_05"):
        c = sp["cases"][k]
        assert c["peak"] is not None and c["decoy_overlaps"] is not None
    assert isinstance(sp["power"]["true_registered_both_directions"], bool)
    assert "strip print (null where it binds)" in result["verdict_line"]


def test_every_refusal_reports_its_margin(result):
    """Fix round 2: each refused gate case says how far it is from registering and which tests refuse it."""
    rm = result["refusal_margins"]
    for k in ("astra_as_photo_05_to_01", "camera_plus_2mm_05_to_01", "photo06_as_01_05_to_01",
              "photo06_as_01_01_to_05", "strip_wrong_shift8_05_to_01", "strip_wrong_shift8_01_to_05"):
        r = rm[k]
        assert not r["registered"] and r["refused_by"] and r["margin"] is not None and r["margin"] > 0, (k, r)
    assert rm["registered_05_to_01"]["registered"] and rm["registered_05_to_01"]["margin"] is None


def test_false_registration_sweep_is_reported(result):
    """Several wrong correspondences (shifted strip print, two seeds; shifted isotropic print; photo 03 as 01): the rate
    and the smallest refusal margin are reported (never in ``pass``)."""
    fr = result["false_registration"]
    assert fr["n_ordinary"] >= 16 and 0.0 <= fr["rate_without_known_false_positive"] <= 1.0
    assert len({c["region"] for c in fr["cases"].values()}) >= 2                 # more than one region
    assert all("margin" in c and "refused_by" in c for c in fr["cases"].values())
    assert "false-registration sweep" in result["verdict_line"]


@pytest.mark.xfail(strict=True, reason="KNOWN FALSE POSITIVE of the strip null (fix round 2): a print periodic in 3D x "
                                       "(not along the null's own surface coordinate) shifted 8 mm registers")
def test_x3d_periodic_wrong_match_is_refused(result):
    kfp = result["false_registration"]["known_false_positive_registered"]
    assert kfp and not any(kfp.values()), kfp
