"""Run outcomes and Unicode survive legacy Windows stdout and reporting failures."""
from __future__ import annotations

import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from glasses3d.agent import cli, procs
from glasses3d.agent.text_io import configure_utf8_stdio


REPO = Path(__file__).resolve().parents[3]
TEXT = "Astra: \u2212Z; \u05e9\u05dc\u05d5\u05dd; \U0001f453"


def test_stdio_configuration_can_repeat_after_utf8_input_was_read(monkeypatch):
    incoming = io.TextIOWrapper(io.BytesIO(TEXT.encode("utf-8")), encoding="cp1252")
    outgoing = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    errors = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    monkeypatch.setattr(sys, "stdin", incoming)
    monkeypatch.setattr(sys, "stdout", outgoing)
    monkeypatch.setattr(sys, "stderr", errors)
    configure_utf8_stdio()
    assert incoming.read() == TEXT
    configure_utf8_stdio()
    print(TEXT, flush=True)
    print(TEXT, file=sys.stderr, flush=True)
    assert outgoing.buffer.getvalue().decode("utf-8").strip() == TEXT
    assert errors.buffer.getvalue().decode("utf-8").strip() == TEXT


@pytest.mark.parametrize("detached", [False, True], ids=["redirected-foreground", "detached"])
@pytest.mark.parametrize("status,expected", [("completed", 0), ("budget_limit", 1)])
def test_cli_subprocess_preserves_unicode_and_run_exit(tmp_path, detached, status, expected):
    """Real CLI reporting; dispatch is replaced before it can open any job/client."""
    result = {"result": {"status": status, "finish": {"summary": TEXT}},
              "delivery": {"status": "delivered" if status == "completed" else "draft"}}
    script = tmp_path / "offline_cli.py"
    script.write_text(
        "import json, sys\n"
        f"sys.path.insert(0, {str(REPO)!r})\n"
        "from glasses3d.agent import cli\n"
        # Deliberately override even the detached keeper's new UTF-8 environment.
        "sys.stdout.reconfigure(encoding='cp1252', errors='strict')\n"
        "sys.stderr.reconfigure(encoding='cp1252', errors='strict')\n"
        "assert sys.stdout.encoding == 'cp1252'\n"
        f"result = {ascii(result)}\n"
        "cli.dispatch = lambda args: result\n"
        "code = cli.main(['job', 'show', 'offline'])\n"
        "assert sys.stdout.encoding == sys.stderr.encoding == 'utf-8'\n"
        f"print({ascii(TEXT)}, file=sys.stderr, flush=True)\n"
        "raise SystemExit(code)\n",
        encoding="utf-8",
    )
    legacy = {"PYTHONIOENCODING": "cp1252:strict", "PYTHONUTF8": "0"}
    log_path = tmp_path / "runner.log"
    if detached:
        child = procs.spawn_detached([sys.executable, str(script)], cwd=REPO, log_path=log_path,
                                     status_path=tmp_path / "runner-status.json", env=legacy)
        assert child.wait(timeout=60) == expected
        assert child.status()["exit_code"] == expected
    else:
        with log_path.open("wb") as log:
            child = subprocess.run([sys.executable, str(script)], cwd=REPO, env=dict(os.environ, **legacy),
                                   stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, timeout=60)
        assert child.returncode == expected
    output = log_path.read_bytes().decode("utf-8")
    shown, end = json.JSONDecoder().raw_decode(output)
    assert shown == result
    assert TEXT in output[end:]
    assert '"status": "refused"' not in output


@pytest.mark.parametrize("status,expected", [("completed", 0), ("failed_worker", 1)])
@pytest.mark.parametrize("failure", ["format", "write", "flush"])
def test_reporting_failure_preserves_terminal_run_status(monkeypatch, capsys, status, expected, failure):
    result = {"result": {"status": status, "finish": {"summary": TEXT}},
              "delivery": {"status": "delivered" if status == "completed" else "no_usable_checkpoint"}}
    original = copy.deepcopy(result)
    monkeypatch.setattr(cli, "dispatch", lambda args: result)

    class FailedOutput:
        def write(self, text):
            if failure == "write":
                raise UnicodeEncodeError("charmap", TEXT, 7, 8, "character maps to <undefined>")
            return len(text)

        def flush(self):
            raise OSError("report stream unavailable")

    def failed_format(value):
        raise ValueError("report formatting failed")

    if failure == "format":
        monkeypatch.setattr(cli, "owner_money", failed_format)
    else:
        monkeypatch.setattr(cli.sys, "stdout", FailedOutput())
    assert cli.main(["job", "show", "offline"]) == expected
    warning = json.loads(capsys.readouterr().err)
    assert warning["status"] == "reporting_failed"
    assert warning["run_status"] == status
    assert warning["delivery_status"] == original["delivery"]["status"]
    assert result == original


def test_non_run_reporting_failure_is_not_an_execution_refusal(monkeypatch, capsys):
    monkeypatch.setattr(cli, "dispatch", lambda args: {"status": "created"})

    def fail(value):
        raise ValueError("report formatting failed")

    monkeypatch.setattr(cli, "owner_money", fail)
    assert cli.main(["job", "show", "offline"]) == 2
    assert json.loads(capsys.readouterr().err)["status"] == "reporting_failed"


@pytest.mark.parametrize("entry", ["corpus", "temple"])
def test_other_agent_cli_streams_are_utf8(entry):
    if entry == "corpus":
        invocation = (
            "from glasses3d.agent import astra_corpus as command\n"
            "command.load = lambda: []\n"
            f"command.gate = lambda *args: {{'summary': {ascii(TEXT)}}}\n"
            "command.main(['gate'])\n"
        )
    else:
        invocation = (
            "from glasses3d.agent.temple_lab import cli as command\n"
            "command.read = lambda path: {}\n"
            f"command.prepare = lambda *args, **kwargs: {{'summary': {ascii(TEXT)}}}\n"
            "command.main(['prepare', '--job', 'offline', '--source-config', 'offline.json'])\n"
        )
    result = subprocess.run(
        [sys.executable, "-c", "import sys\n" + invocation
         + "assert sys.stdout.encoding == sys.stderr.encoding == 'utf-8'\n"
         + f"print({ascii(TEXT)}, file=sys.stderr, flush=True)\n"],
        cwd=REPO, capture_output=True, timeout=60,
        env=dict(os.environ, PYTHONIOENCODING="cp1252:strict", PYTHONUTF8="0"),
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    assert json.loads(result.stdout.decode("utf-8")) == {"summary": TEXT}
    assert result.stderr.decode("utf-8").strip() == TEXT


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
@pytest.mark.parametrize("status,expected", [("completed", 0), ("failed_worker", 1)])
def test_failed_report_flush_does_not_replace_process_exit_at_shutdown(stream, status, expected):
    script = (
        "import sys\n"
        "from glasses3d.agent import cli\n"
        "class FailedStream:\n"
        "    def write(self, text): return len(text)\n"
        "    def flush(self): raise OSError('report stream unavailable')\n"
        f"sys.{stream} = sys.__{stream}__ = FailedStream()\n"
        f"cli.dispatch = lambda args: {{'result': {{'status': {status!r}}}, "
        "'delivery': {'status': 'delivered'}}\n"
    )
    if stream == "stderr":
        script += ("def fail(value): raise ValueError('report formatting failed')\n"
                   "cli.owner_money = fail\n")
    script += "raise SystemExit(cli.main(['job', 'show', 'offline']))\n"
    result = subprocess.run([sys.executable, "-c", script], cwd=REPO, capture_output=True, timeout=60)
    assert result.returncode == expected, result.stderr.decode("utf-8", errors="replace")
    assert b"Exception ignored" not in result.stderr
    if stream == "stdout":
        warning = json.loads(result.stderr.decode("utf-8"))
        assert warning["status"] == "reporting_failed" and warning["run_status"] == status
