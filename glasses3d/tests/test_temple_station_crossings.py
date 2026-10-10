"""Incidental strip subdivisions must not create slivers at clipped facet corners (g3d.temple/2: facet-edge/outline
crossings are station sources, subdivided between)."""
import numpy as np
import pytest

from glasses3d.g3d.temple import build as B, fixtures as FX
from glasses3d.g3d.temple.spec import parse


def roof(offset, height):
    # A rectangular roof independent of product assets. A->C intersects y=-8
    # analytically at d=8+offset, near an incidental d=8 subdivision.
    cy = 8 - 160 / (8 + offset)
    return FX.straight(
        outline={'top': [[0, 8], [40, 8]], 'bottom': [[0, -8], [40, -8]], 'tip_round_mm': 0},
        inner=[[0, 10], [40, 10]], base={'top': [[0, 5], [40, 5]], 'bottom': [[0, 5], [40, 5]]},
        nodes={'A': [0, 8, 'base'], 'B': [20, 8, 'base'], 'C': [10, cy, 5 + height],
               'H': [0, -8, 'base'], 'HL': [0, -40, None], 'CL': [10, -40, None],
               'E': [30, -8, 'base'], 'EL': [30, -40, None]},
        facets={'roof': ['A', 'B', 'C'], 'left': ['A', 'H', 'HL', 'CL', 'C'],
                'right': ['B', 'C', 'CL', 'EL', 'E']})


@pytest.mark.parametrize('offset', [-.01, -.002, -.0005, .0005, .002, .01])
def test_facet_outline_intersection_precedes_regular_subdivision(offset):
    ps = parse(roof(offset, 1.))
    stations = B.station_list(ps)
    assert min(abs(d - (8 + offset)) for d in stations) < 1e-10
    assert max(np.diff([ps.d0, *stations, ps.d1])) <= ps.options['station_step_mm'] + 1e-10


@pytest.mark.parametrize('offset', [.0005, .002, .01])
@pytest.mark.parametrize('height', [.5, 1., 2.])
def test_known_planar_roof_retains_normals_in_float32_metres(offset, height):
    tb = B.build_temple(roof(offset, height))
    outer = [i for i, p in enumerate(tb.tri_poly)
             if tb.poly_kind[p] == 'outer' and tb.poly_cell[p] == 'facet']
    for side in (-1, 1):
        W, _, T = tb.world(side)
        T = T[outer]
        stored = (W * .001).astype(np.float32).astype(float) * 1000
        def normals(V):
            n = np.cross(V[T[:, 1]] - V[T[:, 0]], V[T[:, 2]] - V[T[:, 0]])
            return n / np.linalg.norm(n, axis=1)[:, None]
        ideal, actual = normals(W), normals(stored)
        angles = np.degrees(np.arctan2(np.linalg.norm(np.cross(ideal, actual), axis=1),
                                      (ideal * actual).sum(axis=1)))
        assert max(angles) < .001
    assert tb.checks['closed'] and tb.checks['euler'] == 2
