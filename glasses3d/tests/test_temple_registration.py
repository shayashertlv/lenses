"""Independent camera controls: analytic geometry, original bounds and no crease/R3 inputs."""
import copy
import itertools
from dataclasses import replace

import pytest

from glasses3d.g3d_host import relief_registration as REG, relief_views as RV
from glasses3d.tests.fixtures.camera_evidence import make_job, projection_errors


@pytest.mark.parametrize('yaw,sign', list(itertools.product((25., 47., 68., -25., -47., -68., 90., -90.), (-1., 1.))))
def test_joint_alignment_independent_corner_projection(yaw, sign):
    job, model = make_job(yaw, sign)
    initial = RV._initial_view_fit(job, model, '02', job.base['02'])
    saved = copy.deepcopy(initial)
    result = REG.align_view(job, model, '02', initial, job.base['02'])
    info = result['alignment']
    assert info['kept'] and info['inside_original_box']
    assert info['front_final_mm'] <= info['front_base_mm'] + RV.RR.FRONT_DEGRADE_MAX_MM
    assert info['loss'] < info['initial_loss']
    assert info['termination'] != 'iteration_cap'
    assert all(row['loss'] <= row['loss_start'] for row in info['history'])
    before, after = (projection_errors(job, model, fit) for fit in (initial, result))
    # Keep all corner groups. Individual hidden-arm errors can worsen; aggregate improves.
    assert before.keys() == after.keys() == {'front', '+arm', '-arm'}
    assert sum(x['rms_px']**2 for x in after.values()) < sum(x['rms_px']**2 for x in before.values())
    assert initial['camera'] == saved['camera'] and initial['theta'] == saved['theta']
    if abs(yaw) == 90.:
        assert result['theta'] == {1: 0., -1: 0.}
        assert info['free_theta_sides'] == []


def test_unobserved_hinge_stays_fixed_and_outside_box_refuses():
    job, model = make_job(35., 1.)
    initial = RV._initial_view_fit(job, model, '02', job.base['02'])
    initial['theta_fit']['-1']['observed'] = False
    result = REG.align_view(job, model, '02', initial, job.base['02'])
    assert result['theta'][-1] == initial['theta'][-1]
    assert -1 not in result['alignment']['free_theta_sides']
    bad = copy.deepcopy(initial)
    bad['camera'] = replace(bad['camera'], yaw=bad['camera'].yaw + 10.)
    with pytest.raises(ValueError, match='outside original box'):
        REG.align_view(job, model, '02', bad, job.base['02'])


@pytest.mark.parametrize('unmeasured', [False, True])
def test_front_guard_rejection_preserves_initial_fit(monkeypatch, unmeasured):
    job, model = make_job(35., -1.)
    initial = RV._initial_view_fit(job, model, '02', job.base['02'])
    if unmeasured:
        initial['refit']['front_contour_start_mm'] = float('inf')
    ceiling = initial['refit']['front_contour_start_mm'] + RV.RR.FRONT_DEGRADE_MAX_MM
    monkeypatch.setattr(RV, 'front_contour_mm', lambda *args, **kwargs: ceiling + 1.)
    result = REG.align_view(job, model, '02', initial, job.base['02'])
    assert not result['alignment']['kept']
    assert result['camera'] == initial['camera']
    assert result['theta'] == initial['theta']
    assert result['refit'] == initial['refit']
