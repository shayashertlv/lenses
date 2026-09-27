"""Refit a completed inferred-grouping job's joint photo-lens stage under a changed fit policy.

The job's own preparation report and region report are reused unchanged, so the
refit measures only the fit policy. Output is a fresh joint-stage directory plus
the appearance selection, never a modification of the original job.

    python -m qa.refit_job data/jobs/<job> --output data/refits/<name> [--rear-content excluded|explained]
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import time

from reconstruction.appearance_selection import select_appearance
from reconstruction.joint_photo_lens_fit import JointPhotoLensFitPolicy
from reconstruction.joint_photo_lens_stage import run_joint_photo_lens_stage
from reconstruction.photo_lens_fit import PhotoLensFitPolicy


def refit_job(job: Path, output: Path, *, overrides: dict, tolerance_codes=1.0, jacobian_mode=None) -> dict:
    job, output = Path(job).resolve(), Path(output).resolve()
    report = json.loads((job / 'report.json').read_text('utf-8'))
    fit_report = job / report['optical_summary']['fit_report'] if 'optical_summary' in report else None
    if fit_report is None or not fit_report.exists():
        candidates = sorted(job.glob('stages/photo_lens_fit/attempt_*/request.json'))
        if not candidates:
            raise ValueError('The job has no joint photo-lens stage request')
        request_path = candidates[-1]
    else:
        request_path = fit_report.parent / 'request.json'
    request = json.loads(request_path.read_text('utf-8'))
    saved = request['policy']
    photo = PhotoLensFitPolicy(**{'rear_content': 'explained', 'maximum_validation_share': 1.0, 'gradient_density_keyframes': 3, **saved['photo_policy'], **overrides})
    joint = JointPhotoLensFitPolicy(photo_policy=photo, **{k: v for k, v in saved.items() if k != 'photo_policy'},
                                    ) if jacobian_mode is None else JointPhotoLensFitPolicy(
        photo_policy=photo, **{k: v for k, v in saved.items() if k not in ('photo_policy', 'jacobian_mode')}, jacobian_mode=jacobian_mode)
    started = time.monotonic()
    fitted = run_joint_photo_lens_stage(Path(request['preparation_report']), Path(request['region_report']),
                                        output / 'joint', policy=joint, maximum_samples_per_hypothesis=request['maximum_samples'],
                                        group_photo_exclusions=request.get('group_photo_exclusions'))
    fit = json.loads((output / 'joint' / fitted['fit']['path']).read_text('utf-8'))
    selection = select_appearance(fit, fitted, tolerance_codes=tolerance_codes)
    (output / 'selection.json').write_text(json.dumps(selection, indent=2, allow_nan=False), encoding='utf-8')
    summary = {'job': str(job), 'source_request': str(request_path), 'policy': asdict(joint), 'overrides': overrides,
               'seconds': time.monotonic() - started, 'fit_status': fitted['status'],
               'selected': selection.get('selected', {}).get('family_assignment') if selection.get('selected') else None,
               'score_codes': selection.get('selected', {}).get('score_codes') if selection.get('selected') else None,
               'best_score_codes': selection.get('best_score_codes'), 'photo_policy_pass': selection.get('photo_policy_pass'),
               'identifiability': selection.get('identifiability'),
               'alternatives': [[r['family_assignment'], r['score_codes'], r['photo_policy_status']] for r in selection.get('alternatives', [])],
               'accepted': False, 'quality_verdict': 'unmeasured'}
    (output / 'summary.json').write_text(json.dumps(summary, indent=2, allow_nan=False), encoding='utf-8')
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('job', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--rear-content', choices=('excluded', 'explained'), default='excluded')
    parser.add_argument('--jacobian-mode', choices=('finite_difference', 'analytic'))
    parser.add_argument('--appearance-tolerance-codes', type=float, default=1.0)
    args = parser.parse_args(argv)
    summary = refit_job(args.job, args.output, overrides={'rear_content': args.rear_content},
                        tolerance_codes=args.appearance_tolerance_codes, jacobian_mode=args.jacobian_mode)
    print(json.dumps({k: summary[k] for k in ('seconds', 'fit_status', 'selected', 'score_codes', 'best_score_codes',
                                                  'photo_policy_pass', 'identifiability')}, indent=2))


if __name__ == '__main__':
    main()
