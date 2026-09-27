"""Bounded camera/temple/shared-shape prototype using explicit correspondences.

Semantic-to-surface correspondence and hinge inference are deliberately separate:
supplied triangles and barycentric coordinates must describe actual landmarks or
normal-only edge anchors. Silhouette extrema are not material-point landmarks.
The functions return in-sample hypotheses, never a reconstruction acceptance.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json

import numpy as np
from scipy.optimize import least_squares, minimize_scalar

from .camera import Camera, project
from .deformation import ReprojectionConstraint, fit_cage
from .mesh import TriangleMesh
from .view_scene import Hinge, PartBinding, ViewState, _array, bind_parts, mesh_geometry_sha256, pose_points


@dataclass(frozen=True)
class SurfaceObservation:
    """A frozen image observation anchored to original source triangle corners."""
    view_id: str
    group_id: str
    source_image_sha256: str
    source_geometry_sha256: str
    face_ids: np.ndarray
    barycentric: np.ndarray
    targets_xy: np.ndarray
    sigma_xy: np.ndarray
    provenance: str
    normal_xy: np.ndarray | None = None

    def __post_init__(self):
        for name in ('view_id', 'group_id', 'provenance'):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ValueError(f'{name} is required')
        for name in ('source_image_sha256', 'source_geometry_sha256'):
            value = getattr(self, name)
            if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
                raise ValueError(f'{name} requires SHA-256')
        faces = np.asarray(self.face_ids)
        if faces.ndim != 1 or not len(faces) or faces.dtype.kind not in 'iu' or faces.min() < 0:
            raise ValueError('Source face ordinals must be nonempty nonnegative integers')
        faces = faces.astype(np.int64, copy=True)
        faces.setflags(write=False)
        object.__setattr__(self, 'face_ids', faces)
        for name, shape in (('barycentric', (len(faces), 3)), ('targets_xy', (len(faces), 2)), ('sigma_xy', (len(faces), 2))):
            object.__setattr__(self, name, _array(getattr(self, name), shape, name))
        if np.any(self.barycentric < 0) or not np.allclose(self.barycentric.sum(axis=1), 1., atol=1e-8):
            raise ValueError('Barycentric coordinates must be inside their source triangles')
        if np.any(self.sigma_xy <= 0):
            raise ValueError('Observation uncertainty must be positive')
        if self.normal_xy is not None:
            normals = _array(self.normal_xy, (len(faces), 2), 'normal_xy')
            if not np.allclose(np.linalg.norm(normals, axis=1), 1., atol=1e-8):
                raise ValueError('Observed edge normals must have unit length')
            object.__setattr__(self, 'normal_xy', normals)

    def digest(self):
        digest = hashlib.sha256(json.dumps([self.view_id, self.group_id, self.source_image_sha256,
            self.source_geometry_sha256, self.provenance]).encode())
        for value in (self.face_ids, self.barycentric, self.targets_xy, self.sigma_xy, self.normal_xy):
            if value is not None:
                digest.update(str(value.shape).encode())
                digest.update(value.tobytes())
        return digest.hexdigest()


@dataclass(frozen=True)
class StructuredPolicy:
    maximum_camera_evaluations: int = 100
    maximum_camera_seeds: int = 4
    yaw_window_degrees: float = 35.
    pitch_window_degrees: float = 25.
    roll_window_degrees: float = 20.
    fit_perspective: bool = False
    minimum_front_points: int = 6
    temple_grid_count: int = 13
    maximum_temple_evaluations: int = 80
    minimum_pose_sensitivity_px_per_degree: float = .02
    maximum_shape_evaluations: int = 30
    maximum_displacement_fraction: float = .05

    def __post_init__(self):
        for name in ('maximum_camera_evaluations', 'maximum_camera_seeds', 'minimum_front_points',
                     'temple_grid_count', 'maximum_temple_evaluations', 'maximum_shape_evaluations'):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f'{name} must be a positive integer')
        if self.maximum_camera_seeds > 4 or not 3 <= self.temple_grid_count <= 61:
            raise ValueError('Use at most four camera seeds and 3 to 61 temple grid samples')
        for name in ('yaw_window_degrees', 'pitch_window_degrees', 'roll_window_degrees',
                     'minimum_pose_sensitivity_px_per_degree', 'maximum_displacement_fraction'):
            if not np.isfinite(getattr(self, name)) or getattr(self, name) <= 0:
                raise ValueError(f'{name} must be finite and positive')
        if self.maximum_displacement_fraction > .05 or type(self.fit_perspective) is not bool:
            raise ValueError('Shape trust radius is at most five percent; perspective flag must be boolean')


def propose_part_bindings(scene, photo_evidence, coarse_cameras=None, *, maximum_hypotheses=3):
    """Validate supplied face/hinge hypotheses; never promote names to evidence.

    `photo_evidence['binding_hypotheses']` is supplied by a grounded part stage or
    a diagnostic annotator. Absent bindings are unsupported, not guessed.
    """
    hypotheses = photo_evidence.get('binding_hypotheses', [])
    if type(maximum_hypotheses) is not int or not 1 <= maximum_hypotheses <= 3:
        raise ValueError('Use one to three binding hypotheses')
    if not hypotheses:
        from .automatic_articulation import infer_automatic_part_bindings
        return infer_automatic_part_bindings(scene, maximum_hypotheses=maximum_hypotheses)['bindings']
    if len(hypotheses) > maximum_hypotheses:
        raise ValueError('Too many explicit source-face and hinge hypotheses')
    return [bind_parts(scene, **{**row, 'hinges': tuple(Hinge(**h) for h in row['hinges'])}) for row in hypotheses]


def _validated(scene, binding, evidence):
    binding.validate_scene(scene)
    evidence = tuple(evidence)
    if not evidence or any(not isinstance(item, SurfaceObservation) for item in evidence):
        raise ValueError('Typed source-bound observations are required')
    if len({(item.view_id, item.group_id) for item in evidence}) != len(evidence):
        raise ValueError('Observation group IDs must be unique within a photograph')
    photos = {}
    for item in evidence:
        if item.source_geometry_sha256 != binding.source_geometry_sha256 or item.face_ids.max() >= len(scene.faces):
            raise ValueError('Observation anchors do not match source geometry')
        if item.view_id in photos and photos[item.view_id] != item.source_image_sha256:
            raise ValueError('One view ID cannot refer to different source photographs')
        photos[item.view_id] = item.source_image_sha256
        if len(set(np.asarray(binding.face_roles)[item.face_ids])) != 1:
            raise ValueError('Each observation group must belong to one rigid motion role')
    if len(set(photos.values())) != len(photos):
        raise ValueError('Repeated source bytes cannot represent independent photographs')
    return evidence


def _points(scene, item):
    return np.einsum('ni,nij->nj', item.barycentric, scene.vertices[scene.faces[item.face_ids]])


def _difference(item, projected, *, standardized=True):
    difference = projected - item.targets_xy
    sigma = item.sigma_xy
    if item.normal_xy is not None:
        difference = (difference * item.normal_xy).sum(axis=1, keepdims=True)
        sigma = np.sqrt(((sigma * item.normal_xy) ** 2).sum(axis=1, keepdims=True))
    return difference / sigma if standardized else difference


def _role(binding, item):
    return binding.face_roles[int(item.face_ids[0])]


def _residual(scene, binding, rows, camera, state):
    return np.concatenate([(_difference(row, project(pose_points(_points(scene, row),
        [_role(binding, row)] * len(row.face_ids), binding, state), camera)) /
        np.sqrt(len(row.face_ids))).ravel() for row in rows])


def fit_front_cameras(scene, binding, evidence, camera_seeds, *, policy=StructuredPolicy()):
    """Fit front geometry only; temples cannot bias photographic camera pose."""
    evidence = _validated(scene, binding, evidence)
    cameras, reports = {}, {}
    extent = float(np.ptp(scene.vertices, axis=0).max())
    for view_id in sorted({row.view_id for row in evidence}):
        rows = [row for row in evidence if row.view_id == view_id and _role(binding, row) == 'front']
        if sum(len(row.face_ids) for row in rows) < policy.minimum_front_points:
            reports[view_id] = {'status': 'insufficient_front_observations'}
            continue
        seeds = camera_seeds.get(view_id, ())
        seeds = [seeds] if isinstance(seeds, Camera) else list(seeds)
        if not seeds or len(seeds) > policy.maximum_camera_seeds:
            raise ValueError('Each fitted view needs a bounded explicit camera seed set')
        points_xy = np.concatenate([row.targets_xy for row in rows])
        translation_window = max(float(np.ptp(points_xy, axis=0).max()) * .3, 5.)
        trials = []
        for seed in seeds:
            if not isinstance(seed, Camera) or seed.scale <= 0 or not np.isfinite(list(seed.to_dict().values())).all():
                raise ValueError('Camera seeds must be finite with positive scale')
            if not 0 <= seed.perspective <= .8 / extent:
                raise ValueError('Camera seed perspective exceeds normalized prototype range')
            initial = [seed.yaw, seed.pitch, seed.roll, np.log(seed.scale), seed.center_x, seed.center_y]
            windows = [policy.yaw_window_degrees, policy.pitch_window_degrees,
                       policy.roll_window_degrees, .35, translation_window, translation_window]
            low, high = np.asarray(initial) - windows, np.asarray(initial) + windows
            if policy.fit_perspective:
                initial.append(seed.perspective)
                low, high = np.r_[low, 0.], np.r_[high, .8 / extent]

            def unpack(values):
                return Camera(*map(float, values[:3]), float(values[6]) if policy.fit_perspective else seed.perspective,
                              float(np.exp(values[3])), float(values[4]), float(values[5]))

            state = ViewState(view_id)
            initial_residual = _residual(scene, binding, rows, seed, state)

            def objective(values):
                try:
                    return _residual(scene, binding, rows, unpack(values), state)
                except ValueError:
                    return np.full(initial_residual.shape, 1e6)

            fit = least_squares(objective, initial, bounds=(low, high), loss='soft_l1',
                                max_nfev=policy.maximum_camera_evaluations, ftol=1e-9, xtol=1e-9, gtol=1e-9)
            camera = unpack(fit.x)
            # Retention compares the same unrobust residual; robust fitting cannot hide regressions.
            before, after = float(np.mean(initial_residual ** 2)), float(np.mean(objective(fit.x) ** 2))
            if after > before:
                camera, after = seed, before
            singular = np.linalg.svd(fit.jac, compute_uv=False)
            trials.append({'camera': camera, 'initial_rms_sigma': float(np.sqrt(before)),
                           'rms_sigma': float(np.sqrt(after)), 'evaluations': int(fit.nfev),
                           'optimizer_converged': bool(fit.success),
                           'local_jacobian_rank': int(np.sum(singular > singular[0] * 1e-8)) if len(singular) else 0,
                           'parameter_count': len(initial)})
        selected = min(trials, key=lambda row: row['rms_sigma'])
        cameras[view_id] = selected['camera']
        reports[view_id] = {'status': 'fitted_front_only', **{k: v for k, v in selected.items() if k != 'camera'},
                           'camera': selected['camera'].to_dict(), 'seed_count': len(trials),
                           'independent_validation': False}
    return {'cameras': cameras, 'report': reports, 'scope': 'Supplied front observations only; camera ambiguity remains'}


def fit_temple_poses(scene, binding, evidence, cameras, *, policy=StructuredPolicy()):
    """One angle per visible arm and photograph, one shared rest shape/hinge."""
    evidence = _validated(scene, binding, evidence)
    states, reports = {}, {}
    for view_id, camera in cameras.items():
        angles, side_reports = {}, {}
        for hinge in binding.hinges:
            rows = [row for row in evidence if row.view_id == view_id and _role(binding, row) == f'{hinge.side}_temple']
            if not rows:
                side_reports[hinge.side] = {'status': 'unobserved', 'angle_degrees': 0.}
                continue

            def residual(angle):
                state = ViewState(view_id, **{f'{hinge.side}_degrees': float(angle)})
                return _residual(scene, binding, rows, camera, state)

            def objective(angle):
                values = residual(angle)
                return float(np.sum(2 * (np.sqrt(1 + values ** 2) - 1)))

            grid = np.unique(np.r_[np.linspace(hinge.minimum_degrees, hinge.maximum_degrees, policy.temple_grid_count), 0.])
            losses = np.array([objective(value) for value in grid])
            index = int(losses.argmin())
            lo, hi = grid[max(0, index - 1)], grid[min(len(grid) - 1, index + 1)]
            fit = minimize_scalar(objective, bounds=(lo, hi), method='bounded',
                                  options={'maxiter': policy.maximum_temple_evaluations, 'xatol': 1e-7})
            angle = float(fit.x) if fit.fun <= losses[index] else float(grid[index])
            before, after = residual(0.), residual(angle)
            if np.mean(after ** 2) > np.mean(before ** 2):
                angle, after = 0., before
            delta = .001
            minus, plus = max(hinge.minimum_degrees, angle - delta), min(hinge.maximum_degrees, angle + delta)
            # Pixel sensitivity, without uncertainty/point-count weighting, determines observability.
            a, b = ViewState(view_id, **{f'{hinge.side}_degrees': minus}), ViewState(view_id, **{f'{hinge.side}_degrees': plus})
            derivatives = []
            for row in rows:
                roles = [_role(binding, row)] * len(row.face_ids)
                pa = project(pose_points(_points(scene, row), roles, binding, a), camera)
                pb = project(pose_points(_points(scene, row), roles, binding, b), camera)
                derivatives.append((_difference(row, pb, standardized=False) - _difference(row, pa, standardized=False)) / (plus - minus))
            sensitivity = float(np.sqrt(np.mean(np.concatenate(derivatives) ** 2)))
            observed = sensitivity >= policy.minimum_pose_sensitivity_px_per_degree
            angles[f'{hinge.side}_degrees'] = angle if observed else 0.
            side_reports[hinge.side] = {'status': 'fitted' if observed else 'unobservable',
                'angle_degrees': angles[f'{hinge.side}_degrees'], 'proposed_angle_degrees': angle,
                'initial_rms_sigma': float(np.sqrt(np.mean(before ** 2))),
                'proposal_rms_sigma': float(np.sqrt(np.mean(after ** 2))),
                'sensitivity_px_per_degree': sensitivity, 'grid_candidates': len(grid),
                'at_bounds': min(abs(angle - hinge.minimum_degrees), abs(angle - hinge.maximum_degrees)) < .1}
        states[view_id] = ViewState(view_id, **angles)
        reports[view_id] = side_reports
    return {'view_states': states, 'report': reports, 'scope': 'Conditional on supplied bindings, hinge axes and fitted cameras'}


def assess_geometry_candidate(scene, binding, evidence, cameras, view_states):
    """Measure each frozen semantic group; small total error cannot hide a lens."""
    groups = []
    for row in evidence:
        if row.view_id not in cameras:
            continue
        state = view_states.get(row.view_id, ViewState(row.view_id))
        moved = pose_points(_points(scene, row), [_role(binding, row)] * len(row.face_ids), binding, state)
        predicted = project(moved, cameras[row.view_id])
        error = _difference(row, predicted, standardized=False)
        standardized = _difference(row, predicted)
        groups.append({'view_id': row.view_id, 'group_id': row.group_id, 'motion_role': _role(binding, row),
                       'point_count': len(row.face_ids), 'rms_px': float(np.sqrt(np.mean(error ** 2))),
                       'rms_sigma': float(np.sqrt(np.mean(standardized ** 2))),
                       'maximum_sigma': float(np.linalg.norm(standardized, axis=1).max()),
                       'evidence_sha256': row.digest()})
    return {'groups': groups, 'unresolved_face_count': binding.face_roles.count('unresolved'),
            'quality_verdict': 'unmeasured', 'accepted': False, 'topology_repair_performed': False,
            'scope': 'Frozen supplied-correspondence fit, not independent reconstruction accuracy'}


def fit_shared_geometry(scene, binding, evidence, cameras, view_states, *, policy=StructuredPolicy()):
    """Propose a front-driven existing cage, then check ALL articulated groups.

    The cage does not optimize temple observations. They are selection checks;
    their nonregression prevents an improved front silently damaging an arm.
    Moved hinge origins and differential axes are shared across all photographs.
    The returned mesh is a rest mesh. No job/export pipeline is mutated here.
    """
    evidence = _validated(scene, binding, evidence)
    constraints = []
    for view_id, camera in cameras.items():
        rows = [row for row in evidence if row.view_id == view_id and _role(binding, row) == 'front']
        if not rows:
            continue
        points, targets, sigmas, normals, labels = [], [], [], [], []
        for row in rows:
            repeat = 2 if row.normal_xy is None else 1
            points.append(np.repeat(_points(scene, row), repeat, axis=0))
            targets.append(np.repeat(row.targets_xy, repeat, axis=0))
            sigmas.append(np.repeat(row.sigma_xy, repeat, axis=0))
            normals.append(np.tile(np.eye(2), (len(row.face_ids), 1)) if repeat == 2 else row.normal_xy)
            labels.extend([row.group_id] * (len(row.face_ids) * repeat))
        digest = hashlib.sha256(''.join(row.digest() for row in rows).encode()).hexdigest()
        constraints.append(ReprojectionConstraint(view_id, rows[0].source_image_sha256, digest, camera,
            np.concatenate(points), np.concatenate(targets), np.concatenate(sigmas), tuple(labels), np.concatenate(normals)))
    if not constraints:
        raise ValueError('Shared refinement requires front observations with cameras')
    lo, hi = scene.vertices.min(axis=0), scene.vertices.max(axis=0)
    pad = max(float(np.max(hi - lo)) * .01, 1e-9)
    triangle = scene.vertices[scene.faces]
    indices = np.linspace(0, len(triangle) - 1, min(512, len(triangle)), dtype=int)
    gauge_points = triangle[indices].mean(axis=1)
    gauge_weights = np.linalg.norm(np.cross(triangle[indices, 1] - triangle[indices, 0],
                                           triangle[indices, 2] - triangle[indices, 0]), axis=1)
    valid = gauge_weights > 0
    field, cage_report = fit_cage(constraints, lo - pad, hi + pad, shape=(3, 3, 3),
        gauge_reference=(gauge_points[valid], gauge_weights[valid]),
        max_evaluations=policy.maximum_shape_evaluations,
        maximum_displacement_fraction=policy.maximum_displacement_fraction)
    moved, _ = field.transform(scene.vertices)
    candidate = TriangleMesh(moved, scene.faces.copy(), [dict(part) for part in scene.parts])
    hinges = []
    for hinge in binding.hinges:
        origins, derivatives = field.transform(hinge.origin[None])
        axis = derivatives[0] @ hinge.axis
        hinges.append(Hinge(hinge.side, origins[0], axis / np.linalg.norm(axis), hinge.minimum_degrees, hinge.maximum_degrees))
    candidate_binding = PartBinding(mesh_geometry_sha256(candidate), binding.face_roles, tuple(hinges), binding.provenance)
    before = assess_geometry_candidate(scene, binding, evidence, cameras, view_states)
    after = assess_geometry_candidate(candidate, candidate_binding, evidence, cameras, view_states)
    nonregressed = all(a['rms_sigma'] <= b['rms_sigma'] + 1e-6 for a, b in zip(after['groups'], before['groups']))
    old_normals = np.cross(triangle[:, 1] - triangle[:, 0], triangle[:, 2] - triangle[:, 0])
    moved_triangles = candidate.vertices[candidate.faces]
    new_normals = np.cross(moved_triangles[:, 1] - moved_triangles[:, 0], moved_triangles[:, 2] - moved_triangles[:, 0])
    valid = np.linalg.norm(old_normals, axis=1) > 0
    triangle_orientation = bool(np.all(np.sum(old_normals[valid] * new_normals[valid], axis=1) > 0))
    retained = bool(cage_report['retained'] and nonregressed and triangle_orientation)
    return {'scene': candidate if retained else scene, 'binding': candidate_binding if retained else binding,
            'field': field if retained else None, 'report': {'status': 'proposal' if retained else 'unchanged',
            'retained': retained, 'cage': cage_report, 'before': before, 'proposal': after,
            'every_articulated_group_nonregressed': nonregressed,
            'discrete_triangle_orientation_retained': triangle_orientation,
            'hinge_contact_verified': False,
            'scope': 'Front-driven shared cage with posed-group checks; GLB attribute/export verification remains required'}}


def classify_geometry_failure(assessment, *, required_roles=('front', 'left_temple', 'right_temple'),
                              binding=None, maximum_rms_px=3.):
    """Report unsupported/missing evidence without diagnosing topology from error alone."""
    if binding is not None and any(role not in binding.face_roles for role in required_roles):
        return {'status': 'unresolved', 'class': 'missing_part_binding_or_topology',
                'reason': 'A required role has no source faces; image/topology diagnosis is still needed'}
    if not assessment['groups']:
        return {'status': 'unresolved', 'class': 'insufficient_evidence'}
    failing = [row for row in assessment['groups'] if row['rms_px'] > maximum_rms_px]
    return {'status': 'unresolved' if failing else 'within_supplied_correspondence_tolerance',
            'class': 'pose_shape_correspondence_or_topology' if failing else 'unmeasured_independent_quality',
            'failing_groups': [{'view_id': row['view_id'], 'group_id': row['group_id']} for row in failing]}
