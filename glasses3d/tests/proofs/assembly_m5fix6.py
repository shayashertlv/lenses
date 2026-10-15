"""$0 floor-matte policy proof on unchanged owner job inputs and copied GLBs.

The policy run receives only the existing job's photo/view/candidate/matte
fields. Held-out visual/history choices are read only AFTER that result is
saved and hashed. A subsequent explicit-mode control measures the fit-time
change on the same model, photos and workers; it does not edit the job.
"""
from __future__ import annotations

import os
if __name__ == '__main__':
    os.environ.update({k:'1' for k in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS')})

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import time

from PIL import Image

from glasses3d.agent import procs
from glasses3d.g3d_host import compare as C, fit_views

ROOT=Path(__file__).resolve().parents[3]
DEFAULT_MANIFEST=ROOT/'glasses3d/data/assembly/m5-fix-6/input-baseline.json'
METHOD=('Preserve explicit job matte modes and existing optional-lens open_frame construction; otherwise fit '
        'default and glossy_floor identically, prefer an unchanged-gate pass, then higher native silhouette IoU, '
        'with exact ties choosing default.')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def snapshot_product(spec):
    root=Path(spec['job_root'])
    job_path=root/'job.json'
    if sha(job_path)!=spec['job_sha256']:
        raise ValueError('job definition differs from the independently recorded baseline')
    job=read(job_path)
    model=Path(spec['model']['path'])
    if sha(model)!=spec['model']['sha256']:
        raise ValueError('model differs from the independently recorded baseline')
    files={str(job_path):sha(job_path),str(model):sha(model)}
    for row in job['images']:
        photo=(root/row['file']).resolve()
        if not photo.is_relative_to(root.resolve()) or sha(photo)!=row['sha256']:
            raise ValueError('photo escaped or differs from the immutable job input')
        files[str(photo)]=sha(photo)
    return job,files


def verify_snapshot(files):
    if files!={path:sha(path) for path in files}:
        raise ValueError('a source input changed during the proof')


def compact_record(row):
    return {key:row.get(key) for key in ('status','refusal','code','flags','matte_mode','matte','iou','iou_lens_optional',
        'contour_mean_px','contour_p90_px','contour_mean_mm','usability','camera','theta_deg','theta_observed',
        'loss','loss_common_level','seconds','overlay')}


def comparison_rows(policy,reference_modes):
    rows={}
    if set(policy['images'])!=set(reference_modes):
        raise ValueError('reference controls must name every eligible job photo exactly once')
    for name,row in policy['images'].items():
        selection=row.get('matte_selection')
        if not selection or selection.get('schema')!='g3d.matte_selection/1':
            raise ValueError(f'{name}: production fit omitted matte-selection evidence')
        chosen=row.get('matte_mode')
        if selection.get('chosen')!=chosen:
            raise ValueError(f'{name}: selection and fitted matte disagree')
        alternatives=row.get('matte_alternatives') or selection.get('alternatives') or {}
        if selection.get('mode')=='automatic_fit_comparison' and set(alternatives)!={'default','glossy_floor'}:
            raise ValueError(f'{name}: both automatic alternative records must remain available')
        rows[name]={'selection':selection,'selected_fit':compact_record(row),
                    'alternatives':{mode:compact_record(rec) for mode,rec in alternatives.items()},
                    'held_out_reference':reference_modes[name],
                    'agrees_with_reference':chosen==reference_modes[name]}
    return rows


def fit_run(model,images,views,candidates,directory,workers,modes,phase):
    t0=time.perf_counter()
    print(json.dumps({'phase':phase,'started_at':datetime.now(timezone.utc).isoformat(),
                      'images':list(images),'workers':workers}),flush=True)
    result=fit_views.fit_views(model,images,views,directory,workers=workers,candidates=candidates,
                               matte_modes=modes,log=lambda line:print(line,flush=True))
    elapsed=time.perf_counter()-t0
    print(json.dumps({'phase':phase,'ended_at':datetime.now(timezone.utc).isoformat(),
                      'wall_seconds':elapsed}),flush=True)
    return result,elapsed


def run_product(name,spec,out,workers):
    job,source_files=snapshot_product(spec)
    folder=out/name
    (folder/'images').mkdir(parents=True)
    model=folder/'source.glb'
    shutil.copyfile(spec['model']['path'],model)
    images,views,candidates,declared={}, {}, {}, {}
    photo_facts={}
    for row in job['images']:
        original=Path(spec['job_root'])/row['file']
        target=folder/'images'/row['original_name']
        if target.exists():
            raise ValueError('original photo names are not unique')
        shutil.copyfile(original,target)
        if sha(target)!=row['sha256']:
            raise ValueError('photo copy changed')
        key=target.name
        images[key],views[key]=target,row['view_kind']
        candidates[key]=tuple(row['fit_kinds'])
        # Only the unchanged job's own declaration reaches the policy phase.
        declared[key]=row.get('matte_mode')
        photo_facts[key]={'source':str(original),'sha256':row['sha256'],'view_kind':row['view_kind'],
                         'side':row['side'],'existing_job_matte_mode':declared[key]}
    if sha(model)!=spec['model']['sha256']:
        raise ValueError('model copy changed')
    inputs={'product':name,'source_job':spec['job_root'],'source_files':source_files,
            'photos':photo_facts,'model':spec['model'],'workers':workers,
            'policy_method_predeclared':METHOD,'held_out_labels_passed_to_policy':False,'paid_calls':0}
    C.write_json(folder/'inputs.json',inputs)
    policy,policy_seconds=fit_run(model,images,views,candidates,folder/'policy',workers,declared,name+':policy')
    policy_sha=sha(folder/'policy/cameras.json')
    verify_snapshot(source_files)
    # The held-out reference first enters the computation here, after the policy is frozen.
    reference_modes=spec['reference_mattes']
    comparisons=comparison_rows(policy,reference_modes)
    C.write_json(folder/'held-out-comparison.json',{'policy_sha256':policy_sha,
                 'reference_sources':spec['reference_sources'],'images':comparisons})
    baseline,baseline_seconds=fit_run(model,images,views,candidates,folder/'baseline',workers,
                                     reference_modes,name+':baseline')
    if sha(folder/'policy/cameras.json')!=policy_sha:
        raise ValueError('policy evidence changed after inspecting held-out controls')
    verify_snapshot(source_files)
    for key,row in photo_facts.items():
        if sha(images[key])!=row['sha256']:
            raise ValueError('copied photo changed during fitting')
    if sha(model)!=spec['model']['sha256']:
        raise ValueError('copied model changed during fitting')
    tiles,alternative_tiles=[],[]
    for key in sorted(images,key=lambda value:(views[value]!='front',value)):
        row=policy['images'][key]
        overlay=Path(row['overlay']) if row.get('overlay') else images[key]
        text=f"{name} {key}: chosen {row.get('matte_mode')}; reference {reference_modes[key]}; {row['status']}"
        tiles.append(C.labelled(C.fit_into(Image.open(overlay).convert('RGBA'),1000,750),text))
        for mode,alternative in comparisons[key]['alternatives'].items():
            source=Path(alternative['overlay']) if alternative.get('overlay') else images[key]
            label=f"{name} {key}: {mode}; {alternative.get('status')}; IoU {alternative.get('iou')}; chosen={mode==row.get('matte_mode')}"
            alternative_tiles.append(C.labelled(C.fit_into(Image.open(source).convert('RGBA'),1000,750),label))
    sheet=folder/'policy-overlays.png'
    C.grid(tiles,2).convert('RGB').save(sheet)
    alternatives_sheet=folder/'policy-alternatives.png'
    C.grid(alternative_tiles,2).convert('RGB').save(alternatives_sheet)
    disagreements=[key for key,row in comparisons.items() if not row['agrees_with_reference']]
    return {'product':name,'source_bytes_unchanged':True,'model_sha256':sha(model),'photos':photo_facts,
        'policy_method_predeclared':METHOD,'held_out_labels_passed_to_policy':False,
        'policy_cameras':str(folder/'policy/cameras.json'),'policy_sha256':policy_sha,
        'baseline_cameras':str(folder/'baseline/cameras.json'),'baseline_sha256':sha(folder/'baseline/cameras.json'),
        'comparison_sheet':str(sheet),'alternatives_sheet':str(alternatives_sheet),
        'images':comparisons,'disagreements':disagreements,
        'timing':{'policy_wall_seconds':policy_seconds,'baseline_wall_seconds':baseline_seconds,
                  'delta_seconds':policy_seconds-baseline_seconds,'ratio':policy_seconds/baseline_seconds,
                  'workers_both_phases':workers,
                  'meaning':'Measured successive whole fits on identical copied inputs and worker count; '
                            'baseline uses historical/visual reference modes only after policy evidence freezes. '
                            'Not a guarantee of future runtime; cache/scheduling variation remains.',
                  'policy_reported_seconds':policy.get('seconds'),'baseline_reported_seconds':baseline.get('seconds')},
        'all_photos_retained':len(comparisons)==len(job['images']),
        'unusable_photos':[key for key,row in policy['images'].items() if row.get('status')!='ok'],'paid_calls':0}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--manifest',type=Path,default=DEFAULT_MANIFEST)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--workers',type=int,required=True)
    parser.add_argument('--products',nargs='+',choices=('prada','oakley'),default=['prada','oakley'])
    args=parser.parse_args()
    if not 1<=args.workers<=16:
        parser.error('--workers must be1..16; root must size it from free commit before launch')
    args.out.mkdir(parents=True,exist_ok=False)
    manifest=read(args.manifest)
    manifest_sha=sha(args.manifest)
    source_hashes={key:sha(ROOT/'glasses3d/g3d_host'/key) for key in ('fit_views.py','matte.py','hinge_fit.py','cameras.py')}
    start=datetime.now(timezone.utc).isoformat()
    host_qos=procs.opt_out_self()
    products={}
    with procs.DescendantsOptOut() as qos:
        for name in args.products:
            products[name]=run_product(name,manifest['products'][name],args.out,args.workers)
            C.write_json(args.out/'partial.json',products)
    if sha(args.manifest)!=manifest_sha or source_hashes!={key:sha(ROOT/'glasses3d/g3d_host'/key) for key in source_hashes}:
        raise ValueError('proof baseline or implementation changed during fitting')
    oakley_matches='oakley' not in products or not products['oakley']['disagreements']
    status='passed' if oakley_matches else 'oakley_matte_mismatch'
    report={'status':status,'started_at':start,'ended_at':datetime.now(timezone.utc).isoformat(),
            'manifest':str(args.manifest),'manifest_sha256':manifest_sha,'source_code_sha256':source_hashes,
            'products':products,'oakley_matches_actual_run4':oakley_matches,
            'prada_disagreements_reported_without_tuning':products.get('prada',{}).get('disagreements',[]),
            'ecoqos':{'host':host_qos,'workers':qos.opted_out},'provider_calls':0,'actual_spend_usd':0}
    C.write_json(args.out/'report.json',report)
    print(json.dumps({'status':status,'report':str(args.out/'report.json'),
                      'disagreements':{name:p['disagreements'] for name,p in products.items()},'paid_calls':0}),flush=True)
    return 0 if oakley_matches else 1


if __name__=='__main__':
    raise SystemExit(main())
