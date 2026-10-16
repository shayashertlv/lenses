import copy
import numpy as np
import pytest
from glasses3d.g3d import owner_health as H,owner_mesh as M,owner_surface as S


def box():
    v=[[-2,-2,-1],[2,-2,-1],[2,2,-1],[-2,2,-1],[-2,-2,1],[2,-2,1],[2,2,1],[-2,2,1],[0,0,1]]
    p=[[0,3,2,1],[0,1,5,4],[1,2,6,5],[2,3,7,6],[3,0,4,7],[4,5,8],[5,6,8],[6,7,8],[7,4,8]]
    n=S.face_vectors(np.array(v),p)
    obj={'id':'O1','name':'Box','mesh_name':'Box','vertices_local':v,'vertices_world_mm':copy.deepcopy(v),
         'polygons':p,'triangles':[[r[0],r[k],r[k+1]] for r in p for k in range(1,len(r)-1)],
         'corner_normals':[x.tolist() for x,r in zip(n,p) for _ in r],'polygon_smooth':[False]*len(p),
         'polygon_material':[0]*len(p),'uv_layers':[],'attributes':[],'role':'frame','aliases':{'status':'unavailable'},
         'shared_mesh_users':1,'evaluated':{'topology_matches_source':True,'positions_match_source':True}}
    op={'kind':'controlled_decimate','scope':{'object_id':'O1','vertex_weights':[{'vertex_id':i,'weight':1} for i in range(len(v))],
            'fixed_vertex_ids':[],'affected_polygon_ids':list(range(len(p)))},'params':{'strength':1.,'max_removed_vertices':1},'marking_attachments':[]}
    return obj,op


def test_decimation_removes_only_planar_interior_vertex_and_preserves_surface():
    obj,op=box();before=copy.deepcopy(obj);after,r=H.plan_object(obj,op)
    assert obj==before
    assert r['removed_interior_vertex_ids']==[8]
    assert after['vertices_world_mm']==obj['vertices_world_mm'][:8]
    assert after['polygons'][-1]==[4,5,6,7]
    assert r['physical']['volume_mm3']==M.physical(obj)['volume_mm3']
    assert after['_surface_vertex_parents']==list(range(8))


@pytest.mark.parametrize('protected',['curve','sharp','uv','marking'])
def test_decimation_preserves_curve_sharp_edge_uv_or_marking(protected):
    obj,op=box()
    if protected=='curve':obj['vertices_world_mm'][8][2]=1.1;obj['vertices_local'][8][2]=1.1
    if protected=='sharp':obj['edges']=[[4,8]];obj['attributes']=[{'name':'sharp_edge','values':[[True]]}]
    if protected=='uv':
        obj['uv_layers']=[{'name':'UVMap','uv':[[0,0] for _ in obj['corner_normals']]}];obj['uv_layers'][0]['uv'][-1]=[1,0]
    if protected=='marking':op['marking_attachments']=[{'bindings':[{'surface_vertex_ids':[4,5,8]}]}]
    after,r=H.plan_object(obj,op)
    assert r['removed_interior_vertex_ids']==[]
    assert after['polygons']==obj['polygons']


def test_cleanup_merges_exact_duplicates_and_removes_zero_area_face_without_motion():
    obj,op=box();obj['vertices_local'].append(obj['vertices_local'][4].copy());obj['vertices_world_mm'].append(obj['vertices_world_mm'][4].copy())
    obj['polygons'][5][0]=9;obj['polygons'].append([4,9,5]);obj['corner_normals'] += [[0,0,1]]*3
    obj['polygon_smooth'].append(False);obj['polygon_material'].append(0)
    op['kind']='mesh_cleanup';op['params']={'strength':1.};op['scope']['vertex_weights'].append({'vertex_id':9,'weight':1});op['scope']['affected_polygon_ids']=list(range(10))
    after,r=H.plan_object(obj,op)
    assert r['welded_duplicate_vertices']==1 and r['removed_zero_area_polygon_ids']==[9]
    assert len(after['vertices_local'])==9 and after['polygons'][5]==[4,5,8]
    assert r['physical']['closed']


def test_consistent_normals_reverses_wrong_winding_and_keeps_uv_corner_identity():
    obj,op=box();obj['polygons'][1].reverse();obj['uv_layers']=[{'name':'UVMap','uv':[[float(i),0] for i in range(len(obj['corner_normals']))]}]
    op['kind']='consistent_normals';op['params']={'strength':1.}
    after,r=H.plan_object(obj,op)
    assert r['reversed_polygon_ids']==[1]
    assert after['polygons'][1]==[0,1,5,4] and after['uv_layers'][0]['uv'][4:8]==obj['uv_layers'][0]['uv'][4:8][::-1]
    assert after['vertices_local']==obj['vertices_local']
    assert r['physical']['closed']


def test_symmetry_uses_explicit_pairs_and_keeps_source_half_exact():
    obj,op=box();obj['vertices_local'][0][0]=-2.1;obj['vertices_world_mm'][0][0]=-2.1
    op['kind']='symmetrize';op['params']={'strength':1.,'pairs':[[1,0],[2,3],[5,4],[6,7]]}
    after,r=H.plan_object(obj,op)
    assert after['vertices_local'][0]==[-2,-2,-1]
    assert np.array_equal(np.array(after['vertices_local'])[[1,2,5,6]],np.array(obj['vertices_local'])[[1,2,5,6]])
    assert r['physical']['closed']


def test_consistent_normals_preserves_authored_smooth_field_and_flat_split():
    obj,op=box();obj['polygon_smooth']=[True]*len(obj['polygons']);cursor=0
    for p in obj['polygons']:
        for i in p:
            vector=np.asarray(obj['vertices_world_mm'][i],float);vector/=np.linalg.norm(vector)
            obj['corner_normals'][cursor]=vector.tolist();cursor+=1
    # One deliberate hard lower-face split remains flat while adjacent faces use
    # a continuous smooth corner field. Both already point outside.
    obj['corner_normals'][:4]=[[0.,0.,-1.]]*4
    op['kind']='consistent_normals';op['params']={'strength':1.}
    after,r=H.plan_object(obj,op)
    np.testing.assert_allclose(after['corner_normals'],obj['corner_normals'],atol=1e-15,rtol=0)
    assert r['valid_authored_smooth_and_split_normals_preserved']


def test_zero_area_cleanup_does_not_confuse_cancelling_polygon_with_collinearity():
    v=np.array([[0,0,0],[1,1,0],[1,0,0],[0,1,0]])
    assert np.array_equal(H._cross(v,[0,1,2,3]),[0,0,0])
    assert not H._zero_polygon(v,[0,1,2,3])
    assert H._zero_polygon(np.array([[0,0,0],[1,0,0],[2,0,0]]),[0,1,2])


def test_consistent_normals_preserves_smooth_closed_curved_lens_under_object_transform():
    obj,op=box();vertices=[[0.,0.,1.]]
    for theta in (np.pi/4,np.pi/2,3*np.pi/4):
        for phi in np.arange(8)*np.pi/4:
            vertices.append([20*np.sin(theta)*np.cos(phi),12*np.sin(theta)*np.sin(phi),np.cos(theta)])
    vertices.append([0.,0.,-1.]);polys=[]
    for i in range(8):
        nxt=(i+1)%8
        polys += [[0,1+i,1+nxt],[25,17+nxt,17+i]]
        for ring in range(2):
            a=1+ring*8+i;b=1+ring*8+nxt;c=a+8;d=b+8
            polys += [[a,c,d],[a,d,b]]
    vertices=np.asarray(vertices,dtype=np.float32).astype(float)
    normals=vertices/[400,144,1];normals/=np.linalg.norm(normals,axis=1)[:,None]
    matrix=np.array([[0.,-1.,0.,4.],[1.2,0.,0.,6.],[0.,0.,1.,2.],[0.,0.,0.,1.]])
    obj.update(vertices_local=vertices.tolist(),vertices_world_mm=(vertices@matrix[:3,:3].T+matrix[:3,3]).tolist(),
               matrix_world=matrix.tolist(),role='lens',polygons=polys,triangles=polys,
               corner_normals=[normals[i].tolist() for p in polys for i in p],
               polygon_smooth=[True]*len(polys),polygon_material=[0]*len(polys))
    op['kind']='consistent_normals';op['params']={'strength':1.}
    op['scope']['vertex_weights']=[{'vertex_id':i,'weight':1.} for i in range(len(vertices))]
    op['scope']['affected_polygon_ids']=list(range(len(polys)))
    after,report=H.plan_object(obj,op)
    assert report['backward_corner_normals']==0 and report['physical']['closed']
    np.testing.assert_allclose(after['corner_normals'],obj['corner_normals'],atol=1e-15,rtol=0)
    assert after['vertices_local']==obj['vertices_local']
