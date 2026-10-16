"""The Astra loop (WP2_DESIGN section 7): one stock Agents SDK ``Runner`` session per run, money gated per section 8.

Order of one run (``run_astra``):

1. **$0 preflight** under the job's ledger lock: the owner approval for exactly this request (``JobLedger.preflight``:
   missing, stale, consumed or below cap refuses with ``ApprovalRefused``, exit code 3, 0 requests) and the
   instructions, tool registry and settings this loop will send equal the ones the approval was estimated for; the
   required two-way tool contract; the session file agrees with ``resume``; disk space; the clients can
   be built (an API key, or the offline proof conditions) without building them; with ``RunSpec.load_check`` and
   ``refuse_on_load``, no other Blender or pytest work runs on the machine (before the scene's own worker starts); a
   stale ``stop.requested`` is removed; the scene starts on its expected checkpoint. Any other refusal is
   ``RunRefused`` (exit code 2).
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
   ``context_guard_tokens`` adds the image filter (``ContextGuard``: one epoch on counted tokens, more at rungs of
   request bytes, each scoped to the tool outputs present at its firing). All of them default off, are bound into the
   approval settings and the tool-registry sha, and leave a default run's requests unchanged. ``RunSpec.load_check``
   (the facet CLI sets it) refuses a run at preflight while other Blender or pytest work runs, and reports load that
   starts later (``LoadWatch``).
6. **Always**: an open request becomes unknown (``guard.transport_failed`` in the except clause for a connection
   error or timeout, ``guard.mark_unknown`` in finally), the exit checkpoint (head sha re-verified) is taken before the
   worker closes, the paid run is closed (``JobLedger.close_paid_run``), ``result.json`` is written.

Statuses: completed, turn_limit, budget_limit, stopped, provider_rejected (4xx, $0), provider_unknown (5xx, non-JSON,
connection error or timeout; the full hold kept), output_limit, failed_worker, failed (with the exception type),
ended_without_finish (a finish-gated run whose replies kept ending without the accepted finish), payload_limit (the
next request's body passed the provider's documented per-request limit, budget.PAYLOAD_LIMIT_BYTES / _IMAGES: refused
before counting, $0).

The scene (stage A's ``BlenderSession`` behind stage D's tools) is reached only through ``SceneHooks``; tools are
injected (stage D builds the registry). Tools receive a ``RunStatus`` as the SDK run context (``ctx.context``): money
and context numbers for their result footers. Nothing here imports from automation/ or reads automation/.env.
"""
from __future__ import annotations

import asyncio
import base64
import copy
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import re
import shutil
import threading
import time
from types import SimpleNamespace
import zlib
from typing import Any, Callable, Iterable, Sequence

from agents import Agent, ModelSettings, RunConfig, Runner, RunHooks, SQLiteSession
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
from .budget import (COUNT_TIMEOUT_PER_MB_S, COUNT_TIMEOUT_S, PAYLOAD_LIMIT_BYTES, BudgetGuard, JobLedger,
                     BudgetGuardError, cap_to_micro, unlink_with_retry)
from .budget import StageBudgetExceeded
from .money import money_text, owner_money
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
            "output_limit", "failed_worker", "failed", "ended_without_finish", "payload_limit")
EFFORTS = ("high", "xhigh", "max")
TOOL_CHOICES = ("auto", "required")
FINISH_BEHAVIOR = "g3d.finish_on_accepted/1"       # FinishOnAccepted (name and version bound into the approval)
SMOKE_TOOLS = ("scene_summary", "budget_status")
SMOKE_POLICY = {"policy": "g3d.smoke/1", "tools": list(SMOKE_TOOLS), "max_calls": 1, "max_requests": 1,
                "session": "run",
                "notice": "Smoke check: call scene_summary or budget_status once, then stop. Do not modify the scene."}
# ContextGuard. /2 (review 2026-10-12, v3p3-verify-14/-15): each epoch's choice applies only inside the tool outputs
# present at its firing (by call_id), and besides its one token epoch the guard fires on request bytes, at k x
# CONTEXT_GUARD_BYTES (k = 1, 2, ...); /1 had one token-triggered choice, applied to every later output too.
CONTEXT_GUARD = "g3d.context_guard/2"
CONTEXT_GUARD_PERIODIC = "g3d.context_guard/3"  # request cadence and per-output image identities (assembly M2)
# The guard's byte rung (serialized request bytes, budget.planning's last_request_bytes): a ceiling that keeps a run
# alive, not a routine trigger, at half the provider's documented per-request limit (budget.PAYLOAD_LIMIT_BYTES 512
# MB, where a run ends payload_limit). Every epoch rewrites the prompt cache and a late one costs more than it saves
# (the estimate's projection on the v3 T5 steps, 2026-10-12): a second epoch at 2 x 240k tokens cost $8 more over a
# 60-reply read-heavy run (fired at request 59) and saved $4 over a 90-reply edit-heavy one; a 128 MB byte rung fired
# at request 84 of the 90-reply typical run for $4.17 more; a 64 MB one fired with the token epoch, one request earlier
# on the edit-heavy mix, for $0.46. So the token epoch fires once (the long-context step, as /1), and the byte rung
# sits above every projected run (peaks 108-168 MB at 90 replies with that one epoch, v3p3-verify-15 and the
# estimate): it fires only for a body that grows toward the provider's limit (an image-dense run near the token input
# limit, 922k tokens at up to 414 B a token is 382 MB). The count's timeout scales with the body
# (budget.count_timeout_s), so a large body no longer risks the count. Part of the filter's definition (CONTEXT_GUARD
# /2): RunSettings.context_guard_bytes overrides it and is then bound into the approval.
CONTEXT_GUARD_BYTES = PAYLOAD_LIMIT_BYTES // 2
CONTINUATION_NOTE = ("Your previous response was cut off at the output limit and was not recorded. Continue.")
FINISH_NOTE = "Your reply called no tool. The run ends only when `{tool}` returns status accepted."
DANGLING_OUTPUT = ("interrupted: the run ended before this call returned; it has no result and any effect it had on "
                   "the scene was rolled back")
INFERENCE_TIMEOUT_S = 900.0         # the model-wait ceiling (observed maximum 165 s)
# COUNT_TIMEOUT_S (budget.py): the count client's base timeout; budget.count_timeout_s adds 1 s per MB of each body
HEARTBEAT_INTERVAL_S = 2.0
LOAD_WATCH_INTERVAL_S = 30.0        # LoadWatch: how often a run looks for other Blender / pytest work
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
    "labelled_model_map": 600, "preview_owner_edit": 2400, "understand_finish": 180, "edit_report": 1680, "facet_owner_edit": 660,
    "seat_lens": 150, "hide_eyewire": 150, "unseat_lens": 150, "unhide_eyewire": 150, "check_lens_seating": 120,
    "render_views": 300, "compare_to_photos": 300, "export_glb": 120, "save_checkpoint": 60, "list_checkpoints": 60,
    "restore_checkpoint": 60, "arview_capture": 300, "fit_cameras": 1800, "project_photo_texture": 1800,
    "deliver": 600, "pick_surface_point": 30, "read_image": 30, "read_report": 30, "scene_summary": 30,
    "compare_look_to_photos": 750,  # export 120 + photo-camera 280 + worn capture 280 + host margin
    # Composite finish: existing 780 + optional front-photo AR comparison (reuses the worn capture).
    "finish": 1080, "budget_status": 30,
    # the facet tools (WP6_FACETS §4.3): 3 x the slowest call measured on the Prada lab (agent/temple_lab, a warm
    # worker, studio_dome_v1 and photo_env, 2026-10-11), rounded up, never below 30 s: inspect 0.3 s, photo_image
    # 0.1 s, photo_compare 12.3 s (6 photos uncached, full crop), facet_edit 18.2 s (apply + 5 photos uncached);
    # facet_variants 0.82 s per render + 2.9 s (21 renders 20.0 s) -> its table value 90 s = FACET_VARIANTS_BASE_S +
    # FACET_VARIANTS_PER_RENDER_S x 3 x 7 renders (82.5 s) rounded up. Its worker deadline (hard_timeout_s with
    # photos and variants) scales with the renders, photos x (variants + 1), up to 4 x 5 = 20 (lab.VARIANT_PHOTOS_MAX
    # and VARIANTS_MAX 4 since v3-final; 4 x 7 = 28 with the earlier 6), capped at the table value minus
    # FACET_VARIANTS_HOST_MARGIN_S (75 s: 3.75 x the measured 21 renders, about 3.9 x the 19.3 s estimated for 20),
    # so the host's builds before it and crease lines and sheets after it keep
    # 15 s inside the hard timeout, plus the SDK's 10 s (the measured whole call took 12.8 s).
    # facet_finish (measured 0.2 s) is set above its worst declared inner deadlines plus FACET_DEADLINE_MARGIN_S, so
    # the SDK never answers E_TIMEOUT while an export may still succeed: finish = one checkpoint restore
    # (journal.CHECKPOINT_DEADLINE_S 60 s) and the export (seeing.EXPORT_DEADLINE_S 120 s) -> 210 (test_loop_facet
    # pins it against the imported deadlines).
    # photo_read (WP6 v3, host only: readings.read_point + one pick sheet a reading, no worker): 0.1-0.2 s a reading
    # measured on the Prada lab (v3p2-lab-e2), so 8 readings stay far under the 30 s floor.
    "facet_inspect": 30, "photo_image": 30, "photo_read": 30, "photo_compare": 40, "facet_edit": 60,
    "facet_variants": 90, "facet_finish": 210,
    # New composite tools: compare360 + mandatory restore90 + error recovery90 + host60. Selection covers the
    # normal apply/checkpoint/compare path plus its drift retry, 390s with30s host headroom. Existing limits stay.
    "facet_crease_compare": 600, "facet_visible_angle": 420,
}
FACET_DEADLINE_MARGIN_S = 30.0      # host work after the worker calls and a warm-spare restart (Blender 3.1-4.5 s)
FACET_VARIANTS_BASE_S, FACET_VARIANTS_PER_RENDER_S = 30.0, 2.5
FACET_VARIANTS_HOST_MARGIN_S = 15.0  # facet_variants' host work around its render, inside the hard timeout
SNIPPET_SOFT_DEFAULT_S, SNIPPET_SOFT_MIN_S, SNIPPET_SOFT_MAX_S = 120.0, 0.5, 600.0
SNIPPET_HARD_MARGIN_S = 30.0
SDK_TIMEOUT_MARGIN_S = 10.0
SEATING_REPORT_TIMEOUT_S = 660.0  # two 300 s renders plus bounds/camera reads; worker mutation limits unchanged


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
    """Largest worker timeout + composed diagnostic work + SDK margin; mutation deadlines stay unchanged."""
    report = SEATING_REPORT_TIMEOUT_S if tool_name in {
        "execute_blender_code", "seat_lens", "hide_eyewire", "unseat_lens", "unhide_eyewire"} else 0.0
    if tool_name == "execute_blender_code":
        return SNIPPET_SOFT_MAX_S + SNIPPET_HARD_MARGIN_S + SDK_TIMEOUT_MARGIN_S + report
    return hard_timeout_s(tool_name) + SDK_TIMEOUT_MARGIN_S + report


REMOVED_PHRASES = ("supplementary", "unverified", "hypothes")     # section 14 criterion 5; WP7 extends the list
MIN_FREE_BYTES = 1 << 30            # checkpoints are ~2.4 MB each, up to ~1 GB per run with packed 4K prints


@dataclass(frozen=True)
class ToolContract:
    """The two-way contract of section 7 (WP7 supplies it): every tool the instructions name is registered, every
    registered tool is named and described, gated tools are neither, and no removed phrase appears in host-authored
    instructions, brief prose or tool descriptions. The owner's verbatim evidence is not host prose."""

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
    tool's result has status "accepted"), ``finish_k`` (the finish tool's K distinct compared heads),
    ``context_guard_tokens`` (the image filter's token epoch; the guard is on only with it) and
    ``context_guard_bytes`` (its byte rungs; None: CONTEXT_GUARD_BYTES, the filter's own). An optional
    ``context_guard_every_requests`` also applies the scene's image policy before request 1 and every N requests
    thereafter, including resumed history. None preserves the facet filter. Their defaults change nothing.
    ``settled_stop_usd`` refuses the next request once settled spend reaches it, independently of the
    approval's maximum reserve cap; an already-sent request may settle above the stop but within that cap."""

    effort: str | None = None  # paid runs require the owner's explicit effort choice
    max_output_tokens: int = 16_384
    max_turns: int | None = 60
    max_continuations: int = 2
    fit_output_to_budget: bool = False
    settled_stop_usd: str | float | None = None
    tool_choice: str = "auto"
    reset_tool_choice: bool = True
    finish_tool: str | None = None
    finish_k: int = 4
    context_guard_tokens: int | None = None
    context_guard_bytes: int | None = None
    context_guard_every_requests: int | None = None
    smoke: bool = False

    def guard_bytes(self) -> int | None:
        """The context guard's byte rung in force: None while the guard is off, else context_guard_bytes or the
        filter's CONTEXT_GUARD_BYTES."""
        if self.context_guard_tokens is None:
            return None
        return CONTEXT_GUARD_BYTES if self.context_guard_bytes is None else self.context_guard_bytes

    def behavior(self) -> dict:
        """The non-default agent behaviour, bound into the approval settings and the tool-registry sha ({} when
        every new setting has its default). The guard's default byte rung is part of its filter (CONTEXT_GUARD names
        the version); an explicit context_guard_bytes is bound as request_bytes."""
        out: dict = {}
        if self.smoke:
            out["smoke"] = True
        if self.tool_choice != "auto":
            out["tool_choice"] = self.tool_choice
        if self.reset_tool_choice is not True:
            out["reset_tool_choice"] = self.reset_tool_choice
        if self.finish_tool is not None:
            out["finish"] = {"tool": self.finish_tool, "behavior": FINISH_BEHAVIOR, "k": self.finish_k}
        if self.context_guard_tokens is not None:
            out["context_guard"] = {"input_tokens": self.context_guard_tokens,
                                    "filter": CONTEXT_GUARD_PERIODIC if self.context_guard_every_requests else CONTEXT_GUARD,
                                    **({"request_bytes": self.context_guard_bytes}
                                       if self.context_guard_bytes is not None else {}),
                                    **({"every_requests": self.context_guard_every_requests}
                                       if self.context_guard_every_requests is not None else {})}
        return out

    def approval_settings(self) -> dict:
        settings = {"effort": self.effort, "max_output_tokens": self.max_output_tokens, "max_turns": self.max_turns}
        if self.fit_output_to_budget:
            settings["fit_output_to_budget"] = True
        if self.settled_stop_usd is not None:
            settings["settled_stop_usd"] = usd_text(cap_to_micro(self.settled_stop_usd))
        settings.update(self.behavior())
        return settings


@dataclass
class RunSpec:
    """One paid run of one job. ``brief``, ``photos`` and ``owner_notes`` come from the job (stage E composes them);
    ``tools`` from the registry (stage D). ``offline_handler`` is the MockTransport handler of the $0 proofs.
    ``load_check`` (e.g. machine_load) returns the other work on the machine that slows the scene's worker: with
    ``refuse_on_load`` a non-empty answer at preflight refuses the run (RunRefused, nothing consumed), and LoadWatch
    asks it every ``load_watch_interval_s`` during the run and reports a change (printed, and in result.json)."""

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
    contract_brief: str | None = None  # structured host prose only; None checks the complete brief for legacy callers
    min_free_bytes: int = MIN_FREE_BYTES
    load_check: Callable[[], list] | None = None
    refuse_on_load: bool = True
    load_watch_interval_s: float = LOAD_WATCH_INTERVAL_S
    stage_contracts: Sequence[StageContract] = ()


@dataclass
class StageSpec:
    """A specialist's isolated conversation inside an already approved parent run."""

    name: str
    instructions: str
    tools: Sequence[Any]
    brief: str
    settings: RunSettings
    photos: Sequence[ReferenceInput] = ()
    owner_notes: Sequence[str] = ()
    scene_note: str = ""
    contract: ToolContract | None = None
    contract_brief: str | None = None


@dataclass(frozen=True)
class StageContract:
    name: str
    instructions_sha256: str
    tool_registry_sha256: str
    settings_json: str
    maximum_micro_usd: int

    @classmethod
    def from_spec(cls, spec: StageSpec, maximum_usd):
        _validate_spec(spec)
        if not isinstance(spec.name, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", spec.name):
            raise RunRefused("invalid stage name")
        maximum = cap_to_micro(maximum_usd)
        if maximum <= 0:
            raise RunRefused("stage allowance must be above $0")
        settings = asdict(spec.settings)
        if settings["settled_stop_usd"] is not None:
            settings["settled_stop_usd"] = usd_text(cap_to_micro(settings["settled_stop_usd"]))
        return cls(spec.name, instructions_sha256(spec.instructions),
                   tool_registry_sha256(spec.tools, spec.settings), canonical_json(settings).decode(),
                   maximum)

    def binding(self):
        return {"name": self.name, "instructions_sha256": self.instructions_sha256,
                "tool_registry_sha256": self.tool_registry_sha256, "settings": json.loads(self.settings_json),
                "maximum_micro_usd": self.maximum_micro_usd}


def approval_settings(settings: RunSettings, stage_contracts=()) -> dict:
    """Parent request settings, including every exact specialist contract before any approval is consumed."""
    out = settings.approval_settings()
    if stage_contracts:
        names = [stage.name for stage in stage_contracts]
        if len(set(names)) != len(names):
            raise RunRefused("stage names must be unique")
        out["stages"] = [stage.binding() for stage in sorted(stage_contracts, key=lambda stage: stage.name)]
    return out


# --------------------------------------------------------------------------- other load on the machine


def load_rows(rows: Iterable[dict], mine: set) -> list[dict]:
    """The other work among process rows ({pid, name, cmdline}) outside ``mine`` (this process's tree): every
    background Blender (a blender executable with -b / --background in its command line: suite, rehearsal and lab
    workers) and every pytest process (python -m pytest, pytest.exe or py.test: its xdist workers are its children, so
    it is named once). An open Blender window (the owner's GUI or MCP session) is not counted: idle, it takes no render
    time. [{pid, kind, name}] in pid order."""
    out = []
    for row in rows:
        pid = row.get("pid")
        if pid in mine:
            continue
        name = str(row.get("name") or "")
        args = [str(a) for a in (row.get("cmdline") or [])]
        low = [a.lower() for a in args]
        bases = {re.split(r"[\\/]", a)[-1].removesuffix(".exe") for a in low}
        if name.lower().startswith("blender") and ({"-b", "--background"} & set(low)):
            out.append({"pid": pid, "kind": "background Blender", "name": name})
        elif {"pytest", "py.test"} & bases or any(a == "-m" and b in ("pytest", "py.test")
                                                  for a, b in zip(low, low[1:])):
            out.append({"pid": pid, "kind": "pytest", "name": name})
    return sorted(out, key=lambda r: r["pid"] or 0)


def machine_load() -> list[dict]:
    """load_rows over this machine's processes (psutil; rows that deny access are read as far as they allow), outside
    this process, its ancestors and its descendants (the scene's own worker and warm spare)."""
    import psutil
    me = psutil.Process(os.getpid())
    mine = {me.pid}
    for get in (me.parents, lambda: me.children(recursive=True)):
        try:
            mine |= {p.pid for p in get()}
        except psutil.Error:
            pass
    return load_rows((p.info for p in psutil.process_iter(["pid", "name", "cmdline"])), mine)


def load_text(rows: list[dict]) -> str:
    """The other work in words: each kind with its count and pids."""
    kinds: dict = {}
    for r in rows:
        kinds.setdefault(r["kind"], []).append(str(r["pid"]))
    return ", ".join(f"{len(p)} {k} (pid{'s' if len(p) > 1 else ''} {', '.join(p)})" for k, p in kinds.items())


LOAD_REFUSAL = ("other work is running on this machine: {load}. Under such load the scene's renders pass their "
                "deadlines (2026-10-12: photo_compare 43.5 s against 40 s, facet_edit's compare 67.8 s against 60 s, "
                "4.4 s and 6.5-8.0 s without it; v3p3-verify-16) and each failed compare wastes the paid reply that "
                "asked for it. Ways out: wait until it ends or stop it, then run again; or run with the load accepted "
                "(refuse_on_load off; the facet CLI's --ignore-load).")


class LoadWatch:
    """A daemon thread asking ``check`` every ``interval_s`` for other work on the machine during a run (load that
    starts after the preflight, v3p3-verify-16): each change of the set of pids is recorded (``changes``: {time,
    load}) and printed as one line for the host ("other work started ..." / "... ended"); Astra is never told. A failed
    check is counted, never fatal."""

    def __init__(self, check: Callable[[], list], *, run_id: str, interval_s: float = LOAD_WATCH_INTERVAL_S,
                 printer: Callable[[str], None] = print, initial: list | None = None):
        self.check, self.run_id, self.interval_s, self.printer = check, run_id, float(interval_s), printer
        self.current = {r["pid"]: r for r in initial or []}
        self.changes: list[dict] = []
        self.failures = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def poll(self) -> None:
        try:
            rows = list(self.check() or [])
        except Exception:  # noqa: BLE001 - a watch never fails the run
            self.failures += 1
            return
        now = {r["pid"]: r for r in rows}
        if set(now) == set(self.current):
            return
        self.current = now
        self.changes.append({"time": _iso(), "load": rows})
        line = (f"[astra {self.run_id}] other work started on this machine: {load_text(rows)}; tool calls may pass "
                "their deadlines while it runs" if rows else f"[astra {self.run_id}] the other work on this machine "
                "ended")
        try:
            self.printer(line)
        except Exception:  # noqa: BLE001
            pass

    def _run(self) -> None:
        while not self._stop.wait(self.interval_s):
            self.poll()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="g3d-load-watch", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)          # a daemon: a check still scanning never holds the run's end


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

    def bind_review_log(self, log) -> None:
        """Connect scene call journals to this run's tool receipts once ReviewLog exists."""
        return None

    def context_stubs(self, image_sha256s: list[str]) -> dict[str, str]:
        """The context guard's choice at one epoch: {image sha256: text stub} for the tool-output images (sha256 of
        the image bytes, in input order) that may be replaced in the tool outputs present now; {} keeps every
        image."""
        return {}

    def context_image_stubs(self, images: list[dict]) -> dict[tuple, str]:
        """Choices by (call_id, output part index, sha256), so identical bytes in a new call remain independent.

        The fallback preserves older scene implementations. Each image record has call_id, part_index and sha256;
        a scene with semantic image records can override this to retain selected occurrences of identical bytes.
        """
        if not images:
            return {}
        choices = self.context_stubs([image["sha256"] for image in images])
        return {image_key(image): choices[image["sha256"]] for image in images if image["sha256"] in choices}

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
    if settings.smoke:
        # Contract and approval use the full canonical registry. The explicit, bound smoke policy selects only
        # these read-only executors after preflight; schemas/descriptions of the selected tools are unchanged.
        smoke = SmokeOnce()
        tools = [smoke.wrap(tool) for tool in tools if tool.name in SMOKE_TOOLS]
        extra["tool_use_behavior"] = smoke
    elif settings.finish_tool is not None:
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
    payload = {"tools": rows, "behavior": behavior} if behavior else rows
    if settings is not None and settings.smoke:
        payload["smoke_policy"] = SMOKE_POLICY
    return hashlib.sha256(canonical_json(payload)).hexdigest()


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


class SmokeViolation(RuntimeError):
    """A smoke response attempted more than its single permitted read-only call."""


class SmokeOnce:
    """One read-only executor and then end, even if the provider returns multiple calls or a refusal."""

    def __init__(self):
        self.calls: list[dict] = []

    def wrap(self, tool):
        original = tool.on_invoke_tool

        async def invoke(context, arguments):
            rec = {"tool": tool.name, "call_id": getattr(context, "tool_call_id", None), "status": "started"}
            self.calls.append(rec)  # consume before awaiting, including schema errors, timeout and cancellation
            if len(self.calls) != 1 or tool.name not in SMOKE_TOOLS:
                rec["status"] = "blocked"
                raise SmokeViolation("Smoke permits exactly one scene_summary or budget_status call")
            try:
                output = await original(context, arguments)
            except BaseException:
                rec["status"] = "failed"
                raise
            rec["status"] = output.get("status") if isinstance(output, dict) else tool_result_status(output)
            return output

        clone = copy.copy(tool)
        clone.on_invoke_tool = invoke
        return clone

    @property
    def ok(self):
        return len(self.calls) == 1 and self.calls[0]["status"] == "ok"

    def record(self):
        return {**copy.deepcopy(SMOKE_POLICY), "calls": copy.deepcopy(self.calls), "ok": self.ok}

    def __call__(self, context, results) -> ToolsToFinalOutputResult:
        # Like stop_on_first_tool, but with explicit outcome evidence and a host execution-count guard.
        text = tool_result_text(results[0].output) if results else "Smoke returned no tool result."
        return ToolsToFinalOutputResult(is_final_output=True, final_output=text)


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


def image_key(image: dict) -> tuple:
    return image["call_id"], image["part_index"], image["sha256"]


def tool_output_image_occurrences(items) -> list[dict]:
    """Identify images by their output occurrence, never their bytes alone."""
    images = []
    for item in items or []:
        if (not isinstance(item, dict) or item.get("type") != "function_call_output"
                or not isinstance(item.get("call_id"), str) or not isinstance(item.get("output"), list)):
            continue
        for index, part in enumerate(item["output"]):
            sha = _image_sha256(part.get("image_url")) if isinstance(part, dict) and part.get("type") == "input_image" else None
            if sha is not None:
                images.append({"call_id": item["call_id"], "part_index": index, "sha256": sha})
    return images


class ContextGuard:
    """``RunConfig.call_model_input_filter`` (WP6_FACETS §4.3; CONTEXT_GUARD /2, review 2026-10-12).

    An epoch fires when the last counted request (budget.planning's last_counted_input_tokens and last_request_bytes)
    reached ``threshold`` counted input tokens for the first time (the token epoch fires once: a later one rewrites a
    long-context prefix for little, CONTEXT_GUARD_BYTES' comment), or the next byte rung, k x ``byte_threshold``
    serialized request bytes (k = 1, 2, ...; after any firing the next byte rung is the first above that request, so
    one reading never fires twice). At a firing ``choose`` is asked which tool-output images to replace with text
    stubs ({sha256: stub}, over every tool-output image present). That choice applies to this and every later
    request, but ONLY inside the function_call_outputs present at the firing (their call_ids): an output that arrives
    later reaches the model whole, even when it repeats a stubbed image's bytes. v3p3-verify-14: keyed by bytes alone,
    /1 stubbed those too;
    seeing's compare cache returns the same panel file for the same build, photo and crop, so a revision re-applied
    after the firing (facet_edit with apply: v3 has no restore) reached Astra as "superseded" stubs of the old
    compare, which E_JUDGEMENT still asked it to judge and finish precondition 3 counted as shown. Each epoch is one
    prompt-cache rewrite, then a stable prefix (earlier outputs keep their stubs or get the new choice's; later
    outputs are never rewritten). Brief images and images ``choose`` does not name are never touched; the session
    keeps every image. ``epochs`` lists every firing, ``epoch`` the latest.

    Assembly /3 optionally fires before request 1 and every N requests after it. It chooses by image occurrence
    (call id, part index, digest), retaining a new cached image independently of its older copy. Choices are made
    from the current input before serialization and free counting; previous token/byte readings are additional
    pressure triggers only. A new policy epoch can restore an earlier image when that checkpoint becomes best.
    """

    name = CONTEXT_GUARD

    def __init__(self, threshold: int, *, counted: Callable[[], int | None], choose: Callable[[list], dict],
                 notify: Callable[[dict], None] | None = None, byte_threshold: int | None = None,
                 sent_bytes: Callable[[], int | None] | None = None, every_requests: int | None = None,
                 choose_images: Callable[[list], dict] | None = None):
        self.threshold = int(threshold)
        self.byte_threshold = int(byte_threshold) if byte_threshold is not None else None
        self.counted, self.sent_bytes, self.choose, self.notify = counted, sent_bytes, choose, notify
        if every_requests is not None and (type(every_requests) is not int or every_requests < 1):
            raise ValueError("every_requests must be a positive integer or None")
        self.every_requests, self.choose_images = every_requests, choose_images
        self.name = CONTEXT_GUARD_PERIODIC if every_requests is not None else CONTEXT_GUARD
        self.requests = 0
        self.stubs: dict | None = None
        self.scope: frozenset = frozenset()          # the call_ids of the outputs present at the latest firing
        self.token_due = True                         # the token epoch has not fired yet
        self.byte_rung = 1
        self.epoch: dict | None = None
        self.epochs: list[dict] = []
        self.replaced = 0

    def due(self) -> tuple[int | None, int | None, str | None]:
        """(counted tokens, request bytes, the trigger that fires now or None) of the last counted request."""
        tokens = self.counted()
        size = self.sent_bytes() if self.sent_bytes is not None and self.byte_threshold else None
        if self.every_requests is not None and (self.requests - 1) % self.every_requests == 0:
            return tokens, size, "request_cadence"
        if self.token_due and tokens is not None and tokens >= self.threshold:
            return tokens, size, "input_tokens"
        if size is not None and size >= self.byte_rung * self.byte_threshold:
            return tokens, size, "request_bytes"
        return tokens, size, None

    def __call__(self, data) -> ModelInputData:
        model_data = data.model_data
        self.requests += 1
        tokens, size, trigger = self.due()
        fired = trigger is not None
        if fired:
            shas = tool_output_images(model_data.input)
            if self.every_requests is None:
                present = set(shas)
                self.stubs = {sha: str(text) for sha, text in (self.choose(shas) or {}).items() if sha in present}
            else:
                images = tool_output_image_occurrences(model_data.input)
                present = {image_key(image) for image in images}
                if self.choose_images is not None:
                    choices = self.choose_images(images) or {}
                else:
                    by_sha = (self.choose(shas) or {}) if shas else {}
                    choices = {image_key(image): by_sha[image["sha256"]] for image in images
                               if image["sha256"] in by_sha}
                self.stubs = {key: str(text) for key, text in choices.items() if key in present}
            self.scope = frozenset(item["call_id"] for item in model_data.input
                                   if isinstance(item, dict) and item.get("type") == "function_call_output"
                                   and isinstance(item.get("call_id"), str))
            if tokens is not None and tokens >= self.threshold:
                self.token_due = False
            if size is not None:
                self.byte_rung = max(self.byte_rung, math.floor(size / self.byte_threshold) + 1)
            self.epoch = {"epoch": len(self.epochs) + 1, "trigger": trigger, "at_counted_input_tokens": tokens,
                          "at_request_bytes": size, "tool_images": len(shas), "stubbed": 0,
                          "tool_outputs": len(self.scope),
                          **({"at_request": self.requests} if self.every_requests is not None else {})}
        if not self.stubs:
            self.replaced = 0
            self._fired(fired, 0)
            return model_data
        items, replaced = [], 0
        for item in model_data.input:
            if isinstance(item, dict) and item.get("type") == "function_call_output" and isinstance(
                    item.get("output"), list) and item.get("call_id") in self.scope:
                parts = []
                for index, part in enumerate(item["output"]):
                    sha = _image_sha256(part.get("image_url")) if isinstance(part, dict) and \
                        part.get("type") == "input_image" else None
                    key = (item["call_id"], index, sha) if self.every_requests is not None else sha
                    if key in self.stubs:
                        parts.append({"type": "input_text", "text": self.stubs[key]})
                        replaced += 1
                    else:
                        parts.append(part)
                item = {**item, "output": parts}
            items.append(item)
        self.replaced = replaced
        self._fired(fired, replaced)
        return ModelInputData(input=items, instructions=model_data.instructions)

    def _fired(self, fired: bool, replaced: int) -> None:
        """Record and report a firing: "stubbed" is the images replaced in its request (every occurrence)."""
        if not fired:
            return
        self.epoch["stubbed"] = replaced
        self.epochs.append(dict(self.epoch))
        if self.notify is not None:
            self.notify(dict(self.epoch))


def epoch_text(epoch: dict) -> str:
    """The transcript note of one context guard epoch (ContextGuard.epoch): facts only."""
    size = epoch.get("at_request_bytes")
    return (f"Context guard epoch {epoch['epoch']}: {epoch['stubbed']} of {epoch['tool_images']} tool images replaced "
            f"by text stubs in the {epoch['tool_outputs']} tool outputs present, at "
            f"{epoch['at_counted_input_tokens']} counted input tokens and "
            + ("an unrecorded request size" if size is None else f"{size / 1e6:.1f} MB")
            + " (fired on " + {"input_tokens": "counted input tokens", "request_bytes": "request bytes",
                               "request_cadence": f"request cadence, request {epoch.get('at_request')}"}[epoch["trigger"]] + ").")


class RunStatus:
    """SDK context: shared settled spend, working stop, reserve cap and last counted input for tool footers."""

    def __init__(self, guard: BudgetGuard | None = None, runtime=None):
        self.guard = guard
        self.runtime = runtime
        self.finish_configured = False
        self.finish_reference_count = None
        self.finish_evidence = None

    def configure_finish(self, scene):
        self.finish_configured = True
        images = getattr(scene, "job", {}).get("images")
        if isinstance(images, list):
            self.finish_reference_count = len(images)

    def finish_runway(self, *, evidence=None, review_ready=False):
        from .finish_runway import project
        if self.guard is None or not self.finish_configured:
            return None
        return project(self.guard.summary(), self.guard.tariff, reference_count=self.finish_reference_count,
                       evidence=evidence or self.finish_evidence, review_ready=review_ready)

    def note_finish_evidence(self, evidence):
        if evidence is not None:
            self.finish_evidence = evidence

    async def run_stage(self, spec: StageSpec, scene: SceneHooks, cap_usd, call_id):
        if self.runtime is None:
            raise StageRunFailed("no approved stage runtime is attached")
        return await self.runtime.run_stage(spec, scene, cap_usd, call_id)

    def numbers(self) -> dict:
        if self.guard is None:
            return {"settled_usd": None, "cap_usd": None, "context_tokens": None}
        summary = self.guard.summary()
        return {"settled_usd": summary["settled_micro_usd"] / 1e6, "cap_usd": summary["maximum_micro_usd"] / 1e6,
                "held_usd": summary["held_micro_usd"] / 1e6, "unknown_usd": summary["unknown_micro_usd"] / 1e6,
                "settled_stop_usd": (float(summary["settled_stop_usd"])
                                     if summary.get("settled_stop_usd") is not None else None),
                "remaining_to_stop_usd": (float(summary["remaining_to_stop_usd"])
                                          if summary.get("remaining_to_stop_usd") is not None else None),
                "context_tokens": summary["planning"]["last_counted_input_tokens"]}

    def footer(self, *, finish_runway=None) -> str:
        return footer_text(**self.numbers(), finish_runway=finish_runway or self.finish_runway())


def footer_text(settled_usd=None, cap_usd=None, context_tokens=None, *, settled_stop_usd=None,
                remaining_to_stop_usd=None, held_usd=None, unknown_usd=None, finish_runway=None) -> str:
    def usd(value):
        return "n/a" if value is None else f"${value:.2f}"
    money = money_text({"settled_usd": settled_usd, "held_usd": held_usd, "unknown_usd": unknown_usd}, places=2)
    money += ((f"; reserve cap {usd(cap_usd)}") if settled_stop_usd is None else
              f"; settled stop {usd(settled_stop_usd)} (reserve cap {usd(cap_usd)})")
    text = f"{money} · context {'n/a' if context_tokens is None else context_tokens} tokens"
    if (settled_stop_usd is not None and settled_usd is not None
            and cap_to_micro(settled_usd) * 4 >= cap_to_micro(settled_stop_usd) * 3):
        text += (f"\nAbout {usd(remaining_to_stop_usd)} left; finish your best checkpoint soon. "
                 "Finish needs a first evidence call and a second review call.")
    if finish_runway:
        from .finish_runway import text as runway_text
        text += "\n" + runway_text(finish_runway)
    return text


class StageRunFailed(RuntimeError):
    """A child failure must escape the enclosing tool, preserving the cause for parent classification."""


class GlobalTurnsExceeded(MaxTurnsExceeded):
    """Parent and child have used the approved parent interaction allowance."""


STAGE_FATAL = (StageRunFailed, BudgetGuardError, StopRequested, MaxTurnsExceeded, RunRefused)


class _SharedHooks(RunHooks):
    def __init__(self, log, maximum, scene=None, stage=None):
        self.log, self.maximum, self.scene, self.stage = log, maximum, scene, stage

    def __getattr__(self, name):
        return getattr(self.log, name)

    async def on_llm_start(self, context, agent, system_prompt, input_items):
        if self.maximum is not None and self.log.interactions >= self.maximum:
            raise GlobalTurnsExceeded("parent interaction allowance exhausted, including specialist requests")
        await self.log.on_llm_start(context, agent, system_prompt, input_items)

    async def on_llm_end(self, context, agent, response):
        await self.log.on_llm_end(context, agent, response)

    def _tool_context(self, context):
        if self.stage is None:
            return context
        # Provider call IDs belong to a conversation. Independent conversations can reuse them; only the
        # shared receipt namespace changes, while the private SDK history and scene keep the original ID.
        return SimpleNamespace(tool_call_id=f"stage:{self.stage}:{getattr(context, 'tool_call_id', None)}",
                               tool_arguments=getattr(context, "tool_arguments", None))

    async def on_tool_start(self, context, agent, tool):
        await self.log.on_tool_start(self._tool_context(context), agent, tool)

    async def on_tool_end(self, context, agent, tool, result):
        logged_context = self._tool_context(context)
        await self.log.on_tool_end(logged_context, agent, tool, result)
        if self.scene is not None:
            call_id = getattr(context, "tool_call_id", None)
            rows = [row for row in self.scene.journal_entries() if row.get("call_id") == call_id]
            if rows:
                self.log.record_journal(logged_context.tool_call_id, {**rows[-1], "stage": self.stage})


class StageRuntime:
    """Uses one live client/guard/log; child histories and checkpoint domains never enter the parent session."""

    def __init__(self, spec, client, guard, log, blobs, run_dir):
        self.contracts = {stage.name: stage for stage in spec.stage_contracts}
        self.owner_notes = tuple(spec.owner_notes)  # the exact ordered notes bound into this parent's approval
        self.client, self.guard, self.log, self.blobs = client, guard, log, blobs
        self.run_dir, self.maximum = Path(run_dir), spec.settings.max_turns
        self.tasks = set()
        self.closing = False

    async def close(self):
        self.closing = True
        pending = [task for task in self.tasks if not task.done() and task is not asyncio.current_task()]
        for task in pending:
            if not task.cancelling():
                task.cancel()  # a second cancel would interrupt the first cancellation's settlement/cleanup
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def run_stage(self, spec, scene, cap_usd, call_id):
        if self.closing or self.tasks:
            raise StageRunFailed("another stage is active or the parent run is closing")
        approved = self.contracts.get(spec.name)
        if (approved is None or StageContract.from_spec(spec, usd_text(approved.maximum_micro_usd)) != approved
                or not 0 < cap_to_micro(cap_usd) <= approved.maximum_micro_usd):
            raise StageRunFailed("stage instructions, tools, settings or allowance differ from its approval")
        if not isinstance(call_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", call_id):
            raise StageRunFailed("stage call id must be a confined identifier")
        if spec.contract is None:
            raise StageRunFailed("stage requires a two-way tool contract")
        problems = contract_problems(spec.contract, instructions=spec.instructions, tools=spec.tools,
                                     brief=spec.brief if spec.contract_brief is None else spec.contract_brief)
        if problems:
            raise StageRunFailed("stage tool contract: " + "; ".join(problems))
        task = asyncio.current_task()
        self.tasks.add(task)
        directory = self.run_dir / "stages" / f"{spec.name}-{call_id}"
        session = scope = context_guard = None
        status, output, error, exit_head = "failed", None, None, None
        state = {"continuations": 0, "finish_notes": 0}
        started = self.log.interactions
        try:
            directory.mkdir(parents=True, exist_ok=False)
            session = BlobSession(SESSION_ID, directory / "session.sqlite", blobs=self.blobs)
            notes = tuple(dict.fromkeys((*self.owner_notes, *spec.owner_notes)))
            plan = plan_session_input([], prompt=spec.brief, photos=spec.photos, owner_notes=notes,
                                      invocation_text=spec.scene_note or f"Private {spec.name} stage ({call_id}).")
            write_json(directory / "input-receipt.json", plan.receipt)
            self.log.note(f"Stage {spec.name} started ({call_id}).", stage=spec.name, stage_call_id=call_id,
                          contract=approved.binding())
            settings = spec.settings
            if settings.context_guard_tokens is not None:
                context_guard = ContextGuard(settings.context_guard_tokens, byte_threshold=settings.guard_bytes(),
                    counted=lambda: self.guard.planning()["last_counted_input_tokens"],
                    sent_bytes=lambda: self.guard.planning()["last_request_bytes"], choose=scene.context_stubs,
                    every_requests=settings.context_guard_every_requests, choose_images=scene.context_image_stubs,
                    notify=lambda epoch: self.log.note(epoch_text(epoch), stage=spec.name, context_guard=epoch))
            with self.guard.stage_budget(cap_usd, fit_output_to_budget=settings.fit_output_to_budget) as scope:
                try:
                    status, output = await _drive(build_agent(spec.instructions, spec.tools, settings), plan.content,
                        session=session, log=self.log, guard=self.guard,
                        run_config=build_run_config(self.client, input_filter=context_guard), settings=settings,
                        state=state, context=RunStatus(self.guard),
                        hooks=_SharedHooks(self.log, self.maximum, scene, call_id))
                except BaseException as exc:
                    if any(isinstance(item, StageBudgetExceeded) for item in _chain(exc)):
                        status, error = "stage_budget_limit", {"type": "StageBudgetExceeded", "message": scope.denied}
                    elif isinstance(exc, MaxTurnsExceeded) and not isinstance(exc, GlobalTurnsExceeded):
                        status, error = "turn_limit", {"type": type(exc).__name__, "message": str(exc)}
                    else:
                        await self.guard.mark_unknown("specialist stage ended before its request had an outcome")
                        status = classify_failure(exc, log=self.log, guard=self.guard)
                        error = {"type": type(exc).__name__, "message": str(exc)[:2000]}
                        if not isinstance(exc, Exception):
                            raise
                        raise StageRunFailed(f"stage {spec.name} ended: {status}") from exc
            exit_head = await asyncio.to_thread(scene.exit_checkpoint)
        except BaseException as exc:
            if error is None:
                status = classify_failure(exc, log=self.log, guard=self.guard)
                error = {"type": type(exc).__name__, "message": str(exc)[:2000]}
            await self.guard.mark_unknown("specialist stage did not finish its request")
            if isinstance(exc, Exception) and not isinstance(exc, StageRunFailed):
                raise StageRunFailed(f"stage {spec.name} ended: {status}") from exc
            raise
        finally:
            try:
                if session is not None:
                    session.close()
                receipt = {"schema": "g3d.stage_run/1", "stage": spec.name, "call_id": call_id,
                           "contract": approved.binding(), "status": status, "error": error,
                           "turns_used": self.log.interactions - started,
                           **state,
                           "budget": scope.summary() if scope is not None else None,
                           "session": "session.sqlite", "exit_checkpoint": exit_head,
                           "journal": scene.journal_entries(),
                           "context_epochs": list(context_guard.epochs) if context_guard is not None else []}
                if directory.is_dir():
                    write_json(directory / "result.json", self.log.clean(receipt))
                self.log.note(f"Stage {spec.name} ended: {status}.", stage=spec.name, stage_call_id=call_id)
            finally:
                self.tasks.discard(task)
        return {"status": status, "final_output": output, "receipt": str(directory / "result.json"),
                "budget": receipt["budget"], "exit_checkpoint": exit_head}


def instructions_sha256(instructions: str) -> str:
    return hashlib.sha256(instructions.encode("utf-8")).hexdigest()


def fatal_tool_errors(*fatal: type[BaseException]):
    """A function_tool ``failure_error_function``: the SDK default message for ordinary errors, a re-raise for the
    given types (``WorkerFailed`` must end the run ``failed_worker`` instead of reaching Astra as text)."""
    def failure(context, error):
        if isinstance(error, fatal):
            raise error
        text = default_tool_error_function(context, error)
        runtime = getattr(context, "context", None)
        return text + "\n" + runtime.footer() if hasattr(runtime, "footer") else text
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


def invocation_text(*, run_id: str, scene_note: str, cap_micro: int, rollback: dict | None,
                    settled_stop_micro: int | None = None) -> str:
    """Facts only: the run, scene, working settled stop, reserve cap and any rollback."""
    money = (f"This run's budget cap: ${usd_text(cap_micro)}." if settled_stop_micro is None else
             f"This run's working limit: ${usd_text(settled_stop_micro)} settled stop "
             f"(reserve cap ${usd_text(cap_micro)}).")
    lines = [f"Run {run_id}.", scene_note.strip(), money]
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
                "held_usd": fields.get("reserved_usd"), "unknown_usd": fields.get("unknown_usd"),
                "money_report": money_text({"settled_usd": fields.get("settled_usd"),
                                             "held_usd": fields.get("reserved_usd"),
                                             "unknown_usd": fields.get("unknown_usd")}),
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
    if guard is not None and guard.blocked_kind == "PayloadTooLarge":
        return "payload_limit"
    if any(isinstance(item, MaxTurnsExceeded) for item in _chain(error)):
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
    if settings.settled_stop_usd is not None:
        try:
            cap_to_micro(settings.settled_stop_usd)
        except BudgetGuardError:
            raise RunRefused("settled_stop_usd must be a finite non-negative dollar amount or None") from None
    if settings.tool_choice not in TOOL_CHOICES:
        raise RunRefused(f"tool_choice must be one of {', '.join(TOOL_CHOICES)}")
    if type(settings.reset_tool_choice) is not bool:
        raise RunRefused("reset_tool_choice must be a boolean")
    if type(settings.smoke) is not bool:
        raise RunRefused("smoke must be a boolean")
    if settings.smoke:
        if (settings.max_turns != 1 or settings.max_output_tokens != 4096 or settings.max_continuations != 0
                or settings.tool_choice != "required"
                or settings.finish_tool is not None or spec.resume):
            raise RunRefused("smoke requires one turn, 4096 output tokens, no continuations, required tool choice, "
                             "no finish tool or resume")
        try:
            stop, cap = cap_to_micro(settings.settled_stop_usd), cap_to_micro(spec.requested_cap_usd)
        except BudgetGuardError:
            raise RunRefused("smoke needs an explicit equal settled stop and reservation cap") from None
        if stop <= 0 or cap != stop:
            raise RunRefused("smoke needs an explicit equal positive settled stop and reservation cap")
    if type(settings.finish_k) is not int or settings.finish_k < 1:
        raise RunRefused("finish_k must be a positive integer")
    if settings.context_guard_tokens is not None and (type(settings.context_guard_tokens) is not int
                                                      or settings.context_guard_tokens < 1):
        raise RunRefused("context_guard_tokens must be a positive integer or None")
    if settings.context_guard_bytes is not None and (type(settings.context_guard_bytes) is not int
                                                     or settings.context_guard_bytes < 1
                                                     or settings.context_guard_tokens is None):
        raise RunRefused("context_guard_bytes must be a positive integer, and only with context_guard_tokens")
    if settings.context_guard_every_requests is not None and (type(settings.context_guard_every_requests) is not int
                                                              or settings.context_guard_every_requests < 1
                                                              or settings.context_guard_tokens is None):
        raise RunRefused("context_guard_every_requests must be a positive integer, and only with context_guard_tokens")
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
    if settings.smoke and not set(SMOKE_TOOLS) <= set(names):
        raise RunRefused("smoke requires both canonical read-only tools")


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
                            settings=approval_settings(spec.settings, spec.stage_contracts), unsent_owner_notes=spec.owner_notes,
                            run_inputs=run_input_binding(spec.job_dir, spec.brief, spec.photos))
    if request_sha256(current) != request_sha256(request):
        raise ApprovalRefused("stale", "job inputs, brief, photos, owner notes, instructions, tool registry or settings "
                              "changed; estimate and approve again")
    return current


async def _drive(agent: Agent, first_input: list[dict], *, session, log: ReviewLog, guard: BudgetGuard,
                 run_config: RunConfig, settings: RunSettings, state: dict, context: Any = None,
                 hooks=None) -> tuple[str, Any]:
    """Runner.run, then up to ``max_continuations`` continuation runs after an incomplete response; with a finish
    tool, up to ``max_continuations`` host notes after a reply that ended without the accepted finish."""
    turns_left = settings.max_turns
    run_input: list[dict] = [{"role": "user", "content": first_input}]
    finisher = agent.tool_use_behavior if isinstance(agent.tool_use_behavior, FinishOnAccepted) else None
    while True:
        started = log.interactions
        try:
            result = await Runner.run(agent, run_input, max_turns=turns_left, session=session, hooks=hooks or log,
                                      run_config=run_config, context=context)
        except ModelBehaviorError:
            used = log.interactions - started
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
        if settings.smoke:
            smoke = agent.tool_use_behavior
            return ("completed" if isinstance(smoke, SmokeOnce) and smoke.ok and log.interactions == 1 else "failed",
                    result.final_output)
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
    session_path = (run_dir / "smoke" / "session.sqlite" if spec.settings.smoke
                    else job_dir / "session" / "session.sqlite")
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
        problems = contract_problems(spec.contract, instructions=spec.instructions,
                                     brief=spec.brief if spec.contract_brief is None else spec.contract_brief,
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
        # other work on the machine, before scene.start(): the scene's own worker is not running yet
        preflight_load = list(spec.load_check() or []) if spec.load_check is not None else None
        if preflight_load and spec.refuse_on_load:
            raise RunRefused(LOAD_REFUSAL.format(load=load_text(preflight_load)))
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
        load_watch = LoadWatch(spec.load_check, run_id=spec.run_id, interval_s=spec.load_watch_interval_s,
                               initial=preflight_load) if spec.load_check is not None else None
        state = {"turns_used": 0, "continuations": 0, "finish_notes": 0}
        status, final_output, error, pending = "failed", None, None, None
        counter = client = guard = log = context_guard = runtime = agent = None
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
                                   cap_micro=approved.requested_cap_micro, rollback=rollback,
                                   settled_stop_micro=(None if spec.settings.settled_stop_usd is None else
                                                       cap_to_micro(spec.settings.settled_stop_usd)))
            if spec.settings.smoke:
                text += "\n" + SMOKE_POLICY["notice"]
            plan = plan_session_input(items, prompt=spec.brief, photos=spec.photos, invocation_text=text,
                                      owner_notes=spec.owner_notes)
            if session is None:     # a fresh job: the session file is created only once the approval is consumed
                session_path.parent.mkdir(parents=True, exist_ok=True)
                session = BlobSession(SESSION_ID, session_path, blobs=blobs)
            # (4) only now the clients, the guard and the receipts
            counter = clients.counting_client()
            guard = BudgetGuard.attach(counter, ledger, fit_output_to_budget=spec.settings.fit_output_to_budget,
                                       settled_stop_usd=spec.settings.settled_stop_usd)
            log = ReviewLog(run_dir, blobs_dir=job_dir / "blobs", guard=guard, secrets=clients.secrets,
                            stop_file=stop_file, heartbeat=heartbeat, run_id=spec.run_id, echo=spec.echo)
            scene.bind_review_log(log)
            client = clients.inference_client({"request": [log.before_request], "response": [log.after_response]})
            runtime = StageRuntime(spec, client, guard, log, blobs, run_dir)
            disable_sdk_tracing()
            write_json(run_dir / "run-config.json", {
                "schema": RUN_CONFIG_SCHEMA, "job_id": job_dir.name, "run_id": spec.run_id, "created": _now(),
                "model": MODEL, "agent": AGENT_NAME, "effort": spec.settings.effort, "reasoning_summary": "auto",
                "max_output_tokens": spec.settings.max_output_tokens, "max_turns": spec.settings.max_turns,
                "fit_output_to_budget": spec.settings.fit_output_to_budget,
                "settled_stop_usd": spec.settings.approval_settings().get("settled_stop_usd"),
                "max_continuations": spec.settings.max_continuations, "service_tier": "default",
                "parallel_tool_calls": False, "max_function_tool_concurrency": 1, "model_retries": 0,
                "tracing_disabled": True, "use_responses": True, "use_responses_websocket": False, "client": {
                    "kind": clients.kind, "base_url": BASE_URL, "max_retries": 0,
                    "inference_timeout_s": INFERENCE_TIMEOUT_S, "count_timeout_s": COUNT_TIMEOUT_S,
                    "count_timeout_per_mb_s": COUNT_TIMEOUT_PER_MB_S,
                    "follow_redirects": False},
                "cap_micro_usd": approved.requested_cap_micro, "cap_usd": usd_text(approved.requested_cap_micro),
                "approval_sha256": approved.approval_sha256, "request_sha256": approved.request_sha256,
                "instructions_sha256": request.get("instructions_sha256"),
                "tool_registry_sha256": request.get("tool_registry_sha256"),
                "tools": [tool.name for tool in spec.tools], "session": session_path.relative_to(job_dir).as_posix(),
                **({"smoke_policy": SMOKE_POLICY, "executable_tools": list(SMOKE_TOOLS)}
                   if spec.settings.smoke else {}),
                **({"stages": [contract.binding() for contract in spec.stage_contracts]} if spec.stage_contracts else {}),
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
            if load_watch is not None:
                load_watch.start()
            agent = build_agent(spec.instructions, spec.tools, spec.settings)
            if spec.settings.context_guard_tokens is not None:
                run_guard = guard
                context_guard = ContextGuard(
                    spec.settings.context_guard_tokens, byte_threshold=spec.settings.guard_bytes(),
                    counted=lambda: run_guard.planning()["last_counted_input_tokens"],
                    sent_bytes=lambda: run_guard.planning()["last_request_bytes"], choose=scene.context_stubs,
                    every_requests=spec.settings.context_guard_every_requests, choose_images=scene.context_image_stubs,
                    notify=lambda epoch: log.note(epoch_text(epoch), context_guard=epoch))
            # (5) the stock Runner (and its continuations)
            status, final_output = await _drive(agent, plan.content, session=session, log=log, guard=guard,
                                                run_config=build_run_config(client, input_filter=context_guard),
                                                settings=spec.settings, state=state, context=RunStatus(guard, runtime),
                                                hooks=_SharedHooks(log, spec.settings.max_turns))
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
            if runtime is not None:
                try:
                    await runtime.close()  # child cancellation settlement finishes before closing shared clients
                except Exception as closing:
                    error = error or {"type": type(closing).__name__, "message": str(closing)[:2000]}
            state["turns_used"] = log.interactions if log is not None else 0
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
            if load_watch is not None:
                attempt("load_watch.stop", load_watch.stop)

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
                "settings": {**approval_settings(spec.settings, spec.stage_contracts),
                             "max_continuations": spec.settings.max_continuations},
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
            if spec.settings.smoke:
                smoke = agent.tool_use_behavior if agent is not None else None
                result["smoke"] = (smoke.record() if isinstance(smoke, SmokeOnce) else
                                   {**copy.deepcopy(SMOKE_POLICY), "calls": [], "ok": False})
                # Success requires the executor evidence AND a single settled request; errors keep their status.
                result["smoke"]["ok"] = bool(result["smoke"]["ok"] and status == "completed"
                                             and result["requests"] == 1)
            if spec.settings.context_guard_tokens is not None:
                result["context_guard"] = {"input_tokens": spec.settings.context_guard_tokens,
                                           "request_bytes": spec.settings.guard_bytes(),
                                           **({"every_requests": spec.settings.context_guard_every_requests,
                                               "filter": CONTEXT_GUARD_PERIODIC}
                                              if spec.settings.context_guard_every_requests is not None else {}),
                                           "epoch": context_guard.epoch if context_guard else None,
                                           "epochs": list(context_guard.epochs) if context_guard else [],
                                           "replaced_last_request": context_guard.replaced if context_guard else 0}
            if spec.load_check is not None:
                result["machine_load"] = {"preflight": preflight_load, "refuse_on_load": spec.refuse_on_load,
                                          "changes": list(load_watch.changes) if load_watch else [],
                                          "check_failures": load_watch.failures if load_watch else 0}
            result = cleaner(owner_money(result))
            if log is not None:
                try:
                    budget = summary or {}
                    log.note(f"The run ended: {status}" + (f" ({error['type']})" if error else "")
                             + f"; {result['requests']} request(s), " + money_text(budget)
                             + ".")
                    log.event("session_finished", status=status, error=error)
                except Exception:  # noqa: BLE001 - result.json below is the authoritative record
                    pass
            write_json(run_dir / "result.json", result)
    if pending is not None:
        raise pending
    return result
