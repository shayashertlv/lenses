"""Independent reproductions of surface-edit scope and thickness failures."""
from pathlib import Path

import copy
import json
import numpy as np
import pytest

from glasses3d.g3d import owner_surface, owner_mesh
from glasses3d.tests.test_owner_surface import patch


def test_quadratic_normal_delta_keeps_geometry_uv_source_normals_and_thickness(monkeypatch):
    from glasses3d.g3d import owner_refine
    from glasses3d.tests.test_owner_refine import triangle_patch
    source,operation=triangle_patch();saved=copy.deepcopy(source)
    operation['params']['strength']=.5
    revised,checks=owner_surface.state(source,[operation])
    with monkeypatch.context() as context:
        context.setattr(owner_refine,'sample_normal',lambda points,normals,strength:owner_refine.mean_normal(normals))
        former,former_checks=owner_surface.state(source,[operation])
    assert source==saved
    a,b=former['objects'][0],revised['objects'][0]
    for key in ('vertices_local','vertices_world_mm','polygons','triangles','uv_layers',
                '_surface_edge_splits','_surface_polygon_parents','_surface_sharp_edges'):
        assert a[key]==b[key],key
    assert checks[0]['minimum_thickness_preservation']==former_checks[0]['minimum_thickness_preservation']
    original_count=len(source['objects'][0]['vertices_local'])
    cursor=0;inserted_changed=False
    for polygon in b['polygons']:
        for vertex in polygon:
            if vertex<original_count:assert a['corner_normals'][cursor]==b['corner_normals'][cursor]
            else:inserted_changed|=a['corner_normals'][cursor]!=b['corner_normals'][cursor]
            cursor+=1
    assert inserted_changed
    assert owner_surface.verify(source,revised,[operation])['ok']


def test_quadratic_normal_strength_blends_normalized_fields_and_order_is_symmetric():
    from glasses3d.g3d import owner_refine as R
    points=np.array([[0.,0.,0.],[3.,0.,1.],[1.,2.,.4]])
    normals=np.array([[0.,0.,1.],[-1.,0.,1.],[0.,-.3,1.]])
    normals/=np.linalg.norm(normals,axis=1)[:,None]
    linear=R.mean_normal(normals);full=R.sample_normal(points,normals,1.)
    expected=linear+full;expected/=np.linalg.norm(expected)
    np.testing.assert_allclose(R.sample_normal(points,normals,.5),expected,atol=1e-15)
    np.testing.assert_allclose(R.sample_normal(points[[2,0,1]],normals[[2,0,1]],.5),expected,atol=1e-15)
    with pytest.raises(ValueError,match='distinct finite endpoints'):
        R.sample_normal(np.array([[0.,0.,0.],[0.,0.,0.]]),normals[:2],.5)


def test_refinement_cannot_cross_the_fixed_opposite_surface():
    source, operation = patch()
    obj = source["objects"][0]
    obj["role"] = "lens"
    for index in range(9):
        obj["vertices_world_mm"][index + 9][2] = obj["vertices_world_mm"][index][2] - .05
    assert owner_mesh.physical(obj)["volume_mm3"] == pytest.approx(.8)
    with pytest.raises(owner_mesh.OwnerMeshError):
        owner_surface.state(source, [operation])


def test_actual_refinement_keeps_active_render_and_clone_uv_maps(tmp_path):
    from glasses3d.g3d_host import blender
    from glasses3d.agent.owner_edits import verify_snapshots
    if blender.blender_exe() is None:
        pytest.skip("Blender is not installed")
    result_path = tmp_path / "witness-lifecycle.json"
    result = blender.run(None, Path(__file__).parent / "blender/owner_surface_review.py", [str(result_path)], timeout_s=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "INDEPENDENT_UV_REVIEW" in result.stdout, result.stdout + result.stderr
    snapshots = json.loads(result_path.read_text())
    assert verify_snapshots(snapshots["before"], snapshots["after"], snapshots["operations"])["ok"]


def test_mixed_shading_then_taper_transports_surface_and_marking_normals():
    from glasses3d.g3d import owner_markings
    from glasses3d.tests.test_owner_markings import fixture

    source, operation = fixture()
    for obj in source["objects"]:
        normals = owner_surface.face_vectors(np.asarray(obj["vertices_local"]), obj["polygons"])
        obj["corner_normals"] = [normal.tolist() for normal, polygon in zip(normals, obj["polygons"])
                                 for _ in polygon]
    repair = copy.deepcopy(operation)
    repair.update(id="repair", kind="repair_shading", params={"strength": 1., "mode": "flat"})
    operation.update(id="taper", kind="taper_region",
                     params={"axis_points_mm": [[5, 0, 0], [5, 0, -6]], "delta_mm": [.5, 0, 0]})
    operations = owner_markings.seal(source, [repair, operation])
    after, _ = owner_surface.state(source, operations)
    for obj in after["objects"]:
        normal = owner_surface.face_vectors(np.asarray(obj["vertices_world_mm"]), obj["polygons"])[5]
        np.testing.assert_allclose(obj["corner_normals"][-1], normal, atol=1e-6)
        assert normal[2] > .08


def test_opt_in_refinement_can_relax_interior_but_keeps_boundary_and_opposite_side():
    source, operation = patch(False)
    source["objects"][0]["vertices_local"][4][2] = 2.3
    operation["params"]["relax_interior"] = True
    after, reports = owner_surface.state(source, [operation])
    original = np.asarray(source["objects"][0]["vertices_local"])
    refined = np.asarray(after["objects"][0]["vertices_local"])
    assert 2 < refined[4, 2] < original[4, 2]
    unchanged = [i for i in range(len(original)) if i != 4]
    np.testing.assert_array_equal(refined[unchanged], original[unchanged])
    assert reports[0]["relax_interior"] is True
    assert owner_surface.verify(source, after, [operation])["ok"]
