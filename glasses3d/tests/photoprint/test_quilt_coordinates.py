"""Repacking a chart must not translate its physical donor coordinate."""
from types import SimpleNamespace

import numpy as np
import pytest

from glasses3d.g3d_host.photoprint import fill as FL, quilt_t as QT


@pytest.mark.parametrize('y0', [0, 3, 17])
def test_canvas_and_atlas_coordinates_describe_the_same_texels(y0):
    height, width, x0 = y0 + 10, 24, 5
    index = np.full((height, width), -1, np.int64)
    ys, xs = np.mgrid[y0:y0 + 6, x0:x0 + 9]
    ii, jj = ys.ravel(), xs.ravel()
    index[ii, jj] = np.arange(len(ii))
    tex = SimpleNamespace(index=index, chart=np.zeros(len(ii), np.int64), ii=ii, jj=jj, shape=index.shape)
    layout = SimpleNamespace(charts=[{'part': 'arbitrary.part', 'chart': 0, 'kind': 'strip',
                                     'x0': x0, 'y0': y0, 'w': 9, 'h': 6}])
    canvas = FL.Canvas(layout, tex, 'arbitrary.part')
    actual = canvas.gather(QT.strip_t(canvas, ['strip']))
    # Geometric oracle: same original texel minus the chart's actual origin.
    expected = ii[canvas.rows] - y0
    assert np.array_equal(actual, expected)
    atlas = QT.atlas_t(layout, tex)
    assert np.array_equal(actual, atlas[ii[canvas.rows], jj[canvas.rows]])


def test_repacking_multiple_charts_preserves_each_origin_and_excludes_caps():
    charts = [
        {'part': 'sample', 'chart': 0, 'kind': 'strip', 'x0': 5, 'y0': 0, 'w': 9, 'h': 6},
        {'part': 'sample', 'chart': 1, 'kind': 'cap', 'x0': 25, 'y0': 17, 'w': 8, 'h': 5},
        {'part': 'sample', 'chart': 2, 'kind': 'strip', 'x0': 3, 'y0': 29, 'w': 16, 'h': 7},
    ]
    index = np.full((42, 40), -1, np.int64)
    ii, jj, chart_ids = [], [], []
    for c in charts:
        ys, xs = np.mgrid[c['y0']:c['y0'] + c['h'], c['x0']:c['x0'] + c['w']]
        index[ys, xs] = np.arange(len(ii), len(ii) + ys.size).reshape(ys.shape)
        ii.extend(ys.ravel()); jj.extend(xs.ravel()); chart_ids.extend([c['chart']] * ys.size)
    ii, jj, chart_ids = map(np.asarray, (ii, jj, chart_ids))
    tex = SimpleNamespace(index=index, chart=chart_ids, ii=ii, jj=jj, shape=index.shape)
    canvas = FL.Canvas(SimpleNamespace(charts=charts), tex, 'sample')
    result = QT.strip_t(canvas, [c['kind'] for c in charts])
    actual = canvas.gather(result)
    ids = chart_ids[canvas.rows]
    assert np.isnan(actual[ids == 1]).all()
    for c in (charts[0], charts[2]):
        selected = ids == c['chart']
        assert np.array_equal(actual[selected], ii[canvas.rows[selected]] - c['y0'])
    background = np.ones_like(result, bool)
    background[canvas.ii, canvas.jj] = False
    assert np.isnan(result[background]).all()
