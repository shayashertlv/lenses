"""Generic job SceneHooks: verified checkpoints, durable rollback facts and image records.

The session records a completed mutation before control returns to an async tool.
Consequently a lost tool result is still visible to loop.plan_rollback after restart.
Tool wrappers enrich the same call record rather than adding duplicate mutations.
"""
from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

from glasses3d.g3d_host import blender
from .journal import BlenderSession, WorkerFailed
from .loop import SceneHooks, image_key
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
        out = super().restore_checkpoint(cid, call_id=call_id)
        self.owner.record_call(call_id, "restore_checkpoint", before, (self.head or {}).get("id"), out)
        return out


class JobScene(SceneHooks):
    def __init__(self, root, job=None, selftest=False):
        self.root = Path(root).resolve()
        job_path = self.root / "job.json"
        self.job = job if job is not None else json.loads(job_path.read_text(encoding="utf-8"))
        self.lock = threading.RLock()
        self._state_lock = threading.RLock()
        self.state_path = self.root / "work" / "job_scene" / "state.json"
        state = json.loads(self.state_path.read_text(encoding="utf-8")) if self.state_path.is_file() else {}
        if state and state.get("schema") != STATE_SCHEMA:
            raise ValueError("unsupported job scene state schema")
        self.events = list(state.get("events", []))
        self.images = list(state.get("images", []))
        self.finish = state.get("finish")
        self.last_ar = state.get("last_ar")
        self.best_checkpoint = state.get("best_checkpoint")
        self._unseen = set(state.get("unseen", []))
        self._review_log = None
        self.seed_path = self.root / "inputs" / "seed.blend"
        self.s = _Session(self, seed_blend=self.seed_path, selftest=selftest,
                          calls_dir=self.root / "work" / "session_calls")
        self.seeing = Seeing(self.s)

    def save(self):
        with self._state_lock:
            write_json_atomic(self.state_path, {"schema": STATE_SCHEMA, "events": self.events,
                                               "images": self.images, "finish": self.finish,
                                               "last_ar": self.last_ar, "best_checkpoint": self.best_checkpoint,
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
            return self.s.start()

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
            if call_id in self._unseen and self.finish and self.finish.get("call_id") == call_id:
                self.finish = None
            self.save()
            if self._review_log is not None:
                self._review_log.record_journal(call_id, event)

    def bind_review_log(self, log):
        self._review_log = log

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
        barrier = max((order.get(e.get("head"), -1) for e in self.events if e.get("kind") == "rollback"), default=-1)
        calls = {}
        for n, row in enumerate(checkpoints):
            if n > barrier and row.get("kind") in ("save", "restore", "drift") and row.get("call_id"):
                calls.setdefault(row["call_id"], []).append(row)
        changed = False
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
            self.save()

    def restore(self, checkpoint_id, *, reason):
        with self.lock:
            before = (self.s.head or {}).get("id")
            out = self.s.restore_checkpoint(checkpoint_id, call_id=self.s.new_call_id("loop-rollback"))
            if out.get("status") != "ok":
                raise WorkerFailed(f"rollback to {checkpoint_id} failed: {out.get('message', out.get('status'))}",
                                   self.s.head)
            with self._state_lock:
                self.events.append({"kind": "rollback", "checkpoint": checkpoint_id,
                                    "checkpoint_before": before, "head": self.s.head["id"], "reason": reason})
                self.finish = None
                self.save()
            return {"checkpoint": out["checkpoint"], "restored_from": checkpoint_id}

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

    def context_stubs(self, image_sha256s):
        with self._state_lock:
            records = [dict(i) for i in self.images]
            best = self.best_checkpoint
        # Only superseded generated comparisons/renders are eligible. Photos and
        # current pick sheets remain intact; M2 can expand this policy by metadata.
        eligible = {"compare_to_photos", "render_views", "arview_capture"}
        latest = {(r["kind"], r["view"]): r["call_id"] for r in records if r["kind"] in eligible}
        keep = {r["sha256"] for r in records if r["kind"] not in eligible
                or r["checkpoint"] == best or latest.get((r["kind"], r["view"])) == r["call_id"]}
        by_sha = {r["sha256"]: r for r in records}
        return {digest: (f"[superseded image: call {by_sha[digest]['call_id']}, "
                         f"checkpoint {by_sha[digest]['checkpoint']}, view {by_sha[digest]['view']}]")
                for digest in image_sha256s if digest in by_sha and digest not in keep}

    def context_image_stubs(self, images):
        """Keep source photos, latest generated evidence per view, current picks and the chosen best checkpoint.

        Match only images actually present in this request. A completed but unseen tool result cannot supersede
        evidence the model has received. Unknown or ambiguous older metadata is retained conservatively.
        """
        with self._state_lock:
            records = [dict(item) for item in self.images]
            best = self.best_checkpoint
        roles = {"compare_to_photos": "comparison", "render_views": "render", "arview_capture": "ar",
                 "fit_cameras": "fit", "pick_surface_point": "pick", "photo_read": "pick"}
        generated = {"comparison", "render", "ar", "fit", "pick", "detail"}

        def role(rec):
            return rec.get("context_role", roles.get(rec.get("kind")))

        def views(rec):
            return rec.get("views") or [rec.get("view", "sheet")]

        by_pair = {}
        for index, rec in enumerate(records):
            by_pair.setdefault((rec["call_id"], rec["sha256"]), []).append(index)
        matched = {}
        for image in images:
            candidates = by_pair.get((image["call_id"], image["sha256"]), [])
            exact = [index for index in candidates if records[index].get("output_index") == image["part_index"]]
            if len(exact) == 1:
                matched[image_key(image)] = exact[0]
            elif len(candidates) == 1 and records[candidates[0]].get("output_index") is None:
                matched[image_key(image)] = candidates[0]
        latest = {}
        for index in sorted(set(matched.values())):
            rec = records[index]
            if role(rec) in generated and rec.get("current") is not False:
                for view in views(rec):
                    latest[role(rec), view] = index
        stubs = {}
        for key, index in matched.items():
            rec = records[index]
            kind = role(rec)
            if kind not in generated or (best is not None and rec.get("checkpoint") == best):
                continue
            if rec.get("current") is not False and any(latest.get((kind, view)) == index for view in views(rec)):
                continue
            path = f"; path {rec['path']}" if rec.get("path") else ""
            stubs[key] = (f"[superseded {kind} image: call {rec['call_id']}, "
                          f"checkpoint {rec.get('checkpoint')}, view {', '.join(map(str, views(rec)))}{path}]")
        return stubs

    def mark_unseen(self, call_id):
        with self._state_lock:
            self._unseen.add(call_id)
            if self.finish and self.finish.get("call_id") == call_id:
                self.finish = None
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
