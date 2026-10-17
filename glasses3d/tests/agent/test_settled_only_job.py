"""Settled-only job approval and actual SDK parent/child wiring, entirely offline."""
import asyncio
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from agents import function_tool
from agents.tool_context import ToolContext
from PIL import Image
import pytest

from glasses3d.agent import approvals as A, brief, cli, job_run, jobs, loop as L, replay as R
from glasses3d.agent import finish_runway
from glasses3d.tests.agent.loop_support import FakeScene, tool_instructions
from glasses3d.tests.agent.support import OFFLINE_KEY, usage


@pytest.fixture(autouse=True)
def no_production(monkeypatch):
    monkeypatch.setattr(L, "ProductionClients", lambda *a, **k: pytest.fail("production client forbidden"))


@pytest.fixture
def job(tmp_path):
    source = tmp_path / "photos"
    source.mkdir()
    Image.new("RGB", (23, 11), "navy").save(source / "front.png")
    views = tmp_path / "views.json"
    views.write_text(json.dumps({"front.png": {"kind": "front", "side": "both"}}))
    return jobs.create_job(images=source, views=views, name="Settled-only proof",
                           description="Synthetic product.", jobs_root=tmp_path / "jobs")


def command(*args):
    return cli.dispatch(cli.parser().parse_args(["job", *map(str, args)]))


def inputs(root):
    paths = [root / "job.json", *(root / "images").rglob("*"),
             *(root / "reference_images").rglob("*"), *(root / "inputs").rglob("*")]
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in paths if path.is_file()}


@pytest.mark.parametrize("cap_args", [(), ("--cap", "20")])
def test_cli_estimate_approve_restore_binds_twenty_dollars_and_high_parent_child(job, cap_args):
    before = inputs(job)
    request = command("estimate", job, "--settled-only", "--settled-stop", "20", "--effort", "high", *cap_args)
    settings = request["request"]["settings"]
    assert request["requested_cap_micro_usd"] == 20_000_000
    assert request["requested_cap_usd"] == "20.000000"
    for selected in (settings, settings["stages"][0]["settings"]):
        assert selected["settled_only"] is True and selected["settled_stop_usd"] == "20.000000"
        assert selected["effort"] == "high" and selected["max_output_tokens"] == 16_384
        assert selected["max_turns"] is None and not selected.get("fit_output_to_budget", False)
        assert "context_guard" not in selected
    assert settings["stages"][0]["maximum_micro_usd"] == 20_000_000
    command("approve", job, "--run-id", request["run_id"], "--max-usd", "20",
            "--owner-words", "Offline fixture: stop new requests at $20 settled only.")
    restored, cap = job_run.prepared_settings(job, request["run_id"])
    assert restored.settled_only and restored.effort == "high" and cap == "20.000000"
    rebuilt, _ = job_run.prepare_run(job, request["run_id"], settings=restored, cap=cap)
    assert rebuilt.current_request_sha256 == request["request_sha256"]
    approval = A.check_approval(job / "runs" / request["run_id"],
                                current_request_sha256=rebuilt.current_request_sha256, requested_cap_usd=cap)
    assert approval.max_micro == approval.requested_cap_micro == 20_000_000
    assert approval.settled_only and approval.binding()["settled_only"] is True
    assert inputs(job) == before
    assert not (job / "runs" / request["run_id"] / "budget.json").exists()
    assert not (job / "inputs/seed.blend").exists()


@pytest.mark.parametrize("initial_mode", [False, True])
def test_money_mode_change_requires_fresh_approval(job, initial_mode):
    settings = replace(job_run.settings_default(), effort="high", settled_only=initial_mode)
    spec, _ = job_run.prepare_run(job, "mode-change", settings=settings, cap="20", write_request=True)
    run = job / "runs" / spec.run_id
    A.write_approval(run, max_usd="20", owner_words="Offline fixture approval.")
    changed, _ = job_run.prepare_run(job, spec.run_id, settings=replace(settings, settled_only=not initial_mode),
                                     cap="20")
    assert changed.current_request_sha256 != spec.current_request_sha256
    with pytest.raises(A.ApprovalRefused, match="changed since"):
        A.check_approval(run, current_request_sha256=changed.current_request_sha256, requested_cap_usd="20")
    assert not (run / "budget.json").exists()


@pytest.mark.parametrize("cap,changes", [("44.2788", {}), ("20", {"fit_output_to_budget": True}),
                                         ("20", {"smoke": True})])
def test_job_rejects_mixed_reserve_output_fitting_or_smoke(job, cap, changes):
    settings = replace(job_run.settings_default(), effort="high", settled_only=True, **changes)
    with pytest.raises(ValueError, match="settled-only approval amount"):
        job_run.prepare_run(job, "invalid-mode", settings=settings, cap=cap, write_request=True)
    assert not (job / "runs/invalid-mode").exists()


@pytest.mark.parametrize("settled", [0, 19_990_000])
def test_status_footer_and_invocation_show_only_actual_settled_stop(monkeypatch, settled):
    summary = {"settled_only": True, "settled_micro_usd": settled, "maximum_micro_usd": 20_000_000,
               "held_micro_usd": 0, "unknown_micro_usd": 0, "settled_stop_usd": "20.000000",
               "remaining_to_stop_usd": f"{(20_000_000 - settled) / 1e6:.6f}",
               "planning": {"last_counted_input_tokens": 922_000}}
    ctx = L.RunStatus(SimpleNamespace(summary=lambda: summary))
    ctx.configure_finish(SimpleNamespace(job={"images": [{}] * 6}))
    monkeypatch.setattr(finish_runway, "project", lambda *a, **k: pytest.fail("reserve projection in settled-only run"))
    evidence = {"source": "fixture", "image_tokens": 1000}
    ctx.note_finish_evidence(evidence)
    assert ctx.finish_runway(evidence=evidence, review_ready=True) is None
    assert ctx.numbers()["settled_only"] is True
    stale_projection = {"status": "unavailable", "message": "Finish-review reserve warning: stale projection"}
    for text in (ctx.footer(), ctx.footer(finish_runway=stale_projection),
                 L.footer_text(**ctx.numbers(), finish_runway=stale_projection)):
        assert "$20.00" in text and "no reservation cutoff" in text
        assert "reserve cap" not in text and "Finish-review reserve" not in text
        assert "stale projection" not in text
    invocation = L.invocation_text(run_id="offline", scene_note="Synthetic scene.", cap_micro=20_000_000,
                                    rollback=None, settled_stop_micro=20_000_000, settled_only=True)
    assert "$20.000000" in invocation and "shared settled charges" in invocation
    assert "do not refuse requests or shrink responses" in invocation
    assert "final in-flight request may settle above" in invocation
    assert "reserve cap" not in invocation


def test_real_sdk_parent_and_specialist_share_settled_stop_without_reserve_refusal(job):
    """At $19.99 settled a full child request is sent, then its settlement stops the parent before counting."""
    child_scene, stages = FakeScene(), []
    before = inputs(job)
    settings = L.RunSettings(effort="high", max_output_tokens=16_384, max_turns=None,
                              settled_stop_usd="20", settled_only=True, tool_choice="required",
                              reset_tool_choice=False)

    @function_tool
    async def facet_finish():
        """Accept the synthetic specialist result."""
        return json.dumps({"status": "accepted"})

    refs = jobs.references(job)
    text = brief.build_brief(jobs.load_job(job))
    child_tools = [facet_finish]
    child = L.StageSpec("temple", tool_instructions(child_tools, "Synthetic specialist."), child_tools, text,
                        replace(settings, finish_tool="facet_finish"), photos=refs,
                        contract=L.ToolContract(frozenset({"facet_finish"})))
    contract = L.StageContract.from_spec(child, "20")

    @function_tool(failure_error_function=L.fatal_tool_errors(*L.STAGE_FATAL))
    async def temple_stage(ctx: ToolContext):
        """Run the synthetic temple specialist under the shared settled stop."""
        assert ctx.context.guard.summary()["settled_micro_usd"] == 19_990_000
        result = await ctx.context.run_stage(child, child_scene, "0.01", ctx.tool_call_id)
        stages.append(result)
        return result

    tools = [temple_stage]
    instructions = tool_instructions(tools)
    request = A.build_request(job, instructions_sha256=L.instructions_sha256(instructions),
                              tool_registry_sha256=L.tool_registry_sha256(tools, settings),
                              settings=L.approval_settings(settings, (contract,)),
                              run_inputs=A.run_input_binding(job, text, refs))
    run = job / "runs/shared-settled-stop"
    A.write_approval_request(run, job_id=job.name, request=request, estimate={}, suggested_cap_usd="20")
    A.write_approval(run, max_usd="20", owner_words="Offline fixture: shared settled-only cutoff.")
    transport = R.ScriptedOpenAI([
        R.tool_call("temple_stage", counted=799_600, usage=usage(tokens=799_600, written=799_600, output=0)),
        R.tool_call("facet_finish", counted=922_000, usage=usage(tokens=922_000, written=922_000, output=0)),
    ])
    spec = L.RunSpec(job, run.name, instructions, tools, text, A.request_sha256(request), photos=refs,
                     settings=settings, requested_cap_usd="20", stage_contracts=(contract,),
                     contract=L.ToolContract(frozenset({"temple_stage"})), offline_handler=transport.handle,
                     offline_api_key=OFFLINE_KEY)
    result = asyncio.run(L.run_astra(spec, scene=FakeScene()))
    assert result["status"] == "budget_limit", result
    assert result["requests"] == len(transport.counts) == len(transport.creates) == 2
    assert transport.violations == transport.unexpected == []
    assert all(body["reasoning"]["effort"] == "high" and body["max_output_tokens"] == 16_384
               for body in transport.creates)
    assert len(stages) == 1 and stages[0]["budget"]["request_sequences"] == [2]
    assert json.loads(Path(stages[0]["receipt"]).read_text())["status"] == "completed"
    assert result["budget"]["settled_only"] is True
    assert result["budget"]["settled_micro_usd"] == 43_040_000
    assert result["budget"]["held_micro_usd"] == result["budget"]["unknown_micro_usd"] == 0
    ledger = json.loads((run / "budget.json").read_text())
    assert ledger["settled_only"] is True and ledger["maximum_micro_usd"] == 20_000_000
    assert len(list(job.rglob("budget.json"))) == 1
    assert inputs(job) == before
