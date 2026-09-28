"""BSA orchestration (DESIGN.md "Orchestration"): run S0-S10 in order, AR-check every export in the actual
TryOnRenderer, gate, run the S11 look stage, and write ``data/bsa/runs/<run>/summary.json`` plus a review sheet per
product.

    python -m bsa.pipeline --run m2 --all                 # the 5 products; S11 observe-only (--look-driver none)
    python -m bsa.pipeline --run m2 --product vb --from s4 --force
    python -m bsa.pipeline --run m2 --all --from s11 --force --look-driver scripted --look-script plans/  # <p>.json

Execution rule (make-like, from the artifacts AND the code that wrote them):
- A stage runs when its ``result.json`` is missing, when it is OLDER than the ``result.json`` of a stage it
  reads (``DEPS``; e.g. S8 must follow a re-run S3), when the code that wrote it is not the code on disk now
  (below), or when ``--force`` is given and the stage is at or after ``--from``. Otherwise the stage module's
  own ``run`` skips it. Stages before ``--from`` are never forced.
- Code provenance: a stage's code = its module plus every ``bsa`` module it imports, transitively (module- and
  function-level imports; a static over-approximation), plus ``bsa/__init__.py``. The pipeline fingerprints
  the whole package and imports every stage module BEFORE running anything, so the fingerprint is the code
  that executes; after each stage it writes ``<stage>/code.json`` (per-module sha256 + the result's mtime).
  A stage whose recorded code differs from the disk is stale (``code_changed:<modules>``). A result without a
  valid record (written by a stage run outside the pipeline) falls back to mtimes: stale when one of its
  modules is newer than the result (``code_newer:<modules>``), otherwise reported as ``unrecorded`` (not
  stale, but not proven either: a module edited while that stage was running is invisible to mtimes).
  Not covered: ``reconstruction/``, ``qa/``, ``ar/`` code, and data inputs (photos, cached GLBs, ground truth).
- S9 is exported with ``ar=False`` per product; then ALL exported GLBs go through ONE ``archeck.run`` call
  (views front yaw 0, angled yaw 35, rolled roll 25; the runtime's native room lighting) and each row is
  merged into its S9 result with ``export.attach_archeck`` (which sets ``m1_criterion_1``).
- The previous route's ``candidate.glb`` files are rendered by a second harness call with the same views
  (cached by model sha256 + views under ``runs/<run>/previous_ar``); they only feed the review sheets.
- S10 runs as one ``python -m bsa.gate`` subprocess per product, in parallel. When the S10 result is older
  than S9 only because the AR row was merged (the scored GLB's sha256 is unchanged and S0-S3 are older),
  the gate is re-decided with ``gate.refinalize`` instead of re-rendered.
- S11 (``bsa.look``, ``look_all``) runs after the gate, in-process and one product at a time (each observation
  launches the AR harness twice, ~35 s), for every product that was exported and gated without failure. It is NOT
  in ``core.STAGES`` (core.py is in every stage's code closure: adding it there would mark every recorded stage of
  every run code_changed); ``ALL_STAGES`` = STAGES + s11_look is the orchestrator's chain. Nothing reads S11: its
  final decision is S10's, copied. Drivers: ``none`` (default: observe the unedited export), ``scripted`` (local
  typed plans), ``astra`` (paid; needs --authorize-paid-astra, a cap for the whole command and an owner-named
  ledger ``--look-astra-budget`` shared by every product: re-running the same command after a crash or a kill spends
  what is left of that authorization, never a fresh one; the ledger must live outside
  every s11_look folder). An existing S11 is re-run only when stale/forced/failed, or when an EDITING driver is
  asked for and the recorded one differs (``look_request_reason``); ``none`` never displaces an edited look (a stale
  edited look is kept and reported until an editing driver or --force re-runs it). Every S11 re-run passes
  ``--fresh``: the old session is set aside (``s11_look.superseded-*``), never deleted.
- m1 (the owner-rated record) is never written by accident: ``--run`` is required, and a run that resolves to the
  real m1 folder (compared as folders, not strings: ``M1`` is m1 on NTFS) is refused for EVERY stage unless
  --allow-m1; ``--plan`` stays read-only.

The angled photo is a held-out view: nothing here fits or chooses anything on it (it is only shown and
judged; S3 fits a camera to it and S10 scores through that camera; S11 never copies it).
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .core import AUTOMATION, BSA_DATA, PRODUCTS, STAGES, VIEWS, run_dir, sha256_file, stage_dir

LOOK_STAGE = "s11_look"
ALL_STAGES = STAGES + (LOOK_STAGE,)          # the orchestrator's chain (S11 is not in core.STAGES: see the doc)
MODULES = {"s0_intake": "intake", "s1_generator": "generator", "s2_front": "front", "s3_cameras": "cameras",
           "s4_depth": "depth", "s5_temples": "temples", "s6_assembly": "assemble", "s7_texture": "texture",
           "s8_lens": "lens", "s9_export": "export", "s10_gate": "gate", LOOK_STAGE: "look"}
# What each stage reads, taken from the code (every ``stage_dir``/path read of another stage plus the loaders
# generator.load -> S1, cameras.load_cameras -> S3, depth.load_depth -> S4, donor.hull_views -> S0 + S3;
# ``TestExecutionRule.test_deps_cover_the_reads`` re-derives it). S10 scores S9's GLB, and S6 + S5 arrays when
# nothing is exported; its lens_view_consistency lifts the S2 outline onto S4; its decision reads the S7/S8 fallback
# flags (``gate.STAGE_REVIEW_FLAGS``).
DEPS: dict[str, tuple[str, ...]] = {
    "s0_intake": (),
    "s1_generator": (),
    "s2_front": ("s0_intake",),
    "s3_cameras": ("s0_intake", "s1_generator", "s2_front"),
    "s4_depth": ("s1_generator", "s2_front", "s3_cameras"),
    "s5_temples": ("s0_intake", "s1_generator", "s2_front", "s3_cameras", "s4_depth"),
    "s6_assembly": ("s0_intake", "s1_generator", "s2_front", "s3_cameras", "s4_depth", "s5_temples"),
    "s7_texture": ("s0_intake", "s1_generator", "s2_front", "s3_cameras", "s4_depth", "s5_temples", "s6_assembly"),
    "s8_lens": ("s0_intake", "s1_generator", "s2_front", "s3_cameras", "s5_temples", "s6_assembly"),
    "s9_export": ("s2_front", "s5_temples", "s6_assembly", "s7_texture", "s8_lens"),
    "s10_gate": ("s0_intake", "s1_generator", "s2_front", "s3_cameras", "s4_depth", "s5_temples", "s6_assembly",
                 "s7_texture", "s8_lens", "s9_export"),
    # S11 freezes the S9 GLB, the S10 decision/flags, S8's lens class + colour check and S0's photo boxes
    # (look.prepare_inputs); it reads no camera (its renders are synthetic AR poses, not the S3 photo cameras)
    LOOK_STAGE: ("s0_intake", "s8_lens", "s9_export", "s10_gate"),
}
UPSTREAM = STAGES[:STAGES.index("s9_export")]            # s0 .. s8: run in-process, per product
AR_VIEWS = ({"id": "front", "yaw_degrees": 0}, {"id": "angled", "yaw_degrees": 35}, {"id": "rolled", "roll_degrees": 25})
GATE_TIMEOUT_S = 3600
SHEET_ROW_H = 260
CRIT3_MIN_PRODUCTS = 4


def _log(*a) -> None:
    print(*a, flush=True)


def _short(stage: str) -> str:
    return stage.split("_")[0]


def stage_key(s: str) -> str:
    """'s4' | 's4_depth' -> 's4_depth' (s0..s11)."""
    for st in ALL_STAGES:
        if s == st or s == _short(st):
            return st
    raise ValueError(f"Unknown stage {s!r}; expected one of {[_short(x) for x in ALL_STAGES]}")


# ============================================================================== code provenance
BSA_DIR = Path(__file__).resolve().parent
# S7 (material fit), S8 (mirror calibration), S9 (AR check) and S11 (look observations) choose or judge from renders
# of the ar/ runtime: its code is part of theirs (a pseudo-module ``ar_runtime`` in their fingerprint)
AR_RUNTIME_STAGES = ("s7_texture", "s8_lens", "s9_export", LOOK_STAGE)
AR_DIR = AUTOMATION.parent / "ar"
# S11 also runs reconstruction/ code (the run_astra_job driver, the Astra transport, the canonical lens schema): the
# files bsa.look pins in its own session guard (``look.implementation()``; a test keeps the two lists equal) are a
# pseudo-module ``look_external`` in its fingerprint. The rest of reconstruction/ stays unfingerprinted (below).
LOOK_EXTERNAL_FILES = ("reconstruction/segmented_astra_job.py", "reconstruction/segmented_astra_transport.py",
                       "reconstruction/lens_appearance.py")
# ... plus the helpers of shared modules it pins by bytecode (``look.pinned_functions``: reconstruction/job.py's JSON and
# integrity helpers, segmented_astra_session.digest/read, segmented_providers.pin/verified, texture's GLB helpers)
PSEUDO_MODULES = ("ar_runtime", "look_external")     # fingerprint entries that are not a bsa/<name>.py file
CODE_FILE = "code.json"
CODE_CHECK = True              # the unit tests of the artifact (mtime) rule switch the code rule off
_SHA_CACHE: dict[tuple, str] = {}


def _file_sha(path: Path) -> str:
    st = path.stat()
    key = (str(path), st.st_mtime_ns, st.st_size)
    if key not in _SHA_CACHE:
        _SHA_CACHE[key] = sha256_file(path)
    return _SHA_CACHE[key]


def module_imports(mod: str, root: Path | None = None) -> set[str]:
    """The ``bsa`` modules ``mod`` imports anywhere in its source (module level or inside functions)."""
    root = root or BSA_DIR
    tree = ast.parse((root / f"{mod}.py").read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level == 1:                                   # from . import x / from .x import y
                out.update([node.module.split(".")[0]] if node.module else [a.name.split(".")[0] for a in node.names])
            elif node.level == 0 and node.module == "bsa":        # from bsa import x
                out.update(a.name.split(".")[0] for a in node.names)
            elif node.level == 0 and (node.module or "").startswith("bsa."):
                out.add(node.module.split(".")[1])
        elif isinstance(node, ast.Import):
            out.update(a.name.split(".")[1] for a in node.names if a.name.startswith("bsa."))
    return {m for m in out if (root / f"{m}.py").exists()}


def code_modules(stage: str, root: Path | None = None) -> list[str]:
    """The ``bsa`` modules a stage executes: its own module, everything it imports from ``bsa`` transitively, and
    the package ``__init__`` (the orchestrator itself is excluded: it only calls the stage's ``run``)."""
    root = root or BSA_DIR
    todo, seen = [MODULES[stage]], set()
    while todo:
        m = todo.pop()
        if m in seen or m == "pipeline":
            continue
        seen.add(m)
        todo += sorted(module_imports(m, root) - seen)
    if (root / "__init__.py").exists():
        seen.add("__init__")
    return sorted(seen)


def ar_runtime_files() -> list[Path]:
    """The AR runtime the harness executes: ar/src (TypeScript), ar/package.json, the harness pages and driver
    (ar/qa/provider-comparison*.{mjs,html,js}: the files provider-comparison.mjs snapshots) and the Python side that
    writes the manifest and runs it (automation/qa/provider_comparison*.py). Until 2026-09-25 the harness glob was
    automation/qa/provider-comparison* and matched no file: a harness-page edit never made S7/S8/S9 stale."""
    files = sorted((AR_DIR / "src").rglob("*.ts")) if (AR_DIR / "src").exists() else []
    files += [f for f in (AR_DIR / "package.json",) if f.exists()]
    files += sorted(f for f in (AR_DIR / "qa").glob("provider-comparison*") if f.suffix in (".mjs", ".html", ".js"))
    files += sorted((AUTOMATION / "qa").glob("provider_comparison*.py"))
    return [f for f in files if f.is_file()]


def ar_runtime_digest() -> str:
    """sha256 over (relative path, file sha256) of ``ar_runtime_files`` (mtime-cached)."""
    h = hashlib.sha256()
    for f in ar_runtime_files():
        h.update(str(f.relative_to(AUTOMATION.parent)).replace("\\", "/").encode())
        h.update(_file_sha(f).encode())
    return h.hexdigest()


def look_external_digest() -> str:
    """sha256 over (relative path, file sha256) of ``LOOK_EXTERNAL_FILES`` (mtime-cached) and the bytecode digests of
    ``look.pinned_functions`` (the shared helpers S11 runs, as this process imported them)."""
    from .look import pinned_functions
    h = hashlib.sha256()
    for rel in LOOK_EXTERNAL_FILES:
        f = AUTOMATION / rel
        h.update(rel.encode())
        h.update(_file_sha(f).encode() if f.is_file() else b"missing")
    h.update(json.dumps(pinned_functions(), sort_keys=True).encode())
    return h.hexdigest()


def code_fingerprint(stage: str, root: Path | None = None) -> dict:
    """{stage, code_sha256, modules {name: sha256}} of the stage's code as it is on disk now (for S7/S8/S9/S11 plus
    the ``ar_runtime`` digest, for S11 the ``look_external`` digest, when fingerprinting the real package)."""
    real = root is None or Path(root) == BSA_DIR
    root = root or BSA_DIR
    mods = {m: _file_sha(root / f"{m}.py") for m in code_modules(stage, root)}
    if real and stage in AR_RUNTIME_STAGES:
        mods["ar_runtime"] = ar_runtime_digest()
    if real and stage == LOOK_STAGE:
        mods["look_external"] = look_external_digest()
    return {"stage": stage, "code_sha256": hashlib.sha256(json.dumps(mods, sort_keys=True).encode()).hexdigest(),
            "modules": mods}


def load_code(stages=ALL_STAGES) -> dict[str, dict]:
    """Fingerprint the stages' code, import every stage module, and check nothing changed in between: from here
    on the fingerprints describe the code this process executes (a module is imported once per process)."""
    before = {st: code_fingerprint(st) for st in stages}
    for st in stages:
        importlib.import_module(f"bsa.{MODULES[st]}")
    after = {st: code_fingerprint(st) for st in stages}
    if after != before:
        changed = sorted(st for st in stages if after[st] != before[st])
        raise RuntimeError(f"bsa code changed while it was being imported ({changed}); run again")
    return after


def _read_json(p: Path) -> dict | None:
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    except (OSError, ValueError):
        return None


def record_code(run: str, product: str, stage: str, fp: dict, how: str = "executed") -> None:
    """Write ``<stage>/code.json``: the code that wrote this result, tied to the result's current mtime (a later
    rewrite of the result by anything else invalidates the record)."""
    d = run_dir(run, product) / stage
    res = d / "result.json"
    if not res.exists():
        return
    rec = {**fp, "result_mtime_ns": res.stat().st_mtime_ns, "recorded": time.strftime("%Y-%m-%dT%H:%M:%S"), "how": how}
    tmp = d / (CODE_FILE + ".tmp")
    tmp.write_text(json.dumps(rec, indent=1) + "\n", encoding="utf-8")
    tmp.replace(d / CODE_FILE)


def refresh_code_record(run: str, product: str, stage: str, fp: dict) -> bool:
    """After the pipeline itself rewrote a result with code that is unchanged since the record (S9's AR merge,
    S10's re-decision), move the record to the new mtime. False (record left alone) when the code differs."""
    d = run_dir(run, product) / stage
    rec = _read_json(d / CODE_FILE)
    if not rec or rec.get("code_sha256") != fp["code_sha256"] or not (d / "result.json").exists():
        return False
    rec["result_mtime_ns"] = (d / "result.json").stat().st_mtime_ns
    (d / CODE_FILE).write_text(json.dumps(rec, indent=1) + "\n", encoding="utf-8")
    return True


def code_status(run: str, product: str, stage: str, root: Path | None = None) -> dict:
    """``current`` (recorded code == disk), ``changed`` (recorded code != disk), ``newer`` (no valid record and a
    module is newer than the result), ``unrecorded`` (no valid record, every module older), or ``missing``."""
    root = root or BSA_DIR
    d = run_dir(run, product) / stage
    res = d / "result.json"
    if not res.exists():
        return {"state": "missing"}
    fp = code_fingerprint(stage, root)
    rec = _read_json(d / CODE_FILE)
    own = res.stat().st_mtime_ns
    if rec and rec.get("result_mtime_ns") == own:
        if rec.get("code_sha256") == fp["code_sha256"]:
            return {"state": "current", "how": rec.get("how")}
        old = rec.get("modules") or {}
        return {"state": "changed", "modules": sorted(m for m in set(fp["modules"]) | set(old)
                                                      if fp["modules"].get(m) != old.get(m))}
    newer = sorted(m for m in fp["modules"] if m not in PSEUDO_MODULES and (root / f"{m}.py").stat().st_mtime_ns > own)
    return {"state": "newer", "modules": newer} if newer else {"state": "unrecorded"}


def _code_reason(run: str, product: str, stage: str) -> str | None:
    if not CODE_CHECK:
        return None
    cs = code_status(run, product, stage)
    if cs["state"] == "changed":
        return "code_changed:" + ",".join(cs["modules"])
    if cs["state"] == "newer":
        return "code_newer:" + ",".join(cs["modules"])
    return None


# ============================================================================== staleness
def result_mtime(run: str, product: str, stage: str) -> int | None:
    p = run_dir(run, product) / stage / "result.json"
    return p.stat().st_mtime_ns if p.exists() else None


# stages that record their own failure in result.json (``status: failed``, bsa.look.main): such a result is re-run
FAILED_RESULT_STAGES = (LOOK_STAGE,)


def _failed_reason(run: str, product: str, stage: str) -> str | None:
    if stage not in FAILED_RESULT_STAGES:
        return None
    r = _read_json(run_dir(run, product) / stage / "result.json") or {}
    return "failed_before" if r.get("status") == "failed" else None


def why_run(run: str, product: str, stage: str, *, from_stage: str = "s0_intake", force: bool = False) -> str | None:
    """Reason to execute ``stage`` now, or None to skip it (see the module doc). Several reasons are joined
    with ';' (e.g. ``older_than:s9;code_changed:gate``)."""
    own = result_mtime(run, product, stage)
    if own is None:
        return "missing"
    if force and ALL_STAGES.index(stage) >= ALL_STAGES.index(from_stage):
        return "forced"
    reasons = []
    newer = [d for d in DEPS[stage] if (result_mtime(run, product, d) or 0) > own]
    if newer:
        reasons.append("older_than:" + ",".join(_short(d) for d in newer))
    code = _code_reason(run, product, stage)
    if code:
        reasons.append(code)
    failed = _failed_reason(run, product, stage)
    if failed:
        reasons.append(failed)
    return ";".join(reasons) or None


def plan(run: str, product: str, *, from_stage: str = "s0_intake", to_stage: str = LOOK_STAGE, force: bool = False) -> dict:
    """Which stages would run, and why, if the chain were executed now (a dry run; the actual execution
    re-evaluates after each stage because a re-run stage makes its dependents stale)."""
    out = {}
    simulated: dict[str, int] = {}
    now = time.time_ns()
    for i, st in enumerate(ALL_STAGES[:ALL_STAGES.index(to_stage) + 1]):
        own = result_mtime(run, product, st)
        reason = None
        if own is None:
            reason = "missing"
        elif force and i >= ALL_STAGES.index(from_stage):
            reason = "forced"
        else:
            reasons = []
            newer = [d for d in DEPS[st] if simulated.get(d, result_mtime(run, product, d) or 0) > own]
            if newer:
                reasons.append("older_than:" + ",".join(_short(d) for d in newer))
            code = _code_reason(run, product, st)
            if code:
                reasons.append(code)
            failed = _failed_reason(run, product, st)
            if failed:
                reasons.append(failed)
            reason = ";".join(reasons) or None
        if reason:
            simulated[st] = now + i
        out[st] = reason
    return out


# ============================================================================== stages
def run_stage(product: str, stage: str, run: str, force: bool) -> dict:
    mod = importlib.import_module(f"bsa.{MODULES[stage]}")
    if stage == "s9_export":
        return mod.run(product, run=run, force=force, ar=False)
    return mod.run(product, run=run, force=force)


def run_upstream(product: str, run: str, *, from_stage: str, to_stage: str, force: bool, log=_log,
                 code: dict | None = None) -> list[dict]:
    """S0..min(S8, to_stage) for one product, in order. Returns the execution log. ``code`` = the fingerprints
    of the code this process imported (``load_code``): each executed stage gets its ``code.json``."""
    executed = []
    last = min(ALL_STAGES.index(to_stage), ALL_STAGES.index(UPSTREAM[-1]))
    for st in STAGES[:last + 1]:
        reason = why_run(run, product, st, from_stage=from_stage, force=force)
        if reason is None:
            continue
        t0 = time.time()
        log(f"[pipeline] {product} {st}: run ({reason})")
        try:
            run_stage(product, st, run, force=True if reason != "missing" else False)
        except Exception as e:  # noqa: BLE001 - one product's failure must not stop the other products
            executed.append({"product": product, "stage": st, "reason": reason, "seconds": round(time.time() - t0, 1),
                             "error": f"{type(e).__name__}: {e}"})
            log(f"[pipeline] {product} {st}: FAILED {type(e).__name__}: {e}; the rest of this product's chain is skipped")
            break
        if code:
            record_code(run, product, st, code[st])
        executed.append({"product": product, "stage": st, "reason": reason, "seconds": round(time.time() - t0, 1)})
    return executed


def export_all(products: list[str], run: str, *, from_stage: str, force: bool, log=_log,
               code: dict | None = None) -> tuple[list[dict], list[str]]:
    """S9 export (no AR) for each product that needs it. Returns (execution log, products exported now)."""
    executed, fresh = [], []
    for p in products:
        reason = why_run(run, p, "s9_export", from_stage=from_stage, force=force)
        if reason is None:
            continue
        t0 = time.time()
        log(f"[pipeline] {p} s9_export: export ({reason})")
        try:
            r = run_stage(p, "s9_export", run, force=reason != "missing")
        except Exception as e:  # noqa: BLE001
            r = {"status": f"error: {type(e).__name__}: {e}"}
        if r.get("status") != "exported":
            log(f"[pipeline] {p} s9_export: {r.get('status')} {r.get('missing_upstream', '')}")
        else:
            fresh.append(p)
            if code:
                record_code(run, p, "s9_export", code["s9_export"])
        executed.append({"product": p, "stage": "s9_export", "reason": reason, "seconds": round(time.time() - t0, 1),
                         "status": r.get("status")})
    return executed, fresh


def _ar_current(s9: dict, views=AR_VIEWS) -> bool:
    """The S9 result carries an AR row for the GLB as it is now, with the pipeline's views."""
    ac = s9.get("archeck") or {}
    glb = Path(s9.get("glb", ""))
    return bool(ac.get("model_sha256") and glb.exists() and ac["model_sha256"] == sha256_file(glb)
                and ac.get("views") == [v["id"] for v in views])


def ar_batch_dir(run: str, products: list[str], root: str) -> Path:
    tag = "all" if sorted(products) == sorted(PRODUCTS) else "-".join(sorted(products))
    return BSA_DATA / "runs" / run / root / tag


def ar_check_exports(products: list[str], run: str, *, force: bool = False, log=_log, code: dict | None = None) -> dict | None:
    """One harness call for every exported product whose S9 result lacks a current AR row; merge the rows."""
    from . import archeck, export
    todo = {}
    for p in products:
        sd = stage_dir(run, p, "s9_export")
        if not sd.done():
            continue
        s9 = sd.load()[0]
        if s9.get("status") != "exported":
            continue
        if force or not _ar_current(s9):
            todo[p] = s9
    if not todo:
        return None
    names = {p: f"{p}-bsa-{run}" for p in todo}
    out = ar_batch_dir(run, list(todo), "s9_ar")
    t0 = time.time()
    log(f"[pipeline] AR check of {len(todo)} BSA GLBs in one harness call -> {out}")
    h = archeck.run({names[p]: todo[p]["glb"] for p in todo}, out, ar_views=AR_VIEWS,
                    description=f"BSA {run} exports ({', '.join(todo)}): actual TryOnRenderer, room lighting")
    log(f"[pipeline]   harness {h.get('harness_status')} rc={h.get('returncode')} in {time.time() - t0:.0f} s")
    for p, s9 in todo.items():
        sd = stage_dir(run, p, "s9_export")
        export.attach_archeck(s9, h, names[p], p, run, sheet_dir=sd.root)
        sd.save(s9)
        if code:                                   # the merge is S9 code (export.attach_archeck), unchanged
            refresh_code_record(run, p, "s9_export", code["s9_export"])
    return {"out_dir": str(out), "harness_status": h.get("harness_status"), "returncode": h.get("returncode"),
            "products": list(todo), "seconds": round(time.time() - t0, 1)}


def previous_ar(products: list[str], run: str, *, force: bool = False, log=_log) -> dict:
    """AR renders of the previous route's candidate.glb (same views as BSA), cached by sha256 + views."""
    from . import archeck
    out = BSA_DATA / "runs" / run / "previous_ar"
    cache = out / "archeck.json"
    want = {p: PRODUCTS[p].candidate_glb for p in products if PRODUCTS[p].candidate_glb and Path(PRODUCTS[p].candidate_glb).exists()}
    have = json.loads(cache.read_text()) if cache.exists() else {}
    views_ok = have.get("views") == [v["id"] for v in AR_VIEWS]
    rows = have.get("models", {}) if views_ok else {}
    todo = {p: g for p, g in want.items()
            if force or f"{p}-previous" not in rows or rows[f"{p}-previous"].get("model_sha256") != sha256_file(g)
            or not all(Path(r).exists() for r in rows[f"{p}-previous"].get("renders", []))}
    if todo:
        t0 = time.time()
        sub = out / "batch"
        log(f"[pipeline] AR renders of {len(todo)} previous candidates -> {sub}")
        h = archeck.run({f"{p}-previous": g for p, g in todo.items()}, sub, ar_views=AR_VIEWS,
                        description=f"Previous route candidate.glb ({', '.join(todo)}): actual TryOnRenderer, room lighting")
        # keep the renders of this batch under per-product names so a later partial batch cannot overwrite them
        for p in todo:
            row = dict(h["models"].get(f"{p}-previous", {"status": "not_run"}))
            kept = []
            for r in row.get("renders", []):
                dst = out / Path(r).name
                if Path(r).exists():
                    dst.write_bytes(Path(r).read_bytes())
                    kept.append(str(dst))
            row["renders"] = kept
            row["harness_status"] = h.get("harness_status")
            rows[f"{p}-previous"] = row
        have = {"views": [v["id"] for v in AR_VIEWS], "models": rows, "seconds": round(time.time() - t0, 1)}
        out.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(have, indent=1) + "\n", encoding="utf-8")
    return {p: rows.get(f"{p}-previous", {"status": "no_candidate"}) for p in products}


def _gate_glb_sha(s10: dict) -> str | None:
    prov = (((s10.get("models") or {}).get("bsa") or {}).get("info") or {}).get("provenance") or {}
    return (prov.get("s9_export/model.glb") or {}).get("sha256")


def gate_all(products: list[str], run: str, *, from_stage: str, force: bool, jobs: int = 5, log=_log,
             code: dict | None = None) -> list[dict]:
    """S10 per product: re-decide (refinalize) when only the S9 AR row changed (the reason is exactly
    ``older_than:s9``, so the gate's own code is unchanged), else a full gate subprocess."""
    from . import gate
    executed, full = [], []
    for p in products:
        reason = why_run(run, p, "s10_gate", from_stage=from_stage, force=force)
        if reason is None:
            continue
        glb = run_dir(run, p) / "s9_export" / "model.glb"
        if reason == "older_than:s9" and glb.exists():
            s10 = stage_dir(run, p, "s10_gate").load()[0]
            if _gate_glb_sha(s10) == sha256_file(glb):
                t0 = time.time()
                gate.refinalize(p, run)
                if code:
                    refresh_code_record(run, p, "s10_gate", code["s10_gate"])
                executed.append({"product": p, "stage": "s10_gate", "reason": reason + " (same GLB: refinalize)",
                                 "seconds": round(time.time() - t0, 1)})
                continue
        full.append((p, reason))

    def one(item):
        p, reason = item
        t0 = time.time()
        logf = run_dir(run, p) / "s10_gate.log"
        logf.parent.mkdir(parents=True, exist_ok=True)
        fp = code_fingerprint("s10_gate")          # the subprocess imports the code as it is at launch
        cmd = [sys.executable, "-u", "-m", "bsa.gate", "--product", p, "--run", run, "--force"]
        with open(logf, "w", encoding="utf-8") as fh:
            try:
                rc = subprocess.run(cmd, cwd=str(AUTOMATION), stdout=fh, stderr=subprocess.STDOUT,
                                    timeout=GATE_TIMEOUT_S).returncode
            except subprocess.TimeoutExpired:
                rc = None
        if rc == 0 and code is not None:
            record_code(run, p, "s10_gate", fp)
        return {"product": p, "stage": "s10_gate", "reason": reason, "seconds": round(time.time() - t0, 1),
                "returncode": rc, "log": str(logf)}

    if full:
        log(f"[pipeline] S10 gate for {[p for p, _ in full]} ({min(jobs, len(full))} in parallel)")
        with ThreadPoolExecutor(max_workers=max(1, min(jobs, len(full)))) as ex:
            for r in ex.map(one, full):
                log(f"[pipeline]   {r['product']} s10_gate rc={r['returncode']} in {r['seconds']} s")
                executed.append(r)
    return executed


# ------------------------------------------------------------------------------ S11 look
LOOK_DRIVERS = ("none", "scripted", "astra")          # the manual driver is interactive: run bsa.look directly


def look_options(driver: str = "none", *, script=None, max_turns: int | None = None, authorize_paid_astra: bool = False,
                 astra_maximum_calls: int = 0, astra_budget=None, astra_max_turns: int | None = None, astra_env=None,
                 astra_model: str | None = None, astra_reasoning: str | None = None, allow_m1: bool = False) -> dict:
    """Validated S11 options (ValueError on a contradiction), the same refusals as ``python -m bsa.look``.
    ``script``: one JSON plan list for every product, or a folder of ``<product>.json`` (a product without one is
    skipped). astra: ``authorize_paid_astra``, a cap 1..10 for the WHOLE authorization and ``astra_budget``, the
    owner-named ledger every product session of the command shares. It must live outside every s11_look folder:
    until 2026-09-25 each product had a ledger inside its s11_look, --fresh renamed it away, and a crash or a kill
    followed by the same command spent the cap again (5 products = 5 caps). Each session names its request folders
    uniquely (``look.BsaLookSession.request_folder``), so one ledger serves them all; re-running the command with the
    same ledger spends only what is left of that authorization."""
    if driver not in LOOK_DRIVERS:
        raise ValueError(f"look driver {driver!r}: expected one of {LOOK_DRIVERS} (manual: python -m bsa.look)")
    astra_flags = bool(authorize_paid_astra or astra_maximum_calls or astra_budget or astra_env or astra_max_turns
                       or astra_model or astra_reasoning)
    if driver != "astra" and astra_flags:
        raise ValueError(f"--look-driver {driver} never makes paid calls; drop the Astra flags")
    if driver != "scripted" and (script is not None or max_turns is not None):
        raise ValueError(f"--look-driver {driver} takes neither --look-script nor --look-max-turns")
    if driver == "scripted":
        if script is None or not Path(script).exists():
            raise ValueError("--look-driver scripted needs --look-script (a JSON plan list or a folder of <product>.json)")
        if max_turns is not None and not 1 <= max_turns <= 10:
            raise ValueError("--look-max-turns must be 1..10")
    if driver == "astra":
        from .look import in_stage_folder
        if not (authorize_paid_astra and isinstance(astra_maximum_calls, int) and 1 <= astra_maximum_calls <= 10
                and astra_budget):
            raise ValueError("--look-driver astra needs --authorize-paid-astra, --look-astra-maximum-calls 1..10 (the "
                             "whole authorization, shared by every product) and --look-astra-budget PATH (its ledger)")
        if in_stage_folder(astra_budget):
            raise ValueError("--look-astra-budget must live outside every s11_look folder (--fresh renames it and the "
                             "ledger would start over); e.g. data/bsa/ledgers/<name>.json")
        if astra_max_turns is not None and not 1 <= astra_max_turns <= 10:
            raise ValueError("--look-astra-max-turns must be 1..10")
    return {"driver": driver, "script": None if script is None else str(Path(script).resolve()), "max_turns": max_turns,
            "authorize_paid_astra": bool(authorize_paid_astra), "astra_maximum_calls": astra_maximum_calls,
            "astra_budget": None if astra_budget is None else str(Path(astra_budget).resolve()),
            "astra_max_turns": astra_max_turns, "astra_env": None if astra_env is None else str(astra_env),
            "astra_model": astra_model, "astra_reasoning": astra_reasoning, "allow_m1": bool(allow_m1)}


def look_script(opts: dict, product: str) -> Path | None:
    """The scripted driver's plan file for ``product`` (None: no script for it)."""
    if opts.get("script") is None:
        return None
    s = Path(opts["script"])
    if s.is_dir():
        s = s / f"{product}.json"
    return s if s.is_file() else None


def look_argv(run: str, product: str, opts: dict) -> list[str]:
    """``python -m bsa.look`` arguments for one product (always --fresh: the pipeline re-runs S11 only when the old
    session is stale, failed, forced or from another driver; look.main sets it aside, never deletes it)."""
    argv = ["--run", run, "--product", product, "--driver", opts["driver"], "--fresh"]
    if opts["driver"] == "scripted":
        argv += ["--script", str(look_script(opts, product))]
        if opts.get("max_turns") is not None:
            argv += ["--max-turns", str(opts["max_turns"])]
    elif opts["driver"] == "astra":
        argv += ["--authorize-paid-astra", "--astra-budget", str(opts["astra_budget"]),
                 "--astra-maximum-calls", str(opts["astra_maximum_calls"])]
        for flag, key in (("--astra-max-turns", "astra_max_turns"), ("--astra-env", "astra_env"),
                          ("--astra-model", "astra_model"), ("--astra-reasoning", "astra_reasoning")):
            if opts.get(key) is not None:
                argv += [flag, str(opts[key])]
    if opts.get("allow_m1"):
        argv.append("--allow-m1")
    return argv


def look_request_reason(run: str, product: str, opts: dict) -> str | None:
    """An EDITING driver was asked for and the recorded S11 came from another driver (or another script): re-run.
    ``none`` never displaces an existing look (an edited look stays until it is stale, failed or forced)."""
    drv = opts.get("driver", "none")
    r = _read_json(run_dir(run, product) / LOOK_STAGE / "result.json")
    if drv == "none" or r is None:
        return None
    have = r.get("driver") or {}
    if have.get("name") != drv:
        return f"driver:{have.get('name')}->{drv}"
    if drv == "scripted":
        s = look_script(opts, product)
        rec = ((have.get("client") or {}).get("script") or {}).get("sha256")
        if s is not None and sha256_file(s) != rec:
            return "script_changed"
    return None


def _real_m1(run: str) -> bool:
    """``run`` resolves to the owner-rated m1 folder (``look.is_real_m1``: compared as folders, so ``M1`` or
    ``x/../m1`` count; until 2026-09-25 this was ``run == "m1"``)."""
    from .look import is_real_m1
    return is_real_m1(run)


EDITING_DRIVERS = ("scripted", "manual", "astra")


def kept_edited_look(run: str, product: str, opts: dict, reason: str | None) -> str | None:
    """``none`` never displaces an EDITED look, stale or not: the reason to keep it (None when a re-run may replace
    it: no result, a failed one, --force, or an editing driver asked for). A stale edited look stays in place and
    is reported, until an editing driver or --from s11 --force re-runs it (it may hold paid work)."""
    if opts.get("driver", "none") != "none" or not reason or reason in ("missing", "forced"):
        return None
    r = _read_json(run_dir(run, product) / LOOK_STAGE / "result.json") or {}
    have = (r.get("driver") or {}).get("name")
    if have in EDITING_DRIVERS and r.get("status") != "failed":
        return (f"stale edited look kept ({reason}; driver {have}, {r.get('verdict')}): --look-driver none never "
                f"replaces it; re-run it with an editing driver or --from s11 --force")
    return None


def ledger_left(opts: dict) -> tuple[int, int] | None:
    """(calls used, cap) of the astra ledger (None for another driver). The ledger file is the transport's; a missing
    ledger has used nothing."""
    if opts.get("driver") != "astra":
        return None
    b = _read_json(Path(opts["astra_budget"])) if Path(opts["astra_budget"]).exists() else {}
    if b is None:
        raise ValueError(f"The paid-call ledger {opts['astra_budget']} is unreadable; no request sent")
    if b and b.get("maximum_calls") != opts["astra_maximum_calls"]:
        raise ValueError(f"The paid-call ledger {opts['astra_budget']} records a cap of {b.get('maximum_calls')}, not "
                         f"{opts['astra_maximum_calls']}: a new authorization needs a new ledger")
    return len((b or {}).get("reservations") or []), opts["astra_maximum_calls"]


def look_all(products: list[str], run: str, *, from_stage: str, force: bool, opts: dict | None = None, log=_log,
             code: dict | None = None) -> list[dict]:
    """S11 look for each product that needs it (``why_run`` or ``look_request_reason``), in-process, one product at
    a time. The caller passes only products that were exported and whose S10 did not fail in this invocation; a
    product without an S9 GLB or an S10 result is skipped here too (the look runs after the gate)."""
    from . import look
    opts = opts or look_options()
    executed = []
    for p in dict.fromkeys(products):                  # a product listed twice is looked at once
        reason = why_run(run, p, LOOK_STAGE, from_stage=from_stage, force=force) or look_request_reason(run, p, opts)
        if reason is None:
            continue
        rd = run_dir(run, p)
        skip = None
        try:
            left = ledger_left(opts)
        except ValueError as error:          # an unreadable ledger or another authorization's: nothing is sent
            left, skip = None, str(error)
        if skip:
            pass
        elif _real_m1(run) and not opts.get("allow_m1"):
            skip = "m1 is the owner-rated record (pass --allow-m1)"
        elif not (rd / "s9_export" / "model.glb").exists() or not (rd / "s10_gate" / "result.json").exists():
            skip = "no S9 GLB or no S10 result"
        elif opts["driver"] == "scripted" and look_script(opts, p) is None:
            skip = f"no look script for {p}"
        elif kept_edited_look(run, p, opts, reason):
            skip = kept_edited_look(run, p, opts, reason)
        elif left is not None and left[0] >= left[1]:
            # checked BEFORE --fresh sets the old session aside: an exhausted authorization displaces nothing
            skip = f"paid-call ledger exhausted ({left[0]} of {left[1]} calls used); a new authorization needs a new ledger"
        if skip:
            log(f"[pipeline] {p} {LOOK_STAGE}: skipped ({skip})")
            executed.append({"product": p, "stage": LOOK_STAGE, "reason": reason, "skipped": skip})
            continue
        t0 = time.time()
        log(f"[pipeline] {p} {LOOK_STAGE}: run ({reason}), driver {opts['driver']}")
        row = {"product": p, "stage": LOOK_STAGE, "reason": reason, "driver": opts["driver"]}
        try:
            res = look.main(look_argv(run, p, opts))
        except SystemExit as e:          # an argparse refusal inside bsa.look (look_options should have caught it)
            row["error"] = f"bsa.look refused its arguments (exit {e.code})"
        except Exception as e:  # noqa: BLE001 - one product's failure must not stop the other products
            row["error"] = f"{type(e).__name__}: {e}"[:600]
        else:
            row.update({k: res.get(k) for k in ("status", "verdict", "final_revision", "turns_completed",
                                                "paid_calls_used", "ledger", "final_decision")})
            if code:
                record_code(run, p, LOOK_STAGE, code[LOOK_STAGE])
        row["seconds"] = round(time.time() - t0, 1)
        log(f"[pipeline]   {p} {LOOK_STAGE}: " + (f"FAILED {row['error']}" if "error" in row else
                                                   f"{row['status']} / {row['verdict']} ({row['final_revision']}) "
                                                   f"in {row['seconds']} s"))
        executed.append(row)
    return executed


# ============================================================================== summary
def _load(run: str, product: str, stage: str) -> dict | None:
    p = run_dir(run, product) / stage / "result.json"
    return json.loads(p.read_text()) if p.exists() else None


def _r(x, n=3):
    return None if x is None else round(float(x), n)


TRYON_IDS = ("front", "yaw35", "roll25")      # bsa.look.TRYON_VIEWS, in pipeline.AR_VIEWS' order (same poses)


def look_renders(res: dict) -> list[str]:
    """The try-on renders of the S11 result's final revision (front, yaw 35, roll 25), read through the session's
    pinned records; [] when any link is missing."""
    try:
        state = json.loads((Path(res["session"]) / "state.json").read_text(encoding="utf-8"))
        rev = json.loads(Path(state["revisions"][res["final_revision"]]["path"]).read_text(encoding="utf-8"))
        obs = json.loads(Path(rev["observation"]["path"]).read_text(encoding="utf-8"))
        return [obs["tryon"][v]["path"] for v in TRYON_IDS if v in (obs.get("tryon") or {})]
    except (OSError, KeyError, TypeError, ValueError):
        return []


def look_summary(run: str, product: str, s9_sha: str | None = None) -> dict | None:
    """The S11 row of summary.json (None when the stage never ran)."""
    r = _load(run, product, LOOK_STAGE)
    if r is None:
        return None
    out = {k: r.get(k) for k in ("status", "verdict", "final_revision", "glb", "glb_sha256", "turns_completed",
                                 "paid_calls_used", "ledger", "unreviewed_revision", "final_decision",
                                 "geometry_identical", "contract_ok", "ar_runtime_compatible", "awaiting_plan", "flags",
                                 "error", "seconds")}
    out["driver"] = (r.get("driver") or {}).get("name")
    out["edited"] = bool(r.get("glb_sha256")) and r.get("glb_sha256") != r.get("input_glb_sha256")
    # the look was made on the export as it is now (an older look of an older export is stale, and plan() says so)
    out["of_current_export"] = bool(s9_sha) and r.get("input_glb_sha256") == s9_sha
    out["renders"] = look_renders(r) if r.get("status") != "failed" else []
    return out


def product_summary(product: str, run: str, previous_ar_row: dict | None = None) -> dict:
    """One product's row of summary.json, read from the stage artifacts."""
    s9, s10 = _load(run, product, "s9_export"), _load(run, product, "s10_gate")
    row: dict = {"product": product, "stages_present": [s for s in ALL_STAGES if result_mtime(run, product, s) is not None]}
    row["stale"] = {s: r for s, r in plan(run, product).items() if r and r != "missing"}
    cs = {st: code_status(run, product, st) for st in ALL_STAGES}
    row["code"] = {st: c["state"] + (":" + ",".join(c["modules"]) if c.get("modules") else "") for st, c in cs.items()}
    # S11 is optional (a run --to s10 has none): a missing S11 does not unverify the chain; a present one must be current
    row["code_verified"] = all(c["state"] == "current" for st, c in cs.items()
                               if not (st == LOOK_STAGE and c["state"] == "missing"))
    stage_flags = {}
    for st in ALL_STAGES:
        r = _load(run, product, st)
        if r is None:
            continue
        f = r.get("flags")
        if isinstance(f, list) and f:
            stage_flags[_short(st)] = sorted(set(map(str, f)))
    s5 = _load(run, product, "s5_temples") or {}
    s8 = _load(run, product, "s8_lens") or {}
    row["lens_class"] = s8.get("class")
    row["temples_accepted"] = {s: (s5.get(s) or {}).get("accepted") for s in ("R", "L")}
    if s9 and s9.get("status") == "exported":
        ex, ch, ac = s9["export"], s9["contract"], s9.get("archeck") or {}
        row["glb"] = s9["glb"]
        row["sha256"] = ex["sha256"]
        row["bytes"] = ex["bytes"]
        row["triangles"] = ex["triangles"]
        row["triangles_by_part"] = {n: p["faces_out"] for n, p in ex["parts"].items()}
        row["contract"] = {"ok": ch["ok"], "failures": ch["failures"], "width_m": ch["summary"]["width_m"]}
        row["ar"] = {k: ac.get(k) for k in ("status", "optical_meshes_detected", "lens_mesh_names", "synthetic_fit_ready",
                                             "continuity_failure", "error", "harness_status", "renders")}
    else:
        row["glb"] = None
        row["ar"] = {"status": "not_exported"}
    prev_row = previous_ar_row or {}
    row["previous_ar"] = {k: prev_row.get(k) for k in ("status", "optical_meshes_detected", "continuity_failure", "renders")}
    # c1 comes from S9 itself (contract + the AR harness row); S10 copies it at finalize time
    from .export import m1_criterion_1
    crit = {"c1_contract_ar_lenses": {"pass": m1_criterion_1(s9) if s9 and s9.get("status") == "exported" else False,
                                      "contract_ok": row.get("contract", {}).get("ok"),
                                      "contract_failures": row.get("contract", {}).get("failures"),
                                      "ar_status": row["ar"].get("status"),
                                      "lenses_detected": row["ar"].get("optical_meshes_detected")}}
    if s9 and isinstance(s9.get("archeck"), dict) and "validation" not in s9["archeck"]:
        # an S9 record written before 2026-09-27 carries the harness row but no validation of the run around it:
        # c1 reads unverified (never a silent pass, never a silent failure)
        crit["c1_contract_ar_lenses"]["legacy_unverified"] = True
        crit["c1_contract_ar_lenses"]["note"] = "S9 record predates the harness validation: its run was never validated as a whole; c1 is unverified"
    if s10:
        mc = s10.get("m1_criteria", {})
        b, pv = mc.get("bsa") or {}, mc.get("previous") or {}
        crit["c1_contract_ar_lenses"]["s10_copy"] = (b.get("c1_contract_ar_lenses") or {}).get("pass")
        c2 = b.get("c2_lens_edge_vs_gt") or {}
        gtb = (((s10.get("models") or {}).get("bsa") or {}).get("lens") or {}).get("gt") or {}
        ent = [e for e in gtb.get("entries", []) if e.get("photo") == gtb.get("criterion_photo")]
        crit["c2_lens_edge_vs_gt"] = {"pass": c2.get("pass"), "value_mm": _r(c2.get("value_mm")), "limit_mm": c2.get("limit_mm"),
                                      "photo": c2.get("photo"),
                                      "signed_mean_mm": _r(ent[0]["reference_to_model"].get("signed_mean_mm")) if ent and ent[0].get("reference_to_model") else None,
                                      "all_types_mean_mm": _r(ent[0].get("all_types_symmetric_mean_mm")) if ent else None,
                                      "previous_mm": _r((pv.get("c2_lens_edge_vs_gt") or {}).get("value_mm")),
                                      "applicable": c2.get("applicable", True),
                                      "note": None if c2.get("value_mm") is not None else
                                      (c2.get("reason") or "not evaluated (no value from the gate)")}
        c3 = b.get("c3_heldout_angled_front_piece") or {}
        crit["c3_heldout_angled_front_piece"] = {"pass": c3.get("pass"), "value_pct_w": _r(c3.get("value_pct_w")),
                                                 "previous_pct_w": _r(c3.get("previous_pct_w")),
                                                 "limit_pct_w": _r(c3.get("limit_pct_w")),
                                                 "margin_pct_w": _r(c3.get("margin_pct_w"), 4),
                                                 "temple_sensitivity_pct_w": _r(c3.get("temple_sensitivity_pct_w"), 4),
                                                 "clean": c3.get("clean")}
        c4 = b.get("c4_zero_seam_gaps") or {}
        seam = (((s10.get("models") or {}).get("bsa") or {}).get("seam")) or {}
        crit["c4_zero_seam_gaps"] = {"pass": c4.get("pass"), "gap_pixels": c4.get("gap_pixels"),
                                     "gap_area_mm2": _r(seam.get("gap_area_mm2")),
                                     "previous_gap_pixels": (pv.get("c4_zero_seam_gaps") or {}).get("gap_pixels")}
        row["integrity"] = {"flags": (s10.get("integrity") or {}).get("flags"),
                            "floating": [{k: f[k] for k in ("area_mm2", "centroid_mm")} for f in
                                         ((s10.get("integrity") or {}).get("floating") or {}).get("floating", [])],
                            "roughness_excess_ratio": ((s10.get("integrity") or {}).get("roughness") or {}).get("excess_ratio")}
        row["decision"] = (s10.get("decision") or {}).get("decision")
        row["decision_reasons"] = (s10.get("decision") or {}).get("reasons", [])
        val = s10.get("validation") or {}
        row["gate"] = {"validation_all_pass": val.get("all_pass"),
                       "card_vs_previous_angled": val.get("card_vs_previous_angled"),
                       "scored_glb_sha256": _gate_glb_sha(s10),
                       "scored_current_glb": bool(row.get("sha256")) and _gate_glb_sha(s10) == row.get("sha256"),
                       "bsa_front_pct_w": {v: _r((((s10["models"].get("bsa") or {}).get("views") or {}).get(v, {}).get("front_piece") or {}).get("pct_w"))
                                           for v in VIEWS} if "bsa" in s10.get("models", {}) else None,
                       "previous_front_pct_w": {v: _r((((s10["models"].get("previous") or {}).get("views") or {}).get(v, {}).get("front_piece") or {}).get("pct_w"))
                                                for v in VIEWS} if "previous" in s10.get("models", {}) else None,
                       "phantom_pct": _r(((s10["models"].get("bsa") or {}).get("phantom") or {}).get("pct")) if "bsa" in s10.get("models", {}) else None}
        stage_flags.setdefault("s10", sorted(set(s10.get("flags", []))))
    else:
        row["decision"] = None
        row["decision_reasons"] = ["s10_gate_missing"]
    row["m1_criteria"] = crit
    row["look"] = look_summary(run, product, row.get("sha256"))    # S11: never part of the M1 criteria or the decision
    flags = []
    for st, fl in stage_flags.items():
        flags += [f"{st}:{f}" for f in fl]
    if row["ar"].get("continuity_failure"):
        flags.append("ar:continuity_failure")
    if s10 and not row.get("gate", {}).get("scored_current_glb"):
        flags.append("s10:scored_glb_is_not_the_current_export")
    if s10 and crit["c1_contract_ar_lenses"].get("s10_copy") is not crit["c1_contract_ar_lenses"]["pass"]:
        flags.append("s10:c1_differs_from_s9")
    if row["stale"]:
        flags.append("pipeline:stale_stages:" + ",".join(_short(s) for s in row["stale"]))
    unrec = [_short(st) for st, c in cs.items() if c["state"] == "unrecorded"]
    if unrec:
        flags.append("pipeline:code_unrecorded:" + ",".join(unrec))
    row["flags"] = flags
    row["stage_flags"] = stage_flags
    return row


def aggregate(rows: dict[str, dict]) -> dict:
    """Run-level M1 verdicts (DESIGN.md "M1 pass criteria")."""
    def vals(key):
        return {p: (r.get("m1_criteria", {}).get(key) or {}).get("pass") for p, r in rows.items()}
    c1, c2, c3, c4 = (vals(k) for k in ("c1_contract_ar_lenses", "c2_lens_edge_vs_gt",
                                         "c3_heldout_angled_front_piece", "c4_zero_seam_gaps"))
    n = len(rows)
    all5 = n == len(PRODUCTS)
    # c2: every product whose c2 is applicable must pass; applicable but unevaluated (no S10, a failed measurement)
    # is a failure, not a silent drop. Not applicable = the ground truth has no frame-bounded segment.
    c2_app = {p: v for p, v in c2.items()
              if (rows[p].get("m1_criteria", {}).get("c2_lens_edge_vs_gt") or {}).get("applicable") is not False}

    def verdict(ok: bool):
        """A partial run (fewer than the 5 products) has no run-level verdict."""
        return bool(ok) if all5 else None
    return {
        "products": n,
        "partial_run": None if all5 else f"{n}/{len(PRODUCTS)} products: run-level verdicts are not defined",
        "c1_contract_ar_lenses": {"rule": "5/5 pass contract.check and load in the AR harness with lenses detected",
                                  "passed": sorted(p for p, v in c1.items() if v), "failed": sorted(p for p, v in c1.items() if not v),
                                  "pass": verdict(all(v is True for v in c1.values()))},
        "c2_lens_edge_vs_gt": {"rule": "lens edge vs ground truth <= 0.3 mm mean on frame-bounded segments",
                               "passed": sorted(p for p, v in c2.items() if v is True),
                               "failed": sorted(p for p, v in c2_app.items() if v is not True),
                               "not_measurable": sorted(p for p, v in c2.items() if v is None),
                               "unevaluated_applicable": sorted(p for p, v in c2_app.items() if v is None),
                               "not_applicable": {p: (r.get("m1_criteria", {}).get("c2_lens_edge_vs_gt") or {}).get("note")
                                                  for p, r in rows.items()
                                                  if (r.get("m1_criteria", {}).get("c2_lens_edge_vs_gt") or {}).get("applicable") is False},
                               "pass": verdict(bool(c2_app) and all(v is True for v in c2_app.values()))},
        "c3_heldout_angled_front_piece": {"rule": f"angled front piece <= previous + 0.1 %W on at least {CRIT3_MIN_PRODUCTS}/5",
                                          "passed": sorted(p for p, v in c3.items() if v is True),
                                          "failed": sorted(p for p, v in c3.items() if v is not True),
                                          "clean_passes": sorted(p for p, r in rows.items() if (r.get("m1_criteria", {}).get(
                                              "c3_heldout_angled_front_piece") or {}).get("clean") is True),
                                          "pass": verdict(sum(v is True for v in c3.values()) >= CRIT3_MIN_PRODUCTS)},
        "c4_zero_seam_gaps": {"rule": "zero seam gap pixels", "passed": sorted(p for p, v in c4.items() if v is True),
                              "failed": sorted(p for p, v in c4.items() if v is not True),
                              "pass": verdict(all(v is True for v in c4.values()))},
        "decisions": {p: r.get("decision") for p, r in rows.items()},
    }


def build_summary(run: str, products: list[str] | None = None, executed: list | None = None) -> dict:
    """The summary of every product of the run that has artifacts (not only the ones just executed)."""
    products = products or [p for p in PRODUCTS if run_dir(run, p).exists()]
    cache = BSA_DATA / "runs" / run / "previous_ar" / "archeck.json"
    prev_rows = (json.loads(cache.read_text()).get("models", {}) if cache.exists() else {})
    rows = {}
    for p in products:
        rows[p] = {"run": run, **product_summary(p, run, prev_rows.get(f"{p}-previous"))}
        sheet = run_dir(run, p) / "review.png"
        rows[p]["review_sheet"] = str(sheet) if sheet.exists() else None
    code = {"bsa_modules": {m: _file_sha(BSA_DIR / f"{m}.py")[:16] for m in sorted(
                {m for st in ALL_STAGES for m in code_modules(st)})},
            "ar_runtime": ar_runtime_digest()[:16], "look_external": look_external_digest()[:16],
            "all_stages_verified_current": all(r.get("code_verified") for r in rows.values()),
            "not_current": {p: {st: c for st, c in r.get("code", {}).items() if not c.startswith("current")}
                            for p, r in rows.items() if not r.get("code_verified")}}
    out = {"run": run, "written": time.strftime("%Y-%m-%dT%H:%M:%S"), "code": code, "ar_views": [dict(v) for v in AR_VIEWS],
           "ar_lighting": "runtime native room lighting (no environment override), checker background, shadows on",
           "m1": aggregate(rows), "products": rows}
    if executed is not None:
        out["executed"] = executed
    return out


def save_summary(summary: dict) -> Path:
    path = BSA_DATA / "runs" / summary["run"] / "summary.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")              # concurrent per-product runs of one run name
    tmp.write_text(json.dumps(summary, indent=1, default=float) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


# ============================================================================== review sheet
def _font(size: int):
    for f in ("C:/Windows/Fonts/arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _content_box(paths: list[str], margin: int = 10) -> tuple[int, int, int, int] | None:
    from . import archeck
    paths = [p for p in paths if p and Path(p).exists()]
    return archeck.content_box(paths, margin=margin) if paths else None


def _fit(im: Image.Image, h: int) -> Image.Image:
    return im.resize((max(1, round(im.width * h / im.height)), h), Image.LANCZOS)


def _verdict(c: dict | None) -> str:
    if not c:
        return "n/a"
    return {True: "PASS", False: "FAIL", None: "n/a"}[c.get("pass")]


def review_sheet(product: str, row: dict, out_path: str | Path, *, h: int = SHEET_ROW_H) -> str:
    """Row 1: the 5 photos. Row 2: BSA actual-AR renders. Row 3 (when S11 ran): the S11 look's final revision in
    the same poses (its own harness run: front, yaw 35, roll 25). Last row: previous candidate's actual-AR renders.
    Render columns share one crop box per view (union of every render row's content boxes), so the rows are at the
    same scale."""
    prod = PRODUCTS[product]
    bsa_r = list((row.get("ar") or {}).get("renders") or [])
    prev_r = list((row.get("previous_ar") or {}).get("renders") or [])
    lk = row.get("look") or {}
    look_r = [p for p in (lk.get("renders") or []) if p and Path(p).exists()]
    ncol = max(len(bsa_r), len(prev_r), len(look_r), 1)
    boxes = [_content_box([r[c] for r in (bsa_r, prev_r, look_r) if c < len(r)]) for c in range(ncol)]
    s0 = _load(row.get("run") or "m1", product, "s0_intake") or {}

    def photo(v):
        if not prod.photo_path(v).exists():
            return None
        im = Image.open(prod.photo_path(v)).convert("RGB")
        bb = ((s0.get("views") or {}).get(v) or {}).get("bbox_xyxy")
        if bb:                                   # crop to the S0 matte bbox + 8 % so small glasses stay readable
            x0, y0, x1, y1 = bb
            m = 0.08 * max(x1 - x0, y1 - y0)
            im = im.crop((max(0, int(x0 - m)), max(0, int(y0 - m)), min(im.width, int(x1 + m)), min(im.height, int(y1 + m))))
        return _fit(im, h)
    photos = [photo(v) for v in VIEWS]

    def render_tiles(paths):
        tiles = []
        for c in range(ncol):
            if c < len(paths) and Path(paths[c]).exists() and boxes[c]:
                tiles.append(_fit(Image.open(paths[c]).convert("RGB").crop(boxes[c]), h))
            else:
                tiles.append(None)
        return tiles
    # the photo row may not be much wider than the render rows: scale it down to their width (never up)
    gap = 6
    rw = max(sum((t.width if t else h) + gap for t in render_tiles(r)) for r in (bsa_r, prev_r, look_r))
    pw = sum((t.width if t else h) + gap for t in photos)
    if pw > rw > 0:
        photos = [None if t is None else t.resize((max(1, round(t.width * rw / pw)), max(1, round(t.height * rw / pw))),
                                                  Image.LANCZOS) for t in photos]
    crit = row.get("m1_criteria", {})
    c1, c2, c3, c4 = (crit.get(k) for k in ("c1_contract_ar_lenses", "c2_lens_edge_vs_gt", "c3_heldout_angled_front_piece", "c4_zero_seam_gaps"))
    views = " | ".join(v["id"] for v in AR_VIEWS)
    rows = [("photos:\nfront | back | left |\nright | angled\n(angled = held out)", photos),
            (f"BSA, actual AR\n{views}\nGT edge {(c2 or {}).get('value_mm')} mm\nangled {(c3 or {}).get('value_pct_w')} %W\n"
             f"gaps {(c4 or {}).get('gap_pixels')} px", render_tiles(bsa_r))]
    if look_r:
        rows.append((f"S11 look {lk.get('final_revision')},\nactual AR\ndriver {lk.get('driver')}\n{lk.get('verdict')}\n"
                     + ("edited (materials/lens only)" if lk.get("edited") else "= S9 bytes"), render_tiles(look_r)))
    rows.append((f"previous candidate,\nactual AR\nGT edge {(c2 or {}).get('previous_mm')} mm\nangled {(c3 or {}).get('previous_pct_w')} %W\n"
                 f"gaps {(c4 or {}).get('previous_gap_pixels')} px", render_tiles(prev_r)))
    lw, head = 230, 118 + (19 if lk else 0)
    W = lw + max(sum((t.width if t else h) + gap for t in tiles) for _, tiles in rows)
    heights = [max([t.height for t in tiles if t is not None] + [0]) or h for _, tiles in rows]
    heights = [max(hh, 110) for hh in heights]                 # room for the row label
    sheet = Image.new("RGB", (W, head + sum(hh + 10 for hh in heights)), "white")
    d = ImageDraw.Draw(sheet)
    ar = row.get("ar") or {}
    d.text((10, 8), f"{product}  —  decision {row.get('decision')}   (BSA run {row.get('run', '')})", fill="black", font=_font(24))
    lines = [
        f"c1 contract+AR+lenses {_verdict(c1)}: contract {'ok' if (row.get('contract') or {}).get('ok') else (row.get('contract') or {}).get('failures')}, "
        f"AR {ar.get('status')}, lenses {ar.get('optical_meshes_detected')}; {row.get('triangles')} tris, {(row.get('bytes') or 0) / 1e6:.2f} MB"
        + (f"; continuity: {ar.get('continuity_failure')}" if ar.get("continuity_failure") else ""),
        f"c2 lens edge vs GT {_verdict(c2)}: {(c2 or {}).get('value_mm')} mm (limit 0.3, signed {(c2 or {}).get('signed_mean_mm')}, previous {(c2 or {}).get('previous_mm')})"
        + (f"  [{(c2 or {}).get('note')}]" if (c2 or {}).get("note") else ""),
        f"c3 held-out angled front piece {_verdict(c3)}: {(c3 or {}).get('value_pct_w')} %W vs previous {(c3 or {}).get('previous_pct_w')} (limit {(c3 or {}).get('limit_pct_w')})"
        f"   c4 seam gaps {_verdict(c4)}: {(c4 or {}).get('gap_pixels')} px",
        "reasons: " + ", ".join(row.get("decision_reasons", [])[:6]),
    ]
    if lk:
        lines.append(f"S11 look ({lk.get('driver')}): {lk.get('status')} / {lk.get('verdict')}, revision {lk.get('final_revision')}, "
                     f"{'edited' if lk.get('edited') else 'unedited'}, geometry identical {lk.get('geometry_identical')}, "
                     f"paid calls {lk.get('paid_calls_used')}; final decision {lk.get('final_decision')} (= S10's)"
                     + (f"; flags {', '.join(lk.get('flags') or [])}" if lk.get("flags") else "")
                     + (f"; ERROR {str(lk.get('error'))[:120]}" if lk.get("error") else ""))
    for i, t in enumerate(lines):
        d.text((10, 40 + 19 * i), t, fill="black", font=_font(15))
    for r, (label, tiles) in enumerate(rows):
        y = head + sum(hh + 10 for hh in heights[:r])
        d.multiline_text((8, y + 8), label, fill="black", font=_font(17))
        x = lw
        for t in tiles:
            if t is None:
                d.rectangle([x, y, x + h - 1, y + h - 1], outline="#bbbbbb")
                d.text((x + 10, y + h // 2 - 8), "(none)", fill="#888888", font=_font(15))
                x += h + gap
            else:
                sheet.paste(t, (x, y))
                x += t.width + gap
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_path)
    return str(out_path)


# ============================================================================== driver
def run_pipeline(products: list[str], run: str, *, from_stage: str = "s0_intake", to_stage: str = LOOK_STAGE,
                 force: bool = False, jobs: int = 5, sheets: bool = True, look: dict | None = None,
                 allow_m1: bool = False, log=_log) -> dict:
    """``look``: ``look_options(...)`` for S11 (default: observe-only, driver none). ``run`` has no default and the
    real m1 (the owner-rated record) is refused for every stage unless ``allow_m1`` (until 2026-09-25 the default run
    was m1 and only S11 was guarded: a bare ``--all`` would have rewritten m1's S7-S10)."""
    from_stage, to_stage = stage_key(from_stage), stage_key(to_stage)
    look = look or look_options()
    if _real_m1(run) and not allow_m1:
        raise ValueError(f"run {run!r} is the owner-rated m1 record: the pipeline never writes it unless allow_m1")
    look = {**look, "allow_m1": bool(look.get("allow_m1") or allow_m1)}
    products = list(dict.fromkeys(products))          # a product listed twice runs once
    t0 = time.time()
    code = load_code()                 # import every stage module now: the fingerprints are the executed code
    executed: list[dict] = []
    for p in products:
        executed += run_upstream(p, run, from_stage=from_stage, to_stage=to_stage, force=force, log=log, code=code)
    # a product whose upstream chain failed is NOT exported or gated from mixed-age artifacts: it is blocked
    blocked = sorted({e["product"] for e in executed if e.get("error")})
    if blocked:
        log(f"[pipeline] BLOCKED (an upstream stage failed; no export/AR/gate): {blocked}")
    downstream = [p for p in products if p not in blocked]
    if ALL_STAGES.index(to_stage) >= ALL_STAGES.index("s9_export"):
        ex, _ = export_all(downstream, run, from_stage=from_stage, force=force, log=log, code=code)
        executed += ex
        blocked = sorted(set(blocked) | {e["product"] for e in ex if e.get("status") != "exported"})
        downstream = [p for p in downstream if p not in blocked]
        ar_info = ar_check_exports(downstream, run, log=log, code=code)
        if ar_info:
            executed.append({"stage": "s9_ar_check", **ar_info})
        previous_ar(products, run, log=log)
    if ALL_STAGES.index(to_stage) >= ALL_STAGES.index("s10_gate"):
        gated = gate_all(downstream, run, from_stage=from_stage, force=force, jobs=jobs, log=log, code=code)
        executed += gated
        # the look runs after the gate: a product whose S10 failed now is not looked at (its S10 result is stale)
        gate_failed = sorted({e["product"] for e in gated if "returncode" in e and e["returncode"] != 0})
        if gate_failed:
            log(f"[pipeline] S10 failed for {gate_failed}: no S11 look for them")
        if ALL_STAGES.index(to_stage) >= ALL_STAGES.index(LOOK_STAGE):
            executed += look_all([p for p in downstream if p not in gate_failed], run, from_stage=from_stage,
                                 force=force, opts=look, log=log, code=code)
    drift = sorted({m for st in ALL_STAGES for m, h in code_fingerprint(st)["modules"].items() if code[st]["modules"].get(m) != h})
    if drift:
        log(f"[pipeline] WARNING: bsa modules changed on disk during this run: {drift}; the stages that ran with the "
            f"old code are marked code_changed and will re-run")
    summary = build_summary(run, None, executed)
    summary["code_changed_during_run"] = drift
    if look.get("driver") == "astra":                  # the whole authorization, superseded sessions included
        from .look import ledger_usage
        summary["look_ledger"] = ledger_usage(look["astra_budget"])
    failed = sorted({e["product"] for e in executed if e.get("product") and (
        e.get("error") or (e.get("stage") == "s10_gate" and "returncode" in e and e["returncode"] != 0))})
    summary["blocked_products"] = blocked
    summary["failed_products"] = failed
    for p in blocked:
        if p in summary["products"]:
            summary["products"][p]["decision"] = "BLOCKED"
            summary["products"][p].setdefault("decision_reasons", []).insert(0, "pipeline:upstream_stage_failed")
    if sheets:
        for p in products:
            row = summary["products"].get(p)
            if row is not None:
                row["review_sheet"] = review_sheet(p, row, run_dir(run, p) / "review.png")
    summary["seconds"] = round(time.time() - t0, 1)
    save_summary(summary)
    log(f"[pipeline] done in {time.time() - t0:.0f} s; M1: " + json.dumps({k: v.get("pass") for k, v in summary["m1"].items()
                                                                             if isinstance(v, dict) and "pass" in v})
        + ("" if summary["m1"].get("partial_run") is None else f" ({summary['m1']['partial_run']}); per product: "
           + json.dumps({p: {k: (r.get("m1_criteria", {}).get(k) or {}).get("pass") for k in r.get("m1_criteria", {})}
                         for p, r in summary["products"].items()})))
    looks = {p: f"{(r.get('look') or {}).get('status')}/{(r.get('look') or {}).get('verdict')}"
             for p, r in summary["products"].items() if r.get("look")}
    if looks:
        log(f"[pipeline] S11 look: {json.dumps(looks)}")
    return summary


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="BSA pipeline: S0-S10, AR check, gate, S11 look, summary and review sheets")
    ap.add_argument("--run", required=True, help="the run folder under data/bsa/runs (m1 = the owner-rated record: "
                                                 "refused unless --allow-m1)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--product", choices=list(PRODUCTS), action="append")
    g.add_argument("--all", action="store_true")
    ap.add_argument("--from", dest="from_stage", default="s0", help="first stage --force applies to (s0..s11)")
    ap.add_argument("--to", dest="to_stage", default="s11", help="last stage to run (s0..s11)")
    ap.add_argument("--force", action="store_true", help="recompute the stages at or after --from")
    ap.add_argument("--jobs", type=int, default=5, help="parallel S10 gate processes")
    ap.add_argument("--plan", action="store_true", help="print what would run and exit")
    ap.add_argument("--no-sheets", action="store_true")
    lg = ap.add_argument_group("S11 look (bsa.look; the manual driver is interactive: run python -m bsa.look)")
    lg.add_argument("--look-driver", choices=LOOK_DRIVERS, default="none",
                    help="none = observe the unedited export (default); scripted = local typed plans; astra = PAID")
    lg.add_argument("--look-script", type=Path, help="scripted: a JSON plan list, or a folder of <product>.json")
    lg.add_argument("--look-max-turns", type=int, help="scripted: turn ceiling 1..10 (bsa.look's default otherwise)")
    lg.add_argument("--authorize-paid-astra", action="store_true", help="astra: explicitly enable paid Astra requests")
    lg.add_argument("--look-astra-maximum-calls", type=int, default=0,
                    help="astra: paid-call cap 1..10 for the WHOLE authorization (every product of the command)")
    lg.add_argument("--look-astra-budget", type=Path,
                    help="astra: the authorization's ledger (owner-named, outside every s11_look folder); re-running "
                         "the command with the same ledger spends only what is left")
    lg.add_argument("--look-astra-max-turns", type=int, help="astra: turns per product 1..10 (bsa.look's default otherwise)")
    lg.add_argument("--look-astra-env", type=Path, help="astra: explicit dotenv file holding the credential")
    lg.add_argument("--look-astra-model", help="astra: model id (bsa.look's default otherwise)")
    lg.add_argument("--look-astra-reasoning", help="astra: reasoning effort (bsa.look's default otherwise)")
    ap.add_argument("--allow-m1", "--look-allow-m1", dest="allow_m1", action="store_true",
                    help="let the pipeline (every stage, S11 included) write into the real m1 run (owner-rated record)")
    a = ap.parse_args(argv)
    products = list(PRODUCTS) if a.all else list(dict.fromkeys(a.product))
    try:
        look = look_options(a.look_driver, script=a.look_script, max_turns=a.look_max_turns,
                            authorize_paid_astra=a.authorize_paid_astra, astra_maximum_calls=a.look_astra_maximum_calls,
                            astra_budget=a.look_astra_budget,
                            astra_max_turns=a.look_astra_max_turns, astra_env=a.look_astra_env,
                            astra_model=a.look_astra_model, astra_reasoning=a.look_astra_reasoning,
                            allow_m1=a.allow_m1)
        from_stage, to_stage = stage_key(a.from_stage), stage_key(a.to_stage)
    except ValueError as e:
        ap.error(str(e))
    real_m1 = _real_m1(a.run)
    if a.plan:
        if real_m1 and not a.allow_m1:
            print(f"run {a.run!r} is the owner-rated m1 record: a run would be refused (--allow-m1); plan only")
        looking = ALL_STAGES.index(to_stage) >= ALL_STAGES.index(LOOK_STAGE)
        paid = 0
        for p in products:
            pl = {k: v for k, v in plan(a.run, p, from_stage=from_stage, to_stage=to_stage, force=a.force).items() if v}
            req = look_request_reason(a.run, p, look) if looking else None
            if req and not pl.get(LOOK_STAGE):
                pl[LOOK_STAGE] = req
            if pl.get(LOOK_STAGE):
                kept = kept_edited_look(a.run, p, look, pl[LOOK_STAGE])
                if kept:
                    pl[LOOK_STAGE] += f" (will be skipped: {kept})"
                elif look["driver"] == "astra":
                    paid += 1
            print(p, pl)
            print("   code:", {_short(st): (c["state"] + (":" + ",".join(c["modules"]) if c.get("modules") else ""))
                               for st in ALL_STAGES for c in [code_status(a.run, p, st)]})
        if look["driver"] == "astra" and looking:
            try:
                used, cap = ledger_left(look)
            except ValueError as e:
                print(f"S11 astra: {e}; no paid call would be made")
            else:
                turns = look.get("astra_max_turns") or 3
                print(f"S11 astra: ledger {look['astra_budget']}: {used} of {cap} calls used; this command would make "
                      f"at most {min(cap - used, paid * turns)} paid call(s) ({paid} product session(s), <= {turns} "
                      f"each)")
        return
    if real_m1 and not a.allow_m1:
        ap.error(f"run {a.run!r} is the owner-rated m1 record: the pipeline never writes it (--allow-m1 to override)")
    summary = run_pipeline(products, a.run, from_stage=from_stage, to_stage=to_stage, force=a.force, jobs=a.jobs,
                           sheets=not a.no_sheets, look=look, allow_m1=a.allow_m1)
    if summary.get("blocked_products") or summary.get("failed_products"):
        # a stage failed: the shell (and any caller) must see it
        sys.exit(1)


if __name__ == "__main__":
    main()
