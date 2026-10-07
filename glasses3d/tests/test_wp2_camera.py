"""WP2 stage B, host only: the WP3b camera <-> Blender camera converter (g3d_host/blender_camera.py, WP2_DESIGN §6).

* analytic parity of the numpy model of Blender's view plane (sensor fit AUTO) against camera.project_mm on the 12
  frozen WP3b cameras (Prada + FT1119 cameras.json) and 4 synthetic ones (portrait perspective, landscape and portrait
  orthographic, a square perspective camera at pitch 89.5), with a non-zero scene origin: <= 0.01 px (design §13);
  Blender's own world_to_camera_view and renders are held to the same cameras in test_wp2_camera_parity.py;
* pixel rays pass through the points that project to them (pick_surface_point);
* the inverse (from_blender) gives back the WP3b camera, and any Blender camera (off-axis eye, either type) becomes
  a WP3b camera that projects exactly as Blender does;
* crops (cameras.resized_camera) are the same camera; canonical and custom views frame what they promise;
* what Blender cannot hold is refused."""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest

from glasses3d.g3d_host import blender_camera as BC
from glasses3d.g3d_host import cameras
from glasses3d.g3d_host.camera import Camera, NormFrame, project, project_mm

G3D = Path(__file__).resolve().parents[1]
FROZEN = {p: G3D / "data" / "wp3b" / p / "cameras.json" for p in ("prada", "ft1119")}
REQUIRE_DATA = os.environ.get("G3D_REQUIRE_DATA") == "1"
ORIGIN = np.array([1.5, -2.0, 3.0])
PRADA_FRAME = NormFrame((0.0, -3.9287004619836807, -71.77581137511879), 151.27962338738143)
# the panel's synthetic set (worker_proto dbg2): portrait / landscape / square, perspective / ortho, pitch 89.5
SYNTHETIC = {
    "portrait_persp": (Camera(28.0, 20.0, 3.0, 0.2, 1000.0, 420.0, 760.0), (1400, 800)),
    "landscape_ortho": (Camera(-58.0, 31.0, -6.0, 0.0, 900.0, 700.0, 280.0), (600, 1300)),
    "portrait_ortho": (Camera(150.0, -10.0, 2.0, 0.0, 380.0, 330.0, 580.0), (1100, 700)),
    "square_persp_top": (Camera(0.0, 89.5, 0.0, 0.2, 750.0, 512.0, 540.0), (1024, 1024)),
}


def need(ok: bool, why: str) -> None:
    if not ok:
        (pytest.fail if REQUIRE_DATA else pytest.skip)(why)


def frozen_cameras() -> list[tuple[str, Camera, NormFrame, tuple[int, int]]]:
    out = []
    for prod, path in FROZEN.items():
        need(path.is_file(), f"{path} missing (WP3b data)")
        d = json.loads(path.read_text(encoding="utf-8"))
        fr = NormFrame.from_dict(d["model"]["frame"])
        for name, rec in d["images"].items():
            out.append((f"{prod}/{name}", Camera.from_dict(rec["camera"]), fr, tuple(rec["shape"])))
    return out


def all_cameras():
    cams = frozen_cameras()
    cams += [(f"synthetic/{n}", c, PRADA_FRAME, s) for n, (c, s) in SYNTHETIC.items()]
    assert len(cams) == 16
    return cams


def sample_points(frame: NormFrame, n: int = 2000, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return frame.to_mm(rng.uniform(-0.5, 0.5, size=(n, 3)) * np.array([1.0, 0.4, 1.0]))


def test_analytic_parity_on_16_cameras_is_far_below_a_hundredth_of_a_pixel():
    worst = {}
    for name, cam, fr, shape in all_cameras():
        P = sample_points(fr)
        spec = BC.to_blender(cam, fr, ORIGIN, shape)
        uv, depth = BC.project(spec, P + ORIGIN, return_depth=True)
        ref = project_mm(P, cam, fr)
        err = float(np.abs(uv - ref).max())
        worst[name] = err
        assert err <= 0.01, (name, err)
        assert (depth > 0).all(), name
        assert spec["sensor_fit"] == "AUTO" and spec["resolution"] == [shape[1], shape[0]]
        m = BC.matrix_world(spec)
        np.testing.assert_allclose(m[:3, :3] @ m[:3, :3].T, np.eye(3), atol=1e-12)
        assert np.linalg.det(m[:3, :3]) == pytest.approx(1.0, abs=1e-12)       # a rotation, never a mirror
    assert max(worst.values()) < 1e-9, worst                                      # float64 vs float64: exact


def test_the_convention_matches_the_design_formulas():
    cam, shape = SYNTHETIC["portrait_persp"]
    H, W = shape
    spec = BC.to_blender(cam, PRADA_FRAME, ORIGIN, shape)
    big = max(W, H)
    assert spec["type"] == "PERSP"
    assert spec["lens_mm"] == pytest.approx((cam.scale / cam.perspective) * 36.0 / big, rel=1e-15)
    assert spec["shift_x"] == pytest.approx(-(cam.center_x + 0.5 - W / 2) / big)
    assert spec["shift_y"] == pytest.approx((cam.center_y + 0.5 - H / 2) / big)
    toward = BC.axes(cam)[2]
    np.testing.assert_allclose(BC.matrix_world(spec)[:3, 3],
                               np.asarray(PRADA_FRAME.center) + ORIGIN + toward * PRADA_FRAME.extent / cam.perspective)
    cam, shape = SYNTHETIC["landscape_ortho"]
    spec = BC.to_blender(cam, PRADA_FRAME, ORIGIN, shape)
    assert spec["type"] == "ORTHO" and spec["lens_mm"] is None
    assert spec["ortho_scale"] == pytest.approx(max(shape) * PRADA_FRAME.extent / cam.scale)
    np.testing.assert_allclose(BC.matrix_world(spec)[:3, 3], np.asarray(PRADA_FRAME.center) + ORIGIN
                               + BC.axes(cam)[2] * 4.0 * PRADA_FRAME.extent)


def test_rays_pass_through_the_points_that_project_to_their_pixels():
    for name, cam, fr, shape in all_cameras():
        P = sample_points(fr, 300, seed=1) + ORIGIN
        spec = BC.to_blender(cam, fr, ORIGIN, shape)
        o, d = BC.rays(spec, BC.project(spec, P))
        np.testing.assert_allclose(np.linalg.norm(d, axis=1), 1.0, atol=1e-12)
        v = P - o
        off = np.linalg.norm(v - (v * d).sum(1, keepdims=True) * d, axis=1)
        assert off.max() < 1e-6, (name, off.max())
        assert ((v * d).sum(1) > 0).all(), name                                   # in front of the ray origin


def test_a_crop_is_the_same_camera_on_a_sub_window():
    for name, cam, fr, shape in all_cameras()[:6] + all_cameras()[-4:]:
        P = sample_points(fr, 200, seed=2)
        full = project_mm(P, cam, fr)
        for x0, y0, k in ((37, 19, 1.0), (101.0, 55.0, 2.5), (0, 0, 0.5)):
            c = BC.crop_camera(cam, x0, y0, k)
            assert c == cameras.resized_camera(cam, k, x0, y0)
            crop = project_mm(P, c, fr)
            np.testing.assert_allclose(crop, (full - [x0, y0] + 0.5) * k - 0.5, atol=1e-9)
            spec = BC.to_blender(c, fr, ORIGIN, (int(shape[0] * k) or 1, int(shape[1] * k) or 1))
            np.testing.assert_allclose(BC.project(spec, P + ORIGIN), crop, atol=1e-8)


def test_canonical_views_frame_the_box_with_five_percent_margins():
    lo, hi = np.array([-71.4, -26.0, -147.4]), np.array([71.4, 18.2, 3.9])
    corners = BC.box_corners(lo, hi)
    frame = NormFrame(tuple((lo + hi) / 2), float((hi - lo).max()))
    assert set(BC.CANONICAL_VIEWS) == {"front", "rear", "left", "right", "top", "bottom", "angled_left",
                                       "angled_right"}
    for name, (yaw, pitch) in BC.CANONICAL_VIEWS.items():
        cam, (H, W) = BC.framed_view(yaw, pitch, corners, frame, 1024)
        assert cam.perspective == 0.0 and max(H, W) == 1024
        uv = project(frame.to_norm(corners), cam)
        span = uv.max(0) - uv.min(0)
        long_axis = int(np.argmax(span))
        margin_px = (1024 - span[long_axis]) / 2
        assert margin_px == pytest.approx(0.05 * 1024 / 1.1, abs=1e-6), name
        assert uv.min() >= 0 and uv[:, 0].max() <= W - 1 and uv[:, 1].max() <= H - 1, name
        np.testing.assert_allclose((uv.min(0) + uv.max(0)) / 2, [(W - 1) / 2, (H - 1) / 2], atol=1e-9)
    # yaw +90 looks from +X (the glasses' own left side); the top view looks down
    assert BC.axes(Camera(90, 0, 0, 0, 1, 0, 0))[2] == pytest.approx([1, 0, 0])
    assert BC.axes(Camera(0, 90, 0, 0, 1, 0, 0))[2] == pytest.approx([0, 1, 0], abs=1e-12)


def test_custom_view_centres_the_target_and_spans_width_mm():
    frame = PRADA_FRAME
    target = np.array([30.0, 5.0, -40.0])
    for persp in (0.0, 0.25):
        cam, (H, W) = BC.custom_view(25.0, 20.0, target, 50.0, frame, 800, roll=4.0, perspective=persp)
        assert (H, W) == (800, 800)
        np.testing.assert_allclose(project_mm(target[None], cam, frame)[0], [399.5, 399.5], atol=1e-9)
        assert BC.px_per_mm_at(cam, frame, target) == pytest.approx(800 / 50.0, rel=1e-12)
        assert BC.px_per_mm_at(cam, frame, target) == pytest.approx(cameras.px_per_mm_at(cam, frame, target))


def test_what_blender_cannot_hold_is_refused():
    cam = Camera(0, 0, 0, 0.1, 1000.0, 50_000.0, 300.0)                         # principal point 50 widths away
    with pytest.raises(ValueError, match="off centre"):
        BC.to_blender(cam, PRADA_FRAME, ORIGIN, (600, 800))
    with pytest.raises(ValueError, match="minimum"):
        BC.to_blender(Camera(0, 0, 0, 0.5, 5.0, 400, 300), PRADA_FRAME, ORIGIN, (600, 800))
    with pytest.raises(ValueError, match="near plane"):
        BC.framed_view(0, 0, BC.box_corners([-80] * 3, [80] * 3), PRADA_FRAME, 512, perspective=2.0)
    with pytest.raises(ValueError):
        BC.to_blender(Camera(0, 0, 0, 0.1, 1000.0, 1, 1), PRADA_FRAME, ORIGIN, (0, 10))


def test_from_blender_inverts_to_blender_and_converts_any_blender_camera():
    for name, cam, fr, shape in all_cameras():
        spec = BC.to_blender(cam, fr, ORIGIN, shape)
        back, fr2, shape2 = BC.from_blender(spec, ORIGIN, fr)
        assert fr2 == fr and shape2 == tuple(shape), name
        P = sample_points(fr, 300, seed=3)
        np.testing.assert_allclose(project_mm(P, back, fr2), project_mm(P, cam, fr), atol=1e-7, err_msg=name)
        if abs(cam.pitch) < 89.0:                                                # yaw is degenerate only at the pole
            assert (back.yaw - cam.yaw + 180.0) % 360.0 - 180.0 == pytest.approx(0.0, abs=1e-7), name   # mod 360
            for f in ("pitch", "roll", "perspective", "scale", "center_x", "center_y"):
                assert getattr(back, f) == pytest.approx(getattr(cam, f), rel=1e-9, abs=1e-7), (name, f)
    # an eye moved off the frame's axis (perspective: the frame re-centres; ortho: a principal-point shift)
    for name in ("portrait_persp", "landscape_ortho"):
        cam, shape = SYNTHETIC[name]
        spec = BC.to_blender(cam, PRADA_FRAME, ORIGIN, shape)
        m = np.asarray(spec["matrix_world"])
        m[:3, 3] += 7.0 * m[:3, 0] - 4.0 * m[:3, 1]
        spec["matrix_world"] = m.tolist()
        back, fr2, _ = BC.from_blender(spec, ORIGIN, PRADA_FRAME)
        P = sample_points(PRADA_FRAME, 300, seed=4)
        np.testing.assert_allclose(project_mm(P, back, fr2), BC.project(spec, P + ORIGIN), atol=1e-7, err_msg=name)
        assert (fr2 == PRADA_FRAME) == (name == "landscape_ortho")
    back, fr3, _ = BC.from_blender(BC.to_blender(*SYNTHETIC["portrait_persp"][:1], PRADA_FRAME, ORIGIN,
                                                 SYNTHETIC["portrait_persp"][1]), ORIGIN)
    P = sample_points(PRADA_FRAME, 100, seed=5)
    np.testing.assert_allclose(project_mm(P, back, fr3), project_mm(P, SYNTHETIC["portrait_persp"][0], PRADA_FRAME),
                               atol=1e-7)
