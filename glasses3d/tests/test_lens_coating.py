"""Coating survives the real Blender exporter without replacing authored material graphs."""
import json
from pathlib import Path

import pytest

from glasses3d.g3d import safe_mode
from glasses3d.g3d_host import blender


@pytest.fixture(scope="module")
def proof(tmp_path_factory):
    if blender.blender_exe() is None:
        pytest.skip("Blender 5.2 not installed")
    out = tmp_path_factory.mktemp("lens-coating")
    result = blender.run(None, Path(__file__).parent / "blender/lens_coating.py", [out], timeout_s=180)
    report = out / "report.json"
    assert report.is_file(), result.stdout + result.stderr
    return json.loads(report.read_text(encoding="utf-8"))


def extension(case, name):
    return case["materials"][0].get("extensions", {}).get("KHR_materials_" + name)


@pytest.mark.parametrize("name,present", [("wrong_both", False), ("factor_only", False),
                                          ("correct_both", True), ("occlusion_only", False)])
def test_real_exporter_four_socket_cases(proof, name, present):
    case = proof["cases"][name]
    assert (extension(case, "iridescence") is not None) == present
    assert any("thin film" in s for s in case["receipt"]["optical_warnings"]) != present


def test_missing_extension_is_loss_even_at_numeric_defaults(proof):
    case = proof["cases"]["missing_extension_at_numeric_defaults"]
    assert extension(case, "iridescence") is None
    assert any("thin film" in s for s in case["receipt"]["optical_warnings"])


def test_helper_exports_film_range_tint_and_retains_original_optics(proof):
    case = proof["cases"]["helper"]
    irid = extension(case, "iridescence")
    assert irid["iridescenceFactor"] == 1
    assert irid["iridescenceIor"] == pytest.approx(1.38)
    assert irid["iridescenceThicknessMinimum"] == 140
    assert irid["iridescenceThicknessMaximum"] == 460
    assert case["materials"][0]["pbrMetallicRoughness"]["baseColorFactor"][:3] == pytest.approx([.12, .55, .4])
    assert extension(case, "transmission")["transmissionFactor"] == pytest.approx(.76)
    assert extension(case, "ior")["ior"] == pytest.approx(1.46)
    assert proof["helper"]["effective_uniform_thickness_nm"] == 460
    assert proof["preserved"]["native_thickness"] == 460
    assert proof["preserved"]["other_principled"] == pytest.approx([.76, 1.46, .8])
    assert proof["preserved"]["group_count"] == 1
    assert case["receipt"]["optical_warnings"] == []


def test_factor_zero_explicitly_disables_native_and_export_film(proof):
    case = proof["cases"]["factor_zero"]
    assert extension(case, "iridescence") is None
    assert proof["preserved"]["native_zero"] == 0
    assert case["receipt"]["optical_warnings"] == []


def test_repeated_live_export_uses_current_shared_material_controls(proof):
    irid = extension(proof["cases"]["live_coated"], "iridescence")
    assert irid["iridescenceThicknessMinimum"] == 140, proof["live_before_export"]
    assert irid["iridescenceThicknessMaximum"] == 460


@pytest.mark.parametrize("name", ["linked_transmission", "rgb_transmission"])
def test_linked_optics_cannot_be_dropped_silently_or_break_export(proof, name):
    warnings = proof["cases"][name]["receipt"]["optical_warnings"]
    assert any("transmission" in s for s in warnings)


def test_unused_shader_and_inactive_output_do_not_warn(proof):
    assert proof["cases"]["unused_nodes"]["receipt"]["optical_warnings"] == []
    assert proof["cases"]["zero_weight_shader_branch"]["receipt"]["optical_warnings"] == []


def test_unsupported_authored_optics_and_unknown_controls_are_explicit(proof):
    warnings = proof["cases"]["unsupported_authored_optics"]["receipt"]["optical_warnings"]
    assert any("coat IOR" in w for w in warnings)
    assert any("subsurface scattering" in w for w in warnings)
    unknown = proof["cases"]["unverified_controls"]["receipt"]["optical_warnings"]
    for effect in ("specular", "volume", "dispersion"):
        assert any(effect in w and "cannot be verified" in w for w in unknown)


def test_shared_control_graph_and_other_authored_optics_survive(proof):
    p = proof["preserved"]
    assert all(p[n] for n in ("shared_copied", "other_not_extended", "ao_link", "volume_link"))
    assert p["thickness"] == pytest.approx(.0015)
    assert p["dispersion"] == pytest.approx(.06)
    assert p["ao_unlinked_default"] == pytest.approx(.27)
    case = proof["cases"]["preserved_other_optics"]
    assert extension(case, "volume")["thicknessFactor"] == pytest.approx(.0015)
    assert extension(case, "dispersion")["dispersion"] == pytest.approx(.06)


def test_refusals_preserve_entire_scene(proof):
    assert len(proof["refusals"]) == 13
    assert all(r["unchanged"] for r in proof["refusals"].values())


def test_every_export_preserves_scene(proof):
    assert all(c["receipt"]["scene_unchanged"] for c in proof["cases"].values())


def test_only_public_helper_is_added_to_sandbox():
    assert safe_mode.validate("import g3d\ng3d.lens_coating('Lens', factor=1, ior=1.38, thickness_min_nm=140, "
                              "thickness_max_nm=460, tint=(.12,.55,.4))") == []
    assert safe_mode.validate("g3d.coating_range(material)")
    assert safe_mode.validate("g3d.lens_coating.__globals__")
