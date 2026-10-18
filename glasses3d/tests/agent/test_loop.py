"""The Astra loop on the real SDK over MockTransport (plan/WP2_DESIGN.md sections 7, 9, 14; stage C).

Real openai 3.22.1 + openai-agents 0.22.3 Runner, real BudgetGuard/JobLedger/approvals, real ReviewLog; only the
transport is faked (agent.replay.ScriptedOpenAI). conftest: G3D_OFFLINE=1, no OPENAI_* env, LoopbackOnly and the
production-transport tripwire (0 non-loopback attempts and 0 production transports asserted at teardown); the fixture
below also replaces the production client class by one that fails the test if it is ever constructed.
"""
import asyncio
import json

import pytest

pytest.importorskip("agents")
from agents import function_tool  # noqa: E402
from agents.tool import ToolOutputImage, ToolOutputText  # noqa: E402

from glasses3d.agent import loop as L  # noqa: E402
from glasses3d.agent import replay as R  # noqa: E402
from glasses3d.agent import budget as B  # noqa: E402
from glasses3d.agent.approvals import ApprovalRefused  # noqa: E402
from glasses3d.agent.budget import COUNT_FIELDS, CREATE_FIELDS, read_bytes_with_retry  # noqa: E402
from glasses3d.agent.receipts import BlobStore  # noqa: E402
from glasses3d.tests.agent.loop_support import (FakeScene, SimulatedCrash, data_url, ledger_rows,  # noqa: E402
                                                make_job, make_tools, occurrences, png_bytes, result_of, spec_for)
from glasses3d.tests.agent.support import OFFLINE_KEY  # noqa: E402

SDK_FIELDS = {"include", "input", "instructions", "max_output_tokens", "model", "parallel_tool_calls",
              "prompt_cache_key", "reasoning", "service_tier", "tools"}
RUN = "20261007T120000-run001"
RUN2 = "20261007T130000-run002"
RUN3 = "20261007T140000-run003"
SETTINGS = L.RunSettings(effort="max", max_output_tokens=4096, max_turns=10)
REAL_PRODUCTION = L.ProductionClients


def test_sdk_opt_in_budget_fit_binds_setting_and_sends_reduced_ceiling(tmp_path):
    settings = L.RunSettings(effort="max", max_output_tokens=4096, max_turns=2, fit_output_to_budget=True)
    usage = R.default_usage(1000, output_tokens=20)
    job, transport, result, _ = run_once(tmp_path, [R.message("done", usage=usage, counted=1000)],
                                        cap="0.020", settings=settings)
    assert result["status"] == "completed"
    assert transport.creates[0]["max_output_tokens"] == 150
    rows = assert_money_invariants(job, RUN, transport)
    assert rows[0]["reserved_micro_usd"] == 20000
    archived = json.loads((job / "runs" / RUN / "transport" / "0001-request.json").read_text())
    assert archived["max_output_tokens"] == 150
    request = json.loads((job / "runs" / RUN / "approval-request.json").read_text())
    assert request["request"]["settings"]["fit_output_to_budget"] is True


def test_sdk_no_reply_cap_runs_past_default_and_accounts_continuation(tmp_path):
    settings = L.RunSettings(effort="max", max_output_tokens=4096, max_turns=None)
    replies = [R.tool_call("scene_summary", {}) for _ in range(12)]
    replies += [R.incomplete(), R.message("done")]
    job, transport, result, _ = run_once(tmp_path, replies, cap="10", settings=settings)
    assert result["status"] == "completed"
    assert result["turns_used"] == 14 and result["continuations"] == 1
    assert result["settings"]["max_turns"] is None
    assert len(assert_money_invariants(job, RUN, transport)) == 14


@pytest.mark.parametrize("cap,counted,expected_requests,expected_spend,expected_counts", [
    ("23", 400_000, 2, 20_000_000, 2),  # reaches the stop; third request is neither counted nor reserved
    ("23", 540_000, 1, 13_500_000, 2),  # below the stop, but the next reservation would exceed $23
    ("44.2788", 540_000, 2, 27_000_000, 2),  # owner-approved headroom admits the last request at $13.50
])
def test_settled_stop_and_reserve_cap_are_independent_on_scripted_transport(
        tmp_path, monkeypatch, cap, counted, expected_requests, expected_spend, expected_counts):
    # Isolate admission at deliberately huge fixed counts. Actual renewal and
    # unchanged money terms together are covered by test_conversation_renewal.
    monkeypatch.setattr(L.ConversationRenewal, "check", lambda *args: None)
    settings = L.RunSettings(effort="high", max_output_tokens=16384, max_turns=10, settled_stop_usd="20")
    usage = R.default_usage(counted, output_tokens=0, reasoning_tokens=0)
    replies = [R.tool_call("scene_summary", {}, counted=counted, usage=usage) for _ in range(3)]
    job, transport, result, _ = run_once(tmp_path, replies, cap=cap, settings=settings)
    assert result["status"] == "budget_limit"
    assert result["budget"]["settled_micro_usd"] == expected_spend
    assert len(transport.creates) == expected_requests and len(transport.counts) == expected_counts
    # interactions includes the SDK turn refused before sending, as on_llm_start has already happened.
    assert result["turns_used"] == expected_requests + 1
    settled = 0
    for row in assert_money_invariants(job, RUN, transport, refused_count=expected_counts - expected_requests):
        assert settled < 20_000_000
        assert settled + row["reserved_micro_usd"] <= B.cap_to_micro(cap)
        settled += row["settled_micro_usd"]
    config = json.loads((job / "runs" / RUN / "run-config.json").read_text())
    assert config["settled_stop_usd"] == result["settings"]["settled_stop_usd"] == "20.000000"


def test_settled_stop_is_bound_to_approval_and_zero_stops_before_count(tmp_path):
    settings = L.RunSettings(effort="max", settled_stop_usd="0")
    _, transport, result, _ = run_once(tmp_path, [R.message("never sent")], settings=settings)
    assert result["status"] == "budget_limit" and result["turns_used"] == 1
    assert transport.creates == transport.counts == []
    scene, tools = make_tools()
    job, sha = make_job(tmp_path / "changed", tools=tools, settings=settings)
    altered = L.RunSettings(effort="max", settled_stop_usd="20")
    with pytest.raises(ApprovalRefused, match="settings"):
        run(L.run_astra(spec_for(job, RUN, sha, tools, R.ScriptedOpenAI([R.message("never sent")]),
                                settings=altered), scene=scene))


@pytest.mark.parametrize("value", [True, -1, "nan", "inf", "not-money"])
def test_invalid_settled_stop_is_refused_before_preflight(tmp_path, value):
    settings = L.RunSettings(effort="max", settled_stop_usd=value)
    spec = L.RunSpec(job_dir=tmp_path, run_id=RUN, instructions="test", tools=[], brief="test",
                     current_request_sha256="unused", settings=settings)
    with pytest.raises(L.RunRefused, match="settled_stop_usd"):
        run(L.run_astra(spec))
    assert not (tmp_path / "budget").exists()


@pytest.fixture(autouse=True)
def no_production_clients(monkeypatch):
    """The production client constructor must never run in these tests."""
    calls = []

    class Forbidden:
        def __init__(self, *args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError("ProductionClients constructed in an offline test")
    monkeypatch.setattr(L, "ProductionClients", Forbidden)
    yield calls
    assert calls == []


def run(coroutine):
    return asyncio.run(coroutine)


def run_once(tmp_path, replies, *, scene=None, tools=None, cap="5", settings=SETTINGS, run_id=RUN, resume=False,
             notes=(), id_start=1, **extra):
    scene_, default_tools = make_tools(scene)
    tools = tools if tools is not None else default_tools
    job, sha = make_job(tmp_path, tools=tools, cap=cap, settings=settings, run_id=run_id, notes=notes)
    transport = R.ScriptedOpenAI(replies, id_start=id_start)
    spec = spec_for(job, run_id, sha, tools, transport, settings=settings, resume=resume, notes=notes, **extra)
    result = run(L.run_astra(spec, scene=scene_))
    return job, transport, result, scene_


def assert_money_invariants(job, run_id, transport, *, refused_count=0):
    """Criterion 2: creates = ledger rows (= count calls, plus the count of a request the cap refused)."""
    rows = ledger_rows(job, run_id)
    assert len(transport.creates) == len(rows), "every request sent has exactly one ledger row"
    assert len(transport.counts) == len(transport.creates) + refused_count, "counted before sent"
    for headers, body in zip(transport.create_headers, transport.creates):
        assert headers["x-stainless-retry-count"] == "0"
        assert (body["model"], body["service_tier"]) == ("gpt-6-astra", "default")
        assert isinstance(body["max_output_tokens"], int)
        assert all(tool["type"] == "function" for tool in body["tools"])
        assert set(body) <= COUNT_FIELDS | CREATE_FIELDS
    assert transport.violations == [] and transport.unexpected == []
    return rows


def assert_clean_receipts(job):
    """Criterion 6: no image text and no key anywhere under the job (session included: BlobSession)."""
    assert occurrences(job, b"data:image") == []
    assert occurrences(job, OFFLINE_KEY.encode()) == []


def transport_files(job, run_id):
    return sorted(path.name for path in (job / "runs" / run_id / "transport").iterdir())


def user_texts(body):
    texts = []
    for item in body["input"]:
        if isinstance(item, dict) and item.get("role") == "user":
            content = item.get("content")
            if isinstance(content, str):
                texts.append(content)
            else:
                texts.extend(part.get("text") for part in content if part.get("type") == "input_text")
    return texts


# =========================================================================== a normal session


@pytest.mark.parametrize("effort", L.EFFORTS)
def test_owner_effort_reaches_real_sdk_request_and_approval(tmp_path, effort):
    from dataclasses import replace
    settings = replace(SETTINGS, effort=effort)
    job, transport, result, _ = run_once(tmp_path, [R.message("done")], settings=settings)
    assert result["status"] == "completed"
    assert transport.creates[0]["reasoning"] == {"effort": effort, "summary": "auto"}
    saved = json.loads((job / "runs" / RUN / "approval-request.json").read_text())
    assert saved["request"]["settings"]["effort"] == effort
    assert_money_invariants(job, RUN, transport)


def test_three_turn_session_with_tool_calls_completes_with_full_receipts(tmp_path):
    replies = [R.tool_call("scene_summary", {}, summary="Look at the scene first."),
               R.tool_call("execute_blender_code", {"code": "import bpy\nresult = len(bpy.data.objects)",
                                                    "label": "count"}, text="Counting objects."),
               R.message("The frame is built.", summary="Done.")]
    job, transport, result, scene = run_once(tmp_path, replies)
    assert result["status"] == "completed" and result["final_output"] == "The frame is built."
    assert result["turns_used"] == 3 and result["continuations"] == 0 and result["error"] is None
    rows = assert_money_invariants(job, RUN, transport)
    assert len(rows) == 3 and all((row["outcome"], row["settled_from"]) == ("settled", "transport") for row in rows)
    assert result["budget"]["settled_micro_usd"] == sum(row["settled_micro_usd"] for row in rows)
    for body in transport.creates:
        assert set(body) == SDK_FIELDS
        assert body["parallel_tool_calls"] is False and body["reasoning"] == {"effort": "max", "summary": "auto"}
        assert body["max_output_tokens"] == 4096
    assert len(R.input_images(transport.creates[0])) == 2
    run_dir = job / "runs" / RUN
    assert transport_files(job, RUN) == [f"{n:04d}-{kind}.json" for n in (1, 2, 3) for kind in ("request", "response")]
    assert sorted(path.name for path in (run_dir / "tools").iterdir()) == ["0001-scene_summary",
                                                                          "0002-execute_blender_code"]
    code_dir = run_dir / "tools" / "0002-execute_blender_code"
    assert (code_dir / "issued-code.py").read_text(encoding="utf-8") == "import bpy\nresult = len(bpy.data.objects)"
    arguments = json.loads((code_dir / "arguments.json").read_text(encoding="utf-8"))
    assert arguments["call_id"] == "call_0002" and arguments["arguments"] == {"label": "count"}
    assert json.loads((code_dir / "result.json").read_text(encoding="utf-8"))["result"]["checkpoint"] == "c0001"
    events = [json.loads(line) for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    kinds = [event["event"] for event in events]
    assert kinds.count("api_request") == kinds.count("api_response") == 3
    assert kinds.count("tool_started") == kinds.count("tool_finished") == 2
    assert kinds[0] == "session_started" and kinds[-1] == "session_finished"
    summaries = [json.loads(line) for line in
                 (run_dir / "reasoning-summaries.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [record["availability"] for record in summaries] == ["returned", "not_returned", "returned"]
    transcript = (run_dir / "transcript.md").read_text(encoding="utf-8")
    assert "The frame is built." in transcript and "```python\nimport bpy" in transcript
    assert "Look at the scene first." in transcript and "`scene_summary`" in transcript
    assert "[front.png](../../work/renders/0001/front.png)" in transcript   # job-relative, linked from runs/<run>/
    assert scene.events == ["start", "exit_checkpoint", "close"]
    assert result["exit_checkpoint"] == {"id": "c0001", "sha256": "f" * 64, "verified": True}
    receipt = json.loads((run_dir / "input-receipt.json").read_text(encoding="utf-8"))
    assert receipt["images_sent"] == 2 and receipt["brief"]["action"] == "send"
    assert [ref["view"] for ref in receipt["references"]] == ["front", "side"]
    config = json.loads((run_dir / "run-config.json").read_text(encoding="utf-8"))
    assert (config["model"], config["effort"], config["max_output_tokens"], config["client"]["kind"]) == (
        "gpt-6-astra", "max", 4096, "offline")
    assert config["versions"]["openai-agents"] == "0.22.3" and config["versions"]["openai"] == "3.22.1"
    scan = json.loads((job / "budget" / "job-ledger.json").read_text(encoding="utf-8"))
    assert scan["totals"]["settled_micro_usd"] == result["budget"]["settled_micro_usd"] and scan["blocking"] == []
    assert not (B.PAID_RUN_LOCK_ROOT / ".paid-run.lock").exists()        # the machine-wide lock was released
    heartbeat = json.loads((job / "heartbeat.json").read_text(encoding="utf-8"))
    assert (heartbeat["state"], heartbeat["final_status"]) == ("finished", "completed")
    assert_clean_receipts(job)


def test_two_runs_with_the_same_script_send_identical_bodies_and_money(tmp_path):
    """Criterion 15 (determinism) on the loop: ids and times come from the script, so bodies match byte for byte."""
    replies = [R.tool_call("scene_summary", {}), R.message("done")]
    first = run_once(tmp_path / "one", replies)
    second = run_once(tmp_path / "two", replies)
    assert first[1].creates == second[1].creates and first[1].counts == second[1].counts
    money = [[(row["reserved_micro_usd"], row["settled_micro_usd"], row["outcome"]) for row in
              ledger_rows(job, RUN)] for job in (first[0], second[0])]
    assert money[0] == money[1]


def test_tool_output_images_are_stored_once_and_never_as_text(tmp_path):
    """A tool's image result travels to the model in every later request, but lands once in blobs/ (criterion 6)."""
    image = png_bytes(4, 4, seed=9)

    @function_tool
    def render_views(view: str) -> list:
        """Render one view.

        Args:
            view: The view name.
        """
        return [ToolOutputText(text=f"rendered {view}"), ToolOutputImage(image_url=data_url(image), detail="high")]
    replies = [R.tool_call("render_views", {"view": "front"}), R.tool_call("render_views", {"view": "top"}),
               R.message("seen")]
    job, transport, result, _ = run_once(tmp_path, replies, tools=[render_views])
    assert result["status"] == "completed"
    assert len(R.input_images(transport.creates[2])) == 2 + 2     # 2 brief photos + the 2 renders (same bytes)
    blob = BlobStore(job / "blobs").path(__import__("hashlib").sha256(image).hexdigest(), "image/png")
    assert blob.read_bytes() == image and len(list((job / "blobs").iterdir())) == 3
    transcript = (job / "runs" / RUN / "transcript.md").read_text(encoding="utf-8")
    assert f"(../../blobs/{blob.name})" in transcript
    assert_clean_receipts(job)


# =========================================================================== provider outcomes


def test_rate_limit_429_is_provider_rejected_at_zero(tmp_path):
    job, transport, result, scene = run_once(tmp_path, [R.rate_limited(), R.message("never sent")])
    assert result["status"] == "provider_rejected" and result["error"]["type"] == "RateLimitError"
    rows = assert_money_invariants(job, RUN, transport)
    assert [(row["outcome"], row["settled_micro_usd"], row["http_status"]) for row in rows] == [
        ("rejected_4xx", 0, 429)]
    assert sum(result["budget"][key] for key in ("settled_micro_usd", "held_micro_usd", "unknown_micro_usd")) == 0
    assert "committed_micro_usd" not in result["budget"] and len(transport.creates) == 1
    assert transport_files(job, RUN) == ["0001-request.json", "0001-response.json"]
    assert scene.events == ["start", "exit_checkpoint", "close"]
    assert_clean_receipts(job)


@pytest.mark.parametrize("reply,reason,error_type", [
    (R.origin_error(), "http 520", "InternalServerError"),     # openai's APIStatusError subclass for 5xx
    (R.drop(), "no provider response (APIConnectionError)", "APIConnectionError"),
    (R.read_timeout(), "no provider response (APITimeoutError)", "APITimeoutError"),
])
def test_520_drop_and_timeout_are_provider_unknown_with_exactly_one_request(tmp_path, reply, reason, error_type):
    job, transport, result, _ = run_once(tmp_path, [reply, R.message("never sent")])
    assert result["status"] == "provider_unknown" and result["error"]["type"] == error_type
    assert result["turns_used"] == 1
    rows = assert_money_invariants(job, RUN, transport)
    assert len(transport.creates) == 1 and len(rows) == 1
    assert (rows[0]["outcome"], rows[0]["reason"], rows[0]["settled_micro_usd"]) == ("unknown", reason, None)
    assert result["budget"]["unknown_micro_usd"] == rows[0]["reserved_micro_usd"]     # the full hold is kept
    scan = json.loads((job / "budget" / "job-ledger.json").read_text(encoding="utf-8"))
    assert scan["totals"]["unknown_micro_usd"] == rows[0]["reserved_micro_usd"]
    expected = ["0001-request.json", "0001-response.json"] if reply.kind == "status" else ["0001-request.json"]
    assert transport_files(job, RUN) == expected
    assert_clean_receipts(job)


def test_a_200_naming_a_dated_model_snapshot_fails_closed_as_provider_unknown(tmp_path):
    reply = R.message("never trusted")
    reply.overrides = {"model": "gpt-6-astra-2026-10-01"}
    job, transport, result, _ = run_once(tmp_path, [reply, R.message("never sent")])
    assert result["status"] == "provider_unknown" and result["error"]["type"] == "BudgetGuardError"
    rows = assert_money_invariants(job, RUN, transport)
    assert (rows[0]["outcome"], rows[0]["reason"]) == ("unknown", "the 200 does not name gpt-6-astra")
    assert len(transport.creates) == 1 and result["budget"]["unknown_micro_usd"] == rows[0]["reserved_micro_usd"]
    transcript = (job / "runs" / RUN / "transcript.md").read_text(encoding="utf-8")
    assert "the budget guard did not accept this response" in transcript
    assert "The run ended: provider_unknown (BudgetGuardError); 1 request(s), spent $0.000000 (settled); " in transcript
    assert "unresolved request hold $" in transcript and "(kept until reconciled)" in transcript


def test_a_settlement_that_could_not_be_written_is_reconciled_exactly_from_the_archived_response(
        tmp_path, monkeypatch):
    """The Oakley run 1 case: the provider answered and billed, the ledger write failed. The response was archived
    before the guard settled (ReviewLog order), so the owner's reconcile-from-transport settles it exactly."""
    from glasses3d.agent import budget as B
    real_write = B.write_json_atomic
    failures = []

    def flaky(path, value):
        rows = value.get("rows") if isinstance(value, dict) else None
        if not failures and rows and rows[-1]["outcome"] == "settled":
            failures.append(path)
            raise PermissionError(13, "access denied")       # not a transient sharing error: no retry
        return real_write(path, value)
    monkeypatch.setattr(B, "write_json_atomic", flaky)
    job, transport, result, _ = run_once(tmp_path, [R.message("done", counted=1000), R.message("never sent")])
    assert result["status"] == "provider_unknown" and result["error"]["type"] == "LedgerPersistError"
    assert len(failures) == 1 and len(transport.creates) == 1
    row = ledger_rows(job, RUN)[0]
    assert (row["outcome"], row["settled_micro_usd"]) == ("unknown", None)
    with B.JobLedger(job) as jobs:
        assert jobs.summary()["totals"]["unknown_micro_usd"] == row["reserved_micro_usd"]
        amount = jobs.reconcile_from_transport(RUN, 1, "Settle request 1 from its archived response.")
        totals = jobs.summary()["totals"]
    assert amount == 1000 * 125 // 10 + 50 * 50 == 15_000          # 1,000 written at $12.5/M + 50 output at $50/M
    assert (totals["unknown_micro_usd"], totals["settled_micro_usd"]) == (0, 15_000)
    reconciled = ledger_rows(job, RUN)[0]
    assert (reconciled["outcome"], reconciled["settled_from"], reconciled["response_id"]) == (
        "reconciled", "transport", "resp_0001")


def test_a_rate_limited_run_is_resumed_without_resending_its_input(tmp_path):
    scene, tools = make_tools()
    job, sha = make_job(tmp_path, tools=tools, settings=SETTINGS)
    first = R.ScriptedOpenAI([R.rate_limited()])
    assert run(L.run_astra(spec_for(job, RUN, sha, tools, first), scene=scene))["status"] == "provider_rejected"
    job, sha2 = make_job(tmp_path, tools=tools, settings=SETTINGS, run_id=RUN2)
    second = R.ScriptedOpenAI([R.message("Built after the rate limit.")], id_start=101)
    result = run(L.run_astra(spec_for(job, RUN2, sha2, tools, second, resume=True), scene=scene))
    assert result["status"] == "completed" and result["input"]["images_sent"] == 0
    assert result["input"]["brief"] == "reuse_history" and len(R.input_images(second.creates[0])) == 2
    scan = json.loads((job / "budget" / "job-ledger.json").read_text(encoding="utf-8"))
    assert scan["totals"]["runs"] == 2 and scan["totals"]["unknown_micro_usd"] == 0 and scan["blocking"] == []


def test_incomplete_is_settled_then_one_continuation_completes(tmp_path):
    job, transport, result, _ = run_once(tmp_path, [R.incomplete(), R.message("Finished after the cut.")])
    assert result["status"] == "completed" and result["final_output"] == "Finished after the cut."
    assert result["continuations"] == 1 and result["turns_used"] == 2
    rows = assert_money_invariants(job, RUN, transport)
    assert [(row["outcome"], row["settled_from"]) for row in rows] == [("settled", "transport")] * 2
    second = transport.creates[1]
    assert second["input"][-1] == {"role": "user", "content": L.CONTINUATION_NOTE}
    assert L.CONTINUATION_NOTE == ("Your previous response was cut off at the output limit and was not recorded. "
                                   "Continue.")
    assert not any(item.get("type") == "message" and item.get("role") == "assistant"
                   for item in second["input"] if isinstance(item, dict))       # the cut-off reply is not replayed
    transcript = (job / "runs" / RUN / "transcript.md").read_text(encoding="utf-8")
    assert "cut off" in transcript and "continuation 1 of 2" in transcript
    assert_clean_receipts(job)


def test_incomplete_three_times_ends_output_limit_after_two_continuations(tmp_path):
    job, transport, result, _ = run_once(tmp_path, [R.incomplete(), R.incomplete(), R.incomplete(),
                                                    R.message("never sent")])
    assert result["status"] == "output_limit" and result["continuations"] == 2
    rows = assert_money_invariants(job, RUN, transport)
    assert len(rows) == 3 and all(row["outcome"] == "settled" for row in rows)


def test_continuations_count_against_max_turns(tmp_path):
    settings = L.RunSettings(effort="max", max_output_tokens=4096, max_turns=2)
    job, transport, result, _ = run_once(tmp_path, [R.incomplete(), R.incomplete(), R.message("never sent")],
                                         settings=settings)
    assert result["status"] == "turn_limit" and len(transport.creates) == 2 and result["turns_used"] == 2


def test_budget_limit_sends_no_request_after_the_refusal(tmp_path):
    # each hold: 1,000 counted x $12.5/M + 4,096 x $50/M = 217,300 micro; the first settles at 15,000, so the second
    # needs 15,000 + 217,300 = 232,300 > the $0.22 cap (the first fits: 217,300 <= 220,000)
    replies = [R.tool_call("scene_summary", {}, counted=1000), R.message("never sent", counted=1000)]
    job, transport, result, _ = run_once(tmp_path, replies, cap="0.22")
    assert result["status"] == "budget_limit" and result["error"]["type"] == "BudgetExceeded"
    rows = assert_money_invariants(job, RUN, transport, refused_count=1)
    assert len(transport.creates) == 1 and [row["reserved_micro_usd"] for row in rows] == [217_300]
    assert rows[0]["settled_micro_usd"] == 15_000 and result["budget"]["blocked_kind"] == "BudgetExceeded"
    assert transport_files(job, RUN) == ["0001-request.json", "0001-response.json"]
    transcript = (job / "runs" / RUN / "transcript.md").read_text(encoding="utf-8")
    assert "the budget guard did not send the next request (Next request needs a hold of $0.217300" in transcript
    assert "The run ended: budget_limit (BudgetExceeded); 1 request(s), spent $0.015000 (settled); " in transcript
    assert "held for the request in flight $0.000000 (released after it settles)" in transcript


def test_turn_limit(tmp_path):
    settings = L.RunSettings(effort="max", max_output_tokens=4096, max_turns=2)
    replies = [R.tool_call("scene_summary", {})] * 3
    job, transport, result, _ = run_once(tmp_path, replies, settings=settings)
    assert result["status"] == "turn_limit" and result["error"]["type"] == "MaxTurnsExceeded"
    assert len(assert_money_invariants(job, RUN, transport)) == 2


def test_a_worker_failure_ends_the_run_failed_worker(tmp_path):
    class WorkerFailed(RuntimeError):
        """Stands in for agent.journal.WorkerFailed (3 restarts in 10 minutes)."""

    @function_tool(failure_error_function=L.fatal_tool_errors(WorkerFailed))
    def execute_blender_code(code: str) -> str:
        """Run code.

        Args:
            code: The snippet.
        """
        raise WorkerFailed("3 worker restarts within 600 s")
    job, transport, result, scene = run_once(tmp_path, [R.tool_call("execute_blender_code", {"code": "x"}),
                                                        R.message("never sent")], tools=[execute_blender_code])
    assert result["status"] == "failed_worker" and len(transport.creates) == 1
    assert scene.events == ["start", "exit_checkpoint", "close"]


def test_an_ordinary_tool_error_reaches_astra_as_text_and_the_run_goes_on(tmp_path):
    @function_tool(failure_error_function=L.fatal_tool_errors(SimulatedCrash))
    def scene_summary() -> str:
        """Summarize."""
        raise ValueError("no such object")
    job, transport, result, _ = run_once(tmp_path, [R.tool_call("scene_summary", {}), R.message("ok")],
                                         tools=[scene_summary])
    assert result["status"] == "completed"
    output = [item for item in transport.creates[1]["input"] if item.get("type") == "function_call_output"][0]
    message, footer = output["output"].split("\n")
    assert message == "An error occurred while running the tool. Please try again. Error: no such object"
    assert footer == L.footer_text(ledger_rows(job, RUN)[0]["settled_micro_usd"] / 1e6, 5, 3358, held_usd=0)


# =========================================================================== stop and heartbeat


def test_a_stop_written_mid_tool_ends_the_run_stopped_at_the_next_model_boundary(tmp_path, capsys):
    scene, _ = make_tools()
    job_stop = tmp_path / "jobs" / "test-frame-0000abcd" / L.STOP_FILE
    _, tools = make_tools(scene, stop_file=job_stop)
    replies = [R.tool_call("slow_tool", {"seconds": 0.6}), R.message("never sent")]
    job, transport, result, _ = run_once(tmp_path, replies, scene=scene, tools=tools, status_line_after_s=0.15)
    assert result["status"] == "stopped" and result["stop_observed"] is True
    assert result["error"]["type"] == "StopRequested"
    rows = assert_money_invariants(job, RUN, transport)
    assert len(transport.creates) == 1 and rows[0]["outcome"] == "settled"
    assert not job_stop.exists()                       # consumed by the run it stopped
    assert scene.events == ["start", "exit_checkpoint", "close"]   # the worker closes only after the exit checkpoint
    session = L.BlobSession(L.SESSION_ID, job / "session" / "session.sqlite", blobs=BlobStore(job / "blobs"))
    try:
        items = run(session.get_items())
    finally:
        session.close()
    assert [item.get("type") for item in items[1:]] == ["function_call", "function_call_output"]  # turn persisted
    heartbeat = json.loads((job / "heartbeat.json").read_text(encoding="utf-8"))
    assert (heartbeat["state"], heartbeat["final_status"]) == ("finished", "stopped")
    assert "tool:slow_tool" in result["heartbeat"]["states"] and "model_wait" in result["heartbeat"]["states"]
    assert result["heartbeat"]["max_gap_s"] <= 3.0       # criterion 14's bound (here at a 50 ms cadence)
    assert f"[astra {RUN}] tool:slow_tool for 0 s" in capsys.readouterr().out
    assert_clean_receipts(job)


def test_an_external_cancellation_mid_tool_is_recorded_stopped_and_everything_is_closed(tmp_path):
    """A hard interrupt (the second Ctrl-C) cancels the run task while a tool runs: result.json still says what
    happened, the exit checkpoint is taken before the worker closes, the paid-run lock is released, and the
    cancellation is re-raised to the caller."""
    scene, tools = make_tools()
    job, sha = make_job(tmp_path, tools=tools, settings=SETTINGS)
    transport = R.ScriptedOpenAI([R.tool_call("slow_tool", {"seconds": 30}), R.message("never sent")])

    started = job / "runs" / RUN / "tools" / "0001-slow_tool" / "arguments.json"     # written by on_tool_start

    async def scenario():
        task = asyncio.create_task(L.run_astra(spec_for(job, RUN, sha, tools, transport), scene=scene))
        for _ in range(600):                     # up to 30 s on a loaded machine
            if started.exists():
                break
            await asyncio.sleep(0.05)
        assert started.exists(), "the tool never started"
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    run(scenario())
    result = result_of(job, RUN)
    assert result["status"] == "stopped" and result["error"]["type"] == "CancelledError"
    rows = assert_money_invariants(job, RUN, transport)
    assert [row["outcome"] for row in rows] == ["settled"] and len(transport.creates) == 1
    assert scene.events == ["start", "exit_checkpoint", "close"]
    assert not (B.PAID_RUN_LOCK_ROOT / ".paid-run.lock").exists()
    assert result["teardown_errors"] == []


def test_teardown_failures_are_recorded_and_result_json_is_still_written(tmp_path):
    class BrokenScene(FakeScene):
        def exit_checkpoint(self):
            self.events.append("exit_checkpoint")
            raise RuntimeError("head sha does not verify")

        def close(self):
            self.events.append("close")
            raise OSError("worker would not stop")
    scene = BrokenScene()
    _, tools = make_tools(scene)
    job, sha = make_job(tmp_path, tools=tools, settings=SETTINGS)
    result = run(L.run_astra(spec_for(job, RUN, sha, tools, R.ScriptedOpenAI([R.message("done")])), scene=scene))
    assert result["status"] == "completed" and result_of(job, RUN)["status"] == "completed"
    assert result["exit_checkpoint"] is None
    assert result["exit_checkpoint_error"] == {"type": "RuntimeError", "message": "head sha does not verify"}
    assert result["teardown_errors"] == [{"step": "scene.close", "type": "OSError",
                                          "message": "worker would not stop"}]
    assert scene.events == ["start", "exit_checkpoint", "close"]
    assert not (B.PAID_RUN_LOCK_ROOT / ".paid-run.lock").exists()


def test_an_unconsumed_stop_file_stops_a_new_run(tmp_path):
    scene, tools = make_tools()
    job, sha = make_job(tmp_path, tools=tools, settings=SETTINGS)
    (job / L.STOP_FILE).write_text("old", encoding="utf-8")
    transport = R.ScriptedOpenAI([R.message("done")])
    result = run(L.run_astra(spec_for(job, RUN, sha, tools, transport), scene=scene))
    assert result["status"] == "stopped" and result["stale_stop_file_removed"] is False
    assert result["requests"] == 0 and not transport.count_answers


def test_heartbeat_writes_on_its_cadence_and_prints_long_states(tmp_path):
    lines = []
    beat = L.Heartbeat(tmp_path / "heartbeat.json", run_id="r1", interval_s=0.02, report_after_s=0.1,
                       report_every_s=0.1, worker_info=lambda: {"worker_pid": 7, "worker_cpu_s": 0.5},
                       printer=lines.append)
    beat.start()
    beat.set_state("tool:render_views", tool_call_id="call_9", request_seq=4)
    import time
    time.sleep(0.35)
    # a reader (job status) must retry: Windows denies a read for the instant the writer replaces the file
    record = json.loads(read_bytes_with_retry(tmp_path / "heartbeat.json"))
    assert (record["state"], record["tool_call_id"], record["request_seq"], record["worker_pid"]) == (
        "tool:render_views", "call_9", 4, 7)
    beat.set_state("idle")
    beat.stop(final_state="completed")
    record = json.loads(read_bytes_with_retry(tmp_path / "heartbeat.json"))
    assert (record["state"], record["final_status"]) == ("finished", "completed")
    assert beat.beats >= 3 and beat.max_gap_s() < 1.0 and beat.write_failures == 0      # loaded machine: generous
    assert lines and all(line.startswith("[astra r1] tool:render_views for 0 s (request 4)") for line in lines)
    assert 1 <= len(lines) <= 4                         # one line per 0.1 s while the state lasts (0.25 s past 0.1)
    assert beat.states == ["tool:render_views", "idle", "finished"]


def test_a_failing_heartbeat_write_never_stops_the_run(tmp_path, monkeypatch):
    beat = L.Heartbeat(tmp_path / "heartbeat.json", run_id="r1", interval_s=0.01)
    monkeypatch.setattr(L, "write_json", lambda path, value: (_ for _ in ()).throw(PermissionError(13, "denied")))
    beat.start()
    import time
    time.sleep(0.05)
    beat.stop()
    assert beat.write_failures == beat.beats >= 2


# =========================================================================== resume


def test_resume_sends_no_image_no_unchanged_brief_and_each_owner_note_once(tmp_path):
    scene, tools = make_tools()
    job, sha = make_job(tmp_path, tools=tools, settings=SETTINGS)
    first = R.ScriptedOpenAI([R.message("First run done.")])
    assert run(L.run_astra(spec_for(job, RUN, sha, tools, first), scene=scene))["status"] == "completed"

    note = "Make the temples 2 mm longer."
    job, sha2 = make_job(tmp_path, tools=tools, settings=SETTINGS, run_id=RUN2, notes=[note])
    second = R.ScriptedOpenAI([R.message("Second run done.")], id_start=101)
    result = run(L.run_astra(spec_for(job, RUN2, sha2, tools, second, resume=True, notes=[note],
                                      scene_note="The scene resumes at checkpoint c0000."), scene=scene))
    assert result["status"] == "completed"
    assert result["input"] == {"images_sent": 0, "images_reused_from_history": 2, "notes_sent": 1,
                               "brief": "reuse_history"}
    body = second.creates[0]
    assert len(R.input_images(body)) == 2                   # the history's two photos, not four
    texts = user_texts(body)
    assert texts.count("Owner note (verbatim):\n" + note) == 1
    assert sum(text.startswith("Blender product brief (verbatim):") for text in texts) == 1
    assert any("The scene resumes at checkpoint c0000." in text for text in texts)
    assert_money_invariants(job, RUN2, second)

    job, sha3 = make_job(tmp_path, tools=tools, settings=SETTINGS, run_id=RUN3, notes=[note])
    third = R.ScriptedOpenAI([R.message("Third run done.")], id_start=201)
    result = run(L.run_astra(spec_for(job, RUN3, sha3, tools, third, resume=True, notes=[note]), scene=scene))
    assert result["input"]["notes_sent"] == 0 and result["input"]["images_sent"] == 0
    assert user_texts(third.creates[0]).count("Owner note (verbatim):\n" + note) == 1     # once, from history
    assert_clean_receipts(job)


def test_a_lost_mutating_call_is_rolled_back_on_resume_and_named_in_the_invocation(tmp_path):
    scene = FakeScene()
    _, crashing = make_tools(scene, crash_after="execute_blender_code")
    job, sha = make_job(tmp_path, tools=crashing, settings=SETTINGS)
    first = R.ScriptedOpenAI([R.tool_call("execute_blender_code", {"code": "bpy.ops.mesh.primitive_cube_add()",
                                                                   "label": "cube"}), R.message("never sent")])
    with pytest.raises(SimulatedCrash):
        run(L.run_astra(spec_for(job, RUN, sha, crashing, first), scene=scene))
    crashed = result_of(job, RUN)
    assert crashed["status"] == "failed" and crashed["error"]["type"] == "SimulatedCrash"
    assert scene.head == "c0001" and scene.entries[0]["call_id"] == "call_0001"     # the effect stayed

    _, tools = make_tools(scene)
    job, sha2 = make_job(tmp_path, tools=tools, settings=SETTINGS, run_id=RUN2)
    second = R.ScriptedOpenAI([R.message("Rebuilt the cube.")], id_start=101)
    result = run(L.run_astra(spec_for(job, RUN2, sha2, tools, second, resume=True), scene=scene))
    assert result["status"] == "completed"
    assert scene.restored and scene.restored[0][0] == "c0000"
    sentence = ("Your previous run ended during `execute_blender_code` (call `call_0001`); its effect was rolled back; "
                "the scene is checkpoint c0002.")
    assert result["rollback"]["call_id"] == "call_0001" and result["rollback"]["scene_checkpoint"] == "c0002"
    assert any(sentence in text for text in user_texts(second.creates[0]))
    assert result["input"]["images_sent"] == 0              # run 1's input was persisted before its first request

    job, sha3 = make_job(tmp_path, tools=tools, settings=SETTINGS, run_id=RUN3)
    third = R.ScriptedOpenAI([R.message("ok")], id_start=201)
    result = run(L.run_astra(spec_for(job, RUN3, sha3, tools, third, resume=True), scene=scene))
    assert result["rollback"] is None and len(scene.restored) == 1       # a rollback is applied once


def test_the_dangling_call_backstop_answers_an_unanswered_call(tmp_path, monkeypatch):
    scene, tools = make_tools()
    job, sha = make_job(tmp_path, tools=tools, settings=SETTINGS)
    run(L.run_astra(spec_for(job, RUN, sha, tools, R.ScriptedOpenAI([R.message("done")])), scene=scene))
    session = L.BlobSession(L.SESSION_ID, job / "session" / "session.sqlite", blobs=BlobStore(job / "blobs"))
    try:          # a dangling call 0.22.3 never leaves (test_loop_sdk_pins), injected to exercise the backstop
        run(session.add_items([{"type": "function_call", "id": "fc_x", "call_id": "call_dangling",
                                "name": "scene_summary", "arguments": "{}", "status": "completed"}]))
    finally:
        session.close()

    job, sha2 = make_job(tmp_path, tools=tools, settings=SETTINGS, run_id=RUN2)
    second = R.ScriptedOpenAI([R.message("ok")], id_start=101)
    result = run(L.run_astra(spec_for(job, RUN2, sha2, tools, second, resume=True), scene=scene))
    assert result["status"] == "completed" and result["dangling_repaired"] == ["call_dangling"]
    answered = [item for item in second.creates[0]["input"] if item.get("type") == "function_call_output"]
    assert answered == [{"type": "function_call_output", "call_id": "call_dangling", "output": L.DANGLING_OUTPUT}]
    assert second.violations == []

    # without the backstop 0.22.3 silently drops the unanswered call from the request (session_persistence.py:507-511
    # -> items.py:171-271 drop_orphan_function_calls): no 400, but Astra never learns the call existed
    monkeypatch.setattr(L, "repair_dangling_calls", lambda session, items: asyncio.sleep(0, result=[]))
    other = tmp_path / "other"
    job, sha = make_job(other, tools=tools, settings=SETTINGS)
    run(L.run_astra(spec_for(job, RUN, sha, tools, R.ScriptedOpenAI([R.message("done")])), scene=FakeScene()))
    session = L.BlobSession(L.SESSION_ID, job / "session" / "session.sqlite", blobs=BlobStore(job / "blobs"))
    try:
        run(session.add_items([{"type": "function_call", "id": "fc_y", "call_id": "call_dangling",
                                "name": "scene_summary", "arguments": "{}", "status": "completed"}]))
    finally:
        session.close()
    job, sha2 = make_job(other, tools=tools, settings=SETTINGS, run_id=RUN2)
    silent = R.ScriptedOpenAI([R.message("ok")], id_start=101)
    result = run(L.run_astra(spec_for(job, RUN2, sha2, tools, silent, resume=True), scene=FakeScene()))
    assert result["status"] == "completed" and silent.violations == []
    assert not any(item.get("call_id") == "call_dangling" for item in silent.creates[0]["input"])


def output(call_id):
    return {"type": "function_call_output", "call_id": call_id, "output": "ok"}


def call(call_id, tool="execute_blender_code", before="c0000", mutating=True):
    return {"call_id": call_id, "tool": tool, "mutating": mutating, "checkpoint_before": before}


def test_the_blob_session_keeps_no_image_bytes_on_disk_and_restores_them_exactly(tmp_path):
    image = data_url(png_bytes(3, 3, seed=5))
    item = {"role": "user", "content": [{"type": "input_text", "text": "look"},
                                        {"type": "input_image", "image_url": image, "detail": "high"}]}
    original = json.loads(json.dumps(item))
    blobs = BlobStore(tmp_path / "blobs")

    async def scenario():
        session = L.BlobSession(L.SESSION_ID, tmp_path / "session.sqlite", blobs=blobs)
        try:
            await session.add_items([item, {"type": "function_call_output", "call_id": "c",
                                            "output": [{"type": "input_image", "image_url": image}]}])
            assert item == original                           # the caller's item is never modified
            items = await session.get_items()
            popped = await session.pop_item()
        finally:
            session.close()
        return items, popped
    items, popped = run(scenario())
    assert items[0] == original and popped["output"][0]["image_url"] == image
    raw = (tmp_path / "session.sqlite").read_bytes()
    assert b"data:image" not in raw and b"g3d-blob:v1:image/png:" in raw
    assert len(list((tmp_path / "blobs").iterdir())) == 1
    for blob in (tmp_path / "blobs").iterdir():
        blob.write_bytes(b"tampered")

    async def broken():
        session = L.BlobSession(L.SESSION_ID, tmp_path / "session.sqlite", blobs=blobs)
        try:
            await session.get_items()
        finally:
            session.close()
    with pytest.raises(L.SessionBlobMissing):          # a session that cannot be restored exactly never sends
        run(broken())


def test_the_rollback_rule_in_isolation():
    user = {"role": "user", "content": "brief"}
    assert L.plan_rollback([user], []) is None
    assert L.plan_rollback([user, output("a"), output("b")], [call("a"), call("b", before="c0001")]) is None
    plan = L.plan_rollback([user, output("a")], [call("a"), call("b", before="c0001"), call("c", before="c0002")])
    assert (plan["call_id"], plan["checkpoint_before"], plan["lost_calls"], plan["last_output_call_id"]) == (
        "b", "c0001", ["b", "c"], "a")
    # a read-only tool (not journaled) answered after the last mutation: L is not in the journal, nothing is lost
    assert L.plan_rollback([user, output("a"), output("summary")], [call("a")]) is None
    # unrecorded calls that changed nothing need no rollback; the first one that changed the scene sets the target
    plan = L.plan_rollback([user, output("a")], [call("a"), call("b", mutating=False, before="c0001"),
                                                 call("c", before="c0001")])
    assert (plan["call_id"], plan["checkpoint_before"]) == ("c", "c0001")
    # a rollback already applied is a boundary: the next resume does not roll back again
    assert L.plan_rollback([user], [call("x"), {"kind": "rollback", "checkpoint": "c0000"}]) is None
    with pytest.raises(L.RunRefused):
        L.plan_rollback([user], [call("x", before=None)])
    assert L.dangling_calls([user, {"type": "function_call", "call_id": "a"}, output("a"),
                             {"type": "function_call", "call_id": "b"}]) == ["b"]


def test_a_dangling_journaled_mutating_call_is_rolled_back_before_it_is_answered(tmp_path):
    """The repair must not hide a lost mutation from the rollback rule: planned on the history as found."""
    scene, tools = make_tools()
    job, sha = make_job(tmp_path, tools=tools, settings=SETTINGS)
    run(L.run_astra(spec_for(job, RUN, sha, tools, R.ScriptedOpenAI([R.message("done")])), scene=scene))
    scene.mutate("call_lost", "execute_blender_code")          # it ran and changed the scene: c0000 -> c0001
    session = L.BlobSession(L.SESSION_ID, job / "session" / "session.sqlite", blobs=BlobStore(job / "blobs"))
    try:
        run(session.add_items([{"type": "function_call", "id": "fc_lost", "call_id": "call_lost",
                                "name": "execute_blender_code", "arguments": '{"code": "x", "label": ""}',
                                "status": "completed"}]))
    finally:
        session.close()
    job, sha2 = make_job(tmp_path, tools=tools, settings=SETTINGS, run_id=RUN2)
    second = R.ScriptedOpenAI([R.message("ok")], id_start=101)
    result = run(L.run_astra(spec_for(job, RUN2, sha2, tools, second, resume=True), scene=scene))
    assert result["status"] == "completed"
    assert result["rollback"]["call_id"] == "call_lost" and scene.restored[0][0] == "c0000"
    assert result["dangling_repaired"] == ["call_lost"]
    assert any("call `call_lost`); its effect was rolled back" in text for text in user_texts(second.creates[0]))


def test_the_replay_transport_rejects_an_unanswered_call_like_the_api():
    """The instrument itself: a request whose input carries a function_call without its output gets the API's 400."""
    import httpx2
    transport = R.ScriptedOpenAI([R.message("never")])
    body = {"model": "gpt-6-astra", "input": [{"type": "function_call", "call_id": "call_x", "name": "a",
                                               "arguments": "{}"}]}
    transport.handle(httpx2.Request("POST", "https://api.openai.com/v1/responses/input_tokens", json=body))
    answer = transport.handle(httpx2.Request("POST", "https://api.openai.com/v1/responses", json=body))
    assert answer.status_code == 400 and answer.json()["error"]["param"] == "input"
    assert transport.violations == ["create 1: No tool output found for function call call_x."]


# =========================================================================== refusals before anything is spent


def refusal_leaves_nothing(job, run_id, transport):
    run_dir = job / "runs" / run_id
    assert transport.counts == [] and transport.creates == []
    for name in ("budget.json", "result.json", "run-config.json", "input-receipt.json", "transport"):
        assert not (run_dir / name).exists(), name
    assert not (job / "heartbeat.json").exists()
    assert not (B.PAID_RUN_LOCK_ROOT / ".paid-run.lock").exists()


def test_a_missing_approval_refuses_with_exit_3_and_sends_nothing(tmp_path):
    scene, tools = make_tools()
    job, sha = make_job(tmp_path, tools=tools, settings=SETTINGS, approve=False)
    transport = R.ScriptedOpenAI([R.message("never")])
    with pytest.raises(ApprovalRefused) as refused:
        run(L.run_astra(spec_for(job, RUN, sha, tools, transport), scene=scene))
    assert (refused.value.code, refused.value.exit_code) == ("missing", 3)
    refusal_leaves_nothing(job, RUN, transport)
    assert not (job / "session").exists() and scene.events == []


def test_a_stale_request_refuses_with_exit_3(tmp_path):
    scene, tools = make_tools()
    job, sha = make_job(tmp_path, tools=tools, settings=SETTINGS)
    transport = R.ScriptedOpenAI([R.message("never")])
    with pytest.raises(ApprovalRefused) as refused:
        run(L.run_astra(spec_for(job, RUN, "0" * 64, tools, transport), scene=scene))
    assert refused.value.code == "stale"
    refusal_leaves_nothing(job, RUN, transport)


@pytest.mark.parametrize("change", ["instructions", "tools", "settings"])
def test_the_approval_binds_the_instructions_tools_and_settings_actually_sent(tmp_path, change):
    scene, tools = make_tools()
    job, sha = make_job(tmp_path, tools=tools, settings=SETTINGS)
    transport = R.ScriptedOpenAI([R.message("never")])
    extra = {}
    if change == "instructions":
        extra["instructions"] = "Different instructions."
    elif change == "tools":
        tools = tools[:2]
    else:
        extra["settings"] = L.RunSettings(effort="max", max_output_tokens=8192, max_turns=10)
    with pytest.raises(ApprovalRefused) as refused:
        run(L.run_astra(spec_for(job, RUN, sha, tools, transport, **extra), scene=scene))
    assert refused.value.code == "stale"
    refusal_leaves_nothing(job, RUN, transport)


def test_a_consumed_approval_refuses_a_second_run(tmp_path):
    scene, tools = make_tools()
    job, sha = make_job(tmp_path, tools=tools, settings=SETTINGS)
    assert run(L.run_astra(spec_for(job, RUN, sha, tools, R.ScriptedOpenAI([R.message("done")])),
                           scene=scene))["status"] == "completed"
    again = R.ScriptedOpenAI([R.message("never")])
    with pytest.raises(ApprovalRefused) as refused:
        run(L.run_astra(spec_for(job, RUN, sha, tools, again, resume=True), scene=scene))
    assert refused.value.code == "consumed" and again.counts == [] and again.creates == []


def test_resume_must_match_the_session_file(tmp_path):
    scene, tools = make_tools()
    job, sha = make_job(tmp_path, tools=tools, settings=SETTINGS)
    transport = R.ScriptedOpenAI([R.message("never")])
    with pytest.raises(L.RunRefused) as refused:
        run(L.run_astra(spec_for(job, RUN, sha, tools, transport, resume=True), scene=scene))
    assert refused.value.exit_code == 2
    refusal_leaves_nothing(job, RUN, transport)
    run(L.run_astra(spec_for(job, RUN, sha, tools, R.ScriptedOpenAI([R.message("done")])), scene=scene))
    job, sha2 = make_job(tmp_path, tools=tools, settings=SETTINGS, run_id=RUN2)
    fresh = R.ScriptedOpenAI([R.message("never")])
    with pytest.raises(L.RunRefused, match="already has a session"):
        run(L.run_astra(spec_for(job, RUN2, sha2, tools, fresh), scene=scene))
    assert fresh.creates == [] and not (job / "runs" / RUN2 / "budget.json").exists()


def test_the_section_7_timeout_table_and_the_sdk_timeout_rule():
    assert L.hard_timeout_s("execute_blender_code") == 150.0                 # soft 120 + 30
    assert L.hard_timeout_s("execute_blender_code", soft_timeout_s=2) == 32.0
    assert L.hard_timeout_s("execute_blender_code", soft_timeout_s=0.1) == 30.5    # soft clamped to 0.5..600
    assert L.hard_timeout_s("execute_blender_code", soft_timeout_s=10_000) == 630.0
    from glasses3d.agent import tool_timeouts as T
    for name in ("execute_blender_code", "seat_lens", "check_lens_seating", "render_views", "export_glb",
                 "restore_checkpoint", "fit_cameras", "deliver", "read_report", "budget_status",
                 "compare_look_to_photos", "finish"):
        assert L.sdk_timeout_s(name) == T.inner_steps_s(name) + T.HOST_WORK_MARGIN_S + L.SDK_TIMEOUT_MARGIN_S
    # Old 760 s look budget omitted side-light; finish also needs its diagnostic fallback.
    assert L.sdk_timeout_s("compare_look_to_photos") > T.EXPORT_DEADLINE_S + 3 * T.LOOK_CAPTURE_DEADLINE_S
    assert L.sdk_timeout_s("finish") > T.EXPORT_DEADLINE_S + T.AR_CAPTURE_DEADLINE_S + 2 * T.LOOK_CAPTURE_DEADLINE_S
    with pytest.raises(KeyError):
        L.hard_timeout_s("unknown_tool")

    async def render_views(view: str) -> str:
        """Render.

        Args:
            view: Which.
        """
        return view
    tool = function_tool(timeout=L.sdk_timeout_s("render_views"))(render_views)
    assert (tool.timeout_seconds, tool.timeout_behavior) == (L.sdk_timeout_s("render_views"), "error_as_result")


def test_the_tool_contract_is_checked_both_ways():
    _, tools = make_tools()           # scene_summary, execute_blender_code, slow_tool
    instructions = "Use `scene_summary` first, then `execute_blender_code`; `slow_tool` waits."
    good = L.ToolContract(named=frozenset({"scene_summary", "execute_blender_code", "slow_tool"}))
    assert L.contract_problems(good, instructions=instructions, brief="A frame.", tools=tools) == []
    bad = L.ToolContract(named=frozenset({"scene_summary", "execute_blender_code", "render_views"}),
                         gated=frozenset({"slow_tool"}))
    problems = L.contract_problems(bad, instructions=instructions + " Treat streaks as hypotheses.",
                                   brief="Supplementary photos follow.", tools=tools)
    assert problems == [
        "the instructions name `render_views`, which is not registered",
        "`slow_tool` is registered but the instructions do not name it",
        "`render_views` is listed as named but the instructions never mention it",
        "gated tool `slow_tool` is registered or named",
        "removed phrase 'supplementary' appears in the brief",
        "removed phrase 'hypothes' appears in the instructions"]


def test_a_broken_contract_or_a_full_disk_refuses_before_anything_is_spent(tmp_path, monkeypatch):
    scene, tools = make_tools()
    job, sha = make_job(tmp_path, tools=tools, settings=SETTINGS)
    transport = R.ScriptedOpenAI([R.message("never")])
    contract = L.ToolContract(named=frozenset({"scene_summary"}))
    with pytest.raises(L.RunRefused, match="tool contract"):
        run(L.run_astra(spec_for(job, RUN, sha, tools, transport, contract=contract), scene=scene))
    refusal_leaves_nothing(job, RUN, transport)
    monkeypatch.setattr(L.shutil, "disk_usage", lambda path: __import__("collections").namedtuple(
        "usage", "total used free")(10 << 30, 10 << 30, 200 << 20))
    with pytest.raises(L.RunRefused, match="200 MiB free"):
        run(L.run_astra(spec_for(job, RUN, sha, tools, transport), scene=scene))
    refusal_leaves_nothing(job, RUN, transport)


# =========================================================================== the client seam


def test_model_clients_is_offline_only_under_the_offline_conditions(tmp_path, monkeypatch):
    scene, tools = make_tools()
    transport = R.ScriptedOpenAI([])
    spec = spec_for(tmp_path, RUN, "a" * 64, tools, transport)
    clients = L.model_clients(spec)
    assert isinstance(clients, L.OfflineClients) and clients.secrets == [OFFLINE_KEY]
    spec.offline_api_key = "sk-proj-not-offline"
    with pytest.raises(L.RunRefused):
        L.model_clients(spec)
    spec.offline_api_key, spec.offline_handler = OFFLINE_KEY, None
    with pytest.raises(L.RunRefused, match="offline transport"):
        L.model_clients(spec)
    monkeypatch.delenv("G3D_OFFLINE")
    spec.offline_handler = transport.handle
    with pytest.raises(L.RunRefused, match="without G3D_OFFLINE"):
        L.model_clients(spec)
    spec.offline_handler = None
    with pytest.raises(L.RunRefused, match="OPENAI_API_KEY"):       # no key anywhere: refused, nothing built
        L.model_clients(spec)


def test_the_production_client_settings_and_the_key_loader(tmp_path, monkeypatch):
    assert L.production_client_options(count=False) == {
        "base_url": "https://api.openai.com/v1", "max_retries": 0, "timeout": 900.0,
        "http_client": {"follow_redirects": False}}
    assert L.production_client_options(count=True)["timeout"] == 60.0
    dotenv = tmp_path / "test.env"
    dotenv.write_text("OPENAI_API_KEY=sk-offline-from-dotenv\n", encoding="utf-8")
    assert L.load_api_key(dotenv) == "sk-offline-from-dotenv"
    assert L.load_api_key(None) is None
    monkeypatch.setenv("OPENAI_API_KEY", "sk-offline-from-environment")
    assert L.load_api_key(None) == "sk-offline-from-environment"


def test_the_real_production_class_refuses_while_offline_before_building_anything():
    """REAL_PRODUCTION is the class itself (the autouse fixture swaps the module attribute); its __init__ refuses
    under G3D_OFFLINE=1 before any client exists (the tripwire would count a real transport)."""
    for key in ("sk-proj-looks-real", OFFLINE_KEY, ""):
        with pytest.raises(L.RunRefused):
            REAL_PRODUCTION(api_key=key)
