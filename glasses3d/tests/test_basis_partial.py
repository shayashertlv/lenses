"""Partial camera fits keep every input and failure while only successful cameras are usable."""
from copy import deepcopy
import json

import numpy as np
from PIL import Image
import pytest

from glasses3d.g3d_host import compare as C


CAMERA = {"yaw": 90.0, "pitch": 10.0, "roll": 0.0, "perspective": .1,
          "scale": 500.0, "center_x": 40.0, "center_y": 30.0}
MODEL = {"frame": {"center": [0, 0, -70], "extent": 150}, "front_pivot_mm": [0, 0, 0]}


def source(tmp_path, rows):
    folder = tmp_path / "source"
    folder.mkdir(exist_ok=True)
    photos, images = {}, {}
    for name, overrides in rows.items():
        photo = folder / name
        pixels = np.full((60, 80, 3), 250, np.uint8)
        pixels[15:45, 20:60] = 40
        Image.fromarray(pixels).save(photo)
        row = {"status": "ok", "shape": [60, 80], "camera": deepcopy(CAMERA), "view": "left",
               "view_kind": "side", "matte_mode": "default", "refusal": None}
        row.update(deepcopy(overrides))
        if row.get("shape") is None:
            photo.write_bytes(b"undecodable photo retained as diagnostic input")
        row["image_sha256"] = C.sha256_file(photo)
        images[name], photos[name] = row, photo
    C.write_json(folder / C.CAMERAS_JSON, {"model": MODEL, "images": images})
    return folder, photos, images


def freeze(root, folder, photos):
    return C.import_cameras_json(root, folder / C.CAMERAS_JSON, photos, reason="offline partial fit")


def failure(**extra):
    return {"status": "failed", "code": "E_PHOTO_FIT", "camera": None, "view": None,
            "refusal": "image could not be decoded", "error": {"type": "ValueError", "message": "bad photo"},
            "matte": None, "shape": None, **extra}


def test_mixed_fit_retains_every_photo_and_available_diagnostic(tmp_path):
    lens = {"mode": "optional", "optional_nodes": ["Lens.001"], "optional_faces": 2,
            "construction": [{"node": "Lens.001", "frame_type": "rimless", "frame_type_source": "metadata"}]}
    folder, photos, raw = source(tmp_path, {
        "front photo.png": {"iou": .8, "iou_lens_optional": .95, "lens_pixels_optional": 30,
                            "contour_mean_px_lens_optional": .2, "contour_p95_px_lens_optional": .7,
                            "render_only_px_lens_optional": 2}, "broken photo.png": failure(),
        "rejected.png": {"status": "refused", "code": "E_VIEW_SIGN", "refusal": "camera below top view",
                         "flags": ["label_sign_contradicted"], "matte_mode": "glossy_floor", "lens_loss": lens}})
    Image.new("RGB", (80, 60), "purple").save(folder / "overlay_rejected.png")
    basis = freeze(tmp_path / "job", folder, photos)
    assert list(basis.refs) == ["front photo", "broken photo", "rejected"]
    assert basis.usable_refs() == ["front photo"]
    assert list(basis.unusable_refs()) == ["broken photo", "rejected"]
    for key in ("iou", "iou_lens_optional", "lens_pixels_optional", "contour_mean_px_lens_optional",
                "contour_p95_px_lens_optional", "render_only_px_lens_optional"):
        assert basis.refs["front photo"]["fit"][key] == raw["front photo.png"][key]
    assert basis.refs["broken photo"]["photo_sha256"] == raw["broken photo.png"]["image_sha256"]
    assert all(basis.refs["broken photo"][key] is None for key in ("camera", "shape", "matte", "roi", "fg_px"))
    rejected = basis.refs["rejected"]
    assert rejected["camera"] == CAMERA and rejected["lens_loss"] == lens
    assert rejected["matte_mode"] == "glossy_floor" and basis.matte("rejected").any()
    assert C.sha256_file(basis.dir / rejected["overlay"]) == rejected["overlay_sha256"]
    for rid in ("broken photo", "rejected"):
        with pytest.raises(C.UnusableReference) as caught:
            basis.camera(rid)
        assert caught.value.code == "E_UNUSABLE_CAMERA" and caught.value.ref_id == rid
        assert caught.value.details == basis.unusable_refs()[rid]
        assert raw[basis.refs[rid]["image"]]["refusal"] in str(caught.value)
    assert basis.summary()["refs"] == list(basis.refs)
    assert C.load_basis(tmp_path / "job").refs == basis.refs


def test_all_failed_attempt_is_inspectable_and_previous_usable_stays_default(tmp_path):
    folder, photos, _ = source(tmp_path, {"photo.png": {}})
    root = tmp_path / "job"
    first = freeze(root, folder, photos)
    folder2, failed_photos, _ = source(tmp_path, {"broken.png": failure()})
    second = freeze(root, folder2, failed_photos)
    assert second.id == "basis-02" and second.usable_refs() == []
    assert C.load_basis(root).id == first.id
    assert C.load_basis(root, second.id).unusable_refs()["broken"]["code"] == "E_PHOTO_FIT"
    assert C.list_bases(root) == ["basis-01", "basis-02"] and C.next_basis_id(root) == "basis-03"


def test_all_failed_without_previous_basis_refuses_default_with_evidence_retained(tmp_path):
    folder, photos, _ = source(tmp_path, {"broken.png": failure()})
    root = tmp_path / "job"
    basis = freeze(root, folder, photos)
    with pytest.raises(C.NoBasis, match="no successful camera"):
        C.load_basis(root)
    assert C.load_basis(root, basis.id).refs == basis.refs
    with pytest.raises(C.UnusableReference):
        basis.matte("broken")


def test_failed_empty_matte_is_actual_evidence_not_a_fake_roi(tmp_path):
    folder, photos, raw = source(tmp_path, {"empty.png": failure(shape=[60, 80],
        matte={"foreground_px": 0, "shape": [60, 80]}, refusal="empty matte")})
    Image.new("RGB", (80, 60), "white").save(photos["empty.png"])
    raw["empty.png"]["image_sha256"] = C.sha256_file(photos["empty.png"])
    C.write_json(folder / C.CAMERAS_JSON, {"model": MODEL, "images": raw})
    basis = freeze(tmp_path / "job", folder, photos)
    assert basis.refs["empty"]["fg_px"] == 0 and basis.refs["empty"]["roi"] is None
    assert not basis.matte("empty").any() and not basis.is_usable("empty")


@pytest.mark.parametrize("damage", ["source_json", "photo", "overlay", "matte", "status", "error", "lens_loss",
                                    "missing_ref", "unknown_status", "frame", "id"])
def test_damaged_failed_newest_basis_never_falls_back_silently(tmp_path, damage):
    folder, photos, _ = source(tmp_path, {"good.png": {}})
    root = tmp_path / "job"
    freeze(root, folder, photos)
    folder, photos, _ = source(tmp_path, {"bad.png": {"status": "refused", "refusal": "wrong view",
        "code": "E_VIEW_SIGN", "error": {"type": "ValueError", "message": "original"},
        "lens_loss": {"mode": "full", "optional_nodes": []}}})
    Image.new("RGB", (80, 60), "purple").save(folder / "overlay_bad.png")
    second = freeze(root, folder, photos)
    meta = json.loads((second.dir / "basis.json").read_text())
    row = meta["refs"]["bad"]
    if damage == "source_json":
        (second.dir / C.CAMERAS_JSON).write_text("{}")
    elif damage == "photo":
        photos["bad.png"].write_bytes(b"changed")
    elif damage in ("overlay", "matte"):
        (second.dir / row[damage]).write_bytes(b"changed")
    elif damage == "missing_ref":
        meta["refs"] = {}
    elif damage == "frame":
        meta["frame"]["extent"] += 10
    elif damage == "id":
        meta["id"] = "basis-50"
    elif damage == "lens_loss":
        row["lens_loss"]["mode"] = "optional"
    elif damage == "error":
        row["fit"]["error"]["message"] = "replaced"
    elif damage == "status":
        row["fit"].update(status="ok", refusal=None)
    else:
        row["fit"]["status"] = "unknown"
    C.write_json(second.dir / "basis.json", meta)
    with pytest.raises((ValueError, OSError)):
        C.load_basis(root)


@pytest.mark.parametrize("field,value", [("camera", None), ("camera", {**CAMERA, "scale": float("nan")}),
                                        ("status", "unknown")])
def test_malformed_success_is_not_converted_to_partial_fit(tmp_path, field, value):
    folder, photos, _ = source(tmp_path, {"photo.png": {field: value}})
    with pytest.raises(ValueError):
        freeze(tmp_path / "job", folder, photos)


def test_legacy_status_absent_camera_remains_usable(tmp_path):
    folder, photos, raw = source(tmp_path, {"legacy.png": {}})
    raw["legacy.png"].pop("status")
    raw["legacy.png"].pop("image_sha256")
    C.write_json(folder / C.CAMERAS_JSON, {"model": MODEL, "images": raw})
    basis = freeze(tmp_path / "job", folder, photos)
    assert basis.camera("legacy").to_dict() == CAMERA
    assert basis.usable_refs() == ["legacy"] and not basis.unusable_refs()


def test_failed_supplied_matte_still_requires_its_hash(tmp_path):
    folder, photos, _ = source(tmp_path, {"bad.png": failure(shape=[60, 80], matte_mode="supplied",
        matte_file="provided.png", matte_file_sha256="0" * 64)})
    Image.new("L", (80, 60), 255).save(folder / "provided.png")
    with pytest.raises(ValueError, match="matte_file_sha256"):
        freeze(tmp_path / "job", folder, photos)


def test_same_stem_cannot_discard_a_diagnostic_photo(tmp_path):
    folder, photos, _ = source(tmp_path, {"photo.png": {}, "photo.jpg": failure()})
    with pytest.raises(ValueError, match="share reference id"):
        freeze(tmp_path / "job", folder, photos)


def test_custom_overlay_basenames_cannot_replace_another_photos_evidence(tmp_path):
    folder, photos, raw = source(tmp_path, {"a.png": {}, "b.png": failure()})
    for name, colour in (("a", "red"), ("b", "blue")):
        (folder / name).mkdir()
        path = folder / name / "diagnostic.png"
        Image.new("RGB", (80, 60), colour).save(path)
        raw[name + ".png"]["overlay"] = str(path)
    C.write_json(folder / C.CAMERAS_JSON, {"model": MODEL, "images": raw})
    basis = freeze(tmp_path / "job", folder, photos)
    for rid in ("a", "b"):
        assert basis.refs[rid]["overlay_sha256"] == C.sha256_file(folder / rid / "diagnostic.png")
    assert basis.refs["a"]["overlay"] != basis.refs["b"]["overlay"]
