"""Native comparison pixels survive every delivered page, including edit tools."""
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest

from glasses3d.agent.look_cost import documented_image
from glasses3d.agent.tools import look_panels, owner_preview, owner_edit
from glasses3d.tests.agent.test_look_composition import assert_lossless_pages


def sources(directory, size):
    width, height = size
    x, y = np.arange(width)[None, :], np.arange(height)[:, None]
    paths = []
    for i in range(3):
        pixels = np.empty((height, width, 3), np.uint8)
        pixels[..., 0] = (x * 13 + y * 3 + i * 29) % 256
        pixels[..., 1] = (x * 5 + y * 17 + i * 47) % 256
        pixels[..., 2] = (x * 19 + y * 7 + i * 61) % 256
        path = directory / f"source-{i}.png"
        Image.fromarray(pixels).save(path)
        paths.append(path)
    return paths


def assert_native_tiles(paths, tiles):
    with Image.open(paths[0]) as source:
        cover = np.zeros((source.height, source.width), bool)
    for tile in tiles:
        with Image.open(tile["path"]) as image:
            assert documented_image(*image.size, "high")["processed_dimensions"] == list(image.size)
            assert tile["native_pixels"] and tile["patches_32px"] <= 2500
            x0, y0, x1, y1 = tile["crop_box"]
            cover[y0:y1, x0:x1] = True
            for path, panel in zip(paths, tile["panel_boxes"]):
                with Image.open(path) as source:
                    assert image.crop(panel).tobytes() == source.crop(tile["crop_box"]).tobytes()
            assert all(0 <= a < c <= image.width and 0 <= b < d <= tile["panel_boxes"][0][1]
                       for a, b, c, d in tile["label_boxes"])
    assert cover.all()


@pytest.mark.parametrize("size", [(1024, 1024), (2000, 1333), (4032, 3024)])
@pytest.mark.parametrize("tool", ["preview", "edit_report"])
def test_edit_triples_overflow_to_native_matched_tiles(tmp_path, size, tool):
    paths = sources(tmp_path, size)
    if tool == "preview":
        full, tiles = owner_preview._triple(paths, tmp_path / "comparison.png", "ref", "A: Less", option_id="A")
    else:
        full, tiles = owner_edit._triple(*paths, tmp_path / "comparison.png", "ref")
    assert len(tiles) > 1
    assert full["native_pixels"]
    assert_native_tiles(paths, tiles)


def test_narrow_pair_labels_are_inside_padding_and_source_pixels_unchanged(tmp_path):
    paths = sources(tmp_path, (58, 29))
    images = [Image.open(path) for path in paths[:2]]
    result = look_panels._pair(*images, [0, 0, 58, 29], tmp_path / "pair.png",
                              "a-long-photo-reference: lens_mark / native pixels", look_panels.HIGH_MAX_SIDE)
    assert_native_tiles(paths[:2], [result])
    # Both full column labels survive even though the original crop is just 58 px.
    assert result["headers"] == ["PHOTO", "AR / SAME CAMERA AND PIXEL BOX"]
    assert len(result["label_boxes"]) >= 4


def test_large_lens_corner_is_one_native_detail_not_four_near_duplicates(tmp_path, monkeypatch):
    width, height = 1400, 1100
    selected = {key: np.zeros((height, width), bool) for key in
                ("glass", "markings", "front", "front_face", "front_wall", "inner_rim_wall", "temple_root", "temple_tip")}
    selected["part"] = np.full((height, width), -1)
    selected["glass"][450:550, 200:1000] = True
    selected["front"][500, 1001] = True
    ids = np.zeros((height, width, 3), np.uint8)
    ids[selected["glass"] | selected["front"]] = (0, 0, 255)
    photo = tmp_path / "photo.png"
    Image.new("RGB", (width, height), "white").save(photo)
    identifier = tmp_path / "ids.png"
    Image.fromarray(ids).save(identifier)
    monkeypatch.setattr(look_panels, "load_geometry", lambda path: SimpleNamespace())
    monkeypatch.setattr(look_panels, "posed_geometry", lambda geometry, pose: geometry)
    monkeypatch.setattr(look_panels, "visible_geometry", lambda *args: selected)
    result = look_panels.build_panels({"ref": "wide", "photo_path": photo, "drawing_path": photo,
        "part_id_path": identifier, "camera": {}}, [{"class": "frame", "rgb": [0, 0, 255]}], "unused", tmp_path / "panels")
    corners = next(row for row in result["details"] if row["kind"] == "outer_corner")["images"]
    assert len(corners) == 1
    assert max(corners[0]["source_panel_size_px"]) < look_panels.DETAIL_SIDE


def test_preview_composes_target_closeups_per_photo_and_option(tmp_path):
    paths = sources(tmp_path, (320, 240))
    before = {"views": [{"ref": "sample", "camera": {}, "photo_path": str(paths[0]), "drawing_path": str(paths[1])}]}
    after = {"views": [{**before["views"][0], "drawing_path": str(paths[2])}]}
    options = [{"id": "A", "label": "Less", "after_evidence": after}]
    boxes = {"sample": [{"id": "detail-1", "labels": ["T1"], "box": [10, 20, 100, 120],
                           "object_id": "obj", "target_ids": ["target"]}]}
    images, files = owner_preview._pictures(tmp_path, tmp_path, before, options, boxes)
    assert len(images) == 1 and images[0]["kind"] == "look_composed"
    assert {row["kind"] for row in images[0]["parts"]} == {"owner_amount_full", "owner_amount_detail"}
    assert_lossless_pages(images[0]["parts"], images)
    assert all(Path(tmp_path / path).is_file() for path in files)
