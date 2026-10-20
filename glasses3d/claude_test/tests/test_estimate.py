"""Offline planning arithmetic and evidence failures; no job, SDK or Blender."""
from __future__ import annotations

from decimal import Decimal, localcontext
import hashlib
import json
from pathlib import Path

import pytest

from glasses3d.claude_test import estimate, pricing


def write_json(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def profile(tmp_path):
    run = tmp_path / "astra-run"
    run.mkdir()
    rows = []
    for sequence, (inp, cached, written, output, reasoning) in enumerate(
        [(100, 0, 90, 20, 10), (200, 80, 100, 30, 20), (300, 240, 50, 10, 5)], 1
    ):
        rows.append({
            "sequence": sequence, "input_tokens": inp, "outcome": "settled",
            "settled_from": "transport", "http_status": 200,
            "request_id": f"req-{sequence}", "response_id": f"resp-{sequence}",
            "settled_micro_usd": sequence * 10,
            "usage": {"input_tokens": inp, "cached_tokens": cached, "cache_write_tokens": written,
                      "output_tokens": output, "reasoning_tokens": reasoning},
        })
    budget = {
        "schema": "g3d.run_budget/1", "run_id": "synthetic", "tariff": {"model": "gpt-6-astra"},
        "rows": rows, "violation": None, "blocked": None,
        "totals": {"requests": 3, "settled_micro_usd": 60, "held_micro_usd": 0,
                   "unknown_micro_usd": 0, "outcomes": {"settled": 3, "held": 0, "unknown": 0}},
    }
    renewals = {
        "schema": "g3d.conversation_renewals/1",
        "renewals": [{
            "schema": "g3d.conversation_renewal/1", "run_id": "synthetic", "number": 1,
            "old_conversation_id": "astra", "new_conversation_id": "astra-renewed",
            "summary_request_sequence": 2, "summary_skipped": None,
            "measurements": {"input_tokens": 250, "images": 6, "payload_bytes": 1000},
        }],
    }
    write_json(run / "budget.json", budget)
    write_json(run / "renewals.json", renewals)
    return run, budget, renewals


@pytest.fixture
def livecheck(tmp_path):
    path = tmp_path / "livecheck.json"
    receipt = {
        "schema": "g3d.claude_livecheck/1", "provider": "anthropic", "model": estimate.MODEL,
        "status": "completed", "offline": False, "settings": {"effort": "max"},
        "count_response": {"input_tokens": 100}, "input_tokens": 100,
    }
    write_json(path, receipt)
    return path, receipt


def test_known_profile_prices_writes_reads_and_inclusive_output_once(profile):
    run, _, _ = profile
    result = estimate.build_estimate(run, claude_opening=100)
    assert (result["low_usd"], result["central_usd"], result["high_usd"]) == (
        "0.002662", "0.003504", "0.005188"
    )
    p = result["astra_profile"]
    assert p["opening_counted_input_tokens"] == 100
    assert p["totals"] == {
        "input_tokens": 600, "cached_tokens": 320, "uncached_input_tokens": 280,
        "cache_write_tokens": 240, "plain_input_tokens": 40,
        "output_tokens": 60, "reasoning_tokens": 35,
    }
    assert p["request_count"] == 3
    assert p["settled_usd"] == "0.00006"
    assert p["renewal_count"] == 1
    assert p["renewals"][0]["summary_request_sequence"] == 2
    assert p["renewals"][0]["first_request_after_renewal"] == 3
    assert [row["estimated_claude_usd"]["central"] for row in p["requests"]] == [
        "0.001200", "0.001576", "0.000728",
    ]
    assert result["scenarios"]["central"]["output_tokens"] == "60"
    assert result["scenarios"]["central"]["cache_write_1h_usd"] == "0.002240"
    assert result["scenarios"]["central"]["cache_read_usd"] == "0.000064"
    assert result["output_history_basis"] == {
        "cache_write_1h": 60, "cache_read": 20, "conversation_end_sequences": [2, 3],
    }
    assert [row["output_history_basis"] for row in p["requests"]] == [
        {"cache_write_1h": 20, "cache_read": 0},
        {"cache_write_1h": 30, "cache_read": 20},
        {"cache_write_1h": 10, "cache_read": 0},
    ]
    assert [row["estimated_claude_usd"]["high"] for row in p["requests"]] == [
        "0.001760", "0.002420", "0.001008",
    ]
    assert [row["estimated_claude_usd"]["low"] for row in p["requests"]] == [
        "0.000920", "0.001154", "0.000588",
    ]


@pytest.mark.parametrize("opening,central", [(50, "0.002352"), (100, "0.003504"), (150, "0.004656"),
                                            (200, "0.005808"), (1, "0.001223")])
def test_ratio_scales_inputs_only(profile, opening, central):
    result = estimate.build_estimate(profile[0], claude_opening=opening)
    assert result["central_usd"] == central
    assert Decimal(result["input_ratio"]["decimal"]) == Decimal(opening) / 100
    assert result["input_ratio"]["numerator"] == opening
    assert result["input_ratio"]["denominator"] == 100
    assert result["scenarios"]["central"]["output_tokens"] == "60"


def test_fixed_k_bounds_are_not_claimed_as_confidence_bounds(profile):
    result = estimate.build_estimate(profile[0], claude_opening=100)
    assert {name: Decimal(row["output_multiplier"]) for name, row in result["scenarios"].items()} == {
        "low": Decimal("0.5"), "central": Decimal("1"), "high": Decimal("2"),
        "stress_5x": Decimal("5"), "stress_10x": Decimal("10"),
    }
    names = ("low", "central", "high", "stress_5x", "stress_10x")
    assert [result["scenarios"][name]["output_usd"] for name in names] == [
        "0.000600", "0.001200", "0.002400", "0.006000", "0.012000",
    ]
    assert [result[f"{name}_usd"] for name in names] == [
        "0.002662", "0.003504", "0.005188", "0.010240", "0.018660",
    ]
    assert [Decimal(result[f"{name}_usd"]) for name in names] == sorted(
        Decimal(result[f"{name}_usd"]) for name in names
    )
    assert any("not statistical bounds" in item for item in result["assumptions"])
    assert any("Ignore context-editing savings" in item for item in result["assumptions"])


def test_uses_tariff_table_not_hardcoded_rates(profile, monkeypatch):
    tariff = pricing.TARIFFS[estimate.MODEL]
    replacement = pricing.Tariff(
        Decimal("4"), Decimal("40"), Decimal("0.4"), Decimal("5"), Decimal("16"),
        tariff.source_url, tariff.checked,
    )
    monkeypatch.setitem(pricing.TARIFFS, estimate.MODEL, replacement)
    result = estimate.build_estimate(profile[0], claude_opening=100)
    assert result["central_usd"] == "0.007008"
    assert result["tariff_usd_per_million"]["cache_write_1h"] == "16"
    assert result["low_usd"] == "0.005324"
    assert result["high_usd"] == "0.010376"
    assert result["stress_5x_usd"] == "0.020480"
    assert result["stress_10x_usd"] == "0.037320"


def test_history_adjustments_use_separate_write_read_and_output_tariffs(profile, monkeypatch):
    tariff = pricing.TARIFFS[estimate.MODEL]
    monkeypatch.setitem(pricing.TARIFFS, estimate.MODEL, pricing.Tariff(
        Decimal("97"), Decimal("3"), Decimal("7"), Decimal("101"), Decimal("11"),
        tariff.source_url, tariff.checked,
    ))
    result = estimate.build_estimate(profile[0], claude_opening=100)
    # At 2x: 340 one-hour writes, 340 reads and 120 output, priced independently.
    high = result["scenarios"]["high"]
    assert high["cache_write_1h_usd"] == "0.003740"
    assert high["cache_read_usd"] == "0.002380"
    assert high["output_usd"] == "0.000360"
    assert high["total_usd"] == "0.006480"
    # Neither ordinary-input nor 5-minute-write tariffs apply to this model.
    assert result["central_usd"] == "0.005500"


@pytest.mark.parametrize("opening", [50, 100, 150, 1])
def test_history_deltas_are_signed_and_independent_of_opening_ratio(profile, opening):
    result = estimate.build_estimate(profile[0], claude_opening=opening)
    expected = {
        "low": (Decimal("-30"), Decimal("-10")),
        "central": (Decimal("0"), Decimal("0")),
        "high": (Decimal("60"), Decimal("20")),
        "stress_5x": (Decimal("240"), Decimal("80")),
        "stress_10x": (Decimal("540"), Decimal("180")),
    }
    for name, (written, read) in expected.items():
        scenario = result["scenarios"][name]
        assert {key: Decimal(value) for key, value in scenario["history_token_delta"].items()} == {
            "cache_write_1h": written, "cache_read": read,
        }
        assert Decimal(scenario["input_tokens"]["cache_write_1h"]) == Decimal(280 * opening) / 100 + written
        assert Decimal(scenario["input_tokens"]["cache_read"]) == Decimal(320 * opening) / 100 + read
    if opening == 1:
        # Preserve the requested signed planning arithmetic instead of silently clamping.
        assert Decimal(result["scenarios"]["low"]["input_tokens"]["cache_write_1h"]) == Decimal("-27.2")
        assert Decimal(result["scenarios"]["low"]["input_tokens"]["cache_read"]) == Decimal("-6.8")
        assert result["central_usd"] == "0.001223"


def test_no_renewal_reads_every_prior_output_once_per_later_request(profile):
    run, _, renewals = profile
    renewals["renewals"] = []
    write_json(run / "renewals.json", renewals)
    result = estimate.build_estimate(run, claude_opening=100)
    assert result["output_history_basis"] == {
        "cache_write_1h": 60, "cache_read": 70, "conversation_end_sequences": [3],
    }
    assert [row["output_history_basis"]["cache_read"] for row in result["astra_profile"]["requests"]] == [
        0, 20, 50,
    ]
    assert result["high_usd"] == "0.005198"
    assert result["scenarios"]["low"]["history_token_delta"] == {
        "cache_write_1h": "-30.0", "cache_read": "-35.0",
    }


def test_multiple_nonuniform_renewals_include_summary_but_reset_after_it(profile):
    run, budget, renewals = profile
    outputs = [11, 23, 37, 41, 53, 67, 79]
    rows = []
    for sequence, output in enumerate(outputs, 1):
        rows.append({
            "sequence": sequence, "input_tokens": 900 + sequence * 100, "outcome": "settled",
            "settled_from": "transport", "http_status": 200,
            "request_id": f"req-{sequence}", "response_id": f"resp-{sequence}",
            "settled_micro_usd": sequence * 10,
            "usage": {
                "input_tokens": 900 + sequence * 100, "cached_tokens": (sequence - 1) * 100,
                "cache_write_tokens": 900, "output_tokens": output, "reasoning_tokens": output - 1,
            },
        })
    budget["rows"] = rows
    budget["totals"].update(
        requests=7, settled_micro_usd=280, outcomes={"settled": 7, "held": 0, "unknown": 0}
    )
    template = renewals["renewals"][0]
    renewals["renewals"] = [{
        **template, "number": number, "old_conversation_id": f"epoch-{number}",
        "new_conversation_id": f"epoch-{number + 1}", "summary_request_sequence": end,
    } for number, end in enumerate([2, 5, 6], 1)]
    write_json(run / "budget.json", budget)
    write_json(run / "renewals.json", renewals)
    result = estimate.build_estimate(run, claude_opening=2000)
    assert result["output_history_basis"] == {
        "cache_write_1h": 311, "cache_read": 126, "conversation_end_sequences": [2, 5, 6, 7],
    }
    profile_rows = result["astra_profile"]["requests"]
    assert [row["output_history_basis"]["cache_write_1h"] for row in profile_rows] == outputs
    assert [row["output_history_basis"]["cache_read"] for row in profile_rows] == [0, 11, 0, 37, 78, 0, 0]
    # Summary and final responses retain their one write, even with no subsequent read.
    assert result["scenarios"]["high"]["input_tokens"] == {
        "cache_write_1h": "14311", "cache_read": "4326",
    }
    assert result["low_usd"] == "0.114693"
    assert result["central_usd"] == "0.119060"
    assert result["high_usd"] == "0.127793"
    assert result["stress_5x_usd"] == "0.153993"
    assert result["stress_10x_usd"] == "0.197659"
    # Last request: baseline 2000 writes/1200 reads, 79 output, one added 79-token write.
    assert profile_rows[-1]["estimated_claude_usd"]["high"] == "0.020032"
    for name in result["scenarios"]:
        rounded_rows = sum(Decimal(row["estimated_claude_usd"][name]) for row in profile_rows)
        assert abs(rounded_rows - Decimal(result[f"{name}_usd"])) <= Decimal("0.000004")


def test_fractional_tokens_and_low_decimal_precision_do_not_round_early(profile):
    run, budget, _ = profile
    budget["rows"][0]["input_tokens"] = 101
    budget["rows"][0]["usage"]["input_tokens"] = 101
    write_json(run / "budget.json", budget)
    with localcontext() as context:
        context.prec = 6
        result = estimate.build_estimate(run, claude_opening=100)
    # (281 * 100/101 * 8 + 320 * 100/101 * .2 + 60 * 20) / 1e6
    assert result["central_usd"] == "0.003489"
    assert result["input_ratio"]["numerator"] == 100
    assert result["input_ratio"]["denominator"] == 101
    assert "." in result["scaled_input_tokens"]["cache_write_1h"]


def test_real_livecheck_schema_and_sources_are_hashed(profile, livecheck):
    result = estimate.build_estimate(profile[0], livecheck=livecheck[0])
    assert result["placeholder"] is False
    assert result["kind"] == "owner_planning_estimate"
    assert result["claude_opening_count"] == 100
    assert set(result["sources"]) == {"astra_budget", "astra_renewals", "claude_livecheck",
                                     "pricing", "estimator"}
    for source in result["sources"].values():
        path = Path(source["path"])
        assert path.is_absolute()
        assert source["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
        assert source["selection"]


@pytest.mark.parametrize("scripted", [False, True])
def test_override_and_scripted_counts_are_unmistakable_placeholders(profile, livecheck, scripted):
    if scripted:
        livecheck[1]["offline"] = True
        write_json(livecheck[0], livecheck[1])
        result = estimate.build_estimate(profile[0], livecheck=livecheck[0])
    else:
        result = estimate.build_estimate(profile[0], claude_opening=100)
    assert result["placeholder"] is True
    assert result["kind"] == "placeholder_not_estimate"
    assert "PLACEHOLDER ONLY - NOT THE ESTIMATE" in estimate.summary(result)
    assert "PLACEHOLDER ONLY, NOT THE ESTIMATE" in result["assumptions"][-1]


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "100"])
def test_invalid_override_refuses(profile, value):
    with pytest.raises(estimate.EstimateError, match="positive integer"):
        estimate.build_estimate(profile[0], claude_opening=value)


@pytest.mark.parametrize("both", [False, True])
def test_exactly_one_count_source_required(profile, livecheck, both):
    kwargs = {"livecheck": livecheck[0], "claude_opening": 100} if both else {}
    with pytest.raises(estimate.EstimateError, match="exactly one"):
        estimate.build_estimate(profile[0], **kwargs)


@pytest.mark.parametrize("field,value", [
    ("schema", "other"), ("status", "failed"), ("provider", "other"), ("model", "claude-fable-5-1"),
    ("settings", {"effort": "high"}), ("count_response", None),
    ("count_response", {"input_tokens": 0}), ("count_response", {"input_tokens": True}),
    ("input_tokens", 101), ("offline", None),
])
def test_invalid_livecheck_refuses(profile, livecheck, field, value):
    livecheck[1][field] = value
    write_json(livecheck[0], livecheck[1])
    with pytest.raises(estimate.EstimateError, match="livecheck"):
        estimate.build_estimate(profile[0], livecheck=livecheck[0])


@pytest.mark.parametrize("name", ["budget.json", "renewals.json"])
@pytest.mark.parametrize("failure", ["missing", "malformed", "duplicate", "nan", "array"])
def test_missing_or_unreadable_receipt_refuses(profile, name, failure):
    path = profile[0] / name
    if failure == "missing":
        path.unlink()
    else:
        path.write_text({"malformed": "{", "duplicate": '{"a":1,"a":2}', "nan": '{"a":NaN}',
                         "array": "[]"}[failure], encoding="utf-8")
    with pytest.raises(estimate.EstimateError, match=name):
        estimate.build_estimate(profile[0], claude_opening=100)


@pytest.mark.parametrize("field,value", [
    ("usage", None), ("outcome", "unknown"), ("outcome", "held"), ("settled_from", "host"),
    ("http_status", 400), ("sequence", 2), ("input_tokens", 0), ("input_tokens", 101),
    ("request_id", None), ("response_id", None), ("settled_micro_usd", -1),
])
def test_invalid_settled_row_refuses(profile, field, value):
    run, budget, _ = profile
    budget["rows"][0][field] = value
    write_json(run / "budget.json", budget)
    with pytest.raises(estimate.EstimateError, match=r"budget.rows\[0\]"):
        estimate.reference_profile(run)


@pytest.mark.parametrize("field,value", [
    ("input_tokens", -1), ("cached_tokens", True), ("cache_write_tokens", 1.5),
    ("output_tokens", None), ("reasoning_tokens", 21), ("cached_tokens", 11),
    ("cache_write_tokens", 101),
])
def test_invalid_usage_refuses_without_guessing(profile, field, value):
    run, budget, _ = profile
    budget["rows"][0]["usage"][field] = value
    write_json(run / "budget.json", budget)
    with pytest.raises(estimate.EstimateError):
        estimate.reference_profile(run)


@pytest.mark.parametrize("extra", [False, True])
def test_usage_schema_requires_exact_counters(profile, extra):
    run, budget, _ = profile
    if extra:
        budget["rows"][0]["usage"]["new_charge_tokens"] = 20
    else:
        budget["rows"][0]["usage"].pop("cached_tokens")
    write_json(run / "budget.json", budget)
    with pytest.raises(estimate.EstimateError, match="exactly the five"):
        estimate.reference_profile(run)


@pytest.mark.parametrize("value", [3.0, True])
def test_settled_outcome_count_requires_integer(profile, value):
    run, budget, _ = profile
    budget["totals"]["outcomes"]["settled"] = value
    write_json(run / "budget.json", budget)
    with pytest.raises(estimate.EstimateError, match="outcomes.settled"):
        estimate.reference_profile(run)


@pytest.mark.parametrize("mutation", [
    "schema", "model", "empty", "missing_row", "duplicate_id", "duplicate_response",
    "total_requests", "total_spend", "held", "unknown", "outcomes", "blocked", "violation",
])
def test_inconsistent_reference_refuses(profile, mutation):
    run, budget, _ = profile
    if mutation == "schema":
        budget["schema"] = "other"
    elif mutation == "model":
        budget["tariff"]["model"] = "other"
    elif mutation == "empty":
        budget["rows"] = []
    elif mutation == "missing_row":
        budget["rows"].pop(1)
    elif mutation.startswith("duplicate"):
        key = "request_id" if mutation == "duplicate_id" else "response_id"
        budget["rows"][1][key] = budget["rows"][0][key]
    elif mutation == "total_requests":
        budget["totals"]["requests"] = 4
    elif mutation == "total_spend":
        budget["totals"]["settled_micro_usd"] = 59
    elif mutation in ("held", "unknown"):
        budget["totals"][f"{mutation}_micro_usd"] = 1
    elif mutation == "outcomes":
        budget["totals"]["outcomes"]["settled"] = 2
    else:
        budget[mutation] = {"kind": "invalid"}
    write_json(run / "budget.json", budget)
    with pytest.raises(estimate.EstimateError):
        estimate.reference_profile(run)


@pytest.mark.parametrize("field,value", [
    ("schema", "other"), ("run_id", "other"), ("number", 2),
    ("old_conversation_id", ""), ("new_conversation_id", "astra"),
    ("summary_request_sequence", 0), ("summary_request_sequence", 3),
    ("summary_request_sequence", None), ("summary_skipped", "unknown"),
    ("measurements", {}), ("measurements", None),
])
def test_invalid_renewal_refuses(profile, field, value):
    run, _, renewals = profile
    renewals["renewals"][0][field] = value
    write_json(run / "renewals.json", renewals)
    with pytest.raises(estimate.EstimateError, match="renewals"):
        estimate.reference_profile(run)


def test_empty_renewals_is_explicit_valid_evidence(profile):
    run, _, renewals = profile
    renewals["renewals"] = []
    write_json(run / "renewals.json", renewals)
    assert estimate.reference_profile(run)["renewal_count"] == 0


def test_cli_writes_json_and_exactly_six_summary_lines(profile, tmp_path, capsys):
    out = tmp_path / "report" / "estimate.json"
    assert estimate.main(["--astra-run", str(profile[0]), "--claude-opening", "100", "--out", str(out)]) == 0
    captured = capsys.readouterr()
    assert not captured.err
    assert len(captured.out.splitlines()) == 6
    assert captured.out.startswith("PLACEHOLDER ONLY - NOT THE ESTIMATE")
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["central_usd"] == "0.003504"
    assert "settled-only cutoff unchanged" in captured.out
    lines = captured.out.splitlines()
    assert "claude-opus-5-5, max effort" in lines[0]
    assert "3 requests, 1 renewals" in lines[0]
    assert "opening Claude 100 / Astra 100 (ratio 1.000000)" in lines[0]
    for line, name in zip(lines[1:], ("low", "central", "high", "stress_5x", "stress_10x")):
        assert line.startswith(f"{name} (k={report['scenarios'][name]['output_multiplier']}):")
        assert f"${report[f'{name}_usd']}" in line
        assert "1h writes" in line and "reads" in line and "output including thinking" in line


def test_cli_real_count_path(profile, livecheck, tmp_path, capsys):
    out = tmp_path / "real-estimate.json"
    assert estimate.main(["--astra-run", str(profile[0]), "--livecheck", str(livecheck[0]),
                          "--out", str(out)]) == 0
    assert capsys.readouterr().out.startswith("Owner-only planning estimate")
    assert json.loads(out.read_text())["placeholder"] is False


def test_cli_invalid_receipt_leaves_no_output(profile, tmp_path, capsys):
    run, budget, _ = profile
    budget["rows"][0].pop("usage")
    write_json(run / "budget.json", budget)
    out = tmp_path / "estimate.json"
    assert estimate.main(["--astra-run", str(run), "--claude-opening", "100", "--out", str(out)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Estimate refused:" in captured.err and "usage" in captured.err
    assert not out.exists()


def test_cli_cannot_overwrite_source_evidence(profile, capsys):
    run = profile[0]
    before = (run / "budget.json").read_bytes()
    assert estimate.main(["--astra-run", str(run), "--claude-opening", "100",
                          "--out", str(run / "budget.json")]) == 2
    assert "overwrite reference evidence" in capsys.readouterr().err
    assert (run / "budget.json").read_bytes() == before


def test_env_input_rejected_before_opening(profile, tmp_path, monkeypatch):
    env = tmp_path / ".env"
    original = Path.read_bytes

    def guarded_read(path):
        if path == env:
            pytest.fail("credential file was opened")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", guarded_read)
    with pytest.raises(estimate.EstimateError, match="credential"):
        estimate.build_estimate(profile[0], livecheck=env)
