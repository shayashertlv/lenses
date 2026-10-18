"""README command/flag contracts against the parsers, without dispatching paid or native work."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shlex
import subprocess

import pytest

from glasses3d.agent import cli, edits, job_run, edit_capabilities
from glasses3d.agent.temple_lab import cli as temple_cli, tools as temple_tools
from .test_edit_capabilities import mapping
from .test_owner_edit_lifecycle_review import selected_source


ROOT = Path(__file__).resolve().parents[2]
FLAGS = re.compile(r"(?<![\w-])--[A-Za-z][A-Za-z0-9-]*")


def subcommands(parser):
    return next(action.choices for action in parser._actions if isinstance(action, argparse._SubParsersAction))


def options(parser):
    return {flag for action in parser._actions for flag in action.option_strings}


def check_job_document(text):
    root = cli.parser()
    job = subcommands(root)["job"]
    commands = subcommands(job)
    # Prose noun phrases are not CLI mentions: only inline-code/code-fence snippets and reference table rows.
    prose = re.sub(r"```.*?```", "", text, flags=re.S)
    snippets = re.findall(r"`([^`]+)`", prose) + re.findall(r"```\w*\n(.*?)```", text, re.S)
    mentioned = {name for snippet in snippets for name in re.findall(r"\bjob ([a-z][a-z-]*)", snippet)}
    assert mentioned <= commands.keys(), mentioned - commands.keys()
    assert commands.keys() <= mentioned, commands.keys() - mentioned
    all_flags = options(root) | options(job) | set().union(*(options(p) for p in commands.values()))
    assert set(FLAGS.findall(text)) <= all_flags, set(FLAGS.findall(text)) - all_flags
    # Every CLI-reference row is checked against its specific subcommand, not the union.
    for line in text.splitlines():
        match = re.match(r"\| `job ([a-z-]+)` \|", line)
        if match:
            assert set(FLAGS.findall(line)) <= options(commands[match[1]]), line
    # Full shell examples are actually parsed. This tests required values, choices, and flag ownership too.
    for block in re.findall(r"```powershell\n(.*?)```", text, re.S):
        for line in block.splitlines():
            if " -m glasses3d job " in line:
                argv = shlex.split(line.split(" -m glasses3d ", 1)[1])
                root.parse_args(argv)


def test_job_readme_commands_and_all_flags_exist_in_the_real_parser():
    check_job_document((ROOT / "agent/README.md").read_text(encoding="utf-8"))


@pytest.mark.parametrize("addition", ["`job teleport`", "A flag in prose: `--misspelled-flag`.",
                                       "| `job show` | `--settled-only` |"])
def test_contract_rejects_unknown_prose_commands_flags_and_wrong_subcommand(addition):
    text = (ROOT / "agent/README.md").read_text(encoding="utf-8")
    with pytest.raises(AssertionError):
        check_job_document(text + "\n" + addition)


def temple_parser(monkeypatch):
    """Capture the real inline parser before its dispatch, even when passed a broken command."""
    class Captured(BaseException):
        pass
    captured = []
    def capture(parser, *args, **kwargs):
        captured.append(parser)
        raise Captured
    with monkeypatch.context() as patch:
        patch.setattr(argparse.ArgumentParser, "parse_args", capture)
        with pytest.raises(Captured):
            temple_cli.main([])
    return captured[0]


def test_temple_readme_uses_its_own_parser_and_lists_the_live_tools(monkeypatch):
    parser = temple_parser(monkeypatch)
    text = (ROOT / "agent/temple_lab/README.md").read_text(encoding="utf-8")
    assert set(FLAGS.findall(text)) <= options(parser)
    commands = set(next(a.choices for a in parser._actions if a.dest == "action"))
    examples = re.findall(r"^-m glasses3d\.agent\.temple_lab\.cli (.+)$", text, re.M)
    assert {shlex.split(line)[0] for line in examples} == commands
    for line in examples:
        parser.parse_args(shlex.split(line))
    table = set(re.findall(r"^\| `([a-z_]+)` \|", text, re.M))
    assert set(temple_tools.TOOL_NAMES) <= table
    assert "19,000" in text and "no v3 run exists" not in text


def test_arview_readme_flags_exist_in_capture_or_serve_parser():
    """Use the actual pure Node parsers; missing values are fine, unknown options are not."""
    text = (ROOT / "arview/README.md").read_text(encoding="utf-8")
    # These belong to explicitly named external commands, not arview's capture/serve CLI.
    external = {"--noEmit": "tsc --noEmit", "--test": "node --test",
                "--full": "glasses3d.g3d_host.spec --full", "--download": "npm run assets -- --download"}
    for flag in ("--use-angle", "--enable-gpu", "--ignore-gpu-blocklist"):
        external[flag] = "--use-angle=d3d11 --enable-gpu --ignore-gpu-blocklist"
    documented = set(FLAGS.findall(text))
    for flag, marker in external.items():
        if flag in documented:
            assert marker in " ".join(text.split())
    # Entry points consume help before their pure option parser; exercise that real path too.
    if "--help" in documented:
        for entry in ("capture", "serve"):
            for help_flag in ("--help", "-h"):
                help_result = subprocess.run(["node", f"qa/{entry}.mjs", help_flag], cwd=ROOT / "arview",
                                             capture_output=True, text=True, encoding="utf-8", check=True)
                assert "usage:" in help_result.stdout.lower()
    flags = sorted(documented - external.keys() - {"--help"})
    script = """
import {parseCaptureArgs} from './qa/capture/cli.ts';
import {parseServeArgs} from './qa/serve/cli.ts';
const flags = JSON.parse(process.argv[1]);
const recognized = (parse, flag) => {
  try { parse([flag]); return true; }
  catch (error) {
    if (error.name !== 'UsageError') throw error;
    return !/Unknown option/i.test(error.message);
  }
};
console.log(JSON.stringify(Object.fromEntries(flags.map(flag => [flag,
  recognized(parseCaptureArgs, flag) || recognized(parseServeArgs, flag)]))));
"""
    proc = subprocess.run(["node", "--input-type=module", "-e", script, json.dumps(flags + ["--fake-readme-option"])],
                          cwd=ROOT / "arview", capture_output=True, text=True, encoding="utf-8", check=True)
    known = json.loads(proc.stdout)
    assert not known.pop("--fake-readme-option")
    assert all(known.values()), [flag for flag, ok in known.items() if not ok]
    commands = set(re.findall(r"\barview (capture|serve)\b", text))
    assert commands == {"capture", "serve"}


@pytest.mark.parametrize("settled_only", [False, True])
def test_edit_cli_binds_selected_money_policy_without_approval_or_execution(selected_source, tmp_path,
                                                                          monkeypatch, settled_only):
    fixture = selected_source
    monkeypatch.setattr(edits, "RUNTIME_ROOT", tmp_path / "runtime")
    words = "Move the shown face."
    coverage = edit_capabilities.check(words, mapping(words))
    report = tmp_path / "coverage.json"
    edit_capabilities.write_report(report, coverage)
    argv = ["job", "edit", str(fixture.source), "--request", words, "--coverage", str(report),
            "--cap", "20", "--settled-stop", "20", "--effort", "high"]
    if settled_only:
        argv.append("--settled-only")
    result = cli.dispatch(cli.parser().parse_args(argv))
    assert result["status"] == "awaiting_separate_run_approval"
    round_root = Path(result["round_path"])
    settings, cap = job_run.prepared_settings(round_root, result["run_id"])
    assert settings.settled_only is settled_only
    assert settings.effort == "high" and settings.settled_stop_usd == "20.000000"
    assert cap == "20.000000"
    assert result["request"]["request"]["settings"].get("settled_only", False) is settled_only
    assert not list(round_root.glob("runs/*/approval.json"))
    assert not list(round_root.glob("runs/*/budget.json"))
    assert not (round_root / "work").exists()
    # Legacy ordinary defaults remain available and unchanged.
    defaults = job_run.settings_default()
    assert defaults.effort == "max" and defaults.settled_only is False
