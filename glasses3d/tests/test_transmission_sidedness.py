"""M5-fix-3: sidedness is based on actual exported transmission and every whole solid using it."""
import json
from pathlib import Path

import pytest

from glasses3d.g3d_host import blender


@pytest.fixture(scope="module")
def proof(tmp_path_factory):
    if blender.blender_exe() is None:
        pytest.skip("Blender 5.2 not installed")
    out = tmp_path_factory.mktemp("transmission-sidedness")
    proc = blender.run(None, Path(__file__).parent / "blender/transmission_sidedness.py", [out], timeout_s=180)
    assert (out / "report.json").is_file(), proc.stdout + proc.stderr
    return json.loads((out / "report.json").read_text())


@pytest.mark.parametrize("name", ["closed lens", "crystal frame", "solid patch A", "solid patch B",
                                 "unused on open", "mirrored", "loose wire", "nonplanar faces"])
def test_every_outward_transmitting_solid_is_single_sided(proof, name):
    mat = next(mat for mat in proof["materials"] if mat["name"] == name)
    assert mat.get("doubleSided", False) is False
    assert any(row["material"] == name for row in proof["receipt"]["single_sided_materials"])


@pytest.mark.parametrize("name", ["inward lens", "open lens", "shared open and closed", "mixed shells", "open own", "pinched shells"])
def test_unsafe_geometry_keeps_setting_and_one_plain_note(proof, name):
    mat = next(mat for mat in proof["materials"] if mat["name"] == name)
    assert mat["doubleSided"] is True
    notes = [note for note in proof["receipt"]["notes"] if f"material {name!r} keeps its sidedness" in note]
    assert len(notes) == 1
    assert "open" in notes[0] or "inward" in notes[0]
    assert not any(row["material"] == name for row in proof["receipt"]["single_sided_materials"])


def test_authored_single_sided_open_and_opaque_are_unchanged(proof):
    mats = {mat["name"]: mat for mat in proof["materials"]}
    assert mats["open already single"].get("doubleSided", False) is False
    assert mats["opaque"]["doubleSided"] is True
    assert not any("'opaque'" in note for note in proof["receipt"]["notes"])
    assert proof["receipt"]["scene_unchanged"] is True


def test_inconsistent_winding_is_not_outward(proof):
    mat = next(mat for mat in proof["materials"] if mat["name"] == "inconsistent")
    assert mat["doubleSided"] is True
    assert any("inconsistent face winding" in note for note in proof["receipt"]["notes"])


def test_role_material_copies_keep_independent_whole_mesh_ownership(proof):
    mats = {mat["extras"]["partRole"]: mat for mat in proof["materials"] if mat["name"] == "different roles"}
    assert mats["frame"].get("doubleSided", False) is False
    assert mats["lens"]["doubleSided"] is True
