"""Fit actual controlled Cycles measurements; this is not photo reconstruction.

Known illumination, intrinsic positions, incidence angles and density knot
positions are supplied by the experiment. Ground truth parameters are used only
after fitting to measure recovery. The angular-table and full-mirror cases test
model mismatch and unobservable parameters respectively.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from reconstruction.lens_appearance import LensAppearance
from reconstruction.lens_fit import CalibratedLensSamples, LensFitConfig, fit_lens_appearance


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixtures', type=Path, default=ROOT / 'data/lens-conformance/cases.json')
    parser.add_argument('--native', type=Path, default=ROOT / 'data/lens-conformance/blender/report.json')
    parser.add_argument('--extra-native', type=Path, help='Optional separately measured boundary calibration capture')
    parser.add_argument('--output', type=Path, default=ROOT / 'data/lens-conformance/inverse/report.json')
    args = parser.parse_args()
    fixture_bytes, native_bytes = args.fixtures.read_bytes(), args.native.read_bytes()
    fixtures, native = json.loads(fixture_bytes), json.loads(native_bytes)
    fixture_hash = hashlib.sha256(fixture_bytes).hexdigest()
    if native.get('cases_sha256') != fixture_hash or native.get('complete_conformance') is not True:
        raise ValueError('A complete native probe of the exact fixture manifest is required')
    measured_rows = list(native['results'])
    extra_hash = None
    if args.extra_native:
        extra_bytes = args.extra_native.read_bytes()
        extra = json.loads(extra_bytes)
        if extra.get('cases_sha256') != fixture_hash or extra.get('complete_capture') is not True:
            raise ValueError('Additional measurements must be a complete capture of the exact fixture manifest')
        expected = {(case['id'], v, angle, env) for case in fixtures['cases'] for v in (.025, .975)
                    for angle in (0, 15, 30, 45, 60, 75, 85)
                    for env in ('transmission_only', 'dark_mirror', 'colored_studio')}
        actual = [(row['case'], row['v'], row['angle_degrees'], row['environment']) for row in extra['results']]
        if len(actual) != len(expected) or set(actual) != expected:
            raise ValueError('Additional calibration coverage is incomplete or duplicated')
        extra_hash = hashlib.sha256(extra_bytes).hexdigest()
        measured_rows.extend(extra['results'])
    environments = {row['id']: row for row in fixtures['environments']}
    report = {
        'schema_version': 1, 'scope': 'inverse recovery from calibrated native-rendered samples',
        'fixture_sha256': fixture_hash, 'native_report_sha256': hashlib.sha256(native_bytes).hexdigest(),
        'extra_native_report_sha256': extra_hash,
        'probe_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'noise_sigma': .001, 'cases': [], 'negative_controls': [],
        'supplied_not_inferred': ['illumination', 'surface coordinates', 'incidence angles',
                                 'density knot positions', 'refractive index', 'roughness'],
        'limitations': ['No raw product photos or automatic observations are used.',
                        'Identification is conditional on known calibration and the stipulated model family.',
                        'Local uncertainty and multistart are not a proof of global uniqueness.'],
    }
    saved_samples = {}
    for case in fixtures['cases']:
        rows = [row for row in measured_rows if row['case'] == case['id']]
        if len(rows) != (189 if args.extra_native else 147):
            raise ValueError(f'{case["id"]}: incomplete native measurements')
        appearance = LensAppearance.from_dict(case['appearance'])
        samples = CalibratedLensSamples(
            v=[row['v'] for row in rows], angle_degrees=[row['angle_degrees'] for row in rows],
            observed_linear_rgb=[row['actual_linear_rgb'] for row in rows],
            background_linear_rgb=[environments[row['environment']]['background_linear_rgb'] for row in rows],
            reflected_linear_rgb=[environments[row['environment']]['reflected_linear_rgb'] for row in rows],
            noise_sigma=.001, confidence=1., calibration_id='controlled-cycles-' + fixture_hash,
            calibration_verified=True, clipped=False, source=str(args.native.resolve()),
        )
        config = LensFitConfig(knot_positions=tuple(key.v for key in appearance.optical_density_keyframes),
                               fixed_refractive_index=appearance.refractive_index, fixed_roughness=appearance.roughness)
        result = fit_lens_appearance(samples, config)
        expected = ('nonconverged' if appearance.angular_reflectance_keyframes is not None else
                    'ambiguous' if all(r == 1 for r in appearance.normal_reflectance_rgb) else 'identified')
        errors = None
        if result.best is not None:
            errors = {
                'normal_reflectance_max_absolute': float(np.max(np.abs(np.asarray(result.best.normal_reflectance_rgb) - appearance.normal_reflectance_rgb))),
                'density_max_absolute': float(np.max(np.abs(
                    np.asarray([key.optical_density_rgb for key in result.best.optical_density_keyframes]) -
                    np.asarray([key.optical_density_rgb for key in appearance.optical_density_keyframes])))),
            }
        passed = result.status == expected
        if expected == 'identified':
            passed = passed and errors is not None and errors['normal_reflectance_max_absolute'] < .005 and errors['density_max_absolute'] < .015
        report['cases'].append({'id': case['id'], 'expected_status': expected, 'passed': passed,
                                'recovery_errors': errors, 'fit': result.to_report()})
        saved_samples[case['id']] = (samples, config)
        print(json.dumps({'case': case['id'], 'status': result.status, 'expected': expected,
                          'passed': passed, 'errors': errors, 'reasons': result.reasons}), flush=True)

    samples, config = saved_samples['vertical_gradient']
    for name, changed in [('unknown calibration', replace(samples, calibration_verified=False)),
                          ('clipped observations', replace(samples, clipped=True))]:
        result = fit_lens_appearance(changed, config)
        report['negative_controls'].append({'name': name, 'expected_status': 'rejected',
                                            'passed': result.status == 'rejected', 'fit': result.to_report()})
    # Remove all heights except the center. Multiple angles/lighting cannot
    # identify two endpoint densities from this single intrinsic height.
    keep = samples.v == .5
    center_only = replace(samples, **{field: getattr(samples, field)[keep] for field in
        ('v', 'angle_degrees', 'observed_linear_rgb', 'background_linear_rgb', 'reflected_linear_rgb',
         'noise_sigma', 'confidence', 'clipped')})
    result = fit_lens_appearance(center_only, config)
    report['negative_controls'].append({'name': 'gradient heights unobserved', 'expected_status': 'ambiguous',
                                        'passed': result.status == 'ambiguous', 'fit': result.to_report()})
    report['passed'] = (len(report['cases']) == 10 and all(row['passed'] for row in report['cases'])
                        and all(row['passed'] for row in report['negative_controls']))
    report['observed_status_counts'] = dict(Counter(row['fit']['status'] for row in report['cases']))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    print(json.dumps({'passed': report['passed'], 'status_counts': report['observed_status_counts'],
                      'negative_controls': [row['passed'] for row in report['negative_controls']],
                      'report': str(args.output.resolve())}), flush=True)
    if not report['passed']:
        raise SystemExit('Inverse probe failed; inspect diagnostics without weakening expected criteria')


if __name__ == '__main__':
    main()
