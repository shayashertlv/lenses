"""WP6 v3 seeing (WP6_V3 §5): model edges by identity, the zoom, side-view outline offsets, the rows actually stacked;
with the fixes of the 2026-10-12 seeing reviews (data/wp6v2/review_20261012/v3/review-seeing-*.md, v3-seeing-fix.md).

Unit (synthetic, always run):
* compare.edge_runs projects posed polylines exactly (the samples are project_mm of the 3D points), keeps the runs a
  plate in front hides out, hides an edge seen through the body, keeps a valley seen at a grazing angle (the exact
  ray, not the depth image's pixel centre), and carries a per-point value (a signed angle) along its runs;
* edge_label / run_label / edge_shape_of / draw_edges: "16°" / "-3°" / "24-33°" / "-9 to 4°" (compare.deg_text: the
  v3p3 review's "16..29" read as "16.29" at the size Astra gets a zoom), colours by shape, 2 px from 6 deg and
  1 px below, a run with per-sample angles drawn segment by segment, one label per pair at its longest run, labels
  inside the image and never overlapping; overlay_matched draws the edges INSTEAD of the mesh polylines;
* outline_offsets on synthetic masks: top / bottom photo minus model, None with its reason (covered, off temple, off
  image, no model edge, no photo edge);
* Seeing._side_outline on two synthetic box temples: only the temple whose outer face faces the camera is measured
  ("seen from inside" otherwise), the top is not measured in a view from above ("top face in view": the silhouette
  is the top face's far edge) and the bottom not in a view from below, both in a square-on view; the outline text,
  which prints that far edge (photo and model alike) minus its median (v3p3-verify-8: session2's 2.3-3.8 mm low
  rear arm top at d 110-120 printed nowhere): a step along the arm reads as one, fewer than FAR_EDGE_LEVEL_MIN
  stations as measured, other stations' reasons in the same list, a record without the silhouette "not measured";
* geometry_view, zoom_scale, the 2 x 2 zoom grid, labelled(fit=True); resample_box registers a box on the crop
  camera's exact grid (k up and down, at the image border) and never overshoots (LANCZOS does); backdrop_share;
* Seeing._check_v3_args (refusals, numpy reals, deg_at, a ridge with a negative deg), _check_edges_on_model (edges
  on the head pass; a wrong hinge_z_mm, an opening, no +X body refuse), _edges_posed (mirror, theta, opening,
  deg_at), _edge_records (native px, "charted" / "mirrored", deg_shown), _matched_text, _zoom_text (no "nan");
* WP6 v3 phase 2 (I1 / I7): a soft facet|base border is drawn dashed 1 px grey under the other edges, whatever its
  shape and angles, and never labelled (compare.dashed_line, draw_edges, edges_with_runs); labels read the build's
  own deg_at, else its range, else its deg (run_label); _check_v3_args takes range and soft (deg and deg_at inside
  the edge's own range: the roll-band mix-up "36.9 (37.7-39.3)" is refused; soft only on a facet|base pair; the
  sha holds them only when given), the records mark soft and the texts list soft borders apart from the creases.
* The 2026-10-12 review of that (review2-seeing2-1): the texts say what the drawing means (a soft border is drawn
  without an angle and its fold is listed with the build's creases, not "no crease"; a-b° is over the drawn run the
  label sits on); lines go on whole pixels (compare.line_px: 1 px on the nearest, so a soft dash at v 30.6 is on row
  31, not 30); I1's soft means smooth-shaded in the build: a soft facet's border run built sharp (the builder's own
  steep and straddling fixtures) goes out as an ordinary, labelled crease, and no soft run has a sharp edge.

Integration (needs Blender 5.2 and data/wp6v2/lab/package_007; skipped, or failed under G3D_REQUIRE_DATA=1): a COPY of
the run-7 package (cameras, checkpoints, inputs) at its head c0007 (revision 7); the model edges are revision 7's
built creases as WP6 v3 I1 gives them (the builder's model_edges(tb) when it has one, else this file's reading of I1
from the TempleBuild: outer polygons only, one entry per connected run, deg / range / deg_at from the run's own
edges); every zoom label lies inside its pair's range as given (no roll-band number), and a facet|base border marked
soft is drawn dashed grey with no label and listed as a soft border (plumbing: revision 7's shoulder|base is a hard
border, sharp in the build, marked soft in the edge set; I1's soft flag itself is tested without Blender).
* One matched compare at B1 (black-side) and 05 with model edges and two zooms (05 geometry, B1 render + edges):
  rows actually stacked (3 each, k from 3 rows), the edges drawn instead of the mesh creases, the render rows with
  the model's outline and their backdrop share, in one row order for every colour (photo, render, overlay: the
  stacked image's rows are the row files in that order), titled with ``photo_names`` (the lab's ids) when given and
  keyed by them; the far (mirrored) temple's edges hidden by its own body in 05 (seen
  from inside) and off the image in B1; logo|lower and crown|logo on their photo lines (the investigation's traced
  lines KC and AK, w2/crown-geometry/picks.json) within 2 and 4 px; side outline: the charted temple faces the
  camera, its top is not measured (the views look 14-15 deg down) and its top face's far edge is printed minus its
  median, its bottom within 0.5 mm at d 10-70, the mirrored temple seen from inside; zoom sizes, panels, labels (a
  deg_at ramp labels "a-b°"), the zoom's stretch = its photo's
  compare stretch (also in a zoom-only call); the cache (the opening is in the key without model edges too) and the
  old mesh rule without model edges.
* The instrument on the model itself (review F1): rendered at 05's and 01's own cameras at k 1, composited as the
  compare row and matted with the photos' own rule (glossy_floor), the side outline reads every measured station
  within 1 native px; the top silhouette reads <= -1 mm (the white top face lost to the matte) and, printed minus
  its median, stays within 1 mm at every station: a printed step of 2-4 mm is the photo's, not the matte's.
* Another part hides edges on the real head: at 02 the front and the left lens are the first hit for most of the
  mirrored temple's edge vertices (no photo can show one temple hiding the other's outer-face edges).
* Refusals journaled: model edges without hinge_z_mm, with a wrong hinge_z_mm or an opening the head does not have,
  bad zoom boxes, photo_names naming a photo the basis lacks, photo_v1 (with model edges or photo_names).
The images go under the test's tmp folder; G3D_SEEING_IMAGES=<dir> also copies them there for a review."""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import stat
from collections import defaultdict
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from glasses3d.agent.seeing import FAR_EDGE_LEVEL_MIN, SIDE_FACE_PX, SOFT_TEXT, Seeing, SeeingError
from glasses3d.g3d_host import compare as C
from glasses3d.g3d_host import fitmesh
from glasses3d.g3d_host import pose as PO
from glasses3d.g3d_host import raster
from glasses3d.g3d_host.camera import NormFrame, project_mm

G3D = Path(__file__).resolve().parents[1]
PACKAGE = G3D / "data" / "wp6v2" / "lab" / "package_007"
IMAGES_OUT = os.environ.get("G3D_SEEING_IMAGES")
REQUIRE_DATA = os.environ.get("G3D_REQUIRE_DATA") == "1"


def need(ok: bool, why: str) -> None:
    if not ok:
        (pytest.fail if REQUIRE_DATA else pytest.skip)(why)


# ------------------------------------------------------------------------------------------------ edge_runs
def _tent(z):
    V = np.array([[0, 0, 0], [0, 10, 0], [5, 0, z], [5, 10, z], [10, 0, 0], [10, 10, 0]], float)
    F = np.array([[0, 2, 3], [0, 3, 1], [2, 4, 5], [2, 5, 3]])
    return V, F


FRAME = NormFrame((5.0, 5.0, 0.0), 20.0)


def test_edge_runs_project_exactly_and_hide_behind_a_plate():
    V, F = _tent(2.0)
    cam = raster.view_camera(FRAME, 0.0, 0.0, 10.0, (160, 160))               # from +Z: the tent faces it
    ridge = np.array([[5.0, 0.0, 2.0], [5.0, 4.0, 2.0], [5.0, 10.0, 2.0]])
    scene = raster.RasterScene(V, F, FRAME)
    runs = C.edge_runs([ridge], cam, FRAME, scene, (160, 160))
    assert len(runs) == 1 and len(runs[0]) == 1
    run = runs[0][0]
    assert run.shape[1] == 2                                                      # no values: (M, 2) as before
    np.testing.assert_allclose(run[0], project_mm(ridge[:1], cam, FRAME)[0], atol=1e-9)
    np.testing.assert_allclose(run[-1], project_mm(ridge[-1:], cam, FRAME)[0], atol=1e-9)
    assert float(np.linalg.norm(run[-1] - run[0])) == pytest.approx(100.0, abs=0.5)     # 10 mm at 10 px/mm
    assert np.linalg.norm(np.diff(run, axis=0), axis=1).max() <= C.EDGE_STEP_PX + 1e-9
    occ = np.array([[-1, -1, 5], [11, -1, 5], [11, 5, 5], [-1, 5, 5]], float)   # a plate in front over y < 5
    VO, FO = np.vstack([V, occ]), np.vstack([F, np.array([[6, 7, 8], [6, 8, 9]])])
    half = C.edge_runs([ridge], cam, FRAME, raster.RasterScene(VO, FO, FRAME), (160, 160))[0]
    seen = sum(float(np.linalg.norm(np.diff(r, axis=0), axis=1).sum()) for r in half)
    assert seen == pytest.approx(50.0, abs=2.0)
    back = raster.view_camera(FRAME, 180.0, 0.0, 10.0, (160, 160))            # from -Z the ridge is behind the
    floor = raster.RasterScene(np.vstack([V, [[0, 0, -3], [0, 10, -3], [10, 0, -3], [10, 10, -3]]]),   # body's
                               np.vstack([F, [[6, 8, 9], [6, 9, 7]]]), FRAME)                          # floor
    assert C.edge_runs([ridge], back, FRAME, floor, (160, 160)) == [[]]
    outside = C.edge_runs([ridge + [40.0, 0.0, 0.0]], cam, FRAME, scene, (160, 160))
    assert outside == [[]]                                                        # off the image: nothing


def test_edge_runs_carry_a_value_per_point():
    """values: the signed angle per point rides along every run, linear along each segment, exact at the points;
    a polyline without values gets nan; the plate still cuts the run where it did."""
    V, F = _tent(2.0)
    cam = raster.view_camera(FRAME, 0.0, 0.0, 10.0, (160, 160))
    ridge = np.array([[5.0, 0.0, 2.0], [5.0, 4.0, 2.0], [5.0, 10.0, 2.0]])
    scene = raster.RasterScene(V, F, FRAME)
    runs = C.edge_runs([ridge, ridge], cam, FRAME, scene, (160, 160), values=[[24.0, 28.0, 33.0], None])
    run = runs[0][0]
    assert run.shape[1] == 3 and run[0, 2] == 24.0 and run[-1, 2] == 33.0
    y = (run[:, 1] - run[0, 1]) / (run[-1, 1] - run[0, 1]) * 10.0               # mm along the ridge (10 px/mm)
    want = np.where(y <= 4.0, 24.0 + y, 28.0 + (y - 4.0) * 5.0 / 6.0)
    np.testing.assert_allclose(run[:, 2], want, atol=0.02)
    assert np.isnan(runs[1][0][:, 2]).all()
    np.testing.assert_allclose(run[:, :2], C.edge_runs([ridge], cam, FRAME, scene, (160, 160))[0][0])
    occ = np.array([[-1, -1, 5], [11, -1, 5], [11, 5, 5], [-1, 5, 5]], float)
    VO, FO = np.vstack([V, occ]), np.vstack([F, np.array([[6, 7, 8], [6, 8, 9]])])
    half = C.edge_runs([ridge], cam, FRAME, raster.RasterScene(VO, FO, FRAME), (160, 160), values=[[24, 28, 33]])[0]
    assert len(half) == 1 and half[0][:, 2].min() >= 28.0 + 1.0 * 5 / 6 - 0.1    # only y >= 5 mm shows


def test_edge_runs_keep_a_grazing_valley():
    """A closed block whose top is a 2 mm deep valley, seen 64 deg off the vertical at 3 px/mm: the far face is at a
    grazing 86 deg (still front-facing, so the valley shows), where the depth image's pixel centre sits > 0.4 mm in
    front of the valley. The mesh rule (crease_polylines on the depth image) hides all of it; the exact ray keeps
    every sample."""
    V, F = _tent(-2.0)
    V = np.vstack([V, [[0, 0, -6], [0, 10, -6], [10, 0, -6], [10, 10, -6]]])
    F = np.vstack([F, [[6, 8, 9], [6, 9, 7], [0, 1, 7], [0, 7, 6], [4, 8, 9], [4, 9, 5], [0, 6, 8], [0, 8, 4],
                       [1, 5, 9], [1, 9, 7], [2, 0, 4], [3, 5, 1]]])
    frame = NormFrame((5.0, 5.0, -3.0), 20.0)
    cam = raster.view_camera(frame, 64.0, 0.0, 3.0, (120, 120))
    valley = np.array([[5.0, 0.5, -2.0], [5.0, 9.5, -2.0]])
    runs = C.edge_runs([valley], cam, frame, raster.RasterScene(V, F, frame), (120, 120))[0]
    assert len(runs) == 1
    uv = project_mm(valley, cam, frame)
    assert float(np.linalg.norm(np.diff(runs[0], axis=0), axis=1).sum()) == pytest.approx(
        float(np.linalg.norm(uv[1] - uv[0])), abs=1e-6)
    depth = raster.render(V, F, cam, frame, (120, 120))["depth"]
    assert C.crease_polylines(C.crease_edges(V[:6], F[:4]), cam, frame, depth) == []      # the old rule hides it


# ------------------------------------------------------------------------------------------------ drawing
def test_edge_labels_and_drawing():
    assert C.edge_label(16.2, "ridge") == "16°" and C.edge_label(3.4, "valley") == "-3°"
    assert C.edge_label(-3.4, "valley") == "-3°" and C.edge_label(1.7, "flat") == "2°"
    assert C.edge_label(-0.4, "flat") == "0°" and C.edge_label(-1.6, "flat") == "-2°"
    im = Image.new("RGB", (200, 120), (255, 255, 255))
    edges = [{"pair": "crown|logo", "shape": "ridge", "deg": 10.8,
              "runs": [np.array([[10.0, 20.0], [190.0, 20.0]]), np.array([[10.0, 40.0], [60.0, 40.0]])]},
             {"pair": "crown|logo", "shape": "ridge", "deg": 10.8, "runs": [np.array([[10.0, 60.0], [40.0, 60.0]])]},
             {"pair": "logo|lower", "shape": "flat", "deg": 2.6, "runs": [np.array([[10.0, 80.0], [190.0, 80.0]])]},
             {"pair": "lower|shoulder", "shape": "valley", "deg": 7.7,
              "runs": [np.array([[100.0, 0.0], [100.0, 119.0]])]}]
    labels = C.draw_edges(im, edges, labels=True)
    a = np.asarray(im)
    assert len(np.flatnonzero((a[:, 150] == C.RIDGE_RGB).all(axis=1))) == 2    # 2 px from 6 deg
    assert np.flatnonzero((a[:, 150] == C.FLAT_RGB).all(axis=1)).tolist() == [80]   # 1 px below 6 deg
    assert len(np.flatnonzero((a[100] == C.VALLEY_RGB).all(axis=1))) == 2
    assert sorted(p for p, _t, _c in labels) == ["crown|logo", "logo|lower", "lower|shoulder"]   # once per pair
    assert {p: t for p, t, _c in labels} == {"crown|logo": "11°", "logo|lower": "3°", "lower|shoulder": "-8°"}
    crown = next(c for p, _t, c in labels if p == "crown|logo")
    assert abs(crown[1] - 20.0) < 20 and 60 < crown[0] < 140                   # at the longest run's middle
    boxes = []
    for _p, t, (u, v) in labels:
        assert 0 <= u <= 200 and 0 <= v <= 120
        boxes.append((u, v, len(t)))
    for i, (u1, v1, n1) in enumerate(boxes):                                    # no two labels overlap
        for u2, v2, n2 in boxes[i + 1:]:
            assert abs(u1 - u2) >= 4 * min(n1, n2) or abs(v1 - v2) >= 8
    base = np.full((60, 60, 3), 128, np.uint8)
    mask = np.zeros((60, 60), bool)
    mask[10:50, 10:50] = True
    poly = [(np.array([[5.0, 30.0], [55.0, 30.0]]), 1)]
    ov = np.asarray(C.overlay_matched(base, mask, poly, None, edges=[]))
    assert not (ov == C.RIDGE_RGB).all(axis=2).any()                           # edges replace the mesh lines
    ov = np.asarray(C.overlay_matched(base, mask, poly, None))
    assert (ov == C.RIDGE_RGB).all(axis=2).any()                                # without edges: the old rule
    ov = np.asarray(C.overlay_matched(base, None, [], None))
    assert not (ov == C.OUTLINE_RGB).all(axis=2).any()                          # no outline when no mask
    cut = np.zeros((60, 60), bool)
    cut[20:, 10:50] = True                                                       # the model runs off the bottom
    ov = (np.asarray(C.overlay_matched(base, cut, [], None)) == C.OUTLINE_RGB).all(axis=2)
    assert ov[59, 20:40].all()                                                   # the image border drawn as outline
    ov = (np.asarray(C.overlay_matched(base, cut, [], None, open_border=True)) == C.OUTLINE_RGB).all(axis=2)
    assert not ov[59, 11:49].any() and ov[20, 20:40].all() and ov[30:, 10].all()   # a zoom: only the real outline


def test_per_segment_angles_and_range_labels():
    """A warp face's crease (review F: one angle and one colour per pair hid 24 -> 33 deg along crown|logo): with a
    signed angle per sample each segment takes its own shape and width, and the label reads "min..max" when the
    visible run spans >= 2 deg; a sign change along a crease shows as ridge, flat and valley segments."""
    from glasses3d.g3d.temple import spec as SP
    assert C.EDGE_FLAT_DEG == SP.FLAT_CREASE_DEG                                 # the builder's flat class
    assert C.edge_shape_of([2.0, 1.99, -1.99, -2.0, 25.0]).tolist() == ["ridge", "flat", "flat", "valley", "ridge"]
    u = np.linspace(10.0, 190.0, 181)
    warp = np.column_stack([u, np.full_like(u, 20.0), np.linspace(24.4, 33.0, 181)])
    flip = np.column_stack([u, np.full_like(u, 60.0), np.linspace(4.0, -9.0, 181)])     # +4 -> -9 deg
    level = np.column_stack([u, np.full_like(u, 100.0), np.linspace(24.6, 25.4, 181)])
    assert C.run_label(warp, 25.0, "ridge") == ("24-33°", "ridge")
    assert C.run_label(level, 0.0, "flat") == ("25°", "ridge")                  # spread < 2: the run's mean
    assert C.run_label(warp[:, :2], 25.16, "ridge") == ("25°", "ridge")          # no values: the edge's own deg
    assert C.run_label(flip, 3.0, "valley") == ("-9 to 4°", "valley")            # min to max, shaped by the mean
    im = Image.new("RGB", (200, 120), (255, 255, 255))
    labels = C.draw_edges(im, [{"pair": "crown|logo", "shape": "ridge", "deg": 25.16, "runs": [warp]},
                               {"pair": "logo|nose", "shape": "valley", "deg": 2.0, "runs": [flip]}], labels=True)
    a = np.asarray(im)

    def col(x, rgb, rows=slice(50, 70)):
        """The rows of colour rgb in column x within ``rows`` (the flip run at v 60)."""
        return (np.flatnonzero((a[rows, x] == rgb).all(axis=1)) + rows.start).tolist()
    assert len(col(100, C.RIDGE_RGB, slice(10, 30))) == 2                       # 24-33 deg: orange 2 px all along
    assert col(20, C.RIDGE_RGB) == [60]                                          # +3.3 deg: orange 1 px
    assert col(80, C.FLAT_RGB) == [60]                                           # -1.1 deg: grey 1 px
    assert col(130, C.VALLEY_RGB) == [60]                                        # -4.7 deg: blue 1 px
    assert len(col(180, C.VALLEY_RGB)) == 2                                      # -8.3 deg: blue 2 px
    assert {p: t for p, t, _c in labels} == {"crown|logo": "24-33°", "logo|nose": "-9 to 4°"}


def test_soft_borders_are_dashed_thin_grey_and_unlabelled():
    """WP6 v3 I7: a soft facet|base border (smooth-shaded: a tone change, no crease line) is drawn dashed, 1 px, grey,
    whatever its shape and per-point angles, under the other edges, and never labelled with an angle (probe1 drew a
    soft shoulder|base as a blue valley labelled "-5..1"); the crease crossing it keeps its colour, width and label;
    overlay_matched draws it so too; edges_with_runs carries range and soft (only when true) to draw_edges."""
    im = Image.new("RGB", (200, 120), (255, 255, 255))
    u = np.linspace(10.0, 190.0, 181)
    soft = np.column_stack([u, np.full_like(u, 60.0), np.linspace(-5.3, 1.0, 181)])     # deg_at -5.3 -> +1.0
    ridge = np.column_stack([np.full(101, 150.0), np.linspace(10.0, 110.0, 101)])       # crosses it at (150, 60)
    edges = [{"pair": "crown|shoulder", "shape": "ridge", "deg": 37.0, "runs": [ridge]},
             {"pair": "shoulder|base", "shape": "valley", "deg": -4.5, "range": [-5.3, 1.0], "soft": True,
              "runs": [soft]}]
    labels = C.draw_edges(im, edges, labels=True)
    a = np.asarray(im)
    assert [(p, t) for p, t, _c in labels] == [("crown|shoulder", "37°")]              # the soft one: no label
    lit = (a[60] == C.SOFT_RGB).all(axis=1)
    assert not (a == C.VALLEY_RGB).all(axis=2).any() and not (a == C.RIDGE_RGB).all(axis=2)[:, :120].any()
    assert not (a[59] == C.SOFT_RGB).all(axis=1).any() and not (a[61] == C.SOFT_RGB).all(axis=1).any()   # 1 px
    assert lit[10:15].all() and not lit[15:17].any() and lit[17:22].all()                # 4 px on, 3 off from u 10
    seg = lit[10:95]
    assert 0.6 < seg.mean() < 0.8 and int(np.sum(seg[1:] & ~seg[:-1])) >= 10             # dashed all along
    assert (a[60, 145:156] == C.RIDGE_RGB).all(axis=1).sum() == 2                       # the crease drawn over it
    assert C.SOFT_RGB == C.FLAT_RGB                                                      # grey
    base = np.full((120, 200, 3), 255, np.uint8)
    ov = np.asarray(C.overlay_matched(base, None, [], None, edges=edges, labels=True))
    assert ((ov[60] == C.SOFT_RGB).all(axis=1) == lit).all()
    pts = np.zeros((3, 3))
    both = C.edges_with_runs([{"pair": "a|base", "shape": "flat", "deg": 1.0, "side": -1, "range": (0.5, 1.5),
                               "soft": True, "P": pts, "deg_at": [1, 1, 1]},
                              {"pair": "a|b", "shape": "ridge", "deg": 5.0, "soft": False, "range": None}],
                             [[soft], []])
    assert both[0] == {"pair": "a|base", "shape": "flat", "deg": 1.0, "side": -1, "runs": [soft],
                       "range": (0.5, 1.5), "soft": True}
    assert both[1] == {"pair": "a|b", "shape": "ridge", "deg": 5.0, "side": None, "runs": []}
    mute = Image.new("RGB", (40, 20), (255, 255, 255))
    C.dashed_line(ImageDraw.Draw(mute), np.array([[5.0, 10.0]]), C.SOFT_RGB)            # one point: nothing
    C.dashed_line(ImageDraw.Draw(mute), np.array([[5.0, 10.0], [7.0, 10.0]]), C.SOFT_RGB)   # shorter than a dash
    m = (np.asarray(mute) == C.SOFT_RGB).all(axis=2)
    assert m.sum() == 3 and m[10, 5:8].all()


def test_lines_go_on_the_nearest_pixel():
    """review2-seeing2-1 P3-3: Pillow (12.3) truncates float points toward zero, so a 1 px line at v 30.6 went on row
    30 (nearest: 31; 0.5 px up-left on average, every soft dash among them) and a v in (-1, 0) on row 0. With pixel
    centres at integers (the compare's map), compare.line_px puts a 1 px line on the nearest pixel and a 2 px line on
    floor(v) and floor(v) + 1 (centred within 0.5 px), for solid, per-segment and dashed lines alike; the dashes keep
    their count."""
    assert C.line_px([[10.4, 30.6], [-0.7, -0.4]], 1) == [(10, 31), (-1, 0)]
    assert C.line_px([[10.4, 30.6], [-0.7, -0.4]], 2) == [(10, 30), (-1, -1)]
    assert C.line_px(np.array([[1.6, 2.5, 33.0]]), 1) == [(2, 3)] and C.line_px([], 1) == []    # a deg_at column

    def rows(edges, col=100, size=(200, 60)):
        im = Image.new("RGB", size, (255, 255, 255))
        C.draw_edges(im, edges)
        return np.flatnonzero((np.asarray(im)[:, col] != 255).any(axis=1)).tolist()
    u = np.linspace(10.0, 190.0, 181)
    for v, one, two in ((30.6, [31], [30, 31]), (30.4, [30], [30, 31]), (30.0, [30], [30, 31]),
                        (-0.7, [], [0]), (0.6, [1], [0, 1])):
        line = np.column_stack([u, np.full_like(u, v)])
        assert rows([{"pair": "a|b", "shape": "flat", "deg": 1.0, "runs": [line]}]) == one, v            # solid 1 px
        assert rows([{"pair": "a|b", "shape": "ridge", "deg": 9.0, "runs": [line]}]) == two, v           # 2 px
        segs = np.column_stack([line, np.full_like(u, 3.0)])                                              # deg_at 3
        assert rows([{"pair": "a|b", "shape": "ridge", "deg": 3.0, "runs": [segs]}]) == one, v
        soft = [{"pair": "a|base", "shape": "flat", "deg": 1.0, "soft": True, "runs": [line]}]
        assert rows(soft, col=101) == one, v                                                              # a dash
    im = Image.new("RGB", (200, 60), (255, 255, 255))
    C.draw_edges(im, [{"pair": "a|base", "shape": "flat", "deg": 1.0, "soft": True,
                       "runs": [np.column_stack([u + 0.6, np.full_like(u, 30.6)])]}])
    a = (np.asarray(im) == C.SOFT_RGB).all(axis=2)
    lit = a[31]
    assert a.sum() == lit.sum() and int(np.sum(lit[1:] & ~lit[:-1])) + int(lit[0]) == math.ceil(180.0 / 7.0)
    assert lit[11:16].all() and not lit[16:18].any() and lit[18:23].all()       # dash 10.6-14.6 -> 11-15, gap 16-17


def test_labels_read_the_builds_deg_at_then_range_then_deg():
    """Labels show the build's own angles as given (WP6 v3 I1 / I7), never one seeing measures: per-sample deg_at
    first (the visible run), else the edge's range [lo, hi] (I1's signed form, its whole length) when it spans >= 2
    deg, else its deg; with a range the deg is signed, so the label's sign and colour follow deg, not the crease's
    vote (a warp crease that changes sign)."""
    xy = np.column_stack([np.linspace(10.0, 100.0, 91), np.full(91, 20.0)])
    vals = np.column_stack([xy, np.linspace(24.4, 33.0, 91)])
    assert C.run_label(xy, 17.1, "ridge", (15.7, 29.2)) == ("16-29°", "ridge")         # a warp crease, no deg_at
    assert C.run_label(xy, 38.4, "ridge", (37.7, 39.3)) == ("38°", "ridge")            # spans < 2: deg
    assert C.run_label(xy, -2.2, "ridge", (-10.0, 3.0)) == ("-10 to 3°", "valley")     # signed deg, not the vote
    assert C.run_label(xy, 1.4, "valley", (1.2, 1.8)) == ("1°", "flat")
    assert C.run_label(vals, 25.0, "ridge", (0.0, 50.0)) == ("24-33°", "ridge")        # deg_at wins
    assert C.run_label(xy, 3.4, "valley") == ("-3°", "valley")                          # legacy: unsigned + shape
    im = Image.new("RGB", (120, 60), (255, 255, 255))
    labels = C.draw_edges(im, [{"pair": "crown|logo", "shape": "ridge", "deg": 17.1, "range": [15.7, 29.2],
                                "runs": [xy]}], labels=True)
    assert [t for _p, t, _c in labels] == ["16-29°"]


def test_label_notation_is_one_and_never_reads_as_a_decimal():
    """v3p3 review (P3, checked: compare.run_label printed "16..29" and "17..29"; probes3/astra/zoom_label_b2.png:
    the two dots ~2 px apart in a 1218 px image read as "16.29"; the receipt prints "(15.7-29.2)" for the same
    crease): one notation, deg_text, everywhere a label is drawn: "a°", "a-b°" (lo >= 0), "a to b°" (lo < 0: a hyphen
    between signed numbers reads as a minus). No label holds "..", and every label's numbers parse back (the
    integration's _label_values)."""
    assert C.deg_text(16.4) == "16°" and C.deg_text(-3.4) == "-3°" and C.deg_text(-0.4) == "0°"
    assert C.deg_text(15.7, 29.2) == "16-29°" and C.deg_text(0.0, 4.0) == "0-4°"
    assert C.deg_text(-8.2, -3.1) == "-8 to -3°" and C.deg_text(-9.0, 4.0) == "-9 to 4°"
    u = np.linspace(10.0, 190.0, 181)
    cases = [(np.column_stack([u, np.full_like(u, 20.0), np.linspace(a, b, 181)]), a, b)
             for a, b in ((15.7, 29.2), (-8.2, -3.1), (-9.0, 4.0), (24.6, 25.4))]
    for run, a, b in cases:
        text, _shape = C.run_label(run, (a + b) / 2, "ridge")
        assert ".." not in text and text.endswith("°")
        assert _label_values(text) == ([round(a), round(b)] if b - a >= C.EDGE_RANGE_DEG else [round((a + b) / 2)])
    im = Image.new("RGB", (200, 60), (255, 255, 255))
    labels = C.draw_edges(im, [{"pair": "crown|logo", "shape": "ridge", "deg": 17.1, "range": [15.7, 29.2],
                                "runs": [cases[0][0][:, :2]]}], labels=True)
    assert [t for _p, t, _c in labels] == ["16-29°"]
    from glasses3d.agent.seeing import EDGE_LABEL_TEXT
    assert "a-b°" in EDGE_LABEL_TEXT and "a to b°" in EDGE_LABEL_TEXT and ".." not in EDGE_LABEL_TEXT


# ------------------------------------------------------------------------------------------------ outline offsets
def test_outline_offsets_top_bottom_and_reasons():
    H, W = 100, 200
    temple = np.zeros((H, W), bool)
    temple[40:60, 20:180] = True                                                 # rows 40..59
    model = temple.copy()
    model[20:40, 20:50] = True                                                   # the frame above it at u < 50
    model[50:60, 185:195] = True                                                 # a part beside it, off the temple
    matte = np.zeros((H, W), bool)
    matte[36:62, 10:190] = True                                                  # photo: 4 px higher, 2 px lower
    ppm = 2.0

    def st(u, v, up):
        return {"d_mm": float(u), "uv": [u, v], "dir": (0.0, -1.0 if up else 1.0), "px_per_mm": ppm}
    top = C.outline_offsets([st(100, 50, True), st(30, 50, True), st(100, 80, True), st(190, 55, True),
                             st(100, -5, True)], model, temple, matte)
    assert top[0] == {"d_mm": 100.0, "offset_mm": pytest.approx(4 / ppm), "reason": None}
    assert [r["reason"] for r in top[1:]] == ["covered", "off temple", "covered", "off image"]
    assert all(r["offset_mm"] is None for r in top[1:])
    bot = C.outline_offsets([st(100, 50, False), st(30, 50, False)], model, temple, matte)
    assert bot[0]["offset_mm"] == pytest.approx(2 / ppm) and bot[1]["offset_mm"] == pytest.approx(2 / ppm)
    thin = np.zeros((H, W), bool)
    thin[45:55, 20:180] = True                                                   # the photo's temple is thinner
    assert C.outline_offsets([st(100, 50, True)], model, temple, thin)[0]["offset_mm"] == pytest.approx(-5 / ppm)
    edge = np.zeros((H, W), bool)
    edge[0:30, 20:180] = True                                                    # the temple runs off the image top
    assert C.outline_offsets([st(100, 10, True)], edge, edge, edge)[0]["reason"] == "off image"
    far = np.zeros((H, W), bool)
    far[10:20, 20:180] = True                                                    # the photo edge beyond the reach
    assert C.outline_offsets([st(100, 50, True)], model, temple, far)[0]["reason"] == "no photo edge"
    tall = np.ones((H, W), bool)                                                 # the temple never ends in the walk
    r = C.outline_offsets([{**st(100, 99, True), "px_per_mm": 1.0}], tall, tall, tall, walk_mm=120, reach_mm=6)
    assert r[0]["reason"] == "off image"                                         # the walk leaves the image first
    r = C.outline_offsets([{**st(100, 99, True), "px_per_mm": 1.0}], tall, tall, tall, walk_mm=20, reach_mm=6)
    assert r[0]["reason"] == "no model edge"
    assert set(C.OUTLINE_REASONS) == {"off image", "covered", "off temple", "no model edge", "no photo edge"}


# ------------------------------------------------------------------------------------------------ side outline
HZ = -10.0                                     # the synthetic temples' hinge z: d = HZ - z, d 0 .. 120
SIDE_FRAME = NormFrame((0.0, 0.0, -70.0), 150.0)


def _bar(x0, x1, y0=-3.0, y1=3.0, z0=-10.0, z1=-130.0, step=2.0):
    """A closed box temple x0..x1 / y0..y1 / z0..z1 (z1 < z0) with a ring of 4 vertices every ``step`` mm of z (so
    every 10 mm station finds vertices within 1 mm of its d)."""
    zs = np.arange(z0, z1 - 1e-9, -step)
    ring = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    V = np.array([(x, y, z) for z in zs for x, y in ring], float)
    F = []
    for i in range(len(zs) - 1):
        for j in range(4):
            a, b, c, d = 4 * i + j, 4 * i + (j + 1) % 4, 4 * (i + 1) + (j + 1) % 4, 4 * (i + 1) + j
            F += [[a, b, c], [a, c, d]]
    n = 4 * (len(zs) - 1)
    F += [[0, 1, 2], [0, 2, 3], [n, n + 2, n + 1], [n, n + 3, n + 2]]
    return V, np.array(F)


def _side_scene(inner: float = 60.0):
    """Two box temples (x 60..66 the +X one, its inner face at ``inner``; its mirror) as a FitMesh with temple
    roles."""
    Vr, Fr = _bar(inner, 66.0)
    Vl, Fl = _bar(-66.0, -60.0)
    V = np.vstack([Vr, Vl])
    F = np.vstack([Fr, Fl + len(Vr)])
    part = np.r_[np.zeros(len(Fr), np.int64), np.ones(len(Fl), np.int64)]
    mesh = fitmesh.FitMesh(V, F, part, ["temple_r", "temple_l"], ["temple_r", "temple_l"], [None, None], SIDE_FRAME)
    return mesh, {"temple_r": "temple", "temple_l": "temple"}


class _SideBasis:
    frame = SIDE_FRAME

    def __init__(self, matte):
        self._m = matte

    def matte(self, _rid):
        return self._m


def _side_view(mesh, yaw, pitch, shape=(260, 900), ppm=6.0):
    cam = raster.view_camera(SIDE_FRAME, yaw, pitch, ppm, shape, center_mm=np.array([0.0, 0.0, -70.0]))
    r = mesh.scene().render(cam, shape, 1)
    part = np.full(shape, -1, np.int64)
    part[r["mask"]] = mesh.part[r["face_id"][r["mask"]]]
    return {"cam": cam, "mask": r["mask"], "part": part, "mesh": mesh, "pose": None}


def far_head(edge: str, sign: str, elev: float) -> str:
    """seeing's far-edge head for a view ``elev`` deg above (below) the temple, tan(elevation) computed here."""
    return (f"{edge}: the arm's {edge} height, photo minus model, mm ({sign}), read on the {edge} face's far edge (this "
            f"view shows the {edge} face; that edge is its inner one, so a value also gains "
            f"{math.tan(math.radians(abs(elev))):.2f} per mm the photo's inner face lies further inward)")


def test_side_outline_measures_only_what_a_silhouette_shows():
    mesh, roles = _side_scene()
    bodies = {s: PO.temple_body(mesh, s, roles) for s in (1, -1)}
    assert bodies == {1: 0, -1: 1}
    # from the +X side, 14 deg above (05 / B1): the charted temple faces the camera, its top face shows ~9 px
    g = _side_view(mesh, 90.0, 14.0)
    matte = g["mask"].copy()
    matte[3:] |= g["mask"][:-3]                                                  # the photo's bottom edge 3 px lower
    out = Seeing._side_outline(_SideBasis(matte), "x", g, mesh, roles, bodies, HZ, None)
    ch, mi = out["charted"], out["mirrored"]
    assert ch["facing"] and ch["elevation_deg"] == pytest.approx(14.0, abs=0.05)
    ppm_y = 6.0 * math.cos(math.radians(14.0))                                   # image px per mm of the chart's y
    assert ch["mm_per_px"] == pytest.approx([1 / ppm_y, 1 / ppm_y], abs=1e-3)
    assert all(r["px_per_mm"] == pytest.approx(ppm_y, abs=1e-3) for r in ch["bottom"] + ch["top_silhouette"])
    assert [r["d_mm"] for r in ch["bottom"]] == [10.0 * i for i in range(1, 12)]
    assert all(r["reason"] is None and r["offset_mm"] == pytest.approx(3 / ppm_y, abs=0.01) for r in ch["bottom"])
    assert all(r["offset_mm"] is None and r["reason"] == "top face in view" for r in ch["top"])
    sil = [r["offset_mm"] for r in ch["top_silhouette"]]                        # information: the far top edge
    assert all(v == pytest.approx(0.0, abs=0.01) for v in sil)
    assert "bottom_silhouette" not in ch
    assert not mi["facing"] and mi["elevation_deg"] is None and "top_silhouette" not in mi
    assert all(r["reason"] == "seen from inside" for r in mi["top"] + mi["bottom"])
    text = Seeing._outline_text("charted", ch)
    assert "this view looks 14 deg down onto the temple" in text and f"1 px = {1 / ppm_y:.2f} mm" in text
    assert "not measured" not in text and "top (" not in text                    # no outline list for the top
    # v3-standin-fix: what a reader can edit, the arm's top height and (v3-standin-fix-review) its inner face x
    # tan(14 deg), less the stations' median; the lean and the matte's cut are in the record only
    assert far_head("top", "+ = the photo's edge higher", 14.0) == (
        "top: the arm's top height, photo minus model, mm (+ = the photo's edge higher), read on the top face's far "
        "edge (this view shows the top face; that edge is its inner one, so a value also gains 0.25 per mm the "
        "photo's inner face lies further inward)")
    assert (far_head("top", "+ = the photo's edge higher", 14.0) + ", less its median +0.0 over 11 stations, so a "
            "value is a step at that d against the rest of the arm; at d: 10 +0.0") in text
    assert "per mm wider" not in text and "lean" not in text and "matte" not in text
    terms = ch["top_far_edge"]
    assert terms["inner_face_mm_per_mm"] == round(math.tan(math.radians(ch["elevation_deg"])), 3) and \
        terms["inner_face_mm_per_mm"] == pytest.approx(0.249, abs=0.002)                       # tan(14 deg)
    assert terms["measures"] == "the arm's top height at its inner edge, 1 mm per mm" and \
        terms["also"] == ["the top face's lean across the arm", "the matte's cut at a lit edge"]
    # the far edge is the top face's inner edge: a photo whose inner face lies 2 mm further inward (x 58, the outer
    # face held at 66) reads +2 tan(14 deg) = +0.50 mm at every station, as the text says; the outer-bottom edge 0
    wide = _side_view(_side_scene(inner=58.0)[0], 90.0, 14.0)
    inward = Seeing._side_outline(_SideBasis(wide["mask"]), "x", g, mesh, roles, bodies, HZ, None)["charted"]
    want = 2.0 * math.tan(math.radians(14.0))
    assert all(r["offset_mm"] == pytest.approx(want, abs=1 / ppm_y) for r in inward["top_silhouette"]), \
        inward["top_silhouette"]
    assert all(r["offset_mm"] == pytest.approx(0.0, abs=0.01) for r in inward["bottom"])
    gu = _side_view(mesh, 90.0, -14.0)                                           # from below: the same sign (lower)
    wide_u = _side_view(_side_scene(inner=58.0)[0], 90.0, -14.0)
    below = Seeing._side_outline(_SideBasis(wide_u["mask"]), "x", gu, mesh, roles, bodies, HZ, None)["charted"]
    assert all(r["offset_mm"] == pytest.approx(want, abs=1 / ppm_y) for r in below["bottom_silhouette"]), \
        below["bottom_silhouette"]
    assert "; at d: 10 +0.0, 20 +0.0, 30 +0.0" in text and "110 +0.0; bottom" in text
    assert "bottom (+ = the photo's edge lower) at d: 10 +0.5, 20 +0.5" in text and "n/a" not in text
    # the photo's top raised by ~2 mm (whole px) on the near temple: over the whole arm the median takes it off (it
    # prints as the median: "+2.0 over 11 stations"); behind d 75 only (4 of 11 stations: the run-7 rear arm) the
    # median stays on the level and d 80-110 print the step; the bottom is untouched
    v2 = int(round(2.0 * ppm_y))
    near_t = g["part"] == 0
    u_of = {d: float(project_mm(PO.chart_to_posed([[d, 0.0, 66.0]], 1, HZ, None, None), g["cam"], SIDE_FRAME)[0][0])
            for d in (10.0 * i for i in range(1, 12))}
    u75 = float(np.interp(75.0, sorted(u_of), [u_of[d] for d in sorted(u_of)]))

    def raised(behind_75: bool) -> np.ndarray:
        m = matte.copy()
        for c in np.flatnonzero(near_t.any(axis=0)):
            if behind_75 and (c - u75) * (u_of[110.0] - u_of[10.0]) < 0:
                continue
            top = int(np.flatnonzero(near_t[:, c])[0])
            m[max(0, top - v2):top, c] = True
        return m
    whole = Seeing._side_outline(_SideBasis(raised(False)), "x", g, mesh, roles, bodies, HZ, None)["charted"]
    assert all(r["offset_mm"] == pytest.approx(v2 / ppm_y, abs=0.01) for r in whole["top_silhouette"])
    tw = Seeing._outline_text("charted", whole)
    assert f"less its median {v2 / ppm_y:+.1f} over 11 stations" in tw and "110 +0.0; bottom" in tw
    rear = Seeing._side_outline(_SideBasis(raised(True)), "x", g, mesh, roles, bodies, HZ, None)["charted"]
    got = {r["d_mm"]: r["offset_mm"] for r in rear["top_silhouette"]}
    assert all(got[d] == pytest.approx(v2 / ppm_y if d >= 80 else 0.0, abs=0.01) for d in got), got
    tr = Seeing._outline_text("charted", rear)
    s2 = f"+{v2 / ppm_y:.1f}"
    assert "less its median +0.0 over 11 stations" in tr
    assert f"70 +0.0, 80 {s2}, 90 {s2}, 100 {s2}, 110 {s2}; bottom" in tr     # 4 of 11 stepped: the level holds
    assert rear["bottom"] == ch["bottom"]
    assert Seeing._outline_text("mirrored", mi).startswith("outline: the mirrored temple is seen from inside here")
    # square-on (pitch 0): both edges are silhouettes; the far temple stays unmeasured
    g0 = _side_view(mesh, 90.0, 0.0)
    out0 = Seeing._side_outline(_SideBasis(g0["mask"]), "x", g0, mesh, roles, bodies, HZ, None)
    assert out0["charted"]["facing"] and abs(out0["charted"]["elevation_deg"]) < 0.05
    assert all(r["offset_mm"] == 0.0 for r in out0["charted"]["top"] + out0["charted"]["bottom"])
    assert all(r["reason"] == "seen from inside" for r in out0["mirrored"]["top"])
    # from below: the bottom face shows, the top is the outline's
    gb = _side_view(mesh, 90.0, -14.0)
    outb = Seeing._side_outline(_SideBasis(gb["mask"]), "x", gb, mesh, roles, bodies, HZ, None)["charted"]
    assert all(r["reason"] == "bottom face in view" for r in outb["bottom"]) and "bottom_silhouette" in outb
    assert all(r["offset_mm"] == 0.0 for r in outb["top"])
    assert "looks 14 deg up at the temple" in Seeing._outline_text("charted", outb)
    # from the -X side (01): the mirrored temple faces the camera
    gm = _side_view(mesh, -90.0, 14.0)
    outm = Seeing._side_outline(_SideBasis(gm["mask"]), "x", gm, mesh, roles, bodies, HZ, None)
    assert outm["mirrored"]["facing"] and not outm["charted"]["facing"]
    assert all(r["offset_mm"] == 0.0 for r in outm["mirrored"]["bottom"])
    # just off square: the top face shows < SIDE_FACE_PX, so the top is measured; the far temple, 2.8 mm higher in
    # the image, then lies at the near one's top edge ("covered"), so this check leaves it out of the model mask
    e = math.degrees(math.atan(0.8 * SIDE_FACE_PX / (6.0 * 6.0)))                # 6 mm thick at ~6 px/mm
    gs = _side_view(mesh, 90.0, e)
    outs = Seeing._side_outline(_SideBasis(gs["mask"]), "x", gs, mesh, roles, bodies, HZ, None)["charted"]
    assert all(r["reason"] == "covered" for r in outs["top"]) and all(r["reason"] is None for r in outs["bottom"])
    gs["mask"] = gs["part"] == 0
    outs = Seeing._side_outline(_SideBasis(gs["mask"]), "x", gs, mesh, roles, bodies, HZ, None)["charted"]
    assert all(r["reason"] is None and r["offset_mm"] == 0.0 for r in outs["top"] + outs["bottom"])


# session2's compare cmp022 (head c0015 = r6 of run 7's package, a tool test; v3p3-verify-8): the top silhouette of the
# camera-facing temple, photo minus model, + = the photo's edge higher (None: covered / no photo edge). Identical
# from cmp001 (revision 0) at d 110-130: the original's rear arm.
SESSION2_TOP_SIL = {
    "01": (14.8, [None, -0.63, None, -0.88, -0.88, -0.75, -0.88, -1.01, -0.88, -0.25, 1.40, 2.84, 0.65]),
    "05": (14.7, [None, -0.41, None, -0.69, -0.82, -0.69, -0.82, -0.82, -0.96, -0.14, 1.81, 2.69, 0.86]),
    "B1": (14.7, [None, -0.49, None, -0.73, -0.85, -0.73, -0.73, -0.73, -0.86, -0.37, 1.86, 3.02, 1.02])}


def _sil_record(elev: float, vals: list, reason_none: str = "covered") -> dict:
    """An outline_offsets_mm record of a view from above as _side_outline writes it: every top station "top face in
    view", the far edge in top_silhouette, a bottom measured at 0."""
    d = [10.0 * (i + 1) for i in range(len(vals))]
    top = [{"d_mm": x, "offset_mm": None, "reason": "top face in view", "px_per_mm": 7.5} for x in d]
    sil = [{"d_mm": x, "offset_mm": v, "reason": None if v is not None else reason_none, "px_per_mm": 7.5}
           for x, v in zip(d, vals)]
    bottom = [{"d_mm": x, "offset_mm": 0.0, "reason": None, "px_per_mm": 7.5} for x in d]
    return {"facing": True, "elevation_deg": elev, "mm_per_px": [0.125, 0.131], "top": top, "bottom": bottom,
            "top_silhouette": sil}


def test_far_edge_text_prints_session2s_low_rear_arm():
    """v3p3-verify-8 (owner P1, confirmed): in 01, 05 and B1 (14.7-14.8 deg from above) session2's head prints
    "top: not measured" while its arm top behind the facets sits 2.3-2.6 mm below the photo's at d 110 and 3.5-3.8 at
    d 120 against the d 40-90 level (the model's own render under the photo's matte rule agrees). The line now prints
    the top face's far edge, photo and model alike, minus its median over the stations: d 110 / 120 read +2.1..+2.6 /
    +3.4..+3.8 in all three, d 40-90 within 0.3 of 0, with the median, the count, tan(elevation) per mm of width and
    the other stations' reasons; no "not measured" for the top."""
    want = {"01": ("-0.8", "100 +0.5, 110 +2.1, 120 +3.6, 130 +1.4"),
            "05": ("-0.7", "100 +0.5, 110 +2.5, 120 +3.4, 130 +1.5"),
            "B1": ("-0.7", "100 +0.4, 110 +2.6, 120 +3.8, 130 +1.8")}
    for pid, (elev, vals) in SESSION2_TOP_SIL.items():
        o = _sil_record(elev, vals)
        o["top_silhouette"][2]["reason"] = "no photo edge" if pid == "01" else "covered"
        text = Seeing._outline_text("charted", o)
        med, tail = want[pid]
        assert "not measured" not in text, pid
        assert (far_head("top", "+ = the photo's edge higher", elev) + f", less its median {med} over 11 stations, "
                "so a value is a step at that d against the rest of the arm; at d: 10 covered, 20 ") in text
        assert "per mm wider" not in text and "width" not in text                # v3-standin-fix: not the width
        assert tail + "; bottom (+ = the photo's edge lower) at d: 10 +0.0" in text, (pid, text)
        level = float(np.median([v for v in vals if v is not None]))
        assert all(abs(v - level) <= 0.3 for v in vals[3:9])                     # d 40-90: on the level
        assert "(covered = the frame or the other temple lies at that edge" in text
        assert ("no photo edge = the photo has no edge" in text) == (pid == "01")
    # under FAR_EDGE_LEVEL_MIN stations: as measured, said so
    few = _sil_record(14.7, [None, -0.4, None, None, 1.8, 2.7] + [None] * 7)
    t = Seeing._outline_text("charted", few)
    assert FAR_EDGE_LEVEL_MIN == 5 and ("as measured (3 stations, under 5 for a median), so a value is the "
                                        "difference at that d with what every station shares left in") in t
    assert "20 -0.4, 30 covered, 40 covered, 50 +1.8, 60 +2.7, 70 covered" in t
    # a station whose top is measured (its face shows < 1 px there): the outline's list, then the far edge's
    mixed = _sil_record(14.7, SESSION2_TOP_SIL["05"][1])
    mixed["top"][0] = {"d_mm": 10.0, "offset_mm": 0.12, "reason": None, "px_per_mm": 7.5}
    mixed["top_silhouette"] = mixed["top_silhouette"][1:]
    t = Seeing._outline_text("charted", mixed)
    assert "top (+ = the photo's edge higher) at d: 10 +0.1, 20 top face in view, 30 top face in view" in t
    assert "less its median -0.7 over 11 stations" in t and "; at d: 20 +0.3, 30 covered, 40 +0.0" in t
    assert "top face in view = the view shows the top face; the silhouette is its far edge" in t
    # an older record (no silhouette kept): "not measured", without the false "set by its width and lean"
    old = _sil_record(14.7, SESSION2_TOP_SIL["05"][1])
    del old["top_silhouette"]
    t = Seeing._outline_text("charted", old)
    assert "top: not measured (the view shows the top face, so the top silhouette is that face's far edge); " in t
    assert "width and lean" not in t
    # a view from below: the bottom face's far edge, + = the photo's edge lower
    up = {**_sil_record(-14.0, SESSION2_TOP_SIL["05"][1]), "elevation_deg": -14.0}
    up["bottom_silhouette"] = up.pop("top_silhouette")
    up["top"], up["bottom"] = up["bottom"], up["top"]
    for r in up["bottom"]:
        r["reason"] = "bottom face in view"
    t = Seeing._outline_text("charted", up)
    assert "looks 14 deg up at the temple" in t and "top (+ = the photo's edge higher) at d: 10 +0.0" in t
    assert (far_head("bottom", "+ = the photo's edge lower", -14.0) + ", less its median -0.7 over 11 stations, so "
            "a value is a step at that d against the rest of the arm") in t
    assert "0.25 per mm the photo's inner face lies further inward" in t     # from below: the same sign (lower)
    assert "per mm wider" not in t


# ------------------------------------------------------------------------------------------------ geometry, zoom
def test_geometry_view_and_zoom_layout():
    a = np.zeros((40, 60, 4), np.uint8)
    a[:, :, :3] = 128
    a[5:35, 5:30, :3] = (120, 128, 140)
    a[5:35, 30:55, :3] = (124, 130, 140)                                         # 4 / 2 / 0 levels apart
    a[5:35, 5:55, 3] = 255
    g = C.geometry_view(a).astype(int)
    assert (g[0, 0] == C.GEOMETRY_BG).all()                                      # the backdrop
    assert abs(g[20, 40, 0] - g[20, 10, 0]) == pytest.approx(4 * 255 / C.GEOMETRY_MIN_SPAN, abs=1.5)  # 8 x at most
    assert g[20, 40, 2] == g[20, 10, 2]
    b = a.copy()
    b[5:35, 30:55, :3] = (180, 90, 140)                                          # a strong turn: full range
    gb = C.geometry_view(b).astype(int)
    assert gb[20, 10, 0] == 0 and gb[20, 40, 0] == 255
    region = np.zeros((40, 60), bool)
    region[5:35, 5:30] = True                                                    # stretch over the left face only
    gr = C.geometry_view(b, region=region).astype(int)
    assert gr[20, 40, 0] == 255 and gr[20, 10, 0] == pytest.approx(255 * 16 / 32, abs=1)   # min span about it
    assert (C.geometry_view(b, region=np.zeros((40, 60), bool)) == C.geometry_view(b)).all()   # < 100 px: all
    assert C.zoom_scale(100, 50) == 2.0 and C.zoom_scale(500, 300) == pytest.approx(min(760 / 500, 500 / 300))
    assert C.zoom_scale(1520, 1000) == pytest.approx(0.5) and C.zoom_scale(760, 500) == 1.0
    panels = [C.labelled(Image.new("RGB", (760, 500)), "x") for _ in range(4)]
    sheet = C.grid(panels, 2, max_px=C.SHEET_MAX_PX)
    assert sheet.size == (2 * 760 + 3 * 6, 2 * (500 + C.LABEL_PX) + 3 * 6)     # never downscaled
    long = "photo + outline + model edges (deg, - = valley) and a good deal more words than fit"
    for w in (480, 200, 40):                                                     # review: "- = va" cut at 480 px
        im = C.labelled(Image.new("RGB", (w, 30), (0, 0, 0)), long, fit=True)
        strip = np.asarray(im.convert("RGB"))[:C.LABEL_PX, :, :]
        ink = np.flatnonzero((strip < 128).all(axis=2).any(axis=0))
        assert im.size == (w, 30 + C.LABEL_PX) and len(ink) and ink.max() < w - 2   # the text ends inside
    plain = np.asarray(C.labelled(Image.new("RGB", (100, 30)), long).convert("RGB"))
    assert (plain[:C.LABEL_PX, -3:] < 128).any()                                 # without fit: as before (cut)


def test_resample_box_registers_on_the_crop_grid_and_never_overshoots():
    yy, xx = np.mgrid[0:120, 0:160].astype(np.float32)
    for box, k in (((10, 5, 47, 29), 1.7), ((10, 5, 47, 29), 2.0), ((3, 4, 150, 110), 0.62),
                   ((100, 60, 160, 120), 1.33)):                                # the last at the image's corner
        x0, y0, x1, y1 = box
        shape = (round((y1 - y0) * k), round((x1 - x0) * k))
        u = x0 + (np.arange(shape[1]) + 0.5) / k - 0.5                         # crop_camera's pixel centres
        v = y0 + (np.arange(shape[0]) + 0.5) / k - 0.5
        ox, oy = C.resample_box(xx, box, k, shape), C.resample_box(yy, box, k, shape)
        inner = (slice(2, -2), slice(2, -2))
        tol = 1e-3 if k > 1 else 0.1                                             # bilinear shrinks area-like
        assert np.abs(ox - np.minimum(u, 159)[None])[inner].max() < tol, (box, k)
        assert np.abs(oy - np.minimum(v, 119)[:, None])[inner].max() < tol, (box, k)
    rgb = np.random.default_rng(1).integers(0, 256, (60, 80, 3)).astype(np.uint8)
    assert (C.resample_box(rgb, (3, 4, 50, 40), 1.0, (36, 47)) == rgb[4:40, 3:50]).all()     # k 1: the box
    step = np.zeros((40, 40, 3), np.uint8)
    step[:, 20:] = 255
    step[:, 21:] = 200                                                           # a bright rim, then a lighter face
    up = C.resample_box(step, (0, 0, 40, 40), 2.0, (80, 80)).astype(int)
    assert up.min() >= 0 and up.max() <= 255 and (up[:, 30:42] <= 255).all()
    lan = np.asarray(Image.fromarray(step).resize((80, 80), Image.LANCZOS)).astype(int)
    assert (up[:, 44:] >= 200).all() and lan[:, 44:].min() < 200                 # LANCZOS rings below the face
    alpha = np.zeros((4, 4))
    alpha[1:3, 1:3] = 1.0
    shown = np.full((4, 4, 3), 90, np.uint8)
    shown[1, 1] = (253, 252, 255)
    shown[1, 2] = (240, 255, 255)
    assert C.backdrop_share(shown, alpha, (255, 255, 255)) == pytest.approx(0.25)    # 1 of 4 within 3 levels
    assert C.backdrop_share(shown, np.zeros((4, 4)), (255, 255, 255)) == 0.0


# ------------------------------------------------------------------------------------------------ Seeing helpers
class _Basis:
    id = "basis-1"
    refs = {"reference-05": {}, "black-side": {}}

    @classmethod
    def is_usable(cls, rid):
        return rid in cls.refs

    @classmethod
    def usable_refs(cls):
        return list(cls.refs)

    @staticmethod
    def unusable_refs():
        return {}

    @staticmethod
    def shape(_rid):
        return (600, 1000)


def test_check_v3_args_refusals_and_forms():
    ok = Seeing._check_v3_args(_Basis, [{"rid": "reference-05", "box": [-5.5, 10.2, 300.4, 200], "geometry": True},
                                        {"rid": "black-side.png", "box": [990, 590, 2000, 2000]}],
                               [{"pair": "crown|logo", "shape": "ridge", "deg": 10.8, "pts": [[0, 1, 2], [3, 4, 5]]}],
                               {"z_mm": -10.6, "axis_xz_mm": [64.6, -10.1], "opening_deg": 0.0}, -10.6)
    assert ok["zoom"] == [{"rid": "reference-05", "box": (0, 10, 301, 200), "geometry": True},
                          {"rid": "black-side", "box": (990, 590, 1000, 600), "geometry": False}]
    assert ok["edges"][0]["pts"].shape == (2, 3) and len(ok["edges_sha256"]) == 64 and ok["opening"] is None
    assert ok["edges"][0]["deg_at"] is None
    again = Seeing._check_v3_args(_Basis, None, [{"pair": "crown|logo", "shape": "ridge", "deg": 10.8,
                                                  "pts": [[0, 1, 2], [3, 4, 5.000001]]}], None, -10.6)
    assert again["edges_sha256"] != ok["edges_sha256"] and again["zoom"] == []
    op = Seeing._check_v3_args(_Basis, None, None, (3.0, [-64.6, -10.1]), None)["opening"]
    assert op == (3.0, (64.6, -10.1))
    # numpy reals are numbers (the lab may send them); deg_at rides along and changes the sha
    npy = Seeing._check_v3_args(_Basis, [{"rid": "reference-05", "box": np.array([0, 0, 50, 50], np.float32),
                                          "geometry": np.bool_(True)}],
                                [{"pair": "crown|logo", "shape": "ridge", "deg": np.float32(10.8),
                                  "pts": np.array([[0, 1, 2], [3, 4, 5]], np.float32)}], None, np.float32(-10.6))
    assert npy["edges"][0]["deg"] == pytest.approx(10.8) and npy["zoom"][0]["geometry"] is True
    at = Seeing._check_v3_args(_Basis, None, [{"pair": "crown|logo", "shape": "ridge", "deg": 10.8,
                                               "pts": [[0, 1, 2], [3, 4, 5]], "deg_at": [8.0, -1.5]}], None, -10.6)
    assert at["edges"][0]["deg_at"].tolist() == [8.0, -1.5] and at["edges_sha256"] != ok["edges_sha256"]
    assert Seeing._check_v3_args(_Basis, None, [{"pair": "a|b", "shape": "valley", "deg": 3.0,     # unsigned valley
                                                 "pts": [[0, 0, 0], [1, 1, 1]]}], None, 0.0)["edges"][0]["deg"] == 3.0
    edge = {"pair": "a|b", "shape": "ridge", "deg": 1.0, "pts": [[0, 0, 0], [1, 1, 1]]}
    bad = [dict(zoom=[{"rid": "reference-09", "box": [0, 0, 50, 50]}]),
           dict(zoom=[{"rid": "reference-05", "box": [0, 0, 50]}]),
           dict(zoom=[{"rid": "reference-05", "box": [0, 0, 5, 50]}]),            # under 8 px
           dict(zoom=[{"rid": "reference-05", "box": [0, 0, 50, 50], "geometry": "yes"}]),
           dict(zoom=[{"rid": "reference-05", "box": [0, 0, 50, 50]}] * 4),
           dict(edges=[edge], hinge=None),                                          # edges need hinge_z_mm
           dict(edges=[{**edge, "shape": "crest"}]), dict(edges=[{**edge, "pts": [[0, 0, 0]]}]),
           dict(edges=[{**edge, "deg": float("nan")}]), dict(edges=[{**edge, "pts": [[0, 0], [1, 1]]}]),
           dict(edges=[{**edge, "deg": True}]),                                     # a bool is no angle
           dict(edges=[{**edge, "deg": -5.0}]),                                     # a ridge with a valley's sign
           dict(edges=[{**edge, "deg_at": [1.0]}]), dict(edges=[{**edge, "deg_at": [1.0, float("nan")]}]),
           dict(edges=[{**edge, "deg_at": "steep"}]),
           dict(edges=edge), dict(opening=(3.0, [0.0, -10.1])), dict(opening={"axis_xz_mm": [1, 2]})]
    for kw in bad:
        with pytest.raises(SeeingError) as ei:
            Seeing._check_v3_args(_Basis, kw.get("zoom"), kw.get("edges"), kw.get("opening"),
                                  kw.get("hinge", -10.6))
        assert ei.value.code == "E_ARGS", kw
    with pytest.raises(SeeingError, match="ridge with deg -5"):
        Seeing._check_v3_args(_Basis, None, [{**edge, "deg": -5.0}], None, 0.0)
    # photo_names (v3p3 review: the images said "black-side photo", the texts "B1"): the lab's ids, checked
    nm = Seeing._check_v3_args(_Basis, None, None, None, None, {"black-side.png": "B1", "reference-05": "05"})
    assert nm["names"] == {"black-side": "B1", "reference-05": "05"} and ok["names"] == {}
    for names, words in (({"reference-09": "09"}, "not a photo of basis-1"), (["B1"], "must be {photo: name}"),
                         ({"black-side": "B 1"}, "1-60 characters"), ({"black-side": ""}, "1-60 characters"),
                         ({"black-side": 7}, "1-60 characters")):
        with pytest.raises(SeeingError, match=re.escape(words)) as ei:
            Seeing._check_v3_args(_Basis, None, None, None, None, names)
        assert ei.value.code == "E_ARGS"
    Seeing._check_matched_args("studio_dome_v1", np.float32(-10.6), None, None, None)   # a numpy hinge_z_mm


def test_check_v3_args_range_and_soft():
    """WP6 v3 I1 / I7 fields: range [lo, hi] (signed) and soft (bool). deg and every deg_at must lie in the edge's own
    range within EDGE_RANGE_TOL_DEG (probe1's crown|shoulder "36.9 (37.7-39.3)": a mean that mixed in the roll sub-band
    edges, which a label would then print; refused, naming the edge and the numbers); with a range deg is signed, so
    a crease voted ridge may carry a negative deg (a warp crease that changes sign); soft only on a facet|base pair;
    the sha holds range and soft only when given (soft: false = absent), so earlier edge sets keep their cache keys."""
    pts = [[0, 1, 2], [3, 4, 5]]

    def chk(**e):
        return Seeing._check_v3_args(_Basis, None, [{"pair": "crown|shoulder", "shape": "ridge", "deg": 38.4,
                                                     "pts": pts, **e}], None, -10.6)
    plain = chk()
    assert plain["edges"][0]["range"] is None and plain["edges"][0]["soft"] is False
    rng = chk(range=[37.7, 39.3], deg_at=[37.7, 39.3])
    assert rng["edges"][0]["range"] == (37.7, 39.3) and rng["edges_sha256"] != plain["edges_sha256"]
    assert chk(range=np.array([37.7, 39.3], np.float32))["edges"][0]["range"] == pytest.approx((37.7, 39.3))
    assert chk(range=[37.7, 39.3], deg=37.65, deg_at=[37.61, 39.39])["edges"][0]["deg"] == 37.65   # within 0.1
    assert chk(soft=False)["edges_sha256"] == plain["edges_sha256"]                    # false = absent
    sb = Seeing._check_v3_args(_Basis, None, [{"pair": "shoulder|base", "shape": "valley", "deg": -4.5,
                                               "range": [-5.3, -3.0], "soft": np.bool_(True), "pts": pts}], None, 0.0)
    assert sb["edges"][0]["soft"] is True
    sb2 = Seeing._check_v3_args(_Basis, None, [{"pair": "shoulder|base", "shape": "valley", "deg": -4.5,
                                                "range": [-5.3, -3.0], "pts": pts}], None, 0.0)
    assert sb2["edges_sha256"] != sb["edges_sha256"]                                   # soft changes the drawing
    assert chk(shape="ridge", deg=-2.2, range=[-10.0, 3.0])["edges"][0]["deg"] == -2.2  # signed with a range
    for kw, msg in ((dict(deg=36.9, range=[37.7, 39.3]), r"'crown\|shoulder': deg \+36\.90 lies outside its own range "
                                                        r"\+37\.70\.\.\+39\.30"),
                    (dict(range=[37.7, 39.3], deg_at=[1.0, 39.0]), r"deg_at\[0\] \+1\.00 \(1 of 2 points\) lies "
                                                                   "outside"),
                    (dict(shape="valley", deg=4.5, range=[-5.3, -3.0]), "outside its own range"),   # signed, not -|deg|
                    (dict(soft=True), "only a facet's border with the base can be soft"),
                    (dict(soft="yes"), "soft \\(optional\\): true \\| false"),
                    (dict(range=[39.3, 37.7]), "lo <= hi"), (dict(range=[37.7]), "lo <= hi"),
                    (dict(range=[True, 39.3]), "lo <= hi"), (dict(range="ab"), "lo <= hi"),
                    (dict(range=[37.7, float("inf")]), "lo <= hi"), (dict(range=5.0), "lo <= hi")):
        with pytest.raises(SeeingError, match=msg) as ei:
            chk(**kw)
        assert ei.value.code == "E_ARGS", kw
    with pytest.raises(SeeingError, match="ridge with deg -2.2"):                       # without a range: as before
        chk(deg=-2.2)


def test_soft_records_and_texts():
    """A soft border's record carries soft (no deg_shown: no angle is shown for it) and its range; the compare text
    and the zoom text list soft borders apart from the creases, as dashed grey with no angle; a soft edge with no
    visible run is not listed."""
    drawn = [{"pair": "crown|logo", "shape": "ridge", "deg": 10.8, "side": 1, "range": [10.1, 11.5],
              "runs": [np.array([[0.0, 0.0, 10.2], [10.0, 0.0, 11.4]])]},
             {"pair": "shoulder|base", "shape": "valley", "deg": -4.5, "side": 1, "range": [-5.3, -3.0], "soft": True,
              "runs": [np.array([[0.0, 5.0, -5.0], [10.0, 5.0, -3.1]])]},
             {"pair": "lower|base", "shape": "flat", "deg": 1.0, "side": 1, "soft": True, "runs": []}]
    rec = Seeing._edge_records(drawn, (0, 0), 1.0)
    assert rec[0]["deg_shown"] == [10.2, 11.4] and rec[0]["range"] == [10.1, 11.5] and "soft" not in rec[0]
    assert rec[1]["soft"] is True and "deg_shown" not in rec[1] and rec[1]["range"] == [-5.3, -3.0]
    assert Seeing._edge_pairs(rec) == (["crown|logo"], ["shoulder|base"])
    row = {"text_head": "05 (side)", "crop": {"box": [0, 0, 10, 10], "k": 1.0}, "px_per_mm": 3.2, "model_edges": rec}
    text = Seeing._matched_text(row, None, "cmp003")
    assert ("model edges (from the build): crown|logo; soft borders (dashed grey: smooth-shaded, drawn without an "
            "angle; the fold is listed with the build's creases): shoulder|base") in text and "lower|base" not in text
    assert "no crease" not in text                         # it stays a crease in the build's tables (review2 P3-2)
    zrec = {"rid": "reference-05", "view_kind": "side", "colourway": "target", "box": [0, 0, 40, 40], "k": 2.0,
            "px_per_mm": 7.3, "in_box": "temple", "panel_labels": {"photo": "p", "render": "r", "overlay": "o",
                                                                   "render_edges": "e"},
            "model_edges": rec, "edge_labels": [{"pair": "crown|logo", "text": "11°", "uv": [5.0, 5.0]}]}
    zt = Seeing._zoom_text(zrec, "cmp004")
    assert ("model edges shown (deg from the build; - = valley; a-b° (a to b° when a is negative) = least-largest "
            "angle along the drawn run the label sits on (along the whole edge when the build gives no angle per "
            "point)): crown|logo 11°; soft "
            "borders shown (dashed grey: smooth-shaded, drawn without an angle; the fold is listed with the build's "
            "creases): shoulder|base") in zt
    assert "-5" not in zt and "-4.5" not in zt
    assert zt.startswith("zoom 1 on reference-05 (side, target): native box")      # no name: the basis name
    assert Seeing._zoom_text({**zrec, "name": "05"}, "cmp004").startswith("zoom 1 on 05 (side, target): native box")


def test_check_edges_on_model():
    """Model edges must lie on the head (review: a stale build, a wrong hinge_z_mm or a missing opening were drawn
    with status ok): the +X temple's chart points sit on its vertices; 1 mm off in d (rings every 2 mm) or opened
    5 deg they do not, and the refusal names the edge and the distance."""
    mesh, _roles = _side_scene()
    d = np.arange(0.0, 121.0, 2.0)
    on = [{"pair": "top|outer", "pts": np.column_stack([d, np.full_like(d, 3.0), np.full_like(d, 66.0)])},
          {"pair": "bottom|outer", "pts": np.column_stack([d[5:20], np.full_like(d[5:20], -3.0),
                                                           np.full_like(d[5:20], 66.0)])}]
    assert max(Seeing._check_edges_on_model(on, mesh, 0, HZ, None)) < 1e-9
    with pytest.raises(SeeingError) as ei:
        Seeing._check_edges_on_model(on, mesh, 0, HZ + 1.0, None)
    assert ei.value.code == "E_ARGS" and "2 of 2 model_edges" in str(ei.value) and "1.00 mm" in str(ei.value)
    with pytest.raises(SeeingError):
        Seeing._check_edges_on_model(on, mesh, 0, HZ, (5.0, (60.0, -10.0)))
    with pytest.raises(SeeingError, match="no \\+X temple body"):
        Seeing._check_edges_on_model(on, mesh, None, HZ, None)
    mixed = on + [{"pair": "stale|edge", "pts": on[0]["pts"] + [0.0, 0.0, 0.7]}]
    with pytest.raises(SeeingError, match=r"model_edges\[2\] 'stale\|edge'"):
        Seeing._check_edges_on_model(mixed, mesh, 0, HZ, None)


def test_edges_posed_records_and_text():
    pts = np.array([[10.0, 5.0, 70.0], [40.0, 8.0, 68.0]])
    hz = -10.6
    edges = [{"pair": "crown|logo", "shape": "ridge", "deg": 10.8, "pts": pts, "deg_at": np.array([9.0, 12.0])}]
    pose = {"+1": {"theta_deg": 3.0, "axis_xz_mm": [64.6, -10.1]}, "-1": {"theta_deg": 2.0, "axis_xz_mm": [-64.6,
                                                                                                         -10.1]}}
    out = Seeing._edges_posed(edges, pose, hz, None)
    assert [e["side"] for e in out] == [1, -1] and out[1]["deg_at"].tolist() == [9.0, 12.0]
    flat = Seeing._edges_posed(edges, None, hz, None)
    np.testing.assert_allclose(flat[0]["P"], np.c_[pts[:, 2], pts[:, 1], hz - pts[:, 0]])
    np.testing.assert_allclose(flat[1]["P"], flat[0]["P"] * [-1, 1, 1])        # the mirrored temple
    np.testing.assert_allclose(out[0]["P"], PO.rot_about_hinge(flat[0]["P"], 1, 3.0, (64.6, -10.1)))
    np.testing.assert_allclose(out[1]["P"], PO.rot_about_hinge(flat[1]["P"], -1, 2.0, (64.6, -10.1)))
    opened = Seeing._edges_posed(edges, None, hz, (5.0, (64.6, -10.1)))
    np.testing.assert_allclose(opened[0]["P"], PO.rot_about_hinge(flat[0]["P"], 1, 5.0, (64.6, -10.1)))
    drawn = [{"pair": "crown|logo", "shape": "ridge", "deg": 10.8, "side": 1,
              "runs": [np.array([[0.0, 0.0, 9.0], [10.0, 0.0, 10.5], [20.0, 0.0, 12.0]])]},
             {"pair": "crown|logo", "shape": "ridge", "deg": 10.8, "side": -1, "runs": []}]
    rec = Seeing._edge_records(drawn, (100, 50), 0.5)
    assert rec[0]["side"] == "charted" and rec[1]["side"] == "mirrored"         # one vocabulary per record
    assert rec[0]["shown_px"] == 20.0 and rec[1]["runs_px"] == [] and rec[0]["deg_shown"] == [9.0, 12.0]
    assert "deg_shown" not in rec[1]
    np.testing.assert_allclose(rec[0]["runs_px"][0][0], [100.5, 50.5])           # (u + 0.5) / k - 0.5 + x0
    np.testing.assert_allclose(rec[0]["runs_px"][0][-1], [140.5, 50.5])
    rows = [{"d_mm": 10.0, "offset_mm": 0.25, "reason": None}, {"d_mm": 20.0, "offset_mm": None,
                                                                "reason": "covered"},
            {"d_mm": 30.0, "offset_mm": -0.04, "reason": None}]
    row = {"text_head": "05 (side)", "crop": {"box": [0, 0, 10, 10], "k": 1.0}, "px_per_mm": 3.2,
           "outline_offsets_mm": {
               "charted": {"facing": True, "elevation_deg": 14.6, "mm_per_px": [0.137, 0.143],
                           "top": [{"d_mm": 10.0, "offset_mm": None, "reason": "top face in view"}],
                           "bottom": rows},
               "mirrored": {"facing": False, "elevation_deg": None, "mm_per_px": None,
                            "top": [{"d_mm": 10.0, "offset_mm": None, "reason": "seen from inside"}],
                            "bottom": [{"d_mm": 10.0, "offset_mm": None, "reason": "seen from inside"}]}},
           "model_edges": rec, "render_backdrop_share": {"native_linear": 0.132}}
    text = Seeing._matched_text(row, None, "cmp001")
    assert ("outline of the charted temple, photo minus model (mm; 1 px = 0.14 mm; this view looks 15 deg down onto "
            "the temple): top: not measured") in text
    row["outline_offsets_mm"]["charted"]["mm_per_px"] = [0.122, 0.151]
    assert "(mm; 1 px = 0.12-0.15 mm; this view" in Seeing._matched_text(row, None, "cmp001")
    assert "bottom (+ = the photo's edge lower) at d: 10 +0.2, 20 covered, 30 +0.0 (covered = the frame" in text
    assert "outline: the mirrored temple is seen from inside here" in text and "n/a" not in text
    assert "model edges (from the build): crown|logo" in text and "compare_id cmp001." in text
    assert "render rows: 13% (native_linear) of the model's pixels show in the backdrop's colour" in text
    zrec = {"rid": "reference-05", "view_kind": "side", "colourway": "target", "box": [0, 0, 40, 40], "k": 2.0,
            "px_per_mm": 7.3, "in_box": "none", "panel_labels": {"photo": "p", "render": "r", "overlay": "o",
                                                                 "geometry": "g"},
            "model_edges": [], "edge_labels": [], "render_backdrop_share": 0.0}
    zt = Seeing._zoom_text(zrec, "cmp002")
    assert "the model is not in this box" in zt and "nan" not in zt and "this panel only" in zt
    assert ("model edges shown (deg from the build; - = valley; a-b° (a to b° when a is negative) = least-largest "
            "angle along the drawn run the label sits on (along the whole edge when the build gives no angle per "
            "point)): none here") in zt
    assert "soft borders" not in zt


# ------------------------------------------------------------------------------------------------ integration
def _rw(func, path, _exc):
    os.chmod(path, stat.S_IWRITE)
    func(path)


def model_edges_of(tb) -> list:
    """WP6 v3 I1: the builder's model_edges(tb) (track builder2; g3d/temple/build.py, or report.py), the edges the lab
    sends to compare_to_photos: one entry per connected run of a crease, face to face only (no roll / chamfer band),
    {pair, shape, deg (signed), range, deg_at, pts (the build's own vertices), soft}. Phase 1 chained the outer AND
    roll polygons here and took the crease table's mean, so a label could mix in the roll sub-bands' angles."""
    from glasses3d.g3d.temple import build as BD
    from glasses3d.g3d.temple import report as RP
    fn = getattr(BD, "model_edges", None) or getattr(RP, "model_edges", None)
    assert fn is not None, "WP6 v3 I1: g3d.temple has no model_edges(tb)"
    return fn(tb)


def _soft(edges: list, pair: str) -> list:
    """The edges with ``pair`` marked soft: a PLUMBING check of the soft border's drawing and texts on the real head
    without another Blender build. Revision 7 has no soft facet: its shoulder|base is a hard border, and the build
    makes all 7 of its face edges sharp (review2-seeing2-1 probe_real_soft), so the head these tests draw on shows a
    sharp line where the edge set says soft, and "smooth-shaded" in the texts asserted below is not true of that head.
    seeing draws and prints what the edge set says (it cannot see the build's sharp edges); that a soft flag means
    smooth-shaded is I1's (test_builder_soft_flag_means_smooth_shaded)."""
    return [dict(e, soft=True) if e["pair"] == pair else e for e in edges]


def _label_values(text: str) -> list:
    """The whole degrees a label shows (compare.deg_text, the whole label or an assertion error): "16°" -> [16],
    "16-29°" -> [16, 29], "-9 to 4°" -> [-9, 4]."""
    m = re.fullmatch(r"(-?\d+)(?:-(\d+)| to (-?\d+))?°", text)
    assert m, text
    return [int(x) for x in m.groups() if x is not None]


PROBE1_HOST = G3D / "data" / "wp6v2" / "review_20261012" / "v3" / "probe1" / "host.json"


@pytest.mark.parametrize("case", ["r0007", "r0007_soft_shoulder", "probe1_warp", "probe1_warp_soft_shoulder"])
def test_builder_model_edges_meet_the_seeing_contract(case):
    """I1 -> I7 without Blender: the builder's model_edges of revision 7 (flat facets) and of probe1's fitted warp
    crown (v3/probe1/host.json: crown|logo bends 15.7 -> 29.2 deg along its length), each also with the shoulder soft,
    pass _check_v3_args (deg and every deg_at inside the run's own range; soft only on shoulder|base, and there only
    on a run the build smooth-shades: no edge of it sharp); the label a whole run would get reads inside its range:
    the warp's crown|logo "16-29°", crown|shoulder "38°" (37.7-39.3; the roll sub-bands' edges, which fold to ~0 at
    the outline, gave "1..39" in probe1), revision 7's "11°" / "20°"."""
    from glasses3d.g3d.temple import build_temple
    if case.startswith("r0007"):
        rp = PACKAGE / "work" / "temple_lab" / "revisions" / "r0007.json"
        need(rp.is_file(), f"the run-7 revision is missing: {rp}")
        spec = json.loads(rp.read_text(encoding="utf-8"))
    else:
        need(PROBE1_HOST.is_file(), f"probe1's specs are missing: {PROBE1_HOST}")
        spec = json.loads(PROBE1_HOST.read_text(encoding="utf-8"))["specs"]["warp_fitted"]
    if case.endswith("soft_shoulder"):
        f = spec["facets"]["shoulder"]
        spec["facets"]["shoulder"] = dict(f, soft=True) if isinstance(f, dict) else {"poly": f, "soft": True}
    tb = build_temple(spec)
    edges = model_edges_of(tb)
    v3 = Seeing._check_v3_args(_Basis, None, edges, spec["hinge"], spec["hinge"]["z_mm"])
    assert {e["pair"] for e in v3["edges"] if e["soft"]} == ({"shoulder|base"} if "soft" in case else set())
    assert all(_sharp_on_run(tb, e)[0] == 0 for e in edges if e["soft"])     # soft = smooth-shaded in this build
    whole = {}
    for e in v3["edges"]:
        assert e["deg_at"] is not None and e["range"] is not None and len(e["deg_at"]) == len(e["pts"])
        run = np.column_stack([np.zeros((len(e["pts"]), 2)), e["deg_at"]])
        text, _shape = C.run_label(run, e["deg"], e["shape"], e["range"])
        assert all(round(e["range"][0]) <= v <= round(e["range"][1]) for v in _label_values(text)), (e["pair"], text)
        whole.setdefault(e["pair"], []).append(text)
    if case.startswith("r0007"):
        assert whole["crown|logo"] == ["11°"] and whole["crown|shoulder"] == ["20°"]
    else:
        assert whole["crown|logo"] == ["16-29°"] and whole["crown|shoulder"] == ["38°"]


def _sharp_on_run(tb, e) -> tuple:
    """(sharp, edges): how many of a model_edges run's edges the build made sharp (tb.sharp: (a, b) a < b, vertex
    indices of tb.V), of how many. The run's points are tb.V's own rounded to 1e-6 mm: each is matched to its vertex
    (within 1e-5 mm) and each consecutive pair must be an edge of the mesh: a wrong match fails, never undercounts."""
    from scipy.spatial import cKDTree

    from glasses3d.g3d.temple.build import mesh_topology
    V = np.asarray(tb.V, float)
    dist, idx = cKDTree(V).query(np.asarray(e["pts"], float))
    assert float(dist.max()) < 1e-5, (e["pair"], float(dist.max()))
    mesh = {(min(a, b), max(a, b)) for a, b in mesh_topology(tb.polys)["directed"]}
    pairs = [(int(min(a, b)), int(max(a, b))) for a, b in zip(idx[:-1], idx[1:])]
    assert all(q in mesh for q in pairs), e["pair"]
    sharp = set(map(tuple, tb.sharp))
    return sum(q in sharp for q in pairs), len(pairs)


def _soft_slab(proud: float = 0.5, soft: bool = True, tilt: float = -0.03) -> dict:
    """tests/test_temple_warp.py soft_spec: a flat facet S on the straight slab (d 20-50, y -5..5), ``proud`` mm above
    the base at its lower corners, leaning ``tilt`` mm per mm of y, with a neighbour facet G sharing its d 50 edge."""
    from glasses3d.g3d.temple import fixtures as FX
    t = lambda y: FX.PRISM["T"] + proud + tilt * (y + 5.0)                      # noqa: E731
    nodes = {"A": [20.0, -5.0, t(-5)], "B": [50.0, -5.0, t(-5)], "C": [50.0, 5.0, t(5)], "D": [20.0, 5.0, t(5)],
             "E": [70.0, -5.0, None], "F": [70.0, 5.0, None]}
    return FX.straight(nodes=nodes, facets={"S": {"poly": ["A", "B", "C", "D"], "soft": soft},
                                            "G": {"poly": ["B", "E", "F", "C"],
                                                  "plane": {"crease": ["B", "C"], "rise_per_mm": -0.02}}})


def _soft_straddle(proud: float = 2.0, rise: float = 0.03) -> dict:
    """tests/test_temple_warp.py soft_straddle: a soft flat facet S (d 20-50, y -5..5) ``proud`` mm at d 20 rising
    ``rise`` per mm of d: its folds with the 6 mm soft band run ~18-30 deg (SOFT_SHARP_DEG 25 straddled)."""
    from glasses3d.g3d.temple import fixtures as FX
    t = lambda d: FX.PRISM["T"] + proud + rise * (d - 20.0)                     # noqa: E731
    nodes = {"A": [20.0, -5.0, t(20)], "B": [50.0, -5.0, t(50)], "C": [50.0, 5.0, t(50)], "D": [20.0, 5.0, t(20)]}
    return FX.straight(nodes=nodes, facets={"S": {"poly": ["A", "B", "C", "D"], "soft": True}})


SOFT_CASES = {"steep_4.5": (lambda: _soft_slab(proud=4.5, tilt=0.0), False),    # 31 of 31 S|base edges sharp
              "straddle_2.4": (lambda: _soft_straddle(2.4, 0.03), False),       # 32 of 32 sharp
              "straddle_2.0": (lambda: _soft_straddle(2.0, 0.03), True),        # 0 sharp
              "slab_0.5": (lambda: _soft_slab(), True),                         # 0 sharp
              "hard_slab": (lambda: _soft_slab(soft=False), False)}


@pytest.mark.parametrize("case", list(SOFT_CASES))
def test_builder_soft_flag_means_smooth_shaded(case):
    """review2-seeing2-1 P2-1, the I1 meaning of soft that seeing's texts rest on ("dashed grey: smooth-shaded, drawn
    without an angle"): a run is soft only when its facet is soft AND the build smooth-shades it, i.e. none of its
    edges is in tb.sharp. A soft border run whose median fold reaches SOFT_SHARP_DEG is built sharp, all of it
    (build._sharp_edges): it goes out as an ordinary crease, drawn in its shape's colour and labelled. Every run of a
    soft facet's border with the base that is not soft has a sharp edge. The builder's own fixtures (test_temple_warp:
    test_a_steep_soft_border_stays_sharp, test_a_soft_border_run_is_one_line_or_none)."""
    from glasses3d.g3d.temple import build_temple
    spec, soft = SOFT_CASES[case]
    s = spec()
    tb = build_temple(s)
    edges = model_edges_of(tb)
    sb = [e for e in edges if e["pair"] == "S|base"]
    assert sb and all(e["soft"] is soft for e in sb), (case, [(e["soft"], _sharp_on_run(tb, e)) for e in sb])
    for e in edges:
        n, m = _sharp_on_run(tb, e)
        if e["soft"]:
            assert n == 0 and e["pair"].endswith("|base"), (e["pair"], n, m)
        elif e["pair"].endswith("|base") and s["facets"][e["pair"].split("|")[0]].get("soft"):
            assert n > 0, (e["pair"], "a soft facet's smooth run must go out soft")
    v3 = Seeing._check_v3_args(_Basis, None, edges, s["hinge"], s["hinge"]["z_mm"])
    drawn = [{"pair": e["pair"], "shape": e["shape"], "deg": e["deg"], "side": 1, "range": e["range"],
              "soft": e["soft"], "runs": [np.column_stack([np.linspace(10.0, 190.0, 50), np.full(50, 20.0 + 15 * i),
                                                          np.linspace(e["range"][0], e["range"][1], 50)])]}
             for i, e in enumerate(v3["edges"])]
    labels = {p for p, _t, _c in C.draw_edges(Image.new("RGB", (200, 20 + 15 * len(drawn)), "white"),
                                              C.edges_with_runs(drawn, [d["runs"] for d in drawn]), labels=True)}
    assert ("S|base" in labels) is (not soft)                                  # a sharp border keeps its label


# the investigation's traced photo lines (w2/crown-geometry/picks.json: centre c, unit direction d, span along d)
PHOTO_LINES = {"black-side": {"logo|lower": ([604.36, 248.44], [0.69513, -0.71889], (-99.73, 105.23)),
                              "crown|logo": ([524.63, 130.53], [1.0, 0.00137], (-189.63, 190.37))},
               "reference-05": {"logo|lower": ([482.17, 336.06], [0.69229, -0.72162], (-37.56, 36.07)),
                                "crown|logo": ([342.4, 302.03], [1.0, 0.00058], (-162.4, 162.6))}}


@pytest.fixture(scope="module")
def v3job(tmp_path_factory):
    from glasses3d.agent.journal import BlenderSession
    from glasses3d.g3d.temple import build_temple
    from glasses3d.g3d_host import blender
    need(blender.blender_exe() is not None, "Blender 5.2 not installed")
    need((PACKAGE / "checkpoints" / "index.jsonl").is_file() and (PACKAGE / "cameras").is_dir(),
         f"the run-7 package is missing: {PACKAGE}")
    root = tmp_path_factory.mktemp("seeing_v3") / "pkg"
    for sub in ("cameras", "checkpoints", "inputs"):
        shutil.copytree(PACKAGE / sub, root / sub)
    spec = json.loads((PACKAGE / "work" / "temple_lab" / "revisions" / "r0007.json").read_text(encoding="utf-8"))
    s = BlenderSession(root, seed_blend=root / "inputs" / "scene.blend", calls_dir=root / "calls")
    s.start()
    assert s.head["id"] == "c0007"
    out = root.parent / "images"
    out.mkdir()
    yield {"root": root, "session": s, "seeing": Seeing(s), "spec": spec, "edges": model_edges_of(build_temple(spec)),
           "images": out}
    s.close()
    if IMAGES_OUT:                                                               # a review copy, on demand only
        shutil.copytree(out, Path(IMAGES_OUT), dirs_exist_ok=True)
    shutil.rmtree(root / "checkpoints", onerror=_rw)


def _near_colours(img: np.ndarray, uv, colours=(C.SOFT_RGB, C.VALLEY_RGB, C.RIDGE_RGB)) -> dict:
    """Per colour, the share of the points uv (image px) with a pixel of exactly that colour within 1 px (a 1 px line
    drawn between recorded points can sit a pixel off their rounding)."""
    out = {}
    for rgb in colours:
        hit = (img[..., :3] == rgb).all(axis=2)
        got = [bool(hit[max(0, int(round(v)) - 1):int(round(v)) + 2, max(0, int(round(u)) - 1):int(round(u)) + 2].any())
               for u, v in np.asarray(uv, float).reshape(-1, 2)]
        out[tuple(rgb)] = float(np.mean(got)) if got else 0.0
    return out


def _line_distance(runs_px: list, c, d, span) -> np.ndarray:
    c, d = np.asarray(c, float), np.asarray(d, float)
    n = np.array([-d[1], d[0]])
    p = np.asarray([q for r in runs_px for q in r], float).reshape(-1, 2)
    along = (p - c) @ d
    keep = (along >= span[0]) & (along <= span[1])
    return np.abs((p[keep] - c) @ n)


ZOOMS = [{"rid": "reference-05", "box": [400, 220, 640, 380], "geometry": True},     # around K in 05
         {"rid": "black-side", "box": [560, 40, 900, 260], "geometry": False}]       # around K in B1
ZOOM_END = {"rid": "black-side", "box": [900, 100, 1100, 240], "geometry": False}    # the zone end in B1: shoulder|base
#                                                                                      (u 965-1039, v 137-202 unposed)


def _assert_stacked_in_order(image: str, row_files: list) -> None:
    """The stacked compare image holds the row files top to bottom in that order (compare.stack_rows: each row under
    a LABEL_PX label strip, 4 px apart), pixel for pixel."""
    st = np.asarray(Image.open(image).convert("RGB"))
    y = 0
    for f in row_files:
        row = np.asarray(Image.open(f).convert("RGB"))
        h, w = row.shape[:2]
        assert np.array_equal(st[y + C.LABEL_PX:y + C.LABEL_PX + h, :w], row), (image, f, y)
        y += C.LABEL_PX + h + 4
    assert y - 4 == st.shape[0]


def _head(see, bs):
    exp = see.export_head()
    mesh = see.fit_mesh(exp["path"], exp["sha256"], skip=bs.skip, frame=bs.frame)
    roles = PO.part_roles(exp["path"])
    return mesh, roles, {s: PO.temple_body(mesh, s, roles) for s in (1, -1)}


def test_compare_with_model_edges_and_zoom_on_the_run7_package(v3job):
    see, spec, edges = v3job["seeing"], v3job["spec"], v3job["edges"]
    s = v3job["session"]
    fp0 = s.fingerprint()
    pairs = {e["pair"] for e in edges}
    assert {"crown|logo", "logo|lower", "crown|shoulder", "lower|shoulder", "logo|nose", "shoulder|base"} <= pairs
    assert not any(e["soft"] for e in edges)                                     # revision 7 has no soft facet
    rng = defaultdict(list)                                                      # each pair's angles as the build gives
    for e in edges:
        rng[e["pair"]] += e["range"]
    soft = _soft(edges, "shoulder|base")                                         # drawn as a soft border
    ramp = [dict(e, deg_at=np.linspace(10.0, 40.0, len(e["pts"])).tolist(), range=[10.0, 40.0])
            if e["pair"] == "crown|logo" else e for e in edges]                  # plumbing: a per-point angle
    zooms = ZOOMS + [ZOOM_END]
    r = see.compare_to_photos(["black-side", "reference-05"], passes=("native_linear",), rig="studio_dome_v1",
                              hinge_z_mm=spec["hinge"]["z_mm"], model_edges=soft, opening=spec["hinge"],
                              zoom=zooms)
    assert r["status"] == "ok", r
    rep = json.loads(Path(r["report"]).read_text(encoding="utf-8"))
    assert rep["schema"] == "g3d.photo_compare/4" and rep["model_edges"]["edges"] == len(edges)
    assert rep["model_edges"]["on_model_mm"] < 1e-3                              # the edges lie on the head
    assert rep["model_edges"]["soft_pairs"] == ["shoulder|base"]
    assert set(r["photos"]["reference-05"]["rows"]) == {"photo", "native_linear", "overlay"}       # target
    assert set(r["photos"]["black-side"]["rows"]) == {"photo", "overlay", "gloss_black"}           # other colour
    for rid, order in (("reference-05", ["photo", "native_linear", "overlay"]),        # one row order (v3p3 review:
                       ("black-side", ["photo", "gloss_black", "overlay"])):           # B1 stacked photo / overlay /
        assert list(r["photos"][rid]["rows"]) == order                                 # render)
        _assert_stacked_in_order(r["photos"][rid]["image"], [r["photos"][rid]["rows"][n] for n in order])
    for rid, render_row in (("black-side", "gloss_black"), ("reference-05", "native_linear")):
        rec = rep["photos"][rid]
        x0, y0, x1, y1 = rec["crop"]["box"]
        assert rec["crop"]["k"] == pytest.approx(C.stack_scale(x1 - x0, y1 - y0, 3))              # rows stacked
        im = Image.open(r["photos"][rid]["image"])
        assert max(im.size) <= 1024 and (max(im.size) >= 1014 or rec["crop"]["k"] == 1.0)   # the room is used
        assert rec["creases"]["source"] == "model edges" and rec["creases"]["ridge"] >= 3
        row = np.asarray(Image.open(rec["rows"][render_row]).convert("RGB"))
        assert (row == C.OUTLINE_RGB).all(axis=2).sum() > 200                     # the render row has the outline
        assert 0.0 <= rec["render_backdrop_share"][render_row] <= 1.0
        assert "of the model's pixels show in the backdrop's colour" in r["photos"][rid]["text"]
        me = {(e["pair"], e["side"]): e for e in rec["model_edges"]}
        for pr in ("crown|logo", "logo|lower", "crown|shoulder"):
            assert me[(pr, "charted")]["shown_px"] > 40, (rid, pr)               # the near temple's edges show
        sb = me[("shoulder|base", "charted")]                                    # the soft border: dashed grey
        assert sb["soft"] and sb["shown_px"] > 10 and "deg_shown" not in sb and rec["creases"]["soft"] >= 1
        assert rec["creases"]["valley"] == sum(1 for e in rec["model_edges"] if e["shape"] == "valley"
                                               and e["runs_px"] and not e.get("soft"))
        assert (f"; soft borders ({SOFT_TEXT}): shoulder|base" in r["photos"][rid]["text"])   # plumbing (_soft)
        ov = np.asarray(Image.open(rec["rows"]["overlay"]).convert("RGB"))
        k = rec["crop"]["k"]
        near = _near_colours(ov, (np.asarray([q for run in sb["runs_px"] for q in run]) - [x0, y0] + 0.5) * k - 0.5)
        assert near[C.SOFT_RGB] >= 0.6 and near[C.VALLEY_RGB] <= 0.3, (rid, near)
        for e in rec["model_edges"]:                                             # drawn angles inside the build's
            if "deg_shown" in e:
                assert e["range"][0] - 0.0051 <= e["deg_shown"][0] <= e["deg_shown"][1] <= e["range"][1] + 0.0051, e
        far = sum(e["shown_px"] for e in rec["model_edges"] if e["side"] == "mirrored")
        near = sum(e["shown_px"] for e in rec["model_edges"] if e["side"] == "charted")
        assert far < 0.02 * near, (rid, far, near)      # 05: hidden by its own body (seen from inside); B1: off image
        out = rec["outline_offsets_mm"]
        assert set(out) == {"charted", "mirrored"}
        ch, mi = out["charted"], out["mirrored"]
        assert ch["facing"] and 13.0 < ch["elevation_deg"] < 16.5 and 0.10 < ch["mm_per_px"][0] <= \
            ch["mm_per_px"][1] < 0.16
        assert [o["d_mm"] for o in ch["bottom"]][:3] == [10.0, 20.0, 30.0] and len(ch["bottom"]) >= 12
        assert all(o["offset_mm"] is None and o["reason"] == "top face in view" for o in ch["top"])
        assert sum(o["offset_mm"] is not None for o in ch["top_silhouette"]) >= len(ch["top"]) - 3   # kept, printed
        bottom = [o["offset_mm"] for o in ch["bottom"][:7]]                       # d 10-70: the facet block
        assert sum(v is not None for v in bottom) >= 6 and all(abs(v) <= 0.5 for v in bottom if v is not None), \
            (rid, bottom)
        assert not mi["facing"] and all(o["reason"] == "seen from inside" for o in mi["top"] + mi["bottom"])
        text = r["photos"][rid]["text"]
        assert "outline of the charted temple" in text and "top: not measured" not in text and "n/a" not in text
        assert far_head("top", "+ = the photo's edge higher", ch["elevation_deg"]) + ", less its median " in text
        # run 7's head (revision 7 = the original's rear arm, run7-residual §2): the far edge steps 2-4 mm behind
        # d 100 (B1 +2.5 / +3.6, 05 +2.4 / +3.2 at d 110 / 120) and the text prints it; d 40-90 on the level
        far = {o["d_mm"]: o["offset_mm"] for o in ch["top_silhouette"] if o["offset_mm"] is not None}
        level = float(np.median(list(far.values())))
        assert all(abs(far[d] - level) <= 0.3 for d in (40.0, 50.0, 60.0, 70.0, 80.0, 90.0)), (rid, far)
        assert far[110.0] - level >= 2.0 and far[120.0] - level >= 3.0, (rid, far)
        assert f"110 {far[110.0] - level:+.1f}, 120 {far[120.0] - level:+.1f}" in text
        assert "outline: the mirrored temple is seen from inside here" in text
    # known creases on their photo lines (the traced KC and AK lines in B1 and 05)
    for rid, lines in PHOTO_LINES.items():
        me = {(e["pair"], e["side"]): e for e in rep["photos"][rid]["model_edges"]}
        for pr, lim in (("logo|lower", 2.0), ("crown|logo", 4.0)):
            c, d, span = lines[pr]
            dist = _line_distance(me[(pr, "charted")]["runs_px"], c, d, span)
            assert len(dist) >= 3 and float(np.median(dist)) <= lim, (rid, pr, np.median(dist), dist.max())
    # zooms
    assert [z["rid"] for z in r["zoom"]] == ["reference-05", "black-side", "black-side"]
    for zr, zq, ztext in zip(rep["zoom"], zooms, [z["text"] for z in r["zoom"]]):
        x0, y0, x1, y1 = zq["box"]
        k = C.zoom_scale(x1 - x0, y1 - y0)
        assert zr["k"] == pytest.approx(k) and zr["box"] == zq["box"] and k == 2.0
        w, h = round((x1 - x0) * k), round((y1 - y0) * k)
        assert zr["shape"] == [h, w] and zr["in_box"] == "temple"
        im = Image.open(zr["image"])
        assert im.size == (2 * w + 3 * 6, 2 * (h + C.LABEL_PX) + 3 * 6)        # 2 x 2 panels at k, never scaled
        want = {"photo", "render", "overlay", "geometry" if zq["geometry"] else "render_edges"}
        assert set(zr["panels"]) == want and all(Image.open(p).size == (w, h) for p in zr["panels"].values())
        assert zr["stretch"] == rep["photos"][zq["rid"]]["stretch"]               # the compare's stretch
        rp = np.asarray(Image.open(zr["panels"]["render"]).convert("RGB"))
        assert (rp == C.OUTLINE_RGB).all(axis=2).any() and 0.0 <= zr["render_backdrop_share"] <= 1.0
        labels = {e["pair"]: e["text"] for e in zr["edge_labels"]}
        assert {"crown|logo", "logo|lower"} <= set(labels) if zq is not ZOOM_END else \
            {"crown|shoulder", "lower|shoulder"} <= set(labels)
        assert "shoulder|base" not in labels                                     # a soft border has no angle label
        for p, t in labels.items():                                              # the build's own angles, as given:
            lo, hi = round(min(rng[p])), round(max(rng[p]))                      # never a roll-band number
            assert all(lo <= v <= hi for v in _label_values(t)), (zq["box"], p, t, rng[p])
        assert labels.get("crown|logo", "11°") == "11°" and labels.get("crown|shoulder", "20°") == "20°"
        for e in zr["model_edges"]:
            if "deg_shown" in e:
                assert e["range"][0] - 0.0051 <= e["deg_shown"][0] <= e["deg_shown"][1] <= e["range"][1] + 0.0051, e
        assert f"k {k:.2f}" in zr["text"] and "px/mm in this image" in zr["text"] and \
            f"[{x0},{y0},{x1},{y1}]" in zr["text"] and f"compare_id {r['compare_id']}." in zr["text"]
        assert "nan" not in zr["text"] and ztext == zr["text"]
    geo = np.asarray(Image.open(rep["zoom"][0]["panels"]["geometry"]).convert("RGB")).astype(int)
    assert geo.std(axis=(0, 1)).max() > 20                                       # facets differ in colour
    # the zone end in B1: the shoulder|base border marked soft (plumbing: _soft; the head's border is sharp, so the
    # render panel shows a hard line under the dash) dashed grey in the overlay and render + edges panels
    ze = rep["zoom"][2]
    sbz = next(e for e in ze["model_edges"] if e["pair"] == "shoulder|base" and e["side"] == "charted")
    assert sbz["soft"] and sbz["shown_px"] > 40 and "deg_shown" not in sbz
    assert f"soft borders shown ({SOFT_TEXT}): shoulder|base" in ze["text"]
    assert "dashed grey = soft border" in ze["panel_labels"]["overlay"]
    assert "(dashed grey = soft border)" in ze["panel_labels"]["render_edges"]
    zuv = (np.asarray([q for run in sbz["runs_px"] for q in run]) - ZOOM_END["box"][:2] + 0.5) * 2.0 - 0.5
    for panel in ("overlay", "render_edges"):
        near = _near_colours(np.asarray(Image.open(ze["panels"][panel]).convert("RGB")), zuv)
        assert near[C.SOFT_RGB] >= 0.6 and near[C.VALLEY_RGB] <= 0.3, (panel, near)
    for zr in rep["zoom"][:2]:                                                   # not in the boxes around K
        assert "soft borders" not in zr["text"] and "dashed grey" not in zr["panel_labels"]["overlay"]
    # photo_names (v3p3 review: the image said "black-side photo" where the lab's texts say B1): the name titles the
    # photo row and zoom panel and heads the texts, the rest of the image is unchanged, and it is in the cache key
    named = see.compare_to_photos(["black-side"], passes=("native_linear",), rig="studio_dome_v1",
                                  hinge_z_mm=spec["hinge"]["z_mm"], model_edges=soft, opening=spec["hinge"],
                                  zoom=[ZOOMS[1]], photo_names={"black-side": "B1", "reference-05": "05"})
    assert named["status"] == "ok" and not named["photos"]["black-side"]["cached"] and not named["zoom"][0]["cached"]
    nrep = json.loads(Path(named["report"]).read_text(encoding="utf-8"))
    nb = nrep["photos"]["black-side"]
    assert nb["name"] == "B1" and nb["rid"] == "black-side" and nb["text_head"].startswith("B1 (side, ")
    assert rep["photos"]["black-side"]["name"] == "black-side"                   # none given: the basis name
    a = np.asarray(Image.open(named["photos"]["black-side"]["image"]).convert("RGB")).astype(int)
    b = np.asarray(Image.open(r["photos"]["black-side"]["image"]).convert("RGB")).astype(int)
    diff = np.flatnonzero((a != b).any(axis=(1, 2)))
    assert a.shape == b.shape and len(diff) and diff.max() < C.LABEL_PX       # only the photo row's title differs
    nz = nrep["zoom"][0]
    assert nz["name"] == "B1" and nz["panel_labels"]["photo"].startswith("B1 photo (stretch ")
    assert named["zoom"][0]["text"].startswith("zoom 1 on B1 (side, ") and "black-side" not in named["zoom"][0]["text"]
    assert rep["zoom"][1]["panel_labels"]["photo"].startswith("black-side photo (stretch ")
    # the cache: same call -> cached, texts name the new compare
    again = see.compare_to_photos(["black-side", "reference-05"], passes=("native_linear",), rig="studio_dome_v1",
                                  hinge_z_mm=spec["hinge"]["z_mm"], model_edges=soft, opening=spec["hinge"],
                                  zoom=zooms)
    assert again["status"] == "ok" and all(p["cached"] for p in again["photos"].values())
    assert all(z["cached"] for z in again["zoom"]) and all(f"compare_id {again['compare_id']}." in z["text"]
                                                          for z in again["zoom"])
    # a zoom-only call: edges in the key, a per-point angle labelled "a-b°", the stretch = the photo's compare one
    other = see.compare_to_photos([], passes=("native_linear",), rig="studio_dome_v1",
                                  hinge_z_mm=spec["hinge"]["z_mm"], model_edges=ramp, zoom=ZOOMS[:1])
    assert other["status"] == "ok" and other["photos"] == {} and not other["zoom"][0]["cached"]
    orep = json.loads(Path(other["report"]).read_text(encoding="utf-8"))["zoom"][0]
    assert orep["stretch"] == rep["photos"]["reference-05"]["stretch"]
    lab = {e["pair"]: e["text"] for e in orep["edge_labels"]}
    lo, hi = _label_values(lab["crown|logo"])
    assert 10 <= lo < hi <= 40 and hi - lo >= 2, lab                             # the visible part of the ramp
    cl = [e for e in orep["model_edges"] if e["pair"] == "crown|logo" and e["side"] == "charted"]
    assert cl and 10.0 <= cl[0]["deg_shown"][0] < cl[0]["deg_shown"][1] <= 40.0
    # the opening is in the key without model edges too (review: a record cached under another opening)
    plain = see.compare_to_photos(["reference-05"], passes=("native_linear",), rig="studio_dome_v1",
                                  hinge_z_mm=spec["hinge"]["z_mm"])
    opened = see.compare_to_photos(["reference-05"], passes=("native_linear",), rig="studio_dome_v1",
                                   hinge_z_mm=spec["hinge"]["z_mm"], opening=(5.0, spec["hinge"]["axis_xz_mm"]))
    assert plain["status"] == opened["status"] == "ok" and not opened["photos"]["reference-05"]["cached"]
    po, oo = (json.loads(Path(x["report"]).read_text(encoding="utf-8"))["photos"]["reference-05"]
              ["outline_offsets_mm"]["charted"]["bottom"] for x in (plain, opened))
    assert [o["offset_mm"] for o in po] != [o["offset_mm"] for o in oo]
    # without model edges: the old mesh rule, both target rows when asked (owner sheets), bare render rows
    old = see.compare_to_photos(["reference-05"], passes=("native_linear", "gloss_black"), rig="studio_dome_v1",
                                hinge_z_mm=spec["hinge"]["z_mm"])
    assert old["status"] == "ok" and set(old["photos"]["reference-05"]["rows"]) == {
        "photo", "native_linear", "overlay", "gloss_black"}
    orec = json.loads(Path(old["report"]).read_text(encoding="utf-8"))["photos"]["reference-05"]
    x0, y0, x1, y1 = orec["crop"]["box"]
    assert orec["creases"]["source"] == "mesh dihedrals" and orec["model_edges"] is None
    assert orec["crop"]["k"] == pytest.approx(C.stack_scale(x1 - x0, y1 - y0, 4))
    assert orec["render_backdrop_share"] is None
    row = np.asarray(Image.open(orec["rows"]["native_linear"]).convert("RGB"))
    assert not (row == C.OUTLINE_RGB).all(axis=2).any()                          # owner sheets: the bare render
    assert s.fingerprint() == fp0
    out = v3job["images"]
    for rid, p in r["photos"].items():
        shutil.copy2(p["image"], out / f"compare_{rid}_model_edges.png")
    for n, z in enumerate(r["zoom"], 1):
        shutil.copy2(z["image"], out / f"zoom{n}_{z['rid']}.png")
    shutil.copy2(other["zoom"][0]["image"], out / "zoom_reference-05_deg_at_ramp.png")
    shutil.copy2(old["photos"]["reference-05"]["image"], out / "compare_reference-05_mesh_rule.png")
    C.write_json(out / "texts.json", {"compare": {rid: p["text"] for rid, p in r["photos"].items()},
                                      "zoom": [z["text"] for z in r["zoom"]] + [other["zoom"][0]["text"]]})


def test_side_outline_reads_zero_on_the_models_own_render(v3job):
    """Review F1's self-test: the instrument on the model itself. Rendered at 05's and 01's own cameras at k 1 (the
    whole photo), composited over the photo's backdrop with the job's gain as the compare row is, and matted with the
    photo's own matte rule (glossy_floor), the side outline must read every station it measures within 1 native px
    (only the camera-facing temple's bottom here: the views look 14-15 deg down). The top silhouette reads <= -1 mm
    there (the white top face lost to the matte) and 0 against the render's own alpha: the reason the top is not
    measured as an outline. Minus its median (what ``_far_edge_text`` prints, v3p3-verify-8) it reads within 1 mm at
    every station under both: the matte's cut is shared along the arm, so a printed 2-4 mm step is the photo's."""
    from glasses3d.g3d_host import matte as M
    see, spec, edges = v3job["seeing"], v3job["spec"], v3job["edges"]
    bs = C.load_basis(v3job["root"])
    mesh, roles, bodies = _head(see, bs)
    hz = spec["hinge"]["z_mm"]
    opening = Seeing._check_v3_args(bs, None, None, spec["hinge"], hz)["opening"]
    checked = 0
    for rid, near in (("reference-05", "charted"), ("reference-01", "mirrored")):
        H, W = bs.shape(rid)
        r = see.compare_to_photos([rid], passes=("native_linear",), rig="studio_dome_v1", hinge_z_mm=hz,
                                  model_edges=edges, opening=spec["hinge"],
                                  boxes={rid: {"box": [0, 0, W, H], "k": 1.0}}, use_cache=False)
        assert r["status"] == "ok", r
        rep = json.loads(Path(r["report"]).read_text(encoding="utf-8"))
        nat = C.read_linear(rep["photos"][rid]["renders"]["native_linear"]["path"])
        gain = rep["gains"]["colourways"]["target"]["native_linear"]["gain"]
        synth = C.composite_render(nat[0], nat[1], gain, C.photo_background(bs.photo(rid), bs.matte(rid)))
        assert synth.shape[:2] == (H, W)
        mode = bs.refs[rid].get("matte_mode") or "default"
        g = Seeing._matched_geometry(bs, rid, mesh, roles)
        for matte, own_alpha in ((M.matte(synth, mode=mode), False), (nat[1] > 0.5, True)):
            out = Seeing._side_outline(bs, rid, g, mesh, roles, bodies, hz, opening, matte=matte)
            o = out[near]
            assert o["facing"] and not out["charted" if near == "mirrored" else "mirrored"]["facing"]
            got = [x for x in o["top"] + o["bottom"] if x["offset_mm"] is not None]
            assert len(got) >= 10 and all(x in o["bottom"] for x in got)
            assert all(abs(x["offset_mm"]) <= 1.0 / x["px_per_mm"] + 0.005 + 1e-9 for x in got), (rid, own_alpha, got)
            sil = float(np.median([x["offset_mm"] for x in o["top_silhouette"] if x["offset_mm"] is not None]))
            assert (abs(sil) <= o["mm_per_px"][1] + 0.005) if own_alpha else sil <= -1.0, (rid, own_alpha, sil)
            # what _far_edge_text prints: minus the median, the model against itself reads within 1 mm at every
            # station (the matte's cut is shared along the arm), so the photos' +2.4..+3.6 at d 110-120 is the arm
            dev = {x["d_mm"]: round(x["offset_mm"] - sil, 2) for x in o["top_silhouette"] if x["offset_mm"] is not None}
            assert len(dev) >= 8 and all(abs(v) <= 1.0 for v in dev.values()), (rid, own_alpha, sil, dev)
            checked += len(got)
    assert checked >= 40


def test_the_frame_hides_edges_on_the_real_head(v3job):
    """A part other than the temple's own body hides model edges on the real head (review P3-1). No basis photo shows
    one temple hiding the other's edges, and none can: the edges lie on the outer faces, which face away from each
    other, so a camera that sees a temple's outer face has the other temple behind it (probe seeing-fix/f2: every
    hidden sample in the 9 photos is hidden by its own temple, the front or a lens). At 02 the front and the left lens
    are the first hit for about half of the mirrored temple's edge vertices (phase 1's edges with the roll band: 91 of
    190; the builder's face-to-face edges, I1: 58 of 123), and the compare draws none of that side, while the charted
    temple's edges show."""
    see, spec, edges = v3job["seeing"], v3job["spec"], v3job["edges"]
    bs = C.load_basis(v3job["root"])
    mesh, roles, _bodies = _head(see, bs)
    hz = spec["hinge"]["z_mm"]
    v3 = Seeing._check_v3_args(bs, None, edges, spec["hinge"], hz)
    g = Seeing._matched_geometry(bs, "reference-02", mesh, roles)
    posed = Seeing._edges_posed(v3["edges"], g["pose"], hz, v3["opening"])
    scene = g["mesh"].scene()
    temples = PO.temple_parts(g["mesh"], 1, roles) + PO.temple_parts(g["mesh"], -1, roles)
    shown = {}
    for side in (1, -1):
        P = np.vstack([e["P"] for e in posed if e["side"] == side])
        uv = project_mm(P, g["cam"], bs.frame)
        hit = scene.cast(g["cam"], uv[:, 0], uv[:, 1])
        hidden = hit["hit"] & (C.to_norm_depth(P, g["cam"], bs.frame) > hit["depth"] + C.CREASE_DEPTH_TOL_MM)
        other = hidden & ~np.isin(g["mesh"].part[np.maximum(hit["face_id"], 0)], temples)
        runs = C.edge_runs([e["P"] for e in posed if e["side"] == side], g["cam"], bs.frame, scene, g["shape"])
        shown[side] = (int(other.sum()), len(P), sum(float(np.linalg.norm(np.diff(r, axis=0), axis=1).sum())
                                                     for rs in runs for r in rs))
    assert shown[-1][0] >= 0.4 * shown[-1][1] and shown[-1][2] < 5.0, shown     # the frame hides the far temple
    assert shown[1][0] == 0 and shown[1][2] > 500.0, shown                       # the near temple's edges show


def test_v3_arguments_are_refused_and_journaled(v3job):
    see, spec = v3job["seeing"], v3job["spec"]
    tools = v3job["root"] / "calls"
    n0 = len(list(tools.iterdir())) if tools.exists() else 0   # first on its xdist worker: no call journaled yet
    edge = v3job["edges"][:1]
    hz = spec["hinge"]["z_mm"]
    for kw in (dict(model_edges=edge), dict(zoom=[{"rid": "reference-05", "box": [0, 0, 4, 4]}]),
               dict(zoom=[{"rid": "nope", "box": [0, 0, 40, 40]}]), dict(rig="photo_v1", model_edges=edge,
                                                                       hinge_z_mm=-10.6),
               dict(photo_names={"nope": "N1"}), dict(rig="photo_v1", photo_names={"reference-05": "05"})):
        kw = {"rig": "studio_dome_v1", **kw}
        r = see.compare_to_photos(["reference-05"], **kw)
        assert r["status"] == "error" and r["code"] == "E_ARGS", (kw, r)
    # edges that do not lie on the head: a hinge_z_mm 1 mm off, an opening the head does not have
    for kw in (dict(hinge_z_mm=hz + 1.0), dict(hinge_z_mm=hz, opening=(5.0, spec["hinge"]["axis_xz_mm"]))):
        r = see.compare_to_photos(["reference-05"], rig="studio_dome_v1", model_edges=v3job["edges"], **kw)
        assert r["status"] == "error" and r["code"] == "E_ARGS" and "do not lie on the head" in r["message"], r
    assert len(list(tools.iterdir())) == n0 + 8
    assert math.isfinite(hz)
