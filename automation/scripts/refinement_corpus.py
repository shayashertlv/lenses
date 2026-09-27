"""Run one refinement policy on every case in a local, private corpus manifest.

Usage from automation/: python scripts/refinement_corpus.py --manifest path.json
    --output data/refinement/experiment --resolution 320 --camera-evaluations 160
Manifest: {"schema_version":1,"cases":[{"id":"case-name","model":"path.glb",
           "photos":{"front":"front.jpg","angled":"angled.jpg"}}]}
Paths resolve relative to the manifest. This evaluates a refinement stage;
exporting a proposal is never counted as accepted reconstruction quality.
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
from reconstruction.refine_photos import run, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--resolution', type=int, default=320)
    parser.add_argument('--camera-evaluations', type=int, default=160)
    args = parser.parse_args()
    raw = args.manifest.read_bytes()
    manifest = json.loads(raw)
    if manifest.get('schema_version') != 1 or not isinstance(manifest.get('cases'), list) or not manifest['cases']:
        parser.error('Expected a nonempty version-1 corpus manifest')
    names = [case.get('id', '') for case in manifest['cases']]
    if len(set(names)) != len(names) or any(not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', name) for name in names):
        parser.error('Case ids must be unique, simple directory names')
    if args.output.exists() and any(args.output.iterdir()):
        parser.error('Output must be empty so prior evidence is preserved')
    args.output.mkdir(parents=True, exist_ok=True)
    aggregate = {'schema_version': 1, 'manifest_sha256': hashlib.sha256(raw).hexdigest(),
                 'quality_verdict': 'unmeasured', 'scope': 'development corpus; existing GLB refinement only',
                 'settings': {'resolution': args.resolution, 'camera_evaluations': args.camera_evaluations}, 'cases': []}
    failed = False
    for case in manifest['cases']:
        started = time.monotonic()
        print(f"Case {case['id']}", flush=True)
        try:
            report = run(args.manifest.parent / case['model'],
                         [(view, args.manifest.parent / path) for view, path in case['photos'].items()],
                         args.output / case['id'], resolution=args.resolution, camera_evaluations=args.camera_evaluations)
            row = {'id': case['id'], 'status': report['status'], 'quality_verdict': report['quality_verdict'],
                   'source_sha256': report['source_sha256'],
                   'export_scale': report.get('export_backtracking', {}).get('selected_scale'),
                   'rerender_nonregression': report.get('rerender_nonregression'),
                   'views': [{'id': view['view_id'], 'selected_edges': view.get('selected_edge_count'),
                              'camera_hypothesis': view.get('camera_hypothesis'), 'rerender': view.get('rerender')}
                             for view in report['views']]}
        except Exception as error:
            failed = True
            row = {'id': case['id'], 'status': 'execution_failed', 'quality_verdict': 'unmeasured',
                   'error': f'{type(error).__name__}: {error}'}
        row['seconds'] = time.monotonic() - started
        aggregate['cases'].append(row)
        write_json(args.output / 'summary.json', aggregate)
        print(json.dumps({'id': row['id'], 'status': row['status'], 'seconds': round(row['seconds'], 1)}), flush=True)
    return int(failed)


if __name__ == '__main__':
    raise SystemExit(main())
