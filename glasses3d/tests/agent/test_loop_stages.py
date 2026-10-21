"""Specialist conversations share one approved run and money guard; entirely offline, no Blender."""
import asyncio
import base64
import json
from dataclasses import replace
from pathlib import Path

import pytest
from agents import function_tool
from agents.tool import ToolOutputImage, ToolOutputText
from agents.tool_context import ToolContext

from glasses3d.agent import approvals as A, budget as B, loop as L, replay as R
from glasses3d.tests.agent.loop_support import FakeScene, BRIEF, photos, data_url, png_bytes, tool_instructions
from glasses3d.tests.agent.support import OFFLINE_KEY, fake_counter, request, response

RUN = "offline-stage"
CHILD = L.RunSettings(effort="max", max_output_tokens=4096, max_turns=None, tool_choice="required",
                      reset_tool_choice=False, finish_tool="facet_finish", finish_k=4,
                      context_guard_tokens=150000, context_guard_every_requests=3)


@pytest.fixture(autouse=True)
def no_production(monkeypatch):
    monkeypatch.setattr(L, "ProductionClients", lambda *a, **k: pytest.fail("production client forbidden"))


def setup(root, replies, *, stage_cap="2", allowance="2", parent_cap="5", max_turns=10, alter=None,
          child_scene=None, owner_notes=(), stage_notes=(), stage_tools=None, child_settings=None):
    scene = FakeScene()
    child_scene = child_scene or FakeScene()
    results = []

    @function_tool
    async def look(ctx: ToolContext):
        """Inspect one private temple revision."""
        child_scene.mutate(ctx.tool_call_id, "look")
        return [ToolOutputText(text="private checkpoint c0001; front"),
                ToolOutputImage(image_url=data_url(png_bytes(4, 4, 42)), detail="high")]

    @function_tool
    async def facet_finish():
        """End an accepted specialist stage."""
        return json.dumps({"status": "accepted"})

    child_tools = [look, facet_finish] if stage_tools is None else stage_tools
    child = L.StageSpec("temple", tool_instructions(child_tools, "Private temple specialist."), child_tools,
                        BRIEF, child_settings or CHILD, photos=photos(2), owner_notes=stage_notes,
                        contract=L.ToolContract(frozenset(t.name for t in child_tools)))
    contract = L.StageContract.from_spec(child, allowance)

    @function_tool(failure_error_function=L.fatal_tool_errors(*L.STAGE_FATAL))
    async def temple_stage(ctx: ToolContext):
        """Run the private temple specialist."""
        actual = alter(child) if alter else child
        result = await ctx.context.run_stage(actual, child_scene, stage_cap, ctx.tool_call_id)
        results.append(result)
        return result

    tools = [temple_stage]
    settings = L.RunSettings(effort="max", max_output_tokens=4096, max_turns=max_turns,
                             settled_stop_usd=parent_cap)
    job = root / "job"
    (job / "inputs/images").mkdir(parents=True)
    (job / "job.json").write_text(json.dumps({"schema": "g3d.job/1", "name": "Offline stages"}))
    for photo in photos(2):
        (job / "inputs" / photo.path).write_bytes(base64.b64decode(photo.image_url.split(",", 1)[1]))
    instructions = tool_instructions(tools)
    req = A.build_request(job, instructions_sha256=L.instructions_sha256(instructions),
                         tool_registry_sha256=L.tool_registry_sha256(tools, settings),
                         settings=L.approval_settings(settings, (contract,)),
                         unsent_owner_notes=owner_notes,
                         run_inputs=A.run_input_binding(job, BRIEF, photos(2)))
    A.write_approval_request(job / "runs" / RUN, job_id=job.name, request=req, estimate={}, suggested_cap_usd=parent_cap)
    A.write_approval(job / "runs" / RUN, max_usd=parent_cap, owner_words="Offline fixture approval")
    # A rejected stage count has no corresponding create. Constant fake counts keep ScriptedOpenAI's
    # count-indexed usage fixture valid while the real guard still compares each counted/sent payload.
    transport = R.ScriptedOpenAI(replies, count_tokens=lambda body: 100)
    spec = L.RunSpec(job, RUN, instructions, tools, BRIEF, A.request_sha256(req), photos=photos(2),
                     owner_notes=owner_notes,
                     settings=settings, stage_contracts=(contract,), contract=L.ToolContract(frozenset({"temple_stage"})),
                     offline_handler=transport.handle, offline_api_key=OFFLINE_KEY)
    return job, spec, scene, child_scene, transport, results


def run_case(root, replies, **kwargs):
    values = setup(root, replies, **kwargs)
    job, spec, scene, child, transport, results = values
    result = asyncio.run(L.run_astra(spec, scene=scene))
    return job, scene, child, transport, results, result


def test_nested_stage_uses_one_ledger_shared_receipts_and_isolated_session_and_checkpoint_domain(tmp_path):
    job, parent, child, transport, stages, result = run_case(tmp_path, [
        R.tool_call("temple_stage"), R.tool_call("look"), R.tool_call("facet_finish"), R.message("parent done")])
    assert result["status"] == "completed", result
    assert result["requests"] == result["turns_used"] == 4
    assert transport.violations == [] and transport.unexpected == []
    assert len(list(job.rglob("budget.json"))) == 1
    assert stages[0]["budget"]["request_sequences"] == [2, 3]
    receipt = json.loads(Path(stages[0]["receipt"]).read_text())
    assert receipt["turns_used"] == 2 and receipt["exit_checkpoint"]["id"] == "c0001"
    assert len(receipt["journal"]) == 1 and parent.journal_entries() == [] and parent.restored == []
    assert child.head == "c0001" and parent.head == "c0000"
    assert len(list((job / "runs" / RUN / "transport").glob("*.json"))) == 8
    journals = list((job / "runs" / RUN / "tools").glob("*/journal.json"))
    assert len(journals) == 1 and json.loads(journals[0].read_text())["stage"] == "call_0001"
    assert receipt["context_epochs"] == [] and receipt["renewals"] == []
    async def histories():
        blobs = L.BlobStore(job / "blobs")
        parent_session = L.BlobSession(L.SESSION_ID, job / "session/session.sqlite", blobs=blobs)
        child_session = L.BlobSession(L.SESSION_ID, Path(stages[0]["receipt"]).parent / "session.sqlite", blobs=blobs)
        try:
            return await parent_session.get_items(), await child_session.get_items()
        finally:
            parent_session.close()
            child_session.close()
    parent_items, child_items = asyncio.run(histories())
    assert [item["name"] for item in parent_items if item.get("type") == "function_call"] == ["temple_stage"]
    assert [item["name"] for item in child_items if item.get("type") == "function_call"] == ["look", "facet_finish"]
    for path in job.rglob("*"):
        if path.is_file():
            assert b"data:image" not in path.read_bytes() and OFFLINE_KEY.encode() not in path.read_bytes(), path


def test_small_stage_allowance_does_not_reserve_money_or_shrink_reply(tmp_path):
    job, _, _, transport, stages, result = run_case(tmp_path, [R.tool_call("temple_stage"),
                                           R.tool_call("facet_finish"), R.message("parent done")],
                                           stage_cap="0.000001")
    assert result["status"] == "completed", result
    assert result["requests"] == 3 and stages[0]["status"] == "completed"
    assert stages[0]["budget"]["request_sequences"] == [2]
    assert stages[0]["budget"]["settled_micro_usd"] > 1
    assert transport.creates[1]["max_output_tokens"] == 4096
    ledger = json.loads((job / "runs" / RUN / "budget.json").read_text())
    assert not result["budget"]["blocked"] and not result["budget"]["held_micro_usd"]


def test_parent_and_child_reusing_call_id_preserve_distinct_receipts(tmp_path):
    job, parent, child, _, stages, result = run_case(tmp_path, [
        R.tool_call("temple_stage", call_id="same"), R.tool_call("look", call_id="same"),
        R.tool_call("facet_finish"), R.message("parent done")])
    assert result["status"] == "completed", result
    directories = list((job / "runs" / RUN / "tools").glob("*"))
    receipts = {json.loads((directory / "arguments.json").read_text())["tool"]:
                json.loads((directory / "result.json").read_text()) for directory in directories}
    assert receipts["temple_stage"]["call_id"] == "same"
    assert receipts["look"]["call_id"] == "stage:same:same"
    assert receipts["temple_stage"]["tool"] == "temple_stage" and receipts["look"]["tool"] == "look"
    assert parent.journal_entries() == [] and child.journal_entries()[0]["call_id"] == "same"


def test_child_history_is_append_only_with_legacy_filter_settings_and_counts_exact_sent_input(tmp_path):
    class Child(FakeScene):
        def context_stubs(self, shas):
            pytest.fail("runtime must not filter earlier conversation images")
    _, _, _, transport, stages, result = run_case(tmp_path, [
        R.tool_call("temple_stage"), R.tool_call("look"), R.tool_call("look"), R.tool_call("look"),
        R.tool_call("facet_finish"), R.message("parent done")], child_scene=Child())
    assert result["status"] == "completed", result
    assert [len(R.input_images(body)) for body in transport.creates] == [2, 2, 3, 4, 5, 2]
    assert transport.violations == [] and transport.unexpected == []
    child_requests = transport.creates[1:5]
    for previous, current in zip(child_requests, child_requests[1:]):
        assert current["input"][:len(previous["input"])] == previous["input"]
    assert transport.creates[-1]["input"][:len(transport.creates[0]["input"])] == transport.creates[0]["input"]
    for counted, sent in zip(transport.counts, transport.creates):
        assert counted["input"] == sent["input"]
    receipt = json.loads(Path(stages[0]["receipt"]).read_text())
    assert receipt["context_epochs"] == [] and receipt["renewals"] == []


def test_stage_cannot_exceed_approved_allowance_before_its_first_request(tmp_path):
    _, _, _, _, stages, result = run_case(tmp_path, [R.tool_call("temple_stage")], stage_cap="2.000001")
    assert result["status"] == "failed" and result["requests"] == 1 and stages == []


def test_child_inherits_exact_approval_bound_owner_notes_once_in_sdk_history_and_receipt(tmp_path):
    from glasses3d.agent.session_inputs import NOTE_PREFIX
    note = "Keep the visible fade.\nThe inside reads facet_restore and lean_deg — preserve those words."
    extra = "The hinge must stay 4 mm thick."
    job, _, _, transport, stages, result = run_case(tmp_path, [R.tool_call("temple_stage"),
        R.tool_call("facet_finish"), R.message("parent done")], owner_notes=(note, note),
        stage_notes=(note, extra, extra))
    assert result["status"] == "completed", result
    assert transport.violations == []
    directory = Path(stages[0]["receipt"]).parent
    receipt = json.loads((directory / "input-receipt.json").read_text())
    assert receipt["notes_sent"] == 2 and len(receipt["owner_notes"]) == 2
    assert result["input"]["notes_sent"] == 1  # the original parent planner still owns its deduplication

    async def history(path):
        session = L.BlobSession(L.SESSION_ID, path, blobs=L.BlobStore(job / "blobs"))
        try:
            return await session.get_items()
        finally:
            session.close()

    def texts(items):
        return [part["text"] for item in items if isinstance(item.get("content"), list)
                for part in item["content"] if isinstance(part, dict) and part.get("type") == "input_text"]

    child_text = texts(asyncio.run(history(directory / "session.sqlite")))
    parent_text = texts(asyncio.run(history(job / "session/session.sqlite")))
    assert child_text.count(NOTE_PREFIX + note) == child_text.count(NOTE_PREFIX + extra) == 1
    assert parent_text.count(NOTE_PREFIX + note) == 1 and NOTE_PREFIX + extra not in parent_text
    assert texts(transport.creates[1]["input"]).count(NOTE_PREFIX + note) == 1


@pytest.mark.parametrize("alter", [lambda spec: replace(spec, instructions=spec.instructions + " changed"),
                                  lambda spec: replace(spec, settings=replace(spec.settings, finish_k=5)),
                                  lambda spec: replace(spec, tools=spec.tools[:1])])
def test_actual_stage_contract_mismatch_sends_no_child_request(tmp_path, alter):
    _, _, _, _, stages, result = run_case(tmp_path, [R.tool_call("temple_stage")], alter=alter)
    assert result["status"] == "failed" and result["requests"] == 1 and stages == []


def test_parent_turn_allowance_counts_child_requests_before_the_next_send(tmp_path):
    _, _, _, _, _, result = run_case(tmp_path, [R.tool_call("temple_stage"), R.tool_call("look"),
                                               R.tool_call("facet_finish")], max_turns=2)
    assert result["status"] == "turn_limit", result
    assert result["requests"] == result["turns_used"] == 2


def test_unknown_child_outcome_poison_stops_parent_and_keeps_hold(tmp_path):
    _, _, _, _, stages, result = run_case(tmp_path, [R.tool_call("temple_stage"), R.http_error(503)])
    assert result["status"] == "provider_unknown", result
    assert result["requests"] == 2 and result["budget"]["unknown_micro_usd"] > 0 and stages == []


def test_stage_contract_changes_invalidate_parent_approval_before_any_request(tmp_path):
    job, spec, parent, _, _, _ = setup(tmp_path, [])
    spec.stage_contracts = (replace(spec.stage_contracts[0], maximum_micro_usd=3_000_000),)
    with pytest.raises(A.ApprovalRefused, match="changed"):
        asyncio.run(L.run_astra(spec, scene=parent))
    assert not (job / "runs" / RUN / "budget.json").exists() and parent.events == []


@pytest.mark.parametrize("dollars", ["0.8", "20"])
def test_stage_contract_normalizes_dollars_without_losing_continuation_settings(dollars):
    child = L.StageSpec("temple", "A specialist.", [], BRIEF,
                       replace(CHILD, finish_tool=None, settled_stop_usd=dollars, max_continuations=7))
    first = L.StageContract.from_spec(child, "2")
    rebuilt = replace(child, settings=L.RunSettings(**first.binding()["settings"]))
    assert L.StageContract.from_spec(rebuilt, "2.000000") == first
    assert first.binding()["settings"]["settled_stop_usd"] == B.usd_text(B.cap_to_micro(dollars))
    assert first.binding()["settings"]["max_continuations"] == 7


def test_budget_scope_counts_committed_unknown_and_never_resets_global_failure(tmp_path):
    async def exercise():
        counter, _ = fake_counter()
        guard = B.BudgetGuard(counter, tmp_path / "budget.json", "2")
        with guard.stage_budget("1") as scope:
            await guard.before_request(request())
            assert scope.summary()["held_micro_usd"] > 0
            await guard.mark_unknown("offline forced disconnect")
            assert scope.summary()["unknown_micro_usd"] == scope.summary()["committed_micro_usd"] > 0
        with pytest.raises(B.BudgetGuardError):
            await guard.before_request(request())
    asyncio.run(exercise())


def test_global_budget_refusal_inside_child_stops_whole_run(tmp_path):
    _, _, _, _, stages, result = run_case(tmp_path, [R.tool_call("temple_stage"), R.tool_call("facet_finish")],
                                          parent_cap="0.00375")
    assert result["status"] == "budget_limit" and result["requests"] == 1 and stages == []


def test_child_incomplete_response_continues_only_private_session(tmp_path):
    _, _, _, _, stages, result = run_case(tmp_path, [R.tool_call("temple_stage"), R.incomplete(),
                                                   R.tool_call("facet_finish"), R.message("parent done")])
    assert result["status"] == "completed" and result["requests"] == 4, result
    receipt = json.loads(Path(stages[0]["receipt"]).read_text())
    assert receipt["continuations"] == 1 and result["continuations"] == 0


def test_output_fitting_cannot_shrink_stage_request_or_mutate_parent_policy(tmp_path):
    async def exercise():
        counter, _ = fake_counter()
        guard = B.BudgetGuard(counter, tmp_path / "budget.json", "2", fit_output_to_budget=False)
        with pytest.raises(B.BudgetGuardError, match="shrink|fitting|settled"):
            with guard.stage_budget("0.015", fit_output_to_budget=True):
                await guard.before_request(request())
        assert guard.fit_output_to_budget is False and guard.ledger.rows == []
    asyncio.run(exercise())


def test_stop_in_child_is_global_and_prevents_further_inference(tmp_path):
    class Child(FakeScene):
        def mutate(self, *args, **kwargs):
            result = super().mutate(*args, **kwargs)
            (tmp_path / "job" / L.STOP_FILE).write_text("stop")
            return result
    _, _, _, _, stages, result = run_case(tmp_path, [R.tool_call("temple_stage"), R.tool_call("look"),
                                                   R.tool_call("facet_finish")], child_scene=Child())
    assert result["status"] == "stopped" and result["requests"] == 2 and stages == []


def test_cancelled_parent_awaits_child_transport_cleanup_before_shared_client_close(tmp_path, monkeypatch):
    async def exercise():
        job, spec, parent, child, transport, _ = setup(tmp_path, [R.tool_call("temple_stage"),
                                                               R.tool_call("facet_finish")])
        entered, cleaned = asyncio.Event(), asyncio.Event()
        closed = []
        async def handler(request):
            result = transport.handle(request)
            if request.url.path == R.CREATE_PATH and len(transport.creates) == 2:
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    await asyncio.sleep(.02)
                    cleaned.set()
            return result
        spec.offline_handler = handler
        original = L.OfflineClients.inference_client
        def client_with_checked_close(self, hooks):
            client = original(self, hooks)
            original_close = client.close
            async def close():
                closed.append(cleaned.is_set())
                await original_close()
            client.close = close
            return client
        monkeypatch.setattr(L.OfflineClients, "inference_client", client_with_checked_close)
        task = asyncio.create_task(L.run_astra(spec, scene=parent))
        await asyncio.wait_for(entered.wait(), 10)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        result = json.loads((job / "runs" / RUN / "result.json").read_text())
        assert result["status"] == "stopped" and result["budget"]["unknown_micro_usd"] > 0
        assert closed == [True] and not result["budget"]["held_micro_usd"]
    asyncio.run(exercise())
