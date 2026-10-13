"""Generic job scene hooks: real empty seed, durable lost-turn rollback and image metadata."""
from __future__ import annotations

import json
import asyncio
from types import SimpleNamespace

import pytest
from PIL import Image

pytest.importorskip("agents")
from agents.tool_context import ToolContext
from glasses3d.agent.job_scene import JobScene
from glasses3d.agent.journal import CheckpointMismatch, WorkerFailed
from glasses3d.agent.loop import plan_rollback
from glasses3d.agent.worker_client import WorkerDied
from glasses3d.g3d_host import blender


def test_journal_derives_mutation_and_survives_reopen(tmp_path):
    scene = JobScene(tmp_path, job={})
    scene.finish = {"call_id": "earlier-finish", "checkpoint": "c0001"}
    scene.record_call("read", "scene_summary", "c0001", "c0001", {"status": "ok"})
    assert scene.finish is not None
    scene.record_call("lost", "execute_blender_code", "c0001", "c0002", {"status": "ok"})
    assert scene.finish is None
    scene.record_call("lost", "execute_blender_code", "c0001", "c0002", {"status": "ok"})
    reopened = JobScene(tmp_path, job={})
    entries = reopened.journal_entries()
    assert len(entries) == 2 and [e["mutating"] for e in entries] == [False, True]
    out = plan_rollback([{"type": "function_call_output", "call_id": "read"}], entries)
    assert out["checkpoint_before"] == "c0001" and out["lost_calls"] == ["lost"]


@pytest.mark.parametrize("partial_read", [False, True])
def test_index_recovers_mutation_killed_before_call_journal(tmp_path, partial_read):
    scene = JobScene(tmp_path, job={})
    scene.s.index.append({"id": "c0000", "kind": "start", "parent": None, "call_id": None, "fingerprint": "fp0"})
    if partial_read:
        scene.record_call("lost", "host_tool", "c0000", "c0000", {"status": "ok"})
    # The worker saved both files; the host died before writing its call event.
    scene.s.index.append({"id": "c0001", "kind": "save", "parent": "c0000", "call_id": "lost", "fingerprint": "fp1"})
    reopened = JobScene(tmp_path, job={})
    entries = reopened.journal_entries()
    assert len(entries) == 1 and entries[0]["recovered_from"] == "checkpoint_index"
    assert entries[0]["checkpoint_before"] == "c0000" and entries[0]["checkpoint_after"] == "c0001"
    assert plan_rollback([], entries)["lost_calls"] == ["lost"]
    assert JobScene(tmp_path, job={}).journal_entries() == entries  # recovery is durable and idempotent


def test_index_recovery_preserves_later_read_and_prior_rollback_boundaries(tmp_path):
    scene = JobScene(tmp_path, job={})
    for cid, parent, kind, call_id in (("c0000", None, "start", None), ("c0001", "c0000", "save", "lost")):
        scene.s.index.append({"id": cid, "kind": kind, "parent": parent, "call_id": call_id, "fingerprint": cid})
    scene.record_call("later-read", "scene_summary", "c0001", "c0001", {"status": "ok"})
    entries = scene.journal_entries()
    assert [e["call_id"] for e in entries] == ["lost", "later-read"]
    assert plan_rollback([{"type": "function_call_output", "call_id": "later-read"}], entries) is None
    scene.events = [{"kind": "rollback", "head": "c0002", "checkpoint": "c0000"}]
    scene.s.index.append({"id": "c0002", "kind": "restore", "parent": "c0001", "call_id": "rollback",
                          "fingerprint": "c0000"})
    assert scene.journal_entries() == [{"kind": "rollback", "head": "c0002", "checkpoint": "c0000"}]
    assert plan_rollback([], scene.journal_entries()) is None


def test_worker_cpu_and_fatal_errors_are_not_hidden(tmp_path):
    scene = JobScene(tmp_path, job={})
    scene.s.worker = SimpleNamespace(pid=37, cpu_seconds=lambda: 12.5)
    assert scene.worker_info() == {"worker_pid": 37, "worker_cpu_s": 12.5}

    def fail(*args, **kwargs):
        raise WorkerFailed("worker gone", None)

    scene.s.restore_checkpoint = fail
    with pytest.raises(WorkerFailed, match="worker gone"):
        scene.restore("c0000", reason="lost turn")
    assert scene.journal_entries() == []


def test_refused_restore_does_not_mark_a_rollback(tmp_path):
    scene = JobScene(tmp_path, job={})
    scene.s.restore_checkpoint = lambda *args, **kwargs: {"status": "error", "message": "missing"}
    with pytest.raises(WorkerFailed, match="rollback.*missing"):
        scene.restore("c9999", reason="lost turn")
    assert not scene.journal_entries()


def test_images_persist_with_view_and_checkpoint_and_only_superseded_are_stubbed(tmp_path):
    scene = JobScene(tmp_path, job={})
    for n, (kind, view) in enumerate((("read_image", "front"), ("compare_to_photos", "front"),
                                     ("compare_to_photos", "side"), ("compare_to_photos", "front"))):
        p = tmp_path / f"{n}.png"
        Image.new("RGB", (8, 8), (n, 0, 0)).save(p)
        scene.record_call(f"call{n}", kind, f"c{n}", f"c{n}", {"status": "ok"})
        scene.record_images(f"call{n}", {"images": [{"path": str(p), "view": view, "checkpoint": f"c{n}"}]})
    reopened = JobScene(tmp_path, job={})
    assert len(reopened.images) == 4
    assert all(i["call_id"] and i["view"] and i["checkpoint"] for i in reopened.images)
    digests = [i["sha256"] for i in reopened.images]
    stubs = reopened.context_stubs(digests)
    assert set(stubs) == {digests[1]} and "call1" in stubs[digests[1]]
    reopened.best_checkpoint = "c1"
    assert reopened.context_stubs(digests) == {}


def test_finish_timed_out_before_or_after_assignment_is_not_saved(tmp_path):
    scene = JobScene(tmp_path, job={})
    scene.mark_unseen("late")
    scene.finish = {"call_id": "late"}
    scene.record_call("late", "finish", "c0001", "c0001", {"status": "accepted"})
    assert scene.finish is None
    scene.finish = {"call_id": "early"}
    scene.mark_unseen("early")
    assert JobScene(tmp_path, job={}).finish is None


def test_loop_binds_scene_journal_to_the_run_tool_receipt(tmp_path, monkeypatch):
    """Real SDK + scripted transport: a scene event reaches its run's journal.json before the result returns."""
    from agents import function_tool
    from glasses3d.agent import loop as L, replay as R
    from glasses3d.tests.agent.loop_support import make_job, spec_for

    scene = None

    @function_tool
    async def execute_blender_code(ctx: ToolContext) -> dict:
        """Record one synthetic checkpoint change for this offline journal proof."""
        scene.record_call(ctx.tool_call_id, "execute_blender_code", "c0000", "c0001", {"status": "ok"})
        receipt = next((scene.root / "runs" / "journal-proof" / "tools").glob("*/journal.json"))
        assert json.loads(receipt.read_text(encoding="utf-8"))["mutating"] is True
        return {"status": "ok"}

    tools = [execute_blender_code]
    job, sha = make_job(tmp_path, tools=tools, run_id="journal-proof")
    scene = JobScene(job)
    monkeypatch.setattr(scene, "start", lambda: {"head": "c0000"})
    monkeypatch.setattr(scene, "exit_checkpoint", lambda: {"id": "c0001", "verified": True})
    transport = R.ScriptedOpenAI([R.tool_call("execute_blender_code", {}), R.message("done")])
    result = asyncio.run(L.run_astra(spec_for(job, "journal-proof", sha, tools, transport), scene=scene))
    assert result["status"] == "completed"
    receipt = next((job / "runs" / "journal-proof" / "tools").glob("*/journal.json"))
    row = json.loads(receipt.read_text(encoding="utf-8"))
    assert row["checkpoint_before"] == "c0000" and row["checkpoint_after"] == "c0001"
    assert row["mutating"] is True and row["status"] == "ok"


@pytest.mark.parametrize("rig", ["photo_v1", "studio_dome_v1"])
@pytest.mark.parametrize("error", [WorkerFailed("worker gone", None), CheckpointMismatch("bad checkpoint"),
                                  WorkerDied("exited", "worker gone")])
def test_fatal_export_errors_escape_both_compare_paths(tmp_path, monkeypatch, rig, error):
    from glasses3d.agent import seeing as SE

    scene = JobScene(tmp_path, job={})
    monkeypatch.setattr(SE.C, "load_basis", lambda *args: SimpleNamespace(
        refs={}, is_usable=lambda rid: False, usable_refs=lambda: [], unusable_refs=lambda: {}))
    monkeypatch.setattr(scene.seeing, "_ref_ids", lambda *args: [])
    monkeypatch.setattr(scene.seeing, "_check_v3_args", lambda *args: {"zoom": []})

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(scene.seeing, "export_head", fail)
    with pytest.raises(type(error)) as raised:
        scene.seeing.compare_to_photos(rig=rig, call_id="fatal-compare")
    assert raised.value is error
    assert scene.journal_entries() == []  # no misleading E_COMPARE result was recorded


@pytest.mark.skipif(blender.blender_exe() is None, reason="Blender 5.2 not installed")
def test_real_seed_checkpoints_and_lost_turn_rollback(tmp_path):
    scene = JobScene(tmp_path, job={}, selftest=True)
    try:
        assert scene.start()["head"]["id"] == "c0000"
        summary = scene.s.call_read("scene_summary", "scene_summary", {}, deadline_s=30, call_id="summary")
        data = summary["result"]
        assert data["units"]["system"] == "METRIC" and data["units"]["length_unit"] == "MILLIMETERS"
        assert data["units"]["scale_length"] == pytest.approx(0.001)
        assert data["axes"] == "+Y up; +Z front" and data["origin_marker"] == [0, 0, 0]
        assert set(data["objects"]) == {"mdl_bridge_underside"} and data["warnings"] == []
        inspection = scene.s.execute("result = {'collections': sorted(c.name for c in bpy.context.scene.collection.children), "
                                     "'marker': list(bpy.data.objects['mdl_bridge_underside'].location)}", call_id="inspect")
        assert inspection["status"] == "ok", inspection
        assert inspection["result"] == {"collections": ["frame", "lenses", "temples"], "marker": [0, 0, 0]}
        assert scene.checkpoint("empty seed", "label")["saved"] is False
        created = scene.s.execute("bpy.ops.mesh.primitive_cube_add(size=2)\n"
                                  "bpy.context.object['partRole'] = 'frame'", call_id="lost")
        assert created["status"] == "ok" and scene.s.head["id"] == "c0001"
        assert scene.exit_checkpoint()["verified"] is True
        assert scene.worker_info()["worker_cpu_s"] >= 0
    finally:
        scene.close()
    scene = JobScene(tmp_path, job={}, selftest=True)
    try:
        scene.start()
        lost = plan_rollback([], scene.journal_entries())
        assert lost["checkpoint_before"] == "c0000" and lost["lost_calls"] == ["lost"]
        restored = scene.restore(lost["checkpoint_before"], reason="test lost result")
        assert restored["checkpoint"] == "c0002"
        assert scene.exit_checkpoint()["verified"] is True
        assert plan_rollback([], scene.journal_entries()) is None
        assert json.loads(scene.state_path.read_text(encoding="utf-8"))["events"][-1]["kind"] == "rollback"
    finally:
        scene.close()
