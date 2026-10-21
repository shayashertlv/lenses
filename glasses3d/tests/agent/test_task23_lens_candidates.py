"""Candidate bounds, immutable GLB copies, native tiles and actual SDK image delivery."""
import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest
from agents.tool import ToolOutputImage

from glasses3d.agent import loop as L, replay as R, tools as T
from glasses3d.agent import owner_lens_edit as O
from glasses3d.agent.tools import lens_candidates as LC
from glasses3d.g3d.glb_patch import write_chunks
from glasses3d.g3d_host.camera import Camera, NormFrame
from glasses3d.tests.agent.test_job_tools import scene, invoke, record
from glasses3d.tests.agent import loop_support
from glasses3d.tests.agent.loop_support import make_job, spec_for, FakeScene


VALUES = dict(tint=[.2, .4, .6], transmission=.8, metallic=.15, roughness=.2, ior=1.7,
    iridescence=.5, iridescence_ior=1.4, iridescence_thickness_min=150, iridescence_thickness_max=550)


def candidates():
    return [{"label": "A", **VALUES}, {"label": "B", **{**VALUES, "ior": 2.2}}]


@pytest.mark.parametrize("change", [[], candidates()[:1], candidates() * 4,
    [candidates()[0]] * 2, [{**candidates()[0], "label": "\n"}, candidates()[1]],
    [{**candidates()[0], "ior": .5}, candidates()[1]],
    [{**candidates()[0], "metallic": float("nan")}, candidates()[1]],
    [{**candidates()[0], "transmission": True}, candidates()[1]],
    [{**candidates()[0], "roughness": -1}, candidates()[1]],
    [{**candidates()[0], "tint": [1, 1]}, candidates()[1]]])
def test_invalid_candidates_refuse_before_artifacts(change, tmp_path):
    result = LC.capture(tmp_path, {}, "bad", candidates=change, materials=["Lens"])
    assert result["status"] == "unavailable"
    assert not list(tmp_path.iterdir())


@pytest.fixture
def copied_render(scene, monkeypatch):
    document = {"asset": {"version": "2.0"}, "materials": [
        {"name": "Lens", "extras": {"partRole": "lens"}},
        {"name": "Mark", "extras": {"partRole": "lens"}},
        {"name": "Frame", "extras": {"partRole": "frame"}}],
        "nodes": [{"mesh": i} for i in range(3)],
        "meshes": [{"primitives": [{"material": i}]} for i in range(3)]}
    path = Path(scene.export["path"])
    path.write_bytes(write_chunks(document, b"preserve geometry"))
    scene.export["sha256"] = LC.arview.sha256(path)
    basis_dir = scene.root / "cameras/basis-01"
    basis_dir.mkdir(parents=True)
    (basis_dir / "basis.json").write_text("{}")
    photo = np.full((240, 320, 3), 177, np.uint8)
    bs = SimpleNamespace(id="basis-01", dir=basis_dir, refs={"reference-01": {"view_kind": "front", "photo_sha256": "p"}},
        usable_refs=lambda: ["reference-01"], photo=lambda _: photo, shape=lambda _: photo.shape[:2],
        camera=lambda _: Camera(0, 0, 0, .3, 300, 159.5, 119.5),
        frame=NormFrame((0, 0, 0), 150), pose=lambda _: {}, meta={"cameras_json_sha256": "c"})
    monkeypatch.setattr(LC.C, "load_basis", lambda *_: bs)
    rendered = []
    def fake_worn(root, export, call_id):
        rendered.append(export)
        images = {}
        for view in ("Worn front", "Worn head yaw -50 degrees", "Worn head yaw +50 degrees"):
            image = Path(export["path"]).parent / (view + ".png")
            Image.new("RGB", (320, 240), (len(rendered) * 30, 70, 90)).save(image)
            images[view] = str(image)
        return images, {"report": "synthetic-worn.json"}
    def fake_front(root, export, directory, camera, photo, ref):
        image = directory / "front.png"
        Image.new("RGB", (320, 240), (len(rendered) * 30, 90, 70)).save(image)
        return str(image), ["reference-01 lens: synthetic diagnostic"], "synthetic-front.json"
    monkeypatch.setattr(LC, "_worn", fake_worn)
    monkeypatch.setattr(LC, "_front", fake_front)
    return scene, path, rendered


def test_capture_preserves_source_other_materials_binary_head_and_native_pixels(copied_render):
    scene, path, rendered = copied_render
    original, head = path.read_bytes(), copy.deepcopy(scene.s.head)
    result = LC.capture(scene.root, scene.export, "candidate-test", candidates=candidates(), materials=["Lens"])
    assert result["status"] == "ok", result
    assert path.read_bytes() == original and scene.s.head == head
    assert len(rendered) == 2 and len(result["images"]) == 4
    before, before_chunks = O._parse(original)
    for index, row in enumerate(result["candidates"]):
        after, after_chunks = O._parse(Path(row["export_path"]).read_bytes())
        assert after["materials"][1:] == before["materials"][1:]
        assert [chunk[2] for chunk in after_chunks[1:]] == [chunk[2] for chunk in before_chunks[1:]]
        assert O._material_values(after["materials"][0]) == row["values"]
    for image in result["images"]:
        assert image["native_pixels"] and image["patches_32px"] <= 2500
        assert image["headers"][-2:] == ["A", "B"]
    assert len(result["lens_colour_gap"]) == 2
    assert result["image_cost"]["estimated_image_tokens"] > 0


@pytest.mark.parametrize("materials", [["Frame"], ["missing"], [], ["Lens", "Lens"]])
def test_material_selection_refuses_before_render(copied_render, materials):
    scene, path, rendered = copied_render
    result = LC.capture(scene.root, scene.export, "invalid-material", candidates=candidates(), materials=materials)
    assert result["status"] == "unavailable" and not rendered


def test_native_tiles_cover_every_source_pixel_even_six_candidates(tmp_path):
    paths = []
    for index in range(6):
        path = tmp_path / f"source-{index}.png"
        pixels = np.zeros((700, 1025, 3), np.uint8)
        pixels[:, :, 0] = np.arange(1025, dtype=np.uint16)[None, :] % 256
        pixels[:, :, 1] = index * 40
        Image.fromarray(pixels).save(path)
        paths.append(path)
    tiles = LC._panels(paths, tmp_path / "comparison.png", "Worn native", [f"Candidate {i}" for i in range(6)])
    covered = np.zeros((700, 1025), np.uint8)
    for tile in tiles:
        x0, y0, x1, y1 = tile["crop_box"]
        covered[y0:y1, x0:x1] += 1
        assert tile["patches_32px"] <= 2500
        with Image.open(tile["path"]) as canvas:
            for source, box in zip(paths, tile["panel_boxes"]):
                with Image.open(source) as expected:
                    assert canvas.crop(box).tobytes() == expected.crop(tile["crop_box"]).tobytes()
    assert np.all(covered == 1)


def test_actual_sdk_schema_transport_and_scene_immutability(copied_render):
    scene, path, rendered = copied_render
    scene.instruction_profile = "decision-20261021"
    before = copy.deepcopy(scene.s.head)
    parts = invoke(scene, "compare_lens_candidates", {"candidates": candidates(), "materials": ["Lens"]})
    assert record(parts)["status"] == "ok"
    assert len([part for part in parts if isinstance(part, ToolOutputImage)]) == 4
    assert scene.s.head == before
    assert len(scene.calls) == 1


def test_scripted_sdk_flow_compares_then_applies_without_paid_transport(copied_render, monkeypatch, tmp_path):
    scene, _, _ = copied_render
    scene.instruction_profile = "decision-20261021"
    tools = [tool for tool in T.build_tools(scene) if tool.name in {"compare_lens_candidates", "execute_blender_code"}]
    settings = L.RunSettings(effort="max", max_turns=4, max_output_tokens=100, settled_stop_usd="20")
    with monkeypatch.context() as patch:
        patch.setattr(loop_support, "tool_registry_sha256", lambda tools: L.tool_registry_sha256(tools, settings))
        job, sha = make_job(tmp_path / "scripted", tools=tools, settings=settings, cap="20")
    replies = [R.tool_call("compare_lens_candidates", {"candidates": candidates(), "materials": ["Lens"]}, call_id="compare"),
        R.tool_call("execute_blender_code", {"code": "values = " + repr(VALUES) + "\ng3d.apply_lens_candidate('Lens', source_double_sided=False, **values)",
            "label": "Retain compared A"}, call_id="apply"), R.message("Compared A/B and applied A.")]
    transport = R.ScriptedOpenAI(replies)
    result = asyncio.run(L.run_astra(spec_for(job, "20261007T120000-run001", sha, tools, transport,
        settings=settings, requested_cap_usd="20"), scene=FakeScene()))
    assert result["status"] == "completed", result
    assert len(transport.creates) == 3 and transport.violations == []
    assert [call[1] for call in scene.calls] == ["compare_lens_candidates", "execute_blender_code"]
    assert sum(part.get("type") == "input_image" for item in transport.creates[1]["input"]
               if item.get("type") == "function_call_output" for part in item["output"] if isinstance(part, dict)) == 4


@pytest.mark.parametrize("closed_outward,transmission,allowed", [(True, .5, False), (False, .5, True),
                                                               (None, .5, False), (True, 0, True)])
def test_sidedness_transitions_keep_exporter_rule(copied_render, closed_outward, transmission, allowed):
    scene, source, rendered = copied_render
    doc, chunks = O._parse(source.read_bytes())
    doc["materials"][0]["doubleSided"] = True
    source.write_bytes(write_chunks(doc, chunks[1][1]))
    scene.export["sha256"] = LC.arview.sha256(source)
    if closed_outward is not None:
        receipt = source.with_suffix(".json")
        receipt.write_text(json.dumps({"material_sidedness": [{"material": "Lens", "partRole": "lens",
            "closed_outward": closed_outward, "export_double_sided": True}]}))
        scene.export["receipt"] = str(receipt)
    rows = [{**row, "transmission": transmission} for row in candidates()]
    result = LC.capture(scene.root, scene.export, "sidedness", candidates=rows, materials=["Lens"])
    assert (result["status"] == "ok") == allowed
    if allowed:
        assert all("source_double_sided=True" in row["apply_code"] for row in result["candidates"])
    else:
        assert "sidedness" in result["message"] and not rendered
