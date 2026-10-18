"""Task 2 regressions: reviewed state, run-local best, startup stop and durable recovery."""
import asyncio
import copy
from dataclasses import replace
import hashlib
import json
from types import SimpleNamespace

import pytest

from glasses3d.agent import approvals as A, cli, delivery as D, job_run, jobs, loop as L, replay as R
from glasses3d.agent import resume, verdicts as V
from glasses3d.agent.job_scene import JobScene
from glasses3d.agent.journal import BlenderSession, WorkerFailed, CheckpointMismatch
from glasses3d.agent.receipts import BlobStore
from glasses3d.agent.session_inputs import NOTE_PREFIX
from glasses3d.tests.agent.loop_support import make_job, make_tools, spec_for
from glasses3d.tests.agent.test_draft_delivery import draft_fixture
from glasses3d.tests.agent.test_jobs import setup_inputs


def test_previous_run_best_never_selects_resumed_draft(tmp_path, monkeypatch):
    scene, result, _, _ = draft_fixture(tmp_path, monkeypatch)
    scene.best_checkpoint_run_id = "previous-run"
    delivered = D.deliver_draft(tmp_path, scene, result)
    report = D.load_delivery(tmp_path, delivered["id"])["report_data"]
    assert report["checkpoint_selection"] == "head"
    assert report["checkpoint"] == result["exit_checkpoint"]["id"] == "c0001"


@pytest.mark.parametrize("status", ["failed", "failed_worker"])
def test_failed_run_reverifies_its_own_best_before_latest_head(tmp_path, monkeypatch, status):
    scene, result, _, _ = draft_fixture(tmp_path, monkeypatch, status=status)
    delivered = D.deliver_draft(tmp_path, scene, result)
    report = D.load_delivery(tmp_path, delivered["id"])["report_data"]
    assert report["checkpoint"] == "c0000"
    assert report["source_checkpoint"]["verification_attempts"][0]["status"] == "verified"
    assert report["run_status"] == status and report["accepted_finish"] is False


def test_failed_run_with_corrupt_best_reverifies_latest_head(tmp_path, monkeypatch):
    scene, result, _, _ = draft_fixture(tmp_path, monkeypatch, status="failed_worker")
    (tmp_path / "checkpoints/c0000.blend").write_bytes(b"corrupt best")
    delivered = D.deliver_draft(tmp_path, scene, result)
    report = D.load_delivery(tmp_path, delivered["id"])["report_data"]
    assert report["checkpoint"] == "c0001"
    assert [(r["checkpoint"], r["status"]) for r in report["source_checkpoint"]["verification_attempts"]] == [
        ("c0000", "verification_failed"), ("c0001", "verified")]


def test_best_mark_is_reset_and_persisted_at_new_run_boundary(tmp_path):
    scene = JobScene(tmp_path, job={})
    scene.active_run_id = scene.best_checkpoint_run_id = "old"
    scene.best_checkpoint = "c0001"
    scene.save()
    scene = JobScene(tmp_path, job={})
    scene.bind_review_log(SimpleNamespace(run_id="new"))
    assert scene.best_checkpoint is scene.best_checkpoint_run_id is None
    assert JobScene(tmp_path, job={}).active_run_id == "new"
    scene.best_checkpoint = "c0002"
    scene.best_checkpoint_run_id = "new"
    scene.bind_review_log(SimpleNamespace(run_id="new"))
    assert scene.best_checkpoint == "c0002"


def save_history(root, items):
    async def write():
        path = root / "session/session.sqlite"
        path.parent.mkdir(exist_ok=True)
        session = L.BlobSession(L.SESSION_ID, path, blobs=BlobStore(root / "blobs"))
        try:
            await session.add_items(items)
        finally:
            session.close()
    asyncio.run(write())


def test_resume_estimate_prices_history_and_binds_mode_and_content(tmp_path):
    args, _ = setup_inputs(tmp_path)
    root = jobs.create_job(**args)
    settings = replace(job_run.settings_default(), max_turns=2)
    fresh, _ = job_run.prepare_run(root, "fresh", settings=settings, write_request=True)
    original = json.loads((root / "runs/fresh/approval-request.json").read_text())
    history = [{"role": "user", "content": "old saved evidence " * 20000}]
    save_history(root, history)
    spec, _ = job_run.prepare_run(root, "resume", settings=settings, write_request=True, resume=True)
    request = json.loads((root / "runs/resume/approval-request.json").read_text())
    assert request["estimate"]["initial_context_tokens"] > original["estimate"]["initial_context_tokens"] + 50000
    assert request["request"]["settings"]["session"]["resume"] is True
    assert original["request"]["settings"]["session"] == {"resume": False}
    assert fresh.current_request_sha256 != spec.current_request_sha256
    assert resume.saved_items(root) == history
    L._check_bound_request(root / "runs/resume", spec)
    with pytest.raises(A.ApprovalRefused, match="stale|changed"):
        L._check_bound_request(root / "runs/resume", replace(spec, resume=False))
    save_history(root, [{"role": "assistant", "content": "later work"}])
    with pytest.raises(A.ApprovalRefused, match="changed"):
        L._check_bound_request(root / "runs/resume", spec)


def test_estimate_cli_exposes_resume_mode():
    args = cli.parser().parse_args(["job", "estimate", "some-job", "--resume"])
    assert args.resume is True


def reviewed_fixture(root, monkeypatch):
    scene = JobScene(root, job={})
    for n in range(2):
        path = root / "checkpoints" / f"c{n:04d}.blend"
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(f"checkpoint-{n}".encode())
        scene.s.index.append({"id": path.stem, "kind": "start" if n == 0 else "save",
                              "file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                              "bytes": path.stat().st_size, "fingerprint": str(n) * 64,
                              "parent": "c0000" if n else None})
    delivery = {"id": "reviewed", "glb_sha256": "a" * 64, "manifest_sha256": "b" * 64,
                "report_data": {"checkpoint": "c0000", "fingerprint": "0" * 64}}
    monkeypatch.setattr(V, "_delivery", lambda *a: copy.deepcopy(delivery))
    return scene


def test_verdict_binds_reviewed_checkpoint_and_sent_note_does_not_reset_again(tmp_path, monkeypatch):
    scene = reviewed_fixture(tmp_path, monkeypatch)
    row = V.append_verdict(tmp_path, "changes", "A little narrower.\u2028Keep the tint.", delivery_id="reviewed")
    assert row["checkpoint"] == {"id": "c0000", "fingerprint": "0" * 64}
    note, = V.pending_notes(tmp_path)
    assert "checkpoint c0000" in note and "fingerprint " + "0" * 64 in note
    assert note.endswith(row["notes"])
    target = V.resume_checkpoint(tmp_path, [])
    assert target["id"] == "c0000" and scene.s.head["id"] == "c0001"
    items = [{"role": "user", "content": [{"type": "input_text", "text": NOTE_PREFIX + note}]}]
    assert V.resume_checkpoint(tmp_path, items) is None
    (tmp_path / "checkpoints/c0000.blend").write_bytes(b"changed")
    with pytest.raises(V.VerdictError, match="bytes changed"):
        V.resume_checkpoint(tmp_path, [])


@pytest.mark.parametrize("when", ["before_start", "during_start"])
def test_stop_during_startup_never_counts_or_sends_request(tmp_path, when):
    scene, tools = make_tools()
    root, sha = make_job(tmp_path, tools=tools)
    transport = R.ScriptedOpenAI([R.message("must never run")])
    if when == "before_start":
        (root / L.STOP_FILE).write_text("Owner requested stop.")
    else:
        original = scene.start
        def start():
            (root / L.STOP_FILE).write_text("Owner requested stop.")
            return original()
        scene.start = start
    spec = spec_for(root, "20261007T120000-run001", sha, tools, transport)
    result = asyncio.run(L.run_astra(spec, scene=scene))
    assert result["status"] == "stopped" and result["stop_observed"] is True
    assert result["requests"] == 0 and not transport.creates and not transport.count_answers


@pytest.mark.parametrize("legacy_suffix", [None, "-rollback", "-scope-rollback"])
@pytest.mark.parametrize("has_output", [True, False])
def test_recovery_never_undoes_a_host_refusal_rollback(tmp_path, legacy_suffix, has_output):
    scene = JobScene(tmp_path, job={})
    scene.s.index.append({"id": "c0000", "kind": "start", "fingerprint": "0"})
    scene.s.index.append({"id": "c0001", "kind": "save", "fingerprint": "1", "parent": "c0000", "call_id": "edit"})
    cid = "edit" + (legacy_suffix or "")
    scene.s.index.append({"id": "c0002", "kind": "restore", "fingerprint": "0", "parent": "c0001",
                          "restored_from": "c0000", "call_id": cid,
                          **({"recovery_of": "edit"} if legacy_suffix is None else {})})
    # Killed after the durable restore index but before job_scene state was saved.
    resumed = JobScene(tmp_path, job={})
    outputs = [{"type": "function_call_output", "call_id": "edit"}] if has_output else []
    assert L.plan_rollback(outputs, resumed.journal_entries()) is None
    assert resumed.s.head["fingerprint"] == "0"
    assert L.plan_rollback(outputs, JobScene(tmp_path, job={}).journal_entries()) is None
    # A genuinely later lost mutation must still be rolled back to the safe restored state.
    resumed.s.index.append({"id": "c0003", "kind": "save", "fingerprint": "2", "parent": "c0002", "call_id": "later"})
    assert L.plan_rollback(outputs, resumed.journal_entries())["checkpoint_before"] == "c0002"


@pytest.mark.parametrize("status,fp", [("worker_restarted", "1"), ("ok", "1")])
def test_scope_restore_must_verify_result_and_fingerprint(tmp_path, monkeypatch, status, fp):
    scene = JobScene(tmp_path, job={})
    scene.s.index.append({"id": "c0", "kind": "start", "fingerprint": "0"})
    scene.s.index.append({"id": "c1", "kind": "save", "fingerprint": fp})
    monkeypatch.setattr(BlenderSession, "restore_checkpoint", lambda *a, **kw: {"status": status})
    with pytest.raises(WorkerFailed, match="rollback"):
        scene.rollback_internal("c0", call_id="edit", reason="scope")
    assert not any(e.get("kind") == "rollback" for e in scene.events)


@pytest.mark.parametrize("stage", ["restore_index", "host_state"])
def test_rollback_persistence_error_is_a_fatal_stop(tmp_path, monkeypatch, stage):
    scene = JobScene(tmp_path, job={})
    scene.s.index.append({"id": "c0", "kind": "start", "fingerprint": "0"})
    def fail(*args, **kwargs):
        raise OSError("synthetic disk failure")
    if stage == "restore_index":
        monkeypatch.setattr(BlenderSession, "restore_checkpoint", fail)
    else:
        monkeypatch.setattr(BlenderSession, "restore_checkpoint",
                            lambda *a, **kw: {"status": "ok", "checkpoint": "c0"})
        monkeypatch.setattr(scene, "save", fail)
    with pytest.raises(WorkerFailed, match="synthetic disk failure"):
        scene.rollback_internal("c0", call_id="edit", reason="scope")


@pytest.mark.parametrize("failure", [WorkerFailed("fatal", None), CheckpointMismatch("fatal")])
def test_fatal_scope_check_is_not_downgraded_to_refusal(tmp_path, monkeypatch, failure):
    from glasses3d.agent.owner_edits import ScopeGuard
    scene = JobScene(tmp_path, job={})
    scene.owner_edit = {"phase": "edit"}
    scene.s.index.append({"id": "c0", "kind": "start", "fingerprint": "0"})
    def mutate(*a, **kw):
        scene.s.index.append({"id": "c1", "kind": "save", "fingerprint": "1"})
        return {"status": "ok"}
    monkeypatch.setattr(BlenderSession, "_mutating", mutate)
    monkeypatch.setattr(ScopeGuard, "verify", lambda *a: (_ for _ in ()).throw(failure))
    with pytest.raises(type(failure), match="fatal"):
        scene.s._mutating("edit", "edit", {}, 1, "cid", "label", 0, arguments={})


def test_every_terminal_return_uses_the_status_vocabulary(tmp_path, monkeypatch):
    scene, tools = make_tools()
    root, sha = make_job(tmp_path, tools=tools)
    transport = R.ScriptedOpenAI([R.message("done")])
    checked = []
    original = L.checked_terminal_status
    def check(status):
        checked.append(status)
        return original(status)
    monkeypatch.setattr(L, "checked_terminal_status", check)
    result = asyncio.run(L.run_astra(spec_for(root, "20261007T120000-run001", sha, tools, transport), scene=scene))
    assert checked == [result["status"]]
    for status in L.STATUSES:
        assert original(status) == status
    with pytest.raises(ValueError, match="unknown terminal"):
        original("new_unregistered_status")


def test_new_drive_status_cannot_escape_terminal_vocabulary(tmp_path, monkeypatch):
    scene, tools = make_tools()
    root, sha = make_job(tmp_path, tools=tools)
    transport = R.ScriptedOpenAI([])
    async def drive(*args, **kwargs):
        return "new_unregistered_status", None
    monkeypatch.setattr(L, "_drive", drive)
    with pytest.raises(ValueError, match="unknown terminal"):
        asyncio.run(L.run_astra(spec_for(root, "20261007T120000-run001", sha, tools, transport), scene=scene))
    assert not transport.creates
    assert not (root / "runs/20261007T120000-run001/result.json").exists()


@pytest.mark.parametrize("interrupted", [False, True])
def test_resume_restores_reviewed_draft_before_first_request(tmp_path, monkeypatch, interrupted):
    args, _ = setup_inputs(tmp_path)
    root = jobs.create_job(**args)
    scene = reviewed_fixture(root, monkeypatch)
    V.append_verdict(root, "changes", "Narrow the bridge.", delivery_id="reviewed")
    history = [{"role": "user", "content": "earlier experiments remain in history"}]
    if interrupted:
        scene.s.index.append({**scene.s.index.get("c0001"), "id": "c0002", "kind": "save",
                              "parent": "c0001", "call_id": "lost"})
        history.append({"type": "function_call", "name": "execute_blender_code", "call_id": "lost",
                        "arguments": "{}"})
    save_history(root, history)
    settings = replace(job_run.settings_default(), finish_tool=None, tool_choice="auto", max_turns=1)
    transport = R.ScriptedOpenAI([R.message("done")])
    spec, _ = job_run.prepare_run(root, "review-resume", settings=settings, resume=True,
                                  scene=scene, offline_handler=transport.handle, write_request=True)
    A.write_approval(root / "runs/review-resume", max_usd="23", owner_words="Synthetic offline test only.",
                     recorded_by="test")
    monkeypatch.setattr(scene, "start", lambda: {"head": scene.s.head})
    restored = []
    def restore(cid, *, reason):
        restored.append(cid)
        row = scene.s.index.get(cid)
        alias = f"c{len(restored) + 2:04d}"
        scene.s.index.append({**row, "id": alias, "kind": "restore", "parent": scene.s.head["id"]})
        return {"checkpoint": alias}
    monkeypatch.setattr(scene, "restore", restore)
    monkeypatch.setattr(scene, "exit_checkpoint", lambda: {"id": scene.s.head["id"], "verified": True})
    result = asyncio.run(L.run_astra(spec, scene=scene))
    assert result["status"] == "completed" and restored == (["c0001", "c0000"] if interrupted else ["c0000"])
    request = transport.creates[0]
    assert "reviewed delivery reviewed: checkpoint c0000" in json.dumps(request)
    assert request["input"][0] == history[0]
    assert scene.s.head["fingerprint"] == "0" * 64
    if interrupted:
        assert result["rollback"]["scene_checkpoint"] == "c0004"
        assert result["rollback"]["recovery_checkpoint"] == "c0003"
        assert "the scene is checkpoint c0003" not in json.dumps(request)
