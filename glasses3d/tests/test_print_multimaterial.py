"""Native Blender regressions for G3B-01 and PP2-13, using no saved product data."""
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from glasses3d.g3d import printuv as PU
from glasses3d.g3d_host import blender
from glasses3d.g3d_host.photoprint import manifest as MF


SCRIPT = Path(__file__).parent / "blender" / "print_multimaterial.py"
pytestmark = pytest.mark.skipif(blender.blender_exe() is None, reason="Blender 5.2 not installed")


@pytest.fixture(scope="module")
def scene(tmp_path_factory):
    out = tmp_path_factory.mktemp("print_multi")
    proc = blender.run(None, SCRIPT, ["build", out], timeout_s=120)
    assert (out / "scene.blend").is_file(), proc.stdout + proc.stderr
    return out


def apply(scene, mode):
    proc = blender.run(scene / "scene.blend", SCRIPT, [mode, scene], timeout_s=120)
    receipt = scene / f"{mode}.json"
    assert receipt.is_file(), proc.stdout + proc.stderr
    return json.loads(receipt.read_text(encoding="utf-8"))


def test_print_uv_postcheck_includes_every_material_primitive(scene):
    before = PU.glbmesh.read_glb(scene / "before.glb")
    assert [p.node for p in before] == ["A", "A", "B", "B"]
    result = apply(scene, "default")
    assert result["uv"]["status"] == "ok", result["uv"]
    after = PU.glbmesh.read_glb(scene / "default.glb")
    assert len(after) == len(before)
    for a, b in zip(after, before):
        assert (a.node, a.index) == (b.node, b.index)
        np.testing.assert_array_equal(a.positions_mm[a.triangles], b.positions_mm[b.triangles])
    assert all(p["positions_identical_in_order"] and p["uv_max_err_texel"] < 1e-3
               and p["normal_max_change_deg"] < 0.1 for p in result["uv"]["postcheck"].values())


def test_bake_options_replay_in_blender_and_host_freshness(scene):
    bake = scene / "bake"
    bake.mkdir()
    V, F, part, names = PU.glbmesh.mesh_arrays(PU.glbmesh.read_glb(scene / "before.glb"))
    layout = PU.layout(V, F, part, names, ["A", "B"], 16, mirror=False, atlas_max=256)
    assert layout.tx_per_mm < 16 and layout.report["pass"]
    (bake / "layout.json").write_text(json.dumps(layout.to_json()), encoding="utf-8")
    Image.new("RGB", (layout.width, layout.height), (85, 105, 135)).save(bake / "print.png")
    report = {"inputs": {"sources": {"glb": str(scene / "before.glb")}}, "parts": ["A", "B"],
              "texture_parts": ["A", "B"], "no_texture_parts": [],
              "print_uv": MF.print_identity(V, F, part, names, layout)}
    (bake / "report.json").write_text(json.dumps(report), encoding="utf-8")
    MF.write_manifest(bake)
    man = MF.load_manifest(bake)
    assert man["layout_options"] == {"mirror": False, "atlas_max": 256}
    expected, error = MF.expected_corners(man)
    assert error is None and set(expected) == {"A", "B"}, error
    result = apply(scene, "bake")
    assert result["uv"]["status"] == "ok", result["uv"]
    assert result["uv"]["layout_options"] == man["layout_options"]
    assert result["uv"]["partners"] == {}
    assert result["attach"]["status"] == "ok", result["attach"]
    checked = MF.check_glb(scene / "bake.glb", man)
    assert checked["ok"] and not checked["stale"], checked
    assert all(row["uv_checked"] for row in checked["parts"].values())
