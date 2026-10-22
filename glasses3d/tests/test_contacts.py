"""Near-miss contacts (audit B1): the pure rules with a brute-force surface query, then the real worker."""
from __future__ import annotations

import numpy as np
import pytest

from glasses3d.g3d import contacts as C
from glasses3d.g3d_host import blender

CUBE_FACES = [(0, 1, 2), (0, 2, 3), (4, 6, 5), (4, 7, 6), (0, 4, 5), (0, 5, 1),
              (1, 5, 6), (1, 6, 2), (2, 6, 7), (2, 7, 3), (3, 7, 4), (3, 4, 0)]


def box(lo, hi):
    (x0, y0, z0), (x1, y1, z1) = lo, hi
    vertices = np.array([(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
                         (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)], float)
    return vertices, np.array(CUBE_FACES)


def point_triangle(p, a, b, c):
    """Ericson's closest point on a triangle, for brute-force reference distances."""
    ab, ac, ap = b - a, c - a, p - a
    d1, d2 = ab @ ap, ac @ ap
    if d1 <= 0 and d2 <= 0:
        return np.linalg.norm(p - a)
    bp = p - b
    d3, d4 = ab @ bp, ac @ bp
    if d3 >= 0 and d4 <= d3:
        return np.linalg.norm(p - b)
    vc = d1 * d4 - d3 * d2
    if vc <= 0 and d1 >= 0 and d3 <= 0:
        return np.linalg.norm(p - (a + d1 / (d1 - d3) * ab))
    cp = p - c
    d5, d6 = ab @ cp, ac @ cp
    if d6 >= 0 and d5 <= d6:
        return np.linalg.norm(p - c)
    vb = d5 * d2 - d1 * d6
    if vb <= 0 and d2 >= 0 and d6 <= 0:
        return np.linalg.norm(p - (a + d2 / (d2 - d6) * ac))
    va = d3 * d6 - d5 * d4
    if va <= 0 and d4 - d3 >= 0 and d5 - d6 >= 0:
        return np.linalg.norm(p - (b + (d4 - d3) / ((d4 - d3) + (d5 - d6)) * (c - b)))
    denominator = 1 / (va + vb + vc)
    return np.linalg.norm(p - (a + ab * vb * denominator + ac * vc * denominator))


def run(shapes, **kwargs):
    parts = [{"name": name, "role": "frame", "points": C.samples(*mesh)} for name, mesh in shapes]

    def nearest(j, points, limit):
        vertices, triangles = shapes[j][1]
        out = np.array([min(point_triangle(p, *vertices[t]) for t in triangles) for p in points])
        return np.where(out <= limit, out, np.inf)
    return C.near_misses(parts, nearest, **kwargs)


def test_a_visible_gap_is_a_near_miss_with_its_size_span_and_place():
    result = run([("brow", box((-10, 0, 0), (10, 2, 3))), ("lock", box((10.75, 0, 0), (14, 2, 3)))])
    assert result["schema"] == C.SCHEMA and result["complete"] and result["touching_pairs"] == 0
    [row] = result["near_misses"]
    assert row["parts"] == ["brow", "lock"] and row["roles"] == ["frame", "frame"]
    assert row["min_gap_mm"] == pytest.approx(0.75, abs=1e-6)
    assert row["gap_span_mm"] == pytest.approx(3.0, abs=0.01)  # the facing faces' 2 x 3 mm extent
    assert 10 <= row["closest_mm"][0] <= 10.75


def test_joined_faraway_and_sub_pixel_parts_are_not_reported():
    joined = run([("brow", box((-10, 0, 0), (10, 2, 3))), ("lock", box((10.1, 0, 0), (14, 2, 3)))])
    assert joined["near_misses"] == [] and joined["touching_pairs"] == 1
    far = run([("brow", box((-10, 0, 0), (10, 2, 3))), ("pad", box((13, 0, 0), (14, 2, 3)))])
    assert far["near_misses"] == [] and far["pairs_nearby"] == 0 and far["touching_pairs"] == 0
    crossing = run([("a", box((0, 0, 0), (4, 4, 4))), ("b", box((2, 2, 2), (6, 6, 6)))],
                   intersects=lambda i, j: True)
    assert crossing["near_misses"] == [] and crossing["touching_pairs"] == 1


def test_rows_are_ranked_by_gap_span_capped_and_the_time_budget_is_honest():
    shapes = [("rail", box((0, 0, 0), (40, 2, 2)))]
    shapes += [(f"pad{k}", box((5 * k, 2.5, 0), (5 * k + 1 + k * .5, 3, 2))) for k in range(5)]
    result = run(shapes, max_rows=3)
    spans = [row["gap_span_mm"] for row in result["near_misses"]]
    assert len(spans) == 3 and spans == sorted(spans, reverse=True) and result["omitted_near_misses"] == 2
    ticks = iter(range(100))
    partial = run(shapes, budget_s=1.5, clock=lambda: next(ticks))
    assert partial["complete"] is False and partial["pairs_checked"] < partial["pairs_nearby"]


def test_dense_parts_are_thinned_deterministically():
    points = np.arange(30).reshape(10, 3).astype(float)
    assert np.array_equal(C._strided(points, 20), points)
    thinned = C._strided(points, 4)
    assert len(thinned) == 4 and np.array_equal(thinned[[0, -1]], points[[0, -1]])


@pytest.mark.skipif(blender.blender_exe() is None, reason="Blender 5.2 not installed")
def test_scene_summary_reports_a_real_brow_to_lock_gap_read_only(tmp_path):
    from glasses3d.agent.job_scene import JobScene
    scene = JobScene(tmp_path, job={}, selftest=True)
    try:
        scene.start()
        built = scene.s.execute(
            "import bmesh\n"
            "def block(name, lo, hi):\n"
            "    mesh = bpy.data.meshes.new(name); bm = bmesh.new()\n"
            "    bmesh.ops.create_cube(bm, size=1.0); bm.to_mesh(mesh); bm.free()\n"
            "    obj = bpy.data.objects.new(name, mesh); bpy.context.scene.collection.objects.link(obj)\n"
            "    obj.location = [(a + b) / 2 for a, b in zip(lo, hi)]; obj.scale = [b - a for a, b in zip(lo, hi)]\n"
            "    obj['partRole'] = 'frame'\n"
            "block('brow', (-10, 0, 0), (10, 2, 3)); block('lock', (10.75, 0, 0), (14, 2, 3))\n"
            "block('pad', (-10, -4, 0), (-6, -2.1, 3))\n", call_id="build")
        assert built["status"] == "ok", built
        head = dict(scene.s.head)
        summary = scene.s.call_read("scene_summary", "scene_summary", {}, deadline_s=60, call_id="summary")
        contacts = summary["result"]["contacts"]
        assert contacts["complete"] and scene.s.head == head and summary["restored_to"] is None
        [row] = contacts["near_misses"]
        assert row["parts"] == ["brow", "lock"] and row["min_gap_mm"] == pytest.approx(0.75, abs=1e-4)
        only = scene.s.call_read("contacts", "contacts", {"objects": ["pad"]}, deadline_s=60, call_id="pad")
        assert only["result"]["near_misses"] == []
    finally:
        scene.close()
