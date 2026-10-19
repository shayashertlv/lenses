"""Checkpoints and the per-call protocol around the live Blender worker (glasses3d/plan/archive/20261019/WP2_DESIGN.md §3.1, §3.2, §5).

`BlenderSession` owns one worker and the job's checkpoint chain (`checkpoints/cNNNN.blend` + `index.jsonl`):

* start: an empty chain saves `c0000` from the seed (or the factory scene); otherwise the worker opens the head and
  its fingerprint must equal the head's (else the parent is tried and a defect logged);
* every mutating call (snippet, trusted mutating method, restore): the worker fingerprints the scene first and refuses
  on drift from the head (a `drift` checkpoint is then saved and the call re-issued once), runs, and on success
  protects and fingerprints again; a changed scene becomes checkpoint c(N+1) (head), an unchanged one saves nothing
  (call N's head is call N+1's `checkpoint_before`). ANY non-ok result (error, timeout, refused_runtime) restores
  `checkpoint_before` and verifies it, so Astra always knows the scene state; a statically refused snippet never runs
  and leaves the scene untouched;
* a dead worker (hard timeout, EOF, exit, frame error) is restarted on the head and verified; the result is
  `worker_restarted` and the call is never replayed (upstream issue #279). At most 2 restart attempts per call; 3
  restarts within 10 minutes raise WorkerFailed (the run ends `failed_worker` at the verified head);
* a live worker's RPC `FrameError` reply (an unserializable result) restores the head and journals the original
  error, then raises WorkerFailed. Retrying through a model reply cannot repair a broken worker result contract;
* a call still running at its soft timeout + 1 s starts a warm spare worker on the head in the background (the head
  cannot move while the call runs), so a hard kill costs the verification, not a Blender start (Blender needs 3.1-3.4 s
  to reach the worker script on this machine under load); a call that ends in time discards the spare.

Trusted read-only methods (bounds, render, pick, export_glb: agent/seeing.py) go through `call_read`: a dead worker
is restarted on the head like any call, and a read-only method that reports a changed fingerprint restores the head.

Index rows (`g3d.checkpoint_index/1`): {id, kind: start|save|drift|restore|fallback|label, parent, call_id, label,
fingerprint, sha256, bytes, file, packed_images, packed_external, fake_user_added, created, restored_from, recovery_of}.
A label row names an existing checkpoint and does not move the head. A restore row references the restored file;
an internal rollback names the original call in recovery_of so crash recovery never replays the refused edit.
"""
from __future__ import annotations

import hashlib
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from ..g3d import safe_mode
from . import procs
from .jsonl import append_jsonl, read_jsonl
from .tool_timeouts import (CHECKPOINT_DEADLINE_S, HARD_MARGIN_S, READ_DEADLINE_S, RESTARTS_PER_CALL, J_SPARE_JOIN_S,
                            SOFT_DEFAULT_S, SOFT_MAX_S, SOFT_MIN_S)
from .worker_client import WorkerClient, WorkerDied, WorkerError

INDEX_SCHEMA = "g3d.checkpoint_index/1"
HEAD_KINDS = ("start", "save", "drift", "restore", "fallback")
RESTART_LIMIT, RESTART_WINDOW_S = 3, 600.0
SPARE_GRACE_S = 1.0                  # a warm spare starts when a call is still running soft + this


class CheckpointMismatch(RuntimeError):
    """A restored checkpoint does not have the fingerprint its index row records (a stop condition)."""


class WorkerFailed(RuntimeError):
    """The worker cannot continue safely: failed recovery or an invalid RPC result."""

    def __init__(self, message: str, head: dict | None):
        super().__init__(message)
        self.head = head


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class CheckpointIndex:
    def __init__(self, directory: Path) -> None:
        self.dir = Path(directory)
        self.path = self.dir / "index.jsonl"
        result = read_jsonl(self.path)
        self.rows: list[dict] = result.rows
        self.recoveries: list[dict] = [result.recovery] if result.recovery else []

    def append(self, row: dict) -> dict:
        recovery = append_jsonl(self.path, row)
        if recovery:
            self.recoveries.append(recovery)
        self.rows.append(row)
        return row

    def checkpoints(self) -> list[dict]:
        return [r for r in self.rows if r.get("kind") in HEAD_KINDS]

    @property
    def head(self) -> dict | None:
        cps = self.checkpoints()
        return cps[-1] if cps else None

    def get(self, cid: str) -> dict | None:
        for r in reversed(self.checkpoints()):
            if r["id"] == cid:
                return r
        return None

    def labels(self, cid: str) -> list[str]:
        return [r["label"] for r in self.rows if r.get("id") == cid and r.get("label")]

    def next_id(self) -> str:
        return f"c{len(self.checkpoints()):04d}"


class BlenderSession:
    def __init__(self, job_root, *, seed_blend=None, blender=None, selftest: bool = False, probe: bool = False,
                 calls_dir=None, clock=time.monotonic) -> None:
        self.root = Path(job_root).resolve()
        self.ck_dir = self.root / "checkpoints"
        self.index = CheckpointIndex(self.ck_dir)
        self.seed_blend = Path(seed_blend) if seed_blend else None
        self.blender = blender
        self.selftest = selftest
        self.probe = probe
        self.calls_dir = Path(calls_dir) if calls_dir else None
        self.clock = clock
        self.worker: WorkerClient | None = None
        self._spare: dict | None = None          # {"thread", "worker", "error", "head"}: a warm replacement
        self.restarts: list[float] = []
        self.defects: list[dict] = [dict(record) for record in self.index.recoveries]
        self.call_seq = 0

    # ---------------------------------------------------------------------------------------------- lifecycle
    @property
    def head(self) -> dict | None:
        return self.index.head

    def _file(self, row: dict) -> Path:
        return self.ck_dir / row["file"]

    def _spawn(self, blend: Path | None) -> WorkerClient:
        return WorkerClient(self.root, start_blend=blend, blender=self.blender, selftest=self.selftest,
                            probe=self.probe).start()

    def start(self) -> dict:
        """Start the worker on the head (verified) or save c0000 from the seed. Returns {head, worker}."""
        head = self.head
        if head is None:
            self.worker = self._spawn(self.seed_blend)
            self._save(kind="start", label="start" if self.seed_blend is None else "seed", call_id=None)
        else:
            self.worker = self._spawn(self._file(head))
            self._verify_open(head)
        return {"head": self.head, "worker": self.worker.record(), "ready_s": self.worker.ready_s,
                **({"checkpoint_index_recoveries": self.index.recoveries} if self.index.recoveries else {})}

    def _verify_open(self, head: dict) -> None:
        """The opened head must match its recorded bytes and fingerprint, else try the verified parent."""
        fp = self.worker.hello.get("fingerprint")
        digest = hashlib.sha256(self._file(head).read_bytes()).hexdigest()
        if fp == head["fingerprint"] and digest == head["sha256"]:
            return
        self._defect("head_mismatch", f"{head['id']} opened with fingerprint {fp} (recorded {head['fingerprint']}); "
                                      f"file sha256 {digest} (recorded {head['sha256']})")
        parent = self.index.get(head["parent"]) if head.get("parent") else None
        if parent is None:
            raise CheckpointMismatch(f"{head['id']} does not match its recorded bytes/fingerprint and has no parent")
        r = self.worker.call("checkpoint_restore", {"path": str(self._file(parent)),
                                                    "expect_fingerprint": parent["fingerprint"],
                                                    "expect_sha256": parent["sha256"]},
                             deadline_s=CHECKPOINT_DEADLINE_S)
        if not r.get("matches"):
            raise CheckpointMismatch(f"neither {head['id']} nor its parent {parent['id']} reopens to its fingerprint")
        self.index.append({**parent, "id": self.index.next_id(), "kind": "fallback", "parent": head["id"],
                           "restored_from": parent["id"], "created": _now(), "label": f"fallback to {parent['id']}"})

    def close(self) -> None:
        self._discard_spare()
        if self.worker is not None:
            self.worker.close()
            self.worker = None

    # ---------------------------------------------------------------------------------------------- warm spare
    def _start_spare(self) -> None:
        """A call outlived its soft timeout: start a replacement worker on the head in the background, so a hard
        kill costs the verification only, not a Blender start (3-4.5 s on this machine under load)."""
        if self._spare is not None or self.head is None:
            return
        head = self.head
        spare = {"head": head, "worker": None, "error": None}

        def run():
            try:
                spare["worker"] = self._spawn(self._file(head))
            except Exception as exc:  # noqa: BLE001 - the normal restart path takes over
                spare["error"] = f"{type(exc).__name__}: {exc}"
        spare["thread"] = threading.Thread(target=run, name="g3d-spare-worker", daemon=True)
        self._spare = spare
        spare["thread"].start()

    def _take_spare(self) -> WorkerClient | None:
        spare, self._spare = self._spare, None
        if spare is None:
            return None
        spare["thread"].join(J_SPARE_JOIN_S)
        w = spare["worker"]
        if w is None or spare["head"]["id"] != (self.head or {}).get("id"):
            if w is not None:
                w.kill("stale spare")
            return None
        return w

    def _discard_spare(self) -> None:
        spare, self._spare = self._spare, None
        if spare is None:
            return

        def reap():
            spare["thread"].join(J_SPARE_JOIN_S)
            if spare["worker"] is not None:
                spare["worker"].kill("spare not needed")
        threading.Thread(target=reap, name="g3d-spare-reaper", daemon=True).start()

    def _defect(self, kind: str, detail: str) -> None:
        self.defects.append({"kind": kind, "detail": detail, "time": _now()})

    # ---------------------------------------------------------------------------------------------- checkpoints
    def _save(self, *, kind: str, label: str | None, call_id: str | None, protected: dict | None = None) -> dict:
        """Write checkpoint c(N+1). `protected`: the call's own protect receipt (the worker packs and fake-users at
        the end of every ok call, so the save's protect usually finds nothing left); both are listed in the row."""
        cid = self.index.next_id()
        path = self.ck_dir / f"{cid}.blend"
        r = self.worker.call("checkpoint_save", {"path": str(path)}, deadline_s=CHECKPOINT_DEADLINE_S)
        head = self.head
        prot = protected or {}

        def merged(key):
            return sorted(set(prot.get(key) or []) | set(r.get(key) or []))
        row = {"schema": INDEX_SCHEMA, "id": cid, "kind": kind, "parent": head["id"] if head else None,
               "call_id": call_id, "label": label or None, "fingerprint": r["fingerprint"], "sha256": r["sha256"],
               "bytes": r["bytes"], "file": path.name, "packed_images": merged("packed_images"),
               "packed_external": merged("packed_external"), "fake_user_added": merged("fake_user_added"),
               "created": _now(), "save_seconds": r["seconds"]}
        if r.get("pack_failed"):
            row["pack_failed"] = r["pack_failed"]
        return self.index.append(row)

    def _restore(self, row: dict) -> dict:
        r = self.worker.call("checkpoint_restore", {"path": str(self._file(row)),
                                                    "expect_fingerprint": row["fingerprint"],
                                                    "expect_sha256": row["sha256"]},
                             deadline_s=CHECKPOINT_DEADLINE_S)
        if not r.get("matches"):
            raise CheckpointMismatch(f"restoring {row['id']} gave fingerprint {r.get('fingerprint')} != "
                                     f"{row['fingerprint']}")
        return r

    def fingerprint(self) -> str:
        return self.worker.call("fingerprint", deadline_s=READ_DEADLINE_S)["fingerprint"]

    def list_checkpoints(self) -> list[dict]:
        return [{"id": r["id"], "kind": r["kind"], "parent": r.get("parent"), "call_id": r.get("call_id"),
                 "labels": self.index.labels(r["id"]), "fingerprint": r["fingerprint"], "bytes": r.get("bytes"),
                 "created": r.get("created"), "restored_from": r.get("restored_from")}
                for r in self.index.checkpoints()]

    def save_checkpoint(self, label: str, call_id: str | None = None) -> dict:
        """Label the head; save a new checkpoint only when the scene drifted from it."""
        head = self.head
        if self.fingerprint() == head["fingerprint"]:
            self.index.append({"schema": INDEX_SCHEMA, "id": head["id"], "kind": "label", "label": label,
                               "call_id": call_id, "created": _now()})
            return {"status": "ok", "checkpoint": head["id"], "saved": False, "labels": self.index.labels(head["id"])}
        self._defect("drift", f"scene drifted from {head['id']} before save_checkpoint")
        row = self._save(kind="drift", label=label, call_id=call_id)
        return {"status": "ok", "checkpoint": row["id"], "saved": True, "labels": [label]}

    def restore_checkpoint(self, cid: str, call_id: str | None = None, *, recovery_of: str | None = None) -> dict:
        """Open and verify `cid`, then record a restore row as head.

        Internal rollbacks durably name their originating call in ``recovery_of``.
        """
        target = self.index.get(cid)
        if target is None:
            return {"status": "error", "message": f"no checkpoint {cid!r}; list_checkpoints() names them"}
        head = self.head
        t0 = time.perf_counter()
        try:
            self._restore(target)
        except WorkerDied as exc:
            return self._after_death(exc, "restore_checkpoint", call_id, t0)
        if target["fingerprint"] == head["fingerprint"]:
            return {"status": "ok", "checkpoint": head["id"], "restored_from": cid, "changed": False,
                    **({"recovery_of": recovery_of} if recovery_of is not None else {}),
                    "seconds": round(time.perf_counter() - t0, 3)}
        row = self.index.append({**{k: target[k] for k in ("fingerprint", "sha256", "bytes", "file")},
                                 "schema": INDEX_SCHEMA, "id": self.index.next_id(), "kind": "restore",
                                 "parent": head["id"], "call_id": call_id, "label": f"restore of {cid}",
                                 **({"recovery_of": recovery_of} if recovery_of is not None else {}),
                                 "restored_from": cid, "packed_images": [], "packed_external": [],
                                 "fake_user_added": [], "created": _now()})
        return {"status": "ok", "checkpoint": row["id"], "restored_from": cid, "changed": True,
                **({"recovery_of": recovery_of} if recovery_of is not None else {}),
                "seconds": round(time.perf_counter() - t0, 3)}

    # ---------------------------------------------------------------------------------------------- calls
    def _call_id(self, call_id: str | None, tool: str) -> str:
        self.call_seq += 1
        return call_id or f"{tool}-{self.call_seq:04d}"

    def execute(self, code: str, *, soft_timeout_s: float = SOFT_DEFAULT_S, label: str = "",
                call_id: str | None = None, unvalidated: bool = False, hard_timeout_s: float | None = None) -> dict:
        """Run one Astra snippet through the full protocol; returns the §3.1 result. The hard timeout (the socket
        deadline that kills the worker) defaults to soft + 30 s (glasses3d/plan/archive/20261019/WP2_DESIGN.md §7)."""
        try:
            soft = min(max(float(soft_timeout_s), SOFT_MIN_S), SOFT_MAX_S)
        except (TypeError, ValueError):
            soft = SOFT_DEFAULT_S
        call_id = self._call_id(call_id, "execute_blender_code")
        t0 = time.perf_counter()
        head = self.head
        if not unvalidated:
            violations = safe_mode.validate(code)
            if violations:
                res = {"status": "refused", "violations": [v.as_dict() for v in violations], "checkpoint": head["id"],
                       "restored_to": None, "seconds": round(time.perf_counter() - t0, 4)}
                self._journal("execute_blender_code", call_id, {"code": code, "soft_timeout_s": soft, "label": label},
                              res, head, None)
                return res
        method = "exec_unvalidated" if unvalidated else "exec"
        params = {"code": code, "soft_timeout_s": soft}
        hard = float(hard_timeout_s) if hard_timeout_s else soft + HARD_MARGIN_S
        return self._mutating("execute_blender_code", method, params, max(hard, soft + 1.0), call_id, label, t0,
                              arguments={"code": code, "soft_timeout_s": soft, "label": label})

    def call_mutating(self, tool: str, method: str, params: dict, *, deadline_s: float, label: str = "",
                      call_id: str | None = None) -> dict:
        """A trusted mutating worker method (load_font; later g3d calls) under the same protocol."""
        call_id = self._call_id(call_id, tool)
        return self._mutating(tool, method, dict(params), deadline_s, call_id, label, time.perf_counter(),
                              arguments=dict(params))

    def _mutating(self, tool, method, params, deadline_s, call_id, label, t0, *, arguments) -> dict:
        head = self.head
        for attempt in range(2):
            params["expect_fingerprint"] = head["fingerprint"]
            soft = params.get("soft_timeout_s")
            slow = (float(soft) + SPARE_GRACE_S, self._start_spare) if soft else None
            try:
                res = self.worker.call(method, params, deadline_s=deadline_s, slow=slow)
            except WorkerDied as exc:
                out = self._after_death(exc, tool, call_id, t0)
                self._journal(tool, call_id, arguments, out, head, None)
                return out
            except WorkerError as exc:
                if exc.etype == "FrameError":
                    self._fatal_frame_error(exc, tool, call_id, arguments, head, t0)
                res = {"status": "error", "traceback": f"{exc.etype}: {exc.message}", "worker_error": True,
                       "fingerprint_before": None}
            self._discard_spare()
            if res.get("status") != "drift":
                break
            self._defect("drift", f"scene fingerprint {res.get('fingerprint_before')} != head {head['id']} "
                                  f"before {call_id}")
            try:
                head = self._save(kind="drift", label=f"drift before {call_id}", call_id=call_id)
            except Exception as exc:
                self._fatal_protocol_error(exc, tool, call_id, arguments, head, t0, res,
                                           phase="drift_checkpoint_save", restore=True)
        status = res.get("status")
        out = {"status": status, "call_id": call_id}
        for key in ("result", "stdout", "stdout_truncated", "stderr", "stderr_truncated", "traceback", "message",
                    "code", "stray_files"):
            if key in res:
                out[key] = res[key]
        if status == "refused":
            out["violations"] = res.get("violations", [])
        elif res.get("runtime_violations"):
            out["violations"] = res["runtime_violations"]
        out["worker_seconds"] = res.get("seconds")
        out["restored_to"] = None
        if status == "ok":
            if res.get("changed"):
                try:
                    row = self._save(kind="save", label=label or None, call_id=call_id, protected=res.get("protected"))
                except Exception as exc:
                    self._fatal_protocol_error(exc, tool, call_id, arguments, head, t0, res,
                                               phase="checkpoint_save", restore=True)
                if row["fingerprint"] != res.get("fingerprint_after"):
                    self._defect("save_fingerprint", f"{row['id']} saved {row['fingerprint']} but the call ended at "
                                                     f"{res.get('fingerprint_after')}")
                out["checkpoint"] = row["id"]
                out["scene_delta"] = res.get("scene_delta")
            else:
                out["checkpoint"] = head["id"]
                out["scene_delta"] = None
        elif status == "refused" and res.get("fingerprint_before") is None:
            out["checkpoint"] = head["id"]                    # never ran: the scene is untouched
        else:
            try:
                self._restore(head)
            except WorkerDied as exc:
                try:
                    out2 = self._after_death(exc, tool, call_id, t0)
                except Exception as recovery_error:
                    self._fatal_protocol_error(recovery_error, tool, call_id, arguments, head, t0, res,
                                               phase="checkpoint_restore_restart")
                out2["failed_status"] = status
                self._journal(tool, call_id, arguments, out2, head, res)
                return out2
            except Exception as exc:
                self._fatal_protocol_error(exc, tool, call_id, arguments, head, t0, res,
                                           phase="checkpoint_restore")
            out["checkpoint"] = head["id"]
            out["restored_to"] = head["id"]
        out["seconds"] = round(time.perf_counter() - t0, 4)
        self._journal(tool, call_id, arguments, out, head, res)
        return out

    # ---------------------------------------------------------------------------------------------- read-only calls
    def new_call_id(self, tool: str, call_id: str | None = None) -> str:
        return self._call_id(call_id, tool)

    def call_read(self, tool: str, method: str, params: dict, *, deadline_s: float, call_id: str) -> dict:
        """A trusted read-only worker method (bounds, render, pick, export_glb; glasses3d/plan/archive/20261019/WP2_DESIGN.md §6). Returns
        {status: ok, result} | {status: error, message} | the worker_restarted record of a dead worker (restarted on
        the verified head, the call not replayed). A result reporting `unchanged: false` (the scene moved during a
        read-only method) restores the head checkpoint and logs a defect; the result then says `restored_to`."""
        t0 = time.perf_counter()
        try:
            res = self.worker.call(method, params, deadline_s=deadline_s)
        except WorkerDied as exc:
            return self._after_death(exc, tool, call_id, t0)
        except WorkerError as exc:
            if exc.etype == "FrameError":
                self._fatal_frame_error(exc, tool, call_id, params, self.head, t0)
            out = {"status": "error", "call_id": call_id, "message": f"{exc.etype}: {exc.message}",
                   "traceback": exc.traceback, "restored_to": None}
            try:                                  # a method that raised midway must not leave the scene moved
                if self.fingerprint() != self.head["fingerprint"]:
                    self._defect("read_only_changed", f"{tool} ({method}) raised and left the scene changed")
                    self._restore(self.head)
                    out["restored_to"] = self.head["id"]
            except WorkerDied as died:
                try:
                    return self._after_death(died, tool, call_id, t0)
                except Exception as recovery_error:
                    self._fatal_protocol_error(recovery_error, tool, call_id, params, self.head, t0, out,
                                               phase="read_only_recovery_restart")
            except Exception as recovery_error:
                self._fatal_protocol_error(recovery_error, tool, call_id, params, self.head, t0, out,
                                           phase="read_only_recovery")
            out["seconds"] = round(time.perf_counter() - t0, 4)
            return out
        out = {"status": "ok", "call_id": call_id, "result": res, "restored_to": None}
        if isinstance(res, dict) and res.get("unchanged") is False:
            self._defect("read_only_changed", f"{tool} ({method}) changed the scene: {res.get('fingerprint_before')}"
                                              f" -> {res.get('fingerprint_after')}")
            try:
                self._restore(self.head)
            except WorkerDied as exc:
                try:
                    return self._after_death(exc, tool, call_id, t0)
                except Exception as recovery_error:
                    self._fatal_protocol_error(recovery_error, tool, call_id, params, self.head, t0, res,
                                               phase="read_only_restore_restart")
            except Exception as exc:
                self._fatal_protocol_error(exc, tool, call_id, params, self.head, t0, res,
                                           phase="read_only_restore")
            out["restored_to"] = self.head["id"]
        out["seconds"] = round(time.perf_counter() - t0, 4)
        return out

    def _fatal_protocol_error(self, exc, tool, call_id, arguments, head, t0, worker_result,
                              *, phase, restore=False):
        """A failed save or recovery cannot leave a live run relying on an unverified head.

        Keep the original worker result alongside the infrastructure error. A failed
        save tries to restore the prior head; an already failed restore is not retried.
        Even a successful cleanup does not downgrade this to a paid tool retry.
        """
        self._discard_spare()
        error = {"type": type(exc).__name__, "message": str(exc)}
        if isinstance(exc, WorkerError):
            error = {"type": exc.etype, "message": exc.message, "traceback": exc.traceback}
        out = {"status": "failed_worker", "call_id": call_id, "checkpoint": (self.head or {}).get("id"),
               "restored_to": None, "restore_verified": False, "failure_phase": phase,
               "recovery_error": error, "worker_result": worker_result,
               "failed_status": (worker_result or {}).get("status")}
        if restore:
            try:
                try:
                    self._restore(head)
                except WorkerDied as died:
                    out["recovery"] = self._after_death(died, tool, call_id, t0)
                out.update(restored_to=self.head["id"], restore_verified=True)
            except Exception as restore_error:
                out["restore_error"] = f"{type(restore_error).__name__}: {restore_error}"
        else:
            out["restore_error"] = f"{type(exc).__name__}: {exc}"
        out["checkpoint"] = (self.head or {}).get("id")
        out["seconds"] = round(time.perf_counter() - t0, 4)
        message = f"{tool}: {phase} failed: {exc}; " + (
            f"restored verified checkpoint {out['restored_to']}" if out["restore_verified"] else
            f"checkpoint restoration failed: {out['restore_error']}")
        out["message"] = message
        try:
            self._journal(tool, call_id, arguments, out, head, worker_result)
        except Exception as journal_error:
            message += f"; could not journal worker failure: {journal_error}"
        raise WorkerFailed(message, self.head) from exc

    def _fatal_frame_error(self, exc, tool, call_id, arguments, head, t0):
        """Recover scene state, retain the RPC evidence, and stop instead of inviting another paid retry."""
        self._discard_spare()
        out = {"status": "failed_worker", "call_id": call_id, "checkpoint": head["id"],
               "restored_to": None, "restore_verified": False, "message": str(exc),
               "worker_error": {"type": exc.etype, "message": exc.message, "traceback": exc.traceback}}
        try:
            try:
                self._restore(head)
            except WorkerDied as died:
                # A lost rollback response still follows the existing verified restart path.
                out["recovery"] = self._after_death(died, tool, call_id, t0)
            out.update(checkpoint=self.head["id"], restored_to=self.head["id"], restore_verified=True)
        except (WorkerError, WorkerFailed, CheckpointMismatch) as restore_error:
            out["restore_error"] = f"{type(restore_error).__name__}: {restore_error}"
        out["seconds"] = round(time.perf_counter() - t0, 4)
        message = (f"{tool}: {exc}; " +
                   (f"restored verified checkpoint {out['restored_to']}" if out["restore_verified"] else
                    f"checkpoint restoration failed: {out['restore_error']}"))
        try:
            self._journal(tool, call_id, arguments, out, head, None)
        except OSError as journal_error:
            # Persistence failure must not turn this transport fault back into E_ARGS either.
            message += f"; could not journal worker failure: {journal_error}"
        raise WorkerFailed(message, self.head) from exc

    def record_call(self, tool: str, call_id: str, arguments: dict, result: dict) -> None:
        """Journal a host tool call (render_views, compare_to_photos, pick_surface_point, fit_cameras)."""
        self._journal(tool, call_id, arguments, result, self.head, None)

    # ---------------------------------------------------------------------------------------------- restarts
    def _after_death(self, exc: WorkerDied, tool: str, call_id: str, t0: float) -> dict:
        head = self.head
        now = self.clock()
        self.restarts = [t for t in self.restarts if now - t <= RESTART_WINDOW_S] + [now]
        old_pid = self.worker.pid if self.worker else None
        last = None
        spare = self._take_spare()
        used_spare = spare is not None
        for _ in range(RESTARTS_PER_CALL):
            try:
                if self.worker is not None:
                    self.worker.kill(exc.reason)
                self.worker, spare = (spare, None) if spare is not None else (self._spawn(self._file(head)), None)
                self._verify_open(head)
                break
            except (WorkerDied, WorkerError, CheckpointMismatch) as err:
                last = err
                if self.worker is not None:
                    self.worker.kill("restart not verified")
                self.worker = None
        else:
            raise WorkerFailed(f"this code hangs Blender; the worker could not be restarted on {head['id']} "
                               f"({last})", head)
        if len(self.restarts) >= RESTART_LIMIT:
            raise WorkerFailed(f"{len(self.restarts)} worker restarts within {RESTART_WINDOW_S:g} s; the run ends "
                               f"failed_worker at the verified head {self.head['id']}", self.head)
        return {"status": "worker_restarted", "call_id": call_id, "lost_call": call_id, "reason": exc.reason,
                "detail": exc.detail, "restored_to": self.head["id"], "checkpoint": self.head["id"],
                "old_pid": old_pid, "worker_pid": self.worker.pid, "warm_spare": used_spare,
                "message": f"{tool} {exc.reason.replace('_', ' ')}; the worker was restarted from {self.head['id']} "
                           "and that call's effect is gone (it is not replayed)",
                "seconds": round(time.perf_counter() - t0, 4)}

    # ---------------------------------------------------------------------------------------------- receipts
    def _journal(self, tool, call_id, arguments, result, head_before, worker_result) -> None:
        if self.calls_dir is None:
            return
        n = len(list(self.calls_dir.glob("*"))) + 1 if self.calls_dir.is_dir() else 1
        d = self.calls_dir / f"{n:04d}-{tool}"
        d.mkdir(parents=True, exist_ok=True)
        args = dict(arguments)
        code = args.pop("code", None)
        procs.write_json_atomic(d / "arguments.json", {"call_id": call_id, **args})
        if code is not None:
            (d / "issued-code.py").write_text(code, encoding="utf-8")
        procs.write_json_atomic(d / "result.json", result)
        procs.write_json_atomic(d / "journal.json", {
            "call_id": call_id, "tool": tool, "status": result.get("status"),
            "checkpoint_before": head_before["id"] if head_before else None,
            "fingerprint_before": (worker_result or {}).get("fingerprint_before"),
            "fingerprint_after": (worker_result or {}).get("fingerprint_after"),
            "checkpoint_after": result.get("checkpoint"), "restored_to": result.get("restored_to"),
            "seconds": result.get("seconds"), "worker_pid": self.worker.pid if self.worker else None})
