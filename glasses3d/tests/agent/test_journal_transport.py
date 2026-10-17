"""Fatal RPC result failures use the real journal and SDK loop, with no Blender or network."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from glasses3d.agent import loop as L, replay as R, tools as T
from glasses3d.agent.journal import BlenderSession, WorkerFailed
from glasses3d.agent.worker_client import WorkerDied, WorkerError
from glasses3d.tests.agent.loop_support import FakeScene
from glasses3d.tests.agent.test_job_tools import scene
from glasses3d.tests.agent.test_loop import (RUN, assert_money_invariants,
                                           no_production_clients, run_once)


class ReplyWorker:
    pid = 12345

    def __init__(self, head, error):
        self.fingerprint = head["fingerprint"]
        self.error = error
        self.calls = []
        self.restore_error = None

    def call(self, method, params=None, **kwargs):
        self.calls.append((method, copy.deepcopy(params)))
        if method == "fingerprint":
            return {"fingerprint": self.fingerprint}
        if method == "checkpoint_restore":
            if self.restore_error:
                raise self.restore_error
            assert hashlib.sha256(Path(params["path"]).read_bytes()).hexdigest() == params["expect_sha256"]
            self.fingerprint = params["expect_fingerprint"]
            return {"matches": True, "fingerprint": self.fingerprint}
        self.fingerprint = "changed before result serialization"
        raise self.error


def session(root, error=None):
    result = BlenderSession(root, calls_dir=root / "calls")
    result.ck_dir.mkdir(parents=True)
    checkpoint = result.ck_dir / "c0000.blend"
    checkpoint.write_bytes(b"unchanged checkpoint fixture")
    result.index.append({"id": "c0000", "kind": "start", "parent": None, "file": checkpoint.name,
                         "fingerprint": "saved fingerprint", "sha256": hashlib.sha256(checkpoint.read_bytes()).hexdigest()})
    result.worker = ReplyWorker(result.head, error or WorkerError(
        "FrameError", "result not sendable: Out of range float values are not JSON compliant: nan",
        "original worker serialization traceback"))
    return result


def issue(session, operation):
    if operation == "mutating":
        return session.call_mutating("prepare", "temple_apply", {"revision": 0}, deadline_s=30, call_id="broken")
    return session.call_read("scene_summary", "scene_summary", {}, deadline_s=30, call_id="broken")


@pytest.mark.parametrize("operation", ["mutating", "read_only"])
def test_rpc_frame_error_restores_and_journals_original_before_raising(tmp_path, operation):
    current = session(tmp_path)
    before = copy.deepcopy(current.head)
    checkpoint = current._file(before)
    original_bytes = checkpoint.read_bytes()
    with pytest.raises(WorkerFailed, match="FrameError.*restored verified checkpoint c0000") as caught:
        issue(current, operation)
    assert caught.value.__cause__ is current.worker.error
    assert caught.value.head == current.head == before
    assert current.worker.fingerprint == before["fingerprint"]
    assert checkpoint.read_bytes() == original_bytes and len(current.index.checkpoints()) == 1
    assert [method for method, _ in current.worker.calls] == [
        "temple_apply" if operation == "mutating" else "scene_summary", "checkpoint_restore"]
    directory, = current.calls_dir.iterdir()
    result = json.loads((directory / "result.json").read_text())
    journal = json.loads((directory / "journal.json").read_text())
    assert result["status"] == journal["status"] == "failed_worker"
    assert result["worker_error"] == {"type": "FrameError", "message": current.worker.error.message,
                                      "traceback": current.worker.error.traceback}
    assert result["restore_verified"] is True
    assert result["checkpoint"] == result["restored_to"] == "c0000"
    assert journal["checkpoint_before"] == journal["checkpoint_after"] == journal["restored_to"] == "c0000"


@pytest.mark.parametrize("operation", ["mutating", "read_only"])
@pytest.mark.parametrize("etype", ["ValueError", "RuntimeError", "TempleError"])
def test_domain_errors_remain_recoverable_even_when_message_mentions_frame_error(tmp_path, operation, etype):
    current = session(tmp_path, WorkerError(etype, "invalid domain value, not a FrameError", "domain traceback"))
    result = issue(current, operation)
    assert result["status"] == "error" and result["restored_to"] == "c0000"
    assert current.worker.fingerprint == current.head["fingerprint"]
    assert len(current.index.checkpoints()) == 1


@pytest.mark.parametrize("operation", ["mutating", "read_only"])
def test_failed_restoration_still_stops_and_retains_both_errors(tmp_path, operation):
    current = session(tmp_path)
    current.worker.restore_error = WorkerError("RuntimeError", "checkpoint unavailable")
    with pytest.raises(WorkerFailed, match="FrameError.*checkpoint restoration failed.*checkpoint unavailable") as caught:
        issue(current, operation)
    assert caught.value.__cause__ is current.worker.error
    assert caught.value.head == current.head
    directory, = current.calls_dir.iterdir()
    result = json.loads((directory / "result.json").read_text())
    assert result["worker_error"]["type"] == "FrameError"
    assert result["restore_verified"] is False and result["restored_to"] is None
    assert "checkpoint unavailable" in result["restore_error"]


def test_lost_rollback_response_uses_verified_restart_but_original_fault_stays_fatal(tmp_path, monkeypatch):
    current = session(tmp_path)
    current.worker.restore_error = WorkerDied("eof", "lost rollback response")
    restarts = []

    def recover(error, tool, call_id, started):
        restarts.append(error)
        current.worker.fingerprint = current.head["fingerprint"]
        return {"status": "worker_restarted", "restored_to": current.head["id"]}

    monkeypatch.setattr(current, "_after_death", recover)
    with pytest.raises(WorkerFailed, match="FrameError.*restored verified checkpoint c0000") as caught:
        issue(current, "mutating")
    assert caught.value.__cause__ is current.worker.error
    assert restarts == [current.worker.restore_error]
    directory, = current.calls_dir.iterdir()
    result = json.loads((directory / "result.json").read_text())
    assert result["restore_verified"] and result["recovery"]["status"] == "worker_restarted"


def test_journal_io_error_cannot_downgrade_transport_failure_to_argument_error(tmp_path, monkeypatch):
    current = session(tmp_path)

    def broken_journal(*args):
        raise OSError("journal disk unavailable")

    monkeypatch.setattr(current, "_journal", broken_journal)
    with pytest.raises(WorkerFailed, match="FrameError.*could not journal.*journal disk unavailable") as caught:
        issue(current, "mutating")
    assert caught.value.__cause__ is current.worker.error
    assert current.worker.fingerprint == current.head["fingerprint"]


@pytest.mark.parametrize("tool,args", [
    ("execute_blender_code", {"code": "result = 1", "soft_timeout_s": 30, "label": "test"}),
    ("scene_summary", {"objects": None}),
])
def test_real_sdk_parent_loop_stops_after_first_transport_failure(tmp_path, scene, tool, args, monkeypatch):
    scene.s = session(scene.root)
    tools = [item for item in T.build_tools(scene) if item.name == tool]
    hooks = FakeScene()
    observed = []
    classify = L.classify_failure

    def capture_failure(error, **kwargs):
        observed.append(error)
        return classify(error, **kwargs)

    monkeypatch.setattr(L, "classify_failure", capture_failure)
    job, transport, result, _ = run_once(tmp_path, [R.tool_call(tool, args), R.message("must never be requested")],
                                        tools=tools, scene=hooks)
    # The pinned SDK wraps tool exceptions; classification must follow the retained cause.
    assert result["status"] == "failed_worker" and result["error"]["type"] == "UserError"
    outer, = observed
    assert type(outer).__name__ == "UserError"
    assert isinstance(outer.__cause__, WorkerFailed)
    assert outer.__cause__.__cause__ is scene.s.worker.error
    assert outer.__cause__.head == scene.s.head
    assert "FrameError" in result["error"]["message"]
    assert len(transport.creates) == len(transport.counts) == 1
    assert len(assert_money_invariants(job, RUN, transport)) == 1
    assert scene.s.worker.fingerprint == scene.s.head["fingerprint"]
    assert hooks.events == ["start", "exit_checkpoint", "close"]
