"""Replay frozen photo-lens candidates and audit their validation support.

``diagnose_photo_lens_fit(fit_path, observations_path, output,
observation_report_path=None, runtime_root=None)`` writes a new diagnostic
directory. It never fits, changes masks, changes splits, or accepts a material.
All candidates are audited. Only the existing stage's explicitly labelled
family representatives receive per-sample NPZ files.

An explicit ``runtime_root`` executes that local reconstruction Python snapshot
in a fresh subprocess, with the current interpreter/native dependencies. It is
trusted local code, not a sandbox. Its reconstruction Python source tree is
hashed before and after execution. Numerical replay is verified against saved
measurements, appearance/nuisance round trips, and the saved objective when its
least-squares convention is known. Approximate replay never establishes parity.

Native boundary distances refer to the pinned photographic MASK boundary, not
verified lens geometry. If its source is unavailable, coverage is unmeasured.
All support categories depend only on frozen inputs, not on residual magnitude.
"""
from __future__ import annotations

import argparse
import ast
from dataclasses import asdict
import hashlib
import importlib.metadata
import json
from pathlib import Path
import re
import subprocess
import sys

import numpy as np

METHOD = 'frozen_photo_lens_support_diagnostics_v1'
HEIGHT_EDGES = (0., .25, .5, .75, 1.)
_PARITY_ATOL = 1e-8
_PARITY_RTOL = 1e-10


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f'Duplicate JSON key: {key}')
            result[key] = value
        return result
    def invalid(value):
        raise ValueError(f'Nonfinite JSON constant: {value}')
    return json.loads(Path(path).read_text(encoding='utf-8-sig'), object_pairs_hook=unique, parse_constant=invalid)


def _plain(value):
    if isinstance(value, np.ndarray):
        return _plain(value.tolist())
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, (np.floating, float)):
        if not np.isfinite(value):
            raise ValueError('Diagnostic JSON cannot contain nonfinite values')
        return float(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.bool_):
        return bool(value)
    return value


def _write(path, value):
    Path(path).write_text(json.dumps(_plain(value), indent=2, allow_nan=False)+'\n', encoding='utf-8')


def _child(folder, relative):
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError('Artifact path must be relative')
    path = (folder / relative).resolve()
    if not path.is_relative_to(folder.resolve()) or path == folder.resolve():
        raise ValueError('Artifact path escapes its directory')
    return path


def _source_snapshot(root):
    package = root / 'reconstruction'
    if not (package / 'photo_lens_fit.py').is_file() or not (package / '__init__.py').is_file():
        raise ValueError('runtime_root must contain the selected reconstruction package')
    paths = sorted(package.rglob('*.py'))
    if any(not p.resolve().is_relative_to(package.resolve()) for p in paths):
        raise ValueError('Runtime Python source link escapes selected package')
    return {str(p.resolve()): _sha(p) for p in paths}


def _verify(pins):
    for path, digest in pins.items():
        if not Path(path).is_file() or _sha(path) != digest:
            raise ValueError(f'Pinned diagnostic input changed: {path}')


def _parity(actual, expected, label):
    """Strict structure and numerical roundoff check, not a photo threshold."""
    if isinstance(actual, dict):
        if not isinstance(expected, dict) or actual.keys() != expected.keys():
            raise ValueError(f'{label}: fields differ')
        return max((_parity(actual[k], expected[k], f'{label}/{k}') for k in actual), default=0.)
    if isinstance(actual, (tuple, list)):
        if not isinstance(expected, (tuple, list)) or len(actual) != len(expected):
            raise ValueError(f'{label}: sequence differs')
        return max((_parity(a, b, label) for a, b in zip(actual, expected)), default=0.)
    if isinstance(actual, (float, np.floating, int, np.integer)) and not isinstance(actual, (bool, np.bool_)):
        if not isinstance(expected, (float, int)) or isinstance(expected, bool) or not np.isfinite([actual, expected]).all():
            raise ValueError(f'{label}: finite numeric value required')
        difference = abs(float(actual)-expected)
        if difference > _PARITY_ATOL + _PARITY_RTOL*abs(expected):
            raise ValueError(f'{label}: saved numerical value differs')
        return difference
    if actual != expected:
        raise ValueError(f'{label}: value differs')
    return 0.


def _restore(model, candidate):
    x = np.zeros(len(model.lower))
    appearance = candidate['appearance']
    x[model.slices['density']] = np.asarray([k['optical_density_rgb'] for k in appearance['optical_density_keyframes']]).ravel()
    if model.mirror:
        reflection = ([k['reflectance_rgb'] for k in appearance['angular_reflectance_keyframes'][:-1]]
                      if model.angular else appearance['normal_reflectance_rgb'])
        x[model.slices['reflection']] = np.asarray(reflection).ravel()
    if len({r['photo_id'] for r in candidate['nuisance']}) != len(model.photos):
        raise ValueError('Saved nuisance photo identities differ')
    for row in candidate['nuisance']:
        photo = row['photo_id']
        x[model.slices[(photo, 'environment')]] = row['environment_rgb']
        if (photo, 'shape') in model.slices:
            x[model.slices[(photo, 'shape')]] = np.asarray(row['log_environment_coefficients']).ravel()
        if (photo, 'exposure') in model.slices:
            x[model.slices[(photo, 'exposure')]] = np.log(row['exposure_multiplier'])
            x[model.slices[(photo, 'wb')]] = np.log(row['white_balance_rgb'])[[0, 2]]
        if (photo, 'rear') in model.slices:
            x[model.slices[(photo, 'rear')]] = row['unknown_rear_rgb']
    if not np.isfinite(x).all() or np.any(x < np.asarray(model.lower)-1e-10) or np.any(x > np.asarray(model.upper)+1e-10):
        raise ValueError('Reconstructed parameters are nonfinite or outside saved policy bounds')
    _parity(model.appearance(x, candidate['assumptions']['roughness']).to_dict(), appearance, 'appearance round trip')
    _parity(model.nuisance(x), candidate['nuisance'], 'nuisance round trip')
    return x


def _linear_objective_supported(source):
    """Known v1 uses scipy's default linear loss on already-robust residuals."""
    tree = ast.parse(source)
    functions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'fit_photo_lens_candidates']
    if len(functions) != 1:
        return False
    calls = [n for n in ast.walk(functions[0]) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == 'least_squares']
    if len(calls) != 1 or any(k.arg is None for k in calls[0].keywords):
        return False
    loss = next((k.value for k in calls[0].keywords if k.arg == 'loss'), ast.Constant('linear'))
    return isinstance(loss, ast.Constant) and loss.value == 'linear'


def _stats(error, signed, choose, tolerance):
    e = error[choose]
    if not len(e):
        return {'status': 'unmeasured', 'points': 0, 'reason': 'No eligible frozen samples in this support/split.'}
    return {'status': 'measured_conditional_residual', 'points': len(e), 'mean_absolute_interval_error_codes': e.mean(),
        'p90_absolute_interval_error_codes': np.quantile(e, .9), 'maximum_absolute_interval_error_codes': e.max(),
        'fraction_channels_within_existing_policy': np.mean(e <= tolerance),
        'points_with_any_channel_outside_existing_policy': np.count_nonzero(np.any(e > tolerance, axis=1)),
        'median_signed_interval_error_rgb': np.median(signed[choose], axis=0)}


def _distance_stats(distance, choose):
    if distance is None:
        return {'status': 'unmeasured', 'reason': 'Pinned native mask boundary unavailable.'}
    d = distance[choose]
    if not len(d):
        return {'status': 'unmeasured', 'reason': 'No eligible samples in this subset.'}
    return {'status': 'candidate_mask_distance', 'points': len(d), 'minimum_px': d.min(),
            'median_px': np.median(d), 'maximum_px': d.max()}


def _support(row, input_pins, spatial_split):
    from PIL import Image
    from scipy import ndimage
    a, eligible = row['arrays'], row['eligible']
    v = a['v'][eligible]
    height = np.minimum(3, np.searchsorted(HEIGHT_EDGES, v, side='right')-1)
    w = a['rear_weight'][eligible]
    rear = np.full(len(v), 'rear_geometry_unavailable', dtype='<U32')
    if row['has_rear']:
        rear[w == 0] = 'backdrop_proxy'
        rear[w == 1] = 'rear_proxy'
        rear[(w > 0) & (w < 1)] = 'mixed_rear_proxy'
    cells = [(f'v{b}/{r}', (height == b) & (rear == r)) for b in range(4) for r in sorted(set(rear))]
    cells = [(name, choose) for name, choose in cells if choose.any()]
    mask_sha = row['provenance'].get('mask_sha256')
    matches = sorted(p for p, digest in input_pins.items() if digest == mask_sha) if mask_sha else []
    distance, boundary = None, {'status': 'unmeasured', 'reason': 'Pinned mask not provided or unavailable.'}
    if matches:
        with Image.open(matches[0]) as image:
            mask = np.asarray(image.convert('L')) > 0
        if row['image_size'] is None or list(mask.shape[::-1]) != row['image_size']:
            raise ValueError('Pinned mask grid differs from native observation grid')
        xy = a['xy'][eligible]
        if not np.equal(xy, np.floor(xy)).all():
            boundary = {'status': 'unmeasured', 'reason': 'Native mask distance requires integer source pixel centers.'}
        else:
            coords = xy.astype(int)
            # Explicit outside padding avoids scipy's implicit all-true edge case.
            field = ndimage.distance_transform_edt(np.pad(mask, 1))[1:-1, 1:-1]
            distance = field[coords[:, 1], coords[:, 0]]
            boundary = {'status': 'measured_mask_geometry', 'source': matches[0], 'sha256': mask_sha,
                'equivalent_pinned_paths': matches, 'units': 'native_source_photo_pixels',
                'definition': 'Inside-mask Euclidean distance to first zero pixel center; outside mask is zero; padded exterior is zero.',
                'physical_lens_boundary': 'unverified', 'samples_outside_mask': np.count_nonzero(~mask[coords[:, 1], coords[:, 0]])}
    train, val = row['train'][eligible], row['validation'][eligible]
    cells_ledger = [{'cell': name, 'samples': int(choose.sum()), 'train': int((choose & train).sum()),
        'validation': int((choose & val).sum()),
        'validation_status': 'covered_by_frozen_samples' if (choose & val).any() else 'unmeasured'} for name, choose in cells]
    tiles = []
    if spatial_split:
        xy = a['xy']
        grid = spatial_split.get('grid', 4)
        tile = np.minimum(grid-1, np.floor(grid*(xy-np.asarray(spatial_split['lower_xy']))/np.asarray(spatial_split['span_xy'])).astype(int))
        for y in range(grid):
            for x in range(grid):
                choose = (tile[:, 0] == x) & (tile[:, 1] == y)
                if choose.any():
                    tiles.append({'tile_xy': [x, y], 'provided': int(choose.sum()), 'eligible': int((choose & eligible).sum()),
                        'train': int((choose & row['train']).sum()), 'validation': int((choose & row['validation']).sum())})
    return {'height': height, 'rear': rear, 'cells': cells, 'distance': distance,
        'ledger': {'observation_id': row['id'], 'photo_id': row['photo_id'], 'region_id': row['region_id'],
            'hypothesis_id': row['hypothesis_id'], 'source_sha256': row['source_sha256'],
            'provided_samples': len(eligible), 'eligible_samples': int(eligible.sum()),
            'excluded_samples': int((~eligible).sum()), 'boundary': boundary, 'cells': cells_ledger,
            'frozen_spatial_tiles': tiles, 'spatial_split': spatial_split,
            'train_boundary_distance': _distance_stats(distance, train), 'validation_boundary_distance': _distance_stats(distance, val),
            'rear_color_unknown_at_positive_weight': int(np.count_nonzero((w > 0) & ~np.isfinite(a['rear'][eligible]).all(axis=1)))}}


def _worker(request):
    runtime = Path(request['runtime_root'])
    # In a fresh process this cannot reuse the caller's reconstruction imports.
    sys.path.insert(0, str(runtime))
    from reconstruction import photo_lens_fit as replay
    if not Path(replay.__file__).resolve().is_relative_to(runtime / 'reconstruction'):
        raise ValueError('Imported fitter differs from explicitly selected runtime')
    source_pins = _source_snapshot(runtime)
    if source_pins != request['runtime_source_sha256']:
        raise ValueError('Runtime sources changed before replay')
    output = Path(request['output'])
    fit_path, observation_path = Path(request['fit']), Path(request['observations'])
    saved, manifest = _read(fit_path), _read(observation_path)
    if saved.get('method') != 'uncalibrated_photo_lens_ensemble_v1':
        raise ValueError('Unsupported fit artifact method; faithful replay unavailable')
    pins = dict(request['input_sha256'])
    boundary_pins, missing_optional = {}, []
    if request['observation_report'] is not None:
        bridge = _read(request['observation_report'])
        for path, digest in bridge.get('input_sha256', {}).items():
            if Path(path).is_file():
                if path in pins and pins[path] != digest:
                    raise ValueError('Conflicting observation source hashes')
                pins[path] = digest
                boundary_pins[path] = digest
            else:
                missing_optional.append({'path': path, 'sha256': digest, 'status': 'unavailable'})
    observations = []
    for row in manifest['observations']:
        path = _child(observation_path.parent, row['arrays']['path'])
        if _sha(path) != row['arrays']['sha256']:
            raise ValueError('Observation NPZ hash differs')
        pins[str(path)] = _sha(path)
        with np.load(path, allow_pickle=False) as archive:
            observations.append({**{k: v for k, v in row.items() if k != 'arrays'}, **{k: archive[k].copy() for k in archive.files}})
    _verify(pins)
    policy = replay.PhotoLensFitPolicy(**{'rear_content': 'explained', 'maximum_validation_share': 1.0, 'gradient_density_keyframes': 3, **saved['policy']})
    binding, records, branches, _, split = replay._prepare(observations, manifest['surface_binding'], policy)
    _parity(_plain(split), saved['spatial_split'], 'frozen split')
    input_hash = replay._hash({'surface_binding': binding, 'policy': asdict(policy), 'observations': [
        {k: r[k] for k in ('id', 'photo_id', 'region_id', 'hypothesis_id', 'source_sha256', 'provenance', 'numeric_sha256')} for r in records]})
    if input_hash != saved['input_sha256']:
        raise ValueError('Fit input hash differs from provided observation arrays/provenance/policy')
    record_by_id = {r['id']: r for r in records}
    valid_branches = {tuple(sorted(r['id'] for r in branch)) for branch in branches}
    split_by_photo = {r['photo_id']: r for r in split}
    supports = {r['id']: _support(r, boundary_pins, split_by_photo[r['photo_id']]) for r in records}
    candidates = saved['candidates']
    if len({c['candidate_id'] for c in candidates}) != len(candidates):
        raise ValueError('Duplicate candidate ID')
    if any(not isinstance(c['candidate_id'], str) or not re.fullmatch('[a-f0-9]{24}', c['candidate_id']) for c in candidates):
        raise ValueError('Invalid saved candidate ID')
    objective_supported = _linear_objective_supported(Path(replay.__file__).read_text(encoding='utf-8'))
    representatives, representative_reason = {}, None
    try:
        from reconstruction.photo_lens_stage import preview_representatives
        representatives = {family: c['candidate_id'] for family, c in preview_representatives(saved).items()}
    except (ImportError, AttributeError):
        representative_reason = 'Selected runtime has no stage representative ranking; none invented.'
    report = {'schema_version': 1, 'method': METHOD, 'status': 'running', 'quality_verdict': 'unmeasured',
        'accepted': False, 'parameter_identification': 'unmeasured', 'selected_material': None,
        'input_sha256': pins, 'fit_input_sha256': input_hash, 'runtime': {'root': str(runtime), 'python': sys.version,
            'source_sha256': source_pins, 'execution_assumption': 'Explicitly selected trusted local Python source, fresh subprocess; current interpreter/native dependencies.',
            'packages': {name: importlib.metadata.version(name) for name in ('numpy', 'scipy', 'Pillow')}},
        'diagnostic_driver': request['diagnostic_driver'],
        'optional_source_unavailable': missing_optional, 'height_bin_edges': HEIGHT_EDGES,
        'error_policy': {'tolerance_codes': policy.photo_tolerance_codes, 'required_fraction': policy.required_within_tolerance_fraction,
                         'source': 'unchanged saved fit policy; no additional outlier thresholds'},
        'replay_parity_tolerance': {'absolute': _PARITY_ATOL, 'relative': _PARITY_RTOL, 'scope': 'Floating-point reconstruction only, not appearance acceptance.'},
        'observations': [supports[r['id']]['ledger'] for r in records], 'candidates': [], 'failures': [], 'representatives': [],
        'representative_scope': 'Existing runtime stage family-ranking display choices; ranking uses spatial validation; no independent acceptance or new mask selection.',
        'representative_unavailable_reason': representative_reason,
        'limitations': ['All support categories are frozen input hypotheses, not semantic ground truth.',
            'Zero validation samples make that category unmeasured even when other categories are covered.',
            'Boundary distances are to source masks, not verified geometric lens boundaries.',
            'Photographic nuisance, camera/articulation and material errors are not uniquely separable.',
            'Candidate and roughness alternatives reuse photo samples and are not independent evidence.',
            'Residual concentration can suggest composition or material-family mismatch; it does not identify the cause.',
            'No samples, train/validation assignments, masks, parameters or thresholds were changed.']}
    representative_ids = set(representatives.values())
    for candidate in candidates:
        cid, assumptions = candidate['candidate_id'], candidate['assumptions']
        result = {'candidate_id': cid, 'family': assumptions['family'], 'lighting': assumptions['lighting'],
            'rear_mode': assumptions['rear'], 'photo_policy_status': candidate['photo_policy_status'],
            'optimizer_converged': candidate['optimizer']['converged'], 'replay_status': 'unavailable', 'observations': []}
        report['candidates'].append(result)
        try:
            ids = assumptions['observations']
            if tuple(sorted(ids)) not in valid_branches:
                raise ValueError('Candidate branch does not match preserved mask alternatives')
            model = replay._Model([record_by_id[i] for i in ids], assumptions['family'], assumptions['lighting'], assumptions['rear'], policy)
            x = _restore(model, candidate)
            difference = _parity(model.measurements(x), candidate['photo_measurements'], 'saved measurements')
            objective = {'status': 'unavailable', 'reason': 'Selected runtime objective convention not verified as linear least squares of _Model.residual.'}
            if objective_supported:
                value = float(.5*np.sum(model.residual(x)**2))
                delta = _parity(value, candidate['optimizer']['objective_including_priors'], 'saved objective')
                objective = {'status': 'verified', 'replayed': value, 'saved': candidate['optimizer']['objective_including_priors'], 'absolute_difference': delta}
            result.update(replay_status='verified', maximum_measurement_difference=difference, objective=objective)
        except (ValueError, KeyError, TypeError, IndexError, FloatingPointError) as error:
            result.update(replay_status='mismatch_or_unavailable', reason=str(error))
            report['failures'].append({'code': 'replay_unavailable', 'candidate_id': cid, 'reason': str(error)})
            continue
        representative_samples = []
        for data in model.data:
            row, a = data['observation'], data['arrays']; support = supports[row['id']]
            prediction = model.predict(x, data)
            signed = replay._interval_residual(prediction, a['code']); error = np.abs(signed)
            if not np.isfinite(prediction).all() or not np.isfinite(signed).all():
                raise ValueError('Nonfinite replay prediction')
            train, val = data['train'], data['validation']; tolerance = policy.photo_tolerance_codes
            item = {'observation_id': row['id'], 'photo_id': row['photo_id'], 'region_id': row['region_id'], 'hypothesis_id': row['hypothesis_id'],
                'train': _stats(error, signed, train, tolerance), 'validation': _stats(error, signed, val, tolerance), 'support_cells': [],
                'boundary_distance_of_policy_violating_samples': _distance_stats(support['distance'], np.any(error > tolerance, axis=1)),
                'rear_composition': [], 'intrinsic_height': [], 'pattern_hypotheses': []}
            result['observations'].append(item)
            for name, choose in support['cells']:
                cell = {'cell': name, 'train': _stats(error, signed, train & choose, tolerance),
                        'validation': _stats(error, signed, val & choose, tolerance)}
                item['support_cells'].append(cell)
                if not np.any(val & choose):
                    report['failures'].append({'code': 'validation_support_unmeasured', 'candidate_id': cid, 'observation_id': row['id'], 'cell': name,
                        'train_points': int(np.count_nonzero(train & choose)),
                        'training_policy_violating_points': int(np.count_nonzero(np.any(error > tolerance, axis=1) & train & choose))})
            for split_name, select in (('train', train), ('validation', val)):
                if select.any() and np.mean(error[select] <= tolerance) < policy.required_within_tolerance_fraction:
                    report['failures'].append({'code': 'frozen_photo_policy_failed', 'candidate_id': cid, 'observation_id': row['id'], 'split': split_name})
            for label in sorted(set(support['rear'])):
                select = support['rear'] == label
                item['rear_composition'].append({'category': label, **_stats(error, signed, select, tolerance),
                    'scope': 'Residual conditional on geometry-derived composition hypothesis; cause not identified.'})
            rear_points = (support['rear'] == 'rear_proxy') | (support['rear'] == 'mixed_rear_proxy')
            backdrop_points = support['rear'] == 'backdrop_proxy'
            violating = np.any(error > tolerance, axis=1)
            if rear_points.any() and backdrop_points.any():
                total_violations = int(violating.sum())
                enrichment = (float(np.mean(rear_points[violating])) - float(np.mean(rear_points))) if total_violations else None
                item['pattern_hypotheses'].append({'kind': 'composition_support_comparison',
                    'rear_mean_absolute_error_codes': error[rear_points].mean(),
                    'backdrop_mean_absolute_error_codes': error[backdrop_points].mean(),
                    'rear_share_of_samples': np.mean(rear_points),
                    'rear_share_of_policy_violating_samples': np.mean(rear_points[violating]) if total_violations else None,
                    'rear_policy_violation_share_minus_sample_share': enrichment,
                    'interpretation': 'Error enrichment under a rear proxy is consistent with composition/support mismatch; material, lighting and geometry causes remain confounded.'})
            if len(error):
                maximum = int(np.argmax(error.max(axis=1)))
                item['maximum_error_sample'] = {'xy': a['xy'][maximum], 'maximum_channel_error_codes': error[maximum].max(),
                    'split': 'validation' if val[maximum] else 'train', 'rear_category': str(support['rear'][maximum]),
                    'intrinsic_v': a['v'][maximum], 'native_mask_boundary_distance_px': support['distance'][maximum] if support['distance'] is not None else None}
            for b in range(4):
                item['intrinsic_height'].append({'v_interval': list(HEIGHT_EDGES[b:b+2]), 'last_interval_includes_one': b == 3,
                    **_stats(error, signed, support['height'] == b, tolerance)})
            if cid in representative_ids:
                full_prediction = np.full_like(row['arrays']['code'], np.nan)
                full_residual = np.full_like(full_prediction, np.nan)
                full_prediction[row['eligible']] = prediction;full_residual[row['eligible']] = signed
                distance = np.full(len(row['eligible']), np.nan)
                if support['distance'] is not None:
                    distance[row['eligible']] = support['distance']
                representative_samples.append((row, {'xy': row['arrays']['xy'], 'raw_code_rgb': row['arrays']['code'],
                    'predicted_code_rgb_unclipped': full_prediction, 'interval_residual_codes': full_residual,
                    'eligible': row['eligible'], 'train': row['train'], 'validation': row['validation'],
                    'intrinsic_v': row['arrays']['v'], 'rear_geometry_proxy_weight': row['arrays']['rear_weight'],
                    'native_mask_boundary_distance_px': distance}))
        if cid in representative_ids:
            folder = output / 'representatives' / cid
            folder.mkdir(parents=True)
            entries = []
            for index, (row, arrays) in enumerate(representative_samples):
                path = folder / f'observation-{index:03d}.npz'
                np.savez_compressed(path, **arrays)
                entries.append({'observation_id': row['id'], 'source_sha256': row['source_sha256'],
                    'path': path.relative_to(output).as_posix(), 'sha256': _sha(path), 'unknown_values': 'NaN; never measured zero'})
            report['representatives'].append({'candidate_id': cid, 'families': [f for f, i in representatives.items() if i == cid],
                'appearance': candidate['appearance'], 'nuisance': candidate['nuisance'], 'observations': entries})
    _verify(pins)
    if _source_snapshot(runtime) != source_pins:
        raise ValueError('Runtime sources changed during replay')
    verified = [c for c in report['candidates'] if c['replay_status'] == 'verified']
    report['status'] = 'diagnostics_complete' if len(verified) == len(candidates) else 'replay_incomplete'
    report['summary'] = {'candidates_provided': len(candidates), 'candidates_replayed': len(verified),
        'all_candidates_audited': len(verified) == len(candidates),
        'candidate_objectives_verified': sum(c['objective']['status'] == 'verified' for c in verified),
        'candidate_objectives_unavailable': sum(c['objective']['status'] == 'unavailable' for c in verified),
        'candidate_cell_validation_unmeasured': sum(f['code'] == 'validation_support_unmeasured' for f in report['failures']),
        'candidate_cells_with_training_policy_violations_and_unmeasured_validation': sum(
            f['code'] == 'validation_support_unmeasured' and f['training_policy_violating_points'] > 0 for f in report['failures']),
        'candidate_cell_counts_are_not_independent_evidence': True}
    _write(output / 'report.json', report)
    return report


def diagnose_photo_lens_fit(fit_path: Path, observations_path: Path, output: Path, *,
                           observation_report_path: Path | None = None, runtime_root: Path | None = None) -> dict:
    """Audit saved artifacts in an isolated replay process; create only new output.

    ``runtime_root`` is a directory containing the explicitly selected trusted
    reconstruction package. Omission chooses this module's current package.
    Optional bridge sources that are missing make boundary coverage unavailable;
    existing sources with changed hashes are rejected. Output cannot be inside
    the fit/observation artifact directories or the selected runtime package.
    """
    fit_path, observations_path, output = (Path(p).resolve() for p in (fit_path, observations_path, output))
    runtime = Path(runtime_root).resolve() if runtime_root is not None else Path(__file__).resolve().parent.parent
    bridge = Path(observation_report_path).resolve() if observation_report_path is not None else None
    if any(output.is_relative_to(p) for p in (fit_path.parent, observations_path.parent, runtime / 'reconstruction')):
        raise ValueError('Diagnostic output must not mutate source artifact or runtime directories')
    if output.exists() and any(output.iterdir()):
        raise ValueError('Use an empty diagnostic output directory')
    driver_source = Path(__file__).resolve()
    driver_bytes = driver_source.read_bytes()
    driver_sha = hashlib.sha256(driver_bytes).hexdigest()
    driver_path = output / 'replay-driver.py'
    paths = [fit_path, observations_path] + ([bridge] if bridge else [])
    request = {'schema_version': 1, 'fit': str(fit_path), 'observations': str(observations_path), 'output': str(output),
        'observation_report': str(bridge) if bridge else None, 'runtime_root': str(runtime),
        'runtime_source_sha256': _source_snapshot(runtime), 'input_sha256': {str(p): _sha(p) for p in paths},
        'diagnostic_driver': {'origin_path': str(driver_source), 'sha256': driver_sha,
            'executed_copy': 'replay-driver.py', 'bytecode_writes_disabled': True}}
    request['input_sha256'][str(driver_path)] = driver_sha
    output.mkdir(parents=True, exist_ok=True)
    driver_path.write_bytes(driver_bytes)
    request_path = output / 'request.json'
    _write(request_path, request)
    process = subprocess.run([sys.executable, '-B', str(driver_path), '--_worker', str(request_path)],
                             capture_output=True, text=True, check=False)
    _verify(request['input_sha256'])
    if _sha(driver_source) != driver_sha:
        raise ValueError('Diagnostic implementation changed during replay')
    if _source_snapshot(runtime) != request['runtime_source_sha256']:
        raise ValueError('Runtime sources changed during replay')
    if process.returncode != 0:
        _write(output / 'failure.json', {'status': 'diagnostic_execution_failed', 'quality_verdict': 'unmeasured',
            'accepted': False, 'returncode': process.returncode, 'error': process.stderr[-12000:]})
        raise ValueError('Frozen diagnostic replay failed; see failure.json in the preserved output directory')
    return _read(output / 'report.json')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fit', type=Path)
    parser.add_argument('--observations', type=Path)
    parser.add_argument('--observation-report', type=Path)
    parser.add_argument('--runtime-root', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--_worker', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args._worker:
        _worker(_read(args._worker))
        return 0
    if not args.fit or not args.observations or not args.output:
        parser.error('--fit, --observations and --output are required')
    report = diagnose_photo_lens_fit(args.fit, args.observations, args.output,
        observation_report_path=args.observation_report, runtime_root=args.runtime_root)
    print(json.dumps({k: report[k] for k in ('status', 'summary', 'quality_verdict', 'accepted')}))
    return 0 if report['summary']['all_candidates_audited'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
