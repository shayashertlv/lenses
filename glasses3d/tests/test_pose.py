"""g3d_host/pose.py (WP6_FACETS §3.3): the temple pose on the host.

* rot_about_hinge is relief_views.rot_about_hinge moved verbatim (source compared with the wp6-v1-final tag when the
  tag exists) and has the builder's sign: + = tip outward on both sides, = g3d/temple/build.py to_world for +X and
  its mirror for -X;
* make_pose / parse_pose round-trip and refuse a side's axis on the other side;
* on a synthetic head (front + two temples + a knuckle, partRole by name map): temple_parts / temple_body /
  front_body by role and geometry, posed() opens only the temple parts, unpose_points undoes it, and chart_at
  through the raster returns the chart (d, y, X) of the hit at opening 0."""
from __future__ import annotations

import inspect
import subprocess
from pathlib import Path

import numpy as np
import pytest

from glasses3d.g3d.temple import build as TB
from glasses3d.g3d_host import pose as PO
from glasses3d.g3d_host import raster
from glasses3d.g3d_host.camera import Camera, NormFrame, project_mm
from glasses3d.g3d_host.fitmesh import FitMesh

REPO = Path(__file__).resolve().parents[2]
AXIS = (64.6, -10.1)
HINGE_Z = -10.6


def test_rot_about_hinge_is_the_archived_function_verbatim():
    try:
        old = subprocess.run(["git", "-C", str(REPO), "show", "wp6-v1-final:glasses3d/g3d_host/relief_views.py"],
                             capture_output=True, text=True, check=True, encoding="utf-8").stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("the wp6-v1-final tag is not available")
    src = inspect.getsource(PO.rot_about_hinge)
    assert src in old


def test_tip_outward_on_both_sides_and_equal_to_the_builder():
    rng = np.random.default_rng(3)
    local = np.column_stack([rng.uniform(0, 130, 50), rng.uniform(-20, 15, 50), rng.uniform(64, 72, 50)])
    for theta in (-4.0, 2.5, 8.0):
        W0 = TB.to_world(local, HINGE_Z, AXIS, 0.0)
        Wt = TB.to_world(local, HINGE_Z, AXIS, theta)
        np.testing.assert_allclose(PO.rot_about_hinge(W0, 1, theta, AXIS), Wt, atol=1e-9)
        M0, Mt = W0 * [-1, 1, 1], Wt * [-1, 1, 1]                        # the -X temple is the exact mirror
        np.testing.assert_allclose(PO.rot_about_hinge(M0, -1, theta, AXIS), Mt, atol=1e-9)
    tip = np.array([[66.0, 0.0, -140.0]])
    assert PO.rot_about_hinge(tip, 1, 5.0, AXIS)[0, 0] > 66.0              # + opens outward ...
    assert PO.rot_about_hinge(tip * [-1, 1, 1], -1, 5.0, AXIS)[0, 0] < -66.0   # ... on both sides
    assert PO.rot_about_hinge(tip, 1, 0.0, AXIS) is tip


def test_pose_round_trip_and_refusals():
    pose = PO.make_pose({"1": 6.5, "-1": 2.0}, AXIS)
    assert pose == {"+1": {"theta_deg": 6.5, "axis_xz_mm": [64.6, -10.1]},
                    "-1": {"theta_deg": 2.0, "axis_xz_mm": [-64.6, -10.1]}}
    assert PO.parse_pose(pose) == {1: (6.5, (64.6, -10.1)), -1: (2.0, (64.6, -10.1))}
    assert PO.parse_pose(None) == {}
    with pytest.raises(ValueError, match="side"):
        PO.parse_pose({"+1": {"theta_deg": 1.0, "axis_xz_mm": [-64.6, -10.1]}})
    with pytest.raises(ValueError, match="'\\+1' or '-1'"):
        PO.parse_pose({"2": {"theta_deg": 1.0, "axis_xz_mm": [64.6, -10.1]}})


def _box(lo, hi):
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    V = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
    F = np.array([[0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5], [0, 4, 5], [0, 5, 1], [2, 3, 7], [2, 7, 6],
                  [0, 2, 6], [0, 6, 4], [1, 5, 7], [1, 7, 3]])
    return V, F


def synthetic_head() -> tuple[FitMesh, dict]:
    parts = [("Front", (-70, -20, -8), (70, 18, 4), "frame"),
             ("TempleR", (64, -12, -140), (70, 12, -10.6), "temple"),
             ("TempleL", (-70, -12, -140), (-64, 12, -10.6), "temple"),
             ("KnuckleR", (63.5, 0, -11.2), (65.7, 1.5, -9.0), "temple"),
             ("LensR", (10, -20, -4), (59, 13, 2), "lens")]
    Vs, Fs, part, off = [], [], [], 0
    for i, (_n, lo, hi, _r) in enumerate(parts):
        V, F = _box(lo, hi)
        Vs.append(V)
        Fs.append(F + off)
        part.append(np.full(len(F), i))
        off += len(V)
    V = np.vstack(Vs)
    mesh = FitMesh(V=V, F=np.vstack(Fs), part=np.concatenate(part), names=[p[0] for p in parts],
                   node_names=[p[0] for p in parts], materials=[0, 0, 0, 1, 2], frame=NormFrame.from_bbox(V))
    return mesh, {p[0]: p[3] for p in parts}


def test_parts_pose_and_unpose_on_a_synthetic_head():
    mesh, roles = synthetic_head()
    assert PO.temple_parts(mesh, 1, roles) == [1, 3] and PO.temple_parts(mesh, -1, roles) == [2]
    assert PO.temple_body(mesh, 1, roles) == 1 and PO.temple_body(mesh, -1, roles) == 2
    assert PO.front_body(mesh, roles) == 0
    sides = PO.side_of_faces(mesh, roles)
    assert set(sides[mesh.part == 1]) == {1} and set(sides[mesh.part == 0]) == {0}
    pose = PO.make_pose({"+1": 8.0, "-1": -3.0}, AXIS)
    mp = PO.posed(mesh, pose, roles)
    moved = np.any(mp.V != mesh.V, axis=1)
    fixed = np.unique(mesh.F[np.isin(mesh.part, [0, 4])])
    assert not moved[fixed].any() and moved[np.unique(mesh.F[mesh.part == 1])].all()
    vs = np.zeros(len(mesh.V), np.int8)
    for s in (1, -1):
        vs[np.unique(mesh.F[np.isin(mesh.part, PO.temple_parts(mesh, s, roles))])] = s
    np.testing.assert_allclose(PO.unpose_points(mp.V, vs, pose), mesh.V, atol=1e-9)
    assert mp.F is mesh.F and mp.frame == mesh.frame


def test_chart_at_reads_the_chart_through_the_posed_raster():
    mesh, roles = synthetic_head()
    pose = PO.make_pose({"+1": 6.0, "-1": 6.0}, AXIS)
    mp = PO.posed(mesh, pose, roles)
    cam = raster.view_camera(mesh.frame, 90.0, 10.0, 4.0, (600, 900))       # from +X, a little above
    want = np.array([[70.0, 4.0, -60.0], [70.0, -8.0, -110.0]])               # outer face of the +X temple
    uv = project_mm(PO.rot_about_hinge(want, 1, 6.0, AXIS), cam, mesh.frame)
    res = PO.chart_at(uv, cam, mp, pose, roles, HINGE_Z)
    for rec, w in zip(res, want):
        assert rec["hit"] and rec["part"] == "TempleR" and rec["side"] == 1
        assert rec["d_mm"] == pytest.approx(HINGE_Z - w[2], abs=0.02)
        assert rec["y_mm"] == pytest.approx(w[1], abs=0.02) and rec["X_mm"] == pytest.approx(70.0, abs=0.02)
    miss = PO.chart_at([[1.0, 1.0]], cam, mp, pose, roles, HINGE_Z)[0]
    assert not miss["hit"] and "d_mm" not in miss
    assert Camera.from_dict(cam.to_dict()) == cam
