"""Fast tests of the ported camera model, renderer conventions and the camera-fitting parametrisation (WP3b)."""
from __future__ import annotations

import math
import pickle
import sys
import types
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest

from glasses3d.g3d_host import camera as C
from glasses3d.g3d_host import cameras, raster

REF = Path(__file__).resolve().parents[1] / "data" / "reference" / "projection"
RNG = np.random.default_rng(7)


WP3B_MODULES = ("camera", "raster", "cameras", "matte", "texture_utils", "fitmesh", "fit_views")
FORBIDDEN_ROOTS = ("automation", "reconstruction", "bsa", "modeler", "ar_v4")


def test_no_glasses3d_module_imports_automation():
    """Every module of g3d_host/ and g3d/ (parsed, not imported: g3d runs inside Blender) has no import of the old
    trees, and importing the WP3b modules leaves none of them in sys.modules."""
    import ast
    import importlib
    g3d = Path(__file__).resolve().parents[1]
    files = sorted((g3d / "g3d_host").rglob("*.py")) + sorted((g3d / "g3d").rglob("*.py"))
    assert len(files) >= len(WP3B_MODULES)
    for f in files:
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mods = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                for m in mods:
                    assert m.split(".")[0] not in FORBIDDEN_ROOTS, (str(f.relative_to(g3d)), m)
    for name in WP3B_MODULES:
        importlib.import_module(f"glasses3d.g3d_host.{name}")
    assert not set(FORBIDDEN_ROOTS) & set(sys.modules)


def test_project_matches_renderer_projection_and_documented_basis():
    cam = C.Camera(23.0, -11.0, 4.0, 0.2, 800.0, 400.0, 300.0)
    P = RNG.uniform(-0.5, 0.5, (50, 3))
    np.testing.assert_allclose(C.project(P, cam), raster._project_norm(P, cam), atol=1e-9)
    right, up, toward = raster.camera_basis(cam)
    np.testing.assert_allclose(np.cross(right, up), toward, atol=1e-12)          # right-handed, orthonormal
    front = C.Camera(0.0, 0.0, 0.0, 0.0, 100.0, 50.0, 40.0)
    np.testing.assert_allclose(C.project(np.array([[0.1, 0.2, 0.3]]), front), [[60.0, 20.0]])   # +X right, +Y up
    top = C.Camera(0.0, 90.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    np.testing.assert_allclose(raster.camera_basis(top)[2], [0, 1, 0], atol=1e-12)   # pitch +90 looks straight down


def test_project_refuses_the_near_plane():
    with pytest.raises(ValueError):
        C.project(np.array([[0.0, 0.0, 4.0]]), C.Camera(0, 0, 0, 0.25, 1, 0, 0))


def test_camera_rays_reproject_onto_their_pixels():
    cam = C.Camera(-35.0, 18.0, -3.0, 0.3, 900.0, 512.0, 384.0)
    u, v = RNG.uniform(0, 1024, 20), RNG.uniform(0, 768, 20)
    O, D = raster.camera_rays(cam, u, v, z0=1.2)
    for t in (0.0, 0.4, 1.1):
        np.testing.assert_allclose(raster._project_norm(O + t * D, cam), np.c_[u, v], atol=1e-8)


def test_camera_dict_and_normframe_round_trips():
    cam = C.Camera(1.5, 2.5, -0.5, 0.1, 1234.5, 600.25, 400.75)
    assert C.Camera.from_dict(cam.to_dict()) == cam == C.camera_from_dict(cam.to_dict())
    fr = C.NormFrame((1.0, -2.0, 3.0), 150.0)
    P = RNG.uniform(-80, 80, (10, 3))
    np.testing.assert_allclose(fr.to_mm(fr.to_norm(P)), P, atol=1e-12)
    assert C.NormFrame.from_dict(fr.to_dict()) == fr
    bb = C.NormFrame.from_bbox(np.array([[-70.0, -20, -140], [70, 10, 4]]))
    assert bb.center == (0.0, -5.0, -68.0) and bb.extent == 144.0
    assert C.px_per_mm(cam, fr) == pytest.approx(1234.5 / 150.0)
    np.testing.assert_allclose(C.project_mm(P, cam, fr), C.project(fr.to_norm(P), cam))


@pytest.mark.parametrize("pivot", [None, np.array([0.0, 0.05, 0.46])])
def test_pack_unpack_round_trip(pivot):
    for cam in (C.Camera(179.9, 9.8, -0.02, 0.2157, 1283.9, 722.8, 543.3),
                C.Camera(-73.6, 3.2, 1.6, 0.44, 1099.6, 785.3, 545.1),
                C.Camera(0.1, 59.9, -0.01, 0.0, 1068.0, 767.5, 439.5)):
        x = cameras.pack(cam, 900.0, pivot)
        back = cameras.unpack(x, 900.0, pivot)
        for k in C.CAMERA_FIELDS:
            assert getattr(back, k) == pytest.approx(getattr(cam, k), abs=1e-9), k
        np.testing.assert_allclose(cameras.pack(back, 900.0, pivot), x, atol=1e-9)


def test_pack_holds_the_pivot_still_under_rotation_and_zoom():
    pivot = np.array([0.0, 0.0, 0.46])
    cam = C.Camera(10.0, 5.0, 0.0, 0.2, 1000.0, 500.0, 400.0)
    x = cameras.pack(cam, 800.0, pivot)
    p0 = C.project(pivot[None], cam)[0]
    for i, d in ((0, 7.0), (1, -4.0), (3, 1.0)):                # yaw, pitch, perspective x10
        y = x.copy(); y[i] += d
        np.testing.assert_allclose(C.project(pivot[None], cameras.unpack(y, 800.0, pivot))[0], p0, atol=1e-7)


def _legacy_pickle(monkeypatch, cls_name="Camera", module="reconstruction.camera"):
    @dataclass(frozen=True)
    class Camera:          # stands in for reconstruction.camera.Camera (never imported)
        yaw: float
        pitch: float
        roll: float
        perspective: float
        scale: float
        center_x: float
        center_y: float
    Camera.__module__, Camera.__qualname__, Camera.__name__ = module, cls_name, cls_name
    pkg, mod = types.ModuleType(module.split(".")[0]), types.ModuleType(module)
    setattr(mod, cls_name, Camera)
    setattr(pkg, module.split(".")[1], mod)
    monkeypatch.setitem(sys.modules, module.split(".")[0], pkg)       # fakes only for pickle.dumps' lookup
    monkeypatch.setitem(sys.modules, module, mod)
    data = pickle.dumps(Camera(179.9, 9.8, -0.02, 0.2157, 1283.9, 722.8, 543.3), protocol=4)
    monkeypatch.delitem(sys.modules, module)
    monkeypatch.delitem(sys.modules, module.split(".")[0])
    return data


def test_load_camera_pickle_reads_reconstruction_cameras_without_automation(tmp_path, monkeypatch):
    p = tmp_path / "cam.pkl"
    p.write_bytes(_legacy_pickle(monkeypatch))
    assert "reconstruction.camera" not in sys.modules
    cam = C.load_camera_pickle(p)
    assert isinstance(cam, C.Camera) and cam == C.Camera(179.9, 9.8, -0.02, 0.2157, 1283.9, 722.8, 543.3)


def test_load_camera_pickle_refuses_other_globals(tmp_path):
    p = tmp_path / "evil.pkl"
    p.write_bytes(pickle.dumps(math.sqrt))
    with pytest.raises(pickle.UnpicklingError):
        C.load_camera_pickle(p)


@pytest.mark.skipif(not (REF / "cam_reference-02.png.pkl").exists(), reason="reference data missing")
def test_prototype_pickles_load():
    cam = C.load_camera_pickle(REF / "cam_reference-02.png.pkl")
    assert cam.yaw == 179.9166636715869 and cam.center_y == 543.3428694007581


def test_top_priors_are_first_class_prototype_grid_plus_low_band():
    proto_top = [(y, p, 0.0) for y in (-6.0, 0.0, 6.0) for p in (70.0, 80.0, 88.0, -70.0, -80.0, -88.0)]
    low = [(y, p, 0.0) for y in (-6.0, 0.0, 6.0) for p in (45.0, 55.0, 62.0)]
    top = [s[:3] for s in cameras.prior_seeds("top")]
    assert top[:len(proto_top)] == proto_top                         # the prototype grid first, in its order
    assert top[len(proto_top):] == low and len(top) == 27
    topb = [s[:3] for s in cameras.prior_seeds("topb")]
    assert topb == [(180.0 + y, p, r) for y, p, r in top]
    assert all(p > 0 for _, p, _ in low)                            # the band is camera-above only
    assert cameras.YAW_DISTINCT_VIEWS == ("angled",) + cameras.TOP_OBLIQUE_VIEWS + cameras.REAR_THREEQUARTER_VIEWS
    assert cameras.PITCH_BOUNDS == (-95.0, 95.0)
    assert cameras.yaw_window("top", 0.0) == (-35.0, 35.0) and cameras.yaw_window("topb", 180.0) == (145.0, 215.0)
    assert cameras.pitch_window("top") == (-95.0, 95.0)
    for view, yaw in (("top_oblique_left", 90.0), ("top_oblique_right", -90.0)):
        seeds = cameras.prior_seeds(view)
        assert {s[0] for s in seeds} == {yaw + d for d in cameras.PRIOR_YAW_OFFSETS}
        assert min(s[1] for s in seeds) == 15.0 and max(s[1] for s in seeds) == 60.0 and len(seeds) == 56
        lo, hi = cameras.pitch_window(view)
        assert lo > 0 and all(lo <= s[1] <= hi for s in seeds)
    # bsa's grids are unchanged
    assert len(cameras.prior_seeds("back")) == 42 and cameras.prior_seeds("back")[0] == (168.0, -20.0, 0.0, "prior")
    assert len(cameras.prior_seeds("angled")) == 84
    with pytest.raises(ValueError):
        cameras.prior_seeds("bottom")


def test_bounds_use_the_view_pitch_window():
    cam = C.Camera(90.0, 40.0, 0.0, 0.1, 1000.0, 500.0, 400.0)
    b = cameras._bounds(cam, 800.0, (55.0, 125.0), local={"yaw": 3, "pitch": 50, "roll": 2, "scale": 1, "shift": 1},
                        pitch_win=cameras.pitch_window("top_oblique_left"))
    assert b[1] == (5.0, 85.0)
    b0 = cameras._bounds(cam, 800.0, (55.0, 125.0), local={"yaw": 3, "pitch": 50, "roll": 2, "scale": 1, "shift": 1})
    assert b0[1] == (-10.0, 90.0)
    g = cameras._bounds(cam, 800.0, (55.0, 125.0))                         # global window (no local box)
    assert g[0] == (55.0, 125.0) and g[1] == cameras.PITCH_BOUNDS


def test_cross_seeds_skip_held_out_top_and_elevated_sources_and_respect_pitch_windows():
    winners = {"front": C.Camera(2.0, 15.0, 1.0, 0.1, 1, 0, 0), "angled": C.Camera(40.0, 10.0, 0.0, 0.1, 1, 0, 0),
               "top": C.Camera(0.0, 60.0, 0.0, 0.1, 1, 0, 0),
               "top_oblique_left": C.Camera(89.0, 19.0, -1.0, 0.1, 1, 0, 0)}
    s = cameras.cross_seeds("back", winners)
    assert {o.split(":")[1] for *_, o in s} == {"front"}
    a = cameras.cross_seeds("angled", winners)                     # the angled view receives from front only here
    assert {o.split(":")[1] for *_, o in a} == {"front"} and len(a) == 6
    assert (42.0, 15.0, 1.0, "cross:front:rotated") in a and (-42.0, 15.0, -1.0, "cross:front:mirrored") in a
    assert cameras.wrap_seeds("angled", a) == a
    assert (182.0, 15.0, 1.0, "cross:front:rotated") in s and (178.0, 15.0, -1.0, "cross:front:mirrored") in s
    assert (182.0, -15.0, 1.0, "cross:front:pitch_twin") in s
    t = cameras.cross_seeds("top_oblique_left", winners)
    assert t and all(p > 0 for _, p, _, _ in t)                    # the -15 pitch twin is outside (5, 85)
    w = cameras.wrap_seeds("topb", [(-178.0, 70.0, 0.0, "x"), (10.0, 70.0, 0.0, "y")])
    assert w == [(182.0, 70.0, 0.0, "x")]


def test_px_per_mm_at_and_resized_camera():
    fr = C.NormFrame((0.0, 0.0, 0.0), 100.0)
    ortho = C.Camera(0.0, 0.0, 0.0, 0.0, 500.0, 0.0, 0.0)
    assert cameras.px_per_mm_at(ortho, fr, np.array([0.0, 0.0, 30.0])) == pytest.approx(5.0)
    persp = C.Camera(0.0, 0.0, 0.0, 0.3, 500.0, 0.0, 0.0)
    assert cameras.px_per_mm_at(persp, fr, np.array([0.0, 0.0, 30.0])) > 5.0 > \
        cameras.px_per_mm_at(persp, fr, np.array([0.0, 0.0, -30.0]))
    cam = C.Camera(12.0, 7.0, 1.0, 0.2, 900.0, 640.0, 480.0)
    P = RNG.uniform(-0.4, 0.4, (10, 3))
    k, x0, y0 = 0.37, 100.0, 60.0
    np.testing.assert_allclose(C.project(P, cameras.resized_camera(cam, k, x0, y0)),
                               (C.project(P, cam) - [x0, y0] + 0.5) * k - 0.5, atol=1e-9)


def test_matte_roi_is_the_prototype_rule():
    fg = np.zeros((100, 200), bool)
    fg[40:60, 50:150] = True                       # width 100 -> margin int(15) + 8 = 23
    assert cameras.matte_roi(fg) == (27, 17, 173, 83)
    fg2 = np.zeros((50, 60), bool); fg2[0:50, 0:60] = True
    assert cameras.matte_roi(fg2) == (0, 0, 60, 50)
    with pytest.raises(ValueError):
        cameras.matte_roi(np.zeros((5, 5), bool))


def test_contour_stats_and_downsample_mask():
    a = np.zeros((40, 40), bool); a[10:30, 10:30] = True
    assert cameras.contour_stats(a, a) == (0.0, 0.0)
    b = np.zeros((40, 40), bool); b[12:32, 10:30] = True
    mean, p95 = cameras.contour_stats(a, b)
    assert 0.5 < mean < 2.0 and p95 == pytest.approx(2.0)
    assert cameras.contour_stats(a, np.zeros_like(a))[0] == float("inf")
    d = raster.downsample_mask(a, 4, (8, 8, 33, 33))
    assert d.shape == (7, 7) and d[0, 0] == pytest.approx(0.25) and d[2, 2] == 1.0


def test_opencv_failures_carry_the_array_description():
    """The one unexplained cv2.error seen under load (PORT_REVIEW §9, open) left no evidence; _edge/_dist_to now
    re-raise with shape/dtype/contiguity so a recurrence is diagnosable. Forced here with arrays OpenCV refuses."""
    bad = np.zeros((4, 4, 600), bool)                       # more channels than cv2 accepts
    bad[0, 0, 0] = True
    with pytest.raises(RuntimeError, match=r"cameras\._edge on shape=\(4, 4, 600\) dtype=uint8"):
        cameras._edge(bad)
    with pytest.raises(RuntimeError, match=r"cameras\._dist_to on shape=\(4, 4, 600\) dtype=uint8"):
        cameras._dist_to(bad)
