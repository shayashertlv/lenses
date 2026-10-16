"""Measure the real before/after snapshots from assembly_m7_edits (no mutation)."""
import argparse
import json
from pathlib import Path
import numpy as np
from glasses3d.g3d import owner_surface as S, surface_quality as Q


def read(path):return json.loads(Path(path).read_text())


def mesh(snapshot,name):return next(o for o in snapshot['objects'] if o['name']==name)


def triangle_normals(obj):
    offsets=np.cumsum([0]+[len(p) for p in obj['polygons']]);norm=np.asarray(obj['corner_normals'])
    return np.asarray([[norm[offsets[pid]+obj['polygons'][pid].index(v)] for v in tri]
                       for tri,pid in zip(obj['triangles'],obj['triangle_polygons'])])


def edge_rows(obj):
    vertices=np.asarray(obj['vertices_world_mm']);normals=triangle_normals(obj)
    triangles=np.asarray(obj['triangles']);points=vertices[triangles]
    face=np.cross(points[:,1]-points[:,0],points[:,2]-points[:,0]);face/=np.maximum(np.linalg.norm(face,axis=1)[:,None],1e-30)
    normals/=np.maximum(np.linalg.norm(normals,axis=2,keepdims=True),1e-30)
    edges={}
    for i,tri in enumerate(triangles):
        for a,b in ((0,1),(1,2),(2,0)):
            edges.setdefault(tuple(sorted((int(tri[a]),int(tri[b])))),[]).append((i,a,b))
    result={}
    for edge,rows in edges.items():
        if len(rows)!=2:continue
        i,a,b=rows[0];j,c,d=rows[1]
        left={int(triangles[i,k]):normals[i,k] for k in (a,b)}
        right={int(triangles[j,k]):normals[j,k] for k in (c,d)}
        jump=max(float(np.degrees(np.arccos(np.clip(left[v]@right[v],-1,1)))) for v in edge)
        result[edge]={'mid_mm':vertices[list(edge)].mean(0).tolist(),
                      'polygon_ids':[obj['triangle_polygons'][i],obj['triangle_polygons'][j]],
                      'geometric_angle_deg':float(np.degrees(np.arccos(np.clip(face[i]@face[j],-1,1)))),
                      'normal_jump_deg':jump}
    return result


def measure(proof,out):
    proof=Path(proof);result={'informational_only':True,'source_proof':str(proof),'lenses':{},'front':{}}
    full=next(row for row in read(proof/'proof.json')['variants'] if row['strength']==1.)
    for name in ('Lens L','Lens R'):
        key=name.replace(' ','-')+'0'
        rows={}
        for tag in ('before','after'):
            obj=mesh(read(proof/'full/work/model_maps'/f'{key}-{tag}/snapshot.json'),name)
            v=np.asarray(obj['vertices_world_mm']);tri=np.asarray(obj['triangles'])
            p=v[tri];normal=np.cross(p[:,1]-p[:,0],p[:,2]-p[:,0]);normal/=np.maximum(np.linalg.norm(normal,axis=1)[:,None],1e-30)
            selected=normal[:,2]>.8
            rows[tag]=Q.analyse(v,tri[selected],triangle_normals(obj)[selected])
        result['lenses'][name]=rows
    for side in (1,-1):
        name='Unified faceted acetate front';key=name.replace(' ','-')+str(side)
        before=mesh(read(proof/'full/work/model_maps'/f'{key}-before/snapshot.json'),name)
        after=mesh(read(proof/'full/work/model_maps'/f'{key}-after/snapshot.json'),name)
        old,new=edge_rows(before),edge_rows(after)
        crease={e:r for e,r in old.items() if side*r['mid_mm'][0]>50 and r['mid_mm'][1]>2
                and abs(side*r['mid_mm'][0]+1.4*r['mid_mm'][1]-82)<.02 and 30<r['geometric_angle_deg']<60}
        op=next(op for row in full['operations'] for op in row['operation']
                if op['id']==('F1' if side==1 else 'F2'))
        v=np.asarray(before['vertices_world_mm']);polys=before['polygons']
        selected_faces=set(S.selected(before,op))
        faces=[polys[i] for i in selected_faces]
        counts={}
        for p in faces:
            for a,b in zip(p,p[1:]+p[:1]):e=tuple(sorted((a,b)));counts[e]=counts.get(e,0)+1
        splits=({e:len(v)+i for i,e in enumerate(sorted(e for e,c in counts.items() if c==2))}
                if op['kind']=='refine_curved_surface' else {})
        comparisons=[]
        for e,r in crease.items():
            children=[tuple(sorted((e[0],splits[e]))),tuple(sorted((splits[e],e[1])))] if e in splits else [e]
            incident=sum(face in selected_faces for face in r['polygon_ids'])
            comparisons.append({'source_edge':e,'selection_relation':
                'inside' if incident==2 else 'boundary' if incident else 'outside',
                'before':r,'after':[new[child] for child in children if child in new]})
        result['front'][str(side)]={'operation_kind':op['kind'],'tracked_original_crease_edges':comparisons,
            'note':('Tracks each original cut edge, or its children when refinement splits it; '
                    'a shading-only repair retains the original geometric crease.')}
    Path(out).write_text(json.dumps(result,indent=2));return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('proof');parser.add_argument('output')
    args=parser.parse_args();measure(args.proof,args.output)
