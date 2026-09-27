"""Joint photographic lens fit and source-bound diagnostic GLB export.

This stage shares photographic illumination across groups while preserving
independent optical descriptors. A preview is an explicit display choice, never
an accepted material. Completed optimizer starts are recoverable; incomplete
stage attempts remain on disk and completed output is verified before reuse.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat

import numpy as np

from .job import _job_lock, _read, _write, _sha, _verify
from .joint_photo_lens_fit import JointPhotoLensFitPolicy, fit_joint_photo_lens_candidates
from .optical_asset import write_optical_candidate
from .photo_lens_stage import load_optical_fit_inputs, save_group_observations
from .refine_photos import implementation_manifest


def _plain(value):
    return json.loads(json.dumps(value, sort_keys=True, allow_nan=False))


def _ordinary(path):
    """Inspect the directory entry itself before following any filesystem link."""
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise ValueError('Joint-stage paths must not be symlinks or reparse points')
    if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
        raise ValueError('Joint-stage paths must be ordinary files or directories')
    return info


def _guard_path(output, path):
    if not path.is_relative_to(output):
        raise ValueError('Joint-stage path escapes output')
    for entry in (path, *path.parents):
        try:
            _ordinary(entry)
        except FileNotFoundError:
            pass
        if entry == output:
            break
    if not path.resolve().is_relative_to(output.resolve()):
        raise ValueError('Joint-stage path resolves outside output')


def _output_files(output, folder=None):
    _guard_path(output, folder or output)
    if not output.exists():
        return []
    pending, files = [folder or output], []
    while pending:
        directory = pending.pop()
        for path in directory.iterdir():
            info = _ordinary(path)
            if not path.resolve().is_relative_to(output.resolve()):
                raise ValueError('Joint-stage artifact resolves outside output')
            if stat.S_ISDIR(info.st_mode):
                pending.append(path)
            else:
                files.append(path)
    return sorted(files)


def _safe_write(output, path, value):
    _guard_path(output, path)
    _guard_path(output, path.with_name(path.name+'.next'))
    _write(path, value)


def _inventory(output, folder=None):
    result = {}
    for path in _output_files(output, folder):
        relative = path.relative_to(output).as_posix()
        if relative not in ('.lock', 'report.json', 'receipt.json'):
            result[relative] = _sha(path)
    return result


def _verify_saved_inputs(output, saved, observations):
    if not isinstance(saved, dict) or set(saved) != {'groups', 'observation_report', 'artifacts'}:
        raise ValueError('Invalid saved input receipt schema')
    groups = observations['groups']
    if not isinstance(saved['groups'], dict) or set(saved['groups']) != {str(part) for part in groups}:
        raise ValueError('Saved input groups differ from regenerated observations')
    report_path = saved['observation_report']
    if not isinstance(report_path, str) or not re.fullmatch(r'inputs/attempt-\d{3,}/observation-report\.json', report_path):
        raise ValueError('Invalid saved observation report pointer')
    folder = (output/report_path).parent
    expected = {report_path}
    if _read(output/report_path) != _plain(observations['report']):
        raise ValueError('Saved observation report differs from regenerated inputs')
    _verify(output, saved['artifacts'])
    for part, group in groups.items():
        group_folder = folder/f'part-{part:03d}'
        pointer = (group_folder/'observations.json').relative_to(output).as_posix()
        if saved['groups'][str(part)] != pointer:
            raise ValueError('Saved input group pointer differs from its bound inventory')
        expected.add(pointer)
        record = _read(output/pointer)
        if (not isinstance(record, dict) or set(record) != {'surface_binding', 'observations'}
                or record['surface_binding'] != _plain(group['surface_binding'])
                or not isinstance(record['observations'], list)
                or len(record['observations']) != len(group['observations'])):
            raise ValueError('Saved group binding differs from regenerated inputs')
        for index, (row, observation) in enumerate(zip(record['observations'], group['observations'])):
            arrays = {key: value for key, value in observation.items() if isinstance(value, np.ndarray)}
            name = f'observation-{index:03d}.npz'
            path = group_folder/name
            relative = path.relative_to(output).as_posix()
            expected.add(relative)
            raw = path.read_bytes()
            metadata = {key: value for key, value in observation.items() if key not in arrays}
            metadata['arrays'] = {'path': name, 'sha256': hashlib.sha256(raw).hexdigest()}
            if row != _plain(metadata):
                raise ValueError('Saved observation metadata differs from regenerated inputs')
            with np.load(io.BytesIO(raw), allow_pickle=False) as archive:
                if set(archive.files) != set(arrays) or any(
                        archive[key].dtype != values.dtype or not np.array_equal(archive[key], values, equal_nan=True)
                        for key, values in arrays.items()):
                    raise ValueError('Saved observation arrays differ from regenerated inputs')
    if set(saved['artifacts']) != expected or _inventory(output, folder) != saved['artifacts']:
        raise ValueError('Saved input inventory differs from its complete bound artifacts')


def joint_preview_representatives(fit, *, include_material_relations=False):
    """One candidate per family/relation hypothesis; no per-lens mixing.

    The default preserves the historical family-only helper contract. Stage
    export explicitly retains shared and independent material alternatives.
    """
    result = {}
    for candidate in fit['candidates']:
        assignment = tuple(sorted((group, value['family']) for group, value in candidate['groups'].items()))
        key = ((assignment, json.dumps(candidate.get('assumptions', {}).get('material_relation', {'mode': 'independent'}), sort_keys=True))
               if include_material_relations else assignment)
        if include_material_relations and candidate.get('assumptions',{}).get('rear_response_hypothesis'):
            key=(*key,candidate['assumptions']['rear_response_hypothesis'])
        def rank(row):
            measured = [metric['validation']['mean_absolute_interval_error_codes']
                for group in row['groups'].values() for metric in group['photo_metrics']
                if metric['validation']['points']]
            return (row['photo_policy_status'] != 'within_declared_policy', not row['optimizer']['converged'],
                    max(measured, default=float('inf')), row['optimizer']['objective_including_priors'], row['candidate_id'])
        if key not in result or rank(candidate) < rank(result[key]):
            result[key] = candidate
    return result


def run_joint_photo_lens_stage(preparation_report: Path, region_report: Path, output: Path, *,
                              policy=JointPhotoLensFitPolicy(), maximum_samples_per_hypothesis=256,
                              family_assignments=None, resume=False, group_photo_exclusions=None,
                              appearance_prior_report: Path | None = None):
    preparation_report, region_report = (Path(p).resolve() for p in (preparation_report, region_report))
    output = Path(os.path.abspath(output))
    _output_files(output)  # Reject existing junctions, including .lock, before opening anything for writing.
    output = output.resolve()
    if type(resume) is not bool:
        raise ValueError('resume must be boolean')
    if preparation_report.is_relative_to(output) or region_report.is_relative_to(output):
        raise ValueError('Joint fit output must not contain source preparation/region artifacts')
    appearance_prior_report = Path(appearance_prior_report).resolve() if appearance_prior_report is not None else None
    if appearance_prior_report is not None and appearance_prior_report.is_relative_to(output):
        raise ValueError('Joint fit output must not contain its appearance prior source')
    with _job_lock(output):
        _output_files(output)
        contents = [path for path in output.iterdir() if path.name != '.lock']
        if contents and not resume:
            raise ValueError('Use an empty joint fit directory or explicitly resume its unchanged request')
        implementation = implementation_manifest()
        inputs = load_optical_fit_inputs(preparation_report, region_report,
            maximum_samples_per_hypothesis=maximum_samples_per_hypothesis, group_photo_exclusions=group_photo_exclusions)
        preparation, source, model, surfaces, observations, pins = (
            inputs[key] for key in ('preparation', 'source', 'model', 'prepared_surfaces', 'observations', 'pins'))
        appearance_priors = None
        if appearance_prior_report is not None:
            # Read and hash the same captured bytes; a concurrent writer cannot
            # bind one document's parameters to another document's digest.
            raw_priors = appearance_prior_report.read_bytes()
            appearance_priors = json.loads(raw_priors)
            pins = dict(pins, **{str(appearance_prior_report): hashlib.sha256(raw_priors).hexdigest()})
        if any(Path(path).resolve().is_relative_to(output) for path in (*pins, *inputs.get('optional_original_sha256', {}))):
            raise ValueError('Joint fit output must not contain any source dependency')
        request = _plain({'schema_version': 1, 'method': 'joint_photo_lens_candidate_stage_v1',
            'preparation_report': str(preparation_report), 'region_report': str(region_report), 'input_sha256': pins,
            'implementation': implementation, 'policy': asdict(policy), 'maximum_samples': maximum_samples_per_hypothesis,
            'family_assignments': family_assignments, 'group_photo_exclusions': group_photo_exclusions})
        if appearance_prior_report is not None:
            request['appearance_prior_report'] = str(appearance_prior_report)
        if inputs.get('profile') == 'effective_optical_group_v1_experiment':
            request.update(optical_profile=inputs['profile'], group_inventory=inputs['group_inventory'],
                           optional_original_sha256=inputs['optional_original_sha256'])
        request_path = output / 'request.json'
        if request_path.exists():
            if _read(request_path) != request:
                raise ValueError('Joint fit source/settings/implementation changed; use a new output directory')
        elif contents:
            raise ValueError('Nonempty output has no joint-stage request; preserve it and use a new directory')
        else:
            _safe_write(output, request_path, request)
        if (output / 'receipt.json').exists():
            receipt = _read(output / 'receipt.json')
            if _sha(output / 'report.json') != receipt['report_sha256']:
                raise ValueError('Completed joint-stage report changed')
            report = _read(output / 'report.json')
            _verify(output, report['artifacts'])
            if _inventory(output) != report['artifacts']:
                raise ValueError('Completed joint-stage artifact inventory changed')
            return report

        input_receipt = output / 'inputs.json'
        if input_receipt.exists():
            saved_inputs = _read(input_receipt)
            _verify_saved_inputs(output, saved_inputs, observations)
        else:
            number = 1
            while (output / 'inputs' / f'attempt-{number:03d}').exists():
                number += 1
            input_folder = output / 'inputs' / f'attempt-{number:03d}'
            _guard_path(output, input_folder)
            input_folder.mkdir(parents=True)
            _safe_write(output, input_folder / 'observation-report.json', observations['report'])
            group_paths = {}
            for part, group in observations['groups'].items():
                folder = input_folder / f'part-{part:03d}'
                save_group_observations(folder, group)
                group_paths[str(part)] = (folder / 'observations.json').relative_to(output).as_posix()
            saved_inputs = {'groups': group_paths, 'observation_report': (input_folder/'observation-report.json').relative_to(output).as_posix(),
                            'artifacts': _inventory(output, input_folder)}
            _safe_write(output, input_receipt, saved_inputs)
        attempt = 1
        while (output / 'attempts' / f'attempt-{attempt:03d}').exists():
            attempt += 1
        attempt_folder = output / 'attempts' / f'attempt-{attempt:03d}'
        _guard_path(output, attempt_folder)
        attempt_folder.mkdir(parents=True)
        groups = [group for _, group in sorted(observations['groups'].items())]
        report = {'schema_version': 1, 'method': request['method'], 'status': 'running', 'accepted': False,
            'quality_verdict': 'unmeasured', 'selected_material': None, 'input_sha256': pins, 'implementation': implementation,
            'inputs': saved_inputs, 'policy': request['policy'], 'previews': [],
            'preview_ranking': ['joint photo-policy match', 'optimizer converged', 'lowest worst-group/region validation mean',
                                'lowest joint objective including priors', 'candidate id'],
            'limitations': ['Each preview uses one complete joint candidate, not separately ranked lens fits.',
                'All descriptor roughness alternatives remain in the fit; preview uses its explicitly selected lowest roughness prior.',
                'Ranking reuses spatial validation and is not independent final evaluation.',
                'Shared lighting is a conditional field; exposure gauge is not measured calibration.',
                'Source groups, masks, cameras, rear content and articulation remain hypotheses.',
                'No material identification, product accuracy or AR acceptance follows from a completed fit.']}
        if appearance_prior_report is not None:
            report['appearance_prior_report'] = str(appearance_prior_report)
            report['validation_scope'] = 'conditional_on_full_photo_semantic_and_numeric_priors_not_independent_holdout'
        if 'optical_profile' in request:
            report.update(optical_profile=request['optical_profile'], group_inventory=request['group_inventory'],
                          optional_original_sha256=request['optional_original_sha256'])
        count = 0
        def progress(event):
            nonlocal count
            count += 1
            if count == 1 or count % 25 == 0:
                _safe_write(output, output / 'progress.json', event)
                print(json.dumps(event, allow_nan=False), flush=True)
        try:
            if not groups or any(not group['observations'] for group in groups):
                report.update(status='no_complete_preview', reason='At least one prepared optical group has no photo observations')
            else:
                fit = fit_joint_photo_lens_candidates(groups, policy=policy, family_assignments=family_assignments,
                    progress=progress, checkpoint_dir=output / 'checkpoints', appearance_priors=appearance_priors)
                _output_files(output)
                fit_path = attempt_folder / 'fit.json'; _safe_write(output, fit_path, fit)
                report['fit'] = {'path': fit_path.relative_to(output).as_posix(), 'sha256': _sha(fit_path),
                                 'status': fit['status'], 'candidates': len(fit['candidates'])}
                ids = {part: group['surface_binding']['material_group_id'] for part, group in observations['groups'].items()}
                for candidate in joint_preview_representatives(fit, include_material_relations=True).values():
                    assignment = tuple(sorted((group, value['family']) for group, value in candidate['groups'].items()))
                    if set(candidate['groups']) != set(ids.values()):
                        raise ValueError('Joint candidate does not cover every exported material group')
                    families = {family for _, family in assignment}
                    label = next(iter(families)) if len(families) == 1 else 'mixed-'+hashlib.sha256(json.dumps(assignment).encode()).hexdigest()[:12]
                    relation = candidate.get('assumptions', {}).get('material_relation', {'mode': 'independent'})
                    if relation['mode'] != 'independent':
                        label += '-paired-' + hashlib.sha256(json.dumps(relation, sort_keys=True).encode()).hexdigest()[:10]
                    rear_response=candidate.get('assumptions',{}).get('rear_response_hypothesis')
                    if rear_response:
                        label+='-'+rear_response.replace('_','-')
                    path = attempt_folder / f'preview-{label}.glb'
                    _guard_path(output, path)
                    provenance = {'method': 'joint_photo_family_preview_v1', 'candidate_id': candidate['candidate_id'],
                            'family_assignment': dict(assignment), 'fit_report_sha256': _sha(fit_path),
                            'preparation_report_sha256': pins[str(preparation_report)], 'prepared_glb_sha256': pins[str(model)],
                            'appearance_origin': 'joint_ensemble_display_prior_not_accepted_material',
                            'material_relation': relation,'rear_response_hypothesis':rear_response}
                    if inputs.get('profile') == 'effective_optical_group_v1_experiment':
                        from .optical_group_asset import write_optical_group_candidate, read_optical_group_candidate
                        from .group_photo_lens_inputs import verify_group_preview_geometry
                        replacement_groups = [{'prepared': group,
                            'appearance': candidate['groups'][gid]['appearance']}
                            for gid, group in sorted(inputs['prepared_groups'].items())]
                        receipt = write_optical_group_candidate(source, path, replacement_groups,
                            source_sha256=preparation['source_sha256'], provenance=provenance)
                        verify_group_preview_geometry(inputs['export_receipt'], receipt)
                        read_optical_group_candidate(path, receipt, expected_sha256=receipt['output_sha256'])
                    else:
                        replacements = [{'part_index': part, 'surfaces': [
                            {**surface, 'appearance': candidate['groups'][ids[part]]['appearance']} for surface in arrays]}
                            for part, arrays in surfaces.items()]
                        receipt = write_optical_candidate(source, path, replacements,
                            source_sha256=preparation['source_sha256'], provenance=provenance)
                    export_path = path.with_name(path.stem+'-export.json'); _safe_write(output, export_path, receipt)
                    report['previews'].append({'family_assignment': dict(assignment), 'candidate_id': candidate['candidate_id'],
                        'material_relation': relation,'rear_response_hypothesis':rear_response,
                        'status': 'diagnostic_preview_exported', 'path': path.relative_to(output).as_posix(), 'sha256': receipt['output_sha256'],
                        'export': {'path': export_path.relative_to(output).as_posix(), 'sha256': _sha(export_path)}})
                report['status'] = 'diagnostic_previews_available' if report['previews'] else 'no_complete_preview'
            if any(_sha(path) != value for path, value in pins.items()) or implementation_manifest() != implementation:
                raise ValueError('Source or implementation changed during joint photo fitting')
            if 'optical_profile' in request:
                from .group_photo_lens_inputs import verify_group_source_originals
                verify_group_source_originals(inputs)
            report['artifacts'] = _inventory(output)
            _safe_write(output, output / 'report.json', report)
            _safe_write(output, output / 'receipt.json', {'schema_version': 1, 'report_sha256': _sha(output / 'report.json')})
            return report
        except Exception as error:
            _safe_write(output, attempt_folder / 'failure.json', {'status': 'execution_failed', 'type': type(error).__name__, 'message': str(error),
                                                   'accepted': False, 'resume': 'Only with unchanged source/settings/implementation.'})
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('preparation', 'regions', 'output'):
        parser.add_argument('--'+name, required=True, type=Path)
    parser.add_argument('--maximum-optimization-runs', type=int, default=2430)
    parser.add_argument('--maximum-mask-branches', type=int, default=243)
    parser.add_argument('--mask-search-mode', choices=('exhaustive', 'conditional_seed_beam'), default='exhaustive')
    parser.add_argument('--conditional-beam-width', type=int, default=8)
    parser.add_argument('--maximum-samples', type=int, default=256)
    parser.add_argument('--jacobian-mode', choices=('finite_difference', 'analytic'), default='finite_difference')
    parser.add_argument('--appearance-priors', type=Path, help='Pinned semantic_material_priors_v1 report; inferred preferences remain separate from declared facts')
    parser.add_argument('--lighting', nargs='+', choices=('constant', 'smooth', 'semantic_softbox'))
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    from .photo_lens_fit import PhotoLensFitPolicy
    photo_policy = PhotoLensFitPolicy(lighting_families=tuple(args.lighting)) if args.lighting else PhotoLensFitPolicy()
    policy = JointPhotoLensFitPolicy(maximum_optimization_runs=args.maximum_optimization_runs,
                                    jacobian_mode=args.jacobian_mode, photo_policy=photo_policy,
                                    maximum_joint_mask_branches=args.maximum_mask_branches,
                                    mask_search_mode=args.mask_search_mode, conditional_beam_width=args.conditional_beam_width)
    result = run_joint_photo_lens_stage(args.preparation, args.regions, args.output,
        policy=policy, maximum_samples_per_hypothesis=args.maximum_samples, resume=args.resume,
        appearance_prior_report=args.appearance_priors)
    print(json.dumps({key: result[key] for key in ('status', 'quality_verdict', 'previews')}))


if __name__ == '__main__':
    main()
