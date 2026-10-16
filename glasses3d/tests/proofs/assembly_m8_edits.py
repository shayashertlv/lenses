"""Offline M8 examples through the real mesh worker, on a disposable blend copy.

Run Blender with --python-exit-code 1. The caller supplies a copied source and a
fresh output directory. All quantities are demonstration proposals scripted by
Codex, not Astra, not an owner's confirmation and not a model delivery.
"""
from __future__ import annotations
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
    reply=model_map_bpy.snapshot({'path':str(path)},root)
    assert reply['status']=='ok',reply
    return json.loads(path.read_text()),path


def selection(obj,kind,params,label,weights):
    weights={int(i):float(w) for i,w in weights.items() if w>0}
    affected=[i for i,p in enumerate(obj['polygons']) if weights.keys()&set(p)]
    return {'id':label,'kind':kind,'target_ids':[label],'params':params,
            'scope':{'object_id':obj['id'],
                     'vertex_weights':[{'vertex_id':i,'weight':w} for i,w in sorted(weights.items())],
                     'fixed_vertex_ids':[],'affected_polygon_ids':affected}}


def proposal(snapshot,kind,strength):
    byname={o['name']:o for o in snapshot['objects']}
    name={'material_adjust':'Unified faceted acetate front',
          'thickness':'arm leaf1','scale_region':'Faceted temple L','bend_region':'arm leaf1',
          'transform_marking':'Gold PRADA 1'}[kind]
    obj=byname[name];v=np.asarray(obj['vertices_world_mm']);polys=obj['polygons']
    weights={i:1. for i in range(len(v))}
    params={'strength':strength}
    if kind=='material_adjust':
        params.update(material_index=0,values={'base_color':[.55,.38,.17,1.],'roughness':.32})
        label,legend='F1','whole front frame: warmer colour and a rougher finish'
    elif kind=='thickness':
        weights={i:1. for i,p in enumerate(v) if p[0]>=v[:,0].max()-1e-5}
        params.update(offset_mm=.15)
        label,legend='H1','outer face of the left metal arm leaf: increase its thickness while its opposite face stays fixed'
    elif kind=='scale_region':
        weights={i:float(np.clip((-p[2]-95)/25,0,1)) for i,p in enumerate(v)}
        params.update(factors=[1.12,1.,1.],anchor_mm=[float(v[:,0].mean()),0.,-95.])
        label,legend='T1','left arm tip: widen across X about the fixed centre at the start of the transition, tapering to zero change toward the join'
    elif kind=='bend_region':
        params.update(axis=[1.,0.,0.],direction=[0.,0.,-1.],
                      anchor_mm=[float(v[:,0].mean()),float(v[:,1].mean()),float(v[:,2].max())],
                      angle_deg=15.,length_mm=float(np.ptp(v[:,2])))
        label,legend='H1','left metal arm leaf: bend along its length with quadratic falloff from the fixed hinge end'
    else:
        params.update(surface_object_id=byname['Faceted temple L']['id'],anchor_mm=v.mean(0).tolist(),
                      u_axis=[0.,0.,-1.],normal=[1.,0.,0.],translate_uv_mm=[1.5,0.],scale=[1.12,1.12],angle_deg=4.)
        label,legend='M1','PRADA lettering on the left arm: move along the surface, enlarge and rotate slightly'
    return selection(obj,kind,params,label,weights),legend,obj


def run(out,kinds):
    source=Path(bpy.data.filepath).resolve();out=Path(out).resolve()
    if any(name in source.parts for name in ('deliveries','checkpoints','deliverable')):
        raise ValueError('open a copied blend file for this proof')
    if out.exists() or any(name in out.parts for name in ('deliveries','deliverable')):
        raise ValueError('fresh scratch output required')
    before_hash=hashlib.sha256(source.read_bytes()).hexdigest();out.mkdir(parents=True)
    report={'scripted_by':'Codex, not Astra','cost_usd':0,'api_calls':0,'source_copy':str(source),
            'source_copy_sha256':before_hash,'owner_confirmation':False,'examples':[]}
    for kind in kinds:
        folder=out/kind;folder.mkdir();rows=[]
        bpy.ops.wm.open_mainfile(filepath=str(source))
        baseline,path=snapshot(folder,'baseline')
        base_export=export_glb(folder/'before.glb')
        (folder/'before-export.json').write_text(json.dumps(base_export,indent=2))
        for strength,name in ((.5,'preview'),(1.,'after')):
            bpy.ops.wm.open_mainfile(filepath=str(source))
            work=folder/name;work.mkdir()
            before,path=snapshot(work,'before')
            op,legend,obj=proposal(before,kind,strength)
            operations=owner_markings.seal(before,[op])
            applied=owner_mesh_bpy.apply({'snapshot_path':str(path),'snapshot_sha256':M.digest(before),
                                          'operations':operations},work)
            assert applied['status']=='ok',applied
            after,_=snapshot(work,'after')
            checks=S.verify(before,after,operations)
            assert checks['ok'],checks
            export=export_glb(work/'model.glb')
            export['validate_spec']=validate_spec(work/'model.glb')
            assert export['validate_spec']['ok'],export['validate_spec']
            (work/'export.json').write_text(json.dumps(export,indent=2))
            bpy.ops.wm.save_as_mainfile(filepath=str(work/'model-copy.blend'))
            rows.append({'name':name,'strength':strength,'glb':str(work/'model.glb'),
                         'operations':operations,'checks':checks,'legend':{op['id']:legend}})
            if name=='preview':
                (folder/'selection.json').write_text(json.dumps({'label':op['id'],'legend':legend,
                    'object':obj,'scope':op['scope']},indent=2))
        report['examples'].append({'kind':kind,'before':str(folder/'before.glb'),'variants':rows})
        (out/'proof.json').write_text(json.dumps(report,indent=2))
    assert hashlib.sha256(source.read_bytes()).hexdigest()==before_hash,'source copy changed'
    report['source_unchanged']=True
    (out/'proof.json').write_text(json.dumps(report,indent=2))
    print('M8_COPY_PROOF '+str(out/'proof.json'))


if __name__=='__main__':
    args=sys.argv[sys.argv.index('--')+1:]
    # True round/sharpen radius examples live in assembly_m8_radius.py.
    run(args[0],args[1:] or ['material_adjust','thickness','scale_region','bend_region','transform_marking'])
