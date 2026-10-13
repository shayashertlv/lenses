"""Open immutable delivered bytes in the existing detached AR server, then bind the owner's review to them.

The Node command owns its build/start deadlines and Windows WMI detach. Do not wrap the persistent server in a
preview process job or detach a second time. Each invocation has private state/log paths; a busy port is refused,
never stopped or silently replaced. A binding is published only after the live report and served bytes agree.
"""
from __future__ import annotations

import copy
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from . import delivery, procs
from .tools.arview import ARVIEW, AR_APPEARANCE_POLICY, LENS_ENV_INTENSITY

REVIEW_SCHEMA = "g3d.review_session/1"


class ReviewError(ValueError):
    """No verified review session could be established for this delivery."""


def _read(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ReviewError(f"cannot read review evidence: {path}: {error}") from error


def _loopback(port, route, *, method="GET", headers=None, limit=4 * 1024 * 1024):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request(method, route, headers={"Connection": "close", **(headers or {})})
        response = connection.getresponse()
        data = response.read(limit + 1)
        if len(data) > limit:
            raise ReviewError("AR server response exceeds the expected size")
        return response.status, dict(response.getheaders()), data
    except (OSError, http.client.HTTPException) as error:
        raise ReviewError(f"AR server is not ready: {error}") from error
    finally:
        connection.close()


def _report(port):
    status, _headers, body = _loopback(port, "/__arview/report.json")
    if status != 200:
        raise ReviewError(f"AR server report is not ready (HTTP {status})")
    try:
        report = json.loads(body)
    except ValueError as error:
        raise ReviewError("AR server returned an invalid report") from error
    if not isinstance(report, dict) or report.get("schema") != "arview.serve/1":
        raise ReviewError("AR server returned an unknown report")
    return report


def _same_server(state, report):
    server = report.get("server") or {}
    return (isinstance(server, dict) and type(state.get("pid")) is int and state["pid"] > 0
            and bool(state.get("instance")) and server.get("pid") == state["pid"]
            and server.get("instance") == state["instance"])


def _stop_owned(state):
    """Cleanup only a server whose live instance still matches this invocation; never kill by port or PID."""
    try:
        if _same_server(state, _report(state["port"])):
            _loopback(state["port"], "/__arview/stop", method="POST",
                      headers={"X-Arview-Stop-Token": state["token"]})
    except (ReviewError, KeyError, TypeError):
        pass


def _serve(node, model, port, launch):
    state_dir = launch / "state"
    state_dir.mkdir()
    log_path = launch / "startup.log"
    argv = [node, str(ARVIEW / "bin/arview.mjs"), "serve", str(model), "--detach", "--port", str(port),
            "--state-dir", str(state_dir), "--lensenv", str(LENS_ENV_INTENSITY)]
    env = {key: value for key, value in os.environ.items() if key.upper() in procs.WORKER_ENV_KEYS}
    flags = {"creationflags": procs.CREATE_NO_WINDOW} if os.name == "nt" else {}
    with log_path.open("ab") as log:
        # The CLI exits after its own bounded readiness check; its WMI-created server survives this process.
        result = subprocess.run(argv, cwd=ARVIEW, env=env, stdin=subprocess.DEVNULL, stdout=log,
                                stderr=subprocess.STDOUT, close_fds=True, **flags)
    if result.returncode:
        raise ReviewError(f"AR detached startup failed (exit {result.returncode}); see {log_path}")
    paths = list(state_dir.glob("port-*.json"))
    if len(paths) != 1:
        raise ReviewError(f"AR startup produced no unique server state; see {log_path}")
    state = _read(paths[0])
    if not isinstance(state, dict):
        raise ReviewError(f"AR startup produced an invalid server state; see {log_path}")
    text = log_path.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"started the detached server \(pid (\d+), (wmi|spawn)\)", text)
    method = match[2] if match and int(match[1]) == state.get("pid") else None
    return state, paths[0], method, log_path


def _verify(state, model, digest, requested_port, method, *, draft=False):
    if (not isinstance(state, dict) or state.get("schema") != "arview.serve-state/1"
            or state.get("ready") is not True or type(state.get("pid")) is not int or state["pid"] <= 0
            or not isinstance(state.get("instance"), str) or not state["instance"]
            or not isinstance(state.get("launch_id"), str) or not state["launch_id"]
            or type(state.get("port")) is not int or not 1024 <= state["port"] <= 65535
            or (requested_port and state["port"] != requested_port)
            or state.get("sha256") != digest or Path(state.get("model", "")).resolve() != model):
        raise ReviewError("AR readiness state does not describe the requested immutable delivery")
    if method not in ("wmi", "spawn") or (os.name == "nt" and method != "wmi"):
        raise ReviewError("AR review needs a verified detached start (Windows requires WMI); see startup.log")
    port = state["port"]
    report = _report(port)
    if (not _same_server(state, report) or (report.get("server") or {}).get("detached") is not True
            or report.get("served_sha256") != digest or (report.get("model") or {}).get("sha256") != digest
            or Path((report.get("model") or {}).get("path", "")).resolve() != model
            or (report.get("handover") or {}).get("lensenv") != LENS_ENV_INTENSITY
            or report.get("page_url") != state.get("url")):
        raise ReviewError("live AR report differs from this server, delivery or fixed lens appearance")
    spec_ok = (report.get("verdict") or {}).get("spec_ok")
    if report.get("errors") or (spec_ok is not True and not (draft and spec_ok is False)):
        raise ReviewError("live AR report has errors or cannot confirm the delivered specification")
    page = urlsplit(state["url"])
    query = parse_qs(page.query)
    if (page.scheme != "http" or page.hostname != "127.0.0.1" or page.port != port
            or page.username or page.password or query.get("sha256") != [digest]
            or query.get("lensenv") != [str(LENS_ENV_INTENSITY)]):
        raise ReviewError("AR review address does not bind the model and fixed lens appearance")
    model_url = urlsplit((report.get("model") or {}).get("url", ""))
    if (model_url.scheme != "http" or model_url.hostname != "127.0.0.1" or model_url.port != port
            or model_url.username or model_url.password or model_url.query or model_url.fragment
            or not model_url.path.startswith(f"/__arview/models/{digest}/")
            or query.get("model") != [model_url.geturl()]):
        raise ReviewError("AR model address is not the expected loopback immutable model")
    status, _headers, served = _loopback(port, model_url.path, limit=model.stat().st_size)
    if status != 200 or hashlib.sha256(served).hexdigest() != digest:
        raise ReviewError("AR server served bytes different from the immutable delivery")
    return report


def _delivery_context(delivered):
    """Review readiness is an identity check; the immutable delivery retains its finish and quality results."""
    report = delivered.get("report_data") or {}
    finished = report.get("finished") is not False  # immutable historical deliveries were accepted finishes
    ar = report.get("arview") or {}
    return {"finished": finished, "accepted_finish": finished,
            "run_status": report.get("run_status", "completed" if finished else None),
            "delivery_report": delivered.get("report"),
            "delivery_checks": {"validate_spec": copy.deepcopy(report.get("validate_spec")),
                                "seating": copy.deepcopy(report.get("seating")),
                                "print_check": copy.deepcopy(report.get("print_check")),
                                "arview": {key: copy.deepcopy(ar[key]) for key in
                                           ("status", "exit_code", "errors", "warnings") if key in ar}}}


def review_job(root, delivery_id=None, port=8251):
    """Start and verify the immutable delivery's detached webcam page; return its URL and saved hash binding."""
    if type(port) is not int or (port != 0 and not 1024 <= port <= 65535):
        raise ReviewError("port must be 0 or an integer from 1024 to 65535")
    root = Path(root).resolve()
    delivered = delivery.load_delivery(root, delivery_id)
    delivery_context = _delivery_context(delivered)
    model = Path(delivered["model"]).resolve()
    node = shutil.which("node")
    if node is None:
        raise ReviewError("Node.js is required for the AR review server")
    launch = root / "review" / "servers" / uuid4().hex
    launch.mkdir(parents=True)
    state = None
    try:
        state, state_path, method, startup_log = _serve(node, model, port, launch)
        report = _verify(state, model, delivered["glb_sha256"], port, method,
                         draft=delivery_context["finished"] is False)
        # A delivery rewritten during startup cannot acquire an owner-review binding.
        checked = delivery.load_delivery(root, delivered["delivery_id"])
        if (checked["glb_sha256"] != delivered["glb_sha256"]
                or checked["manifest_sha256"] != delivered["manifest_sha256"]):
            raise ReviewError("delivery changed during AR startup")
        result = {"schema": REVIEW_SCHEMA, "status": "ready", "created_at": procs.utcnow(),
                  "delivery_id": delivered["delivery_id"], "glb_sha256": delivered["glb_sha256"],
                  "manifest_sha256": delivered["manifest_sha256"], "model": str(model), "url": state["url"],
                  "appearance_policy": AR_APPEARANCE_POLICY, "lens_env_intensity": LENS_ENV_INTENSITY,
                  "server": {"pid": state["pid"], "instance": state["instance"], "port": state["port"],
                             "detached": True, "method": method, "state_path": str(state_path),
                             "report_url": state["report_url"], "log": state.get("log"),
                             "startup_log": str(startup_log), "started_at": state.get("started_at")},
                  "server_status": report.get("status"), "warnings": report.get("warnings", []),
                  **delivery_context}
        procs.write_json_atomic(launch / "review.json", result)
        procs.write_json_atomic(root / "review" / "current.json", result)
        return result
    except BaseException:
        if isinstance(state, dict):
            _stop_owned(state)
        raise


def reviewed_delivery(root):
    """Last verified owner-review binding, valid after its server closes; refuse changed delivery evidence."""
    root = Path(root).resolve()
    record = _read(root / "review" / "current.json")
    if (not isinstance(record, dict) or record.get("schema") != REVIEW_SCHEMA or record.get("status") != "ready"
            or not isinstance(record.get("delivery_id"), str)
            or record.get("appearance_policy") != AR_APPEARANCE_POLICY
            or record.get("lens_env_intensity") != LENS_ENV_INTENSITY):
        raise ReviewError("invalid saved owner-review binding")
    checked = delivery.load_delivery(root, record["delivery_id"])
    if (checked["glb_sha256"] != record.get("glb_sha256")
            or checked["manifest_sha256"] != record.get("manifest_sha256")
            or Path(record.get("model", "")).resolve() != Path(checked["model"]).resolve()):
        raise ReviewError("saved owner review refers to changed delivery evidence")
    context = _delivery_context(checked)
    if any(key in record and record[key] != value for key, value in context.items()):
        raise ReviewError("saved owner review changes the delivery's finish or check results")
    return {**record, **context}
