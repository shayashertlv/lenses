"""python -m modeler.agentic: doctor / start / resume / status / cancel / demo / owner-review / owner-verdict / reconcile-unknown / rebuild.

Exit codes (new, this route only): 0 normal completion of the requested workflow (delivered or unresolved alike);
2 invalid CLI or configuration; 3 actionable execution, setup or unknown-outcome failure (needs_attention/failed);
4 budget exhausted; 130 cancelled. The stop reason wins over an attached fallback asset. A job waiting for the owner's
review (awaiting_owner) exits 0: the workflow did what it could until the owner decides (owner-review).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys

from ..paths import AUTOMATION, BLENDER_DIR
from . import PACKAGE_PROTOCOL
# DEFAULT_COMPACT_THRESHOLD_TOKENS: build_policy's threshold when --compact-threshold-tokens is not given; the prune default is
# resolved below it before build_policy runs
from .config import (CRITICS, DEFAULT_COMPACT_THRESHOLD_TOKENS, DRIVERS, EXIT_BUDGET, EXIT_CANCELLED, EXIT_FAILED, EXIT_INVALID, EXIT_OK, FINALS,
                     INTAKE_NOT_WIRED, INTAKES, PRUNE_MIN_TOKENS, WORKERS,
                     ConfigError, build_policy, editable_modules, owner_allowance, owner_text, translate_request)
from .executor import DockerWorker, FakeWorker, NativeFixtureWorker, WorkerError, docker_doctor, native_doctor, validate_worker_config, worker_config_fingerprint
from .responses import CACHE_MODES, HttpTransport, RefusingTransport, ScriptedTransport, canonical
from .state import LeaseError, StateError, Store, TERMINAL_STATES

# New jobs cache the growing conversation (responses.apply_cache_breakpoint: explicit_rolling; test-pilot-002 replayed
# under it costs $3.33 for the author instead of $6.84) and open an image prune epoch (runner.maybe_prune_images) this
# many tokens below the compaction threshold, where a job without a verified compact bound stops. Projected from run 2's
# own sizes (26,967-token first message, 3,740 text + 12,398 image tokens per build reply, 2,399 replayed reply tokens per
# turn), a 15-operation run with 12 edits stops at request 11 without pruning and completes with one prune epoch at
# 160,000; lower thresholds prune more often (120k: 3 epochs, 60k: 9), cost slightly less ($5.9 / $5.3 against $6.0
# for 13 author requests) and keep fewer earlier revisions in view. 40,000 is two edit turns of growth.
DEFAULT_CACHE_MODE = "explicit_rolling"
PRUNE_HEADROOM_TOKENS = 40_000
SEED_REVISION_RE = re.compile(r"^(?P<job>.+):(?P<rid>r\d{4})$")

EXIT_BY_STATE = {"delivered": EXIT_OK, "unresolved": EXIT_OK, "budget_exhausted": EXIT_BUDGET, "cancelled": EXIT_CANCELLED, "failed": EXIT_FAILED,
                 "needs_attention": EXIT_FAILED, "awaiting_owner": EXIT_OK}


def _out(obj: dict) -> None:
    print(json.dumps(obj, indent=1, default=str))


def source_fingerprints() -> dict:
    """Hashes of the code a job depends on: this package, the modeler modules it adapts, the Blender harness and library."""
    files = sorted(p for p in (Path(__file__).parent.rglob("*.py")))
    files += sorted((Path(__file__).parents[1]).glob("*.py"))
    files += [BLENDER_DIR / "harness.py", BLENDER_DIR / "glasses_lib.py", Path(__file__).with_name("AUTHOR_PROMPT.md")]
    for extra in (AUTOMATION / "bsa" / "contract.py", AUTOMATION / "bsa" / "export.py", AUTOMATION / "bsa" / "archeck.py"):
        files.append(extra)
    digests = {}
    for p in files:
        if p.is_file():
            digests[p.relative_to(AUTOMATION).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    ar_src = AUTOMATION.parent / "ar" / "src"
    ar = {}
    if ar_src.is_dir():
        for p in sorted(ar_src.rglob("*.ts")):
            ar[p.relative_to(ar_src).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return {"protocol": PACKAGE_PROTOCOL, "python_sources": digests, "python_sources_sha256": hashlib.sha256(json.dumps(digests, sort_keys=True).encode()).hexdigest(),
            "ar_runtime_sources_sha256": hashlib.sha256(json.dumps(ar, sort_keys=True).encode()).hexdigest(), "ar_runtime_files": len(ar)}


def load_script(path: Path) -> tuple[ScriptedTransport, dict]:
    """The scripted driver's file {steps: [...], fake_scenario?: {...}}: its transport and the synthetic worker's scenario
    (numeric keys as ints; an absent scenario is {}, which FakeWorker treats as none)."""
    script = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(script, dict) or not isinstance(script.get("steps"), list):
        raise ConfigError("the script is {steps: [...], fake_scenario?: {...}}")
    fake_scenario = {int(k) if str(k).isdigit() else k: v for k, v in (script.get("fake_scenario") or {}).items()}
    return ScriptedTransport(script["steps"]), fake_scenario


def load_worker_config(path: Path | None) -> dict | None:
    if path is None:
        return None
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    return validate_worker_config(cfg)


def make_worker(kind: str, *, worker_config: dict | None, fake_scenario: dict | None = None, fixture_hashes: set[str] | None = None):
    if kind == "fake":
        return FakeWorker(fake_scenario)
    if kind == "native-fixture":
        if not fixture_hashes:
            raise ConfigError("--worker native-fixture needs --fixture-program-sha256 (the allow-listed program set hashes); it is a test-only worker")
        return NativeFixtureWorker(fixture_hashes)
    if worker_config is None:
        raise ConfigError("--worker docker needs --worker-config PATH (the real built image digest and a passing doctor record)")
    return DockerWorker(worker_config)


def load_credential(env_file: Path | None, var: str) -> str:
    """The credential from an explicit dotenv file or the environment; never printed, never stored."""
    secret = None
    if env_file is not None:
        from dotenv import dotenv_values
        secret = dotenv_values(env_file).get(var)
    if not secret:
        secret = os.environ.get(var)
    if not secret:
        raise ConfigError(f"no credential in the explicit source ({var}); nothing was sent")
    return secret


def cmd_doctor(args) -> int:
    report = {"python": sys.version.split()[0], "node": None, "blender_native": native_doctor(), "docker": None, "ar_harness": None, "paid_mode": "not checked (doctor never reads a credential)"}
    try:
        import subprocess
        p = subprocess.run(["node", "--version"], capture_output=True, text=True, timeout=30)
        report["node"] = (p.stdout or "").strip() or None
    except Exception as e:  # noqa: BLE001
        report["node"] = f"unavailable: {type(e).__name__}"
    cfg = None
    if args.worker_config:
        try:
            cfg = load_worker_config(args.worker_config)
        except (WorkerError, OSError, ValueError) as e:
            report["docker"] = {"config_error": str(e)}
    if report["docker"] is None:
        report["docker"] = docker_doctor(cfg)
    qa = AUTOMATION.parent / "ar" / "qa" / "provider-comparison.mjs"
    report["ar_harness"] = {"present": qa.is_file(), "node_modules": (AUTOMATION.parent / "ar" / "node_modules").is_dir(),
                            "note": "the actual AR renderer smoke is exercised by tests/test_bsa_contract.py::ArCheckTest, not by doctor"}
    report["generated_code_execution"] = ("docker worker: allowed only after doctor --self-test passes for this config" if not report["docker"].get("blocking")
                                          else f"BLOCKED: {report['docker']['blocking']}")
    report["native_fixture_tests"] = "allowed (fixed audited fixtures only; never generated code)" if report["blender_native"].get("version") else "blocked: no host Blender"
    if args.self_test:
        report["self_test"] = self_test(args, cfg)
    _out(report)
    return EXIT_OK if not args.self_test or report["self_test"].get("passed") else EXIT_FAILED


def self_test(args, cfg: dict | None) -> dict:
    """The mutating worker self-test into a fresh output folder: known fixtures and negative controls in the isolated
    worker; writes the doctor record into a COPY of the config in the output folder (never over the owner's file)."""
    out = Path(args.output) if args.output else None
    if out is None or (out.exists() and any(out.iterdir())):
        return {"passed": False, "reason": "--self-test needs --output NEW_DIR (fresh)"}
    out.mkdir(parents=True, exist_ok=True)
    if cfg is None:
        return {"passed": False, "reason": "no valid --worker-config; build the image and write its real digest first"}
    doc = docker_doctor(cfg)
    if doc.get("blocking") and any("doctor self-test" not in b for b in doc["blocking"]):
        return {"passed": False, "reason": "environment blocks the self-test", "blocking": doc["blocking"]}
    from .selftest import run_self_test
    return run_self_test(cfg, out)


def intake_choice(value: str) -> str:
    """--intake: 'astra' was offered until 2026-09-29 but could never run on this route; argparse refuses it with the reason."""
    if value == "astra":
        raise argparse.ArgumentTypeError(INTAKE_NOT_WIRED)
    return value


def _common_session_args(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--driver", choices=DRIVERS, default="scripted")
    ap.add_argument("--script", type=Path, help="scripted driver: JSON {steps: [...], fake_scenario: {...}} (see demo.write_demo_script)")
    ap.add_argument("--worker", choices=WORKERS, default="fake")
    ap.add_argument("--worker-config", type=Path)
    ap.add_argument("--fixture-program-sha256", action="append", default=[], help="native-fixture worker: allowed program set hash (repeatable; tests only)")
    ap.add_argument("--allow-paid", action="store_true")
    ap.add_argument("--budget-usd", type=str)
    ap.add_argument("--max-inference-requests", type=int)
    ap.add_argument("--max-output-tokens", type=int)
    ap.add_argument("--max-revisions", type=int)
    ap.add_argument("--max-worker-seconds", type=int)
    ap.add_argument("--wall-minutes", type=int)
    ap.add_argument("--images-per-request", type=int)
    ap.add_argument("--compact-threshold-tokens", type=int)
    ap.add_argument("--compact-output-bound-tokens", type=int, help="paid compaction is refused unless the compact endpoint's output bound is verified and given here")
    ap.add_argument("--effort", default="high", choices=("low", "medium", "high", "xhigh", "max"))
    ap.add_argument("--intake", type=intake_choice, choices=INTAKES, default="code",
                    help="code (measured, default), scripted (saved vision answers, offline), synthetic (fake worker only) or none; "
                         "the paid astra intake is not wired into this route")
    ap.add_argument("--intake-script", type=Path, help="scripted intake answers {reading, review}")
    ap.add_argument("--critic", choices=CRITICS, default="none")
    ap.add_argument("--final-evaluator", choices=FINALS, default="none")
    ap.add_argument("--no-ar", action="store_true")
    ap.add_argument("--env", type=Path, help="dotenv file holding the credential (paid mode; never printed)")
    ap.add_argument("--api-key-env", default="OPENAI_API_KEY")
    ap.add_argument("--region", default="global", choices=("global", "us", "eu"))
    ap.add_argument("--seed-program", type=Path, help="explicit import of a previous program folder as revision r0001 (recorded as contamination/seed)")
    ap.add_argument("--seed-revision", help="JOB_DIR:rNNNN: import that revision's sealed program (its program set sha256 verified against its build "
                                            "bundle) as r0001, built and observed before the first author turn")
    ap.add_argument("--owner-instruction", help="the owner's own request for this job, shown to the author verbatim in the first message")
    ap.add_argument("--editable-modules", help="comma list of program modules edit_program may change (default: all); needs a seeded revision")
    ap.add_argument("--cache-mode", choices=CACHE_MODES, default=DEFAULT_CACHE_MODE, help="prompt caching of the author conversation (default: explicit_rolling)")
    ap.add_argument("--image-prune-tokens", type=int, help="open an image prune epoch when an author request counted this many input tokens "
                                                           f"(default: the compaction threshold minus {PRUNE_HEADROOM_TOKENS}; 0: never)")
    ap.add_argument("--no-owner-review", action="store_true", help="deliver on the author's request_delivery (the pre-2026-09-28 behaviour) instead of "
                                                                     "waiting for the owner's live review (awaiting_owner)")
    ap.add_argument("--review-resume-token-limit", type=int, help="owner review: a change request after an author request of this many input tokens "
                                                                   "starts a new seeded job instead (default 150,000)")
    ap.add_argument("--final-on-accept", action="store_true", help="owner review: run the configured --final-evaluator on the accepted candidate (off by default)")


def image_prune_tokens(value, compact_threshold_tokens) -> int | None:
    """--image-prune-tokens as the CLI hands it to config.build_policy: not given, the default PRUNE_HEADROOM_TOKENS below
    the compaction threshold build_policy will use (DEFAULT_COMPACT_THRESHOLD_TOKENS when --compact-threshold-tokens is not
    given), never below config.PRUNE_MIN_TOKENS; given, the value unchanged (build_policy validates it after the
    threshold and stores 0 as None, never)."""
    if value is not None:
        return value
    if compact_threshold_tokens is None or isinstance(compact_threshold_tokens, bool) or not isinstance(compact_threshold_tokens, int):
        compact_threshold_tokens = DEFAULT_COMPACT_THRESHOLD_TOKENS     # build_policy refuses a bad threshold itself
    return max(PRUNE_MIN_TOKENS, compact_threshold_tokens - PRUNE_HEADROOM_TOKENS)


def seed_from_revision(spec: str) -> dict:
    """--seed-revision JOB_DIR:rNNNN: the revision's sealed program, read without writing to the source job (rebuild.read_source
    copies its database) and verified module by module against the revision row and its build bundle (rebuild.sealed_program:
    every module's sha256 and the program set sha256). Returns what Session.create's ``seed`` takes."""
    from .rebuild import RebuildError, read_source, sealed_program
    m = SEED_REVISION_RE.match(str(spec or ""))
    if not m:
        raise ConfigError("--seed-revision is JOB_DIR:rNNNN (for example data/modeler/agentic/test-pilot-002:r0006)")
    job_dir, rid = Path(m.group("job")), m.group("rid")
    try:
        src = read_source(job_dir, rid)
        sealed = sealed_program(job_dir, src["revision"])
    except RebuildError as e:
        raise ConfigError(f"--seed-revision {spec}: {e}") from None
    rev = src["revision"]
    provenance = {"source_job": str(job_dir.resolve()), "source_revision": rid, "source_program_set_sha256": rev["program_set_sha256"],
                  "source_glb_sha256": rev.get("glb_sha256"), "source_build_operation": sealed["operation_id"],
                  "source_bundle_lib_sha256": sealed["bundle_lib_sha256"], "source_product_id": (src["job"].get("request") or {}).get("product_id"),
                  "verified": "every module's sha256 and the program set sha256 against the revision row and its build bundle"}
    return {"modules": sealed["modules"], "source": f"{job_dir.resolve()}:{rid}", "provenance": provenance}


def build_session_parts(args, *, request_path: Path, output: Path):
    """Validate everything before reading a credential or touching the network; returns what Session.create needs."""
    raw = json.loads(Path(request_path).read_text(encoding="utf-8"))
    translated = translate_request(raw, Path(request_path).parent)
    # config.build_policy is the one place a new job's policy is made and validated (cache mode, the prune threshold, the
    # owner-revision mode: the instruction verbatim, blank = none, at most 8,000 characters, and the editable modules that
    # tools.edit_program enforces). The CLI resolves only its own prune default, below the threshold build_policy will use;
    # the policy is stored exactly as build_policy returns it.
    policy = build_policy(driver=args.driver, worker=args.worker, allow_paid=args.allow_paid, budget_usd=args.budget_usd, max_inference_requests=args.max_inference_requests,
                          max_output_tokens=args.max_output_tokens, max_revisions=args.max_revisions, max_worker_seconds=args.max_worker_seconds,
                          wall_minutes=args.wall_minutes, images_per_request=args.images_per_request, reasoning_effort=args.effort,
                          cache_mode=getattr(args, "cache_mode", None) or DEFAULT_CACHE_MODE,
                          compact_threshold_tokens=args.compact_threshold_tokens, compact_output_bound_tokens=args.compact_output_bound_tokens, ar=not args.no_ar,
                          intake=args.intake, critic=args.critic, final_evaluator=args.final_evaluator, region=args.region,
                          image_prune_tokens=image_prune_tokens(getattr(args, "image_prune_tokens", None), args.compact_threshold_tokens),
                          owner_instruction=getattr(args, "owner_instruction", None), editable_modules=getattr(args, "editable_modules", None),
                          owner_review=not getattr(args, "no_owner_review", False), review_resume_token_limit=getattr(args, "review_resume_token_limit", None),
                          final_on_accept=bool(getattr(args, "final_on_accept", False)))
    seed_revision = getattr(args, "seed_revision", None)
    if seed_revision and getattr(args, "seed_program", None):
        raise ConfigError("give --seed-revision or --seed-program, not both")
    if policy["editable_modules"] is not None and not (seed_revision or getattr(args, "seed_program", None)):
        raise ConfigError("--editable-modules needs a seeded revision (--seed-revision or --seed-program): a fresh program writes every module")
    seed = seed_from_revision(seed_revision) if seed_revision else None
    if output.exists() and any(output.iterdir()):
        raise ConfigError(f"--output {output} is not empty; choose a fresh folder (never overwrite a job)")
    worker_config = load_worker_config(args.worker_config) if args.worker_config else None
    if policy["worker"] == "docker":
        doc = docker_doctor(worker_config)
        if doc.get("blocking"):
            raise ConfigError("the Docker worker doctor blocks: " + "; ".join(doc["blocking"]))
    transport, fake_scenario = None, None
    if policy["driver"] == "scripted":
        if not args.script:
            raise ConfigError("--driver scripted needs --script")
        transport, fake_scenario = load_script(args.script)
    # measured evidence (intake code / scripted) with the synthetic worker is allowed (the worker never sees paths); the reverse is refused in the runner
    if policy["intake"] == "synthetic" and policy["worker"] != "fake":
        raise ConfigError("--intake synthetic is only for the synthetic worker")
    worker = make_worker(policy["worker"], worker_config=worker_config, fake_scenario=fake_scenario, fixture_hashes=set(args.fixture_program_sha256))
    intake_drivers = None
    if policy["intake"] == "scripted":
        if not args.intake_script:
            raise ConfigError("--intake scripted needs --intake-script")
        from ..intake_astra import ScriptedIntake
        d = ScriptedIntake(json.loads(Path(args.intake_script).read_text(encoding="utf-8")))
        intake_drivers = (d, d)
    if transport is None:
        credential = load_credential(args.env, args.api_key_env)     # only after every validation above passed
        transport = HttpTransport(credential)
    if seed is not None:
        translated = dict(translated, notes=list(translated["notes"]) + [f"seed revision {seed['source']} (program set {seed['provenance']['source_program_set_sha256'][:16]})"])
    return translated, policy, worker, transport, worker_config, intake_drivers, seed


def cmd_start(args) -> int:
    from .runner import Session
    output = Path(args.output)
    try:
        translated, policy, worker, transport, worker_config, intake_drivers, seed = build_session_parts(args, request_path=args.request, output=output)
    except (ConfigError, WorkerError, OSError, ValueError) as e:
        print(f"invalid: {e}", file=sys.stderr)
        return EXIT_INVALID
    for n in translated["notes"]:
        print(f"[translation] {n}")
    try:
        session = Session.create(output, translated=translated, policy=policy, fingerprints=source_fingerprints(), worker=worker, transport=transport,
                                 worker_config=worker_config, intake_drivers=intake_drivers, seed_program=args.seed_program, seed=seed)
    except StateError as e:
        print(f"invalid: {e}", file=sys.stderr)
        return EXIT_INVALID
    except Exception as e:  # noqa: BLE001
        print(f"failed to initialize: {type(e).__name__}: {e}", file=sys.stderr)
        return EXIT_FAILED
    state = run_session(session)
    return finish(session.store, state)


def run_session(session) -> str:
    """Drive the session. A lease or transition refusal is a durable stop (another runner owns the job, or the state
    machine refused a step): reported with the job's current state (exit 3 unless terminal), never as a traceback."""
    try:
        return session.run()
    except StateError as e:
        print(f"stopped: {type(e).__name__}: {e}", file=sys.stderr)
        try:
            session.store.event("runner_stopped", error=f"{type(e).__name__}: {e}")
        except Exception:  # noqa: BLE001
            pass
        return session.store.state()


def finish(store: Store, state: str) -> int:
    from .runner import status_report
    rep = status_report(store)
    out = {"state": state, "stop_reason": rep["stop_reason"], "deliverable_status": rep["deliverable_status"], "selected_revision": rep["selected_revision"],
           "budget": {k: rep["budget"][k] for k in ("cap_usd", "settled_usd", "unknown_liability_usd", "held_usd", "operations_used", "operations_cap")},
           "exit_code": EXIT_BY_STATE.get(state, EXIT_FAILED)}
    cand = (rep.get("owner_review") or {}).get("candidate")
    if state == "awaiting_owner" and cand:
        out["awaiting_your_review"] = review_hint(store, cand)
    if (rep.get("owner_review") or {}).get("lineage"):
        out["lineage"] = rep["owner_review"]["lineage"]
    _out(out)
    return EXIT_BY_STATE.get(state, EXIT_FAILED)


def review_hint(store: Store, cand: dict) -> dict:
    """What the owner does next with a job in awaiting_owner: the candidate, the try-on link and the three decisions."""
    job = str(store.job_dir)
    return {"round": cand["round"], "revision": cand["revision"], "asset": (cand.get("asset") or {}).get("path"),
            "try_on": (cand.get("tryon") or {}).get("link"), "try_on_server": (cand.get("tryon") or {}).get("server"),
            "accept": f'python -m modeler.agentic owner-review --job "{job}" --accept --authorized-by "<you>"',
            "changes": (f'python -m modeler.agentic owner-review --job "{job}" --changes "<your words>" --authorized-by "<you>" '
                        "--max-inference-requests N --budget-usd USD [--editable-modules materials,lenses]"),
            "stop": f'python -m modeler.agentic owner-review --job "{job}" --stop --authorized-by "<you>"'}


def open_store(job: Path) -> Store:
    try:
        return Store.open(Path(job))
    except StateError as e:
        raise ConfigError(str(e)) from None


def cmd_resume(args) -> int:
    from .runner import Session
    try:
        store = open_store(args.job)
    except ConfigError as e:
        print(f"invalid: {e}", file=sys.stderr)
        return EXIT_INVALID
    job = store.job()
    if job["state"] in TERMINAL_STATES:
        if store.setting("owner_calibration_pending"):
            # the owner's decision ended the job but the calibration file refused its row: written here, under a lease, once
            code = flush_pending_calibration(store)
            if code != EXIT_OK:
                return code
        print(f"the job is {job['state']}; nothing to resume")
        return finish(store, job["state"])
    waiting = job["state"] == "awaiting_owner" or (job["state"] == "needs_attention" and not args.acknowledge_attention)
    if waiting and store.setting("owner_calibration_pending"):
        # a change round's calibration row the file refused: written here as on a terminal job (under the lease, once), before
        # anything else is checked; the wait itself is left as it is
        code = flush_pending_calibration(store)
        if code != EXIT_OK:
            return code
    if job["state"] == "awaiting_owner":
        # a durable wait: nothing is sent and nothing runs until the owner decides (owner-review); no --script or credential is read
        print("the job is awaiting your review (owner-review --accept | --changes TEXT | --stop); nothing to resume", file=sys.stderr)
        return finish(store, job["state"])
    if job["state"] == "needs_attention" and not args.acknowledge_attention:
        # checked before the transport and worker are built: telling the owner to look first needs no --script or credential
        print(f"the job needs attention ({job['stop_reason']}); inspect `status --json`, then resume with --acknowledge-attention to continue", file=sys.stderr)
        return finish(store, job["state"])
    policy = job["policy"]
    finishing_accept = (job["state"] == "evaluating" and store.setting("owner_accept_intent")
                        and not (policy.get("final_on_accept") and policy.get("final_evaluator", "none") != "none"))
    try:
        if finishing_accept:
            # an interrupted acceptance without the final evaluator sends nothing and builds nothing: no --script, no credential, no doctor
            transport, worker, pinned, repin = RefusingTransport(), None, job.get("worker_config"), None
        else:
            transport, worker, pinned, repin = session_io(args, store, job, verb="resuming")
    except (ConfigError, WorkerError, OSError, ValueError) as e:
        print(f"invalid: {e}", file=sys.stderr)
        return EXIT_INVALID
    session = Session(store, transport=transport, worker=worker)
    try:
        session.acquire()
    except LeaseError as e:
        print(f"cannot resume: {e}", file=sys.stderr)
        return EXIT_FAILED
    if repin is not None:
        repin_worker_config(store, pinned, repin)
    if job["state"] == "needs_attention":
        store.transition("ready", "owner acknowledged the attention stop")
    state = run_session(session)
    return finish(store, state)


def flush_pending_calibration(store: Store) -> int:
    """Write the calibration rows an owner decision left pending (setting 'owner_calibration_pending') on a terminal or a
    waiting job (awaiting_owner, needs_attention), under the job's lease; idempotent (Session.append_calibration never
    appends a row already in the file)."""
    from .runner import Session
    session = Session(store, transport=RefusingTransport(), worker=None)
    try:
        session.acquire()
    except LeaseError as e:
        print(f"cannot write the pending calibration rows: {e}", file=sys.stderr)
        return EXIT_FAILED
    try:
        written = session.flush_owner_calibration()
    finally:
        session.release()
    left = len(store.setting("owner_calibration_pending") or [])
    print(f"wrote {len(written)} pending calibration row(s)" + (f"; {left} still pending (the file refused them; resume again)" if left else ""))
    return EXIT_OK if not left else EXIT_FAILED


def session_io(args, store: Store, job: dict, *, verb: str) -> tuple:
    """(transport, worker, pinned worker config, config to re-pin or None) for continuing an existing job: the scripted steps
    (``--script``) or the paid transport (credential read last), and the job's worker with the doctor re-run (resume and a
    continuing owner-review share it)."""
    policy = job["policy"]
    if policy["driver"] == "scripted":
        if not getattr(args, "script", None):
            raise ConfigError(f"{verb} a scripted job needs --script (the remaining steps)")
        transport, fake_scenario = load_script(args.script)
    else:
        if not policy.get("allow_paid"):
            raise ConfigError("paid job without allow_paid in its policy")
        transport = None        # the credential is read last, after the worker checks below
        fake_scenario = None
    pinned = job.get("worker_config")
    run_config, repin = pinned, None
    if policy["worker"] == "docker":
        # the doctor runs on every resume as on start. A harness or library change since the pinned doctor pass blocks the pinned
        # copy (its record is bound to the old library); a NEW config whose own doctor pass matches the current fingerprint re-pins
        # the job (INF-06: until 2026-09-28 a given config was only compared and then ignored, so a paused job was stranded)
        given = load_worker_config(args.worker_config) if getattr(args, "worker_config", None) else None
        if given is not None and (pinned is None or canonical(given) != canonical(pinned)):
            doc = docker_doctor(given)
            if doc.get("blocking"):
                raise ConfigError("the new --worker-config does not pass the doctor: " + "; ".join(doc["blocking"]))
            run_config, repin = given, given
        else:
            doc = docker_doctor(pinned)
            if doc.get("blocking"):
                raise ConfigError("the Docker worker doctor blocks the job's pinned worker config: " + "; ".join(doc["blocking"]) +
                                  " (after a library or harness change: run doctor --self-test into a fresh folder and resume with --worker-config "
                                  "<that folder's passed config>; the switch is recorded)")
    worker = make_worker(policy["worker"], worker_config=run_config, fake_scenario=fake_scenario, fixture_hashes=set(getattr(args, "fixture_program_sha256", None) or []))
    if transport is None:
        transport = HttpTransport(load_credential(getattr(args, "env", None), getattr(args, "api_key_env", "OPENAI_API_KEY")))
    return transport, worker, pinned, repin


def repin_worker_config(store: Store, old: dict | None, new: dict) -> dict:
    """Pin ``new`` as the job's worker config, with provenance: both image digests and doctor fingerprints, the library and
    harness it binds now, and the revisions built before the switch (built with the library of the old pin)."""
    from ..paths import BLENDER_DIR
    old = old or {}
    entry = {"utc": store.now(), "from_image_digest": old.get("image_digest"), "to_image_digest": new.get("image_digest"),
             "from_doctor_fingerprint": (old.get("doctor") or {}).get("fingerprint"), "to_doctor_fingerprint": (new.get("doctor") or {}).get("fingerprint"),
             "to_fingerprint": worker_config_fingerprint(new),
             "library_sha256": {n: hashlib.sha256((BLENDER_DIR / n).read_bytes()).hexdigest() for n in ("glasses_lib.py", "harness.py")},
             "revisions_before": [r["id"] for r in store.revisions()],
             "note": "revisions listed in revisions_before were built with the library of the previous pin; later revisions use this one"}
    with store.tx():
        store.update_job(worker_config_json=new)
        store.set_setting("worker_config_history", list(store.setting("worker_config_history") or []) + [entry])
        store.event("worker_config_repinned", **entry)
    return entry


def cmd_status(args) -> int:
    from .runner import status_report
    try:
        store = Store.open(Path(args.job), readonly=True)
    except StateError as e:
        print(f"invalid: {e}", file=sys.stderr)
        return EXIT_INVALID
    rep = status_report(store)
    if args.json:
        _out(rep)
    else:
        print(f"{rep['job']}: {rep['state']} ({rep['stop_reason']}); revisions {len(rep['revisions'])}; selected {rep['selected_revision']}; "
              f"budget settled {rep['budget']['settled_usd']} + unknown {rep['budget']['unknown_liability_usd']} + held {rep['budget']['held_usd']} of {rep['budget']['cap_usd']} USD; "
              f"operations {rep['budget']['operations_used']}/{rep['budget']['operations_cap']}; deliverable {rep['deliverable_status']}")
        orv = rep.get("owner_review") or {}
        cand = orv.get("candidate")
        if cand:
            hint = review_hint(store, cand)
            print(f"awaiting your review: round {cand['round']}, {cand['revision']}"
                  + (f" ({cand['asset']['path']})" if cand.get("asset") else " (synthetic: no GLB)") + (f"\n  try it on: {hint['try_on']}" if hint["try_on"] else "")
                  + (f"\n  serve it: {hint['try_on_server']}" if hint["try_on_server"] else "")
                  + f"\n  accept:  {hint['accept']}\n  changes: {hint['changes']}\n  stop:    {hint['stop']}")
        for r in orv.get("rounds") or []:
            print(f"round {r['round']}: {r['revision']} {r['decision'] or 'awaiting'}" + (f": {r['text']}" if r.get("text") else ""))
        if orv.get("lineage"):
            print(f"lineage: {json.dumps(orv['lineage'], default=str)}")
        if rep.get("owner_calibration_pending"):
            print(f"{rep['owner_calibration_pending']} calibration row(s) pending (the file refused them): "
                  f'python -m modeler.agentic resume --job "{store.job_dir}" writes them')
        if rep["state"] == "evaluating" and (rep.get("settings") or {}).get("owner_accept_intent"):
            print(f'your acceptance was interrupted: python -m modeler.agentic resume --job "{store.job_dir}" finishes it')
    return EXIT_OK


def cmd_cancel(args) -> int:
    try:
        store = open_store(args.job)
    except ConfigError as e:
        print(f"invalid: {e}", file=sys.stderr)
        return EXIT_INVALID
    job = store.job()
    if job["state"] in TERMINAL_STATES:
        print(f"the job is already {job['state']}")
        return EXIT_OK
    store.update_job(cancel_requested=1)
    store.event("cancel_requested", by="cli")
    from .state import parse_iso
    live = job["lease_holder"] and job["lease_expires_utc"] and parse_iso(job["lease_expires_utc"]) > store.clock()
    if live:
        print(f"cancellation recorded; the live runner {job['lease_holder']} will stop at its next step")
        return EXIT_OK
    # no live runner: take the lease (a runner whose lease expired mid-tool may still be alive; the new fence refuses every write it attempts),
    # stop owned workers and mark cancelled here
    try:
        fence = store.acquire_lease("cli-cancel", 60)
        store.event("cancel_fenced", fence=fence)
    except LeaseError as e:
        print(f"cannot cancel: {e}", file=sys.stderr)
        return EXIT_FAILED
    open_round = store.open_owner_round()
    if open_round is not None:
        store.decide_owner_round(open_round["round"], "cancelled", {"reason": "the job was cancelled while the candidate awaited the owner's review"})
    for op in store.operations(state="running"):
        ident = op.get("worker") or {}
        if ident.get("kind") == "docker" and job.get("worker_config"):
            try:
                DockerWorker(job["worker_config"]).stop(ident)
            except Exception:  # noqa: BLE001
                pass
        store.update_operation(op["id"], state="cancelled", result_json={"error": "cancelled by the owner", "category": "cancelled"}, completed_utc=store.now())
    try:
        store.transition("cancelled", "owner cancellation (no live runner)", stop_reason="cancelled")
    except StateError:
        store.transition("ready", "cancel path")
        store.transition("cancelled", "owner cancellation (no live runner)", stop_reason="cancelled")
    store.release_lease("cli-cancel", fence)
    print("cancelled")
    return EXIT_CANCELLED


def cmd_reconcile_unknown(args) -> int:
    """Owner-authorized settlement of one unknown inference outcome from the provider dashboard. The reservation is
    settled (verified usage, or an explicit no-charge statement) and the request row closed in one transaction; the
    request is never re-posted. `resume --acknowledge-attention` then continues the job with a new request."""
    from .budget import Budget, BudgetError
    try:
        store = open_store(args.job)
    except ConfigError as e:
        print(f"invalid: {e}", file=sys.stderr)
        return EXIT_INVALID
    req = store.request(args.request)
    res = store.reservation(req["reservation_id"]) if req and req.get("reservation_id") else None
    # the liability lives on the RESERVATION: a timeout leaves the request 'unknown', a 5xx leaves it 'failed', a 200 without a usage
    # block 'completed'; each keeps its reservation 'unknown' until it is reconciled here
    liable = (res is not None and res["state"] == "unknown") or (res is None and req is not None and req["state"] == "unknown")
    if req is None or not liable:
        detail = "missing" if req is None else f"request {req['state']}, reservation {res['state'] if res else 'none'}"
        print(f"invalid: {args.request} has no unknown liability to reconcile ({detail})", file=sys.stderr)
        return EXIT_INVALID
    if bool(args.no_charge) == bool(args.usage_json):
        print("invalid: give exactly one of --no-charge or --usage-json FILE (the usage block from the provider dashboard)", file=sys.stderr)
        return EXIT_INVALID
    usage = json.loads(Path(args.usage_json).read_text(encoding="utf-8")) if args.usage_json else None
    amount = 0
    try:
        with store.tx():
            if req.get("reservation_id"):
                amount = Budget(store).reconcile_unknown(req["reservation_id"], authorized_by=args.authorized_by, usage=usage, no_charge=bool(args.no_charge))
            note = f"unknown outcome reconciled by {args.authorized_by}: " + ("no charge" if args.no_charge else f"{amount} micro-USD settled from the dashboard")
            if req["state"] == "unknown":
                store.update_request(req["id"], state="failed", completed_utc=store.now(), error=note)
            else:       # the HTTP outcome stays on the row (5xx, a 200 without usage); the reconciliation is appended to its error text
                store.update_request(req["id"], error=f"{req.get('error') or ''} | {note}".strip(" |"))
            store.event("request_reconciled", request=req["id"], authorized_by=args.authorized_by, settled_micro=amount)
    except (BudgetError, StateError, ValueError) as e:
        print(f"invalid: {e}", file=sys.stderr)
        return EXIT_INVALID
    _out({"request": req["id"], "settled_micro": amount, "state": store.state(),
          "note": "the request is closed and never re-posted; `resume --acknowledge-attention` continues the job"})
    return EXIT_OK


def cmd_demo(args) -> int:
    from .demo import demo_request, write_demo_script
    from .runner import Session
    output = Path(args.output)
    if output.exists() and any(output.iterdir()):
        print(f"invalid: --output {output} is not empty", file=sys.stderr)
        return EXIT_INVALID
    output.mkdir(parents=True, exist_ok=True)
    inputs = output.parent / (output.name + ".inputs")
    request = demo_request(inputs)
    req_path = inputs / "request.json"
    req_path.write_text(json.dumps(request, indent=1), encoding="utf-8")
    script_path = write_demo_script(inputs / "script.json", request["photos"])
    ns = argparse.Namespace(driver="scripted", script=script_path, worker=args.worker, worker_config=args.worker_config, fixture_program_sha256=[], allow_paid=False,
                            budget_usd="5", max_inference_requests=12, max_output_tokens=4000, max_revisions=4, max_worker_seconds=120, wall_minutes=30,
                            images_per_request=6, compact_threshold_tokens=None, compact_output_bound_tokens=None, effort="high", intake="synthetic" if args.worker == "fake" else "code",
                            intake_script=None, critic="scripted", final_evaluator="scripted", no_ar=args.worker == "fake", env=None, api_key_env="OPENAI_API_KEY", region="global",
                            seed_program=None, seed_revision=None, owner_instruction=None, editable_modules=None, cache_mode=DEFAULT_CACHE_MODE, image_prune_tokens=None,
                            no_owner_review=True, review_resume_token_limit=None, final_on_accept=False)
    try:
        translated, policy, worker, transport, worker_config, intake_drivers, _seed = build_session_parts(ns, request_path=req_path, output=output)
    except (ConfigError, WorkerError, ValueError) as e:
        print(f"invalid: {e}", file=sys.stderr)
        return EXIT_INVALID
    session = Session.create(output, translated=translated, policy=policy, fingerprints=source_fingerprints(), worker=worker, transport=transport, worker_config=worker_config)
    state = run_session(session)
    if getattr(transport, "steps", None):
        # a synthetic revision never reaches the final evaluator, so the demo script's report_evaluation step stays unconsumed: said, not hidden
        session.store.event("script_steps_unconsumed", remaining=len(transport.steps), endpoints=[s.get("endpoint") for s in transport.steps])
        print(f"note: {len(transport.steps)} scripted step(s) were not consumed (the synthetic revision skips the final evaluator)")
    print("DEMO: synthetic inputs and worker; the manifest's deliverable_status says synthetic_only and no model.glb exists" if args.worker == "fake" else "DEMO with a real worker")
    return finish(session.store, state)


def cmd_owner_review(args) -> int:
    """The owner's decision on the candidate a job in awaiting_owner holds: --accept (the job delivers exactly those bytes),
    --changes TEXT (the words verbatim to the same author conversation with a new allowance: --max-inference-requests and
    --budget-usd, optional --editable-modules locks; the run then continues like resume unless --no-continue; a conversation
    above the policy's review_resume_token_limit continues in a new seeded job instead) or --stop (unresolved, the candidate
    kept). Every decision is appended to the owner verdicts with the asset hash and the related measurements."""
    from .runner import InferenceUnknownOutstanding, RunnerError, Session
    decisions = [bool(args.accept), args.changes is not None, bool(args.stop)]
    if sum(decisions) != 1:
        print("invalid: give exactly one of --accept, --changes TEXT or --stop", file=sys.stderr)
        return EXIT_INVALID
    try:
        store = open_store(args.job)
    except ConfigError as e:
        print(f"invalid: {e}", file=sys.stderr)
        return EXIT_INVALID
    job = store.job()
    session = None
    try:
        if job["state"] != "awaiting_owner":
            accept = store.setting("owner_accept_intent")
            if job["state"] == "evaluating" and accept:
                raise ConfigError(f"the job is evaluating: your acceptance of round {accept.get('round')} was interrupted and is recorded; finish it "
                                  f'with: python -m modeler.agentic resume --job "{store.job_dir}" (nothing is decided again; nothing awaits your review)')
            pending = len(store.setting("owner_calibration_pending") or [])
            raise ConfigError(f"the job is {job['state']} ({job['stop_reason']}); nothing awaits your review"
                              + (f" ({pending} calibration row(s) of your decision pending: python -m modeler.agentic resume --job \"{store.job_dir}\" "
                                 "writes them)" if pending else ""))
        if not str(args.authorized_by or "").strip():
            raise ConfigError("--authorized-by names who decided (your name)")
        allowance = editable = text = None
        changes = args.changes is not None
        if changes:
            text = owner_text(args.changes)
            # a round's caps are what the job has committed (settled + unknown + held; operations used) plus the grant: the
            # job ceilings are checked against that (Session.check_allowance re-checks them for every caller)
            from .budget import Budget
            committed = Budget(store).totals()
            allowance = owner_allowance(budget_usd=args.budget_usd, max_inference_requests=args.max_inference_requests,
                                        current_cap_micro=int(committed["upper_bound_micro"]), current_operation_cap=int(committed["operations_used"]))
            editable = editable_modules(args.editable_modules) if args.editable_modules else None
            for name, v, lo, hi in (("--max-revisions", args.max_revisions, 1, 40), ("--wall-minutes", args.wall_minutes, 1, 60 * 24)):
                if v is not None and not lo <= int(v) <= hi:
                    raise ConfigError(f"{name} must be in {lo}..{hi}")
        else:
            given = [n for n, v in (("--budget-usd", args.budget_usd), ("--max-inference-requests", args.max_inference_requests),
                                    ("--editable-modules", args.editable_modules), ("--max-revisions", args.max_revisions), ("--wall-minutes", args.wall_minutes)) if v is not None]
            if given:
                raise ConfigError(f"{', '.join(given)} belong to a change round (--changes)")
        policy = job["policy"]
        run_final = bool(args.accept) and bool(policy.get("final_on_accept")) and policy.get("final_evaluator", "none") != "none"
        if run_final or (changes and not args.no_continue):
            # the final evaluator sends a request; a continuing change round runs the author and the worker: as resume builds them
            transport, worker, pinned, repin = session_io(args, store, job, verb="continuing")
        else:
            transport, worker, pinned, repin = RefusingTransport(), None, job.get("worker_config"), None
        session = Session(store, transport=transport, worker=worker)
        try:
            session.acquire()
        except LeaseError as e:
            print(f"cannot decide: {e}", file=sys.stderr)
            return EXIT_FAILED
        cal = Path(args.calibration_file) if args.calibration_file else None
        if args.accept:
            out = session.owner_accept(authorized_by=args.authorized_by, medium=args.medium, note=args.note, calibration_file=cal)
        elif args.stop:
            out = session.owner_stop(authorized_by=args.authorized_by, note=args.note, medium=args.medium, calibration_file=cal)
        else:
            too_large, size, limit = session.conversation_too_large()
            if too_large:
                from .runner import same_path
                round_no = store.open_owner_round()["round"]
                # a continuation a crash left unlinked (its intent names the folder) is finished in that folder, never made twice
                intent = store.setting("owner_continuation_intent") or {}
                pending = intent.get("output") if intent.get("round") == round_no else None
                failed_before = bool(intent.get("round") == round_no and intent.get("failed_attempts"))
                if pending and session.failed_continuation(Path(pending), store.open_owner_round()) is not None:
                    pending, failed_before = None, True     # that attempt failed to initialize: recorded by the session, never reused
                if args.new_job_output:
                    output = Path(args.new_job_output)
                elif pending:
                    output = Path(pending)
                else:
                    # the default folder, or (after an attempt that failed to initialize, kept for inspection) the next free one
                    default = store.job_dir.parent / f"{store.job_dir.name}-continued-{round_no}"
                    output, k = default, 1
                    while failed_before and output.exists() and k < 100:
                        k += 1
                        output = default.parent / f"{default.name}-{k}"
                resuming = pending is not None and same_path(pending, output)
                if not resuming and output.exists() and (not output.is_dir() or any(output.iterdir())):
                    raise ConfigError(f"the continuation job folder {output} is not empty; give --new-job-output NEW_DIR")
                print(f"[owner] the conversation had {size} input tokens (limit {limit}): the change request continues in the new job {output}")
                if worker is None:
                    # --no-continue: nothing is sent, but the new job builds its seed before it waits: the job's own worker, doctor re-run
                    worker = make_worker(policy["worker"], worker_config=job.get("worker_config"), fake_scenario=None,
                                         fixture_hashes=set(args.fixture_program_sha256 or []))
                    if policy["worker"] == "docker" and docker_doctor(job.get("worker_config")).get("blocking"):
                        raise ConfigError("the Docker worker doctor blocks the job's pinned worker config; the new job's seed cannot be built")
                new = session.owner_continue_in_new_job(output, text=text, allowance=allowance, authorized_by=args.authorized_by, editable=editable,
                                                        worker=worker, transport=transport, worker_config=(repin or pinned),
                                                        fingerprints=source_fingerprints(), add_revisions=args.max_revisions, note=args.note, medium=args.medium,
                                                        calibration_file=cal)
                session.release()
                if args.no_continue:
                    new.release()
                    print(f"the new job {new.store.job_dir} is ready; continue it with resume")
                    code = finish(new.store, new.store.state())
                    # the requested outcome: the new job waits in 'ready' for resume (as the same-conversation --no-continue path)
                    return EXIT_OK if new.store.state() == "ready" else code
                state = run_session(new)
                return finish(new.store, state)
            out = session.owner_request_changes(text=text, allowance=allowance, authorized_by=args.authorized_by, editable=editable,
                                                add_revisions=args.max_revisions, wall_minutes=args.wall_minutes, note=args.note, medium=args.medium,
                                                calibration_file=cal)
            if not args.no_continue:
                if repin is not None:
                    repin_worker_config(store, pinned, repin)
                print(f"[owner] round {out['round']}: your words are in the conversation; continuing")
                state = run_session(session)
                return finish(store, state)
    except (ConfigError, RunnerError, InferenceUnknownOutstanding, WorkerError, StateError, OSError, ValueError) as e:
        if session is not None:
            try:
                session.release()
            except Exception:  # noqa: BLE001
                pass
        print(f"invalid: {e}", file=sys.stderr)
        return EXIT_INVALID
    session.release()
    _out({"decision": out})
    code = finish(store, store.state())
    # --no-continue: the change round is recorded and the job waits in 'ready' for resume; that is the requested outcome
    return EXIT_OK if store.state() == "ready" else code


def cmd_owner_verdict(args) -> int:
    from .evaluation import record_owner_verdict
    try:
        store = open_store(args.job)
        row = record_owner_verdict(store, verdict=args.verdict, sha256=args.sha256, medium=args.medium, note=args.note)
    except (ConfigError, ValueError) as e:
        print(f"invalid: {e}", file=sys.stderr)
        return EXIT_INVALID
    _out({"recorded": row["record"], "when": row["created_utc"]})
    return EXIT_OK


def cmd_rebuild(args) -> int:
    """No-inference rebuild of one revision's sealed program with the current library into a fresh preview folder. The
    source job is only read (its database through a copy); the build runs in the same worker as a normal build, so the
    Docker worker needs a doctor record bound to the CURRENT library and harness (a library change invalidates it)."""
    from .rebuild import RebuildError, rebuild_revision
    output = Path(args.output)
    try:
        if output.exists() and (not output.is_dir() or any(output.iterdir())):
            raise ConfigError(f"--output {output} is not empty; choose a fresh folder (a rebuild never overwrites)")
        worker_config = None
        if args.worker == "docker":
            if not args.worker_config:
                raise ConfigError("rebuild with --worker docker needs --worker-config PATH (a config whose doctor self-test passed for this library)")
            worker_config = load_worker_config(args.worker_config)
            doc = docker_doctor(worker_config)
            if doc.get("blocking"):
                raise ConfigError("the Docker worker doctor blocks: " + "; ".join(doc["blocking"]))
        worker = make_worker(args.worker, worker_config=worker_config)
        manifest = rebuild_revision(Path(args.job), args.revision, output, worker=worker, worker_config=worker_config, fingerprints=source_fingerprints(),
                                    max_worker_seconds=args.max_worker_seconds)
    except (ConfigError, RebuildError, WorkerError, OSError, ValueError) as e:
        print(f"invalid: {e}", file=sys.stderr)
        return EXIT_INVALID
    _out({"label": manifest["label"], "asset": manifest["asset"], "built": (manifest.get("build") or {}).get("built"),
          "compatible": (manifest.get("compatibility") or {}).get("compatible"), "library_changed": manifest["library"]["changed"],
          "audit_flags": ((manifest.get("export") or {}).get("audit") or {}).get("flags"), "manifest": str(output / "manifest.json")})
    if manifest["synthetic"]:
        return EXIT_OK if (manifest.get("build") or {}).get("built") else EXIT_FAILED
    return EXIT_OK if manifest["asset"] else EXIT_FAILED


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m modeler.agentic", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    d = sub.add_parser("doctor", help="read-only environment report; --self-test runs the mutating worker self-test into --output NEW_DIR")
    d.add_argument("--worker-config", type=Path)
    d.add_argument("--self-test", action="store_true")
    d.add_argument("--output", type=Path)
    d.set_defaults(fn=cmd_doctor)
    s = sub.add_parser("start", help="create and run a fresh job")
    s.add_argument("--request", type=Path, required=True)
    s.add_argument("--output", type=Path, required=True)
    _common_session_args(s)
    s.set_defaults(fn=cmd_start)
    r = sub.add_parser("resume", help="reconcile and continue an existing job (never resets caps, never re-posts unknown inference)")
    r.add_argument("--job", type=Path, required=True)
    r.add_argument("--script", type=Path)
    r.add_argument("--worker-config", type=Path)
    r.add_argument("--fixture-program-sha256", action="append", default=[])
    r.add_argument("--env", type=Path)
    r.add_argument("--api-key-env", default="OPENAI_API_KEY")
    r.add_argument("--acknowledge-attention", action="store_true", help="continue a job stopped in needs_attention after inspecting it")
    r.set_defaults(fn=cmd_resume)
    t = sub.add_parser("status")
    t.add_argument("--job", type=Path, required=True)
    t.add_argument("--json", action="store_true")
    t.set_defaults(fn=cmd_status)
    c = sub.add_parser("cancel")
    c.add_argument("--job", type=Path, required=True)
    c.set_defaults(fn=cmd_cancel)
    m = sub.add_parser("demo", help="the complete offline scripted session on synthetic inputs")
    m.add_argument("--output", type=Path, required=True)
    m.add_argument("--worker", choices=("fake", "docker"), default="fake")
    m.add_argument("--worker-config", type=Path)
    m.set_defaults(fn=cmd_demo)
    w = sub.add_parser("owner-review", help="decide on the candidate a job in awaiting_owner holds: --accept, --changes TEXT (a new round in the same "
                                             "conversation) or --stop")
    w.add_argument("--job", type=Path, required=True)
    w.add_argument("--accept", action="store_true", help="deliver exactly the candidate's bytes")
    w.add_argument("--changes", help="your words, sent verbatim to the author in the same conversation")
    w.add_argument("--stop", action="store_true", help="end the job unresolved; the candidate is kept")
    w.add_argument("--authorized-by", required=True, help="who decided (and authorizes a change round's allowance)")
    w.add_argument("--note", default="", help="a note recorded with the decision")
    w.add_argument("--medium", default="live AR mirror", help="where you judged it")
    w.add_argument("--max-inference-requests", type=int, help="change round: the inference operations you grant (required)")
    w.add_argument("--budget-usd", type=str, help="change round: the USD this round may spend (required; its own: what earlier rounds left is not carried over; "
                   "each author request reserves its worst case, about 1 USD at 50k input tokens; refused above the per-round ceiling)")
    w.add_argument("--editable-modules", help="change round: comma list of the only modules the author may change this round (default: all)")
    w.add_argument("--max-revisions", type=int, help="change round: new revisions allowed this round (the cap becomes the revisions used plus this; default: the granted operations)")
    w.add_argument("--wall-minutes", type=int, help="change round: the round's wall limit (default: the job's)")
    w.add_argument("--no-continue", action="store_true", help="change round: record it and stop; resume continues later")
    w.add_argument("--new-job-output", type=Path, help="change round of a too-large conversation: the new job's folder (default: <job>-continued-<round>)")
    w.add_argument("--calibration-file", type=Path, help="where the decision's calibration row goes (default: data/modeler/calibration/owner_verdicts.jsonl)")
    w.add_argument("--script", type=Path, help="scripted jobs: the next round's steps (as resume)")
    w.add_argument("--worker-config", type=Path)
    w.add_argument("--fixture-program-sha256", action="append", default=[])
    w.add_argument("--env", type=Path)
    w.add_argument("--api-key-env", default="OPENAI_API_KEY")
    w.set_defaults(fn=cmd_owner_review)
    o = sub.add_parser("owner-verdict", help="record the owner's live verdict against the delivered bytes (append-only; a reject revokes acceptance)")
    o.add_argument("--job", type=Path, required=True)
    o.add_argument("--verdict", choices=("accept", "borderline", "reject"), required=True)
    o.add_argument("--medium", required=True)
    o.add_argument("--note", default="")
    o.add_argument("--sha256")
    o.set_defaults(fn=cmd_owner_verdict)
    u = sub.add_parser("reconcile-unknown", help="owner-authorized settlement of one unknown inference outcome from the provider dashboard; then resume --acknowledge-attention")
    u.add_argument("--job", type=Path, required=True)
    u.add_argument("--request", required=True, help="the request id shown by status (unknown_requests)")
    u.add_argument("--authorized-by", required=True, help="who checked the dashboard, in their own words")
    u.add_argument("--no-charge", action="store_true", help="the dashboard shows no charge for this request")
    u.add_argument("--usage-json", type=Path, help="a file holding the usage block the dashboard reports for this request")
    u.set_defaults(fn=cmd_reconcile_unknown)
    b = sub.add_parser("rebuild", help="no-inference rebuild of a revision's sealed program with the current library into a fresh preview folder (never a delivery)")
    b.add_argument("--job", type=Path, required=True)
    b.add_argument("--revision", required=True, help="the revision id, e.g. r0006")
    b.add_argument("--output", type=Path, required=True, help="a fresh folder outside the job: model.glb, manifest.json (kind preview_rebuild), sheets/")
    b.add_argument("--worker", choices=("docker", "fake"), default="docker", help="docker (default; needs --worker-config) or the synthetic worker (no model)")
    b.add_argument("--worker-config", type=Path, help="the worker config whose doctor self-test passed for the current library and harness")
    b.add_argument("--max-worker-seconds", type=int, help="default: the source job's policy")
    b.set_defaults(fn=cmd_rebuild)
    args = ap.parse_args(argv)
    return args.fn(args)
