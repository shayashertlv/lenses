"""Headless entry point inside Blender:

    blender -b --factory-startup <scene>.blend --python glasses3d/g3d/cli.py -- <command> [args...]

Commands (the receipt is written whenever its path is known, also on failure and on a usage error such as an option
without its value (status refused, code E_ARGS); Blender's exit code does not carry script failures):

    export <out.glb> <receipt.json>
    seat <args.json> <receipt.json> [--save-copy <new.blend>] [--export <out.glb>]
    hide-eyewire <args.json> <receipt.json> [--save-copy <new.blend>] [--export <out.glb>]
    check-seating <args.json> <receipt.json>
    run <steps.json> <receipt.json> [--save-copy <new.blend>] [--export <out.glb>]

`args.json` holds the keyword arguments of one call (g3d.seat_lens / g3d.hide_eyewire / g3d.check_lens_seating), or a
list of them run in order; `steps.json` is a list of {"op": "seat_lens" | "hide_eyewire" | "check_lens_seating" |
"unseat_lens" | "unhide_eyewire", ...kwargs}. Objects are passed by name.

The CLI never saves the .blend it was started on. `--save-copy` writes the changed scene to a NEW path (refused when
the path exists or is the input file); `--export` exports the changed scene with the WP3 exporter.
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


OPS = {"seat": "seat_lens", "hide-eyewire": "hide_eyewire", "check-seating": "check_lens_seating"}


def _call(op: str, kwargs: dict) -> dict:
    from glasses3d.g3d import seat_bpy
    fn = getattr(seat_bpy, op, None)
    if op not in ("seat_lens", "hide_eyewire", "check_lens_seating", "unseat_lens", "unhide_eyewire") or fn is None:
        return {"status": "refused", "code": "E_ARGS", "message": f"unknown op {op!r}"}
    try:
        return fn(**kwargs)
    except TypeError as exc:                           # wrong keyword arguments
        return {"status": "refused", "code": "E_ARGS", "message": f"{op}: {exc}"}


def _options(rest: list[str]) -> tuple[list[str], dict, list[str]]:
    """(positional arguments, options, usage errors). Never raises: a usage error is reported in the receipt."""
    pos, opt, err = [], {}, []
    i = 0
    while i < len(rest):
        a = rest[i]
        if a in ("--save-copy", "--export"):
            if i + 1 >= len(rest) or rest[i + 1].startswith("--"):
                err.append(f"{a} needs a path")
                i += 1
                continue
            if a[2:] in opt:
                err.append(f"{a} given twice")
            opt[a[2:]] = rest[i + 1]
            i += 2
        elif a.startswith("--"):
            err.append(f"unknown option {a!r}")
            i += 1
        else:
            pos.append(a)
            i += 1
    return pos, opt, err


def _save_copy(path: str) -> dict:
    import bpy
    p = Path(path).resolve()
    src = Path(bpy.data.filepath).resolve() if bpy.data.filepath else None
    if src is not None and p == src:
        return {"status": "refused", "message": "--save-copy must be a new path, not the input file"}
    if p.exists():
        return {"status": "refused", "message": f"--save-copy target exists: {p}"}
    p.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(p), copy=True, check_existing=False)
    return {"status": "saved", "path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}


def _run_calls(cmd: str, spec, opt: dict) -> dict:
    receipt = {"command": cmd, "status": "failed", "results": []}
    try:
        if cmd == "run":
            steps = spec if isinstance(spec, list) else [spec]
            for st in steps:
                st = dict(st)
                op = st.pop("op", None)
                receipt["results"].append({"op": op, **_call(op, st)})
        else:
            calls = spec if isinstance(spec, list) else [spec]
            for kw in calls:
                receipt["results"].append({"op": OPS[cmd], **_call(OPS[cmd], dict(kw))})
        if "save-copy" in opt:
            receipt["saved_copy"] = _save_copy(opt["save-copy"])
        if "export" in opt:
            receipt["export"] = _export(Path(opt["export"]).resolve())
        receipt["status"] = "ok"
    except Exception as exc:
        receipt.update(error=f"{type(exc).__name__}: {exc}", traceback=traceback.format_exc(limit=8))
    return receipt


USAGE = "usage: blender -b <scene>.blend --python glasses3d/g3d/cli.py -- <command> ... (see the module docstring)"


def _write(receipt_path: Path, receipt: dict) -> None:
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(receipt, indent=1, default=str), encoding="utf-8")


def run() -> None:
    """The receipt is written whenever its path is known, a usage error included (status refused, E_ARGS); without
    a receipt path there is nowhere to report, so the usage goes to stderr (SystemExit)."""
    args = _args()
    if not args:
        raise SystemExit(USAGE)
    cmd, rest = args[0], args[1:]
    if cmd == "export":
        if len(rest) < 2:
            raise SystemExit(f"export needs <out.glb> <receipt.json>; {USAGE}")
        receipt_path = Path(rest[1])
        if len(rest) > 2:
            receipt = {"command": cmd, "status": "refused", "code": "E_ARGS",
                       "message": f"export takes <out.glb> <receipt.json>, got extra arguments {rest[2:]}",
                       "output_written": False}
        else:
            receipt = _export(Path(rest[0]).resolve())
        _write(receipt_path, receipt)
    elif cmd in OPS or cmd == "run":
        pos, opt, err = _options(rest)
        if len(pos) < 2:
            raise SystemExit(f"{cmd} needs <args.json> <receipt.json>{' (' + '; '.join(err) + ')' if err else ''}; "
                             f"{USAGE}")
        receipt_path = Path(pos[1])
        if len(pos) > 2:
            err.append(f"extra arguments {pos[2:]}")
        try:
            if err:
                receipt = {"command": cmd, "status": "refused", "code": "E_ARGS", "message": "; ".join(err),
                           "results": []}
            else:
                spec = json.loads(Path(pos[0]).read_text(encoding="utf-8"))
                receipt = _run_calls(cmd, spec, opt)
        except Exception as exc:
            receipt = {"command": cmd, "status": "failed", "error": f"{type(exc).__name__}: {exc}",
                       "traceback": traceback.format_exc(limit=6)}
        _write(receipt_path, receipt)
    else:
        raise SystemExit(f"unknown command {cmd!r}; {USAGE}")


run()
