"""Nested stage evidence and cancellation controls; no model or Blender calls."""
import asyncio
import concurrent.futures
import hashlib
import json
import threading
from types import SimpleNamespace

from agents.tool import ToolOutputImage, ToolOutputText
from PIL import Image
import pytest

from glasses3d.agent.temple_lab.stage import StageBridge, StageLab


@pytest.mark.parametrize("effort", ["high", "xhigh", "max"])
def test_specialist_inherits_owner_effort_without_changing_budget(effort):
    from glasses3d.agent.loop import RunSettings
    from glasses3d.agent.temple_lab.stage import settings
    parent = RunSettings(effort=effort, settled_stop_usd="20", max_output_tokens=8192)
    child = settings(parent)
    assert child.effort == effort
    assert child.settled_stop_usd == "20" and child.max_output_tokens == 8192
    assert child.finish_tool == "facet_finish" and child.max_turns is None


def evidence_lab(tmp_path):
    lab = object.__new__(StageLab)
    lab.root = tmp_path.resolve()
    lab.state_lock = threading.RLock()
    lab.s = SimpleNamespace(head={"id": "c0001"})
    lab.state = {"images": {}, "heads": [], "best_checkpoint": None, "readings": {}}
    lab.save = lambda: None
    lab.fingerprint_of = lambda checkpoint: None
    return lab


def returned(lab, call, *, kind="compare", view="front", revision=1, ref="cmp001", reading=None, pixel="red"):
    path = lab.root / f"{call}.png"
    Image.new("RGB", (30, 20), pixel).save(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    lab.state["images"][digest] = {"kind": kind, "photo": view, "revision": revision, "ref": ref,
                                     **({"reading": reading} if reading else {})}
    output = lab.tool_output({"status": "ok", "images": [str(path)]}, "footer", call, "photo_compare")
    assert len(output) == 3
    assert isinstance(output[1], ToolOutputText) and isinstance(output[2], ToolOutputImage)
    expected_view = "temple chart" if kind == "chart" else view
    assert f"call {call}; checkpoint c0001; revision {revision}; view {expected_view}" in output[1].text
    return {"call_id": call, "part_index": 2, "sha256": digest}



def test_stage_charts_share_one_stable_view_despite_unique_inspection_tags(tmp_path):
    lab = evidence_lab(tmp_path)
    old = returned(lab, "old-chart", kind="chart", view="", ref="inspect001")
    current = returned(lab, "new-chart", kind="chart", view="", ref="inspect002", revision=2, pixel="blue")
    assert [row["view"] for row in lab.state["stage_images"]] == ["temple chart", "temple chart"]
    assert [row["call_id"] for row in lab.state["stage_images"]] == [old["call_id"], current["call_id"]]


def test_stage_bridge_runs_on_parent_loop_and_waits_for_cancellation_cleanup():
    async def exercise():
        parent = asyncio.get_running_loop()
        bridge = StageBridge(parent)
        entered = asyncio.Event()
        cleaned = threading.Event()

        class Runtime:
            async def run_stage(self, *args):
                assert asyncio.get_running_loop() is parent
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    await asyncio.sleep(.03)
                    cleaned.set()

        task = asyncio.create_task(asyncio.to_thread(bridge.run, Runtime(), None, None, "2", "outer"))
        await entered.wait()
        bridge.cancel()
        with pytest.raises((asyncio.CancelledError, concurrent.futures.CancelledError)):
            await task
        assert cleaned.is_set() and bridge.done.is_set()
    asyncio.run(exercise())


def test_stage_bridge_cancelled_before_submission_never_runs_inference():
    async def exercise():
        bridge = StageBridge(asyncio.get_running_loop())
        bridge.cancel()
        with pytest.raises(concurrent.futures.CancelledError):
            bridge.run(None, None, None, "2", "outer")
        assert bridge.future is None
    asyncio.run(exercise())


def orchestrated(tmp_path, monkeypatch, *, outcome_status="completed", stale=False, exit_change=None,
                 damage_export=False, cancel_after_prepare=False, finish_change=None,
                 brief_bytes=b"Owner wording retained", contract_bytes=b"Host contract wording",
                 check_input_binding=False, child_error=None):
    """Exercise the actual stage orchestrator with a private fake worker, never a second loop."""
    from glasses3d.agent.temple_lab import stage, cli, package
    root = tmp_path / "job"
    root.mkdir()
    applied, closed, private = [], [], []
    source = {"checkpoint": "parent-current", "checkpoint_sha256": "p" * 64, "fingerprint": "f" * 64}
    parent_head = {"id": source["checkpoint"], "sha256": source["checkpoint_sha256"]}

    def apply(*args, **kwargs):
        applied.append((args, kwargs))
        return {"status": "ok", "checkpoint": "parent-applied"}

    scene = SimpleNamespace(root=root, s=SimpleNamespace(head=parent_head,
        fingerprint=lambda: "changed" if stale else source["fingerprint"], call_mutating=apply))

    def prepare(_scene, folder, _call_id):
        inputs = folder / "inputs"
        inputs.mkdir(parents=True)
        (inputs / "brief.txt").write_bytes(brief_bytes)
        (inputs / "contract-brief.txt").write_bytes(contract_bytes)
        (folder / "job.json").write_text('{"schema":"g3d.job/1","name":"stage control"}', encoding="utf-8")
        return {"source": source}

    class FakeLab:
        def __init__(self, folder):
            private.append(self)
            self.root = folder
            self.s = SimpleNamespace(head={"id": "child-finished", "sha256": "c" * 64})
            export = folder / "finished.glb"
            export.write_bytes(b"finished private geometry")
            self.state = {"finish": {"checkpoint": "child-finished", "revision": 7, "status": "matched",
                "summary": "controlled finish", "validate_spec": {"ok": True},
                "export": {"path": str(export), "sha256": hashlib.sha256(export.read_bytes()).hexdigest()}}}
            self.state["finish"].update(finish_change or {})
            if damage_export:
                export.write_bytes(b"changed geometry")

        def start(self):
            pass

        def spec(self, revision):
            assert revision == 7
            return {"schema": "fitted-test-spec"}

        def close(self):
            closed.append(self)

    class Bridge:
        checks = 0

        def check(self):
            self.checks += 1
            if cancel_after_prepare and self.checks == 2:
                raise concurrent.futures.CancelledError("cancelled after capture")

        def run(self, runtime, spec, lab, cap, call_id):
            assert cap == "2" and call_id == "stage-control"
            assert spec.brief == brief_bytes.decode("utf-8")
            assert spec.contract_brief == contract_bytes.decode("utf-8")
            if child_error is not None:
                raise child_error
            if check_input_binding:
                from glasses3d.agent import approvals as A
                binding = A.run_input_binding(lab.root, spec.brief, [])
                assert binding["brief_sha256"] == hashlib.sha256(brief_bytes).hexdigest()
                normalized = spec.brief.replace("\r\n", "\n").replace("\r", "\n")
                if normalized != spec.brief:
                    with pytest.raises(A.ApprovalRefused, match="brief differs"):
                        A.run_input_binding(lab.root, normalized, [])
            return {"status": outcome_status, "exit_checkpoint": {"verified": True,
                "id": lab.s.head["id"], "sha256": lab.s.head["sha256"], **(exit_change or {})}}

    monkeypatch.setattr(package, "prepare", prepare)
    monkeypatch.setattr(stage, "StageLab", FakeLab)
    monkeypatch.setattr(cli, "check_package", lambda *_: None)
    monkeypatch.setattr(cli, "instructions_text", lambda *_: "controlled instructions")
    monkeypatch.setattr(cli, "references", lambda *_: [])
    monkeypatch.setattr(stage.T, "registry", lambda *_: [])
    return lambda: stage.run(scene, object(), Bridge(), "stage-control", "2", stage.settings()), applied, closed, private


def test_stage_orchestrator_applies_fitted_spec_once_to_captured_parent_and_closes(tmp_path, monkeypatch):
    run, applied, closed, private = orchestrated(tmp_path, monkeypatch)
    assert run()["status"] == "ok"
    assert len(applied) == 1 and closed == private and len(private) == 1
    args, kwargs = applied[0]
    assert args[:2] == ("temple_stage", "temple_stage_apply")
    assert args[2] == {"spec": {"schema": "fitted-test-spec"}, "source_fingerprint": "f" * 64}
    assert kwargs["call_id"] == "stage-control"


def test_failed_child_preserves_parent_and_records_progress_for_draft(tmp_path, monkeypatch):
    from glasses3d.agent.loop import StageRunFailed
    original = StageRunFailed('stage temple_stage ended: budget_limit')
    run, applied, closed, private = orchestrated(tmp_path, monkeypatch, child_error=original)
    with pytest.raises(StageRunFailed) as caught:
        run()
    assert caught.value is original and not applied and closed == private
    progress = original.stage_progress
    assert progress['parent_geometry_unchanged'] is True
    assert progress['checkpoint'] == 'child-finished' and progress['revision'] == 7
    assert progress['finish'] == 'matched' and progress['summary'] == 'controlled finish'
    saved = json.loads((tmp_path / 'job' / progress['package'] / 'stage-progress.json').read_text('utf-8'))
    assert saved == progress


@pytest.mark.parametrize("brief_bytes,contract_bytes", [
    (b"Owner wording\nkept exactly\n", b"Host contract\nwording\n"),
    (b"Owner wording\r\nkept exactly\r\n", b"Host contract\r\nwording\r\n"),
    ("Owner beige café \r\nsecond\nthird\rfourth".encode("utf-8"),
     b"Host contract\r\nsecond\nthird\rfourth"),
])
def test_stage_first_message_preserves_exact_saved_input_bytes(tmp_path, monkeypatch, brief_bytes, contract_bytes):
    run, applied, closed, private = orchestrated(tmp_path, monkeypatch,
        brief_bytes=brief_bytes, contract_bytes=contract_bytes, check_input_binding=True)
    assert run()["status"] == "ok"
    assert len(applied) == 1 and closed == private


@pytest.mark.parametrize("status", ["stage_budget_limit", "turn_limit", "failed"])
def test_incomplete_child_never_applies_and_closes(tmp_path, monkeypatch, status):
    run, applied, closed, private = orchestrated(tmp_path, monkeypatch, outcome_status=status)
    assert run()["code"] == "E_STAGE_INCOMPLETE"
    assert not applied and closed == private and len(private) == 1


def test_stale_parent_never_applies_and_closes(tmp_path, monkeypatch):
    run, applied, closed, private = orchestrated(tmp_path, monkeypatch, stale=True)
    assert run()["code"] == "E_STAGE_STALE"
    assert not applied and closed == private and len(private) == 1


@pytest.mark.parametrize("exit_change", [{"verified": False}, {"id": "another-head"}, {"sha256": "bad"}])
def test_child_exit_must_attest_exact_accepted_checkpoint(tmp_path, monkeypatch, exit_change):
    run, applied, closed, private = orchestrated(tmp_path, monkeypatch, exit_change=exit_change)
    with pytest.raises(ValueError, match="verified child head"):
        run()
    assert not applied and closed == private and len(private) == 1


def test_changed_child_export_never_applies(tmp_path, monkeypatch):
    run, applied, closed, private = orchestrated(tmp_path, monkeypatch, damage_export=True)
    with pytest.raises(ValueError, match="verified child head"):
        run()
    assert not applied and closed == private


@pytest.mark.parametrize("finish_change", [{"checkpoint": "wrong-head"}, {"status": "refused"},
                                           {"validate_spec": {"ok": False}}, {"validate_spec": {}}])
def test_finish_record_must_be_valid_accepted_current_head(tmp_path, monkeypatch, finish_change):
    run, applied, closed, private = orchestrated(tmp_path, monkeypatch, finish_change=finish_change)
    with pytest.raises(ValueError, match="verified child head"):
        run()
    assert not applied and closed == private


def test_cancellation_after_capture_never_starts_child_or_applies(tmp_path, monkeypatch):
    run, applied, closed, private = orchestrated(tmp_path, monkeypatch, cancel_after_prepare=True)
    with pytest.raises(concurrent.futures.CancelledError):
        run()
    assert not applied and not private and not closed
