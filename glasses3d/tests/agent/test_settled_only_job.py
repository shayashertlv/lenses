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


@pytest.mark.parametrize("cutoff,normalized,micro", [
    ("0.01", "0.010000", 10_000), ("19.99", "19.990000", 19_990_000),
    ("20", "20.000000", 20_000_000), ("21", "21.000000", 21_000_000),
    ("44.2788", "44.278800", 44_278_800),
])
@pytest.mark.parametrize("explicit_cap", [False, True])
def test_cli_estimate_approve_restore_binds_typed_cutoff_and_high_parent_child(job, cutoff, normalized, micro,
                                                                           explicit_cap):
    before = inputs(job)
    cap_args = ("--cap", cutoff) if explicit_cap else ()
    request = command("estimate", job, "--settled-only", "--settled-stop", cutoff, "--effort", "high", *cap_args)
    settings = request["request"]["settings"]
    assert request["requested_cap_micro_usd"] == micro
    assert request["requested_cap_usd"] == normalized
    for selected in (settings, settings["stages"][0]["settings"]):
        assert selected["settled_only"] is True and selected["settled_stop_usd"] == normalized
        assert selected["effort"] == "high" and selected["max_output_tokens"] == 16_384
        assert selected["max_turns"] is None and not selected.get("fit_output_to_budget", False)
        assert "context_guard" not in selected
    assert settings["stages"][0]["maximum_micro_usd"] == micro
    command("approve", job, "--run-id", request["run_id"], "--max-usd", cutoff,
            "--owner-words", f"Offline fixture: stop new requests at ${cutoff} settled only.")
    restored, cap = job_run.prepared_settings(job, request["run_id"])
    assert restored.settled_only and restored.effort == "high" and cap == normalized
    rebuilt, _ = job_run.prepare_run(job, request["run_id"], settings=restored, cap=cap)
    assert rebuilt.current_request_sha256 == request["request_sha256"]
    approval = A.check_approval(job / "runs" / request["run_id"],
                                current_request_sha256=rebuilt.current_request_sha256, requested_cap_usd=cap)
    assert approval.max_micro == approval.requested_cap_micro == micro
    assert approval.settled_only and approval.binding()["settled_only"] is True
    assert inputs(job) == before
    assert not (job / "runs" / request["run_id"] / "budget.json").exists()
    assert not (job / "inputs/seed.blend").exists()


def test_legacy_money_mode_is_readable_but_cannot_execute(job):
    settings = replace(job_run.settings_default(), effort="high")
    spec, _ = job_run.prepare_run(job, "mode-change", settings=settings, cap="20", write_request=True)
    run = job / "runs" / spec.run_id
    A.write_approval(run, max_usd="20", owner_words="Offline fixture approval.")
    with pytest.raises(ValueError, match="new runs require settled-only"):
        job_run.prepare_run(job, spec.run_id, settings=replace(settings, settled_only=False), cap="20")
    # Reconstruct a historical request without the mode field; do not reinterpret it as a new-mode request.
    document = json.loads((run / "approval-request.json").read_text())
    document["request"]["settings"].pop("settled_only")
    (run / "approval-request.json").write_text(json.dumps(document))
    restored, cap = job_run.prepared_settings(job, spec.run_id)
    assert restored.settled_only is False and cap == "20.000000"
    with pytest.raises(ValueError, match="new runs require settled-only"):
        job_run.prepare_run(job, spec.run_id, settings=restored, cap=cap)
    assert not (run / "budget.json").exists()


@pytest.mark.parametrize("cap,changes", [("44.2788", {}), ("20", {"fit_output_to_budget": True})])
def test_job_rejects_mixed_reserve_or_output_fitting(job, cap, changes):
    settings = replace(job_run.settings_default(), effort="high", settled_only=True, **changes)
    with pytest.raises(ValueError, match="settled-only approval amount"):
        job_run.prepare_run(job, "invalid-mode", settings=settings, cap=cap, write_request=True)
    assert not (job / "runs/invalid-mode").exists()


@pytest.mark.parametrize("cutoff", [None, "nan", "inf", "-inf", "-1", "0", "0.0000001", True, "nonsense"])
def test_prepare_refuses_missing_nonfinite_nonpositive_or_unrepresentable_cutoff(job, cutoff):
    from glasses3d.agent.budget import BudgetGuardError

    settings = replace(job_run.settings_default(), effort="high", settled_stop_usd=cutoff, settled_only=True)
    with pytest.raises((ValueError, BudgetGuardError)):
        job_run.prepare_run(job, "invalid-cutoff", settings=settings, cap="20", write_request=True)
    assert not (job / "runs/invalid-cutoff").exists()


@pytest.mark.parametrize("settled", [0, 19_990_000])
def test_status_footer_and_invocation_show_only_settled_money_facts(settled):
    from glasses3d.agent.pricing import Tariff
    summary = {"settled_only": True, "settled_micro_usd": settled, "maximum_micro_usd": 20_000_000,
               "held_micro_usd": 0, "unknown_micro_usd": 0, "settled_stop_usd": "20.000000",
               "remaining_to_stop_usd": f"{(20_000_000 - settled) / 1e6:.6f}",
               "planning": {"last_counted_input_tokens": 250_000, "max_output_tokens": 16_384}}
    ctx = L.RunStatus(SimpleNamespace(summary=lambda: summary, tariff=Tariff.frozen()))
    assert ctx.numbers() == {"settled_usd": f"{settled / 1e6:.6f}", "settled_stop_usd": "20.000000",
                             "remaining_to_stop_usd": f"{(20_000_000 - settled) / 1e6:.6f}", "context_tokens": 250_000}
    for text in (ctx.footer(), L.footer_text(**ctx.numbers())):
        assert "$20.000000" in text and "owner cutoff" in text
        assert "left before cutoff" in text and "settled" in text
        assert not any(word in text.lower() for word in ("reserve", "estimated", "allowance", "finish now"))
    invocation = L.invocation_text(run_id="offline", scene_note="Synthetic scene.", cap_micro=20_000_000,
                                    rollback=None, settled_stop_micro=20_000_000, settled_only=True)
    assert "$20.000000" in invocation and "owner cutoff" in invocation
    assert not any(word in invocation.lower() for word in ("reserve", "estimated", "allowance", "finish now"))


@pytest.mark.parametrize("cutoff,parent_tokens,prior_settled", [
    ("12", 479_600, 11_990_000), ("20", 799_600, 19_990_000), ("21", 839_600, 20_990_000),
])
def test_real_sdk_parent_and_specialist_share_settled_stop_without_reserve_refusal(
        job, monkeypatch, cutoff, parent_tokens, prior_settled):
    # Isolate the approved final-request admission at a synthetic maximal count.
    # Renewal + shared settlement has separate real-SDK transport coverage.
    monkeypatch.setattr(L.ConversationRenewal, "check", lambda *args: None)
    # At one cent below the owner's typed cutoff, a full child request is sent; its settlement stops the parent.
    child_scene, stages = FakeScene(), []
    before = inputs(job)
    settings = L.RunSettings(effort="high", max_output_tokens=16_384, max_turns=None,
                              settled_stop_usd=cutoff, settled_only=True, tool_choice="required",
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
    contract = L.StageContract.from_spec(child, cutoff)

    @function_tool(failure_error_function=L.fatal_tool_errors(*L.STAGE_FATAL))
    async def temple_stage(ctx: ToolContext):
        """Run the synthetic temple specialist under the shared settled stop."""
        assert ctx.context.guard.summary()["settled_micro_usd"] == prior_settled
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
    A.write_approval_request(run, job_id=job.name, request=request, estimate={}, suggested_cap_usd=cutoff)
    A.write_approval(run, max_usd=cutoff, owner_words="Offline fixture: shared settled-only cutoff.")
    transport = R.ScriptedOpenAI([
        R.tool_call("temple_stage", counted=parent_tokens, usage=usage(tokens=parent_tokens, written=parent_tokens, output=0)),
        R.tool_call("facet_finish", counted=922_000, usage=usage(tokens=922_000, written=922_000, output=0)),
    ])
    spec = L.RunSpec(job, run.name, instructions, tools, text, A.request_sha256(request), photos=refs,
                     settings=settings, requested_cap_usd=cutoff, stage_contracts=(contract,),
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
    assert result["budget"]["settled_micro_usd"] == prior_settled + 23_050_000
    assert result["budget"]["held_micro_usd"] == result["budget"]["unknown_micro_usd"] == 0
    ledger = json.loads((run / "budget.json").read_text())
    assert ledger["settled_only"] is True and ledger["maximum_micro_usd"] == int(cutoff) * 1_000_000
    assert len(list(job.rglob("budget.json"))) == 1
    assert inputs(job) == before
