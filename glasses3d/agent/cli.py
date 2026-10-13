"""Local job CLI. Estimates are free; approvals are explicit and single-use."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

from . import approvals as A, jobs, procs
from . import job_run, review, verdicts
from .budget import JobLedger, BudgetGuardError
from .loop import RunRefused


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
            cmd.add_argument("--turns", type=int)
            cmd.add_argument("--max-output-tokens", type=int, default=16384)
            cmd.add_argument("--cap", default="23")
            cmd.add_argument("--settled-stop", default="20")
        elif name == "approve":
            cmd.add_argument("--max-usd", required=True)
            cmd.add_argument("--owner-words", required=True)
        elif name == "run":
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
                "pending_run": A.latest_pending_run(root)}
    if args.command == "estimate":
        settings = job_run.settings_default()
        from dataclasses import replace
        settings = replace(settings, max_turns=args.turns, max_output_tokens=args.max_output_tokens,
                           settled_stop_usd=args.settled_stop)
        spec, _ = job_run.prepare_run(root, settings=settings, cap=args.cap, write_request=True)
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
    spec, _ = job_run.prepare_run(root, rid, settings=settings, cap=cap, resume=args.resume)
    with JobLedger(root) as ledger:
        ledger.preflight(rid, current_request_sha256=spec.current_request_sha256, requested_cap_usd=cap)
    if args.foreground:
        return asyncio.run(job_run.execute_run(root, rid, resume=args.resume))
    argv = [sys.executable, "-m", "glasses3d", "job", "run", str(root), "--run-id", rid, "--foreground"]
    if args.resume:
        argv.append("--resume")
    child = procs.spawn_detached(argv, cwd=Path(__file__).resolve().parents[2],
                                  log_path=root / "runs" / rid / "runner.log",
                                  status_path=root / "runs" / rid / "runner-status.json", keep_awake=True)
    return {"status": "started", "job_id": root.name, "run_id": rid, **child.record()}


def main(argv=None):
    try:
        result = dispatch(parser().parse_args(argv))
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        if isinstance(result, dict) and "result" in result:
            return 0 if result["result"].get("status") == "completed" else 1
        return 0
    except (A.ApprovalRefused, RunRefused, BudgetGuardError, procs.DetachError, ValueError, OSError) as exc:
        print(json.dumps({"status": "refused", "message": str(exc)}), file=sys.stderr)
        return getattr(exc, "exit_code", 2)
