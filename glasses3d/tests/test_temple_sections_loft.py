"""WP6 helper upgrades (§4): g3d.polygon_section, g3d.loft_sections, g3d.check_round_corners."""
from __future__ import annotations

import math

import numpy as np
import pytest

from glasses3d.g3d.temple.loft import LoftError, loft_sections
from glasses3d.g3d.temple.sections import (SectionError, check_round_corners, check_round_corners_mesh,
                                           loops_from_segments, polygon_section, round_segments)

RECT = [[0.0, 0.0], [4.0, 0.0], [4.0, 2.0], [0.0, 2.0]]


def test_corner_kinds_and_flags():
    s = polygon_section(RECT, ["sharp", ("chamfer", 0.5), ("round", 0.6), "sharp"])
    P, sh = s["points"], s["sharp"]
    assert s["kind"].count("chamfer") == 2 and sh[np.array(s["kind"]) == "chamfer"].all()
    rnd = np.array(s["kind"]) == "round"
    assert rnd.sum() == round_segments(math.pi / 2) + 1 and not sh[rnd].any()
    # chamfer points 0.5 mm from the corner along both edges
    ch = P[np.array(s["kind"]) == "chamfer"]
    assert np.allclose(ch, [[3.5, 0.0], [4.0, 0.5]])
    # the round's points are 0.6 from one centre (3.4, 1.4) and tangent at both ends
    R = P[rnd]
    assert np.allclose(np.linalg.norm(R - [3.4, 1.4], axis=1), 0.6, atol=1e-12)
    assert np.allclose(R[0], [4.0, 1.4]) and np.allclose(R[-1], [3.4, 2.0])


def test_round_segment_rule():
    assert round_segments(math.radians(90)) == 8
    assert round_segments(math.radians(30)) == 8
    assert round_segments(math.radians(135)) == 12


def test_corner_that_does_not_fit_is_refused():
    with pytest.raises(SectionError):
        polygon_section(RECT, [("round", 1.5)] * 4)          # 2 mm edges cannot take two 1.5 mm rounds
    with pytest.raises(SectionError):
        polygon_section(RECT, ["sharp", ("bevel", 1.0), "sharp", "sharp"])


def test_check_round_corners_reports_never_refuses():
    good = polygon_section(RECT, ("round", 0.6))
    r = check_round_corners(good["points"], good["sharp"])
    assert r["ok"] and len(r["rounds"]) >= 1
    # a coarse round: a rectangle whose corner (4, 2) is a 3-segment quarter circle of radius 1
    a = np.linspace(0, math.pi / 2, 4)
    arc = np.c_[3.0 + np.cos(a), 1.0 + np.sin(a)]
    coarse = np.vstack([[[0.0, 0.0], [4.0, 0.0]], arc, [[0.0, 2.0]]])
    r = check_round_corners(coarse)
    assert not r["ok"]
    rnd = r["rounds"][0]
    assert rnd["turn_deg"] == pytest.approx(90.0) and rnd["needed_segments"] == 8 and rnd["segments"] == 3


def test_loops_from_segments_and_mesh_check():
    s = polygon_section(RECT, ("round", 0.5))
    P = s["points"]
    seg = np.stack([P, np.roll(P, -1, axis=0)], axis=1)
    loops = loops_from_segments(seg[::-1])
    assert len(loops) == 1 and len(loops[0]) == len(P)
    L = loft_sections([{"t": 0.0, **s}, {"t": 30.0, **s}])
    rep = check_round_corners_mesh(L["V"], L["F"], axis=2)
    assert rep["ok"] and rep["sections"]


def test_loft_equal_counts_closed_and_planar_prism():
    a = polygon_section([[-1, -2], [1, -2], [1, 2], [-1, 2]], ["sharp", ("round", 0.6), "sharp", ("round", 0.6)])
    b = polygon_section([[-0.5, -1], [0.5, -1], [0.5, 1], [-0.5, 1]], ["sharp", ("round", 0.2), "sharp",
                                                                      ("round", 0.2)])
    L = loft_sections([{"t": 0.0, **a}, {"t": 50.0, **b}, {"t": 100.0, **b}])
    assert L["closed"] and L["euler"] == 2 and L["volume_mm3"] > 0
    assert L["vertices_per_section"] == max(len(a["points"]), len(b["points"]))
    # sharp longitudinal edges at the two sharp anchors, every segment
    assert len(L["sharp_edges"]) == 2 * 2
    # the straight part (stations 50 -> 100, same section) has untwisted planar strips
    assert max(L["strip_twist_mm"]) < 0.2


def test_loft_follows_a_path_and_reports_twist():
    sq = {"points": [[-1, -1], [1, -1], [1, 1], [-1, 1]], "sharp": [True] * 4}
    rot = np.radians(30)
    R = np.array([[math.cos(rot), -math.sin(rot)], [math.sin(rot), math.cos(rot)]])
    twisted = {"points": (np.array(sq["points"]) @ R.T).tolist(), "sharp": [True] * 4}
    L = loft_sections([{"t": 0.0, **sq}, {"t": 20.0, **twisted}], path=[[60, 0, 0], [60, 0, -10], [55, 0, -30]])
    assert L["closed"] and max(L["strip_twist_mm"]) > 0.1
    V = L["V"]
    assert np.allclose(V[:4].mean(axis=0), [60, 0, 0], atol=1e-9)


def test_loft_refuses_mismatched_anchors():
    a = {"points": RECT, "sharp": [True] * 4}
    b = {"points": [[0, 0], [3, 0], [1.5, 2]], "sharp": [True] * 3}
    with pytest.raises(LoftError):
        loft_sections([{"t": 0.0, **a}, {"t": 10.0, **b}])
    with pytest.raises(LoftError):
        loft_sections([{"t": 5.0, **a}, {"t": 5.0, **a}])
