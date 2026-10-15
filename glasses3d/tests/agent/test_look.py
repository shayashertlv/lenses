import json
import copy
from pathlib import Path

import numpy as np
from PIL import Image
import pytest
from agents.tool import ToolOutputImage

from glasses3d.agent.tools import look
from glasses3d.g3d_host.ar_camera import explicit_camera
from glasses3d.g3d_host.camera import Camera, NormFrame, project_mm
from glasses3d.tests.agent.test_job_tools import scene, finish_scene, invoke, record
from glasses3d.tests.agent.test_delivery import make_finished, digest
from glasses3d.agent import delivery


@pytest.mark.parametrize("angles", [(0, 0, 0), (-87, 12, 33), (17, 90, -120)])
@pytest.mark.parametrize("eyes", [(0, 0), (.3, -.25)])
@pytest.mark.parametrize("perspective", [0, .02, .6])
def test_projection_parity(angles, eyes, perspective):
    frame = NormFrame((11., -9., -73.), 160.)
    camera = Camera(*angles, perspective, 1500., 1002.3, 616.8, *eyes)
    explicit = explicit_camera(camera, frame, (1281, 1921), name="test", background="#ffffff")
    points = frame.to_mm(np.array([[x, y, z] for x in (-.45, 0, .45) for y in (-.4, .4) for z in (-.4, 0, .4)]))
    view = np.asarray(explicit["view"]).reshape(4, 4)
    pc = points / 1000 @ view[:3, :3].T + view[:3, 3]
    actual = np.column_stack([explicit["cx"] + explicit["fx"] * pc[:, 0] / -pc[:, 2],
                              explicit["cy"] - explicit["fy"] * pc[:, 1] / -pc[:, 2]])
    expected = (project_mm(points, camera, frame) + .5) * explicit["meta"]["pixel_scale"] - .5
    error = np.linalg.norm(actual - expected, axis=1).max()
    assert error < (explicit["meta"]["unit_frame_box_error_bound_px"] if perspective == 0 else 1e-8)
    assert explicit["meta"]["orthographic_approximation"] == (perspective == 0)


def test_border_median_and_uniformity_no_gate():
    photo = np.full((20, 30, 3), [250, 240, 230], np.uint8)
    photo[3:17, 3:27] = 0
    result = look.border_colour(photo)
    assert result["hex"] == "#faf0e6" and result["pixels"] == 96
    assert result["uniformity"]["fraction_within_5_levels"] == 1
    photo[0, :15] = 0
    assert look.border_colour(photo)["uniformity"]["fraction_within_5_levels"] < 1


def test_colour_statistics_report_black_white_and_empty():
    mask = np.ones((10, 10), bool)
    assert look.colour_stats(np.zeros((10, 10, 3), np.uint8), mask)["median_lightness_Lstar"] == 0
    assert look.colour_stats(np.full((10, 10, 3), 255, np.uint8), mask)["median_lightness_Lstar"] == pytest.approx(100)
    assert look.colour_stats(np.zeros((10, 10, 3), np.uint8), ~mask)["status"] == "unavailable"


def test_new_sdk_tool_forwards_refs_and_exports_current_head(scene, monkeypatch):
    calls = []
    monkeypatch.setattr(look, "capture", lambda *a, **kw: calls.append((a, kw)) or {"status": "unavailable"})
    out = record(invoke(scene, "compare_look_to_photos", {"refs": ["front.png"], "basis": "basis-02"}))
    assert out["status"] == "unavailable"
    assert calls[0][0][1] == scene.export and calls[0][1] == {"refs": ["front.png"], "basis": "basis-02"}


def test_successful_sdk_comparison_emits_one_sheet_with_list_view_records(scene, monkeypatch):
    path = scene.root / "work/compare.png"
    Image.new("RGB", (1024, 990), "blue").save(path)
    monkeypatch.setattr(look, "capture", lambda *a, **kw: {"status": "ok", "informational_only": True,
        "views": [{"ref": "front", "sheet_path": str(path)}],
        "images": [{"path": str(path), "sha256": digest(path), "view": "photo and worn"}]})
    parts = invoke(scene, "compare_look_to_photos", {"refs": None, "basis": None})
    out = record(parts)
    assert out["status"] == "ok" and len(out["views"]) == 1
    assert sum(isinstance(p, ToolOutputImage) for p in parts) == 1
    assert out["images"][0]["size_px"] == [1024, 990]


@pytest.mark.parametrize("bad_image", [False, True])
def test_finish_keeps_optional_comparison_informational_and_ar_separate(finish_scene, monkeypatch, bad_image):
    scene = finish_scene
    image = scene.root / "work/look.png"
    if not bad_image:
        Image.new("RGB", (1024, 620), "red").save(image)
    monkeypatch.setattr(look, "capture", lambda *a, **kw: {"status": "ok", "informational_only": True,
                        "images": [{"path": str(image), "sha256": digest(image) if image.exists() else "missing"}]})
    parts = invoke(scene, "finish", {"summary": "diagnostic only"})
    out = record(parts)
    assert out["status"] == "accepted" and sum(isinstance(p, ToolOutputImage) for p in parts) == 1
    if bad_image:
        assert out["look_comparison"]["status"] == "unavailable" and out["image_source"] == "arview"
    else:
        assert out["image_source"] == "arview_and_look"
        assert out["arview"]["images"] != out["images"]


def test_delivery_retains_look_after_sources_are_removed(tmp_path):
    scene, result = make_finished(tmp_path)
    directory = tmp_path / "work/look"
    directory.mkdir()
    image = directory / "comparison.png"
    Image.new("RGB", (32, 32), "green").save(image)
    row = {"path": str(image), "sha256": digest(image)}
    look_result = {"schema": look.SCHEMA, "status": "ok", "informational_only": True,
                   "export_sha256": scene.finish["export"]["sha256"], "assets": [row], "images": [row],
                   "worn_front_path": str(image), "views": [{"sheet_path": str(image)}]}
    path = directory / "comparison-report.json"
    path.write_text(json.dumps(look_result))
    scene.finish["look_comparison"] = {**look_result, "report": str(path), "report_sha256": digest(path)}
    delivery.deliver_finished(tmp_path, scene, result)
    image.unlink()
    path.unlink()
    loaded = delivery.load_delivery(tmp_path)
    report = loaded["report_data"] if "report_data" in loaded else loaded["data"]
    assert report["look_comparison"]["status"] == "ok"
    assert Path(report["look_comparison"]["worn_front_path"]).is_file()


@pytest.mark.parametrize("available", [True, False])
def test_sdk_finish_delivery_keeps_independent_evidence_after_source_cleanup(scene, monkeypatch, available):
    source, run = make_finished(scene.root)
    scene.export = source.finish["export"]
    model = scene.root / "work/exports/finished.glb"
    model.write_bytes(Path(scene.export["path"]).read_bytes())
    scene.export["path"] = str(model)
    original_ar = copy.deepcopy(source.last_ar)
    original_ar["images"] = [{"path": p, "sha256": digest(p), "view": "AR front"}
                             for p in original_ar["sheets"]]  # capture returns native sheets, before SDK compaction
    monkeypatch.setattr(look.arview, "capture", lambda *a, **kw: copy.deepcopy(original_ar))
    folder = scene.root / "work/look"
    folder.mkdir()
    image = folder / "comparison.png"
    Image.new("RGB", (1024, 622), "green").save(image)
    worn = folder / "worn"
    worn.mkdir()
    portrait = worn / "portrait.png"
    Image.new("RGB", (64, 48), "blue").save(portrait)
    worn_report = worn / "report.json"
    worn_report.write_text(json.dumps({"input": {"sha256": scene.export["sha256"]},
        "images": [{"file": portrait.name, "sha256": digest(portrait)}]}))
    doc = {"schema": look.SCHEMA, "status": "ok", "informational_only": True,
           "export_sha256": scene.export["sha256"], "views": [{"sheet_path": str(image)}],
           "images": [{"path": str(image), "sha256": digest(image)}], "worn_front_path": str(portrait),
           "worn_capture": {"report": str(worn_report), "report_sha256": digest(worn_report)},
           "assets": [{"path": str(p), "sha256": digest(p)} for p in (image, portrait, worn_report)]}
    report_path = folder / "comparison-report.json"
    report_path.write_text(json.dumps(doc))
    result = {**doc, "report": str(report_path), "report_sha256": digest(report_path)} if available else {
        "status": "unavailable", "images": [], "informational_only": True, "message": "no frozen front"}
    monkeypatch.setattr(look, "capture", lambda *a, **kw: result)
    parts = invoke(scene, "finish", {"summary": "composed SDK evidence"})
    finish = record(parts)
    assert finish["status"] == "accepted"
    assert sum(isinstance(p, ToolOutputImage) for p in parts) == 1
    assert scene.last_ar["sheets"] == original_ar["sheets"]
    shown_hash = finish["images"][0]["sha256"]
    delivered = delivery.deliver_finished(scene.root, scene, run)
    for p in (image, portrait, worn_report, report_path):
        p.unlink()
    for p in (scene.root / "work/tool_images").glob("*"):
        if p.is_file():
            p.unlink()
    loaded = delivery.load_delivery(scene.root)["report_data"]
    assert loaded["look_comparison"]["status"] == ("ok" if available else "unavailable")
    if available:
        assert loaded["finish_images"][0]["sha256"] == shown_hash
        cap = Path(loaded["look_comparison"]["worn_capture"]["report"])
        assert cap.is_relative_to(Path(delivered["snapshot_model"]).parent)
        for row in json.loads(cap.read_text())["images"]:
            assert digest(cap.parent / row["file"]) == row["sha256"]
