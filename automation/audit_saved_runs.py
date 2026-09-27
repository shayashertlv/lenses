"""Read-only inventory of saved Modeling Auto jobs; never calls a provider.

Counts describe saved workflow state, not reconstruction success or cross-product
quality. Only job.json and its five normalized reference JPEGs are read.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re

ANGLES = ("front", "back", "left", "right", "angled")
MAX_REFERENCE_BYTES = 24 * 1024 * 1024
VERDICT_KEYS = ("lenses_present", "seam_tooth", "seam_gap",
                "frame_faces_through_lens", "see_through", "all")
DIMENSIONS = ("frame_width", "lens_width", "lens_height", "bridge_width", "temple_length")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def lens_evidence(current, layout):
    inspection = current.get("inspection") or {}
    quality = inspection.get("lens_quality")
    quality = quality if isinstance(quality, dict) else {}
    verdict = quality.get("verdict") or {}
    verdict = verdict if isinstance(verdict, dict) else {}
    recorded = {key: verdict[key] for key in VERDICT_KEYS if type(verdict.get(key)) is bool}
    reported = {True: "pass", False: "fail"}.get(recorded.get("all"), "unknown")
    seams = quality.get("seam")
    entries, reasons = [], []
    if not isinstance(seams, dict) or not seams:
        reasons.append("No recorded seam measurements.")
        seams = {}
    expected = {"pair": 2, "single": 1, "shield": 1}.get(layout)
    if expected is None or len(seams) != expected:
        reasons.append("Recorded seam count does not establish coverage for the lens layout.")
    for name, seam in sorted(seams.items()):
        seam = seam if isinstance(seam, dict) else {}
        fields = ("loops", "outline_vertices", "tooth_mm", "gap_mm", "gap_vertices",
                  "tucked_vertices", "free_vertices")
        entry = {key: seam[key] for key in fields if number(seam.get(key))}
        observed = all(number(seam.get(key)) and seam[key] > 0 for key in ("loops", "outline_vertices"))
        entry.update(lens=name, boundary_observed=observed)
        entries.append(entry)
        if not observed:
            reasons.append(f"{name}: no positive loop and outline-vertex measurement.")
    coverage = "observed" if not reasons else "unknown" if not seams else "incomplete"
    return {"reported_status": reported, "reported_verdict": recorded,
            "boundary_coverage": coverage, "coverage_reasons": reasons, "seams": entries,
            "eligible_reported_pass": reported == "pass" and coverage == "observed"
            and all(recorded.get(key) is True for key in VERDICT_KEYS)}


def reference_evidence(job, folder):
    """Verify small normalized images, never arbitrary recorded artifact paths."""
    references, artifacts = job.get("references"), job.get("artifacts")
    references = references if isinstance(references, list) else []
    artifacts = artifacts if isinstance(artifacts, dict) else {}
    hashes, issues = {}, []
    for angle in ANGLES:
        refs = [ref for ref in references if isinstance(ref, dict) and ref.get("angle") == angle]
        if len(refs) != 1:
            issues.append({"angle": angle, "reason": "missing_or_duplicate_reference"})
            continue
        url = refs[0].get("url")
        artifact = artifacts.get(url.rsplit("/", 1)[-1], {}) if isinstance(url, str) else {}
        digest = artifact.get("sha256") if isinstance(artifact, dict) else None
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            issues.append({"angle": angle, "reason": "missing_recorded_sha256"})
            continue
        path = folder / "references" / f"{angle}.jpg"
        try:
            if not path.resolve().is_relative_to(folder.resolve()):
                raise ValueError("external_reference")
            if not 0 < path.stat().st_size <= MAX_REFERENCE_BYTES:
                raise ValueError("reference_outside_size_limit")
            actual = sha256(path.read_bytes())
        except (OSError, ValueError) as error:
            reason = str(error) if isinstance(error, ValueError) else "reference_unreadable"
            issues.append({"angle": angle, "reason": reason})
            continue
        if actual != digest.lower():
            issues.append({"angle": angle, "reason": "sha256_mismatch"})
        else:
            hashes[angle] = actual
    complete = len(hashes) == len(ANGLES) and not issues
    identity = sha256(json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()) if complete else None
    return {"status": "verified" if complete else "rejected", "issues": issues,
            "verified_normalized_sha256": hashes, "reference_set_sha256": identity}


def summarize_job(job, source, source_hash):
    if not isinstance(job, dict) or any(not isinstance(job.get(key), str)
                                       for key in ("id", "name", "status", "stage")):
        raise ValueError("invalid_job_identity_or_state")
    current = job.get("current")
    if current is not None and (not isinstance(current, dict)
                               or not isinstance(current.get("inspection", {}), dict)):
        raise ValueError("invalid_current_revision")
    layout = job.get("lens_layout", "pair")
    dimensions = job.get("dimensions") or {}
    dimensions = dimensions if isinstance(dimensions, dict) else {}
    return {"id": job["id"], "name": job["name"], "status": job["status"], "stage": job["stage"],
            "accepted": isinstance(job.get("accepted"), dict) and bool(job["accepted"]),
            "lens_layout": layout if isinstance(layout, str) else "unknown",
            "dimensions_mm": {key: dimensions[key] for key in DIMENSIONS if number(dimensions.get(key))},
            "source": {"file": source.as_posix(), "sha256": source_hash},
            "current_revision": {"id": current.get("id"), "stage": current.get("stage")}
            if current else None,
            "lens_quality": lens_evidence(current or {}, layout if isinstance(layout, str) else None)}


def collect(jobs_dir):
    jobs_dir = Path(jobs_dir).resolve(strict=True)
    if not jobs_dir.is_dir():
        raise ValueError("jobs_dir must be a directory")
    jobs, malformed = [], []
    # Deliberately one level only: no provider receipts, models, or recursive search.
    for path in sorted(jobs_dir.glob("*/job.json")):
        relative = path.relative_to(jobs_dir)
        source_hash = None
        try:
            if not path.resolve().is_relative_to(jobs_dir):
                raise ValueError("external_job_file")
            raw = path.read_bytes()
            source_hash = sha256(raw)
            job = json.loads(raw)
            record = summarize_job(job, relative, source_hash)
            record["references"] = reference_evidence(job, path.parent)
            jobs.append(record)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError, TypeError) as error:
            reason = str(error) if type(error) is ValueError else type(error).__name__
            malformed.append({"file": relative.as_posix(), "sha256": source_hash, "reason": reason})
    names, references = defaultdict(list), defaultdict(list)
    for job in jobs:
        names[" ".join(job["name"].casefold().split())].append(job["id"])
        fingerprint = job["references"]["reference_set_sha256"]
        if fingerprint:
            references[fingerprint].append(job["id"])
    return {"schema_version": 1, "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "collector_sha256": sha256(Path(__file__).read_bytes()), "jobs_dir": str(jobs_dir),
            "interpretation": ["Workflow counts are not success rates or cross-product quality evidence.",
                               "Acceptance is recorded owner action, not independently verified visual fidelity.",
                               "Lens verdicts are historical heuristic reports, not remeasured geometry.",
                               "Positive boundary coverage only qualifies a reported pass; it does not prove fit.",
                               "Missing measurements mean unknown evidence, not poor visual quality.",
                               "Reference identity requires all five labeled normalized JPEG hashes to verify."],
            "counts": {"job_files": len(jobs) + len(malformed), "readable_jobs": len(jobs),
                       "malformed_jobs": len(malformed), "accepted_jobs": sum(job["accepted"] for job in jobs),
                       "verified_reference_jobs": sum(len(ids) for ids in references.values()),
                       "distinct_verified_reference_sets": len(references),
                       "status": dict(sorted(Counter(job["status"] for job in jobs).items())),
                       "stage": dict(sorted(Counter(job["stage"] for job in jobs).items())),
                       "reported_lens_quality": dict(sorted(Counter(job["lens_quality"]["reported_status"] for job in jobs).items())),
                       "eligible_reported_passes": sum(job["lens_quality"]["eligible_reported_pass"] for job in jobs)},
            "product_name_groups": [{"normalized_name": name, "job_ids": ids} for name, ids in sorted(names.items())],
            "duplicate_reference_set_groups": [{"reference_set_sha256": key, "job_ids": ids}
                                               for key, ids in sorted(references.items()) if len(ids) > 1],
            "jobs": jobs, "malformed_jobs": malformed}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(args.jobs_dir.resolve()):
        parser.error("Output must be outside the read-only jobs directory.")
    report = collect(args.jobs_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps(report["counts"], sort_keys=True))


if __name__ == "__main__":
    main()
