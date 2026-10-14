"""Poor registration remains bound diagnostic evidence and never claims a usable rigid camera."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from glasses3d.agent.seeing import Seeing
from glasses3d.g3d_host import cameras, fit_views as FV, compare as C
from glasses3d.g3d_host.camera import Camera
from glasses3d.tests.test_basis_partial import source, freeze
from glasses3d.tests.test_fit_views_general import inputs, no_optional  # noqa: F401 - fixtures


@pytest.mark.parametrize("raw,optional,usable", [(.4999, None, False), (.5, None, True), (.9, None, True),
                                               (.2, .95, True), (.2, .4999, False), (.9, .4, False)])
def test_fitter_labels_selected_metric_and_retains_all_diagnostics(inputs, no_optional, tmp_path, monkeypatch,
                                                                raw, optional, usable):
    model, photos = inputs

    def optimizer(fg, scene, pivot, candidates, nr, **kwargs):
        cam = Camera(0., 12., 90., .1, 100., 90., 60.)
        metrics = cameras.native_metrics(scene, cam, fg, cameras.matte_roi(fg))
        metrics["iou"] = raw
        if optional is not None:
            metrics["iou_lens_optional"] = optional
        row = {"view": candidates[0][0], "camera": cam, "metrics": metrics, "loss": .5, "common_loss": .5,
               "sign": {}, "refine": {"origin": "policy-fixture"}, "fit": SimpleNamespace(evals=1)}
        return row, [row]

    monkeypatch.setattr(cameras, "fit_best", optimizer)
    overlay = tmp_path / "overlay.png"
    row = FV.fit_image(model, photos["front.png"], "front", overlay_path=overlay)
    assert row["iou"] == raw and row.get("iou_lens_optional") == optional
    assert row["status"] == ("ok" if usable else "unusable")
    assert row["usability"] == FV.camera_usability({"iou": raw, **({"iou_lens_optional": optional}
                                                                  if optional is not None else {})})
    assert row["usability"]["minimum_iou"] == .5
    assert row["camera"]["roll"] == 90. and row["matte"]["foreground_px"] > 0
    assert overlay.is_file() and len(row["all_candidates"]) == 1
    assert ("rigid_fit" in row) is usable
    if not usable:
        assert row["code"] == "E_CAMERA_UNUSABLE" and "insufficient_camera_overlap" in row["flags"]
        assert row["refusal"] == row["usability"]["reason"]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_overlap_cannot_certify_a_camera(value):
    result = FV.camera_usability({"iou": value})
    assert result["usable"] is False and result["value"] is None


def test_label_sign_refusal_has_precedence_over_low_overlap(inputs, no_optional, monkeypatch):
    model, photos = inputs

    def optimizer(fg, scene, pivot, candidates, nr, **kwargs):
        cam = Camera(0., -60., 0., .1, 100., 90., 60.)
        metrics = cameras.native_metrics(scene, cam, fg, cameras.matte_roi(fg))
        metrics["iou"] = .1
        row = {"view": "top", "camera": cam, "metrics": metrics, "loss": 1., "common_loss": 1.,
               "sign": {}, "refine": {"origin": "policy-fixture"}, "fit": SimpleNamespace(evals=1)}
        return row, [row]

    monkeypatch.setattr(cameras, "fit_best", optimizer)
    row = FV.fit_image(model, photos["front.png"], "top")
    assert row["status"] == "refused" and row["code"] == "E_VIEW_SIGN"
    assert "contradicts label" in row["refusal"] and row["usability"]["usable"] is False
    assert set(row["flags"]) == {"label_sign_contradicted", "insufficient_camera_overlap"}
    assert "rigid_fit" not in row


def test_unusable_camera_is_frozen_but_never_default_or_projection_source(tmp_path):
    root = tmp_path / "job"
    folder, photos, _ = source(tmp_path, {"good.png": {"iou": .9}})
    good = freeze(root, folder, photos)
    u = FV.camera_usability({"iou": .3})
    folder, photos, raw = source(tmp_path, {"poor.png": {
        "status": "unusable", "iou": .3, "code": "E_CAMERA_UNUSABLE", "refusal": u["reason"],
        "usability": u, "matte": {"foreground_px": 1200}}})
    poor = freeze(root, folder, photos)
    assert list(poor.refs) == ["poor"] and poor.usable_refs() == []
    assert poor.refs["poor"]["camera"] == raw["poor.png"]["camera"] and poor.matte("poor").any()
    assert poor.refs["poor"]["fit"]["usability"] == u
    assert C.load_basis(root).id == good.id
    with pytest.raises(C.UnusableReference):
        poor.camera("poor")
    summary = Seeing._fit_ref_summary(poor.refs["poor"], usable=False)
    assert summary["status"] == "unusable" and summary["usability"] == u and summary["roll"] == 0.
    changed = deepcopy(poor.meta)
    changed["refs"]["poor"]["fit"]["usability"]["minimum_iou"] = .1
    C.write_json(poor.dir / "basis.json", changed)
    with pytest.raises(ValueError, match="evidence"):
        C.load_basis(root, poor.id)


def test_unusable_photo_cannot_seed_another_fit(inputs, no_optional, tmp_path, monkeypatch):
    model, photos = inputs
    photos = {name: photos[name] for name in ("front.png", "angle.png")}

    def fitted(glb, path, kind, **kwargs):
        assert kwargs["cross_winners"] is None
        return {"status": "unusable", "view": "front", "camera": {},
                "model": kwargs["_model_context"]["model"]}

    monkeypatch.setattr(FV, "fit_image", fitted)
    monkeypatch.setattr(FV, "_row", lambda *args: "unusable fixture")
    result = FV.fit_views(model, photos, {"front.png": "front", "angle.png": "angled"},
                          tmp_path / "fit", workers=1, log=lambda _: None)
    assert list(result["images"]) == list(photos) and not result["cross_winners"]
