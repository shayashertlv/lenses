"""Native mesh-health helper regression on new disposable fixture datablocks."""
import json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
import bpy
from glasses3d.g3d import mesh_health_bpy as H,lib

args=sys.argv[sys.argv.index('--')+1:];fixture=json.loads(Path(args[0]).read_text());out=Path(args[1]);results={}
bpy.ops.wm.read_factory_settings(use_empty=True)
def fresh(name):
    for obj in list(bpy.data.objects):bpy.data.objects.remove(obj,do_unlink=True)
    me=bpy.data.meshes.new(name);me.from_pydata(fixture['vertices_local'],[],fixture['polygons']);me.update()
    obj=bpy.data.objects.new(name,me);bpy.context.scene.collection.objects.link(obj)
    if name!='controlled_decimate':me.normals_split_custom_set(fixture['corner_normals'])
    obj['partRole']='frame';return obj
facade,errors=lib.build();results['facade']={'missing':[n for n in lib.HEALTH_NAMES if not hasattr(facade,n)],'errors':errors}
for kind in ['mesh_cleanup','consistent_normals','controlled_decimate']:
    obj=fresh(kind)
    if kind=='mesh_cleanup':
        # Orphan exact duplicates are legitimate duplicate-vertex cleanup too.
        vertices=[list(v.co) for v in obj.data.vertices]+[list(obj.data.vertices[0].co),list(obj.data.vertices[1].co)]
        polygons=[list(p.vertices) for p in obj.data.polygons];obj.data.clear_geometry();obj.data.from_pydata(vertices,[],polygons);obj.data.update()
    if kind=='consistent_normals':obj.data.normals_split_custom_set([[-x for x in n] for n in fixture['corner_normals']])
    before=H._capture();dry=getattr(H,kind)(obj,dry_run=True)
    assert H._capture()==before,'dry-run mutated fixture'
    result=getattr(H,kind)(obj)
    results[kind]={'dry':dry,'actual':result,'vertices':len(obj.data.vertices),'polygons':len(obj.data.polygons)}
obj=fresh('symmetry');obj.data.vertices[0].co.x=-2.1;obj.data.update()
results['symmetrize']=H.symmetrize_mesh(obj,[[1,0],[2,3],[5,4],[6,7]])
before=H._capture();results['refusal']=H.symmetrize_mesh(obj,[[1,0],[1,3]])
assert H._capture()==before,'refusal mutated fixture'
out.write_text(json.dumps(results,indent=2));print('MESH_HEALTH_NATIVE_COMPLETE')
