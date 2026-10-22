"""General appearance preservation, coarse/no-UV gradients and reusable mapping."""
from __future__ import annotations

import copy
import io
import json
from pathlib import Path
import struct

import numpy as np
from PIL import Image
import pytest

from glasses3d.agent import delivery, edits, owner_lens_edit as lens, owner_look_edit as look
from glasses3d.agent import owner_look_gradient as gradient
from glasses3d.agent.tools import arview
from glasses3d.g3d.printuv.glbmesh import accessor
from glasses3d.tests.test_owner_lens_edit import source, _fake_capture


def encode(doc, binary, extra=b"keep me!"):
    raw = json.dumps(doc, indent=2).encode()
    raw += b" " * (-len(raw) % 4)
    binary += b"\0" * (-len(binary) % 4)
    body = b"".join(struct.pack("<II", len(data), kind) + data
                    for kind, data in ((0x4E4F534A, raw), (0x004E4942, binary), (0x12345678, extra)))
    return struct.pack("<4sII", b"glTF", 2, len(body) + 12) + body


def fixture(*, shield=False, existing_uv=False):
    # Two disconnected coarse planes in ONE primitive, with different heights.
    # This prevents a per-mesh or global-Y implementation from passing the pair test.
    positions = [[-.07, 0, 0], [-.01, 0, 0], [-.01, .03, 0], [-.07, .03, 0],
                 [.01, -.01, 0], [.07, -.01, 0], [.07, .05, 0], [.01, .05, 0]]
    triangles = [0, 1, 2, 0, 2, 3, 4, 5, 6, 4, 6, 7]
    if shield:
        positions = [[-.07, 0, 0], [.07, 0, 0], [.07, .03, 0], [-.07, .03, 0]]
        triangles = [0, 1, 2, 0, 2, 3]
    binary = np.asarray(positions, dtype="<f4").tobytes()
    index_offset = len(binary)
    binary += np.asarray(triangles, dtype="<u2").tobytes()
    doc = {"asset": {"version": "2.0", "extras": {"keep": True}}, "scene": 0,
        "scenes": [{"nodes": [0, 1, 2]}],
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": index_offset},
                        {"buffer": 0, "byteOffset": index_offset, "byteLength": len(binary) - index_offset}],
        "accessors": [{"bufferView": 0, "componentType": 5126, "count": len(positions), "type": "VEC3"},
                      {"bufferView": 1, "componentType": 5123, "count": len(triangles), "type": "SCALAR"}],
        "materials": [{"name": "Optics", "extras": {"partRole": "lens"}, "alphaMode": "BLEND",
                       "pbrMetallicRoughness": {"baseColorFactor": [.2, .3, .4, .8]}},
                      {"name": "Acetate", "extras": {"partRole": "frame"},
                       "extensions": {"KHR_materials_transmission": {"transmissionFactor": .75}}},
                      {"name": "Arms", "extras": {"partRole": "temple"}}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1, "material": i}]} for i in range(3)],
        "nodes": [{"name": f"Part {i}", "mesh": i} for i in range(3)]}
    if existing_uv:
        offset = len(binary)
        uv = np.zeros((len(positions), 2), dtype="<f4").tobytes()
        binary += uv
        doc["bufferViews"].append({"buffer": 0, "byteOffset": offset, "byteLength": len(uv)})
        doc["accessors"].append({"bufferView": 2, "componentType": 5126, "count": len(positions), "type": "VEC2"})
        doc["meshes"][0]["primitives"][0]["attributes"]["TEXCOORD_0"] = 2
        doc["buffers"][0]["byteLength"] = len(binary)
    return doc, binary


def selected(source, index=0):
    return copy.deepcopy(next(row["values"] for row in look.read_materials(source)["materials"] if row["material_index"] == index))


def spec(direction="left_to_right", blend=.5):
    return {"direction": direction, "colours": [
        {"colour": [1, 0, 0], "share": 25}, {"colour": [0, 1, 0], "share": 50},
        {"colour": [0, 0, 1], "share": 25}], "blend": blend,
        **({"centre": [0, 0], "roundness": 1} if direction == "around_point" else {})}


@pytest.mark.parametrize("existing_uv", [False, True])
@pytest.mark.parametrize("direction", sorted(gradient.DIRECTIONS))
def test_gradient_appends_image_uv_and_receipts_without_changing_existing_ranges(direction, existing_uv):
    doc, binary = fixture(existing_uv=existing_uv)
    original = encode(doc, binary)
    values = selected(original)
    values["gradient"] = spec(direction)
    patched, receipt = look.patch_glb(original, {0: values})
    result, chunks = lens._parse(patched)
    after_binary = chunks[1][1]
    assert after_binary[:len(binary)] == binary
    assert chunks[2][2] == lens._parse(original)[1][2][2]
    assert result["bufferViews"][:len(doc["bufferViews"])] == doc["bufferViews"]
    assert result["accessors"][:len(doc["accessors"])] == doc["accessors"]
    assert result["materials"][1:] == doc["materials"][1:]
    assert result["nodes"] == doc["nodes"] and result["scenes"] == doc["scenes"]
    assert result["meshes"][1:] == doc["meshes"][1:]
    for key, value in doc["meshes"][0]["primitives"][0]["attributes"].items():
        assert result["meshes"][0]["primitives"][0]["attributes"][key] == value
    pbr = result["materials"][0]["pbrMetallicRoughness"]
    assert pbr["baseColorFactor"] == [1, 1, 1, .8]
    tc = pbr["baseColorTexture"]["texCoord"]
    assert tc == (1 if existing_uv else 0)
    image = result["images"][result["textures"][pbr["baseColorTexture"]["index"]]["source"]]
    view = result["bufferViews"][image["bufferView"]]
    raw = after_binary[view["byteOffset"]:view["byteOffset"] + view["byteLength"]]
    with Image.open(io.BytesIO(raw)) as im:
        assert im.size == (1024, 1024 if direction == "around_point" else 1)
        pixels = np.asarray(im)
    # These intermediate texels exist independently of the coarse mesh's eight
    # vertices. Mirrored gradients naturally repeat their texels on both halves.
    assert ((pixels[:, :, :3] > 0) & (pixels[:, :, :3] < 255)).any()
    assert receipt["existing_binary_ranges_unchanged"] and not receipt["binary_chunks_unchanged"]
    for addition in receipt["added"]:
        if addition["kind"] == "bytes":
            raw = after_binary[addition["byteOffset"]:addition["byteOffset"] + addition["byteLength"]]
            assert lens._sha(raw) == addition["sha256"]
        else:
            assert result[addition["kind"]][addition["index"]] == addition["value"]
    additions = [row for row in receipt["added"] if row["kind"] == "bytes"]
    assert sum(row["byteLength"] for row in additions) == len(after_binary) - len(binary)
    assert selected(patched) == values
    assert look.patch_glb(patched, {0: selected(patched)})[0] == patched


def uv_values(payload):
    doc, chunks = lens._parse(payload)
    pbr = doc["materials"][0]["pbrMetallicRoughness"]
    number = pbr["baseColorTexture"]["texCoord"]
    attribute = doc["meshes"][0]["primitives"][0]["attributes"][f"TEXCOORD_{number}"]
    return accessor(doc, chunks[1][1], attribute)


def test_top_bottom_pair_uses_each_disconnected_lens_extent_even_in_one_primitive():
    doc, binary = fixture()
    original = encode(doc, binary)
    values = selected(original)
    values["gradient"] = spec("top_to_bottom")
    patched, _ = look.patch_glb(original, {0: values})
    assert uv_values(patched)[:, 0].tolist() == [1, 1, 0, 0, 1, 1, 0, 0]


def test_centre_sides_coarse_shield_uses_linear_uv_and_mirrored_image():
    doc, binary = fixture(shield=True)
    original = encode(doc, binary)
    values = selected(original)
    values["gradient"] = spec("centre_to_sides")
    patched, _ = look.patch_glb(original, {0: values})
    assert uv_values(patched)[:, 0].tolist() == [0, 1, 1, 0]
    pixels = np.asarray(Image.open(io.BytesIO(gradient.image_bytes(values["gradient"]))))[0]
    assert np.array_equal(pixels, pixels[::-1])
    # A two-triangle shield has no centre vertex. Its centre nevertheless samples
    # the start colour, and both sides sample the end colour.
    assert pixels[511, 0] == 255 and pixels[0, 2] == 255


def test_gradient_shares_and_blend_have_independent_numerical_expectations():
    sample = gradient.sample_gradient(spec(blend=0), [0, .249, .25, .749, .75, 1])
    assert sample.tolist() == [[1, 0, 0], [1, 0, 0], [0, 1, 0], [0, 1, 0], [0, 0, 1], [0, 0, 1]]
    assert gradient.sample_gradient(spec(blend=1), [.25, .75]).tolist() == [[.5, .5, 0], [0, .5, .5]]
    assert gradient.sample_gradient(spec(blend=1), [.125, .375]).tolist() == [[1, 0, 0], [0, 1, 0]]


@pytest.mark.parametrize("mutate", [
    lambda s: s.update(direction="random"), lambda s: s.update(blend=1.01),
    lambda s: s.update(blend=True), lambda s: s["colours"][0].update(share=0),
    lambda s: s["colours"][0].update(share=26), lambda s: s["colours"][0].update(colour=[2, 0, 0]),
    lambda s: s["colours"].pop(), lambda s: s.update(unknown=1),
])
def test_invalid_gradient_rules_refuse(mutate):
    value = spec()
    mutate(value)
    with pytest.raises(ValueError):
        gradient.validate_gradient(value)


def test_change_gradient_then_solid_keeps_all_old_assets_and_restores_solid_tint():
    doc, binary = fixture()
    original = encode(doc, binary)
    values = selected(original)
    values["gradient"] = spec()
    first, _ = look.patch_glb(original, {0: values})
    values["gradient"] = spec("top_to_bottom")
    second, _ = look.patch_glb(first, {0: values})
    first_doc, first_chunks = lens._parse(first)
    second_doc, second_chunks = lens._parse(second)
    assert second_chunks[1][1][:len(first_chunks[1][1])] == first_chunks[1][1]
    assert len(second_doc["images"]) == len(first_doc["images"]) + 1
    values["gradient"] = None
    final, receipt = look.patch_glb(second, {0: values})
    final_doc, final_chunks = lens._parse(final)
    assert final_chunks[1:] == second_chunks[1:]
    assert final_doc["images"] == second_doc["images"]
    assert "baseColorTexture" not in final_doc["materials"][0]["pbrMetallicRoughness"]
    assert selected(final) == values
    assert receipt["added"] == []


def test_pattern_is_multiplied_by_solid_tint_and_never_replaced_by_gradient():
    doc, binary = fixture()
    for material in doc["materials"]:
        material.setdefault("pbrMetallicRoughness", {})["baseColorTexture"] = {"index": 0, "texCoord": 2}
    doc["textures"] = [{"source": 0}]
    doc["images"] = [{"uri": "data:image/png;base64,test"}]
    original = encode(doc, binary)
    values = selected(original, 1)
    values["tint"] = [.2, .4, .8]
    values["transmission"] = .4
    after, receipt = look.patch_glb(original, {1: values})
    result, chunks = lens._parse(after)
    assert result["materials"][1]["pbrMetallicRoughness"]["baseColorTexture"] == {"index": 0, "texCoord": 2}
    assert result["images"] == doc["images"] and chunks[1][1] == binary
    assert receipt["binary_chunks_unchanged"]
    assert look.read_materials(original)["materials"][1]["colour_note"] == "Colour tints the existing pattern."
    values = selected(original)
    values["gradient"] = spec()
    with pytest.raises(ValueError, match="already has a colour texture"):
        look.patch_glb(original, {0: values})


@pytest.mark.parametrize("change", ["conflict", "unlabelled_use", "laser", "signature", "no_role"])
def test_shared_untagged_and_marking_materials_have_plain_refusals(change):
    doc, binary = fixture()
    if change == "conflict": doc["nodes"][0]["extras"] = {"partRole": "frame"}
    if change == "unlabelled_use":
        doc["materials"][0].pop("extras")
        doc["nodes"][0]["extras"] = {"partRole": "lens"}
        doc["nodes"].append({"mesh": 0})
    if change == "laser": doc["materials"][0]["name"] = "Subtle lens laser marks"
    if change == "signature": doc["nodes"][0]["name"] = "TOM FORD • lens signature"
    if change == "no_role": doc["materials"][0].pop("extras")
    metadata = look.read_materials(encode(doc, binary))
    assert 0 not in [row["material_index"] for row in metadata["materials"]]
    assert metadata["refusals"][0]["reason"]


def test_hardware_uses_declared_support_and_crystal_uses_node_role():
    doc, binary = fixture()
    doc["materials"][1]["extras"] = {"partRole": "hardware"}
    doc["nodes"][1]["extras"] = {"partRole": "frame"}
    assert look.read_materials(encode(doc, binary))["materials"][1]["role"] == "frame"
    doc["materials"][1].pop("extras")
    assert look.read_materials(encode(doc, binary))["materials"][1]["can_transmission"] is True


def test_copy_single_lens_broadcasts_but_multiple_lens_materials_require_names():
    doc, binary = fixture()
    original = encode(doc, binary)
    values = selected(original)
    values["tint"] = [.8, .6, .4]
    saved = look.build_look(original, {0: values})
    target = copy.deepcopy(doc)
    target["materials"].append({"name": "Other optics", "extras": {"partRole": "lens"}})
    target["meshes"].append({"primitives": [{"attributes": {"POSITION": 0}, "indices": 1, "material": 3}]})
    target["nodes"].append({"mesh": 3})
    target["scenes"][0]["nodes"].append(3)
    mapped = look.map_look(saved, encode(target, binary), mode="copy", same_job=False)
    assert set(mapped["values"]) == {"0", "3"}
    saved["entries"].append({"role": "lens", "material_name": "Missing optics", "values": values})
    mapped = look.map_look(saved, encode(target, binary), mode="copy", same_job=False)
    assert set(mapped["values"]) == {"0"} and len(mapped["skipped"]) == 1


def test_reapply_exact_role_names_and_copy_nonlenses_only_within_same_job():
    doc, binary = fixture()
    original = encode(doc, binary)
    saved = look.build_look(original)
    same = look.map_look(saved, original, mode="copy", same_job=True)
    other = look.map_look(saved, original, mode="copy", same_job=False)
    assert set(same["values"]) == {"0", "1", "2"}
    assert set(other["values"]) == {"0"} and len(other["skipped"]) == 2
    doc["materials"][0]["name"] = "New optical material"
    changed = look.map_look(saved, encode(doc, binary))
    assert set(changed["values"]) == {"1", "2"} and changed["skipped"][0]["material_name"] == "Optics"


def test_duplicate_target_names_are_not_chosen_arbitrarily():
    doc, binary = fixture()
    original = encode(doc, binary)
    saved = look.build_look(original, {1: selected(original, 1)})
    doc["materials"][2]["name"] = "Acetate"
    doc["materials"][2]["extras"]["partRole"] = "frame"
    mapped = look.map_look(saved, encode(doc, binary))
    assert mapped["values"] == {} and len(mapped["skipped"]) == 1


def test_transmission_control_does_not_make_opaque_nonlens_newly_transmissive():
    doc, binary = fixture()
    original = encode(doc, binary)
    values = selected(original, 2)
    values["transmission"] = .5
    with pytest.raises(ValueError, match="already carry transmission"):
        look.patch_glb(original, {2: values})
    values["transmission"] = 0
    values["roughness"] = .1
    patched, _ = look.patch_glb(original, {2: values})
    assert look.read_materials(patched)["materials"][2]["can_transmission"] is False
    assert "KHR_materials_transmission" not in lens._parse(patched)[0]["materials"][2]["extensions"]


def test_appearance_save_stores_cumulative_look_and_native_authority(source, tmp_path, monkeypatch):
    root, loaded, old_values = source
    values = {int(key): {**value, "gradient": None} for key, value in old_values.items()}
    monkeypatch.setattr(arview, "capture", _fake_capture)
    original = edits.tree_hashes(root)
    first = look.save_look_edit(root, loaded["id"], values, destination=tmp_path / "first", tool_test=True)
    saved = delivery.load_delivery(first)
    record = look.read_saved_look(first, saved["id"])
    assert record["native_source"]["root"] == str(root)
    assert record["native_source"]["delivery_id"] == loaded["id"]
    assert record["look"]["entries"] and record["glb_sha256"] == saved["glb_sha256"]
    assert look.resolve_native_source(first, saved["id"]) == record["native_source"]
    values[next(iter(values))]["metallic"] = .123
    second = look.save_look_edit(first, saved["id"], values, destination=tmp_path / "second", tool_test=True)
    record2 = look.read_saved_look(second)
    assert record2["native_source"] == record["native_source"]
    assert record2["look"]["entries"][0]["values"]["metallic"] == .123
    assert edits.tree_hashes(root) == original
    assert not list(first.rglob("*.blend"))


def test_skipped_look_entries_survive_noop_reapplication_with_receipt(source, tmp_path, monkeypatch):
    root, loaded, old_values = source
    monkeypatch.setattr(arview, "capture", _fake_capture)
    saved_look = {"schema": look.SCHEMA, "entries": [{"role": "lens", "material_name": "Removed lens",
                  "values": {**next(iter(old_values.values())), "gradient": None}}]}
    mapped = look.map_look(saved_look, loaded["model"])
    assert mapped["values"] == {} and mapped["skipped"]
    target = look.save_look_edit(root, loaded["id"], {}, destination=tmp_path / "reapply", look=saved_look, mapping=mapped)
    record = look.read_saved_look(target)
    assert delivery.load_delivery(target)["glb_sha256"] == loaded["glb_sha256"]
    assert record["look"] == saved_look and record["skipped"] == mapped["skipped"]
    assert look.save_look_edit(root, loaded["id"], {}, destination=target, look=saved_look, mapping=mapped) == target


def test_legacy_lens_edit_native_authority_chain_resolves(source, tmp_path, monkeypatch):
    root, loaded, values = source
    monkeypatch.setattr(arview, "capture", _fake_capture)
    legacy = lens.save_lens_edit(root, loaded["id"], values, destination=tmp_path / "legacy")
    saved = look.read_saved_look(legacy)
    assert saved["look"]["entries"][0]["values"]["tint"] == next(iter(values.values()))["tint"]
    assert look.resolve_native_source(legacy)["delivery_id"] == loaded["id"]


def test_many_gradient_changes_and_solid_switches_reuse_only_the_owner_uv_semantic():
    doc, binary = fixture(existing_uv=True)
    payload = encode(doc, binary)
    for i in range(10):
        old_doc, old_chunks = lens._parse(payload)
        values = selected(payload)
        values["gradient"] = None if i % 3 == 2 else spec("top_to_bottom" if i % 2 else "centre_to_sides")
        payload, receipt = look.patch_glb(payload, {0: values})
        after, chunks = lens._parse(payload)
        assert chunks[1][1][:len(old_chunks[1][1])] == old_chunks[1][1]
        attributes = after["meshes"][0]["primitives"][0]["attributes"]
        assert attributes["TEXCOORD_0"] == 2
        assert set(attributes) == {"POSITION", "TEXCOORD_0", "TEXCOORD_1"}
        if receipt["attribute_bindings_added"]:
            assert receipt["attribute_bindings_added"][0]["previous_accessor"] == old_doc["meshes"][0]["primitives"][0]["attributes"].get("TEXCOORD_1")


def test_saved_gradient_uv_tampering_is_refused():
    doc, binary = fixture()
    values = selected(encode(doc, binary))
    values["gradient"] = spec()
    payload, _ = look.patch_glb(encode(doc, binary), {0: values})
    after, chunks = lens._parse(payload)
    marker = after["materials"][0]["extras"][gradient.KEY]
    index = marker["bindings"][0]["accessor"]
    view = after["bufferViews"][after["accessors"][index]["bufferView"]]
    binary = bytearray(chunks[1][1])
    struct.pack_into("<f", binary, view["byteOffset"], .3)
    with pytest.raises(ValueError, match="gradient metadata"):
        look.read_materials(encode(after, bytes(binary)))


def test_inactive_transformed_helper_does_not_block_canonical_lens_gradient():
    doc, binary = fixture()
    doc["nodes"].append({"name": "Archived helper", "translation": [100, 0, 0]})
    original = encode(doc, binary)
    values = selected(original)
    values["gradient"] = spec()
    assert look.patch_glb(original, {0: values})[1]["changed_material_indices"] == [0]
