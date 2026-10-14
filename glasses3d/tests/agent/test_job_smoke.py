"""M5-prep smoke composition: real photo/approval files; no Blender or paid transport."""
import asyncio
from dataclasses import replace
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from PIL import Image
import pytest

from glasses3d.agent import approvals as A, cli, estimate as E, job_run, jobs
from glasses3d.agent.pricing import Tariff, usd_text


@pytest.fixture
def job(tmp_path):
    source = tmp_path / "photos"
    source.mkdir()
    views = {}
    for index, kind in enumerate(("front", "side", "angled", "top", "rear", "side")):
        name = f"photo {index + 1}.png"
        Image.new("RGB", (31 + index, 19), (11 + index, 23, 61)).save(source / name)
        views[name] = {"kind": kind, "side": "left" if index == 1 else "right" if index == 5 else "both"}
    view_file = tmp_path / "views.json"
    view_file.write_text(json.dumps(views))
    return jobs.create_job(images=source, views=view_file, name="Smoke intake proof",
                           description="  Keep the exact owner's words.\nSix photographs, one product.  ",
                           jobs_root=tmp_path / "jobs")


def command(*args):
    return cli.dispatch(cli.parser().parse_args(["job", *map(str, args)]))


def approved(job, *, smoke=True):
    record = command("estimate", job, *(["--smoke"] if smoke else []))
    command("approve", job, "--run-id", record["run_id"], "--max-usd", record["requested_cap_usd"],
            "--owner-words", "Offline smoke test only.")
    return record


@pytest.mark.parametrize("images", [0, 1, 6])
def test_one_request_estimate_counts_all_photos_without_cache_credit(images):
    result = E.smoke_estimate(4000, images=images, cap_usd="1", one_request=True)
    initial = 1000 + images * 1500
    tariff = Tariff.frozen()
    usage = E.causal_request_usage(initial, 0, E.G3D_PARAMETERS)
    assert usage["input_tokens_details"]["cached_tokens"] == 0
    assert usage["input_tokens_details"]["cache_write_tokens"] == initial
    assert result["initial_context_tokens"] == initial and result["requests"] == 1
    assert result["expected_micro_usd"] == tariff.settle_micro(usage)
    assert result["max_output_tokens"] == 4096
    assert result["first_hold_micro_usd"] == result["largest_hold_micro_usd"] == tariff.reserve_micro(initial, 4096)
    assert result["cap_micro_usd"] == result["suggested_cap_micro_usd"] == 1_000_000
    assert result["smoke"]["images"] == images and result["smoke"]["max_turns"] == 1


def test_smoke_estimate_enforces_full_output_hold_even_when_expected_spend_fits():
    tariff = Tariff.frozen()
    initial = E.initial_context_tokens(4000, 6)
    hold = tariff.reserve_micro(initial, 4096)
    expected = tariff.settle_micro(E.causal_request_usage(initial, 0, E.G3D_PARAMETERS))
    assert expected < hold - 1
    with pytest.raises(E.EstimateError, match="hold"):
        E.smoke_estimate(4000, images=6, cap_usd=usd_text(hold - 1), one_request=True)
    result = E.smoke_estimate(4000, images=6, cap_usd=usd_text(hold), one_request=True)
    assert result["cap_micro_usd"] == result["largest_hold_micro_usd"] == hold


def test_historical_smoke_estimate_defaults_remain_two_requests_one_photo():
    result = E.smoke_estimate(4000)
    assert result["requests"] == 2 and result["smoke"] == E.SMOKE_SETTINGS
    assert result["initial_context_tokens"] == E.initial_context_tokens(4000, 1)


def test_smoke_request_binds_real_prompt_all_photos_owner_notes_and_mode(job, monkeypatch):
    notes = ["Owner changes:  exact supplementary wording.\nKeep these spaces.  "]
    monkeypatch.setattr(job_run.verdicts, "pending_notes", lambda root: notes)
    settings = job_run.settings_smoke("1")
    spec, scene = job_run.prepare_run(job, "smoke-binding", settings=settings, cap="1", write_request=True)
    record = json.loads((job / "runs/smoke-binding/approval-request.json").read_text())
    normal, _ = job_run.prepare_run(job, "ordinary-binding")
    assert spec.instructions == normal.instructions
    assert spec.brief == normal.brief and jobs.load_job(job)["description"] in spec.brief
    assert spec.owner_notes == notes and len(spec.photos) == 6
    assert spec.stage_contracts == () and normal.stage_contracts
    assert {tool.name for tool in spec.tools} == {tool.name for tool in normal.tools}
    binding = record["request"]["run_inputs"]
    assert A.canonical_json(binding) == A.canonical_json(A.run_input_binding(job, spec.brief, spec.photos))
    assert len(binding["photos"]) == 6
    assert record["request"]["unsent_owner_notes"] == [hashlib.sha256(A.canonical_json(note)).hexdigest()
                                                        for note in notes]
    assert record["request"]["settings"]["smoke"] is True
    assert record["requested_cap_micro_usd"] == 1_000_000
    assert record["request"]["settings"]["settled_stop_usd"] == "1.000000"
    schemas = json.dumps([{"name": t.name, "description": t.description,
                           "parameters": t.params_json_schema} for t in spec.tools])
    chars = len(spec.instructions) + len(spec.brief) + len(schemas)
    chars += sum(map(len, notes))
    assert record["estimate"]["initial_context_tokens"] == E.initial_context_tokens(chars, 6)
    assert record["estimate"]["requests"] == 1 and record["estimate"]["smoke"]["images"] == 6
    assert not (job / "inputs/seed.blend").exists()
    assert not (job / "runs/smoke-binding/budget.json").exists()


def test_smoke_settings_round_trip_preserves_mode_zero_continuations_and_approval_hash(job):
    settings = job_run.settings_smoke("0.80")
    spec, _ = job_run.prepare_run(job, "smoke-roundtrip", settings=settings, cap="0.8", write_request=True)
    restored, cap = job_run.prepared_settings(job, spec.run_id)
    assert restored.smoke is True and restored.max_continuations == 0
    assert restored.max_turns == 1 and restored.max_output_tokens == 4096
    assert restored.tool_choice == "required" and restored.reset_tool_choice is False
    assert restored.finish_tool is None and restored.effort == "max"
    assert restored.approval_settings() == settings.approval_settings()
    rebuilt, _ = job_run.prepare_run(job, spec.run_id, settings=restored, cap=cap)
    assert rebuilt.current_request_sha256 == spec.current_request_sha256


@pytest.mark.parametrize("change", [{"max_turns": 2}, {"max_output_tokens": 8192}, {"max_continuations": 1},
                                    {"settled_stop_usd": "0.8"}])
def test_smoke_refuses_settings_that_could_outlive_or_exceed_its_single_request(job, change):
    with pytest.raises(ValueError, match="smoke"):
        job_run.prepare_run(job, settings=replace(job_run.settings_smoke(), **change), cap="1", write_request=True)
    assert not list(job.glob("runs/*/approval-request.json"))


def test_smoke_cannot_resume_existing_model_history(job):
    with pytest.raises(ValueError, match="fresh isolated"):
        job_run.prepare_run(job, settings=job_run.settings_smoke(), cap="1", resume=True, write_request=True)
    assert not list(job.glob("runs/*/approval-request.json"))


@pytest.mark.parametrize("extra,cap", [([], "1.000000"), (["--cap", "0.8"], "0.800000")])
def test_cli_smoke_estimate_defaults_and_explicit_cap_bind_both_limits(job, extra, cap):
    record = command("estimate", "--smoke", job, *extra)
    settings = record["request"]["settings"]
    assert settings["smoke"] is True and settings["settled_stop_usd"] == record["requested_cap_usd"] == cap
    assert settings["max_turns"] == 1 and settings["max_output_tokens"] == 4096
    assert settings["tool_choice"] == "required" and settings["reset_tool_choice"] is False
    assert record["estimate"]["smoke"]["images"] == 6 and record["estimate"]["requests"] == 1


@pytest.mark.parametrize("extra", [["--turns", "2"], ["--max-output-tokens", "8192"],
                                  ["--settled-stop", "0.8"], ["--cap", "0.01"]])
def test_cli_invalid_or_unaffordable_smoke_refuses_before_creating_request(job, capsys, extra):
    assert cli.main(["job", "estimate", "--smoke", str(job), *extra]) == 2
    assert json.loads(capsys.readouterr().err)["status"] == "refused"
    assert not list(job.glob("runs/*/approval-request.json"))


def test_cli_ordinary_estimate_defaults_are_unchanged(job):
    record = command("estimate", job)
    settings = record["request"]["settings"]
    assert settings.get("smoke", False) is False
    assert settings["max_turns"] is None and settings["max_output_tokens"] == 16384
    assert settings["settled_stop_usd"] == "20.000000" and record["requested_cap_usd"] == "23.000000"
    assert record["estimate"]["requests"] == 60


def test_cli_foreground_smoke_passes_the_explicit_mode(job, monkeypatch):
    record = approved(job)
    execute = AsyncMock(return_value={"result": {"status": "completed"}})
    monkeypatch.setattr(job_run, "execute_run", execute)
    command("run", "--smoke", job, "--foreground", "--run-id", record["run_id"])
    execute.assert_awaited_once_with(job, record["run_id"], resume=False, smoke=True)


def test_cli_detached_smoke_preserves_the_approved_mode(job, monkeypatch):
    record = approved(job)
    detach = Mock(return_value=SimpleNamespace(record=lambda: {"pid": 123, "method": "test"}))
    monkeypatch.setattr(cli.procs, "spawn_detached", detach)
    command("run", "--smoke", job, "--run-id", record["run_id"])
    assert detach.call_args.args[0] == [cli.sys.executable, "-m", "glasses3d", "job", "run", str(job),
                                       "--run-id", record["run_id"], "--foreground", "--smoke"]


@pytest.mark.parametrize("approved_smoke", [True, False])
def test_cli_and_execute_refuse_cross_mode_approval_before_any_execution(job, monkeypatch, approved_smoke):
    record = approved(job, smoke=approved_smoke)
    loop = AsyncMock(side_effect=AssertionError("must not enter model loop"))
    detach = Mock(side_effect=AssertionError("must not detach"))
    monkeypatch.setattr(job_run, "run_astra", loop)
    monkeypatch.setattr(cli.procs, "spawn_detached", detach)
    wrong_mode = not approved_smoke
    with pytest.raises(A.ApprovalRefused, match="matching approval"):
        command("run", job, "--run-id", record["run_id"], *(["--smoke"] if wrong_mode else []))
    with pytest.raises(A.ApprovalRefused, match="matching approval"):
        asyncio.run(job_run.execute_run(job, record["run_id"], smoke=wrong_mode))
    assert not loop.called and not detach.called
    assert not (job / "runs" / record["run_id"] / "budget.json").exists()


def test_execute_smoke_returns_connectivity_result_without_publishing_a_product(job, monkeypatch):
    record = approved(job)
    loop = AsyncMock(return_value={"status": "completed", "summary": "Read-only smoke complete."})
    draft = Mock(side_effect=AssertionError("smoke must not export a draft"))
    finished = Mock(side_effect=AssertionError("smoke must not certify a finish"))
    monkeypatch.setattr(job_run, "run_astra", loop)
    monkeypatch.setattr(job_run.D, "deliver_draft", draft)
    monkeypatch.setattr(job_run, "deliver_finished", finished)
    result = asyncio.run(job_run.execute_run(job, record["run_id"], smoke=True))
    assert loop.await_count == 1
    assert result["delivery"] == {"status": "smoke_only", "finished": False, "accepted_finish": False}
    assert not draft.called and not finished.called
    assert not (job / "deliveries/current.json").exists()
