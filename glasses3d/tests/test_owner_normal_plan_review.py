"""Independent normal-fan scope check on disconnected selected/unselected shells."""
import copy

import pytest

from glasses3d.g3d import owner_mesh as M, owner_surface as S, owner_normal_plan as N
from glasses3d.tests.test_owner_edge_review import separate_components


def test_required_fans_never_expand_to_an_unselected_component():
    source, op = separate_components()
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
