"""Run receipts (agent/receipts.py, glasses3d/plan/archive/20261019/WP2_DESIGN.md section 9): the Studio's ReviewLog transport, reasoning-evidence and
stop tests (source-before/tests/test_blender_agent_transport.py, test_blender_agent_reasoning_evidence.py,
test_blender_studio_stop.py) ported to the glasses3d guard, plus the per-call tool receipts, blobs and transcript."""
import asyncio
import base64
import hashlib
import json
from types import SimpleNamespace

import httpx2
import pytest

import agents  # Required pinned SDK: import failures must fail this suite.
from agents.tool_context import ToolContext  # noqa: E402
from agents.usage import Usage  # noqa: E402
from openai import DefaultAsyncHttpxClient  # noqa: E402

from glasses3d.agent.budget import BudgetGuard, BudgetGuardError  # noqa: E402
from glasses3d.agent.receipts import (MASK, REDACTED, BlobStore, Cleaner, ReviewLog, StopRequested,  # noqa: E402
                                      summary_record)
from glasses3d.tests.agent.support import fake_counter, ledger  # noqa: E402

INFERENCE_URL = "https://api.openai.com/v1/responses"
run = asyncio.run


def guard_in(tmp_path, cap="1"):
    client, _ = fake_counter()
    return BudgetGuard(client, tmp_path / "budget.json", cap)


def make_log(tmp_path, **kwargs):
    return ReviewLog(tmp_path / "run", blobs_dir=tmp_path / "blobs", **kwargs)


def inference_request(**changes):
    payload = {"model": "gpt-6-astra", "service_tier": "default", "max_output_tokens": 100, "input": "fixture"}
    payload.update(changes)
    return httpx2.Request("POST", INFERENCE_URL, json=payload)


def terminal_provider(**changes):
    payload = {"id": "resp_incomplete", "model": "gpt-6-astra", "status": "incomplete",
               "incomplete_details": {"reason": "max_output_tokens"},
               "usage": {"input_tokens": 1000, "output_tokens": 100, "total_tokens": 1100,
                         "input_tokens_details": {"cached_tokens": 900, "cache_write_tokens": 100},
                         "output_tokens_details": {"reasoning_tokens": 20}}}
    return {**payload, **changes}


def tool_context(name, call_id, arguments):
    return ToolContext(context=None, tool_name=name, tool_call_id=call_id, tool_arguments=json.dumps(arguments)
                       if not isinstance(arguments, str) else arguments)


# =========================================================================== ported transport tests


def test_incomplete_transport_settles_the_guard_before_the_sdk_fails_and_cannot_double_settle(tmp_path):
    async def scenario():
        log = make_log(tmp_path, guard=guard_in(tmp_path))
        await log.before_request(inference_request())
        original = ledger(log.guard.ledger_path)["rows"][0]
        provider = terminal_provider()
        await log.after_response(httpx2.Response(200, json=provider, headers={"x-request-id": "req_fixture"}))
        row = ledger(log.guard.ledger_path)["rows"][0]
        assert all(row[key] == original[key] for key in ("sequence", "time", "input_tokens", "reserved_micro_usd"))
        assert (row["outcome"], row["settled_micro_usd"]) == ("settled", 7150)
        saved = log.guard.ledger_path.read_bytes()
        sdk = SimpleNamespace(response_id=provider["id"], request_id="req_fixture", output=[],
                              usage=Usage(requests=1, **provider["usage"]))
        await log.on_llm_end(None, None, sdk)            # a later SDK may still deliver it: cross-check only
        assert log.guard.ledger_path.read_bytes() == saved
        sdk.response_id = "resp_different"
        with pytest.raises(BudgetGuardError, match="differs"):
            await log.on_llm_end(None, None, sdk)
        assert ledger(log.guard.ledger_path)["rows"] == json.loads(saved)["rows"]
        return log
    log = run(scenario())
    assert (log.run_dir / "transport" / "0001-request.json").is_file()
    assert json.loads((log.run_dir / "transport" / "0001-response.json").read_text())["status"] == "incomplete"
    assert "cut off (max_output_tokens)" in log.transcript_path.read_text(encoding="utf-8")


@pytest.mark.parametrize("provider,status,raises", [
    (terminal_provider(usage=None), 200, True),
    (terminal_provider(usage={"input_tokens": 0, "output_tokens": 0}), 200, True),
    (terminal_provider(usage={"input_tokens": 1000, "output_tokens": -1}), 200, True),
    (terminal_provider(), 502, False),
    (terminal_provider(model="unknown"), 200, True),
    (terminal_provider(status="in_progress"), 200, True),
])
def test_unverified_usage_never_releases_the_hold_and_the_answer_is_still_archived(tmp_path, provider, status,
                                                                                    raises):
    async def scenario():
        log = make_log(tmp_path, guard=guard_in(tmp_path))
        await log.before_request(inference_request())
        answer = httpx2.Response(status, json=provider)
        if raises:
            with pytest.raises(BudgetGuardError):
                await log.after_response(answer)
        else:
            await log.after_response(answer)
        return log
    log = run(scenario())
    row = ledger(log.guard.ledger_path)["rows"][0]
    assert row["outcome"] == "unknown" and row["settled_micro_usd"] is None
    assert (log.run_dir / "transport" / "0001-response.json").is_file()
    assert log._inflight is None


def test_the_transport_reserves_before_dispatch_and_archives_exact_image_bytes_once(tmp_path):
    image = b"offline image bytes"
    secret = "sk-offline-test-secret"
    url = "data:image/png;base64," + base64.b64encode(image).decode()
    payload = {"model": "gpt-6-astra", "input": [{"type": "input_image", "image_url": url},
                                                 {"type": "input_image", "image_url": url}]}
    provider = {"id": "resp_offline", "status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"},
                "usage": {"input_tokens": 123, "output_tokens": 456}}
    calls = []

    class RecordingGuard:
        open_sequence = 7

        async def before_request(self, request):
            calls.append("reserved")
            await request.aread()

        async def after_response(self, response):
            calls.append("settled")

    log = make_log(tmp_path, guard=RecordingGuard(), secrets=[secret])

    async def handle(request):
        assert calls == ["reserved"]
        calls.append("dispatched")
        assert json.loads(request.content) == payload
        assert request.headers["Authorization"] == "Bearer " + secret
        return httpx2.Response(200, json=provider, headers={"x-request-id": "req_offline"})

    async def scenario():
        async with DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handle), event_hooks={
                "request": [log.before_request], "response": [log.after_response]}) as client:
            response = await client.post(INFERENCE_URL, json=payload, headers={"Authorization": "Bearer " + secret})
            assert response.json() == provider and await response.aread() == response.content
    run(scenario())
    assert calls == ["reserved", "dispatched", "settled"]
    saved = json.loads((log.run_dir / "transport" / "0007-request.json").read_text())
    receipt = saved["input"][0]["image_url"]
    assert receipt == {"sha256": hashlib.sha256(image).hexdigest(), "bytes": len(image), "mime": "image/png"}
    assert saved["input"][1]["image_url"] == receipt
    assert BlobStore(tmp_path / "blobs").get(receipt["sha256"], "image/png") == image
    assert len(list((tmp_path / "blobs").iterdir())) == 1
    assert json.loads((log.run_dir / "transport" / "0007-response.json").read_text()) == provider
    events = [json.loads(line) for line in log.events_path.read_text().splitlines()]
    assert [item["event"] for item in events] == ["api_request", "api_response"]
    assert events[-1]["request_id"] == "req_offline" and events[-1]["status"] == "incomplete"
    assert events[-1]["usage"] == provider["usage"]
    for path in (tmp_path / "run").rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            assert secret not in text and "data:image" not in text


def test_reasoning_summaries_are_public_text_only_and_redacted(tmp_path):
    log = make_log(tmp_path, secrets=["private-key-123"])
    log.interactions = 3
    provider = {"id": "response3", "status": "completed", "output": [
        {"type": "reasoning", "id": "reason3", "summary": [{"type": "summary_text",
                                                            "text": "Inspect coating private-key-123"}],
         "encrypted_content": "opaque-internal-content"},
        {"type": "function_call", "call_id": "call3", "name": "inspect_export"}]}
    run(log.after_response(httpx2.Response(200, json=provider, headers={"x-request-id": "request3"})))
    record = json.loads(log.summaries_path.read_text())
    assert record["interaction"] == 3 and record["response_id"] == "response3"
    assert record["summaries"][0]["text"] == [f"Inspect coating {REDACTED}"]
    assert record["tool_calls"][0]["call_id"] == "call3"
    assert "opaque-internal-content" not in str(record)
    archived = (log.run_dir / "transport").iterdir().__next__().read_text()
    assert "opaque-internal-content" not in archived and MASK in archived


@pytest.mark.parametrize("body", [b"[1,2]", b"null", b"gateway unavailable"])
def test_an_error_payload_does_not_mask_the_sdk_response(tmp_path, body):
    log = make_log(tmp_path)
    response = httpx2.Response(502, content=body)
    run(log.after_response(response))
    assert response.content == body
    event = json.loads(log.events_path.read_text())
    assert event["http_status"] == 502 and event["status"] is None
    assert "HTTP 502" in log.transcript_path.read_text(encoding="utf-8")


def test_summary_record_keeps_only_public_summaries_correlated_to_actions():
    result = summary_record({"id": "response1", "status": "completed", "output": [
        {"type": "reasoning", "id": "reason1", "summary": [{"type": "summary_text", "text": "Compare the coating."}],
         "content": "not a public summary", "encrypted_content": "opaque"},
        {"type": "function_call", "call_id": "call1", "name": "inspect_export", "arguments": "not needed"}]},
        interaction=7, request_id="request1")
    assert result["summaries"] == [{"item_id": "reason1", "text": ["Compare the coating."]}]
    assert result["tool_calls"] == [{"call_id": "call1", "name": "inspect_export"}]
    assert "opaque" not in str(result) and "not a public summary" not in str(result)
    assert result["interaction"] == 7 and result["availability"] == "returned"
    empty = summary_record({"id": "response2", "output": [{"type": "reasoning", "summary": []}]}, interaction=8,
                           request_id=None)
    assert empty["availability"] == "not_returned" and empty["summaries"] == []


# =========================================================================== stop


def test_a_stop_prevents_a_new_request_before_the_budget_reservation(tmp_path):
    flag = tmp_path / "stop.requested"
    flag.touch()
    calls = []

    class Guard:
        async def before_request(self, request):
            calls.append("reserved")
    log = make_log(tmp_path, guard=Guard(), stop_file=flag)
    with pytest.raises(StopRequested):
        run(log.before_request(inference_request()))
    assert calls == [] and log.stop_observed
    assert not (log.run_dir / "transport").exists()


def test_a_stop_blocks_the_next_model_request_and_tool_but_never_a_finished_tool(tmp_path):
    flag = tmp_path / "stop.requested"
    log = make_log(tmp_path, stop_file=flag)
    tool = SimpleNamespace(name="execute_blender_code")
    context = tool_context("execute_blender_code", "call_1", {"code": "x = 1"})
    run(log.on_llm_start(None, None, "", []))
    run(log.on_tool_start(context, None, tool))
    flag.touch()                                    # the owner's stop arrives while the tool runs
    run(log.on_tool_end(context, None, tool, {"status": "ok"}))      # its turn must still be persisted
    with pytest.raises(StopRequested):
        run(log.on_llm_start(None, None, "", []))
    with pytest.raises(StopRequested):
        run(log.on_tool_start(tool_context("scene_summary", "call_2", {}), None, SimpleNamespace(name="x")))
    assert log.interactions == 1
    assert [event["event"] for event in map(json.loads, log.events_path.read_text().splitlines())].count(
        "stop_requested") == 1


def test_without_a_stop_file_the_hooks_count_model_requests(tmp_path):
    log = make_log(tmp_path)
    run(log.on_llm_start(None, None, "", []))
    assert log.interactions == 1 and not log.stop_observed


def test_a_request_hook_without_a_guard_refuses_to_send(tmp_path):
    with pytest.raises(RuntimeError, match="no budget guard"):
        run(make_log(tmp_path).before_request(inference_request()))


# =========================================================================== tool receipts and transcript


def test_tool_receipts_arguments_code_result_journal_and_transcript(tmp_path):
    states = []

    class Beat:
        def set_state(self, state, **fields):
            states.append(state)

        def update(self, **fields):
            pass
    log = make_log(tmp_path, secrets=["sk-offline-hidden-key"], heartbeat=Beat())
    tool = SimpleNamespace(name="execute_blender_code")
    code = "import bpy\nprint('```nested fence```')\nkey = 'sk-offline-hidden-key'"
    context = tool_context("execute_blender_code", "call_7", {"code": code, "label": "cube", "soft_timeout_s": 5})
    run(log.on_llm_start(None, None, "", []))
    run(log.on_tool_start(context, None, tool))
    assert log.record_journal("call_7", {"checkpoint_before": "c0003", "fingerprint_before": "a" * 64,
                                         "fingerprint_after": "b" * 64, "seconds": 0.31, "status": "ok"})
    run(log.on_tool_end(context, None, tool, {"status": "ok", "checkpoint": "c0004", "seconds": 0.31,
                                              "sheet": "work/renders/0004/sheet.png", "notes": "x" * 200}))
    directory = log.run_dir / "tools" / "0001-execute_blender_code"
    arguments = json.loads((directory / "arguments.json").read_text())
    assert arguments["arguments"] == {"label": "cube", "soft_timeout_s": 5} and arguments["call_id"] == "call_7"
    assert arguments["code_sha256"] == hashlib.sha256(code.encode()).hexdigest()
    assert (directory / "issued-code.py").read_text(encoding="utf-8") == code.replace("sk-offline-hidden-key",
                                                                                     REDACTED)
    assert json.loads((directory / "journal.json").read_text())["checkpoint_before"] == "c0003"
    result = json.loads((directory / "result.json").read_text())
    assert result["result"]["checkpoint"] == "c0004" and result["seconds"] >= 0
    transcript = log.transcript_path.read_text(encoding="utf-8")
    assert "````python\n" in transcript                  # a longer fence than the code's own backticks
    assert "- status: `ok`" in transcript and "- checkpoint: `c0004`" in transcript
    assert "[sheet.png](../work/renders/0004/sheet.png)" in transcript and "x" * 200 not in transcript
    assert "sk-offline-hidden-key" not in transcript
    assert states == ["model_wait", "tool:execute_blender_code", "idle"]
    assert log.tool_calls == [{"n": 1, "call_id": "call_7", "name": "execute_blender_code", "status": "ok"}]
    assert log.record_journal("call_unknown", {}) is None


def test_malformed_tool_arguments_and_string_results_are_kept_safely(tmp_path):
    log = make_log(tmp_path)
    tool = SimpleNamespace(name="scene_summary")
    context = tool_context("scene_summary", "call_8", "{not json")
    run(log.on_tool_start(context, None, tool))
    run(log.on_tool_end(context, None, tool, "An error occurred while running the tool. Please try again."))
    directory = log.run_dir / "tools" / "0001-scene_summary"
    assert json.loads((directory / "arguments.json").read_text())["arguments"] == {"unparsed_arguments": "{not json"}
    assert json.loads((directory / "result.json").read_text())["result"].startswith("An error occurred")
    assert log.tool_calls[0]["status"] == "returned"


def test_a_provider_rejection_and_an_unknown_outcome_read_plainly_in_the_transcript(tmp_path):
    log = make_log(tmp_path)
    run(log.after_response(httpx2.Response(429, json={"error": {"type": "requests", "code": "rate_limit_exceeded"}})))
    run(log.after_response(httpx2.Response(520, content=b"error code: 520")))
    transcript = log.transcript_path.read_text(encoding="utf-8")
    assert "HTTP 429 (rate_limit_exceeded)" in transcript and "rejected, not billed" in transcript
    assert "HTTP 520" in transcript and "outcome unknown" in transcript
    assert sorted(path.name for path in (log.run_dir / "transport").iterdir()) == [
        "unsequenced-0001-response.json", "unsequenced-0002-response.json"]


def test_a_transcript_failure_is_recorded_and_never_fails_the_hooks(tmp_path, monkeypatch):
    log = make_log(tmp_path)
    tool = SimpleNamespace(name="scene_summary")
    context = tool_context("scene_summary", "call_1", {})
    monkeypatch.setattr(log, "_transcript_tool_result", lambda *args: (_ for _ in ()).throw(ValueError("bad path")))
    run(log.on_tool_start(context, None, tool))
    run(log.on_tool_end(context, None, tool, {"status": "ok"}))          # no exception reaches the SDK
    assert log.transcript_errors == ["ValueError: bad path"]
    assert json.loads((log.run_dir / "tools" / "0001-scene_summary" / "result.json").read_text())["result"] == {
        "status": "ok"}                                                    # the JSON receipt is still written


# =========================================================================== blobs and cleaning


def test_the_cleaner_masks_redacts_and_never_keeps_image_text(tmp_path):
    clean = Cleaner(BlobStore(tmp_path / "blobs"), secrets=["sk-offline-abcdefgh", "short"])
    value = {"encrypted_content": "opaque", "signature": "sig", "empty_signature": "",
             "nested": [{"encrypted_content": ""}, {"text": "key sk-offline-abcdefgh here"}],
             "sk-offline-abcdefgh": 1, "raw": b"\x00\x01", "nan": float("nan"), "short": "short stays"}
    out = clean(value)
    assert out["encrypted_content"] == MASK and out["signature"] == MASK and out["empty_signature"] == ""
    assert out["nested"][0]["encrypted_content"] == "" and out["nested"][1]["text"] == f"key {REDACTED} here"
    assert out[REDACTED] == 1 and out["raw"] == {"sha256": hashlib.sha256(b"\x00\x01").hexdigest(), "bytes": 2}
    assert out["nan"] is None and out["short"] == "short stays"         # secrets under 8 characters are ignored
    broken = clean("data:image/png;base64,!!not base64!!")
    assert broken["invalid_base64"] is True and broken["mime"] == "image/png"
    assert base64.b64decode("YWJ=", validate=True) == b"ab" and base64.b64encode(b"ab") == b"YWI="
    noncanonical = clean("data:image/png;base64,YWJ=")    # decodable, but not the canonical text: not stored
    assert noncanonical["invalid_base64"] is True
    assert not (tmp_path / "blobs").exists() or list((tmp_path / "blobs").iterdir()) == []


def test_the_blob_store_writes_once_and_verifies_on_read(tmp_path):
    store = BlobStore(tmp_path / "blobs")
    first = store.put(b"pixels", "image/png")
    second = store.put(b"pixels", "image/PNG")
    assert first == second == {"sha256": hashlib.sha256(b"pixels").hexdigest(), "bytes": 6, "mime": "image/png"}
    assert len(list((tmp_path / "blobs").iterdir())) == 1
    path = store.path(first["sha256"], "image/png")
    path.write_bytes(b"tampered")
    with pytest.raises(ValueError):
        store.get(first["sha256"], "image/png")
    assert store.put(b"pixels", "image/png") and store.get(first["sha256"], "image/png") == b"pixels"   # repaired
    assert store.put_data_url("data:image/jpeg;base64," + base64.b64encode(b"jpg").decode())["mime"] == "image/jpeg"
    assert store.put_data_url("https://example.invalid/a.png") is None
