import numpy as np
import pytest
from glasses3d.g3d_host.camera import Camera,NormFrame,project_mm
from glasses3d.g3d_host.relief_ridge import lift_pixels_to_plane,intersect_axis_with_image_line


@pytest.mark.parametrize('yaw,pitch,perspective',[(65,20,0),(90,14,.25),(42,28,.4),(-72,-12,.18)])
def test_reference_plane_and_ridge_intersections(yaw,pitch,perspective):
    frame=NormFrame((0.,0.,-50.),180.)
    cam=Camera(yaw,pitch,3.,perspective,900.,360.,240.)
    normal=np.array([1.,.08,.05]);constant=60.
    points=np.array([[0.,-8.,-30.],[0.,14.,-70.],[0.,3.,-90.]])
    points[:,0]=constant-points[:,1: ]@normal[1:]
    pixels=project_mm(points,cam,frame)
    np.testing.assert_allclose(lift_pixels_to_plane(pixels,cam,frame,normal,constant),points,atol=1e-10)
    for base in points:
        target=base+np.array([3.25,0.,0.])
        ends=project_mm(np.array([target-[0.,7.,9.],target+[0.,7.,9.]]),cam,frame)
        got=intersect_axis_with_image_line(base,[1.,0.,0.],*ends,cam,frame)
        np.testing.assert_allclose(got['point_mm'],target,atol=1e-9)
        assert got['axis_distance']==pytest.approx(3.25,abs=1e-9)


def test_unobservable_side_depth_is_refused():
    frame=NormFrame((0.,0.,0.),100.);cam=Camera(90.,0.,0.,0.,500.,250.,250.)
    with pytest.raises(ValueError,match='unobservable'):
        intersect_axis_with_image_line([60.,0.,-30.],[1.,0.,0.],[100.,200.],[400.,200.],cam,frame)
    with pytest.raises(ValueError,match='parallel'):
        lift_pixels_to_plane([[100.,200.]],cam,frame,[0.,1.,0.],10.)


def test_invalid_line_and_plane_are_refused():
    frame=NormFrame((0.,0.,0.),100.);cam=Camera(65.,15.,0.,0.,500.,250.,250.)
    with pytest.raises(ValueError,match='zero length'):
        intersect_axis_with_image_line([60.,0.,-30.],[1.,0.,0.],[100.,200.],[100.,200.],cam,frame)
    with pytest.raises(ValueError,match='nonzero'):
        lift_pixels_to_plane([[100.,200.]],cam,frame,[0.,0.,0.],10.)
