"""Real deadline composition and background-lock diagnostics, without paid calls."""
import asyncio
import inspect
import json
import subprocess
import socket
import threading
from types import SimpleNamespace

from agents.tool_context import ToolContext
import pytest

from glasses3d.agent import journal, loop, seeing, tool_timeouts as D, tools, worker_client as W
from glasses3d.agent.tools import arview, look, model_map, owner_preview
from glasses3d.tests.agent.test_job_tools import scene  # shared local fake scene, no worker


@pytest.mark.parametrize("edit", [None, {"phase": "understand"}, {"phase": "edit"}])
@pytest.mark.parametrize("photos", [1, 64, 65, 129])
def test_every_registered_tool_has_a_composed_budget(edit, photos):
    source = SimpleNamespace(owner_edit=edit, job={"images": [{}] * photos})
    for tool in tools.build_tools(source):
        steps = D.inner_steps_s(tool.name, photo_count=photos)
        if tool.name in D.UNTIMED_TOOLS:
            assert tool.timeout_seconds is None and steps is None
        else:
            assert tool.timeout_seconds == D.scene_timeout_s(source, tool.name)
            assert tool.timeout_seconds >= steps + D.HOST_WORK_MARGIN_S + D.SDK_TIMEOUT_MARGIN_S
    with pytest.raises(KeyError):
        D.sdk_timeout_s("new_tool_without_a_budget")


def test_every_facet_tool_has_a_composed_budget():
    from glasses3d.agent.temple_lab.tools import TOOL_NAMES
    for name in TOOL_NAMES:
        assert loop.sdk_timeout_s(name) > D.inner_steps_s(name)


def test_real_capture_defaults_and_worker_deadlines_are_the_shared_sources():
    assert inspect.signature(arview.capture).parameters["timeout_s"].default == D.AR_CAPTURE_DEFAULT_S == 300
    assert inspect.signature(look.capture).parameters["timeout_s"].default == D.LOOK_CAPTURE_DEADLINE_S
    assert inspect.signature(model_map.capture).parameters["timeout_s"].default == D.LOOK_CAPTURE_DEADLINE_S
    assert journal.CHECKPOINT_DEADLINE_S == D.CHECKPOINT_DEADLINE_S
    assert journal.READ_DEADLINE_S == D.READ_DEADLINE_S
    assert journal.SOFT_MAX_S == D.SOFT_MAX_S
    assert seeing.EXPORT_DEADLINE_S == D.EXPORT_DEADLINE_S
    assert seeing.RENDER_DEADLINE_S == D.RENDER_DEADLINE_S
    assert seeing.BOUNDS_DEADLINE_S == D.BOUNDS_DEADLINE_S


def test_capture_budgets_include_every_pass_and_kill_wait():
    look_time = D.export_steps_s() + 3 * (D.LOOK_CAPTURE_DEADLINE_S + D.CAPTURE_KILL_WAIT_S)
    assert D.inner_steps_s("compare_look_to_photos") == look_time
    assert loop.sdk_timeout_s("compare_look_to_photos") > look_time > 760
    assert D.inner_steps_s("arview_capture") == (
        D.export_steps_s() + D.AR_CAPTURE_DEADLINE_S + D.CAPTURE_KILL_WAIT_S)
    assert loop.sdk_timeout_s("arview_capture") > D.inner_steps_s("arview_capture") > 310
    assert D.inner_steps_s("edit_report") >= D.EXPORT_DEADLINE_S + 2 * D.look_steps_s()
    assert loop.sdk_timeout_s("edit_report") > 1800
    assert D.inner_steps_s("finish") >= (D.EXPORT_DEADLINE_S + D.AR_CAPTURE_DEADLINE_S +
        2 * D.LOOK_CAPTURE_DEADLINE_S + D.RENDER_DEADLINE_S)


def test_look_budget_counts_actual_photo_batches_and_preview_serial_options():
    one = D.inner_steps_s("compare_look_to_photos", photo_count=D.LOOK_CAMERA_BATCH_SIZE)
    two = D.inner_steps_s("compare_look_to_photos", photo_count=D.LOOK_CAMERA_BATCH_SIZE + 1)
    assert two - one == 2 * (D.LOOK_CAPTURE_DEADLINE_S + D.CAPTURE_KILL_WAIT_S)
    preview = D.inner_steps_s("preview_owner_edit")
    assert preview > (1 + D.PREVIEW_OPTIONS_MAX) * D.look_steps_s()
    options = [{"id": f"o{i}", "label": f"Option {i}", "scale": i + 1} for i in range(D.PREVIEW_OPTIONS_MAX + 1)]
    assert len(owner_preview._options(options[:-1])) == D.PREVIEW_OPTIONS_MAX
    with pytest.raises(ValueError, match="two or three"):
        owner_preview._options(options)
    # Unlike look, the labelled map sends every photo camera to one capture.
    assert D.inner_steps_s("labelled_model_map", photo_count=129) == D.inner_steps_s("labelled_model_map", photo_count=1)


def test_post_kill_wait_timeout_remains_a_capture_timeout(tmp_path, monkeypatch):
    calls = []

    def wait(timeout):
        calls.append(timeout)
        raise subprocess.TimeoutExpired("capture", timeout)

    proc = SimpleNamespace(wait=wait)
    job = SimpleNamespace(handle=1, terminate=lambda: calls.append("terminate"), close=lambda: calls.append("close"))
    monkeypatch.setattr(arview.procs, "spawn_in_job", lambda *a, **k: (proc, job))
    assert arview.run_capture(["node"], tmp_path, timeout_s=7) == (None, True)
    assert calls == [7, "terminate", D.CAPTURE_KILL_WAIT_S, "close"]


def test_cancelled_tool_keeps_identity_and_elapsed_time_until_real_completion(scene):
    entered, release, completed = threading.Event(), threading.Event(), threading.Event()

    def blocked_read(*args, **kwargs):
        entered.set()
        assert release.wait(5), "test failed to release its background operation"
        return {"status": "ok", "result": {"objects": []}}

    scene.s.call_read = blocked_read
    original_record = scene.record_call

    def recorded(*args):
        original_record(*args)
        completed.set()

    scene.record_call = recorded
    registry = {t.name: t for t in tools.build_tools(scene)}

    async def run():
        def invoke(name, cid):
            ctx = ToolContext(context=None, tool_name=name, tool_call_id=cid, tool_arguments="{}")
            return registry[name].on_invoke_tool(ctx, "{}")

        pending = asyncio.create_task(invoke("scene_summary", "slow-summary"))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
            output = await invoke("list_checkpoints", "next-call")
            busy = json.loads(output[0].text)
            assert busy["code"] == "E_BUSY"
            assert busy["running_tool"] == "scene_summary" and busy["running_call_id"] == "slow-summary"
            assert busy["elapsed_s"] >= 0 and busy["remaining_budget_s"] > 0
            assert "Wait" in busy["message"]
            assert "slow-summary" in scene.unseen
        finally:
            release.set()
        assert await asyncio.to_thread(completed.wait, 2)
        # Completion callback runs before finally releases the lock; a thread
        # join through a lock acquisition establishes that the cleanup finished.
        def lock_finished():
            with scene.lock:
                assert scene._running_tool is None
        await asyncio.to_thread(lock_finished)

    asyncio.run(run())


def test_busy_after_budget_elapsed_does_not_claim_completion(monkeypatch):
    source = SimpleNamespace(_running_tool=D.RunningTool("finish", "f1", 10, 20))
    monkeypatch.setattr(D.time, "monotonic", lambda: 45)
    result = D.busy_result(source)
    assert result["elapsed_s"] == 35 and result["remaining_budget_s"] == 0
    assert "remaining time is unknown" in result["message"]


def test_read_failure_budget_covers_real_fingerprint_and_restore_path(tmp_path):
    from glasses3d.tests.agent.test_task2_session_recovery import make_session
    session = make_session(tmp_path, call_error=W.WorkerError("RuntimeError", "read changed the scene"))
    leaves = []
    actual_call = session.worker.call

    def record(method, *args, **kwargs):
        leaves.append((method, kwargs["deadline_s"]))
        return actual_call(method, *args, **kwargs)

    session.worker.call = record
    result = session.call_read("scene_summary", "summary", {}, deadline_s=D.READ_DEADLINE_S, call_id="summary")
    assert result["restored_to"] == "c0000"
    assert leaves == [("summary", D.READ_DEADLINE_S), ("fingerprint", D.READ_DEADLINE_S),
                      ("checkpoint_restore", D.CHECKPOINT_DEADLINE_S)]
    assert D.inner_steps_s("scene_summary") >= sum(seconds for _, seconds in leaves)
    assert D.inner_steps_s("scene_summary") >= sum(seconds for _, seconds in leaves) + D.restart_steps_s()


def test_owner_scope_budget_includes_its_real_strict_export(tmp_path, monkeypatch):
    from glasses3d.agent import owner_edits
    leaves = []
    sealed = {"snapshot": {}, "all_operations": [], "source_fingerprint": "fp", "receipts": [],
              "authority_sha256": "authority"}
    authority = SimpleNamespace(resolve=lambda *a: sealed)
    source = SimpleNamespace(s=SimpleNamespace(head={"fingerprint": "fp"}), seeing=SimpleNamespace())
    def snapshot(*a):
        leaves.append(D.OWNER_SNAPSHOT_DEADLINE_S)
        return {"fingerprint": "fp"}, tmp_path / "snapshot.json"
    def export(*a):
        leaves.append(D.export_steps_s())
        return {"status": "exported", "validate_spec": {"ok": True}, "sha256": "export"}
    monkeypatch.setattr(owner_edits, "snapshot", snapshot)
    monkeypatch.setattr(owner_edits, "verify_snapshots", lambda *a: {"ok": True})
    monkeypatch.setattr(owner_edits, "confirmed_transition", lambda *a: [])
    source.seeing.export_head = export
    assert owner_edits.ScopeGuard(authority).verify(source)["ok"]
    assert leaves == [D.OWNER_SNAPSHOT_DEADLINE_S, D.export_steps_s()]
    assert D.owner_scope_steps_s() >= sum(leaves) + D.restore_steps_s()
    assert D.sdk_timeout_s("execute_blender_code", owner_edit=True) - D.sdk_timeout_s("execute_blender_code") == D.owner_scope_steps_s()
    assert D.inner_steps_s("edit_report") == (D.owner_scope_steps_s() + D.export_steps_s() +
        2 * D.look_steps_s() + D.worker_rpc_steps_s(D.READ_DEADLINE_S))


def test_lifecycle_sources_and_restart_attempts_are_in_budget(tmp_path, monkeypatch):
    from glasses3d.tests.agent.test_task2_session_recovery import make_session
    session = make_session(tmp_path)
    waits, starts, kills = [], [], []
    session.worker.kill = lambda why: kills.append(why)
    stale = SimpleNamespace(kill=lambda why: kills.append(why))
    session._spare = {"thread": SimpleNamespace(join=lambda timeout: waits.append(timeout)),
                      "worker": stale, "head": {"id": "old"}}

    def spawn(path):
        starts.append(path)
        return SimpleNamespace(pid=100 + len(starts), kill=lambda why: kills.append(why))

    def verify(head):
        if len(starts) == 1:
            raise journal.CheckpointMismatch("retry verified parent")

    monkeypatch.setattr(session, "_spawn", spawn)
    monkeypatch.setattr(session, "_verify_open", verify)
    out = session._after_death(W.WorkerDied("hard_timeout"), "summary", "summary", 0)
    assert out["status"] == "worker_restarted"
    assert waits == [D.J_SPARE_JOIN_S] and len(starts) == D.RESTARTS_PER_CALL == 2
    assert kills == ["stale spare", "hard_timeout", "restart not verified"]
    assert journal.J_SPARE_JOIN_S == D.J_SPARE_JOIN_S
    assert journal.RESTARTS_PER_CALL == D.RESTARTS_PER_CALL
    assert W.READY_TIMEOUT_S == D.READY_TIMEOUT_S and W.HELLO_TIMEOUT_S == D.HELLO_TIMEOUT_S
    assert W.RECEIPT_ATTEMPTS == D.RECEIPT_ATTEMPTS and W.WORKER_POLL_S == D.WORKER_POLL_S
    assert D.restart_steps_s() >= (sum(waits) + len(kills) * D.WORKER_KILL_WAIT_S + len(starts) * (
        D.READY_TIMEOUT_S + D.WORKER_POLL_S * D.RECEIPT_ATTEMPTS + D.HELLO_TIMEOUT_S + D.CHECKPOINT_DEADLINE_S))
    assert D.inner_steps_s("preview_owner_edit") > D.PREVIEW_OPTIONS_MAX * (
        D.worker_start_steps_s() + D.worker_close_steps_s())


def test_worker_close_uses_shared_shutdown_and_wait_limits(tmp_path):
    worker = W.WorkerClient(tmp_path)
    leaves = []
    worker.sock = object()
    def wait(seconds):
        leaves.append(("wait", seconds))
        raise subprocess.TimeoutExpired("fake worker", seconds)
    worker.proc = SimpleNamespace(poll=lambda: None, wait=wait)
    worker.call = lambda method, **kw: leaves.append((method, kw["deadline_s"]))
    worker.kill = lambda why: leaves.append(("kill", D.WORKER_KILL_WAIT_S))
    worker.close()
    assert leaves == [("shutdown", D.WORKER_SHUTDOWN_S), ("wait", D.WORKER_CLOSE_WAIT_S),
                      ("kill", D.WORKER_KILL_WAIT_S)]
    assert D.worker_close_steps_s() >= sum(seconds for _, seconds in leaves)


@pytest.mark.parametrize("deadline, succeeds", [(1.0, False), (2.0, True)])
def test_rpc_deadline_is_total_across_send_header_and_fragmented_body(tmp_path, monkeypatch, deadline, succeeds):
    from glasses3d.g3d import rpc
    now, waits, killed = [0.0], [], []
    payload = bytearray(rpc.encode({"id": 1, "ok": True, "result": {"status": "ok"}}))

    class FragmentSocket:
        timeout = None
        def settimeout(self, seconds):
            self.timeout = seconds
            waits.append(seconds)
        def delay(self):
            if self.timeout < 0.4:
                now[0] += self.timeout
                raise socket.timeout()
            now[0] += 0.4
        def sendall(self, data):
            self.delay()
        def recv(self, count):
            self.delay()
            count = min(count, 30)
            chunk = bytes(payload[:count])
            del payload[:count]
            return chunk

    monkeypatch.setattr(W.time, "monotonic", lambda: now[0])
    worker = W.WorkerClient(tmp_path)
    worker.sock = FragmentSocket()
    worker.proc = SimpleNamespace(poll=lambda: None)
    worker.kill = lambda why: killed.append(why)
    if succeeds:
        assert worker.call("summary", deadline_s=deadline) == {"status": "ok"}
        assert not killed
    else:
        with pytest.raises(W.WorkerDied, match="hard_timeout"):
            worker.call("summary", deadline_s=deadline)
        assert killed == ["hard_timeout"] and now[0] == deadline
    assert all(a > b for a, b in zip(waits, waits[1:]))


def test_cold_environment_budget_tracks_actual_fit_iterations_and_gain_render(tmp_path, monkeypatch):
    import numpy as np
    engine = object.__new__(seeing.Seeing)
    engine.root, engine.work = tmp_path, tmp_path / "work"
    engine.s = SimpleNamespace(head={"id": "c0"})
    cal = {"rid": "p", "box": [0, 0, 1, 1], "k": 1, "registration_mm": 0,
           "erode_native_px": 0, "ppm": 1}
    basis = SimpleNamespace(id="basis", frame={}, refs={"p": {"photo_sha256": "sha"}})
    calls = []
    rows = {"lin": np.ones((1, 1, 3)), "region": np.ones((1, 1), bool), "y": np.ones(1),
            "sigma_px": 1, "step_px": 1}
    monkeypatch.setattr(engine, "env_calibration", lambda *a: [cal])
    monkeypatch.setattr(engine, "_env_rows", lambda *a: rows)
    def render(call_id, cals, envs, origin, out_dir, tag):
        calls.append((tag, seeing.ENV_DEADLINE_S))
        return {("p", name): (np.ones((1, 1, 3)), np.ones((1, 1))) for name in envs}
    monkeypatch.setattr(engine, "_env_render", render)
    monkeypatch.setattr(seeing.L, "N_LOBES", 1)
    monkeypatch.setattr(seeing.C, "linear_gain_rgb", lambda *a: {"gain": [1, 1, 1], "gain_Y": 1})
    monkeypatch.setattr(seeing.L, "cell_samples", lambda *a: (None, [np.ones(1)]))
    monkeypatch.setattr(seeing.L, "cv_lambda", lambda *a: (1, [{"lambda": 1, "cv_rel_rms": 0}]))
    monkeypatch.setattr(seeing.L, "fit_env", lambda *a: {"weights": [1], "residuals": {}})
    monkeypatch.setattr(seeing.L, "residual_stats", lambda *a: {"p": {"rel_rms": 0}})
    engine._env_fit(basis, None, None, [0, 0, 0], "env", tmp_path, {}, "geometry")
    assert [tag for tag, _ in calls] == ["uniform", *[tag for i in range(D.ENV_ITERATIONS)
                                                    for tag in (f"basis-{i}", f"direct-{i}")]]
    assert len(calls) == 7
    # Execute the real cold gain entry point as well: one render, all gain views.
    (engine.work / "compare").mkdir(parents=True)
    monkeypatch.setattr(engine, "_light_frame", lambda cid: {})
    monkeypatch.setattr(engine, "gain_views", lambda *a: ({}, []))
    monkeypatch.setattr(engine, "gains_from_renders", lambda *a: {})
    def gain_read(*a, **kw):
        calls.append(("gain", kw["deadline_s"]))
        return {"status": "ok", "result": {"views": []}}
    engine.s.call_read = gain_read
    engine._matched_gains(basis, None, None, seeing.ENV_RIG, [0, 0, 0], "gain", tmp_path, "geometry", {"weights": [1]})
    assert calls[-1] == ("gain", D.RENDER_DEADLINE_S)
    assert D.matched_lighting_steps_s(seeing.ENV_RIG) >= sum(seconds for _, seconds in calls)
    assert D.matched_lighting_steps_s(seeing.MATCHED_RIG) >= calls[-1][1]
    for name in ("photo_compare", "facet_edit", "facet_visible_angle"):
        assert D.inner_steps_s(name, matched_rig=seeing.ENV_RIG) > sum(seconds for _, seconds in calls)
        assert D.inner_steps_s(name, matched_rig=seeing.MATCHED_RIG) < D.inner_steps_s(name, matched_rig=seeing.ENV_RIG)
    assert D.inner_steps_s("facet_visible_angle") == D.inner_steps_s("facet_edit")


@pytest.mark.parametrize("rig", [seeing.MATCHED_RIG, seeing.ENV_RIG])
def test_facet_registry_uses_its_rig_and_busy_keeps_the_real_operation(rig):
    from glasses3d.agent.temple_lab.tools import registry
    from glasses3d.tests.agent.test_temple_lab_v3 import FakeLab
    lab = FakeLab()
    lab.rig = rig
    entered, release = threading.Event(), threading.Event()
    def inspect_slowly():
        entered.set()
        assert release.wait(5)
        return {"status": "ok"}
    lab.inspect = inspect_slowly
    tools = {tool.name: tool for tool in registry(lab)}
    for name, tool in tools.items():
        assert tool.timeout_seconds == D.sdk_timeout_s(name, matched_rig=rig)

    async def run():
        def invoke(cid):
            ctx = ToolContext(context=None, tool_name="facet_inspect", tool_call_id=cid, tool_arguments="{}")
            return tools["facet_inspect"].on_invoke_tool(ctx, "{}")
        pending = asyncio.create_task(invoke("facet-running"))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
            output = await invoke("busy-facet")
            result = json.loads(output[0].text.rsplit("\n", 1)[0])
            assert result["code"] == "E_BUSY" and result["running_tool"] == "facet_inspect"
            assert result["running_call_id"] == "facet-running" and result["elapsed_s"] >= 0
            assert result["remaining_budget_s"] > 0 and "Wait before" in result["message"]
        finally:
            release.set()
        def joined():
            with lab.lock:
                assert lab._running_tool is None and lab.running is None
        await asyncio.to_thread(joined)
    asyncio.run(run())
