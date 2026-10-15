"""Synthetic solids/surfaces through the actual Blender exporter; no product edits."""
import json
import sys
from pathlib import Path

import bpy
import bmesh
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from glasses3d.g3d.export_glb import export_glb
from glasses3d.g3d import glb_patch

out = Path(sys.argv[sys.argv.index("--") + 1])
out.mkdir(parents=True, exist_ok=True)
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.unit_settings.system = "METRIC"
scene.unit_settings.scale_length = .001
scene["blender_agent_axes"] = "+Y up; +Z front"
scene["mdl_bridge_underside"] = [0., 0., 0.]


def material(name, transmission=.8, cull=False):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    mat.use_backface_culling = cull
    mat.node_tree.nodes.get("Principled BSDF").inputs["Transmission Weight"].default_value = transmission
    return mat


def cube(name, mat, *, role="lens", inward=False, opened=False):
    bpy.ops.mesh.primitive_cube_add(size=10, location=(len(scene.objects) * 15, 0, 0))
    obj = bpy.context.object
    obj.name = name
    obj["partRole"] = role
    obj.data.materials.append(mat)
    if inward:
        obj.data.flip_normals()
    if opened:
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        bmesh.ops.delete(bm, geom=[next(iter(bm.faces))], context="FACES")
        bm.to_mesh(obj.data)
        bm.free()
    return obj


cube("closed lens", material("closed lens"))
cube("crystal frame", material("crystal frame"), role="frame")
cube("inward lens", material("inward lens"), inward=True)
cube("open lens", material("open lens"), opened=True)
cube("open already single", material("open already single", cull=True), opened=True)
cube("opaque", material("opaque", transmission=0), role="frame")
mixed = material("shared open and closed")
cube("shared closed", mixed)
cube("shared open", mixed, opened=True)
# Disconnected closed components cannot cancel each other's orientation.
outer = cube("mixed shells", material("mixed shells"))
inner = cube("small inward shell", outer.data.materials[0], inward=True)
inner.scale = (.2, .2, .2)
bpy.ops.object.select_all(action="DESELECT")
outer.select_set(True)
inner.select_set(True)
bpy.context.view_layer.objects.active = outer
bpy.ops.object.join()
# A material patch can be open while its containing solid is closed.
patch = cube("multi-material solid", material("solid patch A"))
patch.data.materials.append(material("solid patch B"))
patch.data.polygons[0].material_index = 1
# An unused material slot on an open mesh must not disqualify the actual closed use.
unused = material("unused on open")
cube("actual closed use", unused)
cube("unrelated open", material("open own"), opened=True).data.materials.append(unused)
# Negative object scale is baked with corrected winding by the existing exporter.
cube("mirrored", material("mirrored")).scale.x = -1
loose = cube("loose wire", material("loose wire"))
bm = bmesh.new()
bm.from_mesh(loose.data)
bm.verts.ensure_lookup_table()
bm.edges.new((bm.verts[0], bm.verts.new((20, 20, 20))))
bm.verts.new((30, 30, 30))
bm.to_mesh(loose.data)
bm.free()
bad = cube("inconsistent", material("inconsistent"))
bm = bmesh.new()
bm.from_mesh(bad.data)
bmesh.ops.reverse_faces(bm, faces=[next(iter(bm.faces))])
bm.to_mesh(bad.data)
bm.free()
roles = material("different roles")
cube("closed frame sharing lens material", roles, role="frame")
cube("open lens sharing frame material", roles, opened=True)
warped = cube("nonplanar faces", material("nonplanar faces"))
warped.data.vertices[0].co.x -= 1
warped.scale = (1.2, .8, .5)
pinched = cube("pinched shells", material("pinched shells"))
touching = cube("touching shell", pinched.data.materials[0])
touching.location = pinched.location + Vector((10, 10, 10))
bpy.ops.object.select_all(action="DESELECT")
pinched.select_set(True)
touching.select_set(True)
bpy.context.view_layer.objects.active = pinched
bpy.ops.object.join()
bm = bmesh.new()
bm.from_mesh(pinched.data)
bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=1e-5)
bm.to_mesh(pinched.data)
bm.free()
receipt = export_glb(out / "cases.glb")
doc, _ = glb_patch.read_chunks((out / "cases.glb").read_bytes())
(out / "report.json").write_text(json.dumps({"receipt": receipt, "materials": doc["materials"]}, indent=2))
