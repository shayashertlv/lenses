"""Separate six-call trial workflow; all providers and geometry here are synthetic."""
import asyncio
import copy
from pathlib import Path

import pytest

from app.controller import Conflict, STAGES
from app.storage import sha
from app.workflows import STANDARD_STAGES
from test_auth_retry import RejectedAstra
from test_controller import ANGLES, FakeAstra, FakeBlender, completed, draft, harness, photos


class DistinctBlender(FakeBlender):
    """Make every revision distinct so stale model/image pairing is detectable."""
    def __init__(self):
        super().__init__()
        self.inputs = []
        self.cancel_call = None

    async def run(self, action, input_path, output_dir, **kwargs):
        self.inputs.append(str(input_path))
        result = await super().run(action, input_path, output_dir, **kwargs)
        for path in output_dir.iterdir():
            path.write_bytes(path.read_bytes() + f'-revision-{len(self.calls)}'.encode())
        result['hashes'] = {p.name: sha(p) for p in output_dir.iterdir()}
        if len(self.calls) == self.cancel_call:
            kwargs['cancel'].set()
        return result


async def test_draft(c):
    return await c.create('Six-call synthetic test', 'Keep original green frame',
                          {'frame_width': 140, 'lens_width': 50, 'lens_height': 40},
                          photos(), pipeline='standard')


# This helper's name describes a product pipeline, not a pytest case.
test_draft.__test__ = False


async def review(c):
    job = await test_draft(c)
    await c.start(job['id'], job['version'])
    return await completed(c, job['id'])


@pytest.mark.asyncio
async def test_trial_runs_all_six_stages_and_refreshes_each_post_texture_input(harness):
    c = harness
    c.blender = DistinctBlender()
    final = await review(c)
    saved = c.store.load(final['id'])
    assert (final['status'], final['stage']) == ('waiting', 'review')
    assert final['pipeline'] == 'standard' and final['pipeline_stages'] == list(STANDARD_STAGES)
    assert '2 Meshy requests and 4 Astra requests' in final['run_disclosure']
    assert final['calls'] == {'meshy': 2, 'astra': 4}
    assert [o['stage'] for o in saved['operations']] == list(STANDARD_STAGES)
    assert [r['stage'] for r in final['revisions']] == list(STANDARD_STAGES)
    assert c.meshy.submits == ['generate', 'texture']
    assert c.blender.calls == [*STAGES, 'finish']
    assert [x['stage'] for x in c.astra.calls] == ['lenses', 'connections', 'finish', 'finish_refine']
    assert [len(x['images']) for x in c.astra.calls] == [5, 6, 11, 11]
    originals = [sha(saved['reference_paths'][a]) for a in ANGLES]
    for call, previous in zip(c.astra.calls[2:], saved['revisions'][3:5]):
        expected = [sha(previous['proof_paths'][a]) for a in ANGLES] + [sha(previous['closeup_path'])]
        assert call['images'] == originals + expected
        assert call['context']['model_sha256'] == previous['master_sha256']
        assert call['context']['pipeline'] == 'standard'
    assert c.astra.calls[2]['images'][5:] != c.astra.calls[3]['images'][5:]
    assert c.blender.inputs[4:6] == [r['blend_path'] for r in saved['revisions'][3:5]]
    assert all(sha(r['blend_path']) == r['master_sha256'] for r in saved['revisions'])


@pytest.mark.asyncio
@pytest.mark.parametrize('legacy', [False, True])
async def test_current_and_legacy_jobs_keep_original_five_call_plan(harness, legacy):
    c = harness
    job = await draft(c)
    if legacy:
        saved = c.store.load(job['id'])
        saved.pop('pipeline')
        c.store.save(saved)
    public = c.public(c.store.load(job['id']))
    assert public['pipeline'] == 'legacy' and public['pipeline_stages'] == list(STAGES)
    assert '3 Astra requests' in public['run_disclosure']
    await c.start(job['id'], job['version'])
    final = await completed(c, job['id'])
    assert final['calls'] == {'meshy': 2, 'astra': 3}
    assert [len(x['images']) for x in c.astra.calls] == [5, 6, 5]
    saved = c.store.load(job['id'])
    if legacy:
        assert 'pipeline' not in saved
    path = c.store.directory(job['id']) / 'job.json'
    before = path.read_bytes()
    c.public(saved)
    c.lock.close()
    await c.open()
    assert path.read_bytes() == before


@pytest.mark.asyncio
@pytest.mark.parametrize('pipeline', ['', 'TEST', 'unknown', None, 0, [], {}])
async def test_unknown_pipeline_is_rejected_without_creating_job(harness, pipeline):
    c = harness
    before = list(c.store.all())
    with pytest.raises(ValueError, match='Pipeline'):
        await c.create('Synthetic', '', {'frame_width': 140, 'lens_width': 50, 'lens_height': 40}, photos(), pipeline=pipeline)
    assert list(c.store.all()) == before
    assert not c.meshy.submits and not c.astra.calls


@pytest.mark.asyncio
async def test_feedback_is_required_preserves_original_notes_and_runs_only_one_new_pass(harness):
    c = harness
    c.blender = DistinctBlender()
    final = await review(c)
    saved = copy.deepcopy(c.store.load(final['id']))
    for instructions in [None, '', ' \n\t', 42, 'x' * 6001]:
        with pytest.raises(ValueError):
            await c.start(final['id'], final['version'], action='edit', notes=instructions)
        assert c.store.load(final['id']) == saved
        assert len(c.astra.calls) == 4
    started = await c.start(final['id'], final['version'], action='edit', notes='  Make the lenses less shiny  ')
    with pytest.raises(Conflict):
        await c.start(final['id'], final['version'], action='edit', notes='Duplicate')
    with pytest.raises(Conflict):
        await c.accept(final['id'], started['version'])
    final = await completed(c, final['id'])
    internal = c.store.load(final['id'])
    assert final['calls'] == {'meshy': 2, 'astra': 5}
    assert final['notes'] == saved['notes'] == 'Keep original green frame'
    assert final['edit_instructions'] == 'Make the lenses less shiny'
    assert internal['authorization']['stages'] == ['finish_refine']
    assert internal['operations'][:6] == saved['operations']
    assert c.astra.calls[-1]['stage'] == 'finish_refine'
    assert c.blender.calls[-1] == 'finish' and len(c.meshy.submits) == 2
    assert 'Keep original green frame' in c.astra.calls[-1]['context']['notes']
    assert 'Make the lenses less shiny' in c.astra.calls[-1]['context']['notes']
    previous = saved['current']
    assert c.astra.calls[-1]['context']['model_sha256'] == previous['master_sha256']
    assert c.astra.calls[-1]['images'][5:] == [sha(previous['proof_paths'][a]) for a in ANGLES] + [sha(previous['closeup_path'])]
    assert all(sha(r['blend_path']) == r['master_sha256'] for r in saved['revisions'])
    accepted = await c.accept(final['id'], final['version'])
    assert accepted['status'] == 'complete' and accepted['allowed_actions'] == []
    assert accepted['accepted']['sha256'] == sha(internal['current']['blend_path'])
    with pytest.raises(Conflict):
        await c.accept(final['id'], final['version'])
    with pytest.raises(Conflict):
        await c.start(final['id'], accepted['version'], action='edit', notes='Already finished')


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['finish', 'finish_refine'])
async def test_restart_after_post_texture_adoption_continues_only_remaining_stages(harness, monkeypatch, stage):
    c = harness
    c.blender = DistinctBlender()
    execute = c._execute
    async def crash_after_adoption(identifier, operation_id):
        operation_stage = c._operation(c.store.load(identifier))['stage']
        await execute(identifier, operation_id)
        if operation_stage == stage:
            raise RuntimeError('Synthetic process stop after successful adoption')
    monkeypatch.setattr(c, '_execute', crash_after_adoption)
    stopped = await review(c)
    saved = c.store.load(stopped['id'])
    assert stopped['status'] == 'failed' and saved['active_operation'] is None
    assert c._continuation(saved) == ('finish_refine' if stage == 'finish' else 'review')
    assert 'recover' in stopped['allowed_actions']
    before = copy.deepcopy(saved['operations'])
    saved['status'] = 'running'
    c.store.save(saved)
    c.lock.close()
    await c.open()
    reopened = c.public(c.store.load(stopped['id']))
    assert reopened['status'] == 'interrupted' and reopened['calls'] == stopped['calls']
    monkeypatch.setattr(c, '_execute', execute)
    await c.start(stopped['id'], reopened['version'], action='recover')
    final = await completed(c, stopped['id'])
    assert final['status'] == 'waiting' and final['calls'] == {'meshy': 2, 'astra': 4}
    assert c.store.load(stopped['id'])['operations'][:len(before)] == before
    assert c.blender.calls == [*STAGES, 'finish']


@pytest.mark.asyncio
@pytest.mark.parametrize('call', [5, 6])
async def test_cancel_after_native_post_texture_result_recovers_without_reexecuting(harness, call):
    c = harness
    c.blender = DistinctBlender()
    c.blender.cancel_call = call
    stopped = await review(c)
    saved = c.store.load(stopped['id'])
    assert stopped['status'] == 'cancelled' and c._operation(saved)['native_result']
    assert len(saved['revisions']) == call - 1
    c.blender.cancel_call = None
    await c.start(stopped['id'], stopped['version'], action='recover')
    final = await completed(c, stopped['id'])
    assert final['status'] == 'waiting' and final['calls'] == {'meshy': 2, 'astra': 4}
    assert c.blender.calls == [*STAGES, 'finish']
    assert len(c.store.load(stopped['id'])['revisions']) == 6


@pytest.mark.asyncio
async def test_cancel_between_finish_and_refine_reserves_only_extra_stage_on_recovery(harness, monkeypatch):
    c = harness
    original = c._new_operation
    async def cancel_before_extra_stage(identifier, stage):
        if stage == 'finish_refine':
            c.cancel_event.set()
        return await original(identifier, stage)
    monkeypatch.setattr(c, '_new_operation', cancel_before_extra_stage)
    stopped = await review(c)
    assert stopped['status'] == 'cancelled' and stopped['calls'] == {'meshy': 2, 'astra': 3}
    assert c._continuation(c.store.load(stopped['id'])) == 'finish_refine'
    monkeypatch.setattr(c, '_new_operation', original)
    await c.start(stopped['id'], stopped['version'], action='recover')
    final = await completed(c, stopped['id'])
    assert final['status'] == 'waiting' and final['calls'] == {'meshy': 2, 'astra': 4}


@pytest.mark.asyncio
async def test_cancel_feedback_before_reservation_retains_single_pass_and_specific_notes(harness):
    c = harness
    final = await review(c)
    started = await c.start(final['id'], final['version'], action='edit', notes='Reduce blue tint')
    await c.cancel(final['id'], started['version'])
    stopped = await completed(c, final['id'])
    assert stopped['calls'] == final['calls']
    assert c._continuation(c.store.load(final['id'])) == 'finish_refine'
    with pytest.raises(ValueError, match='only with another'):
        await c.start(final['id'], stopped['version'], action='recover', notes='Replace the saved request')
    await c.start(final['id'], stopped['version'], action='recover')
    final = await completed(c, final['id'])
    assert final['calls'] == {'meshy': 2, 'astra': 5} and final['status'] == 'waiting'
    assert 'Reduce blue tint' in c.astra.calls[-1]['context']['notes']


@pytest.mark.asyncio
async def test_saved_extra_astra_script_recovers_with_no_new_provider_request(harness):
    class CancelledResponseAstra(FakeAstra):
        async def edit(self, stage, images, context, receipt_dir, cancel):
            script = await super().edit(stage, images, context, receipt_dir, cancel)
            if stage == 'finish_refine':
                cancel.set()
            return script
    c = harness
    c.astra = CancelledResponseAstra()
    stopped = await review(c)
    saved = c.store.load(stopped['id'])
    op = copy.deepcopy(c._operation(saved))
    assert stopped['status'] == 'cancelled' and stopped['recovery_kind'] == 'astra_script'
    assert op['context_sha256'] and op['context']['pipeline'] == 'standard'
    assert len(op['images']) == 11
    await c.start(stopped['id'], stopped['version'], action='recover')
    final = await completed(c, stopped['id'])
    completed_op = c.store.load(stopped['id'])['operations'][-1]
    assert final['status'] == 'waiting' and final['calls'] == {'meshy': 2, 'astra': 4}
    assert len(c.astra.calls) == 4 and c.blender.calls[-1] == 'finish'
    assert completed_op['context'] == op['context'] and completed_op['context_sha256'] == op['context_sha256']
    assert sha(op['script']['path']) == op['script']['sha256']


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['finish', 'finish_refine'])
async def test_auth_rejection_at_post_texture_stages_keeps_test_plan(harness, stage):
    c = harness
    c.astra = RejectedAstra(stage)
    stopped = await review(c)
    assert stopped['auth_failure'] and stopped['allowed_actions'] == ['retry_auth']
    saved = c.store.load(stopped['id'])
    old_op = copy.deepcopy(saved['operations'][-1])
    receipts = {str(p): sha(p) for p in Path(old_op['attempts'][0]).rglob('*') if p.is_file()}
    c.settings.openai_key = 'corrected-fake-openai'
    c.astra.fail_stage = None
    await c.start(stopped['id'], stopped['version'], action='retry_auth')
    final = await completed(c, stopped['id'])
    assert final['status'] == 'waiting' and final['calls'] == {'meshy': 2, 'astra': 5}
    assert c.meshy.submits == ['generate', 'texture']
    assert c.blender.calls == [*STAGES, 'finish']
    assert c.store.load(stopped['id'])['operations'][len(saved['operations']) - 1] == old_op
    assert all(sha(p) == checksum for p, checksum in receipts.items())


@pytest.mark.asyncio
async def test_extra_finish_failure_never_automatically_retries(harness):
    class AmbiguousExtraAstra(FakeAstra):
        async def edit(self, stage, images, context, receipt_dir, cancel):
            if stage != 'finish_refine':
                return await super().edit(stage, images, context, receipt_dir, cancel)
            self.calls.append({'stage': stage})
            raise RuntimeError('Synthetic unknown network outcome')
    c = harness
    c.astra = AmbiguousExtraAstra()
    stopped = await review(c)
    assert stopped['status'] == 'failed' and stopped['current']['stage'] == 'finish'
    assert stopped['allowed_actions'] == ['edit']
    for _ in range(3):
        assert c.public(c.store.load(stopped['id']))['calls'] == {'meshy': 2, 'astra': 4}
    with pytest.raises(Conflict):
        await c.start(stopped['id'], stopped['version'], action='recover')
    with pytest.raises(ValueError):
        await c.start(stopped['id'], stopped['version'], action='edit')
    assert len(c.astra.calls) == 4
