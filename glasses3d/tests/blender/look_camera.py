"""Independent Blender silhouette of decoded GLB geometry, without importing/rotating glTF."""
import json
from pathlib import Path
import sys

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from glasses3d.g3d import render

root = Path(sys.argv[sys.argv.index("--") + 1])
for ob in list(bpy.data.objects):
    bpy.data.objects.remove(ob, do_unlink=True)
geometry = np.load(root / "geometry.npz")
mesh = bpy.data.meshes.new("unchanged decoded GLB")
mesh.from_pydata(geometry["vertices"].tolist(), [], geometry["triangles"].tolist())
mesh.update()
obj = bpy.data.objects.new("all geometry", mesh)
bpy.context.scene.collection.objects.link(obj)
obj["partRole"] = "frame"
marker = bpy.data.objects.new("mdl_bridge_underside", None)
bpy.context.scene.collection.objects.link(marker)
bpy.context.scene.unit_settings.system = "METRIC"
bpy.context.scene.unit_settings.scale_length = .001
params = json.loads((root / "render-input.json").read_text())
camera_data = bpy.data.cameras.new("independent projection probe")
camera_object = bpy.data.objects.new("independent projection probe", camera_data)
bpy.context.scene.collection.objects.link(camera_object)
projections = {}
for view in params["views"]:
    render.apply_camera(camera_object, view["camera"], bpy.context.scene)
    bpy.context.view_layer.update()
    width, height = view["camera"]["resolution"]
    projection = camera_object.calc_matrix_camera(bpy.context.evaluated_depsgraph_get(), x=width, y=height)
    matrix = np.asarray(projection @ camera_object.matrix_world.inverted(), dtype=float)
    clip = np.column_stack([geometry["vertices"], np.ones(len(geometry["vertices"]))]) @ matrix.T
    ndc = clip[:, :2] / clip[:, 3, None]
    projections[view["name"]] = np.column_stack([(ndc[:, 0] + 1) * width / 2 - .5,
                                                (1 - ndc[:, 1]) * height / 2 - .5])
np.savez(root / "blender-projection.npz", **projections)
result = render.render(params, root, selftest=True)
(root / "render-result.json").write_text(json.dumps(result, indent=1))
assert result["unchanged"]
