"""Compose a generic job with the existing approval, ledger and Astra loop. No implicit approval."""
from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from uuid import uuid4
from datetime import datetime, timezone

from . import approvals as A, brief, estimate as E, jobs
from . import delivery as D, verdicts, procs, edits, edit_capabilities
from .budget import cap_to_micro
from .job_scene import JobScene
from .instructions import build_instructions, instruction_contract
from .loop import RESULT_SCHEMA, RunSettings, RunSpec, approval_settings, run_astra, tool_registry_sha256
from .temple_lab import stage as temple_stage
from .tools import build_tools


def settings_default() -> RunSettings:
    return RunSettings(effort="max", max_turns=None, settled_stop_usd="20", tool_choice="required",
                       reset_tool_choice=False, finish_tool="finish")


def settings_smoke(cap="1") -> RunSettings:
    """One separately approved read-only request; its stop and reserve cap are identical."""
    return replace(settings_default(), smoke=True, max_turns=1, max_output_tokens=4096,
                   max_continuations=0, settled_stop_usd=cap, finish_tool=None)


def instructions_text(tools) -> str:
    return build_instructions(tools)


def new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:6]


def prepare_run(root, run_id=None, *, settings=None, cap="23", write_request=False, resume=False,
                scene=None, offline_handler=None, env_file=None):
    root = Path(root).resolve()
    job = jobs.load_job(root)
    settings = settings or settings_default()
    owner_edit = edits.load(root)
    if owner_edit:
        edit_capabilities.require_round(root, owner_edit)
        if resume or settings.smoke:
            raise ValueError("each owner edit round is a fresh separately approved conversation")
        settings = replace(settings, finish_tool="understand_finish" if owner_edit["phase"] == "understand" else "finish")
    if any(value is not None for value in (settings.context_guard_tokens, settings.context_guard_bytes,
                                           settings.context_guard_every_requests)):
        raise ValueError("job history is append-only; context guard epochs require a new unguarded run request")
    # Legacy runs retain their approved reserve cap. Explicit settled-only approvals instead bind this
    # amount to the actual settled stop, with no reservation-based admission limit.
    cap_to_micro(cap)
    if settings.settled_stop_usd is None or cap_to_micro(settings.settled_stop_usd) > 20_000_000:
        raise ValueError("job settled-spend stop must be at most the owner's $20 limit")
    if settings.settled_only and (cap_to_micro(cap) != cap_to_micro(settings.settled_stop_usd)
                                 or settings.fit_output_to_budget or settings.smoke):
        raise ValueError("settled-only approval amount must equal the settled stop; no output fitting or smoke")
    if settings.smoke:
        if resume or cap_to_micro(cap) != cap_to_micro(settings.settled_stop_usd):
            raise ValueError("smoke requires a fresh isolated session and equal settled stop and reserve cap")
        if settings.max_turns != 1 or settings.max_output_tokens != 4096 or settings.max_continuations != 0:
            raise ValueError("smoke requires one request, 4096 output tokens and no continuations")
    scene = scene or JobScene(root, job=job)
    scene.scripted = offline_handler is not None
    tools = build_tools(scene, settings)
    stages = () if settings.smoke else (temple_stage.contract(job, settings, cap, **({"owner_edit": owner_edit} if owner_edit else {})),)
    instructions = build_instructions(tools, owner_edit=owner_edit) if owner_edit else instructions_text(tools)
    text = brief.build_brief(job) + (edits.brief_suffix(owner_edit) if owner_edit else "")
    photos = jobs.references(root)
    notes = verdicts.pending_notes(root)
    request = A.build_request(root, instructions_sha256=hashlib.sha256(instructions.encode()).hexdigest(),
                              tool_registry_sha256=tool_registry_sha256(tools, settings),
                              settings=approval_settings(settings, stages), unsent_owner_notes=notes,
                              run_inputs=A.run_input_binding(root, text, photos))
    run_id = run_id or new_run_id()
    spec = RunSpec(job_dir=root, run_id=run_id, instructions=instructions, tools=tools, brief=text, photos=photos,
                   owner_notes=notes, current_request_sha256=A.request_sha256(request), settings=settings,
                   requested_cap_usd=cap, stage_contracts=stages,
                   resume=resume, scene_note=("Start from the exact delivered checkpoint; phase " + owner_edit["phase"]
                       if owner_edit else "Resume the saved head." if resume else "Start with the empty job template."),
                   contract=instruction_contract(tools, owner_edit=owner_edit), contract_brief=brief.host_text(job),
                   offline_handler=offline_handler,
                   env_file=env_file,
                   offline_api_key="sk-offline-job-proof" if offline_handler is not None else None)
    if write_request:
        schemas = json.dumps([{"name": t.name, "description": t.description,
                               "parameters": t.params_json_schema} for t in tools])
        tokens = E.initial_context_tokens(len(instructions) + len(text) + len(schemas) + sum(map(len, notes)), len(photos))
        if settings.smoke:
            cost = E.smoke_estimate(len(instructions) + len(text) + len(schemas) + sum(map(len, notes)),
                                     images=len(photos), cap_usd=cap, one_request=True)
            cost["planning_note"] = "One request only; full canonical tool schemas conservatively counted in this forecast."
        else:
            cost = E.estimate_g3d(tokens, settings.max_turns or 60, settings.max_output_tokens)
            cost["planning_note"] = "Forecast assumes 60 replies when the run has no reply cap; the money limits govern."
        A.write_approval_request(root / "runs" / run_id, job_id=root.name, request=request, estimate=cost,
                                 suggested_cap_usd=cap, requested_cap_usd=cap)
    return spec, scene


def prepared_settings(root, run_id) -> tuple[RunSettings, str]:
    path = Path(root) / "runs" / run_id / "approval-request.json"
    if not path.is_file():
        raise A.ApprovalRefused("missing", "job estimate must create a run request first")
    doc = json.loads(path.read_text(encoding="utf-8"))
    values = dict(doc["request"]["settings"])
    if values.get("smoke") is True:
        values["max_continuations"] = 0
    values.pop("stages", None)  # recomputed from current job/schema bytes; the loop checks the exact binding
    finish = values.pop("finish", None)
    if finish:
        values.update(finish_tool=finish["tool"], finish_k=finish["k"])
    context = values.pop("context_guard", None)
    if context:
        values.update(context_guard_tokens=context["input_tokens"], context_guard_bytes=context.get("request_bytes"),
                      context_guard_every_requests=context.get("every_requests"))
    return RunSettings(**values), doc["requested_cap_usd"]


def deliver_finished(root, scene, result) -> dict:
    """Publish the verified finished head and its evidence as an immutable delivery."""
    return D.deliver_finished(root, scene, result)


async def execute_run(root, run_id, *, resume=False, offline_handler=None, scene=None, smoke=False, env_file=None):
    settings, cap = prepared_settings(root, run_id)
    if settings.smoke != smoke:
        raise A.ApprovalRefused("stale", "smoke and ordinary runs require their own matching approval request")
    spec, scene = prepare_run(root, run_id, settings=settings, cap=cap, resume=resume,
                              offline_handler=offline_handler, scene=scene,
                              **({"env_file": env_file} if env_file is not None else {}))
    root = Path(root).resolve()
    result_path = D._output_path(root, Path("runs") / D._relative(run_id) / "result.json")
    result_existed = result_path.exists()
    try:
        result = await run_astra(spec, scene=scene)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit) as interrupted:
        # The loop completes its teardown and atomically writes result.json before re-raising these signals.
        # A preflight interruption or an earlier run's result must never publish an unrelated checkpoint.
        if not smoke and not result_existed:
            finalized = _fresh_interrupted_result(root, run_id, result_path, scene, interrupted)
            if finalized is not None:
                try:
                    _publish_result(root, run_id, scene, finalized)
                except Exception as error:
                    # Preserve the owner's original interruption even if local draft publication fails.
                    interrupted.add_note(f"Draft publication failed: {type(error).__name__}: {error}")
        raise
    return {"result": result, "delivery": _publish_result(root, run_id, scene, result, smoke=smoke)}


def _fresh_interrupted_result(root, run_id, path, scene, interrupted):
    try:
        if path != D._output_path(root, path) or not path.is_file():
            return None
        result = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(result, dict) or result.get("schema") != RESULT_SCHEMA
                or result.get("job_id") != root.name or result.get("run_id") != run_id
                or result.get("status") != ("failed" if isinstance(interrupted, SystemExit) else "stopped")
                or not isinstance(result.get("error"), dict)
                or result["error"].get("type") != type(interrupted).__name__
                or not isinstance(result.get("teardown_errors"), list) or "exit_checkpoint" not in result
                or getattr(getattr(scene, "s", None), "worker", None) is not None):
            return None
        started = datetime.fromisoformat(result["started"])
        finished = datetime.fromisoformat(result["finished"])
        if started.tzinfo is None or finished.tzinfo is None or finished < started:
            return None
        return result
    except (OSError, ValueError, TypeError, KeyError):
        return None


def _publish_result(root, run_id, scene, result, *, smoke=False):
    if smoke:
        # A connectivity proof has no product deliverable and cannot certify a finish.
        return {"status": "smoke_only", "finished": False, "accepted_finish": False}
    edit = getattr(scene, "owner_edit", None)
    if edit and edit["phase"] == "understand":
        artifact = Path(root) / "work/owner_edit/understanding.json"
        accepted = result.get("status") == "completed" and artifact.is_file()
        if accepted:
            try:
                understanding = edits.read(artifact)
                shown = edits.read(artifact.with_name("understanding-delivery.json"))
                state = Path(root) / "work/job_scene/state.json"
                unseen = edits.read(state).get("unseen", []) if state.exists() else []
                accepted = (understanding.get("status") in {"awaiting_owner_confirmation", "diagnostic_only"}
                            and bool(understanding.get("operations")) ==
                                (understanding.get("status") == "awaiting_owner_confirmation")
                            and shown.get("delivered") is True and shown.get("sha256") == edits.sha(artifact)
                            and shown.get("call_id") == understanding.get("call_id")
                            and shown.get("call_id") not in unseen
                            and understanding.get("source_fingerprint") == edit["source"]["fingerprint"]
                            and all(edits.sha(edits.confined(root, name)) == digest
                                    for name, digest in understanding["evidence_files"].items()))
            except (OSError, ValueError, KeyError, TypeError):
                accepted = False
        budget = result.get("budget") or {}
        settled = budget.get("settled_usd")
        micro = budget.get("settled_micro_usd")
        if settled is None and micro is not None:
            settled = f"{micro / 1_000_000:.6f}"
        if not budget and getattr(scene, "_review_log", None) is None:
            settled, micro = "0.000000", 0
        usage = {"schema": "g3d.owner_understanding_usage/1", "run_id": run_id,
                 "requests": result.get("requests", 0), "settled_usd": settled,
                 "settled_micro_usd": micro}
        if accepted:
            procs.write_json_atomic(Path(root) / "work/owner_edit/understanding-usage.json", usage)
        page = edits.understanding_page(root) if accepted else None
        return {"status": understanding["status"] if accepted else "understanding_incomplete",
                "finished": False, "accepted_finish": False, "geometry_changed": False,
                "understanding": str(artifact) if accepted else None, "review_page": str(page) if page else None,
                "usage": usage}
    final_failure = None
    if result.get("status") == "completed":
        try:
            delivery = deliver_finished(root, scene, result)
            if delivery.get("status") != "delivered":
                final_failure = {"reason": "Run completed without a publishable accepted finish.",
                                 "attempt": delivery}
        except Exception as exc:
            final_failure = {"reason": f"{type(exc).__name__}: {exc}"}
    if final_failure is not None:
        delivery = D.deliver_draft(root, scene, result, final_delivery_failure=final_failure)
    elif result.get("status") != "completed":
        delivery = D.deliver_draft(root, scene, result)
    # Also retain an explicit no-usable-checkpoint attempt when there is nothing the owner can review.
    procs.write_json_atomic(D._output_path(Path(root).resolve(), Path("runs") / D._relative(run_id) / "delivery.json"),
                           delivery)
    return delivery
