"""Local job CLI. Edit coverage is offline; approvals are explicit and single-use."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

from . import approvals as A, jobs, procs
from . import delivery, job_run, review, verdicts
from .budget import JobLedger, BudgetGuardError
from .loop import EFFORTS, RunRefused
from .money import owner_money
from .text_io import configure_utf8_stdio


def owner_text_file(path):
    with Path(path).open(encoding="utf-8", newline="") as stream:
        return stream.read()


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
    edit = commands.add_parser("edit")
    edit.add_argument("id")
    edit.add_argument("--delivery-id")
    edit.add_argument("--source-runtime", type=Path, help="explicit archived runtime containing this delivery's exact checkpoint/cameras")
    edit.add_argument("--edit-id")
    edit.add_argument("--option", help="exact amount option id from the shown understanding; required when options were previewed")
    edit.add_argument("--coverage", type=Path, help="ready $0 job edit-check report matching the exact request and executable implementation")
    words = edit.add_mutually_exclusive_group(required=True)
    words.add_argument("--request")
    words.add_argument("--request-file", type=Path)
    words.add_argument("--confirm")
    words.add_argument("--confirm-file", type=Path)
    words.add_argument("--correct")
    words.add_argument("--correct-file", type=Path)
    edit.add_argument("--effort", choices=EFFORTS, default="high")
    edit.add_argument("--cap", required=True)
    edit.add_argument("--settled-stop", required=True)
    edit.add_argument("--settled-only", action="store_true", default=True,
                      help="compatibility flag; all new runs stop on settled charges only")
    fallback = commands.add_parser("edit-item", help="prepare one blocked capture item for its own full paid edit")
    fallback.add_argument("id", help="delivered capture UNDERSTAND round path")
    fallback.add_argument("--item", required=True, help="exact item id from the delivered understanding")
    fallback.add_argument("--cap", required=True)
    fallback.add_argument("--settled-stop", required=True)
    fallback.add_argument("--settled-only", action="store_true", default=True,
                          help="compatibility flag; all new runs stop on settled charges only")
    fallback.add_argument("--effort", choices=("high",), default="high")
    fallback.add_argument("--output", type=Path, help="new fallback round directory")
    apply = commands.add_parser("edit-apply", help="$0 host apply of one sealed, shown preview on a fresh copy; no API")
    apply.add_argument("id", help="source job with --edit-id, or its understand round path")
    apply.add_argument("--edit-id")
    apply.add_argument("--option", required=True)
    apply_words = apply.add_mutually_exclusive_group(required=True)
    apply_words.add_argument("--confirm")
    apply_words.add_argument("--confirm-file", type=Path)
    apply.add_argument("--output", type=Path, help="new copy directory; leave the understanding index unchanged")
    apply.add_argument("--tool-test", action="store_true", help="label an authorized illustrative copy, not a product choice")
    check = commands.add_parser("edit-check", help="$0 explicit item-to-operation mapping; no estimate, worker or API")
    check.add_argument("id", help="source job or existing owner-edit round")
    check_words = check.add_mutually_exclusive_group()
    check_words.add_argument("--request")
    check_words.add_argument("--request-file", type=Path)
    check.add_argument("--mapping", type=Path, help="g3d.edit_item_map/1 JSON with exact request text, labelled spans and operation_kinds")
    check.add_argument("--output", type=Path, help="save the local report here; existing edit rounds also install ready reports into their inputs")
    for name in ("show", "estimate", "approve", "run", "stop", "review", "verdict"):
        cmd = commands.add_parser(name)
        cmd.add_argument("id")
        cmd.add_argument("--edit-id")
        cmd.add_argument("--round")
        if name in ("approve", "run"):
            cmd.add_argument("--run-id")
        if name == "estimate":
            cmd.add_argument("--smoke", action="store_true")
            cmd.add_argument("--resume", action="store_true", help="price and bind the saved conversation and reviewed checkpoint")
            cmd.add_argument("--effort", choices=EFFORTS)
            cmd.add_argument("--turns", type=int)
            cmd.add_argument("--max-output-tokens", type=int)
            cmd.add_argument("--cap")
            cmd.add_argument("--settled-stop")
            cmd.add_argument("--settled-only", action="store_true", default=True,
                             help="compatibility flag; all new runs stop on settled charges only")
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
                   "delivery_kind": report.get("delivery_kind", "astra_run"),
                   "origin": report.get("origin", "run"), "item_id": report.get("item_id"),
                   "side_effects": report.get("side_effects"),
                   "tool_test": report.get("tool_test", False),
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
    if args.command == "edit-item":
        from dataclasses import replace
        from . import owner_fallback
        from .budget import cap_to_micro
        if cap_to_micro(args.cap) <= 0 or cap_to_micro(args.cap) != cap_to_micro(args.settled_stop):
            raise ValueError("full edit item cap must equal its positive finite settled stop")
        round_root = owner_fallback.create_full_edit_item(root, args.item, destination=args.output)
        settings = replace(job_run.settings_full_edit_item(args.settled_stop), effort=args.effort)
        spec, _ = job_run.prepare_run(round_root, settings=settings, cap=args.cap, write_request=True)
        return {"status": "awaiting_separate_run_approval", "phase": "full_edit_item", "item_id": args.item,
                "round_path": str(round_root), "run_id": spec.run_id,
                "request": procs.read_json(round_root / "runs" / spec.run_id / "approval-request.json"),
                "next": "Use job approve and job run with this round_path."}
    if args.command == "edit-apply":
        from . import edits, owner_apply
        if args.edit_id:
            edit_root = edits.edit_location(root, args.edit_id)
        else:
            doc = edits.load(root)
            if not doc or doc["phase"] != "understand":
                raise ValueError("edit-apply needs an understand round path or a source job with --edit-id")
            edit_root = root.parent
        words = args.confirm if args.confirm is not None else owner_text_file(args.confirm_file)
        return owner_apply.apply_preview(edit_root, words, args.option, destination=args.output, tool_test=args.tool_test)
    if args.command == "edit-check":
        from . import edits, edit_capabilities as capabilities
        edit_doc = edits.load(root)
        words = (args.request if args.request is not None else owner_text_file(args.request_file)
                 if args.request_file is not None else edit_doc["request_text"] if edit_doc else None)
        if words is None:
            raise ValueError("edit-check needs --request or --request-file for a source job")
        mapping = edits.read(args.mapping) if args.mapping else None
        report = capabilities.check(words, mapping)
        if report["status"] == "ready" and edit_doc is not None:
            capabilities.install(root, report)
            capabilities.require_round(root)
        if args.output:
            capabilities.write_report(args.output, report)
        return report
    if args.command == "edit":
        from dataclasses import replace
        from . import edits, edit_capabilities as capabilities
        if any(v is not None for v in (args.confirm, args.confirm_file, args.correct, args.correct_file)):
            if not args.edit_id or args.delivery_id:
                raise ValueError("confirmation needs --edit-id and cannot select another delivery")
            words = (args.confirm if args.confirm is not None else args.correct if args.correct is not None
                     else owner_text_file(args.confirm_file or args.correct_file))
            round_root = edits.create_edit(edits.edit_location(root, args.edit_id), words,
                                           correction=args.correct is not None or args.correct_file is not None,
                                           option_id=args.option)
        else:
            if args.option:
                raise ValueError("--option selects an existing preview with --confirm or --correct")
            if args.edit_id:
                raise ValueError("a new request creates a new edit id; use --confirm for the existing understanding")
            words = args.request if args.request is not None else owner_text_file(args.request_file)
            if args.coverage is None:
                raise ValueError("run job edit-check at $0 first, then supply its ready report with --coverage")
            capabilities.require_ready(edits.read(args.coverage), words)
            round_root = edits.create_understand(root, words, delivery_id=args.delivery_id, source_runtime=args.source_runtime)
        if args.coverage is not None:
            capabilities.install(round_root, edits.read(args.coverage))
        settings = replace(job_run.settings_default(), effort=args.effort, settled_stop_usd=args.settled_stop,
                           settled_only=args.settled_only)
        spec, _ = job_run.prepare_run(round_root, settings=settings, cap=args.cap, write_request=True)
        doc = edits.load(round_root)
        return {"status": "awaiting_separate_run_approval", "edit_id": doc["edit_id"],
                "phase": doc["phase"], "round_path": str(round_root), "run_id": spec.run_id,
                "request": procs.read_json(round_root / "runs" / spec.run_id / "approval-request.json"),
                "next": "Use job approve and job run with this round_path, or --edit-id and --round."}
    if getattr(args, "edit_id", None):
        from . import edits
        root = edits.resolve_round(root, args.edit_id, args.round)
    elif getattr(args, "round", None):
        raise ValueError("--round requires --edit-id")
    if args.command == "show":
        from .renewal import durable_receipts
        renewals = []
        seen = set()
        for path in sorted((root / "runs").glob("**/renewals.json")):
            record = procs.read_json(path) or {}
            renewals.append({"receipt": str(path.relative_to(root)), "renewals": record.get("renewals", [])})
            seen.update(row["new_conversation_id"] for row in record.get("renewals", []))
        for path in [root / "session/session.sqlite", *sorted((root / "runs").glob("**/session.sqlite"))]:
            rows = [row for row in durable_receipts(path) if row["new_conversation_id"] not in seen]
            if rows:
                renewals.append({"receipt": str(path.relative_to(root)), "renewals": rows})
                seen.update(row["new_conversation_id"] for row in rows)
        return {"job": jobs.load_job(root), "heartbeat": procs.read_json(root / "heartbeat.json"),
                "pending_run": A.latest_pending_run(root), "conversation_renewals": renewals,
                **delivery_status(root)}
    if args.command == "estimate":
        from dataclasses import replace
        cap = args.cap if args.cap is not None else (args.settled_stop or ("1" if args.smoke else "20"))
        settings = job_run.settings_smoke(cap) if args.smoke else job_run.settings_default()
        settings = replace(settings, effort=args.effort if args.effort is not None else settings.effort,
                           max_turns=(settings.max_turns if args.smoke and args.turns is None else args.turns),
                           max_output_tokens=(args.max_output_tokens if args.max_output_tokens is not None
                                              else settings.max_output_tokens),
                           settled_stop_usd=args.settled_stop if args.settled_stop is not None else cap,
                           settled_only=args.settled_only)
        spec, _ = job_run.prepare_run(root, settings=settings, cap=cap, write_request=True, resume=args.resume)
        return procs.read_json(root / "runs" / spec.run_id / "approval-request.json")
    if args.command == "stop":
        (root / "stop.requested").write_text("Owner requested stop.\n", encoding="utf-8")
        return {"status": "stop_requested", "job_id": root.name}
    if args.command == "review":
        from . import edits
        edit = edits.load(root)
        if edit and edit["phase"] == "understand":
            return edits.review_understanding(root, args.port)
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


def _silence_failed_stream(name):
    """Prevent interpreter shutdown from retrying a failed flush and exiting 120."""
    failed = getattr(sys, name)
    try:
        sink = open(os.devnull, "w", encoding="utf-8")
    except OSError:
        sink = None  # No output descriptor remains available.
    setattr(sys, name, sink)
    if getattr(sys, "__" + name + "__") is failed:
        setattr(sys, "__" + name + "__", sink)


def main(argv=None):
    configure_utf8_stdio()
    try:
        result = dispatch(parser().parse_args(argv))
    except (A.ApprovalRefused, RunRefused, BudgetGuardError, procs.DetachError, ValueError, OSError) as exc:
        print(json.dumps({"status": "refused", "message": str(exc)}), file=sys.stderr, flush=True)
        return getattr(exc, "exit_code", 2)

    # Execution and delivery are finished. Console formatting/writes cannot change
    # their outcome or turn a completed run into an approval/execution refusal.
    run_result = result.get("result") if isinstance(result, dict) else None
    if isinstance(run_result, dict):
        exit_code = 0 if run_result.get("status") == "completed" else 1
    elif isinstance(result, dict) and result.get("schema") == "g3d.edit_capabilities/1":
        exit_code = 0 if result["status"] == "ready" else 2
    else:
        exit_code = 0
    writing = False
    try:
        text = json.dumps(owner_money(result), indent=2, ensure_ascii=False, default=str)
        writing = True
        print(text, flush=True)
    except Exception as exc:
        if writing:
            _silence_failed_stream("stdout")
        warning = {"status": "reporting_failed", "message": f"{type(exc).__name__}: {exc}"}
        if isinstance(run_result, dict):
            warning["run_status"] = run_result.get("status")
            warning["delivery_status"] = (result.get("delivery") or {}).get("status")
        try:
            print(json.dumps(warning, default=str), file=sys.stderr, flush=True)
        except (OSError, ValueError):
            _silence_failed_stream("stderr")
        if not isinstance(run_result, dict):
            return 2
    return exit_code
