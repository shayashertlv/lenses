"""Stage allowances use the live working runway; no private scene or model request is started here."""
import asyncio
import json

from agents.tool_context import ToolContext
import pytest

from glasses3d.agent import budget as B, loop as L
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


def invoke(guard, amount, monkeypatch):
    started = []
    monkeypatch.setattr(T.stage, 'run', lambda *args: started.append(args) or {'status': 'ok'})

    async def call(ctx, name, action):
        assert name == 'temple_stage'
        return action('stage-call')

    tool = T.temple_tool(object(), call, L.RunSettings(), ())
    raw = json.dumps({'budget_usd': amount})
    context = ToolContext(context=L.RunStatus(guard), tool_name='temple_stage',
                          tool_call_id='stage-call', tool_arguments=raw)
    before, counts = guard.ledger_path.read_bytes(), guard.client_for_count.responses.input_tokens.count.call_count
    result = asyncio.run(tool.on_invoke_tool(context, raw))
    assert guard.ledger_path.read_bytes() == before
    assert guard.client_for_count.responses.input_tokens.count.call_count == counts
    return result, started


@pytest.mark.parametrize('amount,allowed', [(5, True), (5.000001, False), (20, False), (23, False)])
def test_stage_allowance_uses_remaining_stop_instead_of_total_reserve(tmp_path, monkeypatch, amount, allowed):
    guard, _ = guard_at(tmp_path)
    result, started = invoke(guard, amount, monkeypatch)
    assert bool(started) is allowed
    if allowed:
        assert result['status'] == 'ok' and started[0][4] == str(float(amount))
    else:
        assert result['status'] == 'refused' and result['code'] == 'E_ARGS'
        assert result['maximum_stage_usd'] == '5.000000'
        assert 'settled stop' in result['message']


@pytest.mark.parametrize('outcome', ['held', 'unknown'])
def test_open_and_unknown_holds_reduce_stage_allowance_without_releasing_or_unblocking(tmp_path, monkeypatch, outcome):
    guard, _ = guard_at(tmp_path, outcome=outcome)
    before = guard.summary()
    assert before['remaining_to_stop_usd'] == '3.745000'
    result, started = invoke(guard, 3.745001, monkeypatch)
    assert result['status'] == 'refused' and result['maximum_stage_usd'] == '3.745000' and not started
    assert guard.summary() == before


@pytest.mark.parametrize('stop', ['15', '14'])
def test_stage_cannot_start_at_or_past_working_stop(tmp_path, monkeypatch, stop):
    guard, _ = guard_at(tmp_path, stop=stop)
    result, started = invoke(guard, .000001, monkeypatch)
    assert result['status'] == 'refused' and result['maximum_stage_usd'] == '0.000000' and not started


def test_remaining_reserve_capacity_is_still_a_bound_when_lower_than_stop(tmp_path, monkeypatch):
    guard, _ = guard_at(tmp_path, cap='16')
    assert guard.summary()['remaining_to_stop_usd'] == '5.000000'
    result, started = invoke(guard, 1.000001, monkeypatch)
    assert result['status'] == 'refused' and result['maximum_stage_usd'] == '1.000000' and not started


@pytest.mark.parametrize('amount,allowed', [(23, True), (23.000001, False)])
def test_legacy_no_stop_keeps_existing_total_reserve_allowance_validation(tmp_path, monkeypatch, amount, allowed):
    guard, _ = guard_at(tmp_path, stop=None)
    result, started = invoke(guard, amount, monkeypatch)
    assert bool(started) is allowed
    assert result['status'] == ('ok' if allowed else 'refused')


@pytest.mark.parametrize('amount', [0, -1, float('nan'), float('inf')])
def test_invalid_stage_allowance_never_starts_preparation(tmp_path, monkeypatch, amount):
    guard, _ = guard_at(tmp_path)
    result, started = invoke(guard, amount, monkeypatch)
    assert result['status'] == 'refused' and result['code'] == 'E_ARGS' and not started
