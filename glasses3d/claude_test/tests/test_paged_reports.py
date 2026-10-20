"""T1b paging reminders over the real runner, SDK and guarded offline wire."""
from __future__ import annotations

import asyncio
import copy
from dataclasses import replace
import json
from types import SimpleNamespace

from agents import function_tool
from agents.tool import ToolOutputText
from agents.tool_context import ToolContext
import pytest

from glasses3d.agent.tool_reports import limit_output, read_report as read_saved_report
from glasses3d.claude_test import runner as R, tool_adapter, tool_use
from .test_runner import Harness, call, root, turn


def configure(h, *, source="scene_summary", override=None):
    """Use the shared pager and reader around a controlled scene operation."""
    original = [
        ToolOutputText(text=json.dumps({
            "status": "accepted" if source == "finish" else "ok",
            "rows": [{"index": n, "detail": "unaltered detail " * 20} for n in range(100)],
        }) + "\nOriginal footer."),
        ToolOutputText(text="Original trailing text."),
    ]
    emitted = limit_output(h.root, "fixture", source, original) if override is None else override
    frozen = copy.deepcopy(emitted)

    @function_tool
    async def operation(ctx: ToolContext):
        """Return the controlled result without changing its content."""
        if source == "execute_blender_code":
            h.scene.s.head = {**h.scene.s.head, "fingerprint": "changed"}
        if source == "finish":
            h.scene.finish = {"status": "accepted", "call_id": ctx.tool_call_id,
                              "checkpoint": h.scene.s.head["id"]}
        return emitted

    operation.name = source

    @function_tool
    async def read_report(report: str, pointer: str, offset: int):
        """Read an actual page from the shared immutable report store."""
        try:
            value = read_saved_report(h.root, report, pointer=pointer, offset=offset, limit=1)
        except (ValueError, OSError) as exc:
            value = {"status": "refused", "message": str(exc)}
        return json.dumps(value)

    tools = [operation]
    if source != "read_report":
        tools.append(read_report)
    if source != "finish":
        tools.append(h.spec.tools[-1])
    h.spec = replace(h.spec, tools=tools)
    h.runtime = R._Runtime(
        spec=h.spec, scene=h.scene, conversation=h.conversation, ledger=h.ledger,
        client=h.client, files=h.files, log=h.log, directory=h.root, shared=h.shared)
    return emitted, frozen


def rows(h):
    return [json.loads(line) for line in (h.root / "tool-use.jsonl").read_text(
        encoding="utf-8").splitlines()]


def blocks(emitted):
    return [{"type": "text", "text": part.text} for part in emitted]


def report_id(emitted):
    value, _ = json.JSONDecoder().raw_decode(emitted[0].text)
    return value["full_report"]["id"]


def test_paged_note_correct_id_real_reader_receipts_and_summary(root):
    h = Harness(root, [])
    emitted, frozen = configure(h)
    digest = report_id(emitted)
    h.wire.turns.extend([
        turn(call("scene_summary", "paged")),
        turn(call("read_report", "page1", report=digest, pointer="/result/rows", offset=0)),
        turn(call("read_report", "page2", report=digest, pointer="/result/rows", offset=1)),
        turn(call("finish", "done")),
    ])
    try:
        assert asyncio.run(h.drive()) == "completed"
        note = f"Host: this result is paged; the full report is available with read_report (id {digest})."
        received = rows(h)
        assert received[0]["paged_report"] == digest
        assert received[0]["paged_note"] == note
        assert received[0]["result"]["content"] == blocks(frozen) + [{"type": "text", "text": note}]
        assert emitted == frozen
        for index, row in enumerate(received[:-1]):
            assert h.wire.requests[index + 1]["messages"][-1]["content"][0] == row["result"]
        assert all(row["paged_note"] is None for row in received[1:])
        assert [row["report_read"] for row in received[1:3]] == [digest, digest]
        sent = [json.loads(line) for line in (root / "tool-use-sent.jsonl").read_text(
            encoding="utf-8").splitlines()]
        assert sent == [{"call_id": "paged", "request_no": 2}]
        metrics = tool_use.summarize(root)
        assert metrics["paged_results"] == metrics["paged_results_later_read"] == 1
        assert metrics["paged_notes_sent"] == metrics["host_notes_sent"] == 1
        summary = R.write_summary(SimpleNamespace(run_dir=root, run_id="test", offline=True), {
            "status": "completed", "seconds": 0, "requests": 4,
            "budget": h.ledger.money_status(),
        }, {"status": "not_delivered"})
        assert summary["tool_use"] == metrics
        h.wire.assert_complete()
    finally:
        h.close()


def test_change_note_then_paged_note_fixed_order_and_both_counted(root):
    h = Harness(root, [
        turn(call("execute_blender_code", "edit")),
        turn(call("finish", "done")),
    ])
    emitted, frozen = configure(h, source="execute_blender_code")
    try:
        assert asyncio.run(h.drive()) == "completed"
        row = rows(h)[0]
        assert row["host_note"].startswith("Host: 1 model change")
        assert row["paged_note"].endswith(f"(id {report_id(emitted)}).")
        assert row["result"]["content"] == blocks(frozen) + [
            {"type": "text", "text": row["host_note"]},
            {"type": "text", "text": row["paged_note"]},
        ]
        assert h.wire.requests[1]["messages"][-1]["content"][0] == row["result"]
        assert emitted == frozen
        metrics = tool_use.summarize(root)
        assert metrics["host_notes_sent"] == metrics["host_notes_prepared"] == 2
        assert metrics["paged_notes_sent"] == metrics["paged_results"] == 1
        assert metrics["paged_results_later_read"] == 0
        assert metrics["longest_no_comparison_streak"] == 1
        h.wire.assert_complete()
    finally:
        h.close()


@pytest.mark.parametrize("source,value", [
    ("scene_summary", {"status": "ok", "value": "ordinary"}),
    ("scene_summary", {"status": "ok", "nested": {"paged": True, "full_report": {"id": "a" * 64}}}),
    ("scene_summary", {"status": "ok", "paged": False, "full_report": {"id": "a" * 64}}),
    ("scene_summary", {"status": "ok", "paged": "true", "full_report": {"id": "a" * 64}}),
    ("scene_summary", {"status": "ok", "paged": True, "full_report": {}}),
    ("scene_summary", {"status": "ok", "paged": True, "full_report": {"id": "not-an-id"}}),
    ("scene_summary", {"status": "ok", "paged": True, "full_report": {"id": "A" * 64}}),
    # Even a reader's own newly paged envelope must never trigger another reminder.
    ("read_report", {"status": "ok", "paged": True, "full_report": {"id": "a" * 64}}),
])
def test_nonpaged_invalid_contract_and_reader_pages_get_no_note(root, source, value):
    h = Harness(root, [turn(call(source, "read")), turn(call("finish", "done"))])
    emitted, frozen = configure(h, source=source, override=[
        ToolOutputText(text=json.dumps(value) + "\nOriginal footer.")])
    try:
        assert asyncio.run(h.drive()) == "completed"
        row = rows(h)[0]
        assert row["paged_report"] is row["paged_note"] is None
        assert h.wire.requests[1]["messages"][-1]["content"][0]["content"] == blocks(frozen)
        assert emitted == frozen
        metrics = tool_use.summarize(root)
        assert metrics["paged_results"] == metrics["paged_notes_sent"] == metrics["host_notes_sent"] == 0
        h.wire.assert_complete()
    finally:
        h.close()


def test_only_later_successful_reads_count_each_result_once(root):
    h = Harness(root, [])
    emitted, _ = configure(h)
    digest = report_id(emitted)
    h.wire.turns.extend([
        turn(call("scene_summary", "first")),
        turn(call("read_report", "refused", report=digest, pointer="/absent", offset=0)),
        turn(call("read_report", "page1", report=digest, pointer="/result/rows", offset=0)),
        turn(call("read_report", "page2", report=digest, pointer="/result/rows", offset=1)),
        turn(call("scene_summary", "later_same_report")),
        turn(call("finish", "done")),
    ])
    try:
        assert asyncio.run(h.drive()) == "completed"
        assert rows(h)[1]["report_read"] is None
        metrics = tool_use.summarize(root)
        assert metrics["paged_results"] == metrics["paged_notes_sent"] == 2
        assert metrics["paged_results_later_read"] == 1
        h.wire.assert_complete()
    finally:
        h.close()


def test_final_paged_finish_prepared_but_not_sent(root):
    h = Harness(root, [turn(call("finish", "done"))])
    emitted, frozen = configure(h, source="finish")
    try:
        assert asyncio.run(h.drive()) == "completed"
        metrics = tool_use.summarize(root)
        assert metrics["paged_results"] == metrics["host_notes_prepared"] == 1
        assert metrics["paged_results_later_read"] == metrics["paged_notes_sent"] == metrics["host_notes_sent"] == 0
        assert h.runtime.adapter.last_raw_output == frozen == emitted
        assert len(h.wire.requests) == 1
        h.wire.assert_complete()
    finally:
        h.close()


def test_failed_output_conversion_does_not_advertise_discarded_paged_result(root, monkeypatch):
    h = Harness(root, [turn(call("scene_summary", "bad")), turn(call("finish", "done"))])
    emitted, frozen = configure(h)
    original_prepare = tool_adapter.prepare_images

    def fail_paged(output, **kwargs):
        if output == emitted:
            raise ValueError("offline conversion failure")
        return original_prepare(output, **kwargs)

    monkeypatch.setattr(tool_adapter, "prepare_images", fail_paged)
    try:
        assert asyncio.run(h.drive()) == "completed"
        row = rows(h)[0]
        assert row["result"]["is_error"] is True
        assert row["paged_note"] is row["paged_report"] is None
        assert emitted == frozen
        assert tool_use.summarize(root)["paged_results"] == 0
        h.wire.assert_complete()
    finally:
        h.close()
