"""Anthropic-only synchronous HTTPX2 guard, with settlement below SDK parsing.

Only HTTP 429/529 with a valid retry-after may be resent. SSE usage is cumulative: start
usage is updated by the final message_delta. Closing an unfinished reply records
an unknown outcome. No token estimate or reservation participates in admission.
"""
from __future__ import annotations

import base64
import binascii
import codecs
import copy
from datetime import datetime, timezone
from decimal import Decimal
from email.utils import parsedate_to_datetime
import hashlib
import json
from pathlib import Path
import re
import time
import uuid

import httpx2

from glasses3d.agent.budget import create_json_exclusive, now_iso, write_json_atomic
from .money import LedgerRefused
from .pricing import UnknownCharge, price_usage, validate_usage_observation

MAX_WAIT_SECONDS = Decimal(1200)
_FILE = re.compile(r"/v1/files/[A-Za-z0-9_-]+")


def files_upload_params() -> dict[str, int]:
    """Pass these kwargs to client.files.upload: expire uploads after seven days.

    The runner supplies file=... separately. This is the documented
    expires_in_seconds upload field, not a Messages request option.
    """
    return {"expires_in_seconds": 604800}


class Refused(RuntimeError):
    """The guarded transport cannot send or accept this request."""


def _json(raw):
    def invalid(value):
        raise ValueError("non-finite JSON")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON field")
            result[key] = value
        return result
    return json.loads(raw, parse_constant=invalid, object_pairs_hook=unique)


def _digest(data):
    return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def _clean(value):
    """Archive image identity without archiving inline image bytes."""
    if isinstance(value, dict):
        result = {key: _clean(item) for key, item in value.items()}
        if value.get("type") == "base64" and "data" in value and not (
                isinstance(value["data"], dict) and {"sha256", "bytes"} <= value["data"].keys()):
            encoded = value["data"]
            try:
                raw = base64.b64decode(encoded, validate=True)
                result["data"] = _digest(raw)
            except (ValueError, TypeError, binascii.Error):
                result["data"] = {**_digest(str(encoded).encode()), "invalid_base64": True}
        return result
    if isinstance(value, list):
        return [_clean(item) for item in value]
    if isinstance(value, str) and value.lower().startswith("data:image/"):
        try:
            return _digest(base64.b64decode(value.split(",", 1)[1], validate=True))
        except (ValueError, IndexError, binascii.Error):
            return {**_digest(value.encode()), "invalid_base64": True}
    return value


def _image_strings(value):
    if isinstance(value, dict):
        if value.get("type") == "base64" and isinstance(value.get("data"), str):
            yield value["data"]
        for item in value.values():
            yield from _image_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _image_strings(item)
    elif isinstance(value, str) and value.lower().startswith("data:image/"):
        yield value


class _Receipt:
    def __init__(self, directory, request, payload, number, attempt):
        self.path = directory / f"{number or 'free'}-{attempt:03d}-{uuid.uuid4().hex}.json"
        self.images = sorted(set(_image_strings(payload)), key=len, reverse=True)
        self.data = {
            "schema": "g3d.claude_transport/1", "provider": "anthropic",
            "request_no": number, "attempt": attempt, "started": now_iso(),
            "finished": None, "method": request.method, "url": str(request.url),
            "request": _clean(payload), "request_id": None, "http_status": None,
            "raw_sse": "", "usage": None, "cost_usd": None, "outcome": "started",
        }
        create_json_exclusive(self.path, self.data)

    def save(self, **fields):
        self.data.update(fields)
        # Provider errors can echo request data. Never persist those image strings.
        encoded = json.dumps(_clean(self.data), ensure_ascii=False, allow_nan=False)
        images = sorted(set(self.images) | set(_image_strings(self.data)), key=len, reverse=True)
        for image in images:
            if image:
                encoded = encoded.replace(json.dumps(image, ensure_ascii=False)[1:-1],
                                          "[image data redacted]")
        write_json_atomic(self.path, json.loads(encoded))


class _Events:
    def __init__(self, model):
        self.model = model
        self.decoder = codecs.getincrementaldecoder("utf-8")()
        self.pending = ""
        self.lines = []
        self.raw = []
        self.usage = None
        self.final_usage = False
        self.stopped = False
        self.message_id = None
        self.stop_reason = None
        self.stop_details = None
        self.events = []

    def feed(self, chunk, *, final=False):
        text = self.decoder.decode(chunk, final=final)
        self.raw.append(text)
        self.pending += text
        # A CR at the chunk boundary may be the first half of CRLF.
        while match := re.search(r"\r\n|\r(?!$)|\n", self.pending):
            line = self.pending[:match.start()]
            self.pending = self.pending[match.end():]
            self._line(line)
        if final and self.pending.endswith("\r"):
            self._line(self.pending[:-1])
            self.pending = ""
        if final and (self.pending.strip() or self.lines):
            raise UnknownCharge("SSE ended inside an event")

    def _line(self, line):
        if not line:
            self._dispatch()
            self.lines = []
        elif not line.startswith(":"):
            self.lines.append(line)

    def _dispatch(self):
        if not self.lines:
            return
        data, named = [], None
        for line in self.lines:
            key, _, value = line.partition(":")
            value = value[1:] if value.startswith(" ") else value
            if key == "data":
                data.append(value)
            elif key == "event":
                named = value
        if named == "error":
            raise UnknownCharge("SSE error event")
        if not data:
            return
        event = _json("\n".join(data))
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            raise UnknownCharge("SSE event has no type")
        kind = event["type"]
        if named is not None and named != kind:
            raise UnknownCharge("SSE event type mismatch")
        if self.stopped:
            raise UnknownCharge("SSE data after message_stop")
        self.events.append(event)
        if kind == "error":
            raise UnknownCharge("SSE error event")
        if kind == "message_start":
            message = event.get("message")
            if self.usage is not None or not isinstance(message, dict):
                raise UnknownCharge("missing or repeated message_start")
            if message.get("model") != self.model:
                raise UnknownCharge("served model differs from approved model")
            self.message_id = message.get("id")
            if not isinstance(self.message_id, str) or not self.message_id:
                raise UnknownCharge("message has no id")
            usage = message.get("usage")
            if not isinstance(usage, dict) or "input_tokens" not in usage:
                raise UnknownCharge("message_start has no input usage")
            validate_usage_observation(self.model, usage)
            self.usage = copy.deepcopy(usage)
        elif kind == "message_delta":
            usage = event.get("usage")
            if self.usage is None or not isinstance(usage, dict) or "output_tokens" not in usage:
                raise UnknownCharge("message_delta has no final output usage")
            validate_usage_observation(self.model, usage)
            for key, new in usage.items():
                if key in self.usage:
                    _nondecreasing(self.usage[key], new)
            self.usage.update(copy.deepcopy(usage))
            self.final_usage = True
            delta = event.get("delta")
            if not isinstance(delta, dict):
                raise UnknownCharge("message_delta has no delta")
            self.stop_reason = delta.get("stop_reason")
            self.stop_details = delta.get("stop_details")
            if self.stop_reason is not None and not isinstance(self.stop_reason, str):
                raise UnknownCharge("invalid stop_reason")
        elif kind == "message_stop":
            if not self.final_usage or not self.stop_reason:
                raise UnknownCharge("message_stop without final usage")
            self.stopped = True
        elif kind not in {"ping", "content_block_start", "content_block_delta", "content_block_stop"}:
            raise UnknownCharge("unreviewed SSE event type")


def _nondecreasing(old, new):
    """A cumulative usage update cannot erase already reported billable work."""
    if type(old) is int:
        if (new is None and old) or (type(new) is int and new < old):
            raise UnknownCharge("cumulative usage decreased")
    elif isinstance(old, dict):
        for key, value in old.items():
            _nondecreasing(value, new.get(key) if isinstance(new, dict) else None)
    elif isinstance(old, list):
        if old and (not isinstance(new, list) or len(new) < len(old)):
            raise UnknownCharge("cumulative usage iterations disappeared")
        if isinstance(new, list):
            for previous, current in zip(old, new):
                _nondecreasing(previous, current)


class _Decoded(httpx2.SyncByteStream):
    """Use HTTPX's content decoder before inspecting the SSE event text."""
    def __init__(self, response):
        self.response = response

    def __iter__(self):
        yield from self.response.iter_bytes()

    def close(self):
        self.response.close()


class _Tee(httpx2.SyncByteStream):
    def __init__(self, stream, ledger, number, model, receipt):
        self.stream, self.ledger, self.number = stream, ledger, number
        self.model, self.receipt = model, receipt
        self.events = _Events(model)
        self.done = False
        self.closed = False

    def _finish(self, reason=None):
        if self.done:
            return
        self.done = True
        cost = None
        try:
            if reason:
                raise UnknownCharge(reason)
            if not self.events.stopped or not self.events.final_usage:
                raise UnknownCharge("stream ended without final usage and message_stop")
            cost = price_usage(self.model, self.events.usage)
        except UnknownCharge as error:
            reason = str(error)
        # M13 A2: normal-rate refusal usage is an upper bound, not a claim that
        # the provider charges every refusal category (FACTS.md section 1).
        refusal = self.events.stop_reason == "refusal"
        settlement = ({"refusal": True, "stop_details": self.events.stop_details,
                       "upper_bound": True} if refusal and not reason else {})
        fields = {
            "finished": now_iso(), "raw_sse": "".join(self.events.raw),
            "events": self.events.events, "usage": self.events.usage,
            "response_id": self.events.message_id, "stop_reason": self.events.stop_reason,
            "stop_details": self.events.stop_details,
            "cost_usd": None if reason else str(cost),
            "outcome": "unknown" if reason else "settled", "reason": reason,
            **settlement,
        }
        # Archive before settlement, as in agent/receipts.py.
        try:
            self.receipt.save(**fields)
        except BaseException:
            self.ledger.record_unknown(self.number, "receipt persistence failed")
            raise
        if reason:
            self.ledger.record_unknown(self.number, reason)
            raise Refused(reason)
        self.ledger.record_settled(self.number, self.receipt.data["request_id"],
                                   self.model, self.events.usage, cost, **settlement)

    def __iter__(self):
        try:
            for chunk in self.stream:
                self.events.feed(chunk)
                yield chunk
            self.events.feed(b"", final=True)
        except GeneratorExit:
            raise
        except BaseException as error:
            self._finish(str(error) if isinstance(error, UnknownCharge)
                         else f"stream failed ({type(error).__name__})")
            raise
        else:
            self._finish()

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.stream.close()
        finally:
            if not self.done:
                # SDK consumers can stop on message_stop without advancing to EOF.
                try:
                    self.events.feed(b"", final=True)
                    reason = None if self.events.stopped else "stream closed early"
                except (ValueError, UnicodeError) as error:
                    reason = f"stream closed with an incomplete event ({type(error).__name__})"
                self._finish(reason)


def _route(request):
    url = request.url
    if (url.scheme != "https" or url.host != "api.anthropic.com"
            or url.port not in (None, 443) or url.userinfo or url.fragment):
        raise Refused("only the global HTTPS Anthropic API is allowed")
    # beta=true is emitted by the SDK's documented Files/beta Messages APIs.
    path, _, query = url.raw_path.partition(b"?")
    if b"%" in path:
        raise Refused("unreviewed API path or query")
    path = path.decode("ascii", "strict")
    query_keys = set(url.params)
    allowed_keys = {"beta"}
    if query_keys - allowed_keys or ("beta" in url.params and url.params["beta"] != "true"):
        raise Refused("unreviewed API query")
    if request.method == "POST" and path == "/v1/messages":
        return "message"
    if request.method == "POST" and path == "/v1/messages/count_tokens":
        return "count"
    if path == "/v1/files" and request.method == "POST":
        return "file"
    if _FILE.fullmatch(path) and request.method == "GET":
        return "file"
    if _FILE.fullmatch(path) and request.method == "DELETE":
        return "delete"
    raise Refused("unreviewed API endpoint or method")


def _retry_after(value):
    if not isinstance(value, str) or not value.strip():
        raise Refused("HTTP 429/529 requires a valid retry-after header")
    value = value.strip()
    # Preserve fractional seconds supported by the existing guard, while
    # rejecting signs, exponent notation, NaN and infinities as invalid headers.
    if re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value):
        seconds = Decimal(value)
    else:
        try:
            when = parsedate_to_datetime(value)
            if when.tzinfo is None:
                raise ValueError("retry-after date must include a timezone")
            seconds = Decimal(str((when - datetime.now(timezone.utc)).total_seconds()))
        except (TypeError, ValueError, OverflowError):
            raise Refused("HTTP 429/529 requires a valid retry-after header") from None
    return max(Decimal(0), seconds)


class _Guard(httpx2.BaseTransport):
    def __init__(self, ledger, directory, inner, sleep, file_cache, free_only):
        self.ledger, self.directory = ledger, Path(directory)
        self.inner, self.sleep = inner, sleep
        self.file_cache = file_cache
        self.free_only = free_only or getattr(ledger, "free_only", False)
        if self.file_cache is not None:
            self.file_cache.bind_run(ledger.run_id, ledger=ledger)
        self.directory.mkdir(parents=True, exist_ok=True)

    def _close_before_body(self, response, receipt, number):
        try:
            response.close()
        except BaseException as error:
            rejected = 400 <= response.status_code < 500 or response.status_code == 529
            try:
                receipt.save(finished=now_iso(), outcome="rejected" if rejected else "unknown",
                             cost_usd="0" if rejected else None, reason="response close failed")
            finally:
                if number is not None:
                    if rejected:
                        self.ledger.record_rejected(number, response.status_code, "response close failed")
                    else:
                        self.ledger.record_unknown(number, "response close failed")
            raise Refused("response close failed; no automatic retry") from error

    def handle_request(self, request):
        kind = _route(request)
        if kind == "file" and request.method == "POST":
            try:
                with self.ledger.upload_admission():
                    return self._handle_request(request)
            except LedgerRefused as error:
                raise Refused(str(error)) from error
        return self._handle_request(request)

    def _handle_request(self, request):
        kind = _route(request)
        if kind == "message" and self.free_only:
            raise Refused("free-only transport cannot send Messages requests")
        if kind == "file" and request.method == "POST" and self.ledger.conversation_finished():
            raise Refused("conversation finished; uploads are closed")
        if kind == "delete":
            allowed, reason = self.ledger.may_delete_uploads()
            if not allowed:
                raise Refused(reason)
            file_id = request.url.path.rsplit("/", 1)[-1]
            try:
                owned = self.file_cache is not None and self.file_cache.owns_file(file_id)
            except (ValueError, OSError) as error:
                raise Refused("cannot verify this run's upload cache") from error
            if not owned:
                raise Refused("only this run's own uploads may be deleted")
        if request.headers.get("x-stainless-retry-count", "0") != "0":
            raise Refused("SDK retries must be disabled (max_retries=0)")
        body = request.read()
        try:
            payload = _json(body) if body else {}
        except (ValueError, UnicodeError):
            if kind != "file":
                raise Refused("request body must be a JSON object") from None
            payload = {"multipart_or_binary": _digest(body)}
        if not isinstance(payload, dict):
            raise Refused("request body must be a JSON object")
        number = None
        if kind == "message":
            if payload.get("service_tier") != "standard_only":
                raise Refused("Messages service_tier must be standard_only")
            if "speed" in payload and payload["speed"] != "standard":
                raise Refused("Messages speed must be standard")
            if "inference_geo" in payload and payload["inference_geo"] != "global":
                raise Refused("Messages inference_geo must be global")
            if "fallbacks" in payload:
                raise Refused("Messages fallbacks are not allowed")
            if payload.get("stream") is not True:
                raise Refused("Messages requests must stream SSE")
            config = payload.get("output_config")
            if not isinstance(config, dict) or config.get("effort") != self.ledger.effort:
                raise Refused("request effort differs from the owner's approval")
            try:
                number = self.ledger.start_request(payload.get("model"))
            except LedgerRefused as error:
                raise Refused(str(error)) from None
        wait, attempt = Decimal(0), 0
        started_waiting = time.monotonic()
        while True:
            if attempt and number is not None:
                allowed, reason = self.ledger.may_start_request()
                if not allowed:
                    self.ledger.record_not_sent(number, "retry refused: " + reason)
                    raise Refused(reason)
            attempt += 1
            try:
                receipt = _Receipt(self.directory, request, payload, number, attempt)
                if self.free_only:
                    authorization = getattr(self.ledger, "authorization", None) or {}
                    receipt.save(free_only=True, owner_words=authorization.get("owner_words"))
            except BaseException:
                if number is not None:
                    self.ledger.record_not_sent(number, "receipt creation failed before send")
                raise
            try:
                response = self.inner.handle_request(request)
            except BaseException as error:
                try:
                    receipt.save(finished=now_iso(), outcome="unknown", cost_usd="0" if number is None else None,
                                 reason=f"transport failed ({type(error).__name__})")
                finally:
                    if number is not None:
                        self.ledger.record_unknown(number, f"transport failed ({type(error).__name__})")
                raise Refused("transport failed; no automatic retry") from error
            receipt.data.update(http_status=response.status_code,
                                request_id=response.headers.get("request-id"))
            if response.status_code in (429, 529) and kind != "delete":
                # Never read the rejection body before resending. Headers are enough.
                self._close_before_body(response, receipt, number)
                try:
                    delay = _retry_after(response.headers.get("retry-after"))
                except Refused as error:
                    try:
                        receipt.save(finished=now_iso(), outcome="rejected", cost_usd="0",
                                     reason=str(error))
                    finally:
                        if number is not None:
                            self.ledger.record_rejected(number, response.status_code, str(error))
                    raise
                if max(wait, Decimal(str(time.monotonic() - started_waiting))) >= MAX_WAIT_SECONDS:
                    try:
                        receipt.save(finished=now_iso(), outcome="rejected",
                                     cost_usd="0", reason="retry wait cap reached")
                    finally:
                        if number is not None:
                            self.ledger.record_rejected(number, response.status_code, "retry wait cap reached")
                    raise Refused("retry wait cap reached (20 minutes)")
                remaining = MAX_WAIT_SECONDS - wait
                delay = min(delay, remaining)
                try:
                    receipt.save(finished=now_iso(), outcome="retry", cost_usd="0", wait_seconds=str(delay),
                                 assumed_unbilled_reconcile_with_console=True)
                    self.ledger.record_wait(number, response.status_code, delay)
                    self.sleep(float(delay))
                except BaseException:
                    if number is not None:
                        self.ledger.record_rejected(number, response.status_code, "retry wait interrupted")
                    raise
                wait += delay
                if wait >= MAX_WAIT_SECONDS:
                    if number is not None:
                        self.ledger.record_rejected(number, response.status_code, "retry wait cap reached")
                    raise Refused("retry wait cap reached (20 minutes)")
                continue
            if kind == "message":
                if response.status_code != 200:
                    self._close_before_body(response, receipt, number)
                    try:
                        receipt.save(finished=now_iso(),
                                     outcome="rejected" if 400 <= response.status_code < 500 else "unknown",
                                     cost_usd="0" if 400 <= response.status_code < 500 else None)
                    finally:
                        if 400 <= response.status_code < 500:
                            self.ledger.record_rejected(number, response.status_code)
                        else:
                            self.ledger.record_unknown(number, f"HTTP {response.status_code}")
                    raise Refused(f"HTTP {response.status_code}; no automatic retry")
                if response.headers.get("content-type", "").split(";", 1)[0].strip() != "text/event-stream":
                    self._close_before_body(response, receipt, number)
                    try:
                        receipt.save(finished=now_iso(), outcome="unknown", reason="response is not SSE")
                    finally:
                        self.ledger.record_unknown(number, "response is not SSE")
                    raise Refused("response is not SSE")
                upstream = httpx2.Response(response.status_code, headers=response.headers, stream=response.stream)
                # Bytes reaching both the SSE parser and the client are already decoded.
                response.headers.pop("content-encoding", None)
                response.headers.pop("content-length", None)
                response.stream = _Tee(_Decoded(upstream), self.ledger, number, payload["model"], receipt)
            else:
                try:
                    content = response.read()
                    try:
                        reply = _json(content)
                    except (ValueError, UnicodeError):
                        reply = _digest(content)
                    receipt.save(finished=now_iso(), outcome="free" if response.is_success else "free_failed",
                                 response=reply, cost_usd="0")
                except BaseException as error:
                    receipt.save(finished=now_iso(), outcome="free_failed", cost_usd="0",
                                 reason=f"free response failed ({type(error).__name__})")
                    response.close()
                    raise
            return response

    def close(self):
        self.inner.close()


def guarded_client(ledger, receipts_dir, *, inner=None, sleep=time.sleep,
                   file_cache=None, free_only=False) -> httpx2.Client:
    """A single guard shared by every parent/specialist request in this run."""
    transport = _Guard(ledger, receipts_dir,
                       inner if inner is not None else httpx2.HTTPTransport(retries=0, trust_env=False),
                       sleep, file_cache, free_only)
    return httpx2.Client(transport=transport, trust_env=False, follow_redirects=False)
