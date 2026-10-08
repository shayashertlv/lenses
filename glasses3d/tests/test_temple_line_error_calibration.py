"""Independent evidence calibration, NOT a replacement for S1's retained stress.

An analytic blurred step has a known edge, without a product mesh, camera fit or
photo. Large guide corrections need not be large residual measurement errors.
This proves those quantities differ; it cannot establish the error of real
photographs with highlights, decals, occlusion or ambiguous parallel edges.
"""
import itertools

import numpy as np
import pytest
from scipy.special import ndtr

from glasses3d.g3d_host import relief_cue as CU
from glasses3d.g3d_host import relief_trace as RT


@pytest.mark.parametrize('angle_deg', [0., 31., 87.])
@pytest.mark.parametrize('blur_px', [1., 2.])
@pytest.mark.parametrize('signs', list(itertools.product((-1., 1.), repeat=2)))
def test_guide_displacement_is_not_residual_error(angle_deg, blur_px, signs, record_property):
    angle = np.deg2rad(angle_deg)
    tangent = np.array([np.cos(angle), np.sin(angle)])
    normal = np.array([-tangent[1], tangent[0]])
    centre = np.array([128., 128.])
    yy, xx = np.mgrid[:256, :256]
    distance = (xx-centre[0])*normal[0] + (yy-centre[1])*normal[1]
    lightness = 40. + 12.*ndtr(distance/blur_px)
    exact = centre + np.array([-75., 75.])[:, None]*tangent
    guide = exact + 6.*np.array(signs)[:, None]*normal
    snapped = RT.refine_line(lightness, *guide)
    assert snapped is not None  # every predeclared angle/blur/sign case is required
    observed = np.array([snapped['p0'], snapped['p1']])
    residual_px = float(np.max(np.abs((observed-centre) @ normal)))
    corrections = CU.endpoint_sigmas({'02': {'AB': guide}}, {'02': {'AB': observed}})
    largest_correction = max(corrections.values())
    record_property('residual_px', residual_px)
    record_property('guide_correction_px', largest_correction)
    # Accuracy relative to an analytic edge, not an acceptance threshold for a
    # real photo or permission to reduce the original 3-7 px S1 stress inputs.
    assert residual_px < .25
    assert largest_correction > 4.
