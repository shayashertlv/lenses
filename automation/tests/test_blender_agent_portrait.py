import copy
import hashlib

from blender_agent.portrait import EXPECTED_CAPTURES, portrait_diagnostics, validate_portrait_report


def valid_report(tmp_path):
    captures = []
    for label in EXPECTED_CAPTURES:
        path = tmp_path / (label + ".png")
        path.write_bytes(label.encode())
        captures.append({"label": label, "path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                         "native_pixels": True, "resized": False})
    return {"status": "inspected", "source_snapshot_stable": True, "input_files_stable": True,
            "asset_integrity": {"sha256": "a" * 64, "pinned": True}, "model_sha256": "a" * 64,
            "fixture": {"sha256": "b" * 64}, "fitting": {"ready": True}, "optical_mesh_count": 2,
            "continuity_failure": None, "captures": captures}


def test_portrait_evidence_requires_complete_hash_pinned_native_captures(tmp_path):
    report = valid_report(tmp_path)
    assert validate_portrait_report(report, tmp_path, "a" * 64, "b" * 64)["ok"]
    for change in [{"status": "failed"}, {"input_files_stable": False}, {"source_snapshot_stable": False},
                   {"asset_integrity": {"sha256": "c" * 64, "pinned": True}}, {"optical_mesh_count": 0},
                   {"captures": report["captures"][:-1]}, {"fitting": {"ready": False}}]:
        assert not validate_portrait_report({**report, **change}, tmp_path, "a" * 64, "b" * 64)["ok"]
    modified = copy.deepcopy(report)
    modified["captures"][0]["path"] = str(tmp_path.parent / "escaped.png")
    assert not validate_portrait_report(modified, tmp_path, "a" * 64, "b" * 64)["ok"]
    report["captures"][0]["sha256"] = "0" * 64
    assert not validate_portrait_report(report, tmp_path, "a" * 64, "b" * 64)["ok"]


def test_continuity_error_does_not_misreport_unreached_fitting_as_failed_lens_geometry(tmp_path):
    report = valid_report(tmp_path)
    report.update(status="failed", continuity_failure="An original posterior arm cross-section is missing.",
                  error="page.evaluate: Optical geometry or temple continuity unavailable\nstack details")
    del report["fitting"]
    report["optical_mesh_count"] = 3
    report["captures"] = [c for c in report["captures"] if c["label"] == "clean-eye-detail"]
    diagnostics = portrait_diagnostics(report)
    assert diagnostics["optical_meshes_measured"] and diagnostics["optical_mesh_count"] == 3
    assert diagnostics["continuity_measured"] is True
    assert diagnostics["continuity_failure"] == report["continuity_failure"]
    assert diagnostics["fitting_measured"] is False and diagnostics["fitting"] is None
    assert diagnostics["error"] == report["error"]
    reasons = validate_portrait_report(report, tmp_path, "a" * 64, "b" * 64)["reasons"]
    assert "Fitting diagnostics unavailable" in reasons
    assert "Portrait fitting did not settle" not in reasons
    assert "No optical meshes detected" not in reasons
    assert any(report["continuity_failure"] in r for r in reasons)


def test_missing_continuity_measurement_is_not_a_null_pass(tmp_path):
    report = valid_report(tmp_path)
    del report["continuity_failure"]
    assert portrait_diagnostics(report)["continuity_measured"] is False
    assert "Temple continuity was not measured" in validate_portrait_report(report, tmp_path, "a" * 64, "b" * 64)["reasons"]
