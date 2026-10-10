from dataclasses import replace
import numpy as np
import pytest
from glasses3d.g3d_host.camera import Camera, NormFrame, project_mm
from glasses3d.g3d_host.relief_anchors import fit_anchors


def scene():
    points=np.array([[x,y,z] for x in (-55.,55.) for y in (-15.,12.) for z in (-6.,5.)])
    frame=NormFrame((0,0,-45),160)
    true=Camera(42,19,-2,.21,850,340,250)
    start=replace(true,yaw=42.7,pitch=18.5,roll=-1.8,center_x=342,center_y=249)
    return points,frame,true,start


def solve(points,pixels,frame,start,**kwargs):
    return fit_anchors(points,pixels,np.zeros(len(points),int),frame=frame,camera=start,
        theta={1:0,-1:0},hinge_axis=[60,-8],pivot=np.zeros(3),unit_px=600,**kwargs)


def test_independent_box_exact_camera_and_absent_arm_report():
    points,frame,true,start=scene()
    out=solve(points,project_mm(points,true,frame),frame,start,free_sides=(1,-1),
              probe_world_mm=points,probe_parts=np.zeros(len(points),int))
    assert out['full_rank'] and out['unobserved_sides']==[1,-1]
    assert out['free_sides']==[] and out['theta']=={1:0.,-1:0.}
    np.testing.assert_allclose(list(out['camera'].to_dict().values()),list(true.to_dict().values()),atol=1e-6)
    assert np.asarray(out['probe']['conditional_coordinate_sigma_px']).min()>0


def test_collinear_observations_do_not_get_finite_precision_claim():
    points,frame,true,start=scene()
    line=np.array([[x,0,0] for x in np.linspace(-50,50,10)])
    out=solve(line,project_mm(line,true,frame),frame,start,probe_world_mm=points,probe_parts=np.zeros(len(points),int))
    assert not out['full_rank']
    assert out['conditional_packed_parameter_sigma'] is None
    assert out['probe']['conditional_coordinate_sigma_px'] is None


def test_precision_scales_with_supplied_measurement_noise():
    points,frame,true,start=scene();pixels=project_mm(points,true,frame)
    kwargs={'probe_world_mm':points,'probe_parts':np.zeros(len(points),int)}
    low=solve(points,pixels,frame,start,sigma_px=.5,**kwargs)
    high=solve(points,pixels,frame,start,sigma_px=1.,**kwargs)
    np.testing.assert_allclose(high['probe']['conditional_coordinate_sigma_px'],
        np.array(low['probe']['conditional_coordinate_sigma_px'])*2,rtol=2e-5,atol=1e-7)


def test_invalid_observation_refused():
    points,frame,true,start=scene();pixels=project_mm(points,true,frame);pixels[0,0]=np.nan
    with pytest.raises(ValueError,match='Invalid anchor'):
        solve(points,pixels,frame,start)


def test_full_rank_single_face_exposes_extrapolation_uncertainty():
    points,frame,true,start=scene()
    visible=points[points[:,0]>0]
    out=solve(visible,project_mm(visible,true,frame),frame,start,sigma_px=.5,
              probe_world_mm=points,probe_parts=np.zeros(len(points),int))
    assert out['full_rank']
    sigma=np.array(out['probe']['conditional_coordinate_sigma_px'])
    visible_rms=np.sqrt(np.mean(np.sum(sigma[points[:,0]>0]**2,axis=1)))
    hidden_rms=np.sqrt(np.mean(np.sum(sigma[points[:,0]<0]**2,axis=1)))
    assert hidden_rms>visible_rms*3


def test_fractional_part_identity_is_not_silently_rounded():
    points,frame,true,start=scene()
    with pytest.raises(ValueError,match='Invalid anchor'):
        fit_anchors(points,project_mm(points,true,frame),np.full(len(points),.5),frame=frame,camera=start,
            theta={1:0,-1:0},hinge_axis=[60,-8],pivot=np.zeros(3),unit_px=600)
