"""Read authenticated historical temple builders without replacing live modules.

Only a job's own run archives and explicitly carried edit inputs are searched.
An archive is never extracted. The four numpy-only construction modules execute
under a private package name; no Blender or repository package is imported by them.
The caller must still match the resulting connectivity against the actual mesh.
"""
from __future__ import annotations

import builtins
from datetime import datetime
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import sys
import tarfile
from types import ModuleType


LINEAGE_SCHEMA = "g3d.temple_archive_lineage/1"
MODULES = ("spec", "arrange", "planes", "build")
SOURCE_PREFIX = "glasses3d/g3d/temple/"
EVIDENCE_FILES = ("run-config.json", "implementation.json", "src.tar.gz")
_BUILDERS = {}


def _sha(payload):
    return hashlib.sha256(payload).hexdigest()


def _digest(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _inside(root, relative):
    """Reject filesystem aliases and Windows as well as POSIX traversal."""
    if not isinstance(relative, str) or not relative or "\\" in relative or ":" in relative:
        raise ValueError("invalid archive evidence path")
    parts = relative.split("/")
    if any(part in ("", ".", "..") or part.lower() == ".env" or part.lower().startswith(".env.")
           for part in parts):
        raise ValueError("invalid archive evidence path")
    path = root.joinpath(*parts)
    if path.is_symlink() or path.resolve() != path.absolute() or not path.resolve().is_relative_to(root):
        raise ValueError("archive evidence must stay inside its job without filesystem links")
    return path


def _read(root, relative):
    path = _inside(root, relative)
    if not path.is_file():
        raise ValueError("archive evidence is missing: " + relative)
    return path.read_bytes()


def _json(payload):
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise ValueError("archive receipt must be an object")
    return value


def _time(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("run timestamp must have a timezone")
    return parsed


def _sources(archive, receipt):
    """Validate every header, but only read explicitly named source payloads."""
    required = {SOURCE_PREFIX + name + ".py" for name in MODULES}
    if not isinstance(receipt.get("files"), dict):
        raise ValueError("source receipt file hashes must be an object")
    found, seen = {}, set()
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        for member in tar:
            name = member.name
            parts = name.split("/")
            if (not name or "\\" in name or ":" in name or PurePosixPath(name).is_absolute()
                    or any(part in ("", ".", "..") for part in parts)
                    or any(part.lower() == ".env" or part.lower().startswith(".env.") for part in parts)):
                raise ValueError("unsafe source archive member")
            folded = name.casefold()
            if folded in seen:
                raise ValueError("duplicate source archive member")
            seen.add(folded)
            if not member.isfile() or member.issparse():
                raise ValueError("source archive members must be regular files")
            if name in required:
                if member.size > 4 * 1024 * 1024:
                    raise ValueError("temple source exceeds the supported source size")
                payload = tar.extractfile(member).read()
                expected = receipt.get("files", {}).get(name)
                if not _digest(expected) or _sha(payload) != expected:
                    raise ValueError("temple source SHA-256 mismatch: " + name)
                found[name] = payload
    if found.keys() != required:
        raise ValueError("source archive lacks the historical temple construction modules")
    return found


def _verified(root, directory, *, bindings=None, origin=None):
    payloads = {name: _read(root, directory + "/" + name) for name in EVIDENCE_FILES}
    if bindings is not None:
        if not isinstance(bindings, dict):
            raise ValueError("copied temple source hashes must be an object")
        for name, payload in payloads.items():
            if bindings.get(name) != _sha(payload):
                raise ValueError("copied temple lineage SHA-256 mismatch: " + name)
    config = _json(payloads["run-config.json"])
    receipt = _json(payloads["implementation.json"])
    bound = config.get("implementation", {})
    if not isinstance(bound, dict):
        raise ValueError("run implementation binding must be an object")
    implementation_sha = _sha(payloads["implementation.json"])
    archive_sha = _sha(payloads["src.tar.gz"])
    if (receipt.get("schema") != "g3d.implementation/1" or receipt.get("archive") != "src.tar.gz"
            or bound.get("path") != "implementation.json"
            or bound.get("sha256") != implementation_sha
            or bound.get("archive_sha256") != archive_sha or receipt.get("archive_sha256") != archive_sha):
        raise ValueError("run implementation/archive SHA-256 binding mismatch")
    sources = _sources(payloads["src.tar.gz"], receipt)
    source_hashes = {name: _sha(payload) for name, payload in sources.items()}
    source_set_sha = _sha(json.dumps(source_hashes, sort_keys=True, separators=(",", ":")).encode())
    provenance = {"kind": "archived_run_source", "run_id": config.get("run_id"),
                  "job_id": config.get("job_id"), "created": config.get("created"),
                  "git_head": receipt.get("git_head"), "implementation_sha256": implementation_sha,
                  "archive_sha256": archive_sha, "source_sha256": source_hashes,
                  "source_set_sha256": source_set_sha, "evidence_directory": directory}
    if origin is not None:
        provenance["origin"] = origin
    return {"payloads": payloads, "sources": sources, "provenance": provenance}


def verified_archives(root, *, errors=None, through_run=None, include_runs=True):
    """Yield verified bytes; missing/corrupt evidence never becomes executable code.

    ``through_run`` scopes a new edit to that delivery's run and earlier runs.
    An edited delivery with no run archive can use only its inherited lineage.
    Without a selected run, readers inspect this job's own saved runs only.
    """
    root = Path(root).resolve()
    errors = [] if errors is None else errors
    candidates = []
    lineage = "inputs/temple-lineage/index.json"
    try:
        index_path = _inside(root, lineage)
        if index_path.exists():
            index_payload = _read(root, lineage)
            index = _json(index_payload)
            if index.get("schema") != LINEAGE_SCHEMA or not isinstance(index.get("archives"), list):
                raise ValueError("unsupported copied temple lineage")
            owner_path = _inside(root, "inputs/owner-edit.json")
            if owner_path.exists():
                immutable = _json(_read(root, "inputs/owner-edit.json")).get("immutable_files", {})
                if not isinstance(immutable, dict):
                    raise ValueError("immutable edit input hashes must be an object")
                if immutable.get(lineage) != _sha(index_payload):
                    raise ValueError("copied temple lineage index differs from immutable edit inputs")
            else:
                immutable = None
            for row in index["archives"]:
                if (not isinstance(row, dict) or not _digest(row.get("directory"))
                        or not isinstance(row.get("files"), dict)):
                    raise ValueError("invalid copied temple archive record")
                directory = "inputs/temple-lineage/" + row["directory"]
                _inside(root, directory)
                if immutable is not None and any(immutable.get(directory + "/" + name) != row["files"].get(name)
                                                 for name in EVIDENCE_FILES):
                    raise ValueError("copied temple source is not bound by immutable edit inputs")
                candidates.append((directory, row["files"], row.get("origin")))
            unavailable = index.get("unavailable", [])
            if not isinstance(unavailable, list) or any(not isinstance(value, str) for value in unavailable):
                raise ValueError("invalid copied temple archive refusal records")
            errors.extend(unavailable)
    except (OSError, ValueError, TypeError, KeyError) as error:
        errors.append("Copied temple lineage unavailable: " + str(error))
        # Never use a partially authenticated copied list.
        candidates = []

    cutoff = None
    use_runs = include_runs and through_run is None
    if include_runs and through_run is not None:
        try:
            selected = _json(_read(root, "runs/" + through_run + "/run-config.json"))
            if selected.get("run_id") != through_run:
                raise ValueError("selected run identity differs")
            cutoff = _time(selected["created"])
            use_runs = True
        except (OSError, ValueError, TypeError, KeyError) as error:
            # owner-apply deliveries have no provider run; inherited lineage is sufficient.
            if (root / "runs" / str(through_run)).exists():
                errors.append("Selected run temple source unavailable: " + str(error))
    if use_runs:
        try:
            runs = _inside(root, "runs")
        except ValueError as error:
            errors.append("Job run archives unavailable: " + str(error))
            runs = None
        if runs is not None and runs.exists():
            for path in sorted(runs.glob("*/implementation.json")):
                directory = path.parent.relative_to(root).as_posix()
                try:
                    config = _json(_read(root, directory + "/run-config.json"))
                    if config.get("run_id") != path.parent.name:
                        raise ValueError("run identity differs from its directory")
                    if cutoff is not None and _time(config["created"]) > cutoff:
                        continue
                    candidates.append((directory, None, None))
                except (OSError, ValueError, TypeError, KeyError) as error:
                    errors.append(directory + ": " + str(error))
    seen = set()
    for directory, bindings, origin in candidates:
        try:
            evidence = _verified(root, directory, bindings=bindings, origin=origin)
            identity = evidence["provenance"]["implementation_sha256"]
            if identity not in seen:
                seen.add(identity)
                yield evidence
        except (OSError, ValueError, TypeError, KeyError, tarfile.TarError, EOFError) as error:
            errors.append(directory + ": " + str(error))


def _builder(evidence):
    identity = evidence["provenance"]["source_set_sha256"]
    if identity in _BUILDERS:
        return _BUILDERS[identity]
    package_name = "_g3d_temple_archive_" + identity
    package = ModuleType(package_name)
    package.__path__ = []
    package.__package__ = package_name
    sys.modules[package_name] = package
    allowed = {"__future__", "bisect", "copy", "dataclasses", "hashlib", "heapq", "json", "math", "numpy"}

    def archived_import(name, globals=None, locals=None, fromlist=(), level=0):
        if level:
            if level != 1 or name not in (*MODULES, ""):
                raise ImportError("historical temple source import outside construction package")
            if not name and any(value not in MODULES for value in fromlist):
                raise ImportError("historical temple source import outside construction package")
        elif name not in allowed and not name.startswith("numpy."):
            raise ImportError("historical temple source import is not numpy-only: " + name)
        return builtins.__import__(name, globals, locals, fromlist, level)

    safe_builtins = {name: value for name, value in vars(builtins).items()
                     if name not in {"open", "exec", "eval", "compile", "input", "breakpoint"}}
    safe_builtins["__import__"] = archived_import
    try:
        for name in MODULES:
            module_name = package_name + "." + name
            module = ModuleType(module_name)
            module.__package__ = package_name
            module.__file__ = "<authenticated-temple-archive>/" + name + ".py"
            module.__dict__["__builtins__"] = safe_builtins
            sys.modules[module_name] = module
            setattr(package, name, module)
            payload = evidence["sources"][SOURCE_PREFIX + name + ".py"]
            exec(compile(payload, module.__file__, "exec"), module.__dict__)
        build = sys.modules[package_name + ".build"].build_temple
    except Exception:
        for name in (package_name, *(package_name + "." + item for item in MODULES)):
            sys.modules.pop(name, None)
        raise
    _BUILDERS[identity] = build
    return build


def archived_builders(root, *, errors=None):
    """Yield isolated historical builders and provenance; callers verify topology."""
    errors = [] if errors is None else errors
    seen = set()
    for evidence in verified_archives(root, errors=errors):
        identity = evidence["provenance"]["source_set_sha256"]
        if identity in seen:
            continue
        seen.add(identity)
        try:
            yield _builder(evidence), evidence["provenance"]
        except Exception as error:
            errors.append("Historical temple builder unavailable: " + type(error).__name__ + ": " + str(error))
