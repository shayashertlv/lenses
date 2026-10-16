import copy
import numpy as np
import pytest
from pathlib import Path
from glasses3d.g3d import owner_surface as S,owner_mesh as M
from glasses3d.tests.test_owner_health import box


def fixture():
    obj,op=box();obj['vertices_local']=obj['vertices_local'][:8];obj['vertices_world_mm']=obj['vertices_world_mm'][:8]
    obj['polygons']=obj['polygons'][:5]+[[4,5,6,7]]
    obj['triangles']=[[p[0],p[k],p[k+1]] for p in obj['polygons'] for k in range(1,len(p)-1)]
    obj['corner_normals']=[n.tolist() for n,p in zip(S.face_vectors(np.asarray(obj['vertices_world_mm']),obj['polygons']),obj['polygons']) for _ in p]
    obj['polygon_smooth']=[False]*6;obj['polygon_material']=[0]*6
    op.update(id='round',kind='edge_shape',params={'strength':1.,'mode':'round','radius_mm':.2,'segments':4,'edge_vertex_ids':[4,5],'minimum_mm':1.5})
    op['scope']['vertex_weights']=op['scope']['vertex_weights'][:8];op['scope']['affected_polygon_ids']=list(range(6))
    return {'schema':'g3d.mesh_snapshot/1','origin_mm':[0,0,0],'objects':[obj]},op


def test_true_circular_fillet_preserves_adjoining_planes_and_sharp_restore():
    source,op=fixture();after,reports=S.state(source,[op]);obj=after['objects'][0]
    assert reports[0]['radius_after_mm']==.2
    assert len(obj['polygons'])==10 and len(obj['vertices_local'])==16
    # Edge spans X; all four new intermediate normals/points follow radius .2.
    center=np.array([-2.,-1.8,.8]);points=np.array(obj['vertices_local'])
    for i in [4,8,9,10,11]:assert np.linalg.norm((points[i]-center)[1:])==pytest.approx(.2,abs=1e-7)
    assert S.verify(source,after,[op])['ok']
    sharpen=copy.deepcopy(op);sharpen.update(id='sharp',params={'strength':1.,'mode':'sharpen','radius_mm':0.,'minimum_mm':1.5})
    sharpen['scope']['vertex_weights']=[{'vertex_id':i,'weight':1.} for i in range(len(obj['vertices_local']))]
    sharpen['scope']['affected_polygon_ids']=list(range(len(obj['polygons'])))
    restored,_=S.state(after,[sharpen]);assert restored['objects'][0]['vertices_local']==source['objects'][0]['vertices_local']
    assert restored['objects'][0]['polygons']==source['objects'][0]['polygons']
    assert S.verify(after,restored,[sharpen])['ok']


@pytest.mark.parametrize('change',['large','minimum','unselected','missing_recipe'])
def test_radius_refuses_invalid_geometry_floor_and_scope(change):
    source,op=fixture()
    if change=='large':op['params']['radius_mm']=10.
    if change=='minimum':op['params']['minimum_mm']=1.99
    if change=='unselected':op['scope']['vertex_weights'][0]['weight']=0.;op['scope']['affected_polygon_ids']=[i for i,p in enumerate(source['objects'][0]['polygons']) if set(p)&set(range(1,8))]
    if change=='missing_recipe':op['params']={'strength':1.,'mode':'sharpen','radius_mm':0.,'minimum_mm':1.5}
    with pytest.raises(M.OwnerMeshError):S.state(source,[op])


def test_blender_round_smaller_and_restore_keep_full_scope():
    from glasses3d.g3d_host import blender
    if blender.blender_exe() is None:pytest.skip('Blender is not installed')
    result=blender.run(None,Path(__file__).parent/'blender/owner_edge_radius.py',[],timeout_s=120)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'OWNER_RADIUS_RESULT' in result.stdout,result.stdout+result.stderr
