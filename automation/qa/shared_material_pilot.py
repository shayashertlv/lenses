"""Compare paired/independent response on one immutable cached observation branch."""
from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import io
from pathlib import Path
import time

import numpy as np

from qa.semantic_optics_pilot import _load, _write, export_pilot_candidates
from reconstruction.appearance_selection import select_appearance
from reconstruction.joint_photo_lens_fit import JointPhotoLensFitPolicy, fit_joint_photo_lens_candidates
from reconstruction.material_relations import infer_material_relations
from reconstruction.photo_lens_fit import PhotoLensFitPolicy


def run(stage_report, priors_path, semantic_path, output, *, maximum_evaluations=40, conditional_mask_branches=None):
    stage_report, priors_path, semantic_path, output = (Path(p).resolve() for p in (stage_report, priors_path, semantic_path, output))
    if output.exists() and any(output.iterdir()):
        raise ValueError('Pilot output must be fresh')
    started = time.monotonic()
    stage, request = _load(stage_report), _load(stage_report.parent/'request.json')
    fit = _load(stage_report.parent/stage['fit']['path'], stage['fit']['sha256'])
    baseline = next(c for c in fit['candidates'] if c['candidate_id'] == select_appearance(fit, stage)['selected']['candidate_id'])
    inputs, groups, frozen = stage['inputs'], [], []
    for relative in inputs['groups'].values():
        path = stage_report.parent/relative
        stored = _load(path, inputs['artifacts'][relative])
        gid = stored['surface_binding']['material_group_id']
        wanted = {r['observation_id'] for r in baseline['groups'][gid]['observations']}
        observations = []
        for row in stored['observations']:
            if conditional_mask_branches is None and row['id'] not in wanted:
                continue
            raw = (path.parent/row['arrays']['path']).read_bytes()
            if hashlib.sha256(raw).hexdigest() != row['arrays']['sha256']:
                raise ValueError('Frozen observation bytes changed')
            with np.load(io.BytesIO(raw), allow_pickle=False) as archive:
                arrays = {k: archive[k].copy() for k in archive.files}
            observations.append({**{k:v for k,v in row.items() if k != 'arrays'}, **arrays})
            frozen.append({'group_id':gid, 'observation_id':row['id'], 'array_sha256':row['arrays']['sha256']})
        if conditional_mask_branches is None and {r['id'] for r in observations} != wanted:
            raise ValueError('Frozen observations do not match baseline')
        groups.append({'surface_binding':stored['surface_binding'], 'observations':observations})
    priors = _load(priors_path)
    priors['material_relations'] = infer_material_relations(_load(semantic_path), priors, [g['surface_binding'] for g in groups])
    if not priors['material_relations']:
        raise ValueError('No supported two-lens material hypothesis')
    photo = PhotoLensFitPolicy(**request['policy']['photo_policy'])
    photo = replace(photo, families=('uniform_tint','gradient_tint'), lighting_families=('semantic_softbox',),
        roughness_values=(photo.roughness_values[0],), starts=3, max_nfev=maximum_evaluations, maximum_mask_branches=1)
    branches = conditional_mask_branches or 1
    policy = JointPhotoLensFitPolicy(photo_policy=photo, maximum_joint_mask_branches=branches, maximum_optimization_runs=12*branches,
        mask_search_mode='conditional_seed_beam' if conditional_mask_branches else 'exhaustive')
    output.mkdir(parents=True, exist_ok=True)
    pins = {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (stage_report, priors_path, semantic_path)}
    _write(output/'request.json', {'method':'frozen_pair_material_comparison_v1', 'sources':pins,
        'frozen_branch':frozen, 'maximum_evaluations':maximum_evaluations, 'conditional_mask_branches':conditional_mask_branches})
    _write(output/'priors.json', priors)
    result = fit_joint_photo_lens_candidates(groups, policy=policy, appearance_priors=priors, checkpoint_dir=output/'checkpoints')
    _write(output/'fit.json',result)
    best = {}
    def rank(candidate):
        errors = [m['validation']['mean_absolute_interval_error_codes'] for g in candidate['groups'].values()
                  for m in g['photo_metrics'] if m['validation']['points']]
        return (max(errors, default=float('inf')), candidate['optimizer']['objective_including_priors'])
    for candidate in result['candidates']:
        label = next(iter(candidate['groups'].values()))['family'] + '/' + candidate['assumptions']['material_relation']['mode']
        if label not in best or rank(candidate) < rank(best[label]):
            best[label] = candidate
    summaries, descriptors = [], []
    for label,candidate in sorted(best.items()):
        errors = [m['validation']['mean_absolute_interval_error_codes'] for g in candidate['groups'].values()
                  for m in g['photo_metrics'] if m['validation']['points']]
        appearances = {gid:g['appearance'] for gid,g in candidate['groups'].items()}
        descriptors.append({'candidate_id':candidate['candidate_id'], 'configuration':label,
            'family_assignment':{gid:g['family'] for gid,g in candidate['groups'].items()}, 'appearances':appearances,
            'material_relation':candidate['assumptions']['material_relation']})
        summaries.append({'configuration':label, 'candidate_id':candidate['candidate_id'], 'optimizer':candidate['optimizer'],
            'worst_region_validation_mean_codes':max(errors,default=None),
            'material_descriptors_exactly_equal':len({str(v) for v in appearances.values()})==1,
            'density_by_group':{gid:a['optical_density_keyframes'] for gid,a in appearances.items()}})
    summary = {'method':'frozen_pair_material_comparison_v1', 'seconds':time.monotonic()-started,
        'frozen_branch':frozen, 'candidates':summaries, 'runs':result['exploration'], 'mask_search':result['mask_search'], 'accepted':False,
        'limitations':['All current candidates use the same supplied source arrays and spatial split; optional bounded mask search explicitly reports its approximation.',
            'Shared manufactured materials are a hypothesis; independent fits remain intact.',
            'Spatial validation under full-image priors is conditional, not independent visual evaluation.']}
    _write(output/'candidates.json',descriptors); _write(output/'summary.json',summary)
    if any(hashlib.sha256(Path(p).read_bytes()).hexdigest()!=s for p,s in pins.items()):
        raise ValueError('Pilot source changed')
    export_pilot_candidates(stage_report,output)
    return summary


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage-report',type=Path,required=True)
    parser.add_argument('--priors',type=Path,required=True)
    parser.add_argument('--semantics',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--maximum-evaluations',type=int,default=40)
    parser.add_argument('--conditional-mask-branches',type=int)
    args=parser.parse_args()
    value=run(args.stage_report,args.priors,args.semantics,args.output,maximum_evaluations=args.maximum_evaluations,
              conditional_mask_branches=args.conditional_mask_branches)
    print([(c['configuration'],c['worst_region_validation_mean_codes'],c['optimizer']['parameter_count']) for c in value['candidates']])
