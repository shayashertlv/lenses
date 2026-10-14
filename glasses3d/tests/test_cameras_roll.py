"""Complete-circle registration is a camera change, tested on independent asymmetric synthetic glasses."""
from dataclasses import replace

import numpy as np
import pytest

from glasses3d.g3d_host import cameras, fit_views, raster
from glasses3d.g3d_host.camera import Camera
from glasses3d.tests.test_cameras_fit import synth  # noqa: F401 - shared synthetic fixture, not product geometry


@pytest.mark.parametrize("view", cameras.AXIAL_VIEWS)
def test_axial_roll_charts_cover_circle_and_cross_half_turn(view):
    seeds = cameras.prior_seeds(view)
    prefix = len(cameras.TOP_YAW_OFFSETS) * (len(cameras.TOP_PITCHES) + len(cameras.TOP_LOW_PITCHES))
    assert len(seeds) == prefix * len(cameras.AXIAL_ROLL_SEEDS)
    assert [s[2] for s in seeds[:prefix]] == [0.] * prefix
    for angle in np.linspace(-180., 180., 721):
        lo, hi = cameras.roll_window(view, angle)
        assert lo < angle < hi
    assert cameras.roll_window(view, 180.) == (155., 205.)
    assert cameras.roll_window(view, -179.) == (-205., -155.)
    assert cameras.roll_window(view, 0.) == cameras.ROLL_BOUNDS
    sign = -1 if view in cameras.BOTTOM_VIEWS else 1
    assert all(sign * s[1] > 0 for s in seeds[18:prefix])
    assert view in cameras.NOT_CROSS_SOURCES


@pytest.mark.parametrize("view", [v for v in cameras.VIEWS if v not in cameras.AXIAL_VIEWS])
def test_other_views_keep_the_original_roll_prior(view):
    assert cameras.roll_window(view, 90.) == cameras.ROLL_BOUNDS
    assert all(s[2] == 0 for s in cameras.prior_seeds(view))


def test_powell_and_icp_receive_the_same_unwrapped_roll_window(synth, monkeypatch):
    shape = (180, 240)
    cam = raster.view_camera(synth["frame"], 0., 60., 1., shape, roll=181., perspective=.1)
    fg = synth["scene"].render(cam, shape)["mask"]
    vf = cameras.ViewFit("top", fg, synth["scene"], pivot=synth["pivot"])
    windows = []

    def powell(scene, lv, camera, bounds, *args, **kwargs):
        windows.append(("powell", bounds[2]))
        return camera, lv.loss(scene, camera), 1

    def icp(scene, lv, camera, *args, **kwargs):
        windows.append(("icp", kwargs["roll_win"]))
        return camera

    monkeypatch.setattr(cameras, "powell", powell)
    monkeypatch.setattr(cameras, "contour_icp", icp)
    vf.staged(vf.levels["coarse"], cam, (cameras.COARSE_LOCAL,), 1)
    assert windows == [("powell", (171., 191.)), ("icp", (155., 205.))]
    # The real ICP's initial clip also respects this window, even with no iterations.
    monkeypatch.undo()
    got = cameras.contour_icp(synth["scene"], vf.levels["coarse"], cam, vf.unit, vf.pivot,
                             cameras.yaw_window("top", 0.), iters=0, roll_win=(155., 205.))
    assert got is not None and abs(got.roll - 181.) < 1e-9


@pytest.mark.parametrize("view,pitch,roll", [
    ("top", 60., 90.), ("top", 60., -90.), ("top", 60., 179.), ("top", 60., -179.),
    ("bottom", -60., 90.), ("bottomb", -60., -90.),
])
def test_rotated_axial_photos_register_from_the_full_general_prior(synth, view, pitch, roll):
    shape = (480, 640)
    truth = raster.view_camera(synth["frame"], cameras.PRIOR_YAW[view], pitch, 3., shape,
                               roll=roll, perspective=.1)
    fg = synth["scene"].render(truth, shape)["mask"]
    result = cameras.fit_view(view, fg, synth["scene"], synth["pivot"])
    assert result["metrics"]["iou"] > .98, result["metrics"]
    assert result["metrics"]["contour_mean_px"] < 1.
    assert result["camera"].pitch * pitch > 0
    assert -180. <= result["camera"].roll < 180.
    assert fit_views.sign_check(view, result["camera"]) is None
    # Compare projected evidence rather than interchangeable yaw/roll Euler coordinates near axial views.
    assert cameras.native_metrics(synth["scene"], result["camera"], fg,
                                  cameras.matte_roi(fg))["iou"] > .98


def test_bottom_is_below_and_topb_remains_above():
    assert fit_views.view_candidates("bottom") == ("bottom", "bottomb")
    assert fit_views.view_candidates("bottomb") == ("bottomb",)
    for kind, sign in (("bottom", -1), ("bottomb", -1), ("top", 1), ("topb", 1)):
        cam = Camera(0., sign * 60., 90., .1, 100., 50., 50.)
        assert fit_views.kind_pitch_sign(kind) == sign
        assert fit_views.sign_check(kind, cam) is None
        assert fit_views.sign_check(kind, replace(cam, pitch=-cam.pitch)) is not None
    from glasses3d.agent.jobs import _view
    assert _view({"kind": "bottom", "side": None}, "photo")["fit_kinds"] == ["bottom", "bottomb"]
