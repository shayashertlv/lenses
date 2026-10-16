"""$0 round/sharpen proof inside a copied c0015 product, never a delivered file.

The frame leaf is explicitly replaced by a coarse closed box inside its original
bounds, because this exact-radius tool supports straight edges with simple caps.
All other product objects remain those of the caller's copied source. These are
Codex-scripted proposals, not Astra output, owner approval or a new delivery.
"""
from pathlib import Path
import hashlib
import json
import sys
import bpy
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from glasses3d.tests.proofs.assembly_m8_edits import snapshot,selection
from glasses3d.g3d import owner_mesh as M,owner_mesh_bpy,owner_surface as S,owner_markings
from glasses3d.g3d.export_glb import export_glb
from glasses3d.g3d_host.spec import validate_spec


DISCLOSURE='Coarse closed frame-leaf fixture inside the copied original leaf bounds; all other product objects retained.'


def export(path):
    receipt=export_glb(path);receipt['validate_spec']=validate_spec(path)
    assert receipt['validate_spec']['ok'],receipt['validate_spec']
    path.with_suffix('.export.json').write_text(json.dumps(receipt,indent=2))
    return receipt


def fixture():
    obj=bpy.data.objects['frame leaf1'];v=np.array([obj.matrix_world@p.co for p in obj.data.vertices])
    lo,hi=v.min(0),v.max(0)
    vertices=[[lo[0],lo[1],lo[2]],[hi[0],lo[1],lo[2]],[hi[0],hi[1],lo[2]],[lo[0],hi[1],lo[2]],
              [lo[0],lo[1],hi[2]],[hi[0],lo[1],hi[2]],[hi[0],hi[1],hi[2]],[lo[0],hi[1],hi[2]]]
    faces=[[0,3,2,1],[0,1,5,4],[1,2,6,5],[2,3,7,6],[3,0,4,7],[4,5,6,7]]
    mesh=bpy.data.meshes.new('Scratch copied frame leaf radius fixture');mesh.from_pydata(vertices,[],faces);mesh.update()
    for material in obj.data.materials:mesh.materials.append(material)
    for edge in mesh.edges:edge.use_edge_sharp=True
    obj.data=mesh;obj.matrix_world.identity()
    return {'original_bounds_mm':[lo.tolist(),hi.tolist()],'description':DISCLOSURE}


def apply(root,label,strength,mode):
    before,path=snapshot(root,label+'-before');obj=next(o for o in before['objects'] if o['name']=='frame leaf1')
    params={'strength':strength,'mode':mode,'radius_mm':.25 if mode=='round' else 0.,'minimum_mm':.4}
    if mode=='round':params.update(edge_vertex_ids=[0,4],segments=4)
    op=selection(obj,'edge_shape',params,'E1',{i:1. for i in range(len(obj['vertices_local']))})
    operations=owner_markings.seal(before,[op])
    result=owner_mesh_bpy.apply({'snapshot_path':str(path),'snapshot_sha256':M.digest(before),'operations':operations},root)
    assert result['status']=='ok',result
    after,_=snapshot(root,label+'-after');checks=S.verify(before,after,operations);assert checks['ok']
    return operations,checks


def run(output):
    source=Path(bpy.data.filepath).resolve();out=Path(output).resolve()
    if not source.is_file() or any(p in source.parts for p in ('deliveries','checkpoints','deliverable')):raise ValueError('open a disposable source copy')
    if out.exists() or any(p in out.parts for p in ('deliveries','checkpoints','deliverable')):raise ValueError('fresh scratch output required')
    digest=hashlib.sha256(source.read_bytes()).hexdigest();out.mkdir(parents=True)
    details=fixture();base=out/'fixture-copy.blend';bpy.ops.wm.save_as_mainfile(filepath=str(base))
    report={'scripted_by':'Codex, not Astra','owner_confirmation':False,'api_calls':0,'cost_usd':0,
            'source_copy':str(source),'source_copy_sha256':digest,'fixture':details,'examples':[]}
    for mode in ('round','sharpen'):
        folder=out/('edge_'+mode);folder.mkdir();bpy.ops.wm.open_mainfile(filepath=str(base))
        if mode=='sharpen':apply(folder,'seed-radius',1.,'round')
        beforeblend=folder/'before.blend';bpy.ops.wm.save_as_mainfile(filepath=str(beforeblend))
        baseline,_=snapshot(folder,'baseline');obj=next(o for o in baseline['objects'] if o['name']=='frame leaf1')
        legend=('E1 = copied leaf straight edge: round to 0.25 mm radius' if mode=='round' else
                'E1 = same authored leaf fillet: reduce its radius, ending at its original sharp corner')
        (folder/'selection.json').write_text(json.dumps({'label':'E1','legend':legend,'fixture':DISCLOSURE,'object':obj,
            'scope':{'object_id':obj['id'],'vertex_weights':[{'vertex_id':i,'weight':1.} for i in range(len(obj['vertices_local']))],
                     'fixed_vertex_ids':[],'affected_polygon_ids':list(range(len(obj['polygons'])))}},indent=2))
        export(folder/'before.glb');variants=[]
        for label,strength in (('preview',.5),('after',1.)):
            bpy.ops.wm.open_mainfile(filepath=str(beforeblend));work=folder/label;work.mkdir()
            operations,checks=apply(work,label,strength,mode);export(work/'model.glb')
            bpy.ops.wm.save_as_mainfile(filepath=str(work/'model-copy.blend'))
            variants.append({'name':label,'strength':strength,'glb':str(work/'model.glb'),'operations':operations,'checks':checks,'legend':{'E1':legend}})
        report['examples'].append({'kind':'edge_shape','mode':mode,'before':str(folder/'before.glb'),'variants':variants,'fixture':DISCLOSURE})
        (out/'proof.json').write_text(json.dumps(report,indent=2))
    assert hashlib.sha256(source.read_bytes()).hexdigest()==digest,'source copy changed'
    report['source_unchanged']=True;(out/'proof.json').write_text(json.dumps(report,indent=2))
    print('M8_RADIUS_COPY_PROOF '+str(out/'proof.json'))


if __name__=='__main__':run(sys.argv[sys.argv.index('--')+1])
