"""Record the owner's verdict on an asset seen in the live try-on and apply it to the job.

    python -m modeler.owner_verdict --job data/modeler/jobs/<name> --verdict accept|borderline|reject \
        --medium "live AR try-on, Chrome, own camera" [--note "..."] [--sha256 <digest of the asset judged>]
    python -m modeler.owner_verdict --job data/modeler/jobs/<name> --baseline bsa-m3 --verdict reject --medium ... [--note ...]

The owner's judgement of an asset in the live try-on is the visual bar this experiment lacked. This records it
against the exact asset: the delivered file is rehashed and must equal the manifest's digest (and ``--sha256`` when
given; until 2026-09-27 the manifest's claim was trusted), a baseline's digest is computed from its file. It keeps
the independent evaluator's verdict untouched beside it, and appends one row to the calibration set
``data/modeler/calibration/owner_verdicts.jsonl`` (owner verdict, evaluator verdict, provisional thresholds, metrics,
runtime flags of the same asset). A future automatic bar must reproduce those rows before any job may award
``accepted`` on its own (``modeler.calibration``).

Status rule (delivered assets only, ``apply_to_manifest``): an owner ``accept`` on a valid delivered asset upgrades
the job to ``accepted`` with ``status_detail.accepted_by = "owner"``; the previous status and its reasons stay
recorded. ``borderline`` (acceptable but visibly worse than a reference the owner named) and ``reject`` leave the
status and record the verdict, except after an owner accept, which they REVOKE: the status returns to the rule's own
(``status_detail.owner_revoked`` records when and by which verdict). ``manifest.owner_verdict`` is the latest verdict,
``manifest.owner_verdict_history`` every applied one (append-only); ``status_detail.axes`` reports runtime
compatibility, the automatic visual verdict and the owner's verdict as separate facts. Baseline verdicts never touch
the job status. ``visual_bar_calibrated`` stays false here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from bsa.core import sha256_file

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


def resolve_asset_path(job_dir: Path, asset: dict) -> Path:
    """The delivered file: the manifest's path (absolute, or relative to automation/ as a job writes it), else the same
    file name under the job's deliverable/ folder. A missing file is no asset to judge (fail closed)."""
    p = Path(asset["path"])
    candidates = [p] if p.is_absolute() else [AUTOMATION / p, Path.cwd() / p, Path(job_dir) / "deliverable" / p.name]
    for c in candidates:
        if c.is_file():
            return c
    raise FileNotFoundError(f"the delivered asset {asset['path']} is not on disk; an owner verdict needs the judged bytes")


def non_owner_status(m: dict) -> str:
    """The status without the owner's acceptance: the recorded previous status, else the rule's outcome recomputed from
    the recorded status detail (clean + calibrated bar + covered tags = accepted; else best_effort; the unreachable
    'inconsistent_inputs' rule was removed 2026-09-29, see modeler.evaluate.STATUSES)."""
    sd = m.get("status_detail") or {}
    if sd.get("previous_status"):
        return sd["previous_status"]
    if sd.get("clean") and m.get("visual_bar_calibrated") and not sd.get("uncovered_tags"):
        return "accepted"
    return "best_effort"


def status_axes(m: dict, owner_verdict: str | None) -> dict:
    """Runtime compatibility, the automatic visual verdict and the owner's verdict are separate facts about one asset;
    the status combines them, the axes keep them apart."""
    sd = m.get("status_detail") or {}
    av = (m.get("measurements") or {}).get("author_visible") or {}
    return {"runtime_compatible": av.get("ar_runtime_compatible"), "automatic_verdict": sd.get("automatic_verdict"),
            "owner_verdict": owner_verdict}


def apply_to_manifest(m: dict, ov: dict, *, record_history: bool = True) -> dict:
    """The status rule for one owner verdict ``ov`` ({when, verdict, medium, note, asset_sha256, status_before}) on the
    manifest ``m`` (mutated and returned): an accept upgrades to ``accepted`` (accepted_by owner); a reject or
    borderline after an owner accept revokes it (status back to the pre-owner status, ``owner_revoked`` recorded).
    ``owner_verdict`` is the latest verdict; ``owner_verdict_history`` gets every applied one (a re-finalize that keeps
    a verdict passes ``record_history=False``: the history travels with it, nothing is appended twice)."""
    verdict, when, medium, note = ov["verdict"], str(ov.get("when") or ""), ov.get("medium"), ov.get("note") or ""
    entry = {k: ov.get(k) for k in ("when", "verdict", "medium", "note", "asset_sha256", "status_before")}
    m["owner_verdict"] = entry
    if record_history:
        m["owner_verdict_history"] = list(m.get("owner_verdict_history") or []) + [entry]
    sd = dict(m.get("status_detail") or {})
    ev = m.get("evaluation") or {}
    if verdict == "accept":
        if sd.get("accepted_by") != "owner":      # a second accept keeps the pre-owner status, not 'accepted'
            sd["previous_status"] = m["status"]
            sd["previous_reasons"] = list(sd.get("reasons") or [])
        sd["accepted_by"] = "owner"
        sd["reasons"] = [f"owner accepted the delivered asset ({str(ov['asset_sha256'])[:12]}…) in the {medium} on {when[:10]}"
                         + (f": {note}" if note else "")]
        if ev.get("overall") == "reject":
            sd["reasons"].append("the independent evaluator's 'reject' stays recorded and now counts as a false reject for calibration")
        for k in ("owner_rejected", "owner_borderline", "owner_revoked"):
            sd.pop(k, None)
        sd["status"] = "accepted"
        m["status"] = "accepted"
    else:
        if sd.get("accepted_by") == "owner":
            # a later reject or borderline withdraws the acceptance: the status is the rule's own again
            m["status"] = non_owner_status(m)
            sd["status"] = m["status"]
            sd.pop("accepted_by", None)
            sd["reasons"] = list(sd.get("previous_reasons") or [])
            sd["owner_revoked"] = {"when": when, "verdict": verdict}
        sd["owner_rejected" if verdict == "reject" else "owner_borderline"] = True
        sd["reasons"] = list(sd.get("reasons") or []) + [f"owner verdict '{verdict}' on the delivered asset in the {medium} on {when[:10]}"
                                                        + (f": {note}" if note else "")]
    sd["axes"] = status_axes(m, verdict)
    m["status_detail"] = sd
    return m


def apply_verdict(job_dir: Path, verdict: str, medium: str, note: str = "", sha256: str | None = None,
                  when: str | None = None, calibration_file: Path = CALIBRATION_FILE, write_report: bool = True) -> dict:
    """Owner verdict on the job's DELIVERED asset, bound to the bytes on disk (rehashed here)."""
    job_dir = Path(job_dir)
    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {VERDICTS}")
    mp = job_dir / "manifest.json"
    m = _load(mp)
    asset = m.get("asset")
    if not asset:
        raise ValueError("the job delivered no asset; an owner verdict needs one")
    actual = sha256_file(resolve_asset_path(job_dir, asset))
    if actual != str(asset.get("sha256") or "").lower():
        raise ValueError(f"the deliverable changed: the file hashes {actual[:12]}…, the manifest recorded "
                         f"{str(asset.get('sha256'))[:12]}…; no verdict recorded against unknown bytes")
    if sha256 and sha256.lower() != actual:
        raise ValueError("the digest judged does not match the delivered asset in the manifest")
    when = when or _now()
    meas = (m.get("measurements") or {})
    av, ho = meas.get("author_visible") or {}, meas.get("held_out") or {}
    ev = m.get("evaluation") or {}
    sd = m.get("status_detail") or {}
    record = {
        "kind": "delivered", "when": when, "verdict": verdict, "medium": medium, "note": note,
        "job": m["job"], "product_id": m["product_id"], "candidate": m.get("delivered_candidate"),
        "asset_path": asset["path"], "asset_sha256": actual,
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
        "tags": list(m.get("tags") or _derived_tags(job_dir, "candidates", m.get("delivered_candidate"))),
    }
    (job_dir / "owner_verdict.json").write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")

    apply_to_manifest(m, record)
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
        "tags": _derived_tags(job_dir, "baselines", name),
    }
    (bdir / "owner_verdict.json").write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
    _append(calibration_file, record)
    return record


REVIEW_KIND = "agentic_review"
REVIEW_VERDICTS = ("accept", "changes_requested", "stop")


def review_record(*, verdict: str, job_dir: Path, product_id: str, revision: str, asset_path: str | None, asset_sha256: str, round_no: int,
                  medium: str, note: str = "", text: str | None = None, summary: dict | None = None, related: dict | None = None,
                  runtime_limited: list | None = None, authorized_by: str | None = None, when: str | None = None) -> dict:
    """One owner decision of the agentic owner review loop (python -m modeler.agentic owner-review) as a calibration row:
    accept, changes_requested (with the owner's words verbatim) or stop, bound to the candidate's digest, with the
    silhouette metrics, the runtime flags and a snapshot of the measurements the decision relates to (lens colour and its
    recommended transmission, the appearance per material, the lens reflection, the export audit flags, the runtime-limited
    flags), so an owner verdict that disagrees with an instrument can recalibrate it later. ``candidate`` is the revision
    id; ``job`` the job folder's name (``job_dir`` the full path: agentic jobs live outside data/modeler/jobs). A
    changes_requested or stop row is not an accept or a reject: modeler.calibration counts it as neither."""
    if verdict not in REVIEW_VERDICTS:
        raise ValueError(f"an owner review decision is one of {REVIEW_VERDICTS}")
    summary = summary or {}
    return {"kind": REVIEW_KIND, "when": when or _now(), "verdict": verdict, "medium": medium, "note": note, "text": text,
            "authorized_by": authorized_by, "job": Path(job_dir).name, "job_dir": str(job_dir), "product_id": product_id, "candidate": revision,
            "round": int(round_no), "asset_path": asset_path, "asset_sha256": asset_sha256,
            "metrics_mm": {k: summary.get(k) for k in METRIC_KEYS},
            "runtime": {"ar_runtime_compatible": summary.get("ar_runtime_compatible"), "ar_optical_meshes": summary.get("ar_optical_meshes"),
                        "ar_continuity_failure": summary.get("ar_continuity_failure")},
            "related_measurements": related or {}, "runtime_limited": list(runtime_limited or []), "tags": []}


def append_review_record(record: dict, calibration_file: Path = CALIBRATION_FILE) -> dict:
    """Append one ``review_record`` row to the calibration set (append-only)."""
    if record.get("kind") != REVIEW_KIND or record.get("verdict") not in REVIEW_VERDICTS or not record.get("asset_sha256"):
        raise ValueError("not an owner review record bound to an asset digest")
    _append(Path(calibration_file), record)
    return record


def _derived_tags(job_dir: Path, kind_dir: str, asset: str | None) -> list[str]:
    """The asset's tags (kind of glasses, translucent, mirrored) for the calibration row; [] when underivable."""
    try:
        from .tags import asset_tags, job_evidence, tags_for_asset_dir
        if not asset:
            return asset_tags(job_evidence(job_dir), None)
        return tags_for_asset_dir(Path(job_dir), Path(job_dir) / kind_dir / str(asset))
    except Exception:  # noqa: BLE001 - a verdict is recorded even when the job's evidence is unreadable
        return []


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
