"""Pins of every openai-agents 0.22.3 / openai 3.22.1 behaviour the loop design relies on (WP2_DESIGN sections 7, 16.3).

Each test names the installed source it pins (glasses3d/.venv/Lib/site-packages, file:line). All of them run the
real Runner with the loop's own construction (loop.build_agent / build_model_settings / build_run_config) over
agent.replay.ScriptedOpenAI, through the real BudgetGuard hooks; no network (conftest).
"""
import asyncio
import hashlib
import time
from types import SimpleNamespace

import pytest

import agents  # Required pinned SDK: import failures must fail this suite.
from agents import RunHooks, Runner, SQLiteSession, function_tool  # noqa: E402
from agents.exceptions import MaxTurnsExceeded, ModelBehaviorError, UserError  # noqa: E402
from agents.tool import FunctionTool  # noqa: E402
from openai import APIStatusError, RateLimitError  # noqa: E402

from glasses3d.agent import loop as L  # noqa: E402
from glasses3d.agent import replay as R  # noqa: E402
from glasses3d.agent.budget import BudgetExceeded, BudgetGuard  # noqa: E402
from glasses3d.agent.offline import (disable_sdk_tracing, offline_counting_client,  # noqa: E402
                                     offline_inference_client)
from glasses3d.agent.receipts import StopRequested  # noqa: E402
from glasses3d.agent.session_inputs import plan_session_input  # noqa: E402
from glasses3d.tests.agent.loop_support import BRIEF, SimulatedCrash, photos  # noqa: E402
from glasses3d.tests.agent.support import OFFLINE_KEY  # noqa: E402

SDK_FIELDS = {"include", "input", "instructions", "max_output_tokens", "model", "parallel_tool_calls",
              "prompt_cache_key", "reasoning", "service_tier", "tools"}


def test_global_websocket_default_cannot_bypass_http_budget_hooks(tmp_path, monkeypatch):
    from agents.models import _openai_shared
    monkeypatch.setattr(_openai_shared, "get_default_openai_responses_transport", lambda: "websocket")
    outcome = asyncio.run(sdk_run(tmp_path, [R.message("done")], [scene_summary]))
    assert outcome.error is None
    assert len(outcome.transport.counts) == len(outcome.transport.creates) == len(outcome.guard.ledger.rows) == 1


@function_tool
def scene_summary() -> dict:
    """Summarize the scene."""
    return {"status": "ok", "objects": 3}


@function_tool
def execute_blender_code(code: str, label: str = "") -> str:
    """Run code.

    Args:
        code: The snippet.
        label: A label.
    """
    return "ok:" + label


async def sdk_run(tmp_path, replies, tools, *, max_turns=5, hooks=None, user_input="go", cap="5", name="pin"):
    disable_sdk_tracing()
    transport = R.ScriptedOpenAI(replies)
    counter = offline_counting_client(transport.handle, api_key=OFFLINE_KEY)
    guard = BudgetGuard(counter, tmp_path / f"budget-{name}.json", cap)
    client = offline_inference_client(transport.handle, api_key=OFFLINE_KEY, event_hooks={
        "request": [guard.before_request], "response": [guard.after_response]})
    agent = L.build_agent("pin instructions", tools, L.RunSettings(effort="max", max_output_tokens=1024, max_turns=max_turns))
    session = SQLiteSession("astra", str(tmp_path / f"session-{name}.sqlite"))
    outcome = SimpleNamespace(result=None, error=None, items=None, transport=transport, guard=guard)
    try:
        outcome.result = await Runner.run(agent, user_input, max_turns=max_turns, session=session,
                                          hooks=hooks or RunHooks(), run_config=L.build_run_config(client))
    except BaseException as error:          # noqa: BLE001 - the pins inspect it
        outcome.error = error
    finally:
        outcome.items = await session.get_items()
        session.close()
        await client.close()
        await counter.close()
    return outcome


def outputs(body):
    return [item for item in body["input"] if isinstance(item, dict) and item.get("type") == "function_call_output"]


def calls(items):
    return [item for item in items if isinstance(item, dict) and item.get("type") == "function_call"]


# --------------------------------------------------------------------------- request shape


def test_the_request_carries_exactly_the_reviewed_fields_and_a_session_cache_key(tmp_path):
    """openai_responses.py:1001-1041 (omitted values dropped, extra_body merged); prompt_cache_key.py:100-143 (one
    key per session: agents-sdk:session:<sha256(session id)[:32]>); model_retry.py:526-532 + openai
    _base_client.py:547-548 (x-stainless-retry-count 0)."""
    out = asyncio.run(sdk_run(tmp_path, [R.tool_call("scene_summary", {}), R.message("done")], [scene_summary]))
    assert out.error is None
    expected_key = "agents-sdk:session:" + hashlib.sha256(b"astra").hexdigest()[:32]
    for headers, body in zip(out.transport.create_headers, out.transport.creates):
        assert set(body) == SDK_FIELDS
        assert (body["model"], body["service_tier"], body["max_output_tokens"], body["parallel_tool_calls"]) == (
            "gpt-6-astra", "default", 1024, False)
        assert body["reasoning"] == {"effort": "max", "summary": "auto"} and body["include"] == []
        assert body["prompt_cache_key"] == expected_key
        assert headers["x-stainless-retry-count"] == "0" and "stream" not in body
    assert L.build_model_settings(L.RunSettings(effort="max")).retry.max_retries == 0
    config = L.build_run_config(object.__new__(__import__("openai").AsyncOpenAI))
    assert config.tracing_disabled is True and config.trace_include_sensitive_data is False
    assert config.tool_execution.max_function_tool_concurrency == 1


def test_on_llm_end_receives_the_response_and_request_ids(tmp_path):
    """run_loop.py:2369-2373 calls on_llm_end with the ModelResponse; items.py:706-721 (response_id, request_id from
    x-request-id): what BudgetGuard.on_llm_end cross-checks."""
    seen = []

    class Hooks(RunHooks):
        async def on_llm_end(self, context, agent, response):
            seen.append((response.response_id, response.request_id))
    out = asyncio.run(sdk_run(tmp_path, [R.message("done")], [], hooks=Hooks()))
    assert out.error is None and seen == [("resp_0001", "req_0001")]


# --------------------------------------------------------------------------- retries


def test_no_retry_of_any_kind_with_max_retries_zero(tmp_path):
    """model_retry.py:403-404 (attempt 1 > max_retries 0: no runner retry), :526-532 (provider retries disabled),
    :509-513 (no conversation-locked retry); the openai client itself has max_retries=0."""
    out = asyncio.run(sdk_run(tmp_path, [R.http_error(500, json_body={"error": {"type": "server_error"}}),
                                         R.message("never sent")], [scene_summary]))
    assert isinstance(out.error, APIStatusError) and len(out.transport.creates) == 1
    out = asyncio.run(sdk_run(tmp_path, [R.rate_limited(), R.message("never sent")], [scene_summary], name="429"))
    assert isinstance(out.error, RateLimitError) and len(out.transport.creates) == 1


def test_hook_exceptions_reach_the_loop_unwrapped(tmp_path):
    """openai _base_client.py:1778-1808 + _httpx2.py:105-109: only httpx2.RequestError (and SSL/EndOfStream) become
    APIConnectionError; a BudgetGuardError raised in the request hook propagates as itself through the SDK."""
    # each hold: 1,000 counted x $12.5/M + 1,024 output x $50/M = 63,700 micro; the first settles at 15,000 (1,000
    # written + 50 output), so the second needs 15,000 + 63,700 = 78,700 > the 70,000 cap
    out = asyncio.run(sdk_run(tmp_path, [R.tool_call("scene_summary", {}, counted=1000),
                                         R.message("done", counted=1000)], [scene_summary], cap="0.07"))
    assert type(out.error) is BudgetExceeded and len(out.transport.creates) == 1
    assert out.guard.ledger.rows[0]["reserved_micro_usd"] == 63_700
    assert out.guard.ledger.rows[0]["settled_micro_usd"] == 15_000 and len(out.transport.counts) == 2


# --------------------------------------------------------------------------- incomplete


def test_incomplete_raises_model_behavior_error_before_on_llm_end_and_persists_nothing_of_that_turn(tmp_path):
    """openai_responses.py:856-859 + _response_terminal.py:10-31,52-58: a non-streamed incomplete response raises
    ModelBehaviorError("... `response.incomplete` ...") before any hook sees it; run.py:1844-1907 persists turns only
    after run_single_turn returns, so the session keeps the input alone."""
    ends = []

    class Hooks(RunHooks):
        async def on_llm_end(self, context, agent, response):
            ends.append(response.response_id)
    out = asyncio.run(sdk_run(tmp_path, [R.incomplete()], [scene_summary], hooks=Hooks()))
    assert isinstance(out.error, ModelBehaviorError) and "response.incomplete" in str(out.error)
    assert ends == [] and out.items == [{"role": "user", "content": "go"}]
    assert out.guard.ledger.rows[0]["outcome"] == "settled"      # the transport hook settled it first


# --------------------------------------------------------------------------- persistence and dangling calls


def test_input_is_persisted_before_the_first_request_so_a_resume_resends_no_image(tmp_path):
    """run.py:941-968: the new input is saved to the session before the first turn, even when that request fails.
    (The Studio's test_actual_sdk_sqlite_presaves_failed_input_and_resume_replays_once, on the real transport.)"""
    first = plan_session_input([], prompt=BRIEF, photos=photos(2), invocation_text="Run 1.")
    out = asyncio.run(sdk_run(tmp_path, [R.rate_limited()], [scene_summary],
                              user_input=[{"role": "user", "content": first.content}]))
    assert isinstance(out.error, RateLimitError)
    assert out.items == [{"role": "user", "content": first.content}]
    resumed = plan_session_input(out.items, prompt=BRIEF, photos=photos(2), invocation_text="Run 2.")
    assert resumed.receipt["images_sent"] == 0 and resumed.receipt["brief"]["action"] == "reuse_history"


def test_a_crash_mid_tool_loses_the_whole_paid_turn_from_the_session(tmp_path):
    """run.py:1844-1907 (turn items saved after run_single_turn, i.e. after the tools ran); tool_execution.py:328-357
    + :1700-1705 (a BaseException from a tool is the run's failure): no function_call without its output is ever
    persisted, the paid turn is lost and its scene effect stays (the loop's rollback rule)."""
    effects = []

    @function_tool
    def mutate(label: str) -> str:
        """Change the scene, then the runner dies.

        Args:
            label: A label.
        """
        effects.append(label)
        raise SimulatedCrash("runner died after the scene changed")
    out = asyncio.run(sdk_run(tmp_path, [R.tool_call("mutate", {"label": "x"}), R.message("never")], [mutate]))
    assert isinstance(out.error, SimulatedCrash) and effects == ["x"]
    assert out.items == [{"role": "user", "content": "go"}] and calls(out.items) == []
    assert L.dangling_calls(out.items) == []


def test_a_stop_in_on_tool_start_passes_through_unwrapped_and_leaves_no_call(tmp_path):
    """tool_execution.py:1832-1845: an AgentsException raised before or inside a tool is re-raised as is (StopRequested
    subclasses AgentsException); any other Exception is wrapped in UserError("Error running tool ...")."""
    class StopHooks(RunHooks):
        async def on_tool_start(self, context, agent, tool):
            raise StopRequested("stop")

    class BrokenHooks(RunHooks):
        async def on_tool_start(self, context, agent, tool):
            raise RuntimeError("hook bug")
    out = asyncio.run(sdk_run(tmp_path, [R.tool_call("scene_summary", {})], [scene_summary], hooks=StopHooks()))
    assert type(out.error) is StopRequested and calls(out.items) == []
    out = asyncio.run(sdk_run(tmp_path, [R.tool_call("scene_summary", {})], [scene_summary], hooks=BrokenHooks(),
                              name="broken"))
    assert isinstance(out.error, UserError) and isinstance(out.error.__cause__, RuntimeError)
    assert calls(out.items) == []


def test_every_persisted_call_has_its_output_even_at_the_turn_limit(tmp_path):
    """run.py:1503-1528 (MaxTurnsExceeded before the next turn) after run.py:1844-1907 saved the previous turn's call
    with its output: 0.22.3 never leaves a dangling function_call (the loop's backstop never fires)."""
    replies = [R.tool_call("scene_summary", {}), R.tool_call("scene_summary", {}), R.tool_call("scene_summary", {})]
    out = asyncio.run(sdk_run(tmp_path, replies, [scene_summary], max_turns=2))
    assert isinstance(out.error, MaxTurnsExceeded) and len(out.transport.creates) == 2
    assert len(calls(out.items)) == 2 and L.dangling_calls(out.items) == []
    assert out.transport.violations == []


def test_a_dangling_call_in_session_history_is_dropped_from_the_request(tmp_path):
    """session_persistence.py:507-511 -> items.py:171-271 (drop_orphan_function_calls): a function_call without its
    output in the history is removed from the model input (with a reasoning item tied to it), so it never causes a
    400; it silently disappears from what Astra sees. The loop's backstop answers it explicitly instead."""
    async def scenario():
        session = SQLiteSession("astra", str(tmp_path / "dangling.sqlite"))
        await session.add_items([{"role": "user", "content": "earlier"},
                                 {"type": "reasoning", "id": "rs_old", "summary": []},
                                 {"type": "function_call", "id": "fc_old", "call_id": "call_old",
                                  "name": "scene_summary", "arguments": "{}", "status": "completed"}])
        session.close()
        disable_sdk_tracing()
        transport = R.ScriptedOpenAI([R.message("ok")])
        counter = offline_counting_client(transport.handle, api_key=OFFLINE_KEY)
        guard = BudgetGuard(counter, tmp_path / "budget-dangling.json", "5")
        client = offline_inference_client(transport.handle, api_key=OFFLINE_KEY, event_hooks={
            "request": [guard.before_request], "response": [guard.after_response]})
        session = SQLiteSession("astra", str(tmp_path / "dangling.sqlite"))
        try:
            await Runner.run(L.build_agent("pin", [scene_summary], L.RunSettings(effort="max", max_output_tokens=1024)), "go",
                             session=session, run_config=L.build_run_config(client))
        finally:
            session.close()
            await client.close()
            await counter.close()
        return transport
    transport = asyncio.run(scenario())
    types = [item.get("type", "message") for item in transport.creates[0]["input"]]
    assert types == ["message", "message"] and transport.violations == []


# --------------------------------------------------------------------------- tools


@pytest.mark.timing
def test_tool_timeout_default_is_error_as_result_so_astra_receives_an_error_result(tmp_path):
    """tool.py:517 (timeout_behavior default "error_as_result"), :1984-1986 (the message), :2238-2269 (wait_for, then
    the message returned as the tool's output): the run continues."""
    assert FunctionTool.__dataclass_fields__["timeout_behavior"].default == "error_as_result"

    @function_tool(timeout=0.2)
    async def slow_render(seconds: float) -> str:
        """A render that hangs.

        Args:
            seconds: How long.
        """
        await asyncio.sleep(seconds)
        return "rendered"
    started = time.perf_counter()
    out = asyncio.run(sdk_run(tmp_path, [R.tool_call("slow_render", {"seconds": 5}), R.message("ok")],
                              [slow_render]))
    assert out.error is None and time.perf_counter() - started < 3
    assert outputs(out.transport.creates[1])[0]["output"] == "Tool 'slow_render' timed out after 0.2 seconds."


def test_tool_timeouts_need_async_tools():
    """tool.py:2925-2928: a sync function_tool with a timeout is refused at construction (stage D's tools that need
    the hard + 10 s SDK timeout must be async)."""
    def sync_tool(seconds: float) -> str:
        """Sync.

        Args:
            seconds: How long.
        """
        return "x"
    with pytest.raises(ValueError, match="only supported for async"):
        function_tool(timeout=1.0)(sync_tool)


def test_invalid_tool_arguments_reach_astra_with_the_pinned_prefix(tmp_path):
    """tool.py:2724-2738 (schema failure) and :1941-1947 (malformed JSON) raise ModelBehaviorError("Invalid JSON input
    for tool <name>") without details (_debug.py: DONT_LOG_TOOL_DATA defaults to True); tool.py:1965-1975 turns it
    into the text tool_validation.py (stage D) depends on."""
    replies = [R.tool_call("execute_blender_code", {"label": "missing code"}),
               R.Reply("response", output=[{"type": "function_call", "name": "execute_blender_code",
                                            "arguments": "{not json", "status": "completed"}]),
               R.message("ok")]
    out = asyncio.run(sdk_run(tmp_path, replies, [execute_blender_code]))
    assert out.error is None
    prefix = "An error occurred while running the tool. Please try again. Error: Invalid JSON input for tool"
    assert outputs(out.transport.creates[1])[0]["output"] == f"{prefix} execute_blender_code"
    assert outputs(out.transport.creates[2])[-1]["output"] == f"{prefix} execute_blender_code"


def test_dict_tool_results_reach_astra_as_python_repr_and_strings_verbatim(tmp_path):
    """items.py:946-951: a plain dict return value is sent as str(dict) (single quotes, not JSON); items.py:901-902: a
    str is sent verbatim. Stage D's wrappers should return json.dumps(...) text."""
    out = asyncio.run(sdk_run(tmp_path, [R.tool_call("scene_summary", {}),
                                         R.tool_call("execute_blender_code", {"code": "x", "label": "a"}),
                                         R.message("ok")], [scene_summary, execute_blender_code]))
    assert out.error is None
    assert outputs(out.transport.creates[1])[0]["output"] == str({"status": "ok", "objects": 3})
    assert outputs(out.transport.creates[2])[-1]["output"] == "ok:a"


def test_function_tool_concurrency_one_never_overlaps_two_calls_of_one_response(tmp_path):
    """tool_execution.py:1581-1585 + :1634-1644: max_function_tool_concurrency=1 starts one tool task at a time even
    when one response carries two calls (parallel_tool_calls=False asks the API not to; this is the second line)."""
    state = {"active": 0, "peak": 0}

    @function_tool
    async def busy(n: int) -> str:
        """Busy work.

        Args:
            n: Which.
        """
        state["active"] += 1
        state["peak"] = max(state["peak"], state["active"])
        await asyncio.sleep(0.05)
        state["active"] -= 1
        return f"done {n}"
    two = R.Reply("response", output=[
        {"type": "function_call", "name": "busy", "arguments": '{"n": 1}', "call_id": "call_a", "status": "completed"},
        {"type": "function_call", "name": "busy", "arguments": '{"n": 2}', "call_id": "call_b", "status": "completed"}])
    out = asyncio.run(sdk_run(tmp_path, [two, R.message("ok")], [busy]))
    assert out.error is None and state["peak"] == 1
    assert [item["output"] for item in outputs(out.transport.creates[1])] == ["done 1", "done 2"]
