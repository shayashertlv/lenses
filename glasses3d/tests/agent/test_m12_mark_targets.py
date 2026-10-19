"""Owner marks identify source polygons without bypassing shown-target/support authority."""
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest

from glasses3d.agent.tools import arview
from glasses3d.agent.tools import model_map as M
from glasses3d.agent.tools import model_map_geometry as G
from glasses3d.g3d_host import glb
from glasses3d.tests.fixtures.seat_fixtures import Mesh, patch_glb, write_glb


def source_fixture(tmp_path):
    objects, nodes = [], []
    for index, name in enumerate(("Front.Frame: [raw]", "Temple_Right")):
        vertices = np.array([[0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0]], float)
        vertices += [20*index, 0, -5*index]
        polygons = [[0, 1, 2], [0, 2, 3]]
        edges = sorted({tuple(sorted((a, b))) for p in polygons for a, b in zip(p, p[1:]+p[:1])})
        objects.append({"id": f"O{index+1:04d}", "name": name, "mesh_name": name, "role": "frame",
            "side": -1, "vertices_local": vertices.tolist(), "vertices_world_mm": vertices.tolist(),
            "polygons": polygons, "edges": [list(edge) for edge in edges], "aliases": {},
            "evaluated": {"positions_match_source": True}, "topology_sha256": "t", "positions_sha256": "p"})
        nodes.append({"name": name, "mesh": Mesh(vertices, np.asarray(polygons)), "role": "frame"})
    original = tmp_path / "original.glb"
    write_glb(original, nodes)
    model_path = tmp_path / "source.glb"

    def split_and_reorder(doc):
        original_primitive = doc["meshes"][0]["primitives"][0]
        index_accessor = doc["accessors"][original_primitive["indices"]]
        primitives = []
        # GLB triangle zero of primitive zero is raw polygon ONE, not polygon zero.
        for byte_offset in (12, 0):
            accessor = {**index_accessor, "count": 3, "byteOffset": byte_offset}
            doc["accessors"].append(accessor)
            primitives.append({**original_primitive, "indices": len(doc["accessors"])-1})
        doc["meshes"][0]["primitives"] = primitives
    patch_glb(original, model_path, split_and_reorder)
    return {"fingerprint": "f"*64, "objects": objects}, model_path


def surface(model, *, node=0, primitive=0, triangle=0):
    p = next(p for p in model.primitives if p.node_index == node and p.primitive_index == primitive)
    barycentric = [0.2, 0.3, 0.5]
    return {"kind": "surface", "node_index": node, "node_name": p.node_name,
        "mesh_index": p.mesh_index, "primitive_index": primitive, "triangle_index": triangle,
        "barycentric": barycentric, "position_mm": (np.array(barycentric)@p.positions[p.triangles[triangle]]*1000).tolist(),
        "normal": [0, 0, 1], "is_lens": False, "behind": None}


def capture_fixture(model):
    return {"capture_id": "C1", "marks": [{"label": "A", "kind": "point",
        "vertices": [{"xy_px": [30, 20], "anchor": surface(model)}],
        "footprint": {"pixels": 40, "faces": [{"node_index": 0, "mesh_index": 0,
            "primitive_index": 0, "triangles": [[0, 0]]}]}}]}


def test_real_glb_primitive_local_indices_resolve_raw_polygons_and_exact_name(tmp_path):
    snapshot, path = source_fixture(tmp_path)
    capture = capture_fixture(glb.load(path))
    result = G.resolve_capture_marks(snapshot, path, capture)[0]
    assert result["status"] == "resolved" and result["edit_authority"] is False
    assert result["anchors"][0]["object_name"] == "Front.Frame: [raw]"
    assert result["anchors"][0]["polygon_id"] == 1
    assert result["footprint"]["polygons"][0]["polygon_ids"] == [1]
    assert result["candidate_target_ids"]["parts"] == ["O0001"]
    assert result["candidate_target_ids"]["faces"]
    assert "support_scopes" not in result


def test_every_anchor_and_inclusive_footprint_range_is_resolved(tmp_path):
    snapshot, path = source_fixture(tmp_path)
    model = glb.load(path)
    capture = capture_fixture(model)
    mark = capture["marks"][0]
    mark["kind"] = "closed_shape"
    mark["vertices"] += [{"xy_px": [40, 20], "anchor": surface(model, primitive=1)},
                         {"xy_px": [50, 20], "anchor": surface(model, node=1, triangle=1)}]
    mark["footprint"]["faces"] += [{"node_index": 1, "mesh_index": 1, "primitive_index": 0, "triangles": [[0, 1]]}]
    result = G.resolve_capture_marks(snapshot, model, capture)[0]
    assert [row["polygon_id"] for row in result["anchors"]] == [1, 0, 1]
    assert result["footprint"]["triangle_count"] == result["footprint"]["resolved_triangle_count"] == 3
    assert result["resolved_polygons"] == [
        {"object_id": "O0001", "object_name": "Front.Frame: [raw]", "polygon_ids": [0, 1]},
        {"object_id": "O0002", "object_name": "Temple_Right", "polygon_ids": [0, 1]}]


@pytest.mark.parametrize("field,value", [("node_index", 9), ("mesh_index", 1),
    ("primitive_index", 8), ("triangle_index", 1), ("node_name", "FrontFrame_raw")])
def test_unmatched_glb_identity_is_explicitly_unresolved(tmp_path, field, value):
    snapshot, path = source_fixture(tmp_path)
    capture = capture_fixture(glb.load(path))
    capture["marks"][0]["vertices"][0]["anchor"][field] = value
    row = G.resolve_capture_marks(snapshot, path, capture)[0]
    assert row["status"] == row["anchors"][0]["status"] == "unresolved"
    assert row["footprint"]["resolved_triangle_count"] == 1


@pytest.mark.parametrize("fault", ["duplicate_vertex", "duplicate_polygon", "duplicate_source_name", "moved_vertex"])
def test_nonunique_or_changed_export_correspondence_never_guesses(tmp_path, fault):
    snapshot, path = source_fixture(tmp_path)
    capture = capture_fixture(glb.load(path))
    obj = snapshot["objects"][0]
    if fault == "duplicate_vertex":
        obj["vertices_world_mm"].append(obj["vertices_world_mm"][0])
    elif fault == "duplicate_polygon":
        obj["polygons"].append(obj["polygons"][1])
    elif fault == "duplicate_source_name":
        snapshot["objects"][1]["name"] = obj["name"]
    else:
        obj["vertices_world_mm"][0][0] += 1
    row = G.resolve_capture_marks(snapshot, path, capture, targets={})[0]
    assert row["status"] == "unresolved"
    assert row["resolved_polygons"] == []
    assert row["footprint"]["unresolved"][0]["triangles"] == [0, 0]


def test_two_export_nodes_with_same_name_cannot_claim_one_raw_object(tmp_path):
    snapshot, path = source_fixture(tmp_path)
    model = glb.load(path)
    model.primitives[-1].node_name = model.primitives[0].node_name
    row = G.resolve_capture_marks(snapshot, model, capture_fixture(model))[0]
    assert row["anchors"][0]["status"] == "unresolved"
    assert row["candidate_target_ids"]["parts"] == []


def test_lens_behind_and_background_are_reported_separately(tmp_path):
    snapshot, path = source_fixture(tmp_path)
    model = glb.load(path)
    capture = capture_fixture(model)
    anchor = capture["marks"][0]["vertices"][0]["anchor"]
    anchor.update(is_lens=True, behind=surface(model, node=1))
    mark = G.resolve_capture_marks(snapshot, model, capture)[0]
    assert mark["anchors"][0]["behind"]["object_name"] == "Temple_Right"
    assert mark["candidate_target_ids"]["parts"] == ["O0001"]
    assert mark["behind_candidate_target_ids"]["parts"] == ["O0002"]
    capture["marks"][0]["vertices"][0]["anchor"] = {"kind": "background"}
    mark = G.resolve_capture_marks(snapshot, model, capture)[0]
    assert mark["status"] == "unresolved"
    assert mark["anchors"][0]["reason"] == "Background has no model surface"
    assert mark["footprint"]["status"] == "resolved"


def test_partial_footprint_does_not_claim_complete_resolution(tmp_path):
    snapshot, path = source_fixture(tmp_path)
    capture = capture_fixture(glb.load(path))
    capture["marks"][0]["footprint"]["faces"].append({"node_index": 9, "mesh_index": 9,
        "primitive_index": 0, "triangles": [[0, 4]]})
    mark = G.resolve_capture_marks(snapshot, path, capture)[0]
    assert mark["status"] == mark["footprint"]["status"] == "unresolved"
    assert mark["footprint"]["triangle_count"] == 6
    assert mark["footprint"]["resolved_triangle_count"] == 1
    assert mark["footprint"]["unresolved"][0]["triangles"] == [0, 4]


def test_empty_footprint_is_reported_unresolved_even_with_a_surface_anchor(tmp_path):
    snapshot, path = source_fixture(tmp_path)
    capture = capture_fixture(glb.load(path))
    capture["marks"][0]["footprint"]["faces"] = []
    mark = G.resolve_capture_marks(snapshot, path, capture)[0]
    assert mark["status"] == mark["footprint"]["status"] == "unresolved"
    assert mark["anchors"][0]["status"] == "resolved"


def test_resolved_noneditable_source_is_evidence_only(tmp_path):
    snapshot, path = source_fixture(tmp_path)
    snapshot["objects"][0]["evaluated"]["positions_match_source"] = False
    targets = G.catalogue(snapshot)["targets"]
    row = G.resolve_capture_marks(snapshot, path, capture_fixture(glb.load(path)), targets)[0]
    assert row["status"] == "resolved"
    assert row["anchors"][0]["source_editable"] is False
    with pytest.raises(ValueError, match="editable"):
        G.support(snapshot, targets, row["candidate_target_ids"]["parts"])


def test_owner_label_focus_is_explicit_and_background_does_not_expand(tmp_path):
    snapshot, path = source_fixture(tmp_path)
    manifest = G.catalogue(snapshot)
    manifest["owner_mark_resolutions"] = G.resolve_capture_marks(snapshot, path, capture_fixture(glb.load(path)))
    marks, focus = M._owner_selection(manifest, ["A"], None, "faces")
    assert focus == marks[0]["candidate_target_ids"]["faces"]
    assert M._owner_selection(manifest, ["A"], ["O0002"], "faces")[1] == []
    with pytest.raises(ValueError, match="Unknown owner mark"):
        M._owner_selection(manifest, ["missing"], None, "faces")
    with pytest.raises(ValueError, match="distinct"):
        M._owner_selection(manifest, ["A", "A"], None, "faces")
    manifest["owner_mark_resolutions"][0]["candidate_target_ids"] = {kind: [] for kind in ("parts", "faces", "edges", "points")}
    assert M._owner_selection(manifest, ["A"], None, "faces")[1] == []


@pytest.mark.parametrize("visible", [True, False])
def test_mark_overlay_shows_native_evidence_but_authenticates_no_targets(tmp_path, monkeypatch, visible):
    snapshot, path = source_fixture(tmp_path)
    marks = G.resolve_capture_marks(snapshot, path, capture_fixture(glb.load(path)))
    for name in ("photo", "drawing", "ids"):
        Image.new("RGB", (240, 160), "white").save(tmp_path / (name+".png"))
    row = {"ref": "front", "camera": {"width": 240, "height": 160},
           "photo": str(tmp_path/"photo.png"), "drawing": str(tmp_path/"drawing.png"), "part_id": str(tmp_path/"ids.png")}
    monkeypatch.setattr(M.look_panels, "posed_geometry", lambda geometry, pose: geometry)
    hits = np.full((160, 240), -1, int)
    if visible:
        hits[50:90, 80:130] = 0
    monkeypatch.setattr(G, "first_hits", lambda *args: hits)
    result = M._annotate(row, snapshot, [], SimpleNamespace(), np.array(["O0001"], object),
                         np.array([1]), [], tmp_path, 0, owner_marks=marks)
    assert result["annotated_target_ids"] == result["support_scopes"] == []
    assert result["owner_mark_overlays"] == [{"label": "A", "map_label": "M1", "capture_id": "C1", "status": "resolved", "visible_pixels": 2000 if visible else 0}]
    assert result["images"]
    with Image.open(result["full_pair"]) as pair:
        assert pair.width == 480
        assert pair.getpixel((100, 110)) == (255, 255, 255)  # Product photo remains exact.
        assert (pair.getpixel((340, 110)) != (255, 255, 255)) == visible
    from glasses3d.tests.agent.test_look_composition import assert_lossless_pages
    assert_lossless_pages(result["source_images"], result["images"])


def test_resolved_marks_and_delivered_labels_cannot_replace_exact_support(tmp_path):
    from glasses3d.tests.test_model_map import bundle
    mid, sha, result, operation = bundle(tmp_path)
    path = tmp_path/result["capture_receipt"]
    receipt = json.loads(path.read_text())
    receipt.update(support_scopes=[], owner_mark_resolutions=[{"label": "A", "status": "resolved"}])
    path.write_text(json.dumps(receipt))
    result["capture_sha256"] = arview.sha256(path)
    M.mark_delivered(tmp_path, result)
    with pytest.raises(ValueError, match="exact influence"):
        M.validate_understanding(tmp_path, mid, sha, [result["capture_receipt"]], [operation])


def test_extended_tool_schema_exposes_owner_labels_without_new_authority():
    from agents import function_tool
    async def call(*args):
        raise AssertionError("Schema construction cannot execute a tool")
    tool = M.tool_for(SimpleNamespace(), lambda name: lambda fn: function_tool(fn, name_override=name), call)
    assert tool.name == "labelled_model_map"
    assert {"type": "array", "items": {"type": "string"}} in tool.params_json_schema["properties"]["owner_labels"]["anyOf"]
    assert "do not authorize edits" in tool.params_json_schema["properties"]["owner_labels"]["description"]


def test_ordinary_job_model_map_has_no_capture_dependency(tmp_path):
    assert M.resolve_owner_marks(tmp_path, {}, {}) == ([], [])


def test_capture_focus_overlay_and_immutable_metadata_binding(tmp_path, monkeypatch):
    """Exercise the actual map façade; replace only worker/render boundaries."""
    from glasses3d.agent import owner_captures
    snapshot, source = source_fixture(tmp_path)
    capture = capture_fixture(glb.load(source))
    capture["source"] = {"glb_sha256": arview.sha256(source)}
    folder = tmp_path/"inputs/owner_captures/C1"
    folder.mkdir(parents=True)
    metadata = folder/"capture.json"
    metadata.write_text(json.dumps(capture))
    for name in ("viewer.png", "annotated.png"):
        Image.new("RGB", (240, 160), "white").save(folder/name)
    monkeypatch.setattr(owner_captures, "load_round_captures", lambda root: [{"item_id": "I1",
        "source_glb": source, "payload": capture, "metadata_sha256": arview.sha256(metadata),
        "capture_dir": folder, "viewer_path": folder/"viewer.png", "annotated_path": folder/"annotated.png"}])
    basis_dir = tmp_path/"cameras/frozen"
    basis_dir.mkdir(parents=True)
    for name in ("basis.json", "cameras.json"):
        (basis_dir/name).write_text("{}")
    photo_path = tmp_path/"photo.png"
    Image.new("RGB", (240, 160), "white").save(photo_path)
    basis = SimpleNamespace(id="frozen", dir=basis_dir, frame=None,
        refs={"reference-001": {"photo_sha256": arview.sha256(photo_path), "view_kind": "front"}},
        photo_path=lambda _: photo_path, usable_refs=lambda: ["reference-001"],
        photo=lambda _: np.full((160, 240, 3), 255, np.uint8), camera=lambda _: {},
        shape=lambda _: (160, 240), pose=lambda _: None)
    monkeypatch.setattr(M.C, "load_basis", lambda root: basis)
    monkeypatch.setattr(M, "explicit_camera", lambda *args, **kwargs: {"width": 240, "height": 160,
        "meta": {"pixel_scale": 1}, "id": kwargs["name"]})

    def snapshot_call(name, method, params, **kwargs):
        path = Path(params["path"])
        path.write_text(json.dumps(snapshot))
        return {"status": "ok", "result": {"status": "ok", "path": str(path), "sha256": arview.sha256(path)}}
    export = {"status": "exported", "path": str(source), "sha256": arview.sha256(source),
              "fingerprint": snapshot["fingerprint"], "checkpoint": "c1"}
    scene = SimpleNamespace(root=tmp_path, seeing=SimpleNamespace(export_head=lambda **kwargs: export),
                            s=SimpleNamespace(call_read=snapshot_call))
    def render(root, exported, rows, directory, timeout_s):
        for row in rows:
            row["drawing"] = str(photo_path)
            row["part_id"] = str(photo_path)
        report = directory/"report.json"
        report.write_text("{}")
        return report
    monkeypatch.setattr(M, "_photo_render", render)
    geometry = SimpleNamespace(parts=[{"name": "Front.Frame: [raw]", "node_index": 0}],
        vertices=np.asarray(snapshot["objects"][0]["vertices_world_mm"])/1000,
        faces=np.array([[0, 2, 3]]), face_parts=np.array([0]))
    monkeypatch.setattr(M.look_panels, "load_geometry", lambda _: geometry)
    monkeypatch.setattr(M.look_panels, "posed_geometry", lambda geometry, pose: geometry)
    monkeypatch.setattr(G, "first_hits", lambda *args: np.zeros((160, 240), int))
    result = M.capture(scene, "marks-first", owner_labels=["A"])
    assert result["owner_mark_resolutions"][0]["anchors"][0]["object_name"] == "Front.Frame: [raw]"
    assert result["owner_mark_resolutions"][0]["item_id"] == "I1"
    assert result["annotated_target_ids"] == result["owner_mark_resolutions"][0]["candidate_target_ids"]["faces"]
    assert result["support_scopes"] == []
    assert result["views"][0]["owner_mark_overlays"][0]["visible_pixels"] == 240*160
    M.load_map(tmp_path, result["map_id"], result["map_sha256"])
    metadata.write_text(metadata.read_text().replace('"A"', '"Changed"'))
    with pytest.raises(ValueError, match="Map evidence changed"):
        M.load_map(tmp_path, result["map_id"], result["map_sha256"])
