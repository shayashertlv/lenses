"""Offline adversarial regressions for the stopped stage-C review; no model or product assets."""
import asyncio
import hashlib
import json
import subprocess
import tarfile

import pytest

from glasses3d.agent import approvals as A, budget as B, loop as L, snapshot as S
from glasses3d.agent.receipts import BlobStore, Cleaner, ReviewLog
from glasses3d.tests.agent.loop_support import make_job, make_tools, occurrences, spec_for
from glasses3d.agent.replay import ScriptedOpenAI
from glasses3d.tests.agent.support import fake_counter, request, usage

run = asyncio.run
RUN = "20261007T120000-run001"


def settled_job(tmp_path):
    job = tmp_path / "jobs" / "test"
    ledger = B.RunLedger.create(job / "runs" / RUN / "budget.json", "2")
    for n in (1, 2):
        ledger.reserve(1000, 100)
        ledger.settle(n, usage(), response_id=f"resp_{n}", request_id=f"req_{n}")
    return job, ledger


@pytest.mark.parametrize("archive_only", [False, True])
def test_closed_ledger_truncation_is_not_blessed_by_repeated_scans(tmp_path, archive_only):
    job, ledger = settled_job(tmp_path)
    if archive_only:
        transport = ledger.path.parent / "transport"
        transport.mkdir()
        (transport / "0002-request.json").write_text("{}")
    else:
        with B.JobLedger(job):
            pass
    ledger.data["rows"].pop()
    ledger._persist()  # valid arithmetic on the forged prefix
    B.RunLedger.load(ledger.path)
    with B.JobLedger(job) as jobs:
        assert jobs.summary()["blocking"]
        assert jobs.scan()["blocking"]


def test_forged_owner_zero_requires_a_bound_reconciliation_audit(tmp_path):
    _, ledger = settled_job(tmp_path)
    row = ledger.data["rows"][0]
    row.update(outcome="reconciled", settled_from="owner", settled_micro_usd=0, usage=None,
               reason="I checked; no charge.")
    ledger._persist()
    with pytest.raises(B.LedgerInvalid, match="audit entry"):
        B.RunLedger.load(ledger.path)


def test_valid_reconcile_changes_only_its_audited_unknown_row(tmp_path):
    job, ledger = settled_job(tmp_path)
    ledger.reserve(1000, 100)
    ledger.mark_unknown(3, reason="connection lost")
    with B.JobLedger(job) as jobs:
        jobs.reconcile_unknown(RUN, 3, "I checked the provider; no charge.", no_charge=True)
        assert not jobs.summary()["blocking"]
    modified = B.RunLedger.load(ledger.path)
    modified.data["reconciliations"][0]["owner_words"] = "forged"
    modified._persist()
    with pytest.raises(B.LedgerInvalid, match="provenance"):
        B.RunLedger.load(ledger.path)


def test_cap_cannot_be_raised_above_consumed_approval(tmp_path):
    _, tools = make_tools()
    job, sha = make_job(tmp_path, tools=tools, cap="1", approve=False)
    A.write_approval(job / "runs" / RUN, max_usd="2", owner_words="Offline fixture covering approval")
    with B.JobLedger(job) as jobs:
        ledger = jobs.open_paid_run(RUN, current_request_sha256=sha, requested_cap_usd="1")
        assert B.RunLedger.load(ledger.path).maximum_micro == 1_000_000
    ledger.data["maximum_micro_usd"] = 2_000_000
    ledger._persist()
    with pytest.raises(B.LedgerInvalid, match="cap"):
        B.RunLedger.load(ledger.path)


def test_input_count_disagreement_records_actual_cost_and_blocks_job(tmp_path):
    job = tmp_path / "jobs" / "test"
    ledger = B.RunLedger.create(job / "runs" / RUN / "budget.json", "1")
    ledger.reserve(1000, 100)
    with pytest.raises(B.InputCountMismatch):
        ledger.settle(1, usage(tokens=999), response_id="resp_1", request_id="req_1")
    assert B.RunLedger.load(ledger.path).rows[0]["outcome"] == "settled"
    with B.JobLedger(job) as jobs:
        assert jobs.summary()["blocking"]


@pytest.mark.parametrize("change", ["job", "image", "brief", "photo", "omit_photo", "note", "notes_file"])
def test_paid_preflight_recomputes_current_inputs_instead_of_trusting_sha(tmp_path, change):
    from dataclasses import replace
    _, tools = make_tools()
    job, sha = make_job(tmp_path, tools=tools)
    transport = ScriptedOpenAI([])
    spec = spec_for(job, RUN, sha, tools, transport)
    if change == "job":
        (job / "job.json").write_text('{"changed": true}')
    elif change == "image":
        (job / "inputs/images/reference-01.png").write_bytes(b"changed")
    elif change == "brief":
        spec.brief += " Altered."
    elif change == "photo":
        spec.photos = [replace(spec.photos[0], view="rear"), spec.photos[1]]
    elif change == "omit_photo":
        spec.photos = spec.photos[:1]
    elif change == "note":
        spec.owner_notes = ["A new note"]
    else:
        (job / "notes").mkdir()
        (job / "notes/owner_notes.jsonl").write_text('{"text":"A new note"}\n')
    with pytest.raises(L.ApprovalRefused):
        run(L.run_astra(spec))
    assert not transport.counts and not transport.creates
    assert not (job / "runs" / RUN / "budget.json").exists()


@pytest.mark.parametrize("missing", ["effort", "contract"])
def test_paid_preflight_requires_effort_and_contract(tmp_path, missing):
    _, tools = make_tools()
    job, sha = make_job(tmp_path, tools=tools)
    transport = ScriptedOpenAI([])
    spec = spec_for(job, RUN, sha, tools, transport)
    if missing == "contract":
        spec.contract = None
    else:
        spec.settings = L.RunSettings()
    with pytest.raises(L.RunRefused, match=missing):
        run(L.run_astra(spec))
    assert not transport.counts and not transport.creates


def test_embedded_and_non_base64_images_roundtrip_without_inline_disk_strings(tmp_path):
    blobs = BlobStore(tmp_path / "blobs")
    text = json.dumps({"nested": "data:image/svg+xml,<svg></svg>"})
    items = [{"type": "function_call_output", "call_id": "call_1", "output": text},
             {"role": "user", "content": "prefix data:image/png;base64,YQ== suffix"}]
    session = L.BlobSession("test", tmp_path / "session.sqlite", blobs=blobs)
    try:
        run(session.add_items(items))
        assert run(session.get_items()) == items
        clean = Cleaner(blobs)(items)
        (tmp_path / "archive.json").write_text(json.dumps(clean))
        assert occurrences(tmp_path, b"data:image") == []
    finally:
        session.close()


def test_request_archive_failure_is_proven_unsent_and_cannot_retry(tmp_path, monkeypatch):
    counter, _ = fake_counter()
    guard = B.BudgetGuard(counter, tmp_path / "budget.json", "1")
    log = ReviewLog(tmp_path / "run", blobs_dir=tmp_path / "blobs", guard=guard)
    def fail(*args):
        raise OSError("archive full")
    monkeypatch.setattr(log, "_save_payload", fail)
    with pytest.raises(OSError):
        run(log.before_request(request()))
    row = B.RunLedger.load(guard.ledger_path).rows[0]
    assert (row["outcome"], row["settled_micro_usd"]) == ("not_sent", 0)
    with pytest.raises(B.BudgetGuardError):
        run(log.before_request(request()))


def test_transcript_and_issued_code_also_hoist_embedded_image_text(tmp_path):
    import zlib
    from types import SimpleNamespace

    log = ReviewLog(tmp_path / "run", blobs_dir=tmp_path / "blobs")
    code = 'result = "data:image/svg+xml,<svg></svg>"'
    context = SimpleNamespace(tool_call_id="call_1", tool_arguments=json.dumps({"code": code}))
    run(log.on_tool_start(context, None, SimpleNamespace(name="execute_blender_code")))
    log.note("A response with embedded data:image/png;base64,YQ== text")
    receipt = json.loads(next(log.tools_dir.glob("*/arguments.json")).read_text())
    stored = receipt["code_blob"]
    assert zlib.decompress(log.blobs.get(stored["sha256"], stored["mime"])).decode() == code
    assert occurrences(tmp_path, b"data:image") == []


def test_partial_exclusive_ledger_recovery_keeps_invalid_bytes_and_consumed_approval(tmp_path):
    job = tmp_path / "jobs" / "test"
    budget = job / "runs" / RUN / "budget.json"
    budget.parent.mkdir(parents=True)
    budget.write_bytes(b'{"schema":')
    with B.JobLedger(job) as jobs:
        assert jobs.summary()["blocking"]
        jobs.reconcile_aborted_run(RUN, "I verified it was never sent and no charge occurred.")
        assert not jobs.summary()["blocking"]
    assert budget.read_bytes() == b'{"schema":'
    from glasses3d.agent.approvals import is_consumed
    assert is_consumed(budget.parent)


def test_snapshot_freezes_dirty_source_and_excludes_inputs(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    source = repo / "glasses3d/agent"
    source.mkdir(parents=True)
    (source / "first.py").write_text("version = 1\n")
    def git(*args):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    git("init")
    git("add", "glasses3d/agent/first.py")
    git("-c", "user.name=Offline Test", "-c", "user.email=test@invalid.example", "commit", "-m", "fixture")
    (source / "first.py").write_text("version = 2\n")
    (source / "new.py").write_text("new = True\n")
    for directory in ("data", ".venv", "node_modules"):
        p = repo / "glasses3d" / directory
        p.mkdir()
        (p / "private.txt").write_text("private")
    (source / ".env").write_text("private")
    monkeypatch.setattr(S, "runtime_versions", lambda: {"fixture": True})
    run_dir = tmp_path / "run"
    binding = S.capture(run_dir, repo_root=repo)
    raw = (run_dir / "implementation.json").read_bytes()
    receipt = json.loads(raw)
    assert binding["sha256"] == hashlib.sha256(raw).hexdigest()
    assert set(receipt["files"]) == {"glasses3d/agent/first.py", "glasses3d/agent/new.py"}
    with tarfile.open(run_dir / "src.tar.gz") as tar:
        for name, digest in receipt["files"].items():
            assert hashlib.sha256(tar.extractfile(name).read()).hexdigest() == digest
        assert tar.extractfile("glasses3d/agent/first.py").read() == (source / "first.py").read_bytes()


def test_snapshot_failure_leaves_a_result_and_releases_the_paid_lock_without_a_request(tmp_path, monkeypatch):
    scene, tools = make_tools()
    job, sha = make_job(tmp_path, tools=tools)
    transport = ScriptedOpenAI([])

    def fail(*args, **kwargs):
        raise OSError("source archive could not be written")

    monkeypatch.setattr(L, "capture_implementation", fail)
    result = run(L.run_astra(spec_for(job, RUN, sha, tools, transport), scene=scene))
    assert result["status"] == "failed" and result["error"]["type"] == "OSError"
    assert result["input"] is None and result["requests"] == 0
    assert (job / "runs" / RUN / "result.json").is_file()
    assert scene.events == ["start", "exit_checkpoint", "close"]
    assert not (job.parent / ".paid-run.lock").exists()
    assert not transport.counts and not transport.creates


def test_stage_c_imports_no_previous_application():
    import ast
    from pathlib import Path
    for path in (Path(L.__file__).parent).glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            modules = [a.name for a in node.names] if isinstance(node, ast.Import) else (
                [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
            assert all(not any(part in {"automation", "blender_agent", "modeler"} for part in name.split("."))
                       for name in modules), path.name


def test_resume_losing_the_paid_lock_does_not_restore_or_repair(tmp_path, monkeypatch):
    from glasses3d.tests.agent.loop_support import FakeScene, SimulatedCrash
    from glasses3d.agent import replay as R
    scene = FakeScene()
    _, tools = make_tools(scene, crash_after="execute_blender_code")
    job, sha = make_job(tmp_path, tools=tools)
    first = R.ScriptedOpenAI([R.tool_call("execute_blender_code", {"code": "pass"})])
    with pytest.raises(SimulatedCrash):
        run(L.run_astra(spec_for(job, RUN, sha, tools, first), scene=scene))
    session_path = job / "session/session.sqlite"
    before = session_path.read_bytes()
    next_run = "20261007T130000-run002"
    job, sha = make_job(tmp_path, tools=tools, run_id=next_run)
    def fail(*args, **kwargs):
        raise B.PaidRunBusy("another paid run took the machine lock after preflight")
    monkeypatch.setattr(B.JobLedger, "open_paid_run", fail)
    second = R.ScriptedOpenAI([])
    with pytest.raises(B.PaidRunBusy):
        run(L.run_astra(spec_for(job, next_run, sha, tools, second, resume=True), scene=scene))
    assert scene.restored == [] and scene.head == "c0001"
    assert session_path.read_bytes() == before
    assert not second.counts and not second.creates
