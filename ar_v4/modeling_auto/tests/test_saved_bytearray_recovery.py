"""Recover an actual missing-builtin failure from a saved synthetic lens script.

The mask code runs in the production restricted builtins. Providers and Blender
artifacts are fakes; this regression uses no native process or paid request.
"""
import copy
from pathlib import Path

import httpx
import pytest

from app.script_validation import safe_builtins
from app.server import create_app
from app.storage import atomic_json, sha
from test_controller import FakeAstra, FakeBlender, completed, harness
from test_test_pipeline import test_draft


MASK_SCRIPT = '''face_mask = bytearray(12)
for index in range(len(face_mask)):
    if index % 3 == 1:
        face_mask[index] = 1
selected_faces = [index for index, marked in enumerate(face_mask) if marked]
assert selected_faces == [1, 4, 7, 10]
'''


class SavedMaskAstra(FakeAstra):
    async def edit(self, stage, images, context, receipt_dir, cancel):
        if stage != 'lenses':
            return await super().edit(stage, images, context, receipt_dir, cancel)
        self.calls.append({'stage': stage, 'images': [sha(p) for p in images],
                           'context': copy.deepcopy(context)})
        atomic_json(receipt_dir / 'astra_response.json', {
            'status': 'completed', 'output': [{'type': 'custom_tool_call',
                'name': 'run_blender_python', 'input': MASK_SCRIPT}],
        })
        (receipt_dir / 'astra_script.py').write_text(MASK_SCRIPT, encoding='utf-8')
        return MASK_SCRIPT


class MaskExecutingBlender(FakeBlender):
    def __init__(self):
        super().__init__()
        self.builtins = safe_builtins()
        self.builtins.pop('bytearray')  # Reproduce the historical runtime namespace.
        self.executions = []
        self.mask_results = []

    async def run(self, action, input_path, output_dir, **kwargs):
        if action == 'edit' and kwargs['stage'] == 'lenses':
            self.executions.append({'script': kwargs['script'], 'input': str(input_path),
                                    'sha256': sha(input_path)})
            namespace = {'__builtins__': self.builtins}
            try:
                exec(kwargs['script'], namespace, namespace)
            except NameError as error:
                self.calls.append('lenses')
                output_dir.mkdir(parents=True)
                (output_dir / 'blender.log').write_text(f'NameError: {error}\n', encoding='utf-8')
                raise
            self.mask_results.append((bytes(namespace['face_mask']), namespace['selected_faces']))
        return await super().run(action, input_path, output_dir, **kwargs)


@pytest.mark.asyncio
async def test_saved_bytearray_lens_script_recovers_after_builtin_fix_without_another_lens_request(harness):
    c = harness
    c.astra, c.blender = SavedMaskAstra(), MaskExecutingBlender()
    job = await test_draft(c)
    await c.start(job['id'], job['version'])
    failed = await completed(c, job['id'])
    saved = c.store.load(job['id'])
    blank = copy.deepcopy(saved['current'])
    lens_op = copy.deepcopy(saved['operations'][-1])
    script = Path(lens_op['script']['path'])
    failed_attempt = Path(lens_op['attempts'][0])
    assert (failed['status'], failed['stage'], failed['error']) == (
        'failed', 'lenses', "name 'bytearray' is not defined")
    assert failed['current']['id'] == 'r000' and failed['current']['stage'] == 'generate'
    assert failed['calls'] == {'astra': 1, 'meshy': 1}
    assert failed['allowed_actions'] == ['recover'] and failed['recovery_kind'] == 'astra_script'
    assert c.blender.calls == ['generate', 'lenses'] and c.blender.mask_results == []
    assert script.read_text(encoding='utf-8') == MASK_SCRIPT
    assert lens_op['script']['sha256'] == sha(script)
    assert not lens_op.get('native_result') and len(saved['revisions']) == 1
    assert not (failed_attempt / 'revision' / 'master.blend').exists()
    assert 'bytearray' in (failed_attempt / 'revision' / 'blender.log').read_text(encoding='utf-8')
    original_files = {path: sha(path) for path in c.store.directory(job['id']).rglob('*')
                      if path.is_file() and path.name != 'job.json'}

    # Apply only the builtin correction. Loading this saved job must remain idle.
    c.blender.builtins = safe_builtins()
    c.lock.close()
    await c.open()
    assert c.active_job_id is None
    assert len(c.astra.calls) == len(c.blender.executions) == 1
    assert c.store.load(job['id'])['current'] == blank
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(c)),
                                 base_url='http://127.0.0.1:8060') as http:
        for _ in range(2):
            fetched = await http.get(f"/api/jobs/{job['id']}")
            assert fetched.status_code == 200
            assert fetched.json()['calls'] == {'astra': 1, 'meshy': 1}
        assert len(c.astra.calls) == len(c.blender.executions) == 1
        version = fetched.json()['version']
        reply = await http.post(f"/api/jobs/{job['id']}/recover", json={'version': version})
        assert reply.status_code == 200, reply.text
        assert reply.json()['current']['id'] == 'r000'
        duplicate = await http.post(f"/api/jobs/{job['id']}/recover", json={'version': version})
        assert duplicate.status_code == 409
        final = await completed(c, job['id'])

    completed_job = c.store.load(job['id'])
    completed_lenses = completed_job['operations'][1]
    assert (final['status'], final['stage'], final['current']['id']) == ('waiting', 'review', 'r005')
    assert final['calls'] == {'astra': 4, 'meshy': 2} and final['recovery_kind'] is None
    assert [call['stage'] for call in c.astra.calls] == ['lenses', 'connections', 'finish', 'finish_refine']
    assert c.meshy.submits == ['generate', 'texture']
    assert c.blender.calls == ['generate', 'lenses', 'lenses', 'connections', 'texture', 'finish', 'finish']
    assert c.blender.mask_results == [(bytes([0, 1, 0] * 4), [1, 4, 7, 10])]
    assert c.blender.executions == [
        {'script': MASK_SCRIPT, 'input': blank['blend_path'], 'sha256': blank['master_sha256']},
        {'script': MASK_SCRIPT, 'input': blank['blend_path'], 'sha256': blank['master_sha256']},
    ]
    assert completed_lenses['id'] == lens_op['id'] and completed_lenses['status'] == 'complete'
    assert completed_lenses['script'] == lens_op['script']
    assert completed_lenses['attempts'][0] == str(failed_attempt)
    assert len(completed_lenses['attempts']) == 2 and len(completed_job['operations']) == 6
    assert completed_job['revisions'][0] == blank
    assert all(sha(path) == checksum for path, checksum in original_files.items())
