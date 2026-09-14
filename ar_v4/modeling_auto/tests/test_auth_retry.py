"""Explicit authentication recovery uses only fake providers and retained fixtures."""
import asyncio
import copy
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from app.controller import Conflict, STAGES
from app.providers import ProviderError
from app.server import create_app
from app.storage import atomic_json, sha
from test_controller import FakeAstra, completed, draft, harness


class RejectedAstra(FakeAstra):
    def __init__(self, stage='lenses', *, status=401, code='invalid_api_key', legacy=False):
        super().__init__()
        self.fail_stage = stage
        self.status = status
        self.code = code
        self.legacy = legacy

    async def edit(self, stage, images, context, receipt_dir, cancel):
        if stage != self.fail_stage:
            return await super().edit(stage, images, context, receipt_dir, cancel)
        self.calls.append({'stage': stage, 'images': [sha(p) for p in images], 'context': context})
        raw = receipt_dir / 'astra_response.stream'
        atomic_json(raw, {'error': {'code': self.code, 'message': 'Synthetic provider rejection'}})
        if not self.legacy:
            atomic_json(receipt_dir / 'astra_http_error.json', {
                'status_code': self.status, 'error_code': self.code,
                'response_sha256': sha(raw),
                'key_sha256': hashlib.sha256(b'fake-openai').hexdigest()})
        raise ProviderError(f'Astra HTTP {self.status}; response saved locally, no automatic retry')


async def rejected(c, **options):
    c.astra = RejectedAstra(**options)
    job = await draft(c)
    await c.start(job['id'], job['version'])
    return await completed(c, job['id'])


def provider_folder(c, identifier):
    op = c._operation(c.store.load(identifier))
    return Path(op['attempts'][0]) / 'provider'


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['lenses', 'connections', 'finish'])
async def test_corrected_key_retries_only_rejected_stage_and_remaining_sequence(harness, stage):
    c = harness
    failed = await rejected(c, stage=stage)
    assert failed['auth_failure'] and failed['allowed_actions'] == ['retry_auth']
    assert 'API setup' in failed['error']
    saved = c.store.load(failed['id'])
    previous_operation = copy.deepcopy(saved['operations'][-1])
    originals = {r['blend_path']: sha(r['blend_path']) for r in saved['revisions']}
    receipts = {str(p): p.read_bytes() for p in provider_folder(c, failed['id']).iterdir()}
    before_calls = len(c.astra.calls)
    with pytest.raises(Conflict, match='different OpenAI API key'):
        await c.start(failed['id'], failed['version'], action='retry_auth')
    assert len(c.astra.calls) == before_calls and c.store.load(failed['id']) == saved
    c.settings.openai_key = 'corrected-fake-openai'
    c.astra.fail_stage = None
    await c.start(failed['id'], failed['version'], action='retry_auth')
    final = await completed(c, failed['id'])
    internal = c.store.load(failed['id'])
    assert (final['status'], final['stage']) == ('waiting', 'review')
    assert final['calls'] == {'meshy': 2, 'astra': 4}
    assert not final['auth_failure'] and final['allowed_actions'] == ['edit', 'accept']
    assert c.meshy.submits == ['generate', 'texture']
    assert c.blender.calls == list(STAGES)
    assert [call['stage'] for call in c.astra.calls].count(stage) == 2
    assert internal['operations'][len(saved['operations']) - 1] == previous_operation
    assert all(sha(path) == checksum for path, checksum in originals.items())
    assert all(Path(path).read_bytes() == content for path, content in receipts.items())


@pytest.mark.asyncio
async def test_auth_retry_stale_and_duplicate_actions_do_not_dispatch_twice(harness):
    c = harness
    failed = await rejected(c)
    c.settings.openai_key = 'corrected-fake-openai'
    c.astra.fail_stage = None
    with pytest.raises(Conflict, match='stale'):
        await c.start(failed['id'], failed['version'] - 1, action='retry_auth')
    started = await c.start(failed['id'], failed['version'], action='retry_auth')
    with pytest.raises(Conflict):
        await c.start(failed['id'], failed['version'], action='retry_auth')
    with pytest.raises(Conflict):
        await c.start(failed['id'], started['version'], action='retry_auth')
    final = await completed(c, failed['id'])
    assert final['calls'] == {'meshy': 2, 'astra': 4}
    assert c.meshy.submits == ['generate', 'texture']


@pytest.mark.asyncio
@pytest.mark.parametrize('legacy', [False, True])
async def test_http_auth_retry_continues_saved_job(harness, legacy):
    c = harness
    failed = await rejected(c, legacy=legacy)
    c.settings.openai_key = 'corrected-fake-openai'
    c.astra.fail_stage = None
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(c)), base_url='http://127.0.0.1:8060') as http:
        response = await http.get('/api/jobs/' + failed['id'])
        assert response.status_code == 200 and response.json()['auth_failure']
        response = await http.post('/api/jobs/' + failed['id'] + '/retry_auth', json={'version': failed['version']})
        assert response.status_code == 200 and response.json()['status'] == 'running'
        assert 'corrected-fake-openai' not in response.text
    final = await completed(c, failed['id'])
    assert final['status'] == 'waiting' and final['calls'] == {'meshy': 2, 'astra': 4}
    assert c.meshy.submits == ['generate', 'texture']


@pytest.mark.asyncio
@pytest.mark.parametrize('status,code,legacy', [
    (400, 'invalid_api_key', False), (429, 'invalid_api_key', False),
    (500, 'invalid_api_key', False), (401, 'permission_denied', False),
    (401, None, False), (400, 'invalid_api_key', True),
    (429, 'invalid_api_key', True), (500, 'invalid_api_key', True),
])
async def test_other_provider_failures_do_not_enable_auth_retry(harness, status, code, legacy):
    c = harness
    failed = await rejected(c, status=status, code=code, legacy=legacy)
    assert not failed['auth_failure'] and 'retry_auth' not in failed['allowed_actions']
    with pytest.raises(Conflict):
        await c.start(failed['id'], failed['version'], action='retry_auth')
    assert len(c.astra.calls) == 1 and c.meshy.submits == ['generate']


@pytest.mark.asyncio
async def test_ambiguous_network_failure_never_uses_legacy_auth_classification(harness):
    c = harness
    failed = await rejected(c, legacy=True)
    internal = c.store.load(failed['id'])
    internal['error'] = internal['operations'][-1]['error'] = 'Connection closed; outcome is uncertain'
    c.store.save(internal)
    current = c.public(internal)
    assert not current['auth_failure'] and current['allowed_actions'] == []


@pytest.mark.asyncio
@pytest.mark.parametrize('legacy', [False, True])
@pytest.mark.parametrize('status', ['failed', 'cancelled', 'interrupted'])
async def test_auth_rejection_survives_restart_without_dispatch(harness, legacy, status):
    c = harness
    failed = await rejected(c, legacy=legacy)
    saved = c.store.load(failed['id'])
    saved['status'] = status
    c.store.save(saved)
    originals = {str(p): sha(p) for p in provider_folder(c, failed['id']).iterdir()}
    c.lock.close()
    await c.open()
    reopened = c.public(c.store.load(failed['id']))
    assert reopened['auth_failure'] and reopened['allowed_actions'] == ['retry_auth']
    assert reopened['calls'] == saved['calls']
    assert len(c.astra.calls) == 1 and c.meshy.submits == ['generate']
    assert all(sha(path) == checksum for path, checksum in originals.items())


@pytest.mark.asyncio
async def test_exact_legacy_401_retries_explicitly_without_key_fingerprint(harness):
    c = harness
    failed = await rejected(c, legacy=True)
    assert failed['auth_failure']
    c.astra.fail_stage = None
    await c.start(failed['id'], failed['version'], action='retry_auth')
    final = await completed(c, failed['id'])
    assert final['status'] == 'waiting' and final['calls'] == {'meshy': 2, 'astra': 4}


@pytest.mark.asyncio
@pytest.mark.parametrize('damage', [
    'raw_hash', 'raw_code', 'raw_json', 'receipt_json', 'status', 'receipt_hash', 'key_hash',
    'astra_script.py', 'response.py', 'astra_response.json', 'astra_failure.json',
])
async def test_corrupt_or_conflicting_receipts_never_enable_auth_retry(harness, damage):
    c = harness
    failed = await rejected(c)
    folder = provider_folder(c, failed['id'])
    raw = folder / 'astra_response.stream'
    receipt = folder / 'astra_http_error.json'
    value = json.loads(receipt.read_text())
    if damage == 'raw_hash':
        raw.write_text(raw.read_text() + ' ')
    elif damage == 'raw_code':
        atomic_json(raw, {'error': {'code': 'other_error'}})
        value['response_sha256'] = sha(raw)
        atomic_json(receipt, value)
    elif damage == 'raw_json':
        raw.write_text('{broken')
    elif damage == 'receipt_json':
        receipt.write_text('{broken')
    elif damage in {'status', 'receipt_hash', 'key_hash'}:
        value[{'status': 'status_code', 'receipt_hash': 'response_sha256', 'key_hash': 'key_sha256'}[damage]] = 'invalid'
        atomic_json(receipt, value)
    else:
        (folder / damage).write_text('{}')
    current = c.public(c.store.load(failed['id']))
    assert not current['auth_failure'] and 'retry_auth' not in current['allowed_actions']
    c.settings.openai_key = 'corrected-fake-openai'
    with pytest.raises(Conflict):
        await c.start(failed['id'], failed['version'], action='retry_auth')
    assert len(c.astra.calls) == 1


@pytest.mark.asyncio
async def test_cancel_before_first_reservation_can_explicitly_recover(harness):
    c = harness
    job = await draft(c)
    started = await c.start(job['id'], job['version'])
    await c.cancel(job['id'], started['version'])
    stopped = await completed(c, job['id'])
    assert stopped['status'] == 'cancelled' and stopped['calls'] == {'meshy': 0, 'astra': 0}
    assert stopped['allowed_actions'] == ['recover']
    assert not c.meshy.submits and not c.astra.calls
    await c.start(job['id'], stopped['version'], action='recover')
    final = await completed(c, job['id'])
    assert final['status'] == 'waiting' and final['calls'] == {'meshy': 2, 'astra': 3}


@pytest.mark.asyncio
async def test_cancel_after_reservation_before_dispatch_reuses_reservation(harness, monkeypatch):
    c = harness
    original = c._execute
    entered = asyncio.Event()
    async def pause_before_dispatch(identifier, operation_id):
        entered.set()
        await c.cancel_event.wait()
        c._check_cancel()
    monkeypatch.setattr(c, '_execute', pause_before_dispatch)
    job = await draft(c)
    await c.start(job['id'], job['version'])
    await asyncio.wait_for(entered.wait(), 5)
    await c.cancel(job['id'], c.store.load(job['id'])['version'])
    stopped = await completed(c, job['id'])
    assert stopped['calls'] == {'meshy': 1, 'astra': 0} and stopped['allowed_actions'] == ['recover']
    assert not c.meshy.submits and not c.astra.calls
    monkeypatch.setattr(c, '_execute', original)
    await c.start(job['id'], stopped['version'], action='recover')
    final = await completed(c, job['id'])
    assert final['status'] == 'waiting' and final['calls'] == {'meshy': 2, 'astra': 3}
    assert c.meshy.submits == ['generate', 'texture']


@pytest.mark.asyncio
@pytest.mark.parametrize('action', ['retry_auth', 'edit'])
async def test_cancel_before_repeat_reservation_recovers_intended_stage_after_restart(harness, action):
    c = harness
    if action == 'retry_auth':
        paused = await rejected(c)
        c.settings.openai_key = 'corrected-fake-openai'
        c.astra.fail_stage = None
        expected_stage = 'lenses'
    else:
        job = await draft(c)
        await c.start(job['id'], job['version'])
        paused = await completed(c, job['id'])
        expected_stage = 'finish'
    prior_calls = paused['calls'].copy()
    started = await c.start(paused['id'], paused['version'], action=action)
    await c.cancel(paused['id'], started['version'])
    stopped = await completed(c, paused['id'])
    assert stopped['calls'] == prior_calls and 'recover' in stopped['allowed_actions']
    assert c._continuation(c.store.load(paused['id'])) == expected_stage
    c.lock.close()
    await c.open()
    if action == 'retry_auth':
        c.settings.openai_key = 'fake-openai'
        with pytest.raises(Conflict, match='different OpenAI API key'):
            await c.start(paused['id'], stopped['version'], action='recover')
        c.settings.openai_key = 'corrected-fake-openai'
    await c.start(paused['id'], stopped['version'], action='recover')
    final = await completed(c, paused['id'])
    assert final['status'] == 'waiting' and final['calls'] == {'meshy': 2, 'astra': 4}
    assert c.meshy.submits == ['generate', 'texture']


@pytest.mark.asyncio
async def test_resumed_existing_operation_does_not_become_pending_after_adoption(harness, monkeypatch):
    c = harness
    c.blender.fail_stage = 'lenses'
    job = await draft(c)
    await c.start(job['id'], job['version'])
    failed = await completed(c, job['id'])
    c.blender.fail_stage = None
    original = c._execute
    async def stop_after_adoption(identifier, operation_id):
        await original(identifier, operation_id)
        raise RuntimeError('Synthetic stop after recovered revision adoption')
    monkeypatch.setattr(c, '_execute', stop_after_adoption)
    await c.start(job['id'], failed['version'], action='recover')
    stopped = await completed(c, job['id'])
    assert c._continuation(c.store.load(job['id'])) == 'connections'
    monkeypatch.setattr(c, '_execute', original)
    await c.start(job['id'], stopped['version'], action='recover')
    final = await completed(c, job['id'])
    assert final['status'] == 'waiting' and final['calls'] == {'meshy': 2, 'astra': 3}
    assert [call['stage'] for call in c.astra.calls] == ['lenses', 'connections', 'finish']
