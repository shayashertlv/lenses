"""Runner decisions through the real SDK, guard and durable conversation."""
from __future__ import annotations

import asyncio
import copy
from dataclasses import replace
from decimal import Decimal
import io
import json
from pathlib import Path
import threading
from types import SimpleNamespace
from uuid import uuid4

import anthropic
import httpx2
from agents import function_tool
from agents.tool_context import ToolContext
import pytest
from PIL import Image

from glasses3d.agent import jobs
from glasses3d.agent.budget import PaidRunLock
from glasses3d.agent.loop import (
    FINISH_NOTE, FATAL_TOOL_ERRORS, SceneHooks, StageRunFailed, StageSpec, ToolContract,
    fatal_tool_errors, plan_rollback,
)
from glasses3d.agent.journal import WorkerFailed
from glasses3d.agent.receipts import BlobStore, StopRequested
from glasses3d.claude_test import runner as R, money, transport
from glasses3d.claude_test.conversation import ClaudeReviewLog, Conversation
from glasses3d.claude_test.images import FileCache
from glasses3d.claude_test.scripted import ScriptTurn, ScriptedClaude


class Scene(SceneHooks):
    def __init__(self, root):
        self.root = root
        self.s = SimpleNamespace(head={"id": "c1", "fingerprint": "head1", "sha256": "f" * 64})
        self.finish = self.finish_review = None
        self._unseen = set()
        self.rows = []
        self.started = self.closed = False
        self.active_run_id = None
        self.owner_edit = None

    def start(self):
        self.started = True

    def save(self):
        pass

    def mark_unseen(self, call_id):
        self._unseen.add(call_id)
        self.finish = self.finish_review = None

    def close(self):
        self.closed = True

    def exit_checkpoint(self):
        return {**self.s.head, "verified": True}

    def journal_entries(self):
        return self.rows


def tools_for(scene):
    @function_tool
    async def touch(ctx: ToolContext, change: bool):
        """Read or change the controlled scene."""
        if change:
            old = scene.s.head["id"]
            scene.s.head = {"id": old + "x", "fingerprint": "new", "sha256": "e" * 64}
            scene.finish = None
            scene.rows.append({"call_id": ctx.tool_call_id, "tool": "touch",
                               "mutating": True, "checkpoint_before": old})
        return json.dumps({"status": "ok", "head": scene.s.head})

    @function_tool
    async def finish(ctx: ToolContext):
        """Finish the controlled fixture."""
        scene.finish = {"status": "accepted", "call_id": ctx.tool_call_id,
                        "checkpoint": scene.s.head["id"]}
        return json.dumps(scene.finish)
    return (touch, finish)


def call(name, id="call1", **arguments):
    return {"type": "tool_use", "id": id, "name": name, "input": arguments}


def turn(*blocks, **kwargs):
    return ScriptTurn(list(blocks), **kwargs)


@pytest.fixture
def root(monkeypatch):
    monkeypatch.setenv("G3D_OFFLINE", "1")
    path = R.WORK_ROOT / "unit" / uuid4().hex
    path.mkdir(parents=True)
    return path


class Harness:
    def __init__(self, root, turns, cutoff="20", *, wire=None):
        self.root = root
        money.write_approval(root / "approval.json", words="OFFLINE TEST ONLY", amount_usd=Decimal(cutoff),
                             model=R.MODEL, effort="max", run_id="test")
        approval = money.consume_approval(root / "approval.json", "test")
        self.ledger = money.Ledger(root / "ledger.jsonl", cutoff_usd=Decimal(cutoff), approval=approval)
        self.wire = wire if wire is not None else ScriptedClaude(turns)
        self.waits = []
        self.files = FileCache(root / "file-cache.json", lambda b, n: self.client.files.upload(
            file=(n, b, "image/png"), **transport.files_upload_params()).id, run_id="test")
        self.client = anthropic.Anthropic(
            api_key=R.OFFLINE_KEY, max_retries=0,
            http_client=transport.guarded_client(
                self.ledger, root / "transport", inner=self.wire.transport(), sleep=self.waits.append,
                file_cache=self.files))
        self.scene = Scene(root)
        self.spec = R.ClaudeRunSpec(
            root, "test", R.ClaudeSettings(cutoff_usd=Decimal(cutoff)),
            "Fixture instructions", tools_for(self.scene), "Owner words", (), "", (),
            None, True, finish_tool="finish")
        self.blobs = BlobStore(root / "blobs")
        self.conversation = Conversation(root / "session.sqlite", blobs=self.blobs)
        self.log = ClaudeReviewLog(root, blobs_dir=root / "blobs", stop_file=root / "stop.requested")
        self.shared = {"requests": 0}
        self.runtime = R._Runtime(
            spec=self.spec, scene=self.scene, conversation=self.conversation, ledger=self.ledger,
            client=self.client, files=self.files, log=self.log, directory=root, shared=self.shared)

    async def drive(self):
        await self.runtime.append({"role": "user", "content": [{"type": "text", "text": "Owner words"}]})
        return await self.runtime.drive()

    def close(self):
        self.conversation.close()
        self.client.close()


def run_harness(root, turns, cutoff="20"):
    harness = Harness(root, turns, cutoff)
    try:
        return harness, asyncio.run(harness.drive())
    finally:
        harness.close()


def test_accepted_finish_needs_no_extra_request(root):
    h, status = run_harness(root, [turn(call("finish"))])
    assert status == "completed"
    assert len(h.wire.requests) == 1
    assert h.runtime.final_output
    h.wire.assert_complete()


def test_crossing_response_finish_can_complete(root):
    h, status = run_harness(root, [turn(call("finish"), usage={"input_tokens": 100, "output_tokens": 100})],
                            cutoff="0.000001")
    assert status == "completed"
    assert h.ledger.settled_usd() > h.ledger.cutoff_usd
    assert not h.ledger.may_start_request()[0]
    assert len(h.wire.requests) == 1


def test_crossing_response_executes_tool_but_blocks_next_request(root):
    h = Harness(root, [turn(call("touch", change=True), usage={"input_tokens": 100, "output_tokens": 100})],
                cutoff="0.000001")
    try:
        with pytest.raises(R.Halt) as error:
            asyncio.run(h.drive())
        assert error.value.status == "budget_limit"
        assert h.scene.s.head["id"] == "c1x"
        assert len(h.wire.requests) == 1
    finally:
        h.close()


def test_text_only_notes_are_exact_and_consecutive(root):
    text = {"type": "text", "text": "Working"}
    h, status = run_harness(root, [
        turn(text, stop_reason="end_turn"), turn(text, stop_reason="end_turn"),
        turn(call("touch", id="reset", change=False)),
        turn(text, stop_reason="end_turn"), turn(text, stop_reason="end_turn"),
        turn(text, stop_reason="end_turn"),
    ])
    assert status == "ended_without_finish"
    assert len(h.runtime.finish_notes) == 4
    assert [r["consecutive"] for r in h.runtime.finish_notes] == [1, 2, 1, 2]
    assert {r["text"] for r in h.runtime.finish_notes} == {FINISH_NOTE.format(tool="finish")}
    assert (root / "events.jsonl").read_text(encoding="utf-8").count('"finish_note"') == 4


@pytest.mark.parametrize("kwargs", [
    {"usage": {"input_tokens": 100, "output_tokens": 100, "unknown_charge": 1}},
    {"missing_usage": True}, {"served_model": "other-model"}, {"truncate": True},
    {"error": {"type": "overloaded_error", "message": "offline injected"}},
])
def test_unknown_usage_never_invokes_proposed_tool(root, kwargs):
    h = Harness(root, [turn(call("touch", change=True), **kwargs)])
    try:
        with pytest.raises(Exception):
            asyncio.run(h.drive())
        assert h.scene.s.head["id"] == "c1"
        assert R._ledger_failure(h.ledger) == "provider_unknown"
        assert len(h.wire.requests) == 1
        assert any(r["event"] == "unknown" for r in R._ledger_rows(h.ledger))
    finally:
        h.close()


@pytest.mark.parametrize("stop_reason", ["max_tokens", "model_context_window_exceeded"])
def test_truncated_output_never_invokes_tool(root, stop_reason):
    h = Harness(root, [turn(call("touch", change=True), stop_reason=stop_reason)])
    try:
        with pytest.raises(R.Halt) as error:
            asyncio.run(h.drive())
        assert error.value.status == "output_limit"
        assert h.scene.s.head["id"] == "c1"
        assert h.ledger.settled_usd() > 0
    finally:
        h.close()


def test_refusal_is_priced_upper_bound_and_draft(root):
    details = {"category": "bio", "explanation": "offline refusal fixture"}
    h = Harness(root, [turn({"type": "text", "text": "Cannot continue"}, stop_reason="refusal",
                           stop_details=details)])
    try:
        with pytest.raises(R.Halt) as error:
            asyncio.run(h.drive())
        assert error.value.status == "provider_rejected"
        assert R._ledger_rows(h.ledger)[-1]["upper_bound"]
        assert R._ledger_rows(h.ledger)[-1]["stop_details"] == details
        response = json.loads((root / "requests/0001-response.json").read_text())
        assert response["stop_details"] == details
        assert len(h.wire.requests) == 1
    finally:
        h.close()


def test_non_refusal_stop_details_are_receipted_and_do_not_end_run(root):
    details = {"category": None, "explanation": "ordinary provider completion metadata"}
    h, status = run_harness(root, [
        turn(call("touch", change=True), stop_details=details),
        turn(call("finish", "f")),
    ])
    assert status == "completed"
    assert h.scene.s.head["id"] == "c1x"
    assert len(h.wire.requests) == 2
    response = json.loads((root / "requests/0001-response.json").read_text())
    assert response["stop_details"] == details
    receipts = [json.loads(path.read_text()) for path in (root / "transport").glob("1-*.json")]
    assert len(receipts) == 1 and receipts[0]["stop_details"] == details
    assert not any(row.get("refusal") for row in R._ledger_rows(h.ledger))


def test_multi_tool_reply_is_serial_one_result_message_and_later_mutation_invalidates_finish(root):
    h, status = run_harness(root, [
        turn(call("finish", "f1"), call("touch", "mutate", change=True)),
        turn(call("finish", "f2")),
    ])
    assert status == "completed"
    result_message = h.wire.requests[1]["messages"][-1]
    assert result_message["role"] == "user"
    assert [b["tool_use_id"] for b in result_message["content"]] == ["f1", "mutate"]
    assert h.scene.finish["call_id"] == "f2"
    assert len(h.wire.requests) == 2


def test_requests_preserve_prefix_and_thinking_without_context_edits(root):
    class StandardMessagesWire(ScriptedClaude):
        def _handle(self, request):
            if request.url.path == "/v1/messages":
                assert "anthropic-beta" not in request.headers
                assert not request.url.query
            return super()._handle(request)

    thought = {"type": "thinking", "thinking": "Test summary", "signature": "synthetic-signature"}
    wire = StandardMessagesWire([
        turn(thought, call("touch", change=False)),
        turn(call("finish", "f")),
    ])
    h = Harness(root, [], wire=wire)
    h.scene._emitted_images = {"sha": "prior-image"}
    try:
        status = asyncio.run(h.drive())
    finally:
        h.close()
    assert status == "completed"
    assert h.scene._emitted_images == {"sha": "prior-image"}
    first, second = h.wire.requests
    assert all("context_management" not in body for body in (first, second))
    assert all(body["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
               for body in (first, second))
    assert all(body["speed"] == "standard" for body in (first, second))
    assert second["messages"][:len(first["messages"])] == first["messages"]
    assert second["messages"][-2]["content"][0] == thought
    meter = json.loads((root / "requests/0002-meter.json").read_text())
    assert meter["forecast"]["method"] == "settled_usage_plus_local_content"
    assert meter["actual_next_usage"]
    assert not any("count_tokens" in r.get("path", "") for r in h.wire.calls)


def test_invalid_schema_returns_error_without_mutation(root):
    h, status = run_harness(root, [
        turn(call("touch")), turn(call("touch", "extra", change=True, extra=1)),
        turn(call("finish", "f")),
    ])
    assert status == "completed"
    assert h.scene.s.head["id"] == "c1"
    assert h.wire.requests[1]["messages"][-1]["content"][0]["is_error"]
    assert h.wire.requests[2]["messages"][-1]["content"][0]["is_error"]


def test_global_turn_limit_includes_every_reply(root):
    h = Harness(root, [turn(call("touch", change=False))])
    h.runtime.spec = replace(h.spec, settings=replace(h.spec.settings, max_turns=1))
    try:
        with pytest.raises(R.Halt) as error:
            asyncio.run(h.drive())
        assert error.value.status == "turn_limit"
        assert len(h.wire.requests) == 1
    finally:
        h.close()


def test_stop_preserves_unknown_charge_during_stream(root):
    wire = BlockingWire([turn(call("touch", change=True))], partial=True)
    h = Harness(root, [], wire=wire)

    async def scenario():
        task = asyncio.create_task(h.drive())
        try:
            await wait_event(wire.ready)
            await wait_event(wire.blocked.entered)
            (root / "stop.requested").write_text("stop", encoding="utf-8")
            with pytest.raises(R.Halt) as error:
                await asyncio.wait_for(task, 5)
            assert error.value.status == "stopped"
            assert h.log.stop_observed and h.shared["cancelled"]
            assert wire.blocked.closed and wire.blocked.exited.is_set()
            assert h.scene.s.head["id"] == "c1"
            rows = R._ledger_rows(h.ledger)
            unknown = [row for row in rows if row["event"] == "unknown"]
            assert len(unknown) == 1 and unknown[0]["request_no"] == 1
            assert ("incomplete event" in unknown[0]["reason"]
                    or "SSE ended inside an event" in unknown[0]["reason"])
            assert not any(row["event"] == "settled" for row in rows)
            assert not h.ledger.may_start_request()[0]
            assert len(wire.requests) == 1
        finally:
            if wire.blocked is not None:
                wire.blocked.release.set()
            if not task.done():
                task.cancel()
                try:
                    await task
                except BaseException:
                    pass

    try:
        asyncio.run(scenario())
    finally:
        h.close()


def test_lost_result_keeps_original_rollback_barrier(root, monkeypatch):
    h = Harness(root, [turn(call("touch", change=True))])
    original = h.conversation.append
    async def append(message):
        if message["role"] == "user" and any(b.get("type") == "tool_result" for b in message["content"]):
            raise OSError("injected durable append failure")
        await original(message)
    monkeypatch.setattr(h.conversation, "append", append)
    async def drive():
        with pytest.raises(OSError):
            await h.drive()
        outputs = await h.conversation.recovery_results()
        assert plan_rollback(outputs, h.scene.rows)["checkpoint_before"] == "c1"
        assert "call1" in h.scene._unseen
    try:
        asyncio.run(drive())
    finally:
        h.close()


def owner_job(root):
    source = root / "source"
    source.mkdir()
    Image.new("RGB", (28, 28), "white").save(source / "front.png")
    views = root / "views.json"
    views.write_text(json.dumps({"front.png": {"kind": "front", "side": None}}), encoding="utf-8")
    return jobs.create_job(images=source, views=views, name="fixture", description="Owner words",
                           jobs_root=root / "jobs")


@pytest.fixture
def prepared(root):
    path = owner_job(root)
    spec, scene = R.prepare_run(path, settings=R.ClaudeSettings(), run_id="test", offline=True,
                                write_request=True)
    return spec, scene


@pytest.mark.parametrize("settings", [
    R.ClaudeSettings(model="claude-fable-5-1"), R.ClaudeSettings(effort="high"),
    R.ClaudeSettings(max_tokens=100), R.ClaudeSettings(max_finish_notes=3),
    R.ClaudeSettings(cutoff_usd=Decimal("NaN")), R.ClaudeSettings(cutoff_usd=Decimal("0")),
])
def test_model_effort_and_frozen_policy_gate(settings):
    with pytest.raises(R.RunRefused):
        settings.validate()


def test_prepare_approval_seal_and_single_use(prepared):
    spec, scene = prepared
    assert not scene.s.worker
    assert not (spec.run_dir / "approval.json").exists()
    R.approve_run(spec, words="OFFLINE TEST ONLY")
    assert R._check_seal(spec)["offline_only"] is True
    request = json.loads((spec.run_dir / "run-request.json").read_text(encoding="utf-8"))
    assert not {"context_trigger", "context_clear", "context_keep"} & request["settings"].keys()
    assert "context_management" not in request["settings"]["request_policy"]
    assert request["settings"]["request_policy"]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    money.consume_approval(spec.run_dir / "approval.json", spec.run_id)
    with pytest.raises(R.A.ApprovalRefused):
        R._check_seal(spec)


@pytest.mark.parametrize("file", ["run-request.json", "approval-request.json", "approval.json", "approval-binding.json"])
def test_any_changed_seal_file_refuses(prepared, file):
    spec, _ = prepared
    R.approve_run(spec, words="OFFLINE TEST ONLY")
    path = spec.run_dir / file
    value = json.loads(path.read_text(encoding="utf-8"))
    value["tampered"] = True
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises((R.A.ApprovalRefused, ValueError)):
        R._check_seal(spec)


def test_missing_seal_cannot_be_repaired_implicitly(prepared):
    spec, _ = prepared
    money.write_approval(spec.run_dir / "approval.json", words="OFFLINE TEST ONLY",
                         amount_usd=spec.settings.cutoff_usd, model=R.MODEL, effort="max", run_id=spec.run_id)
    with pytest.raises(R.A.ApprovalRefused):
        R._check_seal(spec)


def test_offline_approval_cannot_authorize_production(prepared):
    spec, _ = prepared
    R.approve_run(spec, words="OFFLINE TEST ONLY")
    with pytest.raises(R.A.ApprovalRefused):
        R._check_seal(replace(spec, offline=False))


def test_stale_owner_input_refuses(prepared):
    spec, _ = prepared
    R.approve_run(spec, words="OFFLINE TEST ONLY")
    job = spec.job_dir / "job.json"
    value = json.loads(job.read_text(encoding="utf-8"))
    value["description"] = "Changed words"
    job.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises((R.A.ApprovalRefused, ValueError)):
        R._check_seal(spec)


def test_cross_provider_lock_excludes_worker_start(prepared, root):
    spec, _ = prepared
    R.approve_run(spec, words="OFFLINE TEST ONLY")
    scene = Scene(spec.job_dir)
    lock = PaidRunLock(root / "lock")
    lock.acquire(job_id="astra-job", run_id="astra-run")
    try:
        with pytest.raises(Exception, match="paid run"):
            asyncio.run(R.run_claude(spec, scene=scene, inner=ScriptedClaude([]).transport(),
                                     isolated_lock_root=root / "lock"))
        assert not scene.started
        assert not (spec.run_dir / "approval.json.consumed.json").exists()
    finally:
        lock.release()


def test_three_money_facts_preserve_submicro_precision(root):
    h = Harness(root, [], cutoff="0.00000001")
    try:
        status = R.ClaudeRunStatus(h.ledger, settings=h.spec.settings)
        assert set(status.numbers()) == {"settled_usd", "cutoff_usd", "left_usd"}
        assert Decimal(status.numbers()["left_usd"]) == Decimal("0.00000001")
        assert "0.00000001" in status.footer()
        assert "reserve" not in status.footer()
    finally:
        h.close()


def test_lifecycle_writes_authoritative_result_and_closes(prepared, root, monkeypatch):
    spec, _ = prepared
    R.approve_run(spec, words="OFFLINE TEST ONLY")
    # The real tools are built, but this reply calls no worker operation.
    wire = ScriptedClaude([turn({"type": "text", "text": "test"}, stop_reason="end_turn")] * 3)
    scene = Scene(spec.job_dir)
    monkeypatch.setattr(R.snapshot, "capture", lambda p: None)
    result = asyncio.run(R.run_claude(spec, scene=scene, inner=wire.transport(),
                                     isolated_lock_root=root / "locks"))
    assert result["status"] == "ended_without_finish", result
    assert result["finish_notes"] == 2
    assert scene.started and scene.closed
    assert result["exit_checkpoint"]["verified"]
    assert json.loads((spec.run_dir / "result.json").read_text()) == result
    assert not (root / "locks/.paid-run.lock").exists()


def cleanup_fixture(root, monkeypatch, *, cutoff="20"):
    """Real runner/SDK/ledger lifecycle, with a small offline scene/tool fixture."""
    monkeypatch.setattr(R, "build_tools", lambda scene, settings: tools_for(scene))
    monkeypatch.setattr(R, "build_instructions", lambda tools: (
        "You are Astra. Use touch and finish. "
        "Only status accepted completes the run; a refusal names the failed checks."))
    monkeypatch.setattr(R, "instruction_contract",
                        lambda tools: ToolContract(frozenset(tool.name for tool in tools)))
    monkeypatch.setattr(R, "_prepared_stages", lambda job, settings: ())
    monkeypatch.setattr(R.snapshot, "capture", lambda p: None)
    path = owner_job(root)
    scene = Scene(path)
    spec, _ = R.prepare_run(path, settings=R.ClaudeSettings(cutoff_usd=Decimal(cutoff)),
                            run_id="cleanup", offline=True, write_request=True, scene=scene)
    R.approve_run(spec, words="OFFLINE CLEANUP TEST ONLY")
    return spec, scene


@pytest.mark.parametrize("ending", [
    "completed", "draft", "cutoff", "unknown", "failed", "stopped", "crash",
    "stopped_before_request", "stopped_before_stream",
])
def test_teardown_deletes_uploads_after_every_ending(root, monkeypatch, ending):
    spec, scene = cleanup_fixture(root, monkeypatch,
                                  cutoff="0.000001" if ending == "cutoff" else "20")
    if ending == "completed":
        turns = [turn(call("finish"))]
    elif ending == "draft":
        turns = [turn({"type": "text", "text": "test"}, stop_reason="end_turn")] * 3
    elif ending == "cutoff":
        turns = [turn(call("touch", change=False), usage={"input_tokens": 100, "output_tokens": 100})]
    elif ending == "unknown":
        turns = [turn(call("touch", change=True), missing_usage=True)]
    elif ending.startswith("stopped"):
        turns = [turn(call("touch", change=True))]
    else:
        def failure(body):
            if ending == "crash":
                raise SystemExit("offline process exception")
            # A protocol failure after settlement is a failed run, not unknown money.
            return turn(call("touch", change=True), stop_reason="unsupported")
        turns = [failure]
    wire = (BlockingWire(turns, partial=True, hold_before_publish=ending == "stopped_before_stream")
            if ending in {"stopped", "stopped_before_stream"} else ScriptedClaude(turns))
    inner = wire.transport()
    original = inner.handle_request
    close_attempted = threading.Event()
    original_close = R._Runtime._close_stream

    def observed_close(runtime):
        try:
            return original_close(runtime)
        finally:
            close_attempted.set()

    monkeypatch.setattr(R._Runtime, "_close_stream", observed_close)
    monkeypatch.setattr(R, "publish_result", lambda job, lifecycle_scene, result: {
        "status": "no_usable_checkpoint"})

    def check_locks_and_settlement(request):
        if request.method == "DELETE":
            assert (root / "locks/.paid-run.lock").exists()
            with pytest.raises(R.RunRefused, match="private job"):
                with R.job_lock(spec.job_dir):
                    pass
            rows = [json.loads(line) for line in (spec.run_dir / "ledger.jsonl").read_text().splitlines()]
            assert rows[-1]["event"] == "conversation_finished"
            terminal = {row["request_no"] for row in rows
                        if row["event"] in {"settled", "unknown", "not_sent", "rejected"}}
            assert all(row["request_no"] in terminal for row in rows if row["event"] == "started")
        return original(request)
    monkeypatch.setattr(inner, "handle_request", check_locks_and_settlement)

    async def scenario():
        if ending == "stopped_before_request":
            (spec.job_dir / "stop.requested").write_text("stop", encoding="utf-8")
        task = asyncio.create_task(R.execute_run(
            spec, scene=scene, inner=inner, isolated_lock_root=root / "locks"))
        try:
            if isinstance(wire, BlockingWire):
                await wait_event(wire.ready)
                if ending == "stopped":
                    await wait_event(wire.blocked.entered)
                (spec.job_dir / "stop.requested").write_text("stop", encoding="utf-8")
                if ending == "stopped_before_stream":
                    # Stop is observed while SDK __enter__ is still awaiting its
                    # response, before _Runtime can publish a closable stream.
                    await wait_event(close_attempted)
                    assert not wire.blocked.closed and not task.done()
                    wire.publish.set()
            return (await asyncio.wait_for(task, 10))["result"]
        finally:
            if isinstance(wire, BlockingWire):
                wire.publish.set()
                if wire.blocked is not None:
                    wire.blocked.release.set()
            if not task.done():
                task.cancel()
                try:
                    await task
                except BaseException:
                    pass

    result = asyncio.run(scenario())
    expected = {"completed": "completed", "draft": "ended_without_finish", "cutoff": "budget_limit",
                "unknown": "provider_unknown", "failed": "failed", "stopped": "stopped",
                "crash": "provider_unknown", "stopped_before_request": "stopped",
                "stopped_before_stream": "stopped"}
    assert result["status"] == expected[ending], result
    if ending.startswith("stopped"):
        assert result["stop_observed"]
        assert scene.s.head["id"] == "c1" and not scene.rows
        assert len(wire.requests) == (0 if ending == "stopped_before_request" else 1)
        if ending == "stopped_before_request":
            assert result["unknown_charges"] == []
        else:
            assert len(result["unknown_charges"]) == 1
            assert result["unknown_charges"][0]["request_no"] == 1
            assert result["unknown_charges"][0]["reason"]
            assert wire.blocked.closed
            if ending == "stopped":
                assert wire.blocked.exited.is_set()
    cleanup = result["upload_cleanup"]
    assert cleanup["uploads"] == cleanup["deleted"] == len(wire.files) == 1
    assert cleanup["failed"] == []
    assert all(record["deleted"] and record["deleted_at"]
               for record in json.loads((spec.run_dir / "file-cache.json").read_text()).values())
    assert len([row for row in wire.calls if row["method"] == "DELETE"]) == 1
    assert scene.closed and not (root / "locks/.paid-run.lock").exists()
    saved = json.loads((spec.run_dir / "result.json").read_text())
    assert saved["status"] == result["status"]
    assert saved["stop_observed"] == result["stop_observed"]
    assert saved["unknown_charges"] == result["unknown_charges"]
    summary = json.loads((spec.run_dir / "summary.json").read_text())
    assert summary["upload_cleanup"] == cleanup
    assert summary["stop_observed"] == result["stop_observed"]
    assert summary["unknown_charges"] == result["unknown_charges"]
    text = (spec.run_dir / "summary.md").read_text()
    assert "Uploads: 1 made; 1 deleted" in text
    if result["unknown_charges"]:
        assert "Unknown charge for request 1" in text


def test_failed_delete_is_reported_once_with_expiry_without_changing_status(root, monkeypatch):
    spec, scene = cleanup_fixture(root, monkeypatch)
    wire = ScriptedClaude([turn(call("finish"))], delete_statuses=[503])
    result = asyncio.run(R.run_claude(spec, scene=scene, inner=wire.transport(),
                                     isolated_lock_root=root / "locks"))
    assert result["status"] == "completed"
    cleanup = result["upload_cleanup"]
    assert cleanup["uploads"] == 1 and cleanup["deleted"] == 0
    assert len(cleanup["failed"]) == 1
    failure = cleanup["failed"][0]
    assert failure["http_status"] == 503 and failure["request_id"]
    assert failure["expires_at"] and failure["error"]
    assert len([row for row in wire.calls if row["method"] == "DELETE"]) == 1
    cache = json.loads((spec.run_dir / "file-cache.json").read_text())
    record = next(iter(cache.values()))
    assert not record.get("deleted") and record["deletion_attempted_at"] and record["deletion_error"]
    R.write_summary(spec, result, {"status": "no_usable_checkpoint"})
    text = (spec.run_dir / "summary.md").read_text()
    assert "Uploads: 1 made; 0 deleted" in text
    assert failure["file_id"] in text and failure["expires_at"] in text


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_cancellation_during_delete_waits_before_releasing_locks(root, monkeypatch, cleanup_fails):
    spec, scene = cleanup_fixture(root, monkeypatch)
    wire = ScriptedClaude([turn(call("finish"))])
    inner = wire.transport()
    entered, release = threading.Event(), threading.Event()

    cleanup = R.delete_uploads

    def blocked_cleanup(*args):
        entered.set()
        assert release.wait(10), "test did not release deletion"
        if cleanup_fails:
            raise OSError("offline cleanup receipt failure")
        return cleanup(*args)
    monkeypatch.setattr(R, "delete_uploads", blocked_cleanup)

    async def scenario():
        task = asyncio.create_task(R.run_claude(
            spec, scene=scene, inner=inner, isolated_lock_root=root / "locks"))
        try:
            assert await asyncio.to_thread(entered.wait, 10)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
            assert (root / "locks/.paid-run.lock").exists()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            release.set()
            if not task.done():
                await R._join(task)
    asyncio.run(scenario())
    result = json.loads((spec.run_dir / "result.json").read_text())
    assert result["upload_cleanup"]["uploads"] == 1
    assert result["upload_cleanup"]["deleted"] == (0 if cleanup_fails else 1)
    if cleanup_fails:
        assert result["upload_cleanup"]["failed"][0]["expires_at"]
        assert any(row["step"] == "delete_uploads" for row in result["teardown_errors"])
    assert not (root / "locks/.paid-run.lock").exists()
    assert scene.closed


def specialist_fixture(h):
    """A real StageSpec/ClaudeStageRuntime with tools that need no Blender."""
    scene = Scene(h.root / "specialist-scene")
    scene.state = {}
    scene.observed_contexts = []
    scene.operation = None

    @function_tool(failure_error_function=fatal_tool_errors(*FATAL_TOOL_ERRORS))
    async def facet_step(ctx: ToolContext, action: str):
        """Inspect or change the controlled specialist scene."""
        scene.observed_contexts.append((ctx.tool_call_id, ctx.context))
        if action == "fatal":
            raise WorkerFailed("controlled specialist worker failure", scene.s.head)
        if scene.operation is not None:
            await scene.operation(ctx)
        if action == "change":
            scene.s.head = {"id": "child2", "fingerprint": "changed", "sha256": "c" * 64}
            scene.state.pop("finish", None)
            scene.finish = None
        scene.rows.append({"call_id": ctx.tool_call_id, "tool": "facet_step", "action": action})
        return json.dumps({"status": "ok", "head": scene.s.head})

    @function_tool
    async def facet_finish(ctx: ToolContext):
        """Finish the controlled specialist fixture."""
        scene.observed_contexts.append((ctx.tool_call_id, ctx.context))
        scene.finish = {"status": "accepted", "call_id": ctx.tool_call_id,
                        "checkpoint": scene.s.head["id"]}
        scene.state["finish"] = scene.finish
        return json.dumps(scene.finish)

    tools = (facet_step, facet_finish)
    text = ("You are Astra, the temple specialist.\nUse facet_step and facet_finish.\n"
            "A refusal names unmet conditions or validation failure\n"
            "and the run continues. Only an accepted finish completes this specialist session.")
    settings = R.stage.settings(h.spec.settings.carrier())
    spec = StageSpec("temple_stage", text, tools, "Owner specialist words", settings,
                     contract=ToolContract(frozenset(tool.name for tool in tools)),
                     scene_note="Controlled specialist fixture.")
    parent = replace(h.spec, stage_contracts=(R._stage_binding(text, tools, settings),))
    stages = R.ClaudeStageRuntime(
        parent, client=h.client, ledger=h.ledger, files=h.files,
        log=h.log, blobs=h.blobs, shared=h.shared)
    return spec, scene, stages


def test_specialist_real_sdk_shares_model_money_and_counter_but_not_history(root):
    h = Harness(root, [turn(call("facet_step", "child_read", action="inspect")),
                       turn(call("facet_finish", "child_finish")), turn(call("finish", "parent_finish"))])
    child, scene, stages = specialist_fixture(h)

    async def scenario():
        outcome = await stages.run_stage(child, scene, Decimal("20"), "parent_stage")
        assert outcome["status"] == "completed"
        assert outcome["turns_used"] == 2
        assert outcome["provider"] == "anthropic" and outcome["model"] == R.MODEL
        assert stages.tasks == set()
        assert await h.conversation.messages() == []
        assert await h.drive() == "completed"
        assert h.shared["requests"] == 3
        assert h.scene.s.head["id"] == "c1"
        for raw_id, context in scene.observed_contexts:
            assert context.ledger is h.ledger
            assert context.settings.model == R.MODEL
            assert context.stage_call_id == "parent_stage"
            assert raw_id in {"child_read", "child_finish"}
        assert {r["model"] for r in h.wire.requests} == {R.MODEL}
        assert h.wire.requests[0]["system"].startswith("You are Claude, the temple specialist.")
        assert h.wire.requests[-1]["system"] == h.spec.instructions
        assert "child_read" not in json.dumps(h.wire.requests[-1]["messages"])
        assert h.wire.requests[1]["messages"][-1]["content"][0]["tool_use_id"] == "child_read"
        assert [entry["call_id"] for entry in h.log.tool_calls] == [
            "stage:parent_stage:child_read", "stage:parent_stage:child_finish", "parent_finish"]
        rows = [r for r in R._ledger_rows(h.ledger) if r["event"] == "settled"]
        assert len(rows) == 3
        assert h.ledger.settled_usd() == sum(Decimal(r["cost_usd"]) for r in rows)
        directory = stages.parent.run_dir / "stages" / "temple_stage-parent_stage"
        child_conv = Conversation(directory / "session.sqlite", blobs=h.blobs)
        try:
            messages = await child_conv.messages()
            assert messages[1]["content"][0]["id"] == "child_read"
        finally:
            child_conv.close()
        h.wire.assert_complete()

    try:
        asyncio.run(scenario())
    finally:
        h.close()


def test_parent_teardown_includes_specialist_uploads(root):
    h = Harness(root, [turn(call("facet_step", "child_image", action="inspect")),
                       turn(call("facet_finish", "child_finish")),
                       turn(call("finish", "parent_finish"))])
    child, scene, stages = specialist_fixture(h)
    image = io.BytesIO()
    Image.new("RGB", (42, 42), "green").save(image, format="PNG")

    async def upload_from_child(ctx):
        assert ctx.context.ledger is h.ledger
        h.files.file_id_for(R.images.tile(image.getvalue(), "specialist fixture")[0])

    scene.operation = upload_from_child

    async def scenario():
        outcome = await stages.run_stage(child, scene, Decimal("20"), "parent_stage")
        assert outcome["status"] == "completed"
        assert len(h.files.uploaded_records()) == 1
        assert not h.ledger.conversation_finished()
        assert not [row for row in h.wire.calls if row["method"] == "DELETE"]
        assert await h.drive() == "completed"
        await stages.close()
        cleanup = R.delete_uploads(h.client, h.files, h.ledger, root)
        assert cleanup["uploads"] == cleanup["deleted"] == 1
        assert cleanup["failed"] == []
        assert len([row for row in h.wire.calls if row["method"] == "DELETE"]) == 1
        # A second cleanup preserves the record and never retries a delete.
        assert R.delete_uploads(h.client, h.files, h.ledger, root)["deleted"] == 1
        assert len([row for row in h.wire.calls if row["method"] == "DELETE"]) == 1
    try:
        asyncio.run(scenario())
    finally:
        h.close()


@pytest.mark.parametrize("accepted", [False, True])
def test_child_submicro_cutoff_allows_crossing_tool_then_blocks_parent(root, accepted):
    response = call("facet_finish", "child_last") if accepted else call("facet_step", "child_last", action="change")
    h = Harness(root, [turn(response)], cutoff="0.00000001")
    child, scene, stages = specialist_fixture(h)

    async def scenario():
        outcome = await stages.run_stage(child, scene, Decimal("0.00000001"), "submicro")
        assert outcome["status"] == ("completed" if accepted else "budget_limit")
        assert len(h.wire.requests) == 1
        assert h.ledger.settled_usd() > h.ledger.cutoff_usd
        if not accepted:
            assert scene.s.head["id"] == "child2"
        with pytest.raises(R.Halt) as raised:
            await h.drive()
        assert raised.value.status == "budget_limit"
        assert len(h.wire.requests) == 1
        assert stages.tasks == set()

    try:
        asyncio.run(scenario())
    finally:
        h.close()


def test_child_unknown_usage_stops_shared_parent_before_mutation(root):
    h = Harness(root, [turn(call("facet_step", "uncertain", action="change"),
                           usage={"input_tokens": 10, "output_tokens": 1, "unknown_bill": 1})])
    child, scene, stages = specialist_fixture(h)

    async def scenario():
        with pytest.raises(StageRunFailed):
            await stages.run_stage(child, scene, Decimal("20"), "unknown")
        assert scene.s.head["id"] == "c1"
        assert scene.observed_contexts == []
        assert stages.outcomes[-1]["status"] == "provider_unknown"
        assert stages.tasks == set()
        with pytest.raises(R.Halt) as raised:
            await h.drive()
        assert raised.value.status == "provider_unknown"
        assert len(h.wire.requests) == 1

    try:
        asyncio.run(scenario())
    finally:
        h.close()


def test_child_worker_failure_retains_fatal_cause_and_receipt(root):
    h = Harness(root, [turn(call("facet_step", "fatal", action="fatal"))])
    child, scene, stages = specialist_fixture(h)

    async def scenario():
        with pytest.raises(StageRunFailed) as raised:
            await stages.run_stage(child, scene, Decimal("20"), "fatal")
        assert R.is_worker_failure(raised.value)
        assert stages.outcomes[-1]["status"] == "failed_worker"
        assert "fatal" in scene._unseen
        assert stages.tasks == set()
        assert h.ledger.may_start_request()[0]  # known charge; fatal scene state stops the run
        assert len(h.wire.requests) == 1

    try:
        asyncio.run(scenario())
    finally:
        h.close()


@pytest.mark.parametrize("change", ["instructions", "description", "schema", "settings"])
def test_changed_specialist_source_contract_refuses_before_network(root, change):
    h = Harness(root, [])
    child, scene, stages = specialist_fixture(h)
    if change == "instructions":
        child = replace(child, instructions=child.instructions + "\nAltered.")
    elif change == "settings":
        child = replace(child, settings=replace(child.settings, effort="high"))
    else:
        changed = copy.copy(child.tools[0])
        if change == "description":
            changed.description += " Altered."
        else:
            changed.params_json_schema = copy.deepcopy(changed.params_json_schema)
            changed.params_json_schema["properties"]["action"]["minLength"] = 1
        child = replace(child, tools=(changed, child.tools[1]))
    try:
        with pytest.raises(StageRunFailed, match="differs from approval"):
            asyncio.run(stages.run_stage(child, scene, Decimal("20"), "tampered"))
        assert h.wire.requests == [] and stages.tasks == set() and stages.outcomes == []
        assert not (stages.parent.run_dir / "stages").exists()
    finally:
        h.close()


def test_specialist_close_cancels_and_joins_actual_child_unwind(root):
    h = Harness(root, [turn(call("facet_step", "wait", action="inspect"))])
    child, scene, stages = specialist_fixture(h)

    async def scenario():
        entered, release, exited = (asyncio.Event() for _ in range(3))

        async def operation(ctx):
            entered.set()
            try:
                await release.wait()
            finally:
                exited.set()

        scene.operation = operation
        task = asyncio.create_task(stages.run_stage(child, scene, Decimal("20"), "cancelled"))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            closing = asyncio.create_task(stages.close())
            await asyncio.sleep(0)
            assert not closing.done() and not task.done() and not exited.is_set()
            with pytest.raises(StageRunFailed, match="closing"):
                await stages.run_stage(child, scene, Decimal("20"), "second")
            release.set()
            await asyncio.wait_for(closing, 5)
            assert exited.is_set() and task.cancelled()
            assert stages.tasks == set()
            assert stages.outcomes[-1]["status"] == "stopped"
            assert h.ledger.may_start_request()[0]
        finally:
            release.set()
            if not task.done():
                task.cancel()
                try:
                    await task
                except BaseException:
                    pass

    try:
        asyncio.run(scenario())
    finally:
        h.close()


class BlockingStream(httpx2.SyncByteStream):
    """Hold a real synchronous response between initial and final usage."""
    def __init__(self, raw, *, partial=False, hold_after_close=False):
        split = raw.index(b"\n\n") + 2
        if partial:
            # A full message_start followed by the beginning of a ping proves
            # Stop handles _Tee.close's incomplete-event Refused path.
            split += len(b"event: ping\ndata: {")
        self.first, self.rest = raw[:split], raw[split:]
        self.entered, self.release, self.exited, self.close_called = (
            threading.Event() for _ in range(4))
        self.closed = False
        self.hold_after_close = hold_after_close

    def __iter__(self):
        try:
            yield self.first
            self.entered.set()
            if not self.release.wait(5):
                raise AssertionError("test did not release the blocked synchronous SSE")
            if not self.closed:
                yield self.rest
        finally:
            self.exited.set()

    def close(self):
        self.closed = True
        self.close_called.set()
        if not self.hold_after_close:
            self.release.set()


class BlockingWire(ScriptedClaude):
    def __init__(self, turns, *, partial=False, hold_before_publish=False, hold_after_close=False):
        super().__init__(turns)
        self.blocked = None
        self.partial = partial
        self.hold_before_publish = hold_before_publish
        self.hold_after_close = hold_after_close
        self.ready, self.publish = (threading.Event() for _ in range(2))

    def __call__(self, request):
        response = super().__call__(request)
        if request.url.path == "/v1/messages":
            self.blocked = BlockingStream(response.read(), partial=self.partial,
                                          hold_after_close=self.hold_after_close)
            self.ready.set()
            if self.hold_before_publish and not self.publish.wait(10):
                raise AssertionError("test did not release SDK stream entry")
            return httpx2.Response(200, headers=response.headers, stream=self.blocked)
        return response


async def wait_event(event):
    """Wait for a real state transition; the timeout only bounds a broken test."""
    assert await asyncio.to_thread(event.wait, 5), "test synchronization event did not arrive"


def test_stop_close_refused_still_joins_stream_before_return(root, monkeypatch):
    wire = BlockingWire([turn(call("touch", change=True))],
                        partial=True, hold_after_close=True)
    h = Harness(root, [], wire=wire)
    close_refused, joining_reader = (threading.Event() for _ in range(2))
    original_close, original_join = h.runtime._close_stream, R._join
    close_joined = False

    def close():
        try:
            original_close()
        except transport.Refused:
            close_refused.set()
            raise

    async def join(task):
        nonlocal close_joined
        if close_joined:
            joining_reader.set()
        try:
            return await original_join(task)
        except transport.Refused:
            # Signal only the later reader join, after the close task has
            # actually returned its refusal, regardless of thread scheduling.
            if close_refused.is_set():
                close_joined = True
            raise

    monkeypatch.setattr(h.runtime, "_close_stream", close)
    monkeypatch.setattr(R, "_join", join)

    async def scenario():
        task = asyncio.create_task(h.drive())
        try:
            await wait_event(wire.ready)
            await wait_event(wire.blocked.entered)
            (root / "stop.requested").write_text("stop", encoding="utf-8")
            await wait_event(joining_reader)
            assert close_refused.is_set() and wire.blocked.close_called.is_set()
            assert not task.done() and not wire.blocked.exited.is_set()
            assert R._ledger_rows(h.ledger)[-1]["event"] == "unknown"
            wire.blocked.release.set()
            with pytest.raises(R.Halt) as error:
                await asyncio.wait_for(task, 5)
            assert error.value.status == "stopped"
            assert wire.blocked.exited.is_set() and h.log.stop_observed
            assert h.scene.s.head["id"] == "c1" and len(wire.requests) == 1
        finally:
            if wire.blocked is not None:
                wire.blocked.release.set()
            if not task.done():
                task.cancel()
                try:
                    await task
                except BaseException:
                    pass

    try:
        asyncio.run(scenario())
    finally:
        h.close()


def test_stop_after_complete_reply_preserves_settled_charge(root, monkeypatch):
    h = Harness(root, [turn(call("touch", change=True))])
    response_complete, return_response, close_attempted = (threading.Event() for _ in range(3))
    original_stream, original_close = h.runtime._stream_request, h.runtime._close_stream

    def stream(body):
        response = original_stream(body)
        response_complete.set()
        if not return_response.wait(10):
            raise AssertionError("test did not release the completed response")
        return response

    def close():
        try:
            original_close()
        finally:
            close_attempted.set()

    monkeypatch.setattr(h.runtime, "_stream_request", stream)
    monkeypatch.setattr(h.runtime, "_close_stream", close)

    async def scenario():
        task = asyncio.create_task(h.drive())
        try:
            await wait_event(response_complete)
            (root / "stop.requested").write_text("stop", encoding="utf-8")
            await wait_event(close_attempted)
            assert not task.done()
            return_response.set()
            with pytest.raises(R.Halt) as error:
                await asyncio.wait_for(task, 5)
            assert error.value.status == "stopped" and h.log.stop_observed
            rows = R._ledger_rows(h.ledger)
            assert rows[-1]["event"] == "settled"
            assert not any(row["event"] == "unknown" for row in rows)
            assert h.scene.s.head["id"] == "c1" and len(h.wire.requests) == 1
            assert h.shared["cancelled"]
        finally:
            return_response.set()
            if not task.done():
                task.cancel()
                try:
                    await task
                except BaseException:
                    pass

    try:
        asyncio.run(scenario())
    finally:
        h.close()


@pytest.mark.parametrize("stop_kind", ["stop_file", "cancel"])
def test_blocked_sse_stop_drains_thread_and_records_unknown(root, stop_kind):
    wire = BlockingWire([turn(call("touch", "must_not_run", change=True))])
    h = Harness(root, [], wire=wire)

    async def scenario():
        task = asyncio.create_task(h.drive())
        try:
            await wait_event(wire.ready)
            await wait_event(wire.blocked.entered)
            if stop_kind == "stop_file":
                (root / "stop.requested").write_text("stop", encoding="utf-8")
            else:
                task.cancel()
            expected = R.Halt if stop_kind == "stop_file" else asyncio.CancelledError
            with pytest.raises(expected) as error:
                await asyncio.wait_for(task, 5)
            if stop_kind == "stop_file":
                assert error.value.status == "stopped"
            assert h.log.stop_observed and h.shared["cancelled"]
            assert wire.blocked.closed and wire.blocked.exited.is_set()
            assert h.scene.s.head["id"] == "c1"
            assert R._ledger_failure(h.ledger) == "provider_unknown"
            assert R._ledger_rows(h.ledger)[-1]["event"] == "unknown"
            assert len(h.wire.requests) == 1
        finally:
            if wire.blocked is not None:
                wire.blocked.release.set()
            if not task.done():
                task.cancel()
                try:
                    await task
                except BaseException:
                    pass

    try:
        asyncio.run(scenario())
    finally:
        h.close()


@pytest.mark.parametrize("stop_kind", ["stop_file", "cancel"])
def test_stop_joins_shared_sync_tool_before_releasing_scene(root, stop_kind):
    """Exercise the unchanged build_tools thread wrapper, with no Blender."""
    h = Harness(root, [turn(call(
        "execute_blender_code", "blocked_tool", code="controlled fixture",
        soft_timeout_s=120, label="blocked", seating_repair=False))])
    entered, release, exited = (threading.Event() for _ in range(3))
    h.scene.lock = threading.Lock()

    def execute(code, **kwargs):
        try:
            assert code == "controlled fixture" and kwargs["call_id"] == "blocked_tool"
            entered.set()
            if not release.wait(5):
                raise AssertionError("test did not release the shared synchronous tool")
            h.scene.s.head = {"id": "c2", "fingerprint": "after", "sha256": "2" * 64}
            return {"status": "ok"}
        finally:
            exited.set()

    def record_call(call_id, tool, before, after, result):
        h.scene.rows.append({"call_id": call_id, "tool": tool,
                             "checkpoint_before": before, "checkpoint_after": after})

    h.scene.s.execute = execute
    h.scene.record_call = record_call
    execute_tool = next(tool for tool in R.build_tools(h.scene, h.spec.settings.carrier())
                        if tool.name == "execute_blender_code")
    h.spec = replace(h.spec, tools=(execute_tool,))
    h.runtime = R._Runtime(
        spec=h.spec, scene=h.scene, conversation=h.conversation, ledger=h.ledger,
        client=h.client, files=h.files, log=h.log, directory=root, shared=h.shared)

    async def scenario():
        task = asyncio.create_task(h.drive())
        try:
            for _ in range(500):
                if entered.is_set():
                    break
                await asyncio.sleep(0.01)
            assert entered.is_set() and h.scene.lock.locked()
            if stop_kind == "stop_file":
                (root / "stop.requested").write_text("stop", encoding="utf-8")
            else:
                task.cancel()
            await asyncio.sleep(0.02)
            assert not task.done() and not exited.is_set()
            assert h.scene.lock.locked() and h.scene.s.head["id"] == "c1"
            release.set()
            expected = StopRequested if stop_kind == "stop_file" else asyncio.CancelledError
            with pytest.raises(expected):
                await asyncio.wait_for(task, 5)
            assert exited.is_set() and not h.scene.lock.locked()
            assert h.scene.s.head["id"] == "c2"
            assert h.scene.rows[-1]["checkpoint_after"] == "c2"
            assert len(h.wire.requests) == 1 and h.ledger.may_start_request()[0]
            messages = await h.conversation.messages()
            if stop_kind == "cancel":
                assert "blocked_tool" in h.scene._unseen
                assert messages[-1]["role"] == "assistant"
                assert h.shared["cancelled"] and h.log.stop_observed
            else:
                assert messages[-1]["content"][0]["tool_use_id"] == "blocked_tool"
        finally:
            release.set()
            if not task.done():
                task.cancel()
                try:
                    await task
                except BaseException:
                    pass

    try:
        asyncio.run(scenario())
    finally:
        h.close()

