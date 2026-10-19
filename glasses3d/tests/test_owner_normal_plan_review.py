"""Independent normal-fan scope check on disconnected selected/unselected shells."""
import copy

import pytest

from glasses3d.g3d import owner_mesh as M, owner_surface as S, owner_normal_plan as N
from glasses3d.tests.test_owner_edge_review import separate_components


def test_required_fans_never_expand_to_an_unselected_component():
    source, op = separate_components()
    # This regression concerns short-code fan splitting. Free normals do not
    # need sharp boundaries; make the authored storage explicit.
    obj = source['objects'][0]
    obj['normal_attributes'] = [{'name': 'custom_normal', 'domain': 'CORNER',
        'data_type': 'INT16_2D', 'field': 'value', 'values': [[0, 0] for _ in obj['corner_normals']]}]
    obj['has_custom_normals'] = True
    op.update(kind='repair_shading', params={'strength': 1., 'mode': 'flat'})
    target, reports = S.state(source, [op])
    before, after = source['objects'][0], target['objects'][0]
    added = N.sharp_edges(after)-N.sharp_edges(before)
    assert added and all(a < 8 and b < 8 for a, b in added)
    assert after['vertices_local'] == before['vertices_local']
    assert after['polygons'] == before['polygons']
    assert reports[-1]['normal_fan_encoding'][0]['added_sharp_edges']
    assert S.verify(source, target, [op])['ok']
    hostile = copy.deepcopy(target)
    row = hostile['objects'][0]
    remote = next(i for i, edge in enumerate(row['edges']) if min(edge) >= 8)
    flags = next(a for a in row['attributes'] if a['name'] == 'sharp_edge')
    flags['values'][remote][0] = True
    with pytest.raises(M.OwnerMeshError, match='exact planned normal fans'):
        S.verify(source, hostile, [op])


def _n1_historical_normal_record(storage_type):
    vectors = [[0., 0., 1.] for _ in range(3)]
    return {'corner_normals': copy.deepcopy(vectors),
        'normal_attributes': [{'name': 'custom_normal', 'domain': 'CORNER',
            'data_type': storage_type,
            'field': 'value' if storage_type == 'INT16_2D' else 'vector',
            'values': [[0, 0] for _ in vectors] if storage_type == 'INT16_2D' else copy.deepcopy(vectors)}],
        'attributes': [{'name': 'g3d_owner_normal_intent', 'domain': 'CORNER',
            'data_type': 'FLOAT_VECTOR', 'values': copy.deepcopy(vectors)}],
        'normal_intent': {'intent_normals': copy.deepcopy(vectors), 'encoded_matches': True,
            'normal_space_matches': True, 'decoded_within_quantization': True}}


@pytest.mark.parametrize('storage_type', ('INT16_2D', 'FLOAT_VECTOR'))
def test_valid_historical_normal_storage_retains_complete_authority(storage_type):
    obj = _n1_historical_normal_record(storage_type)
    before = copy.deepcopy(obj)
    assert 'managed_loop_ids' not in obj['normal_intent']
    assert 'unmanaged_matches' not in obj['normal_intent']
    assert N.managed_loops(obj) == {0, 1, 2}
    assert obj == before


@pytest.mark.parametrize('storage_type', ('INT16_2D', 'FLOAT_VECTOR'))
@pytest.mark.parametrize('damage', ('orphan', 'no_storage', 'false_encoded', 'false_spaces',
    'false_bound', 'truthy_flag', 'missing_intent', 'mismatched_intent', 'bad_storage_rows',
    'bad_intent_rows', 'wrong_storage_domain', 'zero_intent'))
def test_unproved_historical_normal_record_cannot_claim_complete_authority(storage_type, damage):
    obj = _n1_historical_normal_record(storage_type)
    if damage == 'orphan':
        obj['normal_attributes'] = []
        obj['attributes'] = []
    elif damage == 'no_storage':
        obj['normal_attributes'] = []
    elif damage in ('false_encoded', 'false_spaces', 'false_bound'):
        key = {'false_encoded': 'encoded_matches', 'false_spaces': 'normal_space_matches',
               'false_bound': 'decoded_within_quantization'}[damage]
        obj['normal_intent'][key] = False
    elif damage == 'truthy_flag':
        obj['normal_intent']['encoded_matches'] = 1
    elif damage == 'missing_intent':
        obj['attributes'] = []
    elif damage == 'mismatched_intent':
        obj['attributes'][0]['values'][0][0] = .125
    elif damage == 'bad_storage_rows':
        obj['normal_attributes'][0]['values'].pop()
    elif damage == 'bad_intent_rows':
        obj['normal_intent']['intent_normals'].pop()
        obj['attributes'][0]['values'].pop()
    elif damage == 'wrong_storage_domain':
        obj['normal_attributes'][0]['domain'] = 'POINT'
    else:
        obj['normal_intent']['intent_normals'][0] = [0., 0., 0.]
        obj['attributes'][0]['values'][0] = [0., 0., 0.]
    before = copy.deepcopy(obj)
    with pytest.raises(M.OwnerMeshError, match='legacy normal authority'):
        N.managed_loops(obj)
    assert obj == before


def test_ordinary_unmanaged_source_retains_no_normal_encoding_authority():
    obj = {'corner_normals': [[0., 0., 1.]], 'attributes': [], 'normal_attributes': []}
    assert N.managed_loops(obj) == set()
