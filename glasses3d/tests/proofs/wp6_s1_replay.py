"""Read-only replay of trusted local S1 caches with explicit inputs and a NEW output folder.

No fitting, guide editing, scene writes or acceptance decisions. Reproduces the
saved floor, separates camera/source/target errors, and retains every sign case
of the recorded target-endpoint error sizes. True-camera cases are oracle
diagnostics, never replacement evidence for the fitted-camera gate.

Run with --proof-dir DIR --cameras FILE --out-dir NEW_DIR. Historical proof and
camera hashes are checked before the trusted local pickle is loaded. The caller
must run with BLAS=1 and capture PID/log/exit code (WP6_HANDOFF.md).
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import itertools
import json
import pickle
from pathlib import Path

import numpy as np

from glasses3d.g3d_host import relief_fit as RF
from glasses3d.g3d_host import relief_rules as RR
from glasses3d.g3d_host import relief_views as RV
from glasses3d.tests.proofs.wp6_s1_floor_check import exact_from_measured
from glasses3d.tests.proofs.wp6_s1_theta_check import rebuild_s1
from glasses3d.tests.proofs.wp6_theta_frame import instrument_floor

G3D = Path(__file__).resolve().parents[2]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def normal(line):
    a, b = np.asarray(line, float)
    d = (b-a) / np.linalg.norm(b-a)
    return np.array([-d[1], d[0]])


def saved_lines(s1):
    steps = s1['cue']['photo_steps']
    for name in s1['lines']:
        k, cn = name.split()
        if k in ('05', '02') and (steps.get(k) or {}).get(cn) is None:
            raise ValueError(f'saved endpoint data missing for recorded crease: {name}')
    measured = {k: {cn: np.asarray([v['p0'], v['p1']], float)
                    for cn, v in steps[k].items() if v is not None} for k in ('05', '02')}
    moves = {k: {cn: s1['lines'][f'{k} {cn}']['error_added_px'] for cn in rows}
             for k, rows in measured.items()}
    exact = {k: {cn: np.asarray(exact_from_measured(line, moves[k][cn]))
                 for cn, line in rows.items()} for k, rows in measured.items()}
    # Reconstruction is only for the diagnostic exact-line case. Keep raw
    # measured lines byte-for-byte as numbers, including their signs/extent.
    for k, rows in exact.items():
        for cn, line in rows.items():
            reconstructed = line + np.asarray(moves[k][cn])[:, None] * normal(line)
            if not np.allclose(reconstructed, measured[k][cn], atol=1e-7, rtol=0):
                raise ValueError(f'could not invert recorded endpoint moves: {k} {cn}')
    return measured, exact, moves


def sign_cases(exact, measured, moves):
    """Every +/- combination on paired target endpoints; no seed or selection."""
    paired = [cn for cn in measured['05'] if cn in measured['02']]
    ends = [(cn, end) for cn in paired for end in (0, 1)]
    for signs in itertools.product((-1., 1.), repeat=len(ends)):
        target = copy.deepcopy(exact['02'])
        for (cn, end), sign in zip(ends, signs):
            target[cn][end] += sign * abs(moves['02'][cn][end]) * normal(exact['02'][cn])
        yield {f'02 {cn} end {end}': sign for (cn, end), sign in zip(ends, signs)}, {
            '05': measured['05'], '02': target}


def decompose(sj, truth, fits, measured, exact, moves):
    frames = {'fitted': fits,
              'true': {k: {'camera': sj.true_cams[k], 'theta': sj.truth_theta[k]} for k in ('05', '02')}}
    cases = {}
    for frame, source, target in itertools.product(frames, ('measured', 'exact'), ('measured', 'exact')):
        lines = {'05': (measured if source == 'measured' else exact)['05'],
                 '02': (measured if target == 'measured' else exact)['02']}
        result = RF.transfer_all(RF.Context(sj, frames[frame], {}, (), lines), truth)
        key = f'{frame},05={source},02={target}'
        cases[key] = result
        print(f'{key}: {result["mean_mm"]} mm', flush=True)
    patterns = []
    for signs, lines in sign_cases(exact, measured, moves):
        patterns.append({'signs': signs, 'frames': {
            frame: RF.transfer_all(RF.Context(sj, fs, {}, (), lines), truth)
            for frame, fs in frames.items()}})
    summary = {}
    for frame in frames:
        values = [row['frames'][frame]['mean_mm'] for row in patterns]
        valid = np.asarray([v for v in values if v is not None], float)
        summary[frame] = {'cases': len(values), 'unmeasurable': values.count(None),
                          'min_mm': float(valid.min()) if len(valid) else None,
                          'median_mm': float(np.median(valid)) if len(valid) else None,
                          'max_mm': float(valid.max()) if len(valid) else None,
                          'over_frozen_absolute_limit': int(np.sum(valid > RR.R3_TRANSFER_MAX_MM))}
    return {'cases': cases, 'sign_patterns': patterns, 'sign_summary': summary}


def replay(proof_dir: Path, cameras: Path, out_dir: Path):
    proof_dir, cameras, out_dir = (p.resolve() for p in (proof_dir, cameras, out_dir))
    # Never place a new writer inside saved evidence or reuse an output folder.
    if out_dir == proof_dir or proof_dir in out_dir.parents or out_dir.exists():
        raise ValueError('out-dir must be a new folder outside the saved proof directory')
    paths = [proof_dir/'proof.json', proof_dir/'s1_fit.pkl', proof_dir/'fits_delivered.pkl', cameras,
             G3D/'g3d_host/relief_rules.py', G3D/'tests/proofs/wp6_prada_s1_dependencies.json']
    rec = json.loads(paths[0].read_text(encoding='utf-8'))
    expected = [(cameras, rec['cameras']['sha256']),
                (paths[-2], rec['rules']['sha256']),
                (paths[-1], rec['rules']['s1_dependencies']['sha256'])]
    source = Path(rec['source_scene']['path'])
    expected.append((source, rec['source_scene']['sha256_before']))
    for path, want in expected:
        if sha(path) != want:
            raise ValueError(f'saved input hash mismatch: {path}')
    paths.append(source)
    before = {str(p): sha(p) for p in paths}
    job, sj, truth, s1 = rebuild_s1(rec, cameras_path=cameras)
    fit = pickle.loads((proof_dir/'s1_fit.pkl').read_bytes())['fits']
    measured, exact, moves = saved_lines(s1)
    slab = RV.Model.from_spec(sj, RF.slab_spec(**s1['truth_params']), 'zero-relief truth control')
    floor = instrument_floor(sj, truth, slab, fit, measured)
    diagnosis = decompose(sj, truth, fit, measured, exact, moves)
    comparisons = {}
    for label, current, recorded in (
        ('measured', floor['models']['truth']['R3_transfer_mm'],
         s1['instrument_floor']['models']['truth']['R3_transfer_mm']),
        ('exact', diagnosis['cases']['fitted,05=exact,02=exact']['mean_mm'],
         s1['instrument_floor_exact_lines']['models']['truth']['R3_transfer_mm'])):
        comparisons[label] = {'replayed_mm': current, 'saved_mm': recorded,
                              'matches_1e_6': current is not None and abs(current-recorded) <= 1e-6}
    if not all(row['matches_1e_6'] for row in comparisons.values()):
        raise ValueError(f'saved S1 no longer reproduces: {comparisons}')
    after = {str(p): sha(p) for p in paths}
    if before != after:
        raise ValueError('an input changed during replay')
    result = {'label': 'TOOL TEST, saved S1 diagnosis; no fit or acceptance',
              'input_sha256': before, 'inputs_unchanged': True,
              'truth_spec_sha256': s1['truth_spec_sha256'], 'saved_fit_version': s1['fit_version'],
              'runtime_fit_version': RF.FIT_VERSION, 'reproduction': comparisons,
              'instrument_floor': floor, **diagnosis,
              'rules': {'absolute_mm': RR.R3_TRANSFER_MAX_MM, 'comparator_margin_mm': RR.R3_TRANSFER_MARGIN_MM},
              'limitations': ['Oracle cameras and exact lines are diagnostics, not replacement gate inputs.',
                              'Sign cases characterize the retained error budget; none is selected for fitting.',
                              'The S1 zero-relief control is not the fitted real-photo slab comparator.',
                              'No product refit, R1 change, R3 margin waiver or integration promotion.']}
    out_dir.mkdir(parents=True, exist_ok=False)
    (out_dir/'replay.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps({'reproduction': comparisons, 'sign_summary': result['sign_summary'],
                      'stop_i': floor['stop_i']}), flush=True)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--proof-dir', type=Path, required=True)
    p.add_argument('--cameras', type=Path, required=True)
    p.add_argument('--out-dir', type=Path, required=True)
    args = p.parse_args()
    replay(args.proof_dir, args.cameras, args.out_dir)
    return 0  # successful diagnostic execution, not quality acceptance


if __name__ == '__main__':
    raise SystemExit(main())
