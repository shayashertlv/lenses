"""Unsupported requests finish honestly without gaining executable edit authority."""
import copy
import json
from types import SimpleNamespace

from PIL import Image
import pytest

from glasses3d.agent import edits, job_run
from glasses3d.agent.loop import FinishOnAccepted
from glasses3d.agent.tools import owner_edit, model_map
from glasses3d.agent.tools import model_map_geometry as G
from glasses3d.tests.test_owner_markings import fixture as marked_fixture
from .test_owner_edit_lifecycle_review import selected_source, _write


@pytest.fixture
def shown(selected_source):
    fixture = selected_source
    root = edits.create_understand(fixture.source, "Repair four requested features.", destination=fixture.destination)
    doc = edits.load(root)
    source, _ = marked_fixture()
    source["fingerprint"] = doc["source"]["fingerprint"]
    for obj in source["objects"]:
        obj["edges"] = [list(edge) for edge in sorted({tuple(sorted((a, b))) for poly in obj["polygons"]
                         for a, b in zip(poly, poly[1:] + poly[:1])})]
    mid = "m-" + "a" * 20
    directory = root / "work/model_maps" / mid
    snapshot = directory / "snapshot.json"
    _write(snapshot, source)
    asset = {"path": snapshot.relative_to(root).as_posix(), "sha256": edits.sha(snapshot)}
    manifest = {"schema": model_map.SCHEMA, "map_id": mid, "snapshot": asset, "assets": [asset],
                "source": {"fingerprint": source["fingerprint"]}, **G.catalogue(source)}
    path = directory / "map.json"
    _write(path, manifest)
    target = next(key for key, row in manifest["targets"].items()
                  if row["object_id"] == "O0001" and row.get("polygon_ids") == [5])
    scope = G.support(source, manifest["targets"], [target])
    image = directory / "pair.png"
    Image.new("RGB", (12, 8), "white").save(image)
    legend = {"target_id": target, "label": "T1", "description": "Outer side face"}
    capture = directory / "capture.json"
    _write(capture, {"schema": "g3d.model_map_capture/1", "map_sha256": edits.sha(path),
                    "annotated_target_ids": [target], "support_scopes": [scope], "target_legend": [legend],
                    "assets": [{"path": image.relative_to(root).as_posix(), "sha256": edits.sha(image)}],
                    "images": [{"path": str(image)}], "views": [{"ref": "ref01", "full_pair": str(image)}]})
    result = {"status": "ok", "capture_receipt": capture.relative_to(root).as_posix(),
              "capture_sha256": edits.sha(capture), "call_id": "shown-map"}
    model_map.mark_delivered(root, result)
    scene = SimpleNamespace(root=root, owner_edit=doc, s=SimpleNamespace(fingerprint=lambda: source["fingerprint"]))
    operation = {"id": "move", "kind": "translate_region", "target_ids": [target],
                 "scope_ref_sha256": G.digest(scope), "params": {"delta_mm": [.2, 0, 0]}}
    return SimpleNamespace(root=root, scene=scene, doc=doc, fixture=fixture, mid=mid, map=path, target=target,
                           capture=capture, capture_result=result, image=image, operation=operation, scope=scope)


def item(h, number=1, disposition="unsupported", *, located=True):
    executable = disposition == "executable"
    return {"number": number, "label": f"Feature {number}", "interpretation": "Owner's requested change.",
            "disposition": disposition, "reason": "" if executable else "Needs a topology change or a clearer target.",
            "direction": "Outward" if executable else "", "amount": "0.2 mm" if executable else "",
            "target_ids": [h.target] if located else [],
            "operation_json": json.dumps(h.operation) if executable else "[]"}


def finish(h, items, **kwargs):
    result = owner_edit.understand(h.scene, "understand-final", h.mid, edits.sha(h.map),
                                  [h.capture_result["capture_receipt"]], items, [], **kwargs)
    result["call_id"] = "understand-final"
    owner_edit.mark_delivered(h.scene, result)
    return result


def test_all_blocked_items_finish_publish_and_review_but_cannot_be_confirmed(shown):
    h = shown
    original = edits.tree_hashes(h.fixture.source)
    items = [item(h, n, "unsupported" if n < 3 else "needs_clarification", located=n != 4) for n in range(1, 5)]
    result = finish(h, items)
    assert result["status"] == "accepted" and result["disposition"] == "diagnostic_only"
    assert result["interpretations"] == items and not result["following_markings"]
    stop = FinishOnAccepted("understand_finish")
    outcome = stop(None, [SimpleNamespace(tool=SimpleNamespace(name="understand_finish"), output=json.dumps(result))])
    assert outcome.is_final_output
    artifact = edits.read(result["understanding"])
    assert artifact["operations"] == [] and artifact["geometry_changed"] is False
    assert artifact["map_sha256"] == edits.sha(h.map)
    assert artifact["source_fingerprint"] == h.doc["source"]["fingerprint"]
    published = job_run._publish_result(h.root, "offline", h.scene, {"status": "completed"})
    assert published["status"] == "diagnostic_only"
    assert published["finished"] is published["accepted_finish"] is False
    page = edits.understanding_page(h.root).read_text(encoding="utf-8")
    assert "Diagnostic only: no executable edit" in page and "Confirm or correct" not in page
    assert "needs clarification" in page and "Targets: T1" in page and items[0]["reason"] in page
    with pytest.raises(ValueError, match="no executable edit authority"):
        edits.create_edit(h.fixture.destination, "Yes, do these.")
    assert not (h.fixture.destination / "round-002-edit").exists()
    assert not (h.root / "runs").exists() and not (h.root / "budget").exists()
    assert edits.tree_hashes(h.fixture.source) == original


@pytest.mark.parametrize("damage", ["unshown_target", "failed_delivery", "unseen", "image", "map", "source", "no_receipts"])
def test_diagnostics_still_require_exact_current_delivered_map_evidence(shown, damage):
    h = shown
    record = item(h)
    if damage == "unshown_target":
        record["target_ids"] = ["O0001"]  # Exists in the catalogue, but was not shown.
    elif damage == "failed_delivery":
        model_map.mark_delivered(h.root, h.capture_result, False)
    elif damage == "unseen":
        _write(h.root / "work/job_scene/state.json", {"unseen": ["shown-map"]})
    elif damage == "image":
        h.image.write_bytes(b"changed evidence")
    elif damage == "map":
        receipt = edits.read(h.capture)
        receipt["map_sha256"] = "0" * 64
        _write(h.capture, receipt)
    elif damage == "source":
        h.scene.s.fingerprint = lambda: "changed-source"
    with pytest.raises(ValueError):
        owner_edit.understand(h.scene, "stop", h.mid, edits.sha(h.map),
            [] if damage == "no_receipts" else [h.capture_result["capture_receipt"]], [record], [])
    assert not (h.root / "work/owner_edit/understanding.json").exists()


@pytest.mark.parametrize("damage", ["missing_disposition", "unknown_disposition", "no_reason", "blocked_operation",
                                     "executable_empty", "zero_delta", "unknown_kind"])
def test_dispositions_cannot_hide_operations_or_fabricate_noops(shown, damage):
    h = shown
    record = item(h, disposition="executable" if damage in {"executable_empty", "zero_delta", "unknown_kind"} else "unsupported")
    if damage == "missing_disposition":
        del record["disposition"]
    elif damage == "unknown_disposition":
        record["disposition"] = "maybe"
    elif damage == "no_reason":
        record["reason"] = " "
    elif damage == "blocked_operation":
        record["operation_json"] = json.dumps(h.operation)
    elif damage == "executable_empty":
        record["operation_json"] = "[]"
    else:
        operation = copy.deepcopy(h.operation)
        if damage == "zero_delta":
            operation["params"]["delta_mm"] = [0, 0, 0]
        else:
            operation["kind"] = "rebuild_topology"
        record["operation_json"] = json.dumps(operation)
    with pytest.raises(ValueError):
        finish(h, [record])
    assert not (h.root / "work/owner_edit/understanding.json").exists()


def test_mixed_items_keep_blockers_but_only_real_operations_and_markings_gain_authority(shown):
    h = shown
    records = [item(h, disposition="executable"), item(h, 2), item(h, 3, "needs_clarification", located=False)]
    result = finish(h, records)
    assert result["disposition"] == "awaiting_owner_confirmation"
    assert result["following_markings"][0]["object_name"] == "Surface mark"
    second = edits.create_edit(h.fixture.destination, "Apply the executable change only.")
    doc = edits.load(second)
    assert doc["understanding"]["items"] == records
    assert edits.blocked_items(doc["understanding"]) == records[1:]
    assert "Blocked items remain undone" in edits.understanding_page(h.root).read_text(encoding="utf-8")
    assert "Resolve every unsupported or needs_clarification item" in edits.brief_suffix(doc)
    assert "before a paid EDIT round" in result["next"]
    authority = edits.Authority(second)
    sealed = authority.resolve()
    assert [operation["id"] for operation in sealed["all_operations"]] == ["move"]
    assert sealed["all_operations"][0]["scope"] == h.scope
    assert sealed["all_operations"][0]["marking_attachments"][0]["object_name"] == "Surface mark"
    for operation_ids in ([], ["blocked-2"], [h.target]):
        with pytest.raises(ValueError):
            authority.resolve(operation_ids)


@pytest.mark.parametrize("damage", ["support", "normal_ring", "following_marking"])
def test_mixed_plan_cannot_expand_the_shown_support_or_marking_authority(shown, damage):
    h = shown
    record = item(h, disposition="executable")
    operation = copy.deepcopy(h.operation)
    if damage == "following_marking":
        operation["marking_attachments"] = [{"object_id": "invented"}]
        record["operation_json"] = json.dumps(operation)
        result = finish(h, [record, item(h, 2)])
        sealed = edits.read(result["understanding"])["operations"][0]
        assert [row["object_name"] for row in sealed["marking_attachments"]] == ["Surface mark"]
        return
    del operation["scope_ref_sha256"]
    operation["scope"] = copy.deepcopy(h.scope)
    if damage == "support":
        operation["scope"]["vertex_weights"][0]["weight"] = .5
    else:
        operation["scope"]["normal_affected_polygon_ids"] = []
    record["operation_json"] = json.dumps(operation)
    with pytest.raises(ValueError):
        finish(h, [record, item(h, 2)])


@pytest.mark.parametrize("damage", ["evidence", "unseen", "failed_delivery"])
def test_diagnostic_publication_refuses_lost_delivery_or_changed_evidence(shown, damage):
    h = shown
    result = finish(h, [item(h)])
    if damage == "evidence":
        h.image.write_bytes(b"changed")
    elif damage == "unseen":
        _write(h.root / "work/job_scene/state.json", {"unseen": ["understand-final"]})
    else:
        owner_edit.mark_delivered(h.scene, result, False)
    assert job_run._publish_result(h.root, "offline", h.scene, {"status": "completed"})["status"] == "understanding_incomplete"


def test_diagnostic_cannot_bind_an_amount_preview(shown):
    with pytest.raises(ValueError, match="diagnostic-only"):
        finish(shown, [item(shown)], preview_receipt="work/pv/does-not-exist/preview.json")


def _replace_snapshot(h, source):
    manifest = edits.read(h.map)
    path = h.root / manifest["snapshot"]["path"]
    _write(path, source)
    manifest["snapshot"]["sha256"] = edits.sha(path)
    manifest["assets"][0]["sha256"] = edits.sha(path)
    _write(h.map, manifest)
    receipt = edits.read(h.capture)
    receipt["map_sha256"] = edits.sha(h.map)
    _write(h.capture, receipt)
    h.capture_result["capture_sha256"] = edits.sha(h.capture)
    model_map.mark_delivered(h.root, h.capture_result)


@pytest.mark.parametrize("coordinates", ["large_world", "large_local"])
def test_executable_effect_uses_worker_local_float32_coordinates(shown, coordinates):
    h = shown
    source = edits.read(h.root / edits.read(h.map)["snapshot"]["path"])
    obj = source["objects"][0]
    source["objects"] = [obj]
    shift = 100_000_000 if coordinates == "large_world" else -1_000_000
    obj["matrix_world"] = [[1, 0, 0, shift], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
    field = "vertices_world_mm" if coordinates == "large_world" else "vertices_local"
    for vertex in obj[field]:
        vertex[0] += abs(shift)
    _replace_snapshot(h, source)
    record = item(h, disposition="executable")
    operation = copy.deepcopy(h.operation)
    operation["params"]["delta_mm"] = [.2 if coordinates == "large_world" else .01, 0, 0]
    record["operation_json"] = json.dumps(operation)
    if coordinates == "large_world":
        assert finish(h, [record])["disposition"] == "awaiting_owner_confirmation"
    else:
        with pytest.raises(ValueError, match="no-op"):
            finish(h, [record])


def test_cancelling_operation_group_is_not_executable_authority(shown):
    h = shown
    record = item(h, disposition="executable")
    opposite = copy.deepcopy(h.operation)
    opposite.update(id="opposite", params={"delta_mm": [-.2, 0, 0]})
    record["operation_json"] = json.dumps([h.operation, opposite])
    with pytest.raises(ValueError, match="source unchanged"):
        finish(h, [record])


def test_private_understand_diagnostic_returns_blockers_without_applying_anything(shown, monkeypatch):
    from glasses3d.agent import job_scene
    from glasses3d.agent.temple_lab import owner_stage
    h = shown
    records = [item(h)]
    finish(h, records)
    head = {"id": "child-source", "sha256": "exact-source", "fingerprint": h.doc["source"]["fingerprint"]}
    h.scene.s.head = head
    h.scene.start = lambda: None
    closed = []
    h.scene.close = lambda: closed.append(True)
    h.scene.job = {}
    parent = SimpleNamespace(root=h.root.parent, s=SimpleNamespace(head=head, fingerprint=lambda: head["fingerprint"]))
    monkeypatch.setattr(owner_stage, "prepare", lambda *args: {"head": head, "seed": h.root / "inputs/seed.blend"})
    monkeypatch.setattr(job_scene, "JobScene", lambda *args, **kwargs: h.scene)
    monkeypatch.setattr(owner_stage, "registry", lambda *args: [])
    monkeypatch.setattr(owner_stage, "build_instructions", lambda *args, **kwargs: "offline")
    monkeypatch.setattr(owner_stage, "instruction_contract", lambda *args, **kwargs: None)
    monkeypatch.setattr(owner_stage.brief, "build_brief", lambda *args: "Owner request")
    monkeypatch.setattr(owner_stage.brief, "host_text", lambda *args: "Owner request")
    monkeypatch.setattr(owner_stage.jobs, "references", lambda *args: [])
    outcome = {"status": "completed", "exit_checkpoint": {**head, "verified": True}}
    bridge = SimpleNamespace(check=lambda: None, run=lambda *args: outcome)
    result = owner_stage.run(parent, SimpleNamespace(), bridge, "offline-child", "23", job_run.settings_default())
    assert result["status"] == "ok" and result["parent_unchanged"] is True
    assert result["understanding_status"] == "diagnostic_only"
    assert result["blocked_items"] == result["interpretations"] == records
    assert closed == [True] and parent.s.head == head
