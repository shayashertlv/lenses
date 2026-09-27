"""Automatic bounded arm roles, hinges and photo-only articulation hypotheses.

Geometry supplies manufacturing priors; photo edges decide whether a proposed
motion is supported. Disconnected fragments can share one arm. Fused or folded
initializers without a credible front/arm attachment remain explicitly unresolved.
"""
from __future__ import annotations

from dataclasses import dataclass
import json

import numpy as np
from scipy import ndimage
from scipy.optimize import minimize_scalar
from scipy.spatial import cKDTree

from .camera import Camera, project, render_mask
from .mesh import TriangleMesh
from .mesh_components import component_face_labels
from .view_scene import Hinge, PartBinding, ViewState, bind_parts, pose_scene


@dataclass(frozen=True)
class AutomaticArticulationPolicy:
    minimum_arm_depth_fraction: float = .30
    minimum_side_distance_fraction: float = .24
    maximum_attachment_gap_fraction: float = .035
    maximum_fragment_gap_fraction: float = .02
    maximum_motion_degrees: float = 85.
    angle_grid_count: int = 25
    minimum_visible_boundary_points: int = 10
    minimum_pose_gain_px: float = .30
    minimum_pose_gain_fraction: float = .12
    maximum_holdout_error_width_fraction: float = .015
    near_edge_distance_width_fraction: float = .012
    minimum_near_edge_fraction: float = .80


def _compact(mesh, ids):
    faces = mesh.faces[np.asarray(ids)]
    used, inverse = np.unique(faces, return_inverse=True)
    return TriangleMesh(mesh.vertices[used], inverse.reshape(-1, 3), []), used


def _surface_contacts(query, triangles):
    """Closest points on a bounded nearest-centroid triangle candidate set.

    Every returned point lies on an actual source triangle. The approximation
    can conservatively miss a contact; vertex-only proximity incorrectly misses
    attachments inside a coarse face and is not used as hinge evidence.
    """
    triangles = np.asarray(triangles, float)
    tree = cKDTree(triangles.mean(axis=1)); outputs = []
    for start in range(0, len(query), 1024):
        points = query[start:start + 1024]
        ids = tree.query(points, k=min(24, len(triangles)))[1]
        if ids.ndim == 1:
            ids = ids[:, None]
        t = triangles[ids]; a, b, c = t[:, :, 0], t[:, :, 1], t[:, :, 2]
        q = points[:, None, :]; ab, ac, aq = b - a, c - a, q - a
        aa, bb, cc = np.sum(ab * ab, axis=2), np.sum(ab * ac, axis=2), np.sum(ac * ac, axis=2)
        d, e = np.sum(aq * ab, axis=2), np.sum(aq * ac, axis=2)
        denominator = aa * cc - bb * bb
        u = np.divide(d * cc - e * bb, denominator, out=np.zeros_like(d), where=denominator > 1e-30)
        v = np.divide(e * aa - d * bb, denominator, out=np.zeros_like(e), where=denominator > 1e-30)
        projected = a + u[..., None] * ab + v[..., None] * ac
        inside = (denominator > 1e-30) & (u >= 0) & (v >= 0) & (u + v <= 1)
        candidates = [projected]; distances = [np.where(inside, np.sum((q - projected) ** 2, axis=2), np.inf)]
        for left, right in ((a, b), (b, c), (c, a)):
            edge = right - left
            length = np.sum(edge * edge, axis=2)
            fraction = np.clip(np.divide(np.sum((q - left) * edge, axis=2), length,
                out=np.zeros_like(length), where=length > 0), 0., 1.)
            p = left + fraction[..., None] * edge
            candidates.append(p); distances.append(np.sum((q - p) ** 2, axis=2))
        values = np.stack(distances, axis=2).reshape(len(points), -1)
        positions = np.stack(candidates, axis=2).reshape(len(points), -1, 3)
        chosen = values.argmin(axis=1)
        outputs.append(positions[np.arange(len(points)), chosen])
    return np.concatenate(outputs)


def infer_automatic_part_bindings(scene, *, policy=AutomaticArticulationPolicy(), maximum_hypotheses=3):
    """Infer complete face-role hypotheses without provider names or materials.

    The existing +Y-up/+Z-front source-frame contract is required. Each arm
    needs a deep, lateral core plus a nearby attachment to the front assembly.
    Nearby lateral fragments attach to that core, including separated tips.
    """
    if not 1 <= maximum_hypotheses <= 3:
        raise ValueError('Use one to three automatic articulation hypotheses')
    # Provider split pieces often meet on identical cut walls. Welding across
    # primitive instances would merge the entire glasses into one component.
    # Inventory connectivity within each instance, without using its name.
    labels = np.full(len(scene.faces), -1, np.int64)
    offset = 0
    pieces = scene.parts or [{'face_start': 0, 'face_count': len(scene.faces)}]
    for part in pieces:
        start, count = part['face_start'], part['face_count']
        if np.any(labels[start:start + count] >= 0):
            raise ValueError('Overlapping source primitive face ranges')
        local = component_face_labels(scene.vertices, scene.faces[start:start + count], connectivity='exact_position_vertex')
        labels[start:start + count] = local + offset
        offset += int(local.max()) + 1 if len(local) else 0
    if np.any(labels < 0):
        raise ValueError('Source primitive face ranges must cover every triangle')
    used = scene.vertices[np.unique(scene.faces)]
    lo, hi = used.min(axis=0), used.max(axis=0)
    width = float(hi[0] - lo[0]); center_x = float((hi[0] + lo[0]) / 2)
    report = {'method': 'geometric_arm_attachment_hypotheses_v1', 'status': 'unsupported',
              'semantic_identity_verified': False, 'components': [], 'reasons': [],
              'axis_prior': '+Y mechanical hinge axis in declared +Y-up/+Z-front frame'}
    if width <= 0 or hi[2] - lo[2] < policy.minimum_arm_depth_fraction * width:
        report['reasons'].append('insufficient_front_to_arm_depth')
        return {'bindings': [], 'report': report}
    components = []
    for index in range(int(labels.max()) + 1):
        faces = np.flatnonzero(labels == index)
        points = scene.vertices[np.unique(scene.faces[faces])]
        minimum, maximum = points.min(axis=0), points.max(axis=0)
        median = np.median(points, axis=0)
        covariance = np.cov(points.T) if len(points) > 3 else np.zeros((3, 3))
        _, vectors = np.linalg.eigh(covariance)
        side = 'left' if median[0] < center_x else 'right'
        lateral = abs(float(median[0] - center_x)) / width
        depth = float(maximum[2] - minimum[2]) / width
        core = (lateral >= policy.minimum_side_distance_fraction and depth >= policy.minimum_arm_depth_fraction
                and abs(float(vectors[2, -1])) >= .65 and (maximum[0] - minimum[0]) < width * .38)
        row = {'component_id': index, 'face_count': len(faces), 'side': side,
               'depth_fraction': depth, 'lateral_fraction': lateral, 'arm_core': bool(core),
               'minimum': minimum.tolist(), 'maximum': maximum.tolist()}
        report['components'].append(row)
        components.append({'faces': faces, 'points': points, 'minimum': minimum, 'maximum': maximum,
                           'median': median, 'side': side, 'core': core, 'lateral': lateral})
    side_members = {side: [i for i, c in enumerate(components) if c['core'] and c['side'] == side]
                    for side in ('left', 'right')}
    if any(not ids for ids in side_members.values()):
        report['reasons'].append('both_separate_lateral_arm_cores_required')
        return {'bindings': [], 'report': report}
    # A fragment must be lateral, locally connected to the arm by distance,
    # and must not span a substantial fraction of the front lens width.
    for side, members in side_members.items():
        for _ in range(4):
            tree = cKDTree(np.concatenate([components[i]['points'] for i in members]))
            additions = []
            for i, comp in enumerate(components):
                if i in members or comp['side'] != side or comp['lateral'] < .36:
                    continue
                span = comp['maximum'] - comp['minimum']
                if span[0] > width * .17 or span[1] > width * .22:
                    continue
                distance = tree.query(comp['points'], k=1)[0]
                if np.quantile(distance, .05) <= width * policy.maximum_fragment_gap_fraction:
                    additions.append(i)
            if not additions:
                break
            members.extend(additions)
    moving_components = set(side_members['left']) | set(side_members['right'])
    front_ids = [i for i in range(len(components)) if i not in moving_components]
    if not front_ids:
        report['reasons'].append('no_independent_front_assembly')
        return {'bindings': [], 'report': report}
    front_points = np.concatenate([components[i]['points'] for i in front_ids])
    front_width = float(np.ptp(front_points[:, 0]))
    if front_width < width * .6:
        report['reasons'].append('front_assembly_has_insufficient_width')
        return {'bindings': [], 'report': report}
    front_face_ids = np.concatenate([components[i]['faces'] for i in front_ids])
    front_triangles = scene.vertices[scene.faces[front_face_ids]]
    hinges, attachment_rows = [], []
    for side, members in side_members.items():
        points = np.concatenate([components[i]['points'] for i in members])
        # Only the anterior arm end can attach to the lens/frame front. This
        # prevents a folded tip behind the opposite lens from becoming a hinge.
        anterior = points[points[:, 2] >= np.quantile(points[:, 2], .9)]
        if len(anterior) > 5000:
            anterior = anterior[np.linspace(0, len(anterior) - 1, 5000, dtype=int)]
        contacts = _surface_contacts(anterior, front_triangles)
        distance = np.linalg.norm(anterior - contacts, axis=1)
        cutoff = max(float(np.quantile(distance, .03)), width * .002)
        selected = distance <= cutoff
        attachment = (anterior[selected] + contacts[selected]) * .5
        origin = np.median(attachment, axis=0)
        gap = float(np.quantile(distance, .03)) / width
        if gap > policy.maximum_attachment_gap_fraction or not np.isfinite(origin).all():
            report['reasons'].append(f'{side}_hinge_attachment_unsupported')
            continue
        if abs(origin[0] - center_x) < width * .32:
            report['reasons'].append(f'{side}_hinge_not_lateral')
            continue
        hinges.append(Hinge(side, origin, np.array([0., 1., 0.]),
                            -policy.maximum_motion_degrees, policy.maximum_motion_degrees))
        attachment_rows.append({'side': side, 'component_ids': members, 'gap_fraction': gap,
                                'origin': origin.tolist(), 'support_points': int(selected.sum()),
                                'support_extent': np.ptp(attachment, axis=0).tolist()})
    report['attachments'] = attachment_rows
    if report['reasons'] or len(hinges) != 2:
        return {'bindings': [], 'report': report}
    role_faces = {f'{side}_temple_faces': np.concatenate([components[i]['faces'] for i in ids]).tolist()
                  for side, ids in side_members.items()}
    front_faces = np.concatenate([components[i]['faces'] for i in front_ids]).tolist()
    provenance = json.dumps({'method': report['method'], 'component_assignments': side_members,
                             'hinge_evidence': attachment_rows, 'axis_prior': report['axis_prior']}, sort_keys=True)
    binding = bind_parts(scene, front_faces=front_faces, hinges=hinges, provenance=provenance, **role_faces)
    report.update(status='automatic_unverified_binding', role_face_counts={role: binding.face_roles.count(role)
        for role in ('front', 'left_temple', 'right_temple', 'unresolved')}, hypothesis_count=1)
    return {'bindings': [binding], 'report': report}


def _photo_edges(rgb):
    image = np.asarray(rgb, float)
    dx = ndimage.sobel(ndimage.gaussian_filter(image, (1., 1., 0.)), axis=1) / 8
    dy = ndimage.sobel(ndimage.gaussian_filter(image, (1., 1., 0.)), axis=0) / 8
    magnitude = np.sqrt(np.mean(dx * dx + dy * dy, axis=2))
    return magnitude >= max(4., float(np.quantile(magnitude, .85)))


def fit_photo_arm_states(scene, binding, rgb, camera, view_id, *, policy=AutomaticArticulationPolicy()):
    """Bounded image-edge search with fixed spatial holdout and visibility guards.

    Only arm boundaries outside the front assembly supply evidence, avoiding
    studio reflections and frame texture within lenses. An invisible arm is
    left at rest with an unobserved report rather than guessed from a label.
    """
    binding.validate_scene(scene)
    shape = np.asarray(rgb).shape[:2]
    front, _ = _compact(scene, np.flatnonzero(np.asarray(binding.face_roles) == 'front'))
    front_mask = ndimage.binary_dilation(render_mask(front, camera, shape), iterations=2)
    photo_edges = _photo_edges(rgb) & ~front_mask
    if not photo_edges.any():
        return {'state': ViewState(view_id), 'report': {
            h.side: {'status': 'unobserved', 'reason': 'No photographic edges outside front projection'}
            for h in binding.hinges}}
    distance = ndimage.distance_transform_edt(~photo_edges)
    projected = project(scene.vertices, camera)
    reference_width = max(1., float(np.ptp(projected[:, 0])))
    near_distance = max(.5, reference_width * policy.near_edge_distance_width_fraction)
    roles = np.asarray(binding.face_roles)
    angles, rows = {}, {}
    for hinge in binding.hinges:
        arm, source_vertices = _compact(scene, np.flatnonzero(roles == f'{hinge.side}_temple'))
        from .view_scene import hinge_rotation
        cache = {}

        def evaluate(angle):
            token = round(float(angle), 7)
            if token in cache:
                return cache[token]
            moved = (arm.vertices - hinge.origin) @ hinge_rotation(hinge.axis, angle).T + hinge.origin
            mask = render_mask(TriangleMesh(moved, arm.faces, []), camera, shape)
            boundary = mask & ~ndimage.binary_erosion(mask) & ~front_mask
            boundary[[0, -1], :] = False; boundary[:, [0, -1]] = False
            y, x = np.nonzero(boundary)
            values = np.minimum(distance[y, x], 12.)
            holdout = ((x // 5) + (y // 5)) % 3 == 0
            train, test = values[~holdout], values[holdout]
            valid = min(len(train), len(test)) >= policy.minimum_visible_boundary_points
            result = {'angle_degrees': float(angle), 'visible_boundary_points': len(values),
                      'train_points': len(train), 'holdout_points': len(test),
                      'train_error_px': float(np.mean(train)) if valid else 100.,
                      'holdout_error_px': float(np.mean(test)) if valid else 100., 'supported': bool(valid),
                      'holdout_near_edge_fraction': float(np.mean(test <= near_distance)) if valid else 0.}
            cache[token] = result
            return result

        baseline = evaluate(0.)
        grid = np.linspace(hinge.minimum_degrees, hinge.maximum_degrees, policy.angle_grid_count)
        trials = [evaluate(a) for a in np.unique(np.r_[grid, 0.])]
        # Prevent explaining an arm by moving it behind the front or outside the
        # image: every candidate must retain most baseline visible support.
        supported = [r for r in trials if r['supported'] and
                     r['visible_boundary_points'] >= max(policy.minimum_visible_boundary_points * 3,
                                                         baseline['visible_boundary_points'] * .7)]
        if not supported or not baseline['supported']:
            angles[hinge.side] = 0.; rows[hinge.side] = {'status': 'unobserved', 'baseline': baseline}
            continue
        best = min(supported, key=lambda r: (r['train_error_px'], abs(r['angle_degrees'])))
        step = (hinge.maximum_degrees - hinge.minimum_degrees) / (policy.angle_grid_count - 1)
        refined = minimize_scalar(lambda a: evaluate(a)['train_error_px'], method='bounded',
            bounds=(max(hinge.minimum_degrees, best['angle_degrees'] - step),
                    min(hinge.maximum_degrees, best['angle_degrees'] + step)), options={'maxiter': 18, 'xatol': .1})
        proposal = evaluate(float(refined.x))
        if proposal['train_error_px'] > best['train_error_px']:
            proposal = best
        minimum_gain = max(policy.minimum_pose_gain_px, baseline['holdout_error_px'] * policy.minimum_pose_gain_fraction)
        absolute = arm_correspondence_quality(proposal, reference_width, policy=policy)
        baseline_quality = arm_correspondence_quality(baseline, reference_width, policy=policy)
        retained = (absolute['supported'] and proposal['visible_boundary_points'] >= baseline['visible_boundary_points'] * .7
                    and proposal['train_error_px'] < baseline['train_error_px']
                    and baseline['holdout_error_px'] - proposal['holdout_error_px'] >= minimum_gain
                    and abs(proposal['angle_degrees']) < policy.maximum_motion_degrees - 1.)
        angles[hinge.side] = proposal['angle_degrees'] if retained else 0.
        ambiguous = not retained and not baseline_quality['supported']
        rows[hinge.side] = {'status': 'fitted_photo_arm' if retained else 'camera_or_arm_ambiguous' if ambiguous else 'rest_retained', 'baseline': baseline,
            'proposal': proposal, 'retained': bool(retained), 'minimum_holdout_gain_px': minimum_gain,
            'absolute_correspondence_quality': absolute, 'baseline_correspondence_quality': baseline_quality,
            'reference_projected_width_px': reference_width,
            'ambiguity': 'Camera/shape mismatch or wrong image edges cannot be distinguished from arm pose' if ambiguous else None,
            'trials': sorted(cache.values(), key=lambda r: r['angle_degrees']),
            'evidence_scope': 'Image gradients outside front projection, held-out spatial cells; semantic edge identity unverified'}
    return {'state': ViewState(view_id, angles.get('left', 0.), angles.get('right', 0.)), 'report': rows}


def arm_correspondence_quality(measurement, reference_width, *, policy=AutomaticArticulationPolicy()):
    """Relative improvement cannot retain a still-poor photographic explanation."""
    limit = max(.5, reference_width * policy.maximum_holdout_error_width_fraction)
    near_limit = max(.5, reference_width * policy.near_edge_distance_width_fraction)
    supported = (measurement.get('supported') is True and measurement['holdout_error_px'] <= limit
                 and measurement.get('holdout_near_edge_fraction', 0.) >= policy.minimum_near_edge_fraction)
    return {'supported': bool(supported), 'maximum_holdout_error_px': limit,
            'maximum_holdout_error_width_fraction': policy.maximum_holdout_error_width_fraction,
            'near_edge_distance_px': near_limit, 'minimum_near_edge_fraction': policy.minimum_near_edge_fraction,
            'raster_quantization_allowance_px': .5}


def assess_photo_arm_state(scene, binding, rgb, camera, state):
    """Measure frozen arm poses after a shared rest-shape export."""
    from .view_scene import pose_scene
    posed = pose_scene(scene, binding, state, compact=True).mesh
    roles = np.asarray(binding.face_roles)
    shape = np.asarray(rgb).shape[:2]
    front, _ = _compact(posed, np.flatnonzero(roles == 'front'))
    front_mask = ndimage.binary_dilation(render_mask(front, camera, shape), iterations=2)
    edges = _photo_edges(rgb) & ~front_mask
    distance = ndimage.distance_transform_edt(~edges)
    report = {}
    for hinge in binding.hinges:
        arm, _ = _compact(posed, np.flatnonzero(roles == f'{hinge.side}_temple'))
        mask = render_mask(arm, camera, shape)
        boundary = mask & ~ndimage.binary_erosion(mask) & ~front_mask
        boundary[[0, -1], :] = False; boundary[:, [0, -1]] = False
        y, x = np.nonzero(boundary)
        holdout = ((x // 5) + (y // 5)) % 3 == 0
        values = np.minimum(distance[y[holdout], x[holdout]], 12.)
        report[hinge.side] = {'supported': bool(edges.any() and len(values) >= 10),
            'visible_boundary_points': len(x), 'holdout_points': len(values),
            'holdout_error_px': float(values.mean()) if len(values) else None}
    return report
