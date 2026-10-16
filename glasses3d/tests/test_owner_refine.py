"""Local interpolation, shape-preserving triangle subdivision and its guards."""
import copy
import json
from pathlib import Path
import numpy as np
import pytest

from glasses3d.g3d import owner_surface as S, owner_mesh as M, owner_refine as R
from glasses3d.tests.test_owner_surface import patch


def triangle_patch():
    snapshot,op=patch();obj=snapshot['objects'][0]
    polygons=obj['polygons'];normals=np.asarray(obj['corner_normals']).reshape(-1,4,3)
    uv=np.asarray(obj['uv_layers'][0]['uv']).reshape(-1,4,2)
    newp=[];newn=[];newuv=[]
    for i,p in enumerate(polygons):
        for ids in ((0,1,2),(0,2,3)) if i<4 else ((0,1,2,3),):
            newp.append([p[j] for j in ids]);newn.extend(normals[i,list(ids)]);newuv.extend(uv[i,list(ids)])
    obj.update(polygons=newp,corner_normals=np.asarray(newn).tolist(),
        polygon_smooth=[True]*len(newp),polygon_material=[0]*len(newp),
        triangles=[[p[0],p[k],p[k+1]] for p in newp for k in range(1,len(p)-1)])
    obj['uv_layers'][0]['uv']=np.asarray(newuv).tolist()
    op['scope']['affected_polygon_ids']=[i for i,p in enumerate(newp) if set(p)&set(range(9))]
    return snapshot,op


def test_cubic_midpoint_is_endpoint_tangent_bezier_interpolation():
    points=np.array([[0.,0.,0.],[3.,0.,1.]])
    normals=np.array([[0.,0.,1.],[-1.,0.,1.]])/np.array([[1.],[2**.5]])
    a,b=points;na,nb=normals
    c1=(2*a+b-np.dot(b-a,na)*na)/3
    c2=(2*b+a-np.dot(a-b,nb)*nb)/3
    expected=(a+3*c1+3*c2+b)/8
    np.testing.assert_allclose(R.sample(points,normals,1),expected)
    np.testing.assert_allclose(R.sample(points,normals,.5),(points.mean(0)+expected)/2)


def test_quadratic_normal_midpoint_uses_chord_control_not_linear_average():
    points=np.array([[0.,0.,0.],[3.,0.,1.]])
    normals=np.array([[0.,0.,1.],[-1.,0.,1.]])/np.array([[1.],[2**.5]])
    edge=points[1]-points[0];pair=normals.sum(0)
    control=pair-2*np.dot(edge,pair)/np.dot(edge,edge)*edge
    control/=np.linalg.norm(control)
    expected=.25*normals.sum(0)+.5*control;expected/=np.linalg.norm(expected)
    actual=R.sample_normal(points,normals,1.)
    np.testing.assert_allclose(actual,expected,atol=1e-15)
    assert np.linalg.norm(actual-R.mean_normal(normals))>.01
    np.testing.assert_allclose(R.sample_normal(points,normals,0),R.mean_normal(normals))
    np.testing.assert_allclose(R.sample_normal(points[::-1],normals[::-1],1),actual,atol=1e-15)


def test_quadratic_normal_plane_invariance_and_triangle_controls():
    points=np.array([[0.,0.,0.],[3.,0.,0.],[1.,2.,0.]])
    normals=np.tile([0.,0.,1.],(3,1))
    for strength in (.5,1.):
        np.testing.assert_array_equal(R.sample_normal(points,normals,strength),[0.,0.,1.])
        np.testing.assert_array_equal(R.sample_normal(points[:2],normals[:2],strength),[0.,0.,1.])
    points[1,2]=1;normals[1]=[-1/2**.5,0.,1/2**.5]
    controls=[]
    for a,b in ((0,1),(1,2),(0,2)):
        edge=points[b]-points[a];pair=normals[a]+normals[b]
        control=pair-2*np.dot(edge,pair)/np.dot(edge,edge)*edge
        controls.append(control/np.linalg.norm(control))
    expected=(normals.sum(0)+2*np.sum(controls,axis=0))/9
    expected/=np.linalg.norm(expected)
    np.testing.assert_allclose(R.sample_normal(points,normals,1.),expected,atol=1e-15)


def test_triangle_four_children_preserve_scope_uv_and_every_vertex_is_used():
    source,op=triangle_patch();saved=copy.deepcopy(source)
    after,reports=S.state(source,[op]);a=source['objects'][0];b=after['objects'][0]
    assert source==saved and b['vertices_local'][:18]==a['vertices_local']
    parents=b['_surface_polygon_parents']
    split={tuple(row['edge']) for row in b['_surface_edge_splits']}
    four=[i for i,p in enumerate(a['polygons'][:8]) if all(tuple(sorted((u,v))) in split
          for u,v in zip(p,p[1:]+p[:1]))]
    assert four
    assert all(parents.count(i)==4 for i in four)
    assert {v for p in b['polygons'] for v in p}==set(range(len(b['vertices_local'])))
    assert M.physical(b)['closed']
    assert S.verify(source,after,[op])['ok']
    assert 'local cubic tangent' in reports[0]['insertion_policy']
    assert 'shared quadratic PN normals' in reports[0]['insertion_policy']
    original=R.vertex_normals(np.asarray(a['vertices_world_mm']),a['polygons'],range(8),np.asarray(a['corner_normals']))
    # Every child loop at an inserted shared-edge sample uses the same stored
    # direction, while the existing averaged source directions stay unchanged.
    cursor=0;by_vertex={}
    for i,poly in enumerate(b['polygons']):
        for vertex,normal in zip(poly,b['corner_normals'][cursor:cursor+len(poly)]):
            if parents[i]<8:by_vertex.setdefault(vertex,[]).append(normal)
        cursor+=len(poly)
    for split in b['_surface_edge_splits']:
        values=np.asarray(by_vertex[split['vertex_id']])
        np.testing.assert_array_equal(values,np.tile(values[0],(len(values),1)))
    for vertex in range(9):
        np.testing.assert_allclose(by_vertex[vertex],np.tile(original[vertex],(len(by_vertex[vertex]),1)),atol=1e-7)


def test_cancelling_endpoint_normals_refuse_before_nan_geometry():
    source,op=patch();obj=source['objects'][0];cursor=0
    for poly in obj['polygons']:
        for vertex in poly:
            if vertex==1:obj['corner_normals'][cursor]=[1.,0.,0.]
            if vertex==4:obj['corner_normals'][cursor]=[-1.,0.,0.]
            cursor+=1
    saved=copy.deepcopy(source)
    with pytest.raises(M.OwnerMeshError,match='normals cancel'):
        S.state(source,[op])
    assert source==saved


def test_small_selected_triangle_refines_without_a_global_quadratic_fit():
    source,op=triangle_patch();obj=source['objects'][0]
    ids=set(obj['polygons'][0])
    op['scope']['vertex_weights']=[{'vertex_id':i,'weight':1.} for i in sorted(ids)]
    op['scope']['affected_polygon_ids']=[i for i,p in enumerate(obj['polygons']) if ids&set(p)]
    target,reports=S.state(source,[op])
    assert reports[0]['quadric_fit_max_residual_mm'] is None
    assert 'six independent' in reports[0]['quadric_fit_unavailable_reason']
    assert reports[0]['added_vertices']==1
    assert target['objects'][0]['vertices_local'][:18]==obj['vertices_local']
    assert S.verify(source,target,[op])['ok']
    op['params']['relax_interior']=True
    with pytest.raises(M.OwnerMeshError,match='six independent'):
        S.state(source,[op])


def test_native_four_child_refinement_matches_exact_plan(tmp_path):
    from glasses3d.g3d_host import blender
    if blender.blender_exe() is None:pytest.skip('Blender is not installed')
    source,op=triangle_patch();fixture=tmp_path/'fixture.json'
    fixture.write_text(json.dumps({'snapshot':source,'operation':op}))
    result=blender.run(None,Path(__file__).parent/'blender/owner_refine.py',[str(fixture)],timeout_s=90)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'OWNER_PN_NATIVE_OK' in result.stdout,result.stdout+result.stderr
