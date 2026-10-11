"""Chart and section images of a /2 build (plan/WP6_FACETS.md §3.4): g3d.temple.draw. The per-edge crease shapes are
the core's, the images carry ridge / valley colours, contours, nodes and footprints, and the section strip overlays
builds and plain meshes at one scale. Images go to the test's temp folder (VIZ_DIR overrides)."""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

from glasses3d.g3d.temple import build_temple
from glasses3d.g3d.temple import draw as DR
from glasses3d.g3d.temple import fixtures as FX
from glasses3d.g3d.temple import measure as MS
from glasses3d.g3d.temple.build import mesh_topology


@pytest.fixture
def viz(tmp_path) -> Path:
    d = Path(os.environ.get("VIZ_DIR", tmp_path))
    d.mkdir(parents=True, exist_ok=True)
    return d


def colour_count(img, rgb, tol: int = 12) -> int:
    a = np.asarray(img.convert("RGB")).astype(int)
    return int(np.all(np.abs(a - np.array(rgb)) <= tol, axis=-1).sum())


def test_crease_edges_are_the_core_shapes():
    tb = build_temple(FX.prada_default())
    topo = mesh_topology(tb.polys)
    acc: dict = {}
    for a, b, shape, _ in DR.crease_edges(tb):
        l1, l2 = (tb.poly_region[p].split("/")[0] for p in (topo["directed"][(a, b)], topo["directed"][(b, a)]))
        pair = (l1, "base") if l2 == "base" else ((l2, "base") if l1 == "base" else tuple(sorted((l1, l2))))
        r = acc.setdefault(pair, {"ridge": 0.0, "valley": 0.0, "flat": 0.0})
        r[shape] += float(np.linalg.norm(tb.V[a] - tb.V[b]))
    table = {tuple(c["regions"]): c for c in tb.tables["creases"] if c["kind"] == "crease"}
    assert set(acc) == set(table)
    for pair, r in acc.items():
        c = table[pair]
        assert sum(r.values()) == pytest.approx(c["length_mm"], abs=1e-9)
        if c["shape"] != "flat":                     # every declared crease: the drawn majority is the built shape
            assert ("ridge" if r["ridge"] >= r["valley"] else "valley") == c["shape"] == c["declared"]


def test_chart_image_draws_creases_contours_nodes_and_footprints(viz):
    spec = FX.prada_default()
    tb = build_temple(spec)
    fp = {"decals": [{"name": "hallmark", "points": [[26, 0.5], [44, 0.5], [44, 3.5], [26, 3.5]]}],
          "hinge": [{"name": "knuckle", "points": [[-1, 0], [1, 0], [1, 4], [-1, 4]]}]}
    img = DR.chart_image(spec, tb, footprints=fp)
    img.save(viz / "chart_prada_default.png")
    assert img.size[0] == 1200 and 350 < img.size[1] < 700
    assert colour_count(img, DR.RIDGE) > 300 and colour_count(img, DR.VALLEY) > 100       # F4|base is a valley
    assert colour_count(img, DR.DECAL) > 50 and colour_count(img, DR.HINGE) > 20
    assert colour_count(img, DR.NODE, tol=20) > 50
    zoom = DR.chart_image(spec, tb, d_range=(0, 60))
    zoom.save(viz / "chart_prada_default_zoom.png")
    assert zoom.size[0] == 1200 and zoom.size[1] > img.size[1]


def test_thickness_contours_cover_the_range():
    tb = build_temple(FX.wedge())                    # 3.5 mm at the top edge, 6.0 at the bottom edge
    levels = [3.0, 3.5, 4.0, 4.5, 5.0, 5.5, 6.0, 6.5]
    segs = DR._contours(tb, levels)
    assert all(segs[lv] for lv in (4.0, 4.5, 5.0, 5.5)) and not segs[3.0] and not segs[6.5]
    for lv in (4.0, 5.5):                            # every segment end lies on an outer triangle at that thickness
        P = np.array([p for s in segs[lv] for p in s])
        t = tb.spec.base_t(P[:, 0], P[:, 1])
        assert np.abs(t - lv).max() < 0.05          # the core's triangles approximate the wedge closely


def test_a_region_at_a_contour_level_draws_no_noise():
    tb = build_temple(FX.pyramid())                  # the base is 5.0 mm (+- 1e-14) over d 0-65, the facets above it
    segs = DR._contours(tb, [5.0])[5.0]
    d5 = 80.0 + 10.0 * 0.34 / 0.47                   # the arm's base thickness passes 5.0 between d 80 (5.34) and 90
    assert segs and all(abs(p[0] - d5) < 1e-9 and abs(q[0] - d5) < 1e-9 for p, q in segs)


def test_section_strip_overlays_builds_and_meshes(viz):
    cur, prev = build_temple(FX.prada_default()), build_temple(FX.pyramid())
    slab = build_temple(FX.slab())
    W, _, tris = slab.world(1)
    L, F = MS.chart_mesh(W, tris, 1, slab.spec.hinge["z_mm"])
    img = DR.section_strip([("current", cur), ("original", (L, F)), ("previous", prev)])
    img.save(viz / "sections_three.png")
    assert img.size[0] == 170 * 7
    assert colour_count(img, DR.STYLES[1]["fill"], tol=4) > 2000             # the original is filled
    assert colour_count(img, DR.STYLES[2]["line"], tol=30) > 100             # the previous revision is drawn
    assert colour_count(img, DR.RIDGE, tol=30) > 10                           # crease crossings are marked
    pts = DR.section_crease_points(cur, 25.0)
    assert pts and {s for _, _, s in pts} <= {"ridge", "valley", "flat"}
    img2 = DR.section_strip({"slab": {"V": L, "F": F}}, stations=(10, 60))
    assert img2.size[0] == 340


def test_chain_segments_closes_a_section():
    tb = build_temple(FX.slab())
    lines = DR.chain_segments(MS.slice_d(tb.V, tb.tris, 30.0))
    assert len(lines) == 1 and np.allclose(lines[0][0], lines[0][-1])
    y = lines[0][:, 0]
    assert y.min() == pytest.approx(tb.spec.y_bot(30.0)) and y.max() == pytest.approx(tb.spec.y_top(30.0))
