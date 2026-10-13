"""Open construction preserves real background holes without changing the historical matte modes."""
import numpy as np
import pytest
from scipy import ndimage

from glasses3d.g3d_host import fit_views, matte as M
from glasses3d.tests.test_matte import gradient_photo
from glasses3d.tests.test_temple_matte_glossy import PHOTOS, REF


def legacy_default(image):
    """Independent frozen WP3 recipe, including the original component-removal condition."""
    difference = np.abs(image.astype(float) - M.background_model(image)).max(2)
    mask = ndimage.binary_opening(difference > 18, iterations=1)
    mask = ndimage.binary_closing(mask, iterations=2)
    mask = ndimage.binary_fill_holes(mask)
    labels, count = ndimage.label(mask)
    if count > 1:
        sizes = ndimage.sum(mask, labels, range(1, count + 1))
        mask = np.isin(labels, 1 + np.nonzero(sizes > 300)[0])
    return mask


def test_open_frame_preserves_background_holes_and_observed_foreground():
    image, _, _ = gradient_photo()
    opened = M.matte(image, mode='open_frame')
    ordinary = M.matte(image)
    assert ordinary[120, 120] and not opened[120, 120]
    assert opened[118, 250]  # a real dark temple stays foreground
    assert opened[120, 175]  # the enclosing dark rim itself stays foreground
    assert not opened[:20].any() and not opened[:, -20:].any()
    assert not opened[30:33, 40:43].any()  # same dust removal
    assert not np.any(opened & ~ordinary)
    # A coloured region inside an opening is actual foreground, not something the construction may discard.
    image[105:136, 105:136] = (40, 100, 160)
    observed = M.matte(image, mode='open_frame')
    assert observed[120, 120] and not observed[120, 150]


@pytest.mark.skipif(not PHOTOS, reason='reference photos not imported')
@pytest.mark.parametrize('product,name', PHOTOS)
def test_default_reference_masks_remain_byte_identical(product, name):
    image = M.load_photo(REF / product / name)
    expected = legacy_default(image)
    assert M.matte(image).tobytes() == expected.tobytes()
    assert M.matte(image, mode='default').tobytes() == expected.tobytes()


@pytest.mark.parametrize('context', [
    {}, {'optional_parts': [], 'lens_loss': {'mode': 'optional'}},
    {'optional_parts': [0], 'lens_loss': {'mode': 'full'}},
])
def test_absent_mode_needs_actual_optional_lens_faces(context):
    assert fit_views._matte_mode(None, context) == 'default'


@pytest.mark.parametrize('mode', M.MODES)
def test_explicit_mode_is_authoritative_for_either_construction(mode):
    for context in ({}, {'optional_parts': [0], 'lens_loss': {'mode': 'optional'}}):
        assert fit_views._matte_mode(mode, context) == mode
