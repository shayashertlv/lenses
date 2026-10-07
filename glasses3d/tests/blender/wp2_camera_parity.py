"""Camera parity inside Blender 5.2 (WP2 stage B, tests/test_wp2_camera_parity.py).

    blender -b --factory-startup --python wp2_camera_parity.py -- <job.json> <receipt.json>

job.json: {"root": dir, "points_mm": [[x, y, z], ...], "blobs_mm": [[x, y, z], ...], "radius_mm": r,
           "cameras": [{"name": n, "spec": blender_camera spec, "render": bool}, ...]}

1. analytic: each spec applied with the worker's own ``g3d.render.apply_camera``; Blender's ``world_to_camera_view`` of
   every point -> pixel-centre coordinates (u = x W - 0.5, v = (1 - y) H - 0.5);
2. rendered: small UV spheres at ``blobs_mm`` in the (unsaved) factory scene, rendered through the worker's own
   ``g3d.render.render`` (temporary scene, EEVEE, clay pass) for every camera with ``render``; the host measures the
   alpha centroids.
Writes {"blender", "analytic": {name: [[u, v], ...]}, "render": render.render's receipt} to receipt.json.
"""
import json
import sys
from pathlib import Path

import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from glasses3d.g3d import render  # noqa: E402

args = sys.argv[sys.argv.index("--") + 1:]
job = json.loads(Path(args[0]).read_text(encoding="utf-8"))
scene = bpy.context.scene
for obj in list(scene.objects):
    bpy.data.objects.remove(obj, do_unlink=True)
scene.unit_settings.system, scene.unit_settings.scale_length = "METRIC", 0.001
cd = bpy.data.cameras.new("parity")
cam = bpy.data.objects.new("parity", cd)
scene.collection.objects.link(cam)
analytic = {}
for c in job["cameras"]:
    render.apply_camera(cam, c["spec"], scene)
    bpy.context.view_layer.update()
    W, H = c["spec"]["resolution"]
    uv = []
    for p in job["points_mm"]:
        q = world_to_camera_view(scene, cam, Vector(p))
        uv.append([q.x * W - 0.5, (1.0 - q.y) * H - 0.5])
    analytic[c["name"]] = uv
for i, p in enumerate(job["blobs_mm"]):
    bpy.ops.mesh.primitive_uv_sphere_add(radius=float(job["radius_mm"]), location=tuple(p), segments=48,
                                         ring_count=24)
    bpy.context.object.name = f"blob_{i}"
views = [{"name": c["name"], "camera": c["spec"], "passes": ["clay"]} for c in job["cameras"] if c.get("render")]
rec = render.render({"out_dir": str(Path(job["root"]) / "work" / "parity"), "views": views, "engine": "eevee",
                     "samples": 16, "light_frame": {"center": [0, 0, 0], "radius": 100}}, job["root"]) if views else None
Path(args[1]).write_text(json.dumps({"blender": bpy.app.version_string, "analytic": analytic, "render": rec}),
                         encoding="utf-8")
