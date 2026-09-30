"""Native-pixel evidence, filesystem boundaries and durable visual-review identity."""
import asyncio
import base64
import hashlib
from io import BytesIO
import json
from pathlib import Path

import pytest
pytest.importorskip("agents")
from agents.tool_context import ToolContext
from PIL import Image

from blender_agent.observations import ImageRegion, inspect_image, make_read_image, make_record_review
from blender_agent.observations import ComparisonProgress, progress_advice


@pytest.mark.parametrize("format,mime", [("WEBP", "image/webp"), ("PNG", "image/png"),
                                         ("JPEG", "image/jpeg"), ("GIF", "image/gif")])
def test_image_url_uses_actual_format_without_platform_mime_registry(tmp_path, monkeypatch, format, mime):
    import mimetypes
    from blender_agent.agent import image_url
    monkeypatch.setattr(mimetypes, "guess_type", lambda *a, **k: (None, None))
    path = tmp_path / "reference.unregistered-extension"
    Image.new("RGB", (7, 5), "green").save(path, format=format)
    url = image_url(path)
    assert url.startswith(f"data:{mime};base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == path.read_bytes()


def test_image_url_rejects_unsupported_actual_format_despite_png_name(tmp_path):
    from blender_agent.agent import image_url
    path = tmp_path / "not-a-png.png"
    Image.new("RGB", (7, 5)).save(path, format="BMP")
    with pytest.raises(ValueError, match="Expected a PNG"):
        image_url(path)


def invoke(tool, arguments):
    encoded = json.dumps(arguments)
    context = ToolContext(context=None, tool_name=tool.name, tool_call_id="offline-observation", tool_arguments=encoded)
    return asyncio.run(tool.on_invoke_tool(context, encoded))


def test_crop_retains_exact_source_pixels_and_does_not_modify_reference(tmp_path):
    source = tmp_path / "reference.png"
    original = Image.new("RGB", (20, 10))
    original.putdata([(x * 7, y * 13, (x + y) * 3) for y in range(10) for x in range(20)])
    original.save(source)
    before = source.read_bytes()
    output = tmp_path / "agent"
    result = inspect_image(str(source), output, [source], ImageRegion(left=.2, top=.2, right=.8, bottom=.8))
    metadata = json.loads(result[0].text)
    shown = Image.open(BytesIO(base64.b64decode(result[1].image_url.split(",", 1)[1])))
    assert shown.size == (12, 6)
    assert shown.tobytes() == original.crop((4, 2, 16, 8)).tobytes()
    assert metadata["source_sha256"] == hashlib.sha256(before).hexdigest()
    assert source.read_bytes() == before
    assert Path(metadata["displayed_image"]).with_suffix(".json").is_file()


def test_original_image_and_region_tool_schema_work_with_sdk(tmp_path):
    source = tmp_path / "image.png"
    Image.new("RGB", (9, 7)).save(source)
    tool = make_read_image(tmp_path, [])
    result = invoke(tool, {"path": str(source), "region": None})
    assert json.loads(result[0].text)["source_size"] == [9, 7]
    assert base64.b64decode(result[1].image_url.split(",", 1)[1]) == source.read_bytes()


@pytest.mark.parametrize("region", [dict(left=.8, right=.2, top=0, bottom=1),
                                    dict(left=0, right=1, top=.4, bottom=.4),
                                    dict(left=-.1, right=1, top=0, bottom=1)])
def test_invalid_region_rejected(region):
    with pytest.raises(ValueError):
        ImageRegion(**region)


def test_image_outside_authorized_evidence_and_subpixel_crop_rejected(tmp_path):
    source = tmp_path / "unlisted.png"
    Image.new("RGB", (10, 10)).save(source)
    with pytest.raises(ValueError, match="Evidence"):
        inspect_image(str(source), tmp_path / "agent", [])
    with pytest.raises(ValueError, match="two source pixels"):
        inspect_image(str(source), tmp_path, [], ImageRegion(left=0, top=0, right=.01, bottom=.01))


def test_review_pins_evidence_and_checkpoint_identity_without_certifying_quality(tmp_path):
    output = tmp_path / "agent"
    output.mkdir()
    reference = tmp_path / "reference.png"
    Image.new("RGB", (5, 5), "red").save(reference)
    model = output / "detail.png"
    Image.new("RGB", (5, 5), "blue").save(model)
    checkpoint = output / "candidate.blend"
    checkpoint.write_bytes(b"test scene identity")
    arguments = {"label": "rim comparison", "checkpoint": str(checkpoint), "glb": None,
                 "findings": [{"region": "upper rim", "observation": "Repeated bands remain",
                               "interpretation": "Possible surface corrugation", "alternative": "Refraction",
                               "evidence": [str(reference), str(model)], "outcome": "uncertain",
                               "next_check": "Compare identical sections under neutral shading"}]}
    result = invoke(make_record_review(output, [reference]), arguments)
    record = json.loads(Path(result["review"]).read_text())
    assert record["image_sha256"][str(reference.resolve())] == hashlib.sha256(reference.read_bytes()).hexdigest()
    assert record["artifacts"]["checkpoint"]["sha256"] == hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    assert result["quality_certified"] is False
    assert result["unresolved"] == ["upper rim"]
    second = invoke(make_record_review(output, [reference]), arguments)
    assert second["review"] != result["review"]
    assert Path(result["review"]).is_file()


def test_review_cannot_pin_artifact_outside_output(tmp_path):
    output = tmp_path / "agent"
    output.mkdir()
    image = output / "image.png"
    Image.new("RGB", (5, 5)).save(image)
    checkpoint = tmp_path / "outside.blend"
    checkpoint.write_bytes(b"outside")
    result = invoke(make_record_review(output, []), {
        "label": "invalid artifact", "checkpoint": str(checkpoint), "glb": None,
        "findings": [{"region": "rim", "observation": "Unclear", "interpretation": "Unknown",
                      "alternative": None, "evidence": [str(image)], "outcome": "uncertain", "next_check": None}]})
    assert "Evidence must be" in result
    assert not (output / "reviews").exists()


def test_same_defect_no_gain_prompts_reconsideration_without_automatic_stop(tmp_path):
    progress = ComparisonProgress(focus="Core visibility", product_result="unchanged",
                                  new_information=False,
                                  comparison_basis="Same oblique view and light; core still clipped",
                                  next_test=None)
    assert not progress_advice(progress, tmp_path)["repeated_no_gain"]
    (tmp_path / "1.json").write_text(json.dumps({"progress": progress.model_dump(),
                                                "created_at": "2026-09-30T10:00:00.000001+00:00"}))
    result = progress_advice(progress, tmp_path)
    assert result["repeated_no_gain"] is True
    assert result["automatic_stop"] is False
    assert "rollback, export and validation" in result["action"]
    # A useful control, material improvement or different defect must not be
    # labeled stagnation just because the mesh is unchanged.
    for changes in ({"new_information": True}, {"product_result": "improved"},
                    {"focus": "Lens tint"}, {"product_result": "uncertain"}):
        current = progress.model_copy(update=changes)
        assert not progress_advice(current, tmp_path)["repeated_no_gain"]


def test_progress_follows_same_focus_by_precise_time_not_filename_order(tmp_path):
    current = ComparisonProgress(focus="Core visibility", product_result="unchanged", new_information=False,
                                 comparison_basis="Matched pose and light", next_test=None)
    def write(name, microsecond, **changes):
        (tmp_path / name).write_text(json.dumps({
            "created_at": f"2026-09-30T10:00:00.{microsecond:06}+00:00",
            "progress": current.model_copy(update=changes).model_dump()}))
    write("zzz.json", 1)
    write("middle.json", 2, focus="Lens tint", product_result="improved")
    assert progress_advice(current, tmp_path)["repeated_no_gain"] is True
    # A later useful same-focus experiment resets the warning even when a
    # same-second UUID filename happens to sort earlier than the stale failure.
    write("aaa.json", 3, new_information=True)
    assert progress_advice(current, tmp_path)["repeated_no_gain"] is False


def test_review_records_comparison_and_registers_exact_evidence(tmp_path):
    image = tmp_path / "eye.png"
    Image.new("RGB", (5, 5)).save(image)
    registered = []
    class Registry:
        def register(self, path, kind):
            registered.append((path, kind, path.read_bytes()))
    result = invoke(make_record_review(tmp_path, [], evidence_registry=Registry()), {
        "label": "matched tint check", "checkpoint": None, "glb": None,
        "findings": [{"region": "lenses", "observation": "Warmer and lighter",
                      "interpretation": "Material adjustment", "alternative": None,
                      "evidence": [str(image)], "outcome": "improved", "next_check": None}],
        "progress": {"focus": "Lens tint", "product_result": "improved", "new_information": True,
                     "comparison_basis": "Same portrait pose and lighting", "next_test": None}})
    report = json.loads(Path(result["review"]).read_text())
    assert report["progress"]["product_result"] == "improved"
    assert report["progress_advice"]["automatic_stop"] is False
    assert len(registered) == 1 and registered[0][1] == "review"
    assert json.loads(registered[0][2]) == report
