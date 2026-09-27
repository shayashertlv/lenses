"""QA-only complete source-normal bake; never exports altered optical geometry."""
import argparse
import json
from pathlib import Path
import sys
import time

import bpy
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from reconstruction.surface_transfer import _chunks,_pack

parser=argparse.ArgumentParser()
parser.add_argument('--source',required=True);parser.add_argument('--lod',required=True)
parser.add_argument('--output',required=True);parser.add_argument('--resolution',type=int,default=2048)
args=parser.parse_args(sys.argv[sys.argv.index('--')+1:]);out=Path(args.output);start=time.monotonic()
bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
def load(path,prefix):
    before=set(bpy.data.objects)
    document,binary=_chunks(Path(path).read_bytes())
    document['scenes']=[document['scenes'][document.get('scene',0)]];document['scene']=0
    active=set()
    def visit(index):
        active.add(index)
        for child in document['nodes'][index].get('children',[]):visit(child)
    for root in document['scenes'][0]['nodes']:visit(root)
    remap={old:new for new,old in enumerate(sorted(active))}
    document['nodes']=[document['nodes'][i] for i in sorted(active)]
    for item in document['nodes']:
        if 'children'in item:item['children']=[remap[c] for c in item['children']]
    document['scenes'][0]['nodes']=[remap[i] for i in document['scenes'][0]['nodes']]
    staged=out/(prefix+'active-scene.glb');staged.write_bytes(_pack(document,binary))
    bpy.ops.import_scene.gltf(filepath=str(staged.resolve()))
    created=set(bpy.data.objects)-before
    frames=[]
    for obj in created:
        if obj.type=='MESH' and not obj.get('lensSurfaceProfile'):
            frames.append(obj);obj.name=prefix+obj.name
            for polygon in obj.data.polygons:polygon.use_smooth=False
        elif obj.type=='MESH':bpy.data.objects.remove(obj,do_unlink=True)
    return frames
high=load(args.source,'SOURCE_');low=load(args.lod,'LOD_')
if not high or len(high)!=len(low):raise ValueError('Expected matching nonempty source and LOD frame inventories')
report={'method':'selected_source_shading_normal_bake_qa_v1','source_triangles':sum(len(o.data.polygons) for o in high),
        'lod_triangles':sum(len(o.data.polygons) for o in low),'resolution':args.resolution,
        'source_material_normal_nodes':{m.name:[n.type for n in m.node_tree.nodes if n.type in ('NORMAL_MAP','TEX_IMAGE')]
            for o in high for m in o.data.materials if m and m.use_nodes}}
print(json.dumps(report),flush=True)
bpy.ops.object.select_all(action='DESELECT')
for obj in low:obj.select_set(True)
bpy.context.view_layer.objects.active=low[0];bpy.ops.object.join();target=bpy.context.view_layer.objects.active
image=bpy.data.images.new('Source complete frame shading normals',width=args.resolution,height=args.resolution,alpha=False,float_buffer=False)
image.colorspace_settings.name='Non-Color';image.generated_color=(.5,.5,1,1)
mat=bpy.data.materials.new('LOD bake target');mat.use_nodes=True
node=mat.node_tree.nodes.new('ShaderNodeTexImage');node.image=image;node.select=True;mat.node_tree.nodes.active=node
target.data.materials.clear();target.data.materials.append(mat)
for polygon in target.data.polygons:polygon.material_index=0
scene=bpy.context.scene;scene.render.engine='CYCLES';scene.cycles.device='CPU';scene.cycles.samples=1
scene.render.threads_mode='FIXED';scene.render.threads=4
scene.render.bake.use_selected_to_active=True;scene.render.bake.use_clear=True
scene.render.bake.margin=2;scene.render.bake.normal_space='TANGENT'
scene.render.bake.cage_extrusion=.0002;scene.render.bake.max_ray_distance=.001
bpy.ops.object.select_all(action='DESELECT')
for obj in high:obj.select_set(True)
target.select_set(True);bpy.context.view_layer.objects.active=target
bpy.ops.object.bake(type='NORMAL')
image.filepath_raw=str((out/'normal-baked.png').resolve());image.file_format='PNG';image.save()
report.update(seconds=time.monotonic()-start,output=image.filepath_raw)
(out/'bake-report.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report),flush=True)
