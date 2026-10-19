"""Independent choice-input and exact-result controls; no worker or provider."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import shutil
import stat
from types import SimpleNamespace

from PIL import Image
import pytest
import numpy as np

from glasses3d.agent import edits, owner_apply, owner_choices
from glasses3d.tests.test_owner_mesh import box, snapshot
from glasses3d.tests import glbfab


def scene_snapshot():
    first = box()
    first["uv_layers"] = [{"name": "Print", "uv": [[0.0, 0.0] for _ in range(24)], "active_render": True}]
    first["material_definitions"] = [{"name": "Frame", "roughness": 0.3}]
    second = copy.deepcopy(first)
    second.update(id="O0002", name="Other test part", mesh_name="Other mesh")
    source = snapshot(first)
    source["objects"].append(second)
    source["fingerprint"] = "a" * 64
    return source


def pick(source, item_id, *, oid="O0001", vertex=1, amount=0.25, normal=None, uv=None):
    result = copy.deepcopy(source)
    obj = next(row for row in result["objects"] if row["id"] == oid)
    for key in ("vertices_local", "vertices_world_mm"):
        obj[key][vertex][0] += amount
    if normal is not None:
        obj["corner_normals"][normal] = [0.0, 1.0, 0.0]
    if uv is not None:
        obj["uv_layers"][0]["uv"][uv] = [0.25, 0.5]
    result["fingerprint"] = ("b" if item_id == "I1" else "c") * 64
    operation = {"id": item_id + "-move", "kind": "translate_region", "target_ids": [oid],
                 "scope": {"object_id": oid, "vertex_weights": [{"vertex_id": vertex, "weight": 1.0}],
                           "affected_polygon_ids": [], "normal_affected_polygon_ids": []},
                 "params": {"delta_mm": [amount, 0.0, 0.0]}}
    return {"item_id": item_id, "option_id": "A", "amount_text": f"{amount} mm outward",
            "operations": [operation], "snapshot": result,
            "export": {"sha256": ("d" if item_id == "I1" else "e") * 64,
                       "fingerprint": result["fingerprint"]}}


def combine_disjoint(source, *picks):
    result = copy.deepcopy(source)
    for row in picks:
        for original, expected, current in zip(source["objects"], row["snapshot"]["objects"], result["objects"]):
            for key in ("vertices_local", "vertices_world_mm", "corner_normals"):
                for index, value in enumerate(expected[key]):
                    if value != original[key][index]:
                        current[key][index] = copy.deepcopy(value)
            for index, value in enumerate(expected["uv_layers"][0]["uv"]):
                if value != original["uv_layers"][0]["uv"][index]:
                    current["uv_layers"][0]["uv"][index] = copy.deepcopy(value)
    result["fingerprint"] = "f" * 64
    return result


def test_single_pick_requires_both_preview_glb_bytes_and_scene_fingerprint():
    source = scene_snapshot()
    selected = pick(source, "I1")
    before = copy.deepcopy((source, selected))
    result = owner_apply.verify_choice_exactness(source, [selected], selected["snapshot"], selected["export"]["sha256"])
    assert result["ok"] and result["mode"] == "single_glb_bytes"
    assert result["single_pick_glb_equal"] is True and result["overlaps"] == []
    assert result["items"][0]["exact_preview_geometry"] is True
    for wrong_sha, wrong_fp in (("0" * 64, None), (selected["export"]["sha256"], "0" * 64)):
        current = copy.deepcopy(selected["snapshot"])
        if wrong_fp:
            current["fingerprint"] = wrong_fp
        with pytest.raises(ValueError, match="bytes or scene fingerprint"):
            owner_apply.verify_choice_exactness(source, [selected], current, wrong_sha)
    assert (source, selected) == before


def test_single_pick_rejects_snapshot_and_export_from_different_preview_states():
    source = scene_snapshot()
    selected = pick(source, "I1")
    selected["export"]["fingerprint"] = "0" * 64
    with pytest.raises(ValueError, match="scene fingerprint"):
        owner_apply.verify_choice_exactness(source, [selected], selected["snapshot"], selected["export"]["sha256"])


@pytest.mark.parametrize("same_object", [False, True])
def test_disjoint_picks_reproduce_each_preview_exactly_even_on_one_object(same_object):
    source = scene_snapshot()
    first = pick(source, "I1", vertex=1, normal=1, uv=1)
    second = pick(source, "I2", oid="O0001" if same_object else "O0002", vertex=6, normal=12, uv=12)
    result = owner_apply.verify_choice_exactness(source, [first, second], combine_disjoint(source, first, second), "f" * 64)
    assert result["mode"] == "per_item_geometry" and result["single_pick_glb_equal"] is None
    assert result["overlaps"] == []
    assert all(row["exact_preview_geometry"] and row["exclusive_values_checked"] > 0 for row in result["items"])


@pytest.mark.parametrize("damage", ["vertex", "normal", "uv", "material", "unselected_vertex", "topology", "origin", "objects"])
def test_inexact_disjoint_geometry_or_unrequested_state_refuses(damage):
    source = scene_snapshot()
    first, second = pick(source, "I1", normal=1, uv=1), pick(source, "I2", oid="O0002")
    current = combine_disjoint(source, first, second)
    obj = current["objects"][0]
    if damage in {"vertex", "unselected_vertex"}:
        index = 1 if damage == "vertex" else 7
        obj["vertices_local"][index][0] += 1e-10
    elif damage == "normal":
        obj["corner_normals"][1][0] += 1e-10
    elif damage == "uv":
        obj["uv_layers"][0]["uv"][1][0] += 1e-10
    elif damage == "material":
        obj["material_definitions"][0]["roughness"] = 0.4
    elif damage == "topology":
        obj["polygons"][0] = obj["polygons"][0][::-1]
    elif damage == "origin":
        current["origin_mm"][0] = 1
    else:
        current["objects"].pop()
    with pytest.raises(ValueError):
        owner_apply.verify_choice_exactness(source, [first, second], current, "f" * 64)


def test_overlap_names_the_items_and_object_and_still_checks_exclusive_regions():
    source = scene_snapshot()
    first = pick(source, "I1", vertex=1, normal=1)
    second = pick(source, "I2", vertex=1, amount=0.5, uv=12)
    current = combine_disjoint(source, first, second)
    for key in ("vertices_local", "vertices_world_mm"):
        current["objects"][0][key][1][0] = source["objects"][0][key][1][0] + 0.75
    result = owner_apply.verify_choice_exactness(source, [first, second], current, "f" * 64)
    assert result["overlaps"] == [{"item_ids": ["I1", "I2"],
        "objects": [{"object_id": "O0001", "object": "Closed test part"}]}]
    assert all(row["overlap_reported"] and not row["exact_preview_geometry"] for row in result["items"])
    current["objects"][0]["uv_layers"][0]["uv"][12][0] += 0.1
    with pytest.raises(ValueError, match="I2"):
        owner_apply.verify_choice_exactness(source, [first, second], current, "f" * 64)


def test_same_normal_ring_and_following_markings_are_reported_as_overlap():
    source = scene_snapshot()
    first, second = pick(source, "I1", vertex=1), pick(source, "I2", vertex=6)
    for row in (first, second):
        row["operations"][0]["scope"]["normal_affected_polygon_ids"] = [0]
        row["operations"][0]["marking_attachments"] = [{"object_id": "O0002"}]
    result = owner_apply.verify_choice_exactness(source, [first, second], combine_disjoint(source, first, second), "f" * 64)
    assert [obj["object_id"] for obj in result["overlaps"][0]["objects"]] == ["O0001", "O0002"]


def test_scalar_or_topology_changes_claim_whole_object_but_keep_other_objects_exact():
    source = scene_snapshot()
    first, second = pick(source, "I1"), pick(source, "I2", vertex=6)
    first["snapshot"]["objects"][0]["material_definitions"][0]["roughness"] = 0.6
    current = combine_disjoint(source, first, second)
    current["objects"][0]["material_definitions"][0]["roughness"] = 0.6
    result = owner_apply.verify_choice_exactness(source, [first, second], current, "f" * 64)
    assert result["overlaps"][0]["item_ids"] == ["I1", "I2"]
    current["objects"][1]["material_definitions"][0]["roughness"] = 0.4
    with pytest.raises(ValueError, match="unrequested source"):
        owner_apply.verify_choice_exactness(source, [first, second], current, "f" * 64)


def test_derived_snapshot_summaries_do_not_create_false_geometry_differences():
    source = scene_snapshot()
    first, second = pick(source, "I1"), pick(source, "I2", oid="O0002")
    current = combine_disjoint(source, first, second)
    current["objects"][0].update(positions_sha256="recomputed", topology_sha256="recomputed", evaluated={"new_summary": True})
    assert owner_apply.verify_choice_exactness(source, [first, second], current, "f" * 64)["ok"]


def test_disjoint_normal_intent_witnesses_and_hashes_do_not_hide_exact_corner_checks():
    source = scene_snapshot()
    source["objects"][0]["attributes"] = [{"name": "g3d_owner_normal_intent", "domain": "CORNER",
        "data_type": "FLOAT_VECTOR", "values": copy.deepcopy(source["objects"][0]["corner_normals"])}]
    first = pick(source, "I1", vertex=1, normal=1)
    second = pick(source, "I2", vertex=6, normal=12)
    current = combine_disjoint(source, first, second)
    for index, row in enumerate((first["snapshot"], second["snapshot"], current)):
        obj = row["objects"][0]
        obj["attributes"][0]["values"] = copy.deepcopy(obj["corner_normals"])
        obj["normal_intent"] = {"intent_normals": copy.deepcopy(obj["corner_normals"]),
            "encoded_matches": True, "normal_space_matches": True, "decoded_within_quantization": True,
            "max_vector_error": index * 1e-5}
        obj["other_attributes_sha256"] = str(index) * 64
    result = owner_apply.verify_choice_exactness(source, [first, second], current, "f" * 64)
    assert result["overlaps"] == []
    assert all(row["exact_preview_geometry"] for row in result["items"])
    current["objects"][0]["attributes"][0]["values"][1][0] += 1e-10
    with pytest.raises(ValueError, match="attributes"):
        owner_apply.verify_choice_exactness(source, [first, second], current, "f" * 64)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def sealed_choices(tmp_path, monkeypatch):
    """Sealing is upstream; filesystem hashing, display and selection stay real here."""
    root = tmp_path / "understand"
    source = scene_snapshot()
    selections = [pick(source, "I1", normal=1, uv=1), pick(source, "I2", oid="O0002")]
    evidence, loaded, captures, items = {}, [], [], []
    source_export = root / "work/previews/source.glb"
    source_export.parent.mkdir(parents=True)
    source_export.write_bytes(b"sealed source export bytes")
    evidence[source_export.relative_to(root).as_posix()] = edits.sha(source_export)
    source_snapshot = root / "work/maps/source.json"
    write_json(source_snapshot, source)
    evidence[source_snapshot.relative_to(root).as_posix()] = edits.sha(source_snapshot)
    for number, selected in enumerate(selections, 1):
        item_id = selected["item_id"]
        option_rows = []
        for key, label, amount in (("A", "Small", "0.25 mm outward"), (item_id + "_B", "Large", "0.50 mm outward")):
            image = root / "work/previews" / f"{item_id}-{key}-999mm.png"
            image.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (90, 30), (number * 40, 80, 100)).save(image)
            evidence[image.relative_to(root).as_posix()] = edits.sha(image)
            snapshot_path = image.with_suffix(".json")
            write_json(snapshot_path, selected["snapshot"])
            evidence[snapshot_path.relative_to(root).as_posix()] = edits.sha(snapshot_path)
            option_rows.append({"id": key, "item_id": item_id, "label": label, "amount_text": amount,
                "operations": selected["operations"], "export": selected["export"],
                "after_snapshot": {"path": str(snapshot_path), "sha256": edits.sha(snapshot_path)},
                "full_pairs": [{"path": str(image), "sha256": edits.sha(image), "view": "front"}],
                "target_details": []})
        item = {"item_id": item_id, "number": number, "interpretation": "Move the marked region outward.",
                "disposition": "previewed", "reason": "", "amount_preview": {"options": [
                    {k: row[k] for k in ("id", "item_id", "label", "amount_text", "operations")}
                    for row in option_rows]}}
        items.append(item)
        loaded.append((item, {"options": option_rows,
                              "before_export": {"path": str(source_export), "sha256": edits.sha(source_export)}}))
    blocked = {"item_id": "I3", "number": 3, "interpretation": "The hidden edge needs another view.",
               "disposition": "needs_clarification", "reason": "The mark is on background."}
    items.append(blocked)
    loaded.append((blocked, None))
    for item in items:
        annotated = root / "inputs" / (item["item_id"] + ".png")
        annotated.parent.mkdir(exist_ok=True)
        Image.new("RGB", (16, 9), "gray").save(annotated)
        captures.append({"item_id": item["item_id"], "labels": ["Label-" + item["item_id"]],
                         "request_text": "  Owner words for " + item["item_id"] + ".\r\n ",
                         "annotated_path": annotated, "annotated_sha256": edits.sha(annotated),
                         "payload": {"source": {"delivery_key": "source-delivery-key"}}})
    map_receipt = root / "work/maps/capture.json"
    map_image = root / next(name for name in evidence if name.endswith(".png"))
    write_json(map_receipt, {"views": [{"ref": "source-map", "full_pair": str(map_image)}]})
    evidence[map_receipt.relative_to(root).as_posix()] = edits.sha(map_receipt)
    understanding = {"schema": edits.UNDERSTANDING_SCHEMA, "items": items, "evidence_files": evidence,
                     "snapshot_path": source_snapshot.relative_to(root).as_posix(),
                     "capture_receipts": [map_receipt.relative_to(root).as_posix()],
                     "limitations": ["The background mark remains unresolved."]}
    artifact = root / "work/owner_edit/understanding.json"
    write_json(artifact, understanding)
    write_json(artifact.with_name("understanding-usage.json"), {"settled_usd": "1.234567", "requests": 7})
    doc = {"phase": "understand", "edit_id": "test-edit", "request_text": "Owner's original session words.",
           "owner_capture_items": captures,
           "source": {"delivery_id": "source-delivery", "export_sha256": "9" * 64,
                      "fingerprint": source["fingerprint"], "basis": "basis-001"}}
    monkeypatch.setattr(owner_choices, "_load_sealed", lambda path: copy.deepcopy((doc, understanding, loaded)))
    from glasses3d.agent import owner_captures, job_run
    monkeypatch.setattr(owner_captures, "load_round_captures", lambda *args: copy.deepcopy(captures))
    def paid(*args, **kwargs):
        raise AssertionError("Local owner picks reached a model-run entrypoint")
    monkeypatch.setattr(job_run, "prepare_run", paid)
    monkeypatch.setattr(job_run, "execute_run", paid)
    return SimpleNamespace(root=root, artifact=artifact, sha=edits.sha(artifact), doc=doc,
                           source=source, source_snapshot=source_snapshot,
                           understanding=understanding, loaded=loaded, captures=captures,
                           choices=[{"item_id": "I2", "option_id": "A"}, {"item_id": "I1", "option_id": "A"}],
                           skipped=[{"item_id": "I3", "note": "  Need a better view.\r\n"}])


def test_read_dto_preserves_item_words_sealed_amounts_and_photo_first_images(sealed_choices):
    h = sealed_choices
    before = edits.tree_hashes(h.root)
    result = owner_choices.read_understanding(h.root)
    assert result["schema"] == "g3d.dev_understanding/1"
    assert result["understanding_sha256"] == h.sha
    assert result["run_settled_usd"] == "1.234567"
    assert result["source"] == {"delivery_key": "source-delivery-key", "glb_sha256": "9" * 64}
    assert [row["request_text"] for row in result["items"]] == [row["request_text"] for row in h.captures]
    assert [row["fallback_allowed"] for row in result["items"]] == [False, False, True]
    assert result["items"][2]["options"] == [] and result["items"][2]["reason"] == "The mark is on background."
    assert result["items"][0]["options"][0]["amount_text"] == "0.25 mm outward"
    for row in result["items"][:2]:
        for option in row["options"]:
            image = option["images"][0]
            assert image["panels"] == ["photo", "before", "option"]
            assert Path(image["path"]).is_relative_to(h.root)
            assert edits.sha(image["path"]) == image["sha256"]
    assert result["map_images"][0]["label"] == "source-map"
    assert result["limitations"] == h.understanding["limitations"]
    assert edits.tree_hashes(h.root) == before


def test_changed_display_image_refuses_instead_of_showing_unsealed_pixels(sealed_choices):
    h = sealed_choices
    Path(h.loaded[0][1]["options"][0]["full_pairs"][0]["path"]).write_bytes(b"changed")
    with pytest.raises(ValueError, match="image changed"):
        owner_choices.read_understanding(h.root)


def test_selection_uses_original_order_and_same_option_id_stays_item_bound(sealed_choices):
    h = sealed_choices
    original = copy.deepcopy((h.understanding, h.choices, h.skipped))
    chosen, skipped = owner_choices._selection(h.understanding, h.choices, h.skipped)
    assert [row["item_id"] for row in chosen] == ["I1", "I2"]
    assert [row["option"]["operations"][0]["id"] for row in chosen] == ["I1-move", "I2-move"]
    assert [row["amount_text"] for row in chosen] == ["0.25 mm outward"] * 2
    assert skipped == h.skipped
    assert (h.understanding, h.choices, h.skipped) == original


@pytest.mark.parametrize("damage", ["operations", "amount", "unknown_item", "unknown_option", "wrong_item_option",
    "duplicate_choice", "blocked_choice", "missing_item", "duplicate_skip", "picked_and_skipped",
    "empty_note", "unknown_skip", "skip_extra", "numeric_id", "choices_object", "skipped_object", "all_skipped"])
def test_browser_cannot_add_authority_or_omit_an_item(sealed_choices, damage):
    h = sealed_choices
    choices, skipped = copy.deepcopy((h.choices, h.skipped))
    if damage == "operations":
        choices[0]["operations"] = [{"kind": "translate_region", "delta_mm": [100, 0, 0]}]
    elif damage == "amount":
        choices[0]["amount_text"] = "100 mm"
    elif damage == "unknown_item":
        choices[0]["item_id"] = "foreign"
    elif damage == "unknown_option":
        choices[0]["option_id"] = "unsealed"
    elif damage == "wrong_item_option":
        choices[0]["option_id"] = "I1_B"
    elif damage == "duplicate_choice":
        choices.append(copy.deepcopy(choices[0]))
    elif damage == "blocked_choice":
        choices.append({"item_id": "I3", "option_id": "A"})
    elif damage == "missing_item":
        choices.pop()
    elif damage == "duplicate_skip":
        skipped.append(copy.deepcopy(skipped[0]))
    elif damage == "picked_and_skipped":
        skipped.append({"item_id": "I1", "note": "No"})
    elif damage == "empty_note":
        skipped[0]["note"] = " \r\n "
    elif damage == "unknown_skip":
        skipped[0]["item_id"] = "foreign"
    elif damage == "skip_extra":
        skipped[0]["operations"] = []
    elif damage == "numeric_id":
        choices[0]["option_id"] = 1
    elif damage == "choices_object":
        choices = {"I1": "A"}
    elif damage == "skipped_object":
        skipped = {"I3": "No"}
    else:
        choices = []
        skipped = [{"item_id": item["item_id"], "note": "No changes"} for item in h.understanding["items"]]
    with pytest.raises(ValueError):
        owner_choices._selection(h.understanding, choices, skipped)


def test_apply_passes_only_sealed_operations_in_item_order_and_keeps_source_bytes(sealed_choices, monkeypatch, tmp_path):
    h = sealed_choices
    before = edits.tree_hashes(h.root)
    output = tmp_path / "applied"
    received = {}
    def apply(root, source_root, doc, understanding, selected, skipped, confirmation):
        received.update(root=root, source_root=source_root, selected=selected, skipped=skipped, confirmation=confirmation)
        assert root.is_dir() and not any(root.iterdir())
        return root
    monkeypatch.setattr(owner_choices, "_run_apply", apply)
    result = owner_choices.apply_choices(h.root, h.sha, h.choices, h.skipped, "  Apply my picks.\r\n", destination=output)
    assert result == output
    assert received["confirmation"] == "  Apply my picks.\r\n"
    assert [row["item_id"] for row in received["selected"]] == ["I1", "I2"]
    assert [row["operations"][0]["id"] for row in received["selected"]] == ["I1-move", "I2-move"]
    assert [row["snapshot"] for row in received["selected"]] == [
        edits.read(preview["options"][0]["after_snapshot"]["path"]) for _, preview in h.loaded[:2]]
    assert received["skipped"] == h.skipped
    assert edits.tree_hashes(h.root) == before


def _reseal_choice_test(h):
    write_json(h.source_snapshot, h.source)
    h.understanding['evidence_files'][h.understanding['snapshot_path']] = edits.sha(h.source_snapshot)
    write_json(h.artifact, h.understanding)
    h.sha = edits.sha(h.artifact)


def _sealed_operation(h, index, kind, *, oid='O0001', change_normal=False, mutate=None):
    """Replace upstream-validated plans in this boundary-only fixture."""
    item_id = f'I{index + 1}'
    selected = pick(h.source, item_id, oid=oid, vertex=1 if index == 0 else 6,
                    normal=(1 if index == 0 else 12) if change_normal else None)
    operation = selected['operations'][0]
    operation['kind'] = kind
    if mutate is not None:
        mutate(selected)
    for option in h.loaded[index][1]['options']:
        option['operations'][:] = copy.deepcopy(selected['operations'])
        path = Path(option['after_snapshot']['path'])
        write_json(path, selected['snapshot'])
        option['after_snapshot']['sha256'] = edits.sha(path)
        h.understanding['evidence_files'][path.relative_to(h.root).as_posix()] = edits.sha(path)
    _reseal_choice_test(h)


SHADING_CONFLICT = ('Items I1 and I2 both change the shading of the same part, which cannot be combined exactly yet. '
    'Pick an option for one of them now (choose None of these for the other) and send the other again as a new edit.')
SURFACE_NORMAL_KINDS = ('flatten_face', 'repair_shading', 'refine_curved_surface', 'edge_shape', 'thickness',
    'scale_region', 'bend_region', 'transform_marking', 'mesh_cleanup', 'consistent_normals', 'symmetrize', 'controlled_decimate')


@pytest.mark.parametrize('kind', SURFACE_NORMAL_KINDS)
@pytest.mark.parametrize('authored', [False, True])
def test_same_part_surface_normal_items_refuse_before_copy_worker_requests_or_publication(sealed_choices, monkeypatch, tmp_path, kind, authored):
    from glasses3d.g3d import owner_surface
    assert set(SURFACE_NORMAL_KINDS) == owner_surface.OPERATION_KINDS - {'material_adjust'}
    h = sealed_choices
    obj = h.source['objects'][0]
    obj['has_custom_normals'] = authored
    obj['normal_attributes'] = ([{'name': 'custom_normal', 'domain': 'CORNER', 'data_type': 'INT16_2D',
        'field': 'value', 'values': [[0, 0] for _ in obj['corner_normals']]}] if authored else [])
    _sealed_operation(h, 0, kind)
    _sealed_operation(h, 1, 'repair_shading')
    calls = []
    def forbidden(*args, **kwargs):
        calls.append('native, publication or request')
        raise AssertionError('Conflicting shading reached a side effect')
    monkeypatch.setattr(owner_choices, '_run_apply', forbidden)
    monkeypatch.setattr(owner_choices.delivery, 'deliver_owner_applied', forbidden)
    from glasses3d.agent import job_run
    monkeypatch.setattr(job_run, 'prepare_run', forbidden)
    monkeypatch.setattr(job_run, 'execute_run', forbidden)
    before = edits.tree_hashes(h.root)
    output = tmp_path / 'conflicting-shading'
    with pytest.raises(ValueError) as error:
        owner_choices.apply_choices(h.root, h.sha, h.choices, h.skipped, 'Apply my picks.', output)
    assert str(error.value) == SHADING_CONFLICT
    assert calls == [] and not output.exists() and edits.tree_hashes(h.root) == before


@pytest.mark.parametrize('kind', ['translate_region', 'rotate_region', 'taper_region'])
@pytest.mark.parametrize('change', ['intended_normals', 'polygon_smooth', 'topology'])
def test_finite_edits_with_changed_sealed_normal_state_also_refuse(sealed_choices, monkeypatch, tmp_path, kind, change):
    h = sealed_choices
    def mutate(row):
        obj = row['snapshot']['objects'][0]
        if change == 'polygon_smooth':
            obj['polygon_smooth'] = [True for _ in obj['polygons']]
        elif change == 'topology':
            obj['polygons'][0].reverse()
    _sealed_operation(h, 0, kind, change_normal=change == 'intended_normals', mutate=mutate)
    _sealed_operation(h, 1, 'repair_shading')
    def forbidden(*args, **kwargs):
        raise AssertionError('Conflicting finite normal change reached the worker')
    monkeypatch.setattr(owner_choices, '_run_apply', forbidden)
    output = tmp_path / 'finite-shading'
    with pytest.raises(ValueError) as error:
        owner_choices.apply_choices(h.root, h.sha, h.choices, h.skipped, 'Apply my picks.', output)
    assert str(error.value) == SHADING_CONFLICT and not output.exists()


@pytest.mark.parametrize('case', ['single', 'two_operations_one_item', 'different_parts',
    'translate_region', 'rotate_region', 'taper_region', 'material_adjust'])
def test_nonconflicting_choices_keep_existing_apply_path(sealed_choices, monkeypatch, tmp_path, case):
    h = sealed_choices
    _sealed_operation(h, 0, 'repair_shading' if case in {'single', 'two_operations_one_item', 'different_parts'} else case)
    _sealed_operation(h, 1, 'repair_shading' if case == 'different_parts' else 'translate_region',
                      oid='O0002' if case == 'different_parts' else 'O0001')
    if case == 'two_operations_one_item':
        updated = set()
        for option in h.loaded[0][1]['options']:
            if id(option['operations']) in updated:
                continue
            updated.add(id(option['operations']))
            second = copy.deepcopy(option['operations'][0])
            second['id'] += '-second-mark'
            option['operations'].append(second)
        _reseal_choice_test(h)
    choices, skipped = h.choices, h.skipped
    if case == 'single':
        choices = [{'item_id': 'I1', 'option_id': 'A'}]
        skipped = [{'item_id': 'I2', 'note': 'Send this as a new edit.'}, *h.skipped]
    reached = []
    def apply(root, source_root, doc, understanding, selected, *args):
        reached.append(selected)
        return root
    monkeypatch.setattr(owner_choices, '_run_apply', apply)
    before = edits.tree_hashes(h.root)
    output = tmp_path / case
    assert owner_choices.apply_choices(h.root, h.sha, choices, skipped, 'Apply my picks.', output) == output
    assert len(reached) == 1 and edits.tree_hashes(h.root) == before
    assert [row['item_id'] for row in reached[0]] == (['I1'] if case == 'single' else ['I1', 'I2'])
    if case == 'two_operations_one_item':
        assert len(reached[0][0]['operations']) == 2


def test_finite_classification_uses_verified_intent_instead_of_quantized_decoding(sealed_choices, monkeypatch, tmp_path):
    h = sealed_choices
    def encoded(row):
        obj = row['snapshot']['objects'][0]
        obj['normal_intent'] = {'intent_normals': copy.deepcopy(h.source['objects'][0]['corner_normals']),
            'encoded_matches': True, 'normal_space_matches': True, 'decoded_within_quantization': True}
    _sealed_operation(h, 0, 'translate_region', change_normal=True, mutate=encoded)
    _sealed_operation(h, 1, 'repair_shading')
    reached = []
    monkeypatch.setattr(owner_choices, '_run_apply', lambda root, *args: reached.append(root) or root)
    output = tmp_path / 'unchanged-intent'
    assert owner_choices.apply_choices(h.root, h.sha, h.choices, h.skipped, 'Apply my picks.', output) == output
    assert reached == [output]


@pytest.mark.parametrize('changed', [False, True])
def test_only_actually_changed_attached_marking_normals_count(sealed_choices, monkeypatch, tmp_path, changed):
    h = sealed_choices
    def attached(row):
        row['operations'][0]['marking_attachments'] = [{'object_id': 'O0002', 'surface_object_id': 'O0001'}]
        if changed:
            row['snapshot']['objects'][1]['corner_normals'][1] = [0.0, 1.0, 0.0]
    _sealed_operation(h, 0, 'refine_curved_surface', mutate=attached)
    _sealed_operation(h, 1, 'transform_marking', oid='O0002')
    for option in h.loaded[1][1]['options']:
        option['operations'][0]['params']['surface_object_id'] = 'O0001'
    _reseal_choice_test(h)
    reached = []
    monkeypatch.setattr(owner_choices, '_run_apply', lambda root, *args: reached.append(root) or root)
    output = tmp_path / 'marking'
    if changed:
        with pytest.raises(ValueError) as error:
            owner_choices.apply_choices(h.root, h.sha, h.choices, h.skipped, 'Apply my picks.', output)
        assert str(error.value) == SHADING_CONFLICT and reached == [] and not output.exists()
    else:
        assert owner_choices.apply_choices(h.root, h.sha, h.choices, h.skipped, 'Apply my picks.', output) == output
        assert reached == [output]


@pytest.mark.parametrize('damage', ['bytes', 'fingerprint'])
def test_normal_classification_requires_authenticated_source_snapshot(sealed_choices, monkeypatch, tmp_path, damage):
    h = sealed_choices
    h.source['fingerprint'] = '0' * 64
    write_json(h.source_snapshot, h.source)
    if damage == 'fingerprint':
        h.understanding['evidence_files'][h.understanding['snapshot_path']] = edits.sha(h.source_snapshot)
    reached = []
    monkeypatch.setattr(owner_choices, '_run_apply', lambda *args: reached.append('worker'))
    output = tmp_path / 'source-tampered'
    with pytest.raises(ValueError, match='source snapshot'):
        owner_choices.apply_choices(h.root, h.sha, h.choices, h.skipped, 'Apply my picks.', output)
    assert reached == [] and not output.exists()


@pytest.mark.parametrize('damage', ['bytes', 'evidence_binding'])
def test_normal_classification_rechecks_the_exact_selected_preview_bytes(sealed_choices, monkeypatch, tmp_path, damage):
    h = sealed_choices
    _sealed_operation(h, 0, 'translate_region', change_normal=True)
    _sealed_operation(h, 1, 'repair_shading')
    binding = h.loaded[0][1]['options'][0]['after_snapshot']
    path = Path(binding['path'])
    if damage == 'bytes':
        changed = edits.read(path)
        changed['objects'][0]['corner_normals'] = copy.deepcopy(h.source['objects'][0]['corner_normals'])
        write_json(path, changed)
    else:
        h.understanding['evidence_files'][path.relative_to(h.root).as_posix()] = '0' * 64
    reached = []
    monkeypatch.setattr(owner_choices, '_run_apply', lambda *args: reached.append('worker'))
    output = tmp_path / 'preview-tampered'
    with pytest.raises(ValueError, match='selected preview snapshot'):
        owner_choices.apply_choices(h.root, h.sha, h.choices, h.skipped, 'Apply my picks.', output)
    assert reached == [] and not output.exists()


@pytest.mark.parametrize("failure", ["stale_sha", "empty_confirmation", "invalid_choice", "existing_destination",
                                      "nested_destination", "source_job_destination"])
def test_apply_validation_refuses_before_any_private_copy(sealed_choices, monkeypatch, tmp_path, failure):
    h = sealed_choices
    output, digest, confirmation = tmp_path / "apply", h.sha, "Apply my picks."
    choices = copy.deepcopy(h.choices)
    if failure == "stale_sha":
        digest = "0" * 64
    elif failure == "empty_confirmation":
        confirmation = " "
    elif failure == "invalid_choice":
        choices[0]["operations"] = []
    elif failure == "existing_destination":
        output.mkdir()
        (output / "keep.txt").write_text("Existing user data")
    elif failure == "nested_destination":
        output = h.root / "forbidden-child"
    else:
        original_job = tmp_path / "original-job"
        original_job.mkdir()
        (original_job / "original.txt").write_text("Owner's immutable delivery")
        h.doc["source"]["job"] = str(original_job)
        output = original_job / "forbidden-apply"
    before = edits.tree_hashes(tmp_path)
    def unexpected(*args):
        raise AssertionError("Invalid selection reached the apply worker")
    monkeypatch.setattr(owner_choices, "_run_apply", unexpected)
    with pytest.raises(ValueError):
        owner_choices.apply_choices(h.root, digest, choices, h.skipped, confirmation, output)
    assert edits.tree_hashes(tmp_path) == before


@pytest.mark.parametrize("error", [ValueError("A hard check failed"), RuntimeError("Export failed")])
def test_failed_apply_removes_private_partial_delivery_and_allows_retry(sealed_choices, monkeypatch, tmp_path, error):
    h = sealed_choices
    output = tmp_path / "applied"
    before = edits.tree_hashes(h.root)
    def fail(root, *args):
        write_json(root / "deliveries/.pending-test/report.json", {"partial": True})
        write_json(root / "work/owner_edit/applied-picks.json", {"partial": True})
        raise error
    monkeypatch.setattr(owner_choices, "_run_apply", fail)
    for _ in range(2):
        with pytest.raises(type(error), match=str(error)):
            owner_choices.apply_choices(h.root, h.sha, h.choices, h.skipped, "Apply my picks.", output)
        assert not output.exists()
        assert edits.tree_hashes(h.root) == before


def test_failed_apply_cleans_real_readonly_checkpoint_and_preserves_original_error(sealed_choices, monkeypatch, tmp_path):
    h = sealed_choices
    output = tmp_path / "readonly-apply"
    checkpoint = output / "checkpoints/c0000.blend"
    before = edits.tree_hashes(h.root)
    def fail(root, *args):
        checkpoint.parent.mkdir(parents=True)
        checkpoint.write_bytes(b"private immutable checkpoint, no Blender required")
        checkpoint.chmod(stat.S_IREAD)
        assert not checkpoint.stat().st_mode & stat.S_IWRITE
        write_json(root / "deliveries/.pending-test/report.json", {"partial": True})
        raise ValueError("Original apply validation failure")
    monkeypatch.setattr(owner_choices, "_run_apply", fail)
    try:
        with pytest.raises(ValueError, match="Original apply validation failure"):
            owner_choices.apply_choices(h.root, h.sha, h.choices, h.skipped, "Apply my picks.", output)
        assert not output.exists()
        assert edits.tree_hashes(h.root) == before
    finally:
        # Keep the failing-old Windows control from poisoning pytest teardown.
        if checkpoint.exists():
            checkpoint.chmod(stat.S_IREAD | stat.S_IWRITE)
        if output.exists():
            shutil.rmtree(output)


@pytest.mark.parametrize("outside", [False, True])
def test_cleanup_retry_never_chmods_outside_copy_or_retries_other_errors(tmp_path, monkeypatch, outside):
    private = tmp_path / "private"
    private.mkdir()
    target = (tmp_path if outside else private) / "untouched.blend"
    target.write_bytes(b"Must remain unchanged")
    before = (target.stat().st_mode, target.read_bytes())
    calls = []
    error = PermissionError("Read-only file") if outside else OSError("Unrelated cleanup fault")
    def remove(root, *, onexc):
        assert root == private
        onexc(lambda path: calls.append(path), str(target), error)
    monkeypatch.setattr(owner_choices.shutil, "rmtree", remove)
    with pytest.raises(ValueError if outside else OSError) as caught:
        owner_choices._remove_private_tree(private)
    if outside:
        assert "outside its private copy" in str(caught.value)
    else:
        assert caught.value is error
    assert calls == []
    assert (target.stat().st_mode, target.read_bytes()) == before


def delivery_record(*, several=False, overlap=False):
    source = scene_snapshot()
    first = pick(source, "I1")
    selections = [first]
    if several:
        selections.append(pick(source, "I2", oid="O0001" if overlap else "O0002"))
    current = combine_disjoint(source, *selections) if several else first["snapshot"]
    sha = "f" * 64 if several else first["export"]["sha256"]
    exactness = owner_apply.verify_choice_exactness(source, selections, current, sha)
    exactness["exported_geometry"] = {"ok": True, "comparison": "Exact exported triangle-corner attributes",
                                      "primitives_checked": 2, "overlaps": []}
    app = {"schema": "g3d.owner_applied_choices/1", "status": "applied", "origin": "applied_picks",
           "api_requests": 0, "settled_usd": "0.000000", "tool_test": False, "visual_acceptance": False,
           "export": {"sha256": sha, "fingerprint": current["fingerprint"]},
           "source_basis": {"basis": "basis-001", "basis_sha256": "1" * 64},
           "applied": [{k: row[k] for k in ("item_id", "option_id", "amount_text")} for row in selections],
           "skipped": [{"item_id": "I3", "note": "  Leave this one.\r\n"}],
           "exactness": exactness, "warnings": exactness["overlaps"]}
    app["notes"] = copy.deepcopy(app["skipped"])
    report = {"tool_test": False, "delivery_kind": "owner_applied", "finished": True,
              "accepted_finish": False, "run_status": "owner_applied", "glb_sha256": sha,
              "fingerprint": current["fingerprint"], "owner_applied": {"source_basis": app["source_basis"]}}
    sync_delivery_report(app, report)
    return app, report


def sync_delivery_report(app, report):
    for key in ("origin", "applied", "notes", "warnings", "exactness"):
        report[key] = copy.deepcopy(app[key])


@pytest.mark.parametrize("several,overlap", [(False, False), (True, False), (True, True)])
def test_delivery_record_accepts_truthful_exactness_and_retains_all_owner_notes(several, overlap):
    app, report = delivery_record(several=several, overlap=overlap)
    before = copy.deepcopy((app, report))
    assert owner_choices.validate_delivery_application(app, report) is True
    assert (app, report) == before
    assert app["notes"] == [{"item_id": "I3", "note": "  Leave this one.\r\n"}]
    assert app["exactness"]["single_pick_glb_equal"] is (None if several else True)


@pytest.mark.parametrize("field", ["origin", "applied", "notes", "warnings", "exactness"])
def test_delivery_record_rejects_report_provenance_not_matching_immutable_application(field):
    app, report = delivery_record()
    report[field] = "changed"
    with pytest.raises(ValueError, match="provenance"):
        owner_choices.validate_delivery_application(app, report)


@pytest.mark.parametrize("damage", ["requests", "boolean_requests", "cost", "visual_acceptance", "export_sha",
    "export_fingerprint", "source_basis", "duplicate_item", "extra_operation", "empty_amount", "missing_note",
    "exactness_false", "wrong_mode", "missing_exact_item", "single_false", "single_overlap",
    "false_disjoint_claim", "malformed_applied", "malformed_exact_item", "malformed_overlap",
    "missing_export_comparison", "failed_export_comparison"])
def test_delivery_validator_refuses_false_or_malformed_apply_receipts(damage):
    app, report = delivery_record()
    if damage == "requests":
        app["api_requests"] = 1
    elif damage == "boolean_requests":
        app["api_requests"] = False
    elif damage == "cost":
        app["settled_usd"] = "1.000000"
    elif damage == "visual_acceptance":
        app["visual_acceptance"] = True
    elif damage == "export_sha":
        app["export"]["sha256"] = "0" * 64
    elif damage == "export_fingerprint":
        app["export"]["fingerprint"] = "0" * 64
    elif damage == "source_basis":
        app["source_basis"] = {"basis": "wrong", "basis_sha256": "0" * 64}
    elif damage == "duplicate_item":
        app["skipped"][0]["item_id"] = "I1"
        app["notes"] = copy.deepcopy(app["skipped"])
    elif damage == "extra_operation":
        app["applied"][0]["operations"] = []
    elif damage == "empty_amount":
        app["applied"][0]["amount_text"] = " "
    elif damage == "missing_note":
        app["notes"] = []
    elif damage == "exactness_false":
        app["exactness"]["ok"] = False
    elif damage == "wrong_mode":
        app["exactness"]["mode"] = "per_item_geometry"
    elif damage == "missing_exact_item":
        app["exactness"]["items"] = []
    elif damage == "single_false":
        app["exactness"]["single_pick_glb_equal"] = False
    elif damage == "single_overlap":
        app["exactness"]["overlaps"] = [{"item_ids": ["I1", "I1"], "objects": [{"object_id": "O0001"}]}]
    elif damage == "false_disjoint_claim":
        app["exactness"]["items"][0]["exact_preview_geometry"] = False
    elif damage == "malformed_applied":
        app["applied"] = [42]
    elif damage == "malformed_exact_item":
        app["exactness"]["items"] = [42]
    elif damage == "malformed_overlap":
        app["exactness"]["overlaps"] = [42]
    elif damage == "missing_export_comparison":
        app["exactness"].pop("exported_geometry")
    else:
        app["exactness"]["exported_geometry"]["ok"] = False
    app["warnings"] = copy.deepcopy(app["exactness"]["overlaps"])
    sync_delivery_report(app, report)
    with pytest.raises(ValueError):
        owner_choices.validate_delivery_application(app, report)


@pytest.mark.parametrize("damage", ["note_in_applied", "choice_in_skipped", "mixed_applied_shapes",
                                     "mixed_skipped_shapes", "extra_note_in_applied", "extra_option_in_skipped"])
def test_delivery_rows_must_match_their_applied_or_skipped_list(damage):
    app, report = delivery_record(several=damage == "mixed_applied_shapes")
    if damage == "note_in_applied":
        app["applied"] = [{"item_id": "I1", "note": "A note is not a chosen preview."}]
    elif damage == "choice_in_skipped":
        app["skipped"] = [{"item_id": "I3", "option_id": "A", "amount_text": "0.25 mm outward"}]
    elif damage == "mixed_applied_shapes":
        app["applied"][1] = {"item_id": "I2", "note": "Not selected."}
    elif damage == "mixed_skipped_shapes":
        app["skipped"].append({"item_id": "I4", "option_id": "A", "amount_text": "0.25 mm outward"})
    elif damage == "extra_note_in_applied":
        app["applied"][0]["note"] = "This extra field must not silently turn a pick into a skip."
    else:
        app["skipped"][0]["option_id"] = "A"
    app["notes"] = copy.deepcopy(app["skipped"])
    sync_delivery_report(app, report)
    with pytest.raises(ValueError):
        owner_choices.validate_delivery_application(app, report)


def exported_fixture(path, effects=None, *, reindex=False, shorten=None, extra_patch=None):
    """Real GLB accessor buffers, independent of the production exporter."""
    parts = {"Part one": ((2, 5, 0, 2, -6, 0), "frame", "frame"),
             "Part two": ((-5, -2, 0, 2, -6, 0), "lens", "lens")}
    effects = effects or {}
    def patch(doc, accessor):
        for node in doc["nodes"]:
            name = node["name"]
            primitive = doc["meshes"][node["mesh"]]["primitives"][0]
            positions, triangles = glbfab.box(*parts[name][0])
            attributes = {"POSITION": (positions * .001).astype(np.float32),
                          "NORMAL": np.tile([0.0, 0.0, 1.0], (8, 1)),
                          "TEXCOORD_0": np.zeros((8, 2)), "TEXCOORD_1": np.zeros((8, 2)),
                          "COLOR_0": np.ones((8, 4))}
            for semantic, rows in effects.get(name, {}).items():
                for index, value in rows.items():
                    attributes[semantic][index] = value
            if shorten == name:
                triangles = triangles[:6]
            if reindex:
                permutation = np.arange(7, -1, -1)
                attributes = {key: value[permutation] for key, value in attributes.items()}
                triangles = np.argsort(permutation)[triangles]
            primitive["attributes"] = {key: accessor(value, 5126, "VEC" + str(value.shape[1]))
                                       for key, value in attributes.items()}
            primitive["indices"] = accessor(triangles.reshape(-1), 5125, "SCALAR")
        if extra_patch:
            extra_patch(doc, accessor)
    path.write_bytes(glbfab.build(parts, texture_px=(2, 2), patch=patch))
    return path


def export_selection(item_id, path):
    return {"item_id": item_id, "option_id": "A", "export": {"path": str(path), "sha256": edits.sha(path)}}


def export_effects(*, same_object=False):
    first = {"Part one": {"POSITION": {1: [.0055, 0., -.006]}, "NORMAL": {1: [0., 1., 0.]}}}
    second = {"Part one" if same_object else "Part two": {
        "TEXCOORD_1": {6: [.25, .5]}, "COLOR_0": {6: [.25, .5, .75, 1.]}}}
    combined = copy.deepcopy(first)
    for name, values in second.items():
        combined.setdefault(name, {}).update(values)
    return first, second, combined


@pytest.mark.parametrize("same_object", [False, True])
def test_actual_glb_disjoint_attributes_match_even_with_reindexed_vertex_storage(tmp_path, same_object):
    first, second, combined = export_effects(same_object=same_object)
    source = exported_fixture(tmp_path / "source.glb")
    one, two = exported_fixture(tmp_path / "one.glb", first), exported_fixture(tmp_path / "two.glb", second)
    actual = exported_fixture(tmp_path / "actual.glb", combined, reindex=True)
    result = owner_apply.verify_choice_exports(source, [export_selection("I1", one), export_selection("I2", two)], actual)
    assert result["ok"] and result["primitives_checked"] == 2 and result["overlaps"] == []
    assert {row["attribute"] for row in result["comparisons"]} == {
        "POSITION", "NORMAL", "TEXCOORD_0", "TEXCOORD_1", "COLOR_0"}


@pytest.mark.parametrize("semantic", ["POSITION", "NORMAL", "TEXCOORD_0", "TEXCOORD_1", "COLOR_0"])
def test_actual_glb_rejects_unseen_export_attribute_drift(tmp_path, semantic):
    first, second, combined = export_effects()
    source = exported_fixture(tmp_path / "source.glb")
    one, two = exported_fixture(tmp_path / "one.glb", first), exported_fixture(tmp_path / "two.glb", second)
    bad = copy.deepcopy(combined)
    changed = {"POSITION": [.003, .002, -.001], "NORMAL": [.001, 0., 1.],
               "TEXCOORD_0": [.001, 0.], "TEXCOORD_1": [.001, 0.], "COLOR_0": [.99, 1., 1., 1.]}[semantic]
    bad["Part one"].setdefault(semantic, {})[7] = changed
    actual = exported_fixture(tmp_path / "actual.glb", bad)
    with pytest.raises(ValueError, match=semantic):
        owner_apply.verify_choice_exports(source, [export_selection("I1", one), export_selection("I2", two)], actual)


def test_actual_glb_single_pick_requires_identical_file_bytes(tmp_path):
    first, _, _ = export_effects()
    source = exported_fixture(tmp_path / "source.glb")
    one = exported_fixture(tmp_path / "one.glb", first)
    actual = tmp_path / "actual.glb"
    actual.write_bytes(one.read_bytes())
    assert owner_apply.verify_choice_exports(source, [export_selection("I1", one)], actual)["single_pick_glb_equal"] is True
    exported_fixture(actual, first, reindex=True)
    with pytest.raises(ValueError, match="single applied GLB bytes"):
        owner_apply.verify_choice_exports(source, [export_selection("I1", one)], actual)


def test_actual_glb_reports_overlapping_attribute_rows(tmp_path):
    first, _, _ = export_effects()
    second = {"Part one": {"POSITION": {1: [.006, 0., -.006]}}}
    combined = copy.deepcopy(first)
    combined["Part one"]["POSITION"][1] = [.0065, 0., -.006]
    source = exported_fixture(tmp_path / "source.glb")
    one, two = exported_fixture(tmp_path / "one.glb", first), exported_fixture(tmp_path / "two.glb", second)
    actual = exported_fixture(tmp_path / "actual.glb", combined)
    result = owner_apply.verify_choice_exports(source, [export_selection("I1", one), export_selection("I2", two)], actual)
    assert result["overlaps"][0]["item_ids"] == ["I1", "I2"]
    assert result["overlaps"][0]["objects"] == [{"object": "Part one", "primitive_index": 0}]
    assert result["overlaps"][0]["attributes"]["POSITION"] > 0


def test_actual_glb_topology_change_is_exact_only_without_another_change_on_that_primitive(tmp_path):
    first, second, combined = export_effects()
    source = exported_fixture(tmp_path / "source.glb")
    one = exported_fixture(tmp_path / "one.glb", first, shorten="Part one")
    two = exported_fixture(tmp_path / "two.glb", second)
    actual = exported_fixture(tmp_path / "actual.glb", combined, shorten="Part one")
    result = owner_apply.verify_choice_exports(source, [export_selection("I1", one), export_selection("I2", two)], actual)
    assert any(row.get("mode") == "whole_primitive" and row["item_id"] == "I1" for row in result["comparisons"])
    exported_fixture(two, {"Part one": {"TEXCOORD_1": {6: [.25, .5]}}})
    with pytest.raises(ValueError, match="topology overlaps"):
        owner_apply.verify_choice_exports(source, [export_selection("I1", one), export_selection("I2", two)], actual)


def test_actual_glb_material_texture_and_sampler_content_survives_index_reordering(tmp_path):
    first, second, combined = export_effects()
    source = exported_fixture(tmp_path / "source.glb")
    one, two = exported_fixture(tmp_path / "one.glb", first), exported_fixture(tmp_path / "two.glb", second)
    def reorder(doc, accessor):
        doc["materials"].reverse()
        for mesh in doc["meshes"]:
            for primitive in mesh["primitives"]:
                primitive["material"] = 1 - primitive["material"]
        doc["images"].insert(0, copy.deepcopy(doc["images"][0]))
        doc["textures"][0]["source"] = 1
        doc["textures"].insert(0, copy.deepcopy(doc["textures"][0]))
        doc["materials"][1]["pbrMetallicRoughness"]["baseColorTexture"]["index"] = 1
    actual = exported_fixture(tmp_path / "actual.glb", combined, extra_patch=reorder)
    assert owner_apply.verify_choice_exports(source, [export_selection("I1", one), export_selection("I2", two)], actual)["ok"]
    def change_material(doc, accessor):
        doc["materials"][0]["pbrMetallicRoughness"]["roughnessFactor"] = .99
    exported_fixture(actual, combined, extra_patch=change_material)
    with pytest.raises(ValueError, match="material"):
        owner_apply.verify_choice_exports(source, [export_selection("I1", one), export_selection("I2", two)], actual)


@pytest.mark.parametrize("damage", ["duplicate_name", "renamed", "transform", "missing_attribute", "extra_attribute", "texture", "image"])
def test_actual_glb_ambiguous_identity_or_unshown_state_refuses(tmp_path, damage):
    first, second, combined = export_effects()
    source = exported_fixture(tmp_path / "source.glb")
    one, two = exported_fixture(tmp_path / "one.glb", first), exported_fixture(tmp_path / "two.glb", second)
    def mutate(doc, accessor):
        primitive = doc["meshes"][0]["primitives"][0]
        if damage == "duplicate_name":
            doc["nodes"][1]["name"] = doc["nodes"][0]["name"]
        elif damage == "renamed":
            doc["nodes"][0]["name"] = "Different object"
        elif damage == "transform":
            doc["nodes"][0]["translation"] = [.001, 0., 0.]
        elif damage == "missing_attribute":
            primitive["attributes"].pop("COLOR_0")
        elif damage == "extra_attribute":
            primitive["attributes"]["TANGENT"] = accessor(np.ones((8, 4)), 5126, "VEC4")
        elif damage == "texture":
            doc["samplers"] = [{"magFilter": 9728, "minFilter": 9728}]
            doc["textures"][0]["sampler"] = 0
        else:
            import base64
            doc["images"][0].pop("bufferView")
            doc["images"][0]["uri"] = "data:image/png;base64," + base64.b64encode(glbfab.png_bytes(3, 2)).decode()
    actual = exported_fixture(tmp_path / "actual.glb", combined, extra_patch=mutate)
    with pytest.raises(ValueError):
        owner_apply.verify_choice_exports(source, [export_selection("I1", one), export_selection("I2", two)], actual)


@pytest.mark.parametrize("damage", ["changed_bytes", "unbound_export", "different_source"])
def test_apply_requires_one_intact_sealed_source_export_for_all_choices(sealed_choices, monkeypatch, tmp_path, damage):
    h = sealed_choices
    before = h.loaded[0][1]["before_export"]
    if damage == "changed_bytes":
        Path(before["path"]).write_bytes(b"changed")
    elif damage == "unbound_export":
        h.understanding["evidence_files"].pop(Path(before["path"]).relative_to(h.root).as_posix())
    else:
        other = h.root / "work/previews/different-source.glb"
        other.write_bytes(b"another source")
        h.understanding["evidence_files"][other.relative_to(h.root).as_posix()] = edits.sha(other)
        h.loaded[1][1]["before_export"] = {"path": str(other), "sha256": edits.sha(other)}
    def unexpected(*args):
        raise AssertionError("Unbound baseline reached local application")
    monkeypatch.setattr(owner_choices, "_run_apply", unexpected)
    with pytest.raises(ValueError):
        owner_choices.apply_choices(h.root, h.sha, h.choices, h.skipped, "Apply", tmp_path / "apply")
