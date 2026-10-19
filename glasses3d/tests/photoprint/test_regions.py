"""Rigid groups and regions on q001 (WP5_DESIGN §5.1): sides by x sign. The S' tests
(parallax, hinges) were deleted on 2026-10-13 with step 0's Pose / Deformer; see git tag review-20261013-pre-cleanup."""

from glasses3d.tests.fixtures.legacy_ft1119 import PRINT_PARTS as FT1119_PARTS
import numpy as np
import pytest

from glasses3d.tests.fixtures import legacy_ft1119
from glasses3d.g3d_host.photoprint import regions as RG


@pytest.fixture(scope="module")
def job():
    return legacy_ft1119.load_job(with_astra=False)


@pytest.fixture(scope="module")
def groups(job):
    return RG.rigid_groups(job)


@pytest.fixture(scope="module")
def regions(job, groups):
    return RG.build_regions(job, FT1119_PARTS, groups)


def test_sides_come_from_the_x_sign_not_the_names(job, regions):
    """q001 names temples by the VIEWER's side: Temple_Left is at -x (the wearer's right)."""
    side = {r.part_name: r.side for r in regions}
    assert side["Temple_Left"] == -1 and side["Brow_Left"] == -1
    assert side["Temple_Right"] == 1 and side["Brow_Right"] == 1
    for r in regions:
        assert ("[-x]" in r.name) == (r.side < 0)
        if r.facing in ("outer", "inner"):
            n = job.face_normals[r.faces].mean(0)
            assert np.sign(n[0]) == (r.side if r.facing == "outer" else -r.side), r.name


def test_temple_groups_by_position(job, groups):
    names = job.mesh.names
    g = {x.name: {names[p] for p in x.parts} for x in groups}
    for side, tag in ((-1, "Left"), (1, "Right")):
        tg = g[f"temple{'+x' if side > 0 else '-x'}"]
        assert f"Temple_{tag}" in tg and f"Temple_hinge_{tag}" in tg and f"T_arm_{tag}" in tg
        assert f"Front_hinge_{tag}" not in tg and f"T_return_{tag}" not in tg and f"Brow_{tag}" not in tg
    for x in groups:
        if x.side:
            screw = job.part_ids([f"Hinge_screw_{'Right' if x.side > 0 else 'Left'}"])[0]
            v = job.mesh.V[np.unique(job.mesh.F[job.mesh.part == screw])]
            c = (v.min(0) + v.max(0)) / 2
            assert np.hypot(*(c - x.pivot_mm)[[0, 2]]) < 0.5


def test_regions_cover_parts_and_mark_end_caps_minor(job, regions):
    for name in FT1119_PARTS:
        pid = job.part_ids([name])[0]
        faces = np.concatenate([r.faces for r in regions if r.part == pid])
        assert sorted(faces.tolist()) == np.nonzero(job.mesh.part == pid)[0].tolist()
    minor = {r.name for r in regions if r.minor}
    assert "Temple_Left[-x]/back" in minor and "Brow_Right[+x]/outer" in minor
    assert "Brow_Right[+x]/front" not in minor and "Temple_Right[+x]/outer" not in minor
