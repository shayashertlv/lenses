"""General view priors and lens-optional scoring, with real raster evidence and bounded search spies."""
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from glasses3d.g3d_host import camera as C
from glasses3d.g3d_host import cameras, raster


@pytest.fixture(scope="module")
def scene():
    # A hard frame strip beside a broad lens, with exact per-face identities.
    vertices = np.array([[-8, -4, 0], [-4, -4, 0], [-4, 4, 0], [-8, 4, 0],
                         [-3, -4, 0], [8, -4, 0], [8, 4, 0], [-3, 4, 0]], float)
    faces = np.array([[0, 1, 2], [0, 2, 3], [4, 5, 6], [4, 6, 7]])
    frame = C.NormFrame.from_bbox(vertices)
    model = raster.get_scene(vertices, faces, frame)
    shape = (80, 120)
    camera = raster.view_camera(frame, 0., 0., 5., shape, perspective=0.)
    rendered = model.render(camera, shape)
    optional = np.array([False, False, True, True])
    photo = (rendered["face_id"] >= 0) & (rendered["face_id"] < 2)
    return model, camera, photo, optional


@pytest.mark.parametrize("view,sign,window", [("rear_threequarter_left", 1., (100., 172.)),
                                            ("rear_threequarter_right", -1., (-172., -100.))])
def test_rear_threequarter_signed_priors_windows_and_seed_wrapping(view, sign, window):
    expected = [(sign * (180. - y), p, 0., "prior") for y in cameras.ANGLED_YAWS for p in cameras.PITCHES]
    assert view in cameras.VIEWS and cameras.prior_seeds(view) == expected
    assert len(expected) == 42 and all(window[0] <= row[0] <= window[1] for row in expected)
    assert cameras.yaw_window(view, -sign * 140.) == window  # the label, not a supplied seed, fixes handedness
    assert cameras.pitch_window(view) == cameras.PITCH_BOUNDS
    seeds = [(sign * 140., 10., 0., "inside"), (sign * 140. + 360., 10., 0., "wrapped"),
             (-sign * 140., 10., 0., "other-side"), (sign * 80., 10., 0., "front")]
    assert cameras.wrap_seeds(view, seeds) == [(sign * 140., 10., 0., "inside"),
                                             (sign * 140., 10., 0., "wrapped")]
    bounds = cameras._bounds(C.Camera(sign * 140., 10., 0., .1, 500., 60., 40.), 100., window)
    assert bounds[0] == window and bounds[1] == cameras.PITCH_BOUNDS


def test_rear_views_receive_but_never_supply_cross_seeds():
    winners = {"front": C.Camera(2., 10., 1., .1, 500., 60., 40.),
               "rear_threequarter_left": C.Camera(130., 15., 0., .1, 500., 60., 40.),
               "rear_threequarter_right": C.Camera(-150., 5., 0., .1, 500., 60., 40.)}
    for view in ("back", "angled", *cameras.REAR_THREEQUARTER_VIEWS):
        seeds = cameras.cross_seeds(view, winners)
        assert seeds and {origin.split(":")[1] for *_, origin in seeds} == {"front"}
    for view, sign in zip(cameras.REAR_THREEQUARTER_VIEWS, (1., -1.)):
        seeds = cameras.cross_seeds(view, winners)
        assert seeds == [(sign * 140. + 2., 10., 1., "cross:front:rotated"),
                         (sign * 140. - 2., 10., -1., "cross:front:mirrored"),
                         (sign * 140. + 2., -10., 1., "cross:front:pitch_twin")]
        assert cameras.wrap_seeds(view, seeds) == seeds


@pytest.mark.parametrize("view", cameras.REAR_THREEQUARTER_VIEWS)
def test_rear_coarse_starts_retain_distinct_yaws_at_the_same_pitch(scene, monkeypatch, view):
    model, cam, photo, _ = scene
    vf = cameras.ViewFit(view, photo, model)
    sign = 1. if view.endswith("_left") else -1.
    monkeypatch.setattr(vf, "_seed", lambda yaw, pitch, roll, origin: (.1, replace(cam, yaw=yaw, pitch=pitch)))
    monkeypatch.setattr(vf, "staged", lambda level, camera, *args, **kwargs: (camera, .1))
    vf.run_starts([(sign * y, 10., 0., "prior") for y in (120., 135., 150.)], 3)
    assert len(vf.coarse) == 3 and len({row[1].yaw for row in vf.coarse}) == 3


def test_optional_native_metrics_preserve_every_raw_measurement(scene):
    model, cam, photo, optional = scene
    roi = (0, 0, photo.shape[1], photo.shape[0])
    raw = cameras.native_metrics(model, cam, photo, roi)
    measured = cameras.native_metrics(model, cam, photo, roi, optional)
    for key, value in raw.items():
        if isinstance(value, np.ndarray):
            np.testing.assert_array_equal(measured[key], value)
        else:
            assert measured[key] == value
    assert measured["iou"] < .4 and measured["iou_lens_optional"] == 1.
    assert measured["lens_pixels_optional"] > 0
    assert measured["render_only_px_lens_optional"] == 0 < measured["render_only_px"]
    assert measured["contour_mean_px_lens_optional"] == 0. < measured["contour_mean_px"]
    assert measured["contour_p95_px_lens_optional"] == 0.
    zero = cameras.native_metrics(model, cam, photo, roi, np.zeros(model.n_faces, bool))
    assert zero["iou_lens_optional"] == raw["iou"] and zero["lens_pixels_optional"] == 0


def test_optional_loss_does_not_hide_unexplained_photo_foreground(scene):
    model, cam, photo, optional = scene
    raw = cameras.common_final_losses(photo, model, [cam])[0]
    effective = cameras.common_final_losses(photo, model, [cam], optional)[0]
    assert effective < 1e-12 < raw
    unexplained = photo.copy()
    unexplained[10:15, 45:50] = True  # outside either rendered surface
    score = cameras.common_final_losses(unexplained, model, [cam], optional)[0]
    assert score > effective + .01
    # An observed patch inside the optional lens must still be explained; it is
    # retained in the effective silhouette rather than removed from the photo.
    observed = photo.copy()
    observed[35:42, 55:63] = True
    assert cameras.common_final_losses(observed, model, [cam], optional)[0] < 1e-12


@pytest.mark.parametrize("bad", [np.ones(4, int), np.ones((4, 1), bool), np.ones(3, bool), [1, 0, 1, 0]])
def test_invalid_optional_face_masks_refuse_before_search(scene, monkeypatch, bad):
    model, cam, photo, _ = scene
    monkeypatch.setattr(cameras.ViewFit, "run_starts", lambda *args: pytest.fail("invalid mask reached search"))
    with pytest.raises(ValueError, match="boolean mask"):
        cameras.fit_view("front", photo, model, None, ignore_faces=bad)
    with pytest.raises(ValueError, match="boolean mask"):
        cameras.fit_best(photo, model, None, [("front", None)], ignore_faces=bad)
    with pytest.raises(ValueError, match="boolean mask"):
        cameras.native_metrics(model, cam, photo, (0, 0, 120, 80), bad)
    with pytest.raises(ValueError, match="boolean mask"):
        cameras.common_final_losses(photo, model, [cam], bad)


def test_set_ignore_is_stable_and_can_reset(scene):
    model, _, photo, optional = scene
    vf = cameras.ViewFit("front", photo, model)
    supplied = optional.copy()
    vf.set_ignore(supplied)
    supplied[:] = False
    np.testing.assert_array_equal(vf.ignore_faces, optional)
    assert all(level.ignore_faces is vf.ignore_faces for level in vf.levels.values())
    assert not vf.ignore_faces.flags.writeable
    expected = model.V_norm[np.unique(model.F[~optional])]
    for level in vf.levels.values():
        np.testing.assert_array_equal(level.required_hull, expected)
        assert not level.required_hull.flags.writeable
    vf.set_ignore(None)
    assert vf.ignore_faces is None and all(level.ignore_faces is None for level in vf.levels.values())
    assert all(level.required_hull is None for level in vf.levels.values())


@pytest.mark.parametrize("opaque", [False, True])
def test_optional_alignment_accepts_clear_and_opaque_lens_support(scene, opaque):
    model, truth, clear, optional = scene
    photo = model.render(truth, clear.shape)["mask"] if opaque else clear
    vf = cameras.ViewFit("front", photo, model)
    vf.set_ignore(optional)
    level = vf.levels["coarse"]
    aligned = cameras.align(model, level, truth.yaw, truth.pitch, truth.roll, truth.perspective)
    assert aligned == truth
    rendered = level.render(model, aligned)
    assert level.score(rendered["mask"], level.ignored(rendered))[0] < 1e-12
    # An optional policy cannot replace the historical full candidate when it
    # explains an opaque lens, nor retain the lens bbox when the lens is clear.
    full = cameras.align(model, cameras.Level(photo, cameras.COARSE_W),
                         truth.yaw, truth.pitch, truth.roll, truth.perspective)
    assert (aligned == full) is opaque
    if not opaque:
        assert level.loss(model, full) > level.loss(model, aligned)


@pytest.mark.parametrize("opaque", [False, True])
def test_icp_uses_real_silhouette_points_not_borrowed_photo_edges(scene, monkeypatch, opaque):
    model, cam, clear, optional = scene
    photo = model.render(cam, clear.shape)["mask"] if opaque else clear.copy()
    if not opaque:
        photo[35:42, 55:63] = True  # a photo patch wholly inside the optional lens, not its geometric silhouette
    vf = cameras.ViewFit("front", photo, model)
    vf.set_ignore(optional)
    level = vf.levels["fit"]
    projected, solving = [], [False]
    original_project = raster._project_norm

    def project(points, camera):
        if solving[0]:
            projected.append(model.frame.to_mm(points))
        return original_project(points, camera)

    def solve(residual, x, **kwargs):
        solving[0] = True
        residual(x)
        solving[0] = False
        return SimpleNamespace(x=x)

    monkeypatch.setattr(raster, "_project_norm", project)
    monkeypatch.setattr(cameras.optimize, "least_squares", solve)
    assert cameras.contour_icp(model, level, cam, level.width_native, None,
                               cameras.yaw_window("front", cam.yaw), iters=1) is not None
    assert projected and all(np.isfinite(points).all() for points in projected)
    points = np.concatenate(projected)
    if opaque:
        assert (points[:, 0] > 0).any()  # the real lens silhouette remains geometric evidence
    else:
        assert (points[:, 0] <= -4.).all()  # no lens-interior ray point supplies a camera derivative


def test_fit_best_installs_optional_policy_before_every_seed_and_retains_it_at_all_levels(scene, monkeypatch):
    model, _, photo, optional = scene
    events = []
    original_seed = cameras.ViewFit._seed
    original_common = cameras.common_final_losses

    def seeded(self, *args):
        np.testing.assert_array_equal(self.ignore_faces, optional)
        assert all(level.ignore_faces is self.ignore_faces for level in self.levels.values())
        events.append(("seed", self.view))
        return original_seed(self, *args)

    def staged(self, level, cam, *args, **kwargs):
        assert level.ignore_faces is self.ignore_faces
        events.append((level.name, self.view))
        return cam, level.loss(self.scene, cam)

    def common(fg, raster_scene, cameras_, ignore_faces=None):
        np.testing.assert_array_equal(ignore_faces, optional)
        events.append(("common", len(cameras_)))
        return original_common(fg, raster_scene, cameras_, ignore_faces)

    monkeypatch.setattr(cameras.ViewFit, "_seed", seeded)
    monkeypatch.setattr(cameras.ViewFit, "staged", staged)
    monkeypatch.setattr(cameras, "common_final_losses", common)
    best, results = cameras.fit_best(photo, model, None,
                                    [("front", [(0., 10., 0., "test")]),
                                     ("back", [(180., 10., 0., "test")])],
                                    cross={"front": [(5., 10., 0., "cross:test")]}, ignore_faces=optional)
    assert sum(kind == "seed" for kind, _ in events) == 3
    assert ("common", 2) in events
    for result in results:
        vf = result["fit"]
        assert set(vf.levels) == {"coarse", "fit", "final"}
        assert all(level.ignore_faces is vf.ignore_faces for level in vf.levels.values())
        assert result["sign"] is not None and "lens_pixels_optional" in result["metrics"]
    assert best["common_loss"] == min(result["common_loss"] for result in results)


def test_rear_threequarter_fit_from_full_signed_priors():
    from glasses3d.tests.test_cameras_fit import SHAPE, glasses

    vertices, faces = glasses()
    frame = C.NormFrame.from_bbox(vertices)
    model = raster.get_scene(vertices, faces, frame)
    front = vertices[:, 2] >= -12.
    pivot = frame.to_norm(((vertices[front].min(0) + vertices[front].max(0)) / 2)[None])[0]
    true = raster.view_camera(frame, 133., 12., 3., SHAPE, roll=2., perspective=.15)
    photo = model.render(true, SHAPE)["mask"]
    best, results = cameras.fit_best(photo, model, pivot,
                                    [(view, None) for view in reversed(cameras.REAR_THREEQUARTER_VIEWS)])
    assert best["view"] == "rear_threequarter_left"
    assert best["metrics"]["iou"] > .98, best["metrics"]
    assert abs(best["camera"].yaw - true.yaw) < 2., best["camera"]
    assert abs(best["camera"].pitch - true.pitch) < 2., best["camera"]
    assert best["common_loss"] == min(result["common_loss"] for result in results)
    for result in results:
        lo, hi = cameras.yaw_window(result["view"], result["camera"].yaw)
        assert lo <= result["camera"].yaw <= hi
