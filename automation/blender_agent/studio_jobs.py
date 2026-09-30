"""Local studio job storage and orchestration of the existing Astra/Blender CLI.

This is a trusted local application, not a sandbox for Blender Python. HTTP callers
select job IDs and validated uploads, never filesystem paths or executable commands.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import threading
import uuid

from PIL import Image

from .studio_description import credentials

AUTOMATION = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = AUTOMATION / "data/blender_agent/studio"
DEFAULT_BLENDER = Path(r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe")
ACTIVE = {"starting", "running", "stopping", "describing"}
MIMES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
MAX_IMAGE_BYTES = 12 * 1024 * 1024


class JobError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def atomic_json(path: Path, value):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def bounded_path(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    if candidate == root.resolve() or not candidate.is_relative_to(root.resolve()):
        raise JobError("Path leaves its job directory", 403)
    return candidate


def budget_value(value):
    try:
        amount = Decimal(str(value))
        if isinstance(value, bool) or not amount.is_finite() or amount < 1 or amount > 1000:
            raise ValueError
        if amount.as_tuple().exponent < -2:
            raise ValueError
        return float(amount)
    except (ValueError, InvalidOperation):
        raise JobError("Budget must be between $1 and $1000, with at most two decimal places") from None


def text_field(value, limit, name):
    if not isinstance(value, str) or len(value) > limit:
        raise JobError(f"{name} must be text, at most {limit} characters")
    return value.strip()


def validated_specs(value):
    if not isinstance(value, dict) or len(value) > 30:
        raise JobError("Specifications must be a small JSON object")
    if len(json.dumps(value, allow_nan=False)) > 16000:
        raise JobError("Specifications are too large")
    for k, v in value.items():
        if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_ -]{0,59}", k) or not isinstance(v, (str, int, float, bool, dict, list, type(None))):
            raise JobError("Invalid specification")
    return value


def upload_image(item, index):
    if not isinstance(item, dict):
        raise JobError("Each image must be an object")
    encoded = item.get("data_url", "")
    if not isinstance(encoded, str):
        raise JobError("Image data_url must be text")
    match = re.fullmatch(r"data:(image/(?:png|jpeg|webp));base64,([A-Za-z0-9+/=\r\n]+)", encoded)
    if not match or len(encoded) > MAX_IMAGE_BYTES * 1.4:
        raise JobError("Upload a PNG, JPEG or WebP image under 12 MiB")
    try:
        data = base64.b64decode(match[2], validate=True)
        if len(data) > MAX_IMAGE_BYTES:
            raise ValueError
        with Image.open(BytesIO(data)) as image:
            if image.width * image.height > 40_000_000 or image.format not in ("PNG", "JPEG", "WEBP"):
                raise ValueError
            actual = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}[image.format]
            if actual != match[1]:
                raise ValueError
            image.verify()
    except (ValueError, OSError, Image.DecompressionBombError):
        raise JobError("Invalid or oversized image") from None
    return data, {"name": f"reference-{index:02d}" + MIMES[match[1]],
                  "original_name": text_field(item.get("name", f"Reference {index}"), 200, "Image name"),
                  "mime_type": match[1], "sha256": hashlib.sha256(data).hexdigest(),
                  "view": text_field(item.get("view", "unknown"), 60, "View"),
                  "provenance": text_field(item.get("provenance", "original"), 120, "Provenance")}


def clean_process_env(env_file=None, *, include_openai=False):
    """Runner needs only OpenAI credentials; Blender launcher strips these again."""
    env = {k: v for k, v in os.environ.items() if not any(x in k.upper() for x in
           ("API_KEY", "APIKEY", "SECRET", "TOKEN", "PASSWORD", "CREDENTIAL", "ACCESS_KEY", "PRIVATE_KEY"))}
    key = credentials(env_file).get("OPENAI_API_KEY") if include_openai else None
    if key:
        env["OPENAI_API_KEY"] = key
    env.update(PYTHONUNBUFFERED="1", BLENDER_MCP_DISABLE_TELEMETRY="1")
    return env


def process_options():
    if os.name != "nt":
        return {}
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = subprocess.SW_HIDE
    return {"startupinfo": startup, "creationflags": subprocess.CREATE_NO_WINDOW}


def process_identity(pid):
    """Read-only process identity. Never use os.kill(pid, 0) on Windows."""
    try:
        import psutil
        return psutil.Process(pid).create_time()
    except ImportError:
        return None
    except psutil.NoSuchProcess:
        return False
    except psutil.AccessDenied:
        return None


class JobStore:
    def __init__(self, root=DEFAULT_ROOT, *, env_file=None, blender=DEFAULT_BLENDER,
                 python=None, ar_origin="http://127.0.0.1:8240"):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.env_file = Path(env_file).resolve() if env_file else None
        self.blender = Path(blender)
        local_python = AUTOMATION / "data/blender_agent/venv/Scripts/python.exe"
        self.python = str(python or (local_python if local_python.is_file() else sys.executable))
        self.ar_origin = ar_origin.rstrip("/")
        self.lock = threading.RLock()
        self.workers = {}

    def directory(self, job_id):
        if not isinstance(job_id, str) or not re.fullmatch(r"[0-9a-f]{32}", job_id):
            raise JobError("Unknown job", 404)
        return bounded_path(self.root, "jobs/" + job_id)

    def metadata(self, job_id):
        value = read_json(self.directory(job_id) / "job.json")
        if not value:
            raise JobError("Unknown job", 404)
        result = value.get("result")
        if result and "source_match_status" not in result:
            # Early studio drafts assumed independently saved scene.blend matched the export.
            changed = result.get("scene_matches_revision") is False and result.get("revision", "original") != "original"
            result.update(scene_matches_revision=False if changed else None,
                          glb_materials_edited=changed,
                          source_match_status="material_changes" if changed else "unverified" if result.get("scene_url") else "unavailable")
        return value

    def save(self, job):
        job["updated_at"] = now()
        atomic_json(self.directory(job["id"]) / "job.json", job)

    def create(self, payload):
        name = text_field(payload.get("name", "Untitled eyewear"), 120, "Name") or "Untitled eyewear"
        description = text_field(payload.get("description", ""), 24000, "Description")
        specs = validated_specs(payload.get("specs", {}))
        budget = budget_value(payload.get("budget_usd", 20))
        images = payload.get("images", [])
        if not isinstance(images, list) or len(images) > 12:
            raise JobError("Upload at most 12 reference images")
        uploads = [upload_image(item, index + 1) for index, item in enumerate(images)]
        if sum(len(data) for data, _ in uploads) > 40 * 1024 * 1024:
            raise JobError("Combined images exceed 40 MiB")
        job = {"id": uuid.uuid4().hex, "name": name, "description": description, "specs": specs,
               "budget_usd": budget, "status": "draft", "created_at": now(), "images": [],
               "effort": "max", "error": None, "started_once": False, "read_only": False,
               "result": None, "progress": {"message": "Ready to start", "spent_usd": 0, "reserved_usd": 0}}
        directory = self.directory(job["id"])
        (directory / "images").mkdir(parents=True)
        for data, record in uploads:
            (directory / "images" / record["name"]).write_bytes(data)
            job["images"].append(record)
        self.save(job)
        return self.get(job["id"])

    def update(self, job_id, payload):
        with self.lock:
            job = self.metadata(job_id)
            if job["status"] != "draft" or job["started_once"]:
                raise JobError("The brief and budget are frozen after the first start", 409)
            if set(payload) - {"name", "description", "specs", "budget_usd", "images"}:
                raise JobError("Unsupported draft field")
            for key, value in payload.items():
                if key == "images":
                    self._replace_images(job, value)
                    continue
                job[key] = (budget_value(value) if key == "budget_usd" else validated_specs(value) if key == "specs"
                            else text_field(value, 120 if key == "name" else 24000, key))
            self.save(job)
        return self.get(job_id)

    def _replace_images(self, job, items):
        if not isinstance(items, list) or len(items) > 12:
            raise JobError("Upload at most 12 reference images")
        previous = {p["name"]: p for p in job["images"]}
        directory = self.directory(job["id"])
        prepared = []
        for index, item in enumerate(items, 1):
            if isinstance(item, dict) and "data_url" not in item and item.get("name") in previous:
                old = previous[item["name"]]
                item = {**old, **item, "name": old["original_name"], "data_url": "data:" + old["mime_type"] + ";base64,"
                        + base64.b64encode((directory / "images" / old["name"]).read_bytes()).decode("ascii")}
            data, record = upload_image(item, index)
            record["name"] = uuid.uuid4().hex[:12] + Path(record["name"]).suffix
            prepared.append((data, record))
        if sum(len(data) for data, _ in prepared) > 40 * 1024 * 1024:
            raise JobError("Combined images exceed 40 MiB")
        # Old images remain private until metadata atomically selects the replacement set.
        for data, record in prepared:
            (directory / "images" / record["name"]).write_bytes(data)
        job["images"] = [record for _, record in prepared]

    def image_paths(self, job):
        directory = self.directory(job["id"])
        return [{**item, "path": str(bounded_path(directory, "images/" + item["name"]))} for item in job["images"]]

    def list(self):
        return [self.get(p.parent.name) for p in sorted((self.root / "jobs").glob("*/job.json"),
                                                       key=lambda p: p.stat().st_mtime, reverse=True)]

    def _costs(self, agent):
        settled = reserved = 0
        for ledger in (agent / "runs").glob("*/budget.json"):
            data = read_json(ledger)
            if not isinstance(data, dict) or not isinstance(data.get("reservations"), list):
                return {"accounting_available": False, "spent_usd": None, "reserved_usd": None}
            for row in data["reservations"]:
                if not isinstance(row, dict) or any(type(row.get(k, 0)) is not int or row.get(k, 0) < 0
                                                    for k in ("settled_micro_usd", "reserved_micro_usd")):
                    return {"accounting_available": False, "spent_usd": None, "reserved_usd": None}
                if "settled_micro_usd" in row:
                    settled += row["settled_micro_usd"]
                else:
                    reserved += row.get("reserved_micro_usd", 0)
        return {"accounting_available": True, "spent_usd": settled / 1_000_000, "reserved_usd": reserved / 1_000_000}

    def get(self, job_id):
        with self.lock:
            job = self.metadata(job_id)
            directory = self.directory(job_id)
            worker = self.workers.get(job_id)
            if job["status"] in {"starting", "running", "stopping"} and not (worker and worker.is_alive()):
                marker = read_json(directory / "execution.json") or {}
                # After a server restart, the subprocess may still be running. Never launch a duplicate.
                results = [p for p in (directory / "agent/runs").glob("*/result.json")
                           if p.parent.name not in marker.get("prior_runs", [])]
                if marker and results:
                    result = read_json(sorted(results)[-1])
                    if result:
                        self.finish(job_id, result)
                        job = self.metadata(job_id)
                elif marker.get("pid"):
                    identity = process_identity(marker["pid"])
                    if identity and (marker.get("process_created") is None or identity == marker["process_created"]):
                        job["progress"]["message"] = "Existing runner is still active; waiting for recorded result"
                    elif identity is False or identity is not None:
                        job.update(status="interrupted", error="Runner exited without a final receipt; recovery needs host review")
                        job.pop("resume_checkpoint", None)
                        self.save(job)
                    else:
                        job["progress"]["message"] = "Previous runner state needs host review; duplicate starts are blocked"
                else:
                    job["progress"]["message"] = "Startup state needs host review; duplicate starts are blocked"
            if (job["status"] not in {"starting", "running", "stopping"}
                    and not (worker and worker.is_alive())
                    and not job.get("blender_cleanup", {}).get("closed")):
                marker = read_json(directory / "execution.json") or {}
                identity = process_identity(marker["pid"]) if type(marker.get("pid")) is int else None
                gone = identity is False or (identity is not None and marker.get("process_created") is not None
                                              and identity != marker["process_created"])
                session_name = marker.get("blender_session", "")
                if gone and re.fullmatch(r"blender-[0-9a-f]+", session_name):
                    try:
                        self._checkpoint(job_id)
                    except JobError:
                        job["blender_cleanup"] = {"closed": False, "recovery_needed": True,
                                                   "message": "Blender retained because a final checkpoint was not verified"}
                    else:
                        job["blender_cleanup"] = self._cleanup_blender(
                            bounded_path(directory, session_name), marker.get("blender_process_created"))
                    self.save(job)
            if not job["read_only"]:
                job["progress"].update(self._costs(directory / "agent"))
            runs = sorted((directory / "agent/runs").glob("*/events.jsonl"))
            if runs and job["status"] in ACTIVE:
                try:
                    with runs[-1].open("rb") as stream:
                        stream.seek(max(0, runs[-1].stat().st_size - 32768))
                        lines = stream.read().splitlines()
                    event = json.loads(lines[-1])
                    # Never forward arbitrary model text, tool code, transport or provider error bodies.
                    kind = event.get("event", "working")
                    job["progress"].update(message={"model_thinking": "Astra is inspecting and reasoning",
                                                   "tool_started": "Astra is editing or inspecting Blender",
                                                   "tool_finished": "Blender interaction finished",
                                                   "stop_checkpoint": "Saved recovery checkpoint"}.get(kind, "Astra session is working"),
                                           response_index=event.get("interaction", 0))
                except (OSError, ValueError, IndexError):
                    pass
            for image in job["images"]:
                image["url"] = f"/api/jobs/{job_id}/images/{image['name']}"
            # Internal artifact paths never appear in API responses.
            job.pop("artifacts", None)
            job.pop("resume_checkpoint", None)
            job.pop("description_request", None)
            return job

    def _prompt(self, job):
        photos = "\n".join(f"{i+1}. {p['original_name']}: {p['view']}; provenance {p['provenance']}"
                           for i, p in enumerate(job["images"]))
        return ("Build this eyewear product from zero in the current empty scene using the attached references. "
                "Do not import donor models. You have a persistent editable Blender scene and freedom to author and inspect it.\n\n"
                + job["description"] + "\n\nUser specifications (dimensions only verified when explicitly stated):\n"
                + json.dumps(job["specs"], ensure_ascii=False, indent=2) + "\n\nReferences:\n" + photos
                + "\n\nUse numeric millimetres, +Y up and +Z front, with bridge underside at (0,0,0). "
                "Set scene mdl_bridge_underside and partRole frame/temple/lens on delivery parts. Preserve full closed lens "
                "geometry and native exportable materials. Treat ambiguous reflected photo streaks as hypotheses, not "
                "automatic physical geometry. Inspect multiple angles and matched before/after evidence. Save scene.blend "
                "inside your output directory and use native preview_ar plus preview_portrait on the exact delivered GLB. "
                "Inspect actual exported materials if appearance differs. Keep the best evidenced state; report uncertainties "
                "and remaining limitations honestly. The total trial budget is a ceiling, not a spending target.\n")

    def _checkpoint(self, job_id):
        job = self.metadata(job_id)
        record = job.get("resume_checkpoint")
        if not record:
            raise JobError("No verified matching stop checkpoint is available", 409)
        path = bounded_path(self.directory(job_id), record["path"])
        if not path.is_file() or sha(path) != record["sha256"]:
            raise JobError("Recovery checkpoint changed or is missing", 409)
        return path

    def start(self, job_id, *, resume=False):
        with self.lock:
            job = self.metadata(job_id)
            if job["read_only"] or job["status"] in ACTIVE:
                raise JobError("This job cannot start in its current state", 409)
            if any(t.is_alive() for t in self.workers.values()):
                raise JobError("Another studio build is running; stop or finish it first", 409)
            if any(j["id"] != job_id and j["status"] in {"starting", "running", "stopping"} for j in self.list()):
                raise JobError("Another recorded studio runner is active; finish or review it first", 409)
            if not credentials(self.env_file).get("OPENAI_API_KEY"):
                raise JobError("Configure OPENAI_API_KEY on the studio server", 409)
            if not self.blender.is_file():
                raise JobError("Configured Blender executable is unavailable", 409)
            directory = self.directory(job_id)
            if resume:
                if (job.get("result") or {}).get("glb_materials_edited") is True:
                    raise JobError("Material revisions are GLB-only; the saved Blender checkpoint does not contain those edits", 409)
                if not (directory / "agent/session.sqlite").is_file() or not (directory / "agent/trial-budget.json").is_file():
                    raise JobError("No existing conversation and immutable trial budget to resume", 409)
                frozen = read_json(directory / "agent/trial-budget.json")
                if not isinstance(frozen, dict) or frozen.get("maximum_micro_usd") != int(Decimal(str(job["budget_usd"])) * 1_000_000):
                    raise JobError("Studio budget differs from the immutable trial ledger", 409)
                self._checkpoint(job_id)
            elif job["started_once"] or job["status"] != "draft":
                raise JobError("This job was already started; use Resume", 409)
            elif not job["images"] and not job["description"]:
                raise JobError("Provide reference photos or a product description")
            (directory / "stop.requested").unlink(missing_ok=True)
            if not resume:
                (directory / "brief.txt").write_text(self._prompt(job), encoding="utf-8")
            job.update(status="starting", started_once=True, error=None)
            job["progress"]["message"] = "Preparing a matching Blender scene" if resume else "Opening an empty Blender scene"
            self.save(job)
            worker = threading.Thread(target=self._execute, args=(job_id, resume), daemon=True)
            self.workers[job_id] = worker
            worker.start()
        return self.get(job_id)

    def stop(self, job_id):
        with self.lock:
            job = self.metadata(job_id)
            if job["status"] not in {"starting", "running", "stopping"}:
                raise JobError("This job is not running", 409)
            (self.directory(job_id) / "stop.requested").write_text(now(), encoding="utf-8")
            job["status"] = "stopping"
            job["progress"]["message"] = "Stop requested; current interaction will finish, then save a checkpoint"
            self.save(job)
        return self.get(job_id)

    def _execute(self, job_id, resume):
        directory = self.directory(job_id)
        agent = directory / "agent"
        session = None
        blender_identity = None
        runner_started = False
        checkpoint_saved = False
        try:
            job = self.metadata(job_id)
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            session = directory / ("blender-" + uuid.uuid4().hex[:12])
            launcher = [self.python, "-m", "blender_agent.session", "--output", str(session), "--port", str(port),
                        "--blender", str(self.blender)]
            launcher += ["--seed", str(self._checkpoint(job_id))] if resume else ["--empty"]
            env = clean_process_env(self.env_file, include_openai=True)
            # Launcher logs and run logs are host-only, never an HTTP route.
            with (directory / "launcher.log").open("ab") as log:
                result = subprocess.run(launcher, cwd=AUTOMATION, env=env, stdout=log, stderr=log,
                                        timeout=90, **process_options())
            receipt = read_json(session / "session.json")
            if receipt and receipt.get("pid"):
                blender_identity = process_identity(receipt["pid"])
            if result.returncode or not receipt or not receipt.get("ready"):
                raise RuntimeError("Blender startup failed; inspect this job's host launcher log")
            if resume:
                source = self._checkpoint(job_id)
                if Path(receipt.get("source", "")).resolve() != source.resolve():
                    raise RuntimeError("Blender did not restore the matching checkpoint")
            with self.lock:
                job = self.metadata(job_id)
                job["status"] = "stopping" if (directory / "stop.requested").exists() else "running"
                job["progress"]["message"] = "Astra session started"
                self.save(job)
            command = [self.python, "-m", "blender_agent", "run", "--output", str(agent),
                       "--prompt-file", str(directory / "brief.txt"), "--mcp-port", str(port),
                       "--reasoning-effort", "max", "--max-turns", "120", "--max-output-tokens", "8192",
                       "--max-usd", str(job["budget_usd"]), "--stop-file", str(directory / "stop.requested"), "--run-paid"]
            for image in self.image_paths(job):
                command.extend(["--photo", image["path"]])
            if resume:
                command.append("--resume")
            prior_runs = set((agent / "runs").glob("*"))
            atomic_json(directory / "execution.json", {"prior_runs": [p.name for p in prior_runs], "started_at": now()})
            with (directory / "agent.log").open("ab") as log:
                process = subprocess.Popen(command, cwd=AUTOMATION, env=env, stdout=log, stderr=log, **process_options())
                runner_started = True
                atomic_json(directory / "execution.json", {"prior_runs": [p.name for p in prior_runs], "pid": process.pid,
                                                           "process_created": process_identity(process.pid),
                                                           "started_at": now(), "blender_session": session.name,
                                                           "blender_process_created": blender_identity})
                process.wait()
            new_runs = sorted(set((agent / "runs").glob("*")) - prior_runs)
            summary = read_json(new_runs[-1] / "result.json") if new_runs else None
            if not summary:
                raise RuntimeError("Runner stopped before recording its result; no automatic paid retry")
            checkpoint_saved = bool(summary.get("stop_checkpoint", {}).get("saved"))
            self.finish(job_id, summary)
        except Exception:
            with self.lock:
                job = self.metadata(job_id)
                job.update(status="failed", error="Local startup or runner failed. Inspect host job logs; no automatic paid retry.")
                if runner_started:
                    job.pop("resume_checkpoint", None)
                self.save(job)
        finally:
            if session is not None:
                if not runner_started or checkpoint_saved:
                    cleanup = self._cleanup_blender(session, blender_identity)
                else:
                    cleanup = {"closed": False, "recovery_needed": True,
                               "message": "Blender retained because the final checkpoint was not confirmed; save it before closing"}
                with self.lock:
                    job = self.metadata(job_id)
                    job["blender_cleanup"] = cleanup
                    self.save(job)

    def _cleanup_blender(self, session, expected_identity):
        """Close only this dedicated process, after a confirmed save or failed startup."""
        import psutil
        receipt = read_json(session / "session.json") or {}
        pid = receipt.get("pid")
        if type(pid) is not int:
            return {"closed": False, "message": "No owned Blender process was recorded"}
        try:
            process = psutil.Process(pid)
            created = process.create_time()
            if expected_identity is not None and created != expected_identity:
                return {"closed": False, "message": "Process identity changed; no process was closed"}
            command = process.cmdline()
            receipt_arg = command[command.index("--receipt") + 1] if "--receipt" in command else ""
            # Startup time and exact receipt argument also protect a timeout before identity was captured.
            if (Path(process.exe()).resolve() != self.blender.resolve()
                    or Path(receipt_arg).resolve() != (session / "initialization.json").resolve()
                    or created < session.stat().st_ctime - 3):
                return {"closed": False, "message": "Dedicated Blender ownership could not be verified"}
            process.terminate()
            try:
                process.wait(timeout=8)
            except psutil.TimeoutExpired:
                if process.create_time() == created:
                    process.kill()
                    process.wait(timeout=3)
            return {"closed": True, "message": "Dedicated Blender closed after checkpoint"}
        except psutil.NoSuchProcess:
            return {"closed": True, "message": "Dedicated Blender already exited"}
        except (psutil.Error, OSError, ValueError, IndexError):
            return {"closed": False, "message": "Owned Blender cleanup needs host review"}

    def finish(self, job_id, summary):
        with self.lock:
            directory = self.directory(job_id)
            job = self.metadata(job_id)
            status = summary.get("status", "failed")
            job["status"] = "stopped" if (directory / "stop.requested").exists() else status
            job["error"] = None if status in {"completed", "stopped", "interrupted", "turn_limit", "budget_limit"} else "Agent could not finish; see host logs. Paid requests are never retried automatically."
            for provider_code in ("credit_balance_exhausted", "organization_spend_limit_exceeded", "insufficient_quota", "rate_limit_exceeded"):
                if provider_code in str(summary.get("error", "")):
                    job["error"] = f"Provider returned {provider_code}; no automatic paid retry"
                    break
            job["final_output"] = str(summary.get("final_output", ""))[:24000]
            job["progress"]["message"] = {"completed": "Astra finished; review the model and stated limitations",
                                           "budget_limit": "Budget guard stopped the run; useful work may be available",
                                           "stopped": "Stopped with recovery evidence"}.get(job["status"], "Run ended; review available evidence")
            stop = summary.get("stop_checkpoint", {})
            job.pop("resume_checkpoint", None)
            if stop.get("saved"):
                checkpoint = Path(stop.get("path", "")).resolve()
                if checkpoint.is_relative_to(directory) and checkpoint.is_file():
                    job["resume_checkpoint"] = {"path": str(checkpoint.relative_to(directory)), "sha256": sha(checkpoint)}
            agent = directory / "agent"
            previews = sorted((agent / "ar").glob("*/preview-result.json"), key=lambda p: p.stat().st_mtime)
            if previews:
                preview = read_json(previews[-1]) or {}
                model = previews[-1].parent / "model.glb"
                if model.is_file() and preview.get("sha256") == sha(model):
                    scene = agent / "scene.blend"
                    if not scene.is_file() and job.get("resume_checkpoint"):
                        scene = bounded_path(directory, job["resume_checkpoint"]["path"])
                    job["artifacts"] = {"model": str(model.relative_to(directory)), "original_model": str(model.relative_to(directory)),
                                        "scene": str(scene.relative_to(directory)) if scene.is_file() else None}
                    job["result"] = self.result_urls(job_id, sha(model), scene.is_file())
                    job["result"].update(validation=(preview.get("ar") or {}).get("validation"),
                                         evidence_scope="Latest recorded native export; structural AR checks do not certify product accuracy",
                                         candidate_status="completed_run_candidate" if status == "completed" else "interrupted_run_candidate")
            self.save(job)

    def result_urls(self, job_id, model_sha, has_scene=True):
        return {"model_url": f"/api/jobs/{job_id}/model.glb", "model_sha256": model_sha,
                "scene_url": f"/api/jobs/{job_id}/scene.blend" if has_scene else None,
                "revision": "original", "scene_matches_revision": None,
                "source_match_status": "unverified" if has_scene else "unavailable",
                "glb_materials_edited": False, "viewer": {"lens_reflection": 1},
                "viewer_url": f"/?job={job_id}", "ar_url": None}

    def artifact(self, job_id, name, revision=None):
        job = self.metadata(job_id)
        key = {"model.glb": "model", "scene.blend": "scene"}.get(name)
        rel = job.get("artifacts", {}).get(key) if key else None
        if name == "model.glb" and revision is not None:
            if revision == "original":
                rel = job.get("artifacts", {}).get("original_model")
            elif re.fullmatch(r"[0-9a-f]{16}", revision):
                rel = f"revisions/{revision}/model.glb"
            else:
                raise JobError("Unknown revision", 404)
        if not rel:
            raise JobError("Artifact is not available", 404)
        path = bounded_path(self.directory(job_id), rel)
        if not path.is_file():
            raise JobError("Artifact is not available", 404)
        return path

    def import_completed(self, trial: Path, *, name=None):
        """CLI-only read-only import. Copy delivery evidence; never resume another trial."""
        trial = Path(trial).resolve()
        agent = trial / "agent" if (trial / "agent").is_dir() else trial
        summaries = sorted((agent / "runs").glob("*/result.json"))
        latest = read_json(summaries[-1]) if summaries else None
        if not latest or latest.get("status") != "completed":
            raise JobError("Import requires the latest trial invocation to be completed")
        previews = sorted((agent / "ar").glob("*/preview-result.json"), key=lambda p: p.stat().st_mtime)
        if not previews or not (agent / "scene.blend").is_file():
            raise JobError("Import requires an exported model and scene.blend")
        model = previews[-1].parent / "model.glb"
        if sha(model) != (read_json(previews[-1]) or {}).get("sha256"):
            raise JobError("Import export hash mismatch")
        job = self.create({"name": name or trial.name, "description": latest.get("final_output", ""), "images": []})
        job = self.metadata(job["id"])
        directory = self.directory(job["id"])
        (directory / "original").mkdir()
        shutil.copy2(model, directory / "original/model.glb")
        shutil.copy2(agent / "scene.blend", directory / "original/scene.blend")
        job.update(status="imported", read_only=True, started_once=True,
                   artifacts={"model": "original/model.glb", "original_model": "original/model.glb", "scene": "original/scene.blend"})
        frozen = read_json(agent / "trial-budget.json") or {}
        maximum = frozen.get("maximum_micro_usd")
        job["budget_usd"] = maximum / 1_000_000 if type(maximum) is int and maximum > 0 else None
        for index, path in enumerate(sorted((trial / "references").glob("*")), 1):
            if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"} or not path.is_file():
                continue
            mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}[path.suffix.lower()]
            data, image = upload_image({"name": path.name, "data_url": "data:" + mime + ";base64," + base64.b64encode(path.read_bytes()).decode("ascii"),
                                       "view": path.stem if path.stem in {"front", "angled", "top"} else "unknown",
                                       "provenance": "supplementary/unverified" if "ChatGPT" in path.name else "original/unverified"}, index)
            (directory / "images" / image["name"]).write_bytes(data)
            job["images"].append(image)
        job["progress"].update(self._costs(agent), message="Imported completed model; no paid inference")
        job["result"] = self.result_urls(job["id"], sha(model))
        job["final_output"] = str(latest.get("final_output", ""))[:24000]
        job["result"].update(validation=((read_json(previews[-1]) or {}).get("ar") or {}).get("validation"),
                             evidence_scope="Latest recorded export after a completed invocation; saved source and model copied unchanged, source/export match unverified",
                             candidate_status="imported_completed_candidate")
        self.save(job)
        return self.get(job["id"])
