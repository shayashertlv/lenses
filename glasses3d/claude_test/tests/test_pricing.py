"""Offline price and fail-closed schema proofs for the M13 tariff."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
from datetime import date
from decimal import Decimal, localcontext

import pytest

from glasses3d.claude_test.pricing import (
    KNOWN_ZERO_COST_KEYS, TARIFFS, Tariff, UnknownCharge, price_usage, validate_usage_observation,
)


OPUS = "claude-opus-5-5"
FABLE = "claude-fable-5-1"


def usage(**changes):
    return {"input_tokens": 100, "output_tokens": 20, **changes}


def cached_usage(five=0, hour=0, read=0, **changes):
    return usage(
        cache_read_input_tokens=read,
        cache_creation_input_tokens=five + hour,
        cache_creation={"ephemeral_5m_input_tokens": five, "ephemeral_1h_input_tokens": hour},
        **changes,
    )


def iteration(tokens, kind="message", model=None):
    row = {"type": kind, **tokens}
    if model is not None:
        row["model"] = model
    return row


@pytest.mark.parametrize(
    "model,rates",
    [(OPUS, ("4", "20", ".20", "5", "8")), (FABLE, ("10", "50", ".25", "12.50", "20"))],
)
def test_frozen_tariffs_are_decimal_rates_and_dated(model, rates):
    tariff = TARIFFS[model]
    assert [getattr(tariff, key) for key in ("input", "output", "cache_read", "cache_write_5m", "cache_write_1h")] \
        == [Decimal(rate) for rate in rates]
    assert type(tariff.checked) is date
    assert tariff.source_url == "https://platform.claude.com/docs/en/about-claude/pricing"
    with pytest.raises(FrozenInstanceError):
        tariff.input = Decimal("3")


@pytest.mark.parametrize("bad_rate", [4, 4.0, "4", True, Decimal("NaN"), Decimal("Infinity"), Decimal("-1")])
def test_tariff_rejects_non_decimal_or_invalid_money(bad_rate):
    with pytest.raises(ValueError):
        Tariff(bad_rate, Decimal("20"), Decimal(".2"), Decimal("5"), Decimal("8"), "source", date(2026, 10, 20))


@pytest.mark.parametrize(
    "tokens,opus,fable",
    [
        (usage(), "0.0008", "0.002"),
        (usage(input_tokens=0, output_tokens=0), "0", "0"),
        (usage(cache_creation_input_tokens=None, cache_read_input_tokens=None, cache_creation=None), ".0008", ".002"),
        (cached_usage(read=1000), ".001", ".00225"),
        (cached_usage(five=1000), ".0058", ".0145"),
        (cached_usage(hour=1000), ".0088", ".022"),
        (cached_usage(five=600, hour=400, read=1000), ".0072", ".01775"),
        (usage(input_tokens=900_000, output_tokens=128_000), "6.16", "15.4"),
    ],
)
def test_each_token_category_is_priced_once(tokens, opus, fable):
    original = deepcopy(tokens)
    assert price_usage(OPUS, tokens) == Decimal(opus)
    assert price_usage(FABLE, tokens) == Decimal(fable)
    assert tokens == original


def test_one_token_cached_read_retains_sub_micro_dollars():
    assert price_usage(OPUS, usage(input_tokens=0, output_tokens=0, cache_read_input_tokens=1)) == Decimal(".0000002")
    assert price_usage(FABLE, usage(input_tokens=0, output_tokens=0, cache_read_input_tokens=1)) == Decimal(".00000025")


def test_money_is_exact_even_under_low_decimal_precision():
    with localcontext() as context:
        context.prec = 2
        actual = price_usage(FABLE, cached_usage(five=17, hour=19, read=23, input_tokens=29, output_tokens=31))
    assert actual == Decimal(".00243825")


def test_known_no_additional_charge_metadata_is_validated_and_not_double_counted():
    assert "output_tokens_details.thinking_tokens" in KNOWN_ZERO_COST_KEYS
    assert "server_tool_use.web_fetch_requests" in KNOWN_ZERO_COST_KEYS
    assert price_usage(OPUS, usage(
        service_tier="standard", inference_geo="global", speed="standard",
        output_tokens_details={"thinking_tokens": 18}, server_tool_use={"web_fetch_requests": 8},
    )) == Decimal(".0008")


@pytest.mark.parametrize("field", ["inference_geo", "speed", "server_tool_use", "output_tokens_details", "iterations"])
def test_nullable_metadata(field):
    assert price_usage(OPUS, usage(**{field: None})) == Decimal(".0008")


@pytest.mark.parametrize("unknown", [None, 0, 0.0])
def test_unknown_scalar_zero_or_null_is_harmless(unknown):
    assert price_usage(OPUS, usage(new_field=unknown)) == Decimal(".0008")


@pytest.mark.parametrize("unknown", [1, -1, "", "0", False, True, float("nan"), float("inf"),
                                    {}, [], {"new": 0}, {"nested": [None, 0, {"zero": 0}]},
                                    {"nested": [0, {"price": 1}]}, [0, ""], {1: 0}])
def test_unknown_nonzero_or_nonnull_values_including_containers_refuse(unknown):
    with pytest.raises(UnknownCharge):
        price_usage(OPUS, usage(new_field=unknown))


@pytest.mark.parametrize("tokens", [None, [], "usage", {}, {"input_tokens": 1}, {"output_tokens": 1}])
def test_missing_or_incomplete_usage_refuses(tokens):
    with pytest.raises(UnknownCharge):
        price_usage(OPUS, tokens)


@pytest.mark.parametrize("model", ["claude-opus-latest", "", None, [], "claude-opus-4-8"])
def test_unknown_models_refuse(model):
    with pytest.raises(UnknownCharge):
        price_usage(model, usage())


@pytest.mark.parametrize("field", ["input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"])
@pytest.mark.parametrize("value", [True, False, -1, "1", 1.0, [], {}, Decimal("1")])
def test_token_counts_must_be_nonnegative_json_integers(field, value):
    with pytest.raises(UnknownCharge):
        price_usage(OPUS, usage(**{field: value}))


@pytest.mark.parametrize(
    "changes",
    [
        {"service_tier": None}, {"service_tier": "priority"}, {"service_tier": "batch"},
        {"service_tier": "auto"}, {"service_tier": 0},
        {"inference_geo": "us"}, {"inference_geo": "eu"}, {"inference_geo": "default"}, {"inference_geo": []},
        {"speed": "fast"}, {"speed": 0}, {"speed": ""},
        {"server_tool_use": {"web_search_requests": 1}},
        {"server_tool_use": {"code_execution_requests": 1}},
        {"server_tool_use": {"new_tool": {"requests": 1}}},
        {"server_tool_use": 0},
        {"server_tool_use": {"web_fetch_requests": None}},
        {"server_tool_use": {"web_fetch_requests": True}},
        {"server_tool_use": {"web_fetch_requests": -1}},
        {"output_tokens_details": {"thinking_tokens": 21}},
        {"output_tokens_details": {"thinking_tokens": None}},
        {"output_tokens_details": {"thinking_tokens": True}},
        {"output_tokens_details": {"new_tokens": 1}},
        {"output_tokens_details": []},
        {"fallback_credit": {"status": {"type": "redeemed"}}},
    ],
)
def test_unpriced_or_malformed_metadata_stops(changes):
    with pytest.raises(UnknownCharge):
        price_usage(OPUS, usage(**changes))


def test_zero_server_tool_fees_are_safe_with_no_fee_fetches():
    assert price_usage(OPUS, usage(server_tool_use={
        "web_fetch_requests": 2, "web_search_requests": 0, "code_execution_requests": 0,
        "future_fee": 0,
    })) == Decimal(".0008")


@pytest.mark.parametrize(
    "changes",
    [
        {"cache_creation_input_tokens": 1},
        {"cache_creation_input_tokens": 1, "cache_creation": None},
        {"cache_creation_input_tokens": 0, "cache_creation": {"ephemeral_5m_input_tokens": 1, "ephemeral_1h_input_tokens": 0}},
        {"cache_creation_input_tokens": 3, "cache_creation": {"ephemeral_5m_input_tokens": 1, "ephemeral_1h_input_tokens": 1}},
        {"cache_creation_input_tokens": None, "cache_creation": {"ephemeral_5m_input_tokens": 1, "ephemeral_1h_input_tokens": 0}},
        {"cache_creation": {"ephemeral_5m_input_tokens": 0}},
        {"cache_creation": {}},
        {"cache_creation": []},
        {"cache_creation": 0},
        {"cache_creation": {"ephemeral_5m_input_tokens": None, "ephemeral_1h_input_tokens": 0}},
        {"cache_creation": {"ephemeral_5m_input_tokens": True, "ephemeral_1h_input_tokens": 0}},
        {"cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 0, "new_ttl_tokens": 1}},
    ],
)
def test_cache_ttl_ambiguity_malformed_fields_and_conflicting_totals_refuse(changes):
    with pytest.raises(UnknownCharge):
        price_usage(OPUS, usage(**changes))


def test_unknown_zero_cache_details_do_not_change_cost():
    tokens = cached_usage(five=1, hour=1)
    tokens["cache_creation"]["future"] = 0
    assert price_usage(OPUS, tokens) == Decimal(".000813")


def test_same_model_message_iterations_are_already_in_top_level_usage():
    first = cached_usage(five=3, hour=4, read=5, input_tokens=6, output_tokens=7)
    second = cached_usage(five=11, hour=13, read=17, input_tokens=19, output_tokens=23)
    total = cached_usage(five=14, hour=17, read=22, input_tokens=25, output_tokens=30)
    total["iterations"] = [iteration(first), iteration(second, model=OPUS)]
    assert price_usage(OPUS, total) == Decimal(".0009104")


def test_same_model_compaction_is_added_once_to_message_usage():
    main = cached_usage(five=14, hour=17, read=22, input_tokens=25, output_tokens=30)
    compact = cached_usage(five=3, hour=4, read=5, input_tokens=6, output_tokens=7)
    total = {**main, "iterations": [iteration(compact, kind="compaction"), iteration(main, model=OPUS)]}
    assert price_usage(OPUS, total) == Decimal(".0011224")


def test_compaction_only_usage_can_have_zero_top_level_counts():
    compact = cached_usage(five=3, hour=4, read=5, input_tokens=6, output_tokens=7)
    total = usage(input_tokens=0, output_tokens=0, iterations=[iteration(compact, kind="compaction", model=OPUS)])
    assert price_usage(OPUS, total) == Decimal(".000212")


@pytest.mark.parametrize("field", ["input_tokens", "output_tokens", "cache_read_input_tokens",
                                   "cache_creation_input_tokens", "ephemeral_5m_input_tokens", "ephemeral_1h_input_tokens"])
def test_iteration_categories_reconcile_exactly(field):
    main = cached_usage(five=14, hour=17, read=22, input_tokens=25, output_tokens=30)
    altered = deepcopy(main)
    if field.startswith("ephemeral"):
        altered["cache_creation"][field] += 1
        altered["cache_creation_input_tokens"] += 1
    else:
        altered[field] += 1
    total = {**main, "iterations": [iteration(altered)]}
    with pytest.raises(UnknownCharge):
        price_usage(OPUS, total)


@pytest.mark.parametrize(
    "entries",
    [
        "not an array", 0, {},
        [None], [{}], [usage()], [iteration(usage(), kind="advisor_message", model=OPUS)],
        [iteration(usage(), kind="fallback_message", model=OPUS)],
        [iteration(usage(), model=FABLE)], [iteration(usage(), model="")],
        [iteration(usage(), kind="compaction", model=FABLE)],
        [iteration({"input_tokens": 100})],
        [iteration(usage(new_charge={"tokens": 1}))],
        [iteration(usage(service_tier="priority"))],
    ],
)
def test_unknown_iterations_are_never_ignored(entries):
    with pytest.raises(UnknownCharge):
        price_usage(OPUS, usage(iterations=entries))


def test_empty_iteration_list_does_not_erase_usage():
    assert price_usage(OPUS, usage(iterations=[])) == Decimal(".0008")


def test_stream_observations_allow_partial_counters_but_final_requires_complete_usage():
    validate_usage_observation(OPUS, {"cache_creation_input_tokens": 100})
    validate_usage_observation(OPUS, {"cache_creation": {"ephemeral_1h_input_tokens": 100}})
    validate_usage_observation(OPUS, {"output_tokens": 20})
    validate_usage_observation(OPUS, {})
    with pytest.raises(UnknownCharge):
        price_usage(OPUS, {"output_tokens": 20})


@pytest.mark.parametrize(
    "observation",
    [
        {"future_charge": 1},
        {"future_charge": {}},
        {"future_charge": []},
        {"future_charge": {"new": 0}},
        {"future_charge": {"nested": [0, 1]}},
        {"speed": "fast"},
        {"service_tier": "priority"},
        {"inference_geo": "us"},
        {"server_tool_use": {"web_search_requests": 1}},
        {"iterations": [iteration(usage(), kind="fallback_message", model=OPUS)]},
        {"cache_creation": {"new_duration": 1}},
    ],
)
def test_stream_observation_catches_charge_before_later_overwrite(observation):
    with pytest.raises(UnknownCharge):
        validate_usage_observation(OPUS, observation)
