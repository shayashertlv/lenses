"""Explicit credential-path forwarding only; no credential files or model clients are read."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from glasses3d.agent import cli, job_run, loop as L
from glasses3d.tests.agent.test_job_cli import command, job


@pytest.fixture
def unread_credentials(tmp_path, monkeypatch):
    """A nonexistent path guarded against direct reads and the existing secure loader."""
    path = tmp_path / "credential fixtures" / "fake credentials.env"
    load = Mock(side_effect=AssertionError("credential loading must stay inside the real runner"))
    monkeypatch.setattr(L, "load_api_key", load)
    original_open = Path.open

    def guarded_open(self, *args, **kwargs):
        assert self.resolve() != path, "the forwarding layer must not read the credential file"
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    yield path
    load.assert_not_called()
    assert not path.exists()


def request_for(job, smoke):
    record = command("estimate", job, *(["--smoke"] if smoke else []))
    command("approve", job, "--run-id", record["run_id"], "--max-usd", record["requested_cap_usd"],
            "--owner-words", "Offline path-forwarding test only.")
    return record


@pytest.mark.parametrize("smoke", [False, True])
def test_prepare_passes_path_without_reading_or_changing_approval_binding(job, unread_credentials, smoke):
    settings = job_run.settings_smoke() if smoke else job_run.settings_default()
    cap = "1" if smoke else "20"
    plain, _ = job_run.prepare_run(job, "credential-binding", settings=settings, cap=cap)
    supplied, _ = job_run.prepare_run(job, "credential-binding", settings=settings, cap=cap,
                                     env_file=unread_credentials, write_request=True)
    assert plain.env_file is None and supplied.env_file is unread_credentials
    assert supplied.current_request_sha256 == plain.current_request_sha256
    assert supplied.instructions == plain.instructions and supplied.brief == plain.brief
    record = json.loads((job / "runs/credential-binding/approval-request.json").read_text())
    assert record["request_sha256"] == plain.current_request_sha256
    assert str(unread_credentials) not in json.dumps(record)


@pytest.mark.parametrize("smoke", [False, True])
@pytest.mark.parametrize("explicit", [False, True])
def test_execute_passes_credential_path_to_run_spec_only_when_supplied(job, unread_credentials, monkeypatch,
                                                                     smoke, explicit):
    record = request_for(job, smoke)
    run = AsyncMock(return_value={"status": "completed"})
    publish = Mock(return_value={"status": "test-only"})
    monkeypatch.setattr(job_run, "run_astra", run)
    monkeypatch.setattr(job_run, "_publish_result", publish)
    kwargs = {"env_file": unread_credentials} if explicit else {}
    result = asyncio.run(job_run.execute_run(job, record["run_id"], smoke=smoke, **kwargs))
    assert result["result"]["status"] == "completed" and run.await_count == 1
    spec = run.await_args.args[0]
    assert spec.env_file == (unread_credentials if explicit else None)
    assert spec.current_request_sha256 == record["request_sha256"]
    assert publish.call_count == 1


@pytest.mark.parametrize("smoke", [False, True])
@pytest.mark.parametrize("explicit", [False, True])
def test_foreground_cli_resolves_only_explicit_credential_path(job, unread_credentials, monkeypatch,
                                                              tmp_path, smoke, explicit):
    record = request_for(job, smoke)
    monkeypatch.chdir(tmp_path)
    run = AsyncMock(return_value={"result": {"status": "completed"}})
    monkeypatch.setattr(job_run, "execute_run", run)
    options = ["--smoke"] if smoke else []
    if explicit:
        options += ["--env-file", str(unread_credentials.relative_to(tmp_path))]
    command("run", job, "--run-id", record["run_id"], "--foreground", *options)
    expected = {"resume": False}
    if smoke:
        expected["smoke"] = True
    if explicit:
        expected["env_file"] = unread_credentials
    run.assert_awaited_once_with(job, record["run_id"], **expected)


@pytest.mark.parametrize("smoke", [False, True])
@pytest.mark.parametrize("explicit", [False, True])
def test_detached_cli_carries_absolute_credential_path_without_reading_it(job, unread_credentials, monkeypatch,
                                                                       tmp_path, smoke, explicit):
    record = request_for(job, smoke)
    monkeypatch.chdir(tmp_path)
    spawn = Mock(return_value=SimpleNamespace(record=lambda: {"pid": 123, "method": "test"}))
    monkeypatch.setattr(cli.procs, "spawn_detached", spawn)
    options = ["--smoke"] if smoke else []
    if explicit:
        options += ["--env-file", str(unread_credentials.relative_to(tmp_path))]
    result = command("run", job, "--run-id", record["run_id"], *options)
    expected = [cli.sys.executable, "-m", "glasses3d", "job", "run", str(job),
                "--run-id", record["run_id"], "--foreground"]
    if smoke:
        expected.append("--smoke")
    if explicit:
        expected += ["--env-file", str(unread_credentials)]
    assert spawn.call_count == 1 and spawn.call_args.args[0] == expected
    assert result["status"] == "started"


@pytest.mark.parametrize("foreground", [False, True])
@pytest.mark.parametrize("smoke", [False, True])
def test_missing_approval_refuses_before_credential_loading_or_launch(job, unread_credentials, monkeypatch,
                                                                   capsys, foreground, smoke):
    record = command("estimate", job, *(["--smoke"] if smoke else []))
    execute = AsyncMock(side_effect=AssertionError("must not enter an unapproved runner"))
    spawn = Mock(side_effect=AssertionError("must not launch an unapproved runner"))
    monkeypatch.setattr(job_run, "execute_run", execute)
    monkeypatch.setattr(cli.procs, "spawn_detached", spawn)
    args = ["job", "run", str(job), "--run-id", record["run_id"], "--env-file", str(unread_credentials)]
    if smoke:
        args.append("--smoke")
    if foreground:
        args.append("--foreground")
    assert cli.main(args) == 3
    assert "approval" in json.loads(capsys.readouterr().err)["message"].lower()
    assert not execute.called and not spawn.called
    assert not (job / "runs" / record["run_id"] / "budget.json").exists()
