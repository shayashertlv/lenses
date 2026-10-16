"""Independent regression: a topology edit preserves the active/render UV selection."""
from pathlib import Path
import copy
import json
import sys
import tempfile
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from glasses3d.tests.blender.owner_surface import create, capture
from glasses3d.g3d import owner_mesh as M, owner_mesh_bpy, owner_surface


def main():
    with tempfile.TemporaryDirectory(prefix="g3d-independent-uv-") as temp:
        root = Path(temp)
        obj = create()
        # One internal selected edge splits; a patch boundary and an opposite
        # edge stay intact. Their authored sharp and UI-selection flags survive.
        for edge in obj.data.edges:
            pair=tuple(sorted(edge.vertices))
            edge.use_edge_sharp=pair in {(1,4),(0,1),(9,10)}
            edge.select=pair in {(1,4),(9,10)}
        second = obj.data.uv_layers.new(name="RenderUV")
        second.active_render = True
        second.active_clone = True
        obj.data.uv_layers.active_index = 1
        uv_state = lambda: {"active_index": obj.data.uv_layers.active_index, "layers": [
            [u.name, u.active_render, u.active_clone] for u in obj.data.uv_layers]}
        old_uv = uv_state()
        before, path = capture(root, "before")
        row = before["objects"][0]
        scope = {"object_id": row["id"], "vertex_weights": [{"vertex_id": i, "weight": 1.} for i in range(9)],
                 "fixed_vertex_ids": [], "affected_polygon_ids": [i for i, p in enumerate(row["polygons"]) if set(p) & set(range(9))]}
        operation = {"id": "refine", "kind": "refine_curved_surface", "params": {"strength": 1.},
                     "target_ids": ["T1"], "scope": scope, "marking_attachments": []}
        result = owner_mesh_bpy.apply({"snapshot_path": str(path), "snapshot_sha256": M.digest(before),
                                      "operations": [operation]}, root)
        assert result["status"] == "ok", result
        after, _ = capture(root, "after")
        checked = owner_surface.verify(before, after, [operation])
        planned,_=owner_surface.state(before,[operation])
        splits=planned['objects'][0]['_surface_edge_splits']
        mid=next(r['vertex_id'] for r in splits if r['edge']==[1,4])
        actual=after['objects'][0]
        def edge_flags(record,name):
            values=next(a['values'] for a in record['attributes'] if a['name']==name)
            return {tuple(sorted(e)):bool(v[0]) for e,v in zip(record['edges'],values)}
        sharp=edge_flags(actual,'sharp_edge');selected=edge_flags(actual,'.select_edge')
        assert (1,4) not in sharp
        assert all(sharp[e] and selected[e] for e in [(1,mid),(4,mid),(9,10)])
        assert sharp[(0,1)] and not selected[(0,1)]
        for attribute in ('sharp_edge','.select_edge'):
            hostile=copy.deepcopy(after);row=hostile['objects'][0]
            i=next(i for i,e in enumerate(row['edges']) if tuple(sorted(e))==(1,mid))
            values=next(a['values'] for a in row['attributes'] if a['name']==attribute)
            values[i][0]=False
            try:owner_surface.verify(before,hostile,[operation])
            except M.OwnerMeshError:pass
            else:raise AssertionError('lost child edge flag was accepted: '+attribute)
        print("UV_DIAGNOSTIC " + json.dumps({"before": old_uv, "after": uv_state(), "scope_ok": checked["ok"]}))
        assert uv_state() == old_uv, "refinement changed the active, render or clone UV map"
        # The persisted normal witness must not make a subsequent selected
        # refinement or an established finite edit unsupported.
        before, path = capture(root, "second-before")
        row = before["objects"][0]
        vectors = owner_surface.face_vectors(np.asarray(row["vertices_world_mm"]), row["polygons"])
        ids = {vid for i, polygon in enumerate(row["polygons"]) if vectors[i, 2] > .8 for vid in polygon}
        operation["scope"] = {"object_id": row["id"],
            "vertex_weights": [{"vertex_id": i, "weight": 1.} for i in sorted(ids)],
            "fixed_vertex_ids": [], "affected_polygon_ids": [i for i, p in enumerate(row["polygons"]) if set(p) & ids]}
        operation["params"] = {"strength": .5}
        result = owner_mesh_bpy.apply({"snapshot_path": str(path), "snapshot_sha256": M.digest(before),
                                      "operations": [operation]}, root)
        assert result["status"] == "ok", result
        after, _ = capture(root, "second-after")
        assert owner_surface.verify(before, after, [operation])["ok"]
        before, path = capture(root, "rotate-before")
        row = before["objects"][0]
        operation.update(kind="rotate_region", params={"axis_points_mm": [[0, 0, 0], [1, 0, 0]], "delta_deg": 1.})
        operation["scope"] = {"object_id": row["id"],
            "vertex_weights": [{"vertex_id": i, "weight": 1.} for i in range(len(row["vertices_local"]))],
            "fixed_vertex_ids": [], "affected_polygon_ids": list(range(len(row["polygons"])))}
        result = owner_mesh_bpy.apply({"snapshot_path": str(path), "snapshot_sha256": M.digest(before),
                                      "operations": [operation]}, root)
        assert result["status"] == "ok", result
        after, _ = capture(root, "rotate-after")
        output = Path(sys.argv[sys.argv.index("--") + 1])
        output.write_text(json.dumps({"before": before, "after": after, "operations": [operation]}))
        print("INDEPENDENT_UV_REVIEW passed")


if __name__ == "__main__":
    main()
