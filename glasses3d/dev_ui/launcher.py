"""The dev UI's small adapter over prepared approvals and the detached job CLI.

This module does not execute a model loop in the web server. The caller durably
assigns a run ID before calling prepare/approve/launch, so a retried click keeps
the original request and single-use approval.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import replace
from decimal import Decimal, DecimalException, InvalidOperation, ROUND_FLOOR
from pathlib import Path
from types import SimpleNamespace

from glasses3d.agent import procs


class LauncherError(ValueError):
    code = "launch_failed"
    status_code = 409


class PaidRunBusyError(LauncherError):
    code = "paid_run_busy"

    def __init__(self):
        super().__init__("Another paid run is in progress")


def cutoff_text(value) -> str:
    """Positive finite dollars; retain the displayed amount in the owner's words."""
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ValueError("Max budget must be a positive finite decimal amount")
    if len(str(value)) > 100:
        raise ValueError("Max budget must be a positive finite decimal amount")
    try:
        number = Decimal(value)
    except (InvalidOperation, ValueError):
        raise ValueError("Max budget must be a positive finite decimal amount") from None
    if not number.is_finite() or number <= 0:
        raise ValueError("Max budget must be a positive finite decimal amount")
    try:
        micro = _micro(number)
    except (DecimalException, ValueError, OverflowError):
        raise ValueError("Max budget cannot be represented by the pipeline's money format") from None
    if micro <= 0:
        raise ValueError("Max budget must be at least $0.000001")
    return str(number)


def _micro(value):
    return int((Decimal(str(value)) * 1_000_000).to_integral_value(rounding=ROUND_FLOOR))


def _run_dir(root, run_id):
    if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", run_id):
        raise ValueError("Invalid prepared run ID")
    return Path(root).resolve() / "runs" / run_id


def has_launch_evidence(root, run_id) -> bool:
    """Evidence a worker started, including a crash before the UI saved its receipt."""
    run_dir = _run_dir(root, run_id)
    if any((run_dir / name).exists() for name in ("ui-launch.json", "runner-status.json", "budget.json", "result.json")):
        return True
    transport = run_dir / "transport"
    return transport.is_dir() and any(path.is_file() for path in transport.rglob("*"))


def check_paid_run_available() -> None:
    """Read the checkout-wide identity and briefly probe its startup guard.

    Taking this probe never writes or consumes an approval, or acquires a paid
    run. The actual CLI still atomically takes the paid lock before inference.
    """
    from glasses3d.agent import budget

    lock = budget.PaidRunLock(budget.PAID_RUN_LOCK_ROOT)
    stream = budget._open_locked(lock.guard_path)
    if stream is None:
        raise PaidRunBusyError()
    try:
        holder = lock.read()
        if holder and (holder.get("malformed") or budget.identity_alive(holder)):
            raise PaidRunBusyError()
    finally:
        budget._os_unlock(stream)


def _real_core():
    # Importing the server does not initialize the pipeline/model client.
    from glasses3d.agent import approvals, job_run
    return SimpleNamespace(prepare_run=job_run.prepare_run, settings_default=job_run.settings_default,
                           write_approval=approvals.write_approval, new_run_id=job_run.new_run_id)


def _core_call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except Exception as error:
        from glasses3d.agent.approvals import ApprovalRefused
        from glasses3d.agent.budget import BudgetGuardError
        from glasses3d.agent.loop import RunRefused
        if isinstance(error, (ApprovalRefused, BudgetGuardError, RunRefused)):
            raise LauncherError(str(error)) from error
        raise


class Launcher:
    offline_test_mode = False

    def __init__(self, config, *, core=None, command_runner=None, lock_probe=None):
        self.config = config
        self._core_override = core
        self._command_runner = command_runner or subprocess.run
        self._lock_probe = lock_probe or check_paid_run_available

    @property
    def core(self):
        if self._core_override is None:
            self._core_override = _real_core()
        return self._core_override

    def check_available(self):
        self._lock_probe()

    def prepare(self, root, cutoff_usd, *, run_id=None, kind="job") -> dict:
        if kind not in ("job", "understand", "full_edit_item"):
            raise LauncherError("Unknown paid send kind")
        effort = "max" if kind == "job" else "high"
        amount = cutoff_text(cutoff_usd)
        run_id = run_id or self.core.new_run_id()
        run_dir = _run_dir(root, run_id)
        document = procs.read_json(run_dir / "approval-request.json")
        if document is None:
            self.check_available()
            settings = replace(self.core.settings_default(), effort=effort, settled_only=True,
                               settled_stop_usd=amount, fit_output_to_budget=False)
            _core_call(self.core.prepare_run, Path(root).resolve(), run_id, settings=settings, cap=amount,
                       write_request=True)
            document = procs.read_json(run_dir / "approval-request.json")
        if not document:
            raise LauncherError("Run preparation did not write its approval request")
        settings = document.get("request", {}).get("settings", {})
        if (document.get("run_id") != run_id
                or _micro(document.get("requested_cap_usd", "0")) != _micro(amount)
                or settings.get("effort") != effort or settings.get("settled_only") is not True
                or _micro(settings.get("settled_stop_usd", "0")) != _micro(amount)
                or settings.get("fit_output_to_budget", False)):
            raise LauncherError("The saved run request differs from this paid send")
        return {"run_id": run_id, "cutoff_usd": amount,
                "estimate_usd": str(document.get("estimate", {}).get("expected_usd", "0"))}

    def approve(self, root, run_id, cutoff_usd, button_label, subject) -> dict:
        amount = cutoff_text(cutoff_usd)
        run_dir = _run_dir(root, run_id)
        words = f'Dev UI: owner pressed "{button_label}" with max budget ${amount} for {subject}'
        approval = procs.read_json(run_dir / "approval.json")
        if approval is not None:
            request_path = run_dir / "approval-request.json"
            digest = hashlib.sha256(request_path.read_bytes()).hexdigest()
            if (approval.get("run_id") != run_id or approval.get("owner_words") != words
                    or approval.get("authorized_by") != "owner"
                    or _micro(approval.get("max_usd", "0")) != _micro(amount)
                    or approval.get("approval_request_sha256") != digest):
                raise LauncherError("The saved approval differs from this paid send")
            return approval
        self.check_available()
        return _core_call(self.core.write_approval, run_dir, max_usd=amount, owner_words=words,
                          recorded_by="owner via Dev UI")

    def _command(self, args) -> dict:
        argv = [str(self.config.python), "-m", "glasses3d", "job", *map(str, args)]
        result = self._command_runner(argv, cwd=str(self.config.repo_root), capture_output=True,
                                      text=True, encoding="utf-8", stdin=subprocess.DEVNULL,
                                      **({"creationflags": procs.CREATE_NO_WINDOW} if os.name == "nt" else {}))
        try:
            payload = json.loads(result.stdout or "{}")
        except (ValueError, TypeError):
            payload = None
        if result.returncode or not isinstance(payload, dict):
            # CLI stderr contains a structured refusal, never expose arbitrary stdout/logs.
            try:
                refusal = json.loads(result.stderr or "{}")
            except (ValueError, TypeError):
                refusal = {}
            message = refusal.get("message") if isinstance(refusal, dict) else None
            raise LauncherError(message or "The job command could not start; inspect its runner log")
        return payload

    def launch(self, root, run_id) -> dict:
        run_dir = _run_dir(root, run_id)
        receipt_path = run_dir / "ui-launch.json"
        receipt = procs.read_json(receipt_path)
        if receipt and receipt.get("run_id") == run_id:
            return receipt
        status = procs.read_json(run_dir / "runner-status.json")
        if has_launch_evidence(root, run_id):
            receipt = {"status": "started", "run_id": run_id, "recovered": True}
            if status:
                receipt.update(pid=status.get("keeper_pid"), create_time=status.get("keeper_create_time"),
                               status_file=str(run_dir / "runner-status.json"))
            procs.write_json_atomic(receipt_path, receipt)
            return receipt
        self.check_available()
        env_file = getattr(self.config, "env_file", None)
        key_file = ["--env-file", env_file] if env_file is not None and Path(env_file).is_file() else []
        try:
            receipt = self._command(["run", Path(root).resolve(), "--run-id", run_id, *key_file])
        except (LauncherError, OSError):
            if has_launch_evidence(root, run_id):
                return self.launch(root, run_id)
            raise
        if receipt.get("status") != "started":
            raise LauncherError("The job command did not report a detached runner")
        receipt["run_id"] = run_id
        procs.write_json_atomic(receipt_path, receipt)
        return receipt

    def stop(self, root, run_id=None) -> dict:
        # Stop targets the round's root, not its parent/source job or a browser PID.
        return self._command(["stop", Path(root).resolve()])
