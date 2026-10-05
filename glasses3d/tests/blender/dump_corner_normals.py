"""Dump Blender's own evaluated corner normals and positions in ASSET space (what the GLB must contain).

    blender -b <scene>.blend --python dump_corner_normals.py -- <out.json> <object name> [...]

Asset space = Scale(metres per unit) @ Translation(-mdl_bridge_underside) @ matrix_world; normals use the
inverse-transpose of that matrix (how the renderer shades a transformed object). Output: {name: [[px, py, pz, nx, ny,
nz], ...]} with one row per face corner of the evaluated mesh (modifiers applied).
"""
import json
import sys

import bpy
from mathutils import Matrix, Vector

args = sys.argv[sys.argv.index("--") + 1:]
out_path, names = args[0], args[1:]
scene = bpy.context.scene
to_root = Matrix.Scale(scene.unit_settings.scale_length, 4) @ Matrix.Translation(-Vector(scene["mdl_bridge_underside"]))
dg = bpy.context.evaluated_depsgraph_get()
result = {}
for name in names:
    ob = bpy.data.objects[name]
    ev = ob.evaluated_get(dg)
    me = ev.to_mesh()
    m = to_root @ ob.matrix_world
    nmat = m.to_3x3().inverted().transposed()
    rows = []
    for loop in me.loops:
        p = m @ me.vertices[loop.vertex_index].co
        n = (nmat @ me.corner_normals[loop.index].vector).normalized()
        rows.append([p.x, p.y, p.z, n.x, n.y, n.z])
    ev.to_mesh_clear()
    result[name] = rows
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(result, f)
print("NORMALS_DUMPED", len(result))
