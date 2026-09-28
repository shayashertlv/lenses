"""The visual bar: owner verdicts from the live try-on versus the automatic verdict of the same assets.

    python -m modeler.calibration            # table + data/modeler/calibration/calibration.json

Rows come from ``owner_verdicts.jsonl`` (``modeler.owner_verdict``). The automatic verdict of an asset is the v2
evaluator's stored evaluation (``<asset>/evaluation_v2/evaluation.json``, written by ``modeler.evaluate_asset``)
combined with the asset's own metrics and runtime flags through ``evaluate.decide_status`` — exactly what a job would
compute for it. A stored evaluation counts only when it is bound to the asset: its recorded ``asset_sha256`` equals
the digest of the asset's current GLB bytes and its ``meta.protocol`` is the current evaluator protocol; a record
without that binding (legacy), of other bytes or of another protocol makes the row unevaluated, with the reason
listed (``automatic_verdict_status``). The protocol counts as calibrated when the set holds at least ``MIN_ACCEPTS``
owner accepts and ``MIN_REJECTS`` owner rejects and the automatic verdict agrees with the owner on every one of them
(borderline rows are reported, not counted; an ``unmeasured`` automatic verdict agrees with nobody). A job reads this
at freeze time (``evaluation_protocol.json: visual_bar_calibrated``) and may award ``accepted`` on its own only when
it is true.

Coverage by kind: every row carries the asset's tags (``modeler.tags``: pair/single, rim class, translucent, mirrored);
a tag is covered when at least ``MIN_TAG_VERDICTS`` evaluated owner verdicts carry it and none disagrees. A job whose
delivered asset carries an uncovered tag is not awarded ``accepted`` on its own (``evaluate.decide_status``): the bar has
not been judged on that kind of glasses yet, whatever the overall count.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from .paths import JOBS, MODELER_DATA
from .tags import asset_glb

ROWS_FILE = MODELER_DATA / "calibration" / "owner_verdicts.jsonl"
SUMMARY_FILE = MODELER_DATA / "calibration" / "calibration.json"
EVALUATION_DIR = "evaluation_v2"
MIN_ACCEPTS = 3
MIN_REJECTS = 3
MIN_TAG_VERDICTS = 2


def _load(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def load_rows(path: Path = ROWS_FILE) -> list[dict]:
    if not Path(path).exists():
        return []
    return [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]


def latest_by_asset(rows: list[dict]) -> list[dict]:
    """One row per asset digest: the owner's latest verdict."""
    latest: dict[str, dict] = {}
    for r in rows:
        latest[r["asset_sha256"]] = r
    return list(latest.values())


def asset_dir(row: dict, jobs: Path = JOBS) -> Path:
    if row.get("kind") == "baseline":
        return jobs / row["job"] / "baselines" / row["baseline"]
    return jobs / row["job"] / "candidates" / row["candidate"]


def automatic_verdict_status(row: dict, jobs: Path = JOBS, evaluation_dir: str = EVALUATION_DIR) -> dict:
    """``{"verdict": <automatic_verdict dict> | None, "reason": None | why the asset counts as unevaluated}``. Reasons:
    ``no_evaluation``, ``no_observation``, ``legacy_unbound`` (the record carries no asset_sha256 or no meta.protocol),
    ``asset_missing``, ``asset_changed`` (the GLB's bytes are not the ones evaluated), ``protocol_mismatch``,
    ``invalid_evaluation``. A record is never upgraded: the binding must be in the record itself."""
    from .evaluate import PROTOCOL, decide_status, validate_evaluation
    d = asset_dir(row, jobs)
    ev_path = d / evaluation_dir / "evaluation.json"
    obs_path = d / "observe" / "observation.json"
    if not ev_path.exists():
        return {"verdict": None, "reason": "no_evaluation"}
    if not obs_path.exists():
        return {"verdict": None, "reason": "no_observation"}
    rec = _load(ev_path)
    recorded_sha, recorded_protocol = rec.get("asset_sha256"), (rec.get("meta") or {}).get("protocol")
    if not recorded_sha or not recorded_protocol:
        return {"verdict": None, "reason": "legacy_unbound"}
    glb = asset_glb(d)
    if glb is None or not glb.is_file():
        return {"verdict": None, "reason": "asset_missing"}
    if hashlib.sha256(glb.read_bytes()).hexdigest() != str(recorded_sha).lower():
        return {"verdict": None, "reason": "asset_changed"}
    if recorded_protocol != PROTOCOL:
        return {"verdict": None, "reason": "protocol_mismatch"}
    try:
        ev = validate_evaluation(rec.get("evaluation"))
    except ValueError:
        return {"verdict": None, "reason": "invalid_evaluation"}
    obs = _load(obs_path)
    held = _load(d / "heldout" / "heldout.json") if (d / "heldout" / "heldout.json").exists() else None
    summary = obs.get("summary") or {}
    # the job's own frozen gate modes (the intake review may have made the lens gate report-only): the calibration must
    # judge an asset by the same rule its job used, or a job's 'accept' becomes a calibration 'reject' of the same asset
    proto_path = Path(jobs) / row["job"] / "evaluation_protocol.json"
    overrides = (_load(proto_path).get("gate_overrides") if proto_path.exists() else None) or None
    st = decide_status(candidate_valid=bool(summary.get("ar_runtime_compatible")), metrics=summary, heldout=held, evaluation=ev,
                       input_flags=[], protocol_calibrated=False, gate_overrides=overrides)
    verdict = {"verdict": st.get("automatic_verdict", "reject"), "evaluator_overall": ev.get("overall"),
               "major_discrepancies": st.get("major_discrepancies"), "absent_features": st.get("absent_features"),
               "gates": {k: v.get("pass") for k, v in (st.get("provisional") or {}).items() if v.get("gate")},
               "resemblance_0_10": ev.get("resemblance_0_10"), "evaluation_path": str(ev_path),
               "asset_sha256": str(recorded_sha).lower(), "protocol": recorded_protocol}
    return {"verdict": verdict, "reason": None}


def automatic_verdict(row: dict, jobs: Path = JOBS, evaluation_dir: str = EVALUATION_DIR) -> dict | None:
    """What a job would decide for this asset with the current rule and a BOUND v2 evaluation; None without one
    (``automatic_verdict_status`` says why)."""
    return automatic_verdict_status(row, jobs, evaluation_dir)["verdict"]


def agreement(rows: list[dict] | None = None, jobs: Path = JOBS, evaluation_dir: str = EVALUATION_DIR) -> list[dict]:
    out = []
    for r in latest_by_asset(rows if rows is not None else load_rows()):
        status = automatic_verdict_status(r, jobs, evaluation_dir)
        auto = status["verdict"]
        owner = r["verdict"]
        agree = None
        if auto is not None:
            agree = True if owner == "borderline" else (auto["verdict"] == owner)
        from .tags import tags_for_row
        try:
            tags = tags_for_row(r, jobs)
        except Exception:  # noqa: BLE001 - a row without a readable job still counts; it just carries no tags
            tags = list(r.get("tags") or [])
        out.append({"product_id": r["product_id"], "kind": r.get("kind", "delivered"), "job": r["job"],
                    "asset": r.get("candidate") or r.get("baseline"), "asset_sha256": r["asset_sha256"],
                    "owner": owner, "owner_note": r.get("note", ""), "automatic": auto, "automatic_reason": status["reason"],
                    "agree": agree, "tags": tags})
    return out


def coverage(table: list[dict], *, min_tag_verdicts: int = MIN_TAG_VERDICTS) -> dict:
    """Owner verdicts per asset tag; ``covered`` = enough evaluated accept/reject rows and no disagreement among them."""
    per: dict[str, dict] = {}
    for t in table:
        for tag in t.get("tags") or []:
            c = per.setdefault(tag, {"accepts": 0, "rejects": 0, "borderline": 0, "unevaluated": 0, "disagreements": 0})
            if t["owner"] == "borderline":
                c["borderline"] += 1
                continue
            if t["automatic"] is None:
                c["unevaluated"] += 1
                continue
            c["accepts" if t["owner"] == "accept" else "rejects"] += 1
            if not t["agree"]:
                c["disagreements"] += 1
    for c in per.values():
        c["covered"] = (c["accepts"] + c["rejects"]) >= min_tag_verdicts and c["disagreements"] == 0
    return dict(sorted(per.items()))


def summary(rows: list[dict] | None = None, *, min_accepts: int = MIN_ACCEPTS, min_rejects: int = MIN_REJECTS,
            jobs: Path = JOBS, evaluation_dir: str = EVALUATION_DIR) -> dict:
    table = agreement(rows, jobs, evaluation_dir)
    counted = [t for t in table if t["owner"] in ("accept", "reject")]
    evaluated = [t for t in counted if t["automatic"] is not None]
    accepts = sum(1 for t in evaluated if t["owner"] == "accept")
    rejects = sum(1 for t in evaluated if t["owner"] == "reject")
    disagreements = [t for t in evaluated if not t["agree"]]
    unevaluated = [t for t in counted if t["automatic"] is None]
    calibrated = accepts >= min_accepts and rejects >= min_rejects and not disagreements
    reasons = []
    if accepts < min_accepts:
        reasons.append(f"{accepts} evaluated owner accepts, need {min_accepts}")
    if rejects < min_rejects:
        reasons.append(f"{rejects} evaluated owner rejects, need {min_rejects}")
    if disagreements:
        reasons.append(f"{len(disagreements)} automatic verdicts disagree with the owner: "
                       + ", ".join(f"{t['job']}/{t['asset']} owner {t['owner']} vs automatic {t['automatic']['verdict']}" for t in disagreements))
    if unevaluated:
        reasons.append(f"{len(unevaluated)} owner-judged assets have no usable {evaluation_dir} evaluation: "
                       + ", ".join(f"{t['job']}/{t['asset']} ({t.get('automatic_reason') or 'unknown'})" for t in unevaluated))
    by_reason: dict[str, int] = {}
    for t in unevaluated:
        by_reason[t.get("automatic_reason") or "unknown"] = by_reason.get(t.get("automatic_reason") or "unknown", 0) + 1
    cov = coverage(table)
    return {"computed": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"), "rows_file": str(ROWS_FILE),
            "evaluation_dir": evaluation_dir, "min_accepts": min_accepts, "min_rejects": min_rejects, "min_tag_verdicts": MIN_TAG_VERDICTS,
            "owner_accepts_evaluated": accepts, "owner_rejects_evaluated": rejects,
            "borderline": sum(1 for t in table if t["owner"] == "borderline"), "unevaluated": len(unevaluated),
            "unevaluated_by_reason": dict(sorted(by_reason.items())),
            "disagreements": len(disagreements), "calibrated": calibrated, "reasons": reasons,
            "coverage": cov, "covered_tags": [t for t, c in cov.items() if c["covered"]], "table": table}


EVALUATION_DIRS = {"astra": "evaluation_v2_astra"}      # evaluator driver name -> where its answers live; others share evaluation_v2


def evaluation_dir_for(evaluator_name: str | None) -> str:
    return EVALUATION_DIRS.get(str(evaluator_name or ""), EVALUATION_DIR)


def protocol_calibration(evaluator_name: str | None = None) -> dict:
    """What a job records at freeze time (without the per-asset table), for THE evaluator it will use: an API
    evaluator's calibration is its own, not the agent evaluator's (until 2026-09-27 the flag was evaluator-agnostic)."""
    edir = evaluation_dir_for(evaluator_name)
    s = summary(evaluation_dir=edir)
    return {k: v for k, v in s.items() if k != "table"} | {"evaluator": evaluator_name or "package", "evaluation_dir": edir,
                                                            "assets": [(t["job"], t["asset"], t["owner"], (t["automatic"] or {}).get("verdict"))
                                                                       for t in s["table"]]}


def freeze_tags(path: Path = ROWS_FILE, jobs: Path = JOBS) -> int:
    """Write the derived tags into every row that lacks them, so a later change of a job's evidence (a re-measured
    intake) cannot move the coverage table; keeps a backup beside the file. Returns the number of rows updated."""
    from .tags import tags_for_row
    rows = load_rows(path)
    changed = 0
    for r in rows:
        if r.get("tags"):
            continue
        try:
            r["tags"] = tags_for_row(r, jobs)
            changed += 1
        except Exception:  # noqa: BLE001 - a row whose job is unreadable stays untagged
            continue
    if changed:
        backup = Path(path).with_name(Path(path).name + f".before-freeze-{datetime.now().strftime('%Y%m%d-%H%M%S')}")
        backup.write_bytes(Path(path).read_bytes())
        Path(path).write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return changed


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--evaluation-dir", default=EVALUATION_DIR)
    ap.add_argument("--freeze-tags", action="store_true", help="record the derived tags on every row that lacks them (backup kept)")
    args = ap.parse_args(argv)
    if args.freeze_tags:
        print(f"tags frozen on {freeze_tags()} rows")
    s = summary(evaluation_dir=args.evaluation_dir)
    for t in s["table"]:
        a = t["automatic"]
        auto = f"none ({t.get('automatic_reason')})" if a is None else f"{a['verdict']} (evaluator {a['evaluator_overall']}, majors {a['major_discrepancies']}, gates {a['gates']})"
        print(f"{t['product_id']:5s} {t['kind']:9s} {t['job']:11s} {str(t['asset']):15s} owner {t['owner']:10s} automatic {auto} agree {t['agree']} "
              f"tags {','.join(t.get('tags') or []) or '-'}")
    for tag, c in s["coverage"].items():
        print(f"coverage {tag:12s} accepts {c['accepts']} rejects {c['rejects']} borderline {c['borderline']} disagreements {c['disagreements']} "
              f"-> {'covered' if c['covered'] else 'NOT covered'}")
    print(json.dumps({k: v for k, v in s.items() if k != "table"}, indent=1))
    SUMMARY_FILE.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_FILE.write_text(json.dumps(s, indent=1) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
