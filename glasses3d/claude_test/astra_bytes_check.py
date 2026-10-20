"""Offline Astra wire regression against a separately checked-out source tree.

Run with the Claude test venv (the import-side-effect cases need its SDK)::

    python -m glasses3d.claude_test.astra_bytes_check \
        --baseline-root PATH_TO_700b673_WORKTREE --output-dir PRIVATE_OUTPUT

Omit --baseline-root for the current-source unit check. Each capture runs in a
fresh interpreter with the same dependencies, deterministic provider/session/run
IDs, and a synthetic one-photo fixture. It uses real job preparation, the entire
tool registry, the session planner, Agents Runner, budget guard, and budget_status
executor. No worker starts and no approval, credential or live client is used.

Only JSON object ordering and insignificant JSON whitespace are canonicalized;
no request field, image, ID, instruction or tool result is redacted or normalized.
This checks one fixed opening and its tool follow-up, not every possible run.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys

SCHEMA = "g3d.astra_request_bytes/1"
BASELINE_REF = "700b673"
RUN_ID = "m13-astra-bytes"
OFFLINE_KEY = "sk-offline-m13-astra-bytes"
REQUEST_NAMES = ("opening", "tool-followup")
IMPORT_MODES = ("clean", "claude-before", "claude-after")
# __main__ executes the CLI on import; import its cli module without executing it.
CLAUDE_MODULES = (
    "glasses3d.claude_test",
    "glasses3d.claude_test.pricing",
    "glasses3d.claude_test.money",
    "glasses3d.claude_test.transport",
    "glasses3d.claude_test.images",
    "glasses3d.claude_test.conversation",
    "glasses3d.claude_test.tool_adapter",
    "glasses3d.claude_test.scripted",
    "glasses3d.claude_test.runner",
    "glasses3d.claude_test.cli",
    "glasses3d.claude_test.proof_c3",
)


def canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _write_json(path: Path, value: object) -> None:
    path.write_bytes(canonical_bytes(value) + b"\n")


def _git(root: Path, *arguments: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), *arguments], text=True, encoding="utf-8",
    ).strip()


def _source_root(value: str | Path) -> Path:
    root = Path(value).resolve(strict=True)
    if not (root / "glasses3d/agent/loop.py").is_file():
        raise ValueError(f"not a glasses3d source root: {root}")
    return root


def _import_claude() -> None:
    for name in CLAUDE_MODULES:
        importlib.import_module(name)


def _module_sources(root: Path) -> dict[str, str]:
    """Reject source bleed from PYTHONPATH, the caller's checkout or site-packages."""
    result = {}
    for name, module in sorted(sys.modules.items()):
        if name != "glasses3d" and not name.startswith("glasses3d."):
            continue
        filename = getattr(module, "__file__", None)
        if filename is None:
            continue
        path = Path(filename).resolve(strict=True)
        if not path.is_relative_to(root):
            raise AssertionError(f"{name} was imported from another source tree: {path}")
        result[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    for required in ("glasses3d/agent/loop.py", "glasses3d/agent/tools/__init__.py",
                     "glasses3d/agent/job_run.py", "glasses3d/agent/session_inputs.py"):
        if required not in result:
            raise AssertionError(f"real Astra source was not imported: {required}")
    return result


async def _capture_requests(output: Path, mode: str) -> dict:
    # Imported here, after the worker selected and verified its source root.
    from agents import Runner
    from PIL import Image
    from glasses3d.agent import job_run, jobs, loop, replay
    from glasses3d.agent.budget import BudgetGuard, cap_to_micro
    from glasses3d.agent.offline import offline_counting_client, offline_inference_client
    from glasses3d.agent.receipts import BlobStore
    from glasses3d.agent.session_inputs import plan_session_input

    fixture = output / "fixture"
    images = fixture / "images"
    images.mkdir(parents=True)
    # Fixed RGB test pixels only. There is no owner job, product or geometry here.
    photo = Image.new("RGB", (32, 24))
    photo.putdata([(x * 7 % 256, y * 11 % 256, (x + y) * 5 % 256)
                   for y in range(24) for x in range(32)])
    photo.save(images / "front.png")
    views = fixture / "views.json"
    _write_json(views, {"front.png": {"kind": "front", "side": "both"}})
    job = jobs.create_job(
        images=images, views=views, name="Astra request bytes fixture",
        description="Synthetic offline request regression fixture.",
        jobs_root=fixture / "jobs",
    )
    scripted = replay.ScriptedOpenAI([
        replay.tool_call("budget_status", {}, call_id="call_0001",
                         summary="Synthetic offline tool call.", counted=12_000),
        replay.message("Synthetic capture complete.", counted=12_300),
    ])
    spec, scene = job_run.prepare_run(job, RUN_ID, offline_handler=scripted.handle)
    # Keep the default product settings under test, including max effort, required
    # tools, disabled parallelism and reset behavior. Runner's two-reply bound is
    # local to this capture and is not a change to any production setting.
    agent = loop.build_agent(spec.instructions, spec.tools, spec.settings)
    if mode == "claude-after":
        _import_claude()

    invocation = loop.invocation_text(
        run_id=spec.run_id, scene_note=spec.scene_note,
        cap_micro=cap_to_micro(spec.requested_cap_usd), rollback=None,
        settled_stop_micro=cap_to_micro(spec.settings.settled_stop_usd),
        settled_only=spec.settings.settled_only,
    )
    plan = plan_session_input(
        [], prompt=spec.brief, photos=spec.photos, invocation_text=invocation,
        owner_notes=spec.owner_notes,
    )
    counter = offline_counting_client(scripted.handle, api_key=OFFLINE_KEY)
    guard = BudgetGuard(
        counter, output / "budget.json", spec.requested_cap_usd, run_id=RUN_ID,
        settled_stop_usd=spec.settings.settled_stop_usd,
        settled_only=spec.settings.settled_only,
    )
    client = offline_inference_client(scripted.handle, api_key=OFFLINE_KEY, event_hooks={
        "request": [guard.before_request], "response": [guard.after_response],
    })
    session = loop.BlobSession(loop.SESSION_ID, output / "session.sqlite",
                               blobs=BlobStore(output / "blobs"))
    try:
        await Runner.run(
            agent, [{"role": "user", "content": plan.content}], max_turns=2,
            session=session, run_config=loop.build_run_config(client),
            context=loop.RunStatus(guard),
        )
    finally:
        session.close()
        await client.close()
        await counter.close()
        scene.close()

    assert not scripted.violations, scripted.violations
    assert not scripted.unexpected, scripted.unexpected
    assert len(scripted.creates) == len(scripted.counts) == 2
    assert scene.s.worker is None, "the bytes check must not start Blender"
    assert len(scene.events) == 1
    assert (scene.events[0]["tool"], scene.events[0]["call_id"], scene.events[0]["status"]) == (
        "budget_status", "call_0001", "ok",
    ), scene.events
    assert len(guard.ledger.rows) == 2
    assert all(row["outcome"] == "settled" for row in guard.ledger.rows)
    assert plan.receipt["images_sent"] == 1
    first, second = scripted.creates
    assert len(replay.input_images(first)) == len(replay.input_images(second)) == 1
    assert second["input"][:len(first["input"])] == first["input"]
    calls = [row for row in second["input"] if row.get("type") == "function_call"]
    outputs = [row for row in second["input"] if row.get("type") == "function_call_output"]
    assert len(calls) == len(outputs) == 1
    assert calls[0]["name"] == "budget_status"
    assert calls[0]["call_id"] == outputs[0]["call_id"] == "call_0001"
    assert any(row.get("type") == "reasoning" and
               row.get("encrypted_content") == "gAAAAB-opaque-continuation-data"
               for row in second["input"]), "the SDK must replay opaque reasoning unchanged"

    records = []
    for name, body, count in zip(REQUEST_NAMES, scripted.creates, scripted.counts):
        payload = canonical_bytes(body)
        # Random job IDs and worktree-specific absolute paths must not leak into
        # the request. Do not scrub them if this ever fails.
        assert job.name.encode() not in payload
        assert str(output).encode() not in payload
        filename = name + ".json"
        (output / filename).write_bytes(payload)
        (output / (name + ".count.json")).write_bytes(canonical_bytes(count))
        records.append({"name": name, "file": filename, "bytes": len(payload),
                        "sha256": hashlib.sha256(payload).hexdigest()})
    return {
        "requests": records,
        "tool_count": len(first["tools"]),
        "settings": spec.settings.approval_settings(),
        "tool_call": {"name": "budget_status", "call_id": "call_0001", "status": "ok"},
        "mock_inference_requests": len(scripted.creates),
        "mock_count_requests": len(scripted.counts),
        "production_requests": 0,
    }


def capture_worker(source_root: Path, output: Path, mode: str) -> dict:
    """Internal fresh-process entry point; never import a second source in-process."""
    if any(name == "glasses3d" or name.startswith("glasses3d.") for name in sys.modules):
        raise RuntimeError("capture worker must run by file path in a fresh interpreter")
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(source_root))
    os.environ["G3D_OFFLINE"] = "1"
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        os.environ.pop(name, None)
    output.mkdir(parents=True, exist_ok=False)
    from glasses3d.agent.offline import (
        LoopbackOnly, ProductionTransportTripwire, disable_sdk_tracing,
    )

    with LoopbackOnly() as network, ProductionTransportTripwire() as tripwire:
        disable_sdk_tracing()
        if mode == "claude-before":
            _import_claude()
        captured = asyncio.run(_capture_requests(output, mode))
    assert network.attempts == [], network.attempts
    assert tripwire.constructions == [], tripwire.constructions
    sources = _module_sources(source_root)
    imported = sorted(name for name in sys.modules if name.startswith("glasses3d.claude_test"))
    if mode == "clean":
        assert imported == [], imported
    else:
        assert set(CLAUDE_MODULES) <= set(imported), imported
    result = {
        "schema": SCHEMA, "source_root": str(source_root),
        "source_commit": _git(source_root, "rev-parse", "HEAD"),
        "import_mode": mode, "python": sys.executable,
        "source_sha256": sources, "claude_imports": imported,
        "non_loopback_attempts": network.attempts,
        "production_transport_constructions": tripwire.constructions,
        **captured,
    }
    _write_json(output / "capture.json", result)
    return result


def _subprocess_capture(source: Path, output: Path, mode: str) -> dict:
    env = dict(os.environ)
    # An inherited PYTHONPATH could otherwise make a baseline capture use HEAD.
    env.pop("PYTHONPATH", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONHASHSEED"] = "0"
    env["PYTHONUTF8"] = "1"
    env["G3D_OFFLINE"] = "1"
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        env.pop(name, None)
    command = [
        sys.executable, "-B", str(Path(__file__).resolve()), "--capture-worker",
        "--source-root", str(source), "--output-dir", str(output), "--import-mode", mode,
    ]
    started = datetime.now(timezone.utc).isoformat()
    process = subprocess.Popen(
        command, cwd=source, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
    )
    stdout, stderr = process.communicate()
    ended = datetime.now(timezone.utc).isoformat()
    output.mkdir(parents=True, exist_ok=True)
    (output / "stdout.log").write_text(stdout, encoding="utf-8")
    (output / "stderr.log").write_text(stderr, encoding="utf-8")
    receipt = {
        "argv": command, "cwd": str(source), "pid": process.pid,
        "started_utc": started, "ended_utc": ended, "exit_code": process.returncode,
        "stdout_log": "stdout.log", "stderr_log": "stderr.log",
    }
    _write_json(output / "capture-process.json", receipt)
    if process.returncode:
        raise RuntimeError(f"capture failed ({mode}, {source}):\n"
                           f"{stdout}\n{stderr}")
    captured = json.loads((output / "capture.json").read_text(encoding="utf-8"))
    captured["process"] = receipt
    _write_json(output / "capture.json", captured)
    return captured


def assert_same_requests(expected: Path, actual: Path) -> None:
    """Compare the complete canonical JSON payload, with a useful failure location."""
    for name in REQUEST_NAMES:
        before = (expected / (name + ".json")).read_bytes()
        after = (actual / (name + ".json")).read_bytes()
        if before != after:
            first = next((i for i, (a, b) in enumerate(zip(before, after)) if a != b),
                         min(len(before), len(after)))
            raise AssertionError(
                f"{name} request bytes differ at byte {first}: "
                f"{expected} ({hashlib.sha256(before).hexdigest()}) != "
                f"{actual} ({hashlib.sha256(after).hexdigest()})"
            )


def run_check(*, source_root: Path, output_dir: Path,
              baseline_root: Path | None = None) -> dict:
    source_root = _source_root(source_root)
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    captures = {}
    if baseline_root is not None:
        baseline_root = _source_root(baseline_root)
        expected = _git(source_root, "rev-parse", BASELINE_REF + "^{commit}")
        if _git(baseline_root, "rev-parse", "HEAD") != expected:
            raise ValueError(f"baseline worktree must be at {BASELINE_REF}")
        if _git(baseline_root, "status", "--porcelain", "--untracked-files=all"):
            raise ValueError("baseline worktree must contain only its tracked source")
        captures["baseline"] = _subprocess_capture(
            baseline_root, output_dir / "baseline", "clean")
    for mode in IMPORT_MODES:
        captures[mode] = _subprocess_capture(source_root, output_dir / mode, mode)
        if mode != "clean":
            assert_same_requests(output_dir / "clean", output_dir / mode)
    if baseline_root is not None:
        assert_same_requests(output_dir / "baseline", output_dir / "clean")
        assert not _git(baseline_root, "status", "--porcelain", "--untracked-files=all")
    result = {
        "schema": SCHEMA, "equal": True,
        "baseline_ref": BASELINE_REF if baseline_root is not None else None,
        "captures": captures,
    }
    _write_json(output_dir / "comparison.json", result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--baseline-root", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--capture-worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--import-mode", choices=IMPORT_MODES, default="clean", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.capture_worker:
        capture_worker(_source_root(args.source_root), args.output_dir.resolve(), args.import_mode)
        return 0
    result = run_check(source_root=args.source_root, output_dir=args.output_dir,
                       baseline_root=args.baseline_root)
    print(json.dumps({
        "equal": result["equal"], "baseline_ref": result["baseline_ref"],
        "requests": result["captures"]["clean"]["requests"],
        "import_modes": list(IMPORT_MODES), "report": str(args.output_dir / "comparison.json"),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
