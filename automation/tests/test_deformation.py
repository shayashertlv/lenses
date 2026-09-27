from dataclasses import replace
import json
import unittest

import numpy as np

from reconstruction.camera import Camera, project
from reconstruction.deformation import CageField, NormalizedField, ReprojectionConstraint, assess_proposal, fit_cage, measure_constraints, projection_jacobian
from reconstruction.deformation import _bounded_nullspace, _reprojection_jacobian, _reprojection_residual


def constraint(points, target, camera, name, source='a', sigma=1.):
    return ReprojectionConstraint(name, source * 64, source * 64, camera, points,
                                  project(target, camera), np.full((len(points), 2), sigma))


class DeformationTests(unittest.TestCase):
    def test_projection_derivative_includes_perspective_yaw_pitch_and_roll(self):
        points = np.random.default_rng(10).uniform(-.4, .4, (15, 3))
        for perspective in (0, .6):
            camera = Camera(37, 18, -12, perspective, 450, 320, 240)
            expected = np.empty((len(points), 2, 3))
            for axis in range(3):
                delta = np.zeros(3)
                delta[axis] = 1e-6
                expected[:, :, axis] = (project(points + delta, camera) - project(points - delta, camera)) / 2e-6
            np.testing.assert_allclose(projection_jacobian(points, camera), expected, rtol=1e-8, atol=1e-7)

    def test_one_shared_field_transports_contact_and_has_independent_derivative(self):
        random = np.random.default_rng(20)
        field = CageField([-1, -1, -1], [1, 1, 1], random.normal(0, .008, (4, 3, 3, 3)))
        points = random.uniform(-.8, .8, (30, 3))
        moved, derivative = field.transform(points)
        expected = np.empty_like(derivative)
        for axis in range(3):
            delta = np.zeros(3)
            delta[axis] = 1e-6
            expected[:, :, axis] = (field.transform(points + delta)[0] - field.transform(points - delta)[0]) / 2e-6
        np.testing.assert_allclose(derivative, expected, atol=1e-9)
        np.testing.assert_array_equal(moved, field.transform(points.copy())[0])
        self.assertLess(field.gradient_bound(), 1)
        self.assertTrue(np.all(np.linalg.det(derivative) > 0))

    def test_injectivity_bound_catches_internal_fold_even_when_control_displacements_are_small(self):
        offsets = np.zeros((3, 3, 3, 3))
        offsets[1, :, :, 0] = -.12
        field = CageField([0, 0, 0], [.2, 1, 1], offsets)
        self.assertGreater(field.gradient_bound(), 1)
        self.assertLess(np.linalg.det(field.transform([[.025, .5, .5]])[1])[0], 0)

    def test_constant_translation_has_zero_gradient_and_boundary_coordinates_are_exact(self):
        displacement = np.broadcast_to([.02, -.01, .04], (3, 3, 3, 3)).copy()
        field = CageField([-1, -2, -3], [1, 2, 3], displacement)
        points = np.array([[-1, -2, -3], [1, 2, 3], [0, 0, 0]])
        moved, derivative = field.transform(points)
        np.testing.assert_allclose(moved, points + [.02, -.01, .04], atol=1e-15)
        np.testing.assert_allclose(derivative, np.broadcast_to(np.eye(3), derivative.shape), atol=1e-15)
        self.assertEqual(field.gradient_bound(), 0)
        with self.assertRaises(ValueError):
            field.transform([[1.001, 0, 0]])

    def test_multiview_fit_recovers_shared_deformation_on_unseen_points_and_camera(self):
        random = np.random.default_rng(37)
        points = random.uniform(-.4, .4, (45, 3))
        truth_offsets = np.zeros((3, 3, 3, 3))
        truth_offsets[1, :, :, 0] = .022
        truth_offsets[:, 1, :, 1] = -.014
        truth_offsets[:, :, 1, 2] = .018
        truth = CageField([-.5] * 3, [.5] * 3, truth_offsets)
        target = truth.transform(points)[0]
        views = [constraint(points, target, Camera(yaw, 12, 4, .25, 300, 200, 180), name, source)
                 for yaw, name, source in ((0, 'front', 'a'), (80, 'side', 'b'), (150, 'back', 'c'))]
        check_points = random.uniform(-.4, .4, (35, 3))
        held = constraint(check_points, truth.transform(check_points)[0], Camera(-38, 25, -12, .35, 350, 180, 170), 'held', 'd')
        field, report = fit_cage(views, [-.5] * 3, [.5] * 3, held_out=[held], shape=(3, 3, 3))
        self.assertTrue(report['retained'], report)
        self.assertLess(report['held_out_proposal'][0]['mean_px'], .05)
        self.assertLess(report['gradient_bound'], .45 + 1e-12)
        self.assertFalse(report['cameras_optimized'])
        self.assertEqual(report['quality_verdict'], 'unmeasured')
        np.testing.assert_allclose(field.transform(check_points)[0], truth.transform(check_points)[0], atol=.0005)

    def test_held_out_contradiction_restores_original_field(self):
        points = np.random.default_rng(21).uniform(-.4, .4, (20, 3))
        front = Camera(0, 0, 0, 0, 300, 200, 180)
        training = constraint(points, points + [.025, 0, 0], front, 'train', 'a')
        held = constraint(points, points, front, 'validation', 'b')
        field, report = fit_cage([training], [-.5] * 3, [.5] * 3, held_out=[held], shape=(2, 2, 2))
        self.assertFalse(report['retained'])
        self.assertTrue(report['fit_improved'])
        self.assertFalse(report['every_held_out_view_nonregressed'])
        np.testing.assert_array_equal(field.transform(points)[0], points)

    def test_fit_without_independent_evidence_remains_a_proposal(self):
        points = np.random.default_rng(22).uniform(-.4, .4, (12, 3))
        camera = Camera(0, 0, 0, 0, 200, 100, 100)
        train = constraint(points, points + [.02, 0, 0], camera, 'one')
        field, report = fit_cage([train], [-.5] * 3, [.5] * 3, shape=(2, 2, 2))
        self.assertTrue(report['retained'])
        self.assertFalse(report['held_out_present'])
        self.assertIsNone(report['every_held_out_view_nonregressed'])
        self.assertEqual(report['status'], 'proposal')
        self.assertEqual(report['quality_verdict'], 'unmeasured')
        self.assertFalse(report['pose_similarity_constrained'])
        self.assertIsNone(report['similarity_gauge'])
        self.assertIsNone(report['every_labeled_group_nonregressed'])
        self.assertEqual(report['ungrouped_view_ids'], ['one'])
        np.testing.assert_array_equal(train.points_xyz, points)

    def test_reused_source_cannot_be_called_independent_validation(self):
        points = np.array([[0, 0, 0.], [.2, .1, .1]])
        camera = Camera(0, 0, 0, 0, 200, 100, 100)
        train = constraint(points, points, camera, 'train')
        held = constraint(points, points, camera, 'renamed_same_photo')
        with self.assertRaises(ValueError):
            fit_cage([train], [-.5] * 3, [.5] * 3, held_out=[held])

    def test_normalized_field_adapter_preserves_world_units_and_jacobian(self):
        field = CageField([-.5] * 3, [.5] * 3, np.broadcast_to([.01, 0, 0], (2, 2, 2, 3)))
        adapted = NormalizedField(field, np.array([.002, .003, .004]), .14, 'a' * 64)
        points = np.array([[.002, .003, .004], [.02, .03, .04]])
        moved, derivative = adapted.transform(points)
        np.testing.assert_allclose(moved, points + [.0014, 0, 0], atol=1e-15)
        np.testing.assert_allclose(derivative, np.broadcast_to(np.eye(3), derivative.shape), atol=1e-15)
        self.assertEqual(adapted.to_dict()['source_sha256'], 'a' * 64)

    def test_displacement_limit_bounds_vector_norm_not_only_coordinates(self):
        points = np.random.default_rng(2).uniform(-.4, .4, (15, 3))
        target = points + [.049, .049, .049]
        data = [constraint(points, target, Camera(yaw, 0, 0, 0, 300, 150, 150), str(yaw), source)
                for yaw, source in ((0, 'a'), (90, 'b'))]
        field, report = fit_cage(data, [-.5] * 3, [.5] * 3, shape=(2, 2, 2))
        self.assertTrue(report['retained'])
        self.assertLessEqual(np.linalg.norm(field.displacements, axis=-1).max(), .05 + 1e-12)
        self.assertLess(report['trust_scale'], 1)

    def test_gauge_prevents_a_camera_like_translation_becoming_shape_improvement(self):
        points = np.random.default_rng(310).uniform(-.4, .4, (24, 3))
        data = [constraint(points, points + [.02, -.01, .015], Camera(yaw, 0, 0, 0, 250, 140, 130), name, source)
                for yaw, name, source in ((0, 'front', 'a'), (90, 'side', 'b'))]
        free, free_report = fit_cage(data, [-.5] * 3, [.5] * 3, shape=(2, 2, 2))
        self.assertTrue(free_report['retained'])
        self.assertGreater(np.linalg.norm(free.displacements), .01)
        pinned, report = fit_cage(data, [-.5] * 3, [.5] * 3, shape=(2, 2, 2),
                                  gauge_reference=(points, np.ones(len(points))))
        self.assertFalse(report['retained'], report)
        self.assertFalse(report['fit_improved'])
        self.assertTrue(report['pose_similarity_constrained'])
        self.assertEqual(report['similarity_gauge']['rank'], 7)
        self.assertLess(report['similarity_gauge']['returned_maximum_absolute_moment'], 1e-14)
        np.testing.assert_array_equal(pinned.displacements, 0)
        self.assertEqual(report['quality_verdict'], 'unmeasured')

    def test_surface_weighted_gauge_allows_nonsimilarity_shape_and_preserves_all_moments(self):
        # Known anisotropic affine change whose seven weighted moments vanish.
        points = np.asarray([(x, y, z) for x in (-.3, .3) for y in (-.3, .3) for z in (-.3, .3)])
        target = points * [1.025, .975, 1]
        data = [constraint(points, target, Camera(yaw, pitch, 0, 0, 300, 150, 150), name, source)
                for yaw, pitch, name, source in ((0, 0, 'front', 'a'), (90, 0, 'side', 'b'), (0, 90, 'top', 'c'))]
        weights = np.ones(len(points))
        field, report = fit_cage(data, [-.5] * 3, [.5] * 3, shape=(2, 2, 2), gauge_reference=(points, weights))
        self.assertTrue(report['retained'], report)
        displacement = field.transform(points)[0] - points
        center = np.average(points, axis=0, weights=weights)
        np.testing.assert_allclose(np.average(displacement, axis=0, weights=weights), 0, atol=1e-14)
        np.testing.assert_allclose(np.average(np.cross(points - center, displacement), axis=0, weights=weights), 0, atol=1e-14)
        self.assertAlmostEqual(float(np.average(np.sum((points - center) * displacement, axis=1), weights=weights)), 0, places=14)
        np.testing.assert_allclose(field.transform(points)[0], target, atol=1e-5)
        self.assertEqual(report['similarity_gauge']['reduced_parameter_count'], 17)
        self.assertTrue(np.isfinite(report['similarity_gauge']['condition_number']))
        self.assertFalse(report['similarity_gauge']['reference_sampling_verified'])
        json.dumps(report, allow_nan=False)

    def test_gauge_moments_hold_after_explicit_gradient_trust_scaling(self):
        points = np.asarray([(x, y, z) for x in (-.3, .3) for y in (-.3, .3) for z in (-.3, .3)])
        target = points * [1.06, .94, 1]
        data = [constraint(points, target, Camera(yaw, 0, 0, 0, 300, 150, 150), str(yaw), source)
                for yaw, source in ((0, 'a'), (90, 'b'))]
        field, report = fit_cage(data, [-.5] * 3, [.5] * 3, shape=(2, 2, 2),
                                 gauge_reference=(points, np.ones(len(points))), gradient_limit=.005)
        self.assertTrue(report['retained'], report)
        self.assertLess(report['trust_scale'], 1)
        self.assertLessEqual(field.gradient_bound(), .005 + 1e-12)
        self.assertLessEqual(np.linalg.norm(field.displacements, axis=-1).max(), .05 + 1e-12)
        self.assertLess(report['similarity_gauge']['proposal_maximum_absolute_moment'], 1e-14)
        self.assertLess(report['similarity_gauge']['returned_maximum_absolute_moment'], 1e-14)

    def test_gauge_trust_parameterization_has_correct_derivative_and_no_basis_orientation_clipping(self):
        random = np.random.default_rng(77)
        nullspace = np.linalg.qr(random.normal(size=(24, 17)))[0]
        vector = random.normal(0, .02, 17)
        maximum = .05
        bounded, derivative = _bounded_nullspace(nullspace, vector, maximum)
        numeric = np.column_stack([
            (_bounded_nullspace(nullspace, vector + np.eye(17)[axis] * 1e-7, maximum)[0] -
             _bounded_nullspace(nullspace, vector - np.eye(17)[axis] * 1e-7, maximum)[0]) / 2e-7
            for axis in range(17)])
        np.testing.assert_allclose(derivative, numeric, rtol=1e-7, atol=1e-9)
        self.assertLess(np.linalg.norm(bounded.reshape(-1, 3), axis=1).max(), maximum)
        rotated = np.linalg.qr(random.normal(size=(17, 17)))[0]
        np.testing.assert_allclose(_bounded_nullspace(nullspace @ rotated, rotated.T @ vector, maximum)[0], bounded, atol=1e-15)
        # Any interior field is reachable, including one close to the norm cap.
        desired = nullspace @ vector
        desired *= .049 / np.linalg.norm(desired.reshape(-1, 3), axis=1).max()
        raw = desired / np.sqrt(1 - (.049 / maximum) ** 2)
        np.testing.assert_allclose(_bounded_nullspace(nullspace, nullspace.T @ raw, maximum)[0], desired, atol=1e-14)
        at_zero, zero_derivative = _bounded_nullspace(nullspace, np.zeros(17), maximum)
        np.testing.assert_array_equal(at_zero, 0)
        np.testing.assert_allclose(zero_derivative, nullspace, atol=0)

    def test_gauged_fit_conservatively_limits_trials_near_a_perspective_near_plane(self):
        points = np.asarray([(x, y, z) for x in (-.2, .2) for y in (-.2, .2) for z in (.8, .94)])
        target = points.copy()
        target[:, 0] *= 1.1
        camera = Camera(0, 0, 0, 1, 100, 150, 150)
        item = constraint(points, target, camera, 'front')
        field, report = fit_cage([item], [-.5, -.5, .5], [.5, .5, 1.5], shape=(2, 2, 2),
                                 gauge_reference=(points, np.ones(len(points))))
        self.assertTrue(report['retained'], report)
        self.assertTrue(report['similarity_gauge']['camera_clearance_limited'])
        self.assertAlmostEqual(report['similarity_gauge']['trial_maximum_control_displacement_units'], .005)
        self.assertLessEqual(np.linalg.norm(field.displacements, axis=-1).max(), .005 + 1e-14)
        self.assertTrue(np.isfinite(project(field.transform(points)[0], camera)).all())
        json.dumps(report, allow_nan=False)

    def test_gauge_weights_apply_to_actual_reference_points_not_uniform_cage_nodes(self):
        points = np.random.default_rng(410).uniform(-.35, .35, (30, 3))
        weights = np.linspace(.2, 3, len(points))
        target = points.copy()
        target[:, 0] += .01 * points[:, 1] ** 2
        data = [constraint(points, target, Camera(0, 0, 0, 0, 400, 150, 150), 'front')]
        field, report = fit_cage(data, [-.5] * 3, [.5] * 3, shape=(3, 2, 2), gauge_reference=(points, weights))
        self.assertTrue(report['pose_similarity_constrained'])
        center = np.average(points, axis=0, weights=weights)
        displacement = field.transform(points)[0] - points
        np.testing.assert_allclose(np.average(displacement, axis=0, weights=weights), 0, atol=1e-14)
        np.testing.assert_allclose(np.average(np.cross(points - center, displacement), axis=0, weights=weights), 0, atol=1e-14)
        self.assertAlmostEqual(float(np.average(np.sum((points - center) * displacement, axis=1), weights=weights)), 0, places=14)
        np.testing.assert_allclose(report['similarity_gauge']['weighted_center'], center, atol=1e-15)

    def test_unusable_gauge_reference_is_explicitly_rejected(self):
        points = np.asarray([[-.2, -.2, 0], [.2, -.2, 0], [.2, .2, 0], [-.2, .2, 0]])
        camera = Camera(0, 0, 0, 0, 200, 100, 100)
        data = [constraint(points, points, camera, 'front')]
        references = [
            (points, [1, 1, 0, 1]), (points, [1, 1, -1, 1]), (points, [1, 1, float('nan'), 1]),
            (points, [1, 1]), (points + 1, [1] * 4), (np.zeros((4, 3)), [1] * 4),
            (np.column_stack(([-.3, -.1, .1, .3], np.zeros((4, 2)))), [1] * 4),
            (points, [1e-300, 1e300, 1, 1]), 'not a reference',
        ]
        for reference in references:
            with self.subTest(reference=str(reference)), self.assertRaises(ValueError):
                fit_cage(data, [-.5] * 3, [.5] * 3, shape=(2, 2, 2), gauge_reference=reference)
        # Planar surface samples do constrain all seven moments if noncollinear.
        _, report = fit_cage(data, [-.5] * 3, [.5] * 3, shape=(2, 2, 2), gauge_reference=(points, [1] * 4))
        self.assertEqual(report['similarity_gauge']['rank'], 7)

    def test_group_labels_are_owned_and_summarized_per_component(self):
        points = np.zeros((3, 3))
        camera = Camera(0, 0, 0, 0, 100, 50, 50)
        labels = ['rim', 'rim', 'bridge']
        item = replace(constraint(points, points, camera, 'front'), point_group_ids=labels)
        labels[0] = 'changed'
        self.assertEqual(item.point_group_ids, ('rim', 'rim', 'bridge'))
        measured = measure_constraints(CageField([-1] * 3, [1] * 3, np.zeros((2, 2, 2, 3))), [item])[0]
        self.assertEqual([group['group_id'] for group in measured['groups']], ['bridge', 'rim'])
        self.assertEqual([group['point_count'] for group in measured['groups']], [1, 2])
        for invalid in ('rim', ['rim'], ['rim', 'rim', ' '], ['rim', 'rim', 1]):
            with self.assertRaises(ValueError):
                replace(item, point_group_ids=invalid)

    def test_per_component_gate_rejects_a_bridge_regression_hidden_by_many_rim_points(self):
        points = np.zeros((21, 3))
        target = points.copy()
        target[:20, 0] += .02
        camera = Camera(0, 0, 0, 0, 200, 100, 100)
        item = constraint(points, target, camera, 'front')
        _, ungrouped = fit_cage([item], [-.5] * 3, [.5] * 3, shape=(2, 2, 2))
        self.assertTrue(ungrouped['retained'])
        grouped = replace(item, point_group_ids=('rim',) * 20 + ('bridge',))
        field, report = fit_cage([grouped], [-.5] * 3, [.5] * 3, shape=(2, 2, 2))
        self.assertTrue(report['fit_improved'])
        self.assertTrue(report['every_fit_view_nonregressed'])
        self.assertFalse(report['every_labeled_group_nonregressed'])
        self.assertFalse(report['retained'])
        self.assertEqual(report['ungrouped_view_ids'], [])
        bridge = next(group for group in report['point_group_checks'] if group['group_id'] == 'bridge')
        self.assertFalse(bridge['rms_nonregressed'])
        np.testing.assert_array_equal(field.displacements, 0)

    def test_pointwise_worst_gate_catches_local_damage_even_when_group_rms_and_max_improve(self):
        points = np.zeros((21, 3))
        target = points.copy()
        target[:20, 0] += .02
        camera = Camera(0, 0, 0, 0, 200, 100, 100)
        item = replace(constraint(points, target, camera, 'front'), point_group_ids=('frame',) * 21)
        _, report = fit_cage([item], [-.5] * 3, [.5] * 3, shape=(2, 2, 2))
        check = report['point_group_checks'][0]
        self.assertTrue(check['rms_nonregressed'])
        self.assertLess(report['fit_proposal'][0]['maximum_sigma'], report['fit_before'][0]['maximum_sigma'])
        self.assertGreater(check['maximum_pointwise_increase_sigma'], 1)
        self.assertFalse(check['worst_increase_within_policy'])
        self.assertFalse(report['retained'])
        target[:20, 0] = .004
        small = replace(constraint(points, target, camera, 'front'), point_group_ids=('frame',) * 21)
        _, allowed = fit_cage([small], [-.5] * 3, [.5] * 3, shape=(2, 2, 2))
        self.assertTrue(allowed['retained'])
        self.assertLess(allowed['point_group_checks'][0]['maximum_pointwise_increase_sigma'], 1)

    def test_selection_validation_also_applies_group_gates_and_is_never_reported_as_untouched(self):
        points = np.zeros((21, 3))
        target = points.copy()
        target[:, 0] = .02
        camera = Camera(0, 0, 0, 0, 200, 100, 100)
        train = replace(constraint(points, target, camera, 'train', 'a'), point_group_ids=('frame',) * 21)
        target[-1, 0] = 0
        held = replace(constraint(points, target, camera, 'validation', 'b'), point_group_ids=('rim',) * 20 + ('bridge',))
        _, report = fit_cage([train], [-.5] * 3, [.5] * 3, shape=(2, 2, 2), held_out=[held])
        self.assertTrue(report['selection_validation'])
        self.assertIn('not an untouched final test', report['held_out_interpretation'])
        self.assertTrue(report['every_held_out_view_nonregressed'])
        self.assertFalse(report['every_labeled_group_nonregressed'])
        self.assertFalse(report['retained'])
        validation_checks = [item for item in report['point_group_checks'] if item['scope'] == 'selection_validation']
        self.assertEqual(len(validation_checks), 2)

    def test_backtracked_proposal_is_remeasured_before_group_retention(self):
        points = np.zeros((21, 3))
        target = points.copy()
        target[:20, 0] = .02
        camera = Camera(0, 0, 0, 0, 200, 100, 100)
        item = replace(constraint(points, target, camera, 'front'), point_group_ids=('frame',) * len(points))
        base = CageField([-.5] * 3, [.5] * 3, np.zeros((2, 2, 2, 3)))
        proposed = CageField(base.lower, base.upper, np.broadcast_to([.019, 0, 0], (2, 2, 2, 3)))
        original = assess_proposal(base, proposed, [item])
        self.assertTrue(original['fit_improved'])
        self.assertFalse(original['retained'])
        scaled = CageField(base.lower, base.upper, proposed.displacements * .2)
        reassessed = assess_proposal(base, scaled, [item])
        self.assertTrue(reassessed['retained'])
        self.assertLess(reassessed['point_group_checks'][0]['maximum_pointwise_increase_sigma'], 1)
        self.assertNotEqual(original['fit_proposal'], reassessed['fit_proposal'])
        # fit_cage reports the identical public assessment, including policies.
        fitted, fitted_report = fit_cage([replace(item, point_group_ids=None)], base.lower, base.upper, shape=base.shape)
        public_report = assess_proposal(base, fitted, [replace(item, point_group_ids=None)])
        self.assertTrue(public_report['retained'])
        self.assertEqual(public_report, {key: fitted_report[key] for key in public_report})
        json.dumps(reassessed, allow_nan=False)

    def test_contour_normals_are_owned_unit_vectors_with_propagated_uncertainty(self):
        points = np.zeros((2, 3))
        camera = Camera(0, 0, 0, 0, 200, 100, 100)
        normals = np.array([[.6, .8], [1., 0.]])
        item = replace(constraint(points, points, camera, 'front'), normal_xy=normals, sigma_xy=np.tile([2., 3.], (2, 1)))
        normals[:] = 0
        np.testing.assert_array_equal(item.normal_xy, [[.6, .8], [1, 0]])
        self.assertFalse(item.normal_xy.flags.writeable)
        raw, standardized = _reprojection_residual(item, points + [.025, 0, 0])
        np.testing.assert_allclose(raw[:, 0], [3., 5.])
        np.testing.assert_allclose(standardized[:, 0], [3 / np.sqrt(7.2), 2.5])
        for invalid in (np.zeros((2, 2)), [[1, 0]], [[1, 0], [1, 1]], [[1, 0], [float('nan'), 0]]):
            with self.subTest(normal=invalid), self.assertRaises(ValueError):
                replace(item, normal_xy=invalid)

    def test_contour_normal_measurements_and_group_gates_ignore_tangential_motion(self):
        points = np.zeros((3, 3))
        camera = Camera(0, 0, 0, 0, 200, 100, 100)
        target = points + [.01, 0, 0]
        item = replace(constraint(points, target, camera, 'front'), normal_xy=np.tile([1, 0], (3, 1)),
                       point_group_ids=('rim',) * 3)
        base = CageField([-.5] * 3, [.5] * 3, np.zeros((2, 2, 2, 3)))
        tangential = CageField(base.lower, base.upper, np.broadcast_to([0, .03, 0], (2, 2, 2, 3)))
        self.assertEqual(measure_constraints(base, [item]), measure_constraints(tangential, [item]))
        normal_and_tangent = CageField(base.lower, base.upper, np.broadcast_to([.01, .03, 0], (2, 2, 2, 3)))
        report = assess_proposal(base, normal_and_tangent, [item])
        self.assertTrue(report['retained'])
        self.assertEqual(report['fit_proposal'][0]['mean_px'], 0)
        self.assertEqual(report['fit_proposal'][0]['residual_mode'], 'normal_only')
        self.assertEqual(report['fit_proposal'][0]['residual_dimensions'], 1)
        self.assertEqual(report['point_group_checks'][0]['residual_mode'], 'normal_only')
        # The same xy targets would incorrectly constrain the inferred tangent.
        self.assertFalse(assess_proposal(base, normal_and_tangent, [replace(item, normal_xy=None)])['retained'])

    def test_normal_only_fit_does_not_pin_the_unobserved_target_tangent(self):
        points = np.random.default_rng(112).uniform(-.3, .3, (15, 3))
        camera = Camera(0, 0, 0, 0, 300, 150, 150)
        item = replace(constraint(points, points + [.01, .04, 0], camera, 'front'),
                       normal_xy=np.tile([1., 0], (len(points), 1)))
        field, report = fit_cage([item], [-.5] * 3, [.5] * 3, shape=(2, 2, 2))
        self.assertTrue(report['retained'])
        self.assertLess(report['fit_proposal'][0]['mean_px'], .001)
        np.testing.assert_allclose(field.displacements[..., 1:], 0, atol=1e-12)
        self.assertGreater(field.displacements[..., 0].mean(), .009)
        changed_tangent = item.targets_xy.copy()
        changed_tangent[:, 1] += np.linspace(-50, 70, len(points))
        other, _ = fit_cage([replace(item, targets_xy=changed_tangent)], [-.5] * 3, [.5] * 3, shape=(2, 2, 2))
        np.testing.assert_allclose(other.displacements, field.displacements, atol=1e-12)

    def test_normal_only_analytic_jacobian_matches_finite_difference_with_perspective(self):
        random = np.random.default_rng(227)
        points = random.uniform(-.3, .3, (12, 3))
        angles = random.uniform(-np.pi, np.pi, len(points))
        normals = np.column_stack((np.cos(angles), np.sin(angles)))
        camera = Camera(37, -21, 16, .7, 450, 240, 170)
        item = replace(constraint(points, points + [.02, -.01, .03], camera, 'front'),
                       normal_xy=normals, sigma_xy=random.uniform(.5, 3, (len(points), 2)))
        analytic = _reprojection_jacobian(item, points)
        self.assertEqual(analytic.shape, (len(points), 1, 3))
        numeric = np.stack([
            (_reprojection_residual(item, points + np.eye(3)[axis] * 1e-6)[1] -
             _reprojection_residual(item, points - np.eye(3)[axis] * 1e-6)[1]) / 2e-6
            for axis in range(3)], axis=-1)
        np.testing.assert_allclose(analytic, numeric, rtol=1e-8, atol=1e-7)

    def test_data_rank_excludes_regularizers_and_trust_map_in_gauge_coordinates(self):
        points = np.asarray([(x, y, z) for x in (-.3, .3) for y in (-.3, .3) for z in (-.3, .3)])
        camera = Camera(0, 0, 0, 0, 300, 150, 150)
        item = replace(constraint(points, points * [1.02, .98, 1], camera, 'front'),
                       normal_xy=np.tile([1., 0], (len(points), 1)))
        reports = [fit_cage([item], [-.5] * 3, [.5] * 3, shape=(2, 2, 2),
                            gauge_reference=(points, np.ones(len(points))),
                            displacement_prior=prior, smoothness_prior=prior)[1]
                   for prior in (1e-6, 100.)]
        information = reports[0]['data_only_local_information']
        self.assertEqual(information['parameter_space'], 'gauge_nullspace')
        self.assertEqual(information['parameter_count'], 17)
        self.assertEqual(information['residual_count'], 8)
        self.assertLess(information['rank'], 17)
        self.assertFalse(information['full_column_rank'])
        self.assertFalse(information['regularization_rows_included'])
        self.assertFalse(information['optimizer_trust_mapping_included'])
        self.assertFalse(information['global_identifiability_established'])
        # Orthographic data sensitivity is independent of the fitted field, so
        # changing priors cannot supply the missing measured shape directions.
        self.assertEqual(information, reports[1]['data_only_local_information'])


if __name__ == '__main__':
    unittest.main()
