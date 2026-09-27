"""Exercise automatic group preparation and the actual joint-stage input adapter.

One fixed policy is applied to every declared development case. Source parts
remain unverified hypotheses; this does not fit color or establish photo-only
reconstruction. Optional historical comparison checks coordinates and coverage,
not neutral-control pixels or semantic accuracy.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
from pathlib import Path
import re
import time

import numpy as np

from reconstruction.photo_lens_observations import _child, _read_pinned
from reconstruction.photo_lens_stage import load_optical_fit_inputs, save_group_observations
from reconstruction.prepare_optical_groups import _ordinary_path, run_optical_group_preparation
from reconstruction.refine_photos import implementation_manifest


def _write(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def _photo_content(report):
    photos = copy.deepcopy(report['photos'])
    for photo in photos:
        for hypothesis in photo.get('hypotheses', []):
            # Only the explicit identity spelling changes between the archived
            # declarations and automatic binding-derived hypotheses.
            hypothesis['optical_group_id'] = hypothesis['source_part_indices']
    return photos


def _historical_comparison(folder, observations, export, pins):
    old_report = json.loads(_read_pinned(folder/'observations.json', pins))
    old_export = json.loads(_read_pinned(folder/'export.json', pins))
    source_members = lambda receipt: {
        group['group_id']: tuple(sorted(m['source_part_index'] for m in group['members']))
        for group in receipt['groups']}
    old_members, new_members = source_members(old_export), source_members(export)
    previous = {}
    for path in sorted(folder.glob('group-*/observations.json')):
        record = json.loads(_read_pinned(path, pins))
        key = old_members[record['surface_binding']['material_group_id']]
        if key in previous:
            raise ValueError('Historical comparison has duplicate source group membership')
        previous[key] = (path.parent, record)
    current = {new_members[group['surface_binding']['material_group_id']]: group
               for group in observations['groups'].values()}
    rows = []
    for key in sorted(set(previous) | set(current)):
        item = {'source_part_indices': list(key), 'same_observation_inventory': False,
                'arrays_equal': False, 'differences': []}
        rows.append(item)
        if key not in previous or key not in current:
            item['differences'].append('missing_group')
            continue
        old_folder, record = previous[key]
        identity = lambda observation: (observation['photo_id'], observation['region_id'], observation['hypothesis_id'])
        old = {identity(row): row for row in record['observations']}
        new = {identity(row): row for row in current[key]['observations']}
        item['same_observation_inventory'] = set(old) == set(new)
        if len(old) != len(record['observations']) or len(new) != len(current[key]['observations']):
            raise ValueError('Duplicate observation identity in corpus comparison')
        if set(old) != set(new):
            item['differences'].append('observation_inventory')
        for identity_key in sorted(set(old) & set(new)):
            reference = old[identity_key]['arrays']
            raw = _read_pinned(_child(old_folder, reference['path']), pins, reference['sha256'])
            with np.load(io.BytesIO(raw), allow_pickle=False) as archive:
                arrays = {name: value for name, value in new[identity_key].items() if isinstance(value, np.ndarray)}
                if set(archive.files) != set(arrays):
                    item['differences'].append({'observation': identity_key, 'attribute_inventory': False})
                for name in sorted(set(archive.files) & set(arrays)):
                    before, after = archive[name], arrays[name]
                    if before.dtype != after.dtype or not np.array_equal(before, after, equal_nan=True):
                        difference = {'observation': identity_key, 'array': name,
                                      'same_dtype': before.dtype == after.dtype,
                                      'same_shape': before.shape == after.shape}
                        if before.shape == after.shape and np.issubdtype(before.dtype, np.number) and np.issubdtype(after.dtype, np.number):
                            finite = np.isfinite(before) & np.isfinite(after)
                            delta = np.abs(before[finite].astype(np.float64)-after[finite].astype(np.float64))
                            difference.update(maximum_finite_absolute_difference=float(delta.max()) if delta.size else None,
                                finite_compared_elements=int(delta.size),
                                same_finite_mask=bool(np.array_equal(np.isfinite(before), np.isfinite(after))))
                        item['differences'].append(difference)
        item['arrays_equal'] = not item['differences']
    return {'same_source': export['source_sha256'] == old_export['source_sha256'],
            'same_photo_report_except_group_id_spelling': _photo_content(old_report) == _photo_content(observations['report']),
            'groups': rows, 'all_observation_arrays_equal': all(row['arrays_equal'] for row in rows)}


def run(manifest_path, region_root, output, *, baseline_root=None):
    manifest_path, region_root, output = (_ordinary_path(p) for p in (manifest_path, region_root, output))
    baseline_root = _ordinary_path(baseline_root) if baseline_root is not None else None
    if (output == manifest_path.parent or output.is_relative_to(region_root) or region_root.is_relative_to(output)
            or manifest_path.is_relative_to(output) or (baseline_root is not None and
                (output.is_relative_to(baseline_root) or baseline_root.is_relative_to(output)))):
        raise ValueError('Use an output directory separate from source and historical evidence')
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError('Use a new empty output directory')
    pins = {}
    manifest = json.loads(_read_pinned(manifest_path, pins))
    cases = manifest['cases']
    if manifest.get('schema_version') != 1 or not isinstance(cases, list) or not cases:
        raise ValueError('Expected a nonempty version-one refinement corpus')
    ids = [row['id'] for row in cases]
    if len(set(ids)) != len(ids) or any(not isinstance(name, str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,79}', name) for name in ids):
        raise ValueError('Expected unique safe corpus case IDs')
    inputs = []
    for case in cases:
        model = _ordinary_path(manifest_path.parent/case['model'])
        region = _ordinary_path(region_root/f"cases/{case['id']}/attempt-001/report.json")
        if model.is_relative_to(output):
            raise ValueError('Corpus source model must be outside output')
        _read_pinned(model, pins); _read_pinned(region, pins)
        inputs.append((case, model, region))
    implementation = implementation_manifest()
    _read_pinned(Path(__file__).resolve(), pins)
    report = {'schema_version': 1, 'method': 'automatic_group_preparation_joint_input_corpus_v1',
        'status': 'running', 'grouping_mode': 'source_part_hypotheses', 'maximum_samples_per_hypothesis': 256,
        'accepted': False, 'quality_verdict': 'unmeasured', 'selected_material': None,
        'implementation': implementation, 'cases': [], 'limitations': [
            'Archived models are supplied initializers, not a photos-only reconstruction result.',
            'Whole source part groups and common source axes/units remain unverified.',
            'Known mixed frame/lens membership and unobserved groups remain in the corpus.',
            'Neutral materials and coordinate coverage do not measure tint, gradients, mirror response or AR appearance.',
            'These are five saved development designs, not an unseen-product benchmark.']}
    runtime_manifest = {'schema_version': 1, 'cases': []}
    output.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    for case, model, region in inputs:
        item = {'case': case['id'], 'source_sha256': pins[str(model)], 'status': 'running',
                'accepted': False, 'quality_verdict': 'unmeasured'}
        report['cases'].append(item)
        folder = output/case['id']; folder.mkdir()
        case_started = time.perf_counter()
        try:
            prepared = run_optical_group_preparation(model, folder/'preparation', grouping_mode='source_part_hypotheses')
            item.update(preparation_status=prepared['status'], groups=len(prepared['groups']),
                        preparation_report=str(folder/'preparation'/'report.json'))
            if prepared['status'] != 'prepared_optical_group_candidate':
                item.update(status='preparation_unavailable', reasons=prepared['reasons'])
            else:
                loaded = load_optical_fit_inputs(folder/'preparation'/'report.json', region)
                for path, digest in loaded['pins'].items():
                    _read_pinned(Path(path), pins, digest)
                observations = loaded['observations']
                for index, group in observations['groups'].items():
                    save_group_observations(folder/f'group-{index:03d}', group)
                item.update(status='prepared_and_sampled',
                    observations=sum(len(group['observations']) for group in observations['groups'].values()),
                    group_observations=[{'group_id': group['surface_binding']['material_group_id'], 'observations': len(group['observations'])}
                                        for group in observations['groups'].values()],
                    observation_report=_write(folder/'observations.json', observations['report']),
                    group_inventory=loaded['group_inventory'])
                if baseline_root is not None:
                    item['historical_comparison'] = _historical_comparison(baseline_root/case['id'], observations, loaded['export_receipt'], pins)
                runtime_manifest['cases'].append({'id': case['id'], 'path': str(loaded['model']),
                    'model_sha256': prepared['model']['sha256'], 'export_receipt_sha256': prepared['export']['sha256'],
                    'groups': loaded['export_receipt']['groups']})
        except Exception as error:
            # Every manifest case remains represented. An unexpected exception
            # is a failed case, never an omitted or accepted asset.
            item.update(status='failed', error_type=type(error).__name__, error=str(error))
        item['seconds'] = time.perf_counter()-case_started
        print(json.dumps({key: item[key] for key in ('case', 'status', 'seconds', 'groups', 'observations', 'error') if key in item}), flush=True)
        _write(output/'partial-report.json', report)
    for path, digest in tuple(pins.items()):
        _read_pinned(Path(path), pins, digest)
    if implementation_manifest() != implementation:
        raise ValueError('Implementation changed during the fixed-policy corpus run')
    report.update(status='preparation_and_sampling_complete' if all(row['status'] == 'prepared_and_sampled' for row in report['cases'])
                  else 'incomplete_corpus', seconds=time.perf_counter()-started, input_sha256=pins)
    # The render harness gets the complete declared corpus only. A partial
    # subset is not written as an apparently successful runtime experiment.
    report['runtime_manifest'] = _write(output/'runtime-manifest.json', runtime_manifest) if report['status'] == 'preparation_and_sampling_complete' else None
    _write(output/'report.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--regions', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--baseline', type=Path)
    args = parser.parse_args()
    result = run(args.manifest, args.regions, args.output, baseline_root=args.baseline)
    raise SystemExit(0 if result['status'] == 'preparation_and_sampling_complete' else 1)
