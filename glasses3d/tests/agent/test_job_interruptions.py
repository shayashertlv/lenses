"""Interrupted ordinary runs publish only their own finalized stopped result, then preserve the signal."""
import asyncio
from datetime import datetime, timezone
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from glasses3d.agent import job_run as J, procs
from glasses3d.agent.loop import RESULT_SCHEMA


def fixture(tmp_path, monkeypatch, interrupted, *, smoke=False):
    root = tmp_path / "job"
    root.mkdir()
    run_id = "interrupted"
    path = root / "runs" / run_id / "result.json"
    scene = SimpleNamespace(s=SimpleNamespace(worker=None), finish=None)
    settings = J.settings_smoke() if smoke else J.settings_default()
    monkeypatch.setattr(J, "prepared_settings", lambda *a: (settings, "1" if smoke else "23"))
    monkeypatch.setattr(J, "prepare_run", lambda *a, **k: (object(), scene))
    now = datetime.now(timezone.utc).isoformat()
    result = {"schema": RESULT_SCHEMA, "job_id": root.name, "run_id": run_id, "status": "stopped",
              "error": {"type": type(interrupted).__name__, "message": str(interrupted)},
              "started": now, "finished": now, "teardown_errors": [],
              "exit_checkpoint": {"id": "c0001", "verified": True, "sha256": "a" * 64}}
    draft = Mock(return_value={"status": "delivered", "finished": False, "run_status": "stopped", "id": "draft"})
    monkeypatch.setattr(J.D, "deliver_draft", draft)
    return root, run_id, path, scene, result, draft


@pytest.mark.parametrize("signal", [asyncio.CancelledError, KeyboardInterrupt])
def test_finalized_interruption_publishes_before_same_exception_is_reraised(tmp_path, monkeypatch, signal):
    interrupted = signal("owner stopped")
    root, run_id, path, scene, result, draft = fixture(tmp_path, monkeypatch, interrupted)

    async def run(*a, **k):
        procs.write_json_atomic(path, result)
        raise interrupted

    monkeypatch.setattr(J, "run_astra", run)
    with pytest.raises(signal) as caught:
        asyncio.run(J.execute_run(root, run_id, scene=scene))
    assert caught.value is interrupted
    draft.assert_called_once_with(root, scene, result)
    assert json.loads(path.with_name("delivery.json").read_text()) == draft.return_value


@pytest.mark.parametrize("condition", ["preexisting", "missing", "malformed", "wrong_job", "wrong_run",
                                        "wrong_schema", "not_stopped", "no_teardown", "no_checkpoint_field",
                                        "no_finished", "bad_finished", "different_error", "worker_open"])
def test_ineligible_or_nonfresh_results_never_trigger_salvage(tmp_path, monkeypatch, condition):
    interrupted = asyncio.CancelledError("owner stopped")
    root, run_id, path, scene, result, draft = fixture(tmp_path, monkeypatch, interrupted)
    if condition == "preexisting":
        procs.write_json_atomic(path, result)
    elif condition == "wrong_job":
        result["job_id"] = "another-job"
    elif condition == "wrong_run":
        result["run_id"] = "another-run"
    elif condition == "wrong_schema":
        result["schema"] = "unknown"
    elif condition == "not_stopped":
        result["status"] = "failed"
    elif condition == "no_teardown":
        result.pop("teardown_errors")
    elif condition == "no_checkpoint_field":
        result.pop("exit_checkpoint")
    elif condition == "no_finished":
        result.pop("finished")
    elif condition == "bad_finished":
        result["finished"] = "not-a-timestamp"
    elif condition == "different_error":
        result["error"]["type"] = "StopRequested"
    elif condition == "worker_open":
        scene.s.worker = object()

    async def run(*a, **k):
        if condition == "malformed":
            path.parent.mkdir(parents=True)
            path.write_text("unfinished JSON{")
        elif condition != "missing":
            procs.write_json_atomic(path, result)
        raise interrupted

    monkeypatch.setattr(J, "run_astra", run)
    with pytest.raises(asyncio.CancelledError) as caught:
        asyncio.run(J.execute_run(root, run_id, scene=scene))
    assert caught.value is interrupted and not draft.called
    assert not path.with_name("delivery.json").exists()


@pytest.mark.parametrize("signal", [asyncio.CancelledError, KeyboardInterrupt, SystemExit])
def test_smoke_interruptions_never_publish_even_with_a_stopped_result(tmp_path, monkeypatch, signal):
    interrupted = signal("stop")
    root, run_id, path, scene, result, draft = fixture(tmp_path, monkeypatch, interrupted, smoke=True)

    async def run(*a, **k):
        procs.write_json_atomic(path, result)
        raise interrupted

    monkeypatch.setattr(J, "run_astra", run)
    with pytest.raises(signal) as caught:
        asyncio.run(J.execute_run(root, run_id, scene=scene, smoke=True))
    assert caught.value is interrupted and not draft.called and not path.with_name("delivery.json").exists()


def test_system_exit_failed_classification_is_preserved_and_not_salvaged(tmp_path, monkeypatch):
    interrupted = SystemExit(7)
    root, run_id, path, scene, result, draft = fixture(tmp_path, monkeypatch, interrupted)
    result["status"] = "failed"  # the existing loop classification is deliberately unchanged

    async def run(*a, **k):
        procs.write_json_atomic(path, result)
        raise interrupted

    monkeypatch.setattr(J, "run_astra", run)
    with pytest.raises(SystemExit) as caught:
        asyncio.run(J.execute_run(root, run_id, scene=scene))
    assert caught.value is interrupted and caught.value.code == 7 and not draft.called


def test_draft_failure_cannot_replace_the_original_interruption(tmp_path, monkeypatch):
    interrupted = KeyboardInterrupt("owner stopped")
    root, run_id, path, scene, result, draft = fixture(tmp_path, monkeypatch, interrupted)
    draft.side_effect = OSError("read-only output")

    async def run(*a, **k):
        procs.write_json_atomic(path, result)
        raise interrupted

    monkeypatch.setattr(J, "run_astra", run)
    with pytest.raises(KeyboardInterrupt) as caught:
        asyncio.run(J.execute_run(root, run_id, scene=scene))
    assert caught.value is interrupted and "read-only output" in interrupted.__notes__[0]
    assert not path.with_name("delivery.json").exists()


def test_unusable_checkpoint_attempt_is_still_persisted_on_cancel(tmp_path, monkeypatch):
    interrupted = asyncio.CancelledError()
    root, run_id, path, scene, result, draft = fixture(tmp_path, monkeypatch, interrupted)
    draft.return_value = {"status": "no_usable_checkpoint", "finished": False, "reason": "empty scene"}

    async def run(*a, **k):
        procs.write_json_atomic(path, result)
        raise interrupted

    monkeypatch.setattr(J, "run_astra", run)
    with pytest.raises(asyncio.CancelledError) as caught:
        asyncio.run(J.execute_run(root, run_id, scene=scene))
    assert caught.value is interrupted
    assert json.loads(path.with_name("delivery.json").read_text()) == draft.return_value


def test_actual_sdk_cancellation_publishes_finalized_loop_receipt_after_scene_close(tmp_path, monkeypatch):
    """Use the real SDK/ledger/teardown producer; only the scene and final asset packaging are faked."""
    from glasses3d.agent import loop as L, replay as R
    from glasses3d.tests.agent.loop_support import FakeScene, make_job, make_tools, spec_for

    class ClosingScene(FakeScene):
        def __init__(self):
            super().__init__()
            self.s = SimpleNamespace(worker=None)

        def start(self):
            self.s.worker = object()
            return super().start()

        def close(self):
            super().close()
            self.s.worker = None

    scene, tools = make_tools(ClosingScene())
    settings = L.RunSettings(effort="max", max_output_tokens=4096, max_turns=10)
    run_id = "20261007T120000-run001"
    root, sha = make_job(tmp_path, tools=tools, settings=settings, run_id=run_id)
    transport = R.ScriptedOpenAI([R.tool_call("slow_tool", {"seconds": 30}), R.message("never sent")])
    spec = spec_for(root, run_id, sha, tools, transport, settings=settings)
    monkeypatch.setattr(J, "prepared_settings", lambda *a: (settings, "5"))
    monkeypatch.setattr(J, "prepare_run", lambda *a, **k: (spec, scene))
    assert J.run_astra is L.run_astra
    attempt = {"status": "no_usable_checkpoint", "finished": False, "reason": "synthetic empty scene"}
    published = []

    def publish(job, actual_scene, result):
        assert job == root and actual_scene is scene
        assert scene.events == ["start", "exit_checkpoint", "close"] and scene.s.worker is None
        assert result["schema"] == RESULT_SCHEMA and result["status"] == "stopped"
        assert result["job_id"] == root.name and result["run_id"] == run_id
        assert result["error"]["type"] == "CancelledError" and result["teardown_errors"] == []
        assert datetime.fromisoformat(result["finished"]) >= datetime.fromisoformat(result["started"])
        assert result["exit_checkpoint"]["verified"] is True
        published.append(result)
        return attempt

    monkeypatch.setattr(J.D, "deliver_draft", publish)
    started = root / "runs" / run_id / "tools" / "0001-slow_tool" / "arguments.json"

    async def scenario():
        task = asyncio.create_task(J.execute_run(root, run_id, scene=scene))
        for _ in range(600):
            if started.exists():
                break
            await asyncio.sleep(0.05)
        assert started.exists(), "the scripted SDK tool never started"
        task.cancel("owner stopped")
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert len(published) == 1
    assert len(transport.creates) == 1 and not transport.violations and not transport.unexpected
    directory = root / "runs" / run_id
    assert json.loads((directory / "result.json").read_text()) == published[0]
    assert json.loads((directory / "delivery.json").read_text()) == attempt
