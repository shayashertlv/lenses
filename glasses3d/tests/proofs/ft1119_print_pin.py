"""Replay the owner-accepted FT1119 recipe on explicit copied legacy inputs; no paid calls.

Run once after print sources are frozen: python -m glasses3d.tests.proofs.ft1119_print_pin --out NEW_DIR --workers 12
The expected PNG hash is fixed by WP5_HANDOFF. No numerical gate, threshold, seed or prior evidence is changed.
"""
import argparse
import json
from pathlib import Path
import time

import numpy as np

from glasses3d.g3d_host.photoprint import inputs as IN, production as P
from glasses3d.g3d_host.photoprint.report import jsonable
from glasses3d.tests.fixtures import legacy_ft1119 as FT
from glasses3d.tests.photoprint import printproof as PP


def run(out: Path, workers=12):
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    fixture = FT.prepare(out / 'inputs')
    job = IN.load_job(fixture['cameras'])
    calibration = FT.REPO / 'glasses3d/data/wp5/ar_calibration/calibration.json'
    accepted = FT.REPO / 'glasses3d/data/wp5/quilt_joins_20261008/slope/bake'
    if FT.sha(accepted / 'print.png') != FT.APPROVED_PRINT_SHA:
        raise ValueError('owner-accepted reference PNG changed')
    sources = [calibration, accepted / 'print.png', FT.CAMERAS, FT.STEP0,
               Path(fixture['receipt']['model']['source'])]
    sources.extend(Path(row['path']) for row in job.sources['images'].values())
    sources.extend(sorted((FT.REPO / 'glasses3d/g3d_host/photoprint').glob('*.py')))
    sources.extend(sorted((FT.REPO / 'glasses3d/g3d/printuv').glob('*.py')))
    sources.append(FT.REPO / 'glasses3d/g3d_host/fitmesh.py')
    before = {str(path): FT.sha(path) for path in sources}
    t0 = time.perf_counter()
    result = P.bake_print(job, IN.FT1119_PARTS, out_dir=out / 'bake', thresholds=P.THRESHOLDS,
                          calibration=calibration, workers=workers, step0_report=fixture['step0'],
                          compare_fill=('patchmatch',))
    audit = PP.audit_outputs(job, result, out / 'bake')
    actual = FT.sha(out / 'bake/print.png')
    after = {str(path): FT.sha(path) for path in sources}
    record = {'schema': 'g3d.accepted_print_pin/1', 'paid_calls': 0, 'seconds': time.perf_counter() - t0,
              'workers': workers, 'thresholds': dict(P.THRESHOLDS), 'source_hashes': before,
              'source_hashes_unchanged': before == after, 'migration': fixture['receipt'],
              'expected_print_sha256': FT.APPROVED_PRINT_SHA, 'print_sha256': actual,
              'print_byte_identical_to_approved': actual == FT.APPROVED_PRINT_SHA, 'audit': audit,
              'timings': result['report']['timings'],
              'unchanged_outputs': {name: FT.sha(out / 'bake' / name) == FT.sha(accepted / name)
                                    for name in ('classes.png', 'source.png', 'tier.png', 'layout.json',
                                                 'registration.json')},
              'independent_quality_proof': 'not_run', 'owner_completed_wp5': True}
    if (accepted / 'gain_field.npz').exists():
        old, new = np.load(accepted / 'gain_field.npz'), np.load(out / 'bake/gain_field.npz')
        record['gain_field_identical'] = old.files == new.files and all(np.array_equal(old[k], new[k]) for k in old.files)
        old.close()
        new.close()
    record['pass'] = bool(record['print_byte_identical_to_approved'] and audit['pass'] and before == after)
    FT.write(out / 'result.json', jsonable(record))
    if not record['pass']:
        raise AssertionError(f"accepted print pin failed: see {out / 'result.json'}")
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=12)
    args = parser.parse_args(argv)
    if args.workers < 1:
        parser.error('--workers must be positive')
    record = run(args.out, args.workers)
    print(json.dumps({key: record[key] for key in ('pass', 'paid_calls', 'seconds', 'print_sha256')}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
