"""Crash seams and setup changes, using only fake providers and short local paths."""
import asyncio
import json
from pathlib import Path

import httpx
import pytest

import app.controller as controller_module
import app.server as server_module
from app.controller import Conflict, STAGES
from app.providers import ProviderError
from app.server import create_app
from app.storage import ServiceLock, atomic_json, sha
from test_controller import FakeAstra, FakeBlender, FakeMeshy, completed, draft, harness


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", STAGES)
async def test_restart_after_adoption_continues_without_replaying_stage(harness, monkeypatch, stage):
    c = harness
    execute = c._execute
    async def crash_after_saved_stage(identifier, operation_id):
        current_stage = next(op['stage'] for op in c.store.load(identifier)['operations'] if op['id'] == operation_id)
        await execute(identifier, operation_id)
        if current_stage == stage:
            raise RuntimeError('Synthetic stop after immutable revision adoption')
    monkeypatch.setattr(c, '_execute', crash_after_saved_stage)
    job = await draft(c)
    await c.start(job['id'], job['version'])
    stopped = await completed(c, job['id'])
    internal = c.store.load(job['id'])
    assert internal['active_operation'] is None
    assert internal['operations'][-1]['status'] == 'complete'
    assert 'recover' in stopped['allowed_actions']
    originals = {r['blend_path']: sha(r['blend_path']) for r in internal['revisions']}
    before_calls = internal['calls'].copy()
    # A hard process exit would retain the last running state, without the
    # exception handler's bookkeeping. Opening it must not dispatch anything.
    internal['status'] = 'running'
    c.store.save(internal)
    c.lock.close()
    await c.open()
    reopened = c.public(c.store.load(job['id']))
    assert reopened['status'] == 'interrupted' and reopened['calls'] == before_calls
    assert 'recover' in reopened['allowed_actions']
    monkeypatch.setattr(c, '_execute', execute)
    await c.start(job['id'], reopened['version'], action='recover')
    final = await completed(c, job['id'])
    assert (final['status'], final['stage']) == ('waiting', 'review')
    assert final['calls'] == {'astra': 3, 'meshy': 2}
    assert c.blender.calls == list(STAGES)
    assert c.meshy.submits == ['generate', 'texture']
    assert [call['stage'] for call in c.astra.calls] == ['lenses', 'connections', 'finish']
    assert all(sha(path) == checksum for path, checksum in originals.items())
    if stage == 'finish':
        assert final['calls'] == before_calls


@pytest.mark.asyncio
async def test_cancel_between_stages_recover_only_remaining_calls(harness, monkeypatch):
    c = harness
    execute = c._execute
    async def cancel_after_adoption(identifier, operation_id):
        await execute(identifier, operation_id)
        c.cancel_event.set()
    monkeypatch.setattr(c, '_execute', cancel_after_adoption)
    job = await draft(c)
    await c.start(job['id'], job['version'])
    final = await completed(c, job['id'])
    assert final['status'] == 'cancelled' and final['calls'] == {'astra': 0, 'meshy': 1}
    assert final['allowed_actions'] == ['recover']
    monkeypatch.setattr(c, '_execute', execute)
    await c.start(job['id'], final['version'], action='recover')
    final = await completed(c, job['id'])
    assert final['status'] == 'waiting'
    assert c.meshy.submits == ['generate', 'texture']
    assert c.blender.calls == list(STAGES)


@pytest.mark.asyncio
async def test_raw_meshy_response_commit_gap_is_recovered_without_post(harness):
    class RawReplyMeshy(FakeMeshy):
        async def submit(self, stage, references, model_path, receipt_dir, cancel):
            if stage != 'generate':
                return await super().submit(stage, references, model_path, receipt_dir, cancel)
            self.submits.append(stage)
            atomic_json(receipt_dir / 'submit_request.json', {'stage': stage, 'fake': True})
            atomic_json(receipt_dir / 'submit_response.json', {'result': 'raw-confirmed-task'})
            raise RuntimeError('Synthetic stop before task receipt write')
    c = harness
    c.meshy = RawReplyMeshy()
    job = await draft(c)
    await c.start(job['id'], job['version'])
    failed = await completed(c, job['id'])
    assert failed['status'] == 'failed' and failed['allowed_actions'] == ['recover']
    op = c.store.load(job['id'])['operations'][-1]
    assert op['task_id'] == 'raw-confirmed-task'
    original_response = Path(op['attempts'][0]) / 'provider' / 'submit_response.json'
    original_hash = sha(original_response)
    assert not original_response.with_name('task_receipt.json').exists()
    await c.start(job['id'], failed['version'], action='recover')
    final = await completed(c, job['id'])
    assert final['status'] == 'waiting' and final['calls'] == {'astra': 3, 'meshy': 2}
    assert c.meshy.submits == ['generate', 'texture']
    assert c.meshy.polls[0] == 'raw-confirmed-task'
    assert sha(original_response) == original_hash


@pytest.mark.asyncio
async def test_complete_astra_response_commit_gap_materializes_saved_script(harness):
    class ResponseOnlyAstra(FakeAstra):
        async def edit(self, stage, images, context, receipt_dir, cancel):
            if stage != 'lenses':
                return await super().edit(stage, images, context, receipt_dir, cancel)
            self.calls.append({'stage': stage, 'images': [sha(p) for p in images], 'context': context})
            atomic_json(receipt_dir / 'astra_response.json', {'status': 'completed', 'output': [
                {'type': 'custom_tool_call', 'name': 'run_blender_python', 'input': 'import bpy\n'}]})
            raise RuntimeError('Synthetic stop before Python file materialization')
    c = harness
    c.astra = ResponseOnlyAstra()
    job = await draft(c)
    await c.start(job['id'], job['version'])
    failed = await completed(c, job['id'])
    assert failed['status'] == 'failed' and failed['allowed_actions'] == ['recover']
    op = c.store.load(job['id'])['operations'][-1]
    saved_script = Path(op['script']['path'])
    assert saved_script.name == 'recovered_script.py' and saved_script.read_text() == 'import bpy\n'
    response = saved_script.with_name('astra_response.json')
    original_hash = sha(response)
    await c.start(job['id'], failed['version'], action='recover')
    final = await completed(c, job['id'])
    assert final['status'] == 'waiting' and final['calls'] == {'astra': 3, 'meshy': 2}
    assert [call['stage'] for call in c.astra.calls] == ['lenses', 'connections', 'finish']
    assert sha(response) == original_hash


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,payload", [
    ('astra_response.json', '{broken'),
    ('astra_response.json', '{"status":"incomplete","output":[]}'),
    ('astra_response.json', '[]'),
    ('task_receipt.json', '[]'),
    ('submit_response.json', 'null'),
])
async def test_malformed_receipt_restart_keeps_setup_and_other_jobs_available(harness, kind, payload):
    c = harness
    job = await draft(c)
    op_id = await c._new_operation(job['id'], 'generate')
    internal = c.store.load(job['id'])
    folder = c.store.directory(job['id']) / 'operations' / op_id / 'saved'
    (folder / 'provider').mkdir(parents=True)
    (folder / 'provider' / kind).write_text(payload, encoding='utf-8')
    internal['operations'][-1].update(attempts=[str(folder)], dispatch_started=True)
    internal['status'] = 'running'
    c.store.save(internal)
    other = await draft(c)
    c.lock.close()
    await c.open()
    stopped = c.store.load(job['id'])
    assert stopped['status'] == 'interrupted'
    assert stopped['operations'][-1]['receipt_error']
    assert c.store.load(other['id'])['status'] == 'draft'
    assert not c.meshy.submits and not c.astra.calls
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(c)), base_url='http://127.0.0.1:8060') as http:
        assert (await http.get('/api/health')).status_code == 200
        assert (await http.get('/api/jobs/' + other['id'])).status_code == 200


@pytest.mark.asyncio
async def test_runtime_change_between_stages_blocks_next_paid_dispatch(harness, monkeypatch):
    c = harness
    execute = c._execute
    async def change_runtime_after_adoption(identifier, operation_id):
        await execute(identifier, operation_id)
        monkeypatch.setattr(controller_module, 'runtime_manifest', lambda: {'synthetic_changed_file.py': 'changed'})
    monkeypatch.setattr(c, '_execute', change_runtime_after_adoption)
    job = await draft(c)
    await c.start(job['id'], job['version'])
    stopped = await completed(c, job['id'])
    assert stopped['status'] == 'failed' and 'Restart' in stopped['error']
    assert stopped['calls'] == {'astra': 0, 'meshy': 1}
    assert c.meshy.submits == ['generate'] and not c.astra.calls
    assert c.blender.calls == ['generate']
    with pytest.raises(Conflict):
        await c.start(job['id'], stopped['version'], action='recover')


@pytest.mark.asyncio
async def test_shutdown_cancels_owned_native_work_and_releases_store(harness):
    class BlockingBlender(FakeBlender):
        def __init__(self):
            super().__init__()
            self.entered = asyncio.Event()
            self.cancelled = False
        async def run(self, action, input_path, output_dir, *, cancel=None, **kwargs):
            self.calls.append(kwargs['stage'])
            self.entered.set()
            await cancel.wait()
            self.cancelled = True
            raise asyncio.CancelledError()
    c = harness
    c.blender = BlockingBlender()
    job = await draft(c)
    await c.start(job['id'], job['version'])
    await asyncio.wait_for(c.blender.entered.wait(), 5)
    await asyncio.wait_for(c.close(), 5)
    saved = c.store.load(job['id'])
    assert c.blender.cancelled and c.task.done() and c.active_job_id is None
    assert saved['status'] == 'interrupted' and saved['calls'] == {'astra': 0, 'meshy': 1}
    assert c.meshy.submits == ['generate'] and not c.astra.calls
    lock = ServiceLock(c.settings.data_dir)
    lock.acquire()
    lock.close()


@pytest.mark.asyncio
async def test_settings_replacement_is_atomic_local_and_redacted(harness, monkeypatch):
    c = harness
    settings_root = c.settings.data_dir / 'settings'
    settings_root.mkdir()
    monkeypatch.setattr(server_module, 'ROOT', settings_root)
    old_bytes = b'OPENAI_API_KEY=original-openai\nMESHY_API_KEY=original-meshy\n'
    key_path = settings_root / '.env'
    key_path.write_bytes(old_bytes)
    old_providers = (c.astra, c.meshy)
    real_replace = server_module.os.replace
    def fail_only_settings_replace(source, target):
        if Path(target) == key_path:
            raise OSError('Synthetic disk failure before atomic replacement')
        return real_replace(source, target)
    monkeypatch.setattr(server_module.os, 'replace', fail_only_settings_replace)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(c), raise_app_exceptions=False), base_url='http://127.0.0.1:8060') as http:
        reply = await http.post('/api/settings', json={'openai_key': 'sk-new-openai-secret', 'meshy_key': 'new-meshy-secret'})
        assert reply.status_code == 500
        assert key_path.read_bytes() == old_bytes
        assert (c.astra, c.meshy) == old_providers
        assert c.settings.openai_key == 'fake-openai' and c.settings.meshy_key == 'fake-meshy'
        assert not list(settings_root.glob('.env.*.tmp'))
        monkeypatch.setattr(server_module.os, 'replace', real_replace)
        reply = await http.post('/api/settings', json={'openai_key': 'sk-new-openai-secret', 'meshy_key': 'new-meshy-secret'})
        assert reply.status_code == 200
        assert reply.json()['keys_present'] == {'openai': True, 'meshy': True}
        assert 'sk-new-openai-secret' not in reply.text and 'new-meshy-secret' not in reply.text
        assert key_path.read_text() == 'OPENAI_API_KEY=sk-new-openai-secret\nMESHY_API_KEY=new-meshy-secret\n'
        assert c.astra.key == 'sk-new-openai-secret' and c.meshy.key == 'new-meshy-secret'
        assert not list(settings_root.glob('.env.*.tmp'))
        for invalid in ({'openai_key': 'short'}, {'openai_key': 'key with spaces'}, {'openai_key': 'k-proj-missing-first-character'}, {'openai_key': 'sk-non-ascii-é'}, {'other': 'unknown-key'}):
            denied = await http.post('/api/settings', json=invalid)
            assert denied.status_code == 400
        assert key_path.read_text() == 'OPENAI_API_KEY=sk-new-openai-secret\nMESHY_API_KEY=new-meshy-secret\n'
    assert not old_providers[0].calls and not old_providers[1].submits


@pytest.mark.asyncio
async def test_settings_are_locked_while_a_run_is_active(harness, monkeypatch):
    c = harness
    settings_root = c.settings.data_dir / 'settings'
    settings_root.mkdir()
    monkeypatch.setattr(server_module, 'ROOT', settings_root)
    c.meshy.block = 'submit'
    job = await draft(c)
    await c.start(job['id'], job['version'])
    await asyncio.wait_for(c.meshy.entered.wait(), 5)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(c)), base_url='http://127.0.0.1:8060') as http:
        denied = await http.post('/api/settings', json={'openai_key': 'sk-new-openai-secret'})
        assert denied.status_code == 409
    assert not (settings_root / '.env').exists()
    assert c.settings.openai_key == 'fake-openai'
    await c.cancel(job['id'], c.store.load(job['id'])['version'])
    await completed(c, job['id'])


@pytest.mark.asyncio
async def test_explicit_stream_failure_does_not_recover_earlier_completion(harness):
    c = harness
    job = await draft(c)
    op_id = await c._new_operation(job['id'], 'generate')
    internal = c.store.load(job['id'])
    folder = c.store.directory(job['id']) / 'operations' / op_id / 'saved'
    (folder / 'provider').mkdir(parents=True)
    atomic_json(folder / 'provider' / 'astra_response.json', {
        'status': 'completed', 'output': [{'type': 'custom_tool_call', 'name': 'run_blender_python', 'input': 'import bpy\n'}]})
    atomic_json(folder / 'provider' / 'astra_failure.json', {'type': 'multiple_completed_responses'})
    internal['operations'][-1].update(attempts=[str(folder)], dispatch_started=True)
    internal['status'] = 'running'
    c.store.save(internal)
    c.lock.close()
    await c.open()
    stopped = c.store.load(job['id'])
    assert stopped['status'] == 'interrupted'
    assert 'script' not in stopped['operations'][-1]
    assert c.public(stopped)['allowed_actions'] == []
    assert not (folder / 'provider' / 'recovered_script.py').exists()
