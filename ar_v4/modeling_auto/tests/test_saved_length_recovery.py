"""A completed paid script rejected by the old validator is reused locally."""
from pathlib import Path

import httpx
import pytest

from app.providers import ProviderError
from app.server import create_app
from app.storage import atomic_json, sha
from test_controller import FakeAstra, completed, draft, harness

SCRIPT = 'value = (0.2, 0.3, 0.4, 1.0)\ncolor = tuple(value) if hasattr(value, "__len__") else value\n'


class PreviouslyRejectedLengthQuery(FakeAstra):
    async def edit(self, stage, images, context, receipt_dir, cancel):
        if stage != 'connections':
            return await super().edit(stage, images, context, receipt_dir, cancel)
        self.calls.append({'stage': stage, 'images': [sha(p) for p in images], 'context': context})
        atomic_json(receipt_dir / 'astra_response.json', {
            'status': 'completed', 'output': [{'type': 'custom_tool_call',
                'name': 'run_blender_python', 'input': SCRIPT}],
        })
        (receipt_dir / 'astra_script.py').write_text(SCRIPT, encoding='utf-8')
        raise ProviderError("Astra Python failed local validation: Attribute '__len__' is outside the Blender editing contract")


@pytest.mark.asyncio
async def test_saved_rejected_script_recovers_without_paid_repeat_and_keeps_shown_revision(harness):
    c = harness
    c.astra = PreviouslyRejectedLengthQuery()
    job = await draft(c)
    await c.start(job['id'], job['version'])
    failed = await completed(c, job['id'])
    assert failed['stage'] == 'connections' and failed['current']['stage'] == 'lenses'
    assert failed['current']['id'] == 'r001' and failed['allowed_actions'] == ['recover']
    assert failed['recovery_kind'] == 'astra_script'
    saved = c.store.load(job['id'])
    source = saved['current']['blend_path']
    source_sha = sha(source)
    op = saved['operations'][-1]
    script_path = Path(op['script']['path'])
    original_script = script_path.read_bytes()
    response = script_path.with_name('astra_response.json')
    original_response = response.read_bytes()
    c.lock.close()
    await c.open()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(c)), base_url='http://127.0.0.1:8060') as http:
        stopped = (await http.get('/api/jobs/' + job['id'])).json()
        assert stopped['recovery_kind'] == 'astra_script' and len(c.astra.calls) == 2
        reply = await http.post('/api/jobs/' + job['id'] + '/recover', json={'version': stopped['version']})
        assert reply.status_code == 200
    final = await completed(c, job['id'])
    assert final['status'] == 'waiting' and final['stage'] == 'review'
    assert final['calls'] == {'astra': 3, 'meshy': 2}
    assert [call['stage'] for call in c.astra.calls] == ['lenses', 'connections', 'finish']
    assert c.meshy.submits == ['generate', 'texture']
    assert c.blender.calls == ['generate', 'lenses', 'connections', 'texture', 'finish']
    assert sha(source) == source_sha
    assert script_path.read_bytes() == original_script and response.read_bytes() == original_response
    assert final['recovery_kind'] is None
