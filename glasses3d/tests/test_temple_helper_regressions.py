"""Task 8: helper geometry preserves corners and refuses unsupported template topology."""
from __future__ import annotations

import math

import numpy as np
import pytest

from glasses3d.g3d.temple.loft import loft_sections
from glasses3d.g3d.temple.sections import (check_round_corners, check_round_corners_mesh, loops_from_segments,
                                         polygon_section)
from glasses3d.g3d.temple.spec import DEFAULT_OPTIONS, TempleError
from glasses3d.g3d.temple.template import template_from_mesh


RECT = [[0.0, 0.0], [4.0, 0.0], [4.0, 2.0], [0.0, 2.0]]


def _mixed_rounds(height=4.0):
    points = []
    for cx, cy, a0 in ((3.4, 0.6, -90), (3.4, height - 0.6, 0), (0.6, height - 0.6, 90), (0.6, 0.6, 180)):
        for angle in np.linspace(a0, a0 + 90, 9 if a0 == -90 else 4):
            a = math.radians(angle)
            points.append([cx + 0.6 * math.cos(a), cy + 0.6 * math.sin(a)])
    return np.array(points)


def test_all_smooth_corners_are_checked_independent_of_start():
    points = _mixed_rounds()
    for offset in range(len(points)):
        report = check_round_corners(np.roll(points, offset, axis=0))
        assert not report["ok"]
        assert len(report["rounds"]) == 4
        assert sorted(r["segments"] for r in report["rounds"]) == [3, 3, 3, 8]


def test_close_coarse_corners_are_not_skipped_after_the_first_run():
    # The original review reproducer: one short straight lies below the existing STRAIGHT_RATIO threshold,
    # so two coarse arcs are one 180-degree run. Both still need checking at every cyclic start.
    points = _mixed_rounds(height=2.0)
    for offset in range(len(points)):
        report = check_round_corners(np.roll(points, offset, axis=0))
        assert not report["ok"]
        assert sum(r["vertices"] for r in report["rounds"]) == len(points)


def test_mesh_sections_check_every_round_despite_triangle_slice_midpoints():
    section = polygon_section(RECT, ("round", 0.6))
    mesh = loft_sections([{"t": 0, **section}, {"t": 30, **section}])
    report = check_round_corners_mesh(mesh["V"], mesh["F"], stations=[-13.0])
    assert report["ok"]
    assert len(report["sections"]) == 1
    assert len(report["sections"][0]["rounds"]) == 4


def test_a_closed_circle_counts_its_closing_segment():
    angle = np.linspace(0, 2 * math.pi, 32, endpoint=False)
    report = check_round_corners(np.c_[np.cos(angle), np.sin(angle)])
    assert report["ok"]
    assert report["rounds"][0]["segments"] == 32


def test_unequal_loft_counts_preserve_every_original_corner_vertex():
    first = polygon_section(RECT, ["sharp", ("round", 0.6), "sharp", "sharp"])
    last = polygon_section([[0, 0], [4, 0], [3.9, 2], [0, 2]],
                           ["sharp", ("round", 0.6), "sharp", "sharp"])
    mesh = loft_sections([{"t": 0, **first}, {"t": 10, **last}])
    count = mesh["vertices_per_section"]
    for i, section in enumerate((first, last)):
        ring = mesh["V"][i * count:(i + 1) * count, :2]
        distances = np.linalg.norm(section["points"][:, None] - ring[None], axis=2).min(axis=1)
        assert distances.max() < 1e-12
        assert check_round_corners(ring, mesh["sharp_anchor"])["ok"]
    assert mesh["closed"] and mesh["euler"] == 2


def test_stadium_section_has_no_duplicate_closure_and_lofts_closed():
    section = polygon_section(RECT, ("round", 1.0))
    points = section["points"]
    assert np.linalg.norm(points - np.roll(points, 1, axis=0), axis=1).min() > 1e-9
    mesh = loft_sections([{"t": 0, **section}, {"t": 20, **section}])
    tri = mesh["V"][mesh["F"]]
    assert np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1).min() > 1e-12
    assert mesh["closed"] and mesh["euler"] == 2


@pytest.mark.parametrize("offset", range(4))
def test_duplicate_corner_merge_preserves_sharp_chamfer_anchor(offset):
    # The round and chamfer consume the whole left edge, including across the loop closure.
    points = np.roll(np.array(RECT), offset, axis=0)
    corners = np.roll(np.array([("round", 1.0), "sharp", "sharp", ("chamfer", 1.0)], dtype=object),
                      offset).tolist()
    section = polygon_section(points, corners)
    match = np.linalg.norm(section["points"] - [0, 1], axis=1) < 1e-9
    assert match.sum() == 1
    assert section["sharp"][match].all()
    assert np.array(section["kind"])[match].tolist() == ["chamfer"]


def test_internal_duplicate_prefers_sharp_over_the_preceding_round():
    section = polygon_section([[0, 0], [2, 0], [2, 2], [0, 2]],
                              [("round", 1.0), ("chamfer", 1.0), "sharp", "sharp"])
    match = np.linalg.norm(section["points"] - [1, 0], axis=1) < 1e-9
    assert match.sum() == 1 and section["sharp"][match].all()
    assert np.array(section["kind"])[match].tolist() == ["chamfer"]


def _body(mask, *, thickness=4.0, height_step=2.0, d_step=20.0):
    """Closed extruded grid: weld shared vertices and omit internal voxel faces."""
    mask = np.asarray(mask, bool)
    vertices, faces, pool = [], [], {}

    def vertex(p):
        if p not in pool:
            pool[p] = len(vertices)
            vertices.append(p)
        return pool[p]

    for row, col in zip(*np.nonzero(mask)):
        x0, x1 = 60.0, 60.0 + thickness
        y0, y1 = row * height_step, (row + 1) * height_step
        z0, z1 = -(col + 1) * d_step, -col * d_step
        quads = [[(x0, y0, z0), (x0, y0, z1), (x0, y1, z1), (x0, y1, z0)],
                 [(x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1)]]
        for dr, dc, quad in (
            (-1, 0, [(x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1)]),
            (1, 0, [(x0, y1, z0), (x0, y1, z1), (x1, y1, z1), (x1, y1, z0)]),
            (0, -1, [(x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]),
            (0, 1, [(x0, y0, z0), (x0, y1, z0), (x1, y1, z0), (x1, y0, z0)]),
        ):
            r, c = row + dr, col + dc
            if not (0 <= r < mask.shape[0] and 0 <= c < mask.shape[1] and mask[r, c]):
                quads.append(quad)
        for quad in quads:
            a, b, c, d = [vertex(p) for p in quad]
            faces.extend([[a, b, c], [a, c, d]])
    return np.array(vertices), np.array(faces)


@pytest.mark.parametrize("mask", [
    [[1, 1, 1], [0, 0, 0], [1, 1, 1]],  # separate bodies
    [[1, 1, 1], [1, 0, 0], [1, 1, 1]],  # connected fork
    [[1, 1, 1], [1, 0, 1], [1, 1, 1]],  # connected window
])
def test_template_refuses_split_branched_or_holed_body(mask):
    vertices, faces = _body(mask)
    with pytest.raises(TempleError) as caught:
        template_from_mesh(vertices, faces, 1, 0, None, samples=100)
    assert caught.value.code == "E_TARGET"
    assert "component" in caught.value.message or "loop" in caught.value.message


def test_template_refuses_a_window_between_measurement_stations():
    vertices, faces = _body([[1, 1, 1], [1, 0, 1], [1, 1, 1]])
    # The entire middle slab lies between d=20 and d=21. It must not disappear between 1 mm stations.
    vertices[:, 2] = -np.interp(-vertices[:, 2], [0, 20, 40, 60], [0, 20.2, 20.4, 60])
    with pytest.raises(TempleError) as caught:
        template_from_mesh(vertices, faces, 1, 0, None, samples=100)
    assert caught.value.code == "E_TARGET"
    assert caught.value.fields["section_loops"] == 2


def test_section_chaining_does_not_call_an_open_chain_a_closed_loop():
    points = np.array([[0, 0], [0, 2], [2, 2], [2, 0]])
    segments = np.stack([points[:-1], points[1:]], axis=1)
    assert loops_from_segments(segments) == []


@pytest.mark.parametrize("sides", [1, 3])
def test_template_refuses_open_sheet_sections(sides):
    # A connected one-sided or U-shaped shell, with open side sections, cannot supply a solid cross-section.
    points = np.array([[60, 0], [60, 4], [64, 4], [64, 0]])[:sides + 1]
    vertices = np.vstack([np.c_[points, np.zeros(len(points))], np.c_[points, np.full(len(points), -60)]])
    n = len(points)
    faces = np.array([[v for v in face] for i in range(n - 1)
                      for face in ((i, i + 1, n + i + 1), (i, n + i + 1, n + i))])
    with pytest.raises(TempleError) as caught:
        template_from_mesh(vertices, faces, 1, 0, None, samples=100)
    assert caught.value.code == "E_TARGET"
    assert caught.value.fields["section_loops"] == 0


def test_template_low_sections_have_a_factual_target_refusal():
    vertices, faces = _body([[1, 1, 1]], height_step=0.2)
    with pytest.raises(TempleError) as caught:
        template_from_mesh(vertices, faces, 1, 0, None, samples=100)
    assert caught.value.code == "E_TARGET"
    assert "0.3 mm" in caught.value.message


def test_template_thin_sections_name_the_default_floor():
    vertices, faces = _body([[1, 1, 1]], thickness=0.8)
    with pytest.raises(TempleError) as caught:
        template_from_mesh(vertices, faces, 1, 0, None, samples=100)
    assert caught.value.code == "E_TOO_THIN"
    assert f"default t_min_mm={DEFAULT_OPTIONS['t_min_mm']:g} mm" in caught.value.message


def test_template_reports_reverse_surface_distance_and_accepts_split_normals():
    vertices, faces = _body([[1, 1, 1]])
    # GLB normal/UV seams duplicate positions: topology checks must weld these, not refuse them as components.
    split = vertices[faces].reshape(-1, 3)
    _, report = template_from_mesh(split, np.arange(len(split)).reshape(-1, 3), 1, 0, None, samples=100)
    reverse = report["distance_rebuild_to_original"]
    assert reverse["samples"] == 100 and reverse["all"]["n"] == 100
    assert reverse["all"]["max_mm"] <= 0.011
