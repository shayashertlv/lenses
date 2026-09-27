"""Bounded cached-geometry optics comparison on one frozen existing mask branch.

This diagnostic does not alter geometry, select new pixels, call an API, or
claim exhaustive material recovery. Both lighting families use identical source
arrays and evaluation domains. Three starts remain mandatory under fit policy.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import io
import json
from pathlib import Path
import time

import numpy as np

from reconstruction.appearance_selection import select_appearance
from reconstruction.joint_photo_lens_fit import JointPhotoLensFitPolicy, fit_joint_photo_lens_candidates
from reconstruction.photo_lens_fit import PhotoLensFitPolicy


def _load(path, expected=None):
    raw = Path(path).read_bytes()
    if expected is not None and hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError('Saved pilot source hash mismatch: '+str(path))
    return json.loads(raw)


def _write(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def run_pilot(stage_report, priors_path, output, *, maximum_evaluations=30):
    stage_report, priors_path, output = (Path(p).resolve() for p in (stage_report, priors_path, output))
    if output.exists() and any(output.iterdir()):
        raise ValueError('Pilot requires a fresh output directory')
    started = time.monotonic()
    stage = _load(stage_report)
    folder = stage_report.parent
    source_request = _load(folder/'request.json')
    fit = _load(folder/stage['fit']['path'], stage['fit']['sha256'])
    selected = select_appearance(fit, stage)['selected']
    if selected is None:
        raise ValueError('Saved stage has no baseline candidate to freeze')
    baseline = next(c for c in fit['candidates'] if c['candidate_id'] == selected['candidate_id'])
    pins = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (stage_report, priors_path, folder/'request.json')}
    input_receipt = stage['inputs']
    groups, branch = [], []
    for relative in input_receipt['groups'].values():
        path = folder/relative
        stored = _load(path, input_receipt['artifacts'][relative])
        gid = stored['surface_binding']['material_group_id']
        wanted = {r['observation_id'] for r in baseline['groups'][gid]['observations']}
        rows = []
        for row in stored['observations']:
            if row['id'] not in wanted:
                continue
            array_path = path.parent/row['arrays']['path']
            raw = array_path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != row['arrays']['sha256']:
                raise ValueError('Cached observation arrays changed')
            with np.load(io.BytesIO(raw), allow_pickle=False) as archive:
                arrays = {name: archive[name].copy() for name in archive.files}
            observation = {k: v for k, v in row.items() if k != 'arrays'}
            observation.update(arrays)
            rows.append(observation)
            branch.append({'group_id': gid, 'observation_id': row['id'], 'photo_id': row['photo_id'],
                           'source_sha256': row['source_sha256'], 'array_sha256': row['arrays']['sha256'],
                           'samples': len(arrays['code_rgb'])})
        if {r['id'] for r in rows} != wanted:
            raise ValueError('Frozen baseline branch does not match stored observations')
        groups.append({'surface_binding': stored['surface_binding'], 'observations': rows})
    prior = _load(priors_path)
    photo = PhotoLensFitPolicy(**source_request['policy']['photo_policy'])
    photo = replace(photo, families=('gradient_tint', 'gradient_angular_mirror'),
                    lighting_families=('constant', 'semantic_softbox'), max_nfev=maximum_evaluations,
                    starts=3, roughness_values=(photo.roughness_values[0],), maximum_mask_branches=1)
    policy = JointPhotoLensFitPolicy(photo_policy=photo, maximum_joint_mask_branches=1,
                                    maximum_optimization_runs=12, jacobian_mode='finite_difference')
    output.mkdir(parents=True, exist_ok=True)
    _write(output/'request.json', {'method': 'frozen_branch_semantic_optics_pilot_v1',
           'source_stage': str(stage_report), 'source_candidate_id': baseline['candidate_id'],
           'input_sha256': pins, 'frozen_branch': branch,
           'scope': 'one_preselected_mask_branch_identical_samples_across_current_candidates_not_exhaustive',
           'maximum_evaluations_per_start': maximum_evaluations})
    def progress(event):
        if event['event'] in ('planned', 'completed'):
            print(json.dumps(event), flush=True)
    result = fit_joint_photo_lens_candidates(groups, policy=policy, appearance_priors=prior,
                                            progress=progress, checkpoint_dir=output/'checkpoints')
    _write(output/'fit.json', result)
    best = {}
    for candidate in result['candidates']:
        family = next(iter(candidate['groups'].values()))['family']
        key = family+'/'+candidate['assumptions']['lighting']
        def rank(row):
            return row['optimizer']['objective_including_priors']
        if key not in best or rank(candidate) < rank(best[key]):
            best[key] = candidate
    summaries, descriptor_candidates = [], []
    for label, candidate in sorted(best.items()):
        metrics = [m['validation']['mean_absolute_interval_error_codes'] for g in candidate['groups'].values()
                   for m in g['photo_metrics'] if m['validation']['points']]
        summaries.append({'configuration': label, 'candidate_id': candidate['candidate_id'],
                          'worst_region_validation_mean_codes': max(metrics),
                          'mean_region_validation_mean_codes': float(np.mean(metrics)),
                          'optimizer': candidate['optimizer'], 'photo_policy_status': candidate['photo_policy_status'],
                          'shared_nuisance_by_photo': candidate['shared_nuisance_by_photo']})
        descriptor_candidates.append({'candidate_id': candidate['candidate_id'], 'origin': 'frozen_branch_numerical_pilot',
                                      'configuration': label,
                                      'family_assignment': {gid: g['family'] for gid, g in candidate['groups'].items()},
                                      'appearances': {gid: g['appearance'] for gid, g in candidate['groups'].items()}})
    _write(output/'candidates.json', descriptor_candidates)
    if any(hashlib.sha256(Path(path).read_bytes()).hexdigest() != digest for path, digest in pins.items()):
        raise ValueError('Pilot source changed while fitting')
    summary = {'method': 'frozen_branch_semantic_optics_pilot_v1', 'seconds': time.monotonic()-started,
               'source_candidate_id': baseline['candidate_id'], 'frozen_branch': branch,
               'candidates': summaries, 'runs': result['exploration'], 'accepted': False,
               'limitations': ['One existing selected mask branch only; no exhaustive branch search.',
                               'All current fits use identical samples; original saved scores have a different optimization scope.',
                               'Full-photo semantic and numerical priors make validation conditional, not an independent holdout.',
                               'Nonconverged local fits remain explicit; numerical photo score is not AR appearance quality.']}
    _write(output/'summary.json', summary)
    return summary


def export_pilot_candidates(stage_report, output, *, width_mm=145.):
    """Export the four diagnostic descriptors against their exact source groups."""
    from reconstruction.photo_lens_stage import load_optical_fit_inputs
    from reconstruction.optical_group_asset import write_optical_group_candidate, read_optical_group_candidate
    from reconstruction.group_photo_lens_inputs import verify_group_preview_geometry
    output, stage_report = Path(output).resolve(), Path(stage_report).resolve()
    request = _load(stage_report.parent/'request.json')
    inputs = load_optical_fit_inputs(Path(request['preparation_report']), Path(request['region_report']),
              maximum_samples_per_hypothesis=request['maximum_samples'],
              group_photo_exclusions=request.get('group_photo_exclusions'))
    candidates = _load(output/'candidates.json')
    cases = []
    (output/'models').mkdir(exist_ok=True)
    for candidate in candidates:
        path = output/'models'/(candidate['candidate_id']+'.glb')
        if path.exists():
            raise ValueError('Pilot export requires fresh model paths')
        receipt = write_optical_group_candidate(inputs['source'], path,
              [{'prepared': group, 'appearance': candidate['appearances'][gid]}
               for gid, group in sorted(inputs['prepared_groups'].items())],
              source_sha256=inputs['preparation']['source_sha256'],
              provenance={'method': 'frozen_branch_semantic_optics_pilot_v1',
                          'configuration': candidate['configuration'], 'candidate_id': candidate['candidate_id'],
                          'accepted': False})
        verify_group_preview_geometry(inputs['export_receipt'], receipt)
        read_optical_group_candidate(path, receipt, expected_sha256=receipt['output_sha256'])
        _write(path.with_suffix('.export.json'), receipt)
        cases.append({'id': candidate['candidate_id'], 'path': str(path),
                      'model_sha256': receipt['output_sha256'], 'groups': receipt['groups'], 'width_mm': width_mm})
    if any(hashlib.sha256(Path(path).read_bytes()).hexdigest() != digest for path, digest in inputs['pins'].items()):
        raise ValueError('Source changed during pilot export')
    manifest = {'schema_version': 1, 'cases': cases}
    _write(output/'runtime-manifest.json', manifest)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage-report', type=Path, required=True)
    parser.add_argument('--priors', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--maximum-evaluations', type=int, default=30)
    parser.add_argument('--export-models', action='store_true')
    parser.add_argument('--export-existing', action='store_true', help='Export a previously completed pilot without refitting')
    args = parser.parse_args(argv)
    result = (_load(args.output/'summary.json') if args.export_existing else
              run_pilot(args.stage_report, args.priors, args.output, maximum_evaluations=args.maximum_evaluations))
    if args.export_models or args.export_existing:
        export_pilot_candidates(args.stage_report, args.output)
    print(json.dumps({'seconds': result['seconds'], 'candidates': result['candidates']}, indent=2))


if __name__ == '__main__':
    main()
