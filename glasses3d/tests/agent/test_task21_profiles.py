"""Per-run profile selection is approval-bound; no renderer or paid transport."""
import ast
from pathlib import Path
import subprocess
from unittest.mock import AsyncMock
from types import SimpleNamespace

import pytest

from glasses3d.agent import approvals as A, cli, job_run
from glasses3d.agent.instruction_profiles import DEFAULT_PROFILE, LEGACY_PROFILE
from glasses3d.tests.agent.test_job_cli import job, command


@pytest.mark.parametrize("profile", [DEFAULT_PROFILE, LEGACY_PROFILE])
def test_profile_is_selected_bound_and_reloaded(job, profile):
    request = command("estimate", job, "--instruction-profile", profile)
    assert request["request"]["settings"]["instruction_profile"] == profile
    settings, cap = job_run.prepared_settings(job, request["run_id"])
    spec, scene = job_run.prepare_run(job, request["run_id"], settings=settings, cap=cap)
    assert settings.instruction_profile == scene.instruction_profile == profile
    assert spec.current_request_sha256 == request["request_sha256"]
    assert ("record_difference" in {tool.name for tool in spec.tools}) == (profile == DEFAULT_PROFILE)


def test_default_is_new_and_cannot_switch_an_existing_approval(job, monkeypatch):
    request = command("estimate", job)
    assert request["request"]["settings"]["instruction_profile"] == DEFAULT_PROFILE
    command("approve", job, "--run-id", request["run_id"], "--max-usd", "20",
            "--owner-words", "Offline test only.")
    execute = AsyncMock()
    monkeypatch.setattr(job_run, "execute_run", execute)
    with pytest.raises(A.ApprovalRefused, match="profile differs"):
        command("run", job, "--run-id", request["run_id"], "--foreground",
                "--instruction-profile", LEGACY_PROFILE)
    execute.assert_not_called()
    legacy = command("estimate", job, "--instruction-profile", LEGACY_PROFILE)
    assert legacy["request_sha256"] != request["request_sha256"]


def test_run_option_accepts_selected_legacy_profile(job, monkeypatch):
    request = command("estimate", job, "--instruction-profile", LEGACY_PROFILE)
    command("approve", job, "--run-id", request["run_id"], "--max-usd", "20",
            "--owner-words", "Offline test only.")
    execute = AsyncMock(return_value={"status": "offline"})
    monkeypatch.setattr(job_run, "execute_run", execute)
    assert command("run", job, "--run-id", request["run_id"], "--foreground",
                   "--instruction-profile", LEGACY_PROFILE) == {"status": "offline"}
    execute.assert_awaited_once()


def test_unknown_profile_refuses_before_creating_request(job):
    before = set((job / "runs").glob("*"))
    with pytest.raises(ValueError, match="profile"):
        job_run.prepare_run(job, write_request=True, instruction_profile="invented")
    assert set((job / "runs").glob("*")) == before


@pytest.mark.parametrize("profile", [DEFAULT_PROFILE, LEGACY_PROFILE])
def test_owner_specialist_inherits_the_approved_profile(profile):
    from dataclasses import replace
    from glasses3d.agent.temple_lab import owner_stage
    from glasses3d.agent.instructions import build_instructions
    from glasses3d.agent.instruction_profiles import legacy_text
    owner = {"phase": "understand"}
    parent = replace(job_run.settings_default(), instruction_profile=profile)
    settings = owner_stage.settings(parent, owner)
    scene = SimpleNamespace(root=None, owner_edit=owner)
    tools = owner_stage.registry(scene, settings)
    text = build_instructions(tools, owner_edit=owner, profile=profile)
    assert settings.instruction_profile == scene.instruction_profile == profile
    assert ("record_difference" in {tool.name for tool in tools}) == (profile == DEFAULT_PROFILE)
    assert "This never expands confirmed scope" in text if profile == DEFAULT_PROFILE else "record_difference" not in text


@pytest.mark.parametrize("phase", ["understand", "edit"])
def test_legacy_owner_specialist_description_matches_pre_task21_source(phase):
    from glasses3d.agent.tools import build_tools
    source = subprocess.run(
        ["git", "show", "28abe77:glasses3d/agent/tools/temple.py"], check=True,
        capture_output=True, encoding="utf-8", cwd=Path(__file__).resolve().parents[3]).stdout
    descriptions = [ast.literal_eval(node.value) for node in ast.walk(ast.parse(source))
                    if isinstance(node, ast.Assign) and any(
                        isinstance(target, ast.Attribute) and target.attr == "description"
                        and isinstance(target.value, ast.Name) and target.value.id == "temple_stage"
                        for target in node.targets)]
    assert len(descriptions) == 1
    ordinary = next(tool for tool in build_tools(SimpleNamespace(
        root=None, instruction_profile=LEGACY_PROFILE)) if tool.name == "temple_stage")
    owner = next(tool for tool in build_tools(SimpleNamespace(
        root=None, instruction_profile=LEGACY_PROFILE, owner_edit={"phase": phase}))
                 if tool.name == "temple_stage")
    assert owner.description.encode() == descriptions[0].encode()
    assert owner.description != ordinary.description
    assert owner.params_json_schema == ordinary.params_json_schema


def test_cutoff_draft_uses_formal_best_even_with_open_difference(tmp_path, monkeypatch):
    from glasses3d.agent import differences, delivery
    from glasses3d.tests.agent.test_draft_delivery import draft_fixture
    scene, result, workers, captures = draft_fixture(tmp_path, monkeypatch, best=True, status="budget_limit")
    scene.root, scene.active_run_id = tmp_path, result["run_id"]
    scene.instruction_profile = DEFAULT_PROFILE
    # Retain the host's already-recorded unresolved observation. A cutoff requires
    # no new look, list operation or Astra request before exporting the saved best.
    differences._append(scene, "difference", call_id="earlier-record", item={
        "id": "still-open", "what_differs": "Lens lightness differs", "status": "open",
        "evidence_ref": "earlier-look", "last_correction": "Tint adjusted", "outcome": "Still differs",
        "next_action": "Compare another candidate", "refs": ["front"], "reason": None})
    journal = tmp_path / "runs" / result["run_id"] / "differences.jsonl"
    before = journal.read_bytes()
    outcome = delivery.deliver_draft(tmp_path, scene, result)
    report = delivery.load_delivery(tmp_path, outcome["id"])["report_data"]
    assert report["checkpoint_selection"] == "best" and report["checkpoint"] == "c0000"
    assert report["exit_checkpoint"]["id"] == "c0001"
    assert report["finished"] is report["accepted_finish"] is False
    assert journal.read_bytes() == before
    assert len(workers) == len(captures) == 1
