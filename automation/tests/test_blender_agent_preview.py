import asyncio
import base64
import hashlib
import json
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("agents")
from blender_agent import native_export, preview


class FakeServer:
    def __init__(self, unchanged=True, receipt=True):
        self.unchanged, self.receipt = unchanged, receipt

    async def call_tool(self, name, arguments):
        assert name == "execute_blender_code"
        path = Path(arguments["code"])
        chunk = json.dumps({"asset": {"version": "2.0"}}).encode()
        chunk += b" " * (-len(chunk) % 4)
        path.write_bytes(b"glTF" + struct.pack("<4I", 2, 20 + len(chunk), len(chunk), 0x4E4F534A) + chunk)
        receipt = {"status": "exported", "scene_unchanged": self.unchanged}
        text = native_export.RECEIPT_PREFIX + json.dumps(receipt) if self.receipt else "failed after writing a partial file"
        return SimpleNamespace(isError=False, content=[SimpleNamespace(type="text", text=text)])


@pytest.mark.parametrize("unchanged,receipt", [(False, True), (True, False)])
def test_never_render_partial_or_scene_mutating_export(tmp_path, monkeypatch, unchanged, receipt):
    monkeypatch.setattr(native_export, "export_code", lambda path, **kw: str(path))
    def unexpected(*args, **kwargs):
        pytest.fail("The AR renderer must not run after a failed export")
    monkeypatch.setattr(preview.archeck, "run", unexpected)
    with pytest.raises(RuntimeError, match="did not confirm"):
        asyncio.run(preview.ARPreview(FakeServer(unchanged, receipt), tmp_path).capture(
            "test", [preview.ARView(id="front")]))


def test_exact_export_is_rendered_and_views_are_forwarded(tmp_path, monkeypatch):
    monkeypatch.setattr(native_export, "export_code", lambda path, **kw: str(path))
    observed = {}
    def render(paths, output, **options):
        observed.update(path=paths["candidate"], data=paths["candidate"].read_bytes(), options=options)
        return {"validation": {"ok": False}, "models": {}}
    monkeypatch.setattr(preview.archeck, "run", render)
    result = asyncio.run(preview.ARPreview(FakeServer(), tmp_path).capture(
        "../../test", [preview.ARView(id="detail", yaw_degrees=57, pitch_degrees=-19)], "solid", "#ffffff"))
    assert observed["path"] == Path(result["glb"])
    assert observed["path"].is_relative_to(tmp_path)
    assert observed["data"].startswith(b"glTF")
    assert observed["options"]["ar_views"][0]["yaw_degrees"] == 57
    assert observed["options"]["background_color"] == "#ffffff"
    assert result["ar"]["validation"]["ok"] is False  # Do not turn compatibility failure into success.
    saved = json.loads(Path(result["metadata_path"]).read_text())
    assert saved == result


def test_reject_invalid_rear_pose_before_scene_access(tmp_path):
    with pytest.raises(ValueError, match="asset inspection"):
        asyncio.run(preview.ARPreview(None, tmp_path).capture(
            "test", [preview.ARView(id="rear", type="asset-back", yaw_degrees=20)]))


def test_compact_tool_observation_does_not_repeat_materials_commands_or_logs():
    result = {"glb": "candidate.glb", "sha256": "a" * 64, "metadata_path": "full.json",
              "export": {"scene_unchanged": True, "objects": [{"long": "data" * 1000}] * 100},
              "native_materials": [{"huge": "record" * 1000}] * 30,
              "ar": {"validation": {"ok": False, "reasons": ["real failure"]}, "command": ["secret-command"],
                     "stdout_tail": "log-content" * 1000, "models": {"candidate": {"render_files": [
                         {"view": "front", "path": "front.png", "sha256": "b" * 64}]}}}}
    compact = preview.compact_ar_result(result)
    encoded = json.dumps(compact)
    assert len(encoded) < 3500
    assert "secret-command" not in encoded and "log-content" not in encoded and "huge" not in encoded
    assert compact["validation"]["ok"] is False
    assert compact["omitted_from_response"]["native_materials"] == 30


def test_portrait_reuses_exact_glb_without_mcp_and_rejects_outside_paths(tmp_path, monkeypatch):
    from blender_agent import portrait
    agent_output = tmp_path / "agent"
    agent_output.mkdir()
    glb = agent_output / "saved.glb"
    chunk = json.dumps({"asset": {"version": "2.0"}}).encode()
    chunk += b" " * (-len(chunk) % 4)
    glb.write_bytes(b"glTF" + struct.pack("<4I", 2, 20 + len(chunk), len(chunk), 0x4E4F534A) + chunk)
    observed = {}
    def capture(path, output):
        observed.update(path=path, data=path.read_bytes())
        return {"validation": {"ok": True}}
    monkeypatch.setattr(portrait, "run_portrait", capture)
    p = preview.ARPreview(None, agent_output)
    result = asyncio.run(p.capture_portrait("detail", str(glb)))
    assert observed["path"] == glb and observed["data"] == glb.read_bytes()
    assert result["export"] == {"live_scene_accessed": False, "source_glb_reused": True}
    with pytest.raises(ValueError, match="inside"):
        asyncio.run(p.capture_portrait("detail", str(tmp_path / "outside.glb")))


@pytest.mark.parametrize("valid", [True, False])
def test_portrait_tool_preserves_pinned_sdk_images_even_after_compatibility_failure(tmp_path, monkeypatch, valid):
    """Exercise the installed SDK conversion, not only our Python return types."""
    from agents.items import ItemHelpers
    from agents.tool_context import ToolContext
    from openai.types.responses import ResponseFunctionToolCall
    from PIL import Image

    labels = ["clean-eye-detail", "condition-a-portrait", "condition-a-eye-detail",
              "condition-b-portrait", "condition-b-eye-detail"]
    captures = []
    for i, label in enumerate(labels):
        path = tmp_path / (label + ".png")
        Image.new("RGB", (16 + i, 10), (i * 40, 20, 30)).save(path)
        captures.append({"label": label, "path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                         "width": 16 + i, "height": 10, "send_to_agent": label != "condition-b-portrait"})
    p = preview.ARPreview(None, tmp_path)
    async def capture(label, glb_path):
        return {"glb": glb_path, "sha256": "a" * 64, "metadata_path": str(tmp_path / "full.json"),
                "portrait": {"captures": captures, "validation": {"ok": valid}, "comparison_key": "matched",
                             "omissions": ["Only the actual detected pose"],
                             "lighting": {"internal_numeric_setting": 12345}}}
    monkeypatch.setattr(p, "capture_portrait", capture)
    tool = p.portrait_tool()
    arguments = json.dumps({"label": "offline", "glb_path": str(tmp_path / "exact.glb")})
    context = ToolContext(context=None, tool_name=tool.name, tool_call_id="offline-portrait", tool_arguments=arguments)
    outputs = asyncio.run(tool.on_invoke_tool(context, arguments))
    call = ResponseFunctionToolCall(type="function_call", call_id="offline-portrait", name=tool.name, arguments=arguments)
    payload = ItemHelpers.tool_call_output_item(call, outputs)["output"]
    assert isinstance(payload, list)  # A stringified image list is not image evidence.
    images = [item for item in payload if item["type"] == "input_image"]
    expected = [row for row in captures if row["send_to_agent"]]
    assert len(images) == len(expected)
    for image, row in zip(images, expected):
        assert image["detail"] == "high"
        assert base64.b64decode(image["image_url"].split(",", 1)[1]) == Path(row["path"]).read_bytes()
    text = "\n".join(item["text"] for item in payload if item["type"] == "input_text")
    assert "12345" not in text and "internal_numeric_setting" not in text
    assert len(text) < 4000
    assert json.loads(payload[0]["text"])["validation"]["ok"] is valid


@pytest.mark.parametrize("continuity,measured,fit", [("missing cross-section", True, True),
    (None, False, True), (None, True, False), (None, True, None)])
def test_render_success_cannot_hide_structural_failure(continuity, measured, fit):
    ar = {"validation": {"ok": True, "harness_ok": True, "reasons": [],
                         "models": {"candidate": {"ok": True, "reasons": []}}},
          "models": {"candidate": {"continuity_failure": continuity, "continuity_measured": measured,
                                     "synthetic_fit_ready": fit}}}
    checked = preview.validate_preview_structure(ar)
    assert checked["ok"] is False
    assert ar["validation"]["ok"] is True  # Do not overwrite the recorded render-only verdict.
    reasons = checked["models"]["candidate"]["reasons"]
    if continuity:
        assert any(continuity in reason for reason in reasons)
    if not measured or fit is None:
        assert any("not measured" in reason for reason in reasons)


def test_measured_continuity_and_fitting_pass_but_do_not_override_other_failure():
    ar = {"validation": {"ok": True}, "models": {"candidate": {
        "continuity_failure": None, "continuity_measured": True, "synthetic_fit_ready": True}}}
    assert preview.validate_preview_structure(ar)["ok"] is True
    ar["validation"]["ok"] = False
    assert preview.validate_preview_structure(ar)["ok"] is False


def test_capture_registers_exact_host_json_only(tmp_path, monkeypatch):
    monkeypatch.setattr(native_export, "export_code", lambda path, **kw: str(path))
    monkeypatch.setattr(preview.archeck, "run", lambda *a, **k: {"validation": {"ok": False}, "models": {}})
    producer = preview.ARPreview(FakeServer(), tmp_path)
    result = asyncio.run(producer.capture("test", [preview.ARView(id="front")]))
    artifact = producer.evidence_registry.read(result["metadata_path"], "/ar/validation")
    assert artifact["data"]["ok"] is False
    assert len(producer.evidence_registry.entries) == 3  # Metadata, native material inspection, export receipt.
    with pytest.raises(ValueError, match="not registered"):
        producer.evidence_registry.read(str(Path(result["directory"]) / "export-response.txt"))


def test_saved_compatibility_aggregate_agrees_with_structure_not_render_success(tmp_path, monkeypatch):
    monkeypatch.setattr(native_export, "export_code", lambda path, **kw: str(path))
    monkeypatch.setattr(preview.archeck, "run", lambda *a, **k: {
        "validation": {"ok": True}, "all_compatible_with_lenses": True,
        "models": {"candidate": {"continuity_measured": True,
                                  "continuity_failure": "missing cross-section", "synthetic_fit_ready": True}}})
    producer = preview.ARPreview(FakeServer(), tmp_path)
    result = asyncio.run(producer.capture("failed", [preview.ARView(id="front")]))
    assert result["ar"]["render_validation"]["ok"] is True
    assert result["ar"]["validation"]["ok"] is False
    saved = producer.evidence_registry.read(result["metadata_path"])["data"]["ar"]
    assert saved["all_compatible_with_lenses"] is False
    assert saved["validation"]["ok"] is False


def test_ar_tool_forwards_failure_images_but_never_hash_mismatches_or_external_paths(tmp_path, monkeypatch):
    from agents.tool_context import ToolContext
    from PIL import Image

    output = tmp_path / "agent"
    output.mkdir()
    good = output / "good.png"
    Image.new("RGB", (8, 8), "red").save(good)
    outside = tmp_path / "outside.png"
    Image.new("RGB", (8, 8), "blue").save(outside)
    rows = [{"view": "good", "path": str(good), "sha256": hashlib.sha256(good.read_bytes()).hexdigest()},
            {"view": "stale", "path": str(good), "sha256": "0" * 64},
            {"view": "outside", "path": str(outside), "sha256": hashlib.sha256(outside.read_bytes()).hexdigest()}]
    p = preview.ARPreview(None, output)
    async def capture(*args):
        return {"glb": "exact.glb", "sha256": "a" * 64, "metadata_path": "full.json",
                "export": {"scene_unchanged": True}, "ar": {"validation": {"ok": True}, "models": {
                    "candidate": {"continuity_measured": True, "continuity_failure": "missing cross-section",
                                  "synthetic_fit_ready": True, "render_files": rows}}}}
    monkeypatch.setattr(p, "capture", capture)
    tool = p.tool()
    args = json.dumps({"label": "failure", "views": [{"id": "front", "yaw_degrees": 0,
        "pitch_degrees": 0, "roll_degrees": 0, "type": "pose"}], "background": "checker", "background_color": None})
    context = ToolContext(context=None, tool_name=tool.name, tool_call_id="offline", tool_arguments=args)
    outputs = asyncio.run(tool.on_invoke_tool(context, args))
    summary = json.loads(outputs[0].text)
    assert summary["validation"]["ok"] is False and summary["render_validation"]["ok"] is True
    assert summary["diagnostics"]["candidate"]["continuity_failure"] == "missing cross-section"
    images = [o for o in outputs if o.type == "image"]
    assert len(images) == 1
    assert base64.b64decode(images[0].image_url.split(",", 1)[1]) == good.read_bytes()
