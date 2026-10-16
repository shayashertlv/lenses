"""General finite M8 edits retain selected scope and reject harmful amounts."""
import copy
import numpy as np
import pytest
from pathlib import Path
from glasses3d.g3d import owner_surface as S,owner_mesh as M,owner_markings
from glasses3d.tests.test_owner_surface import patch


@pytest.mark.parametrize('kind,params',[
    ('thickness',{'offset_mm':-.1,'minimum_mm':1.}),
    ('scale_region',{'factors':[1.02,1.,1.],'anchor_mm':[0.,0.,2.]}),
    ('bend_region',{'axis':[0.,0.,1.],'direction':[0.,1.,0.],'anchor_mm':[0.,-2.,0.],'angle_deg':2.,'length_mm':4.})])
def test_deformations_keep_opposite_surface_and_show_distinct_amounts(kind,params):
    source,op=patch(kind!='thickness');op.update(kind=kind,params={'strength':1.,**params})
    if kind!='thickness':
        op['scope']['vertex_weights']=[{'vertex_id':i,'weight':1.} for i in range(18)]
        op['scope']['affected_polygon_ids']=list(range(16))
    before=copy.deepcopy(source)
    after,reports=S.state(source,[op]);half=copy.deepcopy(op);half['params']['strength']=.5
    partial,_=S.state(source,[half]);assert S.signature(partial)!=S.signature(after)
    assert source==before
    if kind=='thickness':assert after['objects'][0]['vertices_local'][9:]==source['objects'][0]['vertices_local'][9:]
    assert after['objects'][0]['polygons']==source['objects'][0]['polygons']
    assert reports[0]['maximum_move_mm']>0
    assert S.verify(source,after,[op])['ok']
    tamper=copy.deepcopy(after);tamper['objects'][0]['vertices_local'][9][0]+=.1
    with pytest.raises(M.OwnerMeshError):S.verify(source,tamper,[op])


def test_thickness_refuses_actual_opposite_surface_crossing():
    source,op=patch(False);op.update(kind='thickness',params={'strength':1.,'offset_mm':-3.,'minimum_mm':1.})
    with pytest.raises(M.OwnerMeshError):S.state(source,[op])


def test_bend_honours_fractional_selected_support():
    source,op=patch(False);op.update(kind='bend_region',params={'strength':1.,'axis':[0,0,1],
        'direction':[0,1,0],'anchor_mm':[0,-2,0.],'angle_deg':2.,'length_mm':4.})
    op['scope']['vertex_weights']=[{'vertex_id':i,'weight':1.} for i in range(18)]
    op['scope']['affected_polygon_ids']=list(range(16))
    for row in op['scope']['vertex_weights']:row['weight']=.5
    weighted,_=S.state(source,[op])
    for row in op['scope']['vertex_weights']:row['weight']=1.
    op['params']['strength']=.5
    half,_=S.state(source,[op]);assert S.signature(weighted)==S.signature(half)


def marking_fixture():
    source,_=patch(False);surface=source['objects'][0]
    mark=copy.deepcopy(surface);v=[[-.4,-.2,2.02],[.4,-.2,2.02],[.4,.2,2.02],[-.4,.2,2.02]]
    mark.update(id='O0002',name='Mark',mesh_name='Mark',vertices_local=v,vertices_world_mm=v,polygons=[[0,1,2,3]],triangles=[[0,1,2],[0,2,3]],
        corner_normals=[[0,0,1]]*4,polygon_smooth=[False],polygon_material=[0],uv_layers=[],topology_sha256='mark',parent_name=surface['name'])
    source['objects'].append(mark)
    op={'id':'mark','kind':'transform_marking','target_ids':['M1'],'scope':{'object_id':'O0002','vertex_weights':[{'vertex_id':i,'weight':1.} for i in range(4)],
        'fixed_vertex_ids':[],'affected_polygon_ids':[0]},'params':{'strength':1.,'surface_object_id':'O0001','anchor_mm':[0,0,2.],
        'u_axis':[1,0,0],'normal':[0,0,1],'translate_uv_mm':[.2,.1],'scale':[1.1,.9],'angle_deg':10.}}
    return source,owner_markings.seal(source,[op])[0]


def test_marking_transform_projects_on_surface_and_keeps_it_exact():
    source,op=marking_fixture();after,report=S.state(source,[op])
    assert after['objects'][0]==source['objects'][0]
    points=np.asarray(after['objects'][1]['vertices_local'])
    np.testing.assert_allclose(points[:,2],2.02,atol=1e-6)
    np.testing.assert_allclose(points[:,:2].mean(0),[.2,.1],atol=1e-6)
    assert S.verify(source,after,[op])['ok']
    op['params']['translate_uv_mm']=[10,0]
    with pytest.raises(M.OwnerMeshError,match='leaves'):S.state(source,[op])


def test_marking_transform_requires_sealed_complete_selection():
    source,op=marking_fixture();op['scope']['vertex_weights'][0]['weight']=.5
    with pytest.raises(M.OwnerMeshError,match='complete'):S.state(source,[op])


def test_actual_blender_marking_transform_leaves_surface_unchanged():
    from glasses3d.g3d_host import blender
    if blender.blender_exe() is None:pytest.skip('Blender is not installed')
    result=blender.run(None,Path(__file__).parent/'blender/owner_extended.py',[],timeout_s=120)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'OWNER_MARK_TRANSFORM' in result.stdout,result.stdout+result.stderr
