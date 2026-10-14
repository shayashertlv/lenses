"""General off-axis pinhole representation, independent of any product photograph."""
from dataclasses import replace
import itertools
import json
from types import SimpleNamespace

import numpy as np
import pytest
from scipy.optimize import least_squares

from glasses3d.g3d_host import blender_camera as B, cameras, camera_similarity as S, compare as C, pose, raster
from glasses3d.g3d_host.camera import CAMERA_FIELDS, Camera, NormFrame, project_mm
from glasses3d.agent.photo_read import world_matrix

FRAME = NormFrame((13.0, -4.0, -70.0), 150.0)
POINTS = FRAME.to_mm(np.array(list(itertools.product((-.4, 0, .4), (-.3, 0, .3), (-.45, 0, .45)))))
CAM = Camera(60, 20, 7, .3, 900, 640, 480, .22, -.12)


@pytest.mark.parametrize('off_axis', [False, True])
def test_seeing_forwards_and_journals_requested_camera_family(tmp_path, monkeypatch, off_axis):
    from glasses3d.agent import seeing
    calls, records = [], []
    session = SimpleNamespace(root=tmp_path, new_call_id=lambda *a: 'family-test',
                              record_call=lambda *a: records.append(a))
    host = seeing.Seeing(session)
    monkeypatch.setattr(host, 'export_head', lambda _: {'status': 'exported', 'path': 'fixture.glb'})

    def fitter(*args, **kwargs):
        calls.append(kwargs)
        raise RuntimeError('stop after recording host fitter arguments')

    monkeypatch.setattr(seeing.fit_views, 'fit_views', fitter)
    kwargs = {'off_axis': True} if off_axis else {}
    result = host.fit_cameras('camera family test', {'front.png': tmp_path/'front.png'},
                              {'front.png': 'front'}, workers=1, **kwargs)
    assert result['code'] == 'E_FIT' and 'stop after recording' in result['message']
    assert calls[0]['off_axis'] is off_axis
    assert records[-1][2]['off_axis'] is off_axis


def physical_camera(camera=CAM):
    """Independent Blender view-plane fixture: translate the physical eye, with no new projection helper."""
    spec = B.to_blender(replace(camera, eye_x=0, eye_y=0), FRAME, shape_hw=(960, 1280))
    matrix = np.asarray(spec['matrix_world'])
    right, up = B.axes(camera)[:2]
    matrix[:3, 3] += FRAME.extent * (camera.eye_x*right + camera.eye_y*up)
    spec['matrix_world'] = matrix.tolist()
    return spec


@pytest.mark.parametrize('perspective', [0.0, .3])
def test_off_axis_projection_rays_and_blender_keep_one_frame(perspective):
    camera = replace(CAM, perspective=perspective)
    spec = physical_camera(camera)
    expected = B.project(spec, POINTS)
    np.testing.assert_allclose(project_mm(POINTS, camera, FRAME), expected, atol=1e-10)
    np.testing.assert_allclose(raster._project_norm(FRAME.to_norm(POINTS), camera), expected, atol=1e-10)
    np.testing.assert_allclose(B._project_norm(camera, FRAME.to_norm(POINTS)), expected, atol=1e-10)
    np.testing.assert_allclose(B.project(B.to_blender(camera, FRAME, shape_hw=(960,1280)), POINTS), expected, atol=1e-10)
    restored, frame, shape = B.from_blender(spec, frame=FRAME, preserve_frame=True)
    assert frame == FRAME and shape == (960,1280)
    np.testing.assert_allclose(project_mm(POINTS, restored, frame), expected, atol=1e-10)
    origins, directions = raster.camera_rays(camera, expected[:,0], expected[:,1], z0=2)
    delta = FRAME.to_norm(POINTS)-origins
    np.testing.assert_allclose(np.cross(delta,directions), 0, atol=1e-12)
    expected_direction = np.asarray(spec['matrix_world'])[:3,3]-POINTS if perspective else B.axes(camera)[2]
    actual_direction = pose.toward_camera(POINTS, camera, FRAME)
    np.testing.assert_allclose(np.cross(actual_direction,expected_direction), 0, atol=1e-12)


def test_legacy_serialization_projection_and_conversion_stay_exact():
    legacy = replace(CAM, eye_x=0, eye_y=0)
    record = legacy.to_dict()
    assert set(record) == set(CAMERA_FIELDS)
    assert Camera.from_dict(record).to_dict() == record
    assert Camera.from_dict({**record,'eye_x':0,'eye_y':0}).to_dict() == record
    before = project_mm(POINTS, legacy, FRAME)
    np.testing.assert_array_equal(project_mm(POINTS, Camera.from_dict(record), FRAME), before)
    spec = physical_camera()
    camera, frame, _ = B.from_blender(spec, frame=FRAME)
    assert frame != FRAME and camera.eye_x == camera.eye_y == 0
    np.testing.assert_allclose(project_mm(POINTS,camera,frame),B.project(spec,POINTS),atol=1e-10)


def test_print_donor_and_refraction_directions_follow_the_physical_off_axis_eye():
    from glasses3d.g3d_host import texture_utils as T
    from glasses3d.g3d_host.photoprint import fill
    physical_eye = np.asarray(physical_camera()['matrix_world'])[:3,3]
    expected = physical_eye-POINTS
    expected /= np.linalg.norm(expected, axis=1, keepdims=True)
    directions = T.viewing_direction(POINTS,CAM,FRAME)
    np.testing.assert_allclose(directions,expected,atol=1e-12)
    normals = np.tile([0,0,1.],(len(POINTS),1))
    np.testing.assert_allclose(T.viewing_cosine(POINTS,normals,CAM,FRAME),expected[:,2],atol=1e-12)
    # Independent Snell tangent and normal decomposition, then per-ray vectorized equivalence.
    eta = 1/fill.ACETATE_N
    cosine = expected[:,2].clip(0,1)
    refracted = -eta*(expected-cosine[:,None]*normals)-np.sqrt(1-eta**2*(1-cosine**2))[:,None]*normals
    np.testing.assert_allclose(fill._refracted(normals,directions),refracted,atol=2e-12)
    legacy = replace(CAM,eye_x=0,eye_y=0)
    np.testing.assert_array_equal(T.viewing_cosine(POINTS,normals,legacy,FRAME),normals@raster.camera_basis(legacy)[2])


@pytest.mark.parametrize('field', ['eye_x','eye_y'])
@pytest.mark.parametrize('value', [True,float('nan'),float('inf')])
def test_invalid_lateral_camera_evidence_refuses(field,value):
    row={**CAM.to_dict(),field:value}
    with pytest.raises(ValueError): Camera.from_dict(row)
    with pytest.raises(ValueError): C._valid_camera(row,'test')


def test_off_axis_records_crops_similarity_and_triangulation_matrix():
    record=json.loads(json.dumps(CAM.to_dict()))
    assert Camera.from_dict(record)==CAM
    assert C._valid_camera(record,'test')==CAM
    original=project_mm(POINTS,CAM,FRAME)
    crop=cameras.resized_camera(CAM,.6,117,39)
    assert crop==B.crop_camera(CAM,117,39,.6)
    np.testing.assert_allclose(project_mm(POINTS,crop,FRAME),(original-[117,39]+.5)*.6-.5,atol=1e-10)
    matrix=S.similarity_matrix([.8,.2,17,-8])
    transported=S.transport_camera(CAM,matrix)
    assert (transported.eye_x,transported.eye_y)==(CAM.eye_x,CAM.eye_y)
    np.testing.assert_allclose(project_mm(POINTS,transported,FRAME),original@matrix[:,:2].T+matrix[:,2],atol=1e-10)
    basis=SimpleNamespace(frame=FRAME,camera=lambda rid:CAM,pose=lambda rid:None)
    homogeneous=np.c_[POINTS,np.ones(len(POINTS))]@world_matrix(basis,'test').T
    np.testing.assert_allclose(homogeneous[:,:2]/homogeneous[:,2,None],original,atol=1e-10)
    pivot=np.array([.1,-.05,.4])
    vector=cameras.pack(CAM,1100,pivot)
    assert len(vector)==9
    np.testing.assert_allclose(project_mm(POINTS,cameras.unpack(vector,1100,pivot),FRAME),original,atol=1e-10)
    assert len(cameras.pack(replace(CAM,eye_x=0,eye_y=0),1100,pivot))==7
    assert len(cameras.pack(replace(CAM,eye_x=0,eye_y=0),1100,pivot,include_eye=True))==9


def test_lateral_eye_cannot_invert_a_signed_view_label():
    from glasses3d.g3d_host.fit_views import sign_check
    # Optical orientation alone says above; its translated physical eye is below the front pivot.
    camera=Camera(0,5,0,.6,900,640,480,0,-.5)
    assert sign_check('top',camera,pivot_norm=[0,0,0]) is not None
    assert sign_check('top',replace(camera,eye_y=.1),pivot_norm=[0,0,0]) is None
    assert sign_check('top',replace(camera,pitch=-5,eye_y=.5),pivot_norm=[0,0,0]) is not None
    rear = Camera(100,0,0,.6,900,640,480,-.6,0)
    assert sign_check('rear_threequarter_left',rear,pivot_norm=[0,0,0]) is not None
    assert sign_check('rear-threequarter',rear,pivot_norm=[0,0,0]) is not None
    high = Camera(90,89,0,.6,900,640,480,0,.6)
    assert sign_check('top_oblique_left',high,pivot_norm=[0,0,0]) is not None


def test_general_pinhole_needs_lateral_eye_and_recovers_held_out_points():
    """Exact noncoplanar training points cannot hide the legacy family's missing translation."""
    expected=B.project(physical_camera(),POINTS)
    holdout=FRAME.to_mm(np.array(list(itertools.product((-.3,.1,.35),(-.2,.2),(-.3,.15)))))
    expected_holdout=B.project(physical_camera(),holdout)
    original=np.array([CAM.yaw,CAM.pitch,CAM.roll,CAM.perspective,CAM.scale,CAM.center_x,CAM.center_y])
    fits={}
    for extended in (False,True):
        x0=np.r_[original,0,0] if extended else original
        sol=least_squares(lambda x:(project_mm(POINTS,Camera(*x),FRAME)-expected).ravel(),x0,
                          x_scale='jac',max_nfev=500,ftol=1e-12,xtol=1e-12,gtol=1e-12)
        fits[extended]=Camera(*sol.x)
    legacy_error=np.linalg.norm(project_mm(holdout,fits[False],FRAME)-expected_holdout,axis=1)
    assert np.sqrt(np.mean(legacy_error**2))>1.0
    np.testing.assert_allclose(project_mm(POINTS,fits[True],FRAME),expected,atol=1e-8)
    np.testing.assert_allclose(project_mm(holdout,fits[True],FRAME),expected_holdout,atol=1e-8)
