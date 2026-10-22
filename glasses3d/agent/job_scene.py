"""Generic job SceneHooks: verified checkpoints, durable rollback facts and image records.

The session records a completed mutation before control returns to an async tool.
Consequently a lost tool result is still visible to loop.plan_rollback after restart.
Tool wrappers enrich the same call record rather than adding duplicate mutations.
"""
from __future__ import annotations

import hashlib
import json
import threading
from copy import deepcopy
from pathlib import Path

from glasses3d.g3d_host import blender
from .journal import BlenderSession, WorkerFailed, CheckpointMismatch
from .worker_client import WorkerDied
from .loop import SceneHooks
from .procs import write_json_atomic
from .seeing import Seeing

LOCK_WAIT_S = 90.0
STATE_SCHEMA = "g3d.job_scene/1"
SEED_SCRIPT = Path(__file__).resolve().parents[1] / "g3d" / "job_seed.py"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class _Session(BlenderSession):
    def __init__(self, owner, **kwargs):
        self.owner = owner
        super().__init__(owner.root, **kwargs)

    def _journal(self, tool, call_id, arguments, result, head_before, worker_result):
        super()._journal(tool, call_id, arguments, result, head_before, worker_result)
        self.owner.record_call(call_id, tool, (head_before or {}).get("id"),
                               (self.head or {}).get("id"), result)

    def save_checkpoint(self, label, call_id=None):
        call_id = call_id or self.new_call_id("save_checkpoint")
        before = (self.head or {}).get("id")
        out = super().save_checkpoint(label, call_id=call_id)
        self.owner.record_call(call_id, "save_checkpoint", before, (self.head or {}).get("id"), out)
        return out

    def restore_checkpoint(self, cid, call_id=None):
        call_id = call_id or self.new_call_id("restore_checkpoint")
        before = (self.head or {}).get("id")
        if self.owner.owner_edit and self.owner.owner_edit["phase"] == "understand" and cid != before:
            return {"status": "refused", "code": "E_UNDERSTAND_READ_ONLY", "checkpoint": before,
                    "message": "Understand stops for owner confirmation before any geometry change."}
        out = super().restore_checkpoint(cid, call_id=call_id)
        self.owner.record_call(call_id, "restore_checkpoint", before, (self.head or {}).get("id"), out)
        return out

    def _mutating(self, tool, method, params, deadline_s, call_id, label, t0, *, arguments):
        edit = self.owner.owner_edit
        if edit and edit["phase"] == "understand":
            out = {"status": "refused", "code": "E_UNDERSTAND_READ_ONLY", "checkpoint": self.head["id"],
                   "message": "Understand is read-only. Show numbered targets and stop for owner confirmation."}
            self._journal(tool, call_id, arguments, out, self.head, None)
            return out
        before = self.head
        out = super()._mutating(tool, method, params, deadline_s, call_id, label, t0, arguments=arguments)
        if edit and out.get("status") == "ok" and self.head["fingerprint"] != before["fingerprint"]:
            from .owner_edits import ScopeGuard
            try:
                check = ScopeGuard(self.owner.owner_edit_authority).verify(self.owner)
            except (WorkerFailed, CheckpointMismatch, WorkerDied):
                raise
            except Exception as error:
                check = {"ok": False, "message": f"Scope verification failed: {type(error).__name__}: {error}"}
            if not check.get("ok"):
                self.owner.rollback_internal(before["id"], call_id=call_id, reason="owner edit scope refusal")
                out = {"status": "refused", "code": "E_OWNER_EDIT_SCOPE", "message": "Edit rolled back: " + str(check),
                       "checkpoint": self.head["id"], "restored_to": before["id"], "scope_check": check}
                self._journal(tool, call_id, arguments, out, before, None)
            else:
                out["owner_scope_check"] = check
        return out


class JobScene(SceneHooks):
    def __init__(self, root, job=None, selftest=False, *, captured_seed=None, captured_fingerprint=None):
        self.root = Path(root).resolve()
        job_path = self.root / "job.json"
        self.job = job if job is not None else json.loads(job_path.read_text(encoding="utf-8"))
        from . import edits
        self.owner_edit = edits.load(self.root)
        self.owner_edit_authority = edits.Authority(self.root) if self.owner_edit else None
        self.captured_fingerprint = captured_fingerprint
        self.lock = threading.RLock()
        self._state_lock = threading.RLock()
        self.state_path = self.root / "work" / "job_scene" / "state.json"
        state = json.loads(self.state_path.read_text(encoding="utf-8")) if self.state_path.is_file() else {}
        if state and state.get("schema") != STATE_SCHEMA:
            raise ValueError("unsupported job scene state schema")
        self.events = list(state.get("events", []))
        self.images = list(state.get("images", []))
        self.finish = state.get("finish")
        self.finish_review = state.get("finish_review")
        self.last_ar = state.get("last_ar")
        self.best_checkpoint = state.get("best_checkpoint")
        self.best_checkpoint_run_id = state.get("best_checkpoint_run_id")
        self.active_run_id = state.get("active_run_id")
        self._unseen = set(state.get("unseen", []))
        self._review_log = None
        self.seed_path = Path(captured_seed).resolve() if captured_seed else self.root / "inputs" / "seed.blend"
        if captured_seed and (not self.seed_path.is_relative_to(self.root / "work") or not captured_fingerprint):
            raise ValueError("a host-captured stage seed must be bound to a fingerprint inside its work directory")
        self.s = _Session(self, seed_blend=self.seed_path, selftest=selftest,
                          calls_dir=self.root / "work" / "session_calls")
        self.seeing = Seeing(self.s)

    def save(self):
        with self._state_lock:
            write_json_atomic(self.state_path, {"schema": STATE_SCHEMA, "events": self.events,
                                               "images": self.images, "finish": self.finish,
                                               "finish_review": self.finish_review,
                                               "last_ar": self.last_ar, "best_checkpoint": self.best_checkpoint,
                                               "best_checkpoint_run_id": self.best_checkpoint_run_id,
                                               "active_run_id": self.active_run_id,
                                               "unseen": sorted(self._unseen)})

    def start(self):
        with self.lock:
            if self.s.worker is not None:
                return {"head": self.s.head, "worker": self.s.worker.record()}
            if self.s.head is None and not self.seed_path.is_file():
                out = blender.run(None, SEED_SCRIPT, [str(self.seed_path)], timeout_s=120.0)
                log = self.root / "work" / "job_scene" / "seed.log"
                log.parent.mkdir(parents=True, exist_ok=True)
                log.write_text(out.stdout + "\n" + out.stderr, encoding="utf-8")
                if out.returncode or not self.seed_path.is_file():
                    raise RuntimeError(f"starting scene creation failed (exit {out.returncode}); see {log}")
            result = self.s.start()
            if self.owner_edit and len(self.s.index.checkpoints()) == 1:
                if self.s.head["fingerprint"] != (self.captured_fingerprint or self.owner_edit["source"]["fingerprint"]):
                    self.s.close()
                    raise ValueError("edit seed does not match the delivered fingerprint")
            return result

    def checkpoint(self, label, call_id=None):
        with self.lock:
            return self.s.save_checkpoint(label, call_id=call_id)

    def record_call(self, call_id, tool, checkpoint_before, checkpoint_after, result=None):
        with self._state_lock:
            # A nested seeing call and its wrapper share the SDK id. Keep the original
            # before, then the wrapper's final after, so a mutation cannot become a read.
            previous = next((e for e in self.events if e.get("kind", "call") == "call"
                             and e.get("call_id") == call_id), None)
            before = previous["checkpoint_before"] if previous else checkpoint_before
            event = {"kind": "call", "call_id": call_id, "tool": tool,
                     "checkpoint_before": before, "checkpoint_after": checkpoint_after,
                     "fingerprint_before": (self.s.index.get(before) or {}).get("fingerprint"),
                     "fingerprint_after": (self.s.index.get(checkpoint_after) or {}).get("fingerprint"),
                     "mutating": before != checkpoint_after, "status": (result or {}).get("status"),
                     "seconds": (result or {}).get("seconds"), "restored_to": (result or {}).get("restored_to")}
            if previous is None:
                self.events.append(event)
            else:
                previous.update(event)
            if event["mutating"]:
                self.finish = None
                self.finish_review = None
            if tool == "fit_cameras":
                self.finish_review = None
            if call_id in self._unseen and self.finish and self.finish.get("call_id") == call_id:
                self.finish = None
            if call_id in self._unseen and self.finish_review and self.finish_review.get("call_id") == call_id:
                self.finish_review = None
            self.save()
            if self._review_log is not None:
                self._review_log.record_journal(call_id, event)

    def bind_review_log(self, log):
        self._review_log = log
        run_id = log.run_id
        if self.active_run_id != run_id:
            self.best_checkpoint = None
            self.best_checkpoint_run_id = None
        self.active_run_id = run_id
        # A reused scene object may serve a fresh run with no previous conversation.
        self._emitted_images = {}
        self._segment_images = {}
        self.finish_review = None
        self.save()

    def journal_entries(self):
        with self._state_lock:
            self._recover_checkpoint_events()
            return [dict(e) for e in self.events]

    def _recover_checkpoint_events(self):
        """Recover the kill window between durable checkpoint append and completed-call journaling.

        A rollback marker covers its indexed head and every earlier checkpoint. Already
        journaled mutations retain their original facts; a partially journaled read is
        upgraded only when the index proves that same call changed the checkpoint.
        """
        checkpoints = self.s.index.checkpoints()
        order = {row["id"]: n for n, row in enumerate(checkpoints)}
        # Host rollbacks are recovery barriers, never model actions to undo. The index metadata
        # survives a kill before state.json was written. Recognize old derived ids only when
        # their original model call is independently present in the index or journal.
        known = {r.get("call_id") for r in checkpoints} | {e.get("call_id") for e in self.events}
        recovered, removed = [], False
        for row in checkpoints:
            if row.get("kind") != "restore":
                continue
            call_id = row.get("call_id") or ""
            origin = row.get("recovery_of")
            if not origin:
                for suffix in ("-scope-rollback", "-rollback"):
                    if call_id.endswith(suffix) and call_id[:-len(suffix)] in known:
                        origin = call_id[:-len(suffix)]
                        break
            if origin:
                count_before = len(self.events)
                self.events = [e for e in self.events if not
                               (e.get("kind", "call") == "call" and e.get("call_id") == call_id
                                and call_id != origin)]
                removed |= len(self.events) != count_before
                if not any(e.get("kind") == "rollback" and e.get("head") == row["id"] for e in self.events):
                    recovered.append({"kind": "rollback", "checkpoint": row.get("restored_from"),
                                      "checkpoint_before": row.get("parent"), "head": row["id"],
                                      "origin_call_id": origin, "reason": "recovered host rollback"})
        for event in recovered:
            position = next((n for n, e in enumerate(self.events)
                             if order.get(e.get("checkpoint_before"), -1) >= order[event["head"]]), len(self.events))
            self.events.insert(position, event)
        barrier = max((order.get(e.get("head"), -1) for e in self.events if e.get("kind") == "rollback"), default=-1)
        calls = {}
        for n, row in enumerate(checkpoints):
            if n > barrier and row.get("kind") in ("save", "restore") and row.get("call_id"):
                calls.setdefault(row["call_id"], []).append(row)
        changed = bool(recovered) or removed
        for call_id, rows in calls.items():
            previous = next((e for e in self.events if e.get("kind", "call") == "call"
                             and e.get("call_id") == call_id), None)
            if previous and previous.get("mutating"):
                continue
            first, last = rows[0], rows[-1]
            before = (previous or {}).get("checkpoint_before") or first["parent"]
            if before == last["id"]:
                before = first["parent"]
            event = {"kind": "call", "call_id": call_id,
                     "tool": (previous or {}).get("tool", "checkpointed_scene_change"),
                     "checkpoint_before": before, "checkpoint_after": last["id"], "mutating": True,
                     "fingerprint_before": (self.s.index.get(before) or {}).get("fingerprint"),
                     "fingerprint_after": last["fingerprint"], "status": "checkpointed",
                     "recovered_from": "checkpoint_index"}
            if previous is not None:
                previous.update(event)
            else:
                # A later read may already have a result; keep its real position as
                # plan_rollback treats the last recorded result as a boundary.
                position = next((n for n, e in enumerate(self.events)
                                 if order.get(e.get("checkpoint_before"), -1) >= order[first["id"]]), len(self.events))
                self.events.insert(position, event)
            changed = True
        if changed:
            self.finish = None
            self.finish_review = None
            self.save()

    def rollback_internal(self, checkpoint_id, *, call_id, reason):
        with self.lock:
            before = (self.s.head or {}).get("id")
            target = self.s.index.get(checkpoint_id)
            try:
                out = BlenderSession.restore_checkpoint(self.s, checkpoint_id, call_id=call_id, recovery_of=call_id)
            except (WorkerFailed, CheckpointMismatch):
                raise
            except Exception as error:
                raise WorkerFailed(f"rollback to {checkpoint_id} failed: {error}", self.s.head) from error
            if (out.get("status") != "ok" or not target
                    or self.s.head["fingerprint"] != target["fingerprint"]):
                raise WorkerFailed(f"rollback to {checkpoint_id} failed: {out.get('message', out.get('status'))}",
                                   self.s.head)
            with self._state_lock:
                self.events.append({"kind": "rollback", "checkpoint": checkpoint_id,
                                    "checkpoint_before": before, "head": self.s.head["id"], "reason": reason,
                                    "origin_call_id": call_id})
                self.finish = None
                self.finish_review = None
                try:
                    self.save()
                except Exception as error:
                    raise WorkerFailed(f"rollback to {checkpoint_id} could not save its recovery state: {error}",
                                       self.s.head) from error
            return {"checkpoint": out["checkpoint"], "restored_from": checkpoint_id}

    def restore(self, checkpoint_id, *, reason):
        return self.rollback_internal(checkpoint_id, call_id=self.s.new_call_id("loop-rollback"), reason=reason)

    def exit_checkpoint(self):
        if not self.lock.acquire(timeout=LOCK_WAIT_S):
            raise RuntimeError("a job tool still holds the scene; the exit checkpoint was not verified")
        try:
            head = self.s.head
            if head is None:
                return None
            return {"id": head["id"], "sha256": head["sha256"],
                    "verified": _sha(self.s.ck_dir / head["file"]) == head["sha256"]
                    and self.s.fingerprint() == head["fingerprint"]}
        finally:
            self.lock.release()

    def worker_info(self):
        worker = self.s.worker
        return {"worker_pid": worker.pid if worker else None,
                "worker_cpu_s": worker.cpu_seconds() if worker else None}

    def record_images(self, call_id, result):
        with self._state_lock:
            event = next((e for e in reversed(self.events) if e.get("call_id") == call_id), {})
            head = self.s.head or {}
            for item in result.get("images") or []:
                desc = item if isinstance(item, dict) else {"path": item}
                path = Path(desc["path"])
                path = path.resolve() if path.is_absolute() else (self.root / path).resolve()
                if not path.is_relative_to(self.root) or not path.is_file():
                    raise ValueError("tool image must be a file inside the job")
                rec = {**desc, "path": str(path.relative_to(self.root)), "sha256": _sha(path),
                       "call_id": call_id, "checkpoint": desc.get("checkpoint", head.get("id")),
                       "kind": desc.get("kind", event.get("tool", "image")),
                       "view": desc.get("view", desc.get("ref", "sheet"))}
                if not any(i["sha256"] == rec["sha256"] and i["call_id"] == call_id
                           and i.get("output_index", i["view"]) == rec.get("output_index", rec["view"])
                           for i in self.images):
                    self.images.append(rec)
            self.save()
        return result

    def renewal_state(self):
        """Read-only facts for a fresh conversation, independent of Astra's summary."""
        with self._state_lock:
            review = deepcopy(self.finish_review)
            return {"head_checkpoint": (self.s.head or {}).get("id"),
                    "best_checkpoint": self.best_checkpoint,
                    "finish_review": {"status": "pending" if review else "not_pending", **(review or {})},
                    "tool_calls_so_far": sum(e.get("kind", "call") == "call" for e in self.events),
                    "owner_edit_phase": (self.owner_edit or {}).get("phase")}

    def renewal_started(self):
        """Restart the two-call finish review; its next first call must emit every image again."""
        with self._state_lock:
            previous = (self.finish_review or {}).get("call_id")
            self.finish_review = None
            self._emitted_images = {}
            self._segment_images = {}
            self.save()
            return {"finish_review_restarted": previous is not None, "previous_review_call_id": previous}

    def renewal_evidence(self):
        """Latest full comparison per photo and current picks, using their exact delivered bytes.

        A comparison is a call/photo group, not one image: keep every native tile,
        close-up and side-light page belonging to the selected group. Metadata survives
        renewal, so a second renewal can select evidence carried by the first one.
        """
        with self._state_lock:
            records = deepcopy(self.images)
            events = {e.get("call_id"): e.get("tool") for e in self.events}
            unseen = set(self._unseen)
        comparison_tools = {"compare_look_to_photos", "compare_to_photos"}
        full_kinds = {"look_full_pair", "look_composed", "compare_to_photos", "compare_look_to_photos"}
        look_kinds = full_kinds | {"look_detail", "surface_detail"}
        groups, picks = {}, {}
        for index, record in enumerate(records):
            if record.get("call_id") in unseen or record.get("current") is False:
                continue
            kind = record.get("kind")
            tool = events.get(record.get("call_id"), kind)
            if kind in {"photo_read", "pick_surface_point"} or record.get("context_role") == "pick":
                # photo_read's view is the persistent reading name; a compact sheet
                # contains all names in views and remains whole when any is current.
                for view in record.get("views") or [record.get("view", "sheet")]:
                    picks[(kind, view)] = index
            if tool not in comparison_tools and kind not in look_kinds:
                continue
            panels = record.get("panels") or [record]
            for panel in panels:
                view = panel.get("ref") or panel.get("view", "sheet")
                # Legacy compare_to_photos crops use a labelled view but lack ref.
                # A crop does not supersede that photo's preceding full comparison.
                crop = " crop " in view and not panel.get("ref")
                photo = view.split(" crop ", 1)[0] if crop else view
                key = (record["call_id"], photo)
                group = groups.setdefault(key, {"indices": [], "full": False})
                group["indices"].append(index)
                group["full"] |= not crop and panel.get("kind", kind) in full_kinds
        latest = {}
        for key, group in groups.items():
            if group["full"]:
                latest[key[1]] = key
        chosen = set(picks.values())
        for key in latest.values():
            chosen.update(groups[key]["indices"])
        result = []
        for index in sorted(chosen):
            record = records[index]
            path = (self.root / record["path"]).resolve()
            if not path.is_relative_to(self.root) or not path.is_file() or _sha(path) != record["sha256"]:
                raise ValueError("renewal evidence image is missing, changed or outside the job")
            result.append({"path": str(path), "sha256": record["sha256"], "call_id": record["call_id"],
                           "checkpoint": record.get("checkpoint"), "view": record.get("view", "sheet"),
                           "kind": record.get("kind", "image"),
                           **({"revision": record["revision"]} if "revision" in record else {})})
        return result

    def mark_unseen(self, call_id):
        with self._state_lock:
            self._unseen.add(call_id)
            if self.finish and self.finish.get("call_id") == call_id:
                self.finish = None
            if self.finish_review and self.finish_review.get("call_id") == call_id:
                self.finish_review = None
            self.save()

    def close(self):
        got = self.lock.acquire(timeout=LOCK_WAIT_S)
        try:
            self.s.close()
            self.save()
        finally:
            if got:
                self.lock.release()
        if not got:
            raise RuntimeError("a job tool still held the scene when its worker was closed")
