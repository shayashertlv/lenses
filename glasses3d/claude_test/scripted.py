"""Strict offline Anthropic wire fixture, exercised through the real SDK.

Synthetic thinking signatures prove byte-preserving replay, not cryptographic
validity. This module never creates a production HTTP transport.
"""
from __future__ import annotations

import base64
import copy
from dataclasses import dataclass
from datetime import datetime, timezone
from email.parser import BytesParser
from email.policy import default
import hashlib
import io
import json
import re
from typing import Callable, Sequence
from uuid import uuid4

import httpx2
from PIL import Image

from .images import check_request, fits, visual_tokens

MODEL = "claude-opus-5-5"
DEFAULT_USAGE = {"input_tokens": 100, "output_tokens": 10,
                 "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
                 "service_tier": "standard", "speed": "standard", "inference_geo": "global"}


@dataclass(frozen=True)
class ScriptTurn:
    content: list[dict]
    stop_reason: str = "tool_use"
    usage: dict | None = None
    http_status: int = 200
    headers: dict | None = None
    context_management: dict | None = None
    served_model: str | None = None
    missing_usage: bool = False
    truncate: bool = False
    error: dict | None = None
    stop_details: dict | None = None
    chunk_size: int = 37


class _Chunks(httpx2.SyncByteStream):
    def __init__(self, raw: bytes, size: int):
        self.raw, self.size = raw, size

    def __iter__(self):
        for offset in range(0, len(self.raw), self.size):
            yield self.raw[offset:offset + self.size]


def _pieces(text: str):
    # Exercise partial tool JSON and thinking accumulation, including
    # multibyte text split independently by the byte stream below.
    for offset in range(0, len(text), 11):
        yield text[offset:offset + 11]


def sse_response(turn: ScriptTurn, *, request_id: str, model: str,
                 message_id: str) -> httpx2.Response:
    """Build native SSE events, deliberately split inside strings/JSON/UTF-8."""
    headers = {"request-id": request_id, **(turn.headers or {})}
    if turn.http_status != 200:
        return httpx2.Response(turn.http_status, headers=headers,
                               json={"type": "error", "error": {"type": "scripted_error",
                                                               "message": "Offline scripted HTTP error"}})
    if type(turn.chunk_size) is not int or turn.chunk_size < 1:
        raise ValueError("chunk_size must be positive")
    usage = copy.deepcopy(DEFAULT_USAGE if turn.usage is None else turn.usage)
    initial = {key: value for key, value in usage.items() if key != "output_tokens"}
    initial["output_tokens"] = 0
    message = {"id": message_id, "type": "message", "role": "assistant",
               "model": turn.served_model or model, "content": [],
               "stop_reason": None, "stop_sequence": None}
    if not turn.missing_usage:
        message["usage"] = initial
    events = [{"type": "message_start", "message": message}, {"type": "ping"}]
    for index, original in enumerate(turn.content):
        block = copy.deepcopy(original)
        kind = block["type"]
        deltas = []
        if kind == "thinking":
            text, signature = block.pop("thinking", ""), block.pop("signature", "")
            block.update(thinking="", signature="")
            deltas.extend({"type": "thinking_delta", "thinking": piece} for piece in _pieces(text))
            # The SDK assigns (rather than concatenates) signature_delta; the
            # signature is one logical event, split only at wire byte boundaries.
            deltas.append({"type": "signature_delta", "signature": signature})
        elif kind == "text":
            text = block.pop("text")
            block["text"] = ""
            deltas.extend({"type": "text_delta", "text": piece} for piece in _pieces(text))
        elif kind == "tool_use":
            arguments = json.dumps(block.pop("input"), ensure_ascii=False, allow_nan=False)
            block["input"] = {}
            deltas.extend({"type": "input_json_delta", "partial_json": piece} for piece in _pieces(arguments))
        elif kind != "redacted_thinking":
            raise ValueError(f"unsupported scripted content block: {kind}")
        events.append({"type": "content_block_start", "index": index, "content_block": block})
        events.extend({"type": "content_block_delta", "index": index, "delta": delta} for delta in deltas)
        events.append({"type": "content_block_stop", "index": index})
    if turn.error:
        events.append({"type": "error", "error": turn.error})
    else:
        delta = {"type": "message_delta",
                 "delta": {"stop_reason": turn.stop_reason, "stop_sequence": None,
                           "stop_details": turn.stop_details}}
        if not turn.missing_usage:
            delta["usage"] = usage
        if turn.context_management is not None:
            delta["context_management"] = copy.deepcopy(turn.context_management)
        events.append(delta)
        if not turn.truncate:
            events.append({"type": "message_stop"})
    raw = "".join(f"event: {event['type']}\ndata: "
                  f"{json.dumps(event, ensure_ascii=False, allow_nan=False)}\n\n"
                  for event in events).encode("utf-8")
    if turn.truncate:
        raw += b"event: message_stop\ndata: {"
    return httpx2.Response(200, headers={"content-type": "text/event-stream", **headers},
                           stream=_Chunks(raw, turn.chunk_size))


def _json(raw: bytes) -> dict:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise AssertionError("duplicate JSON key")
            result[key] = value
        return result
    def invalid(value):
        raise AssertionError(f"nonfinite JSON: {value}")
    result = json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)
    assert isinstance(result, dict), "request body must be a JSON object"
    return result


def _walk(value):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


class ScriptedClaude:
    """Finite scripts and retained fake Files evidence, with no fallback.

    Callbacks receive a detached copy of the actual Messages request, allowing
    IDs and arguments to be derived from real preceding tool results.
    """
    def __init__(self, turns: Sequence[ScriptTurn | Callable], *,
                 token_counts: Sequence[dict] = (), delete_statuses: Sequence[int] = ()):
        self.turns, self.token_counts = list(turns), list(token_counts)
        self.delete_statuses = list(delete_statuses)
        assert all(type(status) is int and (status == 200 or 400 <= status <= 599)
                   for status in self.delete_statuses), "invalid scripted deletion status"
        self.requests: list[dict] = []
        self.request_sizes: list[int] = []
        self.request_sha256: list[str] = []
        self.counts: list[dict] = []
        self.files: dict[str, dict] = {}
        self.calls: list[dict] = []
        self.violations: list[str] = []
        self.uploads: list[dict] = []
        self.deletions: list[dict] = []
        self.replies: list[ScriptTurn] = []
        self._turn_index = self._count_index = 0

    @property
    def dims(self):
        return {key: (item["w"], item["h"]) for key, item in self.files.items()}

    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self)

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        try:
            return self._handle(request)
        except Exception as error:
            self.violations.append(f"{type(error).__name__}: {error}")
            raise

    def _handle(self, request):
        url = request.url
        assert url.scheme == "https" and url.host == "api.anthropic.com" and url.port in (None, 443)
        assert not url.username and not url.password and not url.fragment
        assert url.raw_path.partition(b"?")[0] == url.path.encode("ascii"), "encoded paths are forbidden"
        assert list(url.params.multi_items()) in ([], [("beta", "true")]), "unknown query"
        method, path = request.method, url.path
        self.calls.append({"method": method, "path": path, "query": str(url.query, "ascii")})
        if (method, path) == ("POST", "/v1/files"):
            return self._upload(request)
        if method == "GET" and re.fullmatch(r"/v1/files/[A-Za-z0-9_-]+", path):
            assert not request.content, "metadata GET has a body"
            record = self.files[path.rsplit("/", 1)[1]]
            assert not record["deleted"], "deleted file metadata is unavailable"
            return httpx2.Response(200, headers={"request-id": f"req_metadata_{len(self.calls):04d}"},
                                   json=record["metadata"])
        if method == "DELETE" and re.fullmatch(r"/v1/files/[A-Za-z0-9_-]+", path):
            return self._delete(request)
        assert method == "POST" and path in ("/v1/messages", "/v1/messages/count_tokens"), "unknown endpoint"
        assert len(request.content) < 32_000_000, "request exceeds strict 32 MB limit"
        body = _json(request.content)
        assert body.get("model") == MODEL
        assert isinstance(body.get("messages"), list) and body["messages"]
        allowed = {"model", "messages", "system", "tools", "thinking", "tool_choice",
                   "context_management", "output_config", "cache_control", "speed"}
        if path == "/v1/messages":
            allowed |= {"stream", "max_tokens", "service_tier", "inference_geo"}
        assert not set(body) - allowed, f"unexpected request fields: {sorted(set(body) - allowed)}"
        for message in body["messages"]:
            assert set(message) == {"role", "content"}
            assert message["role"] in {"assistant", "user"}
            assert isinstance(message["content"], (str, list))
        assert all("strict" not in tool for tool in body.get("tools", []))
        if path.endswith("/count_tokens"):
            return self._count(body)
        assert body.get("stream") is True
        assert body.get("service_tier") == "standard_only"
        assert body.get("output_config", {}).get("effort") == "max"
        assert body.get("speed", "standard") == "standard"
        assert body.get("inference_geo", "global") == "global"
        assert "fallbacks" not in body
        assert not check_request(body, self.dims), check_request(body, self.dims)
        for item in _walk(body):
            if item.get("type") == "image" and item.get("source", {}).get("type") == "file":
                assert not self.files[item["source"]["file_id"]]["deleted"], "deleted file used in Messages"
        assert self._turn_index < len(self.turns), "script exhausted; no network fallback"
        self.requests.append(copy.deepcopy(body))
        self.request_sizes.append(len(request.content))
        self.request_sha256.append(hashlib.sha256(request.content).hexdigest())
        turn = self.turns[self._turn_index]
        self._turn_index += 1
        if callable(turn):
            turn = turn(copy.deepcopy(body))
        assert isinstance(turn, ScriptTurn), "script callbacks must return ScriptTurn"
        self.replies.append(turn)
        return sse_response(turn, request_id=f"req_script_{self._turn_index:04d}",
                            model=body["model"], message_id=f"msg_script_{self._turn_index:04d}")

    def _upload(self, request):
        content_type = request.headers.get("content-type", "")
        assert content_type.startswith("multipart/form-data;")
        message = BytesParser(policy=default).parsebytes(
            b"Content-Type: " + content_type.encode("ascii") + b"\r\nMIME-Version: 1.0\r\n\r\n" + request.content)
        fields = {}
        for part in message.iter_parts():
            name = part.get_param("name", header="content-disposition")
            assert name not in fields, "duplicate multipart field"
            fields[name] = part
        assert set(fields) == {"file", "expires_in_seconds"}
        assert fields["expires_in_seconds"].get_payload(decode=True) == b"604800"
        part = fields["file"]
        raw = part.get_payload(decode=True)
        assert 0 < len(raw) <= 10_000_000
        with Image.open(io.BytesIO(raw)) as image:
            image.load()
            w, h = image.size
            assert fits(w, h, many=True), "uploaded image must already satisfy native inference bounds"
        sha = hashlib.sha256(raw).hexdigest()
        # Provider IDs belong to an upload, not to its bytes. Separate run caches
        # must receive different IDs even when they upload the same photograph.
        file_id = "file_" + uuid4().hex
        metadata = {"id": file_id, "type": "file", "filename": part.get_filename(),
                    "mime_type": part.get_content_type(), "size_bytes": len(raw),
                    "created_at": "2026-10-20T00:00:00Z", "expires_at": "2026-10-27T00:00:00Z",
                    "downloadable": False}
        self.files[file_id] = {"bytes": raw, "sha256": sha, "w": w, "h": h,
                               "metadata": metadata, "deleted": False, "deleted_at": None}
        self.uploads.append({"file_id": file_id, "sha256": sha, "expires_in_seconds": 604800})
        return httpx2.Response(200, headers={"request-id": f"req_upload_{len(self.uploads):04d}"}, json=metadata)

    def _delete(self, request):
        assert not request.content, "file DELETE has a body"
        file_id = request.url.path.rsplit("/", 1)[1]
        assert file_id in self.files, "cannot delete a file absent from this fake service"
        record = self.files[file_id]
        assert not record["deleted"], "file was already deleted"
        number = len(self.deletions) + 1
        status = self.delete_statuses[number - 1] if number <= len(self.delete_statuses) else 200
        request_id = f"req_delete_{number:04d}"
        deleted_at = datetime.now(timezone.utc).isoformat() if status == 200 else None
        self.deletions.append({"file_id": file_id, "http_status": status, "request_id": request_id,
                               "deleted": status == 200, "deleted_at": deleted_at})
        if status == 200:
            # Retain private evidence for pixel/hash audits after teardown, but
            # refuse API access or inference reuse of the deleted file.
            record.update(deleted=True, deleted_at=deleted_at)
            response = {"id": file_id, "type": "file_deleted"}
        else:
            response = {"type": "error", "error": {"type": "scripted_delete_error",
                                                  "message": "Offline scripted deletion failed"}}
        return httpx2.Response(status, headers={"request-id": request_id}, json=response)

    def _count(self, body):
        images = [item for item in _walk(body) if item.get("type") == "image"]
        assert len(images) <= 600
        local = 0
        for item in images:
            assert item.get("transformations") == {"oversized_image": "error"}
            source = item["source"]
            assert source["type"] == "base64", "counting cannot use Files IDs or URLs"
            with Image.open(io.BytesIO(base64.b64decode(source["data"], validate=True))) as image:
                assert fits(*image.size, many=len(images) > 20)
                local += visual_tokens(*image.size)
        self.counts.append(copy.deepcopy(body))
        if self._count_index < len(self.token_counts):
            result = copy.deepcopy(self.token_counts[self._count_index])
            self._count_index += 1
        else:
            text = sum(len(str(item.get("text", "")).encode("utf-8")) for item in _walk(body))
            result = {"input_tokens": local + (text + 2) // 3}
        return httpx2.Response(200, headers={"request-id": f"req_count_{len(self.counts):04d}"}, json=result)

    def assert_complete(self) -> dict:
        assert not self.violations, self.violations
        assert self._turn_index == len(self.turns), (self._turn_index, len(self.turns))
        assert self._count_index == len(self.token_counts)
        assert len(self.deletions) >= len(self.delete_statuses), "unused scripted deletion status"
        return {"scripted_messages": self._turn_index, "count_requests": len(self.counts),
                "uploads": len(self.uploads), "unique_files": len(self.files),
                "deleted_files": sum(row["deleted"] for row in self.files.values()),
                "network_fallback": False}
