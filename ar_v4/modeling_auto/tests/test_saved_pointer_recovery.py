"""A saved pointer-identity edit is recovered once, without a new Astra call."""
from pathlib import Path

import httpx
import pytest

from app.providers import ProviderError
from app.server import create_app
from app.storage import atomic_json, sha
from test_controller import FakeAstra, FakeBlender, completed, harness
from test_test_pipeline import test_draft

SCRIPT = '''import bpy
processed = set()
for obj in bpy.data.objects:
    if obj.type == 'MESH':
        mesh = obj.data
        if mesh.as_pointer() in processed:
            continue
        processed.add(mesh.as_pointer())
'''


class PreviouslyRejectedPointer(FakeAstra):
    async def edit(self, stage, images, context, receipt_dir, cancel):
        if stage != 'lenses':
            return await super().edit(stage, images, context, receipt_dir, cancel)
        self.calls.append({'stage': stage, 'images': [sha(p) for p in images], 'context': context})
        atomic_json(receipt_dir / 'astra_response.json', {
            'status': 'completed', 'output': [{'type': 'custom_tool_call',
                'name': 'run_blender_python', 'input': SCRIPT}],
        })
        (receipt_dir / 'astra_script.py').write_text(SCRIPT, encoding='utf-8')
        raise ProviderError("Astra Python failed local validation: Attribute 'as_pointer' is unavailable to the Blender editor")


class ScriptRecordingBlender(FakeBlender):
    def __init__(self):
        super().__init__()
        self.scripts = []

    async def run(self, action, input_path, output_dir, **kwargs):
        if action == 'edit':
            self.scripts.append(kwargs['script'])
        return await super().run(action, input_path, output_dir, **kwargs)


@pytest.mark.asyncio
async def test_saved_lens_pointer_response_recovers_exactly_once_then_completes_test_plan(harness):
    c = harness
    c.astra, c.blender = PreviouslyRejectedPointer(), ScriptRecordingBlender()
    job = await test_draft(c)
    await c.start(job['id'], job['version'])
    failed = await completed(c, job['id'])
    assert failed['stage'] == 'lenses' and failed['current']['id'] == 'r000'
    assert failed['calls'] == {'astra': 1, 'meshy': 1}
    assert c.blender.calls == ['generate']
    assert failed['allowed_actions'] == ['recover'] and failed['recovery_kind'] == 'astra_script'
    saved = c.store.load(job['id'])
    script = Path(saved['operations'][-1]['script']['path'])
    response = script.with_name('astra_response.json')
    original = {p: sha(p) for p in (script, response, Path(saved['current']['blend_path']))}
    c.lock.close()
    await c.open()
    assert len(c.astra.calls) == 1 and c.active_job_id is None
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(c)), base_url='http://127.0.0.1:8060') as http:
        reply = await http.post(f"/api/jobs/{job['id']}/recover", json={'version': failed['version']})
        assert reply.status_code == 200, reply.text
        final = await completed(c, job['id'])
        duplicate = await http.post(f"/api/jobs/{job['id']}/recover", json={'version': failed['version']})
        assert duplicate.status_code == 409
    assert final['status'] == 'waiting' and final['stage'] == 'review'
    assert final['calls'] == {'astra': 4, 'meshy': 2}
    assert [call['stage'] for call in c.astra.calls] == ['lenses', 'connections', 'finish', 'finish_refine']
    assert c.blender.calls == ['generate', 'lenses', 'connections', 'texture', 'finish', 'finish']
    assert c.blender.scripts[0] == SCRIPT and c.blender.scripts.count(SCRIPT) == 1
    assert all(sha(path) == checksum for path, checksum in original.items())
    assert final['recovery_kind'] is None and final['current']['id'] == 'r005'
