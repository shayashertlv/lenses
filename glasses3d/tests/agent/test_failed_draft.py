"""Failed runs retain an honest draft of the latest integrity-verified checkpoint, at $0."""
import asyncio
import copy
import json
from pathlib import Path

import pytest

from glasses3d.agent import delivery as D, job_run, worker_client
from glasses3d.g3d_host import spec, seat_check
from glasses3d.tests.agent.test_draft_delivery import draft_fixture, preserved
from glasses3d.tests.agent.test_delivery import digest


@pytest.mark.parametrize("best", [True, False])
@pytest.mark.parametrize("exit_verified", [True, False, None])
def test_failed_uses_latest_reverified_checkpoint_and_preserves_failure(tmp_path, monkeypatch, best, exit_verified):
    scene, result, workers, captures = draft_fixture(tmp_path, monkeypatch, status="failed", best=best)
    if exit_verified is None:
        result["exit_checkpoint"] = None  # teardown could not obtain a live-scene fingerprint
    else:
        result["exit_checkpoint"]["verified"] = exit_verified
    result["error"] = {"type": "CheckpointMismatch", "message": "read operation failed"}
    original_result, before = copy.deepcopy(result), preserved(tmp_path)
    out = D.deliver_draft(tmp_path, scene, result)
    report = D.load_delivery(tmp_path, out["id"])["report_data"]
    assert out["finished"] is False and out["run_status"] == "failed"
    assert report["checkpoint"] == "c0001" and report["checkpoint_selection"] == "last_verified"
    assert report["source_checkpoint"]["verified"] is True
    assert report["source_checkpoint"]["verification_attempts"] == [
        {"checkpoint": "c0001", "status": "verified", "sha256": scene.s.head["sha256"], "fingerprint": "1" * 64}]
    assert report["exit_checkpoint"] == (original_result["exit_checkpoint"] or {})
    assert report["accepted_finish"] is False and scene.finish is None
    assert len(captures) == len(workers) == 1 and workers[0].closed
    assert preserved(tmp_path) == before and result == original_result


@pytest.mark.parametrize("fault", ["file_hash", "missing_file", "fingerprint", "startup"])
def test_failed_falls_back_only_after_original_integrity_verification_fails(tmp_path, monkeypatch, fault):
    scene, result, workers, _ = draft_fixture(tmp_path, monkeypatch, status="failed")
    head_path = tmp_path / "checkpoints/c0001.blend"
    if fault == "file_hash":
        head_path.write_bytes(b"corrupt head")
    elif fault == "missing_file":
        head_path.unlink()
    else:
        original = worker_client.WorkerClient.start

        def start(self):
            original(self)
            if self.fp == "1" * 64:
                if fault == "startup":
                    raise OSError("cannot open this checkpoint")
                self.hello["fingerprint"] = "f" * 64
            return self

        monkeypatch.setattr(worker_client.WorkerClient, "start", start)
    before = preserved(tmp_path)
    out = D.deliver_draft(tmp_path, scene, result)
    report = D.load_delivery(tmp_path, out["id"])["report_data"]
    assert report["checkpoint"] == "c0000" and report["checkpoint_selection"] == "last_verified"
    attempts = report["source_checkpoint"]["verification_attempts"]
    assert [(a["checkpoint"], a["status"]) for a in attempts] == [
        ("c0001", "verification_failed"), ("c0000", "verified")]
    assert attempts[0]["reason"] and attempts[1]["fingerprint"] == "0" * 64
    assert all(worker.closed for worker in workers)
    assert preserved(tmp_path) == before and scene.s.head["id"] == "c0001"


@pytest.mark.parametrize("fault", ["file_hash", "fingerprint"])
def test_no_verifiable_checkpoint_records_all_attempts_and_never_restamps(tmp_path, monkeypatch, fault):
    scene, result, workers, captures = draft_fixture(tmp_path, monkeypatch, status="failed")
    if fault == "file_hash":
        for path in (tmp_path / "checkpoints").glob("*.blend"):
            path.write_bytes(b"corrupt")
    else:
        original = worker_client.WorkerClient.start

        def start(self):
            original(self)
            self.hello["fingerprint"] = "f" * 64
            return self

        monkeypatch.setattr(worker_client.WorkerClient, "start", start)
    before = preserved(tmp_path)
    out = D.deliver_draft(tmp_path, scene, result)
    assert out["status"] == "no_usable_checkpoint" and out["run_status"] == "failed"
    assert [a["checkpoint"] for a in out["checkpoint_attempts"]] == ["c0001", "c0000"]
    assert all(a["status"] == "verification_failed" and a["reason"] for a in out["checkpoint_attempts"])
    assert all(worker.closed for worker in workers) and captures == []
    assert preserved(tmp_path) == before and not (tmp_path / "deliveries").exists()


@pytest.mark.parametrize("fault", ["refused", "empty", "drift", "wrong_path", "changed_source"])
def test_latest_verified_export_failure_cannot_select_an_older_model(tmp_path, monkeypatch, fault):
    scene, result, workers, captures = draft_fixture(tmp_path, monkeypatch, status="failed")
    original = worker_client.WorkerClient.call

    def call(self, *args, **kwargs):
        out = original(self, *args, **kwargs)
        if fault == "refused":
            out.update(status="refused", message="unsupported component role hardware")
        elif fault == "empty":
            out["receipt"]["object_count"] = 0
        elif fault == "drift":
            out["fingerprint_after"] = "f" * 64
        elif fault == "wrong_path":
            out["path"] = str(tmp_path / "other.glb")
        else:
            (tmp_path / "checkpoints/c0001.blend").write_bytes(b"external corruption")
        return out

    monkeypatch.setattr(worker_client.WorkerClient, "call", call)
    out = D.deliver_draft(tmp_path, scene, result)
    assert out["status"] == "no_usable_checkpoint" and out["reason"]
    assert [a["checkpoint"] for a in out["checkpoint_attempts"]] == ["c0001"]
    assert out["checkpoint_attempts"][0]["status"] == "verified"
    assert len(workers) == 1 and workers[0].closed and captures == []
    assert not (tmp_path / "deliveries").exists()


def test_latest_verified_quality_failure_is_delivered_honestly_without_fallback(tmp_path, monkeypatch):
    scene, result, workers, _ = draft_fixture(tmp_path, monkeypatch, status="failed")
    monkeypatch.setattr(spec, "validate_spec", lambda path: {"ok": False, "sha256": digest(path), "errors": ["wrong scale"]})
    monkeypatch.setattr(seat_check, "check_glb", lambda path: {"ok": False, "violations": ["gap"]})
    before = preserved(tmp_path)
    out = D.deliver_draft(tmp_path, scene, result)
    report = D.load_delivery(tmp_path, out["id"])["report_data"]
    assert report["checkpoint"] == "c0001" and len(workers) == 1
    assert report["failed_checks"] == ["validate_spec", "seating"]
    assert report["validate_spec"]["errors"] == ["wrong scale"] and report["seating"]["violations"] == ["gap"]
    assert report["accepted_finish"] is False and preserved(tmp_path) == before


@pytest.mark.parametrize("fault", ["active_worker", "advanced_head", "changed_head_sha"])
def test_failed_draft_refuses_stale_or_still_running_source(tmp_path, monkeypatch, fault):
    scene, result, workers, _ = draft_fixture(tmp_path, monkeypatch, status="failed")
    if fault == "active_worker":
        scene.s.worker = object()
    elif fault == "advanced_head":
        result["exit_checkpoint"]["id"] = "c0000"
    else:
        result["exit_checkpoint"]["sha256"] = "f" * 64
    out = D.deliver_draft(tmp_path, scene, result)
    assert out["status"] == "no_usable_checkpoint" and workers == []


def test_execute_failed_result_persists_draft_without_reclassifying_run(tmp_path, monkeypatch):
    scene, result, _, _ = draft_fixture(tmp_path, monkeypatch, status="failed")
    monkeypatch.setattr(job_run, "prepared_settings", lambda *a: (job_run.settings_default(), "23"))
    monkeypatch.setattr(job_run, "prepare_run", lambda *a, **k: (object(), scene))

    async def run(*a, **k):
        return result

    monkeypatch.setattr(job_run, "run_astra", run)
    out = asyncio.run(job_run.execute_run(tmp_path, "offline", scene=scene))
    assert out["result"] == result and out["result"]["status"] == "failed"
    assert out["delivery"]["status"] == "delivered" and out["delivery"]["finished"] is False
    assert json.loads((tmp_path / "runs/offline/delivery.json").read_text()) == out["delivery"]


@pytest.mark.parametrize("tamper", ["missing_attempts", "last_sha", "hidden_failure", "best_selection"])
def test_failed_draft_loader_requires_source_selection_evidence(tmp_path, monkeypatch, tamper):
    scene, result, _, _ = draft_fixture(tmp_path, monkeypatch, status="failed")
    out = D.deliver_draft(tmp_path, scene, result)
    original = Path(out["manifest"]).parent
    files = {p.relative_to(original).as_posix(): p.read_bytes() for p in original.rglob("*")
             if p.is_file() and p.name != "manifest.json"}
    report = json.loads(files["report.json"])
    source = report["source_checkpoint"]
    if tamper == "missing_attempts":
        source.pop("verification_attempts")
    elif tamper == "last_sha":
        source["verification_attempts"][-1]["sha256"] = "f" * 64
    elif tamper == "hidden_failure":
        source["verification_attempts"].insert(0, {"checkpoint": "c0002", "status": "verified"})
    else:
        report["checkpoint_selection"] = "best"
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
