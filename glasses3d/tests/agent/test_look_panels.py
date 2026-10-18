import hashlib

import numpy as np
from PIL import Image
import pytest

from glasses3d.agent.tools import look_panels as P
from glasses3d.tests import glbfab


def fixture_glb(tmp_path, *, marking=True):
    # Deliberately meaningless names: selection must depend on roles/materials/geometry.
    parts = {
        "a": ((-25, 25, -15, 15, -1, 1), "clear", "lens"),
        "b": ((-30, 30, 15, 20, -4, 4), "ivory", "frame"),
        "c": ((-30, -25, -20, 15, -4, 4), "ivory", "frame"),
        "d": ((25, 30, -20, 15, -4, 4), "ivory", "frame"),
        "e": ((-30, 30, -20, -15, -4, 4), "ivory", "frame"),
        "f": ((-32, -29, 10, 16, -120, -4), "ivory", "temple_left"),
        "g": ((29, 32, 10, 16, -120, -4), "ivory", "temple_right"),
    }
    if marking:
        parts.update({"h": ((10, 16, 8, 10, 1.1, 1.2), "ink", "lens"),
                      "i": ((-16, -10, 8, 10, 3, 3.1), "ink", "lens")})
    materials = {
        "clear": {"pbrMetallicRoughness": {"baseColorFactor": [1, 1, 1, 1]},
                  "extensions": {"KHR_materials_transmission": {"transmissionFactor": .8}}},
        "ivory": {"pbrMetallicRoughness": {"baseColorFactor": [.8, .7, .5, 1]}},
        "ink": {"alphaMode": "BLEND", "pbrMetallicRoughness": {"baseColorFactor": [.5, .5, .5, .6]}},
    }
    path = tmp_path / "test.glb"
    path.write_bytes(glbfab.build(parts, materials=materials))
    return path


def camera(yaw=0):
    angle = np.radians(yaw)
    R = np.array([[np.cos(angle), 0, -np.sin(angle)], [0, 1, 0], [np.sin(angle), 0, np.cos(angle)]])
    view = np.eye(4)
    view[:3, :3] = R
    view[:3, 3] = [0, 0, -.25]
    return {"view": view.ravel().tolist(), "fx": 700., "fy": 700., "cx": 160., "cy": 120.,
            "width": 320, "height": 240, "meta": {"pixel_scale": 1.}}


def test_collapsed_lens_ids_do_not_hide_geometry_marking(tmp_path):
    geometry = P.load_geometry(fixture_glb(tmp_path))
    by_name = {p["name"]: i for i, p in enumerate(geometry.parts)}
    assert geometry.glass.sum() == 1
    assert geometry.markings[by_name["h"]]  # physically on the glass, despite optical role
    assert not geometry.markings[by_name["i"]]  # floats 2 mm away: not a decal
    result = P.visible_geometry(geometry, camera(), np.ones((240, 320), bool))
    assert result["glass"].any() and result["markings"].any()
    assert np.all(result["part"][result["markings"]] == by_name["h"])
    assert not np.any(result["glass"] & result["markings"])


def test_frozen_openings_pose_host_selection_without_mutating_export(tmp_path):
    parts = glbfab.default_parts()
    parts['temple-mark'] = ((70, 70.1, 3, 5, -30, -25), 'frame', 'temple')
    path = tmp_path / 'posed.glb'
    path.write_bytes(glbfab.build(parts))
    original_bytes = path.read_bytes()
    geometry = P.load_geometry(path)
    original = geometry.vertices.copy()
    pose = {'+1': {'theta_deg': 12, 'axis_xz_mm': [68, -2]},
            '-1': {'theta_deg': -7, 'axis_xz_mm': [-68, -2]}}
    posed = P.posed_geometry(geometry, pose)
    for old, new in zip(geometry.parts, posed.parts):
        if old['role'] != 'temple':
            np.testing.assert_array_equal(old['vertices'], new['vertices'])
            continue
        side = 1 if old['bounds'][:, 0].sum() > 0 else -1
        record = pose[f'{side:+d}']
        angle = np.radians(record['theta_deg'] * side)
        axis = np.array([record['axis_xz_mm'][0], 0, record['axis_xz_mm'][1]]) / 1000
        # Independent homogeneous rotation; no production pose helper in truth calculation.
        rotation = np.array([[np.cos(angle), 0, -np.sin(angle)], [0, 1, 0], [np.sin(angle), 0, np.cos(angle)]])
        expected = (old['vertices'] - axis) @ rotation.T + axis
        np.testing.assert_allclose(new['vertices'], expected, atol=1e-12)
    np.testing.assert_array_equal(geometry.vertices, original)
    assert path.read_bytes() == original_bytes
    assert P.posed_geometry(geometry, {}) is geometry
    assert not np.array_equal(posed.vertices, original)


def test_surface_normals_not_pixel_brightness_define_walls(tmp_path):
    geometry = P.load_geometry(fixture_glb(tmp_path))
    result = P.visible_geometry(geometry, camera(40), np.ones((240, 320), bool))
    assert result["front_face"].any()
    assert result["front_wall"].any()
    assert result["inner_rim_wall"].any()
    assert not np.any(result["front_face"] & result["front_wall"])
    assert np.all(result["front_wall"][result["inner_rim_wall"]])
    # The method takes no beauty image at all: painting it black cannot relabel a face as a wall.


def test_id_decoder_keeps_mixed_and_does_not_invent_markings():
    ids = np.array([[[255, 0, 255], [0, 0, 255], [0, 0, 0], [0, 255, 0]]], np.uint8)
    palette = [{"rgb": [255, 0, 255], "class": "lens", "decode_tolerance": 24},
               {"rgb": [255, 0, 255], "class": "lens", "decode_tolerance": 24},
               {"rgb": [0, 0, 255], "class": "frame", "decode_tolerance": 24},
               {"rgb": [0, 255, 0], "class": "mixed", "decode_tolerance": 24}]
    masks = P.decode_masks(ids, palette)
    assert masks["visible"].tolist() == [[True, True, False, True]]
    assert masks["lens"].sum() == 1


@pytest.mark.parametrize("shape", [(1280, 700), (4096, 4096), (1190, 474), (500, 500)])
def test_pairs_keep_every_native_pixel_and_tile_over_the_patch_budget(tmp_path, shape):
    photo = Image.new("RGB", shape, (31, 47, 83))
    drawing = Image.new("RGB", shape, (91, 73, 61))
    rec = P._pair(photo, drawing, [0, 0, *shape], tmp_path / "pair.png", "pair", P.HIGH_MAX_SIDE)
    assert rec["pair_scale"] == 1
    coverage = np.zeros(shape[::-1], bool)
    for tile in rec.get("native_tiles", [rec]):
        assert tile["patches_32px"] <= 2500 and tile["native_detail"]
        x0, y0, x1, y1 = tile["crop_box"]
        coverage[y0:y1, x0:x1] = True
        with Image.open(tile["path"]) as image:
            for source, box in zip((photo, drawing), tile["panel_boxes"]):
                assert image.crop(box).tobytes() == source.crop(tile["crop_box"]).tobytes()
    assert coverage.all()


def test_native_tiles_cover_entire_large_detail():
    box = [11, 17, 1239, 706]
    mask = np.zeros((720, 1280), bool)
    for x0, y0, x1, y1 in P._tiles(box):
        assert x1 - x0 <= 500 and y1 - y0 <= 500
        mask[y0:y1, x0:x1] = True
    assert mask[17:706, 11:1239].all()
    assert mask.sum() == (1239 - 11) * (706 - 17)


def test_panels_preserve_sources_report_missing_features_and_show_wordmark(tmp_path):
    model = fixture_glb(tmp_path)
    geometry = P.load_geometry(model)
    cam = camera()
    selected = P.visible_geometry(geometry, cam, np.ones((240, 320), bool))
    part = selected["part"]
    ids = np.zeros((240, 320, 3), np.uint8)
    ids[part >= 0] = [0, 0, 255]
    for i, p in enumerate(geometry.parts):
        if p["lens"]:
            ids[part == i] = [255, 0, 255]  # glass AND glyph genuinely collapse in the saved ID image
    palette = [{"rgb": [255, 0, 255], "class": "lens", "decode_tolerance": 24},
               {"rgb": [0, 0, 255], "class": "frame", "decode_tolerance": 24}]
    photo_path, draw_path, id_path = [tmp_path / x for x in ("photo.png", "draw.png", "ids.png")]
    Image.new("RGB", (320, 240), (230, 225, 210)).save(photo_path)
    Image.new("RGB", (320, 240), (200, 190, 170)).save(draw_path)
    Image.fromarray(ids).save(id_path)
    paths = [model, photo_path, draw_path, id_path]
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    row = {"ref": "arbitrary-photo", "photo_path": str(photo_path), "drawing_path": str(draw_path),
           "part_id_path": str(id_path), "camera": cam, "crop_box": [0, 0, 320, 240]}
    result = P.build_panels(row, palette, geometry, tmp_path / "panels")
    details = {x["kind"]: x for x in result["details"]}
    assert set(details) == set(P.DETAIL_KINDS)
    assert details["lens_mark"]["status"] == "visible"
    assert details["inner_rim"]["status"] == "visible"
    assert all(x["native_detail"] for x in result["images"] if x["kind"] == "look_detail")
    assert {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths} == before
    assert "front_wall" in result["surface_colours"]


def test_empty_view_reports_absence_without_fabricating_crops(tmp_path):
    geometry = P.load_geometry(fixture_glb(tmp_path, marking=False))
    path = tmp_path / "blank.png"
    Image.new("RGB", (320, 240)).save(path)
    row = {"ref": "offscreen", "photo_path": str(path), "drawing_path": str(path),
           "part_id_path": str(path), "camera": camera()}
    result = P.build_panels(row, [], geometry, tmp_path / "panels")
    assert len(result["images"]) == 1
    assert all(x["status"] == "unavailable" and x["reason"] for x in result["details"])
    assert all(x["photo"]["status"] == "unavailable" for x in result["surface_colours"].values())
