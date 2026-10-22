"""Around-a-point gradients: independent image/affine geometry and saved-look checks."""
from __future__ import annotations

import copy
import io
from pathlib import Path

import numpy as np
from PIL import Image
import pytest

from glasses3d.agent import delivery, edits, owner_lens_edit as lens, owner_look_edit as look
from glasses3d.agent import owner_look_gradient as gradient
from glasses3d.agent.tools import arview
from glasses3d.g3d.printuv.glbmesh import accessor
from glasses3d.tests.test_owner_lens_edit import source, _fake_capture
from glasses3d.tests.test_owner_look_edit import encode, fixture, selected, spec, uv_values


def radial(*, centre=(0, 0), roundness=1, blend=0):
    return {"direction": "around_point", "centre": list(centre), "roundness": roundness,
            "blend": blend, "colours": [{"colour": [0, 1, 0], "share": 25},
                {"colour": [1, 0, 0], "share": 50}, {"colour": [0, 0, 1], "share": 25}]}


def texture(value):
    return np.asarray(Image.open(io.BytesIO(gradient.image_bytes(value))))


def pixel(image, uv):
    uv = np.asarray(uv)
    xy = np.floor(np.clip(uv, 0, 1) * np.array([image.shape[1], image.shape[0]])).astype(int)
    xy = np.minimum(xy, [image.shape[1] - 1, image.shape[0] - 1])
    return image[xy[1], xy[0], :3].tolist()


def rectangle_uv(vertices, uv, point):
    """Independent affine interpolation inside each coarse rectangle's triangles."""
    x = (point[0] - vertices[0, 0]) / (vertices[1, 0] - vertices[0, 0])
    y = (point[1] - vertices[0, 1]) / (vertices[3, 1] - vertices[0, 1])
    return uv[0] + x * (uv[1] - uv[0]) + y * (uv[3] - uv[0])


def patched_fixture(*, shield=False, **parameters):
    doc, binary = fixture(shield=shield, existing_uv=True)
    original = encode(doc, binary)
    values = selected(original)
    values["gradient"] = radial(**parameters)
    patched, receipt = look.patch_glb(original, {0: values})
    vertices = accessor(doc, binary, 0)
    return original, patched, receipt, values, vertices, uv_values(patched)


def test_ring_shares_and_blend_use_the_existing_linear_region_rules():
    sharp = radial()
    soft = radial(blend=1)
    assert gradient.sample_gradient(sharp, [0, .249, .25, .749, .75, 1]).tolist() == [
        [0, 1, 0], [0, 1, 0], [1, 0, 0], [1, 0, 0], [0, 0, 1], [0, 0, 1]]
    assert gradient.sample_gradient(soft, [.125, .25, .375, .75, .875]).tolist() == [
        [0, 1, 0], [.5, .5, 0], [1, 0, 0], [.5, 0, .5], [0, 0, 1]]
    image = texture(sharp)
    assert image.shape == (1024, 1024, 4)
    for radius, expected in [(0, [0, 255, 0]), (.2, [0, 255, 0]),
                             (.5, [255, 0, 0]), (.9, [0, 0, 255])]:
        assert pixel(image, [.5 + radius / 2, .5]) == expected
        assert pixel(image, [.5, .5 + radius / 2]) == expected
    # Share width, not ring area, decides the boundary.
    wider = radial()
    wider["colours"][0]["share"] = 60
    wider["colours"][1]["share"] = 15
    assert pixel(texture(wider), [.75, .5]) == [0, 255, 0]
    # At a half blend the PNG encodes linear .5 as sRGB about 188, not 128.
    blended = pixel(texture(soft), [.625, .5])
    assert 185 <= blended[0] <= 190 and 185 <= blended[1] <= 190 and blended[2] == 0


@pytest.mark.parametrize("shield", [False, True])
def test_circle_is_isotropic_on_wide_shield_and_coarse_pair_without_centre_vertices(shield):
    _, _, _, values, vertices, uv = patched_fixture(shield=shield)
    image = texture(values["gradient"])
    for start in range(0, len(vertices), 4):
        p, tex = vertices[start:start + 4], uv[start:start + 4]
        centre = (p[0, :2] + p[2, :2]) / 2
        radius = (p[2, 1] - p[0, 1]) / 2
        # One equal canonical displacement has equal UV magnitude in either axis.
        unit_x = (tex[1] - tex[0]) / (p[1, 0] - p[0, 0])
        unit_y = (tex[3] - tex[0]) / (p[3, 1] - p[0, 1])
        assert np.linalg.norm(unit_x) == pytest.approx(np.linalg.norm(unit_y), rel=1e-6)
        assert pixel(image, rectangle_uv(p, tex, centre)) == [0, 255, 0]
        for r, expected in [(.2, [0, 255, 0]), (.5, [255, 0, 0]), (.9, [0, 0, 255])]:
            for axis in [(1, 0), (0, 1), (.6, .8)]:
                assert pixel(image, rectangle_uv(p, tex, centre + radius * r * np.array(axis))) == expected
        # All four original vertices can be outside the circle. They must NOT
        # interpolate an outside-only radius over the otherwise green centre.
        assert all(pixel(image, item) == [0, 0, 255] for item in tex)


def test_moved_pair_centres_are_mirrored_in_x_and_both_move_up():
    _, patched, receipt, values, vertices, uv = patched_fixture(centre=(.6, .4))
    image = texture(values["gradient"])
    centres = []
    for start, sign in [(0, -1), (4, 1)]:
        p, tex = vertices[start:start + 4], uv[start:start + 4]
        original = (p[0, :2] + p[2, :2]) / 2
        half = (p[2, :2] - p[0, :2]) / 2
        centre = original + half * [.6 * sign, .4]
        centres.append(centre)
        assert np.allclose(rectangle_uv(p, tex, centre), [.5, .5])
        assert pixel(image, rectangle_uv(p, tex, centre)) == [0, 255, 0]
        assert centre[1] > original[1]
    assert centres[0][0] == pytest.approx(-centres[1][0])
    assert receipt["values"]["0"]["gradient"] == values["gradient"]
    assert selected(patched)["gradient"] == values["gradient"]


def test_roundness_is_horizontal_axis_ratio_and_increases_horizontal_reach():
    _, _, _, values, p, uv = patched_fixture(shield=True, roundness=3)
    image = texture(values["gradient"])
    centre = (p[0, :2] + p[2, :2]) / 2
    radius = (p[2, 1] - p[0, 1]) / 2
    # x = 3 * y lies on one ellipse; unscaled x remains in the centre region.
    assert pixel(image, rectangle_uv(p, uv, centre + [radius * 1.5, 0])) == [255, 0, 0]
    assert pixel(image, rectangle_uv(p, uv, centre + [0, radius * .5])) == [255, 0, 0]
    assert pixel(image, rectangle_uv(p, uv, centre + [radius * .5, 0])) == [0, 255, 0]
    assert np.linalg.norm((uv[1] - uv[0]) / (p[1, 0] - p[0, 0])) == pytest.approx(
        np.linalg.norm((uv[3] - uv[0]) / (p[3, 1] - p[0, 1])), rel=1e-6)


def test_moved_wide_shield_uses_shared_centre_and_clamps_only_end_colour():
    _, patched, _, values, p, uv = patched_fixture(shield=True, centre=(1, -1), roundness=4)
    image = texture(values["gradient"])
    centre = np.array([p[1, 0], p[1, 1]])
    assert pixel(image, rectangle_uv(p, uv, centre)) == [0, 255, 0]
    assert pixel(image, rectangle_uv(p, uv, centre - [.12, 0])) == [0, 0, 255]
    assert pixel(image, [-10, .5]) == [0, 0, 255]
    assert pixel(image, [.5, 10]) == [0, 0, 255]
    projection = look.read_materials(patched)["materials"][0]["radial_projection"]
    assert projection["mode"] == "single" and projection["surfaces"][0]["mirror_x"] == 1


def test_radial_clamp_edges_keep_end_colour_even_for_sub_texel_end_share():
    value = radial()
    value["colours"][1]["share"] = 74.9999
    value["colours"][2]["share"] = .0001
    image = texture(value)
    for uv in [(-1, .5), (2, .5), (.5, -1), (.5, 2)]:
        assert pixel(image, uv) == [0, 0, 255]


def sheets_fixture(sheets):
    """Separate disconnected sheets and materials, with no product identifiers."""
    doc, _ = fixture(shield=True)
    doc.update(bufferViews=[], accessors=[], meshes=[{"primitives": []}], nodes=[{"mesh": 0}])
    doc["scenes"][0]["nodes"] = [0]
    doc["materials"] = [{"name": f"Glass {i}", "extras": {"partRole": "lens"}} for i in range(2)]
    binary = b""
    for xmin, xmax, ymin, ymax, z, material in sheets:
        positions = np.array([[xmin, ymin, z], [xmax, ymin, z], [xmax, ymax, z], [xmin, ymax, z]], dtype="<f4")
        start = len(binary)
        binary += positions.tobytes()
        doc["bufferViews"].append({"buffer": 0, "byteOffset": start, "byteLength": positions.nbytes})
        pi = len(doc["accessors"])
        doc["accessors"].append({"bufferView": len(doc["bufferViews"]) - 1, "componentType": 5126, "type": "VEC3", "count": 4})
        start = len(binary)
        binary += np.array([0, 1, 2, 0, 2, 3], dtype="<u2").tobytes()
        doc["bufferViews"].append({"buffer": 0, "byteOffset": start, "byteLength": 12})
        doc["accessors"].append({"bufferView": len(doc["bufferViews"]) - 1, "componentType": 5123, "type": "SCALAR", "count": 6})
        doc["meshes"][0]["primitives"].append({"attributes": {"POSITION": pi}, "indices": pi + 1, "material": material})
    doc["buffers"][0]["byteLength"] = len(binary)
    return doc, binary


@pytest.mark.parametrize("shield", [False, True])
def test_disconnected_front_back_sheets_and_materials_decide_pair_or_shared_shield(shield):
    bounds = [(-.08, .08)] if shield else [(-.08, -.02), (.02, .08)]
    # The back sheets are inset and vertically displaced: a per-sheet centre
    # would create four rings/handles on this pair instead of one per lens.
    sheets = [(lo + inset, hi - inset, -.02 + inset, .02, z, side)
              for side, (lo, hi) in enumerate(bounds) for z, inset in [(0, 0), (-.003, .004)]]
    doc, binary = sheets_fixture(sheets)
    original = encode(doc, binary)
    values = {row["material_index"]: {**row["values"], "gradient": radial(centre=(.5, .25))}
              for row in look.read_materials(original)["materials"]}
    payload, receipt = look.patch_glb(original, values)
    result, chunks = lens._parse(payload)
    for index, row in enumerate(look.read_materials(payload)["materials"]):
        projection = row["radial_projection"]
        assert projection["mode"] == ("single" if shield else "pair")
        assert len(projection["surfaces"]) == 2
        a, b = projection["surfaces"]
        assert a["centre"] == b["centre"] and a["half_extent"] == b["half_extent"]
        assert a["lens_id"] == b["lens_id"] == (0 if shield else -1 if index == 0 else 1)
        assert a["vertex_ranges"] == [[0, 3]]
        assert a["mirror_x"] == (1 if shield or index else -1)
        assert selected(payload, index)["gradient"] == values[index]["gradient"]
    # Metadata returns fresh copies; browser/UI consumers cannot poison its cache.
    look.read_materials(payload)["materials"][0]["radial_projection"]["surfaces"].clear()
    assert len(look.read_materials(payload)["materials"][0]["radial_projection"]["surfaces"]) == 2
    assert chunks[1][1][:len(binary)] == binary
    assert receipt["changed_material_indices"] == list(values)
    for primitive in result["meshes"][0]["primitives"]:
        assert primitive["attributes"]["POSITION"] in [0, 2, 4, 6]


def test_centre_only_edits_reuse_png_but_change_affine_uvs():
    first = radial(centre=(.123, -.23), roundness=2.345, blend=.678)
    moved = {**first, "centre": [-.65, .4]}
    before = gradient._image_bytes.cache_info()
    a, b = gradient.image_bytes(first), gradient.image_bytes(moved)
    after = gradient._image_bytes.cache_info()
    assert a is b and after.misses - before.misses <= 1 and after.hits > before.hits
    doc, binary = fixture()
    row, xmin, xmax = gradient.projection_data(doc, binary, {0})
    assert gradient.coordinates(row[0], "around_point", xmin, xmax, first) != gradient.coordinates(
        row[0], "around_point", xmin, xmax, moved)


def test_radial_edit_repeated_changes_and_solid_restore_preserve_every_original_byte_range():
    original, payload, receipt, values, _, _ = patched_fixture(centre=(.23, -.4), roundness=2.7)
    original_doc, original_chunks = lens._parse(original)
    for next_spec in [radial(centre=(-.5, .8), roundness=4), spec("left_to_right"), radial(), None]:
        old_doc, old_chunks = lens._parse(payload)
        values["gradient"] = next_spec
        payload, receipt = look.patch_glb(payload, {0: values})
        after, chunks = lens._parse(payload)
        assert chunks[1][1][:len(old_chunks[1][1])] == old_chunks[1][1]
        assert chunks[2:] == original_chunks[2:]
        assert after["materials"][1:] == original_doc["materials"][1:]
        assert after["accessors"][:len(old_doc["accessors"])] == old_doc["accessors"]
        assert after["bufferViews"][:len(old_doc["bufferViews"])] == old_doc["bufferViews"]
        assert after["meshes"][0]["primitives"][0]["attributes"]["TEXCOORD_0"] == 2
        assert receipt["values"]["0"] == values
        assert selected(payload) == values
        assert look.patch_glb(payload, {0: values})[0] == payload


def test_radial_saved_receipt_and_reapplication_keep_parameters_and_native_source(source, tmp_path, monkeypatch):
    root, loaded, _ = source
    before = edits.tree_hashes(root)
    monkeypatch.setattr(arview, "capture", _fake_capture)
    row = next(row for row in look.read_materials(loaded["model"])["materials"] if row["role"] == "lens")
    value = {**row["values"], "gradient": radial(centre=(.25, -.3), roundness=2.25, blend=.65)}
    target = look.save_look_edit(root, loaded["id"], {row["material_index"]: value},
                                 destination=tmp_path / "radial-version", tool_test=True)
    saved = delivery.load_delivery(target)
    record = look.read_saved_look(target)
    assert record["look"]["entries"][0]["values"] == value
    assert record["native_source"]["glb_sha256"] == loaded["glb_sha256"]
    application = look._application(saved)
    assert application["owner_lens_edit"]["patch"]["values"][str(row["material_index"])] == value
    mapping = look.map_look(record["look"], loaded["model"])
    assert mapping["skipped"] == []
    reapplied = look.save_look_edit(root, loaded["id"], mapping["values"], destination=tmp_path / "reapplied",
                                   look=record["look"], mapping=mapping, tool_test=True)
    assert Path(delivery.load_delivery(reapplied)["model"]).read_bytes() == Path(saved["model"]).read_bytes()
    assert look.read_saved_look(reapplied)["look"] == record["look"]
    assert edits.tree_hashes(root) == before


def test_radial_look_reapplies_parameters_to_new_geometry_and_recomputes_centres():
    original, _, _, values, _, _ = patched_fixture(centre=(.4, -.2), roundness=1.8)
    saved = look.build_look(original, {0: values})
    doc, binary = sheets_fixture([(-.12, -.03, -.03, .03, 0, 0), (.03, .12, -.03, .03, 0, 1)])
    target = encode(doc, binary)
    mapped = look.map_look(saved, target, mode="copy", same_job=False)
    assert mapped["skipped"] == [] and set(mapped["values"]) == {"0", "1"}
    assert all(row["gradient"] == values["gradient"] for row in mapped["values"].values())
    patched, _ = look.patch_glb(target, mapped["values"])
    rows = look.read_materials(patched)["materials"]
    assert rows[0]["radial_projection"]["surfaces"][0]["centre"][0] == pytest.approx(-.075)
    assert rows[1]["radial_projection"]["surfaces"][0]["centre"][0] == pytest.approx(.075)
    assert all(row["values"]["gradient"] == values["gradient"] for row in rows)


def test_saved_radial_centre_tampering_is_refused_even_though_png_is_unchanged():
    _, payload, _, _, _, _ = patched_fixture(centre=(.2, -.4), roundness=2)
    doc, chunks = lens._parse(payload)
    marker = doc["materials"][0]["extras"][gradient.KEY]
    old_image = gradient.image_bytes(marker["spec"])
    marker["spec"]["centre"] = [-.7, .3]
    assert gradient.image_bytes(marker["spec"]) == old_image
    with pytest.raises(ValueError, match="gradient metadata"):
        look.read_materials(encode(doc, chunks[1][1]))


@pytest.mark.parametrize("field,value", [("centre", None), ("centre", [0]), ("centre", [0, 0, 0]),
    ("centre", [True, 0]), ("centre", [float("nan"), 0]), ("centre", [1.001, 0]),
    ("centre", [0, -1.001]), ("roundness", .99), ("roundness", 4.01),
    ("roundness", True), ("roundness", float("inf"))])
def test_invalid_radial_parameters_refuse(field, value):
    candidate = radial()
    candidate[field] = value
    with pytest.raises(ValueError):
        gradient.validate_gradient(candidate)


def test_old_directions_keep_exact_schema_and_radial_requires_both_parameters():
    for direction in ["centre_to_sides", "top_to_bottom", "left_to_right"]:
        old = spec(direction)
        assert gradient.validate_gradient(old) == old
        with pytest.raises(ValueError):
            gradient.validate_gradient({**old, "centre": [0, 0], "roundness": 1})
    for key in ["centre", "roundness"]:
        missing = radial()
        missing.pop(key)
        with pytest.raises(ValueError):
            gradient.validate_gradient(missing)
