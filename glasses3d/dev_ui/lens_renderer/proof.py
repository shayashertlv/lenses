"""Prepare a read-only product copy, then run E2 renderer proofs in a detached serial job.

The ``prepare`` command performs no rendering. Start the proof only after the
parallel task's exit has been confirmed; it owns the native/browser check slot.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from glasses3d.agent.delivery import load_delivery
from glasses3d.agent.owner_lens_edit import read_lens_materials
from glasses3d.agent.procs import spawn_detached

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
CHECK_ENV = {"OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1",
             "NUMEXPR_NUM_THREADS": "1", "PYTHONDONTWRITEBYTECODE": "1"}


def _require_task18_exit() -> str:
    status = (REPO / "glasses3d/data/review_20261018/codex/task18_status.txt").read_text(encoding="utf-8")
    if not any(line.startswith("exit") for line in status.splitlines()):
        raise ValueError("Task 18 must exit before the native renderer proof.")
    return status


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def prepare(job: Path, out: Path) -> dict:
    """Verify the delivery before copying only its model and panel DTO."""
    job, out = job.resolve(), out.resolve()
    if out.is_relative_to(job):
        raise ValueError("Proof inputs must be outside the original job.")
    snapshot = load_delivery(job)
    source = Path(snapshot["model"])
    payload = source.read_bytes()
    rows = read_lens_materials(payload)
    if _sha(payload) != snapshot["glb_sha256"] or rows["glb_sha256"] != snapshot["glb_sha256"]:
        raise ValueError("The original delivery changed during proof preparation.")
    out.mkdir(parents=True, exist_ok=False)
    (out / "model.glb").write_bytes(payload)
    _write(out / "materials.json", rows)
    receipt = {
        "schema": "g3d.lens_renderer_inputs/1", "original_job": str(job),
        "original_model": str(source), "delivery_id": snapshot["id"],
        "source_glb_sha256": snapshot["glb_sha256"],
        "source_manifest_sha256": snapshot["manifest_sha256"],
        "copy_glb_sha256": _sha((out / "model.glb").read_bytes()),
        "materials_sha256": _sha((out / "materials.json").read_bytes()),
        "source_unchanged": _sha(source.read_bytes()) == snapshot["glb_sha256"],
    }
    _write(out / "inputs.json", receipt)
    if not receipt["source_unchanged"]:
        raise ValueError("The original delivery changed during proof preparation.")
    return receipt


def run(inputs: Path, out: Path) -> int:
    task18_status = _require_task18_exit()
    node = shutil.which("node")
    if not node:
        raise RuntimeError("Node is required for the pinned AR renderer proof.")
    receipt = json.loads((inputs / "inputs.json").read_text(encoding="utf-8"))
    if (_sha((inputs / "model.glb").read_bytes()) != receipt["copy_glb_sha256"]
            or _sha((inputs / "materials.json").read_bytes()) != receipt["materials_sha256"]):
        raise ValueError("Prepared proof input bytes changed.")
    cases = [("synthetic", []), ("opaque", ["--opaque", "true"]),
             ("oakley", ["--model", str(inputs / "model.glb"), "--materials", str(inputs / "materials.json")])]
    results = []
    for name, arguments in cases:
        target = out / name
        if target.exists():
            raise ValueError("Each proof attempt must use a fresh output directory.")
        print(f"Starting {name}: {target}", flush=True)
        process = subprocess.run([node, str(HERE / "parity.mjs"), "--out", str(target), *arguments], cwd=REPO,
                                 env=dict(os.environ, **CHECK_ENV),
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        results.append({"case": name, "exit_code": process.returncode, "output": str(target)})
        print(f"Finished {name}: exit {process.returncode}", flush=True)
    original_unchanged = _sha(Path(receipt["original_model"]).read_bytes()) == receipt["source_glb_sha256"]
    passed = all(row["exit_code"] == 0 for row in results) and original_unchanged
    _write(out / "summary.json", {"schema": "g3d.lens_renderer_proofs/1", "cases": results,
                                  "original_unchanged": original_unchanged, "passed": passed,
                                  "task18_status": task18_status, "check_environment": CHECK_ENV})
    return 0 if passed else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prep = commands.add_parser("prepare")
    prep.add_argument("--job", type=Path, required=True)
    prep.add_argument("--out", type=Path, required=True)
    for verb in ("start", "run"):
        command = commands.add_parser(verb)
        command.add_argument("--inputs", type=Path, required=True)
        command.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "prepare":
        print(json.dumps(prepare(args.job, args.out), indent=2))
        return 0
    inputs, out = args.inputs.resolve(), args.out.resolve()
    if args.command == "run":
        return run(inputs, out)
    _require_task18_exit()
    out.mkdir(parents=True, exist_ok=False)
    detached = spawn_detached(
        [sys.executable, "-X", "utf8", "-m", "glasses3d.dev_ui.lens_renderer.proof", "run",
         "--inputs", str(inputs), "--out", str(out)], cwd=REPO,
        log_path=out / "proof.log", status_path=out / "status.json", keep_awake=True, env=CHECK_ENV)
    print(json.dumps({"keeper_pid": detached.pid, "status": str(out / "status.json"), "output": str(out)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
