"""P3 must grade the actual copy and its original decals before claiming success."""
from types import SimpleNamespace

import pytest

from glasses3d.g3d.temple import fixtures as FX
from glasses3d.tests.proofs import wp6_prada as P
from glasses3d.tests.proofs import wp6_prada_stages as S
from glasses3d.tests.proofs import wp6_p0_instrument as I


@pytest.mark.parametrize('failure', [None, 'normals', 'decals', 'reference_export'])
def test_p3_keeps_independent_export_failures(tmp_path, monkeypatch, failure):
    captured = {}
    monkeypatch.setattr(P, 'scene_copy', lambda out, name: out / name)
    monkeypatch.setattr(P, 'sha', lambda path: 'original-scene')
    monkeypatch.setattr(S, 'validate_spec', lambda path: {'ok': True})

    def blender_steps(out, blend, steps, label):
        results = []
        applied = False
        for step in steps:
            if step['op'] == 'export':
                if not applied:
                    captured['reference'] = step['path']
                results.append({'status': 'error' if not applied and failure == 'reference_export'
                                else 'exported'})
            elif step['op'] == 'faceted_temple':
                applied = True
                results.append({'status': 'ok', 'warnings': [], 'sides': {'+x': {'decals': [{'name': 'foil'}]}}})
            else:
                results.append({})
        return results

    def grade(path, **kwargs):
        captured['graded'] = str(path)
        captured['reference_used'] = str(kwargs.get('reference_glb'))
        captured['decal_names'] = kwargs.get('decal_names')
        return {'pass': failure not in ('normals', 'decals'),
                'temples': {'1': {'checks': {'facet_corner_normals': {'pass': failure != 'normals'}}}},
                'checks': {'decals': {'pass': failure != 'decals'}}}

    monkeypatch.setattr(P, 'blender_steps', blender_steps)
    monkeypatch.setattr(I, 'grade_glb', grade)
    pf = SimpleNamespace(out=tmp_path, rec={'source_scene': {'sha256_before': 'original-scene'}})
    result = S.stage_p3(pf, FX.prada_default())
    assert result['pass'] is (failure is None)
    if failure != 'reference_export':
        assert captured['reference_used'] == captured['reference']
        assert captured['graded'] != captured['reference']
        assert captured['decal_names'] == ['foil']
        assert result['instrument']['global']['decals']['pass'] is (failure != 'decals')
    else:
        assert 'graded' not in captured


@pytest.mark.parametrize('cached', [
    {'pass': True},  # historical record omitted reference coverage
    {'pass': True, 'export_validation_version': 2, 'instrument': {'pass': True}},
    {'pass': False, 'export_validation_version': 3, 'instrument': {'pass': False}},
])
def test_resumed_gate_run_cannot_bypass_failed_or_incomplete_p3(tmp_path, monkeypatch, cached):
    from glasses3d.tests.test_temple_proof_completion import _Proof
    proof = _Proof(tmp_path)
    cached = {**cached, 'spec_sha256': S._spec_sha({})}
    proof.rec['stages']['P3'] = cached
    monkeypatch.setattr(S, 'stage_fit', lambda *a: {
        '_results': {'candidate': {'spec': {}}}, 'stop_s': [], 'conflicts': {},
        's3': {'stop_c': False, 'front_bound_stop': False}})
    monkeypatch.setattr(S, 'stage_gates', lambda *a: {'pass': True, 'failed': [], 'gates': {}})
    monkeypatch.setattr(S, 'label_sheet', lambda *a: None)
    result = S.run_rest(proof, None, {}, {}, {'guides': {}}, {'fit', 'gates'},
                        SimpleNamespace(quick=False), p2_done={'pass': True})
    assert result == 'STOP-T'
    assert proof.rec['stop']['evidence'] == cached
