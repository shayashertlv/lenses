"""Print projection and its edge admission must use the same frozen pose as the photo camera."""
import numpy as np
import pytest

from glasses3d.g3d_host import pose as PO, raster
from glasses3d.g3d_host.photoprint import edges as ED, inputs as IN, texels as TX
from glasses3d.tests.test_pose import synthetic_head


@pytest.fixture(autouse=True)
def synthetic_hardware(monkeypatch):
    # Material loading alone is stubbed; geometry, cameras, raster occlusion and image sampling are real.
    monkeypatch.setattr(TX.MT, 'hardware_faces',
                        lambda job, parts, decals=None: np.zeros(len(job.F) + 1, np.uint8))


def posed_job(angle=12.0):
    mesh, roles = synthetic_head()
    mesh.part_roles = [roles[n] for n in mesh.node_names]
    pose = PO.make_pose({1: angle, -1: -3.0 if angle else 0.0}, (64.6, -10.1))
    moved = PO.posed(mesh, pose, roles)
    shape = (300, 500)
    cam = raster.view_camera(mesh.frame, 55, 15, 2, shape)
    matte = moved.scene().render(cam, shape, 1)['mask']
    yy, xx = np.indices(shape)
    image = np.stack([70 + xx % 70, 35 + yy % 35, 20 + xx % 20], -1).astype(np.uint8)
    view = IN.View('arbitrary.webp', 'angled', image, matte, cam)
    view.pose = pose
    job = IN.Job(mesh, mesh.frame, np.zeros(3), {view.name: view})
    face = np.arange(len(mesh.F))
    class Samples:
        bary = np.full((len(face), 3), 1 / 3)
        def __len__(self):
            return len(face)
    tex = Samples()
    tex.face = face
    return job, view, tex, moved


def test_photo_samples_and_occlusion_follow_opening():
    job, view, tex, moved = posed_job()
    occ = TX.OcclusionScene(job, [1, 2])
    expected = TX.view_samples(job, tex, view.name, occ, V=moved.V, metal=False)
    actual = TX.view_samples(job, tex, view.name, occ, metal=False)
    np.testing.assert_array_equal(actual.uv, expected.uv)
    np.testing.assert_array_equal(actual.ok, expected.ok)
    np.testing.assert_array_equal(actual.col, expected.col)
    authored = TX.view_samples(job, tex, view.name, occ, V=job.V, metal=False)
    assert np.nanmax(np.abs(expected.uv - authored.uv)) > 10
    assert expected.ok.any()


def test_edge_admission_uses_photo_opening():
    job, view, _, moved = posed_job()
    expected = ED.view_part_edges(job, view.name, [1, 2], V=moved.V)
    assert ED.edge_table(job, [1, 2])[view.name] == expected
    assert expected != ED.view_part_edges(job, view.name, [1, 2], V=job.V)


def test_tagged_temple_decals_follow_the_same_opening_in_occlusion():
    job, view, _, moved = posed_job()
    V = np.array([[71., -3., -120.], [71., 3., -120.], [71., 0., -125.]])
    decals = {'V': V, 'F': np.array([[0, 1, 2]]), 'prim': np.array([0]), 'roles': ['temple']}
    occ = TX.OcclusionScene(job, [1, 2], decals)
    posed = occ.scene(moved.V, pose=view.pose)
    expected = PO.rot_about_hinge(V, 1, 12., (64.6, -10.1))
    np.testing.assert_array_equal(posed.V_norm[-3:], job.frame.to_norm(expected))
    np.testing.assert_array_equal(occ.scene(job.V).V_norm[-3:], job.frame.to_norm(V))
    np.testing.assert_array_equal(decals['V'], V)


@pytest.mark.parametrize('pose', [None, {}])
def test_absent_pose_keeps_every_sample_byte(pose):
    job, view, tex, _ = posed_job(0)
    view.pose = pose
    occ = TX.OcclusionScene(job, [1, 2])
    actual = TX.view_samples(job, tex, view.name, occ, metal=False)
    expected = TX.view_samples(job, tex, view.name, occ, V=job.V, metal=False)
    for key in ('ok', 'col', 'cos', 'ppm', 'w', 'uv', 'seen', 'seen_graze'):
        assert getattr(actual, key).tobytes() == getattr(expected, key).tobytes(), key


def test_frozen_basis_pose_reaches_print_sources(tmp_path, ft1119_job):
    import json
    from glasses3d.g3d_host import compare as C
    source = json.loads(open(ft1119_job.sources['cameras_json'], encoding='utf-8').read())
    for rec in source['images'].values():
        rec.update(theta_deg={'+1': 5.0}, hinge_axis_xz_mm={'+1': [65., -10.]})
    path = tmp_path / 'posed-cameras.json'
    path.write_text(json.dumps(source), encoding='utf-8')
    direct = IN.load_job(path, with_astra=False)
    assert all(v.pose == {'+1': {'theta_deg': 5., 'axis_xz_mm': [65., -10.]}} for v in direct.views.values())
    basis = C.import_cameras_json(tmp_path, path,
            {k: v['path'] for k, v in ft1119_job.sources['images'].items()}, reason='synthetic opening tool test')
    job = IN.job_from_dir(tmp_path, glb=ft1119_job.sources['glb'], basis=basis.id, with_astra=False)
    for rid, rec in basis.refs.items():
        assert job.views[rec['image']].pose == basis.pose(rid)
        assert job.sources['images'][rec['image']]['pose'] == basis.pose(rid)
