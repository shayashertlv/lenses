"""The 1 mm gain field must not depend on how densely its surface is rasterized."""
from types import SimpleNamespace

import numpy as np

from glasses3d.g3d_host.photoprint import gains as GN
from glasses3d.g3d_host import texture_utils as T


def _surface(tx):
    # A flat 8 x 4 mm surface, one photo per half, differing only in exposure.
    yy, xx = np.mgrid[:4 * tx, :8 * tx]
    ii, jj = yy.ravel(), xx.ravel()
    label = (jj >= 4 * tx).astype(np.int64)
    tex = SimpleNamespace(
        ii=ii, jj=jj, shape=yy.shape, index=np.arange(yy.size).reshape(yy.shape),
        region=label, chart=np.zeros(yy.size, np.int64), part=np.zeros(yy.size, np.int64),
        P=np.column_stack([(jj + .5) / tx, (ii + .5) / tx, np.zeros(yy.size)]))
    colours = np.where(label[:, None] == 0, [130., 85., 45.], [70., 45., 22.])
    return tex, label, colours


def test_gain_field_is_invariant_to_texel_density_at_fixed_physical_size():
    medians = []
    for tx in (4, 8, 16):
        tex, label, colours = _surface(tx)
        result = GN.level_field(tex, np.ones(len(label), bool), label, colours, 0, tx=tx)
        medians.append(np.median(np.log(result['gain'][label == 1]), axis=0))
        assert result['nodes'] == 32
        assert result['boundary_pairs'] == 4
    assert np.allclose(medians, medians[0], rtol=0, atol=1e-10)


def test_repeated_samples_of_the_same_chart_edge_do_not_reweight_smoothness():
    tex, label, colours = _surface(8)
    painted = np.ones(len(label), bool)
    # Corresponding surface samples can be repeated by seam-edge discretization.
    # They describe one node adjacency, so this cannot change the physical solve.
    pairs = np.array([[tex.index[y, 47], tex.index[y, 48]] for y in range(32)])
    a = GN.level_field(tex, painted, label, colours, 0, pairs, tx=8)
    b = GN.level_field(tex, painted, label, colours, 0, np.repeat(pairs, 3, axis=0), tx=8)
    assert np.allclose(a['gain'], b['gain'], rtol=0, atol=1e-12)


def test_assigning_two_view_labels_to_one_continuous_gradient_cannot_change_its_exposure():
    tex, label, _ = _surface(8)
    # Both cameras already observe the same spatially varying radiance. Their
    # adjacent node interiors differ because of the surface gradient, not gain.
    values = -2.5 + tex.P[:, :1] * np.array([[.08, .05, .03]])
    colours = T.linear_to_srgb(np.exp(values))
    result = GN.level_field(tex, np.ones(len(label), bool), label, colours, 0, tx=8)
    assert np.allclose(result['gain'], 1, rtol=0, atol=1e-10)


def test_common_surface_gradient_does_not_change_correction_of_a_real_exposure_step():
    tex, label, _ = _surface(8)
    constant = np.tile([-2.5, -3., -3.5], (len(label), 1))
    constant[label == 1] += np.log(1.4)
    gradient = tex.P[:, :1] * np.array([[.08, .05, .03]])
    painted = np.ones(len(label), bool)
    a = GN.level_field(tex, painted, label, T.linear_to_srgb(np.exp(constant)), 0, tx=8)
    b = GN.level_field(tex, painted, label, T.linear_to_srgb(np.exp(constant + gradient)), 0, tx=8)
    assert np.allclose(a['gain'], b['gain'], rtol=0, atol=1e-10)
    assert np.median(a['gain'][label == 1]) < 1


def test_boundary_extrapolation_does_not_depend_on_world_orientation():
    tex, label, colours = _surface(8)
    values = -2.5 + tex.P[:, :1] * np.array([[.08, .05, .03]])
    values[label == 1] += np.log(1.4)
    colours = T.linear_to_srgb(np.exp(values))
    painted = np.ones(len(label), bool)
    a = GN.level_field(tex, painted, label, colours, 0, tx=8)
    rotation, _ = np.linalg.qr(np.array([[1., 3., 2.], [4., 2., 1.], [3., 1., 5.]]))
    tex.P = tex.P @ rotation + [50., -10., 30.]
    b = GN.level_field(tex, painted, label, colours, 0, tx=8)
    assert np.allclose(a['gain'], b['gain'], rtol=0, atol=1e-10)


def test_boundary_gradient_uses_observations_inside_a_one_cell_wide_group():
    tex, _, _ = _surface(8)
    label = (tex.jj >= 8).astype(np.int64)
    tex.region = label
    values = -2.5 + tex.P[:, :1] * np.array([[.08, .05, .03]])
    colours = T.linear_to_srgb(np.exp(values))
    result = GN.level_field(tex, np.ones(len(label), bool), label, colours, 0, tx=8)
    assert np.allclose(result['gain'], 1, rtol=0, atol=1e-10)
