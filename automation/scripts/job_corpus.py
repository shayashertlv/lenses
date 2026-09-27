"""Prepare and exercise the same local job policy on a private saved corpus.

This is an integration instrument, not a reconstruction acceptance benchmark.
The existing corpus format is {schema_version:1,cases:[{id,model,photos:{view:path},
dimensions_mm?:{...}}]}; paths resolve relative to the corpus manifest. Optional
--archive-dimensions explicitly imports recorded dimensions from the source
model's enclosing job.json and records that provenance outside the job request.

Example: python scripts/job_corpus.py --manifest data/refinement-corpus.json
  --output data/job-corpus/run1 --archive-dimensions --case rayban --prepare-only
Remove --prepare-only and add --resume to execute those exact prepared requests.
No provider initializer is selected by this script.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from reconstruction.job import _dimension_application  # noqa: E402

DIMENSIONS = frozenset({'frame_width', 'lens_width', 'lens_height', 'bridge_width', 'temple_length'})


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def encoded(value) -> bytes:
    return (json.dumps(value, indent=2, allow_nan=False) + '\n').encode('utf-8')


def write_atomic(path: Path, value) -> None:
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_bytes(encoded(value))
    temporary.replace(path)


def _existing_file(base: Path, value, field: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f'{field} must be a nonempty path string')
    path = (base / value).resolve()
    if not path.is_file():
        raise ValueError(f'{field} is not an existing file: {path}')
    return path


def _recorded_dimensions(model: Path) -> tuple[dict, dict]:
    for folder in model.parents:
        job_path = folder / 'job.json'
        if job_path.is_file():
            job = json.loads(job_path.read_bytes())
            dimensions = job.get('dimensions', {})
            if not isinstance(dimensions, dict) or set(dimensions) - DIMENSIONS:
                raise ValueError(f'Unsupported archived dimensions in {job_path}')
            return dimensions, {'path': str(job_path), 'sha256': digest(job_path),
                                'field': 'dimensions', 'interpretation': 'recorded millimeters; unverified and unapplied'}
    raise ValueError(f'No enclosing archived job.json for {model}')


def prepare(manifest_path: Path, output: Path, *, selected=(), archive_dimensions=False,
            resolution=320, camera_evaluations=160, resume=False) -> dict:
    """Validate all selected paths before writes; prepare requests without running."""
    manifest_path = manifest_path.resolve()
    raw = manifest_path.read_bytes()
    manifest = json.loads(raw)
    cases = manifest.get('cases')
    if manifest.get('schema_version') != 1 or not isinstance(cases, list) or not cases:
        raise ValueError('Expected a nonempty version-1 corpus manifest')
    names = [case.get('id') if isinstance(case, dict) else None for case in cases]
    if any(not isinstance(name, str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', name) for name in names):
        raise ValueError('Case ids must be simple lowercase directory names')
    if len(set(names)) != len(names):
        raise ValueError('Case ids must be unique')
    if len(set(selected)) != len(selected) or set(selected) - set(names):
        raise ValueError('--case values must be unique known corpus ids')
    if not 128 <= resolution <= 768 or not 20 <= camera_evaluations <= 1000:
        raise ValueError('Use resolution 128–768 and camera evaluations 20–1000')
    rows = []
    for case in cases:
        if selected and case['id'] not in selected:
            continue
        model = _existing_file(manifest_path.parent, case.get('model'), 'model')
        photos = case.get('photos')
        if not isinstance(photos, dict) or not photos:
            raise ValueError('Saved corpus photos must map labels to paths')
        photo_rows, photo_records = [], []
        for index, (view, value) in enumerate(photos.items()):
            if view not in ('front', 'back', 'left', 'right', 'angled', 'unknown'):
                raise ValueError(f'Unsupported corpus view label {view!r}')
            path = _existing_file(manifest_path.parent, value, 'photo')
            photo_id = f'photo-{index + 1:02d}'
            photo_rows.append({'id': photo_id, 'view': view, 'path': str(path)})
            photo_records.append({'id': photo_id, 'view': view, 'path': str(path), 'sha256': digest(path)})
        dimensions = case.get('dimensions_mm', {})
        dimension_source = {'path': str(manifest_path), 'sha256': hashlib.sha256(raw).hexdigest(),
                            'field': f'cases[{case["id"]}].dimensions_mm'} if dimensions else None
        if archive_dimensions:
            if dimensions:
                raise ValueError('Do not combine explicit corpus dimensions and --archive-dimensions')
            dimensions, dimension_source = _recorded_dimensions(model)
        if not isinstance(dimensions, dict) or set(dimensions) - DIMENSIONS:
            raise ValueError('Unsupported dimension fields')
        model_hash = digest(model)
        request = {'schema_version': 1, 'photos': photo_rows,
                   'initializer': {'kind': 'existing_glb', 'path': str(model), 'sha256': model_hash}}
        if dimensions:
            request['dimensions_mm'] = dimensions
        rows.append({'id': case['id'], 'request': request, 'request_sha256': hashlib.sha256(encoded(request)).hexdigest(),
                     'sources': {'model': {'path': str(model), 'sha256': model_hash}, 'photos': photo_records},
                     'dimensions_source': dimension_source,
                     'dimension_use': _dimension_application(dimensions)})
    receipt = {'schema_version': 1, 'manifest_sha256': hashlib.sha256(raw).hexdigest(),
               'scope': 'saved existing-GLB integration corpus; not unseen-product or photo-only generation evidence',
               'quality_verdict': 'unmeasured', 'archive_dimensions': archive_dimensions,
               'settings': {'resolution': resolution, 'camera_evaluations': camera_evaluations}, 'cases': rows}
    if output.exists() and any(output.iterdir()):
        if not resume:
            raise ValueError('Output must be empty; use --resume to verify the same prepared corpus')
        if not (output / 'corpus-receipt.json').is_file() or (output / 'corpus-receipt.json').read_bytes() != encoded(receipt):
            raise ValueError('Corpus input, settings or receipt changed; preserve this output and use a new directory')
        if not (output / 'source-manifest.json').is_file() or (output / 'source-manifest.json').read_bytes() != raw:
            raise ValueError('Archived corpus manifest changed; refusing to replace it')
        for row in rows:
            request_path = output / 'requests' / f'{row["id"]}.json'
            if not request_path.is_file() or digest(request_path) != row['request_sha256']:
                raise ValueError('Prepared request changed; refusing to replace it')
        return receipt
    output.mkdir(parents=True, exist_ok=True)
    (output / 'requests').mkdir()
    (output / 'source-manifest.json').write_bytes(raw)
    for row in rows:
        write_atomic(output / 'requests' / f'{row["id"]}.json', row['request'])
    write_atomic(output / 'corpus-receipt.json', receipt)
    return receipt


def execute(receipt: dict, output: Path) -> dict:
    # Lazy import keeps preparation independent from orchestration availability.
    from reconstruction.job import run_job

    aggregate = {'schema_version': 1, 'scope': receipt['scope'], 'quality_verdict': 'unmeasured',
                 'corpus_receipt_sha256': digest(output / 'corpus-receipt.json'),
                 'manifest_sha256': receipt['manifest_sha256'], 'settings': receipt['settings'], 'cases': []}
    for case in receipt['cases']:
        started = time.monotonic()
        job_output = output / 'jobs' / case['id']
        print(f'Job {case["id"]}', flush=True)
        row = {'id': case['id'], 'request_sha256': case['request_sha256'],
               'dimension_use': case['dimension_use'], 'dimensions_source': case['dimensions_source']}
        try:
            for source in [case['sources']['model'], *case['sources']['photos']]:
                if digest(Path(source['path'])) != source['sha256']:
                    raise ValueError('Corpus source changed after preparation')
            result = run_job(output / 'requests' / f'{case["id"]}.json', job_output, **receipt['settings'])
            if (result.get('quality_verdict') != 'unmeasured' or result.get('status') == 'accepted'
                    or result.get('quality', {}).get('accepted') is not False):
                raise ValueError('Current corpus stage cannot claim measured or accepted reconstruction quality')
            dimension_gate = next((gate for gate in result.get('quality', {}).get('gates', [])
                                   if gate.get('id') == 'physical_dimensions'), {})
            if dimension_gate.get('application') != case['dimension_use']:
                raise ValueError('Reported dimension application differs from the prepared corpus contract')
            row.update(status=result.get('status'), quality_verdict=result['quality_verdict'],
                       quality_accepted=False, input_sha256=result.get('input_sha256'),
                       refinement_status=result.get('refinement_status'), candidate=result.get('candidate'),
                       reported_dimension_use=dimension_gate['application'],
                       report_sha256=digest(job_output / 'report.json'),
                       job_journal_sha256=digest(job_output / 'job.json'),
                       receipts=[{'path': str(path.relative_to(job_output)), 'sha256': digest(path)}
                                 for path in sorted(job_output.rglob('*receipt*.json'))])
        except Exception as error:
            row.update(status='execution_failed', quality_verdict='unmeasured', error=f'{type(error).__name__}: {error}')
        row['seconds'] = time.monotonic() - started
        aggregate['cases'].append(row)
        aggregate['complete'] = len(aggregate['cases']) == len(receipt['cases'])
        aggregate['execution_failures'] = sum(item['status'] == 'execution_failed' for item in aggregate['cases'])
        write_atomic(output / 'summary.json', aggregate)
        print(json.dumps({'id': row['id'], 'status': row['status'], 'seconds': round(row['seconds'], 2)}), flush=True)
    return aggregate


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--case', action='append', default=[])
    parser.add_argument('--archive-dimensions', action='store_true')
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--resolution', type=int, default=320)
    parser.add_argument('--camera-evaluations', type=int, default=160)
    args = parser.parse_args()
    try:
        receipt = prepare(args.manifest, args.output, selected=args.case, archive_dimensions=args.archive_dimensions,
                          resolution=args.resolution, camera_evaluations=args.camera_evaluations, resume=args.resume)
    except (ValueError, OSError, json.JSONDecodeError) as error:
        parser.error(str(error))
    if args.prepare_only:
        print(json.dumps({'status': 'prepared', 'cases': [case['id'] for case in receipt['cases']],
                          'quality_verdict': 'unmeasured'}))
        return 0
    return int(execute(receipt, args.output)['execution_failures'] > 0)


if __name__ == '__main__':
    raise SystemExit(main())
