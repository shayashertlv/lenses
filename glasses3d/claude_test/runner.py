"""The isolated, settled-only Anthropic runner. Shared modeling code is imported unchanged."""
from __future__ import annotations

import asyncio
import copy
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from decimal import Decimal
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import threading
import time

import anthropic
import httpx2

from glasses3d.agent import approvals as A, brief, delivery as D, jobs, snapshot
from glasses3d.agent.budget import (
    PAID_RUN_LOCK_ROOT, PaidRunLock, _is_link, _open_locked, _os_unlock,
    create_json_exclusive, now_iso, write_json_atomic,
)
from glasses3d.agent.instructions import build_instructions, instruction_contract
from glasses3d.agent.job_run import new_run_id
from glasses3d.agent.job_scene import JobScene, SEED_SCRIPT
from glasses3d.agent.loop import (
    FINISH_NOTE, Heartbeat, LoadWatch, RunRefused, RunSettings,
    StageRunFailed, checked_terminal_status, contract_problems, is_worker_failure,
    machine_load, tool_registry_sha256,
)
from glasses3d.agent.offline import require_offline
from glasses3d.agent.receipts import BlobStore, StopRequested
from glasses3d.agent.session_inputs import ReferenceInput
from glasses3d.agent.temple_lab import stage
from glasses3d.agent.tools import build_tools

from . import images, money, transport
from .conversation import (
    ClaudeReviewLog, Conversation, context_measurement, estimate_content,
    opening_content, pressure, renew_conversation, request_body,
)
from .tool_adapter import ToolAdapter, claude_instructions, effective_registry_sha256, tool_definitions

WORK_ROOT = Path(__file__).resolve().parent / "work"
OFFLINE_KEY = "sk-offline-claude-proof"
MODEL = "claude-opus-5-5"


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def confined(path: Path, *, root: Path = WORK_ROOT) -> Path:
    path, root = Path(path).absolute(), Path(root).resolve()
    resolved = path.resolve()
    if not resolved.is_relative_to(root) or resolved == root:
        raise RunRefused("Claude test output must stay inside claude_test/work")
    current = path
    while current != root and current != current.parent:
        if _is_link(current):
            raise RunRefused("Claude test paths must not contain links")
        current = current.parent
    return resolved


def _token(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value):
        raise RunRefused("run and call identifiers must be confined tokens")
    return value


@dataclass(frozen=True)
class ClaudeSettings:
    model: str = MODEL
    cutoff_usd: Decimal = Decimal("20")
    effort: str = "max"
    max_tokens: int = 128_000
    max_turns: int | None = None
    max_finish_notes: int = 2
    soft_images: int = 480
    soft_bytes: int = 25_600_000
    soft_tokens: int = 800_000
    context_trigger: int = 350_000
    context_clear: int = 150_000
    context_keep: int = 8

    def validate(self):
        if self.model != MODEL or self.effort != "max":
            raise RunRefused("M13 runs require claude-opus-5-5 and max effort")
        if (not isinstance(self.cutoff_usd, Decimal) or not self.cutoff_usd.is_finite()
                or self.cutoff_usd <= 0):
            raise RunRefused("the owner's cutoff must be a positive finite Decimal")
        if self.max_turns is not None and (type(self.max_turns) is not int or self.max_turns <= 0):
            raise RunRefused("max_turns must be a positive integer or None")
        frozen = (self.max_tokens, self.max_finish_notes, self.soft_images, self.soft_bytes,
                  self.soft_tokens, self.context_trigger, self.context_clear, self.context_keep)
        if frozen != (128_000, 2, 480, 25_600_000, 800_000, 350_000, 150_000, 8):
            raise RunRefused("M13 output, finish and context policies are fixed")

    def record(self):
        return {**asdict(self), "cutoff_usd": str(self.cutoff_usd)}

    def carrier(self, *, specialist=False):
        return RunSettings(effort=self.effort, max_output_tokens=self.max_tokens,
                           max_turns=self.max_turns, settled_stop_usd=str(self.cutoff_usd),
                           settled_only=True, tool_choice="required", reset_tool_choice=False,
                           finish_tool="facet_finish" if specialist else "finish", finish_k=4)


@dataclass(frozen=True)
class ClaudeRunSpec:
    job_dir: Path
    run_id: str
    settings: ClaudeSettings
    instructions: str
    tools: tuple
    brief: str
    photos: tuple[ReferenceInput, ...]
    request_sha256: str
    stage_contracts: tuple[dict, ...]
    env_file: Path | None
    offline: bool
    source_instructions: str = ""
    owner_notes: tuple = ()
    invocation_text: str = ""
    scene_seed_binding: dict | None = None
    finish_tool: str = "finish"

    @property
    def run_dir(self):
        return confined(self.job_dir / "runs" / _token(self.run_id))


def _stage_binding(instructions, tools, settings):
    return {
        "name": "temple_stage", "source_instructions_sha256": _sha(instructions.encode()),
        "source_tools_sha256": tool_registry_sha256(tools, settings),
        "source_settings": asdict(settings),
        "instructions_sha256": _sha(claude_instructions(instructions, specialist=True).encode()),
        "tools_sha256": effective_registry_sha256(tool_definitions(tools, specialist=True)),
        "provider": "anthropic", "model": MODEL,
    }


def _prepared_stages(job, settings):
    from glasses3d.agent.temple_lab.cli import K_TOKEN
    child = stage.settings(settings.carrier())
    text = (Path(stage.__file__).parent / "instructions.txt").read_text(encoding="utf-8")
    if text.count(K_TOKEN) != 1:
        raise RunRefused("specialist finish count contract changed")
    text = text.replace(K_TOKEN, str(child.finish_k))
    return (_stage_binding(text, stage.registry(stage.PhotoCatalog(job), child), child),)


def _execution_request(spec):
    spec.settings.validate()
    job = jobs.load_job(spec.job_dir)
    if job.get("id") not in (None, spec.job_dir.name):
        raise RunRefused("private job directory must retain the original job id")
    settings = {
        **spec.settings.record(), "provider": "anthropic", "settled_only": True,
        "max_output_tokens": spec.settings.max_tokens, "stages": list(spec.stage_contracts),
        "source_instructions_sha256": _sha(spec.source_instructions.encode()),
        "source_tools_sha256": tool_registry_sha256(spec.tools, spec.settings.carrier()),
        "session": {"resume": False, "provider": "anthropic"},
        "client_mode": "offline" if spec.offline else "production",
        "scene_seed": spec.scene_seed_binding,
        "empty_template_sha256": A.file_sha256(SEED_SCRIPT),
        "image_policy": {**images.LIMITS, "oversized_image": "error", "native_tiles": True},
        "request_policy": request_body(spec, [], tools=[]),
        "finish_policy": {"tool": spec.finish_tool, "consecutive_notes": 2,
                          "note": FINISH_NOTE.format(tool=spec.finish_tool)},
    }
    request = A.build_request(
        spec.job_dir, instructions_sha256=_sha(spec.instructions.encode()),
        tool_registry_sha256=effective_registry_sha256(tool_definitions(spec.tools)),
        settings=settings, unsent_owner_notes=spec.owner_notes,
        run_inputs=A.run_input_binding(spec.job_dir, spec.brief, spec.photos),
    )
    # The host creates this empty template during scene.start; it is not an owner input.
    # Its producing script is bound above. Every other input remains in A's digest.
    request["inputs"] = [r for r in request["inputs"] if r["path"] != "inputs/seed.blend"]
    request.update(provider="anthropic", model=spec.settings.model, run_id=spec.run_id)
    # Native reference records contain tuples; approvals bind the JSON representation.
    # Normalize once so rebuilding the same request compares equal to its saved form.
    return json.loads(A.canonical_json(request))


def prepare_run(job_dir: Path, *, settings: ClaudeSettings, run_id=None, env_file=None,
                write_request=False, offline=False, scene=None):
    """Bind inputs/settings without starting a worker, reading a key or opening a client."""
    root = confined(job_dir)
    settings.validate()
    job = jobs.load_job(root)
    from glasses3d.agent import edits, owner_fallback, verdicts
    if edits.load(root) or owner_fallback.load(root):
        raise RunRefused("M13 supports fresh product runs only")
    run_id = _token(run_id or new_run_id())
    if not offline and (root / "inputs" / "seed.blend").exists():
        raise RunRefused("production M13 needs a fresh private owner-input copy")
    scene = scene or JobScene(root, job=job)
    scene.scripted = bool(offline)
    seed = None
    captured = getattr(scene, "captured_fingerprint", None)
    if captured:
        if not offline:
            raise RunRefused("captured seeds are offline tool-test evidence only")
        path = confined(scene.seed_path)
        seed = {"path": path.relative_to(root).as_posix(),
                "sha256": A.file_sha256(path), "fingerprint": captured}
    tools = tuple(build_tools(scene, settings.carrier()))
    original = build_instructions(tools)
    instructions = claude_instructions(original)
    problems = contract_problems(instruction_contract(tools), instructions=instructions,
                                 brief=brief.host_text(job), tools=tools)
    if problems:
        raise RunRefused("tool contract: " + "; ".join(problems))
    spec = ClaudeRunSpec(
        root, run_id, settings, instructions, tools, brief.build_brief(job),
        tuple(jobs.references(root)), "", _prepared_stages(job, settings),
        Path(env_file).absolute() if env_file is not None else None, bool(offline),
        original, tuple(verdicts.pending_notes(root)),
        f"Run {run_id}.\nStart with the empty job template.\n"
        f"This run's owner cutoff: ${settings.cutoff_usd} settled spend.", seed,
    )
    if seed:
        spec = replace(spec, invocation_text=f"Run {run_id}. Offline seeded tool test.\n"
                       f"This run's owner cutoff: ${settings.cutoff_usd} settled spend.")
    request = _execution_request(spec)
    spec = replace(spec, request_sha256=A.request_sha256(request))
    if write_request:
        spec.run_dir.mkdir(parents=True, exist_ok=True)
        create_json_exclusive(spec.run_dir / "run-request.json", request)
    return spec, scene


def _recompute(spec):
    """Rebuild sources and owner inputs, not just the dataclass supplied by a caller."""
    root = confined(spec.job_dir)
    job = jobs.load_job(root)
    tools = tuple(build_tools(JobScene(root, job=job), spec.settings.carrier()))
    original = build_instructions(tools)
    from glasses3d.agent import verdicts
    current = replace(spec, tools=tools, source_instructions=original,
                      instructions=claude_instructions(original), brief=brief.build_brief(job),
                      photos=tuple(jobs.references(root)), owner_notes=tuple(verdicts.pending_notes(root)),
                      stage_contracts=_prepared_stages(job, spec.settings))
    if spec.scene_seed_binding:
        seed = spec.scene_seed_binding
        if A.file_sha256(confined(root / seed["path"])) != seed["sha256"]:
            raise A.ApprovalRefused("stale", "captured proof seed changed")
    request = _execution_request(current)
    # Also catch replacement of the supplied instructions/tool/schema/input values.
    if _execution_request(spec) != request or A.request_sha256(request) != spec.request_sha256:
        raise A.ApprovalRefused("stale", "inputs, instructions, tools or settings changed")
    return request


def _request_bytes(spec):
    request = _recompute(spec)
    path = spec.run_dir / "run-request.json"
    if _is_link(path) or not path.is_file():
        raise A.ApprovalRefused("missing", "prepare must write run-request.json first")
    raw = path.read_bytes()
    if json.loads(raw) != request:
        raise A.ApprovalRefused("stale", "prepared execution request changed")
    return raw


def approve_run(spec, *, words: str):
    raw = _request_bytes(spec)
    if spec.offline:
        require_offline(OFFLINE_KEY)
    directory = spec.run_dir
    approval = money.write_approval(
        directory / "approval.json", words=words, amount_usd=spec.settings.cutoff_usd,
        model=spec.settings.model, effort=spec.settings.effort, run_id=spec.run_id,
    )
    seal = {
        "schema": "g3d.claude_approval_binding/1", "provider": "anthropic",
        "model": spec.settings.model, "job_id": spec.job_dir.name, "run_id": spec.run_id,
        "offline_only": spec.offline, "owner_words": words,
        "amount_usd": str(spec.settings.cutoff_usd), "request_sha256": spec.request_sha256,
        "run_request_sha256": _sha(raw),
        "approval_request_sha256": A.file_sha256(directory / "approval-request.json"),
        "approval_sha256": A.file_sha256(directory / "approval.json"),
    }
    create_json_exclusive(directory / "approval-binding.json", seal)
    return {**approval, "binding": seal}


def _check_seal(spec):
    raw = _request_bytes(spec)
    directory = spec.run_dir
    path = directory / "approval-binding.json"
    if _is_link(path) or not path.is_file():
        raise A.ApprovalRefused("missing", "the complete execution approval seal is required")
    seal = json.loads(path.read_text(encoding="utf-8"))
    expected = {
        "schema": "g3d.claude_approval_binding/1", "provider": "anthropic",
        "model": spec.settings.model, "job_id": spec.job_dir.name, "run_id": spec.run_id,
        "offline_only": spec.offline, "amount_usd": str(spec.settings.cutoff_usd),
        "request_sha256": spec.request_sha256, "run_request_sha256": _sha(raw),
        "approval_request_sha256": A.file_sha256(directory / "approval-request.json"),
        "approval_sha256": A.file_sha256(directory / "approval.json"),
    }
    approval = json.loads((directory / "approval.json").read_text(encoding="utf-8"))
    expected["owner_words"] = approval["owner_words"]
    if seal != expected:
        raise A.ApprovalRefused("stale", "execution approval seal changed or belongs to another mode")
    if (directory / "approval.json.consumed.json").exists():
        raise A.ApprovalRefused("consumed", "this approval has already been consumed")
    return seal


@contextmanager
def job_lock(root):
    path = confined(Path(root) / "budget" / ".job-ledger.lock")
    stream = _open_locked(path)
    if stream is None:
        raise RunRefused("another process holds this private job")
    try:
        yield
    finally:
        _os_unlock(stream)


class ToolMoneyView:
    def __init__(self, ledger):
        self.ledger = ledger

    def summary(self):
        facts = self.ledger.money_status()
        return {**facts, "settled_stop_usd": facts["cutoff_usd"],
                "remaining_to_stop_usd": facts["left_usd"], "settled_only": True}


class ClaudeRunStatus:
    def __init__(self, ledger, *, settings, runtime=None, stage_call_id=None):
        self.ledger, self.runtime = ledger, runtime
        self.guard = ToolMoneyView(ledger)
        self.settings = settings
        self.tool_settings = settings.carrier(specialist=stage_call_id is not None)
        self.stage_call_id = stage_call_id

    def numbers(self):
        return self.ledger.money_status()

    def footer(self):
        facts = self.numbers()
        return (f"Spent ${facts['settled_usd']} (settled); owner cutoff ${facts['cutoff_usd']}; "
                f"money left ${facts['left_usd']}.")

    async def run_stage(self, spec, scene, cap_usd, call_id):
        if self.runtime is None:
            raise StageRunFailed("nested specialist stages are unavailable")
        return await self.runtime.run_stage(spec, scene, cap_usd, call_id)


class ClaudeHeartbeat(Heartbeat):
    def __init__(self, path, *, model, **kwargs):
        super().__init__(path, **kwargs)
        self.model = model

    def snapshot(self):
        row = super().snapshot()
        for name in ("reserved_usd", "held_usd", "unknown_usd"):
            row.pop(name, None)
        with self._lock:
            facts = {key: self._fields.get(key) for key in ("settled_usd", "cutoff_usd", "left_usd")}
        row.update(facts, provider="anthropic", model=self.model)
        row["money_report"] = (f"Spent ${facts['settled_usd']} (settled); "
                               f"cutoff ${facts['cutoff_usd']}; left ${facts['left_usd']}.")
        return row


class Halt(RuntimeError):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


def _ledger_rows(ledger):
    return [json.loads(line) for line in ledger.path.read_text(encoding="utf-8").splitlines()]


def _ledger_failure(ledger, error=None):
    rows = _ledger_rows(ledger)
    if any(row["event"] == "unknown" for row in rows):
        return "provider_unknown"
    if any(row.get("refusal") for row in rows) or any(row["event"] == "rejected" for row in rows):
        return "provider_rejected"
    allowed, reason = ledger.may_start_request()
    if not allowed:
        return "budget_limit" if ledger.settled_usd() >= ledger.cutoff_usd else "provider_unknown"
    if isinstance(error, Halt):
        return error.status
    if isinstance(error, (StopRequested, asyncio.CancelledError, KeyboardInterrupt)):
        return "stopped"
    if error and (is_worker_failure(error) or type(error).__name__ in {"WorkerDied", "CheckpointMismatch"}):
        return "failed_worker"
    if isinstance(error, anthropic.APIStatusError):
        return "provider_rejected"
    return "failed"


def _public_text(response):
    return "\n".join(block["text"] for block in response.get("content", [])
                     if block.get("type") == "text" and isinstance(block.get("text"), str))


async def _join(task):
    """Finish real thread/task teardown even when the awaiting coroutine was cancelled."""
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
    return task.result()


async def _owned_thread(function, *args, **kwargs):
    """Keep lifecycle resources owned until their synchronous operation returns."""
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await _join(task)
        except BaseException:
            pass
        raise


class _Runtime:
    def __init__(self, *, spec, scene, conversation, ledger, client, files, log, directory,
                 shared, stage_runtime=None, stage_call_id=None, pressure_hook=None):
        self.spec, self.scene, self.conversation = spec, scene, conversation
        self.ledger, self.client, self.files, self.log = ledger, client, files, log
        self.directory, self.shared = Path(directory), shared
        (self.directory / "requests").mkdir(parents=True, exist_ok=True)
        self.status = ClaudeRunStatus(ledger, settings=spec.settings, runtime=stage_runtime,
                                     stage_call_id=stage_call_id)
        self.adapter = ToolAdapter(spec.tools, scene=scene, status=self.status, log=log, files=files,
                                   image_manifest=self.directory / "image-manifest.jsonl",
                                   specialist=stage_call_id is not None)
        self.definitions = tool_definitions(spec.tools, specialist=stage_call_id is not None)
        self.last_usage = None
        self.appended_estimate = {"text_tokens": 0, "image_tokens": 0}
        self.renewals = []
        self.bypass_soft = False
        self.irreducible_pressure = False
        self.pressure_hook = pressure_hook
        self.final_output = None
        self.finish_notes = []
        self.consecutive_text = 0
        self.tool_calls = 0
        self.stage_call_id = stage_call_id
        self._stream = None
        self._stream_lock = threading.Lock()

    def dimensions(self):
        return {v["file_id"]: (v["w"], v["h"]) for v in self.files._read().values()}

    def mark_renewed(self):
        self.last_usage = None
        self.appended_estimate = {"text_tokens": 0, "image_tokens": 0}
        self.bypass_soft = True
        self.adapter.expire_image_dedup()

    async def append(self, message):
        await self.conversation.append(message)
        measured = estimate_content(message.get("content", []), self.dimensions())
        for key, value in measured.items():
            if isinstance(value, (int, float)):
                self.appended_estimate[key] = self.appended_estimate.get(key, 0) + value

    def _stream_request(self, body):
        kwargs = copy.deepcopy(body)
        kwargs.pop("stream", None)
        with self.client.beta.messages.stream(
                **kwargs, betas=["context-management-2025-06-27"]) as stream:
            with self._stream_lock:
                self._stream = stream
            try:
                message = stream.get_final_message()
                return message.model_dump(mode="json", exclude_unset=True)
            finally:
                with self._stream_lock:
                    self._stream = None

    def _close_stream(self):
        with self._stream_lock:
            stream = self._stream
        if stream is not None:
            stream.close()

    async def request(self, *, summary=False):
        self.log.check_stop()
        if self.shared.get("cancelled"):
            raise Halt("stopped", "run cancellation requested")
        allowed, reason = self.ledger.may_start_request()
        if not allowed:
            raise Halt(_ledger_failure(self.ledger), reason)
        if self.spec.settings.max_turns is not None and self.shared["requests"] >= self.spec.settings.max_turns:
            raise Halt("turn_limit", "parent and specialists exhausted the request limit")
        messages = await self.conversation.messages()
        body = request_body(self.spec, messages, tools=self.definitions)
        estimate = self.appended_estimate
        if self.last_usage is None:
            estimate = estimate_content(
                [{"type": "text", "text": self.spec.instructions + json.dumps(self.definitions)},
                 *[block for message in messages for block in (
                     message["content"] if isinstance(message["content"], list)
                     else [{"type": "text", "text": message["content"]}])]], self.dimensions())
        measurement = context_measurement(last_usage=self.last_usage, appended_estimate=estimate)
        reasons = pressure(body, dims=self.dimensions(), measurement=measurement, settings=self.spec.settings)
        if self.pressure_hook is not None and not summary:
            reasons.extend(self.pressure_hook(self, body, measurement) or [])
        if reasons and not summary and not self.bypass_soft:
            if self.irreducible_pressure:
                raise Halt("payload_limit", "carried evidence remains above the renewal threshold")
            renewal = asyncio.create_task(renew_conversation(
                self, {"reasons": reasons, "measurement": measurement}))
            try:
                record = await asyncio.shield(renewal)
            except asyncio.CancelledError:
                self.shared["cancelled"] = True
                self.log.stop_observed = True
                try:
                    await _join(renewal)
                except BaseException:
                    pass
                raise
            if record not in self.renewals:
                self.renewals.append(record)
            self.mark_renewed()
            return await self.request()
        problems = images.check_request(body, self.dimensions())
        if problems:
            raise Halt("payload_limit", "; ".join(problems))
        if measurement["forecast_tokens"] > 1_000_000:
            raise Halt("payload_limit", "local input forecast exceeds the context capacity")
        if self.bypass_soft and reasons:
            self.irreducible_pressure = True
        self.bypass_soft = False
        prefix = await self.conversation.assert_request_prefix(body)
        self.shared["requests"] += 1
        number = self.shared["requests"]
        self.log.interactions = number
        self.log._heartbeat("model_wait", request_seq=number)
        request_path = self.directory / "requests" / f"{number:04d}-request.json"
        write_json_atomic(request_path, body)
        meter = {"provider": "anthropic", "model": self.spec.settings.model, "request_no": number,
                 "summary": summary, "forecast": measurement, "prefix": prefix}
        write_json_atomic(request_path.with_name(f"{number:04d}-meter.json"), meter)
        task = asyncio.create_task(asyncio.to_thread(self._stream_request, body))
        try:
            while not task.done():
                await asyncio.wait({task}, timeout=0.2)
                if self.log.stop_requested() or self.shared.get("cancelled"):
                    self.log.stop_observed = True
                    await asyncio.to_thread(self._close_stream)
                    try:
                        await _join(task)
                    except Exception:
                        pass
                    raise Halt(_ledger_failure(self.ledger, StopRequested("Stop requested")), "Stop requested")
            response = task.result()
        except asyncio.CancelledError:
            await asyncio.to_thread(self._close_stream)
            try:
                await _join(task)
            except Exception:
                pass
            raise
        finally:
            self.log._heartbeat("idle")
            self.log.publish_money(self.ledger.money_status())
        # Only transport settlement, never the SDK's parsed usage, authorizes tools.
        rows = _ledger_rows(self.ledger)
        settlement = next((r for r in reversed(rows) if r.get("request_no") == number
                           and r["event"] in {"settled", "unknown"}), None)
        if settlement is None or settlement["event"] != "settled":
            raise Halt("provider_unknown", "the reply has no complete transport settlement")
        self.last_usage = settlement["usage"]
        self.appended_estimate = {"text_tokens": 0, "image_tokens": 0}
        meter["actual_next_usage"] = copy.deepcopy(self.last_usage)
        meter["request_id"] = settlement.get("request_id")
        write_json_atomic(request_path.with_name(f"{number:04d}-meter.json"), meter)
        raw_path = self.directory / "requests" / f"{number:04d}-response.json"
        write_json_atomic(raw_path, response)
        authority = next((p for p in self.files.path.parent.joinpath("transport").glob(f"{number}-*.json")
                          if json.loads(p.read_text(encoding="utf-8")).get("outcome") == "settled"), raw_path)
        self.log.record_message(request_no=number, request_id=settlement.get("request_id"),
                                raw_response=response, transport_receipt=authority)
        await self.conversation.append({"role": "assistant", "content": response.get("content", [])})
        self.final_output = _public_text(response) or self.final_output
        edits = (response.get("context_management") or {}).get("applied_edits", [])
        if any(e.get("type") == "clear_tool_uses_20250919" for e in edits):
            self.adapter.expire_image_dedup()
            self.log.event("context_edit", edits=edits, local_history_unchanged=True)
        if settlement.get("refusal") or response.get("stop_reason") == "refusal" or response.get("stop_details"):
            raise Halt("provider_rejected", "provider refusal; settled cost is an upper bound")
        if response.get("stop_reason") in {"max_tokens", "model_context_window_exceeded"}:
            raise Halt("output_limit", str(response["stop_reason"]))
        if response.get("stop_reason") not in {"tool_use", "end_turn", "stop_sequence"}:
            raise Halt("failed", "unsupported provider stop reason: " + str(response.get("stop_reason")))
        return response

    async def drive(self):
        while True:
            response = await self.request()
            calls = [b for b in response.get("content", []) if b.get("type") == "tool_use"]
            if not calls:
                self.consecutive_text += 1
                if self.consecutive_text > self.spec.settings.max_finish_notes:
                    return "ended_without_finish"
                note = FINISH_NOTE.format(tool=self.spec.finish_tool)
                record = {"request_no": self.shared["requests"], "consecutive": self.consecutive_text, "text": note}
                self.finish_notes.append(record)
                self.log.note(note, finish_note=record)
                await self.append({"role": "user", "content": [{"type": "text", "text": note}]})
                continue
            self.consecutive_text = 0
            results, accepted, accepted_output, accepted_result = [], None, None, None
            try:
                for call in calls:
                    self.log.check_stop()
                    invocation = asyncio.create_task(self.adapter.invoke(call))
                    try:
                        result = await asyncio.shield(invocation)
                    except asyncio.CancelledError:
                        # Shared tool executors can own synchronous worker threads.
                        # Joining the protected invocation keeps its worker and locks
                        # alive until the mutation and its result conversion finish.
                        self.shared["cancelled"] = True
                        self.log.stop_observed = True
                        try:
                            await _join(invocation)
                        except BaseException:
                            pass
                        raise
                    self.tool_calls += 1
                    results.append(result)
                    if self.adapter.accepted_finish(call["name"], self.adapter.last_raw_output):
                        accepted = call
                        accepted_output = self.adapter.last_raw_output
                        accepted_result = result
                await self.append({"role": "user", "content": results})
            except BaseException:
                for call in calls:
                    self.adapter.mark_unseen(call["id"])
                raise
            # A later call may have changed the head or invalidated an earlier finish.
            if (accepted and self._finish_current(accepted)
                    and self.adapter.accepted_finish(accepted["name"], accepted_output)):
                self.final_output = "\n".join(
                    block.get("text", "") for block in accepted_result.get("content", [])
                    if block.get("type") == "text") or self.final_output
                return "completed"

    def _finish_current(self, accepted):
        if accepted["name"] != self.spec.finish_tool:
            return False
        if self.spec.finish_tool == "finish":
            finished = getattr(self.scene, "finish", None)
            if not finished:
                return False
            head = getattr(getattr(self.scene, "s", None), "head", {}) or {}
            return (finished.get("call_id") == accepted["id"]
                    and finished.get("checkpoint") == head.get("id"))
        state = getattr(self.scene, "state", {}) or {}
        return bool(state.get("finish"))


class ClaudeStageRuntime:
    def __init__(self, parent, *, client, ledger, files, log, blobs, shared):
        self.parent, self.client, self.ledger = parent, client, ledger
        self.files, self.log, self.blobs, self.shared = files, log, blobs, shared
        self.tasks, self.outcomes, self.closing = set(), [], False

    async def close(self):
        self.closing = True
        pending = [task for task in self.tasks if task is not asyncio.current_task()]
        for task in pending:
            if not task.cancelling():
                task.cancel()
        for task in pending:
            try:
                await _join(task)
            except BaseException:
                pass

    async def run_stage(self, spec, scene, cap_usd, call_id):
        if self.closing or self.tasks:
            raise StageRunFailed("another specialist is active or the run is closing")
        _token(call_id)
        expected = next((v for v in self.parent.stage_contracts if v["name"] == spec.name), None)
        actual = _stage_binding(spec.instructions, spec.tools, spec.settings)
        if expected != actual:
            raise StageRunFailed("specialist source or effective Claude contract differs from approval")
        if not isinstance(cap_usd, Decimal) or not cap_usd.is_finite() or cap_usd <= 0:
            raise StageRunFailed("specialist allowance must be an exact positive Decimal")
        allowed, reason = self.ledger.may_start_request()
        if not allowed:
            raise Halt(_ledger_failure(self.ledger), reason)
        if cap_usd > Decimal(self.ledger.money_status()["left_usd"]):
            raise StageRunFailed("specialist allowance exceeds the parent's money left")
        if spec.contract is None or contract_problems(
                spec.contract, instructions=spec.instructions, tools=spec.tools,
                brief=spec.contract_brief if spec.contract_brief is not None else spec.brief):
            raise StageRunFailed("specialist tool contract failed")
        directory = self.parent.run_dir / "stages" / f"{spec.name}-{call_id}"
        directory.mkdir(parents=True, exist_ok=False)
        task = asyncio.current_task()
        self.tasks.add(task)
        conv = Conversation(directory / "session.sqlite", blobs=self.blobs)
        child = replace(
            self.parent, instructions=claude_instructions(spec.instructions, specialist=True),
            tools=tuple(spec.tools), brief=spec.brief, photos=tuple(spec.photos),
            owner_notes=tuple(dict.fromkeys((*self.parent.owner_notes, *spec.owner_notes))),
            invocation_text=spec.scene_note, finish_tool="facet_finish",
        )
        runtime = _Runtime(spec=child, scene=scene, conversation=conv, ledger=self.ledger,
                           client=self.client, files=self.files, log=self.log, directory=directory,
                           shared=self.shared, stage_call_id=call_id)
        status, error, head = "failed", None, None
        before = self.shared["requests"]
        try:
            content, receipt = await _owned_thread(opening_content, child, files=self.files)
            write_json_atomic(directory / "input-receipt.json", receipt)
            await runtime.append({"role": "user", "content": content})
            status = await runtime.drive()
            head = await _owned_thread(scene.exit_checkpoint)
        except BaseException as exc:
            status = _ledger_failure(self.ledger, exc)
            error = {"type": type(exc).__name__, "message": str(exc)[:2000]}
            if not isinstance(exc, Exception):
                raise
            if status not in {"budget_limit", "turn_limit", "ended_without_finish", "output_limit"}:
                raise StageRunFailed(f"stage {spec.name} ended: {status}") from exc
        finally:
            conv.close()
            outcome = {
                "schema": "g3d.stage_run/1", "provider": "anthropic", "model": MODEL,
                "stage": spec.name, "call_id": call_id, "status": status, "error": error,
                "turns_used": self.shared["requests"] - before, "budget": self.ledger.money_status(),
                "exit_checkpoint": head, "final_output": runtime.final_output,
                "finish_notes": runtime.finish_notes, "renewals": runtime.renewals,
                "contract": expected, "journal": scene.journal_entries(),
                "receipt": str(directory / "result.json"),
            }
            write_json_atomic(directory / "result.json", outcome)
            self.outcomes.append(outcome)
            self.tasks.discard(task)
        return outcome


def _production_key(path):
    """Called only by an authorized production run; agents/tests never invoke this path."""
    if os.environ.get("G3D_OFFLINE") == "1":
        raise RunRefused("offline environment cannot construct a production client")
    if path is None:
        raise RunRefused("an explicit --env-file path is required")
    from dotenv import dotenv_values
    value = dotenv_values(path).get("ANTHROPIC_API_KEY")
    if not value or value.startswith("sk-offline-"):
        raise RunRefused("the authorized key file has no production Anthropic key")
    return value


async def run_claude(spec, *, scene=None, inner=None, isolated_lock_root=None, pressure_hook=None,
                     sleep=time.sleep):
    spec.settings.validate()
    root, directory = confined(spec.job_dir), spec.run_dir
    if spec.offline:
        require_offline(OFFLINE_KEY)
        if not isinstance(inner, httpx2.MockTransport):
            raise RunRefused("offline runs require httpx2.MockTransport")
        lock_root = confined(isolated_lock_root or WORK_ROOT / "locks")
    else:
        if inner is not None or isolated_lock_root is not None or pressure_hook is not None or sleep is not time.sleep:
            raise RunRefused("production runs cannot inject transport, locks, pressure or sleep")
        lock_root = PAID_RUN_LOCK_ROOT
    scene = scene or JobScene(root)
    if getattr(scene, "root", root).resolve() != root:
        raise RunRefused("scene belongs to a different private job")
    # A prepared FunctionTool closes over its scene. Rebind identical descriptors
    # to the actual lifecycle scene, then require the sealed registry to agree.
    spec = replace(spec, tools=tuple(build_tools(scene, spec.settings.carrier())))
    if (root / "session" / "session.sqlite").exists():
        raise RunRefused("M13 runs are fresh; saved conversations require a separate crash audit")
    if shutil.disk_usage(root).free < (1 << 30):
        raise RunRefused("at least 1 GiB free disk is required")
    started, clock = now_iso(), time.perf_counter()
    ledger = client = conversation = runtime = stages = log = heartbeat = load_watch = None
    consumed = False
    result = None
    status, error, head, exit_error, pending = "failed", None, None, None, None
    teardown = []
    shared = {"requests": 0}
    paid = PaidRunLock(lock_root)
    with job_lock(root):
        seal = _check_seal(spec)
        paid.acquire(job_id=root.name, run_id=spec.run_id)
        try:
            await _owned_thread(scene.start)
            if spec.scene_seed_binding:
                if scene.s.head["fingerprint"] != spec.scene_seed_binding["fingerprint"]:
                    raise RunRefused("started proof scene fingerprint differs from the bound source")
            seal = _check_seal(spec)
            approval = money.consume_approval(directory / "approval.json", spec.run_id)
            consumed = True
            ledger = money.Ledger(directory / "ledger.jsonl", cutoff_usd=spec.settings.cutoff_usd,
                                  approval=approval)
            await _owned_thread(snapshot.capture, directory)
            scene.active_run_id = spec.run_id
            if hasattr(scene, "save"):
                scene.save()
            key = OFFLINE_KEY if spec.offline else _production_key(spec.env_file)
            client = anthropic.Anthropic(
                api_key=key, http_client=transport.guarded_client(
                    ledger, directory / "transport", inner=inner, sleep=sleep),
                max_retries=0, timeout=900,
            )
            def uploader(payload, filename):
                response = client.files.upload(
                    file=(filename, payload, "image/" + ("jpeg" if filename.endswith(".jpg")
                                                       else filename.rsplit(".", 1)[-1])),
                    **transport.files_upload_params())
                return response.id
            files = images.FileCache(directory / "file-cache.json", uploader)
            blobs = BlobStore(root / "blobs")
            conversation = Conversation(root / "session" / "session.sqlite", blobs=blobs)
            heartbeat = ClaudeHeartbeat(root / "heartbeat.json", model=MODEL, run_id=spec.run_id,
                                       worker_info=scene.worker_info,
                                       printer=lambda text: print(text.replace("[astra ", "[claude "), flush=True))
            log = ClaudeReviewLog(directory, blobs_dir=root / "blobs", guard=None,
                                  stop_file=root / "stop.requested", heartbeat=heartbeat)
            if hasattr(scene, "bind_review_log"):
                scene.bind_review_log(log)
            stages = ClaudeStageRuntime(spec, client=client, ledger=ledger, files=files,
                                        log=log, blobs=blobs, shared=shared)
            runtime = _Runtime(spec=spec, scene=scene, conversation=conversation, ledger=ledger,
                               client=client, files=files, log=log, directory=directory, shared=shared,
                               stage_runtime=stages, pressure_hook=pressure_hook)
            content, receipt = await _owned_thread(opening_content, spec, files=files)
            write_json_atomic(directory / "input-receipt.json", receipt)
            await runtime.append({"role": "user", "content": content})
            write_json_atomic(directory / "run-config.json", {
                "schema": "g3d.run_config/1", "provider": "anthropic", "model": MODEL,
                "job_id": root.name, "run_id": spec.run_id, "settings": spec.settings.record(),
                "sdk": importlib.metadata.version("anthropic"), "max_retries": 0,
                "approval_binding": seal, "offline": spec.offline,
                "request": request_body(spec, [], tools=runtime.definitions),
            })
            log.publish_money(ledger.money_status())
            heartbeat.start()
            load_watch = LoadWatch(machine_load, run_id=spec.run_id, interval_s=30,
                                   printer=lambda text: print(text.replace("[astra ", "[claude "), flush=True))
            load_watch.start()
            status = await runtime.drive()
        except BaseException as exc:
            if not consumed:
                raise
            status = _ledger_failure(ledger, exc) if ledger is not None else "failed"
            error = {"type": type(exc).__name__, "message": str(exc)[:2000]}
            if not isinstance(exc, Exception):
                pending = exc
        finally:
            if stages:
                await stages.close()
            if consumed:
                try:
                    head = await _owned_thread(scene.exit_checkpoint)
                except Exception as exc:
                    exit_error = {"type": type(exc).__name__, "message": str(exc)[:2000]}
                    if status == "completed":
                        status = "failed_worker"
            for name, action in (
                    ("scene.close", scene.close),
                    ("conversation.close", conversation.close if conversation else None),
                    ("client.close", client.close if client else None),
                    ("heartbeat.stop", lambda: heartbeat.stop(status) if heartbeat else None),
                    ("load_watch.stop", load_watch.stop if load_watch else None)):
                if action:
                    try:
                        await _owned_thread(action)
                    except Exception as exc:
                        teardown.append({"step": name, "type": type(exc).__name__, "message": str(exc)[:2000]})
            paid.release()
            if consumed:
                if log and log.stop_observed:
                    (root / "stop.requested").unlink(missing_ok=True)
                rows = _ledger_rows(ledger) if ledger else []
                result = {
                    "schema": "g3d.run_result/1", "provider": "anthropic", "model": MODEL,
                    "job_id": root.name, "run_id": spec.run_id, "status": checked_terminal_status(status),
                    "error": error, "started": started, "finished": now_iso(),
                    "seconds": round(time.perf_counter() - clock, 3), "settings": spec.settings.record(),
                    "requests": sum(r["event"] == "started" for r in rows), "turns_used": shared["requests"],
                    "budget": ledger.money_status() if ledger else None,
                    "finish_notes": len(runtime.finish_notes) if runtime else 0,
                    "finish_note_records": runtime.finish_notes if runtime else [],
                    "continuations": 0, "tool_calls": len(log.tool_calls) if log else 0,
                    "final_output": runtime.final_output if runtime else None,
                    "stages": stages.outcomes if stages else [], "renewals": runtime.renewals if runtime else [],
                    "rollback": None, "dangling_repaired": [], "exit_checkpoint": head,
                    "exit_checkpoint_error": exit_error, "teardown_errors": teardown,
                    "stop_observed": bool(log and log.stop_observed),
                    "transcript_errors": log.transcript_errors if log else [], "transcript": "transcript.md",
                    "approval_binding": seal, "offline": spec.offline, "tool_test": spec.offline,
                    "input": {"photos": len(spec.photos)},
                    "ledger": {"path": "ledger.jsonl", "sha256": A.file_sha256(ledger.path)} if ledger else None,
                    "heartbeat": {"beats": heartbeat.beats, "write_failures": heartbeat.write_failures,
                                  "max_gap_s": heartbeat.max_gap_s()} if heartbeat else {},
                }
                write_json_atomic(directory / "result.json", result)
    if pending:
        raise pending
    return result


def publish_result(job_dir, scene, result):
    root = confined(job_dir)
    failure = None
    if result["status"] == "completed":
        try:
            outcome = D.deliver_finished(root, scene, result)
        except Exception as exc:
            outcome = {"status": "delivery_failed", "reason": f"{type(exc).__name__}: {exc}"}
        if outcome.get("status") == "delivered":
            write_json_atomic(root / "runs" / result["run_id"] / "delivery.json", outcome)
            return outcome
        failure = {**outcome, "reason": outcome.get("reason") or str(outcome)}
    refusal = {"status": "no_usable_checkpoint", "finished": False, "accepted_finish": False,
               "run_status": result["status"], "run_id": result["run_id"],
               "stages": copy.deepcopy(result.get("stages", [])),
               "stage_progress": copy.deepcopy((result.get("error") or {}).get("stage_progress", []))}
    if failure:
        refusal["final_delivery_failure"] = failure
    if not D._draft_eligible(result["status"], failure):
        outcome = {"status": "not_finished", "run_status": result["status"]}
    else:
        try:
            with job_lock(root):
                try:
                    export, source, selection = D._draft_export(root, scene, result)
                except Exception as exc:
                    evidence = {"checkpoint_attempts": exc.checkpoint_attempts} if isinstance(exc, D.DraftSourceError) else {}
                    outcome = {**refusal, "reason": f"{type(exc).__name__}: {exc}", **evidence}
                else:
                    from uuid import uuid4
                    D._draft_checks(root, export)
                    ar, (capture, raw, shown, contact) = D._draft_capture(root, export, "draft-" + uuid4().hex)
                    _, model = D._read(root, export["path"], export["sha256"])
                    manifest, files = D._package(
                        root, result, export, source["id"], source["fingerprint"],
                        result.get("exit_checkpoint") or {}, D._last_summary(root, result),
                        ar, model, capture, raw, shown, contact, source=source, selection=selection,
                        final_delivery_failure=failure)
                    outcome = D._publish(root, manifest, files)
        except Exception as exc:
            outcome = {**refusal, "status": "draft_failed", "reason": f"{type(exc).__name__}: {exc}"}
    write_json_atomic(root / "runs" / result["run_id"] / "delivery.json", outcome)
    return outcome


def write_summary(spec, result, delivered):
    directory = spec.run_dir
    attempts = []
    for path in (directory / "transport").glob("*.json"):
        row = json.loads(path.read_text(encoding="utf-8"))
        if row.get("schema") == "g3d.claude_transport/1":
            attempts.append(row)
    records = []
    for path in directory.rglob("image-manifest.jsonl"):
        records.extend(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines())
    for path in directory.rglob("input-receipt.json"):
        records.extend(json.loads(path.read_text(encoding="utf-8")).get("images", []))
    mentions = []
    for path in directory.rglob("*-response.json"):
        if path.parent.name != "requests":
            continue
        response = json.loads(path.read_text(encoding="utf-8"))
        public = _public_text(response)
        for row in records:
            candidates = {row.get(key) for key in ("file_id", "sha256", "source_sha256", "label", "call_id")}
            matched = sorted(value for value in candidates if isinstance(value, str)
                             and len(value) >= 6 and value in public)
            if matched:
                mention = {"response": str(path.relative_to(directory)), "file_id": row.get("file_id"),
                           "sha256": row.get("sha256"), "literal_matches": matched}
                if mention not in mentions:
                    mentions.append(mention)
    lens = {"method": "Native verified delivery GLB materials on primitives tagged partRole=lens.",
            "materials": [], "availability": "no verified delivery"}
    if delivered.get("status") == "delivered":
        try:
            from glasses3d.g3d_host.glb import load
            verified = D.load_delivery(spec.job_dir, delivered.get("delivery_id") or delivered.get("id"))
            model = load(verified["model"])
            seen = set()
            for primitive in model.primitives:
                node, mesh = model.nodes[primitive.node_index], model.meshes[primitive.mesh_index]
                if (node.get("extras") or {}).get("partRole", (mesh.get("extras") or {}).get("partRole")) != "lens":
                    continue
                identity = (primitive.node_index, primitive.material_index)
                if identity not in seen:
                    seen.add(identity)
                    lens["materials"].append({"node": primitive.node_name,
                                              "material_index": primitive.material_index,
                                              "material": model.material(primitive.material_index)})
            lens.update(availability="verified", glb_sha256=verified["glb_sha256"])
        except Exception as exc:
            lens["availability"] = f"unavailable: {type(exc).__name__}: {exc}"
    def endpoint(row):
        return row.get("url", "").split("?", 1)[0].removeprefix("https://api.anthropic.com")
    messages = [row for row in attempts if endpoint(row) == "/v1/messages"]
    summary = {
        "provider": "anthropic", "model": MODEL, "run_id": spec.run_id,
        "status": result["status"], "tool_test": spec.offline, "seconds": result["seconds"],
        "requests": result["requests"], "budget": result["budget"], "delivery": delivered,
        "request_counts": {"model_requests": result["requests"], "message_http_attempts": len(messages),
                           "retry_resends": sum(row.get("attempt", 1) > 1 for row in messages),
                           "retry_waits": sum(row.get("outcome") == "retry" for row in attempts),
                           "count_tokens": sum(endpoint(row) == "/v1/messages/count_tokens" for row in attempts),
                           "uploads": sum(endpoint(row) == "/v1/files" and row["method"] == "POST"
                                          for row in attempts)},
        "image_receipts": {"occurrences": len(records),
                           "unique_sha256": len({row["sha256"] for row in records}),
                           "unique_file_ids": len({row["file_id"] for row in records})},
        "public_image_references": mentions, "lens_material_choices": lens,
        "image_reference_method": (
            "Literal full file ID, SHA, tool call ID or label matches in public text blocks only; "
            "thinking and tool inputs excluded. Reused labels/call IDs may refer to multiple images; "
            "implicit references are not counted. Neither mentions nor receipts prove internal attention."),
    }
    write_json_atomic(directory / "summary.json", summary)
    (directory / "summary.md").write_text(
        f"Anthropic / {MODEL}\n\nStatus: {result['status']}. "
        f"{result['requests']} requests in {result['seconds']} seconds.\n\n"
        f"Settled money: {json.dumps(result['budget'])}.\n"
        f"\nDelivery: {delivered.get('delivery_id', delivered.get('id', delivered['status']))}; "
        f"GLB SHA-256: {delivered.get('glb_sha256', 'unavailable')}.\n"
        f"\nRequest counts: {json.dumps(summary['request_counts'])}.\n"
        f"\nImage receipts: {json.dumps(summary['image_receipts'])}. "
        f"{len(mentions)} literal public image references; {summary['image_reference_method']}\n"
        f"\nLens materials: {json.dumps(lens, ensure_ascii=False)}.\n"
        + ("\nOffline scripted tool test; no product verdict.\n" if spec.offline else ""),
        encoding="utf-8")
    return summary


async def execute_run(spec, *, scene=None, inner=None, **kwargs):
    scene = scene or JobScene(spec.job_dir)
    existed = (spec.run_dir / "result.json").exists()
    try:
        result = await run_claude(spec, scene=scene, inner=inner, **kwargs)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        if not existed and (spec.run_dir / "result.json").exists():
            result = json.loads((spec.run_dir / "result.json").read_text(encoding="utf-8"))
            delivered = await _owned_thread(publish_result, spec.job_dir, scene, result)
            await _owned_thread(write_summary, spec, result, delivered)
        raise
    delivered = await _owned_thread(publish_result, spec.job_dir, scene, result)
    await _owned_thread(write_summary, spec, result, delivered)
    return {"result": result, "delivery": delivered}
