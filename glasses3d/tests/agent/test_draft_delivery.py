"""Host-only unfinished-run delivery: actual GLB checks, fake owned worker/AR receipts, no API calls."""
from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from glasses3d.agent import delivery as D, job_run, worker_client
from glasses3d.agent.budget import JobLedger, JobLedgerError
from glasses3d.agent.journal import CheckpointIndex
from glasses3d.agent.tools import arview
from glasses3d.tests.agent.test_delivery import make_finished, digest


def draft_fixture(root, monkeypatch, *, best=True, status="budget_limit"):
    """Keep real checks and snapshot publication; replace only Blender execution and the local renderer."""
    finished, result = make_finished(root)
    model = Path(finished.finish["export"]["path"]).read_bytes()
    index = CheckpointIndex(root / "checkpoints")
    for n in range(2):
        path = index.dir / f"c{n:04d}.blend"
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(f"test-checkpoint-{n}".encode())
        index.append({"id": path.stem, "kind": "save", "file": path.name, "sha256": digest(path),
                      "fingerprint": str(n) * 64, "parent": None if n == 0 else "c0000"})
    scene = SimpleNamespace(s=SimpleNamespace(head=index.head, worker=None, selftest=True, blender=None),
                            best_checkpoint="c0000" if best else None, finish=None, last_ar=None)
    result.update(status=status, exit_checkpoint={"id": index.head["id"], "sha256": index.head["sha256"],
                                                   "verified": True}, final_output=None)
    state = root / "work/job_scene/state.json"
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps({"best_checkpoint": scene.best_checkpoint, "events": ["keep history"]}))
    created = []

    class Worker:
        def __init__(self, worker_root, *, start_blend, **kwargs):
            assert worker_root == root
            self.blend = Path(start_blend)
            assert self.blend.parent != index.dir
            self.closed = False
            self.started = False
            created.append(self)

        def start(self):
            with pytest.raises(JobLedgerError, match="lock"):
                with JobLedger(root):
                    pass
            self.started = True
            self.fp = "0" * 64 if self.blend.read_bytes().endswith(b"0") else "1" * 64
            self.hello = {"fingerprint": self.fp}
            return self

        def call(self, method, params, **kwargs):
            assert method == "export_glb"
            path = Path(params["path"])
            path.write_bytes(model)
            return {"status": "exported", "path": str(path), "sha256": digest(path), "bytes": len(model),
                    "receipt": {"object_count": 7, "triangle_count": 300},
                    "fingerprint_before": self.fp, "fingerprint_after": self.fp, "unchanged": True}

        def close(self):
            self.closed = True

    monkeypatch.setattr(worker_client, "WorkerClient", Worker)
    captures = []

    def capture(capture_root, export, call_id):
        assert capture_root == root and export["sha256"] == digest(export["path"])
        assert created[-1].closed  # export worker is always gone before Chromium starts
        captures.append(copy.deepcopy(export))
        return {**copy.deepcopy(finished.last_ar), "checkpoint": export["checkpoint"]}

    monkeypatch.setattr(arview, "capture", capture)
    return scene, result, created, captures


def preserved(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for folder in ("checkpoints", "work/job_scene")
            for p in (root / folder).rglob("*") if p.is_file()}


@pytest.mark.parametrize("status", sorted(D.DRAFT_STATUSES))
@pytest.mark.parametrize("best", [True, False])
def test_each_unfinished_status_exports_best_else_head_without_changing_resume(tmp_path, monkeypatch, status, best):
    scene, result, workers, captures = draft_fixture(tmp_path, monkeypatch, status=status, best=best)
    before = preserved(tmp_path)
    out = D.deliver_draft(tmp_path, scene, result)
    assert out["status"] == "delivered" and out["finished"] is False and out["run_status"] == status
    loaded = D.load_delivery(tmp_path, out["id"])
    report = loaded["report_data"]
    assert report["finished"] is report["accepted_finish"] is False
    assert report["checkpoint_selection"] == ("best" if best else "head")
    assert report["checkpoint"] == ("c0000" if best else "c0001")
    assert report["source_checkpoint"]["verified"] and report["exit_checkpoint"]["id"] == "c0001"
    assert report["failed_checks"] == [] and report["summary"] is None
    assert report["validate_spec"]["ok"] and report["seating"]["ok"] and report["print_check"]["ok"]
    assert len(captures) == len(workers) == 1 and workers[0].closed
    assert scene.s.worker is None and scene.s.head["id"] == "c0001" and scene.finish is None
    assert preserved(tmp_path) == before
    assert digest(out["snapshot_model"]) == out["glb_sha256"]


@pytest.mark.parametrize("status", sorted(D.DRAFT_STATUSES))
def test_each_status_records_no_usable_checkpoint_when_empty(tmp_path, status):
    scene = SimpleNamespace(s=SimpleNamespace(head=None), best_checkpoint=None)
    result = {"status": status, "run_id": "empty", "exit_checkpoint": None}
    out = D.deliver_draft(tmp_path, scene, result)
    assert out["status"] == "no_usable_checkpoint" and out["finished"] is False
    assert "no checkpoint" in out["reason"] and out["run_status"] == status
    assert not (tmp_path / "deliveries").exists()


@pytest.mark.parametrize("failure", ["missing_best", "corrupt_best", "advanced_head", "changed_head_sha",
                                     "worker_start", "empty_export", "drift"])
def test_unusable_source_never_falls_back_or_leaks_worker(tmp_path, monkeypatch, failure):
    scene, result, workers, _ = draft_fixture(tmp_path, monkeypatch)
    if failure == "missing_best":
        scene.best_checkpoint = "c9999"
    elif failure == "corrupt_best":
        (tmp_path / "checkpoints/c0000.blend").write_bytes(b"changed")
    elif failure == "advanced_head":
        result["exit_checkpoint"]["id"] = "older-run"
    elif failure == "changed_head_sha":
        result["exit_checkpoint"]["sha256"] = "a" * 64
    elif failure == "worker_start":
        monkeypatch.setattr(worker_client.WorkerClient, "start", lambda self: (_ for _ in ()).throw(OSError("startup")))
    else:
        original = worker_client.WorkerClient.call

        def call(self, *args, **kwargs):
            out = original(self, *args, **kwargs)
            if failure == "empty_export":
                out["receipt"]["triangle_count"] = 0
            else:
                out["fingerprint_after"] = "9" * 64
            return out

        monkeypatch.setattr(worker_client.WorkerClient, "call", call)
    before = preserved(tmp_path)
    out = D.deliver_draft(tmp_path, scene, result)
    assert out["status"] == "no_usable_checkpoint" and out["reason"]
    assert all(w.closed for w in workers)
    assert preserved(tmp_path) == before and not (tmp_path / "deliveries").exists()


def test_failed_checks_are_kept_and_never_certified(tmp_path, monkeypatch):
    from glasses3d.g3d_host import spec, seat_check
    from glasses3d.agent import print_freshness
    scene, result, _, _ = draft_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(spec, "validate_spec", lambda path: {"ok": False, "sha256": digest(path), "errors": ["bad axis"]})
    monkeypatch.setattr(seat_check, "check_glb", lambda path: {"ok": False, "violations": ["gap"]})
    monkeypatch.setattr(print_freshness, "check_current_print", lambda *a: (_ for _ in ()).throw(ValueError("bad print")))
    monkeypatch.setattr(arview, "capture", lambda *a: {"status": "infrastructure_failed", "code": "E_NODE"})
    out = D.deliver_draft(tmp_path, scene, result)
    report = D.load_delivery(tmp_path, out["id"])["report_data"]
    assert report["failed_checks"] == ["validate_spec", "seating", "print_check", "arview"]
    assert report["validate_spec"]["errors"] == ["bad axis"]
    assert report["seating"]["violations"] == ["gap"]
    assert report["print_check"]["ok"] is False and "bad print" in report["print_check"]["error"]
    assert report["arview"]["code"] == "E_NODE" and out["contact_sheets"] == []
    assert out["contact_sheet"] is None and report["accepted_finish"] is False


def test_spec_failed_capture_keeps_actual_hashed_images(tmp_path, monkeypatch):
    scene, result, _, _ = draft_fixture(tmp_path, monkeypatch)
    original = arview.capture

    def capture(*args):
        ar = original(*args)
        path = Path(ar["report"])
        report = json.loads(path.read_text())
        report.update(exit_code=3, status="spec_failed")
        path.write_text(json.dumps(report))
        return {**ar, "status": "spec_failed", "exit_code": 3, "report_sha256": digest(path)}

    monkeypatch.setattr(arview, "capture", capture)
    out = D.deliver_draft(tmp_path, scene, result)
    report = D.load_delivery(tmp_path)["report_data"]
    assert report["failed_checks"] == ["arview"] and report["arview"]["exit_code"] == 3
    assert len(out["contact_sheets"]) == 1 and Path(out["contact_sheet"]).is_file()


@pytest.mark.parametrize("final", [None, "Final assistant summary."])
def test_last_summary_uses_only_assistant_output_text(tmp_path, monkeypatch, final):
    scene, result, _, _ = draft_fixture(tmp_path, monkeypatch)
    transport = tmp_path / "runs/offline/transport"
    transport.mkdir(parents=True)
    (transport / "0001-response.json").write_text(json.dumps({"output": [
        {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Last public summary."}]}]}))
    (transport / "0002-response.json").write_text(json.dumps({"output": [
        {"type": "message", "role": "user", "content": [{"type": "output_text", "text": "Owner text"}]},
        {"type": "function_call_output", "output": "Tool text"},
        {"type": "reasoning", "encrypted_content": "not a summary"}]}))
    result["final_output"] = final
    out = D.deliver_draft(tmp_path, scene, result)
    assert D.load_delivery(tmp_path, out["id"])["report_data"]["summary"] == (final or "Last public summary.")


def test_old_draft_identity_and_bytes_survive_new_publication_and_tamper_is_refused(tmp_path, monkeypatch):
    scene, result, _, _ = draft_fixture(tmp_path, monkeypatch)
    first = D.deliver_draft(tmp_path, scene, result)
    old = {p: p.read_bytes() for p in Path(first["manifest"]).parent.rglob("*") if p.is_file()}
    scene.best_checkpoint = None
    result.update(run_id="second", status="turn_limit")
    second = D.deliver_draft(tmp_path, scene, result)
    assert second["id"] != first["id"] and D.load_delivery(tmp_path)["id"] == second["id"]
    assert D.load_delivery(tmp_path, first["id"])["report_data"]["checkpoint"] == "c0000"
    assert all(path.read_bytes() == data for path, data in old.items())
    Path(first["snapshot_report"]).write_bytes(b"tampered")
    with pytest.raises(D.DeliveryError, match="hash"):
        D.load_delivery(tmp_path, first["id"])


def test_job_lock_prevents_resume_race_before_any_worker_starts(tmp_path, monkeypatch):
    scene, result, workers, _ = draft_fixture(tmp_path, monkeypatch)
    with JobLedger(tmp_path):
        out = D.deliver_draft(tmp_path, scene, result)
    assert out["status"] == "no_usable_checkpoint" and "lock" in out["reason"]
    assert workers == []


@pytest.mark.parametrize("mutation", ["seating_missing_sha", "seating_wrong_sha", "fingerprint",
                                      "finish_true", "finish_missing", "finish_not_bool",
                                      "accepted_true", "status", "failed_checks", "source_verified"])
def test_rehashed_draft_cannot_lose_check_binding_or_masquerade_as_finished(tmp_path, monkeypatch, mutation):
    scene, result, _, _ = draft_fixture(tmp_path, monkeypatch, best=False)
    out = D.deliver_draft(tmp_path, scene, result)
    original = Path(out["manifest"]).parent
    files = {p.relative_to(original).as_posix(): p.read_bytes() for p in original.rglob("*")
             if p.is_file() and p.name != "manifest.json"}
    report = json.loads(files["report.json"])
    if mutation == "seating_missing_sha":
        report["seating"].pop("glb_sha256")
    elif mutation == "seating_wrong_sha":
        report["seating"]["glb_sha256"] = "0" * 64
    elif mutation == "fingerprint":
        report["fingerprint"] = report["source_checkpoint"]["fingerprint"] = "not-a-fingerprint"
    elif mutation == "finish_true":
        report["finished"] = True
    elif mutation == "finish_missing":
        report.pop("finished")
    elif mutation == "finish_not_bool":
        report["finished"] = 0
    elif mutation == "accepted_true":
        report["accepted_finish"] = True
    elif mutation == "status":
        report["run_status"] = "completed"
    elif mutation == "failed_checks":
        report["failed_checks"] = ["seating"]
    else:
        report["source_checkpoint"]["verified"] = False
    files["report.json"] = D._json_bytes(report)
    manifest = json.loads(Path(out["manifest"]).read_text())
    manifest["files"] = {name: {"sha256": D._sha(data), "bytes": len(data)} for name, data in sorted(files.items())}
    manifest["id"] = D._snapshot_id(manifest)
    modified = tmp_path / "deliveries" / manifest["id"]
    for name, data in files.items():
        D._write_file(modified / name, data)
    D._write_file(modified / "manifest.json", D._json_bytes(manifest))
    with pytest.raises(D.DeliveryError):
        D.load_delivery(tmp_path, manifest["id"])


@pytest.mark.parametrize("usable", [True, False])
def test_execute_run_persists_attempt_after_loop_closes_scene(tmp_path, monkeypatch, usable):
    scene, result, _, _ = draft_fixture(tmp_path, monkeypatch)
    closed = []
    monkeypatch.setattr(job_run, "prepared_settings", lambda *a: (job_run.settings_default(), "23"))
    monkeypatch.setattr(job_run, "prepare_run", lambda *a, **k: (object(), scene))

    async def run(*a, **k):
        assert k["scene"] is scene
        closed.append(True)
        if not usable:
            scene.best_checkpoint = "missing"
        return result

    monkeypatch.setattr(job_run, "run_astra", run)
    out = asyncio.run(job_run.execute_run(tmp_path, "offline", scene=scene))
    assert closed and out["delivery"]["status"] == ("delivered" if usable else "no_usable_checkpoint")
    assert json.loads((tmp_path / "runs/offline/delivery.json").read_text()) == out["delivery"]
