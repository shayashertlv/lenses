"""Independent main-helper review: rollback after real writes, never a saved model."""
import json
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
import bpy
from glasses3d.g3d import mesh_health_bpy as H

args=sys.argv[sys.argv.index('--')+1:]
fixture=json.loads(Path(args[0]).read_text());out=Path(args[1])
bpy.ops.wm.read_factory_settings(use_empty=True)
for row in fixture['objects']:
    mesh=bpy.data.meshes.new(row['mesh_name'])
    mesh.from_pydata(row['vertices_local'],[],row['polygons']);mesh.update()
    obj=bpy.data.objects.new(row['name'],mesh);bpy.context.scene.collection.objects.link(obj)
    obj['partRole']=row['role'];mesh.normals_split_custom_set(row['corner_normals'])
surface=bpy.data.objects[fixture['objects'][0]['name']]
before=H._capture();original={obj.name:obj.data for obj in bpy.data.objects}
mesh_count=len(bpy.data.meshes);real_apply=H.APPLY._surface_apply;observed={}
def apply_then_fail(source,operations):
    real_apply(source,operations)
    after=H._capture()
    observed['written']=all(a['vertices_local']!=b['vertices_local'] for a,b in zip(after['objects'],source['objects']))
    observed['copies']=sum(bpy.data.objects[name].data!=mesh for name,mesh in original.items())
    raise RuntimeError('independent failure after applying both copied meshes')
H.APPLY._surface_apply=apply_then_fail
try:
    result=H.symmetrize_mesh(surface,[[1,0],[2,3],[5,4],[6,7]])
finally:
    H.APPLY._surface_apply=real_apply
assert observed['written'] and observed['copies']==2,observed
assert result['status']=='refused' and 'independent failure' in result['message'],result
assert H._capture()==before,'post-write rollback must restore exact mesh state'
assert all(bpy.data.objects[name].data==mesh for name,mesh in original.items())
assert len(bpy.data.meshes)==mesh_count,'rollback must remove disposable meshes'
out.write_text(json.dumps({'ok':True,'copies_restored':observed['copies']}))
print('INDEPENDENT_MESH_HEALTH_ROLLBACK_OK')
