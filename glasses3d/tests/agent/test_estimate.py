"""The pre-run cost estimate (glasses3d/plan/archive/20261019/WP2_DESIGN.md section 8, Estimate; section 13: leave-one-out median absolute
error <= 10%).

The section-8 example (C0 25,000, N 60) rounds up to $10.12, range $8.91-12.69, tail $2.47 at 8,192 and $2.88 at 16,384
output. The design prototype truncated its float arithmetic; this module rounds every token count
up, which is exactly 3,070 micro-USD more on the example (3,000 from 851 vs 850 output tokens on 60 requests, 70 from
the inputs rounded up) and 12 micro-USD more on the tail (one more written token); the test reproduces the prototype's
own numbers and that decomposition.

Data: ``data/studio-astra-13-tokens.json`` holds token counts only (no paths) of the 13 Blender Studio gpt-6-astra
ledgers, so the leave-one-out back-test always runs. ``test_fixture_equals_the_recorded_ledgers`` re-derives the
fixture from the ledgers themselves (automation/data/blender_agent in the repo and
lenses-recovery/automation-cleanup-20261003T071535Z/moved/data/blender_agent beside it; ``G3D_STUDIO_LEDGER_ROOTS``,
os.pathsep-separated, overrides) and FAILS under ``G3D_REQUIRE_DATA=1`` when they are missing. Nothing here touches
the network (conftest: offline, loopback only).
"""
from fractions import Fraction
import json
import os
from pathlib import Path
import re

import pytest

from glasses3d.agent import approvals as A
from glasses3d.agent import estimate as E
from glasses3d.agent.approvals import build_request, canonical_json
from glasses3d.agent.budget import BudgetExceeded, RunLedger
from glasses3d.agent.estimate import EstimateError, Parameters
from glasses3d.agent.pricing import Tariff, ceil_div, usd_text
from glasses3d.tests.agent.support import REPO_ROOT, require_data

T = Tariff.frozen()
FIXTURE = Path(__file__).resolve().parent / "data" / "studio-astra-13-tokens.json"
USAGE_COLUMNS = ("input_tokens", "cached_tokens", "cache_write_tokens", "output_tokens", "reasoning_tokens")
LEDGER_ROOTS = (REPO_ROOT / "automation" / "data" / "blender_agent",
                REPO_ROOT.parent / "lenses-recovery" / "automation-cleanup-20261003T071535Z" / "moved" / "data"
                / "blender_agent")
HEX32 = re.compile(r"[0-9a-f]{32}")

# Each run's settled total, re-priced from its token counts (= the recorded settled_micro_usd sums; the data test
# checks every row against its ledger).
SETTLED = {"20261001T064535-6a568a": 8_744_595, "20261001T085658-b197ec": 8_450_358,
           "20261001T144107-ad29ba": 14_261_424, "20261001T093641-76eea4": 8_639_393,
           "20261001T110933-78e442": 11_482_261, "20260930T155132-bcf7b6": 5_700_409,
           "20260930T164354-75a31d": 5_944_574, "20261002T061337-874b70": 3_054_455,
           "20261002T063216-aae94f": 6_140_239, "20261001T170018-eafbe1": 3_901_808,
           "20261001T172737-9ce098": 3_723_893, "20261002T154101-6062cd": 16_522_789,
           "20260930T125702-5b882b": 14_981_959}
# Leave-one-out errors in whole percent, the design prototype's table (section 8: range -33..+32%, 8% median abs).
LOO_PERCENT = {"20261001T064535-6a568a": 32, "20261001T085658-b197ec": 8, "20261001T144107-ad29ba": -33,
               "20261001T093641-76eea4": 2, "20261001T110933-78e442": 6, "20260930T155132-bcf7b6": 6,
               "20260930T164354-75a31d": 20, "20261002T061337-874b70": -17, "20261002T063216-aae94f": -19,
               "20261001T170018-eafbe1": 4, "20261001T172737-9ce098": 5, "20261002T154101-6062cd": 6,
               "20260930T125702-5b882b": 28}


def fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def fixture_usages(run: dict) -> list[dict]:
    return [dict(zip(USAGE_COLUMNS, row[2:])) for row in run["rows"]]


def fixture_shapes() -> list:
    return [E.run_shape(fixture_usages(run)) for run in fixture()["runs"]]


def prototype(c0: int, n: int, g: float, h: float, o: float) -> int:
    """The design prototype's estimate (scratch loop/estimator.py): floats truncated with int()."""
    total = 0
    for i in range(n):
        c = int(c0 + g * i)
        cached = int(c * h)
        total += T.settle_micro({"input_tokens": c, "output_tokens": int(o),
                                 "input_tokens_details": {"cached_tokens": cached, "cache_write_tokens": c - cached}})
    return total


# ---- the section-8 example ----


def test_section8_example_expected_and_range():
    result = E.estimate(25_000, 60, 8_192)
    assert result["expected_micro_usd"] == 10_110_546
    assert result["range_micro_usd"] == [8_901_109, 12_688_769]
    assert [E.cents_text(v) for v in (result["expected_micro_usd"], *result["range_micro_usd"])] == \
        ["$10.12", "$8.91", "$12.69"]
    assert result["expected_usd"] == "10.110546" and result["range_usd"] == ["8.901109", "12.688769"]
    assert result["text"] == ("Expected $10.12 (range $8.91-$12.69) for up to 60 Astra replies; the last $2.47 of any "
                              "cap cannot be spent.")
    assert result["parameters"] == {"growth_tokens_per_request": "129796/55", "cache_hit": "4436251/4567794",
                                    "output_tokens_per_request": "24662/29"}
    assert result["growth_band"] == {"p10": "51705/28", "p90": "200341/58"}
    # the output cap sizes the holds only, never the spend
    assert E.estimate(25_000, 60, 16_384)["range_micro_usd"] == result["range_micro_usd"]
    assert E.estimate(25_000, 60, 16_384)["expected_micro_usd"] == result["expected_micro_usd"]


def test_section8_tail_is_the_worst_case_hold_at_the_last_request():
    last = 25_000 + ceil_div(129_796 * 59, 55)
    assert last == 164_236
    for cap, micro, text in ((8_192, 2_462_550, "$2.47"), (16_384, 2_872_150, "$2.88")):
        result = E.estimate(25_000, 60, cap)
        assert result["last_request_input_tokens"] == last
        assert result["tail_micro_usd"] == micro == T.reserve_micro(last, cap)
        assert micro == ceil_div(last * 25, 2) + cap * 50             # all input at $12.5/M + the whole output cap
        assert E.cents_text(micro) == text
    assert E.estimate(25_000, 60, 16_384)["tail_high_micro_usd"] == T.reserve_micro(228_796, 16_384) == 3_679_150
    assert E.estimate(25_000, 60, 16_384)["first_long_context_request"] is None   # 164k: below the 272k step


def test_prototype_numbers_reproduced_and_the_difference_explained():
    g, h, o = 129_796 / 55, 4_436_251 / 4_567_794, 24_662 / 29
    low, high = 51_705 / 28, 200_341 / 58
    assert [prototype(25_000, 60, x, h, o) for x in (g, low, high)] == [10_107_476, 8_898_028, 12_685_699]
    assert T.reserve_micro(int(25_000 + g * 59), 8_192) == 2_462_538
    assert T.reserve_micro(int(25_000 + g * 59), 16_384) == 2_872_138
    ours = E.project(25_000, 60, 8_192)
    assert ours.spend_micro - 10_107_476 == 3_070
    # 3,000 of it: the output per request rounds up to 851 tokens (the prototype truncated to 850): 60 x 50 micro
    p = E.STUDIO_PARAMETERS
    at_850 = E.project(25_000, 60, 8_192, Parameters(growth=p.growth, cache_hit=p.cache_hit, output=850))
    assert ours.spend_micro - at_850.spend_micro == 60 * 50
    # the other 70: the input rounds up, one more token on 58 of 60 requests (i = 0 and 55 are exact)
    kinds = {}
    for i in range(60):
        c = int(25_000 + g * i)
        cached = int(c * h)
        usage = E.request_usage(25_000, i, at_850.parameters)
        key = (usage["input_tokens"] - c, usage["input_tokens_details"]["cached_tokens"] - cached,
               usage["input_tokens_details"]["cache_write_tokens"] - (c - cached),
               at_850.settled[i] - T.settle_micro({"input_tokens": c, "output_tokens": 850, "input_tokens_details": {
                   "cached_tokens": cached, "cache_write_tokens": c - cached}}))
        kinds[key] = kinds.get(key, 0) + 1
    # (more input, more cached, more written, more micro-USD): 57 x one cached token (1 micro), 1 x one written (13)
    assert kinds == {(0, 0, 0, 0): 2, (1, 1, 0, 1): 57, (1, 0, 1, 13): 1}
    assert at_850.spend_micro - 10_107_476 == 57 * 1 + 13 == 70
    # the tail: one more written token at $12.5/M, 12 micro after the rounding up
    assert E.estimate(25_000, 60, 8_192)["tail_micro_usd"] - 2_462_538 == 12


def test_required_cap_admits_every_modelled_request_through_the_real_run_ledger(tmp_path):
    result = E.estimate(25_000, 60, 16_384)
    assert result["required_cap_micro_usd"] == 12_721_515
    assert result["required_cap_range_micro_usd"] == [11_173_817, 16_020_799]
    assert E.estimate(25_000, 60, 8_192)["required_cap_micro_usd"] == 12_311_915
    # the suggested cap: the expected run's required cap up to the next $5 (the design's "Suggested $15")
    assert result["suggested_cap_micro_usd"] == 15_000_000 and result["suggested_cap_usd"] == "15.000000"
    assert E.suggested_cap_micro(15_000_000) == 15_000_000 and E.suggested_cap_micro(15_000_001) == 20_000_000
    projection = E.project(25_000, 60, 16_384)

    def drive(cap_micro: int, name: str) -> int:
        ledger = RunLedger.create(tmp_path / name / "budget.json", usd_text(cap_micro))
        for i in range(60):
            usage = E.request_usage(25_000, i, E.STUDIO_PARAMETERS)
            try:
                row = ledger.reserve(usage["input_tokens"], 16_384)
            except BudgetExceeded:
                return i
            assert row["reserved_micro_usd"] == projection.holds[i]
            ledger.settle(row["sequence"], usage, response_id=f"resp_{i:02d}", request_id=None)
        assert ledger.totals()["settled_micro_usd"] == projection.spend_micro == 10_110_546
        return 60

    assert drive(projection.required_cap_micro, "exact") == 60
    assert drive(projection.required_cap_micro - 1, "short") == 59      # the last request no longer fits


# ---- the mechanics ----


def test_every_token_count_rounds_up_in_cost():
    parameters = Parameters(growth=Fraction(1, 3), cache_hit=Fraction(2, 3), output=Fraction(7, 2))
    usage = E.request_usage(10, 1, parameters)
    assert usage == {"input_tokens": 11, "output_tokens": 4, "total_tokens": 15,
                     "input_tokens_details": {"cached_tokens": 7, "cache_write_tokens": 4},
                     "output_tokens_details": {"reasoning_tokens": 0}}
    assert [E.request_input_tokens(10, i, Fraction(1, 3)) for i in range(5)] == [10, 11, 11, 11, 12]
    projection = E.project(10, 5, 100, parameters)
    assert projection.settled == tuple(T.settle_micro(E.request_usage(10, i, parameters)) for i in range(5))
    assert all(type(v) is int for v in projection.settled + projection.holds + projection.inputs)


def test_initial_context_tokens_is_chars_over_four_rounded_up_plus_1500_per_image():
    assert [E.initial_context_tokens(c, 0) for c in (0, 1, 4, 5)] == [0, 1, 1, 2]
    assert E.initial_context_tokens(42_000, 1) == 12_000
    assert E.initial_context_tokens(60_001, 6) == 15_001 + 9_000
    for bad in ((True, 0), (-1, 0), (4.0, 0), (4, -1), (4, None)):
        with pytest.raises(EstimateError):
            E.initial_context_tokens(*bad)


def test_the_272k_step_prices_each_request_on_its_own_side():
    parameters = Parameters(growth=2_360, cache_hit=Fraction(97, 100), output=850)
    projection = E.project(260_000, 10, 8_192, parameters)
    assert projection.inputs == tuple(260_000 + 2_360 * i for i in range(10))
    assert projection.first_long_context_request == 7                   # 274,160 > 272,000; 271,800 is not

    def by_hand(c: int) -> int:
        long = c > 272_000
        written = ceil_div(c * 3, 100)
        cached = c - written
        return (ceil_div(cached * 1_000_000 * (4 if long else 2), 2_000_000)
                + ceil_div(written * 12_500_000 * (4 if long else 2), 2_000_000)
                + ceil_div(850 * 50_000_000 * (3 if long else 2), 2_000_000))

    assert projection.settled == tuple(by_hand(c) for c in projection.inputs)
    assert projection.settled[6] > 1.9 * projection.settled[5]          # x2 input/cache, x1.5 output
    assert projection.holds[6] == ceil_div(274_160 * 12_500_000 * 4, 2_000_000) + ceil_div(
        8_192 * 50_000_000 * 3, 2_000_000)


def test_inputs_that_cannot_be_estimated_are_refused():
    with pytest.raises(EstimateError, match="input"):
        E.estimate(900_000, 20, 8_192)                                  # beyond the model's 922k input limit
    with pytest.raises(EstimateError, match="lower max_turns"):
        E.estimate(25_000, 300, 8_192)                                  # the p90 path outgrows it
    E.project(25_000, 300, 8_192)                                       # the expected path alone still fits
    for args in ((25_000, 0, 8_192), (25_000, 60, 0), (25_000, 60, 128_001), (-1, 60, 8_192), (True, 60, 8_192),
                 (25_000.0, 60, 8_192)):
        with pytest.raises(EstimateError):
            E.estimate(*args)
    with pytest.raises(EstimateError):
        Parameters(growth=2_360, cache_hit=Fraction(3, 2), output=850)
    with pytest.raises(EstimateError):
        Parameters(growth=2_360.5, cache_hit=Fraction(1, 2), output=850)  # floats are never money inputs
    with pytest.raises(EstimateError):
        Parameters(growth=-1, cache_hit=Fraction(1, 2), output=850)
    with pytest.raises(EstimateError, match="band"):
        E.estimate(25_000, 60, 8_192, growth_band=(Fraction(1), Fraction(2)))
    with pytest.raises(EstimateError):
        E.estimate(25_000, 60, 8_192, growth_band=(1_846.6, 3_454.2))


def test_the_estimate_is_plain_json_an_approval_request_accepts(tmp_path):
    result = E.estimate(25_000, 60, 16_384)
    assert json.loads(canonical_json(result)) == result
    job = tmp_path / "job"
    (job / "inputs").mkdir(parents=True)
    (job / "job.json").write_text("{}", encoding="utf-8")
    request = build_request(job, instructions_sha256="a" * 64, tool_registry_sha256="b" * 64,
                            settings={"effort": "max", "max_output_tokens": 16_384, "max_turns": 60})
    document = A.write_approval_request(job / "runs" / "r1", job_id="job", request=request, estimate=result,
                                        suggested_cap_usd=result["suggested_cap_usd"])
    assert document["estimate"] == result and document["suggested_cap_usd"] == "15.000000"


# ---- the smoke settings (WP8 step 0) ----


def test_smoke_settings_spend_cents_and_hold_under_forty_cents():
    assert E.SMOKE_SETTINGS == {"images": 1, "max_output_tokens": 4_096, "max_turns": 2, "cap_usd": "1.00"}
    smoke = E.smoke_estimate(42_000)                                    # 10,500 text tokens + 1 image = 12,000
    assert smoke["initial_context_tokens"] == 12_000 and smoke["requests"] == 2
    assert smoke["max_output_tokens"] == 4_096
    assert smoke["cap_micro_usd"] == smoke["suggested_cap_micro_usd"] == 1_000_000
    assert smoke["first_hold_micro_usd"] == 12_000 * 25 // 2 + 4_096 * 50 == 354_800
    # the design's "~$0.36" is the first hold from 12,016 context tokens on; at exactly 12,000 it is $0.35
    assert T.reserve_micro(12_015, 4_096) == 354_988 and T.reserve_micro(12_016, 4_096) == 355_000
    assert smoke["largest_hold_micro_usd"] == 397_988 <= 400_000         # "hold: up to $0.40"
    assert smoke["expected_micro_usd"] == 120_200
    assert max(smoke["required_cap_range_micro_usd"]) == 456_517 <= 1_000_000
    # even with the first request a full cache miss (as every recorded first request was) the spend is in range
    cold = T.settle_micro({"input_tokens": 12_000, "output_tokens": 851, "input_tokens_details": {
        "cached_tokens": 0, "cache_write_tokens": 12_000}}) + E.project(12_000, 2, 4_096).settled[1]
    assert 100_000 <= smoke["expected_micro_usd"] < cold <= 300_000      # "spend ~$0.10-0.30"
    assert smoke["text"] == "Smoke turn: spend about $0.13; hold up to $0.40 of the $1.00 cap."
    with pytest.raises(EstimateError, match="smoke cap"):
        E.smoke_estimate(4 * 70_000)


# ---- the corpus and the leave-one-out back-test ----


def test_the_fixture_gives_the_module_medians_and_band():
    data = fixture()
    assert data["schema"] == "g3d.test.studio_corpus_tokens/1"
    assert [run["run"] for run in data["runs"]] == list(SETTLED)
    shapes = fixture_shapes()
    assert len(shapes) == 13 and sum(s.requests for s in shapes) == 655
    assert all(s.requests >= E.MIN_CORPUS_REQUESTS for s in shapes)
    assert {run["run"]: s.settled_micro for run, s in zip(data["runs"], shapes)} == SETTLED
    assert E.corpus_parameters(shapes) == E.STUDIO_PARAMETERS
    assert E.corpus_growth_band(shapes) == E.STUDIO_GROWTH_BAND
    growths = sorted(s.parameters.growth for s in shapes)
    assert (growths[1], growths[6], growths[11]) == (E.STUDIO_GROWTH_BAND[0], E.STUDIO_PARAMETERS.growth,
                                                     E.STUDIO_GROWTH_BAND[1])
    p = E.STUDIO_PARAMETERS                                             # the design's 2,360 tokens/turn, 0.971, 850
    assert (round(p.growth), round(float(p.cache_hit), 3), round(p.output)) == (2_360, 0.971, 850)


def test_leave_one_out_median_absolute_error_is_within_ten_percent():
    runs = fixture()["runs"]
    tests = E.leave_one_out(fixture_shapes())
    errors = {run["run"]: test.error for run, test in zip(runs, tests)}
    mae = E.median_absolute_error(tests)
    assert mae <= Fraction(1, 10)
    assert mae == Fraction(49_525, 603_597)                             # 8.20%: Oakley run b197ec
    assert {run: round(100 * float(error)) for run, error in errors.items()} == LOO_PERCENT
    assert min(errors.values()) == errors["20261001T144107-ad29ba"]     # Prada, underestimated
    assert (round(100 * float(min(errors.values())), 2), round(100 * float(max(errors.values())), 2)) == (-32.6, 32.24)
    for test in tests:                                                  # each estimate is the module's own sum
        assert test.estimate_micro == E.project(test.shape.initial_tokens, test.shape.requests, 1,
                                                test.parameters).spend_micro


def test_nearest_rank_percentiles():
    values = list(range(1, 14))
    assert [E.nearest_rank(values, p) for p in (10, 50, 90, 100)] == [2, 7, 12, 13]
    for bad in (0, 101, 10.0):
        with pytest.raises(EstimateError):
            E.nearest_rank(values, bad)
    with pytest.raises(EstimateError):
        E.nearest_rank([], 10)


def test_ledger_usages_reads_a_glasses3d_run_ledger(tmp_path):
    ledger = RunLedger.create(tmp_path / "budget.json", "5")
    blocks = [E.request_usage(20_000, i, E.STUDIO_PARAMETERS) for i in range(4)]
    row = ledger.reserve(20_000, 8_192)
    ledger.settle(row["sequence"], blocks[0], response_id="resp_1", request_id="req_1")
    row = ledger.reserve(blocks[1]["input_tokens"], 8_192)
    ledger.reject_4xx(row["sequence"], http_status=429, request_id="req_2", reason="rate limited")
    row = ledger.reserve(blocks[1]["input_tokens"], 8_192)
    ledger.mark_unknown(row["sequence"], reason="connection dropped")
    ledger.reconcile_unknown(row["sequence"], "The dashboard shows this usage.", blocks[1])
    row = ledger.reserve(blocks[2]["input_tokens"], 8_192)
    ledger.settle(row["sequence"], blocks[2], response_id="resp_4", request_id="req_4")
    row = ledger.reserve(blocks[3]["input_tokens"], 8_192)              # still held: not a settled usage
    document = json.loads((tmp_path / "budget.json").read_text(encoding="utf-8"))
    usages = E.ledger_usages(document)
    assert [u["input_tokens"] for u in usages] == [b["input_tokens"] for b in blocks[:3]]
    shape = E.run_shape(usages)
    assert shape.settled_micro == ledger.totals()["settled_micro_usd"] == sum(T.settle_micro(b) for b in blocks[:3])
    assert shape.parameters.growth == Fraction(blocks[2]["input_tokens"] - 20_000, 2)


def test_studio_ledger_format_and_malformed_ledgers():
    studio = {"schema": "blender_agent_budget_v2", "reservations": [
        {"sequence": 1, "usage": {"input_tokens": 100, "cached_tokens": 0, "cache_write_tokens": 99,
                                  "output_tokens": 5, "reasoning_tokens": 0}},
        {"sequence": 2, "usage": None},
        {"sequence": 3, "usage": {"input_tokens": 150, "cached_tokens": 99, "cache_write_tokens": 50,
                                  "output_tokens": 7, "reasoning_tokens": 2}}]}
    usages = E.ledger_usages(studio)
    assert [u["input_tokens"] for u in usages] == [100, 150]
    shape = E.run_shape(usages)
    assert shape.parameters == Parameters(growth=50, cache_hit=Fraction(99, 250), output=6)
    with pytest.raises(EstimateError, match="schema"):
        E.ledger_usages({"schema": "claude_budget_v1", "reservations": []})
    with pytest.raises(EstimateError, match="flat"):
        E.ledger_usages({"schema": "blender_agent_budget_v2", "reservations": [{"usage": {"input_tokens": "1"}}]})
    with pytest.raises(EstimateError):
        E.run_shape(usages[:1])
    with pytest.raises(EstimateError):
        E.run_shape([dict(usages[0], cached_tokens=100), usages[1]])   # cached + written > input


# ---- the recorded ledgers themselves ----


def ledger_roots() -> list[Path]:
    override = os.environ.get("G3D_STUDIO_LEDGER_ROOTS")
    return [Path(p) for p in override.split(os.pathsep) if p] if override else list(LEDGER_ROOTS)


def discover(roots: list[Path]) -> dict:
    """The selection rule of the corpus: blender_agent_budget_v2, a run-config.json naming gpt-6-astra, >= 15
    settled requests."""
    found = {}
    for root in roots:
        for path in sorted(root.rglob("runs/*/budget.json")):
            document = json.loads(path.read_text(encoding="utf-8"))
            config = path.parent / "run-config.json"
            if document.get("schema") != "blender_agent_budget_v2" or not config.is_file():
                continue
            if json.loads(config.read_text(encoding="utf-8")).get("model") != "gpt-6-astra":
                continue
            rows = [row for row in document["reservations"] if row.get("usage")]
            if len(rows) < E.MIN_CORPUS_REQUESTS:
                continue
            job = path.parents[3].name
            entry = {"job": job[:8] if HEX32.fullmatch(job) else job, "run": path.parent.name,
                     "unsettled_rows": len(document["reservations"]) - len(rows),
                     "rows": [[row["input_tokens"], row["max_output_tokens"]] + [row["usage"][k] for k in USAGE_COLUMNS]
                              for row in rows],
                     "recorded": [row["settled_micro_usd"] for row in rows], "usages": E.ledger_usages(document)}
            assert found.setdefault(entry["run"], entry)["rows"] == entry["rows"], f"two ledgers for {entry['run']}"
    return found


def test_fixture_equals_the_recorded_ledgers():
    roots = ledger_roots()
    missing = [root for root in roots if not root.is_dir()]
    if missing:
        require_data(None, f"the Blender Studio ledgers ({len(missing)} of {len(roots)} roots missing)")
    found = discover(roots)
    data = fixture()
    assert sorted(found) == sorted(run["run"] for run in data["runs"])
    for run in data["runs"]:
        entry = found[run["run"]]
        assert {k: entry[k] for k in ("job", "run", "unsettled_rows", "rows")} == run
        assert [T.settle_micro(E.provider_usage(u)) for u in entry["usages"]] == entry["recorded"]
        assert E.run_shape(entry["usages"]).settled_micro == sum(entry["recorded"]) == SETTLED[run["run"]]
    # the back-test straight from the ledgers: the same 13 shapes, the same median absolute error
    shapes = [E.run_shape(found[run["run"]]["usages"]) for run in data["runs"]]
    assert shapes == fixture_shapes()
    assert E.median_absolute_error(E.leave_one_out(shapes)) == Fraction(49_525, 603_597) <= Fraction(1, 10)
    assert E.corpus_parameters(shapes) == E.STUDIO_PARAMETERS and E.corpus_growth_band(shapes) == E.STUDIO_GROWTH_BAND


def test_the_estimator_is_stdlib_plus_pricing_only():
    """$0 by construction: no client, transport or file access can be reached from the estimate."""
    import ast
    tree = ast.parse(Path(E.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add("." * node.level + (node.module or ""))
    assert imported == {"__future__", "dataclasses", "fractions", "statistics", "typing", ".pricing"}
    assert "open(" not in Path(E.__file__).read_text(encoding="utf-8")
