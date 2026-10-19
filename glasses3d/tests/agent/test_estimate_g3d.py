"""M2 recalibration: every complete paid run, exact token evidence, unchanged <=10% honest LOO gate.

The corpus predates pruning and covers one product's temple stage. These are forecasts, never permission to spend.
Historical Studio assertions remain unchanged in test_estimate.py; the new method is also refit on all 13 runs.
"""
from fractions import Fraction
import json
from pathlib import Path
import re

import pytest

from glasses3d.agent import estimate as E
from glasses3d.agent.approvals import canonical_json
from glasses3d.agent.budget import BudgetExceeded, RunLedger
from glasses3d.agent.pricing import Tariff, usd_text
from glasses3d.tests.agent import extract_g3d_corpus as X
from glasses3d.tests.agent.support import require_data


T = Tariff.frozen()
SETTLED = [5_599_202, 6_679_752, 3_598_669, 8_209_531, 6_766_442, 5_075_421, 3_846_039, 8_993_582]
PREDICTED = [7_342_744, 6_515_466, 2_660_656, 6_396_154, 6_102_078, 5_233_183, 3_840_824, 9_138_810]


def corpus(name="g3d-astra-8-tokens.json"):
    return json.loads((Path(__file__).with_name("data") / name).read_text(encoding="utf-8"))


def usages(run):
    return [dict(zip(E.FLAT_USAGE_KEYS, row[2:])) for row in run["rows"]]


def shapes(name="g3d-astra-8-tokens.json"):
    return [E.causal_run_shape(usages(run)) for run in corpus(name)["runs"]]


def test_complete_corpus_is_sanitized_token_evidence_and_reprices_exactly():
    data = corpus()
    assert set(data) == {"schema", "method", "about", "columns", "runs"}
    assert data["schema"] == "g3d.test.paid_corpus_tokens/1"
    assert data["method"] == E.G3D_METHOD
    assert data["columns"] == list(X.COLUMNS)
    assert len(data["runs"]) == 8
    assert [len(run["rows"]) for run in data["runs"]] == [35, 33, 15, 32, 30, 26, 20, 40]
    assert sum(len(run["rows"]) for run in data["runs"]) == 231
    assert [run["settled_micro_usd"] for run in data["runs"]] == SETTLED
    assert [shape.settled_micro for shape in shapes()] == SETTLED
    assert sum(SETTLED) == 48_768_638
    for run in data["runs"]:
        assert set(run) == {"run", "source_ledger_sha256", "source_config_sha256", "settled_micro_usd", "rows"}
        assert re.fullmatch(r"(?:owner-test-001|facet-astra-00[2-8])", run["run"])
        assert all(re.fullmatch(r"[0-9a-f]{64}", run[key])
                   for key in ("source_ledger_sha256", "source_config_sha256"))
        assert all(len(row) == len(X.COLUMNS) and all(type(value) is int and value >= 0 for value in row)
                   for row in run["rows"])
        assert all(row[1] == 16_384 for row in run["rows"])


def test_fixture_equals_every_canonical_source_ledger_and_config():
    for relative in X.CANONICAL_SOURCES:
        source = require_data(X.REPO / relative, "canonical g3d paid ledger")
        require_data(source.with_name("run-config.json"), "canonical g3d paid configuration")
    assert X.extract() == corpus()
    assert json.loads(X.render(corpus())) == corpus()


def test_all_eight_runs_reproduce_the_pooled_parameters_and_growth_band():
    records = shapes()
    fitted = E.causal_parameters(records)
    assert fitted == E.G3D_PARAMETERS
    assert fitted.growth == Fraction(6_720_790, 1_727)
    assert fitted.prefix_reuse == Fraction(4_312_580, 4_318_559)
    assert fitted.output == Fraction(109_182, 77)
    assert E.causal_growth_band(records) == E.G3D_GROWTH_BAND
    assert E.G3D_GROWTH_BAND == (min(s.growth for s in records), max(s.growth for s in records))
    for run, record in zip(corpus()["runs"], records):
        blocks = usages(run)
        # The causal structure is observed directly: each first request is uncached, and later requests mostly
        # reuse the preceding input. The old endpoint line overstates total exposure in all eight runs.
        assert blocks[0]["cached_tokens"] == 0
        reuse = Fraction(record.cached_after_first, record.previous_input_tokens)
        assert Fraction(9976, 10_000) < reuse <= 1
        observed = sum(block["input_tokens"] for block in blocks)
        assert record.requests * record.initial_tokens + record.exposure_denominator * record.growth == observed
        old = E.run_shape(blocks)
        assert old.requests * old.initial_tokens + record.exposure_denominator * old.parameters.growth > observed


def test_all_eight_held_out_runs_pass_the_unchanged_ten_percent_gate():
    records = shapes()
    backtests = E.leave_one_out_causal(records)
    assert len(backtests) == 8
    assert [test.estimate_micro for test in backtests] == PREDICTED
    assert E.median_absolute_error(backtests) <= Fraction(1, 10)
    assert round(float(E.median_absolute_error(backtests)) * 100, 8) == 6.46343308
    assert (round(float(min(test.error for test in backtests)) * 100, 2),
            round(float(max(test.error for test in backtests)) * 100, 2)) == (-26.07, 31.14)
    for index, test in enumerate(backtests):
        assert test.shape == records[index]
        assert test.parameters == E.causal_parameters(records[:index] + records[index + 1:])
        assert test.estimate_micro == E.project_g3d(test.shape.initial_tokens, test.shape.requests, 1,
                                                  test.parameters).spend_micro


def test_same_method_independently_refit_on_all_thirteen_studio_runs_passes_the_same_gate():
    records = shapes("studio-astra-13-tokens.json")
    assert len(records) == 13 and sum(record.requests for record in records) == 655
    backtests = E.leave_one_out_causal(records)
    assert E.median_absolute_error(backtests) <= Fraction(1, 10)
    assert round(float(E.median_absolute_error(backtests)) * 100, 8) == 8.67872814
    # This is a separately fitted diagnostic, never using the new corpus's constants on historical jobs.
    assert all(test.parameters != E.G3D_PARAMETERS for test in backtests)


def test_first_request_is_uncached_and_later_cache_cannot_include_new_growth():
    params = E.CausalParameters(Fraction(1, 3), Fraction(1, 2), Fraction(7, 2))
    blocks = [E.causal_request_usage(10, i, params) for i in range(5)]
    assert [block["input_tokens"] for block in blocks] == [10, 11, 11, 11, 12]
    assert [block["input_tokens_details"]["cached_tokens"] for block in blocks] == [0, 5, 5, 5, 5]
    assert [block["input_tokens_details"]["cache_write_tokens"] for block in blocks] == [10, 6, 6, 6, 7]
    assert all(block["output_tokens"] == 4 for block in blocks)
    full_reuse = E.CausalParameters(50, 1, 10)
    block = E.causal_request_usage(100, 1, full_reuse)
    assert block["input_tokens_details"] == {"cached_tokens": 100, "cache_write_tokens": 50}


def test_cost_step_and_holds_use_the_real_tariff_with_no_cache_credit_on_the_hold():
    params = E.CausalParameters(2_360, Fraction(99, 100), 850)
    projected = E.project_g3d(260_000, 10, 8_192, params)
    assert projected.first_long_context_request == 7
    assert projected.settled == tuple(T.settle_micro(E.causal_request_usage(260_000, i, params)) for i in range(10))
    assert projected.holds == tuple(T.reserve_micro(tokens, 8_192) for tokens in projected.inputs)
    without_reuse = E.project_g3d(260_000, 10, 8_192, E.CausalParameters(2_360, 0, 850))
    assert without_reuse.holds == projected.holds
    assert without_reuse.spend_micro > projected.spend_micro
    assert projected.settled[6] > projected.settled[5]


def test_causal_forecast_does_not_control_the_real_settled_cutoff(tmp_path):
    projected = E.project_g3d(26_000, 40, 16_384)

    def drive(cap, name):
        ledger = RunLedger.create(tmp_path / name / "budget.json", usd_text(cap))
        for i in range(projected.requests):
            usage = E.causal_request_usage(26_000, i, E.G3D_PARAMETERS)
            try:
                row = ledger.reserve(usage["input_tokens"], 16_384)
            except BudgetExceeded:
                assert ledger.totals()["settled_micro_usd"] == sum(projected.settled[:i]) >= cap
                assert len(ledger.rows) == i
                return i
            assert row["reserved_micro_usd"] == projected.holds[i]
            ledger.settle(row["sequence"], usage, response_id=f"resp_causal_{i}", request_id=None)
        assert ledger.totals()["settled_micro_usd"] == projected.spend_micro
        return projected.requests

    assert projected.required_cap_micro == 11_763_296
    assert drive(projected.required_cap_micro, "exact") == 40
    assert drive(projected.required_cap_micro - 1, "below-estimated-cap") == 40
    before_last = sum(projected.settled[:-1])
    assert drive(before_last, "at-settled-cutoff") == 39
    assert drive(before_last + 1, "below-settled-cutoff") == 40


def test_job_estimate_is_json_with_corpus_limits_range_tail_and_modeled_cap():
    result = E.estimate_g3d(26_000, 40, 16_384)
    assert json.loads(canonical_json(result)) == result
    assert result["schema"] == E.ESTIMATE_SCHEMA and result["method"] == E.G3D_METHOD
    assert result["corpus"] == E.G3D_CORPUS and result["limitations"] == list(E.G3D_LIMITATIONS)
    assert result["expected_micro_usd"] == 9_018_136
    assert result["range_micro_usd"] == [8_137_855, 10_254_125]
    assert result["tail_micro_usd"] == 3_041_363
    assert result["tail_micro_usd"] == T.reserve_micro(result["last_request_input_tokens"], 16_384)
    assert "no savings assumed" in result["text"] and "growth-only range" in result["text"]
    assert result["suggested_cap_micro_usd"] >= result["required_cap_micro_usd"]
    larger = E.estimate_g3d(26_000, 60, 16_384)
    assert larger["first_long_context_request"] is None
    assert larger["first_long_context_request_high"] == 52
    assert larger["expected_micro_usd"] > result["expected_micro_usd"]


@pytest.mark.parametrize("args", [(26_000, 0, 16_384), (26_000, 40, 0), (-1, 40, 16_384),
                                  (26_000, 40, 128_001), (26_000.0, 40, 16_384), (True, 40, 16_384),
                                  (900_000, 40, 16_384)])
def test_bad_forecast_inputs_fail_closed(args):
    with pytest.raises(E.EstimateError):
        E.estimate_g3d(*args)


def test_bad_parameters_band_and_corpus_fail_closed():
    for params in ((1, Fraction(3, 2), 1), (-1, 1, 1), (1, 1, -1), (1.5, 1, 1), (1, True, 1)):
        with pytest.raises(E.EstimateError):
            E.CausalParameters(*params)
    with pytest.raises(E.EstimateError, match="band"):
        E.estimate_g3d(26_000, 40, 16_384, growth_band=(Fraction(1), Fraction(2)))
    with pytest.raises(E.EstimateError):
        E.causal_parameters([])
    with pytest.raises(E.EstimateError):
        E.leave_one_out_causal(shapes()[:1])
    with pytest.raises(E.EstimateError):
        E.project_g3d(26_000, 40, 16_384, E.STUDIO_PARAMETERS)
