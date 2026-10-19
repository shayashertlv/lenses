"""WP2 stage B, host only: camera bases and comparison metrics (g3d_host/compare.py, glasses3d/plan/archive/20261019/WP2_DESIGN.md §6).

* importing the frozen WP3b Prada cameras.json freezes basis-01 (mattes once, ROI checked, photos sha-checked); a
  basis is never overwritten, a changed matte or photo is refused, the next import is basis-02;
* the host metrics on the Studio delivery GLB (the model the basis was fitted to) reproduce cameras.json exactly for
  all six references (the Blender half of the chain is in test_wp2_seeing.py);
* deltas, sheets and the parts image behave as documented.
Data-dependent (Prada WP3b cameras and reference photos): skipped, or failed under G3D_REQUIRE_DATA=1.

WP6_FACETS §3.1/§3.3 on synthetic data (always run): a basis carrying the optional pose fields and a supplied matte
round-trips (an old-style record still loads, without a pose); the photo-matched pieces — sRGB, linear gain and its
region fallback, the per-channel white balance, the monotone hue-keeping stretch, compositing (scalar or per-channel
gain; float forms without 8-bit rounding), the float .npz reader, crease edges (ridge vs valley) and their occlusion,
the chart grid (labels per side and level, none overlapping), outer-edge offsets, the stack scale and the crop
box."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from glasses3d.g3d_host import compare as C
from glasses3d.g3d_host import fitmesh
from glasses3d.tests.fixtures import legacy_prada

G3D = Path(__file__).resolve().parents[1]
CAMERAS_JSON = G3D / "data" / "wp3b" / "prada" / "cameras.json"
PHOTOS = G3D / "data" / "reference" / "prada"
REQUIRE_DATA = os.environ.get("G3D_REQUIRE_DATA") == "1"


def need(ok: bool, why: str) -> None:
    if not ok:
        (pytest.fail if REQUIRE_DATA else pytest.skip)(why)


@pytest.fixture(scope="module")
def imported(tmp_path_factory):
    need(CAMERAS_JSON.is_file() and (PHOTOS / "model.glb").is_file(), "WP3b Prada data missing")
    root = tmp_path_factory.mktemp("wp2compare")
    fixture = legacy_prada.prepare(root / "legacy-inputs")
    camera_path = fixture["cameras"]
    doc = json.loads(camera_path.read_text(encoding="utf-8"))
    photos = {n: PHOTOS / n for n in doc["images"]}
    b = C.import_cameras_json(root, camera_path, photos, reason="frozen WP3b basis")
    return {"root": root, "doc": doc, "photos": photos, "basis": b, "cameras": camera_path,
            "source_sha256": fixture["receipt"]["sources"][str(CAMERAS_JSON)]}


def test_import_freezes_basis_01(imported):
    b, doc = imported["basis"], imported["doc"]
    assert b.id == "basis-01" and C.list_bases(imported["root"]) == ["basis-01"]
    assert b.meta["schema"] == C.BASIS_SCHEMA and b.meta["notes"] == [] and b.meta["reason"] == "frozen WP3b basis"
    assert b.meta["cameras_json_sha256"] == C.sha256_file(imported["cameras"])
    assert C.sha256_file(CAMERAS_JSON) == imported["source_sha256"] == legacy_prada.CAMERAS_SHA
    assert b.skip == ("hallmark", "etch", "ribbon") and len(b.front_parts) == 3
    assert list(b.refs) == [f"reference-0{i}" for i in range(1, 7)]
    for name, rec in doc["images"].items():
        r = b.refs[C.ref_id(name)]
        assert r["camera"] == rec["camera"] and r["roi"] == rec["roi"] and r["shape"] == rec["shape"]
        assert r["photo_sha256"] == rec["image_sha256"] and r["fit"]["iou"] == rec["iou"]
        assert b.matte(C.ref_id(name)).sum() == r["fg_px"] == rec["matte"]["foreground_px"]
        assert (b.dir / r["overlay"]).is_file()


def test_host_metrics_reproduce_cameras_json_on_the_fitted_glb(imported):
    b, doc = imported["basis"], imported["doc"]
    mesh = fitmesh.load_fit_mesh(PHOTOS / "model.glb", skip=b.skip, frame=b.frame)
    for name, rec in doc["images"].items():
        m, masks = C.reference_metrics(mesh, b, C.ref_id(name))
        a = m["all"]
        assert a["iou"] == rec["iou"] and a["contour_mean_px"] == rec["contour_mean_px"], name
        assert a["contour_p90_px"] == rec["contour_p90_px"] and a["contour_p95_px"] == rec["contour_p95_px"], name
        assert a["contour_mean_mm"] == pytest.approx(rec["contour_mean_mm"], rel=1e-12)
        assert a["model_only_px"] == rec["render_only_px"] and a["photo_only_px"] == rec["photo_only_px"]
        assert m["px_per_mm"] == pytest.approx(rec["px_per_mm_at_front_pivot"], rel=1e-12)
        assert 0.5 < m["front"]["iou"] <= 1.0 and masks["mask"].shape == tuple(rec["shape"])


def test_a_basis_is_frozen(imported, tmp_path):
    root, photos = imported["root"], imported["photos"]
    camera_path = imported["cameras"]
    with pytest.raises(FileExistsError):
        C.import_cameras_json(root, camera_path, photos, reason="again", basis_id="basis-01")
    b2 = C.import_cameras_json(root, camera_path, photos, reason="again")
    assert b2.id == "basis-02" and C.load_basis(root).id == "basis-02" and C.load_basis(root, "basis-01").id == \
        "basis-01"
    p = b2.dir / b2.refs["reference-01"]["matte"]
    Image.fromarray(np.zeros_like(np.asarray(Image.open(p)))).save(p)
    with pytest.raises(ValueError, match="matte differs"):
        C.load_basis(root, "basis-02").matte("reference-01")
    bad = dict(photos)
    bad["reference-01.webp"] = photos["reference-02.webp"]
    with pytest.raises(ValueError, match="sha256"):
        C.import_cameras_json(tmp_path, camera_path, bad, reason="wrong photo")
    with pytest.raises(C.NoBasis, match="fit_cameras"):
        C.load_basis(tmp_path / "empty")
    shutil.rmtree(b2.dir)


def test_deltas_sheets_and_parts_image(imported):
    cur = {"all": {"iou": 0.8, "contour_mean_mm": 1.0, "contour_p90_mm": 2.0, "iou_full_image": 0.79}}
    prev = {"all": {"iou": 0.9, "contour_mean_mm": 0.5, "contour_p90_mm": 2.5, "iou_full_image": 0.9}}
    d = C.deltas(cur, prev)
    assert d["all"]["iou"] == pytest.approx(-0.1) and d["all"]["contour_p90_mm"] == pytest.approx(-0.5)
    assert C.deltas(cur, None) is None
    tall, wide = Image.new("RGBA", (300, 900), (1, 2, 3, 255)), Image.new("RGBA", (900, 120), (1, 2, 3, 255))
    g = C.grid([wide, wide, tall, tall], 2, max_px=10_000, gap=6)
    assert g.size == (900 + 900 + 18, 120 + 900 + 18)                       # rows as tall as their own tallest
    s = C.contact_sheet([("a", [tall, tall]), ("b", [wide])] * 4)
    assert max(s.size) <= 2048
    b = imported["basis"]
    mesh = fitmesh.load_fit_mesh(PHOTOS / "model.glb", frame=b.frame)
    img, legend = C.parts_image(mesh, b.camera("reference-04"), b.shape("reference-04"))
    assert img.size == tuple(reversed(b.shape("reference-04"))) and len(legend) >= 5
    assert any(n.startswith("Front") for n in legend)
    box, names = C.part_box(mesh, b.camera("reference-01"), b.shape("reference-01"), "temple right")
    assert names and box[2] > box[0] and box[3] > box[1]
    with pytest.raises(ValueError, match="no part matches"):
        C.part_box(mesh, b.camera("reference-01"), b.shape("reference-01"), "nonexistent")


# ------------------------------------------------------------------------------------------------ WP6 v2 (synthetic)
CAM = {"yaw": 90.0, "pitch": 10.0, "roll": 0.0, "perspective": 0.1, "scale": 500.0, "center_x": 40.0,
       "center_y": 30.0}


def _photo(path, box, shape=(60, 80)):
    a = np.full(shape + (3,), 250, np.uint8)
    x0, y0, x1, y1 = box
    a[y0:y1, x0:x1] = 40
    Image.fromarray(a).save(path)
    return a


def test_basis_carries_pose_fields_and_old_records_still_load(tmp_path):
    src = tmp_path / "src"
    (src / "mattes").mkdir(parents=True)
    p1, p2 = src / "reference-01.png", src / "black-side.png"
    _photo(p1, (20, 15, 60, 45))
    _photo(p2, (10, 10, 50, 40))
    m = np.zeros((60, 80), np.uint8)
    m[10:40, 10:50] = 255
    Image.fromarray(m).save(src / "mattes" / "black-side.png")
    axis = {"+1": [64.6, -10.1], "-1": [-64.6, -10.1]}
    doc = {"model": {"frame": {"center": [0.0, 0.0, -70.0], "extent": 150.0}, "front_pivot_mm": [0.0, 0.0, 0.0],
                     "skip": ["etch"], "front_parts": None},
           "images": {"reference-01.png": {"shape": [60, 80], "camera": CAM, "view_kind": "side", "view": "left"},
                      "black-side.png": {"shape": [60, 80], "camera": CAM, "view_kind": "side", "view": "left",
                                         "matte_mode": "supplied", "matte_file": "mattes/black-side.png",
                                         "matte_file_sha256": C.sha256_file(src / "mattes" / "black-side.png"),
                                         "matte_note": "synthetic", "theta_deg": {"+1": 2.0, "-1": 1.0},
                                         "theta_observed": {"+1": False, "-1": False}, "hinge_axis_xz_mm": axis,
                                         "colourway": "variant", "fit_status": "camera transported from 05"}}}
    C.write_json(src / "cameras.json", doc)
    photos = {"reference-01.png": p1, "black-side.png": p2}
    b = C.import_cameras_json(tmp_path / "job", src / "cameras.json", photos, reason="synthetic")
    assert b.pose("reference-01") is None and b.theta("reference-01") is None            # an old-style record
    assert b.colourway("reference-01") == "target" and b.refs["reference-01"]["matte_mode"] == "default"
    m1 = b.matte("reference-01")                                    # the default matte (opening rounds corners)
    assert m1[16:44, 21:59].all() and abs(int(m1.sum()) - 30 * 40) <= 8 and not m1[:, :18].any()
    assert b.pose("black-side") == {"+1": {"theta_deg": 2.0, "axis_xz_mm": [64.6, -10.1]},
                                    "-1": {"theta_deg": 1.0, "axis_xz_mm": [-64.6, -10.1]}}
    assert b.colourway("black-side") == "variant" and b.refs["black-side"]["theta_observed"]["+1"] is False
    assert b.refs["black-side"]["fit_status"] == "camera transported from 05"
    assert b.refs["black-side"]["matte_mode"] == "supplied" and b.matte("black-side").sum() == 30 * 40
    again = C.load_basis(tmp_path / "job")
    assert again.refs == b.refs and again.pose("black-side") == b.pose("black-side")
    doc["images"]["black-side.png"]["matte_file_sha256"] = "0" * 64
    C.write_json(src / "cameras.json", doc)
    with pytest.raises(ValueError, match="matte_file_sha256"):
        C.import_cameras_json(tmp_path / "job2", src / "cameras.json", photos, reason="tampered")


def test_srgb_gain_and_gain_region():
    x = np.linspace(0.0, 1.0, 101)
    assert np.abs(C.srgb_to_linear(C.linear_to_srgb8(x)) - x).max() < 0.01
    assert C.linear_to_srgb8(np.array([-1.0, 0.0, 1.0, 2.0])).tolist() == [0, 0, 255, 255]
    photo = np.full((40, 40, 3), 200, np.uint8)
    g = C.linear_gain(photo, np.full((40, 40, 3), 0.25), np.ones((40, 40), bool))
    assert g["gain"] == pytest.approx(float(C.srgb_to_linear(200)) / 0.25) and g["px"] == 1600
    with pytest.raises(ValueError):
        C.linear_gain(photo, np.zeros((40, 40, 3)), np.ones((40, 40), bool))
    H = W = 260
    matte, alpha = np.ones((H, W), bool), np.ones((H, W))
    temple = np.zeros((H, W), bool)
    temple[20:120, 20:240] = True
    for side, how in ((60, "largest acetate"), (110, "front acetate")):
        front = np.zeros((H, W), bool)
        front[140:140 + side, 20:20 + side] = True
        region, label = C.gain_region(front, front | temple, matte, alpha)
        assert label.startswith(how), (side, label)
        assert region.sum() > 0 and not (region & ~(front | temple)).any()
        assert C.erode_px(front, 12).sum() < front.sum()


def test_stretch_is_monotone_hue_keeping_and_compositing():
    ramp = np.repeat(np.linspace(0, 255, 256)[None, :, None], 3, axis=2).astype(np.uint8)
    lo, hi = C.stretch_range(ramp, np.ones(ramp.shape[:2], bool))
    assert lo == pytest.approx(5.1, abs=0.2) and hi == pytest.approx(249.9, abs=0.2)
    out = C.luma8(C.apply_stretch(ramp, lo, hi))[0]
    assert np.all(np.diff(out) >= 0) and out[0] == 0 and out[-1] == pytest.approx(255.0)
    beige = np.array([[[150, 140, 110]]], np.uint8)
    s = C.apply_stretch(beige, 100.0, 355.0)[0, 0].astype(int)
    assert (s[0] - s[1], s[1] - s[2]) == (10, 30)                     # channel differences kept (no clipping)
    rgb, a = np.full((2, 2, 3), 0.2), np.array([[0.0, 1.0], [0.5, 1.0]])
    comp = C.composite_render(rgb, a, 2.0, (255, 255, 255))
    assert comp[0, 0].tolist() == [255, 255, 255] and comp[0, 1].tolist() == C.linear_to_srgb8([0.4] * 3).tolist()
    assert C.photo_background(np.full((4, 4, 3), 7, np.uint8), np.zeros((4, 4), bool)) == (7, 7, 7)


def _tent(z):
    V = np.array([[0, 0, 0], [0, 10, 0], [5, 0, z], [5, 10, z], [10, 0, 0], [10, 10, 0]], float)
    F = np.array([[0, 2, 3], [0, 3, 1], [2, 4, 5], [2, 5, 3]])
    return V, F


def test_crease_edges_ridge_valley_and_occlusion():
    from glasses3d.g3d_host import raster
    from glasses3d.g3d_host.camera import NormFrame
    for z, shape in ((2.0, 1), (-2.0, -1)):
        V, F = _tent(z)
        e = C.crease_edges(V, F)
        assert len(e["a"]) == 1 and int(e["shape"][0]) == shape                 # the diagonals are coplanar
        assert e["dihedral_deg"][0] == pytest.approx(2 * np.degrees(np.arctan2(2.0, 5.0)), abs=1e-6)
    V, F = _tent(2.0)
    e = C.crease_edges(np.vstack([V, V[:0]]), F)
    frame = NormFrame((5.0, 5.0, 0.0), 20.0)
    cam = raster.view_camera(frame, 0.0, 0.0, 10.0, (160, 160))               # from +Z: the tent faces it
    depth = raster.render(V, F, cam, frame, (160, 160))["depth"]
    full = C.crease_polylines(e, cam, frame, depth)
    assert len(full) == 1 and full[0][1] == 1
    length = float(np.linalg.norm(full[0][0][-1] - full[0][0][0]))
    assert length == pytest.approx(100.0, abs=3.0)                              # 10 mm at 10 px/mm
    occ = np.array([[-1, -1, 5], [11, -1, 5], [11, 5, 5], [-1, 5, 5]], float)   # a plate above, over y < 5
    VO = np.vstack([V, occ])
    FO = np.vstack([F, np.array([[6, 7, 8], [6, 8, 9]])])
    half = C.crease_polylines(e, cam, frame, raster.render(VO, FO, cam, frame, (160, 160))["depth"])
    seen = sum(float(np.linalg.norm(p[-1] - p[0])) for p, _s in half)
    assert seen == pytest.approx(50.0, abs=4.0)
    back = raster.view_camera(frame, 180.0, 0.0, 10.0, (160, 160))            # from -Z: both faces turn away
    assert C.crease_polylines(e, back, frame, raster.render(V, F, back, frame, (160, 160))["depth"]) == []


def test_iso_grid_offsets_overlay_and_layout():
    H, W = 60, 120
    d = np.tile(np.arange(W) * 0.5, (H, 1))
    y = np.tile((np.arange(H) * 0.25)[:, None], (1, W))
    grid = C.iso_lines(d, y, np.ones((H, W), bool))
    cols = sorted(set(np.nonzero(grid["d_mask"])[1]))
    assert cols == [19, 39, 59, 79, 99]                                          # d crosses 10, 20, ... mm
    assert {t for _u, _v, t in grid["labels"]} >= {"d10", "d50", "y5", "y10"}
    assert not C.iso_lines(d, y, np.zeros((H, W), bool))["labels"]
    model = np.zeros((H, W), bool)
    model[:, :50] = True
    matte = np.zeros((H, W), bool)
    matte[:, :54] = True
    off = C.outer_edge_offsets([{"d_mm": 10.0, "uv": [45.0, 30.0], "dir": [1.0, 0.0], "px_per_mm": 2.0},
                                {"d_mm": 20.0, "uv": [45.0, 30.0], "dir": [0.0, 1.0], "px_per_mm": 2.0},
                                {"d_mm": 30.0, "uv": [45.0, 30.0], "dir": [1.0, 0.0], "px_per_mm": 0.5}], model,
                               matte)
    assert off[0]["offset_mm"] == pytest.approx(2.0, abs=0.3) and off[1]["offset_mm"] is None
    assert off[2]["offset_mm"] is None                                          # 4 px = 8 mm: beyond the 6 mm reach
    ov = np.asarray(C.overlay_matched(np.full((H, W, 3), 128, np.uint8), model, [], grid))
    assert (ov[:, 49] == C.OUTLINE_RGB).all(axis=1).any() and ov.shape == (H, W, 3)
    assert C.stack_scale(100, 50, 3) == 1.0                                     # never upscale
    assert C.stack_scale(2000, 800, 4) == pytest.approx((1024 - 4 * 24 - 12 - 2) / 3200)   # 0.5 px a row: rounding
    for w, h, n in ((1357, 418, 3), (1177, 540, 3), (1357, 418, 4), (900, 1000, 3), (2000, 333, 4), (640, 2001, 3)):
        k = C.stack_scale(w, h, n)                                               # every stack fits, rows rounded
        assert n * (round(h * k) + C.LABEL_PX) + 4 * (n - 1) <= 1024 and round(w * k) <= 1024, (w, h, n)
    st = C.stack_rows([("a", Image.new("RGB", (100, 40))), ("b", Image.new("RGB", (80, 30)))])
    assert st.size == (100, 40 + 30 + 2 * 24 + 4)
    t = np.zeros((100, 200), bool)
    t[40:60, 50:150] = True
    mt = np.zeros((100, 200), bool)
    mt[30:70, 40:170] = True
    x0, y0, x1, y1 = C.crop_box(t, mt)
    assert x0 < 40 and x1 > 170 and y0 < 30 and y1 > 70 and C.crop_box(np.zeros((4, 4), bool), mt) is None


def test_white_balance_float_forms_and_float_reader(tmp_path):
    photo = np.zeros((20, 20, 3), np.uint8)
    photo[...] = (220, 214, 196)                                                 # ivory photo
    render = np.zeros((20, 20, 3))
    render[...] = (0.70, 0.58, 0.22)                                             # yellower render
    m = np.ones((20, 20), bool)
    wb = C.linear_gain_rgb(photo, render, m)
    want = C.srgb_to_linear(np.array([220, 214, 196])) / np.array([0.70, 0.58, 0.22])
    np.testing.assert_allclose(wb["gain"], want)
    assert wb["gain_Y"] == pytest.approx(float(want @ C.LUMA)) and wb["px"] == 400
    out = C.composite_render(render, np.ones((20, 20)), wb["gain"], (255, 255, 255))
    assert np.abs(out.astype(int) - photo.astype(int)).max() <= 1                # hue matched per channel
    f = C.composite_render(render, np.ones((20, 20)), wb["gain"], (255, 255, 255), as_float=True)
    assert f.dtype == np.float64 and np.abs(f - photo).max() < 1e-6
    f = C.composite_render(0.97 * render, np.ones((20, 20)), wb["gain"], (255, 255, 255), as_float=True)
    assert not np.allclose(f, np.round(f))                                       # not rounded to 8 bits
    s8, sf = C.apply_stretch(f, 150.0, 250.0), C.apply_stretch(f, 150.0, 250.0, as_float=True)
    assert s8.dtype == np.uint8 and np.abs(sf - s8).max() <= 0.5 and sf.dtype == np.float64
    with pytest.raises(ValueError):
        C.linear_gain_rgb(photo, render * [1, 1, 0], m)
    rgb = np.random.default_rng(0).uniform(0, 3, (6, 7, 3)).astype(np.float16)
    alpha = np.linspace(0, 1, 42).reshape(6, 7).astype(np.float16)
    with open(tmp_path / "x_native_linear.npz", "wb") as fh:
        np.savez_compressed(fh, rgb=rgb, alpha=alpha)
    got_rgb, got_a = C.read_linear(tmp_path / "x_native_linear.npz")
    np.testing.assert_array_equal(got_rgb, rgb.astype(np.float64))
    np.testing.assert_array_equal(got_a, alpha.astype(np.float64))
    assert got_rgb.max() > 1.0                                                   # unclipped
    assert "start model" in C.CAMERA_NOTE and "same sign on both sides" in C.CAMERA_NOTE


def test_grid_labels_per_side_without_overlap():
    H, W = 120, 300
    d = np.tile(np.arange(W) * 0.25, (H, 1))                                     # 4 px/mm: iso-d every 40 px
    y = np.tile((np.arange(H) * 0.1)[:, None], (1, W))
    side = np.zeros((H, W), np.int8)
    side[:70, :] = 1                                                             # the near temple: more pixels
    side[80:, 20:200] = -1
    grid = C.iso_lines(d, y, side != 0, side)
    labels = grid["labels"]
    assert {t for _u, v, t in labels if v < 70} >= {"d10", "d20", "d30", "d40", "d50", "d60", "d70"}
    assert any(v >= 80 for _u, v, _t in labels)                                  # the far temple gets labels too
    for i, (u1, v1, t1) in enumerate(labels):                                    # no two labels overlap
        for u2, v2, t2 in labels[i + 1:]:
            w = C.LABEL_CHAR_PX * max(len(t1), len(t2)) + 4
            assert abs(v1 - v2) >= C.LABEL_GAP_PX or abs(u1 - u2) >= w, (t1, t2)
    near_d10 = [(u, v) for u, v, t in labels if t == "d10" and v < 70]
    far_d10 = [(u, v) for u, v, t in labels if t == "d10" and v >= 80]
    assert len(near_d10) == 1 and len(far_d10) == 1                              # one label per side and level
