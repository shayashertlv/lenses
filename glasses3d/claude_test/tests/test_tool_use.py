"""T1 feedback is additive, factual and visible through the real SDK/guard wire."""
from __future__ import annotations

import asyncio
import copy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from agents import function_tool
from agents.tool_context import ToolContext
import pytest

from glasses3d.agent.instructions import build_instructions
from glasses3d.agent.loop import RunSettings
from glasses3d.agent.tools import build_tools
from glasses3d.claude_test import runner as R, tool_use
from glasses3d.claude_test.tool_adapter import (
    TOOL_PREFERENCE_PARAGRAPH, claude_instructions, prepare_images,
)
from .test_runner import Harness, call, root, turn


def configure(h):
    """Controlled journaled scene, real runner/adapter/SDK/transport around it."""
    outputs = {}

    @function_tool
    async def execute_blender_code(ctx: ToolContext, change: bool, refused: bool = False):
        """A deterministic scene edit or read for the offline wire."""
        before = copy.deepcopy(h.scene.s.head)
        # A checkpoint-only write must not produce a model-change note.
        h.scene.s.head = {**before, "id": before["id"] + "x"}
        if change:
            h.scene.s.head["fingerprint"] = before["fingerprint"] + "x"
        h.scene.rows.append({
            "call_id": ctx.tool_call_id, "tool": "execute_blender_code",
            "fingerprint_before": before["fingerprint"],
            "fingerprint_after": h.scene.s.head["fingerprint"],
        })
        output = json.dumps({"status": "refused" if refused else "ok",
                             "value": {"original": [1, "unaltered"], "change": change}})
        outputs[ctx.tool_call_id] = output
        return output

    @function_tool
    async def compare_to_photos(ctx: ToolContext, refused: bool = False):
        """A comparison that can be refused without evidence."""
        output = json.dumps({"status": "refused" if refused else "ok", "shape": "unchanged"})
        outputs[ctx.tool_call_id] = output
        return output

    @function_tool
    async def compare_look_to_photos(ctx: ToolContext):
        """A completed look comparison."""
        output = json.dumps({"status": "ok", "look": "unchanged"})
        outputs[ctx.tool_call_id] = output
        return output

    @function_tool
    async def photo_read(ctx: ToolContext):
        """A completed photo measurement."""
        output = json.dumps({"status": "partial", "readings": [
            {"status": "ok", "xyz_mm": [1, 2, 3]}, {"status": "refused", "code": "E_PHOTO_READ"}]})
        outputs[ctx.tool_call_id] = output
        return output

    @function_tool
    async def pick_surface_point(ctx: ToolContext):
        """A refused photo pick."""
        output = json.dumps({"status": "ok", "hits": [{"hit": False}]})
        outputs[ctx.tool_call_id] = output
        return output

    source = build_instructions(build_tools(h.scene, RunSettings()))
    tools = (execute_blender_code, compare_to_photos, compare_look_to_photos, photo_read,
             pick_surface_point, h.spec.tools[-1])
    h.spec = replace(h.spec, tools=tools, instructions=claude_instructions(source),
                     source_instructions=source)
    h.runtime = R._Runtime(
        spec=h.spec, scene=h.scene, conversation=h.conversation, ledger=h.ledger,
        client=h.client, files=h.files, log=h.log, directory=h.root, shared=h.shared)
    return outputs


def receipts(root):
    return [json.loads(line) for line in (root / "tool-use.jsonl").read_text(
        encoding="utf-8").splitlines()]


def test_notes_through_runner_wire_reset_and_preserve_results(root):
    h = Harness(root, [
        turn(call("execute_blender_code", "edit1", change=True, refused=False)),
        turn(call("execute_blender_code", "read", change=False, refused=False)),
        turn(call("photo_read", "measure")),
        turn(call("execute_blender_code", "edit2", change=True, refused=False)),
        turn(call("compare_to_photos", "shape", refused=False)),
        turn(call("execute_blender_code", "edit3", change=True, refused=False)),
        turn(call("compare_look_to_photos", "look")),
        turn(call("execute_blender_code", "edit4", change=True, refused=False)),
        turn(call("pick_surface_point", "miss")),
        turn(call("finish", "done")),
    ])
    outputs = configure(h)
    try:
        assert asyncio.run(h.drive()) == "completed"
        rows = receipts(root)
        assert [r["changes_since_comparison"] for r in rows] == [1, 1, 1, 2, 0, 1, 0, 1, 1, 1]
        assert [r["call_id"] for r in rows if r["host_note"]] == ["edit1", "edit2", "edit3", "edit4"]
        assert "since the run started (no comparison yet)" in rows[0]["host_note"]
        assert "Host: 2 model changes" in rows[3]["host_note"]
        assert "since your last compare_to_photos." in rows[5]["host_note"]
        assert "since your last compare_look_to_photos." in rows[7]["host_note"]
        # One request per scripted choice, no forced/blocking extra calls.
        assert len(h.wire.requests) == 10
        for index, row in enumerate(rows[:-1]):
            sent = h.wire.requests[index + 1]["messages"][-1]["content"][0]
            assert sent == row["result"]
            original, _ = prepare_images(outputs[row["call_id"]], files=h.files, metadata={})
            assert sent["content"] == original + (
                [{"type": "text", "text": row["host_note"]}] if row["host_note"] else [])
            assert not sent.get("is_error", False)
        assert rows[1]["scene_change"]["changed"] is False  # new ID, same fingerprint
        assert h.runtime.adapter.last_raw_output == json.dumps(h.scene.finish)
        assert TOOL_PREFERENCE_PARAGRAPH in h.wire.requests[0]["system"]
        assert TOOL_PREFERENCE_PARAGRAPH not in h.spec.source_instructions
        metrics = tool_use.summarize(root)
        assert metrics["total_calls"] == 10
        assert metrics["per_tool_counts"]["execute_blender_code"] == 5
        assert metrics["dedicated_tool_share"] == 0.5
        assert metrics["longest_no_comparison_streak"] == 2
        assert metrics["photo_measurements"] == 1
        assert metrics["photo_measurement_calls"] == 2
        assert metrics["unresolved_measurement_calls"] == 0
        assert metrics["host_notes_sent"] == 4
        # The durable summary uses exactly the same receipt-derived metrics.
        spec = SimpleNamespace(run_dir=root, run_id="test", offline=True)
        summary = R.write_summary(spec, {
            "status": "completed", "seconds": 0, "requests": 10,
            "budget": h.ledger.money_status(),
        }, {"status": "not_delivered"})
        assert summary["tool_use"] == metrics
        h.wire.assert_complete()
    finally:
        h.close()


def test_refused_comparison_and_invalid_calls_do_not_reset_or_get_notes(root):
    h = Harness(root, [
        turn(call("execute_blender_code", "edit1", change=True, refused=False)),
        turn(call("compare_to_photos", "refused", refused=True)),
        turn(call("execute_blender_code", "bad_schema", nonexistent=True)),
        turn(call("execute_blender_code", "no_change", change=False, refused=True)),
        # A failure after a persisted edit still represents a real change.
        turn(call("execute_blender_code", "partial", change=True, refused=True)),
        turn(call("finish", "done")),
    ])
    configure(h)
    try:
        assert asyncio.run(h.drive()) == "completed"
        rows = receipts(root)
        assert [r["changes_since_comparison"] for r in rows] == [1, 1, 1, 1, 2, 2]
        assert [r["call_id"] for r in rows if r["host_note"]] == ["edit1", "partial"]
        assert rows[2]["result"]["is_error"] is True
        assert not any(row["comparison_reset"] for row in rows)
        assert tool_use.summarize(root)["host_notes_sent"] == 2
    finally:
        h.close()


def test_renewal_keeps_scene_change_count(root):
    h = Harness(root, [turn(call("finish", "done"))])
    configure(h)
    try:
        adapter = h.runtime.adapter
        asyncio.run(adapter.invoke(call("execute_blender_code", "first", change=True, refused=False)))
        h.runtime.mark_renewed()
        result = asyncio.run(adapter.invoke(
            call("execute_blender_code", "second", change=True, refused=False)))
        assert result["content"][-1]["text"].startswith("Host: 2 model changes")
    finally:
        h.close()


def test_stop_after_settlement_still_records_note_sent(root):
    h = Harness(root, [
        turn(call("execute_blender_code", "edit", change=True, refused=False)),
        turn(call("finish", "unused")),
    ])
    configure(h)
    original = h.runtime._stream_request

    def stop_after_reply(body):
        response = original(body)
        if len(h.wire.requests) == 2:
            h.shared["cancelled"] = True
        return response

    h.runtime._stream_request = stop_after_reply
    try:
        with pytest.raises(R.Halt, match="Stop requested"):
            asyncio.run(h.drive())
        metrics = tool_use.summarize(root)
        assert metrics["host_notes_sent"] == metrics["host_notes_prepared"] == 1
        assert metrics["total_calls"] == 1
        assert len(h.wire.requests) == 2
    finally:
        h.close()


def test_frozen_effective_instructions_and_unmodified_astra_text(root):
    h = Harness(root, [])
    try:
        source = build_instructions(build_tools(h.scene, RunSettings()))
        effective = claude_instructions(source)
        assert effective.startswith("You are Claude.\n\n" + TOOL_PREFERENCE_PARAGRAPH + "\n\n")
        assert hashlib.sha256(effective.encode()).hexdigest() == (
            "c990a52f24f8ac4f5b9de8b7770011c68639a40ba457b51ebbcf6ec77ed0d83e")
        assert hashlib.sha256(source.encode()).hexdigest() == (
            "18bbbd38aa2728e86b8d800fdcd5eb64cc101d227d68b5350356b8ea606764c7")
        specialist = (Path(__file__).parents[2] / "agent/temple_lab/instructions.txt").read_text(
            encoding="utf-8")
        assert TOOL_PREFERENCE_PARAGRAPH not in claude_instructions(specialist, specialist=True)
        assert build_instructions(build_tools(h.scene, RunSettings())) == source
    finally:
        h.close()


def test_empty_summary_is_explicit(tmp_path):
    metrics = tool_use.summarize(tmp_path)
    assert metrics["total_calls"] == metrics["host_notes_sent"] == 0
    assert metrics["dedicated_tool_share"] == 0.0


def test_paged_measurements_use_verified_full_report(tmp_path):
    from glasses3d.agent.tool_reports import REPORT_SCHEMA, save_report
    document = {"schema": REPORT_SCHEMA, "tool": "photo_read", "result": {
        "status": "partial", "readings": [{"status": "ok"}, {"status": "ok"}, {"status": "refused"}]}}
    reference = save_report(tmp_path, document)
    output = {"status": "partial", "paged": True, "full_report": reference}
    assert tool_use.measurement_count("photo_read", output, tmp_path) == 2
    (tmp_path / reference["path"]).write_text("{}", encoding="utf-8")
    assert tool_use.measurement_count("photo_read", output, tmp_path) is None


def test_surface_measurements_count_hits_not_successful_calls(tmp_path):
    assert tool_use.measurement_count("pick_surface_point", {
        "status": "ok", "hits": [{"hit": True}, {"hit": False}, {"hit": True}]}, tmp_path) == 2
