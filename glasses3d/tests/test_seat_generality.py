"""Review task 7: common cap constructions and explicit inspection semantics."""
from types import SimpleNamespace

import numpy as np
import pytest

from glasses3d.g3d.seat import check, geom, stations
from glasses3d.g3d_host.seat_check import _translucent


def fan_lens(n=128, centre=True):
    th = 2 * np.pi * np.arange(n) / n
    ring = np.c_[25 * np.cos(th), 18 * np.sin(th)]
    v = np.vstack([np.c_[ring, np.full(n, .8)], np.c_[ring, np.full(n, -.8)],
                   [[0, 0, .8], [0, 0, -.8]]])
    t = []
    for i in range(n):
        j = (i + 1) % n
        t += [(i, n + i, n + j), (i, n + j, j)]
        if centre:
            t += [(2 * n, i, j), (2 * n + 1, n + j, n + i)]
    if not centre:
        v = v[:-2]
        for i in range(1, n - 1):
            t += [(0, i, i + 1), (n, n + i + 1, n + i)]
    return v, np.array(t)


@pytest.mark.parametrize("n", [64, 128, 200])
@pytest.mark.parametrize("centre", [False, True])
def test_flat_fan_caps_are_recognised_without_lowering_cap_gates(n, centre):
    v, t = fan_lens(n, centre)
    result = stations.recognise(v, t, "fan")
    assert not result.refused, result.refused
    assert len(result.bodies) == 1
    frames = stations.station_frames(result.bodies[0])
    assert np.allclose(frames.n, [0, 0, 1])
    assert frames.ok.all()
    split = geom.split_caps(v, t)
    normals = geom.face_geometry(v, t)[0]
    assert np.all(np.abs(normals[split.cls != 2, 2]) > .99)
    assert np.all(np.abs(normals[split.cls == 2, 2]) < .01)
    assert split.area_share >= geom.CAP_AREA_SHARE


def test_explicit_inspection_overrides_recorded_construction_without_mutation():
    node = {"extras": {"g3d_seat": {"frame_type": "metal", "pass_mm": .44, "target_mm": .45}}}
    warnings = []
    ft, source, seat = check.frame_type_for(SimpleNamespace(name="lens"), node, None,
                                           "semi_rimless", "plain", warnings)
    assert ft.name == "semi_rimless" and ft.lower_edge == "plain"
    assert source == "argument"
    assert seat["frame_type"] == "metal"
    assert any("metal" in w and "semi_rimless" in w for w in warnings)


def test_same_construction_inspection_keeps_stricter_recorded_capture():
    node = {"extras": {"g3d_seat": {"frame_type": "metal", "pass_mm": .6, "target_mm": .7}}}
    ft, source, _ = check.frame_type_for(SimpleNamespace(name="lens"), node, None, "metal", None, [])
    assert source == "argument" and ft.pass_mm == .6 and ft.target_mm == .7


def test_same_construction_inspection_preserves_recorded_lower_edge():
    node = {"extras": {"g3d_seat": {"frame_type": "semi_rimless", "lower_edge": "nylor"}}}
    ft, source, _ = check.frame_type_for(SimpleNamespace(name="lens"), node, None, "semi_rimless", None, [])
    assert source == "argument" and ft.lower_edge == "nylor"


@pytest.mark.parametrize("material, expected", [
    ({"pbrMetallicRoughness": {"baseColorFactor": [1, 1, 1, .1]}}, False),
    ({"alphaMode": "BLEND", "pbrMetallicRoughness": {"baseColorFactor": [1, 1, 1, .5]}}, True),
    ({"alphaMode": "MASK", "pbrMetallicRoughness": {"baseColorFactor": [1, 1, 1, .5]}}, False),
    ({"alphaMode": "BLEND", "pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}}, True),
    ({"extensions": {"KHR_materials_transmission": {"transmissionFactor": .9}}}, True),
    ({"extensions": {"KHR_materials_transmission": {"transmissionTexture": {"index": 0}}}}, False),
])
def test_translucency_uses_rendered_gltf_semantics(material, expected):
    assert _translucent(material) is expected


def test_browline_refusal_names_supported_range_declaration():
    from glasses3d.tests.fixtures import seat_fixtures as fixtures
    from glasses3d.tests.test_seat_tools import seat
    result = seat(fixtures.browline_scene())
    assert result.code == "E_NO_RANGES"
    assert "hide_eyewire" in result.message and "g3d_hidden_x_ranges_mm" in result.message
    assert "hidden_x_range_mm" not in result.message.replace("g3d_hidden_x_ranges_mm", "")


def test_tool_description_discloses_profile_scope_and_new_wall_uvs():
    from glasses3d.agent.tools.seating import seat_tools
    tools = seat_tools(None, lambda name: lambda fn: fn, None, None)
    description = tools[0].__doc__
    for phrase in ("Acetate/metal", "browline/semi_rimless", "newly built profile-wall UVs",
                   "one lens body", "quad wall", "no custom normals", "unlisted parts are obstacles"):
        assert phrase in description
    assert "UVs are kept." not in description
