"""Photo-aperture/component relations with explicit hypothetical visibility.

Every component is rasterized independently, including geometry behind other
components. Full-scene first hit assumes every surface is opaque and is only a
geometric diagnostic. Aperture coverage or coincident depths never assign an
optical material or establish that two pieces belong to one physical lens.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import itertools

import numpy as np

from .camera import Camera, project
from .mesh import TriangleMesh
from .raster import DEPTH_UNITS, Raster, rasterize


@dataclass(frozen=True)
class ProjectionLimits:
    maximum_vertices: int = 2_000_000
    maximum_faces: int = 2_000_000
    maximum_components: int = 1024
    maximum_pixels: int = 262_144
    maximum_sparse_events: int = 8_000_000
    maximum_apertures: int = 256
    maximum_mask_pixels: int = 64_000_000
    maximum_pair_checks: int = 1_000_000
    maximum_aperture_pair_checks: int = 2_000_000


def _limits(value):
    if not isinstance(value, ProjectionLimits) or any(type(v) is not int or v < 1 for v in asdict(value).values()):
        raise ValueError('Positive integer projection limits required')


def _hash(array):
    value = np.ascontiguousarray(array)
    prefix = json.dumps({'dtype': value.dtype.str, 'shape': value.shape}, sort_keys=True).encode()
    return hashlib.sha256(prefix+value.tobytes()).hexdigest()


def project_components(mesh: TriangleMesh, face_components, camera: Camera, shape, *,
                       depth_to_reference=1.0, limits=ProjectionLimits(), view_scene=None,
                       coordinate_normalization=None):
    """Return sparse per-component first hits and an all-opaque scene first hit.

    Input geometry/camera already share one coordinate system. Depth arrays use
    input mesh units; depth_to_reference scales differences into a caller's
    declared reference units (normally source extent). The caller must bind
    source bytes, component provenance, camera normalization and image mapping.
    """
    _limits(limits)
    articulation = None
    if view_scene is not None:
        from .view_scene import pose_mesh_from_contract
        posed = pose_mesh_from_contract(mesh, view_scene['contract'], view_scene['view_id'],
            photo_sha256=view_scene.get('source_image_sha256'), normalization=coordinate_normalization)
        mesh, articulation = posed['mesh'], posed['report']
    vertices, faces, labels = np.asarray(mesh.vertices), np.asarray(mesh.faces), np.asarray(face_components)
    if (vertices.ndim != 2 or vertices.shape[1] != 3 or vertices.dtype.kind != 'f'
            or not 1 <= len(vertices) <= limits.maximum_vertices or not np.isfinite(vertices).all()
            or faces.ndim != 2 or faces.shape[1] != 3 or faces.dtype.kind not in 'iu'
            or not 1 <= len(faces) <= limits.maximum_faces or np.any(faces < 0) or np.any(faces >= len(vertices))):
        raise ValueError('Finite bounded indexed triangle geometry required')
    if labels.shape != (len(faces),) or labels.dtype.kind not in 'iu' or np.any(labels < 0):
        raise ValueError('Every source face requires a nonnegative component label')
    unique = np.unique(labels)
    if len(unique) > limits.maximum_components or not np.array_equal(unique, np.arange(len(unique))):
        raise ValueError('Component labels must be contiguous and within capacity')
    if (not isinstance(shape, (tuple, list)) or len(shape) != 2 or any(type(v) is not int or v < 2 for v in shape)
            or shape[0]*shape[1] > limits.maximum_pixels):
        raise ValueError('Invalid or over-capacity projection shape')
    if (not isinstance(camera, Camera) or not np.isfinite(list(camera.to_dict().values())).all()
            or camera.scale <= 0 or camera.perspective < 0):
        raise ValueError('Finite camera with positive scale and nonnegative perspective required')
    if isinstance(depth_to_reference, bool) or not np.isfinite(depth_to_reference) or depth_to_reference <= 0:
        raise ValueError('Positive finite depth reference scale required')
    # The rasterizer is a point-sampling diagnostic, not a clipping renderer.
    screen = project(vertices, camera)
    if not np.isfinite(screen).all() or np.max(np.abs(screen)) > 1e7:
        raise ValueError('Projected geometry exceeds the supported domain')
    full = rasterize(mesh, camera, tuple(shape))
    order = np.argsort(labels, kind='stable')
    boundaries = np.flatnonzero(np.diff(labels[order]))+1
    selections = np.split(order, boundaries)
    pieces, event_count = [], 0
    for component_id, chosen in enumerate(selections):
        # Only the temporary raster mesh is compacted. Original source faces,
        # source vertex references and barycentric corner order remain intact.
        used, inverse = np.unique(faces[chosen], return_inverse=True)
        local = TriangleMesh(vertices[used], inverse.reshape((-1, 3)), [])
        result = rasterize(local, camera, tuple(shape))
        pixels = np.flatnonzero(result.mask)
        event_count += len(pixels)
        if event_count > limits.maximum_sparse_events:
            raise ValueError('Sparse projection capacity exceeded; no components may be omitted')
        local_faces = result.face_index.ravel()[pixels]
        pieces.append({'component_id': component_id, 'pixels': pixels.astype(np.int64),
                       'face_indices': chosen[local_faces].astype(np.int64),
                       'barycentric': result.barycentric.reshape((-1, 3))[pixels].copy(),
                       'depth': result.depth.ravel()[pixels].copy()})
    report = {'schema_version': 1, 'method': 'complete_component_first_hit_v1',
              'camera': camera.to_dict(), 'shape': list(shape), 'depth_units': DEPTH_UNITS,
              'depth_to_reference': float(depth_to_reference),
              'component_count': len(pieces), 'source_face_count': len(faces),
              'source_faces_sha256': _hash(faces), 'source_vertices_sha256': _hash(vertices),
              'face_components_sha256': _hash(labels), 'sparse_events': event_count,
              'full_scene_pixels': int(full.mask.sum()), 'limits': asdict(limits),
              'accepted': False, 'semantic_identity': 'not_inferred', 'quality_verdict': 'unmeasured',
              'articulation': articulation,
              'visibility_scope': 'full scene assumes every component opaque; per-component first hits have no opaque stop',
              'limitations': ['Pixel-center support is conditional on supplied geometry, cameras and articulation.',
                  'A component with no projected samples is unobserved at this grid, not verified absent.',
                  'Nearest face ties use source face ordering; exact ownership does not identify physical visibility.',
                  'Depth scale is caller supplied and must be bound to the source/camera normalization.',
                  'No clipping, refraction, optical classification or physical group selection is performed.']}
    return {'shape': tuple(shape), 'face_components': labels.copy(), 'full_scene': full,
            'pieces': pieces, 'report': report}


def _ratio(numerator, denominator):
    return float(numerator/denominator) if denominator else None


def _depth_summary(differences, tolerance):
    if not len(differences):
        return None
    if not np.isfinite(differences).all():
        raise ValueError('Depth differences overflow the declared reference units')
    return {'sample_count': len(differences),
            'signed_quantiles': np.quantile(differences, [0, .1, .5, .9, 1]).tolist(),
            'absolute_quantiles': np.quantile(np.abs(differences), [0, .1, .5, .9, 1]).tolist(),
            'left_nearer_pixels': int(np.count_nonzero(differences > tolerance)),
            'right_nearer_pixels': int(np.count_nonzero(differences < -tolerance)),
            'within_tolerance_pixels': int(np.count_nonzero(np.abs(differences) <= tolerance))}


def measure_component_apertures(projections, apertures, *, depth_tolerance=0.0,
                                limits=ProjectionLimits()):
    """Measure complete denominators, overlap and depth without semantic selection.

    Each aperture has id, boolean mask and known_domain on the exact projection
    grid, plus nonempty JSON provenance. Unknown crop exteriors never become
    negative evidence. Separate decoder/component alternatives remain separate.
    Positive pair depth means the left component is nearer than the right.
    """
    _limits(limits)
    if isinstance(depth_tolerance, bool) or not np.isfinite(depth_tolerance) or depth_tolerance < 0:
        raise ValueError('Nonnegative finite depth tolerance required')
    shape = projections['shape']; pixels_count = int(np.prod(shape))
    pieces, labels, full = projections['pieces'], np.asarray(projections['face_components']), projections['full_scene']
    if (len(shape) != 2 or any(type(v) is not int or v < 2 for v in shape)
            or pixels_count > limits.maximum_pixels or not 1 <= len(pieces) <= limits.maximum_components
            or labels.ndim != 1 or labels.dtype.kind not in 'iu' or not 1 <= len(labels) <= limits.maximum_faces
            or np.any(labels < 0) or np.any(labels >= len(pieces))):
        raise ValueError('Invalid projection inventory or capacity')
    if (not isinstance(full, Raster) or full.face_index.shape != tuple(shape) or full.depth.shape != tuple(shape)
            or full.face_index.dtype.kind not in 'iu' or np.any(full.face_index < -1) or np.any(full.face_index >= len(labels))
            or not np.isfinite(full.depth[full.mask]).all() or not np.isposinf(full.depth[~full.mask]).all()):
        raise ValueError('Invalid full-scene geometric visibility')
    scale = projections['report']['depth_to_reference']
    if isinstance(scale, bool) or not np.isfinite(scale) or scale <= 0:
        raise ValueError('Invalid depth reference scale')
    metadata = projections['report']
    if (metadata.get('method') != 'complete_component_first_hit_v1'
            or metadata.get('shape') != list(shape) or metadata.get('component_count') != len(pieces)
            or metadata.get('source_face_count') != len(labels) or metadata.get('face_components_sha256') != _hash(labels)
            or metadata.get('full_scene_pixels') != int(full.mask.sum())
            or metadata.get('accepted') is not False or metadata.get('semantic_identity') != 'not_inferred'):
        raise ValueError('Projection metadata contradicts its supplied arrays')
    possible_pairs = len(pieces)*(len(pieces)-1)//2
    if possible_pairs > limits.maximum_pair_checks:
        raise ValueError('Complete component-pair work exceeds capacity')
    if (not isinstance(apertures, list) or len(apertures) > limits.maximum_apertures
            or len(apertures)*pixels_count > limits.maximum_mask_pixels):
        raise ValueError('Aperture inventory exceeds capacity')
    local_apertures, ids = [], set()
    for a in apertures:
        if not isinstance(a, dict) or set(a) != {'id', 'mask', 'known_domain', 'provenance'}:
            raise ValueError('Aperture requires id, mask, known_domain and provenance')
        aid = a['id']
        if not isinstance(aid, str) or not aid or aid in ids:
            raise ValueError('Unique nonempty aperture IDs required')
        ids.add(aid)
        mask, known = np.asarray(a['mask']), np.asarray(a['known_domain'])
        if mask.dtype != np.bool_ or known.dtype != np.bool_ or mask.shape != tuple(shape) or known.shape != tuple(shape) or np.any(mask & ~known):
            raise ValueError('Aperture mask/domain must be matching booleans, with positives inside known domain')
        if not isinstance(a['provenance'], dict) or not a['provenance']:
            raise ValueError('Aperture provenance required')
        provenance = json.loads(json.dumps(a['provenance'], allow_nan=False))
        local_apertures.append({'id': aid, 'mask': mask.ravel(), 'known': known.ravel(),
                                'pixels': int(mask.sum()), 'known_pixels': int(known.sum()),
                                'mask_sha256': _hash(mask), 'known_sha256': _hash(known), 'provenance': provenance})
    flat_face, flat_depth = full.face_index.ravel(), full.depth.ravel()
    full_components = np.full(pixels_count, -1, np.int64)
    full_components[flat_face >= 0] = labels[flat_face[flat_face >= 0]]
    rows, event_count, validated = [], 0, []
    for index, piece in enumerate(pieces):
        p, f, d, b = (np.asarray(piece[k]) for k in ('pixels', 'face_indices', 'depth', 'barycentric'))
        if (type(piece['component_id']) is not int or piece['component_id'] != index
                or p.ndim != 1 or p.dtype.kind not in 'iu' or np.any(p < 0) or np.any(p >= pixels_count)
                or np.any(p[1:] <= p[:-1]) or f.shape != p.shape or f.dtype.kind not in 'iu'
                or np.any(f < 0) or np.any(f >= len(labels)) or np.any(labels[f] != index)
                or d.shape != p.shape or d.dtype.kind != 'f' or not np.isfinite(d).all()
                or b.shape != (len(p), 3) or b.dtype.kind != 'f' or not np.isfinite(b).all()
                or (len(b) and (np.any(b < -1e-8) or not np.allclose(b.sum(axis=1), 1, atol=1e-8, rtol=0)))):
            raise ValueError('Invalid sparse component first-hit data')
        event_count += len(p)
        if event_count > limits.maximum_sparse_events:
            raise ValueError('Sparse projection capacity exceeded')
        if np.any(flat_face[p] < 0):
            raise ValueError('Per-component footprint lacks full-scene geometric support')
        delta = (d-flat_depth[p])*scale
        if not np.isfinite(delta).all():
            raise ValueError('Front-depth differences overflow reference units')
        blockers, counts = np.unique(full_components[p[delta > depth_tolerance]], return_counts=True)
        relations = []
        for a in local_apertures:
            known_count = int(a['known'][p].sum()); inside = int(a['mask'][p].sum())
            union = known_count+a['pixels']-inside
            relations.append({'aperture_id': a['id'], 'piece_known_pixels': known_count,
                'piece_unknown_pixels': len(p)-known_count, 'aperture_pixels': a['pixels'],
                'intersection_pixels': inside, 'piece_outside_aperture_pixels': known_count-inside,
                'aperture_unexplained_pixels': a['pixels']-inside,
                'piece_inside_fraction': _ratio(inside, known_count),
                'aperture_covered_fraction': _ratio(inside, a['pixels']), 'iou': _ratio(inside, union),
                'area_ratio': _ratio(known_count, a['pixels'])})
        rows.append({'component_id': index, 'projected_pixels': len(p),
                     'exact_owner_pixels': int(np.count_nonzero(full_components[p] == index)),
                     'depth_compatible_front_pixels': int(np.count_nonzero(np.abs(delta) <= depth_tolerance)),
                     'piece_numerically_ahead_of_full_scene_pixels': int(np.count_nonzero(delta < -depth_tolerance)),
                     'hypothetical_opaque_blockers': [{'component_id': int(c), 'pixels': int(n)} for c, n in zip(blockers, counts)],
                     'apertures': relations})
        validated.append((p, d))
    if event_count != metadata.get('sparse_events'):
        raise ValueError('Sparse event count contradicts projection metadata')
    pairs, zero_pairs, aperture_pair_work = [], 0, 0
    for left, right in itertools.combinations(range(len(pieces)), 2):
        lp, ld = validated[left]; rp, rd = validated[right]
        overlap, li, ri = np.intersect1d(lp, rp, assume_unique=True, return_indices=True)
        if not len(overlap):
            zero_pairs += 1; continue
        aperture_pair_work += len(local_apertures)
        if aperture_pair_work > limits.maximum_aperture_pair_checks:
            raise ValueError('Aperture/component-pair work capacity exceeded')
        delta = (rd[ri]-ld[li])*scale
        relations = []
        for a in local_apertures:
            inside = a['mask'][overlap]
            relations.append({'aperture_id': a['id'], 'known_overlap_pixels': int(a['known'][overlap].sum()),
                              'aperture_overlap_pixels': int(inside.sum()),
                              'depth_summary': _depth_summary(delta[inside], depth_tolerance)})
        pairs.append({'left_component': left, 'right_component': right,
                      'left_pixels': len(lp), 'right_pixels': len(rp), 'intersection_pixels': len(overlap),
                      'left_contained_fraction': _ratio(len(overlap), len(lp)),
                      'right_contained_fraction': _ratio(len(overlap), len(rp)),
                      'iou': _ratio(len(overlap), len(lp)+len(rp)-len(overlap)),
                      'depth_summary': _depth_summary(delta, depth_tolerance), 'apertures': relations})
    return {'schema_version': 1, 'method': 'component_aperture_relations_v1', 'accepted': False,
            'quality_verdict': 'unmeasured', 'semantic_identity': 'not_inferred',
            'projection': projections['report'], 'depth_tolerance_reference_units': float(depth_tolerance),
            'depth_quantile_probabilities': [0, .1, .5, .9, 1],
            'depth_sign': 'right depth minus left depth; positive means left nearer',
            'zero_denominator_policy': 'null; no projected/known support is not verified absence',
            'apertures': [{k: v for k, v in a.items() if k not in ('mask', 'known')} |
                          {'full_scene_intersection_pixels': int(np.count_nonzero(a['mask'] & (flat_face >= 0))),
                           'unexplained_pixels': int(np.count_nonzero(a['mask'] & (flat_face < 0)))} for a in local_apertures],
            'pieces': rows, 'pairs': pairs, 'zero_overlap_pairs': zero_pairs,
            'possible_component_pairs': possible_pairs, 'aperture_pair_checks': aperture_pair_work,
            'limits': asdict(limits),
            'limitations': ['Apertures are image hypotheses, not clean optical-material samples or verified lens instances.',
                'All-opaque exact ownership and depth compatibility are hypothetical geometric visibility only.',
                'Component overlap/containment can arise from duplicated shells, hardware, pads, clip-ons or projected coincidence.',
                'Known domains describe prediction support, not semantic certainty; crop exterior remains unknown.',
                'This report selects no physical groups or materials and cannot certify unseen or subpixel geometry.']}
