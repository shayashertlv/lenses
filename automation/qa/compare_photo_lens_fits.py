"""Read-only comparison of saved independent and joint photo-lens stage outputs.

No refit, resplit, mask selection or objective comparison occurs. Exact saved
arrays, provenance, prepared binding and frozen split must agree. Existing
external dependencies are hash-verified; unavailable originals are explicit.
Matched configuration summaries include all starts, collapse roughness-only
copies, and retain unmeasured validation. Counts describe explored hypotheses,
not independent observations, posterior probabilities or statistical confidence.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import itertools
import json
from pathlib import Path

import numpy as np

from reconstruction import photo_lens_fit as single


def _hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _child(root, relative):
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError('Artifact path must be relative')
    result = (root/relative).resolve()
    if not result.is_relative_to(root.resolve()):
        raise ValueError('Artifact path escapes saved stage')
    return result


class _Reader:
    def __init__(self):
        self.pins = {}
        self.unavailable = []

    def bytes(self, path, expected=None):
        path = Path(path).resolve()
        content = path.read_bytes(); digest = hashlib.sha256(content).hexdigest()
        if expected is not None and digest != expected:
            raise ValueError(f'Artifact/source hash mismatch: {path}')
        if str(path) in self.pins and self.pins[str(path)] != digest:
            raise ValueError(f'Input changed during comparison: {path}')
        self.pins[str(path)] = digest
        return content

    def json(self, path, expected=None):
        def invalid(value):
            raise ValueError(f'Nonfinite JSON constant: {value}')
        return json.loads(self.bytes(path, expected), parse_constant=invalid)

    def external(self, pins, label):
        for path, digest in sorted(pins.items()):
            if Path(path).is_file():
                self.bytes(path, digest)
            else:
                self.unavailable.append({'stage': label, 'path': path, 'declared_sha256': digest,
                                         'status': 'original_source_verification_unavailable'})

    def verify(self):
        for path, digest in self.pins.items():
            if _hash(path) != digest:
                raise ValueError(f'Input changed during comparison: {path}')


def _load_group(path, reader):
    import io
    manifest = reader.json(path)
    observations = []
    for record in manifest['observations']:
        metadata = {k: v for k, v in record.items() if k != 'arrays'}
        receipt = record['arrays']
        content = reader.bytes(_child(path.parent, receipt['path']), receipt['sha256'])
        with np.load(io.BytesIO(content), allow_pickle=False) as archive:
            arrays = {k: archive[k].copy() for k in archive.files}
        if set(arrays) & set(metadata):
            raise ValueError('Array fields collide with observation metadata')
        observations.append({**metadata, **arrays})
    return {'surface_binding': manifest['surface_binding'], 'observations': observations}


def _load_stages(independent_root, joint_root, reader):
    old = reader.json(independent_root/'report.json')
    new = reader.json(joint_root/'report.json')
    if old.get('method') != 'photo_lens_candidate_stage_v1' or new.get('method') != 'joint_photo_lens_candidate_stage_v1':
        raise ValueError('Expected independent and joint candidate-stage reports')
    if old.get('status') == 'running' or new.get('status') == 'running' or 'fit' not in new:
        raise ValueError('Comparison requires completed fit artifacts')
    receipt = reader.json(joint_root/'receipt.json')
    if receipt['report_sha256'] != reader.pins[str((joint_root/'report.json').resolve())]:
        raise ValueError('Joint completed-report receipt mismatch')
    # Completed-stage inventory includes checkpoints/previews; verifying it
    # prevents a comparison from legitimizing a partially substituted run.
    for relative, digest in new['artifacts'].items():
        reader.bytes(_child(joint_root, relative), digest)
    reader.external(old['input_sha256'], 'independent')
    reader.external(new['input_sha256'], 'joint')
    for path in old['input_sha256'].keys() & new['input_sha256'].keys():
        if old['input_sha256'][path] != new['input_sha256'][path]:
            raise ValueError('Stages declare conflicting source dependency identities')
    old_groups, fits, part_ids = {}, {}, {}
    for row in old['groups']:
        if 'report' not in row:
            raise ValueError('Independent stage lacks a complete group fit')
        fit_path = _child(independent_root, row['report']['path'])
        fit = reader.json(fit_path, row['report']['sha256'])
        group = _load_group(fit_path.parent/'observations.json', reader)
        gid = group['surface_binding']['material_group_id']
        if gid in old_groups or fit['surface_binding'] != group['surface_binding']:
            raise ValueError('Independent group identity/binding mismatch')
        old_groups[gid], fits[gid] = group, fit
        part_ids[str(row['source_part_index'])] = gid
    new_groups = {}
    for relative in new['inputs']['groups'].values():
        group = _load_group(_child(joint_root, relative), reader)
        gid = group['surface_binding']['material_group_id']
        if gid in new_groups:
            raise ValueError('Duplicate joint observation group')
        new_groups[gid] = group
    if old_groups.keys() != new_groups.keys():
        raise ValueError('Independent and joint material groups differ')
    fit = reader.json(_child(joint_root, new['fit']['path']), new['fit']['sha256'])
    if fit.get('method') != 'joint_uncalibrated_photo_lens_ensemble_v1':
        raise ValueError('Unsupported joint fit contract')
    old_obs_report = reader.json(independent_root/'observation-report.json')
    new_obs_report = reader.json(_child(joint_root, new['inputs']['observation_report']))
    if old_obs_report != new_obs_report:
        raise ValueError('Observation/camera/projection reports differ; fixed-source comparison unavailable')
    return old, new, old_groups, new_groups, fits, fit, part_ids


def _split(row, ledger):
    if ledger['method'] == 'fixed_4x4_spatial_tiles_mod4':
        grid = 4
    elif ledger['method'] in ('adaptive_spatial_tiles_mod4_v1', 'adaptive_spatial_tiles_share_v2') and type(ledger.get('grid')) is int and 2 <= ledger['grid'] <= 64:
        grid = ledger['grid']
    else:
        raise ValueError('Unknown frozen split rule; no guessed reconstruction')
    if ledger['validation_condition'] != '(tile_x+2*tile_y)%4==0':
        raise ValueError('Unknown frozen split rule; no guessed reconstruction')
    lower, span = np.asarray(ledger['lower_xy']), np.asarray(ledger['span_xy'])
    if lower.shape != (2,) or span.shape != (2,) or not np.isfinite([*lower, *span]).all() or np.any(span <= 0):
        raise ValueError('Invalid frozen split bounds')
    tile = np.minimum(grid-1, np.floor(grid*(row['arrays']['xy']-lower)/span).astype(int))
    validation = (tile[:, 0]+2*tile[:, 1])%4 == 0
    return {'train': row['eligible'] & ~validation, 'validation': row['eligible'] & validation}


def _verify_observations(old_groups, new_groups, fits, joint):
    if set(joint['surface_bindings']) != set(old_groups):
        raise ValueError('Joint fit group bindings differ')
    joint_split = {r['photo_id']: r for r in joint['spatial_split']}
    joint_coverage = {(r['group_id'], r['observation_id']): r for r in joint['coverage']}
    support, records = [], {}
    for gid in sorted(old_groups):
        a, b, fit = old_groups[gid], new_groups[gid], fits[gid]
        if a['surface_binding'] != b['surface_binding'] or a['surface_binding'] != joint['surface_bindings'][gid]:
            raise ValueError('Prepared/source/coordinate binding differs between stages')
        p = single.PhotoLensFitPolicy(**{'rear_content': 'explained', 'maximum_validation_share': 1.0, 'gradient_density_keyframes': 3, **fit['policy']})
        rows_a = {r['id']: r for r in (single._read_observation(v, p) for v in a['observations'])}
        rows_b = {r['id']: r for r in (single._read_observation(v, p) for v in b['observations'])}
        if len(rows_a) != len(a['observations']) or len(rows_b) != len(b['observations']) or rows_a.keys() != rows_b.keys():
            raise ValueError('Observation identities/alternatives differ')
        coverage = {r['observation_id']: r for r in fit['coverage']}
        old_split = {r['photo_id']: r for r in fit['spatial_split']}
        if any(photo not in joint_split or old_split[photo] != joint_split[photo] for photo in old_split):
            raise ValueError('Frozen training/validation split differs')
        for oid, row in sorted(rows_a.items()):
            other = rows_b[oid]
            if any(row[k] != other[k] for k in ('photo_id', 'region_id', 'hypothesis_id', 'source_sha256', 'provenance', 'image_size', 'has_rear', 'numeric_sha256')):
                raise ValueError('Observation arrays/provenance/source/support differs')
            if any(not np.array_equal(row['arrays'][k], other['arrays'][k], equal_nan=True) for k in row['arrays']):
                raise ValueError('Exact observation arrays differ')
            if coverage[oid]['numeric_sha256'] != row['numeric_sha256']:
                raise ValueError('Independent fit does not bind saved observation arrays')
            # Joint hash names the group/region namespace. Compute that hash
            # explicitly instead of treating different hashes as changed pixels.
            raw = next(v for v in b['observations'] if v['id'] == oid)
            namespaced = dict(raw, id=json.dumps([gid, oid], separators=(',', ':')),
                              region_id=json.dumps([gid, row['region_id']], separators=(',', ':')))
            bound = single._read_observation(namespaced, p)
            if joint_coverage[(gid, oid)]['numeric_sha256'] != bound['numeric_sha256']:
                raise ValueError('Joint fit does not bind saved observation arrays')
            split = _split(row, old_split[row['photo_id']])
            for label, measured in (('independent', coverage[oid]), ('joint', joint_coverage[(gid, oid)])):
                if measured['eligible'] != int(row['eligible'].sum()) or any(measured[s] != int(mask.sum()) for s, mask in split.items()):
                    raise ValueError(f'{label} saved support differs from frozen arrays/split')
            arrays, eligible = row['arrays'], row['eligible']
            height = np.minimum(3, np.searchsorted([0, .25, .5, .75, 1], arrays['v'], side='right')-1)
            rear = np.full(len(eligible), 'rear_geometry_unavailable', dtype='<U32')
            if row['has_rear']:
                weight = arrays['rear_weight']
                rear[weight == 0] = 'backdrop_proxy'; rear[weight == 1] = 'rear_proxy'
                rear[(weight > 0)&(weight < 1)] = 'mixed_rear_proxy'
            cells = []
            for bin_id, category in itertools.product(range(4), sorted(set(rear[eligible]))):
                choose = eligible&(height == bin_id)&(rear == category)
                if choose.any():
                    cells.append({'cell': f'v{bin_id}/{category}', 'samples': int(choose.sum()),
                                  **{s: int((choose&mask).sum()) for s, mask in split.items()},
                                  'validation_status': 'covered_by_frozen_samples' if (choose&split['validation']).any() else 'unmeasured'})
            support.append({'group_id': gid, 'observation_id': oid, 'photo_id': row['photo_id'],
                            'region_id': row['region_id'], 'hypothesis_id': row['hypothesis_id'],
                            'source_sha256': row['source_sha256'], 'samples': len(eligible), 'eligible': int(eligible.sum()),
                            'excluded': int((~eligible).sum()), **{s: int(mask.sum()) for s, mask in split.items()},
                            'intrinsic_height_rear_cells': cells, 'numeric_sha256_without_joint_namespace': row['numeric_sha256'],
                            'exact_saved_arrays_equal': True})
            records[(gid, oid)] = (row, split)
    if len(joint_coverage) != len(records):
        raise ValueError('Unexpected joint observation coverage records')
    return support, records


def _collapse(candidates):
    buckets = {}
    for c in candidates:
        a = c['assumptions']
        key = (a['family'], a['lighting'], a['rear'], tuple(sorted(a['observations'])), a['start'])
        descriptor = dict(c['appearance']); descriptor.pop('roughness')
        proof = {k: c[k] for k in ('optimizer', 'nuisance', 'photo_measurements', 'photo_policy_status', 'ar_probes')}
        proof['descriptor_without_roughness'] = descriptor
        if key in buckets:
            saved = buckets[key]
            if saved['proof'] != proof:
                raise ValueError('Roughness-only candidate copies disagree in fitted response/metrics')
            saved['candidate_ids'].append(c['candidate_id'])
            if a['roughness'] < saved['candidate']['assumptions']['roughness']:
                saved['candidate'] = c
        else:
            buckets[key] = {'candidate': c, 'candidate_ids': [c['candidate_id']], 'proof': proof}
    return [buckets[k] for k in sorted(buckets)]


def _distribution(values):
    values = [float(v) for v in values if v is not None]
    if not values:
        return {'values': 0, 'status': 'unmeasured', 'minimum': None, 'median': None, 'maximum': None}
    if not np.isfinite(values).all():
        raise ValueError('Nonfinite stored measurement')
    return {'values': len(values), 'status': 'measured_conditional_candidates',
            'minimum': min(values), 'median': float(np.median(values)), 'maximum': max(values)}


def _counts(candidates, *, group_id=None):
    states = [c['groups'][group_id]['photo_policy_status'] if group_id is not None else c['photo_policy_status'] for c in candidates]
    return {'optimizer_candidate_records': len(candidates), 'converged': sum(c['optimizer']['converged'] for c in candidates),
            'photo_policy_status_counts': dict(sorted(Counter(states).items()))}


def _metrics(candidates, *, group_id=None):
    gathered = defaultdict(list)
    for c in candidates:
        metrics = c['groups'][group_id]['photo_metrics'] if group_id is not None else c['photo_measurements']
        for row in metrics:
            gathered[(row['observation_id'], row['photo_id'], row['region_id'])].append((row, c['optimizer']['converged']))
    result = []
    for (oid, photo, region), rows in sorted(gathered.items()):
        entry = {'observation_id': oid, 'photo_id': photo, 'region_id': region}
        for split in ('train', 'validation'):
            points = {r[split]['points'] for r, _ in rows}
            if len(points) != 1:
                raise ValueError('Same saved observation has inconsistent metric support')
            entry[split] = {'points_per_candidate': points.pop(), 'all_starts': {}, 'converged_starts': {}}
            for name in ('mean_absolute_interval_error_codes', 'p90_absolute_interval_error_codes', 'maximum_absolute_interval_error_codes', 'fraction_channels_within_policy'):
                entry[split]['all_starts'][name] = _distribution(r[split][name] for r, _ in rows)
                entry[split]['converged_starts'][name] = _distribution(r[split][name] for r, ok in rows if ok)
        result.append(entry)
    return result


_DIRECTIONS = np.asarray([[1., 0, 0], [-1., 0, 0], [0, 1., 0], [0, -1., 0], [0, 0, 1.], [0, 0, -1.]])


def _lighting(row):
    base = np.asarray(row['environment_rgb'])
    field = np.tile(base, (len(_DIRECTIONS), 1))
    if 'log_environment_coefficients' in row:
        features = np.column_stack((_DIRECTIONS, _DIRECTIONS[:, 0]**2-_DIRECTIONS[:, 1]**2))
        field *= np.exp(features@np.asarray(row['log_environment_coefficients']))
    gain = row['exposure_multiplier']*np.asarray(row['white_balance_rgb'])
    return field, gain


def _lighting_disagreement(independent_candidates):
    result = []
    for a, b in itertools.combinations(sorted(independent_candidates), 2):
        rows_a = defaultdict(list); rows_b = defaultdict(list)
        for candidates, dest in ((independent_candidates[a], rows_a), (independent_candidates[b], rows_b)):
            for c in candidates:
                for nuisance in c['nuisance']:
                    dest[nuisance['photo_id']].append(_lighting(nuisance))
        for photo in sorted(rows_a.keys() & rows_b.keys()):
            differences = [(float(np.max(np.abs(x[0]-y[0]))), float(np.max(np.abs(x[1]-y[1]))))
                           for x, y in itertools.product(rows_a[photo], rows_b[photo])]
            result.append({'group_pair': [a, b], 'photo_id': photo,
                           'independent_pairings': len(differences),
                           'maximum_channel_environment_difference_at_common_probe_directions': _distribution(v[0] for v in differences),
                           'maximum_channel_exposure_times_white_balance_difference': _distribution(v[1] for v in differences),
                           'joint_disagreement': 0., 'joint_scope': 'shared_by_parameter_alias_construction_not_measured_light_equality'})
    return result


def _verify_candidate_support(candidates, records, *, joint=False):
    for c in candidates:
        if joint and set(c['groups']) != {g for g, _ in records}:
            raise ValueError('Joint candidate does not cover every saved material group')
        parts = c['groups'].items() if joint else [(c['material_group_id'], {'photo_metrics': c['photo_measurements']})]
        for gid, value in parts:
            expected_ids = [o['observation_id'] for o in value['observations']] if joint else c['assumptions']['observations']
            metric_ids = [m['observation_id'] for m in value['photo_metrics']]
            if set(metric_ids) != set(expected_ids) or len(metric_ids) != len(set(metric_ids)) or len(expected_ids) != len(set(expected_ids)):
                raise ValueError('Candidate metric set differs from its full mask configuration')
            for m in value['photo_metrics']:
                row, split = records[(gid, m['observation_id'])]
                if row['photo_id'] != m['photo_id'] or row['region_id'] != m['region_id']:
                    raise ValueError('Candidate metric identity differs from saved observation')
                for s, mask in split.items():
                    if m[s]['points'] != int(mask.sum()):
                        raise ValueError('Candidate metrics differ from frozen support')
        if joint:
            expected = {o['photo_id'] for g in c['groups'].values() for o in g['observations']}
            actual = [r['photo_id'] for r in c['shared_nuisance_by_photo']]
            if set(actual) != expected or len(actual) != len(expected):
                raise ValueError('Joint shared nuisance must have exactly one entry per observed photo')


def _ar_summary(envelope):
    if envelope is None:
        return None
    return {k: envelope[k] for k in ('scope', 'maximum_channel_spread', 'response_status', 'tolerance_linear', 'not_a_certified_uncertainty_bound')} | {
        'candidate_records_in_saved_envelope': len(envelope['candidate_ids'])}


def compare_photo_lens_fits(independent_root: Path, joint_root: Path, output: Path | None = None) -> dict:
    """Compare immutable saved artifacts; optionally create report.json/README.md.

    Exact array/provenance/split mismatch raises, rather than calling different
    samples a paired experiment. This does not independently replay photometric
    residuals; supplied fit metrics remain saved numerical evidence, cross-checked
    against support. The separate frozen-fit replay audit serves that purpose.
    """
    independent_root, joint_root = Path(independent_root).resolve(), Path(joint_root).resolve()
    reader = _Reader()
    old, new, old_groups, new_groups, fits, joint, parts = _load_stages(independent_root, joint_root, reader)
    support, records = _verify_observations(old_groups, new_groups, fits, joint)
    required_source_digests = {r['source_sha256'] for r in support}
    for group in old_groups.values():
        binding = group['surface_binding']
        required_source_digests.add(binding['prepared_glb_sha256'])
        if 'source_sha256' in binding:
            required_source_digests.add(binding['source_sha256'])
    if any(not required_source_digests <= set(stage['input_sha256'].values()) for stage in (old, new)):
        raise ValueError('Source photos/prepared geometry are not fully pinned by both stages')
    collapsed = {g: _collapse(f['candidates']) for g, f in fits.items()}
    candidates = {g: [r['candidate'] for r in rows] for g, rows in collapsed.items()}
    for rows in candidates.values():
        _verify_candidate_support(rows, records)
    _verify_candidate_support(joint['candidates'], records, joint=True)
    index = {}
    for gid, rows in candidates.items():
        lookup = defaultdict(list)
        for c in rows:
            a = c['assumptions']
            lookup[(a['family'], a['lighting'], a['rear'], tuple(sorted(a['observations'])))].append(c)
        index[gid] = lookup
    configurations = defaultdict(list)
    for c in joint['candidates']:
        signature = tuple((gid, value['family'], value['rear_mode'], tuple(sorted(o['observation_id'] for o in value['observations'])))
                          for gid, value in sorted(c['groups'].items()))
        configurations[(c['assumptions']['lighting'], signature)].append(c)
    matched, unmatched, family_buckets = [], [], {}
    for (lighting, signature), rows in sorted(configurations.items()):
        matched_old = {gid: index[gid].get((family, lighting, rear, ids), []) for gid, family, rear, ids in signature}
        config = {'family_assignment': {g: f for g, f, _, _ in signature}, 'lighting': lighting,
                  'rear_modes': {g: r for g, _, r, _ in signature}, 'observations_by_group': {g: list(ids) for g, _, _, ids in signature}}
        missing = [g for g, values in matched_old.items() if not values]
        if missing:
            unmatched.append({**config, 'reason': 'independent_configuration_unavailable', 'groups': missing,
                              'joint_candidate_ids': [c['candidate_id'] for c in rows]})
            continue
        groups = {}
        assignment = tuple(sorted(config['family_assignment'].items()))
        bucket = family_buckets.setdefault(assignment, {'independent': defaultdict(dict), 'joint': {}})
        for gid, independent in matched_old.items():
            groups[gid] = {'independent': {'counts': _counts(independent), 'candidate_ids': [c['candidate_id'] for c in independent], 'region_metrics': _metrics(independent)},
                           'joint': {'counts': _counts(rows, group_id=gid), 'candidate_ids': [c['candidate_id'] for c in rows], 'region_metrics': _metrics(rows, group_id=gid)}}
            bucket['independent'][gid].update({c['candidate_id']: c for c in independent})
        bucket['joint'].update({c['candidate_id']: c for c in rows})
        matched.append({**config, 'groups': groups, 'joint_global_counts': _counts(rows),
                        'independent_start_pairing_cardinality': int(np.prod([len(v) for v in matched_old.values()], dtype=object)),
                        'lighting_disagreement': _lighting_disagreement(matched_old)})
    summaries = []
    for assignment, bucket in sorted(family_buckets.items()):
        rows = list(bucket['joint'].values())
        summaries.append({'family_assignment': dict(assignment), 'joint_global_counts': _counts(rows),
                          'groups': {g: {'independent': {'counts': _counts(list(values.values())), 'region_metrics': _metrics(list(values.values()))},
                                         'joint': {'counts': _counts(rows, group_id=g), 'region_metrics': _metrics(rows, group_id=g)}}
                                     for g, values in sorted(bucket['independent'].items())}})
    policy_differences = []
    ignored = {'families', 'lighting_families', 'roughness_values', 'maximum_mask_branches', 'maximum_optimization_runs',
               'maximum_observations', 'maximum_total_samples', 'maximum_photos', 'maximum_samples_per_observation'}
    for gid, fit in sorted(fits.items()):
        new_policy = joint['policy']['photo_policy']
        for key in sorted(fit['policy'].keys() | new_policy.keys()):
            if key not in ignored and fit['policy'].get(key) != new_policy.get(key):
                policy_differences.append({'group_id': gid, 'field': key, 'independent': fit['policy'].get(key), 'joint': new_policy.get(key)})
    preview_records = []
    for preview in new.get('previews', []):
        if 'candidate_id' not in preview:
            continue
        c = next(v for v in joint['candidates'] if v['candidate_id'] == preview['candidate_id'])
        groups = {}
        for gid, value in c['groups'].items():
            old_stage_group = next(r for r in old['groups'] if parts[str(r['source_part_index'])] == gid)
            cid = old_stage_group.get('preview_representative_ids', {}).get(value['family'])
            baseline = next((v for v in fits[gid]['candidates'] if v['candidate_id'] == cid), None)
            same = baseline is not None and (baseline['assumptions']['lighting'], baseline['assumptions']['rear'], sorted(baseline['assumptions']['observations'])) == (
                c['assumptions']['lighting'], value['rear_mode'], sorted(o['observation_id'] for o in value['observations']))
            groups[gid] = {'independent_candidate_id': cid, 'same_mask_lighting_rear_configuration': same,
                           'independent_metrics': baseline['photo_measurements'] if baseline else None, 'joint_metrics': value['photo_metrics']}
        preview_records.append({'joint_candidate_id': c['candidate_id'], 'family_assignment': preview['family_assignment'],
                                'groups': groups, 'scope': 'existing_stage_display_choices_reuse_validation_not_independent_evaluation'})
    helper_sha = _hash(__file__)
    reader.verify()
    old_code = old.get('implementation', {}).get('source_sha256', {})
    joint_code = joint.get('implementation', {}).get('files', {})
    photometric_implementation = {name: {'independent_sha256': old_code.get(name), 'joint_sha256': joint_code.get(name),
        'status': 'same_bytes' if old_code.get(name) is not None and old_code.get(name) == joint_code.get(name) else
                  'unavailable' if old_code.get(name) is None or joint_code.get(name) is None else 'different_bytes_requires_replay_review'}
        for name in ('photo_lens_fit.py', 'lens_appearance.py')}
    result = {'schema_version': 1, 'method': 'fixed_artifact_independent_joint_photo_comparison_v1',
        'status': 'conditional_comparison_available', 'quality_verdict': 'unmeasured', 'parameter_identification': 'unmeasured',
        'independent_root': str(independent_root), 'joint_root': str(joint_root), 'input_sha256': reader.pins,
        'helper_sha256': helper_sha, 'unavailable_original_sources': reader.unavailable,
        'comparability': {'exact_saved_arrays_provenance_bindings_and_splits': True,
                         'observation_camera_report_equal': True, 'external_dependency_digest_multisets_equal':
                         sorted(old['input_sha256'].values()) == sorted(new['input_sha256'].values()),
                         'photo_policy_differences': policy_differences, 'saved_objectives_comparable': False,
                         'shared_photometric_implementation': photometric_implementation,
                         'reason': 'Independent objectives normalize each group/photo and its nuisance priors separately; joint uses global unique photo pixels and one shared nuisance prior.',
                         'residual_replay': 'not_performed_here_saved_metrics_are_support_checked'},
        'counts': {'independent': {g: {'roughness_expanded_records': len(fits[g]['candidates']),
                                      'roughness_collapsed': _counts(candidates[g]), 'declared_optimization_runs': fits[g]['exploration']['optimization_runs'],
                                      'failed_runs': len(fits[g]['exploration']['failed_runs'])}
                                  for g in sorted(fits)},
                   'joint': {**_counts(joint['candidates']), 'declared_optimization_runs': joint['exploration']['optimization_runs'],
                             'failed_runs': len(joint['exploration']['failed_runs'])},
                   'matched_configurations': len(matched), 'unmatched_joint_configurations': len(unmatched),
                   'unique_observation_support_cells_without_validation': sum(c['validation_status'] == 'unmeasured' for r in support for c in r['intrinsic_height_rear_cells'])},
        'fixed_support': support, 'family_summaries': summaries, 'matched_configurations': matched, 'unmatched_joint_configurations': unmatched,
        'lighting_common_probe_directions': _DIRECTIONS.tolist(),
        'ar_envelopes': {'independent': {g: _ar_summary(f['ar_prediction_envelope']) for g, f in fits.items()},
                         'joint': {g: _ar_summary(e) for g, e in joint['ar_prediction_envelopes_by_group'].items()},
                         'scope': 'saved_local_response_ensembles_candidate_pool_may_differ_no_renderer_or_roughness_blur_validation'},
        'existing_display_representatives': preview_records,
        'limitations': ['A shared illumination explanation is an extra conditional constraint, not identified optics or verified physical lighting.',
            'Reported distributions summarize dependent explored starts/configurations, not probabilities, independent samples or confidence intervals.',
            'Three joint starts are not all Cartesian combinations of independent group starts; family and illumination restrictions remain disclosed in each fit.',
            'Smoother or narrower AR envelopes can result from assumptions/bounds and do not prove greater accuracy.',
            'Masks, cameras, local height registration, rear content and physical group identity remain candidate-dependent.',
            'Validation rows and height/rear coverage are preserved; missing cells remain unmeasured even if aggregate region metrics pass.',
            'Selected previews reuse validation and are display choices; no model, mask or material is selected by this comparison.']}
    # Ensure all report values are portable, finite JSON before writing anything.
    result = json.loads(json.dumps(result, allow_nan=False))
    if output is not None:
        output = Path(output).resolve()
        if output.is_relative_to(independent_root) or output.is_relative_to(joint_root) or any(Path(p).is_relative_to(output) for p in reader.pins):
            raise ValueError('Comparison output must be outside and not contain source artifact trees')
        if output.exists() and any(output.iterdir()):
            raise ValueError('Use an empty comparison output directory')
        reader.verify()
        if _hash(__file__) != helper_sha:
            raise ValueError('Comparison implementation changed')
        output.mkdir(parents=True, exist_ok=True)
        (output/'report.json').write_text(json.dumps(result, indent=2, allow_nan=False)+'\n', encoding='utf-8')
        (output/'README.md').write_text(_markdown(result), encoding='utf-8')
    return result


def _markdown(report):
    counts = report['counts']
    lines = ['# Independent versus joint conditional photo fits', '',
        'Exact saved observation arrays, bindings, provenance, camera report and frozen spatial splits agree. No refit or mask selection was performed.', '',
        f"Matched joint configurations: **{counts['matched_configurations']}**; unmatched: **{counts['unmatched_joint_configurations']}**.",
        f"Distinct observation/height/rear cells without validation: **{counts['unique_observation_support_cells_without_validation']}**. These are unmeasured, not extra independent failures.", '',
        '| Fit | Optimizer candidate records | Converged | Saved policy statuses |', '|---|---:|---:|---|']
    for gid, values in counts['independent'].items():
        row = values['roughness_collapsed']
        lines.append(f"| Independent {gid} | {row['optimizer_candidate_records']} | {row['converged']} | {row['photo_policy_status_counts']} |")
    row = counts['joint']; lines.append(f"| Joint | {row['optimizer_candidate_records']} | {row['converged']} | {row['photo_policy_status_counts']} |")
    lines.extend(['', 'The JSON contains all matched-mask/family/lighting/rear configurations, region-level train/validation error distributions, convergence counts, common-direction lighting disagreement and saved AR envelopes.', '',
        'Saved optimizer objectives are not compared: pixel normalization and nuisance priors differ. Shared lighting equality is imposed by the joint parameter aliases, not measured from the photos. All distributions summarize dependent explored explanations, not confidence intervals.', '',
        'Existing preview rankings reuse validation. Missing validation coverage, geometry/rear-content errors, local-coordinate ambiguity and material/illumination tradeoffs remain. No appearance is identified or accepted.', ''])
    if report['unavailable_original_sources']:
        lines.extend([f"Original source verification was unavailable for {len(report['unavailable_original_sources'])} declared dependencies; exact saved arrays were still checked.", ''])
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--independent', required=True, type=Path)
    parser.add_argument('--joint', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    result = compare_photo_lens_fits(args.independent, args.joint, args.output)
    print(json.dumps({'status': result['status'], 'counts': result['counts']}, allow_nan=False))


if __name__ == '__main__':
    main()
