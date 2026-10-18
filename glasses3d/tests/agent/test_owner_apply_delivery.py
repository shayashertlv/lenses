"""Task 5 regressions: reusable shown choices, portable print evidence and reviewable local delivery."""
from pathlib import Path
from types import SimpleNamespace
import copy
import shutil

import pytest

from glasses3d.agent import cli, delivery, edits, owner_apply, print_freshness, review, verdicts
from .test_owner_preview_selection import shown_choices
from .test_owner_edit_lifecycle_review import selected_source, _write
from .test_delivery import make_finished
from .test_print_freshness import bake, mesh


@pytest.mark.parametrize("failure", ["stale", "worker", "apply"])
def test_failed_apply_does_not_consume_paid_understanding_and_another_choice_retries(shown_choices, monkeypatch, failure):
    h = shown_choices
    before = (h.edit_root / "edit.json").read_bytes()
    source = edits.tree_hashes(h.first)
    def fail(*args):
        raise ValueError(failure)
    monkeypatch.setattr(owner_apply.edit_capabilities, "require_round", fail if failure == "stale" else lambda *a: None)
    monkeypatch.setattr(owner_apply, "_choice", lambda *a: ({}, []))
    session = SimpleNamespace(head={"fingerprint": h.artifact["source_fingerprint"]},
                              fingerprint=lambda: h.artifact["source_fingerprint"])
    scene = SimpleNamespace(s=session, start=fail if failure == "worker" else lambda: None, close=lambda: None)
    monkeypatch.setattr(owner_apply, "JobScene", lambda root: scene)
    monkeypatch.setattr(owner_apply.owner_edits, "apply", fail)
    for option in ("A", "B"):
        with pytest.raises(ValueError, match=failure):
            owner_apply.apply_preview(h.edit_root, "Apply the shown option.", option)
        assert (h.edit_root / "edit.json").read_bytes() == before
        assert edits.tree_hashes(h.first) == source


def test_two_completed_copy_rounds_can_select_same_unchanged_understanding(shown_choices):
    h = shown_choices
    source = edits.tree_hashes(h.first)
    first = edits.create_edit(h.edit_root, "A.", option_id="A")
    second = edits.create_edit(h.edit_root, "B.", option_id="B")
    assert first != second
    assert edits.load(first)["selected_preview_option"] == "A"
    assert edits.load(second)["selected_preview_option"] == "B"
    assert edits.tree_hashes(h.first) == source
    assert edits.read(h.edit_root / "edit.json")["rounds"] == [h.first.name, first.name, second.name]


@pytest.mark.parametrize("damage", ["unconfirmed_amount", "changed_axis", "unknown_id", "duplicate"])
def test_historical_replay_cannot_grant_new_operation_or_unconfirmed_amount(damage):
    from glasses3d.agent import owner_edits
    from glasses3d.g3d.owner_mesh import OwnerMeshError
    operation = {"id": "turn", "kind": "rotate_region", "scope": {"object_id": "same-target"},
        "params": {"axis_points_mm": [[0, 0, 0], [0, 1, 0]], "delta_deg": 2.}}
    saved = copy.deepcopy(operation)
    if damage == "unconfirmed_amount": saved["params"]["delta_deg"] = 5.
    if damage == "changed_axis": saved["params"]["axis_points_mm"][0][0] = 1.
    if damage == "unknown_id": saved["id"] = "unconfirmed"
    sealed = {"source_fingerprint": "source", "all_operations": [operation], "correction": False,
        "receipts": [{"fingerprint": "saved-head", "operations": [saved] * (2 if damage == "duplicate" else 1)}]}
    with pytest.raises(OwnerMeshError, match="historical receipt"):
        owner_edits._recorded_operations(sealed, "saved-head")
    assert owner_edits._recorded_operations({**sealed, "selected_preview": {"id": "B"}}, "saved-head") is None


def test_retained_print_evidence_survives_two_copy_generations_and_missing_original(tmp_path):
    source, first, second = (tmp_path / name for name in ("source", "round", "specialist"))
    source.mkdir()
    baked = bake(source, "bake", ["A", "B"])
    printed = mesh(source / "printed.glb", paints={"A": baked, "B": baked}, image_reencode=True)
    assert print_freshness.check_current_print(printed, source)["ok"]
    source_bytes = edits.tree_hashes(source)
    print_freshness.carry_evidence(source, first)
    print_freshness.carry_evidence(first, second)
    shutil.copyfile(printed, second / "printed.glb")
    assert edits.tree_hashes(source) == source_bytes
    source.rename(tmp_path / "source-unavailable")
    first.rename(tmp_path / "round-unavailable")
    check = print_freshness.check_current_print(second / "printed.glb", second)
    assert check["ok"] and check["status"] == "verified", check
    manifest = next((second / "work/print").glob("*/print_manifest.json"))
    retained = edits.read(manifest)
    assert retained == baked["manifest"]  # historical manifest bytes/identity are unchanged
    (manifest.parent / "print.png").write_bytes(b"changed")
    assert not print_freshness.check_current_print(second / "printed.glb", second)["ok"]


def test_print_evidence_is_carried_by_real_understanding_and_specialist_copy(selected_source):
    from glasses3d.agent.temple_lab import owner_stage
    f = selected_source
    baked = bake(f.source, "bake", ["A", "B"])
    printed = mesh(f.source / "printed.glb", paints={"A": baked, "B": baked})
    root = edits.create_understand(f.source, "Adjust the material.", destination=f.destination)
    doc = edits.load(root)
    assert any(name.startswith("work/print/") for name in doc["immutable_files"])
    assert print_freshness.check_current_print(printed, root)["ok"]
    checkpoint = root / "checkpoints/c0001.blend"
    checkpoint.parent.mkdir()
    shutil.copyfile(f.source / "checkpoints/c0001.blend", checkpoint)
    scene = SimpleNamespace(root=root, owner_edit=doc, owner_edit_authority=SimpleNamespace(receipts=lambda: []),
        s=SimpleNamespace(head=f.chosen, fingerprint=lambda: f.chosen["fingerprint"]))
    child = root / "work/os/short"
    owner_stage.prepare(scene, child)
    assert print_freshness.check_current_print(printed, child)["ok"]
    assert edits.load(child)["immutable_files"] == doc["immutable_files"]


def application(root, scene):
    export = scene.finish["export"]
    image = Path(scene.last_ar["report"]).parent / "front.png"
    return {"schema": "g3d.owner_applied_preview/1", "status": "applied", "tool_test": True,
        "visual_acceptance": False, "api_requests": 0, "settled_usd": "0.000000",
        "exact_preview_fingerprint": True, "export_matches_preview_bytes": True,
        "preview_export_sha256": export["sha256"],
        "option": {"id": "B", "fingerprint": export["fingerprint"]}, "export": export,
        "images": [{"path": str(image), "sha256": edits.sha(image)}],
        "source_basis": {"basis": "basis-001", "basis_sha256": "e" * 64}}


def test_applied_choice_is_immutable_reviewable_delivery_with_tool_test_verdict(tmp_path):
    scene, result = make_finished(tmp_path)
    local = application(tmp_path, scene)
    saved = delivery.deliver_owner_applied(tmp_path, scene.finish["export"],
                                          result["exit_checkpoint"], scene.last_ar, local)
    loaded = delivery.load_delivery(tmp_path)
    assert saved["delivery_id"] == loaded["delivery_id"]
    report = loaded["report_data"]
    assert report["finished"] and not report["accepted_finish"]
    assert report["delivery_kind"] == "owner_applied" and report["run_status"] == "owner_applied"
    assert report["tool_test"]
    assert report["print_check"]["ok"] and report["validate_spec"]["ok"]
    assert report["seating"]["ok"] and report["arview"]["status"] == "ok"
    context = review._delivery_context(loaded)
    assert context["tool_test"] and not context["accepted_finish"]
    status = cli.delivery_status(tmp_path)["delivery"]
    assert status["tool_test"] and status["delivery_kind"] == "owner_applied" and not status["accepted_finish"]
    row = verdicts.append_verdict(tmp_path, "changes", "Testing the optional effect.", loaded["id"])
    assert row["scope"] == "tool_test"
    assert verdicts.read_verdicts(tmp_path) == [row]
    assert verdicts.pending_notes(tmp_path) == []
    assert verdicts.resume_checkpoint(tmp_path, []) is None
    assert Path(loaded["model"]).read_bytes() == Path(scene.finish["export"]["path"]).read_bytes()


@pytest.mark.parametrize("failure", ["print", "ar", "checkpoint", "preview_bytes"])
def test_local_delivery_preserves_existing_hard_and_identity_checks(tmp_path, failure):
    scene, result = make_finished(tmp_path)
    local = application(tmp_path, scene)
    if failure == "print":
        scene.finish["export"]["print_check"]["ok"] = False
    elif failure == "ar":
        scene.last_ar["export_sha256"] = "0" * 64
    elif failure == "checkpoint":
        result["exit_checkpoint"]["verified"] = False
    else:
        local["export_matches_preview_bytes"] = False
    with pytest.raises(delivery.DeliveryError):
        delivery.deliver_owner_applied(tmp_path, scene.finish["export"], result["exit_checkpoint"], scene.last_ar, local)
    assert not (tmp_path / "deliveries/current.json").exists()


def test_default_runtime_depth_independent_of_longest_job_id_and_edit_generation(selected_source, tmp_path, monkeypatch):
    f = selected_source
    long_source = f.source.with_name("x" * 60 + "-" + "a" * 8)
    f.source.rename(long_source)
    # The selected checkpoint and frozen basis are independently bound by the fixture.
    f.basis.dir = long_source / "cameras/basis-001"
    runtime = tmp_path / "oe"
    monkeypatch.setattr(edits, "RUNTIME_ROOT", runtime, raising=False)
    first = edits.create_understand(long_source, "Move the shown face.")
    edit_id = edits.load(first)["edit_id"]
    assert edits.resolve_round(long_source, edit_id) == first
    assert first.parent.parent == runtime
    # Generation 100 still selects a new fixed-depth runtime, not its parent's tree.
    for generation in range(2, 101):
        previous = first
        monkeypatch.setattr(edits, "CheckpointIndex", lambda path: SimpleNamespace(get=lambda cid: f.chosen))
        (previous / "checkpoints").mkdir()
        shutil.copyfile(long_source / "checkpoints/c0001.blend", previous / "checkpoints/c0001.blend")
        first = edits.create_understand(previous, "Move the shown face again.")
        assert first.parent.parent == runtime
        longest = first / "work/os/0123456789ab/work/model_maps/m-0123456789abcdef0123/captures/v-0123456789abcdef/capture-delivery.json"
        # Production path length, independent of pytest's often much longer temporary prefix.
        production = Path(edits.__file__).resolve().parents[1] / "data/oe" / longest.relative_to(runtime)
        assert len(str(production)) < 260
