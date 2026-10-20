"""Runner decisions through the real SDK, guard and durable conversation."""
from __future__ import annotations

import asyncio
import copy
from dataclasses import replace
from decimal import Decimal
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
        self.client = anthropic.Anthropic(
            api_key=R.OFFLINE_KEY, max_retries=0,
            http_client=transport.guarded_client(
                self.ledger, root / "transport", inner=self.wire.transport(), sleep=self.waits.append))
        self.scene = Scene(root)
        self.spec = R.ClaudeRunSpec(
            root, "test", R.ClaudeSettings(cutoff_usd=Decimal(cutoff)),
            "Fixture instructions", tools_for(self.scene), "Owner words", (), "", (),
            None, True, finish_tool="finish")
        self.files = FileCache(root / "file-cache.json", lambda b, n: self.client.files.upload(
            file=(n, b, "image/png"), **transport.files_upload_params()).id)
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
    h = Harness(root, [turn({"type": "text", "text": "Cannot continue"}, stop_reason="refusal")])
    try:
        with pytest.raises(R.Halt) as error:
            asyncio.run(h.drive())
        assert error.value.status == "provider_rejected"
        assert R._ledger_rows(h.ledger)[-1]["upper_bound"]
        assert len(h.wire.requests) == 1
    finally:
        h.close()


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


def test_clear_event_preserves_prefix_and_thinking(root):
    thought = {"type": "thinking", "thinking": "Test summary", "signature": "synthetic-signature"}
    h, status = run_harness(root, [
        turn(thought, call("touch", change=False),
             context_management={"applied_edits": [{"type": "clear_tool_uses_20250919",
                                                   "cleared_tool_uses": 1, "cleared_input_tokens": 150000}]}),
        turn(call("finish", "f")),
    ])
    assert status == "completed"
    first, second = h.wire.requests
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
    def delayed(body):
        (root / "stop.requested").write_text("stop", encoding="utf-8")
        return turn(call("touch", change=True))
    h = Harness(root, [delayed])
    try:
        with pytest.raises((R.Halt, StopRequested)):
            asyncio.run(h.drive())
        assert h.scene.s.head["id"] == "c1"
        rows = R._ledger_rows(h.ledger)
        assert rows[-1]["event"] in {"settled", "unknown"}
        assert not [r for r in rows if r["event"] == "started"
                    and not any(e.get("request_no") == r["request_no"] and
                                e["event"] in {"settled", "unknown"} for e in rows)]
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
    def __init__(self, raw):
        split = raw.index(b"\n\n") + 2
        self.first, self.rest = raw[:split], raw[split:]
        self.entered, self.release, self.exited = (threading.Event() for _ in range(3))
        self.closed = False

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
        self.release.set()


class BlockingWire(ScriptedClaude):
    def __init__(self, turns):
        super().__init__(turns)
        self.blocked = None

    def __call__(self, request):
        response = super().__call__(request)
        if request.url.path == "/v1/messages":
            self.blocked = BlockingStream(response.read())
            return httpx2.Response(200, headers=response.headers, stream=self.blocked)
        return response


@pytest.mark.parametrize("stop_kind", ["stop_file", "cancel"])
def test_blocked_sse_stop_drains_thread_and_records_unknown(root, stop_kind):
    wire = BlockingWire([turn(call("touch", "must_not_run", change=True))])
    h = Harness(root, [], wire=wire)

    async def scenario():
        task = asyncio.create_task(h.drive())
        try:
            for _ in range(500):
                if wire.blocked is not None and wire.blocked.entered.is_set():
                    break
                await asyncio.sleep(0.01)
            assert wire.blocked is not None and wire.blocked.entered.is_set()
            if stop_kind == "stop_file":
                (root / "stop.requested").write_text("stop", encoding="utf-8")
            else:
                task.cancel()
            with pytest.raises((Exception, asyncio.CancelledError)):
                await asyncio.wait_for(task, 5)
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

