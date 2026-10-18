"""Informational finish reservation warning, offline; no endpoint or renderer."""
import copy
import json
from types import SimpleNamespace

import pytest

from glasses3d.agent import finish_runway as F, loop as L, tools as T
from glasses3d.agent.pricing import Tariff
from glasses3d.agent.tool_reports import TEXT_LIMIT
from glasses3d.tests.agent.test_job_tools import scene, finish_scene, invoke, record
from glasses3d.tests.agent.test_finish_review import review_scene, final_args


def summary(tokens=246_969, settled=16_700_063, held=0, unknown=0):
    return {'maximum_micro_usd':23_000_000,'settled_micro_usd':settled,'held_micro_usd':held,
            'unknown_micro_usd':unknown,'settled_stop_usd':'20.000000',
            'remaining_to_stop_usd':f'{max(0,20_000_000-settled-held-unknown)/1e6:.6f}',
            'planning':{'last_counted_input_tokens':tokens,'max_output_tokens':16384}}


def runtime():
    state = summary()
    guard = SimpleNamespace(tariff=Tariff.frozen(),summary=lambda:copy.deepcopy(state))
    return L.RunStatus(guard),state


def test_project_uses_guard_tariff_for_whole_context_price_step_and_never_mutates():
    state=summary(); before=copy.deepcopy(state)
    evidence={'source':'test','image_tokens':5940,'ar_sheet_allowance_tokens':0,'text_allowance_tokens':4000}
    tariff=Tariff.frozen()
    result=F.project(state,tariff,evidence=evidence,review_ready=True)
    assert result['projected_input_tokens']==273293
    assert result['second_finish_hold_micro_usd']==8_061_125==tariff.reserve_micro(273293,16384)
    assert result['reserve_room_micro_usd']==6_299_937
    assert result['crosses_long_context_price_step'] and result['warning'] and not result['fits_current_reserve_room']
    assert 'not reserved money or a completion guarantee' in F.text(result)
    assert state==before and evidence['image_tokens']==5940


@pytest.mark.parametrize('held,unknown',[(1_000_000,0),(0,1_000_000),(15_000_000,2_000_000)])
def test_reserve_room_counts_unsettled_money_without_spending_or_releasing_it(held,unknown):
    state=summary(held=held,unknown=unknown)
    result=F.project(state,Tariff.frozen(),reference_count=6)
    assert result['reserve_room_micro_usd']==max(0,23_000_000-16_700_063-held-unknown)
    assert state['held_micro_usd']==held and state['unknown_micro_usd']==unknown


def test_warning_approaches_hold_with_disclosed_one_request_margin():
    tariff=Tariff.frozen()
    evidence={'image_tokens':0,'ar_sheet_allowance_tokens':0,'text_allowance_tokens':0}
    first=F.project(summary(tokens=1000,settled=0),tariff,evidence=evidence)
    boundary=23_000_000-first['second_finish_hold_micro_usd']-first['warning_margin_micro_usd']
    def reserve_only(settled):
        return {**summary(tokens=1000,settled=settled), 'settled_stop_usd': None}
    assert not F.project(reserve_only(boundary-1),tariff,evidence=evidence)['warning']
    at=F.project(reserve_only(boundary),tariff,evidence=evidence)
    assert at['warning'] and at['fits_current_reserve_room']
    assert at['warning_margin_micro_usd']==tariff.reserve_micro(1000,16384)


@pytest.mark.parametrize('settled_only', [False, True])
@pytest.mark.parametrize('review_ready', [False, True])
def test_finish_room_is_stop_minus_settled_held_unknown_in_every_mode(settled_only, review_ready):
    state = {**summary(tokens=250_000, settled=19_500_000, held=100_000, unknown=200_000),
             'settled_only': settled_only, 'maximum_micro_usd': 44_280_000}
    before = copy.deepcopy(state)
    tariff = Tariff.frozen()
    result = F.project(state, tariff, reference_count=6, review_ready=review_ready)
    assert result['remaining_to_stop_micro_usd'] == 200_000
    assert result['settled_stop_warning'] and result['warning']
    assert not result['fits_remaining_to_stop']
    assert result['finish_calls_remaining'] == (1 if review_ready else 2)
    assert result['finish_allowance_micro_usd'] == result['second_finish_hold_micro_usd'] + (
        0 if review_ready else tariff.reserve_micro(250_000, 16384))
    if settled_only:
        assert result['reserve_room_micro_usd'] is None
        assert 'estimates never refuse or shrink' in F.text(result)
    else:
        assert result['fits_current_reserve_room'] and not result['reserve_warning']
    assert state == before


def test_unknown_context_and_projected_limit_are_honestly_unavailable():
    state=summary(); state['planning']['last_counted_input_tokens']=None
    assert F.project(state,Tariff.frozen())['status']=='unavailable'
    result=F.project(summary(tokens=900_000),Tariff.frozen(),reference_count=6)
    assert result['status']=='unavailable' and result['warning']
    assert 'no completion guarantee' in F.text(result)


def test_panel_estimate_includes_reused_panels_and_scales_partial_photo_observation():
    data={'status':'ok','call_id':'look1','views':[{'ref':'front'}],
          'images':[{'size_px':[1008,542]},{'size_px':[1008,542],'reused_from_call':'earlier'}]}
    evidence=F.observation('compare_look_to_photos',data)
    assert evidence['image_tokens']==1306 and evidence['image_count']==2
    result=F.project(summary(),Tariff.frozen(),reference_count=6,evidence=evidence)
    assert result['evidence']['image_tokens']==1306*6 and result['evidence']['scaled_to_reference_count']==6
    assert evidence['image_tokens']==1306
    assert F.observation('read_image',data) is None
    assert F.observation('finish',{'look_comparison':{'status':'unavailable'},'images':data['images']}) is None


def test_default_photo_allowance_is_explicit_not_claimed_a_bound():
    one=F.project(summary(),Tariff.frozen(),reference_count=1)
    six=F.project(summary(),Tariff.frozen(),reference_count=6)
    assert six['evidence_allowance_tokens']>one['evidence_allowance_tokens']
    assert 'More crops can be needed' in six['evidence']['note']
    assert 'not exact counts' in six['assumptions']


def test_actual_budget_status_and_footer_expose_same_projection_without_mutation(scene):
    ctx,state=runtime(); before=copy.deepcopy(state)
    scene.job={'images':[{}]*6}
    output=invoke(scene,'budget_status',context=ctx)
    value=record(output)['finish_runway']
    assert value==ctx.finish_runway()
    assert value['second_finish_hold_usd'] in output[-1].text
    assert 'Start the finish review now' in output[-1].text
    assert state==before and scene.s.calls==[]


def test_parent_finish_projection_does_not_teach_unavailable_finish_to_specialist_context():
    ctx,_=runtime()
    assert ctx.finish_runway() is None
    assert 'second finish may need' not in ctx.footer()
    ctx.configure_finish(SimpleNamespace(job={'images':[{},{}]}))
    assert ctx.finish_runway()['status']=='projected'


def test_first_finish_reports_encoded_current_evidence_and_keeps_all_gates(review_scene):
    ctx,state=runtime(); review_scene.job={'images':[{},{}]}
    before=copy.deepcopy(state)
    output=invoke(review_scene,'finish',{'summary':'Inspect'},cid='first',context=ctx)
    first=record(output)
    assert first['status']=='review_required' and review_scene.finish is None
    report=first['finish_runway']
    assert report['review_ready'] and report['evidence']['tool']=='finish'
    assert report['evidence']['image_count']==4 and report['reply_allowance_tokens']==16384
    assert report['second_finish_hold_usd'] in output[-1].text
    assert 'not reserved money or a completion guarantee' in output[-1].text
    assert sum(len(v.text) for v in output if v.type=='text')<=TEXT_LIMIT
    assert ctx.finish_evidence==report['evidence']
    second=record(invoke(review_scene,'finish',final_args(),cid='second',context=ctx))
    assert second['status']=='accepted'
    assert state==before


def test_failed_output_does_not_remember_undelivered_evidence(review_scene,monkeypatch):
    ctx,_=runtime()
    def fail(*args,**kwargs):
        raise OSError('save report failed')
    monkeypatch.setattr(T,'limit_output',fail)
    output=invoke(review_scene,'finish',{'summary':'Inspect'},cid='first',context=ctx)
    assert isinstance(output,str) and 'save report failed' in output
    assert ctx.finish_evidence is None and review_scene.finish_review is None


def test_large_first_finish_keeps_runway_notice_in_actual_bounded_sdk_output(review_scene):
    ctx,_=runtime()
    output=invoke(review_scene,'finish',{'summary':'Detailed evidence. '*20_000},cid='first',context=ctx)
    assert json.loads(output[0].text)['paged']
    assert 'second finish may need $' in output[-1].text
    assert 'not reserved money or a completion guarantee' in output[-1].text
    assert sum(len(v.text) for v in output if v.type=='text')<=TEXT_LIMIT
