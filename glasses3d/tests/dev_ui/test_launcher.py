"""No model transport or spendable approval is used by these launcher checks."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import subprocess
import sys
import time
from types import SimpleNamespace
from urllib.error import URLError
from urllib.request import urlopen

import pytest

from glasses3d.agent import procs
from glasses3d.dev_ui import __main__ as cli
from glasses3d.dev_ui import launcher as launching
from glasses3d.dev_ui.contracts import Config


@dataclass
class FakeSettings:
    effort: str = "max"
    settled_only: bool = False
    settled_stop_usd: str = "20"
    fit_output_to_budget: bool = True


class FakeCore:
    """Writes deliberately non-production schemas; no actual approvals API."""
    def __init__(self):
        self.prepared = []
        self.approved = []

    def new_run_id(self):
        return "fake-run-1"

    def settings_default(self):
        return FakeSettings()

    def prepare_run(self, root, run_id, **kwargs):
        self.prepared.append((root, run_id, kwargs))
        settings = kwargs["settings"]
        procs.write_json_atomic(root / "runs" / run_id / "approval-request.json", {
            "schema": "test.not_a_real_request/1", "run_id": run_id,
            "requested_cap_usd": kwargs["cap"], "estimate": {"expected_usd": "8.720000"},
            "request": {"settings": vars(settings)}})

    def write_approval(self, run_dir, **kwargs):
        self.approved.append((run_dir, kwargs))
        approval = {"schema": "test.not_a_real_approval/1", "run_id": run_dir.name,
                    "authorized_by": "owner", "max_usd": kwargs["max_usd"],
                    "owner_words": kwargs["owner_words"],
                    "approval_request_sha256": hashlib.sha256(
                        (run_dir / "approval-request.json").read_bytes()).hexdigest()}
        procs.write_json_atomic(run_dir / "approval.json", approval)
        return approval


@pytest.fixture
def config(tmp_path):
    return Config(port=8278, state_root=tmp_path / "ui", jobs_root=tmp_path / "jobs",
                  static_root=tmp_path / "pages")


@pytest.fixture
def fixture_launcher(config):
    core = FakeCore()
    calls = []
    def command(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stderr="", stdout=json.dumps(
            {"status": "stop_requested" if "stop" in argv else "started", "pid": 900000001}))
    launcher = launching.Launcher(config, core=core, command_runner=command, lock_probe=lambda: None)
    return launcher, core, calls


def test_preparation_exact_money_terms_and_idempotency(config, fixture_launcher):
    launcher, core, _ = fixture_launcher
    root = config.jobs_root / "neutral-job"
    result = launcher.prepare(root, "27.50", run_id="run-abc")
    assert result == {"run_id": "run-abc", "cutoff_usd": "27.50", "estimate_usd": "8.720000"}
    assert core.prepared[0][2] == {"settings": FakeSettings("high", True, "27.50", False),
                                    "cap": "27.50", "write_request": True}
    assert launcher.prepare(root, "27.50", run_id="run-abc") == result
    assert len(core.prepared) == 1
    assert not core.approved
    with pytest.raises(launching.LauncherError, match="differs"):
        launcher.prepare(root, "28", run_id="run-abc")


@pytest.mark.parametrize("value", ["0", "-1", "NaN", "Infinity", "-Infinity", "", True,
                                  "0.0000001", "1e1000000", "1e-1000000", 1.25])
def test_invalid_cutoffs_never_prepare(fixture_launcher, config, value):
    launcher, core, _ = fixture_launcher
    with pytest.raises(ValueError):
        launcher.prepare(config.jobs_root / "j", value)
    assert not core.prepared and not core.approved


def test_micro_precision_retains_typed_authorization(fixture_launcher, config):
    launcher, core, _ = fixture_launcher
    root = config.jobs_root / "j"
    launcher.prepare(root, "7.1234567", run_id="r1")
    result = launcher.approve(root, "r1", "7.1234567", "Start a paid run", "j")
    assert '$7.1234567 for j' in result["owner_words"]


def test_no_fixed_money_ceiling(fixture_launcher, config):
    launcher, _, _ = fixture_launcher
    result = launcher.prepare(config.jobs_root / "j", "1e100", run_id="r1")
    assert result["cutoff_usd"] == "1E+100"


@pytest.mark.parametrize("method", ["prepare", "approve"])
@pytest.mark.parametrize("kind", ["approval", "budget", "run"])
def test_known_pipeline_refusals_have_json_compatible_launcher_errors(fixture_launcher, config, method, kind):
    from glasses3d.agent.approvals import ApprovalRefused
    from glasses3d.agent.budget import BudgetGuardError
    from glasses3d.agent.loop import RunRefused
    launcher, core, _ = fixture_launcher
    root = config.jobs_root / "j"
    errors = {"approval": ApprovalRefused("stale", "synthetic approval refusal"),
              "budget": BudgetGuardError("synthetic money refusal"), "run": RunRefused("synthetic run refusal")}
    def refuse(*args, **kwargs):
        raise errors[kind]
    if method == "prepare":
        core.prepare_run = refuse
        invoke = lambda: launcher.prepare(root, "12", run_id="r1")
    else:
        launcher.prepare(root, "12", run_id="r1")
        core.write_approval = refuse
        invoke = lambda: launcher.approve(root, "r1", "12", "Start a paid run", "j")
    with pytest.raises(launching.LauncherError) as error:
        invoke()
    assert "synthetic" in str(error.value)
    assert error.value.status_code == 409
    assert not core.approved


def test_single_approval_reused_and_bound(fixture_launcher, config):
    launcher, core, _ = fixture_launcher
    root = config.jobs_root / "j"
    launcher.prepare(root, "30", run_id="r1")
    first = launcher.approve(root, "r1", "30", "Start a paid run", "j")
    assert first["owner_words"] == 'Dev UI: owner pressed "Start a paid run" with max budget $30 for j'
    assert launcher.approve(root, "r1", "30", "Start a paid run", "j") == first
    assert len(core.approved) == 1
    with pytest.raises(launching.LauncherError, match="differs"):
        launcher.approve(root, "r1", "31", "Start a paid run", "j")
    path = root / "runs/r1/approval-request.json"
    path.write_text(path.read_text() + "\n", encoding="utf-8")
    with pytest.raises(launching.LauncherError, match="differs"):
        launcher.approve(root, "r1", "30", "Start a paid run", "j")
    assert len(core.approved) == 1


def test_busy_refuses_before_preparation_or_approval(fixture_launcher, config):
    launcher, core, _ = fixture_launcher
    def busy():
        raise launching.PaidRunBusyError()
    launcher._lock_probe = busy
    with pytest.raises(launching.PaidRunBusyError, match="Another paid run is in progress"):
        launcher.prepare(config.jobs_root / "j", "20", run_id="r1")
    assert not core.prepared and not core.approved


def test_launch_and_stop_use_existing_cli_on_actual_root(fixture_launcher, config):
    launcher, _, calls = fixture_launcher
    root = config.jobs_root / "edit-round"
    result = launcher.launch(root, "r1")
    assert launcher.launch(root, "r1") == result
    assert len(calls) == 1
    assert calls[0][0] == [str(config.python), "-m", "glasses3d", "job", "run",
                           str(root.resolve()), "--run-id", "r1"]
    assert calls[0][1]["cwd"] == str(config.repo_root)
    assert "--foreground" not in calls[0][0]
    launcher.stop(root, "r1")
    assert calls[1][0][-2:] == ["stop", str(root.resolve())]
    assert launcher.offline_test_mode is False


def test_retry_failed_launch_reuses_prepared_approval(fixture_launcher, config):
    launcher, core, calls = fixture_launcher
    root = config.jobs_root / "j"
    launcher.prepare(root, "12", run_id="r1")
    launcher.approve(root, "r1", "12", "Send edits", "edit-1")
    successful = launcher._command_runner
    launcher._command_runner = lambda *a, **k: SimpleNamespace(returncode=2, stdout="", stderr='{"message":"not launched"}')
    with pytest.raises(launching.LauncherError, match="not launched"):
        launcher.launch(root, "r1")
    launcher._command_runner = successful
    launcher.prepare(root, "12", run_id="r1")
    launcher.approve(root, "r1", "12", "Send edits", "edit-1")
    launcher.launch(root, "r1")
    assert len(core.prepared) == len(core.approved) == len(calls) == 1


@pytest.mark.parametrize("marker", ["runner-status.json", "budget.json", "result.json"])
def test_retry_after_detached_worker_started_never_relaunches(fixture_launcher, config, marker):
    launcher, _, calls = fixture_launcher
    root = config.jobs_root / "j"
    procs.write_json_atomic(root / "runs/r1" / marker, {"keeper_pid": 12345})
    assert launcher.launch(root, "r1")["recovered"] is True
    assert not calls


def test_cli_error_after_worker_started_recovers_without_relaunch(fixture_launcher, config):
    launcher, _, _ = fixture_launcher
    root = config.jobs_root / "j"
    calls = []
    def failed_after_spawn(*args, **kwargs):
        calls.append(args)
        procs.write_json_atomic(root / "runs/r1/runner-status.json", {"keeper_pid": 12345})
        return SimpleNamespace(returncode=2, stdout="", stderr='{"message":"caller failed after detach"}')
    launcher._command_runner = failed_after_spawn
    assert launcher.launch(root, "r1")["recovered"] is True
    assert launcher.launch(root, "r1")["recovered"] is True
    assert len(calls) == 1


def test_checkout_lock_probe_checks_os_guard_and_stale_identity(monkeypatch, tmp_path):
    from glasses3d.agent import budget
    monkeypatch.setattr(budget, "PAID_RUN_LOCK_ROOT", tmp_path)
    stream = budget._open_locked(tmp_path / ".paid-run.guard")
    try:
        with pytest.raises(launching.PaidRunBusyError):
            launching.check_paid_run_available()
    finally:
        budget._os_unlock(stream)
    procs.write_json_atomic(tmp_path / ".paid-run.lock", {"pid": 999999999, "create_time": 1, "exe": "missing"})
    launching.check_paid_run_available()
    procs.write_json_atomic(tmp_path / ".paid-run.lock", budget.process_identity())
    with pytest.raises(launching.PaidRunBusyError):
        launching.check_paid_run_available()


def test_server_stop_refuses_unmatched_or_pid_only_record(monkeypatch, config):
    _, path, _ = cli._paths(config)
    record = {"worker_argv": cli._argv(config), "pid": 1234, "create_time": 1}
    procs.write_json_atomic(path, record)
    killed = []
    monkeypatch.setattr(procs, "matches_record", lambda row: False)
    monkeypatch.setattr(procs, "stop_tree", lambda *a: killed.append(a))
    assert cli.stop_server(config)["stop_requested"] is False
    assert not killed
    record["create_time"] = None
    procs.write_json_atomic(path, record)
    monkeypatch.setattr(procs, "matches_record", lambda row: True)
    assert cli.server_status(config)["status"] == "stopped"


def test_foreground_has_one_loopback_worker(monkeypatch, config):
    calls = []
    app = object()
    monkeypatch.setitem(sys.modules, "glasses3d.dev_ui.server", SimpleNamespace(create_app=lambda c: app))
    monkeypatch.setitem(sys.modules, "uvicorn", SimpleNamespace(run=lambda *a, **kw: calls.append((a, kw))))
    assert cli.main(["--foreground", "--port", "8278", "--state-root", str(config.state_root)]) == 0
    assert calls == [((app,), {"host": "127.0.0.1", "port": 8278, "workers": 1,
                              "reload": False, "access_log": False})]


def test_task_subcommand_calls_detached_task_worker(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setitem(sys.modules, "glasses3d.dev_ui.tasks", SimpleNamespace(run_task_file=lambda p: calls.append(p)))
    path = tmp_path / "task.json"
    assert cli.main(["task", str(path)]) == 0
    assert calls == [path.resolve()]


def test_real_detached_server_start_status_stop(config):
    """The only real process test; no run/approval endpoint is touched."""
    config.static_root.mkdir(parents=True)
    (config.static_root / "index.html").write_text("offline server lifecycle test", encoding="utf-8")
    argv = [str(config.python), "-B", "-m", "glasses3d.dev_ui", "--port", "8278",
            "--state-root", str(config.state_root), "--jobs-root", str(config.jobs_root),
            "--static-root", str(config.static_root)]
    started = None
    try:
        result = subprocess.run(argv, cwd=config.repo_root, capture_output=True, text=True,
                                encoding="utf-8", timeout=60)
        assert result.returncode == 0, result.stderr
        started = json.loads(result.stdout)
        assert started["status"] == "starting"
        deadline = time.monotonic() + 30
        while True:
            try:
                with urlopen("http://127.0.0.1:8278/api/config", timeout=1) as response:
                    assert json.load(response)["offline_test_mode"] is False
                break
            except (URLError, ConnectionError, TimeoutError):
                if time.monotonic() >= deadline:
                    pytest.fail((cli._paths(config)[0] / "server.log").read_text(encoding="utf-8"))
                time.sleep(0.1)
        # The starter process has exited; the identity-checked detached worker remains.
        status = subprocess.run([*argv, "--status"], cwd=config.repo_root, capture_output=True,
                                text=True, encoding="utf-8", timeout=30)
        assert status.returncode == 0, status.stderr
        assert json.loads(status.stdout)["status"] == "running"
        duplicate = cli.start_server(config)
        assert duplicate["status"] == "running"
        stopped = subprocess.run([*argv, "--stop"], cwd=config.repo_root, capture_output=True,
                                 text=True, encoding="utf-8", timeout=60)
        assert stopped.returncode == 0, stopped.stderr
        assert json.loads(stopped.stdout)["stop_requested"] is True
        assert cli.server_status(config)["status"] == "stopped"
    finally:
        if started:
            cli.stop_server(config)
