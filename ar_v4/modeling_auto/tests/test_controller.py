import asyncio
from io import BytesIO
import json
import uuid
from pathlib import Path

import httpx
from PIL import Image
import pytest
import pytest_asyncio

from app.config import ROOT, Settings
from app.controller import ANGLES, Conflict, Controller, runtime_manifest
from app.server import create_app
from app.storage import atomic_json, sha

def photos():
    output = {}
    for index, angle in enumerate(ANGLES):
        stream = BytesIO()
        Image.new('RGB', (96, 96), (index * 40, 90, 180)).save(stream, 'JPEG')
        output[angle] = stream.getvalue()
    return output

class FakeMeshy:
    def __init__(self):
        self.submits = []; self.polls = []; self.block = None; self.entered = asyncio.Event()
        self.fail = None
    async def close(self): pass
    async def submit(self, stage, references, model_path, receipt_dir, cancel):
        self.submits.append(stage)
        atomic_json(receipt_dir / 'submit_request.json', {'stage': stage, 'fake': True})
        self.entered.set()
        if self.block == 'submit':
            await cancel.wait(); raise asyncio.CancelledError()
        if self.fail == 'ambiguous': raise RuntimeError('Unknown submission result')
        task_id = 'fake-' + str(len(self.submits))
        atomic_json(receipt_dir / 'task_receipt.json', {'task_id': task_id, 'stage': stage})
        if self.fail == 'after-receipt': raise RuntimeError('Simulated commit gap')
        return task_id
    async def poll(self, stage, task_id, receipt_dir, cancel):
        self.polls.append(task_id); self.entered.set()
        if self.block == 'poll':
            await cancel.wait(); raise asyncio.CancelledError()
        return {'id': task_id, 'status': 'SUCCEEDED'}
    async def download(self, remote, receipt_dir, cancel):
        path = receipt_dir / 'source.glb'; path.write_bytes(b'new fake asset ' + remote['id'].encode())
        atomic_json(receipt_dir / 'download_receipt.json', {'task_id': remote['id'], 'sha256': sha(path)})
        return path

class FakeAstra:
    def __init__(self): self.calls = []; self.bad = False
    async def close(self): pass
    async def edit(self, stage, images, context, receipt_dir, cancel):
        self.calls.append({'stage': stage, 'images': [sha(p) for p in images], 'context': context})
        script = 'import bpy\n' if not self.bad else 'import os\n'
        (receipt_dir / 'astra_script.py').write_text(script, encoding='utf-8')
        atomic_json(receipt_dir / 'astra_response.json', {'status': 'completed', 'output': [
            {'type': 'custom_tool_call', 'name': 'run_blender_python', 'input': script}]})
        if self.bad: raise ValueError('Saved script failed local validation')
        return script

class FakeBlender:
    def __init__(self): self.calls = []; self.fail_stage = None; self.cancel_on_stage = None
    async def run(self, action, input_path, output_dir, *, script=None, stage=None, dimensions=None, cancel=None):
        self.calls.append(stage)
        if stage == self.fail_stage: raise ValueError('Synthetic native failure')
        output_dir.mkdir(parents=True)
        result = {'proofs': {}, 'inspection': {'geometry_sha256': 'fixture-geometry', 'synthetic': True}, 'hashes': {}}
        for key, name in [('blend_path', 'master.blend'), ('model_path', 'model.glb'), ('closeup_path', 'closeup.png')]:
            path = output_dir / name; path.write_bytes((stage + name).encode()); result[key] = str(path)
        for angle in ANGLES:
            path = output_dir / (angle + '.png'); path.write_bytes((stage + angle).encode()); result['proofs'][angle] = str(path)
        for path in output_dir.iterdir(): result['hashes'][path.name] = sha(path)
        if stage == self.cancel_on_stage: cancel.set()
        return result

@pytest_asyncio.fixture
async def harness(tmp_path, monkeypatch):
    # Artifacts and pytest's base temp are explicitly inside modeling_auto/data.
    assert tmp_path.resolve().is_relative_to(ROOT)
    monkeypatch.setattr('app.controller.LOADED_RUNTIME', runtime_manifest())
    short_folder = tmp_path.parent / ('c' + uuid.uuid4().hex[:8])
    c = Controller(Settings(data_dir=short_folder, openai_key='fake-openai', meshy_key='fake-meshy',
                            blender_path=__file__), meshy=FakeMeshy(), astra=FakeAstra(), blender=FakeBlender())
    await c.open()
    try: yield c
    finally: await c.close()

async def draft(c):
    return await c.create('New isolated fixture', 'Preserve source',
                          {'frame_width': 140, 'lens_width': 50, 'lens_height': 40}, photos())

async def completed(c, identifier):
    await asyncio.wait_for(c.task, 10)
    return c.public(c.store.load(identifier))

@pytest.mark.asyncio
async def test_full_automatic_flow_then_only_finish_loop_and_exact_accept(harness):
    c = harness; job = await draft(c)
    await c.start(job['id'], job['version']); final = await completed(c, job['id'])
    assert (final['status'], final['stage']) == ('waiting', 'review')
    assert final['calls'] == {'astra': 3, 'meshy': 2}
    assert c.meshy.submits == ['generate', 'texture']
    assert c.blender.calls == ['generate', 'lenses', 'connections', 'texture', 'finish']
    assert [len(x['images']) for x in c.astra.calls] == [5, 6, 5]
    assert c.astra.calls[0]['images'] == c.astra.calls[2]['images']
    assert c.astra.calls[0]['context']['inspection']['synthetic']
    assert c.astra.calls[0]['context']['dimensions']['frame_width'] == 140
    assert c.astra.calls[0]['context']['notes'] == 'Preserve source'
    original = {r['id']: r['master_sha256'] for r in c.store.load(job['id'])['revisions']}
    await c.start(job['id'], final['version'], action='edit', notes='Less shiny')
    final = await completed(c, job['id'])
    assert final['calls'] == {'astra': 4, 'meshy': 2}
    assert c.blender.calls[-1] == 'finish' and len(c.meshy.submits) == 2
    assert c.astra.calls[-1]['context']['notes'] == 'Less shiny'
    assert all(sha(r['blend_path']) == original[r['id']] for r in c.store.load(job['id'])['revisions'][:5])
    accepted = await c.accept(job['id'], final['version'])
    assert accepted['status'] == 'complete' and accepted['allowed_actions'] == []
    assert accepted['accepted']['sha256'] == sha(c.store.load(job['id'])['current']['blend_path'])

@pytest.mark.asyncio
async def test_stale_view_and_parallel_start_cannot_duplicate_request(harness):
    c = harness; job = await draft(c); c.meshy.block = 'submit'
    await c.start(job['id'], job['version']); await asyncio.wait_for(c.meshy.entered.wait(), 5)
    with pytest.raises(Conflict): await c.start(job['id'], job['version'])
    other = await draft(c)
    with pytest.raises(Conflict): await c.start(other['id'], other['version'])
    current = c.store.load(job['id']); await c.cancel(job['id'], current['version'])
    final = await completed(c, job['id'])
    assert final['status'] == 'cancelled' and final['calls']['meshy'] == 1
    assert final['allowed_actions'] == [] and not c.astra.calls

@pytest.mark.asyncio
async def test_cancel_poll_recover_existing_task_no_second_paid_submit(harness):
    c = harness; job = await draft(c); c.meshy.block = 'poll'
    await c.start(job['id'], job['version'])
    while not c.meshy.polls: await asyncio.sleep(0)
    await c.cancel(job['id'], c.store.load(job['id'])['version'])
    final = await completed(c, job['id'])
    assert final['allowed_actions'] == ['recover']
    c.meshy.block = None
    await c.start(job['id'], final['version'], action='recover'); final = await completed(c, job['id'])
    assert final['status'] == 'waiting' and c.meshy.submits == ['generate', 'texture']
    assert final['calls'] == {'astra': 3, 'meshy': 2}

@pytest.mark.asyncio
async def test_submission_commit_gap_recovers_durable_task_receipt(harness):
    c = harness; job = await draft(c); c.meshy.fail = 'after-receipt'
    await c.start(job['id'], job['version']); final = await completed(c, job['id'])
    assert final['status'] == 'failed' and final['allowed_actions'] == ['recover']
    c.meshy.fail = None
    await c.start(job['id'], final['version'], action='recover'); final = await completed(c, job['id'])
    assert final['status'] == 'waiting' and c.meshy.submits == ['generate', 'texture']

@pytest.mark.asyncio
async def test_ambiguous_submission_is_not_recoverable_or_retried(harness):
    c = harness; job = await draft(c); c.meshy.fail = 'ambiguous'
    await c.start(job['id'], job['version']); final = await completed(c, job['id'])
    assert final['status'] == 'failed' and final['allowed_actions'] == []
    assert c.meshy.submits == ['generate'] and final['calls']['meshy'] == 1

@pytest.mark.asyncio
async def test_native_failure_recovery_uses_saved_script(harness):
    c = harness; job = await draft(c); c.blender.fail_stage = 'lenses'
    await c.start(job['id'], job['version']); final = await completed(c, job['id'])
    assert final['status'] == 'failed' and len(c.astra.calls) == 1
    c.blender.fail_stage = None
    await c.start(job['id'], final['version'], action='recover'); final = await completed(c, job['id'])
    assert final['status'] == 'waiting' and len(c.astra.calls) == 3
    assert final['calls'] == {'astra': 3, 'meshy': 2}

@pytest.mark.asyncio
async def test_validator_failure_keeps_script_and_failed_accounting(harness):
    c = harness; job = await draft(c); c.astra.bad = True
    await c.start(job['id'], job['version']); final = await completed(c, job['id'])
    assert final['status'] == 'failed' and final['allowed_actions'] == ['recover']
    assert final['calls'] == {'astra': 1, 'meshy': 1}
    assert c.store.load(job['id'])['operations'][-1]['script']['sha256']
    await c.start(job['id'], final['version'], action='recover'); final = await completed(c, job['id'])
    assert final['status'] == 'failed' and len(c.astra.calls) == 1
    assert c.blender.calls == ['generate']

@pytest.mark.asyncio
async def test_completed_native_recovery_checks_every_retained_output(harness):
    c = harness; job = await draft(c); c.blender.cancel_on_stage = 'generate'
    await c.start(job['id'], job['version']); final = await completed(c, job['id'])
    op = c.store.load(job['id'])['operations'][-1]
    result = json.loads(Path(op['native_result']['path']).read_text())
    Path(result['proofs']['front']).write_bytes(b'tampered')
    await c.start(job['id'], final['version'], action='recover'); final = await completed(c, job['id'])
    assert final['status'] == 'failed' and 'integrity' in final['error']
    assert c.blender.calls == ['generate'] and c.meshy.submits == ['generate']

@pytest.mark.asyncio
async def test_restart_marks_running_interrupted_and_does_not_dispatch(harness):
    c = harness; job = await draft(c)
    internal = c.store.load(job['id']); internal['status'] = 'running'; c.store.save(internal)
    c.lock.close(); await c.open()
    assert c.store.load(job['id'])['status'] == 'interrupted'
    assert not c.meshy.submits and not c.astra.calls

@pytest.mark.asyncio
async def test_service_lock_blocks_second_controller(harness):
    from app.storage import ServiceLock
    lock = ServiceLock(harness.settings.data_dir)
    with pytest.raises(RuntimeError): lock.acquire()

@pytest.mark.asyncio
async def test_http_upload_start_review_and_download_and_origin_checks(harness):
    c = harness
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(c)), base_url='http://127.0.0.1:8060') as http:
        files = {a: (a + '.jpg', v, 'image/jpeg') for a, v in photos().items()}
        data = {'name': 'HTTP Fixture', 'notes': '', 'dimensions': json.dumps({'frame_width': 140, 'lens_width': 50, 'lens_height': 40})}
        response = await http.post('/api/jobs', data=data, files=files)
        assert response.status_code == 200, response.text
        job = response.json()
        denied = await http.post('/api/jobs/' + job['id'] + '/start', json={'version': job['version']}, headers={'origin': 'https://foreign.example'})
        assert denied.status_code == 403 and not c.meshy.submits
        assert (await http.get('/api/health', headers={'host': 'foreign.example'})).status_code == 403
        assert (await http.post('/api/jobs/' + job['id'] + '/start', content='x')).status_code == 415
        assert (await http.post('/api/jobs/' + job['id'] + '/start', json={'version': job['version']})).status_code == 200
        final = await completed(c, job['id'])
        accepted = await http.post('/api/jobs/' + job['id'] + '/accept', json={'version': final['version']})
        assert accepted.status_code == 200
        artifact = await http.get(accepted.json()['accepted']['url'])
        assert artifact.status_code == 200 and 'attachment' in artifact.headers['content-disposition']
        assert artifact.content == Path(c.store.load(job['id'])['current']['blend_path']).read_bytes()

@pytest.mark.asyncio
async def test_input_tamper_blocks_next_paid_call(harness):
    c = harness; job = await draft(c)
    Path(c.store.load(job['id'])['reference_paths']['front']).write_bytes(b'tampered')
    with pytest.raises(ValueError): await c.start(job['id'], job['version'])
    assert not c.meshy.submits

@pytest.mark.asyncio
async def test_missing_keys_server_can_boot_without_network(tmp_path):
    c = Controller(Settings(data_dir=tmp_path))
    await c.open()
    assert c.health()['keys_present'] == {'openai': False, 'meshy': False}
    assert not c.health()['ready']
    await c.close()
