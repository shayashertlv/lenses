"""Summarize one or more completed inferred-grouping jobs into one table.

Reads each job's journal and reports: stage timings, hypothesis ranking with
composition scores, the selected hypothesis, fit support and exploration, the
appearance selection with its alternatives, and the artifact hashes. This is a
reading aid over recorded evidence; it accepts nothing and changes no job.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def summarize_job(job: Path) -> dict:
    job = Path(job).resolve()
    journal = json.loads((job/'job.json').read_bytes())
    report = json.loads((job/'report.json').read_bytes())
    timings = {}
    for name, attempts in journal['stages'].items():
        stage = attempts[-1]
        if 'finished_at' in stage:
            timings[name] = (datetime.fromisoformat(stage['finished_at'])-datetime.fromisoformat(stage['started_at'])).total_seconds()
    optics = report.get('optical_candidates') or {}
    row = {'job': str(job), 'status': report['status'], 'quality_verdict': report['quality_verdict'],
           'stage_seconds': timings, 'total_seconds': sum(timings.values()),
           'candidate_sha256': report['candidate']['sha256'], 'candidate_selection': report['candidate']['selection'],
           'grouping_mode': optics.get('grouping_mode'), 'hypotheses': [], 'appearance': None}
    groups = optics.get('physical_groups')
    if groups:
        stage_report = json.loads((job/groups['report']).read_bytes())
        row['physical_groups'] = {'status': stage_report['status'], 'components': stage_report['inventory']['components'],
                                  'hypothesis_count': stage_report.get('hypothesis_count'), 'bridges_executed': stage_report.get('bridges_executed'),
                                  'selected_index': (stage_report.get('selected_hypothesis') or {}).get('index')}
        for h in sorted(stage_report['hypotheses'], key=lambda h: h['composition_rank']):
            full = [t for t in h['composition_table'] if t['interpretation_id'] == 'full']
            row['hypotheses'].append({'index': h['index'], 'rank': h['composition_rank'], 'rungs': h['rungs'],
                                      'score': h['composition_score'], 'bridge': (h['bridge'] or {}).get('status'),
                                      'optical_groups': [g['members'] for g in h['consensus_optical_groups']],
                                      'clean_by_view': {t['view_id']: t['clean_transmission_fraction'] for t in full},
                                      'excluded_by_view': {t['view_id']: t['excluded_fraction'] for t in full},
                                      'no_geometry_by_view': {t['view_id']: t['aperture_without_geometry_fraction'] for t in full}})
    if optics.get('fit_report'):
        fitted = json.loads((job/optics['fit_report']).read_bytes())
        fit = json.loads((job/'stages'/'photo_lens_fit'/Path(optics['fit_report']).parts[2]/fitted['fit']['path']).read_bytes()) if 'fit' in fitted else None
        if fit:
            row['fit'] = {'status': fit['status'], 'candidates': len(fit['candidates']),
                          'unresolved': fit['exploration']['unresolved_optimizer_runs'],
                          'spatial_split': [(s['photo_id'], s.get('grid', 4)) for s in fit['spatial_split']],
                          'support': {gid: g['support_unique_pixels_by_photo'] for gid, g in fit['candidates'][0]['groups'].items()}}
    if optics.get('appearance_selection'):
        selection = json.loads((job/optics['appearance_selection']['report']).read_bytes())
        row['appearance'] = {'selected': selection['selected']['family_assignment'] if selection.get('selected') else None,
                             'score_basis': (selection.get('selected') or {}).get('score_basis'),
                             'score_codes': (selection.get('selected') or {}).get('score_codes'),
                             'best_score_codes': selection.get('best_score_codes'), 'identifiability': selection.get('identifiability'),
                             'photo_policy_pass': selection.get('photo_policy_pass'), 'policy_verdict': selection.get('policy_verdict'),
                             'any_candidate_within_policy': selection.get('any_candidate_within_policy'),
                             'alternatives': [(a['family_assignment'], a['score_codes']) for a in selection.get('alternatives', [])],
                             'candidate_appearance_sha256': (report['candidate'].get('appearance') or {}).get('sha256')}
    return row


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('jobs', nargs='+', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    rows = [summarize_job(job) for job in args.jobs]
    if args.output:
        args.output.write_text(json.dumps(rows, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    for row in rows:
        best = row['hypotheses'][0] if row['hypotheses'] else None
        print(json.dumps({'job': Path(row['job']).name, 'status': row['status'], 'seconds': round(row['total_seconds'], 1),
                          'selected_hypothesis': best and {'index': best['index'], 'score': round(best['score'], 3), 'groups': best['optical_groups']},
                          'appearance': row['appearance'] and {k: row['appearance'][k] for k in ('selected', 'score_codes', 'best_score_codes', 'identifiability', 'photo_policy_pass', 'policy_verdict')}}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
