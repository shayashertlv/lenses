"""One standard SDK session editing a live Blender scene through upstream MCP.

No construction-program rebuild, phase framework, or custom agent loop. A
request hook bounds inference reservations; no limit establishes scene quality.
SQLite resumes conversation only: keep/reopen the matching Blender checkpoint.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
from contextlib import asynccontextmanager
from dataclasses import asdict, is_dataclass, replace
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import time
import uuid

from agents import Agent, ModelSettings, RunConfig, RunHooks, Runner, SQLiteSession
from agents import function_tool, set_tracing_disabled
from agents.exceptions import MaxTurnsExceeded
from agents.mcp import MCPServerStdio, create_static_tool_filter
from agents.models.openai_provider import OpenAIProvider
from agents.retry import ModelRetrySettings
from agents.run import ToolExecutionConfig
from agents.tool import ToolOutputImage, ToolOutputText
from agents.usage import Usage
from dotenv import dotenv_values
from openai import AsyncOpenAI, DefaultAsyncHttpxClient
from openai.types.shared import Reasoning
from PIL import Image
from .observations import make_read_image, make_record_review

TOOLS = ["get_scene_info", "get_object_info", "get_viewport_screenshot",
         "execute_blender_code", "export_scene", "describe_node_type", "bpy_api_lookup"]
INSTRUCTIONS = """You are the artist responsible for an accurate physical product in Blender and AR.
Work autonomously in the persistent Blender scene. Choose your own modeling methods,
inspection views, experiments and edits. Source photographs are observations of an
object under lighting, not geometry maps. Reconstruct the object that explains them.

For an ambiguous line, band or color patch, consider a real boundary/bevel, reflected
light, refraction of another surface, an internal part, and a geometry/normal defect.
Inspect that region in multiple original views with read_image crops. Prefer an
explanation consistent with silhouette, parallax, occlusion and manufacturing sense.
Common sense is a prior, not permission to ignore contradictory photo evidence.
Do not sculpt a streak, add a groove, or paint a shadow solely because one image has it.
If evidence is insufficient, preserve a simple plausible shape and state uncertainty.

Before a substantial edit, identify the visible defect, your testable explanation
and the view/check that could disprove it. Inspect a section, normals or a temporary
neutral-material view when that separates shape from shading. Restore inspection
overrides before delivery/export. Change one causal family at a time when diagnosing;
compare before/after under the same camera and lighting, and retain a known-good save.
Lighting experiments test a hypothesis; prettier studio illumination is not a product
repair. Judge the full shape and component transitions before adding tiny decoration.

Use actual AR images early and after important geometry/material changes. Inspect
the exported result on the portrait as well as useful synthetic angles and closeups.
The portrait is supplementary runtime evidence, never a product-reference image.
Face occlusion can hide temples: also inspect their full geometry inside Blender.
Compare source and model region by region; do not infer detail quality from a thumbnail.
If an edit fails two meaningful checks, reconsider the cause rather than repeating it.
If two comparisons on the same defect yield neither an evidence-backed improvement
nor new information, stop that line of experimentation. Choose a different testable
explanation, or keep/revert the best reviewed candidate and report the limitation.
An unchanged mesh alone is not stagnation: materials, fit, rollback and verification
can still matter. Once no actionable mismatch or discriminating check remains,
finish honestly; the budget is a ceiling, not a target to spend.
Use record_review at meaningful checkpoints to save brief observations, evidence,
alternative explanations and the next check. Add its progress assessment for matched
before/after experiments, including negative results. Keep notes concise; do not narrate every
thought. Tool success and closed topology are not evidence of photographic accuracy.
After a tool timeout or uncertain outcome, inspect the live scene before repeating
a mutation; a timed-out command may already have changed the model.

Preview and review results include current budget context. Use budget_status for
the full accounting, or after substantial work without a preview/review.
Its planning section estimates the next reservation and when to finalize; it does not
guarantee future calls. Prioritize the largest visible mismatch and reserve
room for final export/review. The ceiling covers all resumed invocations and another
response may not fit near it. Save checkpoints under the output directory before risky
operations. Preserve original sources. Finish with saved scene.blend, the exact reviewed
GLB and specific remaining defects; report uncertainty instead of claiming perfection.
Read exact saved diagnostic details with read_evidence_json when a preview reports
a problem. A captured image does not imply continuity/fitting passed. If a controlled
comparison isolates an AR/runtime failure, preserve the sound model, record the
reproducer and stop trying unrelated geometry/material edits to fix the host.
Blender Python variables are fresh each tool invocation; scene objects persist.
The upstream MCP runs in safe mode: bpy, bmesh, mathutils and pure-Python stdlib
imports are available, but not os/pathlib, open/exec/eval, network/process access,
dunder introspection, handlers, drivers or class registration. Blender operators
for saving, rendering and import/export are allowed. Your output directory exists.
"""


def image_url(path: Path) -> str:
    if path.stat().st_size > 20 * 1024 * 1024:
        raise ValueError("Image exceeds 20 MiB; save a smaller preview")
    payload = path.read_bytes()
    if len(payload) > 20 * 1024 * 1024:
        raise ValueError("Image exceeds 20 MiB; save a smaller preview")
    # Windows MIME registrations can omit WebP. Identify the bytes, not an OS
    # registry or filename extension, and send those exact validated bytes.
    with Image.open(BytesIO(payload)) as source:
        mime = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp",
                "GIF": "image/gif"}.get(source.format)
        if mime is None:
            raise ValueError("Expected a PNG, JPEG, WebP, or GIF image")
        source.verify()
    return f"data:{mime};base64,{base64.b64encode(payload).decode()}"


def make_budget_status(trial, budget):
    @function_tool
    def budget_status() -> dict:
        """Read total trial spending and remaining allowance, including prior invocations."""
        return trial.summary(budget)

    return budget_status


def with_budget_context(tool, trial, budget):
    """Attach a compact balance to a useful observation without another model turn."""
    invoke = tool.on_invoke_tool

    async def with_context(context, arguments):
        result = await invoke(context, arguments)
        state = trial.summary(budget)
        planning = state.get("current_invocation", {}).get("planning", {})
        hint = {"remaining_usd": state["remaining_usd"],
                "planning_status": planning.get("status"),
                "next_reservation_at_last_size_usd": planning.get("next_reservation_at_last_size_usd"),
                "suggested_finish_balance_usd": planning.get("suggested_finish_balance_usd"),
                "advisory_only": True}
        if isinstance(result, dict):
            return {**result, "budget_context": hint}
        if isinstance(result, list):
            return [*result, ToolOutputText(text=json.dumps({"budget_context": hint}))]
        return result  # Preserve tool errors verbatim.

    return replace(tool, on_invoke_tool=with_context)


@asynccontextmanager
async def checkpoint_on_exit(server, directory: Path, summary: dict, log):
    """Save while MCP is still connected, even when inference cannot continue."""
    try:
        yield
    finally:
        path = directory / "stop-checkpoint.blend"
        try:
            result = await server.call_tool("execute_blender_code", {
                "code": "import bpy\nresult = bpy.ops.wm.save_as_mainfile(filepath="
                        + repr(str(path)) + ", copy=True)\nprint('STOP_CHECKPOINT', sorted(result))",
                "user_prompt": "Save the live scene at the end of the autonomous run without changing geometry",
            })
            if getattr(result, "isError", False) or not path.is_file():
                raise RuntimeError("Blender did not produce the stop checkpoint")
            summary["stop_checkpoint"] = {"path": str(path), "saved": True}
        except Exception as error:
            summary["stop_checkpoint"] = {"path": str(path), "saved": False,
                                          "error": f"{type(error).__name__}: {error}"}
        log.event("stop_checkpoint", **summary["stop_checkpoint"])


class ReviewLog(RunHooks):
    """SDK lifecycle logging; image bytes stay in local review files, not console logs."""
    def __init__(self, directory: Path, secret: str = "", stop_file: Path | None = None):
        self.directory, self.secret = directory, secret
        self.stop_file = stop_file
        self.usage, self.interactions = Usage(), 0
        self.budget = None
        self.server = None
        self.sequence = 0
        self.started = time.perf_counter()
        self.tool_starts = {}
        directory.mkdir(parents=True, exist_ok=True)

    def check_stop(self):
        """Cooperative studio stop: finish current I/O, then let checkpoint_on_exit save."""
        if self.stop_file is not None and self.stop_file.is_file():
            raise asyncio.CancelledError("Local studio stop requested")

    def clean(self, value):
        if hasattr(value, "model_dump"):
            value = value.model_dump(mode="json")
        elif is_dataclass(value):
            value = asdict(value)
        if isinstance(value, dict):
            return {key: self.clean(item) for key, item in value.items()}
        if isinstance(value, (tuple, list)):
            return [self.clean(item) for item in value]
        if isinstance(value, str):
            if value.startswith("data:image/") and ";base64," in value:
                header, encoded = value.split(",", 1)
                payload = base64.b64decode(encoded, validate=True)
                suffix = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
                          "image/gif": ".gif"}.get(header[5:].split(";")[0], ".img")
                path = self.directory / (hashlib.sha256(payload).hexdigest()[:20] + suffix)
                if not path.exists():
                    path.write_bytes(payload)
                return {"saved_image": str(path), "sha256": hashlib.sha256(payload).hexdigest(),
                        "bytes": len(payload), "mime_type": header[5:].split(";")[0]}
            return value.replace(self.secret, "[REDACTED]") if self.secret else value
        return value

    def event(self, kind, **fields):
        self.sequence += 1
        item = self.clean({"time": datetime.now(timezone.utc).isoformat(), "event": kind,
                           "sequence": self.sequence, "interaction": self.interactions,
                           "elapsed_s": round(time.perf_counter() - self.started, 6), **fields})
        with (self.directory / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(item, ensure_ascii=False, default=str) + "\n")
        print(kind + (f": {fields['name']}" if "name" in fields else ""), flush=True)

    async def on_llm_start(self, context, agent, system_prompt, input_items):
        self.check_stop()
        self.interactions += 1
        self.event("model_thinking", interaction=self.interactions)

    async def on_llm_end(self, context, agent, response):
        self.usage.add(response.usage)
        self.event("model_response", output=response.output, usage=response.usage,
                   response_id=getattr(response, "response_id", None), request_id=getattr(response, "request_id", None))
        if self.budget is not None:
            await self.budget.settle_latest(self.clean(response.usage))

    async def on_tool_start(self, context, agent, tool):
        self.check_stop()
        call_id = getattr(context, "tool_call_id", None)
        self.tool_starts[call_id or tool.name] = time.perf_counter()
        if self.server is not None and hasattr(self.server, "bind_context"):
            self.server.bind_context(tool_call_id=call_id, response_index=self.interactions, parent_tool=tool.name)
        self.event("tool_started", name=tool.name, arguments=getattr(context, "tool_arguments", None), tool_call_id=call_id)

    async def on_tool_end(self, context, agent, tool, result):
        call_id = getattr(context, "tool_call_id", None)
        started = self.tool_starts.pop(call_id or tool.name, None)
        self.event("tool_finished", name=tool.name, result=result, tool_call_id=call_id,
                   duration_s=round(time.perf_counter() - started, 6) if started is not None else None)
        if self.server is not None and hasattr(self.server, "bind_context"):
            self.server.bind_context(response_index=self.interactions)

    def save_payload(self, kind, payload):
        directory = self.directory / "transport"
        directory.mkdir(exist_ok=True)
        path = directory / f"{self.interactions:04d}-{kind}.json"
        path.write_text(json.dumps(self.clean(payload), ensure_ascii=False, indent=2), encoding="utf-8")
        return str(path)

    async def before_request(self, request):
        """Reserve first, then save the actual serialized input without auth headers."""
        self.check_stop()
        if self.budget is None:
            raise RuntimeError("Evidence hook has no budget guard")
        await self.budget.before_request(request)
        body = request.content
        path = self.save_payload("request", json.loads(body))
        self.event("api_request", payload_path=path, serialized_sha256=hashlib.sha256(body).hexdigest(),
                   serialized_bytes=len(body))

    async def after_response(self, response):
        """Capture provider completion status before the SDK normalizes its output."""
        body = await response.aread()
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            payload = {"non_json_body": body.decode("utf-8", errors="replace")[:16000]}
        path = self.save_payload("response", payload)
        metadata = payload if isinstance(payload, dict) else {}
        self.event("api_response", payload_path=path, http_status=response.status_code,
                   request_id=response.headers.get("x-request-id"), response_id=metadata.get("id"),
                   status=metadata.get("status"), incomplete_details=metadata.get("incomplete_details"),
                   usage=metadata.get("usage"), serialized_sha256=hashlib.sha256(body).hexdigest(),
                   serialized_bytes=len(body))


def mcp_server(args, evidence_directory=None):
    server_type, extra = MCPServerStdio, {}
    if evidence_directory is not None:
        from .evidence import EvidenceMCPServer
        server_type, extra = EvidenceMCPServer, {"evidence_directory": evidence_directory}
    return server_type(
        name="Community Blender MCP",
        params={"command": str(args.mcp_command),
                "args": ["--host", "127.0.0.1", "--port", str(args.mcp_port)],
                "env": {"BLENDER_MCP_DISABLE_TELEMETRY": "1", "BLENDER_MCP_SAFE_MODE": "1"}},
        cache_tools_list=True, use_structured_content=False, max_retry_attempts=0,
        client_session_timeout_seconds=300,
        tool_filter=create_static_tool_filter(allowed_tool_names=TOOLS),
        **extra,
    )


async def run(args, extra_tools=None):
    """Run the standard SDK against the connected live Blender process."""
    from .trial_budget import TrialBudget
    if not args.run_paid:
        raise ValueError("Paid inference is disabled. Add --run-paid to start the agent.")
    with TrialBudget(args.output.resolve(), args.max_usd, args.resume) as trial:
        return await _run_locked(args, trial, extra_tools)


async def _run_locked(args, trial, extra_tools=None):
    set_tracing_disabled(True)
    if not args.run_paid:
        raise ValueError("Paid inference is disabled. Add --run-paid to start the agent.")
    key = (dotenv_values(args.env).get("OPENAI_API_KEY") if args.env else None) or os.getenv("OPENAI_API_KEY")
    if not key:
        raise ValueError("Set OPENAI_API_KEY or pass --env with an explicit dotenv file")
    output = args.output.resolve()
    database = output / "session.sqlite"
    if database.exists() != args.resume:
        raise ValueError("Use --resume for an existing session, or a new output directory for a new session")
    prompt = args.prompt_file.read_text(encoding="utf-8-sig")
    references = [path.resolve(strict=True) for path in args.photo]
    images = [(path, image_url(path)) for path in references]
    invocation = output / "runs" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6])
    log = ReviewLog(invocation, key, getattr(args, "stop_file", None))
    (invocation / "prompt.txt").write_text(prompt, encoding="utf-8")
    (invocation / "instructions.txt").write_text(INSTRUCTIONS, encoding="utf-8")
    configuration = {"model": "gpt-6-astra", "reasoning_effort": args.reasoning_effort,
                     "max_output_tokens": args.max_output_tokens, "max_turns": args.max_turns,
                     "maximum_trial_usd": args.max_usd, "resume": args.resume,
                     "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                     "instructions_sha256": hashlib.sha256(INSTRUCTIONS.encode()).hexdigest()}
    (invocation / "run-config.json").write_text(json.dumps(configuration, indent=2), encoding="utf-8")
    log.event("session_started", model="gpt-6-astra", reasoning=args.reasoning_effort,
              max_turns=args.max_turns, max_output_tokens=args.max_output_tokens,
              maximum_usd=args.max_usd, resume=args.resume)
    session = SQLiteSession("blender", database)
    summary = {"status": "running", "output": str(output), "review_directory": str(invocation)}
    budget = None
    try:
        from .session_inputs import ReferenceInput, plan_session_input
        photos = []
        for index, (path, url) in enumerate(images):
            copied = invocation / f"reference-{index + 1}{path.suffix.lower()}"
            shutil.copy2(path, copied)
            photos.append(ReferenceInput(label=f"Reference {index + 1} ({path.name})",
                                         path=str(copied), image_url=url, detail="high"))
        invocation_text = (f"Output directory: {output}\nThis invocation allows up to {args.max_turns} "
                           f"model responses. The entire trial has a ${args.max_usd} total cap; "
                           f"${trial.remaining_usd()} remains before this invocation. "
                           "Use budget_status to monitor it. Save useful progress regularly.")
        input_plan = plan_session_input(await session.get_items(), prompt=prompt,
                                        photos=photos, invocation_text=invocation_text)
        content = input_plan.content
        (invocation / "input-receipt.json").write_text(json.dumps(input_plan.receipt, indent=2), encoding="utf-8")
        log.event("session_input_prepared", receipt=input_plan.receipt)
        async with AsyncOpenAI(api_key=key, base_url="https://api.openai.com/v1", max_retries=0, timeout=1800) as counter, mcp_server(args, invocation / "edits") as server:
            from .preview import ARPreview
            from .inspect_export import make_inspect_export
            from .evidence_json import EvidenceJSONRegistry, make_read_evidence_json
            budget = trial.reserve_invocation(counter, invocation / "budget.json")
            log.budget = budget
            log.server = server
            evidence_registry = EvidenceJSONRegistry(output)
            preview = ARPreview(server, output, meters_per_unit=args.meters_per_unit,
                                origin=tuple(args.bridge_origin) if args.bridge_origin else None,
                                evidence_registry=evidence_registry)
            agent = Agent(name="Astra Blender", model="gpt-6-astra", instructions=INSTRUCTIONS,
                          mcp_servers=[server], tools=[make_read_image(output, references),
                                                     with_budget_context(make_record_review(output, references, evidence_registry=evidence_registry), trial, budget),
                                                     make_inspect_export(output, evidence_registry=evidence_registry),
                                                     make_read_evidence_json(evidence_registry),
                                                     with_budget_context(preview.tool(), trial, budget),
                                                     with_budget_context(preview.portrait_tool(), trial, budget),
                                                     make_budget_status(trial, budget),
                                                     *(extra_tools or [])],
                          model_settings=ModelSettings(parallel_tool_calls=False, max_tokens=args.max_output_tokens,
                                                       retry=ModelRetrySettings(max_retries=0),
                                                       extra_body={"service_tier": "default"},
                                                       reasoning=Reasoning(effort=args.reasoning_effort)))
            transport = DefaultAsyncHttpxClient(event_hooks={"request": [log.before_request], "response": [log.after_response]})
            async with checkpoint_on_exit(server, invocation, summary, log), AsyncOpenAI(api_key=key, base_url="https://api.openai.com/v1", max_retries=0,
                                   timeout=1800, http_client=transport) as client:
                result = await Runner.run(
                    agent, [{"role": "user", "content": content}], max_turns=args.max_turns,
                    session=session, hooks=log,
                    run_config=RunConfig(tracing_disabled=True, trace_include_sensitive_data=False,
                                         tool_execution=ToolExecutionConfig(max_function_tool_concurrency=1),
                                         model_provider=OpenAIProvider(openai_client=client, use_responses=True)))
            summary.update(status="completed", final_output=result.final_output)
    except MaxTurnsExceeded as exc:
        summary.update(status="turn_limit", error=str(exc))
    except BaseException as exc:
        if budget is not None and budget.blocked_reason:
            summary.update(status="budget_limit" if budget.blocked_kind == "BudgetExceeded" else "failed",
                           error=budget.blocked_reason)
        else:
            summary.update(status="interrupted" if isinstance(exc, (KeyboardInterrupt, asyncio.CancelledError)) else "failed",
                           error=f"{type(exc).__name__}: {exc}")
            raise
    finally:
        summary["budget"] = budget.summary() if budget is not None else None
        summary["trial_budget"] = trial.summary(budget)
        summary["usage"] = log.clean(log.usage)
        (invocation / "result.json").write_text(json.dumps(log.clean(summary), indent=2, default=str), encoding="utf-8")
        session.close()
        log.event("session_finished", status=summary["status"], usage=log.usage)
    print(json.dumps(log.clean(summary), indent=2, default=str), flush=True)
    return summary


async def doctor(args):
    async with mcp_server(args) as server:
        names = [tool.name for tool in await server.list_tools()]
        scene = await server.call_tool("get_scene_info", {"user_prompt": "Inspect the connected Blender scene"})
        print(json.dumps({"tools": names, "scene": scene.model_dump(mode="json")}, indent=2), flush=True)


def main():
    set_tracing_disabled(True)
    parser = argparse.ArgumentParser(description="Standard Astra Agents SDK session with community Blender MCP")
    commands = parser.add_subparsers(dest="command", required=True)
    executable = Path(__file__).resolve().parents[1] / "data/blender_agent/venv" / (
        "Scripts/mcp-for-blender.exe" if os.name == "nt" else "bin/mcp-for-blender")
    for name in ("doctor", "run"):
        command = commands.add_parser(name)
        command.add_argument("--mcp-command", type=Path, default=executable)
        command.add_argument("--mcp-port", type=int, default=9876)
        if name == "run":
            command.add_argument("--output", type=Path, required=True)
            command.add_argument("--prompt-file", type=Path, required=True)
            command.add_argument("--photo", type=Path, action="append", default=[])
            command.add_argument("--env", type=Path, help="Explicit dotenv file containing OPENAI_API_KEY")
            command.add_argument("--reasoning-effort", choices=("high", "max"), default="max",
                                 help="Astra reasoning effort (default: max); both modes obey the trial budget")
            command.add_argument("--max-turns", type=int, default=40, help="SDK interaction limit; not a dollar cap")
            command.add_argument("--max-output-tokens", type=int, default=8192)
            command.add_argument("--max-usd", default="20", help="Immutable total trial cap in USD, shared by all resumes")
            command.add_argument("--resume", action="store_true", help="Continue conversation with the matching Blender scene open")
            command.add_argument("--stop-file", type=Path, help="Cooperatively stop before the next inference/tool when this file exists")
            command.add_argument("--meters-per-unit", type=float, default=.001, help="Source scene unit; .001 for numeric millimetres")
            command.add_argument("--bridge-origin", type=float, nargs=3, help="Bridge underside in source units; defaults to scene metadata")
            command.add_argument("--run-paid", action="store_true", help="Enable paid model inference for this run")
    args = parser.parse_args()
    if args.command == "run" and (args.max_turns < 1 or args.max_output_tokens < 1):
        parser.error("Turn and output-token limits must be positive")
    try:
        summary = asyncio.run(run(args) if args.command == "run" else doctor(args))
        if summary and summary["status"] != "completed":
            raise SystemExit(2)
    except (ValueError, FileNotFoundError) as exc:
        parser.error(str(exc))
