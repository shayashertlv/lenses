"""Offline approval and append-only ledger invariants."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, localcontext
import json
import threading

import pytest

from glasses3d.agent.approvals import ApprovalRefused
from glasses3d.claude_test import money
from glasses3d.claude_test.pricing import price_usage

MODEL = "claude-opus-5-5"
USAGE = {"input_tokens": 1_000_000, "output_tokens": 0}


def approved(tmp_path, amount="5", *, model=MODEL, run_id="test-run"):
    path = tmp_path / "approval.json"
    money.write_approval(path, words="Yes, this test run, cutoff $" + amount,
                         amount_usd=Decimal(amount), model=model, effort="max", run_id=run_id)
    return money.consume_approval(path, run_id)


def ledger(tmp_path, amount="5"):
    return money.Ledger(tmp_path / "ledger.jsonl", cutoff_usd=Decimal(amount),
                        approval=approved(tmp_path, amount))


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def settle(book, usage=USAGE):
    number = book.start_request(MODEL)
    book.record_settled(number, f"req-{number}", MODEL, usage, price_usage(MODEL, usage))
    return number


def test_finish_conversation_requires_settled_requests_and_permanently_closes(tmp_path):
    book = ledger(tmp_path)
    number = book.start_request(MODEL)
    with pytest.raises(money.LedgerRefused, match="request is open"):
        book.finish_conversation()
    assert book.may_delete_uploads()[0] is False
    book.record_unknown(number, "stream interrupted")
    assert book.may_delete_uploads()[0] is False
    book.finish_conversation()
    book.finish_conversation()
    assert book.conversation_finished()
    assert book.may_delete_uploads()[0] is True
    assert sum(row["event"] == "conversation_finished" for row in rows(book.path)) == 1
    reopened = money.Ledger(book.path, cutoff_usd=book.cutoff_usd, approval=book.approval)
    assert reopened.conversation_finished()
    with pytest.raises(money.LedgerRefused, match="conversation finished"):
        reopened.start_request(MODEL)


def test_livecheck_has_no_paid_approval_or_request_admission(tmp_path):
    path = tmp_path / "ledger.jsonl"
    book = money.Ledger.for_livecheck(path, run_id="live", owner_words="Check the photos for free")
    assert book.money_status() == {"settled_usd": "0", "cutoff_usd": "0", "left_usd": "0"}
    assert rows(path)[0]["approval"] is None
    assert rows(path)[0]["authorization"]["owner_words"] == "Check the photos for free"
    assert not list(tmp_path.glob("approval*"))
    with pytest.raises(money.LedgerRefused, match="free-only"):
        book.start_request(MODEL)
    book.finish_conversation()
    reopened = money.Ledger.for_livecheck(path, run_id="live", owner_words="Check the photos for free")
    assert reopened.may_delete_uploads()[0] is True
    with pytest.raises(money.LedgerRefused, match="different approval"):
        money.Ledger.for_livecheck(path, run_id="other", owner_words="Check the photos for free")


def test_finish_waits_for_active_upload_cache_transaction(tmp_path):
    book = ledger(tmp_path)
    closing = threading.Event()

    def finish():
        closing.set()
        book.finish_conversation()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with book.upload_admission():
            closing_request = pool.submit(finish)
            assert closing.wait(5)
            assert not closing_request.done()
        closing_request.result(timeout=5)
    assert book.conversation_finished()
    with pytest.raises(money.LedgerRefused, match="uploads are closed"):
        with book.upload_admission():
            pytest.fail("closed upload admission entered")


def test_approval_shape_words_binding_and_single_use(tmp_path):
    path = tmp_path / "approval.json"
    words = "  Yes: max effort, five dollars.\n"
    document = money.write_approval(path, words=words, amount_usd=Decimal("5"),
                                    model=MODEL, effort="max", run_id="owner-run")
    assert document["schema"] == "g3d.approval/1"
    assert document["owner_words"] == words
    assert document["authorized_by"] == "owner"
    assert document["max_micro_usd"] == 5_000_000
    assert document["provider"] == "anthropic"
    with pytest.raises(ApprovalRefused, match="different run_id"):
        money.consume_approval(path, "another-run")
    original = path.read_bytes()
    consumed = money.consume_approval(path, "owner-run")
    assert consumed["model"] == MODEL
    assert consumed["effort"] == "max"
    assert consumed["max_usd"] == "5"
    assert path.read_bytes() == original
    with pytest.raises(ApprovalRefused) as error:
        money.consume_approval(path, "owner-run")
    assert error.value.code == "consumed"
    with pytest.raises(ApprovalRefused):
        money.write_approval(path, words="Again", amount_usd=Decimal("5"), model=MODEL,
                             effort="max", run_id="owner-run")


@pytest.mark.parametrize("key,value", [
    ("max_usd", "50"), ("model", "claude-fable-5-1"), ("effort", "high"), ("run_id", "changed"),
])
def test_approval_tampering_cannot_be_consumed(tmp_path, key, value):
    path = tmp_path / "approval.json"
    money.write_approval(path, words="Yes", amount_usd=Decimal("5"),
                         model=MODEL, effort="max", run_id="run")
    doc = json.loads(path.read_bytes())
    doc[key] = value
    path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ApprovalRefused):
        money.consume_approval(path, "run")
    assert not path.with_name("approval.json.consumed.json").exists()


def test_approval_request_tampering_is_stale(tmp_path):
    path = tmp_path / "approval.json"
    money.write_approval(path, words="Yes", amount_usd=Decimal("5"),
                         model=MODEL, effort="max", run_id="run")
    request = tmp_path / "approval-request.json"
    request.write_bytes(request.read_bytes() + b" ")
    with pytest.raises(ApprovalRefused, match="binding"):
        money.consume_approval(path, "run")


@pytest.mark.parametrize("amount", [5.0, "5", Decimal("NaN"), Decimal("Infinity"), Decimal("0"), Decimal("-1")])
def test_approval_amount_requires_positive_finite_decimal(tmp_path, amount):
    with pytest.raises(ApprovalRefused):
        money.write_approval(tmp_path / "approval.json", words="Yes", amount_usd=amount,
                             model=MODEL, effort="max", run_id="run")


def test_submicro_approval_keeps_exact_cutoff(tmp_path):
    book = ledger(tmp_path, "0.0000001")
    assert book.cutoff_usd == Decimal("0.0000001")
    assert book.money_status()["cutoff_usd"] == "0.0000001"


def test_low_decimal_precision_does_not_round_approval_micro_field(tmp_path):
    with localcontext() as context:
        context.prec = 2
        approval = approved(tmp_path, "0.0012345")
    assert approval["max_micro_usd"] == 1234
    assert approval["max_usd"] == "0.0012345"
    book = money.Ledger(tmp_path / "ledger.jsonl", cutoff_usd=Decimal("0.0012345"), approval=approval)
    assert book.money_status()["cutoff_usd"] == "0.0012345"


def test_approval_consumed_once_under_concurrency(tmp_path):
    path = tmp_path / "approval.json"
    money.write_approval(path, words="Yes", amount_usd=Decimal("5"),
                         model=MODEL, effort="max", run_id="run")

    def consume(_):
        try:
            return money.consume_approval(path, "run")
        except ApprovalRefused:
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(consume, range(8)))
    assert sum(value is not None for value in outcomes) == 1


def test_ledger_requires_exact_approved_cutoff_and_model(tmp_path):
    approval = approved(tmp_path)
    with pytest.raises(money.LedgerRefused, match="approved amount"):
        money.Ledger(tmp_path / "ledger.jsonl", cutoff_usd=Decimal("6"), approval=approval)
    book = money.Ledger(tmp_path / "ledger.jsonl", cutoff_usd=Decimal("5"), approval=approval)
    with pytest.raises(money.LedgerRefused, match="model"):
        book.start_request("claude-fable-5-1")
    assert len(rows(book.path)) == 1


def test_consumed_approval_cannot_fund_two_ledgers(tmp_path):
    book = ledger(tmp_path)
    with pytest.raises(money.LedgerRefused, match="another ledger"):
        money.Ledger(tmp_path / "second.jsonl", cutoff_usd=book.cutoff_usd, approval=book.approval)
    assert not (tmp_path / "second.jsonl").exists()


def test_consumed_approval_dictionary_cannot_be_tampered(tmp_path):
    approval = approved(tmp_path)
    changed = {**approval, "owner_words": "Replacement approval words"}
    with pytest.raises(money.LedgerRefused, match="binding changed"):
        money.Ledger(tmp_path / "ledger.jsonl", cutoff_usd=Decimal("5"), approval=changed)


def test_missing_ledger_cannot_reset_consumed_approval(tmp_path):
    book = ledger(tmp_path)
    settle(book)
    book.path.unlink()
    with pytest.raises(money.LedgerRefused, match="ledger.*missing"):
        money.Ledger(book.path, cutoff_usd=book.cutoff_usd, approval=book.approval)
    assert not book.path.exists()


def test_settled_only_crosses_cutoff_then_stops(tmp_path):
    book = ledger(tmp_path)
    settle(book)
    assert book.settled_usd() == Decimal("4")
    assert book.may_start_request()[0] is True
    start_bytes = book.path.read_bytes()
    number = book.start_request(MODEL)
    assert book.money_status() == {"settled_usd": "4", "cutoff_usd": "5", "left_usd": "1"}
    book.record_settled(number, "request-cross", MODEL, USAGE, Decimal("4"))
    assert book.settled_usd() == Decimal("8")
    assert book.money_status() == {"settled_usd": "8", "cutoff_usd": "5", "left_usd": "0"}
    assert book.may_start_request()[0] is False
    with pytest.raises(money.LedgerRefused, match="cutoff"):
        book.start_request(MODEL)
    assert book.path.read_bytes().startswith(start_bytes)
    assert not any(key in row for row in rows(book.path) for key in ("held", "reserved", "projected"))


def test_equal_cutoff_stops_and_zero_usage_does_not_spend(tmp_path):
    book = ledger(tmp_path, "4")
    settle(book, {"input_tokens": 0, "output_tokens": 0})
    assert book.settled_usd() == Decimal(0)
    settle(book)
    assert book.may_start_request()[0] is False


def test_low_decimal_precision_preserves_settlement_left_and_cutoff(tmp_path):
    book = ledger(tmp_path, "0.000603")
    with localcontext() as context:
        context.prec = 2
        settle(book, {"input_tokens": 1, "output_tokens": 0})
        assert book.money_status() == {
            "settled_usd": "0.000004", "cutoff_usd": "0.000603", "left_usd": "0.000599",
        }
        settle(book, {"input_tokens": 100, "output_tokens": 10})
        assert book.settled_usd() == Decimal("0.000604")
        assert book.money_status()["left_usd"] == "0"
        assert book.may_start_request()[0] is False
        with pytest.raises(money.LedgerRefused, match="cutoff"):
            book.start_request(MODEL)


def test_inflight_requests_do_not_reserve_money_and_can_finish_after_unknown(tmp_path):
    book = ledger(tmp_path, "1")
    first, second = book.start_request(MODEL), book.start_request(MODEL)
    assert book.money_status()["settled_usd"] == "0"
    book.record_unknown(first, "SSE disconnected")
    assert book.may_start_request()[0] is False
    book.record_settled(second, "req-2", MODEL, USAGE, Decimal("4"))
    assert book.settled_usd() == Decimal("4")
    assert "unknown" in book.may_start_request()[1]


def test_unknown_stops_and_survives_reopen(tmp_path):
    book = ledger(tmp_path)
    number = book.start_request(MODEL)
    book.record_unknown(number, "missing final usage")
    reopened = money.Ledger(book.path, cutoff_usd=book.cutoff_usd, approval=book.approval)
    assert reopened.settled_usd() == Decimal(0)
    assert reopened.may_start_request()[0] is False
    with pytest.raises(money.LedgerRefused, match="unknown"):
        reopened.start_request(MODEL)


@pytest.mark.parametrize("amount", ["3", "4", "5"])
@pytest.mark.parametrize("details", [None, {"category": "bio"}, {"category": "other", "reason": "declined"}])
def test_refusal_settles_normal_rate_as_upper_bound_and_stops_on_reopen(tmp_path, amount, details):
    book = ledger(tmp_path, amount)
    number = book.start_request(MODEL)
    before = book.path.read_bytes()
    book.record_settled(number, "req-refusal", MODEL, USAGE, Decimal("4"),
                        refusal=True, stop_details=details, upper_bound=True)
    assert book.path.read_bytes().startswith(before)
    row = rows(book.path)[-1]
    assert row["event"] == "settled"
    assert row["usage"] == USAGE
    assert row["cost_usd"] == "4"
    assert row["refusal"] is True
    assert row["stop_details"] == details
    assert row["upper_bound"] is True
    after = book.path.read_bytes()
    reopened = money.Ledger(book.path, cutoff_usd=book.cutoff_usd, approval=book.approval)
    for current in (book, reopened):
        assert current.settled_usd() == Decimal("4")
        assert current.may_start_request() == (False, "refusal")
        assert set(current.money_status()) == {"settled_usd", "cutoff_usd", "left_usd"}
        with pytest.raises(money.LedgerRefused, match="^refusal$"):
            current.start_request(MODEL)
    assert book.path.read_bytes() == after


def test_already_inflight_outcomes_cannot_clear_refusal(tmp_path):
    book = ledger(tmp_path, "100")
    first, second, third, fourth = (book.start_request(MODEL) for _ in range(4))
    book.record_settled(first, "req-refusal", MODEL, USAGE, Decimal("4"),
                        refusal=True, stop_details=None, upper_bound=True)
    book.record_settled(second, "req-finished", MODEL, USAGE, Decimal("4"))
    book.record_rejected(third, 400)
    book.record_unknown(fourth, "missing usage on concurrent request")
    assert book.may_start_request() == (False, "refusal")
    assert book.settled_usd() == Decimal("8")
    assert [row["event"] for row in rows(book.path)[-4:]] == ["settled", "settled", "rejected", "unknown"]


def test_refusal_missing_usage_stays_unknown(tmp_path):
    book = ledger(tmp_path)
    number = book.start_request(MODEL)
    with pytest.raises(money.LedgerRefused, match="cannot settle"):
        book.record_settled(number, "req-refusal", MODEL, None, Decimal("0"),
                            refusal=True, stop_details=None, upper_bound=True)
    assert rows(book.path)[-1]["event"] == "unknown"
    assert book.settled_usd() == 0
    assert "unknown charge" in book.may_start_request()[1]


@pytest.mark.parametrize("metadata", [
    {"refusal": "true", "upper_bound": True},
    {"refusal": True, "upper_bound": 1},
    {"refusal": True, "upper_bound": False},
    {"refusal": False, "upper_bound": True},
    {"refusal": True, "upper_bound": True, "stop_details": "bio"},
])
def test_invalid_refusal_metadata_stops_as_unknown(tmp_path, metadata):
    book = ledger(tmp_path)
    number = book.start_request(MODEL)
    with pytest.raises(money.LedgerRefused, match="cannot settle"):
        book.record_settled(number, "req-refusal", MODEL, USAGE, Decimal("4"), **metadata)
    assert rows(book.path)[-1]["event"] == "unknown"
    assert "unknown charge" in book.may_start_request()[1]
    assert book.settled_usd() == 0


@pytest.mark.parametrize("field,value", [
    ("refusal", "true"), ("upper_bound", False), ("stop_details", ["bio"]),
])
def test_invalid_saved_refusal_metadata_refuses_reopen_without_rewrite(tmp_path, field, value):
    book = ledger(tmp_path)
    number = book.start_request(MODEL)
    book.record_settled(number, "req-refusal", MODEL, USAGE, Decimal("4"),
                        refusal=True, stop_details=None, upper_bound=True)
    history = rows(book.path)
    history[-1][field] = value
    book.path.write_text("".join(json.dumps(row) + "\n" for row in history), encoding="utf-8")
    before = book.path.read_bytes()
    with pytest.raises(money.LedgerRefused, match="ledger history is invalid"):
        money.Ledger(book.path, cutoff_usd=book.cutoff_usd, approval=book.approval)
    assert book.path.read_bytes() == before


@pytest.mark.parametrize("cost,model,usage", [
    (Decimal("3.99"), MODEL, USAGE),
    (4.0, MODEL, USAGE),
    (Decimal("4"), "claude-fable-5-1", USAGE),
    (Decimal("4"), MODEL, {**USAGE, "unknown_billable_field": 5}),
    (Decimal("4"), MODEL, None),
])
def test_bad_settlement_becomes_unknown(tmp_path, cost, model, usage):
    book = ledger(tmp_path)
    number = book.start_request(MODEL)
    with pytest.raises(money.LedgerRefused, match="cannot settle"):
        book.record_settled(number, "req-1", model, usage, cost)
    assert rows(book.path)[-1]["event"] == "unknown"
    assert book.may_start_request()[0] is False
    assert book.settled_usd() == Decimal(0)


def test_duplicate_settlement_is_refused_without_double_charge(tmp_path):
    book = ledger(tmp_path)
    number = settle(book)
    before = book.path.read_bytes()
    with pytest.raises(money.LedgerRefused, match="no open"):
        book.record_settled(number, "again", MODEL, USAGE, Decimal("4"))
    assert book.path.read_bytes() == before
    assert book.settled_usd() == Decimal("4")


def test_waits_and_rejected_http_stop_without_false_unknown_charge(tmp_path):
    book = ledger(tmp_path)
    number = book.start_request(MODEL)
    book.record_wait(number, 429, 2)
    book.record_wait(number, 529, Decimal("0.5"))
    book.record_rejected(number, 429, "retry wait cap reached")
    assert [row["event"] for row in rows(book.path)] == ["opened", "started", "wait", "wait", "rejected"]
    assert all(row["assumed_unbilled_reconcile_with_console"] is True
               for row in rows(book.path) if row["event"] == "wait")
    assert book.settled_usd() == 0
    assert book.may_start_request() == (False, "retry wait cap reached")


def test_free_endpoint_wait_is_logged_without_model_request_or_charge(tmp_path):
    book = ledger(tmp_path, "4")
    book.record_wait(None, 429, "30")
    assert book.settled_usd() == 0
    assert len(rows(book.path)) == 2
    settle(book)
    book.record_wait(None, 529, Decimal("2.5"))
    assert rows(book.path)[-1]["request_no"] is None
    assert rows(book.path)[-1]["assumed_unbilled_reconcile_with_console"] is True
    assert book.may_start_request()[0] is False
    assert book.settled_usd() == Decimal("4")


def test_legacy_wait_and_settlement_rows_remain_readable(tmp_path):
    book = ledger(tmp_path)
    number = book.start_request(MODEL)
    book.record_wait(None, 429, 1)
    book.record_wait(number, 529, 2)
    book.record_settled(number, "req-legacy", MODEL, USAGE, Decimal("4"))
    history = rows(book.path)
    for row in history:
        row.pop("assumed_unbilled_reconcile_with_console", None)
    assert not {"refusal", "stop_details", "upper_bound"}.intersection(history[-1])
    book.path.write_text("".join(json.dumps(row) + "\n" for row in history), encoding="utf-8")
    before = book.path.read_bytes()
    reopened = money.Ledger(book.path, cutoff_usd=book.cutoff_usd, approval=book.approval)
    assert reopened.settled_usd() == Decimal("4")
    assert reopened.may_start_request()[0] is True
    assert book.path.read_bytes() == before


def test_not_sent_is_zero_without_invented_usage(tmp_path):
    book = ledger(tmp_path)
    number = book.start_request(MODEL)
    book.record_not_sent(number, "cancelled before sending")
    assert book.may_start_request()[0] is True
    assert book.settled_usd() == 0
    assert "usage" not in rows(book.path)[-1]


def test_shared_instances_replay_other_settlements_and_atomic_request_numbers(tmp_path):
    first = ledger(tmp_path, "100")
    second = money.Ledger(first.path, cutoff_usd=first.cutoff_usd, approval=first.approval)

    def start(index):
        return (first if index % 2 else second).start_request(MODEL)

    with ThreadPoolExecutor(max_workers=4) as pool:
        numbers = list(pool.map(start, range(12)))
    assert sorted(numbers) == list(range(1, 13))
    first.record_settled(numbers[0], "req", MODEL, USAGE, Decimal("4"))
    assert second.settled_usd() == Decimal("4")


def test_reopening_dead_request_records_unknown(tmp_path, monkeypatch):
    book = ledger(tmp_path)
    book.start_request(MODEL)
    monkeypatch.setattr(money, "identity_alive", lambda identity: False)
    reopened = money.Ledger(book.path, cutoff_usd=book.cutoff_usd, approval=book.approval)
    assert rows(book.path)[-1]["event"] == "unknown"
    assert reopened.may_start_request()[0] is False


def test_reopening_live_request_does_not_mark_unknown(tmp_path):
    book = ledger(tmp_path)
    number = book.start_request(MODEL)
    other = money.Ledger(book.path, cutoff_usd=book.cutoff_usd, approval=book.approval)
    assert other.may_start_request()[0] is True
    other.record_settled(number, "req", MODEL, USAGE, Decimal("4"))
    assert book.settled_usd() == Decimal("4")


@pytest.mark.parametrize("change", ["torn", "unknown_event", "cost"])
def test_invalid_history_blocks_without_rewriting(tmp_path, change):
    book = ledger(tmp_path)
    settle(book)
    if change == "torn":
        with book.path.open("ab") as stream:
            stream.write(b'{"event":')
    else:
        history = rows(book.path)
        if change == "cost":
            history[-1]["cost_usd"] = "0"
        else:
            history[-1]["event"] = "forgiven"
        book.path.write_text("".join(json.dumps(row) + "\n" for row in history), encoding="utf-8")
    invalid = book.path.read_bytes()
    assert book.may_start_request()[0] is False
    assert book.path.read_bytes() == invalid
    with pytest.raises(money.LedgerRefused):
        money.Ledger(book.path, cutoff_usd=book.cutoff_usd, approval=book.approval)
