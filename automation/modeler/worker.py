"""Host-side Blender worker: runs the harness in a fresh headless Blender process with a wall-clock limit.

Every run is isolated (a new process, a factory-empty scene, its own output folder), so a failed program cannot
damage another candidate. Nothing is retried silently; the result dict carries the harness's own report or the
process failure.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import time

from .paths import BLENDER_DIR, blender_executable

HARNESS = BLENDER_DIR / "harness.py"
DEFAULT_TIME_LIMIT_S = 300


class BlenderUnavailable(RuntimeError):
    pass


def run_harness(job: dict, out_dir: Path, *, time_limit_s: int = DEFAULT_TIME_LIMIT_S) -> dict:
    """Write ``job`` (with out_dir/lib_dir filled) to out_dir/job.json, run Blender, return the result dict
    (``ok`` False with ``process_error`` when the process failed or timed out)."""
    exe = blender_executable()
    if exe is None:
        raise BlenderUnavailable("Blender 5.x not found; set MODELER_BLENDER")
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    job = dict(job)
    job["out_dir"] = str(out_dir)
    job.setdefault("lib_dir", str(BLENDER_DIR))
    job_path = out_dir / "job.json"
    job_path.write_text(json.dumps(job, indent=1), encoding="utf-8")
    result_path = out_dir / "result.json"
    if result_path.exists():
        result_path.unlink()
    cmd = [str(exe), "-b", "--factory-startup", "--python", str(HARNESS), "--", str(job_path)]
    t0 = time.monotonic()
    stdout = stderr = ""
    process_error = None
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=time_limit_s)
        stdout, stderr, returncode = proc.stdout, proc.stderr, proc.returncode
    except subprocess.TimeoutExpired as e:
        stdout = (e.stdout or b"").decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        stderr = (e.stderr or b"").decode("utf-8", "replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
        returncode = None
        process_error = f"timeout after {time_limit_s}s"
    (out_dir / "blender.stdout.log").write_text(stdout or "", encoding="utf-8")
    (out_dir / "blender.stderr.log").write_text(stderr or "", encoding="utf-8")
    if result_path.exists():
        result = json.loads(result_path.read_text(encoding="utf-8"))
    else:
        result = {"ok": False, "error": process_error or f"Blender exited {returncode} without a result",
                  "module_results": [], "renders": [], "inventory": []}
    result["process"] = {"returncode": returncode, "seconds": round(time.monotonic() - t0, 2), "error": process_error,
                         "command": cmd, "stdout_tail": (stdout or "")[-2000:], "stderr_tail": (stderr or "")[-2000:]}
    if process_error:
        result["ok"] = False
    return result
