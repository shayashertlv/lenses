"""Reproducible cached-product audits for the five implementation workstreams."""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np

from reconstruction.component_scene import load_component_scene
from reconstruction.face_role_repair import propose_face_roles
from reconstruction.mesh import TriangleMesh
from reconstruction.production_validation import measure_heldout_views, artifact_metrics, evaluate_acceptance


def read(path):return json.loads(Path(path).read_bytes())
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write(path,value):Path(path).write_text(json.dumps(value,indent=2,allow_nan=False)+'\n',encoding='utf-8')


def topology(job,physical,output):
    source=load_component_scene(physical/'inventory'/'report.json',recompute_labels=False)
    refined=read(job/'stages/refinement/attempt_1/report.json');report=read(physical/'report.json')
    norm=refined['normalization'];mesh=TriangleMesh((source['mesh'].vertices-np.asarray(norm['center']))/norm['extent'],source['mesh'].faces,[])
    from reconstruction.interior_contact import interior_contact_faces
    owners=np.zeros(len(mesh.faces),np.int64)
    for row in source['component_table']:owners[source['face_components']==row['component_id']]=row['source_primitive_ordinal']
    interior,_=interior_contact_faces(source['mesh'],owners=owners);faces=mesh.faces.copy();faces[interior]=faces[interior][:,[0,0,0]]
    visibility=TriangleMesh(mesh.vertices,faces,[])
    selected=next(h for h in report['hypotheses'] if h['index']==report['selected_hypothesis']['index'])
    views=[]
    for view in report['views']:
        if view['status']!='projected':continue
        data=np.load(physical/view['aperture_arrays']['path'],allow_pickle=False)
        interpretations=[{'mask':data[a['variant']+'_mask'],'known_domain':data[a['variant']+'_known_domain']} for a in view['apertures']]
        width,height=view['working_size_xy']
        views.append({'id':view['id'],'shape':(height,width),'camera':view['camera'],'interpretations':interpretations})
    result=propose_face_roles(mesh,source['face_components'],selected['consensus_optical_groups'],views,visibility_mesh=visibility)
    result.update(source_sha256=source['source_sha256'],physical_report_sha256=sha(physical/'report.json'))
    write(output/'report.json',result)
    print(json.dumps({'status':result['status'],'assigned_faces':sum(len(a['source_face_indices']) for a in result['assignments']),
                      'eligible_face_count':result['eligible_face_count'],'patches':len(result['assignments'])}),flush=True)


def heldout(output):
    cases=[]
    for name,identifier in [('rayban','186d88b7-1dfd-4cd6-8bdc-c141af34f2b4'),('invu','f8934f43-a232-4a6b-9bb9-f722c0f1ef5b')]:
        job=Path('data/jobs')/(name+'-inferred-v1');model=job/'candidate.glb'
        initializer=read(job/'report.json')['initializer']
        used=[p['sha256'] for p in initializer['all_photos']]
        photos=[{'id':view,'view':view,'path':str((Path('../ar_v4/modeling_auto/data/jobs')/identifier/'references'/(view+'.jpg')).resolve())}
                for view in ('back','left','right')]
        report=measure_heldout_views(model,photos,used_photo_sha256=used,source_history_complete=False,resolution=192)
        report['product_id']=name
        report['holdout_scope']='Held out from this implementation and current refinement; historical provider training/input use is not fully known.'
        write(output/(name+'.json'),report);cases.append(report)
        print(json.dumps({'case':name,'iou':report['minimum_iou'],'contour_p95':report['maximum_contour_p95'],'views':report['views']}),flush=True)
    write(output/'report.json',{'schema_version':1,'cases':cases,'accepted':False,
                              'policy_frozen_before_evaluation':True,'new_unseen_product_claim':False})


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--phase',choices=('topology','heldout','delivery'),required=True)
    p.add_argument('--job',type=Path);p.add_argument('--physical',type=Path);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--model',type=Path);p.add_argument('--export',type=Path);p.add_argument('--regions',type=Path)
    a=p.parse_args()
    if a.output.exists() and any(a.output.iterdir()):raise ValueError('Use fresh audit output')
    if a.phase=='delivery':
        from reconstruction.delivery_stage import run_delivery_stage
        from reconstruction.semantic_appearance_stage import render_material_cards
        result=run_delivery_stage(a.model,a.export,a.regions,a.output,renderer=render_material_cards,physical_report=a.physical)
        print(json.dumps({'status':result['status'],'quality':result['quality'],'model':result['model']}),flush=True)
    else:
        a.output.mkdir(parents=True,exist_ok=True)
        if a.phase=='topology':topology(a.job,a.physical,a.output)
        else:heldout(a.output)


if __name__=='__main__':main()
