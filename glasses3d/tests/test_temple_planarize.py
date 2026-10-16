import numpy as np
import pytest
from glasses3d.g3d.temple.planarize import fit_group, fit_chart_group


def case():
    v=np.array([[0,0,0],[1,0,.2],[2,0,0],[0,1,0],[1,1,.5],[2,1,0],[0,2,0],[1,2,.1],[2,2,0]],float)
    f=np.array([[0,1,4],[0,4,3],[3,4,7],[3,7,6],[1,2,5],[1,5,4],[4,5,8],[4,8,7]])
    return v,f,{'left':[0,1,2,3],'right':[4,5,6,7]}


def test_joint_fit_flattens_each_face_with_shared_edges():
    v,f,g=case();out,report=fit_group(v,f,g,axis=2,keep_boundary=False)
    np.testing.assert_array_equal(out[:,:2],v[:,:2])
    assert report['max_move_mm']>0
    assert all(r['max_residual_mm']<1e-10 for r in report['faces'].values())
    for row in report['faces'].values():
        assert row['border_max_move_mm']>0


def test_bent_fixed_border_is_refused_instead_of_faking_flatness():
    v,f,g=case()
    with pytest.raises(ValueError,match='retaining them and flattening is impossible'):
        fit_group(v,f,g,axis=2)


def test_unselected_vertices_stay_exact_and_geometry_input_is_unchanged():
    v,f,g=case();before=v.copy()
    out,_=fit_group(v,f,{'left':g['left']},axis=2,keep_boundary=False)
    np.testing.assert_array_equal(v,before)
    np.testing.assert_array_equal(out[[2,5,8]],before[[2,5,8]])


def test_overlapping_face_labels_are_refused():
    v,f,g=case()
    with pytest.raises(ValueError,match='two face labels'):
        fit_group(v,f,{'left':[0,1],'right':[1,2]},axis=2)


def test_free_fit_keeps_facet_normals_and_intersects_shared_vertices():
    v,f,g=case();before=v.copy()
    out,report=fit_group(v,f,g,axis=None,keep_boundary=False)
    np.testing.assert_array_equal(v,before)
    assert report['max_move_mm']>0
    assert all(row['normal_change_deg']==0 and row['max_residual_mm']<1e-10 for row in report['faces'].values())
    with pytest.raises(ValueError,match='retaining them and flattening is impossible'):
        fit_group(v,f,g,axis=None)


def test_chart_fit_keeps_joint_corners_while_fitting_bent_actual_faces():
    v,f,g=case()
    footprints={'left':{'A':[0,0],'B':[1,0],'C':[1,2],'D':[0,2]},
                'right':{'B':[1,0],'E':[2,0],'F':[2,2],'C':[1,2]}}
    planes,report=fit_chart_group(v,f,g,footprints)
    for xy in [[1,0],[1,2]]:
        assert np.r_[1,xy]@planes['left']==pytest.approx(np.r_[1,xy]@planes['right'],abs=1e-10)
    assert all(row['fit_max_move_mm']>0 for row in report['faces'].values())
    assert report['informational_only'] is True
    footprints['right']['B']=[1.1,0]
    with pytest.raises(ValueError,match='inconsistent coordinates'):
        fit_chart_group(v,f,g,footprints)
