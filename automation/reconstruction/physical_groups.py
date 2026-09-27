"""Competing physical-group partitions over complete source components.

Membership is inferred only from projected co-occupancy and depth agreement
measured in every supplied view, at an explicit ladder of depth tolerances.
Nothing here reads a material, a part name or an optical role. Aperture
interpretations supply per-branch role evidence for a group; they never decide
which components belong together. The output is a set of competing partitions
with complete source membership and visible failure cases, not a verified lens
identity, a lens count or an accepted material.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math

import numpy as np


QUANTILES = [0., .1, .5, .9, 1.]
RELATIONS = ('same_surface', 'separated_layers', 'partial_overlap', 'minor_coincident', 'minor_separated',
             'inconsistent_across_views', 'no_overlap')
ROLES = ('optical_candidate', 'non_optical_evidence', 'unresolved', 'unobserved')


@dataclass(frozen=True)
class PhysicalGroupPolicy:
    """One fixed, product-independent policy. Every number is reported."""

    depth_tolerance_ladder: tuple = (.005, .01, .02, .04, .08)
    containment_fraction: float = .9
    depth_agreement_fraction: float = .9
    minor_share: float = .05
    # A group is an optical candidate when, pooled over the views that see it,
    # at least this share of its known pixels lies inside the aperture ...
    optical_inside_fraction: float = .8
    # ... no single view puts less than this share inside (a view whose camera
    # or geometry is off for this part can lower the pooled share, not veto it) ...
    optical_inside_floor: float = .5
    # ... and in every view it covers at least this share of the aperture blobs
    # it lands in (weighted by where its inside pixels fall), so a pad inside a
    # lens blob stays minor while a foreshortened far lens is not penalized for
    # the other lens's blob.
    optical_cover_fraction: float = .25
    # Two components that share an interior-contact patch whose boundary their
    # outer surfaces continue across (this share of the shared boundary edges
    # within the angle, given at least this many edges) are one body the
    # provider split cut in two: a reunited membership hypothesis is added and
    # the composition rank decides between the readings.
    cut_continuity_fraction: float = .75
    cut_continuity_angle_degrees: float = 20.
    cut_continuity_minimum_edges: int = 8

    def __post_init__(self):
        ladder = self.depth_tolerance_ladder
        floor = self.optical_inside_floor
        if isinstance(floor, bool) or not isinstance(floor, (int, float)) or not math.isfinite(floor) or not 0 < floor <= self.optical_inside_fraction <= 1:
            raise ValueError('optical_inside_floor must lie in (0, optical_inside_fraction] with optical_inside_fraction <= 1')
        if (not isinstance(ladder, tuple) or not ladder or len(ladder) > 16
                or any(isinstance(r, bool) or not isinstance(r, (int, float)) or not math.isfinite(r) or r <= 0 for r in ladder)
                or any(b <= a for a, b in zip(ladder, ladder[1:]))):
            raise ValueError('depth_tolerance_ladder must be a strictly increasing tuple of positive tolerances')
        angle = self.cut_continuity_angle_degrees
        if isinstance(angle, bool) or not isinstance(angle, (int, float)) or not 0 < angle < 90:
            raise ValueError('cut_continuity_angle_degrees must lie in (0, 90)')
        if type(self.cut_continuity_minimum_edges) is not int or self.cut_continuity_minimum_edges < 1:
            raise ValueError('cut_continuity_minimum_edges must be a positive integer')
        for name in ('containment_fraction', 'depth_agreement_fraction', 'minor_share',
                     'optical_inside_fraction', 'optical_cover_fraction', 'cut_continuity_fraction'):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value < 1:
                raise ValueError(f'{name} must lie strictly between zero and one')


@dataclass(frozen=True)
class PhysicalGroupLimits:
    maximum_components: int = 1024
    maximum_views: int = 32
    maximum_pixels_per_view: int = 262_144
    maximum_component_pixels: int = 8_000_000
    maximum_candidate_pairs: int = 200_000
    maximum_interpretations_per_view: int = 64
    maximum_branches: int = 64


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _hash(array):
    value = np.ascontiguousarray(array)
    prefix = _json({'dtype': value.dtype.str, 'shape': list(value.shape)})
    return hashlib.sha256(prefix+value.tobytes()).hexdigest()


def _ratio(numerator, denominator):
    return float(numerator/denominator) if denominator else None


def _mask(value, shape, name):
    array = np.asarray(value)
    if array.dtype != np.bool_ or array.shape != tuple(shape):
        raise ValueError(f'{name} must be a boolean array on the view grid')
    return array.ravel()


def _views(views, count, limits):
    if not isinstance(views, list) or not 1 <= len(views) <= limits.maximum_views:
        raise ValueError('A bounded nonempty view list is required')
    captured, ids, events = [], set(), 0
    for view in views:
        if not isinstance(view, dict) or set(view) != {'id', 'shape', 'component_pixels', 'component_depths',
                                                        'interpretations', 'provenance'}:
            raise ValueError('View requires id, shape, component_pixels, component_depths, interpretations, provenance')
        identifier, shape = view['id'], view['shape']
        if not isinstance(identifier, str) or not identifier or identifier in ids:
            raise ValueError('Distinct nonempty view IDs required')
        ids.add(identifier)
        if (not isinstance(shape, (list, tuple)) or len(shape) != 2
                or any(type(n) is not int or n < 1 for n in shape) or shape[0]*shape[1] > limits.maximum_pixels_per_view):
            raise ValueError('View shape must be a bounded positive [height, width]')
        shape = tuple(shape); pixels = shape[0]*shape[1]
        px, dz = view['component_pixels'], view['component_depths']
        if not isinstance(px, list) or not isinstance(dz, list) or len(px) != count or len(dz) != count:
            raise ValueError('Every view must supply one pixel and one depth vector per component')
        pixel_arrays, depth_arrays = [], []
        for p, d in zip(px, dz):
            p, d = np.asarray(p), np.asarray(d)
            if (p.ndim != 1 or p.dtype.kind not in 'iu' or d.shape != p.shape or d.dtype.kind != 'f'
                    or (len(p) and (p[0] < 0 or p[-1] >= pixels or np.any(p[1:] <= p[:-1])))
                    or not np.isfinite(d).all()):
                raise ValueError('Component pixels must be sorted unique bounded integers with finite depths')
            events += len(p)
            if events > limits.maximum_component_pixels:
                raise ValueError('Component pixel-event capacity exceeded; no component may be omitted')
            pixel_arrays.append(p.astype(np.int64, copy=False)); depth_arrays.append(d.astype(float, copy=False))
        interpretations, seen = [], set()
        if not isinstance(view['interpretations'], list) or len(view['interpretations']) > limits.maximum_interpretations_per_view:
            raise ValueError('Bounded interpretation list required')
        for item in view['interpretations']:
            if not isinstance(item, dict) or set(item) != {'id', 'mask', 'known_domain', 'provenance'}:
                raise ValueError('Interpretation requires id, mask, known_domain and provenance')
            if not isinstance(item['id'], str) or not item['id'] or item['id'] in seen:
                raise ValueError('Unique nonempty interpretation IDs required per view')
            seen.add(item['id'])
            mask, known = _mask(item['mask'], shape, 'mask'), _mask(item['known_domain'], shape, 'known_domain')
            if np.any(mask & ~known):
                raise ValueError('Interpretation positives must lie inside the known domain')
            if not isinstance(item['provenance'], dict) or not item['provenance']:
                raise ValueError('Interpretation provenance required')
            interpretations.append({'id': item['id'], 'mask': mask, 'known': known, 'pixels': int(mask.sum()),
                                    'provenance': json.loads(_json(item['provenance']))})
        if not isinstance(view['provenance'], dict) or not view['provenance']:
            raise ValueError('View provenance required')
        captured.append({'id': identifier, 'shape': shape, 'pixels': pixels, 'component_pixels': pixel_arrays,
                         'component_depths': depth_arrays, 'interpretations': interpretations,
                         'provenance': json.loads(_json(view['provenance']))})
    return captured


def _branches(branches, views, limits):
    if not isinstance(branches, list) or len(branches) > limits.maximum_branches:
        raise ValueError('Bounded branch list required')
    lookup = {v['id']: {i['id'] for i in v['interpretations']} for v in views}
    result, ids = [], set()
    for branch in branches:
        if not isinstance(branch, dict) or set(branch) != {'id', 'interpretation_by_view'}:
            raise ValueError('Branch requires id and interpretation_by_view')
        if not isinstance(branch['id'], str) or not branch['id'] or branch['id'] in ids:
            raise ValueError('Unique nonempty branch IDs required')
        ids.add(branch['id'])
        mapping = branch['interpretation_by_view']
        if (not isinstance(mapping, dict) or set(mapping) != set(lookup)
                or any(mapping[v] not in lookup[v] for v in lookup)):
            raise ValueError('Every branch must name one existing interpretation per view')
        result.append({'id': branch['id'], 'interpretation_by_view': dict(mapping)})
    return result


def _candidate_pairs(view, count, limits):
    """Pairs whose footprints share a pixel, found through packed membership bits."""
    width = (count+7)//8
    packed = np.zeros((view['pixels'], width), np.uint8)
    for component, pixels in enumerate(view['component_pixels']):
        if len(pixels):
            packed[pixels, component//8] |= np.uint8(1 << (component % 8))
    pairs = set()
    for component, pixels in enumerate(view['component_pixels']):
        if not len(pixels):
            continue
        union = np.bitwise_or.reduce(packed[pixels], axis=0)
        others = np.flatnonzero(np.unpackbits(union, bitorder='little')[:count])
        for other in others:
            if other > component:
                pairs.add((component, int(other)))
                if len(pairs) > limits.maximum_candidate_pairs:
                    raise ValueError('Candidate component-pair capacity exceeded')
    return pairs


def _pair_view(view, left, right, ladder):
    lp, rp = view['component_pixels'][left], view['component_pixels'][right]
    overlap, li, ri = np.intersect1d(lp, rp, assume_unique=True, return_indices=True)
    row = {'view_id': view['id'], 'left_pixels': len(lp), 'right_pixels': len(rp), 'overlap_pixels': len(overlap),
           'left_contained_fraction': _ratio(len(overlap), len(lp)),
           'right_contained_fraction': _ratio(len(overlap), len(rp)),
           'iou': _ratio(len(overlap), len(lp)+len(rp)-len(overlap)),
           'size_share': _ratio(min(len(lp), len(rp)), max(len(lp), len(rp)))}
    if not len(overlap):
        row.update(signed_quantiles=None, absolute_quantiles=None, left_nearer_pixels=0, right_nearer_pixels=0,
                   depth_agreement_by_rung=[None]*len(ladder))
        return row
    delta = view['component_depths'][right][ri]-view['component_depths'][left][li]
    absolute = np.abs(delta)
    row.update(signed_quantiles=np.quantile(delta, QUANTILES).tolist(),
               absolute_quantiles=np.quantile(absolute, QUANTILES).tolist(),
               left_nearer_pixels=int(np.count_nonzero(delta > 0)), right_nearer_pixels=int(np.count_nonzero(delta < 0)),
               depth_agreement_by_rung=[float(np.count_nonzero(absolute <= r)/len(delta)) for r in ladder])
    return row


def _classify(rows, policy):
    """One relation per rung from the views where both components project."""
    ladder = policy.depth_tolerance_ladder
    both = [r for r in rows if r['left_pixels'] and r['right_pixels']]
    if not both or all(r['overlap_pixels'] == 0 for r in both):
        return ['no_overlap']*len(ladder), [False]*len(ladder)
    if any(r['overlap_pixels'] == 0 for r in both):
        return ['inconsistent_across_views']*len(ladder), [False]*len(ladder)
    contained = all(max(r['left_contained_fraction'], r['right_contained_fraction']) >= policy.containment_fraction for r in both)
    if not contained:
        return ['partial_overlap']*len(ladder), [False]*len(ladder)
    minor = min(r['size_share'] for r in both) < policy.minor_share
    relations, merges = [], []
    for index in range(len(ladder)):
        agrees = all(r['depth_agreement_by_rung'][index] >= policy.depth_agreement_fraction for r in both)
        if minor:
            # A minor piece never merges through the cluster graph; a coincident
            # one may be attached afterwards to exactly one neighbouring group.
            relations.append('minor_coincident' if agrees else 'minor_separated'); merges.append(False)
        elif agrees:
            relations.append('same_surface'); merges.append(True)
        else:
            relations.append('separated_layers'); merges.append(False)
    return relations, merges


def _minor_side(rows):
    """The component with the smaller footprint in every shared view, else None."""
    both = [r for r in rows if r['left_pixels'] and r['right_pixels']]
    if all(r['left_pixels'] < r['right_pixels'] for r in both):
        return 'left'
    if all(r['right_pixels'] < r['left_pixels'] for r in both):
        return 'right'
    return None


def _attach_minor(labels, pairs, rung):
    """Attach singleton minor pieces to one coincident group; ambiguity stays unattached."""
    labels = labels.copy()
    counts = np.bincount(labels, minlength=int(labels.max())+1)
    candidates = {}
    for pair in pairs:
        if pair['relation_by_rung'][rung] != 'minor_coincident' or pair['minor_side'] is None:
            continue
        small, large = ((pair['left_component'], pair['right_component']) if pair['minor_side'] == 'left'
                        else (pair['right_component'], pair['left_component']))
        candidates.setdefault(small, set()).add(int(labels[large]))
    attachments, ambiguous = [], []
    for small, targets in sorted(candidates.items()):
        targets.discard(int(labels[small]))
        if counts[labels[small]] != 1:
            continue
        if len(targets) == 1:
            attachments.append((small, next(iter(targets))))
        elif len(targets) > 1:
            ambiguous.append({'component_id': small, 'candidate_group_labels': sorted(targets)})
    for small, target in attachments:
        labels[small] = target
    _, labels = np.unique(labels, return_inverse=True)
    return labels, [a[0] for a in attachments], ambiguous


def _union_find(count, edges):
    parent = np.arange(count)

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]; i = parent[i]
        return i
    for a, b in edges:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)
    roots = np.array([find(i) for i in range(count)])
    _, labels = np.unique(roots, return_inverse=True)
    return labels


def _group_footprint(view, members):
    pixels = np.unique(np.concatenate([view['component_pixels'][m] for m in members])) if members else np.empty(0, np.int64)
    if not len(pixels):
        return pixels, np.empty(0, float)
    depth = np.full(view['pixels'], np.inf)
    for m in members:
        p = view['component_pixels'][m]
        if len(p):
            np.minimum.at(depth, p, view['component_depths'][m])
    return pixels, depth[pixels]


def _aperture_blobs(view, item):
    """Connected components of one aperture mask, labelled once per interpretation."""
    if '_blob_labels' not in item:
        from scipy import ndimage
        labels, _ = ndimage.label(np.asarray(item['mask']).reshape(view['shape']))
        labels = labels.ravel()
        item['_blob_labels'] = labels
        item['_blob_pixels'] = np.bincount(labels)
    return item['_blob_labels'], item['_blob_pixels']


def _blob_cover(view, item, pixels, inside):
    """Inside-pixel-weighted coverage of the aperture blobs the group lands in."""
    if not inside:
        return None
    labels, sizes = _aperture_blobs(view, item)
    hit = labels[pixels]
    hit = hit[hit > 0]
    counts = np.bincount(hit, minlength=len(sizes))
    touched = np.flatnonzero(counts)
    return float(np.sum((counts[touched] / inside) * np.minimum(1., counts[touched] / sizes[touched])))


def _group_evidence(view, pixels):
    rows = []
    for item in view['interpretations']:
        known = int(item['known'][pixels].sum()); inside = int(item['mask'][pixels].sum())
        rows.append({'interpretation_id': item['id'], 'group_known_pixels': known, 'group_unknown_pixels': len(pixels)-known,
                     'aperture_pixels': item['pixels'], 'intersection_pixels': inside,
                     'group_inside_fraction': _ratio(inside, known), 'aperture_covered_fraction': _ratio(inside, item['pixels']),
                     'blob_covered_fraction': _blob_cover(view, item, pixels, inside),
                     'iou': _ratio(inside, known+item['pixels']-inside)})
    return rows


def _role(group_views, branch, policy):
    """Role evidence under one branch: per view, the named interpretation.

    The inside share is pooled over the views that see the group (pixel
    weighted) and must reach ``optical_inside_fraction``; no view may fall
    under ``optical_inside_floor``; and in every view the group must cover
    ``optical_cover_fraction`` of the aperture blobs it lands in.
    """
    per_view, decisions, inside_pixels, known_pixels = [], [], 0, 0
    for row in group_views:
        chosen = branch['interpretation_by_view'][row['view_id']]
        evidence = next((e for e in row['interpretations'] if e['interpretation_id'] == chosen), None)
        if evidence is None or evidence['group_inside_fraction'] is None:
            per_view.append({'view_id': row['view_id'], 'interpretation_id': chosen, 'decision': 'no_known_support'})
            continue
        inside, cover = evidence['group_inside_fraction'], evidence.get('blob_covered_fraction')
        inside_pixels += evidence['intersection_pixels']; known_pixels += evidence['group_known_pixels']
        if inside < policy.optical_inside_floor:
            decision = 'outside_aperture'
        elif cover is not None and cover >= policy.optical_cover_fraction:
            decision = 'optical_support'
        else:
            decision = 'contained_minor'
        decisions.append(decision)
        per_view.append({'view_id': row['view_id'], 'interpretation_id': chosen, 'decision': decision,
                         'group_inside_fraction': inside, 'aperture_covered_fraction': evidence['aperture_covered_fraction'],
                         'blob_covered_fraction': cover})
    pooled = _ratio(inside_pixels, known_pixels)
    if not decisions:
        role = 'unresolved'
    elif any(d == 'outside_aperture' for d in decisions) or pooled < policy.optical_inside_fraction:
        role = 'non_optical_evidence'
    elif all(d == 'optical_support' for d in decisions):
        role = 'optical_candidate'
    else:
        role = 'unresolved'
    return {'branch_id': branch['id'], 'role': role, 'pooled_inside_fraction': pooled, 'views': per_view}


def infer_physical_groups(components, views, branches, *, support=None, body_edges=None,
                          policy=PhysicalGroupPolicy(), limits=PhysicalGroupLimits()):
    """Return ``{'report': JSON, 'hypotheses': [arrays]}`` for competing partitions.

    ``components`` lists one record per canonical component in order, each with
    ``component_id`` and ``source_face_count`` plus any caller provenance.
    ``views`` supply, per component, sorted unique pixel indices and matching
    depths in one common reference unit (the caller binds that unit). Each view
    carries aperture interpretations; ``branches`` name one interpretation per
    view. ``support`` optionally lists earlier support-union branch results.

    Two components merge at a rung when, in every view where both project, they
    overlap, the smaller footprint is mostly inside the larger, neither is a
    minor share of the other, and most overlap pixels agree in depth within the
    rung tolerance. Groups are connected components of those merges. Distinct
    partitions across rungs become competing hypotheses; roles are evaluated per
    branch and never feed back into membership. ``body_edges`` name component
    pairs the geometry says are one body cut by the provider split; every
    distinct partition also gets a reunited variant with those pairs merged.
    """
    if not isinstance(policy, PhysicalGroupPolicy) or not isinstance(limits, PhysicalGroupLimits):
        raise ValueError('policy and limits must be the declared dataclasses')
    if any(type(v) is not int or v < 1 for v in asdict(limits).values()):
        raise ValueError('Positive integer limits required')
    if (not isinstance(components, list) or not 1 <= len(components) <= limits.maximum_components
            or any(not isinstance(c, dict) or c.get('component_id') != i or type(c.get('source_face_count')) is not int
                   or c['source_face_count'] < 1 for i, c in enumerate(components))):
        raise ValueError('Complete canonical component records with positive face counts are required')
    count = len(components)
    reunions = []
    for pair in (body_edges or []):
        if (not isinstance(pair, (list, tuple)) or len(pair) != 2 or any(type(c) is not int or not 0 <= c < count for c in pair)
                or pair[0] == pair[1]):
            raise ValueError('body_edges must pair two distinct component ids within the inventory')
        reunions.append((min(pair), max(pair)))
    reunions = sorted(set(reunions))
    captured = _views(views, count, limits)
    branch_rows = _branches(branches, captured, limits)
    ladder = policy.depth_tolerance_ladder
    projected = np.array([[len(v['component_pixels'][c]) for v in captured] for c in range(count)], np.int64)
    observed = projected.sum(axis=1) > 0
    pair_ids = set()
    for view in captured:
        pair_ids |= _candidate_pairs(view, count, limits)
    pairs, edges_by_rung = [], [[] for _ in ladder]
    for left, right in sorted(pair_ids):
        rows = [_pair_view(view, left, right, ladder) for view in captured]
        relations, merges = _classify(rows, policy)
        for index, merge in enumerate(merges):
            if merge:
                edges_by_rung[index].append((left, right))
        pairs.append({'left_component': left, 'right_component': right, 'views': rows,
                      'views_both_projected': sum(1 for r in rows if r['left_pixels'] and r['right_pixels']),
                      'views_overlapping': sum(1 for r in rows if r['overlap_pixels']),
                      'relation_by_rung': relations, 'merge_by_rung': merges,
                      'minor_side': _minor_side(rows) if 'minor_coincident' in relations or 'minor_separated' in relations else None,
                      'rung_dependent': len(set(relations)) > 1})
    pair_lookup = {(p['left_component'], p['right_component']): p for p in pairs}
    distinct, hypotheses = {}, []
    for index, edges in enumerate(edges_by_rung):
        labels, attached, ambiguous = _attach_minor(_union_find(count, edges), pairs, index)
        key = labels.tobytes()
        if key in distinct:
            hypotheses[distinct[key]]['rungs'].append(index)
            continue
        distinct[key] = len(hypotheses)
        hypotheses.append({'index': len(hypotheses), 'rungs': [index], 'labels': labels,
                           'attached_minor_components': attached, 'ambiguous_minor_attachments': ambiguous})
    if reunions:
        # A reunited reading of every distinct partition: the cut pairs merged on top of that rung's merges.
        for hypothesis in list(hypotheses):
            rung = hypothesis['rungs'][0]
            labels, attached, ambiguous = _attach_minor(_union_find(count, list(edges_by_rung[rung]) + reunions), pairs, rung)
            key = labels.tobytes()
            if key in distinct:
                continue
            distinct[key] = len(hypotheses)
            hypotheses.append({'index': len(hypotheses), 'rungs': list(hypothesis['rungs']), 'labels': labels,
                               'attached_minor_components': attached, 'ambiguous_minor_attachments': ambiguous,
                               'reunited_from': hypothesis['index']})
    support_rows = []
    if support is not None:
        if not isinstance(support, list):
            raise ValueError('support must be a list of earlier support-union branch records')
        for row in support:
            if (not isinstance(row, dict) or not isinstance(row.get('id'), str)
                    or not isinstance(row.get('selected_component_ids'), list)
                    or any(type(c) is not int or not 0 <= c < count for c in row['selected_component_ids'])):
                raise ValueError('Support rows require id and selected_component_ids within the component inventory')
            support_rows.append({'id': row['id'], 'selected': set(row['selected_component_ids'])})
    report_hypotheses, array_hypotheses = [], []
    for hypothesis in hypotheses:
        labels = hypothesis['labels']
        groups, arrays = [], []
        for group_index in range(int(labels.max())+1):
            members = np.flatnonzero(labels == group_index).tolist()
            is_observed = bool(observed[members].any())
            core = [m for m in members if m not in hypothesis['attached_minor_components']] or members
            group_id = f'group-{min(core):04d}'
            footprints = {}
            per_view = []
            for view_index, view in enumerate(captured):
                present = [m for m in members if projected[m, view_index]]
                pixels, depth = _group_footprint(view, present)
                footprints[view['id']] = (pixels, depth)
                per_view.append({'view_id': view['id'], 'projected_pixels': len(pixels),
                                 'members_projected': present,
                                 'interpretations': _group_evidence(view, pixels) if len(pixels) else []})
            roles = [_role(per_view, branch, policy) for branch in branch_rows] if is_observed else []
            role_set = sorted({r['role'] for r in roles})
            consensus = ('unobserved' if not is_observed else role_set[0] if len(role_set) == 1
                         else 'branch_dependent')
            support_evidence = [{'support_branch_id': s['id'],
                                 'selected_members': sorted(m for m in members if m in s['selected']),
                                 'selected_fraction_of_observed_members': _ratio(sum(1 for m in members if m in s['selected'] and observed[m]),
                                                                                  int(observed[members].sum()))}
                                for s in support_rows]
            attached = [m for m in members if m in hypothesis['attached_minor_components']]
            bridged = []
            for a_index, a in enumerate(members):
                for b in members[a_index+1:]:
                    if not observed[a] or not observed[b] or a in attached or b in attached:
                        continue
                    pair = pair_lookup.get((a, b))
                    shared = bool(np.any(projected[a] & projected[b])) if pair is None else pair['views_both_projected'] > 0
                    if shared and (pair is None or pair['views_overlapping'] == 0):
                        bridged.append([a, b])
            groups.append({'group_id': group_id, 'members': members, 'observed': is_observed,
                           'source_face_count': int(sum(components[m]['source_face_count'] for m in members)),
                           'attached_minor_members': attached,
                           'member_pairs_without_direct_overlap': bridged,
                           'views': per_view, 'roles_by_branch': roles, 'role_consensus': consensus,
                           'support_union_evidence': support_evidence})
            arrays.append({'group_id': group_id, 'members': members, 'footprints': footprints})
        # Group-level layering: pairs of observed groups sharing pixels in some view.
        layering = []
        for a in range(len(groups)):
            if not groups[a]['observed']:
                continue
            for b in range(a+1, len(groups)):
                if not groups[b]['observed']:
                    continue
                rows = []
                for view in captured:
                    pa, da = arrays[a]['footprints'][view['id']]; pb, db = arrays[b]['footprints'][view['id']]
                    if not len(pa) or not len(pb):
                        continue
                    overlap, ia, ib = np.intersect1d(pa, pb, assume_unique=True, return_indices=True)
                    if not len(overlap):
                        continue
                    delta = db[ib]-da[ia]
                    rows.append({'view_id': view['id'], 'overlap_pixels': len(overlap),
                                 'left_contained_fraction': _ratio(len(overlap), len(pa)),
                                 'right_contained_fraction': _ratio(len(overlap), len(pb)),
                                 'signed_quantiles': np.quantile(delta, QUANTILES).tolist(),
                                 'left_nearer_pixels': int(np.count_nonzero(delta > 0)),
                                 'right_nearer_pixels': int(np.count_nonzero(delta < 0)),
                                 'order': ('left_nearer' if np.all(delta > 0) else 'right_nearer' if np.all(delta < 0) else 'interleaved')})
                if rows:
                    layering.append({'left_group': groups[a]['group_id'], 'right_group': groups[b]['group_id'], 'views': rows})
        minor = [{'left_component': p['left_component'], 'right_component': p['right_component'],
                  'left_group': groups[int(labels[p['left_component']])]['group_id'],
                  'right_group': groups[int(labels[p['right_component']])]['group_id'],
                  'minor_side': p['minor_side'], 'relation_by_rung': p['relation_by_rung']}
                 for p in pairs if p['minor_side'] is not None
                 and labels[p['left_component']] != labels[p['right_component']]]
        report_hypotheses.append({'index': hypothesis['index'], 'rungs': hypothesis['rungs'],
                                  'depth_tolerances': [ladder[i] for i in hypothesis['rungs']],
                                  'cut_reunion': 'reunited_from' in hypothesis, 'reunited_from': hypothesis.get('reunited_from'),
                                  'group_count': len(groups), 'observed_group_count': sum(g['observed'] for g in groups),
                                  'component_group_labels_sha256': _hash(labels),
                                  'groups': groups, 'group_layering': layering, 'unattached_minor_pairs': minor,
                                  'attached_minor_components': hypothesis['attached_minor_components'],
                                  'ambiguous_minor_attachments': hypothesis['ambiguous_minor_attachments'],
                                  'role_counts': {role: sum(1 for g in groups if g['role_consensus'] == role)
                                                  for role in (*ROLES, 'branch_dependent')}})
        array_hypotheses.append({'index': hypothesis['index'], 'labels': labels, 'groups': arrays})
    component_rows = [{'component_id': c, 'observed': bool(observed[c]),
                       'projected_pixels_by_view': {v['id']: int(projected[c, k]) for k, v in enumerate(captured)},
                       'group_by_hypothesis': [report_hypotheses[h]['groups'][int(array_hypotheses[h]['labels'][c])]['group_id']
                                               for h in range(len(hypotheses))],
                       **{k: v for k, v in components[c].items() if k not in ('component_id',)}}
                      for c in range(count)]
    report = {'schema_version': 1, 'method': 'projected_coincidence_group_ladder_v1', 'accepted': False,
              'quality_verdict': 'unmeasured', 'semantic_identity': 'not_inferred', 'physical_groups': 'hypotheses_only',
              'policy': asdict(policy), 'limits': asdict(limits), 'depth_tolerance_ladder': list(ladder),
              'depth_units': 'caller-declared common reference unit shared by every view',
              'component_count': count, 'observed_component_count': int(observed.sum()),
              'unobserved_component_ids': np.flatnonzero(~observed).tolist(),
              'views': [{'id': v['id'], 'shape': list(v['shape']),
                         'interpretations': [{'id': i['id'], 'aperture_pixels': i['pixels'], 'known_pixels': int(i['known'].sum()),
                                              'provenance': i['provenance']} for i in v['interpretations']],
                         'provenance': v['provenance']} for v in captured],
              'branches': branch_rows, 'components': component_rows, 'pairs': pairs, 'body_edges': [list(e) for e in reunions],
              'candidate_pair_count': len(pairs), 'rung_dependent_pair_count': sum(p['rung_dependent'] for p in pairs),
              'hypotheses': report_hypotheses, 'hypothesis_count': len(report_hypotheses),
              'relation_scope': 'Relations use complete per-component first hits without occlusion; depth agreement is measured at overlap pixels only.',
              'membership_scope': 'Merges require overlap in every shared view; transitive closure of same-surface merges forms a group. Separated layers and partial overlaps never merge. A singleton minor piece coincident with exactly one group is attached to it as an effective-response member, never as a bridge between groups. Component pairs the caller names as one cut body add a reunited variant of every partition.',
              'role_scope': 'Roles are per-branch aperture evidence for a group union; they do not change membership and are not material identities.',
              'limitations': ['Front/back surfaces of one solid lens and a lens with a coincident clip-on are indistinguishable by depth alone; the ladder exposes that as rung dependence.',
                              'A fused frame/lens component cannot be split by this stage; it appears as a group with mixed or non-optical role evidence.',
                              'Unobserved components remain unknown singletons; they are never discarded or assigned by proximity.',
                              'Cameras and articulation are frozen inputs; disagreement between views may come from camera or geometry error.',
                              'Coarse and SAM interpretations are competing branches, not independent votes; branch-dependent roles remain unresolved.']}
    _json(report)
    return {'report': report, 'hypotheses': array_hypotheses}


def partition_declarations_for_hypothesis(hypothesis, component_table, primitive_labels, *,
                                          source_sha256, declared_groups, provenance, interior_faces=None):
    """Complete face partitions plus piece groups for the existing exporters.

    ``component_table`` is the canonical component ledger with source bindings
    and local component IDs; ``primitive_labels`` maps each ``(node, mesh,
    primitive)`` binding to its complete local component label array. Every
    source face of a touched primitive lands in exactly one piece: one piece
    per declared group within that primitive plus one explicit remainder piece.
    Primitives without a declared group are omitted and stay unchanged.
    Undeclared groups keep their source material; nothing is dropped.
    ``interior_faces`` maps a binding to a boolean mask over its faces: those
    faces leave every group piece and form one ``interior_contact`` piece per
    primitive, even in primitives without a declared group.
    """
    if not isinstance(declared_groups, list) or any(not isinstance(g, str) for g in declared_groups):
        raise ValueError('declared_groups must list group IDs')
    ordered = {g['group_id']: g for g in hypothesis['groups']}
    if len(set(declared_groups)) != len(declared_groups) or any(g not in ordered for g in declared_groups):
        raise ValueError('Declared groups must be distinct IDs of this hypothesis')
    by_binding = {}
    for row in component_table:
        key = tuple(row['source_binding'][k] for k in ('node_index', 'mesh_index', 'primitive_index'))
        by_binding.setdefault(key, []).append(row)
    partitions, piece_groups = [], {g: [] for g in declared_groups}
    for key, rows in sorted(by_binding.items()):
        labels = np.asarray(primitive_labels[key])
        if labels.ndim != 1 or labels.dtype.kind not in 'iu' or len(labels) != sum(r['source_face_count'] for r in rows):
            raise ValueError('Primitive labels must cover exactly its recorded source faces')
        local_to_global = {r['local_component_id']: r['component_id'] for r in rows}
        owner = np.full(len(labels), -1, np.int64)
        pieces = []
        interior = np.zeros(len(labels), bool)
        if interior_faces is not None and key in interior_faces:
            interior = np.asarray(interior_faces[key], bool)
            if interior.shape != labels.shape:
                raise ValueError('Interior face masks must cover exactly the primitive faces')
        for ordinal, group_id in enumerate(declared_groups):
            members = set(ordered[group_id]['members'])
            local = [local_id for local_id, global_id in local_to_global.items() if global_id in members]
            if not local:
                continue
            faces = np.flatnonzero(np.isin(labels, local) & ~interior)
            if not len(faces):
                continue
            owner[faces] = ordinal
            piece_id = f'p{key[0]}-{key[1]}-{key[2]}-{group_id}'
            # The hypothesis, not a source material or name, declares the piece
            # optical; downstream region priors read this role from the piece.
            pieces.append({'id': piece_id, 'source_face_indices': faces.tolist(), 'declared_role': 'optical'})
            piece_groups[group_id].append(piece_id)
        if interior.any():
            faces = np.flatnonzero(interior)
            owner[faces] = len(declared_groups)
            pieces.append({'id': f'p{key[0]}-{key[1]}-{key[2]}-interior', 'source_face_indices': faces.tolist(),
                           'declared_role': 'interior_contact'})
        if not pieces:
            # No declared group touches this primitive: leave it unpartitioned
            # and unchanged rather than re-emitting an identical copy.
            continue
        remainder = np.flatnonzero(owner < 0)
        if len(remainder):
            pieces.append({'id': f'p{key[0]}-{key[1]}-{key[2]}-remainder', 'source_face_indices': remainder.tolist()})
        partitions.append({'source_binding': dict(zip(('node_index', 'mesh_index', 'primitive_index'), key)), 'pieces': pieces})
    groups = [{'group_id': g, 'piece_ids': ids} for g, ids in piece_groups.items() if ids]
    missing = [g for g, ids in piece_groups.items() if not ids]
    if missing:
        raise ValueError('Declared groups without any source faces: '+', '.join(missing))
    declarations = {'schema_version': 1, 'source_sha256': source_sha256,
                    'provenance': {'method': 'projected_coincidence_group_ladder_v1 hypothesis partition',
                                   'hypothesis_index': hypothesis['index'], 'rungs': hypothesis['rungs'],
                                   'declared_groups': declared_groups, 'semantic_identity': 'not_inferred',
                                   **json.loads(_json(provenance))},
                    'partitions': partitions}
    return declarations, groups
