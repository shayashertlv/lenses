"""Exact GLB scalar parity through the actual Blender authoring/export path."""
import json
from pathlib import Path

import pytest

from glasses3d.agent import owner_lens_edit
from glasses3d.g3d import safe_mode
from glasses3d.g3d_host import blender


@pytest.fixture(scope="module")
def proof(tmp_path_factory):
    if blender.blender_exe() is None:
        pytest.skip("Blender unavailable")
    directory = tmp_path_factory.mktemp("task23-lens-parity")
    result = blender.run(None, Path(__file__).parent / "blender/task23_lens_candidate.py", [directory], timeout_s=180)
    assert (directory / "report.json").is_file(), result.stdout + result.stderr
    return directory, json.loads((directory / "report.json").read_text())


def test_actual_apply_export_values_equal_candidate_json_exactly(proof):
    directory, report = proof
    original = directory / "before.glb"
    original_values = owner_lens_edit.read_lens_materials(original)["materials"]
    assert len(original_values) == 1
    for case in report["cases"]:
        candidate, receipt = owner_lens_edit.patch_lens_glb(original,
            {original_values[0]["material_index"]: case["values"]})
        expected = owner_lens_edit.read_lens_materials(candidate)["materials"][0]["values"]
        actual = owner_lens_edit.read_lens_materials(case["path"])["materials"][0]["values"]
        assert actual == expected == case["values"]  # Exact, not approx or rounded.
        assert case["receipt"]["scene_unchanged"]
        assert receipt["binary_chunks_unchanged"]


def test_later_socket_edit_cannot_reapply_old_metadata(proof):
    directory, report = proof
    assert report["stale_receipt"]["lens_candidate_values"] == []
    assert owner_lens_edit.read_lens_materials(directory / "stale.glb")["materials"][0]["values"]["roughness"] == pytest.approx(.333)


def test_linked_controls_refuse_before_mutation_and_helper_is_allowed(proof):
    assert proof[1]["linked_unchanged"]
    assert "unlinked" in proof[1]["linked_refusal"]
    assert safe_mode.validate("values = {}\ng3d.apply_lens_candidate('Lens', **values)") == []


def test_open_lens_opacity_transitions_match_source_and_closed_gate_still_applies(proof):
    for row in proof[1]["transitions"]:
        assert row["source_audit"][0]["closed_outward"] == (not row["open"])
        if not row["open"] and row["source_transmission"] == 0:
            # Candidate tool refuses this transition; helper cannot bypass the existing export correction.
            assert row["source_double_sided"] is True and row["after_double_sided"] is False
        else:
            assert row["after_double_sided"] == row["source_double_sided"]
