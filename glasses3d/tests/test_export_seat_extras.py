"""WP4 exporter allowlist (g3d/export_glb.py SEAT_EXTRAS; WP4_DESIGN.md §1 finding 1, §9): g3d_seat and
g3d_hidden_x_ranges_mm go from each source object to its export node's extras, nothing else of the source's custom
properties does, the live scene is unchanged, and the host seating check reads the declared frame type and ranges.
The WP3 suite (test_export_blender.py, scene invariance included) runs unchanged. Skipped without Blender."""
import json
from pathlib import Path

import pytest

from glasses3d.g3d_host import blender, glb
from glasses3d.g3d_host.seat_check import check_glb

HERE = Path(__file__).resolve().parent
BUILD = HERE / "blender" / "seat_build.py"
SCRIPT = HERE / "blender" / "seat_extras_export.py"

pytestmark = pytest.mark.skipif(blender.blender_exe() is None, reason="Blender 5.2 not installed")


@pytest.fixture(scope="module")
def exported(tmp_path_factory):
    d = tmp_path_factory.mktemp("seat_extras")
    proc = blender.run(None, BUILD, [d, "metal"], timeout_s=300)
    scene = d / "metal.blend"
    assert scene.is_file(), proc.stdout[-1500:] + proc.stderr[-1500:]
    out, receipt = d / "extras.glb", d / "receipt.json"
    proc = blender.run(scene, SCRIPT, [out, receipt], timeout_s=300)
    assert receipt.is_file(), proc.stdout[-1500:] + proc.stderr[-1500:]
    return json.loads(receipt.read_text(encoding="utf-8")), out


def node_extras(g, name):
    return next(n for n in g.nodes if n.get("name") == name).get("extras") or {}


def test_allowlisted_properties_reach_the_node_extras_and_nothing_else(exported):
    receipt, out = exported
    assert receipt["status"] == "exported" and receipt["scene_unchanged"] is True, receipt
    assert receipt["snapshot_equal_after"] is True
    assert receipt["seat_extras"] == {"Eyewire": ["g3d_hidden_x_ranges_mm"],
                                      "Lens_L": ["g3d_seat", "g3d_hidden_x_ranges_mm"]}
    g = glb.load(out)
    lens = node_extras(g, "Lens_L")
    assert set(lens) == {"partRole", "g3d_seat", "g3d_hidden_x_ranges_mm"}, lens
    assert json.loads(lens["g3d_seat"])["frame_type"] == "metal"
    assert json.loads(lens["g3d_hidden_x_ranges_mm"]) == [[-40.0, -20.0]]
    ew = node_extras(g, "Eyewire")
    assert set(ew) == {"partRole", "g3d_hidden_x_ranges_mm"}, ew
    assert json.loads(ew["g3d_hidden_x_ranges_mm"]) == [[-40.0, -20.0]]          # ID-property list -> JSON
    for m in g.meshes:                                                          # mesh extras: partRole only
        assert set(m.get("extras") or {}) <= {"partRole"}, m.get("extras")


def test_the_host_check_reads_the_declared_frame_type_and_ranges(exported):
    _, out = exported
    rep = check_glb(out)
    lens = rep["lenses"][0]
    assert lens["frame_type"] == "metal" and lens["frame_type_source"] == "g3d_seat", lens
    assert lens["ranges"] == [(-40.0, -20.0)] or lens["ranges"] == [[-40.0, -20.0]]
    assert not lens["provisional"]
