"""$0 sequential replay of saved Prada 3 finish-runway notices. No token-count/model API."""
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import time
from unittest.mock import patch

from glasses3d.agent import finish_runway as F
from glasses3d.agent.loop import footer_text
from glasses3d.agent.pricing import Tariff

ROOT=Path(__file__).resolve().parents[2]
JOB=ROOT/'data/jobs/prada-beige-86248315'
RUN=JOB/'runs/20261015T185027-2f6756'


def read(path):
    assert not path.name.endswith('.spec.json') and not path.name.startswith('.env')
    return json.loads(path.read_text(encoding='utf8'))


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def tool_body(path,inputs):
    inputs[path]=sha(path)
    output=read(path)['result']
    body=json.JSONDecoder().raw_decode(next(v['text'] for v in output if v['type']=='text'))[0]
    if body.get('full_report'):
        report=(JOB/body['full_report']['path']).resolve()
        assert report.is_relative_to((JOB/'work/tool_reports').resolve())
        assert sha(report)==body['full_report']['sha256']
        inputs[report]=sha(report)
        body=read(report)['result']
    return body


def replay(out):
    started=time.monotonic()
    began=datetime.now(timezone.utc).isoformat()
    out=Path(out).resolve()
    out.mkdir(parents=True,exist_ok=False)
    budget_path,events_path=RUN/'budget.json',RUN/'events.jsonl'
    inputs={p:sha(p) for p in (budget_path,events_path,JOB/'job.json',RUN/'result.json')}
    budget,result,job=read(budget_path),read(RUN/'result.json'),read(JOB/'job.json')
    rows={v['sequence']:v for v in budget['rows']}
    child_sequences={n for p in (RUN/'stages').glob('*/result.json')
                     for n in read(p)['budget']['request_sequences']}
    tariff=Tariff.from_dict(budget['tariff'])
    events=[json.loads(line) for line in events_path.read_text(encoding='utf8').splitlines() if line]
    all_requests=sorted((RUN/'transport').glob('*-request.json'))
    inputs.update({p:sha(p) for p in all_requests})
    assert len(rows)==len(all_requests)==88
    for row in rows.values():
        assert row['reserved_micro_usd']==tariff.reserve_micro(row['input_tokens'],row['max_output_tokens'])
    parent_tools={'scene_summary','read_image','execute_blender_code','render_views','fit_cameras','read_report',
        'temple_stage','save_checkpoint','list_checkpoints','restore_checkpoint','compare_to_photos','pick_surface_point',
        'photo_read','export_glb','arview_capture','compare_look_to_photos','budget_status','finish','seat_lens',
        'hide_eyewire','check_lens_seating','unseat_lens','unhide_eyewire','photo_print'}
    starts,seen,records={},set(),[]
    last_sequence,evidence=0,None
    for event in events:
        if event['event']=='api_response':
            last_sequence=event['request_seq']
            seen.add(last_sequence)
        elif event['event']=='tool_started':
            starts[event['tool_call_id']]=event
        elif event['event']=='tool_finished' and event['name'] in parent_tools:
            # Stage tools such as read_report share a name; only parent tool directories count.
            origin=starts[event['tool_call_id']]
            path=RUN/origin['tool_dir']/'result.json'
            if Path(origin['tool_dir']).parts[0]!='tools':
                continue
            if int(Path(origin['tool_dir']).name.split('-')[0]) in child_sequences:
                continue
            body=tool_body(path,inputs)
            observed=F.observation(event['name'],body)
            money=sum(rows[n]['settled_micro_usd'] for n in seen)
            state={'maximum_micro_usd':budget['maximum_micro_usd'],'settled_micro_usd':money,
                'held_micro_usd':0,'unknown_micro_usd':0,
                'planning':{'last_counted_input_tokens':rows[last_sequence]['input_tokens'],
                            'max_output_tokens':rows[last_sequence]['max_output_tokens']}}
            projection=F.project(state,tariff,reference_count=len(job['images']),evidence=observed or evidence,
                                 review_ready=body.get('status')=='review_required')
            footer=footer_text(settled_usd=money/1e6,cap_usd=23,context_tokens=rows[last_sequence]['input_tokens'],
                settled_stop_usd=20,remaining_to_stop_usd=max(0,20_000_000-money)/1e6,
                held_usd=0,unknown_usd=0,finish_runway=projection)
            records.append({'event_sequence':event['sequence'],'time':event['time'],'tool':event['name'],
                'call_id':event['tool_call_id'],'context_request_sequence':last_sequence,
                'settled_micro_usd':money,'projection':projection,'footer':footer})
            if observed is not None:
                evidence=observed
    warning=next(row for row in records if row['projection'].get('warning'))
    before88=[row for row in records if row['context_request_sequence']<88 and row['projection'].get('warning')]
    assert before88, 'Warning must be shown before request 88, without using future finish evidence.'
    final=records[-1]
    assert final['tool']=='finish' and final['projection']['review_ready']
    assert final['projection']['projected_input_tokens']>=result['budget']['planning']['last_counted_input_tokens']
    assert final['projection']['second_finish_hold_micro_usd']>=result['budget']['planning']['next_hold_micro_usd']
    assert all(sha(path)==digest for path,digest in inputs.items())
    report={'schema':'g3d.m5fix9_runway_replay/1','status':'passed','started_utc':began,
        'finished_utc':datetime.now(timezone.utc).isoformat(),'seconds':round(time.monotonic()-started,3),'pid':os.getpid(),
        'source_run':str(RUN),'paid_calls':0,'token_count_calls':0,'network_calls':0,
        'tariff':budget['tariff'],'source_requests':len(all_requests),'source_assets_unchanged':True,
        'earliest_warning':warning,'last_warning_before_request88':before88[-1],'first_finish_projection':final,
        'actual_refused_next_request':result['budget']['planning'],
        'interpretation':'Replay uses saved counts and only evidence available by each event. New warning text is not remotely recounted. Projections and warning timing do not guarantee completion.',
        'records':records,'input_hashes':[{'path':str(p),'sha256':v} for p,v in inputs.items()]}
    (out/'report.json').write_text(json.dumps(report,indent=2),encoding='utf8')
    print(json.dumps({'status':report['status'],'output':str(out/'report.json'),'seconds':report['seconds'],
        'first_warning_after_request':warning['context_request_sequence'],
        'last_before88_after_request':before88[-1]['context_request_sequence'],
        'final_projected_hold':final['projection']['second_finish_hold_usd'],'paid_calls':0,'token_count_calls':0},indent=2))
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',required=True)
    args=parser.parse_args()
    def forbidden(*args,**kwargs):
        raise AssertionError('Network is forbidden in this saved-evidence proof')
    with patch.object(socket,'create_connection',forbidden),patch.object(socket.socket,'connect',forbidden):
        replay(args.out)


if __name__=='__main__':
    main()
