"""Offline mailbox, token/cache/money simulation and async waiting; no paid calls or Blender."""
import asyncio
import base64
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import httpx2
import jsonschema
import pytest

from glasses3d.agent import replay as R
from glasses3d.agent.budget import COUNT_FIELDS
from glasses3d.agent.offline import LoopbackOnly, ProductionTransportTripwire
from glasses3d.agent.pricing import Tariff
from glasses3d.tests.proofs import desktop_automation as D


def request(instructions='main instructions'):
    return {'model': 'gpt-6-astra', 'reasoning': {'effort': 'xhigh'},
            'instructions': instructions, 'input': [{'role': 'user', 'content': [
                {'type': 'input_text', 'text': 'Current complete request'},
                {'type': 'input_image', 'image_url': 'data:image/png;base64,'+base64.b64encode(b'pngbytes').decode()}]}],
            'tools': [{'type': 'function', 'name': 'scene_summary', 'description': 'Read current objects',
                       'parameters': {'type': 'object', 'properties': {'objects': {'type': 'null'}},
                                      'required': ['objects'], 'additionalProperties': False}, 'strict': True}],
            'tool_choice': 'required'}


def response(packet, **changes):
    return json.dumps({'request_sha256': packet['request_sha256'], 'name': 'scene_summary',
                       'arguments': {'objects': None}, **changes}).encode()


def test_exact_packet_extracts_images_and_separates_contexts(tmp_path):
    mailbox = D.Mailbox(tmp_path/'mailbox')
    body = request();raw = json.dumps(body, indent=3).encode()
    packet, parsed = mailbox.publish(raw)
    folder = Path(packet['directory'])
    assert (folder/'request.json').read_bytes() == raw
    assert parsed == body and packet['request_sha256'] == D.sha(raw)
    assert packet['role'] == 'main'
    displayed = json.loads((folder/'input.json').read_text())
    local = displayed[0]['content'][1]['image_url']['desktop_local_image']
    assert Path(local['path']).read_bytes() == b'pngbytes'
    assert local['sha256'] == D.sha(b'pngbytes')
    assert (folder/'instructions.txt').read_text() == body['instructions']
    assert json.loads((folder/'tools.json').read_text()) == body['tools']
    child, _ = mailbox.publish(json.dumps(request('specialist instructions')).encode())
    again, _ = mailbox.publish(raw)
    assert child['role'] == 'specialist' and child['context'] != packet['context']
    assert again['queue'] == packet['queue'] and again['context'] != packet['context']
    assert all(row['fresh_desktop_task_required'] for row in (packet,child,again))
    assert Path(again['directory']).name == '000002'
    assert len(list((mailbox.root/'images').iterdir())) == 1


def test_repeated_specialist_invocations_require_distinct_fresh_tasks(tmp_path):
    mailbox=D.Mailbox(tmp_path/'mailbox')
    mailbox.publish(json.dumps(request()).encode())
    first=request('same specialist instructions')
    second=request('same specialist instructions')
    second['input'][0]['content'][0]['text']='Second isolated specialist invocation, different source'
    a,_=mailbox.publish(json.dumps(first).encode())
    b,_=mailbox.publish(json.dumps(second).encode())
    assert a['queue']==b['queue']
    assert a['context']!=b['context']
    assert a['fresh_desktop_task_required'] and b['fresh_desktop_task_required']
    assert 'Second isolated' not in (Path(a['directory'])/'input.json').read_text()
    assert 'Second isolated' in (Path(b['directory'])/'input.json').read_text()


@pytest.mark.parametrize('changes,pattern', [
    ({'request_sha256': '0'*64}, 'stale'),
    ({'name': 'execute_python'}, 'absent'),
    ({'arguments': {'objects': 'wrong'}}, 'not of type'),
    ({'arguments': {'objects': None, 'extra': True}}, 'Additional properties'),
    ({'extra': True}, 'exactly'),
])
def test_response_binds_exact_request_and_current_schema(tmp_path, changes, pattern):
    mailbox = D.Mailbox(tmp_path/'mailbox')
    packet, body = mailbox.publish(json.dumps(request()).encode())
    with pytest.raises((ValueError, jsonschema.ValidationError), match=pattern):
        mailbox.response(packet, body, response(packet, **changes))


def test_mock_transport_counts_actual_payload_and_settles_simulated_usage(tmp_path):
    mailbox = D.Mailbox(tmp_path/'mailbox')
    async def answer(packet, body):
        return mailbox.response(packet, body, response(packet))
    mailbox.wait_response = answer
    body = request()
    count = {key: value for key, value in body.items() if key in COUNT_FIELDS}
    async def scenario():
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(mailbox)) as client:
            counted = await client.post('https://api.openai.com'+R.COUNT_PATH, json=count)
            created = await client.post('https://api.openai.com'+R.CREATE_PATH, json=body)
        return counted, created
    with LoopbackOnly() as network, ProductionTransportTripwire() as clients:
        counted, created = asyncio.run(scenario())
    assert counted.json()['input_tokens'] == R.estimate_tokens(count) > R.TOKENS_PER_IMAGE
    result = created.json()
    assert result['output'][0]['name'] == 'scene_summary'
    assert json.loads(result['output'][0]['arguments']) == {'objects': None}
    assert result['usage']['input_tokens'] == counted.json()['input_tokens']
    assert result['usage']['output_tokens'] > 0
    assert result['usage']['output_tokens_details']['reasoning_tokens'] == 0
    saved = json.loads((Path(mailbox.packets[0]['directory'])/'simulated-usage.json').read_text())
    assert saved['simulated'] and saved['simulated_usage'] == result['usage']
    assert saved['simulated_cost_micro_usd'] == Tariff.frozen().settle_micro(result['usage']) > 0
    assert mailbox.packets[0]['api_calls'] == mailbox.packets[0]['api_cost_usd'] == 0
    assert not network.attempts and not clients.constructions
    assert not mailbox.transport.violations and not mailbox.transport.unexpected


def test_bad_response_does_not_execute_and_cancel_has_no_fallback(tmp_path):
    mailbox = D.Mailbox(tmp_path/'mailbox', timeout_s=.01, poll_s=.001)
    packet, body = mailbox.publish(json.dumps(request()).encode())
    path = Path(packet['response_path']);path.write_bytes(response(packet, name='missing'))
    with pytest.raises(TimeoutError):
        asyncio.run(mailbox.wait_response(packet, body))
    assert json.loads(path.with_name('response-error.json').read_text())['executed'] is False
    assert not mailbox.transport.creates and not mailbox.transport.replies
    (mailbox.root/'CANCEL').touch()
    with pytest.raises(RuntimeError, match='cancelled'):
        asyncio.run(mailbox.wait_response(packet, body))


def test_simulated_cache_is_append_only_and_renewal_rebuilds_once():
    simulation = D.SimulatedUsage()
    first = request()
    reply = R.tool_call('scene_summary', {'objects': None})
    first_usage, first_record = simulation.settle(first, reply)
    assert first_record['simulated_cache_rebuild']
    assert first_usage['input_tokens_details']['cached_tokens'] == 0
    grown = deepcopy(first)
    grown['input'].append({'role': 'user', 'content': 'latest evidence'})
    grown_usage, grown_record = simulation.settle(grown, reply)
    assert not grown_record['simulated_cache_rebuild']
    assert grown_usage['input_tokens_details']['cached_tokens'] == first_usage['input_tokens']
    fresh = deepcopy(first)
    fresh['input'].append({'role': 'user', 'content': 'Astra summary and host factual state'})
    fresh_usage, fresh_record = simulation.settle(fresh, reply)
    assert fresh_record['simulated_cache_rebuild']
    assert fresh_usage['input_tokens_details']['cache_write_tokens'] == fresh_usage['input_tokens']
    fresh['input'].append({'role': 'user', 'content': 'new evidence after renewal'})
    _, next_record = simulation.settle(fresh, reply)
    assert not next_record['simulated_cache_rebuild']
    assert first == request(), 'cache simulation must not rewrite the old conversation'


def test_summary_packet_accepts_only_a_bound_text_response(tmp_path):
    mailbox = D.Mailbox(tmp_path/'mailbox')
    body = request(); body['tool_choice'] = 'none'
    packet, body = mailbox.publish(json.dumps(body).encode())
    assert set(packet['response_shape']) == {'request_sha256', 'text'}
    raw = json.dumps({'request_sha256': packet['request_sha256'], 'text': 'Current head: c0012'}).encode()
    reply = mailbox.response(packet, body, raw)
    assert reply.output[0]['content'][0]['text'] == 'Current head: c0012'
    with pytest.raises(ValueError, match='exactly'):
        mailbox.response(packet, body, response(packet))
    body['tool_choice'] = 'required'
    with pytest.raises(ValueError, match='exactly'):
        mailbox.response(packet, body, raw)


@pytest.mark.parametrize('tool_choice', ['required', 'none'])
def test_output_ceiling_rejects_oversized_desktop_reply(tmp_path, tool_choice):
    mailbox = D.Mailbox(tmp_path/'mailbox')
    body = request(); body.update(tool_choice=tool_choice, max_output_tokens=1)
    packet, body = mailbox.publish(json.dumps(body).encode())
    raw = (json.dumps({'request_sha256': packet['request_sha256'], 'text': 'a summary'}).encode()
           if tool_choice == 'none' else response(packet))
    with pytest.raises(ValueError, match='output ceiling'):
        mailbox.response(packet, body, raw)


def test_mailbox_wait_allows_heartbeat_stop_and_watchdog_cancellation(tmp_path):
    stop = tmp_path/'stop.requested'
    mailbox = D.Mailbox(tmp_path/'mailbox', timeout_s=5, poll_s=.001, stop_file=stop)
    packet, body = mailbox.publish(json.dumps(request()).encode())
    async def scenario():
        beats = []
        async def heartbeat_and_stop():
            for number in range(3):
                await asyncio.sleep(.005)
                beats.append(number)
            stop.touch()
        pulse = asyncio.create_task(heartbeat_and_stop())
        with pytest.raises(D.StopRequested, match='owner stop'):
            await asyncio.wait_for(mailbox.wait_response(packet, body), timeout=1)
        await pulse
        assert beats == [0, 1, 2]
        stop.unlink()
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(mailbox.wait_response(packet, body), timeout=.01)
    asyncio.run(scenario())
    assert not mailbox.transport.creates and not mailbox.simulation.records


def test_replay_unsent_pressure_counts_preserve_latest_count_binding():
    transport = R.ScriptedOpenAI([R.message('summary'), R.message('continued')])
    def post(path, body):
        return transport.handle(httpx2.Request('POST', 'https://api.openai.com'+path, json=body))
    candidate = {'model': R.MODEL, 'input': 'request exceeding pressure threshold'}
    summary = {**candidate, 'input': candidate['input']+'; summarize'}
    post(R.COUNT_PATH, candidate)
    post(R.COUNT_PATH, summary)
    first = post(R.CREATE_PATH, summary).json()
    assert first['usage']['input_tokens'] == R.estimate_tokens(summary)
    fresh = {'model': R.MODEL, 'input': 'fresh conversation'}
    post(R.COUNT_PATH, fresh)
    second = post(R.CREATE_PATH, fresh).json()
    assert second['usage']['input_tokens'] == R.estimate_tokens(fresh)
    assert len(transport.counts) == 3 and len(transport.creates) == 2 and not transport.violations
    post(R.CREATE_PATH, fresh)
    assert 'count calls before it' in transport.violations[-2], 'a prior count may not be reused'


@pytest.mark.parametrize('cap', ['23', '3.5', '20'])
def test_simulated_usage_obeys_real_settled_cutoff(tmp_path, cap):
    from glasses3d.agent.budget import BudgetExceeded, BudgetGuard
    mailbox = D.Mailbox(tmp_path/'mailbox')
    async def answer(packet, body):
        return R.message('visible answer '*4200)
    mailbox.wait_response = answer
    async def count(**kwargs):
        kwargs.pop('timeout', None)
        result = await mailbox(httpx2.Request('POST', 'https://api.openai.com'+R.COUNT_PATH, json=kwargs))
        return SimpleNamespace(**result.json())
    counter = SimpleNamespace(base_url='https://api.openai.com/v1/',
                              responses=SimpleNamespace(input_tokens=SimpleNamespace(count=count)))
    counter.with_options = lambda **kwargs: counter
    guard = BudgetGuard(counter, tmp_path/'budget.json', cap)
    body = request()
    body.update(tool_choice='none', service_tier='default', max_output_tokens=16384, stream=False)
    body['input'][0]['content'][0]['text'] = 'a'*780_000
    async def scenario():
        for index in range(40):
            req = httpx2.Request('POST', 'https://api.openai.com'+R.CREATE_PATH, json=body)
            try:
                await guard.before_request(req)
            except BudgetExceeded:
                break
            result = await mailbox(req)
            result.request = req
            await guard.after_response(result)
            body['input'].append({'role': 'user', 'content': f'continue {index}'})
        else:
            pytest.fail('the unchanged money guard never stopped the simulated run')
    with LoopbackOnly() as network, ProductionTransportTripwire() as clients:
        asyncio.run(scenario())
    rows = guard.ledger.rows
    settled = sum(row['settled_micro_usd'] for row in rows)
    assert len(rows) == len(mailbox.transport.creates) == len(mailbox.simulation.records)
    assert settled == sum(row['simulated_cost_micro_usd'] for row in mailbox.simulation.records)
    assert settled >= guard.ledger.maximum_micro
    spent = 0
    for row in rows:
        assert row['input_tokens'] > 190_000 and row['outcome'] == 'settled'
        assert spent < guard.ledger.maximum_micro
        spent += row['settled_micro_usd']
    assert not mailbox.transport.violations and not network.attempts and not clients.constructions


@pytest.mark.parametrize('settled_only,cap,expected', [(False, None, None),
                                                   (True, '44.2788', 44_278_800),
                                                   (True, None, 20_000_000)])
def test_desktop_fixture_binds_current_money_terms_and_stays_unspendable(tmp_path, monkeypatch,
                                                                      settled_only, cap, expected):
    from glasses3d.agent import approvals as A, jobs
    from glasses3d.agent.loop import _check_bound_request
    from glasses3d.tests.agent.test_jobs import setup_inputs
    args, _ = setup_inputs(tmp_path)
    root = jobs.create_job(**args)
    monkeypatch.setenv('G3D_OFFLINE', '1')
    mailbox = D.Mailbox(tmp_path/'mailbox')
    settings = replace(D.job_run.settings_default(), effort='xhigh', settled_only=settled_only,
                       settled_stop_usd=cap or '20')
    with LoopbackOnly() as network, ProductionTransportTripwire() as clients:
        if not settled_only:
            with pytest.raises(ValueError, match='new runs require settled-only'):
                D.prepare_offline_request(root, 'desktop-proof', mailbox, settings=settings, cap=cap)
            assert not (root/'runs'/'desktop-proof').exists()
            assert not mailbox.packets and not mailbox.transport.counts
            assert not network.attempts and not clients.constructions
            return
        spec, _ = D.prepare_offline_request(root, 'desktop-proof', mailbox, settings=settings, cap=cap)
        run = root/'runs'/'desktop-proof'
        document = json.loads((run/'approval-request.json').read_text())
        assert document['offline_only']
        assert document['requested_cap_micro_usd'] == expected
        approved = A.check_approval(run, current_request_sha256=spec.current_request_sha256)
        assert approved.settled_only == settled_only
        assert approved.max_micro == expected
        _check_bound_request(run, spec)
        with pytest.raises(A.ApprovalRefused, match='cannot authorize a production run'):
            _check_bound_request(run, replace(spec, offline_handler=None, offline_api_key=None))
    assert not mailbox.packets and not mailbox.transport.counts
    assert not network.attempts and not clients.constructions


def test_async_desktop_mailbox_renews_real_loop_from_token_pressure_with_simulated_cache(
        tmp_path, monkeypatch):
    """Actual SDK, shared renewal/guard/session/receipts; scripted mailbox and tiny synthetic evidence."""
    import hashlib
    from agents import function_tool
    from agents.tool import ToolOutputImage, ToolOutputText
    from glasses3d.agent import approvals as A, budget as B, loop as L, renewal as N
    from glasses3d.tests.agent import loop_support as S

    monkeypatch.setenv('G3D_OFFLINE', '1')
    monkeypatch.setattr(B, 'PAID_RUN_LOCK_ROOT', tmp_path/'paid-run-root')
    # Snapshotting is unrelated to renewal; avoid launching Blender --version in this mailbox test.
    monkeypatch.setattr(L, 'capture_implementation', lambda run: {'kind': 'offline desktop fixture'})
    summary = ('1. Current head and best checkpoint: c0001.\n'
               '2. Open differences per photo: front none; side none.\n'
               '3. Decisions and reasons: keep the verified shape.\n'
               '4. Outstanding owner requests: none.\n5. Next action: inspect then finish.')
    scene = S.FakeScene()
    evidence = []
    for index, view in enumerate(('front', 'side')):
        path = tmp_path/(view+'.png')
        path.write_bytes(S.png_bytes(seed=41+index))
        evidence.append({'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                         'call_id': 'desktop-comparison', 'checkpoint': 'c0001', 'view': view,
                         'kind': 'compare_look_to_photos'})
    scene.renewal_evidence = lambda: evidence

    @function_tool
    async def grow():
        """Return obsolete text plus the current photo comparisons."""
        scene.head = 'c0001'
        padding = 'x'*(Tariff.frozen().long_context_input_tokens*4//5*4)
        return [ToolOutputText(text='OLD_DESKTOP_EVIDENCE '+padding),
                *[ToolOutputImage(image_url=S.data_url(Path(row['path']).read_bytes()), detail='high')
                  for row in evidence]]

    @function_tool
    async def inspect():
        """Inspect the same checkpoint after renewal without changing geometry."""
        return {'status': 'ok', 'head': scene.head}

    @function_tool
    async def finish():
        """Accept the checked synthetic fixture."""
        return json.dumps({'status': 'accepted'})

    tools = [grow, inspect, finish]
    settings = L.RunSettings(effort='xhigh', max_output_tokens=1024, max_turns=20,
                             finish_tool='finish', settled_stop_usd='20')
    with monkeypatch.context() as patch:
        patch.setattr(S, 'tool_registry_sha256', lambda tools: L.tool_registry_sha256(tools, settings))
        job, digest = S.make_job(tmp_path, tools=tools, settings=settings, cap='20')
    run_id = '20261007T120000-run001'
    run = job/'runs'/run_id
    # The shared loop fixture writes a synthetic approval: explicitly make this one production-unspendable.
    document = json.loads((run/'approval-request.json').read_text())
    document['offline_only'] = True
    (run/'approval-request.json').write_text(json.dumps(document), encoding='utf-8')
    approval = json.loads((run/'approval.json').read_text())
    approval.update(authorized_by='offline_fixture', owner_words='Synthetic desktop renewal test; no paid run.',
                    approval_request_sha256=A.file_sha256(run/'approval-request.json'))
    (run/'approval.json').write_text(json.dumps(approval), encoding='utf-8')
    mailbox = D.Mailbox(tmp_path/'mailbox')
    tool_names = iter(('grow', 'inspect', 'finish'))
    async def answer(packet, body):
        await asyncio.sleep(0)
        value = {'request_sha256': packet['request_sha256']}
        if body.get('tool_choice') == 'none':
            value['text'] = summary
        else:
            value.update(name=next(tool_names), arguments={})
        return mailbox.response(packet, body, json.dumps(value).encode())
    mailbox.wait_response = answer
    spec = S.spec_for(job, run_id, digest, tools, SimpleNamespace(handle=mailbox), settings=settings)
    with LoopbackOnly() as network, ProductionTransportTripwire() as clients:
        result = asyncio.run(L.run_astra(spec, scene=scene))
    assert result['status'] == 'completed', result
    assert result['requests'] == 4 and len(result['renewals']) == 1
    renewal = result['renewals'][0]
    assert {row['reason'] for row in renewal['reasons']} == {'input_tokens'}
    assert renewal['summary_text'] == summary and renewal['summary_request_sequence'] == 2
    assert renewal['summary_sha256'] == hashlib.sha256(summary.encode()).hexdigest()
    original, summarized, fresh, continued = mailbox.transport.creates
    assert summarized['tool_choice'] == 'none'
    assert summarized['input'][-1]['content'] == N.SUMMARY_REQUEST
    assert summarized['input'][:-1] == mailbox.transport.counts[1]['input']
    assert len(mailbox.transport.counts) == 5, 'the unsent pressure candidate must still be counted'
    assert original['instructions'] == fresh['instructions'] and original['tools'] == fresh['tools']
    assert summary in json.dumps(fresh).replace('\\n', '\n')
    assert 'OLD_DESKTOP_EVIDENCE' not in json.dumps(fresh)
    assert all(photo.image_url in R.input_images(fresh) for photo in S.photos(2))
    assert all(row['sha256'] in json.dumps(fresh) for row in evidence)
    usage = [row['simulated_usage'] for row in mailbox.simulation.records]
    rebuilds = [row['simulated_cache_rebuild'] for row in mailbox.simulation.records]
    assert rebuilds == [True, False, True, False], 'one new cache write, then cached prefix after renewal'
    assert usage[1]['input_tokens_details']['cached_tokens'] == usage[0]['input_tokens']
    assert usage[2]['input_tokens_details']['cached_tokens'] == 0
    assert usage[3]['input_tokens_details']['cached_tokens'] == usage[2]['input_tokens']
    assert usage[2]['input_tokens'] < usage[1]['input_tokens']
    assert R.estimate_tokens(D.count_body(fresh)) == usage[2]['input_tokens']
    assert R.estimate_tokens(D.count_body(continued)) == usage[3]['input_tokens']
    assert all(row['simulated'] for row in mailbox.simulation.records)
    rows = S.ledger_rows(job, run_id)
    assert len(rows) == 4 and all(row['outcome'] == 'settled' for row in rows)
    assert result['budget']['settled_micro_usd'] == sum(row['simulated_cost_micro_usd']
                                                    for row in mailbox.simulation.records) > 0
    assert result['budget']['held_micro_usd'] == result['budget']['unknown_micro_usd'] == 0
    assert not mailbox.transport.violations and not network.attempts and not clients.constructions
    assert all(packet['api_calls'] == packet['api_cost_usd'] == 0 for packet in mailbox.packets)


def test_fresh_run_rejects_existing_work_before_any_run(tmp_path):
    root = tmp_path/'job';root.mkdir();(root/'job.json').write_text('{}');(root/'work').mkdir()
    with pytest.raises(ValueError, match='fresh job'):
        D.run_fresh(root, tmp_path/'mailbox')
    assert not (tmp_path/'mailbox').exists()


def test_wrong_model_or_effort_never_publishes_a_packet(tmp_path):
    mailbox = D.Mailbox(tmp_path/'mailbox')
    body = request();body['reasoning']['effort'] = 'max'
    with pytest.raises(ValueError, match='xhigh'):
        mailbox.publish(json.dumps(body).encode())
    assert not mailbox.packets


def test_offline_marker_refuses_production_before_count_or_client(tmp_path,monkeypatch):
    from glasses3d.agent.loop import _check_bound_request
    from glasses3d.agent.approvals import ApprovalRefused
    run=tmp_path/'runs'/'offline';run.mkdir(parents=True)
    (run/'approval-request.json').write_text(json.dumps({'offline_only':True,'request':{}}))
    spec=SimpleNamespace(job_dir=tmp_path,offline_handler=None,offline_api_key=None)
    monkeypatch.delenv('G3D_OFFLINE',raising=False)
    with LoopbackOnly() as network,ProductionTransportTripwire() as clients:
        with pytest.raises(ApprovalRefused,match='cannot authorize a production run'):
            _check_bound_request(run,spec)
    assert not network.attempts and not clients.constructions
    monkeypatch.setenv('G3D_OFFLINE','1')
    with pytest.raises(ApprovalRefused,match='cannot authorize a production run'):
        _check_bound_request(run,spec)
