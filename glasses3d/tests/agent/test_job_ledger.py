"""JobLedger (a rewrite of the Studio's trial_budget.py, plan/WP2_DESIGN.md section 8): per-job non-blocking OS lock,
the machine-wide data/.paid-run.lock {pid, create_time, exe} re-checked with psutil, totals rebuilt from every
runs/*/budget.json (re-priced and tamper-checked), unknown holds counted until reconciled, aborted_before_paid, and the
order preflight -> consume the approval + exclusive-create budget.json in one transaction -> first POST.
Ports the Studio's trial tests (source-before/tests/test_blender_agent_trial_budget.py) where they still apply."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import patch

import httpx2
import psutil
import pytest

from glasses3d.agent import approvals as A
from glasses3d.agent import budget as B
from glasses3d.agent.approvals import ApprovalRefused, build_request, request_sha256
from glasses3d.agent.budget import (BudgetExceeded, BudgetGuard, JobLedger, JobLedgerError, LedgerInvalid,
                                    PaidRunBusy, PaidRunLock, ReconcileError, RunLedger)
from glasses3d.agent.offline import offline_counting_client, offline_inference_client
from glasses3d.tests.agent.support import (OFFLINE_KEY, REPO_ROOT, fake_counter, provider_body, request,
                                           response, usage)

run = asyncio.run
INSTRUCTIONS = hashlib.sha256(b"WP7 instructions").hexdigest()
REGISTRY = hashlib.sha256(b"tool registry").hexdigest()
SETTINGS = {"model": "gpt-6-astra", "effort": "max", "max_output_tokens": 100, "max_turns": 60}
WORDS = "Approved: run it with that cap. (owner)"


def make_job(root: Path, name: str = "prada-8c82d986") -> Path:
    job = root / "jobs" / name
    (job / "inputs" / "images").mkdir(parents=True)
    (job / "job.json").write_text(json.dumps({"schema": "g3d.job/1", "name": name}), encoding="utf-8")
    (job / "inputs" / "images" / "reference-01.png").write_bytes(b"\x89PNG fake")
    return job


def current_sha(job: Path) -> str:
    return request_sha256(build_request(job, instructions_sha256=INSTRUCTIONS, tool_registry_sha256=REGISTRY,
                                        settings=dict(SETTINGS)))


def approve(job: Path, run_id: str, *, cap: str = "15", approved: str | None = None) -> Path:
    request = build_request(job, instructions_sha256=INSTRUCTIONS, tool_registry_sha256=REGISTRY,
                            settings=dict(SETTINGS))
    run_dir = job / "runs" / run_id
    A.write_approval_request(run_dir, job_id=job.name, request=request, estimate={"expected_usd": "1"},
                             suggested_cap_usd=cap)
    A.write_approval(run_dir, max_usd=approved or cap, owner_words=WORDS)
    return run_dir


def paid_run(job: Path, run_id: str, steps, *, cap: str = "15", tokens: int = 1000):
    """Estimate, approve and run: open the paid run, attach a guard, play ``steps`` (each a response or 'drop')."""
    approve(job, run_id, cap=cap)
    client, count = fake_counter(tokens)
    with JobLedger(job) as jobs:
        ledger = jobs.open_paid_run(run_id, current_request_sha256=current_sha(job))
        guard = BudgetGuard.attach(client, ledger)

        async def flow():
            for step in steps:
                await guard.before_request(request())
                if step == "drop":
                    await guard.transport_failed(httpx2.ConnectError("dropped"))
                elif step is not None:
                    await guard.after_response(step)
        run(flow())
        return guard, jobs.close_paid_run()


def settled(cached=1000, response_id="resp_1"):
    return response(body=provider_body(response_id=response_id, usage_block=usage(cached=cached)))


# =========================================================================== the transaction


def test_open_consumes_the_approval_by_creating_budget_json_bound_to_it(tmp_path):
    job = make_job(tmp_path)
    run_dir = approve(job, "20261007T090000-aaaaaa", cap="15")
    approval_sha = hashlib.sha256((run_dir / "approval.json").read_bytes()).hexdigest()
    with JobLedger(job) as jobs:
        assert not (run_dir / "budget.json").exists()
        ledger = jobs.open_paid_run(run_dir.name, current_request_sha256=current_sha(job))
        data = json.loads((run_dir / "budget.json").read_text(encoding="utf-8"))
        assert data["schema"] == "g3d.run_budget/1" and data["rows"] == [] and data["run_id"] == run_dir.name
        assert data["maximum_micro_usd"] == 15_000_000
        assert data["approval"]["approval_sha256"] == approval_sha
        assert data["approval"]["request_sha256"] == current_sha(job)
        holder = json.loads((B.PAID_RUN_LOCK_ROOT / ".paid-run.lock").read_text(encoding="utf-8"))
        me = psutil.Process()
        assert (holder["pid"], holder["create_time"], holder["job_id"], holder["run_id"]) == (
            me.pid, me.create_time(), job.name, run_dir.name)
        assert ledger.fresh and not ledger.rows
        with pytest.raises(JobLedgerError, match="One paid run"):
            jobs.open_paid_run(run_dir.name, current_request_sha256=current_sha(job))
        jobs.close_paid_run()
        assert not (B.PAID_RUN_LOCK_ROOT / ".paid-run.lock").exists()
        refusal = None
        try:
            jobs.open_paid_run(run_dir.name, current_request_sha256=current_sha(job))
        except ApprovalRefused as error:
            refusal = error
        assert refusal is not None and refusal.code == "consumed" and refusal.exit_code == 3
    record = json.loads((job / "budget" / "job-ledger.json").read_text(encoding="utf-8"))
    assert record["schema"] == "g3d.job_ledger/1" and record["runs"][0]["status"] == "ledger"
    assert record["totals"]["committed_micro_usd"] == 0 and record["blocking"] == [] and record["warnings"] == []


def loop_entry(job: Path, run_id: str, calls: list) -> None:
    """The loop's money order: $0 preflight -> consume the approval + create budget.json -> build the clients ->
    the first POST. Any refusal before the clients exist means no request can have been sent."""
    def handler(sent):
        calls.append(sent.url.path)
        if sent.url.path.endswith("/input_tokens"):
            return httpx2.Response(200, json={"object": "response.input_tokens", "input_tokens": 1000})
        return httpx2.Response(200, json=provider_body(usage_block=usage(cached=1000)))

    with JobLedger(job) as jobs:
        jobs.preflight(run_id, current_request_sha256=current_sha(job))
        ledger = jobs.open_paid_run(run_id, current_request_sha256=current_sha(job))
        counter = offline_counting_client(handler, api_key=OFFLINE_KEY)
        guard = BudgetGuard.attach(counter, ledger)
        client = offline_inference_client(handler, api_key=OFFLINE_KEY, event_hooks={
            "request": [guard.before_request], "response": [guard.after_response]})

        async def first_post():
            await client.responses.create(model="gpt-6-astra", input="first", max_output_tokens=100,
                                          service_tier="default")
            await client.close()
            await counter.close()
        run(first_post())


@pytest.mark.parametrize("case", ["missing_request", "missing_approval", "stale", "below_cap", "consumed"])
def test_a_refused_approval_exits_3_creates_nothing_and_sends_zero_requests(tmp_path, case):
    job = make_job(tmp_path)
    run_id = "20261007T090000-aaaaaa"
    run_dir = job / "runs" / run_id
    if case == "missing_request":
        run_dir.mkdir(parents=True)
    elif case == "missing_approval":
        request = build_request(job, instructions_sha256=INSTRUCTIONS, tool_registry_sha256=REGISTRY,
                                settings=dict(SETTINGS))
        A.write_approval_request(run_dir, job_id=job.name, request=request, estimate={}, suggested_cap_usd="15")
    elif case == "below_cap":
        approve(job, run_id, cap="15", approved="10")
    else:
        approve(job, run_id)
    if case == "stale":
        (job / "inputs" / "images" / "reference-01.png").write_bytes(b"edited photo bytes")
    if case == "consumed":
        RunLedger.create(run_dir / "budget.json", "15", run_id=run_id)
    before = sorted(path.name for path in run_dir.iterdir())
    calls = []
    with pytest.raises(ApprovalRefused) as raised:
        loop_entry(job, run_id, calls)
    assert raised.value.exit_code == 3 and raised.value.code == {"missing_request": "missing",
                                                                 "missing_approval": "missing"}.get(case, case)
    assert calls == [] and sorted(path.name for path in run_dir.iterdir()) == before
    assert not (B.PAID_RUN_LOCK_ROOT / ".paid-run.lock").exists()


def test_the_approved_order_reaches_the_first_post_after_budget_json_exists(tmp_path):
    job = make_job(tmp_path)
    approve(job, "20261007T090000-aaaaaa")
    calls = []
    loop_entry(job, "20261007T090000-aaaaaa", calls)
    assert calls == ["/v1/responses/input_tokens", "/v1/responses"]
    row = RunLedger.load(job / "runs" / "20261007T090000-aaaaaa" / "budget.json").rows[0]
    assert (row["outcome"], row["settled_micro_usd"]) == ("settled", 1050)


def test_a_failed_ledger_create_leaves_the_approval_unconsumed_and_the_machine_lock_free(tmp_path):
    job = make_job(tmp_path)
    run_dir = approve(job, "20261007T090000-aaaaaa")
    with JobLedger(job) as jobs:
        with patch.object(RunLedger, "create", side_effect=OSError("disk full")):
            with pytest.raises(OSError):
                jobs.open_paid_run(run_dir.name, current_request_sha256=current_sha(job))
        assert not (run_dir / "budget.json").exists() and not (B.PAID_RUN_LOCK_ROOT / ".paid-run.lock").exists()
        jobs.open_paid_run(run_dir.name, current_request_sha256=current_sha(job))      # the approval still stands
        assert (run_dir / "budget.json").exists()


def test_preflight_creates_nothing(tmp_path):
    job = make_job(tmp_path)
    run_dir = approve(job, "20261007T090000-aaaaaa")
    with JobLedger(job) as jobs:
        approved = jobs.preflight(run_dir.name, current_request_sha256=current_sha(job))
        assert approved.requested_cap_micro == 15_000_000
    assert sorted(path.name for path in run_dir.iterdir()) == ["approval-request.json", "approval.json"]
    assert not (B.PAID_RUN_LOCK_ROOT / ".paid-run.lock").exists()


# =========================================================================== totals across runs (Studio trial tests)


def test_two_runs_keep_their_own_caps_and_the_job_totals_add_up(tmp_path):
    job = make_job(tmp_path)
    first, scan = paid_run(job, "20261007T090000-aaaaaa", [settled(), settled(response_id="resp_2")], cap="15",
                           tokens=1000)
    assert scan["totals"]["settled_micro_usd"] == 2 * 1050
    # a change round: the owner grants a new, smaller cap; the first run's spend does not shrink it
    second, scan = paid_run(job, "20261007T100000-bbbbbb", [settled(response_id="resp_3")], cap="0.02")
    assert second.ledger.maximum_micro == 20_000
    assert [run["settled_micro_usd"] for run in scan["runs"]] == [2100, 1050]
    assert scan["totals"]["settled_micro_usd"] == 3150 and scan["totals"]["requests"] == 3
    with JobLedger(job) as jobs:
        assert jobs.summary()["totals"]["committed_usd"] == "0.003150"


def test_an_unknown_outcome_and_a_crashed_run_keep_the_full_hold(tmp_path):
    job = make_job(tmp_path)
    guard, scan = paid_run(job, "20261007T090000-aaaaaa", ["drop"], cap="0.03")
    assert scan["totals"]["unknown_micro_usd"] == 17_500 and scan["runs"][0]["open_requests"] == [1]
    # a runner killed mid-request leaves a held row on disk: every later scan counts it as unknown
    run_dir = approve(job, "20261007T100000-bbbbbb", cap="0.03")
    crashed = RunLedger.create(run_dir / "budget.json", "0.03", run_id=run_dir.name)
    crashed.reserve(1000, 100)
    assert not (run_dir / "result.json").exists()
    with JobLedger(job) as jobs:
        totals = jobs.summary()["totals"]
    assert totals["unknown_micro_usd"] == 35_000 and totals["committed_micro_usd"] == 35_000


def test_cache_settlement_is_repriced_from_the_flat_persisted_usage(tmp_path):
    job = make_job(tmp_path)
    paid_run(job, "20261007T090000-aaaaaa", [settled(cached=1000)], cap="0.03")
    with JobLedger(job) as jobs:
        assert jobs.summary()["totals"]["settled_usd"] == "0.001050"


def test_a_budget_exceeded_run_still_settles_and_reports(tmp_path):
    job = make_job(tmp_path)
    approve(job, "20261007T090000-aaaaaa", cap="0.018")     # 1,050 settled + 17,500 next hold > 18,000
    client, _ = fake_counter()
    with JobLedger(job) as jobs:
        guard = BudgetGuard.attach(client, jobs.open_paid_run("20261007T090000-aaaaaa",
                                                              current_request_sha256=current_sha(job)))

        async def flow():
            await guard.before_request(request())
            await guard.after_response(settled())
            with pytest.raises(BudgetExceeded):
                await guard.before_request(request())
        run(flow())
        scan = jobs.close_paid_run()
    assert guard.status_hint() == "budget_limit" and scan["totals"]["settled_micro_usd"] == 1050


# =========================================================================== tamper checks (fail closed)


def tampered(original: dict, change: str):
    ledger = json.loads(json.dumps(original))
    row = ledger["rows"][0]
    if change == "amount":
        row["reserved_micro_usd"] = 0
    elif change == "settlement":
        row["settled_micro_usd"] = 0
    elif change == "tariff":
        ledger["tariff"]["input_per_million"] = 0
    elif change == "sequence":
        row["sequence"] = True
    elif change == "extra_field":
        row["settled_time"] = "2026-10-07T09:00:00+00:00"
    elif change == "missing_field":
        del row["reason"]
    elif change == "totals":
        ledger["totals"]["settled_micro_usd"] = 1
    elif change == "usage":
        row["usage"]["cached_tokens"] = 0
    elif change == "outcome":
        row["outcome"] = "refunded"
    elif change == "provenance":
        row["response_id"] = None
    elif change == "source":
        row["settled_from"] = "owner"
    elif change == "cap":
        ledger["maximum_micro_usd"] = 1
    elif change == "schema":
        ledger["schema"] = "blender_agent_budget_v2"
    elif change == "held_not_last":
        extra = json.loads(json.dumps(row))
        extra["sequence"] = 2
        row.update(outcome="held", request_id=None, response_id=None, http_status=None, settled_micro_usd=None,
                   usage=None, settled_from=None, reason=None)
        ledger["rows"].append(extra)
        ledger["totals"] = B.ledger_totals(ledger["rows"])
    return json.dumps(ledger)


@pytest.mark.parametrize("change", ["amount", "settlement", "tariff", "violation", "sequence", "extra_field",
                                    "missing_field", "totals", "usage", "outcome", "provenance", "source", "cap",
                                    "schema", "held_not_last", "{}", "{partial", "null"])
def test_malformed_tampered_and_violated_ledgers_block_paid_runs(tmp_path, change):
    job = make_job(tmp_path)
    paid_run(job, "20261007T090000-aaaaaa", [settled()], cap="0.03")
    path = job / "runs" / "20261007T090000-aaaaaa" / "budget.json"
    original = json.loads(path.read_text(encoding="utf-8"))
    if change == "violation":
        original["violation"] = {"sequence": 1, "reserved_micro_usd": 1, "settled_micro_usd": 2}
        path.write_text(json.dumps(original), encoding="utf-8")
    elif change in ("{}", "{partial", "null"):
        path.write_text(change, encoding="utf-8")
    else:
        path.write_text(tampered(original, change), encoding="utf-8")
    if change != "violation":
        with pytest.raises(LedgerInvalid):
            RunLedger.load(path)
    approve(job, "20261007T100000-bbbbbb")
    with JobLedger(job) as jobs:
        scan = jobs.summary()
        assert len(scan["blocking"]) == 1 and "20261007T090000-aaaaaa" in scan["blocking"][0]
        with pytest.raises(JobLedgerError, match="blocks a paid run"):
            jobs.open_paid_run("20261007T100000-bbbbbb", current_request_sha256=current_sha(job))
    assert not (job / "runs" / "20261007T100000-bbbbbb" / "budget.json").exists()


def test_run_directories_must_stay_inside_the_job(tmp_path):
    _winapi = pytest.importorskip("_winapi")
    job = make_job(tmp_path)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (job / "runs").mkdir()
    _winapi.CreateJunction(str(outside), str(job / "runs" / "20261007T090000-junction"))
    with JobLedger(job) as jobs:
        assert any("junction" in problem for problem in jobs.summary()["blocking"])
        with pytest.raises(JobLedgerError):
            jobs.run_dir("../escape")


# =========================================================================== runs without budget.json


def test_aborted_before_paid_counts_zero_and_never_locks_the_job_out(tmp_path):
    job = make_job(tmp_path)
    approve(job, "20261007T090000-aaaaaa")                  # estimated + approved, preflight failed: no ledger
    (job / "runs" / "20261007T090500-bbbbbb").mkdir()       # a bare run directory
    with JobLedger(job) as jobs:
        scan = jobs.summary()
        assert [run["status"] for run in scan["runs"]] == ["pending_approval", "aborted_before_paid"]
        assert scan["blocking"] == [] and scan["totals"]["committed_micro_usd"] == 0
        jobs.open_paid_run("20261007T090000-aaaaaa", current_request_sha256=current_sha(job))  # still approved


def test_transport_without_a_ledger_blocks_until_the_owner_reconciles(tmp_path):
    job = make_job(tmp_path)
    legacy = job / "runs" / "20261007T080000-legacy"
    (legacy / "transport").mkdir(parents=True)
    (legacy / "transport" / "0001-request.json").write_text("{}", encoding="utf-8")
    approve(job, "20261007T090000-aaaaaa")
    with JobLedger(job) as jobs:
        assert jobs.summary()["runs"][0]["status"] == "ledgerless_with_transport"
        with pytest.raises(JobLedgerError, match="reconcile"):
            jobs.open_paid_run("20261007T090000-aaaaaa", current_request_sha256=current_sha(job))
        with pytest.raises(ReconcileError):
            jobs.reconcile_ledgerless_run(legacy.name, "", no_charge=True)
        with pytest.raises(ReconcileError):
            jobs.reconcile_ledgerless_run(legacy.name, WORDS)
        with pytest.raises(ReconcileError):
            jobs.reconcile_ledgerless_run("20261007T090000-aaaaaa", WORDS, no_charge=True)   # has no transport
        assert jobs.reconcile_ledgerless_run(legacy.name, "Owner: the dashboard shows $0.0123451 for it",
                                             charged_usd="0.0123451") == 12_346               # a cost rounds up
        scan = jobs.summary()
        assert scan["runs"][0]["status"] == "reconciled_ledgerless" and scan["blocking"] == []
        assert scan["totals"]["settled_micro_usd"] == 12_346
        jobs.open_paid_run("20261007T090000-aaaaaa", current_request_sha256=current_sha(job))


# =========================================================================== owner reconcile of closed runs


def test_reconcile_a_closed_run_through_the_job_ledger(tmp_path):
    job = make_job(tmp_path)
    paid_run(job, "20261007T090000-aaaaaa", ["drop"], cap="1")
    words = "Owner: request 1 cost $0.00105 on the dashboard"
    with JobLedger(job) as jobs:
        assert jobs.summary()["totals"]["unknown_micro_usd"] == 17_500
        assert jobs.reconcile_unknown("20261007T090000-aaaaaa", 1, words, usage(cached=1000)) == 1050
        totals = jobs.summary()["totals"]
        assert (totals["unknown_micro_usd"], totals["settled_micro_usd"]) == (0, 1050)
        with pytest.raises(ReconcileError):
            jobs.reconcile_unknown("20261007T090000-aaaaaa", 1, words, no_charge=True)


def test_reconcile_from_transport_settles_a_crashed_runs_held_row(tmp_path):
    job = make_job(tmp_path)
    run_dir = approve(job, "20261007T090000-aaaaaa", cap="1")
    crashed = RunLedger.create(run_dir / "budget.json", "1", run_id=run_dir.name)
    crashed.reserve(1000, 100)          # sent; the 200 was archived; the runner died before settling
    archived = provider_body(response_id="resp_archived", usage_block=usage(cached=1000))
    def archive(payload):
        path = run_dir / "transport/0001-response.json"
        path.parent.mkdir(exist_ok=True)
        raw = json.dumps(payload).encode()
        path.write_bytes(raw)
        (run_dir / "events.jsonl").write_text(json.dumps({
            "event": "api_response", "request_seq": 1, "payload_path": "transport/0001-response.json",
            "archived_sha256": hashlib.sha256(raw).hexdigest(), "http_status": 200,
            "request_id": "req_archived"}) + "\n", encoding="utf-8")
    with JobLedger(job) as jobs:
        with pytest.raises(ReconcileError, match="missing or changed"):
            jobs.reconcile_from_transport(run_dir.name, 1, WORDS)
        archive(provider_body(usage_block=usage(tokens=999, cached=999)))
        with pytest.raises(ReconcileError, match="counts 999 input tokens"):
            jobs.reconcile_from_transport(run_dir.name, 1, WORDS)
        archive(archived)
        assert jobs.reconcile_from_transport(run_dir.name, 1, WORDS) == 1050
        row = RunLedger.load(run_dir / "budget.json").rows[0]
        assert (row["outcome"], row["settled_from"], row["response_id"]) == ("reconciled", "transport", "resp_archived")
        assert jobs.summary()["totals"] == {**jobs.summary()["totals"], "settled_micro_usd": 1050,
                                            "unknown_micro_usd": 0}


def test_reconcile_refuses_the_active_run(tmp_path):
    job = make_job(tmp_path)
    approve(job, "20261007T090000-aaaaaa")
    with JobLedger(job) as jobs:
        jobs.open_paid_run("20261007T090000-aaaaaa", current_request_sha256=current_sha(job))
        with pytest.raises(ReconcileError, match="after it has ended"):
            jobs.reconcile_unknown("20261007T090000-aaaaaa", 1, WORDS, no_charge=True)


def test_closing_a_run_marks_an_open_request_unknown(tmp_path):
    job = make_job(tmp_path)
    guard, scan = paid_run(job, "20261007T090000-aaaaaa", [None], cap="1")     # sent, no outcome, run ends
    row = RunLedger.load(guard.ledger_path).rows[0]
    assert (row["outcome"], row["reason"]) == ("unknown", "the run ended before this request had an outcome")
    assert scan["totals"]["unknown_micro_usd"] == 17_500


# =========================================================================== locks (real processes)


def child(code: str, *args: str) -> subprocess.Popen:
    environment = dict(os.environ, PYTHONPATH=str(REPO_ROOT), PYTHONDONTWRITEBYTECODE="1")
    return subprocess.Popen([sys.executable, "-c", code, *args], cwd=str(REPO_ROOT), env=environment,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def finish(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.kill()
    process.communicate(timeout=30)


def wait_dead(pid: int, seconds: float = 10.0) -> None:
    """Killing the venv launcher ends the interpreter through its job object, a moment later."""
    deadline = time.monotonic() + seconds
    while psutil.pid_exists(pid):
        try:
            if psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
                return
        except psutil.NoSuchProcess:
            return
        assert time.monotonic() < deadline, f"process {pid} did not exit"
        time.sleep(.02)


def retry_until(action, seconds: float = 3.0):
    """Windows may finish releasing byte-range locks shortly after the process handle signals exit."""
    deadline = time.monotonic() + seconds
    while True:
        try:
            return action()
        except JobLedgerError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(.02)


def test_one_job_lock_per_process_and_a_crash_releases_it(tmp_path):
    job = make_job(tmp_path)
    with JobLedger(job):
        with pytest.raises(JobLedgerError, match="Another process"):
            with JobLedger(job):
                pass
    code = ("import sys; from glasses3d.agent.budget import JobLedger; ledger = JobLedger(sys.argv[1]); "
            "ledger.__enter__(); print('locked', flush=True); sys.stdin.readline()")
    process = child(code, str(job))
    try:
        assert process.stdout.readline().strip() == "locked", process.stderr.read()
        with pytest.raises(JobLedgerError, match="Another process holds this job's budget lock"):
            with JobLedger(job):
                pass
        process.kill()
        process.wait(timeout=10)
        assert (job / "budget" / ".job-ledger.lock").exists()

        def enter():
            with JobLedger(job) as jobs:
                return jobs.summary()["totals"]["committed_micro_usd"]
        assert retry_until(enter) == 0
    finally:
        finish(process)


def test_the_machine_lock_allows_one_paid_run_across_jobs_in_this_process(tmp_path):
    first = make_job(tmp_path / "lab" / "package", "job-one-aaaaaaaa")
    second = make_job(tmp_path / "ordinary-jobs", "job-two-bbbbbbbb")
    assert first.parent != second.parent  # the lab and ordinary job cannot split the machine-wide lock
    approve(first, "20261007T090000-aaaaaa")
    approve(second, "20261007T090000-bbbbbb")
    with JobLedger(first) as one, JobLedger(second) as two:
        one.open_paid_run("20261007T090000-aaaaaa", current_request_sha256=current_sha(first))
        assert one._paid_lock.path == B.PAID_RUN_LOCK_ROOT / ".paid-run.lock"
        with pytest.raises(PaidRunBusy, match="Another paid run"):
            two.preflight("20261007T090000-bbbbbb", current_request_sha256=current_sha(second))
        with pytest.raises(PaidRunBusy, match="Another paid run"):
            two.open_paid_run("20261007T090000-bbbbbb", current_request_sha256=current_sha(second))
        assert not (second / "runs" / "20261007T090000-bbbbbb" / "budget.json").exists()
        one.close_paid_run()
        two.open_paid_run("20261007T090000-bbbbbb", current_request_sha256=current_sha(second))


def test_a_live_paid_run_in_another_process_blocks_and_its_crash_frees_the_machine(tmp_path):
    job = make_job(tmp_path)
    approve(job, "20261007T090000-aaaaaa")
    code = ("import sys, os; from glasses3d.agent.budget import PaidRunLock; lock = PaidRunLock(sys.argv[1]); "
            "lock.acquire(job_id='other-job', run_id='r'); print(os.getpid(), flush=True); sys.stdin.readline()")
    process = child(code, str(B.PAID_RUN_LOCK_ROOT))
    try:
        child_pid = int(process.stdout.readline().strip() or 0)
        assert child_pid, process.stderr.read()
        with JobLedger(job) as jobs:
            with pytest.raises(PaidRunBusy, match=f"pid {child_pid}"):
                jobs.preflight("20261007T090000-aaaaaa", current_request_sha256=current_sha(job))
            with pytest.raises(PaidRunBusy, match=f"pid {child_pid}"):
                jobs.open_paid_run("20261007T090000-aaaaaa", current_request_sha256=current_sha(job))
            assert not (job / "runs" / "20261007T090000-aaaaaa" / "budget.json").exists()
            process.kill()
            process.wait(timeout=10)
            retry_until(lambda: jobs.open_paid_run("20261007T090000-aaaaaa",
                                                   current_request_sha256=current_sha(job)))
            assert jobs._paid_lock.replaced_stale["pid"] == child_pid         # the dead runner's file, replaced
    finally:
        finish(process)


def test_a_live_holder_without_the_os_guard_still_blocks(tmp_path):
    # an older runner that only wrote its identity file: psutil says it is alive, so the machine stays taken
    jobs_root = tmp_path / "jobs"
    jobs_root.mkdir()
    code = ("import json, sys, os; from glasses3d.agent.budget import process_identity; identity = process_identity(); "
            "identity.update(schema='g3d.paid_run_lock/1', job_id='old', run_id='r'); "
            "open(sys.argv[1] + '/.paid-run.lock', 'w').write(json.dumps(identity)); "
            "print(os.getpid(), flush=True); sys.stdin.readline()")
    process = child(code, str(jobs_root))
    try:
        child_pid = int(process.stdout.readline().strip() or 0)
        assert child_pid, process.stderr.read()
        with pytest.raises(PaidRunBusy, match=f"pid {child_pid}"):
            PaidRunLock(jobs_root).acquire(job_id="mine", run_id="r")
    finally:
        finish(process)
    wait_dead(child_pid)
    lock = PaidRunLock(jobs_root)
    lock.acquire(job_id="mine", run_id="r")             # the holder died: its file is stale now
    assert lock.replaced_stale["pid"] == child_pid
    lock.release()
    assert not lock.path.exists()


@pytest.mark.parametrize("stale", ["dead_pid", "reused_pid", "other_exe", "malformed", "not_object"])
def test_a_stale_lock_file_is_replaced(tmp_path, stale):
    jobs_root = tmp_path / "jobs"
    jobs_root.mkdir()
    me = B.process_identity()
    finished = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True, text=True,
                              timeout=60)
    content = {"dead_pid": json.dumps({**me, "pid": int(finished.stdout.split()[-1])}),
               "reused_pid": json.dumps({**me, "create_time": me["create_time"] - 3600}),
               "other_exe": json.dumps({**me, "exe": os.path.normcase("C:/somewhere/else.exe")}),
               "malformed": "{partial", "not_object": "[1]"}[stale]
    (jobs_root / ".paid-run.lock").write_text(content, encoding="utf-8")
    lock = PaidRunLock(jobs_root)
    identity = lock.acquire(job_id="mine", run_id="r")
    assert identity["pid"] == me["pid"] and json.loads(lock.path.read_text(encoding="utf-8")) == identity
    lock.release()


def test_our_own_leftover_identity_is_not_a_live_run(tmp_path):
    jobs_root = tmp_path / "jobs"
    jobs_root.mkdir()
    (jobs_root / ".paid-run.lock").write_text(json.dumps({**B.process_identity(), "job_id": "x", "run_id": "y"}),
                                              encoding="utf-8")
    lock = PaidRunLock(jobs_root)
    lock.acquire(job_id="mine", run_id="r")             # we hold the OS guard: the file is a leftover of ours
    with pytest.raises(PaidRunBusy):
        PaidRunLock(jobs_root).acquire(job_id="again", run_id="r")     # but a second holder in-process is refused
    lock.release()


def test_identity_liveness_uses_pid_create_time_and_exe():
    me = B.process_identity()
    assert B.identity_alive(me)
    assert not B.identity_alive({**me, "create_time": me["create_time"] + 5})
    assert not B.identity_alive({**me, "pid": True})
    assert not B.identity_alive(None) and not B.identity_alive({"pid": me["pid"]})
