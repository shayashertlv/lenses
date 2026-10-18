"""Failure seams use real journal recovery and SDK tool dispatch without Blender or model calls."""
import asyncio
import threading
from types import SimpleNamespace

from agents.tool_context import ToolContext
import pytest

from glasses3d.agent import loop as L
from glasses3d.agent.journal import BlenderSession, CheckpointMismatch, WorkerFailed
from glasses3d.agent.worker_client import WorkerDied
from glasses3d.agent.temple_lab.lab import Lab
from glasses3d.agent.temple_lab import tools as T, owner_stage, stage
from glasses3d.agent.temple_lab.recovery import restore_head
from glasses3d.agent.tools import build_tools, FATAL


def test_unfinished_real_lab_progress_ignores_bound_finish_method(tmp_path):
    lab = object.__new__(Lab)
    lab.state = {'finish': None, 'heads': [{'checkpoint': 'c0002', 'revision': 2}],
                 'best_checkpoint': 'c0001', 'revisions': [{}, {}, {}]}
    lab.s = SimpleNamespace(head={'id': 'c0002'})
    assert callable(lab.finish)
    progress = stage.progress(lab, tmp_path / 'work' / 'temple_stage' / 'private', tmp_path)
    assert progress == {'package': 'work/temple_stage/private', 'checkpoint': 'c0002',
                        'best_checkpoint': 'c0001', 'revision': 2, 'revisions_built': 3,
                        'finish': None, 'summary': None, 'parent_geometry_unchanged': True}


def test_apply_preserves_worker_failure_when_real_dead_session_rollback_raises():
    lab = object.__new__(Lab)
    lab.verify_inputs = lambda: None
    lab.spec = lambda rid: {'spec': rid}
    lab.state = {'start_fingerprint': 'fp0', 'start_checkpoint': 'c0000'}
    head = {'id': 'c0003', 'fingerprint': 'fp0', 'file': 'c0003.blend', 'sha256': 'x'}
    session = object.__new__(BlenderSession)
    session.index = SimpleNamespace(head=head, get=lambda cid: {**head, 'id': cid})
    session.worker = None
    original = WorkerFailed('worker could not restart', head)

    def failed(*args, **kwargs):
        raise original

    session.call_mutating = failed
    lab.s = session
    with pytest.raises(WorkerFailed) as caught:
        lab._apply('call-1', 1, tool='facet_edit')
    assert caught.value is original
    assert 'AttributeError' in original.__notes__[0]


@pytest.mark.parametrize('recovery', ['refused', 'wrong_fingerprint', 'exception'])
def test_failed_recovery_is_fatal_without_a_prior_worker_error(recovery):
    head = {'id': 'saved', 'fingerprint': 'expected'}

    def restore(*args, **kwargs):
        if recovery == 'exception':
            raise AttributeError('worker missing')
        return {'status': 'refused' if recovery == 'refused' else 'ok'}

    session = SimpleNamespace(head=head, restore_checkpoint=restore, fingerprint=lambda: 'different')
    with pytest.raises(WorkerFailed):
        restore_head(session, head, 'rollback', original=ValueError('ordinary refused work'))


@pytest.mark.parametrize('error_type', [WorkerFailed, CheckpointMismatch, WorkerDied])
def test_facet_sdk_dispatch_uses_shared_fatal_errors(error_type):
    failure = error_type('fatal', None) if error_type is WorkerFailed else error_type('fatal')
    assert FATAL == L.FATAL_TOOL_ERRORS
    lab = SimpleNamespace(lock=threading.Lock(), running=None, root=None,
        variant_photos_max=lambda: 4, photo_ids=lambda: ['photo'], compare_ids=lambda: ['photo'],
        image_ids=lambda: ['photo'])

    def inspect(*args):
        raise failure

    lab.inspect = inspect
    tool = next(t for t in T.registry(lab) if t.name == 'facet_inspect')
    context = ToolContext(context=None, tool_name=tool.name, tool_call_id='fatal-test', tool_arguments='{}')
    with pytest.raises(error_type) as caught:
        asyncio.run(tool.on_invoke_tool(context, '{}'))
    assert caught.value is failure and not lab.lock.locked() and lab.running is None


@pytest.mark.parametrize('phase', ['understand', 'edit'])
def test_owner_parent_retains_specialist_but_nested_registry_does_not(phase):
    scene = SimpleNamespace(root=None, owner_edit={'phase': phase})
    settings = L.RunSettings()
    assert 'temple_stage' in {t.name for t in build_tools(scene, settings)}
    nested = owner_stage.registry(scene, owner_stage.settings(settings, scene.owner_edit))
    assert 'temple_stage' not in {t.name for t in nested}
    from glasses3d.agent.instructions import build_instructions
    text = build_instructions(nested, owner_edit=scene.owner_edit)
    assert 'temple_stage' not in text
