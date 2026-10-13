"""Pre-run cost forecasts (plan/WP2_DESIGN.md section 8; ASSEMBLY M2).

``estimate`` preserves the historical Studio model and its exact evidence below. Assembled jobs use
``estimate_g3d``: a fresh first request is uncached; later requests reuse the preceding prefix. Its growth matches
total token exposure (cost sums requests), rather than extrapolating the last input back over the whole run.
Eight complete paid glasses3d runs calibrate that model. All eight remain in honest leave-one-out validation
(median absolute error 6.46%); the same method independently gives 8.68% on all 13 Studio runs. No output/effort
strata or fitted correction constants are used. The corpus predates image pruning: no pruning savings are assumed,
and its cache invalidation is unmeasured. Per-run errors still range -26.07% to +31.14%; this is not a money bound.

Historical Studio model:

    expected = sum over i < N of settle(C0 + g*i, cache hit h, output o)

priced request by request with the frozen tariff (pricing.Tariff, including the 272,000-token step: above it the
whole request is billed 2x input/cache and 1.5x output). C0 is the first request's input, ``initial_context_tokens``
= text characters / 4 + 1,500 per image for a fresh run (a resume passes its own counted context). N is the run's
``max_turns``. g (tokens added to the context per request), h (the cached share of the input) and o (output tokens per
request) are the medians of the 13 Blender Studio gpt-6-astra ledgers; the range repeats the sum at the p10 and the
p90 of g (h and o held at their medians). The unspendable tail is the worst-case hold (every input token at the
cache-write rate plus the whole output cap) at the last request's size: a reply is admitted only while it fits, so the
last ~tail of any cap cannot be spent.

Per-run statistics (``run_shape``), all exact fractions of a run's settled usages:
    g = (input of the last settled request - input of the first) / (requests - 1)
    h = sum of cached tokens / sum of input tokens
    o = sum of output tokens / requests
The corpus (``STUDIO_PARAMETERS``) is the set of every ``blender_agent_budget_v2`` run ledger with a run-config.json
naming gpt-6-astra and at least 15 settled requests (2026-09-30..10-02; 13 runs, 655 settled requests; three earlier
astra ledgers have no run-config.json and are outside it). The median of 13 values is the 7th; the band is the
nearest-rank p10/p90 (the 2nd and the 12th of 13). Leave-one-out over the 13 (``leave_one_out``): median absolute
error 8.20% (range -32.60% .. +32.24%; Prada, the costliest per request, is underestimated by 32.60%). A glasses3d
ledger (``g3d.run_budget/1``) is read by the same functions, so the corpus can be refitted on our own runs later.

Integer arithmetic only. Each modelled request is a whole usage block: input c_i = C0 + ceil(g*i); the written
(cache-miss) partition = ceil(c_i*(1-h)) priced at the cache-write rate, as the provider bills a miss (Prada request 1:
24,784 of 24,787 tokens written); cached = the rest; output = ceil(o). Every rounding goes up in cost, and the money is
the tariff's own integer micro-USD (every division rounding up). The design prototype truncated instead (int() of
floats); that is exactly 3,070 micro-USD lower on the section-8 example (C0 25,000, N 60: 10,110,546 here vs
10,107,476): 3,000 from 850 vs 851 output tokens on 60 requests, 70 from the input rounded up (57 requests one more
cached token at 1 micro-USD, one request one more written token at 13). An estimate is a forecast, not a bound: the
guard's per-request reservation is the bound.

The C0 formula is rough: on the 13 recorded first requests (instructions + input text + compact tool JSON / 4, plus
1,500 per image) it errs -24% .. +38% (median absolute 21%), because an image's tokens depend on its size. At N 60 a
C0 error of 1,000 tokens moves the estimate by about $0.08 (60 requests, mostly cached).
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from statistics import median
from typing import Iterable, Sequence

from .pricing import (LONG_CONTEXT_INPUT_TOKENS, MICRO, MODEL_MAX_INPUT_TOKENS, MODEL_MAX_OUTPUT_TOKENS, PricingError,
                      Tariff, ceil_div, usd_text, usd_to_micro, validate_usage)

ESTIMATE_SCHEMA = "g3d.estimate/1"
CHARS_PER_TOKEN = 4
TOKENS_PER_IMAGE = 1_500
MIN_CORPUS_REQUESTS = 15                    # a run ledger joins the corpus only with this many settled requests
BAND_PERCENTILES = (10, 90)                 # nearest rank
SUGGESTED_CAP_STEP_MICRO = 5 * MICRO        # the suggested cap: the expected run's required cap, up to the next $5
CORPUS = ("13 Blender Studio gpt-6-astra run ledgers (blender_agent_budget_v2, run-config.json naming gpt-6-astra, "
          ">= 15 settled requests; 2026-09-30..10-02; 655 settled requests)")
FLAT_USAGE_KEYS = ("input_tokens", "cached_tokens", "cache_write_tokens", "output_tokens", "reasoning_tokens")


class EstimateError(ValueError):
    """The inputs cannot be estimated honestly (malformed, or the run would outgrow the model's input limit)."""


@dataclass(frozen=True)
class Parameters:
    """g, h, o of the estimate, as exact fractions."""

    growth: Fraction        # tokens added to the context per request (g)
    cache_hit: Fraction     # cached share of the input (h)
    output: Fraction        # output tokens per request (o)

    def __post_init__(self):
        for name in ("growth", "cache_hit", "output"):
            object.__setattr__(self, name, _exact(getattr(self, name), name))
        if self.growth < 0 or self.output < 0 or not 0 <= self.cache_hit <= 1:
            raise EstimateError("growth and output must be >= 0 and the cache hit within [0, 1]")

    def with_growth(self, growth) -> "Parameters":
        return Parameters(growth=growth, cache_hit=self.cache_hit, output=self.output)

    def to_json(self) -> dict:
        return {"growth_tokens_per_request": str(self.growth), "cache_hit": str(self.cache_hit),
                "output_tokens_per_request": str(self.output)}


def _count(value, name: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise EstimateError(f"{name} must be an integer >= {minimum}")
    return value


def _ceil(value: Fraction) -> int:
    return -(-value.numerator // value.denominator)


def _exact(value, name: str) -> Fraction:
    """An exact rational (int or Fraction; never a float or a bool)."""
    if isinstance(value, Fraction):
        return value
    if type(value) is int:
        return Fraction(value)
    raise EstimateError(f"{name} must be an int or a Fraction")


# The 13-ledger medians and growth band (tests/agent/test_estimate.py recomputes every one from the committed token
# counts, and from the ledgers themselves when they are present).
STUDIO_PARAMETERS = Parameters(
    growth=Fraction(129_796, 55),           # 2,359.93 tokens/request: Oakley 3dff8674, run 20261001T085658-b197ec
    cache_hit=Fraction(4_436_251, 4_567_794),   # 0.9712: 2aae5816, run 20261001T064535-6a568a
    output=Fraction(24_662, 29))            # 850.41 tokens/request: oakley-scratch-001, run 20260930T164354-75a31d
STUDIO_GROWTH_BAND = (Fraction(51_705, 28),     # p10, 1,846.61: oakley-scratch-001, run 20260930T164354-75a31d
                      Fraction(200_341, 58))    # p90, 3,454.16: Prada 8c82d986, run 20261001T144107-ad29ba

# WP8 step 0: one image, a 4,096-token output cap, two turns, $1.00 ("spend: cents; hold: up to $0.40").
SMOKE_SETTINGS = {"images": 1, "max_output_tokens": 4_096, "max_turns": 2, "cap_usd": "1.00"}


def initial_context_tokens(text_chars: int, images: int) -> int:
    """C0 of a fresh run: every character sent as text (instructions, brief, tool schemas) / 4, rounded up, plus
    1,500 tokens per image."""
    return ceil_div(_count(text_chars, "text_chars"), CHARS_PER_TOKEN) + TOKENS_PER_IMAGE * _count(images, "images")


def request_input_tokens(initial_tokens: int, index: int, growth: Fraction) -> int:
    """c_i = C0 + ceil(g*i), the modelled input of request i (0-based)."""
    return _count(initial_tokens, "initial_tokens") + _ceil(_exact(growth, "growth") * _count(index, "index"))


def request_usage(initial_tokens: int, index: int, parameters: Parameters) -> dict:
    """The provider-shaped usage block the estimate prices for request i (0-based)."""
    tokens = request_input_tokens(initial_tokens, index, parameters.growth)
    written = _ceil(tokens * (1 - parameters.cache_hit))
    output = _ceil(parameters.output)
    return {"input_tokens": tokens, "output_tokens": output, "total_tokens": tokens + output,
            "input_tokens_details": {"cached_tokens": tokens - written, "cache_write_tokens": written},
            "output_tokens_details": {"reasoning_tokens": 0}}


@dataclass(frozen=True)
class Projection:
    """One modelled run: per-request input, settlement and worst-case hold, all integers."""

    initial_tokens: int
    requests: int
    max_output_tokens: int
    parameters: Parameters | CausalParameters
    inputs: tuple
    settled: tuple
    holds: tuple

    @property
    def spend_micro(self) -> int:
        return sum(self.settled)

    @property
    def tail_micro(self) -> int:
        """The worst-case hold at the last request's size: the part of a cap that cannot be spent."""
        return self.holds[-1]

    @property
    def required_cap_micro(self) -> int:
        """The smallest cap under which the guard admits every modelled request when each settles at its
        modelled amount: max over k of the settlements before request k plus request k's hold."""
        spent, need = 0, 0
        for settled, hold in zip(self.settled, self.holds):
            need = max(need, spent + hold)
            spent += settled
        return need

    @property
    def first_long_context_request(self) -> int | None:
        """The 1-based request number of the first request above the 272k price step, or None."""
        for number, tokens in enumerate(self.inputs, 1):
            if tokens > LONG_CONTEXT_INPUT_TOKENS:
                return number
        return None


def project(initial_tokens: int, requests: int, max_output_tokens: int, parameters: Parameters = STUDIO_PARAMETERS,
            *, tariff: Tariff | None = None) -> Projection:
    initial_tokens = _count(initial_tokens, "initial_tokens")
    requests = _count(requests, "requests", minimum=1)
    max_output_tokens = _count(max_output_tokens, "max_output_tokens", minimum=1)
    if max_output_tokens > MODEL_MAX_OUTPUT_TOKENS:
        raise EstimateError(f"max_output_tokens {max_output_tokens} exceeds the model's {MODEL_MAX_OUTPUT_TOKENS}")
    if not isinstance(parameters, Parameters):
        raise EstimateError("parameters must be a Parameters")
    tariff = Tariff.frozen() if tariff is None else tariff
    last = request_input_tokens(initial_tokens, requests - 1, parameters.growth)
    if last > MODEL_MAX_INPUT_TOKENS:
        raise EstimateError(f"at {parameters.growth} tokens per request, request {requests} would need {last} input "
                            f"tokens, above the model's {MODEL_MAX_INPUT_TOKENS}; lower max_turns")
    inputs, settled, holds = [], [], []
    try:
        for index in range(requests):
            usage = request_usage(initial_tokens, index, parameters)
            inputs.append(usage["input_tokens"])
            settled.append(tariff.settle_micro(usage))
            holds.append(tariff.reserve_micro(usage["input_tokens"], max_output_tokens))
    except PricingError as error:
        raise EstimateError(str(error)) from None
    return Projection(initial_tokens=initial_tokens, requests=requests, max_output_tokens=max_output_tokens,
                      parameters=parameters, inputs=tuple(inputs), settled=tuple(settled), holds=tuple(holds))


def cents_text(micro: int) -> str:
    """Dollars to the cent, rounded up; every machine field stays in exact micro-USD."""
    cents = ceil_div(_count(micro, "micro"), 10_000)
    return f"${cents // 100}.{cents % 100:02d}"


def suggested_cap_micro(required_cap_micro: int) -> int:
    """The suggested cap: the expected run's required cap rounded up to the next whole $5."""
    steps = ceil_div(_count(required_cap_micro, "required_cap_micro"), SUGGESTED_CAP_STEP_MICRO)
    return steps * SUGGESTED_CAP_STEP_MICRO


def estimate(initial_tokens: int, requests: int, max_output_tokens: int, *,
             parameters: Parameters = STUDIO_PARAMETERS, growth_band: tuple = STUDIO_GROWTH_BAND,
             tariff: Tariff | None = None) -> dict:
    """The approval request's ``estimate`` block (plain JSON, integer micro-USD with dollar texts): the expected
    spend of ``requests`` replies (N = max_turns) and its range at the p10/p90 growth; the tail (the hold at the last
    request's size, the part of a cap that cannot be spent; ``tail_high`` at the p90 growth); the required cap (the
    smallest cap that admits every modelled reply) at the expected and the band growths; and the suggested cap, the
    expected required cap up to the next $5 (an engineering suggestion; the owner sets the cap)."""
    if not isinstance(parameters, Parameters) or len(growth_band) != 2:
        raise EstimateError("parameters must be a Parameters and the growth band a (p10, p90) pair")
    low_growth, high_growth = (_exact(value, "growth band") for value in growth_band)
    if not low_growth <= parameters.growth <= high_growth:
        raise EstimateError("the growth band must contain the expected growth")
    tariff = Tariff.frozen() if tariff is None else tariff
    expected = project(initial_tokens, requests, max_output_tokens, parameters, tariff=tariff)
    low = project(initial_tokens, requests, max_output_tokens, parameters.with_growth(low_growth), tariff=tariff)
    high = project(initial_tokens, requests, max_output_tokens, parameters.with_growth(high_growth), tariff=tariff)
    suggested = suggested_cap_micro(expected.required_cap_micro)
    return {
        "schema": ESTIMATE_SCHEMA, "price_version": tariff.price_version, "corpus": CORPUS,
        "initial_context_tokens": expected.initial_tokens, "requests": expected.requests,
        "max_output_tokens": expected.max_output_tokens,
        "parameters": parameters.to_json(),
        "growth_band": {"p10": str(low_growth), "p90": str(high_growth)},
        "expected_micro_usd": expected.spend_micro, "expected_usd": usd_text(expected.spend_micro),
        "range_micro_usd": [low.spend_micro, high.spend_micro],
        "range_usd": [usd_text(low.spend_micro), usd_text(high.spend_micro)],
        "tail_micro_usd": expected.tail_micro, "tail_usd": usd_text(expected.tail_micro),
        "tail_high_micro_usd": high.tail_micro,
        "last_request_input_tokens": expected.inputs[-1], "last_request_input_tokens_high": high.inputs[-1],
        "first_long_context_request": expected.first_long_context_request,
        "first_long_context_request_high": high.first_long_context_request,
        "required_cap_micro_usd": expected.required_cap_micro,
        "required_cap_range_micro_usd": [low.required_cap_micro, high.required_cap_micro],
        "suggested_cap_micro_usd": suggested, "suggested_cap_usd": usd_text(suggested),
        "text": (f"Expected {cents_text(expected.spend_micro)} (range {cents_text(low.spend_micro)}-"
                 f"{cents_text(high.spend_micro)}) for up to {expected.requests} Astra replies; the last "
                 f"{cents_text(expected.tail_micro)} of any cap cannot be spent."),
    }


def smoke_estimate(text_chars: int, *, tariff: Tariff | None = None, images: int = 1,
                   cap_usd="1.00", one_request: bool = False) -> dict:
    """One-request assembled-job smoke prices all photos against its owner's cap using fresh uncached input.

    With ``one_request=False``, preserve the historical two-turn, one-image Studio forecast and its evidence.
    Neither estimate is a live token count; the guard still admits only a counted reservation within the cap.
    """
    tariff = Tariff.frozen() if tariff is None else tariff
    if one_request:
        initial = initial_context_tokens(text_chars, images)
        result = estimate_g3d(initial, 1, SMOKE_SETTINGS["max_output_tokens"], tariff=tariff)
        cap = usd_to_micro(cap_usd)
        hold = tariff.reserve_micro(initial, SMOKE_SETTINGS["max_output_tokens"])
        if cap <= 0 or hold > cap or result["expected_micro_usd"] >= cap:
            raise EstimateError(f"the one-request smoke needs a ${usd_text(hold)} hold at {initial} context tokens, "
                                f"outside the ${usd_text(cap)} smoke cap")
        return result | {"smoke": {"images": images, "max_output_tokens": SMOKE_SETTINGS["max_output_tokens"],
                                  "max_turns": 1, "cap_usd": usd_text(cap)},
                         "cap_micro_usd": cap, "suggested_cap_micro_usd": cap,
                         "suggested_cap_usd": usd_text(cap), "first_hold_micro_usd": hold,
                         "largest_hold_micro_usd": hold,
                         "text": (f"One smoke request: expected {cents_text(result['expected_micro_usd'])}; "
                                  f"estimated maximum hold {cents_text(hold)} within the ${usd_text(cap)} cap. "
                                  "Fresh uncached input; all job photos included. The live token count controls admission.")}
    settings = SMOKE_SETTINGS
    initial = initial_context_tokens(text_chars, settings["images"])
    result = estimate(initial, settings["max_turns"], settings["max_output_tokens"], tariff=tariff)
    cap = usd_to_micro(settings["cap_usd"])
    needed = max(result["required_cap_range_micro_usd"])
    if needed > cap:
        raise EstimateError(f"the smoke run needs a ${usd_text(needed)} cap at {initial} context tokens, above the "
                            f"${settings['cap_usd']} smoke cap; shorten the brief")
    largest_hold = result["tail_high_micro_usd"]
    return result | {"smoke": dict(settings), "cap_micro_usd": cap,
                     "suggested_cap_micro_usd": cap, "suggested_cap_usd": usd_text(cap),
                     "first_hold_micro_usd": tariff.reserve_micro(initial, settings["max_output_tokens"]),
                     "largest_hold_micro_usd": largest_hold,
                     "text": (f"Smoke turn: spend about {cents_text(result['expected_micro_usd'])}; hold up to "
                              f"{cents_text(largest_hold)} of the ${settings['cap_usd']} cap.")}


# ---- the corpus: per-run statistics, medians, band and the leave-one-out back-test ----


def ledger_usages(document: dict) -> list[dict]:
    """The settled usages, in request order, of a run ledger: a Blender Studio ``blender_agent_budget_v2`` (rows
    with a usage) or a glasses3d ``g3d.run_budget/1`` (settled or reconciled rows with a usage). Flat usage blocks."""
    if not isinstance(document, dict):
        raise EstimateError("a run ledger is a JSON object")
    schema = document.get("schema")
    if schema == "blender_agent_budget_v2":
        rows = [row for row in document.get("reservations") or [] if row.get("usage")]
    elif schema == "g3d.run_budget/1":
        rows = [row for row in document.get("rows") or []
                if row.get("outcome") in ("settled", "reconciled") and row.get("usage")]
    else:
        raise EstimateError(f"unknown run ledger schema {schema!r}")
    usages = []
    for row in rows:
        usage = row["usage"]
        if not isinstance(usage, dict) or any(type(usage.get(key)) is not int for key in FLAT_USAGE_KEYS):
            raise EstimateError(f"request {row.get('sequence')}: usage is not a flat token block")
        usages.append({key: usage[key] for key in FLAT_USAGE_KEYS})
    return usages


def provider_usage(flat: dict) -> dict:
    """A flat ledger usage block in the provider's shape (what Tariff.settle_micro reads)."""
    return {"input_tokens": flat["input_tokens"], "output_tokens": flat["output_tokens"],
            "total_tokens": flat["input_tokens"] + flat["output_tokens"],
            "input_tokens_details": {"cached_tokens": flat["cached_tokens"],
                                     "cache_write_tokens": flat["cache_write_tokens"]},
            "output_tokens_details": {"reasoning_tokens": flat["reasoning_tokens"]}}


@dataclass(frozen=True)
class RunShape:
    """A recorded run reduced to what the estimate models, plus what it actually settled."""

    requests: int
    initial_tokens: int
    parameters: Parameters
    settled_micro: int


def run_shape(usages: Sequence[dict], *, tariff: Tariff | None = None) -> RunShape:
    """g, h, o (exact) and the settled total of one run, from its settled usages in request order (flat blocks).
    A run whose context shrank overall (none in the corpus) gets g = 0."""
    tariff = Tariff.frozen() if tariff is None else tariff
    if len(usages) < 2:
        raise EstimateError("a run shape needs at least two settled requests")
    blocks = []
    try:
        for usage in usages:
            block = provider_usage(usage)
            validate_usage(block)
            blocks.append(block)
    except (KeyError, TypeError, PricingError) as error:
        raise EstimateError(f"invalid usage in the run: {error}") from None
    inputs = [block["input_tokens"] for block in blocks]
    total_input = sum(inputs)
    if total_input == 0:
        raise EstimateError("a run with no input tokens has no cache-hit ratio")
    n = len(blocks)
    parameters = Parameters(
        growth=max(Fraction(inputs[-1] - inputs[0], n - 1), Fraction(0)),
        cache_hit=Fraction(sum(b["input_tokens_details"]["cached_tokens"] for b in blocks), total_input),
        output=Fraction(sum(b["output_tokens"] for b in blocks), n))
    return RunShape(requests=n, initial_tokens=inputs[0], parameters=parameters,
                    settled_micro=sum(tariff.settle_micro(block) for block in blocks))


def corpus_parameters(shapes: Iterable[RunShape]) -> Parameters:
    """The medians of g, h and o over the runs (the mean of the middle two for an even count), exact."""
    shapes = list(shapes)
    if not shapes:
        raise EstimateError("an empty corpus has no medians")
    return Parameters(growth=Fraction(median(s.parameters.growth for s in shapes)),
                      cache_hit=Fraction(median(s.parameters.cache_hit for s in shapes)),
                      output=Fraction(median(s.parameters.output for s in shapes)))


def nearest_rank(values: Iterable, percentile: int):
    """The nearest-rank percentile: the ceil(p*n/100)-th smallest value (1-based)."""
    ordered = sorted(values)
    if not ordered or type(percentile) is not int or not 0 < percentile <= 100:
        raise EstimateError("nearest rank needs values and a percentile in (0, 100]")
    return ordered[ceil_div(percentile * len(ordered), 100) - 1]


def corpus_growth_band(shapes: Iterable[RunShape]) -> tuple:
    growths = [s.parameters.growth for s in shapes]
    return tuple(Fraction(nearest_rank(growths, p)) for p in BAND_PERCENTILES)


@dataclass(frozen=True)
class BackTest:
    shape: RunShape | CausalRunShape
    parameters: Parameters | CausalParameters
    estimate_micro: int

    @property
    def error(self) -> Fraction:
        """estimate / actual - 1, exact."""
        return Fraction(self.estimate_micro, self.shape.settled_micro) - 1


def leave_one_out(shapes: Sequence[RunShape], *, tariff: Tariff | None = None) -> list[BackTest]:
    """Each run estimated from its own first input and request count with the medians of the other runs."""
    shapes = list(shapes)
    if len(shapes) < 2:
        raise EstimateError("leave-one-out needs at least two runs")
    tests = []
    for index, shape in enumerate(shapes):
        parameters = corpus_parameters(shapes[:index] + shapes[index + 1:])
        # the spend does not depend on the output cap (it only sizes the holds)
        projection = project(shape.initial_tokens, shape.requests, 1, parameters, tariff=tariff)
        tests.append(BackTest(shape=shape, parameters=parameters, estimate_micro=projection.spend_micro))
    return tests


def median_absolute_error(tests: Sequence[BackTest]) -> Fraction:
    return Fraction(median(abs(test.error) for test in tests))


# ---- assembled-job forecast: first-request cache warming and measured token exposure ----

@dataclass(frozen=True)
class CausalParameters:
    """Linear input exposure, fraction of the previous prefix reused, and mean output per request."""

    growth: Fraction
    prefix_reuse: Fraction
    output: Fraction

    def __post_init__(self):
        for name in ("growth", "prefix_reuse", "output"):
            object.__setattr__(self, name, _exact(getattr(self, name), name))
        if self.growth < 0 or self.output < 0 or not 0 <= self.prefix_reuse <= 1:
            raise EstimateError("growth and output must be >= 0 and prefix reuse within [0, 1]")

    def with_growth(self, growth) -> "CausalParameters":
        return CausalParameters(growth, self.prefix_reuse, self.output)

    def to_json(self) -> dict:
        return {"growth_tokens_per_request": str(self.growth), "prefix_reuse": str(self.prefix_reuse),
                "output_tokens_per_request": str(self.output)}


G3D_METHOD = "causal-prefix-exposure/1"
G3D_CORPUS = ("8 complete paid glasses3d gpt-6-astra runs (g3d.run_budget/1, production client, max effort, "
              ">= 15 settled requests; 2026-10-10..13; 231 settled requests; before image pruning)")
G3D_PARAMETERS = CausalParameters(growth=Fraction(6_720_790, 1_727),
                                  prefix_reuse=Fraction(4_312_580, 4_318_559),
                                  output=Fraction(109_182, 77))
G3D_GROWTH_BAND = (Fraction(120_911, 38), Fraction(513_253, 105))
G3D_LIMITATIONS = (
    "Eight temple-stage runs on one product; generic job and new-product costs remain unmeasured.",
    "Corpus predates image pruning; no savings assumed and cache invalidation after pruning is unmeasured.",
    "Growth range varies only growth, not every source of uncertainty; it is not a confidence interval.",
    "Token-exposure growth forecasts summed cost, not the exact last-request size; the live reserve guard is the bound.",
)


def causal_request_usage(initial_tokens: int, index: int, parameters: CausalParameters) -> dict:
    """Fresh request 0 has no cached prefix. Later requests reuse only their predicted predecessor's input.

    Cache tokens round down; new/write and output tokens round up. This avoids the old constant hit fraction's
    false cache discount on request 0 and its dependence on the training runs' length.
    """
    if not isinstance(parameters, CausalParameters):
        raise EstimateError("parameters must be CausalParameters")
    tokens = request_input_tokens(initial_tokens, index, parameters.growth)
    cached = 0
    if index:
        previous = request_input_tokens(initial_tokens, index - 1, parameters.growth)
        reused = previous * parameters.prefix_reuse
        cached = min(tokens, reused.numerator // reused.denominator)
    output = _ceil(parameters.output)
    return {"input_tokens": tokens, "output_tokens": output, "total_tokens": tokens + output,
            "input_tokens_details": {"cached_tokens": cached, "cache_write_tokens": tokens - cached},
            "output_tokens_details": {"reasoning_tokens": 0}}


def project_g3d(initial_tokens: int, requests: int, max_output_tokens: int,
                parameters: CausalParameters = G3D_PARAMETERS, *, tariff: Tariff | None = None) -> Projection:
    initial_tokens = _count(initial_tokens, "initial_tokens")
    requests = _count(requests, "requests", minimum=1)
    max_output_tokens = _count(max_output_tokens, "max_output_tokens", minimum=1)
    if max_output_tokens > MODEL_MAX_OUTPUT_TOKENS:
        raise EstimateError(f"max_output_tokens {max_output_tokens} exceeds the model's {MODEL_MAX_OUTPUT_TOKENS}")
    if not isinstance(parameters, CausalParameters):
        raise EstimateError("parameters must be CausalParameters")
    tariff = Tariff.frozen() if tariff is None else tariff
    last = request_input_tokens(initial_tokens, requests - 1, parameters.growth)
    if last > MODEL_MAX_INPUT_TOKENS:
        raise EstimateError(f"at {parameters.growth} tokens per request, request {requests} would need {last} input "
                            f"tokens, above the model's {MODEL_MAX_INPUT_TOKENS}; lower max_turns")
    inputs, settled, holds = [], [], []
    try:
        for index in range(requests):
            usage = causal_request_usage(initial_tokens, index, parameters)
            tokens = usage["input_tokens"]
            inputs.append(tokens)
            settled.append(tariff.settle_micro(usage))
            holds.append(tariff.reserve_micro(tokens, max_output_tokens))
    except PricingError as error:
        raise EstimateError(str(error)) from None
    return Projection(initial_tokens, requests, max_output_tokens, parameters,
                      tuple(inputs), tuple(settled), tuple(holds))


def estimate_g3d(initial_tokens: int, requests: int, max_output_tokens: int, *,
                  parameters: CausalParameters = G3D_PARAMETERS, growth_band: tuple = G3D_GROWTH_BAND,
                  tariff: Tariff | None = None) -> dict:
    """The job forecast with the historical append-only corpus explicitly named; no unmeasured pruning discount.

    The range retains nearest-rank p10/p90 across every run's token-exposure growth. Cache reuse and output are
    held fixed for that range, as in the historical forecast. Tail and cap describe the modelled request sequence;
    actual request admission still depends only on the money guard's real count and worst-case reservation.
    """
    if not isinstance(parameters, CausalParameters) or len(growth_band) != 2:
        raise EstimateError("parameters must be CausalParameters and growth band a (p10, p90) pair")
    low_growth, high_growth = (_exact(value, "growth band") for value in growth_band)
    if not low_growth <= parameters.growth <= high_growth:
        raise EstimateError("the growth band must contain the expected growth")
    expected = project_g3d(initial_tokens, requests, max_output_tokens, parameters, tariff=tariff)
    low = project_g3d(initial_tokens, requests, max_output_tokens, parameters.with_growth(low_growth), tariff=tariff)
    high = project_g3d(initial_tokens, requests, max_output_tokens, parameters.with_growth(high_growth), tariff=tariff)
    suggested = suggested_cap_micro(expected.required_cap_micro)
    tariff = Tariff.frozen() if tariff is None else tariff
    return {
        "schema": ESTIMATE_SCHEMA, "price_version": tariff.price_version, "corpus": G3D_CORPUS,
        "method": G3D_METHOD, "limitations": list(G3D_LIMITATIONS),
        "initial_context_tokens": expected.initial_tokens, "requests": expected.requests,
        "max_output_tokens": expected.max_output_tokens, "parameters": parameters.to_json(),
        "growth_band": {"p10": str(low_growth), "p90": str(high_growth)},
        "expected_micro_usd": expected.spend_micro, "expected_usd": usd_text(expected.spend_micro),
        "range_micro_usd": [low.spend_micro, high.spend_micro],
        "range_usd": [usd_text(low.spend_micro), usd_text(high.spend_micro)],
        "tail_micro_usd": expected.tail_micro, "tail_usd": usd_text(expected.tail_micro),
        "tail_high_micro_usd": high.tail_micro,
        "last_request_input_tokens": expected.inputs[-1], "last_request_input_tokens_high": high.inputs[-1],
        "first_long_context_request": expected.first_long_context_request,
        "first_long_context_request_high": high.first_long_context_request,
        "required_cap_micro_usd": expected.required_cap_micro,
        "required_cap_range_micro_usd": [low.required_cap_micro, high.required_cap_micro],
        "suggested_cap_micro_usd": suggested, "suggested_cap_usd": usd_text(suggested),
        "text": (f"Expected {cents_text(expected.spend_micro)} (growth-only range {cents_text(low.spend_micro)}-"
                 f"{cents_text(high.spend_micro)}) for {expected.requests} Astra replies; modelled final-request hold "
                 f"{cents_text(expected.tail_micro)}. Historical costs before image pruning; no savings assumed."),
    }


@dataclass(frozen=True)
class CausalRunShape:
    """Sufficient statistics of one complete run, with no prompts, images, paths or provider identifiers."""

    requests: int
    initial_tokens: int
    exposure_excess: int
    previous_input_tokens: int
    cached_after_first: int
    output_tokens: int
    settled_micro: int

    @property
    def exposure_denominator(self) -> int:
        return self.requests * (self.requests - 1) // 2

    @property
    def growth(self) -> Fraction:
        return max(Fraction(self.exposure_excess, self.exposure_denominator), Fraction(0))


def causal_run_shape(usages: Sequence[dict], *, tariff: Tariff | None = None) -> CausalRunShape:
    """Validate usages, then measure Σ(C_i-C_0)/Σi for input exposure, not (C_last-C_0)/(N-1).

    Linear endpoint growth overstates observed summed input by 4.36–19.16% in all eight paid glasses3d runs.
    This method-of-moments growth targets the quantity the tariff charges for, using no fitted constants.
    """
    verified = run_shape(usages, tariff=tariff)
    first = verified.initial_tokens
    return CausalRunShape(requests=verified.requests, initial_tokens=first,
                          exposure_excess=sum(u["input_tokens"] - first for u in usages),
                          previous_input_tokens=sum(u["input_tokens"] for u in usages[:-1]),
                          cached_after_first=sum(u["cached_tokens"] for u in usages[1:]),
                          output_tokens=sum(u["output_tokens"] for u in usages), settled_micro=verified.settled_micro)


def causal_parameters(shapes: Iterable[CausalRunShape]) -> CausalParameters:
    """Pool exposure per request index, cached tokens per prior input token, output per request.

    Whole-run cache fractions depend on run length. Prior-prefix reuse is stable at 0.99763–0.99996 in the eight
    recorded runs, each of whose first requests has zero cached tokens. Every eligible run contributes fully.
    """
    shapes = list(shapes)
    if not shapes or any(not isinstance(shape, CausalRunShape) for shape in shapes):
        raise EstimateError("calibration requires nonempty CausalRunShape records")
    previous = sum(s.previous_input_tokens for s in shapes)
    if previous <= 0:
        raise EstimateError("prefix reuse requires previous input tokens")
    return CausalParameters(
        growth=max(Fraction(sum(s.exposure_excess for s in shapes), sum(s.exposure_denominator for s in shapes)),
                   Fraction(0)),
        prefix_reuse=Fraction(sum(s.cached_after_first for s in shapes), previous),
        output=Fraction(sum(s.output_tokens for s in shapes), sum(s.requests for s in shapes)))


def causal_growth_band(shapes: Iterable[CausalRunShape]) -> tuple:
    growths = [shape.growth for shape in shapes]
    return tuple(Fraction(nearest_rank(growths, p)) for p in BAND_PERCENTILES)


def leave_one_out_causal(shapes: Sequence[CausalRunShape], *, tariff: Tariff | None = None) -> list[BackTest]:
    """Fit on every other complete run; forecast only from the held-out first-input count and request count."""
    shapes = list(shapes)
    if len(shapes) < 2:
        raise EstimateError("leave-one-out needs at least two runs")
    tests = []
    for index, shape in enumerate(shapes):
        parameters = causal_parameters(shapes[:index] + shapes[index + 1:])
        forecast = project_g3d(shape.initial_tokens, shape.requests, 1, parameters, tariff=tariff)
        tests.append(BackTest(shape, parameters, forecast.spend_micro))
    return tests
