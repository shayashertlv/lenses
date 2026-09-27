"""Residual overlays for one candidate of a completed joint photo-lens fit.

    python -m qa.joint_fit_residuals data/jobs/<job> --output data/residuals/<name> [--candidate <id>]

The candidate defaults to the shipped one (the appearance selection). The
fit's own observations, frozen split and policy are reloaded, the candidate's
configuration and its checkpointed parameter vector are replayed through the
unchanged joint model, and the replayed per-observation means are checked
against the saved photo metrics before anything is drawn. Outputs per
photograph: an overlay of every eligible sample coloured by its absolute
interval error (green under 4 codes, yellow to 8, orange to 16, red above),
training samples as dots and validation samples as rings, a 3x crop around
the samples, and a table of error against intrinsic height, incidence angle
and distance from the edge of the sampled region. It reads; it never refits,
selects or accepts anything.
"""
from __future__ import annotations

import argparse
import io
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

from reconstruction import joint_photo_lens_fit as joint
from reconstruction import photo_lens_fit as single

BANDS = ((4., (40, 180, 60)), (8., (230, 200, 30)), (16., (240, 130, 30)), (np.inf, (220, 40, 40)))


def _read(path):
    return json.loads(Path(path).read_text('utf-8'))


def _load_group(path):
    manifest = _read(path)
    observations = []
    for record in manifest['observations']:
        metadata = {k: v for k, v in record.items() if k != 'arrays'}
        with np.load(io.BytesIO((Path(path).parent / record['arrays']['path']).read_bytes()), allow_pickle=False) as archive:
            arrays = {k: archive[k].copy() for k in archive.files}
        observations.append({**metadata, **arrays})
    return {'surface_binding': manifest['surface_binding'], 'observations': observations}


def _colour(error):
    for limit, colour in BANDS:
        if error < limit:
            return colour
    return BANDS[-1][1]


def replay(job: Path, output: Path, candidate_id: str | None = None) -> dict:
    job, output = Path(job).resolve(), Path(output).resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError('Use a fresh empty output directory')
    output.mkdir(parents=True, exist_ok=True)
    report = _read(job / 'report.json')
    optics = report['optical_candidates']
    stage_folder = job / Path(optics['fit_report']).parent
    stage = _read(stage_folder / 'report.json')
    fit = _read(stage_folder / stage['fit']['path'])
    if fit.get('method') != 'joint_uncalibrated_photo_lens_ensemble_v1':
        raise ValueError('Only the joint ensemble fit is supported')
    if candidate_id is None:
        selection = _read(job / optics['appearance_selection']['report'])
        candidate_id = selection['selected']['candidate_id']
    candidate = next((c for c in fit['candidates'] if c['candidate_id'] == candidate_id), None)
    if candidate is None:
        raise ValueError('Candidate not found in the fit')
    saved_policy = fit['policy']
    photo_policy = single.PhotoLensFitPolicy(**{'rear_content': 'explained', 'maximum_validation_share': 1.0, 'gradient_density_keyframes': 3, **saved_policy['photo_policy']})
    policy = joint.JointPhotoLensFitPolicy(photo_policy=photo_policy, **{k: v for k, v in saved_policy.items() if k != 'photo_policy'})
    groups = [_load_group(stage_folder / relative) for _, relative in sorted(stage['inputs']['groups'].items())]
    bindings, records, branches, _, split = joint._prepare_joint(groups, policy)
    assumptions = candidate['assumptions']
    config = {k: assumptions[k] for k in ('family_assignment', 'lighting', 'rear_modes', 'observations')}
    run_key = single._hash({'input': fit['input_sha256'], 'configuration': config, 'start': assumptions['start']})
    checkpoint = _read(stage_folder / 'checkpoints' / f'{run_key}.json')['payload']
    if checkpoint.get('run_key') != run_key or 'fit' not in checkpoint:
        raise ValueError('The candidate has no completed checkpoint')
    wanted = sorted(json.dumps(o, sort_keys=True) for o in config['observations'])
    branch = next((b for b in branches if sorted(json.dumps(joint._identity(r), sort_keys=True) for r in b) == wanted), None)
    if branch is None:
        raise ValueError('The candidate branch is absent from the replayed preparation')
    rear_bindings = joint._rear_bindings(fit.get('rear_source_bindings'), records)
    p = policy.photo_policy
    model = joint._JointModel(branch, config['family_assignment'], config['lighting'], config['rear_modes'], p, rear_bindings)
    x = np.asarray(checkpoint['fit']['x'], dtype=float)
    if x.shape != (len(model.lower),):
        raise ValueError('Checkpointed parameter vector does not match the replayed model')
    residual = model.residual(x)
    if not np.isclose(float(np.dot(residual, residual) / 2), checkpoint['fit']['cost'], rtol=1e-8, atol=1e-10):
        raise ValueError('Replayed objective differs from the checkpoint; not the same model')
    # Photos: the normalized images of the input stage.
    journal = _read(job / 'job.json')
    input_folder = job / journal['stages']['input'][-1]['directory']
    bundle = _read(input_folder / 'manifest.json')
    photos = {item['id']: Image.open(input_folder / item['normalized']['path']).convert('RGB') for item in bundle['photos']}
    samples, verification = [], []
    for gid, local in model.models.items():
        saved_metrics = {m['observation_id']: m for m in candidate['groups'][gid]['photo_metrics']}
        xl = x[model.maps[gid]]
        for d in local.data:
            source = d['observation']
            signed = single._interval_residual(local.predict(xl, d), d['arrays']['code'])
            error = np.abs(signed)
            metric = saved_metrics.get(source['original_id'])
            for name in ('train', 'validation'):
                e = error[d[name]]
                replayed = float(e.mean()) if len(e) else None
                saved = (metric or {}).get(name, {}).get('mean_absolute_interval_error_codes') if metric else None
                verification.append({'group_id': gid, 'observation_id': source['original_id'], 'split': name, 'points': int(len(e)),
                                     'replayed_mean': replayed, 'saved_mean': saved,
                                     'agrees': (replayed is None and saved is None) or (replayed is not None and saved is not None and abs(replayed - saved) < 1e-6)})
            arrays = d['arrays']
            # Distance from each eligible sample to the nearest rear-content sample of the same observation
            # (a sample the policy excluded because opaque geometry lies behind the lens along its ray).
            full = source['arrays']
            rear_xy = full['xy'][full['rear_weight'] > 0] if 'rear_weight' in full else np.zeros((0, 2))
            if len(rear_xy):
                rear_distance = np.sqrt(((arrays['xy'][:, None, :].astype(float) - rear_xy[None, :, :].astype(float)) ** 2).sum(axis=2)).min(axis=1)
            else:
                rear_distance = np.full(len(error), np.inf)
            for i in range(len(error)):
                samples.append({'group_id': gid, 'photo_id': source['photo_id'], 'observation_id': source['original_id'],
                                'x': int(arrays['xy'][i][0]), 'y': int(arrays['xy'][i][1]),
                                'split': 'train' if d['train'][i] else 'validation' if d['validation'][i] else 'none',
                                'error_max': float(error[i].max()), 'error_mean': float(error[i].mean()), 'signed_mean': float(signed[i].mean()),
                                'v': float(arrays['v'][i]), 'angle': float(arrays['angle'][i]),
                                'rear_distance_px': float(rear_distance[i]) if np.isfinite(rear_distance[i]) else None,
                                'code': arrays['code'][i].tolist(), 'background': arrays['background'][i].tolist()})
    if not all(v['agrees'] for v in verification):
        (output / 'verification.json').write_text(json.dumps(verification, indent=2), encoding='utf-8')
        raise ValueError('Replayed means differ from the saved photo metrics; see verification.json')
    # Overlays and tables per (photo, group).
    tables = []
    for photo_id, image in photos.items():
        overlay = image.copy()
        draw = ImageDraw.Draw(overlay)
        rows = [s for s in samples if s['photo_id'] == photo_id]
        if not rows:
            continue
        for s in rows:
            colour = _colour(s['error_max'])
            if s['split'] == 'train':
                draw.ellipse((s['x'] - 2, s['y'] - 2, s['x'] + 2, s['y'] + 2), fill=colour)
            elif s['split'] == 'validation':
                draw.ellipse((s['x'] - 3, s['y'] - 3, s['x'] + 3, s['y'] + 3), outline=colour, width=2)
        overlay.save(output / f'{photo_id}-residuals.png')
        xs, ys = [s['x'] for s in rows], [s['y'] for s in rows]
        pad = 24
        box = (max(0, min(xs) - pad), max(0, min(ys) - pad), min(image.width, max(xs) + pad), min(image.height, max(ys) + pad))
        crop = overlay.crop(box)
        crop.resize((crop.width * 3, crop.height * 3), Image.NEAREST).save(output / f'{photo_id}-residuals-crop.png')
        for gid in sorted({s['group_id'] for s in rows}):
            group_rows = [s for s in rows if s['group_id'] == gid]
            mask = np.zeros((image.height, image.width), bool)
            for s in group_rows:
                mask[s['y'], s['x']] = True
            # Distance from the edge of the sampled region: the samples are a sparse subset of the lens
            # pixels, so the region is closed with a disk of about twice the median sample spacing.
            points = np.array([(s['y'], s['x']) for s in group_rows], float)
            deltas = np.linalg.norm(points[:, None, :] - points[None, :, :], axis=2)
            np.fill_diagonal(deltas, np.inf)
            spacing = float(np.median(deltas.min(axis=1))) if len(points) > 1 else 1.
            radius = max(2, int(round(2 * spacing)))
            yy, xx = np.mgrid[-radius:radius + 1, -radius:radius + 1]
            filled = ndimage.binary_closing(ndimage.binary_dilation(mask, structure=(xx ** 2 + yy ** 2) <= radius ** 2),
                                            structure=(xx ** 2 + yy ** 2) <= radius ** 2)
            distance = np.maximum(0., ndimage.distance_transform_edt(filled) - radius)
            for s in group_rows:
                s['edge_distance_px'] = float(distance[s['y'], s['x']])
            errors = np.array([s['error_max'] for s in group_rows])
            signed_means = np.array([s['signed_mean'] for s in group_rows])

            def bucket(key, edges, label):
                values = np.array([s[key] if s[key] is not None else np.inf for s in group_rows], float)
                out = []
                for lo, hi in zip(edges, edges[1:]):
                    pick = (values >= lo) & (values < hi)
                    if pick.any():
                        out.append({label: [float(lo), float(hi)], 'samples': int(pick.sum()), 'mean_error_max': float(errors[pick].mean()),
                                    'within_8_fraction': float(np.mean(errors[pick] <= 8.)),
                                    'mean_signed_prediction_minus_code': float(np.mean(signed_means[pick]))})
                return out
            tables.append({'photo_id': photo_id, 'group_id': gid, 'samples': len(group_rows),
                           'mean_error_max': float(errors.mean()), 'within_8_fraction': float(np.mean(errors <= 8.)),
                           'by_edge_distance': bucket('edge_distance_px', [0, 3, 6, 12, 24, 1e9], 'edge_distance_px'),
                           'by_intrinsic_height': bucket('v', [0, .25, .5, .75, 1.0001], 'v'),
                           'by_incidence_angle': bucket('angle', [0, 20, 40, 60, 90.0001], 'angle_degrees'),
                           'by_rear_distance': bucket('rear_distance_px', [0, 8, 16, 32, 64, 1e9], 'rear_distance_px')
                           if any(s['rear_distance_px'] is not None for s in group_rows) else []})
    summary = {'job': str(job), 'candidate_id': candidate_id, 'family_assignment': config['family_assignment'], 'lighting': config['lighting'],
               'rear_modes': config['rear_modes'], 'run_key': run_key, 'samples': len(samples), 'verification': verification,
               'tables': tables, 'colour_bands_codes': [b[0] if np.isfinite(b[0]) else None for b in BANDS],
               'accepted': False, 'quality_verdict': 'unmeasured',
               'limitations': ['The edge distance is measured from the sampled region, a proxy for the lens boundary in the photograph.',
                               'Overlays show the shipped candidate under its fitted nuisance; they are a reading aid, not a validation.']}
    (output / 'summary.json').write_text(json.dumps(summary, indent=2, allow_nan=False), encoding='utf-8')
    (output / 'samples.json').write_text(json.dumps(samples, allow_nan=False), encoding='utf-8')
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('job', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--candidate')
    args = parser.parse_args(argv)
    summary = replay(args.job, args.output, args.candidate)
    for t in summary['tables']:
        print(json.dumps({k: t[k] for k in ('photo_id', 'group_id', 'samples', 'mean_error_max', 'within_8_fraction')}))
        for row in t['by_edge_distance']:
            print('   edge', row)
        for row in t['by_intrinsic_height']:
            print('   height', row)
        for row in t['by_incidence_angle']:
            print('   angle', row)
        for row in t['by_rear_distance']:
            print('   rear', row)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
