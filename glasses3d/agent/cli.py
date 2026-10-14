"""Local job CLI. Estimates are free; approvals are explicit and single-use."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

from . import approvals as A, jobs, procs
from . import delivery, job_run, review, verdicts
from .budget import JobLedger, BudgetGuardError
from .loop import RunRefused
from .money import owner_money


def parser():
    ap = argparse.ArgumentParser(prog="python -m glasses3d")
    group = ap.add_subparsers(dest="group", required=True)
    job = group.add_parser("job")
    job.add_argument("--jobs-root", type=Path, default=None)
    commands = job.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    for key in ("images", "views", "specs"):
        create.add_argument("--" + key, type=Path, required=key != "specs")
    create.add_argument("--name", required=True)
    create.add_argument("--description", required=True)
    migrate = commands.add_parser("migrate-verdicts")
    ledger_dir = Path(__file__).resolve().parents[1] / "data/ledger"
    migrate.add_argument("--source", type=Path, default=ledger_dir / "owner_verdicts.jsonl")
    migrate.add_argument("--ledger", type=Path, default=ledger_dir / "verdicts.jsonl")
    for name in ("show", "estimate", "approve", "run", "stop", "review", "verdict"):
        cmd = commands.add_parser(name)
        cmd.add_argument("id")
        if name in ("approve", "run"):
            cmd.add_argument("--run-id")
        if name == "estimate":
            cmd.add_argument("--smoke", action="store_true")
            cmd.add_argument("--turns", type=int)
            cmd.add_argument("--max-output-tokens", type=int)
            cmd.add_argument("--cap")
            cmd.add_argument("--settled-stop")
        elif name == "approve":
            cmd.add_argument("--max-usd", required=True)
            cmd.add_argument("--owner-words", required=True)
        elif name == "run":
            cmd.add_argument("--smoke", action="store_true")
            cmd.add_argument("--env-file", type=Path, help="credential file read only by the existing runner loader")
            cmd.add_argument("--resume", action="store_true")
            cmd.add_argument("--foreground", action="store_true", help="default is a detached, logged runner")
        elif name == "review":
            cmd.add_argument("--delivery-id")
            cmd.add_argument("--port", type=int, default=8251)
        elif name == "verdict":
            cmd.add_argument("verdict", choices=("accept", "changes"))
            cmd.add_argument("--notes", required=True, help="Owner's exact words about this delivery")
            cmd.add_argument("--delivery-id")
            cmd.add_argument("--sha256")
    return ap


def resolve_job(value, jobs_root=None):
    root = Path(jobs_root or Path(__file__).resolve().parents[1] / "data/jobs").resolve()
    path = Path(value)
    if path.is_absolute():
        resolved = path.resolve()
    else:
        if len(path.parts) != 1 or value in (".", ".."):
            raise ValueError("job id must be a directory name or an absolute job path")
        resolved = root / path
    jobs.load_job(resolved)
    return resolved


def run_id_for(root, requested):
    run_id = requested or A.latest_pending_run(root)
    if not run_id:
        raise A.ApprovalRefused("missing", "no pending run request; use job estimate first")
    if len(Path(run_id).parts) != 1 or run_id in (".", "..") or Path(run_id).is_absolute():
        raise ValueError("run id must be one directory name")
    return run_id


def delivery_status(root):
    """Show verified delivery failures explicitly; an unfinished draft is never an accepted finish."""
    current = None
    if (root / "deliveries/current.json").is_file():
        saved = delivery.load_delivery(root)
        report = saved["report_data"]
        finished = report.get("finished", True)  # historical immutable deliveries required an accepted finish
        current = {"status": "finished" if finished else "draft", "finished": finished,
                   "accepted_finish": report.get("accepted_finish", finished),
                   "run_status": report.get("run_status", "completed"), "delivery_id": saved["delivery_id"],
                   "glb_sha256": saved["glb_sha256"], "report": saved["report"],
                   "checks": {key: report.get(key) for key in ("validate_spec", "seating", "print_check", "arview")}}
    attempts = list((root / "runs").glob("*/delivery.json"))
    latest = max(attempts, key=lambda path: (path.stat().st_mtime_ns, str(path))) if attempts else None
    return {"delivery": current, "last_delivery_attempt": procs.read_json(latest) if latest else None}


def dispatch(args):
    if args.command == "migrate-verdicts":
        return verdicts.migrate_legacy(args.source, args.ledger)
    if args.command == "create":
        root = jobs.create_job(images=args.images, views=args.views, name=args.name, description=args.description,
                               specs=args.specs, jobs_root=args.jobs_root)
        return {"status": "created", "job_id": root.name, "path": str(root)}
    root = resolve_job(args.id, args.jobs_root)
    if args.command == "show":
        return {"job": jobs.load_job(root), "heartbeat": procs.read_json(root / "heartbeat.json"),
                "pending_run": A.latest_pending_run(root), **delivery_status(root)}
    if args.command == "estimate":
        from dataclasses import replace
        cap = args.cap if args.cap is not None else ("1" if args.smoke else "23")
        settings = job_run.settings_smoke(cap) if args.smoke else job_run.settings_default()
        settings = replace(settings, max_turns=(settings.max_turns if args.smoke and args.turns is None else args.turns),
                           max_output_tokens=(args.max_output_tokens if args.max_output_tokens is not None
                                              else settings.max_output_tokens),
                           settled_stop_usd=args.settled_stop if args.settled_stop is not None else settings.settled_stop_usd)
        spec, _ = job_run.prepare_run(root, settings=settings, cap=cap, write_request=True)
        return procs.read_json(root / "runs" / spec.run_id / "approval-request.json")
    if args.command == "stop":
        (root / "stop.requested").write_text("Owner requested stop.\n", encoding="utf-8")
        return {"status": "stop_requested", "job_id": root.name}
    if args.command == "review":
        return review.review_job(root, delivery_id=args.delivery_id, port=args.port)
    if args.command == "verdict":
        delivery_id, digest = args.delivery_id, args.sha256
        if delivery_id is None and digest is None:
            binding = review.reviewed_delivery(root)
            delivery_id, digest = binding["delivery_id"], binding["glb_sha256"]
        return verdicts.append_verdict(root, args.verdict, args.notes, delivery_id=delivery_id,
                                       expected_sha256=digest)
    rid = run_id_for(root, args.run_id)
    if args.command == "approve":
        return A.write_approval(root / "runs" / rid, max_usd=args.max_usd, owner_words=args.owner_words,
                                 recorded_by="owner via job approve")
    # Preflight in the caller too: a missing/stale approval refuses synchronously before detaching.
    settings, cap = job_run.prepared_settings(root, rid)
    if settings.smoke != args.smoke:
        raise A.ApprovalRefused("stale", "smoke and ordinary runs require their own matching approval request")
    spec, _ = job_run.prepare_run(root, rid, settings=settings, cap=cap, resume=args.resume)
    with JobLedger(root) as ledger:
        ledger.preflight(rid, current_request_sha256=spec.current_request_sha256, requested_cap_usd=cap)
    if args.foreground:
        return asyncio.run(job_run.execute_run(root, rid, resume=args.resume,
                            **({"smoke": True} if args.smoke else {}),
                            **({"env_file": args.env_file.resolve()} if args.env_file is not None else {})))
    argv = [sys.executable, "-m", "glasses3d", "job", "run", str(root), "--run-id", rid, "--foreground"]
    if args.resume:
        argv.append("--resume")
    if args.smoke:
        argv.append("--smoke")
    if args.env_file is not None:
        argv.extend(["--env-file", str(args.env_file.resolve())])
    child = procs.spawn_detached(argv, cwd=Path(__file__).resolve().parents[2],
                                  log_path=root / "runs" / rid / "runner.log",
                                  status_path=root / "runs" / rid / "runner-status.json", keep_awake=True)
    return {"status": "started", "job_id": root.name, "run_id": rid, **child.record()}


def main(argv=None):
    try:
        result = dispatch(parser().parse_args(argv))
        print(json.dumps(owner_money(result), indent=2, ensure_ascii=False, default=str))
        if isinstance(result, dict) and "result" in result:
            return 0 if result["result"].get("status") == "completed" else 1
        return 0
    except (A.ApprovalRefused, RunRefused, BudgetGuardError, procs.DetachError, ValueError, OSError) as exc:
        print(json.dumps({"status": "refused", "message": str(exc)}), file=sys.stderr)
        return getattr(exc, "exit_code", 2)
