"""Exact overlap counterexamples, sufficient proofs and explicitly bounded scope."""
from copy import deepcopy
from fractions import Fraction
import json
import unittest

import numpy as np

from reconstruction.optical_group_validation import CoincidentPatchPolicy, ExactBudgetExceeded, ExactWork, validate_optical_group_ties


def primitive(identifier='sheet', points=None, faces=None, normals=None):
    p = np.array(points if points is not None else [[0., 0., 0.], [1., 0., 0.], [0., 1., 0.]])
    return {'id': identifier, 'positions': p.astype(np.float64),
        'indices': np.array(faces if faces is not None else [[0, 1, 2]], dtype=np.int64),
        'normals': np.array(normals if normals is not None else np.tile([0., 0., 1.], (len(p), 1))),
        'uv': np.column_stack((p[:, 0], p[:, 1])).astype(float)}


def group(identifier, members):
    return {'group_id': identifier, 'coordinate_frame_id': 'world-meters', 'appearance_sha256': 'a'*64,
            'primitives': members}


class CoincidentOpticalGroupTests(unittest.TestCase):
    def test_rational_raw_intermediates_are_checked_even_when_result_reduces_to_small_integer(self):
        # These follow the browser's explicit integer cross-products. In
        # particular the add/sub numerator cannot disappear inside Fraction's
        # normalization before the integer width and work gates see it.
        for method, a, b in (
                ('add', Fraction(127, 128), Fraction(129, 128)),
                ('sub', Fraction(127, 128), Fraction(-129, 128)),
                ('mul', Fraction(255, 254), Fraction(254, 255)),
                ('div', Fraction(255, 254), Fraction(255, 254))):
            with self.subTest(method=method), self.assertRaisesRegex(ExactBudgetExceeded, 'integer_budget'):
                getattr(ExactWork(maximum_bits=8), method)(a, b)
        # Cancellation at zero still charges unreduced cross-products, their
        # sum, denominator, and normalization; a tiny work budget refuses.
        with self.assertRaisesRegex(ExactBudgetExceeded, 'work_budget'):
            ExactWork(maximum_bits=64, maximum_work=3).sub(Fraction(127, 128), Fraction(127, 128))

    def test_explicit_rational_arithmetic_matches_fraction_with_sufficient_budget(self):
        work = ExactWork(maximum_bits=128)
        rng = np.random.default_rng(102194)
        for _ in range(100):
            a = Fraction(int(rng.integers(-500, 500)), int(rng.integers(1, 500)))
            b = Fraction(int(rng.integers(1, 500)), int(rng.integers(1, 500)))
            self.assertEqual(work.add(a, b), a+b)
            self.assertEqual(work.sub(a, b), a-b)
            self.assertEqual(work.mul(a, b), a*b)
            self.assertEqual(work.div(a, b), a/b)
            self.assertEqual(work.add(2, a), 2+a)
            self.assertEqual(work.div(a, -2), a/-2)
        with self.assertRaisesRegex(ValueError, 'zero_denominator'): work.div(Fraction(1, 2), 0)

    def validate(self, groups, **kwargs):
        result = validate_optical_group_ties(groups, **kwargs)
        self.assertFalse(result['accepted'])
        self.assertEqual(result['quality_verdict'], 'unmeasured')
        json.dumps(result, allow_nan=False)
        return result

    def test_exact_duplicate_with_reversed_winding_and_normal_magnitude_is_proven(self):
        first, second = primitive(), primitive('copy')
        second['indices'] = np.array([[2, 1, 0]])
        second['normals'] *= np.array([[-2.], [-4.], [-.5]])
        before = deepcopy(second)
        report = self.validate([group('lens', [first, second])])
        self.assertEqual(report['status'], 'coincident_patch_contract_satisfied')
        self.assertEqual(report['counts']['proven_same_group_pairs'], 1)
        for key in ('positions', 'indices', 'normals', 'uv'):
            np.testing.assert_array_equal(second[key], before[key])

    def test_conflicting_normals_and_even_tiny_uv_difference_are_unsupported(self):
        for mode, reason in [('normal', 'conflicting_constant_normal_directions'), ('v', 'conflicting_intrinsic_v_affine_fields')]:
            first, second = primitive(), primitive('copy')
            if mode == 'normal':
                second['normals'][:] = [.5, 0., 1.]
            else:
                second['uv'][0, 1] = 2.**-80
            report = self.validate([group('lens', [first, second])])
            self.assertTrue(report['complete'])
            self.assertEqual(report['status'], 'unsupported')
            self.assertEqual(report['reason_counts'][reason], 1)

    def test_retriangulated_overlap_constant_normal_and_affine_v_prove_without_sampling(self):
        points = [[0., 0., 0.], [1., 0., 0.], [1., 1., 0.], [0., 1., 0.]]
        first = primitive('diagonal-a', points, [[0, 1, 2], [0, 2, 3]])
        second = primitive('diagonal-b', points, [[0, 1, 3], [1, 2, 3]])
        second['normals'] *= -3
        report = self.validate([group('lens', [first, second])])
        self.assertEqual(report['counts']['positive_area_pairs'], 4)
        self.assertEqual(report['counts']['proven_same_group_pairs'], 4)
        self.assertEqual(report['status'], 'coincident_patch_contract_satisfied')

    def test_identical_varying_normal_triangle_is_proven_but_retessellation_is_not_guessed(self):
        normals = [[0., 0., 1.], [.1, 0., 1.], [0., .1, 1.]]
        first = primitive(normals=normals)
        second = primitive('duplicate', normals=-2*np.array(normals))
        second['indices'] = np.array([[1, 0, 2]])
        report = self.validate([group('lens', [first, second])])
        self.assertEqual(report['reason_counts']['identical_triangle_uniform_corner_direction_sign'], 1)
        subset = primitive('subset', [[0., 0., 0.], [.5, 0., 0.], [0., .5, 0.]], normals=normals)
        report = self.validate([group('lens', [first, subset])])
        self.assertEqual(report['reason_counts']['unproven_retessellated_varying_normal_field'], 1)

    def test_cornerwise_sign_changes_cannot_be_mistaken_for_symmetric_field_equivalence(self):
        first = primitive(normals=[[0., 0., 1.], [.1, 0., 1.], [0., .1, 1.]])
        second = deepcopy(first); second['id'] = 'copy'; second['normals'][1] *= -1
        report = self.validate([group('lens', [first, second])])
        self.assertEqual(report['reason_counts']['unproven_nonuniform_normal_sign'], 1)

    def test_shared_material_does_not_merge_distinct_coincident_groups(self):
        report = self.validate([group('left', [primitive()]), group('right', [primitive()])])
        self.assertEqual(report['reason_counts']['cross_group_coincident_patch_undefined_order'], 1)
        self.assertEqual(report['counts']['unsupported_pairs'], 1)

    def test_no_positive_area_floor_hides_a_tiny_conflicting_patch(self):
        epsilon = 2.**-80
        small = primitive('tiny', [[0., 0., 0.], [epsilon, 0., 0.], [0., epsilon, 0.]],
                          normals=np.tile([1., 0., 1.], (3, 1)))
        report = self.validate([group('lens', [primitive(), small])])
        self.assertEqual(report['counts']['unsupported_pairs'], 1)
        self.assertTrue(report['complete'])

    def test_vertical_planes_use_a_nondegenerate_projection_and_shared_edges_are_outside_scope(self):
        a = primitive('a', [[0., 0., 0.], [0., 1., 0.], [0., 0., 1.]])
        b = deepcopy(a); b['id'] = 'b'
        report = self.validate([group('lens', [a, b])])
        self.assertEqual(report['counts']['proven_same_group_pairs'], 1)
        b = primitive('adjacent', [[1., 0., 0.], [1., 1., 0.], [0., 1., 0.]])
        report = self.validate([group('lens', [primitive(), b])])
        self.assertEqual(report['counts']['positive_area_pairs'], 0)

    def test_float32_export_must_be_revalidated_when_separate_planes_become_coincident(self):
        first, second = primitive(), primitive()
        first['positions'][:, 2] = 1.
        second['positions'][:, 2] = 1.+2.**-30
        inputs = [group('a', [first]), group('b', [second])]
        report = self.validate(inputs)
        self.assertEqual(report['counts']['positive_area_pairs'], 0)
        for g in inputs:
            for p in g['primitives']:
                p['positions'] = p['positions'].astype(np.float32)
        report = self.validate(inputs)
        self.assertEqual(report['counts']['unsupported_pairs'], 1)

    def test_budget_is_incomplete_not_a_partial_certificate(self):
        inputs = [group('lens', [primitive('a'), primitive('b'), primitive('c')])]
        report = self.validate(inputs, policy=CoincidentPatchPolicy(maximum_candidate_pairs=1))
        self.assertFalse(report['complete'])
        self.assertEqual(report['status'], 'unsupported')
        self.assertIn('candidate_pair_budget_exceeded', report['reasons'])

    def test_large_exact_budget_cannot_overflow_or_inflate_float_broadphase_bounds(self):
        # The first case exceeds float range after exact common-grid scaling;
        # the second converts but outward nextafter reaches infinity.
        for height in (2.**-100, 1.):
            with self.subTest(height=height):
                first = primitive('a', [[0., 0., 0.], [np.finfo(float).max, 0., 0.], [0., height, 0.]])
                first['uv'][:] = 0.
                second = deepcopy(first); second['id'] = 'b'
                report = self.validate([group('lens', [first, second])],
                    policy=CoincidentPatchPolicy(maximum_integer_bits=2048))
                self.assertFalse(report['complete'])
                self.assertEqual(report['status'], 'unsupported')
                self.assertEqual(report['reasons'], ['broadphase_float_range_exceeded'])
                self.assertEqual(report['counts']['candidate_pairs'], 0)

    def test_degenerate_geometry_is_explicit_and_frame_identity_required(self):
        bad = primitive(); bad['positions'][2] = bad['positions'][1]
        report = self.validate([group('lens', [bad])])
        self.assertFalse(report['complete'])
        self.assertIn('exact_degenerate_triangle', report['reasons'][0])
        second = group('other', [primitive()]); second['coordinate_frame_id'] = 'different'
        with self.assertRaisesRegex(ValueError, 'same explicit coordinate frame'):
            self.validate([group('lens', [primitive()]), second])


if __name__ == '__main__':
    unittest.main()
