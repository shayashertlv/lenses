"""Exact breakpoints, expiry, lookback and tariff arithmetic without a provider."""
from decimal import Decimal

import pytest

from glasses3d.claude_test.cache_sim import CacheBlock, CacheSimulator, request_values
from glasses3d.claude_test.pricing import price_usage


def block(name, tokens=100):
    return CacheBlock({"type": "text", "text": name}, tokens)


def test_prior_breakpoint_only_no_arbitrary_common_prefix():
    sim = CacheSimulator()
    original = [block("a", 700), block("b", 300)]
    first = sim.request(original, now=0, output_tokens=50)
    assert first.read_tokens == 0 and first.write_tokens == 1000
    # 700 tokens match, but no earlier request wrote a breakpoint there.
    changed = sim.request([original[0], block("different", 300)], now=60, output_tokens=50)
    assert changed.read_tokens == 0 and changed.write_tokens == 1000
    extended = sim.request([*original, block("c", 50)], now=120, output_tokens=50)
    assert extended.hit_blocks == 2 and extended.read_tokens == 1000 and extended.write_tokens == 50
    assert extended.cost_usd == Decimal(".0016")
    assert extended.cost_usd == price_usage(sim.model, extended.usage)


@pytest.mark.parametrize("added,expected_read", [(0, 700), (19, 700), (20, 700), (21, 0)])
def test_lookback_boundary(added, expected_read):
    sim = CacheSimulator()
    original = [block("opening", 700)]
    sim.request(original, now=0, output_tokens=0)
    result = sim.request(original + [block(str(i)) for i in range(added)], now=60, output_tokens=0)
    assert result.read_tokens == expected_read
    assert result.write_tokens == 700 + added * 100 - expected_read


def test_ttl_is_from_request_start_and_refreshes_matched_entry():
    sim = CacheSimulator()
    opening = [block("a", 700)]
    sim.request(opening, now=0, output_tokens=0)
    assert sim.request(opening, now=3599, output_tokens=0).read_tokens == 700
    assert sim.request(opening, now=7198, output_tokens=0).read_tokens == 700
    assert sim.request(opening, now=10798, output_tokens=0).write_tokens == 700
    assert sim.request(opening, now=10799, output_tokens=0).read_tokens == 700


def test_only_matched_entry_refreshes_when_later_prefix_is_used():
    sim = CacheSimulator()
    a = [block("a", 700)]
    b = [*a, block("b")]
    sim.request(a, now=0, output_tokens=0)
    sim.request(b, now=10, output_tokens=0)  # also reads/refreshed a at t=10
    sim.request(b, now=3500, output_tokens=0)
    # A new branch can only use a, whose last match was at t=10.
    branched = sim.request([*a, block("branch")], now=3610, output_tokens=0)
    assert branched.read_tokens == 0 and branched.write_tokens == 800


def test_minimum_is_prefix_threshold_without_rounding_invented():
    sim = CacheSimulator()
    short = [block("short", 511)]
    first = sim.request(short, now=0, output_tokens=3, uncached_tokens=2)
    assert first.usage["input_tokens"] == 513 and first.write_tokens == first.read_tokens == 0
    assert sim.request(short, now=1, output_tokens=0).read_tokens == 0
    minimum = [*short, block("one", 1)]
    assert sim.request(minimum, now=2, output_tokens=0).write_tokens == 512
    tiny_delta = sim.request([*minimum, block("two", 2)], now=3, output_tokens=0, uncached_tokens=7)
    assert tiny_delta.read_tokens == 512 and tiny_delta.write_tokens == 2
    assert tiny_delta.usage["input_tokens"] == 7 and tiny_delta.total_input_tokens == 521


def test_exact_prefix_includes_roles_signatures_settings_and_nested_content():
    body = {"tools": [{"name": "inspect", "input_schema": {}}], "system": "instructions",
            "messages": [{"role": "assistant", "content": [
                {"type": "thinking", "thinking": "x", "signature": "signed-a"}]},
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "id",
                                             "content": [{"type": "text", "text": "nested"}]}]}]}
    values = request_values(body)
    assert len(values) == 4  # nested text stays inside its tool-result parent
    sim = CacheSimulator()
    blocks = [CacheBlock(value, 250) for value in values]
    sim.request(blocks, now=0, output_tokens=0, namespace="policy-a")
    assert sim.request(blocks, now=1, output_tokens=0, namespace="policy-b").read_tokens == 0
    for changed in [
        {**values[-1], "role": "assistant"},
        {**values[-1], "value": {**values[-1]["value"], "content": [{"type": "text", "text": "other"}]}},
    ]:
        assert sim.request([*blocks[:-1], CacheBlock(changed, 250)], now=2,
                           output_tokens=0, namespace="policy-a").read_tokens == 0
    values[2]["value"]["signature"] = "signed-b"
    assert sim.request([CacheBlock(value, 250) for value in values], now=3,
                       output_tokens=0, namespace="policy-a").read_tokens == 0


@pytest.mark.parametrize("model", ["claude-opus-5-5", "claude-fable-5-1"])
def test_cost_uses_existing_tariffs_exactly(model):
    sim = CacheSimulator(model)
    a = [block("a", 12345)]
    one = sim.request(a, now=0, output_tokens=123, uncached_tokens=7)
    two = sim.request([*a, block("b", 13)], now=1, output_tokens=321, uncached_tokens=3)
    assert all(row.cost_usd == price_usage(model, row.usage) for row in [one, two])


def test_invalid_measurements_do_not_silently_change_model():
    with pytest.raises(ValueError):
        CacheBlock("negative", -1)
    with pytest.raises(ValueError):
        CacheBlock("bool", True)
    sim = CacheSimulator()
    sim.request([block("a", 600)], now=10, output_tokens=0)
    with pytest.raises(ValueError, match="nondecreasing"):
        sim.request([block("a", 600)], now=9, output_tokens=0)
    with pytest.raises(ValueError, match="token sizes changed"):
        sim.request([block("a", 601)], now=11, output_tokens=0)
