"""Offline owner surface-operation examples using a disclosed copied-leaf fixture.

Open a copied product blend and run --python this.py -- FRESH_OUTPUT. The copied
frame leaf is replaced by a closed box near its bounds (corners rounded to a
1/128 mm grid so the demonstration's centre is exactly symmetric in float32).
Flattening gets a deliberate
0.15 mm outer-face centre bump; shading repair gets deliberately tilted normals.
The owner worker applies two scripted preview amounts; no confirmation is claimed.
"""
from pathlib import Path
import hashlib
import json
import sys
import bpy
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from glasses3d.tests.proofs.assembly_m8_health import box,install
from glasses3d.tests.proofs.assembly_m8_edits import snapshot,selection
from glasses3d.g3d import owner_mesh_bpy,owner_mesh as M,owner_surface as S,owner_markings
from glasses3d.g3d.export_glb import export_glb
from glasses3d.g3d_host.spec import validate_spec


def fixture(kind):
    obj=bpy.data.objects['frame leaf1'];v,p=box(obj)
    # Put the two fans on the thin leaf's X faces. An outer-X bump preserves
    # a positive opposite-surface thickness throughout its projection.
    a=np.asarray(v);lo,hi=a.min(0),a.max(0);old=a.copy()
    a[:,0]=lo[0]+(old[:,2]-lo[2])/(hi[2]-lo[2])*(hi[0]-lo[0])
    a[:,2]=lo[2]+(old[:,0]-lo[0])/(hi[0]-lo[0])*(hi[2]-lo[2])
    a[:8]=np.round(a[:8]*128)/128
    a[8]=(a[4]+a[6])/2;a[9]=(a[0]+a[2])/2
    v=a.tolist();p=[list(reversed(poly)) for poly in p]
    if kind=='flatten_face':v[8][0]+=.15
    install(obj,v,p,kind)
    if kind=='repair_shading':
        normals=[list(n.vector) for n in obj.data.corner_normals]
        for polygon in list(obj.data.polygons)[8:]:
            for i in polygon.loop_indices:normals[i]=[2**-.5,0.,2**-.5]
        obj.data.normals_split_custom_set(normals)


def run(output):
    source=Path(bpy.data.filepath).resolve();out=Path(output).resolve()
    if not source.is_file() or any(n in source.parts for n in ('deliveries','checkpoints','deliverable')):
        raise ValueError('open a copied blend source first')
    if out.exists() or any(n in out.parts for n in ('deliveries','checkpoints','deliverable')):
        raise ValueError('fresh scratch output required')
    sha=hashlib.sha256(source.read_bytes()).hexdigest();out.mkdir(parents=True)
    report={'source_copy':str(source),'source_copy_sha256':sha,'cost_usd':0,'api_calls':0,
            'scripted_by':'Codex','owner_confirmation':False,'examples':[]}
    for kind in ('flatten_face','repair_shading'):
        folder=out/kind;folder.mkdir();bpy.ops.wm.open_mainfile(filepath=str(source));fixture(kind)
        baseline,_=snapshot(folder,'baseline');obj=next(o for o in baseline['objects'] if o['name']=='frame leaf1')
        weights={i:1. for i in (4,5,6,7,8)}
        legend=('copied frame-leaf box outer face: flatten the deliberately raised centre while keeping its border fixed'
                if kind=='flatten_face' else 'copied frame-leaf box outer face: repair deliberately tilted shading normals without moving geometry')
        params={'strength':1.,'keep_boundary':True} if kind=='flatten_face' else {'strength':1.,'mode':'flat'}
        op=selection(obj,kind,params,'F1',weights)
        (folder/'selection.json').write_text(json.dumps({'label':'F1','legend':legend,'object':obj,'scope':op['scope']},indent=2))
        bpy.ops.wm.save_as_mainfile(filepath=str(folder/'before.blend'));export_glb(folder/'before.glb')
        before_check=validate_spec(folder/'before.glb');assert before_check['ok'],before_check
        rows=[]
        for strength,name in ((.5,'preview'),(1.,'after')):
            bpy.ops.wm.open_mainfile(filepath=str(folder/'before.blend'));work=folder/name;work.mkdir()
            before,path=snapshot(work,'before');obj=next(o for o in before['objects'] if o['name']=='frame leaf1')
            operation=selection(obj,kind,{**params,'strength':strength},'F1',weights)
            operations=owner_markings.seal(before,[operation])
            applied=owner_mesh_bpy.apply({'snapshot_path':str(path),'snapshot_sha256':M.digest(before),'operations':operations},work)
            assert applied['status']=='ok',applied
            after,_=snapshot(work,'after');checks=S.verify(before,after,operations);assert checks['ok'],checks
            export=export_glb(work/'model.glb');export['strict_spec']=validate_spec(work/'model.glb');assert export['strict_spec']['ok'],export
            (work/'export.json').write_text(json.dumps(export,indent=2));bpy.ops.wm.save_as_mainfile(filepath=str(work/'model-copy.blend'))
            rows.append({'name':name,'strength':strength,'glb':str(work/'model.glb'),'checks':checks,'operations':operations})
        report['examples'].append({'kind':kind,'fixture_disclosure':legend,'before':str(folder/'before.glb'),'variants':rows})
        (out/'proof.json').write_text(json.dumps(report,indent=2))
    assert hashlib.sha256(source.read_bytes()).hexdigest()==sha
    report['source_copy_unchanged']=True;(out/'proof.json').write_text(json.dumps(report,indent=2));print('M7_SURFACE_OPS_PROOF',out)


if __name__=='__main__':run(sys.argv[sys.argv.index('--')+1])
