"""Real native replay of four-child triangle refinement on a disposable solid."""
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
import bpy
from glasses3d.g3d import model_map_bpy,owner_mesh_bpy,owner_mesh as M,owner_surface as S

fixture=json.loads(Path(sys.argv[sys.argv.index('--')+1]).read_text())
source=fixture['snapshot']['objects'][0]
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.context.scene.unit_settings.system='METRIC';bpy.context.scene.unit_settings.scale_length=.001
bpy.context.scene['mdl_bridge_underside']=[0.,0.,0.]
mesh=bpy.data.meshes.new('Triangle refinement source')
mesh.from_pydata(source['vertices_local'],[],source['polygons']);mesh.update();mesh.shade_smooth()
mesh.normals_split_custom_set(source['corner_normals'])
for layer in source['uv_layers']:
    uv=mesh.uv_layers.new(name=layer['name'])
    for item,value in zip(uv.data,layer['uv']):item.uv=value
obj=bpy.data.objects.new('Curved copied fixture',mesh);bpy.context.scene.collection.objects.link(obj);obj['partRole']='frame'

with tempfile.TemporaryDirectory(prefix='g3d-native-pn-') as temp:
    root=Path(temp)
    def snapshot(label):
        path=root/'work/model_maps'/label/'snapshot.json'
        reply=model_map_bpy.snapshot({'path':str(path)},root);assert reply['status']=='ok',reply
        return json.loads(path.read_text()),path
    before,path=snapshot('before');op=fixture['operation'];op['scope']['object_id']=before['objects'][0]['id']
    planned,report=S.state(before,[op])
    reply=owner_mesh_bpy.apply({'snapshot_path':str(path),'snapshot_sha256':M.digest(before),'operations':[op]},root)
    assert reply['status']=='ok',reply
    after,_=snapshot('after');assert S.verify(before,after,[op])['ok']
    assert after['objects'][0]['vertices_local'][:18]==before['objects'][0]['vertices_local']
    assert after['objects'][0]['polygons']==planned['objects'][0]['polygons']
    assert M.physical(after['objects'][0])['closed']
    print('OWNER_PN_NATIVE_OK')
