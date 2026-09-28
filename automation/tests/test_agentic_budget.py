"""Budget and pricing of ``modeler.agentic``: the frozen tariff's integer arithmetic, the transactional reservation gateway
over a real job database (including a two-process race for the last allowance) and the runner's settlement discipline as
observed through the offline scripted session. No network, no Docker, no Blender: ScriptedTransport and FakeWorker only.
Every assertion reads externally observable state (database rows through Store, files in the job folder, the payloads
the transport captured, job states and stop reasons), never implementation details."""
from __future__ import annotations

from decimal import Decimal
from fractions import Fraction
import json
import math
import multiprocessing
import os
from pathlib import Path
from types import SimpleNamespace
import unittest


from modeler.agentic.budget import PAID_PURPOSES, Budget, BudgetError, BudgetExhausted
from modeler.agentic.pricing import (MICRO, MODEL, MODEL_MAX_INPUT_TOKENS, MODEL_MAX_OUTPUT_TOKENS, PRICE_VERSION, PricingError, Tariff, ceil_div,
                                     usd_to_micro, validate_usage)
from modeler.agentic.state import StateError, Store
import test_agentic_support as support

ENDPOINT = "https://api.openai.com/v1/responses"
# the usage block every scripted demo response carries: 300 plain + 800 cached + 100 written input tokens, 300 output
DEMO_USAGE = {"input_tokens": 1200, "input_tokens_details": {"cached_tokens": 800, "cache_write_tokens": 100}, "output_tokens": 300,
              "output_tokens_details": {"reasoning_tokens": 200}, "total_tokens": 1500}
DEMO_USAGE_MICRO = 300 * 10 + 800 * 1 + 1250 + 300 * 50          # 20_050 micro-USD; 100 cache-write tokens at 12.5 = 1250


def fresh_dir(case: unittest.TestCase, prefix: str = "t") -> Path:
    """A new folder under <short tmp>/lag-budget, removed (and any leftover reported) at the test's cleanup."""
    return support.fresh_dir(case, "budget", prefix)


def minimal_job(job_dir: Path, *, cap_usd="1", operation_cap: int = 5, tariff: dict | None = None) -> Store:
    """The smallest job record Store.create accepts (the keys runner.Session.create supplies)."""
    return Store.create(job_dir, {"request": {"product_id": "budget-test"}, "policy": {"driver": "scripted", "worker": "fake"}, "fingerprints": {"protocol": "test"},
                                  "model": MODEL, "reasoning_effort": "high", "service_tier": "default", "endpoint": ENDPOINT,
                                  "tariff": Tariff.frozen().to_dict() if tariff is None else tariff, "cap_micro": usd_to_micro(cap_usd),
                                  "inference_operation_cap": operation_cap, "driver": "scripted", "worker": "fake"})


def _quiet(*_a, **_k) -> None:
    pass


_RT = None


def _rt():
    """The runner-side modules, imported lazily so the spawned race children (which import this module) stay light."""
    global _RT
    if _RT is None:
        from modeler.agentic import config, demo, executor, responses, runner
        _RT = SimpleNamespace(config=config, demo=demo, executor=executor, responses=responses, runner=runner)
    return _RT


# =============================================================================================== pricing
class UsdToMicro(unittest.TestCase):
    def test_exact_decimal_parsing(self):
        self.assertEqual(usd_to_micro("1"), 1_000_000)
        self.assertEqual(usd_to_micro(1), MICRO)
        self.assertEqual(usd_to_micro("0.1"), 100_000)
        self.assertEqual(usd_to_micro(0.1), 100_000)                  # parsed through str(): no binary-float residue
        self.assertEqual(usd_to_micro(2.675), 2_675_000)              # the binary float is 2.67499999...; Decimal keeps the typed value
        self.assertEqual(usd_to_micro("15"), 15_000_000)
        self.assertEqual(usd_to_micro("200"), 200_000_000)
        self.assertEqual(usd_to_micro(Decimal("0.000001")), 1)
        self.assertEqual(usd_to_micro(0), 0)

    def test_rounds_up_below_the_micro_dollar(self):
        self.assertEqual(usd_to_micro("0.0000001"), 1)
        self.assertEqual(usd_to_micro("1e-7"), 1)
        self.assertEqual(usd_to_micro("1.0000005"), 1_000_001)
        self.assertEqual(usd_to_micro("0.9999999"), 1_000_000)

    def test_refuses_bool_negative_nonfinite_and_garbage(self):
        for bad in (True, False, -1, "-0.01", "nan", "inf", "-inf", "abc", None, [1], "1,5"):
            with self.assertRaises(PricingError, msg=repr(bad)):
                usd_to_micro(bad)

    def test_ceil_div(self):
        self.assertEqual(ceil_div(7, 2), 4)
        self.assertEqual(ceil_div(6, 2), 3)
        self.assertEqual(ceil_div(0, 5), 0)
        self.assertEqual(ceil_div(1, 1_000_000), 1)
        for b in (0, -1):
            with self.assertRaises(PricingError):
                ceil_div(1, b)


class FrozenTariff(unittest.TestCase):
    def test_default_is_the_standard_global_table(self):
        t = Tariff.frozen()
        self.assertEqual((t.price_version, t.model, t.service_tier, t.region, t.uplift_percent), (PRICE_VERSION, MODEL, "default", "global", 0))
        self.assertEqual((t.input_per_million, t.cached_per_million, t.cache_write_per_million, t.output_per_million), (10 * MICRO, 1 * MICRO, 12_500_000, 50 * MICRO))
        self.assertEqual(t.long_context_input_tokens, 272_000)
        self.assertEqual(Tariff.frozen(region="us").uplift_percent, 0)
        self.assertEqual(Tariff.frozen(region="eu").uplift_percent, 10)

    def test_refuses_everything_outside_the_table(self):
        with self.assertRaises(PricingError):
            Tariff.frozen(fast_mode=True)
        for tier in ("flex", "priority", "auto", "batch", "scale", ""):
            with self.assertRaises(PricingError, msg=tier):
                Tariff.frozen(service_tier=tier)
        for region in ("apac", "EU", "europe", "", "uk"):
            with self.assertRaises(PricingError, msg=region):
                Tariff.frozen(region=region)

    def test_from_dict_round_trips_and_refuses_another_price_version(self):
        t = Tariff.frozen(region="eu")
        self.assertEqual(Tariff.from_dict(t.to_dict()), t)
        self.assertEqual(Tariff.from_dict(json.loads(json.dumps(t.to_dict()))), t)
        foreign = dict(t.to_dict(), price_version="gpt-6-astra-standard-2027-01-01")
        with self.assertRaises(PricingError) as cm:
            Tariff.from_dict(foreign)
        self.assertIn("2027-01-01", str(cm.exception))
        self.assertIn(PRICE_VERSION, str(cm.exception))


class Reservations(unittest.TestCase):
    def setUp(self):
        self.t = Tariff.frozen()

    def test_short_context_hand_computation(self):
        # every input token at the cache-write rate (12.5 USD/M) plus the whole output limit at 50 USD/M, in micro-USD
        self.assertEqual(self.t.reserve_micro(1000, 4000), 12_500 + 200_000)
        self.assertEqual(self.t.reserve_micro(0, 256), 12_800)
        self.assertEqual(self.t.reserve_micro(1, 256), 13 + 12_800)          # ceil(12.5) for one written token
        self.assertEqual(self.t.reserve_micro(3, 256), 38 + 12_800)          # ceil(37.5)
        for inp, out in ((1, 256), (7, 1000), (123_457, 24_000), (272_000, 128_000), (12, 4000)):
            exact = Fraction(inp * 25, 2) + Fraction(out * 50)
            self.assertEqual(self.t.reserve_micro(inp, out), math.ceil(exact), (inp, out))

    def test_an_all_cache_write_usage_settles_exactly_at_the_reservation(self):
        usage = {"input_tokens": 1200, "input_tokens_details": {"cache_write_tokens": 1200}, "output_tokens": 300}
        self.assertEqual(self.t.settle_micro(usage), self.t.reserve_micro(1200, 300))
        self.assertLessEqual(self.t.settle_micro(DEMO_USAGE), self.t.reserve_micro(1200, 300))

    def test_long_context_boundary_is_at_input_tokens_not_total(self):
        self.assertFalse(self.t.multipliers(272_000)["long_context"])
        self.assertTrue(self.t.multipliers(272_001)["long_context"])
        self.assertEqual(self.t.reserve_micro(272_000, 1000), 3_400_000 + 50_000)          # 1x
        self.assertEqual(self.t.reserve_micro(272_001, 1000), 6_800_025 + 75_000)          # 2x input/cache, 1.5x output
        # 272_000 input + 100_000 output = 372_000 total tokens: still the short-context tariff
        self.assertEqual(self.t.settle_micro({"input_tokens": 272_000, "output_tokens": 100_000}), 2_720_000 + 5_000_000)
        # one token over: the WHOLE request is long-context, cached and plain partitions included
        long_ = {"input_tokens": 272_001, "input_tokens_details": {"cached_tokens": 272_000}, "output_tokens": 10}
        self.assertEqual(self.t.settle_micro(long_), 1 * 10 * 2 + 272_000 * 1 * 2 + 10 * 50 * 3 // 2)
        self.assertEqual(self.t.settle_micro({"input_tokens": 272_001, "output_tokens": 0}), 272_001 * 20)

    def test_eu_uplift_is_applied_after_the_sum_and_rounded_up(self):
        eu = Tariff.frozen(region="eu")
        self.assertEqual(eu.reserve_micro(1000, 4000), 233_750)                       # 212_500 x 1.1 exactly
        self.assertEqual(eu.reserve_micro(1, 256), 14_095)                            # ceil(12_813 x 1.1 = 14_094.3)
        usage = {"input_tokens": 2, "input_tokens_details": {"cached_tokens": 1, "cache_write_tokens": 1}, "output_tokens": 0}
        self.assertEqual(self.t.settle_micro(usage), 1 + 13)
        self.assertEqual(Tariff.frozen(region="us").settle_micro(usage), 14)
        self.assertEqual(eu.settle_micro(usage), 16)                                  # ceil(14 x 1.1 = 15.4); per-partition rounding would give 2 + 15 = 17

    def test_reserve_refuses_bad_counts(self):
        for inp, out in ((1000.0, 4000), (1000, 4000.0), (True, 4000), (1000, True), (-1, 4000), (1000, 0), (1000, -5), ("1000", 4000),
                         (MODEL_MAX_INPUT_TOKENS + 1, 256), (1000, MODEL_MAX_OUTPUT_TOKENS + 1)):
            with self.assertRaises(PricingError, msg=f"{inp!r}, {out!r}"):
                self.t.reserve_micro(inp, out)
        self.assertEqual(self.t.reserve_micro(MODEL_MAX_INPUT_TOKENS, MODEL_MAX_OUTPUT_TOKENS), MODEL_MAX_INPUT_TOKENS * 25 + 128_000 * 75)   # long context


class UsageValidation(unittest.TestCase):
    def test_accepts_the_demo_block_and_defaults_missing_details(self):
        self.assertEqual(validate_usage(DEMO_USAGE), {"input_tokens": 1200, "cached_tokens": 800, "cache_write_tokens": 100, "output_tokens": 300, "reasoning_tokens": 200})
        self.assertEqual(validate_usage({"input_tokens": 5, "output_tokens": 0}), {"input_tokens": 5, "cached_tokens": 0, "cache_write_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0})
        self.assertEqual(validate_usage({"input_tokens": 5, "output_tokens": 2, "total_tokens": 7})["output_tokens"], 2)

    def test_rejections(self):
        bad = {"negative input": dict(DEMO_USAGE, input_tokens=-1),
               "bool input": dict(DEMO_USAGE, input_tokens=True),
               "float input": dict(DEMO_USAGE, input_tokens=1200.0),
               "float output": dict(DEMO_USAGE, output_tokens=300.0),
               "string output": dict(DEMO_USAGE, output_tokens="300"),
               "cached + write > input": dict(DEMO_USAGE, input_tokens_details={"cached_tokens": 1100, "cache_write_tokens": 101}),
               "float cached": dict(DEMO_USAGE, input_tokens_details={"cached_tokens": 800.0}),
               "negative cached": dict(DEMO_USAGE, input_tokens_details={"cached_tokens": -1}),
               "reasoning > output": dict(DEMO_USAGE, output_tokens_details={"reasoning_tokens": 301}),
               "total mismatch": dict(DEMO_USAGE, total_tokens=1501),
               "total bool": dict(DEMO_USAGE, total_tokens=True),
               "details not an object": dict(DEMO_USAGE, input_tokens_details=[800]),
               "output details not an object": dict(DEMO_USAGE, output_tokens_details="x"),
               "missing input_tokens": {"output_tokens": 300},
               "missing output_tokens": {"input_tokens": 300}}
        for name, usage in bad.items():
            with self.assertRaises(PricingError, msg=name):
                validate_usage(usage)
        for missing in (None, [], "usage", 5, ()):
            with self.assertRaises(PricingError, msg=repr(missing)):
                validate_usage(missing)


class Settlement(unittest.TestCase):
    def test_partitions_at_their_own_rates(self):
        t = Tariff.frozen()
        self.assertEqual(t.settle_micro(DEMO_USAGE), DEMO_USAGE_MICRO)
        self.assertEqual(DEMO_USAGE_MICRO, 20_050)
        self.assertEqual(t.settle_micro({"input_tokens": 1, "output_tokens": 0}), 10)                                                          # one plain token
        self.assertEqual(t.settle_micro({"input_tokens": 1, "input_tokens_details": {"cached_tokens": 1}, "output_tokens": 0}), 1)          # one cached token: exactly 1 micro-USD
        self.assertEqual(t.settle_micro({"input_tokens": 1, "input_tokens_details": {"cache_write_tokens": 1}, "output_tokens": 0}), 13)    # ceil(12.5)
        self.assertEqual(t.settle_micro({"input_tokens": 0, "output_tokens": 1}), 50)
        self.assertEqual(t.settle_micro({"input_tokens": 0, "output_tokens": 0}), 0)
        # reasoning tokens are a detail of the output partition, never billed twice
        self.assertEqual(t.settle_micro({"input_tokens": 0, "output_tokens": 10, "output_tokens_details": {"reasoning_tokens": 10}}), 500)
        # the partitions are ceiled separately: three odd write counts do not lose their halves
        self.assertEqual(t.settle_micro({"input_tokens": 3, "input_tokens_details": {"cache_write_tokens": 3}, "output_tokens": 0}), 38)

    def test_settle_refuses_a_malformed_block(self):
        t = Tariff.frozen()
        for usage in (None, {}, {"input_tokens": 1}, dict(DEMO_USAGE, total_tokens=1)):
            with self.assertRaises(PricingError, msg=repr(usage)):
                t.settle_micro(usage)


# =============================================================================================== the gateway over a real store
class BudgetGateway(unittest.TestCase):
    def setUp(self):
        self.dir = fresh_dir(self, "b")
        self.store = minimal_job(self.dir / "job", cap_usd="1", operation_cap=5)
        self.addCleanup(self.store.close)
        self.budget = Budget(self.store)
        self.tariff = Tariff.frozen()

    def reserve(self, *, role="author", purpose="author", input_tokens=1000, max_output_tokens=4000, **kw) -> dict:
        return self.budget.reserve(role=role, purpose=purpose, input_tokens=input_tokens, max_output_tokens=max_output_tokens, **kw)

    def test_reserve_holds_the_worst_case_before_any_request_row(self):
        row = self.reserve(input_tokens=1000, max_output_tokens=4000)
        self.assertEqual(row["reserved_micro"], 212_500)
        self.assertEqual(row["reserved_micro"], self.tariff.reserve_micro(1000, 4000))
        self.assertEqual((row["state"], row["request_id"], row["counts_operation"], row["price_version"], row["liability_micro"]), ("held", None, 1, PRICE_VERSION, 0))
        self.assertEqual(self.store.requests(), [])
        self.assertEqual(self.store.reservation(row["id"]), row)
        t = self.budget.totals()
        self.assertEqual((t["cap_micro"], t["settled_micro"], t["unknown_liability_micro"], t["held_micro"]), (1_000_000, 0, 0, 212_500))
        self.assertEqual((t["upper_bound_micro"], t["remaining_micro"], t["operations_used"], t["operations_cap"], t["operations_remaining"]), (212_500, 787_500, 1, 5, 4))
        self.assertEqual((t["cap_usd"], t["held_usd"], t["remaining_usd"], t["price_version"]), ("1.000000", "0.212500", "0.787500", PRICE_VERSION))
        self.assertEqual(t["per_role"], {"author": {"settled_micro": 0, "unknown_liability_micro": 0, "held_micro": 212_500, "operations": 1}})
        events = self.store.events("budget_reserved")
        self.assertEqual(len(events), 1)
        self.assertEqual((events[0]["data"]["reservation"], events[0]["data"]["reserved_micro"], events[0]["data"]["input_tokens"], events[0]["data"]["max_output_tokens"]),
                         (row["id"], 212_500, 1000, 4000))

    def test_reserve_rejects_before_any_row_when_the_cap_would_be_exceeded(self):
        first = self.reserve(input_tokens=0, max_output_tokens=12_000)          # 0.6 USD
        self.assertEqual(first["reserved_micro"], 600_000)
        with self.assertRaises(BudgetExhausted) as cm:
            self.reserve(input_tokens=0, max_output_tokens=12_000)              # another 0.6 would pass 1.0
        self.assertIn("no request sent", str(cm.exception))
        self.assertIsInstance(cm.exception, BudgetError)
        self.assertIsInstance(cm.exception, StateError)
        self.assertEqual([r["id"] for r in self.store.reservations()], [first["id"]])
        self.assertEqual(self.store.requests(), [])
        self.assertEqual(len(self.store.events("budget_reserved")), 1)
        # exactly filling the cap is allowed; one more micro-dollar is not
        second = self.reserve(input_tokens=0, max_output_tokens=8_000)         # 0.4 USD: 0.6 + 0.4 == 1.0
        self.assertEqual(self.budget.totals()["remaining_micro"], 0)
        with self.assertRaises(BudgetExhausted):
            self.reserve(input_tokens=0, max_output_tokens=256)
        self.assertEqual({r["id"] for r in self.store.reservations()}, {first["id"], second["id"]})

    def test_settled_and_unknown_amounts_count_against_new_reservations(self):
        a = self.reserve(input_tokens=0, max_output_tokens=8_000)               # 400_000 held
        self.budget.settle(a["id"], {"input_tokens": 0, "output_tokens": 8_000}, request_id="q0001")     # settled 400_000
        b = self.reserve(input_tokens=0, max_output_tokens=8_000)               # 400_000 held
        self.budget.mark_unknown(b["id"], "timeout", request_id="q0002")        # unknown liability 400_000
        with self.assertRaises(BudgetExhausted):
            self.reserve(input_tokens=0, max_output_tokens=8_000)               # 400_000 + 400_000 + 400_000 > cap
        c = self.reserve(input_tokens=0, max_output_tokens=4_000)               # 200_000 fits exactly
        t = self.budget.totals()
        self.assertEqual((t["settled_micro"], t["unknown_liability_micro"], t["held_micro"], t["upper_bound_micro"], t["remaining_micro"]), (400_000, 400_000, 200_000, 1_000_000, 0))
        self.assertEqual(len(self.store.reservations()), 3)
        self.assertEqual(c["state"], "held")

    def test_unpriceable_or_unpaid_purposes_leave_no_row(self):
        with self.assertRaises(BudgetError) as cm:
            self.reserve(purpose="marketing")
        self.assertNotIsInstance(cm.exception, BudgetExhausted)
        for bad in ({"max_output_tokens": 0}, {"input_tokens": MODEL_MAX_INPUT_TOKENS + 1}, {"max_output_tokens": MODEL_MAX_OUTPUT_TOKENS + 1}, {"input_tokens": -1}):
            with self.assertRaises(BudgetError, msg=repr(bad)) as cm:
                self.reserve(**bad)
            self.assertNotIsInstance(cm.exception, BudgetExhausted)
        self.assertEqual(self.store.reservations(), [])
        self.assertEqual(self.store.events("budget_reserved"), [])
        self.assertEqual(set(PAID_PURPOSES), {"author", "intake_reading", "intake_review", "critic", "final", "compaction"})

    def test_reserve_refuses_non_integer_token_counts(self):
        """The tariff refuses floats and booleans; the gateway must not coerce them into a smaller integer first
        (1000.9 tokens reserved as 1000 would be a reservation below the worst case)."""
        for bad in ({"input_tokens": 1000.9}, {"max_output_tokens": 4000.5}, {"input_tokens": True}, {"max_output_tokens": True}, {"input_tokens": "1000"}):
            with self.assertRaises(BudgetError, msg=repr(bad)):
                self.reserve(**bad)
        self.assertEqual(self.store.reservations(), [])

    def test_operation_cap_is_shared_across_every_paid_role(self):
        for role, purpose in (("author", "author"), ("intake", "intake_reading"), ("critic", "critic"), ("final", "final"), ("author", "compaction")):
            self.reserve(role=role, purpose=purpose, input_tokens=0, max_output_tokens=256)
        self.assertEqual(self.budget.totals()["operations_used"], 5)
        with self.assertRaises(BudgetExhausted) as cm:
            self.reserve(role="intake", purpose="intake_review", input_tokens=0, max_output_tokens=256)
        self.assertIn("inference-operation cap", str(cm.exception))
        self.assertEqual(len(self.store.reservations()), 5)
        # a reservation that does not count an operation still passes and still holds money
        free = self.reserve(role="author", purpose="author", input_tokens=0, max_output_tokens=256, counts_operation=False)
        self.assertEqual(free["counts_operation"], 0)
        t = self.budget.totals()
        self.assertEqual((t["operations_used"], t["operations_remaining"], t["held_micro"]), (5, 0, 6 * 12_800))
        self.assertEqual({r: d["operations"] for r, d in t["per_role"].items()}, {"author": 2, "intake": 1, "critic": 1, "final": 1})
        # releasing a held reservation frees its slot; settling or marking unknown does not
        first = self.store.reservations()[0]
        self.budget.release(first["id"], "never sent")
        self.assertEqual(self.budget.totals()["operations_used"], 4)
        again = self.reserve(role="critic", purpose="critic", input_tokens=0, max_output_tokens=256)
        self.budget.settle(again["id"], {"input_tokens": 0, "output_tokens": 1}, request_id="q0009")
        self.assertEqual(self.budget.totals()["operations_used"], 5)
        with self.assertRaises(BudgetExhausted):
            self.reserve(role="author", purpose="author", input_tokens=0, max_output_tokens=256)

    def test_settle_once_is_idempotent_for_the_same_request_only(self):
        row = self.reserve(input_tokens=1200, max_output_tokens=300)
        self.assertEqual(self.budget.settle(row["id"], DEMO_USAGE, request_id="q0001"), DEMO_USAGE_MICRO)
        after = self.store.reservation(row["id"])
        self.assertEqual((after["state"], after["settled_micro"], after["request_id"], after["liability_micro"]), ("settled", 20_050, "q0001", 0))
        self.assertEqual(after["usage"], validate_usage(DEMO_USAGE))
        self.assertIsNotNone(after["settled_utc"])
        # the same request again: the recorded amount, even with a different block; no second settlement event
        self.assertEqual(self.budget.settle(row["id"], {"input_tokens": 0, "output_tokens": 1}, request_id="q0001"), 20_050)
        self.assertEqual(len(self.store.events("budget_settled")), 1)
        # another request cannot claim it
        with self.assertRaises(BudgetError):
            self.budget.settle(row["id"], DEMO_USAGE, request_id="q0002")
        self.assertEqual(self.store.reservation(row["id"]), after)
        self.assertEqual(self.budget.totals()["settled_micro"], 20_050)
        with self.assertRaises(BudgetError):
            self.budget.settle("b9999", DEMO_USAGE, request_id="q0001")

    def test_bind_request_binds_once(self):
        row = self.reserve()
        self.budget.bind_request(row["id"], "q0001")
        self.budget.bind_request(row["id"], "q0001")
        with self.assertRaises(BudgetError):
            self.budget.bind_request(row["id"], "q0002")
        with self.assertRaises(BudgetError):
            self.budget.settle(row["id"], DEMO_USAGE, request_id="q0002")
        self.assertEqual(self.store.reservation(row["id"])["state"], "held")
        self.assertEqual(self.budget.settle(row["id"], DEMO_USAGE, request_id="q0001"), 20_050)

    def test_malformed_usage_becomes_an_unknown_liability_of_the_whole_reservation(self):
        row = self.reserve(input_tokens=0, max_output_tokens=4000)              # 200_000
        with self.assertRaises(BudgetError) as cm:
            self.budget.settle(row["id"], {"input_tokens": 5}, request_id="q0001")
        self.assertNotIsInstance(cm.exception, BudgetExhausted)
        after = self.store.reservation(row["id"])
        self.assertEqual((after["state"], after["liability_micro"], after["settled_micro"], after["request_id"]), ("unknown", 200_000, None, "q0001"))
        self.assertEqual(after["liability_micro"], after["reserved_micro"])
        self.assertTrue(after["reason"].startswith("usage unusable"))
        self.assertEqual(after["usage"], {"input_tokens": 5})
        t = self.budget.totals()
        self.assertEqual((t["unknown_liability_micro"], t["held_micro"], t["settled_micro"], t["upper_bound_micro"], t["operations_used"]), (200_000, 0, 0, 200_000, 1))
        self.assertEqual(len(self.store.events("budget_unknown")), 1)
        with self.assertRaises(BudgetError):                                    # unknown is not held: a later good block cannot settle it
            self.budget.settle(row["id"], DEMO_USAGE, request_id="q0001")
        # a block that is not even an object is kept as text
        other = self.reserve(input_tokens=0, max_output_tokens=256)
        with self.assertRaises(BudgetError):
            self.budget.settle(other["id"], None, request_id="q0002")
        self.assertEqual(self.store.reservation(other["id"])["usage"], {"raw": "None"})
        # a block that cannot even be serialised (a non-finite number) still becomes an unknown liability, not a held row
        nan = self.reserve(input_tokens=0, max_output_tokens=256)
        with self.assertRaises(BudgetError):
            self.budget.settle(nan["id"], {"input_tokens": float("nan"), "output_tokens": 1}, request_id="q0003")
        z = self.store.reservation(nan["id"])
        self.assertEqual((z["state"], z["liability_micro"], z["request_id"]), ("unknown", 12_800, "q0003"))
        self.assertIn("nan", z["usage"]["raw"])
        self.assertEqual(self.budget.totals()["unknown_liability_micro"], 200_000 + 12_800 + 12_800)
        self.assertEqual(len(self.store.events("budget_unknown")), 3)

    def test_settlement_above_the_reservation_is_recorded_and_counted_at_the_larger_number(self):
        row = self.reserve(input_tokens=0, max_output_tokens=256)               # 12_800 reserved
        amount = self.budget.settle(row["id"], {"input_tokens": 0, "output_tokens": 1000}, request_id="q0001")
        self.assertEqual(amount, 50_000)
        ev = self.store.events("budget_settlement_exceeds_reservation")
        self.assertEqual(len(ev), 1)
        self.assertEqual((ev[0]["data"]["reservation"], ev[0]["data"]["reserved_micro"], ev[0]["data"]["settled_micro"]), (row["id"], 12_800, 50_000))
        self.assertEqual(self.store.reservation(row["id"])["settled_micro"], 50_000)
        self.assertEqual(self.budget.totals()["settled_micro"], 50_000)

    def test_mark_unknown_then_reconcile_with_owner_authorization(self):
        row = self.reserve(input_tokens=0, max_output_tokens=4000)
        self.budget.mark_unknown(row["id"], "read timeout after send", request_id="q0007")
        after = self.store.reservation(row["id"])
        self.assertEqual((after["state"], after["liability_micro"], after["request_id"], after["reason"]), ("unknown", 200_000, "q0007", "read timeout after send"))
        self.budget.mark_unknown(row["id"], "again")                            # idempotent, no second event
        self.assertEqual(len(self.store.events("budget_unknown")), 1)
        for call in (lambda: self.budget.settle(row["id"], DEMO_USAGE, request_id="q0007"),
                     lambda: self.budget.release(row["id"], "x"),
                     lambda: self.budget.settle_rejected(row["id"], request_id="q0007", http_status=429, reason="x"),
                     lambda: self.budget.reconcile_unknown(row["id"], authorized_by="", usage=DEMO_USAGE)):
            with self.assertRaises(BudgetError):
                call()
        with self.assertRaises((BudgetError, PricingError)):                   # malformed dashboard numbers never settle
            self.budget.reconcile_unknown(row["id"], authorized_by="owner", usage={"input_tokens": -1})
        self.assertEqual(self.store.reservation(row["id"])["state"], "unknown")
        self.assertEqual(self.budget.totals()["unknown_liability_micro"], 200_000)
        # verified usage from the dashboard
        self.assertEqual(self.budget.reconcile_unknown(row["id"], authorized_by="owner: dashboard 2026-09-27", usage=DEMO_USAGE), 20_050)
        done = self.store.reservation(row["id"])
        self.assertEqual((done["state"], done["settled_micro"], done["liability_micro"], done["reason"]), ("settled", 20_050, 0, "reconciled by owner: dashboard 2026-09-27"))
        self.assertEqual(done["usage"], validate_usage(DEMO_USAGE))
        self.assertEqual(self.store.events("budget_reconciled")[-1]["data"]["settled_micro"], 20_050)
        # an explicit statement that nothing was charged
        other = self.reserve(input_tokens=0, max_output_tokens=256)
        self.budget.mark_unknown(other["id"], "crash after send")
        self.assertEqual(self.budget.reconcile_unknown(other["id"], authorized_by="owner", usage=None, no_charge=True), 0)
        z = self.store.reservation(other["id"])
        self.assertEqual((z["state"], z["settled_micro"], z["liability_micro"]), ("settled", 0, 0))
        t = self.budget.totals()
        self.assertEqual((t["unknown_liability_micro"], t["settled_micro"], t["operations_used"]), (0, 20_050, 2))
        # settled and held rows cannot become unknown / be reconciled
        with self.assertRaises(BudgetError):
            self.budget.mark_unknown(row["id"], "late")
        held = self.reserve(input_tokens=0, max_output_tokens=256)
        with self.assertRaises(BudgetError):
            self.budget.reconcile_unknown(held["id"], authorized_by="owner", usage=None, no_charge=True)

    def test_release_only_from_held_and_only_once(self):
        row = self.reserve()
        self.budget.release(row["id"], "prepared but never sent")
        after = self.store.reservation(row["id"])
        self.assertEqual((after["state"], after["liability_micro"], after["reason"], after["settled_micro"]), ("released", 0, "prepared but never sent", None))
        t = self.budget.totals()
        self.assertEqual((t["held_micro"], t["operations_used"], t["upper_bound_micro"]), (0, 0, 0))
        with self.assertRaises(BudgetError):
            self.budget.release(row["id"], "twice")
        self.assertEqual(len(self.store.events("budget_released")), 1)
        settled = self.reserve()
        self.budget.settle(settled["id"], DEMO_USAGE, request_id="q0001")
        with self.assertRaises(BudgetError):
            self.budget.release(settled["id"], "after settlement")
        self.assertEqual(self.store.reservation(settled["id"])["state"], "settled")

    def test_settle_rejected_only_for_4xx_and_the_slot_stays_consumed(self):
        row = self.reserve()
        for status in (200, 399, 500, 503, 0):
            with self.assertRaises(BudgetError, msg=status):
                self.budget.settle_rejected(row["id"], request_id="q0001", http_status=status, reason="x")
        self.assertEqual(self.store.reservation(row["id"])["state"], "held")
        self.budget.settle_rejected(row["id"], request_id="q0001", http_status=429, reason="rate limited")
        after = self.store.reservation(row["id"])
        self.assertEqual((after["state"], after["settled_micro"], after["liability_micro"], after["request_id"]), ("settled", 0, 0, "q0001"))
        self.assertTrue(after["reason"].startswith("http 429"))
        t = self.budget.totals()
        self.assertEqual((t["settled_micro"], t["operations_used"], t["remaining_micro"]), (0, 1, 1_000_000))
        ev = self.store.events("budget_settled")
        self.assertEqual((ev[-1]["data"]["settled_micro"], ev[-1]["data"]["http_status"]), (0, 429))
        with self.assertRaises(BudgetError):
            self.budget.settle_rejected(row["id"], request_id="q0001", http_status=429, reason="again")
        self.assertEqual(self.budget.settle(row["id"], DEMO_USAGE, request_id="q0001"), 0)       # same request: the recorded zero

    def test_totals_arithmetic_and_remaining_never_negative(self):
        held = self.reserve(input_tokens=0, max_output_tokens=256)                  # 12_800
        unknown = self.reserve(input_tokens=0, max_output_tokens=256)
        self.budget.mark_unknown(unknown["id"], "timeout")
        settled = self.reserve(input_tokens=1200, max_output_tokens=300)            # 30_000 reserved, settles at 20_050
        self.budget.settle(settled["id"], DEMO_USAGE, request_id="q0003")
        t = self.budget.totals()
        self.assertEqual((t["held_micro"], t["unknown_liability_micro"], t["settled_micro"]), (12_800, 12_800, 20_050))
        self.assertEqual(t["upper_bound_micro"], 12_800 + 12_800 + 20_050)
        self.assertEqual(t["remaining_micro"], 1_000_000 - 45_650)
        self.assertEqual((t["upper_bound_usd"], t["remaining_usd"], t["settled_usd"]), ("0.045650", "0.954350", "0.020050"))
        self.assertEqual(t["operations_used"], 3)
        # a settlement far above the cap: the upper bound exceeds the cap and remaining is zero, not negative
        self.budget.settle(held["id"], {"input_tokens": 0, "output_tokens": 128_000}, request_id="q0001")      # 6.4 USD
        t = self.budget.totals()
        self.assertEqual(t["settled_micro"], 6_400_000 + 20_050)
        self.assertGreater(t["upper_bound_micro"], t["cap_micro"])
        self.assertEqual((t["remaining_micro"], t["remaining_usd"], t["operations_remaining"]), (0, "0.000000", 2))
        with self.assertRaises(BudgetExhausted):
            self.reserve(input_tokens=0, max_output_tokens=256)

    def test_caps_live_in_the_job_row_and_cannot_be_raised(self):
        self.reserve(input_tokens=0, max_output_tokens=256)
        reopened = Store.open(self.dir / "job")
        self.addCleanup(reopened.close)
        other = Budget(reopened)
        self.assertEqual((other.cap_micro, other.operation_cap), (1_000_000, 5))
        self.assertEqual(other.totals(), self.budget.totals())
        for fields in ({"cap_micro": 5_000_000}, {"inference_operation_cap": 99}, {"tariff_json": "{}"}, {"price_version": "x"}, {"policy_json": "{}"}):
            with self.assertRaises(StateError, msg=repr(fields)):
                self.store.update_job(**fields)
        job = reopened.job()
        self.assertEqual((job["cap_micro"], job["inference_operation_cap"], job["price_version"]), (1_000_000, 5, PRICE_VERSION))
        self.assertEqual(Budget(Store.open(self.dir / "job")).cap_micro, 1_000_000)

    def test_a_job_frozen_on_another_price_version_refuses_this_code(self):
        d = fresh_dir(self, "v")
        store = minimal_job(d / "job", tariff=dict(Tariff.frozen().to_dict(), price_version="gpt-6-astra-standard-2027-01-01"))
        self.addCleanup(store.close)
        with self.assertRaises(PricingError):
            Budget(store)


# =============================================================================================== two processes, one allowance
def _race_child(job_dir: str, barrier, out_path: str, max_output_tokens: int) -> None:
    """Spawned process: its own Store, waits at the barrier with its sibling, then tries to reserve."""
    from modeler.agentic.budget import Budget, BudgetExhausted
    from modeler.agentic.state import Store
    result = {"pid": os.getpid()}
    try:
        store = Store.open(Path(job_dir))
        try:
            budget = Budget(store)
            barrier.wait(timeout=60)
            try:
                row = budget.reserve(role="author", purpose="author", input_tokens=0, max_output_tokens=max_output_tokens)
                result.update(outcome="reserved", reservation=row["id"], reserved_micro=row["reserved_micro"])
            except BudgetExhausted as e:
                result.update(outcome="exhausted", error=str(e))
        finally:
            store.close()
    except Exception as e:  # noqa: BLE001 - reported to the parent, never swallowed
        result.update(outcome="error", error=f"{type(e).__name__}: {e}")
    Path(out_path).write_text(json.dumps(result), encoding="utf-8")


class ReservationRace(unittest.TestCase):
    def test_two_processes_exactly_one_wins_five_rounds(self):
        ctx = multiprocessing.get_context("spawn")
        for round_no in range(5):
            d = fresh_dir(self, f"race{round_no}-")
            minimal_job(d / "job", cap_usd="1", operation_cap=10).close()
            barrier = ctx.Barrier(2)
            outs = [d / f"child{i}.json" for i in range(2)]
            procs = [ctx.Process(target=_race_child, args=(str(d / "job"), barrier, str(outs[i]), 12_000)) for i in range(2)]   # 0.6 USD each; only one fits under 1.0
            for p in procs:
                p.start()
            for p in procs:
                p.join(timeout=120)
                if p.is_alive():
                    p.terminate()
                    self.fail(f"round {round_no}: child {p.pid} did not finish")
            results = []
            for p, o in zip(procs, outs):
                self.assertTrue(o.is_file(), f"round {round_no}: child exit code {p.exitcode} left no result file")
                results.append(json.loads(o.read_text(encoding="utf-8")))
            self.assertEqual(sorted(r["outcome"] for r in results), ["exhausted", "reserved"], f"round {round_no}: {results}")
            winner = next(r for r in results if r["outcome"] == "reserved")
            self.assertEqual(winner["reserved_micro"], 600_000)
            store = Store.open(d / "job")
            self.addCleanup(store.close)
            rows = store.reservations()
            self.assertEqual([(r["id"], r["state"], r["reserved_micro"]) for r in rows], [(winner["reservation"], "held", 600_000)])
            self.assertEqual(len(store.events("budget_reserved")), 1)
            self.assertEqual(store.requests(), [])
            self.assertEqual(Budget(store).totals()["remaining_micro"], 400_000)


# =============================================================================================== the runner's settlement discipline
class RunnerSettlement(unittest.TestCase):
    """The offline scripted Session exactly as the demo builds it (config.translate_request + build_policy + Session.create)."""

    def prepare(self, *, budget_usd="5"):
        rt = _rt()
        root = fresh_dir(self, "r")
        raw = rt.demo.demo_request(root / "in")
        translated = rt.config.translate_request(raw, root / "in")
        policy = rt.config.build_policy(owner_review=False, driver="scripted", worker="fake", budget_usd=budget_usd, max_inference_requests=12, max_output_tokens=4000, max_revisions=4,
                                        max_worker_seconds=120, wall_minutes=30, images_per_request=6, intake="synthetic", critic="scripted", final_evaluator="scripted", ar=False)
        sealed = rt.demo.sealed_pixel_hashes(raw["photos"])
        return SimpleNamespace(rt=rt, root=root, job_dir=root / "job", raw=raw, translated=translated, policy=policy, sealed=sealed)

    def create(self, prep, steps=None, *, transport=None, scenario=None):
        rt = prep.rt
        transport = transport if transport is not None else rt.responses.ScriptedTransport(list(steps))
        worker = rt.executor.FakeWorker(rt.demo.demo_fake_scenario() if scenario is None else scenario)
        session = rt.runner.Session.create(prep.job_dir, translated=prep.translated, policy=prep.policy, fingerprints={"protocol": "test"}, worker=worker,
                                           transport=transport, worker_config=None, log=_quiet)
        self.addCleanup(session.store.close)
        return session, transport

    def first_step(self, prep) -> dict:
        return prep.rt.demo.demo_script(prep.sealed)[0]

    def second_response(self, prep, name="list_evidence", args=None) -> dict:
        return prep.rt.demo._resp("call_2", name, {} if args is None else args, rs="rs_2")

    def reopen(self, prep, steps=()):
        """A second runner on the same job folder: what `resume` constructs."""
        rt = prep.rt
        store = Store.open(prep.job_dir)
        self.addCleanup(store.close)
        transport = rt.responses.ScriptedTransport(list(steps))
        return rt.runner.Session(store, transport=transport, worker=rt.executor.FakeWorker(), log=_quiet), transport, store

    def test_demo_settles_every_completed_request_from_its_usage_and_leaves_the_final_step(self):
        prep = self.prepare()
        steps = prep.rt.demo.demo_script(prep.sealed)
        session, transport = self.create(prep, steps)
        state = session.run()
        store = session.store
        job = store.job()
        self.assertEqual((state, job["state"], job["stop_reason"], job["deliverable_status"]), ("unresolved", "unresolved", "synthetic_demo_complete", "synthetic_only"))
        tariff = Tariff.from_dict(job["tariff"])
        requests = store.requests()
        # call_1 .. call_3b and call_4 (request_critic) are author requests; the critic's own call follows; then select and deliver
        self.assertEqual([r["role"] for r in requests], ["author"] * 5 + ["critic"] + ["author"] * 2)
        self.assertEqual({r["state"] for r in requests}, {"completed"})
        self.assertEqual([r["role"] for r in requests if r["role"] == "final"], [])
        max_out = {"author": 4000, "critic": min(4000, 8000)}
        for req in requests:
            res = store.reservation(req["reservation_id"])
            self.assertEqual((res["state"], res["request_id"], res["role"], res["purpose"]), ("settled", req["id"], req["role"], req["purpose"]), req["id"])
            self.assertEqual(res["settled_micro"], tariff.settle_micro(req["usage"]), req["id"])
            self.assertEqual(res["settled_micro"], DEMO_USAGE_MICRO)
            self.assertEqual(res["reserved_micro"], tariff.reserve_micro(req["input_token_count"], max_out[req["role"]]), req["id"])
            self.assertGreaterEqual(res["reserved_micro"], res["settled_micro"])
            self.assertEqual(res["usage"], validate_usage(req["usage"]))
        self.assertEqual({r["request_id"] for r in store.reservations()}, {r["id"] for r in requests})      # no orphan reservation
        self.assertEqual({r["state"] for r in store.reservations()}, {"settled"})
        t = Budget(store).totals()
        authors = sum(1 for r in requests if r["role"] == "author")
        critics = sum(1 for r in requests if r["role"] == "critic")
        self.assertEqual((authors, critics), (7, 1))
        self.assertEqual(t["operations_used"], authors + critics)
        self.assertEqual((t["settled_micro"], t["held_micro"], t["unknown_liability_micro"], t["remaining_micro"]), (8 * DEMO_USAGE_MICRO, 0, 0, 5_000_000 - 8 * DEMO_USAGE_MICRO))
        self.assertEqual(t["settled_usd"], "0.160400")
        self.assertEqual({r: d["operations"] for r, d in t["per_role"].items()}, {"author": 7, "critic": 1})
        # the final evaluator's scripted step was never consumed: a synthetic revision has nothing real to judge
        self.assertEqual(len(transport.steps), 1)
        self.assertEqual([i["name"] for i in transport.steps[0]["body"]["output"] if i["type"] == "function_call"], ["report_evaluation"])
        sent = [s["endpoint"] for s in transport.sent]
        self.assertEqual((sent.count("responses"), sent.count("count"), sent.count("compact")), (8, 8, 0))
        # reserve before send before settle, per request, in the event log
        events = store.events()
        by_kind = {}
        for e in events:
            by_kind.setdefault(e["kind"], []).append(e)
        for req in requests:
            reserved = [e["id"] for e in by_kind["budget_reserved"] if e["data"]["reservation"] == req["reservation_id"]]
            sent_ev = [e["id"] for e in by_kind["inference_sent"] if e["data"]["request"] == req["id"]]
            settled = [e["id"] for e in by_kind["budget_settled"] if e["data"]["reservation"] == req["reservation_id"]]
            self.assertEqual((len(reserved), len(sent_ev), len(settled)), (1, 1, 1), req["id"])
            self.assertLess(reserved[0], sent_ev[0])
            self.assertLess(sent_ev[0], settled[0])
        self.assertEqual(by_kind.get("settlement_problem", []), [])
        self.assertEqual(by_kind.get("budget_unknown", []), [])
        manifest = json.loads((prep.job_dir / "deliverable" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual((manifest["budget"]["settled_micro"], manifest["budget"]["operations_used"], manifest["state"]), (8 * DEMO_USAGE_MICRO, 8, "unresolved"))
        receipts = json.loads((prep.job_dir / "deliverable" / "receipts.json").read_text(encoding="utf-8"))
        self.assertEqual({r["state"] for r in receipts["reservations"]}, {"settled"})
        self.assertFalse((prep.job_dir / "deliverable" / "model.glb").exists())

    def test_http_429_settles_at_zero_consumes_the_slot_and_stops_the_job(self):
        prep = self.prepare()
        body = {"error": {"type": "rate_limit_exceeded", "message": "slow down", "code": "rate_limit_exceeded"}}
        session, transport = self.create(prep, [self.first_step(prep), {"endpoint": "responses", "status": 429, "expect": {"contains_call_output": "call_1"}, "body": body}])
        state = session.run()
        store = session.store
        job = store.job()
        self.assertEqual((state, job["stop_reason"]), ("needs_attention", "inference_failed"))
        q1, q2 = store.requests()
        self.assertEqual((q1["state"], q2["state"], q2["http_status"]), ("completed", "failed", 429))
        self.assertTrue(q2["error"].startswith("HTTP 429"))
        res = store.reservation(q2["reservation_id"])
        self.assertEqual((res["state"], res["settled_micro"], res["liability_micro"], res["request_id"]), ("settled", 0, 0, q2["id"]))
        self.assertTrue(res["reason"].startswith("http 429"))
        self.assertEqual(res["reserved_micro"], Tariff.frozen().reserve_micro(q2["input_token_count"], 4000))
        t = Budget(store).totals()
        self.assertEqual((t["operations_used"], t["settled_micro"], t["held_micro"], t["unknown_liability_micro"]), (2, DEMO_USAGE_MICRO, 0, 0))
        self.assertEqual((prep.job_dir / "host" / "requests" / q2["id"] / "response.json").read_bytes(), prep.rt.responses.canonical(body))
        self.assertEqual([e["data"]["request"] for e in store.events("inference_failed")], [q2["id"]])
        self.assertEqual(transport.steps, [])
        self.assertEqual(sum(1 for s in transport.sent if s["endpoint"] == "responses"), 2)

    def test_unknown_send_outcome_keeps_the_liability_and_a_resume_never_reposts(self):
        prep = self.prepare()
        session, transport = self.create(prep, [self.first_step(prep), {"endpoint": "responses", "raise": "unknown", "expect": {"contains_call_output": "call_1"}}])
        state = session.run()
        store = session.store
        self.assertEqual((state, store.job()["stop_reason"]), ("needs_attention", "inference_unknown"))
        q1, q2 = store.requests()
        self.assertEqual((q1["state"], q2["state"]), ("completed", "unknown"))
        self.assertIn("UnknownOutcome", q2["error"])
        res = store.reservation(q2["reservation_id"])
        self.assertEqual((res["state"], res["request_id"]), ("unknown", q2["id"]))
        self.assertGreater(res["reserved_micro"], 0)
        self.assertEqual(res["liability_micro"], res["reserved_micro"])
        t = Budget(store).totals()
        self.assertEqual((t["unknown_liability_micro"], t["held_micro"], t["settled_micro"], t["operations_used"]), (res["reserved_micro"], 0, DEMO_USAGE_MICRO, 2))
        self.assertEqual([e["data"]["request"] for e in store.events("inference_unknown")], [q2["id"]])
        posted_before = sum(1 for s in transport.sent if s["endpoint"] == "responses")
        self.assertEqual(posted_before, 2)
        # resume: a fresh runner with a transport full of answers posts nothing while the outcome is unknown
        session2, transport2, store2 = self.reopen(prep, prep.rt.demo.demo_script(prep.sealed))
        self.assertEqual(session2.run(), "needs_attention")
        self.assertEqual(transport2.sent, [])
        # even after the owner acknowledges the stop (resume --acknowledge-attention: a fresh Session, the transition, run),
        # reconciliation refuses to continue past an unknown request
        session3, transport3, store3 = self.reopen(prep, prep.rt.demo.demo_script(prep.sealed))
        session3.acquire()
        store3.transition("ready", "owner acknowledged the attention stop")
        self.assertEqual(session3.run(), "needs_attention")
        self.assertEqual(store2.job()["stop_reason"], "inference_unknown")
        self.assertEqual((transport2.sent, transport3.sent), ([], []))
        self.assertEqual(len(store2.requests()), 2)
        self.assertEqual(store2.request(q2["id"])["state"], "unknown")
        self.assertEqual(store2.reservation(q2["reservation_id"])["state"], "unknown")
        # the owner reconciles the liability from the dashboard
        Budget(store2).reconcile_unknown(q2["reservation_id"], authorized_by="owner: dashboard shows no charge", usage=None, no_charge=True)
        t2 = Budget(store2).totals()
        self.assertEqual((t2["unknown_liability_micro"], t2["settled_micro"], t2["operations_used"]), (0, DEMO_USAGE_MICRO, 2))

    def test_a_response_without_usage_is_an_unknown_liability_that_stops_the_next_send(self):
        prep = self.prepare()
        no_usage = self.second_response(prep)
        del no_usage["usage"]
        session, transport = self.create(prep, [self.first_step(prep), {"endpoint": "responses", "expect": {"contains_call_output": "call_1"}, "body": no_usage}])
        state = session.run()
        store = session.store
        # the usage-less response leaves its reservation an unknown liability (the request row itself reads 'completed'): the next paid
        # send is refused before anything is counted or reserved (no third request row) and the job stops for the owner's reconciliation
        self.assertEqual((state, store.job()["stop_reason"]), ("needs_attention", "inference_unknown"))
        q1, q2 = store.requests()
        self.assertEqual((q1["state"], q2["state"]), ("completed", "completed"))
        self.assertIsNone(q2.get("usage"))
        r2 = store.reservation(q2["reservation_id"])
        self.assertEqual((r2["state"], r2["liability_micro"], r2["settled_micro"]), ("unknown", r2["reserved_micro"], None))
        self.assertIn("usage", r2["reason"])
        t = Budget(store).totals()
        self.assertEqual((t["settled_micro"], t["unknown_liability_micro"], t["held_micro"], t["operations_used"]), (DEMO_USAGE_MICRO, r2["reserved_micro"], 0, 2))
        self.assertEqual([o["tool_name"] for o in store.operations()], ["list_evidence", "list_evidence"])     # the unbilled response was still applied
        self.assertEqual(sum(1 for s in transport.sent if s["endpoint"] == "responses"), 2, "a paid request was posted while a liability was unknown")
        self.assertEqual([e["data"]["requests"] for e in store.events("inference_refused_unknown_outstanding")], [[q2["id"]]])
        self.assertEqual(store.reservations(state="held"), [])

    def test_a_200_with_a_body_that_is_not_json_is_an_unknown_liability_and_a_failed_request(self):
        prep = self.prepare()
        rt = prep.rt
        raw = b"<html><body>502 Bad Gateway</body></html>"

        class RawBodyTransport(rt.responses.ScriptedTransport):
            """A scripted step {"raw": bytes} is returned verbatim (the base class can only serialise JSON bodies)."""

            def post(self, endpoint, payload, *, timeout=(15, 300)):
                if endpoint == rt.responses.ENDPOINT and self.steps and "raw" in self.steps[0]:
                    step = self.steps.pop(0)
                    self.sent.append({"endpoint": "responses", "payload": json.loads(rt.responses.canonical(payload))})
                    rt.responses.check_expectations(step.get("expect") or {}, payload)
                    return rt.responses.HttpResult(int(step.get("status", 200)), step["raw"], 0.0)
                return super().post(endpoint, payload, timeout=timeout)

        transport = RawBodyTransport([self.first_step(prep), {"endpoint": "responses", "status": 200, "raw": raw, "expect": {"contains_call_output": "call_1"}}])
        session, _ = self.create(prep, transport=transport)
        state = session.run()
        store = session.store
        self.assertEqual((state, store.job()["stop_reason"]), ("needs_attention", "inference_failed"))
        q1, q2 = store.requests()
        self.assertEqual((q1["state"], q2["state"], q2["http_status"]), ("completed", "failed", 200))
        self.assertTrue(q2["error"].startswith("unparsable response"), q2["error"])
        res = store.reservation(q2["reservation_id"])
        self.assertEqual((res["state"], res["liability_micro"]), ("unknown", res["reserved_micro"]))
        self.assertTrue(res["reason"].startswith("unparsable 200 body"), res["reason"])
        self.assertEqual((prep.job_dir / "host" / "requests" / q2["id"] / "response.json").read_bytes(), raw)
        t = Budget(store).totals()
        self.assertEqual((t["unknown_liability_micro"], t["settled_micro"], t["operations_used"]), (res["reserved_micro"], DEMO_USAGE_MICRO, 2))
        self.assertEqual(len(store.operations()), 1)          # nothing of the unparsable reply was applied

    def test_budget_exhausted_before_the_first_request_leaves_no_request_row(self):
        prep = self.prepare(budget_usd="0.01")                # 10_000 micro-USD; the first author request needs >= 4000 x 50 = 200_000
        session, transport = self.create(prep, prep.rt.demo.demo_script(prep.sealed))
        state = session.run()
        store = session.store
        job = store.job()
        self.assertEqual((state, job["state"], job["stop_reason"]), ("budget_exhausted", "budget_exhausted", "budget_exhausted"))
        self.assertEqual(store.requests(), [])
        self.assertEqual(store.reservations(), [])
        self.assertEqual([s["endpoint"] for s in transport.sent], ["count"])       # the free count happened; nothing paid was posted
        self.assertEqual(len(store.events("budget_exhausted")), 1)
        t = Budget(store).totals()
        self.assertEqual((t["cap_micro"], t["remaining_micro"], t["operations_used"]), (10_000, 10_000, 0))
        manifest = json.loads((prep.job_dir / "deliverable" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual((manifest["state"], manifest["stop_reason"], manifest["deliverable_status"], manifest["exit_semantics"]["budget_exhausted"]), ("budget_exhausted", "budget_exhausted", "none", 4))

    def test_resume_replays_a_4xx_response_whose_reservation_was_already_settled(self):
        prep = self.prepare()
        session, _ = self.create(prep, [])
        store, budget = session.store, session.budget
        rt = prep.rt
        # the state request_once leaves right before update_request(... state="failed") when the crash hits
        row = budget.reserve(role="author", purpose="author", input_tokens=100, max_output_tokens=4000)
        req = store.insert_request(role="author", purpose="author", epoch=store.epoch("author"), input_sha256="0" * 64, reservation_id=row["id"], input_token_count=100)
        budget.bind_request(row["id"], req["id"])
        rdir = prep.job_dir / "host" / "requests" / req["id"]
        rdir.mkdir(parents=True)
        body = rt.responses.canonical({"error": {"type": "rate_limit_exceeded", "message": "slow down"}})
        (rdir / "response.json").write_bytes(body)
        store.update_request(req["id"], state="sent", sent_utc=store.now(), response_path=str(rdir / "response.json"), response_sha256=rt.responses.sha256(body), http_status=429)
        store.transition("inference_pending", "author request", expected="ready")
        budget.settle_rejected(row["id"], request_id=req["id"], http_status=429, reason="slow down")      # committed; then the process died
        session.release()
        session2, transport2, store2 = self.reopen(prep)
        session2.acquire()
        session2.reconcile()                                                                            # must replay the durable 429 without raising
        self.assertEqual(store2.request(req["id"])["state"], "failed")
        self.assertEqual(store2.reservation(row["id"])["settled_micro"], 0)
        self.assertEqual(transport2.sent, [])


if __name__ == "__main__":
    unittest.main()
