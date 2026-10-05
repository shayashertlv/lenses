"""Headless entry point inside Blender:

    blender -b --factory-startup <scene>.blend --python glasses3d/g3d/cli.py -- <command> [args...]

Commands: `export <out.glb> <receipt.json>`. The receipt is always written (also on failure); the exit status is
not relied on (Blender's own exit code does not carry script failures reliably).

The CLI never saves the .blend it was started on, so the host may run it on the ORIGINAL scene file (relative image
paths then resolve exactly as they do for the author). Every command runs read-only on bpy.data's file.
"""
import hashlib
import json
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))      # the repo root, so `glasses3d` imports


def _args() -> list[str]:
    return sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


def _export(out: Path) -> dict:
    try:
        from glasses3d.g3d.export_glb import export_glb
        return export_glb(out)
    except Exception as exc:                           # report every failure in the receipt
        receipt = {"status": "failed", "error": f"{type(exc).__name__}: {exc}",
                   "traceback": traceback.format_exc(limit=6), "output_written": False}
        if out.is_file():                              # never mistake an earlier export for this one
            receipt["stale_output_sha256"] = hashlib.sha256(out.read_bytes()).hexdigest()
            receipt["stale_output_note"] = ("the file at the output path is from an EARLIER export; this export "
                                            "wrote nothing")
        return receipt


def run() -> None:
    args = _args()
    if not args:
        raise SystemExit("usage: ... --python glasses3d/g3d/cli.py -- export <out.glb> <receipt.json>")
    cmd, rest = args[0], args[1:]
    if cmd == "export":
        out, receipt_path = Path(rest[0]).resolve(), Path(rest[1])
        receipt = _export(out)
        receipt_path.write_text(json.dumps(receipt, indent=1), encoding="utf-8")
    else:
        raise SystemExit(f"unknown command {cmd!r}")


run()
