"""python -m modeler.agentic rebuild: a no-inference rebuild of one revision's sealed program with the CURRENT library.

A library, harness or exporter fix (test-pilot-002's r0006: faceted temples from glasses_lib.tube_along_path's arc-length
resample) reaches a delivered program only through a rebuild of that exact program; before 2026-09-28 the only way was a
paid author revision. This module:

* reads the source job without writing to it: the database and its WAL are COPIED into a scratch folder and opened
  there (a read-only SQLite open still writes the source's -shm file), everything else is only read;
* takes the revision's exact sealed program set from its build operation's bundle (worker/<op>/bundle) and verifies
  every module's sha256 against the revision row, the bundle's operation.json program_set_sha256 and the revision's
  working copy; any mismatch is refused;
* builds it through the same path a normal build uses (``tools.build_revision``: worker bundle, executor, ingestion,
  export, contract, observation with the AR harness) inside a SHADOW job (``<output>/work``: its own job.sqlite3,
  worker bundles and revision folder), then renders the wearer poses (``evaluation.wearer_render_paths``);
* writes the preview folder: ``model.glb``, ``manifest.json`` (kind ``preview_rebuild``) and ``sheets/``.

No inference request, no reservation, no budget: the shadow job's caps are zero. The preview is never a delivery; the
owner judges it live (modeler.tryon lists it with that label) and a delivery still needs an author revision or an
explicit owner decision.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import tempfile
import traceback

from ..paths import BLENDER_DIR
from . import evaluation
from . import tools as T
from .artifacts import AUTHOR_VISIBLE, HOST_ONLY, ArtifactStore, atomic_write, contained, regular_file_or_raise
from .executor import Worker, fingerprint_diff, measurement_fingerprint, program_set_sha256, worker_config_fingerprint
from .state import DB_NAME, LEASE_TTL_S, TERMINAL_STATES, StateError, Store, runner_identity, sha256_bytes

PREVIEW_KIND = "preview_rebuild"
SHADOW_DIR = "work"
MODULE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


class RebuildError(ValueError):
    """The rebuild was refused before anything was written (the output folder stays as it was)."""


def preview_label(job: str, rid: str) -> str:
    return f"rebuild of {job} {rid} with the current library, not a delivery"


def read_source(job_dir: Path, rid: str) -> dict:
    """The job row, the revision row, its build operation row and the evidence, read from a COPY of the database: the
    source folder is never opened for writing (a read-only SQLite connection still updates the source's -shm file)."""
    db = job_dir / DB_NAME
    if not db.is_file():
        raise RebuildError(f"no job database at {db}")
    scratch = Path(tempfile.mkdtemp(prefix="lenses-rebuild-"))
    try:
        for suffix in ("", "-wal"):             # without the -shm the copy rebuilds its WAL index from the copied log
            src = Path(str(db) + suffix)
            if src.is_file():
                shutil.copyfile(src, scratch / (DB_NAME + suffix))
        try:
            store = Store.open(scratch)
        except StateError as e:
            raise RebuildError(f"the source database cannot be read: {e}") from None
        try:
            job = store.job()
            rev = store.revision(rid)
            if rev is None:
                raise RebuildError(f"unknown revision {rid!r}; the job has {[r['id'] for r in store.revisions()]}")
            op = store.operation(rev["operation_id"]) if rev.get("operation_id") else None
            evidence = store.setting("evidence") or {}
        finally:
            store.close()
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return {"job": job, "revision": rev, "operation": op, "evidence": evidence}


def sealed_program(job_dir: Path, rev: dict) -> dict:
    """The revision's exact program set from its build operation's bundle, every module verified against the revision
    row (sha256 per module and program_set_sha256), the bundle's operation.json and the revision's working copy."""
    rid = rev["id"]
    mods = rev.get("modules") or {}
    op_id = rev.get("operation_id")
    if not mods or not rev.get("program_set_sha256") or not op_id:
        raise RebuildError(f"revision {rid} has no sealed program: it was never built (no build operation bundle binds its modules)")
    bundle = job_dir / "worker" / op_id / "bundle"
    opj = bundle / "operation.json"
    try:
        regular_file_or_raise(opj)
        operation = json.loads(opj.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001 - missing, a link, unreadable
        raise RebuildError(f"revision {rid} has no sealed program: {opj} is not a readable operation file ({type(e).__name__}: {e})") from None
    if not isinstance(operation, dict) or operation.get("mode") != "build" or not isinstance(operation.get("modules"), list):
        raise RebuildError(f"{opj} is not a build operation")
    if operation.get("program_set_sha256") != rev["program_set_sha256"]:
        raise RebuildError(f"the bundle of {op_id} is bound to program_set_sha256 {str(operation.get('program_set_sha256'))[:16]}, not the revision's "
                           f"{rev['program_set_sha256'][:16]}; refusing")
    names = [m.get("name") for m in operation["modules"] if isinstance(m, dict)]
    if len(names) != len(operation["modules"]) or sorted(names) != sorted(mods) or len(set(names)) != len(names):
        raise RebuildError(f"the bundle of {op_id} lists modules {names}, the revision {sorted(mods)}; refusing")
    modules, problems = {}, []
    for name in names:
        if not isinstance(name, str) or not MODULE_NAME_RE.fullmatch(name):
            raise RebuildError(f"module name {str(name)[:40]!r} is not safe")
        expected = (mods[name] or {}).get("sha256")
        p = bundle / "program" / f"{name}.py"
        try:
            regular_file_or_raise(p)
            if not contained(p, bundle):
                raise ValueError("escapes the bundle")
            data = p.read_bytes()
        except Exception as e:  # noqa: BLE001
            problems.append(f"{name}: the sealed bundle file is missing or not a regular file ({type(e).__name__}: {e})")
            continue
        if sha256_bytes(data) != expected:
            problems.append(f"{name}: the sealed bundle module's sha256 {sha256_bytes(data)[:16]} is not the revision's {str(expected)[:16]} (tampered or corrupted)")
            continue
        copy = job_dir / "revisions" / rid / "program" / f"{name}.py"
        if copy.exists() and (not copy.is_file() or sha256_bytes(copy.read_bytes()) != expected):
            problems.append(f"{name}: the revision's working copy {copy.relative_to(job_dir).as_posix()} differs from the sealed module (tampered or corrupted)")
            continue
        modules[name] = data.decode("utf-8")
    if problems:
        raise RebuildError("the sealed program does not verify; refusing: " + "; ".join(problems))
    if program_set_sha256(modules) != rev["program_set_sha256"]:
        raise RebuildError(f"the verified modules hash to {program_set_sha256(modules)[:16]}, not the revision's program_set_sha256 {rev['program_set_sha256'][:16]}")
    return {"modules": modules, "operation_id": op_id, "bundle_lib_sha256": operation.get("lib_sha256") or {}, "module_order": names,
            "measurement_fingerprint": operation.get("measurement_fingerprint") if isinstance(operation.get("measurement_fingerprint"), dict) else None}


def library_fingerprint(bundle_lib: dict) -> dict:
    """The library and harness this rebuild bundles against the ones the source build carried."""
    now = {name: sha256_bytes((BLENDER_DIR / name).read_bytes()) for name in ("glasses_lib.py", "harness.py")}
    return {"glasses_lib_sha256": now["glasses_lib.py"], "harness_sha256": now["harness.py"], "source_bundle_lib_sha256": bundle_lib,
            "changed": {name: bundle_lib.get(name) != sha for name, sha in now.items()}}


def source_changes(source_fp: dict, current_fp: dict, source_measurement: dict | None, current_measurement: dict, library: dict) -> dict:
    """Every component that differs between the source build and this rebuild (review 2026-09-28, INF-13: test-pilot-002's
    preview named only glasses_lib.py although the observer, exporter, lens_colour, see_through and the AR runtime had
    changed too). ``host_python``: per file against the source job's fingerprints.python_sources (None when the source did not
    record them); ``ar_runtime``: the aggregate ar/src digest and file count; ``measurement``: per file against the source
    build bundle's measurement fingerprint (None for a bundle written before 2026-09-28); ``library``: the bundle lib files."""
    source_fp, current_fp = dict(source_fp or {}), dict(current_fp or {})
    src_py, cur_py = source_fp.get("python_sources"), current_fp.get("python_sources")
    host = fingerprint_diff(src_py, cur_py) if isinstance(src_py, dict) and isinstance(cur_py, dict) else None
    src_ar, cur_ar = source_fp.get("ar_runtime_sources_sha256"), current_fp.get("ar_runtime_sources_sha256")
    ar = {"changed": (src_ar != cur_ar) if src_ar and cur_ar else None, "source_sha256": src_ar, "current_sha256": cur_ar,
          "source_files": source_fp.get("ar_runtime_files"), "current_files": current_fp.get("ar_runtime_files")}
    if isinstance(source_measurement, dict) and isinstance(source_measurement.get("python"), dict) and isinstance(source_measurement.get("ar"), dict):
        measurement = {"source_sha256": source_measurement.get("sha256"), "current_sha256": current_measurement.get("sha256"),
                       "python": fingerprint_diff(source_measurement["python"], current_measurement.get("python")),
                       "ar": fingerprint_diff(source_measurement["ar"], current_measurement.get("ar"))}
    else:
        measurement = {"source_sha256": None, "current_sha256": current_measurement.get("sha256"), "python": None, "ar": None,
                       "note": "not recorded by the source build (its bundle predates 2026-09-28): host_python is the only per-file record"}
    lib = sorted(n for n, c in (library.get("changed") or {}).items() if c)
    moved = lambda d: bool(d) and any(d[k] for k in ("changed", "added", "removed"))      # noqa: E731
    components = [name for name, hit in (("library", bool(lib)), ("host_python", moved(host)), ("ar_runtime", bool(ar["changed"])),
                                         ("measurement", moved(measurement["python"]) or moved(measurement["ar"]))) if hit]
    return {"components": components, "library": lib, "host_python": host, "ar_runtime": ar, "measurement": measurement}


def changes_notes(changes: dict) -> list[str]:
    """The manifest's prose for ``source_changes``: one line naming every changed file, then what could not be compared."""
    parts = []
    if changes["library"]:
        parts.append("library " + ", ".join(changes["library"]))

    def files(diff: dict, prefix: str = "") -> str:
        out = [prefix + f for f in diff["changed"]]
        out += [f"{prefix}{f} (added)" for f in diff["added"]] + [f"{prefix}{f} (removed)" for f in diff["removed"]]
        return ", ".join(out)
    host = changes["host_python"]
    if host and files(host):
        parts.append("host code " + files(host))
    ar = changes["ar_runtime"]
    if ar["changed"]:
        parts.append(f"AR runtime {str(ar['source_sha256'])[:8]} -> {str(ar['current_sha256'])[:8]} ({ar['source_files']} -> {ar['current_files']} files)")
    m = changes["measurement"]
    if m["python"] is not None:
        listed = ", ".join(x for x in (files(m["python"]), files(m["ar"], "ar/")) if x)
        if listed:
            parts.append("measurement code " + listed)
    notes = [("changed since the source build: " + "; ".join(parts) + " (changes)") if parts
             else "changed since the source build: nothing that was recorded (changes)"]
    if host is None:
        notes.append("the source job recorded no per-file host hashes (fingerprints.python_sources): host code changes cannot be listed")
    if ar["changed"] is None:
        notes.append("the source job recorded no AR runtime digest: an AR runtime change cannot be ruled out")
    if m["python"] is None:
        notes.append("the measurement fingerprint was not recorded by the source build (a bundle written before 2026-09-28)")
    return notes


def rebuild_revision(job_dir: Path, rid: str, output: Path, *, worker: Worker, worker_config: dict | None = None, fingerprints: dict | None = None,
                     max_worker_seconds: int | None = None, log=print) -> dict:
    """Rebuild ``rid`` of the job at ``job_dir`` into the fresh folder ``output``; returns the manifest (also written as
    ``output/manifest.json``). Raises RebuildError before writing anything when the output is not fresh, lies inside
    the source job, or the revision has no verifiable sealed program."""
    job_dir, output = Path(job_dir).resolve(), Path(output).resolve()
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise RebuildError(f"--output {output} is not empty; choose a fresh folder (a rebuild never overwrites)")
    if output.is_relative_to(job_dir):
        raise RebuildError(f"--output {output} lies inside the source job {job_dir}; a rebuild writes nothing there")
    src = read_source(job_dir, rid)
    job, rev = src["job"], src["revision"]
    sealed = sealed_program(job_dir, rev)
    if fingerprints is None:
        from .cli import source_fingerprints
        fingerprints = source_fingerprints()
    policy = dict(job["policy"], worker=worker.kind)
    if max_worker_seconds is not None:
        policy["max_worker_seconds"] = int(max_worker_seconds)
    # ---- the shadow job: the normal build path needs a job database, a revision and an operation; none is the source's
    work = output / SHADOW_DIR
    request = dict(job["request"], rebuild_of={"job": job_dir.name, "revision": rid, "program_set_sha256": rev["program_set_sha256"], "output": output.name})
    store = Store.create(work, {"request": request, "policy": policy, "fingerprints": fingerprints, "model": job["model"], "reasoning_effort": job["reasoning_effort"],
                                "service_tier": job["service_tier"], "endpoint": job["endpoint"], "tariff": job["tariff"], "cap_micro": 0, "inference_operation_cap": 0,
                                "driver": "rebuild", "worker": worker.kind, "worker_config": worker_config})
    try:
        return _run(store, job_dir, job, rev, src, sealed, policy, worker, worker_config, fingerprints, output, log)
    finally:
        store.close()


def _run(store: Store, job_dir: Path, job: dict, rev: dict, src: dict, sealed: dict, policy: dict, worker: Worker, worker_config: dict | None,
         fingerprints: dict, output: Path, log) -> dict:
    rid = rev["id"]
    evidence = src["evidence"]
    store.set_setting("evidence", evidence)
    store.set_setting("rebuild_of", {"job_dir": str(job_dir), "revision": rid, "operation_id": sealed["operation_id"]})
    store.set_setting("worker_description", worker.describe())
    if (job_dir / "evidence").is_dir():
        shutil.copytree(job_dir / "evidence", store.job_dir / "evidence")     # the observer reads masks.npz there; a copy, never the source
    artifacts = ArtifactStore(store)
    modules = sealed["modules"]
    shadow = T.insert_program_revision(store, modules, module_source=f"rebuild:{job_dir.name}/{rid}", set_sha256=rev["program_set_sha256"],
                                       rationale=f"no-inference rebuild of {job_dir.name} {rid} with the current library", synthetic=worker.synthetic)
    sid = shadow["id"]
    holder = f"rebuild-{runner_identity()}"
    fence = store.acquire_lease(holder, LEASE_TTL_S)
    op = store.insert_operation(call_id="rebuild", request_id=None, tool_name="build_candidate", schema_version=T.TOOLS_VERSION,
                                args={"revision_id": sid, "deliver_if_compatible": False}, source_revision_id=sid, fence=fence)
    ctx = T.ToolContext(store=store, artifacts=artifacts, worker=worker, evidence=evidence, policy=policy, log=log, holder=holder, fence=fence,
                        clock=store.clock, critic=None, ar=bool(policy.get("ar", True)), heartbeat=lambda: store.renew_lease(holder, fence, LEASE_TTL_S))
    log(f"[rebuild] {job_dir.name} {rid} (program {rev['program_set_sha256'][:12]}, bundle {sealed['operation_id']}) -> {output} with worker {worker.kind}")
    try:
        result = T.build_revision(ctx, sid, op, deliver_if_compatible=False)
        text = result.text
    except Exception as e:  # noqa: BLE001 - recorded in the manifest; the preview then carries no asset
        text = {"built": False, "category": "host", "error": f"{type(e).__name__}: {e}"}
        store.event("rebuild_exception", error=text["error"], traceback=traceback.format_exc()[-4000:])
    row = store.operation(op["id"])
    if row is not None and row["state"] in ("pending", "running"):
        store.update_operation(op["id"], state="completed" if text.get("built") else "failed", output_committed=1, completed_utc=store.now())
    built = store.revision(sid)
    rdir = store.job_dir / "revisions" / sid
    # ---- the preview: the asset (byte-bound to the shadow revision), the sheets, the wearer poses
    asset, sheets = None, {}
    sheet_dir = output / "sheets"
    sheet_dir.mkdir(parents=True, exist_ok=True)
    glb = rdir / "model.glb"
    if not built["synthetic"] and built.get("glb_sha256") and glb.is_file() and sha256_bytes(glb.read_bytes()) == built["glb_sha256"]:
        data = glb.read_bytes()
        atomic_write(output / "model.glb", data)
        asset = {"path": "model.glb", "sha256": built["glb_sha256"], "bytes": len(data)}
    for a in store.artifacts(revision_id=sid, kind="sheet"):
        key = str((a.get("recipe") or {}).get("view") or a["id"])
        p = artifacts.path_of(a)
        if a["role"] in (AUTHOR_VISIBLE, HOST_ONLY) and p.is_file() and re.fullmatch(r"[a-z0-9_-]{1,40}", key):
            shutil.copyfile(p, sheet_dir / f"sheet_{key}.png")
            sheets[key] = f"sheets/sheet_{key}.png"
    bbox = (built.get("observation") or {}).get("bbox_mm")
    width = float(bbox[1][0] - bbox[0][0]) if bbox else None
    wearer = {"count": 0, "error": "no preview asset: nothing to render", "status": None, "runtime_compatible": None}
    if asset is not None and ctx.ar:
        w = evaluation.wearer_render_paths(rdir, glb, width)
        renders = [str(x) for x in (w.get("renders") or []) if Path(str(x)).is_file()]
        wearer = {"count": len(renders), "error": w.get("error"), "status": w.get("status"), "runtime_compatible": w.get("runtime_compatible")}
        if renders:
            from .. import evaluate as mevaluate
            try:
                mirror, detail = mevaluate.wearer_sheets(renders, sheet_dir)
                for key, p in (("wearer_mirror", mirror), ("wearer_detail", detail)):
                    if p and Path(p).is_file():
                        sheets[key] = Path(p).relative_to(output).as_posix()
            except Exception as e:  # noqa: BLE001 - the renders stay in the shadow job; the manifest says why no sheet was made
                wearer["error"] = f"wearer sheets: {type(e).__name__}: {e}"
    export = T.load_json_or_none(rdir / "export.json") or {}
    receipt = export.get("receipt") if isinstance(export.get("receipt"), dict) else {}
    front = next((p for p in (job["request"] or {}).get("photos", []) if isinstance(p, dict) and p.get("view") == "front"), None)
    source_image = (job.get("worker_config") or {}).get("image_digest")
    notes = [f"{preview_label(job_dir.name, rid)}: the same sealed program built through the worker with the library, harness, exporter and "
             "AR runtime of this checkout; the owner judges it live. It is not a delivery: the source job, its manifest and its verdicts are untouched."]
    library = library_fingerprint(sealed["bundle_lib_sha256"])
    measurement = measurement_fingerprint()
    changes = source_changes(job.get("fingerprints") or {}, fingerprints, sealed["measurement_fingerprint"], measurement, library)
    notes.extend(changes_notes(changes))
    if job["state"] not in TERMINAL_STATES:
        notes.append(f"the source job was {job['state']!r} when it was read (not terminal)")
    if worker.synthetic:
        notes.append("SYNTHETIC worker: no Blender ran, no export; this proves the command, never a model")
    manifest = {"kind": PREVIEW_KIND, "schema_version": 1, "written_utc": store.now(), "label": preview_label(job_dir.name, rid), "delivery": False,
                "no_inference": True, "synthetic": bool(worker.synthetic),
                "source": {"job": job_dir.name, "job_dir": str(job_dir), "job_state": job["state"], "revision": rid, "revision_state": rev["state"],
                           "program_set_sha256": rev["program_set_sha256"], "operation_id": sealed["operation_id"],
                           "modules": {n: sha256_bytes(s.encode("utf-8")) for n, s in modules.items()}, "glb_sha256": rev.get("glb_sha256"),
                           "request_sha256": job["request_sha256"], "product_id": (job["request"] or {}).get("product_id"),
                           "front_photo": front.get("path") if front else None, "worker_image_digest": source_image},
                "library": library, "changes": changes, "measurement_fingerprint": measurement,
                "fingerprints": dict({k: fingerprints.get(k) for k in ("protocol", "python_sources_sha256", "ar_runtime_sources_sha256", "ar_runtime_files")},
                                     measurement_sha256=measurement["sha256"]),
                "worker": dict(worker.describe(), config_fingerprint=worker_config_fingerprint(worker_config) if worker_config else None,
                               same_image_as_source=(worker_config or {}).get("image_digest") == source_image if worker_config else None),
                "build": {k: text.get(k) for k in ("built", "category", "error", "failed_module", "repair_hints", "state") if k in text},
                "export": text.get("export"), "export_audit": receipt.get("audit"),
                "compatibility": built.get("compatibility"),
                "observation": {k: (built.get("observation") or {}).get(k) for k in ("summary", "bbox_mm", "triangles", "seconds")},
                "width_mm": round(width, 2) if width else None, "wearer": wearer,
                "asset": asset, "sheets": sheets, "shadow_job": SHADOW_DIR, "shadow_revision": sid, "notes": notes}
    atomic_write(output / "manifest.json", json.dumps(manifest, indent=1, default=str).encode("utf-8"))
    store.event("rebuild_written", asset=asset, sheets=sorted(sheets), built=bool(text.get("built")))
    store.release_lease(holder, fence)
    return json.loads(json.dumps(manifest, default=str))
