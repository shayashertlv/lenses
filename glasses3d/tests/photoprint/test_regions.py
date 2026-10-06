"""Rigid groups, regions, stripes and S' on q001 (WP5_DESIGN §5.1, §5.2): sides by x sign, folds disjoint, the
anchor's projection unchanged by its own parallax, hinges opening outward on both sides."""
import numpy as np
import pytest

from glasses3d.g3d_host import raster
from glasses3d.g3d_host.camera import project_mm
from glasses3d.g3d_host.photoprint import inputs as IN
from glasses3d.g3d_host.photoprint import regions as RG
from glasses3d.g3d_host.photoprint.pose import Deformer, Pose, hinge_matrix, points


@pytest.fixture(scope="module")
def job():
    return IN.load_job(with_astra=False)


@pytest.fixture(scope="module")
def groups(job):
    return RG.rigid_groups(job)


@pytest.fixture(scope="module")
def regions(job, groups):
    return RG.build_regions(job, IN.FT1119_PARTS, groups, n_samples=4000)


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
    for name in IN.FT1119_PARTS:
        pid = job.part_ids([name])[0]
        faces = np.concatenate([r.faces for r in regions if r.part == pid])
        assert sorted(faces.tolist()) == np.nonzero(job.mesh.part == pid)[0].tolist()
    minor = {r.name for r in regions if r.minor}
    assert "Temple_Left[-x]/back" in minor and "Brow_Right[+x]/outer" in minor
    assert "Brow_Right[+x]/front" not in minor and "Temple_Right[+x]/outer" not in minor


def test_stripe_folds_are_disjoint_with_guard_bands():
    c = np.linspace(0, 40, 40001)
    f = RG.stripe_fold(c)
    k = np.floor(c / RG.STRIPE_MM)
    frac = c / RG.STRIPE_MM - k
    assert np.all(f[(frac < 0.12) | (frac > 0.88)] == RG.FOLD_GUARD)
    inner = (frac > 0.13) & (frac < 0.87)
    assert np.all((f[inner] == RG.FOLD_A) == (k[inner] % 2 == 0))
    assert np.all((f[inner] == RG.FOLD_B) == (k[inner] % 2 == 1))
    assert np.array_equal(RG.stripe_fold(-c), f)            # |x|: mirror brows share stripes


def test_samples_are_deterministic_and_area_uniform(job, groups):
    a = RG.build_regions(job, ["Brow_Right"], groups, n_samples=20000, seed=3)
    b = RG.build_regions(job, ["Brow_Right"], groups, n_samples=20000, seed=3)
    for ra, rb in zip(a, b):
        assert np.array_equal(ra.samples.face, rb.samples.face) and np.array_equal(ra.samples.bary, rb.samples.bary)
    r = next(x for x in a if x.facing == "front")
    share = np.bincount(np.isin(r.samples.face, r.faces[: len(r.faces) // 2]).astype(int), minlength=2)[1] / 20000
    area = job.face_area[r.faces[: len(r.faces) // 2]].sum() / job.face_area[r.faces].sum()
    assert abs(share - area) < 0.015


def test_parallax_leaves_the_anchor_projection_unchanged(job, regions, groups):
    r = next(x for x in regions if x.name == "Brow_Right[+x]/front")
    cam = job.views["reference-05.png"].camera
    axis = raster.camera_basis(cam)[2]
    d = Deformer(job, regions, groups, {r.rid: axis})
    p0 = Pose({n: v.camera for n, v in job.views.items()})
    p1 = Pose(p0.cameras, {}, {r.rid: (4.0, 0.0)})
    P0, _ = points(d.vertices(p0, "x"), job.mesh.F, r.samples.face, r.samples.bary)
    P1, _ = points(d.vertices(p1, "x"), job.mesh.F, r.samples.face, r.samples.bary)
    interior = ~np.isin(job.mesh.F[r.samples.face], np.concatenate(
        [x.vidx for x in regions if x.part == r.part and x.rid != r.rid])).any(1)
    assert np.allclose(np.linalg.norm(P1 - P0, axis=1)[interior], 4.0)
    uv0, uv1 = project_mm(P0, cam, job.frame), project_mm(P1, cam, job.frame)
    ppm = job.ppm("reference-05.png")
    shift_mm = np.linalg.norm(uv1 - uv0, axis=1) / ppm
    assert shift_mm.max() < 0.12                            # perspective only (measured 0.099 mm at 4 mm)
    uv01 = project_mm(P1, job.views["reference-01.png"].camera, job.frame)
    assert (np.linalg.norm(uv01 - project_mm(P0, job.views["reference-01.png"].camera, job.frame), axis=1)
            / job.ppm("reference-01.png")).mean() > 1.0       # a 30-deg view sees it


def test_hinge_opens_outward_on_both_sides(job, groups):
    for g in groups:
        if not g.side:
            continue
        tip = np.array([g.pivot_mm[0], g.pivot_mm[1], g.pivot_mm[2] - 130.0])
        moved = (tip - g.pivot_mm) @ hinge_matrix(3.0, g.side).T + g.pivot_mm
        assert np.sign(moved[0] - tip[0]) == g.side
        assert abs(np.linalg.norm(moved - g.pivot_mm) - 130.0) < 1e-9
