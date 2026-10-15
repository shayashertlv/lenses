import hashlib

import numpy as np
from PIL import Image
import pytest

from glasses3d.agent.tools.lens_response import background_response, response_summary
from glasses3d.agent.tools.arview import contact_sheet
from glasses3d.agent.tool_reports import limit_output


def fixture(out, difference):
    records = []
    for variant, bg, colour in [("part-id", None, [255, 0, 0]), ("beauty", "skin", [40 + difference, 40, 40]),
                                ("beauty", "dark", [40, 40, 40])]:
        file = f"front-{variant}-{bg}.png"
        Image.new("RGB", (20, 20), tuple(colour)).save(out / file)
        records.append({"file": file, "sha256": hashlib.sha256((out / file).read_bytes()).hexdigest(),
                        "kind": "pose", "pose": "front", "background": bg, "variant": variant})
    return {"images": records, "id_palette": [{"class": "lens", "rgb": [255, 0, 0], "decode_tolerance": 0}]}


@pytest.mark.parametrize("difference", [0, 80, -20])
def test_response_matches_known_rgb_pair_and_erosion_without_gate_or_clipping(tmp_path, difference):
    report = background_response(tmp_path, fixture(tmp_path, difference))
    row = report["views"][0]
    assert row["status"] == "measured" and row["pixels"] == 14 * 14
    assert row["mean_channel_background_response_gain"] == pytest.approx(difference / 160 / 3)
    assert report["informational_only"] is True and "ok" not in report and "ok" not in row
    assert report["portrait"]["status"] == "unavailable"
    assert len(row["sources"]) == 3


def test_numbers_remain_in_paged_tool_output(tmp_path):
    import json
    response = background_response(tmp_path, fixture(tmp_path, 80))
    result = {"status": "ok", "runtime": {"large": "x" * 30000}, "summary": response_summary(response),
              "lens_background_response": response}
    shown = json.loads(limit_output(tmp_path, "test", "arview_capture", json.dumps(result)))
    assert shown["paged"] and "front 0.167" in shown["summary"] and "information only" in shown["summary"]


@pytest.mark.parametrize("missing", ["lens", "pair", "interior", "size"])
def test_unavailable_measurement_is_not_zero_or_a_failed_capture(tmp_path, missing):
    doc = fixture(tmp_path, 0)
    if missing == "lens":
        doc["id_palette"] = []
    elif missing == "pair":
        doc["images"].pop()
    elif missing == "interior":
        im = np.zeros((20, 20, 3), dtype=np.uint8)
        im[4:7, :, 0] = 255
        Image.fromarray(im).save(tmp_path / doc["images"][0]["file"])
    else:
        Image.new("RGB", (10, 10)).save(tmp_path / doc["images"][0]["file"])
    row = background_response(tmp_path, doc)["views"][0]
    assert row["status"] == "unavailable" and row["reason"]
    assert "mean_channel_background_response_gain" not in row


def test_sheet_prioritizes_eye_detail_and_keeps_all_old_views_without_resizing_again(tmp_path):
    records = []
    specs = [("portrait", "portrait-face-a", "condition-a", "face-a", "eyes", "red"),
             ("pose", "front", "beauty", "checker", None, "blue")]
    specs += [("worn_crop", p, "beauty", "skin", None, "green") for p in ("yaw_p50", "yaw_m50")]
    specs += [("pose", p, "beauty", "skin", None, "yellow") for p in
              ("front", "yaw_p30", "yaw_m30", "yaw_p45", "yaw_m45", "pitch_p20", "pitch_m20")]
    for index, (kind, pose, variant, background, region, colour) in enumerate(specs):
        file = f"{index}.png"
        Image.new("RGB", (769, 376) if kind == "portrait" else (640, 200), colour).save(tmp_path / file)
        records.append(dict(file=file, kind=kind, pose=pose, variant=variant, background=background, region=region))
    # Dark diagnostic views must not replace or multiply the seven standard tiles.
    records += [{**rec, "background": "dark"} for rec in records if rec["kind"] == "pose" and rec["background"] == "skin"]
    with Image.open(contact_sheet(tmp_path, records)) as image:
        assert image.size == (1024, 1012)
        assert image.getpixel((320, 150))[0] > 240  # large portrait is actually present
        assert image.getpixel((820, 150))[2] > 240  # front checker is present
