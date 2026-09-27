"""Exact, bounded coincident-patch diagnostics for effective optical groups.

This checks positive-area coplanar overlaps in the supplied coordinate arrays.
It does not certify semantic groups, volume optics, raster-depth equality or
noncoplanar intersection lines. Coordinates are never welded or quantized.

Same-group coincidence is supported only with identical intrinsic-V affine
fields and a sufficient proof of equal symmetric normal fields: constant
collinear directions, or identical geometric triangles with corresponding
corner directions equal up to one uniform sign. Other varying-normal overlaps
remain unsupported, even when numerical sampling might suggest agreement.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import re

import numpy as np
from shapely.geometry import box
from shapely.strtree import STRtree


@dataclass(frozen=True)
class CoincidentPatchPolicy:
    maximum_triangles: int = 500_000
    maximum_candidate_pairs: int = 2_000_000
    maximum_integer_bits: int = 256
    maximum_witnesses: int = 32
    maximum_arithmetic_integer_bits: int = 4096
    maximum_arithmetic_work: int = 500_000_000
    maximum_broadphase_work: int = 20_000_000

    def __post_init__(self):
        for key, value in asdict(self).items():
            if type(value) is not int or value < 1:
                raise ValueError(f'{key} must be a positive integer')


class ExactBudgetExceeded(ValueError):
    """An explicit capacity refusal, never a partial proof of compatibility."""


class ExactWork:
    """Bound scalar/rational intermediates and charged 64-bit-limb work.

    These are deterministic Python operation bounds, not a CPU-time limit or a
    claim that Python and JavaScript execute the same number of instructions.
    """
    def __init__(self, maximum_bits=4096, maximum_work=500_000_000, maximum_broadphase=20_000_000):
        self.maximum_bits, self.maximum_work, self.maximum_broadphase = maximum_bits, maximum_work, maximum_broadphase
        self.arithmetic = self.broadphase = 0

    def check(self, value):
        values = (value.numerator, value.denominator) if isinstance(value, Fraction) else (int(value),)
        width = max(abs(v).bit_length() for v in values)
        if width > self.maximum_bits:
            raise ExactBudgetExceeded('arithmetic_integer_budget_exceeded')
        self.arithmetic += sum(max(1, (abs(v).bit_length()+63)//64) for v in values)
        if self.arithmetic > self.maximum_work:
            raise ExactBudgetExceeded('arithmetic_work_budget_exceeded')
        return value

    def rational(self, numerator, denominator=1):
        """Check unreduced components, then normalize with charged Euclid work.

        Fraction construction receives already coprime bounded integers. Its
        representation bookkeeping cannot form larger cross-products. We do
        not delegate rational arithmetic, whose hidden pre-reduction values
        otherwise evade the declared intermediate bounds.
        """
        self.check(numerator); self.check(denominator)
        if not denominator:
            raise ValueError('exact_rational_zero_denominator')
        if denominator < 0:
            numerator, denominator = -numerator, -denominator
        divisor = self.gcd(numerator, denominator)
        return Fraction(numerator//divisor, denominator//divisor)

    def add(self, a, b):
        if isinstance(a, Fraction) or isinstance(b, Fraction):
            return self.rational(self.add(self.mul(a.numerator, b.denominator), self.mul(b.numerator, a.denominator)),
                                 self.mul(a.denominator, b.denominator))
        return self.check(a+b)
    def sub(self, a, b):
        if isinstance(a, Fraction) or isinstance(b, Fraction):
            return self.rational(self.sub(self.mul(a.numerator, b.denominator), self.mul(b.numerator, a.denominator)),
                                 self.mul(a.denominator, b.denominator))
        return self.check(a-b)
    def mul(self, a, b):
        if isinstance(a, Fraction) or isinstance(b, Fraction):
            return self.rational(self.mul(a.numerator, b.numerator), self.mul(a.denominator, b.denominator))
        # Both operands have already bounded bit widths. Check products before
        # allocation as well; conservative refusal is permitted at a budget.
        for x, y in ((getattr(a, 'numerator', a), getattr(b, 'numerator', b)),
                     (getattr(a, 'denominator', 1), getattr(b, 'denominator', 1))):
            if abs(int(x)).bit_length()+abs(int(y)).bit_length() > self.maximum_bits+1:
                raise ExactBudgetExceeded('arithmetic_integer_budget_exceeded')
        return self.check(a*b)
    def div(self, a, b):
        return self.rational(self.mul(a.numerator, b.denominator), self.mul(a.denominator, b.numerator))
    def gcd(self, *values):
        result = 0
        for value in values:
            a, b = abs(result), abs(value)
            while b:
                a, b = b, self.check(a % b)
            result = a
        return result
    def visit(self, count=1):
        self.broadphase += count
        if self.broadphase > self.maximum_broadphase:
            raise ExactBudgetExceeded('broadphase_work_budget_exceeded')


def _hash_array(value):
    array = np.ascontiguousarray(value)
    header = json.dumps({'dtype': array.dtype.str, 'shape': array.shape}, sort_keys=True).encode()
    return hashlib.sha256(header+array.tobytes()).hexdigest()


def _cross(a, b, work=None):
    if work is None:
        return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])
    return tuple(work.sub(work.mul(a[i], b[j]), work.mul(a[j], b[i])) for i, j in ((1, 2), (2, 0), (0, 1)))


def _dot(a, b, work=None):
    if work is None:
        return sum(x*y for x, y in zip(a, b))
    result = 0
    for x, y in zip(a, b): result = work.add(result, work.mul(x, y))
    return result


def _subtract(a, b):
    return tuple(x-y for x, y in zip(a, b))


def _orient(a, b, p, work):
    return work.sub(work.mul(work.sub(b[0], a[0]), work.sub(p[1], a[1])),
                    work.mul(work.sub(b[1], a[1]), work.sub(p[0], a[0])))


def _intersection(a, b, work):
    """Exact rational convex clipping; no positive area is discarded."""
    polygon = [tuple(Fraction(v) for v in point) for point in a]
    if _orient(*b, work) < 0:
        b = (b[0], b[2], b[1])
    for first, second in zip(b, (*b[1:], b[0])):
        if not polygon:
            break
        clipped = []
        previous = polygon[-1]; old = _orient(first, second, previous, work)
        for current in polygon:
            new = _orient(first, second, current, work)
            if (new >= 0) != (old >= 0):
                t = work.div(old, work.sub(old, new))
                clipped.append(tuple(work.add(x, work.mul(t, work.sub(y, x))) for x, y in zip(previous, current)))
            if new >= 0:
                clipped.append(current)
            previous, old = current, new
        polygon = clipped
    if len(polygon) < 3:
        return []
    area2 = 0
    for i in range(1, len(polygon)-1): area2 = work.add(area2, _orient(polygon[0], polygon[i], polygon[i+1], work))
    return polygon if area2 else []


def _affine(points, values, work):
    a, b, c = points
    determinant = _orient(a, b, c, work)
    q = tuple(work.check(Fraction(float(value))) for value in values)
    def weighted(axis):
        return work.add(work.add(work.mul(q[0], work.sub(b[axis], c[axis])),
                                 work.mul(q[1], work.sub(c[axis], a[axis]))),
                        work.mul(q[2], work.sub(a[axis], b[axis])))
    x, y = work.div(weighted(1), determinant), work.div(-weighted(0), determinant)
    return (x, y, work.sub(work.sub(q[0], work.mul(x, a[0])), work.mul(y, a[1])))


def _integer_normal(values, work):
    ratios = [float(value).as_integer_ratio() for value in values]
    exponent = max(den.bit_length()-1 for _, den in ratios)
    return tuple(work.check(num << (exponent-(den.bit_length()-1))) for num, den in ratios)


def _normal_origin_reason(normals, work):
    """Exact zero-in-convex-hull test, invariant under positive corner scaling."""
    edges = [_cross(normals[i], normals[(i+1) % 3], work) for i in range(3)]
    if any(edge == (0, 0, 0) and _dot(normals[i], normals[(i+1) % 3], work) <= 0 for i, edge in enumerate(edges)):
        return 'normal_field_contains_zero_on_edge'
    if _dot(normals[0], edges[1], work):
        return None
    plane = next((edge for edge in edges if edge != (0, 0, 0)), None)
    if plane is None:
        return None
    axis = next(i for i, value in enumerate(plane) if value)
    signs = [edge[axis] for edge in edges]
    if all(value >= 0 for value in signs) or all(value <= 0 for value in signs):
        return 'normal_field_contains_zero_interior'
    return None


def _normal_proof(first, second, work):
    """Sufficient exact proof before per-vertex normalization/interpolation."""
    n, m = first['normals'], second['normals']
    constant = lambda vectors: all(_cross(vectors[0], v, work) == (0, 0, 0) and _dot(vectors[0], v, work) > 0 for v in vectors[1:])
    if constant(n) and constant(m):
        return ('constant_collinear_directions' if _cross(n[0], m[0], work) == (0, 0, 0)
                else 'conflicting_constant_normal_directions')
    a = dict(zip(first['xyz'], n)); b = dict(zip(second['xyz'], m))
    if a.keys() != b.keys():
        return 'unproven_retessellated_varying_normal_field'
    signs = set()
    for point, normal in a.items():
        other = b[point]
        if _cross(normal, other, work) != (0, 0, 0):
            return 'conflicting_corresponding_corner_directions'
        signs.add(1 if _dot(normal, other, work) > 0 else -1)
    if len(signs) != 1:
        return 'unproven_nonuniform_normal_sign'
    # Every triangle already passed exact origin-hull exclusion. An acute-cone
    # shortcut would incorrectly reject identical, valid obtuse fields.
    return 'identical_triangle_uniform_corner_direction_sign'


def validate_optical_group_ties(groups: list[dict], *, policy=CoincidentPatchPolicy()) -> dict:
    """Check caller-grouped primitives in one explicit common coordinate frame.

    Each group is {group_id, coordinate_frame_id, appearance_sha256, primitives}.
    Each primitive is {id, positions, indices, normals, uv}; extra preparation
    fields are ignored. Appearance SHA binds a single descriptor per group but
    does not validate it. Run again on actual exported/loaded float32 attributes:
    a result for float64 source arrays cannot certify their later quantization.
    """
    if not isinstance(policy, CoincidentPatchPolicy) or not isinstance(groups, list) or not groups:
        raise ValueError('Expected nonempty explicit groups and CoincidentPatchPolicy')
    report = {'schema_version': 1, 'method': 'exact_coincident_optical_patch_diagnostic_v1',
        'status': 'unsupported', 'complete': False, 'accepted': False, 'quality_verdict': 'unmeasured',
        'policy': asdict(policy), 'groups': [], 'reasons': [], 'reason_counts': {}, 'witnesses': [],
        'counts': {'triangles': 0, 'normal_triangles_checked': 0, 'exact_planes': 0, 'candidate_pairs': 0,
                   'positive_area_pairs': 0, 'proven_same_group_pairs': 0, 'unsupported_pairs': 0},
        'implementation_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'predicate_contract': 'exact rational arithmetic on supplied IEEE floating-point values; zero geometric tolerance',
        'limitations': ['Caller group identity and appearance SHA are unverified assertions, not material or product acceptance.',
            'Only positive-area coplanar overlap is checked; shared boundaries and noncoplanar intersection lines remain outside this contract.',
            'Near-coincidence and finite GPU raster/depth/normal precision require separate runtime checks.',
            'No manifoldness, thickness, global self-intersection, opaque-stop, or all-pose layer-capacity certificate is produced.',
            'Intrinsic V is checked; unused U and source texture/normal maps are not part of this effective optical response.',
            'A result applies only to the exact supplied arrays and one common frame; export or transform requires revalidation.',
            'Unproven varying-normal overlap is unsupported rather than approximated or accepted from sampled agreement.',
            'Every referenced triangle excludes zero from its corner-normal convex hull; finite GPU precision is not certified.']}
    work = ExactWork(policy.maximum_arithmetic_integer_bits, policy.maximum_arithmetic_work, policy.maximum_broadphase_work)
    try:
        return _validate_optical_group_ties(groups, policy, report, work)
    except ExactBudgetExceeded as error:
        report['reasons'].append(str(error))
        return report
    finally:
        report['work'] = {'arithmetic_limb_units': work.arithmetic, 'broadphase_units': work.broadphase}


def _validate_optical_group_ties(groups, policy, report, work):
    items, ids, frames = [], set(), set()
    maximum_denominator = 0
    for group in groups:
        if not isinstance(group, dict) or set(group) != {'group_id', 'coordinate_frame_id', 'appearance_sha256', 'primitives'}:
            raise ValueError('Each group requires explicit identity, frame, descriptor SHA and primitives')
        gid, frame, appearance = (group[k] for k in ('group_id', 'coordinate_frame_id', 'appearance_sha256'))
        if not isinstance(gid, str) or not gid or gid in ids or not isinstance(frame, str) or not frame:
            raise ValueError('Group IDs must be unique and frame IDs nonempty')
        if not isinstance(appearance, str) or not re.fullmatch('[a-f0-9]{64}', appearance):
            raise ValueError('Each group needs one lowercase appearance SHA256')
        if not isinstance(group['primitives'], list) or not group['primitives']:
            raise ValueError('Group primitives must be nonempty')
        ids.add(gid); frames.add(frame)
        row = {'group_id': gid, 'coordinate_frame_id': frame, 'appearance_sha256': appearance, 'primitives': []}
        report['groups'].append(row)
        primitive_ids = set()
        for primitive in group['primitives']:
            pid = primitive.get('id')
            if not isinstance(pid, str) or not pid or pid in primitive_ids:
                raise ValueError('Primitive IDs must be nonempty and unique within a group')
            primitive_ids.add(pid)
            p, f, n, uv = (np.asarray(primitive[k]) for k in ('positions', 'indices', 'normals', 'uv'))
            if (p.ndim != 2 or p.shape[1] != 3 or p.dtype.kind != 'f' or p.dtype.itemsize > 8 or not np.isfinite(p).all()
                    or f.ndim != 2 or f.shape[1] != 3 or f.dtype.kind not in 'iu' or not len(f)
                    or np.any(f < 0) or np.any(f >= len(p)) or n.shape != p.shape or n.dtype.kind != 'f'
                    or n.dtype.itemsize > 8 or uv.shape != (len(p), 2) or uv.dtype.kind != 'f' or uv.dtype.itemsize > 8):
                raise ValueError('Primitives require finite indexed geometry with corresponding normal/UV arrays')
            used = np.unique(f)
            if (not np.isfinite(n[used]).all() or np.any(np.all(n[used] == 0, axis=1))
                    or not np.isfinite(uv[used]).all() or np.any((uv[used, 1] < 0)|(uv[used, 1] > 1))):
                raise ValueError('Referenced normals must be nonzero finite and intrinsic V within 0..1')
            report['counts']['triangles'] += len(f)
            row['primitives'].append({'id': pid, 'triangles': len(f), 'attribute_sha256': {
                key: _hash_array(array) for key, array in zip(('positions', 'indices', 'normals', 'uv'), (p, f, n, uv))}})
            if report['counts']['triangles'] > policy.maximum_triangles:
                report['reasons'].append('triangle_budget_exceeded'); return report
            ratios = {int(i): tuple(float(v).as_integer_ratio() for v in p[i]) for i in used}
            maximum_denominator = max(maximum_denominator, max(d.bit_length()-1 for point in ratios.values() for _, d in point))
            items.append((gid, pid, ratios, f, n, uv))
    if len(frames) != 1:
        raise ValueError('Every group must use the same explicit coordinate frame')
    planes = {}
    for gid, pid, ratios, faces, normals, uv in items:
        positions = {index: tuple(num << (maximum_denominator-(den.bit_length()-1)) for num, den in point)
                     for index, point in ratios.items()}
        if any(abs(value).bit_length() > policy.maximum_integer_bits for p in positions.values() for value in p):
            report['reasons'].append('exact_coordinate_integer_budget_exceeded'); return report
        normal_vectors = {index: _integer_normal(normals[index], work) for index in ratios}
        for face_index, indices in enumerate(faces):
            xyz = tuple(positions[int(i)] for i in indices)
            cross = _cross(_subtract(xyz[1], xyz[0]), _subtract(xyz[2], xyz[0]), work)
            if cross == (0, 0, 0):
                report['reasons'].append(f'{gid}/{pid}/{face_index}:exact_degenerate_triangle'); return report
            values = tuple(normal_vectors[int(i)] for i in indices)
            reason = _normal_origin_reason(values, work)
            report['counts']['normal_triangles_checked'] += 1
            if reason:
                report['reasons'].append(f'{gid}/{pid}/{face_index}:{reason}'); return report
            coefficients = (*cross, -_dot(cross, xyz[0], work))
            divisor = work.gcd(*coefficients)
            if next(v for v in cross if v) < 0:
                divisor *= -1
            plane = tuple(v//divisor for v in coefficients)
            axis = max(range(3), key=lambda i: abs(plane[i]))
            projected = tuple(tuple(v for k, v in enumerate(p) if k != axis) for p in xyz)
            planes.setdefault(plane, []).append({'group': gid, 'primitive': pid, 'face': face_index,
                'xyz': xyz, 'xy': projected, 'v': uv[indices, 1], 'normals': values})
    report['counts']['exact_planes'] = len(planes)
    for triangles in planes.values():
        if len(triangles) < 2:
            continue
        bounds = []
        for triangle in triangles:
            work.visit()
            p = triangle['xy']
            # A caller may permit exact integers larger than the floating-point
            # spatial index can represent. Never turn that capacity mismatch
            # into a missing pair or an uncaught conversion failure.
            try:
                with np.errstate(over='ignore'):
                    lo = [np.nextafter(float(min(v[k] for v in p)), -np.inf) for k in range(2)]
                    hi = [np.nextafter(float(max(v[k] for v in p)), np.inf) for k in range(2)]
            except OverflowError:
                report['reasons'].append('broadphase_float_range_exceeded'); return report
            if not all(math.isfinite(value) for value in (*lo, *hi)):
                report['reasons'].append('broadphase_float_range_exceeded'); return report
            bounds.append(box(*lo, *hi))
        tree = STRtree(bounds)
        for i, first in enumerate(triangles):
            candidates = tree.query(bounds[i])
            work.visit(len(candidates))
            for j in sorted(int(v) for v in candidates if v > i):
                if report['counts']['candidate_pairs'] >= policy.maximum_candidate_pairs:
                    report['reasons'].append('candidate_pair_budget_exceeded'); return report
                report['counts']['candidate_pairs'] += 1
                second = triangles[j]
                polygon = _intersection(first['xy'], second['xy'], work)
                if not polygon:
                    continue
                report['counts']['positive_area_pairs'] += 1
                witness = {'first': {k: first[k] for k in ('group', 'primitive', 'face')},
                           'second': {k: second[k] for k in ('group', 'primitive', 'face')}}
                if first['group'] != second['group']:
                    reason = 'cross_group_coincident_patch_undefined_order'
                elif _affine(first['xy'], first['v'], work) != _affine(second['xy'], second['v'], work):
                    reason = 'conflicting_intrinsic_v_affine_fields'
                else:
                    reason = _normal_proof(first, second, work)
                proven = reason in ('constant_collinear_directions', 'identical_triangle_uniform_corner_direction_sign')
                report['reason_counts'][reason] = report['reason_counts'].get(reason, 0)+1
                report['counts']['proven_same_group_pairs' if proven else 'unsupported_pairs'] += 1
                if len(report['witnesses']) < policy.maximum_witnesses:
                    report['witnesses'].append({**witness, 'status': 'proven' if proven else 'unsupported', 'reason': reason})
    report['complete'] = True
    report['status'] = 'unsupported' if report['counts']['unsupported_pairs'] else 'coincident_patch_contract_satisfied'
    report['witnesses_truncated'] = report['counts']['positive_area_pairs'] > len(report['witnesses'])
    return report
