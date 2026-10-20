"""Small receipt fixtures for C3's note audit; these do not run native tools."""
from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from glasses3d.claude_test.proof_c3 import audit_tool_use


def text(value):
    return {"type": "text", "text": value}


def change_note(count, comparison):
    noun = "change" if count == 1 else "changes"
    return (
        f"Host: {count} model {noun} since your last {comparison}. "
        "Dedicated tools for the next step: compare_to_photos (shape), "
        "compare_look_to_photos (colour/material), photo_read or pick_surface_point (measure)."
    )


def paging_note(digest):
    return (
        "Host: this result is paged; the full report is available with read_report "
        f"(id {digest})."
    )


def persist(case):
    """Keep wire and receipts consistent, so tamper tests exercise the audit itself."""
    for filename, values in (
        ("tool-use.jsonl", case.rows), ("tool-use-sent.jsonl", case.sent),
    ):
        (case.run_dir / filename).write_text(
            "".join(json.dumps(value) + "\n" for value in values), encoding="utf-8")
    (case.run_dir / "summary.json").write_text(
        json.dumps({"tool_use": case.metrics}), encoding="utf-8")
    messages = [{"role": "user", "content": [text("Offline audit fixture.")]}]
    case.wire.requests = [copy.deepcopy({"messages": messages})]
    # The final accepted finish has a durable result but no subsequent request.
    for row in case.rows[:-1]:
        messages.append({"role": "user", "content": [copy.deepcopy(row["result"])]})
        case.wire.requests.append(copy.deepcopy({"messages": messages}))


@pytest.fixture
def audit_case(tmp_path):
    # Real C3's fixed three edits and comparison resets, with paging on an edit,
    # a comparison, and the terminal accepted finish.
    script = [
        ("c3_compare", "compare_to_photos", "f0", "f0", 0, None, None),
        ("c3_edit", "execute_blender_code", "f0", "f1", 1,
         change_note(1, "compare_to_photos"), "a" * 64),
        ("c3_read_only", "execute_blender_code", "f1", "f1", 1, None, None),
        ("c3_edit_second", "execute_blender_code", "f1", "f2", 2,
         change_note(2, "compare_to_photos"), None),
        ("c3_look", "compare_look_to_photos", "f2", "f2", 0, None, "b" * 64),
        ("c3_edit_after_compare", "execute_blender_code", "f2", "f3", 1,
         change_note(1, "compare_look_to_photos"), None),
        ("c3_finish_accept", "finish", "f3", "f3", 1, None, "c" * 64),
    ]
    rows, events, replies, sent = [], [], [], []
    for index, (cid, name, before, after, streak, note, digest) in enumerate(script):
        body = {"status": "accepted" if name == "finish" else "ok",
                "value": {"original": [1, "unchanged"]}}
        if digest is not None:
            body.update(paged=True, full_report={"id": digest})
        original = [text(json.dumps(body) + "\nOriginal footer."), text("Original trailing text.")]
        original_dir = tmp_path / "tools" / cid
        original_dir.mkdir(parents=True)
        (original_dir / "result.json").write_text(
            json.dumps({"call_id": cid, "result": original}), encoding="utf-8")
        reminder = paging_note(digest) if digest else None
        notes = [text(value) for value in (note, reminder) if value is not None]
        rows.append({
            "call_id": cid, "name": name, "stage": None, "successful": True,
            "scene_change": {"before": before, "after": after, "changed": before != after},
            "comparison_reset": name in {"compare_to_photos", "compare_look_to_photos"},
            "changes_since_comparison": streak, "host_note": note,
            "paged_report": digest, "paged_note": reminder, "report_read": None,
            "result": {"type": "tool_result", "tool_use_id": cid, "content": original + notes},
        })
        if name == "execute_blender_code":
            events.append({"call_id": cid, "fingerprint_before": before,
                           "fingerprint_after": after})
        replies.append(SimpleNamespace(content=[
            {"type": "tool_use", "id": cid, "name": name, "input": {}}]))
        if notes and name != "finish":
            sent.append({"call_id": cid, "request_no": index + 2})
    metrics = {
        "per_tool_counts": {
            "compare_to_photos": 1, "execute_blender_code": 4,
            "compare_look_to_photos": 1, "finish": 1,
        },
        "total_calls": 7, "dedicated_tool_calls": 3, "dedicated_tool_share": 3 / 7,
        "longest_no_comparison_streak": 2, "photo_measurements": 0,
        "photo_measurement_calls": 0, "unresolved_measurement_calls": 0,
        "host_notes_sent": 5, "host_notes_prepared": 6,
        "paged_results": 3, "paged_notes_sent": 2, "paged_results_later_read": 0,
    }
    case = SimpleNamespace(run_dir=tmp_path, rows=rows, sent=sent, metrics=metrics,
                           scene=SimpleNamespace(events=events),
                           wire=SimpleNamespace(replies=replies, requests=[]))
    persist(case)
    return case


def test_c3_audit_accepts_both_notes_and_unsent_paged_finish(audit_case):
    case = audit_case
    result = audit_tool_use(case.run_dir, case.scene, case.wire)
    assert result["status"] == "passed"
    assert result["metrics"] == case.metrics
    assert result["scene_changing_scripts"] == [
        "c3_edit", "c3_edit_second", "c3_edit_after_compare",
    ]
    assert len(case.sent) == 4  # One result carries two separate host notes.
    assert result["metrics"]["host_notes_sent"] == 5
    assert result["metrics"]["host_notes_prepared"] == 6


@pytest.mark.parametrize("tamper", [
    "wrong_paged_id", "wrong_paged_text", "missing_paged_note", "reversed_note_order",
    "missing_change_note", "changed_original", "unsent_finish_claimed", "wrong_note_total",
])
def test_c3_audit_rejects_note_and_preservation_tampering(audit_case, tamper):
    case = audit_case
    edit = case.rows[1]
    if tamper == "wrong_paged_id":
        edit["paged_report"] = "d" * 64
    elif tamper == "wrong_paged_text":
        edit["paged_note"] = paging_note("d" * 64)
        edit["result"]["content"][-1] = text(edit["paged_note"])
    elif tamper == "missing_paged_note":
        comparison = case.rows[4]
        comparison["paged_note"] = None
        comparison["result"]["content"].pop()
    elif tamper == "reversed_note_order":
        edit["result"]["content"][-2:] = reversed(edit["result"]["content"][-2:])
    elif tamper == "missing_change_note":
        edit["host_note"] = None
        edit["result"]["content"].pop(-2)
    elif tamper == "changed_original":
        edit["result"]["content"][1] = text("Replaced original trailing text.")
    elif tamper == "unsent_finish_claimed":
        case.sent.append({"call_id": "c3_finish_accept", "request_no": 8})
    elif tamper == "wrong_note_total":
        case.metrics["host_notes_sent"] = 4
    persist(case)
    with pytest.raises(AssertionError):
        audit_tool_use(case.run_dir, case.scene, case.wire)
