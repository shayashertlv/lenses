"""Independent paid-provider adapters with durable receipts and no POST retries."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import json
import re
import struct
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .prompts import image_labels, stage_context, stage_instructions
from .script_validation import MAX_SCRIPT_BYTES, validate_script
from .workflows import canonical_pipeline

MESHY_BASE = "https://api.meshy.ai/openapi/v1"
OPENAI_RESPONSES = "https://api.openai.com/v1/responses"
ASTRA_MODEL = "gpt-6-astra"
MAX_RESPONSE_BYTES = 8_000_000
MAX_IMAGE_BYTES = 20_000_000
MAX_GLB_BYTES = 2_000_000_000
# One local container envelope in both directions. Meshy's Retexture docs do
# not publish an exact server byte limit; this is not a claim about their limit.
MAX_UPLOAD_BYTES = MAX_GLB_BYTES
MAX_JSON_BYTES = 4_000_000
REFERENCE_ORDER = ("front", "back", "left", "right")


class ProviderError(RuntimeError):
    pass


def meshy_settings(stage: str) -> dict:
    if stage == "generate":
        return {"ai_model": "meshy-7", "ultra_mode": True, "should_texture": False,
                "should_remesh": False, "target_formats": ["glb"]}
    if stage == "texture":
        return {"ai_model": "meshy-7", "enable_pbr": True,
                "texture_resolution": "8k", "enable_original_uv": False,
                "target_formats": ["glb"]}
    raise ValueError(f"Unknown Meshy stage: {stage}")


def _endpoint(stage: str) -> str:
    meshy_settings(stage)
    return MESHY_BASE + ("/multi-image-to-3d" if stage == "generate" else "/retexture")


def _json_file(path: Path, data: dict, *, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False)
    if exclusive:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(text)
    else:
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(path)


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _cancel_check(cancel: asyncio.Event) -> None:
    if cancel.is_set():
        raise asyncio.CancelledError("Provider operation cancelled; no retry was submitted")


async def _cancellable(awaitable, cancel: asyncio.Event, timeout: float):
    action = asyncio.ensure_future(awaitable)
    stopped = asyncio.create_task(cancel.wait())
    try:
        done, _ = await asyncio.wait({action, stopped}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        if stopped in done:
            raise asyncio.CancelledError("Provider operation cancelled")
        if action not in done:
            raise ProviderError("Provider operation timed out; no automatic retry was submitted")
        return action.result()
    finally:
        if not action.done():
            action.cancel()
        stopped.cancel()
        await asyncio.gather(action, stopped, return_exceptions=True)


def _data_uri(path: Path, *, model: bool = False) -> tuple[str, dict]:
    limit = MAX_UPLOAD_BYTES if model else MAX_IMAGE_BYTES
    size = path.stat().st_size
    if size <= 0 or size > limit:
        raise ProviderError(f"Input {'model' if model else 'image'} exceeds its {limit:,}-byte limit or is empty")
    payload = path.read_bytes()
    if len(payload) != size or len(payload) > limit:
        raise ProviderError("Provider input changed while it was being read")
    if model:
        _glb_header(payload[:20], len(payload))
        mime = "application/octet-stream"
    elif payload.startswith(b"\xff\xd8\xff"):
        mime = "image/jpeg"
    elif payload.startswith(b"\x89PNG\r\n\x1a\n"):
        mime = "image/png"
    else:
        raise ProviderError("Provider images must be normalized JPEG or PNG files")
    return "data:" + mime + ";base64," + base64.b64encode(payload).decode("ascii"), {
        "sha256": hashlib.sha256(payload).hexdigest(), "bytes": size, "mime": mime,
    }


def _glb_header(header: bytes, actual_size: int) -> None:
    if len(header) < 20 or header[:4] != b"glTF":
        raise ProviderError("Meshy model is not a GLB container")
    version, declared, chunk_size, chunk_type = struct.unpack_from("<IIII", header, 4)
    if version != 2 or declared != actual_size or chunk_type != 0x4E4F534A or chunk_size > declared - 20:
        raise ProviderError("Meshy GLB header or declared length is invalid")


async def _model_metadata(path: Path, cancel: asyncio.Event) -> dict:
    size = path.stat().st_size
    if size <= 0 or size > MAX_UPLOAD_BYTES:
        raise ProviderError("Edited GLB exceeds the 2 GB local model envelope or is empty")
    digest, count = hashlib.sha256(), 0
    with path.open("rb") as handle:
        _glb_header(handle.read(20), size)
        handle.seek(0)
        while block := handle.read(1024 * 1024):
            _cancel_check(cancel)
            count += len(block)
            if count > size:
                raise ProviderError("Edited GLB changed before submission")
            digest.update(block)
            await asyncio.sleep(0)
    if count != size:
        raise ProviderError("Edited GLB changed before submission")
    return {"sha256": digest.hexdigest(), "bytes": size, "mime": "application/octet-stream"}


def _model_json_header(payload: dict) -> bytes:
    # The image references are small normalized inputs; only the potentially
    # large GLB is streamed. Base64 needs no JSON escaping.
    prefix = json.dumps(payload, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode("utf-8")
    return prefix[:-1] + (b"," if payload else b"") + b'"model_url":"data:application/octet-stream;base64,'


async def _model_json_body(prefix: bytes, path: Path, metadata: dict, cancel: asyncio.Event):
    _cancel_check(cancel)
    yield prefix
    digest, count = hashlib.sha256(), 0
    with path.open("rb") as handle:
        # All non-final reads are divisible by three, so concatenated base64
        # chunks exactly equal the encoding of the complete model.
        while block := handle.read(3 * 64 * 1024):
            _cancel_check(cancel)
            count += len(block)
            if count > metadata["bytes"]:
                raise ProviderError("Edited GLB changed during submission; no retry was submitted")
            digest.update(block)
            yield base64.b64encode(block)
            await asyncio.sleep(0)
    if count != metadata["bytes"] or digest.hexdigest() != metadata["sha256"]:
        raise ProviderError("Edited GLB changed during submission; no retry was submitted")
    _cancel_check(cancel)
    yield b'"}'


def _download_url(url: str) -> str:
    if not isinstance(url, str):
        raise ProviderError("Meshy task has no downloadable GLB URL")
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.port not in (None, 443):
        raise ProviderError("Meshy download requires a public HTTPS URL")
    host = parts.hostname.lower()
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")) or "." not in host:
        raise ProviderError("Meshy download URL is not public")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if not address.is_global:
            raise ProviderError("Meshy download URL is not public")
    return url


class _HttpProvider:
    def __init__(self, key: str, client: httpx.AsyncClient | None = None):
        self.key = key.strip() if isinstance(key, str) else ""
        self.client = client or httpx.AsyncClient(timeout=httpx.Timeout(90, connect=20), follow_redirects=False)
        self.owns_client = client is None

    def _require_key(self):
        if not self.key:
            raise ProviderError("Configure the provider API key before starting a run")

    async def close(self):
        if self.owns_client:
            await self.client.aclose()

    async def _json_request(self, method: str, url: str, receipt: Path,
                            cancel: asyncio.Event, payload: dict | None = None, *,
                            model_path: Path | None = None, model_metadata: dict | None = None) -> dict:
        _cancel_check(cancel)
        self._require_key()
        headers = {"Authorization": "Bearer " + self.key}
        if model_path is not None:
            prefix = _model_json_header(payload or {})
            headers.update({"Content-Type": "application/json", "Content-Length": str(
                len(prefix) + 4 * ((model_metadata["bytes"] + 2) // 3) + 2)})
            stream = self.client.stream(method, url,
                content=_model_json_body(prefix, model_path, model_metadata, cancel),
                headers=headers, follow_redirects=False)
        else:
            stream = self.client.stream(method, url, json=payload, headers=headers, follow_redirects=False)
        response = None
        request_timeout = 900 if model_path is not None else 120
        deadline = time.monotonic() + request_timeout
        try:
            response = await _cancellable(stream.__aenter__(), cancel, request_timeout)
            content = bytearray()
            iterator = response.aiter_bytes()
            receipt.parent.mkdir(parents=True, exist_ok=True)
            with receipt.open("xb") as raw:
                while True:
                    try:
                        chunk = await _cancellable(anext(iterator), cancel, max(0, deadline - time.monotonic()))
                    except StopAsyncIteration:
                        break
                    if len(content) + len(chunk) > MAX_JSON_BYTES:
                        raise ProviderError("Provider JSON response exceeds its bounded size")
                    content.extend(chunk)
                    raw.write(chunk)
                    raw.flush()
            # Provider replies, not request payloads, are retained for diagnosis.
            if response.status_code >= 300:
                raise ProviderError(f"Provider HTTP {response.status_code}; response saved locally, no automatic retry")
            try:
                result = json.loads(content)
            except (ValueError, UnicodeError) as error:
                raise ProviderError("Provider returned invalid JSON; response saved locally") from error
            if not isinstance(result, dict):
                raise ProviderError("Provider JSON must be an object")
            return result
        except httpx.HTTPError as error:
            raise ProviderError("Provider network request failed; no automatic retry was submitted") from error
        finally:
            if response is not None:
                await stream.__aexit__(None, None, None)


class MeshyClient(_HttpProvider):
    poll_interval = 5.0
    poll_timeout = 3600.0
    download_timeout = 1800.0

    async def submit(self, stage: str, references: dict[str, Path], model_path: Path | None,
                     receipt_dir: Path, cancel: asyncio.Event) -> str:
        _cancel_check(cancel)
        self._require_key()
        payload = meshy_settings(stage)
        images, image_receipts = [], []
        for angle in REFERENCE_ORDER:
            if angle not in references:
                raise ProviderError(f"Missing {angle} Meshy reference")
            uri, info = _data_uri(Path(references[angle]))
            images.append(uri)
            image_receipts.append({"angle": angle, **info})
        request_receipt = {"provider": "meshy", "stage": stage, "settings": payload.copy(),
                           "references": image_receipts, "submitted_at": time.time(), "retry": False}
        if stage == "generate":
            if model_path is not None:
                raise ProviderError("Blank generation must not submit an existing model")
            payload["image_urls"] = images
        else:
            if model_path is None:
                raise ProviderError("Texturing requires the locally edited GLB")
            request_receipt["model"] = await _model_metadata(Path(model_path), cancel)
            payload["multiview_image_urls"] = images
        # An existing reservation is never permission for another paid POST.
        try:
            _json_file(receipt_dir / "submit_request.json", request_receipt, exclusive=True)
        except FileExistsError as error:
            raise ProviderError("This Meshy submission already has a reservation; automatic resubmission is forbidden") from error
        result = await self._json_request("POST", _endpoint(stage), receipt_dir / "submit_response.json", cancel, payload,
            model_path=Path(model_path) if model_path is not None else None,
            model_metadata=request_receipt.get("model"))
        task_id = result.get("result")
        if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", task_id):
            raise ProviderError("Meshy submission response has no valid task ID; do not submit it again automatically")
        _json_file(receipt_dir / "task_receipt.json", {"task_id": task_id, "stage": stage, "settings": meshy_settings(stage)})
        return task_id

    async def poll(self, stage: str, task_id: str, receipt_dir: Path, cancel: asyncio.Event) -> dict:
        if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", task_id):
            raise ProviderError("Invalid saved Meshy task ID")
        deadline = time.monotonic() + self.poll_timeout
        # Intermediate polls of a running task carry no diagnostic value beyond
        # the newest one. Keep the first reply, the latest pending reply and the
        # terminal reply; drop the pending replies in between.
        first_receipt = None
        pending_receipt = None
        while time.monotonic() < deadline:
            _cancel_check(cancel)
            receipt = receipt_dir / ("poll_" + uuid.uuid4().hex + ".json")
            task = await self._json_request("GET", _endpoint(stage) + "/" + task_id, receipt, cancel)
            if task.get("id") != task_id:
                raise ProviderError("Meshy returned a different task ID")
            status = task.get("status")
            _json_file(receipt_dir / "task.json", task)
            if status == "SUCCEEDED":
                return task
            if status in {"FAILED", "CANCELED", "CANCELLED", "EXPIRED"}:
                raise ProviderError(f"Meshy task {status.lower()}; saved task and paid accounting are retained")
            if status not in {"PENDING", "IN_PROGRESS", "QUEUED"}:
                raise ProviderError("Meshy task returned an unknown status")
            if first_receipt is None:
                first_receipt = receipt
            elif pending_receipt is not None:
                pending_receipt.unlink(missing_ok=True)
            pending_receipt = receipt if receipt != first_receipt else None
            try:
                await asyncio.wait_for(cancel.wait(), timeout=min(self.poll_interval, max(0.001, deadline - time.monotonic())))
            except TimeoutError:
                continue
            _cancel_check(cancel)
        raise ProviderError("Meshy polling deadline reached; saved task may still be running and was not resubmitted")

    async def download(self, task: dict, receipt_dir: Path, cancel: asyncio.Event) -> Path:
        _cancel_check(cancel)
        if task.get("status") != "SUCCEEDED":
            raise ProviderError("Only a successful Meshy task can be downloaded")
        url = _download_url((task.get("model_urls") or {}).get("glb"))
        receipt_dir.mkdir(parents=True, exist_ok=True)
        output = receipt_dir / "source.glb"
        complete_receipt = receipt_dir / "download_receipt.json"
        if output.exists() or complete_receipt.exists():
            if not output.is_file() or not complete_receipt.is_file():
                raise ProviderError("Saved download is incomplete; original artifacts were retained")
            saved = json.loads(complete_receipt.read_text(encoding="utf-8"))
            if saved.get("task_id") != task.get("id") or saved.get("sha256") != _sha(output):
                raise ProviderError("Saved Meshy download failed its integrity check")
            return output
        part = receipt_dir / ("download_" + uuid.uuid4().hex + ".part")
        response = None
        stream = None
        deadline = time.monotonic() + self.download_timeout
        try:
            # Validate every redirect, and never send the API Authorization header
            # to a model CDN. A caller-supplied client must not have auth defaults.
            for redirect in range(6):
                stream = self.client.stream("GET", url, headers={"Authorization": ""}, follow_redirects=False)
                response = await _cancellable(stream.__aenter__(), cancel, max(0, deadline - time.monotonic()))
                if response.status_code in {301, 302, 303, 307, 308}:
                    from urllib.parse import urljoin
                    location = response.headers.get("location")
                    next_url = _download_url(urljoin(url, location or ""))
                    await stream.__aexit__(None, None, None)
                    response = None
                    url = next_url
                    continue
                break
            if response is None:
                raise ProviderError("Meshy download exceeded the redirect limit")
            if response.status_code != 200:
                raise ProviderError(f"Meshy download HTTP {response.status_code}; no resubmission was made")
            declared = response.headers.get("content-length")
            if declared and (not declared.isdigit() or int(declared) > MAX_GLB_BYTES):
                raise ProviderError("Meshy download exceeds the 2 GB GLB limit")
            count, digest, header = 0, hashlib.sha256(), bytearray()
            iterator = response.aiter_bytes()
            with part.open("xb") as handle:
                while True:
                    try:
                        chunk = await _cancellable(anext(iterator), cancel, max(0, deadline - time.monotonic()))
                    except StopAsyncIteration:
                        break
                    count += len(chunk)
                    if count > MAX_GLB_BYTES:
                        raise ProviderError("Meshy download exceeds the 2 GB GLB limit")
                    if len(header) < 20:
                        header.extend(chunk[:20 - len(header)])
                    digest.update(chunk)
                    handle.write(chunk)
                    handle.flush()
            _cancel_check(cancel)
            _glb_header(bytes(header), count)
            part.replace(output)
            _json_file(complete_receipt, {"task_id": task.get("id"), "sha256": digest.hexdigest(), "bytes": count,
                                        "source_url_sha256": hashlib.sha256(url.encode()).hexdigest(), "path": output.name})
            return output
        except httpx.HTTPError as error:
            raise ProviderError("Meshy download network failure; partial bytes and saved task were retained") from error
        finally:
            if response is not None and stream is not None:
                await stream.__aexit__(None, None, None)


def _extract_script(response: dict) -> str:
    if response.get("status") != "completed":
        raise ProviderError("Astra did not complete its response; partial response was saved and will not execute")
    output = response.get("output")
    if not isinstance(output, list):
        raise ProviderError("Astra response has no output list")
    calls = [item for item in output if isinstance(item, dict) and item.get("type") == "custom_tool_call"]
    if len(calls) != 1 or calls[0].get("name") != "run_blender_python":
        raise ProviderError("Astra must return exactly one run_blender_python custom tool call")
    # A narrative message beside the one tool call is unwanted but harmless; the
    # paid script is still the tool input. Any other tool use is a different
    # response than the one requested.
    if any(not isinstance(item, dict) or item.get("type") not in {"reasoning", "message", "custom_tool_call"} for item in output):
        raise ProviderError("Astra returned another tool call instead of one Python tool response")
    call = calls[0]
    if call.get("status") not in (None, "completed") or not isinstance(call.get("input"), str):
        raise ProviderError("Astra custom tool was incomplete or has invalid Python input")
    return call["input"]


class AstraClient(_HttpProvider):
    response_timeout = 1800.0

    async def edit(self, stage: str, images: list[Path], context: dict,
                   receipt_dir: Path, cancel: asyncio.Event) -> str:
        _cancel_check(cancel)
        self._require_key()
        pipeline = canonical_pipeline(context.get('pipeline'), default='legacy')
        labels = image_labels(stage, pipeline=pipeline)
        if len(images) != len(labels):
            raise ProviderError(f"Astra {stage} requires exactly {len(labels)} images, received {len(images)}")
        content = [{"type": "input_text", "text": stage_context(stage, context)}]
        receipts = []
        for label, path in zip(labels, images, strict=True):
            uri, metadata = _data_uri(Path(path))
            content.extend([{"type": "input_text", "text": label}, {"type": "input_image", "image_url": uri, "detail": "high"}])
            receipts.append({"label": label, **metadata})
        payload = {
            "model": ASTRA_MODEL, "reasoning": {"effort": "high"}, "store": False,
            "stream": True, "max_output_tokens": 32_000, "parallel_tool_calls": False,
            "instructions": stage_instructions(stage, pipeline=pipeline),
            "input": [{"role": "user", "content": content}],
            "tools": [{"type": "custom", "name": "run_blender_python",
                       "description": "One complete raw Python script for this single Blender editing session."}],
            "tool_choice": {"type": "custom", "name": "run_blender_python"},
        }
        try:
            _json_file(receipt_dir / "astra_request.json", {
                "provider": "openai", "stage": stage, "pipeline": pipeline, "model": ASTRA_MODEL,
                "reasoning": payload["reasoning"], "max_output_tokens": payload["max_output_tokens"],
                "images": receipts, "instructions": payload["instructions"],
                "context": content[0]["text"], "submitted_at": time.time(),
                "tool_choice": payload["tool_choice"], "standalone": True, "retry": False,
            }, exclusive=True)
        except FileExistsError as error:
            raise ProviderError("This Astra request already has a reservation; automatic resubmission is forbidden") from error
        response = await self._response(payload, receipt_dir, cancel)
        _json_file(receipt_dir / "astra_response.json", response)
        script = _extract_script(response)
        # The actual returned code is durable even if the local validator rejects it.
        (receipt_dir / "astra_script.py").write_text(script, encoding="utf-8")
        try:
            validate_script(script)
        except ValueError as error:
            raise ProviderError(f"Astra Python failed local validation: {error}. Saved response retained; no Blender edit executed") from error
        return script

    async def _response(self, payload: dict, receipt_dir: Path, cancel: asyncio.Event) -> dict:
        stream = self.client.stream("POST", OPENAI_RESPONSES, json=payload,
                                    headers={"Authorization": "Bearer " + self.key}, follow_redirects=False)
        response = None
        deadline = time.monotonic() + self.response_timeout
        try:
            response = await _cancellable(stream.__aenter__(), cancel, min(120, self.response_timeout))
            count, buffer, completed = 0, bytearray(), None
            iterator = response.aiter_bytes()
            raw_path = receipt_dir / "astra_response.stream"
            with raw_path.open("xb") as raw:
                while True:
                    try:
                        chunk = await _cancellable(anext(iterator), cancel, max(0, deadline - time.monotonic()))
                    except StopAsyncIteration:
                        break
                    count += len(chunk)
                    if count > MAX_RESPONSE_BYTES:
                        raise ProviderError("Astra response exceeded the 8 MB limit; retained partial stream will not execute")
                    raw.write(chunk)
                    raw.flush()
                    buffer.extend(chunk)
                    if response.status_code >= 300:
                        continue
                    if "text/event-stream" in response.headers.get("content-type", ""):
                        # SSE separators can be split across arbitrary network chunks.
                        buffer[:] = buffer.replace(b"\r\n", b"\n")
                        while b"\n\n" in buffer:
                            event_bytes, _, rest = buffer.partition(b"\n\n")
                            buffer = bytearray(rest)
                            data = b"\n".join(line[5:].lstrip() for line in event_bytes.split(b"\n") if line.startswith(b"data:"))
                            if not data or data == b"[DONE]":
                                continue
                            try:
                                event = json.loads(data)
                            except (ValueError, UnicodeError) as error:
                                raise ProviderError("Astra returned malformed streaming JSON; raw bytes saved") from error
                            if not isinstance(event, dict):
                                raise ProviderError("Astra streaming event is not an object")
                            kind = event.get("type")
                            if kind == "response.completed":
                                if completed is not None:
                                    _json_file(receipt_dir / "astra_failure.json", {"type": "multiple_completed_responses"})
                                    raise ProviderError("Astra returned multiple completed responses")
                                completed = event.get("response")
                                if isinstance(completed, dict):
                                    # Completion can arrive just before cancellation or
                                    # a dropped connection. Preserve the paid result
                                    # now; executing it still requires normal validation.
                                    _json_file(receipt_dir / "astra_response.json", completed)
                            elif kind in {"response.failed", "response.incomplete", "error"}:
                                _json_file(receipt_dir / "astra_failure.json", event)
                                raise ProviderError("Astra response failed or was incomplete; raw bytes saved")
            if response.status_code >= 300:
                # Keep raw bytes private. Provider error messages can echo keys;
                # only a known code and hashes belong in the structured receipt.
                error_code = None
                try:
                    body = json.loads(buffer)
                    error = body.get("error") if isinstance(body, dict) else None
                    if isinstance(error, dict) and error.get("code") == "invalid_api_key":
                        error_code = "invalid_api_key"
                except (ValueError, UnicodeError):
                    pass
                _json_file(receipt_dir / "astra_http_error.json", {
                    "status_code": response.status_code, "error_code": error_code,
                    "response_sha256": _sha(raw_path),
                    "key_sha256": hashlib.sha256(self.key.encode()).hexdigest(),
                }, exclusive=True)
                if response.status_code == 401:
                    guidance = ("Correct the OpenAI key in API setup, then explicitly retry Astra from the saved stage. "
                                if error_code == "invalid_api_key" else
                                "Check OpenAI credentials and account access in API setup; the rejection response is saved locally. ")
                    raise ProviderError("Astra HTTP 401: OpenAI rejected authentication. " + guidance + "No automatic retry was made.")
                raise ProviderError(f"Astra HTTP {response.status_code}; response saved locally, no automatic retry")
            if "text/event-stream" not in response.headers.get("content-type", ""):
                try:
                    completed = json.loads(buffer)
                except (ValueError, UnicodeError) as error:
                    raise ProviderError("Astra returned invalid JSON; response saved locally") from error
            if not isinstance(completed, dict):
                raise ProviderError("Astra stream ended without a completed response; partial script will not execute")
            _cancel_check(cancel)
            return completed
        except httpx.HTTPError as error:
            raise ProviderError("Astra connection failed; saved response bytes retained, no automatic retry") from error
        finally:
            if response is not None:
                await stream.__aexit__(None, None, None)
