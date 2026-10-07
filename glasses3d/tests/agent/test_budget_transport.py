"""BudgetGuard on the installed SDK stack (openai 3.22.1 + openai-agents 0.22.3 over httpx2.MockTransport): the money
parts of the Studio's transport tests (source-before/tests/test_blender_agent_transport.py) and pins of what the real
Runner sends and raises. Offline: G3D_OFFLINE=1, an sk-offline- key, MockTransport only (conftest)."""
import asyncio
import json

import httpx2
import pytest

pytest.importorskip("agents")
from agents import Agent, ModelSettings, RunConfig, RunHooks, Runner, SQLiteSession, function_tool  # noqa: E402
from agents.models.openai_provider import OpenAIProvider  # noqa: E402
from agents.retry import ModelRetrySettings  # noqa: E402
from agents.run import ToolExecutionConfig  # noqa: E402
from agents.usage import Usage  # noqa: E402
from openai import APIConnectionError, APIStatusError, APITimeoutError, RateLimitError  # noqa: E402
from openai.types.shared import Reasoning  # noqa: E402

from glasses3d.agent.budget import COUNT_FIELDS, CREATE_FIELDS, BudgetGuard, BudgetGuardError  # noqa: E402
from glasses3d.agent.offline import (disable_sdk_tracing, offline_counting_client,  # noqa: E402
                                     offline_inference_client)
from glasses3d.tests.agent.support import (OFFLINE_KEY, fake_counter, ledger, provider_body, request,  # noqa: E402
                                           response)

run = asyncio.run
SDK_FIELDS = {"include", "input", "instructions", "max_output_tokens", "model", "parallel_tool_calls",
              "prompt_cache_key", "reasoning", "service_tier", "tools"}     # = the Prada run's archived requests


def terminal_provider(**changes):
    payload = {"id": "resp_incomplete", "object": "response", "created_at": 1790000000, "model": "gpt-6-astra",
               "status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}, "output": [],
               "parallel_tool_calls": False, "tool_choice": "auto", "tools": [],
               "usage": {"input_tokens": 1000, "output_tokens": 100, "total_tokens": 1100,
                         "input_tokens_details": {"cached_tokens": 900, "cache_write_tokens": 100},
                         "output_tokens_details": {"reasoning_tokens": 20}}}
    return {**payload, **changes}


def guard_in(tmp_path, cap="1"):
    client, _ = fake_counter()
    return BudgetGuard(client, tmp_path / "budget.json", cap)


# =========================================================================== ported Studio transport tests


def test_incomplete_transport_settles_before_sdk_failure_and_cannot_double_settle(tmp_path):
    guard = guard_in(tmp_path)
    provider = terminal_provider()

    async def flow():
        await guard.before_request(request())
        original = ledger(guard.ledger_path)["rows"][0]
        await guard.after_response(httpx2.Response(200, json=provider, headers={"x-request-id": "req_fixture"}))
        # this receipt exists even when the SDK then raises instead of calling on_llm_end
        row = ledger(guard.ledger_path)["rows"][0]
        assert all(row[key] == original[key] for key in ("sequence", "time", "input_tokens", "max_output_tokens",
                                                         "reserved_micro_usd"))
        assert row["settled_micro_usd"] == 7150 and guard.summary()["unknown_usd"] == "0.000000"
        saved = guard.ledger_path.read_bytes()
        # a future SDK may still deliver on_llm_end: it only cross-checks, the money is recorded once
        sdk = type("ModelResponse", (), {})()
        sdk.response_id, sdk.request_id, sdk.output = provider["id"], "req_fixture", []
        sdk.usage = Usage(requests=1, **provider["usage"])
        await guard.on_llm_end(sdk)
        assert guard.ledger_path.read_bytes() == saved
        sdk.response_id = "resp_different"
        with pytest.raises(BudgetGuardError, match="differs"):
            await guard.on_llm_end(sdk)
        assert ledger(guard.ledger_path)["rows"] == json.loads(saved)["rows"]
    run(flow())


@pytest.mark.parametrize("provider,status,raises", [
    (terminal_provider(usage=None), 200, True),
    (terminal_provider(usage={"input_tokens": 0, "output_tokens": 0}), 200, True),
    (terminal_provider(usage={"input_tokens": 1000, "output_tokens": -1}), 200, True),
    (terminal_provider(), 502, False),
    (terminal_provider(model="unknown"), 200, True),
    (terminal_provider(status="in_progress"), 200, True),
])
def test_unverified_usage_never_releases_the_hold(tmp_path, provider, status, raises):
    guard = guard_in(tmp_path)

    async def flow():
        await guard.before_request(request())
        answer = httpx2.Response(status, json=provider)
        if raises:
            with pytest.raises(BudgetGuardError):
                await guard.after_response(answer)
        else:
            await guard.after_response(answer)
    run(flow())
    row = ledger(guard.ledger_path)["rows"][0]
    assert row["outcome"] == "unknown" and row["settled_micro_usd"] is None
    assert guard.summary()["requests_unknown"] == 1 and guard.summary()["unknown_micro_usd"] == 17_500


@pytest.mark.parametrize("body", [b"[1,2]", b"null", b"gateway unavailable"])
def test_error_payload_does_not_mask_the_sdk_response(tmp_path, body):
    guard = guard_in(tmp_path)

    async def flow():
        await guard.before_request(request())
        answer = httpx2.Response(502, content=body)
        row = await guard.after_response(answer)
        assert answer.content == body          # the SDK still reads the provider's own bytes
        return row
    row = run(flow())
    assert (row["outcome"], row["http_status"]) == ("unknown", 502)


# =========================================================================== the real Runner and client


class GuardHooks(RunHooks):
    """The part of ReviewLog the money path needs: on_llm_end cross-checks against the transport settlement."""

    def __init__(self, guard):
        self.guard = guard
        self.ends = []

    async def on_llm_end(self, context, agent, response):
        self.ends.append((response.response_id, response.request_id))
        await self.guard.on_llm_end(response)


@function_tool
def ping(text: str) -> str:
    """Echo the text."""
    return text


def scripted(answers):
    """A MockTransport handler: counts are 1000 + 10*n; responses come from ``answers`` (callables of n)."""
    state = {"counts": 0, "creates": 0, "bodies": []}

    def handler(sent):
        body = json.loads(sent.content or b"{}")
        if sent.url.path == "/v1/responses/input_tokens":
            state["counts"] += 1
            return httpx2.Response(200, json={"object": "response.input_tokens",
                                              "input_tokens": 1000 + 10 * state["counts"]})
        assert sent.url.path == "/v1/responses"
        state["creates"] += 1
        state["bodies"].append(body)
        return answers[state["creates"] - 1](state["creates"])
    return handler, state


def tool_call(n):
    return httpx2.Response(200, headers={"x-request-id": f"req_{n}"}, json=provider_body(
        response_id=f"resp_{n}", usage_block=usage_for(n), output=[{
            "type": "function_call", "id": f"fc_{n}", "call_id": f"call_{n}", "name": "ping",
            "arguments": json.dumps({"text": "hi"}), "status": "completed"}]))


def message(n, status="completed"):
    return httpx2.Response(200, headers={"x-request-id": f"req_{n}"}, json=provider_body(
        response_id=f"resp_{n}", status=status, usage_block=usage_for(n)))


def usage_for(n):
    tokens = 1000 + 10 * n
    return {"input_tokens": tokens, "output_tokens": 50, "total_tokens": tokens + 50,
            "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": tokens},
            "output_tokens_details": {"reasoning_tokens": 10}}


async def run_agent(tmp_path, handler, *, max_turns=5, cap="5"):
    disable_sdk_tracing()          # never builds the SDK's tracing exporter client
    counter = offline_counting_client(handler, api_key=OFFLINE_KEY)
    guard = BudgetGuard(counter, tmp_path / "budget.json", cap)
    client = offline_inference_client(handler, api_key=OFFLINE_KEY, event_hooks={
        "request": [guard.before_request], "response": [guard.after_response]})
    settings = ModelSettings(parallel_tool_calls=False, max_tokens=16_384, retry=ModelRetrySettings(max_retries=0),
                             extra_body={"service_tier": "default"}, reasoning=Reasoning(effort="max", summary="auto"))
    agent = Agent(name="Astra", model="gpt-6-astra", instructions="stage C pin", tools=[ping], model_settings=settings)
    hooks = GuardHooks(guard)
    session = SQLiteSession("astra", str(tmp_path / "session.sqlite"))
    error = None
    try:
        await Runner.run(agent, "go", max_turns=max_turns, session=session, hooks=hooks, run_config=RunConfig(
            tracing_disabled=True, trace_include_sensitive_data=False,
            tool_execution=ToolExecutionConfig(max_function_tool_concurrency=1),
            model_provider=OpenAIProvider(openai_client=client, use_responses=True)))
    except Exception as raised:                     # noqa: BLE001 - the test inspects it
        error = raised
    finally:
        session.close()
    return guard, hooks, error


def test_runner_sends_only_reviewed_fields_and_every_response_settles_from_the_transport(tmp_path):
    handler, state = scripted([tool_call, message])
    guard, hooks, error = run(run_agent(tmp_path, handler))
    assert error is None
    assert state["counts"] == state["creates"] == len(guard.ledger.rows) == 2
    for body in state["bodies"]:
        assert set(body) == SDK_FIELDS and set(body) <= COUNT_FIELDS | CREATE_FIELDS
        assert (body["model"], body["service_tier"], body["max_output_tokens"]) == ("gpt-6-astra", "default", 16_384)
        assert all(tool["type"] == "function" for tool in body["tools"]) and "stream" not in body
    assert hooks.ends == [("resp_1", "req_1"), ("resp_2", "req_2")]
    rows = guard.ledger.rows
    assert [(row["outcome"], row["settled_from"], row["response_id"]) for row in rows] == [
        ("settled", "transport", "resp_1"), ("settled", "transport", "resp_2")]
    assert [row["reserved_micro_usd"] for row in rows] == [1010 * 12.5 + 16_384 * 50, 1020 * 12.5 + 16_384 * 50]
    assert [row["settled_micro_usd"] for row in rows] == [1010 * 12.5 + 50 * 50, 1020 * 12.5 + 50 * 50]


def test_runner_incomplete_is_settled_by_the_transport_before_the_sdk_raises(tmp_path):
    handler, state = scripted([lambda n: message(n, status="incomplete")])
    guard, hooks, error = run(run_agent(tmp_path, handler))
    assert error is not None and not isinstance(error, BudgetGuardError)
    assert hooks.ends == []                         # the SDK raised before on_llm_end
    row = guard.ledger.rows[0]
    assert (row["outcome"], row["settled_from"], row["response_id"], row["settled_micro_usd"]) == (
        "settled", "transport", "resp_1", 1010 * 12.5 + 50 * 50)
    assert guard.status_hint() is None and guard.summary()["unknown_micro_usd"] == 0


@pytest.mark.parametrize("answer,expected,outcome", [
    (lambda n: httpx2.Response(429, json={"error": {"type": "requests", "code": "rate_limit_exceeded"}}),
     RateLimitError, "rejected_4xx"),
    (lambda n: httpx2.Response(520, content=b"origin error"), APIStatusError, "unknown"),
    (lambda n: httpx2.Response(500, json={"error": {"type": "server_error"}}), APIStatusError, "unknown"),
])
def test_runner_provider_status_errors_reach_the_loop_with_the_outcome_recorded(tmp_path, answer, expected, outcome):
    handler, state = scripted([answer])
    guard, hooks, error = run(run_agent(tmp_path, handler))
    assert isinstance(error, expected)
    assert state["creates"] == 1 and len(guard.ledger.rows) == 1
    row = guard.ledger.rows[0]
    assert row["outcome"] == outcome
    assert row["settled_micro_usd"] == (0 if outcome == "rejected_4xx" else None)
    assert guard.status_hint() == ("provider_rejected" if outcome == "rejected_4xx" else "provider_unknown")


@pytest.mark.parametrize("failure,expected", [(httpx2.ConnectError("dropped after send"), APIConnectionError),
                                              (httpx2.ReadTimeout("900 s"), APITimeoutError)])
def test_runner_connection_error_or_timeout_leaves_one_request_for_the_loop_to_mark_unknown(tmp_path, failure,
                                                                                         expected):
    def drop(n):
        raise failure
    handler, state = scripted([drop])
    guard, hooks, error = run(run_agent(tmp_path, handler))
    assert isinstance(error, expected)
    assert guard.ledger.rows[0]["outcome"] == "held"          # no response hook ran

    async def loop_except_clause():
        return await guard.transport_failed(error)
    row = run(loop_except_clause())
    assert (row["outcome"], row["reason"]) == ("unknown", f"no provider response ({type(error).__name__})")
    assert state["creates"] == 1 and guard.status_hint() == "provider_unknown"
    assert guard.summary()["unknown_micro_usd"] == row["reserved_micro_usd"]


def test_runner_budget_limit_sends_nothing_after_the_refusal(tmp_path):
    # $0.84 covers the first 16,384-token hold ($0.831825) but not the second ($0.831950) beside the first
    # settlement ($0.015125): 0.015125 + 0.831950 = 0.847075 > 0.84
    handler, state = scripted([tool_call, message])
    guard, hooks, error = run(run_agent(tmp_path, handler, cap="0.84"))
    assert error is not None and guard.blocked_kind == "BudgetExceeded" and guard.status_hint() == "budget_limit"
    assert state["creates"] == 1 and state["counts"] == 2 and len(guard.ledger.rows) == 1
    assert guard.ledger.rows[0]["outcome"] == "settled"


def test_offline_clients_answer_the_guard_counter_check():
    handler, _ = scripted([])
    counter = offline_counting_client(handler, api_key=OFFLINE_KEY)
    assert str(counter.base_url) == "https://api.openai.com/v1/" and counter.max_retries == 0
