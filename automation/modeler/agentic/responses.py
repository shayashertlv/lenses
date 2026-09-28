"""Exact Responses API capture: payload construction, transports, strict parsing, input-token counting.

Every request is one ``POST /v1/responses`` with ``store: false``, the complete current window replayed, strict
function tools, ``parallel_tool_calls: false`` and an explicit ``max_output_tokens``. The developer instructions are
sent as the first input message (a developer message can carry the explicit prompt-cache breakpoint; top-level
``instructions`` cannot). Transports: ``HttpTransport`` (requests, no retries, bounded read, credential redaction),
``ScriptedTransport`` (tests and offline demos: complete scripted response bodies plus assertions on what was sent),
``RefusingTransport`` (the default: nothing leaves the machine). A response is parsed into every item it carries;
nothing is reduced to one function's arguments; unknown item types are reported, never guessed.

Official pages read on 2026-09-27 (snapshots under reviews/agentic/20260927-working-tree-review/official-docs, local-only, not in Git):
conversation state, function calling, reasoning (encrypted content, phase), compaction (standalone endpoint has no
max_output_tokens), token counting (POST /v1/responses/input_tokens), prompt caching (explicit mode, breakpoints).
The count endpoint's accepted request fields (``COUNT_MIRRORED_FIELDS``) were read on 2026-09-28 from the generated
SDK parameter types, which the token-counting guide's reference link resolves to: openai-python
``src/openai/types/responses/input_token_count_params.py`` and openai-node ``src/resources/responses/input-tokens.ts``
(https://raw.githubusercontent.com/openai/openai-python/main/... and .../openai/openai-node/master/...); both list
conversation, input, instructions, model, parallel_tool_calls, personality, previous_response_id, reasoning, text,
tool_choice, tools, truncation and nothing else.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
import hashlib
import json
import math
import time

from reconstruction import segmented_astra_transport as legacy_transport

from .pricing import MODEL, MODEL_MAX_OUTPUT_TOKENS
from .state import canonical_json as canonical, sha256_bytes as sha256     # the public names runner, demo and tests use

ENDPOINT = "https://api.openai.com/v1/responses"
ENDPOINT_COUNT = ENDPOINT + "/input_tokens"
ENDPOINT_COMPACT = ENDPOINT + "/compact"
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_IMAGE_BYTES = 12 * 1024 * 1024
MAX_IMAGES_BYTES_PER_REQUEST = 32 * 1024 * 1024
REASONING_EFFORTS = ("low", "medium", "high", "xhigh", "max")
# none: explicit mode without a marker (no cache read, no cache write); explicit_one_breakpoint: one marker at the end of the
# first message (every job before 2026-09-28); explicit_rolling: a marker on every input carrier, so each request reads the
# previous one's whole input from the cache (the default of new jobs, see apply_cache_breakpoint)
CACHE_MODES = ("none", "explicit_one_breakpoint", "explicit_rolling")
IMAGE_MIMES = ("image/png", "image/jpeg", "image/webp")

validate_tools_schema = legacy_transport.validate_tools_schema
validate_plan = legacy_transport.validate_plan
loads_strict = legacy_transport._loads          # duplicate keys and non-finite numbers rejected


class TransportError(RuntimeError):
    """The request was NOT sent (or a reply arrived that cannot be a provider response)."""


class PaidRefused(TransportError):
    """Paid mode is off: nothing leaves the machine."""


class UnknownOutcome(RuntimeError):
    """The request MAY have reached the provider (timeout after connect, read failure): financially unresolved."""


@dataclass
class HttpResult:
    status: int
    body: bytes
    seconds: float
    redacted: bool = False


# --------------------------------------------------------------------------- content blocks
def text_block(text: str) -> dict:
    return {"type": "input_text", "text": str(text)}


def sniff_image_mime(data: bytes) -> str | None:
    """The media type the bytes actually are (PNG, JPEG or WebP magic), None for anything else."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def image_block(data: bytes, mime: str, *, detail: str = "high") -> dict:
    """``mime`` is the caller's label (the artifact's suffix); the data URL carries the type the bytes are. test-pilot-002's
    photos were WebP files named .jpeg and went out as data:image/jpeg on every request (CE-10)."""
    if mime not in IMAGE_MIMES:
        raise TransportError(f"unsupported image mime {mime!r}")
    if len(data) > MAX_IMAGE_BYTES:
        raise TransportError(f"image of {len(data)} bytes exceeds {MAX_IMAGE_BYTES}")
    actual = sniff_image_mime(data)
    if actual is None:
        raise TransportError(f"the image bytes (labelled {mime}) are not PNG, JPEG or WebP")
    return {"type": "input_image", "image_url": "data:" + actual + ";base64," + base64.b64encode(data).decode("ascii"), "detail": detail}


def user_message(blocks: list[dict]) -> dict:
    return {"type": "message", "role": "user", "content": list(blocks)}


def developer_message(text: str, *, breakpoint: bool) -> dict:
    block = text_block(text)
    if breakpoint:
        block["prompt_cache_breakpoint"] = {"mode": "explicit"}
    return {"type": "message", "role": "developer", "content": [block]}


def function_output_item(call_id: str, output) -> dict:
    """``output`` is a string or a list of input_text / input_image blocks (images returned by a tool)."""
    if isinstance(output, list):
        for b in output:
            if not isinstance(b, dict) or b.get("type") not in ("input_text", "input_image"):
                raise TransportError("a function output list holds input_text / input_image blocks only")
    elif not isinstance(output, str):
        raise TransportError("a function output is a string or a list of blocks")
    return {"type": "function_call_output", "call_id": str(call_id), "output": output}


def _input_blocks(item: dict) -> list | None:
    """The content blocks of an input item that can carry images and a cache marker: a message's content list or a
    function_call_output's output list (None for reasoning, calls, string outputs)."""
    blocks = item.get("content") if item.get("type") == "message" else (item.get("output") if item.get("type") == "function_call_output" else None)
    return blocks if isinstance(blocks, list) else None


def image_count_in(items: list[dict]) -> int:
    """input_image blocks in a request's input (policy images_per_request bounds one tool output, not this: q0010 carried 55)."""
    return sum(1 for item in items for b in (_input_blocks(item) or []) if isinstance(b, dict) and b.get("type") == "input_image")


def image_bytes_in(items: list[dict]) -> int:
    total = 0
    for item in items:
        blocks = item.get("content") if item.get("type") == "message" else (item.get("output") if item.get("type") == "function_call_output" else None)
        if isinstance(blocks, list):
            for b in blocks:
                if isinstance(b, dict) and b.get("type") == "input_image":
                    url = str(b.get("image_url", ""))
                    total += (len(url) - url.find(",") - 1) * 3 // 4 if url.startswith("data:") else 0
    return total


# --------------------------------------------------------------------------- payloads
def tool_declarations(tools: dict) -> list[dict]:
    """Strict function tools from a registry {name: {description, parameters}} in a stable order."""
    out = []
    for name, t in tools.items():
        validate_tools_schema(t["parameters"])
        out.append({"type": "function", "name": name, "strict": True, "description": t["description"], "parameters": t["parameters"]})
    return out


def _mark_last_text(blocks: list) -> None:
    # the marker sits on the LAST input_text block (the docs name text blocks as carriers); an all-image list takes it on its last block
    idx = max((i for i, b in enumerate(blocks) if isinstance(b, dict) and b.get("type") == "input_text"), default=len(blocks) - 1)
    blocks[idx]["prompt_cache_breakpoint"] = {"mode": "explicit"}


def _is_carrier(item: dict) -> bool:
    """An input item the docs let carry an explicit breakpoint: a user message or a function_call_output with blocks."""
    if not _input_blocks(item):
        return False
    return item.get("type") == "function_call_output" or item.get("role") == "user"


def apply_cache_breakpoint(items: list[dict], mode: str) -> list[dict]:
    """Explicit-only caching (``prompt_cache_options.mode = explicit``). Every marker already in the items is stripped first.

    * ``none``: no marker, so the request neither reads nor writes a cache.
    * ``explicit_one_breakpoint``: exactly one marker at the end of the stable prefix, the developer message plus the first
      user message when present. Test-pilot-002 measured what it misses: the window is append-only (request n's input is
      a byte-identical prefix of request n+1's, 8 of 8 pairs), so everything after the first message was paid in full
      again on every request (cached_tokens 26,301 throughout; 423,629 re-sent tokens, $4.24 of the $7.79 run).
    * ``explicit_rolling``: a marker on the last text block of EVERY input carrier (user messages and tool outputs), the
      pattern of the docs' multi-turn agent ('a breakpoint is added after each tool result'). A marker depends only on
      its own item, so a marker once placed never moves and request n's input stays a byte-identical prefix of request
      n+1's; the lookup finds the previous request's end (it was that request's newest breakpoint) and only the new
      suffix is written. Replayed over run 2's usage this is $3.32 for the author instead of $6.84 (at most: a cache
      entry lives at least 30 minutes, and run 1 saw one miss inside that window; the runner records a miss as an event)."""
    if mode not in CACHE_MODES:
        raise TransportError(f"unknown cache mode {mode!r}")
    items = [json.loads(json.dumps(i)) for i in items]
    for item in items:
        for b in _input_blocks(item) or []:
            if isinstance(b, dict):
                b.pop("prompt_cache_breakpoint", None)
    if mode == "none":
        return items
    if mode == "explicit_rolling":
        carriers = [item for item in items[1:] if _is_carrier(item)]
        if not carriers and items and _input_blocks(items[0]):
            carriers = [items[0]]           # an empty window: the developer message alone carries it, as in the one-breakpoint mode
        if not carriers:
            raise TransportError("no message to carry the cache breakpoint")
        for item in carriers:
            _mark_last_text(_input_blocks(item))
        return items
    target = None
    for item in items[:2]:
        if item.get("type") == "message" and item.get("role") in ("developer", "user") and isinstance(item.get("content"), list) and item["content"]:
            target = item
    if target is None:
        raise TransportError("no message to carry the cache breakpoint")
    # the runner ends its first message with a text trailer, so the photos before the marker stay inside the cached prefix
    _mark_last_text(target["content"])
    return items


def build_payload(*, model: str, developer_text: str, window: list[dict], tools: dict, max_output_tokens: int, reasoning_effort: str,
                  tool_choice="required", cache_mode: str = "explicit_one_breakpoint", service_tier: str = "default") -> dict:
    if model != MODEL and not model.startswith(MODEL + "-"):
        raise TransportError(f"model {model!r} is not the frozen {MODEL}")
    if reasoning_effort not in REASONING_EFFORTS:
        raise TransportError(f"reasoning effort {reasoning_effort!r} unsupported")
    if type(max_output_tokens) is not int or not 256 <= max_output_tokens <= MODEL_MAX_OUTPUT_TOKENS:
        raise TransportError(f"max_output_tokens must be an integer in 256..{MODEL_MAX_OUTPUT_TOKENS}")
    if not developer_text.strip():
        raise TransportError("developer instructions are empty")
    items = apply_cache_breakpoint([developer_message(developer_text, breakpoint=False)] + list(window), cache_mode)
    if image_bytes_in(items) > MAX_IMAGES_BYTES_PER_REQUEST:
        raise TransportError("aggregate image bytes exceed the per-request bound")
    # explicit mode always: with cache_mode 'none' there is no marker, and explicit mode without a marker neither reads nor
    # writes. Until 2026-09-28 'none' omitted the option and the provider's implicit default wrote 19,391 (critic) and 19,172
    # (final) tokens at 1.25x for a cache nobody reads (F6)
    return {"model": model, "store": False, "service_tier": service_tier, "reasoning": {"effort": reasoning_effort},
            "include": ["reasoning.encrypted_content"], "max_output_tokens": max_output_tokens, "input": items,
            "tools": tool_declarations(tools), "tool_choice": tool_choice, "parallel_tool_calls": False, "prompt_cache_options": {"mode": "explicit"}}


# What POST /v1/responses/input_tokens accepts (see the module docstring for the source and date) and what it rejects.
# ``personality`` is accepted but never emitted by this route, so it is mirrored only if a payload ever carries it.
COUNT_MIRRORED_FIELDS = ("model", "input", "instructions", "tools", "tool_choice", "parallel_tool_calls", "reasoning", "text", "truncation",
                         "conversation", "previous_response_id", "personality")
COUNT_REJECTED_FIELDS = ("store", "include", "max_output_tokens", "service_tier", "prompt_cache_options", "stream", "metadata", "temperature", "top_p",
                         "background", "user", "safety_identifier", "prompt_cache_key", "prompt_cache_retention", "top_logprobs")
_COUNT_TOOL_DEPENDENT = ("tool_choice", "parallel_tool_calls")


def count_payload(payload: dict) -> dict:
    """The count body for POST /v1/responses/input_tokens: a pure function of a response (or compact) payload.

    Every field the counting endpoint accepts and the payload carries is mirrored verbatim (``COUNT_MIRRORED_FIELDS``:
    model, input, instructions, tools, tool_choice, parallel_tool_calls, reasoning, text, truncation, ...); every field
    it rejects is left out (``COUNT_REJECTED_FIELDS``: store, include, max_output_tokens, service_tier,
    prompt_cache_options, stream, ...). ``None`` values are not sent; ``tools`` is sent only when non-empty, and
    tool_choice / parallel_tool_calls only alongside tools (there is nothing to choose among otherwise). A compact body
    (model, input, tools, plus the max_output_tokens the runner attaches for its reservation) therefore counts as
    model/input/tools. The count is exact for the inference request only with respect to the configuration mirrored
    here: a field the endpoint does not accept (the explicit prompt-cache mode, the output bound, the service tier)
    cannot influence the count, and the cache breakpoint marker travels inside ``input`` unchanged. Until 2026-09-28
    only model/input/tools were sent, so tool_choice, parallel_tool_calls and reasoning were counted differently
    from how the inference request was billed."""
    out = {}
    tools = payload.get("tools")
    for key in COUNT_MIRRORED_FIELDS:
        if key == "tools":
            if tools:
                out["tools"] = tools
            continue
        if key in _COUNT_TOOL_DEPENDENT and not tools:
            continue
        value = payload.get(key)
        if value is not None:
            out[key] = value
    return out


def compact_payload(*, model: str, developer_text: str, window: list[dict], tools: dict) -> dict:
    """POST /v1/responses/compact: the full window; the reviewed schema has NO max_output_tokens and none is sent."""
    items = apply_cache_breakpoint([developer_message(developer_text, breakpoint=False)] + list(window), "none")
    return {"model": model, "input": items, "tools": tool_declarations(tools)}


def window_input_sha256(window: list[dict]) -> str:
    return sha256(canonical([sha256(canonical(i)) for i in window]))


# --------------------------------------------------------------------------- timeouts
# No request streams, so the first byte of a reply arrives after the whole reply is generated: the read timeout must cover
# the longest legal reply. Test-pilot-002's eleven requests (sent_utc -> completed_utc against usage.output_tokens) ran at
# 36-49 output tokens/s on its long replies (q0004: 7,196 tokens in 191 s = 37.7/s, the slowest long one; a least-squares
# fit over all eleven gives 41/s plus ~6e-5 s per input token). The bound assumes 25 tokens/s (two thirds of the slowest
# seen), 1 s per 10,000 input tokens (1.7x the fitted input cost) and 30 s of fixed overhead, never below the old 300 s.
READ_TIMEOUT_FLOOR_S = 300
BOUND_OUTPUT_TOKENS_PER_S = 25
BOUND_INPUT_TOKENS_PER_S = 10_000
READ_TIMEOUT_OVERHEAD_S = 30
WALL_MARGIN_S = 60
CONNECT_TIMEOUT_S = 15


def read_timeout_s(max_output_tokens: int, input_tokens: int = 0) -> int:
    """The HTTP read timeout for one reply of at most ``max_output_tokens`` over ``input_tokens`` (INF-02: the fixed 300 s
    would have cut any author reply above ~11,300 tokens at run 2's rates while the policy allows 24,000)."""
    t = READ_TIMEOUT_OVERHEAD_S + max(0, int(input_tokens)) / BOUND_INPUT_TOKENS_PER_S + max(0, int(max_output_tokens)) / BOUND_OUTPUT_TOKENS_PER_S
    return max(READ_TIMEOUT_FLOOR_S, int(math.ceil(t)))


# --------------------------------------------------------------------------- transports
class Transport:
    paid = False
    name = "abstract"

    def post(self, endpoint: str, payload: dict, *, timeout=(15, 300)) -> HttpResult:
        raise NotImplementedError

    def describe(self) -> dict:
        return {"transport": self.name, "paid": self.paid}


class RefusingTransport(Transport):
    """The default: every send is refused before any socket is opened."""
    name = "refusing"

    def post(self, endpoint, payload, *, timeout=(15, 300)):
        raise PaidRefused(f"paid mode is off; refusing to send to {endpoint}")


class HttpTransport(Transport):
    """Live HTTPS with a credential that never enters any artifact; one attempt, no redirects, no retries."""
    name = "https"
    paid = True

    def __init__(self, credential: str, *, session=None, max_seconds: float | None = None):
        if not isinstance(credential, str) or len(credential.strip()) < 8:
            raise TransportError("an explicit credential is required")
        self._credential = credential.strip()
        if session is None:
            import requests
            session = requests.Session()
            session.mount("https://", requests.adapters.HTTPAdapter(max_retries=0))
        self._session = session
        # None: the wall bound follows each call's read timeout (read_timeout_s); a number pins it (tests)
        self.max_seconds = None if max_seconds is None else float(max_seconds)

    def wall_seconds(self, timeout) -> float:
        """The wall-time bound of one call: the pinned max_seconds, else the read timeout plus WALL_MARGIN_S (until
        2026-09-28 a fixed 600 s, which a 24,000-token reply at 37.7 tokens/s already exceeds)."""
        if self.max_seconds is not None:
            return self.max_seconds
        read = timeout[1] if isinstance(timeout, (tuple, list)) else timeout
        return float(read) + WALL_MARGIN_S

    def post(self, endpoint, payload, *, timeout=(15, 300)):
        if not endpoint.startswith("https://api.openai.com/"):
            raise TransportError(f"refusing endpoint {endpoint!r}")
        raw = canonical(payload)
        started = time.monotonic()
        wall = self.wall_seconds(timeout)
        try:
            response = self._session.post(endpoint, headers={"Authorization": "Bearer " + self._credential, "Content-Type": "application/json"},
                                          data=raw, timeout=timeout, allow_redirects=False, stream=True)
        except Exception as e:  # noqa: BLE001 - connect or send failed: the provider may still have received it
            raise UnknownOutcome(f"{type(e).__name__} while sending; outcome unknown") from None
        try:
            try:
                status = int(response.status_code)
                chunks, size = [], 0
                for chunk in response.iter_content(chunk_size=65536):
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES or time.monotonic() - started > wall:
                        raise UnknownOutcome("response exceeded the byte or wall-time bound while being read")
                    chunks.append(chunk)
                body = b"".join(chunks)
            except UnknownOutcome:
                raise
            except Exception as e:  # noqa: BLE001 - the reply broke mid-stream: sent, outcome unknown
                raise UnknownOutcome(f"{type(e).__name__} while reading the reply") from None
        finally:
            response.close()
        cred = self._credential.encode()
        redacted = cred in body
        if redacted:
            body = body.replace(cred, b"[REDACTED]")
        return HttpResult(status=status, body=body, seconds=time.monotonic() - started, redacted=redacted)


class ScriptedTransport(Transport):
    """Deterministic replies for tests and the offline demo. ``steps`` is a list consumed in order; each step is
    {"endpoint": "responses"|"count"|"compact", "status": int, "body": dict} or {"endpoint": ..., "raise": "unknown"|"refused"},
    optionally with "expect": {"tools": [names], "last_item_type": str, "contains_call_output": call_id, "min_items": n,
    "has_image": bool, "no_text": [strings that must not appear anywhere in the payload]}. A count request without a
    scripted step is answered from a deterministic size rule. Every payload sent is kept in ``sent``."""
    name = "scripted"
    paid = False

    def __init__(self, steps: list[dict], *, count_rule=None):
        self.steps = list(steps)
        self.sent: list[dict] = []
        self.count_rule = count_rule or default_count_rule

    def post(self, endpoint, payload, *, timeout=(15, 300)):
        kind = {ENDPOINT: "responses", ENDPOINT_COUNT: "count", ENDPOINT_COMPACT: "compact"}.get(endpoint)
        if kind is None:
            raise TransportError(f"unknown endpoint {endpoint}")
        self.sent.append({"endpoint": kind, "payload": json.loads(canonical(payload))})
        if kind == "count" and not (self.steps and self.steps[0].get("endpoint") == "count"):
            return HttpResult(200, canonical({"object": "response.input_tokens", "input_tokens": self.count_rule(payload)}), 0.0)
        if not self.steps:
            raise TransportError(f"scripted transport exhausted at a {kind} request")
        step = self.steps.pop(0)
        if step.get("endpoint", kind) != kind:
            raise TransportError(f"scripted transport expected a {step.get('endpoint')} request, got {kind}")
        check_expectations(step.get("expect") or {}, payload)
        if step.get("raise") == "unknown":
            raise UnknownOutcome("scripted: outcome unknown")
        if step.get("raise") == "refused":
            raise PaidRefused("scripted: refused")
        return HttpResult(int(step.get("status", 200)), canonical(step["body"]), 0.0)


def default_count_rule(payload: dict) -> int:
    """A deterministic stand-in for the count endpoint: text characters / 4 plus a ceiling per image."""
    text = 0
    images = 0
    for item in payload.get("input", []):
        blocks = item.get("content") if isinstance(item.get("content"), list) else (item.get("output") if isinstance(item.get("output"), list) else None)
        if blocks is None:
            text += len(canonical(item))
            continue
        for b in blocks:
            if b.get("type") == "input_image":
                images += 1
            else:
                text += len(canonical(b))
    text += len(canonical(payload.get("tools", [])))
    return int(math.ceil(text / 4)) + images * 3000


def check_expectations(expect: dict, payload: dict) -> None:
    items = payload.get("input", [])
    if "tools" in expect:
        names = [t["name"] for t in payload.get("tools", [])]
        if names != list(expect["tools"]):
            raise AssertionError(f"scripted expectation: tools {names} != {expect['tools']}")
    if "last_item_type" in expect:
        last = items[-1].get("type") if items else None
        if last != expect["last_item_type"]:
            raise AssertionError(f"scripted expectation: last item type {last} != {expect['last_item_type']}")
    if "contains_call_output" in expect:
        if not any(i.get("type") == "function_call_output" and i.get("call_id") == expect["contains_call_output"] for i in items):
            raise AssertionError(f"scripted expectation: no function_call_output for {expect['contains_call_output']}")
    if "min_items" in expect and len(items) < int(expect["min_items"]):
        raise AssertionError(f"scripted expectation: {len(items)} items < {expect['min_items']}")
    if "has_image" in expect:
        has = image_bytes_in(items) > 0
        if bool(expect["has_image"]) != has:
            raise AssertionError(f"scripted expectation: has_image {has} != {expect['has_image']}")
    for needle in expect.get("no_text", []):
        if needle in canonical(payload).decode("utf-8", "replace"):
            raise AssertionError(f"scripted expectation: forbidden text {needle!r} present in the payload")
    for needle in expect.get("has_text", []):
        if needle not in canonical(payload).decode("utf-8", "replace"):
            raise AssertionError(f"scripted expectation: expected text {needle!r} absent from the payload")
    sealed = set(expect.get("no_sealed_pixels", []))
    if sealed:
        for pix in payload_image_pixel_hashes(payload):
            if pix in sealed:
                raise AssertionError("scripted expectation: a sealed photograph's pixels are in the payload")


def payload_image_pixel_hashes(payload: dict) -> list[str]:
    """Decoded-pixel hashes of every input_image in a payload (the leak sentinel a re-encoded copy cannot dodge)."""
    import io
    from PIL import Image
    out = []
    for item in payload.get("input", []):
        blocks = item.get("content") if isinstance(item.get("content"), list) else (item.get("output") if isinstance(item.get("output"), list) else [])
        for b in blocks or []:
            if isinstance(b, dict) and b.get("type") == "input_image":
                url = str(b.get("image_url", ""))
                if url.startswith("data:") and "," in url:
                    data = base64.b64decode(url.split(",", 1)[1])
                    with Image.open(io.BytesIO(data)) as im:
                        out.append(hashlib.sha256(im.convert("RGBA").tobytes()).hexdigest())
    return out


# --------------------------------------------------------------------------- parsing
@dataclass
class FunctionCall:
    call_id: str
    name: str
    arguments_raw: str
    item: dict
    arguments: dict | None = None
    parse_error: str | None = None


@dataclass
class ParsedResponse:
    status: str
    response_id: str | None
    model: str | None
    items: list[dict] = field(default_factory=list)
    function_calls: list[FunctionCall] = field(default_factory=list)
    texts: list[dict] = field(default_factory=list)          # {"phase", "text"}
    refusals: list[str] = field(default_factory=list)
    reasoning_items: int = 0
    compaction_items: int = 0
    unknown_types: list[str] = field(default_factory=list)
    usage: dict | None = None
    error: dict | None = None
    incomplete_details: dict | None = None

    @property
    def complete(self) -> bool:
        return self.status == "completed" and not self.error and not self.unknown_types

    @property
    def replayable_items(self) -> list[dict]:
        """Every output item verbatim, opaque fields included (what the next request must carry)."""
        return list(self.items)


def parse_response_body(raw: bytes, *, model: str) -> ParsedResponse:
    """Every item of a Responses body, or TransportError when the bytes are not a response object at all."""
    try:
        body = loads_strict(raw)
    except Exception as e:  # noqa: BLE001
        raise TransportError(f"response body is not strict JSON: {type(e).__name__}: {e}") from None
    if not isinstance(body, dict):
        raise TransportError("response body is not an object")
    status = body.get("status")
    if body.get("error") and status is None:
        status = "failed"
    if not isinstance(status, str):
        raise TransportError("response has no status")
    returned = body.get("model")
    if status == "completed" and not (isinstance(returned, str) and (returned == model or returned.startswith(model + "-"))):
        raise TransportError(f"response model {returned!r} is not the requested {model!r}")
    output = body.get("output")
    if output is None:
        output = []
    if not isinstance(output, list) or any(not isinstance(v, dict) for v in output):
        raise TransportError("response output is not a list of items")
    parsed = ParsedResponse(status=status, response_id=body.get("id") if isinstance(body.get("id"), str) else None,
                            model=returned if isinstance(returned, str) else None, items=list(output),
                            usage=body.get("usage") if isinstance(body.get("usage"), dict) else None,
                            error=body.get("error") if isinstance(body.get("error"), dict) else None,
                            incomplete_details=body.get("incomplete_details") if isinstance(body.get("incomplete_details"), dict) else None)
    for item in output:
        t = item.get("type")
        if t == "function_call":
            call_id, name, args = item.get("call_id"), item.get("name"), item.get("arguments")
            if not isinstance(call_id, str) or not call_id or not isinstance(name, str) or not isinstance(args, str):
                parsed.unknown_types.append("function_call:malformed")
                continue
            fc = FunctionCall(call_id=call_id, name=name, arguments_raw=args, item=item)
            if item.get("status") not in (None, "completed"):
                fc.parse_error = f"function call status {item.get('status')!r}"
            else:
                try:
                    a = loads_strict(args)
                    if not isinstance(a, dict):
                        raise ValueError("arguments are not an object")
                    fc.arguments = a
                except Exception as e:  # noqa: BLE001
                    fc.parse_error = f"{type(e).__name__}: {e}"
            parsed.function_calls.append(fc)
        elif t == "message":
            for b in item.get("content") or []:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "output_text":
                    parsed.texts.append({"phase": item.get("phase"), "text": str(b.get("text", ""))})
                elif b.get("type") == "refusal":
                    parsed.refusals.append(str(b.get("refusal", "")))
                else:
                    parsed.unknown_types.append(f"message:{b.get('type')}")
        elif t == "reasoning":
            parsed.reasoning_items += 1
        elif t == "compaction":
            parsed.compaction_items += 1
        else:
            parsed.unknown_types.append(str(t))
    return parsed


def parse_count_body(raw: bytes) -> int:
    try:
        body = loads_strict(raw)
    except Exception as e:  # noqa: BLE001
        raise TransportError(f"count body is not strict JSON: {e}") from None
    if not isinstance(body, dict) or body.get("object") != "response.input_tokens" or type(body.get("input_tokens")) is not int or body["input_tokens"] < 0:
        raise TransportError("count body is not a response.input_tokens object")
    return int(body["input_tokens"])


def parse_compact_body(raw: bytes, *, model: str) -> tuple[list[dict], dict | None, dict]:
    """(the returned canonical window, usage, the whole body) of a compact response."""
    try:
        body = loads_strict(raw)
    except Exception as e:  # noqa: BLE001
        raise TransportError(f"compact body is not strict JSON: {e}") from None
    if not isinstance(body, dict) or not isinstance(body.get("output"), list) or not body["output"]:
        raise TransportError("compact body carries no output window")
    if any(not isinstance(i, dict) or not isinstance(i.get("type"), str) for i in body["output"]):
        raise TransportError("compact window items are malformed")
    if body.get("error"):
        raise TransportError(f"compact response error: {body['error']}")
    if not any(i.get("type") == "compaction" for i in body["output"]):
        raise TransportError("compact window has no compaction item")
    return list(body["output"]), body.get("usage") if isinstance(body.get("usage"), dict) else None, body


def validate_call_arguments(fc: FunctionCall, tools: dict) -> tuple[dict | None, str | None]:
    """(validated arguments, None) or (None, structured error text) for the same call_id; nothing is executed on error."""
    if fc.name not in tools:
        return None, f"unknown tool {fc.name!r}; available: {sorted(tools)}"
    if fc.parse_error:
        return None, f"arguments rejected: {fc.parse_error}"
    try:
        return validate_plan(fc.arguments, tools[fc.name]["parameters"]), None
    except ValueError as e:
        return None, f"arguments do not satisfy the strict schema of {fc.name}: {e}"

