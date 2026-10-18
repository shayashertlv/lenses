"""Reconcile only complete, matching receipts even after a torn log append."""
from __future__ import annotations

import hashlib
import json

import pytest

from glasses3d.agent.budget import JobLedger, ReconcileError, RunLedger
from glasses3d.agent.jsonl import read_jsonl
from glasses3d.agent.receipts import ReviewLog
from glasses3d.tests.agent.support import provider_body, usage
from glasses3d.tests.agent.test_job_ledger import WORDS, approve, make_job


@pytest.fixture
def archived_run(tmp_path):
    job = make_job(tmp_path, name="receipt-recovery")
    run = approve(job, "20261018T120000-receipt", cap="1")
    ledger = RunLedger.create(run / "budget.json", "1", run_id=run.name)
    ledger.reserve(1000, 100)  # Local synthetic hold only; no transport or paid request.
    response = provider_body(response_id="resp_archived", usage_block=usage(cached=1000))
    path = run / "transport/0001-response.json"
    path.parent.mkdir()
    raw = json.dumps(response).encode("utf-8")
    path.write_bytes(raw)
    event = {"event": "api_response", "request_seq": 1, "payload_path": "transport/0001-response.json",
             "archived_sha256": hashlib.sha256(raw).hexdigest(), "http_status": 200,
             "request_id": "req_archived"}
    return job, run, event


def encode(event):
    return json.dumps(event, ensure_ascii=False).encode("utf-8") + b"\n"


@pytest.mark.parametrize("separator", ["\u2028", "\u2029", "\u0085"])
def test_reconcile_accepts_unicode_line_characters_inside_a_complete_receipt(archived_run, separator):
    job, run, event = archived_run
    event["note"] = "before" + separator + "after"
    (run / "events.jsonl").write_bytes(encode(event))
    with JobLedger(job) as ledger:
        assert ledger.reconcile_from_transport(run.name, 1, WORDS) == 1050
        assert ledger.summary()["totals"]["unknown_micro_usd"] == 0
    row, = RunLedger.load(run / "budget.json").rows
    assert row["outcome"] == "reconciled" and row["settled_from"] == "transport"
    assert not (run / "events-tail-recovery.json").exists()


@pytest.mark.parametrize("tail", [b'{"event":"llm_end","details":', b'{"event":"note","text":"\xe2\x80'])
def test_reconcile_uses_valid_receipt_before_torn_final_event_and_reports_it(archived_run, tail):
    job, run, event = archived_run
    payload = encode(event) + tail
    (run / "events.jsonl").write_bytes(payload)
    with JobLedger(job) as ledger:
        assert ledger.reconcile_from_transport(run.name, 1, WORDS) == 1050
        assert ledger.summary()["totals"]["unknown_micro_usd"] == 0
    report = json.loads((run / "events-tail-recovery.json").read_text(encoding="utf-8"))
    assert report["kind"] == "torn_jsonl_tail"
    assert report["offset"] == len(encode(event)) and report["bytes"] == len(tail)
    assert not report["repaired"]
    assert (run / "events.jsonl").read_bytes() == payload  # Reconcile reads evidence without rewriting it.


def test_reconcile_never_infers_a_receipt_from_a_torn_response_event(archived_run):
    job, run, event = archived_run
    prefix = encode({"event": "api_request", "request_seq": 1})
    (run / "events.jsonl").write_bytes(prefix + encode(event)[:-10])
    with JobLedger(job) as ledger:
        before = RunLedger.load(run / "budget.json").rows
        with pytest.raises(ReconcileError, match="missing or changed"):
            ledger.reconcile_from_transport(run.name, 1, WORDS)
        assert RunLedger.load(run / "budget.json").rows == before
        assert ledger.summary()["totals"]["unknown_micro_usd"] > 0
    assert not (run / "events-tail-recovery.json").exists()


@pytest.mark.parametrize("bad_line", [b'{"event":bad}\n', b'\xff\n'])
def test_reconcile_refuses_corruption_in_a_complete_middle_record(archived_run, bad_line):
    job, run, event = archived_run
    payload = encode(event) + bad_line + encode({"event": "llm_end"})
    (run / "events.jsonl").write_bytes(payload)
    with JobLedger(job) as ledger:
        before = RunLedger.load(run / "budget.json").rows
        with pytest.raises(ReconcileError, match="missing or changed"):
            ledger.reconcile_from_transport(run.name, 1, WORDS)
        assert RunLedger.load(run / "budget.json").rows == before
        assert ledger.summary()["totals"]["unknown_micro_usd"] > 0
    assert (run / "events.jsonl").read_bytes() == payload


def test_receipt_append_preserves_torn_bytes_and_reopens_as_complete_records(tmp_path):
    run = tmp_path / "runs" / "receipt-recovery"
    log = ReviewLog(run, blobs_dir=tmp_path / "blobs")
    first = log.event("before_crash", note="text\u2028kept")
    tail = b'{"event":"incomplete","sequence":'
    with log.events_path.open("ab") as stream:
        stream.write(tail)
    restarted = ReviewLog(run, blobs_dir=tmp_path / "blobs")
    second = restarted.event("after_restart")
    assert read_jsonl(log.events_path).rows == [first, second]
    report = json.loads((run / "events.jsonl.recovery.json").read_text(encoding="utf-8"))
    assert report["repaired"] and report["bytes"] == len(tail)
    archived, = run.glob("events.jsonl.torn-*.bin")
    assert archived.read_bytes() == tail
