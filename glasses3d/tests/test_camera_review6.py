"""Review task 6 controls, independent synthetic photos and unchanged physical constraints."""
from dataclasses import replace
import tracemalloc

import numpy as np
from PIL import Image
import pytest

from glasses3d.g3d_host import hinge_fit, matte, raster
from glasses3d.tests.fixtures.hinge_fit_known import box, pack, truth_pose


def thin_case(view, truth=0., start=8., width=4.):
    mesh, roles=pack(box((-75,75),(-12,12),(-4,4)),
                     box((71-width/2,71+width/2),(-3,3),(-150,-4)),
                     box((-71-width/2,-71+width/2),(-3,3),(-150,-4)))
    axes={'+1':[71.,-4.],'-1':[-71.,-4.]}
    yaw,pitch={'front':(0.,3.),'back':(180.,5.),'top':(0.,60.)}[view]
    shape=(360,480)
    cam=raster.view_camera(mesh.frame,yaw,pitch,2.4,shape,perspective=.1)
    fg=truth_pose(mesh,axes,dict.fromkeys(axes,truth)).scene().render(cam,shape)['mask']
    return mesh,roles,axes,cam,fg,dict.fromkeys(axes,start)


@pytest.mark.parametrize('view', ['front','back','top'])
@pytest.mark.parametrize('truth,start',[(0.,8.),(6.,12.),(6.,0.)])
def test_physical_openings_recover_photo_even_when_authored_arm_is_hidden(view,truth,start):
    mesh,roles,axes,cam,fg,initial=thin_case(view,truth,start)
    out=hinge_fit.refine(mesh,roles,axes,fg,cam,view,mesh.front_pivot(),initial_theta=initial,fit_camera=False)
    mask=out['mesh'].scene().render(out['camera'],fg.shape)['mask']
    assert (mask&fg).sum()/max(1,(mask|fg).sum()) > .99
    for key,angle in out['theta_deg'].items():
        assert -90 <= angle <= 90
        if view == 'front' and truth == 0.:
            # Every fully hidden angle ties: choose the closest edge of that range to the input.
            assert 0. < angle < start and not out['theta_observed'][key]
        else:
            assert abs(angle-truth) < 2., (view,truth,start,out['theta_deg'])
        if out['opening_fit']['per_side'][key]['photo_supported_pixels']==0:
            assert not out['theta_observed'][key]


def test_thin_top_arm_is_not_aliased():
    mesh,roles,axes,cam,fg,initial=thin_case('top',1.3,8.,width=1.)
    out=hinge_fit.refine(mesh,roles,axes,fg,cam,'top',mesh.front_pivot(),initial_theta=initial,fit_camera=False)
    assert all(abs(v-1.3)<.4 for v in out['theta_deg'].values())


def test_thin_top_arm_joint_fit_keeps_final_resolution_evidence():
    mesh,roles,axes,cam,fg,initial=thin_case('top',1.3,8.,width=1.)
    out=hinge_fit.refine(mesh,roles,axes,fg,cam,'top',mesh.front_pivot(),initial_theta=initial)
    mask=out['mesh'].scene().render(out['camera'],fg.shape)['mask']
    assert (mask&fg).sum()/max(1,(mask|fg).sum()) > .99
    assert all(abs(v-1.3)<.4 for v in out['theta_deg'].values())


def test_alpha_cutout_uses_the_same_white_background_as_intake(tmp_path):
    pixels=np.zeros((60,80,4),np.uint8)
    pixels[20:40,20:60]=[10,20,30,255]
    path=tmp_path/'cutout.png'
    Image.fromarray(pixels).save(path)
    photo=matte.load_photo(path)
    assert np.all(photo[0,0]==255)
    assert np.array_equal(photo[30,30],[10,20,30])
    assert matte.matte(photo)[30,30]


def test_background_memory_has_no_dense_image_design_matrix():
    image=np.full((1000,1500,3),240,np.uint8)
    image[400:600,200:1300]=20
    tracemalloc.start()
    result=matte.matte(image)
    _,peak=tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert result[500,700]
    assert peak/image.shape[0]/image.shape[1] < 75


@pytest.mark.parametrize('k',[1e-8,1e-7,1e-6,1e-5,1e-4])
def test_near_orthographic_float32_camera_projection(k):
    from glasses3d.g3d_host import blender_camera as BC
    from glasses3d.g3d_host.ar_camera import explicit_camera
    from glasses3d.g3d_host.camera import Camera,NormFrame,project_mm
    frame=NormFrame((0.,20.,-70.),150.)
    cam=Camera(35.,15.,4.,k,1500.,1000.,750.)
    points=frame.to_mm(np.array([[x,y,z] for x in (-.45,0,.45) for y in (-.3,.3) for z in (-.4,0,.4)]))
    spec=BC.to_blender(cam,frame,shape_hw=(1500,2000))
    spec['matrix_world']=np.asarray(spec['matrix_world'],np.float32).astype(float).tolist()
    assert np.max(np.abs(BC.project(spec,points)-project_mm(points,cam,frame))) < .3
    ar=explicit_camera(cam,frame,(1500,2000),name='test',background='#ffffff',size_px=2000)
    view=np.asarray(ar['view'],np.float32).reshape(4,4).astype(float)
    xyz=points/1000 @ view[:3,:3].T+view[:3,3]
    uv=np.column_stack([ar['cx']+ar['fx']*xyz[:,0]/-xyz[:,2], ar['cy']-ar['fy']*xyz[:,1]/-xyz[:,2]])
    assert np.max(np.abs(uv-project_mm(points,cam,frame))) < .3


def test_memory_pool_cap_reads_free_commit(monkeypatch):
    from glasses3d.g3d_host import resources
    monkeypatch.setattr(resources,'free_commit_bytes',lambda:int(6*2**30))
    monkeypatch.setattr(resources.os,'cpu_count',lambda:32)
    assert resources.fit_workers(14)==3


def test_fit_pool_uses_measured_budget_even_for_an_explicit_worker_request(monkeypatch):
    from glasses3d.g3d_host import fit_views as FV
    seen=[]
    class Pool:
        def __init__(self,*,max_workers): seen.append(max_workers)
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def map(self,fn,jobs): return list(jobs)
    monkeypatch.setattr(FV,'fit_workers',lambda requested:3,raising=False)
    monkeypatch.setattr(FV,'ProcessPoolExecutor',Pool)
    assert FV._map(list(range(8)),14)==list(range(8))
    assert seen==[3]


def test_historical_product_constants_live_only_in_test_fixtures():
    from glasses3d.g3d_host import fit_views,fitmesh
    from glasses3d.tests.fixtures import camera_products
    assert not hasattr(fit_views,'PRODUCTS')
    assert not hasattr(fitmesh,'PROTOTYPE_SKIP')
    assert len(camera_products.PRODUCTS)==2


def test_articulated_compare_reproduces_frozen_metrics(tmp_path,monkeypatch):
    from glasses3d.tests.test_hinge_fit_evidence import make_basis
    from glasses3d.g3d_host import compare
    basis,row,mesh,_=make_basis(tmp_path,monkeypatch)
    metrics,_=compare.reference_metrics(mesh,basis,'photo')
    assert metrics['all']['iou']==row['iou']==1.


def test_compare_reports_optional_clear_lens_metrics():
    from types import SimpleNamespace
    from glasses3d.g3d_host import compare
    mesh,_,_,cam,_,_=thin_case('top')
    mesh=replace(mesh,lens_parts=[True,False,False])
    shape=(360,480)
    foreground=raster.get_scene(mesh.V,mesh.F[mesh.part!=0],mesh.frame).render(cam,shape)['mask']
    basis=SimpleNamespace(frame=mesh.frame,pivot_mm=np.zeros(3),front_parts=None,
        camera=lambda _:cam,shape=lambda _:shape,matte=lambda _:foreground,pose=lambda _:None,
        roi=lambda _:(0,0,480,360),refs={'photo':{'image':'photo.png',
            'lens_loss':{'mode':'optional','optional_nodes':[mesh.names[0]]}}})
    metrics,_=compare.reference_metrics(mesh,basis,'photo')
    assert metrics['all']['iou_lens_optional']==1.
    assert metrics['all']['iou'] < .8


def test_comparison_crop_and_blender_request_carry_the_frozen_opening(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from glasses3d.tests.test_hinge_fit_evidence import make_basis
    from glasses3d.agent.seeing import Seeing
    basis,_,mesh,_=make_basis(tmp_path,monkeypatch)
    sent=[]
    session=SimpleNamespace(root=tmp_path,call_read=lambda *args,**kwargs:sent.append(args[2]) or
        {'status':'ok','result':{'views':[]}})
    seeing=Seeing(session)
    monkeypatch.setattr(seeing,'_light_frame',lambda _:None)
    crop=seeing._crop_view(basis,mesh,{'ref':'photo','box_px':[0,0,300,200]},1,np.zeros(3),['native'])
    assert crop['view']['pose']==basis.pose('photo')
    seeing._render('compare_to_photos','test',[crop['view']],tmp_path/'renders')
    assert sent[0]['views'][0]['pose']==basis.pose('photo')


def test_domain_rejects_front_lens_crossings_even_at_authored_zero():
    from glasses3d.g3d_host.hinge_domain import OpeningDomain
    from glasses3d.tests.fixtures.hinge_fit_known import combine
    front = combine([box((-75,75),(-12,12),(-4,4)), box((45,55),(-8,8),(-65,-55))])
    mesh,roles = pack(front,box((69,73),(-3,3),(-150,-4)),box((-73,-69),(-3,3),(-150,-4)))
    axes={'+1':[71.,-4.],'-1':[-71.,-4.]}
    domain=OpeningDomain(mesh,roles,axes)
    assert domain.valid_side('+1',0.)
    assert not domain.valid_side('+1',-20.)
    assert not domain.valid_side('+1',-55.)  # clear endpoint, but reaching it crosses the lens
    assert not domain.valid_side('+1',150.)
    crossing=truth_pose(mesh,axes,{'+1':-20.,'-1':0.})
    assert not OpeningDomain(crossing,roles,axes).valid_side('+1',0.)


def test_off_axis_opening_box_uses_the_posed_radius(monkeypatch):
    mesh,roles,axes,cam,_,_=thin_case('top')
    theta=dict.fromkeys(axes,30.)
    posed=truth_pose(mesh,axes,theta)
    fg=posed.scene().render(cam,(360,480))['mask']
    monkeypatch.setattr(hinge_fit,'_angle_search',lambda *args:(30.,{'input_deg':30.,'visible_pixels_initial':1,
        'theta_deg':30.,'observed':False,'evaluations':0,'status':'flat_whole_scene_objective'}))
    result=hinge_fit.refine(mesh,roles,axes,fg,cam,'top',mesh.front_pivot(),initial_theta=theta,
                            off_axis=True,fit_camera=False)
    assert posed.scene().max_radius > mesh.scene().max_radius
    assert result['opening_fit']['camera_box'][-1][1] == pytest.approx(posed.scene().max_radius)


@pytest.mark.parametrize('k',[1e-9,1e-8,1e-6,1e-4])
def test_near_orthographic_projected_silhouettes_agree(k):
    from PIL import ImageDraw
    from glasses3d.g3d_host import blender_camera as BC
    from glasses3d.g3d_host.ar_camera import explicit_camera
    from glasses3d.g3d_host.camera import project_mm
    mesh,_,_,cam,_,_=thin_case('top')
    cam=replace(cam,perspective=k)
    shape=(360,480)
    truth=project_mm(mesh.V,cam,mesh.frame)
    spec=BC.to_blender(cam,mesh.frame,shape_hw=shape)
    spec['matrix_world']=np.asarray(spec['matrix_world'],np.float32).tolist()
    ar=explicit_camera(cam,mesh.frame,shape,name='near-ortho',background='#fff',size_px=480)
    view=np.asarray(ar['view'],np.float32).reshape(4,4).astype(float)
    xyz=mesh.V/1000 @ view[:3,:3].T+view[:3,3]
    ar_uv=np.column_stack([ar['cx']+ar['fx']*xyz[:,0]/-xyz[:,2],ar['cy']-ar['fy']*xyz[:,1]/-xyz[:,2]])
    def silhouette(uv):
        im=Image.new('1',shape[::-1]); draw=ImageDraw.Draw(im)
        for face in mesh.F:
            draw.polygon([tuple(p) for p in uv[face]],fill=1)
        return np.asarray(im)
    expected=silhouette(truth)
    for uv in (BC.project(spec,mesh.V),ar_uv):
        actual=silhouette(uv)
        assert (expected & actual).sum()/(expected | actual).sum() > .998


def test_lab_reading_widens_unmeasured_opening_and_retains_transport_lineage():
    from glasses3d.tests.agent.test_temple_readings import SynthBasis,corner_picks,point,FX,build_temple
    from glasses3d.agent.temple_lab import readings as R
    tb,bs=build_temple(FX.prada_default()),SynthBasis()
    pk=corner_picks(bs,point(tb,80.,2.,5.),['05','02','03'])
    measured=R.read_point(tb,bs,'P',pk,kind='corner')
    bs.refs['reference-02']['theta_observed']['+1']=False
    unmeasured=R.read_point(tb,bs,'P',pk,kind='corner')
    assert unmeasured['sd_X_camera'] > 10*measured['sd_X_camera']
    assert R.opening_uncertainty(bs,'black-angled',1)['sd_deg']==10.
    assert R.opening_uncertainty(bs,'reference-02',-1)['sd_deg']==.3
    assert 'opening_unmeasured' in R.reading_text(unmeasured)
    bs.refs['reference-03']['pose_provenance']['opening_calibrated']=False
    assert R.opening_uncertainty(bs,'reference-03',1)['sd_deg']==10.


def test_main_photo_read_uses_each_photos_opening_provenance(tmp_path):
    from glasses3d.tests.agent.test_photo_read import Basis,picks
    from glasses3d.agent import photo_read as P
    bs=Basis(tmp_path)
    for rid in bs.refs:
        bs.poses[rid]={'+1':{'theta_deg':5.,'axis_xz_mm':[65.,-18.]}}
        bs.refs[rid].update(theta_observed={'+1':True},pose_provenance={'opening_calibrated':True})
    pk=picks(bs,[52.,-4.,-145.],side=1)
    observed=P.read_point(bs,'tip',pk,temple_side=1)
    bs.refs['reference-b']['theta_observed']['+1']=False
    unmeasured=P.read_point(bs,'tip',pk,temple_side=1)
    assert unmeasured['angular_sd_deg']['reference-b']==10.
    assert unmeasured['angular_sd_deg']['reference-a']==.3
    assert np.trace(unmeasured['cov_angular_mm2']) > 10*np.trace(observed['cov_angular_mm2'])


def test_glossy_full_rim_keeps_observed_lens_pixels_but_allows_clear_interiors(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from glasses3d.tests.fixtures import seat_fixtures as SF
    from glasses3d.tests.test_cameras_fit import ring
    from glasses3d.tests.test_fit_views_synthetic import lens
    from glasses3d.g3d_host import fit_views as FV,fitmesh,cameras
    nodes=[]
    for sign in (-1,1):
        for name,role,geometry in [('Rim','frame',ring(sign*32,0,26,19,4,4)),
                                    ('Lens','lens',lens(sign*32,0,24,17))]:
            nodes.append({'name':f'{name}{sign}', 'role':role, 'mesh':SF.Mesh(*geometry),
                          'extras':{'g3d_seat':{'frame_type':'full_rim'}}})
    path=tmp_path/'full-rim.glb'; SF.write_glb(path,nodes)
    mesh=fitmesh.load_fit_mesh(path)
    optional=np.isin(mesh.part,[i for i,v in enumerate(mesh.lens_parts) if v])
    shape=(600,900); cam=raster.view_camera(mesh.frame,0,0,5.,shape,perspective=.1)
    hard=raster.get_scene(mesh.V,mesh.F[~optional],mesh.frame).render(cam,shape)['mask']
    photo=np.full((*shape,3),245,np.uint8); photo[hard]=30
    photo[440:455,210:690]=223  # faint floor reflection, outside the hard rim
    image=tmp_path/'photo.png'; Image.fromarray(photo).save(image)
    def known_camera(fg,scene,pivot,*args,ignore_faces=None,**kwargs):
        metrics=cameras.native_metrics(scene,cam,fg,cameras.matte_roi(fg),ignore_faces)
        record={'camera':cam,'view':'front','metrics':metrics,'sign':{},'loss':0.,'common_loss':0.,
                'refine':{'origin':'independent camera truth'},'fit':SimpleNamespace(evals=0)}
        return record,[record]
    monkeypatch.setattr(cameras,'fit_best',known_camera)
    row=FV.fit_image(path,image,'front',fit_openings=False)
    assert row['matte_mode']=='glossy_floor'
    assert all(r['frame_type']=='full_rim' for r in row['lens_loss']['construction'])
    assert row['iou_lens_optional'] > .9 and row['iou_lens_optional'] > row['iou']+.4
    assert row['matte_alternatives']['default']['iou'] < row['iou_lens_optional']
