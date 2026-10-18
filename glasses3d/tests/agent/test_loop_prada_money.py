"""Criterion 3 (Proof A money) through the whole loop: the 59 recorded Prada usages driven by ``run_astra``.

test_money_replay.py proves the arithmetic through the client and the hooks; this runs the same 59 requests through the
real Runner, the loop's construction, ReviewLog (archive + settle) and the on_llm_end cross-check: 58 turns that call
a read-only tool and a final message, each count answering the recorded input and each response carrying the recorded
usage verbatim. Request 1 must reserve 719,438 micro-USD and the settlements must total 14,261,424, exactly.
"""
import asyncio
import json

import pytest

pytest.importorskip("agents")
from agents import function_tool  # noqa: E402

from glasses3d.agent import loop as L  # noqa: E402
from glasses3d.agent import replay as R  # noqa: E402
from glasses3d.agent.budget import RunLedger  # noqa: E402
from glasses3d.tests.agent.loop_support import FakeScene, ledger_rows, make_job, occurrences, spec_for  # noqa: E402
from glasses3d.tests.agent.support import OFFLINE_KEY  # noqa: E402
from glasses3d.tests.agent.test_money_replay import (PEAK_COMMITTED, ROW1_RESERVED, SETTLED_TOTAL,  # noqa: E402
                                                     replay_rows)

RUN = "20261001T144107-loop01"


@function_tool
def budget_status() -> str:
    """Read the run's money state (numbers only)."""
    return json.dumps({"status": "ok"})


def test_the_59_recorded_prada_usages_settle_exactly_through_the_loop(tmp_path, monkeypatch):
    monkeypatch.setattr(L, "ProductionClients", None)          # never constructed: calling None would fail
    # Replay the frozen original conversation's usages verbatim: inserting a
    # summary/new prefix would make later recorded counts and charges inapplicable.
    # Disable only renewal in this historical settlement test; current renewal
    # plus money is tested in test_conversation_renewal and the desktop real-loop
    # estimator tests (reserve and settled-only). All guard/ledger checks stay live.
    monkeypatch.setattr(L.ConversationRenewal, "check", lambda self, *args: None)
    rows = replay_rows()
    replies = [R.tool_call("budget_status", {}, counted=row["counted"], usage=row["usage"]) for row in rows[:-1]]
    replies.append(R.message("Delivered.", counted=rows[-1]["counted"], usage=rows[-1]["usage"]))
    settings = L.RunSettings(effort="max", max_output_tokens=8192, max_turns=60)
    job, sha = make_job(tmp_path, tools=[budget_status], run_id=RUN, cap="18", settings=settings,
                        name="prada-loop-8c82d986")
    transport = R.ScriptedOpenAI(replies)
    result = asyncio.run(L.run_astra(spec_for(job, RUN, sha, [budget_status], transport, settings=settings),
                                     scene=FakeScene()))
    assert result["status"] == "completed" and result["final_output"] == "Delivered."
    assert result["renewals"] == []
    ledger = ledger_rows(job, RUN)
    assert len(ledger) == len(transport.counts) == len(transport.creates) == 59 == result["turns_used"]
    assert all((row["outcome"], row["settled_from"], row["http_status"]) == ("settled", "transport", 200)
               for row in ledger)
    assert ledger[0]["reserved_micro_usd"] == ROW1_RESERVED
    total = sum(row["settled_micro_usd"] for row in ledger)
    assert total == SETTLED_TOTAL, f"off by {total - SETTLED_TOTAL} micro-USD"
    peak, settled = 0, 0
    for row in ledger:
        peak = max(peak, settled + row["reserved_micro_usd"])
        settled += row["settled_micro_usd"]
    assert peak == PEAK_COMMITTED <= 18_000_000
    if "reserved" in rows[0]:          # the recorded ledger is present: every row equals the recording
        assert [row["reserved_micro_usd"] for row in ledger] == [row["reserved"] for row in rows]
        assert [row["settled_micro_usd"] for row in ledger] == [row["settled"] for row in rows]
    RunLedger.load(job / "runs" / RUN / "budget.json")          # re-priced and tamper-checked
    assert len(list((job / "runs" / RUN / "transport").iterdir())) == 2 * 59
    assert result["budget"]["settled_micro_usd"] == SETTLED_TOTAL and result["budget"]["unknown_micro_usd"] == 0
    assert transport.violations == [] and transport.unexpected == []
    assert all(body["max_output_tokens"] == 8192 for body in transport.creates)
    assert occurrences(job, OFFLINE_KEY.encode()) == [] and occurrences(job, b"data:image") == []
