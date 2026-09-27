"""Replay a completed job's physical-group stage under the current code and compare its consensus groups.

    python -m qa.replay_group_stage data/jobs/<job> --output data/group-replays/<name> [--aperture-weights data/models/glasses-detector-v1]

The job's own candidate model, refinement report and normalized photos are
reused unchanged, so the replay isolates the stage (projection, hypotheses,
roles, composition, bridges). The printed comparison lists every hypothesis
whose consensus optical membership or selected index differs from the saved
stage report. This is a regression instrument, never an acceptance.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

from reconstruction.photo_apertures import OfflineLensApertureEngine
from reconstruction.physical_group_stage import run_physical_group_stage


def _read(path):
    return json.loads(Path(path).read_text('utf-8'))


def _consensus(report):
    return {h['index']: sorted(tuple(g['members']) for g in h['consensus_optical_groups']) for h in report['hypotheses']}


def replay(job: Path, output: Path, *, aperture_weights: Path, maximum_bridges=8) -> dict:
    job = Path(job).resolve()
    journal = _read(job / 'job.json')
    stages = journal['stages']
    input_folder = job / stages['input'][-1]['directory']
    refinement_folder = job / stages['refinement'][-1]['directory']
    saved_folder = job / stages['physical_groups'][-1]['directory']
    bundle = _read(input_folder / 'manifest.json')
    refinement = _read(refinement_folder / 'report.json')
    report = _read(job / 'report.json')
    model = job / report['candidate']['source_artifact']
    photos = [{'id': item['id'], 'view': item['view'], 'path': str(input_folder / item['normalized']['path']),
               'sha256': item['normalized']['sha256']} for item in bundle['photos'] if not item['normalized'].get('has_transparency')]
    engine = OfflineLensApertureEngine(aperture_weights)
    started = time.monotonic()
    fresh = run_physical_group_stage(model, refinement, photos, Path(output), aperture_engine=engine, maximum_bridges=maximum_bridges)
    saved = _read(saved_folder / 'report.json')
    before, after = _consensus(saved), _consensus(fresh)
    rows, differences = [], 0
    for index in sorted(set(before) | set(after)):
        same = before.get(index) == after.get(index)
        differences += not same
        rows.append({'hypothesis': index, 'same': same, 'before': before.get(index), 'after': after.get(index)})
    selected_before = (saved.get('selected_hypothesis') or {}).get('index')
    selected_after = (fresh.get('selected_hypothesis') or {}).get('index')
    summary = {'job': str(job), 'seconds': time.monotonic() - started, 'hypotheses': rows, 'differences': differences,
               'selected_before': selected_before, 'selected_after': selected_after,
               'selected_same_membership': before.get(selected_before) == after.get(selected_after),
               'bridges_before': saved.get('bridges_executed'), 'bridges_after': fresh.get('bridges_executed'),
               'accepted': False, 'quality_verdict': 'unmeasured'}
    (Path(output) / 'comparison.json').write_text(json.dumps(summary, indent=2, allow_nan=False), encoding='utf-8')
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('job', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--aperture-weights', type=Path, default=Path('data/models/glasses-detector-v1'))
    args = parser.parse_args(argv)
    summary = replay(args.job, args.output, aperture_weights=args.aperture_weights)
    for row in summary['hypotheses']:
        print(f"h{row['hypothesis']}: {'same' if row['same'] else 'DIFFERENT'}  before={row['before']}  after={row['after']}")
    print(json.dumps({k: summary[k] for k in ('seconds', 'differences', 'selected_before', 'selected_after', 'selected_same_membership', 'bridges_before', 'bridges_after')}))


if __name__ == '__main__':
    main()
