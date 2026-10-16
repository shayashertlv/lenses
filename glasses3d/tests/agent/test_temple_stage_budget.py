"""Stage allowances use the live working runway; no private scene or model request is started here."""
import asyncio
from contextlib import nullcontext
from dataclasses import replace

from agents.tool_context import ToolContext
import pytest

from glasses3d.agent import budget as B, loop as L
from glasses3d.agent import job_run, pricing
from glasses3d.agent.temple_lab import owner_stage, stage
from glasses3d.agent.tools import temple as T
from glasses3d.tests.agent.support import fake_counter, provider_body, request, response, usage


def guard_at(tmp_path, *, stop='20', cap='23', outcome=None):
    counter, count = fake_counter(600_000)
    guard = B.BudgetGuard(counter, tmp_path / 'budget.json', cap, settled_stop_usd=stop)

    async def prepare():
        await guard.before_request(request(max_output_tokens=100))
        await guard.after_response(response(body=provider_body(
            usage_block=usage(tokens=600_000, written=600_000, output=0))))  # $15 settled
        if outcome:
            count.return_value.input_tokens = 100_000
            await guard.before_request(request(max_output_tokens=100))  # $1.255 held
            if outcome == 'unknown':
                await guard.mark_unknown('offline request outcome unknown')

    asyncio.run(prepare())
    return guard, count


def invoke(guard, monkeypatch):
    started = []
    monkeypatch.setattr(T.stage, 'run', lambda *args: started.append(args) or {'status': 'ok'})

    async def call(ctx, name, action):
        assert name == 'temple_stage'
        return action('stage-call')

    tool = T.temple_tool(object(), call, L.RunSettings(), ())
    assert 'budget_usd' not in tool.params_json_schema.get('properties', {})
    raw = '{}'
    context = ToolContext(context=L.RunStatus(guard), tool_name='temple_stage',
                          tool_call_id='stage-call', tool_arguments=raw)
    before, counts = guard.ledger_path.read_bytes(), guard.client_for_count.responses.input_tokens.count.call_count
    result = asyncio.run(tool.on_invoke_tool(context, raw))
    assert guard.ledger_path.read_bytes() == before
    assert guard.client_for_count.responses.input_tokens.count.call_count == counts
    return result, started


def test_stage_uses_owner_reserve_without_astra_money_choice(tmp_path, monkeypatch):
    guard, _ = guard_at(tmp_path)
    result, started = invoke(guard, monkeypatch)
    assert result['status'] == 'ok' and started[0][4] == '8.000000'
    assert guard.settled_stop_micro == 20_000_000


@pytest.mark.parametrize('outcome', ['held', 'unknown'])
def test_open_and_unknown_holds_reduce_stage_allowance_without_releasing_or_unblocking(tmp_path, monkeypatch, outcome):
    guard, _ = guard_at(tmp_path, outcome=outcome)
    before = guard.summary()
    assert before['remaining_to_stop_usd'] == '3.745000'
    result, started = invoke(guard, monkeypatch)
    assert result['status'] == 'refused' and result['code'] == 'E_STAGE_BUDGET' and not started
    assert guard.summary() == before


@pytest.mark.parametrize('stop', ['15', '14'])
def test_stage_cannot_start_at_or_past_working_stop(tmp_path, monkeypatch, stop):
    guard, _ = guard_at(tmp_path, stop=stop)
    result, started = invoke(guard, monkeypatch)
    assert result['status'] == 'refused' and result['code'] == 'E_STAGE_BUDGET' and not started


def test_remaining_reserve_capacity_is_still_a_bound_when_lower_than_stop(tmp_path, monkeypatch):
    guard, _ = guard_at(tmp_path, cap='16')
    assert guard.summary()['remaining_to_stop_usd'] == '5.000000'
    result, started = invoke(guard, monkeypatch)
    assert result['status'] == 'ok' and started[0][4] == '1.000000'


def test_legacy_no_stop_still_uses_remaining_owner_reserve(tmp_path, monkeypatch):
    guard, _ = guard_at(tmp_path, stop=None)
    result, started = invoke(guard, monkeypatch)
    assert result['status'] == 'ok' and started[0][4] == '8.000000'


def default_run_settings(kind):
    parent = replace(job_run.settings_default(), effort='high')
    if kind == 'main':
        return parent
    if kind == 'temple_stage':
        return stage.settings(parent)
    return owner_stage.settings(parent, {'phase': kind})


@pytest.mark.parametrize('kind', ['main', 'temple_stage', 'understand', 'edit'])
def test_explicit_reserve_covers_largest_main_and_child_request_at_19_99(tmp_path, kind):
    settings = default_run_settings(kind)
    assert settings.effort == 'high' and settings.settled_stop_usd == '20'
    assert settings.max_output_tokens == 16_384 and settings.fit_output_to_budget is False
    maximum_input = pricing.MODEL_MAX_INPUT_TOKENS
    tariff = pricing.Tariff.frozen()
    maximum_hold = tariff.reserve_micro(maximum_input, settings.max_output_tokens)
    assert maximum_input == 922_000 and maximum_hold == 24_278_800
    # The bound also covers the last representable settled amount below $20, not just the example below.
    assert 19_999_999 + maximum_hold < B.cap_to_micro('44.2788')
    counter, count = fake_counter(799_600)
    guard = B.BudgetGuard(counter, tmp_path/'budget.json', '44.2788',
                          settled_stop_usd=settings.settled_stop_usd)

    async def flow():
        await guard.before_request(request(max_output_tokens=settings.max_output_tokens))
        await guard.after_response(response(body=provider_body(
            usage_block=usage(tokens=799_600, written=799_600, output=0))))
        assert guard.summary()['settled_micro_usd'] == 19_990_000
        # The production specialist gets the whole remaining reserve, sharing the same settled stop.
        scope = (nullcontext() if kind == 'main' else
                 guard.stage_budget(guard.summary()['remaining_usd'],
                                    fit_output_to_budget=settings.fit_output_to_budget))
        with scope:
            count.return_value.input_tokens = maximum_input
            last = request(max_output_tokens=settings.max_output_tokens)
            original = last.content
            await guard.before_request(last)
            assert last.content == original  # no shrinking output to fit a prematurely exhausted reserve
            assert guard.ledger.rows[-1]['reserved_micro_usd'] == maximum_hold
            assert guard.summary()['committed_micro_usd'] == 44_268_800
            await guard.after_response(response(body=provider_body(response_id='resp_last',
                usage_block=usage(tokens=maximum_input, written=maximum_input,
                                  output=settings.max_output_tokens))))
            assert guard.summary()['settled_micro_usd'] == 44_268_800
            assert guard.summary()['held_micro_usd'] == 0
            with pytest.raises(B.BudgetExceeded, match='Settled spend reached'):
                await guard.before_request(request(max_output_tokens=settings.max_output_tokens))
        assert count.call_count == 2 and len(guard.ledger.rows) == 2
        assert guard.ledger.totals()['committed_micro_usd'] <= guard.ledger.maximum_micro
    asyncio.run(flow())


@pytest.mark.parametrize('kind', ['main', 'temple_stage', 'understand', 'edit'])
@pytest.mark.parametrize('seed_tokens,settled', [(800_000, 20_000_000), (800_400, 20_010_000)])
def test_larger_reserve_never_starts_another_main_or_child_request_at_or_above_stop(
        tmp_path, kind, seed_tokens, settled):
    settings = default_run_settings(kind)
    counter, count = fake_counter(seed_tokens)
    guard = B.BudgetGuard(counter, tmp_path/'budget.json', '44.2788', settled_stop_usd='20')

    async def flow():
        await guard.before_request(request(max_output_tokens=settings.max_output_tokens))
        await guard.after_response(response(body=provider_body(
            usage_block=usage(tokens=seed_tokens, written=seed_tokens, output=0))))
        assert guard.summary()['settled_micro_usd'] == settled
        scope = nullcontext() if kind == 'main' else guard.stage_budget(guard.summary()['remaining_usd'])
        with scope, pytest.raises(B.BudgetExceeded, match='Settled spend reached'):
            await guard.before_request(request(max_output_tokens=settings.max_output_tokens))
        assert count.call_count == 1 and len(guard.ledger.rows) == 1
        assert guard.summary()['held_micro_usd'] == 0
    asyncio.run(flow())
