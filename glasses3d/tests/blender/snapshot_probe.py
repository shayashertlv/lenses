"""Which scene changes does export_glb.snapshot() see? Each change is made on a freshly opened copy of the fixture.

    blender -b <fixture-ok>.blend --python snapshot_probe.py -- <out.json>

Writes {change: detected}. 'nothing' must be False, every other change True.
"""
import json
import math
import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from glasses3d.g3d.export_glb import snapshot  # noqa: E402

out = sys.argv[sys.argv.index("--") + 1:][0]
SRC = bpy.data.filepath


def unlink_texture():
    mat = bpy.data.materials["Havana | photo print"]
    lk = next(l for l in mat.node_tree.links if l.from_node.bl_idname == "ShaderNodeTexImage")
    mat.node_tree.links.remove(lk)


def custom_normals():
    me = bpy.data.objects["Bridge"].data
    me.normals_split_custom_set([(0.0, 1.0, 0.0)] * len(me.loops))


def rewrite_uvs():
    uv = bpy.data.objects["Brow_L"].data.uv_layers[0]
    uv.data[0].uv = (0.123, 0.456)


def edit_pixel():
    im = bpy.data.images["havana_print"]
    px = list(im.pixels[:4])
    im.pixels[0] = 1.0 - px[0]


def smooth_faces():
    for p in bpy.data.objects["Temple_L"].data.polygons:
        p.use_smooth = True


CHANGES = {
    "nothing": lambda: None,
    "texture link removed": unlink_texture,
    "object partRole": lambda: bpy.data.objects["Lens_L"].__setitem__("partRole", "frame"),
    "mdl_bridge_underside": lambda: bpy.context.scene.__setitem__("mdl_bridge_underside", [0.0, 5.0, 0.0]),
    "modifier added": lambda: bpy.data.objects["Hinge_cap"].modifiers.new("Bevel", "BEVEL"),
    "modifier setting": lambda: setattr(bpy.data.objects["Hinge_cap"].modifiers.new("Bevel", "BEVEL"), "width", 0.5),
    "custom normals": custom_normals,
    "uv rewritten": rewrite_uvs,
    "image pixel": edit_pixel,
    "faces smooth": smooth_faces,
    "scale_length": lambda: setattr(bpy.context.scene.unit_settings, "scale_length", 0.01),
    "length_unit": lambda: setattr(bpy.context.scene.unit_settings, "length_unit", "METERS"),
    "socket value": lambda: setattr(bpy.data.materials["Lens | clear"].node_tree.nodes["Principled BSDF"]
                                    .inputs["IOR"], "default_value", 1.6),
    "view layer exclude": lambda: (bpy.data.collections.new("C"), bpy.context.scene.collection.children.link(
        bpy.data.collections["C"]), setattr(bpy.context.view_layer.layer_collection.children["C"], "exclude", True)),
    "vertex nan": lambda: setattr(bpy.data.objects["Bridge"].data.vertices[0], "co", (math.nan, 0.0, 0.0)),
}

result = {}
for name, change in CHANGES.items():
    bpy.ops.wm.open_mainfile(filepath=SRC)
    bpy.context.view_layer.update()
    before = snapshot(bpy.context.scene)
    change()
    bpy.context.view_layer.update()
    result[name] = snapshot(bpy.context.scene) != before
# NaN-safe: a NaN coordinate that does not change is not a change
bpy.ops.wm.open_mainfile(filepath=SRC)
bpy.data.objects["Bridge"].data.vertices[0].co = (math.nan, 0.0, 0.0)
result["nan stays nan (must be False)"] = snapshot(bpy.context.scene) != snapshot(bpy.context.scene)
Path(out).write_text(json.dumps(result), encoding="utf-8")
print("SNAPSHOT_PROBE", result)
