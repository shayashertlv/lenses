"""Full-tool fallback diagnostics keep independent change evidence without introducing quality gates."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from glasses3d.agent import delivery as D, owner_fallback, worker_client
from glasses3d.agent.journal import CheckpointIndex
from glasses3d.tests.agent.test_delivery import digest, make_finished
from glasses3d.tests.agent.test_draft_delivery import draft_fixture


def source_snapshot():
    vertices = [[0., 0., 0.], [10., 0., 0.], [0., 10., 0.],
                [20., 0., 0.], [30., 0., 0.], [20., 10., 0.]]
    obj = {"id": "O0001", "name": "Front", "vertices_local": copy.deepcopy(vertices),
           "vertices_world_mm": copy.deepcopy(vertices), "polygons": [[0, 1, 2], [3, 4, 5]],
           "edges": [[0, 1], [1, 2], [0, 2], [3, 4], [4, 5], [3, 5]], "loops": list(range(6)),
           "triangles": [[0, 1, 2], [3, 4, 5]], "triangle_polygons": [0, 1],
           "corner_normals": [[0., 0., 1.]] * 6, "polygon_smooth": [False, False],
           "material_names": ["Frame"], "material_definitions": [{"parameters": {"roughness": 0.3}}],
           "material_sha256": "a" * 64, "polygon_material": [0, 0], "material_slot_links": ["DATA"],
           "uv_layers": [{"name": "UV", "uv": [[0., 0.]] * 6}], "uv_active_index": 0,
           "matrix_world": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]], "parent_name": None,
           "evaluated": {"positions_match_source": True, "topology_matches_source": True,
                         "vertices_world_mm": copy.deepcopy(vertices)}}
    return {"schema": "g3d.mesh_snapshot/1", "fingerprint": "b" * 64, "origin_mm": [0, 0, 0],
            "objects": [obj], "unsupported_rendered_geometry": [], "scene_state": {}}


def marks(*, polygons=(0,), status="resolved"):
    region = {"object_id": "O0001", "object_name": "Front", "polygon_ids": list(polygons)}
    return [{"item_id": "I1", "capture_id": "C1", "label": "A", "status": status,
             "resolved_polygons": [region], "behind_polygons": [],
             "footprint": {"status": status, "polygons": [region]},
             "candidate_target_ids": {"parts": ["O0001"]}, "edit_authority": False}]


def move(snapshot, vertex=0, mm=1.):
    obj = snapshot["objects"][0]
    for key in ("vertices_local", "vertices_world_mm"):
        obj[key][vertex][2] += mm
    obj["evaluated"]["vertices_world_mm"][vertex][2] += mm


def measure(before, after, resolved=None):
    return D.measure_full_edit_effects(before, after, marks() if resolved is None else resolved,
        item_id="I1", source_glb_sha256="a" * 64, delivered_glb_sha256="c" * 64)


def test_inside_and_outside_geometry_have_counts_displacements_and_no_quality_gate():
    before, after = source_snapshot(), source_snapshot()
    move(after, 0, 2.)
    inside = measure(before, after)
    assert inside["informational_only"] is True
    assert inside["summary"]["geometry_outside_item_region"] is False
    assert inside["objects"][0]["geometry"]["changed_vertex_count"] == 1
    move(after, 3, 4.)
    report = measure(before, after)
    row = report["objects"][0]["geometry"]
    assert report["summary"]["geometry_outside_item_region"] is True
    assert row["changed_vertex_ids"] == [0, 3] and row["changed_polygon_ids"] == [0, 1]
    assert row["outside_resolved_vertex_ids"] == [3] and row["outside_resolved_polygon_ids"] == [1]
    assert row["max_vertex_displacement_mm"] == 4.
    assert "ok" not in report and report["region_resolution"] == "resolved"


def test_polygon_boundary_movement_reports_unmarked_incident_polygon():
    before = source_snapshot()
    before["objects"][0]["polygons"][1] = [0, 4, 5]
    after = copy.deepcopy(before)
    move(after)
    row = measure(before, after)["objects"][0]["geometry"]
    assert row["outside_resolved_vertex_ids"] == []
    assert row["outside_resolved_polygon_ids"] == [1] and row["outside_item_region"] is True


@pytest.mark.parametrize("category,key,value", [
    ("materials", "material_definitions", [{"parameters": {"roughness": 0.8}}]),
    ("materials", "polygon_material", [1, 1]),
    ("uvs", "uv_layers", [{"name": "UV", "uv": [[0.5, 0.5]] * 6}]),
    ("transforms", "parent_name", "DifferentParent"),
])
def test_each_non_geometry_category_is_measured_separately(category, key, value):
    before, after = source_snapshot(), source_snapshot()
    after["objects"][0][key] = value
    row = measure(before, after)["objects"][0]
    assert row[category]["changed"] and row[category]["changed_fields"] == [key]
    assert row[category]["source_sha256"] != row[category]["delivered_sha256"]
    assert row["geometry"]["changed"] is False


def test_object_names_keep_identity_when_snapshot_ordinals_change_and_additions_are_reported():
    before, after = source_snapshot(), source_snapshot()
    after["objects"][0]["id"] = "O0002"
    added = copy.deepcopy(after["objects"][0])
    added.update(id="O0001", name="Added")
    after["objects"].insert(0, added)
    report = measure(before, after)
    by_name = {row["name"]: row for row in report["objects"]}
    assert by_name["Front"]["changed"] is False
    assert by_name["Front"]["source_object_id"] == "O0001" and by_name["Front"]["delivered_object_id"] == "O0002"
    assert by_name["Added"]["status"] == "added"
    assert by_name["Added"]["geometry"]["outside_item_region"] is None
    assert by_name["Added"]["geometry"]["correspondence"] == "new_object"
    removed = measure(after, before, [])
    assert removed["objects"][0]["status"] == "removed"


def test_duplicate_names_unresolved_and_no_invented_identity():
    before, after = source_snapshot(), source_snapshot()
    after["objects"].append(copy.deepcopy(after["objects"][0]))
    report = measure(before, after)
    assert report["status"] == "partially_resolved" and report["objects"][0]["status"] == "unresolved"
    assert report["summary"]["geometry_outside_item_region"] is None


def test_unresolved_mark_never_becomes_false_confinement_or_expands_to_candidates():
    before, after = source_snapshot(), source_snapshot()
    move(after, 3)
    resolved = marks(status="unresolved")
    report = measure(before, after, resolved)
    row = report["objects"][0]["geometry"]
    assert row["outside_resolved_polygon_ids"] == [1]
    assert row["outside_item_region"] is None and report["summary"]["geometry_outside_item_region"] is None
    assert report["region_resolution"] == "unresolved_or_partial"
    assert measure(before, after, [])["summary"]["geometry_outside_item_region"] is None


def test_behind_lens_polygons_count_but_other_items_do_not():
    before, after = source_snapshot(), source_snapshot()
    move(after, 3)
    selected = marks()
    selected[0]["behind_polygons"] = [{"object_id": "O0001", "object_name": "Front", "polygon_ids": [1]}]
    assert measure(before, after, selected)["summary"]["geometry_outside_item_region"] is False
    selected[0]["item_id"] = "I2"
    assert measure(before, after, selected)["region_resolution"] == "unresolved_or_partial"


def test_topology_changes_keep_exact_survivors_and_unresolved_new_geometry():
    before, after = source_snapshot(), source_snapshot()
    obj = after["objects"][0]
    obj["vertices_world_mm"].append([1., 1., 0.])
    obj["vertices_local"].append([1., 1., 0.])
    obj["polygons"] = [[0, 1, 6], [1, 2, 6], [2, 0, 6], [3, 4, 5]]
    row = measure(before, after)["objects"][0]["geometry"]
    assert row["status"] == "partially_resolved" and row["changed_source_polygon_ids"] == [0]
    assert row["added_or_changed_polygon_count"] == 3
    assert row["outside_resolved_polygon_ids"] == [] and row["outside_item_region"] is None
    obj["polygons"].pop()
    assert measure(before, after)["objects"][0]["geometry"]["outside_item_region"] is True


def test_shading_and_evaluated_geometry_changes_are_not_lost():
    before, after = source_snapshot(), source_snapshot()
    after["objects"][0]["corner_normals"][3] = [0., 1., 0.]
    assert measure(before, after)["objects"][0]["geometry"]["outside_resolved_polygon_ids"] == [1]
    after = source_snapshot()
    after["objects"][0]["evaluated"]["vertices_world_mm"][3][2] = 5.
    assert measure(before, after)["objects"][0]["geometry"]["outside_resolved_polygon_ids"] == [1]
    after["objects"][0]["evaluated"]["topology_matches_source"] = False
    assert measure(before, after)["objects"][0]["geometry"]["outside_item_region"] is None


def test_shared_material_resources_are_reported_with_explicit_attribution_limit():
    before, after = source_snapshot(), source_snapshot()
    after["scene_state"] = {"images": {"Print": "new-bytes"}, "node_groups": {"Shader": "new-graph"}}
    report = measure(before, after)
    assert report["shared_material_resource_changes"] == {"images": ["Print"], "node_groups": ["Shader"]}
    assert report["objects"][0]["materials"]["dependency_resolution"] == "unresolved"
    assert report["status"] == "partially_resolved"


def bind_fake_fallback(root, monkeypatch, *, fingerprint="f" * 64):
    baseline, after = source_snapshot(), source_snapshot()
    move(after, 3, 2.)
    after["fingerprint"] = fingerprint
    path = root / "inputs/fallback-source-snapshot.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(D._json_bytes(baseline))
    (root / "inputs/full-edit-item.json").write_text("{}", encoding="utf-8")
    context = {"item_id": "I1", "source": {"fingerprint": baseline["fingerprint"], "export_sha256": "a" * 64},
               "baseline_snapshot": "inputs/fallback-source-snapshot.json", "understanding_sha256": "u" * 64,
               "immutable_files": {"inputs/fallback-source-snapshot.json": digest(path)},
               "owner_mark_resolutions": marks()}
    monkeypatch.setattr(owner_fallback, "load", lambda root: context)
    called = []

    def snapshot(root, checkpoint, fingerprint):
        called.append((checkpoint, fingerprint))
        assert fingerprint == after["fingerprint"]
        return copy.deepcopy(after)
    monkeypatch.setattr(D, "read_checkpoint_snapshot", snapshot)
    return context, called


def test_finished_delivery_seals_origin_item_side_effects_and_reopens_without_live_source(tmp_path, monkeypatch):
    scene, result = make_finished(tmp_path)
    _, calls = bind_fake_fallback(tmp_path, monkeypatch)
    out = D.deliver_finished(tmp_path, scene, result)
    assert out["origin"] == "full_edit_item" and out["item_id"] == "I1"
    assert out["side_effects"]["summary"]["geometry_outside_item_region"] is True
    assert out["finished"] is True and out["report_data"]["accepted_finish"] is True
    assert calls == [("c0001", "f" * 64)]
    manifest = json.loads(Path(out["manifest"]).read_text(encoding="utf-8"))
    assert manifest["origin"] == "full_edit_item" and manifest["item_id"] == "I1"
    assert {"side-effects/report.json", "side-effects/source-snapshot.json", "side-effects/delivered-snapshot.json",
            "side-effects/marks.json"} <= manifest["files"].keys()
    assert Path(out["side_effects_report"]["path"]).is_file()
    monkeypatch.setattr(owner_fallback, "load", lambda root: pytest.fail("delivery must be self contained"))
    assert D.load_delivery(tmp_path)["side_effects"] == out["side_effects"]


def test_draft_measures_selected_best_not_exit_head_and_keeps_draft_status(tmp_path, monkeypatch):
    scene, result, _, _ = draft_fixture(tmp_path, monkeypatch, best=True)
    _, calls = bind_fake_fallback(tmp_path, monkeypatch, fingerprint="0" * 64)
    out = D.deliver_draft(tmp_path, scene, result)
    assert out["status"] == "delivered" and out["finished"] is False
    assert out["origin"] == "full_edit_item" and out["item_id"] == "I1"
    assert out["side_effects"]["summary"]["geometry_outside_item_region"] is True
    assert calls == [("c0000", "0" * 64)] and out["report_data"]["exit_checkpoint"]["id"] == "c0001"


def test_measurement_failure_reports_unavailable_without_blocking_valid_delivery(tmp_path, monkeypatch):
    scene, result = make_finished(tmp_path)
    bind_fake_fallback(tmp_path, monkeypatch)

    def fail(*args):
        raise RuntimeError("snapshot unavailable")
    monkeypatch.setattr(D, "read_checkpoint_snapshot", fail)
    out = D.deliver_finished(tmp_path, scene, result)
    assert out["status"] == "delivered"
    assert out["side_effects"]["status"] == "unavailable"
    assert out["side_effects"]["summary"]["geometry_outside_item_region"] is None
    assert "snapshot unavailable" in out["side_effects"]["reason"]


@pytest.mark.parametrize("failure", ["binding", "source_snapshot", "invalid_export", "missing_mode_binding", "item_identity"])
def test_existing_and_immutable_source_refusals_stay_hard(tmp_path, monkeypatch, failure):
    scene, result = make_finished(tmp_path)
    bind_fake_fallback(tmp_path, monkeypatch)
    if failure == "binding":
        def stale(root):
            raise ValueError("immutable input changed")
        monkeypatch.setattr(owner_fallback, "load", stale)
    elif failure == "source_snapshot":
        (tmp_path / "inputs/fallback-source-snapshot.json").write_text("{}")
    elif failure == "invalid_export":
        scene.finish["export"]["validate_spec"]["ok"] = False
    elif failure == "item_identity":
        result.update(mode="full_edit_item", item_id="I2")
    else:
        (tmp_path / "inputs/full-edit-item.json").unlink()
        result["mode"] = "full_edit_item"
        monkeypatch.setattr(owner_fallback, "load", lambda root: None)
    with pytest.raises((D.DeliveryError, ValueError)):
        D.deliver_finished(tmp_path, scene, result)
    assert not (tmp_path / "deliveries/current.json").exists()


def test_side_effect_snapshot_tampering_is_refused_on_delivery_read(tmp_path, monkeypatch):
    scene, result = make_finished(tmp_path)
    bind_fake_fallback(tmp_path, monkeypatch)
    out = D.deliver_finished(tmp_path, scene, result)
    evidence = Path(out["manifest"]).parent / "side-effects/delivered-snapshot.json"
    evidence.write_text("{}", encoding="utf-8")
    with pytest.raises(D.DeliveryError, match="hash changed"):
        D.load_delivery(tmp_path)


@pytest.mark.parametrize("failure", [None, "fingerprint", "changed", "snapshot_hash"])
def test_checkpoint_snapshot_reopens_copy_checks_binding_and_always_closes(tmp_path, monkeypatch, failure):
    index = CheckpointIndex(tmp_path / "checkpoints")
    index.dir.mkdir(parents=True)
    blend = index.dir / "c0001.blend"
    blend.write_bytes(b"immutable checkpoint")
    before = source_snapshot()
    index.append({"id": "c0001", "kind": "save", "file": blend.name, "sha256": digest(blend),
                  "fingerprint": before["fingerprint"], "parent": None})
    original = {p.name: p.read_bytes() for p in index.dir.iterdir() if p.is_file()}
    workers = []

    class Worker:
        def __init__(self, root, *, start_blend, **kwargs):
            self.source, self.closed = Path(start_blend), False
            assert self.source != blend and self.source.read_bytes() == blend.read_bytes()
            workers.append(self)

        def start(self):
            self.hello = {"fingerprint": "bad" if failure == "fingerprint" else before["fingerprint"]}

        def call(self, name, args, **kwargs):
            assert name == "model_map_snapshot"
            path = Path(args["path"])
            path.write_bytes(D._json_bytes(before))
            return {"status": "ok", "unchanged": failure != "changed",
                    "fingerprint_before": before["fingerprint"], "fingerprint_after": before["fingerprint"],
                    "sha256": "0" * 64 if failure == "snapshot_hash" else digest(path)}

        def close(self):
            self.closed = True

    monkeypatch.setattr(worker_client, "WorkerClient", Worker)
    if failure:
        with pytest.raises(D.DeliveryError):
            D.read_checkpoint_snapshot(tmp_path, "c0001", before["fingerprint"])
    else:
        assert D.read_checkpoint_snapshot(tmp_path, "c0001", before["fingerprint"]) == before
    assert len(workers) == 1 and workers[0].closed
    assert {p.name: p.read_bytes() for p in index.dir.iterdir() if p.is_file()} == original
