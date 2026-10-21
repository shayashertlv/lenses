"""Lens-only binary preservation, strict input refusals and immutable delivery seams."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import struct

from PIL import Image
import pytest

from glasses3d.agent import delivery, edits, jobs, owner_lens_edit as lens, review
from glasses3d.agent.tools import arview
from glasses3d.tests.agent.test_delivery import make_finished


def _glb(doc, binary=b"\0\1\2\3opaque\0\0", extra=b"preserve"):
    data = json.dumps(doc, indent=2).encode()
    data += b" " * (-len(data) % 4)
    body = struct.pack("<II", len(data), 0x4E4F534A) + data
    for kind, value in ((0x004E4942, binary), (0x12345678, extra)):
        assert len(value) % 4 == 0
        body += struct.pack("<II", len(value), kind) + value
    return struct.pack("<4sII", b"glTF", 2, len(body) + 12) + body


@pytest.fixture
def document():
    return {"asset": {"version": "2.0", "extras": {"preserve": "owner metadata"}},
        "extensionsUsed": ["KHR_materials_volume"], "extensionsRequired": [],
        "materials": [
            {"name": "Frame", "extras": {"partRole": "frame"}, "pbrMetallicRoughness": {
                "baseColorFactor": [.1, .2, .3, 1], "roughnessFactor": .7}},
            {"name": "Lens", "extras": {"partRole": "lens", "preserve": [1, 2]},
             "doubleSided": False, "alphaMode": "BLEND", "pbrMetallicRoughness": {
                 "baseColorFactor": [.2, .4, .6, .8], "baseColorTexture": {"index": 0}},
             "extensions": {"KHR_materials_volume": {"thicknessFactor": .02},
                 "KHR_materials_iridescence": {"iridescenceThicknessTexture": {"index": 1}}}},
            {"name": "Other lens", "extras": {"partRole": "lens"}}],
        "meshes": [{"primitives": [{"material": index, "attributes": {"POSITION": 0}, "indices": 1}]}
                   for index in range(3)], "nodes": [{"mesh": index} for index in range(3)],
        "accessors": [{"bufferView": 0}, {"bufferView": 1}], "bufferViews": [{"buffer": 0}, {"buffer": 0}],
        "buffers": [{"byteLength": 12}], "images": [{"bufferView": 0}], "samplers": [{"wrapS": 10497}],
        "textures": [{"source": 0}], "animations": [{"name": "unchanged"}]}


def _selection(payload, index=1):
    row = next(row for row in lens.read_lens_materials(payload)["materials"] if row["material_index"] == index)
    values = copy.deepcopy(row["values"])
    values.update(tint=[.8, .12, .4], transmission=.23, metallic=.61, roughness=.19,
                  iridescence=.73, ior=1.6, iridescence_ior=1.4,
                  iridescence_thickness_min=210, iridescence_thickness_max=510)
    return {str(index): values}


def test_patch_preserves_binary_unknown_chunks_and_every_unrelated_json_field(document):
    original = _glb(document)
    patched, receipt = lens.patch_lens_glb(original, _selection(original))
    result, chunks = lens._parse(patched)
    before, original_chunks = lens._parse(original)
    assert [row[2] for row in chunks[1:]] == [row[2] for row in original_chunks[1:]]
    assert receipt["binary_chunks_unchanged"] and receipt["unrelated_json_unchanged"]
    assert receipt["changed_material_indices"] == [1]
    assert receipt["source_glb_sha256"] == hashlib.sha256(original).hexdigest()
    assert result["materials"][0] == before["materials"][0]
    assert result["materials"][2] == before["materials"][2]
    # Independently remove only the documented mutable leaves before comparison.
    for doc in (result, before):
        material = doc["materials"][1]
        pbr = material["pbrMetallicRoughness"]
        assert pbr["baseColorFactor"][3] == .8
        for field in ("baseColorFactor", "metallicFactor", "roughnessFactor"):
            pbr.pop(field, None)
        for name, fields in {
            "KHR_materials_transmission": ["transmissionFactor"], "KHR_materials_ior": ["ior"],
            "KHR_materials_iridescence": ["iridescenceFactor", "iridescenceIor", "iridescenceThicknessMinimum", "iridescenceThicknessMaximum"]
        }.items():
            ext = material["extensions"].get(name, {})
            for field in fields:
                ext.pop(field, None)
            if not ext:
                material["extensions"].pop(name, None)
        doc["extensionsUsed"] = [name for name in doc["extensionsUsed"] if name not in lens.EXTENSIONS]
    assert result == before


def test_no_op_preserves_entire_glb_byte_for_byte(document):
    original = _glb(document)
    values = {str(row["material_index"]): row["values"] for row in lens.read_lens_materials(original)["materials"]}
    patched, receipt = lens.patch_lens_glb(original, values)
    assert patched == original
    assert receipt["changed_material_indices"] == []


@pytest.mark.parametrize("field,value", [
    ("ior", 0), ("ior", 3.2), ("ior", 6.0), ("ior", 1234.56789),
    ("iridescence_ior", 6.25), ("iridescence_ior", 1e100),
    ("iridescence_thickness_min", 1600.25), ("iridescence_thickness_max", 1e100),
])
def test_spec_valid_values_open_exactly_and_noop_keeps_every_byte(document, field, value):
    material = document["materials"][1]
    extension, property_name = {
        "ior": ("KHR_materials_ior", "ior"),
        "iridescence_ior": ("KHR_materials_iridescence", "iridescenceIor"),
        "iridescence_thickness_min": ("KHR_materials_iridescence", "iridescenceThicknessMinimum"),
        "iridescence_thickness_max": ("KHR_materials_iridescence", "iridescenceThicknessMaximum"),
    }[field]
    material["extensions"].setdefault(extension, {})[property_name] = value
    original = _glb(document)
    values = {str(row["material_index"]): row["values"]
              for row in lens.read_lens_materials(original)["materials"]}
    assert values["1"][field] == value
    patched, receipt = lens.patch_lens_glb(original, values)
    assert patched == original
    assert receipt["changed_material_indices"] == []


def test_ior_six_edit_round_trips_exactly(document):
    original = _glb(document)
    selected = {str(row["material_index"]): row["values"]
                for row in lens.read_lens_materials(original)["materials"]}
    selected["1"]["ior"] = 6.0
    patched, receipt = lens.patch_lens_glb(original, selected)
    assert receipt["changed_material_indices"] == [1]
    doc, chunks = lens._parse(patched)
    assert doc["materials"][1]["extensions"]["KHR_materials_ior"]["ior"] == 6.0
    assert doc["materials"][0] == document["materials"][0]
    assert doc["materials"][2] == document["materials"][2]
    assert [row[2] for row in chunks[1:]] == [row[2] for row in lens._parse(original)[1][1:]]
    reread = {str(row["material_index"]): row["values"]
              for row in lens.read_lens_materials(patched)["materials"]}
    assert reread == selected
    assert lens.patch_lens_glb(patched, reread)[0] == patched


def test_reversed_film_thickness_endpoints_are_valid_for_texture_interpolation(document):
    original = _glb(document)
    selected = _selection(original)
    selected["1"].update(iridescence_thickness_min=600, iridescence_thickness_max=200)
    patched, _ = lens.patch_lens_glb(original, selected)
    film = lens._parse(patched)[0]["materials"][1]["extensions"]["KHR_materials_iridescence"]
    assert film["iridescenceThicknessMinimum"] == 600
    assert film["iridescenceThicknessMaximum"] == 200
    assert film["iridescenceThicknessTexture"] == {"index": 1}
    reread = {str(row["material_index"]): row["values"]
              for row in lens.read_lens_materials(patched)["materials"]}
    assert reread["1"] == selected["1"]
    assert lens.patch_lens_glb(patched, reread)[0] == patched


@pytest.mark.parametrize("filename,expected_ior", [("00.glb", 6.0), ("17.glb", 3.200000047683716)])
def test_copied_gallery_high_ior_values_and_noop_are_exact(tmp_path, filename, expected_ior):
    source = Path(__file__).resolve().parents[1] / "data/lens_lab/gallery/models" / filename
    if not source.is_file():
        pytest.skip("The owner's local lens-gallery GLB is unavailable.")
    original = source.read_bytes()
    copied = tmp_path / filename
    copied.write_bytes(original)
    rows = lens.read_lens_materials(copied)["materials"]
    assert expected_ior in [row["values"]["ior"] for row in rows]
    source_document = lens._parse(original)[0]
    for row in rows:
        material = source_document["materials"][row["material_index"]]
        assert row["values"]["ior"] == material.get("extensions", {}).get("KHR_materials_ior", {}).get("ior", 1.5)
    selected = {str(row["material_index"]): row["values"] for row in rows}
    patched, receipt = lens.patch_lens_glb(copied, selected)
    assert patched == copied.read_bytes() == original
    assert receipt["changed_material_indices"] == []
    assert source.read_bytes() == original


@pytest.mark.parametrize("bad", [None, {}, [], {"1": {}}, {"01": {}}, {True: {}}, {"0": {}}, {"999": {}}])
def test_missing_unknown_nonlens_or_invalid_material_values_refused(document, bad):
    with pytest.raises(ValueError):
        lens.patch_lens_glb(_glb(document), bad)


@pytest.mark.parametrize("field,value", [("transmission", True), ("metallic", float("nan")),
    ("metallic", 10 ** 400),
    ("roughness", float("inf")), ("iridescence", -.01), ("ior", .5), ("iridescence_ior", .9),
    ("iridescence_thickness_min", -1), ("iridescence_thickness_max", "300"),
    ("ior", -1), ("ior", float("nan")), ("ior", float("inf")), ("ior", 10 ** 400),
    ("iridescence_ior", True), ("iridescence_ior", float("inf")),
    ("iridescence_thickness_min", float("nan")), ("iridescence_thickness_max", float("inf")),
    ("tint", [1, 2, 0]), ("tint", [1, 0]), ("tint", [True, 0, 0])])
def test_control_ranges_are_strict(document, field, value):
    payload = _glb(document)
    selected = _selection(payload)
    selected["1"][field] = value
    with pytest.raises(ValueError):
        lens.patch_lens_glb(payload, selected)


def test_unknown_fields_and_duplicate_index_refused(document):
    payload = _glb(document)
    selected = _selection(payload)
    selected["1"]["alpha"] = 0
    with pytest.raises(ValueError, match="unknown"):
        lens.patch_lens_glb(payload, selected)
    selected = _selection(payload)
    selected[1] = selected["1"]
    with pytest.raises(ValueError, match="distinct"):
        lens.patch_lens_glb(payload, selected)


def test_role_must_be_exclusive_and_cannot_be_inferred_from_transmission(document):
    document["nodes"][1]["extras"] = {"partRole": "frame"}
    document["materials"][0]["extensions"] = {"KHR_materials_transmission": {"transmissionFactor": .8}}
    assert [row["material_index"] for row in lens.read_lens_materials(_glb(document))["materials"]] == [2]
    document["materials"][2].pop("extras")
    with pytest.raises(ValueError, match="no materials"):
        lens.read_lens_materials(_glb(document))


def test_node_role_identifies_a_lens_without_material_role(document):
    document["materials"][1].pop("extras")
    document["nodes"][1]["extras"] = {"partRole": "lens"}
    assert 1 in [row["material_index"] for row in lens.read_lens_materials(_glb(document))["materials"]]


def test_shared_mesh_cannot_borrow_lens_role_from_another_node(document):
    document["materials"][1].pop("extras")
    document["materials"][1]["name"] = "Shared surface"
    document["nodes"][1]["extras"] = {"partRole": "lens"}
    document["nodes"].append({"mesh": 1, "name": "Unlabelled instance"})
    assert 1 not in [row["material_index"] for row in lens.read_lens_materials(_glb(document))["materials"]]
    document["nodes"][-1]["extras"] = {"partRole": "lens"}
    assert 1 in [row["material_index"] for row in lens.read_lens_materials(_glb(document))["materials"]]


def test_primitive_only_role_does_not_grant_authority_ignored_by_ar(document):
    document["materials"][1].pop("extras")
    document["materials"][1]["name"] = "Unlabelled surface"
    document["meshes"][1]["primitives"][0]["extras"] = {"partRole": "lens"}
    assert 1 not in [row["material_index"] for row in lens.read_lens_materials(_glb(document))["materials"]]


def test_legacy_role_conflict_cannot_override_ar_material_classification(document):
    document["materials"][1]["extras"] = {"part_role": "frame"}
    document["nodes"][1]["extras"] = {"partRole": "lens"}
    assert 1 not in [row["material_index"] for row in lens.read_lens_materials(_glb(document))["materials"]]
    document["materials"][1]["extras"] = {"part_role": "LENS_left"}
    assert 1 in [row["material_index"] for row in lens.read_lens_materials(_glb(document))["materials"]]


def test_explicit_role_case_and_whitespace_match_ar(document):
    document["materials"][1]["extras"] = {"partRole": "  LENS  "}
    assert 1 in [row["material_index"] for row in lens.read_lens_materials(_glb(document))["materials"]]


@pytest.mark.parametrize("role", [True, 1, ["lens"], {"value": "lens"}])
def test_invalid_role_data_refuses_even_with_other_lens_tag(document, role):
    document["materials"][1]["extras"] = {"partRole": role}
    document["nodes"][1]["extras"] = {"partRole": "lens"}
    assert 1 not in [row["material_index"] for row in lens.read_lens_materials(_glb(document))["materials"]]


def test_lens_must_have_a_default_scene_triangle_use(document):
    document["scenes"] = [{"nodes": [0, 2]}, {"nodes": [1]}]
    assert [row["material_index"] for row in lens.read_lens_materials(_glb(document))["materials"]] == [2]
    document["scene"] = 1
    assert [row["material_index"] for row in lens.read_lens_materials(_glb(document))["materials"]] == [1]
    document["meshes"][1]["primitives"][0]["mode"] = 1
    with pytest.raises(ValueError, match="no materials"):
        lens.read_lens_materials(_glb(document))


@pytest.mark.parametrize("extras", [None, 1, "metadata", [], ["lens"]])
def test_arbitrary_nonobject_gltf_extras_do_not_grant_lens_role(document, extras):
    document["materials"][1]["extras"] = extras
    assert 1 not in [row["material_index"] for row in lens.read_lens_materials(_glb(document))["materials"]]


@pytest.mark.parametrize("kind", ["header", "truncated", "duplicate_json", "nan"])
def test_invalid_glb_refused(document, kind):
    payload = _glb(document)
    if kind == "header": payload = b"oops" + payload[4:]
    if kind == "truncated": payload = payload[:-1]
    if kind in ("duplicate_json", "nan"):
        raw = b'{"materials": [], "materials": []}' if kind == "duplicate_json" else b'{"materials": [], "value": NaN}'
        raw += b" " * (-len(raw) % 4)
        payload = struct.pack("<4sIIII", b"glTF", 2, len(raw) + 20, len(raw), 0x4E4F534A) + raw
    with pytest.raises(ValueError):
        lens.read_lens_materials(payload)


@pytest.fixture
def source(tmp_path):
    inputs = tmp_path / "input"
    inputs.mkdir()
    Image.new("RGB", (16, 12), "navy").save(inputs / "front.png")
    views = tmp_path / "views.json"
    views.write_text(json.dumps({"front.png": {"kind": "front", "side": "both"}}))
    root = jobs.create_job(name="Owner fixture", description="Owner words.", images=inputs, views=views,
                           jobs_root=tmp_path / "jobs")
    scene, result = make_finished(root)
    delivery.deliver_finished(root, scene, result)
    loaded = delivery.load_delivery(root)
    row = lens.read_lens_materials(loaded["model"])["materials"][0]
    values = _selection(Path(loaded["model"]).read_bytes(), row["material_index"])
    return root, loaded, values


def _fake_capture(root, export, call_id):
    """Small deterministic local renderer stand-in; all file hashes are real."""
    folder = root / "work/arview" / call_id
    folder.mkdir(parents=True)
    image = folder / "front.png"
    Image.new("RGB", (24, 18), "purple").save(image)
    records = [{"file": image.name, "sha256": edits.sha(image), "pose": "front", "variant": "beauty"}]
    report = folder / "report.json"
    report.write_text(json.dumps({"schema": "arview.capture/1", "status": "captured", "exit_code": 0,
        "input": {"sha256": export["sha256"]}, "request": {"fixed_scale": True}, "images": records,
        "warnings": [], "errors": []}))
    return {"status": "ok", "exit_code": 0, "fixed_scale": True, "appearance_policy": "g3d.ar_appearance/1",
        "lens_env_intensity": 1, "checkpoint": export["checkpoint"], "export_sha256": export["sha256"],
        "glb_sha256": export["sha256"], "report": str(report), "report_sha256": edits.sha(report),
        "sheets": [str(image)], "images": [{"path": str(image), "sha256": edits.sha(image)}]}


def test_save_is_new_delivery_source_unchanged_and_existing_review_verifies(source, tmp_path, monkeypatch):
    root, loaded, values = source
    before = edits.tree_hashes(root)
    monkeypatch.setattr(arview, "capture", _fake_capture)
    target = lens.save_lens_edit(root, loaded["id"], values, destination=tmp_path / "new-version", tool_test=True)
    saved = delivery.load_delivery(target)
    assert saved["id"] != loaded["id"] and saved["glb_sha256"] != loaded["glb_sha256"]
    assert edits.tree_hashes(root) == before
    assert saved["report_data"]["accepted_finish"] is False
    assert saved["report_data"]["exit_checkpoint"]["kind"] == "owner_lens_glb"
    app = edits.read(Path(saved["report"]).parent / saved["report_data"]["owner_applied"]["path"])
    assert app["owner_lens_edit"]["patch"]["values"] == values
    assert app["origin"] == "owner_lens_edit" and app["api_requests"] == 0
    assert app["owner_lens_edit"]["native_scene_available"] is False
    assert not list(target.rglob("*.blend"))
    assert jobs.load_job(target)["description"] == "Owner words."
    monkeypatch.setattr(review, "_serve", lambda *args: ({"url": "http://127.0.0.1:9001/", "pid": 10,
        "instance": "fixture", "port": 9001, "report_url": "fixture"}, target / "state.json", "spawn", target / "log"))
    monkeypatch.setattr(review, "_verify", lambda state, model, digest, *args, **kwargs:
                        {"status": "ready", "warnings": []} if edits.sha(model) == digest else pytest.fail("bad model"))
    review.review_job(target, saved["id"], port=0)
    assert review.reviewed_delivery(target)["glb_sha256"] == saved["glb_sha256"]
    assert edits.tree_hashes(root) == before


def test_preview_is_temporary_and_cannot_write_inside_source(source, tmp_path):
    root, loaded, values = source
    before = edits.tree_hashes(root)
    preview = lens.create_preview(root, loaded["id"], values, destination=tmp_path / "preview", open_ar=False)
    assert preview["temporary"] is True
    assert not (Path(preview["root"]) / "deliveries").exists()
    assert edits.sha(preview["model"]) == preview["glb_sha256"]
    assert edits.tree_hashes(root) == before
    with pytest.raises(ValueError, match="outside"):
        lens.create_preview(root, loaded["id"], values, destination=root / "bad", open_ar=False)
    assert edits.tree_hashes(root) == before


def test_repeated_save_preserves_previous_generation(source, tmp_path, monkeypatch):
    root, loaded, values = source
    monkeypatch.setattr(arview, "capture", _fake_capture)
    first = lens.save_lens_edit(root, loaded["id"], values, destination=tmp_path / "first")
    saved = delivery.load_delivery(first)
    before = edits.tree_hashes(first)
    index = next(iter(values))
    values[index]["metallic"] = .1
    second = lens.save_lens_edit(first, saved["id"], values, destination=tmp_path / "second")
    assert delivery.load_delivery(second)["glb_sha256"] != saved["glb_sha256"]
    assert edits.tree_hashes(first) == before


def test_retry_keeps_interrupted_evidence_and_binds_exact_values(source, tmp_path, monkeypatch):
    root, loaded, values = source
    target = tmp_path / "interrupted"
    def interrupted(*args):
        raise RuntimeError("simulated interruption")
    monkeypatch.setattr(arview, "capture", interrupted)
    with pytest.raises(RuntimeError, match="interruption"):
        lens.save_lens_edit(root, loaded["id"], values, destination=target)
    before = edits.tree_hashes(target)
    monkeypatch.setattr(arview, "capture", _fake_capture)
    completed = lens.save_lens_edit(root, loaded["id"], values, destination=target)
    assert completed != target and completed.name.startswith("interrupted-retry-")
    assert edits.tree_hashes(target) == before
    assert delivery.load_delivery(completed)["finished"]
    assert lens.save_lens_edit(root, loaded["id"], values, destination=completed) == completed
    # Simulate loss of the task registry result after the retry was published.
    # The original deterministic destination must rediscover the same version.
    assert lens.save_lens_edit(root, loaded["id"], values, destination=target) == completed
    assert len(list(tmp_path.glob("interrupted-retry-*"))) == 1
    changed = copy.deepcopy(values)
    changed[next(iter(changed))]["metallic"] = .05
    with pytest.raises(ValueError, match="different lens edit"):
        lens.save_lens_edit(root, loaded["id"], changed, destination=target)


@pytest.mark.parametrize("check", ["spec", "print", "ar"])
def test_failing_existing_checks_never_publish_a_delivery(source, tmp_path, monkeypatch, check):
    from glasses3d.agent import print_freshness
    from glasses3d.g3d_host import spec
    root, loaded, values = source
    original = edits.tree_hashes(root)
    target = tmp_path / "refused"
    monkeypatch.setattr(arview, "capture", _fake_capture)
    if check == "spec":
        monkeypatch.setattr(spec, "validate_spec", lambda *args: {"ok": False})
    elif check == "print":
        monkeypatch.setattr(print_freshness, "check_current_print", lambda *args: {"ok": False})
    else:
        monkeypatch.setattr(arview, "capture", lambda *args: {"status": "refused"})
    with pytest.raises(ValueError):
        lens.save_lens_edit(root, loaded["id"], values, destination=target)
    assert not (target / "deliveries/current.json").exists()
    assert edits.tree_hashes(root) == original
