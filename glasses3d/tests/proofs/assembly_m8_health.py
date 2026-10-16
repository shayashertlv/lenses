"""Offline health demonstrations on a copied product and an explicitly altered leaf.

Blender --python this.py -- OUTPUT [--snapshot-existing]. For a new proof open a
copied blend first and choose a fresh output directory. The small frame leaf is
replaced by a box occupying its bounds, then deliberate faults are injected.
These are demonstration proposals, never owner confirmation or deliveries.
"""
from pathlib import Path
import hashlib
import json
import sys
import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from glasses3d.g3d import mesh_health_bpy as H, export_glb as E, model_map_bpy as MAP
from glasses3d.g3d_host.spec import validate_spec

KINDS = ('mesh_cleanup', 'consistent_normals', 'symmetrize', 'controlled_decimate')
LEGENDS = {
    'mesh_cleanup': 'copied frame-leaf box: remove the deliberately duplicated vertices and zero-area triangles',
    'consistent_normals': 'copied frame-leaf box: turn its deliberately backward shading normals outward',
    'symmetrize': 'two copied frame-leaf boxes: mirror the displaced negative-X leaf about X=0 using the positive-X leaf',
    'controlled_decimate': 'copied frame-leaf box: remove planar interior fan vertices while keeping its surface and edges',
}


def annotate(folder):
    path = folder/'work/model_maps/baseline/snapshot.json'
    reply = MAP.snapshot({'path':str(path)}, folder)
    assert reply['status'] == 'ok', reply
    snap = json.loads(path.read_text())
    obj = next(o for o in snap['objects'] if o['name'] == 'frame leaf1')
    scope = {'object_id':obj['id'], 'vertex_weights':[{'vertex_id':i,'weight':1.} for i in range(len(obj['vertices_local']))],
             'fixed_vertex_ids':[], 'affected_polygon_ids':list(range(len(obj['polygons'])))}
    (folder/'selection.json').write_text(json.dumps({'label':'H1','legend':LEGENDS[folder.name],
                                                   'object':obj,'scope':scope}, indent=2))


def box(obj):
    v = np.array([obj.matrix_world@p.co for p in obj.data.vertices]);lo,hi = v.min(0),v.max(0)
    xyz = [[lo[0],lo[1],lo[2]],[hi[0],lo[1],lo[2]],[hi[0],hi[1],lo[2]],[lo[0],hi[1],lo[2]],
           [lo[0],lo[1],hi[2]],[hi[0],lo[1],hi[2]],[hi[0],hi[1],hi[2]],[lo[0],hi[1],hi[2]]]
    xyz += [((lo+hi)/2*[1,1,0]+[0,0,hi[2]]).tolist(),((lo+hi)/2*[1,1,0]+[0,0,lo[2]]).tolist()]
    faces = [[0,9,1],[1,9,2],[2,9,3],[3,9,0],[0,1,5,4],[1,2,6,5],[2,3,7,6],[3,0,4,7],
             [4,5,8],[5,6,8],[6,7,8],[7,4,8]]
    return xyz,faces


def install(obj,vertices,faces,kind):
    mat = obj.data.materials[0];me = bpy.data.meshes.new('Scratch leaf health fixture '+kind)
    me.from_pydata(vertices,[],faces);me.update();me.materials.append(mat);obj.data=me;obj.matrix_world.identity()
    normals=[]
    for p in me.polygons:
        n=np.array(p.normal)
        if np.linalg.norm(n)<1e-12:n=np.array([0,0,1])
        normals.extend([n.tolist()]*len(p.vertices))
    if kind!='controlled_decimate':me.normals_split_custom_set(normals)
    if kind=='consistent_normals':
        normals=[list(n.vector) for n in me.corner_normals]
        for p in list(me.polygons)[4:6]:
            for i in p.loop_indices:normals[i]=(-np.array(normals[i])).tolist()
        me.normals_split_custom_set(normals)


def run(output, snapshots_only=False):
    output=Path(output).resolve()
    if any(n in output.parts for n in ('deliveries','checkpoints','deliverable')):raise ValueError('scratch output required')
    if snapshots_only:
        for kind in KINDS:
            folder=output/kind;source=folder/'before.blend';sha=hashlib.sha256(source.read_bytes()).hexdigest()
            bpy.ops.wm.open_mainfile(filepath=str(source));annotate(folder)
            assert hashlib.sha256(source.read_bytes()).hexdigest()==sha
        return
    source=Path(bpy.data.filepath).resolve()
    if not source.is_file() or any(n in source.parts for n in ('deliveries','checkpoints','deliverable')):
        raise ValueError('open a copied blend file first')
    if output.exists():raise ValueError('fresh scratch output required')
    output.mkdir(parents=True);sha=hashlib.sha256(source.read_bytes()).hexdigest();report={};strict={}
    for kind in KINDS:
        folder=output/kind;folder.mkdir();bpy.ops.wm.open_mainfile(filepath=str(source))
        obj=bpy.data.objects['frame leaf1'];v,p=box(obj);pairs=[]
        if kind=='mesh_cleanup':
            v += [list(v[0]),list(v[1]),list(v[2])];p[4][0]=10;p += [[0,1,10],[4,0,10]]
        elif kind=='symmetrize':
            other=bpy.data.objects['frame leaf-1'];left,faces=box(other);base=len(v);v+=left;p += [[i+base for i in f] for f in faces]
            for i in range(base):
                q=np.array(v[i])*[-1,1,1];j=min(range(base,len(v)),key=lambda j:np.linalg.norm(np.array(v[j])-q));pairs.append([i,j])
            for row in v[base:]:row[0]-=.2
            bpy.data.objects.remove(other,do_unlink=True)
        install(obj,v,p,kind);annotate(folder)
        bpy.ops.wm.save_as_mainfile(filepath=str(folder/'before.blend'));before=E.export_glb(folder/'before.glb')
        report[kind]={'scripted_by':'Codex','cost_usd':0,'api_calls':0,'owner_confirmation':False,
                      'source_copy':str(source),'source_copy_sha256':sha,'fixture':LEGENDS[kind],
                      'before_export':before['status'],'variants':{}}
        for name,strength in [('half',.5),('full',1.)]:
            bpy.ops.wm.open_mainfile(filepath=str(folder/'before.blend'));obj=bpy.data.objects['frame leaf1']
            if kind=='symmetrize':receipt=H.symmetrize_mesh(obj,pairs,strength=strength)
            elif kind=='controlled_decimate':receipt=H.controlled_decimate(obj,max_removed_vertices=2,strength=strength)
            else:receipt=getattr(H,kind)(obj,strength=strength)
            assert receipt['status']=='ok',receipt
            report[kind]['variants'][name]=receipt
            bpy.ops.wm.save_as_mainfile(filepath=str(folder/f'{name}.blend'))
            receipt['export']=E.export_glb(folder/f'{name}.glb')['status']
        for name in ('before','half','full'):
            check=validate_spec(folder/f'{name}.glb');assert check['ok'],check;strict[f'{kind}/{name}']=check
    assert hashlib.sha256(source.read_bytes()).hexdigest()==sha
    (output/'receipts.json').write_text(json.dumps(report,indent=2));(output/'strict-checks.json').write_text(json.dumps(strict,indent=2))
    print('M8_HEALTH_COPY_PROOF',output)


if __name__=='__main__':
    args=sys.argv[sys.argv.index('--')+1:]
    run(args[0], '--snapshot-existing' in args)
