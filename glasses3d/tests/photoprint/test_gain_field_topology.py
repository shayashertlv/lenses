"""The 1 mm gain field must not depend on how densely its surface is rasterized."""
from types import SimpleNamespace

import numpy as np

from glasses3d.g3d_host.photoprint import gains as GN


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
