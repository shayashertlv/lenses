"""The frozen Astra tariff and integer money.

Money is counted in micro-USD (1 USD = 1_000_000) and every division rounds UP, so a reservation or a settlement is
never smaller than the provider's charge under the frozen tariff. The tariff is the standard tier read from
developers.openai.com/api/docs/models/gpt-6-astra on 2026-09-27 (input 10, cached input 1, cache write 12.5, output
50 USD per million tokens; above 272,000 input tokens the whole request is billed 2x input/cache and 1.5x output;
regional data-residency endpoints add 10 %). Anything outside this table (Fast, Batch, Flex, priority, an unknown
region) blocks paid mode rather than being guessed. A computed amount is an execution bound, not an invoice.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation, ROUND_CEILING

MICRO = 1_000_000
PRICE_VERSION = "gpt-6-astra-standard-2026-09-27"
MODEL = "gpt-6-astra"
LONG_CONTEXT_INPUT_TOKENS = 272_000
MODEL_MAX_OUTPUT_TOKENS = 128_000           # the model page's ceiling; the compact endpoint's own bound is NOT verified
MODEL_MAX_INPUT_TOKENS = 922_000              # of the model's 1,050,000-token context window

# micro-USD per one million tokens (USD per million tokens x 1e6), standard tier
STANDARD_RATES_PER_MILLION = {"input": 10 * MICRO, "cached": 1 * MICRO, "cache_write": 12_500_000, "output": 50 * MICRO}
LONG_CONTEXT_MULTIPLIER = {"input": 2.0, "cached": 2.0, "cache_write": 2.0, "output": 1.5}
KNOWN_SERVICE_TIERS = ("default",)          # flex, priority, auto: not in the frozen table, refused
KNOWN_REGIONS = {"global": 0, "us": 0, "eu": 10}   # percent uplift for data residency (eu: +10 %)


class PricingError(ValueError):
    pass


def ceil_div(a: int, b: int) -> int:
    if b <= 0:
        raise PricingError("division by a non-positive number")
    return -(-a // b)


def usd_to_micro(usd) -> int:
    """Exact decimal parsing of a dollar amount into micro-USD (rounded up below the micro-dollar)."""
    if isinstance(usd, bool):
        raise PricingError("a dollar amount cannot be a boolean")
    try:
        d = Decimal(str(usd))
    except (InvalidOperation, ValueError):
        raise PricingError(f"not a dollar amount: {usd!r}") from None
    if not d.is_finite() or d < 0:
        raise PricingError(f"a dollar amount must be finite and non-negative: {usd!r}")
    return int((d * MICRO).to_integral_value(rounding=ROUND_CEILING))


def usd_text(micro: int) -> str:
    return f"{micro / MICRO:.6f}"


@dataclass(frozen=True)
class Tariff:
    price_version: str = PRICE_VERSION
    model: str = MODEL
    service_tier: str = "default"
    region: str = "global"
    uplift_percent: int = 0
    input_per_million: int = STANDARD_RATES_PER_MILLION["input"]
    cached_per_million: int = STANDARD_RATES_PER_MILLION["cached"]
    cache_write_per_million: int = STANDARD_RATES_PER_MILLION["cache_write"]
    output_per_million: int = STANDARD_RATES_PER_MILLION["output"]
    long_context_input_tokens: int = LONG_CONTEXT_INPUT_TOKENS

    @staticmethod
    def frozen(*, service_tier: str = "default", region: str = "global", fast_mode: bool = False) -> "Tariff":
        """The only tariff paid mode may use; anything the table does not cover raises."""
        if fast_mode:
            raise PricingError("Fast mode (2x) is disabled for this runner")
        if service_tier not in KNOWN_SERVICE_TIERS:
            raise PricingError(f"service tier {service_tier!r} is not in the frozen tariff; only {KNOWN_SERVICE_TIERS}")
        if region not in KNOWN_REGIONS:
            raise PricingError(f"billing region {region!r} unknown; known: {sorted(KNOWN_REGIONS)}")
        return Tariff(service_tier=service_tier, region=region, uplift_percent=KNOWN_REGIONS[region])

    @staticmethod
    def from_dict(d: dict) -> "Tariff":
        t = Tariff(**{k: d[k] for k in Tariff.__dataclass_fields__ if k in d})
        if t.price_version != PRICE_VERSION:
            raise PricingError(f"the job froze tariff {t.price_version!r}; this code knows {PRICE_VERSION!r}")
        return t

    def to_dict(self) -> dict:
        return asdict(self)

    def _uplift(self, micro: int) -> int:
        return ceil_div(micro * (100 + self.uplift_percent), 100)

    def multipliers(self, input_tokens: int) -> dict:
        long = int(input_tokens) > self.long_context_input_tokens
        return {k: (v if long else 1.0) for k, v in LONG_CONTEXT_MULTIPLIER.items()} | {"long_context": long}

    @staticmethod
    def _cost(tokens: int, per_million: int, multiplier: float) -> int:
        if tokens < 0:
            raise PricingError("negative token count")
        # the multipliers are 1, 1.5 or 2: exact in halves, so scale by two and stay in integers
        return ceil_div(int(tokens) * per_million * int(round(multiplier * 2)), 2 * MICRO)

    def reserve_micro(self, input_tokens: int, max_output_tokens: int) -> int:
        """Worst case before a request: every input token at the cache-write rate, the whole output limit spent."""
        for v in (input_tokens, max_output_tokens):
            if type(v) is not int:
                raise PricingError("token counts must be integers")
        if input_tokens < 0 or max_output_tokens <= 0:
            raise PricingError("a reservation needs a non-negative input count and a positive output limit")
        if input_tokens > MODEL_MAX_INPUT_TOKENS:
            raise PricingError(f"{input_tokens} input tokens exceed the model's {MODEL_MAX_INPUT_TOKENS} input limit")
        if max_output_tokens > MODEL_MAX_OUTPUT_TOKENS:
            raise PricingError(f"{max_output_tokens} output tokens exceed the model's {MODEL_MAX_OUTPUT_TOKENS} ceiling")
        m = self.multipliers(input_tokens)
        total = (self._cost(input_tokens, self.cache_write_per_million, m["cache_write"])
                 + self._cost(max_output_tokens, self.output_per_million, m["output"]))
        return self._uplift(total)

    def settle_micro(self, usage: dict) -> int:
        """The charge for a validated usage block, the cached and written partitions at their own rates."""
        u = validate_usage(usage)
        m = self.multipliers(u["input_tokens"])
        plain = u["input_tokens"] - u["cached_tokens"] - u["cache_write_tokens"]
        total = (self._cost(plain, self.input_per_million, m["input"])
                 + self._cost(u["cached_tokens"], self.cached_per_million, m["cached"])
                 + self._cost(u["cache_write_tokens"], self.cache_write_per_million, m["cache_write"])
                 + self._cost(u["output_tokens"], self.output_per_million, m["output"]))
        return self._uplift(total)


def validate_usage(usage) -> dict:
    """The response's usage block as integers, or PricingError: a malformed block must never settle as zero."""
    if not isinstance(usage, dict):
        raise PricingError("usage block missing or not an object")

    def nonneg(v, name):
        if type(v) is not int or v < 0:
            raise PricingError(f"usage.{name} must be a non-negative integer")
        return v

    inp = nonneg(usage.get("input_tokens"), "input_tokens")
    out = nonneg(usage.get("output_tokens"), "output_tokens")
    det = usage.get("input_tokens_details") or {}
    if not isinstance(det, dict):
        raise PricingError("usage.input_tokens_details must be an object")
    cached = nonneg(det.get("cached_tokens", 0), "input_tokens_details.cached_tokens")
    written = nonneg(det.get("cache_write_tokens", 0), "input_tokens_details.cache_write_tokens")
    if cached + written > inp:
        raise PricingError("cached + cache-write tokens exceed input tokens")
    odet = usage.get("output_tokens_details") or {}
    if not isinstance(odet, dict):
        raise PricingError("usage.output_tokens_details must be an object")
    reasoning = nonneg(odet.get("reasoning_tokens", 0), "output_tokens_details.reasoning_tokens")
    if reasoning > out:
        raise PricingError("reasoning tokens exceed output tokens")
    total = usage.get("total_tokens")
    if total is not None and (type(total) is not int or total != inp + out):
        raise PricingError("usage.total_tokens disagrees with input + output")
    return {"input_tokens": inp, "cached_tokens": cached, "cache_write_tokens": written, "output_tokens": out,
            "reasoning_tokens": reasoning}
