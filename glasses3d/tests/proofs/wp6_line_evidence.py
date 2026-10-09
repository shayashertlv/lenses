"""Offline line-instrument calibration, not a WP6 acceptance proof.

No product, camera fit, S1 perturbation or frozen gate is changed. Every declared
case, including missing and ambiguous evidence, stays in the report. Use a new
output directory and the logged runner described in WP6_HANDOFF.md.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from glasses3d.g3d_host import relief_cue as CU
from glasses3d.g3d_host import relief_trace as RT
from glasses3d.g3d_host.photoprint.procs import pool_map
from glasses3d.tests.fixtures import line_evidence as FX


def measure_case(case, masked=False):
    lightness, guide, exact, valid = FX.make_case(**case)
    line = RT.refine_line(lightness, *guide, valid_mask=valid if masked else None)
    row = {'case': case, 'line': line}
    if line is not None:
        row['endpoint_error_px'] = FX.endpoint_error(line, exact).tolist()
        row['guide_correction_px'] = list(CU.endpoint_sigmas(
            {'02': {'edge': guide}}, {'02': {'edge': [line['p0'], line['p1']]}}).values())
    return row


def summarize(rows):
    result = {}
    for kind in FX.KINDS:
        group = [r for r in rows if r['case']['kind'] == kind]
        measured = [r for r in group if r['line'] is not None]
        errors = [max(r['endpoint_error_px']) for r in measured]
        result[kind] = {
            'cases': len(group), 'measured': len(measured), 'missing': len(group)-len(measured),
            'max_endpoint_error_px': max(errors) if errors else None,
            'median_endpoint_error_px': float(np.median(errors)) if errors else None,
            'wrong_by_over_1_px': sum(e > 1. for e in errors),
            'wrong_with_rms_under_025_px': sum(max(r['endpoint_error_px']) > 1.
                                            and r['line']['rms_px'] < .25 for r in measured)}
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out-dir', type=Path, required=True)
    p.add_argument('--workers', type=int, required=True)
    p.add_argument('--masked', action='store_true', help='use the fixture known-corruption mask')
    p.add_argument('--split', choices=('calibration', 'validation'), default='calibration')
    a = p.parse_args()
    out = a.out_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    source_paths = [Path(FX.__file__), Path(RT.__file__), Path(CU.__file__), Path(__file__)]
    hashes = {str(f): hashlib.sha256(f.read_bytes()).hexdigest() for f in source_paths}
    matrix = list(FX.cases(a.split))
    # Store the complete matrix before evaluating it: no selective seed/sign reruns.
    (out/'cases.json').write_text(json.dumps(matrix, indent=2), encoding='utf-8')
    rows = pool_map(measure_case, [(c, a.masked) for c in matrix], workers=a.workers, tag='line controls')
    if hashes != {str(f): hashlib.sha256(f.read_bytes()).hexdigest() for f in source_paths}:
        raise RuntimeError('calibration source changed during the run')
    result = {'label': 'TOOL TEST, independent analytic line evidence; no acceptance decision',
              'source_sha256': hashes, 'source_unchanged': True,
              'workers': a.workers, 'known_corruption_mask': a.masked,
              'split': a.split, 'summary': summarize(rows), 'rows': rows,
              'limitations': ['Analytic contamination does not establish real-photo error.',
                              'RMS measures line scatter, not correctness of edge identity.',
                              'All legacy S1 errors, signs, cameras and gates remain unchanged.']}
    (out/'report.json').write_text(json.dumps(result, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps(result['summary'], indent=2), flush=True)


if __name__ == '__main__':
    main()
