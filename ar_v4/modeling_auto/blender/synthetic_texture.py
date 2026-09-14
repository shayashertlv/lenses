"""Fake provider return: native full textured GLB with no tangent requirement."""
import json
from pathlib import Path
import sys
import bpy

source,target = [Path(p).resolve() for p in sys.argv[sys.argv.index('--')+1:]]
root=Path(__file__).resolve().parents[1]
if not source.is_relative_to(root) or not target.is_relative_to(root):
    raise ValueError('Fixture paths outside modeling_auto')
bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
bpy.ops.import_scene.gltf(filepath=str(source),import_pack_images=True)
image=bpy.data.images.new('Synthetic texture',width=32,height=32,alpha=True)
pixels=[]
for y in range(32):
    for x in range(32):
        color=(.07,.15,.23,1) if (x//4+y//4)%2 else (.13,.22,.30,1)
        pixels.extend(color)
image.pixels.foreach_set(pixels); image.pack()
for obj in bpy.context.scene.objects:
    if obj.type != 'MESH': continue
    if not obj.data.uv_layers:
        uv=obj.data.uv_layers.new(name='UVMap')
        for poly in obj.data.polygons:
            for li in poly.loop_indices:
                p=obj.data.vertices[obj.data.loops[li].vertex_index].co
                uv.data[li].uv=(p.x*6+.5,p.z*10+.5)
    for material in obj.data.materials:
        if not material or 'optical' in material.name: continue
        material.use_nodes=True
        shader=material.node_tree.nodes.get('Principled BSDF')
        tex=material.node_tree.nodes.new('ShaderNodeTexImage'); tex.image=image
        material.node_tree.links.new(tex.outputs['Color'],shader.inputs['Base Color'])
target.parent.mkdir(parents=True,exist_ok=True)
bpy.ops.export_scene.gltf(filepath=str(target),export_format='GLB',export_tangents=False,export_extras=True)
