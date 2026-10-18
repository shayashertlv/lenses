"""Surface attachment regressions independent of a product or its object names."""
import copy
import numpy as np
import pytest

from glasses3d.g3d import owner_mesh as M, owner_markings as A
from glasses3d.agent.owner_edits import _advance, verify_snapshots, confirmed_transition
from glasses3d.tests.test_owner_mesh import box, operation, snapshot


def fixture(side=1):
    surface = box(side)
    mark = box(side, role='frame')
    mark.update(id='O0002', name='Surface mark', mesh_name='Mark mesh', aliases={'status': 'unavailable'},
                shared_mesh_users=['Surface mark'])
    p = np.asarray(mark['vertices_world_mm'])
    p[:, 0] = side*(5.04+(abs(p[:, 0])-2)*.02)
    p[:, 1] = .5+p[:, 1]*.5
    p[:, 2] = -1+p[:, 2]*.5
    mark['vertices_world_mm'] = mark['vertices_local'] = p.tolist()
    doc = snapshot(surface)
    doc['objects'].append(mark)
    return doc, operation(surface)


@pytest.mark.parametrize('side', [-1, 1])
def test_markings_are_discovered_and_follow_translations_without_changing_other_data(side):
    doc, op = fixture(side)
    original = copy.deepcopy(doc)
    ops = A.seal(doc, [op])
    assert A.summary(ops)[0]['object_name'] == 'Surface mark'
    after = _advance(doc, ops)
    assert verify_snapshots(doc, after, ops)['ok']
    a = np.asarray(doc['objects'][1]['vertices_world_mm'])
    b = np.asarray(after['objects'][1]['vertices_world_mm'])
    np.testing.assert_allclose(b-a, np.tile([side*.5, 0, 0], (len(a), 1)), atol=1e-6)
    assert doc == original
    patch = M.plan(doc, ops)
    assert len(patch['target']['objects']) == 2
    assert M.between(doc, after, ops)['patches'][1]['object_id'] == 'O0002'
    sealed = {'snapshot': doc, 'all_operations': ops, 'source_fingerprint': 'source', 'receipts': [],'selected_preview':{'id':'A'}}
    assert confirmed_transition(sealed, after, 'source')[0]['id'] == op['id']


def test_taper_preserves_standoff_and_changes_marking_plane():
    doc, op = fixture()
    op.update(kind='taper_region', params={'axis_points_mm': [[5, 0, 0], [5, 0, -6]], 'delta_mm': [.5, 0, 0]})
    ops = A.seal(doc, [op])
    world, _, _ = M.deformation(doc, ops)
    mark = doc['objects'][1]
    before = A.nearest(mark['vertices_world_mm'], doc['objects'][0]['vertices_world_mm'], doc['objects'][0]['triangles'])
    after = A.nearest(world['O0002'], world['O0001'], doc['objects'][0]['triangles'])
    np.testing.assert_allclose([r[3] for r in before], [r[3] for r in after], atol=1e-12)
    assert not np.allclose(world['O0002'][:, 2], np.asarray(mark['vertices_world_mm'])[:, 2])


def test_unlisted_marking_or_wrong_marking_displacement_is_refused():
    doc, op = fixture()
    ops = A.seal(doc, [op])
    after = _advance(doc, ops)
    with pytest.raises(M.OwnerMeshError, match='older understanding'):
        verify_snapshots(doc, after, [op])
    after['objects'][1]['vertices_local'][0][0] += .03
    sealed = {'snapshot': doc, 'all_operations': ops, 'source_fingerprint': 'source', 'receipts': [],'selected_preview':{'id':'A'}}
    with pytest.raises(M.OwnerMeshError, match='confirmed surface operation'):
        confirmed_transition(sealed, after, 'source')


def test_far_or_unaffected_markings_remain_exact():
    doc, op = fixture()
    for p in doc['objects'][1]['vertices_world_mm']:
        p[0] += 4
    assert A.seal(doc, [op])[0]['marking_attachments'] == []
    doc, op = fixture()
    # The inner face is untouched by this outer-face operation.
    for p in doc['objects'][1]['vertices_world_mm']:
        p[0] -= 3.2
    assert A.seal(doc, [op])[0]['marking_attachments'] == []


def test_unsupported_or_ambiguous_attachment_refuses_before_edit():
    doc, op = fixture()
    doc['objects'][1]['evaluated']['positions_match_source'] = False
    with pytest.raises(M.OwnerMeshError, match='uniquely editable'):
        A.seal(doc, [op])
    doc, op = fixture()
    second = copy.deepcopy(doc['objects'][0]); second.update(id='O0003', name='Second surface')
    doc['objects'].append(second)
    another = operation(second); another['id'] = 'item-2'
    with pytest.raises(M.OwnerMeshError, match='ambiguous'):
        A.seal(doc, [op, another])


def test_caller_attachment_is_overwritten_and_open_decals_stay_open():
    doc, op = fixture()
    mark = doc['objects'][1]
    mark['polygons'] = mark['polygons'][-1:]
    mark['triangles'] = mark['triangles'][-2:]
    op['marking_attachments'] = [{'object_id': 'untrusted'}]
    ops = A.seal(doc, [op])
    patch = M.plan(doc, ops)
    assert verify_snapshots(doc,patch['target'],ops)['physical']['O0002']['open_surface_marking']


def test_two_call_replay_preserves_exact_float32_positions_and_no_duplicate_motion():
    doc, op = fixture()
    other = copy.deepcopy(op); other['id'] = 'item-2'; other['params']['delta_mm'] = [.15, 0, 0]
    ops = A.seal(doc, [op, other])
    middle = _advance(doc, ops[:1])
    final = _advance(middle, ops[1:])
    sealed = {'snapshot': doc, 'all_operations': ops, 'source_fingerprint': 'source','selected_preview':{'id':'A'},
              'receipts': [{'fingerprint': 'middle', 'operations': ops[:1]}]}
    assert confirmed_transition(sealed,final,'source')==ops
    assert confirmed_transition(sealed,final,'middle')==[]


def test_nearest_triangle_interior_edge_and_vertex():
    points = [[.2, .2, .1], [-.1, .3, .2], [-.1, -.1, .3]]
    rows = A.nearest(points, [[0, 0, 0], [1, 0, 0], [0, 1, 0]], [[0, 1, 2]])
    np.testing.assert_allclose([r[1] for r in rows], [[.6, .2, .2], [.7, 0, .3], [1, 0, 0]])


def test_legacy_understanding_cannot_edit_a_surface_while_leaving_its_marks_fixed():
    doc, op = fixture()
    for action in (lambda: M.plan(doc, [op]), lambda: _advance(doc, [op]),
                   lambda: M.between(doc, doc, [op])):
        with pytest.raises(M.OwnerMeshError, match='older understanding'):
            action()


def test_surface_check_refuses_marking_that_was_left_buried():
    doc, op = fixture()
    ops = A.seal(doc, [op])
    world, _, _ = M.deformation(doc, ops)
    world['O0002'] = np.asarray(doc['objects'][1]['vertices_world_mm'])
    with pytest.raises(M.OwnerMeshError, match='buried'):
        A.surface_checks(doc, world, ops)


def test_crossing_check_sees_surface_through_triangle_interior():
    mark = [[-2, -2, 0], [2, -2, 0], [0, 2, 0]]
    surface = [[0, 0, -1], [.5, 0, 1], [-.5, 0, 1]]
    assert A._pierced(mark, [[0, 1, 2]], surface, [[0, 1, 2]]) == {0}


def test_explicit_parented_marking_outside_supported_distance_is_not_silently_omitted():
    doc, op = fixture()
    mark = doc['objects'][1]
    mark['parent_name'] = doc['objects'][0]['name']
    for p in mark['vertices_world_mm']:
        p[0] += 4
    with pytest.raises(M.OwnerMeshError, match='attached marking.*surface distance'):
        A.seal(doc, [op])


def test_nonmesh_marking_near_edited_surface_refuses_with_named_limitation():
    doc, op = fixture()
    doc['unsupported_rendered_geometry'] = [{'name': 'Raised text', 'type': 'FONT', 'parent_name': None,
                                            'bbox_world_mm': [[5.05, .5, -2], [5.1, 1.5, -1]]}]
    with pytest.raises(M.OwnerMeshError, match='Raised text.*unsupported FONT'):
        A.seal(doc, [op])


def test_understanding_reply_and_page_disclose_every_following_marking(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace
    from glasses3d.agent import edits
    from glasses3d.agent.tools import owner_edit
    doc, op = fixture()
    source = tmp_path/'snapshot.json'
    source.write_text(json.dumps(doc))
    monkeypatch.setattr(edits, 'load', lambda root: {'phase': 'understand', 'source': {'fingerprint': 'same'},
                                                   'request_text': 'Move the face.'})
    evidence = {'operations': [op], 'source': {'fingerprint': 'same'}, 'snapshot_path': str(source),
                'evidence_files': [], 'map_path': 'map.json', 'map_sha256': 'sha', 'images': [],
                'annotated_target_ids': ['F1'],
                'legend': [{'label': 'T1', 'target_id': 'F1', 'description': 'Side face'}]}
    monkeypatch.setattr(owner_edit.model_map, 'validate_understanding', lambda *args: evidence)
    preview={'map_id':'map','map_sha256':'sha','capture_receipts':['capture.json'],'base_operations':A.seal(doc,[op]),
        'evidence_files':{},'preview_receipt':'preview.json','preview_sha256':'preview','images':[],'call_id':'preview',
        'options':[{'id':key,'label':key,'scale':scale,'operations':A.seal(doc,[op])} for key,scale in [('A',.5),('B',1.)]]}
    monkeypatch.setattr(owner_edit.owner_preview,'load_verified',lambda *args:preview)
    (tmp_path/'preview.json').write_text(json.dumps(preview))
    scene = SimpleNamespace(root=tmp_path, s=SimpleNamespace(fingerprint=lambda: 'same'))
    items = [{'number': 1, 'label': 'Side face', 'interpretation': 'Move the face.', 'direction': 'Outward',
              'disposition': 'executable', 'reason': '',
              'amount': '.5 mm', 'target_ids': ['F1'], 'operation_json': json.dumps(op)}]
    (tmp_path/'capture.json').write_text('{"views": []}')
    result = owner_edit.understand(scene, 'stop', 'map', 'sha', ['capture.json'], items, [],'preview.json')
    assert [r['object_name'] for r in result['following_markings']] == ['Surface mark']
    artifact = json.loads((tmp_path/'work/owner_edit/understanding.json').read_text())
    assert artifact['operations'][0]['marking_attachments'][0]['object_id'] == 'O0002'
    page = edits.understanding_page(tmp_path).read_text()
    assert 'Surface mark follows Closed test part' in page and 'Targets: T1' in page
