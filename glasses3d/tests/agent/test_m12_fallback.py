"""Full-item intake is immutable, single-item, and never implicitly approved."""
from types import SimpleNamespace

import pytest

from glasses3d.agent import edits, owner_captures, owner_fallback as F, procs
from .test_owner_edit_lifecycle_review import selected_source
from .test_owner_captures import capture_fixture
from .test_m12_mark_targets import source_fixture


@pytest.fixture
def fallback_source(selected_source, tmp_path):
    f = selected_source
    snapshot, model = source_fixture(tmp_path)
    snapshot["schema"] = "g3d.mesh_snapshot/1"
    snapshot["fingerprint"] = f.chosen["fingerprint"]
    f.saved.update(model=str(model), glb_sha256=edits.sha(model))
    f.saved["report_data"]["glb_sha256"] = edits.sha(model)
    records = []
    for number, label in ((1, "A"), (2, "B")):
        cap = capture_fixture(tmp_path / f"draft/C{number}", model, capture_id=f"C{number}",
                              label=label, triangle=0)
        records.append({"item_id": f"I{number}", "request_text": f"  Change {label}.\r\n  ",
                        "capture_dir": cap.folder})
    root = owner_captures.create_capture_understand(f.source, "selected-delivery", records, f.destination)
    doc = edits.load(root)
    snapshot_name = "work/model_maps/map/snapshot.json"
    procs.write_json_atomic(root / snapshot_name, snapshot)
    understanding = {"schema": edits.UNDERSTANDING_SCHEMA, "status": "diagnostic_only", "call_id": "shown-u",
        "source_fingerprint": doc["source"]["fingerprint"], "request_text": doc["request_text"],
        "items": [{"item_id": row["item_id"], "number": n, "label": str(n),
                   "interpretation": "Scripted reading, not Astra", "disposition": "cannot_preview",
                   "reason": "Scripted unsupported preview", "operation_json": "[]"}
                  for n, row in enumerate(records, 1)], "operations": [], "scripted": True,
        "snapshot_path": snapshot_name, "evidence_files": {snapshot_name: edits.sha(root / snapshot_name)}}
    artifact = root / F.UNDERSTANDING_PATH
    procs.write_json_atomic(artifact, understanding)
    procs.write_json_atomic(artifact.with_name("understanding-delivery.json"), {
        "schema": "g3d.owner_understanding_delivery/1", "sha256": edits.sha(artifact),
        "call_id": "shown-u", "delivered": True})
    return SimpleNamespace(original=f, root=root, snapshot=snapshot, artifact=artifact, records=records,
                           destination=tmp_path / "fallback")


def rewrite_understanding(f, change, *, reseal=True):
    doc = edits.read(f.artifact)
    change(doc)
    procs.write_json_atomic(f.artifact, doc)
    if reseal:
        path = f.artifact.with_name("understanding-delivery.json")
        shown = edits.read(path)
        shown["sha256"] = edits.sha(f.artifact)
        procs.write_json_atomic(path, shown)


def create(f):
    return F.create_full_edit_item(f.root, "I1", f.destination)


def test_single_item_copy_preserves_photos_words_reading_seed_and_source(fallback_source):
    f = fallback_source
    original_hashes = edits.tree_hashes(f.original.source)
    round_hashes = edits.tree_hashes(f.root)
    root = create(f)
    doc = F.load(root)
    assert root == f.destination
    assert doc["mode"] == doc["origin"] == "full_edit_item"
    assert doc["item_id"] == "I1"
    assert doc["owner_words"] == f.records[0]["request_text"]
    assert doc["item_interpretation"] == edits.read(f.artifact)["items"][0]
    assert doc["understanding_sha256"] == edits.sha(f.artifact)
    assert (root / "inputs/seed.blend").read_bytes() == b"chosen delivered checkpoint"
    assert edits.tree_hashes(root / "images") == edits.tree_hashes(f.original.source / "images")
    assert edits.tree_hashes(root / "cameras") == edits.tree_hashes(f.root / "cameras")
    assert edits.tree_hashes(f.original.source) == original_hashes
    assert edits.tree_hashes(f.root) == round_hashes
    assert not (root / "runs").exists() and not (root / "budget").exists()
    assert not (root / "inputs/owner-edit.json").exists()
    assert not (root / "inputs/owner_captures/C2").exists()
    assert not (root / "inputs/edit-capabilities.json").exists()
    assert edits.load(root) is None
    assert len(owner_captures.load_round_captures(root, F.capture_context(doc))) == 1
    assert doc["owner_mark_resolutions"][0]["status"] == "resolved"
    assert doc["owner_mark_resolutions"][0]["edit_authority"] is False
    assert f.records[0]["request_text"] in F.brief_suffix(doc)
    assert "Change B" not in F.brief_suffix(doc)


@pytest.mark.parametrize("disposition", ["previewed", "executable", "invented", None])
def test_previewed_or_invalid_item_refuses(fallback_source, disposition):
    f = fallback_source
    rewrite_understanding(f, lambda d: d["items"][0].update(disposition=disposition))
    with pytest.raises(ValueError, match="previewed|disposition"):
        create(f)
    assert not f.destination.exists()


@pytest.mark.parametrize("disposition", ["cannot_preview", "unsupported", "needs_clarification"])
def test_each_recorded_unpreviewable_disposition_is_eligible(fallback_source, disposition):
    f = fallback_source
    rewrite_understanding(f, lambda d: d["items"][0].update(disposition=disposition))
    assert F.load(create(f))["item_interpretation"]["disposition"] == disposition


@pytest.mark.parametrize("item", ["I3", "../I1", "I01", "", None])
def test_wrong_item_refuses(fallback_source, item):
    f = fallback_source
    with pytest.raises(ValueError, match="original capture item"):
        F.create_full_edit_item(f.root, item, f.destination)
    assert not f.destination.exists()


@pytest.mark.parametrize("change,match", [
    (lambda d: d.update(source_fingerprint="0"*64), "different source"),
    (lambda d: d.update(request_text="different request"), "different source"),
    (lambda d: d["items"].reverse(), "identity or order"),
    (lambda d: d["items"][0].update(interpretation=""), "reading"),
    (lambda d: d["items"][0].update(reason=""), "reason"),
    (lambda d: d.update(snapshot_path="missing"), "bound source snapshot"),
    (lambda d: d.update(schema="invalid"), "delivered understanding"),
    (lambda d: d.update(status="incomplete"), "delivered understanding"),
])
def test_wrong_understanding_refuses(fallback_source, change, match):
    f = fallback_source
    rewrite_understanding(f, change)
    with pytest.raises(ValueError, match=match):
        create(f)
    assert not f.destination.exists()


@pytest.mark.parametrize("damage", ["undelivered", "unseen", "changed", "missing", "snapshot"])
def test_understanding_must_be_shown_unchanged_and_bound(fallback_source, damage):
    f = fallback_source
    shown = f.artifact.with_name("understanding-delivery.json")
    if damage == "undelivered":
        procs.write_json_atomic(shown, {**edits.read(shown), "delivered": False})
    elif damage == "unseen":
        procs.write_json_atomic(f.root / "work/job_scene/state.json", {"unseen": ["shown-u"]})
    elif damage == "changed":
        rewrite_understanding(f, lambda d: d["items"][0].update(interpretation="different"), reseal=False)
    elif damage == "missing":
        shown.unlink()
    else:
        path = f.root / edits.read(f.artifact)["snapshot_path"]
        path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="understanding"):
        create(f)
    assert not f.destination.exists()


@pytest.mark.parametrize("damage", ["export", "fingerprint", "checkpoint", "seed", "capture"])
def test_source_changes_refuse_before_copy(fallback_source, damage):
    f = fallback_source
    if damage == "export":
        f.original.saved["glb_sha256"] = "0"*64
    elif damage == "fingerprint":
        f.original.saved["report_data"]["fingerprint"] = "0"*64
    else:
        path = (f.original.source / "checkpoints/c0001.blend" if damage == "checkpoint" else
                f.root / "inputs/seed.blend" if damage == "seed" else
                f.root / "inputs/owner_captures/C1/annotated.png")
        path.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="changed"):
        create(f)
    assert not f.destination.exists()


@pytest.mark.parametrize("path", ["inputs/seed.blend", "inputs/fallback-understanding.json",
    "inputs/fallback-understanding-delivery.json", F.BASELINE_PATH, "inputs/owner_captures/C1/capture.json",
    "inputs/owner_captures/C1/viewer.png", "inputs/owner_captures/C1/annotated.png",
    "inputs/owner_captures/items.json", "images/front.png", "cameras/basis-001/cameras.json"])
def test_every_copied_input_is_reverified(fallback_source, path):
    root = create(fallback_source)
    target = root / path
    target.write_bytes(target.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="input changed"):
        F.load(root)


def test_resealed_understanding_after_copy_still_refuses(fallback_source):
    f = fallback_source
    root = create(f)
    rewrite_understanding(f, lambda d: d["items"][0].update(interpretation="another reading"))
    with pytest.raises(ValueError, match="understanding or selected item changed"):
        F.load(root)


@pytest.mark.parametrize("change,match", [
    (lambda d: d.update(item_id="I2"), "capture or owner's words"),
    (lambda d: d.update(understanding_sha256="0"*64), "understanding or selected item"),
    (lambda d: d.update(baseline_snapshot="../outside.json"), "snapshot path"),
    (lambda d: d.update(owner_mark_resolutions=[]), "mark resolutions"),
    (lambda d: d.update(owner_words="change everything"), "capture or owner's words"),
])
def test_descriptor_cannot_substitute_item_reading_or_marks(fallback_source, change, match):
    root = create(fallback_source)
    path = root / F.RELATIVE_PATH
    doc = edits.read(path)
    change(doc)
    procs.write_json_atomic(path, doc)
    with pytest.raises(ValueError, match=match):
        F.load(root)


def test_cannot_create_inside_original_or_understanding_or_existing_folder(fallback_source):
    f = fallback_source
    for target in (f.original.source / "new", f.root / "new", f.root):
        with pytest.raises(ValueError, match="new directory outside"):
            F.create_full_edit_item(f.root, "I1", target)


def test_regular_job_has_no_fallback_and_text_cannot_claim_one(fallback_source):
    assert F.load(fallback_source.original.source) is None
    with pytest.raises(ValueError, match="capture UNDERSTAND"):
        F.create_full_edit_item(fallback_source.original.source, "I1")
    assert "complete\nnormal modeling toolset" in F.instructions_suffix()
    assert "not a product\nphoto" in F.instructions_suffix()
    assert "short report" in F.instructions_suffix()


def test_malformed_source_snapshot_refuses_plainly(fallback_source):
    f = fallback_source
    path = f.root / edits.read(f.artifact)["snapshot_path"]
    procs.write_json_atomic(path, {"fingerprint": f.snapshot["fingerprint"]})
    rewrite_understanding(f, lambda d: d["evidence_files"].update({d["snapshot_path"]: edits.sha(path)}))
    with pytest.raises(ValueError, match="unsupported format"):
        create(f)
