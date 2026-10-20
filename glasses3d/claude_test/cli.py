"""Explicit commands and a private, read-only review for the Claude experiment."""
from __future__ import annotations

import argparse
import asyncio
import base64
import copy
from dataclasses import fields
from decimal import Decimal
import hashlib
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import mimetypes
import os
from pathlib import Path
import sys
import threading
from urllib.parse import quote, urlsplit
from uuid import uuid4

import anthropic
from anthropic.types.beta.message_count_tokens_params import MessageCountTokensParams
import httpx2
from PIL import Image

from glasses3d.agent import delivery, jobs, review
from glasses3d.agent.budget import _is_link, now_iso, write_json_atomic
from glasses3d.agent.offline import (
    LoopbackOnly, ProductionTransportTripwire, disable_sdk_tracing, require_offline,
)

from . import images, money, runner, transport
from .conversation import CONTEXT_BETA, canonical_bytes, opening_content, request_body
from .money import _arithmetic, _text
from .tool_adapter import tool_definitions

_REVIEW_SERVERS: dict[str, tuple[ThreadingHTTPServer, threading.Thread, dict]] = {}


def _read(path: Path) -> dict:
    if _is_link(path):
        raise ValueError("linked records are not allowed")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("expected a JSON object")
    return value


def copy_job(source: Path, destination: Path) -> Path:
    """Copy exactly the verified owner manifest and its original/transport photos."""
    source = Path(source).resolve()
    job = jobs.load_job(source)
    target = runner.confined(destination)
    if target.name != job["id"] or source == target:
        raise ValueError("the fresh copy must retain its source job id")
    names = {"job.json"}
    names.update(row[key] for row in job["images"] for key in ("file", "sent_file"))
    original = {}
    for name in sorted(names):
        path = source / name
        if _is_link(path):
            raise ValueError("owner inputs cannot be filesystem links")
        original[name] = path.read_bytes()
    target.mkdir(parents=True, exist_ok=False)
    for name, data in original.items():
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(data)
    jobs.load_job(target)
    # Recheck both sides after the copy; a racing source mutation must refuse.
    if any((source / name).read_bytes() != data or (target / name).read_bytes() != data
           for name, data in original.items()):
        raise ValueError("owner inputs changed while copying")
    write_json_atomic(target / "copy-provenance.json", {
        "schema": "g3d.claude_input_copy/1", "source": str(source), "created": now_iso(),
        "inputs": {name: hashlib.sha256(data).hexdigest() for name, data in original.items()},
        "owner_inputs_only": True,
    })
    return target


def _load_spec(job_dir: Path, run_id: str, *, env_file: Path | None = None, offline=False):
    root = runner.confined(job_dir)
    run_id = runner._token(run_id)
    request = _read(root / "runs" / run_id / "run-request.json")
    settings = request["settings"]
    if settings.get("client_mode") != ("offline" if offline else "production"):
        raise ValueError("prepared request belongs to a different client mode")
    keys = {field.name for field in fields(runner.ClaudeSettings)}
    selected = {key: value for key, value in settings.items() if key in keys}
    selected["cutoff_usd"] = Decimal(selected["cutoff_usd"])
    spec, scene = runner.prepare_run(root, settings=runner.ClaudeSettings(**selected),
                                    run_id=run_id, env_file=env_file, offline=offline)
    runner._request_bytes(spec)
    return spec, scene


def _count_opening(body: dict, input_receipt: dict) -> tuple[dict, dict]:
    """Project the real opening onto the pinned SDK's counting request schema."""
    accepted = MessageCountTokensParams.__annotations__
    counted = copy.deepcopy({key: value for key, value in body.items() if key in accepted})
    omitted = {
        key: {"value": copy.deepcopy(value),
              "reason": "Not accepted by the pinned SDK's beta count_tokens request schema."}
        for key, value in body.items() if key not in accepted
    }
    by_id = {row["file_id"]: row for row in input_receipt["images"]}
    for message in counted["messages"]:
        for block in message["content"]:
            if block.get("type") != "image":
                continue
            row = by_id[block["source"]["file_id"]]
            path = runner.confined(Path(row["tile_path"]))
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != row["sha256"]:
                raise ValueError("opening image bytes changed before token counting")
            encoded = base64.b64encode(raw).decode("ascii")
            if len(encoded) > images.LIMITS["max_image_bytes"]:
                raise ValueError("opening photo exceeds the base64 counting image limit")
            with Image.open(io.BytesIO(raw)) as source:
                mime = Image.MIME[source.format]
            block["source"] = {"type": "base64", "media_type": mime, "data": encoded}
    if len(canonical_bytes(counted)) >= images.LIMITS["max_request_bytes"]:
        raise ValueError("opening token-count request must be below 32,000,000 bytes")
    return counted, omitted


def _livecheck_http(directory: Path) -> list[dict]:
    calls = []
    for path in (directory / "transport").glob("*.json"):
        row = _read(path)
        calls.append({
            key: row.get(key) for key in
            ("method", "url", "http_status", "request_id", "outcome", "started", "finished")
        } | {"receipt": path.relative_to(directory).as_posix()})
    return sorted(calls, key=lambda row: (row["started"] or "", row["receipt"]))


def livecheck(job_dir: Path, *, env_file: Path, owner_ok: str, run_id: str | None = None,
              cutoff_usd: Decimal = Decimal("20"), inner=None) -> dict:
    """Check uploads/metadata/opening count only; the wire guard forbids inference.

    ``inner`` is an offline-test-only MockTransport. The production CLI never
    exposes injection or reads a key until its explicit owner words are checked.
    """
    if not isinstance(owner_ok, str) or not owner_ok.strip():
        raise ValueError("livecheck requires the owner's explicit words")
    offline = inner is not None
    if offline:
        require_offline(runner.OFFLINE_KEY)
        if not isinstance(inner, httpx2.MockTransport):
            raise ValueError("offline livechecks require httpx2.MockTransport")
    check_id = "livecheck-" + uuid4().hex
    directory = runner.confined(runner.WORK_ROOT / "livecheck" / check_id)
    settings = runner.ClaudeSettings(cutoff_usd=cutoff_usd)
    spec, _ = runner.prepare_run(
        job_dir, settings=settings, run_id=run_id or check_id,
        env_file=env_file, offline=offline,
    )
    if len(spec.photos) != 6:
        raise ValueError("the M13 livecheck requires exactly six owner photos")
    directory.mkdir(parents=True, exist_ok=False)
    receipt_path = directory / "livecheck.json"
    result = {
        "schema": "g3d.claude_livecheck/1", "provider": "anthropic", "model": settings.model,
        "id": check_id, "run_id": spec.run_id, "job_id": spec.job_dir.name,
        "owner_words": owner_ok, "free_only": True, "offline": offline,
        "started": now_iso(), "finished": None, "status": "started",
        "settings": settings.record(), "request_sha256": spec.request_sha256,
        "input_tokens": None, "uploads": [], "file_metadata": [], "deletions": [],
        "http_calls": [], "messages_requests": 0, "settled_usd": "0",
        "receipt": str(receipt_path),
    }
    write_json_atomic(receipt_path, result)
    ledger = money.Ledger.for_livecheck(
        directory / "ledger.jsonl", run_id=check_id, owner_words=owner_ok,
        model=settings.model, effort=settings.effort,
    )
    client = None

    def uploader(payload, filename):
        response = client.files.upload(
            file=(filename, payload, mimetypes.guess_type(filename)[0]),
            **transport.files_upload_params(),
        )
        metadata = response.model_dump(mode="json", exclude_unset=True)
        result["uploads"].append(metadata)
        return metadata

    files = images.FileCache(directory / "file-cache.json", uploader, run_id=check_id)
    try:
        key = runner.OFFLINE_KEY if offline else runner._production_key(spec.env_file)
        client = anthropic.Anthropic(
            api_key=key, http_client=transport.guarded_client(
                ledger, directory / "transport", inner=inner, file_cache=files, free_only=True),
            max_retries=0, timeout=900,
        )
        content, inputs = opening_content(spec, files=files)
        write_json_atomic(directory / "input-receipt.json", inputs)
        body = request_body(spec, [{"role": "user", "content": content}],
                            tools=tool_definitions(spec.tools))
        dimensions = {row["file_id"]: (row["w"], row["h"]) for row in inputs["images"]}
        problems = images.check_request(body, dimensions)
        if problems:
            raise ValueError("; ".join(problems))
        result["opening_request"] = body
        for file_id in dict.fromkeys(row["file_id"] for row in inputs["images"]):
            metadata = client.files.retrieve_metadata(file_id)
            result["file_metadata"].append(metadata.model_dump(mode="json", exclude_unset=True))
        count_body, omitted = _count_opening(body, inputs)
        result["count_request"] = transport._clean(count_body)
        result["count_omitted_fields"] = omitted
        result["count_schema"] = "anthropic.types.beta.message_count_tokens_params.MessageCountTokensParams"
        result["count_request_bytes"] = len(canonical_bytes(count_body))
        response = client.beta.messages.count_tokens(**count_body, betas=[CONTEXT_BETA])
        result["count_response"] = response.model_dump(mode="json", exclude_unset=True)
        if type(response.input_tokens) is not int or response.input_tokens < 0:
            raise ValueError("token count must be a non-negative integer")
        result["input_tokens"] = response.input_tokens
        result["status"] = "completed"
    except BaseException as exc:
        result["status"] = "interrupted" if not isinstance(exc, Exception) else "failed"
        # SDK exception messages can echo request data; keep only structured facts.
        result["error"] = {"type": type(exc).__name__, "http_status": getattr(exc, "status_code", None),
                           "request_id": getattr(exc, "request_id", None)}
        if not isinstance(exc, Exception):
            raise
    finally:
        try:
            cleanup = runner.delete_uploads(client, files, ledger, directory)
            result["upload_cleanup"] = cleanup
            result["deletions"] = cleanup["results"]
        except Exception as exc:
            result["upload_cleanup"] = {
                "uploads": len(result["uploads"]), "deleted": 0, "results": [],
                "failed": [{"error": {"type": type(exc).__name__},
                            "reason": "Cleanup did not complete; inspect transport and cache receipts."}],
            }
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception as exc:
                    result.setdefault("teardown_errors", []).append({"type": type(exc).__name__,
                                                                    "step": "client.close"})
            result["finished"] = now_iso()
            result["http_calls"] = _livecheck_http(directory)
            rows = runner._ledger_rows(ledger)
            result["messages_requests"] = sum(row.get("event") == "started" for row in rows)
            result["settled_usd"] = str(ledger.settled_usd())
            write_json_atomic(receipt_path, result)
    return result


def status(job_dir: Path, run_id: str) -> dict:
    root = runner.confined(job_dir)
    directory = root / "runs" / runner._token(run_id)
    request = _read(directory / "run-request.json")
    result = _read(directory / "result.json") if (directory / "result.json").is_file() else {}
    cutoff = Decimal(request["settings"]["cutoff_usd"])
    settled, unknown, pending = Decimal(0), [], set()
    ledger = directory / "ledger.jsonl"
    if ledger.is_file():
        # Pure read: status must never open/recover a ledger or consume approval.
        rows = ledger.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(rows):
            try:
                row = json.loads(line)
            except ValueError:
                if index == len(rows) - 1:
                    break  # append in progress; the next poll sees the whole row
                raise
            event, number = row.get("event"), row.get("request_no")
            if event == "started":
                pending.add(number)
            elif event in {"settled", "unknown", "not_sent", "rejected"}:
                pending.discard(number)
            if event == "settled":
                settled = _arithmetic(settled, Decimal(row["cost_usd"]))
            elif event == "unknown":
                unknown.append(row.get("reason"))
    state = result.get("status") or ("running" if ledger.exists() else
                                  "approved" if (directory / "approval-binding.json").is_file() else "prepared")
    return {
        "provider": "anthropic", "model": request["model"], "job_id": root.name, "run_id": run_id,
        "status": state, "offline": request["settings"]["client_mode"] == "offline",
        "budget": {"settled_usd": _text(settled), "cutoff_usd": _text(cutoff),
                   "left_usd": _text(max(Decimal(0), _arithmetic(cutoff, settled, subtract=True)))},
        "unknown_charges": unknown, "pending_requests": len(pending),
        "stop_requested": (root / "stop.requested").exists(),
        "result": str(directory / "result.json") if result else None,
    }


def history(work_root: Path | None = None) -> list[dict]:
    root = Path(work_root or runner.WORK_ROOT).resolve()
    if root != runner.WORK_ROOT.resolve():
        runner.confined(root)
    results = []
    for path in sorted(root.rglob("run-request.json")):
        if path.parent.parent.name != "runs":
            continue
        try:
            results.append(status(path.parent.parent.parent, path.parent.name))
        except (ValueError, OSError, KeyError) as exc:
            results.append({"run_id": path.parent.name, "status": "unreadable",
                            "error": str(exc)[:500]})
    return results


def request_stop(job_dir: Path, run_id: str) -> dict:
    record = status(job_dir, run_id)
    if record["status"] != "running":
        return {**record, "stop_written": False, "reason": "the selected run is not active"}
    root = runner.confined(job_dir)
    # One job lock protects all runs; do not turn a historical stop into a
    # stop for a different currently active run.
    heartbeat = _read(root / "heartbeat.json") if (root / "heartbeat.json").is_file() else {}
    if heartbeat.get("run_id") not in (None, run_id):
        raise ValueError("another run owns this job's heartbeat")
    write_json_atomic(root / "stop.requested", {"run_id": run_id, "requested": now_iso(),
                                               "provider": "anthropic"})
    return {**record, "stop_requested": True, "stop_written": True}


def _review_assets(root, directory, delivered, summary, native):
    assets: dict[str, tuple[bytes, str]] = {}
    photos, evidence = [], []
    job = jobs.load_job(root)
    for i, row in enumerate(job["images"], 1):
        route = f"/photos/{i}"
        data = (root / row["sent_file"]).read_bytes()
        assets[route] = (data, row["sent_mime"])
        photos.append(f'<figure><img src="{route}" alt="Owner photo {i}"><figcaption>'
                      f'{html.escape(row["original_name"])} — {html.escape(row["view_kind"])}'
                      '</figcaption></figure>')
    manifest = _read(Path(delivered["manifest"]))
    base = Path(delivered["manifest"]).parent
    for name, metadata in manifest["files"].items():
        if Path(name).suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            continue
        data = (base / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != metadata["sha256"]:
            raise ValueError("delivery evidence changed")
        route = "/evidence/" + quote(name, safe="")
        assets[route] = (data, mimetypes.guess_type(name)[0] or "application/octet-stream")
        evidence.append(f'<figure><img loading="lazy" src="{route}" alt="{html.escape(name)}">'
                        f'<figcaption>{html.escape(name)}</figcaption></figure>')
    rendered_summary = json.dumps(summary, indent=2, ensure_ascii=False)
    assets["/summary.json"] = (rendered_summary.encode(), "application/json")
    document = f"""<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Claude run {html.escape(directory.name)}</title>
<style>body{{font:16px system-ui;margin:2rem;background:#f5f6f8;color:#172030}}
main{{max-width:1100px;margin:auto}}img{{max-width:100%;height:auto}}figure{{margin:1rem 0;background:white;padding:1rem}}
pre{{white-space:pre-wrap;overflow-wrap:anywhere}}a{{color:#084eac}}</style><main>
<h1>Anthropic · {html.escape(summary["model"])}</h1>
<p>Run {html.escape(directory.name)} · {html.escape(summary.get("status", "unknown"))}</p>
<p>{"Offline scripted tool test; no owner product verdict." if summary.get("tool_test") else "Product run; appearance remains for the owner to judge."}</p>
<p><a href="{html.escape(native["url"], quote=True)}">Open verified AR model</a> ·
<a href="/summary.json">Run summary JSON</a></p>
<h2>Owner photos</h2>{''.join(photos)}
<h2>Delivered evidence</h2>{''.join(evidence)}
<h2>Run summary</h2><pre>{html.escape(rendered_summary)}</pre></main></html>"""
    assets["/"] = (document.encode("utf-8"), "text/html; charset=utf-8")
    return assets


def review_run(job_dir: Path, run_id: str, *, port: int = 0) -> dict:
    root = runner.confined(job_dir)
    directory = root / "runs" / runner._token(run_id)
    result = _read(directory / "result.json")
    outcome = _read(directory / "delivery.json")
    delivery_id = outcome.get("delivery_id") or outcome.get("id")
    if not delivery_id:
        raise ValueError("this run has no immutable delivery to review")
    delivered = delivery.load_delivery(root, delivery_id)
    if delivered["report_data"]["run_id"] != run_id:
        raise ValueError("delivery belongs to another run")
    summary = (_read(directory / "summary.json") if (directory / "summary.json").is_file()
               else {key: result.get(key) for key in ("status", "model", "provider", "tool_test", "budget")})
    summary.update(provider="anthropic", model=result["model"])
    native = review.review_job(root, delivery_id, port=port)
    state = _read(Path(native["server"]["state_path"]))
    server = None
    try:
        assets = _review_assets(root, directory, delivered, summary, native)
        instance = uuid4().hex

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                # Exact allowlist, no filesystem route or directory listing.
                route = urlsplit(self.path).path
                value = assets.get(route)
                if value is None:
                    self.send_error(404)
                    return
                payload, mime = value
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Security-Policy",
                                 "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; base-uri 'none'")
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, name=f"claude-review-{instance}", daemon=True)
        thread.start()
        _REVIEW_SERVERS[instance] = (server, thread, state)
        record = {**native, "provider": "anthropic", "model": result["model"], "run_id": run_id,
                  "review_id": instance, "native_review": native,
                  "page_url": f"http://127.0.0.1:{server.server_port}/",
                  "review_server": {"instance": instance, "port": server.server_port, "pid": os.getpid()}}
        (directory / "review").mkdir(parents=True, exist_ok=True)
        write_json_atomic(directory / "review" / "current.json", record)
        return record
    except BaseException:
        for key, owned in list(_REVIEW_SERVERS.items()):
            if owned[0] is server:
                _REVIEW_SERVERS.pop(key, None)
        if server is not None:
            server.shutdown()
            server.server_close()
        review._stop_owned(state)
        raise


def stop_review(record: dict) -> None:
    owned = _REVIEW_SERVERS.pop(record.get("review_id"), None)
    if owned is None:
        return
    server, thread, native_state = owned
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)
    review._stop_owned(native_state)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Private M13 Claude test runner")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "scripted"):
        command = commands.add_parser(name)
        command.add_argument("--source", type=Path, required=True)
        command.add_argument("--destination", type=Path)
        command.add_argument("--run-id")
        command.add_argument("--cutoff", type=Decimal, required=True)
        command.add_argument("--model", default=runner.MODEL)
        command.add_argument("--effort", default="max")
        if name == "scripted":
            command.add_argument("--script", type=Path, required=True,
                                 help="JSON array of ScriptTurn fields, offline only")
    for name in ("approve", "run", "status", "stop", "review"):
        command = commands.add_parser(name)
        command.add_argument("--job-dir", type=Path, required=True)
        command.add_argument("--run-id", required=True)
        if name == "approve":
            command.add_argument("--cutoff", type=Decimal, required=True)
            command.add_argument("--model", required=True)
            command.add_argument("--effort", default="max")
            command.add_argument("--words", required=True)
        if name == "run":
            command.add_argument("--env-file", type=Path,
                                 default=Path(__file__).parent / ".env")
        if name == "review":
            command.add_argument("--port", type=int, default=0)
    history_parser = commands.add_parser("history")
    history_parser.add_argument("--work-root", type=Path, default=runner.WORK_ROOT)
    check = commands.add_parser("livecheck", help="Free-only Files and opening token-count check")
    check.add_argument("job_dir", type=Path)
    check.add_argument("--env-file", type=Path, required=True)
    check.add_argument("--owner-ok", required=True)
    check.add_argument("--run-id", help="Use the intended run ID in the opening text")
    check.add_argument("--cutoff", type=Decimal, default=Decimal("20"),
                       help="Match the intended opening text; grants no spending permission")
    return parser


def _show(value):
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str), flush=True)


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if callable(getattr(stream, "reconfigure", None)):
            stream.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    command = args.command
    try:
        if command in {"prepare", "scripted"}:
            settings = runner.ClaudeSettings(model=args.model, cutoff_usd=args.cutoff, effort=args.effort)
            settings.validate()
            source = Path(args.source).resolve()
            destination = args.destination or runner.WORK_ROOT / "jobs" / jobs.load_job(source)["id"]
            root = copy_job(source, destination)
            spec, scene = runner.prepare_run(root, settings=settings, run_id=args.run_id,
                                             write_request=True, offline=command == "scripted")
            if command == "prepare":
                _show({"status": "prepared", "job_dir": root, "run_id": spec.run_id,
                       "request_sha256": spec.request_sha256, "budget_cutoff_usd": str(args.cutoff)})
                return 0
            from .scripted import ScriptedClaude, ScriptTurn
            script = json.loads(args.script.read_text(encoding="utf-8"))
            if not isinstance(script, list):
                raise ValueError("script must be an array of turns")
            wire = ScriptedClaude([ScriptTurn(**item) for item in script])
            previous = os.environ.get("G3D_OFFLINE")
            os.environ["G3D_OFFLINE"] = "1"
            try:
                disable_sdk_tracing()
                with LoopbackOnly(), ProductionTransportTripwire():
                    runner.approve_run(spec, words="Offline scripted fixture only; zero provider requests.")
                    output = asyncio.run(runner.execute_run(spec, scene=scene, inner=wire.transport()))
                    audit = wire.assert_complete()
                _show({**output, "script": audit})
                return 0 if output["result"]["status"] == "completed" else 3
            finally:
                if previous is None:
                    os.environ.pop("G3D_OFFLINE", None)
                else:
                    os.environ["G3D_OFFLINE"] = previous
        if command == "approve":
            spec, _ = _load_spec(args.job_dir, args.run_id)
            if (args.cutoff != spec.settings.cutoff_usd or args.model != spec.settings.model
                    or args.effort != spec.settings.effort):
                raise ValueError("explicit approval model, effort and cutoff must match the prepared request")
            _show(runner.approve_run(spec, words=args.words))
        elif command == "run":
            spec, scene = _load_spec(args.job_dir, args.run_id, env_file=args.env_file)
            output = asyncio.run(runner.execute_run(spec, scene=scene))
            _show(output)
            return 0 if output["result"]["status"] == "completed" else 3
        elif command == "status":
            _show(status(args.job_dir, args.run_id))
        elif command == "stop":
            _show(request_stop(args.job_dir, args.run_id))
        elif command == "history":
            _show(history(args.work_root))
        elif command == "livecheck":
            output = livecheck(args.job_dir, env_file=args.env_file, owner_ok=args.owner_ok,
                               run_id=args.run_id, cutoff_usd=args.cutoff)
            _show({key: output[key] for key in (
                "status", "id", "receipt", "input_tokens", "messages_requests", "settled_usd",
                "upload_cleanup",
            )})
            return 0 if output["status"] == "completed" and not output["upload_cleanup"]["failed"] else 2
        elif command == "review":
            record = review_run(args.job_dir, args.run_id, port=args.port)
            _show(record)
            try:
                _REVIEW_SERVERS[record["review_id"]][1].join()
            finally:
                stop_review(record)
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        _show({"status": "refused" if command in {"prepare", "approve", "run", "scripted"} else "failed",
               "phase": command, "error": f"{type(exc).__name__}: {exc}"})
        return 2 if command != "run" else 3
