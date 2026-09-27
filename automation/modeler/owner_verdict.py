"""Record the owner's verdict on an asset seen in the live try-on and apply it to the job.

    python -m modeler.owner_verdict --job data/modeler/jobs/<name> --verdict accept|borderline|reject \
        --medium "live AR try-on, Chrome, own camera" [--note "..."] [--sha256 <digest of the asset judged>]
    python -m modeler.owner_verdict --job data/modeler/jobs/<name> --baseline bsa-m3 --verdict reject --medium ... [--note ...]

The owner's judgement of an asset in the live try-on is the visual bar this experiment lacked. This records it
against the exact asset (sha256 checked against the manifest for a delivered asset; computed from the file for a
baseline), keeps the independent evaluator's verdict untouched beside it, and appends one row to the calibration set
``data/modeler/calibration/owner_verdicts.jsonl`` (owner verdict, evaluator verdict, provisional thresholds, metrics,
runtime flags of the same asset). A future automatic bar must reproduce those rows before any job may award
``accepted`` on its own (``modeler.calibration``).

Status rule (delivered assets only): an owner ``accept`` on a valid delivered asset upgrades the job to ``accepted``
with ``status_detail.accepted_by = "owner"``; the previous status and its reasons stay recorded. ``borderline``
(acceptable but visibly worse than a reference the owner named) and ``reject`` leave the status and record the
verdict. Baseline verdicts never touch the job status. ``visual_bar_calibrated`` stays false here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from .paths import AUTOMATION, MODELER_DATA

CALIBRATION_FILE = MODELER_DATA / "calibration" / "owner_verdicts.jsonl"
VERDICTS = ("accept", "borderline", "reject")
METRIC_KEYS = ("front_contour_mean_mm", "lens_outline_mean_mm", "back_contour_mean_mm", "side_contour_mean_mm",
               "mean_contour_mm_all_fit_views")


def _load(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _append(calibration_file: Path, record: dict) -> None:
    calibration_file.parent.mkdir(parents=True, exist_ok=True)
    with calibration_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def apply_verdict(job_dir: Path, verdict: str, medium: str, note: str = "", sha256: str | None = None,
                  when: str | None = None, calibration_file: Path = CALIBRATION_FILE, write_report: bool = True) -> dict:
    """Owner verdict on the job's DELIVERED asset."""
    job_dir = Path(job_dir)
    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {VERDICTS}")
    mp = job_dir / "manifest.json"
    m = _load(mp)
    asset = m.get("asset")
    if not asset:
        raise ValueError("the job delivered no asset; an owner verdict needs one")
    if sha256 and sha256.lower() != asset["sha256"].lower():
        raise ValueError("the digest judged does not match the delivered asset in the manifest")
    when = when or _now()
    meas = (m.get("measurements") or {})
    av, ho = meas.get("author_visible") or {}, meas.get("held_out") or {}
    ev = m.get("evaluation") or {}
    sd = m.get("status_detail") or {}
    record = {
        "kind": "delivered", "when": when, "verdict": verdict, "medium": medium, "note": note,
        "job": m["job"], "product_id": m["product_id"], "candidate": m.get("delivered_candidate"),
        "asset_path": asset["path"], "asset_sha256": asset["sha256"],
        "status_before": m["status"],
        "evaluator": {"overall": ev.get("overall"), "resemblance_0_10": ev.get("resemblance_0_10"),
                      "major_discrepancies": sum(1 for d in ev.get("discrepancies", []) if d.get("severity") == "major"),
                      "absent_features": sum(1 for c in ev.get("identity_checklist", []) if c.get("verdict") == "absent")},
        "provisional": {k: v.get("pass") for k, v in (sd.get("provisional") or {}).items()},
        "metrics_mm": {k: av.get(k) for k in METRIC_KEYS},
        "heldout_mean_mm": ho.get("mean_contour_mm_all_fit_views"),
        "runtime": {"ar_runtime_compatible": av.get("ar_runtime_compatible"), "ar_optical_meshes": av.get("ar_optical_meshes"),
                    "ar_continuity_failure": av.get("ar_continuity_failure")},
        "scale": m.get("scale"),
    }
    (job_dir / "owner_verdict.json").write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")

    m["owner_verdict"] = {k: record[k] for k in ("when", "verdict", "medium", "note", "asset_sha256", "status_before")}
    sd = dict(sd)
    if verdict == "accept":
        sd["previous_status"] = m["status"]
        sd["previous_reasons"] = list(sd.get("reasons") or [])
        sd["accepted_by"] = "owner"
        sd["reasons"] = [f"owner accepted the delivered asset ({asset['sha256'][:12]}…) in the {medium} on {when[:10]}"
                         + (f": {note}" if note else "")]
        if ev.get("overall") == "reject":
            sd["reasons"].append("the independent evaluator's 'reject' stays recorded and now counts as a false reject for calibration")
        sd["status"] = "accepted"
        m["status"] = "accepted"
    else:
        sd["owner_rejected" if verdict == "reject" else "owner_borderline"] = True
        sd["reasons"] = list(sd.get("reasons") or []) + [f"owner verdict '{verdict}' on the delivered asset in the {medium} on {when[:10]}"
                                                        + (f": {note}" if note else "")]
    m["status_detail"] = sd
    mp.write_text(json.dumps(m, indent=1) + "\n", encoding="utf-8")
    _append(calibration_file, record)
    if write_report:
        from .report import report
        (job_dir / "REPORT.md").write_text(report(job_dir), encoding="utf-8")
    return record


def apply_baseline_verdict(job_dir: Path, name: str, verdict: str, medium: str, note: str = "", when: str | None = None,
                           calibration_file: Path = CALIBRATION_FILE) -> dict:
    """Owner verdict on one of the job's same-protocol BASELINE assets (a previous route); never touches the status."""
    job_dir = Path(job_dir)
    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {VERDICTS}")
    bdir = job_dir / "baselines" / name
    b = _load(bdir / "baseline.json")
    m = _load(job_dir / "manifest.json")
    glb = Path(b["glb"])
    glb = glb if glb.is_absolute() else AUTOMATION / glb
    if not glb.is_file():
        raise FileNotFoundError(glb)
    s, h = b.get("summary") or {}, b.get("held_out") or {}
    when = when or _now()
    record = {
        "kind": "baseline", "when": when, "verdict": verdict, "medium": medium, "note": note,
        "job": m["job"], "product_id": m["product_id"], "baseline": name,
        "asset_path": str(b["glb"]), "asset_sha256": hashlib.sha256(glb.read_bytes()).hexdigest(),
        "evaluator": {"overall": None},
        "metrics_mm": {k: s.get(k) for k in METRIC_KEYS},
        "heldout_mean_mm": h.get("mean_contour_mm_all_fit_views"),
        "runtime": {"ar_runtime_compatible": s.get("ar_runtime_compatible"), "ar_optical_meshes": s.get("ar_optical_meshes"),
                    "ar_continuity_failure": s.get("ar_continuity_failure")},
        "scale": m.get("scale"),
    }
    (bdir / "owner_verdict.json").write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
    _append(calibration_file, record)
    return record


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--job", type=Path, required=True)
    ap.add_argument("--verdict", choices=VERDICTS, required=True)
    ap.add_argument("--medium", required=True, help="where the owner judged it, e.g. 'live AR try-on, Chrome, own camera'")
    ap.add_argument("--note", default="", help="the owner's words")
    ap.add_argument("--sha256", help="digest of the delivered asset the owner judged (checked against the manifest)")
    ap.add_argument("--baseline", help="judge this baseline of the job instead of the delivered asset")
    args = ap.parse_args(argv)
    if args.baseline:
        rec = apply_baseline_verdict(args.job, args.baseline, args.verdict, args.medium, args.note)
        print(json.dumps({k: rec[k] for k in ("job", "baseline", "verdict", "asset_sha256")}, indent=1))
    else:
        rec = apply_verdict(args.job, args.verdict, args.medium, args.note, args.sha256)
        print(json.dumps({k: rec[k] for k in ("job", "candidate", "verdict", "status_before", "asset_sha256")}, indent=1))
        print("status now:", _load(args.job / "manifest.json")["status"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
