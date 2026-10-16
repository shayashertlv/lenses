"""Boundary behaviour of the optional original-vertex relaxation."""
import numpy as np
from glasses3d.g3d import owner_surface as S
from glasses3d.tests.test_owner_surface import patch


def test_relaxation_fades_beside_long_edges_and_explicit_fixed_points():
    # The nearest edge endpoint is five units away, but the fixed segment is
    # only .001 away. Endpoint-only distances would incorrectly give full motion.
    vertices=np.array([[0.,0.],[10.,0.],[10.,10.],[0.,10.],[5.,.001],[5.,5.]])
    values,width=S._relax_falloff(vertices,list(range(6)),[(0,1),(1,2),(2,3),(3,0)],[])
    assert width==1.
    np.testing.assert_array_equal(values[:4],np.zeros(4))
    assert 0<values[4]<.000004
    assert values[5]==1.
    fixed,_=S._relax_falloff(vertices,list(range(6)),[(0,1),(1,2),(2,3),(3,0)],[5])
    assert fixed[5]==0.
    samples,_=S._relax_falloff(vertices,list(range(6)),[(0,1)],[],points=[[5.,.001],[5.,1.]])
    assert samples.shape==(2,) and samples[0]<.000004 and samples[1]==1.


def test_optional_relaxation_protects_narrow_boundary_strip_and_opposite_side():
    source,operation=patch(False);obj=source['objects'][0]
    # The only interior sample is very close to the left boundary in the chart.
    obj['vertices_local'][4][:]=[-1.999,0.,2.03]
    operation['params']['relax_interior']=True
    original=np.asarray(obj['vertices_local']).copy()
    after,reports=S.state(source,[operation]);target=after['objects'][0]
    actual=np.asarray(target['vertices_local'])
    np.testing.assert_array_equal(actual[[*range(4),*range(5,18)]],original[[*range(4),*range(5,18)]])
    assert np.linalg.norm(actual[4]-original[4])<.00001
    weights={r['vertex_id']:r['weight'] for r in reports[0]['relaxation_falloff']['vertex_weights']}
    assert weights[4]<.001
    assert reports[0]['minimum_thickness_preservation']['original_minimum_preserved']
    assert S.verify(source,after,[operation])['ok']
