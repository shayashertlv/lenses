"""Least-complex-family selection within tolerance; nothing is accepted."""
import json
import unittest

from reconstruction.appearance_selection import FAMILY_COMPLEXITY, select_appearance


def candidate(cid, family, train, validation=None, converged=True, policy='outside_declared_policy'):
    metrics = {'train': {'mean_absolute_interval_error_codes': train, 'fraction_channels_within_policy': .8, 'points': 10},
               'validation': ({'mean_absolute_interval_error_codes': validation, 'fraction_channels_within_policy': .8, 'points': 4}
                              if validation is not None else {'mean_absolute_interval_error_codes': None, 'points': 0})}
    return {'candidate_id': cid, 'optimizer': {'converged': converged}, 'photo_policy_status': policy,
            'groups': {'g0': {'family': family, 'photo_metrics': [metrics], 'photo_policy_status': policy}}}


def stage(previews):
    return {'previews': [{'status': 'diagnostic_preview_exported', 'candidate_id': cid, 'family_assignment': {'g0': family},
                          'path': f'preview-{family}.glb', 'sha256': 'a'*64, 'export': {'path': f'preview-{family}-export.json', 'sha256': 'b'*64}}
                         for cid, family in previews]}


class AppearanceSelectionTests(unittest.TestCase):
    def test_least_complex_family_within_tolerance_is_selected_and_alternatives_kept(self):
        fit = {'candidates': [candidate('u', 'uniform_tint', 4.2, 4.0), candidate('g', 'gradient_tint', 3.8, 3.6),
                              candidate('m', 'angular_mirror', 3.5, 3.4), candidate('c', 'colored_mirror', 9., 8.)],
               'ar_prediction_envelopes_by_group': {'g0': {'maximum_channel_spread': 1., 'candidate_ids': ['u', 'g', 'm', 'c']}}}
        result = select_appearance(fit, stage([('u', 'uniform_tint'), ('g', 'gradient_tint'), ('m', 'angular_mirror'), ('c', 'colored_mirror')]))
        json.dumps(result, allow_nan=False)
        self.assertEqual(result['status'], 'appearance_selected')
        self.assertEqual(result['selected']['candidate_id'], 'u', 'uniform tint is within 1 code of the best validation error')
        self.assertEqual(result['selected']['score_basis'], 'worst_validation_mean_codes')
        self.assertEqual([a['candidate_id'] for a in result['equally_supported_alternatives']], ['g', 'm'])
        self.assertEqual(result['identifiability'], 'families_indistinguishable_within_tolerance')
        self.assertFalse(result['photo_policy_pass']); self.assertFalse(result['accepted']); self.assertIsNone(result['selected_material'])
        self.assertEqual(result['ar_prediction_envelopes_by_group']['g0'], {'maximum_channel_spread': 1.})
        tight = select_appearance(fit, stage([('u', 'uniform_tint'), ('g', 'gradient_tint'), ('m', 'angular_mirror')]), tolerance_codes=.1)
        self.assertEqual(tight['selected']['candidate_id'], 'm')
        self.assertEqual(tight['identifiability'], 'single_family_within_tolerance')

    def test_policy_describes_the_shipped_preview_and_never_narrows_the_pool(self):
        # The least complex family is a fraction of a code behind a within-policy mirror: on a white backdrop
        # that is not evidence for the mirror, so the tint ships and the verdict says an equally supported
        # alternative is within policy.
        fit = {'candidates': [candidate('u', 'uniform_tint', 4.2, 4.0), candidate('g', 'gradient_tint', 3.8, 3.6),
                              candidate('m', 'angular_mirror', 3.5, 3.4, policy='within_declared_policy')]}
        result = select_appearance(fit, stage([('u', 'uniform_tint'), ('g', 'gradient_tint'), ('m', 'angular_mirror')]))
        json.dumps(result, allow_nan=False)
        self.assertEqual(result['selected']['candidate_id'], 'u')
        self.assertFalse(result['photo_policy_pass']); self.assertTrue(result['any_candidate_within_policy'])
        self.assertEqual(result['policy_verdict'], 'indistinguishable_from_within_policy')
        self.assertEqual([(a['candidate_id'], a['photo_policy_status']) for a in result['equally_supported_alternatives']],
                         [('g', 'outside_declared_policy'), ('m', 'within_declared_policy')])
        # The shipped preview within policy is a plain pass.
        fit['candidates'][0] = candidate('u', 'uniform_tint', 4.2, 4.0, policy='within_declared_policy')
        shipped = select_appearance(fit, stage([('u', 'uniform_tint'), ('g', 'gradient_tint'), ('m', 'angular_mirror')]))
        self.assertTrue(shipped['photo_policy_pass']); self.assertEqual(shipped['policy_verdict'], 'shipped_within_policy')
        # A within-policy preview beyond the tolerance is named as such; none at all likewise.
        far = {'candidates': [candidate('u', 'uniform_tint', 4.2, 4.0), candidate('m', 'angular_mirror', 2.0, 1.9, policy='within_declared_policy')]}
        beyond = select_appearance(far, stage([('u', 'uniform_tint'), ('m', 'angular_mirror')]))
        self.assertEqual((beyond['selected']['candidate_id'], beyond['policy_verdict']), ('m', 'shipped_within_policy'))
        wide = select_appearance(far, stage([('u', 'uniform_tint'), ('m', 'angular_mirror')]), tolerance_codes=.5)
        self.assertEqual((wide['selected']['candidate_id'], wide['policy_verdict']), ('m', 'shipped_within_policy'))
        none = select_appearance({'candidates': [candidate('u', 'uniform_tint', 4.2, 4.0), candidate('m', 'angular_mirror', 3.5, 3.4)]},
                                 stage([('u', 'uniform_tint'), ('m', 'angular_mirror')]))
        self.assertEqual((none['selected']['candidate_id'], none['policy_verdict']), ('u', 'no_candidate_within_policy'))
        self.assertFalse(none['photo_policy_pass']); self.assertFalse(none['any_candidate_within_policy'])
        tight = select_appearance({'candidates': [candidate('u', 'uniform_tint', 4.2, 4.0), candidate('m', 'angular_mirror', 5.6, 5.5, policy='within_declared_policy')]},
                                  stage([('u', 'uniform_tint'), ('m', 'angular_mirror')]))
        self.assertEqual((tight['selected']['candidate_id'], tight['policy_verdict']), ('u', 'within_policy_candidate_beyond_tolerance'))
        unmeasured = select_appearance({'candidates': [candidate('u', 'uniform_tint', 4.2, 4.0, policy='validation_unmeasured')]}, stage([('u', 'uniform_tint')]))
        self.assertEqual(unmeasured['policy_verdict'], 'validation_unmeasured'); self.assertFalse(unmeasured['photo_policy_pass'])

    def test_training_basis_when_validation_is_unmeasured_and_mixed_bases_fall_back(self):
        fit = {'candidates': [candidate('u', 'uniform_tint', 5.0), candidate('g', 'gradient_tint', 3.0, 2.5)]}
        result = select_appearance(fit, stage([('u', 'uniform_tint'), ('g', 'gradient_tint')]))
        self.assertTrue(all(a['score_basis'] == 'worst_train_mean_codes' for a in result['alternatives']))
        self.assertEqual(result['selected']['candidate_id'], 'g')
        empty = select_appearance({'candidates': []}, {'previews': []})
        self.assertEqual(empty['status'], 'no_scored_preview'); self.assertIsNone(empty['selected'])
        with self.assertRaises(ValueError):
            select_appearance(fit, stage([('missing', 'uniform_tint')]))
        with self.assertRaises(ValueError):
            select_appearance(fit, stage([]), tolerance_codes=-1)
        self.assertEqual(FAMILY_COMPLEXITY[0], 'uniform_tint')


if __name__ == '__main__':
    unittest.main()
