"""Detached, dollar-zero application of already sealed owner picks."""
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import math
from pathlib import Path

from glasses3d.agent import procs
from .contracts import Config, now_iso, opaque
from .registry import Registry

STARTUP_GRACE_SECONDS = 30


def _worker_identity():
    import psutil
    process = psutil.Process()
    return {"pid": process.pid, "create_time": process.create_time(), "exe": process.exe()}


def _identity_alive(identity):
    if not isinstance(identity, dict) or type(identity.get("pid")) is not int:
        return False
    created = identity.get("create_time")
    if isinstance(created, bool) or not isinstance(created, (int, float)) or not math.isfinite(created):
        return False
    return procs.matches_record(identity)


def _folder(config, key):
    return config.state_root / "tasks" / opaque(key)


def _launch_identities(config, key, task, status):
    folder = _folder(config, key)
    marker = str(procs.spec_path_for(folder / "runner-status.json"))
    launch = task.get("launch") or procs.read_json(folder / "launch.json") or {}
    result = [{"pid": launch.get("pid"), "create_time": launch.get("create_time"),
               "exe": procs.keeper_python(), "argv_marker": marker}]
    if status:
        # The status is written only by the keeper for this task's confined spec.
        expected = [str(config.python), "-B", "-m", "glasses3d.dev_ui", "task", str(folder / "task.json")]
        if status.get("argv") == expected:
            result.extend([
                {"pid": status.get("keeper_pid"), "create_time": status.get("keeper_create_time"),
                 "exe": procs.keeper_python(), "argv_marker": marker},
                {"pid": status.get("child_pid"), "create_time": status.get("child_create_time"),
                 "exe": str(config.python), "argv_marker": "glasses3d.dev_ui"},
            ])
    return result


def _launch_age(task):
    stamp = task.get("launched_at") or task.get("launch_requested_at") or task.get("created_utc")
    try:
        return max(0, (datetime.now(timezone.utc) - datetime.fromisoformat(stamp)).total_seconds())
    except (TypeError, ValueError):
        return STARTUP_GRACE_SECONDS


def reconcile_task(service, key):
    """Recover the UI state of a dead worker without applying or launching anything.

    Actual core failures stay terminal. Only interrupted launches/workers become
    retryable; the same task and deterministic destination are retained.
    """
    with service.registry.lock:
        task = service.get("tasks", key)
        if task["phase"] in ("done", "failed"):
            return task
        status = procs.read_json(_folder(service.config, key) / "runner-status.json") or {}
        if (_identity_alive(task.get("worker_identity"))
                or any(_identity_alive(row) for row in _launch_identities(service.config, key, task, status))):
            return task
        launched = task.get("launched") or task.get("launch_requested_at") or task.get("launch") or status
        if task["phase"] == "queued" and not launched:
            return task
        # A keeper can exist just before its status/identity reaches disk. Do not
        # duplicate a newly launched worker during that small publication window.
        if not status.get("finished_utc") and status.get("exit_code") is None and _launch_age(task) < STARTUP_GRACE_SECONDS:
            return task
        code = status.get("exit_code")
        detail = f" (worker exit {code})" if type(code) is int else ""
        retry = ("Save as new version" if task["kind"] in ("lens_save", "appearance_save") else
                 "Check in AR" if task["kind"] in ("lens_preview", "appearance_preview") else "Apply my picks")
        task.update(phase="failed", recoverable=True, launched=False, ended_at=now_iso(),
                    progress="Interrupted; retry " + retry, error="The local task was interrupted" + detail)
        service.registry.put("tasks", task, key)
        return task


def launch_task(config, task_key):
    registry = Registry(config.state_root)
    with registry.lock:
        task = registry.get("tasks", opaque(task_key))
        if (task["phase"] != "queued" or task.get("launched")
                or (task.get("launch_requested_at") and not task.get("recoverable"))):
            raise ValueError("The local apply task is already launched or terminal")
        folder = _folder(config, task_key)
        folder.mkdir(parents=True, exist_ok=True)
        file = folder / "task.json"
        settings = {k: str(v) if isinstance(v, Path) else v for k, v in asdict(config).items()}
        procs.write_json_atomic(file, {"config": settings, "task_key": task_key})
        task.update(launch_requested_at=now_iso(), recoverable=False)
        # Remove dead claims/receipts before the same task's next launch. Leave its
        # deterministic apply output intact for publication recovery.
        task.pop("worker_identity", None)
        task.pop("launch", None)
        task.pop("launched_at", None)
        registry.put("tasks", task, task_key)
        try:
            child = procs.spawn_detached([str(config.python), "-B", "-m", "glasses3d.dev_ui", "task", str(file)],
                cwd=config.repo_root, log_path=folder / "task.log", status_path=folder / "runner-status.json",
                env={"OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"})
        except Exception:
            # The keeper may already exist: reconciliation observes its status
            # or waits out the short launch publication grace before retrying.
            raise
        receipt = child.record()
        procs.write_json_atomic(folder / "launch.json", receipt)
        task.update(launch=receipt, launched=True, launched_at=now_iso())
        registry.put("tasks", task, task_key)
        return child


def _applied_draft(service, task, delivery_key):
    """One durable draft even if the worker dies between draft and task writes."""
    key = "apply_" + hashlib.sha256(task["task_key"].encode()).hexdigest()[:32]
    delivery = service.delivery(delivery_key)
    from .appearance_flow import native_draft_source
    delivery_key, appearance_source = native_draft_source(service, delivery_key, delivery)
    delivery = service.delivery(delivery_key)
    source = {"delivery_key": delivery_key, "glb_sha256": delivery["glb_sha256"]}
    with service.registry.lock:
        try:
            draft = service.get("drafts", key)
        except KeyError:
            service.registry.put("drafts", {"source": source, "items": [], "frozen": False,
                "next_number": 1, "created_utc": now_iso(), "applied_task_key": task["task_key"],
                **({"appearance_source": appearance_source} if appearance_source else {})}, key)
        else:
            if (draft.get("source") != source or draft.get("applied_task_key") != task["task_key"]
                    or draft.get("appearance_source") != appearance_source):
                raise ValueError("The applied draft is bound to a different task or delivery")
        return key


def execute_task(service, key):
    registry = service.registry
    with registry.lock:
        task = reconcile_task(service, key)
        if task["phase"] == "done" or (task["phase"] == "failed" and not task.get("recoverable")):
            return task
        if task["phase"] == "running":
            return task
        # Two detached commands may arrive after an interrupted launcher reply;
        # only one can claim this task while holding the cross-process lock.
        identity = _worker_identity()
        if _identity_alive(task.get("worker_identity")):
            return task
        progress = ("Checking and saving your look" if task["kind"] in ("lens_save", "appearance_save") else
                    "Preparing a temporary AR preview" if task["kind"] in ("lens_preview", "appearance_preview") else
                    "Reapplying your saved look" if task["kind"] == "appearance_reapply" else
                    "Applying the selected sealed options")
        task.update(phase="running", started_at=now_iso(), progress=progress,
                    error=None, recoverable=False, worker_identity=identity)
        task.pop("ended_at", None)
        registry.put("tasks", task, key)
    try:
        body = task["body"]
        if task["kind"] in ("appearance_save", "appearance_preview", "appearance_reapply"):
            from .appearance_api import execute_appearance_task
            from .appearance_flow import reapply_owner_look
            result = (reapply_owner_look(service, task["appearance_source"], task["native_delivery_key"],
                        service.config.state_root / "reapplied" / key)
                      if task["kind"] == "appearance_reapply" else execute_appearance_task(service, task))
            task.update(result=result, phase="done", progress="AR preview ready" if
                        task["kind"] == "appearance_preview" else "Saved", ended_at=now_iso())
            registry.put("tasks", task, key)
            return task
        if task["kind"] in ("lens_save", "lens_preview"):
            from .lens_api import execute_lens_task
            task.update(result=execute_lens_task(service, task), phase="done",
                        progress="Saved" if task["kind"] == "lens_save" else "AR preview ready")
            task["ended_at"] = now_iso()
            registry.put("tasks", task, key)
            return task
        destination = service.config.state_root / "applied" / key
        # Deterministic destination makes an interrupted publication discoverable on retry.
        pointer = destination / "deliveries/current.json"
        root = destination if pointer.is_file() else service.core.apply_choices(
            Path(task["round_root"]), body["understanding_sha256"], body["choices"], body["skipped"],
            task["confirmation_text"], destination=destination)
        root = Path(root)
        if root.name == "model.glb":
            raise ValueError("apply_choices must return the new job/round root, not a model path")
        # Accept façade delivery-directory returns as well as a job/round root.
        delivery_id = None
        if root.parent.name == "deliveries":
            delivery_id, root = root.name, root.parent.parent
        understanding = service.understanding(task["edit_key"])
        amounts = {(item["item_id"], option["option_id"]): option.get("amount_text", "")
                   for item in understanding["items"] for option in item["options"]}
        applied = [{**choice, "amount_text": amounts[(choice["item_id"], choice["option_id"])]} for choice in body["choices"]]
        delivery_key = service.register_delivery(root, delivery_id, origin="applied_picks", job_id=task["job_id"],
                                                  applied=applied, notes=body["skipped"])
        result = {"delivery_key": delivery_key}
        if task.get("appearance_source"):
            from .appearance_flow import reapply_owner_look, record_reapply_failure
            try:
                result = reapply_owner_look(service, task["appearance_source"], delivery_key,
                                            service.config.state_root / "reapplied" / key)
            except Exception as error:
                # The selected native operations already passed their checks and
                # published. Preserve access to that success if the separate
                # owner appearance publication fails; never certify its look.
                message = record_reapply_failure(service, delivery_key, error, key)
                result.update(native_delivery_key=delivery_key, appearance_error=message,
                              look_delivery_key=task["appearance_source"]["delivery_key"])
        # An appearance delivery's next draft must resolve its native source and
        # retain the saved look just like any direct Ask Astra action.
        draft_id = _applied_draft(service, task, result["delivery_key"])
        task.update(phase="done", progress="Picks applied; saved look could not be reapplied"
                    if result.get("appearance_error") else "Applied", result=result, draft_id=draft_id)
    except Exception as error:
        task.update(phase="failed", recoverable=False, progress="Application failed", error=str(error))
    task["ended_at"] = now_iso()
    registry.put("tasks", task, key)
    return task


def run_task_file(file):
    from .api import Service
    from .core_adapter import CoreAdapter
    from .launcher import Launcher
    from .contracts import json_object
    record = json_object(Path(file).read_text(encoding="utf-8"))
    config = Config(**record["config"])
    service = Service(config, Launcher(config), CoreAdapter())
    task = service.get("tasks", record["task_key"])
    if task.get("offline_test_mode"):
        raise ValueError("Offline tasks require a Python-injected proof worker; production task transport is disabled.")
    return 0 if execute_task(service, record["task_key"])["phase"] == "done" else 1
