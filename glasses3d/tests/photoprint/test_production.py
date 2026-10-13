"""G1: explicit jobs, runtime independence, frozen-input refusals and parity with independent instruments."""
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from glasses3d.g3d_host import compare as C
from glasses3d.g3d_host.photoprint import inputs as IN, metrics, production as P
from glasses3d.g3d_host.photoprint.ar_calibrate import Calibrator
from glasses3d.g3d_host.photoprint.ar_levels import Measurements
from glasses3d.tests.instruments import print_metrics as PM
from glasses3d.tests.photoprint import ar_levels as AL, ar_calibrate as AC


@pytest.fixture
def basis_job(tmp_path, ft1119_job):
    basis = C.import_cameras_json(tmp_path, ft1119_job.sources['cameras_json'],
                                  {k: v['path'] for k, v in ft1119_job.sources['images'].items()},
                                  reason='G1 offline fixture')
    return tmp_path, basis


def test_explicit_basis_matches_original_inputs(basis_job, ft1119_job, ft1119_fixture):
    root, basis = basis_job
    job = IN.job_from_dir(root, glb=ft1119_job.sources['glb'], basis=basis.id)
    assert job.sources['camera_fit_glb_sha256'] == job.sources['glb_sha256']
    assert list(job.views) == list(ft1119_job.views)
    np.testing.assert_array_equal(job.V, ft1119_job.V)
    np.testing.assert_array_equal(job.F, ft1119_job.F)
    for name, view in job.views.items():
        ref = ft1119_job.views[name]
        assert view.camera == ref.camera
        np.testing.assert_array_equal(view.image, ref.image)
        np.testing.assert_array_equal(view.matte, ref.matte)
    P.validate_registration_inputs(job, ft1119_fixture['step0'])


@pytest.mark.parametrize('damage', ['cameras', 'camera', 'matte', 'photo', 'missing', 'kind', 'colourway'])
def test_changed_basis_refuses_before_baking(basis_job, ft1119_job, damage):
    root, basis = basis_job
    bp = basis.dir / 'basis.json'
    meta = json.loads(bp.read_bytes())
    ref = next(iter(meta['refs'].values()))
    if damage == 'cameras':
        with (basis.dir / 'cameras.json').open('a') as f:
            f.write(' ')
    elif damage == 'camera':
        ref['camera']['yaw'] += 1
    elif damage == 'kind':
        ref['view_kind'] = 'rear'
    elif damage == 'colourway':
        ref['colourway'] = 'other finish'
    elif damage == 'photo':
        ref['photo_sha256'] = '0' * 64
    elif damage == 'missing':
        meta['refs'].pop(next(iter(meta['refs'])))
    else:
        with (basis.dir / ref['matte']).open('ab') as f:
            f.write(b'changed')
    bp.write_text(json.dumps(meta))
    with pytest.raises(IN.InputError):
        IN.job_from_dir(root, glb=ft1119_job.sources['glb'], basis=basis.id)


def test_no_implicit_job_or_newest_basis(tmp_path):
    with pytest.raises(TypeError):
        IN.load_job()
    with pytest.raises(TypeError):
        IN.job_from_dir(tmp_path, glb='model.glb')
    with pytest.raises(IN.InputError, match='no camera basis'):
        IN.job_from_dir(tmp_path, glb='model.glb', basis='basis-001')


def _roles(root, basis, **overrides):
    rows = [{'original_name': ref['image'], 'basis_ref': rid, 'sha256': ref['photo_sha256'],
             **overrides.get(rid, {})} for rid, ref in basis.refs.items()]
    (root / 'job.json').write_text(json.dumps({'images': rows}))
    return rows


def _frozen_colourways(basis, changes):
    """Fixture metadata is recorded in both the source cameras and its frozen copy, with the source hash rebound."""
    cameras_path = basis.dir / C.CAMERAS_JSON
    cameras = json.loads(cameras_path.read_bytes())
    meta = json.loads((basis.dir / 'basis.json').read_bytes())
    for rid, colourway in changes.items():
        meta['refs'][rid]['colourway'] = colourway
        cameras['images'][meta['refs'][rid]['image']]['colourway'] = colourway
    cameras_path.write_text(json.dumps(cameras), encoding='utf-8')
    meta['cameras_json_sha256'] = C.sha256_file(cameras_path)
    (basis.dir / 'basis.json').write_text(json.dumps(meta), encoding='utf-8')
    return C.Basis(basis.dir, basis.job_root)


@pytest.mark.parametrize('metadata', ['basis_only', 'job_without_roles', 'explicit_target'])
def test_frozen_alternate_colourway_is_excluded_unless_job_explicitly_selects_it(basis_job, ft1119_job, metadata):
    root, basis = basis_job
    rid = next(iter(basis.refs))
    basis = _frozen_colourways(basis, {rid: 'blue'})
    if metadata != 'basis_only':
        _roles(root, basis, **({rid: {'role': 'target'}} if metadata == 'explicit_target' else {}))
    job = IN.job_from_dir(root, glb=ft1119_job.sources['glb'], basis=basis.id)
    ref = basis.refs[rid]
    if metadata == 'explicit_target':
        assert ref['image'] in job.views and not job.sources['excluded_images']
        assert job.sources['images'][ref['image']]['colourway'] == 'blue'
    else:
        assert ref['image'] not in job.views and len(job.views) == len(basis.refs) - 1
        excluded = job.sources['excluded_images'][ref['image']]
        assert excluded['sha256'] == ref['photo_sha256'] and excluded['matte_sha256'] == ref['matte_sha256']
        assert excluded['camera'] == ref['camera'] and excluded['basis_ref'] == rid and excluded['colourway'] == 'blue'


def test_basis_only_alternate_filter_retains_two_target_gate(basis_job, ft1119_job):
    root, basis = basis_job
    basis = _frozen_colourways(basis, {rid: 'blue' for rid in list(basis.refs)[1:]})
    with pytest.raises(IN.InputError, match='fewer than two target-colourway'):
        IN.job_from_dir(root, glb=ft1119_job.sources['glb'], basis=basis.id)


def test_only_target_colourway_photos_paint_but_all_evidence_is_bound(basis_job, ft1119_job):
    root, basis = basis_job
    ids = list(basis.refs)
    _roles(root, basis, **{ids[0]: {'colourway': 'blue'}, ids[1]: {'role': 'reference'},
                           ids[2]: {'role': 'target', 'colourway': 'the selected blue finish'}})
    job = IN.job_from_dir(root, glb=ft1119_job.sources['glb'], basis=basis.id)
    excluded = {basis.refs[rid]['image'] for rid in ids[:2]}
    assert set(job.views) == set(ft1119_job.views) - excluded
    assert set(job.sources['excluded_images']) == excluded
    assert basis.refs[ids[2]]['image'] in job.views
    for rid in ids[:2]:
        ref = basis.refs[rid]
        item = job.sources['excluded_images'][ref['image']]
        assert item['sha256'] == ref['photo_sha256'] and item['matte_sha256'] == ref['matte_sha256']
        assert item['camera'] == ref['camera'] and item['basis_ref'] == rid and item['reason']


@pytest.mark.parametrize('damage', ['unknown_role', 'wrong_identity', 'excluded_matte', 'one_target'])
def test_target_filter_never_hides_invalid_or_insufficient_evidence(basis_job, ft1119_job, damage):
    root, basis = basis_job
    rid = next(iter(basis.refs))
    rows = _roles(root, basis, **{rid: {'role': 'colourway'}})
    if damage == 'unknown_role':
        rows[0]['role'] = 'guess'
    elif damage == 'wrong_identity':
        rows[0]['sha256'] = '0' * 64
    elif damage == 'excluded_matte':
        with (basis.dir / basis.refs[rid]['matte']).open('ab') as handle:
            handle.write(b'changed')
    else:
        for row in rows[1:]:
            row['role'] = 'reference'
        rows[0]['role'] = 'target'
    (root / 'job.json').write_text(json.dumps({'images': rows}))
    with pytest.raises(IN.InputError):
        IN.job_from_dir(root, glb=ft1119_job.sources['glb'], basis=basis.id)


def test_manifest_write_failure_is_a_failed_bake(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from glasses3d.g3d_host.photoprint import project
    def refuse(_):
        raise ValueError('injected invalid attachment eligibility')
    monkeypatch.setattr(project.MF, 'write_manifest', refuse)
    res = {'atlas': np.zeros((1, 1, 3), np.uint8),
           'tex': SimpleNamespace(image=lambda *args: np.zeros((1, 1), np.uint8)),
           'classes': [0], 'source': [0], 'tier': [0], 'report': {}, 'registration': {},
           'layout': SimpleNamespace(to_json=lambda: {})}
    with pytest.raises(ValueError, match='attachment eligibility'):
        project.write_outputs(res, tmp_path)


def test_stale_registration_cannot_certify_another_job(ft1119_job, tmp_path):
    src = IN.REPO / 'glasses3d/data/wp5/ft1119_strip_null/report.json'
    rec = json.loads(src.read_bytes())
    rec['inputs']['glb_sha256'] = '0' * 64
    p = tmp_path / 'registration.json'
    p.write_text(json.dumps(rec))
    with pytest.raises(ValueError, match='glb_sha256 differs'):
        P.validate_registration_inputs(ft1119_job, p)


def test_runtime_imports_do_not_load_test_modules():
    code = '''
import importlib.abc, sys
class NoTests(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith('glasses3d.tests'):
            raise AssertionError(fullname)
sys.meta_path.insert(0, NoTests())
from glasses3d.g3d_host.photoprint import production, ar_levels, ar_calibrate
'''
    r = subprocess.run([sys.executable, '-B', '-c', code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_runtime_metrics_match_independent_instrument():
    # the runtime ``evaluate_case`` (check_print's) was deleted on 2026-10-13 with the step-0 experiment
    rng = np.random.default_rng(983)
    photo = rng.uniform(20, 220, (70, 90, 3))
    actual = np.clip(photo * [1.04, 0.93, 0.85] + 3, 0, 255)
    assert metrics.level_compare(actual.reshape(-1, 3), photo.reshape(-1, 3)) == PM.level_compare(
        actual.reshape(-1, 3), photo.reshape(-1, 3))


def test_measurement_parity(ft1119_job):
    # the runtime ``Measurements.part_sides`` (called only by this parity check) was deleted on 2026-10-13
    m = Measurements(ft1119_job, AL.GROUPS, temple_z=AL.AR_TEMPLE_Z, reference_point_mm=AL.BRIDGE_MM)
    assert m.pair_table(ft1119_job) == AL.pair_table(ft1119_job)


def test_missing_measurement_part_refuses(ft1119_job):
    with pytest.raises(ValueError, match='absent from the job'):
        Measurements(ft1119_job, {'brow': ('Brow_Left', 'missing-part')})


def test_calibration_refuses_bake_from_another_job(ft1119_job, tmp_path):
    bake = tmp_path / 'bake'
    bake.mkdir()
    (bake / 'report.json').write_text(json.dumps({'inputs': {'glb_sha256': '0' * 64}}))
    with pytest.raises(ValueError, match='glb_sha256 differs'):
        P.calibrate_level(ft1119_job, AL.GROUPS, bake=bake, glb='unused.glb',
                          out_dir=tmp_path / 'out', chroma_family={})


def test_real_capture_measurements_match_legacy_instrument(ft1119_job):
    m = Measurements(ft1119_job, AL.GROUPS, temple_z=AL.AR_TEMPLE_Z, reference_point_mm=AL.BRIDGE_MM)
    base = IN.REPO / 'glasses3d/data/wp5/resume_20261007/ar'
    cap, probe = base / 'paint', base / 'black'
    if not (cap / 'report.json').is_file():
        import os
        if os.environ.get('G3D_REQUIRE_DATA') == '1':
            pytest.fail('G1 requires the accepted candidate AR capture')
        pytest.skip('local-only AR capture')
    # Exercise calibration, trade-off and held-out rows, the runtime metrics and part-specific pixel density.
    pairs = m.auto_pairs(ft1119_job, 'photo_angle')
    near = m.auto_pairs(ft1119_job, 'near')
    loose = m.auto_pairs(ft1119_job, 'loose')
    args = dict(pairs=pairs, tradeoff=near, heldout=loose, job=ft1119_job, probes={str(cap): str(probe)})
    assert m.measure([cap], **args) == AL.measure([cap], **args)


def test_arbitrary_photo_names_and_absent_roles(ft1119_job, tmp_path):
    views = {f'product_vs_angle_{i}.jpg': replace(v, name=f'product_vs_angle_{i}.jpg')
             for i, v in enumerate(ft1119_job.views.values())}
    job = replace(ft1119_job, views=views)
    m = Measurements(job, {'frame': IN.FT1119_PARTS})
    assert m.view_name('product_vs_angle_0') == 'product_vs_angle_0.jpg'
    assert all(r['photo'].startswith('product_vs_angle_') for r in m.pair_table(job))
    r = m.measure([tmp_path], pairs=(), tradeoff=(), heldout=())
    assert all(x is None for row in r['captures'][str(tmp_path)]['pass'].values() for x in row.values())


def test_calibrator_search_parity(ft1119_job):
    from glasses3d.tests.photoprint.test_step6 import _synthetic_capture
    new = Calibrator(ft1119_job, AL.GROUPS, chroma_family=AC.CHROMA_FAMILY)
    residuals = {'calibration': [-0.97, -1.22, 1.62, -1.62], 'trade-off': [1.5, 4.9],
                 'held-out': [5.0], 'appearance': [-0.97, -1.22, 1.62, -1.62, 1.6, -0.27]}
    def run(api):
        def capture(s, p, tag):
            return _synthetic_capture(tag, api.with_chroma(.93, 1.08, s, p), residuals)
        base = capture(1, 1, 'base')
        return api.chroma_stage(base, capture, log=lambda msg: None)
    assert run(new) == run(AC)
    assert new.search_box() == AC.search_box()


def test_luminance_search_parity(ft1119_job):
    from glasses3d.tests.photoprint.test_correction_round import _synthetic_residuals
    new = Calibrator(ft1119_job, AL.GROUPS, chroma_family={})
    def run(api):
        captures = []
        def capture(gain, gamma, tag):
            e = {'tag': tag, 'level': {'gain': gain, 'gamma': gamma},
                 'residuals': _synthetic_residuals(gain, gamma)}
            captures.append(e)
            return e
        for gamma in AC.GRID[1]:
            for gain in AC.GRID[0]:
                capture(gain, gamma, f'g{gain}_{gamma}')
        searches = {role: api.search(captures, capture, role, 8, role, log=lambda msg: None)
                    for role in ('appearance', 'trade-off')}
        return searches, captures
    assert run(new) == run(AC)


def test_missing_optional_roles_produce_no_candidate_or_pass(ft1119_job):
    from glasses3d.tests.photoprint.test_step6 import _synthetic_capture
    api = Calibrator(ft1119_job, AL.GROUPS, chroma_family={})
    residuals = {'calibration': [-.9, -1.2, 1.6, -1.6], 'appearance': [-.9, -1.2, 1.6, -1.6],
                 'trade-off': [], 'held-out': []}
    def capture(gain, gamma, tag, sat=1., power=1.):
        e = _synthetic_capture(tag, api.with_chroma(gain, gamma, sat, power), residuals)
        for key in ('pairs', 'chroma', 'chroma_units', 'chroma_stats'):
            for role in ('trade-off', 'held-out'):
                e[key][role] = []
        return e
    base = capture(.93, 1.08, 'base')
    result = api.finish([base], capture, base['pairs'], log=lambda m: None)
    assert result['near'] is None and result['all_pairs'] is None
    assert set(result['candidates']) == {'photo_angles'}
    assert result['candidates']['photo_angles']['worst']['trade-off'] is None


def test_dotted_part_names_are_not_truncated():
    palette = [{'name': 'acetate.outer.001', 'rgb': [10, 20, 30]},
               {'name': 'acetate.inner', 'rgb': [50, 60, 70]}]
    image = np.array([[[10, 20, 30], [50, 60, 70]]], np.uint8)
    assert metrics.ar_frame_mask(image, palette, ['acetate.outer'], tol=0, erode=0).tolist() == [[True, False]]
    assert not metrics.ar_frame_mask(image, palette, ['acetate'], tol=0, erode=0).any()


def test_measurement_context_cannot_be_reused_for_another_job(ft1119_job, tmp_path):
    m = Measurements(ft1119_job, AL.GROUPS)
    with pytest.raises(ValueError, match='one job'):
        m.measure([tmp_path], job=replace(ft1119_job), pairs=(), tradeoff=(), heldout=())


def test_probe_refuses_shared_nonprint_image(ft1119_job, tmp_path):
    api = Calibrator(ft1119_job, AL.GROUPS, chroma_family={})
    import struct
    doc = {'nodes': [{'name': 'Lens', 'mesh': 0}],
           'meshes': [{'primitives': [{'material': 0}]}],
           'materials': [{'pbrMetallicRoughness': {'baseColorTexture': {'index': 0}}}],
           'textures': [{'source': 0}]}
    js = json.dumps(doc).encode()
    js += b' ' * (-len(js) % 4)
    data = struct.pack('<4sII', b'glTF', 2, 20 + len(js)) + struct.pack('<II', len(js), 0x4e4f534a) + js
    src, dst = tmp_path / 'source.glb', tmp_path / 'probe.glb'
    src.write_bytes(data)
    with pytest.raises(ValueError, match='non-print part'):
        api.swap_images(src, dst, {0: b'new image'})
    assert src.read_bytes() == data and not dst.exists()
    with pytest.raises(ValueError, match='copy'):
        api.swap_images(src, src, {0: b'new image'})


def test_pigment_policy_cannot_override_gain_or_gamma(ft1119_job):
    with pytest.raises(ValueError, match='chroma_family'):
        Calibrator(ft1119_job, AL.GROUPS, chroma_family={'gain': 9.0})


def test_full_calibration_entry_with_synthetic_renderer(ft1119_job, ft1119_fixture, tmp_path, monkeypatch):
    """Real job/bake/GLB attachment validation and orchestration; only captures are a synthetic renderer."""
    from glasses3d.tests.photoprint.test_correction_round import _synthetic_residuals
    from glasses3d.tests.photoprint.test_step6 import _synthetic_capture
    from glasses3d.tests.fixtures.legacy_ft1119 import copy_bake
    bake = copy_bake(IN.REPO / 'glasses3d/data/wp5/ft1119_bake_radiance_s6b', tmp_path / 'bake', ft1119_fixture)
    src = IN.REPO / 'glasses3d/data/wp5/resume_20261007/tooltest/q001_painted_TOOLTEST.glb'
    api = Calibrator(ft1119_job, AL.GROUPS, chroma_family=AC.CHROMA_FAMILY)
    template = tmp_path / 'unlevelled_TOOLTEST.glb'
    api.swap_image(src, template, (bake / 'print.png').read_bytes())
    before = template.read_bytes()
    def evaluate(self, bake, glb, work, level, tag, job, probe, photos, log):
        residuals = _synthetic_residuals(level['gain'], level['gamma'])
        e = _synthetic_capture(tag, level, residuals)
        e['brow_residuals'] = e['temple_residuals'] = residuals
        return e
    monkeypatch.setattr(Calibrator, 'evaluate', evaluate)
    monkeypatch.setattr(Calibrator, 'capture', lambda self, glb, out, log, portrait=False: out)
    out = tmp_path / 'calibration'
    result = P.calibrate_level(ft1119_job, AL.GROUPS, bake=bake, glb=template, out_dir=out,
                               chroma_family=AC.CHROMA_FAMILY, max_iter=1, portraits=False, log=lambda m: None)
    saved = json.loads((out / 'calibration.json').read_bytes())
    assert saved['inputs']['attachment']['ok']
    assert saved['level'] == result['candidates']['photo_angles']['level']
    assert len(saved['iterations']) >= 12 and saved['leave_one_pair_out']
    assert 'production_selection' not in saved and template.read_bytes() == before
    assert P.read_level(out / 'calibration.json')[0] == saved['level']


def test_no_usable_camera_direction_refuses_before_capture(ft1119_job, tmp_path, monkeypatch):
    job = replace(ft1119_job, views={n: replace(v, camera=replace(v.camera, pitch=90.0))
                                    for n, v in ft1119_job.views.items()})
    api = Calibrator(job, AL.GROUPS, chroma_family={})
    def forbidden(*args, **kwargs):
        pytest.fail('must refuse before writing a probe or capturing')
    monkeypatch.setattr(api, 'black_probe', forbidden)
    with pytest.raises(ValueError, match='calibration direction'):
        api.calibrate(tmp_path, tmp_path / 'missing.glb', tmp_path / 'out')


def test_calibration_never_overwrites_or_levels_twice(ft1119_job, tmp_path):
    out, bake = tmp_path / 'cal', tmp_path / 'bake'
    out.mkdir()
    (out / 'calibration.json').write_text('{"production_selection": {}}')
    args = dict(bake=bake, glb='unused.glb', out_dir=out, chroma_family={})
    with pytest.raises(ValueError, match='preserve'):
        P.calibrate_level(ft1119_job, AL.GROUPS, **args)
    bake.mkdir()
    (bake / 'report.json').write_text('{"level": {"applied": true}}')
    args['out_dir'] = tmp_path / 'new'
    with pytest.raises(ValueError, match='unlevelled'):
        P.calibrate_level(ft1119_job, AL.GROUPS, **args)


@pytest.mark.parametrize('value', [float('nan'), float('inf'), 0, -1])
def test_nonfinite_calibration_refuses(tmp_path, value):
    p = tmp_path / 'calibration.json'
    p.write_text(json.dumps({'level': {'gain': value, 'gamma': 1}}))
    with pytest.raises(ValueError, match='finite and positive'):
        P.read_level(p)
