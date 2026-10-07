"""Build a seed scene from triangle parts (WP2 stage B, tests/test_wp2_fit_cameras.py).

    blender -b --factory-startup --python wp2_synthetic_scene.py -- <parts.json> <out.blend>

parts.json: {"origin_mm": [x, y, z], "parts": [{"name": n, "role": "frame|lens|temple", "V": [[x, y, z] mm], "F":
[[a, b, c]]}]}. The scene follows the exporter's convention (numeric millimetres, +Y up / +Z front, the bridge
underside at ``origin_mm``); each part is one mesh object with its vertices shifted by the origin, so the export
gives back the given millimetres. Written to a NEW file with save_as_mainfile(copy=True).
"""
import json
import sys
from pathlib import Path

import bpy

args = sys.argv[sys.argv.index("--") + 1:]
job = json.loads(Path(args[0]).read_text(encoding="utf-8"))
out = Path(args[1])
if out.exists():
    raise SystemExit(f"{out} exists; refusing to overwrite")
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.unit_settings.system, scene.unit_settings.scale_length = "METRIC", 0.001
scene["blender_agent_axes"] = "+Y up; +Z front"
ox, oy, oz = (float(v) for v in job["origin_mm"])
scene["mdl_bridge_underside"] = [ox, oy, oz]
for p in job["parts"]:
    me = bpy.data.meshes.new(p["name"])
    me.from_pydata([(x + ox, y + oy, z + oz) for x, y, z in p["V"]], [], [tuple(f) for f in p["F"]])
    me.validate()
    ob = bpy.data.objects.new(p["name"], me)
    ob["partRole"] = p["role"]
    scene.collection.objects.link(ob)
bpy.ops.wm.save_as_mainfile(filepath=str(out), copy=True)
print("G3D_SYNTHETIC_SCENE", json.dumps({"out": str(out), "objects": len(job["parts"])}))
