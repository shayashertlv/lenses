"""Exercise hinge-gap reporting with real Blender BVHs; no saved scene or inference."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import bpy
import numpy as np
from mathutils.bvhtree import BVHTree
from glasses3d.g3d import rpc, temple_bpy as T
from glasses3d.tests.blender.temple_uv import pair


def plane(side, inward=True):
    vertices = [(side * 2., y, z) for y, z in ((-1., -1.), (-1., 1.), (1., 1.), (1., -1.))]
    order = [0, 1, 2, 3] if (side == 1) == inward else [3, 2, 1, 0]
    return BVHTree.FromPolygons(vertices, [order], all_triangles=False)


def leaf(name, vertices):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(vertices, [], [list(range(len(vertices)))])
    mesh.update()
    ob = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(ob)
    return ob


data = {"sides": {}}
for side in (1, -1):
    points = np.array([[side * 1.5, 0., 0.], [side * 1.25, .5, .5]])
    missed = points + np.array([0., 3., 0.])
    inward, outward = plane(side), plane(side, inward=False)
    ob = leaf(f"leaf {side}", points.tolist() + [[side * 1.5, -.5, -.5]])
    report = T.hinge_report(inward, outward, side, [2., 0.], 0., bpy.context.scene,
                            np.zeros(3), set(), parts=[(ob, "leaf")])
    # Validate the actual strict wire encoding, not json.dumps' permissive default.
    rpc.encode({"result": report})
    data["sides"][str(side)] = {
        "measured": T._leaf_gap(inward, points, side),
        "miss": T._leaf_gap(inward, missed, side),
        "outward": T._leaf_gap(outward, points, side),
        "report": report,
    }

# A current, unavailable gap also reaches the planner's warning path without arithmetic on None.
objects, spec = pair()
ob = leaf("leaf beyond outline", [[59., 99., -12.], [59., 100., -12.], [59., 100., -13.]])
original_parts = T.hinge_parts
T.hinge_parts = lambda side, *args, **kwargs: [(ob, "leaf")] if side == 1 else []
before = T.scene_digest()
try:
    receipt = T.faceted_temple(spec, side="both", decals="auto", dry_run=True)
finally:
    T.hinge_parts = original_parts
rpc.encode({"result": receipt})
data["planned"] = {"status": receipt["status"], "side": receipt["sides"]["+x"],
                   "unchanged": T.scene_digest() == before}
Path(sys.argv[sys.argv.index("--") + 1]).write_text(json.dumps(data, allow_nan=False), encoding="utf-8")
