"""Job intake uses explicit views and preserves the owner's original photo bytes ($0)."""
import base64
import hashlib
import json

from PIL import Image
import pytest

from glasses3d.agent import jobs as J


def setup_inputs(tmp_path, *, kind="front", side="both", suffix=".png"):
    source = tmp_path / "photos"
    source.mkdir()
    photo = source / f"owner photo{suffix}"
    Image.new("RGB", (31, 19), (50, 80, 130)).save(photo)
    views = tmp_path / "views.json"
    views.write_text(json.dumps({photo.name: {"kind": kind, "side": side}}))
    return {"images": source, "views": views, "name": "An owner's frame",
            "description": "  Exact owner description.\r\n尺寸: 145 mm  ", "jobs_root": tmp_path / "jobs"}, photo


def test_intake_copies_exact_bytes_and_offline_metadata(tmp_path):
    args, photo = setup_inputs(tmp_path)
    payload = photo.read_bytes()
    root = J.create_job(**args)
    job = J.load_job(root)
    assert job["schema"] == "g3d.job/1" and job["input_layout"] == "job-images/1"
    assert job["description"] == args["description"]
    row, = job["images"]
    assert row["original_name"] == photo.name
    assert (root / row["file"]).read_bytes() == payload
    assert row["sha256"] == hashlib.sha256(payload).hexdigest()
    assert row["size_px"] == [31, 19] and row["mime"] == "image/png"
    assert J.image_paths(root) == {photo.name: root / row["file"]}
    assert J.view_specs(root) == {photo.name: {"kind": "front", "side": "both"}}
    reference, = J.references(root)
    assert base64.b64decode(reference.image_url.split(",")[1]) == payload
    assert reference.path == row["file"] and reference.size_px == (31, 19)
    assert "front" in reference.view and reference.detail == "high"
    assert not (root / "budget").exists() and not (root / "runs").exists()


def test_photo_stems_must_not_collide_in_camera_records(tmp_path):
    args, photo = setup_inputs(tmp_path)
    duplicate_stem = photo.with_suffix(".jpg")
    Image.new("RGB", (31, 19), "green").save(duplicate_stem)
    args["views"].write_text(json.dumps({p.name: {"kind": "front", "side": None}
                                        for p in (photo, duplicate_stem)}))
    with pytest.raises(J.JobError, match="photo stems"):
        J.create_job(**args)
    assert not args["jobs_root"].exists()


@pytest.mark.parametrize("change,match", [
    ("missing", "bijection"), ("extra", "bijection"), ("unknown", "unknown view kind"),
    ("absent_kind", "explicit view kind"), ("absent_side", "side must"),
    ("bad_side", "side must"), ("contradiction", "contradicts"),
])
def test_view_bijection_and_explicit_labels_are_required(tmp_path, change, match):
    args, photo = setup_inputs(tmp_path)
    views = {photo.name: {"kind": "front", "side": "both"}}
    if change == "missing":
        views = {}
    elif change == "extra":
        views["absent.png"] = {"kind": "rear", "side": "both"}
    elif change == "unknown":
        views[photo.name]["kind"] = "guess this camera"
    elif change == "absent_kind":
        del views[photo.name]["kind"]
    elif change == "absent_side":
        del views[photo.name]["side"]
    elif change == "bad_side":
        views[photo.name]["side"] = "starboard"
    else:
        views[photo.name] = {"kind": "top_oblique_left", "side": "right"}
    args["views"].write_text(json.dumps(views))
    with pytest.raises(J.JobError, match=match):
        J.create_job(**args)
    assert not args["jobs_root"].exists()


@pytest.mark.parametrize("kind,side", [("side", "left"), ("angled", "right"), ("top", None),
                                     ("rear", "both"), ("top_oblique", "right"), ("Elevated_Side", "left")])
def test_existing_camera_vocabulary_and_explicit_unspecified_side(tmp_path, kind, side):
    args, photo = setup_inputs(tmp_path, kind=kind, side=side)
    root = J.create_job(**args)
    assert J.view_specs(root)[photo.name] == {"kind": kind, "side": side}


@pytest.mark.parametrize("kind,side,expected", [("side", "left", ["left"]),
                                               ("side", "right", ["right"]),
                                               ("top_oblique", "left", ["top_oblique_left"]),
                                               ("top_oblique", "right", ["top_oblique_right"]),
                                               ("angled", "left", ["angled"]),
                                               ("side", None, ["right", "left"])])
def test_explicit_photo_side_restricts_camera_candidates(tmp_path, kind, side, expected):
    args, _ = setup_inputs(tmp_path, kind=kind, side=side)
    root = J.create_job(**args)
    assert J.load_job(root)["images"][0]["fit_kinds"] == expected


def test_duplicate_decoded_pixels_refused_even_when_file_bytes_differ(tmp_path):
    args, photo = setup_inputs(tmp_path)
    other = photo.with_name("same pixels.png")
    image = Image.open(photo)
    image.save(other, compress_level=0)
    assert other.read_bytes() != photo.read_bytes()
    args["views"].write_text(json.dumps({p.name: {"kind": "front", "side": "both"} for p in (photo, other)}))
    with pytest.raises(J.JobError, match="duplicate decoded"):
        J.create_job(**args)


def test_mime_mismatch_and_corrupt_photos_are_refused(tmp_path):
    args, photo = setup_inputs(tmp_path)
    Image.new("RGB", (31, 19), "red").save(photo, format="JPEG")
    with pytest.raises(J.JobError, match="MIME"):
        J.create_job(**args)
    photo.write_bytes(b"not an image")
    with pytest.raises(J.JobError, match="could not be decoded"):
        J.create_job(**args)


def test_unsupported_photo_cannot_silently_disappear_from_intake(tmp_path):
    args, photo = setup_inputs(tmp_path)
    Image.new("RGB", (31, 19), "green").save(photo.with_suffix(".tiff"))
    with pytest.raises(J.JobError, match="unsupported photo format"):
        J.create_job(**args)


def test_image_pixel_limit_is_checked_before_decode(tmp_path, monkeypatch):
    args, _ = setup_inputs(tmp_path)
    monkeypatch.setattr(J, "MAX_PIXELS", 100)
    with pytest.raises(J.JobError, match="at most 100 pixels"):
        J.create_job(**args)


def test_exif_orientation_recorded_with_oriented_dimensions_and_original_preserved(tmp_path):
    args, photo = setup_inputs(tmp_path, suffix=".jpg")
    exif = Image.Exif()
    exif[274] = 6
    Image.new("RGB", (31, 19), "red").save(photo, exif=exif)
    payload = photo.read_bytes()
    root = J.create_job(**args)
    row, = J.load_job(root)["images"]
    assert row["exif_orientation"] == 6 and row["size_px"] == [19, 31]
    assert (row["w"], row["h"]) == (31, 19)
    assert (root / row["file"]).read_bytes() == payload


def test_fitter_and_compare_read_same_oriented_pixels_as_job_image_tools(tmp_path):
    import numpy as np
    from PIL import ImageOps
    from glasses3d.g3d_host import matte

    args, photo = setup_inputs(tmp_path, suffix=".jpg")
    exif = Image.Exif()
    exif[274] = 6
    pixels = Image.new("RGB", (31, 19), "red")
    pixels.paste("blue", (0, 0, 10, 7))
    pixels.save(photo, exif=exif)
    root = J.create_job(**args)
    row, = J.load_job(root)["images"]
    fitted = matte.load_photo(root / row["file"])
    with Image.open(photo) as original:
        expected = np.asarray(ImageOps.exif_transpose(original).convert("RGB"))
    assert [fitted.shape[1], fitted.shape[0]] == row["size_px"]
    assert np.array_equal(fitted, expected)


def test_large_original_gets_same_size_transport_copy(tmp_path, monkeypatch):
    args, photo = setup_inputs(tmp_path)
    Image.new("RGB", (31, 19), "red").save(photo, compress_level=0)
    monkeypatch.setattr(J, "MAX_REFERENCE_BYTES", 1000)
    root = J.create_job(**args)
    row, = J.load_job(root)["images"]
    assert row["bytes"] > 1000 and row["sent_file"] != row["file"]
    assert (root / row["file"]).read_bytes() == photo.read_bytes()
    with Image.open(root / row["sent_file"]) as sent:
        assert list(sent.size) == row["size_px"]
    reference, = J.references(root)
    assert len(base64.b64decode(reference.image_url.split(",")[1])) < 1000


def test_changed_or_escaping_input_cannot_be_loaded(tmp_path):
    args, _ = setup_inputs(tmp_path)
    root = J.create_job(**args)
    job = J.load_job(root)
    row, = job["images"]
    original = root / row["file"]
    payload = original.read_bytes()
    original.write_bytes(b"changed")
    with pytest.raises(J.JobError, match="digest differs"):
        J.load_job(root)
    original.write_bytes(payload)
    row["file"] = "images/../../outside.png"
    (root / "job.json").write_text(json.dumps(job))
    with pytest.raises(J.JobError, match="within"):
        J.load_job(root)


def test_specs_keep_exact_text_and_object(tmp_path):
    args, _ = setup_inputs(tmp_path)
    text = '{\r\n  "width_mm": 145, "custom": {"description": "晶体"}\r\n}\n'
    specs = tmp_path / "specs.json"
    specs.write_bytes(text.encode("utf-8"))
    job = J.load_job(J.create_job(**args, specs=specs))
    assert job["specs_json"] == text and job["specs"] == json.loads(text)
    assert job["specs_sha256"] == hashlib.sha256(text.encode()).hexdigest()


@pytest.mark.parametrize("text", ['[]', '{"width": NaN}', '{"width": 1, "width": 2}'])
def test_specs_refuse_non_objects_nonfinite_and_duplicate_keys(tmp_path, text):
    args, _ = setup_inputs(tmp_path)
    specs = tmp_path / "specs.json"
    specs.write_text(text)
    with pytest.raises(J.JobError):
        J.create_job(**args, specs=specs)
