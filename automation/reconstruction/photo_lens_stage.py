"""Fit conditional lens materials and export reproducible diagnostic previews.

This stage never chooses an accepted material. It preserves the complete bounded
ensemble and makes one explicitly labelled preview per family using a fixed
ranking rule. A missing material group prevents a complete preview rather than
silently receiving a guessed clear lens.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import io
import json
from pathlib import Path

import numpy as np

from .optical_asset import write_optical_candidate
from .photo_lens_fit import FAMILIES, PhotoLensFitPolicy, fit_photo_lens_candidates
from .photo_lens_observations import build_photo_lens_observations, _child
from .refine_photos import implementation_manifest


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _write(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def preview_representatives(fit):
    """One diagnostic per family; ranking is explicit and never acceptance."""
    result = {}
    for family in FAMILIES:
        candidates = [row for row in fit['candidates'] if row['assumptions']['family'] == family]
        if not candidates:
            continue
        def rank(row):
            validation = [m['validation']['mean_absolute_interval_error_codes'] for m in row['photo_measurements']
                          if m['validation']['points']]
            return (row['photo_policy_status'] != 'within_declared_policy', not row['optimizer']['converged'],
                    max(validation, default=float('inf')), row['optimizer']['objective_including_priors'],
                    row['assumptions']['roughness'], row['candidate_id'])
        result[family] = min(candidates, key=rank)
    return result


def load_optical_fit_inputs(preparation_report: Path, region_report: Path, *, maximum_samples_per_hypothesis=256,
                            group_photo_exclusions=None):
    """Shared source/attribute verification for independent and joint fitting.

    The exact exported geometry, observations and every input dependency are
    returned together. Callers must recheck these pins after fitting/export.
    """
    preparation_report, region_report = (Path(p).resolve() for p in (preparation_report, region_report))
    preparation_bytes = preparation_report.read_bytes()
    preparation = json.loads(preparation_bytes.decode('utf-8'))
    profile = preparation.get('optical_profile', 'front_sheet_v1')
    if profile == 'effective_optical_group_v1_experiment':
        from .group_photo_lens_inputs import load_group_optical_fit_inputs
        return load_group_optical_fit_inputs(preparation_report, region_report,
            preparation_bytes=preparation_bytes, maximum_samples_per_hypothesis=maximum_samples_per_hypothesis,
            group_photo_exclusions=group_photo_exclusions)
    if group_photo_exclusions:
        raise ValueError('Per-view group exclusions exist only for the effective optical group profile')
    if profile != 'front_sheet_v1':
        raise ValueError('Unknown optical preparation profile')
    if preparation['status'] != 'prepared_optical_candidate':
        raise ValueError('Photo lens fitting requires complete prepared candidate surfaces')
    folder = preparation_report.parent
    model = _child(folder, preparation['model']['path'])
    export = _child(folder, preparation['export']['path'])
    source = Path(preparation['source']).resolve()
    export_bytes = export.read_bytes()
    pins = {str(p): _sha(p) for p in (region_report, model, source)}
    pins[str(preparation_report)] = hashlib.sha256(preparation_bytes).hexdigest()
    pins[str(export)] = hashlib.sha256(export_bytes).hexdigest()
    if (pins[str(model)] != preparation['model']['sha256'] or pins[str(export)] != preparation['export']['sha256']
            or pins[str(source)] != preparation['source_sha256']):
        raise ValueError('Prepared model, export or source hash changed')
    export_receipt = json.loads(export_bytes.decode('utf-8'))
    if (export_receipt['source_sha256'] != preparation['source_sha256']
            or export_receipt['output_sha256'] != pins[str(model)]):
        raise ValueError('Preparation source lineage differs from exported surface binding')
    bindings = {row['surface_id']: row for row in export_receipt['surfaces']}
    prepared_surfaces, seen = {}, set()
    for part in preparation['parts']:
        group_id = part['source_part_index']
        if group_id in prepared_surfaces:
            raise ValueError('Duplicate preparation source part')
        prepared_surfaces[group_id] = []
        for surface in part['surfaces']:
            binding = bindings.get(surface['id'])
            if binding is None or surface['id'] in seen or binding['source_part_index'] != group_id:
                raise ValueError('Prepared surface identity differs from fitted export')
            seen.add(surface['id'])
            path = _child(folder, surface['path'])
            surface_bytes = path.read_bytes()
            pins[str(path)] = hashlib.sha256(surface_bytes).hexdigest()
            if pins[str(path)] != surface['sha256']:
                raise ValueError('Prepared surface arrays changed')
            with np.load(io.BytesIO(surface_bytes), allow_pickle=False) as archive:
                arrays = {key: archive[key].copy() for key in ('positions', 'normals', 'uv', 'indices')}
            for key, values in arrays.items():
                digest = hashlib.sha256(values.astype('<u4' if key == 'indices' else '<f4').tobytes()).hexdigest()
                if digest != binding['attribute_sha256'][key]:
                    raise ValueError(f'Prepared {key} arrays differ from actual fitted GLB')
            prepared_surfaces[group_id].append({'id': surface['id'], **arrays})
    if seen != bindings.keys() or set(prepared_surfaces) != set(export_receipt['replaced_parts']):
        raise ValueError('Preparation does not represent every fitted optical surface')
    observations = build_photo_lens_observations(model, export, region_report,
        maximum_samples_per_hypothesis=maximum_samples_per_hypothesis)
    for path, digest in observations['report']['input_sha256'].items():
        if path in pins and pins[path] != digest:
            raise ValueError('Observation dependency changed while preparing fit inputs')
        pins[path] = digest
    if any(_sha(path) != digest for path, digest in pins.items()):
        raise ValueError('Optical fit dependency changed while loading inputs')
    return {'profile': 'front_sheet_v1', 'preparation': preparation, 'source': source, 'model': model,
            'prepared_surfaces': prepared_surfaces, 'observations': observations, 'pins': pins}


def save_group_observations(group_folder: Path, group: dict):
    """Persist exact arrays and provenance with the same format for both fitters."""
    group_folder.mkdir()
    records = []
    for index, observation in enumerate(group['observations']):
        arrays = {key: value for key, value in observation.items() if isinstance(value, np.ndarray)}
        path = group_folder / f'observation-{index:03d}.npz'
        np.savez_compressed(path, **arrays)
        records.append({**{key: value for key, value in observation.items() if key not in arrays},
            'arrays': {'path': path.name, 'sha256': _sha(path)}})
    _write(group_folder / 'observations.json', {'surface_binding': group['surface_binding'], 'observations': records})
    return records


def run_photo_lens_stage(preparation_report: Path, region_report: Path, output: Path, *,
                         policy=PhotoLensFitPolicy(), maximum_samples_per_hypothesis=256):
    preparation_report, region_report, output = (Path(p).resolve() for p in (preparation_report, region_report, output))
    if output.exists() and any(output.iterdir()):
        raise ValueError('Use an empty optical fit attempt directory')
    implementation = implementation_manifest()
    inputs = load_optical_fit_inputs(preparation_report, region_report,
        maximum_samples_per_hypothesis=maximum_samples_per_hypothesis)
    if inputs['profile'] != 'front_sheet_v1':
        raise ValueError('Effective optical groups require the joint photo lens stage')
    preparation, source, model, prepared_surfaces, observations, pins = (
        inputs[key] for key in ('preparation', 'source', 'model', 'prepared_surfaces', 'observations', 'pins'))
    output.mkdir(parents=True, exist_ok=True)
    _write(output / 'observation-report.json', observations['report'])
    report = {'schema_version': 1, 'method': 'photo_lens_candidate_stage_v1', 'status': 'running',
        'quality_verdict': 'unmeasured', 'accepted': False, 'input_sha256': pins, 'implementation': implementation,
        'policy': asdict(policy), 'maximum_samples_per_hypothesis': maximum_samples_per_hypothesis,
        'groups': [], 'previews': [], 'selected_material': None,
        'preview_ranking': ['within photo policy', 'optimizer converged', 'lowest worst-region validation mean',
                            'lowest objective including priors', 'lowest roughness prior', 'candidate id'],
        'limitations': ['All fit candidates and mask alternatives remain in the group reports.',
            'Family preview representatives are display choices, not recovered or accepted product materials.',
            'Per-source-part material grouping and per-group illumination remain unverified hypotheses.',
            'Preview ranking reuses spatial validation measurements; it is not independent final evaluation.',
            'Selected representative roughness is a prior, not measured from photos.',
            'Actual AR runtime compatibility and appearance must be checked after export.']}
    fits, representatives = {}, {}
    for group_id, group in observations['groups'].items():
        print(f'Fitting conditional photo materials for source part {group_id}', flush=True)
        group_folder = output / f'part-{group_id:03d}'
        records = save_group_observations(group_folder, group)
        row = {'source_part_index': group_id, 'observations': len(records)}
        report['groups'].append(row)
        if not records:
            row['status'] = 'no_photo_observations'
            continue
        try:
            fit = fit_photo_lens_candidates(group['observations'], surface_binding=group['surface_binding'], policy=policy)
        except ValueError as error:
            row.update(status='unsupported_fit', reason=str(error))
            continue
        fits[group_id] = fit
        representatives[group_id] = preview_representatives(fit)
        _write(group_folder / 'fit.json', fit)
        row.update(status=fit['status'], diagnosis=fit['diagnosis'], candidates=len(fit['candidates']),
            report={'path': f'part-{group_id:03d}/fit.json', 'sha256': _sha(group_folder / 'fit.json')},
            ar_response=fit['ar_prediction_envelope']['response_status'] if fit['ar_prediction_envelope'] else None,
            preview_representative_ids={family: candidate['candidate_id'] for family, candidate in representatives[group_id].items()})
    for family in FAMILIES:
        if not all(group in representatives and family in representatives[group] for group in observations['groups']):
            report['previews'].append({'family': family, 'status': 'missing_group_candidate'})
            continue
        replacements, candidate_ids = [], {}
        for group_id, arrays in prepared_surfaces.items():
            chosen = representatives[group_id][family]
            candidate_ids[str(group_id)] = chosen['candidate_id']
            surfaces = []
            for surface in arrays:
                surfaces.append({**surface, 'appearance': chosen['appearance']})
            replacements.append({'part_index': group_id, 'surfaces': surfaces})
        destination = output / f'preview-{family}.glb'
        receipt = write_optical_candidate(source, destination, replacements, source_sha256=preparation['source_sha256'],
            provenance={'method': 'conditional_photo_family_preview_v1', 'family': family, 'candidate_ids': candidate_ids,
                'preparation_report_sha256': pins[str(preparation_report)], 'prepared_glb_sha256': pins[str(model)],
                'appearance_origin': 'photo_ensemble_representative_not_accepted_material'})
        export_path = output / f'preview-{family}-export.json'
        _write(export_path, receipt)
        report['previews'].append({'family': family, 'status': 'diagnostic_preview_exported',
            'path': destination.name, 'sha256': receipt['output_sha256'], 'candidate_ids': candidate_ids,
            'export': {'path': export_path.name, 'sha256': _sha(export_path)}})
    if any(_sha(path) != digest for path, digest in pins.items()) or implementation_manifest() != implementation:
        raise ValueError('Source or implementation changed during photo fitting')
    report['status'] = 'diagnostic_previews_available' if any(p['status'] == 'diagnostic_preview_exported' for p in report['previews']) else 'no_complete_preview'
    _write(output / 'report.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('preparation', 'regions', 'output'):
        parser.add_argument('--' + name, required=True, type=Path)
    parser.add_argument('--maximum-optimization-runs', type=int, default=540)
    parser.add_argument('--maximum-samples', type=int, default=256)
    args = parser.parse_args()
    result = run_photo_lens_stage(args.preparation, args.regions, args.output,
        policy=PhotoLensFitPolicy(maximum_optimization_runs=args.maximum_optimization_runs),
        maximum_samples_per_hypothesis=args.maximum_samples)
    print(json.dumps({key: result[key] for key in ('status', 'quality_verdict', 'previews')}))


if __name__ == '__main__':
    main()
