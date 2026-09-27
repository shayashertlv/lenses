"""Fixed-policy optical preparation and photo-material corpus experiment.

Uses existing saved region hypotheses; never invokes SAM, a provider, or a paid
API. All five material families, mask alternatives and three optimizer starts
are retained under one explicit 7,290-run budget per source material group.
The 256-sample capacity is identical for every mask. This is a bounded offline
experiment, not a completed-generation or appearance-acceptance benchmark.

Prepare after the source is frozen, then execute or resume:
  python qa/optical_corpus.py --output data/optical-corpus/fixed-policy-v1 --prepare-only
  python qa/optical_corpus.py --output data/optical-corpus/fixed-policy-v1 --resume

Completed preparation/fit attempts are immutable. Failed or interrupted fit
attempts remain on disk; resume reuses preparation and creates a new fit attempt.
There is no within-optimizer checkpoint recovery or silent hypothesis pruning.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from reconstruction.job import _inside, _job_lock, _read, _write
from reconstruction.photo_lens_fit import PhotoLensFitPolicy
from reconstruction.photo_lens_stage import run_photo_lens_stage
from reconstruction.prepare_optics import run_optical_preparation
from reconstruction.refine_photos import implementation_manifest

EXPECTED = ('rayban', 'miumiu', 'oakley', 'invu', 'victoria-beckham')
MAXIMUM_OPTIMIZATION_RUNS = 7290
MAXIMUM_SAMPLES = 256


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while block := stream.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _json(value):
    return json.loads(json.dumps(value, allow_nan=False))


def implementation():
    return {**implementation_manifest(), 'corpus_runner_sha256': sha(Path(__file__))}


def inventory(folder):
    folder = Path(folder).resolve()
    result = {}
    for path in sorted(folder.rglob('*')):
        if not path.resolve().is_relative_to(folder):
            raise ValueError('Artifact link escapes its attempt directory')
        if path.is_file():
            result[path.relative_to(folder).as_posix()] = sha(path)
    return result


def _verify_pins(pins):
    for path, expected in pins.items():
        if not Path(path).is_file() or sha(path) != expected:
            raise ValueError(f'Pinned input changed: {path}')


def _verify_case(case):
    _verify_pins(case['input_sha256'])
    if inventory(case['region_attempt']) != case['region_artifacts']:
        raise ValueError(f'Saved region attempt artifacts changed: {case["id"]}')


def _unaccepted(report):
    if report.get('quality_verdict') != 'unmeasured' or report.get('accepted') is not False:
        raise ValueError('Optical experiment cannot establish accepted appearance')
    if report.get('selected_material') is not None:
        raise ValueError('Optical experiment cannot select an accepted material')


def prepare(manifest: Path, regions_root: Path, output: Path, *, selected=(), resume=False):
    """Pin exact archived inputs, saved region inventories, code and fixed policy.

    Reading old region evidence intentionally does not require its implementation
    to equal today's optical fitter. Its original receipt and all artifact bytes
    are pinned separately; masks and source-part semantics remain hypotheses.
    """
    manifest, regions_root, output = (Path(p).resolve() for p in (manifest, regions_root, output))
    if output.is_relative_to(regions_root) or regions_root.is_relative_to(output):
        raise ValueError('Optical output must be outside the saved region corpus')
    raw = manifest.read_bytes()
    source = _read(manifest)
    cases = source.get('cases', [])
    if source.get('schema_version') != 1 or sorted(c.get('id', '') for c in cases) != sorted(EXPECTED):
        raise ValueError('Expected exactly the five archived design IDs')
    if len(set(selected)) != len(selected) or set(selected) - set(EXPECTED):
        raise ValueError('Select unique known design IDs')
    aggregate_path = regions_root / 'report.json'
    region_receipt_path = regions_root / 'corpus-receipt.json'
    archived_manifest = regions_root / 'source-manifest.json'
    aggregate, region_receipt = _read(aggregate_path), _read(region_receipt_path)
    _unaccepted(aggregate)
    if (aggregate.get('corpus_receipt_sha256') != sha(region_receipt_path)
            or region_receipt.get('manifest', {}).get('sha256') != sha(manifest)
            or archived_manifest.read_bytes() != raw):
        raise ValueError('Saved region corpus manifest/receipt lineage differs')
    records = aggregate.get('cases', [])
    saved = {row['id']: row for row in records}
    if len(saved) != len(records):
        raise ValueError('Duplicate saved corpus case')
    rows = []
    for case in cases:
        if selected and case['id'] not in selected:
            continue
        name = case['id']
        prior = saved.get(name)
        if not prior or prior.get('execution_complete') is not True:
            raise ValueError(f'Saved region case is incomplete: {name}')
        if not re.fullmatch(rf'cases/{re.escape(name)}/attempt-[0-9]{{3,}}', prior.get('attempt', '')):
            raise ValueError('Saved region attempt path differs')
        attempt = _inside(regions_root, prior['attempt'])
        artifacts = inventory(attempt)
        if artifacts != prior.get('artifacts') or not artifacts:
            raise ValueError(f'Saved region attempt artifacts changed: {name}')
        region_path = attempt / 'report.json'
        region = _read(region_path)
        _unaccepted(region)
        model = (manifest.parent / case['model']).resolve()
        model_sha = sha(model)
        if region.get('candidate_sha256') != model_sha:
            raise ValueError(f'Region camera/model lineage differs: {name}')
        photos = {p['id']: p for p in region.get('photos', [])}
        if len(photos) != len(region.get('photos', [])) or set(photos) != set(case['photos']):
            raise ValueError(f'Region photo set differs: {name}')
        pins = {str(model): model_sha, str(region_path): sha(region_path)}
        for view, relative in case['photos'].items():
            photo = (manifest.parent / relative).resolve()
            source_photo = Path(photos[view]['source']).resolve()
            photo_sha = sha(photo)
            if photos[view].get('source_sha256') != photo_sha or sha(source_photo) != photo_sha:
                raise ValueError(f'Region photo hash differs: {name}/{view}')
            pins[str(photo)] = photo_sha
            pins[str(source_photo)] = photo_sha
        rows.append({'id': name, 'model': str(model), 'model_sha256': model_sha,
            'region_report': str(region_path), 'region_attempt': str(attempt),
            'region_artifacts': artifacts, 'input_sha256': pins})
    receipt = {'schema_version': 1, 'method': 'fixed_policy_photo_optics_corpus_v1',
        'scope': 'Archived candidate geometry plus frozen photo-region alternatives; conditional material candidates only.',
        'quality_verdict': 'unmeasured', 'accepted': False, 'selected_material': None,
        'input_sha256': {str(p): sha(p) for p in (manifest, aggregate_path, region_receipt_path, archived_manifest)},
        'implementation': implementation(), 'policy': _json(asdict(PhotoLensFitPolicy(maximum_optimization_runs=MAXIMUM_OPTIMIZATION_RUNS))),
        'maximum_samples_per_hypothesis': MAXIMUM_SAMPLES, 'cases': rows,
        'limitations': ['Source optical-part identities, photos, fitted cameras and mask alternatives are not independently verified.',
            'One fixed policy is used for all products; no product-specific material, mask or geometry tuning.',
            '7,290 is an explicit per-group optimizer-run ceiling, not an assurance of convergence.',
            'Prepared or preview assets are diagnostic artifacts, never an accepted final reconstruction.',
            'Interrupted fitting restarts in a new attempt; completed preparation is reused.']}
    with _job_lock(output):
        existing = [p for p in output.iterdir() if p.name != '.lock']
        if existing:
            if not resume or not (output / 'corpus-receipt.json').is_file() or _read(output / 'corpus-receipt.json') != receipt:
                raise ValueError('Output is not empty or its pinned inputs, policy or implementation changed; use a new directory')
            if (output / 'source-manifest.json').read_bytes() != raw:
                raise ValueError('Optical corpus source manifest changed')
        else:
            (output / 'source-manifest.json').write_bytes(raw)
            _write(output / 'corpus-receipt.json', receipt)
    return receipt


def _stage(output, journal, row, name, invoke, validate):
    history = row.setdefault('stages', {}).setdefault(name, [])
    for old in history:
        if old.get('status') == 'complete':
            if not re.fullmatch(rf'cases/{re.escape(row["id"])}/{name}/attempt-[0-9]{{3,}}', old.get('directory', '')):
                raise ValueError('Completed optical attempt path changed')
            folder = _inside(output, old['directory'])
            if inventory(folder) != old.get('artifacts'):
                raise ValueError('Completed optical attempt artifacts changed')
            result = _read(folder / 'report.json')
            _unaccepted(result)
            validate(result, folder)
            return result, folder
    parent = output / 'cases' / row['id'] / name
    number = 1
    while (parent / f'attempt-{number:03d}').exists():
        number += 1
    folder = parent / f'attempt-{number:03d}'
    record = {'status': 'running', 'directory': folder.relative_to(output).as_posix(),
              'started_at': datetime.now(timezone.utc).isoformat()}
    history.append(record)
    _write(output / 'report.json', journal)
    started = time.monotonic()
    try:
        result = invoke(folder)
        if _json(result) != _read(folder / 'report.json'):
            raise ValueError('Returned stage report differs from saved artifact')
        _unaccepted(result)
        validate(result, folder)
        record.update(status='complete', artifacts=inventory(folder))
    except Exception as error:
        record.update(status='failed', error=f'{type(error).__name__}: {error}',
                      artifacts=inventory(folder) if folder.exists() else {})
        raise
    finally:
        record['elapsed_seconds'] = time.monotonic()-started
        _write(output / 'report.json', journal)
    return result, folder


def _preview_links(fitted, folder, output, source_sha):
    previews = []
    for row in fitted.get('previews', []):
        if row.get('status') != 'diagnostic_preview_exported':
            previews.append(row)
            continue
        model, export = _inside(folder, row['path']), _inside(folder, row['export']['path'])
        if sha(model) != row['sha256'] or sha(export) != row['export']['sha256']:
            raise ValueError('Diagnostic preview or export hash differs')
        binding = _read(export)
        if binding.get('source_sha256') != source_sha or binding.get('output_sha256') != row['sha256']:
            raise ValueError('Diagnostic preview export lineage differs')
        previews.append({**row, 'path': model.relative_to(output).as_posix(),
                         'export': {**row['export'], 'path': export.relative_to(output).as_posix()}})
    return previews


def _group_links(fitted, folder, output):
    groups = []
    for row in fitted.get('groups', []):
        if 'report' not in row:
            groups.append(row)
            continue
        path = _inside(folder, row['report']['path'])
        if sha(path) != row['report']['sha256']:
            raise ValueError('Material-group fit report hash differs')
        groups.append({**row, 'report': {**row['report'], 'path': path.relative_to(output).as_posix()}})
    return groups


def execute(receipt, output: Path):
    """Execute or verify/resume fixed cases; failures preserve all attempt files."""
    output = Path(output).resolve()
    with _job_lock(output):
        if _read(output / 'corpus-receipt.json') != receipt or implementation() != receipt['implementation']:
            raise ValueError('Optical receipt or implementation changed after preparation')
        _verify_pins(receipt['input_sha256'])
        for case in receipt['cases']:
            _verify_case(case)
        report_path = output / 'report.json'
        if report_path.exists():
            report = _read(report_path)
            _unaccepted(report)
            if report.get('corpus_receipt_sha256') != sha(output / 'corpus-receipt.json'):
                raise ValueError('Optical aggregate receipt mismatch')
        else:
            report = {'schema_version': 1, 'status': 'running', 'quality_verdict': 'unmeasured',
                'accepted': False, 'selected_material': None, 'full_generation_complete': False,
                'corpus_receipt_sha256': sha(output / 'corpus-receipt.json'), 'cases': []}
        by_id = {row['id']: row for row in report['cases']}
        if len(by_id) != len(report['cases']) or set(by_id) - {c['id'] for c in receipt['cases']}:
            raise ValueError('Optical aggregate case identities differ')
        policy = PhotoLensFitPolicy(**{'rear_content': 'explained', 'maximum_validation_share': 1.0, 'gradient_density_keyframes': 3, **receipt['policy']})
        report.update(status='running', execution_complete=False)
        for case in receipt['cases']:
            row = by_id.get(case['id'])
            if row is None:
                row = {'id': case['id'], 'stages': {}}
                report['cases'].append(row)
            row.update(execution_complete=False)
            for field in ('error', 'preparation_status', 'preparation_report', 'fit_status', 'fit_report', 'groups', 'previews'):
                row.pop(field, None)
            _verify_case(case)
            print(f'Optical corpus: {case["id"]}', flush=True)
            def validate_preparation(value, folder):
                if value.get('source_sha256') != case['model_sha256']:
                    raise ValueError('Preparation source lineage differs')
                _verify_case(case)
            try:
                prepared, preparation_folder = _stage(output, report, row, 'preparation',
                    lambda folder: run_optical_preparation(Path(case['model']), folder), validate_preparation)
                row.update(preparation_status=prepared['status'],
                    preparation_report={'path': (preparation_folder / 'report.json').relative_to(output).as_posix(),
                                        'sha256': sha(preparation_folder / 'report.json')},
                    fit_status='not_prepared', previews=[])
                if prepared['status'] == 'prepared_optical_candidate':
                    prep_path, region_path = preparation_folder / 'report.json', Path(case['region_report'])
                    def validate_fit(value, folder):
                        if (value.get('input_sha256', {}).get(str(prep_path)) != sha(prep_path)
                                or value.get('input_sha256', {}).get(str(region_path)) != sha(region_path)):
                            raise ValueError('Photo fit preparation/region lineage differs')
                        if _json(value.get('policy')) != receipt['policy'] or value.get('maximum_samples_per_hypothesis') != receipt['maximum_samples_per_hypothesis']:
                            raise ValueError('Photo fit fixed policy differs')
                        _preview_links(value, folder, output, case['model_sha256'])
                        _group_links(value, folder, output)
                        _verify_case(case)
                    fitted, fit_folder = _stage(output, report, row, 'fit', lambda folder: run_photo_lens_stage(
                        prep_path, region_path, folder, policy=policy,
                        maximum_samples_per_hypothesis=receipt['maximum_samples_per_hypothesis']), validate_fit)
                    row.update(fit_status=fitted['status'],
                        fit_report={'path': (fit_folder / 'report.json').relative_to(output).as_posix(),
                                    'sha256': sha(fit_folder / 'report.json')},
                        groups=_group_links(fitted, fit_folder, output),
                        previews=_preview_links(fitted, fit_folder, output, case['model_sha256']))
                row['execution_complete'] = True
            except Exception as error:
                row.update(error=f'{type(error).__name__}: {error}')
            _write(report_path, report)
        _verify_pins(receipt['input_sha256'])
        for case in receipt['cases']:
            _verify_case(case)
        if implementation() != receipt['implementation']:
            raise ValueError('Implementation changed during optical corpus execution')
        report['execution_complete'] = all(row['execution_complete'] for row in report['cases'])
        report['full_corpus_experiment_complete'] = report['execution_complete'] and set(c['id'] for c in receipt['cases']) == set(EXPECTED)
        report['cases_with_diagnostic_previews'] = sum(any(p.get('status') == 'diagnostic_preview_exported' for p in row.get('previews', [])) for row in report['cases'])
        report['status'] = 'experiment_completed' if report['execution_complete'] else 'incomplete_or_failed'
        _write(report_path, report)
        return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, default=ROOT / 'data/refinement-corpus.json')
    parser.add_argument('--regions', type=Path, default=ROOT / 'data/region-corpus/fixed-policy-v1')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--case', action='append', default=[])
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args(argv)
    receipt = prepare(args.manifest, args.regions, args.output, selected=args.case, resume=args.resume)
    if args.prepare_only:
        print(json.dumps({'status': 'prepared', 'cases': len(receipt['cases']), 'quality_verdict': 'unmeasured',
                          'maximum_optimization_runs': MAXIMUM_OPTIMIZATION_RUNS, 'accepted': False}))
        return 0
    report = execute(receipt, args.output)
    print(json.dumps({key: report[key] for key in ('status', 'execution_complete', 'full_corpus_experiment_complete',
                                                  'cases_with_diagnostic_previews', 'quality_verdict', 'accepted')}))
    return 0 if report['execution_complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
