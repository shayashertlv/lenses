"""Task 2 recovery: cache identity, complete photo bases and durable JSON ($0, no Blender)."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from glasses3d.agent import procs, seeing
from glasses3d.agent.tools import finish_review
from glasses3d.g3d_host import compare as C, fit_views
from glasses3d.g3d_host.photoprint import inputs
from glasses3d.tests.agent.test_export_validation import ExportSession
from glasses3d.tests import glbfab
from glasses3d.tests.test_basis_partial import source, freeze, failure


class DriftSession(ExportSession):
    actual = "d" * 64

    def call_read(self, *args, **kwargs):
        if args[1] == "fingerprint":
            return {"status": "ok", "result": {"fingerprint": self.actual}}
        result = super().call_read(*args, **kwargs)
        result["result"]["fingerprint_before"] = self.actual
        return result


def test_drift_export_is_keyed_by_actual_scene_and_cannot_poison_head(tmp_path):
    session = DriftSession(tmp_path, glbfab.build())
    drift = seeing.Seeing(session).export_head()
    assert drift["drift"] and drift["fingerprint"] == session.actual
    assert drift["checkpoint"] is None and drift["requested_checkpoint"] == session.head["id"]
    index = json.loads((tmp_path / "work/exports/index.json").read_text())
    assert set(index) == {seeing.export_cache_key(session.actual, drift["exporter_identity"])}
    session.actual = session.head["fingerprint"]
    correct = seeing.Seeing(session).export_head()
    assert not correct["cached"] and "drift" not in correct and session.calls == 2
    assert correct["path"] != drift["path"]
    assert seeing.Seeing(session).export_head()["cached"]


def test_cached_head_export_checks_the_live_scene_before_reuse(tmp_path):
    session = DriftSession(tmp_path, glbfab.build())
    session.actual = session.head["fingerprint"]
    correct = seeing.Seeing(session).export_head()
    session.actual = "d" * 64
    drift = seeing.Seeing(session).export_head()
    assert not drift["cached"] and drift["fingerprint"] == session.actual and drift["drift"]
    assert drift["checkpoint"] is None and drift["path"] != correct["path"]
    assert session.calls == 2
    session.actual = session.head["fingerprint"]
    restored = seeing.Seeing(session).export_head()
    assert restored["cached"] and restored["path"] == correct["path"] and "drift" not in restored


def test_cached_export_refuses_when_live_identity_cannot_be_verified(tmp_path, monkeypatch):
    session = ExportSession(tmp_path, glbfab.build())
    seeing.Seeing(session).export_head()
    monkeypatch.setattr(session, "call_read", lambda *args, **kwargs: {
        "status": "worker_restarted", "message": "fingerprint call was lost"})
    result = seeing.Seeing(session).export_head()
    assert result["status"] == "worker_restarted" and result["code"] == "E_WORKER"
    assert "cached" not in result


def test_native_exporter_runtime_version_invalidates_cached_bytes(tmp_path):
    session = ExportSession(tmp_path, glbfab.build())
    session.worker = SimpleNamespace(hello={"blender": "5.2.0", "python": "3.13.0"})
    before = seeing.Seeing(session).export_head()
    session.worker.hello["blender"] = "5.2.1"
    after = seeing.Seeing(session).export_head()
    assert not after["cached"] and after["exporter_identity"] != before["exporter_identity"]
    assert after["exporter_runtime"]["blender"] == "5.2.1" and session.calls == 2


def test_exporter_implementation_change_forces_fresh_bytes(tmp_path, monkeypatch):
    session = ExportSession(tmp_path, glbfab.build())
    monkeypatch.setattr(seeing, "exporter_identity", lambda: "version-one")
    first = seeing.Seeing(session).export_head()
    monkeypatch.setattr(seeing, "exporter_identity", lambda: "version-two")
    second = seeing.Seeing(session).export_head()
    assert not second["cached"] and second["exporter_identity"] == "version-two"
    assert second["path"] != first["path"] and session.calls == 2


def test_legacy_fingerprint_only_cache_is_not_trusted(tmp_path):
    session = ExportSession(tmp_path, glbfab.build())
    first = seeing.Seeing(session).export_head()
    first.pop("exporter_identity")
    procs.write_json_atomic(tmp_path / "work/exports/index.json", {session.head["fingerprint"]: first})
    assert not seeing.Seeing(session).export_head()["cached"]
    assert session.calls == 2


@pytest.fixture
def refit(tmp_path, monkeypatch):
    folder, photos, raw = source(tmp_path, {"front.png": {}, "side.png": {}, "top.png": {}})
    doc = json.loads((folder / C.CAMERAS_JSON).read_text())
    doc["model"].update(sha256="same-model", skip=[], front_parts=None)
    C.write_json(folder / C.CAMERAS_JSON, doc)
    root = tmp_path / "job"
    previous = freeze(root, folder, photos)
    C.write_json(root / "job.json", {"images": [{"original_name": name,
        "sha256": C.sha256_file(photo)} for name, photo in photos.items()]})
    calls, fitted = [], []
    session = SimpleNamespace(root=root, head={"id": "c0001", "fingerprint": "fp"},
        new_call_id=lambda name, cid: cid or "refit", record_call=lambda *args: calls.append(args))
    tool = seeing.Seeing(session)
    export = {"status": "exported", "path": "unused.glb", "sha256": "same-model",
              "fingerprint": "fp", "checkpoint": "c0001"}
    monkeypatch.setattr(tool, "export_head", lambda *args: export)
    def fake_fit(glb, images, views, directory, **kwargs):
        fitted.append(list(images))
        new = {"model": {**deepcopy(doc["model"]), "sha256": export["sha256"]},
               "images": {name: deepcopy(raw[name]) for name in images}}
        C.write_json(directory / C.CAMERAS_JSON, new)
        return {"seconds": 0}
    monkeypatch.setattr(fit_views, "fit_views", fake_fit)
    return SimpleNamespace(tool=tool, photos=photos, views={n: "side" for n in photos},
        previous=previous, fitted=fitted, raw=raw, export=export, root=root)


def test_subset_refit_retains_other_owner_photos_in_default_basis(refit):
    result = refit.tool.fit_cameras("refine front", refit.photos, refit.views, refs=["front"])
    assert result["status"] == "ok", result
    assert refit.fitted == [["front.png"]]
    current = C.load_basis(refit.root)
    assert list(current.refs) == ["front", "side", "top"]
    assert current.camera("side") == refit.previous.camera("side")
    assert current.matte("side").tolist() == refit.previous.matte("side").tolist()
    assert set(result["refit"]["retained_refs"]) == {"side", "top"}
    binding = finish_review.evidence(refit.root, refit.tool.head, refit.export)
    assert {row["ref"] for row in binding["photos"]} == {"front", "side", "top"}


def test_failed_subset_refit_keeps_compatible_usable_camera_and_attempt(refit):
    refit.raw["front.png"].update(status="failed", camera=None, refusal="test fit failure")
    result = refit.tool.fit_cameras("retry front", refit.photos, refit.views, refs=["front"])
    assert result["status"] == "ok", result
    current = C.load_basis(refit.root)
    assert current.usable_refs() == ["front", "side", "top"]
    assert current.camera("front") == refit.previous.camera("front")
    kept = result["refs"]["front"]["retained_fit"]
    assert kept["reason"] == "refit_failed" and kept["attempt"]["refusal"] == "test fit failure"


def test_changed_geometry_refits_all_photos_instead_of_reusing_incompatible_cameras(refit):
    refit.export["sha256"] = "new-geometry"
    result = refit.tool.fit_cameras("new front", refit.photos, refit.views, refs=["front"])
    assert result["status"] == "ok", result
    assert refit.fitted == [["front.png", "side.png", "top.png"]]
    assert not result["refit"]["retained_refs"]
    assert result["refit"]["requested_refs"] == ["front"]


def test_first_subset_fit_fits_remaining_owner_photos(refit):
    # Remove the catalogue from selection without deleting evidence: use a fresh job root.
    refit.tool.root = refit.root / "fresh"
    result = refit.tool.fit_cameras("initial front", refit.photos, refit.views, refs=["front"])
    assert result["status"] == "ok", result
    assert refit.fitted == [["front.png", "side.png", "top.png"]]
    assert set(result["refs"]) == {"front", "side", "top"}


def test_frozen_basis_remains_reported_when_optional_sheet_fails(refit, monkeypatch):
    monkeypatch.setattr(C, "grid", lambda *args: (_ for _ in ()).throw(OSError("sheet locked")))
    result = refit.tool.fit_cameras("refine front", refit.photos, refit.views, refs=["front"])
    assert result["status"] == "ok" and result["sheet"] is None
    assert "sheet locked" in result["sheet_error"] and result["basis"] == C.load_basis(refit.root).id


@pytest.mark.parametrize("explicit", [False, True])
def test_legacy_subset_basis_refuses_with_all_missing_owner_photo_names(tmp_path, explicit):
    folder, photos, _ = source(tmp_path, {"front.png": {}})
    root = tmp_path / "job"
    basis = freeze(root, folder, photos)
    C.write_json(root / "job.json", {"images": [{"original_name": n}
        for n in ("front.png", "side.png", "top.png")]})
    with pytest.raises(C.MissingOwnerPhotos, match="side.png, top.png"):
        C.load_basis(root, basis.id if explicit else None)
    with pytest.raises(inputs.InputError, match="side.png, top.png"):
        inputs._photo_roles(root, basis)
    with pytest.raises(inputs.InputError, match="side.png, top.png"):
        inputs.job_from_dir(root, glb=root / "unused.glb", basis=basis.id)


def test_finish_review_names_unusable_owner_photo_and_reason(tmp_path):
    folder, photos, _ = source(tmp_path, {"front.png": {}, "broken.png": failure()})
    root = tmp_path / "job"
    freeze(root, folder, photos)
    binding = finish_review.evidence(root, {"id": "c1", "fingerprint": "fp"}, {"sha256": "sha"})
    assert "broken.png" in binding["unavailable"] and "image could not be decoded" in binding["unavailable"]
    assert list(binding["unusable_refs"]) == ["broken"]


def test_print_subset_refusal_names_every_missing_owner_photo(tmp_path):
    folder, photos, _ = source(tmp_path, {"front.png": {}})
    root = tmp_path / "job"
    basis = freeze(root, folder, photos)
    C.write_json(root / "job.json", {"images": [{"original_name": name,
        "sha256": C.sha256_file(photos["front.png"])} for name in ("front.png", "side.png", "top.png")]})
    with pytest.raises(inputs.InputError, match="side.png, top.png"):
        inputs._photo_roles(root, basis)


def test_seeing_json_retries_windows_rename_and_has_finite_numpy_diagnostics(tmp_path, monkeypatch):
    actual = procs.os.replace
    attempts = []
    def locked_once(src, dst):
        attempts.append((src, dst))
        if len(attempts) == 1:
            raise PermissionError("Defender temporary lock")
        actual(src, dst)
    monkeypatch.setattr(procs.os, "replace", locked_once)
    monkeypatch.setattr(procs.time, "sleep", lambda _: None)
    path = tmp_path / "record.json"
    C.write_json(path, {"array": np.array([1, np.inf]), "metric": float("nan")})
    assert len(attempts) == 2 and json.loads(path.read_text()) == {"array": [1, None], "metric": None}
    assert not list(tmp_path.glob("*.tmp"))


def test_camera_json_uses_retry_and_null_unavailable_metrics(tmp_path, monkeypatch):
    model = tmp_path / "model.glb"
    model.write_bytes(b"synthetic")
    row = {"status": "failed", "code": "E_PHOTO_FIT", "refusal": "no contour", "view_kind": "side",
           "contour_mean_mm": np.inf}
    monkeypatch.setattr(fit_views, "_fit_context", lambda *args: {"model": {"sha256": C.sha256_file(model)}})
    monkeypatch.setattr(fit_views, "_map", lambda jobs, workers: [deepcopy(row) for _ in jobs])
    original = procs.replace_with_retry
    written = []
    def record(src, dst):
        written.append(Path(dst).name)
        return original(src, dst)
    monkeypatch.setattr(procs, "replace_with_retry", record)
    fit_views.fit_views(model, {"front.png": tmp_path / "unused"}, {"front.png": "side"}, tmp_path / "fit",
                        workers=1, log=lambda _: None)
    assert written == ["cameras.json"]
    document = json.loads((tmp_path / "fit/cameras.json").read_text())
    assert document["images"]["front.png"]["contour_mean_mm"] is None
