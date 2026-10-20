"""Durable Claude Messages history, local context forecasting and renewal.

Raw replay records never pass through ReviewLog's presentation cleaner. The
existing BlobSession transaction is the only operation that changes epochs.
"""
from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import io
import json
import os
from pathlib import Path

from PIL import Image

from glasses3d.agent.jsonl import append_jsonl
from glasses3d.agent.loop import BlobSession
from glasses3d.agent.receipts import BlobStore, ReviewLog, write_json, write_text_atomic
from glasses3d.agent.renewal import (
    ASTRA_SUMMARY_PREFIX, FINISH_RESTART_NOTICE, HOST_STATE_PREFIX, NO_SUMMARY,
    RENEWAL_NOTICE, SUMMARY_REQUEST, durable_receipts,
)
from glasses3d.agent.session_inputs import plan_session_input

from .images import LIMITS, FileCache, Tile, check_request, fits, image_block, visual_tokens


CONTEXT_BETA = "context-management-2025-06-27"
SOFT_IMAGES = 480
SOFT_BYTES = 25_600_000
SOFT_TOKENS = 800_000
HARD_TOKENS = 1_000_000
CLAUDE_SUMMARY_PREFIX = ASTRA_SUMMARY_PREFIX.replace("Astra", "Claude")
CLAUDE_NO_SUMMARY = NO_SUMMARY.replace("Astra", "Claude")


def canonical_bytes(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _sha(value) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _blocks(message) -> list:
    content = message.get("content")
    return content if isinstance(content, list) else []


def pending_tool_ids(messages: list[dict]) -> list[str]:
    """Validate complete ordered call/result groups and return a dangling tail."""
    pending, seen = [], set()
    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in {"user", "assistant"}:
            raise ValueError("history must contain user/assistant Messages objects")
        content = message.get("content")
        if not isinstance(content, (str, list)):
            raise ValueError("message content must be text or an array")
        blocks = _blocks(message)
        if any(not isinstance(block, dict) for block in blocks):
            raise ValueError("message content blocks must be objects")
        results = [b for b in blocks if b.get("type") == "tool_result"]
        calls = [b for b in blocks if b.get("type") == "tool_use"]
        if pending:
            result_ids = [b.get("tool_use_id") for b in results]
            if (message["role"] != "user" or result_ids != pending
                    or blocks[:len(results)] != results):
                raise ValueError("tool results must immediately follow all calls in their original order")
            pending = []
        elif results:
            raise ValueError("tool result has no pending call")
        if calls:
            if message["role"] != "assistant":
                raise ValueError("tool calls belong to assistant messages")
            for call in calls:
                call_id = call.get("id")
                if not isinstance(call_id, str) or not call_id or call_id in seen:
                    raise ValueError("tool call IDs must be nonempty and unique within an epoch")
                seen.add(call_id)
                pending.append(call_id)
    return pending


class Conversation:
    """Append-only public surface around BlobSession, with durable prefix checks."""

    def __init__(self, db_path: Path, *, blobs: BlobStore, root_id: str = "claude"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._session = BlobSession(root_id, self.db_path, blobs=blobs)
        self._lock = asyncio.Lock()
        with self._session._write_connection() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS g3d_claude_request_prefixes "
                "(id INTEGER PRIMARY KEY, conversation_id TEXT NOT NULL, policy TEXT NOT NULL, "
                "message_hashes TEXT NOT NULL, receipt TEXT NOT NULL)"
            )
            conn.commit()

    @property
    def session_id(self) -> str:
        return self._session.session_id

    async def messages(self) -> list[dict]:
        # SQLiteSession.get_items intentionally skips invalid JSON rows. A
        # signed Messages prefix must fail closed instead of silently shrinking.
        session_id = self.session_id

        def read():
            with self._session._locked_connection() as conn:
                rows = conn.execute(
                    "SELECT message_data FROM agent_messages WHERE session_id=? ORDER BY id",
                    (session_id,),
                ).fetchall()
            return [self._session._rehydrate(json.loads(row[0])) for row in rows]

        return await asyncio.to_thread(read)

    async def append(self, message: dict) -> None:
        value = json.loads(canonical_bytes(message))
        async with self._lock:
            items = await self.messages()
            pending_tool_ids([*items, value])
            await self._session.add_items([value])

    async def assert_request_prefix(self, body: dict) -> dict:
        """Check replay bytes against durable history and the preceding request.

        Message SHA-256 values represent canonical JSON, retaining every unknown
        field and signature. The immutable policy is stored verbatim for equality.
        A new check is appended; previous request records are never updated.
        """
        body = json.loads(canonical_bytes(body))
        async with self._lock:
            messages = body.get("messages")
            if not isinstance(messages, list) or messages != await self.messages():
                raise ValueError("request messages differ from durable conversation history")
            if pending_tool_ids(messages):
                raise ValueError("cannot request inference with a pending tool call")
            policy = {k: v for k, v in body.items() if k != "messages"}
            policy_json = canonical_bytes(policy).decode("utf-8")
            hashes = [_sha(message) for message in messages]
            session_id = self.session_id

            def persist():
                with self._session._write_connection() as conn:
                    previous = conn.execute(
                        "SELECT policy, message_hashes FROM g3d_claude_request_prefixes "
                        "WHERE conversation_id=? ORDER BY id DESC LIMIT 1", (session_id,),
                    ).fetchone()
                    prior = json.loads(previous[1]) if previous else []
                    if previous and (previous[0] != policy_json or hashes[:len(prior)] != prior):
                        raise ValueError("request changed its immutable policy or prior message prefix")
                    receipt = {
                        "schema": "g3d.claude_request_prefix/1", "conversation_id": session_id,
                        "previous_messages": len(prior), "messages": len(hashes),
                        "prefix_matches": True, "policy_sha256": _sha(policy),
                        "messages_sha256": _sha(messages), "request_sha256": _sha(body),
                    }
                    conn.execute(
                        "INSERT INTO g3d_claude_request_prefixes "
                        "(conversation_id, policy, message_hashes, receipt) VALUES (?, ?, ?, ?)",
                        (session_id, policy_json, json.dumps(hashes), json.dumps(receipt)),
                    )
                    conn.commit()
                    return receipt

            return await asyncio.to_thread(persist)

    async def renew(self, messages: list[dict], *, receipt: dict) -> str:
        value = json.loads(canonical_bytes(messages))
        if not value or any(message.get("role") != "user" for message in value):
            raise ValueError("a renewed epoch must begin with fresh user content only")
        if any(block.get("type") in {"thinking", "redacted_thinking", "tool_use", "tool_result"}
               for message in value for block in _blocks(message)):
            raise ValueError("signed thinking and old call/result blocks cannot cross renewal")
        if pending_tool_ids(value):
            raise ValueError("new epoch has pending calls")
        async with self._lock:
            if pending_tool_ids(await self.messages()):
                raise ValueError("finish pending tool call/result pairs before renewal")
            return await self._session.renew(value, receipt=receipt)

    async def recovery_results(self) -> list[dict]:
        """Project completed IDs across all epochs for unchanged plan_rollback."""
        return [
            {"type": "function_call_output", "call_id": block["tool_use_id"]}
            for message in await self._session.recovery_items()
            for block in _blocks(message)
            if message.get("role") == "user" and block.get("type") == "tool_result"
            and isinstance(block.get("tool_use_id"), str)
        ]

    async def repair_dangling_calls(self) -> list[str]:
        """Append crash results only after the caller planned scene rollback."""
        pending = pending_tool_ids(await self.messages())
        if pending:
            await self.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": call_id, "is_error": True,
                 "content": [{"type": "text", "text": "The host was interrupted before this tool result "
                              "was persisted. Scene recovery is recorded by the host."}]}
                for call_id in pending
            ]})
        return pending

    def close(self) -> None:
        self._session.close()


def _opening_content(spec, *, files: FileCache, invocation_text: str) -> tuple[list[dict], dict]:
    plan = plan_session_input([], prompt=spec.brief, photos=spec.photos,
                              invocation_text=invocation_text,
                              owner_notes=getattr(spec, "owner_notes", ()))
    content, manifests = [], []
    label = ""
    tile_store = files.path.parent / "tiles"
    for block in plan.content:
        if block["type"] == "input_text":
            label = block["text"]
            content.append({"type": "text", "text": label})
        elif block["type"] == "input_image":
            raw = base64.b64decode(block["image_url"].split(",", 1)[1], validate=True)
            with Image.open(io.BytesIO(raw)) as source:
                source.load()
                w, h = source.size
            if not fits(w, h, many=True):
                raise ValueError("owner reference must fit whole at native pixels throughout the conversation")
            digest = hashlib.sha256(raw).hexdigest()
            tile = Tile(raw, w, h, 0, 0, label, digest)
            file_id = files.file_id_for(tile)
            tile_store.mkdir(parents=True, exist_ok=True)
            target = tile_store / digest
            if target.exists():
                if target.read_bytes() != raw:
                    raise ValueError("stored owner-reference bytes differ from their SHA")
            else:
                with target.open("xb") as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
            content.append(image_block(file_id))
            manifests.append({
                "source_sha256": digest, "tile_sha256": digest, "sha256": digest,
                "source_size_px": [w, h], "size_px": [w, h], "w": w, "h": h,
                "x0": 0, "y0": 0, "x1": w, "y1": h, "bytes": len(raw),
                "label": label, "file_id": file_id, "tile_path": str(target),
                "kind": "owner_reference",
            })
    return content, {**plan.receipt, "provider": "anthropic", "images": manifests}


def opening_content(spec, *, files: FileCache) -> tuple[list[dict], dict]:
    return _opening_content(spec, files=files, invocation_text=getattr(
        spec, "invocation_text", "Continue the task using the supplied instructions, tools and owner inputs."
    ))


def request_body(spec, messages: list[dict], *, tools: list[dict]) -> dict:
    """Build the fixed inference policy. No free counting request belongs here."""
    settings = spec.settings
    return copy.deepcopy({
        "model": settings.model, "max_tokens": settings.max_tokens,
        "thinking": {"type": "adaptive", "display": "summarized"},
        "output_config": {"effort": settings.effort},
        "service_tier": "standard_only", "speed": "standard", "inference_geo": "global",
        "tool_choice": {"type": "auto", "disable_parallel_tool_use": True},
        "cache_control": {"type": "ephemeral", "ttl": "1h"},
        "context_management": {"edits": [{
            "type": "clear_tool_uses_20250919",
            "trigger": {"type": "input_tokens", "value": 350_000},
            "clear_at_least": {"type": "input_tokens", "value": 150_000},
            "keep": {"type": "tool_uses", "value": 8}, "clear_tool_inputs": False,
        }]},
        "system": spec.instructions, "tools": tools, "messages": messages, "stream": True,
    })


def _image_blocks(value):
    if isinstance(value, list):
        for item in value:
            yield from _image_blocks(item)
    elif isinstance(value, dict):
        if value.get("type") == "image":
            yield value
        else:
            for item in value.values():
                yield from _image_blocks(item)


def estimate_content(content, dims: dict[str, tuple[int, int]]) -> dict:
    """Conservative local text bytes/3 plus native image patches, never a count API."""
    text_bytes = text_tokens = image_tokens = images = 0

    def visit(value):
        nonlocal text_bytes, text_tokens, image_tokens, images
        if isinstance(value, str):
            size = len(value.encode("utf-8"))
            text_bytes += size
            text_tokens += (size + 2) // 3
        elif isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            if value.get("type") == "image":
                source = value.get("source") or {}
                dimensions = dims.get(source.get("file_id"))
                if source.get("type") != "file" or dimensions is None:
                    raise ValueError("context estimate needs known file-image dimensions")
                image_tokens += visual_tokens(*dimensions)
                images += 1
            elif "content" in value:
                visit(value["content"])
            elif value.get("type") in {"text", "input_text"}:
                visit(value.get("text", ""))
            elif value.get("type") == "thinking":
                visit(value.get("thinking", ""))
            elif value.get("type") == "tool_use":
                visit(canonical_bytes(value.get("input", {})).decode("utf-8"))
            else:
                # System/tool definitions in a fresh epoch are text overhead.
                visit(canonical_bytes(value).decode("utf-8"))

    visit(content)
    return {"text_bytes": text_bytes, "text_tokens": text_tokens,
            "image_tokens": image_tokens, "images": images,
            "total_tokens": text_tokens + image_tokens}


def _counter(value, name):
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
    return value


def context_measurement(*, last_usage: dict | None, appended_estimate: dict,
                        opening_count: dict | None = None) -> dict:
    """Forecast next input from settled usage; clearing is already in that usage."""
    new_tokens = _counter(appended_estimate.get("total_tokens", 0), "new content estimate")
    anchor = output = 0
    if last_usage is not None:
        anchor = sum(_counter(0 if last_usage.get(key) is None else last_usage[key], key) for key in (
            "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
        output = _counter(last_usage.get("output_tokens"), "output_tokens")
    return {
        "method": "settled_usage_plus_local_content" if last_usage is not None else "local_opening_estimate",
        "anchor_input_tokens": anchor if last_usage is not None else None,
        "anchor_output_tokens": output if last_usage is not None else None,
        "new_content": copy.deepcopy(appended_estimate),
        "forecast_tokens": anchor + output + new_tokens,
        "uncertainty": "Local UTF-8 bytes/3 and image patches; tokenization and wrapper overhead are estimated.",
        "count_tokens_called": False,
        **({"external_opening_count": copy.deepcopy(opening_count)} if opening_count is not None else {}),
    }


def pressure(body: dict, *, dims: dict[str, tuple[int, int]],
             measurement: dict, settings=None) -> list[dict]:
    """Soft reasons only; caller must also apply images.check_request hard checks."""
    size = len(json.dumps(body, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"))
    images = sum(1 for _ in _image_blocks(body.get("messages", [])))
    forecast = _counter(measurement["forecast_tokens"], "forecast_tokens")
    values = (("images", images, SOFT_IMAGES, LIMITS["max_images"], images > SOFT_IMAGES),
              ("payload_bytes", size, SOFT_BYTES, LIMITS["max_request_bytes"], size >= SOFT_BYTES),
              ("input_tokens", forecast, SOFT_TOKENS, HARD_TOKENS, forecast > SOFT_TOKENS))
    return [{"reason": name, "measured": value, "threshold": threshold, "limit": limit}
            for name, value, threshold, limit, triggered in values if triggered]


async def renew_conversation(runtime, trigger: dict) -> dict:
    """Summarize with the shared request path, then atomically carry current evidence."""
    from .tool_adapter import prepare_images, tool_definitions

    conversation = runtime.conversation
    old_items = await conversation.messages()
    if pending_tool_ids(old_items):
        raise ValueError("cannot renew before pending tool results are durable")
    old_id, old_hash = conversation.session_id, _sha(old_items)
    summary, skipped, response = "", None, None
    summary_item = {"role": "user", "content": [{"type": "text", "text": SUMMARY_REQUEST}]}
    append = getattr(runtime, "append", conversation.append)
    await append(summary_item)
    definitions = getattr(runtime, "definitions", None)
    if definitions is None:
        definitions = tool_definitions(runtime.spec.tools)
    candidate = request_body(runtime.spec, await conversation.messages(), tools=definitions)
    dims = runtime.dimensions()
    problems = check_request(candidate, dims)
    last_usage = getattr(runtime, "last_usage", None)
    appended_estimate = getattr(runtime, "appended_estimate", None)
    if last_usage is None or appended_estimate is None:
        appended_estimate = estimate_content(
            [candidate["system"], candidate["tools"], *candidate["messages"]], dims)
    measured = context_measurement(last_usage=last_usage, appended_estimate=appended_estimate)
    if measured["forecast_tokens"] > HARD_TOKENS:
        problems.append(f"forecast input exceeds {HARD_TOKENS} tokens")
    allowed, reason = runtime.ledger.may_start_request()
    if not allowed:
        if "cutoff" not in reason:
            raise RuntimeError("renewal summary cannot continue: " + reason)
        skipped = "settled cutoff: " + reason
    elif problems:
        skipped = "summary exceeds hard request limits: " + "; ".join(problems)
    else:
        response = await runtime.request(summary=True)
        stop = response.get("stop_reason")
        if stop not in {"end_turn", "tool_use"} or response.get("stop_details"):
            raise RuntimeError(f"renewal summary ended with {stop}: no fresh request is permitted")
        calls = [block for block in response.get("content", []) if block.get("type") == "tool_use"]
        if calls:
            await append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": block["id"], "is_error": True,
                 "content": [{"type": "text", "text": "No tool was executed: the host requested only "
                              "a state summary for conversation renewal."}]}
                for block in calls
            ]})
        summary = "\n".join(block["text"] for block in response.get("content", [])
                            if block.get("type") == "text" and isinstance(block.get("text"), str)).strip()
        if not summary:
            skipped = "summary response contained no written state"
        runtime.log.event("renewal_summary_response", response_id=response.get("id"), stop_reason=stop,
                          tool_calls_not_executed=[block["id"] for block in calls])
    after = await conversation.messages()
    if after[:len(old_items)] != old_items:
        raise ValueError("conversation prefix changed while obtaining the summary")

    evidence = runtime.scene.renewal_evidence()

    def prepare_evidence():
        evidence_blocks, evidence_rows = [], []
        for record in evidence:
            raw = Path(record["path"]).read_bytes()
            if hashlib.sha256(raw).hexdigest() != record["sha256"]:
                raise ValueError("renewal evidence bytes differ from their recorded hash")
            with Image.open(io.BytesIO(raw)) as source:
                mime = Image.MIME.get(source.format)
            if mime not in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
                raise ValueError("renewal evidence has unsupported image encoding")
            label = (f"Renewal evidence: call {record['call_id']}; checkpoint {record.get('checkpoint')}; "
                     f"view {record.get('view')}; kind {record.get('kind')}; sha256 {record['sha256']}.")
            blocks, rows = prepare_images([
                {"type": "input_text", "text": label},
                {"type": "input_image", "image_url": "data:" + mime + ";base64,"
                 + base64.b64encode(raw).decode("ascii")},
            ], files=runtime.files, metadata={**record, "phase": "renewal"})
            evidence_blocks.extend(blocks)
            evidence_rows.extend(rows)
        return evidence_blocks, evidence_rows

    evidence_blocks, evidence_rows = await asyncio.to_thread(prepare_evidence)
    restart = runtime.scene.renewal_started()
    scene_state = runtime.scene.renewal_state()
    state = {**scene_state, "finish_review_renewal": restart,
             "money": runtime.ledger.money_status(), "tool_calls_so_far": list(runtime.log.tool_calls),
             "owner_requests_supplied_for_this_run": list(getattr(runtime.spec, "owner_notes", ()))}
    notice = RENEWAL_NOTICE + (" " + FINISH_RESTART_NOTICE if restart.get("finish_review_restarted") else "")
    content, input_receipt = await asyncio.to_thread(
        _opening_content, runtime.spec, files=runtime.files, invocation_text=notice)
    content.extend([
        {"type": "text", "text": CLAUDE_SUMMARY_PREFIX + summary if summary else CLAUDE_NO_SUMMARY},
        {"type": "text", "text": HOST_STATE_PREFIX + json.dumps(state, ensure_ascii=False)},
        *evidence_blocks,
    ])
    row = {
        "schema": "g3d.conversation_renewal/1", "provider": "anthropic",
        "model": runtime.spec.settings.model, "number": len(runtime.renewals) + 1,
        "run_id": runtime.log.run_id, "receipt_directory": str(runtime.directory),
        "old_conversation_id": old_id, "old_items": len(old_items), "old_items_sha256": old_hash,
        "reasons": copy.deepcopy(trigger.get("reasons", [])),
        "measurements": copy.deepcopy(trigger.get("measurements", trigger.get("measurement", {}))),
        "old_epoch_items_sha256": _sha(after), "old_epoch_items": len(after),
        "summary_text": summary, "summary_sha256": hashlib.sha256(summary.encode()).hexdigest() if summary else None,
        "summary_skipped": skipped, "summary_response_id": response.get("id") if response else None,
        "evidence": evidence_rows, "input_receipt": input_receipt, "host_state": state,
    }
    new_id = await conversation.renew([{"role": "user", "content": content}], receipt=row)
    runtime.renewals.append(row)
    # SQLite is authoritative if this derivative write fails after the switch.
    write_json(Path(runtime.directory) / "renewals.json",
               {"schema": "g3d.conversation_renewals/1", "renewals": durable_receipts(conversation.db_path)})
    manifest = getattr(runtime.adapter, "image_manifest", Path(runtime.directory) / "image-manifest.jsonl")
    for record in [*input_receipt["images"], *evidence_rows]:
        append_jsonl(Path(manifest), record)
    runtime.mark_renewed()
    runtime.log.note(f"Conversation renewed: {old_id} -> {new_id}.", renewal=row)
    runtime.log.event("conversation_renewed", renewal=row)
    return row


class ClaudeReviewLog(ReviewLog):
    """Reuse Stop/tool/journal hooks with separate raw Claude replay receipts."""

    def __init__(self, run_dir, *, model="claude-opus-5-5", **kwargs):
        self.model = model
        run_dir = Path(run_dir)
        transcript = run_dir / "transcript.md"
        if not transcript.exists():
            write_text_atomic(transcript, f"# Claude run {kwargs.get('run_id') or run_dir.name}\n\n"
                              f"Provider: anthropic. Model: {model}.\n\n"
                              "Owner-readable replies, API-provided thinking summaries and real tool records.\n")
        if kwargs.pop("guard", None) is not None:
            raise ValueError("ClaudeReviewLog must not use the OpenAI budget hooks")
        super().__init__(run_dir, guard=None, **kwargs)

    def record_message(self, *, request_no: int, request_id: str | None,
                       raw_response: dict, transport_receipt: Path) -> None:
        value = json.loads(canonical_bytes(raw_response))
        path = self.transport_dir / f"{request_no:04d}-claude-response.json"
        if path.exists():
            raise ValueError("refusing to overwrite an existing raw Claude response")
        write_json(path, value)
        blocks = value.get("content", [])
        summaries = [block.get("thinking", "") for block in blocks if block.get("type") == "thinking"]
        calls = [{"call_id": block.get("id"), "name": block.get("name")}
                 for block in blocks if block.get("type") == "tool_use"]
        row = {
            "sequence": request_no, "provider": "anthropic", "model": value.get("model", self.model),
            "request_id": request_id, "response_id": value.get("id"),
            "stop_reason": value.get("stop_reason"), "status": value.get("stop_reason"),
            "payload_path": self._relative(path), "transport_receipt": self._relative(Path(transport_receipt)),
        }
        self.last_response = row
        self.responses.append(row)
        self._append(self.summaries_path, {
            **row, "scope": "API-provided thinking summaries; not raw internal reasoning",
            "summaries": summaries, "availability": "returned" if any(summaries) else "not_returned",
            "tool_calls": calls,
        })
        self.event("api_response", request_seq=request_no, **{k: v for k, v in row.items() if k != "sequence"},
                   usage=value.get("usage"), stop_details=value.get("stop_details"),
                   context_management=value.get("context_management"), raw_response_sha256=_sha(value))
        lines = [f"## Claude, response {value.get('id')} (request {request_no}, {value.get('stop_reason')})"]
        for block in blocks:
            if block.get("type") == "text":
                lines.append("**Claude:** " + block.get("text", ""))
            elif block.get("type") == "thinking" and block.get("thinking"):
                lines.append("**Thinking summary:** " + block["thinking"])
            elif block.get("type") == "tool_use":
                lines.append(f"**Calls** `{block.get('name')}` (call `{block.get('id')}`)")
        self._derived(self._transcript, "\n\n".join(lines))

    def publish_money(self, facts: dict) -> None:
        if set(facts) != {"settled_usd", "cutoff_usd", "left_usd"}:
            raise ValueError("Claude money display has exactly settled spend, cutoff and money left")
        if self.heartbeat is not None:
            self.heartbeat.update(**facts)

    def _publish_money(self) -> None:
        # Shared tool hooks have no ownership of Anthropic accounting.
        return None
