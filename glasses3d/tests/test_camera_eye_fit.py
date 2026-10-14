"""Actual silhouette fitting with a fixed, independently known off-axis camera."""
from dataclasses import replace
from types import SimpleNamespace
import numpy as np
from scipy.optimize import least_squares

from glasses3d.g3d_host import blender_camera as B, cameras, raster
from glasses3d.g3d_host.camera import Camera, NormFrame, project_mm
from glasses3d.tests.test_cameras_fit import glasses


def test_off_axis_continuation_recovers_geometry_and_retains_legacy_candidate():
    V,F=glasses()
    frame=NormFrame.from_bbox(V)
    truth=Camera(75,12,3,.3,580,400,300,.22,-.12)
    spec=B.to_blender(replace(truth,eye_x=0,eye_y=0),frame,shape_hw=(600,800))
    right,up=B.axes(truth)[:2]
    matrix=np.asarray(spec['matrix_world'])
    matrix[:3,3]+=frame.extent*(truth.eye_x*right+truth.eye_y*up)
    spec['matrix_world']=matrix.tolist()
    pixels=B.project(spec,V)
    initial=cameras.pack(replace(truth,eye_x=0,eye_y=0),800)
    sol=least_squares(lambda x:(project_mm(V,cameras.unpack(x,800),frame)-pixels).ravel(),initial,
                      x_scale='jac',max_nfev=500,ftol=1e-12,xtol=1e-12,gtol=1e-12)
    start=cameras.unpack(sol.x,800)
    scene=raster.get_scene(V,F,frame)
    fg=scene.render(truth,(600,800))['mask']
    front=V[:,2]>=-12
    pivot=frame.to_norm(((V[front].min(0)+V[front].max(0))/2)[None])[0]
    vf=cameras.ViewFit('left',fg,scene,pivot)
    vf.coarse=[(vf.levels['coarse'].loss(scene,start),start,'best_legacy_projection')]
    legacy=vf.refine()
    result=vf.refine_off_axis(legacy)
    record=result['off_axis']
    assert record['legacy_camera']==legacy['camera'].to_dict()
    assert record['selected']=='off_axis' and record['candidate_refusal'] is None
    assert record['common_losses'][1]<record['common_losses'][0]
    assert record['eye_limit']==scene.max_radius
    old=np.sqrt(np.mean(np.sum((project_mm(V,legacy['camera'],frame)-pixels)**2,axis=1)))
    new=np.sqrt(np.mean(np.sum((project_mm(V,result['camera'],frame)-pixels)**2,axis=1)))
    assert new<old*.6, (old,new,record)
    print('Independent actual-fitter point RMS px:',{'legacy':old,'off_axis':new})


def test_invalid_off_axis_candidate_cannot_replace_a_valid_legacy_camera(monkeypatch):
    V,F=glasses()
    frame=NormFrame.from_bbox(V)
    scene=raster.get_scene(V,F,frame)
    valid=Camera(0,60,0,.3,500,320,210)
    fg=scene.render(valid,(420,640))['mask']
    vf=cameras.ViewFit('top',fg,scene,np.zeros(3))
    vf.levels['final']=vf.levels['fit']
    invalid=replace(valid,pitch=-5,eye_y=.5)
    monkeypatch.setattr(vf,'staged',lambda *a,**kw:(invalid,0))
    monkeypatch.setattr(vf,'final_losses',lambda cams:[1,0])
    result=vf.refine_off_axis({'camera':valid,'origin':'fixture'})
    assert result['camera']==valid and result['off_axis']['selected']=='legacy'
    assert result['off_axis']['candidate_camera']==invalid.to_dict()
    assert 'pitch' in result['off_axis']['candidate_refusal']


def test_global_common_grid_retains_legacy_when_local_pair_order_reverses(monkeypatch):
    legacy = Camera(70,10,0,.3,500,320,210)
    candidate = replace(legacy,eye_x=.2)
    other = replace(legacy,yaw=80)
    other_candidate = replace(other,eye_y=.1)
    rows=[]
    for name,old,new in [('left',legacy,candidate),('angled',other,other_candidate)]:
        vf=SimpleNamespace(levels={'final':SimpleNamespace(loss=lambda scene,cam:cam.yaw)},
                           ignore_faces=None,pitch_twin=lambda cam:{'measured_camera':cam.to_dict()})
        evidence={'legacy_camera':old.to_dict(),'candidate_camera':new.to_dict(),
                  'common_losses':[4.,2.],'selected':'off_axis','candidate_refusal':None}
        rows.append({'view':name,'camera':new,'refine':{'off_axis':evidence},'fit':vf})
    def fit(view,*args,**kwargs):
        assert kwargs['sign_margin'] is False
        return rows[0 if view=='left' else 1]
    def common(fg,scene,cams,ignore):
        assert cams==[legacy,candidate,other,other_candidate]
        return [1.,3.,2.,4.]  # same cameras, global grid reverses both local orders
    monkeypatch.setattr(cameras,'fit_view',fit)
    monkeypatch.setattr(cameras,'common_final_losses',common)
    monkeypatch.setattr(cameras,'native_metrics',lambda scene,cam,*args:{'yaw':cam.yaw})
    best,all_rows=cameras.fit_best(np.ones((10,10),bool),None,None,
                                  [('left',None),('angled',None)],off_axis=True)
    assert best['camera']==legacy and best['common_loss']==1
    assert best['refine']['off_axis']['local_selected']=='off_axis'
    assert best['refine']['off_axis']['selected']=='legacy'
    assert best['refine']['off_axis']['common_losses']==[1.,3.]
    assert best['metrics']=={'yaw':70}
    assert best['sign']['measured_camera']==legacy.to_dict()


def test_global_selection_normalizes_axial_roll_and_preserves_offsets(monkeypatch):
    legacy = Camera(0, 70, 170, .3, 500, 320, 210)
    candidate = replace(legacy, roll=195, eye_x=.2, eye_y=-.1)
    evidence = {'legacy_camera': legacy.to_dict(), 'candidate_camera': candidate.to_dict(),
                'common_losses': [4., 2.], 'selected': 'off_axis', 'candidate_refusal': None}
    vf = SimpleNamespace(levels={'final': SimpleNamespace(loss=lambda scene, cam: 2.)},
                         ignore_faces=None, pitch_twin=lambda cam: {'camera': cam.to_dict()})
    row = {'view': 'top', 'camera': replace(candidate, roll=-165),
           'refine': {'off_axis': evidence}, 'fit': vf}
    monkeypatch.setattr(cameras, 'fit_view', lambda *args, **kwargs: row)
    monkeypatch.setattr(cameras, 'common_final_losses', lambda *args: [4., 2.])
    monkeypatch.setattr(cameras, 'native_metrics', lambda scene, cam, *args: {'roll': cam.roll})
    best, _ = cameras.fit_best(np.ones((10, 10), bool), None, None, [('top', None)], off_axis=True)
    expected = replace(candidate, roll=-165)
    assert best['camera'] == best['refine']['camera'] == expected
    assert best['metrics']['roll'] == -165
    assert best['sign']['camera'] == expected.to_dict()
    frame = NormFrame((0, 0, 0), 100)
    points = np.array([[10, 5, 20], [-20, 10, -30]])
    np.testing.assert_allclose(project_mm(points, expected, frame),
                               project_mm(points, candidate, frame), atol=1e-12)
