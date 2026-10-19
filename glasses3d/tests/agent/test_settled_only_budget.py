"""Settled-only admission keeps exact liabilities without treating estimates as spent money."""
import asyncio
from contextlib import nullcontext
from copy import deepcopy
import json

import pytest

from glasses3d.agent import approvals as A, budget as B
from glasses3d.tests.agent.support import fake_counter, provider_body, request, response, usage
from glasses3d.tests.agent.test_approvals import make_job, estimated, SETTINGS


def guard_for(tmp_path, **changes):
    counter, count = fake_counter(799_600)
    settings = {"settled_stop_usd": "20", "settled_only": True, **changes}
    return B.BudgetGuard(counter, tmp_path / "budget.json", "20", **settings), count


@pytest.mark.parametrize("child", [False, True])
def test_full_request_below_stop_ignores_global_and_child_estimated_reserve(tmp_path, child):
    guard, count = guard_for(tmp_path)

    async def flow():
        await guard.before_request(request(max_output_tokens=16_384))
        await guard.after_response(response(body=provider_body(
            usage_block=usage(tokens=799_600, written=799_600, output=0))))
        assert guard.summary()["settled_micro_usd"] == 19_990_000
        scope = guard.stage_budget("0.01") if child else nullcontext()
        with scope:
            count.return_value.input_tokens = 922_000
            next_request = request(max_output_tokens=16_384)
            original = next_request.content
            await guard.before_request(next_request)
            assert next_request.content == original
            assert guard.ledger.rows[-1]["reserved_micro_usd"] == 24_278_800
            assert guard.summary()["remaining_micro_usd"] == 10_000
            # Planning includes the hold; settled-only admission still sent the full request.
            assert guard.summary()["remaining_to_stop_usd"] == "0.000000"
            # A pending hold above $20 remains a valid audit record, not an incurred charge.
            assert B.RunLedger.load(guard.ledger_path).totals()["settled_micro_usd"] == 19_990_000
            await guard.after_response(response(body=provider_body(response_id="resp_child_or_parent",
                usage_block=usage(tokens=922_000, cached=922_000, output=16_384))))
        assert guard.summary()["settled_micro_usd"] == 23_062_800
        assert guard.summary()["remaining_micro_usd"] == 0
        assert guard.summary()["settled_only"] is True
        assert guard.ledger.violation is None
        restored = B.RunLedger.load(guard.ledger_path)
        assert restored.settled_only and restored.totals()["settled_micro_usd"] == 23_062_800
        with pytest.raises(B.BudgetExceeded, match="Settled spend reached"):
            await guard.before_request(request(max_output_tokens=16_384))
        assert count.call_count == 2 and len(guard.ledger.rows) == 2
        with pytest.raises(B.BudgetExceeded, match="Settled spend reached"):
            restored.reserve(1000, 16_384)
    asyncio.run(flow())


@pytest.mark.parametrize("seed_tokens", [800_000, 800_400])
def test_actual_stop_blocks_parent_and_child_before_count(tmp_path, seed_tokens):
    guard, count = guard_for(tmp_path)
    count.return_value.input_tokens = seed_tokens

    async def flow():
        await guard.before_request(request(max_output_tokens=16_384))
        await guard.after_response(response(body=provider_body(
            usage_block=usage(tokens=seed_tokens, written=seed_tokens, output=0))))
        with pytest.raises(B.BudgetExceeded, match="Settled spend reached"):
            await guard.before_request(request())
        with guard.stage_budget("20"), pytest.raises(B.BudgetExceeded, match="Settled spend reached"):
            await guard.before_request(request())
        assert count.call_count == 1 and len(guard.ledger.rows) == 1
    asyncio.run(flow())


@pytest.mark.parametrize("through_guard", [False, True])
def test_unknown_liability_blocks_without_counting_or_releasing_hold(tmp_path, through_guard):
    guard, count = guard_for(tmp_path)

    async def flow():
        await guard.before_request(request(max_output_tokens=16_384))
        if through_guard:
            await guard.mark_unknown("offline lost response")
        else:
            guard.ledger.mark_unknown(1, reason="offline lost response")
        before = guard.ledger_path.read_bytes()
        with pytest.raises(B.BudgetGuardError, match="unknown|unresolved"):
            await guard.before_request(request())
        with pytest.raises(B.BudgetGuardError, match="unresolved"):
            guard.ledger.reserve(1000, 100)
        assert guard.ledger_path.read_bytes() == before and count.call_count == 1
        assert B.RunLedger.load(guard.ledger_path).totals()["unknown_micro_usd"] > 20_000_000
    asyncio.run(flow())


@pytest.mark.parametrize("state", ["held", "unknown", "stopped"])
def test_reload_rejects_a_later_request_after_unresolved_or_crossed_stop(tmp_path, state):
    ledger = B.RunLedger.create(tmp_path / "budget.json", "20", settled_only=True)
    ledger.reserve(800_000, 16_384)
    if state == "unknown":
        ledger.mark_unknown(1, reason="offline lost response")
    elif state == "stopped":
        ledger.settle(1, usage(tokens=800_000, written=800_000, output=0),
                      response_id="resp_1", request_id="req_1")
    changed = deepcopy(ledger.data)
    changed["rows"].append(B._new_row(2, 1000, 100, ledger.tariff.reserve_micro(1000, 100)))
    changed["totals"] = B.ledger_totals(changed["rows"])
    with pytest.raises(B.LedgerInvalid):
        B.validate_ledger(changed)


@pytest.mark.parametrize("changes", [
    {"settled_stop_usd": "0"}, {"settled_stop_usd": "19"},
    {"fit_output_to_budget": True}, {"settled_only": 1},
])
def test_invalid_settled_only_configuration_refuses_before_ledger_creation(tmp_path, changes):
    with pytest.raises(B.BudgetGuardError, match="settled.only"):
        guard_for(tmp_path, **changes)
    assert not (tmp_path / "budget.json").exists()


def test_settled_only_stage_cannot_enable_output_fitting(tmp_path):
    guard, _ = guard_for(tmp_path)
    with pytest.raises(B.BudgetGuardError, match="cannot fit output"):
        with guard.stage_budget("20", fit_output_to_budget=True):
            pytest.fail("stage entered with output fitting")
    assert guard.ledger.rows == []


def test_legacy_reservation_mode_cannot_create_or_admit_new_requests(tmp_path):
    with pytest.raises(B.BudgetGuardError, match="require settled-only"):
        guard_for(tmp_path, settled_only=False)
    assert not (tmp_path / "budget.json").exists()
    with pytest.raises(B.BudgetGuardError, match="require settled-only"):
        B.RunLedger.create(tmp_path / "budget.json", "20", settled_only=False)
    ledger = B.RunLedger.create(tmp_path / "budget.json", "20")
    legacy = deepcopy(ledger.data)
    legacy.pop("settled_only")
    ledger.path.write_text(json.dumps(legacy), encoding="utf-8")
    restored = B.RunLedger.load(ledger.path)
    assert not restored.settled_only and restored.totals()["settled_micro_usd"] == 0
    before = ledger.path.read_bytes()
    with pytest.raises(B.BudgetGuardError, match="legacy reservation-mode"):
        restored.reserve(1000, 100)
    assert ledger.path.read_bytes() == before


def test_guard_default_cutoff_comes_from_approved_amount(tmp_path):
    counter, _ = fake_counter()
    guard = B.BudgetGuard(counter, tmp_path / "budget.json", "20")
    assert guard.ledger.settled_only and guard.settled_stop_micro == 20_000_000


def test_historical_settlements_and_unknown_charge_remain_readable_and_reconcilable(tmp_path):
    ledger = B.RunLedger.create(tmp_path / "budget.json", "20")
    ledger.reserve(1000, 100)
    ledger.settle(1, usage(cached=1000), response_id="resp_old", request_id="req_old")
    ledger.reserve(1000, 100)
    ledger.mark_unknown(2, reason="historical missing response")
    historical = deepcopy(ledger.data)
    historical.pop("settled_only")
    ledger.path.write_text(json.dumps(historical), encoding="utf-8")
    restored = B.RunLedger.load(ledger.path)
    assert restored.totals()["settled_micro_usd"] == 1050
    assert restored.totals()["unknown_micro_usd"] == 17_500
    with pytest.raises(B.BudgetGuardError, match="legacy reservation-mode"):
        restored.reserve(1000, 100)
    restored.reconcile_unknown(2, "Offline fixture: owner confirms no charge", no_charge=True)
    final = B.RunLedger.load(ledger.path)
    assert not final.settled_only and "settled_only" not in final.data
    assert final.totals()["settled_micro_usd"] == 1050
    assert final.totals()["unknown_micro_usd"] == 0


def test_settled_only_persistence_failure_keeps_open_liability(tmp_path, monkeypatch):
    guard, count = guard_for(tmp_path)

    def fail():
        raise OSError("offline disk failure")

    monkeypatch.setattr(guard.ledger, "_persist", fail)
    with pytest.raises(B.LedgerPersistError, match="inference was not sent"):
        asyncio.run(guard.before_request(request(max_output_tokens=16_384)))
    assert guard.ledger.open_row()["reserved_micro_usd"] > 20_000_000
    with pytest.raises(B.BudgetGuardError, match="no recorded outcome"):
        asyncio.run(guard.before_request(request()))
    assert count.call_count == 1


def test_approval_consumes_exact_mode_and_rejects_mode_tampering(tmp_path):
    job = make_job(tmp_path)
    settings = {**SETTINGS, "max_turns": None, "settled_only": True, "settled_stop_usd": "20"}
    run_dir, bound = estimated(job, cap="20", settings=settings)
    A.write_approval(run_dir, max_usd="20", owner_words="Offline test: stop at $20 settled only.")
    digest = A.request_sha256(bound)
    approved = A.check_approval(run_dir, current_request_sha256=digest, requested_cap_usd="20")
    assert approved.settled_only and approved.binding()["settled_only"] is True
    changed = {**bound, "settings": {**settings, "settled_only": False}}
    with pytest.raises(A.ApprovalRefused, match="settings changed"):
        A.check_approval(run_dir, current_request_sha256=A.request_sha256(changed), requested_cap_usd="20")
    with B.JobLedger(job) as jobs:
        ledger = jobs.open_paid_run(run_dir.name, current_request_sha256=digest, requested_cap_usd="20")
        assert ledger.settled_only and ledger.maximum_micro == 20_000_000
        counter, _ = fake_counter()
        guard = B.BudgetGuard.attach(counter, ledger, settled_stop_usd="20")
        assert guard.summary()["settled_only"] is True
        assert B.RunLedger.load(ledger.path).settled_only
        tampered = deepcopy(ledger.data)
        tampered["settled_only"] = False
        with pytest.raises(B.LedgerInvalid, match="consumed approval"):
            B.validate_ledger(tampered)


def test_legacy_approval_cannot_be_reinterpreted_by_changing_both_mode_markers(tmp_path):
    job = make_job(tmp_path)
    run_dir, bound = estimated(job, cap="20", settings={**SETTINGS, "settled_stop_usd": "20"})
    A.write_approval(run_dir, max_usd="20", owner_words="Offline test: $20 reserve cap.")
    approved = A.check_approval(run_dir, current_request_sha256=A.request_sha256(bound), requested_cap_usd="20")
    assert not approved.settled_only
    with B.JobLedger(job) as jobs:
        for action in (jobs.preflight, jobs.open_paid_run):
            with pytest.raises(A.ApprovalRefused, match="legacy reservation-mode approval"):
                action(run_dir.name, current_request_sha256=A.request_sha256(bound), requested_cap_usd="20")
    assert not (run_dir / "budget.json").exists()
    ledger = B.RunLedger.create(run_dir / "budget.json", "20")
    legacy = deepcopy(ledger.data)
    legacy.pop("settled_only")
    legacy["approval"] = approved.binding()
    ledger.path.write_text(json.dumps(legacy), encoding="utf-8")
    ledger = B.RunLedger.load(ledger.path)
    assert not ledger.settled_only
    tampered = deepcopy(ledger.data)
    tampered["settled_only"] = tampered["approval"]["settled_only"] = True
    ledger.path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(B.LedgerInvalid, match="consumed owner approval"):
        B.RunLedger.load(ledger.path)


@pytest.mark.parametrize("changes", [
    {"settled_stop_usd": "19"}, {"settled_stop_usd": None},
    {"fit_output_to_budget": True}, {"settled_only": "true"},
])
def test_malformed_or_conflicting_mode_is_not_an_approved_budget(tmp_path, changes):
    job = make_job(tmp_path)
    settings = {**SETTINGS, "settled_only": True, "settled_stop_usd": "20", **changes}
    run_dir, bound = estimated(job, cap="20", settings=settings)
    A.write_approval(run_dir, max_usd="20", owner_words="Offline test: $20 settled only.")
    with pytest.raises(A.ApprovalRefused, match="settled"):
        A.check_approval(run_dir, current_request_sha256=A.request_sha256(bound), requested_cap_usd="20")
