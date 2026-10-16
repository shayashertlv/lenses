"""Independent owner-edit lifecycle controls; no worker or provider is started."""
from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

from PIL import Image
import pytest

from glasses3d.agent import approvals as A, edits, jobs, tool_reports
from glasses3d.agent.journal import CheckpointIndex
from .test_job_tools import scene, finish_scene, invoke, record  # shared worker-free SDK fixture


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def selected_source(tmp_path, monkeypatch):
    photos = tmp_path / "photos"
    photos.mkdir()
    Image.new("RGB", (37, 21), (72, 130, 180)).save(photos / "front.png")
    views = tmp_path / "views.json"
    _write(views, {"front.png": {"kind": "front", "side": "both"}})
    source = jobs.create_job(images=photos, views=views, name="Owner source",
                             description="  Owner's exact original description.\r\n  ",
                             jobs_root=tmp_path / "jobs")
    (source / "inputs").mkdir()
    (source / "inputs/seed.blend").write_bytes(b"original empty seed")
    index = CheckpointIndex(source / "checkpoints")
    for cid, payload in (("c0001", b"chosen delivered checkpoint"),
                         ("c0002", b"later unselected checkpoint")):
        index.dir.mkdir(exist_ok=True)
        (index.dir / (cid + ".blend")).write_bytes(payload)
        index.append({"id": cid, "kind": "save", "file": cid + ".blend",
                      "sha256": hashlib.sha256(payload).hexdigest(),
                      "fingerprint": hashlib.sha256((cid + " fingerprint").encode()).hexdigest()})
    basis_dir = source / "cameras/basis-001"
    _write(basis_dir / "basis.json", {"id": "basis-001", "camera": "frozen"})
    _write(basis_dir / "cameras.json", {"theta_deg": {"left": 7, "right": 11}})
    basis = SimpleNamespace(id=basis_dir.name, dir=basis_dir)
    chosen = index.get("c0001")
    report = {"checkpoint": chosen["id"], "fingerprint": chosen["fingerprint"],
              "run_id": "selected-run", "glb_sha256": "f" * 64}
    saved = {"report_data": report, "delivery_id": "selected-delivery", "glb_sha256": "f" * 64}
    monkeypatch.setattr(edits.delivery, "load_delivery", lambda root, delivery_id=None: saved)
    monkeypatch.setattr(edits, "_source_basis", lambda *args, **kwargs: basis)
    _write(source / "runs/old/approval.json", {"owner_words": "old source approval"})
    _write(source / "budget/job-ledger.json", {"settled": 17})
    return SimpleNamespace(source=source, saved=saved, basis=basis, chosen=chosen,
                           destination=tmp_path / "owner-edit")


def test_edit_copies_selected_delivery_not_newer_head_and_never_mutates_source(selected_source):
    f = selected_source
    original = edits.tree_hashes(f.source)
    words = "  Please tilt face 3 outward.\r\nKeep the lenses unchanged.  "
    root = edits.create_understand(f.source, words, delivery_id="selected-delivery", destination=f.destination)
    doc = edits.load(root)
    assert (root / "inputs/seed.blend").read_bytes() == b"chosen delivered checkpoint"
    assert doc["source"]["checkpoint"] == "c0001"
    assert doc["request_text"] == words
    assert words in edits.brief_suffix(doc)
    assert jobs.load_job(root) == jobs.load_job(f.source)
    assert edits.tree_hashes(root / "cameras") == edits.tree_hashes(f.source / "cameras")
    assert not (root / "runs").exists() and not (root / "budget").exists()
    assert edits.tree_hashes(f.source) == original


@pytest.mark.parametrize("damage", ["fingerprint", "checkpoint_bytes"])
def test_selected_delivery_mismatch_refuses_before_any_round_exists(selected_source, damage):
    f = selected_source
    if damage == "fingerprint":
        f.saved["report_data"]["fingerprint"] = "0" * 64
    else:
        (f.source / "checkpoints/c0001.blend").write_bytes(b"tampered chosen checkpoint")
    with pytest.raises(ValueError, match="checkpoint"):
        edits.create_understand(f.source, "Move the shown face.", destination=f.destination)
    assert not f.destination.exists()


@pytest.mark.parametrize("name", ["inputs/seed.blend", "cameras/basis-001/cameras.json"])
def test_edit_round_refuses_changed_source_geometry_or_frozen_arm_openings(selected_source, name):
    f = selected_source
    root = edits.create_understand(f.source, "Move the shown face.", destination=f.destination)
    path = root / name
    path.write_bytes(path.read_bytes() + b" changed")
    with pytest.raises(ValueError, match="input changed"):
        edits.load(root)


def test_new_round_needs_its_own_approval_and_binds_verbatim_request(selected_source):
    f = selected_source
    words = "\nPlease move edge 2 out by 0.4 mm.  "
    root = edits.create_understand(f.source, words, destination=f.destination)

    def request():
        doc = edits.load(root)
        return A.build_request(root, instructions_sha256="a" * 64, tool_registry_sha256="b" * 64,
            settings={"effort": "high", "max_output_tokens": 16384, "max_turns": None},
            run_inputs=A.run_input_binding(root, edits.brief_suffix(doc), jobs.references(root)))

    bound = request()
    run = root / "runs/new-round"
    A.write_approval_request(run, job_id=root.name, request=bound, estimate={"expected_micro_usd": 10},
                             suggested_cap_usd="23", requested_cap_usd="23")
    with pytest.raises(A.ApprovalRefused, match="owner approval"):
        A.check_approval(run, current_request_sha256=A.request_sha256(bound), requested_cap_usd="23")
    owner_words = "Yes, this exact understanding round only.\r\n"
    A.write_approval(run, max_usd="23", owner_words=owner_words)
    approved = A.check_approval(run, current_request_sha256=A.request_sha256(request()), requested_cap_usd="23")
    assert approved.owner_words == owner_words
    path = root / "inputs/owner-edit.json"
    doc = edits.read(path)
    doc["request_text"] += " Change the lenses too."
    _write(path, doc)
    with pytest.raises(A.ApprovalRefused, match="changed since"):
        A.check_approval(run, current_request_sha256=A.request_sha256(request()), requested_cap_usd="23")


def test_understand_session_refuses_mutation_and_other_checkpoint_before_worker(selected_source, monkeypatch):
    from glasses3d.agent.job_scene import JobScene
    from glasses3d.agent.journal import BlenderSession
    f = selected_source
    root = edits.create_understand(f.source, "Move the shown face.", destination=f.destination)
    scene = JobScene(root)
    scene.s.index.append({"id": "c0000", "kind": "start", "fingerprint": f.chosen["fingerprint"]})
    calls = []
    monkeypatch.setattr(scene.s, "_journal", lambda *args, **kwargs: calls.append(args))

    def never(*args, **kwargs):
        raise AssertionError("understand reached a mutating worker operation")

    monkeypatch.setattr(BlenderSession, "_mutating", never)
    monkeypatch.setattr(BlenderSession, "restore_checkpoint", never)
    out = scene.s._mutating("execute_blender_code", "execute", {"code": "change_geometry()"},
                            30, "try-mutation", "edit", 0, arguments={})
    assert out["status"] == "refused" and out["code"] == "E_UNDERSTAND_READ_ONLY"
    restored = scene.s.restore_checkpoint("c0009", call_id="try-restore")
    assert restored["status"] == "refused" and restored["code"] == "E_UNDERSTAND_READ_ONLY"
    assert len(calls) == 1 and scene.s.worker is None
    assert scene.s.head["fingerprint"] == f.chosen["fingerprint"]


@pytest.mark.parametrize("condition", ["shown", "unseen", "changed", "wrong_source", "failed_output"])
def test_second_round_uses_exact_shown_understanding_and_new_confirmation(selected_source, condition):
    f = selected_source
    first = edits.create_understand(f.source, "Move the shown face.", destination=f.destination)
    snapshot = first / "work/model_maps/test/snapshot.json"
    _write(snapshot, {"fingerprint": f.chosen["fingerprint"]})
    name = snapshot.relative_to(first).as_posix()
    artifact_path = first / "work/owner_edit/understanding.json"
    artifact = {"schema": edits.UNDERSTANDING_SCHEMA, "status": "awaiting_owner_confirmation",
                "call_id": "understand-final", "source_fingerprint": f.chosen["fingerprint"],
                "snapshot_path": name, "evidence_files": {name: edits.sha(snapshot)}, "operations": [{
                    "id": "shown-move", "kind": "translate_region", "target_ids": ["shown-face"],
                    "params": {"delta_mm": [.3, 0, 0]}}]}
    _write(artifact_path, artifact)
    _write(artifact_path.with_name("understanding-delivery.json"), {
        "schema": "g3d.owner_understanding_delivery/1", "call_id": "understand-final",
        "delivered": condition != "failed_output", "sha256": edits.sha(artifact_path)})
    if condition == "unseen":
        _write(first / "work/job_scene/state.json", {"unseen": ["understand-final"]})
    if condition in ("changed", "wrong_source"):
        artifact["operations"] = [{"id": "new-unshown-target"}]
        if condition == "wrong_source":
            artifact["source_fingerprint"] = "0" * 64
        _write(artifact_path, artifact)
        if condition == "wrong_source":
            _write(artifact_path.with_name("understanding-delivery.json"), {
                "call_id": "understand-final", "delivered": True, "sha256": edits.sha(artifact_path)})
    confirmation = "  Correct it to 0.3 mm outwards.\r\nKeep the same edge.  "
    if condition != "shown":
        with pytest.raises(ValueError):
            edits.create_edit(f.destination, confirmation, correction=True)
        assert not (f.destination / "round-002-edit").exists()
        return
    before = edits.tree_hashes(first)
    second = edits.create_edit(f.destination, confirmation, correction=True)
    doc = edits.load(second)
    assert doc["confirmation_text"] == confirmation and doc["correction"] is True
    assert doc["request_text"] == "Move the shown face."
    assert doc["understanding"] == artifact
    assert edits.tree_hashes(first) == before
    assert not (second / "runs").exists() and not (second / "budget").exists()


def test_frozen_camera_lookup_recovers_hash_verified_paged_finish_receipt(tmp_path, monkeypatch):
    from glasses3d.g3d_host import compare
    basis_dir = tmp_path / "cameras/basis-001"
    _write(basis_dir / "basis.json", {"id": "basis-001"})
    basis = SimpleNamespace(id=basis_dir.name, dir=basis_dir)
    monkeypatch.setattr(compare, "load_basis", lambda root, name: basis)
    look = {"status": "ok", "export_sha256": "f" * 64, "basis": basis.id,
            "basis_sha256": edits.sha(basis_dir / "basis.json")}
    full = tool_reports.save_report(tmp_path, {"schema": tool_reports.REPORT_SCHEMA,
               "result": {"status": "review_required", "look_comparison": look}})
    bounded = {"status": "review_required", "full_report": full}
    _write(tmp_path / "runs/selected-run/tools/0088/result.json", {"result": [
        {"type": "text", "text": json.dumps(bounded) + "\nBudget: $16.70 settled."}]})
    report = {"run_id": "selected-run", "glb_sha256": "f" * 64}
    assert edits._source_basis(tmp_path, report).id == basis.id
    (tmp_path / full["path"]).write_text("changed report", encoding="utf-8")
    with pytest.raises(ValueError):
        edits._source_basis(tmp_path, report)


@pytest.mark.parametrize("head_fp,live_fp,expected", [
    ("applied-fingerprint", "applied-fingerprint", "ok"),
    ("restored-original-fingerprint", "restored-original-fingerprint", "refused"),
    ("applied-fingerprint", "uncheckpointed-drift", "refused"),
])
def test_applied_ids_are_not_evidence_after_restore_or_live_drift(head_fp, live_fp, expected):
    from glasses3d.agent import owner_edits
    receipts = [{"operation_ids": ["change-1"], "fingerprint": "applied-fingerprint"}]
    sealed = {"operations": [{"id": "change-1", "params": {"delta_mm": [0.4, 0, 0]}}],
              "applied_operation_ids": ["change-1"], "receipts": receipts}
    scene = SimpleNamespace(owner_edit_authority=SimpleNamespace(resolve=lambda *args: sealed),
        s=SimpleNamespace(head={"id": "c0009", "fingerprint": head_fp}, fingerprint=lambda: live_fp))
    result = owner_edits.apply(scene, ["change-1"], "repeat-after-restore")
    assert result["status"] == expected
    if expected == "ok":
        assert result["idempotent"] is True and result["receipts"] == receipts
    else:
        assert result["code"] == "E_OWNER_REPLAY"


def test_edit_report_returns_structured_native_details_even_without_detail_in_caption(tmp_path, monkeypatch):
    from glasses3d.agent import owner_edits
    from glasses3d.agent.tools import owner_edit
    root = tmp_path
    _write(root / "inputs/owner-edit.json", {"local": "test binding"})
    image_paths = {}
    for key, colour in (("photo", "white"), ("before", "beige"), ("after", "tan"),
                        ("detail-before", "gray"), ("detail-after", "black")):
        image_paths[key] = root / (key + ".png")
        Image.new("RGB", (32, 21), colour).save(image_paths[key])
    doc = {"phase": "edit", "request_text": "Move edge 2.", "confirmation_text": "Yes.",
           "source": {"export_sha256": "f" * 64, "checkpoint": "c0001", "basis": "basis-001"},
           "understanding": {"map_id": "m-test", "map_sha256": "a" * 64, "operations": []}}
    monkeypatch.setattr(edits, "load", lambda _: doc)
    monkeypatch.setattr(owner_edit.model_map, "load_map", lambda *args: {
        "assets": [{"path": "source.glb", "sha256": "f" * 64}]})
    monkeypatch.setattr(owner_edits.ScopeGuard, "verify", lambda *args: {"ok": True, "changes": []})

    def capture(job_root, export, call_id, **kwargs):
        phase = "before" if call_id.endswith("-before") else "after"
        return {"status": "ok", "views": [{"ref": "front", "photo_path": str(image_paths["photo"]),
                "drawing_path": str(image_paths[phase])}], "worn_front_path": str(image_paths[phase]),
                "images": [{"path": str(image_paths["detail-" + phase]), "view": "front: outer_corner 1.1",
                            "kind": "look_detail", "detail_kind": "outer_corner"}]}

    monkeypatch.setattr(owner_edit.look, "capture", capture)
    scene = SimpleNamespace(root=root, s=SimpleNamespace(head={"id": "c0002", "fingerprint": "new"}),
        seeing=SimpleNamespace(export_head=lambda **kwargs: {"sha256": "e" * 64}),
        owner_edit_authority=SimpleNamespace(receipts=lambda: []))
    result = owner_edit.change_report(scene, "report-test", "Changed requested edge.", [])
    paths = {row["path"] for row in result["images"]}
    assert str(image_paths["detail-before"]) in paths
    assert str(image_paths["detail-after"]) in paths


def test_finish_requires_edit_comparison_images_to_have_been_delivered(finish_scene):
    scene = finish_scene
    scene.owner_edit = {"phase": "edit"}
    scene.s.fingerprint = lambda: scene.s.head["fingerprint"]
    _write(scene.root / "inputs/owner-edit.json", {"local": "bound edit"})
    _write(scene.root / "work/owner_edit/change-report.json", {
        "schema": "g3d.owner_edit_report/1", "status": "ok", "call_id": "failed-image-output",
        "fingerprint": scene.s.head["fingerprint"], "authority_sha256": edits.sha(scene.root / "inputs/owner-edit.json")})
    result = record(invoke(scene, "finish", {"summary": "done"}, cid="finish-with-unseen-report"))
    assert result["status"] == "refused"
    assert any(row.get("check") == "owner_edit_report" for row in result["failures"])
    assert scene.finish is None


def test_edit_run_approval_binds_the_actual_owner_specialist_contract(selected_source):
    from glasses3d.agent import job_run
    from glasses3d.agent.temple_lab import owner_stage
    f = selected_source
    root = edits.create_understand(f.source, "Move the shown face.", destination=f.destination)
    spec, scene = job_run.prepare_run(root, "independent-estimate", write_request=True)
    expected = owner_stage.contract(jobs.load_job(root), spec.settings, "23", edits.load(root))
    assert spec.stage_contracts == (expected,)
    assert scene.s.worker is None
    requested = edits.read(root / "runs/independent-estimate/approval-request.json")
    assert requested["request"]["settings"]["stages"] == [expected.binding()]
    assert not (root / "runs/independent-estimate/approval.json").exists()


def test_map_can_show_visible_object_without_editable_source_polygon_correspondence(tmp_path, monkeypatch):
    import numpy as np
    from glasses3d.agent.tools import model_map
    for name in ("photo", "drawing", "ids"):
        Image.new("RGB", (80, 60), "white").save(tmp_path / (name + ".png"))
    camera = {"width": 80, "height": 60}
    row = {"ref": "front", "camera": camera, "photo": str(tmp_path / "photo.png"),
           "drawing": str(tmp_path / "drawing.png"), "part_id": str(tmp_path / "ids.png")}
    snapshot = {"objects": [{"id": "O0001", "role": "frame", "side": 1,
                 "vertices_world_mm": [[0, 0, 0], [1, 0, 0], [0, 1, 0]], "polygons": [[0, 1, 2]]}]}
    target = {"id": "O0001", "object_id": "O0001", "kind": "object", "polygon_ids": [0],
              "aliases": ["Modified visible frame"], "source_editable": False}
    monkeypatch.setattr(model_map.look_panels, "posed_geometry", lambda geometry, pose: geometry)
    monkeypatch.setattr(model_map.G, "first_hits", lambda *args: np.zeros((60, 80), dtype=int))
    monkeypatch.setattr(model_map.G, "project", lambda points, camera: (
        np.full((len(points), 2), 30.0), np.ones(len(points))))
    result = model_map._annotate(row, snapshot, [target], SimpleNamespace(),
        np.array(["O0001"], dtype=object), np.array([-1]), [], tmp_path, 0)
    assert result["annotated_target_ids"] == ["O0001"]
    assert result["labels"][0]["source_editable"] is False
    assert result["labels"][0]["visible_pixels"] == 80 * 60


def test_understanding_html_preserves_owner_words_and_links_native_local_evidence(tmp_path):
    from html.parser import HTMLParser
    from urllib.parse import unquote
    photo_pair = tmp_path / "work/model_maps/m-test/front pair.png"
    photo_pair.parent.mkdir(parents=True)
    Image.new("RGB", (30, 20), "white").save(photo_pair)
    receipt = tmp_path / "work/model_maps/m-test/capture.json"
    _write(receipt, {"views": [{"ref": "front", "full_pair": str(photo_pair)}]})
    words = "  Keep <this> & that; don't run <script>alert(1)</script>.\nSecond line.  "
    _write(tmp_path / "work/owner_edit/understanding.json", {
        "scripted": True, "request_text": words, "items": [{"number": 1, "label": "Edge <2>",
        "interpretation": "Move & retain", "direction": "outwards", "amount": "0.3 mm",
        "target_ids": ["E0002"]}], "limitations": ["Adjacent face may move."],
        "capture_receipts": [receipt.relative_to(tmp_path).as_posix()]})
    page = edits.understanding_page(tmp_path)
    content = page.read_text(encoding="utf-8")

    class Parser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.links, self.data, self.scripts = [], [], 0
        def handle_starttag(self, tag, attrs):
            if tag == "script":
                self.scripts += 1
            self.links += [value for name, value in attrs if name in ("href", "src")]
        def handle_data(self, data):
            self.data.append(data)

    parsed = Parser()
    parsed.feed(content)
    assert words in parsed.data
    assert "scripted by Codex, not Astra" in "".join(parsed.data)
    assert parsed.scripts == 0 and parsed.links
    assert all((page.parent / unquote(link)).resolve() == photo_pair for link in parsed.links)
    assert all((page.parent / unquote(link)).is_file() for link in parsed.links)


@pytest.mark.parametrize("condition", ["success", "incomplete", "unverified", "missing_finish", "parent_stale", "apply_refused",
                                        "child_live_drift", "child_snapshot_drift"])
def test_owner_specialist_only_applies_completed_verified_current_child(tmp_path, monkeypatch, condition):
    import copy
    from glasses3d.agent import job_scene, job_run
    from glasses3d.agent.temple_lab import owner_stage
    parent_head = {"id": "parent-before", "fingerprint": "source-fp", "sha256": "source-sha"}
    child_head = {"id": "child-after", "fingerprint": "child-fp", "sha256": "child-sha"}
    inherited = {"operation_ids": ["inherited"], "fingerprint": "source-fp"}
    new_receipt = {"operation_ids": ["new"], "operations": [{"id": "new"}], "fingerprint": "child-fp"}
    recorded, mutations, closes = [], [], []
    sealed = {"snapshot": {}, "all_operations": [{"id": "new"}], "round_id": "edit:2",
              "authority_sha256": "authority", "confirmation_sha256": "confirmation",
              "request_text": "Owner request", "confirmation_text": "Yes, those targets"}
    child = SimpleNamespace(root=tmp_path / "child", owner_edit={"phase": "edit"}, job={},
        s=SimpleNamespace(head=child_head, fingerprint=lambda: "uncheckpointed-fp" if condition == "child_live_drift" else "child-fp"),
        start=lambda: None, close=lambda: closes.append("closed"),
        finish=None if condition == "missing_finish" else {"status": "accepted", "fingerprint": "child-fp", "summary": "Done"},
        owner_edit_authority=SimpleNamespace(resolve=lambda *args: sealed,
                                             receipts=lambda: [inherited, new_receipt]))
    parent_session = SimpleNamespace(head=copy.deepcopy(parent_head))
    parent_session.fingerprint = lambda: parent_session.head["fingerprint"]

    def mutate(*args, **kwargs):
        mutations.append((args, kwargs))
        if condition == "apply_refused":
            return {"status": "refused", "code": "E_SCOPE"}
        parent_session.head = {"id": "parent-after", "fingerprint": "child-fp"}
        return {"status": "ok", "owner_scope_check": {"ok": True}}

    parent_session.call_mutating = mutate
    parent = SimpleNamespace(root=tmp_path, s=parent_session, scripted=True,
        owner_edit_authority=SimpleNamespace(record_applied=recorded.append))
    monkeypatch.setattr(owner_stage, "prepare", lambda *args: {
        "head": copy.deepcopy(parent_head), "seed": tmp_path / "seed.blend", "inherited_receipts": 1})
    monkeypatch.setattr(job_scene, "JobScene", lambda *args, **kwargs: child)
    monkeypatch.setattr(owner_stage, "registry", lambda *args: [])
    monkeypatch.setattr(owner_stage, "build_instructions", lambda *args, **kwargs: "generic instructions")
    monkeypatch.setattr(owner_stage, "instruction_contract", lambda *args, **kwargs: None)
    monkeypatch.setattr(owner_stage.brief, "build_brief", lambda *args: "Owner description")
    monkeypatch.setattr(owner_stage.brief, "host_text", lambda *args: "Host description")
    monkeypatch.setattr(owner_stage.edits, "brief_suffix", lambda *args: "Owner request")
    monkeypatch.setattr(owner_stage.jobs, "references", lambda *args: [])
    def captured_snapshot(scene, label):
        fingerprint = scene.s.fingerprint()
        if scene is child and condition == "child_snapshot_drift":
            fingerprint = "uncheckpointed-fp"
        return {"fingerprint": fingerprint, "objects": []}, tmp_path / (label + ".json")

    monkeypatch.setattr(owner_stage.owner_edits, "snapshot", captured_snapshot)
    monkeypatch.setattr(owner_stage.owner_edits, "verify_snapshots", lambda *args: {"ok": True})

    def run_child(*args):
        if condition == "parent_stale":
            parent_session.head = {"id": "parent-other", "fingerprint": "other-fp"}
        return {"status": "failed" if condition == "incomplete" else "completed",
                "exit_checkpoint": {**child_head, "verified": condition != "unverified"}}

    bridge = SimpleNamespace(check=lambda: None, run=run_child)
    result = owner_stage.run(parent, SimpleNamespace(), bridge, "stage-control", "23", job_run.settings_default())
    assert closes == ["closed"]
    assert child.scripted is True
    if condition == "success":
        assert result["status"] == "ok" and len(mutations) == len(recorded) == 1
        assert recorded[0]["operation_ids"] == ["new"]
        assert recorded[0]["child_receipts"] == [new_receipt]
        assert recorded[0]["fingerprint"] == parent_session.head["fingerprint"] == "child-fp"
    else:
        assert result["status"] == "refused" and not recorded
        assert len(mutations) == (1 if condition == "apply_refused" else 0)


@pytest.mark.parametrize("corrected", [False, True])
def test_read_only_scope_check_replays_current_receipt_without_reapplying_or_duplicating(tmp_path, monkeypatch, corrected):
    import copy
    from glasses3d.agent import owner_edits
    from glasses3d.tests.test_owner_mesh import box, operation, snapshot
    obj = box()
    source = snapshot(obj)
    op = operation(obj)
    amount = .75 if corrected else .5
    current = copy.deepcopy(source)
    for key in ("vertices_local", "vertices_world_mm"):
        for vertex in (1, 2, 5, 6):
            current["objects"][0][key][vertex][0] += amount
    applied_op = copy.deepcopy(op)
    applied_op["params"] = {"delta_mm": [amount, 0, 0]}
    receipt = {"fingerprint": "current-fp", "operations": [applied_op], "operation_ids": [op["id"]]}
    sealed = {"snapshot": source, "source_fingerprint": "source-fp", "all_operations": [op],
              "receipts": [receipt], "authority_sha256": "authority", "round_id": "edit:2",
              "confirmation_sha256": "confirmation", "request_text": "Move outward.",
              "confirmation_text": "Use 0.75 mm." if corrected else "Yes."}
    recorded = []
    authority = SimpleNamespace(resolve=lambda *args: sealed, record_applied=recorded.append)
    scene = SimpleNamespace(owner_edit_authority=authority,
        s=SimpleNamespace(head={"id": "c0002", "parent": "c0001", "fingerprint": "current-fp"},
                          index=SimpleNamespace(get=lambda key: {"fingerprint": "source-fp"}),
                          fingerprint=lambda: "current-fp"),
        seeing=SimpleNamespace(export_head=lambda *args: {
            "status": "exported", "validate_spec": {"ok": True}, "sha256": "export"}))
    monkeypatch.setattr(owner_edits, "snapshot", lambda *args: (current, tmp_path / "current.json"))
    result = owner_edits.ScopeGuard(authority).verify(scene)
    assert result["ok"] is True
    assert result["confirmed_amounts_match"] is True
    assert recorded == []


def test_each_round_refreshes_only_copied_brief_and_binds_exact_owner_words(selected_source):
    from glasses3d.agent import brief, job_run
    f = selected_source
    original = brief.build_brief(jobs.load_job(f.source)).encode("utf-8")
    (f.source / "inputs/brief.txt").write_bytes(original)
    source_hashes = edits.tree_hashes(f.source)
    words = "  Move the numbered face.\r\nKeep all other geometry.  "
    first = edits.create_understand(f.source, words, destination=f.destination)
    first_doc = edits.load(first)
    spec, scene = job_run.prepare_run(first, "understand-estimate", write_request=True)
    assert spec.brief == brief.build_brief(jobs.load_job(first)) + edits.brief_suffix(first_doc)
    assert (first / "inputs/brief.txt").read_bytes() == spec.brief.encode("utf-8")
    assert (first / "inputs/source-brief.txt").read_bytes() == original
    assert first_doc["immutable_files"]["inputs/brief.txt"] == edits.sha(first / "inputs/brief.txt")
    assert words in spec.brief and scene.s.worker is None

    snapshot = first / "work/model_maps/test/snapshot.json"
    _write(snapshot, {"fingerprint": f.chosen["fingerprint"]})
    relative = snapshot.relative_to(first).as_posix()
    artifact_path = first / "work/owner_edit/understanding.json"
    artifact = {"schema": edits.UNDERSTANDING_SCHEMA, "status": "awaiting_owner_confirmation",
                "call_id": "understand-final", "source_fingerprint": f.chosen["fingerprint"],
                "snapshot_path": relative, "evidence_files": {relative: edits.sha(snapshot)}, "operations": [{
                    "id": "shown-move", "kind": "translate_region", "target_ids": ["shown-face"],
                    "params": {"delta_mm": [.75, 0, 0]}}]}
    _write(artifact_path, artifact)
    _write(artifact_path.with_name("understanding-delivery.json"), {
        "schema": "g3d.owner_understanding_delivery/1", "call_id": "understand-final",
        "delivered": True, "sha256": edits.sha(artifact_path)})
    first_hashes = edits.tree_hashes(first)
    confirmation = "  Yes. Use 0.75 mm, on those same targets.\r\n"
    second = edits.create_edit(f.destination, confirmation, correction=True)
    second_doc = edits.load(second)
    second_spec, second_scene = job_run.prepare_run(second, "edit-estimate", write_request=True)
    assert second_spec.brief == brief.build_brief(jobs.load_job(second)) + edits.brief_suffix(second_doc)
    assert (second / "inputs/brief.txt").read_bytes() == second_spec.brief.encode("utf-8")
    assert (second / "inputs/source-brief.txt").read_bytes() == original
    assert second_doc["immutable_files"]["inputs/brief.txt"] == edits.sha(second / "inputs/brief.txt")
    assert words in second_spec.brief and confirmation in second_spec.brief and second_scene.s.worker is None
    assert second_spec.brief != spec.brief
    assert not (first / "runs/understand-estimate/approval.json").exists()
    assert not (second / "runs/edit-estimate/approval.json").exists()
    assert edits.tree_hashes(first) == first_hashes
    assert edits.tree_hashes(f.source) == source_hashes


def test_edit_brief_keeps_exact_words_and_numbered_items_without_full_support_payload():
    import copy
    words = "  Move face 2 outwards.\r\nKeep the lenses unchanged.  "
    confirmation = "  Yes, but use 0.75 mm.\r\n"
    item = {"number": 1, "label": "Hinge-side face", "interpretation": "Move the shown boundary.",
            "direction": "outwards", "amount": "0.75 mm", "target_ids": ["F0002"],
            "operation_json": json.dumps({"id": "item-1", "kind": "translate_region", "target_ids": ["F0002"],
                                          "scope": {"shown_scope_sha256": "a" * 64},
                                          "params": {"delta_mm": [.75, 0, 0]}})}
    huge = "FULL_VERTEX_SUPPORT_MUST_NOT_ENTER_BRIEF_" * 1000
    evidence = "FULL_EVIDENCE_HASH_TABLE_MUST_NOT_ENTER_BRIEF_" * 1000
    understanding = {"schema": edits.UNDERSTANDING_SCHEMA, "source_fingerprint": "f" * 64,
                     "items": [item], "limitations": ["Only the marked vertices move."],
                     "map_id": "m-shown", "map_sha256": "b" * 64,
                     "capture_receipts": ["work/model_maps/m-shown/capture.json"],
                     "operations": [{"id": "item-1", "scope": {"vertex_weights": huge}}],
                     "evidence_files": {"source.json": evidence}}
    doc = {"phase": "edit", "request_text": words, "confirmation_text": confirmation,
           "understanding": understanding}
    original = copy.deepcopy(doc)
    text = edits.brief_suffix(doc)
    assert words in text and confirmation in text
    shown = json.loads(text[text.index('{'):])
    assert shown == {key: understanding[key] for key in (
        "schema", "source_fingerprint", "items", "limitations", "map_id", "map_sha256", "capture_receipts")}
    assert shown["items"] == [item]
    assert "FULL_VERTEX_SUPPORT_MUST_NOT_ENTER_BRIEF_" not in text
    assert "FULL_EVIDENCE_HASH_TABLE_MUST_NOT_ENTER_BRIEF_" not in text
    assert "inputs/understanding.json" in text and len(text) < 3000
    assert doc == original  # The full sealed authority is retained untouched outside the brief.
