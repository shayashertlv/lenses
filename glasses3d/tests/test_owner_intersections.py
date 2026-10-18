"""Exact crossing regressions for geometry hidden by near-vertical chart walls."""
import copy

import numpy as np
import pytest

from glasses3d.g3d import owner_intersections as I, owner_mesh as M


def mesh(points, triangles, polygons=None):
    return {'vertices_world_mm': points, 'triangles': triangles,
            'polygons': triangles if polygons is None else polygons}


def reject(source, target):
    with pytest.raises(M.OwnerMeshError, match='crossing|overlap|ambiguous') as caught:
        I.no_new_crossings(source, target)
    assert caught.value.code == 'E_OWNER_CROSSING'


@pytest.mark.parametrize('exponent', [25, 40, 100])
def test_wall_crossing_inside_float32_representation_bound_is_not_normalized_away(exponent):
    eps = 2.**-exponent
    points = [[0., -1., -1.], [0., 1., -1.], [0., 0., 1.],
              [eps, -.5, -.5], [eps, .5, -.5], [eps, 0., .5]]
    source = mesh(points, [[0, 1, 2], [3, 4, 5]])
    target = copy.deepcopy(source)
    target['vertices_world_mm'][5][0] = -eps
    reject(source, target)


def test_nonadjacent_coplanar_overlap_refused():
    source = mesh([[0, 0, 0], [2, 0, 0], [0, 2, 0], [3, 0, 0], [4, 0, 0], [3, 1, 0]],
                  [[0, 1, 2], [3, 4, 5]])
    target = copy.deepcopy(source)
    target['vertices_world_mm'][3:] = [[.25, .25, 0], [1, .25, 0], [.25, 1, 0]]
    reject(source, target)


def test_same_parent_polygon_is_not_an_exemption_for_a_folded_triangle():
    source = mesh([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]],
                  [[0, 1, 2], [0, 2, 3]], [[0, 1, 2, 3]])
    source['triangle_polygons'] = [0, 0]
    target = copy.deepcopy(source)
    target['vertices_world_mm'][3] = [.75, .25, 0]
    reject(source, target)


def test_shared_vertex_does_not_excuse_intersection_segment():
    source = mesh([[0, 0, 0], [2, 0, 0], [0, 2, 0], [1, 1, 1], [1, 1, 2]],
                  [[0, 1, 2], [0, 3, 4]])
    target = copy.deepcopy(source)
    target['vertices_world_mm'][4][2] = -1
    reject(source, target)


def test_shared_edge_can_change_dihedral_without_creating_an_overlap():
    source = mesh([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, -1, 0]], [[0, 1, 2], [1, 0, 3]])
    target = copy.deepcopy(source)
    target['vertices_world_mm'][3] = [0, .5, .5]
    report = I.no_new_crossings(source, target)
    assert report['changed_triangles'] == 1
    assert report['candidate_pairs'] == 1


def test_existing_contact_requires_identical_locus():
    source = mesh([[-2, -2, 0], [2, -2, 0], [0, 2, 0], [0, 0, -1], [0, 0, 1], [0, 1, 0]],
                  [[0, 1, 2], [3, 4, 5]])
    target = copy.deepcopy(source)
    target['vertices_world_mm'][3][2] = -2
    target['vertices_world_mm'][4][2] = 2
    assert I.no_new_crossings(source, target)['inherited_contacts'] == 1
    target['vertices_world_mm'][5][1] = .5
    reject(source, target)


def test_same_connectivity_uses_current_ids_instead_of_stale_ancestry():
    source = mesh([[-2, -2, 0], [2, -2, 0], [0, 2, 0], [0, 0, -1], [0, 0, 1], [0, 1, 0]],
                  [[0, 1, 2], [3, 4, 5]])
    source['_surface_vertex_parents'] = [3, 4, 5, 0, 1, 2]
    target = copy.deepcopy(source)
    target['vertices_world_mm'][3][2] = -2
    target['vertices_world_mm'][4][2] = 2
    assert I.no_new_crossings(source, target)['inherited_contacts'] == 1


def test_new_rim_contact_is_not_accepted_as_a_zero_clearance_edge():
    eps = 2.**-40
    source = mesh([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, eps], [0, -1, eps], [-1, 0, eps]],
                  [[0, 1, 2], [3, 4, 5]])
    target = copy.deepcopy(source)
    target['vertices_world_mm'][3][2] = 0
    reject(source, target)


def test_extra_triangle_multiplicity_cannot_be_called_unchanged():
    source = mesh([[0, 0, 0], [1, 0, 0], [0, 1, 0]], [[0, 1, 2]])
    target = copy.deepcopy(source)
    target['triangles'].append([0, 1, 2])
    reject(source, target)


def test_retriangulation_of_planar_quad_is_not_an_overlap():
    source = mesh([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], [[0, 1, 2], [0, 2, 3]])
    target = copy.deepcopy(source)
    target['triangles'] = [[0, 1, 3], [1, 2, 3]]
    assert I.no_new_crossings(source, target)['changed_triangles'] == 2


def test_changed_degenerate_triangle_refuses_even_without_nearby_surface():
    source = mesh([[0, 0, 0], [1, 0, 0], [0, 1, 0]], [[0, 1, 2]])
    target = copy.deepcopy(source)
    target['vertices_world_mm'][2] = [2, 0, 0]
    reject(source, target)


def test_bvh_bounds_candidate_work_by_changed_neighborhood():
    starts = np.c_[np.arange(4096)*3., np.zeros(4096), np.zeros(4096)]
    points = (starts[:, None, :]+np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]])).reshape(-1, 3)
    source = mesh(points.tolist(), np.arange(len(points)).reshape(-1, 3).tolist())
    target = copy.deepcopy(source)
    target['vertices_world_mm'][0][2] = .25
    report = I.no_new_crossings(source, target)
    assert report['changed_triangles'] == 1
    assert report['candidate_pairs'] == 0


def test_exact_clipping_locus_does_not_depend_on_winding_or_collinear_vertices():
    a = ((0, 0, 0), (4, 0, 0), (0, 4, 0))
    b = ((1, -1, 0), (1, 3, 0), (3, 1, 0))
    expected = I._intersection(a, b)
    assert len(expected) == 4
    for x in (a, a[::-1], a[1:]+a[:1]):
        for y in (b, b[::-1], b[1:]+b[:1]):
            assert I._intersection(x, y) == expected
