"""Randomized but deterministic event sequences through BudgetGuard (fixed seeds): after every step the on-disk
ledger re-validates (re-priced, tamper-checked) and equals memory, committed money never exceeds the cap unless a
violation is recorded, at most one request is open and it is the last row, nothing is reserved after an unknown
outcome or a violation, and a refused request is never counted twice."""
import asyncio
import json
import random
from types import SimpleNamespace

import httpx2
import pytest

from glasses3d.agent.budget import BudgetExceeded, BudgetGuard, BudgetGuardError, RunLedger, ReconcileError
from glasses3d.agent.pricing import Tariff
from glasses3d.tests.agent.support import fake_counter, provider_body, request

T = Tariff.frozen()
OUTCOMES = ("settle",) * 6 + ("incomplete",) * 2 + ("4xx",) * 2 + ("5xx", "unusable", "drop", "open", "over")


def usage_for(rng, counted, max_output, *, over=False):
    cached = rng.randint(0, counted)
    written = rng.randint(0, counted - cached)
    output = rng.randint(max_output + 1, max_output + 50_000) if over else rng.randint(0, max_output)
    return {"input_tokens": counted, "output_tokens": output, "total_tokens": counted + output,
            "input_tokens_details": {"cached_tokens": cached, "cache_write_tokens": written},
            "output_tokens_details": {"reasoning_tokens": rng.randint(0, output)}}


def check_invariants(guard, path):
    disk = RunLedger.load(path)                       # re-priced and tamper-checked every step
    assert disk.rows == guard.ledger.rows
    totals = disk.totals()
    if disk.violation is None:
        assert totals["committed_micro_usd"] <= disk.maximum_micro
    held = [row for row in disk.rows if row["outcome"] == "held"]
    assert len(held) <= 1 and (not held or disk.rows[-1]["outcome"] == "held")
    for row in disk.rows:
        if row["outcome"] in ("settled", "rejected_4xx", "reconciled"):
            assert type(row["settled_micro_usd"]) is int and row["settled_micro_usd"] >= 0
        else:
            assert row["settled_micro_usd"] is None


@pytest.mark.parametrize("seed", range(40))
def test_random_event_sequences_keep_the_money_invariants(tmp_path, seed):
    rng = random.Random(seed)
    client, count = fake_counter()
    cap = rng.choice(["0.5", "2", "5", "18"])
    path = tmp_path / "budget.json"
    guard = BudgetGuard(client, path, cap)
    stopped = False                                   # an unknown outcome or a violation ends spending

    async def flow():
        nonlocal stopped
        for step in range(rng.randint(5, 40)):
            counted = rng.randint(1, 300_000) if rng.random() < .25 else rng.randint(1, 40_000)
            max_output = rng.choice([1, 100, 4096, 8192, 16_384])
            count.return_value = SimpleNamespace(input_tokens=counted)
            open_before = guard.ledger.open_row()
            calls_before = count.call_count
            rows_before = len(guard.ledger.rows)
            try:
                await guard.before_request(request(max_output_tokens=max_output))
            except BudgetExceeded:
                assert not stopped and open_before is None
                assert len(guard.ledger.rows) == rows_before
                check_invariants(guard, path)
                continue
            except BudgetGuardError:
                assert stopped or open_before is not None
                assert count.call_count == calls_before            # refused before counting
                assert len(guard.ledger.rows) == rows_before
                if open_before is not None and not stopped and rng.random() < .7:
                    await guard.transport_failed(httpx2.ConnectError("dropped"))
                    stopped = True
                elif stopped and rng.random() < .5:
                    unknown = [row for row in guard.ledger.rows if row["outcome"] == "unknown"]
                    if unknown:             # a live owner reconcile keeps the invariants and never unblocks the run
                        assert await guard.reconcile_unknown(unknown[0]["sequence"], "Owner: nothing was charged",
                                                             no_charge=True) == 0
                check_invariants(guard, path)
                continue
            assert not stopped and open_before is None
            row = guard.ledger.rows[-1]
            assert (row["outcome"], row["input_tokens"], row["reserved_micro_usd"]) == (
                "held", counted, T.reserve_micro(counted, max_output))
            outcome = rng.choice(OUTCOMES)
            sequence = row["sequence"]
            if outcome in ("settle", "incomplete", "over"):
                block = usage_for(rng, counted, max_output, over=outcome == "over")
                answer = httpx2.Response(200, json=provider_body(
                    response_id=f"resp_{seed}_{sequence}", usage_block=block,
                    status="incomplete" if outcome == "incomplete" else "completed"))
                expected = T.settle_micro(block)
                try:
                    settled = await guard.after_response(answer)
                    assert settled["settled_micro_usd"] == expected <= row["reserved_micro_usd"]
                except BudgetGuardError:
                    assert expected > row["reserved_micro_usd"]       # only a cost above the worst case raises
                    stopped = True
            elif outcome == "4xx":
                settled = await guard.after_response(httpx2.Response(rng.choice([400, 401, 403, 429]), json={}))
                assert settled["outcome"] == "rejected_4xx" and settled["settled_micro_usd"] == 0
            elif outcome == "5xx":
                settled = await guard.after_response(httpx2.Response(rng.choice([500, 502, 503, 520]), content=b""))
                assert settled["outcome"] == "unknown"
                stopped = True
            elif outcome == "unusable":
                with pytest.raises(BudgetGuardError):
                    await guard.after_response(httpx2.Response(200, json=provider_body(status="failed")))
                stopped = True
            elif outcome == "drop":
                assert (await guard.transport_failed(httpx2.ReadTimeout("timeout")))["outcome"] == "unknown"
                stopped = True
            check_invariants(guard, path)

    asyncio.run(flow())
    check_invariants(guard, path)
    disk = RunLedger.load(path)
    settled_rows = [row for row in disk.rows if row["outcome"] in ("settled", "rejected_4xx", "reconciled")]
    assert disk.totals()["settled_micro_usd"] == sum(row["settled_micro_usd"] for row in settled_rows)
    # an owner reconcile of every unknown hold (closed run) keeps the ledger valid
    closed = RunLedger.load(path)
    for row in list(closed.rows):
        if row["outcome"] in ("unknown", "held"):
            if row["sequence"] % 2:
                closed.reconcile_unknown(row["sequence"], "Owner: nothing was charged", no_charge=True)
            else:
                block = usage_for(rng, row["input_tokens"], row["max_output_tokens"])
                assert closed.reconcile_unknown(row["sequence"], "Owner: dashboard usage", block) == T.settle_micro(block)
            with pytest.raises(ReconcileError):
                closed.reconcile_unknown(row["sequence"], "Owner: again", no_charge=True)
    final = RunLedger.load(path)
    assert final.totals()["unknown_micro_usd"] == final.totals()["held_micro_usd"] == 0
    json.dumps(final.data)
