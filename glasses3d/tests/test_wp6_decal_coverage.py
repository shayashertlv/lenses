"""Independent export controls: normal splitting must not hide decal movement."""
import copy
import numpy as np
import pytest
from glasses3d.tests.proofs import wp6_p0_instrument as I

def scene(move=.4, split=False, reorder=False):
    W=np.array([[10.04,-1.,-1.],[10.04,1.,-1.],[10.04,1.,1.],[10.04,-1.,1.]])
    T=np.array([[0,1,2],[0,2,3]])
    old={'name':'foil','role':'temple','W':W-[move,0,0],'T':T}
    new={'name':'foil','role':'temple','W':W.copy(),'T':T.copy()}
    if split:new.update(W=W[T].reshape(-1,3),T=np.arange(6).reshape(-1,3))
    if reorder:
        order=np.array([2,3,0,1]);new.update(W=W[order],T=np.argsort(order)[T])
    surface=W.copy();surface[:,0]=10.
    out={'temples':{'+x':{'_Vw':surface,'_Tw':T,'_regions':[[0,1]],'_region_of':{},
                         'facets':[{'id':0,'normal':[1.,0.,0.]}]}}}
    return {0:new},{0:old},out

@pytest.mark.parametrize('move',[.4,1.2])
def test_split_export_vertices_do_not_skip_movement(monkeypatch,move):
    monkeypatch.setattr(I,'_nodes',lambda x:x)
    new,old,out=scene(move,split=True)
    r=I._decals(new,old,out)
    assert len(r['decals'])==1
    assert r['decals'][0]['max_move_mm']==pytest.approx(move)
    assert r['pass'] is (move<=1.)

def test_reindexed_vertices_follow_triangle_corners(monkeypatch):
    monkeypatch.setattr(I,'_nodes',lambda x:x)
    new,old,out=scene(.2,reorder=True)
    r=I._decals(new,old,out)
    assert r['pass']
    assert r['decals'][0]['max_move_mm']==pytest.approx(.2)

@pytest.mark.parametrize('failure',['missing_new','missing_reference','topology','unmatched_vertex','stationary_off_surface'])
def test_declared_decals_are_never_silently_omitted(monkeypatch,failure):
    monkeypatch.setattr(I,'_nodes',lambda x:x)
    new,old,out=scene(.2)
    if failure=='missing_new':new={}
    elif failure=='missing_reference':old={}
    elif failure=='topology':new[0]['T']=new[0]['T'][:1]
    elif failure=='unmatched_vertex':
        new[0]['W'][3,1]=100.;old[0]['W'][3,1]=100.
    elif failure=='stationary_off_surface':
        new[0]['W'][:,0]=10.2;old=copy.deepcopy(new)
    r=I._decals(new,old,out,decal_names=['foil'])
    assert not r['pass']
    assert len(r['decals'])==1
    assert r['decals'][0]['name']=='foil' and not r['decals'][0]['pass']
