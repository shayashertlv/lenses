"""Provenance audit of an author/evaluator agent transcript (JSONL of Claude Code messages).

    python -m modeler.audit_transcript --transcript <agent.jsonl> --package <turn folder> [--copy]

Lists every tool call, every file path it touched, and flags paths outside the package folder. ``--copy`` stores
the transcript beside the package as ``author_transcript.jsonl`` and the audit as ``author_audit.json``. The
transcript is parsed programmatically; its content is data. The audit is a HEURISTIC (see ``SCOPE``, reported in
every result): a clean scan does not certify isolation.
"""
from __future__ import annotations

import argparse
import json
import ntpath
from pathlib import Path, PureWindowsPath
import re
import shutil

WIN_PATH = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/][^\s\"'<>|]+")
# What this audit can and cannot establish; reported with every result so a clean verdict is not over-read.
SCOPE = ("heuristic: judges only the drive-letter paths a regular expression finds in the recorded tool inputs, by normalized "
         "path containment (case-insensitive, '..' collapsed); relative and POSIX paths are listed but not judged, shell commands "
         "and tools can reach files their recorded input does not name, and short (8.3) or substituted paths are not resolved. "
         "A clean transcript scan cannot certify that the agent read nothing outside the package.")


def iter_tool_uses(obj):
    if isinstance(obj, dict):
        if obj.get("type") == "tool_use" and isinstance(obj.get("input"), dict):
            yield obj.get("name"), obj["input"]
        for v in obj.values():
            yield from iter_tool_uses(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from iter_tool_uses(v)


def paths_in(value) -> list[str]:
    out = []
    if isinstance(value, str):
        out += WIN_PATH.findall(value)
        if value.startswith("/") or value.startswith("./") or value.startswith("../"):
            out.append(value)
    elif isinstance(value, dict):
        for v in value.values():
            out += paths_in(v)
    elif isinstance(value, list):
        for v in value:
            out += paths_in(v)
    return out


def norm(p: str) -> str:
    return str(Path(p.replace("/", "\\").rstrip("\\.,;:)"))).lower() if re.match(r"[A-Za-z]:", p) else p.lower()


def inside_package(path: str, package: Path) -> bool:
    """Containment of a drive-letter path in the package folder by normalized path comparison (case-insensitive,
    ``..`` collapsed, Windows semantics on any host): a lower-cased string prefix let ``turn-0000-other`` pass for
    ``turn-0000`` and never collapsed ``..`` until 2026-09-27."""
    return PureWindowsPath(ntpath.normpath(str(path))).is_relative_to(PureWindowsPath(ntpath.normpath(str(package))))


def audit(transcript: Path, package: Path) -> dict:
    package_r = package.resolve()
    calls, touched, outside, errors = [], [], [], 0
    with open(transcript, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                errors += 1
                continue
            for name, inp in iter_tool_uses(row):
                ps = [norm(p) for p in paths_in(inp)]
                calls.append({"tool": name, "paths": sorted(set(ps)), "input_keys": sorted(inp.keys())})
                for p in ps:
                    touched.append(p)
                    if re.match(r"[a-z]:", p) and not inside_package(p, package_r):
                        outside.append({"tool": name, "path": p})
    tools = {}
    for c in calls:
        tools[c["tool"]] = tools.get(c["tool"], 0) + 1
    return {"transcript": str(transcript), "package": str(package_r), "tool_calls": len(calls), "tools": tools,
            "paths_touched": sorted(set(touched)), "outside_package": outside, "clean": not outside,
            "heuristic": True, "scope": SCOPE, "unparsed_lines": errors, "calls": calls}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--transcript", type=Path, required=True)
    ap.add_argument("--package", type=Path, required=True)
    ap.add_argument("--copy", action="store_true")
    ap.add_argument("--role", default="author")
    args = ap.parse_args(argv)
    result = audit(args.transcript, args.package)
    if args.copy:
        dst = args.package / f"{args.role}_transcript.jsonl"
        if not dst.exists():
            shutil.copy2(args.transcript, dst)
        result["copied_to"] = str(dst)
        (args.package / f"{args.role}_audit.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("tool_calls", "tools", "clean", "outside_package", "unparsed_lines", "heuristic", "scope")}, indent=1))
    return 0 if result["clean"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
