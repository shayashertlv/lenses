"""Provider-only adapters over the unchanged, journaled glasses tools."""
from __future__ import annotations

import asyncio
import base64
import copy
from decimal import Decimal
import hashlib
import io
import json
import os
from pathlib import Path
import re
from types import SimpleNamespace
from typing import Any, Callable, Sequence

from agents import RunContextWrapper, function_tool
from agents.tool import ToolOutputImage, ToolOutputText, invoke_function_tool
from agents.tool_context import ToolContext
from jsonschema import Draft202012Validator
from PIL import Image

from glasses3d.agent.loop import FATAL_TOOL_ERRORS, fatal_tool_errors, tool_result_status
from glasses3d.agent.tool_reports import bounded_tool, limit_output
from glasses3d.agent.tool_timeouts import busy_result, clear_running, mark_running

from .images import FileCache, image_block, label_block, tile


INSTRUCTION_REPLACEMENTS = (
    ("You are Astra.", "You are Claude."),
    ("Only status accepted completes the run; a refusal names the failed checks.",
     "The run ends only when the finish tool returns status accepted; a text reply does not end it. "
     "A refusal names the failed checks."),
)
SPECIALIST_REPLACEMENTS = (
    ("You are Astra, the temple specialist.", "You are Claude, the temple specialist."),
    ("A refusal names unmet conditions or validation failure\n"
     "and the run continues. Only an accepted finish completes this specialist session.",
     "A refusal names unmet conditions or validation failure\n"
     "and the run continues. This specialist session ends only when facet_finish returns status accepted; "
     "a text reply does not end it."),
)


def claude_instructions(text: str, *, specialist: bool = False) -> str:
    """Change only the two approved source fragments; fail on source drift."""
    for old, new in SPECIALIST_REPLACEMENTS if specialist else INSTRUCTION_REPLACEMENTS:
        if text.count(old) != 1:
            raise ValueError(f"expected exactly one instruction fragment: {old!r}")
        text = text.replace(old, new, 1)
    return text


def tool_definitions(tools: Sequence, *, specialist: bool = False) -> list[dict]:
    """Preserve source descriptors and order without requesting strict grammar."""
    del specialist
    names = [tool.name for tool in tools]
    if len(set(names)) != len(names):
        raise ValueError("duplicate tool registry names")
    return [{"name": tool.name, "description": tool.description,
             "input_schema": copy.deepcopy(tool.params_json_schema)} for tool in tools]


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def effective_registry_sha256(definitions: Sequence[dict]) -> str:
    return hashlib.sha256(_canonical(list(definitions)).encode("utf-8")).hexdigest()


def _items(output: Any) -> list:
    return output if isinstance(output, list) else [output]


def _text(item: Any) -> str | None:
    if isinstance(item, str):
        return item
    if isinstance(item, ToolOutputText):
        return item.text
    if isinstance(item, dict) and item.get("type") in {"text", "input_text"}:
        return item.get("text")
    return None


def _image_data(item: Any) -> bytes | None:
    if isinstance(item, ToolOutputImage):
        url = item.image_url
    elif isinstance(item, dict) and item.get("type") in {"image", "input_image"}:
        url = item.get("image_url")
        if isinstance(url, dict):
            url = url.get("url")
    else:
        return None
    if not isinstance(url, str) or not url.startswith("data:image/"):
        raise ValueError("tool images must contain their actual emitted bytes as a data URL")
    header, separator, encoded = url.partition(",")
    if not separator or not header.endswith(";base64"):
        raise ValueError("tool image must have a base64 data URL")
    return base64.b64decode(encoded, validate=True)


def _retain_tile(store: Path, digest: str, data: bytes) -> Path:
    store.mkdir(parents=True, exist_ok=True)
    target = store / digest
    if target.exists():
        if target.read_bytes() != data:
            raise ValueError("stored tile hash collision or corrupted bytes")
    else:
        # Parent and specialist share a sequential client and a content cache.
        with target.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    return target


def prepare_images(raw_output: Any, *, files: FileCache, metadata: dict) -> tuple[list[dict], list[dict]]:
    """Convert only emitted pixels, preserving text/image order and provenance."""
    blocks, records = [], []
    previous_label = None
    tile_store = Path(metadata.get("tile_store") or files.path.parent / "tiles")
    origin = {key: value for key, value in metadata.items() if key != "tile_store"}
    for index, item in enumerate(_items(raw_output)):
        text = _text(item)
        if text is not None:
            blocks.append(label_block(text))
            previous_label = text
            continue
        data = _image_data(item)
        if data is None:
            if isinstance(item, dict):
                blocks.append(label_block(_canonical(item)))
                previous_label = None
                continue
            raise ValueError(f"unsupported tool output type: {type(item).__name__}")
        source_hash = hashlib.sha256(data).hexdigest()
        source_path = _retain_tile(tile_store, source_hash, data)
        with Image.open(io.BytesIO(data)) as source:
            source.load()
            width, height = source.size
        label = previous_label or str(metadata.get("label") or f"Image {index + 1}")
        for piece in tile(data, label):
            path = _retain_tile(tile_store, piece.sha256, piece.png)
            file_id = files.file_id_for(piece)
            blocks.extend((label_block(piece.label), image_block(file_id)))
            records.append({
                **origin, "output_index": index, "source_sha256": source_hash,
                "source_path": str(source_path),
                "source_size_px": [width, height], "source_bytes": len(data),
                "tile_sha256": piece.sha256, "sha256": piece.sha256,
                "size_px": [piece.w, piece.h], "w": piece.w, "h": piece.h,
                "x0": piece.x0, "y0": piece.y0,
                "x1": piece.x0 + piece.w, "y1": piece.y0 + piece.h,
                "bytes": len(piece.png), "label": piece.label,
                "file_id": file_id, "tile_path": str(path),
            })
        previous_label = None
    return blocks, records


class ToolAdapter:
    def __init__(self, tools, *, scene, status, log, files: FileCache,
                 image_manifest: Path, specialist: bool = False, parent_settings=None):
        self.scene, self.status, self.log, self.files = scene, status, log, files
        self.image_manifest = Path(image_manifest)
        self.specialist = specialist
        self.parent_settings = (parent_settings or getattr(status, "tool_settings", None)
                                or getattr(status, "settings", None))
        self.definitions = tool_definitions(tools, specialist=specialist)
        self.tools = {tool.name: tool for tool in tools}
        self.validators = {name: Draft202012Validator(tool.params_json_schema)
                           for name, tool in self.tools.items()}
        if "temple_stage" in self.tools:
            self.tools["temple_stage"] = self._temple_tool(self.tools["temple_stage"])
        self._used_ids: set[str] = set()
        self._active_bridges: dict[str, Any] = {}
        self.last_raw_output = self.last_tool_name = self.last_call_id = None
        self.image_records: list[dict] = []
        self._accepted: dict[str, Any] | None = None

    def dimensions(self) -> dict[str, tuple[int, int]]:
        # Cache remains authoritative across parents, children, and renewal.
        return {row["file_id"]: (row["w"], row["h"]) for row in self.files._read().values()}

    def _error(self, call_id, message: str, *, code="E_ARGS") -> dict:
        raw = {"status": "refused", "code": code, "message": message[:1000]}
        content = [label_block(_canonical(raw))]
        footer = getattr(self.status, "footer", None)
        if callable(footer):
            content.append(label_block(footer()))
        return {"type": "tool_result", "tool_use_id": call_id,
                "is_error": True, "content": content}

    def _context(self, call_id: str, name: str, arguments: str) -> ToolContext:
        return ToolContext.from_agent_context(
            RunContextWrapper(context=self.status), tool_call_id=call_id,
            tool_name=name, tool_arguments=arguments)

    def _log_context(self, ctx):
        stage_id = getattr(self.status, "stage_call_id", None)
        if stage_id is None:
            return ctx
        return SimpleNamespace(tool_call_id=f"stage:{stage_id}:{ctx.tool_call_id}",
                               tool_arguments=ctx.tool_arguments)

    async def _hook(self, name: str, ctx, tool, *args):
        method = getattr(self.log, name, None)
        if callable(method):
            await method(self._log_context(ctx), None, tool, *args)

    def _journal(self, ctx):
        if callable(getattr(self.scene, "journal_entries", None)):
            rows = [row for row in self.scene.journal_entries() if row.get("call_id") == ctx.tool_call_id]
            if rows and callable(getattr(self.log, "record_journal", None)):
                row = {**rows[-1], "stage": getattr(self.status, "stage_call_id", None)}
                self.log.record_journal(self._log_context(ctx).tool_call_id, row)

    def mark_unseen(self, call_id: str) -> None:
        self._accepted = None
        mark = getattr(self.scene, "mark_unseen", None)
        if callable(mark):
            mark(call_id)
        # Both pending evidence and accepted finish require durable delivery.
        if hasattr(self.scene, "finish_review"):
            self.scene.finish_review = None
        if hasattr(self.scene, "finish") and not callable(self.scene.finish):
            self.scene.finish = None
        if self.specialist and isinstance(getattr(self.scene, "state", None), dict):
            # Lab.finish is an operation, unlike JobScene.finish's saved value.
            # Keep that method callable when an emitted specialist result fails.
            lock = getattr(self.scene, "state_lock", None)
            if lock is None:
                self.scene.state["finish"] = None
            else:
                with lock:
                    self.scene.state["finish"] = None
        if callable(getattr(self.scene, "save", None)):
            self.scene.save()

    def expire_image_dedup(self) -> None:
        """Server clears affect emission only, never the delivered-call journal."""
        lock = getattr(self.scene, "_state_lock", None)
        if lock is None:
            self.scene._emitted_images = {}
        else:
            with lock:
                self.scene._emitted_images = {}

    def _manifest(self, records: list[dict]) -> None:
        if not records:
            return
        self.image_manifest.parent.mkdir(parents=True, exist_ok=True)
        with self.image_manifest.open("a", encoding="utf-8", newline="\n") as stream:
            for record in records:
                stream.write(_canonical(record) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        self.image_records.extend(records)

    async def invoke(self, block: dict) -> dict:
        call_id = block.get("id") if isinstance(block, dict) else None
        name = block.get("name") if isinstance(block, dict) else None
        self.last_raw_output = None
        self.last_call_id, self.last_tool_name = call_id, name
        if not isinstance(call_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", call_id):
            return self._error(call_id if isinstance(call_id, str) else "", "invalid tool_use id")
        if call_id in self._used_ids:
            return self._error(call_id, "duplicate tool_use id")
        self._used_ids.add(call_id)
        if block.get("type") != "tool_use" or name not in self.tools:
            return self._error(call_id, "unknown tool name or invalid tool_use block")
        arguments = block.get("input")
        try:
            if not isinstance(arguments, dict):
                raise ValueError("tool input must be an object")
            serialized = _canonical(arguments)
            self.validators[name].validate(arguments)
        except Exception as exc:
            return self._error(call_id, str(exc))
        tool = self.tools[name]
        ctx = self._context(call_id, name, serialized)
        await self._hook("on_tool_start", ctx, tool)
        try:
            output = await invoke_function_tool(function_tool=tool, context=ctx, arguments=serialized)
            self.last_raw_output = output
            await self._hook("on_tool_end", ctx, tool, output)
            self._journal(ctx)
            head = getattr(getattr(self.scene, "s", None), "head", None) or {}
            metadata = {"call_id": call_id, "tool": name, "checkpoint": head.get("id"),
                        "stage": getattr(self.status, "stage_call_id", None)}
            preparation = asyncio.create_task(asyncio.to_thread(
                prepare_images, output, files=self.files, metadata=metadata))
            try:
                content, records = await asyncio.shield(preparation)
            except asyncio.CancelledError:
                # Uploads use the shared synchronous client. Do not release it
                # to teardown or a child while a conversion thread is alive.
                try:
                    await asyncio.shield(preparation)
                except BaseException:
                    pass
                raise
            self._manifest(records)
            result = {"type": "tool_result", "tool_use_id": call_id, "content": content}
            if self._executor_error(output):
                result["is_error"] = True
            expected = "facet_finish" if self.specialist else "finish"
            if name == expected and tool_result_status(output) == "accepted":
                self._accepted = {"call_id": call_id, "checkpoint": copy.deepcopy(head),
                                  "name": name, "output": output}
            return result
        except (*FATAL_TOOL_ERRORS, asyncio.CancelledError):
            self.mark_unseen(call_id)
            raise
        except Exception as exc:
            self.mark_unseen(call_id)
            failure = self._error(call_id, f"{type(exc).__name__}: {exc}", code="E_TOOL_OUTPUT")
            event = getattr(self.log, "event", None)
            if callable(event):
                event("tool_output_failed", tool_call_id=call_id, name=name,
                      error=f"{type(exc).__name__}: {exc}"[:1000])
            return failure

    @staticmethod
    def _executor_error(output: Any) -> bool:
        for item in _items(output):
            text = _text(item)
            if text is None:
                continue
            try:
                value, _ = json.JSONDecoder().raw_decode(text.lstrip())
            except ValueError:
                if (text.startswith("An error occurred while running the tool")
                        or "timed out after" in text):
                    return True
            else:
                if isinstance(value, dict) and value.get("code") in {
                    "E_ARGS", "E_LAB_ARGS", "E_TIMEOUT", "E_TOOL_OUTPUT",
                }:
                    return True
        return False

    def accepted_finish(self, name: str, raw_output: Any) -> bool:
        expected = "facet_finish" if self.specialist else "finish"
        if name != expected or tool_result_status(raw_output) != "accepted":
            return False
        if (self._accepted is None or self._accepted["name"] != name
                or self._accepted["output"] != raw_output):
            return False
        if self._accepted["call_id"] in getattr(self.scene, "_unseen", getattr(self.scene, "unseen", ())):
            return False
        head = getattr(getattr(self.scene, "s", None), "head", None) or {}
        if head != self._accepted["checkpoint"]:
            return False
        if hasattr(self.scene, "finish") and self.scene.finish is None:
            return False
        if self.specialist and isinstance(getattr(self.scene, "state", None), dict):
            if not self.scene.state.get("finish"):
                return False
        return True

    def _temple_tool(self, original):
        from glasses3d.agent.temple_lab import stage
        if getattr(self.scene, "owner_edit", None):
            raise ValueError("owner-edit temple stages are outside M13")
        child_settings = stage.settings(self.parent_settings)

        @function_tool(name_override=original.name, timeout=None,
                       failure_error_function=fatal_tool_errors(*FATAL_TOOL_ERRORS))
        async def temple_stage(ctx: ToolContext):
            runtime = ctx.context
            ledger = getattr(runtime, "ledger", None)
            attached = getattr(runtime, "runtime", None)
            if ledger is None or not callable(getattr(attached, "run_stage", None)):
                return await self.call_scene(ctx, "temple_stage", lambda cid: {
                    "status": "refused", "code": "E_STAGE_CONTEXT",
                    "message": "temple_stage needs the active job run"})
            allowed, _reason = ledger.may_start_request()
            if not allowed:
                return await self.call_scene(ctx, "temple_stage", lambda cid: {
                    "status": "refused", "code": "E_STAGE_BUDGET",
                    "message": "the owner's cutoff has been reached, or a request remains unsettled"})
            left = Decimal(ledger.money_status()["left_usd"])
            bridge = stage.StageBridge(asyncio.get_running_loop())
            self._active_bridges[ctx.tool_call_id] = bridge
            try:
                return await self.call_scene(ctx, "temple_stage", lambda cid: stage.run(
                    self.scene, runtime, bridge, cid, left, child_settings))
            except asyncio.CancelledError:
                bridge.cancel()
                raise
            finally:
                self._active_bridges.pop(ctx.tool_call_id, None)

        if temple_stage.params_json_schema != original.params_json_schema:
            raise ValueError("private temple entry changed its schema")
        if original.timeout_seconds is not None:
            raise ValueError("temple_stage unexpectedly acquired an outer timeout")
        temple_stage.description = original.description
        temple_stage.params_json_schema = copy.deepcopy(original.params_json_schema)
        return bounded_tool(temple_stage, self.scene.root)

    async def call_scene(self, ctx, name: str, operation: Callable) -> Any:
        """The private temple entry retains the original lock/journal/output path."""
        from glasses3d.agent.tools import _output
        cid = ctx.tool_call_id

        def locked():
            if not self.scene.lock.acquire(timeout=0):
                return [ToolOutputText(text=json.dumps(busy_result(self.scene))),
                        ToolOutputText(text=self.status.footer())]
            mark_running(self.scene, name, cid)
            before = (self.scene.s.head or {}).get("id")
            try:
                try:
                    result = operation(cid)
                    if not isinstance(result, dict):
                        result = {"status": "ok", "result": result}
                    result.setdefault("call_id", cid)
                    result.setdefault("checkpoint", (self.scene.s.head or {}).get("id"))
                    output = _output(self.scene, cid, name, result)
                except FATAL_TOOL_ERRORS:
                    raise
                except (ValueError, KeyError, TypeError, OSError) as exc:
                    result = {"status": "refused", "code": "E_ARGS", "call_id": cid,
                              "message": str(exc)[:1000]}
                    output = [ToolOutputText(text=json.dumps(result))]
                try:
                    output = limit_output(self.scene.root, cid, name,
                                          [*output, ToolOutputText(text=self.status.footer())])
                    self.scene.record_call(cid, name, before, (self.scene.s.head or {}).get("id"), result)
                except BaseException:
                    self.mark_unseen(cid)
                    raise
                return output
            finally:
                clear_running(self.scene, cid)
                self.scene.lock.release()

        task = asyncio.create_task(asyncio.to_thread(locked))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            bridge = self._active_bridges.get(cid)
            if bridge is not None:
                bridge.cancel()
            self.mark_unseen(cid)
            # A cancelled asyncio wrapper does not stop its mutation thread.
            try:
                await asyncio.shield(task)
            except BaseException:
                pass
            raise
