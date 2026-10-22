"""Cheap, read-only status snapshots for registered job and owner-edit runs.

``read_run_status`` accepts a server registry record with ``root``, ``run_id``,
``run_key``, ``kind``, ``job_id``, ``cutoff_usd``, and optional ``estimate_usd``,
``edit_key``, ``delivery_key``, ``stop_requested``, ``error`` and
``offline_test_mode``. It returns only the public RunDTO. ``find_run_delivery``
returns an internal publication descriptor for registration by the API layer.
That descriptor is deliberately not a verified asset: the delivery adapter must
verify/cache immutable files before exposing their bytes or a delivery key.

No process probes, core imports, ledger locks, directory scans or model hashes
belong here. All observations come from small, bounded JSON files. Unknown money
remains null; a missing reading must not claim that nothing has been spent.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re


_TERMINAL = frozenset({"completed", "turn_limit", "budget_limit", "stopped", "provider_rejected",
                       "provider_unknown", "output_limit", "failed_worker", "failed",
                       "ended_without_finish", "payload_limit"})
_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_MAX_JSON_BYTES = 4 * 1024 * 1024


def _segment(value):
    return isinstance(value, str) and _SEGMENT.fullmatch(value) is not None and value not in {".", ".."}


def _read(root: Path, relative: str):
    """Read one confined JSON object; an absent/in-progress/bad record is unavailable."""
    try:
        path = root / relative
        if not path.resolve().is_relative_to(root) or path.stat().st_size > _MAX_JSON_BYTES:
            return None, None
        data = path.read_bytes()
        if len(data) > _MAX_JSON_BYTES:
            return None, None
        value = json.loads(data)
        return (value, data) if isinstance(value, dict) else (None, None)
    except (OSError, ValueError, TypeError, RecursionError):
        return None, None


def _runner_refusal(root: Path, relative: str):
    """The CLI's structured refusal line (status "refused", a short message) from the end of the runner log, or
    None. Only that JSON message is shown, never other log text."""
    try:
        path = root / relative
        if not path.resolve().is_relative_to(root):
            return None
        with path.open("rb") as stream:
            stream.seek(max(0, path.stat().st_size - 8192))
            lines = stream.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict) and value.get("status") == "refused" and isinstance(value.get("message"), str):
            return value["message"][:500] or None
    return None


def _time(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None
    except ValueError:
        return None


def _iso(value):
    parsed = _time(value)
    return parsed.isoformat() if parsed is not None else None


def _amount(value, *, micro=False):
    if value is None or isinstance(value, bool):
        return None
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0:
            return None
        if micro:
            amount /= 1_000_000
        return format(amount, "f")
    except (InvalidOperation, ValueError, TypeError):
        return None


def _money(sources, name):
    for source in sources:
        if not isinstance(source, dict):
            continue
        for suffix, micro in (("_micro_usd", True), ("_usd", False)):
            value = _amount(source.get(name + suffix), micro=micro)
            if value is not None:
                return value
        if name == "held":
            value = _amount(source.get("reserved_usd"))
            if value is not None:
                return value
    return None


def _same_run(doc, run_id):
    return doc if isinstance(doc, dict) and doc.get("run_id") == run_id else None


def _snapshot(record):
    root = Path(record["root"]).resolve()
    run_id = record["run_id"]
    if not _segment(run_id):
        raise ValueError("invalid registered run id")
    run_relative = "runs/" + run_id + "/"
    runner, _ = _read(root, run_relative + "runner-status.json")
    result, _ = _read(root, run_relative + "result.json")
    heartbeat, _ = _read(root, "heartbeat.json")
    attempt, _ = _read(root, run_relative + "delivery.json")
    pointer, _ = _read(root, "deliveries/current.json")
    usage, _ = _read(root, "work/owner_edit/understanding-usage.json") if record.get("kind") == "understand" else (None, None)
    usage = _same_run(usage, run_id)
    refusal = _runner_refusal(root, run_relative + "runner.log")
    return {"root": root, "run_id": run_id, "runner": runner or {}, "refusal": refusal,
            "result": _same_run(result, run_id) or {},
            "heartbeat": _same_run(heartbeat, run_id) or {}, "attempt": attempt or {},
            "pointer": pointer or {}, "usage": usage or {}}


def _delivery(snapshot):
    root, run_id = snapshot["root"], snapshot["run_id"]
    attempt, pointer = snapshot["attempt"], snapshot["pointer"]
    # Prefer the run-specific record: a later run may already have moved current.
    candidates = [attempt.get("id", attempt.get("delivery_id")), pointer.get("id")]
    for delivery_id in dict.fromkeys(value for value in candidates if _segment(value)):
        manifest, data = _read(root, "deliveries/" + delivery_id + "/manifest.json")
        if (not manifest or manifest.get("schema") != "g3d.delivery/1"
                or manifest.get("id") != delivery_id or manifest.get("run_id") != run_id
                or manifest.get("job_id") != root.name):
            continue
        glb_sha = manifest.get("glb_sha256")
        if not isinstance(glb_sha, str) or not _SHA.fullmatch(glb_sha):
            continue
        manifest_sha = hashlib.sha256(data).hexdigest()
        if pointer.get("id") == delivery_id and (pointer.get("glb_sha256") != glb_sha
                or pointer.get("manifest_sha256") != manifest_sha):
            continue
        if candidates[0] == delivery_id and attempt.get("manifest_sha256", manifest_sha) != manifest_sha:
            continue
        return {"root": str(root), "delivery_id": delivery_id, "glb_sha256": glb_sha,
                "manifest_sha256": manifest_sha, "manifest": manifest,
                "pointer": pointer if pointer.get("id") == delivery_id else None,
                "attempt": attempt if candidates[0] == delivery_id else None}
    return None


def find_run_delivery(record: dict) -> dict | None:
    """Find this run's publication without hashing/reading its model or report.

    Returns ``root, delivery_id, glb_sha256, manifest_sha256, manifest, pointer,
    attempt``. A run-specific attempt can resolve an old run after current moves.
    The manifest must identify this exact run; directory-name prefixes never count.
    """
    return _delivery(_snapshot(record))


def _understanding_ready(snapshot):
    if not snapshot["usage"]:
        return False
    root = snapshot["root"]
    shown, _ = _read(root, "work/owner_edit/understanding-delivery.json")
    understanding, data = _read(root, "work/owner_edit/understanding.json")
    return bool(shown and understanding and shown.get("delivered") is True
                and shown.get("sha256") == hashlib.sha256(data).hexdigest()
                and shown.get("call_id") == understanding.get("call_id")
                and isinstance(shown.get("call_id"), str))


def _error(value, fallback):
    if isinstance(value, dict):
        message = value.get("message") or value.get("reason")
        if isinstance(message, str) and message:
            return message
    if isinstance(value, str) and value:
        return value
    return fallback


def read_run_status(record: dict, *, now: datetime | None = None) -> dict:
    """Return the frozen ``g3d.dev_run/1`` DTO from cheap persisted observations.

    ``now`` is an optional timezone-aware test clock. ``delivery_key`` is emitted
    only after a matching publication is found; callers register that descriptor
    through their verified delivery adapter, then persist its key in ``record``.
    A usable draft ends the run even when its actual ``terminal_status`` is failed.
    A publication refusal, or an exited runner with no publication, fails visibly.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("status clock must be timezone-aware")
    state = _snapshot(record)
    runner, result, heartbeat = state["runner"], state["result"], state["heartbeat"]
    attempt = state["attempt"]
    delivery = _delivery(state) if record.get("kind", "job") != "understand" else None
    understanding_ready = record.get("kind") == "understand" and _understanding_ready(state)
    terminal = result.get("status") or heartbeat.get("final_status")
    if not terminal and heartbeat.get("state") in _TERMINAL:
        terminal = heartbeat["state"]
    loop_state = heartbeat.get("state") or result.get("status") or "starting"
    exited = isinstance(runner.get("exit_code"), int) and not isinstance(runner.get("exit_code"), bool)
    own_refusal = (attempt.get("status") in {"no_usable_checkpoint", "draft_failed", "not_finished"}
                   and attempt.get("run_id", state["run_id"]) == state["run_id"])
    error = result.get("error")
    if delivery or understanding_ready:
        phase = "ended"
    elif own_refusal:
        phase = "failed"
        error = _error(attempt, "The run ended without a usable model.")
    elif exited or runner.get("error") or record.get("error"):
        phase = "failed"
        error = _error(runner.get("error") or record.get("error") or error or state["refusal"],
                       "The run ended before its result could be published.")
    elif result or terminal:
        phase = "finalizing"
    elif heartbeat and heartbeat.get("state") != "starting":
        phase = "running"
    else:
        phase = "starting"
    started = _iso(runner.get("started_utc")) or _iso(result.get("started")) or _iso(record.get("started_at"))
    ended_at = _time(runner.get("finished_utc"))
    # Result.finished is loop completion, not host finalization. It is only a
    # fallback once publication has completed and no detached runner is recorded.
    if ended_at is None and phase in {"ended", "failed"} and not runner:
        ended_at = _time(result.get("finished"))
    elapsed = max(0.0, ((ended_at or now) - _time(started)).total_seconds()) if started else 0.0
    timestamps = [_time(value) for value in (runner.get("started_utc"), runner.get("finished_utc"),
                  heartbeat.get("updated"), result.get("finished"), record.get("updated_at"), record.get("created_utc"))]
    updated = max((value for value in timestamps if value is not None), default=None)
    budget = result.get("budget")
    money_sources = [budget, state["usage"], heartbeat]
    estimate = _amount(record.get("estimate_usd"))
    if estimate is None:
        prepared, _ = _read(state["root"], "runs/" + state["run_id"] + "/approval-request.json")
        estimate = _money([(prepared or {}).get("estimate")], "expected")
    return {"schema": "g3d.dev_run/1", "run_key": record["run_key"], "kind": record.get("kind", "job"),
            "job_id": record.get("job_id", state["root"].name), "run_id": state["run_id"], "phase": phase,
            "loop_state": loop_state, "started_at": started, "updated_at": updated.isoformat() if updated else None,
            "elapsed_seconds": round(elapsed, 3), "settled_usd": _money(money_sources, "settled"),
            "held_usd": _money(money_sources, "held"), "unknown_usd": _money(money_sources, "unknown"),
            "cutoff_usd": _amount(record.get("cutoff_usd")), "estimate_usd": estimate,
            "terminal_status": terminal, "stop_requested": bool(record.get("stop_requested")
                or result.get("stop_observed") or (phase not in {"ended", "failed"}
                    and (state["root"] / "stop.requested").is_file())),
            "delivery_key": record.get("delivery_key") if delivery else None,
            "edit_key": record.get("edit_key"), "error": error,
            "offline_test_mode": record.get("offline_test_mode") is True}
