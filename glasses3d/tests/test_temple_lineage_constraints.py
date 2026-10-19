"""Losing historical names must not remove an authenticated temple wall minimum."""
import copy

import numpy as np
import pytest

from glasses3d.g3d import owner_health as H, owner_mesh as M, owner_surface as S
from glasses3d.tests.test_owner_health import box as health_box
from glasses3d.tests.test_owner_mesh import box
from glasses3d.tests.test_owner_surface import patch
from glasses3d.tests.test_owner_thickness import complex_part


def constrained(obj, minimum):
    obj['role'] = 'temple'
    obj['aliases'] = {'status': 'unavailable', 'declared_constraints': {
        'spec_sha256': 'a' * 64, 'options': {'t_min_mm': minimum},
        'hinge': {'axis_xz_mm': [0., 0.]}}}
    return obj


@pytest.mark.parametrize('side', [-1, 1])
def test_declared_floor_uses_actual_graph_without_claiming_builder_pairs(side, monkeypatch):
    obj = constrained(box(side), 2.5)
    before = copy.deepcopy(obj)
    monkeypatch.setattr(M, 'paired_thickness', lambda *args: pytest.fail('names are unavailable'))
    check = M.physical(obj)['thickness']
    assert check['minimum_mm'] == 3 and check['t_min_mm'] == 2.5
    assert check['method'] == 'exact piecewise-linear graph intersection'
    assert obj == before and obj['aliases']['status'] == 'unavailable'
    thin = np.asarray(obj['vertices_world_mm'])
    thin[[1, 2, 5, 6], 0] -= side
    with pytest.raises(M.OwnerMeshError, match='minimum graph thickness'):
        M.physical(obj, thin)


@pytest.mark.parametrize('minimum', [0, -1, float('nan'), float('inf'), True, '1'])
def test_malformed_constraint_cannot_bypass_the_physical_floor(minimum):
    with pytest.raises(M.OwnerMeshError, match='finite and positive'):
        M.physical(constrained(box(), minimum))


@pytest.mark.parametrize('local', [False, True])
def test_generic_surface_and_local_fallback_keep_the_declared_floor(local):
    source, _ = complex_part() if local else patch(False)
    obj = constrained(source['objects'][0], 1.75)
    target = copy.deepcopy(obj)
    target['vertices_world_mm'][4][2] = 1.5
    with pytest.raises(M.OwnerMeshError, match='required minimum'):
        S.preserve_minimum_thickness(obj, target, [0, 1, 2, 3], preserve_original=False, minimum_mm=1.)
    target['vertices_world_mm'][4][2] = 1.875
    report = S.preserve_minimum_thickness(obj, target, [0, 1, 2, 3], preserve_original=False, minimum_mm=1.)
    check = report['patches'][0] if local else report
    assert check['required_minimum_mm'] == 1.75
    with pytest.raises(M.OwnerMeshError, match='required minimum'):
        S.preserve_minimum_thickness(obj, target, [0, 1, 2, 3], preserve_original=False, minimum_mm=1.9)


@pytest.mark.parametrize('verified', [False, True])
@pytest.mark.parametrize('mirrored', [False, True])
def test_health_remap_keeps_constraint_for_the_next_edit(verified, mirrored):
    obj, operation = health_box()
    constrained(obj, 3.5)
    obj['side'] = 1
    if verified:
        obj['aliases'] = {'status': 'verified_topology', 'stored_spec_sha256': 'a' * 64,
                          'options': {'t_min_mm': 3.5}, 'hinge': {'axis_xz_mm': [0., 0.]}}
    if mirrored:
        matrix = np.diag([-1., 1., 1., 1.])
        obj['matrix_world'] = matrix.tolist()
        obj['vertices_world_mm'] = (np.asarray(obj['vertices_local']) @ matrix[:3, :3].T).tolist()
        obj['side'] = -1
    target, report = H.plan_object(obj, operation)
    assert report['removed_interior_vertex_ids'] == [8]
    assert report['declared_thickness']['t_min_mm'] == 3.5
    assert report['physical']['thickness']['t_min_mm'] == 3.5
    assert target['aliases']['status'] == 'unavailable'
    assert target['aliases']['declared_constraints'] == M.temple_constraints(obj)
    thin = np.asarray(target['vertices_world_mm'])
    thin[:, 0] *= .75
    with pytest.raises(M.OwnerMeshError, match='minimum graph thickness'):
        M.physical(target, thin)


def test_health_topology_edit_cannot_drop_an_already_violated_declared_floor():
    obj, operation = health_box()
    constrained(obj, 4.5)
    obj['side'] = 1
    with pytest.raises(M.OwnerMeshError, match='minimum graph thickness'):
        H.plan_object(obj, operation)
