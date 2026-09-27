"""Explicit selection among fitted appearance families; never acceptance.

Given a completed joint stage (one diagnostic preview per family assignment)
and its fit, rank the previews by the worst group error, keep every preview
within a declared tolerance of the best as equally supported, and pick the
least complex family among them. Complexity order is fixed: uniform tint,
gradient tint, colored mirror, angular mirror, gradient angular mirror. The
photo policy does not narrow that pool: on a white backdrop a clear lens and a
mirror of a white environment produce the same pixels, and a fraction of a code
between two families is not evidence for the more complex one; the least
complex family is the stated tie-break. What the policy does is describe the
shipped preview honestly: ``photo_policy_pass`` is the shipped preview's own
status, and ``policy_verdict`` says whether an equally supported alternative is
within policy when the shipped one is not, or that a row had too few
held-out samples to judge. The result names the selected
preview, every alternative with its numbers and the AR probe spread. It does
not certify a material.
"""
from __future__ import annotations

FAMILY_COMPLEXITY = ('uniform_tint', 'gradient_tint', 'colored_mirror', 'angular_mirror', 'gradient_angular_mirror')
METHOD = 'least_complex_family_within_tolerance_v3'
WITHIN_POLICY = 'within_declared_policy'
VERDICTS = ('shipped_within_policy', 'indistinguishable_from_within_policy', 'within_policy_candidate_beyond_tolerance',
            'validation_unmeasured', 'no_candidate_within_policy')


def _complexity(assignment):
    ranks = [FAMILY_COMPLEXITY.index(f) if f in FAMILY_COMPLEXITY else len(FAMILY_COMPLEXITY) for f in assignment.values()]
    return max(ranks) if ranks else len(FAMILY_COMPLEXITY)


def _group_errors(group):
    train = [m['train']['mean_absolute_interval_error_codes'] for m in group['photo_metrics'] if m.get('train') and m['train'].get('points')]
    validation = [m['validation']['mean_absolute_interval_error_codes'] for m in group['photo_metrics']
                  if m.get('validation') and m['validation'].get('points')]
    within = [m['train']['fraction_channels_within_policy'] for m in group['photo_metrics'] if m.get('train') and m['train'].get('points')]
    return {'worst_train_mean_codes': max(train) if train else None,
            'worst_validation_mean_codes': max(validation) if validation else None,
            'minimum_train_fraction_within_policy': min(within) if within else None,
            'photo_policy_status': group.get('photo_policy_status')}


def select_appearance(fit: dict, stage_report: dict, *, tolerance_codes=1.0) -> dict:
    """Rank previews and choose the least complex family within tolerance; report the shipped preview's policy status."""
    if isinstance(tolerance_codes, bool) or not isinstance(tolerance_codes, (int, float)) or tolerance_codes < 0:
        raise ValueError('tolerance_codes must be a nonnegative number')
    candidates = {c['candidate_id']: c for c in fit.get('candidates', [])}
    rows = []
    for preview in stage_report.get('previews', []):
        if preview.get('status') != 'diagnostic_preview_exported':
            continue
        candidate = candidates.get(preview['candidate_id'])
        if candidate is None:
            raise ValueError('Preview refers to a candidate absent from the fit')
        groups = {gid: _group_errors(group) for gid, group in candidate['groups'].items()}
        validation_measured = all(g['worst_validation_mean_codes'] is not None for g in groups.values())
        basis = 'worst_validation_mean_codes' if validation_measured else 'worst_train_mean_codes'
        scores = [g[basis] for g in groups.values() if g[basis] is not None]
        rows.append({'candidate_id': preview['candidate_id'], 'family_assignment': dict(preview['family_assignment']),
                     'path': preview['path'], 'sha256': preview['sha256'], 'export': preview.get('export'),
                     'complexity_rank': _complexity(preview['family_assignment']), 'score_basis': basis,
                     'score_codes': max(scores) if scores else None, 'groups': groups,
                     'converged': bool(candidate['optimizer'].get('converged')),
                     'photo_policy_status': candidate.get('photo_policy_status')})
    scored = [r for r in rows if r['score_codes'] is not None]
    if not scored:
        return {'schema_version': 1, 'method': METHOD, 'status': 'no_scored_preview', 'selected': None,
                'alternatives': rows, 'accepted': False, 'quality_verdict': 'unmeasured', 'selected_material': None}
    bases = {r['score_basis'] for r in scored}
    if len(bases) > 1:
        # Mixed bases cannot be compared; fall back to training error for all.
        for r in scored:
            r['score_basis'] = 'worst_train_mean_codes'
            r['score_codes'] = max(g['worst_train_mean_codes'] for g in r['groups'].values() if g['worst_train_mean_codes'] is not None)
    best = min(r['score_codes'] for r in scored)
    eligible = [r for r in scored if r['score_codes'] <= best+tolerance_codes]
    eligible.sort(key=lambda r: (r['complexity_rank'], r['score_codes'], r['candidate_id']))
    selected = eligible[0]
    shipped_within = selected['photo_policy_status'] == WITHIN_POLICY
    verdict = ('shipped_within_policy' if shipped_within
               else 'indistinguishable_from_within_policy' if any(r['photo_policy_status'] == WITHIN_POLICY for r in eligible)
               else 'within_policy_candidate_beyond_tolerance' if any(r['photo_policy_status'] == WITHIN_POLICY for r in scored)
               else 'validation_unmeasured' if selected['photo_policy_status'] == 'validation_unmeasured'
               else 'no_candidate_within_policy')
    envelopes = {gid: {k: v for k, v in env.items() if k != 'candidate_ids'}
                 for gid, env in fit.get('ar_prediction_envelopes_by_group', {}).items()}
    return {'schema_version': 1, 'method': METHOD, 'status': 'appearance_selected', 'accepted': False,
            'quality_verdict': 'unmeasured', 'selected_material': None,
            'policy': {'tolerance_codes': float(tolerance_codes), 'complexity_order': list(FAMILY_COMPLEXITY),
                       'rule': 'among previews whose worst group error is within tolerance of the best, choose the least complex family; '
                               'ties by lower error then candidate id; the photo policy describes the shipped preview and never narrows the pool'},
            'best_score_codes': best,
            'selected': {k: selected[k] for k in ('candidate_id', 'family_assignment', 'path', 'sha256', 'export',
                                                  'score_basis', 'score_codes', 'complexity_rank', 'photo_policy_status', 'groups')},
            'equally_supported_alternatives': [{k: r[k] for k in ('candidate_id', 'family_assignment', 'score_codes', 'complexity_rank', 'photo_policy_status')}
                                               for r in eligible[1:]],
            'alternatives': [{k: r[k] for k in ('candidate_id', 'family_assignment', 'score_basis', 'score_codes', 'complexity_rank', 'photo_policy_status', 'converged', 'groups')}
                             for r in sorted(scored, key=lambda r: (r['score_codes'], r['complexity_rank']))],
            'identifiability': ('families_indistinguishable_within_tolerance' if len(eligible) > 1 else 'single_family_within_tolerance'),
            'photo_policy_pass': shipped_within, 'policy_verdict': verdict,
            'any_candidate_within_policy': any(r['photo_policy_status'] == WITHIN_POLICY for r in scored),
            'ar_prediction_envelopes_by_group': envelopes,
            'limitations': ['Scores are interval errors against uncalibrated photographs under fitted cameras and proposal masks.',
                            'The least complex family is a stated preference, not evidence that simpler physics is true.',
                            'photo_policy_pass describes the shipped preview; policy_verdict says whether an equally supported alternative is within policy.',
                            'A selected preview remains a diagnostic candidate with accepted=false and an unmeasured quality verdict.']}
