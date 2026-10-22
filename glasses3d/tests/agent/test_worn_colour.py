"""Task 18: native worn evidence leads the existing photo and surface evidence."""
import base64
import copy
import io
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest
from agents.tool import ToolOutputImage, ToolOutputText

from glasses3d.agent import instructions
from glasses3d.agent.job_scene import JobScene
from glasses3d.agent.tools import build_tools, look
from glasses3d.g3d_host.camera import Camera, NormFrame
from glasses3d.tests.agent.test_job_tools import invoke, record, scene


SENTENCE = (
    "Judge lens colour on the worn, on-face AR images against the owner photos. "
    "Use photo-background panels for shape, see-through and diagnostic measurements; "
    "a better photo-background colour number does not overrule the worn result. "
    "Report disagreements separately and explain the retained choice."
)


def _png(path, colour, size=(97, 65)):
    path.parent.mkdir(parents=True, exist_ok=True)
    # A spatial pattern catches accidental resampling, normalization and flat-colour substitution.
    yy, xx = np.indices((size[1], size[0]))
    pixels = np.stack([(xx + colour[0]) % 256, (yy + colour[1]) % 256,
                       (xx + yy + colour[2]) % 256], axis=-1).astype(np.uint8)
    Image.fromarray(pixels).save(path)
    return path


def _row(ref, yaw, pitch=0, roll=0):
    return {"ref": ref, "camera": {"meta": {"source_camera": {
        "yaw": yaw, "pitch": pitch, "roll": roll}}}}


def _worn(directory):
    front = _png(directory / "portrait__a__eyes.png", (10, 20, 30), (1603, 577))
    report = {"poses": [], "images": [{"file": front.name, "sha256": look.arview.sha256(front),
        "kind": "portrait", "variant": "condition-a", "region": "eyes", "pose": "portrait-face-a"}]}
    for yaw, name, colour in ((50, "yaw_p50", (40, 50, 60)), (-50, "yaw_m50", (70, 80, 90))):
        # Real capture reports use a metadata object, not just boolean true, for visible heads.
        report["poses"].append({"id": name, "yaw": yaw, "pitch": 0, "roll": 0,
                                 "worn": {"head": "existing face surface", "hair": False}})
        path = _png(directory / f"{name}__skin__beauty__worn.png", colour)
        report["images"].append({"file": path.name, "sha256": look.arview.sha256(path), "pose": name,
                                   "variant": "beauty", "background": "skin", "kind": "worn_crop"})
    return front, report


def test_instruction_places_owner_colour_decision_beside_metallic_guidance():
    # The placement belongs to decision-20261021; decision-20261022 keeps the sentence (its own contract).
    registry = build_tools(SimpleNamespace(instruction_profile="decision-20261021"))
    text = " ".join(instructions.build_instructions(registry, profile="decision-20261021").split())
    assert text.count(SENTENCE) == 1
    metallic_end = "photo and worn comparisons."
    assert metallic_end + " " + SENTENCE in text
    assert len(text) <= instructions.MAX_INSTRUCTION_CHARS


@pytest.mark.parametrize("yaw,pitch,roll,expected", [
    (0, 0, 0, ["front"]),
    (24.9, 0, 0, ["front"]),
    (25, 0, 0, ["front"]),
    (25.1, 0, 0, ["front", "yaw_m50"]),
    (-25.1, 0, 0, ["front", "yaw_p50"]),
    (84, 0, 0, ["front", "yaw_m50"]),
    (-84, 0, 0, ["front", "yaw_p50"]),
    (50, 90, 0, ["front"]),
    (48, 13, 127, ["front", "yaw_m50"]),
])
def test_nearest_existing_worn_angle_uses_fitted_camera_and_correct_side(tmp_path, yaw, pitch, roll, expected):
    front, report = _worn(tmp_path)
    rows = [_row("photo", yaw, pitch, roll)]
    before = copy.deepcopy((rows, report))
    source_bytes = {path.name: path.read_bytes() for path in tmp_path.glob("*.png")}
    images = look.worn_colour_images(rows, report, tmp_path, front)
    assert [image["pose"] for image in images] == expected
    assert all(image["kind"] == "compare_look_to_photos" for image in images)
    assert all(image["purpose"] == "lens_colour_judgement" for image in images)
    assert all(image["view"].startswith("Lens-colour judgement: worn") for image in images)
    assert images[0]["path"] == str(front)
    assert all(Path(image["path"]).read_bytes() == source_bytes[Path(image["path"]).name] for image in images)
    assert (rows, report) == before


def test_repeated_photos_select_each_native_capture_once_in_first_matching_order(tmp_path):
    front, report = _worn(tmp_path)
    rows = [_row("left", 45), _row("left-close", 65), _row("front", 0), _row("right", -45)]
    images = look.worn_colour_images(rows, report, tmp_path, front)
    assert [image["pose"] for image in images] == ["front", "yaw_m50", "yaw_p50"]
    assert [item["ref"] for item in images[1]["matched_refs"]] == ["left", "left-close"]
    assert [item["angle_difference_deg"] for item in images[1]["matched_refs"]] == pytest.approx([5, 15])
    assert len({image["sha256"] for image in images}) == 3


def test_identical_angle_bytes_do_not_duplicate_front_or_other_angle(tmp_path):
    front, report = _worn(tmp_path)
    for image in report["images"][1:]:
        (tmp_path / image["file"]).write_bytes(front.read_bytes())
        image["sha256"] = look.arview.sha256(front)
    images = look.worn_colour_images([_row("left", 50), _row("right", -50)], report, tmp_path, front)
    assert len(images) == 1
    assert images[0]["path"] == str(front)
    assert [item["ref"] for item in images[0]["matched_refs"]] == ["left", "right"]


def test_missing_worn_crop_never_substitutes_standard_pose_without_visible_head(tmp_path):
    front, report = _worn(tmp_path)
    report["images"] = report["images"][:1]
    for kind, name, worn in (("pose", "yaw_m50", True), ("worn_crop", "yaw_m45", None)):
        path = _png(tmp_path / f"{name}-{kind}.png", (91, 2, 83))
        report["poses"].append({"id": name, "yaw": -50, "pitch": 0, "roll": 0, "worn": worn})
        report["images"].append({"kind": kind, "pose": name, "file": path.name,
                                   "sha256": look.arview.sha256(path)})
    images = look.worn_colour_images([_row("left", 50)], report, tmp_path, front)
    assert [image["pose"] for image in images] == ["front"]


def test_sdk_sends_labelled_worn_first_at_exact_native_pixels(scene, monkeypatch):
    front, report = _worn(scene.root / "work/worn")
    worn = look.worn_colour_images([_row("left", 50)], report, front.parent, front)
    photo = _png(scene.root / "work/photo-pair.png", (180, 20, 30))
    result = {"status": "ok", "image_delivery": "separate", "images": worn + [
        {"path": str(photo), "sha256": look.arview.sha256(photo), "kind": "look_full_pair", "view": "Owner photo pair"}]}
    monkeypatch.setattr(look, "capture", lambda *args, **kwargs: copy.deepcopy(result))
    parts = invoke(scene, "compare_look_to_photos", {"refs": ["left"], "basis": None})
    delivered = record(parts)["images"]
    assert [item["kind"] for item in delivered] == ["compare_look_to_photos", "compare_look_to_photos", "look_full_pair"]
    assert [item["purpose"] for item in delivered[:2]] == ["lens_colour_judgement", "lens_colour_judgement"]
    payloads = [part for part in parts if isinstance(part, ToolOutputImage)]
    assert len(payloads) == 3
    for expected, actual, payload in zip(result["images"], delivered, payloads):
        data = base64.b64decode(payload.image_url.split(",", 1)[1])
        assert data == Path(expected["path"]).read_bytes()
        with Image.open(io.BytesIO(data)) as im:
            assert actual["size_px"] == actual["source_size_px"] == list(im.size)
        index = parts.index(payload)
        assert isinstance(parts[index - 1], ToolOutputText)
        assert expected["view"] in parts[index - 1].text
    assert delivered[0]["size_px"] == [1603, 577]


def test_latest_worn_sdk_evidence_survives_save_reload_and_second_renewal(scene, monkeypatch):
    durable = JobScene(scene.root, job={})
    scene.record_images = durable.record_images
    results = []
    for generation, call_id in enumerate(("old-look", "latest-look")):
        front, report = _worn(scene.root / "work" / call_id)
        if generation:
            # Distinct current pixels prevent prior-call transport dedup from hiding a stale selection.
            for index, image in enumerate(report["images"]):
                path = front.parent / image["file"]
                with Image.open(path) as source:
                    size = source.size
                _png(path, (110 + index, 120, 130), size)
                image["sha256"] = look.arview.sha256(path)
        worn = look.worn_colour_images([_row("left", 50)], report, front.parent, front)
        photo = _png(front.parent / "photo-pair.png", (180 + generation, 20, 30))
        output = {"status": "ok", "image_delivery": "separate", "images": worn + [
            {"path": str(photo), "sha256": look.arview.sha256(photo), "kind": "look_full_pair",
             "ref": "left", "view": "left"}]}
        monkeypatch.setattr(look, "capture", lambda *args, **kwargs: copy.deepcopy(output))
        results.append(record(invoke(scene, "compare_look_to_photos", {"refs": ["left"], "basis": None}, cid=call_id)))

    expected = results[-1]["images"]
    loaded = JobScene(scene.root, job={})
    first = loaded.renewal_evidence()
    assert len(first) == 3  # Both worn images survive alongside the existing photo panel.
    assert [image["sha256"] for image in first] == [image["sha256"] for image in expected]
    assert [image["view"] for image in first] == [image["view"] for image in expected]
    assert {image["call_id"] for image in first} == {"latest-look"}
    for image, original in zip(first, expected):
        assert Path(image["path"]).read_bytes() == (scene.root / original["path"]).read_bytes()
    loaded.renewal_started()
    assert JobScene(scene.root, job={}).renewal_evidence() == first

    # The first renewal's reduced metadata must also be sufficient on its own for another renewal.
    loaded.images = copy.deepcopy(first)
    loaded.events = []
    loaded.save()
    second = JobScene(scene.root, job={})
    second.renewal_started()
    assert second.renewal_evidence() == first
    assert JobScene(scene.root, job={}).renewal_evidence() == first


@pytest.fixture
def capture_case(tmp_path, monkeypatch):
    """Exercise production capture assembly while replacing only external render/geometry work."""
    root = tmp_path
    model = root / "work/model.glb"
    model.parent.mkdir()
    model.write_bytes(b"same immutable export")
    export = {"status": "exported", "path": str(model), "sha256": look.arview.sha256(model), "checkpoint": "c1"}
    basis_dir = root / "work/basis"
    basis_dir.mkdir()
    (basis_dir / "basis.json").write_text("{}", encoding="utf-8")
    refs = {"front": {"view_kind": "front", "photo_sha256": "a" * 64},
            "left": {"view_kind": "angled", "photo_sha256": "b" * 64}}
    photo = np.full((64, 80, 3), [152, 151, 163], dtype=np.uint8)
    basis = SimpleNamespace(id="basis-fixed", dir=basis_dir, refs=refs,
        frame=NormFrame((0, 0, 0), 140), meta={"cameras_json_sha256": "c" * 64},
        usable_refs=lambda: list(refs), unusable_refs=lambda: {"rear": "unusable fixture"},
        photo=lambda rid: photo.copy(), shape=lambda rid: photo.shape[:2], pose=lambda rid: None,
        camera=lambda rid: Camera(0 if rid == "front" else 48, 0, 0, .2, 65, 39.5, 31.5),
        roi=lambda rid: [0, 0, 80, 64])
    monkeypatch.setattr(look.C, "load_basis", lambda *args: basis)
    monkeypatch.setattr(look.look_panels, "load_geometry", lambda path: [])
    monkeypatch.setattr(look.shutil, "which", lambda name: "fixture-node")
    from glasses3d.g3d_host import surface_quality
    monkeypatch.setattr(surface_quality, "report", lambda path: {"fixture_quality": "retained"})
    render_calls, panel_records, composed_records = [], [], []

    def render(argv, directory, **kwargs):
        render_calls.append(list(argv))
        out = Path(argv[argv.index("--out") + 1])
        out.mkdir()
        cameras = json.loads(Path(argv[argv.index("--cameras") + 1]).read_text())["cameras"]
        images = []
        for cam in cameras:
            for kind, colour in (("beauty", [82, 105, 100]), ("part-id", [255, 0, 0])):
                path = out / f"{cam['id']}__{kind}.png"
                Image.new("RGB", (cam["width"], cam["height"]), tuple(colour)).save(path)
                images.append({"file": path.name, "sha256": look.arview.sha256(path)})
        doc = {"input": {"sha256": export["sha256"]}, "images": images, "poses": [],
               "id_palette": [{"class": "lens", "rgb": [255, 0, 0], "decode_tolerance": 0}]}
        (out / "report.json").write_text(json.dumps(doc), encoding="utf-8")
        return 0, False

    def panels(row, palette, geometry, directory, *, surface_only=False):
        path = _png(directory / "panel.png", (32 if surface_only else 93, 43, 65))
        rec = {"path": str(path), "sha256": look.arview.sha256(path), "ref": row["ref"],
               "kind": "surface_detail" if surface_only else "look_full_pair", "view": row["ref"]}
        panel_records.append(rec)
        return {"images": [rec], "details": [{"fixture_detail": "retained"}]}

    def compose(images, directory, ref):
        path = _png(directory / "composed.png", (143, 4, 86))
        rec = {"path": str(path), "sha256": look.arview.sha256(path), "ref": ref,
               "kind": "look_composed", "view": ref, "sources": copy.deepcopy(images)}
        composed_records.append(rec)
        return [rec]

    monkeypatch.setattr(look.arview, "run_capture", render)
    monkeypatch.setattr(look.look_panels, "build_panels", panels)
    monkeypatch.setattr(look.look_composition, "compose", compose)
    front, report = _worn(root / "work/source-worn")
    report["input"] = {"sha256": export["sha256"]}
    report_path = front.parent / "report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    worn = {"report": str(report_path), "report_sha256": look.arview.sha256(report_path),
            "export_sha256": export["sha256"]}
    monkeypatch.setattr(look.arview, "capture", lambda *args, **kwargs: pytest.fail("existing worn capture must be reused"))
    return SimpleNamespace(root=root, export=export, worn=worn, front=front, report=report,
                           renders=render_calls, panels=panel_records, composed=composed_records)


@pytest.mark.parametrize("combine", [False, True])
@pytest.mark.parametrize("front_only", [False, True])
def test_capture_promotes_only_worn_images_preserving_all_existing_evidence(capture_case, combine, front_only):
    case = capture_case
    result = look.capture(case.root, case.export, "test", front_only=front_only, worn=case.worn, combine=combine)
    assert result["status"] == "ok", result
    refs = ["front"] if front_only else ["front", "left"]
    worn_count = 1 if front_only else 2
    assert [item["pose"] for item in result["images"][:worn_count]] == (["front"] if front_only else ["front", "yaw_m50"])
    assert all(item["view"].startswith("Lens-colour judgement: worn") for item in result["images"][:worn_count])
    # The trailing evidence records, order and composition inputs are exactly those built before promotion.
    assert result["images"][worn_count:] == (case.composed if combine else case.panels)
    if combine:
        for image in case.composed:
            assert image["sources"] == [panel for panel in case.panels if panel["ref"] == image["ref"]]
    assert [view["ref"] for view in result["views"]] == refs
    assert result["lens_colour_gap"] == [
        f"{ref} lens: AR L* 42.5 vs photo 62.9 (20.4 darker); median RGB (sRGB 8-bit) AR 82,105,100 vs photo 152,151,163"
        for ref in refs]
    for view in result["views"]:
        assert view["colours"]["lens"]["photo"]["median_srgb_8bit"] == [152, 151, 163]
        assert view["colours"]["lens"]["drawing"]["median_srgb_8bit"] == [82, 105, 100]
        assert view["colours"]["frame"]["photo"]["status"] == "unavailable"
        assert view["details"] == [{"fixture_detail": "retained"}]
    assert result["surface_quality"]["fixture_quality"] == "retained"
    assert [view["ref"] for view in result["surface_quality"]["views"]] == refs
    assert result["fixed_scale"] is True and result["lens_env_intensity"] == look.arview.LENS_ENV_INTENSITY
    assert result["informational_only"] is True
    assert result["unusable_refs"] == {"rear": "unusable fixture"}
    assert Path(result["worn_front_path"]).name == "worn-front.png"
    assert Path(result["worn_front_path"]).read_bytes() == case.front.read_bytes()
    assert len(case.renders) == 2  # The existing photo and side-light batches; no extra rendering.
    assert all("--fixed-scale" in command for command in case.renders)
    assert len(result["capture_reports"]) == 2
    assert len(result["images"]) == len(case.composed if combine else case.panels) + worn_count
    copied = Path(result["worn_capture"]["report"])
    assert json.loads(copied.read_text(encoding="utf-8")) == case.report
    assert all((copied.parent / image["file"]).read_bytes() == (case.front.parent / image["file"]).read_bytes()
               for image in case.report["images"])
