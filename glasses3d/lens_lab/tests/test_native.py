"""Opt-in real-worker regressions. One serial fixture, no product source writes."""
import json
import os
from pathlib import Path

import pytest

from glasses3d.lens_lab import harness as H

pytestmark = [pytest.mark.native,
              pytest.mark.skipif(not os.environ.get("LENS_LAB_NATIVE_TRIAL"),
                                 reason="Set LENS_LAB_NATIVE_TRIAL to a fresh private lab trial after task17 exits")]


@pytest.fixture(scope="module")
def lab():
    path = Path(os.environ["LENS_LAB_NATIVE_TRIAL"]).resolve()
    if not (path / "trial.json").exists():
        H.init_trial(path)
    with H.LabSession(path) as live:
        yield live


def test_neutral_baseline_is_glass_and_mesh_exact(lab):
    assert lab.verify()["mesh_fingerprint"] == lab.manifest["mesh_fingerprint"]
    output = lab.call("export_glb", {})["result"]
    assert output["status"] == "exported", output
    doc = H.gltf_document(Path(lab.manifest["job_dir"]) / output["path"])
    mats = [m for m in doc["materials"] if m.get("name") in lab.manifest["lens_materials"]]
    assert len(mats) == 1
    pbr = mats[0]["pbrMetallicRoughness"]
    assert pbr.get("baseColorFactor", [1, 1, 1, 1]) == [1, 1, 1, 1]
    assert pbr["metallicFactor"] == 0
    assert mats[0]["extensions"]["KHR_materials_transmission"]["transmissionFactor"] == 1
    assert "KHR_materials_iridescence" not in mats[0].get("extensions", {})


def test_geometry_change_is_refused_and_reopened_baseline_restored(lab):
    before = lab.verify()
    output = lab.call("execute_blender_code", {
        "code": "obj = next(o for o in bpy.context.scene.objects if o.type == 'MESH')\nobj.data.vertices[0].co.x += 1",
        "label": "LAB deliberate scope refusal",
    })["result"]
    assert output["code"] == "E_LAB_SCOPE", output
    assert output["restored_to"]
    assert lab.verify() == before
    replay = lab.call("restore_checkpoint", {"checkpoint": output["refused_checkpoint"]})["result"]
    assert replay["code"] == "E_LAB_SCOPE", replay
    assert lab.verify() == before


@pytest.mark.parametrize("edit", ["obj.location.x += 1",
                                 "obj.data.uv_layers.active.data[0].uv.x += .1",
                                 "obj.data.polygons[0].use_smooth = not obj.data.polygons[0].use_smooth"])
def test_transform_uv_and_shading_changes_are_refused(lab, edit):
    names = lab.manifest["lens_materials"]
    output = lab.call("execute_blender_code", {
        "code": f"obj = next(o for o in bpy.context.scene.objects if o.type == 'MESH' and "
                f"any(s.material and s.material.name in {names!r} for s in o.material_slots))\n{edit}",
    })["result"]
    assert output["code"] == "E_LAB_SCOPE", output


def test_non_lens_material_change_is_refused(lab):
    names = lab.manifest["lens_materials"]
    output = lab.call("execute_blender_code", {
        "code": f"mat = next(m for m in bpy.data.materials if m.name not in {names!r})\nmat.diffuse_color = (1, 0, 0, 1)",
    })["result"]
    assert output["code"] == "E_LAB_SCOPE", output


def test_coating_helper_and_material_edit_survive_export(lab):
    names = lab.manifest["lens_materials"]
    output = lab.call("execute_blender_code", {
        "code": f"g3d.lens_coating({names[0]!r}, factor=.5, ior=1.4, thickness_min_nm=100, thickness_max_nm=400, tint=(.5,.4,.6))",
        "label": "LAB synthetic coating export proof",
    })["result"]
    assert output["status"] == "ok", output
    export = lab.call("export_glb")["result"]
    doc = H.gltf_document(Path(lab.manifest["job_dir"]) / export["path"])
    mat = next(m for m in doc["materials"] if m.get("name") == names[0])
    assert mat["extensions"]["KHR_materials_iridescence"]["iridescenceFactor"] == .5
    assert lab.verify()["protected_scene_unchanged"]


def test_real_photo_look_emits_exact_saved_images_and_pipeline_text(lab):
    from PIL import Image
    receipt = lab.call("compare_look_to_photos", {"refs": ["reference-01"]})
    assert receipt["result"]["status"] == "ok", receipt["result"]
    assert receipt["output_text"] and receipt["images"]
    for row in receipt["images"]:
        path = Path(row["path"])
        assert path.is_relative_to(lab.root)
        assert H.sha256(path) == row["sha256"]
        with Image.open(path) as image:
            assert image.width > 0 and image.height > 0
    persisted = json.loads((lab.root / "emitted-images.json").read_text())
    assert persisted == lab.scene._emitted_images and persisted
    assert lab.verify()["protected_scene_unchanged"]


def test_restore_and_finish_never_publish_product(lab):
    output = lab.call("restore_checkpoint", {"checkpoint": lab.manifest["baseline_checkpoint"]})["result"]
    assert output["status"] == "ok", output
    receipt = lab.call("finish", {"summary": "LAB native wrapper proof; no product delivery"})
    result = receipt["result"]
    assert result["status"] == "lab_finished", result
    assert result["product_deliverable"] is False
    assert result["final_materials"] and result["native_materials"]
    assert result["api_requests"] == result["api_spend_usd"] == 0
    assert not (Path(lab.manifest["job_dir"]) / "deliverable").exists()
    assert all(H.sha256(p) == digest for p, digest in lab.manifest["source_files"].items())
