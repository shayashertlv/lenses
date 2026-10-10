"""Guard and observation-retention checks for opt-in contour-normal refinement."""
import copy
from types import SimpleNamespace

import numpy as np
import pytest

from glasses3d.g3d_host import relief_registration as REG, relief_views as RV
from glasses3d.tests.fixtures.camera_evidence import make_job


def initial_fit(job):
    return {'camera':job.base['02'], 'theta':{1:0.,-1:0.},
            'theta_fit':{'1':{'observed':True},'-1':{'observed':False}},
            'refit':{'front_contour_start_mm':0.}}


@pytest.mark.parametrize('yaw', [90., -90.])
def test_side_refinement_preserves_exact_existing_fit(yaw, monkeypatch):
    job,model=make_job(yaw,1.)
    fit=initial_fit(job)
    def forbidden(*args,**kwargs):
        raise AssertionError('Side-view alignment must not run')
    monkeypatch.setattr(REG,'align_view',forbidden)
    assert REG.refine_contour_normals(job,model,'02',fit,job.base['02']) is fit


def test_directionless_normals_keep_full_correspondence_residual(monkeypatch):
    job,model=make_job(35.,1.)
    fit=initial_fit(job)
    captured=[]
    def solve(fun,x,**kwargs):
        captured.append(fun(x))
        return SimpleNamespace(x=x.copy(),nfev=1)
    monkeypatch.setattr(REG.optimize,'least_squares',solve)
    monkeypatch.setattr(REG,'contour_normals',lambda m:np.zeros((*m.shape,2)))
    monkeypatch.setattr(RV,'front_contour_mm',lambda *args,**kwargs:0.)
    point=REG.align_view(job,model,'02',fit,job.base['02'])
    normal=REG.refine_contour_normals(job,model,'02',fit,job.base['02'])
    assert len(captured)==2, 'Directionless contour samples must not be dropped'
    assert np.linalg.norm(captured[0])>0
    np.testing.assert_array_equal(captured[0],captured[1])
    assert normal['alignment']['history'][0]['matches']==point['alignment']['history'][0]['matches']
    assert not normal['alignment']['kept']


@pytest.mark.parametrize('front', [float('inf'),float('nan'),1.])
def test_normal_refinement_requires_original_front_guard(front,monkeypatch):
    job,model=make_job(35.,1.)
    fit=initial_fit(job);saved=copy.deepcopy(fit)
    monkeypatch.setattr(RV,'front_contour_mm',lambda *args,**kwargs:front)
    result=REG.refine_contour_normals(job,model,'02',fit,job.base['02'])
    assert result['alignment']['free_theta_sides']==[1]
    assert not result['alignment']['kept']
    assert result['camera']==saved['camera']
    assert result['theta']==saved['theta']
    assert fit==saved
