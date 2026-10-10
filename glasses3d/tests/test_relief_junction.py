import numpy as np
import pytest
from glasses3d.g3d_host.relief_junction import triangle_outline_gap


@pytest.mark.parametrize('angle,scale,shift', [(0,1,[0,0]), (73,3.2,[142,-80]), (-110,.4,[-20,300])])
@pytest.mark.parametrize('gap', [-.04,0,.08])
def test_known_truncated_triangle_and_similarity_invariance(angle,scale,shift,gap):
    a,b,c=np.array([[-40,100],[60,100],[10,0]],float)
    line=np.array([[-30,gap*100],[50,gap*100]],float)
    u=np.radians(angle);rot=np.array([[np.cos(u),-np.sin(u)],[np.sin(u),np.cos(u)]])
    transform=lambda p: scale*np.asarray(p)@rot.T+shift
    out=triangle_outline_gap(*transform([a,b,c]),[transform(line)])
    assert out['gap_fraction']==pytest.approx(gap,abs=1e-10)
    assert out['gap_px']==pytest.approx(gap*100*scale,abs=1e-8)


def test_competing_contacts_retained_and_missing_contact_refused():
    out=triangle_outline_gap([0,20],[20,20],[10,0],[[[0,1],[20,1]],[[0,-3],[20,-3]]])
    assert out['all_crossing_fractions']==pytest.approx([.05,-.15])
    with pytest.raises(ValueError,match='No boundary'):
        triangle_outline_gap([0,20],[20,20],[10,0],[[[30,1],[40,1]]])


def test_degenerate_triangle_refused():
    with pytest.raises(ValueError,match='Degenerate'):
        triangle_outline_gap([0,0],[10,0],[5,0],[[[0,1],[10,1]]])
