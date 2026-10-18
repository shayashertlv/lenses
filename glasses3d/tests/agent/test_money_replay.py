"""The stage-C stop rule (plan/WP2_DESIGN.md sections 14 and 15; Proof A criterion 3, money part).

The 59 recorded usages of the Blender Studio Prada session (job 8c82d986, its run budget.json, an $18 cap) go through
the real AsyncOpenAI client, the real httpx2 event hooks and BudgetGuard exactly as the transport would deliver them:
the count endpoint answers each recorded input count, each response carries the recorded usage verbatim, the guard
reserves before dispatch and settles every 200 from its own usage, and on_llm_end cross-checks an SDK-built
ModelResponse. Request 1 must reserve 719,438 micro-USD and the settlements must total 14,261,424 micro-USD, exactly.

Data: the recorded ledger is read from its location (ignored by git; ``G3D_PRADA_BUDGET`` overrides the path). The
committed fixture ``data/prada-8c82-tokens.json`` holds token counts only, so the replay also runs where the data is
absent; under ``G3D_REQUIRE_DATA=1`` a missing recorded ledger FAILS the data tests instead of skipping them.
"""
import asyncio
import json
import os
from pathlib import Path
import sys

import httpx2
import pytest

import agents  # Required pinned SDK: import failures must fail this suite.
from agents.items import ModelResponse  # noqa: E402
from agents.usage import Usage  # noqa: E402

from glasses3d.agent import approvals as A  # noqa: E402
from glasses3d.agent.approvals import build_request, request_sha256  # noqa: E402
from glasses3d.agent.budget import BudgetGuard, JobLedger, RunLedger, provider_usage  # noqa: E402
from glasses3d.agent.offline import offline_counting_client, offline_inference_client  # noqa: E402
from glasses3d.agent.pricing import Tariff  # noqa: E402
from glasses3d.tests.agent.support import OFFLINE_KEY, REPO_ROOT, require_data  # noqa: E402

PRADA_LEDGER = ("automation/data/blender_agent/studio/jobs/8c82d986e6c14d26b4b38f3807712ea0/agent/runs/"
                "20261001T144107-ad29ba/budget.json")
FIXTURE = Path(__file__).resolve().parent / "data" / "prada-8c82-tokens.json"
ROW1_RESERVED = 719_438                 # WP2_DESIGN section 14, criterion 3
SETTLED_TOTAL = 14_261_424              # the recorded Prada total ($14.261424)
PEAK_COMMITTED = 17_164_748             # settled so far + the open hold, at its highest (request 59)
CAP_USD = "18"
SDK_FIELDS = {"include", "input", "instructions", "max_output_tokens", "model", "parallel_tool_calls",
              "prompt_cache_key", "reasoning", "service_tier", "tools"}
TOOL = {"type": "function", "name": "execute_blender_code", "description": "Run bpy code in the live scene.",
        "parameters": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"],
                       "additionalProperties": False}, "strict": True}
run = asyncio.run


def recorded_ledger_path() -> Path | None:
    override = os.environ.get("G3D_PRADA_BUDGET")
    for candidate in ([Path(override)] if override else []) + [REPO_ROOT / PRADA_LEDGER]:
        if candidate.is_file():
            return candidate
    return None


def fixture_rows() -> list[dict]:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return [{"counted": row[0], "max_output": row[1],
             "usage": provider_usage(dict(zip(("input_tokens", "cached_tokens", "cache_write_tokens",
                                               "output_tokens", "reasoning_tokens"), row[2:])))}
            for row in data["rows"]]


def recorded_rows(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [{"counted": row["input_tokens"], "max_output": row["max_output_tokens"],
             "usage": provider_usage(row["usage"]), "reserved": row["reserved_micro_usd"],
             "settled": row["settled_micro_usd"]} for row in data["reservations"]]


def replay_rows() -> list[dict]:
    """The recorded ledger when present (its amounts are then checked per row), else the token-count fixture."""
    path = recorded_ledger_path()
    if path is None and os.environ.get("G3D_REQUIRE_DATA") == "1":
        pytest.fail(f"the recorded Prada ledger is missing: {PRADA_LEDGER} (G3D_REQUIRE_DATA=1)")
    return recorded_rows(path) if path is not None else fixture_rows()


class ScriptedOpenAI:
    """httpx2.MockTransport handler for api.openai.com: /v1/responses/input_tokens answers the recorded count and
    /v1/responses the recorded usage, after checking that the reservation is already durable."""

    def __init__(self, rows):
        self.rows, self.counts, self.creates, self.peak_committed, self.guard = rows, [], [], 0, None

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        if request.url.path == "/v1/responses/input_tokens":
            self.counts.append(body)
            row = self.rows[len(self.counts) - 1]
            return httpx2.Response(200, json={"object": "response.input_tokens", "input_tokens": row["counted"]})
        assert request.url.path == "/v1/responses" and request.method == "POST"
        self.creates.append((dict(request.headers), body))
        n = len(self.creates)
        assert n == len(self.counts), "every request is counted before it is sent"
        durable = json.loads(self.guard.ledger_path.read_text(encoding="utf-8"))["rows"][-1]
        assert (durable["sequence"], durable["outcome"]) == (n, "held"), "reserve, persist, then send"
        self.peak_committed = max(self.peak_committed, self.guard.summary()["committed_micro_usd"])
        row = self.rows[n - 1]
        return httpx2.Response(200, headers={"x-request-id": f"req_prada_{n:02d}"}, json={
            "id": f"resp_prada_{n:02d}", "object": "response", "created_at": 1_790_000_000 + n,
            "model": "gpt-6-astra", "status": "completed", "service_tier": "default",
            "output": [{"type": "message", "id": f"msg_prada_{n:02d}", "role": "assistant", "status": "completed",
                        "content": [{"type": "output_text", "text": f"turn {n}", "annotations": []}]}],
            "parallel_tool_calls": False, "tool_choice": "auto", "tools": [], "max_output_tokens": row["max_output"],
            "usage": row["usage"]})


def sdk_model_response(created) -> ModelResponse:
    """What openai-agents 0.22.3 hands on_llm_end for a Responses reply (agents/usage.py:371-378)."""
    usage = Usage(requests=1, input_tokens=created.usage.input_tokens, output_tokens=created.usage.output_tokens,
                  total_tokens=created.usage.total_tokens, input_tokens_details=created.usage.input_tokens_details,
                  output_tokens_details=created.usage.output_tokens_details)
    return ModelResponse(output=list(created.output), usage=usage, response_id=created.id,
                         request_id=getattr(created, "_request_id", None))


def make_approved_job(root: Path, run_id: str) -> tuple[Path, str]:
    job = root / "jobs" / "prada-replay-8c82d986"
    (job / "inputs" / "images").mkdir(parents=True)
    (job / "job.json").write_text(json.dumps({"schema": "g3d.job/1", "name": "Prada (money replay)"}),
                                  encoding="utf-8")
    (job / "inputs" / "images" / "reference-01.png").write_bytes(b"\x89PNG replay")
    settings = {"model": "gpt-6-astra", "effort": "max", "max_output_tokens": 8192, "max_turns": 60}
    request = build_request(job, instructions_sha256="a" * 64, tool_registry_sha256="b" * 64, settings=settings)
    A.write_approval_request(job / "runs" / run_id, job_id=job.name, request=request,
                             estimate={"expected_usd": "14.26"}, suggested_cap_usd=CAP_USD)
    A.write_approval(job / "runs" / run_id, max_usd=CAP_USD, owner_words="Approve $18 for the Prada replay.")
    return job, request_sha256(request)


async def replay(root: Path, rows: list[dict]):
    run_id = "20261007T120000-prada0"
    job, current = make_approved_job(root, run_id)
    script = ScriptedOpenAI(rows)
    checks = 0
    with JobLedger(job) as jobs:
        ledger = jobs.open_paid_run(run_id, current_request_sha256=current)       # consumes the $18 approval
        counter = offline_counting_client(script.handle, api_key=OFFLINE_KEY)
        guard = BudgetGuard.attach(counter, ledger)
        script.guard = guard
        client = offline_inference_client(script.handle, api_key=OFFLINE_KEY, event_hooks={
            "request": [guard.before_request], "response": [guard.after_response]})
        try:
            for n, row in enumerate(rows, 1):
                created = await client.responses.create(
                    model="gpt-6-astra", instructions="Prada money replay",
                    input=[{"role": "user", "content": f"turn {n}"}], tools=[TOOL], include=[],
                    max_output_tokens=row["max_output"], parallel_tool_calls=False,
                    prompt_cache_key="agents-sdk:session:prada-replay",
                    reasoning={"effort": "max", "summary": "auto"}, service_tier="default")
                await guard.on_llm_end(sdk_model_response(created))
                checks += 1
        finally:
            await client.close()
            await counter.close()
        scan = jobs.close_paid_run()
    return guard, script, scan, checks


def money(rows: list[dict]) -> list[tuple]:
    return [(row["sequence"], row["input_tokens"], row["reserved_micro_usd"], row["outcome"], row["settled_from"],
             row["settled_micro_usd"], json.dumps(row["usage"], sort_keys=True), row["response_id"],
             row["request_id"]) for row in rows]


def test_prada_money_replay_settles_the_recorded_total_exactly(tmp_path):
    rows = replay_rows()
    guard, script, scan, checks = run(replay(tmp_path, rows))
    ledger_rows = RunLedger.load(guard.ledger_path).rows          # re-priced and tamper-checked on load
    assert len(ledger_rows) == len(script.counts) == len(script.creates) == checks == 59
    assert all((row["outcome"], row["settled_from"], row["http_status"]) == ("settled", "transport", 200)
               for row in ledger_rows), "each row settled once, from the transport"
    assert [row["response_id"] for row in ledger_rows] == [f"resp_prada_{n:02d}" for n in range(1, 60)]
    assert ledger_rows[0]["reserved_micro_usd"] == ROW1_RESERVED
    total = sum(row["settled_micro_usd"] for row in ledger_rows)
    assert total == SETTLED_TOTAL, f"settled {total} micro-USD, recorded {SETTLED_TOTAL}: off by {total - SETTLED_TOTAL}"
    summary = guard.summary()
    assert (summary["settled_micro_usd"], summary["held_micro_usd"], summary["unknown_micro_usd"],
            summary["requests_settled"]) == (SETTLED_TOTAL, 0, 0, 59)
    assert script.peak_committed == PEAK_COMMITTED <= 18_000_000          # peak committed within the $18 cap
    assert scan["totals"]["settled_micro_usd"] == SETTLED_TOTAL and scan["totals"]["unknown_micro_usd"] == 0
    assert scan["blocking"] == [] and scan["warnings"] == []
    for headers, body in script.creates:
        assert headers["x-stainless-retry-count"] == "0" and headers["authorization"] == f"Bearer {OFFLINE_KEY}"
        assert set(body) == SDK_FIELDS
        assert (body["model"], body["service_tier"], body["max_output_tokens"]) == ("gpt-6-astra", "default", 8192)
        assert all(tool["type"] == "function" for tool in body["tools"])
    for count in script.counts:
        assert "max_output_tokens" not in count and "service_tier" not in count
    if "reserved" in rows[0]:       # the recorded ledger is present: every row's money equals the recording
        assert [row["reserved_micro_usd"] for row in ledger_rows] == [row["reserved"] for row in rows]
        assert [row["settled_micro_usd"] for row in ledger_rows] == [row["settled"] for row in rows]


def test_prada_money_replay_is_deterministic(tmp_path):
    rows = fixture_rows()
    first = run(replay(tmp_path / "one", rows))[0]
    second = run(replay(tmp_path / "two", rows))[0]
    assert money(RunLedger.load(first.ledger_path).rows) == money(RunLedger.load(second.ledger_path).rows)


def test_fixture_holds_only_token_counts():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert set(data) == {"schema", "about", "columns", "rows"} and len(data["rows"]) == 59
    assert all(len(row) == 7 and all(type(value) is int and value >= 0 for value in row) for row in data["rows"])
    text = FIXTURE.read_text(encoding="utf-8")
    for forbidden in ("C:", "Users", "\\", "resp_", "req_", "sk-", "http", "micro"):
        assert forbidden not in text


def test_fixture_is_an_exact_token_excerpt_of_the_recorded_ledger():
    path = require_data(recorded_ledger_path(), f"the recorded Prada ledger ({PRADA_LEDGER})")
    recorded = recorded_rows(path)
    fixture = fixture_rows()
    assert [(row["counted"], row["max_output"], row["usage"]) for row in recorded] == [
        (row["counted"], row["max_output"], row["usage"]) for row in fixture]
    assert recorded[0]["reserved"] == ROW1_RESERVED and sum(row["settled"] for row in recorded) == SETTLED_TOTAL


def test_recorded_transport_usages_are_the_ledger_usages():
    """The ledger's flat usage is lossless: each archived provider usage block equals its reconstruction."""
    path = require_data(recorded_ledger_path(), f"the recorded Prada ledger ({PRADA_LEDGER})")
    transport = require_data(path.parent / "transport", "the recorded Prada transport archive")
    rows = recorded_rows(path)
    for n, row in enumerate(rows, 1):
        answer = json.loads((transport / f"{n:04d}-response.json").read_text(encoding="utf-8"))
        sent = json.loads((transport / f"{n:04d}-request.json").read_text(encoding="utf-8"))
        assert (answer["model"], answer["status"], answer["service_tier"]) == ("gpt-6-astra", "completed", "default")
        assert answer["usage"] == row["usage"] and isinstance(answer["id"], str)
        assert set(sent) == SDK_FIELDS and sent["max_output_tokens"] == row["max_output"]
        assert Tariff.frozen().settle_micro(answer["usage"]) == row["settled"]


def test_missing_recorded_data_fails_under_require_data(monkeypatch):
    monkeypatch.setattr(sys.modules[__name__], "recorded_ledger_path", lambda: None)
    monkeypatch.setenv("G3D_REQUIRE_DATA", "1")
    with pytest.raises(pytest.fail.Exception):
        replay_rows()
    with pytest.raises(pytest.fail.Exception):
        require_data(None, "the recorded Prada ledger")
    monkeypatch.delenv("G3D_REQUIRE_DATA")
    assert replay_rows() == fixture_rows()          # without the switch the token fixture stands in
    with pytest.raises(pytest.skip.Exception):
        require_data(None, "the recorded Prada ledger")
