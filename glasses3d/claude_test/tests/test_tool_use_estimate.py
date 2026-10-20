"""Arithmetic and receipt-backed checks for the offline T1 planning scenarios."""
from decimal import Decimal

import pytest

from glasses3d.claude_test.cache_replay import DEFAULT_RUN, load_profile, replay
from glasses3d.claude_test.pricing import price_usage
from glasses3d.claude_test.tool_use_estimate import (
    balanced, comparison_samples, distribute_comparisons,
    expanded_scenario, source_steps, write_report,
)
from glasses3d.claude_test.tests.test_cache_replay import profile_fixture


def test_balancing_and_insertion_preserve_totals_and_final_source():
    assert sum(balanced(Decimal(68) / 3, n) for n in range(1, 24)) == 521
    for sources, extras in ((45, 23), (45, 45), (69, 35), (69, 70)):
        counts = distribute_comparisons(sources, extras)
        assert len(counts) == sources
        assert counts[0] == 0
        assert sum(counts) == extras
        assert max(counts[1:]) - min(counts[1:]) <= 1
    with pytest.raises(ValueError):
        distribute_comparisons(1, 2)


def test_comparison_measurement_matches_complete_result_not_whole_followup():
    profile = profile_fixture()
    call = {"type": "tool_use", "id": "c", "name": "compare_to_photos", "input": {}}
    result = {"type": "tool_result", "tool_use_id": "c", "content": [
        {"type": "text", "text": "abcdef"},
        {"type": "image", "source": {"type": "file", "file_id": "i"}},
    ]}
    profile[1]["added"] = [{"value": call}, {"value": result},
                            {"value": {"type": "text", "text": "unrelated" * 1000}}]
    measured = comparison_samples(profile, {"i": (28, 56)}, Decimal(1))
    assert len(measured["samples"]) == 1
    assert measured["samples"][0]["image_tokens"] == 2
    assert measured["samples"][0]["images"] == 1
    assert measured["samples"][0]["result_bytes"] < 300
    result["is_error"] = True
    with pytest.raises(ValueError, match="adapter error"):
        comparison_samples(profile, {"i": (28, 56)}, Decimal(1))


def fake_comparison():
    return {"means": {
        "modeled_result_tokens": "100000", "call_tokens": "20", "images": "30",
        "result_bytes": "10000", "call_bytes": "100", "response_output_tokens": "200",
        "image_tokens": "99000", "text_byte_div_3_tokens": "1000",
        "request_start_gap_seconds": "60",
    }}


def test_renewals_add_requests_and_every_row_reconciles_with_tariff():
    profile = profile_fixture()
    renewal = {"opening_tokens": 50000, "image_occurrences": 6, "payload_bytes": 5000,
               "summary_output_tokens": 4000}
    base = replay(profile, {}, renewal=renewal)
    result = expanded_scenario(profile, base=base, extra_count=23,
                               comparison=fake_comparison(), renewal=renewal)
    assert result["productive_requests"] == 68
    assert result["total_requests_including_renewal_summaries"] == 68 + len(result["renewals"])
    assert len(result["renewals"]) >= 2
    assert [row["source_request"] for row in result["requests"] if row["kind"] == "source"] == list(range(1, 46))
    assert result["requests"][-1]["kind"] == "source"
    assert result["comparison_result_tokens_added"] == 2_300_000
    assert result["comparison_image_occurrences_added"] == 690
    assert result["comparison_result_bytes_added"] == 230000
    independent = Decimal(0)
    for row in result["requests"] + result["renewals"]:
        read, write = row["cache_read_tokens"], row["cache_write_tokens"]
        independent += price_usage("claude-opus-5-5", {
            "input_tokens": row["input_tokens"] - read - write, "output_tokens": row["output_tokens"],
            "cache_read_input_tokens": read, "cache_creation_input_tokens": write,
            "cache_creation": {"ephemeral_1h_input_tokens": write, "ephemeral_5m_input_tokens": 0},
        })
    assert independent == Decimal(result["cost_usd"])
    for item in result["renewals"]:
        first = result["requests"][item["before_productive_request"] - 1]
        assert first["cache_write_tokens"] == 50000
        assert first["cache_read_tokens"] == 0
    assert result["elapsed_request_start_seconds"] == str(Decimal(44 * 60 + 23 * 60 + len(result["renewals"])))
    assert sum(row["comparison_output_tokens_consumed_by_request_or_summary"] for row in result["requests"]) == 23 * 200


def test_zero_additions_reproduces_f1_baseline_costs_and_renewals():
    profile = profile_fixture(growth=14000)
    renewal = {"opening_tokens": 1200, "image_occurrences": 6, "payload_bytes": 5000,
               "summary_output_tokens": 4000}
    for count in (45, 70):
        base = replay(profile, {}, count=count, renewal=renewal)
        result = expanded_scenario(profile, base=base, extra_count=0,
                                   comparison=fake_comparison(), renewal=renewal)
        assert result["cost_usd"] == base["cost_usd"]
        assert result["total_requests_including_renewal_summaries"] == count
        assert len(result["renewals"]) == len(base["renewals"])
    assert len(source_steps(profile, count=69)) == 69


@pytest.mark.skipif(not DEFAULT_RUN.is_dir(), reason="local C5 receipts are not committed")
def test_actual_c5_results_and_f1_baselines(tmp_path):
    result = write_report(DEFAULT_RUN, tmp_path)
    assert len(result["comparison_measurement"]["samples"]) == 3
    assert [row["result_in_request"] for row in result["comparison_measurement"]["samples"]] == [23, 30, 37]
    assert [row["images"] for row in result["comparison_measurement"]["samples"]] == [29, 29, 12]
    assert sum(row["result_bytes"] for row in result["comparison_measurement"]["samples"]) == 35456
    assert result["calibration"]["mismatched_requests"] == []
    assert result["calibration"]["relative_error"] == "0"
    assert len((tmp_path / "estimate_summary.md").read_text(encoding="utf-8").splitlines()) == 6
    assert [row["added_comparison_requests"] for row in result["scenarios"].values()] == [23, 45, 35, 70]
    assert [row["baseline_productive_requests"] for row in result["scenarios"].values()] == [45, 45, 69, 69]
    assert all(Decimal(row["cost_usd"]) > Decimal(row["baseline_cost_usd"])
               for row in result["scenarios"].values())
    profile, dims = load_profile(DEFAULT_RUN)
    for count in (45, 70):
        base = replay(profile, dims, count=count, renewal=result["renewal_template"])
        without_additions = expanded_scenario(
            profile, base=base, extra_count=0,
            comparison=result["comparison_measurement"], renewal=result["renewal_template"])
        assert without_additions["cost_usd"] == base["cost_usd"]
        assert without_additions["total_requests_including_renewal_summaries"] == count
