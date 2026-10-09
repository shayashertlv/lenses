"""Independent analytic image fields for two competing projected top edges."""
import numpy as np
import pytest
from scipy.special import erf
from glasses3d.g3d_host.relief_edges import trace_edge_candidates


@pytest.mark.parametrize('angle',[-32.,-9.,17.,48.])
@pytest.mark.parametrize('reverse',[False,True])
def test_competing_edges_remain_distinct(angle,reverse):
    y,x=np.mgrid[:220,:280];a=np.radians(angle)
    t=np.array([np.cos(a),np.sin(a)]);n=np.array([-t[1],t[0]])
    s=(x-140)*t[0]+(y-110)*t[1];v=(x-140)*n[0]+(y-110)*n[1]
    # Two boundaries exchange contrast along the guide, defeating independent
    # strongest-peak selection. Positions are analytic, not a rendered temple.
    w=np.clip((s+85)/170,0,1)
    L=35+(8+25*w)*.5*(1+erf((v+4)/.7))-(33-25*w)*.5*(1+erf((v-4)/.7))
    ends=[np.array([140,110])-90*t,np.array([140,110])+90*t]
    if reverse:ends.reverse()
    result=trace_edge_candidates(L,*ends)
    assert len(result['candidates'])==2
    offsets=sorted(r['guide_mid_offset_px'] for r in result['candidates'])
    assert offsets==pytest.approx([-4.,4.],abs=.15)
    assert all(abs(r['guide_angle_delta_deg'])<.1 for r in result['candidates'])
    assert all(r['support_fraction']>.9 for r in result['candidates'])


def test_missing_mask_support_is_not_edge_evidence():
    y,x=np.mgrid[:100,:160];L=np.where(y>50,80.,20.)
    mask=np.ones(L.shape,bool);mask[40:60]=False
    result=trace_edge_candidates(L,[20,50],[140,50],valid_mask=mask)
    assert result['candidates']==[]
    assert result['profiles']==0


def test_border_and_flat_field_do_not_invent_candidates():
    L=np.full((100,160),40.)
    assert trace_edge_candidates(L,[20,2],[140,2])['profiles']==0
    assert trace_edge_candidates(L,[20,50],[140,50])['candidates']==[]


@pytest.mark.parametrize('sigma', [.4,1.4,2.])
@pytest.mark.parametrize('guide_error',[-3.,3.])
def test_held_out_blur_and_imperfect_guide(sigma,guide_error):
    y,x=np.mgrid[:180,:260]
    v=y-90-.18*(x-130)
    L=30+24*.5*(1+erf((v+5)/sigma))-20*.5*(1+erf((v-5)/sigma))+.02*x
    p0=[30,72+guide_error];p1=[230,108+guide_error]
    result=trace_edge_candidates(L,p0,p1,half=14.)
    assert len(result['candidates'])==2
    expected=sorted([(-5-guide_error)/np.sqrt(1+.18**2),(5-guide_error)/np.sqrt(1+.18**2)])
    assert sorted(r['guide_mid_offset_px'] for r in result['candidates'])==pytest.approx(expected,abs=.2)
    assert all(r['support_fraction']>.85 for r in result['candidates'])
