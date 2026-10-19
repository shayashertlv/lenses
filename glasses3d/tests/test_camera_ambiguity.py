"""Independent symmetric silhouettes and public fit records expose orientation ambiguity."""
from dataclasses import replace

import numpy as np
import pytest
from PIL import Image

from glasses3d.g3d_host import cameras, fit_views, fitmesh, raster
from glasses3d.tests import glbfab


@pytest.fixture
def case(tmp_path):
    glb = tmp_path / "symmetric.glb"
    glb.write_bytes(glbfab.build())
    mesh = fitmesh.load_fit_mesh(glb)
    return glb, mesh, tmp_path / "photo.png", (180, 260)


def known_result(view, camera, fg, scene, pivot):
    vf = cameras.ViewFit(view, fg, scene, pivot)
    metrics = cameras.native_metrics(scene, camera, fg, cameras.matte_roi(fg))
    metrics = {key: value for key, value in metrics.items() if key not in ("mask", "face_id")}
    return {"view": view, "camera": camera, "metrics": metrics, "loss": 0., "common_loss": 0.,
            "sign": {}, "fit": vf, "refine": {"origin": "known independent camera"}}


@pytest.mark.parametrize("view,yaw,pitch,twin_yaw", [("left", 72., 11., 108.), ("right", -108., -11., -72.)])
def test_side_fit_retains_both_profile_candidates_without_changing_camera(case, monkeypatch, view, yaw, pitch, twin_yaw):
    glb, mesh, photo, shape = case
    scene = mesh.scene()
    original = raster.view_camera(mesh.frame, yaw, pitch, 1., shape, perspective=0.)
    true_mask = scene.render(original, shape)["mask"]
    # This is the independent ambiguity: identical observations from two physically different sides of profile.
    twin = replace(original, yaw=twin_yaw, pitch=-pitch)
    np.testing.assert_array_equal(true_mask, scene.render(twin, shape)["mask"])
    Image.fromarray(np.where(true_mask[..., None], 30, np.full((*shape, 3), 240)).astype("uint8")).save(photo)
    monkeypatch.setattr(fit_views.M, "matte", lambda *args, **kwargs: true_mask.copy())

    def optimizer(fg, scene, pivot, *args, **kwargs):
        result = known_result(view, original, fg, scene, pivot)
        return result, [result]

    monkeypatch.setattr(cameras, "fit_best", optimizer)
    row = fit_views.fit_image(glb, photo, view, matte_mode="default", fit_openings=False,
                             overlay_path=photo.with_name("overlay.png"))
    assert row["camera"] == original.to_dict()
    assert row["status"] == "ok" and row["iou"] == 1.
    assert "camera_orientation_unresolved" in row["flags"]
    evidence = row["camera_ambiguity"]
    assert evidence["status"] == "unresolved" and evidence["selection_changed"] is False
    assert evidence["evidence"] == "photo silhouette against current model"
    assert len(evidence["candidates"]) == 2
    selected, alternative = evidence["candidates"]
    assert selected["id"] == evidence["selected"]
    assert selected["camera"] == original.to_dict()
    assert alternative["source"] == "opposite_profile_probe"
    lo, hi = alternative["yaw_window"]
    assert lo <= alternative["camera"]["yaw"] <= hi
    assert alternative["iou"] > .98  # real constrained Powell/ICP remains in the equally explanatory basin
    assert selected["profile_region"] != alternative["profile_region"]
    for candidate in evidence["candidates"]:
        assert set(original.to_dict()) <= set(candidate["camera"])
        path = photo.parent / candidate["overlay"]
        assert Image.open(path).size == (shape[1], shape[0])
        assert fit_views.sha256_file(path) == candidate["overlay_sha256"]


def test_top_fit_keeps_front_back_candidate_poses_and_label_refusals(case, monkeypatch):
    glb, mesh, photo, shape = case
    scene = mesh.scene()
    original = raster.view_camera(mesh.frame, 0., 65., 1., shape, perspective=.1)
    second = replace(original, yaw=180., pitch=-65.)
    fg = scene.render(original, shape)["mask"]
    Image.new("RGB", (shape[1], shape[0]), "white").save(photo)
    monkeypatch.setattr(fit_views.M, "matte", lambda *args, **kwargs: fg.copy())

    def optimizer(fg, scene, pivot, *args, **kwargs):
        results = [known_result("top", original, fg, scene, pivot),
                   known_result("topb", second, fg, scene, pivot)]
        return results[0], results

    monkeypatch.setattr(cameras, "fit_best", optimizer)
    row = fit_views.fit_image(glb, photo, "top", matte_mode="default", fit_openings=False)
    assert row["camera"] == original.to_dict() and row["status"] == "ok"
    report = row["camera_ambiguity"]
    assert report["kind"] == "axial_front_back"
    candidates = report["candidates"]
    assert [c["view"] for c in candidates] == ["top", "topb"]
    assert candidates[1]["camera"] == second.to_dict()
    assert candidates[1]["label_refusal"]  # retained evidence never silently passes the existing sign check
    assert candidates[0]["label_refusal"] is None
    assert all(c["overlay"] is None for c in candidates)


def test_profile_domain_reaches_powell_and_contour_icp(case, monkeypatch):
    _, mesh, _, shape = case
    scene = mesh.scene()
    camera = raster.view_camera(mesh.frame, 100., 9., 1., shape, perspective=.1)
    fg = scene.render(camera, shape)["mask"]
    vf = cameras.ViewFit("left", fg, scene)
    calls = []

    def powell(level, camera, *args, **kwargs):
        calls.append(("powell", kwargs["yaw_win"]))
        return camera, 0.

    def icp(scene, level, camera, unit, pivot, yaw_window, **kwargs):
        calls.append(("icp", yaw_window))
        return None

    monkeypatch.setattr(vf, "powell", powell)
    monkeypatch.setattr(cameras, "contour_icp", icp)
    vf.staged(vf.levels["fit"], camera, ({},), 1, yaw_win=(90., 125.))
    assert calls == [("powell", (90., 125.)), ("icp", (90., 125.))]


@pytest.mark.parametrize("view,yaw,windows", [("left", 90., [(55., 90.), (90., 125.)]),
                                             ("right", -90., [(-125., -90.), (-90., -55.)])])
def test_exact_profile_retains_both_half_domains(view, yaw, windows):
    from glasses3d.g3d_host.camera import Camera
    from glasses3d.g3d_host.camera_ambiguity import opposite_profile
    candidates = opposite_profile(view, Camera(yaw, 4., 0., .1, 100., 100., 100.))
    assert [window for _, window in candidates] == windows
    assert all(lo < camera.yaw < hi for camera, (lo, hi) in candidates)
    assert all(camera.pitch == -4. for camera, _ in candidates)


def test_non_ambiguous_view_does_not_add_a_probe(case):
    from glasses3d.g3d_host.camera_ambiguity import evidence
    _, mesh, _, shape = case
    camera = raster.view_camera(mesh.frame, 0., 10., 1., shape)
    fg = mesh.scene().render(camera, shape)["mask"]
    result = known_result("front", camera, fg, mesh.scene(), None)
    assert evidence(result, [result], fg, mesh.scene(), None) == (None, [])


def test_profile_probe_keeps_optional_faces_and_off_axis_eye_domain(case, monkeypatch):
    from glasses3d.g3d_host.camera_ambiguity import profile_probes
    _, mesh, _, shape = case
    scene = mesh.scene()
    camera = replace(raster.view_camera(mesh.frame, 72., 10., 1., shape, perspective=.15), eye_x=.05, eye_y=-.03)
    fg = scene.render(camera, shape)["mask"]
    optional = np.arange(scene.n_faces) % 2 == 0
    seen = []

    def staged(self, level, start, stages, maxfev, **kwargs):
        np.testing.assert_array_equal(level.ignore_faces, optional)
        assert self.eye_limit == scene.max_radius
        assert (start.eye_x, start.eye_y) == (camera.eye_x, camera.eye_y)
        assert kwargs["yaw_win"] == (90., 125.)
        seen.append(level.name)
        return start, 0.

    monkeypatch.setattr(cameras.ViewFit, "staged", staged)
    candidates, _ = profile_probes("left", camera, fg, scene, None, optional, off_axis=True)
    assert seen == ["fit", "orientation_final"]
    assert len(candidates) == 1 and candidates[0][0].yaw == 108.
    assert (candidates[0][0].eye_x, candidates[0][0].eye_y) == (.05, -.03)


@pytest.mark.parametrize("eyes", [(0., 0.), (.05, -.03), (-.05, .03)])
@pytest.mark.parametrize("perspective", [0., .25])
def test_profile_probe_aligns_full_camera_before_refinement(case, monkeypatch, eyes, perspective):
    from glasses3d.g3d_host.camera_ambiguity import profile_probes
    _, mesh, _, shape = case
    scene = mesh.scene()
    camera = replace(raster.view_camera(mesh.frame, 72., 10., 1., shape, perspective=perspective),
                     eye_x=eyes[0], eye_y=eyes[1])
    fg = scene.render(camera, shape)["mask"]
    starts = []

    def staged(self, level, start, stages, maxfev, **kwargs):
        if not starts:
            # A local search must start centred using its complete projection. Appending eye
            # offsets after zero-eye alignment displaces this independently computed bbox.
            xy = cameras.project(scene.hull, start)
            x0, y0, x1, y1 = level.bbox
            np.testing.assert_allclose((xy.min(0) + xy.max(0)) / 2.,
                                       [(x0 + x1 - 1) / 2., (y0 + y1 - 1) / 2.], atol=1e-9)
            assert (start.eye_x, start.eye_y) == eyes
        starts.append(start)
        return start, 0.

    monkeypatch.setattr(cameras.ViewFit, "staged", staged)
    profile_probes("left", camera, fg, scene, None, off_axis=True)
    assert len(starts) == 2


@pytest.mark.parametrize("failure", ["search", "overlay"])
def test_diagnostic_failure_preserves_completed_fit(case, monkeypatch, failure):
    from types import SimpleNamespace
    from glasses3d.g3d_host import camera_ambiguity
    glb, mesh, photo, shape = case
    scene = mesh.scene()
    camera = raster.view_camera(mesh.frame, 72., 10., 1., shape)
    fg = scene.render(camera, shape)["mask"]
    Image.new("RGB", (shape[1], shape[0]), "white").save(photo)
    monkeypatch.setattr(fit_views.M, "matte", lambda *args, **kwargs: fg.copy())

    def optimizer(fg, scene, pivot, *args, **kwargs):
        result = known_result("left", camera, fg, scene, pivot)
        return result, [result]

    def fail(*args, **kwargs):
        raise RuntimeError("diagnostic failure control")

    candidate = {"id": "failed_overlay", "overlay": None}
    report = {"schema": "g3d.camera_orientation/1", "status": "unresolved", "candidates": [candidate]}
    monkeypatch.setattr(cameras, "fit_best", optimizer)
    monkeypatch.setattr(camera_ambiguity, "evidence", fail if failure == "search" else
                        lambda *args, **kwargs: (report, [(candidate, SimpleNamespace(render=fail), camera)]))
    row = fit_views.fit_image(glb, photo, "left", matte_mode="default", fit_openings=False,
                             overlay_path=photo.with_name("overlay.png"))
    assert row["status"] == "ok" and row["camera"] == camera.to_dict() and row["iou"] == 1.
    if failure == "search":
        assert row["camera_ambiguity"]["status"] == "unavailable"
        assert row["camera_ambiguity"]["error"]["type"] == "RuntimeError"
        assert "camera_orientation_unavailable" in row["flags"]
    else:
        assert candidate["overlay"] is None and candidate["overlay_error"]["type"] == "RuntimeError"
        assert Image.open(row["overlay"]).size == (shape[1], shape[0])


@pytest.mark.parametrize("alternative_valid", [True, False])
def test_top_selection_uses_existing_valid_alternative_or_retains_refusal(case, monkeypatch, alternative_valid):
    glb, mesh, photo, shape = case
    scene = mesh.scene()
    original = raster.view_camera(mesh.frame, 0., -65., 1., shape, perspective=0.)
    alternative = replace(original, yaw=180., pitch=65. if alternative_valid else -65.)
    fg = scene.render(original, shape)["mask"]
    if alternative_valid:
        # Both directions explain the same independent observation on symmetric orthographic geometry.
        np.testing.assert_array_equal(fg, scene.render(alternative, shape)["mask"])
    Image.new("RGB", (shape[1], shape[0]), "white").save(photo)
    monkeypatch.setattr(fit_views.M, "matte", lambda *args, **kwargs: fg.copy())

    def optimizer(fg, scene, pivot, *args, **kwargs):
        rows = [known_result("top", original, fg, scene, pivot),
                known_result("topb", alternative, fg, scene, pivot)]
        # Independent optimizer outputs deliberately rank the invalid label first.
        rows[0].update(loss=.1, common_loss=.1)
        rows[1].update(loss=.2, common_loss=.2)
        return rows[0], rows

    monkeypatch.setattr(cameras, "fit_best", optimizer)
    row = fit_views.fit_image(glb, photo, "top", matte_mode="default", fit_openings=False)
    if alternative_valid:
        assert row["camera"] == alternative.to_dict() and row["status"] == "ok"
    else:
        assert row["camera"] == original.to_dict() and row["status"] == "refused"
    selection = row["candidate_selection"]
    assert selection["original"]["camera"] == original.to_dict()
    assert selection["changed"] is alternative_valid
    assert row["camera_ambiguity"]["selection_changed"] is alternative_valid
    assert row["camera_ambiguity"]["diagnostic_changes_selection"] is False
    assert len(row["all_candidates"]) == len(row["camera_ambiguity"]["candidates"]) == 2
    if alternative_valid:
        assert row["loss_common_level"] == .2 > selection["original"]["loss_common_level"]
        assert selection["eligible_candidates"] == ["view_2"]
        assert selection["original"]["label_refusal"] and selection["selected"]["label_refusal"] is None
    else:
        assert row["code"] == "E_VIEW_SIGN" and selection["eligible_candidates"] == []
        assert selection["reason"] == "no_candidate_passes_unchanged_checks"


def test_candidate_selection_does_not_relax_overlap_threshold(case):
    _, mesh, _, shape = case
    scene = mesh.scene()
    original = raster.view_camera(mesh.frame, 0., -65., 1., shape)
    alternative = replace(original, yaw=180., pitch=65.)
    fg = scene.render(original, shape)["mask"]
    rows = [known_result("top", original, fg, scene, None),
            known_result("topb", alternative, fg, scene, None)]
    rows[1]["metrics"]["iou"] = np.nextafter(fit_views.MIN_USABLE_IOU, 0.)
    selected, record = fit_views.select_camera_candidate("top", rows[0], rows)
    assert selected is rows[0] and record["eligible_candidates"] == []
    rows[1]["metrics"]["iou"] = fit_views.MIN_USABLE_IOU
    selected, record = fit_views.select_camera_candidate("top", rows[0], rows)
    assert selected is rows[1] and record["eligible_candidates"] == ["view_2"]
