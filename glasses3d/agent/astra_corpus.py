"""The Astra snippet corpus: every `execute_blender_code` Astra wrote in the Blender Studio (glasses3d/plan/archive/20261019/WP2_DESIGN.md §0, §13).

`build()` copies the journaled `issued-code.py` files of the Studio jobs (data only; nothing is imported from
automation/ or the recovery archive) into the ignored `glasses3d/data/reference/astra_corpus/`, with a manifest that
names each snippet's job, run, parent tool, journal status and its recorded before/after .blend. The repo is public:
the manifest holds machine paths and therefore lives only under the ignored data folder.

Used by the safe-mode corpus gate (0 false positives on non-I/O lines) and by the chained replays of the three
owner-accepted runs (Prada 19/19, Oakley 21/21, INVU 20/20).

    python -m glasses3d.agent.astra_corpus build      # (re)build the corpus folder
    python -m glasses3d.agent.astra_corpus gate       # run the safe-mode gate and print the summary
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from pathlib import Path

from glasses3d.agent.text_io import configure_utf8_stdio

G3D = Path(__file__).resolve().parents[1]
CORPUS_DIR = G3D / "data" / "reference" / "astra_corpus"
STUDIO_JOBS = G3D.parent / "automation" / "data" / "blender_agent" / "studio" / "jobs"
RECOVERY_MOVED = (G3D.parents[1] / "lenses-recovery" / "automation-cleanup-20261003T071535Z" / "moved" / "data"
                  / "blender_agent")
SOURCES = (STUDIO_JOBS, RECOVERY_MOVED)
ASTRA = "execute_blender_code"
#: `parent_tool` is the journal's tool-call context, else its tool name (glasses3d/plan/archive/20261019/WP2_DESIGN.md's 214 count: 186 Astra tool calls
#: plus 28 host stop-checkpoint saves journaled under the same tool name); `context_parent` is the context alone
#: the three owner-accepted Studio runs and the step count of their chained replay (Astra snippets + host coating)
ACCEPTED_RUNS = {
    "prada": ("8c82d986e6c14d26b4b38f3807712ea0", "20261001T144107-ad29ba", 19),
    "oakley": ("3dff867466d449c9a6369273f0a14631", "20261001T085658-b197ec", 21),
    "invu": ("c8e81c7214a646cfa07e082c59128f10", "20261001T093641-76eea4", 20),
}
REPLAY_TOOLS = (ASTRA, "configure_lens_coating")

#: what counts as a file-I/O line (the replacement tools of glasses3d/plan/archive/20261019/WP2_DESIGN.md §4 cover these)
IO_CLASSES = (
    ("checkpoint", re.compile(r"save_as_mainfile|save_mainfile|open_mainfile|bpy\.ops\.wm\.")),
    ("self_render", re.compile(r"bpy\.ops\.render|render\.render\(|\.filepath\b|filepath_raw|file_format|"
                               r"write_still|save_render|image_settings")),
    ("self_export", re.compile(r"export_scene|bpy\.ops\.export_")),
    ("font_load", re.compile(r"fonts\.load|\bfonts\b.*\.load\(")),
    ("text_block", re.compile(r"\btexts\b")),
    ("preferences", re.compile(r"\bpreferences\b")),
    ("pack_files", re.compile(r"pack_all|bpy\.ops\.file\b|\bunpack\(|\.pack\(")),
    ("image_file", re.compile(r"images\.load|\.save\(")),
)
#: statements stripped for a replay (the snippet's own file I/O, which the host tools now do)
STRIP = re.compile(r"save_as_mainfile|save_mainfile|\.filepath\s*=|filepath_raw\s*=|render\.render\(|pack_all\(|"
                   r"file_format\s*=|bpy\.data\.texts|\btexts\.new|\.save_render\(|\.save\(\)|"
                   r"bpy\.context\.preferences")
FONT_LOAD = re.compile(r"bpy\.data\.fonts\.load\(\s*r?(['\"])(?:[A-Za-z]:)?[\\/]+Windows[\\/]+Fonts[\\/]+"
                       r"(?P<family>\w+)\.ttf\1[^)]*\)", re.I)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _job_and_run(path: Path) -> tuple[str, str]:
    parts = path.parts
    job = run = "?"
    for i, part in enumerate(parts):
        if part == "jobs" and i + 1 < len(parts):
            job = parts[i + 1]
        if part == "runs" and i + 1 < len(parts):
            run = parts[i + 1]
    if job == "?":
        for i, part in enumerate(parts):
            if part == "blender_agent" and i + 1 < len(parts):
                job = parts[i + 1]
                break
    return job, run


def build(out_dir: Path = CORPUS_DIR, sources=SOURCES) -> dict:
    """Copy every journaled snippet into `out_dir/snippets/<sha12>.py` and write `manifest.json`. Returns the manifest."""
    out_dir = Path(out_dir)
    snippets = out_dir / "snippets"
    snippets.mkdir(parents=True, exist_ok=True)
    rows = []
    for root in sources:
        if not Path(root).is_dir():
            continue
        for code_path in sorted(Path(root).rglob("issued-code.py")):
            d = code_path.parent
            raw = code_path.read_bytes()
            try:
                journal = json.loads((d / "journal.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                journal = {}
            ctx = journal.get("context") or {}
            sha = _sha(raw)
            (snippets / f"{sha[:12]}.py").write_bytes(raw)
            job, run = _job_and_run(d)
            rows.append({"id": sha[:12], "sha256": sha, "bytes": len(raw), "job": job, "run": run, "edit": d.name,
                         "parent_tool": ctx.get("parent_tool") or journal.get("tool_name"),
                         "context_parent": ctx.get("parent_tool"), "tool_name": journal.get("tool_name"),
                         "status": journal.get("status"), "started_at": journal.get("started_at"),
                         "source_dir": str(d),
                         "before_blend": str(d / "before.blend") if (d / "before.blend").is_file() else None,
                         "after_blend": str(d / "after.blend") if (d / "after.blend").is_file() else None})
    manifest = {"schema": "g3d.astra_corpus/1", "snippets": len(rows),
                "astra_snippets": sum(r["parent_tool"] == ASTRA for r in rows),
                "astra_tool_calls": sum(r["context_parent"] == ASTRA for r in rows), "rows": rows}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return manifest


def load(corpus_dir: Path = CORPUS_DIR) -> list[dict]:
    """Manifest rows with their `code` text; [] when the corpus was never built."""
    path = Path(corpus_dir) / "manifest.json"
    if not path.is_file():
        return []
    rows = json.loads(path.read_text(encoding="utf-8"))["rows"]
    for r in rows:
        r["code"] = (Path(corpus_dir) / "snippets" / f"{r['id']}.py").read_bytes().decode("utf-8", "replace")
    return rows


def io_class(line: str) -> str | None:
    """The file-I/O class of one source line, or None."""
    for name, rx in IO_CLASSES:
        if rx.search(line):
            return name
    return None


def gate(rows: list[dict], validate) -> dict:
    """Run `validate` (safe_mode.validate) over the Astra-authored snippets. A violation on a line that is not a
    file-I/O line is a false positive."""
    astra = [r for r in rows if r["parent_tool"] == ASTRA]
    out = {"astra_snippets": len(astra), "pass": 0, "refused": 0, "refused_io_only": 0, "false_positives": [],
           "io_classes": {}, "messages": {}}
    for r in astra:
        found = validate(r["code"])
        if not found:
            out["pass"] += 1
            continue
        out["refused"] += 1
        lines = r["code"].splitlines()
        io_only = True
        for v in found:
            src = lines[v.line - 1] if v.line and v.line <= len(lines) else ""
            cls = io_class(src)
            key = re.sub(r"'[^']*'", "'…'", v.message)[:100]
            out["messages"][key] = out["messages"].get(key, 0) + 1
            if cls:
                out["io_classes"][cls] = out["io_classes"].get(cls, 0) + 1
            else:
                io_only = False
                out["false_positives"].append({"id": r["id"], "job": r["job"][:8], "line": v.line,
                                               "message": v.message, "src": src.strip()[:160]})
        out["refused_io_only"] += io_only
    return out


def strip_io(code: str) -> str:
    """The snippet with only its file-I/O statements removed (glasses3d/plan/archive/20261019/WP2_DESIGN.md §0: "with only those lines removed")."""
    out = []
    for line in code.splitlines():
        m = re.match(r"(\s*)(try|else|finally)\s*:\s*(.*)$", line)
        if m and m.group(3) and STRIP.search(m.group(3)):
            out.append(f"{m.group(1)}{m.group(2)}: pass")
            continue
        m = re.match(r"(\s*)(if .*|elif .*|for .*|while .*|with .*|except.*):\s*(.+)$", line)
        if m and STRIP.search(m.group(3)):
            kept = [p for p in m.group(3).split(";") if not STRIP.search(p)]
            out.append(f"{m.group(1)}{m.group(2)}: " + (";".join(kept) if kept else "pass"))
            continue
        if STRIP.search(line):
            indent = re.match(r"\s*", line).group(0)
            kept = [p for p in line.strip().split(";") if not STRIP.search(p)]
            out.append(indent + (";".join(kept).strip() if kept else "pass"))
            continue
        out.append(line)
    return "\n".join(out)


def font_loads(code: str) -> list[str]:
    """Font families a snippet loads from C:/Windows/Fonts (replaced by the trusted load_font in a replay)."""
    return [m.group("family").lower() for m in FONT_LOAD.finditer(code)]


def replace_font_loads(code: str, names: dict) -> str:
    """`bpy.data.fonts.load('C:\\Windows\\Fonts\\X.ttf')` -> `bpy.data.fonts['<datablock name>']` (after load_font)."""
    return FONT_LOAD.sub(lambda m: f"bpy.data.fonts[{names[m.group('family').lower()]!r}]", code)


def accepted_run_steps(rows: list[dict], product: str) -> list[dict]:
    """The chained-replay steps of one accepted run, in journal order."""
    job, run, _ = ACCEPTED_RUNS[product]
    steps = [r for r in rows if r["job"] == job and r["run"] == run and r.get("context_parent") in REPLAY_TOOLS]
    return sorted(steps, key=lambda r: r["edit"])


def product_view(state: dict) -> dict:
    """The product a scene state describes: per object in a scene, its object digest, its data digest (mesh/curve/
    light/camera) and the digests of the materials in its slots. Zero-user datablocks, which a .blend save drops
    (the Studio's recorded files lack them; the worker keeps them with fake users), are outside it by construction."""
    other = state.get("other", {})
    view = {}
    for name, info in state["summary"].items():
        if not info.get("in_scene"):
            continue
        data = info.get("data")
        dd = (state["meshes"].get(data) or state["curves"].get(data) or other.get("lights", {}).get(data)
              or other.get("cameras", {}).get(data)) if data else None
        mats = tuple(state["materials"].get(m) if m else None for m in info.get("materials", []))
        view[name] = (state["objects"][name], dd, mats)
    return view


def diff_views(live: dict, recorded: dict) -> list[str]:
    return sorted(k for k in set(live) | set(recorded) if live.get(k) != recorded.get(k))


def studio_refused(row: dict) -> bool:
    """The Studio's own safe mode refused this snippet (it never ran there)."""
    try:
        text = (Path(row["source_dir"]) / "result.json").read_text(encoding="utf-8")
    except OSError:
        return False
    return "Rejected by safe mode" in text


def replay_chain(product: str, work: Path, rows: list[dict] | None = None, log=print) -> dict:
    """Rebuild one accepted Studio run from its first before.blend in ONE worker: each step through the worker's
    validated `exec` (safe mode + every runtime layer), as sent when it passes, else with only its file-I/O
    statements removed and font loads replaced by the trusted `load_font`; a step the Studio's safe mode refused is
    sent as is. After every step the product (product_view) is compared with the step's recorded after.blend, read
    in a second worker. The journal's rollback-on-error is not applied here: the Studio kept a failed snippet's
    partial effects (Prada step 3 added both lenses before raising), and the next steps were written on that scene."""
    import shutil
    from ..g3d import safe_mode
    from .worker_client import WorkerClient
    rows = rows if rows is not None else load()
    steps = accepted_run_steps(rows, product)
    work = Path(work)
    (work / "reader").mkdir(parents=True, exist_ok=True)
    start = work / "start.blend"
    shutil.copy2(steps[0]["before_blend"], start)
    live_w = WorkerClient(work, start_blend=start).start()
    reader = WorkerClient(work / "reader").start()
    results = []
    try:
        for k, st in enumerate(steps):
            code, mode = st["code"], "as_sent"
            if studio_refused(st):
                mode = "as_sent_refused_in_studio"
            else:
                fams = font_loads(code)
                if fams:
                    names = {}
                    for fam in sorted(set(fams)):
                        r = live_w.call("load_font", {"family": fam}, deadline_s=60)
                        names[fam] = r["result"]["name"]
                    code = replace_font_loads(code, names)
                    mode = "load_font"
                if safe_mode.validate(code):
                    code = strip_io(code)
                    mode = "io_stripped" if mode == "as_sent" else mode + "+io_stripped"
            t0 = time.perf_counter()
            res = live_w.call("exec", {"code": code, "soft_timeout_s": 180}, deadline_s=240)
            live = live_w.call("fingerprint", {"state": True}, deadline_s=60)["state"]
            rec_path = work / "reader" / f"after_{k:02d}.blend"
            shutil.copy2(st["after_blend"], rec_path)
            reader.call("checkpoint_restore", {"path": str(rec_path)}, deadline_s=120)
            recorded = reader.call("fingerprint", {"state": True}, deadline_s=60)["state"]
            diff = diff_views(product_view(live), product_view(recorded))
            results.append({"step": k, "edit": st["edit"], "tool": st["context_parent"], "mode": mode,
                            "studio_status": st["status"], "status": res.get("status"),
                            "equal": not diff, "diff": diff[:8], "objects": len(product_view(recorded)),
                            "seconds": round(time.perf_counter() - t0, 2),
                            "error": (res.get("traceback") or "")[-300:] or None,
                            "violations": res.get("violations") or res.get("runtime_violations") or None})
            log(f"{product} step {k:02d} {mode:28s} studio={st['status']:10s} ours={res.get('status'):16s} "
                f"equal={not diff} objects={len(product_view(recorded))} {diff[:3] if diff else ''}")
    finally:
        live_w.close()
        reader.close()
    equal = sum(r["equal"] for r in results)
    return {"product": product, "steps": len(steps), "expected_steps": ACCEPTED_RUNS[product][2],
            "equal_steps": equal, "final_equal": bool(results and results[-1]["equal"]), "results": results}


def main(argv=None) -> int:
    configure_utf8_stdio()
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["build"]:
        m = build()
        print(json.dumps({k: v for k, v in m.items() if k != "rows"}))
        return 0
    if args[:1] == ["gate"]:
        sys.path.insert(0, str(G3D.parent))
        from glasses3d.g3d import safe_mode
        print(json.dumps(gate(load(), safe_mode.validate), indent=1, ensure_ascii=False))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
