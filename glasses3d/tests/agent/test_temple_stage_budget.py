"""Stage allowances use the live working runway; no private scene or model request is started here."""
import asyncio

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
