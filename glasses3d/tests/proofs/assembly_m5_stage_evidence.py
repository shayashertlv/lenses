"""$0 native stage-observation proof and separately labelled B7 startup cost.

Reads a frozen six-photo proof basis and immutable GLB. It does not fit cameras,
edit a model, open a model client or send a provider request. A real package
preparation is a separate invocation on an explicitly supplied copy job.
"""
from __future__ import annotations

import argparse
from datetime import datetime,timezone
import json
import math
import os
from pathlib import Path
import shutil
from types import SimpleNamespace

from glasses3d.agent import procs
from glasses3d.agent.temple_lab import arm_evidence as E, cli, package
from glasses3d.agent.temple_lab.lab import sha,read,write_json_atomic
from glasses3d.agent.look_cost import documented_image,usage_block,VISION_SOURCE,SOURCE_ACCESSED
from glasses3d.agent.pricing import Tariff,validate_usage
from glasses3d.g3d_host import compare as C

ROOT=Path(__file__).resolve().parents[3]
HISTORY=ROOT/'glasses3d/data/jobs/prada-beige-86248315/runs/20261015T083404-b50f0d'


def prove_child_input(root, parent_job):
    """Construct the actual nested-stage contract/input locally; never create a client."""
    from glasses3d.agent.loop import StageSpec,StageContract,contract_problems
    from glasses3d.agent.session_inputs import plan_session_input,image_identity
    from glasses3d.agent.temple_lab import stage
    root=Path(root).resolve()
    lab=stage.StageLab(root)
    try:
        cli.check_package(root,lab)
        settings=stage.settings()
        spec=StageSpec('temple_stage',cli.instructions_text(root,settings.finish_k),
                       stage.registry(lab,settings),(root/'inputs/brief.txt').read_text(encoding='utf-8'),
                       settings,photos=cli.references(lab),
                       scene_note=(f"Current job head captured in revision 0 at {lab.s.head['id']}. "
                                   "This temple stage has a $23 allowance inside the job's approval. "
                                   "The tool footer reports the shared job's totals; both limits apply."),
                       contract=cli.tool_contract(),
                       contract_brief=(root/'inputs/contract-brief.txt').read_text(encoding='utf-8'))
        assert not contract_problems(spec.contract,instructions=spec.instructions,tools=spec.tools,
                                     brief=spec.contract_brief)
        actual=StageContract.from_spec(spec,'23')
        assert actual==stage.contract(parent_job,settings,'23')
        plan=plan_session_input([],prompt=spec.brief,photos=spec.photos,invocation_text=spec.scene_note)
        sent=[p for p in plan.content if p.get('type')=='input_image']
        assert len(sent)==12
        assert [image_identity(p['image_url'])[0] for p in sent]==[image_identity(p.image_url)[0] for p in spec.photos]
        # Saved proof input is explicitly unapproved: construction is not an API request or approval.
        payload={'proof_only':True,'approved':False,'provider_calls':0,'instructions':spec.instructions,
                 'tools':[{'name':t.name,'description':t.description,'parameters':t.params_json_schema} for t in spec.tools],
                 'input':[{'role':'user','content':plan.content}]}
        write_json_atomic(root/'proof-child-input.json',payload)
        write_json_atomic(root/'proof-child-input-receipt.json',plan.receipt)
        return {'contract_matches_parent_schema':True,'image_count':len(sent),'tool_names':[t.name for t in spec.tools],
                'input_file':'proof-child-input.json','input_sha256':sha(root/'proof-child-input.json'),
                'proof_file_bytes':(root/'proof-child-input.json').stat().st_size,
                'compact_content_json_bytes':len(json.dumps(plan.content,separators=(',',':')).encode('utf-8')),
                'reference_data_url_bytes':sum(len(p.image_url.encode('ascii')) for p in spec.photos),
                'provider_calls':0,'approved':False}
    finally:
        lab.close()


def startup_cost(references,extra_brief,history=HISTORY):
    """New B7 input only; A5's unchanged historical temple history stays unchanged."""
    images=[]
    for ref in references:
        image=documented_image(*ref.size_px,'high')
        images.append({"path":ref.path,"label":ref.label,**image,"data_url_bytes":len(ref.image_url.encode('ascii'))})
    image_tokens=sum(r['tokens'] for r in images)
    # No local tokenizer is asserted to equal Astra's provider tokenizer.
    text_chars=len(extra_brief)+sum(len(r.label)+len(r.path)+len(str(r.size_px))+30 for r in references)
    text_tokens=math.ceil(text_chars*.42)
    added=image_tokens+text_tokens
    tariff=Tariff.frozen()
    rows=[]
    for index,sequence in enumerate(range(9,41)):
        reqpath=history/f'transport/{sequence:04d}-request.json'
        respath=history/f'transport/{sequence:04d}-response.json'
        req,res=read(reqpath),read(respath)
        names={r.get('name') for r in req['tools']}
        assert 'facet_edit' in names and 'execute_blender_code' not in names
        flat=validate_usage(res['usage'])
        total=flat['input_tokens']+added
        stable=usage_block(flat,tokens=total,cached=flat['cached_tokens']+(added if index else 0),
                           written=flat['cache_write_tokens']+(0 if index else added))
        all_writes=usage_block(flat,tokens=total,written=flat['cache_write_tokens']+added)
        observed=tariff.settle_micro(res['usage'])
        rows.append({"sequence":sequence,"request_sha256":sha(reqpath),"response_sha256":sha(respath),
                     "observed_input_tokens":flat['input_tokens'],"modeled_input_tokens":total,
                     "added_image_tokens":image_tokens,"modeled_added_text_tokens":text_tokens,
                     "added_cache_write_tokens":added if index==0 else 0,
                     "added_cached_tokens":added if index else 0,
                     "observed_micro_usd":observed,
                     "stable_extra_micro_usd":tariff.settle_micro(stable)-observed,
                     "all_extra_writes_micro_usd":tariff.settle_micro(all_writes)-observed})
    return {"scope":"new B7 startup evidence only; separate from A5 historical look-image counterfactual",
            "source_history":str(history),"historical_child_requests":32,"provider_calls":0,"actual_spend_usd":0,
            "official_image_formula_source":VISION_SOURCE,"access_date":SOURCE_ACCESSED,
            "images":images,"extra_image_count":len(images),"image_tokens":image_tokens,
            "extra_brief_utf8_bytes":len(extra_brief.encode('utf-8')),"modeled_text_chars_with_descriptors":text_chars,
            "text_token_model":"ceil(0.42 * characters), historical local estimate, not a provider count",
            "modeled_text_tokens":text_tokens,"modeled_added_input_tokens":added,
            "assumption":"One new stable child prefix; first request writes it, next31 read it. "
                         "Identical images/detail/order/text are required; cache hits are not guaranteed. "
                         "Saved model replies, outputs and tool history stay fixed; this is a cost scenario, not a future-run prediction.",
            "rows":rows,"first_write_extra_usd":rows[0]['stable_extra_micro_usd']/1e6,
            "repeat_extra_usd":sum(r['stable_extra_micro_usd'] for r in rows[1:])/1e6,
            "stable_total_extra_usd":sum(r['stable_extra_micro_usd'] for r in rows)/1e6,
            "every_request_writes_extra_usd":sum(r['all_extra_writes_micro_usd'] for r in rows)/1e6,
            "modeled_peak_input_tokens":max(r['modeled_input_tokens'] for r in rows)}


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--basis-job',type=Path)
    p.add_argument('--model',type=Path)
    p.add_argument('--copy-job',type=Path,help='Prepare a real private lab only from this disposable job copy')
    p.add_argument('--out',type=Path,required=True)
    args=p.parse_args()
    procs.opt_out_self()
    start=datetime.now(timezone.utc).isoformat()
    if args.copy_job:
        from glasses3d.agent.job_scene import JobScene
        if not args.copy_job.resolve().is_relative_to((ROOT/'glasses3d/data/assembly').resolve()):
            raise ValueError('real preparation proof requires an explicit disposable assembly copy')
        scene=JobScene(args.copy_job)
        expected_head=dict(scene.s.head or {})
        if not expected_head:
            raise ValueError('real preparation proof requires a copied verified checkpoint chain, not a seed scene')
        try:
            scene.start()
            assert scene.s.head==expected_head
            with scene.lock:
                result=package.prepare(scene,args.out,'m5-b7-prepared-proof')
            assert scene.s.head==expected_head
        finally:
            scene.close()
        manifest=read(args.out/'inputs/lab.json')
        report=read(args.out/'inputs/arm-evidence/report.json')
        result['child_input']=prove_child_input(args.out,scene.job)
    else:
        if not args.basis_job or not args.model:
            p.error('--basis-job and --model are required without --copy-job')
        args.out.mkdir(parents=True,exist_ok=False)
        inputs=args.out/'inputs'
        (inputs/'images').mkdir(parents=True)
        basis=C.load_basis(args.basis_job)
        photos=[]
        before={str(args.model):sha(args.model),str(basis.dir/'basis.json'):sha(basis.dir/'basis.json')}
        for rid,row in basis.refs.items():
            source=basis.photo_path(rid)
            before[str(source)]=sha(source)
            dst=inputs/'images'/source.name
            shutil.copyfile(source,dst)
            photos.append({"id":rid,"basis_ref":rid,"role":"target","path":f'images/{source.name}',
                           "view":row['view_kind'],"sha256":sha(dst)})
        assert len(photos)==6
        report,assets=E.prepare(inputs,basis,args.model,photos)
        manifest={"photos":photos,"assets":assets,"arm_evidence":{"images":report['images']}}
        write_json_atomic(inputs/'lab.json',manifest)
        (inputs/'brief.txt').write_text(E.brief_text(report),encoding='utf-8')
        assert before=={path:sha(path) for path in before}
        result={"status":"host_evidence_proved","source_unchanged":before,"paid_calls":0}
    refs=cli.references(SimpleNamespace(root=args.out,manifest=manifest))
    extra=refs[len(manifest['photos']):]
    cost=startup_cost(extra,E.brief_text(report))
    write_json_atomic(args.out/'startup-cost.json',cost)
    compact={"started_at":start,"ended_at":datetime.now(timezone.utc).isoformat(),"result":result,
             "evidence_report":str(args.out/'inputs/arm-evidence/report.json'),
             "original_photos":len(manifest['photos']),"startup_reference_inputs":len(refs),
             "profiles":{r['photo_id']:{side:{"opening":a['theta_deg'],"observed":a['theta_observed'],
                          "outline":a['outline_fit'],"tip":a.get('tip_crossing',{}).get('status'),
                          "profile_stations":len(a.get('image_plane_profile',{}).get('stations',[])),
                          "warnings":a['warnings']} for side,a in r['arms'].items()} for r in report['photos']},
             "cost":{k:v for k,v in cost.items() if k not in ('rows','images')},"paid_calls":0}
    write_json_atomic(args.out/'proof.json',compact)
    print(json.dumps({"status":result['status'],"out":str(args.out),"reference_inputs":len(refs),
                      "cost_extra_usd":cost['stable_total_extra_usd'],"paid_calls":0}),flush=True)


if __name__=='__main__':
    main()
