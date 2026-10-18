"""A scripted api.openai.com for the $0 proofs (WP2_DESIGN section 14): an ``httpx2.MockTransport`` handler.

``ScriptedOpenAI`` answers exactly two endpoints, the way the Responses API does:

* ``POST /v1/responses/input_tokens`` (BudgetGuard's exact count): ``{"object": "response.input_tokens",
  "input_tokens": N}`` where N is the next scripted reply's ``counted`` value, else a deterministic estimate
  (text characters / 4, rounded up, plus 1,500 per image: the estimator's own model);
* ``POST /v1/responses`` (inference): the next scripted reply. Before answering it checks what the real API rejects
  with a 400: every ``function_call`` in the input has its ``function_call_output`` and every output its call (a
  violation is recorded and answered with the API's own error shape). It also checks that the request was counted
  first with exactly its count fields (``budget.COUNT_FIELDS``), which proves the guard priced this very payload.

Scripted replies (builders below): a function call, a message, an ``incomplete`` response (cut off at the output
limit), any HTTP status (429 rate limit, 500, a non-JSON 520) and transport failures (a dropped connection, a connect
error, a read timeout). Usage defaults to the counted input written to the cache, 50 output tokens (10 reasoning);
a reply can carry its own ``usage`` (the Prada proof replays the 59 recorded usages verbatim).

Every request body is recorded (``counts``, ``creates``) with its headers, so tests assert the exact field set, the
retry header, the image count and the request order. Ids and timestamps are deterministic (``resp_0001``,
``req_0001``, ``call_0001``, ``created_at`` fixed), so two replays produce identical bodies.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
import re
from typing import Any, Callable

import httpx2

from .budget import COUNT_FIELDS

COUNT_PATH = "/v1/responses/input_tokens"
CREATE_PATH = "/v1/responses"
MODEL = "gpt-6-astra"
CREATED_AT = 1_790_000_000
TOKENS_PER_IMAGE = 1_500
DEFAULT_OUTPUT_TOKENS = 50
DEFAULT_REASONING_TOKENS = 10
_DATA_IMAGE = re.compile(r"^data:image/[a-z0-9.+-]+;base64,", re.IGNORECASE)


@dataclass
class Reply:
    """One scripted answer to ``POST /v1/responses`` (and the count that precedes it)."""

    kind: str                                   # "response" | "status" | "raise"
    status: int = 200
    response_status: str = "completed"          # Responses API status of a 200
    output: list[dict] = field(default_factory=list)
    usage: dict | None = None
    counted: int | None = None                  # what the count endpoint answers before this request
    json_body: Any = None                       # a non-200 JSON body
    content: bytes | None = None                # a non-JSON body
    error: Callable[[], BaseException] | None = None
    headers: dict = field(default_factory=dict)
    incomplete_reason: str = "max_output_tokens"
    label: str = ""
    overrides: dict = field(default_factory=dict)   # top-level 200 body fields to replace (e.g. a dated model name)


def _ids(n: int) -> dict:
    return {"response": f"resp_{n:04d}", "request": f"req_{n:04d}", "message": f"msg_{n:04d}",
            "function": f"fc_{n:04d}", "call": f"call_{n:04d}", "reasoning": f"rs_{n:04d}"}


def _reasoning(summary: str | None) -> list[dict]:
    if summary is None:
        return []
    return [{"type": "reasoning", "summary": [{"type": "summary_text", "text": summary}],
             "encrypted_content": "gAAAAB-opaque-continuation-data"}]


def tool_call(name: str, arguments: dict | None = None, *, call_id: str | None = None, text: str | None = None,
              summary: str | None = None, usage: dict | None = None, counted: int | None = None) -> Reply:
    """A completed response that calls one function tool (``call_id`` defaults to call_NNNN)."""
    output = _reasoning(summary)
    if text is not None:
        output.append({"type": "message", "role": "assistant", "status": "completed",
                       "content": [{"type": "output_text", "text": text, "annotations": []}]})
    output.append({"type": "function_call", "name": name, "arguments": json.dumps(arguments or {}),
                   "status": "completed", **({"call_id": call_id} if call_id else {})})
    return Reply("response", output=output, usage=usage, counted=counted, label=f"tool_call {name}")


def message(text: str, *, summary: str | None = None, usage: dict | None = None,
            counted: int | None = None) -> Reply:
    """A completed response with Astra's final text."""
    output = _reasoning(summary) + [{"type": "message", "role": "assistant", "status": "completed",
                                     "content": [{"type": "output_text", "text": text, "annotations": []}]}]
    return Reply("response", output=output, usage=usage, counted=counted, label="message")


def incomplete(*, text: str = "Partial answer cut off at", usage: dict | None = None, counted: int | None = None,
               reason: str = "max_output_tokens") -> Reply:
    """A 200 whose status is ``incomplete`` (the output cap was hit): billed, and the SDK raises on it."""
    output = [{"type": "message", "role": "assistant", "status": "incomplete",
               "content": [{"type": "output_text", "text": text, "annotations": []}]}]
    return Reply("response", response_status="incomplete", output=output, usage=usage, counted=counted,
                 incomplete_reason=reason, label="incomplete")


def http_error(status: int, *, json_body: Any = None, content: bytes | None = None,
               counted: int | None = None) -> Reply:
    """Any non-200 answer: a JSON error body, or raw bytes (a gateway page)."""
    return Reply("status", status=status, json_body=json_body, content=content, counted=counted,
                 label=f"http {status}")


def rate_limited(*, counted: int | None = None) -> Reply:
    return http_error(429, json_body={"error": {"message": "Rate limit reached for requests.", "type": "requests",
                                                "param": None, "code": "rate_limit_exceeded"}}, counted=counted)


def origin_error(*, counted: int | None = None) -> Reply:
    """The Cloudflare 520 the provider returns when its origin fails: non-JSON, outcome unknown."""
    return http_error(520, content=b"error code: 520", counted=counted)


def drop(*, counted: int | None = None) -> Reply:
    """The connection closes after the request was sent and before any response: outcome unknown."""
    return Reply("raise", error=lambda: httpx2.RemoteProtocolError("Server disconnected without sending a response."),
                 counted=counted, label="drop")


def connect_error(*, counted: int | None = None) -> Reply:
    return Reply("raise", error=lambda: httpx2.ConnectError("connection refused"), counted=counted,
                 label="connect error")


def read_timeout(*, counted: int | None = None) -> Reply:
    return Reply("raise", error=lambda: httpx2.ReadTimeout("no response within the client timeout"),
                 counted=counted, label="timeout")


def default_usage(counted: int, *, output_tokens: int = DEFAULT_OUTPUT_TOKENS,
                  reasoning_tokens: int = DEFAULT_REASONING_TOKENS) -> dict:
    """The counted input written to the prompt cache (a first-sight prefix), plus a small output."""
    return {"input_tokens": counted, "output_tokens": output_tokens, "total_tokens": counted + output_tokens,
            "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": counted},
            "output_tokens_details": {"reasoning_tokens": reasoning_tokens}}


def _strip_images(value) -> tuple[Any, int]:
    """The value with every data-URL image replaced by a marker, and the number of images."""
    if isinstance(value, str):
        return ("[image]", 1) if _DATA_IMAGE.match(value) else (value, 0)
    if isinstance(value, list):
        out, total = [], 0
        for item in value:
            stripped, images = _strip_images(item)
            out.append(stripped)
            total += images
        return out, total
    if isinstance(value, dict):
        out, total = {}, 0
        for key, item in value.items():
            stripped, images = _strip_images(item)
            out[key] = stripped
            total += images
        return out, total
    return value, 0


def estimate_tokens(count_body: dict) -> int:
    """Deterministic count: ceil(text characters / 4) + 1,500 per image (WP2_DESIGN section 8's C0 model)."""
    stripped, images = _strip_images(count_body)
    text = json.dumps(stripped, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return max(1, math.ceil(len(text) / 4)) + TOKENS_PER_IMAGE * images


def function_call_problems(input_items) -> list[str]:
    """What the Responses API rejects: a function_call without its output, or an output without its call."""
    if not isinstance(input_items, list):
        return []
    calls, outputs = [], []
    for item in input_items:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "function_call":
            calls.append(item.get("call_id"))
        elif item.get("type") == "function_call_output":
            outputs.append(item.get("call_id"))
    problems = [f"No tool output found for function call {call_id}." for call_id in calls if call_id not in outputs]
    problems += [f"No tool call found for function call output with call_id {call_id}."
                 for call_id in outputs if call_id not in calls]
    return problems


class ScriptedOpenAI:
    """The MockTransport handler: answers counts and creates from ``replies``, records every request."""

    def __init__(self, replies, *, count_tokens: Callable[[dict], int] = estimate_tokens, id_start: int = 1):
        self.replies: list[Reply] = list(replies)
        self.count_tokens = count_tokens
        self.id_start = id_start                    # a later run's transport continues the provider's id sequence
        self.counts: list[dict] = []                # count request bodies, in order
        self.count_answers: list[int] = []
        self.creates: list[dict] = []               # create request bodies, in order
        self.create_headers: list[dict] = []
        self.violations: list[str] = []
        self.unexpected: list[str] = []
        self.answered: list[str] = []               # label of every create answered
        self._last_create_count = 0                # pressure probes may count a request without sending it

    @property
    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self.handle)

    @property
    def exhausted(self) -> bool:
        return len(self.creates) >= len(self.replies)

    def _reply_for(self, index: int) -> Reply | None:
        return self.replies[index] if index < len(self.replies) else None

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        if request.method != "POST" or request.url.host != "api.openai.com" or path not in (COUNT_PATH, CREATE_PATH):
            self.unexpected.append(f"{request.method} {request.url}")
            return httpx2.Response(404, json={"error": {"message": "unexpected request", "type": "test"}})
        try:
            body = json.loads(request.content or b"{}")
        except ValueError:
            self.violations.append(f"{path}: the body is not JSON")
            return httpx2.Response(400, json={"error": {"message": "invalid JSON", "type": "invalid_request_error"}})
        if path == COUNT_PATH:
            return self._count(body)
        return self._create(request, body)

    def _count(self, body: dict) -> httpx2.Response:
        self.counts.append(body)
        reply = self._reply_for(len(self.creates))
        tokens = reply.counted if reply is not None and reply.counted is not None else self.count_tokens(body)
        self.count_answers.append(tokens)
        return httpx2.Response(200, json={"object": "response.input_tokens", "input_tokens": tokens})

    def _create(self, request: httpx2.Request, body: dict) -> httpx2.Response:
        self.creates.append(body)
        self.create_headers.append(dict(request.headers))
        n = len(self.creates)
        ids = _ids(self.id_start + n - 1)
        if len(self.counts) <= self._last_create_count:
            self.violations.append(f"create {n}: {len(self.counts)} count calls before it (every request is counted "
                                   "before it is sent)")
        elif {key: value for key, value in body.items() if key in COUNT_FIELDS} != self.counts[-1]:
            self.violations.append(f"create {n}: the payload differs from the one counted before it")
        self._last_create_count = len(self.counts)
        problems = function_call_problems(body.get("input"))
        if problems:
            self.violations.extend(f"create {n}: {problem}" for problem in problems)
            self.answered.append("400 function_call/output mismatch")
            return httpx2.Response(400, headers={"x-request-id": ids["request"]}, json={"error": {
                "message": problems[0], "type": "invalid_request_error", "param": "input", "code": None}})
        reply = self._reply_for(n - 1)
        if reply is None:
            self.violations.append(f"create {n}: the script has only {len(self.replies)} replies")
            self.answered.append("500 script exhausted")
            return httpx2.Response(500, json={"error": {"message": "script exhausted", "type": "test"}})
        self.answered.append(reply.label)
        headers = {"x-request-id": ids["request"], **reply.headers}
        if reply.kind == "raise":
            raise reply.error()
        if reply.kind == "status":
            if reply.content is not None:
                return httpx2.Response(reply.status, content=reply.content, headers=headers)
            return httpx2.Response(reply.status, json=reply.json_body, headers=headers)
        counted = self.count_answers[-1] if self.count_answers else 0
        return httpx2.Response(200, headers=headers, json=self._response_body(self.id_start + n - 1, ids, reply,
                                                                              counted, body))

    @staticmethod
    def _response_body(n: int, ids: dict, reply: Reply, counted: int, request_body: dict) -> dict:
        output = []
        for item in reply.output:
            item = dict(item)
            kind = item["type"]
            if kind == "reasoning":
                item.setdefault("id", ids["reasoning"])
            elif kind == "message":
                item.setdefault("id", ids["message"])
            elif kind == "function_call":
                item.setdefault("id", ids["function"])
                item.setdefault("call_id", ids["call"])
            output.append(item)
        status = reply.response_status
        body = {
            "id": ids["response"], "object": "response", "created_at": CREATED_AT + n, "model": MODEL,
            "status": status, "error": None,
            "incomplete_details": {"reason": reply.incomplete_reason} if status == "incomplete" else None,
            "instructions": None, "output": output, "parallel_tool_calls": False, "tool_choice": "auto",
            "tools": [], "service_tier": "default", "store": True,
            "max_output_tokens": request_body.get("max_output_tokens"),
            "usage": reply.usage if reply.usage is not None else default_usage(counted),
        }
        body.update(reply.overrides)
        return body


def input_images(body: dict) -> list[str]:
    """Every data-URL image string anywhere in a request body (brief images and tool-output images)."""
    found: list[str] = []

    def walk(value):
        if isinstance(value, str):
            if _DATA_IMAGE.match(value):
                found.append(value)
        elif isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
    walk(body.get("input"))
    return found
