"""Nested stage previews preserve native evidence and append-only outputs; no paid or Blender calls."""
import base64
import copy
import hashlib
import io
import json
import threading
from types import SimpleNamespace

from agents.tool import ToolOutputImage, ToolOutputText
from PIL import Image
import pytest

from glasses3d.agent.temple_lab import stage
from glasses3d.agent.temple_lab.stage import StageLab, settings
from glasses3d.agent.tools.images import IMAGE_POLICY, MAX_IMAGE_SIDE


def lab_at(tmp_path):
    lab = object.__new__(StageLab)
    lab.root = tmp_path.resolve()
    lab.state_lock = threading.RLock()
    lab.s = SimpleNamespace(head={"id": "c0007"})
    lab.state = {"images": {}}
    lab.save = lambda: None
    return lab


def panel(lab, name, size, colour, **meta):
    path = lab.root / f"{name}.png"
    Image.new("RGB", size, colour).save(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    lab.state["images"][digest] = {"kind": "compare", "revision": 7, "photo": name, **meta}
    return path, digest


def image_and_receipt(output):
    images = [item for item in output if isinstance(item, ToolOutputImage)]
    assert len(images) == 1
    with Image.open(io.BytesIO(base64.b64decode(images[0].image_url.split(",", 1)[1]))) as image:
        size = image.size
    body = json.loads(output[0].text.split("\n", 1)[0])
    assert body["image_policy"] == IMAGE_POLICY
    assert max(size) <= MAX_IMAGE_SIDE
    assert body["image_receipt"]["size_px"] == list(size)
    return body["image_receipt"]


def test_nested_stage_disables_history_guards_even_when_parent_used_them():
    parent = SimpleNamespace(max_output_tokens=2048, settled_stop_usd="1", context_guard_tokens=1,
                             context_guard_every_requests=1, context_guard_bytes=1)
    chosen = settings(parent)
    assert chosen.context_guard_tokens is None
    assert chosen.context_guard_every_requests is None
    assert chosen.context_guard_bytes is None
    assert "context_guard" not in chosen.approval_settings()
    assert chosen.max_output_tokens == 2048 and chosen.settled_stop_usd == "1"
    assert chosen.finish_tool == "facet_finish" and chosen.finish_k == 4


def test_nested_registry_explains_preview_coordinates_without_changing_standalone():
    catalog = SimpleNamespace(lock=threading.Lock(), variant_photos_max=lambda: 3,
                              photo_ids=lambda: ["front"], compare_ids=lambda: ["front"],
                              image_ids=lambda: ["front"])
    standalone = {tool.name: tool for tool in stage.T.registry(catalog, settings())}
    nested = {tool.name: tool for tool in stage.registry(catalog, settings())}
    for name, tool in nested.items():
        assert tool.params_json_schema == standalone[name].params_json_schema
        assert "Nested-stage images" not in standalone[name].description
        if name == "facet_finish":
            assert tool.description == standalone[name].description
        else:
            assert tool.description.startswith("Nested-stage images")
            assert "preview_to_panel" in tool.description
            assert tool.description.endswith(standalone[name].description)


def test_multiple_panels_become_one_preview_with_native_provenance(tmp_path):
    lab = lab_at(tmp_path)
    front, front_sha = panel(lab, "front", (3000, 2000), "red", ref="cmp001")
    side, side_sha = panel(lab, "side", (900, 1600), "blue", ref="cmp001", zoom=True)
    native = {path: path.read_bytes() for path in (front, side)}
    result = {"status": "ok", "compare_id": "cmp001", "images": [str(front), str(side)]}
    before = copy.deepcopy(result)
    receipt = image_and_receipt(lab.tool_output(result, "settled $0", "compare-1", "photo_compare"))
    assert receipt["views"] == ["front", "side"]
    assert receipt["checkpoint"] == "c0007"
    assert [p["sha256"] for p in receipt["panels"]] == [front_sha, side_sha]
    assert [p["size_px"] for p in receipt["panels"]] == [[3000, 2000], [900, 1600]]
    assert [p["path"] for p in receipt["panels"]] == ["front.png", "side.png"]
    assert [p["context_role"] for p in receipt["panels"]] == ["comparison", "zoom"]
    assert all(p["revision"] == 7 and p["ref"] == "cmp001" for p in receipt["panels"])
    assert result == before
    assert all(path.read_bytes() == data for path, data in native.items())
    assert lab.state["images"][front_sha]["photo"] == "front"
    assert lab.state["stage_images"] == [receipt]
    assert "preview_scale_xy" not in receipt  # a grid is not one native panel's pixel frame


def test_single_preview_preserves_photo_transform_and_exact_pixel_center_mapping(tmp_path):
    lab = lab_at(tmp_path)
    photo, _ = panel(lab, "crop", (2048, 1025), "green", kind="photo", revision=0)
    transform = {"box": [40, 30, 4136, 2080], "k": 0.5, "native_size": [5000, 3000]}
    result = {"status": "ok", "photo": "front", "transform": transform, "images": [str(photo)]}
    output = lab.tool_output(result, "footer", "crop", "photo_image")
    receipt = image_and_receipt(output)
    scale = receipt["preview_scale_xy"]
    assert receipt["size_px"] == [1024, 513]
    assert scale == [1024 / 2048, 513 / 1025]
    assert "u_preview + 0.5" in receipt["preview_to_panel"]
    assert "v_preview + 0.5" in receipt["preview_to_panel"]
    # Odd-height rounding makes X and Y scales different; inverse mapping must use each actual dimension.
    native = [1000.25, 500.75]
    native_panel = [(value - offset + 0.5) * transform["k"] - 0.5
                    for value, offset in zip(native, transform["box"][:2])]
    preview = [(value + 0.5) * factor - 0.5 for value, factor in zip(native_panel, scale)]
    recovered = [offset + (value + 0.5) / factor / transform["k"] - 0.5
                 for value, factor, offset in zip(preview, scale, transform["box"][:2])]
    assert recovered == pytest.approx(native)
    assert json.loads(output[0].text.split("\n", 1)[0])["transform"] == transform
    assert "transform/k applies to saved panels" in output[1].text


def test_repeated_outputs_are_visible_and_earlier_history_does_not_change(tmp_path):
    lab = lab_at(tmp_path)
    photo, digest = panel(lab, "source", (1200, 800), "green", kind="photo", revision=0)
    result = {"status": "ok", "photo": "source", "images": [str(photo)]}
    first = lab.tool_output(result, "footer", "first", "photo_image")
    first_bytes = [item.model_dump() for item in first]
    first_record = copy.deepcopy(lab.state["stage_images"][0])
    preview = lab.root / first_record["path"]
    preview_bytes = preview.read_bytes()
    lab.s.head = {"id": "c0008"}
    second = lab.tool_output(result, "footer", "retry", "photo_image")
    receipt = image_and_receipt(second)
    assert receipt["panels"][0]["sha256"] == digest
    assert receipt["panels"][0]["size_px"] == [1200, 800]
    assert receipt["checkpoint"] == "c0008"
    assert [item.model_dump() for item in first] == first_bytes
    assert lab.state["stage_images"][0] == first_record
    assert preview.read_bytes() == preview_bytes
    assert hashlib.sha256(photo.read_bytes()).hexdigest() == digest


def test_no_images_preserves_result_and_footer(tmp_path):
    lab = lab_at(tmp_path)
    result = {"status": "refused", "code": "E_ARGS"}
    output = lab.tool_output(result, "settled $0", "refused", "facet_edit")
    assert len(output) == 1 and isinstance(output[0], ToolOutputText)
    assert output[0].text == '{"status":"refused","code":"E_ARGS"}\nsettled $0'
    assert "stage_images" not in lab.state


def test_native_panels_outside_stage_package_are_refused(tmp_path):
    root = tmp_path / "stage"
    root.mkdir()
    outside = tmp_path / "outside.png"
    Image.new("RGB", (20, 20)).save(outside)
    lab = lab_at(root)
    with pytest.raises(ValueError, match="inside its package"):
        lab.tool_output({"images": [str(outside)]}, "footer", "outside", "photo_image")
