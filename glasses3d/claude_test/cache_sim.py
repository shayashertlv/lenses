"""Deterministic, caller-tokenized model of one automatic 1-hour cache.

The model implements FACTS.md section 9: an exact prefix can hit only a
breakpoint written by a preceding request, within the 20-block lookback and
before its last-use timestamp is one hour old. A request writes only its final
breakpoint. There are no implicit entries at intermediate blocks.

``CacheBlock`` is the unit of lookback; its token size is always supplied by the
caller. ``request_values`` projects tools, system and *top-level* message
content blocks, retaining nested tool-result content inside its parent. That
projection is explicit so offline evidence can also test a different block
interpretation without changing the cache algorithm.

512 is the minimum *prefix length*, not an invented token rounding interval.
No provider chunk rounding, tokenization, latency, server-side clearing,
concurrent requests or hidden thinking is inferred here. If measured accounting
has an uncached suffix, callers must supply it as ``uncached_tokens`` and report
that assumption. ``now`` is request-start time, so generation/tool time consumes
TTL. Only the entry actually matched is refreshed; new writes start at ``now``.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import json
import math
from typing import Any, Sequence

from .pricing import TARIFFS

MINIMUM_PREFIX_TOKENS = 512
LOOKBACK_BLOCKS = 20
TTL_SECONDS = 3600


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _counter(value: int, name: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


@dataclass(frozen=True)
class CacheBlock:
    """An exact JSON block value and its caller-supplied input token size."""

    value: Any
    tokens: int

    def __post_init__(self):
        _counter(self.tokens, "block tokens")
        _canonical(self.value)


@dataclass(frozen=True)
class CacheResult:
    usage: dict
    cost_usd: Decimal
    hit_blocks: int
    block_count: int
    total_input_tokens: int

    @property
    def read_tokens(self) -> int:
        return self.usage["cache_read_input_tokens"]

    @property
    def write_tokens(self) -> int:
        return self.usage["cache_creation_input_tokens"]


@dataclass
class _Entry:
    namespace: bytes
    prefix: tuple[bytes, ...]
    sizes: tuple[int, ...]
    last_use: float


def request_values(body: dict) -> list[dict]:
    """Return exact top-level prompt blocks in tools/system/messages order.

    Message and block indices preserve message boundaries as part of equality.
    Values are detached by canonical serialization. No marker is inserted or
    moved. Policy changes outside these values should be passed as ``namespace``
    to ``request`` (the fake wire does this automatically).
    """
    values = []
    for index, tool in enumerate(body.get("tools", [])):
        values.append({"section": "tools", "index": index, "value": tool})
    system = body.get("system", [])
    if isinstance(system, str):
        system = [{"type": "text", "text": system}]
    for index, block in enumerate(system):
        values.append({"section": "system", "index": index, "value": block})
    for message_index, message in enumerate(body["messages"]):
        content = message["content"]
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        for block_index, block in enumerate(content):
            values.append({"section": "messages", "message_index": message_index,
                           "block_index": block_index, "role": message["role"], "value": block})
    return json.loads(_canonical(values))


def request_namespace(body: dict) -> dict:
    """Conservatively bind every non-prompt request setting to cache identity."""
    return {key: value for key, value in body.items()
            if key not in {"tools", "system", "messages"}}


class CacheSimulator:
    """Stateful automatic-breakpoint model; it never calls a provider.

    ``lookback_blocks`` is the maximum distance from the current breakpoint to
    an earlier written breakpoint: a distance of 20 is included, 21 is excluded.
    This inclusive-edge convention is explicit, not verified by FACTS' retained
    wording; provider behavior at the 19/20-block edge remains an offline limit.
    C5 adds at most three top-level blocks, so that ambiguity does not affect its
    calibration or replay.
    All successful calls must use nondecreasing request-start seconds. A renewed
    conversation can use a new instance or a fresh namespace; a different full
    opening also misses naturally because no prior endpoint matches it.
    """

    def __init__(self, model: str = "claude-opus-5-5", *,
                 lookback_blocks: int = LOOKBACK_BLOCKS,
                 ttl_seconds: float = TTL_SECONDS,
                 minimum_prefix_tokens: int = MINIMUM_PREFIX_TOKENS):
        self.tariff = TARIFFS[model]
        self.model = model
        self.lookback_blocks = _counter(lookback_blocks, "lookback_blocks")
        self.minimum_prefix_tokens = _counter(minimum_prefix_tokens, "minimum_prefix_tokens")
        if (isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, (int, float))
                or not math.isfinite(ttl_seconds) or ttl_seconds <= 0):
            raise ValueError("ttl_seconds must be finite and positive")
        self.ttl_seconds = ttl_seconds
        self._entries: list[_Entry] = []
        self._last_request_time: float | None = None
        self.results: list[CacheResult] = []

    def request(self, blocks: Sequence[CacheBlock], *, output_tokens: int,
                now: float, uncached_tokens: int = 0, namespace: Any = None) -> CacheResult:
        output_tokens = _counter(output_tokens, "output_tokens")
        uncached_tokens = _counter(uncached_tokens, "uncached_tokens")
        if (isinstance(now, bool) or not isinstance(now, (int, float))
                or not math.isfinite(now)
                or self._last_request_time is not None and now < self._last_request_time):
            raise ValueError("now must be finite, nondecreasing request-start seconds")
        if not all(isinstance(block, CacheBlock) for block in blocks):
            raise TypeError("blocks must contain CacheBlock values")
        prefix = tuple(_canonical(block.value) for block in blocks)
        sizes = tuple(block.tokens for block in blocks)
        namespace_key = _canonical(namespace)
        total = sum(sizes)
        hit = None
        for entry in self._entries:
            count = len(entry.prefix)
            if (entry.namespace == namespace_key and count <= len(prefix)
                    and len(prefix) - count <= self.lookback_blocks
                    and now - entry.last_use < self.ttl_seconds
                    and prefix[:count] == entry.prefix):
                if sizes[:count] != entry.sizes:
                    raise ValueError("token sizes changed for an identical cached prefix")
                if hit is None or count > len(hit.prefix):
                    hit = entry
        read = sum(hit.sizes) if hit is not None else 0
        write = total - read if total >= self.minimum_prefix_tokens else 0
        ordinary = uncached_tokens + (total if total < self.minimum_prefix_tokens else 0)
        usage = {
            "input_tokens": ordinary, "output_tokens": output_tokens,
            "cache_read_input_tokens": read, "cache_creation_input_tokens": write,
            "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": write},
            "service_tier": "standard", "speed": "standard", "inference_geo": "global",
        }
        tariff = self.tariff
        cost = (Decimal(ordinary) * tariff.input + Decimal(output_tokens) * tariff.output
                + Decimal(read) * tariff.cache_read + Decimal(write) * tariff.cache_write_1h) / 1_000_000
        result = CacheResult(usage, cost, len(hit.prefix) if hit is not None else 0,
                             len(prefix), total + uncached_tokens)
        if hit is not None:
            hit.last_use = now
        if total >= self.minimum_prefix_tokens:
            existing = next((entry for entry in self._entries
                             if entry.namespace == namespace_key and entry.prefix == prefix), None)
            if existing is None:
                self._entries.append(_Entry(namespace_key, prefix, sizes, now))
            else:
                if existing.sizes != sizes:
                    raise ValueError("token sizes changed for an identical cached prefix")
                existing.last_use = now
        self._last_request_time = now
        self.results.append(result)
        return result
