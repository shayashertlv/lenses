"""Independent lightness fields: masking must not invent a cross-line step."""
from types import SimpleNamespace

import numpy as np
import pytest

from glasses3d.g3d_host import relief_cue as CU


def field_case(axis, slope, step, mask):
    """Known, already blurred L*: longitudinal ramp plus a constant side difference."""
    yy, xx = np.indices((100, 240))
    lightness = 35.0 + slope * (xx - 120) + step * (yy > 50)
    valid = np.ones_like(lightness, dtype=bool)
    if mask == 'overlap':
        valid &= np.where(yy > 50, xx >= 75, xx <= 165)
    elif mask == 'disjoint':
        valid &= np.where(yy > 50, xx >= 125, xx <= 115)
    line = [[20., 50.], [220., 50.]]
    if axis == 'vertical':
        lightness, valid = lightness.T, valid.T
        # Keep the same positive normal (towards the stepped side).
        line = [[50., 220.], [50., 20.]]
    return lightness, valid, line


@pytest.mark.parametrize('axis', ['horizontal', 'vertical'])
@pytest.mark.parametrize('slope', [-.18, .18])
@pytest.mark.parametrize('step', [-6., 0., 6.])
@pytest.mark.parametrize('mask', ['full', 'overlap', 'disjoint'])
def test_photo_step_uses_corresponding_samples(axis, slope, step, mask):
    lightness, valid, line = field_case(axis, slope, step, mask)
    # Supplying the cached field isolates sampling from RGB quantization and blur.
    job = SimpleNamespace(_cue_L={'test': lightness}, matte=lambda key: valid)
    result = CU.photo_steps(job, 'test', {'crease': line})['crease']
    if mask == 'disjoint':
        assert result is None, 'Samples at unrelated positions cannot establish a crease step'
    else:
        assert result is not None
        assert result['step_L'] == pytest.approx(step, abs=1e-12)


def test_photo_step_does_not_reuse_clipped_border_pixels():
    lightness = np.full((40, 80), 45.)
    job = SimpleNamespace(_cue_L={'test': lightness}, matte=lambda key: np.ones_like(lightness, bool))
    result = CU.photo_steps(job, 'test', {'outside': [[10., -10.], [70., -10.]]})['outside']
    assert result is None
