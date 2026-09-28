"""The worker protocol: how a construction program reaches headless Blender and how its output comes back.

The host prepares an immutable operation bundle (harness + helper library, the program modules, the author-visible
evidence, an optional ``.blend`` for render-only operations, ``operation.json``); a worker runs Blender against it and
holds a finished output ready; the host copies bounded regular files into fresh staging, hashes and validates them,
and only then commits. Three workers:

* ``FakeWorker`` (synthetic): scripted outcomes and labelled synthetic images; proves orchestration, never claims a
  real export; everything it produces is marked ``synthetic`` and can never become a compatible deliverable.
* ``NativeFixtureWorker`` (test-only): the installed host Blender, but ONLY for program sets whose SHA-256 is on an
  explicit allow-list (the audited fixed fixtures). Any other program is refused: generated code never runs on the host.
* ``DockerWorker``: a locally built digest-pinned Linux image, ``--pull never``, no network, read-only root, dropped
  capabilities, no new privileges, nonroot, CPU/memory/PID limits, bounded tmpfs for scratch and output, the bundle
  mounted read-only, nothing else mounted. Output is ingested as a tar stream that is validated entry by entry.
  Docker availability is a doctor question; the adapter is complete and unit-tested with a fake docker command.
"""
from __future__ import annotations

import ast
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import time

from ..paths import AR as AR_ROOT, AUTOMATION, BLENDER_DIR, blender_executable
from .artifacts import atomic_write, contained, is_reparse_point, regular_file_or_raise

OUTPUT_LIMITS = {"max_files": 400, "max_file_bytes": 96 * 1024 * 1024, "max_total_bytes": 512 * 1024 * 1024, "max_log_bytes": 2 * 1024 * 1024}
CONTAINER_LABEL = "lenses.agentic"
WORKER_INTERPRETER = "python3"          # passed as --entrypoint; the configured ``entry`` is its first argument (see worker/Dockerfile)
DEFAULT_RESOURCES ={"cpus": "2", "memory": "6g", "pids_limit": 512, "tmpfs_mb": 2048, "uid": 10001, "gid": 10001}
# EEVEE TAA samples of a bundle that names none: the harness default. The observation's own job asks for
# modeler.observe.RENDER_SAMPLES (16); the sheets are the only consumer of these renders, never a measurement.
DEFAULT_SAMPLES = 16


def llvmpipe_threads(cpus) -> int:
    """LP_NUM_THREADS for the worker: the integer part of the docker --cpus quota, at least 1. Unset, Mesa's llvmpipe
    starts one rasterizer thread per CPU the VM reports (16 on the pilot's host) and the cgroup quota (2 CPUs) then
    throttles them all: the render step of the first paid run spent 543 s of a 12-minute build that way."""
    return max(1, int(float(str(cpus))))


class WorkerError(RuntimeError):
    pass


class WorkerRefused(WorkerError):
    """The worker will not run this program (native fixture allow-list, missing doctor pass)."""


@dataclass
class WorkerOutcome:
    status: str                      # completed | failed | timed_out | interrupted | cancelled | lost
    exit_code: int | None
    seconds: float
    identity: dict
    stdout_tail: str = ""
    stderr_tail: str = ""
    note: str = ""
    gl: dict | None = None           # the worker's GL renderer record (worker/entry.py GL_PROBE); None: no render, or an older image
    fonts: dict | None = None        # {font path: sha256} of the image's font inventory; None: an older image

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Ingested:
    staging: Path
    files: list[dict]                # {rel, sha256, bytes}
    result: dict | None
    manifest_sha256: str
    synthetic: bool = False
    problems: list[str] | None = None


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def program_set_sha256(modules: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps({k: hashlib.sha256(v.encode("utf-8")).hexdigest() for k, v in sorted(modules.items())},
                                     sort_keys=True).encode()).hexdigest()


def utc_stamp() -> str:
    """Now in UTC with its zone, seconds precision (state.iso's form). A bare time.strftime is LOCAL time: the doctor's
    passed_utc of test-pilot-002 read 13:04:00 for 10:04Z (review 2026-09-28, INF-14)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- measurement fingerprint
# What MEASURES a build, as opposed to what builds it (the bundle's lib_sha256, bound to the doctor record) or the route's own
# code (cli.source_fingerprints' python_sources): the host modules the observation, the export and the lens / see-through
# metrics run, with their module-level imports inside this repository, plus the AR harness page and runtime the actual-AR
# renders come from. test-pilot-002 recorded none of it per file (review 2026-09-28, INF-12): bsa/cameras.py, raster.py,
# front.py, lens.py and the ar/qa harness were missing, so a moved number could not be attributed to a code change. Every
# operation bundle records it (write_bundle), and a rebuild diffs it against the source build's bundle (rebuild.py).
# It is deliberately NOT part of worker_config_fingerprint: that binds the doctor pass to what runs INSIDE the worker; the
# measurement code runs on the host, and a host edit must not demand a new container self-test.
MEASUREMENT_ROOTS = ("modeler.observe", "modeler.evaluate", "modeler.see_through", "modeler.lens_colour", "modeler.export",
                     "bsa.cameras", "bsa.archeck", "bsa.tryon", "reconstruction.mesh", "qa.provider_comparison")
MEASUREMENT_PACKAGES = ("modeler", "bsa", "reconstruction", "qa")
MEASUREMENT_EXCLUDED = ("modeler.agentic", "modeler.blender")       # fingerprinted elsewhere (python_sources, lib_sha256)
AR_HARNESS_FILES = ("qa/provider-comparison.mjs", "qa/provider-comparison.html", "qa/provider-comparison-ar.html",
                    "qa/provider-comparison-lighting.mjs", "package.json", "package-lock.json")


def _module_file(automation: Path, name: str) -> Path | None:
    p = automation.joinpath(*name.split("."))
    if p.with_suffix(".py").is_file():
        return p.with_suffix(".py")
    if (p / "__init__.py").is_file():
        return p / "__init__.py"
    return None


def _module_imports(automation: Path, path: Path) -> set[tuple[str, bool]]:
    """(name, required) for what a file imports at module level (top-level statements, including inside a top-level if / try).
    ``required``: the name must be a module (``import x.y``, the ``x`` of ``from x import y``); ``from x import y`` may name a
    module or an attribute. Imports inside functions are lazy: the roots list the lazily imported measurement modules
    themselves (see_through, lens_colour)."""
    rel = path.relative_to(automation).with_suffix("")
    parts = list(rel.parts)
    is_pkg = parts[-1] == "__init__"
    if is_pkg:
        parts = parts[:-1]
    package = parts if is_pkg else parts[:-1]
    nodes = []
    for top in ast.parse(path.read_bytes()).body:
        nodes.extend(ast.walk(top) if isinstance(top, (ast.If, ast.Try)) else [top])
    out: set[tuple[str, bool]] = set()
    for node in nodes:
        if isinstance(node, ast.Import):
            out.update((a.name, True) for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[:len(package) - (node.level - 1)] if node.level > 1 else package
                mod = ".".join(list(base) + ([node.module] if node.module else []))
            else:
                mod = node.module or ""
            out.add((mod, True))
            out.update((f"{mod}.{a.name}", False) for a in node.names)      # "from . import raster" names a module
    return out


def measurement_fingerprint(*, automation: Path | None = None, ar: Path | None = None) -> dict:
    """{schema, sha256, python: {path under automation/: sha256}, ar: {path under ar/: sha256}, missing: [...]}: the import
    closure of MEASUREMENT_ROOTS inside this repository, the AR harness files and every ar/src/**/*.ts."""
    automation = Path(automation or AUTOMATION)
    ar = Path(ar or AR_ROOT)
    seen: dict[str, Path] = {}
    missing: list[str] = []
    todo = [(name, True) for name in MEASUREMENT_ROOTS]
    while todo:
        name, required = todo.pop()
        if name.split(".")[0] not in MEASUREMENT_PACKAGES or any(name == e or name.startswith(e + ".") for e in MEASUREMENT_EXCLUDED):
            continue
        parts = name.split(".")
        for i in range(1, len(parts) + 1):                  # the packages along the path run too (their __init__)
            sub = ".".join(parts[:i])
            if sub in seen:
                continue
            f = _module_file(automation, sub)
            if f is None:
                if required and sub == name:        # a module the measurement code imports is gone (the import would fail)
                    missing.append(name.replace(".", "/") + ".py")
                continue
            seen[sub] = f
            todo.extend(_module_imports(automation, f))
    python = {f.relative_to(automation).as_posix(): sha256_file(f) for f in sorted(seen.values())}
    ar_files = {}
    for rel in AR_HARNESS_FILES:
        p = ar / rel
        if p.is_file():
            ar_files[rel] = sha256_file(p)
        else:
            missing.append("ar/" + rel)
    if (ar / "src").is_dir():
        for p in sorted((ar / "src").rglob("*.ts")):
            ar_files[p.relative_to(ar).as_posix()] = sha256_file(p)
    digest = hashlib.sha256(json.dumps({"python": python, "ar": ar_files}, sort_keys=True).encode()).hexdigest()
    return {"schema": 1, "sha256": digest, "python": python, "ar": dict(sorted(ar_files.items())), "missing": sorted(set(missing))}


def fingerprint_diff(before: dict, after: dict) -> dict:
    """{changed, added, removed}: sorted file names between two {file: sha256} maps."""
    before, after = dict(before or {}), dict(after or {})
    return {"changed": sorted(k for k in before.keys() & after.keys() if before[k] != after[k]),
            "added": sorted(after.keys() - before.keys()), "removed": sorted(before.keys() - after.keys())}


# --------------------------------------------------------------------------- bundles
def write_bundle(bundle_dir: Path, *, operation_id: str, mode: str, modules: dict[str, str], module_order: list[str], renders: list[dict],
                 evidence: dict | None, blend_bytes: bytes | None, time_limit_s: int, samples: int = DEFAULT_SAMPLES, export: bool = True) -> dict:
    """An immutable operation bundle. ``evidence`` must already be the author-visible reduction (no sealed entries)."""
    bundle_dir = Path(bundle_dir)
    if bundle_dir.exists() and any(bundle_dir.iterdir()):
        raise WorkerError(f"bundle folder {bundle_dir} is not empty")
    if mode not in ("build", "render_only"):
        raise WorkerError(f"unknown operation mode {mode!r}")
    if mode == "render_only" and blend_bytes is None:
        raise WorkerError("render_only needs the candidate .blend")
    if evidence is not None and ("held_out" in evidence or any(r.get("held_out") for r in evidence.get("inputs", []))):
        raise WorkerError("sealed evidence must not enter a worker bundle")
    (bundle_dir / "lib").mkdir(parents=True)
    (bundle_dir / "program").mkdir()
    (bundle_dir / "input").mkdir()
    lib_hashes = {}
    for name in ("glasses_lib.py", "harness.py"):
        data = (BLENDER_DIR / name).read_bytes()
        atomic_write(bundle_dir / "lib" / name, data)
        lib_hashes[name] = hashlib.sha256(data).hexdigest()
    module_files = []
    for name in module_order:
        if name not in modules:
            continue
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", name):
            raise WorkerError(f"module name {name!r} is not safe")
        atomic_write(bundle_dir / "program" / f"{name}.py", modules[name].encode("utf-8"))
        module_files.append({"name": name, "file": f"program/{name}.py"})
    if evidence is not None:
        atomic_write(bundle_dir / "input" / "evidence.json", json.dumps(evidence, indent=1, default=str).encode("utf-8"))
    if blend_bytes is not None:
        atomic_write(bundle_dir / "input" / "candidate.blend", blend_bytes)
    operation = {"protocol": "lenses_agentic_worker_v1", "operation_id": operation_id, "mode": mode, "modules": module_files,
                 "renders": renders, "export": bool(export), "save_blend": True, "samples": int(samples), "time_limit_s": int(time_limit_s),
                 "evidence": "input/evidence.json" if evidence is not None else None,
                 "blend": "input/candidate.blend" if blend_bytes is not None else None,
                 "lib_sha256": lib_hashes, "program_set_sha256": program_set_sha256({m["name"]: modules[m["name"]] for m in module_files}),
                 "output_limits": OUTPUT_LIMITS,
                 # host-side provenance (the worker ignores it): the code that measures this operation's output
                 "measurement_fingerprint": measurement_fingerprint()}
    atomic_write(bundle_dir / "operation.json", json.dumps(operation, indent=1).encode("utf-8"))
    return operation


def harness_job_for(operation: dict, *, bundle_root: str, out_dir: str) -> dict:
    """The job.json the existing harness understands, with every path inside the worker's view of the bundle."""
    job = {"lib_dir": f"{bundle_root}/lib", "out_dir": out_dir, "export": operation["export"], "save_blend": operation["save_blend"],
           "renders": operation["renders"], "samples": operation["samples"], "mode": operation["mode"],
           "modules": [{"name": m["name"], "path": f"{bundle_root}/{m['file']}"} for m in operation["modules"]],
           "evidence_path": f"{bundle_root}/{operation['evidence']}" if operation.get("evidence") else None}
    if operation["mode"] == "render_only":
        job["blend_path"] = f"{bundle_root}/{operation['blend']}"
    return job


# --------------------------------------------------------------------------- ingestion
def validate_and_ingest_dir(source: Path, staging: Path, *, limits: dict = OUTPUT_LIMITS) -> Ingested:
    """Copy bounded regular files from a finished output folder into fresh staging; links, reparse points, devices,
    path escapes and oversize content are refused; everything is hashed independently of any receipt the worker wrote."""
    source, staging = Path(source), Path(staging)
    if staging.exists() and any(staging.iterdir()):
        raise WorkerError("staging must be empty")
    staging.mkdir(parents=True, exist_ok=True)
    files, total, problems = [], 0, []
    for root, dirs, names in os.walk(source, followlinks=False):
        rootp = Path(root)
        for d in list(dirs):
            if is_reparse_point(rootp / d):
                problems.append(f"link directory refused: {(rootp / d).relative_to(source).as_posix()}")
                dirs.remove(d)
        for n in names:
            p = rootp / n
            rel = p.relative_to(source).as_posix()
            try:
                size = regular_file_or_raise(p, max_bytes=limits["max_file_bytes"])
            except Exception as e:  # noqa: BLE001
                problems.append(f"{rel}: {e}")
                continue
            if not contained(p, source):
                problems.append(f"{rel}: escapes the output folder")
                continue
            total += size
            if total > limits["max_total_bytes"] or len(files) >= limits["max_files"]:
                problems.append(f"{rel}: output exceeds the total byte or file-count limit")
                break
            dest = staging / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(p, dest)
            files.append({"rel": rel, "sha256": sha256_file(dest), "bytes": size})
    return _finish_ingest(staging, files, problems)


def validate_and_ingest_tar(tar_bytes: bytes, staging: Path, *, strip_prefix: str = "", limits: dict = OUTPUT_LIMITS) -> Ingested:
    """The same validation for a ``docker cp`` tar stream: only regular files, no links or devices, no path escape."""
    staging = Path(staging)
    if staging.exists() and any(staging.iterdir()):
        raise WorkerError("staging must be empty")
    staging.mkdir(parents=True, exist_ok=True)
    files, total, problems = [], 0, []
    try:
        tf = tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:*")
    except tarfile.TarError as e:
        raise WorkerError(f"output stream is not a tar archive: {e}") from None
    with tf:
        for member in tf:
            name = member.name
            if strip_prefix and name.startswith(strip_prefix):
                name = name[len(strip_prefix):].lstrip("/")
            if member.isdir():
                continue
            if not member.isreg():
                problems.append(f"{member.name}: not a regular file (type {member.type!r}) refused")
                continue
            parts = Path(name).parts
            if not name or name.startswith("/") or any(p in ("..", "") for p in parts) or re.search(r"[<>:\"|?*\x00]", name) or (len(parts[0]) == 2 and parts[0][1] == ":"):
                problems.append(f"{member.name}: unsafe path refused")
                continue
            if member.size > limits["max_file_bytes"]:
                problems.append(f"{name}: {member.size} bytes above the per-file limit")
                continue
            total += member.size
            if total > limits["max_total_bytes"] or len(files) >= limits["max_files"]:
                problems.append(f"{name}: output exceeds the total byte or file-count limit")
                break
            f = tf.extractfile(member)
            if f is None:
                problems.append(f"{name}: unreadable")
                continue
            data = f.read()
            dest = staging / name
            if not contained(dest.parent, staging) and dest.parent != staging:
                problems.append(f"{name}: escapes staging")
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(dest, data)
            files.append({"rel": name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})
    return _finish_ingest(staging, files, problems)


def _finish_ingest(staging: Path, files: list[dict], problems: list[str]) -> Ingested:
    result = None
    rp = staging / "result.json"
    if rp.is_file():
        try:
            result = json.loads(rp.read_text(encoding="utf-8"))
            if not isinstance(result, dict):
                problems.append("result.json is not an object")
                result = None
        except Exception as e:  # noqa: BLE001
            problems.append(f"result.json unreadable: {type(e).__name__}: {e}")
    files.sort(key=lambda f: f["rel"])
    manifest = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    return Ingested(staging=staging, files=files, result=result, manifest_sha256=manifest, problems=problems or None)


def result_is_untrusted_receipt(ingested: Ingested) -> list[str]:
    """The worker's own claims (paths in result.json) are cross-checked against what was actually ingested."""
    issues = []
    r = ingested.result or {}
    have = {f["rel"] for f in ingested.files}
    for key in ("parts_npz", "materials_json", "blend"):
        v = r.get(key)
        if v:
            rel = Path(str(v)).name if "/" not in str(v).replace("\\", "/") else str(v).replace("\\", "/").split("/out/", 1)[-1]
            if rel not in have and Path(rel).name not in {Path(f["rel"]).name for f in ingested.files}:
                issues.append(f"result.json names {key}={v} which was not ingested")
    for row in r.get("renders", []) or []:
        p = row.get("path")
        if p and Path(str(p)).name not in {Path(f["rel"]).name for f in ingested.files}:
            issues.append(f"result.json names render {row.get('id')} which was not ingested")
    return issues


# --------------------------------------------------------------------------- workers
class Worker:
    kind = "abstract"
    synthetic = False

    def describe(self) -> dict:
        return {"kind": self.kind, "synthetic": self.synthetic}

    def launch(self, bundle_dir: Path, operation: dict, *, work_dir: Path, deadline_s: int, identity: dict) -> dict:
        """Start the operation; returns the persisted identity (container/pid). Must be idempotent per identity."""
        raise NotImplementedError

    def await_outcome(self, identity: dict, *, deadline_s: int, cancel_check=None) -> WorkerOutcome:
        raise NotImplementedError

    def ingest(self, identity: dict, staging: Path) -> Ingested:
        raise NotImplementedError

    def stop(self, identity: dict, *, timeout_s: int = 30) -> None:
        pass

    def reattach(self, identity: dict) -> str:
        """'running' | 'finished' | 'lost' for an identity persisted before a restart."""
        return "lost"


class FakeWorker(Worker):
    """Synthetic outcomes for the offline demo and tests. ``scenario`` maps operation ordinals (1-based) or ids to
    {"ok": bool, "error": str, "renders": bool}; unlisted operations succeed. Every image says SYNTHETIC on it."""
    kind = "fake"
    synthetic = True

    def __init__(self, scenario: dict | None = None):
        self.scenario = dict(scenario or {})
        self.count = 0
        self.runs: dict[str, dict] = {}

    def describe(self) -> dict:
        return {"kind": self.kind, "synthetic": True, "note": "SYNTHETIC worker: no Blender ran, no real export, no AR check; orchestration only"}

    def launch(self, bundle_dir, operation, *, work_dir, deadline_s, identity):
        self.count += 1
        step = self.scenario.get(operation["operation_id"], self.scenario.get(self.count, {}))
        identity = dict(identity, kind="fake", ordinal=self.count, started_utc=utc_stamp())
        self.runs[operation["operation_id"]] = {"operation": operation, "step": step, "work_dir": str(work_dir), "identity": identity}
        return identity

    def await_outcome(self, identity, *, deadline_s, cancel_check=None):
        run = self.runs.get(identity.get("operation_id"))
        if run is None:
            return WorkerOutcome("lost", None, 0.0, identity, note="fake worker has no record of this operation (restart)")
        step = run["step"]
        if step.get("hang"):
            return WorkerOutcome("timed_out", None, float(deadline_s), identity, note="fake worker: scripted hang")
        if cancel_check and cancel_check():
            return WorkerOutcome("cancelled", None, 0.0, identity)
        return WorkerOutcome("completed" if step.get("ok", True) else "failed", 0 if step.get("ok", True) else 1, 0.01, identity,
                             stdout_tail="MODELER_HARNESS| done ok=%s (SYNTHETIC)" % step.get("ok", True))

    def ingest(self, identity, staging):
        run = self.runs[identity["operation_id"]]
        op, step = run["operation"], run["step"]
        out = Path(run["work_dir"]) / "out"
        out.mkdir(parents=True, exist_ok=True)
        ok = bool(step.get("ok", True))
        result = {"ok": ok, "synthetic": True, "mode": op["mode"], "module_results": [{"name": m["name"], "ok": ok or i < len(op["modules"]) - 1,
                  "error": None if (ok or i < len(op["modules"]) - 1) else step.get("error", "SyntheticError: scripted build failure"),
                  "traceback": None if ok else "Traceback (synthetic)\n" + step.get("error", "SyntheticError")} for i, m in enumerate(op["modules"])],
                  "notes": ["SYNTHETIC: no Blender ran"], "inventory": [{"object": "front_plate", "part": "frame", "component": "front", "triangles": 0}] if ok else [],
                  "renders": [], "blend": None, "parts_npz": None, "materials_json": None, "error": None if ok else step.get("error", "scripted failure"),
                  "seconds": 0.01, "blender_version": "SYNTHETIC"}
        if ok and op["mode"] == "build":
            result["blend"] = "candidate.blend"
            atomic_write(out / "candidate.blend", b"SYNTHETIC-BLEND\n" + op["program_set_sha256"].encode())
        if ok and step.get("renders", True):
            # the real harness writes every render beside result.json (modeler/blender/harness.py render_views); the fake keeps that layout
            for spec in op["renders"]:
                data = synthetic_png(spec["id"], op["operation_id"], int(spec.get("width", 320)), int(spec.get("height", 240)))
                atomic_write(out / f"{spec['id']}.png", data)
                result["renders"].append({"id": spec["id"], "path": f"{spec['id']}.png", "kind": spec.get("kind", "textured"),
                                          "width": spec.get("width", 320), "height": spec.get("height", 240), "camera": spec.get("camera"), "error": None, "seconds": 0.0})
        atomic_write(out / "result.json", json.dumps(result, indent=1).encode())
        atomic_write(out / "blender.stdout.log", b"SYNTHETIC worker: nothing ran\n")
        ing = validate_and_ingest_dir(out, staging)
        ing.synthetic = True
        return ing

    def reattach(self, identity):
        return "lost"


def synthetic_png(label: str, operation_id: str, width: int, height: int) -> bytes:
    """A conspicuously synthetic image: flat grey with the words SYNTHETIC, the view id and the operation id."""
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (max(64, width), max(48, height)), (120, 120, 128))
    d = ImageDraw.Draw(im)
    d.text((6, 6), "SYNTHETIC", fill=(255, 220, 0))
    d.text((6, 22), str(label)[:40], fill=(255, 255, 255))
    d.text((6, 38), str(operation_id)[:40], fill=(255, 255, 255))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


class NativeFixtureWorker(Worker):
    """The installed host Blender for an explicit allow-list of program-set hashes: test fixtures only."""
    kind = "native-fixture"

    def __init__(self, allowed_program_sha256: set[str] | frozenset[str], *, blender: Path | None = None):
        if not allowed_program_sha256:
            raise WorkerRefused("the native fixture worker needs an explicit allow-list of program hashes")
        self.allowed = frozenset(allowed_program_sha256)
        self.blender = Path(blender) if blender else blender_executable()
        self.procs: dict[str, subprocess.Popen] = {}
        self.records: dict[str, dict] = {}

    def describe(self) -> dict:
        return {"kind": self.kind, "synthetic": False, "blender": str(self.blender), "allowed_program_sha256": sorted(self.allowed),
                "note": "test-only: refuses any program set not on the allow-list; not an isolation boundary"}

    def launch(self, bundle_dir, operation, *, work_dir, deadline_s, identity):
        if operation["program_set_sha256"] not in self.allowed and operation["mode"] == "build":
            raise WorkerRefused(f"program set {operation['program_set_sha256'][:12]} is not an allowed native fixture; generated code does not run on the host")
        if self.blender is None or not self.blender.is_file():
            raise WorkerError("Blender is not installed")
        work_dir = Path(work_dir)
        out = work_dir / "out"
        out.mkdir(parents=True, exist_ok=True)
        job = harness_job_for(operation, bundle_root=Path(bundle_dir).resolve().as_posix(), out_dir=out.resolve().as_posix())
        atomic_write(work_dir / "job.json", json.dumps(job, indent=1).encode())
        cmd = [str(self.blender), "-b", "--factory-startup", "--python", str(Path(bundle_dir) / "lib" / "harness.py"), "--", str(work_dir / "job.json")]
        env = {"PATH": os.environ.get("PATH", ""), "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""), "TEMP": str(work_dir), "TMP": str(work_dir),
               "HOME": str(work_dir), "USERPROFILE": str(work_dir), "PYTHONDONTWRITEBYTECODE": "1"}
        with open(work_dir / "blender.stdout.log", "wb") as so, open(work_dir / "blender.stderr.log", "wb") as se:
            proc = subprocess.Popen(cmd, stdout=so, stderr=se, env=env, cwd=str(work_dir))
        identity = dict(identity, kind=self.kind, pid=proc.pid, started_unix=time.time(), work_dir=str(work_dir))
        self.procs[identity["operation_id"]] = proc
        self.records[identity["operation_id"]] = {"work_dir": work_dir, "cmd": cmd}
        return identity

    def await_outcome(self, identity, *, deadline_s, cancel_check=None):
        proc = self.procs.get(identity["operation_id"])
        if proc is None:
            return WorkerOutcome("lost", None, 0.0, identity, note="process handle not held by this runner (restart)")
        t0 = time.monotonic()
        status = "completed"
        while True:
            rc = proc.poll()
            if rc is not None:
                break
            if cancel_check and cancel_check():
                _kill_tree(proc)
                status = "cancelled"
                break
            if time.monotonic() - t0 > deadline_s:
                _kill_tree(proc)
                status = "timed_out"
                break
            time.sleep(0.2)
        rc = proc.poll()
        wd = self.records[identity["operation_id"]]["work_dir"]
        so = (wd / "blender.stdout.log").read_bytes()[-2000:].decode("utf-8", "replace") if (wd / "blender.stdout.log").exists() else ""
        se = (wd / "blender.stderr.log").read_bytes()[-2000:].decode("utf-8", "replace") if (wd / "blender.stderr.log").exists() else ""
        if status == "completed" and rc != 0:
            status = "failed"
        return WorkerOutcome(status, rc, time.monotonic() - t0, identity, stdout_tail=so, stderr_tail=se)

    def ingest(self, identity, staging):
        wd = self.records[identity["operation_id"]]["work_dir"]
        for name in ("blender.stdout.log", "blender.stderr.log"):
            src = wd / name
            if src.exists():
                data = src.read_bytes()[-OUTPUT_LIMITS["max_log_bytes"]:]
                atomic_write(wd / "out" / name, data)
        return validate_and_ingest_dir(wd / "out", staging)

    def stop(self, identity, *, timeout_s=30):
        proc = self.procs.get(identity.get("operation_id"))
        if proc is not None and proc.poll() is None:
            _kill_tree(proc)


def _kill_tree(proc: subprocess.Popen) -> None:
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=30)
        else:
            proc.kill()
        proc.wait(timeout=30)
    except Exception:  # noqa: BLE001 - best effort; the caller records the state
        pass


class DockerWorker(Worker):
    """docker run with the full isolation argument vector; output ingested as a validated tar stream.

    ``config`` (the worker config file, pinned into the job): image_digest (sha256:...), image_ref (for messages only),
    blender_version, entry (path of the trusted entry point inside the image), resources {cpus, memory, pids_limit,
    tmpfs_mb, uid, gid}, doctor {passed_utc, fingerprint} written by the doctor self-test. ``runner`` is the function
    that executes a docker argv (injected in tests): runner(argv, input=None, timeout=None) -> (rc, stdout, stderr).
    """
    kind = "docker"

    def __init__(self, config: dict, *, runner=None, docker: str = "docker"):
        self.config = validate_worker_config(config)
        self.runner = runner or run_docker
        self.docker = docker
        self.launched: dict[str, dict] = {}

    def describe(self) -> dict:
        return {"kind": self.kind, "synthetic": False, "image_digest": self.config["image_digest"], "blender_version": self.config.get("blender_version"),
                "resources": self.config["resources"], "doctor": self.config.get("doctor")}

    def container_name(self, identity: dict) -> str:
        return f"lenses-agentic-{identity['job_short']}-{identity['operation_id']}-{identity['attempt']}"

    def argv_run(self, bundle_dir: Path, operation: dict, identity: dict) -> list[str]:
        r = self.config["resources"]
        name = self.container_name(identity)
        raw = Path(bundle_dir)
        # checked BEFORE resolving: resolve() follows a junction/symlink to its target, so a linked bundle path would
        # otherwise be silently replaced by (and mount) whatever folder the link points at
        if is_reparse_point(raw) or not contained(raw, raw.parent):
            raise WorkerError("bundle path is a link")
        bundle = raw.resolve()
        if is_reparse_point(bundle):
            raise WorkerError("bundle path is a link")
        argv = [self.docker, "run", "--detach", "--name", name, "--label", f"{CONTAINER_LABEL}.job={identity['job_short']}",
                "--label", f"{CONTAINER_LABEL}.operation={identity['operation_id']}", "--label", f"{CONTAINER_LABEL}.attempt={identity['attempt']}",
                "--pull", "never", "--network", "none", "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges=true",
                "--user", f"{r['uid']}:{r['gid']}", "--cpus", str(r["cpus"]), "--memory", str(r["memory"]), "--memory-swap", str(r["memory"]),
                "--pids-limit", str(r["pids_limit"]), "--tmpfs", f"/work:rw,nosuid,nodev,noexec,size={int(r['tmpfs_mb'])}m,uid={r['uid']},gid={r['gid']},mode=0770",
                "--mount", f"type=bind,source={bundle.as_posix()},target=/bundle,readonly",
                "--env", "HOME=/work/home", "--env", "TMPDIR=/work/tmp", "--env", "XDG_CACHE_HOME=/work/cache", "--env", "XDG_CONFIG_HOME=/work/config",
                "--env", "BLENDER_USER_RESOURCES=/work/blender", "--env", "PYTHONDONTWRITEBYTECODE=1",
                "--env", f"LENSES_TIME_LIMIT_S={int(operation['time_limit_s'])}",
                # llvmpipe's thread count follows the CPU quota (worker/entry.py forwards it into Blender's environment)
                "--env", f"LP_NUM_THREADS={llvmpipe_threads(r['cpus'])}",
                # the process is named explicitly: docker runs ENTRYPOINT + <arguments after the image>, and the image
                # declares the supervisor as its ENTRYPOINT, so naming the interpreter again after the image would hand
                # entry.py the argv  [python3, <entry>, operation.json, /work/out]  and op_path="python3"
                "--entrypoint", WORKER_INTERPRETER,
                self.config["image_digest"], self.config["entry"], "/bundle/operation.json", "/work/out"]
        return argv

    def launch(self, bundle_dir, operation, *, work_dir, deadline_s, identity):
        doctor = self.config.get("doctor") or {}
        if not doctor.get("passed_utc"):
            raise WorkerRefused("the Docker worker has no passing doctor record for this configuration; generated code is not executed")
        # the record is bound to the config/image/harness fingerprint (docker_doctor, selftest); resume never re-runs the
        # doctor, so a record left over from another harness or config must not enable a launch
        if doctor.get("fingerprint") != worker_config_fingerprint(self.config):
            raise WorkerRefused("the doctor record is not bound to this config/image/harness fingerprint (run doctor --self-test again); generated code is not executed")
        identity = dict(identity, kind=self.kind, container_name=self.container_name(identity), image_digest=self.config["image_digest"])
        argv = self.argv_run(bundle_dir, operation, identity)
        rc, out, err = self.runner(argv, timeout=120)
        if rc != 0:
            raise WorkerError(f"docker run failed ({rc}): {err.decode('utf-8', 'replace')[-500:]}")
        identity["container_id"] = out.decode("utf-8", "replace").strip()[:64]
        identity["started_unix"] = time.time()
        self.launched[identity["operation_id"]] = identity
        return identity

    def _inspect(self, identity: dict) -> dict | None:
        rc, out, _ = self.runner([self.docker, "inspect", identity["container_name"]], timeout=60)
        if rc != 0:
            return None
        try:
            rows = json.loads(out.decode("utf-8", "replace"))
        except json.JSONDecodeError:
            return None
        if not rows or not isinstance(rows, list):
            return None
        row = rows[0]
        if identity.get("container_id") and not str(row.get("Id", "")).startswith(identity["container_id"][:12]):
            return {"State": {"Status": "mismatch"}}
        labels = (row.get("Config") or {}).get("Labels") or {}
        if labels.get(f"{CONTAINER_LABEL}.operation") != identity["operation_id"]:
            return {"State": {"Status": "mismatch"}}
        return row

    # /work is a tmpfs: `docker cp` cannot read tmpfs or user mounts (documented; on Docker Desktop it answers "Could not find the
    # file" although the file exists), so the output is streamed as a tar by `tar` running INSIDE the container over `docker exec`.
    # The supervisor keeps the container alive after READY.json until the host has copied and verified everything.
    def _ready(self, identity: dict) -> dict | None:
        rc, out, _ = self.runner([self.docker, "exec", identity["container_name"], "tar", "-C", "/work/out", "-cf", "-", "READY.json"], timeout=60)
        if rc != 0:
            return None
        try:
            ing = validate_and_ingest_tar_bytes_single(out)
            return json.loads(ing)
        except Exception:  # noqa: BLE001
            return None

    def await_outcome(self, identity, *, deadline_s, cancel_check=None):
        t0 = time.monotonic()
        while True:
            if cancel_check and cancel_check():
                self.stop(identity)
                return WorkerOutcome("cancelled", None, time.monotonic() - t0, identity)
            info = self._inspect(identity)
            if info is None:
                return WorkerOutcome("lost", None, time.monotonic() - t0, identity, note="container not found")
            st = (info.get("State") or {}).get("Status")
            if st == "mismatch":
                return WorkerOutcome("lost", None, time.monotonic() - t0, identity, note="a container with this name has another identity")
            ready = self._ready(identity)
            if ready is not None:
                return WorkerOutcome("completed" if ready.get("ok") else "failed", int(ready.get("exit_code")) if isinstance(ready.get("exit_code"), int) else None,
                                     time.monotonic() - t0, identity, stdout_tail=str(ready.get("stdout_tail", ""))[-2000:], stderr_tail=str(ready.get("stderr_tail", ""))[-2000:],
                                     gl=receipt_gl(ready.get("gl")), fonts=receipt_fonts(ready.get("fonts")))
            if st in ("exited", "dead"):
                return WorkerOutcome("interrupted", (info.get("State") or {}).get("ExitCode"), time.monotonic() - t0, identity,
                                     note="the container stopped before READY.json: its tmpfs output is gone")
            if time.monotonic() - t0 > deadline_s:
                self.stop(identity)
                return WorkerOutcome("timed_out", None, time.monotonic() - t0, identity)
            time.sleep(2.0)

    def ingest(self, identity, staging):
        rc, out, err = self.runner([self.docker, "exec", identity["container_name"], "tar", "-C", "/work", "-cf", "-", "out"], timeout=600)
        if rc != 0:
            raise WorkerError(f"docker exec tar failed (the container may have stopped: its tmpfs output is gone): {err.decode('utf-8', 'replace')[-300:]}")
        ing = validate_and_ingest_tar(out, staging, strip_prefix="out/")
        ready = ing.result and None
        rp = Path(staging) / "READY.json"
        if rp.is_file():
            try:
                ready = json.loads(rp.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                ready = None
        if ready and isinstance(ready.get("files"), list):
            claimed = {f.get("rel"): f.get("sha256") for f in ready["files"] if isinstance(f, dict)}
            actual = {f["rel"]: f["sha256"] for f in ing.files if f["rel"] != "READY.json"}
            for rel, sha in claimed.items():
                if actual.get(rel) != sha:
                    (ing.problems or []).append(f"worker receipt disagrees for {rel}") if ing.problems is not None else setattr(ing, "problems", [f"worker receipt disagrees for {rel}"])
        self.stop(identity)
        return ing

    def stop(self, identity, *, timeout_s=30):
        self.runner([self.docker, "rm", "-f", identity["container_name"]], timeout=timeout_s + 30)

    def reattach(self, identity):
        info = self._inspect(identity)
        if info is None or (info.get("State") or {}).get("Status") == "mismatch":
            return "lost"
        if self._ready(identity) is not None:
            return "finished"
        st = (info.get("State") or {}).get("Status")
        return "running" if st in ("running", "created", "paused") else "lost"


GL_RECORD_KEYS = ("renderer", "vendor", "version", "backend", "device", "error")


def receipt_gl(value) -> dict | None:
    """The GL renderer record of an untrusted READY.json (worker/entry.py): known keys only, bounded strings, and the count
    of EGL_BAD_MATCH lines the supervisor removed from stderr. None when the receipt carries none (no render; older image)."""
    if not isinstance(value, dict):
        return None
    out = {k: str(value[k])[:200] for k in GL_RECORD_KEYS if k in value and value[k] is not None}
    n = value.get("egl_bad_match_lines_removed")
    if type(n) is int and 0 <= n < 10_000:
        out["egl_bad_match_lines_removed"] = n
    return out or None


def receipt_fonts(value) -> dict | None:
    """{font path: sha256} of an untrusted READY.json, at most 64 well-formed entries; None when absent (older image)."""
    if not isinstance(value, dict):
        return None
    out = {}
    for path, digest in value.items():
        if len(out) >= 64:
            break
        if isinstance(path, str) and path.startswith("/") and len(path) <= 200 and isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest):
            out[path] = digest
    return out


def validate_and_ingest_tar_bytes_single(tar_bytes: bytes) -> bytes:
    """The bytes of the single regular file inside a tar stream (docker cp of one file)."""
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:*") as tf:
        members = [m for m in tf if m.isreg()]
        if len(members) != 1 or members[0].size > 1_000_000:
            raise WorkerError("expected exactly one small regular file")
        return tf.extractfile(members[0]).read()


def run_docker(argv: list[str], *, input=None, timeout=None) -> tuple[int, bytes, bytes]:
    try:
        p = subprocess.run(argv, input=input, capture_output=True, timeout=timeout)
    except FileNotFoundError:
        return 127, b"", b"docker executable not found"
    except subprocess.TimeoutExpired:
        return 124, b"", b"docker command timed out"
    return p.returncode, p.stdout, p.stderr


def validate_worker_config(config: dict) -> dict:
    if not isinstance(config, dict):
        raise WorkerError("worker config must be an object")
    digest = config.get("image_digest")
    # a registry digest (<repository>@sha256:<manifest digest>) or the content-addressed image ID (sha256:<64 hex>, `docker image inspect
    # --format '{{.Id}}'`): docker resolves <name>@sha256:... only for pulled/pushed images, so a locally built image is referenced by its ID
    if not isinstance(digest, str) or not re.fullmatch(r"([a-z0-9._/-]+@)?sha256:[0-9a-f]{64}", digest):
        raise WorkerError("worker config needs image_digest as <repository>@sha256:<64 hex> (a registry digest) or sha256:<64 hex> "
                          "(the image ID of a locally built image, from `docker image inspect --format '{{.Id}}'`); never invented")
    entry = config.get("entry", "/opt/lenses/worker_entry.py")
    if not isinstance(entry, str) or not entry.startswith("/"):
        raise WorkerError("worker config entry must be an absolute path inside the image")
    res = dict(DEFAULT_RESOURCES)
    res.update(config.get("resources") or {})
    for k in ("pids_limit", "tmpfs_mb", "uid", "gid"):
        if type(res[k]) is not int or res[k] <= 0:
            raise WorkerError(f"worker resource {k} must be a positive integer")
    if res["uid"] == 0 or res["gid"] == 0:
        raise WorkerError("the worker must not run as root")
    if not re.fullmatch(r"\d+(\.\d+)?", str(res["cpus"])) or not re.fullmatch(r"\d+[kmg]?", str(res["memory"]).lower()):
        raise WorkerError("worker cpus/memory must be docker numbers like 2 / 6g")
    out = dict(config)
    out.update(image_digest=digest, entry=entry, resources=res)
    return out


def worker_config_fingerprint(config: dict) -> str:
    """What a doctor pass is bound to: the config plus the harness and helper library bytes."""
    payload = {"config": {k: v for k, v in config.items() if k != "doctor"},
               "harness_sha256": hashlib.sha256((BLENDER_DIR / "harness.py").read_bytes()).hexdigest(),
               "lib_sha256": hashlib.sha256((BLENDER_DIR / "glasses_lib.py").read_bytes()).hexdigest()}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


# --------------------------------------------------------------------------- doctor
def docker_doctor(config: dict | None, *, runner=None, docker: str = "docker") -> dict:
    """Read-only: is the engine reachable, is the pinned image present, which Blender does it carry. No key is read."""
    runner = runner or run_docker
    out = {"docker_cli": None, "engine": None, "image_present": None, "image_blender_version": None, "config_valid": None, "doctor_pass": None,
           "blocking": []}
    rc, so, se = runner([docker, "version", "--format", "{{.Client.Version}}|{{.Server.Version}}"], timeout=30)
    text = (so or b"").decode("utf-8", "replace").strip()
    if rc == 127:
        out["docker_cli"] = "absent"
        out["blocking"].append("docker CLI not found")
        return out
    client, _, server = text.partition("|") if "|" in text else (text, "", "")
    out["docker_cli"] = client or "present"
    out["engine"] = server or None
    if not server:
        out["blocking"].append("Docker engine unavailable: " + ((se or b"").decode("utf-8", "replace").strip().splitlines() or ["no server version"])[0][:200])
    if config is None:
        out["config_valid"] = False
        out["blocking"].append("no worker config: build the image locally and write its real digest (README: worker setup)")
        return out
    try:
        cfg = validate_worker_config(config)
        out["config_valid"] = True
    except WorkerError as e:
        out["config_valid"] = False
        out["blocking"].append(f"worker config invalid: {e}")
        return out
    if out["engine"]:
        rc, so, se = runner([docker, "image", "inspect", cfg["image_digest"], "--format", "{{.Id}}"], timeout=60)
        out["image_present"] = rc == 0
        if rc != 0:
            out["blocking"].append(f"image {cfg['image_digest']} is not present locally (docker image inspect failed)")
        else:
            # --entrypoint blender: the image's ENTRYPOINT is the worker supervisor, which must not receive "blender --version"
            rc, so, se = runner([docker, "run", "--rm", "--network", "none", "--pull", "never", "--entrypoint", "blender", cfg["image_digest"], "--version"], timeout=180)
            ver = (so or b"").decode("utf-8", "replace").strip().splitlines()
            out["image_blender_version"] = ver[0] if ver else None
            if rc != 0 or not ver:
                out["blocking"].append("blender --version failed inside the image")
            elif cfg.get("blender_version") and cfg["blender_version"] not in ver[0]:
                out["blocking"].append(f"image Blender {ver[0]!r} differs from the configured {cfg['blender_version']!r}")
    doc = cfg.get("doctor") or {}
    fp = worker_config_fingerprint(cfg)
    out["doctor_pass"] = bool(doc.get("passed_utc")) and doc.get("fingerprint") == fp
    out["fingerprint"] = fp
    if not out["doctor_pass"]:
        out["blocking"].append("no passing doctor self-test bound to this config/image/harness fingerprint (run doctor --self-test)")
    return out


def native_doctor() -> dict:
    exe = blender_executable()
    out = {"blender": str(exe) if exe else None, "version": None}
    if exe is None:
        out["blocking"] = ["host Blender 5.x not found (MODELER_BLENDER)"]
        return out
    try:
        p = subprocess.run([str(exe), "--version"], capture_output=True, text=True, timeout=60)
        out["version"] = (p.stdout or "").strip().splitlines()[0] if p.stdout else None
    except Exception as e:  # noqa: BLE001
        out["blocking"] = [f"blender --version failed: {type(e).__name__}"]
    return out
