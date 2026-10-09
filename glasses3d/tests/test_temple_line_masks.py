"""Independent corrupted-image controls for the line tracer's mask handling."""
import itertools

import numpy as np
import pytest

from glasses3d.g3d_host import relief_trace as RT
from glasses3d.tests.fixtures import line_evidence as FX


@pytest.mark.parametrize('kind', ['clipped', 'decal', 'occluded'])
@pytest.mark.parametrize('angle_deg', [0., 31., 87.])
@pytest.mark.parametrize('blur_px', [1., 2.])
@pytest.mark.parametrize('signs', list(itertools.product((-1., 1.), repeat=2)))
def test_masked_contamination_recovers_known_edge(kind, angle_deg, blur_px, signs):
    image, guide, exact, valid = FX.make_case(kind, angle_deg, blur_px, signs)
    line = RT.refine_line(image, *guide, valid_mask=valid)
    assert line is not None
    assert max(FX.endpoint_error(line, exact)) < .25


def test_excluded_pixels_cannot_move_the_measurement():
    image, guide, exact, valid = FX.make_case('decal', 31., 1., (-1., 1.))
    results = []
    for value in (-1000., 0., 1000., np.nan):
        changed = image.copy()
        changed[~valid] = value
        results.append(RT.refine_line(changed, *guide, valid_mask=valid))
    assert all(r is not None for r in results)
    assert all(r == results[0] for r in results)


def test_no_valid_evidence_is_not_a_line():
    image, guide, _, valid = FX.make_case('clean', 31., 1., (-1., 1.))
    assert RT.refine_line(image, *guide, valid_mask=np.zeros_like(valid)) is None


def test_mask_shape_mismatch_is_an_input_error():
    image, guide, _, valid = FX.make_case('clean', 31., 1., (-1., 1.))
    with pytest.raises(ValueError, match='mask.*shape'):
        RT.refine_line(image, *guide, valid_mask=valid[:-1])
