"""Source-bound manufactured-pair material hypotheses, never semantic facts.

Two distinct optical groups and an evidence-backed two-lens construction can
justify testing a common coating/density response. They do not establish it:
the fitter always retains the independent-material contrary configuration.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re


def _sha(value):
    return isinstance(value, str) and re.fullmatch('[a-f0-9]{64}', value) is not None


def validate_material_relations(value, *, group_ids, sources, photos, bindings=None,
                                group_priors=None):
    """Validate relations against image identity and, when supplied, asset bytes."""
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 2:
        raise ValueError('material_relations must contain at most two disjoint pairs')
    result, used, identifiers = [], set(), set()
    for raw in value:
        row = deepcopy(raw)
        if not isinstance(row, dict) or set(row) != {
                'relation_id', 'kind', 'group_ids', 'prepared_glb_sha256',
                'source_image_sha256', 'evidence', 'confidence'}:
            raise ValueError('Invalid material relation schema')
        if row['kind'] != 'shared_manufactured_pair' or row['confidence'] not in ('medium', 'high'):
            raise ValueError('Material relation requires a credible manufactured-pair hypothesis')
        rid, groups = row['relation_id'], row['group_ids']
        if not isinstance(rid, str) or not rid or len(rid) > 128 or rid in identifiers:
            raise ValueError('Material relation IDs must be distinct bounded text')
        if (not isinstance(groups, list) or len(groups) != 2 or any(not isinstance(g, str) for g in groups)
                or len(set(groups)) != 2 or set(groups) - set(group_ids) or used.intersection(groups)):
            raise ValueError('Material relations require disjoint pairs of known groups')
        source_list = row['source_image_sha256']
        if (not isinstance(source_list, list) or not source_list or len(source_list) > 48
                or any(not _sha(s) for s in source_list) or len(set(source_list)) != len(source_list)
                or set(source_list) - set(sources) or not _sha(row['prepared_glb_sha256'])):
            raise ValueError('Material relation source binding differs from appearance evidence')
        evidence = row['evidence']
        if not isinstance(evidence, list) or not 1 <= len(evidence) <= 24:
            raise ValueError('Material relation requires bounded source-photo evidence')
        for item in evidence:
            if (not isinstance(item, dict) or set(item) != {'photo_id', 'source_sha256', 'observation'}
                    or item['photo_id'] not in photos
                    or item['source_sha256'] != photos[item['photo_id']]['source_sha256']
                    or item['source_sha256'] not in source_list
                    or not isinstance(item['observation'], str) or not item['observation']
                    or len(item['observation']) > 6000):
                raise ValueError('Material relation evidence is not bound to its source photo')
        if bindings is not None:
            related = [bindings[g] for g in groups]
            if any(b['prepared_glb_sha256'] != row['prepared_glb_sha256'] for b in related):
                raise ValueError('Material relation prepared GLB differs from fitted groups')
            if len({(b.get('coordinate_method'), b.get('uv_semantics')) for b in related}) != 1:
                raise ValueError('Shared material requires equal intrinsic-coordinate semantics')
        facts = [(group_priors or {}).get(g, {}).get('declared_facts', {}) for g in groups]
        if facts[0] != facts[1]:
            raise ValueError('Shared material relation conflicts with different declared group facts')
        row['group_ids'] = sorted(groups)
        row['source_image_sha256'] = sorted(source_list)
        result.append(row)
        used.update(groups); identifiers.add(rid)
    return sorted(result, key=lambda r: r['relation_id'])


def infer_material_relations(semantic, priors, surface_bindings):
    """Propose one pair only from explicit, credible two-lens construction.

This deliberately abstains on shields, extra optical pieces, uncertain counts,
different declared group facts, and incompatible coordinate conventions. The
semantic interpretation cannot establish bilateral material identity by itself.
"""
    bindings = {b['material_group_id']: b for b in surface_bindings}
    construction = semantic.get('construction', {})
    description = construction.get('lens_count_or_shield', '').strip().lower()
    pair = (re.search(r'\b(two|2)\b.{0,30}\blenses\b', description) is not None
            or description in ('pair of lenses', 'two-lens pair'))
    if (len(bindings) != 2 or not pair or 'shield' in description
            or any(word in description for word in ('uncertain', 'possibly', 'maybe'))
            or construction.get('confidence') not in ('medium', 'high')):
        return []
    if set(bindings) - set(priors.get('groups', {})):
        return []
    evidence = [deepcopy(e) for e in construction.get('evidence', [])
                if e.get('photo_id') in priors.get('photos', {})
                and e.get('source_sha256') == priors['photos'][e['photo_id']]['source_sha256']]
    if not evidence:
        return []
    ordered = sorted(bindings)
    if len({bindings[g]['prepared_glb_sha256'] for g in ordered}) != 1:
        return []
    identity = {'groups': ordered, 'prepared_glb_sha256': bindings[ordered[0]]['prepared_glb_sha256'],
                'evidence': evidence}
    relation = {'relation_id': 'pair-' + hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:20],
                'kind': 'shared_manufactured_pair', 'group_ids': ordered,
                'prepared_glb_sha256': identity['prepared_glb_sha256'],
                'source_image_sha256': sorted({e['source_sha256'] for e in evidence}),
                'evidence': evidence, 'confidence': construction['confidence']}
    try:
        return validate_material_relations([relation], group_ids=ordered,
            sources=priors['source_image_sha256'], photos=priors['photos'], bindings=bindings,
            group_priors=priors.get('groups'))
    except ValueError:
        # An inferred relation is optional; explicit invalid relations still
        # raise in the fitter rather than silently becoming independent.
        return []
