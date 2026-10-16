import copy
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from glasses3d.agent.tools import model_map as M
from glasses3d.agent.tools import model_map_geometry as G
from glasses3d.agent.tools import arview


def snapshot():
    v = [[-1,-1,0],[1,-1,0],[1,1,0],[-1,1,0],[-1,-1,-1],[1,-1,-1],[1,1,-1],[-1,1,-1]]
    ps = [[0,1,2,3],[4,7,6,5],[0,4,5,1],[1,5,6,2],[2,6,7,3],[3,7,4,0]]
    edges = sorted({tuple(sorted((a,b))) for p in ps for a,b in zip(p,p[1:]+p[:1])})
    return {"schema":"g3d.mesh_snapshot/1","fingerprint":"f"*64,"objects":[{
        "id":"O0001","name":"Current cube","mesh_name":"mesh","role":"frame","side":0,
        "vertices_local":v,"vertices_world_mm":v,"polygons":ps,"edges":[list(e) for e in edges],
        "topology_sha256":"a"*64,"positions_sha256":"b"*64,
        "evaluated":{"positions_match_source":True},
        "aliases":{"status":"verified_topology","facets":{"front":[0]},"points":{"A":0,"C":2}}}]}


def test_automatic_catalogue_preserves_actual_elements_and_named_current_coordinates():
    s = snapshot()
    s["objects"][0]["vertices_world_mm"][0][0] = -1.4
    result = G.catalogue(s)
    faces = [t for t in result["targets"].values() if t["kind"] == "face_region"]
    assert sorted(p for t in faces for p in t["polygon_ids"]) == list(range(6))
    named = next(t for t in faces if "front" in t["aliases"])
    assert named["centroid_mm"][0] == pytest.approx(-.1)
    point = next(t for t in result["targets"].values() if t["aliases"] == ["A"])
    assert point["centroid_mm"] == [-1.4,-1,0]
    assert G.catalogue(copy.deepcopy(s)) == result


def test_general_object_without_specialist_metadata_is_discovered():
    s = snapshot(); s["objects"][0]["aliases"] = {}
    result = G.catalogue(s)
    assert len([t for t in result["targets"].values() if t["kind"]=="face_region"]) == 6
    assert len([t for t in result["targets"].values() if t["kind"]=="edge_chain"]) == 12
    assert len([t for t in result["targets"].values() if t["kind"]=="point"]) == 8


def test_scope_accounts_for_shared_neighbors_and_rejects_expansion():
    s = snapshot(); targets = G.catalogue(s)["targets"]
    face = next(t for t in targets.values() if t["aliases"] == ["front"])
    scope = G.support(s,targets,[face["id"]],fixed_vertex_ids=[0])
    assert scope["affected_polygon_ids"] == [0,2,3,4,5]
    assert scope["normal_affected_polygon_ids"] == list(range(6))
    assert next(w["weight"] for w in scope["vertex_weights"] if w["vertex_id"]==0) == 0
    with pytest.raises(ValueError,match="zero support"):
        G.support(s,targets,[face["id"]],vertex_weights=[{"vertex_id":0,"weight":1}],fixed_vertex_ids=[0])
    with pytest.raises(ValueError,match="beyond"):
        G.support(s,targets,[face["id"]],vertex_weights=[{"vertex_id":7,"weight":1}])
    with pytest.raises(ValueError,match="finite"):
        G.support(s,targets,[face["id"]],vertex_weights=[{"vertex_id":0,"weight":float("nan")}])


def test_unmapped_evaluation_cannot_offer_editable_source_support():
    s = snapshot(); s["objects"][0]["evaluated"]["positions_match_source"] = False
    targets = G.catalogue(s)["targets"]
    with pytest.raises(ValueError,match="editable"):
        G.support(s,targets,["O0001"])


def test_unsupported_mesh_data_is_visible_but_cannot_offer_edit_support():
    s = snapshot()
    s["objects"][0]["unsupported_edit_state"] = ["Shape-key mesh"]
    targets = G.catalogue(s)["targets"]
    assert "O0001" in targets
    with pytest.raises(ValueError, match="editable"):
        G.support(s, targets, ["O0001"])


def test_export_triangle_correspondence_requires_unique_vertices():
    s = snapshot()
    geometry = SimpleNamespace(parts=[{"name":"Current cube"}],faces=np.array([[0,1,2],[0,2,3]]),
        vertices=np.array(s["objects"][0]["vertices_world_mm"],float)/1000,face_parts=np.array([0,0]))
    _,polys,reports = G.export_correspondence(s,geometry)
    assert polys.tolist() == [0,0]
    assert reports["0"]["matched"] == 2
    s["objects"][0]["vertices_world_mm"].append([-1,-1,0])
    _,polys,reports = G.export_correspondence(s,geometry)
    assert polys.tolist() == [-1,-1]
    assert reports["0"]["ambiguous_or_unmatched_vertices"] == 1


def bundle(tmp_path):
    mid="m-"+"a"*20; base=tmp_path/"work/model_maps"/mid
    base.mkdir(parents=True)
    snap=snapshot(); (base/"snapshot.json").write_text(json.dumps(snap))
    asset={"path":(base/"snapshot.json").relative_to(tmp_path).as_posix(),"sha256":arview.sha256(base/"snapshot.json")}
    manifest={"schema":M.SCHEMA,"map_id":mid,"snapshot":asset,"assets":[asset],"source":{"fingerprint":"f"*64},**G.catalogue(snap)}
    (base/"map.json").write_text(json.dumps(manifest)); sha=arview.sha256(base/"map.json")
    fid=next(t["id"] for t in manifest["targets"].values() if t["aliases"]==["front"])
    scope=G.support(snap,manifest["targets"],[fid])
    receipt={"schema":"g3d.model_map_capture/1","map_sha256":sha,"annotated_target_ids":[fid],"support_scopes":[scope],"assets":[],"images":[]}
    path=base/"capture.json"; path.write_text(json.dumps(receipt))
    result={"status":"ok","capture_receipt":path.relative_to(tmp_path).as_posix(),"capture_sha256":arview.sha256(path)}
    operation={"target_ids":[fid],"scope":scope}
    return mid,sha,result,operation


def test_understanding_requires_delivered_images_and_exact_shown_support(tmp_path):
    mid,sha,result,op=bundle(tmp_path)
    with pytest.raises(FileNotFoundError):
        M.validate_understanding(tmp_path,mid,sha,[result["capture_receipt"]],[op])
    M.mark_delivered(tmp_path,result)
    assert M.validate_understanding(tmp_path,mid,sha,[result["capture_receipt"]],[op])["annotated_target_ids"] == op["target_ids"]
    changed=copy.deepcopy(op); changed["scope"]["vertex_weights"][0]["weight"] = .5
    with pytest.raises(ValueError,match="exact influence"):
        M.validate_understanding(tmp_path,mid,sha,[result["capture_receipt"]],[changed])
    unseen=copy.deepcopy(op); unseen["target_ids"]=["O0001"]
    with pytest.raises(ValueError,match="did not show"):
        M.validate_understanding(tmp_path,mid,sha,[result["capture_receipt"]],[unseen])


def test_understanding_resolves_only_a_delivered_exact_scope_digest(tmp_path):
    mid,sha,result,op=bundle(tmp_path); M.mark_delivered(tmp_path,result)
    reference={"target_ids":op["target_ids"],"scope_ref_sha256":G.digest(op["scope"])}
    verified=M.validate_understanding(tmp_path,mid,sha,[result["capture_receipt"]],[reference])
    assert verified["operations"][0]["scope"]==op["scope"]
    assert "scope" not in reference
    reference["scope_ref_sha256"]="0"*64
    with pytest.raises(ValueError,match="Scope reference"):
        M.validate_understanding(tmp_path,mid,sha,[result["capture_receipt"]],[reference])


def test_visible_fixed_target_descriptor_produces_executable_zero_weight_scope(tmp_path):
    mid,sha,result,op=bundle(tmp_path)
    manifest=M.load_map(tmp_path,mid,sha)
    point=next(t for t in manifest["targets"].values() if t["kind"]=="point" and t["vertex_ids"]==[0])
    scope=M._scope(tmp_path,manifest,{"target_ids":op["target_ids"],"fixed_target_ids":[point["id"]]})
    assert point["id"] in scope["target_ids"] and scope["fixed_vertex_ids"]==[0]
    assert next(row for row in scope["vertex_weights"] if row["vertex_id"]==0)["weight"]==0


@pytest.mark.parametrize("kind",["point","edge_chain"])
@pytest.mark.parametrize("visible",[False,True])
def test_boundary_visibility_uses_its_continuous_rays_not_adjacent_pixel_count(tmp_path,monkeypatch,kind,visible):
    from PIL import Image
    for name in ("photo","drawing","ids"):
        Image.new("RGB",(80,60),"white").save(tmp_path/(name+".png"))
    row={"ref":"narrow","camera":{"width":80,"height":60},
         **{key:str(tmp_path/(name+".png")) for key,name in (("photo","photo"),("drawing","drawing"),("part_id","ids"))}}
    s=snapshot(); values=G.catalogue(s)["targets"]
    chosen=next(t for t in values.values() if t["kind"]==kind)
    monkeypatch.setattr(M.look_panels,"posed_geometry",lambda geometry,pose:geometry)
    monkeypatch.setattr(G,"first_hits",lambda *args:np.full((60,80),-1,dtype=int))
    monkeypatch.setattr(G,"project",lambda points,camera:(np.full((len(points),2),30.),np.ones(len(points))))
    monkeypatch.setattr(G,"visible_points",lambda points,camera,geometry:np.full(len(points),visible))
    out=M._annotate(row,s,[chosen],SimpleNamespace(),np.array(["O0001"],object),np.array([0]),[],tmp_path,0)
    assert out["annotated_target_ids"]==([chosen["id"]] if visible else [])
    assert out["legend"] == "numbered actual-mesh targets"
    if visible:
        assert out["labels"][0]["label"] in ("P1", "E1")
        assert out["labels"][0]["font_px"] >= 26
        assert out["target_legend"][0]["target_id"] == chosen["id"]


def test_movement_legend_requires_an_actual_support_scope(tmp_path, monkeypatch):
    from PIL import Image
    for name in ("photo", "drawing", "ids"):
        Image.new("RGB", (80,60), "white").save(tmp_path/(name+".png"))
    row={"ref":"support", "camera":{"width":80,"height":60},
         **{key:str(tmp_path/(name+".png")) for key,name in (("photo","photo"),("drawing","drawing"),("part_id","ids"))}}
    s=snapshot(); targets=G.catalogue(s)["targets"]
    chosen=next(t for t in targets.values() if t["aliases"]==["front"])
    scope=G.support(s, targets, [chosen["id"]])
    monkeypatch.setattr(M.look_panels,"posed_geometry",lambda geometry,pose:geometry)
    monkeypatch.setattr(G,"first_hits",lambda *args:np.zeros((60,80),dtype=int))
    monkeypatch.setattr(G,"project",lambda points,camera:(np.full((len(points),2),30.),np.ones(len(points))))
    monkeypatch.setattr(G,"visible_points",lambda points,camera,geometry:np.ones(len(points),dtype=bool))
    out=M._annotate(row,s,[chosen],SimpleNamespace(),np.array(["O0001"],object),np.array([0]),[scope],tmp_path,0)
    assert out["legend"]=="magenta: selected support, black: fixed, amber: normal influence"
    assert out["support_scopes"]==[scope]


def test_capture_or_snapshot_tampering_refuses(tmp_path):
    mid,sha,result,op=bundle(tmp_path); M.mark_delivered(tmp_path,result)
    path=tmp_path/result["capture_receipt"]
    doc=json.loads(path.read_text()); doc["annotated_target_ids"].append("O0001"); path.write_text(json.dumps(doc))
    with pytest.raises(ValueError,match="successfully delivered"):
        M.validate_understanding(tmp_path,mid,sha,[result["capture_receipt"]],[op])
    snap=path.parent/"snapshot.json"; snap.write_text("{}")
    with pytest.raises(ValueError,match="evidence changed"):
        M.load_map(tmp_path,mid,sha)


def test_cancellation_after_delivery_invalidates_understanding(tmp_path):
    mid,sha,result,op=bundle(tmp_path); result["call_id"]="cancelled-map"
    M.mark_delivered(tmp_path,result)
    state=tmp_path/"work/job_scene/state.json"; state.parent.mkdir()
    state.write_text(json.dumps({"unseen":["cancelled-map"]}))
    with pytest.raises(ValueError,match="successfully delivered"):
        M.validate_understanding(tmp_path,mid,sha,[result["capture_receipt"]],[op])


def test_same_kind_focus_does_not_expand_to_neighbor_faces():
    cat=G.catalogue(snapshot())
    fid=next(t["id"] for t in cat["targets"].values() if t["aliases"]==["front"])
    assert [t["id"] for t in M._select(cat,[fid],"faces")] == [fid]


def test_sdk_wrapper_schema_resolves_context_annotation():
    from agents import function_tool
    async def call(*args):
        raise AssertionError("schema construction must not execute a tool")
    tool=M.tool_for(SimpleNamespace(),lambda name:lambda fn:function_tool(fn,name_override=name),call)
    assert tool.name=="labelled_model_map"
    assert tool.params_json_schema["additionalProperties"] is False


def test_hidden_point_is_not_visible_merely_because_adjacent_face_is_visible():
    from glasses3d.agent.tools import look_panels
    vertices=np.array([[-1,-1,0],[1,-1,0],[1,1,0],[-1,1,0]],float)/1000
    geometry=SimpleNamespace(scene=look_panels._scene(vertices,np.array([[0,1,2],[0,2,3]])))
    matrix=np.eye(4); matrix[2,3]=-1
    camera={"view":matrix.ravel().tolist(),"fx":10000,"fy":10000,"cx":50,"cy":50,"width":100,"height":100}
    assert G.visible_points([[0,0,0],[0,0,-1]],camera,geometry).tolist() == [True,False]


def test_short_labels_stay_stable_across_views_and_resolve_to_exact_mesh_ids():
    from glasses3d.agent.tools import model_map_labels as L
    obj = snapshot()["objects"][0]
    left = {"id":"F127388", "kind":"face_region", "side":1,"aliases":["front"]}
    right = {**left,"id":"F123456","side":-1}
    labels = {}
    a = L.allocate(left,obj,labels,{left["id"]:"triangle nearest the frame, left arm"})
    b = L.allocate(right,obj,labels,{right["id"]:"triangle nearest the frame, right arm"})
    assert (a["label"],b["label"]) == ("T1","T2")
    assert L.allocate(left,obj,labels,{}) == a
    assert L.resolve("T1",{left["id"]:left,right["id"]:right},labels) == left["id"]
    assert L.resolve(left["id"],{left["id"]:left},labels) == left["id"]
    with pytest.raises(ValueError,match="fixed for this map"):
        L.allocate(left,obj,labels,{left["id"]:"different target"})


def test_complete_plain_legend_is_on_the_image_without_resizing_native_pixels():
    from PIL import Image
    from glasses3d.agent.tools import model_map_labels as L
    original = Image.new("RGB",(500,200),(17,43,65))
    result = L.append_legend(original,[{"label":"T1","description":"triangle nearest the frame, left arm"}],M._font(22))
    assert result.width == original.width and result.height > original.height
    np.testing.assert_array_equal(np.asarray(result)[:200],np.asarray(original))
    assert np.any(np.asarray(result)[200:] < 100), "legend text must be visibly drawn"


def test_understanding_carries_the_hash_bound_plain_legend(tmp_path):
    mid,sha,result,op=bundle(tmp_path)
    path=tmp_path/result["capture_receipt"]
    doc=json.loads(path.read_text())
    row={"label":"T1","target_id":op["target_ids"][0],"description":"front surface"}
    doc["target_legend"]=[row]
    path.write_text(json.dumps(doc)); result["capture_sha256"]=arview.sha256(path)
    M.mark_delivered(tmp_path,result)
    assert M.validate_understanding(tmp_path,mid,sha,[result["capture_receipt"]],[op])["legend"] == [row]


def test_clustered_labels_remain_readable_and_long_legends_do_not_shrink_native_tiles(tmp_path, monkeypatch):
    """Even coincident projected targets need distinct unobscured tags and legible delivered legends."""
    from PIL import Image
    width, height = 800, 474
    for name in ("photo", "drawing", "ids"):
        Image.new("RGB", (width, height), "white").save(tmp_path / (name + ".png"))
    row = {"ref": "cluster", "camera": {"width": width, "height": height},
           **{key: str(tmp_path / (name + ".png")) for key, name in
              (("photo", "photo"), ("drawing", "drawing"), ("part_id", "ids"))}}
    source = snapshot()
    point = next(t for t in G.catalogue(source)["targets"].values() if t["kind"] == "point")
    targets = [{**point, "id": f"P{i:04d}", "aliases": [f"corner {i}"]} for i in range(1, 25)]
    descriptions = {t["id"]: "longword" * 22 for t in targets}
    monkeypatch.setattr(M.look_panels, "posed_geometry", lambda geometry, pose: geometry)
    monkeypatch.setattr(G, "first_hits", lambda *args: np.full((height, width), -1, dtype=int))
    monkeypatch.setattr(G, "project", lambda points, camera:
                        (np.full((len(points), 2), 250.), np.ones(len(points))))
    monkeypatch.setattr(G, "visible_points", lambda points, camera, geometry: np.ones(len(points), dtype=bool))
    result = M._annotate(row, source, targets, SimpleNamespace(), np.array(["O0001"], object),
                         np.array([0]), [], tmp_path, 0, {}, descriptions)
    assert len(result["labels"]) == 24
    image = np.asarray(Image.open(result["full_pair"]))
    for i, label in enumerate(result["labels"]):
        x0, y0, x1, y1 = label["label_box"]
        assert label["font_px"] >= 26
        for other in result["labels"][i + 1:]:
            a, b, c, d = other["label_box"]
            assert not (x0 < c and x1 > a and y0 < d and y1 > b), "owner labels overlap"
        box = image[44 + y0:44 + y1, width + x0:width + x1]
        assert np.all(box[0] == 255) and np.all(box[-1] == 255)
        assert np.all(box[:, 0] == 255) and np.all(box[:, -1] == 255), "leader crossed an opaque label box"
    for record in result["images"]:
        delivered = Image.open(record["path"])
        assert max(delivered.size) <= 2048, "text must not force native labels to shrink during delivery"
        if "-legend-" in record["path"]:
            pixels = np.asarray(delivered)
            assert np.all(pixels[:, -8:] == 255), "long legend words clipped at the right edge"
