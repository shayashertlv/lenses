"""Settled-stop finish planning over the scripted transport; no paid requests."""
import asyncio
import json

import pytest

from glasses3d.agent import budget as B, loop as L, replay as R, tools as T
from glasses3d.agent.money import money_text
from glasses3d.tests.agent.loop_support import FakeScene, make_job, spec_for
from glasses3d.tests.agent.test_job_tools import scene
from glasses3d.tests.agent.support import fake_counter, request


def test_scripted_money_facts_never_urge_early_finish_below_cutoff(tmp_path, monkeypatch, scene):
    # Inject the expensive context solely to exercise the footer/stop projection;
    # renewal's own transport proofs cover its interception of that context.
    monkeypatch.setattr(L.ConversationRenewal, 'check', lambda *args: None)
    settings = L.RunSettings(effort='high', max_output_tokens=4096, max_turns=4,
                             settled_stop_usd='20')
    cap = '20'
    tools = T.build_tools(scene, settings)
    run_id = '20261018T100000-runway'
    job, sha = make_job(tmp_path, tools=tools, run_id=run_id, cap=cap, settings=settings)
    transport = R.ScriptedOpenAI([
        R.tool_call('budget_status', counted=780_000,
                    usage=R.default_usage(780_000, output_tokens=0, reasoning_tokens=0)),
        R.message('Observed the settled spend.', counted=1000,
                  usage=R.default_usage(1000, output_tokens=0, reasoning_tokens=0)),
    ])
    spec = spec_for(job, run_id, sha, tools, transport, settings=settings, requested_cap_usd=cap)
    result = asyncio.run(L.run_astra(spec, scene=FakeScene()))
    assert result['status'] == 'completed', result
    assert len(transport.creates) == len(transport.counts) == 2
    assert transport.unexpected == transport.violations == []
    output = next(item['output'] for item in transport.creates[1]['input']
                  if item.get('type') == 'function_call_output')
    data = json.loads(output[0]['text'])
    assert data['budget']['settled_usd'] == '19.500000'
    assert data['budget']['remaining_to_stop_usd'] == '0.500000'
    assert 'finish_runway' not in data
    assert 'left before cutoff $0.500000' in output[-1]['text']
    assert 'finish' not in output[-1]['text'] and 'estimate' not in output[-1]['text']


@pytest.mark.parametrize('outcome', ['held', 'unknown'])
def test_internal_accounting_preserves_open_liability(tmp_path, outcome):
    counter, _ = fake_counter(100_000)
    guard = B.BudgetGuard(counter, tmp_path/'budget.json', '20',
                          settled_stop_usd='20')

    async def hold():
        await guard.before_request(request(max_output_tokens=100))
        if outcome == 'unknown':
            await guard.mark_unknown('scripted unknown outcome')
    asyncio.run(hold())
    before = guard.ledger_path.read_bytes()
    summary = guard.summary()
    assert summary['remaining_to_stop_usd'] == '18.745000'
    assert summary['settled_usd'] == '0.000000' and summary[outcome+'_usd'] == '1.255000'
    assert guard.ledger_path.read_bytes() == before


@pytest.mark.parametrize('settled', ['19.995', '19.999999'])
def test_footer_never_rounds_below_stop_spend_up_to_stop(settled):
    assert 'spent $19.99 (settled)' in money_text({'settled_usd': settled}, places=2)
    text = L.footer_text(settled_usd=float(settled), settled_stop_usd=20,
                         remaining_to_stop_usd=20-float(settled), cap_usd=23)
    assert 'spent $20.00' not in text and 'left before cutoff $0.' in text


def test_footer_has_no_finish_instructions():
    text = L.footer_text(settled_usd=19, settled_stop_usd=20, remaining_to_stop_usd=1,
                         cap_usd=20)
    assert 'finish' not in text
    assert 'first evidence call' not in text and 'second review call' not in text
