"""Real two-lens crystal grooves, cap tessellation, rollback and material export parity."""
import json
from pathlib import Path

import pytest

from glasses3d.g3d_host import blender, glb, seat_check

HERE = Path(__file__).parent
pytestmark = [pytest.mark.slow, pytest.mark.skipif(blender.blender_exe() is None, reason="Blender 5.2 not installed")]


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    root = tmp_path_factory.mktemp("seat_generality")
    scenes = root / "scenes"
    built = blender.run(None, HERE / "blender/seat_build.py", [scenes, "crystal", "browline", "acetate_flat"], timeout_s=300)
    assert built.returncode == 0, built.stdout + built.stderr
    output = root / "result.json"
    ran = blender.run(None, HERE / "blender/seat_generality.py", [scenes, output], timeout_s=300)
    assert output.exists(), ran.stdout + ran.stderr
    return json.loads(output.read_text(encoding="utf-8")), output


def test_two_crystal_lenses_keep_separate_grooves(result):
    rec = result[0]["pair"]
    assert rec["first"]["status"] == "seated", rec
    assert rec["second"]["status"] == "seated", rec
    assert len(rec["first_mods"]) == 1 and len(rec["both_mods"]) == 2
    assert rec["first_mods"][0] in rec["both_mods"]
    assert rec["remaining"] == rec["first_mods"]
    assert len(rec["check"]["lenses"]) == 2
    assert rec["check"]["ok"], rec["check"]
    assert all(r["crystal_overlap_mm3"] <= .01 for r in rec["check"]["lenses"])


def test_nearby_clear_part_without_intersection_stays_untouched(result):
    rec = result[0]["near"]
    assert rec["result"]["status"] == "seated", rec
    assert rec["pad_unchanged"]
    parts = rec["result"]["profile_result"]["parts"]
    assert any(r["part"] == "Clear nearby pad" and r["status"] == "untouched" for r in parts)


@pytest.mark.parametrize("kind", ["key", "index"])
def test_hide_lookup_failure_after_write_restores_exact_scene(result, kind):
    rec = result[0][f"rollback_{kind}"]
    assert rec["calls"] == 2, rec
    assert rec["result"]["code"] == "E_INTERNAL", rec
    assert rec["restored"] and rec["result"]["rolled_back"], rec


@pytest.mark.parametrize("kind", ["ngon", "fan"])
def test_ngon_and_fan_caps_seat(result, kind):
    rec = result[0][kind]
    assert rec["status"] == "seated", rec


def test_blender_and_exported_material_translucency_agree(result):
    rec, output = result
    g = glb.load(output.with_suffix(".glb"))
    nodes, _, _ = seat_check.read_nodes(g)
    actual = {n["name"]: n["translucent"] for n in nodes}
    assert rec["materials"] == actual
    assert all(actual[k] for k in ("transmission", "texture_transmission", "value_transmission", "alpha", "texture_alpha"))
    assert not any(actual[k] for k in ("opaque", "disconnected", "glass", "unused", "procedural_transmission",
                                      "procedural_alpha", "volume"))
