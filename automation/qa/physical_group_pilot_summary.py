"""Summarize a completed appearance pilot without accepting anything.

Reads the pilot directory (regions, composition cross-check, joint stage
output) and writes ``pilot-summary.json`` beside them. Every number is a
diagnostic of the explored candidates; convergence, low error or an exported
preview is not appearance success or a selected material.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _best(rows, key):
    rows = [r for r in rows if r.get(key) is not None]
    return min(rows, key=lambda r: r[key]) if rows else None


def summarize(pilot: Path, joint: Path) -> dict:
    pilot, joint = Path(pilot).resolve(), Path(joint).resolve()
    stage = json.loads((joint/'report.json').read_bytes())
    fit_ref = stage['fit']
    fit = json.loads((joint/fit_ref['path']).read_bytes())
    if _sha(joint/fit_ref['path']) != fit_ref['sha256']:
        raise ValueError('Joint fit artifact differs from its stage receipt')
    candidates = fit['candidates']
    statuses = Counter(c['photo_policy_status'] for c in candidates)
    converged = sum(1 for c in candidates if c['optimizer'].get('converged'))
    by_group_family = defaultdict(list)
    for c in candidates:
        for gid, group in c['groups'].items():
            train_err = [m['train']['mean_absolute_interval_error_codes'] for m in group['photo_metrics'] if m.get('train')]
            val = [m['validation']['mean_absolute_interval_error_codes'] for m in group['photo_metrics']
                   if m.get('validation') and m['validation'].get('mean_absolute_interval_error_codes') is not None]
            within = [m['train']['fraction_channels_within_policy'] for m in group['photo_metrics'] if m.get('train')]
            by_group_family[(gid, group['family'])].append({
                'candidate_id': c['candidate_id'], 'lighting': c['assumptions']['lighting'],
                'converged': bool(c['optimizer'].get('converged')), 'policy': group['photo_policy_status'],
                'train_mean_error_codes': max(train_err) if train_err else None,
                'validation_mean_error_codes': max(val) if val else None,
                'train_fraction_within_policy': min(within) if within else None})
    tables = []
    for (gid, family), rows in sorted(by_group_family.items()):
        best_train = _best(rows, 'train_mean_error_codes'); best_val = _best(rows, 'validation_mean_error_codes')
        tables.append({'group_id': gid, 'family': family, 'candidates': len(rows),
                       'converged': sum(r['converged'] for r in rows),
                       'policy_statuses': dict(Counter(r['policy'] for r in rows)),
                       'best_train': best_train, 'best_validation': best_val,
                       'best_train_fraction_within_policy': max((r['train_fraction_within_policy'] for r in rows
                                                                 if r['train_fraction_within_policy'] is not None), default=None)})
    envelopes = {gid: {k: v for k, v in env.items() if k != 'candidate_ids'}
                 for gid, env in fit.get('ar_prediction_envelopes_by_group', {}).items()}
    cross = None
    cross_path = pilot/'composition-cross-check.json'
    if cross_path.exists():
        cross = json.loads(cross_path.read_bytes())['maximum_absolute_difference']
    regions = json.loads((pilot/'regions'/'report.json').read_bytes())
    observation_report = json.loads((joint/stage['inputs']['observation_report']).read_bytes()) if 'observation_report' in stage.get('inputs', {}) else None
    summary = {'schema_version': 1, 'method': 'physical_group_appearance_pilot_summary_v1', 'accepted': False,
               'quality_verdict': 'unmeasured', 'selected_material': None,
               'stage_status': stage['status'], 'fit_status': fit['status'], 'diagnosis': fit.get('diagnosis'),
               'exploration': {k: v for k, v in fit['exploration'].items() if k != 'unsupported_configurations'},
               'unsupported_configurations': len(fit['exploration'].get('unsupported_configurations', [])),
               'candidates': len(candidates), 'converged': converged,
               'photo_policy_statuses': dict(statuses),
               'jacobian_mode': fit.get('policy', {}).get('jacobian_mode', stage.get('policy', {}).get('jacobian_mode')),
               'per_group_family': tables, 'ar_prediction_envelopes_by_group': envelopes,
               'previews': [{k: p[k] for k in ('family_assignment', 'candidate_id', 'status', 'path', 'sha256')} for p in stage.get('previews', [])],
               'preview_ranking': stage.get('preview_ranking'),
               'regions': [{'id': p['id'], 'status': p['status'],
                            'regions': [{'id': r['id'], 'prior_parts': r['prior'].get('part_indices')} for r in p['regions']]}
                           for p in regions['photos']],
               'unbound_region_prior_parts': observation_report.get('unbound_region_prior_parts') if observation_report else None,
               'composition_cross_check_maximum_absolute_difference': cross,
               'artifacts': {'stage_report_sha256': _sha(joint/'report.json'), 'fit_sha256': fit_ref['sha256'],
                             'region_report_sha256': _sha(pilot/'regions'/'report.json')},
               'limitations': ['Errors are interval errors in 8-bit codes against uncalibrated photographs under frozen cameras and SAM masks.',
                               'A photo-policy pass or a low error selects nothing; previews are diagnostic exports of whole joint candidates.',
                               'One design, one hypothesis, one optimizer mode; not generalization evidence.']}
    (pilot/'pilot-summary.json').write_text(json.dumps(summary, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pilot', type=Path, required=True)
    parser.add_argument('--joint', type=Path, required=True)
    args = parser.parse_args(argv)
    summary = summarize(args.pilot, args.joint)
    print(json.dumps({k: summary[k] for k in ('stage_status', 'fit_status', 'diagnosis', 'candidates', 'converged', 'photo_policy_statuses')}))
    for row in summary['per_group_family']:
        print(json.dumps({k: row[k] for k in ('group_id', 'family', 'converged', 'policy_statuses', 'best_train_fraction_within_policy')}),
              'best train', row['best_train'] and round(row['best_train']['train_mean_error_codes'], 2),
              'best val', row['best_validation'] and round(row['best_validation']['validation_mean_error_codes'], 2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
