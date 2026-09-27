"""Independent finite-subset oracles for shared support inference."""
from dataclasses import replace
import itertools
import unittest
from unittest.mock import patch

import numpy as np
from scipy.optimize import OptimizeResult

from reconstruction.support_union import SupportLimits, optimize_support, union_apertures


def views_for(components, masks, known=None, shape=(4, 4)):
    result = []
    for i, (parts, positives) in enumerate(zip(components, masks)):
        mask = np.zeros(shape, bool)
        mask.ravel()[list(positives)] = True
        domain = np.ones(shape, bool) if known is None else np.zeros(shape, bool)
        if known is not None:
            domain.ravel()[list(known[i])] = True
        result.append({'id': f'photo-{i}', 'shape': shape,
                       'component_pixels': [np.array(sorted(p), dtype=np.int64) for p in parts],
                       'mask': mask, 'known_domain': domain,
                       'provenance': {'test_source': 'independent_finite_sets', 'photo': i}})
    return result


def independent_scores(views, selected):
    scores = []
    for view in views:
        predicted = set().union(*(set(view['component_pixels'][i].tolist()) for i in selected))
        known = set(np.flatnonzero(view['known_domain']).tolist())
        aperture = set(np.flatnonzero(view['mask']).tolist())
        supported = predicted & known
        scores.append(len(supported & aperture) / len(supported | aperture))
    return scores


def scored_unions(views, selected):
    return [set().union(*(set(v['component_pixels'][i].tolist()) for i in selected))
            & set(np.flatnonzero(v['known_domain']).tolist()) for v in views]


def exhaustive_value(views):
    count = len(views[0]['component_pixels'])
    return max(min(independent_scores(views, [i for i, flag in enumerate(bits) if flag]))
               for bits in itertools.product((False, True), repeat=count))


class SupportUnionTests(unittest.TestCase):
    def check_oracle(self, views, **kwargs):
        report = optimize_support(views, ratio_gap=0.0001, time_limit=15,
                                  maximum_iterations=30, **kwargs)
        ids = report['selected_component_ids']
        self.assertEqual(ids, sorted(set(ids)))
        expected = exhaustive_value(views)
        scores = independent_scores(views, ids)
        self.assertAlmostEqual(report['minimum_iou'], min(scores), places=12)
        self.assertAlmostEqual(report['mean_iou'], np.mean(scores), places=12)
        self.assertLessEqual(report['minimum_iou'], expected + 1e-12)
        self.assertGreaterEqual(report['minimum_iou'], expected - 0.00011)
        self.assertGreaterEqual(report['upper_bound'], expected - 1e-8)
        self.assertLessEqual(report['upper_bound'], 1)
        self.assertGreaterEqual(report['upper_bound'], report['minimum_iou'])
        self.assertGreaterEqual(report['remaining_gap'], 0)
        self.assertAlmostEqual(report['remaining_gap'], report['upper_bound'] - report['minimum_iou'], places=12)
        selected = set(ids)
        unions = scored_unions(views, selected)
        count = len(views[0]['component_pixels'])
        self.assertEqual(report['addable_without_scored_union_change'],
                         [i for i in range(count) if i not in selected
                          and scored_unions(views, selected | {i}) == unions])
        self.assertEqual(report['individually_removable_without_scored_union_change'],
                         [i for i in sorted(selected) if scored_unions(views, selected - {i}) == unions])
        self.assertFalse(report['accepted'])
        self.assertEqual(report['physical_groups'], 'not_inferred')
        return report

    def test_complementary_fragments_are_jointly_selected_without_hardware(self):
        parts = [[{0, 1}, {2, 3}, {8, 9, 10}], [{4, 5}, {6, 7}, {12, 13}]]
        report = self.check_oracle(views_for(parts, [{0, 1, 2, 3}, {4, 5, 6, 7}]))
        self.assertEqual(report['selected_component_ids'], [0, 1])
        self.assertEqual(report['minimum_iou'], 1)

    def test_opposing_views_require_shared_tradeoff_not_independent_winners(self):
        views = views_for([[{0}, {1}], [{1}, {0}]], [{0}, {0}])
        report = self.check_oracle(views)
        self.assertEqual(report['selected_component_ids'], [0, 1])
        self.assertEqual(report['minimum_iou'], 0.5)

    def test_unequal_aperture_areas_do_not_turn_max_min_into_pooled_iou(self):
        views = views_for([[set(range(100)), set(range(100, 200))], [set(), {0}]],
                          [set(range(100)), {0}], shape=(2, 101))
        report = self.check_oracle(views)
        self.assertEqual(report['selected_component_ids'], [0, 1])
        self.assertEqual(report['minimum_iou'], .5)

    def test_random_small_problems_match_independent_exhaustive_subsets(self):
        rng = np.random.default_rng(60922)
        for trial in range(18):
            count = 3 + trial % 5
            photos = 2 + trial % 2
            parts = [[set(np.flatnonzero(rng.random(16) < .35).tolist())
                      for _ in range(count)] for _ in range(photos)]
            domains = [set(np.flatnonzero(rng.random(16) < .85).tolist()) | {0}
                       for _ in range(photos)]
            masks = [(set(np.flatnonzero(rng.random(16) < .4).tolist()) & k) | {0}
                     for k in domains]
            with self.subTest(trial=trial):
                self.check_oracle(views_for(parts, masks, domains))

    def test_duplicate_representation_does_not_change_optimal_objective(self):
        original = views_for([[{0, 1}, {1, 2}, {2, 3}], [{1, 2}, {0, 2}, {0, 3}]],
                             [{0, 1, 2}, {0, 1, 2}])
        expanded = views_for([[{0, 1}, {1, 2}, {2, 3}, {0, 1}, {1, 2}],
                              [{1, 2}, {0, 2}, {0, 3}, {1, 2}, {0, 2}]],
                             [{0, 1, 2}, {0, 1, 2}])
        first, second = self.check_oracle(original), self.check_oracle(expanded)
        self.assertAlmostEqual(first['minimum_iou'], second['minimum_iou'], places=8)

    def test_permuting_component_ids_preserves_the_objective(self):
        views = views_for([[{0, 1}, {2}, {3, 4}], [{0, 2}, {1}, {3, 5}]],
                          [{0, 1, 2}, {0, 1, 2}])
        expected = self.check_oracle(views)['minimum_iou']
        for v in views:
            v['component_pixels'] = [v['component_pixels'][i] for i in (2, 0, 1)]
        self.assertEqual(self.check_oracle(views)['minimum_iou'], expected)

    def test_unknown_crop_exterior_neither_penalizes_nor_identifies_members(self):
        views = views_for([[{0, 10}, {11}, set()], [{1, 12}, {13}, set()]],
                          [{0}, {1}], [{0, 1}, {0, 1}])
        report = self.check_oracle(views)
        self.assertEqual(report['minimum_iou'], 1)
        self.assertEqual(report['zero_known_support_component_ids'], [1, 2])

    def test_unexplainable_aperture_pixels_stay_in_denominator(self):
        views = views_for([[{0}, set()], [{1}, set()]], [{0, 5}, {1, 6, 7}])
        report = self.check_oracle(views)
        self.assertEqual(report['minimum_iou'], 1 / 3)

    def test_no_geometry_support_is_zero_agreement_not_perfect(self):
        views = views_for([[set(), set()], [set(), set()]], [{0}, {1}])
        report = self.check_oracle(views)
        self.assertEqual(report['minimum_iou'], 0)
        self.assertEqual(report['zero_known_support_component_ids'], [0, 1])

    def test_conditional_membership_ambiguity_is_reported(self):
        views = views_for([[{0, 1}, {0, 1}, {0}], [{2, 3}, {2, 3}, {2}]],
                          [{0, 1}, {2, 3}])
        report = self.check_oracle(views)
        selected = set(report['selected_component_ids'])
        expected_addable = [i for i in range(3) if i not in selected]
        expected_removable = [i for i in sorted(selected)
                              if independent_scores(views, selected - {i}) == [1, 1]]
        self.assertEqual(report['addable_without_scored_union_change'], expected_addable)
        self.assertEqual(report['individually_removable_without_scored_union_change'], expected_removable)

    def test_empty_aperture_is_unsupported_instead_of_scored_one(self):
        views = views_for([[{0}], [{0}]], [{0}, set()])
        with self.assertRaises(ValueError):
            optimize_support(views)

    def test_same_bottleneck_score_is_not_an_unchanged_union(self):
        views = views_for([[{0}, set()], [{0, 1}, {2}]], [{0, 1}, {0, 1}])
        report = self.check_oracle(views)
        self.assertEqual(report['minimum_iou'], .5)
        self.assertNotIn(1, report['addable_without_scored_union_change'])
        self.assertNotIn(1, report['individually_removable_without_scored_union_change'])

    def test_one_view_duplicate_ids_and_different_component_counts_fail(self):
        views = views_for([[{0}], [{1}]], [{0}, {1}])
        with self.assertRaises(ValueError):
            optimize_support(views[:1])
        views[1]['id'] = views[0]['id']
        with self.assertRaises(ValueError):
            optimize_support(views)
        views[1]['id'] = 'separate'
        views[1]['component_pixels'].append(np.array([], dtype=np.int64))
        with self.assertRaises(ValueError):
            optimize_support(views)

    def test_unsorted_duplicate_out_of_range_and_float_pixels_fail(self):
        for bad in (np.array([1, 0]), np.array([0, 0]), np.array([16]), np.array([-1]), np.array([0.])):
            with self.subTest(bad=bad):
                views = views_for([[{0}], [{0}]], [{0}, {0}])
                views[0]['component_pixels'][0] = bad
                with self.assertRaises(ValueError):
                    optimize_support(views)

    def test_positive_outside_known_and_non_boolean_mask_fail(self):
        views = views_for([[{0}], [{0}]], [{0}, {0}])
        views[0]['known_domain'][0, 0] = False
        with self.assertRaises(ValueError):
            optimize_support(views)
        views[0]['known_domain'][0, 0] = True
        views[0]['mask'] = views[0]['mask'].astype(np.uint8)
        with self.assertRaises(ValueError):
            optimize_support(views)

    def test_provenance_and_resource_limits_fail_explicitly(self):
        views = views_for([[{0}, {1}], [{0}, {1}]], [{0}, {0}])
        with self.assertRaises(ValueError):
            optimize_support(views, limits=replace(SupportLimits(), maximum_components=1))
        with self.assertRaises(ValueError):
            optimize_support(views, limits=replace(SupportLimits(), maximum_signature_bytes=1))
        views[0]['provenance'] = {}
        with self.assertRaises(ValueError):
            optimize_support(views)

    def test_inputs_are_not_mutated(self):
        views = views_for([[{0, 1}, {2}], [{0}, {1, 2}]], [{0, 1}, {0, 1}])
        before = [(v['mask'].copy(), v['known_domain'].copy(), [a.copy() for a in v['component_pixels']])
                  for v in views]
        self.check_oracle(views)
        for view, (mask, known, parts) in zip(views, before):
            np.testing.assert_array_equal(view['mask'], mask)
            np.testing.assert_array_equal(view['known_domain'], known)
            for actual, expected in zip(view['component_pixels'], parts):
                np.testing.assert_array_equal(actual, expected)

    def test_solver_timeout_does_not_become_an_infeasibility_bound(self):
        views = views_for([[{0}, {1}, {2}, {8, 9}], [{0}, {1}, {2}, {8, 9}]],
                          [{0, 1, 2}, {0, 1, 2}])
        with patch('reconstruction.support_union.milp', return_value=OptimizeResult(
                status=1, message='independent timeout control', x=None)):
            report = optimize_support(views)
        self.assertEqual(report['upper_bound'], 1)
        self.assertLess(report['minimum_iou'], 1)
        self.assertGreater(report['remaining_gap'], 0)

    def test_solver_error_and_nonfinite_incumbent_cannot_close_gap(self):
        views = views_for([[{0}, {1}, {2}, {8, 9}], [{0}, {1}, {2}, {8, 9}]],
                          [{0, 1, 2}, {0, 1, 2}])
        for status in (3, 4):
            with self.subTest(status=status):
                with patch('reconstruction.support_union.milp', return_value=OptimizeResult(
                        status=status, message='independent failed control', x=np.array([np.nan]))):
                    report = optimize_support(views)
                self.assertEqual(report['upper_bound'], 1)
                self.assertGreater(report['remaining_gap'], 0)

    def test_feasible_status_cannot_promote_a_failed_or_nonfinite_incumbent(self):
        views = views_for([[{0}, {1}, {2}, {8, 9}], [{0}, {1}, {2}, {8, 9}]],
                          [{0, 1, 2}, {0, 1, 2}])
        for value in (0., np.nan):
            def fake_result(c, **kwargs):
                return OptimizeResult(status=0, message='independent invalid feasible control',
                                      x=np.full(len(c), value))
            with self.subTest(value=value):
                with patch('reconstruction.support_union.milp', side_effect=fake_result):
                    report = optimize_support(views)
                self.assertEqual(report['minimum_iou'], .6)
                self.assertEqual(report['upper_bound'], 1)
                self.assertGreater(report['remaining_gap'], 0)


class ApertureUnionTests(unittest.TestCase):
    def hypothesis(self, name, positive, known):
        mask = np.zeros((2, 2), bool); domain = np.zeros((2, 2), bool)
        mask.ravel()[positive] = True; domain.ravel()[known] = True
        return {'id': name, 'mask': mask, 'known_domain': domain,
                'provenance': {'independent_test': name}}

    def test_positive_union_and_all_constituent_negative_domain(self):
        a = self.hypothesis('a', [0], [0, 1, 2])
        b = self.hypothesis('b', [3], [1, 3])
        result = union_apertures([a, b])
        self.assertEqual(np.flatnonzero(result['mask']).tolist(), [0, 3])
        self.assertEqual(np.flatnonzero(result['known_domain']).tolist(), [0, 1, 3])
        self.assertEqual([x['id'] for x in result['provenance']['constituents']], ['a', 'b'])
        self.assertEqual(np.flatnonzero(a['known_domain']).tolist(), [0, 1, 2])

    def test_union_preserves_singleton_and_empty_positive_prediction(self):
        a = self.hypothesis('empty', [], [0, 1])
        result = union_apertures([a])
        self.assertFalse(result['mask'].any())
        np.testing.assert_array_equal(result['known_domain'], a['known_domain'])

    def test_union_rejects_empty_inventory_duplicate_ids_and_unavailable_positive(self):
        with self.assertRaises(ValueError):
            union_apertures([])
        a = self.hypothesis('same', [0], [0, 1])
        with self.assertRaises(ValueError):
            union_apertures([a, a])
        a['known_domain'][0, 0] = False
        with self.assertRaises(ValueError):
            union_apertures([a])


if __name__ == '__main__':
    unittest.main()
