"""Unusable camera evidence stays visible at the seeing-tool boundary."""
from types import SimpleNamespace

import pytest

from glasses3d.agent.seeing import Seeing, SeeingError
from glasses3d.g3d_host import compare as C
from glasses3d.tests.test_basis_partial import source, freeze, failure


@pytest.fixture
def partial(tmp_path, monkeypatch):
    folder, photos, _ = source(tmp_path, {"good.png": {}, "failed.png": failure()})
    root = tmp_path / "job"
    basis = freeze(root, folder, photos)
    calls = []
    session = SimpleNamespace(root=root, new_call_id=lambda name, cid: cid or "call-test",
                              record_call=lambda *args: calls.append(args))
    seeing = Seeing(session)
    monkeypatch.setattr(seeing, "bounds", lambda *_: {
        "lo": [-70, -20, -145], "hi": [70, 20, 5], "origin_mm": [0, 0, 0]})
    return seeing, basis, calls


def test_automatic_reference_expansion_keeps_only_usable_cameras(partial):
    seeing, basis, _ = partial
    views = seeing.resolve_views("reference", 128, basis_id=basis.id, call_id="resolve")
    assert [v["ref"] for v in views] == ["good"]
    assert Seeing._ref_ids(basis, None) == ["good"]
    assert Seeing._ref_ids(basis, "all") == ["good"]
    assert list(basis.refs) == ["good", "failed"]
    assert basis.unusable_refs()["failed"]["code"] == "E_PHOTO_FIT"


@pytest.mark.parametrize("operation", ["render", "compare", "pick"])
def test_explicit_failed_photo_refuses_with_named_diagnostic_and_journal(partial, operation):
    seeing, basis, calls = partial
    if operation == "render":
        result = seeing.render_views(["reference:failed"], basis=basis.id)
    elif operation == "compare":
        result = seeing.compare_to_photos(refs=["failed"], basis=basis.id, passes=())
    else:
        result = seeing.pick_surface_point("reference:failed", [[20, 20]], basis=basis.id)
    assert result["code"] == "E_UNUSABLE_CAMERA", result
    assert "failed" in result["message"] and "image could not be decoded" in result["message"]
    assert calls and calls[-1][-1] == result


def test_all_failed_explicit_basis_cannot_expand_a_fake_camera(tmp_path, monkeypatch):
    folder, photos, _ = source(tmp_path, {"failed.png": failure()})
    root = tmp_path / "job"
    basis = freeze(root, folder, photos)
    seeing = Seeing(SimpleNamespace(root=root, head={"id": "c0000"},
        new_call_id=lambda name, cid: cid or "call-test", record_call=lambda *args: None))
    monkeypatch.setattr(seeing, "bounds", lambda *_: {
        "lo": [-70, -20, -145], "hi": [70, 20, 5], "origin_mm": [0, 0, 0]})
    with pytest.raises(SeeingError, match="no usable photo cameras") as caught:
        seeing.resolve_views("reference", 128, basis_id=basis.id, call_id="resolve")
    assert caught.value.code == "E_UNUSABLE_CAMERA"
    monkeypatch.setattr(seeing, "_render", lambda *args, **kwargs: {"status": "ok", "result": {"views": []}})
    monkeypatch.setattr(seeing, "_target_points", lambda b, *args: [b["lo"], b["hi"]])
    rendered = seeing.render_views("all", basis=basis.id, passes=("native",), size_px=128)
    assert rendered["status"] == "ok" and rendered["views"]
    assert all(v["kind"] == "canonical" for v in rendered["views"].values())
    assert rendered["unusable_refs"] == basis.unusable_refs()


def test_failed_fit_summary_has_null_metrics_and_full_diagnostics():
    diagnostic = failure()
    result = Seeing._fit_ref_summary({"fit": diagnostic, "camera": None,
                                     "matte_mode": "glossy_floor"}, usable=False)
    assert result["usable"] is False and result["code"] == "E_PHOTO_FIT"
    assert result["error"] == diagnostic["error"] and result["refusal"] == diagnostic["refusal"]
    assert result["matte_mode"] == "glossy_floor"
    assert all(result[key] is None for key in ("iou", "contour_mean_mm", "yaw", "pitch"))


def test_lens_optional_fit_metrics_are_visible_separately_from_raw_metrics():
    result = Seeing._fit_ref_summary({"fit": {"status": "ok", "iou": .4,
        "iou_lens_optional": .99, "lens_pixels_optional": 100,
        "contour_mean_px_lens_optional": .2}, "camera": {"yaw": 20, "pitch": 12}}, usable=True)
    assert result["iou"] == .4 and result["iou_lens_optional"] == .99
    assert result["lens_pixels_optional"] == 100 and result["contour_mean_px_lens_optional"] == .2
