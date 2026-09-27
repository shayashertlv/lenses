"""Compare numerical/analytic derivatives on one fixed saved mask branch.

This is a local performance instrument, not an exhaustive fit or acceptance test.
Both methods use the same observations, objective, starts, bounds and tolerances.
Wall times may include contention from other processes; call counts are separate.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import itertools
import json
import os
from pathlib import Path
import time

import numpy as np
from scipy.optimize import least_squares

from reconstruction import photo_lens_fit as single, joint_photo_lens_fit as joint, photo_lens_derivatives
from reconstruction import lens_appearance
from .compare_photo_lens_fits import _Reader, _load_group


def run(observation_paths, output, *, max_nfev=80):
    output = Path(output).resolve()
    if output.exists():
        raise ValueError('Use a new performance report path')
    reader = _Reader()
    for module in (single, joint, photo_lens_derivatives, lens_appearance):
        reader.bytes(module.__file__)
    reader.bytes(Path(__file__))
    groups = [_load_group(Path(path).resolve(), reader) for path in observation_paths]
    p = joint.JointPhotoLensFitPolicy(photo_policy=single.PhotoLensFitPolicy(max_nfev=max_nfev))
    bindings, _, branches, _, split = joint._prepare_joint(groups, p)
    branch = branches[0]
    modes = {gid: 'unknown_rear_color' if any(np.any(row['arrays']['rear_weight'][row['eligible']] > 0)
             for row in branch if row['group_id'] == gid) else 'rear_geometry_unknown_backdrop_only' for gid in bindings}
    records = []
    for index, (family, lighting, start) in enumerate(itertools.product(p.photo_policy.families, p.photo_policy.lighting_families, range(p.photo_policy.starts))):
        model = joint._JointModel(branch, {gid: family for gid in bindings}, lighting, modes, p.photo_policy, {})
        row = {'family': family, 'lighting': lighting, 'start': start, 'parameters': len(model.lower), 'methods': {}}
        # Alternate order to avoid always giving one method the warmed execution.
        for mode in (('finite_difference', 'analytic') if index % 2 == 0 else ('analytic', 'finite_difference')):
            residual_calls, jacobian_calls = 0, 0
            def residual(x):
                nonlocal residual_calls
                residual_calls += 1
                return model.residual(x)
            def jacobian(x):
                nonlocal jacobian_calls
                jacobian_calls += 1
                return photo_lens_derivatives.joint_residual_jacobian(model, x)
            began = time.perf_counter()
            fitted = least_squares(residual, model.initial(start), jac=jacobian if mode == 'analytic' else '2-point',
                bounds=(model.lower, model.upper), max_nfev=max_nfev, ftol=1e-7, xtol=1e-7, gtol=1e-7)
            row['methods'][mode] = {'seconds': time.perf_counter()-began, 'converged': bool(fitted.success),
                'status': int(fitted.status), 'nfev': fitted.nfev, 'njev': fitted.njev,
                'residual_calls': residual_calls, 'analytic_jacobian_calls': jacobian_calls,
                'objective_including_priors': fitted.cost, 'parameters': fitted.x.tolist()}
        records.append(row)
        print(json.dumps({'completed_pairs': len(records), 'family': family, 'lighting': lighting, 'start': start}), flush=True)
    reader.verify()
    summary = {mode: {'seconds': sum(r['methods'][mode]['seconds'] for r in records),
                      'residual_calls': sum(r['methods'][mode]['residual_calls'] for r in records),
                      'converged': sum(r['methods'][mode]['converged'] for r in records)}
               for mode in ('finite_difference', 'analytic')}
    result = {'schema_version': 1, 'method': 'fixed_branch_derivative_comparison_v1', 'accepted': False,
        'policy': asdict(p), 'pairs': len(records), 'mask_branches_available': len(branches),
        'mask_branch_tested': [joint._identity(row) for row in branch], 'spatial_split': split,
        'input_sha256': reader.pins, 'numerical_versions': joint._implementation(),
        'threading_environment': {name: os.environ.get(name) for name in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS')},
        'summary': summary, 'records': records,
        'limitations': ['Only the first deterministic branch is tested; results cannot replace the complete declared ensemble.',
            'Analytic and finite-difference local optimizers may stop in different minima near interval kinks or weakly constrained directions.',
            'A faster derivative does not improve physical identifiability, semantic support or data coverage.',
            'Elapsed times are local concurrent-process measurements, not an isolated device throughput benchmark.']}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as handle:
        json.dump(result, handle, indent=2, allow_nan=False)
        handle.write('\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--observations', type=Path, action='append', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--max-nfev', type=int, default=80)
    args = parser.parse_args()
    result = run(args.observations, args.output, max_nfev=args.max_nfev)
    print(json.dumps(result['summary']))
