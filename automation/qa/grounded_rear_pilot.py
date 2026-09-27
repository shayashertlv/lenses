"""Exercise frozen image eligibility and joint rear response on cached rays."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import time

import numpy as np

from reconstruction.appearance_anchors import sample_appearance_anchors, compile_material_priors
from reconstruction.grounded_fit_support import freeze_grounded_fit_support
from reconstruction.image_appearance_evidence import ground_semantic_regions, sample_image_appearance_evidence
from reconstruction.joint_photo_lens_fit import JointPhotoLensFitPolicy, fit_joint_photo_lens_candidates
from reconstruction.photo_apertures import OfflineLensApertureEngine
from reconstruction.photo_lens_fit import PhotoLensFitPolicy
from reconstruction.rear_correspondence import collect_rear_transmission_anchors
from qa.semantic_optics_pilot import _load, _write, export_pilot_candidates


def run(stage_report, semantic_path, output, *, maximum_evaluations=45):
    stage_report, semantic_path, output=(Path(p).resolve() for p in (stage_report,semantic_path,output))
    if output.exists() and any(output.iterdir()):
        raise ValueError('Use a fresh pilot output')
    output.mkdir(parents=True,exist_ok=True);started=time.monotonic()
    stage=_load(stage_report);semantic=_load(semantic_path);groups=[]
    for relative in stage['inputs']['groups'].values():
        path=stage_report.parent/relative;stored=_load(path,stage['inputs']['artifacts'][relative]);rows=[]
        for row in stored['observations']:
            raw=(path.parent/row['arrays']['path']).read_bytes()
            if hashlib.sha256(raw).hexdigest()!=row['arrays']['sha256']:
                raise ValueError('Cached observation bytes changed')
            with np.load(io.BytesIO(raw),allow_pickle=False) as archive:
                arrays={k:archive[k].copy() for k in archive.files}
            rows.append({**{k:v for k,v in row.items() if k!='arrays'},**arrays})
        groups.append({'surface_binding':stored['surface_binding'],'observations':rows})
    grounding=ground_semantic_regions(semantic,OfflineLensApertureEngine(Path('data/models/glasses-detector-v1')),output/'grounding')
    # This fixture's source intake explicitly labels image-3 as the rear view.
    evidence=sample_image_appearance_evidence(semantic,grounding,view_labels={'front':'front','angled':'angled','image-3':'back'})
    anchors=sample_appearance_anchors(groups,semantic,grounding=grounding,image_evidence=evidence)
    priors=compile_material_priors(semantic,anchors)
    priors['frozen_image_fit_support']=freeze_grounded_fit_support(semantic,grounding)
    contrasts=collect_rear_transmission_anchors(semantic,grounding,groups)
    for gid,rows in contrasts['anchors_by_group'].items():priors['groups'][gid]['transmission_anchors']=rows[:16]
    for name,value in [('image-evidence',evidence),('anchors',anchors),('priors',priors),('rear-correspondences',contrasts)]:_write(output/(name+'.json'),value)
    policy=JointPhotoLensFitPolicy(photo_policy=PhotoLensFitPolicy(families=('colored_mirror','angular_mirror'),
        lighting_families=('semantic_softbox',),roughness_values=(.05,),max_nfev=maximum_evaluations),
        maximum_joint_mask_branches=4,maximum_optimization_runs=48,mask_search_mode='conditional_seed_beam',conditional_beam_width=4)
    reports={};candidates=[]
    for mode in ('front_only','front_and_rear'):
        prior=deepcopy(priors)
        if mode=='front_only':
            for group in prior['groups'].values():group.pop('rear_image_constraints',None)
        report=fit_joint_photo_lens_candidates(groups,policy=policy,appearance_priors=prior,checkpoint_dir=output/mode/'checkpoints')
        _write(output/(mode+'.json'),report);reports[mode]=report
        def rank(c):
            values=[m['validation']['mean_absolute_interval_error_codes'] for g in c['groups'].values() for m in g['photo_metrics'] if m['validation']['points']]
            return max(values,default=float('inf')),c['optimizer']['objective_including_priors']
        keys=sorted({(next(iter(c['groups'].values()))['family'],c['assumptions'].get('rear_response_hypothesis','legacy')) for c in report['candidates']})
        for family,response in keys:
            best=min((c for c in report['candidates'] if all(g['family']==family for g in c['groups'].values())
                and c['assumptions'].get('rear_response_hypothesis','legacy')==response),key=rank)
            candidates.append({'candidate_id':mode+'-'+best['candidate_id'],'configuration':mode+'/'+family+'/'+response,
                'rear_response_hypothesis':response,
                'family_assignment':{g:r['family'] for g,r in best['groups'].items()},
                'appearances':{g:r['appearance'] for g,r in best['groups'].items()},
                'source_candidate_id':best['candidate_id'],'validation_codes':rank(best)[0],
                'optimizer':best['optimizer'],'nuisance':best['shared_nuisance_by_photo']})
    summary={'seconds':time.monotonic()-started,'accepted':False,'candidates':candidates,
        'rear_constraints':{g:len(v.get('rear_image_constraints',[])) for g,v in priors['groups'].items()},
        'contrast_anchors':{g:len(v) for g,v in contrasts['anchors_by_group'].items()},
        'coverage':reports['front_and_rear']['coverage'],
        'limitations':['Conditional image intervals and uncertain rear incidence do not identify unique optical material.',
                      'Pilot front rays use existing registered geometry; no new rear camera is invented.']}
    _write(output/'candidates.json',candidates);_write(output/'summary.json',summary)
    export_pilot_candidates(stage_report,output,width_mm=138.)
    return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage-report',type=Path,required=True);parser.add_argument('--semantics',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--maximum-evaluations',type=int,default=45)
    args=parser.parse_args();summary=run(args.stage_report,args.semantics,args.output,maximum_evaluations=args.maximum_evaluations)
    print(json.dumps({k:summary[k] for k in ('seconds','rear_constraints','contrast_anchors')},indent=2))
