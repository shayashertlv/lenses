"""Finite frozen temple deltas must cross the host/Blender comparison boundary unchanged."""
import copy
import json
from pathlib import Path

import numpy as np
import pytest

from glasses3d.g3d_host import blender, pose


# Full-circle endpoints, nonzero-origin continuations and finite numeric extremes are all valid rigid views.
ANGLES = (-1e308, -720., -550., -180., -90., 0., 60., 180., 330., 550., 720., 1e308)
AXES = {"+1": [64., -9.], "-1": [-64., -9.]}


def _pose(theta):
    return {key: {"theta_deg": theta, "axis_xz_mm": axis.copy()} for key, axis in AXES.items()}


@pytest.mark.parametrize("theta", ANGLES)
def test_host_finite_pose_keeps_raw_delta_and_rigid_source(theta):
    raw = _pose(theta)
    before = copy.deepcopy(raw)
    parsed = pose.parse_pose(raw)
    for side in (1, -1):
        assert parsed[side][0] == theta
        points = np.array([[side*66., 0., -135.], [side*70., 7., -40.], [side*64., -2., -9.]])
        source = points.copy()
        transformed = pose.rot_about_hinge(points, side, *parsed[side])
        assert np.isfinite(transformed).all()
        np.testing.assert_array_equal(points, source)
        np.testing.assert_allclose(np.linalg.norm(transformed[1:]-transformed[0], axis=1),
                                   np.linalg.norm(source[1:]-source[0], axis=1), atol=1e-12, rtol=0)
    assert raw == before


@pytest.mark.skipif(blender.blender_exe() is None, reason="Blender is not installed")
def test_native_temporary_pose_copies_match_host_with_sources_and_markings_unchanged(tmp_path):
    parts = {}
    for side in (1, -1):
        for label, lo, hi in [("temple", (64., -8., -135.), (70., 8., -9.)),
                              ("marking", (70.1, -3., -54.), (70.15, 3., -34.))]:
            vertices = [[side*x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
            parts[f"{label}{side:+d}"] = {"role": "temple", "side": side, "vertices_mm": vertices}
    parts["front"] = {"role": "frame", "side": 0,
                      "vertices_mm": [[x, y, z] for x in (-70., 70.) for y in (-12., 12.) for z in (-8., 0.)]}
    cases = []
    for theta in ANGLES:
        raw, expected = _pose(theta), {}
        for name, part in parts.items():
            vertices = np.array(part["vertices_mm"], dtype=np.float32).astype(float)
            transformed = pose.rot_about_hinge(vertices, part["side"], theta, [64., -9.]) if part["side"] else vertices
            expected[name] = transformed.tolist()
        cases.append({"pose": raw, "expected_mm": expected})
    fixture = tmp_path / "fixture.json"
    receipt = tmp_path / "receipt.json"
    fixture.write_text(json.dumps({"parts": parts, "cases": cases}), encoding="utf-8")
    original = fixture.read_bytes()
    script = Path(__file__).parent / "blender/photo_pose_contract.py"
    proc = blender.run(None, script, [fixture, receipt], timeout_s=180)
    assert receipt.is_file(), proc.stdout[-6000:] + proc.stderr[-6000:]
    result = json.loads(receipt.read_text())
    assert proc.returncode == 0 and result["status"] == "ok", result
    assert result["cases"] == len(ANGLES)
    assert result["max_component_error_mm"] < 2e-5
    assert result["retained_refusals"] == 9
    assert result["unchanged"] and result["raw_poses_unchanged"] and result["temporary_scene_removed"]
    assert fixture.read_bytes() == original
