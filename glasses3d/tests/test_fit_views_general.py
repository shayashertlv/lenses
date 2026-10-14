"""Per-photo failures and declared view/matte/lens policy integration; optimizer arithmetic is tested separately."""
import json
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest

from glasses3d.g3d_host import cameras, fit_views as FV, fitmesh, matte, seat_check
from glasses3d.g3d_host.camera import Camera
from glasses3d.tests import glbfab


@pytest.fixture
def inputs(tmp_path):
    model = tmp_path / 'model.glb'
    model.write_bytes(glbfab.build())
    photos = {}
    for name in ('front.png', 'blank.png', 'angle.png'):
        photo = Image.new('RGB', (180, 120), 'white')
        if name != 'blank.png':
            photo.paste((40, 40, 40), (30, 35, 150, 80))
        path = tmp_path / name
        photo.save(path)
        photos[name] = path
    return model, photos


@pytest.fixture
def no_optional(monkeypatch):
    calls = []
    def checked(path):
        calls.append(path)
        return {'lenses': []}
    monkeypatch.setattr(seat_check, 'check_glb', checked)
    return calls


def test_failed_photo_does_not_abort_other_views_or_seed_cross_fits(inputs, no_optional, tmp_path, monkeypatch):
    model, photos = inputs
    calls = []
    def fitted(glb, path, kind, **kwargs):
        calls.append((path.name, kwargs))
        if path.name != 'angle.png':
            raise ValueError('deliberate per-photo failure')
        assert kwargs['cross_winners'] is None
        return {'status': 'ok', 'view': 'angled', 'camera': {}, 'model': kwargs['_model_context']['model']}
    monkeypatch.setattr(FV, 'fit_image', fitted)
    monkeypatch.setattr(FV, '_row', lambda n, r: r['status'])
    result = FV.fit_views(model, photos, dict(zip(photos, ['front', 'side', 'angled'])), tmp_path / 'fit',
                          workers=1, matte_modes={'angle.png': 'glossy_floor'}, log=lambda _: None)
    assert list(result['images']) == list(photos) and len(no_optional) == 1
    assert [r['status'] for r in result['images'].values()] == ['failed', 'failed', 'ok']
    assert not result['cross_winners'] and calls[-1][1]['matte_mode'] == 'glossy_floor'
    for name in ('front.png', 'blank.png'):
        row = result['images'][name]
        assert row['image_sha256'] == FV.sha256_file(photos[name])
        assert row['camera'] is None and row['code'] == 'E_PHOTO_FIT' and 'rigid_fit' not in row
        assert row['shape'] == [120, 180] and row['matte'] is not None
    assert result['images']['blank.png']['matte']['foreground_px'] == 0
    assert result['images']['blank.png']['roi'] is None
    assert json.loads((tmp_path / 'fit/cameras.json').read_bytes())['model'] == result['model']


def test_all_failed_photos_keep_model_and_unreadable_photo_hash(inputs, no_optional, tmp_path):
    model, photos = inputs
    photos['front.png'].write_bytes(b'invalid image')
    photos = {name: photos[name] for name in ('front.png', 'blank.png')}
    result = FV.fit_views(model, photos, {name: 'front' for name in photos}, tmp_path / 'fit', workers=1,
                          log=lambda _: None)
    assert result['model']['sha256'] == FV.sha256_file(model)
    assert all(row['status'] == 'failed' and row['camera'] is None for row in result['images'].values())
    assert result['images']['front.png']['image_sha256'] == FV.sha256_file(photos['front.png'])
    assert result['images']['front.png']['matte'] is None and result['images']['front.png']['shape'] is None
    assert result['images']['blank.png']['matte']['foreground_px'] == 0


@pytest.mark.parametrize('mode', [None, 'default', 'glossy_floor', 'open_frame'])
def test_direct_fitter_uses_requested_matte_and_preserves_raw_optional_metrics(inputs, monkeypatch, mode):
    model, photos = inputs
    monkeypatch.setattr(seat_check, 'check_glb', lambda _: {'lenses': [
        {'node': 'Lens_L', 'frame_type': 'rimless', 'frame_type_source': 'g3d_seat', 'provisional': False}]})
    calls = []
    def optimizer(fg, scene, pivot, candidates, nr, *, cross, ignore_faces):
        calls.append((fg.copy(), ignore_faces.copy()))
        camera = Camera(0., 12., 0., .1, 100., 90., 60.)
        metrics = cameras.native_metrics(scene, camera, fg, cameras.matte_roi(fg), ignore_faces)
        row = {'view': candidates[0][0], 'camera': camera, 'metrics': metrics, 'loss': .5, 'common_loss': .5,
               'sign': {}, 'refine': {'origin': 'fixture'}, 'fit': SimpleNamespace(evals=1)}
        return row, [row]
    monkeypatch.setattr(cameras, 'fit_best', optimizer)
    result = FV.fit_image(model, photos['front.png'], 'front', matte_mode=mode)
    resolved = mode or 'open_frame'
    np.testing.assert_array_equal(calls[0][0], matte.matte(matte.load_photo(photos['front.png']), mode=resolved))
    mesh = fitmesh.load_fit_mesh(model)
    np.testing.assert_array_equal(calls[0][1], np.isin(mesh.part, mesh.part_ids(['Lens_L'])))
    assert result['matte_mode'] == resolved and result['lens_loss']['optional_nodes'] == ['Lens_L']
    # This stub returns a fixed camera, not a fitted one: keep its low-overlap metrics and label it honestly.
    assert result['iou_lens_optional'] < FV.MIN_USABLE_IOU
    assert result['status'] == 'unusable' and 'rigid_fit' not in result
    assert result['image_sha256'] == FV.sha256_file(photos['front.png'])
    for key in ('iou', 'contour_mean_px', 'render_only_px', 'iou_lens_optional', 'lens_pixels_optional'):
        assert key in result


@pytest.mark.parametrize('modes', [{'missing.png': 'default'}, {'front.png': 'unknown'}])
def test_invalid_matte_configuration_fails_before_any_fit(inputs, tmp_path, monkeypatch, modes):
    model, photos = inputs
    monkeypatch.setattr(FV, '_fit_context', lambda *a: pytest.fail('invalid request reached model/fitter'))
    with pytest.raises(ValueError, match='unknown'):
        FV.fit_views(model, photos, {n: 'front' for n in photos}, tmp_path / 'fit', matte_modes=modes)
    assert not (tmp_path / 'fit').exists()


@pytest.mark.parametrize('kind', ['rear_threequarter', 'rear-threequarter', 'rear three quarter', 'Rear_Three_Quarter'])
def test_rear_threequarter_aliases_keep_both_declared_candidates(kind):
    assert FV.view_candidates(kind) == ('rear_threequarter_left', 'rear_threequarter_right')


def test_optional_loss_uses_exact_lens_primitives_not_other_material_on_same_node(tmp_path, monkeypatch):
    def patch(doc, _):
        doc['materials'][0]['extras'] = {'partRole': 'frame'}
        index = next(i for i, n in enumerate(doc['nodes']) if n.get('name') == 'Lens_L')
        primitives = doc['meshes'][doc['nodes'][index]['mesh']]['primitives']
        primitives.append(dict(primitives[0], material=0))  # frame material's explicit role wins
    path = tmp_path / 'mixed.glb'
    path.write_bytes(glbfab.build(patch=patch))
    mesh = fitmesh.load_fit_mesh(path)
    monkeypatch.setattr(seat_check, 'check_glb', lambda _: {'lenses': [
        {'node': 'Lens_L', 'frame_type': 'semi_rimless', 'frame_type_source': 'inferred', 'provisional': True},
        {'node': 'Lens_R', 'frame_type': None, 'frame_type_source': 'inferred', 'provisional': False}]})
    policy, ids = FV.lens_loss_policy(path, mesh)
    assert len(mesh.part_ids(['Lens_L'])) == 2 and len(ids) == 1
    assert all(mesh.lens_parts[i] for i in ids)
    assert policy['optional_nodes'] == ['Lens_L'] and policy['optional_faces'] == 12
    assert policy['construction'][0]['provisional'] is True


@pytest.mark.parametrize('frame_type', [None, 'acetate', 'metal', 'browline', 'crystal'])
def test_unknown_and_full_rim_construction_remain_hard(inputs, monkeypatch, frame_type):
    model, _ = inputs
    monkeypatch.setattr(seat_check, 'check_glb', lambda _: {'lenses': [
        {'node': 'Lens_L', 'frame_type': frame_type, 'frame_type_source': 'inferred'}]})
    policy, ids = FV.lens_loss_policy(model, fitmesh.load_fit_mesh(model))
    assert not ids and policy['mode'] == 'full' and policy['optional_faces'] == 0


@pytest.mark.parametrize('frame_type,expected', [('rimless', 'open_frame'), ('semi_rimless', 'open_frame'),
                                               (None, 'default'), ('acetate', 'default'), ('crystal', 'default')])
def test_grouped_failed_rows_record_resolved_mode_and_keep_explicit_override(inputs, tmp_path, monkeypatch,
                                                                           frame_type, expected):
    model, photos = inputs
    monkeypatch.setattr(seat_check, 'check_glb', lambda _: {'lenses': [
        {'node': 'Lens_L', 'frame_type': frame_type, 'frame_type_source': 'g3d_seat'}]})
    attempted = {}
    def fail(glb, path, kind, **kwargs):
        attempted[path.name] = kwargs['matte_mode']
        raise ValueError('failure after mode selection')
    monkeypatch.setattr(FV, 'fit_image', fail)
    result = FV.fit_views(model, photos, {name: 'front' for name in photos}, tmp_path / 'fit', workers=1,
                          matte_modes={'angle.png': 'default', 'blank.png': 'glossy_floor'}, log=lambda _: None)
    assert attempted == {'front.png': expected, 'blank.png': 'glossy_floor', 'angle.png': 'default'}
    for name, row in result['images'].items():
        assert row['status'] == 'failed' and row['matte_mode'] == attempted[name]
        mask = matte.matte(matte.load_photo(photos[name]), mode=attempted[name])
        assert row['matte']['foreground_px'] == int(mask.sum())
