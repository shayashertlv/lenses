"""Blender-side M7 surface tool proof on a caller-provided disposable checkpoint.

No delivery paths are accepted as output. Scripted by Codex, not Astra; preview
amounts are examples, not owner confirmation or paid-run authorization.
"""
import hashlib
import json
from pathlib import Path
import sys

import bpy
import numpy as np

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from glasses3d.g3d import model_map_bpy, owner_mesh_bpy, owner_mesh as M, owner_surface as S, owner_markings
from glasses3d.g3d.export_glb import export_glb
from glasses3d.g3d_host.spec import validate_spec


def snapshot(root,label):
    path=root/'work/model_maps'/label/'snapshot.json'
    result=model_map_bpy.snapshot({'path':str(path)},root)
    assert result['status']=='ok',result
    return json.loads(path.read_text()),path


def operation(obj,kind,params,vertex_ids,label):
    vertex_ids=set(vertex_ids)
    return {'id':label,'kind':kind,'target_ids':[label], 'params':params,
            'scope':{'object_id':obj['id'],'vertex_weights':[{'vertex_id':i,'weight':1.} for i in sorted(vertex_ids)],
                     'fixed_vertex_ids':[], 'affected_polygon_ids':[i for i,p in enumerate(obj['polygons']) if vertex_ids.intersection(p)]}}


def run(out):
    out=Path(out).resolve()
    if 'deliveries' in out.parts or 'deliverable' in out.parts or out.exists():
        raise ValueError('fresh scratch output required')
    out.mkdir(parents=True)
    source=Path(bpy.data.filepath).resolve()
    if 'deliveries' in source.parts or 'checkpoints' in source.parts:
        raise ValueError('open a copied checkpoint for this proof')
    source_hash=hashlib.sha256(source.read_bytes()).hexdigest()
    report={'scripted_by':'Codex, not Astra','api_calls':0,'cost_usd':0,'source_copy':str(source),
            'source_copy_sha256':source_hash,'owner_confirmation':False,'variants':[]}
    for strength in (.5,1.):
        bpy.ops.wm.open_mainfile(filepath=str(source))
        folder=out/('half' if strength==.5 else 'full');folder.mkdir()
        details=[]
        # Source object names are fixture evidence only; operations accept any mapped mesh.
        for name,side in (('Lens L',0),('Lens R',0),('Unified faceted acetate front',1),('Unified faceted acetate front',-1)):
            key=name.replace(' ','-')+str(side)
            before,path=snapshot(folder,key+'-before')
            obj=next(row for row in before['objects'] if row['name']==name)
            v=np.asarray(obj['vertices_world_mm']); polys=obj['polygons']
            p=np.asarray([v[ids].mean(0) for ids in polys])
            normals=S.face_vectors(v,polys)
            if name.startswith('Lens '):
                selected=[i for i,n in enumerate(normals) if n[2]>.8]
                ids={j for i in selected for j in polys[i]}
                op=operation(obj,'refine_curved_surface',{'strength':strength},ids,'L1' if name.endswith('L') else 'L2')
                legend='front curved surface of the '+('left' if name.endswith('L') else 'right')+' lens: finer curved triangles, keeping the original outline'
            else:
                # Include both actual faces of the authored corner cut. A .8
                # normal cutoff excludes its sloping face and misses the seam.
                selected=np.flatnonzero((side*p[:,0]>50)&(p[:,1]>2)&(normals[:,2]>.7))
                ids={j for i in selected for j in polys[i]}
                op=operation(obj,'repair_shading',{'strength':strength,'mode':'smooth'},ids,'F1' if side==1 else 'F2')
                legend='top outer corner of the front on the '+('left' if side==1 else 'right')+' side: join the shading across the selected crease, keeping its geometry and outline'
            (folder/(key+'-selection.json')).write_text(json.dumps({'label':op['id'],'legend':legend,
                'object':obj,'scope':op['scope']},indent=2))
            ops=owner_markings.seal(before,[op])
            result=owner_mesh_bpy.apply({'snapshot_path':str(path),'snapshot_sha256':M.digest(before),'operations':ops},folder)
            assert result['status']=='ok',result
            after,_=snapshot(folder,key+'-after')
            checked=S.verify(before,after,ops)
            assert checked['ok'],checked
            details.append({'object':name,'operation':ops,'checks':checked,'legend':legend})
        model=folder/'model.glb';receipt=export_glb(model)
        receipt['validate_spec']=validate_spec(model)
        assert receipt['validate_spec']['ok'],receipt['validate_spec']
        (folder/'export.json').write_text(json.dumps(receipt,indent=2))
        bpy.ops.wm.save_as_mainfile(filepath=str(folder/'model-copy.blend'))
        report['variants'].append({'strength':strength,'glb':str(model),'operations':details})
    assert hashlib.sha256(source.read_bytes()).hexdigest()==source_hash,'source copy changed'
    report['source_unchanged']=True
    (out/'proof.json').write_text(json.dumps(report,indent=2))
    print('M7_COPY_PROOF '+str(out/'proof.json'))


if __name__=='__main__':
    run(sys.argv[sys.argv.index('--')+1])
