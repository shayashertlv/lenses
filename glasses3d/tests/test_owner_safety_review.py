"""Review regressions: signed-volume alone cannot certify a local owner move."""
import copy

import numpy as np
import pytest

from glasses3d.g3d import owner_mesh as M, owner_surface as S, owner_thickness as T
from glasses3d.tests.test_owner_health import box


def moved_apex(kind='translate_region', amount=-2.5):
    obj, _ = box()
    obj.update(topology_sha256='topology', positions_sha256='positions')
    obj['triangle_polygons'] = [i for i,p in enumerate(obj['polygons']) for _ in range(len(p)-2)]
    op = dict(id='move', kind=kind, target_ids=['point'], marking_attachments=[],
              scope=dict(object_id=obj['id'], vertex_weights=[dict(vertex_id=8,weight=1.)],
                         fixed_vertex_ids=[], affected_polygon_ids=[5,6,7,8]),
              params=dict(delta_mm=[0,0,amount]))
    if kind=='taper_region':
        op['params']['axis_points_mm']=[[0,0,0],[0,0,1]]
    if kind=='rotate_region':
        op['params']={'axis_points_mm':[[0,1,0],[1,1,0]],'delta_deg':180}
    return {'schema':'g3d.mesh_snapshot/1','origin_mm':[0,0,0],'objects':[obj]},op


@pytest.mark.parametrize('kind',['translate_region','rotate_region','taper_region'])
def test_move_cannot_pierce_generic_wall_even_with_positive_volume(kind):
    source,op=moved_apex(kind)
    obj=source['objects'][0]
    changed,_=M.displaced(np.asarray(obj['vertices_world_mm'],float),op,obj)
    assert M.physical(obj,changed)['volume_mm3']>0
    with pytest.raises(M.OwnerMeshError,match='thickness|cross|graph|solid'):
        M.plan(source,[op])


def test_generic_move_preserves_minimum_and_source():
    source,op=moved_apex(amount=.5);before=copy.deepcopy(source)
    target,reports=S.state(source,[op])
    assert source==before
    assert target['objects'][0]['vertices_world_mm'][8][2]==1.5
    check=reports[0]['minimum_thickness']
    assert check['original_minimum_preserved']
    assert check['unchanged_remainder_proven']


def test_source_certificate_reused_without_retaining_large_mesh_keys(monkeypatch):
    source,op=moved_apex(amount=.5);obj=source['objects'][0]
    T._certificates.clear()
    original=T.minimum;calls=[]
    def measure(*args,**kwargs):
        calls.append(kwargs.get('footprint'))
        return original(*args,**kwargs)
    monkeypatch.setattr(T,'minimum',measure)
    S.state(source,[op]);S.state(source,[op])
    assert sum(f is None for f in calls)==1
    assert len(calls)==3  # one immutable source, two independent edited-region proofs
    assert all(isinstance(k,str) and len(k)==64 for k in T._certificates)


def test_round_with_many_sources_does_not_evict_a_certificate(monkeypatch):
    source,_=moved_apex();obj=source['objects'][0]
    vertices=np.asarray(obj['vertices_world_mm'],float)
    T._certificates.clear();original=T.minimum;calls=[]
    def measure(*args,**kwargs):
        calls.append(1)
        return original(*args,**kwargs)
    monkeypatch.setattr(T,'minimum',measure)
    coordinates=[vertices+[i,0,0] for i in range(9)]
    for points in coordinates:T.source_certificate(points,obj['triangles'])
    T.source_certificate(coordinates[0],obj['triangles'])
    assert len(calls)==len(coordinates)


def test_actual_tessellation_is_in_changed_footprint():
    source,_=moved_apex();obj=source['objects'][0];target=copy.deepcopy(obj)
    coords=np.asarray(obj['vertices_world_mm'])
    assert T.changed_footprint(obj,target,coords,coords) is None
    target['triangles'][0]=target['triangles'][0][::-1]
    assert T.changed_footprint(obj,target,coords,coords) is not None


def test_coincident_triangle_multiplicity_is_in_changed_footprint():
    source,_=moved_apex();obj=source['objects'][0];target=copy.deepcopy(obj)
    target['triangles'].append(target['triangles'][0])
    coords=np.asarray(obj['vertices_world_mm'])
    assert T.changed_footprint(obj,target,coords,coords) is not None


@pytest.mark.parametrize('height',[.999,-1.])
def test_representable_narrow_top_faces_cannot_hide_thinning_or_crossing(height):
    eps=1e-7
    outer=[[-1,-1,1],[1,-1,1],[1,1,1],[-1,1,1]]
    inner=[[-eps,-.5,1],[eps,-.5,1],[eps,.5,1],[-eps,.5,1]]
    vertices=np.asarray(outer+inner+[[0,0,1]]+[[x,y,0] for x,y,z in outer],np.float32).astype(float)
    polygons=[[i,(i+1)%4,4+(i+1)%4,4+i] for i in range(4)]
    polygons += [[4+i,4+(i+1)%4,8] for i in range(4)]
    polygons += [[9,12,11,10]]
    polygons += [[i,9+i,9+(i+1)%4,(i+1)%4] for i in range(4)]
    source=dict(vertices_world_mm=vertices.tolist(),vertices_local=vertices.tolist(),polygons=polygons,
                triangles=[[p[0],p[j],p[j+1]] for p in polygons for j in range(1,len(p)-1)],
                triangle_polygons=[i for i,p in enumerate(polygons) for _ in range(len(p)-2)],
                matrix_world=np.eye(4).tolist(),role='frame')
    target=copy.deepcopy(source);target['vertices_world_mm'][8][2]=height
    target['vertices_local'][8][2]=height
    assert M.physical(target)['volume_mm3']>0
    with pytest.raises(M.OwnerMeshError,match='thickness|cross'):
        S.preserve_minimum_thickness(source,target,[4,5,6,7])


def test_mirrored_physical_orientation_is_local_winding():
    source,_=moved_apex();obj=source['objects'][0]
    matrix=np.eye(4);matrix[0,0]=-1
    obj['matrix_world']=matrix.tolist()
    obj['vertices_world_mm']=(np.asarray(obj['vertices_local'])@matrix[:3,:3].T).tolist()
    assert M.physical(obj)['volume_mm3']==pytest.approx(32)
    obj['polygons']=[p[::-1] for p in obj['polygons']]
    obj['triangles']=[t[::-1] for t in obj['triangles']]
    with pytest.raises(M.OwnerMeshError,match='positive solid'):
        M.physical(obj)


def narrow_bevel(offset, right, width, merged_parent=False):
    xz=[(right-4,0),(right,0),(right,1),(right-width,2),(right-4,2)]
    v=np.asarray([[x+offset,y,z] for y in (-1,1) for x,z in xz],np.float32).astype(float)
    if merged_parent:
        # Same closed geometry, with an actual tessellation of one slightly
        # nonplanar wall n-gon. Polygon grouping cannot waive its positive floor.
        polygons=[list(range(5)),list(range(5,10))[::-1], [0,5,6,1],
                  [1,6,7,8,3,2],[3,8,9,4],[4,9,5,0]]
        top=4
    else:
        polygons=[list(range(5)),list(range(5,10))[::-1]]
        polygons += [[i,i+5,(i+1)%5+5,(i+1)%5] for i in range(5)]
        top=5
    triangles=[];parents=[]
    for i,p in enumerate(polygons):
        rows=([[1,6,7],[1,7,2],[2,7,8],[2,8,3]] if merged_parent and i==3 else
              [[p[0],p[k],p[k+1]] for k in range(1,len(p)-1)])
        triangles.extend(rows);parents.extend([i]*len(rows))
    source=dict(vertices_world_mm=v.tolist(),vertices_local=v.tolist(),polygons=polygons,
                triangles=triangles,triangle_polygons=parents,matrix_world=np.eye(4).tolist(),role='frame')
    target=copy.deepcopy(source)
    for i in (2,7):
        target['vertices_world_mm'][i][2]=target['vertices_local'][i][2]=.5
    return source,target,top


@pytest.mark.parametrize('offset,right,width',[(1e6,4,.125),(0,0,1e-7)])
@pytest.mark.parametrize('preserve_original',[False,True])
@pytest.mark.parametrize('merged_parent',[False,True])
def test_nearvertical_bevel_retains_its_positive_floor(offset,right,width,preserve_original,merged_parent):
    source,target,top=narrow_bevel(offset,right,width,merged_parent)
    center,basis,_=S._chart(np.asarray(source['vertices_world_mm']),source['polygons'],[top])
    before,_=T._minimum((np.asarray(source['vertices_world_mm'])-center)@basis.T,source['triangles'])
    after,_=T._minimum((np.asarray(target['vertices_world_mm'])-center)@basis.T,target['triangles'])
    assert before==1 and after==.5
    assert M.physical(source)['volume_mm3']>0 and M.physical(target)['volume_mm3']>0
    with pytest.raises(M.OwnerMeshError,match='thickness|graph'):
        S.preserve_minimum_thickness(source,target,[top],preserve_original=preserve_original,
                                     minimum_mm=None if preserve_original else .75)


def test_wall_floor_audit_compares_the_same_frozen_chart():
    source,target,_=narrow_bevel(0,0,1e-7)
    faces=[0,1,3,4]
    center,basis,_=S._chart(np.asarray(source['vertices_world_mm']),source['polygons'],faces)
    before,_=T._minimum((np.asarray(source['vertices_world_mm'])-center)@basis.T,source['triangles'])
    after,_=T._minimum((np.asarray(target['vertices_world_mm'])-center)@basis.T,target['triangles'])
    assert before==after==0  # Multi-face SVD chart has an inherited zero rim.
    report=S.preserve_minimum_thickness(source,target,faces)
    assert report['before']['minimum_mm']==report['after']['minimum_mm']==0


@pytest.mark.parametrize('raise_unchanged_component',[False,True])
def test_explicit_floor_above_source_minimum_checks_the_whole_target(raise_unchanged_component):
    base,_=box();points=np.asarray(base['vertices_world_mm'],float)
    left=points.copy();left[:,0]-=5;left[:,2]*=.5
    right=points.copy();right[:,0]+=5
    vertices=np.vstack([left,right])
    polygons=copy.deepcopy(base['polygons'])+[[i+len(points) for i in p] for p in base['polygons']]
    source=dict(vertices_world_mm=vertices.tolist(),vertices_local=vertices.tolist(),polygons=polygons,
                triangles=[[p[0],p[k],p[k+1]] for p in polygons for k in range(1,len(p)-1)],
                triangle_polygons=[i for i,p in enumerate(polygons) for _ in range(len(p)-2)],
                matrix_world=np.eye(4).tolist(),role='frame')
    target=copy.deepcopy(source)
    for i in range(len(points)+4,len(vertices)):
        target['vertices_world_mm'][i][2]+=1;target['vertices_local'][i][2]+=1
    faces=list(range(len(base['polygons'])+5,len(polygons)))
    if raise_unchanged_component:
        for i in range(4,len(points)):
            target['vertices_world_mm'][i][2]+=1;target['vertices_local'][i][2]+=1
        report=S.preserve_minimum_thickness(source,target,faces,preserve_original=False,minimum_mm=1.5)
        assert report['before']['minimum_mm']==1 and report['after']['minimum_mm']==2
        assert report['after']['scope']=='whole_target'
    else:
        # The edited right component reaches 3 mm, but the unedited left one
        # stays at 1 mm. Its source certificate cannot certify a new 1.5-mm floor.
        with pytest.raises(M.OwnerMeshError,match='thickness'):
            S.preserve_minimum_thickness(source,target,faces,preserve_original=False,minimum_mm=1.5)
