"""Generate a standard Blender glTF export of the current evaluated scene.

Only temporary evaluated meshes/material copies are exported. No lens surfaces
are removed and no canonical optical descriptors or construction records are
used. Blender's native glTF material support defines shader portability.
"""
from __future__ import annotations

import math
from pathlib import Path
from textwrap import dedent

RECEIPT_PREFIX = "NATIVE_EXPORT_RECEIPT:"


def export_code(path: Path, meters_per_unit: float = 0.001,
                origin: tuple[float, float, float] | None = None) -> str:
    """Return MCP-safe Blender Python; caller creates the output directory.

    Coordinates already use +Y up and +Z forward. ``origin=None`` uses the
    scene's explicit ``mdl_bridge_underside`` metadata; missing origin is an error.
    Render-hidden objects/collection paths are excluded; viewport isolation is ignored.
    Unrealized instances are rejected; realize them in Blender before preview.
    Object mode is required. Once export starts, a JSON receipt is printed even on failure.
    """
    destination = Path(path).resolve()
    if destination.suffix.lower() != ".glb":
        raise ValueError("Native export requires a .glb path")
    if isinstance(meters_per_unit, bool) or not math.isfinite(meters_per_unit) or meters_per_unit <= 0:
        raise ValueError("meters_per_unit must be finite and positive")
    if origin is not None:
        if len(origin) != 3 or any(isinstance(v, bool) or not math.isfinite(v) for v in origin):
            raise ValueError("origin must contain three finite coordinates")
        origin = tuple(float(v) for v in origin)
    return dedent(_SCRIPT).replace("_METERS_PER_UNIT_", repr(float(meters_per_unit))).replace(
        "_ORIGIN_", repr(origin)).replace("_EXPORT_PATH_", repr(str(destination)))


_SCRIPT = '''\
import bpy
import json
import math
from mathutils import Matrix, Vector

def native_export():
    scene = bpy.context.scene
    view_layer = bpy.context.view_layer
    if bpy.context.mode != 'OBJECT':
        raise ValueError('Leave Edit/Sculpt mode before native export; no mode was changed')
    origin = _ORIGIN_
    origin_source = 'explicit'
    if origin is None:
        if 'mdl_bridge_underside' not in scene:
            raise ValueError('Set the bridge origin explicitly or declare scene mdl_bridge_underside before export')
        origin_source = 'scene_metadata'
        origin = list(scene['mdl_bridge_underside'])
    if len(origin) != 3 or not all(math.isfinite(float(v)) for v in origin):
        raise ValueError('Bridge origin must contain three finite coordinates')
    scale = _METERS_PER_UNIT_
    transform = Matrix.Scale(scale, 4) @ Matrix.Translation(-Vector(origin))

    def socket_value(socket):
        try:
            value = socket.default_value
        except AttributeError:
            return None
        if isinstance(value, (str, bool, int, float)):
            return value
        try:
            return tuple(value)
        except TypeError:
            return repr(value)

    def snapshot():
        objects = []
        for obj in scene.objects:
            geometry = None
            if obj.type == 'MESH':
                geometry = (tuple(tuple(v.co) for v in obj.data.vertices),
                            tuple(tuple(p.vertices) for p in obj.data.polygons),
                            tuple(p.material_index for p in obj.data.polygons))
            objects.append((obj.name, obj, obj.data,
                            tuple(tuple(row) for row in obj.matrix_world), obj.select_get(),
                            obj.hide_get(), obj.hide_viewport, obj.hide_render, geometry,
                            tuple(slot.material for slot in obj.material_slots)))
        materials = []
        for mat in bpy.data.materials:
            nodes = tuple((node.name, node.bl_idname, tuple(socket_value(s) for s in node.inputs))
                          for node in mat.node_tree.nodes) if mat.node_tree else ()
            links = tuple((link.from_node.name, link.from_socket.name, link.to_node.name, link.to_socket.name)
                          for link in mat.node_tree.links) if mat.node_tree else ()
            materials.append((mat.name, mat, tuple(mat.diffuse_color), nodes, links))
        return (bpy.data.filepath, bpy.context.mode, scene,
                view_layer.objects.active,
                tuple(objects), tuple(materials), len(bpy.data.meshes), len(bpy.data.objects), len(bpy.data.scenes))

    def role_of(obj):
        role = obj.get('partRole')
        if role in ('frame', 'temple', 'lens'):
            return role
        part = str(obj.get('part', ''))
        if part == 'frame':
            return 'frame'
        if part == 'temple' or part.startswith('temple_'):
            return 'temple'
        if part == 'lens' or part.startswith('lens_'):
            return 'lens'
        return None

    before = snapshot()
    receipt = {'path': _EXPORT_PATH_, 'meters_per_unit': scale, 'origin': list(origin),
               'origin_source': origin_source, 'export_yup': False, 'objects': [], 'excluded': [],
               'scene_unchanged': False, 'status': 'failed'}
    temporary_scene = None
    meshes, duplicates, materials = [], [], {}
    try:
        depsgraph = bpy.context.evaluated_depsgraph_get()
        # An object linked through any render-enabled collection path can render. Do not use hide_get()/visible_get():
        # viewport isolation must not silently remove the rest of the product from a preview export.
        render_objects = set()
        def collect_render_objects(collection, hidden=False):
            hidden = hidden or collection.hide_render
            if not hidden:
                render_objects.update(collection.objects)
            for child in collection.children:
                collect_render_objects(child, hidden)
        collect_render_objects(scene.collection)
        def render_enabled(obj):
            return not obj.hide_render and obj in render_objects
        instances = [obj.name for obj in scene.objects
                     if render_enabled(obj) and obj.instance_type == 'COLLECTION']
        for instance in depsgraph.object_instances:
            if not instance.is_instance:
                continue
            owner = instance.parent.original if instance.parent else instance.object.original
            if not render_enabled(owner):
                continue
            # Blender 5.2 exposes a beveled curve's OWN evaluated mesh as an instance. new_from_object below retains
            # that surface, so it is not an unrealized duplicate. Require source identity (not matching names) and
            # exclude Geometry Nodes owners from this exception; their instances still require explicit realization.
            converted_surface = (instance.parent is not None and instance.object.type == 'MESH'
                                 and owner.type in ('CURVE', 'SURFACE', 'FONT', 'META')
                                 and instance.object.original == owner
                                 and not any(mod.type == 'NODES' for mod in owner.modifiers))
            if not converted_surface and owner.name not in instances:
                instances.append(owner.name)
        if instances:
            raise ValueError('Unrealized instances are not exported: ' + ', '.join(instances)
                             + '. Realize collection/Geometry Nodes instances in Blender before preview_ar.')
        surface_objects = [obj for obj in scene.objects if obj.type in ('MESH', 'CURVE', 'SURFACE', 'FONT', 'META')]
        sources = [obj for obj in surface_objects if render_enabled(obj)]
        receipt['excluded'] = [{'source': obj.name,
                                'reason': 'hide_render' if obj.hide_render else 'collection.hide_render'}
                               for obj in surface_objects if not render_enabled(obj)]
        if not sources:
            raise ValueError('No render-visible surface objects to export')
        temporary_scene = bpy.data.scenes.new('Native glTF export temporary scene')
        temporary_scene.unit_settings.system = 'NONE'
        temporary_scene.unit_settings.scale_length = 1.0
        for obj in sources:
            evaluated = obj.evaluated_get(depsgraph)
            mesh = bpy.data.meshes.new_from_object(evaluated, preserve_all_data_layers=True, depsgraph=depsgraph)
            meshes.append(mesh)
            for key in list(mesh.keys()):
                del mesh[key]
            world_to_asset = transform @ obj.matrix_world
            mesh.transform(world_to_asset)
            if world_to_asset.to_3x3().determinant() < 0:
                mesh.flip_normals()
            mesh.update()
            mesh.calc_loop_triangles()
            part, role = str(obj.get('part', '')), role_of(obj)
            duplicate = bpy.data.objects.new((part + '__' if part else '') + obj.name, mesh)
            duplicates.append(duplicate)
            temporary_scene.collection.objects.link(duplicate)
            duplicate['sourceObject'] = obj.name
            if part:
                duplicate['part'] = part
            if role:
                duplicate['partRole'] = role
                mesh['partRole'] = role
            for index, slot in enumerate(evaluated.material_slots):
                material = slot.material
                if material is None:
                    continue
                key = material.name
                if key not in materials:
                    copy = material.copy()
                    materials[key] = copy
                    for property_name in list(copy.keys()):
                        del copy[property_name]
                mesh.materials[index] = materials[key]
            receipt['objects'].append({'source': obj.name, 'name': duplicate.name, 'part': part,
                                       'partRole': role, 'vertices': len(mesh.vertices),
                                       'triangles': len(mesh.loop_triangles),
                                       'materials': [slot.material.name if slot.material else None for slot in evaluated.material_slots]})
        with bpy.context.temp_override(scene=temporary_scene, view_layer=temporary_scene.view_layers[0]):
            result = bpy.ops.export_scene.gltf(
                filepath=_EXPORT_PATH_, export_format='GLB', export_yup=False,
                use_active_scene=True, use_selection=False, export_apply=False,
                export_materials='EXPORT', export_extras=True, export_animations=False,
                use_mesh_edges=True, use_mesh_vertices=True, will_save_settings=False,
                export_cameras=False, export_lights=False, export_draco_mesh_compression_enable=False)
            if result != {'FINISHED'}:
                raise RuntimeError('Native glTF exporter did not finish: ' + str(result))
        receipt['status'] = 'exported'
    finally:
        for duplicate in duplicates:
            bpy.data.objects.remove(duplicate, do_unlink=True)
        for mesh in meshes:
            bpy.data.meshes.remove(mesh)
        for material in materials.values():
            bpy.data.materials.remove(material)
        if temporary_scene is not None:
            bpy.data.scenes.remove(temporary_scene)
        receipt['scene_unchanged'] = snapshot() == before
        receipt['object_count'] = len(receipt['objects'])
        receipt['triangle_count'] = sum(obj['triangles'] for obj in receipt['objects'])
        print('NATIVE_EXPORT_RECEIPT:' + json.dumps(receipt))
        if not receipt['scene_unchanged']:
            raise RuntimeError('Live scene invariance check failed after native export')
    return receipt

native_export()
'''
