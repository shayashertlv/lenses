"""Explicit, bounded Meshy HTTPS transport; no credential or proxy discovery.

The caller supplies the key and permission to reserve at most one new task.
Credit limits constrain our documented estimate, NOT the provider's final bill:
https://docs.meshy.ai/en/api/pricing (checked 2026-09-21).
Cancellation stops local work only; it never deletes or cancels a provider task.

Every connection uses an already validated numeric DNS address, with the original
hostname retained for TLS verification. API requests never follow redirects or
retry. Downloads follow bounded public HTTPS redirects without Authorization.
"""
from __future__ import annotations

import base64
import hashlib
import http.client
import ipaddress
import json
import math
import os
from pathlib import Path
import queue
import re
import socket
import ssl
import threading
import time
from urllib.parse import urljoin, urlsplit
import uuid


API_HOST = "api.meshy.ai"
API_PATH = "/openapi/v1/multi-image-to-3d"
SPLIT_PATH = "/openapi/v1/print/split"
API_PATHS = (API_PATH, SPLIT_PATH)
# Observed consumed_credits of the 2026-09-21 split tasks (modeling pipeline
# receipts); the published pricing page lists no separate figure for it.
SPLIT_ESTIMATED_CREDITS = 10
SPLIT_SETTINGS = {"mode": "by_parts", "layout": "assembled", "target_formats": ["glb"]}
_SPLIT_TASK_ID = re.compile(r"[A-Za-z0-9_-]{1,160}\Z")
_SPLIT_PART = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _-]{0,39}\Z")
PRICING_URL = "https://docs.meshy.ai/en/api/pricing"
MAX_JSON_BYTES = 4 * 1024**2
MAX_IMAGE_BYTES = 20 * 1024**2
MAX_REQUEST_BYTES = 128 * 1024**2
MAX_MODEL_BYTES = 2 * 1024**3


class TransportError(RuntimeError):
    """Sanitized network/protocol failure; request outcome may be uncertain."""


class TransportPolicyError(ValueError):
    pass


class LocalCancellation(TransportError):
    """Local work stopped. An already submitted provider task may keep running."""


class HTTPStatusError(TransportError):
    def __init__(self, status: int):
        self.status = status
        super().__init__(f"HTTPS request returned status {status}")


class ExpiredDownloadError(HTTPStatusError):
    """An explicit provider expiry response permits same-task URL refresh."""


def _raw_json(value) -> bytes:
    return (json.dumps(value, sort_keys=True, allow_nan=False, indent=2) + "\n").encode()


def _exclusive(path: Path, raw: bytes):
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def split_settings(payload: dict) -> dict:
    """Validate a print/split request: fixed part mode, one input task, 1..10 part names."""
    if not isinstance(payload, dict) or set(payload) != {*SPLIT_SETTINGS, "input_task_id", "prompt"}:
        raise TransportPolicyError("A split request carries exactly mode, layout, target_formats, input_task_id and prompt")
    if any(payload[key] != value for key, value in SPLIT_SETTINGS.items()):
        raise TransportPolicyError("This adapter pins the assembled by-parts GLB split")
    if not isinstance(payload["input_task_id"], str) or not _SPLIT_TASK_ID.fullmatch(payload["input_task_id"]):
        raise TransportPolicyError("A split needs the generation task id")
    prompt = payload["prompt"]
    names = [p.strip() for p in prompt.split(",")] if isinstance(prompt, str) else []
    if not 1 <= len(names) <= 10 or any(not _SPLIT_PART.fullmatch(n) for n in names) or len(set(names)) != len(names):
        raise TransportPolicyError("A split prompt names one to ten distinct parts, comma separated")
    return dict(payload)


def estimated_credits(settings: dict) -> int:
    """Published Meshy 7.1 multi-image estimate, or the observed split cost; never a spend cap."""
    from .initializer import _settings
    if isinstance(settings, dict) and settings.get("mode") == "by_parts":
        split_settings(settings)
        return SPLIT_ESTIMATED_CREDITS
    settings = _settings(settings)
    cost = (35 if settings["texture_resolution"] == "8k" else 30) if settings["should_texture"] else 20
    return cost + (5 if settings.get("geometry_resolution") == "2k" else 0)


def _global_address(value: str):
    address = ipaddress.ip_address(value)
    if getattr(address, "ipv4_mapped", None) is not None:
        address = address.ipv4_mapped
    if not address.is_global or address.is_multicast or address.is_unspecified:
        raise TransportPolicyError("HTTPS destination must resolve only to public unicast addresses")
    return address


def _url(value: str, *, api: bool):
    if not isinstance(value, str) or len(value) > 16384 or any(ord(c) <= 32 or ord(c) >= 127 for c in value):
        raise TransportPolicyError("Expected an ASCII HTTPS URL without whitespace")
    try:
        parsed = urlsplit(value)
        host, port = (parsed.hostname or "").lower(), parsed.port
    except ValueError:
        raise TransportPolicyError("Invalid HTTPS destination") from None
    if parsed.scheme != "https" or not host or parsed.username or parsed.password or parsed.fragment or port not in (None, 443):
        raise TransportPolicyError("Only HTTPS port 443 without user information is supported")
    if host.endswith(".") or "%" in host or "\\" in value:
        raise TransportPolicyError("Ambiguous HTTPS destination")
    try:
        _global_address(host)
    except ValueError as error:
        if isinstance(error, TransportPolicyError):
            raise
        if "." not in host or host.endswith((".localhost", ".local", ".internal", ".home", ".test")):
            raise TransportPolicyError("Private HTTPS hostname is unsupported") from None
        if not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in host.split(".")):
            raise TransportPolicyError("Invalid HTTPS hostname") from None
    if api and (host != API_HOST or parsed.query or not any(re.fullmatch(
            re.escape(path) + r"(?:/[A-Za-z0-9_-]{1,160})?", parsed.path) for path in API_PATHS)):
        raise TransportPolicyError("Authentication is restricted to the Meshy task API")
    return parsed, host


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, address, timeout):
        super().__init__(host, port=443, timeout=timeout, context=ssl.create_default_context())
        self._address = address

    def connect(self):
        # No second DNS lookup: connect the exact sockaddr vetted by the caller.
        family, socktype, protocol, _, sockaddr = self._address
        raw = socket.socket(family, socktype, protocol)
        try:
            raw.settimeout(self.timeout)
            self.sock = raw  # deadline/cancellation watcher can interrupt connect
            raw.connect(sockaddr)
            if _global_address(raw.getpeername()[0]) != _global_address(sockaddr[0]):
                raise TransportPolicyError("Connected peer differs from the validated address")
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host, do_handshake_on_connect=False)
            self.sock.do_handshake()
        except BaseException:
            raw.close()
            raise


class MeshySubmissionBudget:
    """Aggregate allowance for new POSTs in one explicitly authorized session.

    Existing-task GETs/downloads cost no *new submission* allowance. A POST is
    counted before entering the network, including uncertain outcomes. Each
    phase still records its durable exclusive reservation, preventing a resumed
    client from repeating an earlier POST. This limits local documented cost
    estimates, not the provider's bill.
    """

    def __init__(self, *, max_new_tasks: int, max_estimated_credits: int):
        if type(max_new_tasks) is not int or not 0 <= max_new_tasks <= 2:
            raise TransportPolicyError("Aggregate max_new_tasks must be zero, one or two")
        if type(max_estimated_credits) is not int or max_estimated_credits < 0:
            raise TransportPolicyError("Aggregate credit allowance must be a nonnegative integer")
        self.max_new_tasks, self.max_estimated_credits = max_new_tasks, max_estimated_credits
        self._tasks = self._credits = 0
        self._lock = threading.Lock()

    def _checked(self, estimate):
        if type(estimate) is not int or estimate < 0:
            raise TransportPolicyError("A submission estimate must be a nonnegative integer")
        if self._tasks + 1 > self.max_new_tasks or self._credits + estimate > self.max_estimated_credits:
            raise TransportPolicyError("New task exceeds the aggregate submission or estimated credit allowance")
        return {"scope": "new_POSTs_in_this_explicit_client_session",
                "max_new_tasks": self.max_new_tasks, "max_estimated_credits": self.max_estimated_credits,
                "reserved_new_tasks": self._tasks, "reserved_estimated_credits": self._credits,
                "provider_hard_spend_ceiling": False}

    def check(self, estimate):
        with self._lock:
            return self._checked(estimate)

    def reserve(self, estimate, persist):
        """Atomically check, durably reserve the phase, then debit this session."""
        with self._lock:
            before = self._checked(estimate)
            receipt = {**before, "reserved_new_tasks_after": self._tasks + 1,
                       "reserved_estimated_credits_after": self._credits + estimate}
            persist(receipt)  # A failed exclusive write has not reserved a POST.
            self._tasks += 1
            self._credits += estimate
            return receipt


class BoundedMeshyTransport:
    """One reserved POST; resumable GET/downloads with immutable local receipts.

    Constructor is side-effect free. ``bind_output`` is called by MeshyBackend
    after the initializer request is pinned. Credentials are never persisted.
    Resolver/connection/clock injection exists for offline adversarial tests.
    """

    def __init__(self, api_key: str, *, max_new_tasks: int = 0, max_estimated_credits: int = 0,
                 request_timeout: float = 60, download_timeout: float = 900,
                 cancel_event: threading.Event | None = None, receipt_dir: Path | None = None,
                 resolver=None, connection_factory=None, clock=None,
                 submission_budget: MeshySubmissionBudget | None = None):
        if not isinstance(api_key, str) or not 1 <= len(api_key) <= 4096 or any(ord(c) < 33 or ord(c) > 126 for c in api_key):
            raise TransportPolicyError("Supply a nonempty printable API key explicitly")
        if type(max_new_tasks) is not int or max_new_tasks not in (0, 1):
            raise TransportPolicyError("max_new_tasks must be zero or one")
        if type(max_estimated_credits) is not int or max_estimated_credits < 0:
            raise TransportPolicyError("max_estimated_credits must be a nonnegative integer")
        for value in (request_timeout, download_timeout):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= 3600:
                raise TransportPolicyError("HTTPS deadlines must be finite and at most 3600 seconds")
        self._key = api_key
        if submission_budget is not None and not isinstance(submission_budget, MeshySubmissionBudget):
            raise TransportPolicyError("submission_budget must be an explicit MeshySubmissionBudget")
        self.submission_budget = submission_budget
        self.max_new_tasks, self.max_estimated_credits = max_new_tasks, max_estimated_credits
        self.request_timeout, self.download_timeout = request_timeout, download_timeout
        self.cancel_event = cancel_event or threading.Event()
        self._resolver = resolver or socket.getaddrinfo
        self._connection_factory = connection_factory or _PinnedHTTPSConnection
        self._clock = clock or time.monotonic
        self._directory = Path(receipt_dir).resolve() if receipt_dir is not None else None
        self._request_sha = None
        self._submitted = False
        self.last_receipt = None

    def bind_output(self, directory: Path, request_sha256: str):
        directory = Path(directory).resolve()
        if self._directory is not None and self._directory != directory:
            raise TransportPolicyError("A transport instance cannot change receipt directories")
        if self._request_sha is not None and self._request_sha != request_sha256:
            raise TransportPolicyError("A transport instance cannot change bound requests")
        self._directory, self._request_sha = directory, request_sha256

    def check_submission(self, settings: dict):
        self._check_cancel()
        estimate = estimated_credits(settings)
        if self.max_new_tasks != 1 or estimate > self.max_estimated_credits:
            raise TransportPolicyError("New task exceeds the explicit task count or estimated credit allowance")
        if self._submitted or self._directory is not None and (self._directory / "submission.json").exists():
            raise TransportPolicyError("A POST is already reserved; reconcile its outcome instead of retrying")
        shared = self.submission_budget.check(estimate) if self.submission_budget is not None else None
        return {"estimated_credits": estimate, "max_new_tasks": self.max_new_tasks,
                "max_estimated_credits": self.max_estimated_credits, "provider_hard_spend_ceiling": False,
                "pricing_source": PRICING_URL, "pricing_checked_date": "2026-09-21",
                **({"shared_submission_budget": shared} if shared is not None else {})}

    def _check_cancel(self):
        if self.cancel_event.is_set():
            raise LocalCancellation("Local cancellation; any provider task is left intact")

    def _remaining(self, deadline):
        self._check_cancel()
        remaining = deadline - self._clock()
        if remaining <= 0:
            raise TimeoutError("HTTPS operation deadline exceeded")
        return remaining

    def _addresses(self, host, deadline):
        # getaddrinfo itself has no portable timeout. A daemon resolver cannot
        # hold the job open; its result is never used after deadline/cancellation.
        result = queue.Queue(maxsize=1)
        def resolve():
            try:
                result.put((True, self._resolver(host, 443, type=socket.SOCK_STREAM)))
            except Exception:
                result.put((False, None))
        threading.Thread(target=resolve, daemon=True).start()
        while True:
            try:
                okay, addresses = result.get(timeout=min(.1, self._remaining(deadline)))
                break
            except queue.Empty:
                pass
        if not okay or not addresses:
            raise TransportError("DNS resolution failed")
        for entry in addresses:
            if entry[0] not in (socket.AF_INET, socket.AF_INET6) or entry[1] != socket.SOCK_STREAM or entry[4][1] != 443:
                raise TransportPolicyError("Unexpected resolved socket address")
            _global_address(entry[4][0])
        return addresses

    def _directory_required(self):
        if self._directory is None:
            raise TransportPolicyError("Bind an immutable receipt directory before HTTP execution")
        self._directory.mkdir(parents=True, exist_ok=True)
        return self._directory

    def _redact(self, value):
        if isinstance(value, str):
            return value.replace(self._key, "[REDACTED_API_KEY]")
        if isinstance(value, list):
            return [self._redact(item) for item in value]
        if isinstance(value, dict):
            return {self._redact(key): self._redact(item) for key, item in value.items()}
        return value

    def _exchange(self, method, url, *, api, body=None, max_bytes, deadline):
        parsed, host = _url(url, api=api)
        directory = self._directory_required() / ("http-" + uuid.uuid4().hex)
        directory.mkdir()
        request = {"method": method, "url": url, "authenticated_api": api,
                   "request_sha256": self._request_sha, "body_sha256": hashlib.sha256(body).hexdigest() if body else None,
                   "body_bytes": len(body) if body else 0, "max_response_bytes": max_bytes}
        _exclusive(directory / "request.json", _raw_json(self._redact(request)))
        started = self._clock()
        connection, response, active_socket, stop, watcher = None, None, None, threading.Event(), None
        raw, status, response_headers, error_type = bytearray(), None, {}, None
        try:
            addresses = self._addresses(host, deadline)
            connection = self._connection_factory(host, addresses[0], min(30, self._remaining(deadline)))
            def watch():
                while not stop.wait(.05):
                    if self.cancel_event.is_set() or self._clock() >= deadline:
                        # Closing alone may not interrupt a blocked read on every
                        # platform. shutdown wakes it before closing the socket.
                        active = active_socket or getattr(connection, "sock", None)
                        if active is not None:
                            try:
                                active.shutdown(socket.SHUT_RDWR)
                            except OSError:
                                pass
                        connection.close()
                        # connect/TLS can publish a replacement socket after a
                        # close races an earlier None/raw socket. Keep watching
                        # until the operation exits, including those sockets.
            watcher = threading.Thread(target=watch, daemon=True)
            watcher.start()
            headers = {"Accept": "application/json" if api else "application/octet-stream",
                       "Accept-Encoding": "identity", "Connection": "close", "User-Agent": "LensesOfflinePipeline/1"}
            if api:
                headers["Authorization"] = "Bearer " + self._key
            if body is not None:
                headers["Content-Type"] = "application/json"
            target = parsed.path or "/"
            if parsed.query:
                target += "?" + parsed.query
            self._remaining(deadline)
            connection.request(method, target, body=body, headers=headers)
            # HTTPConnection may release its socket to HTTPResponse when the
            # server says Connection: close; retain it for deadline shutdown.
            active_socket = getattr(connection, "sock", None)
            response = connection.getresponse()
            self._remaining(deadline)
            status = response.status
            response_headers = {key.lower(): value for key, value in response.getheaders()
                                if key.lower() in {"content-type", "content-length", "content-encoding", "location", "date", "x-request-id"}}
            if response_headers.get("content-encoding", "identity").lower() not in ("identity", ""):
                raise TransportPolicyError("Encoded HTTP response is unsupported")
            declared = response_headers.get("content-length")
            if declared is not None and (not declared.isdecimal() or int(declared) > max_bytes):
                raise TransportPolicyError("HTTP response exceeds the byte limit or has invalid length")
            while True:
                remaining = self._remaining(deadline)
                if getattr(connection, "sock", None) is not None:
                    connection.sock.settimeout(min(30, remaining))
                # read1 does at most one raw read; the outer deadline therefore
                # also bounds a peer that drips bytes without socket timeouts.
                chunk = response.read1(min(65536, max_bytes + 1 - len(raw)))
                raw.extend(chunk)
                if len(raw) > max_bytes:
                    raise TransportPolicyError("Streamed response exceeds the byte limit")
                self._remaining(deadline)
                if not chunk:
                    break
            if declared is not None and len(raw) != int(declared):
                raise TransportError("Truncated HTTP response")
        except BaseException as error:
            error_type = type(error).__name__
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            if self.cancel_event.is_set():
                raise LocalCancellation("Local cancellation; any provider task is left intact") from None
            if self._clock() >= deadline:
                raise TimeoutError("HTTPS operation deadline exceeded") from None
            if isinstance(error, TimeoutError):
                raise TimeoutError("HTTPS operation timed out") from None
            if isinstance(error, (TransportError, TransportPolicyError)):
                raise
            raise TransportError("HTTPS request failed; consult its sanitized receipt") from None
        finally:
            stop.set()
            if response is not None:
                response.close()
            if connection is not None:
                connection.close()
            if watcher is not None:
                watcher.join(timeout=.2)
            original = bytes(raw)
            # An API may echo a credential in its error body. Preserve everything
            # except the explicitly supplied secret; retain the original digest.
            saved = original.replace(self._key.encode(), b"[REDACTED_API_KEY]") if api else original
            if api:
                try:
                    decoded = json.loads(original)
                    redacted = self._redact(decoded)
                    if redacted != decoded:
                        saved = _raw_json(redacted)
                except (ValueError, UnicodeError):
                    pass
            _exclusive(directory / "response.bin", saved)
            metadata = {"status": status, "headers": response_headers, "bytes_received": len(original),
                        "raw_sha256": hashlib.sha256(original).hexdigest(),
                        "saved_sha256": hashlib.sha256(saved).hexdigest(), "secret_redacted": saved != original,
                        "elapsed_seconds": max(0, self._clock() - started), "error_type": error_type}
            # Header values are untrusted and can also echo the secret.
            _exclusive(directory / "response.json", _raw_json(self._redact(metadata)))
            self.last_receipt = {"directory": directory.name, "response_sha256": hashlib.sha256(saved).hexdigest(),
                                 "status": status, "error_type": error_type}
        return status, response_headers, bytes(raw)

    def _json(self, method, url, body=None):
        status, _, raw = self._exchange(method, url, api=True, body=body, max_bytes=MAX_JSON_BYTES,
                                        deadline=self._clock() + self.request_timeout)
        if not 200 <= status < 300:
            raise HTTPStatusError(status)  # includes redirects: never followed
        try:
            # Keep untrusted credential echoes out of adapter/report dictionaries.
            value = self._redact(json.loads(raw))
        except (ValueError, UnicodeError):
            raise TransportError("API response is not valid JSON") from None
        if not isinstance(value, dict):
            raise TransportError("API response must be a JSON object")
        return value

    def post_json(self, url: str, payload: dict) -> dict:
        parsed, _ = _url(url, api=True)
        if parsed.path not in API_PATHS:
            raise TransportPolicyError("POST is restricted to task creation")
        if parsed.path == SPLIT_PATH:
            settings = split_settings(payload)
            images = []
        else:
            settings = {key: value for key, value in payload.items() if key != "image_urls"}
            if settings.get("mode") == "by_parts":
                raise TransportPolicyError("A split payload must be posted to the split path")
            images = payload.get("image_urls")
            if not isinstance(images, list) or not 1 <= len(images) <= 4:
                raise TransportPolicyError("Expected one to four local image data URIs")
        allowance = self.check_submission(settings)
        for item in images:
            if not isinstance(item, str) or len(item) > 4 * ((MAX_IMAGE_BYTES + 2) // 3) + 64:
                raise TransportPolicyError("Image exceeds the byte limit")
            matched = re.fullmatch(r"data:image/(png|jpeg);base64,([A-Za-z0-9+/=]+)", item)
            if not matched:
                raise TransportPolicyError("Only PNG/JPEG data URIs are supported")
            try:
                decoded = base64.b64decode(matched[2], validate=True)
            except ValueError:
                raise TransportPolicyError("Invalid image data URI") from None
            if not 1 <= len(decoded) <= MAX_IMAGE_BYTES:
                raise TransportPolicyError("Image exceeds the byte limit")
        body = _raw_json(payload)
        if len(body) > MAX_REQUEST_BYTES:
            raise TransportPolicyError("API request exceeds the byte limit")
        directory = self._directory_required()
        reservation = {"request_sha256": self._request_sha, "payload_sha256": hashlib.sha256(body).hexdigest(),
                       "payload_bytes": len(body), "image_count": len(images), "settings": settings, **allowance}
        try:
            if self.submission_budget is None:
                _exclusive(directory / "submission.json", _raw_json(reservation))
            else:
                self.submission_budget.reserve(allowance['estimated_credits'], lambda aggregate:
                    _exclusive(directory / "submission.json", _raw_json({**reservation, 'shared_submission_budget': aggregate})))
        except FileExistsError:
            raise TransportPolicyError("A POST is already reserved; it must never be repeated") from None
        self._submitted = True
        return self._json("POST", url, body)

    def get_json(self, url: str) -> dict:
        parsed, _ = _url(url, api=True)
        if parsed.path in API_PATHS:
            raise TransportPolicyError("GET requires an already known task id")
        return self._json("GET", url)

    def download_public(self, url: str, *, max_bytes: int) -> bytes:
        if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_MODEL_BYTES:
            raise TransportPolicyError("Invalid model byte limit")
        deadline = self._clock() + self.download_timeout
        for redirect in range(6):
            status, headers, raw = self._exchange("GET", url, api=False, max_bytes=max_bytes, deadline=deadline)
            if status in {301, 302, 303, 307, 308}:
                if redirect == 5 or not headers.get("location"):
                    raise TransportPolicyError("Download redirect limit or missing Location")
                url = urljoin(url, headers["location"])
                _url(url, api=False)  # next exchange also validates actual DNS addresses
                continue
            if status in (401, 403) and any(marker in raw[:MAX_JSON_BYTES].lower() for marker in (
                    b"expiredtoken", b"requestexpired", b"request has expired", b"token has expired",
                    b"signature has expired", b"token expired", b"signature expired")):
                raise ExpiredDownloadError(status)
            if not 200 <= status < 300:
                raise HTTPStatusError(status)
            return raw
        raise AssertionError("Unreachable redirect state")
