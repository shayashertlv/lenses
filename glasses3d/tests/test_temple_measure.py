"""measure.chart_mesh: chart handling for both sides. (The v1 §5 instrument's tests, with the dense-area reference
tests/fixtures/plane_evidence.py, were deleted with the instrument on 2026-10-13; see git tag
review-20261013-pre-cleanup.)"""
from __future__ import annotations

import numpy as np

from glasses3d.g3d.temple import build_temple
from glasses3d.g3d.temple import fixtures as FX
from glasses3d.g3d.temple import measure as MS


def tri_normals(V: np.ndarray, F: np.ndarray) -> np.ndarray:
    a, b, c = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
    n = np.cross(b - a, c - a)
    return n / np.maximum(np.linalg.norm(n, axis=1)[:, None], 1e-300)


def test_chart_mesh_keeps_normals_outward_on_both_sides():
    tb = build_temple(FX.pyramid())
    for side in (+1, -1):
        W, _, T = tb.world(side)
        V, F = MS.chart_mesh(W, T, side, tb.spec.hinge["z_mm"])
        assert np.allclose(V, tb.V, atol=1e-12)
        assert np.allclose(tri_normals(V, F), tri_normals(tb.V, tb.tris), atol=1e-12)
