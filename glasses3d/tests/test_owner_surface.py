"""Surface operations on independent closed solids, including negative authority cases."""
import copy
import numpy as np
import pytest
from pathlib import Path

from glasses3d.g3d import owner_mesh as M, owner_surface as S
from glasses3d.agent.tools.owner_preview import scaled_operations
from glasses3d.agent.owner_edits import verify_snapshots


def patch(curved=True):
    # A closed square shell with a sampled curved top and flat underside.
    vertices = [[x, y, 2+.08*x*x+.05*y*y if curved else 2.] for y in (-2., 0., 2.) for x in (-2., 0., 2.)]
    vertices += [[x,y,0.] for y in (-2.,0.,2.) for x in (-2.,0.,2.)]
    polys = [[y*3+x,y*3+x+1,(y+1)*3+x+1,(y+1)*3+x] for y in (0,1) for x in (0,1)]
    polys += [[i+9 for i in p[::-1]] for p in polys.copy()]
    rim = [0,1,2,5,8,7,6,3]
    polys += [[b,a,a+9,b+9] for a,b in zip(rim,rim[1:]+rim[:1])]
    normal = S.face_vectors(np.asarray(vertices), polys)
    obj = {'id':'O0001','name':'Quadratic patch','mesh_name':'Patch','role':'frame','side':1,
           'vertices_local':vertices,'vertices_world_mm':vertices,'polygons':polys,
           'triangles':[[p[0],p[k],p[k+1]] for p in polys for k in range(1,len(p)-1)],
           'corner_normals':[n.tolist() for n,p in zip(normal,polys) for _ in p],
           'polygon_smooth':[True]*len(polys),'polygon_material':[0]*len(polys),'attributes':[],
           'uv_layers':[{'name':'UVMap','uv':[[0.,0.],[1.,0.],[1.,1.],[0.,1.]]*len(polys)}],
           'topology_sha256':'topology','positions_sha256':'positions','shared_mesh_users':1,
           'aliases':{'status':'unavailable'},'evaluated':{'topology_matches_source':True,'positions_match_source':True}}
    snapshot = {'schema':'g3d.mesh_snapshot/1','origin_mm':[0,0,0],'objects':[obj]}
    scope = {'object_id':obj['id'], 'vertex_weights':[{'vertex_id':i,'weight':1.} for i in range(9)],
             'fixed_vertex_ids':[], 'affected_polygon_ids':[i for i,p in enumerate(polys) if set(p)&set(range(9))]}
    operation = {'id':'surface','kind':'refine_curved_surface','target_ids':['F1'],'scope':scope,
                 'params':{'strength':1.},'marking_attachments':[]}
    assert M.physical(obj)['closed']
    return snapshot, operation


def test_refine_inserts_only_inside_faces_keeps_outline_uv_and_opposite_surface():
    source, op = patch()
    before = copy.deepcopy(source)
    after, reports = S.state(source,[op])
    a,b = source['objects'][0],after['objects'][0]
    assert source == before
    assert len(b['vertices_local']) == len(a['vertices_local'])+8
    assert b['vertices_local'][:18] == a['vertices_local']
    assert b['polygons'][24:] == a['polygons'][4:]
    assert reports[0]['split_internal_edges']==4
    assert reports[0]['boundary_edges_unchanged']
    # Shared source edges are replaced by common curved midpoints. Their two
    # neighbours remain welded while the outer boundary remains identical.
    edges={tuple(sorted((a,b))) for p in b['polygons'][:24] for a,b in zip(p,p[1:]+p[:1])}
    assert (1,4) not in edges and (0,1) in edges
    assert M.physical(b)['closed']
    assert reports[0]['quadric_fit_max_residual_mm'] < 1e-12
    assert verify_snapshots(source,after,[op])['ok']
    hostile = copy.deepcopy(after); hostile['objects'][0]['vertices_local'][9][2] += .1
    with pytest.raises(M.OwnerMeshError,match='confirmed surface'):
        verify_snapshots(source,hostile,[op])


def test_refine_preserves_original_boundary_uv_loops():
    source,op = patch()
    after,_ = S.state(source,[op])
    uv = after['objects'][0]['uv_layers'][0]['uv']
    assert uv[:3] == [[0,0],[1,0],[.5,.5]]
    hostile=copy.deepcopy(after); hostile['objects'][0]['uv_layers'][0]['uv'][0]=[.1,.1]
    with pytest.raises(M.OwnerMeshError,match='UVs'):
        verify_snapshots(source,hostile,[op])


def test_refine_nonquadratic_samples_cannot_inject_remote_fit_error_into_local_edges():
    # A distant sample must not perturb an edge whose endpoints/tangents did not
    # change. Absolute projection and global-curvature insertion both did this.
    source,op=patch();obj=source['objects'][0]
    baseline,_=S.plan_object(obj,op)
    obj['vertices_local'][0][2]+=.25
    vertices=np.asarray(obj['vertices_world_mm'])
    _,_,residual=S._quadratic(vertices,obj['polygons'],[0,1,2,3])
    assert residual>.05
    target,report=S.plan_object(obj,op)
    actual=np.asarray(target['vertices_world_mm'])
    np.testing.assert_array_equal(actual[:len(vertices)],vertices)
    row=next(row for row in target['_surface_edge_splits'] if row['edge']==[4,5])
    assert target['vertices_local'][row['vertex_id']]==baseline['vertices_local'][row['vertex_id']]
    from glasses3d.g3d.surface_quality import analyse
    triangles=np.asarray(target['polygons'][:24])
    measured=analyse(actual,triangles)['adjacent_geometric_normal_angle_deg']
    # This fixed nonquadratic regression has a 24.88-degree source crease.
    assert measured['max']<18.
    assert M.physical(target)['closed']


def test_flatten_locked_nonplanar_boundary_refuses_instead_of_moving_it():
    source,op=patch();op['kind']='flatten_face'
    with pytest.raises(M.OwnerMeshError,match='nonplanar'):
        S.state(source,[op])
    op['params']['keep_boundary']=False
    after,reports=S.state(source,[op])
    assert reports[0]['boundary_movement_explicitly_selected']
    assert reports[0]['maximum_residual_after_mm'] < 2e-5
    assert after['objects'][0]['vertices_local'][9:] == source['objects'][0]['vertices_local'][9:]


def test_flatten_with_planar_border_removes_only_the_interior_bump():
    source,op=patch(False);source['objects'][0]['vertices_local'][4][2]=2.3
    # lists are shared in this deliberate synthetic fixture
    op['kind']='flatten_face'
    after,reports=S.state(source,[op])
    assert after['objects'][0]['vertices_local'][4][2] == pytest.approx(2.)
    assert reports[0]['maximum_residual_before_mm'] == pytest.approx(.3)
    assert reports[0]['maximum_residual_after_mm'] == pytest.approx(0)


def test_shading_only_geometry_is_exact_and_partial_amounts_are_distinct():
    source,op=patch();op['kind']='repair_shading';op['params']={'strength':1.,'mode':'smooth'}
    before=copy.deepcopy(source)
    after,reports=S.state(source,[op])
    assert source==before
    assert after['objects'][0]['vertices_local']==source['objects'][0]['vertices_local']
    assert after['objects'][0]['polygons']==source['objects'][0]['polygons']
    assert reports[0]['geometry_unchanged']
    half=scaled_operations([op],.5)
    assert S.signature(S.state(source,half)[0]) != S.signature(after)
    assert verify_snapshots(source,after,[op])['ok']
    hostile=copy.deepcopy(after);hostile['objects'][0]['corner_normals'][-1]=[1,0,0]
    with pytest.raises(M.OwnerMeshError,match='shading'):
        verify_snapshots(source,hostile,[op])


@pytest.mark.parametrize('bad',['unknown_attribute','temple_chart','partial_face','strength','invalid_mode'])
def test_surface_refuses_unsupported_or_incomplete_selections(bad):
    source,op=patch()
    if bad=='unknown_attribute':source['objects'][0]['attributes']=[{'name':'arbitrary_weight'}]
    if bad=='temple_chart':source['objects'][0]['aliases']['status']='verified_topology'
    if bad=='partial_face':
        for row in op['scope']['vertex_weights']:row['weight']=.5
    if bad=='strength':op['params']['strength']=1.1
    if bad=='invalid_mode':op.update(kind='repair_shading',params={'strength':1.,'mode':'magic'})
    with pytest.raises(M.OwnerMeshError):S.state(source,[op])


def test_surface_changes_cannot_authorize_other_objects_or_materials():
    source,op=patch();source['objects'][0]['material_sha256']='unchanged'
    after,_=S.state(source,[op]);after['objects'][0]['material_sha256']='new'
    with pytest.raises(M.OwnerMeshError,match='material'):
        verify_snapshots(source,after,[op])


def test_shading_only_result_needs_selected_rendered_preview_authority():
    from glasses3d.agent.owner_edits import confirmed_transition
    source,op=patch();op.update(kind='repair_shading',params={'strength':1.,'mode':'smooth'})
    after,_=S.state(source,[op]);after['fingerprint']='after'
    sealed={'snapshot':source,'all_operations':[op],'source_fingerprint':'before','receipts':[]}
    with pytest.raises(M.OwnerMeshError,match='rendered preview'):
        confirmed_transition(sealed,after,'before')


def test_refinement_cannot_cross_opposite_side_despite_positive_total_volume():
    source,op=patch();obj=source['objects'][0];obj['role']='lens'
    for i in range(9):obj['vertices_local'][i+9][2]=obj['vertices_local'][i][2]-.15
    assert M.physical(obj)['volume_mm3'] > 0
    with pytest.raises(M.OwnerMeshError,match='graph thickness'):
        S.state(source,[op])


def test_flatten_cannot_thin_a_shell_below_its_original_minimum():
    source,op=patch();obj=source['objects'][0];obj['role']='lens'
    for i in range(9):obj['vertices_local'][i+9][2]=obj['vertices_local'][i][2]-.05
    op.update(kind='flatten_face',params={'strength':1.,'keep_boundary':False})
    with pytest.raises(M.OwnerMeshError,match='graph thickness'):
        S.state(source,[op])


def test_blender_surface_operations_keep_actual_scope(tmp_path):
    from glasses3d.g3d_host import blender
    if blender.blender_exe() is None:pytest.skip('Blender is not installed')
    result=blender.run(None,Path(__file__).parent/'blender/owner_surface.py',[],timeout_s=120)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'OWNER_SURFACE_RESULT' in result.stdout,result.stdout+result.stderr
