"""Geometric seam coverage independent of product data and quality thresholds."""
from types import SimpleNamespace

import numpy as np
import pytest

from glasses3d.g3d_host.photoprint import seams as SM


def _adjacent_rectangles(y0=0, unrelated=False):
    # Two adjacent 1 x 1 physical rectangles share x=1. Their UV charts are
    # separated by eight gutter texels, at density eight texels per unit.
    V = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0], [2, 0, 0], [2, 1, 0]], float)
    F = np.array([[0, 1, 2], [1, 3, 2], [1, 4, 3], [4, 5, 3]])
    uv = V[F, :2] * 8
    uv[2:, :, 0] += 8
    uv[:, :, 1] += y0
    index = np.full((y0 + 10, 27), -1, np.int64)
    ii, jj, ids = [], [], []
    for chart, xa, xb in ((0, 0, 8), (1, 16, 24)):
        yy, xx = np.mgrid[y0:y0 + 8, xa:xb]
        index[yy, xx] = np.arange(len(ii), len(ii) + yy.size).reshape(yy.shape)
        ii.extend(yy.ravel()); jj.extend(xx.ravel()); ids.extend([chart] * yy.size)
    if unrelated:
        # An unrelated chart near the edge must never become a seam partner.
        for y in range(y0, y0 + 8):
            index[y, 8] = len(ii)
            ii.append(y); jj.append(8); ids.append(9)
    tex = SimpleNamespace(index=index, ii=np.asarray(ii), jj=np.asarray(jj), chart=np.asarray(ids), shape=index.shape)
    layout = SimpleNamespace(faces=np.arange(4), uv=uv, chart=np.array([0, 0, 1, 1]))
    job = SimpleNamespace(mesh=SimpleNamespace(V=V, F=F))
    return job, layout, tex


@pytest.mark.parametrize('y0', [0, 5])
def test_edge_on_empty_gutter_still_connects_adjacent_surface_texels(y0):
    job, layout, tex = _adjacent_rectangles(y0)
    pairs = SM.chart_seam_pairs(job, layout, tex)
    actual = {(int(tex.ii[a]), int(tex.jj[a]), int(tex.ii[b]), int(tex.jj[b])) for a, b in pairs}
    expected = {(y, 7, y, 16) for y in range(y0, y0 + 8)}
    assert actual == expected


def test_seam_lookup_cannot_pair_an_unrelated_neighbouring_chart():
    job, layout, tex = _adjacent_rectangles(unrelated=True)
    pairs = SM.chart_seam_pairs(job, layout, tex)
    assert len(pairs) == 8
    assert np.all(tex.chart[pairs[:, 0]] == 0)
    assert np.all(tex.chart[pairs[:, 1]] == 1)


def test_missing_local_surface_support_does_not_bridge_a_wider_gap():
    job, layout, tex = _adjacent_rectangles()
    tex.index[:, 6:8] = -1
    assert SM.chart_seam_pairs(job, layout, tex).shape == (0, 2)
