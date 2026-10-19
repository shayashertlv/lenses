"""A finished fallback can start another capture UNDERSTAND without inheriting its mode."""
from __future__ import annotations

import copy
from pathlib import Path

from glasses3d.agent import delivery, edits, job_run, owner_captures, owner_fallback, procs
from glasses3d.agent.delivery import load_delivery as load_published_delivery
from glasses3d.agent.journal import CheckpointIndex
from .test_delivery import make_finished
from .test_m12_fallback import fallback_source
from .test_owner_captures import capture_fixture
from .test_owner_edit_lifecycle_review import selected_source


def test_finished_fallback_starts_clean_capture_understand_without_changing_source(
        fallback_source, tmp_path, monkeypatch):
    f = fallback_source
    root = owner_fallback.create_full_edit_item(f.root, "I1", f.destination)
    original_loader = delivery.load_delivery
    # Keep the source fixture's original delivery stub; publish/reopen the fallback through real delivery code.
    def load(root, delivery_id=None):
        if Path(root).resolve() == f.original.source:
            return original_loader(root, delivery_id)
        return load_published_delivery(root, delivery_id)
    monkeypatch.setattr(delivery, "load_delivery", load)
    scene, result = make_finished(root, run_id="scripted-fallback")
    checkpoint = root / "checkpoints/c0001.blend"
    checkpoint.parent.mkdir()
    checkpoint.write_bytes(b"scripted finished fallback checkpoint; no Blender needed")
    CheckpointIndex(checkpoint.parent).append({"id": "c0001", "kind": "save", "file": checkpoint.name,
        "sha256": edits.sha(checkpoint), "fingerprint": scene.finish["fingerprint"]})
    result["exit_checkpoint"]["sha256"] = edits.sha(checkpoint)
    result.update(mode="full_edit_item", item_id="I1")
    final_snapshot = copy.deepcopy(f.snapshot)
    final_snapshot["fingerprint"] = scene.finish["fingerprint"]
    monkeypatch.setattr(delivery, "read_checkpoint_snapshot", lambda *args: copy.deepcopy(final_snapshot))
    published = delivery.deliver_finished(root, scene, result)
    saved = delivery.load_delivery(root, published["delivery_id"])
    assert saved["origin"] == "full_edit_item" and saved["item_id"] == "I1"
    assert saved["report_data"]["finished"] is saved["report_data"]["accepted_finish"] is True
    assert saved["report_data"]["run_status"] == "completed"

    controls = ("full-edit-item.json", "fallback-understanding.json",
                "fallback-understanding-delivery.json", "fallback-source-snapshot.json")
    assert all((root / "inputs" / name).is_file() for name in controls)
    protected = {path: edits.tree_hashes(path) for path in (f.original.source, f.root, root)}
    capture = capture_fixture(tmp_path / "next-capture", Path(saved["model"]), capture_id="C3", label="C", triangle=0)
    # A fresh background mark is valid evidence, without inventing model targets for this control-path test.
    capture.payload["marks"][0]["vertices"] = [{"xy_px": [5, 5], "anchor": {"kind": "background"}}]
    capture.payload["marks"][0]["footprint"] = {"pixels": 0, "faces": []}
    procs.write_json_atomic(capture.folder / "capture.json", capture.payload)
    words = "  C: please inspect this area again.\r\nKeep these words.  "
    following = owner_captures.create_capture_understand(root, saved["delivery_id"],
        [{"item_id": "I1", "request_text": words, "capture_dir": capture.folder}], tmp_path / "next-edit")

    assert all(not (following / "inputs" / name).exists() for name in controls)
    assert owner_fallback.load(following) is None
    doc = edits.load(following)
    assert doc["phase"] == "understand"
    assert doc["source"]["job"] == str(root) and doc["source"]["delivery_id"] == saved["delivery_id"]
    assert doc["source"]["fingerprint"] == saved["report_data"]["fingerprint"]
    assert (following / "inputs/seed.blend").read_bytes() == checkpoint.read_bytes()
    captures = owner_captures.load_round_captures(following)
    assert [(row["capture_id"], row["labels"], row["request_text"]) for row in captures] == [("C3", ["C"], words)]
    assert not (following / "inputs/owner_captures/C1").exists()
    spec, fresh_scene = job_run.prepare_run(following, cap="8", settings=job_run.settings_full_edit_item("8"))
    assert spec.settings.finish_tool == "understand_finish"
    assert "understand_finish" in {tool.name for tool in spec.tools}
    assert fresh_scene.owner_edit["phase"] == "understand"
    assert not getattr(fresh_scene, "full_edit_item", None)
    assert "Full edit of selected owner item" not in spec.brief
    assert not (following / "runs").exists()
    for path, before in protected.items():
        assert edits.tree_hashes(path) == before
    assert owner_fallback.load(root)["item_id"] == "I1"
