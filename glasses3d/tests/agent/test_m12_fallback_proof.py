"""Independent guards for the offline proof's capture and exported-change evidence."""
from copy import deepcopy
import json

import numpy as np
import pytest
from PIL import Image

from glasses3d.agent import edits, owner_captures
from glasses3d.tests import glbfab
from glasses3d.tests.proofs import assembly_m12_fallback as proof


def parts():
    rows = glbfab.default_parts()
    rows[proof.PART] = rows.pop("Temple_R")
    return rows


def write_glb(path, rows):
    path.write_bytes(glbfab.build(parts=rows))
    return path


def test_proof_capture_uses_visible_real_anchor_and_partial_footprint(tmp_path):
    model = write_glb(tmp_path / "source.glb", parts())
    before = edits.sha(model)
    folder = tmp_path / "capture"
    capture = proof.make_capture(model, folder)
    mark = capture["marks"][0]
    assert mark["vertices"][0]["anchor"]["node_name"] == proof.PART
    assert 0 < mark["footprint"]["pixels"] < 400
    assert sum(end-start+1 for row in mark["footprint"]["faces"] for start, end in row["triangles"]) < 12
    assert capture["request_text"] == proof.WORDS
    assert owner_captures.validate_capture(capture, folder / "viewer.png", folder / "annotated.png", model) == capture
    with Image.open(folder / "viewer.png") as viewer, Image.open(folder / "annotated.png") as annotated:
        assert viewer.size == annotated.size == (1600, 1000)
        assert np.any(np.array(viewer) != np.array(annotated))
    assert edits.sha(model) == before


@pytest.mark.parametrize("change", ["correct", "none", "second_object", "wrong_amount", "wrong_axis"])
def test_export_audit_requires_exact_predeclared_single_object_shift(tmp_path, change):
    original = parts()
    updated = deepcopy(original)
    if change != "none":
        box, material, role = updated[proof.PART]
        box = list(box)
        axis = (2, 3) if change == "wrong_axis" else (0, 1)
        for i in axis:
            box[i] += proof.SHIFT_MM * (2 if change == "wrong_amount" else 1)
        updated[proof.PART] = (box, material, role)
    if change == "second_object":
        box, material, role = updated["Bridge"]
        box = list(box)
        box[0] += .1
        updated["Bridge"] = (box, material, role)
    before = write_glb(tmp_path / "before.glb", original)
    after = write_glb(tmp_path / "after.glb", updated)
    if change == "correct":
        assert proof.changed_glb_objects(before, after) == [proof.PART]
    else:
        with pytest.raises(AssertionError):
            proof.changed_glb_objects(before, after)


def test_source_hash_audit_detects_added_changed_and_removed_evidence(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    path = source / "input.txt"
    path.write_text("original", encoding="utf-8")
    before = proof.protected(source)
    path.write_text("changed", encoding="utf-8")
    assert proof.protected(source) != before
    path.write_text("original", encoding="utf-8")
    extra = source / "additional.txt"
    extra.write_text("new", encoding="utf-8")
    assert proof.protected(source) != before
    extra.unlink()
    assert proof.protected(source) == before
    path.unlink()
    assert proof.protected(source) != before


@pytest.mark.parametrize("paged", [True, False])
def test_actual_tool_receipt_reader_supports_full_and_short_results(tmp_path, paged):
    result = {"status": "ok", "checkpoint": "c0001"}
    path = tmp_path / ("work/tool_reports/result.json" if paged else "runs/f/tools/0001-execute_blender_code/result.json")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"call_id": "actual-call", "result": result if paged else [
        {"type": "text", "text": json.dumps(result)}, {"type": "text", "text": "Money footer"}]}), encoding="utf-8")
    assert proof.tool_result(tmp_path, "actual-call") == result
    with pytest.raises(AssertionError, match="Missing real tool result"):
        proof.tool_result(tmp_path, "missing-call")
