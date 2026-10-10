"""WP6 temple core: the (d, y) arrangement (clipping, splitting, half-edge faces) and ear clipping."""
from __future__ import annotations

import numpy as np
import pytest

from glasses3d.g3d.temple import arrange as AR
from glasses3d.g3d.temple.spec import TempleError

SQ = np.array([[0.0, 0.0], [10.0, 0.0], [10.0, 6.0], [0.0, 6.0]])


def test_clip_segment_inside_parts():
    assert AR.clip_segment([-5, 3], [15, 3], SQ) == [(0.25, 0.75)]
    assert AR.clip_segment([-5, 9], [15, 9], SQ) == []
    # a segment along the boundary is inside (boundary included)
    assert AR.clip_segment([2, 0], [8, 0], SQ) == [(0.0, 1.0)]
    # concave outline: a chord leaves and re-enters
    U = np.array([[0, 0], [9, 0], [9, 9], [6, 9], [6, 3], [3, 3], [3, 9], [0, 9]], float)
    parts = AR.clip_segment([-1, 6], [10, 6], U)
    assert len(parts) == 2


def test_arrangement_tiles_and_faces_are_ccw():
    segs = [(np.array([5.0, -1.0]), np.array([5.0, 7.0]), ("station", 5.0)),
            (np.array([-1.0, 2.0]), np.array([11.0, 4.0]), ("facet", "X"))]
    arr = AR.build_arrangement(SQ, segs)
    assert len(arr.faces) == 4
    assert np.all(arr.areas > 0)
    assert arr.areas.sum() == pytest.approx(60.0, abs=1e-9)
    for f, p in zip(arr.faces, arr.interior_pts):
        assert AR.point_in_poly(p, arr.V[f])
    # the station and facet lines cross at one vertex shared by all four faces
    centre = [v for v in range(len(arr.V)) if sum(v in f for f in arr.faces) == 4]
    assert len(centre) == 1
    assert arr.V[centre[0]] == pytest.approx([5.0, 3.0 + 0.0 / 12], abs=1e-9)


def test_touching_and_collinear_segments_split_consistently():
    # a facet edge ending exactly on the outline, and a second segment overlapping it collinearly
    segs = [(np.array([2.0, 0.0]), np.array([2.0, 6.0]), ("facet", "A")),
            (np.array([2.0, 1.0]), np.array([2.0, 4.0]), ("chamfer_split", 0)),
            (np.array([2.0, 2.0]), np.array([10.0, 2.0]), ("facet", "B"))]
    arr = AR.build_arrangement(SQ, segs)
    assert arr.areas.sum() == pytest.approx(60.0, abs=1e-9)
    ys = sorted(arr.V[np.isclose(arr.V[:, 0], 2.0)][:, 1])
    assert ys == pytest.approx([0.0, 1.0, 2.0, 4.0, 6.0])
    tags = [t for (a, b), t in arr.edge_tags.items()
            if np.allclose(sorted([arr.V[a, 1], arr.V[b, 1]]), [1.0, 2.0]) and np.isclose(arr.V[a, 0], 2.0)]
    assert tags and ("chamfer_split", 0) in tags[0] and ("facet", "A") in tags[0]


def test_floating_component_is_joined_by_a_chord():
    tri = [np.array([3.0, 2.0]), np.array([6.0, 2.0]), np.array([4.0, 4.0])]
    segs = [(tri[i], tri[(i + 1) % 3], ("facet", "F")) for i in range(3)]
    with pytest.raises(TempleError) as e:
        AR.build_arrangement(SQ, segs)
    assert e.value.code == "E_TRIANGULATE"
    arr = AR.build_arrangement(SQ, segs, chord_fn=lambda d: (np.array([d, 0.0]), np.array([d, 6.0])))
    assert arr.chords_added == [("station", 3.0), ("station", 6.0)]      # first and last vertex in d: no pinch
    assert all(len(set(f)) == len(f) for f in arr.faces)
    assert arr.areas.sum() == pytest.approx(60.0, abs=1e-9)
    inside = [i for i, p in enumerate(arr.interior_pts) if AR.point_in_poly(p, np.array(tri))]
    assert sum(arr.areas[i] for i in inside) == pytest.approx(3.0, abs=1e-9)


def test_dangling_segment_is_refused():
    segs = [(np.array([2.0, -1.0]), np.array([2.0, 3.0]), ("facet", "X"))]
    with pytest.raises(TempleError) as e:
        AR.build_arrangement(SQ, segs)
    assert e.value.code == "E_TRIANGULATE"


def test_ear_clip_concave_with_collinear_vertices():
    P = np.array([[0, 0], [2, 0], [4, 0], [4, 4], [3, 4], [3, 1], [1, 1], [1, 4], [0, 4], [0, 2]], float)
    tris = AR.ear_clip(P, list(range(len(P))))
    area = sum(0.5 * ((P[b] - P[a])[0] * (P[c] - P[a])[1] - (P[b] - P[a])[1] * (P[c] - P[a])[0]) for a, b, c in tris)
    assert area == pytest.approx(AR.poly_area(P), abs=1e-12)
    assert all(0.5 * ((P[b] - P[a])[0] * (P[c] - P[a])[1] - (P[b] - P[a])[1] * (P[c] - P[a])[0]) > 0
               for a, b, c in tris)
    assert len(tris) == len(P) - 2


def test_vertex_pool_merges_within_tolerance():
    pool = AR.VertexPool()
    a = pool.add((1.0, 2.0))
    assert pool.add((1.0 + 5e-8, 2.0 - 5e-8)) == a
    assert pool.add((1.0 + 3e-7, 2.0)) != a


def test_box_pairs_match_brute_force():
    rng = np.random.default_rng(7)
    A = rng.uniform(0, 30, (120, 2))
    B = A + rng.normal(0, 3, (120, 2))
    B[:20, 0] = A[:20, 0]                                  # vertical chords (zero width in d)
    i, j = AR._box_pairs(A, B)
    got = set(zip(i.tolist(), j.tolist()))
    lo, hi = np.minimum(A, B) - AR.DEDUPE_MM, np.maximum(A, B) + AR.DEDUPE_MM
    want = {(a, b) for a in range(120) for b in range(120) if a != b and lo[a, 0] <= hi[b, 0] and lo[b, 0] <= hi[a, 0]
            and lo[a, 1] <= hi[b, 1] and lo[b, 1] <= hi[a, 1]}
    assert got == want


def test_small_ear_clip_follows_the_same_rule():
    rng = np.random.default_rng(3)
    for n in range(4, AR.SMALL_POLY + 1):
        for _ in range(5):
            ang = np.sort(rng.uniform(0, 2 * np.pi, n))
            rad = rng.uniform(1.0, 3.0, n)
            P = np.stack([rad * np.cos(ang), rad * np.sin(ang)], axis=1)       # a star-shaped, simple CCW polygon
            small = AR._ear_clip_small(P, list(range(n)))
            old, AR.SMALL_POLY = AR.SMALL_POLY, 3
            try:
                big = AR.ear_clip(P, list(range(n)))
            finally:
                AR.SMALL_POLY = old
            assert small == big
