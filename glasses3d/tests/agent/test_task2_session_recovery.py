"""Session recovery failures stop with evidence; only an incomplete JSONL tail is recoverable."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from glasses3d.agent import jsonl, procs
from glasses3d.agent.journal import BlenderSession, CheckpointIndex, CheckpointMismatch, WorkerFailed
from glasses3d.agent.jsonl import append_jsonl, read_jsonl
from glasses3d.agent.worker_client import WorkerDied, WorkerError


class RecoveryWorker:
    pid = 117

    def __init__(self, head, *, result=None, call_error=None, save_error=None, restore_error=None,
                 fingerprint_error=None):
        self.fp = head["fingerprint"]
        self.result = result if result is not None else {
            "status": "error", "traceback": "the original failed edit", "fingerprint_before": self.fp}
        self.call_error = call_error
        self.save_error = save_error
        self.restore_error = restore_error
        self.fingerprint_error = fingerprint_error
        self.calls = []

    def call(self, method, params=None, **kwargs):
        self.calls.append(method)
        if method == "fingerprint":
            if self.fingerprint_error:
                raise self.fingerprint_error
            return {"fingerprint": self.fp}
        if method == "checkpoint_restore":
            if self.restore_error:
                raise self.restore_error
            assert hashlib.sha256(Path(params["path"]).read_bytes()).hexdigest() == params["expect_sha256"]
            self.fp = params["expect_fingerprint"]
            return {"matches": True, "fingerprint": self.fp}
        if method == "checkpoint_save":
            raise self.save_error or AssertionError("test did not expect a checkpoint save")
        self.fp = "changed"
        if self.call_error:
            raise self.call_error
        return dict(self.result)


def make_session(root, **worker_options):
    session = BlenderSession(root, calls_dir=root / "calls")
    session.ck_dir.mkdir(parents=True)
    path = session.ck_dir / "c0000.blend"
    payload = b"original checkpoint"
    path.write_bytes(payload)
    session.index.append({"id": "c0000", "kind": "start", "parent": None, "file": path.name,
                          "fingerprint": "original", "sha256": hashlib.sha256(payload).hexdigest(),
                          "bytes": len(payload)})
    session.worker = RecoveryWorker(session.head, **worker_options)
    return session


def call_mutating(session):
    return session.call_mutating("execute_blender_code", "exec", {"code": "result = 3"},
                                 deadline_s=30, call_id="edit")


def journal_result(session):
    directory, = session.calls_dir.iterdir()
    result = json.loads((directory / "result.json").read_text(encoding="utf-8"))
    journal = json.loads((directory / "journal.json").read_text(encoding="utf-8"))
    assert result["status"] == journal["status"] == "failed_worker"
    assert journal["call_id"] == result["call_id"] == "edit"
    assert journal["checkpoint_before"] == "c0000"
    return result


def test_mutating_rollback_worker_error_is_journaled_stop(tmp_path):
    error = WorkerError("RuntimeError", "cannot open checkpoint (file locked)", "restore traceback")
    session = make_session(tmp_path, restore_error=error)
    with pytest.raises(WorkerFailed, match="checkpoint_restore.*file locked") as caught:
        call_mutating(session)
    assert caught.value.__cause__ is error
    result = journal_result(session)
    assert result["worker_result"] == session.worker.result
    assert result["failed_status"] == "error"
    assert result["restore_verified"] is False and result["restored_to"] is None
    assert result["recovery_error"]["traceback"] == "restore traceback"
    assert session.head["fingerprint"] == "original" and session.worker.fp == "changed"


@pytest.mark.parametrize("status", ["ok", "drift"])
@pytest.mark.parametrize("error", [WorkerError("RuntimeError", "cannot save checkpoint"),
                                   OSError("disk unavailable")])
def test_checkpoint_save_failure_preserves_result_restores_and_stops(tmp_path, status, error):
    worker_result = {"status": status, "changed": True, "fingerprint_before": "original",
                     "fingerprint_after": "changed", "result": {"author_result": 3}}
    session = make_session(tmp_path, result=worker_result, save_error=error)
    with pytest.raises(WorkerFailed, match="checkpoint_save.*restored verified checkpoint c0000") as caught:
        call_mutating(session)
    assert caught.value.__cause__ is error
    result = journal_result(session)
    assert result["worker_result"] == worker_result and result["failed_status"] == status
    assert result["restored_to"] == "c0000" and result["restore_verified"]
    assert session.worker.fp == session.head["fingerprint"] == "original"
    assert len(session.index.checkpoints()) == 1


def test_checkpoint_save_failure_preserves_failed_cleanup_too(tmp_path):
    save_error = WorkerError("RuntimeError", "save denied")
    session = make_session(tmp_path, result={"status": "ok", "changed": True}, save_error=save_error,
                           restore_error=WorkerError("RuntimeError", "restore denied"))
    with pytest.raises(WorkerFailed, match="save denied.*restore denied") as caught:
        call_mutating(session)
    assert caught.value.__cause__ is save_error
    result = journal_result(session)
    assert result["recovery_error"]["message"] == "save denied"
    assert "restore denied" in result["restore_error"] and not result["restore_verified"]


@pytest.mark.parametrize("failure", ["restore", "fingerprint"])
def test_failed_read_error_recovery_is_journaled_stop(tmp_path, failure):
    error = WorkerError("RuntimeError", "recovery unavailable")
    session = make_session(tmp_path, call_error=WorkerError("DomainError", "the original read failed"),
                           **{failure + "_error": error})
    with pytest.raises(WorkerFailed, match="read_only_recovery.*recovery unavailable") as caught:
        session.call_read("scene_summary", "scene_summary", {}, deadline_s=30, call_id="edit")
    assert caught.value.__cause__ is error
    result = journal_result(session)
    assert result["worker_result"]["message"] == "DomainError: the original read failed"
    assert result["failed_status"] == "error" and not result["restore_verified"]


def test_changed_read_restore_failure_is_journaled_stop(tmp_path):
    error = WorkerError("RuntimeError", "cannot restore changed read")
    reply = {"unchanged": False, "fingerprint_before": "original", "fingerprint_after": "changed",
             "bounds": {"changed-object": [1, 2, 3]}}
    session = make_session(tmp_path, result=reply, restore_error=error)
    with pytest.raises(WorkerFailed, match="read_only_restore.*cannot restore changed read") as caught:
        session.call_read("scene_summary", "scene_summary", {}, deadline_s=30, call_id="edit")
    assert caught.value.__cause__ is error
    assert journal_result(session)["worker_result"] == reply


def test_rollback_fingerprint_mismatch_is_journaled_before_fatal_stop(tmp_path, monkeypatch):
    session = make_session(tmp_path)
    error = CheckpointMismatch("restored fingerprint does not match")

    def restore(_head):
        raise error

    monkeypatch.setattr(session, "_restore", restore)
    with pytest.raises(WorkerFailed, match="fingerprint does not match") as caught:
        call_mutating(session)
    assert caught.value.__cause__ is error
    assert journal_result(session)["worker_result"]["traceback"] == "the original failed edit"


def test_restart_failure_during_rollback_retains_original_failed_call(tmp_path, monkeypatch):
    session = make_session(tmp_path, restore_error=WorkerDied("eof", "rollback response lost"))
    fatal = WorkerFailed("worker cannot restart", session.head)

    def restart(*args):
        raise fatal

    monkeypatch.setattr(session, "_after_death", restart)
    with pytest.raises(WorkerFailed, match="checkpoint_restore_restart.*worker cannot restart") as caught:
        call_mutating(session)
    assert caught.value.__cause__ is fatal
    assert journal_result(session)["worker_result"]["traceback"] == "the original failed edit"


def test_journal_failure_cannot_turn_recovery_error_into_recoverable_tool_reply(tmp_path, monkeypatch):
    error = WorkerError("RuntimeError", "restore denied")
    session = make_session(tmp_path, restore_error=error)

    def journal(*args):
        raise OSError("journal disk unavailable")

    monkeypatch.setattr(session, "_journal", journal)
    with pytest.raises(WorkerFailed, match="restore denied.*could not journal.*disk unavailable") as caught:
        call_mutating(session)
    assert caught.value.__cause__ is error


def test_internal_restore_metadata_is_durable_before_call_event(tmp_path):
    session = make_session(tmp_path)
    path = session.ck_dir / "c0001.blend"
    path.write_bytes(b"refused edit")
    session.index.append({"id": "c0001", "kind": "save", "parent": "c0000", "file": path.name,
                          "call_id": "edit", "fingerprint": "changed", "bytes": path.stat().st_size,
                          "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    session.worker.fp = "changed"
    result = session.restore_checkpoint("c0000", call_id="edit", recovery_of="edit")
    indexed = CheckpointIndex(session.ck_dir).head
    assert result["recovery_of"] == indexed["recovery_of"] == "edit"
    assert indexed["restored_from"] == "c0000" and indexed["fingerprint"] == "original"
    assert not session.calls_dir.exists()  # Kill window before any JobScene call/barrier event.


def test_start_reports_ignored_checkpoint_tail_for_run_receipt(tmp_path, monkeypatch):
    session = make_session(tmp_path)
    with session.index.path.open("ab") as stream:
        stream.write(b'{"id": "c0001",')
    resumed = BlenderSession(tmp_path)
    worker = session.worker
    worker.hello = {"fingerprint": "original"}
    worker.ready_s = 0
    worker.record = lambda: {"pid": worker.pid}
    monkeypatch.setattr(resumed, "_spawn", lambda _path: worker)
    started = resumed.start()
    assert started["head"]["id"] == "c0000"
    recovery, = started["checkpoint_index_recoveries"]
    assert recovery["kind"] == "torn_jsonl_tail" and not recovery["repaired"]


@pytest.mark.parametrize("tail", [b'{"id": "c0001",', b'{"label":"\xe2\x80'])
def test_torn_index_is_reported_then_repaired_before_append(tmp_path, tail):
    directory = tmp_path / "checkpoints"
    directory.mkdir()
    path = directory / "index.jsonl"
    first = {"id": "c0000", "kind": "start", "fingerprint": "original", "parent": None}
    prefix = json.dumps(first).encode() + b"\n"
    path.write_bytes(prefix + tail)
    index = CheckpointIndex(directory)
    assert index.head == first and len(index.recoveries) == 1
    assert path.read_bytes() == prefix + tail  # Opening alone is read-only.
    session = BlenderSession(tmp_path)
    assert session.defects[0]["kind"] == "torn_jsonl_tail"
    assert session.defects[0]["offset"] == len(prefix)
    second = {"id": "c0001", "kind": "save", "fingerprint": "next", "parent": "c0000"}
    index.append(second)
    assert read_jsonl(path).rows == [first, second]
    assert path.read_bytes().startswith(prefix)
    archive, = directory.glob("index.jsonl.torn-*.bin")
    assert archive.read_bytes() == tail
    assert index.recoveries[-1]["repaired"]
    assert CheckpointIndex(directory).head == second


@pytest.mark.parametrize("payload", [b'{"id": "c0000"}\n{bad}\n',
                                     b'{"id": "c0000"}\n{bad}\n{"id":"c0001"}',
                                     b'{"id": "c0000"}\n\xff\n'])
def test_complete_jsonl_corruption_is_never_treated_as_a_torn_tail(tmp_path, payload):
    path = tmp_path / "events.jsonl"
    path.write_bytes(payload)
    with pytest.raises((json.JSONDecodeError, UnicodeDecodeError)):
        read_jsonl(path)
    if payload.endswith(b"\n"):
        append_jsonl(path, {"next": True})
        assert path.read_bytes().startswith(payload)
        with pytest.raises((json.JSONDecodeError, UnicodeDecodeError)):
            read_jsonl(path)
    else:
        with pytest.raises((json.JSONDecodeError, UnicodeDecodeError)):
            append_jsonl(path, {"next": True})
        assert path.read_bytes() == payload
    assert not list(tmp_path.glob("*.bin"))


def test_jsonl_preserves_unicode_separators_and_missing_final_lf(tmp_path):
    path = tmp_path / "events.jsonl"
    first = {"message": "before\u2028middle\u2029after\u0085end"}
    raw = json.dumps(first, ensure_ascii=False).encode("utf-8")
    path.write_bytes(raw)
    records = read_jsonl(path)
    assert records.rows == [first] and records.needs_newline and records.recovery is None
    append_jsonl(path, {"next": True})
    assert path.read_bytes().startswith(raw + b"\n")
    assert read_jsonl(path).rows == [first, {"next": True}]


def test_invalid_new_jsonl_row_never_repairs_or_changes_prior_bytes(tmp_path):
    path = tmp_path / "events.jsonl"
    payload = b'{"valid":true}\n{"torn":'
    path.write_bytes(payload)
    with pytest.raises(ValueError):
        append_jsonl(path, {"invalid": float("nan")})
    assert path.read_bytes() == payload and not list(tmp_path.glob("*.bin"))


def test_failed_tail_archive_does_not_truncate_log_or_publish_partial_evidence(tmp_path, monkeypatch):
    path = tmp_path / "events.jsonl"
    payload = b'{"valid":true}\n{"torn":'
    path.write_bytes(payload)

    def fail_sync(fd):
        raise OSError("archive flush failed")

    monkeypatch.setattr(jsonl.os, "fsync", fail_sync)
    with pytest.raises(OSError, match="archive flush failed"):
        append_jsonl(path, {"next": True})
    assert path.read_bytes() == payload
    assert list(tmp_path.iterdir()) == [path]


def test_atomic_json_syncs_complete_temporary_before_replacement(tmp_path, monkeypatch):
    target = tmp_path / "atomic.json"
    events = []
    fsync = procs.os.fsync
    replace = procs.replace_with_retry

    def sync(fd):
        assert procs.os.fstat(fd).st_size > 0
        events.append("fsync")
        return fsync(fd)

    def replace_checked(source, destination):
        assert events == ["fsync"]
        assert json.loads(Path(source).read_text(encoding="utf-8")) == {"fresh": "saved"}
        events.append("replace")
        return replace(source, destination)

    monkeypatch.setattr(procs.os, "fsync", sync)
    monkeypatch.setattr(procs, "replace_with_retry", replace_checked)
    procs.write_json_atomic(target, {"fresh": "saved"})
    assert events == ["fsync", "replace"]
    assert json.loads(target.read_text(encoding="utf-8")) == {"fresh": "saved"}
    assert not list(tmp_path.glob(".g3d-*.tmp"))


def test_atomic_json_keeps_prior_file_when_fsync_fails(tmp_path, monkeypatch):
    target = tmp_path / "atomic.json"
    previous = b'{"original":true}'
    target.write_bytes(previous)

    def fail_sync(fd):
        raise OSError("flush failed")

    monkeypatch.setattr(procs.os, "fsync", fail_sync)
    with pytest.raises(OSError, match="flush failed"):
        procs.write_json_atomic(target, {"new": True})
    assert target.read_bytes() == previous and not list(tmp_path.glob(".g3d-*.tmp"))
