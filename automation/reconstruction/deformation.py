"""Bounded shared 3D shape refinement from frozen multiview correspondences.

This is a geometric operation, not automatic correspondence extraction. Cameras,
image observations and original 3D surface points stay fixed while one continuous
trilinear displacement field moves every component. A conservative Lipschitz
bound below one makes the continuous field injective on its rectangular domain.
Coincident sample points remain coincident. Finite triangles/edges approximate
that field and need separate export checks: unequal tessellation can open gaps
and long thin triangles can invert even when the continuous field is injective.

This preserves existing topology; it cannot create a missing bridge or temple.
In-sample improvement is never called reconstruction-quality acceptance.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math

import numpy as np
from scipy.optimize import least_squares

from .camera import Camera, project


def _finite(value, shape, name):
    raw = np.asarray(value)
    if raw.dtype.kind not in 'fiu':
        raise ValueError(f'{name} must be numeric')
    array = raw.astype(float, copy=True)
    if array.shape != shape or not np.isfinite(array).all():
        raise ValueError(f'{name} must have finite shape {shape}')
    array.setflags(write=False)
    return array


def projection_jacobian(vertices: np.ndarray, camera: Camera) -> np.ndarray:
    """d(pixel xy)/d(world xyz), matching camera.project including perspective."""
    yaw, pitch, roll = np.radians([camera.yaw, camera.pitch, camera.roll])
    cy, sy, cp, sp = math.cos(yaw), math.sin(yaw), math.cos(pitch), math.sin(pitch)
    right = np.array([cy, 0, -sy])
    up = np.array([-sp * sy, cp, -sp * cy])
    toward = np.array([cp * sy, sp, cp * cy])
    cr, sr = math.cos(roll), math.sin(roll)
    axes = np.stack((cr * right - sr * up, -sr * right - cr * up))
    denominator = 1 - camera.perspective * (vertices @ toward)
    if np.any(denominator <= .05):
        raise ValueError('Geometry crosses the camera near plane')
    numerator = vertices @ axes.T
    return camera.scale * (axes[None] / denominator[:, None, None] +
        camera.perspective * numerator[:, :, None] * toward[None, None] / denominator[:, None, None] ** 2)


@dataclass(frozen=True)
class ReprojectionConstraint:
    """Frozen point observations or normal-only contour observations.

    ``normal_xy`` contains source-image unit normals fixed before fitting. It
    removes the unobserved tangential coordinate from both loss and reporting;
    ``sigma_xy`` then propagates to the normal assuming independent xy errors.
    """
    view_id: str
    source_sha256: str
    evidence_sha256: str
    camera: Camera
    points_xyz: np.ndarray
    targets_xy: np.ndarray
    sigma_xy: np.ndarray
    point_group_ids: tuple[str, ...] | None = None
    normal_xy: np.ndarray | None = None

    def __post_init__(self):
        if not isinstance(self.view_id, str) or not self.view_id.strip():
            raise ValueError('A view ID is required')
        for name in ('source_sha256', 'evidence_sha256'):
            value = getattr(self, name)
            if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
                raise ValueError(f'{name} must be a lowercase SHA-256')
        n = len(self.points_xyz)
        if n < 1:
            raise ValueError('A constraint must contain observed points')
        for name, shape in (('points_xyz', (n, 3)), ('targets_xy', (n, 2)), ('sigma_xy', (n, 2))):
            object.__setattr__(self, name, _finite(getattr(self, name), shape, name))
        if np.any(self.sigma_xy <= 0):
            raise ValueError('Coordinate uncertainty must be positive in pixels')
        if (not isinstance(self.camera, Camera) or not np.isfinite(list(self.camera.to_dict().values())).all()
                or self.camera.scale <= 0 or self.camera.perspective < 0):
            raise ValueError('A finite positive-scale camera is required')
        if self.point_group_ids is not None:
            if not isinstance(self.point_group_ids, (tuple, list, np.ndarray)):
                raise ValueError('Point group IDs must be a sequence, not a single string')
            labels = tuple(self.point_group_ids)
            if len(labels) != n or any(not isinstance(label, str) or not label.strip() for label in labels):
                raise ValueError('Each point requires one nonblank group ID')
            object.__setattr__(self, 'point_group_ids', labels)
        if self.normal_xy is not None:
            normals = _finite(self.normal_xy, (n, 2), 'normal_xy')
            if not np.allclose(np.linalg.norm(normals, axis=1), 1., rtol=0, atol=1e-6):
                raise ValueError('Contour normal_xy vectors must have unit length')
            object.__setattr__(self, 'normal_xy', normals)


@dataclass(frozen=True)
class CageField:
    lower: np.ndarray
    upper: np.ndarray
    displacements: np.ndarray  # nx, ny, nz, xyz

    def __post_init__(self):
        object.__setattr__(self, 'lower', _finite(self.lower, (3,), 'lower'))
        object.__setattr__(self, 'upper', _finite(self.upper, (3,), 'upper'))
        raw = np.asarray(self.displacements)
        if raw.ndim != 4 or raw.shape[-1] != 3 or min(raw.shape[:3]) < 2 or max(raw.shape[:3]) > 12:
            raise ValueError('A cage needs 2 to 12 nodes per axis and xyz displacements')
        object.__setattr__(self, 'displacements', _finite(raw, raw.shape, 'displacements'))
        if np.any(self.upper <= self.lower):
            raise ValueError('Cage bounds must have positive extent on every axis')

    @property
    def shape(self):
        return self.displacements.shape[:3]

    @property
    def spacing(self):
        return (self.upper - self.lower) / (np.asarray(self.shape) - 1)

    def basis(self, points):
        points = _finite(points, (len(points), 3), 'points')
        coordinate = (points - self.lower) / self.spacing
        if np.any(coordinate < -1e-9) or np.any(coordinate > np.asarray(self.shape) - 1 + 1e-9):
            raise ValueError('Points outside the deformation domain are unsupported')
        # Only round-off at the exact domain boundary is clipped.
        coordinate = np.clip(coordinate, 0, np.asarray(self.shape) - 1)
        cell = np.minimum(np.floor(coordinate).astype(int), np.asarray(self.shape) - 2)
        local = coordinate - cell
        weights = np.zeros((len(points), np.prod(self.shape)))
        gradients = np.zeros((len(points), np.prod(self.shape), 3))
        for dx in (0, 1):
            for dy in (0, 1):
                for dz in (0, 1):
                    corner = np.array([dx, dy, dz])
                    index = np.ravel_multi_index((cell + corner).T, self.shape)
                    factors = np.where(corner, local, 1 - local)
                    weights[np.arange(len(points)), index] = factors.prod(axis=1)
                    for axis in range(3):
                        other = [j for j in range(3) if j != axis]
                        derivative = (1 if corner[axis] else -1) * factors[:, other].prod(axis=1) / self.spacing[axis]
                        gradients[np.arange(len(points)), index, axis] = derivative
        return weights, gradients

    def transform(self, points):
        """Return deformed coordinates and d(deformed)/d(original) for GLB export."""
        points = np.asarray(points, dtype=float)
        if len(points) > 8192:
            values = [self.transform(points[start:start + 8192]) for start in range(0, len(points), 8192)]
            return np.concatenate([value[0] for value in values]), np.concatenate([value[1] for value in values])
        weights, gradients = self.basis(points)
        offsets = self.displacements.reshape(-1, 3)
        return points + weights @ offsets, np.eye(3)[None] + np.einsum('nki,kj->nji', gradients, offsets)

    def gradient_bound(self):
        """Global operator-norm upper bound, not a finite sampling of determinants.

        Each derivative column is a convex combination of parallel edge
        differences inside a cell. Combining their norm maxima bounds the
        Frobenius norm everywhere, hence the displacement's Lipschitz constant.
        """
        maxima = [np.max(np.linalg.norm(np.diff(self.displacements, axis=axis) / self.spacing[axis], axis=-1))
                  for axis in range(3)]
        return float(np.linalg.norm(maxima))

    def to_dict(self):
        return {'schema_version': 1, 'interpolation': 'trilinear_displacement',
                'lower': self.lower.tolist(), 'upper': self.upper.tolist(),
                'displacements': self.displacements.tolist(), 'gradient_bound': self.gradient_bound()}


@dataclass(frozen=True)
class NormalizedField:
    """Explicit adapter from normalized fitting coordinates to source GLB units."""
    field: CageField
    center: np.ndarray
    extent: float
    source_sha256: str

    def __post_init__(self):
        object.__setattr__(self, 'center', _finite(self.center, (3,), 'normalization center'))
        if not isinstance(self.field, CageField) or not np.isfinite(self.extent) or self.extent <= 0:
            raise ValueError('A field and positive source-space normalization extent are required')
        if not isinstance(self.source_sha256, str) or len(self.source_sha256) != 64 or any(c not in '0123456789abcdef' for c in self.source_sha256):
            raise ValueError('The source GLB SHA-256 must be pinned')

    def transform(self, world_points):
        moved, derivative = self.field.transform((np.asarray(world_points) - self.center) / self.extent)
        return self.center + self.extent * moved, derivative

    def to_dict(self):
        return {'schema_version': 1, 'coordinate_system': 'source_GLB_world_units',
                'source_sha256': self.source_sha256, 'center': self.center.tolist(),
                'extent': float(self.extent), 'normalized_field': self.field.to_dict()}


def _constraint_sigma(item):
    if item.normal_xy is None:
        return item.sigma_xy
    return np.sqrt(np.sum((item.normal_xy * item.sigma_xy) ** 2, axis=1))[:, None]


def _reprojection_residual(item, points):
    """Pixel and standardized residuals in the observation's measured subspace."""
    difference = project(points, item.camera) - item.targets_xy
    if item.normal_xy is not None:
        difference = np.sum(difference * item.normal_xy, axis=1)[:, None]
    return difference, difference / _constraint_sigma(item)


def _reprojection_jacobian(item, points):
    """d(standardized measured residual)/d(world xyz)."""
    derivative = projection_jacobian(points, item.camera)
    if item.normal_xy is not None:
        derivative = np.einsum('na,nai->ni', item.normal_xy, derivative)[:, None, :]
    return derivative / _constraint_sigma(item)[:, :, None]


def _constraint_errors(field, item):
    difference, standardized = _reprojection_residual(item, field.transform(item.points_xyz)[0])
    return np.linalg.norm(difference, axis=1), np.linalg.norm(standardized, axis=1)


def _error_summary(radial, standardized):
    return {'point_count': len(radial), 'mean_px': float(radial.mean()),
            'p95_px': float(np.quantile(radial, .95)), 'maximum_px': float(radial.max()),
            'rms_sigma': float(np.sqrt(np.mean(standardized ** 2))),
            'maximum_sigma': float(standardized.max())}


def measure_constraints(field, constraints):
    results = []
    for item in constraints:
        radial, standardized = _constraint_errors(field, item)
        groups = None
        if item.point_group_ids is not None:
            labels = np.asarray(item.point_group_ids)
            groups = [{'group_id': label, **_error_summary(radial[labels == label], standardized[labels == label])}
                      for label in sorted(set(item.point_group_ids))]
        results.append({'view_id': item.view_id, 'source_sha256': item.source_sha256,
                        'evidence_sha256': item.evidence_sha256, **_error_summary(radial, standardized),
                        'residual_mode': 'normal_only' if item.normal_xy is not None else 'point_xy',
                        'residual_dimensions': 1 if item.normal_xy is not None else 2,
                        'groups': groups})
    return results


def _similarity_gauge(base, reference):
    """Nullspace of seven moments on supplied actual-geometry samples.

    Weights must describe a fixed source-space sampling measure (e.g. surface
    area or curve arc length), NOT the cage's empty volume or candidate-dependent
    tessellation. The caller establishes that provenance. Constraints remove the
    weighted linear translation, rotation and uniform-scale displacement modes;
    they are not measured pose/scale anchors or proof of geometric identifiability.
    """
    if not isinstance(reference, (tuple, list)) or len(reference) != 2:
        raise ValueError('gauge_reference must be (source points, positive sample weights)')
    raw_points = np.asarray(reference[0])
    if raw_points.ndim != 2 or raw_points.shape[1] != 3 or not len(raw_points):
        raise ValueError('Gauge source points must be a nonempty Nx3 array')
    points = _finite(raw_points, raw_points.shape, 'gauge source points')
    raw_weights = _finite(reference[1], (len(points),), 'gauge sample weights')
    if np.any(raw_weights <= 0):
        raise ValueError('Gauge sample weights must be strictly positive')
    # Normalize without overflowing when all supplied positive weights are large.
    mass = raw_weights / raw_weights.max()
    mass = mass / mass.sum()
    if not np.isfinite(mass).all() or np.any(mass <= 0):
        raise ValueError('Gauge weight ratios are not representable as positive normalized weights')
    center = np.sum(mass[:, None] * points, axis=0)
    centered = points - center
    radius = float(np.sqrt(np.sum(mass * np.sum(centered ** 2, axis=1))))
    if not np.isfinite(radius) or radius <= 0:
        raise ValueError('Gauge source geometry must have finite nonzero spatial extent')
    coordinates = centered / radius
    basis = base.basis(points)[0]
    average_basis = mass @ basis
    coordinate_basis = (mass[:, None] * coordinates).T @ basis
    matrix = np.zeros((7, int(np.prod(base.shape)), 3))
    for axis in range(3):
        matrix[axis, :, axis] = average_basis
        matrix[6, :, axis] = coordinate_basis[axis]
    # Cross(centered / radius, displacement), in xyz order.
    matrix[3, :, 2], matrix[3, :, 1] = coordinate_basis[1], -coordinate_basis[2]
    matrix[4, :, 0], matrix[4, :, 2] = coordinate_basis[2], -coordinate_basis[0]
    matrix[5, :, 1], matrix[5, :, 0] = coordinate_basis[0], -coordinate_basis[1]
    matrix = matrix.reshape(7, -1)
    _, singular, vt = np.linalg.svd(matrix, full_matrices=True)
    relative_tolerance = 1e-10
    threshold = float(singular[0]) * relative_tolerance
    rank = int(np.sum(singular > threshold))
    if rank != 7:
        raise ValueError('Gauge geometry cannot stably constrain all seven similarity moments (rank below seven)')
    nullspace = vt[rank:].T
    digest = hashlib.sha256()
    digest.update(json.dumps({'point_count': len(points), 'coordinates': 'same as cage'}).encode())
    digest.update(np.asarray(points, dtype='<f8').tobytes())
    digest.update(np.asarray(raw_weights, dtype='<f8').tobytes())
    metadata = {'rank': rank, 'condition_number': float(singular[0] / singular[-1]),
                'singular_values': singular.tolist(), 'rank_relative_tolerance': relative_tolerance,
                'reference_sha256': digest.hexdigest(), 'point_count': len(points),
                'weighted_center': center.tolist(), 'rms_radius_units': radius,
                'reduced_parameter_count': nullspace.shape[1],
                'scope': 'weighted linear similarity moments on caller-supplied fixed actual source geometry',
                'reference_sampling_verified': False,
                'moment_order': ['translation_x', 'translation_y', 'translation_z',
                                 'rotation_x_over_radius', 'rotation_y_over_radius', 'rotation_z_over_radius',
                                 'scale_over_radius'],
                'moment_units': 'cage coordinate units; rotation and scale divided by reference rms radius'}
    return nullspace, matrix, metadata


def _group_nonregression(base, proposed, constraints, scope):
    """Frozen point pairs prevent an old bad point hiding a new local regression."""
    checks = []
    for item in constraints:
        if item.point_group_ids is None:
            continue
        before = _constraint_errors(base, item)[1]
        after = _constraint_errors(proposed, item)[1]
        labels = np.asarray(item.point_group_ids)
        for label in sorted(set(item.point_group_ids)):
            selected = labels == label
            rms_before = float(np.sqrt(np.mean(before[selected] ** 2)))
            rms_after = float(np.sqrt(np.mean(after[selected] ** 2)))
            worst_increase = float(np.max(after[selected] - before[selected]))
            checks.append({'scope': scope, 'view_id': item.view_id, 'group_id': label,
                           'residual_mode': 'normal_only' if item.normal_xy is not None else 'point_xy',
                           'point_count': int(selected.sum()), 'rms_sigma_before': rms_before,
                           'rms_sigma_proposal': rms_after, 'maximum_pointwise_increase_sigma': worst_increase,
                           'rms_nonregressed': rms_after <= rms_before + 1e-6,
                           'worst_increase_within_policy': worst_increase <= 1.0 + 1e-6})
    return checks


def _validated_constraints(constraints, held_out):
    constraints, held_out = tuple(constraints), tuple(held_out)
    if not constraints or any(not isinstance(c, ReprojectionConstraint) for c in constraints + held_out):
        raise ValueError('Reprojection constraints are required')
    if len({c.view_id for c in constraints + held_out}) != len(constraints + held_out):
        raise ValueError('A view may appear only once; combine its correspondence points')
    if {c.source_sha256 for c in constraints} & {c.source_sha256 for c in held_out}:
        raise ValueError('A fitted source image cannot also be selection-validation evidence')
    return constraints, held_out


def assess_proposal(base, proposed, constraints, held_out=()):
    """Reevaluate one concrete field with the shared frozen-evidence policy.

    Both fields and observations must use the same source coordinate system.
    Every trial, including a scaled backtracking candidate, needs this assessment:
    decisions from an earlier field do not transfer. This checks reprojection
    retention only; it does not establish export validity or appearance quality.
    ``held_out`` participates in selection and is not an untouched final test.
    """
    constraints, held_out = _validated_constraints(constraints, held_out)
    before = measure_constraints(base, constraints)
    after = measure_constraints(proposed, constraints)
    before_held, after_held = measure_constraints(base, held_out), measure_constraints(proposed, held_out)
    improved = sum(c['rms_sigma'] ** 2 for c in after) < sum(c['rms_sigma'] ** 2 for c in before) - 1e-8
    per_view_nonregression = all(a['rms_sigma'] <= b['rms_sigma'] + 1e-6 for a, b in zip(after, before))
    held_nonregression = all(a['rms_sigma'] <= b['rms_sigma'] + 1e-6 for a, b in zip(after_held, before_held))
    group_checks = (_group_nonregression(base, proposed, constraints, 'fit') +
                    _group_nonregression(base, proposed, held_out, 'selection_validation'))
    groups_nonregressed = all(item['rms_nonregressed'] and item['worst_increase_within_policy'] for item in group_checks)
    retained = improved and per_view_nonregression and held_nonregression and groups_nonregressed
    return {'quality_verdict': 'unmeasured', 'retained': retained,
            'fit_before': before, 'fit_proposal': after,
            'held_out_before': before_held, 'held_out_proposal': after_held,
            'fit_improved': improved, 'every_fit_view_nonregressed': per_view_nonregression,
            'held_out_present': bool(held_out), 'every_held_out_view_nonregressed': held_nonregression if held_out else None,
            'selection_validation': bool(held_out),
            'held_out_interpretation': 'selection validation participates in retention; not an untouched final test',
            'point_group_checks': group_checks, 'every_labeled_group_nonregressed': groups_nonregressed if group_checks else None,
            'group_retention_policy': {'rms_increase_limit_sigma': 0.0,
                                       'maximum_pointwise_increase_sigma': 1.0, 'numerical_tolerance_sigma': 1e-6},
            'ungrouped_view_ids': [item.view_id for item in constraints + held_out if item.point_group_ids is None]}


def _bounded_nullspace(nullspace, vector, maximum_norm):
    """Map reduced coordinates into the open control-vector norm ball.

    Unlike a coordinate box on an arbitrary SVD basis, this radial map spans
    every allowed interior field. Its scalar factor preserves the nullspace.
    The maximum has piecewise derivatives; at a tied maximum the first active
    control supplies a valid branch derivative. The map is differentiable at
    zero with derivative ``nullspace``.
    """
    raw = nullspace @ vector
    controls = raw.reshape(-1, 3)
    norms = np.hypot.reduce(controls, axis=1)
    active = int(np.argmax(norms))
    maximum = float(norms[active])
    denominator = math.hypot(maximum_norm, maximum)
    scale = maximum_norm / denominator
    bounded = raw * scale
    derivative = scale * nullspace
    if maximum:
        active_derivative = (controls[active] / maximum) @ nullspace[3 * active:3 * active + 3]
        derivative -= np.outer(bounded / maximum_norm, active_derivative) * scale * (maximum / denominator)
    return bounded, derivative


def _camera_safe_control_norm(constraints, desired):
    """A conservative trial radius keeps every observed point projectable."""
    maximum = desired
    for item in constraints:
        camera = item.camera
        if not camera.perspective:
            continue
        yaw, pitch = np.radians([camera.yaw, camera.pitch])
        toward = np.array([math.cos(pitch) * math.sin(yaw), math.sin(pitch),
                           math.cos(pitch) * math.cos(yaw)])
        clearance = float(np.min(1 - camera.perspective * (item.points_xyz @ toward)) - .05)
        if clearance <= 0:
            raise ValueError('Original geometry crosses the camera near plane')
        # Convex cage interpolation cannot exceed the largest control norm.
        # Leave half the initial clearance to avoid a round-off boundary case.
        maximum = min(maximum, .5 * clearance / camera.perspective)
    return maximum


def fit_cage(constraints, lower, upper, *, held_out=(), shape=(4, 3, 3),
             maximum_displacement_fraction=.05, gradient_limit=.45,
             displacement_prior=.03, smoothness_prior=.05, max_evaluations=80,
             gauge_reference=None):
    """Propose one shared field; never change cameras, masks or evidence to fit it.

    Observations must be externally established correspondences. Loss gives each
    view equal total weight while respecting coordinate uncertainty. A returned
    proposal still requires independent image/geometry/material validation.

    Optional ``gauge_reference=(points_xyz, positive_weights)`` pins seven linear
    weighted similarity moments on fixed ACTUAL source-geometry samples. With a
    gauge, optimization occurs inside its nullspace, not by post-projecting a
    fitted field. A radial map inside the objective spans the control-norm trust
    ball and preserves the nullspace; final scalar gradient scaling does too.
    Near-plane clearance may conservatively tighten this trial ball, as reported.
    Without it, pose/scale-like displacements remain allowed and are reported.

    Labeled point groups require nonincreasing group RMS and at most one sigma
    increase at any individual frozen point. This is a retention policy, not an
    appearance threshold. ``held_out`` is selection validation used for retention;
    it is not an untouched test set, especially after repeated proposals.
    """
    constraints, held_out = _validated_constraints(constraints, held_out)
    for value in (maximum_displacement_fraction, gradient_limit, displacement_prior, smoothness_prior):
        if not np.isfinite(value) or value <= 0:
            raise ValueError('Refinement limits and regularization must be finite and positive')
    if gradient_limit >= 1 or maximum_displacement_fraction > .25 or type(max_evaluations) is not int or max_evaluations < 1:
        raise ValueError('Use a conservative injective bound and a finite optimization budget')
    if len(shape) != 3 or any(type(n) is not int or not 2 <= n <= 8 for n in shape):
        raise ValueError('Fitting requires 2 to 8 cage nodes per axis')
    base = CageField(lower, upper, np.zeros((*shape, 3)))
    count = int(np.prod(shape))
    max_delta = maximum_displacement_fraction * np.max(base.upper - base.lower)
    nullspace = gauge_matrix = gauge_metadata = None
    if gauge_reference is not None:
        nullspace, gauge_matrix, gauge_metadata = _similarity_gauge(base, gauge_reference)
    weights = [base.basis(c.points_xyz)[0] for c in constraints]
    for item in held_out:
        base.basis(item.points_xyz)
    # Constant regularizers constrain displacement and neighboring derivatives.
    rows = [np.eye(3 * count) * math.sqrt(displacement_prior / (3 * count)) / max_delta]
    links = []
    for index in np.ndindex(shape):
        for axis in range(3):
            if index[axis] == shape[axis] - 1:
                continue
            adjacent = list(index)
            adjacent[axis] += 1
            a, b = np.ravel_multi_index(index, shape), np.ravel_multi_index(tuple(adjacent), shape)
            for xyz in range(3):
                row = np.zeros(3 * count)
                row[3 * a + xyz], row[3 * b + xyz] = -1 / base.spacing[axis], 1 / base.spacing[axis]
                links.append(row)
    rows.append(np.asarray(links) * math.sqrt(smoothness_prior / len(links)) / gradient_limit)
    regularizer = np.concatenate(rows)

    def residual(vector):
        displacement = vector.reshape(-1, 3)
        values = [(_reprojection_residual(c, c.points_xyz + w @ displacement)[1] /
                   math.sqrt(len(c.points_xyz))).ravel() for c, w in zip(constraints, weights)]
        return np.concatenate(values + [regularizer @ vector])

    def jacobian(vector):
        displacement = vector.reshape(-1, 3)
        values = []
        for c, w in zip(constraints, weights):
            j = _reprojection_jacobian(c, c.points_xyz + w @ displacement) / math.sqrt(len(c.points_xyz))
            values.append(np.einsum('nai,nk->naki', j, w).reshape(-1, 3 * count))
        return np.concatenate(values + [regularizer])

    if nullspace is None:
        fitted = least_squares(residual, np.zeros(3 * count), jac=jacobian,
                               bounds=(-max_delta, max_delta), max_nfev=max_evaluations,
                               ftol=1e-7, xtol=1e-7, gtol=1e-7)
        fitted_vector = fitted.x
    else:
        trial_bound = _camera_safe_control_norm(constraints + held_out, max_delta)
        gauge_metadata['trial_maximum_control_displacement_units'] = float(trial_bound)
        gauge_metadata['camera_clearance_limited'] = bool(trial_bound < max_delta)
        gauge_metadata['optimization_trust_region'] = 'radial control-norm bound inside reduced-coordinate objective; final gradient scaling preserves gauge'

        def reduced_residual(vector):
            bounded, _ = _bounded_nullspace(nullspace, vector, trial_bound)
            return residual(bounded)

        def reduced_jacobian(vector):
            bounded, derivative = _bounded_nullspace(nullspace, vector, trial_bound)
            return jacobian(bounded) @ derivative

        fitted = least_squares(reduced_residual, np.zeros(nullspace.shape[1]), jac=reduced_jacobian,
                               max_nfev=max_evaluations,
                               ftol=1e-7, xtol=1e-7, gtol=1e-7)
        fitted_vector = _bounded_nullspace(nullspace, fitted.x, trial_bound)[0]
    proposed = CageField(lower, upper, fitted_vector.reshape(*shape, 3))
    raw_bound = proposed.gradient_bound()
    factor = min(1., gradient_limit / raw_bound) if raw_bound else 1.
    raw_maximum_offset = float(np.max(np.linalg.norm(proposed.displacements, axis=-1)))
    if raw_maximum_offset:
        factor = min(factor, max_delta / raw_maximum_offset)
    # Explicitly reported trust scaling; never silently export a folding field.
    proposed = CageField(lower, upper, proposed.displacements * factor)
    assessment = assess_proposal(base, proposed, constraints, held_out)
    retained = assessment['retained']
    returned = proposed if retained else base
    # Local data information excludes the prior and optimizer trust-map rows.
    # Otherwise a strong regularizer would falsely make unobserved shape modes
    # appear measured. Rank remains conditional on the frozen correspondences.
    data_jacobian = jacobian(returned.displacements.ravel())[:-len(regularizer)]
    if nullspace is not None:
        data_jacobian = data_jacobian @ nullspace
    data_singular = np.linalg.svd(data_jacobian, compute_uv=False)
    data_rank_tolerance = 1e-10
    data_rank = int(np.sum(data_singular > data_singular[0] * data_rank_tolerance))
    data_information = {'evaluated_at': 'returned_field', 'scope': 'local frozen-correspondence reprojection sensitivity only',
                        'parameter_space': 'gauge_nullspace' if nullspace is not None else 'full_cage',
                        'rank': data_rank, 'parameter_count': data_jacobian.shape[1],
                        'residual_count': data_jacobian.shape[0], 'singular_values': data_singular.tolist(),
                        'relative_rank_tolerance': data_rank_tolerance,
                        'full_column_rank': data_rank == data_jacobian.shape[1],
                        'regularization_rows_included': False, 'optimizer_trust_mapping_included': False,
                        'global_identifiability_established': False}
    if gauge_metadata is not None:
        proposal_moments = gauge_matrix @ proposed.displacements.ravel()
        returned_moments = gauge_matrix @ returned.displacements.ravel()
        gauge_metadata.update(proposal_moment_residuals=proposal_moments.tolist(),
                              returned_moment_residuals=returned_moments.tolist(),
                              proposal_maximum_absolute_moment=float(np.max(np.abs(proposal_moments))),
                              returned_maximum_absolute_moment=float(np.max(np.abs(returned_moments))))
    report = {'schema_version': 1, 'status': 'proposal' if retained else 'unchanged',
              **assessment,
              'optimizer_converged': bool(fitted.success), 'optimizer_message': str(fitted.message),
              'evaluations': int(fitted.nfev), 'raw_gradient_bound': raw_bound,
              'trust_scale': factor, 'gradient_bound': returned.gradient_bound(),
              'gradient_limit': gradient_limit, 'maximum_control_displacement_units': max_delta,
              'actual_maximum_control_displacement_units': float(np.max(np.linalg.norm(returned.displacements, axis=-1))),
              'injectivity_scope': 'continuous field only; finite exported triangles require separate validation',
              'pose_similarity_constrained': gauge_reference is not None, 'similarity_gauge': gauge_metadata,
              'data_only_local_information': data_information,
              'cameras_optimized': False, 'correspondence_selection': 'supplied_frozen',
              'field_sha256': hashlib.sha256(json.dumps(returned.to_dict(), sort_keys=True).encode()).hexdigest(),
              'limitations': ['Topology and initially missing components are unchanged.',
                              'Camera and correspondence accuracy must be established externally.',
                              'Retained proposal is not final reconstruction acceptance.'] +
                              (['Some views have no group labels; component/localized nonregression is not established there.'] if assessment['ungrouped_view_ids'] else []) +
                              (['No similarity gauge was supplied; the field may absorb pose or uniform scale errors.'] if gauge_reference is None else [])}
    return returned, report
