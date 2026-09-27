"""Bounded float32 effective-group import contract, not product acceptance.

The height predicate matches the browser's common closed-rounding-cell model:
one pair of pre-quantization Y endpoints must satisfy every referenced vertex.
Closed cells conservatively include ties; this does not invert IEEE tie-breaking
or recover unique source coordinates. Actual coincident V fields remain exact.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import math
from pathlib import Path
import re

import numpy as np

from .optical_group_validation import (
    CoincidentPatchPolicy, ExactBudgetExceeded, ExactWork, _cross, _dot,
    validate_optical_group_ties,
)


PROFILE = 'effective_optical_group_v1_experiment'


@dataclass(frozen=True)
class EffectiveOpticalRuntimePolicy:
    maximum_groups: int = 8
    maximum_primitives: int = 4096
    maximum_vertices: int = 1_500_000
    maximum_triangles: int = 500_000
    maximum_candidate_pairs: int = 2_000_000
    maximum_coordinate_integer_bits: int = 256
    maximum_arithmetic_integer_bits: int = 4096
    maximum_arithmetic_work: int = 500_000_000
    maximum_broadphase_work: int = 20_000_000
    maximum_height_polygon_vertices: int = 4096

    def __post_init__(self):
        for name, value in asdict(self).items():
            limit = self.__dataclass_fields__[name].default
            if type(value) is not int or not 1 <= value <= limit:
                raise ValueError(f'{name} must be positive and cannot exceed the browser contract {limit}')


def _rounding_interval(value):
    if value == 0:
        return (-2.**-150, 2.**-150)
    with np.errstate(over='ignore'):
        low = float(np.nextafter(np.float32(value), np.float32(-np.inf)))
        high = float(np.nextafter(np.float32(value), np.float32(np.inf)))
    if not math.isfinite(low) or not math.isfinite(high):
        raise ExactBudgetExceeded('float32_rounding_envelope_range_exceeded')
    # Adjacent float32 midpoints are exactly representable in binary64.
    return (value-(value-low)/2, value+(high-value)/2)


def _height(members, work, policy):
    records, intervals = [], {}
    minimum = maximum = None
    minimum_witness = maximum_witness = False
    min_lo = min_hi = math.inf
    max_lo = max_hi = -math.inf
    denominator_exponent = 0
    def interval(value):
        if value not in intervals:
            intervals[value] = _rounding_interval(value)
        return intervals[value]
    for primitive in members:
        used = np.unique(primitive['indices'])
        for index in used:
            y, v = float(primitive['positions'][index, 1]), float(primitive['uv'][index, 1])
            lo, hi = interval(y)
            records.append((lo, hi, v))
            min_lo, min_hi = min(min_lo, lo), min(min_hi, hi)
            max_lo, max_hi = max(max_lo, lo), max(max_hi, hi)
            denominator_exponent = max(denominator_exponent, *(value.as_integer_ratio()[1].bit_length()-1 for value in (lo, hi)))
            if minimum is None or y < minimum:
                minimum, minimum_witness = y, False
            if maximum is None or y > maximum:
                maximum, maximum_witness = y, False
            if y == minimum and v == 0:
                minimum_witness = True
            if y == maximum and v == 1:
                maximum_witness = True
    if not (max_lo > min_hi and minimum_witness and maximum_witness):
        return {'status': 'unsupported', 'reason': 'group_height_requires_distinct_bottom_0_and_top_1'}
    def coordinate(value):
        numerator, denominator = value.as_integer_ratio()
        return work.check(numerator << (denominator_exponent-(denominator.bit_length()-1)))
    def constraint(y, v, lower):
        n, d = v.as_integer_ratio()
        a, c = work.sub(d, n), work.mul(coordinate(y), d)
        return (a, n, -c) if lower else (-a, -n, c)
    def constraints():
        for lo, hi, v in records:
            v_lo, v_hi = interval(v)
            yield constraint(lo, min(1., v_hi), True)
            yield constraint(hi, max(0., v_lo), False)
    witness = (coordinate(minimum), coordinate(maximum), 1)
    if all(_dot(plane, witness, work) >= 0 for plane in constraints()):
        return {'status': 'common_height_feasible', 'proof': 'one_shared_stored_endpoint_witness', 'referenced_vertices': len(records)}
    a_lo, a_hi, b_lo, b_hi = map(coordinate, (min_lo, min_hi, max_lo, max_hi))
    polygon = [(a_lo, b_lo, 1), (a_hi, b_lo, 1), (a_hi, b_hi, 1), (a_lo, b_hi, 1)]
    for plane in constraints():
        clipped = []
        previous, old = polygon[-1], _dot(plane, polygon[-1], work)
        for current in polygon:
            new = _dot(plane, current, work)
            if (old >= 0) != (new >= 0):
                intersection = _cross(_cross(previous, current, work), plane, work)
                if not intersection[2]:
                    raise ValueError('height_feasibility_nonfinite_intersection')
                divisor = work.gcd(*intersection)
                if intersection[2] < 0:
                    divisor = -divisor
                clipped.append(tuple(value//divisor for value in intersection))
            if new >= 0:
                clipped.append(current)
            previous, old = current, new
        polygon = [point for i, point in enumerate(clipped) if not i or point != clipped[i-1]]
        if not polygon:
            return {'status': 'unsupported', 'reason': 'intrinsic_V_does_not_match_group_wide_Y_height'}
        if len(polygon) > policy.maximum_height_polygon_vertices:
            raise ExactBudgetExceeded('height_polygon_budget_exceeded')
    return {'status': 'common_height_feasible', 'proof': 'exact_common_endpoint_halfplane_intersection',
            'referenced_vertices': len(records), 'feasible_polygon_vertices': len(polygon)}


def validate_effective_optical_runtime(groups: list[dict], *, policy=EffectiveOpticalRuntimePolicy()) -> dict:
    """Validate actual float32 primitive arrays using the analysis group schema.

    This is the numeric geometry/attribute subset of browser import validation.
    The GLB exporter/reader separately validates source metadata, material
    descriptors, baked transforms and membership. No supplied receipt is a proof.
    """
    if not isinstance(policy, EffectiveOpticalRuntimePolicy) or not isinstance(groups, list) or not groups:
        raise ValueError('Expected nonempty optical groups and EffectiveOpticalRuntimePolicy')
    report = {'schema_version': 1, 'method': 'effective_group_float32_runtime_contract_v1', 'profile': PROFILE,
        'status': 'unsupported', 'complete': False, 'accepted': False, 'quality_verdict': 'unmeasured',
        'policy': asdict(policy), 'reasons': [], 'height': [], 'coincident_patch_validation': None,
        'counts': {'groups': len(groups), 'primitives': 0, 'vertices': 0, 'triangles': 0},
        'implementation_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'limitations': [
            'Numerical effective-profile compatibility does not identify semantic groups, physical materials or product accuracy.',
            'Height feasibility uses closed float32 rounding enclosures, not unique source recovery or inverse IEEE tie-breaking.',
            'Only exact positive-area coplanar ties are proved; near ties, noncoplanar intersections and GPU arithmetic remain separate.',
            'Opaque material simulation, camera/light layer capacity, actual rendering and device performance are not certified.',
            'GLB metadata, descriptors and baked transforms require the separate strict asset reader.',
            'Python and browser have bounded but different operation accounting; no universal work-budget equivalence is claimed.']}
    work = ExactWork(policy.maximum_arithmetic_integer_bits, policy.maximum_arithmetic_work, policy.maximum_broadphase_work)
    def capacity(value, limit, reason):
        if value > limit:
            raise ExactBudgetExceeded(reason)
    try:
        capacity(len(groups), policy.maximum_groups, 'group_budget_exceeded')
        ids = set()
        for group in groups:
            if (not isinstance(group, dict) or set(group) != {'group_id', 'coordinate_frame_id', 'appearance_sha256', 'primitives'}
                    or not isinstance(group['group_id'], str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}', group['group_id'])
                    or group['group_id'] in ids or not isinstance(group['primitives'], list) or not group['primitives']):
                raise ValueError('Invalid runtime group identity/schema')
            ids.add(group['group_id']); member_ids = set()
            for primitive in group['primitives']:
                pid = primitive.get('id')
                if not isinstance(pid, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}', pid) or pid in member_ids:
                    raise ValueError('Invalid or duplicate runtime member ID')
                member_ids.add(pid)
                p, f, n, uv = (np.asarray(primitive[key]) for key in ('positions', 'indices', 'normals', 'uv'))
                if (p.ndim != 2 or p.shape[1] != 3 or len(p) < 3 or n.shape != p.shape or uv.shape != (len(p), 2)
                        or any(a.dtype.kind != 'f' or a.dtype.itemsize != 4 for a in (p, n, uv))
                        or f.ndim != 2 or f.shape[1] != 3 or not len(f) or f.dtype.kind not in 'iu'
                        ):
                    raise ValueError('Runtime profile requires finite actual float32 XYZ/NORMAL/UV and complete integer triangles')
                for name, count, limit, reason in (
                        ('primitives', 1, policy.maximum_primitives, 'mesh_budget_exceeded'),
                        ('vertices', len(p), policy.maximum_vertices, 'vertex_budget_exceeded'),
                        ('triangles', len(f), policy.maximum_triangles, 'triangle_budget_exceeded')):
                    report['counts'][name] += count
                    capacity(report['counts'][name], limit, reason)
                if any(not np.isfinite(a).all() for a in (p, n, uv)) or np.any(f < 0) or np.any(f >= len(p)):
                    raise ValueError('Runtime profile requires finite actual float32 XYZ/NORMAL/UV and valid integer triangles')
                used = np.unique(f)
                if np.any(np.all(n[used] == 0, axis=1)) or np.any((uv[used, 1] < 0)|(uv[used, 1] > 1)):
                    raise ValueError('Referenced runtime normals must be nonzero and intrinsic V in 0..1')
            height = _height(group['primitives'], work, policy)
            report['height'].append({'group_id': group['group_id'], **height})
            if height['status'] != 'common_height_feasible':
                report['reasons'].append(height['reason']); return report
        remaining = policy.maximum_arithmetic_work-work.arithmetic
        if remaining < 1:
            raise ExactBudgetExceeded('arithmetic_work_budget_exceeded')
        ties = validate_optical_group_ties(groups, policy=CoincidentPatchPolicy(
            maximum_triangles=policy.maximum_triangles, maximum_candidate_pairs=policy.maximum_candidate_pairs,
            maximum_integer_bits=policy.maximum_coordinate_integer_bits,
            maximum_arithmetic_integer_bits=policy.maximum_arithmetic_integer_bits,
            maximum_arithmetic_work=remaining, maximum_broadphase_work=policy.maximum_broadphase_work))
        report['coincident_patch_validation'] = ties
        if not ties['complete'] or ties['status'] != 'coincident_patch_contract_satisfied':
            report['reasons'].extend(ties['reasons'] or list(ties['reason_counts'])); return report
        report.update(status='runtime_contract_satisfied', complete=True)
        return report
    except ExactBudgetExceeded as error:
        report['reasons'].append(str(error)); return report
    finally:
        report['height_work'] = {'arithmetic_limb_units': work.arithmetic}
