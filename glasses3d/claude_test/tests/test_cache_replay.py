"""Small offline checks of calibration honesty and estimate arithmetic."""
from copy import deepcopy
from decimal import Decimal

from glasses3d.claude_test.cache_replay import (
    calibrate, compact_bytes, count_nested_blocks, modeled_pressure, replay,
)
from glasses3d.claude_test.cache_sim import request_values
from glasses3d.claude_test.conversation import estimate_content, pressure
from glasses3d.claude_test.pricing import price_usage


def profile_fixture(*, growth=1000):
    profile, messages, previous, endpoint = [], [], [], 0
    for index in range(45):
        messages.append({"role": "user", "content": [{"type": "text", "text": "x" * 300}]})
        body = {"model": "claude-opus-5-5", "cache_control": {"type": "ephemeral", "ttl": "1h"},
                "messages": deepcopy(messages)}
        values = request_values(body)
        added = values[len(previous):]
        new = estimate_content([item["value"] for item in added], {})
        increment = 40_000 if index == 0 else growth
        usage = {
            "input_tokens": 2, "output_tokens": 100,
            "cache_read_input_tokens": endpoint, "cache_creation_input_tokens": increment,
            "cache_creation": {"ephemeral_1h_input_tokens": increment, "ephemeral_5m_input_tokens": 0},
            "service_tier": "standard", "speed": "standard", "inference_geo": "global",
        }
        profile.append({
            "number": index + 1, "body": body, "values": values, "added": added,
            "new": new, "total_estimate": estimate_content([item["value"] for item in values], {}),
            "actual_usage": usage, "actual_cost_usd": price_usage(body["model"], usage),
            "now": index * 60, "payload_bytes": len(compact_bytes(body)),
            "meter": {"forecast": {"forecast_tokens": endpoint + increment,
                                   "new_content": {"total_tokens": 100}}},
        })
        previous, endpoint = values, endpoint + increment
    return profile


def test_calibration_does_not_copy_the_observed_hit_decision():
    profile = profile_fixture()
    assert calibrate(profile)["mismatched_requests"] == []
    # Reclassify an observed hit as a full rewrite without changing total
    # token count. The model must retain the original hit and report a mismatch.
    usage = profile[2]["actual_usage"]
    usage["cache_creation_input_tokens"] += usage["cache_read_input_tokens"]
    usage["cache_creation"]["ephemeral_1h_input_tokens"] = usage["cache_creation_input_tokens"]
    usage["cache_read_input_tokens"] = 0
    result = calibrate(profile)
    assert result["mismatched_requests"] == [3]
    assert result["requests"][2]["simulated_read"] == 41_000


def test_fixed_input_double_output_and_retained_sensitivity_are_distinct():
    profile = profile_fixture()
    normal = replay(profile, {}, output_multiplier=1)
    fixed = replay(profile, {}, output_multiplier=2)
    retained = replay(profile, {}, output_multiplier=2, retain_extra_output=True)
    assert normal["output_tokens"] == 4500
    assert fixed["output_tokens"] == 9000
    assert Decimal(fixed["cost_usd"]) - Decimal(normal["cost_usd"]) == Decimal("0.09")
    assert [row["input_tokens"] for row in normal["requests"]] == [row["input_tokens"] for row in fixed["requests"]]
    assert retained["requests"][-1]["input_tokens"] - normal["requests"][-1]["input_tokens"] == 4400
    assert Decimal(retained["cost_usd"]) > Decimal(fixed["cost_usd"])


def test_projected_renewal_counts_summary_and_one_fresh_write():
    template = {"opening_tokens": 1200, "image_occurrences": 6, "payload_bytes": 5000,
                "summary_output_tokens": 4000}
    result = replay(profile_fixture(growth=14_000), {}, count=70, renewal=template)
    assert len(result["renewals"]) == 1
    renewal = result["renewals"][0]
    first = result["requests"][renewal["before_productive_request"] - 1]
    following = result["requests"][renewal["before_productive_request"]]
    assert first["cache_read_tokens"] == 0
    assert first["cache_write_tokens"] == 1200
    assert following["cache_read_tokens"] == 1200
    assert result["total_requests_including_renewal_summaries"] == 70
    assert result["productive_requests"] == 69
    assert str(result["largest_request"]["by_input_tokens"]["request"]).startswith("summary_before_")
    def independently_price(read, write, total, output):
        return price_usage("claude-opus-5-5", {
            "input_tokens": total - read - write, "output_tokens": output,
            "cache_read_input_tokens": read, "cache_creation_input_tokens": write,
            "cache_creation": {"ephemeral_1h_input_tokens": write, "ephemeral_5m_input_tokens": 0},
        })
    costs = sum((independently_price(row["cache_read_tokens"], row["cache_write_tokens"],
                                    row["input_tokens"], row["output_tokens"])
                 for row in result["requests"]), Decimal(0))
    summary_cost = independently_price(renewal["summary_cache_read_tokens"], renewal["summary_cache_write_tokens"],
                                      renewal["summary_input_tokens"], renewal["summary_output_tokens"])
    assert Decimal(result["cost_usd"]) == costs + summary_cost


def test_live_trigger_edges_and_nested_count_are_preserved():
    assert modeled_pressure(images=480, payload_bytes=25_599_999, forecast=800_000) == []
    for images, size, forecast, reason in ((481, 10, 1, "images"),
                                           (1, 25_600_000, 1, "payload_bytes"),
                                           (1, 10, 800_001, "input_tokens")):
        assert modeled_pressure(images=images, payload_bytes=size, forecast=forecast)[0]["reason"] == reason
    body = {"messages": [{"role": "user", "content": [
        {"type": "image", "source": {"type": "file", "file_id": "x"}} for _ in range(481)
    ]}]}
    live = pressure(body, dims={}, measurement={"forecast_tokens": 800_001})
    modeled = modeled_pressure(images=481, payload_bytes=len(compact_bytes(body)), forecast=800_001)
    assert [row["reason"] for row in live] == [row["reason"] for row in modeled]
    result = {"type": "tool_result", "content": [{"type": "text", "text": "image labels"},
                                               {"type": "image", "source": {"type": "file", "file_id": "x"}}]}
    assert count_nested_blocks(result) == 3
