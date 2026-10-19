"""Per-item UNDERSTAND seals real native evidence with mocked render workers; $0."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from glasses3d.agent import edits, instructions, owner_captures, owner_choices
from glasses3d.agent.journal import CheckpointIndex
from glasses3d.agent.tools import build_tools, owner_edit, owner_preview as P
from glasses3d.tests.test_owner_preview import harness, write


@pytest.fixture
def captured(harness, monkeypatch):
    h = harness
    h.doc.update(request_text='Owner words one.\n\nOwner words two.', owner_capture_items=[
        {'item_id': 'I1', 'request_text': 'Owner words one.', 'labels': ['A'], 'capture_id': 'C1'},
        {'item_id': 'I2', 'request_text': 'Owner words two.', 'labels': ['B'], 'capture_id': 'C2'}])
    h.evidence.update(images=[], annotated_target_ids=h.op['target_ids'])

    def validate(root, map_id, map_sha256, capture_receipts, operations):
        return {**copy.deepcopy(h.evidence), 'operations': copy.deepcopy(operations)}

    monkeypatch.setattr(P.model_map, 'validate_understanding', validate)
    return h


def operation(h, item_id):
    return {**copy.deepcopy(h.op), 'id': item_id + '-move'}


def preview(h, item_id='I1', *, options=None, call_id=None, op=None):
    options = options if options is not None else [
        {'id': 'A', 'label': 'Less', 'scale': .5, 'amount_text': '0.25 mm outward'},
        {'id': 'B', 'label': 'More', 'scale': 1.5, 'amount_text': '0.75 mm outward'}]
    result = P.preview(h.scene, call_id or 'preview-' + str(item_id), 'test-map', h.evidence['map_sha256'],
                       ['shown-map.json'], json.dumps([op or operation(h, item_id)]), options, item_id=item_id)
    P.mark_delivered(h.scene, result)
    return result


def item(h, item_id, receipt=None, disposition=None):
    ready = receipt is not None
    return {'item_id': item_id, 'number': int(item_id[1:]), 'label': 'Marked feature ' + item_id,
            'interpretation': 'Move the shown outer face.', 'disposition': disposition or ('previewed' if ready else 'cannot_preview'),
            'reason': '' if ready else 'The marked feature has no safe supported preview.',
            'direction': 'Outward' if ready else '', 'amount': '0.5 mm' if ready else '',
            'target_ids': h.op['target_ids'] if ready else [],
            'operation_json': json.dumps(operation(h, item_id)) if ready else '[]',
            'preview_receipt': receipt['preview_receipt'] if ready else None}


def understand(h, rows, **kwargs):
    return owner_edit.understand(h.scene, 'understand', 'test-map', h.evidence['map_sha256'],
                                 ['shown-map.json'], rows, ['Scripted tool fixture, not Astra.'], **kwargs)


def test_mixed_capture_keeps_each_original_item_with_native_sealed_options(captured):
    h = captured
    original_head, source_bytes = copy.deepcopy(h.head), h.checkpoint.read_bytes()
    previewed = preview(h)
    result = understand(h, [item(h, 'I1', previewed), item(h, 'I2')])
    artifact = edits.read(result['understanding'])
    assert result['status'] == 'accepted' and result['disposition'] == 'awaiting_owner_confirmation'
    assert 'choose one shown option per previewed item or skip it with a note' in result['next']
    assert 'amount_preview' not in artifact
    assert [row['item_id'] for row in artifact['items']] == ['I1', 'I2']
    first, blocked = artifact['items']
    assert blocked['disposition'] == 'cannot_preview' and blocked['operation_json'] == '[]'
    assert 'amount_preview' not in blocked
    choices = first['amount_preview']
    assert choices['item_id'] == 'I1'
    assert [option['amount_text'] for option in choices['options']] == ['0.25 mm outward', '0.75 mm outward']
    assert all(option['item_id'] == 'I1' for option in choices['options'])
    assert [op['id'] for op in artifact['operations']] == ['I1-move']
    assert artifact['following_markings'][0]['object_name'] == 'Surface mark'
    for key, hash_key in [('preview_receipt', 'preview_sha256'), ('preview_delivery', 'preview_delivery_sha256')]:
        assert artifact['evidence_files'][choices[key]] == choices[hash_key] == edits.sha(h.root / choices[key])
    verified = P.load_verified(h.scene, choices['preview_receipt'])
    assert all(image['panels'] == ['photo', 'before', 'option'] and image['native_pixels'] for image in verified['images'])
    assert {image['option_id'] for image in verified['images']} == {'A', 'B'}
    assert all(image['item_id'] == 'I1' for image in verified['images'])
    for option in verified['options']:
        for name in ('before_snapshot', 'after_snapshot', 'export'):
            saved = option[name]
            assert Path(saved['path']).is_file()
            relative = Path(saved['path']).relative_to(h.root).as_posix()
            assert artifact['evidence_files'][relative] == saved['sha256'] == edits.sha(saved['path'])
    assert h.head == original_head and h.checkpoint.read_bytes() == source_bytes
    assert not (h.root / 'runs').exists() and not (h.root / 'budget').exists()
    assert edits.read(Path(result['understanding']).with_name('understanding-usage.json'))['requests'] == 0


def test_two_capture_items_keep_separate_options_and_operation_order(captured):
    h = captured
    a, b = preview(h, 'I1'), preview(h, 'I2')
    result = understand(h, [item(h, 'I1', a), item(h, 'I2', b)])
    saved = edits.read(result['understanding'])
    assert [op['id'] for op in saved['operations']] == ['I1-move', 'I2-move']
    assert saved['items'][0]['amount_preview']['preview_receipt'] != saved['items'][1]['amount_preview']['preview_receipt']
    # Option IDs can be reused across items; authority is (item_id, option_id).
    assert [[o['id'] for o in row['amount_preview']['options']] for row in saved['items']] == [['A', 'B'], ['A', 'B']]


def test_understanding_page_shows_each_item_amounts_native_images_and_blocked_reason(captured):
    h = captured
    shown = preview(h)
    rows = [item(h, 'I1', shown), item(h, 'I2')]
    rows[0]['label'] = 'Outer <corner>'
    understand(h, rows)
    write(h.root / 'shown-map.json', {'views': [{'ref': 'ref01', 'full_pair': shown['images'][0]['path']}]})
    page = edits.understanding_page(h.root).read_text(encoding='utf-8')
    assert 'Outer &lt;corner&gt;' in page and 'Outer <corner>' not in page
    assert '0.25 mm outward' in page and '0.75 mm outward' in page
    assert 'Choose one shown option for this item, or skip it with a note.' in page
    assert 'Apply my picks runs locally at $0 and reports overlapping changes.' in page
    assert rows[1]['reason'] in page and 'Disposition: cannot preview' in page
    assert page.count('<h3>') == 2
    for option in shown['options']:
        for image in [*option['full_pairs'], *option['target_details']]:
            assert Path(image['path']).name in page and Path(image['path']).is_file()


def delivered_understanding(h, *, mixed=False):
    h.head['kind'] = 'start'
    CheckpointIndex(h.root / 'checkpoints').append(h.head)
    h.doc['edit_id'] = 'edit-fixture'
    h.doc['source'].update(export_sha256='e' * 64, delivery_id='source-delivery')
    first = preview(h, 'I1')
    second = None if mixed else preview(h, 'I2')
    result = understand(h, [item(h, 'I1', first), item(h, 'I2', second)])
    result['call_id'] = 'understand'
    owner_edit.mark_delivered(h.scene, result)
    write(h.root / 'shown-map.json', {'views': [{'ref': 'ref01', 'full_pair': first['images'][0]['path']}]})
    return result, first


@pytest.mark.parametrize('mixed', [False, True])
def test_real_sealed_reader_accepts_tool_produced_items_and_exposes_native_dto(captured, monkeypatch, mixed):
    h = captured
    result, first = delivered_understanding(h, mixed=mixed)
    before = edits.tree_hashes(h.root)
    doc, saved, loaded = owner_choices._load_sealed(h.root)
    assert doc == h.doc and saved == edits.read(result['understanding'])
    assert [row['item_id'] for row, _ in loaded] == ['I1', 'I2']
    assert loaded[0][1]['item_id'] == 'I1'
    assert (loaded[1][1] is None) is mixed
    assert edits.tree_hashes(h.root) == before
    image = Path(first['images'][0]['path'])
    captures = [{**row, 'annotated_path': image, 'annotated_sha256': edits.sha(image),
                 'payload': {'source': {'delivery_key': 'source-key'}}} for row in h.doc['owner_capture_items']]
    monkeypatch.setattr(owner_captures, 'load_round_captures', lambda *args: captures)
    dto = owner_choices.read_understanding(h.root)
    assert dto['understanding_sha256'] == result['understanding_sha256']
    assert dto['source'] == {'delivery_key': 'source-key', 'glb_sha256': 'e' * 64}
    assert [row['request_text'] for row in dto['items']] == ['Owner words one.', 'Owner words two.']
    assert [row['fallback_allowed'] for row in dto['items']] == [False, mixed]
    assert dto['run_settled_usd'] == '0.000000' and dto['map_images']
    for row in dto['items']:
        for option in row['options']:
            assert option['amount_text'] in {'0.25 mm outward', '0.75 mm outward'}
            assert option['images']
            for record in option['images']:
                assert record['panels'] == ['photo', 'before', 'option']
                assert edits.sha(record['path']) == record['sha256']


@pytest.mark.parametrize('damage', ['wrong_item_binding', 'changed_receipt', 'changed_source_bytes', 'changed_source_head'])
def test_real_sealed_reader_refuses_changed_item_receipt_or_source(captured, damage):
    h = captured
    result, first = delivered_understanding(h, mixed=True)
    if damage == 'wrong_item_binding':
        artifact = edits.read(result['understanding'])
        artifact['items'][0]['amount_preview']['item_id'] = 'I2'
        write(Path(result['understanding']), artifact)
        owner_edit.mark_delivered(h.scene, {**result, 'understanding_sha256': edits.sha(result['understanding'])})
    elif damage == 'changed_receipt':
        path = h.root / first['preview_receipt']
        path.write_bytes(path.read_bytes() + b' ')
    elif damage == 'changed_source_bytes': h.checkpoint.write_bytes(b'changed selected source')
    else:
        CheckpointIndex(h.root / 'checkpoints').append({**h.head, 'id': 'changed', 'fingerprint': 'changed source'})
    with pytest.raises(ValueError):
        owner_choices._load_sealed(h.root)


@pytest.mark.parametrize('disposition', ['cannot_preview', 'needs_clarification', 'unsupported'])
def test_all_blocked_capture_has_reason_and_no_selectable_authority(captured, disposition):
    h = captured
    result = understand(h, [item(h, 'I1', disposition=disposition), item(h, 'I2', disposition=disposition)])
    saved = edits.read(result['understanding'])
    assert result['disposition'] == 'diagnostic_only'
    assert saved['operations'] == [] and saved['following_markings'] == []
    assert all(row['reason'] and 'amount_preview' not in row for row in saved['items'])


def test_all_blocked_sealed_reader_still_verifies_actual_source_checkpoint(captured):
    h = captured
    h.head['kind'] = 'start'
    CheckpointIndex(h.root / 'checkpoints').append(h.head)
    result = understand(h, [item(h, 'I1'), item(h, 'I2')])
    result['call_id'] = 'understand'
    owner_edit.mark_delivered(h.scene, result)
    _, saved, loaded = owner_choices._load_sealed(h.root)
    assert saved['status'] == 'diagnostic_only'
    assert all(receipt is None for _, receipt in loaded)
    h.checkpoint.write_bytes(b'changed selected source')
    with pytest.raises(ValueError, match='checkpoint|source'):
        owner_choices._load_sealed(h.root)


@pytest.mark.parametrize('damage', ['missing_id', 'unknown_id', 'blank_amount', 'missing_amount', 'one_option'])
def test_capture_preview_rejects_unbound_or_unreadable_options_before_worker(captured, damage):
    h = captured
    options = [{'id': 'A', 'label': 'Less', 'scale': .5, 'amount_text': '0.25 mm outward'},
               {'id': 'B', 'label': 'More', 'scale': 1.5, 'amount_text': '0.75 mm outward'}]
    item_id = None if damage == 'missing_id' else 'I9' if damage == 'unknown_id' else 'I1'
    if damage == 'blank_amount': options[0]['amount_text'] = ' '
    if damage == 'missing_amount': options[0].pop('amount_text')
    if damage == 'one_option': options.pop()
    with pytest.raises(ValueError):
        preview(h, item_id, options=options, op=operation(h, 'I1'))
    assert not h.calls
    assert not (h.root / 'work/pv').exists()


@pytest.mark.parametrize('damage', ['wrong_item', 'missing_receipt', 'whole_group', 'duplicate_operation',
                                  'blocked_with_receipt', 'blocked_with_operation', 'blocked_without_reason',
                                  'legacy_disposition', 'item_order'])
def test_capture_understanding_refuses_rebinding_and_blocked_authority(captured, damage):
    h = captured
    a = preview(h, 'I1')
    rows = [item(h, 'I1', a), item(h, 'I2')]
    kwargs = {}
    if damage == 'wrong_item':
        rows[0]['preview_receipt'] = preview(h, 'I2')['preview_receipt']
    elif damage == 'missing_receipt': rows[0]['preview_receipt'] = None
    elif damage == 'whole_group': kwargs['preview_receipt'] = a['preview_receipt']
    elif damage == 'duplicate_operation':
        rows[1] = {**rows[0], 'item_id': 'I2', 'number': 2}
    elif damage == 'blocked_with_receipt': rows[1]['preview_receipt'] = a['preview_receipt']
    elif damage == 'blocked_with_operation': rows[1]['operation_json'] = rows[0]['operation_json']
    elif damage == 'blocked_without_reason': rows[1]['reason'] = ' '
    elif damage == 'legacy_disposition': rows[0]['disposition'] = 'executable'
    elif damage == 'item_order': rows[0]['item_id'], rows[1]['item_id'] = 'I2', 'I1'
    with pytest.raises(ValueError):
        understand(h, rows, **kwargs)
    assert not (h.root / 'work/owner_edit/understanding.json').exists()


@pytest.mark.parametrize('damage', ['undelivered', 'unseen', 'changed_image', 'changed_checkpoint',
                                  'option_item', 'amount_text', 'no_images', 'pixel_order', 'snapshot_unbound'])
def test_item_preview_verification_keeps_delivery_pixels_and_source_bindings(captured, damage):
    h = captured
    result = preview(h)
    path = h.root / result['preview_receipt']
    receipt = edits.read(path)
    if damage == 'undelivered': P.mark_delivered(h.scene, result, False)
    elif damage == 'unseen': h.scene._unseen.add(result['call_id'])
    elif damage == 'changed_image': Path(receipt['images'][0]['path']).write_bytes(b'altered image')
    elif damage == 'changed_checkpoint': h.checkpoint.write_bytes(b'other source')
    else:
        if damage == 'option_item': receipt['options'][0]['item_id'] = 'I2'
        elif damage == 'amount_text': receipt['options'][0].pop('amount_text')
        elif damage == 'no_images': receipt['images'] = []
        elif damage == 'pixel_order': receipt['images'][0]['panels'] = ['option', 'before', 'photo']
        elif damage == 'snapshot_unbound':
            key = Path(receipt['options'][0]['after_snapshot']['path']).relative_to(h.root).as_posix()
            del receipt['evidence_files'][key]
        write(path, receipt)
        P.mark_delivered(h.scene, {**receipt, 'preview_sha256': edits.sha(path)})
    with pytest.raises(ValueError):
        P.load_verified(h.scene, result['preview_receipt'])


def test_noop_capture_preview_still_refuses_before_any_private_worker(captured):
    h = captured
    op = operation(h, 'I1'); op['params']['delta_mm'] = [0, 0, 0]
    with pytest.raises(ValueError, match='change the source'):
        preview(h, op=op)
    assert not h.calls


def test_capture_instruction_and_sdk_schema_describe_the_sealed_item_contract():
    owner = {'phase': 'understand'}
    tools = build_tools(SimpleNamespace(root=None, owner_edit=owner))
    text = instructions.build_instructions(tools, owner_edit=owner)
    for phrase in ('original item_id', 'amount_text', 'previewed', 'cannot_preview', 'Mix',
                   'top-level preview_receipt null', 'Apply my picks', 'skipped notes', 'reporting overlapping',
                   'single pick must reproduce its preview bytes', 'separately approved full paid edit'):
        assert phrase in text
    assert len(text) <= instructions.MAX_INSTRUCTION_CHARS == 24_000
    registry = {tool.name: tool for tool in tools}
    schema = registry['preview_owner_edit'].params_json_schema
    assert 'item_id' in schema['properties']
    assert 'amount_text' in schema['$defs']['PreviewOption']['properties']
    interpretation = registry['understand_finish'].params_json_schema['$defs']['Interpretation']['properties']
    assert {'previewed', 'cannot_preview', 'unsupported', 'needs_clarification', 'executable'} == set(interpretation['disposition']['enum'])
    assert 'preview_receipt' in interpretation
