"""Timed-out native finish attempts reuse their saved Astra response and r003.

The providers, geometry and timeout are synthetic. Actual native timing and
geometry protection are covered separately; these cases make no paid requests.
"""
import copy
from pathlib import Path

import httpx
import pytest

from app.blender_runner import BlenderError
from app.controller import ANGLES
from app.server import create_app
from app.storage import atomic_json, sha
from test_controller import FakeAstra, completed, harness
from test_test_pipeline import DistinctBlender, test_draft


FINISH_SCRIPT = '''import bpy
for material in bpy.data.materials:
    material.diffuse_color = (0.25, 0.35, 0.2, 1.0)
'''


class SavedFinishAstra(FakeAstra):
    async def edit(self, stage, images, context, receipt_dir, cancel):
        if stage != 'finish':
            return await super().edit(stage, images, context, receipt_dir, cancel)
        self.calls.append({'stage': stage, 'images': [sha(p) for p in images],
                           'context': copy.deepcopy(context)})
        atomic_json(receipt_dir / 'astra_response.json', {
            'status': 'completed', 'output': [{'type': 'custom_tool_call',
                'name': 'run_blender_python', 'input': FINISH_SCRIPT}],
        })
        (receipt_dir / 'astra_script.py').write_text(FINISH_SCRIPT, encoding='utf-8')
        return FINISH_SCRIPT


class TimedOutFinishBlender(DistinctBlender):
    def __init__(self, timeouts):
        super().__init__()
        self.timeouts = timeouts
        self.executions = []
        self.partial_outputs = []

    async def run(self, action, input_path, output_dir, **kwargs):
        if action == 'edit':
            self.executions.append({'input': str(input_path), 'sha256': sha(input_path),
                                    'script': kwargs['script']})
        if action == 'edit' and kwargs['script'] == FINISH_SCRIPT and self.timeouts:
            self.timeouts -= 1
            self.calls.append(kwargs['stage'])
            self.inputs.append(str(input_path))
            output_dir.mkdir(parents=True)
            # A partial output from the failed child must never replace r003.
            partial = output_dir / 'master.blend'
            partial.write_bytes(b'SYNTHETIC INCOMPLETE FINISH; MUST NOT BE ADOPTED')
            (output_dir / 'blender.log').write_text('Synthetic native render timeout\n', encoding='utf-8')
            self.partial_outputs.append(partial)
            raise BlenderError('Blender exceeded 1200 seconds')
        return await super().run(action, input_path, output_dir, **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize('timeouts', [1, 2])
async def test_timed_out_finish_reuses_exact_script_then_runs_only_authorized_refine(harness, timeouts):
    c = harness
    c.astra = SavedFinishAstra()
    c.blender = TimedOutFinishBlender(timeouts)
    job = await test_draft(c)
    await c.start(job['id'], job['version'])
    stopped = await completed(c, job['id'])
    saved = c.store.load(job['id'])
    original_current = copy.deepcopy(saved['current'])
    original_revisions = copy.deepcopy(saved['revisions'])
    finish_op = copy.deepcopy(saved['operations'][-1])
    exact_script = Path(finish_op['script']['path'])
    assert exact_script.read_text(encoding='utf-8') == FINISH_SCRIPT
    assert sha(exact_script) == finish_op['script']['sha256']
    assert finish_op['stage'] == 'finish' and not finish_op.get('native_result')

    def assert_preserved_failure(state):
        persisted = c.store.load(job['id'])
        assert (state['status'], state['stage'], state['error']) == (
            'failed', 'finish', 'Blender exceeded 1200 seconds')
        assert state['current']['id'] == 'r003' and state['current']['stage'] == 'texture'
        assert persisted['current'] == original_current
        assert persisted['revisions'] == original_revisions
        assert state['calls'] == {'astra': 3, 'meshy': 2}
        assert state['allowed_actions'] == ['recover'] and state['recovery_kind'] == 'astra_script'
        assert [call['stage'] for call in c.astra.calls] == ['lenses', 'connections', 'finish']
        assert c.meshy.submits == ['generate', 'texture']
        assert persisted['operations'][-1]['id'] == finish_op['id']
        assert persisted['operations'][-1]['script'] == finish_op['script']
        assert sha(exact_script) == finish_op['script']['sha256']
        assert all(str(path) not in [item['path'] for item in persisted['artifacts'].values()]
                   for path in c.blender.partial_outputs)

    assert_preserved_failure(stopped)
    retained = {path: sha(path) for path in c.store.directory(job['id']).rglob('*')
                if path.is_file() and path.name != 'job.json'}
    # Reopening and HTTP reads must not execute the saved script or resume calls.
    c.lock.close()
    await c.open()
    assert c.active_job_id is None
    assert len(c.blender.executions) == 3
    assert_preserved_failure(c.public(c.store.load(job['id'])))
    transport = httpx.ASGITransport(app=create_app(c))
    async with httpx.AsyncClient(transport=transport, base_url='http://127.0.0.1:8060') as http:
        for _ in range(timeouts):
            for _ in range(2):
                reply = await http.get(f"/api/jobs/{job['id']}")
                assert reply.status_code == 200
                assert_preserved_failure(reply.json())
            version = reply.json()['version']
            response = await http.post(f"/api/jobs/{job['id']}/recover", json={'version': version})
            assert response.status_code == 200, response.text
            assert response.json()['current']['id'] == 'r003'
            duplicate = await http.post(f"/api/jobs/{job['id']}/recover", json={'version': version})
            assert duplicate.status_code == 409
            stopped = await completed(c, job['id'])
            if stopped['status'] == 'failed':
                assert_preserved_failure(stopped)
                retained.update({path: sha(path) for path in c.store.directory(job['id']).rglob('*')
                                 if path.is_file() and path.name != 'job.json'})

    final = c.store.load(job['id'])
    assert (stopped['status'], stopped['stage'], stopped['current']['id']) == ('waiting', 'review', 'r005')
    assert stopped['calls'] == {'astra': 4, 'meshy': 2}
    assert stopped['recovery_kind'] is None
    assert [call['stage'] for call in c.astra.calls] == ['lenses', 'connections', 'finish', 'finish_refine']
    assert c.meshy.submits == ['generate', 'texture']
    assert final['revisions'][:4] == original_revisions
    assert len(final['operations']) == 6
    recovered_op = final['operations'][4]
    assert recovered_op['id'] == finish_op['id'] and recovered_op['status'] == 'complete'
    assert recovered_op['script'] == finish_op['script']
    assert len(recovered_op['attempts']) == timeouts + 1
    finish_executions = [item for item in c.blender.executions if item['script'] == FINISH_SCRIPT]
    assert len(finish_executions) == timeouts + 1
    assert all(item['input'] == original_current['blend_path']
               and item['sha256'] == original_current['master_sha256'] for item in finish_executions)
    assert all(sha(path) == checksum for path, checksum in retained.items())
    # The only new Astra request uses r004's fresh images/model, never stale r003.
    recovered = final['revisions'][4]
    refine = c.astra.calls[-1]
    originals = [sha(final['reference_paths'][angle]) for angle in ANGLES]
    current_views = [sha(recovered['proof_paths'][angle]) for angle in ANGLES] + [sha(recovered['closeup_path'])]
    assert refine['images'] == originals + current_views
    assert refine['context']['model_sha256'] == recovered['master_sha256']
    assert refine['images'][5:] != c.astra.calls[2]['images'][5:]
    assert c.blender.executions[-1]['input'] == recovered['blend_path']
