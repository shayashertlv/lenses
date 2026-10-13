"""Compose a generic job with the existing approval, ledger and Astra loop. No implicit approval."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4
from datetime import datetime, timezone

from . import approvals as A, brief, estimate as E, jobs
from . import delivery as D, verdicts
from .budget import cap_to_micro
from .job_scene import JobScene
from .instructions import build_instructions, instruction_contract
from .loop import RunSettings, RunSpec, approval_settings, run_astra, tool_registry_sha256
from .temple_lab import stage as temple_stage
from .tools import build_tools


def settings_default() -> RunSettings:
    return RunSettings(effort="max", max_turns=None, settled_stop_usd="20", tool_choice="required",
                       reset_tool_choice=False, finish_tool="finish", context_guard_tokens=150000,
                       context_guard_every_requests=3)


def instructions_text(tools) -> str:
    return build_instructions(tools)


def new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:6]


def prepare_run(root, run_id=None, *, settings=None, cap="23", write_request=False, resume=False,
                scene=None, offline_handler=None):
    root = Path(root).resolve()
    job = jobs.load_job(root)
    settings = settings or settings_default()
    if cap_to_micro(cap) > 23_000_000:
        raise ValueError("job reservation cap cannot exceed the owner's $23 limit")
    if settings.settled_stop_usd is None or cap_to_micro(settings.settled_stop_usd) > 20_000_000:
        raise ValueError("job settled-spend stop must be at most the owner's $20 limit")
    scene = scene or JobScene(root, job=job)
    tools = build_tools(scene, settings)
    stages = (temple_stage.contract(job, settings, cap),)
    instructions = instructions_text(tools)
    text = brief.build_brief(job)
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
                   resume=resume, scene_note="Resume the saved head." if resume else "Start with the empty job template.",
                   contract=instruction_contract(tools), contract_brief=brief.host_text(job),
                   offline_handler=offline_handler,
                   offline_api_key="sk-offline-job-proof" if offline_handler is not None else None)
    if write_request:
        schemas = json.dumps([t.params_json_schema for t in tools])
        tokens = E.initial_context_tokens(len(instructions) + len(text) + len(schemas) + sum(map(len, notes)), len(photos))
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


async def execute_run(root, run_id, *, resume=False, offline_handler=None, scene=None):
    settings, cap = prepared_settings(root, run_id)
    spec, scene = prepare_run(root, run_id, settings=settings, cap=cap, resume=resume,
                              offline_handler=offline_handler, scene=scene)
    result = await run_astra(spec, scene=scene)
    delivery = deliver_finished(root, scene, result)
    return {"result": result, "delivery": delivery}
