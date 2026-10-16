"""Independent material-group and actual rendered-slot regressions."""
from pathlib import Path
import sys
import tempfile

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from glasses3d.tests.blender.owner_surface import create, capture
from glasses3d.g3d import owner_mesh as M, owner_mesh_bpy, owner_surface as S


def operation(row, ident, kind, params):
    return {"id": ident, "kind": kind, "params": params, "target_ids": ["T1"], "marking_attachments": [],
            "scope": {"object_id": row["id"],
                      "vertex_weights": [{"vertex_id": i, "weight": 1.} for i in range(len(row["vertices_local"]))],
                      "fixed_vertex_ids": [], "affected_polygon_ids": list(range(len(row["polygons"])))}}


def run():
    with tempfile.TemporaryDirectory(prefix="g3d-material-review-") as temp:
        root = Path(temp)
        obj = create()
        second = bpy.data.materials.new("Second material")
        second.use_nodes = True
        obj.data.materials.append(second)
        for i, polygon in enumerate(obj.data.polygons):
            polygon.material_index = i % 2
        before, path = capture(root, "two-slots-before")
        row = before["objects"][0]
        operations = [operation(row, "mat-a", "material_adjust",
                                {"strength": 1., "material_index": 0, "values": {"roughness": .2}}),
                      operation(row, "mat-b", "material_adjust",
                                {"strength": 1., "material_index": 1, "values": {"roughness": .7}}),
                      operation(row, "shade", "repair_shading", {"strength": .6, "mode": "smooth"})]
        result = owner_mesh_bpy.apply({"snapshot_path": str(path), "snapshot_sha256": M.digest(before),
                                      "operations": operations}, root)
        assert result["status"] == "ok", result
        after, _ = capture(root, "two-slots-after")
        assert S.verify(before, after, operations)["ok"]
        assert np.isclose(obj.data.materials[0].node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value, .2)
        assert np.isclose(obj.data.materials[1].node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value, .7)
        assert obj.data.materials[0].name != "Original" and obj.data.materials[1].name != "Second material"
        assert not np.array_equal(before["objects"][0]["corner_normals"], after["objects"][0]["corner_normals"])
        obj = create()
        override = bpy.data.materials.new("Visible object override")
        override.use_nodes = True
        obj.material_slots[0].link = "OBJECT"
        obj.material_slots[0].material = override
        before, _ = capture(root, "object-linked-before")
        row = before["objects"][0]
        op = operation(row, "material", "material_adjust",
                       {"strength": 1., "material_index": 0, "values": {"roughness": .2}})
        try:
            S.state(before, [op])
        except M.OwnerMeshError as error:
            assert "object" in str(error).lower(), str(error)
        else:
            raise AssertionError("editing the hidden data material could leave the visible OBJECT override unchanged")
    print("OWNER_MATERIAL_REVIEW_PASSED")


if __name__ == "__main__":
    run()
