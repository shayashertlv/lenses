"""$0 scripted transport proofs for all three pressure measures and durable renewal."""
import asyncio
import hashlib
import json
import sqlite3
from dataclasses import replace

import pytest
from agents import function_tool
from agents.tool import ToolOutputImage, ToolOutputText

from glasses3d.agent import budget as B, loop as L, replay as R, renewal as N, delivery as D
from glasses3d.tests.agent.loop_support import (FakeScene, make_job, spec_for, png_bytes, data_url,
                                               photos, ledger_rows)
from glasses3d.tests.agent.test_delivery import make_finished

RUN = "20261007T120000-run001"
SUMMARY = "1. Current head and best checkpoint: c0001.\n2. Open differences per photo: front none; side none.\n3. Decisions and reasons: checked.\n4. Outstanding owner requests: preserved.\n5. Next action: finish."


class EvidenceScene(FakeScene):
    def __init__(self, root):
        super().__init__()
        self.best_checkpoint = "c0001"
        self.records = []
        self.pending = True
        for n, view in enumerate(("front", "side")):
            path = root / (view + ".png")
            path.write_bytes(png_bytes(seed=31+n))
            self.records.append({"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                 "call_id": "comparison", "checkpoint": "c0001", "view": view,
                                 "kind": "compare_look_to_photos"})

    def renewal_evidence(self):
        return self.records

    def renewal_started(self):
        self.pending = False
        return {"finish_review_restarted": True}


def case(tmp_path, monkeypatch, kind, *, cap="23", notes=(), max_turns=20):
    scene = EvidenceScene(tmp_path)
    image = data_url(png_bytes(seed=31))
    n_images = B.PAYLOAD_LIMIT_IMAGES * 4 // 5 if kind == "images" else 2
    if kind == "payload_bytes":
        monkeypatch.setattr(B, "PAYLOAD_LIMIT_BYTES", 100_000)

    @function_tool
    async def grow():
        """Return obsolete evidence plus current photo comparisons."""
        scene.head = "c0001"
        padding = "x" * 80_000 if kind == "payload_bytes" else ""
        return [ToolOutputText(text="OLD_EVIDENCE " + padding),
                *[ToolOutputImage(image_url=image, detail="high") for _ in range(n_images)]]

    @function_tool
    async def finish():
        """Finish the checked synthetic model."""
        return json.dumps({"status": "accepted"})

    tools = [grow, finish]
    settings = L.RunSettings(effort="max", max_turns=max_turns, max_output_tokens=100, finish_tool="finish", settled_stop_usd="20")
    from glasses3d.tests.agent import loop_support
    with monkeypatch.context() as patch:
        patch.setattr(loop_support, "tool_registry_sha256", lambda tools: L.tool_registry_sha256(tools, settings))
        job, sha = make_job(tmp_path, tools=tools, settings=settings, cap=cap, notes=notes)
    def count(body):
        if kind == "input_tokens" and "OLD_EVIDENCE" in json.dumps(body):
            return B.Tariff.frozen().long_context_input_tokens * 4 // 5 + 1
        return 100
    transport = R.ScriptedOpenAI([R.tool_call("grow"), R.message(SUMMARY), R.tool_call("finish")],
                                 count_tokens=count)
    original_renew = N.ConversationRenewal.renew
    async def verify_archive(renewal, trigger, **kwargs):
        with sqlite3.connect(job / "session/session.sqlite") as conn:
            before = conn.execute("SELECT message_data FROM agent_messages WHERE session_id=? ORDER BY id",
                                  (renewal.session.session_id,)).fetchall()
        old_id = renewal.session.session_id
        await original_renew(renewal, trigger, **kwargs)
        with sqlite3.connect(job / "session/session.sqlite") as conn:
            after = conn.execute("SELECT message_data FROM agent_messages WHERE session_id=? ORDER BY id",
                                 (old_id,)).fetchall()
        assert before and after[:len(before)] == before  # exact stored bytes, before and after renewal
        scene.archive_before_renewal = before
    monkeypatch.setattr(N.ConversationRenewal, "renew", verify_archive)
    result = asyncio.run(L.run_astra(spec_for(job, RUN, sha, tools, transport, settings=settings, notes=notes),
                                    scene=scene))
    return job, scene, transport, result


@pytest.mark.parametrize("kind", ["images", "payload_bytes", "input_tokens"])
def test_renewal_counts_exact_candidate_preserves_prefix_photos_evidence_ledger_and_delivers(tmp_path, monkeypatch, kind):
    job, scene, transport, result = case(tmp_path, monkeypatch, kind)
    assert result["status"] == "completed", result
    assert transport.violations == []
    assert result["requests"] == 3 and len(result["renewals"]) == 1
    row = result["renewals"][0]
    assert kind in {r["reason"] for r in row["reasons"]}
    assert row["summary_text"] == SUMMARY and row["summary_sha256"] == hashlib.sha256(SUMMARY.encode()).hexdigest()
    assert row["summary_request_sequence"] == 2 and row["summary_skipped"] is None
    assert not scene.pending
    old, summary, fresh = transport.creates
    assert summary["input"][:-1] == transport.counts[1]["input"]
    assert summary["input"][-1]["content"] == N.SUMMARY_REQUEST and summary["tool_choice"] == "none"
    for photo in photos(2):
        assert photo.image_url in R.input_images(fresh)
    for record in scene.records:
        assert record["sha256"] in json.dumps(fresh)
        assert record["view"] in json.dumps(fresh)
    assert json.dumps(SUMMARY)[1:-1] in json.dumps(fresh) and "OLD_EVIDENCE" not in json.dumps(fresh)
    assert len(R.input_images(fresh)) == 4
    assert old["tools"] == fresh["tools"] == summary["tools"]
    assert N.FINISH_RESTART_NOTICE in json.dumps(fresh)
    assert N.durable_receipts(job / "session/session.sqlite")[0]["new_conversation_id"] == row["new_conversation_id"]
    rows = ledger_rows(job, RUN)
    assert len(rows) == 3 and all(r["outcome"] == "settled" for r in rows)
    assert result["budget"]["held_micro_usd"] == result["budget"]["unknown_micro_usd"] == 0
    assert len(list((job / "runs" / RUN / "transport").glob("*.json"))) == 6
    # Archived original conversation remains byte-identical after a new one starts.
    with sqlite3.connect(job / "session/session.sqlite") as conn:
        frozen = conn.execute("SELECT message_data FROM agent_messages WHERE session_id=? ORDER BY id", ("astra",)).fetchall()
    assert frozen[:len(scene.archive_before_renewal)] == scene.archive_before_renewal
    async def read_active():
        session = L.BlobSession("astra", job / "session/session.sqlite", blobs=L.BlobStore(job / "blobs"))
        try:
            assert session.session_id == row["new_conversation_id"]
            return await session.get_items()
        finally:
            session.close()
    active = asyncio.run(read_active())
    assert "OLD_EVIDENCE" not in json.dumps(active)
    with sqlite3.connect(job / "session/session.sqlite") as conn:
        assert frozen == conn.execute("SELECT message_data FROM agent_messages WHERE session_id=? ORDER BY id", ("astra",)).fetchall()
    deliver_scene, delivery_result = make_finished(job, run_id=RUN)
    result["exit_checkpoint"] = delivery_result["exit_checkpoint"]
    delivered = D.deliver_finished(job, deliver_scene, result)
    assert delivered["status"] == "delivered"
    assert D.load_delivery(job)["report_data"]["conversation_renewals"] == result["renewals"]


def test_owner_edit_notes_survive_verbatim(tmp_path, monkeypatch):
    note = "Owner's edit request:\nMove only the selected corner inward. Keep the marking."
    _, _, transport, result = case(tmp_path, monkeypatch, "input_tokens", notes=(note,))
    assert result["status"] == "completed"
    assert json.dumps(note)[1:-1] in json.dumps(transport.creates[-1])
    assert result["renewals"][0]["host_state"]["owner_requests_supplied_for_this_run"] == [note]


def test_summary_unaffordable_renews_from_factual_state(tmp_path, monkeypatch):
    # An old 217601-token summary cannot reserve within $1; fresh 100-token input can.
    original = R.ScriptedOpenAI
    def script(replies, **kwargs):
        return original([replies[0], replies[-1]], **kwargs)
    monkeypatch.setattr(R, "ScriptedOpenAI", script)
    _, _, transport, result = case(tmp_path, monkeypatch, "input_tokens", cap="1", max_turns=2)
    assert result["status"] == "completed", result
    assert result["requests"] == 2
    row = result["renewals"][0]
    assert row["summary_sha256"] is None and row["summary_skipped"].startswith("BudgetExceeded:")
    assert N.NO_SUMMARY in json.dumps(transport.creates[-1])
    assert transport.violations == [] and result["budget"]["held_micro_usd"] == 0


def _assert_paid_summary_fallback(job, transport, result, reason):
    assert result["status"] == "completed", result
    assert result["requests"] == result["turns_used"] == 3
    assert result["tool_calls"] == 2
    assert len(transport.creates) == 3 and transport.violations == []
    assert len(result["renewals"]) == 1
    row = result["renewals"][0]
    assert row["summary_text"] == "" and row["summary_sha256"] is None
    assert row["summary_request_sequence"] == 2 and row["summary_skipped"] == reason
    old, summary, fresh = transport.creates
    assert summary["input"][:-1] == transport.counts[1]["input"]
    assert summary["input"][-1]["content"] == N.SUMMARY_REQUEST
    assert summary["tool_choice"] == "none"
    assert old["tools"] == summary["tools"] == fresh["tools"]
    texts = [part["text"] for item in fresh["input"] for part in item.get("content", [])
             if isinstance(part, dict) and part.get("type") == "input_text"]
    assert N.NO_SUMMARY in texts and any(text.startswith(N.HOST_STATE_PREFIX) for text in texts)
    assert not any(text.startswith(N.ASTRA_SUMMARY_PREFIX) for text in texts)
    assert "OLD_EVIDENCE" not in json.dumps(fresh)
    assert row["host_state"]["head_checkpoint"] == "c0001"
    assert [call["name"] for call in row["host_state"]["tool_calls_so_far"]] == ["grow"]
    assert all(photo.image_url in R.input_images(fresh) for photo in photos(2))
    assert len(R.input_images(fresh)) == 4
    rows = ledger_rows(job, RUN)
    assert len(rows) == 3 and all(row["outcome"] == "settled" for row in rows)
    assert result["budget"]["held_micro_usd"] == result["budget"]["unknown_micro_usd"] == 0
    return fresh


def test_empty_summary_renews_from_factual_state(tmp_path, monkeypatch):
    original = R.ScriptedOpenAI
    def script(replies, **kwargs):
        return original([replies[0], R.message(""), replies[-1]], **kwargs)
    monkeypatch.setattr(R, "ScriptedOpenAI", script)
    job, _, transport, result = case(tmp_path, monkeypatch, "input_tokens", max_turns=3)
    _assert_paid_summary_fallback(job, transport, result, "summary response contained no written state")


def test_tool_call_summary_is_never_executed_or_carried_into_fresh_conversation(tmp_path, monkeypatch):
    original = R.ScriptedOpenAI
    call_id, text = "summary_must_not_run", "MALFORMED_SUMMARY_MUST_NOT_CARRY"
    def script(replies, **kwargs):
        return original([replies[0], R.tool_call("grow", call_id=call_id, text=text), replies[-1]], **kwargs)
    monkeypatch.setattr(R, "ScriptedOpenAI", script)
    job, _, transport, result = case(tmp_path, monkeypatch, "input_tokens", max_turns=3)
    fresh = _assert_paid_summary_fallback(job, transport, result,
                                         "summary returned a tool call; no tool was executed")
    assert call_id not in json.dumps(fresh) and text not in json.dumps(fresh)
    with sqlite3.connect(job / "session/session.sqlite") as conn:
        active = conn.execute("SELECT message_data FROM agent_messages WHERE session_id=? ORDER BY id",
                              (result["renewals"][0]["new_conversation_id"],)).fetchall()
    assert call_id not in json.dumps(active) and text not in json.dumps(active)


def test_exact_eighty_percent_does_not_trigger():
    tariff = B.Tariff.frozen()
    assert N.pressure(B.PAYLOAD_LIMIT_BYTES * 4 // 5, B.PAYLOAD_LIMIT_IMAGES * 4 // 5,
                      tariff.long_context_input_tokens * 4 // 5, tariff) == []


def test_specialist_renews_inside_shared_approval_and_money(tmp_path, monkeypatch):
    from glasses3d.tests.agent.test_loop_stages import setup, RUN as stage_run
    job, spec, scene, child, transport, _ = setup(tmp_path, [
        R.tool_call("temple_stage"), R.tool_call("look"), R.message(SUMMARY),
        R.tool_call("facet_finish"), R.message("parent done")], parent_cap="23", stage_cap="10", allowance="10",
        max_turns=20)
    transport.count_tokens = lambda body: (B.Tariff.frozen().long_context_input_tokens * 4 // 5 + 1
        if "private checkpoint c0001" in json.dumps(body) else 100)
    result = asyncio.run(L.run_astra(spec, scene=scene))
    assert result["status"] == "completed", result
    assert result["renewals"] == [] and len(result["stages"][0]["renewals"]) == 1
    assert result["requests"] == 5 and len(list(job.rglob("budget.json"))) == 1
    assert result["stages"][0]["budget"]["request_sequences"] == [2, 3, 4]
    assert transport.violations == []
    assert all(photo.image_url in R.input_images(transport.creates[3]) for photo in photos(2))
    assert N.FINISH_RESTART_NOTICE not in json.dumps(transport.creates[3])
