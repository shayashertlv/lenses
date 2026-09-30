"""Local exact-GLB portrait evidence. No model API, live Blender access or renderer edits."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil

from bsa import archeck
from bsa.core import AUTOMATION

AR_ROOT = AUTOMATION.parent / "ar"
FIXTURE = AR_ROOT / "qa" / "fixtures" / "face-a.jpg"
EXPECTED_CAPTURES = {
    "clean-eye-detail", "condition-a-portrait", "condition-a-eye-detail",
    "condition-b-portrait", "condition-b-eye-detail",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def portrait_diagnostics(report: dict) -> dict:
    """Keep failure causes distinct from downstream measurements never reached."""
    error = str(report["error"]) if report.get("error") is not None else None
    return {"status": report.get("status"), "error": error[:2000] if error else error,
            "error_truncated": bool(error and len(error) > 2000),
            "optical_mesh_count": report.get("optical_mesh_count"),
            "optical_meshes_measured": "optical_mesh_count" in report,
            "continuity_measured": "continuity_failure" in report,
            "continuity_failure": report.get("continuity_failure"),
            "fitting_measured": "fitting" in report, "fitting": report.get("fitting"),
            "source_snapshot_stable": report.get("source_snapshot_stable"),
            "input_files_stable": report.get("input_files_stable"),
            "harness_errors": {key: {"count": len(report.get(key) or []),
                                      "first": str(report[key][0])[:1000] if report.get(key) else None}
                               for key in ("errors", "failed_responses", "blocked_external")}}


def validate_portrait_report(report: dict, output: Path, expected_sha: str, fixture_sha: str) -> dict:
    """Fail closed on unpinned inputs, failed runtime, missing images or stale output."""
    reasons = []
    if report.get("status") != "inspected":
        reasons.append("Portrait renderer did not complete")
    if report.get("source_snapshot_stable") is not True or report.get("input_files_stable") is not True:
        reasons.append("Source or input stability was not confirmed")
    if any(report.get(key) for key in ("errors", "failed_responses", "blocked_external", "error")):
        reasons.append("Portrait harness reported an error")
    if report.get("error"):
        reasons.append("Portrait error: " + str(report["error"]).splitlines()[0][:1000])
    integrity = report.get("asset_integrity") or {}
    if report.get("model_sha256") != expected_sha or integrity.get("sha256") != expected_sha or integrity.get("pinned") is not True:
        reasons.append("Rendered GLB identity does not match the requested file")
    if (report.get("fixture") or {}).get("sha256") != fixture_sha:
        reasons.append("Portrait fixture identity does not match")
    if "optical_mesh_count" not in report:
        reasons.append("Optical mesh diagnostics unavailable")
    elif not report.get("optical_mesh_count"):
        reasons.append("No optical meshes detected")
    if "fitting" not in report:
        reasons.append("Fitting diagnostics unavailable")
    elif (report.get("fitting") or {}).get("ready") is not True:
        reasons.append("Portrait fitting did not settle")
    if "continuity_failure" not in report:
        reasons.append("Temple continuity was not measured")
    elif report["continuity_failure"] is not None:
        reasons.append("Temple continuity failed: " + str(report["continuity_failure"])[:1000])
    captures = report.get("captures") or []
    if len(captures) != len(EXPECTED_CAPTURES) or {c.get("label") for c in captures} != EXPECTED_CAPTURES:
        reasons.append("Missing or duplicate portrait captures")
    for capture in captures:
        path = Path(capture.get("path", "")).resolve()
        if not path.is_relative_to(output.resolve()) or not path.is_file() or sha256(path) != capture.get("sha256"):
            reasons.append("Portrait capture missing, outside output directory or hash-mismatched")
            break
        if capture.get("native_pixels") is not True or capture.get("resized") is not False:
            reasons.append("Portrait detail did not retain native pixels")
            break
    return {"ok": not reasons, "reasons": reasons}


def run_portrait(glb: Path, output: Path, *, timeout_s: int = 360) -> dict:
    """Capture one approved synthetic wearer at its real inferred pose in a fresh directory."""
    glb, output = Path(glb).resolve(), Path(output).resolve()
    model_sha, fixture_sha = sha256(glb), sha256(FIXTURE)
    width = min(250.0, max(60.0, archeck.front_width_mm(glb)))
    output.mkdir(parents=True, exist_ok=False)
    manifest = {"schema_version": 1, "glb": str(glb), "model_sha256": model_sha, "width_mm": width,
                "fixture": {"id": "face-a", "path": str(FIXTURE), "sha256": fixture_sha, "synthetic": True,
                            "provenance": "Checked-in AR QA fixture, visibly marked StyleGAN2 (Karras et al.). "
                                          "Approved for this local supplementary wearer inspection; no private recording.",
                            "role": "Supplementary wearer evidence, not a target product photograph"}}
    manifest_path = output / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    node = shutil.which("node")
    if not node:
        raise RuntimeError("Node.js is required for portrait AR inspection")
    command = [node, "qa/portrait-preview.mjs", f"--manifest={manifest_path}", f"--output={output}"]
    code, stdout, stderr = archeck.run_harness_command(command, cwd=AR_ROOT, timeout_s=timeout_s)
    (output / "harness-stdout.txt").write_text(stdout or "", encoding="utf-8")
    (output / "harness-stderr.txt").write_text(stderr or "", encoding="utf-8")
    report_path = output / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else {}
    validation = validate_portrait_report(report, output, model_sha, fixture_sha)
    try:
        input_files_stable = sha256(glb) == model_sha and sha256(FIXTURE) == fixture_sha
    except OSError:
        input_files_stable = False
    if not input_files_stable:
        validation["ok"] = False
        validation["reasons"].append("Source model or portrait fixture changed during capture")
    if code != 0:
        validation["ok"] = False
        validation["reasons"].append("Portrait harness returned a failure")
    result = {"glb": str(glb), "sha256": model_sha, "directory": str(output), "report_path": str(report_path),
              "validation": validation, "returncode": code, "comparison_key": report.get("comparison_key"),
              "input_files_stable": input_files_stable,
              "diagnostics": portrait_diagnostics(report),
              "captures": report.get("captures", []), "fixture": manifest["fixture"],
              "omissions": ["No invented oblique wearer pose; only the source portrait's detected pose is available"],
              "command": command}
    (output / "portrait-result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--glb", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run_portrait(args.glb, args.output)
    print(json.dumps({"validation": result["validation"], "report_path": result["report_path"],
                      "sha256": result["sha256"], "comparison_key": result["comparison_key"]}, indent=2))
    if not result["validation"]["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
