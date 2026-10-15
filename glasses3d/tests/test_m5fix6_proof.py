"""Proof-control checks: held-out labels never enter policy fitting or mutate jobs."""
import json
from pathlib import Path

from PIL import Image
import pytest

from glasses3d.tests.proofs import assembly_m5fix6 as proof


def inputs(tmp_path,declared=None):
    job=tmp_path/'original-job'
    (job/'images').mkdir(parents=True)
    photo=job/'images/photo.png'
    Image.new('RGB',(20,20),'white').save(photo)
    model=tmp_path/'source.glb'
    model.write_bytes(b'immutable source model; fit is mocked in this control')
    row={'file':'images/photo.png','original_name':'photo.png','sha256':proof.sha(photo),
         'view_kind':'front','side':'both','fit_kinds':['front']}
    if declared is not None:
        row['matte_mode']=declared
    (job/'job.json').write_text(json.dumps({'images':[row],'description':'owner words','specs':{}}))
    return {'job_root':str(job),'job_sha256':proof.sha(job/'job.json'),
            'model':{'path':str(model),'sha256':proof.sha(model)},
            'reference_mattes':{'photo.png':'glossy_floor' if declared is None else declared},
            'reference_sources':['held-out test observation']}


def record(chosen,mode):
    alternatives={key:{'status':'ok' if key==chosen else 'unusable','matte_mode':key,'iou':.9 if key==chosen else .2}
                  for key in (('default','glossy_floor') if mode=='automatic_fit_comparison' else (chosen,))}
    return {'status':'ok','matte_mode':chosen,'iou':.9,'overlay':None,
            'matte_selection':{'schema':'g3d.matte_selection/1','mode':mode,'chosen':chosen,
                               'rule':'fixture','reason':'independent fixture result'},
            'matte_alternatives':alternatives}


@pytest.mark.parametrize('declared',[None,'open_frame'])
def test_held_out_choice_is_only_a_later_comparison(monkeypatch,tmp_path,declared):
    spec=inputs(tmp_path,declared)
    output=tmp_path/'proof'
    calls=[]
    policy_file=output/'fixture/policy/cameras.json'
    class HeldOutSpec(dict):
        def __getitem__(self,key):
            if key=='reference_mattes':
                assert policy_file.is_file(), 'held-out choices were read before policy evidence was frozen'
            return super().__getitem__(key)
    def fit(model,images,views,candidates,directory,workers,modes,phase):
        calls.append(dict(modes))
        chosen=(declared or 'default') if phase.endswith(':policy') else modes['photo.png']
        row=record(chosen,'explicit' if declared or phase.endswith(':baseline') else 'automatic_fit_comparison')
        result={'images':{'photo.png':row},'seconds':1.}
        directory.mkdir(parents=True)
        proof.C.write_json(directory/'cameras.json',result)
        return result,1.
    monkeypatch.setattr(proof,'fit_run',fit)
    before=Path(spec['job_root'],'job.json').read_bytes()
    result=proof.run_product('fixture',HeldOutSpec(spec),output,1)
    assert calls[0]=={'photo.png':declared}
    assert calls[1]==spec['reference_mattes']
    assert result['disagreements']==(['photo.png'] if declared is None else [])
    assert proof.read(policy_file)['images']['photo.png']['matte_mode']==(declared or 'default')
    assert Path(spec['job_root'],'job.json').read_bytes()==before
    if declared is None:
        assert result['images']['photo.png']['alternatives']['glossy_floor']['status']=='unusable'


def test_source_mutation_aborts_before_held_out_baseline(monkeypatch,tmp_path):
    spec=inputs(tmp_path)
    calls=[]
    def fit(model,images,views,candidates,directory,workers,modes,phase):
        calls.append(phase)
        directory.mkdir(parents=True)
        result={'images':{'photo.png':record('default','automatic_fit_comparison')},'seconds':1.}
        proof.C.write_json(directory/'cameras.json',result)
        Path(spec['job_root'],'images/photo.png').write_bytes(b'changed outside proof')
        return result,1.
    monkeypatch.setattr(proof,'fit_run',fit)
    with pytest.raises(ValueError,match='source input changed'):
        proof.run_product('fixture',spec,tmp_path/'proof',1)
    assert calls==['fixture:policy']
