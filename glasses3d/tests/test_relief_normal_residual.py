"""Independent thin-mask and analytic-vector controls for normal proposals."""
import numpy as np
import pytest
from scipy import ndimage
from glasses3d.g3d_host.relief_normal_residual import normal_or_point_residual


@pytest.mark.parametrize('width', [1, 3, 5])
def test_thin_silhouette_center_keeps_its_point_error(width):
    mask = np.zeros((61, 81), float)
    mask[8:53, 40-width//2:41+width//2] = 1.
    gradient = np.array([ndimage.gaussian_filter(mask, 1., order=(0, 1))[30, 40],
                         ndimage.gaussian_filter(mask, 1., order=(1, 0))[30, 40]])
    assert np.linalg.norm(gradient) < 1e-12
    normalized = gradient / max(np.linalg.norm(gradient), 1e-12)
    assert np.linalg.norm(normalized) <= .99  # old diagnostic assertion fails
    delta = np.array([[3., -4.]])
    np.testing.assert_array_equal(normal_or_point_residual(delta, normalized[None]), delta)


@pytest.mark.parametrize('angle', [-73., 0., 24., 90.])
def test_valid_normal_discards_only_tangential_proposal_error(angle):
    a = np.radians(angle)
    n = np.array([np.cos(a), np.sin(a)])
    t = np.array([-n[1], n[0]])
    delta = 2.5*n + 8*t
    np.testing.assert_allclose(normal_or_point_residual(delta[None], n[None]), (2.5*n)[None], atol=1e-12)


def test_mixed_samples_are_retained_and_inputs_are_not_changed():
    delta = np.array([[3., 4.], [-5., 7.], [2., -3.]])
    normals = np.array([[1., 0.], [0., 0.], [0., 1.]])
    original = delta.copy()
    np.testing.assert_allclose(normal_or_point_residual(delta, normals), [[3., 0.], [-5., 7.], [0., -3.]])
    np.testing.assert_array_equal(delta, original)
    with pytest.raises(ValueError, match='finite'):
        normal_or_point_residual([[np.nan, 0]], [[1., 0.]])
