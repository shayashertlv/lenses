"""Independent planar R3 controls: no product fixtures, fitted cameras or rendered photos.

The source looks along +X; target yaw is 30 degrees. Orthographic scale is
10 px/mm, so a 7.5 px vertical displacement must measure exactly 0.75 mm.
Every paired photo crease is required even when one misses the candidate.
"""
from types import SimpleNamespace

import numpy as np
import pytest

from glasses3d.g3d_host import relief_check as RC
from glasses3d.g3d_host import relief_fit as RF
from glasses3d.g3d_host.camera import Camera, NormFrame


@pytest.fixture
def plate():
    vertices = np.array([[10., -8., -10.], [10., 8., -10.],
                         [10., 8., 10.], [10., -8., 10.]])
    faces = np.array([[0, 1, 2], [0, 2, 3]])
    model = SimpleNamespace(composite=lambda job, theta: (vertices, faces, np.ones(2, int)),
                            build=None, spec=None)
    job = SimpleNamespace(frame=NormFrame(np.zeros(3), 100.),
                          hinge={'axis_xz_mm': [10., 0.]})
    fits = {key: {'camera': Camera(yaw, 0., 0., 0., 1000., 200., 200.), 'theta': {1: 0.}}
            for key, yaw in [('05', 90.), ('02', 30.)]}
    return job, model, fits


def lines(y, error_mm=0.):
    # z=-6..6, source u=200-10z. Target u=200+10(cos(30)*10-sin(30)*z).
    v = 200. - 10. * y
    target_center = 200. + 100. * np.sqrt(3.) / 2.
    return [[260., v], [140., v]], [[target_center+30., v+10.*error_mm],
                                  [target_center-30., v+10.*error_mm]]


def context(job, fits, rows):
    return RF.Context(job, fits, {}, (),
                      {view: {name: ln[i] for name, ln in rows.items()}
                       for i, view in enumerate(('05', '02'))})


@pytest.mark.parametrize('offset', [0., 0.75, -1.25])
def test_transfer_matches_analytic_mm(plate, offset):
    job, model, fits = plate
    l05, l02 = lines(3., offset)
    r = RC.crease_transfer(job, model, fits['05']['theta'], fits['05']['camera'],
                           fits['02']['camera'], fits['02']['theta'], l05, l02)
    assert r['n'] == 25
    assert r['mean_abs_mm'] == pytest.approx(abs(offset), abs=1e-5)


def test_one_missed_crease_cannot_leave_a_passing_mean(plate):
    job, model, fits = plate
    ctx = context(job, fits, {'AB': lines(3.), 'BC': lines(20.)})
    r = RF.transfer_all(ctx, model)
    assert r['per_crease']['AB']['mean_abs_mm'] < 1e-5
    assert r['per_crease']['BC'] is None
    assert r['mean_mm'] is None, 'a failed BC measurement must not disappear from the denominator'
    assert r['missing_creases'] == ['BC'] and not r['complete']


def test_incomplete_transfer_cannot_make_the_fit_objective_cheaper(plate):
    job, model, fits = plate
    ctx = context(job, fits, {'AB': lines(3.), 'BC': lines(20.)})
    extras = RF._extra_terms(ctx, model)
    loss, details = RF._reduce(ctx, {}, extras, True)
    assert loss >= RF.INFEASIBLE
    assert details['transfer']['missing_creases'] == ['BC']
    assert RF._photo_and_penalty((loss, details))[0] is None


def test_all_paired_creases_keep_equal_weight_and_unpaired_is_reported(plate):
    job, model, fits = plate
    ctx = context(job, fits, {'AB': lines(3., .75), 'BC': lines(-3., 1.25)})
    ctx.guides_lines['05']['AC'] = lines(0.)[0]
    r = RF.transfer_all(ctx, model)
    assert r['mean_mm'] == pytest.approx(1., abs=1e-5)
    assert r['complete'] and r['missing_creases'] == []
    assert r['required_creases'] == ['AB', 'BC']
    assert r['unpaired_creases'] == {'05': ['AC'], '02': []}


def test_no_paired_lines_is_unmeasurable_without_inventing_an_objective_term(plate):
    job, model, fits = plate
    ctx = context(job, fits, {})
    result = RF.transfer_all(ctx, model)
    assert result['mean_mm'] is None and not result['complete']
    assert result['required_creases'] == []
    assert RF._reduce(ctx, {}, RF._extra_terms(ctx, model), False) == 0.


def test_missing_floor_does_not_fall_back_to_ideal_lines():
    from glasses3d.tests.proofs.wp6_prada_stages import _s1_case_b, S1_CASE_B
    rec = {'stages': {'S1': {
        'instrument_floor': {'models': {'truth': {'R3_transfer_mm': None}}},
        'theta_cases': {'cases': {S1_CASE_B: {'mean_mm': 0.1}}}}},
        'proof_record': {'s1_case_b_mm': 0.2}}
    assert _s1_case_b(rec) is None
    # Old evidence without an instrument-floor record remains readable.
    del rec['stages']['S1']['instrument_floor']
    assert _s1_case_b(rec) == 0.1


def test_instrument_floor_keeps_the_missing_crease_and_stops(plate, monkeypatch):
    from glasses3d.tests.proofs import wp6_theta_frame as TF
    job, model, fits = plate
    slab = SimpleNamespace(**vars(model))
    monkeypatch.setattr(TF, '_r2', lambda sj, m, f: {'pass': m is model})
    glines = context(job, fits, {'AB': lines(3.), 'BC': lines(20.)}).guides_lines
    result = TF.instrument_floor(job, model, slab, fits, glines)
    assert not result['pass']
    assert result['models']['truth']['R3_transfer_mm'] is None
    assert result['models']['truth']['R3_transfer']['missing_creases'] == ['BC']


@pytest.mark.parametrize('invalid', [float('nan'), float('inf'), -1.])
def test_nonfinite_or_negative_transfer_invalidates_the_mean(plate, monkeypatch, invalid):
    job, model, fits = plate
    monkeypatch.setattr(RC, 'crease_transfer', lambda *a, **kw: {'mean_abs_mm': invalid})
    result = RF.transfer_all(context(job, fits, {'AB': lines(3.)}), model)
    assert result['mean_mm'] is None and result['missing_creases'] == ['AB']
