"""Scripted shared-stop endings retain private progress in the parent's real draft report."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from glasses3d.agent import approvals as A, delivery as D, job_run, loop as L, replay as R
from glasses3d.agent.temple_lab import stage
from glasses3d.tests.agent import test_loop_stages as fixture
from glasses3d.tests.agent.test_draft_delivery import draft_fixture


@pytest.mark.parametrize('completed', [False, True])
def test_child_reaching_shared_stop_keeps_progress_and_parent_draft(tmp_path, monkeypatch, completed):
    monkeypatch.setattr(L, 'ProductionClients', lambda *a, **k: pytest.fail('paid clients forbidden'))
    stop = '0.01'  # three fixture responses settle $0.01125; no fourth request may start
    monkeypatch.setattr(fixture, 'CHILD', replace(fixture.CHILD, settled_stop_usd=stop,
        context_guard_tokens=None, context_guard_every_requests=None))
    job, spec, parent, child, transport, _ = fixture.setup(tmp_path / 'run', [
        R.tool_call('temple_stage'), R.tool_call('look'),
        R.tool_call('facet_finish' if completed else 'look')])
    spec.settings = replace(spec.settings, settled_stop_usd=stop)
    request = A.build_request(job, instructions_sha256=L.instructions_sha256(spec.instructions),
        tool_registry_sha256=L.tool_registry_sha256(spec.tools, spec.settings),
        settings=L.approval_settings(spec.settings, spec.stage_contracts),
        run_inputs=A.run_input_binding(job, spec.brief, spec.photos))
    spec.run_id = 'shared-stop'
    directory = job / 'runs' / spec.run_id
    A.write_approval_request(directory, job_id=job.name, request=request, estimate={}, suggested_cap_usd=stop)
    A.write_approval(directory, max_usd=stop, owner_words='Offline fixture only')
    spec.current_request_sha256 = A.request_sha256(request)
    original = L.StageRuntime.run_stage
    private_folder = job / 'work' / 'temple_stage' / 'test'
    private_folder.mkdir(parents=True)

    def snapshot():
        return SimpleNamespace(s=SimpleNamespace(head={'id': child.head}), state={
            'heads': [{'checkpoint': child.head, 'revision': child.count}],
            'best_checkpoint': child.head, 'revisions': [{}] * child.count})

    async def with_private_receipt(runtime, *args, **kwargs):
        # The real wrappers use this helper on failure and apply only after accepted validation.
        # Geometry validation/application itself is covered by test_temple_stage's orchestrator tests.
        try:
            outcome = await original(runtime, *args, **kwargs)
        except L.StageRunFailed as error:
            stage.record_failure(error, snapshot(), private_folder, job)
            raise
        outcome['progress'] = stage.progress(snapshot(), private_folder, job)
        if outcome['status'] == 'completed':
            parent.mutate('accepted-private-stage', 'temple_stage_apply')
            outcome['progress']['parent_geometry_unchanged'] = False
        return outcome

    monkeypatch.setattr(L.StageRuntime, 'run_stage', with_private_receipt)
    result = asyncio.run(L.run_astra(spec, scene=parent))
    assert result['status'] == 'budget_limit', result
    assert result['requests'] == len(transport.creates) == 3
    assert not transport.violations and not transport.unexpected
    assert result['budget']['held_micro_usd'] == result['budget']['unknown_micro_usd'] == 0
    assert parent.head == ('c0001' if completed else 'c0000')
    child_outcome = result['stages'][0]
    assert child_outcome['status'] == ('completed' if completed else 'budget_limit')
    assert child_outcome['progress']['revision'] == (1 if completed else 2)
    assert child_outcome['progress']['parent_geometry_unchanged'] is not completed

    # Publish from a real checkpoint index with fake export/AR execution and real strict GLB checks.
    root = tmp_path / 'delivery'
    root.mkdir()
    scene, delivery_result, _, _ = draft_fixture(root, monkeypatch, status=result['status'])
    delivery_result.update(stages=result['stages'], error=result.get('error'))
    out = job_run._publish_result(root, delivery_result['run_id'], scene, delivery_result)
    report = D.load_delivery(root, out['id'])['report_data']
    assert out['status'] == 'delivered' and report['finished'] is report['accepted_finish'] is False
    assert report['checkpoint_selection'] == 'best' and report['checkpoint'] == 'c0000'
    assert report['stages'][0]['progress'] == child_outcome['progress']


def test_legacy_child_money_mode_cannot_start_a_new_shared_run(tmp_path, monkeypatch):
    monkeypatch.setattr(L, 'ProductionClients', lambda *a, **k: pytest.fail('paid clients forbidden'))
    monkeypatch.setattr(fixture, 'CHILD', replace(fixture.CHILD, settled_only=False))
    with pytest.raises(L.RunRefused, match='new runs require settled_only=True'):
        fixture.setup(tmp_path / 'run', [])
    assert not list(tmp_path.rglob('approval.json'))
    assert not list(tmp_path.rglob('budget.json'))
