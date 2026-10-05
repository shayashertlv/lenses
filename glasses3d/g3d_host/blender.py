"""Run glasses3d Blender scripts headless from the host (WP3-WP6 proofs never write the .blend files they read).

The live Astra session uses the WP2 worker instead; this is the one-shot path: `blender -b <scene>.blend --python ...`.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

G3D = Path(__file__).resolve().parents[1]                  # glasses3d/
BLENDER_DEFAULT = Path(r"C:/Program Files/Blender Foundation/Blender 5.2/blender.exe")


def blender_exe() -> Path | None:
    env = os.environ.get("G3D_BLENDER")
    for cand in ([Path(env)] if env else []) + [BLENDER_DEFAULT]:
        if cand.exists():
            return cand
    found = shutil.which("blender")
    return Path(found) if found else None


def run(blend: str | Path | None, script: str | Path, args: list[str], timeout_s: float = 300.0,
        python_expr: str | None = None) -> subprocess.CompletedProcess:
    """Run `script` in a fresh headless Blender (factory settings, no autoexec). `blend=None` starts empty."""
    exe = blender_exe()
    if exe is None:
        raise FileNotFoundError("Blender 5.2 not found (set G3D_BLENDER)")
    cmd = [str(exe), "-b", "--factory-startup", "--disable-autoexec"]
    if blend is not None:
        cmd.append(str(blend))
    if python_expr:
        cmd += ["--python-expr", python_expr]
    cmd += ["--python", str(script), "--"] + [str(a) for a in args]
    env = {k: v for k, v in os.environ.items() if not k.upper().endswith(("_API_KEY", "_TOKEN", "_SECRET"))}
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s, env=env, encoding="utf-8",
                          errors="replace")


def export_copy(blend: str | Path, out_glb: str | Path, timeout_s: float = 300.0) -> dict:
    """Export `blend` to `out_glb` and return the exporter receipt.

    Blender runs on the ORIGINAL .blend, read-only: g3d/cli.py never saves (headless Blender has no autosave and
    autoexec is disabled), so image paths stored relative to the .blend ('//tex/print.png') resolve exactly as they
    do for the author. (The name is historical: an earlier version exported a temporary copy, which silently lost
    every relative texture.)"""
    blend, out_glb = Path(blend).resolve(), Path(out_glb)
    with tempfile.TemporaryDirectory(prefix="g3d-export-") as td:
        receipt_path = Path(td) / "receipt.json"
        proc = run(blend, G3D / "g3d" / "cli.py", ["export", out_glb.resolve(), receipt_path], timeout_s)
        if not receipt_path.exists():
            return {"status": "failed", "error": "Blender wrote no receipt", "returncode": proc.returncode,
                    "stderr_tail": proc.stderr[-2000:], "stdout_tail": proc.stdout[-2000:]}
        return json.loads(receipt_path.read_text(encoding="utf-8"))


def export_and_validate(blend: str | Path, out_glb: str | Path, timeout_s: float = 300.0) -> dict:
    """Export `blend`, then run validate_spec on the result (PLAN WP3: the validator runs on every export and its
    report goes to Astra). Returns {"export": receipt, "spec": report-or-None}. validate_spec never raises."""
    from .spec import validate_spec
    receipt = export_copy(blend, out_glb, timeout_s)
    spec = validate_spec(out_glb) if receipt.get("status") == "exported" else None
    return {"export": receipt, "spec": spec}
