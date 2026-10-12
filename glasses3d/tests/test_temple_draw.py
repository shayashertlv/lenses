"""Chart and section images of a /2 build (plan/WP6_FACETS.md §3.4): g3d.temple.draw. The per-edge crease shapes are
the core's, the images carry ridge / valley colours, contours, nodes, facet names (one FACET fill, no lean shading)
and footprints under a legend that fits the width, and the section strip overlays
builds and plain meshes at one scale. Images go to the test's temp folder (VIZ_DIR overrides)."""
from __future__ import annotations

import math
import os
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

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


FOOTPRINTS = {"decals": [{"name": "hallmark", "points": [[26, 0.5], [44, 0.5], [44, 3.5], [26, 3.5]]}],
              "hinge": [{"name": "knuckle", "points": [[-1, 0], [1, 0], [1, 4], [-1, 4]]}]}


def warp_soft() -> dict:
    """A warp facet W (P3 1.5 mm off the plane of the others) above a soft flat facet S sharing its edge P1-P2: every
    optional legend item but the footprints (test_temple_warp's prada_warp, the Blender export case)."""
    s = FX.base_spec()
    tz = FX.T_ZONE
    s["nodes"] = {"P1": [6.0, 2.0, tz + 1.0], "P2": [52.0, 1.0, tz + 1.0], "P3": [56.0, 9.5, tz + 0.6 + 1.5],
                  "P4": [8.0, 9.0, tz + 0.6], "Q1": [7.0, -7.0, tz + 0.2], "Q2": [44.0, -5.0, None]}
    s["facets"] = {"W": {"poly": ["P1", "P2", "P3", "P4"], "plane": "warp"},
                   "S": {"poly": ["Q1", "Q2", "P2", "P1"], "plane": "nodes", "soft": True}}
    return s


def frame_px(info: dict):
    f = info["frame"]
    return lambda d, y: (f["ml"] + (d - f["lo_d"]) * f["sc"], f["mt"] + (f["y_hi"] - y) * f["sc"])


def facet_mask(tb, info: dict, label: str, size) -> np.ndarray:
    """The facet's cells rasterised in the chart's final pixels."""
    px = frame_px(info)
    m = Image.new("L", size, 0)
    md = ImageDraw.Draw(m)
    for fi, face in enumerate(tb.arr.faces):
        if tb.face_label[fi] == label:
            md.polygon([px(*tb.arr.V[v]) for v in face], fill=255)
    return np.asarray(m) > 0


def overlaps(b, q) -> bool:
    return b[0] < q[2] and q[0] < b[2] and b[1] < q[3] and q[1] < b[3]


def test_chart_names_every_facet_inside_it(viz):
    """WP6_V3 §6 "chart image (facets labelled)": the chart named no facet (v3 session2), so Astra matched the
    receipt's names to shapes by their nodes. Each facet on the chart is named once; with room, the whole name (its
    white halo aside) lies on the facet's own cells; at a narrow width the name's centre still does. No name, node
    label or node dot covers another (session2: L0's label ran over L's dot 1.4 mm away; here A2 is 1.4 mm from A)."""
    close = FX.straight(nodes={"A": [20.0, -5.0, 5.5], "A2": [21.4, -5.0, 5.5], "B": [50.0, -5.0, 5.5],
                               "C": [50.0, 5.0, 5.5], "D": [20.0, 5.0, 5.5]},
                        facets={"S": {"poly": ["A", "A2", "B", "C", "D"]}})
    cases = [("prada_default", FX.prada_default(), FOOTPRINTS, 1200, True),
             ("prada_default_700", FX.prada_default(), FOOTPRINTS, 700, False),
             ("warp_soft", warp_soft(), None, 1024, True), ("close_nodes", close, None, 1200, True)]
    for name, spec, fps, width, room in cases:
        tb = build_temple(spec)
        img = DR.chart_image(spec, tb, footprints=fps, width=width)
        img.save(viz / f"chart_names_{name}.png")
        info = img.info["g3d_chart"]
        assert set(info["facets"]) == set(spec["facets"])
        a = np.asarray(img.convert("RGB")).astype(int)
        for label, (x0, y0, x1, y1) in info["facets"].items():
            mask = facet_mask(tb, info, label, img.size)
            inner = [(x0 + 2, y0 + 2), (x1 - 2, y0 + 2), (x0 + 2, y1 - 2), (x1 - 2, y1 - 2)] if room else []
            for x, y in inner + [(0.5 * (x0 + x1), 0.5 * (y0 + y1))]:
                assert mask[int(y), int(x)], (name, label, x, y)
            box = a[int(y0):int(math.ceil(y1)), int(x0):int(math.ceil(x1))]
            assert colour_count_arr(box, DR.FACET_TEXT, 40) > 8            # the name is drawn there
        boxes = ([("facet " + k, b) for k, b in info["facets"].items()] + [("node " + k, b) for k, b in
                 info["nodes"].items()] + [("dot " + k, b) for k, b in info["dots"].items()])
        for i, (n1, b1) in enumerate(boxes):
            for n2, b2 in boxes[i + 1:]:                              # two dots may share a place (A, A_l)
                assert n1.startswith("dot") and n2.startswith("dot") or not overlaps(b1, b2), (name, n1, n2)
        if name == "close_nodes":                                    # A's label left its line: A2's dot is there
            assert info["nodes"]["A"][3] < info["dots"]["A2"][1]


def colour_count_arr(a: np.ndarray, rgb, tol: int) -> int:
    return int(np.all(np.abs(a - np.array(rgb)) <= tol, axis=-1).sum())


def test_chart_fills_every_facet_alike_without_lean(viz):
    """No lean shading (review 2026-10-12: the lean has no visual meaning for Astra and is gone from its text): F1-F4
    of prada_default lean 20.9, 6.4, 7.1 and 5.1 deg against the base and are all filled FACET (v2 shaded them from
    near the base's colour to dark slate); the legend has no lean row, and names "facet"."""
    spec = FX.prada_default()
    tb = build_temple(spec)
    lean = [f["lean_deg"] for f in tb.tables["facets"]]
    assert max(lean) - min(lean) > 10.0                               # v2 would have shaded them apart
    img = DR.chart_image(spec, tb)
    img.save(viz / "chart_no_lean.png")
    info = img.info["g3d_chart"]
    a = np.asarray(img.convert("RGB")).astype(int)
    for label in spec["facets"]:
        px = a[facet_mask(tb, info, label, img.size)]
        cols, counts = np.unique(px, axis=0, return_counts=True)
        assert np.abs(cols[np.argmax(counts)] - np.array(DR.FACET)).max() <= 2, label
    labels = [lab for lab, _ in info["legend"]]
    assert "facet" in labels and not any("lean" in lab for lab in labels)
    assert not hasattr(DR, "_lean_colour") and not hasattr(DR, "LEAN_HI")


def test_chart_legend_fits_the_image(viz):
    """The legend wraps to the image width (v3 session2: at the lab's 1024 px it ran past the right edge and cut
    "decal" and the hinge entry). Items by measured width, one more row when the next would pass the edge; the image
    grows by the rows; marks the chart lacks are not listed (warp, soft border, decal, hinge part); the title shrinks
    to fit a narrow chart."""
    plain = DR.chart_image(None, build_temple(FX.prada_default()), width=1200)
    assert [lab for lab, _ in plain.info["g3d_chart"]["legend"]] == [
        "ridge", "valley", "flat < 2°", "facet", "base", "border ramp", "thickness contour", "node"]
    tb = build_temple(warp_soft())
    every = ["ridge", "valley", "flat < 2°", "soft border", "facet", "warp", "base", "border ramp",
             "thickness contour", "decal", "hinge part", "node"]
    rows = {}
    for width in (1200, 1024, 700, 480):
        img = DR.chart_image(None, tb, footprints=FOOTPRINTS, width=width)
        img.save(viz / f"chart_legend_{width}.png")
        legend = img.info["g3d_chart"]["legend"]
        assert [lab for lab, _ in legend] == every
        for lab, (x0, y0, x1, y1) in legend:
            assert 0 <= x0 and x1 <= width - 12 and 0 <= y0 and y1 <= img.size[1], (width, lab)
        a = np.asarray(img.convert("RGB")).astype(int)
        assert colour_count_arr(a[int(legend[-1][1][1]):int(legend[-1][1][3]), int(legend[-1][1][0]):], DR.NODE,
                                20) > 20                               # the last item is drawn, not cut
        ys = sorted({b[1] for _, b in legend})
        rows[width] = len(ys)
        assert all(b2 - b1 == pytest.approx(DR.LEGEND_ROW_PX) for b1, b2 in zip(ys, ys[1:]))
        right = np.asarray(img.convert("L"))[:40, width - 6:]          # the title stays inside the image
        assert right.min() > 200
    assert rows[1200] <= rows[1024] <= rows[700] <= rows[480] and rows[480] > rows[1200]


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
