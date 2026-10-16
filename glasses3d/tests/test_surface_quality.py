import numpy as np
import pytest
from glasses3d.g3d.surface_quality import analyse
from glasses3d.g3d.lens_surface import quadratic_lens
from glasses3d.g3d.temple.build import mesh_topology


def test_reports_real_fold_separately_from_normal_seam_and_degenerate():
    v = np.array([[0,0,0],[2,0,0],[0,2,0],[2,2,.5]], float)
    t = np.array([[0,1,2],[1,3,2],[0,0,1]])
    normals = np.tile([0.,0.,1.], (3,3,1)); normals[1] = [1,0,0]
    result = analyse(v,t,normals,facets={"selected face":[0,1]})
    assert result["informational_only"] and "ok" not in result
    assert result["degenerate_triangles"] == 1
    assert result["normal_seam_count"] == 1
    assert result["largest_normal_seams"][0]["jump_deg"] == pytest.approx(90)
    assert 10 < result["largest_normal_seams"][0]["geometric_fold_deg"] < 30
    assert result["facets"]["selected face"]["max_mm"] > .1


@pytest.mark.parametrize('curvature', [0, .001, -.004])
def test_lens_is_closed_continuous_and_has_bounded_chord_error(curvature):
    theta = np.arange(32)*2*np.pi/32
    outline = np.c_[24*np.cos(theta), 17*np.sin(theta)]
    built = quadratic_lens(outline,[1,.01,-.02,curvature,0,curvature],1.8)
    v,t,n = built['vertices_mm'],built['triangles'],built['corner_normals']
    topo = mesh_topology(t.tolist())
    assert not topo['open'] and not topo['dup']
    assert np.einsum('ij,ij->i',v[t[:,0]],np.cross(v[t[:,1]],v[t[:,2]])).sum() > 0
    assert built['max_quadratic_edge_chord_mm'] <= .003
    assert np.all(np.linalg.norm(np.cross(v[t[:,1]]-v[t[:,0]],v[t[:,2]]-v[t[:,0]]),axis=1)>1e-10)
    assert np.allclose(np.linalg.norm(n,axis=2),1)
    # Every optical-front corner at a shared vertex has exactly one analytic normal.
    front = np.all(t<len(v)//2,axis=1)
    seen = {}
    for tri, cn in zip(t[front], n[front]):
        for vid, vector in zip(tri,cn):
            if int(vid) in seen:
                assert np.array_equal(seen[int(vid)], vector)
            seen[int(vid)] = vector
    assert np.array_equal(v[:len(v)//2,:2],v[len(v)//2:,:2])
    assert np.allclose(v[:len(v)//2,2]-v[len(v)//2:,2],1.8)


def test_lens_refuses_self_intersection_and_bad_sampling():
    with pytest.raises(ValueError,match='simple'):
        quadratic_lens([[0,0],[2,2],[0,2],[2,0]], [0]*6, 1)
    with pytest.raises(ValueError,match='positive'):
        quadratic_lens([[0,0],[2,0],[0,2]], [0]*6, 0)


def test_narrow_outline_and_steep_surface_obey_sampling_bounds():
    built = quadratic_lens([[-10,-1],[10,-1],[0,1]], [0,0,0,.1,0,.1],1)
    assert built['max_quadratic_edge_chord_mm'] <= .003
    assert built['max_optical_edge_mm'] <= 1.5
    built = quadratic_lens([[-2,-1],[2,-1],[0,1]], [0,3,-2,0,0,0],1)
    assert built['max_optical_edge_mm'] <= 1.5
