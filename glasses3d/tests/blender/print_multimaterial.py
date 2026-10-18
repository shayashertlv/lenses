"""Small native round trip: every primitive of a two-material print target is checked."""
import json
from pathlib import Path
import sys

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from glasses3d.g3d.export_glb import export_glb
from glasses3d.g3d.print_bpy import attach_print, print_uv

mode, directory = sys.argv[sys.argv.index("--") + 1:]
out = Path(directory)
out.mkdir(parents=True, exist_ok=True)
if mode == "build":
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = 0.001
    scene.unit_settings.length_unit = "MILLIMETERS"
    scene["blender_agent_axes"] = "+Y up; +Z front; numeric millimetres"
    scene["mdl_bridge_underside"] = [0.0, 0.0, 0.0]
    materials = []
    for i in range(2):
        material = bpy.data.materials.new(f"Pigment {i}")
        material.use_nodes = True
        material.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.1 + i * 0.2, 0.2, 0.3, 1)
        materials.append(material)
    for name, side in (("A", 1), ("B", -1)):
        bpy.ops.mesh.primitive_cube_add(size=1)
        ob = bpy.context.object
        ob.name = name
        ob["partRole"] = "frame"
        for vertex in ob.data.vertices:
            x, y, z = vertex.co
            vertex.co = (35 * side + x * 20, y * 16, z * 8)
        for material in materials:
            ob.data.materials.append(material)
        for polygon in ob.data.polygons:
            polygon.material_index = polygon.index % 2
    export_glb(out / "before.glb")
    bpy.ops.wm.save_as_mainfile(filepath=str(out / "scene.blend"))
else:
    bake = out / "bake"
    receipt = print_uv(["A", "B"], bake=bake if mode == "bake" else None, density=4)
    result = {"uv": receipt}
    if receipt["status"] == "ok":
        if mode == "bake":
            result["attach"] = attach_print(report=bake)
        result["export"] = export_glb(out / f"{mode}.glb")
    (out / f"{mode}.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
