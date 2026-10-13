"""Historical command wrappers preserve their sources and pass bound migrated inputs, without baking."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from glasses3d.g3d.printuv import glbmesh as GM
from glasses3d.g3d_host.photoprint import production as P
from glasses3d.tests.fixtures import legacy_ft1119 as FT


BAKE = FT.REPO / 'glasses3d/data/wp5/ft1119_bake'


class StopBeforeExpensiveWork(Exception):
    pass


def file_hashes(directory):
    return {str(path): FT.sha(path) for path in Path(directory).iterdir() if path.is_file()}


def test_bake_wrapper_uses_rebound_registration_before_any_bake(tmp_path, monkeypatch):
    from glasses3d.tests.photoprint import bake_ft1119 as wrapper
    calls = []
    def intercept(job, parts, **kwargs):
        P.validate_registration_inputs(job, kwargs['step0_report'])
        assert kwargs['step0_report'] != wrapper.STEP0
        assert job.sources['glb_sha256'] != FT.MODEL_SHA
        calls.append(kwargs)
        raise StopBeforeExpensiveWork
    monkeypatch.setattr(wrapper.production, 'bake_print', intercept)
    args = SimpleNamespace(out=tmp_path / 'bake', job=None, workers=2, level=None)
    with pytest.raises(StopBeforeExpensiveWork):
        wrapper._main(args, lambda *args, **kwargs: None)
    assert len(calls) == 1


def test_bake_adapter_preserves_original_and_accepts_verified_prior_migration(tmp_path):
    before = file_hashes(BAKE)
    job, bake = FT.bake_inputs(BAKE, tmp_path / 'bake')
    assert bake != BAKE and file_hashes(BAKE) == before
    P.validate_registration_inputs(job, bake / 'report.json')
    copied = file_hashes(bake)
    again, existing = FT.bake_inputs(bake, tmp_path / 'unused')
    assert existing == bake and not (tmp_path / 'unused').exists()
    assert again.sources == job.sources and file_hashes(bake) == copied


def test_bake_adapter_refuses_forged_prior_migration(tmp_path):
    _, bake = FT.bake_inputs(BAKE, tmp_path / 'bake')
    report = json.loads((bake / 'report.json').read_bytes())
    report['inputs']['sources']['cameras_sha256'] = '0' * 64
    FT.write(bake / 'report.json', report)
    with pytest.raises(ValueError, match='bound explicit historical FT migration'):
        FT.bake_inputs(bake, tmp_path / 'unused')


def test_calibration_wrapper_migrates_bound_bake_and_template_without_capturing(tmp_path, monkeypatch):
    from glasses3d.tests.photoprint import ar_calibrate as wrapper
    template = FT.REPO / 'glasses3d/data/wp5/tooltest/q001_painted_TOOLTEST.glb'
    before = file_hashes(BAKE) | {str(template): FT.sha(template)}
    calls = []
    def intercept(job, groups, *, bake, glb, out_dir, **kwargs):
        P.validate_registration_inputs(job, bake / 'report.json')
        assert bake != BAKE and glb != template and out_dir == tmp_path / 'calibration'
        old, old_binary = GM.chunks(template.read_bytes())
        new, new_binary = GM.chunks(glb.read_bytes())
        assert old_binary == new_binary
        for source, target in zip(old['nodes'], new['nodes']):
            expected = dict(source)
            if source.get('name') in FT.NAME_MAP:
                expected['name'] = FT.NAME_MAP[source['name']]
            assert expected == target
        calls.append((bake, glb))
        return {'intercepted': True}
    monkeypatch.setattr(P, 'calibrate_level', intercept)
    result = wrapper.calibrate(BAKE, template, tmp_path / 'calibration')
    assert result == {'intercepted': True} and len(calls) == 1
    assert all(FT.sha(path) == value for path, value in before.items())


def test_tooltest_default_bake_is_copied_before_blender(tmp_path, monkeypatch):
    from glasses3d.tests.photoprint import print_tooltest as wrapper
    before = file_hashes(BAKE)
    calls = []
    def intercept(copy, out, bake, label):
        assert bake == (tmp_path / 'tooltest' / 'legacy-bake').resolve()
        manifest = json.loads((bake / 'print_manifest.json').read_bytes())
        assert manifest['texture_parts'] == list(wrapper.PARTS) and manifest['no_texture_parts'] == []
        assert manifest['glb_sha256'] != FT.MODEL_SHA
        calls.append(bake)
        raise StopBeforeExpensiveWork
    monkeypatch.setattr(wrapper, 'run_blender', intercept)
    with pytest.raises(StopBeforeExpensiveWork):
        wrapper.main(['--out', str(tmp_path / 'tooltest'), '--no-record'])
    assert len(calls) == 1 and file_hashes(BAKE) == before
