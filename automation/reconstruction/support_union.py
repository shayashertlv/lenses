"""Finite shared support-set inference, without physical optical grouping.

The objective is the worst-view aperture IoU of a union of supplied component
footprints. Every component has one shared inclusion variable. Unknown image
domains are unscored, not negative evidence. The finite MILP is complete for
these supplied footprints; its floating-point feasibility bounds are conditional
on SciPy/HiGHS numerical decisions, not rigorous mathematical certificates.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
import time

import numpy as np
import scipy
from scipy import sparse
from scipy.optimize import Bounds, LinearConstraint, milp


@dataclass(frozen=True)
class SupportLimits:
    maximum_components: int = 1024
    maximum_views: int = 32
    maximum_pixels_per_view: int = 262_144
    maximum_total_pixels: int = 2_000_000
    maximum_component_pixels: int = 8_000_000
    maximum_signature_bytes: int = 64_000_000
    maximum_signatures: int = 200_000
    maximum_signature_memberships: int = 2_000_000
    maximum_constraint_nonzeros: int = 8_000_000
    maximum_apertures: int = 256
    maximum_union_mask_pixels: int = 64_000_000


_BINARY_TOLERANCE = 1e-6
_STATUSES = {0: 'optimal', 1: 'limit_reached', 2: 'infeasible',
             3: 'unbounded', 4: 'other_failure'}


def _limits(value):
    if not isinstance(value, SupportLimits) or any(type(v) is not int or v < 1 for v in asdict(value).values()):
        raise ValueError('Positive integer support limits required')


def _json(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError('Finite JSON provenance required') from error


def _provenance(value):
    if not isinstance(value, dict) or not value:
        raise ValueError('Nonempty provenance required')
    return json.loads(_json(value))


def _hash(array):
    value = np.ascontiguousarray(array)
    prefix = _json({'dtype': value.dtype.str, 'shape': list(value.shape)})
    return hashlib.sha256(prefix+value.tobytes()).hexdigest()


def _mask_domain(mask, known, shape):
    mask, known = np.asarray(mask), np.asarray(known)
    if (mask.dtype != np.bool_ or known.dtype != np.bool_ or mask.shape != tuple(shape)
            or known.shape != tuple(shape) or np.any(mask & ~known)):
        raise ValueError('Matching boolean masks with positives inside known domain required')
    return mask, known


def union_apertures(hypotheses):
    """Union one caller-declared interpretation; preserve every constituent.

    A union is known positive if any constituent is positive. It is known
    negative only where every constituent supplied a prediction. This helper
    never chooses among correlated decoder, crop, or component alternatives.
    """
    limits = SupportLimits()
    if not isinstance(hypotheses, list) or not 1 <= len(hypotheses) <= limits.maximum_apertures:
        raise ValueError('A bounded nonempty aperture interpretation is required')
    shape, ids, captured, records = None, set(), [], []
    for item in hypotheses:
        if not isinstance(item, dict) or set(item) != {'id', 'mask', 'known_domain', 'provenance'}:
            raise ValueError('Aperture requires exactly id, mask, known_domain and provenance')
        identifier = item['id']
        if not isinstance(identifier, str) or not identifier or identifier in ids:
            raise ValueError('Unique nonempty aperture IDs required')
        ids.add(identifier)
        candidate = np.asarray(item['mask'])
        if candidate.ndim != 2 or min(candidate.shape) < 1:
            raise ValueError('Aperture grids must be nonempty two-dimensional arrays')
        if shape is None:
            shape = candidate.shape
            if math.prod(shape)*len(hypotheses) > limits.maximum_union_mask_pixels:
                raise ValueError('Aperture union pixel capacity exceeded')
        mask, known = _mask_domain(candidate, item['known_domain'], shape)
        provenance = _provenance(item['provenance'])
        captured.append((mask, known))
        records.append({'id': identifier, 'provenance': provenance,
                        'mask_sha256': _hash(mask), 'known_domain_sha256': _hash(known)})
    positive, all_known = np.zeros(shape, bool), np.ones(shape, bool)
    for mask, known in captured:
        positive |= mask
        all_known &= known
    known = positive | all_known
    positive.setflags(write=False); known.setflags(write=False)
    return {'mask': positive, 'known_domain': known,
            'provenance': {'method': 'declared_aperture_union_v1', 'constituents': records,
                           'known_domain_rule': 'OR positives OR AND constituent known domains',
                           'alternative_selection': 'caller_supplied; not inferred',
                           'semantic_identity': 'unverified'}}


def _views(views, limits):
    if not isinstance(views, list) or not 2 <= len(views) <= limits.maximum_views:
        raise ValueError('At least two bounded views required')
    identifiers, captured, records = set(), [], []
    components, pixels_total, events_total, packed_bytes = None, 0, 0, 0
    for view in views:
        if not isinstance(view, dict) or set(view) != {'id', 'shape', 'component_pixels', 'mask', 'known_domain', 'provenance'}:
            raise ValueError('Invalid support view fields')
        identifier, shape = view['id'], view['shape']
        if not isinstance(identifier, str) or not identifier or identifier in identifiers:
            raise ValueError('Distinct nonempty view IDs required')
        identifiers.add(identifier)
        if (not isinstance(shape, (list, tuple)) or len(shape) != 2
                or any(type(n) is not int or n < 1 for n in shape)):
            raise ValueError('Positive integer [height,width] shape required')
        shape = tuple(shape); pixels = math.prod(shape)
        pixels_total += pixels
        if pixels > limits.maximum_pixels_per_view or pixels_total > limits.maximum_total_pixels:
            raise ValueError('Support image pixel capacity exceeded')
        supplied = view['component_pixels']
        if not isinstance(supplied, list) or not 1 <= len(supplied) <= limits.maximum_components:
            raise ValueError('Bounded complete component inventory required')
        if components is None:
            components = len(supplied)
        if len(supplied) != components:
            raise ValueError('Every view must retain the same stable component inventory')
        packed_bytes += pixels*((components+7)//8)
        if packed_bytes > limits.maximum_signature_bytes:
            raise ValueError('Packed membership-signature byte capacity exceeded')
        mask, known = _mask_domain(view['mask'], view['known_domain'], shape)
        if not mask.any():
            raise ValueError('This profile requires a positive aperture in every supplied view')
        provenance = _provenance(view['provenance'])
        arrays, component_pins = [], []
        for supplied_pixels in supplied:
            # Inspect the length before an optional list-to-array conversion.
            if isinstance(supplied_pixels, np.ndarray) and supplied_pixels.ndim != 1:
                raise ValueError('Component pixels must be one-dimensional integer vectors')
            if not isinstance(supplied_pixels, (list, tuple, np.ndarray)) or len(supplied_pixels) > limits.maximum_component_pixels-events_total:
                raise ValueError('Component pixel-event capacity exceeded')
            p = np.asarray(supplied_pixels)
            if (p.ndim != 1 or p.dtype.kind not in 'iu' or np.any(p < 0) or np.any(p >= pixels)
                    or np.any(p[1:] <= p[:-1])):
                raise ValueError('Component pixels must be sorted unique bounded integer vectors')
            events_total += len(p)
            component_pins.append(_hash(p))
            copy = p.astype(np.int64, copy=True); copy.setflags(write=False); arrays.append(copy)
        mask_copy, known_copy = mask.ravel().copy(), known.ravel().copy()
        mask_copy.setflags(write=False); known_copy.setflags(write=False)
        captured.append({'id': identifier, 'shape': shape, 'component_pixels': arrays,
                         'mask': mask_copy, 'known': known_copy, 'aperture_pixels': int(mask.sum())})
        records.append({'id': identifier, 'shape': list(shape), 'component_count': components,
                        'component_pixel_sha256': component_pins, 'mask_sha256': _hash(mask),
                        'known_domain_sha256': _hash(known), 'provenance': provenance})
    return captured, records, {'components': components, 'views': len(captured),
                              'image_pixels': pixels_total, 'component_pixels': events_total,
                              'packed_signature_bytes_upper_bound': packed_bytes}


def _measure(selected, views):
    ids = np.flatnonzero(selected)
    metrics = []
    for view in views:
        union = np.zeros(math.prod(view['shape']), bool)
        for component in ids:
            union[view['component_pixels'][component]] = True
        mask, known = view['mask'], view['known']
        tp = int(np.count_nonzero(union & mask))
        fp = int(np.count_nonzero(union & known & ~mask))
        aperture = view['aperture_pixels']; denominator = aperture+fp
        metrics.append({'view_id': view['id'], 'intersection_pixels': tp,
                        'false_positive_pixels': fp, 'false_negative_pixels': aperture-tp,
                        'aperture_pixels': aperture, 'union_pixels': denominator,
                        'iou': tp/denominator, 'known_domain_pixels': int(known.sum()),
                        'projected_known_pixels': int(np.count_nonzero(union & known)),
                        'projected_unknown_pixels': int(np.count_nonzero(union & ~known)),
                        'projected_pixels': int(union.sum())})
    values = [row['iou'] for row in metrics]
    return metrics, min(values), float(np.mean(values))


def _ambiguity(selected, views):
    count = len(selected)
    known_support = np.zeros(count, bool)
    addable, removable = ~selected.copy(), selected.copy()
    for view in views:
        coverage = np.zeros(math.prod(view['shape']), np.int32)
        for component in np.flatnonzero(selected):
            coverage[view['component_pixels'][component]] += 1
        known = view['known']
        for component, p in enumerate(view['component_pixels']):
            supported = known[p]
            known_support[component] |= bool(supported.any())
            if selected[component]:
                removable[component] &= not bool(np.any(supported & (coverage[p] == 1)))
            else:
                addable[component] &= not bool(np.any(supported & (coverage[p] == 0)))
    return {'zero_known_support_component_ids': np.flatnonzero(~known_support).tolist(),
            'addable_without_scored_union_change': np.flatnonzero(addable).tolist(),
            'individually_removable_without_scored_union_change': np.flatnonzero(removable).tolist()}


def _compress(views, components, limits, deadline):
    signatures, membership_count, zero_positive = [], 0, []
    for view_index, view in enumerate(views):
        if time.monotonic() >= deadline:
            return None
        packed = np.zeros((math.prod(view['shape']), (components+7)//8), np.uint8)
        for component, pixels in enumerate(view['component_pixels']):
            packed[pixels, component//8] |= np.uint8(1 << (component % 8))
        patterns, inverse, counts = np.unique(packed[view['known']], axis=0,
                                               return_inverse=True, return_counts=True)
        positives = np.bincount(inverse, weights=view['mask'][view['known']], minlength=len(patterns)).astype(np.int64)
        nonzero = np.any(patterns, axis=1)
        zero_positive.append(int(positives[~nonzero].sum()))
        if len(signatures)+int(nonzero.sum()) > limits.maximum_signatures:
            raise ValueError('Nonzero membership-signature capacity exceeded')
        for index in np.flatnonzero(nonzero):
            members = np.flatnonzero(np.unpackbits(patterns[index], bitorder='little')[:components]).astype(np.int64)
            membership_count += len(members)
            if membership_count > limits.maximum_signature_memberships:
                raise ValueError('Membership-signature incidence capacity exceeded')
            signatures.append({'view': view_index, 'members': members,
                               'positive': int(positives[index]), 'negative': int(counts[index]-positives[index])})
    count = len(signatures)
    nonzeros = 3*membership_count+2*count
    if nonzeros > limits.maximum_constraint_nonzeros:
        raise ValueError('Support MILP sparse nonzero capacity exceeded')
    return signatures, {'signatures': count, 'signature_memberships': membership_count,
                        'constraint_nonzeros_upper_bound': nonzeros,
                        'variables': components+count, 'constraints': membership_count+count+len(views),
                        'uncoverable_positive_pixels_by_view': zero_positive}


def _or_constraints(signatures, components):
    incidence = sum(len(s['members']) for s in signatures)
    rows_count = incidence+len(signatures)
    nnz = 3*incidence+len(signatures)
    rows, columns = np.empty(nnz, np.int64), np.empty(nnz, np.int64)
    values = np.empty(nnz, float)
    lower, upper = np.zeros(rows_count), np.full(rows_count, np.inf)
    row, offset = 0, 0
    for ordinal, signature in enumerate(signatures):
        y = components+ordinal
        for component in signature['members']:
            rows[offset:offset+2] = row
            columns[offset:offset+2] = [int(component), y]
            values[offset:offset+2] = [-1., 1.]
            row += 1; offset += 2
        n = len(signature['members'])
        rows[offset:offset+n+1] = row
        columns[offset:offset+n] = signature['members']; columns[offset+n] = y
        values[offset:offset+n] = -1.; values[offset+n] = 1.
        lower[row], upper[row] = -np.inf, 0.
        row += 1; offset += n+1
    matrix = sparse.coo_array((values, (rows, columns)), shape=(rows_count, components+len(signatures))).tocsc()
    return matrix, lower, upper


def _incumbent(result, variable_count, components):
    raw = getattr(result, 'x', None)
    if raw is None:
        return None, 'not_returned'
    values = np.asarray(raw)
    if values.shape != (variable_count,) or values.dtype.kind not in 'fiu' or not np.isfinite(values).all():
        return None, 'invalid_solution_vector'
    x = values[:components]
    rounded = np.rint(x)
    if (np.any(x < -_BINARY_TOLERANCE) or np.any(x > 1+_BINARY_TOLERANCE)
            or np.any(np.abs(x-rounded) > _BINARY_TOLERANCE)):
        return None, 'nonbinary_component_incumbent'
    return rounded.astype(bool), 'independently_reconstructed_binary_support'


def _solver_versions():
    try:
        from scipy.optimize._highspy._core import HIGHS_VERSION_MAJOR, HIGHS_VERSION_MINOR, HIGHS_VERSION_PATCH
        highs = f'{HIGHS_VERSION_MAJOR}.{HIGHS_VERSION_MINOR}.{HIGHS_VERSION_PATCH}'
    except (ImportError, AttributeError):
        highs = None
    return {'scipy': scipy.__version__, 'highs': highs}


def optimize_support(views, *, ratio_gap=0.001, time_limit=30.0,
                     maximum_iterations=20, limits=SupportLimits()):
    """Maximize minimum view IoU over every binary component inclusion set.

    Each input view has exactly id, shape, component_pixels, mask, known_domain,
    and provenance. Component ordinals are caller-bound across all views. A
    positive aperture is required in each of at least two distinct views.

    For trial ratio r, TP-r*FP >= r*A is a feasibility constraint in every
    view. Pixel patterns with the same component membership share an exact OR
    variable. No component count, group count, optical role, or hidden-area
    preference is added. Actual unions recheck every returned incumbent.

    Time is one scheduling budget across validation, compression, and all MILP
    calls; a running native operation is not preempted by Python. A timeout
    retains a measured incumbent and unresolved interval. Only HiGHS status 2
    narrows the conditional numerical upper bound. A numerical contradiction
    resets that upper bound to 1 and never clamps a better measured incumbent.
    """
    started = time.monotonic()
    _limits(limits)
    if isinstance(ratio_gap, bool) or not isinstance(ratio_gap, (int, float)) or not np.isfinite(ratio_gap) or not 0 < ratio_gap < 1:
        raise ValueError('Finite ratio_gap strictly between zero and one required')
    if isinstance(time_limit, bool) or not isinstance(time_limit, (int, float)) or not np.isfinite(time_limit) or time_limit <= 0:
        raise ValueError('Positive finite total time limit required')
    if type(maximum_iterations) is not int or maximum_iterations < 1:
        raise ValueError('Positive integer maximum_iterations required')
    deadline = started+float(time_limit)
    captured, input_records, resources = _views(views, limits)
    components = resources['components']
    observed = np.zeros(components, bool)
    for view in captured:
        for component, pixels in enumerate(view['component_pixels']):
            observed[component] |= bool(view['known'][pixels].any())
    best = np.zeros(components, bool)
    metrics, lower, mean = _measure(best, captured)
    seeds = [{'name': 'empty', 'minimum_iou': lower, 'mean_iou': mean}]
    # Canonicalize completely unobserved variables to zero without assigning a
    # non-optical label. They remain in the complete inventory and unknown list.
    all_selected = observed.copy()
    all_metrics, all_lower, all_mean = _measure(all_selected, captured)
    seeds.append({'name': 'all_components', 'minimum_iou': all_lower, 'mean_iou': all_mean})
    if all_lower > lower:
        best, metrics, lower, mean = all_selected, all_metrics, all_lower, all_mean
    upper, trials, status = 1., [], None
    if upper-lower <= ratio_gap:
        status = 'conditional_gap_resolved'
    elif time.monotonic() >= deadline:
        status = 'time_limit'
    compiled = None if status else _compress(captured, components, limits, deadline)
    if not status and compiled is None:
        status = 'time_limit'
    if not status:
        signatures, compression = compiled
        resources.update(compression)
        if time.monotonic() >= deadline:
            status = 'time_limit'
    if not status:
        static, static_lower, static_upper = _or_constraints(signatures, components)
        variable_count = components+len(signatures)
        objective = np.zeros(variable_count)
        integrality = np.r_[np.ones(components, np.uint8), np.zeros(len(signatures), np.uint8)]
        bounds = Bounds(np.zeros(variable_count), np.ones(variable_count))
        signature_views = np.asarray([s['view'] for s in signatures], np.int64)
        signature_variables = components+np.arange(len(signatures), dtype=np.int64)
        positive = np.asarray([s['positive'] for s in signatures], float)
        negative = np.asarray([s['negative'] for s in signatures], float)
        aperture_counts = np.asarray([v['aperture_pixels'] for v in captured], float)
        for iteration in range(maximum_iterations):
            if upper-lower <= ratio_gap:
                status = 'conditional_gap_resolved'; break
            remaining = deadline-time.monotonic()
            if remaining <= 0:
                status = 'time_limit'; break
            ratio = (lower+upper)/2
            ratio_matrix = sparse.coo_array((positive-ratio*negative, (signature_views, signature_variables)),
                                           shape=(len(captured), variable_count)).tocsc()
            matrix = sparse.vstack((static, ratio_matrix), format='csc')
            constraint = LinearConstraint(matrix, np.r_[static_lower, ratio*aperture_counts],
                                          np.r_[static_upper, np.full(len(captured), np.inf)])
            remaining = deadline-time.monotonic()
            if remaining <= 0:
                status = 'time_limit'; break
            trial_start = time.monotonic()
            trial = {'iteration': iteration, 'trial_ratio': ratio, 'lower_before': lower,
                     'upper_before': upper, 'time_budget_seconds': remaining}
            try:
                result = milp(objective, integrality=integrality, bounds=bounds, constraints=constraint,
                              options={'time_limit': remaining, 'presolve': True, 'mip_rel_gap': 0., 'disp': False})
            except Exception as error:
                trial.update(solver_status=None, solver_status_name='exception',
                             message=f'{type(error).__name__}: {error}', seconds=time.monotonic()-trial_start,
                             lower_after=lower, upper_after=upper, upper_bound_update='none')
                trials.append(trial); status = 'solver_failure'; break
            solver_status = getattr(result, 'status', None)
            solver_status = int(solver_status) if isinstance(solver_status, (int, np.integer)) else None
            trial.update(solver_status=solver_status, solver_status_name=_STATUSES.get(solver_status, 'unknown'),
                         message=str(getattr(result, 'message', '')), seconds=time.monotonic()-trial_start)
            nodes = getattr(result, 'mip_node_count', None)
            trial['mip_node_count'] = int(nodes) if isinstance(nodes, (int, np.integer)) and nodes >= 0 else None
            # mip_gap and mip_dual_bound concern the zero objective, not IoU.
            candidate, candidate_status = _incumbent(result, variable_count, components)
            trial['incumbent_status'] = candidate_status
            actual = None
            if candidate is not None:
                candidate &= observed
                candidate_metrics, actual, candidate_mean = _measure(candidate, captured)
                trial.update(incumbent_minimum_iou=actual, incumbent_mean_iou=candidate_mean,
                             incumbent_selected_component_ids=np.flatnonzero(candidate).tolist(),
                             independently_meets_trial_ratio=actual >= ratio)
                if actual > lower:
                    best, metrics, lower, mean = candidate, candidate_metrics, actual, candidate_mean
            contradictory_upper = lower > upper
            contradictory_infeasibility = solver_status == 2 and actual is not None and actual >= ratio
            if contradictory_upper or contradictory_infeasibility:
                upper = 1.
                trial['upper_bound_update'] = 'reset_to_one_after_numerical_contradiction'
                status = 'numerical_inconsistency'
            elif solver_status == 2:
                upper = ratio
                trial['upper_bound_update'] = 'conditional_on_solver_infeasibility_status'
            else:
                trial['upper_bound_update'] = 'none'
                if solver_status == 0 and (actual is None or actual < ratio):
                    status = 'numerical_inconsistency'
                elif solver_status == 1 and (actual is None or actual < ratio):
                    status = 'time_limit' if time.monotonic() >= deadline else 'solver_limit'
                elif solver_status not in (0, 1):
                    status = 'solver_failure'
            trial.update(lower_after=lower, upper_after=upper)
            trials.append(trial)
            if status:
                break
        if status is None:
            status = ('conditional_gap_resolved' if upper-lower <= ratio_gap else
                      'time_limit' if time.monotonic() >= deadline else 'iteration_limit')
    ambiguity = _ambiguity(best, captured)
    report = {'schema_version': 1, 'method': 'shared_component_support_union_milp_v1',
              'status': status, 'selected_component_ids': np.flatnonzero(best).tolist(),
              'metrics': metrics, 'minimum_iou': lower, 'mean_iou': mean,
              'upper_bound': upper, 'remaining_gap': max(0., upper-lower),
              'ratio_gap_requested': float(ratio_gap), 'time_limit_seconds': float(time_limit),
              'maximum_iterations': maximum_iterations, 'solver_trials': trials, 'seeds': seeds,
              **ambiguity, 'input_views': input_records, 'resources': resources, 'limits': asdict(limits),
              'solver': {'name': 'scipy.optimize.milp / HiGHS', **_solver_versions(),
                         'objective': 'zero; ratio feasibility only', 'integrality': 'component variables binary; OR variables continuous in [0,1]',
                         'incumbent_binary_tolerance': _BINARY_TOLERANCE,
                         'status_codes': {str(k): v for k, v in _STATUSES.items()}},
              'bound_scope': 'Conditional floating-point HiGHS feasibility interval, not a rigorous certificate or semantic confidence.',
              'tie_policy': 'Keep first independently measured incumbent at equal minimum IoU; no component-count or mean-IoU preference.',
              'unobserved_variable_policy': 'Zero-known-support variables are canonically excluded from selected support but remain unknown, not classified non-optical.',
              'ambiguity_scope': 'Single additions/removals preserve scored unions only; individually removable does not imply jointly removable or enumerate every optimum.',
              'accepted': False, 'quality_verdict': 'unmeasured', 'physical_groups': 'not_inferred',
              'semantic_identity': 'not_inferred', 'elapsed_seconds': time.monotonic()-started,
              'limitations': ['Each view is one caller-declared aperture interpretation; correlated alternatives are not independent evidence.',
                  'Support IDs refer to the caller-supplied stable component order and provenance; this core does not read source models or images.',
                  'No known-domain support leaves membership unidentifiable; unknown pixels do not count as negatives.',
                  'Footprints are fixed inputs; selected support does not alter their visibility or model optical composition.',
                  'Equivalent unions can represent separate lenses, a shield, duplicated shells, clip-ons, pads or hidden geometry.',
                  'This objective does not infer physical groups, articulation, calibrated cameras, optical materials or reconstruction acceptance.',
                  'Time budgets prevent additional work and bound solver calls; Python cannot preempt a running native operation.',
                  'Numerical solver infeasibility narrows only a conditional bound; time, iteration, or numerical failures never prove infeasibility.']}
    _json(report)
    return report
