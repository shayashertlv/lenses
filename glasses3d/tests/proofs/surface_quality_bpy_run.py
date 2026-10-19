"""Small synthetic Blender regression; never opens or saves a product model."""
import sys,json
from pathlib import Path
import bpy
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from glasses3d.g3d.temple_bpy import _repair_degenerate_tessellation

v = [(i*10/8, 0, 0) for i in range(9)]+[(10, 10, 0), (0, 10, 0)]
v = np.array([(70+x*.001, y, x*.0003+y*.04) for x, y, z in v])
me = bpy.data.meshes.new('thin tilted planar polygon')
me.from_pydata(v.tolist(), [], [list(range(len(v)))])
me.update()
me.shade_smooth()
normal = np.cross(v[1]-v[0], v[-1]-v[0]);normal /= np.linalg.norm(normal)
me.normals_split_custom_set([normal.tolist()]*len(v))
uv = me.uv_layers.new(name='test');coords = [(float(i)/len(v), float(i%3)/3) for i in range(len(v))]
for x,y in zip(uv.data, coords): x.uv=y
me.edges[0].use_edge_sharp=True
canonical = [[10, i, i+1] for i in range(8)]+[[10, 8, 9]]

def bad(mesh):
    mesh.calc_loop_triangles()
    p=np.asarray([x.co[:] for x in mesh.vertices]);f=np.asarray([x.vertices[:] for x in mesh.loop_triangles]);q=p[f]
    return int(sum(np.linalg.norm(np.cross(q[:,1]-q[:,0], q[:,2]-q[:,0]), axis=1)<=2e-12))

def boundary(mesh):
    counts={}
    for p in mesh.polygons:
        ids=list(p.vertices)
        for a,b in zip(ids,ids[1:]+ids[:1]):
            edge=tuple(sorted((a,b)));counts[edge]=counts.get(edge,0)+1
    return sorted(k for k,v in counts.items() if v==1)

before_bad=bad(me);before_normals=np.asarray([n.vector[:] for n in me.corner_normals]);new,n=_repair_degenerate_tessellation(me,v,canonical,[0]*len(canonical))
uv_error=0.;normal_error=0.;normal_angle=0.
for p in new.polygons:
    for i in p.loop_indices:
        vi=new.loops[i].vertex_index
        uv_error=max(uv_error,float(np.max(np.abs(np.asarray(new.uv_layers[0].data[i].uv)-coords[vi]))))
        normal_error=max(normal_error,float(np.max(np.abs(np.asarray(new.corner_normals[i].vector)-before_normals[vi]))))
        a=np.asarray(new.corner_normals[i].vector);b=before_normals[vi]
        normal_angle=max(normal_angle,float(np.degrees(np.arccos(np.clip(a@b/np.linalg.norm(a)/np.linalg.norm(b),-1,1)))))
_,second=_repair_degenerate_tessellation(new,v)
rep={'before_degenerate':before_bad,'after_degenerate':bad(new),'repaired_polygons':n,
     'positions_unchanged':np.array_equal(np.asarray([x.co[:] for x in me.vertices]),np.asarray([x.co[:] for x in new.vertices])),
     'boundary_unchanged':boundary(me)==boundary(new),'uv_max_error':uv_error,'normal_max_error':normal_error,
     'normal_max_angle_deg':normal_angle,'second_repaired':second}
from glasses3d.g3d.model_map_bpy import _aliases
from glasses3d.g3d.temple_lineage import freeze
from glasses3d.g3d.temple import build_temple, fixtures
tb=build_temple(fixtures.prada_default())
world,polys,tris=tb.world()
pi=next(i for i,p in enumerate(polys) if len(p)>3)
polys=polys[:pi]+tris[tb.tri_poly==pi].tolist()+polys[pi+1:]
metadata={'g3d_temple':json.dumps({'canonical_spec':tb.spec.canonical,'side':'+x',
    'spec_sha256':tb.spec.sha256,'lineage':freeze(tb,1,len(world),polys)})}
raw={'vertices_local':world.tolist(),'polygons':polys}
rep['aliases_after_core_triangulation']=_aliases(metadata,raw)['status']
raw['polygons']=[*polys,polys[0]]
rep['aliases_reject_extra_face']=_aliases(metadata,raw)['status']
from glasses3d.g3d import temple_bpy as T
from glasses3d.g3d.temple import build as TB
old_spec=fixtures.slab();old_spec['hinge']['opening_deg']=17.
new_spec=json.loads(json.dumps(old_spec))
for station in new_spec['inner']:station[1]+=1.25
old_tb=build_temple(old_spec);new_tb=build_temple(new_spec)
old_chart=T.OuterSurface(old_tb);new_chart=T.OuterSurface(new_tb)
h=old_spec['hinge'];rep['decal_charts']=[]
for side in (1,-1):
    world,polys,_=old_tb.world(side);old_bvh=T._bvh(world,polys)
    for inner in (False,True):
        chart=old_chart.inner if inner else old_chart
        target=new_chart.inner if inner else new_chart
        local=[];expected=[]
        for d,y in ((20.,0.),(30.,2.),(40.,-1.)):
            x,n,_,_=chart.at(d,y);local.append(np.array([d,y,x])+.03*n)
            x,n,_,_=target.at(d,y);expected.append(np.array([d,y,x])+.03*n)
        v=TB.to_world(np.asarray(local),h['z_mm'],h['axis_xz_mm'],h['opening_deg'])
        want=TB.to_world(np.asarray(expected),h['z_mm'],h['axis_xz_mm'],h['opening_deg'])
        if side<0:v[:,0]*=-1;want[:,0]*=-1
        actual=T.transfer_decal(v,old_bvh,new_chart,side,h)
        rep['decal_charts'].append({'side':side,'inner':inner,'regions':actual['regions'],
            'max_position_error_mm':float(np.max(np.linalg.norm(actual['V_new']-want,axis=1))),
            'max_standoff_error_mm':float(np.max(np.abs(actual['standoff']-.03)))})
Path(sys.argv[sys.argv.index('--')+1]).write_text(json.dumps(rep))
