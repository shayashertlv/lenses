"""Synthetic camera-fit tests (WP3b): render a procedurally built pair of glasses (elliptical rims on a wrapped front,
bridge, temples with an ear bend, one nose pad) at a known camera, then fit it back from a perturbed seed (front),
from the full first-class top prior (on and off the seed grid, with roll), and from the full elevated-side prior;
plus the sign margin (``ViewFit.pitch_twin``), the common-level re-score (``final_losses``) and ``fit_best``'s choice.

(An all-box model is too degenerate for a fit test: its flat front leaves yaw, pitch, perspective and scale in one
long valley, so a 6-7 degree seed error ends at IoU 0.96.)"""
from __future__ import annotations

import types

import numpy as np
import pytest

from glasses3d.g3d_host import camera as C
from glasses3d.g3d_host import cameras, raster
from glasses3d.tests import glbfab

SHAPE = (420, 640)
WRAP = 0.0022            # front wrap: z -= WRAP x^2 (mm)


def ring(cx, cy, rx, ry, w, d, n=48):
    """Elliptical rim band (radial width w, depth d) centred at (cx, cy, 0)."""
    t = np.linspace(0, 2 * np.pi, n, endpoint=False)
    V = []
    for k in range(n):
        c, s = np.cos(t[k]), np.sin(t[k])
        for (ax, ay) in ((rx, ry), (rx - w, ry - w)):
            for z in (d / 2, -d / 2):
                V.append((cx + ax * c, cy + ay * s, z))
    V = np.array(V)               # per angle: outer front, outer back, inner front, inner back
    F = []
    for k in range(n):
        a, b = 4 * k, 4 * ((k + 1) % n)
        for i, j in ((0, 2), (1, 3), (0, 1), (2, 3)):          # front, back, outer wall, inner wall
            F += [(a + i, b + i, b + j), (a + i, b + j, a + j)]
    return V, np.array(F)


def bar(p0, p1, w, h):
    """Box of width w x height h along the segment p0 -> p1."""
    p0, p1 = np.asarray(p0, float), np.asarray(p1, float)
    d = p1 - p0
    side = np.cross(d, [0.0, 1.0, 0.0])
    side = side / np.linalg.norm(side) if np.linalg.norm(side) > 1e-9 else np.array([1.0, 0.0, 0.0])
    up = np.cross(side, d / np.linalg.norm(d))
    V = np.array([p + sx * w / 2 * side + sy * h / 2 * up
                  for p in (p0, p1) for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))])
    V = V[[0, 1, 2, 3, 4, 5, 6, 7]]
    return V, glbfab.BOX_FACES.astype(np.int64)


def glasses():
    front = [ring(-32, 0, 26, 19, 4, 4), ring(32, 0, 26, 19, 4, 4), bar((-8, 9, 0), (8, 9, 0), 3, 4),
             bar((-60, 6, 0), (-66, 6, 0), 4, 8), bar((60, 6, 0), (66, 6, 0), 4, 8),
             bar((-12, -10, -3), (-11, -14, -6), 4, 3)]                                  # one nose pad (asymmetry)
    parts = []
    for V, F in front:
        V = V.copy(); V[:, 2] -= WRAP * V[:, 0] ** 2
        parts.append((V, F))
    for sx in (-1, 1):
        zh = -WRAP * 66 ** 2
        parts.append(bar((sx * 66, 6, zh - 2), (sx * 69, 5, -110), 3, 6))
        parts.append(bar((sx * 69, 5, -110), (sx * 68, -18, -138), 3, 5))
    Vs, Fs, off = [], [], 0
    for V, F in parts:
        Vs.append(V); Fs.append(F + off); off += len(V)
    return np.vstack(Vs), np.vstack(Fs)


@pytest.fixture(scope="module")
def synth():
    V, F = glasses()
    frame = C.NormFrame.from_bbox(V)
    scene = raster.get_scene(V, F, frame)
    front = V[:, 2] >= -12.0
    pivot = frame.to_norm(((V[front].min(0) + V[front].max(0)) / 2)[None])[0]
    return {"V": V, "F": F, "frame": frame, "scene": scene, "pivot": pivot}


def _photo(s, yaw, pitch, roll=0.0, persp=0.15, ppm=3.0):
    cam = raster.view_camera(s["frame"], yaw, pitch, ppm, SHAPE, roll=roll, perspective=persp)
    return cam, s["scene"].render(cam, SHAPE)["mask"]


def test_render_is_consistent_with_project(synth):
    cam, fg = _photo(synth, 15.0, 10.0)
    uv = C.project_mm(synth["V"], cam, synth["frame"])
    ys, xs = np.nonzero(fg)
    assert abs(xs.min() - uv[:, 0].min()) <= 1.0 and abs(xs.max() - uv[:, 0].max()) <= 1.0
    assert abs(ys.min() - uv[:, 1].min()) <= 1.0 and abs(ys.max() - uv[:, 1].max()) <= 1.0
    r = synth["scene"].render(cam, SHAPE, 3, (100, 50, 500, 350), want_points=True)
    hit = r["mask"]
    back = C.project_mm(r["points"][hit], cam, synth["frame"])
    us, vs = r["grid"]
    ii, jj = np.nonzero(hit)
    np.testing.assert_allclose(back, np.c_[us[jj], vs[ii]], atol=1e-3)


def test_front_fit_recovers_the_camera_from_a_perturbed_seed(synth):
    true, fg = _photo(synth, 8.0, 12.0, roll=2.0)
    vf = cameras.ViewFit("front", fg, synth["scene"], pivot=synth["pivot"])
    vf.run_starts([(true.yaw + 6.0, true.pitch - 7.0, 0.0, "perturbed")], 1)
    res = vf.refine()
    met = cameras.native_metrics(synth["scene"], res["camera"], fg, cameras.matte_roi(fg))
    assert met["iou"] > 0.98, met["iou"]
    assert met["contour_mean_px"] < 1.0
    assert abs(res["camera"].roll - true.roll) < 1.0


@pytest.mark.parametrize("pitch, roll, min_iou", [(72.0, 0.0, 0.98), (60.0, 0.0, 0.98), (62.0, 9.0, 0.97)])
def test_top_view_fit_from_the_full_top_prior(synth, pitch, roll, min_iou):
    """72 sits 2 deg from a prototype-grid seed; 60 is 10 deg below that grid (FT1119 reference-04 fits at +60; from
    the grid alone this case ended at pitch 77.7, IoU 0.46, which is why the prior has the +45..62 band); the last
    case adds a 9 deg in-plane rotation (roll seeds are 0): measured IoU 0.973 with yaw/pitch/roll within 0.6/0.2/0.7
    deg, a residual in the yaw-roll valley that a top view couples. All from the full prior_seeds('top')."""
    true, fg = _photo(synth, 0.0, pitch, roll=roll, persp=0.1)
    r = cameras.fit_view("top", fg, synth["scene"], synth["pivot"])
    assert r["metrics"]["iou"] > min_iou, r["metrics"]["iou"]
    assert abs(r["camera"].pitch - true.pitch) < 1.0, r["camera"]
    assert abs(r["camera"].roll - true.roll) < 1.0, r["camera"]
    assert abs(r["camera"].yaw - true.yaw) < 1.0, r["camera"]
    s = r["sign"]                               # the camera-below twin must fit clearly worse
    assert s["twin_pitch_window"] == [cameras.PITCH_BOUNDS[0], 0.0] and s["twin_camera"][1] <= 0.0
    assert s["margin"] > cameras.PITCH_TWIN_TIE and s["flag"] is None, s


def test_elevated_side_fit_from_the_full_prior_and_fit_best(synth):
    """True pitch 18 lies between the prior's 15 and 20 seeds; both candidates use the full 56-seed prior. With bsa's
    pitch-only start picks this ended in a perspective basin (persp 0.037 vs 0.12, IoU 0.869; the 'left' prior fails
    the same way); YAW_DISTINCT_VIEWS refines more than one yaw per camera height."""
    true, fg = _photo(synth, 90.0, 18.0, persp=0.12)
    assert all(len(cameras.prior_seeds(v)) == 56 for v in cameras.TOP_OBLIQUE_VIEWS)
    best, results = cameras.fit_best(fg, synth["scene"], synth["pivot"],
                                     [("top_oblique_right", None), ("top_oblique_left", None)])
    assert [r["view"] for r in results] == ["top_oblique_right", "top_oblique_left"]
    assert best["view"] == "top_oblique_left"
    assert best["common_loss"] == min(r["common_loss"] for r in results)
    assert best["metrics"]["iou"] > 0.98, best["metrics"]["iou"]
    assert abs(best["camera"].pitch - 18.0) < 1.0 and abs(best["camera"].yaw - 90.0) < 1.0, best["camera"]
    assert abs(best["camera"].perspective - true.perspective) < 0.02, best["camera"]
    s = best["sign"]                            # one-signed window (5, 85): the twin searches (-85, -5)
    assert s["twin_pitch_window"] == [-85.0, -5.0] and s["twin_camera"][1] <= -5.0
    assert s["margin"] > 0, s


def test_opposite_pitch_window():
    assert cameras.opposite_pitch_window("top", 60.0) == (-95.0, 0.0)
    assert cameras.opposite_pitch_window("top", -60.0) == (0.0, 95.0)
    assert cameras.opposite_pitch_window("top_oblique_left", 19.0) == (-85.0, -5.0)
    assert cameras.opposite_pitch_window("back", 10.0) == (-95.0, 0.0)


def test_final_losses_put_cameras_on_one_level(synth):
    true, fg = _photo(synth, 8.0, 12.0, roll=2.0)
    off = C.Camera(true.yaw + 9.0, true.pitch - 6.0, true.roll, true.perspective, true.scale * 1.04,
                   true.center_x + 25.0, true.center_y - 10.0)
    vf = cameras.ViewFit("front", fg, synth["scene"], pivot=synth["pivot"])
    a = vf.final_losses([true, off])
    assert a[0] < 0.05 < a[1]                   # the true camera is (almost) perfect on the common level
    assert vf.final_losses([off, true]) == a[::-1]                      # one level for all: order does not matter
    assert a == cameras.common_final_losses(fg, synth["scene"], [true, off])
    alone = cameras.common_final_losses(fg, synth["scene"], [true])[0]
    assert alone == pytest.approx(a[0], abs=0.02)                       # the level widens, the loss barely moves


def test_fit_best_chooses_on_the_common_level_not_the_own_level(synth, monkeypatch):
    """Own-level losses are on different ROIs: a candidate with the lower OWN loss but a worse camera must lose."""
    true, fg = _photo(synth, 8.0, 12.0, roll=2.0)
    off = C.Camera(true.yaw + 9.0, true.pitch - 6.0, true.roll, true.perspective, true.scale * 1.04,
                   true.center_x + 25.0, true.center_y - 10.0)
    fake = {"front": (off, 0.01), "angled": (true, 0.5)}

    def fit_view(view, fg_, scene, pivot, seeds=None, n_refine=3, *, cross=None, sign_margin=True, ignore_faces=None):
        cam, loss = fake[view]
        return {"view": view, "camera": cam, "loss": loss, "metrics": {}, "sign": None, "refine": {},
                "fit": types.SimpleNamespace(ignore_faces=None)}

    monkeypatch.setattr(cameras, "fit_view", fit_view)
    best, results = cameras.fit_best(fg, synth["scene"], synth["pivot"], [("front", None), ("angled", None)])
    assert best["view"] == "angled" and best["loss"] > results[0]["loss"]
    assert best["common_loss"] < results[0]["common_loss"]
    assert [r["common_loss"] for r in results] == cameras.common_final_losses(fg, synth["scene"], [off, true])
