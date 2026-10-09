"""Photo-line intersections must map to their actual point on a faceted surface."""
from dataclasses import replace
import itertools

import numpy as np
import pytest

from glasses3d.g3d.temple import fixtures as FX
from glasses3d.g3d_host import camera as CM, relief_fit as RF, relief_trace as RT


def exact_lines(monkeypatch):
    # This isolates the coordinate mapping; line detection has its own controls.
    monkeypatch.setattr(RT, 'refine_line', lambda L, p0, p1: {'p0': p0, 'p1': p1, 'rms_px': 0., 'n': 40})


@pytest.mark.parametrize('height,yaw,pitch,side', list(itertools.product((2., 4.), (70., 90.), (-25., 25.), (1, -1))))
def test_surface_corner_is_lifted_at_the_image_intersection(monkeypatch, height, yaw, pitch, side):
    exact_lines(monkeypatch)
    key = '05' if side == 1 else '01'
    job = RF.synthetic_setup(FX.analytic_prism(h=height), views=(key,))
    cam = replace(job.true_cams[key], yaw=side*yaw, pitch=pitch, perspective=.22)
    fit = {'camera': cam, 'theta': {1: 0., -1: 0.}}
    d, y = 14., 1.
    # Independent closed-form surface, not production plane/triangle evaluation.
    x = 64. + min(height*d/20, height*(40-d)/20, height-height*abs(y)/10)
    uv = CM.project_mm(np.array([[side*x, y, job.hinge['z_mm']-d]]), cam, job.frame)[0]
    guides = {}
    for name, angle in [('a', 8.), ('b', -36.)]:
        direction = np.array([np.cos(np.radians(angle)), np.sin(np.radians(angle))])
        guides[name] = [(uv-75*direction).tolist(), (uv+75*direction).tolist()]
    result = RT.trace_creases(job, {key: guides}, job.truth, {key: fit},
                              corners={'q': ('a', 'b')}, lift_views=(key,))
    assert result['views'][key]['corners_dy']['q'] == pytest.approx([d, y], abs=1e-4)
    assert result['views'][key]['corner_lift_methods']['q'] == 'surface_intersection'


def test_off_surface_corner_keeps_line_extension(monkeypatch):
    exact_lines(monkeypatch)
    spec = FX.analytic_prism()
    spec['nodes'], spec['facets'] = {}, {}
    job = RF.synthetic_setup(spec, views=('05',))
    cam = replace(job.true_cams['05'], yaw=70., pitch=25., perspective=.22)
    fit = {'camera': cam, 'theta': {1: 0., -1: 0.}}
    corner = np.array([64., 15., job.hinge['z_mm']-20.])
    guides = {}
    for name, d in [('a', 10.), ('b', 30.)]:
        inside = np.array([64., 0., job.hinge['z_mm']-d])
        guides[name] = CM.project_mm(np.array([.6*corner+.4*inside, inside]), cam, job.frame).tolist()
    uv = CM.project_mm(corner[None], cam, job.frame)[0]
    assert RT.lift_px(job, job.truth, fit, uv, 1) is None
    result = RT.trace_creases(job, {'05': guides}, job.truth, {'05': fit},
                              corners={'q': ('a', 'b')}, lift_views=('05',))
    assert result['views']['05']['corners_dy']['q'] == pytest.approx([20., 15.], abs=1e-4)
    assert result['views']['05']['corner_lift_methods']['q'] == 'extended_chart_lines'


def test_parallel_lines_have_no_corner(monkeypatch):
    exact_lines(monkeypatch)
    job = RF.synthetic_setup(FX.analytic_prism(), views=('05',))
    fits = {'05': {'camera': job.true_cams['05'], 'theta': {1: 0., -1: 0.}}}
    guides = {'05': {'a': [[300., 300.], [500., 300.]], 'b': [[300., 320.], [500., 320.]]}}
    result = RT.trace_creases(job, guides, job.truth, fits,
                              corners={'q': ('a', 'b')}, lift_views=('05',))
    assert result['views']['05']['corners_px']['q'] is None
    assert 'q' not in result['corners']
