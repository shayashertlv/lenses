"""Full-item preparation, normal tools and real SDK admission; no Blender or paid clients."""
import asyncio
import copy
from dataclasses import replace
import json
from pathlib import Path
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from agents import function_tool
from agents.tool_context import ToolContext

from glasses3d.agent import approvals as A, cli, job_run, jobs, owner_fallback as F, replay as R
from glasses3d.agent import loop as L, tools as T
from glasses3d.agent.job_scene import JobScene
from glasses3d.tests.agent.loop_support import FakeScene
from glasses3d.tests.agent.support import usage
from glasses3d.tests.proofs.offline_job_approval import approve_offline_request
from .test_job_cli import job
from .test_job_tools import scene, invoke, record
from .test_m12_capture_transport import fixture_records


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("G3D_OFFLINE", "1")
    monkeypatch.setattr(L, "ProductionClients", lambda *a, **k: pytest.fail("production client forbidden"))


@pytest.fixture
def fallback(job, monkeypatch):
    captured, _ = fixture_records(job, monkeypatch, (32, 24))
    doc = {"schema": F.SCHEMA, "mode": "full_edit_item", "origin": "full_edit_item", "item_id": "I1",
           "source": {"fingerprint": "f" * 64}, "owner_words": captured["request_text"],
           "understanding_sha256": "d" * 64,
           "item_interpretation": {"item_id": "I1", "interpretation": "Move the marked corner inward.",
                                   "disposition": "cannot_preview", "reason": "Needs free modeling."},
           "owner_mark_resolutions": [], "owner_capture_items": [{"item_id": "I1"}]}
    path = job / F.RELATIVE_PATH
    path.write_text(json.dumps(doc), encoding="utf-8")
    # Creation/provenance refusals use the real facade in test_m12_fallback.py. Here isolate runtime contracts.
    monkeypatch.setattr(F, "load", lambda root: json.loads((Path(root) / F.RELATIVE_PATH).read_text(encoding="utf-8"))
                        if (Path(root) / F.RELATIVE_PATH).is_file() else None)
    return job


def prepared(root, cutoff="27.50", **kwargs):
    return job_run.prepare_run(root, "runtime", cap=cutoff, write_request=True, **kwargs)


def request(root, run="runtime"):
    return json.loads((root / "runs" / run / "approval-request.json").read_text(encoding="utf-8"))


def command(*args):
    return cli.dispatch(cli.parser().parse_args(["job", *map(str, args)]))


def test_mode_has_exact_normal_tools_and_ordinary_scene_authority(fallback):
    spec, actual = prepared(fallback)
    normal = T.build_tools(JobScene(fallback), spec.settings)
    assert actual.owner_edit is actual.owner_edit_authority is None
    assert actual.full_edit_item["item_id"] == "I1"
    assert L.tool_registry_sha256(spec.tools, spec.settings) == L.tool_registry_sha256(normal, spec.settings)
    assert [tool.name for tool in spec.tools] == [tool.name for tool in normal]
    assert {"execute_blender_code", "fit_cameras", "temple_stage", "photo_print", "finish"} <= {
        tool.name for tool in spec.tools}
    assert spec.settings.finish_tool == "finish" and "understand_finish" not in {t.name for t in spec.tools}
    assert spec.instructions.endswith(F.instructions_suffix())
    assert L.contract_problems(spec.contract, instructions=spec.instructions,
                               tools=spec.tools, brief=spec.contract_brief) == []


def test_capture_roles_words_and_typed_cutoff_survive_preparation_and_reload(fallback):
    spec, _ = prepared(fallback)
    doc = F.load(fallback)
    assert spec.settings.effort == "high" and spec.settings.settled_only
    assert spec.settings.settled_stop_usd == "27.50"
    assert doc["owner_words"] in spec.brief and doc["understanding_sha256"] in spec.brief
    assert "existing delivered model" in spec.instructions and "not a product" in spec.instructions
    assert "I1" in spec.scene_note and "full_edit_item" in spec.scene_note
    assert [ref.evidence_kind for ref in spec.photos] == ["product_photo", "owner_capture", "owner_capture"]
    binding = request(fallback)["request"]["run_inputs"]
    assert binding["full_edit_item"] == {"mode": "full_edit_item", "item_id": "I1",
                                        "descriptor_sha256": A.file_sha256(fallback / F.RELATIVE_PATH)}
    for row in binding["photos"][1:]:
        assert row["evidence_metadata"]["native_pixels"] is True
    restored, cap = job_run.prepared_settings(fallback, spec.run_id)
    again, _ = job_run.prepare_run(fallback, spec.run_id, settings=restored, cap=cap)
    assert again.current_request_sha256 == spec.current_request_sha256
    assert cap == "27.500000"
    for stage in request(fallback)["request"]["settings"]["stages"]:
        assert stage["settings"]["effort"] == "high" and stage["settings"]["settled_only"] is True
        assert stage["maximum_micro_usd"] == 27_500_000


@pytest.mark.parametrize("changes", [{"effort": "max"}, {"settled_only": False}, {"settled_only": "yes"}, {"smoke": True},
                                     {"fit_output_to_budget": True}])
def test_incompatible_mode_terms_refuse_before_request(fallback, changes):
    settings = replace(job_run.settings_full_edit_item("27.50"), **changes)
    with pytest.raises(ValueError):
        prepared(fallback, settings=settings)
    assert not (fallback / "runs/runtime").exists()


@pytest.mark.parametrize("cutoff", ["0", "-1", "NaN", "Infinity", "bad", True, "0.0000001"])
def test_invalid_typed_cutoff_refuses_before_request(fallback, cutoff):
    from glasses3d.agent.budget import BudgetGuardError
    with pytest.raises((ValueError, BudgetGuardError)):
        prepared(fallback, cutoff)
    assert not (fallback / "runs/runtime").exists()


def test_resume_is_not_permission_to_reuse_item_approval(fallback):
    with pytest.raises(ValueError, match="fresh separately approved"):
        prepared(fallback, resume=True)


def test_changed_cutoff_changes_bound_request(fallback):
    first, _ = prepared(fallback)
    changed, _ = job_run.prepare_run(fallback, cap="28", settings=job_run.settings_full_edit_item("28"))
    assert first.current_request_sha256 != changed.current_request_sha256


@pytest.mark.parametrize("change", ["item", "understanding", "capture", "missing"])
def test_changed_item_inputs_refuse_even_with_cached_request_hash(fallback, change):
    spec, _ = prepared(fallback)
    transport = R.ScriptedOpenAI([])
    run = fallback / "runs" / spec.run_id
    approve_offline_request(run, transport.handle, cap="27.50")
    if change == "capture":
        image = fallback / "inputs/owner_captures/C1/annotated.png"
        image.write_bytes(image.read_bytes() + b"changed")
    elif change == "missing":
        (fallback / F.RELATIVE_PATH).unlink()
    else:
        path = fallback / F.RELATIVE_PATH
        value = F.load(fallback)
        value["item_id" if change == "item" else "understanding_sha256"] = "I2" if change == "item" else "e" * 64
        path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(A.ApprovalRefused, match="changed|missing"):
        A.check_approval(run, current_request_sha256=spec.current_request_sha256, requested_cap_usd="27.50")
    assert not (run / "budget.json").exists()


def test_approval_writer_refuses_changed_fallback_before_writing_authority(fallback, monkeypatch):
    spec, _ = prepared(fallback)
    (fallback / F.RELATIVE_PATH).unlink()
    monkeypatch.setattr(A, "create_json_exclusive", lambda *a: pytest.fail("must not write authority"))
    with pytest.raises(A.ApprovalRefused, match="missing"):
        A.write_approval(fallback / "runs" / spec.run_id, max_usd="27.50", owner_words="Unused test words")


@pytest.mark.parametrize("matches", [True, False])
@pytest.mark.parametrize("checkpoints", [1, 3])
def test_seed_fingerprint_is_checked_before_main_scene_is_used(fallback, monkeypatch, matches, checkpoints):
    scene = job_run._FullEditItemScene(fallback, job=jobs.load_job(fallback), fallback=F.load(fallback))
    close = Mock()
    scene.s = SimpleNamespace(index=SimpleNamespace(checkpoints=lambda: ["checkpoint"] * checkpoints), close=close,
                              head={"fingerprint": "f" * 64 if matches else "a" * 64})
    monkeypatch.setattr(JobScene, "start", lambda self: {"head": self.s.head})
    if matches:
        assert scene.start()["head"] == scene.s.head
        close.assert_not_called()
    else:
        with pytest.raises(ValueError, match="delivered fingerprint"):
            scene.start()
        close.assert_called_once()


def test_full_item_tools_do_not_acquire_confirmed_edit_restrictions(scene, fallback):
    scene.root = fallback
    scene.full_edit_item = {"item_id": "I1"}
    assert record(invoke(scene, "execute_blender_code", {"code": "result = 1", "label": "one item"}))["status"] == "ok"
    scene.seeing.fit_cameras = lambda *args, **kw: {"status": "ok"}
    assert record(invoke(scene, "fit_cameras", {"reason": "Inspect the requested edit", "workers": 1}))["status"] == "ok"


def test_cli_prepares_only_the_requested_item_with_own_cutoff(fallback, monkeypatch):
    factory = Mock(return_value=fallback)
    monkeypatch.setattr(F, "create_full_edit_item", factory)
    out = command("edit-item", fallback, "--item", "I1", "--cap", "35", "--settled-stop", "35",
                  "--settled-only", "--effort", "high")
    factory.assert_called_once_with(fallback, "I1", destination=None)
    assert out["phase"] == "full_edit_item" and out["item_id"] == "I1"
    assert out["status"] == "awaiting_separate_run_approval"
    assert out["request"]["requested_cap_usd"] == "35.000000"
    assert not (fallback / "runs" / out["run_id"] / "approval.json").exists()


def test_cli_bad_cutoff_refuses_before_creating_copy(fallback, monkeypatch):
    factory = Mock()
    monkeypatch.setattr(F, "create_full_edit_item", factory)
    with pytest.raises(ValueError, match="cap must equal"):
        command("edit-item", fallback, "--item", "I1", "--cap", "35", "--settled-stop", "34", "--settled-only")
    factory.assert_not_called()


def test_cli_uses_existing_detached_runner_and_stop(fallback, monkeypatch):
    spec, _ = prepared(fallback)
    approve_offline_request(fallback / "runs" / spec.run_id, R.ScriptedOpenAI([]).handle, cap="27.50")
    spawned = Mock(return_value=SimpleNamespace(record=lambda: {"pid": 1234}))
    monkeypatch.setattr(cli.procs, "spawn_detached", spawned)
    result = command("run", fallback, "--run-id", spec.run_id)
    assert result["status"] == "started" and result["pid"] == 1234
    argv = spawned.call_args.args[0]
    assert argv[1:] == ["-m", "glasses3d", "job", "run", str(fallback), "--run-id", spec.run_id, "--foreground"]
    assert spawned.call_args.kwargs["keep_awake"] is True
    assert command("stop", fallback)["status"] == "stop_requested"
    assert (fallback / "stop.requested").is_file()


@pytest.mark.parametrize("end", ["completed", "budget_limit", "stopped"])
def test_publication_keeps_mode_and_item_for_ordinary_finish_and_draft(fallback, monkeypatch, end):
    scene = SimpleNamespace(full_edit_item=F.load(fallback), owner_edit=None)
    calls = []
    def publish(root, actual, result, **kw):
        calls.append(copy.deepcopy(result))
        return {"status": "delivered", "origin": result["mode"], "item_id": result["item_id"]}
    monkeypatch.setattr(job_run, "deliver_finished", publish)
    monkeypatch.setattr(job_run.D, "deliver_draft", publish)
    out = job_run._publish_result(fallback, "published", scene, {"status": end})
    assert out["origin"] == "full_edit_item" and out["item_id"] == "I1"
    assert calls == [{"status": end, "mode": "full_edit_item", "item_id": "I1"}]


def test_real_sdk_cutoff_crossing_stops_before_next_request_and_writes_heartbeat(fallback):
    scene = FakeScene()
    scene.root, scene.job = fallback, jobs.load_job(fallback)
    scene.s = SimpleNamespace(head={"id": "c0000", "fingerprint": "f" * 64})
    scene.lock = threading.RLock()
    scene.record_call = lambda *args: None
    transport = R.ScriptedOpenAI([
        R.tool_call("budget_status", counted=1000, usage=usage(tokens=1000, written=1000, output=0)),
        R.tool_call("budget_status"),
    ])
    spec, _ = prepared(fallback, "0.01", scene=scene, offline_handler=transport.handle)
    run = fallback / "runs" / spec.run_id
    approve_offline_request(run, transport.handle, cap="0.01")
    result = asyncio.run(L.run_astra(spec, scene=scene))
    assert result["status"] == "budget_limit", result
    assert result["requests"] == len(transport.creates) == len(transport.counts) == 1
    assert result["budget"]["settled_micro_usd"] > 10_000
    assert result["budget"]["settled_only"] is True
    saved = json.loads((run / "result.json").read_text())
    assert saved["mode"] == "full_edit_item" and saved["item_id"] == "I1"
    assert transport.violations == transport.unexpected == []
    assert (fallback / "heartbeat.json").is_file()
    assert scene.events[-2:] == ["exit_checkpoint", "close"]
    assert json.loads((run / "approval.json").read_text())["authorized_by"] == "offline_fixture"


@pytest.mark.parametrize("head", [{"id": "changed", "fingerprint": "e" * 64}, {"id": "unverifiable"}])
def test_injected_scene_cannot_bypass_source_fingerprint_before_approval_consumption(fallback, head):
    scene = FakeScene()
    scene.root, scene.job = fallback, jobs.load_job(fallback)
    scene.s = SimpleNamespace(head=head)
    transport = R.ScriptedOpenAI([])
    spec, _ = prepared(fallback, scene=scene, offline_handler=transport.handle)
    run = fallback / "runs" / spec.run_id
    approve_offline_request(run, transport.handle, cap="27.50")
    with pytest.raises(L.RunRefused, match="exact delivered source fingerprint"):
        asyncio.run(L.run_astra(spec, scene=scene))
    assert scene.events[-1] == "close"
    assert not (run / "budget.json").exists()
    assert transport.creates == transport.counts == []


def test_real_sdk_specialist_receives_bound_item_and_native_captures_with_shared_cutoff(fallback, monkeypatch):
    child_scene = FakeScene()
    @function_tool
    async def facet_finish():
        """Accept a scripted specialist fixture, not a product result."""
        return json.dumps({"status": "accepted"})
    settings = job_run.settings_full_edit_item("0.03")
    child = L.StageSpec("temple_stage", "Scripted fixture. End with facet_finish.", [facet_finish],
                        "Product photos and the current model.", replace(settings, finish_tool="facet_finish"),
                        photos=jobs.references(fallback), contract=L.ToolContract(frozenset({"facet_finish"})))
    contract = L.StageContract.from_spec(child, "0.03")
    outcomes = []
    @function_tool
    async def temple_stage(ctx: ToolContext):
        """Run the scripted specialist through the existing shared runtime."""
        result = await ctx.context.run_stage(child, child_scene, "0.03", ctx.tool_call_id)
        outcomes.append(result)
        return json.dumps(result)
    ordinary_tools = job_run.build_tools
    monkeypatch.setattr(job_run, "build_tools", lambda scene, settings: [
        temple_stage if tool.name == "temple_stage" else tool for tool in ordinary_tools(scene, settings)])
    monkeypatch.setattr(job_run.temple_stage, "contract", lambda *a, **kw: contract)
    renewal_specs = []
    renewal_class = L.ConversationRenewal
    def renewal(**kwargs):
        renewal_specs.append(kwargs["spec"])
        return renewal_class(**kwargs)
    monkeypatch.setattr(L, "ConversationRenewal", renewal)
    scene = FakeScene()
    scene.root, scene.job = fallback, jobs.load_job(fallback)
    scene.s = SimpleNamespace(head={"id": "source", "fingerprint": "f" * 64})
    scene.lock = threading.RLock()
    scene.record_call = lambda *args: None
    transport = R.ScriptedOpenAI([
        R.tool_call("temple_stage", counted=1000, usage=usage(tokens=1000, written=1000, output=0)),
        R.tool_call("facet_finish", counted=2000, usage=usage(tokens=2000, written=2000, output=0)),
        R.tool_call("budget_status"),
    ])
    spec, _ = prepared(fallback, "0.03", settings=settings, scene=scene, offline_handler=transport.handle)
    run = fallback / "runs" / spec.run_id
    approve_offline_request(run, transport.handle, cap="0.03")
    result = asyncio.run(L.run_astra(spec, scene=scene))
    assert result["status"] == "budget_limit", result
    assert len(transport.creates) == len(transport.counts) == result["requests"] == 2
    assert result["budget"]["settled_micro_usd"] == 37_500
    assert outcomes[0]["status"] == "completed" and outcomes[0]["budget"]["request_sequences"] == [2]
    sent_child = next(item for item in renewal_specs if isinstance(item, L.StageSpec))
    assert L.StageContract.from_spec(sent_child, "0.03") == contract
    assert sent_child.instructions == child.instructions
    assert sent_child.brief == child.brief + F.specialist_suffix(F.load(fallback))
    captures = [ref for ref in spec.photos if ref.evidence_kind == "owner_capture"]
    assert list(sent_child.photos) == [*child.photos, *captures]
    def objects(value):
        if isinstance(value, dict):
            yield value
            for item in value.values():
                yield from objects(item)
        elif isinstance(value, list):
            for item in value:
                yield from objects(item)
    submitted = list(objects(transport.creates[1]["input"]))
    assert all(any(row.get("image_url") == photo.image_url for row in submitted) for photo in captures)
    assert any(F.load(fallback)["owner_words"] in row.get("text", "") for row in submitted)
    assert transport.violations == transport.unexpected == []
