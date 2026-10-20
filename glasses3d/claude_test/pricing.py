"""Exact settled Messages usage, with unpriced usage failing closed.

Sources checked 2026-10-20:
https://platform.claude.com/docs/en/about-claude/pricing
https://platform.claude.com/docs/en/build-with-claude/prompt-caching
https://platform.claude.com/docs/en/api/beta/messages/create
https://platform.claude.com/docs/en/build-with-claude/compaction-threshold
https://platform.claude.com/docs/en/agents-and-tools/tool-use/advisor-tool

Input, cache reads and cache writes are separate token categories. Thinking
tokens are already included in output. Message iterations sum to the top-level
counts; compaction iterations are additional billed usage. No output estimate,
reservation, rounding to cents, or long-context multiplier belongs here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, localcontext
from typing import Any


class UnknownCharge(ValueError):
    """Usage cannot be priced exactly; the run must stop spending."""


@dataclass(frozen=True)
class Tariff:
    """USD per million tokens at the standard, global tariff."""

    input: Decimal
    output: Decimal
    cache_read: Decimal
    cache_write_5m: Decimal
    cache_write_1h: Decimal
    source_url: str
    checked: date

    def __post_init__(self) -> None:
        for name in ("input", "output", "cache_read", "cache_write_5m", "cache_write_1h"):
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
                raise ValueError(f"Tariff.{name} must be a finite non-negative Decimal")
        if not isinstance(self.checked, date):
            raise ValueError("Tariff.checked must be a date")
        if not isinstance(self.source_url, str) or not self.source_url:
            raise ValueError("Tariff.source_url must name its source")


_PRICING_URL = "https://platform.claude.com/docs/en/about-claude/pricing"
_CHECKED = date(2026, 10, 20)

# PROVISIONAL M13 D1 values; Claude reconciles these with task R's FACTS.md.
# The official page above also lists the 5m and 1h writes as 1.25x and 2x input.
TARIFFS: dict[str, Tariff] = {
    "claude-opus-5-5": Tariff(
        Decimal("4"), Decimal("20"), Decimal("0.20"), Decimal("5"), Decimal("8"),
        _PRICING_URL, _CHECKED,
    ),
    "claude-fable-5-1": Tariff(
        Decimal("10"), Decimal("50"), Decimal("0.25"), Decimal("12.50"), Decimal("20"),
        _PRICING_URL, _CHECKED,
    ),
}

# Explicit no-additional-charge allowlist. Every allowed value is still checked.
# Other server tools can carry fees; zero counters are harmless, nonzero refuse.
KNOWN_ZERO_COST_KEYS = frozenset({
    "service_tier",                    # only the standard tariff
    "inference_geo",                   # only default/global routing
    "speed",                           # only standard speed
    "server_tool_use.web_fetch_requests",
    "output_tokens_details.thinking_tokens",  # included in output_tokens
})
_TOKEN_KEYS = frozenset({
    "input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens",
})
_CACHE_KEYS = frozenset({"ephemeral_5m_input_tokens", "ephemeral_1h_input_tokens"})
_USAGE_KEYS = _TOKEN_KEYS | {
    "cache_creation", "service_tier", "inference_geo", "speed",
    "server_tool_use", "output_tokens_details", "iterations",
}
_ITERATION_KEYS = _TOKEN_KEYS | {"cache_creation", "model", "type"}
_MILLION = Decimal(1_000_000)


def _zero_or_null(value: Any) -> bool:
    """Only scalar JSON zero or null is harmless without a known field schema."""
    if value is None:
        return True
    if type(value) in (int, float):
        return value == 0
    return False


def _unknown_fields(value: dict, known: frozenset | set, path: str) -> None:
    for key, item in value.items():
        if not isinstance(key, str):
            raise UnknownCharge(f"{path} contains a non-string field name")
        if key not in known and not _zero_or_null(item):
            raise UnknownCharge(f"{path}.{key} is not in the priced usage schema")


def _count(value: Any, path: str) -> int:
    if type(value) is not int or value < 0:
        raise UnknownCharge(f"{path} must be a non-negative integer")
    return value


def _optional_count(usage: dict, key: str, path: str) -> int:
    value = usage.get(key)
    return 0 if value is None else _count(value, f"{path}.{key}")


def _object(value: Any, path: str) -> dict:
    if not isinstance(value, dict):
        raise UnknownCharge(f"{path} must be an object")
    return value


def _validate_counters(usage: dict, path: str) -> None:
    for key in _TOKEN_KEYS & usage.keys():
        # The cache aggregate/read fields are nullable in the API schema.
        if usage[key] is None and key.startswith("cache_"):
            continue
        _count(usage[key], f"{path}.{key}")
    details = usage.get("cache_creation")
    if details is not None:
        details = _object(details, f"{path}.cache_creation")
        _unknown_fields(details, _CACHE_KEYS, f"{path}.cache_creation")
        for key in _CACHE_KEYS & details.keys():
            _count(details[key], f"{path}.cache_creation.{key}")


def _validate_iteration(model: str, value: Any, path: str) -> None:
    iteration = _object(value, path)
    _unknown_fields(iteration, _ITERATION_KEYS, path)
    if iteration.get("type") not in ("message", "compaction"):
        raise UnknownCharge(f"{path}.type is not a same-model message or compaction")
    if iteration.get("model") not in (None, model):
        raise UnknownCharge(f"{path}.model differs from the requested model")
    _validate_counters(iteration, path)


def validate_usage_observation(model: str, usage: dict) -> None:
    """Validate fields present in a streaming usage observation.

    Streaming events can omit token categories and replace cumulative totals.
    This checks every observed modifier and unknown field before it could be
    overwritten by a later event. Only ``price_usage`` validates completeness
    and agreement of the final combined counters.
    """
    if not isinstance(model, str) or model not in TARIFFS:
        raise UnknownCharge("model is not in the frozen tariff")
    usage = _object(usage, "usage")
    _unknown_fields(usage, _USAGE_KEYS, "usage")
    _validate_counters(usage, "usage")
    if "service_tier" in usage and usage["service_tier"] != "standard":
        raise UnknownCharge("usage.service_tier is not standard")
    if usage.get("inference_geo") not in (None, "global"):
        raise UnknownCharge("usage.inference_geo is not default/global")
    if usage.get("speed") not in (None, "standard"):
        raise UnknownCharge("usage.speed is not standard")

    output_details = usage.get("output_tokens_details")
    if output_details is not None:
        output_details = _object(output_details, "usage.output_tokens_details")
        _unknown_fields(output_details, {"thinking_tokens"}, "usage.output_tokens_details")
        if "thinking_tokens" in output_details:
            thinking = _count(output_details["thinking_tokens"], "usage.output_tokens_details.thinking_tokens")
            if "output_tokens" in usage and thinking > usage["output_tokens"]:
                raise UnknownCharge("usage thinking tokens exceed output_tokens")
    server_tools = usage.get("server_tool_use")
    if server_tools is not None:
        server_tools = _object(server_tools, "usage.server_tool_use")
        _unknown_fields(server_tools, {"web_fetch_requests"}, "usage.server_tool_use")
        if "web_fetch_requests" in server_tools:
            _count(server_tools["web_fetch_requests"], "usage.server_tool_use.web_fetch_requests")
    iterations = usage.get("iterations")
    if iterations is not None:
        if not isinstance(iterations, list):
            raise UnknownCharge("usage.iterations must be an array")
        for index, iteration in enumerate(iterations):
            _validate_iteration(model, iteration, f"usage.iterations[{index}]")


def _categories(usage: dict, path: str) -> tuple[int, int, int, int, int]:
    """Validated counts in tariff order, requiring unambiguous write TTLs."""
    inp = _count(usage.get("input_tokens"), f"{path}.input_tokens")
    out = _count(usage.get("output_tokens"), f"{path}.output_tokens")
    read = _optional_count(usage, "cache_read_input_tokens", path)
    created = _optional_count(usage, "cache_creation_input_tokens", path)
    details = usage.get("cache_creation")
    if details is None:
        if created:
            raise UnknownCharge(f"{path}.cache_creation is missing the cache write TTL breakdown")
        return inp, out, read, 0, 0
    # Missing TTLs are ambiguous even when one reported TTL matches the aggregate.
    five = _count(details.get("ephemeral_5m_input_tokens"), f"{path}.cache_creation.ephemeral_5m_input_tokens")
    hour = _count(details.get("ephemeral_1h_input_tokens"), f"{path}.cache_creation.ephemeral_1h_input_tokens")
    if created != five + hour:
        raise UnknownCharge(f"{path} cache creation aggregate differs from its TTL breakdown")
    return inp, out, read, five, hour


def _sum_categories(rows: list[tuple[int, int, int, int, int]]) -> tuple[int, int, int, int, int]:
    return tuple(sum(row[index] for row in rows) for index in range(5))


def price_usage(model: str, usage: dict) -> Decimal:
    """Price complete Messages usage exactly, or raise ``UnknownCharge``.

    Positive aggregate writes without TTL details cannot be priced: this runner
    requests 1h caching, while server tools can also introduce 5m writes. Nullable
    cache fields mean zero only when no positive breakdown contradicts them.
    Same-model message iterations reconcile to the top-level categories;
    compaction usage is added once. Advisor/fallback iterations are refused.
    """
    validate_usage_observation(model, usage)
    categories = _categories(usage, "usage")
    iterations = usage.get("iterations")
    if iterations:
        message_rows, all_rows = [], []
        for index, iteration in enumerate(iterations):
            row = _categories(iteration, f"usage.iterations[{index}]")
            all_rows.append(row)
            if iteration["type"] == "message":
                message_rows.append(row)
        if _sum_categories(message_rows) != categories:
            raise UnknownCharge("usage.iterations message totals differ from top-level usage")
        categories = _sum_categories(all_rows)
    tariff = TARIFFS[model]
    rates = (tariff.input, tariff.output, tariff.cache_read, tariff.cache_write_5m, tariff.cache_write_1h)
    # Token usage is normally small. Retain exactness even for unusually large
    # integer counts and when the caller has lowered the thread's Decimal precision.
    with localcontext() as context:
        context.prec = max(28, max(len(str(count)) for count in categories) + 20)
        return sum((Decimal(count) * rate for count, rate in zip(categories, rates)), Decimal(0)) / _MILLION
