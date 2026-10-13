"""Assembly print transactions, fixed policy and cancellation without a bake or provider call."""
import asyncio
import json
import os
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from agents.tool_context import ToolContext

from glasses3d.agent import tools as T
from glasses3d.agent.tools import photo_print as PP
from glasses3d.agent.journal import CheckpointMismatch
from glasses3d.g3d_host.photoprint import inputs as IN, manifest as MF, production as P


@pytest.fixture
def stage(tmp_path, monkeypatch):
    path = tmp_path / "work/exports/source.glb"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"source")
    initial = {"id": "c0001", "fingerprint": "a" * 64}
    session = SimpleNamespace(head=initial.copy(), calls=[], restores=[])
    scene = SimpleNamespace(root=tmp_path, s=session)
    state = {"bake_calls": [], "read_calls": [], "fail": None, "cancel": threading.Event(), "export_count": 0}

    def export(**kwargs):
        state["export_count"] += 1
        out = {"status": "exported", "path": str(path), "sha256": PP.arview.sha256(path),
               "validation_sha256": PP.arview.sha256(path), "validate_spec": {"ok": True}, **session.head}
        out["checkpoint"] = session.head["id"]
        if state["fail"] == "export" and len(session.calls) == 3:
            out["validate_spec"]["ok"] = False
        out["print_check"] = {"ok": state["fail"] != "freshness", "glb_sha256": out["sha256"]}
        if state["export_count"] == 3:
            if state["fail"] == "freshness_sha":
                out["print_check"]["glb_sha256"] = "wrong"
            elif state["fail"] == "exception:verified":
                raise RuntimeError("verification export unexpectedly failed")
            elif state["fail"] == "cancel:verified":
                state["cancel"].set()
        return out

    def mutate(op, method, params, **kwargs):
        session.calls.append((op, method, params, kwargs))
        n = len(session.calls)
        session.head = {"id": f"c{n + 1:04d}", "fingerprint": str(n) * 64}
        if state["fail"] == "exception:" + op:
            raise RuntimeError("worker unexpectedly failed")
        if state["fail"] == "cancel:" + op:
            state["cancel"].set()
        return {"status": "refused" if state["fail"] == op else "ok", "result": {"status": "ok"}}

    def restore(cid, **kwargs):
        session.restores.append((cid, kwargs))
        if state["fail"] == "restore":
            return {"status": "error"}
        session.head = {**initial, "id": "c0005"}
        return {"status": "ok", "restored_from": cid}

    def load(root, **kwargs):
        state["read_calls"].append((root, kwargs))
        return object()

    def bake(job, parts, **kwargs):
        state["bake_calls"].append((job, parts, kwargs))
        if state["fail"] == "bake":
            raise ValueError("bake input refused")
        if state["fail"] == "cancel:bake":
            state["cancel"].set()
        (kwargs["out_dir"] / MF.MANIFEST).write_text('{"test": "manifest identity"}')
        return {"production": {"quality_proof": {"status": "not_run"}, "thresholds": kwargs["thresholds"]}}

    session.call_mutating, session.restore_checkpoint = mutate, restore
    scene.seeing = SimpleNamespace(export_head=export)
    monkeypatch.setattr(IN, "job_from_dir", load)
    monkeypatch.setattr(P, "bake_print", bake)
    monkeypatch.setattr(MF, "load_manifest", lambda _: {"parts": ["Brow", "Brow.001"],
        "texture_parts": [] if state["fail"] == "no_texture" else ["Brow.001"]})
    monkeypatch.setattr(MF, "check_glb", lambda *a: {"ok": state["fail"] not in {"manifest", "restore"},
                                                    "reasons": ["controlled"]})
    monkeypatch.setattr(T, "fit_workers", lambda: 12)
    return scene, state


def run(stage, **kwargs):
    scene, state = stage
    return PP.run(scene, "print-test", parts=["Brow", "Brow.001"], basis="basis-001",
                  cancelled=state["cancel"], **kwargs)


def test_current_geometry_bake_then_only_eligible_parts_attach(stage):
    scene, state = stage
    result = run(stage)
    assert result["status"] == "ok" and result["texture_parts"] == ["Brow.001"]
    assert [r[0] for r in scene.s.calls] == ["detach_print", "print_uv", "attach_print"]
    assert scene.s.calls[-1][2]["kwargs"]["parts"] == ["Brow.001"]
    assert scene.s.calls[1][2]["kwargs"]["parts"] == ["Brow", "Brow.001"]
    assert scene.s.calls[1][2]["kwargs"]["replace"] is True
    assert all(r[1] == "g3d_call" and r[3]["call_id"] == "print-test" for r in scene.s.calls)
    assert not scene.s.restores
    assert state["read_calls"][0][1]["basis"] == "basis-001"
    assert state["bake_calls"][0][2]["thresholds"] == P.THRESHOLDS
    assert state["bake_calls"][0][2]["workers"] == 12
    assert not ({"override_min_cover", "level", "min_cover"} & state["bake_calls"][0][2].keys())
    saved = json.loads((Path(result["bake"]) / "application.json").read_text())
    assert saved == result and saved["production"]["quality_proof"]["status"] == "not_run"


@pytest.mark.parametrize("failure", ["detach_print", "print_uv", "attach_print", "export", "manifest",
                                     "freshness", "freshness_sha", "cancel:verified",
                                     "cancel:detach_print", "cancel:print_uv", "cancel:attach_print"])
def test_every_post_write_failure_restores_the_prior_print_and_geometry(stage, failure):
    scene, state = stage
    state["fail"] = failure
    result = run(stage)
    assert result["status"] == "refused" and result["restored_to"] == "c0001"
    assert scene.s.head["fingerprint"] == "a" * 64
    assert scene.s.restores == [("c0001", {"call_id": "print-test"})]
    assert json.loads((Path(result["bake"]) / "application.json").read_text())["status"] == "refused"


@pytest.mark.parametrize("failure", ["no_texture", "cancel:bake"])
def test_pre_write_refusal_never_changes_scene(stage, failure):
    scene, state = stage
    state["fail"] = failure
    result = run(stage)
    assert result["status"] == "refused" and result["restored_to"] is None
    assert not scene.s.calls and not scene.s.restores


def test_cancel_before_bake_never_runs_host_or_worker(stage):
    scene, state = stage
    state["cancel"].set()
    assert run(stage)["code"] == "E_CANCELLED"
    assert not state["bake_calls"] and not scene.s.calls


@pytest.mark.parametrize("op", ["detach_print", "print_uv", "attach_print", "verified"])
def test_unexpected_error_after_scene_change_also_restores(stage, op):
    scene, state = stage
    state["fail"] = "exception:" + op
    with pytest.raises(RuntimeError, match="unexpectedly failed"):
        run(stage)
    assert scene.s.head["fingerprint"] == "a" * 64


def test_restore_failure_is_fatal(stage):
    _, state = stage
    state["fail"] = "restore"
    with pytest.raises(CheckpointMismatch):
        run(stage)


def test_calibration_and_registration_cannot_escape_job(stage, tmp_path):
    scene, state = stage
    with pytest.raises(ValueError):
        run(stage, calibration="../../private.json")
    assert not state["bake_calls"] and not scene.s.calls


def test_bake_output_rejects_redirected_parent_before_writing(stage, tmp_path):
    scene, state = stage
    external = tmp_path.parent / (tmp_path.name + "-outside")
    external.mkdir()
    link = tmp_path / "work/print"
    try:
        os.symlink(external, link, target_is_directory=True)
    except OSError as exc:
        if os.name != "nt":
            raise
        junction = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(external)], capture_output=True)
        assert junction.returncode == 0, f"cannot create test junction after symlink refusal: {exc}"
    with pytest.raises(ValueError, match="inside"):
        run(stage)
    assert not list(external.iterdir()) and not state["bake_calls"] and not scene.s.calls


def test_bake_evidence_is_never_overwritten(stage):
    scene, state = stage
    first = run(stage)
    path = Path(first["bake"]) / "application.json"
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        run(stage)
    assert path.read_bytes() == before and len(state["bake_calls"]) == 1


@pytest.mark.parametrize("parts,basis", [([], "b1"), (["Brow", "Brow"], "b1"), ([""], "b1"),
                                        (["Brow"], ""), ([1], "b1")])
def test_exact_explicit_inputs_required_before_export(stage, parts, basis):
    scene, state = stage
    assert PP.run(scene, "bad", parts=parts, basis=basis)["code"] == "E_ARGS"
    assert state["export_count"] == 0 and not state["bake_calls"]


def test_sdk_cancellation_during_bake_cannot_apply_after_caller_leaves(stage, monkeypatch):
    scene, state = stage
    entered, release = threading.Event(), threading.Event()
    original = P.bake_print

    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(10), "test did not release its controlled bake"
        return original(*args, **kwargs)

    monkeypatch.setattr(P, "bake_print", slow)
    scene.lock = threading.RLock()
    scene.record_call = lambda *a: None
    scene.record_images = lambda *a: None
    scene.mark_unseen = lambda *a: None
    scene.images = []
    tool = next(t for t in T.build_tools(scene) if t.name == "photo_print")
    arguments = json.dumps({"parts": ["Brow", "Brow.001"], "basis": "basis-001"})
    ctx = ToolContext(context=None, tool_name=tool.name, tool_call_id="cancelled-print", tool_arguments=arguments)

    async def exercise():
        task = asyncio.create_task(tool.on_invoke_tool(ctx, arguments))
        assert await asyncio.to_thread(entered.wait, 10)
        task.cancel()
        await asyncio.sleep(0.05)
        assert not task.done()  # session teardown must wait for the print thread's real cleanup
        task.cancel()  # repeated cancellation still cannot orphan the worker operation
        await asyncio.sleep(0.05)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        def wait_closed():
            with scene.lock:
                return not scene.s.calls
        assert await asyncio.to_thread(wait_closed)
    try:
        asyncio.run(exercise())
    finally:
        release.set()
    assert not scene.s.calls and not scene.s.restores
