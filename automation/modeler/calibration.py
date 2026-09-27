"""The visual bar: owner verdicts from the live try-on versus the automatic verdict of the same assets.

    python -m modeler.calibration            # table + data/modeler/calibration/calibration.json

Rows come from ``owner_verdicts.jsonl`` (``modeler.owner_verdict``). The automatic verdict of an asset is the v2
evaluator's stored evaluation (``<asset>/evaluation_v2/evaluation.json``, written by ``modeler.evaluate_asset``)
combined with the asset's own metrics and runtime flags through ``evaluate.decide_status`` — exactly what a job would
compute for it. The protocol counts as calibrated when the set holds at least ``MIN_ACCEPTS`` owner accepts and
``MIN_REJECTS`` owner rejects and the automatic verdict agrees with the owner on every one of them (borderline rows
are reported, not counted). A job reads this at freeze time (``evaluation_protocol.json: visual_bar_calibrated``)
and may award ``accepted`` on its own only when it is true.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from .paths import JOBS, MODELER_DATA

ROWS_FILE = MODELER_DATA / "calibration" / "owner_verdicts.jsonl"
SUMMARY_FILE = MODELER_DATA / "calibration" / "calibration.json"
EVALUATION_DIR = "evaluation_v2"
MIN_ACCEPTS = 3
MIN_REJECTS = 3


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


def automatic_verdict(row: dict, jobs: Path = JOBS, evaluation_dir: str = EVALUATION_DIR) -> dict | None:
    """What a job would decide for this asset with the current rule and the v2 evaluation; None without one."""
    from .evaluate import decide_status
    d = asset_dir(row, jobs)
    ev_path = d / evaluation_dir / "evaluation.json"
    obs_path = d / "observe" / "observation.json"
    if not ev_path.exists() or not obs_path.exists():
        return None
    ev = _load(ev_path)["evaluation"]
    obs = _load(obs_path)
    held = _load(d / "heldout" / "heldout.json") if (d / "heldout" / "heldout.json").exists() else None
    summary = obs.get("summary") or {}
    st = decide_status(candidate_valid=bool(summary.get("ar_runtime_compatible")), metrics=summary, heldout=held, evaluation=ev,
                       input_flags=[], protocol_calibrated=False)
    return {"verdict": st.get("automatic_verdict", "reject"), "evaluator_overall": ev.get("overall"),
            "major_discrepancies": st.get("major_discrepancies"), "absent_features": st.get("absent_features"),
            "gates": {k: v.get("pass") for k, v in (st.get("provisional") or {}).items() if v.get("gate")},
            "resemblance_0_10": ev.get("resemblance_0_10"), "evaluation_path": str(ev_path)}


def agreement(rows: list[dict] | None = None, jobs: Path = JOBS, evaluation_dir: str = EVALUATION_DIR) -> list[dict]:
    out = []
    for r in latest_by_asset(rows if rows is not None else load_rows()):
        auto = automatic_verdict(r, jobs, evaluation_dir)
        owner = r["verdict"]
        agree = None
        if auto is not None:
            agree = True if owner == "borderline" else (auto["verdict"] == owner)
        out.append({"product_id": r["product_id"], "kind": r.get("kind", "delivered"), "job": r["job"],
                    "asset": r.get("candidate") or r.get("baseline"), "asset_sha256": r["asset_sha256"],
                    "owner": owner, "owner_note": r.get("note", ""), "automatic": auto, "agree": agree})
    return out


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
        reasons.append(f"{len(unevaluated)} owner-judged assets have no {evaluation_dir} evaluation yet")
    return {"computed": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"), "rows_file": str(ROWS_FILE),
            "evaluation_dir": evaluation_dir, "min_accepts": min_accepts, "min_rejects": min_rejects,
            "owner_accepts_evaluated": accepts, "owner_rejects_evaluated": rejects,
            "borderline": sum(1 for t in table if t["owner"] == "borderline"), "unevaluated": len(unevaluated),
            "disagreements": len(disagreements), "calibrated": calibrated, "reasons": reasons, "table": table}


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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--evaluation-dir", default=EVALUATION_DIR)
    args = ap.parse_args(argv)
    s = summary(evaluation_dir=args.evaluation_dir)
    for t in s["table"]:
        a = t["automatic"]
        auto = "none" if a is None else f"{a['verdict']} (evaluator {a['evaluator_overall']}, majors {a['major_discrepancies']}, gates {a['gates']})"
        print(f"{t['product_id']:5s} {t['kind']:9s} {t['job']:11s} {str(t['asset']):15s} owner {t['owner']:10s} automatic {auto} agree {t['agree']}")
    print(json.dumps({k: v for k, v in s.items() if k != "table"}, indent=1))
    SUMMARY_FILE.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_FILE.write_text(json.dumps(s, indent=1) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
