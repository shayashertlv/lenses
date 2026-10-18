"""OE-02--07/09 regressions on non-axis-aligned, float32 and reflected meshes."""
import copy
import json
from pathlib import Path

import numpy as np
import pytest

from glasses3d.g3d import owner_health as H, owner_mesh as M, owner_surface as S
from glasses3d.tests.test_owner_health import box
from glasses3d.tests.test_owner_edge_radius import fixture


def rotation():
    a,b=.3,.2
    return np.array([[np.cos(b),0,np.sin(b)],[0,1,0],[-np.sin(b),0,np.cos(b)]]) @ np.array(
        [[1,0,0],[0,np.cos(a),-np.sin(a)],[0,np.sin(a),np.cos(a)]])


def snapshot(obj):
    return {'schema':'g3d.mesh_snapshot/1','origin_mm':[0,0,0],'objects':[obj]}


def test_translation_of_float32_sliver_leading_ngons_preserves_authored_normals():
    rng=np.random.default_rng(3)
    for _ in range(2000):
        base=rng.uniform(-60,60,3);r=np.linalg.qr(rng.normal(size=(3,3)))[0]
        points=(np.array([[0,0,0],[1.3,0,0],[2.6,0,0],[1.3,1.5,0]])@r.T+base).astype(np.float32).astype(float)
        n=r@np.array([0.,0.,1.]);normals=[n.tolist()]*4
        obj={'vertices_local':points.tolist(),'polygons':[[0,1,2,3]],'corner_normals':normals}
        after=(points+rng.uniform(-3,3,3)).astype(np.float32).astype(float)
        assert S.transport_normals(obj,after)==normals


def test_rigid_rotation_uses_all_vertices_of_sliver_leading_polygon():
    p=np.array([[0,0,0],[1,0,0],[2,1e-8,0],[2,2,0],[0,2,0]],float)
    n=np.array([.2,.1,1.]);n/=np.linalg.norm(n)
    obj={'vertices_local':p.tolist(),'polygons':[list(range(5))],'corner_normals':[n.tolist()]*5}
    target=(p@rotation().T+[20,10,30]).astype(np.float32).astype(float)
    actual=np.asarray(S.transport_normals(obj,target))
    np.testing.assert_allclose(actual,np.tile(n@rotation().T,(5,1)),atol=2e-6,rtol=0)


@pytest.mark.parametrize('reflected',[False,True])
def test_consistent_normals_is_bit_exact_noop_under_float32_and_reflection(reflected):
    obj,op=box();v=np.asarray(obj['vertices_local'],float)
    obj['corner_normals']=[(v[i]/np.linalg.norm(v[i])).astype(np.float32).astype(float).tolist() for p in obj['polygons'] for i in p]
    obj['polygon_smooth']=[True]*len(obj['polygons'])
    matrix=np.eye(4);matrix[:3,:3]=rotation()@np.diag([-1 if reflected else 1,1,1]);matrix[:3,3]=[60,20,-40]
    obj.update(matrix_world=matrix.tolist(),vertices_world_mm=(v@matrix[:3,:3].T+matrix[:3,3]).tolist())
    op.update(id='normal',kind='consistent_normals',params={'strength':1.})
    target,reports=S.state(snapshot(obj),[op]);after=target['objects'][0]
    assert after['corner_normals']==obj['corner_normals'] and after['polygons']==obj['polygons']
    assert reports[0]['reversed_polygon_ids']==[] and reports[0]['backward_corner_normals']==0
    assert '_surface_sharp_edges' not in after
    assert M.physical(after)['volume_mm3']==pytest.approx(32.)


@pytest.mark.parametrize('authored_float32',[False,True])
def test_tilted_planar_fan_decimates_at_representation_precision(authored_float32):
    obj,op=box();v=np.asarray(obj['vertices_local'],float);r=rotation()
    if authored_float32:
        points=(v@r.T+[60,20,-40]).astype(np.float32).astype(float)
        obj.update(vertices_local=points.tolist(),vertices_world_mm=points.tolist())
    else:
        matrix=np.eye(4);matrix[:3,:3]=r;matrix[:3,3]=[60,20,-40]
        obj.update(matrix_world=matrix.tolist(),vertices_world_mm=(v@r.T+matrix[:3,3]).tolist())
    after,report=H.plan_object(obj,op)
    assert report['removed_interior_vertex_ids']==[8]
    assert after['vertices_local']==obj['vertices_local'][:8]


def test_mirrored_shading_repair_and_fillet_keep_local_outside():
    source,fillet=fixture();obj=source['objects'][0];matrix=np.diag([-1.,1.,1.,1.])
    obj.update(matrix_world=matrix.tolist(),vertices_world_mm=(np.asarray(obj['vertices_local'])@matrix[:3,:3].T).tolist())
    repair=copy.deepcopy(fillet);repair.update(id='repair',kind='repair_shading',params={'strength':1.,'mode':'flat'})
    repaired,_=S.state(source,[repair])
    np.testing.assert_allclose(repaired['objects'][0]['corner_normals'],obj['corner_normals'],atol=1e-15,rtol=0)
    rounded,reports=S.state(source,[fillet])
    local=copy.deepcopy(rounded['objects'][0]);local['vertices_world_mm']=local['vertices_local'];local['matrix_world']=np.eye(4).tolist()
    assert M.physical(local)['volume_mm3']>0
    assert reports[0]['physical']['closed'] and S.verify(source,rounded,[fillet])['ok']


def test_mirrored_nearest_preserves_outward_standoff_sign():
    from glasses3d.g3d import owner_markings
    triangle=np.array([[0.,0.,0.],[-1,0,0],[0,1,0]])
    row=owner_markings.nearest([[-.2,.2,.1]],triangle,[[0,1,2]],orientation=-1)[0]
    assert row[3]==pytest.approx(.1)


@pytest.mark.parametrize('scale,offset',[(1,[0,0,0]),(10,[0,0,0]),(1,[60,20,-40])])
def test_tilted_float32_fillet_has_a_clearance_certificate(scale,offset):
    source,op=fixture();obj=source['objects'][0]
    v=(np.asarray(obj['vertices_local'])*scale@rotation().T+offset).astype(np.float32).astype(float)
    obj.update(vertices_local=v.tolist(),vertices_world_mm=v.tolist())
    obj['corner_normals']=[n.tolist() for n,p in zip(S.face_vectors(v,obj['polygons']),obj['polygons']) for _ in p]
    if scale==10:op['params']['minimum_mm']=15.
    after,reports=S.state(source,[op])
    assert reports[0]['minimum_thickness'] and S.verify(source,after,[op])['ok']


@pytest.mark.parametrize('normal_override',[False,True])
def test_open_lens_material_adjust_and_verification_do_not_require_a_solid(normal_override):
    obj,op=box();obj.update(polygons=[[0,1,2,3]],triangles=[[0,1,2],[0,2,3]],corner_normals=[[0,0,-1]]*4,
        polygon_smooth=[True],polygon_material=[0],material_slot_links=['DATA'],material_names=['Lens'],
        material_definitions=[{'name':'Lens','principled_node':'Principled BSDF','linked_parameters':[],
        'parameters':{'base_color':[1.,1.,1.,1.]},'protected_sha256':'original'}])
    op.update(id='color',kind='material_adjust',params={'strength':1.,'material_index':0,'values':{'base_color':[.5,.5,.5,1.]}})
    op['scope']['affected_polygon_ids']=[0]
    if normal_override:
        obj['unsupported_normal_edit_state']=['Normal-changing modifier Weighted: apply it before editing authored normals']
        obj['evaluated'].update(normals_match_source=False,corner_normals=[[0.,1.,0.]]*4)
    source=snapshot(obj);target,reports=S.state(source,[op])
    assert reports[0]['physical']['geometry_unchanged']
    assert S.verify(source,target,[op])['ok']
    if normal_override:
        target['objects'][0]['evaluated']['corner_normals'][0]=[1.,0.,0.]
        with pytest.raises(M.OwnerMeshError,match='evaluated normals'):
            S.verify(source,target,[op])


def test_evaluated_normal_override_cannot_verify_as_repaired_shading():
    obj,op=box();op.update(id='repair',kind='repair_shading',params={'strength':1.,'mode':'smooth'})
    source=snapshot(obj);target,_=S.state(source,[op])
    target['objects'][0]['evaluated']['normals_match_source']=False
    with pytest.raises(M.OwnerMeshError,match='evaluated normals'):
        S.verify(source,target,[op])


def test_marking_transform_preserves_existing_sink_depth():
    from glasses3d.tests.test_owner_extended import marking_fixture
    from glasses3d.g3d import owner_markings
    source,operation=marking_fixture();mark=source['objects'][1]
    points=np.asarray(mark['vertices_local']);points[:,2]=1.99
    mark['vertices_local']=points.tolist();mark['vertices_world_mm']=points.tolist()
    operation=owner_markings.seal(source,[operation])[0]
    target,reports=S.state(source,[operation])
    np.testing.assert_allclose(np.asarray(target['objects'][1]['vertices_world_mm'])[:,2],1.99,atol=2e-6,rtol=0)
    assert reports[0]['outward_clearance_lift_mm']<2e-6


def test_blender_batch_sequential_parity_and_modifier_refusal(tmp_path):
    from glasses3d.g3d_host import blender
    if blender.blender_exe() is None:pytest.skip('Blender is not installed')
    # Both Python runtimes must author identical float32 vectors even where a
    # fitted component is almost zero. The exact encoding witness depends on it.
    points=np.array([[-1.,-2.,0.],[2.,-2.,0.],[2.,2.,0.],[-1.,2.,0.]])
    obj={'vertices_local':points.tolist(),'polygons':[[0,1,2,3]],'corner_normals':[[.02,0.,.9998]]*4}
    after=points.copy();after[:,2]=points[:,0]*.023
    payload={'object':obj,'after':after.astype(np.float32).astype(float).tolist()}
    path=tmp_path/'normal-parity.json';path.write_text(json.dumps(payload),encoding='utf-8')
    result=blender.run(None,Path(__file__).parent/'blender/owner_geometry_review.py',[str(path)],timeout_s=120)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'OWNER_GEOMETRY_REVIEW_PASSED' in result.stdout,result.stdout+result.stderr
    native=json.loads(next(line.removeprefix('TRANSPORT_PARITY=') for line in result.stdout.splitlines()
                           if line.startswith('TRANSPORT_PARITY=')))
    assert native==S.transport_normals(obj,payload['after'])
