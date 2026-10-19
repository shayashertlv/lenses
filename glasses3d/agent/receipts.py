"""Run receipts (glasses3d/plan/archive/20261019/WP2_DESIGN.md section 9): ``ReviewLog``, the port of the Blender Studio's agent.py:181-339.

One ``ReviewLog`` per run is both the httpx2 event hooks of the inference client and the Agents SDK ``RunHooks``:

* ``before_request`` (an httpx2 request hook): the stop check, then ``BudgetGuard.before_request`` (count, reserve,
  persist), and only then the archive of the exact serialized body as ``transport/NNNN-request.json``, where NNNN is
  the budget ledger row the guard just reserved (``guard.open_sequence``): a refused request leaves no file;
* ``after_response`` (an httpx2 response hook): the archive of the provider's own body as ``NNNN-response.json``,
  its public reasoning summaries, the transcript entry, and only then ``BudgetGuard.after_response`` (settle every
  usable 200 from its own usage, a 4xx at $0, anything else as unknown);
* ``on_llm_end`` cross-checks the SDK's parsed response against the settled row (``BudgetGuard.on_llm_end``);
* ``on_tool_start`` / ``on_tool_end`` write ``tools/NNNN-<tool>/{arguments,result}.json`` (+ ``issued-code.py``) and
  ``journal.json`` when the tool layer hands one over (``record_journal``);
* ``check_stop`` raises ``StopRequested`` at the next model request, request hook or tool start once the job's
  ``stop.requested`` exists (never after a tool finished: its turn must still be persisted).

Everything written passes the cleaner: a data-URL image becomes ``{sha256, bytes, mime}`` and its bytes are stored
once in the job's ``blobs/`` (``<sha256>.<ext>``); ``encrypted_content`` and ``signature`` values are masked; every
secret (the API key) is replaced by ``[REDACTED]``. Request headers are never archived. Files: ``events.jsonl``
(hook, body sha/size, request and response ids, status, usage), ``reasoning-summaries.jsonl`` (the API's public
summaries only, ``summary_record`` kept from the Studio's reasoning_evidence.py), ``transcript.md`` (the
owner-readable record: Astra's text, summaries where present, every tool call's arguments with code fenced, every
result's status, key numbers and image links).
"""
from __future__ import annotations

from .jsonl import append_jsonl

import base64
import binascii
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
import zlib
from typing import Any, Iterable

from agents import RunHooks
from agents.exceptions import AgentsException

from .budget import replace_with_retry

MASK = "[masked]"
REDACTED = "[REDACTED]"
MASKED_KEYS = frozenset({"encrypted_content", "signature"})
IMAGE_EXTENSIONS = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}
_DATA_IMAGE = re.compile(r"^data:(image/[A-Za-z0-9.+-]+);base64,", re.IGNORECASE)
_IMAGE_PATH = re.compile(r"\.(png|jpe?g|webp|gif)$", re.IGNORECASE)
_TOOL_DIR_NAME = re.compile(r"[^A-Za-z0-9_.-]+")
NON_JSON_LIMIT = 16_000
TRANSCRIPT_TEXT_LIMIT = 4_000
TRANSCRIPT_RESULT_LIMIT = 1_500


class StopRequested(AgentsException):
    """The owner asked the run to stop (``job stop`` wrote stop.requested). An AgentsException so the SDK passes it
    through unwrapped from tool hooks (tool_execution.py:1843-1844 re-raises AgentsException, wraps anything else)."""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def write_text_atomic(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}-",
                                         suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        replace_with_retry(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_json(path: Path, value) -> None:
    write_text_atomic(path, json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False, default=str) + "\n")


# --------------------------------------------------------------------------- blobs and cleaning


class BlobStore:
    """Content-addressed image bytes: ``<sha256><ext>``, written once (atomically), verified on read."""

    def __init__(self, directory):
        self.directory = Path(directory)

    def path(self, sha256: str, mime: str) -> Path:
        return self.directory / f"{sha256}{IMAGE_EXTENSIONS.get(mime.lower(), '.img')}"

    def put(self, payload: bytes, mime: str) -> dict:
        sha256 = hashlib.sha256(payload).hexdigest()
        path = self.path(sha256, mime)
        if not path.is_file() or path.stat().st_size != len(payload):
            self.directory.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile("wb", dir=self.directory, prefix=".blob-", suffix=".tmp",
                                                 delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                replace_with_retry(temporary, path)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        return {"sha256": sha256, "bytes": len(payload), "mime": mime.lower()}

    def get(self, sha256: str, mime: str) -> bytes:
        payload = self.path(sha256, mime).read_bytes()
        if hashlib.sha256(payload).hexdigest() != sha256:
            raise ValueError(f"blob {sha256[:12]} does not hash to its name")
        return payload

    def put_data_url(self, url: str) -> dict | None:
        """Store a canonical base64 data-URL image; None when the URL is not one (or not canonical base64)."""
        match = _DATA_IMAGE.match(url)
        if match is None:
            return None
        encoded = url[match.end():]
        try:
            payload = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            return None
        if not payload or base64.b64encode(payload).decode("ascii") != encoded:
            return None
        return self.put(payload, match[1])


class Cleaner:
    """JSON-safe copies with images moved to the blob store, private continuation data masked, secrets redacted."""

    def __init__(self, blobs: BlobStore, secrets: Iterable[str] = ()):
        self.blobs = blobs
        self.secrets = sorted({secret for secret in secrets if isinstance(secret, str) and len(secret) >= 8},
                              key=len, reverse=True)

    def redact(self, text: str) -> str:
        for secret in self.secrets:
            if secret in text:
                text = text.replace(secret, REDACTED)
        return text

    def image(self, text: str) -> dict:
        stored = self.blobs.put_data_url(text)
        if stored is not None:
            return stored
        match = _DATA_IMAGE.match(text)
        mime = match[1].lower() if match else "image/unknown"
        # malformed or non-canonical base64: keep its identity, never its text and never a blob
        return {"sha256": hashlib.sha256(text.encode("utf-8", "replace")).hexdigest(), "bytes": len(text),
                "mime": mime, "invalid_base64": True}

    def __call__(self, value):
        if hasattr(value, "model_dump"):
            try:
                value = value.model_dump(mode="json")
            except Exception:  # noqa: BLE001 - a receipt never fails on an odd object
                value = str(value)
        elif is_dataclass(value) and not isinstance(value, type):
            value = asdict(value)
        if isinstance(value, dict):
            cleaned = {}
            for key, item in value.items():
                key = self.redact(str(key))
                if "data:image" in key.lower():
                    key = "image-bearing-key:" + hashlib.sha256(key.encode()).hexdigest()
                cleaned[key] = MASK if key in MASKED_KEYS and item else self(item)
            return cleaned
        if isinstance(value, (list, tuple)):
            return [self(item) for item in value]
        if isinstance(value, str):
            value = self.redact(value)
            if _DATA_IMAGE.match(value):
                return self.image(value)
            if "data:image" in value.lower():
                return {**self.blobs.put(zlib.compress(value.encode("utf-8")), "application/x-g3d-text-zlib"),
                        "encoding": "zlib-utf8"}
            return value
        if isinstance(value, (bytes, bytearray)):
            return {"sha256": hashlib.sha256(bytes(value)).hexdigest(), "bytes": len(value)}
        if value is None or isinstance(value, (bool, int, float)):
            if isinstance(value, float) and value != value:  # NaN is not JSON
                return None
            return value
        return self(str(value))


def summary_record(response: dict, *, interaction: int, request_id: str | None) -> dict:
    """Only the API's public reasoning summaries, never internal reasoning (Studio reasoning_evidence.py, kept).

    Only function_call items are listed as actions: BudgetGuard refuses every non-function tool, so no hosted call
    type can occur (glasses3d/plan/archive/20261019/PORT_REVIEW.md defect note)."""
    summaries, actions = [], []
    for item in response.get("output") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "reasoning":
            text = [part["text"] for part in item.get("summary") or []
                    if isinstance(part, dict) and part.get("type") == "summary_text"
                    and isinstance(part.get("text"), str)]
            if text:
                summaries.append({"item_id": item.get("id"), "text": text})
        elif item.get("type") == "function_call":
            actions.append({"call_id": item.get("call_id"), "name": item.get("name")})
    return {"scope": "API-provided reasoning summaries; not raw internal reasoning",
            "interaction": interaction, "response_id": response.get("id"), "request_id": request_id,
            "response_status": response.get("status"), "summaries": summaries,
            "availability": "returned" if summaries else "not_returned", "tool_calls": actions}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fence(text: str, language: str = "") -> str:
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}{language}\n{text}\n{fence}"


def _short(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + f"\n... ({len(text) - limit} more characters)"


# --------------------------------------------------------------------------- the review log


class ReviewLog(RunHooks):
    """Transport receipts, SDK lifecycle events, per-call tool receipts and the owner transcript of one run."""

    def __init__(self, run_dir, *, blobs_dir, guard=None, secrets: Iterable[str] = (), stop_file=None,
                 heartbeat=None, run_id: str | None = None, echo: bool = False, job_dir=None):
        self.run_dir = Path(run_dir)
        self.job_dir = Path(job_dir) if job_dir is not None else Path(blobs_dir).parent   # tool paths are job-relative
        self.transport_dir = self.run_dir / "transport"
        self.tools_dir = self.run_dir / "tools"
        self.events_path = self.run_dir / "events.jsonl"
        self.summaries_path = self.run_dir / "reasoning-summaries.jsonl"
        self.transcript_path = self.run_dir / "transcript.md"
        self.blobs = BlobStore(blobs_dir)
        self.clean = Cleaner(self.blobs, secrets)
        self.guard = guard
        self.stop_file = Path(stop_file) if stop_file is not None else None
        self.heartbeat = heartbeat
        self.run_id = run_id or self.run_dir.name
        self.echo = echo
        self.interactions = 0               # model requests started (on_llm_start): the SDK's turns
        self.sequence = 0                   # event sequence
        self.started = time.perf_counter()
        self.stop_observed = False
        self.last_response: dict | None = None
        self.responses: list[dict] = []     # one row per archived provider response
        self.tool_calls: list[dict] = []    # one row per tool call: {n, call_id, name, status}
        self._tool_count = 0
        self._tool_dirs: dict[str, Path] = {}
        self._tool_starts: dict[str, float] = {}
        self._inflight: int | None = None
        self._unsequenced = 0
        self.transcript_errors: list[str] = []
        self.run_dir.mkdir(parents=True, exist_ok=True)
        if not self.transcript_path.exists():
            self._transcript(f"# Astra run {self.run_id}\n\nOwner-readable record of this run: Astra's replies, "
                             "public reasoning summaries where the API returned one, every tool call and its "
                             "result. Images are stored once in the job's blobs/ folder.\n")

    # -- files
    def _append(self, path: Path, value) -> None:
        recovery = append_jsonl(path, self.clean(value), default=str)
        if recovery:
            write_json(path.with_name(path.name + ".recovery.json"), recovery)

    def _transcript(self, text: str) -> None:
        cleaned = self.clean(text)
        if not isinstance(cleaned, str):
            cleaned = "Text stored in blob: " + json.dumps(cleaned, ensure_ascii=False)
        with self.transcript_path.open("a", encoding="utf-8") as stream:
            stream.write(cleaned.rstrip("\n") + "\n\n")

    def _derived(self, write, *args) -> None:
        """The transcript is derived from the JSON receipts: a failure writing it is recorded, never fatal."""
        try:
            write(*args)
        except Exception as error:  # noqa: BLE001
            self.transcript_errors.append(f"{type(error).__name__}: {str(error)[:200]}")

    def _relative(self, path: Path) -> str:
        try:
            return os.path.relpath(path, self.run_dir).replace(os.sep, "/")
        except ValueError:          # another drive (Windows): keep the path as it is
            return str(path).replace(os.sep, "/")

    def event(self, kind: str, **fields) -> dict:
        self.sequence += 1
        item = {"time": now_iso(), "event": kind, "sequence": self.sequence, "interaction": self.interactions,
                "elapsed_s": round(time.perf_counter() - self.started, 6), **fields}
        self._append(self.events_path, item)
        if self.echo:
            print(kind + (f": {fields['name']}" if "name" in fields else ""), flush=True)
        return item

    def note(self, text: str, **fields) -> None:
        """A host line in the transcript (continuation, rollback, stop, provider outcome) plus its event."""
        self._derived(self._transcript, f"> **Host:** {text}")
        self.event("host_note", text=text, **fields)

    def _best_effort_note(self, event: str, error: BaseException, text: str) -> None:
        """Record why the guard stopped a request or a response; never replaces the guard's own exception."""
        try:
            self.event(event, refusal=type(error).__name__, reason=str(error)[:500])
            self._transcript(f"> **Host:** {text}.")
        except Exception:  # noqa: BLE001 - the caller re-raises the original error
            pass

    def _payload_path(self, sequence: int | None, kind: str) -> Path:
        if sequence is None:
            self._unsequenced += 1
            return self.transport_dir / f"unsequenced-{self._unsequenced:04d}-{kind}.json"
        return self.transport_dir / f"{sequence:04d}-{kind}.json"

    def _save_payload(self, sequence: int | None, kind: str, payload) -> str:
        path = self._payload_path(sequence, kind)
        write_json(path, self.clean(payload))
        return self._relative(path)

    def _heartbeat(self, state: str | None = None, **fields) -> None:
        if self.heartbeat is None:
            return
        if state is not None:
            self.heartbeat.set_state(state, **fields)
        elif fields:
            self.heartbeat.update(**fields)

    def _publish_money(self) -> None:
        if self.heartbeat is None or self.guard is None:
            return
        summary = self.guard.summary()
        self.heartbeat.update(settled_usd=summary["settled_usd"], reserved_usd=summary["held_usd"],
                              unknown_usd=summary["unknown_usd"], requests=summary["requests"],
                              context_tokens=summary["planning"]["last_counted_input_tokens"])

    # -- stop
    def stop_requested(self) -> bool:
        return self.stop_file is not None and self.stop_file.is_file()

    def check_stop(self) -> None:
        """Cooperative stop at the next model request, request hook or tool start."""
        if self.stop_requested():
            if not self.stop_observed:
                self.stop_observed = True
                self.event("stop_requested", stop_file=self.stop_file.name)
            raise StopRequested("the owner requested a stop (stop.requested)")

    # -- transport hooks
    async def before_request(self, request) -> None:
        """Stop check, then reserve (BudgetGuard), then archive the exact serialized body without headers."""
        self.check_stop()
        if self.guard is None:
            raise RuntimeError("ReviewLog has no budget guard; no request may be sent")
        try:
            await self.guard.before_request(request)
        except Exception as error:
            from .renewal import RenewalNeeded
            if isinstance(error, RenewalNeeded):
                # Intentional pre-admission handoff, with no hold or HTTP send.
                self.event("conversation_pressure", measurements=error.measurements, reasons=error.reasons)
                raise
            try:
                await self.guard.not_sent(error, request)
            except Exception:
                pass  # failed disk write retains the hold; it cannot create a spending allowance
            self._best_effort_note("request_refused", error,
                                   f"the budget guard did not send the next request ({error})")
            raise
        sequence = self.guard.open_sequence
        body = request.content
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeError):
            payload = {"non_json_body": body.decode("utf-8", "replace")[:NON_JSON_LIMIT]}
        try:
            path = self._save_payload(sequence, "request", payload)
            self.event("api_request", request_seq=sequence, payload_path=path, serialized_sha256=_sha(body),
                       serialized_bytes=len(body))
            self._heartbeat(request_seq=sequence)
            self._publish_money()
        except BaseException as error:
            try:
                await self.guard.not_sent(error, request)
            except Exception:
                pass
            raise
        self._inflight = sequence

    async def after_response(self, response):
        """Archive the provider's own body before the SDK parses it, then settle it (BudgetGuard)."""
        body = await response.aread()
        sequence = self._inflight
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeError):
            payload = {"non_json_body": body.decode("utf-8", "replace")[:NON_JSON_LIMIT]}
        try:
            path = self._save_payload(sequence, "response", payload)
            metadata = payload if isinstance(payload, dict) else {}
            request_id = response.headers.get("x-request-id")
            if response.status_code == 200 and isinstance(metadata.get("output"), list):
                self._append(self.summaries_path, summary_record(metadata, interaction=self.interactions,
                                                                 request_id=request_id))
            row = {"sequence": sequence, "http_status": response.status_code, "request_id": request_id,
                   "response_id": metadata.get("id"), "status": metadata.get("status"), "payload_path": path}
            self.last_response = row
            self.responses.append(row)
            self.event("api_response", request_seq=sequence, payload_path=path, http_status=response.status_code,
                       request_id=request_id, response_id=metadata.get("id"), status=metadata.get("status"),
                       incomplete_details=metadata.get("incomplete_details"), usage=metadata.get("usage"),
                       serialized_sha256=_sha(body), serialized_bytes=len(body),
                       archived_sha256=_sha((self.run_dir / path).read_bytes()))
            self._derived(self._transcript_response, sequence, response.status_code, metadata, payload)
        finally:
            try:
                if self.guard is not None:
                    await self.guard.after_response(response)
            except Exception as error:
                self._best_effort_note("response_not_accepted", error,
                                       f"the budget guard did not accept this response ({error})")
                raise
            finally:
                self._inflight = None
                self._publish_money()

    # -- SDK lifecycle hooks
    async def on_llm_start(self, context, agent, system_prompt, input_items) -> None:
        self.check_stop()
        self.interactions += 1
        self.event("model_thinking")
        self._heartbeat("model_wait", tool_call_id=None)

    async def on_llm_end(self, context, agent, response) -> None:
        self.event("model_response", response_id=getattr(response, "response_id", None),
                   request_id=getattr(response, "request_id", None), usage=getattr(response, "usage", None))
        self._heartbeat("idle")
        if self.guard is not None:
            await self.guard.on_llm_end(response)

    async def on_tool_start(self, context, agent, tool) -> None:
        self.check_stop()
        call_id = getattr(context, "tool_call_id", None)
        raw = getattr(context, "tool_arguments", None)
        self._tool_count += 1
        directory = self.tools_dir / f"{self._tool_count:04d}-{_TOOL_DIR_NAME.sub('_', tool.name)[:60]}"
        directory.mkdir(parents=True, exist_ok=True)
        key = call_id or f"{tool.name}-{self._tool_count}"
        self._tool_dirs[key] = directory
        self._tool_starts[key] = time.perf_counter()
        arguments, code = self._split_arguments(raw)
        receipt = {"call_id": call_id, "tool": tool.name, "n": self._tool_count, "arguments": arguments}
        if code is not None:
            receipt["code_sha256"] = _sha(code.encode("utf-8"))
            receipt["code_chars"] = len(code)
            cleaned_code = self.clean(code)
            if not isinstance(cleaned_code, str):
                receipt["code_blob"] = cleaned_code
                cleaned_code = "# Original code stored in blob: " + json.dumps(cleaned_code) + "\n"
            write_text_atomic(directory / "issued-code.py", cleaned_code)
        write_json(directory / "arguments.json", self.clean(receipt))
        self.tool_calls.append({"n": self._tool_count, "call_id": call_id, "name": tool.name, "status": None})
        self.event("tool_started", name=tool.name, tool_call_id=call_id, tool_dir=self._relative(directory),
                   arguments=arguments, code_sha256=receipt.get("code_sha256"))
        self._derived(self._transcript_tool_call, tool.name, call_id, arguments, code)
        self._heartbeat(f"tool:{tool.name}", tool_call_id=call_id)

    async def on_tool_end(self, context, agent, tool, result) -> None:
        call_id = getattr(context, "tool_call_id", None)
        key = call_id or f"{tool.name}-{self._tool_count}"
        directory = self._tool_dirs.get(key)
        if directory is None:       # an end without a start (never seen on 0.22.3): still leave a receipt
            self._tool_count += 1
            directory = self.tools_dir / f"{self._tool_count:04d}-{_TOOL_DIR_NAME.sub('_', tool.name)[:60]}"
            directory.mkdir(parents=True, exist_ok=True)
        started = self._tool_starts.pop(key, None)
        seconds = round(time.perf_counter() - started, 6) if started is not None else None
        cleaned = self.clean(result)
        status = cleaned.get("status") if isinstance(cleaned, dict) else None
        write_json(directory / "result.json", {"call_id": call_id, "tool": tool.name, "seconds": seconds,
                                               "result": cleaned})
        for row in reversed(self.tool_calls):
            if row["call_id"] == call_id and row["name"] == tool.name:
                row["status"] = status if status is not None else "returned"
                break
        self.event("tool_finished", name=tool.name, tool_call_id=call_id, status=status, duration_s=seconds,
                   result=cleaned)
        self._derived(self._transcript_tool_result, tool.name, call_id, cleaned, seconds)
        self._heartbeat("idle", tool_call_id=None)

    def record_journal(self, call_id: str | None, journal: dict) -> Path | None:
        """The tool layer's per-call journal ({checkpoint_before, fingerprint before/after, seconds, status})."""
        directory = self._tool_dirs.get(call_id) if call_id is not None else None
        if directory is None:
            return None
        path = directory / "journal.json"
        write_json(path, self.clean(journal))
        return path

    @staticmethod
    def _split_arguments(raw) -> tuple[Any, str | None]:
        if not isinstance(raw, str):
            return raw, None
        try:
            value = json.loads(raw)
        except ValueError:
            return {"unparsed_arguments": raw[:TRANSCRIPT_TEXT_LIMIT]}, None
        if isinstance(value, dict) and isinstance(value.get("code"), str):
            value = dict(value)
            code = value.pop("code")
            return value, code
        return value, None

    # -- transcript
    def _transcript_response(self, sequence, status: int, metadata: dict, payload) -> None:
        label = f"request {sequence}" if sequence is not None else "an unreserved request"
        if status != 200:
            error = metadata.get("error") if isinstance(metadata.get("error"), dict) else {}
            code = error.get("code") or error.get("type") or ""
            outcome = ("rejected, not billed" if 400 <= status < 500 else
                       "outcome unknown: the full hold stays until the owner reconciles it")
            self._transcript(f"## Provider answer to {label}: HTTP {status}" + (f" ({code})" if code else "")
                             + f"\n\n{outcome}.")
            return
        response_status = metadata.get("status")
        heading = f"## Astra, response {metadata.get('id')} ({label}, {response_status})"
        lines = [heading]
        for item in metadata.get("output") or []:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "reasoning":
                for part in item.get("summary") or []:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        lines.append("**Reasoning summary:** " + _short(part["text"], TRANSCRIPT_TEXT_LIMIT))
            elif item.get("type") == "message":
                for part in item.get("content") or []:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        lines.append("**Astra:** " + _short(part["text"], TRANSCRIPT_TEXT_LIMIT))
            elif item.get("type") == "function_call":
                lines.append(f"**Calls** `{item.get('name')}` (call `{item.get('call_id')}`)")
        if response_status == "incomplete":
            reason = (metadata.get("incomplete_details") or {}).get("reason")
            lines.append(f"This response was cut off ({reason}); it is billed but not recorded in the session, "
                         "and no tool ran for it.")
        if not isinstance(payload, dict):
            lines.append("The 200 body is not a JSON object; its outcome is unknown.")
        self._transcript("\n\n".join(lines))

    def _transcript_tool_call(self, name: str, call_id, arguments, code: str | None) -> None:
        lines = [f"### Tool `{name}` (call `{call_id}`)"]
        if isinstance(arguments, dict):
            shown = {key: value for key, value in arguments.items()}
            if shown:
                lines.append("Arguments:\n" + _fence(_short(json.dumps(self.clean(shown), indent=2,
                                                                         ensure_ascii=False, default=str),
                                                            TRANSCRIPT_TEXT_LIMIT), "json"))
        elif arguments is not None:
            lines.append("Arguments: " + _short(str(self.clean(arguments)), TRANSCRIPT_TEXT_LIMIT))
        if code is not None:
            lines.append("Code:\n" + _fence(self.clean.redact(code), "python"))
        self._transcript("\n\n".join(lines))

    def _image_links(self, value, found: list[str]) -> None:
        if len(found) >= 20:
            return
        if isinstance(value, dict):
            if isinstance(value.get("sha256"), str) and str(value.get("mime", "")).startswith("image/") \
                    and not value.get("invalid_base64"):
                found.append(self._relative(self.blobs.path(value["sha256"], value["mime"])))
                return
            for item in value.values():
                self._image_links(item, found)
        elif isinstance(value, list):
            for item in value:
                self._image_links(item, found)
        elif isinstance(value, str) and _IMAGE_PATH.search(value) and len(value) < 400:
            path = Path(value)
            found.append(value.replace("\\", "/") if path.is_absolute() else self._relative(self.job_dir / path))

    def _transcript_tool_result(self, name: str, call_id, result, seconds) -> None:
        lines = [f"Result of `{name}` (call `{call_id}`)" + (f", {seconds:.2f} s" if seconds is not None else "")
                 + ":"]
        if isinstance(result, dict):
            if "status" in result:
                lines.append(f"- status: `{result['status']}`")
            shown = 0
            for key, value in result.items():
                if key == "status" or shown >= 12:
                    continue
                if isinstance(value, (bool, int, float)) or value is None or (
                        isinstance(value, str) and len(value) <= 80 and not _IMAGE_PATH.search(value)):
                    lines.append(f"- {key}: `{value}`")
                    shown += 1
        elif isinstance(result, str):
            lines.append(_fence(_short(result, TRANSCRIPT_RESULT_LIMIT)))
        elif isinstance(result, list):
            for item in result[:12]:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    lines.append(_fence(_short(item["text"], TRANSCRIPT_RESULT_LIMIT)))
        links: list[str] = []
        self._image_links(result, links)
        if links:
            lines.append("Images: " + ", ".join(f"[{Path(link).name}]({link})" for link in links))
        self._transcript("\n".join(lines))
