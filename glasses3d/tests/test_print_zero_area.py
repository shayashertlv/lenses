"""Degenerate geometry remains an A1 failure, without crashing UV layout or losing face IDs."""
import numpy as np
import pytest

from glasses3d.g3d import printuv as PU
from glasses3d.g3d.printuv import charts as CH
from glasses3d.tests.photoprint.test_printuv import _tube


@pytest.mark.parametrize("kind", ["caps", "strip"])
def test_zero_area_keeps_global_faces_and_regular_chart_geometry(kind):
    V, regular = _tube(n_s=12, n_t=12, length=30)
    # An unrelated face at index zero, followed by two degenerate faces among
    # the selected triangles: distinct collinear corners and a repeated corner.
    V = np.vstack([V, [[0, 0, 0], [1, 1, 1], [2, 2, 2]]])
    F = np.vstack([regular[0], [len(V)-3, len(V)-2, len(V)-1], regular, [0, 0, 1]])
    part = np.r_[-1, np.zeros(len(F)-1, int)]
    faces = np.arange(1, len(F))
    kwargs = dict(kind=kind, tx=2.0, ref=np.array([0., 0., 1.]))
    original, _ = CH.part_charts(V, F, np.arange(2, len(F)-1), **kwargs)
    charts, info = CH.part_charts(V, F, faces, **kwargs)
    for before, after in zip(original, charts[:-1]):
        assert before[0] == after[0]
        assert np.array_equal(before[1]+1, after[1])
        assert np.array_equal(before[2], after[2])
    assert info["zero_area_face_ids"] == [1, len(F)-1]
    spec = PU.PartSpec("part", faces, kind, np.array([0., 0., 1.]))
    lay = PU.layout(V, F, part, ["part"], ["part"], 2., specs={"part": spec})
    assert np.array_equal(np.sort(lay.faces), faces)
    assert np.unique(lay.faces).size == len(faces)
    rows = lay.face_index(len(F))
    assert rows[0] == -1 and np.array_equal(lay.faces[rows[faces]], faces)
    assert np.isfinite(lay.uv).all()
    assert lay.report["zero_area_faces"] == 2
    assert not lay.report["criteria"]["flips"] and not lay.report["pass"]


@pytest.mark.parametrize("kind", ["caps", "strip"])
def test_all_zero_area_geometry_has_a_finite_layout_and_failed_gate(kind):
    V = np.array([[0., 0., 0.], [1., 0., 0.], [2., 0., 0.]])
    F = np.array([[0, 1, 2], [0, 0, 1]])
    spec = PU.PartSpec("line", np.arange(2), kind, np.array([0., 0., 1.]))
    lay = PU.layout(V, F, np.zeros(2, int), ["line"], ["line"], 4., specs={"line": spec})
    assert np.array_equal(lay.faces, [0, 1])
    assert np.isfinite(lay.uv).all() and lay.report["zero_area_faces"] == 2
    assert not lay.report["pass"]


def test_zero_area_does_not_hide_invalid_faces_or_chart_kind():
    V = np.array([[0., 0., 0.], [1., 0., 0.], [2., 0., 0.]])
    F = np.array([[0, 1, 2], [0, 0, 1]])
    with pytest.raises(ValueError, match="unknown chart kind"):
        CH.part_charts(V, F, np.arange(2), kind="invalid", tx=1.)
    V[2, 0] = np.nan
    with pytest.raises(ValueError, match="non-finite triangle area"):
        CH.part_charts(V, F, np.arange(2), kind="caps", tx=1.)
