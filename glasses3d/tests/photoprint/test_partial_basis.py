"""Print donors use accepted cameras while every attempted photo remains bound evidence."""
from copy import deepcopy

import numpy as np
from PIL import Image
import pytest

from glasses3d.g3d_host import compare as C, fitmesh, matte
from glasses3d.g3d_host.photoprint import inputs as IN
from glasses3d.tests import glbfab


CAMERA = {'yaw': 0., 'pitch': 10., 'roll': 0., 'perspective': .1, 'scale': 100.,
          'center_x': 40., 'center_y': 30.}


def partial(tmp_path, good=2, mode='glossy_floor'):
    root, source = tmp_path / 'job', tmp_path / 'inputs'
    source.mkdir()
    model = source / 'model.glb'
    model.write_bytes(glbfab.build())
    mesh = fitmesh.load_fit_mesh(model)
    images, photos = {}, {}
    for index in range(good + 2):
        name = f'view-{index}.png'
        photo = source / name
        image = Image.new('RGB', (80, 60), 'white')
        image.paste((40 + index, 40, 40), (20, 15, 60, 45))
        if mode == 'open_frame':
            image.paste('white', (29, 23, 51, 37))
        image.save(photo)
        row = {'image': str(photo), 'shape': [60, 80], 'camera': deepcopy(CAMERA), 'view_kind': 'front',
               'view': 'front', 'status': 'ok', 'refusal': None, 'matte_mode': mode}
        if index == good:
            photo.write_bytes(b'original undecodable photo')
            row.update(status='failed', camera=None, shape=None, matte=None, view=None,
                       code='E_PHOTO_FIT', refusal='decode failed', error={'type': 'ValueError', 'message': 'decode'})
        elif index == good + 1:
            row.update(status='refused', code='E_VIEW_SIGN', refusal='wrong pitch', flags=['label_sign_contradicted'])
        row['image_sha256'] = C.sha256_file(photo)
        images[name], photos[name] = row, photo
    C.write_json(source / C.CAMERAS_JSON, {'model': {'path': str(model), 'sha256': C.sha256_file(model),
        'frame': mesh.frame.to_dict(), 'front_pivot_mm': mesh.frame.to_mm(mesh.front_pivot()[None])[0].tolist(),
        'skip': []}, 'images': images})
    basis = C.import_cameras_json(root, source / C.CAMERAS_JSON, photos, reason='partial print fixture')
    return root, model, basis, photos


def test_print_partial_basis_keeps_two_successes_and_all_failed_diagnostics(tmp_path):
    root, model, basis, photos = partial(tmp_path)
    job = IN.job_from_dir(root, glb=model, basis=basis.id, with_astra=False)
    assert list(job.views) == ['view-0.png', 'view-1.png']
    assert list(job.sources['excluded_images']) == ['view-2.png', 'view-3.png']
    for name, status in [('view-2.png', 'failed'), ('view-3.png', 'refused')]:
        row = job.sources['excluded_images'][name]
        assert row['sha256'] == C.sha256_file(photos[name]) and row['basis_ref'] == C.ref_id(name)
        assert row['fit']['status'] == status and row['fit']['refusal']
    assert job.sources['excluded_images']['view-2.png']['matte_sha256'] is None
    assert job.sources['excluded_images']['view-3.png']['camera'] == CAMERA
    for name, view in job.views.items():
        np.testing.assert_array_equal(view.matte, matte.matte(matte.load_photo(photos[name]), mode='glossy_floor'))


@pytest.mark.parametrize('mode', ['glossy_floor', 'open_frame'])
def test_raw_camera_loader_uses_same_recorded_matte_as_frozen_basis(tmp_path, mode):
    root, model, basis, _ = partial(tmp_path, mode=mode)
    frozen = IN.job_from_dir(root, glb=model, basis=basis.id, with_astra=False)
    direct = IN.load_job(tmp_path / 'inputs/cameras.json', with_astra=False)
    assert list(direct.views) == list(frozen.views)
    for name in direct.views:
        np.testing.assert_array_equal(direct.views[name].matte, frozen.views[name].matte)
        assert basis.refs[C.ref_id(name)]['matte_mode'] == mode
        assert C.load_basis(root, basis.id).refs[C.ref_id(name)]['matte_mode'] == mode
        if mode == 'open_frame':
            assert not direct.views[name].matte[30, 40]
            assert matte.matte(direct.views[name].image)[30, 40]


@pytest.mark.parametrize('damage', ['undecodable_photo', 'refused_matte', 'refused_camera'])
def test_failed_evidence_integrity_never_disappears_behind_filter(tmp_path, damage):
    root, model, basis, photos = partial(tmp_path)
    if damage == 'undecodable_photo':
        photos['view-2.png'].write_bytes(b'changed corrupt image')
    elif damage == 'refused_matte':
        (basis.dir / basis.refs['view-3']['matte']).write_bytes(b'changed matte')
    else:
        meta = deepcopy(basis.meta)
        meta['refs']['view-3']['camera']['yaw'] += 1
        C.write_json(basis.dir / 'basis.json', meta)
    with pytest.raises(IN.InputError):
        IN.job_from_dir(root, glb=model, basis=basis.id, with_astra=False)


@pytest.mark.parametrize('good', [0, 1])
def test_partial_basis_does_not_reduce_two_target_gate(tmp_path, good):
    root, model, basis, _ = partial(tmp_path, good)
    with pytest.raises(IN.InputError, match='fewer than two target-colourway'):
        IN.job_from_dir(root, glb=model, basis=basis.id, with_astra=False)
