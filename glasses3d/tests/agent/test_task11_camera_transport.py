"""Camera alternatives must survive freezing, partial refits and bounded tool text."""
from copy import deepcopy
import json

from agents.tool import ToolOutputText
from PIL import Image
import pytest

from glasses3d.agent.seeing import Seeing
from glasses3d.agent import tool_reports
from glasses3d.agent.tools import _read_image_path
from glasses3d.g3d_host import compare as C
from glasses3d.tests.test_basis_partial import CAMERA, MODEL, source


def orientation(folder):
    rows = []
    for name, yaw in (("selected", 70), ("opposite_profile", 110)):
        path = folder / f"overlay_side_orientation_{name}.png"
        Image.new("RGB", (80, 60), "purple" if yaw == 70 else "green").save(path)
        rows.append({"id": name, "camera": {**CAMERA, "yaw": yaw}, "iou": .9,
                     "overlay": path.name, "overlay_sha256": C.sha256_file(path)})
    return {"schema": "g3d.camera_orientation/1", "status": "unresolved",
            "note": "Silhouettes alone do not identify front/back orientation.",
            "selection_changed": False, "selected": "selected", "candidates": rows}


def prepared(tmp_path):
    folder, photos, rows = source(tmp_path, {"side.png": {}})
    evidence = orientation(folder)
    rows["side.png"]["camera_ambiguity"] = evidence
    rows["side.png"]["candidate_selection"] = {
        "schema": "g3d.camera_candidate_selection/1", "changed": True,
        "selected": {"id": "view_2"}, "original": {"id": "view_1"},
        "reason": "usable_label_consistent_candidate_over_invalid_loss_winner"}
    C.write_json(folder / C.CAMERAS_JSON, {"model": MODEL, "images": rows})
    basis = C.import_cameras_json(tmp_path / "job", folder / C.CAMERAS_JSON, photos,
                                  reason="orientation transport test")
    return basis, photos, evidence


def test_alternatives_reach_astra_with_both_readable_overlays(tmp_path):
    basis, photos, evidence = prepared(tmp_path)
    assert basis.refs["side"]["fit"]["camera_ambiguity"] == evidence
    prefix = basis.dir.relative_to(basis.job_root).as_posix()
    result = Seeing._fit_ref_summary(basis.refs["side"], usable=True, basis_path=prefix)
    assert result["camera_ambiguity"]["status"] == "unresolved"
    assert result["camera_ambiguity"]["selection_changed"] is False
    assert result["candidate_selection"] == basis.refs["side"]["fit"]["candidate_selection"]
    assert result["candidate_selection"]["changed"] is True
    for candidate, expected in zip(result["camera_ambiguity"]["candidates"], evidence["candidates"]):
        assert candidate["camera"] == expected["camera"]
        assert C.sha256_file(basis.job_root / candidate["overlay"]) == expected["overlay_sha256"]
    assert basis.camera("side").to_dict()["yaw"] == CAMERA["yaw"]


def test_partial_refit_preserves_alternative_overlay_bytes(tmp_path):
    basis, photos, evidence = prepared(tmp_path)
    dest = C.basis_root(basis.job_root) / "basis-02"
    dest.mkdir()
    C.write_json(dest / C.CAMERAS_JSON, {"model": MODEL, "images": {}})
    C.complete_refit(dest, photos, basis)
    second = C.freeze_basis(basis.job_root, dest, photos, reason="retain", call_id="t", source="test")
    assert second.refs["side"]["fit"]["camera_ambiguity"] == evidence
    for row in evidence["candidates"]:
        assert (dest / row["overlay"]).read_bytes() == (basis.dir / row["overlay"]).read_bytes()


def failed_refit_collision(tmp_path, *, escaped=False):
    folder, photos, rows = source(tmp_path, {"side.png": {}})
    old_evidence = orientation(folder)
    old_main = folder / "overlay_side.png"
    Image.new("RGB", (80, 60), "orange").save(old_main)
    rows["side.png"].update(overlay=old_main.name, camera_ambiguity=old_evidence)
    C.write_json(folder / C.CAMERAS_JSON, {"model": MODEL, "images": rows})
    prior = C.import_cameras_json(tmp_path / "job", folder / C.CAMERAS_JSON, photos, reason="prior")
    dest = C.basis_root(prior.job_root) / "basis-02"
    dest.mkdir()
    attempt = deepcopy(rows["side.png"])
    attempt.update(status="unusable", refusal="new overlap unusable", camera={**CAMERA, "yaw": 60})
    failed_main = dest / "overlay_side.png"
    Image.new("RGB", (80, 60), "black").save(failed_main)
    attempt["overlay"] = str(failed_main.resolve())  # real fit records carry the absolute main path
    expected = [failed_main.read_bytes()]
    for index, candidate in enumerate(attempt["camera_ambiguity"]["candidates"]):
        path = dest / candidate["overlay"]
        Image.new("RGB", (80, 60), "red" if index == 0 else "blue").save(path)
        candidate["overlay_sha256"] = C.sha256_file(path)
        expected.append(path.read_bytes())
    if escaped:
        attempt["overlay"] = str(old_main.resolve())  # an existing image outside the new basis
    C.write_json(dest / C.CAMERAS_JSON, {"model": MODEL, "images": {"side.png": attempt}})
    C.complete_refit(dest, photos, prior)
    second = C.freeze_basis(prior.job_root, dest, photos, reason="failed refit", call_id="t", source="test")
    return prior, second, expected


def test_failed_refit_preserves_distinct_main_and_alternative_attempt_pixels(tmp_path):
    prior, second, expected = failed_refit_collision(tmp_path)
    assert second.camera("side").to_dict() == prior.camera("side").to_dict()
    assert second.refs["side"]["fit"]["camera_ambiguity"] == prior.refs["side"]["fit"]["camera_ambiguity"]
    assert (second.dir / second.refs["side"]["overlay"]).read_bytes() == (
        prior.dir / prior.refs["side"]["overlay"]).read_bytes()
    summary = Seeing._fit_ref_summary(second.refs["side"], usable=True,
        basis_path=second.dir.relative_to(second.job_root).as_posix())
    attempt = summary["retained_fit"]["attempt"]
    assert attempt["status"] == "unusable" and attempt["camera"]["yaw"] == 60
    for record, pixels in zip([attempt, *attempt["camera_ambiguity"]["candidates"]], expected):
        path = _read_image_path(second.job_root, record["overlay"])
        assert path.name.startswith("overlay_attempt_") and path.parent == second.dir
        assert path.read_bytes() == pixels
        assert C.sha256_file(path) == record["overlay_sha256"]
    assert C.load_basis(second.job_root).id == second.id


@pytest.mark.parametrize("target", ["main", "alternative", "metadata"])
def test_retained_failed_attempt_damage_is_not_accepted_as_frozen_evidence(tmp_path, target):
    _, second, _ = failed_refit_collision(tmp_path)
    attempt = second.refs["side"]["retained_fit"]["attempt"]
    if target == "metadata":
        meta = deepcopy(second.meta)
        del meta["refs"]["side"]["retained_fit"]
        C.write_json(second.dir / "basis.json", meta)
    else:
        record = attempt if target == "main" else attempt["camera_ambiguity"]["candidates"][0]
        (second.dir / record["overlay"]).write_bytes(b"changed")
    with pytest.raises(ValueError, match="retained (fit evidence|attempt overlay)"):
        C.load_basis(second.job_root)


def test_failed_attempt_overlay_must_stay_inside_its_new_basis(tmp_path):
    with pytest.raises(ValueError, match="attempted overlay escapes"):
        failed_refit_collision(tmp_path, escaped=True)


def test_damaged_orientation_overlay_is_not_frozen_evidence(tmp_path):
    basis, _, evidence = prepared(tmp_path)
    (basis.dir / evidence["candidates"][1]["overlay"]).write_bytes(b"changed")
    with pytest.raises(ValueError, match="orientation overlay differs"):
        C.load_basis(basis.job_root)


def test_omitted_alternatives_do_not_silently_certify_one_orientation(tmp_path):
    basis, _, _ = prepared(tmp_path)
    meta = deepcopy(basis.meta)
    del meta["refs"]["side"]["fit"]["camera_ambiguity"]
    C.write_json(basis.dir / "basis.json", meta)
    with pytest.raises(ValueError, match="orientation alternatives differ"):
        C.load_basis(basis.job_root)


def test_orientation_warning_survives_large_report_and_full_candidates_are_pageable(tmp_path):
    evidence = orientation(tmp_path)
    warning = {"unresolved_refs": ["side"], "unavailable_refs": ["top"], "note": evidence["note"]}
    original = {"status": "ok", "camera_orientation": warning,
                "refs": {"side": {"camera_ambiguity": evidence}}, "large": "x" * 100000}
    output = tool_reports.limit_output(tmp_path, "fit1", "fit_cameras",
                                       [ToolOutputText(text=json.dumps(original))])
    body, _ = tool_reports._body(output[0].text)
    assert body["camera_orientation"] == warning
    report = body["full_report"]["id"]
    page = tool_reports.read_report(tmp_path, report, "/result/refs/side/camera_ambiguity/candidates")
    assert [entry["value"] for entry in page["entries"]] == evidence["candidates"]


def test_omitted_candidate_selection_does_not_hide_label_based_choice(tmp_path):
    basis, _, _ = prepared(tmp_path)
    meta = deepcopy(basis.meta)
    del meta["refs"]["side"]["fit"]["candidate_selection"]
    C.write_json(basis.dir / "basis.json", meta)
    with pytest.raises(ValueError, match="candidate selection differs"):
        C.load_basis(basis.job_root)
