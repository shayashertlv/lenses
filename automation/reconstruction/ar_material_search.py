"""Bounded material proposals and image-bound, order-reversed AR comparisons.

Semantic judgments are fallible preferences. They never establish acceptance,
replace declared product facts, or alter geometry. All candidate colors below
come from numerical anchors or existing fitted descriptors, never color names.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import numpy as np
from scipy.optimize import lsq_linear

from .lens_appearance import LensAppearance, DensityKeyframe
from .material_relations import validate_material_relations


METHOD = 'semantic_ar_v1'


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _permitted(candidate, facts):
    mirror = facts.get('mirror_coating')
    if mirror is None:
        return True
    if type(mirror) is not bool:
        raise ValueError('mirror_coating must be a declared boolean')
    return all(('mirror' in family) == mirror for family in candidate['family_assignment'].values())


def _fit_density_proposal(samples, seed_appearances):
    """One numeric density curve from uncertain anchors, with soft continuation."""
    heights = np.array([0., .5, 1.])
    prior = np.mean([LensAppearance.from_dict(a).evaluate(heights).optical_density_rgb
                     for a in seed_appearances], axis=0)
    design = []
    for sample in samples:
        interval = min(1, int(sample['v'] >= .5))
        t = (sample['v'] - heights[interval]) / .5
        blend = t * t * (3 - 2 * t)
        basis = np.zeros(3)
        basis[interval], basis[interval + 1] = 1 - blend, blend
        design.append(basis)
    design = np.asarray(design)
    observed = np.array([a['density_rgb'] for a in samples])
    sigma = np.array([a['sigma'] for a in samples])
    weights = np.sqrt(np.array([a.get('weight', 1./len(samples)) for a in samples]))
    density = np.zeros((3, 3))
    for ch in range(3):
        w = weights / np.maximum(.05, sigma[:, ch])
        matrix = np.vstack((design * w[:, None], np.eye(3) * .3))
        target = np.r_[observed[:, ch] * w, prior[:, ch] * .3]
        density[:, ch] = lsq_linear(matrix, target, bounds=(0., 16.)).x
    return [{'v': float(v), 'optical_density_rgb': rgb.tolist()} for v, rgb in zip(heights, density)]


def _rear_transmission_proposals(priors, existing, group_ids, fit_groups):
    """Jointly refit front observations and rear radiance; no coating scaling."""
    if not fit_groups or not any(g.get('rear_image_constraints') for g in priors.get('groups',{}).values()):
        return []
    from .joint_photo_lens_fit import JointPhotoLensFitPolicy, fit_joint_photo_lens_candidates
    from .photo_lens_fit import PhotoLensFitPolicy
    bindings={g['surface_binding']['material_group_id']:g['surface_binding'] for g in fit_groups}
    if set(bindings)!=group_ids or any(c.get('prepared_glb_sha256')!=b['prepared_glb_sha256'] for c in existing for b in bindings.values()):
        raise ValueError('Joint rear refinement source geometry differs from candidates')
    families=('uniform_tint',) if priors.get('declared_facts',{}).get('mirror_coating') is False else ('colored_mirror','angular_mirror')
    photo=PhotoLensFitPolicy(families=families,lighting_families=('semantic_softbox',),roughness_values=(.05,),max_nfev=45)
    policy=JointPhotoLensFitPolicy(photo_policy=photo,maximum_joint_mask_branches=4,maximum_optimization_runs=48,
        mask_search_mode='conditional_seed_beam',conditional_beam_width=4)
    fitted=fit_joint_photo_lens_candidates(fit_groups,policy=policy,appearance_priors=priors)
    best={}
    def score(candidate):
        values=[m['validation']['mean_absolute_interval_error_codes'] for g in candidate['groups'].values()
                for m in g['photo_metrics'] if m['validation']['points']]
        return (max(values,default=float('inf')),candidate['optimizer']['objective_including_priors'])
    for candidate in fitted['candidates']:
        family=(tuple((gid,g['family']) for gid,g in sorted(candidate['groups'].items())),
                candidate['assumptions'].get('rear_response_hypothesis'))
        if family not in best or score(candidate)<score(best[family]):
            best[family]=candidate
    return [{'appearances':{gid:g['appearance'] for gid,g in c['groups'].items()},
        'family_assignment':{gid:g['family'] for gid,g in c['groups'].items()},
        'origin':'joint_front_rear_optical_refinement','score_codes':score(c)[0] if np.isfinite(score(c)[0]) else None,
        'prepared_glb_sha256':next(iter(bindings.values()))['prepared_glb_sha256'],
        'source_candidate_id':c['candidate_id'],
        'rear_response_hypothesis':c['assumptions'].get('rear_response_hypothesis'),
        'joint_refinement':{'input_sha256':fitted['input_sha256'],'implementation':fitted['implementation'],
            'optimizer':c['optimizer'],'mask_search':fitted['mask_search'],'nuisance_by_photo':c['shared_nuisance_by_photo'],
            'rear_reflection_exported_as':'rear_reflection_fraction_rgb'},
        'assumption':'Front angular knots, absorption and energy-bounded rear response jointly fitted; rear incidence, illumination and exposure remain bounded conditional nuisance.'}
        for c in best.values()]


def propose_material_candidates(anchors, priors, *, existing_candidates, policy=None, fit_groups=None):
    """Keep baseline/contrary explanations, then propose anchored density variants.

existing_candidates: [{candidate_id, family_assignment, appearances:{gid:dict},
score_codes, ...}]. The first row is the baseline. No existing row is mutated.
"""
    policy = policy or {}
    maximum = policy.get('maximum_candidates', 8)
    if type(maximum) is not int or not 3 <= maximum <= 12:
        raise ValueError('Candidate capacity must be 3..12')
    if not existing_candidates:
        raise ValueError('A baseline candidate is required')
    facts = priors.get('declared_facts', {})
    existing = [deepcopy(c) for c in existing_candidates if _permitted(c, facts)]
    if not existing:
        raise ValueError('No existing candidate satisfies declared facts')
    group_ids = set(existing[0]['appearances'])
    relations = validate_material_relations(priors.get('material_relations'), group_ids=group_ids,
        sources=priors.get('source_image_sha256', []), photos=priors.get('photos', {}),
        group_priors=priors.get('groups'))
    if any(candidate.get('prepared_glb_sha256') != relation['prepared_glb_sha256']
           for candidate in existing for relation in relations):
        raise ValueError('Material relation does not bind every existing candidate asset')
    result, seen = [], set()

    def add(row):
        if set(row['appearances']) != group_ids or set(row['family_assignment']) != group_ids:
            raise ValueError('Every material candidate must cover the same groups')
        for appearance in row['appearances'].values():
            LensAppearance.from_dict(appearance)
        digest = _hash(row['appearances'])
        if digest in seen or len(result) >= maximum:
            return
        seen.add(digest)
        row['candidate_id'] = 'material-' + digest[:20]
        result.append(row)

    # Baseline, strongest tint, strongest mirror: contrary evidence is retained.
    add({**existing[0], 'origin': 'baseline_photo_selection'})
    for mirrored in (False, True):
        pool = [c for c in existing if all(('mirror' in f) == mirrored for f in c['family_assignment'].values())]
        if pool:
            add({**min(pool, key=lambda c: c.get('score_codes') if c.get('score_codes') is not None else float('inf')),
                 'origin': 'existing_mirror_alternative' if mirrored else 'existing_tint_alternative'})
    # Retain a fitted pair explicitly; ranking by independent photographic
    # error alone otherwise silently discards every shared hypothesis.
    for mirrored in (False, True):
        shared = [c for c in existing if c.get('material_relation', {}).get('mode') == 'shared_manufactured_pair'
                  and all(('mirror' in f) == mirrored for f in c['family_assignment'].values())]
        if shared:
            add({**min(shared, key=lambda c: c.get('score_codes') if c.get('score_codes') is not None else float('inf')),
                 'origin': 'existing_shared_material_alternative'})
    # Front residual alone cannot identify whether a colored rear photograph is
    # transmitted or reflected light. Reserve comparison slots for both bounded
    # hypotheses before optional density variations consume the finite budget.
    for mode in ('free_rear_reflection','weak_rear_reflection'):
        pool=[c for c in existing if c.get('rear_response_hypothesis')==mode]
        if pool:
            add({**min(pool,key=lambda c:c.get('score_codes') if c.get('score_codes') is not None else float('inf')),
                 'origin':'existing_rear_response_alternative'})
    if len(result)<maximum:
        for proposal in _rear_transmission_proposals(priors,existing,group_ids,fit_groups):
            add(proposal)
    if facts.get('mirror_coating') is not True and any(
            h.get('absorption') == 'clear' and h.get('coating') == 'ordinary' and h.get('confidence') != 'low'
            for h in priors.get('hypotheses', [])):
        clear = LensAppearance((DensityKeyframe(0., (0., 0., 0.)), DensityKeyframe(1., (0., 0., 0.)))).to_dict()
        add({'appearances': {g: clear for g in group_ids}, 'family_assignment': {g: 'uniform_tint' for g in group_ids},
             'origin': 'semantic_clear_hypothesis', 'score_codes': None,
             'assumption': 'Zero absorption is a tested clear-lens hypothesis, not measured transmission.'})
    # A measured density proposal is permitted only if all groups have support.
    numeric = priors.get('groups', {})
    density_by_group = {}
    tint_seeds = [c for c in existing if all('mirror' not in f for f in c['family_assignment'].values())]
    seed = min(tint_seeds, key=lambda c: c.get('score_codes') if c.get('score_codes') is not None else float('inf')) if tint_seeds else None
    for gid in group_ids:
        samples = numeric.get(gid, {}).get('density_anchors', [])
        if samples and seed:
            # Fit only supported heights; endpoint continuation is regularized
            # toward an existing fitted tint, explicitly not measured endpoints.
            density_by_group[gid] = _fit_density_proposal(samples, [seed['appearances'][gid]])
    if relations and seed and facts.get('mirror_coating') is not True:
        shared_density = deepcopy(density_by_group)
        supported_relations = []
        for relation in relations:
            paired = relation['group_ids']
            samples = [a for gid in paired for a in numeric.get(gid, {}).get('density_anchors', [])]
            if samples:
                pooled = _fit_density_proposal(samples, [seed['appearances'][gid] for gid in paired])
                shared_density.update({gid: pooled for gid in paired})
                supported_relations.append(relation)
        if supported_relations and set(shared_density) == group_ids:
            for factor in (1., .75, 1.25):
                appearances = {gid: LensAppearance(tuple(DensityKeyframe(k['v'], tuple(
                    np.asarray(k['optical_density_rgb']) * factor)) for k in keys)).to_dict()
                    for gid, keys in shared_density.items()}
                add({'appearances': appearances, 'family_assignment': {g: 'gradient_tint' for g in group_ids},
                     'origin': 'paired_measured_density_proposal', 'density_factor': factor, 'score_codes': None,
                     'material_relation': {'mode': 'shared_manufactured_pair',
                         'relation_ids': [r['relation_id'] for r in supported_relations],
                         'shared_group_sets': [r['group_ids'] for r in supported_relations]},
                     'prepared_glb_sha256': supported_relations[0]['prepared_glb_sha256'],
                     'extrapolation': 'Shared manufactured-pair hypothesis pools measured anchors; unsupported heights use soft fitted-tint continuation, not measurements.'})
    if set(density_by_group) == group_ids and facts.get('mirror_coating') is not True:
        for factor in (1., .75, 1.25):
            appearances = {}
            for gid, keys in density_by_group.items():
                density = [DensityKeyframe(float(k['v']), tuple(np.maximum(0., np.asarray(k['optical_density_rgb']) * factor))) for k in keys]
                if density[0].v != 0 or density[-1].v != 1:
                    # Missing endpoints are intentionally not invented here.
                    break
                appearances[gid] = LensAppearance(tuple(density)).to_dict()
            if set(appearances) == group_ids:
                add({'appearances': appearances, 'family_assignment': {g: 'gradient_tint' for g in group_ids},
                     'origin': 'measured_density_proposal', 'density_factor': factor, 'score_codes': None,
                     'extrapolation': 'Unobserved endpoint densities regularized toward existing fitted tint; not measurements.'})
    for row in sorted(existing, key=lambda c: c.get('score_codes') if c.get('score_codes') is not None else float('inf')):
        add({**row, 'origin': 'existing_fitted_alternative'})
    return result


_COMPARISON_SCHEMA = {
    'type': 'object', 'properties': {
        'winner': {'type': 'string'},
        'assessments': {'type': 'array', 'items': {'type': 'object', 'properties': {
            'candidate': {'type': 'string'}, 'match': {'type': 'string', 'enum': ['good', 'plausible', 'poor', 'unjudgeable']},
            'defects': {'type': 'array', 'items': {'type': 'string'}},
            'evidence': {'type': 'array', 'items': {'type': 'string'}}},
            'required': ['candidate', 'match', 'defects', 'evidence']}},
        'limitations': {'type': 'array', 'items': {'type': 'string'}}},
    'required': ['winner', 'assessments', 'limitations']}


def compare_rendered_candidates(photos, blind_cards, *, client, policy=None):
    """Two separate calls reverse candidate order; names/scores/priors are withheld."""
    from .photo_semantics import build_image_manifest
    policy = policy or {}
    if not 2 <= len(blind_cards) <= 12:
        raise ValueError('Blind comparison requires 2..12 candidates')
    ids = [c['candidate_id'] for c in blind_cards]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate material candidate ID')
    for card in blind_cards:
        if _file_hash(card['path']) != card['sha256']:
            raise ValueError('Rendered card bytes changed')
    source_manifest = build_image_manifest(photos)
    comparisons = []
    reversed_order = list(reversed(blind_cards))
    if len(reversed_order) % 2:
        middle = len(reversed_order) // 2
        # Pure reversal leaves the middle candidate at the same label/position.
        reversed_order[middle], reversed_order[middle + 1] = reversed_order[middle + 1], reversed_order[middle]
    for order in (list(blind_cards), reversed_order):
        mapping = {chr(65+i): c['candidate_id'] for i, c in enumerate(order)}
        inputs = [{'id': 'photo-' + str(i+1), 'prompt_label': 'photo-' + str(i+1),
                   'path': p['path'], 'sha256': source_manifest[i]['sha256']} for i, p in enumerate(photos)]
        inputs += [{'id': 'candidate-' + chr(65+i), 'prompt_label': 'candidate-' + chr(65+i),
                    'path': c['path'], 'sha256': c['sha256']} for i, c in enumerate(order)]
        manifest = build_image_manifest(inputs)
        prompt = (
            'Compare intrinsic lens appearance in candidate AR cards to the original product photos. '
            'Each card has identical rendering: columns front, angled, rolled, and rear asset inspection; rows white, light skin, dark skin. '
            'Rear inspection rotates the glasses about the bridge and does not simulate a wearer facing away. '
            'Eye shapes are synthetic transparency probes. Lighting/backgrounds differ from studio photos. '
            'Evaluate tint hue, gradient direction/strength, transparency, colored mirror behavior and reflections. '
            'Do not reward matching a studio softbox reflection that should disappear in a different environment. '
            'Keep geometry, frame defects and render artifacts in limitations. Do not infer coating chemistry. '
            'Assess EVERY candidate by letter. Choose one letter only if supported, or tie or neither. '
            'Use evidence from numbered source photos, acknowledge lighting ambiguity. '
            'Defects may include wrong_hue, too_silver, gradient_missing, gradient_reversed, too_opaque, '
            'too_clear, mirror_missing, reflection_baked_into_tint, or a short specific observation. '
            'No candidate has privileged status. Return the structured JSON.')
        receipt = client.infer(manifest, prompt, _COMPARISON_SCHEMA)
        response = receipt.get('response', receipt)
        # The image's explicit label is candidate-A; the requested short form
        # is A. Both identify exactly the same bound card. Do not infer labels
        # from ordering, descriptions, unknown suffixes or missing assessments.
        def label(value):
            if isinstance(value,str) and value.startswith('candidate-') and value[10:] in mapping:
                return value[10:]
            return value
        winner = label(response.get('winner'))
        assessments = [{**a, 'candidate':label(a.get('candidate'))} for a in response.get('assessments', [])]
        labels = [a.get('candidate') for a in assessments]
        if winner not in {*mapping, 'tie', 'neither'} or len(labels) != len(mapping) or set(labels) != set(mapping):
            raise ValueError('Comparison does not identify every supplied candidate exactly once')
        if any(a.get('match') not in ('good', 'plausible', 'poor', 'unjudgeable') for a in assessments):
            raise ValueError('Invalid comparison assessment')
        comparisons.append({'mapping': mapping, 'winner': mapping.get(winner), 'verdict': winner,
                            'assessments': [{**a, 'candidate_id': mapping[a['candidate']]} for a in assessments],
                            'limitations': response.get('limitations', []), 'receipt': receipt})
    return {'schema_version': 1, 'method': 'blind_order_reversed_ar_comparison_v1',
            'photos': source_manifest, 'cards': deepcopy(blind_cards), 'comparisons': comparisons,
            'order_policy': 'reverse; swap middle pair for odd counts so every candidate changes position',
            'confidence': 'uncalibrated_model_preference', 'accepted': False}


def select_ar_material(candidates, anchors, comparisons, *, policy=None):
    """Switch baseline only for a consistent, non-poor blind winner with bound cards."""
    policy = policy or {}
    facts = policy.get('declared_facts', {})
    eligible = [c for c in candidates if _permitted(c, facts)]
    if not eligible:
        raise ValueError('No candidates satisfy declared product facts')
    by_id = {c['candidate_id']: c for c in eligible}
    if len(by_id) != len(eligible):
        raise ValueError('Duplicate material candidates')
    cards = comparisons.get('cards', [])
    if len(cards) != len(candidates) or {c['candidate_id'] for c in cards} != {c['candidate_id'] for c in candidates}:
        raise ValueError('Comparison must include every exported candidate exactly once')
    for card in cards:
        candidate = next((c for c in candidates if c['candidate_id'] == card['candidate_id']), None)
        if candidate is None or candidate.get('sha256') != card.get('model_sha256'):
            raise ValueError('Comparison refers to a different exported candidate')
        if card.get('path') and _file_hash(card['path']) != card['sha256']:
            raise ValueError('Rendered comparison card changed')
    runs = comparisons.get('comparisons', [])
    winners = [r.get('winner') for r in runs]
    stable = len(runs) == 2 and winners[0] in by_id and winners[0] == winners[1]
    if stable:
        stable = all(any(a.get('candidate_id') == winners[0] and a.get('match') in ('good', 'plausible')
                         for a in r['assessments']) for r in runs)
    supported = [c for c in eligible if len(runs) == 2 and all(
        any(a.get('candidate_id') == c['candidate_id'] and a.get('match') in ('good', 'plausible')
            for a in r.get('assessments', [])) for r in runs)]
    baseline_rejected = len(runs) == 2 and all(any(
        a.get('candidate_id') == eligible[0]['candidate_id'] and a.get('match') == 'poor'
        for a in r.get('assessments', [])) for r in runs)

    def anchor_error(candidate):
        errors = []
        for gid, evidence in anchors.get('groups', {}).items():
            if gid not in candidate['appearances']:
                continue
            appearance = LensAppearance.from_dict(candidate['appearances'][gid])
            for anchor in evidence.get('density_anchors', []):
                if candidate['family_assignment'][gid] not in anchor.get('applicable_families', ['uniform_tint', 'gradient_tint']):
                    continue
                delta = (appearance.evaluate(anchor['v']).optical_density_rgb - anchor['density_rgb']) / np.maximum(.05, anchor['sigma'])
                errors.append(float(np.mean(delta * delta)) * anchor.get('weight', 1.))
        return sum(errors) if errors else float('inf')

    consensus_pool = not stable and baseline_rejected and bool(supported) and all(r.get('verdict') != 'neither' for r in runs)
    if stable:
        selected, basis = by_id[winners[0]], 'consistent_order_reversed_ar_preference'
    elif consensus_pool:
        # Exact ranking can be unstable while rejection of the baseline and
        # membership of the plausible set are stable. A numerical tie-break is
        # explicit; it must not be presented as unanimous visual agreement.
        selected = min(supported, key=lambda c: (anchor_error(c),
                       c.get('score_codes') if c.get('score_codes') is not None else float('inf'), c['candidate_id']))
        basis = 'consistent_visual_rejection_then_numerical_tiebreak'
    else:
        selected, basis = eligible[0], 'baseline_fallback'
    return {'schema_version': 1, 'method': METHOD,
            'status': 'appearance_selected' if stable else 'appearance_selected_ambiguous_pool' if consensus_pool else 'comparison_unresolved_baseline_retained',
            'selected': deepcopy(selected), 'alternatives': [deepcopy(c) for c in eligible if c != selected],
            'selection_basis': basis, 'visually_supported_candidate_ids': [c['candidate_id'] for c in supported],
            'exact_visual_winner_stable': stable,
            'comparison_sha256': _hash(comparisons), 'anchors_sha256': _hash(anchors),
            'declared_facts': facts, 'accepted': False, 'quality_verdict': 'unmeasured',
            'limitations': ['Repeated model judgments share biases and are not independent human validation.',
                           'Synthetic backgrounds test rendered appearance, not wearer fit.',
                           'Missing geometry and unseen material behavior remain unresolved.']}
