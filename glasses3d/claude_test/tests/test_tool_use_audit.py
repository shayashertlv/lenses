from __future__ import annotations

import hashlib
import json
from pathlib import Path

from PIL import Image
import pytest

from glasses3d.claude_test.tool_use_audit import (
    _verify_image_group, audit_claude_adapter, audit_run, main,
)


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def add_call(root, number, name, *, before="a", after="a", status="ok"):
    cid = f"call-{number}"
    folder = root / "tools" / f"{number:04}-{name}"
    write_json(folder / "arguments.json",
               {"n": number, "tool": name, "call_id": cid, "arguments": {}})
    write_json(folder / "result.json", {
        "tool": name, "call_id": cid,
        "result": [{"type": "text", "text": json.dumps({"status": status})}]})
    write_json(folder / "journal.json", {
        "tool": name, "call_id": cid, "fingerprint_before": before,
        "fingerprint_after": after, "status": status, "mutating": before != after})
    return folder


def test_counts_and_streaks_come_from_actual_fingerprints(tmp_path):
    add_call(tmp_path, 1, "execute_blender_code", before="a", after="b")
    add_call(tmp_path, 2, "read_image")
    add_call(tmp_path, 3, "execute_blender_code", before="b", after="b", status="refused")
    add_call(tmp_path, 4, "execute_blender_code", before="b", after="c")
    add_call(tmp_path, 5, "compare_to_photos", status="refused")
    add_call(tmp_path, 6, "execute_blender_code", before="c", after="d")
    add_call(tmp_path, 7, "compare_look_to_photos")
    add_call(tmp_path, 8, "execute_blender_code", before="d", after="e")
    add_call(tmp_path, 9, "photo_read")
    add_call(tmp_path, 10, "pick_surface_point")
    add_call(tmp_path, 11, "finish", status="accepted")
    result = audit_run(tmp_path)
    assert result["total_calls"] == 11
    assert result["per_tool_counts"]["execute_blender_code"] == 5
    assert result["scene_changing_scripts"] == 4
    assert result["nonchanging_scripts"] == 1
    assert result["dedicated_tool_share"] == 6 / 11
    assert result["longest_no_comparison_streak"] == 3
    assert result["longest_streak_script_calls"] == [1, 4, 6]
    assert result["photo_measurements"] == 2
    assert result["comparison_segments"] == [
        {"script_calls": [1, 4, 6], "comparison_call": 7},
        {"script_calls": [8], "comparison_call": None}]


def test_missing_mutation_evidence_is_unknown_not_read_only(tmp_path):
    folder = add_call(tmp_path, 1, "execute_blender_code", before="a", after="b")
    (folder / "journal.json").unlink()
    result = audit_run(tmp_path)
    assert result["mutation_unknown_script_calls"] == [1]
    assert result["longest_no_comparison_streak"] is None
    assert result["nonchanging_scripts"] == 0


def test_missing_invocation_receipt_cannot_silently_undercount(tmp_path):
    folder = add_call(tmp_path, 1, "scene_summary")
    (folder / "arguments.json").unlink()
    with pytest.raises(ValueError, match="lacks invocation receipt"):
        audit_run(tmp_path)


def test_start_events_independently_check_complete_receipt_census(tmp_path):
    add_call(tmp_path, 1, "scene_summary")
    row = {"event": "tool_started", "tool_call_id": "call-1", "name": "scene_summary"}
    path = tmp_path / "events.jsonl"
    path.write_text(json.dumps(row), encoding="utf-8")
    assert audit_run(tmp_path)["tool_started_events_match_receipts"] is True
    path.write_text(json.dumps({**row, "tool_call_id": "missing"}), encoding="utf-8")
    assert audit_run(tmp_path)["tool_started_events_match_receipts"] is False


@pytest.mark.parametrize("mismatch", ["call_id", "tool"])
def test_mismatched_receipt_identity_refuses(tmp_path, mismatch):
    folder = add_call(tmp_path, 1, "scene_summary")
    path = folder / "result.json"
    value = json.loads(path.read_text())
    value[mismatch] = "different"
    write_json(path, value)
    with pytest.raises(ValueError, match="identity mismatch"):
        audit_run(tmp_path)


def image_records(tmp_path):
    source = Image.new("RGBA", (7, 3))
    source.putdata([(x * 20, y * 30, 0, 255) for y in range(3) for x in range(7)])
    path = tmp_path / "source.png"
    source.save(path)
    records = []
    for index, box in enumerate(((0, 0, 4, 3), (3, 0, 7, 3))):
        piece_path = tmp_path / f"tile-{index}.png"
        source.crop(box).save(piece_path)
        records.append({
            "call_id": "call-1", "output_index": 1,
            "source_path": str(path), "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "source_bytes": path.stat().st_size, "source_size_px": [7, 3],
            "tile_path": str(piece_path),
            "tile_sha256": hashlib.sha256(piece_path.read_bytes()).hexdigest(),
            "file_id": f"file-{index}", "w": 4, "h": 3,
            "x0": box[0], "y0": box[1], "x1": box[2], "y1": box[3]})
    return records


def test_tiles_preserve_native_pixels_and_complete_coverage(tmp_path):
    assert _verify_image_group(image_records(tmp_path)) == []


@pytest.mark.parametrize("defect, expected", [
    ("missing", "missing tile"),
    ("hash", "tile SHA-256 mismatch"),
    ("pixels", "tile pixels differ"),
    ("coverage", "not covered"),
    ("size", "tile dimensions mismatch"),
])
def test_image_audit_detects_missing_corrupt_resampled_or_uncovered_tiles(tmp_path, defect, expected):
    records = image_records(tmp_path)
    if defect == "missing":
        Path(records[0]["tile_path"]).unlink()
    elif defect == "hash":
        records[0]["tile_sha256"] = "bad"
    elif defect == "pixels":
        path = Path(records[0]["tile_path"])
        Image.new("RGBA", (4, 3), "red").save(path)
        records[0]["tile_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    elif defect == "coverage":
        records.pop()
    elif defect == "size":
        records[0]["w"] = 5
    assert any(expected in error for error in _verify_image_group(records))


def claude_receipts(tmp_path):
    first = add_call(tmp_path, 1, "render_views")
    add_call(tmp_path, 2, "finish", status="accepted")
    records = image_records(tmp_path)
    value = json.loads((first / "result.json").read_text())
    value["result"].append({
        "type": "image",
        "image_url": {"sha256": records[0]["source_sha256"], "bytes": records[0]["source_bytes"]}})
    write_json(first / "result.json", value)
    (tmp_path / "image-manifest.jsonl").write_text(
        "\n".join(json.dumps(row) for row in records), encoding="utf-8")
    definitions = [
        {"name": name, "input_schema": {"type": "object", "additionalProperties": False}}
        for name in ("render_views", "finish")]
    write_json(tmp_path / "requests/0001-request.json", {"tools": definitions, "messages": []})
    result = {"type": "tool_result", "tool_use_id": "call-1", "content": [
        *value["result"][:1],
        *({"type": "image", "source": {"type": "file", "file_id": row["file_id"]}}
          for row in records)]}
    write_json(tmp_path / "requests/0002-request.json", {
        "tools": definitions, "messages": [{"role": "user", "content": [result]}]})
    for number, name in ((1, "render_views"), (2, "finish")):
        write_json(tmp_path / f"requests/{number:04}-response.json", {
            "content": [{"type": "tool_use", "id": f"call-{number}", "name": name, "input": {}}]})
    return tmp_path


def test_adapter_audit_matches_wire_and_all_native_images(tmp_path):
    result = audit_claude_adapter(claude_receipts(tmp_path))
    assert result["wire_calls_match_tool_receipts"] is True
    assert result["schema_validation_failures"] == []
    assert result["image_or_text_integrity_errors"] == []
    assert result["retained_raw_images_verified"] == 1
    assert result["adapter_tile_records_verified"] == 2
    assert result["forwarded_tool_image_occurrences"] == 2
    assert result["terminal_accepted_results_not_forwarded"] == ["call-2"]
    assert result["missing_forwarded_results"] == []


def test_adapter_audit_reports_schema_error_and_missing_forwarded_image(tmp_path):
    claude_receipts(tmp_path)
    response_path = tmp_path / "requests/0001-response.json"
    response = json.loads(response_path.read_text())
    response["content"][0]["input"] = {"invalid": True}
    write_json(response_path, response)
    request_path = tmp_path / "requests/0002-request.json"
    request = json.loads(request_path.read_text())
    request["messages"][0]["content"][0]["content"].pop()
    request["messages"][0]["content"][0]["is_error"] = True
    write_json(request_path, request)
    result = audit_claude_adapter(tmp_path)
    assert len(result["schema_validation_failures"]) == 1
    assert result["is_error_results"] == [{"call_id": "call-1", "tool": "render_views"}]
    assert any("image IDs differ" in row["problem"]
               for row in result["image_or_text_integrity_errors"])


def test_cli_writes_machine_json_and_factual_table(tmp_path):
    source = claude_receipts(tmp_path / "source")
    destination = tmp_path / "output"
    assert main(["--run", f"fixture={source}", "--claude-label", "fixture",
                 "--output", str(destination)]) == 0
    report = json.loads((destination / "audit.json").read_text())
    assert report["runs"]["fixture"]["total_calls"] == 2
    text = (destination / "audit.md").read_text()
    assert "| `render_views` | 1 |" in text
    assert "full pixel coverage" in text
