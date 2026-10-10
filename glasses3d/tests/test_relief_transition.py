"""Independent boundary/tangent and topology controls for a local smooth-copy tool."""
import numpy as np
import pytest
from glasses3d.g3d_host.relief_transition import tangent_strip, subdivide_copy, topology_report


def strip(q, slope=-.2):
    return tangent_strip(q, lower=(0., -.25), upper=(0., .5),
                         top=[[0., 8.], [24., 8.]], slope=slope, start=0., fade_start=12., end=20.)


@pytest.mark.parametrize('slope', [-.4, -.1, .25])
def test_fixed_boundaries_and_independent_plane_tangent(slope):
    d = np.array([2., 7., 11.])
    q = np.c_[d, -.25*d]
    shift, derivative = strip(q, slope)
    np.testing.assert_allclose(shift, 0, atol=1e-12)
    np.testing.assert_allclose(derivative, np.tile([slope*.25, slope], (len(d), 1)), atol=1e-12)
    eps = 1e-6
    # Compare the patch with independent known plane values from either side of the seam.
    above = strip(q+[0., eps], slope)[0]
    plane_below = np.full(len(d), -slope*eps)
    np.testing.assert_allclose((above-plane_below)/(2*eps), slope, atol=2e-6)
    value, grad = strip(np.c_[d, .5*d], slope)
    np.testing.assert_allclose(value, 0, atol=1e-12)
    np.testing.assert_allclose(grad, 0, atol=1e-12)


def test_protected_end_and_no_change_outside_patch():
    q = np.array([[-1., 0.], [20., 2.], [23., 2.], [4., -2.], [4., 4.]])
    original = q.copy()
    value, grad = strip(q)
    np.testing.assert_array_equal(value, np.zeros(len(q)))
    np.testing.assert_array_equal(grad, np.zeros_like(q))
    np.testing.assert_array_equal(q, original)
    value, grad = strip([[20.-1e-5, 2.]])
    assert abs(value[0]) < 1e-10 and np.linalg.norm(grad[0]) < 1e-6


def test_derivative_matches_finite_difference_inside_and_after_outline_clip():
    q = np.array([[7., 1.], [14., 2.], [18., 2.]])
    _, grad = strip(q)
    eps = 1e-5
    for k in range(2):
        step = np.eye(2)[k]*eps
        numerical = (strip(q+step)[0]-strip(q-step)[0])/(2*eps)
        np.testing.assert_allclose(grad[:, k], numerical, atol=1e-9)


def test_midpoint_subdivision_preserves_closed_tetrahedron_and_volume():
    v = np.array([[0., 0., 0.], [2., 0., 0.], [0., 3., 0.], [0., 0., 4.]])
    f = np.array([[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]])
    v0, f0 = v.copy(), f.copy()
    refined, faces, labels = subdivide_copy(v, f, ['a', 'b', 'c', 'd'], 3)
    report = topology_report(refined, faces)
    assert report['closed'] and report['euler'] == 2
    assert report['volume_mm3'] == pytest.approx(4., abs=1e-12)
    assert report['triangles'] == 4*4**3
    assert set(labels) == {'a', 'b', 'c', 'd'}
    np.testing.assert_array_equal(v, v0)
    np.testing.assert_array_equal(f, f0)


def test_open_or_reversed_mesh_is_not_closed():
    v = [[0., 0., 0.], [1., 0., 0.], [0., 1., 0.]]
    assert not topology_report(v, [[0, 1, 2]])['closed']
    assert not topology_report(v, [[0, 1, 2], [0, 1, 2]])['closed']


def test_rejects_invalid_domain():
    with pytest.raises(ValueError, match='start'):
        tangent_strip([[1., 0.]], lower=(0., 0.), upper=(1., 0.), top=[[0., 1.], [2., 1.]],
                      slope=.1, start=1., fade_start=.5, end=2.)
