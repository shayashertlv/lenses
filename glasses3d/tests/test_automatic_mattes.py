"""Automatic matte choice is input-only, retains alternatives, and never relaxes camera gates."""
from copy import deepcopy
import json
from types import SimpleNamespace

from PIL import Image
import pytest

from glasses3d.g3d_host import fit_views as FV
from glasses3d.agent.seeing import resolve_photo_mattes, Seeing
from glasses3d.tests.agent.test_job_tools import scene, invoke, record


@pytest.fixture
def fit_case(tmp_path, monkeypatch):
    model, photo = tmp_path/'model.glb', tmp_path/'photo.png'
    model.write_bytes(b'unchanged model')
    Image.new('RGB',(40,30),'white').save(photo)
    context = {'model':{'sha256':FV.sha256_file(model)}, 'optional_parts':[], 'lens_loss':{'mode':'full'}}
    monkeypatch.setattr(FV,'_fit_context',lambda *args:context)
    calls = []
    rows = {mode:{'status':'ok','view':'front','camera':{},'matte_mode':mode,'iou':score,
                  'flags':[],'seconds':1.,'model':context['model'],'image_sha256':FV.sha256_file(photo)}
            for mode,score in [('default',.8),('glossy_floor',.9)]}
    def fitted(glb,path,kind,**kw):
        calls.append(deepcopy(kw))
        value = rows[kw['matte_mode']]
        if isinstance(value,Exception):
            raise value
        row = deepcopy(value)
        if kw['overlay_path']:
            kw['overlay_path'].write_bytes(kw['matte_mode'].encode())
            row['overlay'] = str(kw['overlay_path'])
        return row
    monkeypatch.setattr(FV,'_fit_image_mode',fitted)
    return model,photo,context,rows,calls


def test_automatic_retains_both_identical_fit_settings_and_selected_overlay(fit_case,tmp_path):
    model,photo,context,rows,calls = fit_case
    original = (model.read_bytes(),photo.read_bytes())
    out = tmp_path/'overlay.png'
    row = FV.fit_image(model,photo,'front',overlay_path=out,candidates=('front',),n_refine=3,
        cross_winners={'left':{'yaw':90}},initial_theta={'+1':3.4},off_axis=True)
    assert row['matte_mode']=='glossy_floor' and out.read_bytes()==b'glossy_floor'
    assert set(row['matte_alternatives'])=={'default','glossy_floor'}
    assert row['matte_selection']['mode']=='automatic_fit_comparison'
    assert row['matte_selection']['chosen']=='glossy_floor'
    assert 'higher native silhouette IoU' in row['matte_selection']['reason']
    a,b = [{k:v for k,v in call.items() if k not in ('matte_mode','overlay_path')} for call in calls]
    assert a==b and a['initial_theta']=={'+1':3.4} and a['off_axis'] is True
    assert calls[0]['overlay_path']!=calls[1]['overlay_path']!=out
    assert (model.read_bytes(),photo.read_bytes())==original
    json.dumps(row)  # alternatives are full records, without a self-cycle


@pytest.mark.parametrize('failed', [None, 'default', 'both'])
def test_one_photo_distributes_alternatives_in_one_bounded_pool(fit_case, monkeypatch, tmp_path, failed):
    model,photo,context,rows,calls = fit_case
    if failed:
        rows['default'] = FV._EmptyMatteError('no foreground')
    if failed == 'both':
        rows['glossy_floor'] = RuntimeError('optimizer failed')
    batches=[]
    def mapped(jobs, workers):
        batches.append((len(jobs), workers))
        return [FV._run(job) for job in jobs]
    monkeypatch.setattr(FV, '_map', mapped)
    row, = FV._map_photos([((model,photo,'front'), {'_model_context':context,
        'overlay_path':tmp_path/'parallel.png', 'matte_mode':None})], 3)
    assert batches == [(2,3)] and len(calls)==2
    assert set(row['matte_alternatives'])=={'default','glossy_floor'}
    if failed == 'both':
        assert row['status']=='failed'
        assert all(value['camera'] is None for value in row['matte_alternatives'].values())
        assert not (tmp_path/'parallel.png').exists()
    else:
        assert row['matte_mode']=='glossy_floor'
        assert (tmp_path/'parallel.png').read_bytes()==b'glossy_floor'


@pytest.mark.parametrize('default,glossy,chosen',[
    (('ok',.7),('unusable',.99),'default'),
    (('refused',.99),('ok',.7),'glossy_floor'),
    (('ok',.8),('ok',.8),'default'),
    (('unusable',.8),('refused',.8),'default'),
    (('ok',float('nan')),('ok',.8),'glossy_floor'),
    (('refused',.7),('refused',.8),'glossy_floor')])
def test_selection_uses_existing_status_then_finite_native_iou(fit_case,default,glossy,chosen):
    model,photo,_,rows,_ = fit_case
    for mode,values in [('default',default),('glossy_floor',glossy)]:
        rows[mode].update(status=values[0],iou=values[1],flags=['retained diagnostic'])
    row = FV.fit_image(model,photo,'front')
    assert row['matte_mode']==chosen and row['status']==rows[chosen]['status']
    assert all(r['flags']==['retained diagnostic'] for r in row['matte_alternatives'].values())


@pytest.mark.parametrize('both',[False,True])
def test_failed_alternatives_remain_reported_without_fabricated_camera(fit_case,both):
    model,photo,_,rows,_ = fit_case
    rows['default']=ValueError('default failed')
    if both:
        rows['glossy_floor']=RuntimeError('glossy failed')
    row = FV.fit_image(model,photo,'front')
    assert row['matte_mode']==('default' if both else 'glossy_floor')
    failed=row['matte_alternatives']['default']
    assert failed['status']=='failed' and failed['camera'] is None
    assert failed['error']=={'type':'ValueError','message':'default failed'}
    assert row['matte_selection']['alternatives']['default']['code']=='E_PHOTO_FIT'


@pytest.mark.parametrize('other',['ok','failed'])
def test_one_empty_alternative_keeps_both_records(fit_case,other):
    model,photo,_,rows,_=fit_case
    rows['default']=FV._EmptyMatteError('matte is empty')
    if other=='failed':
        rows['glossy_floor']=RuntimeError('optimizer failed')
    row=FV.fit_image(model,photo,'front')
    assert set(row['matte_alternatives'])=={'default','glossy_floor'}
    assert row['matte_alternatives']['default']['camera'] is None
    assert row['matte_alternatives']['glossy_floor']['status']==other


@pytest.mark.parametrize('with_lenses', [False, True], ids=['frame_only', 'coarse_browline'])
def test_empty_input_direct_refuses_but_grouped_retains_both_actual_mattes(tmp_path, with_lenses):
    from glasses3d.tests import glbfab
    model,photo=tmp_path/'model.glb',tmp_path/'blank.png'
    parts=glbfab.default_parts()
    if not with_lenses:
        parts={name:part for name,part in parts.items() if part[2]!='lens'}
    model.write_bytes(glbfab.build(parts=parts))
    Image.new('RGB',(40,40),(200,200,200)).save(photo)
    with pytest.raises(ValueError,match='matte is empty'):
        FV.fit_image(model,photo,'front')
    result=FV.fit_views(model,{photo.name:photo},{photo.name:'front'},tmp_path/'fit',workers=1,log=lambda _:None)
    row=result['images'][photo.name]
    # The coarse rectangular lenses are valid plates: their brow-only frame is semi-rimless.
    # Removing those primitives exercises real default mattes without a construction-policy mock.
    base,floor=('open_frame','glossy_open_frame') if with_lenses else ('default','glossy_floor')
    assert row['status']=='failed' and row['camera'] is None and row['matte_mode']==base
    assert set(row['matte_alternatives'])=={base,floor}
    policy=row['lens_loss']
    assert policy['mode']==('optional' if with_lenses else 'full')
    if with_lenses:
        assert policy['optional_nodes']==['Lens_L','Lens_R']
        assert len(policy['construction'])==2
        assert all(c['frame_type']=='semi_rimless' and c['frame_type_source']=='inferred' and c['provisional'] is True
                   for c in policy['construction'])
    else:
        assert policy['optional_nodes']==[] and policy['construction']==[]
    for candidate in row['matte_alternatives'].values():
        assert candidate['status']=='failed' and candidate['camera'] is None
        assert candidate['matte']['foreground_px']==0 and 'matte is empty' in candidate['refusal']
    json.dumps(result)  # exception/traceback does not leak into the retained plain report
    explicit=FV.fit_views(model,{photo.name:photo},{photo.name:'front'},tmp_path/'explicit',workers=1,
        matte_modes='default',log=lambda _:None)['images'][photo.name]
    assert explicit['status']=='failed' and explicit['camera'] is None and 'matte_alternatives' not in explicit
    assert explicit['matte_mode']=='default'


@pytest.mark.parametrize('kind,candidates,match',[
    ('diagonal',None,'unknown view kind'),('front',('diagonal',),'unknown views')])
def test_direct_invalid_candidates_refuse_before_any_fit(fit_case,kind,candidates,match):
    model,photo,_,_,calls=fit_case
    with pytest.raises(ValueError,match=match):
        FV.fit_image(model,photo,kind,candidates=candidates)
    assert not calls


@pytest.mark.parametrize('mode',['default','glossy_floor','open_frame'])
def test_explicit_mode_is_authoritative_and_single_fit(fit_case,mode):
    model,photo,_,rows,calls = fit_case
    rows['open_frame']={**rows['default'],'matte_mode':'open_frame'}
    row=FV.fit_image(model,photo,'front',matte_mode=mode)
    assert len(calls)==1 and row['matte_mode']==mode
    assert row['matte_selection']['mode']=='explicit' and 'matte_alternatives' not in row


def test_optional_lens_construction_compares_both_floor_candidates(fit_case):
    model,photo,context,rows,calls = fit_case
    context.update(optional_parts=[1],lens_loss={'mode':'optional'})
    rows['open_frame']={**rows['default'],'matte_mode':'open_frame'}
    rows['glossy_open_frame']={**rows['glossy_floor'],'matte_mode':'glossy_open_frame','iou_lens_optional':.95}
    row=FV.fit_image(model,photo,'front')
    assert len(calls)==2 and row['matte_mode']=='glossy_open_frame'
    assert row['matte_selection']['mode']=='automatic_fit_comparison'
    assert set(row['matte_alternatives']) == {'open_frame','glossy_open_frame'}


def test_clear_lens_floor_candidate_ranks_optional_overlap_not_filled_raw_overlap(fit_case):
    model,photo,_,rows,_=fit_case
    rows['default']['iou']=.88
    rows['glossy_floor'].update(iou=.08,iou_lens_optional=.99)
    result=FV.fit_image(model,photo,'front')
    assert result['matte_mode']=='glossy_floor'
    assert result['matte_alternatives']['glossy_floor']['iou']==.08


@pytest.mark.parametrize('which',['photo','model'])
def test_source_change_between_alternatives_cannot_be_selected(fit_case,monkeypatch,which):
    model,photo,_,_,_ = fit_case
    real=FV._fit_image_mode
    def changed(*args,**kw):
        row=real(*args,**kw)
        if kw['matte_mode']=='default':
            (photo if which=='photo' else model).write_bytes(b'changed')
        return row
    monkeypatch.setattr(FV,'_fit_image_mode',changed)
    with pytest.raises(ValueError,match='changed between matte alternatives'):
        FV.fit_image(model,photo,'front')


def test_photo_override_ids_filenames_owner_precedence_and_immutable_mapping():
    owner={'front.png':None,'side.jpg':'open_frame'}
    override={'front':'glossy_floor','side.jpg':'open_frame'}
    originals=deepcopy((owner,override))
    modes,sources=resolve_photo_mattes(list(owner),owner,override)
    assert modes=={'front.png':'glossy_floor','side.jpg':'open_frame'}
    assert sources=={'front.png':'astra_override','side.jpg':'owner'}
    assert (owner,override)==originals


@pytest.mark.parametrize('overrides,match',[
    ({'missing':'default'},'unknown matte photo'),
    ({'front':'wrong'},'unknown matte mode'),
    ({'front':'default','front.png':'default'},'duplicate matte override'),
    ({'side':'default'},'owner declared'),
    (['front'],'mattes must map')])
def test_invalid_overrides_are_rejected(overrides,match):
    with pytest.raises(ValueError,match=match):
        resolve_photo_mattes(['front.png','side.jpg'],{'side.jpg':'open_frame'},overrides)


def test_summary_keeps_choice_and_both_alternative_diagnostics():
    selection={'schema':'g3d.matte_selection/1','chosen':'default','alternatives':{'default':{},'glossy_floor':{}}}
    out=Seeing._fit_ref_summary({'fit':{'matte_selection':selection}},usable=False)
    assert out['matte_selection']==selection
    from glasses3d.g3d_host.compare import FIT_EVIDENCE_FIELDS
    assert 'matte_selection' in FIT_EVIDENCE_FIELDS


def test_choice_evidence_is_frozen_and_independent_metadata_tampering_refuses(tmp_path):
    from glasses3d.tests.test_basis_partial import source,freeze
    from glasses3d.g3d_host import compare as C
    selection={'schema':'g3d.matte_selection/1','chosen':'default','alternatives':{
        'default':{'status':'ok','iou':.8},'glossy_floor':{'status':'refused','iou':.9}}}
    folder,photos,_=source(tmp_path,{'photo.png':{'matte_selection':selection}})
    root=tmp_path/'job'
    basis=freeze(root,folder,photos)
    assert basis.refs['photo']['fit']['matte_selection']==selection
    changed=deepcopy(basis.meta)
    changed['refs']['photo']['fit']['matte_selection']['chosen']='glossy_floor'
    C.write_json(basis.dir/'basis.json',changed)
    with pytest.raises(ValueError,match='fit diagnostic evidence'):
        C.Basis(basis.dir,root)


def test_owner_conflict_refuses_before_export_and_records_arguments(tmp_path,monkeypatch):
    calls=[]
    session=SimpleNamespace(root=tmp_path,new_call_id=lambda name,cid:cid or 'fit',
        record_call=lambda *args:calls.append(args))
    seeing=Seeing(session)
    monkeypatch.setattr(seeing,'export_head',lambda *args:pytest.fail('invalid override exported a model'))
    row=seeing.fit_cameras('inspect matte',{'photo.png':tmp_path/'photo.png'},{'photo.png':'front'},
        matte_modes={'photo.png':'open_frame'},mattes={'photo':'default'})
    assert row['code']=='E_ARGS' and 'owner declared' in row['message']
    assert calls[-1][-1]==row and calls[-1][2]['mattes']=={'photo':'default'}
    assert not (tmp_path/'cameras').exists()


def test_real_tool_registry_accepts_strict_mattes_and_forwards_mapping(scene,monkeypatch):
    from glasses3d.agent import jobs,tools
    monkeypatch.setattr(jobs,'load_job',lambda root:{'images':[
        {'original_name':'front.png','file':'images/front.png','view_kind':'front','fit_kinds':['front']}]})
    received=[]
    scene.seeing.fit_cameras=lambda *args,**kwargs:received.append(kwargs) or {'status':'ok'}
    registry=tools.build_tools(scene)
    fit=next(tool for tool in registry if tool.name=='fit_cameras')
    encoded=json.dumps(fit.params_json_schema)
    assert 'PhotoMatte' in encoded and 'additionalProperties' in encoded
    invoke(scene,'fit_cameras',{'reason':'inspect floor','workers':1,'mattes':[{'photo':'front','mode':'glossy_floor'}]})
    assert received[0]['mattes']=={'front':'glossy_floor'}
    assert received[0]['matte_modes']=={'front.png':None}
    output=invoke(scene,'fit_cameras',{'reason':'duplicate','mattes':[
        {'photo':'front','mode':'default'},{'photo':'front','mode':'glossy_floor'}]},cid='duplicate')
    assert len(received)==1 and 'duplicate matte override' in str(record(output))
