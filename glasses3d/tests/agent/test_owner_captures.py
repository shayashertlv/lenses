"""Capture intake proves real GLB identity/math and immutable owner-only bundles."""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from glasses3d.agent import edits, owner_captures as C, edit_capabilities
from glasses3d.g3d_host import glb
from glasses3d.tests import glbfab
from .test_owner_edit_lifecycle_review import selected_source


def capture_fixture(folder, model_path=None, *, capture_id="C1", label="A", node=0, triangle=2, camera_y_deg=0):
    folder.mkdir(parents=True, exist_ok=True)
    if model_path is None:
        model_path = folder / "source.glb"
        model_path.write_bytes(glbfab.build())
    primitive = glb.load(model_path).primitives[node]
    weights = np.array([0.2, 0.3, 0.5])
    point = weights @ primitive.positions[primitive.triangles[triangle]]
    angle = math.radians(camera_y_deg)
    quaternion = [0, math.sin(angle / 2), 0, math.cos(angle / 2)]
    position = [math.sin(angle) * .4, 0, math.cos(angle) * .4]
    world = glb.node_local_matrix({"rotation": quaternion, "translation": position})
    fov, aspect, near, far = 35, 1600 / 900, .001, 10
    f = 1 / math.tan(math.radians(fov) / 2)
    projection = np.array([[f / aspect, 0, 0, 0], [0, f, 0, 0],
                           [0, 0, -(far + near) / (far - near), -2 * far * near / (far - near)],
                           [0, 0, -1, 0]])
    clip = projection @ np.linalg.inv(world) @ np.r_[point, 1]
    xy = [float((clip[0] / clip[3] + 1) * 800), float((1 - clip[1] / clip[3]) * 450)]
    payload = {"schema": C.SCHEMA, "capture_id": capture_id,
        "source": {"delivery_key": "fixture", "glb_sha256": edits.sha(model_path)},
        "image": {"width_px": 1600, "height_px": 900},
        "camera": {"projection": "perspective", "fov_y_deg": fov, "aspect": aspect, "near_m": near, "far_m": far,
                   "position_m": position, "quaternion_xyzw": quaternion, "target_m": [0, 0, 0],
                   "matrix_world": world.T.ravel().tolist(), "projection_matrix": projection.T.ravel().tolist()},
        "marks": [{"label": label, "kind": "point", "radius_px": 9, "stroke_px": 4,
                   "vertices": [{"xy_px": xy, "anchor": {"kind": "surface",
                       "node_index": primitive.node_index, "node_name": primitive.node_name,
                       "mesh_index": primitive.mesh_index, "primitive_index": 0, "triangle_index": triangle,
                       "barycentric": weights.tolist(), "position_mm": (point * 1000).tolist(),
                       "normal": [0, 0, 1], "is_lens": node in (3, 4), "behind": None}}],
                   "footprint": {"pixels": 13, "faces": [{"node_index": primitive.node_index,
                       "mesh_index": primitive.mesh_index, "primitive_index": 0, "triangles": [[triangle, triangle]]}]}}],
        "request_text": "  Move A inwards.\r\n  "}
    for name in ("viewer.png", "annotated.png"):
        Image.new("RGB", (1600, 900), "white").save(folder / name)
    (folder / "capture.json").write_text(json.dumps(payload), encoding="utf8")
    return SimpleNamespace(payload=payload, folder=folder, model=model_path,
                           viewer=folder / "viewer.png", annotated=folder / "annotated.png")


def validate(f, payload=None):
    return C.validate_capture(payload if payload is not None else f.payload, f.viewer, f.annotated, f.model)


@pytest.fixture
def cap(tmp_path):
    return capture_fixture(tmp_path / "capture")


def test_valid_capture_checks_actual_glb_and_preserves_verbatim_payload(cap):
    result = validate(cap)
    assert result == cap.payload and result is not cap.payload
    assert result["request_text"] == "  Move A inwards.\r\n  "
    assert C.validate_capture(cap.payload, cap.viewer.read_bytes(), cap.annotated.read_bytes(), cap.model) == result


@pytest.mark.parametrize("change,message", [
    (lambda p: p.update(schema="wrong"), "format"),
    (lambda p: p.update(capture_id="../C1"), "identifier"),
    (lambda p: p["source"].update(glb_sha256="0" * 64), "different model"),
    (lambda p: p["image"].update(width_px=True), "whole number"),
    (lambda p: p["image"].update(width_px=800), "exact size"),
    (lambda p: p["camera"].update(fov_y_deg=float("nan")), "finite"),
    (lambda p: p["camera"].update(fov_y_deg=10 ** 1000), "finite"),
    (lambda p: p["camera"].update(aspect=3), "aspect"),
    (lambda p: p["camera"].update(near_m=11), "range"),
    (lambda p: p["camera"].update(projection="orthographic"), "perspective"),
    (lambda p: p["camera"].update(quaternion_xyzw=[0, 0, 0, 2]), "unit length"),
    (lambda p: p["camera"].update(target_m=[0, 1, 0]), "target"),
    (lambda p: p["camera"]["matrix_world"].__setitem__(12, 1), "matrix"),
    (lambda p: p["camera"]["projection_matrix"].__setitem__(0, 1), "projection matrix"),
    (lambda p: p["marks"][0].update(kind="polygon"), "closed shape"),
    (lambda p: p["marks"][0].update(radius_px=0), "visible size"),
    (lambda p: p["marks"][0].update(vertices=[]), "number of points"),
    (lambda p: p["marks"].append(copy.deepcopy(p["marks"][0])), "unique label"),
    (lambda p: p["marks"][0]["vertices"][0].update(xy_px=[-1, 0]), "outside"),
    (lambda p: p["marks"][0]["vertices"][0]["xy_px"].__setitem__(0, 12), "line up"),
    (lambda p: p["marks"][0]["vertices"][0]["anchor"].update(node_index=999), "cannot be found"),
    (lambda p: p["marks"][0]["vertices"][0]["anchor"].update(node_name="other"), "object name"),
    (lambda p: p["marks"][0]["vertices"][0]["anchor"].update(mesh_index=1), "cannot be found"),
    (lambda p: p["marks"][0]["vertices"][0]["anchor"].update(primitive_index=2), "cannot be found"),
    (lambda p: p["marks"][0]["vertices"][0]["anchor"].update(triangle_index=99), "triangle"),
    (lambda p: p["marks"][0]["vertices"][0]["anchor"].update(barycentric=[.3, .3, .5]), "add up"),
    (lambda p: p["marks"][0]["vertices"][0]["anchor"].update(barycentric=[-.2, .7, .5]), "between"),
    (lambda p: p["marks"][0]["vertices"][0]["anchor"].update(position_mm=[0, 0, 0]), "position"),
    (lambda p: p["marks"][0]["vertices"][0]["anchor"].update(normal=[0, 0, float("inf")]), "finite"),
    (lambda p: p["marks"][0]["vertices"][0]["anchor"].update(normal=[0, 0, 2]), "unit length"),
    (lambda p: p["marks"][0]["vertices"][0]["anchor"].update(is_lens=True), "lens flag"),
    (lambda p: p["marks"][0]["vertices"][0]["anchor"].update(behind={"kind": "background"}), "Only a lens"),
    (lambda p: p["marks"][0]["footprint"].update(pixels=-1), "pixel count"),
    (lambda p: p["marks"][0]["footprint"].update(pixels=0), "do not agree"),
    (lambda p: p["marks"][0]["footprint"]["faces"][0].update(triangles=[[0, 12]]), "triangle"),
    (lambda p: p["marks"][0]["footprint"]["faces"][0].update(triangles=[[2, 1]]), "triangle"),
    (lambda p: p["marks"][0]["footprint"]["faces"][0].update(triangles=[[1, 4], [3, 5]]), "overlaps"),
])
def test_invalid_capture_refuses_with_plain_message(cap, change, message):
    payload = copy.deepcopy(cap.payload)
    change(payload)
    with pytest.raises(ValueError, match=message):
        validate(cap, payload)


@pytest.mark.parametrize("name", ["viewer", "annotated"])
@pytest.mark.parametrize("damage", ["jpeg", "corrupt", "dimension"])
def test_both_pngs_must_be_intact_same_native_size(cap, name, damage):
    path = getattr(cap, name)
    if damage == "jpeg":
        Image.new("RGB", (1600, 900)).save(path, format="JPEG")
    elif damage == "corrupt":
        path.write_bytes(path.read_bytes()[:-15])
    else:
        Image.new("RGB", (800, 450)).save(path)
    with pytest.raises(ValueError, match="PNG"):
        validate(cap)


@pytest.mark.parametrize("kind,count", [("polyline", 2), ("closed_shape", 3)])
def test_line_shape_and_background_are_supported_without_invented_anchors(cap, kind, count):
    mark = cap.payload["marks"][0]
    mark["kind"] = kind
    mark["vertices"] = [{"xy_px": [i * 20, 20], "anchor": {"kind": "background"}} for i in range(count)]
    mark["footprint"] = {"pixels": 0, "faces": []}
    assert validate(cap)["marks"][0] == mark
    if kind == "closed_shape":
        mark["vertices"].append(copy.deepcopy(mark["vertices"][0]))
        with pytest.raises(ValueError, match="distinct corners"):
            validate(cap)


def make_round(f, tmp_path):
    cap = capture_fixture(tmp_path / "draft/C1")
    f.saved.update(model=str(cap.model), glb_sha256=edits.sha(cap.model))
    f.saved["report_data"]["glb_sha256"] = edits.sha(cap.model)
    other = capture_fixture(tmp_path / "draft/C2", cap.model, capture_id="C2", label="B", node=5,
                            triangle=10, camera_y_deg=-45)
    items = [{"item_id": "I1", "request_text": "  Move A.\r\n  ", "capture_dir": cap.folder},
             {"item_id": "I2", "request_text": "B should match A.\n", "capture_dir": other.folder}]
    root = C.create_capture_understand(f.source, "selected-delivery", items, f.destination)
    return root, items


def test_facade_preserves_source_and_photos_retains_selected_seed_and_unresolved_items(selected_source, tmp_path):
    f = selected_source
    before = edits.tree_hashes(f.source)
    root, items = make_round(f, tmp_path)
    assert edits.tree_hashes(f.source) == before
    assert (root / "job.json").read_bytes() == (f.source / "job.json").read_bytes()
    assert edits.tree_hashes(root / "images") == edits.tree_hashes(f.source / "images")
    assert (root / "inputs/seed.blend").read_bytes() == b"chosen delivered checkpoint"
    records = C.load_round_captures(root)
    assert [r["request_text"] for r in records] == [i["request_text"] for i in items]
    assert [r["labels"] for r in records] == [["A"], ["B"]]
    assert all(r["source_glb"] == root / C.SOURCE_PATH for r in records)
    assert not (root / edit_capabilities.RELATIVE_PATH).exists()
    assert edit_capabilities.require_round(root) == {
        "schema": "g3d.owner_capture_admission/1", "status": "unresolved", "item_ids": ["I1", "I2"],
        "mutation_authority": False}
    assert "not product photos" in edits.brief_suffix(edits.load(root))
    # Existing follow-on copier carries exactly these immutable inputs.
    follow = tmp_path / "follow"
    edits._copy_inputs(root, follow)
    assert C.load_round_captures(follow)[0]["metadata_sha256"] == records[0]["metadata_sha256"]


@pytest.mark.parametrize("damage", ["viewer", "annotated", "label", "anchor", "words", "items", "metadata"])
def test_saved_capture_tampering_refuses(selected_source, tmp_path, damage):
    root, _ = make_round(selected_source, tmp_path)
    if damage in {"viewer", "annotated"}:
        path = root / f"inputs/owner_captures/C1/{damage}.png"
        path.write_bytes(path.read_bytes() + b"changed")
    else:
        path = root / (C.ITEMS_PATH if damage == "items" else "inputs/owner-edit.json" if damage == "metadata"
                       else "inputs/owner_captures/C1/capture.json")
        value = json.loads(path.read_text())
        if damage == "label":
            value["marks"][0]["label"] = "Z"
        elif damage == "anchor":
            value["marks"][0]["vertices"][0]["anchor"]["position_mm"][0] += 1
        elif damage == "items":
            value[0]["request_text"] = "Change the entire model"
        elif damage == "metadata":
            value["owner_capture_items"][0]["request_text"] = "Change the entire model"
        else:
            value["request_text"] += "Change everything"
        path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="changed|differ"):
        edits.load(root)


def test_unique_session_labels_refuse_before_creating_round(selected_source, tmp_path):
    f = selected_source
    first = capture_fixture(tmp_path / "a")
    second = capture_fixture(tmp_path / "b", first.model, capture_id="C2", label="a")
    f.saved.update(model=str(first.model), glb_sha256=edits.sha(first.model))
    with pytest.raises(ValueError, match="unique across"):
        C.create_capture_understand(f.source, "selected-delivery", [
            {"item_id": "I1", "request_text": "A", "capture_dir": first.folder},
            {"item_id": "I2", "request_text": "a", "capture_dir": second.folder}], f.destination)
    assert not f.destination.exists()


def test_ordinary_job_has_no_capture_evidence(tmp_path):
    assert C.load_round_captures(tmp_path) == []


def test_capture_understanding_requires_original_item_ids_before_map_or_mutation(selected_source, tmp_path):
    from glasses3d.agent.tools import owner_edit
    root, _ = make_round(selected_source, tmp_path)
    doc = edits.load(root)
    scene = SimpleNamespace(root=root, s=SimpleNamespace(fingerprint=lambda: doc["source"]["fingerprint"]))
    item = {"number": 1, "item_id": "I9", "label": "A", "interpretation": "Needs discussion",
            "disposition": "needs_clarification", "reason": "Need direction", "direction": "", "amount": "",
            "target_ids": [], "operation_json": "[]"}
    with pytest.raises(ValueError, match="original item_id"):
        owner_edit.understand(scene, "call", "map", "sha", [], [item], [])


def test_capture_edit_still_refuses_unresolved_authority(selected_source, tmp_path):
    root, _ = make_round(selected_source, tmp_path)
    doc = edits.load(root)
    doc["phase"] = "edit"
    doc["understanding"] = {"status": "diagnostic_only", "operations": [], "items": []}
    with pytest.raises(ValueError, match="no executable"):
        edit_capabilities.require_round(root, doc)


def test_degenerate_surface_and_excessively_long_capture_id_refuse(cap):
    payload = copy.deepcopy(cap.payload)
    payload["capture_id"] = "C" + "1" * 100
    with pytest.raises(ValueError, match="identifier"):
        validate(cap, payload)
    cap.model.write_bytes(glbfab.build(parts={"Zero": ((0, 1, 0, 0, 0, 0), "frame", "frame")}))
    cap.payload["source"]["glb_sha256"] = edits.sha(cap.model)
    cap.payload["marks"][0]["vertices"][0]["anchor"]["node_name"] = "Zero"
    with pytest.raises(ValueError, match="no surface area"):
        validate(cap)


def test_hidden_anchor_and_false_background_refuse(cap):
    front = cap.payload["marks"][0]["vertices"][0]
    front["anchor"] = {"kind": "background"}
    with pytest.raises(ValueError, match="background mark points at the model"):
        validate(cap)
    rear = capture_fixture(cap.folder, cap.model, triangle=0)
    with pytest.raises(ValueError, match="hidden behind"):
        validate(rear)


def test_lens_behind_must_be_first_real_non_lens_hit(tmp_path):
    path = tmp_path / "lenses.glb"
    path.write_bytes(glbfab.build(parts={
        "Glass": ((-10, 10, -10, 10, 1, 2), "lens", "lens"),
        "Near": ((-10, 10, -10, 10, -4, -3), "frame", "frame"),
        "Far": ((-10, 10, -10, 10, -10, -9), "frame", "frame")}))
    f = capture_fixture(tmp_path / "lens", path)
    anchor = f.payload["marks"][0]["vertices"][0]["anchor"]
    anchor["is_lens"] = True
    with pytest.raises(ValueError, match="retain the first"):
        validate(f)
    model = glb.load(path)
    def rear_anchor(node):
        primitive = model.primitives[node]
        corners = primitive.positions[primitive.triangles[2]]
        origin = np.array([0, 0, .4])
        ray = np.asarray(anchor["position_mm"]) / 1000 - origin
        point = origin + ray * ((corners[0, 2] - origin[2]) / ray[2])
        weights = np.linalg.lstsq(np.vstack([corners.T, np.ones(3)]), np.r_[point, 1], rcond=None)[0]
        return {**copy.deepcopy(anchor), "node_index": node, "node_name": primitive.node_name,
                "mesh_index": primitive.mesh_index, "is_lens": False, "behind": None,
                "position_mm": (point * 1000).tolist(), "barycentric": weights.tolist()}
    anchor["behind"] = rear_anchor(1)
    assert validate(f)["marks"][0]["vertices"][0]["anchor"]["behind"]["node_name"] == "Near"
    anchor["behind"] = rear_anchor(2)
    with pytest.raises(ValueError, match="not its first"):
        validate(f)


def test_public_edit_round_copies_capture_bundle_and_keeps_strict_item_authority(selected_source, tmp_path):
    root, _ = make_round(selected_source, tmp_path)
    doc = edits.load(root)
    operations = [{"id": "op1", "kind": "translate_region", "target_ids": ["T1"], "params": {}},
                  {"id": "op2", "kind": "translate_region", "target_ids": ["T2"], "params": {}}]
    artifact = {"schema": edits.UNDERSTANDING_SCHEMA, "status": "awaiting_owner_confirmation",
                "source_fingerprint": doc["source"]["fingerprint"], "call_id": "shown",
                "evidence_files": {}, "operations": operations, "items": [
                    {"number": i + 1, "item_id": f"I{i + 1}", "disposition": "executable",
                     "operation_json": json.dumps(op)} for i, op in enumerate(operations)]}
    folder = root / "work/owner_edit"
    folder.mkdir(parents=True)
    (folder / "understanding.json").write_text(json.dumps(artifact))
    (folder / "understanding-delivery.json").write_text(json.dumps({"delivered": True, "call_id": "shown",
        "sha256": edits.sha(folder / "understanding.json")}))
    before = edits.tree_hashes(root)
    follow = edits.create_edit(root.parent, "  Yes, exact items.\n", destination=tmp_path / "paid-edit")
    assert edits.tree_hashes(root) == before
    assert edits.tree_hashes(root / "inputs/owner_captures") == edits.tree_hashes(follow / "inputs/owner_captures")
    assert edit_capabilities.require_round(follow)["status"] == "confirmed"
    doc = edits.load(follow)
    doc["understanding"]["items"][0]["item_id"] = "I2"
    doc["understanding"]["items"][1]["item_id"] = "I1"
    with pytest.raises(ValueError, match="original order"):
        edit_capabilities.require_round(follow, doc)


def test_surface_math_uses_world_transform_and_accepts_shared_triangle_edge(tmp_path):
    path = tmp_path / "transformed.glb"
    transform = glbfab.trans(.015, .003, 0) @ glbfab.rot("z", 20)
    path.write_bytes(glbfab.build(node_matrix=transform))
    f = capture_fixture(tmp_path / "transformed", path)
    assert validate(f) == f.payload
    vertex = f.payload["marks"][0]["vertices"][0]
    anchor = vertex["anchor"]
    primitive = glb.load(path).primitives[0]
    weights = np.array([.5, 0, .5])
    point = weights @ primitive.positions[primitive.triangles[2]]
    anchor.update(barycentric=weights.tolist(), position_mm=(point * 1000).tolist())
    camera = f.payload["camera"]
    projection = np.array(camera["projection_matrix"]).reshape(4, 4).T
    world = np.array(camera["matrix_world"]).reshape(4, 4).T
    clip = projection @ np.linalg.inv(world) @ np.r_[point, 1]
    vertex["xy_px"] = [float((clip[0] / clip[3] + 1) * 800), float((1 - clip[1] / clip[3]) * 450)]
    assert validate(f) == f.payload


@pytest.mark.parametrize("damage,message", [
    ("duplicate_selected_ids", "confirmed operations need distinct identities"),
    ("reused_item_operation", "each capture item needs its own distinct confirmed operations"),
    ("absent_selected_operation", "each capture item needs its own distinct confirmed operations"),
    ("changed_selected_kind", "selected operation kinds differ"),
    ("unrequested_selected_operation", "include an unrequested item"),
    ("unsupported_kind", "operations are unavailable"),
    ("blocked_item", "understanding still has blocked items"),
])
def test_capture_edit_refuses_invalid_confirmed_operation_accounting(selected_source, tmp_path, damage, message):
    root, _ = make_round(selected_source, tmp_path)
    doc = edits.load(root)
    operations = [{"id": "op1", "kind": "translate_region", "target_ids": ["T1"], "params": {}},
                  {"id": "op2", "kind": "translate_region", "target_ids": ["T2"], "params": {}}]
    items = [{"number": i + 1, "item_id": row["item_id"], "disposition": "executable",
              "operation_json": json.dumps(op)}
             for i, (row, op) in enumerate(zip(doc["owner_capture_items"], operations))]
    doc.update(phase="edit", selected_operations=copy.deepcopy(operations), understanding={
        "schema": edits.UNDERSTANDING_SCHEMA, "status": "awaiting_owner_confirmation",
        "source_fingerprint": doc["source"]["fingerprint"], "operations": operations, "items": items})
    assert edit_capabilities.require_round(root, doc)["status"] == "confirmed"
    selected = doc["selected_operations"]
    if damage == "duplicate_selected_ids":
        selected[1]["id"] = selected[0]["id"]
    elif damage == "reused_item_operation":
        items[1]["operation_json"] = items[0]["operation_json"]
        operations[1] = copy.deepcopy(operations[0])
    elif damage == "absent_selected_operation":
        selected.pop()
    elif damage == "changed_selected_kind":
        selected[0]["kind"] = "rotate_region"
    elif damage == "unrequested_selected_operation":
        selected.append({**copy.deepcopy(selected[0]), "id": "unrequested-op"})
    elif damage == "unsupported_kind":
        selected[0]["kind"] = operations[0]["kind"] = "unavailable_operation"
        items[0]["operation_json"] = json.dumps(operations[0])
    elif damage == "blocked_item":
        items[1].update(disposition="needs_clarification", reason="The owner has not specified a direction.",
                        operation_json="[]")
        operations.pop()
        selected.pop()
    # Leave the preexisting seal check satisfied, so each case reaches the new
    # capture-specific admission refusal rather than a malformed-understanding guard.
    edits.require_executable(doc["understanding"])
    with pytest.raises(ValueError, match=message):
        edit_capabilities.require_round(root, doc)
