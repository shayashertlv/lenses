"""Independent scope regressions for saved-source fillet sharpening."""
import copy

import pytest
from glasses3d.g3d import owner_surface as S, owner_mesh as M
from glasses3d.tests.test_owner_edge_radius import fixture


def separate_components():
    source,op=fixture();obj=source['objects'][0];base=copy.deepcopy(obj)
    for key in ('vertices_local','vertices_world_mm'):
        obj[key]+=[[p[0]+10,p[1],p[2]] for p in base[key]]
    obj['polygons'] += [[v+8 for v in p] for p in base['polygons']]
    for key in ('corner_normals','polygon_smooth','polygon_material'):
        obj[key]+=copy.deepcopy(base[key])
    obj['triangles']=[[p[0],p[k],p[k+1]] for p in obj['polygons'] for k in range(1,len(p)-1)]
    obj['triangle_polygons']=[i for i,p in enumerate(obj['polygons']) for _ in range(len(p)-2)]
    return source,op


@pytest.mark.parametrize('change',['normals','smooth'])
@pytest.mark.parametrize('radius',[0.,.1])
def test_sharpen_never_restores_unselected_data_from_stale_recipe(change,radius):
    source,op=separate_components();rounded,_=S.state(source,[op]);obj=rounded['objects'][0]
    loop=sum(map(len,obj['polygons'][:6]))
    if change=='normals':obj['corner_normals'][loop]=[.1,0.,-.99498743710662]
    else:obj['polygon_smooth'][6]=True
    before=copy.deepcopy(rounded)
    sharpen=copy.deepcopy(op)
    sharpen.update(id='sharp',params={'strength':1.,'mode':'sharpen','radius_mm':radius,'minimum_mm':1.5})
    ids=list(range(8))+list(range(16,24))
    sharpen['scope']['vertex_weights']=[{'vertex_id':i,'weight':1.} for i in ids]
    sharpen['scope']['affected_polygon_ids']=[i for i,p in enumerate(obj['polygons']) if set(p)&set(ids)]
    assert not set(range(6,12))&set(S.selected(obj,sharpen))
    try:after,_=S.state(rounded,[sharpen])
    except M.OwnerMeshError:
        assert rounded==before
        return
    target=after['objects'][0];newloop=sum(map(len,target['polygons'][:6]))
    assert target['corner_normals'][newloop:newloop+24]==obj['corner_normals'][loop:loop+24]
    assert target['polygon_smooth'][6:12]==obj['polygon_smooth'][6:12]
    assert S.verify(rounded,after,[sharpen])['ok']
