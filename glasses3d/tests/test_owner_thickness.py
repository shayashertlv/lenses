"""Local physical checks use the selected wall, not unrelated geometry's chart."""
import copy

import numpy as np
import pytest

from glasses3d.g3d import owner_surface as S, owner_mesh as M, owner_thickness as T
from glasses3d.tests.test_owner_surface import patch


def complex_part():
    source, operation = patch(False)
    obj = source["objects"][0]
    obj["vertices_local"][4][2] = 2.3
    base = copy.deepcopy(obj)
    # Two remote closed components overlap in projection. The complete part
    # cannot be represented by one opposing graph pair in the selected chart.
    for translation in ([10, 0, 0], [10, 0, 5]):
        offset = len(obj["vertices_local"])
        obj["vertices_local"].extend((np.asarray(base["vertices_local"]) + translation).tolist())
        obj["polygons"].extend([[i + offset for i in p] for p in base["polygons"]])
        for key in ("corner_normals", "polygon_smooth", "polygon_material"):
            obj[key].extend(copy.deepcopy(base[key]))
        obj["uv_layers"][0]["uv"].extend(copy.deepcopy(base["uv_layers"][0]["uv"]))
    obj["triangles"] = [[p[0], p[k], p[k + 1]] for p in obj["polygons"] for k in range(1, len(p) - 1)]
    obj["triangle_polygons"] = [i for i, p in enumerate(obj["polygons"]) for _ in range(len(p) - 2)]
    return source, operation


def test_local_refinement_preserves_wall_when_remote_geometry_is_not_a_graph():
    source, operation = complex_part()
    before = copy.deepcopy(source)
    operation["params"]["relax_interior"] = True
    target, reports = S.state(source, [operation])
    assert source == before
    check = reports[0]["minimum_thickness_preservation"]
    assert "selected local graph" in check["method"]
    assert check["original_minimum_preserved"] and check["unselected_geometry_unchanged"]
    assert check["patches"][0]["before_minimum_mm"] == 2
    assert check["patches"][0]["after_minimum_mm"] == 2
    assert not check["new_intersections_or_contacts"]
    assert M.physical(target["objects"][0])["closed"]


@pytest.mark.parametrize("change", ["thinner", "crossing", "boundary", "unselected"])
def test_local_proof_refuses_changed_wall_boundary_or_unselected_surface(change):
    source, _ = complex_part()
    obj = source["objects"][0]
    after = copy.deepcopy(obj)
    if change == "thinner":
        after["vertices_world_mm"][4][2] = 1.5
    elif change == "crossing":
        after["vertices_world_mm"][4][2] = -.1
    elif change == "boundary":
        after["vertices_world_mm"][0][2] += .1
    else:
        after["vertices_world_mm"][9][2] += .1
    with pytest.raises(ValueError):
        T.local_patches(obj, after, [0, 1, 2, 3])


def test_local_proof_rejects_a_new_collision_with_an_overhead_surface():
    source, _ = complex_part()
    obj = source["objects"][0]
    # Move the remote second component above the selected patch while keeping
    # the source solid separated. It is an obstacle, not this patch's back wall.
    vertices = np.asarray(obj["vertices_world_mm"])
    vertices[18:36] += [-10, 0, 4]
    obj["vertices_world_mm"] = obj["vertices_local"] = vertices.tolist()
    after = copy.deepcopy(obj)
    after["vertices_world_mm"][4][2] = 5.
    with pytest.raises(ValueError, match="intersects|crossed|contact"):
        T.local_patches(obj, after, [0, 1, 2, 3])


def test_missing_opposing_coverage_is_never_assumed_valid():
    source, _ = complex_part()
    obj = source["objects"][0]
    keep = [i for i, polygon in enumerate(obj["triangle_polygons"]) if polygon != 4]
    obj["triangles"] = [obj["triangles"][i] for i in keep]
    obj["triangle_polygons"] = [obj["triangle_polygons"][i] for i in keep]
    with pytest.raises(ValueError, match="covered"):
        T.local_patches(obj, copy.deepcopy(obj), [0, 1, 2, 3])


@pytest.mark.parametrize("local", [False, True])
def test_generic_thinning_needs_and_enforces_explicit_positive_minimum(local):
    source, _ = complex_part() if local else patch(False)
    obj = source['objects'][0]
    obj['triangle_polygons'] = [i for i, p in enumerate(obj['polygons']) for _ in range(len(p)-2)]
    after = copy.deepcopy(obj)
    after['vertices_world_mm'][4][2] = 1.5
    for invalid in (None, 0, -1, float('nan'), float('inf'), True):
        with pytest.raises(M.OwnerMeshError, match='positive'):
            S.preserve_minimum_thickness(obj, after, [0,1,2,3], preserve_original=False, minimum_mm=invalid)
    check = S.preserve_minimum_thickness(obj, after, [0,1,2,3], preserve_original=False, minimum_mm=1.5)
    report = check['patches'][0] if local else check
    assert report['required_minimum_mm'] == 1.5
    with pytest.raises(M.OwnerMeshError, match='required minimum'):
        S.preserve_minimum_thickness(obj, after, [0,1,2,3], preserve_original=False, minimum_mm=1.500001)
    with pytest.raises(M.OwnerMeshError, match='required minimum'):
        S.preserve_minimum_thickness(obj, after, [0,1,2,3], preserve_original=True, minimum_mm=1.)


def test_declared_temple_floor_keeps_unchanged_pair_certificate_and_never_weakens(monkeypatch):
    from glasses3d.tests.test_owner_mesh import box
    obj=box();order=[1,2,6,5,0,3,7,4];index={old:new for new,old in enumerate(order)}
    for key in ('vertices_local','vertices_world_mm'):obj[key]=[obj[key][i] for i in order]
    for key in ('polygons','triangles'):obj[key]=[[index[i] for i in p] for p in obj[key]]
    after=copy.deepcopy(obj)
    def unsupported_graph(*args):raise M.OwnerMeshError('E_OWNER_GRAPH','graph deliberately unavailable')
    monkeypatch.setattr(M,'graph_thickness',unsupported_graph)
    check=S.preserve_minimum_thickness(obj,after,[5],preserve_original=False,minimum_mm=.5)
    assert check['declared_thickness']['projected_chart_unchanged']
    assert check['declared_thickness']['t_min_mm']==1
    with pytest.raises(M.OwnerMeshError,match='below'):
        S.preserve_minimum_thickness(obj,after,[5],minimum_mm=3.1)
    after['vertices_world_mm'][0][0]=2.5
    with pytest.raises(M.OwnerMeshError,match='below'):
        S.preserve_minimum_thickness(obj,after,[5],preserve_original=False,minimum_mm=.1)
    after=copy.deepcopy(obj);after['vertices_world_mm'][0][1]+=.1
    with pytest.raises(M.OwnerMeshError,match='graph deliberately unavailable'):
        S.preserve_minimum_thickness(obj,after,[5])


def test_two_moving_selected_patches_cannot_use_each_others_old_clearance():
    source,_=complex_part();obj=source['objects'][0];after=copy.deepcopy(obj)
    # Each change is individually clear of the other patch's *old* position.
    # Together the top and underside cross, while total signed volume is positive.
    after['vertices_world_mm'][4][2]=1.2
    after['vertices_world_mm'][13][2]=1.3
    assert M.physical(after)['volume_mm3']>0
    with pytest.raises(ValueError,match='separately selected patches'):
        T.local_patches(obj,after,list(range(8)),preserve_original=False,minimum_mm=.1)
