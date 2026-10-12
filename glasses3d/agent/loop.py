"""The Astra loop (WP2_DESIGN section 7): one stock Agents SDK ``Runner`` session per run, money gated per section 8.

Order of one run (``run_astra``):

1. **$0 preflight** under the job's ledger lock: the owner approval for exactly this request (``JobLedger.preflight``:
   missing, stale, consumed or below cap refuses with ``ApprovalRefused``, exit code 3, 0 requests) and the
   instructions, tool registry and settings this loop will send equal the ones the approval was estimated for; the
   required two-way tool contract; the session file agrees with ``resume``; disk space; the clients can
   be built (an API key, or the offline proof conditions) without building them; a stale ``stop.requested`` is
   removed; the scene starts on its expected checkpoint. Any other refusal is ``RunRefused`` (exit code 2).
2. **Resume planning** ($0) on a resumed session: identify journaled mutating calls whose turn was not recorded.
   Read history and plan the rollback without restoring the scene or repairing the session yet.
3. **Consume the approval** (``JobLedger.open_paid_run``: exclusive ``budget.json`` + the machine-wide paid-run lock).
   Capture the implementation receipt, then restore ``checkpoint_before`` of the first lost call and record the
   rollback in the invocation text. Answer dangling calls with an "interrupted" output and plan new input against
   ``session.get_items()`` (reuse unchanged images/brief; append unsent owner notes verbatim once).
4. Only now the clients (``model_clients(run)``: production or offline over ``httpx2.MockTransport``), the guard
   (``BudgetGuard.attach``), the receipts (``ReviewLog``: request hook = guard then archive, response hook = archive
   then guard, ``on_llm_end`` cross-check), SDK tracing disabled without building its exporter, the heartbeat thread.
5. ``Runner.run`` with exactly the section-7 construction; an ``incomplete`` response (settled by the transport
   hook, then raised by the SDK) gets up to 2 continuation runs on the same session with the host note, each
   counting against ``max_turns``; every other outcome maps to one status. With ``RunSettings.finish_tool`` (the
   facet runs of WP6_FACETS §4.3) the run is final only when that tool's result has status "accepted"
   (``FinishOnAccepted``); a text reply without it continues with one factual host note, up to ``max_continuations``
   notes, then ends ``ended_without_finish``. ``tool_choice`` / ``reset_tool_choice`` set the model's tool choice;
   ``context_guard_tokens`` adds the one-epoch image filter (``ContextGuard``). All of them default off, are bound
   into the approval settings and the tool-registry sha, and leave a default run's requests unchanged.
6. **Always**: an open request becomes unknown (``guard.transport_failed`` in the except clause for a connection
   error or timeout, ``guard.mark_unknown`` in finally), the exit checkpoint (head sha re-verified) is taken before the
   worker closes, the paid run is closed (``JobLedger.close_paid_run``), ``result.json`` is written.

Statuses: completed, turn_limit, budget_limit, stopped, provider_rejected (4xx, $0), provider_unknown (5xx, non-JSON,
connection error or timeout; the full hold kept), output_limit, failed_worker, failed (with the exception type),
ended_without_finish (a finish-gated run whose replies kept ending without the accepted finish).

The scene (stage A's ``BlenderSession`` behind stage D's tools) is reached only through ``SceneHooks``; tools are
injected (stage D builds the registry). Tools receive a ``RunStatus`` as the SDK run context (``ctx.context``): money
and context numbers for their result footers. Nothing here imports from automation/ or reads automation/.env.
"""
from __future__ import annotations

import asyncio
import base64
import copy
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import threading
import time
import zlib
from typing import Any, Callable, Iterable, Sequence

from agents import Agent, ModelSettings, RunConfig, Runner, SQLiteSession
from agents.agent import ToolsToFinalOutputResult
from agents.exceptions import MaxTurnsExceeded, ModelBehaviorError
from agents.models.openai_provider import OpenAIProvider
from agents.retry import ModelRetrySettings
from agents.run import ToolExecutionConfig
from agents.run_config import ModelInputData
from agents.tool import FunctionTool, default_tool_error_function
from openai import APIConnectionError
from openai.types.shared import Reasoning

from .approvals import ApprovalRefused, build_request, canonical_json, request_sha256, run_input_binding
from .budget import BudgetGuard, JobLedger, unlink_with_retry
from .offline import (BASE_URL, OFFLINE_ENV, OFFLINE_KEY_PREFIX, disable_sdk_tracing, offline_counting_client,
                      offline_inference_client, require_offline)
from .pricing import MODEL, usd_text
from .receipts import BlobStore, Cleaner, ReviewLog, StopRequested, write_json
from .session_inputs import ReferenceInput, plan_session_input
from .snapshot import capture as capture_implementation

AGENT_NAME = "Astra"
SESSION_ID = "astra"
RESULT_SCHEMA = "g3d.run_result/1"
RUN_CONFIG_SCHEMA = "g3d.run_config/1"
HEARTBEAT_SCHEMA = "g3d.heartbeat/1"
STATUSES = ("completed", "turn_limit", "budget_limit", "stopped", "provider_rejected", "provider_unknown",
            "output_limit", "failed_worker", "failed", "ended_without_finish")
EFFORTS = ("high", "max")
TOOL_CHOICES = ("auto", "required")
FINISH_BEHAVIOR = "g3d.finish_on_accepted/1"       # FinishOnAccepted (name and version bound into the approval)
CONTEXT_GUARD = "g3d.context_guard/1"              # ContextGuard
CONTINUATION_NOTE = ("Your previous response was cut off at the output limit and was not recorded. Continue.")
FINISH_NOTE = "Your reply called no tool. The run ends only when `{tool}` returns status accepted."
DANGLING_OUTPUT = ("interrupted: the run ended before this call returned; it has no result and any effect it had on "
                   "the scene was rolled back")
INFERENCE_TIMEOUT_S = 900.0         # the model-wait ceiling (observed maximum 165 s)
COUNT_TIMEOUT_S = 60.0
HEARTBEAT_INTERVAL_S = 2.0
STATUS_LINE_AFTER_S = 30.0
STOP_FILE = "stop.requested"


class RunRefused(Exception):
    """A $0 preflight refusal other than the approval (exit code 2): nothing was consumed and no request sent."""

    exit_code = 2


# --------------------------------------------------------------------------- run description


# Section 7's table of hard timeouts (s). The SDK function_tool timeout is hard + 10 s with the default
# timeout_behavior "error_as_result", so Astra receives an error result (tool.py:517, :2238-2269; pinned in
# test_loop_sdk_pins); a tool still running at hard + 30 s is killed through the Job Object (stage E's watchdog).
# The SDK accepts a timeout only on async function tools (tool.py:2925-2928). execute_blender_code's hard timeout is
# its soft timeout + 30 s per call, so its SDK timeout covers the largest soft timeout (600 s).
HARD_TIMEOUT_S = {
    "seat_lens": 150, "hide_eyewire": 150, "unseat_lens": 150, "unhide_eyewire": 150, "check_lens_seating": 120,
    "render_views": 300, "compare_to_photos": 300, "export_glb": 120, "save_checkpoint": 60, "list_checkpoints": 60,
    "restore_checkpoint": 60, "arview_capture": 300, "fit_cameras": 1800, "project_photo_texture": 1800,
    "deliver": 600, "pick_surface_point": 30, "read_image": 30, "read_report": 30, "scene_summary": 30,
    # the facet tools (WP6_FACETS §4.3): 3 x the slowest call measured on the Prada lab (agent/temple_lab, a warm
    # worker, studio_dome_v1 and photo_env, 2026-10-11), rounded up, never below 30 s: inspect 0.3 s, photo_image
    # 0.1 s, photo_compare 12.3 s (6 photos uncached, full crop), facet_edit 18.2 s (apply + 5 photos uncached);
    # facet_variants 0.82 s per render + 2.9 s (21 renders 20.0 s) -> its table value 90 s = FACET_VARIANTS_BASE_S +
    # FACET_VARIANTS_PER_RENDER_S x 3 x 7 renders (82.5 s) rounded up. Its worker deadline (hard_timeout_s with
    # photos and variants) scales with the renders, photos x (variants + 1), up to 4 x 7 = 28 (lab.py module doc),
    # capped at the table value minus FACET_VARIANTS_HOST_MARGIN_S (75 s: 3.75 x the measured 21 renders, about
    # 2.9 x the 26 s estimated for 28), so the host's builds before it and crease lines and sheets after it keep
    # 15 s inside the hard timeout, plus the SDK's 10 s (the measured whole call took 12.8 s).
    # facet_restore and facet_finish (measured 0.3 s and 0.2 s) are set above their worst declared inner deadlines
    # plus FACET_DEADLINE_MARGIN_S, so the SDK never answers E_TIMEOUT while a restore or export may still succeed:
    # restore = one checkpoint restore (journal.CHECKPOINT_DEADLINE_S 60 s) -> 90; finish = that restore and the
    # export (seeing.EXPORT_DEADLINE_S 120 s) -> 210 (test_loop_facet pins both against the imported deadlines).
    # photo_read (WP6 v3, host only: readings.read_point + one pick sheet a reading, no worker): 0.1-0.2 s a reading
    # measured on the Prada lab (v3p2-lab-e2), so 8 readings stay far under the 30 s floor. facet_restore is no
    # longer registered in v3 (WP6_V3 §6); its row stays for the v2 rehearsal records.
    "facet_inspect": 30, "photo_image": 30, "photo_read": 30, "photo_compare": 40, "facet_edit": 60,
    "facet_variants": 90, "facet_restore": 90, "facet_finish": 210,
}
FACET_DEADLINE_MARGIN_S = 30.0      # host work after the worker calls and a warm-spare restart (Blender 3.1-4.5 s)
FACET_VARIANTS_BASE_S, FACET_VARIANTS_PER_RENDER_S = 30.0, 2.5
FACET_VARIANTS_HOST_MARGIN_S = 15.0  # facet_variants' host work around its render, inside the hard timeout
SNIPPET_SOFT_DEFAULT_S, SNIPPET_SOFT_MIN_S, SNIPPET_SOFT_MAX_S = 120.0, 0.5, 600.0
SNIPPET_HARD_MARGIN_S = 30.0
SDK_TIMEOUT_MARGIN_S = 10.0


def hard_timeout_s(tool_name: str, *, soft_timeout_s: float | None = None, variants: int | None = None,
                   photos: int | None = None) -> float:
    """A call's hard timeout (section 7); KeyError for a tool the table does not name (stage D decides it).
    With photos and variants, facet_variants' worker render deadline: it scales with its renders (photos x
    (variants + 1), the head included), capped at the table value minus FACET_VARIANTS_HOST_MARGIN_S (the table's
    comment)."""
    if tool_name == "execute_blender_code":
        soft = SNIPPET_SOFT_DEFAULT_S if soft_timeout_s is None else float(soft_timeout_s)
        return min(max(soft, SNIPPET_SOFT_MIN_S), SNIPPET_SOFT_MAX_S) + SNIPPET_HARD_MARGIN_S
    if tool_name == "facet_variants" and variants is not None and photos is not None:
        scaled = FACET_VARIANTS_BASE_S + FACET_VARIANTS_PER_RENDER_S * int(photos) * (int(variants) + 1)
        return float(min(scaled, HARD_TIMEOUT_S[tool_name] - FACET_VARIANTS_HOST_MARGIN_S))
    return float(HARD_TIMEOUT_S[tool_name])


def sdk_timeout_s(tool_name: str) -> float:
    """The function_tool ``timeout`` for a registered tool: its largest hard timeout + 10 s."""
    if tool_name == "execute_blender_code":
        return SNIPPET_SOFT_MAX_S + SNIPPET_HARD_MARGIN_S + SDK_TIMEOUT_MARGIN_S
    return hard_timeout_s(tool_name) + SDK_TIMEOUT_MARGIN_S


REMOVED_PHRASES = ("supplementary", "unverified", "hypothes")     # section 14 criterion 5; WP7 extends the list
MIN_FREE_BYTES = 1 << 30            # checkpoints are ~2.4 MB each, up to ~1 GB per run with packed 4K prints


@dataclass(frozen=True)
class ToolContract:
    """The two-way contract of section 7 (WP7 supplies it): every tool the instructions name is registered, every
    registered tool is named and described, gated tools are neither, and no removed phrase appears anywhere Astra
    reads (instructions, brief, tool descriptions)."""

    named: frozenset
    gated: frozenset = frozenset()
    removed_phrases: tuple = REMOVED_PHRASES


def contract_problems(contract: ToolContract, *, instructions: str, brief: str, tools: Sequence[Any]) -> list[str]:
    registered = {tool.name for tool in tools}
    named = set(contract.named)
    problems = [f"the instructions name `{name}`, which is not registered" for name in sorted(named - registered)]
    problems += [f"`{name}` is registered but the instructions do not name it" for name in sorted(registered - named)]
    problems += [f"`{name}` is listed as named but the instructions never mention it" for name in sorted(named)
                 if not re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", instructions)]
    problems += [f"gated tool `{name}` is registered or named" for name in sorted(set(contract.gated)
                                                                                  & (registered | named))]
    problems += [f"`{tool.name}` has no description" for tool in tools if not (tool.description or "").strip()]
    texts = [("the instructions", instructions), ("the brief", brief)] + [
        (f"the description of `{tool.name}`", tool.description or "") for tool in tools]
    for tool in tools:
        texts += [(f"a parameter description of `{tool.name}` ({path})", text)
                  for path, text in schema_descriptions(getattr(tool, "params_json_schema", None))]
    for phrase in contract.removed_phrases:
        problems += [f"removed phrase {phrase!r} appears in {where}" for where, text in texts
                     if phrase.lower() in text.lower()]
    return problems


def schema_descriptions(schema, path: str = "$") -> list[tuple[str, str]]:
    """Every ``description`` string inside a JSON schema (properties, $defs, anyOf members, items), with its path."""
    out = []
    if isinstance(schema, dict):
        if isinstance(schema.get("description"), str):
            out.append((path, schema["description"]))
        for key, value in schema.items():
            if key != "description":
                out += schema_descriptions(value, f"{path}.{key}")
    elif isinstance(schema, list):
        for i, value in enumerate(schema):
            out += schema_descriptions(value, f"{path}[{i}]")
    return out


@dataclass(frozen=True)
class RunSettings:
    """Engineering settings recorded per run (WP2_DESIGN section 1: output cap 16,384, max_turns 60).

    The facet runs (WP6_FACETS §4.3) add: ``tool_choice`` "required" (every reply calls a tool), ``reset_tool_choice``
    False (the SDK keeps that choice after the first tool call), ``finish_tool`` (the run is final only when this
    tool's result has status "accepted"), ``finish_k`` (the finish tool's K distinct compared heads) and
    ``context_guard_tokens`` (the one-epoch image filter threshold). Their defaults change nothing."""

    effort: str | None = None  # paid runs require the owner's explicit high/max choice
    max_output_tokens: int = 16_384
    max_turns: int | None = 60
    max_continuations: int = 2
    fit_output_to_budget: bool = False
    tool_choice: str = "auto"
    reset_tool_choice: bool = True
    finish_tool: str | None = None
    finish_k: int = 4
    context_guard_tokens: int | None = None

    def behavior(self) -> dict:
        """The non-default agent behaviour, bound into the approval settings and the tool-registry sha ({} when
        every new setting has its default)."""
        out: dict = {}
        if self.tool_choice != "auto":
            out["tool_choice"] = self.tool_choice
        if self.reset_tool_choice is not True:
            out["reset_tool_choice"] = self.reset_tool_choice
        if self.finish_tool is not None:
            out["finish"] = {"tool": self.finish_tool, "behavior": FINISH_BEHAVIOR, "k": self.finish_k}
        if self.context_guard_tokens is not None:
            out["context_guard"] = {"input_tokens": self.context_guard_tokens, "filter": CONTEXT_GUARD}
        return out

    def approval_settings(self) -> dict:
        settings = {"effort": self.effort, "max_output_tokens": self.max_output_tokens, "max_turns": self.max_turns}
        if self.fit_output_to_budget:
            settings["fit_output_to_budget"] = True
        settings.update(self.behavior())
        return settings


@dataclass
class RunSpec:
    """One paid run of one job. ``brief``, ``photos`` and ``owner_notes`` come from the job (stage E composes them);
    ``tools`` from the registry (stage D). ``offline_handler`` is the MockTransport handler of the $0 proofs."""

    job_dir: Path
    run_id: str
    instructions: str
    tools: Sequence[Any]
    brief: str
    current_request_sha256: str
    photos: Sequence[ReferenceInput] = ()
    owner_notes: Sequence[str] = ()
    settings: RunSettings = field(default_factory=RunSettings)
    requested_cap_usd: Any = None
    resume: bool = False
    scene_note: str = "The scene is empty."
    env_file: Path | None = None
    offline_handler: Callable | None = None
    offline_api_key: str | None = None
    heartbeat_interval_s: float = HEARTBEAT_INTERVAL_S
    status_line_after_s: float = STATUS_LINE_AFTER_S
    echo: bool = False
    contract: ToolContract | None = None
    min_free_bytes: int = MIN_FREE_BYTES


class SceneHooks:
    """The live scene behind a run (stage A's BlenderSession, wired by stage D/E). The defaults do nothing, so a
    run without Blender (the stage-C tests) has no journal, no rollback and no exit checkpoint.

    ``journal_entries()`` returns the journaled calls in order: ``{"call_id", "tool", "mutating": bool,
    "checkpoint_before"}`` per completed call, and ``{"kind": "rollback", ...}`` markers for rollbacks already
    applied. ``restore(checkpoint_id, reason=...)`` restores and verifies that checkpoint, records the rollback marker
    and returns ``{"checkpoint": <head id>}``. ``exit_checkpoint()`` re-verifies the head's sha and returns
    ``{"id", "sha256", "verified"}``; ``close()`` shuts the worker down (always after the exit checkpoint)."""

    def start(self) -> dict | None:
        return None

    def journal_entries(self) -> list[dict]:
        return []

    def restore(self, checkpoint_id: str, *, reason: str) -> dict:
        raise NotImplementedError("this scene cannot restore checkpoints")

    def exit_checkpoint(self) -> dict | None:
        return None

    def worker_info(self) -> dict:
        return {}

    def context_stubs(self, image_sha256s: list[str]) -> dict[str, str]:
        """The context guard's one choice: {image sha256: text stub} for the tool-output images (sha256 of the
        image bytes, in input order) that may be replaced; {} keeps every image."""
        return {}

    def close(self) -> None:
        return None


# --------------------------------------------------------------------------- the SDK construction (section 7)


def build_model_settings(settings: RunSettings) -> ModelSettings:
    return ModelSettings(parallel_tool_calls=False, max_tokens=settings.max_output_tokens,
                         retry=ModelRetrySettings(max_retries=0), extra_body={"service_tier": "default"},
                         reasoning=Reasoning(effort=settings.effort, summary="auto"),
                         tool_choice=None if settings.tool_choice == "auto" else settings.tool_choice)


def build_agent(instructions: str, tools: Sequence[Any], settings: RunSettings) -> Agent:
    extra: dict = {}
    if settings.finish_tool is not None:
        extra["tool_use_behavior"] = FinishOnAccepted(settings.finish_tool)
    if settings.reset_tool_choice is not True:
        extra["reset_tool_choice"] = settings.reset_tool_choice
    return Agent(name=AGENT_NAME, model=MODEL, instructions=instructions, tools=list(tools),
                 model_settings=build_model_settings(settings), **extra)


def build_run_config(client, *, input_filter: Callable | None = None) -> RunConfig:
    extra = {"call_model_input_filter": input_filter} if input_filter is not None else {}
    return RunConfig(tracing_disabled=True, trace_include_sensitive_data=False,
                     tool_execution=ToolExecutionConfig(max_function_tool_concurrency=1),
                     model_provider=OpenAIProvider(openai_client=client, use_responses=True,
                                                   use_responses_websocket=False), **extra)


def tool_registry_sha256(tools: Sequence[Any], settings: RunSettings | None = None) -> str:
    """sha256 of the registry Astra sees: every tool's name, description, parameter schema and strictness, plus the
    run's non-default agent behaviour (``RunSettings.behavior``: tool choice, finish gate, context guard) when there
    is one, so the registry sha of a default run is unchanged."""
    rows = sorted(({"name": tool.name, "description": getattr(tool, "description", ""),
                    "parameters": getattr(tool, "params_json_schema", None),
                    "strict": getattr(tool, "strict_json_schema", None)} for tool in tools),
                  key=lambda row: row["name"])
    behavior = settings.behavior() if settings is not None else {}
    return hashlib.sha256(canonical_json({"tools": rows, "behavior": behavior} if behavior else rows)).hexdigest()


def tool_result_text(output) -> str:
    """The text of a tool's return value (a str, a ToolOutputText, or a list of text/image parts)."""
    if isinstance(output, str):
        return output
    parts = output if isinstance(output, (list, tuple)) else [output]
    texts = []
    for part in parts:
        text = part.get("text") if isinstance(part, dict) else getattr(part, "text", None)
        if isinstance(text, str):
            texts.append(text)
    return "\n".join(texts)


def tool_result_status(output) -> str | None:
    """The "status" of a tool result whose text starts with a JSON object (the facet tools' format), else None."""
    text = tool_result_text(output).lstrip()
    try:
        value, _end = json.JSONDecoder().raw_decode(text)
    except ValueError:
        return None
    return value.get("status") if isinstance(value, dict) else None


class FinishOnAccepted:
    """The facet runs' ``tool_use_behavior`` (a ToolsToFinalOutputFunction, WP6_FACETS §4.3): the run is final only
    when ``tool``'s result has status "accepted" (its text is the final output). A refused finish goes back to Astra
    like any other result. ``accepted`` lists the accepted results of this run."""

    name = FINISH_BEHAVIOR

    def __init__(self, tool: str):
        self.tool = tool
        self.accepted: list[str] = []

    def __call__(self, context, results) -> ToolsToFinalOutputResult:
        for result in results:
            if getattr(result.tool, "name", None) == self.tool and tool_result_status(result.output) == "accepted":
                text = tool_result_text(result.output)
                self.accepted.append(text)
                return ToolsToFinalOutputResult(is_final_output=True, final_output=text)
        return ToolsToFinalOutputResult(is_final_output=False, final_output=None)


_DATA_IMAGE_B64 = re.compile(r"^data:image/[A-Za-z0-9.+-]+;base64,(.*)$", re.DOTALL)


def _image_sha256(url) -> str | None:
    match = _DATA_IMAGE_B64.match(url) if isinstance(url, str) else None
    if match is None:
        return None
    try:
        return hashlib.sha256(base64.b64decode(match[1], validate=True)).hexdigest()
    except ValueError:
        return None


def tool_output_images(items) -> list[str]:
    """sha256 of every data-URL image inside a function_call_output of the model input, in order."""
    out = []
    for item in items or []:
        if isinstance(item, dict) and item.get("type") == "function_call_output" and isinstance(item.get("output"),
                                                                                                 list):
            for part in item["output"]:
                if isinstance(part, dict) and part.get("type") == "input_image":
                    sha = _image_sha256(part.get("image_url"))
                    if sha is not None:
                        out.append(sha)
    return out


class ContextGuard:
    """``RunConfig.call_model_input_filter`` (WP6_FACETS §4.3): once the last counted request reached ``threshold``
    input tokens, ask ``choose`` ONCE which tool-output images to replace with text stubs ({sha256: stub}), then apply
    that same choice to this and every later request of the run (one prompt-cache rewrite, then a stable prefix).
    Brief images and images ``choose`` does not name are never touched; the session keeps every image."""

    name = CONTEXT_GUARD

    def __init__(self, threshold: int, *, counted: Callable[[], int | None], choose: Callable[[list], dict],
                 notify: Callable[[dict], None] | None = None):
        self.threshold = int(threshold)
        self.counted, self.choose, self.notify = counted, choose, notify
        self.stubs: dict | None = None
        self.epoch: dict | None = None
        self.replaced = 0

    def __call__(self, data) -> ModelInputData:
        model_data = data.model_data
        if self.stubs is None:
            tokens = self.counted()
            if tokens is None or tokens < self.threshold:
                return model_data
            shas = tool_output_images(model_data.input)
            present = set(shas)
            self.stubs = {sha: str(text) for sha, text in (self.choose(shas) or {}).items() if sha in present}
            self.epoch = {"at_counted_input_tokens": tokens, "tool_images": len(shas), "stubbed": len(self.stubs)}
            if self.notify is not None:
                self.notify(dict(self.epoch))
        if not self.stubs:
            return model_data
        items, replaced = [], 0
        for item in model_data.input:
            if isinstance(item, dict) and item.get("type") == "function_call_output" and isinstance(
                    item.get("output"), list):
                parts = []
                for part in item["output"]:
                    sha = _image_sha256(part.get("image_url")) if isinstance(part, dict) and \
                        part.get("type") == "input_image" else None
                    if sha in self.stubs:
                        parts.append({"type": "input_text", "text": self.stubs[sha]})
                        replaced += 1
                    else:
                        parts.append(part)
                item = {**item, "output": parts}
            items.append(item)
        self.replaced = replaced
        return ModelInputData(input=items, instructions=model_data.instructions)


class RunStatus:
    """The SDK run context the tools receive (``ctx.context``): settled money, the cap and the last counted input
    for the footer every facet tool result ends with (WP6_FACETS §4.5)."""

    def __init__(self, guard: BudgetGuard | None = None):
        self.guard = guard

    def numbers(self) -> dict:
        if self.guard is None:
            return {"settled_usd": None, "cap_usd": None, "context_tokens": None}
        summary = self.guard.summary()
        return {"settled_usd": summary["settled_micro_usd"] / 1e6, "cap_usd": summary["maximum_micro_usd"] / 1e6,
                "context_tokens": summary["planning"]["last_counted_input_tokens"]}

    def footer(self) -> str:
        return footer_text(**self.numbers())


def footer_text(settled_usd=None, cap_usd=None, context_tokens=None) -> str:
    def usd(value):
        return "n/a" if value is None else f"${value:.2f}"
    return (f"settled {usd(settled_usd)} / cap {usd(cap_usd)} · context "
            f"{'n/a' if context_tokens is None else context_tokens} tokens")


def instructions_sha256(instructions: str) -> str:
    return hashlib.sha256(instructions.encode("utf-8")).hexdigest()


def fatal_tool_errors(*fatal: type[BaseException]):
    """A function_tool ``failure_error_function``: the SDK default message for ordinary errors, a re-raise for the
    given types (``WorkerFailed`` must end the run ``failed_worker`` instead of reaching Astra as text)."""
    def failure(context, error):
        if isinstance(error, fatal):
            raise error
        return default_tool_error_function(context, error)
    return failure


# --------------------------------------------------------------------------- clients (the seam)


def load_api_key(env_file=None) -> str | None:
    """The OpenAI key from an explicit dotenv file, else the environment; never printed or logged."""
    value = None
    if env_file is not None:
        from dotenv import dotenv_values
        value = dotenv_values(env_file).get("OPENAI_API_KEY")
    value = value or os.environ.get("OPENAI_API_KEY")
    return value.strip() if isinstance(value, str) and value.strip() else None


def production_client_options(*, count: bool) -> dict:
    """What a production client is built with (kept pure so tests check it without constructing one)."""
    return {"base_url": BASE_URL, "max_retries": 0, "timeout": COUNT_TIMEOUT_S if count else INFERENCE_TIMEOUT_S,
            "http_client": {"follow_redirects": False}}


class OfflineClients:
    """The production classes over httpx2.MockTransport (offline.py); refused unless G3D_OFFLINE=1 and an
    sk-offline- key."""

    kind = "offline"

    def __init__(self, handler: Callable, *, api_key: str):
        require_offline(api_key)
        if not callable(handler):
            raise RunRefused("the offline transport handler is not callable")
        self.handler, self.api_key = handler, api_key

    @property
    def secrets(self) -> list[str]:
        return [self.api_key]

    def counting_client(self):
        return offline_counting_client(self.handler, api_key=self.api_key, timeout=COUNT_TIMEOUT_S)

    def inference_client(self, event_hooks: dict):
        return offline_inference_client(self.handler, api_key=self.api_key, event_hooks=event_hooks,
                                        timeout=INFERENCE_TIMEOUT_S)


class ProductionClients:
    """AsyncOpenAI at the global endpoint, retries off, redirects off; built only after the approval is consumed."""

    kind = "production"

    def __init__(self, *, api_key: str):
        if os.environ.get(OFFLINE_ENV) == "1":
            raise RunRefused(f"production clients are refused while {OFFLINE_ENV}=1")
        if not isinstance(api_key, str) or not api_key.strip() or api_key.startswith(OFFLINE_KEY_PREFIX):
            raise RunRefused("no usable OpenAI API key")
        self.api_key = api_key

    @property
    def secrets(self) -> list[str]:
        return [self.api_key]

    def counting_client(self):
        from openai import AsyncOpenAI, DefaultAsyncHttpxClient
        options = production_client_options(count=True)
        return AsyncOpenAI(api_key=self.api_key, base_url=options["base_url"], max_retries=options["max_retries"],
                           timeout=options["timeout"],
                           http_client=DefaultAsyncHttpxClient(**options["http_client"]))

    def inference_client(self, event_hooks: dict):
        from openai import AsyncOpenAI, DefaultAsyncHttpxClient
        options = production_client_options(count=False)
        return AsyncOpenAI(api_key=self.api_key, base_url=options["base_url"], max_retries=options["max_retries"],
                           timeout=options["timeout"],
                           http_client=DefaultAsyncHttpxClient(event_hooks=event_hooks, **options["http_client"]))


def model_clients(run: RunSpec):
    """Production or offline: decided ($0, nothing built) from G3D_OFFLINE and the run's offline handler."""
    if os.environ.get(OFFLINE_ENV) == "1":
        if run.offline_handler is None:
            raise RunRefused(f"{OFFLINE_ENV}=1 needs an offline transport handler (agent.replay)")
        try:
            return OfflineClients(run.offline_handler, api_key=run.offline_api_key)
        except Exception as error:     # OfflineRefused: the key is not an offline key
            raise RunRefused(str(error)) from None
    if run.offline_handler is not None:
        raise RunRefused(f"an offline transport was supplied without {OFFLINE_ENV}=1")
    key = load_api_key(run.env_file)
    if key is None:
        raise RunRefused("set OPENAI_API_KEY or pass an explicit dotenv file (env_file)")
    return ProductionClients(api_key=key)


# --------------------------------------------------------------------------- the session (no image bytes on disk)


_BLOB_REF = re.compile(r"^g3d-blob:v1:(image/[a-z0-9.+-]+):([0-9a-f]{64})$")
_TEXT_REF = re.compile(r"^g3d-text:v1:([0-9a-f]{64})$")
_DATA_IMAGE = re.compile(r"^data:image/[A-Za-z0-9.+-]+;base64,")


class SessionBlobMissing(RuntimeError):
    """A stored session item names an image blob that is missing or does not hash to its name."""


class BlobSession(SQLiteSession):
    """``SQLiteSession("astra", job/session/session.sqlite)`` whose stored rows carry no image bytes: a canonical
    data-URL image is stored once in the job's ``blobs/`` and replaced by ``g3d-blob:v1:<mime>:<sha256>`` on write,
    and restored byte for byte on read (WP2_DESIGN section 14 criterion 6: no ``data:image`` under the job dir).
    The SDK reads and writes sessions only through get_items/add_items/pop_item (session_persistence.py:197-259)."""

    def __init__(self, session_id: str, db_path, *, blobs: BlobStore):
        super().__init__(session_id, db_path)
        self.blobs = blobs

    def _dehydrate(self, value):
        if isinstance(value, str):
            if _DATA_IMAGE.match(value):
                stored = self.blobs.put_data_url(value)
                if stored is not None:
                    return f"g3d-blob:v1:{stored['mime']}:{stored['sha256']}"
            if "data:image" in value.lower() or _TEXT_REF.fullmatch(value) or _BLOB_REF.fullmatch(value):
                stored = self.blobs.put(zlib.compress(value.encode("utf-8")), "application/x-g3d-text-zlib")
                return f"g3d-text:v1:{stored['sha256']}"
            return value
        if isinstance(value, list):
            return [self._dehydrate(item) for item in value]
        if isinstance(value, dict):
            return {key: self._dehydrate(item) for key, item in value.items()}
        return value

    def _rehydrate(self, value):
        if isinstance(value, str):
            text = _TEXT_REF.fullmatch(value)
            if text is not None:
                try:
                    return zlib.decompress(self.blobs.get(text[1], "application/x-g3d-text-zlib")).decode("utf-8")
                except (OSError, ValueError, zlib.error) as error:
                    raise SessionBlobMissing(f"session text blob {text[1][:12]} is unreadable ({type(error).__name__})")
            match = _BLOB_REF.match(value)
            if match is None:
                return value
            mime, sha256 = match[1], match[2]
            try:
                payload = self.blobs.get(sha256, mime)
            except (OSError, ValueError) as error:
                raise SessionBlobMissing(f"session image blob {sha256[:12]} is unreadable ({type(error).__name__})")
            return f"data:{mime};base64,{base64.b64encode(payload).decode('ascii')}"
        if isinstance(value, list):
            return [self._rehydrate(item) for item in value]
        if isinstance(value, dict):
            return {key: self._rehydrate(item) for key, item in value.items()}
        return value

    async def add_items(self, items) -> None:
        await super().add_items([self._dehydrate(copy.deepcopy(item)) for item in items])

    async def get_items(self, limit: int | None = None):
        return [self._rehydrate(item) for item in await super().get_items(limit)]

    async def pop_item(self):
        item = await super().pop_item()
        return None if item is None else self._rehydrate(item)


# --------------------------------------------------------------------------- resume planning


def _items(items: Iterable[Any], kind: str) -> list[dict]:
    return [item for item in items if isinstance(item, dict) and item.get("type") == kind]


def dangling_calls(items: Sequence[Any]) -> list[str]:
    """Call ids of function_call items without a function_call_output (the Responses API rejects them)."""
    outputs = {item.get("call_id") for item in _items(items, "function_call_output")}
    return [item.get("call_id") for item in _items(items, "function_call") if item.get("call_id") not in outputs]


async def repair_dangling_calls(session, items: Sequence[Any]) -> list[str]:
    """Backstop: append an "interrupted" output for every dangling call (0.22.3 never leaves one; tests pin it)."""
    missing = dangling_calls(items)
    if missing:
        await session.add_items([{"type": "function_call_output", "call_id": call_id, "output": DANGLING_OUTPUT}
                                 for call_id in missing])
    return missing


def plan_rollback(items: Sequence[Any], entries: Sequence[dict]) -> dict | None:
    """The rollback rule: let L be the call id of the last function_call_output in the session; a journaled mutating
    call after L (after the last journal entry the session records, and after the last rollback already applied) lost
    its turn, so the scene must return to the checkpoint_before of the first such call."""
    outputs = [item.get("call_id") for item in _items(items, "function_call_output")]
    recorded = set(outputs)
    boundary = -1
    for index, entry in enumerate(entries):
        if entry.get("kind") == "rollback" or entry.get("call_id") in recorded:
            boundary = index
    lost = [entry for entry in entries[boundary + 1:]
            if entry.get("kind", "call") == "call" and entry.get("mutating")]
    if not lost:
        return None
    first = lost[0]
    if not first.get("checkpoint_before"):
        raise RunRefused(f"journaled call {first.get('call_id')} has no checkpoint_before to roll back to")
    return {"call_id": first.get("call_id"), "tool": first.get("tool"),
            "checkpoint_before": first["checkpoint_before"], "lost_calls": [entry.get("call_id") for entry in lost],
            "last_output_call_id": outputs[-1] if outputs else None}


def rollback_sentence(rollback: dict) -> str:
    return (f"Your previous run ended during `{rollback['tool']}` (call `{rollback['call_id']}`); its effect was "
            f"rolled back; the scene is checkpoint {rollback['scene_checkpoint']}.")


def invocation_text(*, run_id: str, scene_note: str, cap_micro: int, rollback: dict | None) -> str:
    """Facts only (RULES_AUDIT: no steering prose): the run, the scene, this run's cap, a rollback if one happened."""
    lines = [f"Run {run_id}.", scene_note.strip(), f"This run's budget cap: ${usd_text(cap_micro)}."]
    if rollback is not None:
        lines.append(rollback_sentence(rollback))
    return "\n".join(line for line in lines if line)


# --------------------------------------------------------------------------- heartbeat


def _iso(epoch: float | None = None) -> str:
    moment = datetime.fromtimestamp(epoch, timezone.utc) if epoch is not None else datetime.now(timezone.utc)
    return moment.isoformat(timespec="milliseconds")


class Heartbeat:
    """A daemon thread writing ``heartbeat.json`` every ``interval_s`` (2 s): {state: model_wait|tool:<name>|idle,
    since, request_seq, tool_call_id, worker_pid, worker_cpu_s, context_tokens, settled_usd, reserved_usd}. A state
    held for ``report_after_s`` (30 s) prints one status line, repeated every ``report_every_s``. A failed write never
    stops the run (it is counted)."""

    def __init__(self, path, *, run_id: str, interval_s: float = HEARTBEAT_INTERVAL_S,
                 report_after_s: float = STATUS_LINE_AFTER_S, report_every_s: float | None = None,
                 worker_info: Callable[[], dict] | None = None, printer: Callable[[str], None] = print,
                 clock: Callable[[], float] = time.monotonic):
        self.path = Path(path)
        self.run_id = run_id
        self.interval_s = float(interval_s)
        self.report_after_s = float(report_after_s)
        self.report_every_s = float(report_every_s if report_every_s is not None else report_after_s)
        self.worker_info = worker_info
        self.printer = printer
        self.clock = clock
        self._lock = threading.Lock()
        self._state = "starting"
        self._since = clock()
        self._since_wall = time.time()
        self._fields: dict = {"request_seq": None, "tool_call_id": None, "context_tokens": None,
                              "settled_usd": None, "reserved_usd": None}
        self._last_report: float | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._beat_lock = threading.Lock()          # the thread and start/stop never write the file together
        self.beats = 0
        self.write_failures = 0
        self.lines: list[str] = []
        self.states: list[str] = []
        self.beat_times: list[float] = []          # monotonic times of the last 2,000 writes
        self._max_gap: float | None = None          # the largest gap between two writes over the whole run

    def set_state(self, state: str, **fields) -> None:
        with self._lock:
            if state != self._state:
                self._state, self._since, self._since_wall = state, self.clock(), time.time()
                self._last_report = None
                self.states.append(state)
            self._fields.update(fields)

    def update(self, **fields) -> None:
        with self._lock:
            self._fields.update(fields)

    def snapshot(self) -> dict:
        with self._lock:
            state, since, since_wall, fields = self._state, self._since, self._since_wall, dict(self._fields)
        worker = {}
        if self.worker_info is not None:
            try:
                worker = dict(self.worker_info() or {})
            except Exception:  # noqa: BLE001 - a heartbeat never fails the run
                worker = {}
        return {"schema": HEARTBEAT_SCHEMA, "run_id": self.run_id, "pid": os.getpid(), "state": state,
                "since": _iso(since_wall), "seconds_in_state": round(self.clock() - since, 3), "updated": _iso(),
                "beats": self.beats, "request_seq": fields.get("request_seq"),
                "tool_call_id": fields.get("tool_call_id"), "worker_pid": worker.get("worker_pid"),
                "worker_cpu_s": worker.get("worker_cpu_s"), "context_tokens": fields.get("context_tokens"),
                "settled_usd": fields.get("settled_usd"), "reserved_usd": fields.get("reserved_usd"),
                "final_status": fields.get("final_status")}

    def beat(self) -> dict:
        with self._beat_lock:
            record = self.snapshot()
            self.beats += 1
            record["beats"] = self.beats
            try:
                write_json(self.path, record)
            except OSError:
                self.write_failures += 1
            now = self.clock()
            if self.beat_times:
                gap = now - self.beat_times[-1]
                self._max_gap = gap if self._max_gap is None else max(self._max_gap, gap)
            self.beat_times.append(now)
            del self.beat_times[:-2000]
            held = record["seconds_in_state"]
            if record["state"] != "finished" and held >= self.report_after_s:
                now = self.clock()
                if self._last_report is None or now - self._last_report >= self.report_every_s:
                    self._last_report = now
                    line = (f"[astra {self.run_id}] {record['state']} for {int(held)} s"
                            + (f" (request {record['request_seq']})" if record["request_seq"] else ""))
                    self.lines.append(line)
                    try:
                        self.printer(line)
                    except Exception:  # noqa: BLE001
                        pass
            return record

    def max_gap_s(self) -> float | None:
        return None if self._max_gap is None else round(self._max_gap, 4)

    def _run(self) -> None:
        while not self._stop.wait(self.interval_s):
            self.beat()

    def start(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.beat()
        self._thread = threading.Thread(target=self._run, name="g3d-heartbeat", daemon=True)
        self._thread.start()

    def stop(self, final_state: str = "finished") -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(5.0, 3 * self.interval_s))
        self.set_state("finished", final_status=final_state)
        self.update(tool_call_id=None)
        self.beat()


# --------------------------------------------------------------------------- statuses


def _chain(error: BaseException | None):
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        yield error
        error = error.__cause__ or error.__context__


def is_worker_failure(error: BaseException) -> bool:
    """Stage A's journal.WorkerFailed anywhere in the chain (by name: the loop does not import stage A)."""
    return any(type(item).__name__ == "WorkerFailed" or getattr(item, "failed_worker", False) is True
               for item in _chain(error))


def is_transport_failure(error: BaseException) -> bool:
    """A connection error or timeout: no response hook ran for the request in flight."""
    return isinstance(error, APIConnectionError)


def _was_incomplete(log: ReviewLog, guard: BudgetGuard) -> bool:
    last = log.last_response
    if not last or last.get("http_status") != 200 or last.get("status") != "incomplete":
        return False
    rows = guard.ledger.rows
    return bool(rows) and rows[-1]["outcome"] == "settled" and rows[-1]["response_id"] == last.get("response_id")


def classify_failure(error: BaseException, *, log: ReviewLog | None, guard: BudgetGuard | None) -> str:
    if (log is not None and log.stop_observed) or any(isinstance(item, StopRequested) for item in _chain(error)) \
            or isinstance(error, (KeyboardInterrupt, asyncio.CancelledError)):
        return "stopped"
    if guard is not None and guard.blocked_kind == "BudgetExceeded":
        return "budget_limit"
    if isinstance(error, MaxTurnsExceeded):
        return "turn_limit"
    hint = guard.status_hint() if guard is not None else None
    if hint in ("provider_unknown", "provider_rejected"):
        return hint
    if is_worker_failure(error):
        return "failed_worker"
    return "failed"


# --------------------------------------------------------------------------- the run


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _versions() -> dict:
    out = {}
    for name in ("openai", "openai-agents", "httpx2", "httpcore2"):
        try:
            out[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            out[name] = None
    return out


def _validate_spec(spec: RunSpec) -> None:
    settings = spec.settings
    if settings.effort not in EFFORTS:
        raise RunRefused(f"effort must be one of {', '.join(EFFORTS)}")
    for name in ("max_output_tokens", "max_turns"):
        value = getattr(settings, name)
        if name == "max_turns" and value is None:
            continue
        if type(value) is not int or value < 1:
            raise RunRefused(f"{name} must be a positive integer")
    if type(settings.max_continuations) is not int or settings.max_continuations < 0:
        raise RunRefused("max_continuations must be a non-negative integer")
    if type(settings.fit_output_to_budget) is not bool:
        raise RunRefused("fit_output_to_budget must be a boolean")
    if settings.tool_choice not in TOOL_CHOICES:
        raise RunRefused(f"tool_choice must be one of {', '.join(TOOL_CHOICES)}")
    if type(settings.reset_tool_choice) is not bool:
        raise RunRefused("reset_tool_choice must be a boolean")
    if type(settings.finish_k) is not int or settings.finish_k < 1:
        raise RunRefused("finish_k must be a positive integer")
    if settings.context_guard_tokens is not None and (type(settings.context_guard_tokens) is not int
                                                      or settings.context_guard_tokens < 1):
        raise RunRefused("context_guard_tokens must be a positive integer or None")
    if not isinstance(spec.instructions, str) or not spec.instructions.strip():
        raise RunRefused("the instructions are empty")
    if not isinstance(spec.brief, str) or not spec.brief.strip():
        raise RunRefused("the brief is empty")
    names = [getattr(tool, "name", None) for tool in spec.tools]
    if any(not isinstance(tool, FunctionTool) for tool in spec.tools):
        raise RunRefused("only local function tools are covered by the budget")
    if len(set(names)) != len(names):
        raise RunRefused("tool names must be unique")
    if settings.finish_tool is not None and settings.finish_tool not in names:
        raise RunRefused(f"the finish tool `{settings.finish_tool}` is not registered")


def _check_bound_request(run_dir: Path, spec: RunSpec) -> dict:
    """Recompute from current job bytes and the exact payload; never trust the caller's cached sha."""
    try:
        document = json.loads((run_dir / "approval-request.json").read_text(encoding="utf-8"))
        request = document["request"]
    except (OSError, ValueError, KeyError, TypeError):
        raise ApprovalRefused("invalid", "approval-request.json is unreadable") from None
    if request_sha256(request) != spec.current_request_sha256:
        raise ApprovalRefused("stale", "the run's request sha differs from the approved request")
    current = build_request(spec.job_dir, instructions_sha256=instructions_sha256(spec.instructions),
                            tool_registry_sha256=tool_registry_sha256(spec.tools, spec.settings),
                            settings=spec.settings.approval_settings(), unsent_owner_notes=spec.owner_notes,
                            run_inputs=run_input_binding(spec.job_dir, spec.brief, spec.photos))
    if request_sha256(current) != request_sha256(request):
        raise ApprovalRefused("stale", "job inputs, brief, photos, owner notes, instructions, tool registry or settings "
                              "changed; estimate and approve again")
    return current


async def _drive(agent: Agent, first_input: list[dict], *, session, log: ReviewLog, guard: BudgetGuard,
                 run_config: RunConfig, settings: RunSettings, state: dict, context: Any = None) -> tuple[str, Any]:
    """Runner.run, then up to ``max_continuations`` continuation runs after an incomplete response; with a finish
    tool, up to ``max_continuations`` host notes after a reply that ended without the accepted finish."""
    turns_left = settings.max_turns
    run_input: list[dict] = [{"role": "user", "content": first_input}]
    finisher = agent.tool_use_behavior if isinstance(agent.tool_use_behavior, FinishOnAccepted) else None
    while True:
        started = log.interactions
        try:
            result = await Runner.run(agent, run_input, max_turns=turns_left, session=session, hooks=log,
                                      run_config=run_config, context=context)
        except ModelBehaviorError:
            used = log.interactions - started
            state["turns_used"] += used
            if turns_left is not None:
                turns_left -= used
            if not _was_incomplete(log, guard) or log.stop_observed:
                raise
            sequence = log.last_response.get("sequence")
            if turns_left is not None and turns_left <= 0:
                log.note(f"Response to request {sequence} was cut off at the output limit and no turn is left.")
                return "turn_limit", None
            if state["continuations"] >= settings.max_continuations:
                log.note(f"Response to request {sequence} was cut off at the output limit again; "
                         f"{settings.max_continuations} continuations were used.")
                return "output_limit", None
            state["continuations"] += 1
            log.note(f"Response to request {sequence} was cut off at the output limit (settled, not recorded in "
                     f"the session); continuation {state['continuations']} of {settings.max_continuations}.",
                     continuation=state["continuations"])
            run_input = [{"role": "user", "content": CONTINUATION_NOTE}]
            continue
        used = log.interactions - started
        state["turns_used"] += used
        if finisher is None or finisher.accepted:
            return "completed", result.final_output
        # a text reply without the accepted finish (the fallback when required tool choice is not in force)
        if turns_left is not None:
            turns_left -= used
        if state["finish_notes"] >= settings.max_continuations:
            log.note(f"The reply ended without an accepted `{finisher.tool}`; {settings.max_continuations} host "
                     "notes were used, so the run ends.")
            return "ended_without_finish", result.final_output
        if turns_left is not None and turns_left <= 0:
            log.note(f"The reply ended without an accepted `{finisher.tool}` and no turn is left.")
            return "turn_limit", result.final_output
        state["finish_notes"] += 1
        log.note(f"The reply ended without an accepted `{finisher.tool}`; host note {state['finish_notes']} of "
                 f"{settings.max_continuations}.", finish_note=state["finish_notes"])
        run_input = [{"role": "user", "content": FINISH_NOTE.format(tool=finisher.tool)}]


async def run_astra(spec: RunSpec, *, scene: SceneHooks | None = None, clients=None) -> dict:
    """One run of the section-7 loop; returns the result (also written to runs/<run>/result.json).

    Raises ``ApprovalRefused`` (exit 3) or ``RunRefused`` (exit 2) before anything is consumed or sent."""
    scene = scene or SceneHooks()
    _validate_spec(spec)
    job_dir = Path(spec.job_dir).resolve()
    run_dir = job_dir / "runs" / spec.run_id
    session_path = job_dir / "session" / "session.sqlite"
    stop_file = job_dir / STOP_FILE
    blobs = BlobStore(job_dir / "blobs")
    started = _now()
    started_clock = time.perf_counter()

    with JobLedger(job_dir) as jobs:
        # (1) $0 preflight: nothing below creates money state or sends anything
        approved = jobs.preflight(spec.run_id, current_request_sha256=spec.current_request_sha256,
                                  requested_cap_usd=spec.requested_cap_usd)
        request = _check_bound_request(jobs.run_dir(spec.run_id), spec)
        if spec.contract is None:
            raise RunRefused("a two-way tool contract is required for every run")
        problems = contract_problems(spec.contract, instructions=spec.instructions, brief=spec.brief,
                                     tools=spec.tools)
        if problems:
            raise RunRefused("the tool contract does not hold: " + "; ".join(problems))
        if session_path.exists() != bool(spec.resume):
            raise RunRefused("this job already has a session: run with resume" if session_path.exists()
                             else "resume needs this job's session/session.sqlite, which does not exist")
        free = shutil.disk_usage(job_dir).free
        if free < spec.min_free_bytes:
            raise RunRefused(f"only {free // (1 << 20)} MiB free on the job's drive; a run needs "
                             f"{spec.min_free_bytes // (1 << 20)} MiB for checkpoints and receipts")
        clients = clients if clients is not None else model_clients(spec)
        cleaner = Cleaner(blobs, clients.secrets)
        stale_stop = stop_file.exists()
        if stale_stop:
            unlink_with_retry(stop_file)        # a stop for an earlier run; a stop written from here on is honoured
        try:
            scene_start = scene.start()
        except BaseException:
            scene.close()
            raise

        # (2) resume planning ($0)
        session = None
        repaired: list[str] = []
        rollback = None
        try:
            items: list = []
            if spec.resume:
                session = BlobSession(SESSION_ID, session_path, blobs=blobs)
                items = await session.get_items()
                # plan the rollback on the history as found: a dangling call has no output, so a journaled mutating
                # one counts as lost; only then answer it (after the repair it would look recorded)
                rollback = plan_rollback(items, scene.journal_entries())
            # (3) consume the approval: exclusive budget.json + the machine-wide paid-run lock
            ledger = jobs.open_paid_run(spec.run_id, current_request_sha256=spec.current_request_sha256,
                                        requested_cap_usd=spec.requested_cap_usd)
        except BaseException:
            if session is not None:
                session.close()
            scene.close()
            raise

        heartbeat = Heartbeat(job_dir / "heartbeat.json", run_id=spec.run_id, interval_s=spec.heartbeat_interval_s,
                              report_after_s=spec.status_line_after_s, worker_info=scene.worker_info)
        state = {"turns_used": 0, "continuations": 0, "finish_notes": 0}
        status, final_output, error, pending = "failed", None, None, None
        counter = client = guard = log = context_guard = None
        exit_checkpoint = exit_error = scan = None
        plan = None
        teardown_errors: list[dict] = []
        try:
            implementation = capture_implementation(run_dir)
            if rollback is not None:
                restored = scene.restore(rollback["checkpoint_before"],
                                         reason=f"rollback of {rollback['tool']} call {rollback['call_id']}")
                rollback["scene_checkpoint"] = (restored or {}).get("checkpoint") or rollback["checkpoint_before"]
            if spec.resume:
                repaired = await repair_dangling_calls(session, items)
                if repaired:
                    items = await session.get_items()
            text = invocation_text(run_id=spec.run_id, scene_note=spec.scene_note,
                                   cap_micro=approved.requested_cap_micro, rollback=rollback)
            plan = plan_session_input(items, prompt=spec.brief, photos=spec.photos, invocation_text=text,
                                      owner_notes=spec.owner_notes)
            if session is None:     # a fresh job: the session file is created only once the approval is consumed
                session_path.parent.mkdir(parents=True, exist_ok=True)
                session = BlobSession(SESSION_ID, session_path, blobs=blobs)
            # (4) only now the clients, the guard and the receipts
            counter = clients.counting_client()
            guard = BudgetGuard.attach(counter, ledger, fit_output_to_budget=spec.settings.fit_output_to_budget)
            log = ReviewLog(run_dir, blobs_dir=job_dir / "blobs", guard=guard, secrets=clients.secrets,
                            stop_file=stop_file, heartbeat=heartbeat, run_id=spec.run_id, echo=spec.echo)
            client = clients.inference_client({"request": [log.before_request], "response": [log.after_response]})
            disable_sdk_tracing()
            write_json(run_dir / "run-config.json", {
                "schema": RUN_CONFIG_SCHEMA, "job_id": job_dir.name, "run_id": spec.run_id, "created": _now(),
                "model": MODEL, "agent": AGENT_NAME, "effort": spec.settings.effort, "reasoning_summary": "auto",
                "max_output_tokens": spec.settings.max_output_tokens, "max_turns": spec.settings.max_turns,
                "fit_output_to_budget": spec.settings.fit_output_to_budget,
                "max_continuations": spec.settings.max_continuations, "service_tier": "default",
                "parallel_tool_calls": False, "max_function_tool_concurrency": 1, "model_retries": 0,
                "tracing_disabled": True, "use_responses": True, "use_responses_websocket": False, "client": {
                    "kind": clients.kind, "base_url": BASE_URL, "max_retries": 0,
                    "inference_timeout_s": INFERENCE_TIMEOUT_S, "count_timeout_s": COUNT_TIMEOUT_S,
                    "follow_redirects": False},
                "cap_micro_usd": approved.requested_cap_micro, "cap_usd": usd_text(approved.requested_cap_micro),
                "approval_sha256": approved.approval_sha256, "request_sha256": approved.request_sha256,
                "instructions_sha256": request.get("instructions_sha256"),
                "tool_registry_sha256": request.get("tool_registry_sha256"),
                "tools": [tool.name for tool in spec.tools], "session": "session/session.sqlite",
                "resume": bool(spec.resume), "scene_start": scene_start, "stale_stop_file_removed": stale_stop,
                "versions": _versions(), "implementation": implementation,
                **({"behavior": spec.settings.behavior()} if spec.settings.behavior() else {})})
            write_json(run_dir / "input-receipt.json", plan.receipt)
            log.event("session_started", model=MODEL, effort=spec.settings.effort, resume=bool(spec.resume),
                      max_turns=spec.settings.max_turns, max_output_tokens=spec.settings.max_output_tokens,
                      cap_usd=usd_text(approved.requested_cap_micro), client=clients.kind)
            log.event("session_input_prepared", receipt=plan.receipt, dangling_repaired=repaired)
            if repaired:
                log.note(f"{len(repaired)} unanswered tool call(s) in the session received an 'interrupted' output: "
                         + ", ".join(f"`{call_id}`" for call_id in repaired))
            if rollback is not None:
                log.note(rollback_sentence(rollback), rollback=rollback)
            heartbeat.start()
            agent = build_agent(spec.instructions, spec.tools, spec.settings)
            if spec.settings.context_guard_tokens is not None:
                run_guard = guard
                context_guard = ContextGuard(
                    spec.settings.context_guard_tokens,
                    counted=lambda: run_guard.planning()["last_counted_input_tokens"], choose=scene.context_stubs,
                    notify=lambda epoch: log.note(f"Context guard: {epoch['stubbed']} of {epoch['tool_images']} "
                                                  f"tool images replaced by text stubs at "
                                                  f"{epoch['at_counted_input_tokens']} counted input tokens.",
                                                  context_guard=epoch))
            # (5) the stock Runner (and its continuations)
            status, final_output = await _drive(agent, plan.content, session=session, log=log, guard=guard,
                                                run_config=build_run_config(client, input_filter=context_guard),
                                                settings=spec.settings, state=state, context=RunStatus(guard))
        except BaseException as raised:     # noqa: BLE001 - every outcome becomes a status and a result.json
            if guard is not None and is_transport_failure(raised) and guard.open_sequence is not None:
                try:
                    await guard.transport_failed(raised)
                except Exception:  # noqa: BLE001 - the finally clause still marks it unknown
                    pass
            status = classify_failure(raised, log=log, guard=guard)
            error = {"type": type(raised).__name__, "message": str(raised)[:2000]}
            if not isinstance(raised, Exception):
                pending = raised             # KeyboardInterrupt, CancelledError, SystemExit: re-raised below
        finally:
            # (6) always: no open hold, exit checkpoint before the worker closes, the paid run closed, result.json
            if guard is not None:
                try:
                    await guard.mark_unknown("the run ended before this request had an outcome")
                except Exception as marking:  # noqa: BLE001
                    error = error or {"type": type(marking).__name__, "message": str(marking)[:2000]}
            try:
                exit_checkpoint = scene.exit_checkpoint()
            except Exception as failure:  # noqa: BLE001
                exit_error = {"type": type(failure).__name__, "message": str(failure)[:2000]}

            def attempt(step: str, action) -> None:
                try:
                    action()
                except Exception as failure:  # noqa: BLE001 - every teardown step runs; each failure is recorded
                    teardown_errors.append({"step": step, "type": type(failure).__name__,
                                            "message": str(failure)[:2000]})
            attempt("scene.close", scene.close)             # the worker shuts down only after the exit checkpoint
            if session is not None:
                attempt("session.close", session.close)
            for open_client in (client, counter):
                if open_client is not None:
                    try:
                        await open_client.close()
                    except Exception as failure:  # noqa: BLE001
                        teardown_errors.append({"step": "client.close", "type": type(failure).__name__,
                                                "message": str(failure)[:2000]})
            attempt("heartbeat.stop", lambda: heartbeat.stop(final_state=status))

            def close_paid_run():
                nonlocal scan
                scan = jobs.close_paid_run()
            attempt("job_ledger.close_paid_run", close_paid_run)
            if log is not None and log.stop_observed:
                attempt("stop_file.remove", lambda: unlink_with_retry(stop_file))
            summary = guard.summary() if guard is not None else None
            result = {
                "schema": RESULT_SCHEMA, "job_id": job_dir.name, "run_id": spec.run_id, "status": status,
                "error": error, "started": started, "finished": _now(),
                "seconds": round(time.perf_counter() - started_clock, 3), "model": MODEL,
                "settings": {**spec.settings.approval_settings(), "max_continuations": spec.settings.max_continuations},
                "turns_used": state["turns_used"], "continuations": state["continuations"],
                "final_output": final_output if isinstance(final_output, (str, type(None))) else str(final_output),
                "requests": summary["requests"] if summary else 0, "budget": summary,
                "job_ledger": scan.get("totals") if isinstance(scan, dict) else None,
                "input": ({key: plan.receipt.get(key) for key in ("images_sent", "images_reused_from_history",
                                                                 "notes_sent")} | {
                    "brief": plan.receipt["brief"]["action"]}) if plan is not None else None,
                "rollback": rollback, "dangling_repaired": repaired, "exit_checkpoint": exit_checkpoint,
                "exit_checkpoint_error": exit_error, "teardown_errors": teardown_errors,
                "stale_stop_file_removed": stale_stop, "stop_observed": bool(log is not None and log.stop_observed),
                "tool_calls": len(log.tool_calls) if log is not None else 0,
                "transcript_errors": list(log.transcript_errors) if log is not None else [],
                "heartbeat": {"beats": heartbeat.beats, "write_failures": heartbeat.write_failures,
                              "max_gap_s": heartbeat.max_gap_s(), "states": list(heartbeat.states)},
                "transcript": "transcript.md"}
            if spec.settings.finish_tool is not None:
                result["finish_notes"] = state["finish_notes"]
            if spec.settings.context_guard_tokens is not None:
                result["context_guard"] = {"epoch": context_guard.epoch if context_guard else None,
                                           "replaced_last_request": context_guard.replaced if context_guard else 0}
            result = cleaner(result)
            if log is not None:
                try:
                    budget = summary or {}
                    log.note(f"The run ended: {status}" + (f" ({error['type']})" if error else "")
                             + f"; {result['requests']} request(s), ${budget.get('settled_usd', '0.000000')} settled"
                             + (f", ${budget['unknown_usd']} unknown" if budget.get("unknown_micro_usd") else "")
                             + ".")
                    log.event("session_finished", status=status, error=error)
                except Exception:  # noqa: BLE001 - result.json below is the authoritative record
                    pass
            write_json(run_dir / "result.json", result)
    if pending is not None:
        raise pending
    return result
