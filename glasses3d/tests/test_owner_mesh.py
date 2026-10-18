"""Independent small closed solids for finite owner edits; no product fixtures."""
import copy
import numpy as np
import pytest

from glasses3d.g3d import owner_mesh as M, owner_surface as S
from glasses3d.agent.owner_edits import corrected_operations, verify_snapshots, confirmed_transition, _advance


def box(side=1, role="temple"):
    v = np.array([[2,0,0],[5,0,0],[5,2,0],[2,2,0],
                  [2,0,-6],[5,0,-6],[5,2,-6],[2,2,-6]], float)
    p = [[0,1,2,3],[4,7,6,5],[0,4,5,1],[3,2,6,7],[0,3,7,4],[1,5,6,2]]
    if side < 0:
        v[:,0] *= -1
        p = [row[::-1] for row in p]
    t = [[row[0],row[k],row[k+1]] for row in p for k in range(1,len(row)-1)]
    return {"id":"O0001", "name":"Closed test part", "mesh_name":"Test mesh", "role":role,
            "side":side, "vertices_world_mm":v.tolist(), "vertices_local":v.tolist(), "polygons":p,
            "triangles":t, "triangle_polygons":[i for i in range(6) for _ in range(2)],
            "corner_normals":[[1,0,0] for _ in range(24)], "topology_sha256":"topology",
            "positions_sha256":"positions", "shared_mesh_users":["Closed test part"],
            "evaluated":{"topology_matches_source":True,"positions_match_source":True},
            "aliases":{"status":"verified_topology","options":{"t_min_mm":1}}}


def operation(obj, delta=None):
    ids = [1,2,5,6]
    faces = [i for i,p in enumerate(obj["polygons"]) if set(ids).intersection(p)]
    return {"id":"item-1", "kind":"translate_region", "target_ids":["F1"],
            "scope":{"object_id":obj["id"],"vertex_weights":[{"vertex_id":i,"weight":1.} for i in ids],
                     "fixed_vertex_ids":[0,3,4,7],"affected_polygon_ids":faces},
            "params":{"delta_mm":delta or [obj["side"]*.5,0,0]}}


def snapshot(obj):
    return {"schema":"g3d.mesh_snapshot/1","origin_mm":[0,0,0],"objects":[obj]}


@pytest.mark.parametrize("side", [-1,1])
def test_actual_thickness_and_local_patch(side):
    obj=box(side); before=copy.deepcopy(obj)
    assert M.physical(obj)["thickness"]["minimum_mm"] == pytest.approx(3.)
    patch=M.plan(snapshot(obj),[operation(obj)])
    assert obj == before
    got=patch["operations"][0]
    assert got["changed_vertex_ids"] == [1,2,5,6]
    assert got["physical"]["thickness"]["minimum_mm"] == pytest.approx(3.5)


def test_owner_target_does_not_rewrite_or_gate_on_photo_reading():
    obj=box(); obj["reading"]={"X":5,"sd":.001,"state":"certain"}
    patch=M.plan(snapshot(obj),[operation(obj)])
    target,_=S.state(snapshot(obj),[operation(obj)])
    assert target['objects'][0]['vertices_world_mm'][1][0] == 5.5
    assert obj["reading"] == {"X":5,"sd":.001,"state":"certain"}


@pytest.mark.parametrize("change", ["too_thin","reversed","open","missing_support","shared_mesh"])
def test_physical_or_scope_refusal(change):
    obj=box(); op=operation(obj)
    if change=="too_thin": op["params"]["delta_mm"]=[-2.5,0,0]
    elif change=="reversed": obj["polygons"]=[p[::-1] for p in obj["polygons"]];obj["triangles"]=[p[::-1] for p in obj["triangles"]]
    elif change=="open": obj["polygons"]=obj["polygons"][:-1]
    elif change=="missing_support": op["scope"]["affected_polygon_ids"]=[]
    else: obj["shared_mesh_users"].append("Another object")
    with pytest.raises(M.OwnerMeshError): M.plan(snapshot(obj),[op])


def test_scope_rejects_unrelated_position_material_or_uv():
    obj=box(); obj["material_sha256"]="material";obj["uv_layers"]=[]
    source=snapshot(obj); op=operation(obj)
    for key,value in [("material_sha256","changed"),("uv_layers",[{}])]:
        current=_advance(source,[op]);current["objects"][0][key]=value
        with pytest.raises(M.OwnerMeshError):
            verify_snapshots(source,current,[op])
    current=_advance(source,[op]);current["objects"][0]["vertices_local"][0][0]+=.1
    with pytest.raises(M.OwnerMeshError,match="vertices_local"):
        verify_snapshots(source,current,[op])


def test_rotating_only_outer_boundary_cannot_create_a_zero_thickness_rim():
    obj=box(); op=operation(obj)
    op["kind"]="rotate_region";op["params"]={"axis_points_mm":[[5,0,0],[5,0,-6]],"delta_deg":5}
    with pytest.raises(M.OwnerMeshError, match="minimum graph thickness"):
        M.plan(snapshot(obj),[op])


def test_taper_changes_angle_at_fixed_graph_coordinates():
    obj=box(); op=operation(obj)
    op["kind"]="taper_region";op["params"]={"axis_points_mm":[[5,0,0],[5,0,-6]],"delta_mm":[-.5,0,0]}
    result=M.plan(snapshot(obj),[op]);assert result["operations"][0]["physical"]["thickness"]["minimum_mm"]==pytest.approx(2.5)


def test_correction_never_expands_kind_axis_or_scope():
    obj=box();op=operation(obj)
    sealed={"operations":[op],"correction":False,"confirmation_sha256":"owner words"}
    rows=[{"operation_id":"item-1","params":{"delta_mm":[.75,0,0]}}]
    with pytest.raises(M.OwnerMeshError): corrected_operations(sealed,rows)
    sealed["correction"]=True
    got=corrected_operations(sealed,rows)[0]
    assert got["scope"]==op["scope"] and got["params"]["delta_mm"]==[.75,0,0]
    rows[0]["scope"]={}
    with pytest.raises(M.OwnerMeshError):corrected_operations(sealed,rows)


def test_generic_parts_require_source_bound_thickness_certificate():
    obj=box(role="frame");obj["aliases"]={"status":"unavailable"}
    assert M.physical(obj)["thickness"]["status"] == "requires_edit_certificate"
    patch=M.plan(snapshot(obj),[operation(obj)])
    check=patch['operations'][0]['minimum_thickness']
    assert check['original_minimum_preserved']
    assert check['unchanged_remainder_proven']


def test_same_vertices_wrong_amount_is_not_owner_authority():
    obj=box(); source=snapshot(obj); op=operation(obj)
    sealed={"snapshot":source,"all_operations":[op],"source_fingerprint":"source","receipts":[],"selected_preview":{'id':'picked'}}
    correct=_advance(source,[op])
    assert [o["id"] for o in confirmed_transition(sealed,correct,"source")] == [op["id"]]
    hostile=copy.deepcopy(op);hostile["params"]["delta_mm"]=[10.,0,0]
    wrong=_advance(source,[hostile])
    with pytest.raises(M.OwnerMeshError,match="confirmed surface operation"):
        verify_snapshots(source,wrong,[op])
    with pytest.raises(M.OwnerMeshError,match="confirmed surface operation"):
        confirmed_transition(sealed,wrong,"source")


def test_general_tool_requires_preview_and_the_complete_selected_group():
    source=snapshot(box());one=operation(source["objects"][0]);two=copy.deepcopy(one)
    two["id"]="item-2";two["params"]["delta_mm"]=[.25,0,0]
    # Distinct supports make the subset independently identifiable.
    two["scope"]["vertex_weights"]=[{"vertex_id":5,"weight":1.},{"vertex_id":6,"weight":1.}]
    two["scope"]["affected_polygon_ids"]=[1,2,3,5]
    one["scope"]["vertex_weights"]=[{"vertex_id":1,"weight":1.},{"vertex_id":2,"weight":1.}]
    one["scope"]["affected_polygon_ids"]=[0,2,3,5]
    sealed={"snapshot":source,"all_operations":[one,two],"source_fingerprint":"source","receipts":[]}
    middle=_advance(source,[two])
    with pytest.raises(M.OwnerMeshError,match='rendered preview'):
        confirmed_transition(sealed,middle,'source')
    sealed['selected_preview']={'id':'picked'}
    with pytest.raises(M.OwnerMeshError,match='confirmed surface operation'):
        confirmed_transition(sealed,middle,'source')
    final=_advance(source,[one,two])
    assert [o['id'] for o in confirmed_transition(sealed,final,'source')]==['item-1','item-2']


def test_selected_derived_bbox_can_follow_verified_vertices_but_other_summary_cannot():
    obj=box();source=snapshot(obj);op=operation(obj)
    source["scene_state"]={"meshes":{obj["mesh_name"]:"old geometry"},"objects":{obj["name"]:"unchanged object"},
        "summary":{obj["name"]:{"bbox_mm":[[2,0,-6],[5,2,0]],"materials":["original"]},
                   "Unselected object":{"bbox_mm":[[0,0,0],[1,1,1]]}}}
    current=_advance(source,[op])
    current["scene_state"]["meshes"][obj["mesh_name"]]="new geometry"
    current["scene_state"]["summary"][obj["name"]]["bbox_mm"]=[[2,0,-6],[5.5,2,0]]
    assert verify_snapshots(source,current,[op])["ok"]
    for target,key,value in [(obj["name"],"materials",["unrequested"]),
                             ("Unselected object","bbox_mm",[[0,0,0],[2,2,2]])]:
        hostile=copy.deepcopy(current);hostile["scene_state"]["summary"][target][key]=value
        with pytest.raises(M.OwnerMeshError,match="scene or datablock"):
            verify_snapshots(source,hostile,[op])
