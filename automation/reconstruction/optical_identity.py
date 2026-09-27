"""Sparse, candidate-conditioned optical-instance evidence; never semantic IDs.

Masks, cameras, source triangles and entity memberships are supplied hypotheses.
This graph does not infer physical lenses from components/materials, alter meshes,
or establish calibration. A shared triangle is coarse association evidence, not a
surface-point correspondence: two masks can touch different parts of one face.
All decoder/prompt alternatives survive, including identical and empty masks.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
import hashlib
import itertools
import json
import re

import numpy as np


@dataclass(frozen=True)
class IdentityGraphLimits:
    max_faces: int = 2_000_000
    max_entities: int = 512
    max_views: int = 32
    max_regions: int = 1024
    max_alternatives: int = 2048
    max_entity_memberships: int = 8_000_000
    max_entity_pair_checks: int = 2_000_000
    max_diagnostics: int = 1_000_000
    max_mask_pixels: int = 256_000_000
    max_visibility_events: int = 32_000_000
    max_face_edges: int = 4_000_000
    max_cross_view_edges: int = 200_000
    max_cross_view_face_pairs: int = 20_000_000


def _json(value):
    try:
        return json.loads(json.dumps(value, sort_keys=True, allow_nan=False))
    except (TypeError, ValueError) as error:
        raise ValueError('Provenance must be finite JSON') from error


def identity_json_sha256(value) -> str:
    """Canonical JSON digest, matching the saved camera-receipt convention."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def identity_array_sha256(value: np.ndarray) -> str:
    """Hash exact array dtype, shape and contiguous bytes; no implicit conversion."""
    array = np.ascontiguousarray(value)
    prefix = json.dumps({'dtype': array.dtype.str, 'shape': array.shape}, sort_keys=True).encode()
    return hashlib.sha256(prefix + array.tobytes()).hexdigest()


def _keys(value, required, optional, label):
    if not isinstance(value, dict) or required-set(value) or set(value)-required-optional:
        raise ValueError(f'Invalid {label} fields')


def _slug(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}', value):
        raise ValueError('IDs must be safe nonempty identifiers')
    return value


def _sha(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{64}', value):
        raise ValueError('Expected lowercase SHA256')
    return value


def _provenance(value):
    if not isinstance(value, dict) or not value:
        raise ValueError('Nonempty provenance is required')
    return _json(value)


def _list(value, label):
    if not isinstance(value, list):
        raise ValueError(f'{label} must be a list')
    return value


def _counts(array):
    indices, counts = np.unique(array[array >= 0], return_counts=True)
    return dict(zip(map(int, indices), map(int, counts)))


def build_optical_identity_graph(*, source_sha256: str, face_count: int,
                                entities: list[dict], views: list[dict],
                                limits: IdentityGraphLimits = IdentityGraphLimits()) -> dict:
    """Build a JSON graph without selecting alternatives or accepting identities.

    Entity: {id, kind: primitive|connected_component|explicit_group,
      face_indices: integer vector, provenance: nonempty JSON}.
    View: {id, source_sha256, image_sha256, camera, camera_sha256,
      pixel_mapping: {native_size_xy, grid_size_xy, method, provenance},
      face_index: integer HxW or LxHxW, visibility_scope, regions,
      provenance, optional face_index_sha256}.
    Scope is {kind: full_scene_frontmost_opaque_geometry, provenance} for HxW;
    or {kind: per_entity_first_hit, entity_ids: one per layer, provenance} for
    LxHxW. The latter has no implicit opaque stop, layer composition or physical
    identity. Each layer must contain only faces from its stated prior entity.
    Region: {id, prior_entity_ids, provenance, alternatives}. Alternative:
    {id, mask: bool HxW, provenance, optional mask_sha256/predicted_quality}.

    Array digests are verified when supplied, otherwise computed. Source/image
    digests pin caller-reported identities; this API does not read their files.
    Camera JSON digest is checked. Mapping is declared, never silently resampled.
    Caps fail explicitly, never truncate observations. At least two views are
    required, but distinct IDs/cameras do not prove independent photographs.
    """
    source_sha256 = _sha(source_sha256)
    if not isinstance(limits, IdentityGraphLimits) or any(type(v) is not int or v < 1 for v in asdict(limits).values()):
        raise ValueError('Positive integer graph limits are required')
    if type(face_count) is not int or not 1 <= face_count <= limits.max_faces:
        raise ValueError('Invalid or over-capacity source face count')
    _list(entities, 'entities'); _list(views, 'views')
    if not 1 <= len(entities) <= limits.max_entities or not 2 <= len(views) <= limits.max_views:
        raise ValueError('Invalid or over-capacity entity/view count')
    entity_sets, entity_records, membership_count = {}, [], 0
    for entity in entities:
        _keys(entity, {'id', 'kind', 'face_indices', 'provenance'}, set(), 'entity')
        eid = _slug(entity['id'])
        faces = np.asarray(entity['face_indices'])
        if eid in entity_sets or entity['kind'] not in ('primitive', 'connected_component', 'explicit_group'):
            raise ValueError('Duplicate entity ID or unknown prior kind')
        if faces.ndim != 1 or not len(faces) or faces.dtype.kind not in 'iu' or np.any(faces >= face_count) or np.any(faces < 0):
            raise ValueError('Entity faces must be valid source indices')
        members = set(map(int, faces))
        if len(members) != len(faces):
            raise ValueError('Duplicate entity face membership')
        membership_count += len(members)
        if membership_count > limits.max_entity_memberships:
            raise ValueError('Entity membership capacity exceeded')
        entity_sets[eid] = members
        entity_records.append({'id': eid, 'kind': entity['kind'], 'face_indices': sorted(members),
                               'provenance': _provenance(entity['provenance']), 'identity': 'unverified_prior'})
    graph_views, nodes, node_faces, view_faces, inverted = [], [], {}, {}, defaultdict(list)
    diagnostics, regions_internal = [], []
    def diagnose(record):
        if len(diagnostics) >= limits.max_diagnostics:
            raise ValueError('Diagnostic capacity exceeded')
        diagnostics.append(record)
    totals = {'mask_pixels': 0, 'visibility_events': 0, 'face_edges': 0, 'entity_pair_checks': 0}
    for view in views:
        _keys(view, {'id', 'source_sha256', 'image_sha256', 'camera', 'camera_sha256',
                     'pixel_mapping', 'face_index', 'visibility_scope', 'regions', 'provenance'},
              {'face_index_sha256'}, 'view')
        vid = _slug(view['id'])
        if vid in view_faces or _sha(view['source_sha256']) != source_sha256:
            raise ValueError('Duplicate view ID or mismatched source identity')
        camera = _provenance(view['camera'])
        if _sha(view['camera_sha256']) != identity_json_sha256(camera):
            raise ValueError('Camera digest mismatch')
        field = np.asarray(view['face_index'])
        if field.dtype.kind not in 'iu' or field.ndim not in (2, 3) or min(field.shape) < 1 or np.any(field < -1) or np.any(field >= face_count):
            raise ValueError('Invalid source-face visibility array')
        digest = identity_array_sha256(field)
        if 'face_index_sha256' in view and _sha(view['face_index_sha256']) != digest:
            raise ValueError('Visibility digest mismatch')
        scope = view['visibility_scope']
        _keys(scope, {'kind', 'provenance'}, {'entity_ids'}, 'visibility scope')
        _provenance(scope['provenance'])
        if scope['kind'] == 'full_scene_frontmost_opaque_geometry':
            if field.ndim != 2 or 'entity_ids' in scope:
                raise ValueError('Full-scene first hit requires one HxW field')
        elif scope['kind'] == 'per_entity_first_hit':
            ids = _list(scope.get('entity_ids'), 'scope entity_ids')
            for eid in ids:
                _slug(eid)
            if field.ndim != 3 or len(ids) != field.shape[0] or len(set(ids)) != len(ids):
                raise ValueError('Per-entity first hit requires one distinct entity per layer')
            for index, eid in enumerate(ids):
                if eid not in entity_sets or not set(_counts(field[index])) <= entity_sets[eid]:
                    raise ValueError('First-hit layer contains a face outside its stated entity')
        else:
            raise ValueError('Unknown visibility scope')
        layers = field[None] if field.ndim == 2 else field
        h, w = field.shape[-2:]
        mapping = view['pixel_mapping']
        _keys(mapping, {'native_size_xy', 'grid_size_xy', 'method', 'provenance'}, set(), 'pixel mapping')
        for size in (mapping['native_size_xy'], mapping['grid_size_xy']):
            if not isinstance(size, list) or len(size) != 2 or any(type(n) is not int or n < 1 for n in size):
                raise ValueError('Pixel mapping sizes must be positive integer [width,height]')
        if mapping['grid_size_xy'] != [w, h] or not isinstance(mapping['method'], str) or not mapping['method']:
            raise ValueError('Visibility grid does not match declared pixel mapping')
        _provenance(mapping['provenance'])
        totals['visibility_events'] += field.size
        if totals['visibility_events'] > limits.max_visibility_events:
            raise ValueError('Visibility event capacity exceeded')
        visible = _counts(layers); view_faces[vid] = set(visible)
        graph_views.append({'id': vid, 'source_sha256': source_sha256, 'image_sha256': _sha(view['image_sha256']),
                            'camera': camera, 'camera_sha256': view['camera_sha256'], 'pixel_mapping': _json(mapping),
                            'visibility_scope': _json(scope), 'face_index_sha256': digest,
                            'visible_face_hits': [[f, c] for f, c in visible.items()],
                            'provenance': _provenance(view['provenance'])})
        region_ids, local_regions = set(), []
        for region in _list(view['regions'], 'regions'):
            if len(regions_internal) >= limits.max_regions:
                raise ValueError('Region capacity exceeded')
            _keys(region, {'id', 'prior_entity_ids', 'provenance', 'alternatives'}, set(), 'region')
            rid = _slug(region['id'])
            priors = _list(region['prior_entity_ids'], 'prior_entity_ids')
            for eid in priors:
                _slug(eid)
            if rid in region_ids or len(set(priors)) != len(priors) or any(e not in entity_sets for e in priors):
                raise ValueError('Duplicate region or invalid prior entity')
            region_ids.add(rid)
            alternative_ids, region_faces, union_mask = [], set(), np.zeros((h, w), bool)
            for alternative in _list(region['alternatives'], 'alternatives'):
                _keys(alternative, {'id', 'mask', 'provenance'}, {'mask_sha256', 'predicted_quality'}, 'alternative')
                aid = _slug(alternative['id']); key = f'{vid}/{rid}/{aid}'
                if aid in alternative_ids:
                    raise ValueError('Duplicate alternative ID')
                alternative_ids.append(aid)
                if len(nodes) >= limits.max_alternatives:
                    raise ValueError('Alternative capacity exceeded')
                mask = np.asarray(alternative['mask'])
                if mask.dtype != np.bool_ or mask.shape != (h, w):
                    raise ValueError('Masks must be boolean and exactly match the declared grid')
                mask_digest = identity_array_sha256(mask)
                if 'mask_sha256' in alternative and _sha(alternative['mask_sha256']) != mask_digest:
                    raise ValueError('Mask digest mismatch')
                totals['mask_pixels'] += mask.size
                if totals['mask_pixels'] > limits.max_mask_pixels:
                    raise ValueError('Mask pixel capacity exceeded')
                hits = _counts(layers[:, mask]); faces = set(hits); node_faces[key] = faces
                totals['face_edges'] += len(hits)
                if totals['face_edges'] > limits.max_face_edges:
                    raise ValueError('Sparse face-edge capacity exceeded')
                associations = []
                for eid, members in entity_sets.items():
                    touched = faces & members
                    if touched or eid in priors:
                        hit_pixels = int(np.count_nonzero(mask & np.isin(layers, list(touched)).any(axis=0))) if touched else 0
                        associations.append({'entity_id': eid, 'face_count': len(touched), 'pixel_count': hit_pixels,
                                             'hit_events': sum(hits[f] for f in touched), 'is_declared_prior': eid in priors})
                geometric_pixels = int(np.count_nonzero(mask & (layers >= 0).any(axis=0)))
                record = {'id': key, 'view_id': vid, 'region_id': rid, 'alternative_id': aid,
                          'mask_sha256': mask_digest, 'mask_pixels': int(mask.sum()),
                          'geometry_pixels': geometric_pixels, 'unexplained_pixels': int(mask.sum())-geometric_pixels,
                          'geometry_hit_events': sum(hits.values()), 'face_hits': [[f, c] for f, c in hits.items()],
                          'entity_associations': associations, 'provenance': _provenance(alternative['provenance']),
                          'identity': 'unverified', 'status': 'candidate_associations' if faces else 'unmeasured_geometry_association'}
                if 'predicted_quality' in alternative:
                    quality = alternative['predicted_quality']
                    if isinstance(quality, bool) or not isinstance(quality, (int, float)) or not np.isfinite(quality):
                        raise ValueError('Predicted mask quality must be finite')
                    record['predicted_quality'] = quality
                    record['quality_scope'] = 'mask-engine score, not semantic confidence'
                nodes.append(record); region_faces |= faces; union_mask |= mask
                for face in faces:
                    inverted[face].append((vid, key))
                positive = [a['entity_id'] for a in associations if a['face_count']]
                totals['entity_pair_checks'] += len(positive)*(len(positive)-1)//2
                if totals['entity_pair_checks'] > limits.max_entity_pair_checks:
                    raise ValueError('Entity-pair diagnostic work capacity exceeded')
                disjoint = [[a, b] for a, b in itertools.combinations(positive, 2) if entity_sets[a].isdisjoint(entity_sets[b])]
                if disjoint:
                    diagnose({'kind': 'region_spans_disjoint_entity_priors', 'alternative': key,
                                        'entity_pairs': disjoint, 'interpretation': 'merge_or_mask_geometry_camera_disagreement'})
                if priors and faces and not any(faces & entity_sets[e] for e in priors):
                    diagnose({'kind': 'declared_prior_association_conflict', 'alternative': key,
                                        'prior_entity_ids': list(priors), 'semantic_contradiction': 'unverified'})
            local_regions.append((rid, region_faces, union_mask))
            regions_internal.append({'view_id': vid, 'id': rid, 'prior_entity_ids': list(priors),
                                     'alternatives': [f'{vid}/{rid}/{a}' for a in alternative_ids],
                                     'provenance': _provenance(region['provenance']), 'selected_alternative': None})
        for eid, members in entity_sets.items():
            touched_regions = [(r, faces & members, mask) for r, faces, mask in local_regions if faces & members]
            if not (view_faces[vid] & members):
                diagnose({'kind': 'entity_unobserved_in_supplied_ray_domain', 'view_id': vid, 'entity_id': eid,
                                    'meaning': 'missing visibility, not negative optical identity'})
            elif not touched_regions:
                diagnose({'kind': 'visible_entity_without_region_association', 'view_id': vid, 'entity_id': eid})
            for (ra, fa, ma), (rb, fb, mb) in itertools.combinations(touched_regions, 2):
                intersection = int(np.count_nonzero(ma & mb)); union = int(np.count_nonzero(ma | mb))
                diagnose({'kind': 'multiple_regions_associate_with_entity', 'view_id': vid, 'entity_id': eid,
                                    'region_ids': [ra, rb], 'shared_faces': len(fa & fb),
                                    'exclusive_faces': [len(fa-fb), len(fb-fa)], 'alternative_union_mask_iou': intersection/union,
                                    'interpretation': 'split_or_duplicate_region_hypothesis; physical_piece_count_unverified'})
    pair_counts, pair_events = defaultdict(int), 0
    for records in inverted.values():
        by_view = defaultdict(list)
        for vid, key in records:
            by_view[vid].append(key)
        for va, vb in itertools.combinations(sorted(by_view), 2):
            for a, b in itertools.product(by_view[va], by_view[vb]):
                pair_events += 1
                if pair_events > limits.max_cross_view_face_pairs:
                    raise ValueError('Cross-view sparse association work capacity exceeded')
                pair_counts[(a, b)] += 1
                if len(pair_counts) > limits.max_cross_view_edges:
                    raise ValueError('Cross-view edge capacity exceeded')
    cross_views, view_pairs = [], []
    for va, vb in itertools.combinations(sorted(view_faces), 2):
        common = view_faces[va] & view_faces[vb]
        view_pairs.append({'view_ids': [va, vb], 'co_visible_source_face_count': len(common),
                           'status': 'candidate_conditioned_support' if common else 'unmeasured_no_common_visible_faces',
                           'independent_camera_or_image_evidence': 'unverified'})
    for (a, b), count in sorted(pair_counts.items()):
        va, vb = a.split('/')[0], b.split('/')[0]
        common = view_faces[va] & view_faces[vb]
        union = (node_faces[a] | node_faces[b]) & common
        shared = node_faces[a] & node_faces[b]
        cross_views.append({'alternatives': [a, b], 'shared_source_face_count': count,
                            'co_visible_face_union_count': len(union), 'co_visible_face_jaccard': count/len(union),
                            'shared_entity_face_counts': [{'entity_id': eid, 'face_count': len(shared & members)}
                                                          for eid, members in entity_sets.items() if shared & members],
                            'identity': 'unverified_candidate_link'})
    result = {'schema_version': 1, 'method': 'candidate_conditioned_optical_identity_graph_v1',
              'source_sha256': source_sha256, 'face_count': face_count, 'limits': asdict(limits),
              'entities': entity_records, 'views': graph_views, 'regions': regions_internal, 'alternatives': nodes,
              'cross_view_edges': cross_views, 'view_pair_coverage': view_pairs, 'diagnostics': diagnostics,
              'counts': {**totals, 'alternatives': len(nodes), 'cross_view_edges': len(cross_views),
                         'cross_view_face_pairs': pair_events}, 'accepted': False, 'semantic_identity': 'unverified',
              'physical_optical_instance_count': None,
              'limitations': ['Source entity membership, fitted cameras and prompted masks are mutually candidate-dependent.',
                             'A source primitive/component can contain several optical pieces or only part of one piece.',
                             'Full-scene opaque geometry hides optical backs; per-entity first hits do not model transparency.',
                             'Common triangle membership is not a barycentric surface-point correspondence.',
                             'Missing links, empty masks and no visibility do not establish absence or contradictory identity.',
                             'Positive associations and prior conflicts cannot distinguish mask, geometry and camera errors.',
                             'No mask alternative is selected and no physical grouping, material or AR acceptance is inferred.']}
    result['graph_sha256'] = identity_json_sha256(result)
    return result
