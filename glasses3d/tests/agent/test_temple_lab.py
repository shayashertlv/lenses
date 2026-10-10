"""Independent lab controls; synthetic geometry is never a product answer."""
import asyncio
import copy
import json

import numpy as np
from PIL import Image
from agents.tool_context import ToolContext
import pytest

from glasses3d.agent.temple_lab.geometry import blank_template
from glasses3d.agent.temple_lab.lab import Lab, write_json_atomic
from glasses3d.agent.temple_lab.tools import registry
from glasses3d.agent.loop import ToolContract, contract_problems
from glasses3d.agent.temple_lab.cli import HERE


def box():
    vertices = np.array([[x, y, z] for x in (60., 64.) for y in (-12., 12.) for z in (-130., -10.)])
    faces = np.array([[0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5], [0, 4, 5], [0, 5, 1],
                      [2, 3, 7], [2, 7, 6], [0, 2, 6], [0, 6, 4], [1, 5, 7], [1, 7, 3]])
    return vertices, faces


@pytest.fixture
def lab(tmp_path):
    (tmp_path / "inputs").mkdir()
    write_json_atomic(tmp_path / "inputs/lab.json", {"assets": [], "photos": []})
    x = Lab(tmp_path)
    im = Image.new("RGB", (200, 120), "white")
    im.save(tmp_path / "inputs/a.png")
    x.register_image("photo", tmp_path / "inputs/a.png", kind="photo")
    x.state["template"], _ = blank_template(*box())
    x.mark("photo", 0, [{"id": "edge", "points": [[10, 10], [100, 80]],
                         "kind": "uncertain", "evidence": "synthetic control"}])
    return x


def proposal():
    coords = {"P": [5, 0, 0], "U": [20, 10, 0], "Q": [35, 0, 0], "L": [20, -10, 0], "M": [20, 0, 1]}
    nodes = [dict(id=k, d_mm=d, y_mm=y, relief_mm=r) for k, (d,y,r) in coords.items()]
    facets = [dict(id=k, nodes=p, crease=None, rise_per_mm=None) for k,p in
              {"NW": ["P", "M", "U"], "NE": ["U", "M", "Q"],
               "SE": ["Q", "M", "L"], "SW": ["L", "M", "P"]}.items()]
    return nodes, facets


def test_template_is_measured_blank():
    s, diag = blank_template(*box())
    assert not s["nodes"] and not s["facets"]
    assert diag["length_mm"] == 120
    assert np.allclose(np.array(s["inner"])[:, 1], 60)
    assert np.allclose(np.array(s["thickness"])[:, 1], 4)
    assert diag["inner_linearization_max_mm"] < 1e-10


def test_crop_coordinates_and_no_resize(lab):
    result = lab.image("photo", [20, 30, 100, 90])
    row = result["image"]
    assert row["size_px"] == [80, 60] and row["source_rect"] == [20, 30, 100, 90]
    with pytest.raises(ValueError): lab.image("photo", [-1, 0, 10, 10])
    with pytest.raises(KeyError): lab.image("../a", None)


def test_marks_replace_delete_and_stale_is_atomic(lab):
    before = copy.deepcopy(lab.state)
    with pytest.raises(ValueError): lab.mark("photo", 0, [])
    assert lab.state == before
    with pytest.raises(ValueError):
        lab.mark("photo", 1, [{"id": "bad", "points": [[-1, 0], [20, 30]], "kind": "crease"}])
    assert lab.state == before
    result = lab.mark("photo", 1, [])
    assert result["revision"] == 2 and result["marks"] == []


def test_depth_is_absolute_preserves_layout_and_failed_edit(lab):
    nodes, facets = proposal()
    first = lab.candidate(0, nodes, facets, ["photo:1:edge"], "synthetic control")
    second = lab.depth(1, [{"node": "M", "relief_mm": 2.5}], [], "more depth")
    assert second["spec"]["nodes"]["M"][2] == 2.5
    for n in first["spec"]["nodes"]:
        assert first["spec"]["nodes"][n][:2] == second["spec"]["nodes"][n][:2]
    assert first["spec"]["facets"] == second["spec"]["facets"]
    with pytest.raises(ValueError): lab.depth(1, [{"node": "M", "relief_mm": 5}], [], "stale")
    with pytest.raises(ValueError): lab.depth(2, [{"node": "M", "relief_mm": -10}], [], "too thin")
    assert len(lab.state["candidates"]) == 2


def test_generic_graph_and_bad_evidence(lab):
    nodes, facets = proposal()
    with pytest.raises(ValueError): lab.candidate(0, nodes, facets, ["photo:0:edge"], "stale evidence")
    facets.append(dict(facets[0], id="overlap"))
    with pytest.raises(ValueError): lab.candidate(0, nodes, facets, ["photo:1:edge"], "overlap")
    assert lab.state["candidates"] == []


def test_sdk_schemas_contract_and_real_image_blocks(lab):
    tools = registry(lab)
    text = (HERE / "instructions.txt").read_text()
    assert not contract_problems(ToolContract(frozenset(t.name for t in tools)), instructions=text, brief="test", tools=tools)
    tool = next(t for t in tools if t.name == "lab_image")
    assert tool.strict_json_schema and tool.params_json_schema["additionalProperties"] is False
    ctx = ToolContext(context=None, tool_name="lab_image", tool_call_id="control", tool_arguments="{}")
    output = asyncio.run(tool.on_invoke_tool(ctx, json.dumps({"image_id": "photo", "crop": None})))
    assert output[1].type == "image" and output[1].image_url.startswith("data:image/png;base64,")
    assert json.loads(output[0].text)["status"] == "ok"


def test_changed_image_refused(lab):
    (lab.root / "inputs/a.png").write_bytes(b"changed")
    with pytest.raises(ValueError): lab.image("photo", None)


def test_refine_proposes_without_accepting(lab):
    im = Image.new("RGB", (200,120), "white")
    from PIL import ImageDraw
    ImageDraw.Draw(im).rectangle((100,0,199,119), fill="black")
    im.save(lab.root / "inputs/edge.png")
    lab.register_image("edge-photo", lab.root / "inputs/edge.png", kind="photo")
    lab.mark("edge-photo",0,[dict(id="ridge",points=[[98,10],[98,110]],kind="uncertain",evidence="control")])
    before = copy.deepcopy(lab.state["marks"])
    out = lab.refine("edge-photo",1,"ridge")
    assert out["status"] == "proposal" and not out["accepted"]
    assert abs(out["proposal"]["p0"][0]-99.5) < 1
    assert lab.state["marks"] == before


def test_mapping_round_trip_and_collinear_refusal(lab):
    out = lab.map_marks("photo",1,[dict(pixel=p,chart_mm=q) for p,q in
                                   [([0,0],[0,20]),([100,0],[50,20]),([0,100],[0,-30])]])
    assert np.allclose(out["chart_segments_mm"]["edge"],[[5,15],[50,-20]])
    assert not lab.state["candidates"]
    with pytest.raises(ValueError):
        lab.map_marks("photo",1,[dict(pixel=[x,x],chart_mm=[x,x]) for x in (0,10,20)])


def test_profile_revision_is_retained_by_new_layout(lab):
    s = copy.deepcopy(lab.state["template"])
    thicker = [[d,t+1] for d,t in s["thickness"]]
    out = lab.profile(0,s["outline"]["top"],s["outline"]["bottom"],s["inner"],thicker,s["zone_end_d"],"control")
    assert out["revision"] == 1
    nodes, facets = proposal()
    out = lab.candidate(1,nodes,facets,["photo:1:edge"],"control")
    assert out["spec"]["thickness"] == thicker


def test_round_tip_trim_is_explicit():
    v,f = box()
    # One end collapses to a horizontal line; the rest is a triangular longitudinal prism.
    v[v[:,2] == -130,1] = 0
    s,diag = blank_template(v,f)
    assert 0 < diag["cap_trim_mm"][1] < 2
    assert s["outline"]["top"][-1][0] < 120


def test_bad_native_apply_restores_previous_scene(lab):
    class Scene:
        head = {"id":"before","fingerprint":"before"}
        restored = []
        def restore_checkpoint(self, cid, **kw):
            self.restored.append(cid); self.head={"id":cid,"fingerprint":cid}
            return {"status":"ok"}
        def call_mutating(self,*args,**kw):
            return {"status":"failed","result":{"status":"failed","error":"independent injected native failure"}}
    lab.s = Scene(); lab.state["start_checkpoint"]="start"
    nodes,facets=proposal(); lab.candidate(0,nodes,facets,["photo:1:edge"],"control")
    out=lab.apply(1,"before","control-call")
    assert out["status"]=="failed" and lab.s.restored==["start","before"]
    assert lab.s.head["id"]=="before" and not lab.state["applied"]


def test_worker_failure_remains_fatal(lab):
    from glasses3d.agent.journal import WorkerFailed
    def fail(): raise WorkerFailed("injected worker failure", {"id":"control"})
    lab.inspect = fail
    tool = next(t for t in registry(lab) if t.name == "lab_inspect")
    ctx = ToolContext(context=None,tool_name=tool.name,tool_call_id="control",tool_arguments="{}")
    with pytest.raises(WorkerFailed): asyncio.run(tool.on_invoke_tool(ctx,"{}"))


def test_resource_bounds_and_inconsistent_plane_refused(lab):
    nodes,facets=proposal()
    facets[0]["rise_per_mm"] = 1
    with pytest.raises(ValueError): lab.candidate(0,nodes,facets,["photo:1:edge"],"control")
    nodes,facets=proposal(); nodes[0]["d_mm"]=100000000
    with pytest.raises(ValueError): lab.candidate(0,nodes,facets,["photo:1:edge"],"control")
    assert not lab.state["candidates"]


def test_raised_native_failure_restores_before_propagation(lab):
    from glasses3d.agent.journal import WorkerFailed
    class Scene:
        head={"id":"before","fingerprint":"before"}
        restored=[]
        def restore_checkpoint(self,cid,**kw):
            self.restored.append(cid); self.head={"id":cid,"fingerprint":cid}
            return {"status":"ok"}
        def call_mutating(self,*args,**kw): raise WorkerFailed("native control",self.head)
    lab.s=Scene(); lab.state["start_checkpoint"]="start"
    nodes,facets=proposal(); lab.candidate(0,nodes,facets,["photo:1:edge"],"control")
    with pytest.raises(WorkerFailed): lab.apply(1,"before","control-call")
    assert lab.s.restored==["start","before"] and lab.s.head["id"]=="before"
