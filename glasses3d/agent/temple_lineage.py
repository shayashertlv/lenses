"""Carry authenticated historical temple source into self-contained edit inputs."""
from __future__ import annotations

import hashlib
from pathlib import Path

from ..g3d.temple_archive import LINEAGE_SCHEMA, EVIDENCE_FILES, verified_archives
from .procs import write_json_atomic


def carry_evidence(source, target, *, run_id=None, source_runtime=None):
    """Copy verified source bytes only; bind the returned paths as immutable inputs.

    Invalid or absent old archives do not prevent unrelated edits. Their explicit
    refusal reasons travel with the copy, so a later map cannot silently guess names.
    """
    source, target = Path(source).resolve(), Path(target).resolve()
    errors, rows, hashes = [], [], {}
    evidence = list(verified_archives(source, errors=errors, through_run=run_id))
    if source_runtime is not None and Path(source_runtime).resolve() != source:
        evidence.extend(verified_archives(source_runtime, errors=errors, through_run=run_id))
    # source_runtime may hold checkpoint/camera bytes separately from the source
    # delivery's inputs. _copy_inputs already carried the latter into target.
    evidence.extend(verified_archives(target, errors=errors, include_runs=False))
    if not evidence and not errors and not (target / "inputs/temple-lineage").exists():
        return hashes
    seen = set()
    for item in evidence:
        provenance = item["provenance"]
        directory = provenance["implementation_sha256"]
        if directory in seen:
            continue
        seen.add(directory)
        base = target / "inputs/temple-lineage" / directory
        base.mkdir(parents=True, exist_ok=True)
        bound = {}
        for name in EVIDENCE_FILES:
            payload = item["payloads"][name]
            path = base / name
            if path.exists() and path.read_bytes() != payload:
                raise ValueError("carried temple source conflicts with existing copied input")
            if not path.exists():
                path.write_bytes(payload)
            digest = hashlib.sha256(payload).hexdigest()
            bound[name] = digest
            hashes[path.relative_to(target).as_posix()] = digest
        origin = provenance.get("origin") or {"job_id": provenance.get("job_id"),
            "run_id": provenance.get("run_id"), "created": provenance.get("created"),
            "implementation_sha256": provenance["implementation_sha256"]}
        rows.append({"directory": directory, "files": bound, "origin": origin})
    index = target / "inputs/temple-lineage/index.json"
    write_json_atomic(index, {"schema": LINEAGE_SCHEMA, "archives": rows,
                              "unavailable": list(dict.fromkeys(errors))})
    hashes[index.relative_to(target).as_posix()] = hashlib.sha256(index.read_bytes()).hexdigest()
    # Previously copied rows may include unavailable evidence. Bind every carried
    # byte too, even when it is no longer eligible to execute.
    for path in sorted(index.parent.rglob("*")):
        if path.is_file():
            if path.name.lower() == ".env" or path.name.lower().startswith(".env."):
                raise ValueError("carried temple source cannot contain environment files")
            if path.is_symlink() or path.resolve() != path.absolute():
                raise ValueError("carried temple source cannot contain filesystem links")
            hashes[path.relative_to(target).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes
