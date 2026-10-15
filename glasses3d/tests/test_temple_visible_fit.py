"""M5-fix-8: visible angles have a narrow, original-evidence-bound authority, never a general fit bypass."""
from __future__ import annotations

import copy
import json
import math

import numpy as np
import pytest

from glasses3d.g3d.temple import fit as F, fixtures as FX, spec as S
from glasses3d.g3d.temple.build import build_temple


def design(sd=1.0, valley=0.0):
    """Two triangular planes, shared horizontal edge; only D is exclusive to the chosen lower face.

    All observations come from the analytic truth, independently of the fitter. Broad errors make the flat-looking
    fold unresolved; a precise flat or signed valley is a predeclared negative control.
    """
    rise = 8 * math.tan(math.radians(valley / 2))
    xyz = {"P": (20., 0., 68.), "Q": (60., 0., 68.), "D": (40., -8., 68. + rise),
           "U": (40., 8., 68. + rise)}
    return FX.straight(nodes={n: [d, y, {"read": {"id": n, "X": x, "sd": sd}}] for n, (d, y, x) in xyz.items()},
                       facets={"lower": ["P", "D", "Q"], "upper": ["P", "Q", "U"]},
                       base={"top": [[0., 8.], [120., 8.]], "bottom": [[0., 8.], [120., 8.]]})


def select(s, degrees=12., shape="ridge", moving="lower"):
    s = copy.deepcopy(s)
    s["creases"] = [{"facets": ["lower", "upper"], "shape": shape,
                     "visible_angle": {"degrees": degrees, "moving_facet": moving, "comparison_id": "fixture-cmp1",
                                       "comparison_sha256": "a" * 64, "reason": "chosen against rendered photo crops"}}]
    return s


def target(result):
    return next(r for r in result.creases if r["name"] == "lower|upper")


@pytest.mark.parametrize("degrees,shape,moving", [(6., "ridge", "lower"), (12., "ridge", "lower"),
                                                   (16., "valley", "lower"), (11., "ridge", "upper")])
def test_exact_visible_angle_preserves_all_unrelated_geometry_and_observations(degrees, shape, moving):
    s = design()
    before = json.dumps(s, sort_keys=True)
    baseline = F.fit_spec(s)
    result = F.fit_spec(select(s, degrees, shape, moving))
    assert target(baseline)["state"] == target(result)["state"] == "open"
    assert target(result)["normal_deg"] == pytest.approx(degrees, abs=1e-7)
    expected = (-1 if shape == "ridge" else 1) * 8 * math.tan(math.radians(degrees))
    exclusive = "D" if moving == "lower" else "U"
    assert result.nodes[exclusive]["X"] - baseline.nodes[exclusive]["X"] == pytest.approx(expected, abs=1e-9)
    for n in s["nodes"]:
        assert result.spec["nodes"][n][:2] == baseline.spec["nodes"][n][:2]
        if n != exclusive:
            assert result.nodes[n]["X"] == baseline.nodes[n]["X"]
            assert result.nodes[n]["residual_mm"] == baseline.nodes[n]["residual_mm"]
    fixed = "upper" if moving == "lower" else "lower"
    assert result.spec["facets"][fixed] == baseline.spec["facets"][fixed]
    assert result.camera == baseline.camera
    assert result.model["visible_angles"]["unrelated_residuals_unchanged"] is True
    tb = build_temple(result.spec)
    assert tb.checks["closed"] and tb.checks["euler"] == 2
    assert tb.tables["thickness"]["min_mm"] >= tb.spec.options["t_min_mm"]
    assert json.dumps(s, sort_keys=True) == before


def test_local_uncertain_reading_cost_can_exceed_old_gate_but_is_reported_honestly():
    result = F.fit_spec(select(design(), 25., "valley"))
    assert result.chi2 - result.chi2_free > F.ACCEPT_DCHI2
    assert result.nodes["D"]["residual_sd"] > 2
    v = result.model["visible_angles"]
    assert v["local_cost_change"] == pytest.approx(result.chi2 - v["baseline_chi2"])
    assert v["choices"][0]["reading_residuals"]["D"]["residual_sd"] == result.nodes["D"]["residual_sd"]
    text = " ".join(result.report_lines(limit=99))
    assert "unresolved local-face readings" in text and "full residual cost is shown" in text


@pytest.mark.parametrize("sd,valley,state", [(.01, 0., "flat"), (.05, 10., "certain"), (.05, -10., "certain")])
def test_original_certain_and_flat_measurements_are_not_eligible(sd, valley, state):
    s = design(sd, valley)
    assert target(F.fit_spec(s))["state"] == state
    with pytest.raises(S.TempleError, match="not unresolved") as exc:
        F.fit_spec(select(s))
    assert exc.value.code == "E_FIT_SHAPE"
    assert exc.value.fields["measurement"][0]["state"] == state


def test_choice_is_checked_against_original_baseline_not_an_ordinary_declaration():
    s = select(design(.01), 12)
    # The new field cannot turn a precise flat reading into an eligible angle just by requesting a ridge.
    with pytest.raises(S.TempleError, match="original readings-only"):
        F.fit_spec(s)


@pytest.mark.parametrize("override", [None, 8., {"read": {"X": 68., "sd": 99.}}])
def test_typed_solved_or_inflated_error_cannot_replace_original_stored_evidence(override):
    original = design(.01, 10.)
    stored = {n: {"name": n, "d": d, "y": y, "X": t["read"]["X"], "sd_X": t["read"]["sd"]}
              for n, (d, y, t) in original["nodes"].items()}
    s = design()
    for node in s["nodes"]:
        s["nodes"][node][2] = copy.deepcopy(override)
    assert target(F.fit_spec(s))["state"] == "open"
    with pytest.raises(S.TempleError, match="not unresolved") as exc:
        F.fit_spec(select(s), readings=stored)
    assert exc.value.fields["measurement"][0]["state"] == "certain"


@pytest.mark.parametrize("lift,state", [(0., "flat"), (.6, "certain")])
def test_unresolved_target_cannot_contradict_another_certain_or_flat_crease(lift, state):
    s = design()
    for n in ("P", "Q", "D"):
        s["nodes"][n][2]["read"]["sd"] = .001
    s["nodes"]["D"][2]["read"]["X"] += lift
    baseline = F.fit_spec(s)
    assert target(baseline)["state"] == "open"
    protected = next(c for c in baseline.creases if c["pair"] == ["lower", "base"])
    assert protected["state"] == state
    with pytest.raises(S.TempleError, match="original .* classification"):
        F.fit_spec(select(s))


def test_sign_certainty_does_not_invent_an_exact_angle_interval_gate():
    s = design()
    for n in ("P", "Q", "D"):
        s["nodes"][n][2]["read"]["sd"] = .001
    s["nodes"]["D"][2]["read"]["X"] -= .6
    baseline = F.fit_spec(s)
    assert target(baseline)["state"] == "open"
    result = F.fit_spec(select(s))
    old = next(c for c in baseline.creases if c["pair"] == ["lower", "base"])
    new = next(c for c in result.creases if c["pair"] == ["lower", "base"])
    assert old["state"] == "certain" and old["deg"] * new["deg"] > 0
    assert abs(new["deg"]) >= S.FLAT_CREASE_DEG
    assert abs(new["deg"] - old["deg"]) > 2 * old["sd_deg"]
    assert result.nodes["D"]["residual_sd"] < -2       # visible choice reports, rather than hides, this local misfit


def test_shared_non_edge_corner_cannot_move_another_face():
    s = design()
    s["nodes"]["T"] = [70., -9., {"read": {"X": 68., "sd": 1.}}]
    s["facets"]["other"] = ["D", "T", "Q"]
    baseline = F.fit_spec(s)
    assert target(baseline)["state"] == "open"
    with pytest.raises(S.TempleError, match="shared away from the rotation edge"):
        F.fit_spec(select(s))


@pytest.mark.parametrize("pair", [["base", "lower"], ["lower", "base"]])
def test_base_pair_order_cannot_escape_structural_validation(pair):
    s = select(design())
    s["creases"][0]["facets"] = pair
    with pytest.raises(S.TempleError) as exc:
        F.fit_spec(s)
    assert exc.value.code == "E_SPEC"


def test_thickness_is_never_clamped_or_waived():
    with pytest.raises(S.TempleError, match="thickness"):
        F.fit_spec(select(design(), 60., "ridge"))


def test_unrelated_ordinary_declaration_keeps_its_original_cost_refusal():
    s = select(design(.05, 10.))
    s["creases"].append({"facets": ["upper", "base"], "shape": "valley"})
    # This test's main target is also ineligible; an ordinary refusal must still retain the old facts/threshold.
    ordinary = copy.deepcopy(s)
    ordinary["creases"] = ordinary["creases"][1:]
    try:
        F.fit_spec(ordinary)
    except S.TempleError as a:
        with pytest.raises(S.TempleError) as b:
            F.fit_spec(s)
        assert b.value.code == a.code and b.value.fields == a.fields
    else:
        pytest.fail("predeclared ordinary contradiction did not refuse")


def test_persisted_fitted_spec_retains_choice_and_builder_enforces_its_exact_angle():
    result = F.fit_spec(select(design(), 12))
    text = S.canonical_json(result.spec)
    restored = json.loads(text)
    assert restored["creases"][0]["visible_angle"] == select(design())["creases"][0]["visible_angle"]
    a, b = build_temple(result.spec), build_temple(restored)
    assert np.array_equal(a.V, b.V) and a.checks == b.checks
    assert S.spec_sha256(result.spec) == S.spec_sha256(restored)
    restored["creases"][0]["visible_angle"]["degrees"] = 14.
    with pytest.raises(S.TempleError, match="visible angle declared") as exc:
        build_temple(restored)
    assert exc.value.code == "E_CREASE_SHAPE"


@pytest.mark.parametrize("field,value", [("degrees", True), ("degrees", float("nan")), ("degrees", 0),
                                       ("degrees", 180), ("degrees", 2), ("moving_facet", "missing"),
                                       ("comparison_sha256", "forged"), ("comparison_id", ""), ("reason", " ")])
def test_malformed_visible_declarations_are_not_a_bypass(field, value):
    s = select(design())
    s["creases"][0]["visible_angle"][field] = value
    with pytest.raises(S.TempleError) as exc:
        F.fit_spec(s)
    assert exc.value.code == "E_SPEC"


def test_caller_cannot_supply_an_eligibility_flag():
    s = select(design(.01))
    s["creases"][0]["visible_angle"]["measurement_state"] = "open"
    with pytest.raises(S.TempleError) as exc:
        F.fit_spec(s)
    assert exc.value.code == "E_SPEC"


def test_unresolved_report_has_no_flat_measurement_word_and_separates_angles():
    row = target(F.fit_spec(design()))
    text = F._crease_line(row)
    assert "built fold" in text and "; measurement" in text and "unresolved" in text
    assert "flat" not in text
    row = target(F.fit_spec(design(.01)))
    assert "flat within error" in F._crease_line(row)


def test_absent_optional_field_preserves_canonical_hash_and_geometry():
    original = FX.analytic_prism()
    from hashlib import sha256
    raw = S.canonical_json(original)
    assert "visible_angle" not in raw
    assert S.spec_sha256(original) == sha256(raw.encode()).hexdigest()
    tb = build_temple(original)
    assert np.array_equal(tb.V, build_temple(json.loads(raw)).V)
