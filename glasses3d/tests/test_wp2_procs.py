"""WP2 stage A: processes (agent/procs.py): the worker's allowlisted environment, the kill-on-close Job Object, the
EcoQoS opt-out read back from inside the process, identity checks before any kill, and detached starts (breakaway and
the WMI fallback). Host only; Windows-only cases skip elsewhere."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

from glasses3d.agent import procs

REPO = Path(__file__).resolve().parents[2]
windows = pytest.mark.skipif(os.name != "nt", reason="Windows process API")
HIGH_QOS = [1, 0]
PY_PATH = f"import sys; sys.path.insert(0, {str(REPO)!r})"


def wait_dead(pid: int, timeout: float = 10.0) -> bool:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        try:
            if psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
                return True
        except psutil.NoSuchProcess:
            return True
        time.sleep(0.05)
    return False


#: the real interpreter (a venv's python.exe is a launcher whose child is the interpreter: an opt-out through the
#: launcher's handle would be read back from a different process)
REAL_PY = procs.keeper_python()


# --------------------------------------------------------------------------- environment
def test_worker_env_is_an_allowlist(tmp_path):
    src = {"SystemRoot": r"C:\Windows", "PATH": r"C:\bin", "OPENAI_API_KEY": "sk-live-x", "GITHUB_TOKEN": "t",
           "AWS_SECRET_ACCESS_KEY": "s", "PYTHONPATH": r"C:\evil", "G3D_BLENDER": "x", "USERPROFILE": r"C:\Users\x",
           "HTTP_PROXY": "http://p", "TEMP": r"C:\old"}
    env = procs.worker_env("tok", tmp_path / "tmp", source=src)
    assert set(env) == {"SYSTEMROOT", "PATH", "USERPROFILE", "TEMP", "TMP", "G3D_WORKER_TOKEN",
                        "PYTHONDONTWRITEBYTECODE"}
    assert env["TEMP"] == env["TMP"] == str(tmp_path / "tmp") and (tmp_path / "tmp").is_dir()
    assert env["G3D_WORKER_TOKEN"] == "tok" and env["PYTHONDONTWRITEBYTECODE"] == "1"
    assert set(procs.WORKER_ENV_KEYS) == {"SYSTEMROOT", "WINDIR", "PATH", "PATHEXT", "COMSPEC",
                                          "NUMBER_OF_PROCESSORS", "USERPROFILE", "LOCALAPPDATA", "APPDATA"}


# --------------------------------------------------------------------------- Job Object
RUNNER = f"""
{PY_PATH}
import time, sys
from glasses3d.agent import procs
child, job = procs.spawn_in_job([sys.executable, "-c", "import time; time.sleep(120)"], cwd=".",
                                stdout=open(sys.argv[1], "ab"), env=None)
print(child.pid, flush=True)
time.sleep(120)
"""


@windows
def test_job_object_reaps_the_child_when_the_runner_dies(tmp_path):
    runner = subprocess.Popen([sys.executable, "-c", RUNNER, str(tmp_path / "child.log")], stdout=subprocess.PIPE,
                              text=True)
    try:
        child = int(runner.stdout.readline())
        assert psutil.pid_exists(child)
        runner.kill()                                  # TerminateProcess: the runner's handles close with it
        runner.wait(10)
        assert wait_dead(child, 10), "the job object did not reap the child"
    finally:
        if runner.poll() is None:
            runner.kill()


@windows
def test_job_object_terminate_and_pids(tmp_path):
    with open(tmp_path / "out.log", "ab") as log:
        p, job = procs.spawn_in_job([sys.executable, "-c", "import time; time.sleep(60)"], cwd=tmp_path, stdout=log,
                                    env=dict(os.environ))
        assert p.pid in job.pids()
        assert job.terminate()
        p.wait(10)
        job.close()
    assert p.returncode is not None


@windows
def test_spawned_child_is_opted_out_of_ecoqos_read_back_from_inside(tmp_path):
    code = (f"{PY_PATH}\nimport json, time\nfrom glasses3d.agent import procs\nt0 = time.time()\n"
            "while time.time() - t0 < 10 and procs.qos_state() != (1, 0):\n    time.sleep(0.05)\n"
            "print(json.dumps(procs.qos_state()), flush=True)\n")
    out = tmp_path / "qos.log"
    with open(out, "ab") as log:
        p, job = procs.spawn_in_job([REAL_PY, "-c", code], cwd=tmp_path, stdout=log, env=dict(os.environ))
        p.wait(30)
        job.close()
    assert json.loads(out.read_text().strip().splitlines()[-1]) == HIGH_QOS


@windows
def test_opt_out_self_reads_back_and_is_not_inherited():
    code = (f"{PY_PATH}\nimport json, subprocess, sys\nfrom glasses3d.agent import procs\nbefore = procs.qos_state()\n"
            "ok = procs.opt_out_self()\nchild = subprocess.run([sys.executable, '-c', "
            f"{(PY_PATH + '; import json; from glasses3d.agent import procs; print(json.dumps(procs.qos_state()))')!r}],"
            " capture_output=True, text=True).stdout\n"
            "print(json.dumps([before, ok, procs.qos_state(), json.loads(child)]))\n")
    before, ok, after, child = json.loads(subprocess.run([sys.executable, "-c", code], capture_output=True,
                                                         text=True, timeout=60).stdout)
    assert ok is True and after == HIGH_QOS and before != HIGH_QOS and child != HIGH_QOS


def test_bad_arguments_are_false_never_exceptions():
    for bad in (None, True, 0, -5, "x", 2 ** 40):
        assert procs.opt_out(bad) is False
    assert procs.qos_state(2 ** 31 - 1) is None
    assert procs.process_state(True) == (False, None) and procs.process_state(-1) == (False, None)


# --------------------------------------------------------------------------- identity
def test_process_identity_matches_psutil_and_rejects_reuse():
    alive, created = procs.process_state(os.getpid())
    assert alive and abs(created - psutil.Process().create_time()) < 0.01
    assert procs.same_process(os.getpid(), created)
    assert not procs.same_process(os.getpid(), created - 100.0)


def test_matches_record_checks_exe_and_marker_before_a_kill(tmp_path):
    marker = str(tmp_path / "receipt-xyz.json")
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", "--receipt", marker])
    try:
        rec = {"pid": p.pid, "create_time": procs.process_create_time(p.pid), "exe": sys.executable,
               "argv_marker": marker}
        assert procs.matches_record(rec)
        assert not procs.matches_record({**rec, "argv_marker": marker + "x"})
        assert not procs.matches_record({**rec, "create_time": rec["create_time"] - 50})
        assert not procs.matches_record({**rec, "exe": r"C:\elsewhere\python.exe"})
        assert not procs.kill_recorded({**rec, "argv_marker": "other"}) and p.poll() is None
        assert procs.kill_recorded(rec)
        p.wait(10)
    finally:
        if p.poll() is None:
            p.kill()


# --------------------------------------------------------------------------- detach
@windows
@pytest.mark.parametrize("breakaway", [True, False], ids=["breakaway", "wmi"])
def test_detached_command_runs_and_records_its_exit(tmp_path, breakaway):
    out = tmp_path / "done.txt"
    code = f"open({str(out)!r}, 'w').write('detached'); print('hello from the command')"
    d = procs.spawn_detached([sys.executable, "-c", code], cwd=tmp_path, log_path=tmp_path / "cmd.log",
                             status_path=tmp_path / "status.json", breakaway=breakaway)
    assert d.method == ("breakaway" if breakaway else "wmi")
    assert d.wait(timeout=90) == 0
    assert out.read_text() == "detached"
    log = (tmp_path / "cmd.log").read_text()
    assert "hello from the command" in log and "exit code 0" in log
    st = d.status()
    assert st["child_power_throttling_opt_out"] is True and st["keeper_pid"] == d.pid


@windows
def test_a_stopped_detached_command_reads_gone(tmp_path):
    d = procs.spawn_detached([sys.executable, "-c", "import time; time.sleep(120)"], cwd=tmp_path,
                             log_path=tmp_path / "cmd.log", status_path=tmp_path / "status.json")
    t0 = time.monotonic()
    while (d.status() or {}).get("child_pid") is None and time.monotonic() - t0 < 30:
        time.sleep(0.1)
    child = d.status()["child_pid"]
    assert procs.stop_tree(d.pid, d.create_time)
    assert wait_dead(child, 15)
    assert d.state()[0] == "gone"


def test_replace_with_retry_and_atomic_json(tmp_path):
    procs.write_json_atomic(tmp_path / "a" / "x.json", {"k": 1})
    assert procs.read_json(tmp_path / "a" / "x.json") == {"k": 1}
    assert procs.read_json(tmp_path / "missing.json") is None
    assert procs.REPLACE_DELAYS_S == (0.01, 0.025, 0.05, 0.1, 0.2, 0.4, 0.8, 1.6)
