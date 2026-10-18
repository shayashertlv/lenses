"""A real UNDERSTAND round renews and publishes over scripted transport, without Blender.

The source checkpoint and shown map are synthetic fixtures. Only worker start/fingerprint
and comparison capture are faked; round creation, approval, SDK tools, map validation,
renewal, accounting and understanding publication run through their production paths.
"""
import asyncio
from dataclasses import replace
import json
import sqlite3

import pytest

from glasses3d.agent import approvals as A, budget as B, edits, job_run, replay as R, renewal as N
from glasses3d.agent.job_scene import JobScene
from glasses3d.tests.proofs.offline_job_approval import prepare_offline_request
from .loop_support import ledger_rows
from .test_owner_edit_lifecycle_review import selected_source, _cover_round
from .test_owner_understanding_dispositions import shown, item, shown_preview


def test_understand_round_renews_with_bound_owner_request_and_publishes(shown, monkeypatch):
    h = shown
    preview_receipt = shown_preview(h, [item(h, disposition="executable")])
    source_before = edits.tree_hashes(h.fixture.source)
    _cover_round(h.root)
    doc = edits.load(h.root)
    assert doc["phase"] == "understand" and h.root.name == "round-001-understand"
    assert doc["source"]["checkpoint"] == h.fixture.chosen["id"]
    immutable_before = {name: edits.sha(h.root / name) for name in doc["immutable_files"]}

    scene = JobScene(h.root)
    checkpoint = scene.s.ck_dir / "c0000.blend"
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    checkpoint.write_bytes(scene.seed_path.read_bytes())
    assert scene.s.index.head["id"] == "c0000"
    monkeypatch.setattr(scene.s, "start", lambda: {"head": scene.s.head, "worker": None})
    monkeypatch.setattr(scene.s, "fingerprint", lambda: doc["source"]["fingerprint"])

    marker = "OWNER_EDIT_RENEWAL_OLD_EVIDENCE"
    def compare(**kwargs):
        return {"status": "ok", "note": marker, "image_delivery": "separate",
                "images": [{"path": str(h.image), "view": "front", "kind": "compare_to_photos"}]}
    monkeypatch.setattr(scene.seeing, "compare_to_photos", compare)
    summary = ("1. Current head and best checkpoint: c0000, unchanged selected source.\n"
               "2. Open differences per photo: front target remains proposed.\n"
               "3. Decisions and reasons: use the shown map target.\n"
               "4. Outstanding owner requests: " + doc["request_text"] + "\n"
               "5. Next action: submit understand_finish and await owner confirmation.")
    transport = R.ScriptedOpenAI([
        R.tool_call("compare_to_photos", call_id="owner-compare"), R.message(summary),
        R.tool_call("understand_finish", {"map_id": h.mid, "map_sha256": edits.sha(h.map),
            "capture_receipts": [h.capture_result["capture_receipt"]],
            "items": [item(h, disposition="executable")], "limitations": [], "preview_receipt": preview_receipt},
            call_id="owner-understand-finish")],
        count_tokens=lambda body: (B.Tariff.frozen().long_context_input_tokens * 4 // 5 + 1
                                   if marker in json.dumps(body) else 100))
    run_id = "owner-renewal"
    settings = replace(job_run.settings_default(), max_turns=3, max_output_tokens=100)
    spec, prepared = job_run.prepare_run(h.root, run_id, settings=settings, cap="23", write_request=False,
                                         scene=scene, offline_handler=transport.handle)
    assert prepared is scene and spec.settings.finish_tool == "understand_finish"
    with pytest.raises(A.ApprovalRefused, match="no approval-request.json"):
        A.check_approval(h.root / "runs" / run_id, current_request_sha256=spec.current_request_sha256,
                         requested_cap_usd="23")
    offline_spec, unused_scene = prepare_offline_request(h.root, run_id, transport.handle,
                                                        settings=settings, cap="23")
    assert offline_spec.current_request_sha256 == spec.current_request_sha256
    assert unused_scene.s.worker is None
    unused_scene.close()
    assert edits.read(h.root / "runs" / run_id / "approval-request.json")["offline_only"] is True
    assert edits.read(h.root / "runs" / run_id / "approval.json")["authorized_by"] == "offline_fixture"
    approval_before = (h.root / "runs" / run_id / "approval.json").read_bytes()
    request_before = (h.root / "runs" / run_id / "approval-request.json").read_bytes()

    archive_checks = []
    original_renew = N.ConversationRenewal.renew
    async def verify_archive(renewal, trigger, **kwargs):
        old_id = renewal.session.session_id
        with sqlite3.connect(h.root / "session/session.sqlite") as conn:
            before = conn.execute("SELECT message_data FROM agent_messages WHERE session_id=? ORDER BY id",
                                  (old_id,)).fetchall()
        await original_renew(renewal, trigger, **kwargs)
        with sqlite3.connect(h.root / "session/session.sqlite") as conn:
            after = conn.execute("SELECT message_data FROM agent_messages WHERE session_id=? ORDER BY id",
                                 (old_id,)).fetchall()
        assert before and after[:len(before)] == before
        archive_checks.append(True)
    monkeypatch.setattr(N.ConversationRenewal, "renew", verify_archive)
    outcome = asyncio.run(job_run.execute_run(h.root, run_id, scene=scene, offline_handler=transport.handle))
    result, delivery = outcome["result"], outcome["delivery"]
    assert result["status"] == "completed", result
    assert archive_checks == [True] and result["requests"] == result["turns_used"] == 3
    assert result["tool_calls"] == 2 and transport.violations == []
    assert len(result["renewals"]) == 1
    renewal = result["renewals"][0]
    assert renewal["host_state"]["owner_edit_phase"] == "understand"
    assert renewal["host_state"]["owner_requests_supplied_for_this_run"] == list(spec.owner_notes)
    assert renewal["summary_text"] == summary and renewal["summary_request_sequence"] == 2
    assert renewal["evidence"][0]["call_id"] == "owner-compare"
    assert renewal["evidence"][0]["sha256"] == edits.sha(h.image)
    old, summary_request, fresh = transport.creates
    assert old["instructions"] == summary_request["instructions"] == fresh["instructions"] == spec.instructions
    assert old["tools"] == summary_request["tools"] == fresh["tools"]
    texts = [part["text"] for row in fresh["input"] for part in row.get("content", [])
             if isinstance(part, dict) and part.get("type") == "input_text"]
    assert any(spec.brief in text for text in texts)
    assert any(doc["request_text"] in text and "Edit phase: understand" in text for text in texts)
    assert all(photo.image_url in R.input_images(fresh) for photo in spec.photos)
    assert len(R.input_images(fresh)) == len(spec.photos) + 1
    assert marker not in json.dumps(fresh) and N.FINISH_RESTART_NOTICE not in json.dumps(fresh)
    assert summary_request["input"][:-1] == transport.counts[1]["input"]
    assert summary_request["tool_choice"] == "none"

    assert delivery["status"] == "awaiting_owner_confirmation" and delivery["geometry_changed"] is False
    assert not delivery["finished"] and not delivery["accepted_finish"]
    artifact = edits.read(h.root / "work/owner_edit/understanding.json")
    assert artifact["request_text"] == doc["request_text"] and artifact["operations"]
    assert artifact["source_fingerprint"] == doc["source"]["fingerprint"]
    assert edits.read(h.root / "work/owner_edit/understanding-delivery.json")["delivered"] is True
    assert delivery["review_page"] and (h.root / "work/owner_edit/understanding-usage.json").is_file()
    assert len(list(h.root.rglob("budget.json"))) == 1
    rows = ledger_rows(h.root, run_id)
    assert len(rows) == 3 and all(row["outcome"] == "settled" for row in rows)
    assert result["budget"]["held_micro_usd"] == result["budget"]["unknown_micro_usd"] == 0
    assert (h.root / "runs" / run_id / "approval.json").read_bytes() == approval_before
    assert (h.root / "runs" / run_id / "approval-request.json").read_bytes() == request_before
    assert {name: edits.sha(h.root / name) for name in doc["immutable_files"]} == immutable_before
    assert edits.load(h.root)["source"] == doc["source"]
    assert edits.tree_hashes(h.fixture.source) == source_before
    assert scene.s.worker is None and all(not event["mutating"] for event in scene.events)
