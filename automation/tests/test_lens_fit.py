"""Calibrated inverse tests; synthetic observations do not prove photo recovery."""

from dataclasses import replace
import json
import unittest

import numpy as np

from reconstruction.lens_appearance import DensityKeyframe, LensAppearance, ReflectanceKeyframe
from reconstruction.lens_fit import CalibratedLensSamples, LensFitConfig, _Model, fit_lens_appearance


def observations(lens, positions=(0, 0.25, 0.5, 0.75, 1), angles=(0, 35, 65),
                 environments=(((1, 1, 1), (0, 0, 0)), ((0, 0, 0), (1, 1, 1))), sigma=0.0005):
    rows = [(v, angle, background, reflected) for v in positions for angle in angles for background, reflected in environments]
    v = np.array([row[0] for row in rows])
    angle = np.array([row[1] for row in rows])
    background = np.array([row[2] for row in rows], dtype=float)
    reflected = np.array([row[3] for row in rows], dtype=float)
    observed = lens.evaluate(v, angle).compose(background, reflected)
    return CalibratedLensSamples(v, angle, observed, background, reflected, sigma, 1.0,
                                 "synthetic-controlled-illumination-v1", calibration_verified=True,
                                 source="unit-test synthetic optical samples")


def parameters(lens):
    return np.r_[[value for key in lens.optical_density_keyframes for value in key.optical_density_rgb], lens.normal_reflectance_rgb]


class LensFitTests(unittest.TestCase):
    def setUp(self):
        self.lens = LensAppearance((DensityKeyframe(0, (0.1, 0.6, 1.4)), DensityKeyframe(1, (2, 1, 0.4))),
                                   (0.9, 0.85, 0.8), roughness=0)
        self.config = LensFitConfig(starts=5)

    def test_recovers_high_mirror_and_spatial_density_gradient(self):
        result = fit_lens_appearance(observations(self.lens), self.config)
        self.assertEqual(result.status, "identified", result.reasons)
        np.testing.assert_allclose(parameters(result.best), parameters(self.lens), atol=1e-6)
        self.assertIs(result.usable_appearance, result.best)
        self.assertEqual(result.evidence["data_jacobian"]["rank"], 9)
        self.assertEqual(result.evidence["inlier_data_jacobian"]["rank"], 9)
        self.assertEqual(result.evidence["illumination_design_rank_per_channel"], [2, 2, 2])

    def test_recovers_uniform_tint_and_preserves_declared_fixed_parameters(self):
        lens = LensAppearance((DensityKeyframe(0, (0.2, 1.5, 2.3)),), (0.35, 0.15, 0.75), 1.62, 0.07)
        config = LensFitConfig(knot_positions=(0,), fixed_refractive_index=1.62, fixed_roughness=0.07, starts=4)
        result = fit_lens_appearance(observations(lens), config)
        self.assertEqual(result.status, "identified", result.reasons)
        np.testing.assert_allclose(parameters(result.best), parameters(lens), atol=1e-6)
        self.assertEqual(result.evidence["fixed_not_inferred"], {"refractive_index": 1.62, "roughness": 0.07})
        self.assertIn("conditional", result.evidence["uncertainty_scope"])

    def test_recovers_multiple_density_stops_without_frame_ids(self):
        lens = LensAppearance((DensityKeyframe(0, (0.1, 1.0, 0.3)), DensityKeyframe(0.3, (1.2, 0.4, 0.9)),
                               DensityKeyframe(0.7, (0.5, 1.3, 1.7)), DensityKeyframe(1, (2.0, 0.8, 0.2))), (0.7, 0.4, 0.2))
        data = observations(lens, positions=(0, 0.15, 0.3, 0.5, 0.7, 0.85, 1))
        config = LensFitConfig(knot_positions=(0, 0.3, 0.7, 1), starts=4)
        first = fit_lens_appearance(data, config)
        renamed = fit_lens_appearance(replace(data, calibration_id="different-id", source="different source"), config)
        self.assertEqual(first.status, "identified", first.reasons)
        np.testing.assert_allclose(parameters(first.best), parameters(lens), atol=1e-6)
        np.testing.assert_array_equal(parameters(first.best), parameters(renamed.best))
        self.assertNotEqual(first.evidence["samples_sha256"], renamed.evidence["samples_sha256"])

    def test_equal_white_exposure_at_constant_angle_is_ambiguous(self):
        lens = LensAppearance((DensityKeyframe(0, (1, 2, 0.5)),), (0.8, 0.7, 0.6))
        data = observations(lens, angles=(0,), environments=(((1, 1, 1), (1, 1, 1)),))
        result = fit_lens_appearance(data, LensFitConfig(knot_positions=(0,), starts=6))
        self.assertEqual(result.status, "ambiguous", result.reasons)
        self.assertIn("data_jacobian_rank_deficient", result.reasons)
        self.assertEqual(result.evidence["data_jacobian"]["rank"], 3)
        self.assertEqual(result.evidence["illumination_design_rank_per_channel"], [1, 1, 1])
        self.assertIsNone(result.usable_appearance)
        self.assertGreater(len(result.alternatives), 0)
        for alternative in result.alternatives:
            predicted = alternative.appearance.evaluate(data.v, data.angle_degrees).compose(data.background_linear_rgb, data.reflected_linear_rgb)
            np.testing.assert_allclose(predicted, data.observed_linear_rgb, atol=1e-6)

    def test_angular_only_data_can_have_full_local_information_without_global_certificate(self):
        lens = LensAppearance((DensityKeyframe(0, (1.0, 0.7, 0.3)),), (0.2, 0.5, 0.8))
        data = observations(lens, angles=(0, 15, 30, 45, 60, 75, 85),
                            environments=(((1, 1, 1), (1, 1, 1)),), sigma=0.00001)
        result = fit_lens_appearance(data, LensFitConfig(knot_positions=(0,), starts=5))
        self.assertEqual(result.status, "ambiguous", result.reasons)
        self.assertIn("global_separation_certificate_not_established", result.reasons)
        self.assertNotIn("data_jacobian_rank_deficient", result.reasons)
        self.assertFalse(result.evidence["direct_separation_certificate"]["established"])
        self.assertEqual(result.evidence["illumination_design_rank_per_channel"], [1, 1, 1])
        self.assertEqual(result.evidence["data_jacobian"]["rank"], 6)
        np.testing.assert_allclose(parameters(result.best), parameters(lens), atol=1e-5)

    def test_two_exact_materials_cannot_be_missed_by_converged_multistarts(self):
        # Adversarial nonlinear fold: every initial optimizer run can converge
        # to one precise basin although a very different exact material exists.
        angle = [15.76442520459711, 40.494639936402415]
        background = np.repeat([[1.3152766733960304], [0.2876271598836904]], 3, axis=1)
        reflected = np.repeat([[1.7025208573230342], [0.36762317346480466]], 3, axis=1)
        observed = np.repeat([[0.7904134094701489], [0.16493203683633464]], 3, axis=1)
        roots = [(0.5699189577257454, 0.05552916794304101), (0.977714326927838, 0.24980184807590544)]
        for density, reflection in roots:
            lens = LensAppearance((DensityKeyframe(0, (density,) * 3),), (reflection,) * 3)
            np.testing.assert_allclose(lens.evaluate([0, 1], angle).compose(background, reflected), observed, atol=2e-15)
        for sigma in (1e-4, 1e-5):
            samples = CalibratedLensSamples([0, 1], angle, observed, background, reflected, sigma, 1,
                                            "nonlinear-two-root-regression", True)
            result = fit_lens_appearance(samples, LensFitConfig(knot_positions=(0,), starts=8))
            with self.subTest(sigma=sigma):
                self.assertEqual(result.status, "ambiguous", result.reasons)
                self.assertIn("global_separation_certificate_not_established", result.reasons)
                self.assertIsNone(result.usable_appearance)
                self.assertEqual(result.evidence["data_jacobian"]["rank"], 6)

    def test_almost_proportional_pose_illumination_fails_noise_aware_certificate(self):
        lens = LensAppearance((DensityKeyframe(0, (1, 0.7, 0.3)),), (0.2, 0.5, 0.8))
        data = observations(lens, angles=(0, 15, 30, 45, 60, 75, 85), sigma=0.00001,
                            environments=(((1, 1, 1), (1, 1, 1)), ((1, 1, 1), (1.000001, 1.000001, 1.000001))))
        result = fit_lens_appearance(data, LensFitConfig(knot_positions=(0,), starts=4))
        self.assertEqual(result.status, "ambiguous", result.reasons)
        self.assertEqual(result.evidence["data_jacobian"]["rank"], 6)
        self.assertIn("global_separation_certificate_not_established", result.reasons)
        self.assertEqual(result.evidence["direct_separation_certificate"]["certified_group_count_rgb"], [0, 0, 0])

    def test_missing_gradient_endpoint_coverage_is_not_hidden_by_algebraic_rank(self):
        data = observations(self.lens, positions=(0.45, 0.475, 0.5, 0.525, 0.55), sigma=0.00001)
        result = fit_lens_appearance(data, self.config)
        self.assertEqual(result.status, "ambiguous", result.reasons)
        self.assertIn("density_knot_positions_lack_observed_coverage", result.reasons)
        self.assertEqual(result.evidence["data_jacobian"]["rank"], 9)
        self.assertTrue(np.all(np.array(result.evidence["knot_basis_support_per_channel"]) < 0.8))

    def test_near_total_mirror_is_ambiguous_below_the_noise_floor(self):
        lens = LensAppearance((DensityKeyframe(0, (0.4, 0.6, 0.8)),), (0.9999, 0.9998, 0.9997))
        data = observations(lens, sigma=0.001)
        result = fit_lens_appearance(data, LensFitConfig(knot_positions=(0,), starts=5))
        self.assertEqual(result.status, "ambiguous", result.reasons)
        self.assertEqual(result.evidence["data_jacobian"]["rank"], 6)
        self.assertIn("weak_parameter_direction_below_noise_precision", result.reasons)
        self.assertIn("parameter_uncertainty_exceeds_requested_precision", result.reasons)
        self.assertLess(result.evidence["standardized_rmse"], 0.001)

    def test_dense_tint_does_not_become_identified_from_tiny_residuals(self):
        lens = LensAppearance((DensityKeyframe(0, (10, 9, 11)),), (0.2, 0.3, 0.4))
        result = fit_lens_appearance(observations(lens, sigma=0.001), LensFitConfig(knot_positions=(0,), starts=4))
        self.assertEqual(result.status, "ambiguous", result.reasons)
        self.assertIn("weak_parameter_direction_below_noise_precision", result.reasons)
        self.assertLess(result.evidence["standardized_rmse"], 0.01)

    def test_total_mirror_preserves_unobservable_density_alternatives(self):
        lens = LensAppearance((DensityKeyframe(0, (0.4, 0.6, 0.8)),), (1, 1, 1))
        result = fit_lens_appearance(observations(lens, sigma=0.001), LensFitConfig(knot_positions=(0,), starts=4))
        self.assertEqual(result.status, "ambiguous", result.reasons)
        self.assertIn("weak_parameter_direction_below_noise_precision", result.reasons)
        self.assertGreater(len(result.alternatives), 0)

    def test_robust_fit_resists_sparse_unclipped_measurement_outliers(self):
        data = observations(self.lens)
        observed = data.observed_linear_rgb.copy()
        observed[8, 0] += 0.15
        result = fit_lens_appearance(replace(data, observed_linear_rgb=observed), self.config)
        self.assertEqual(result.status, "identified", result.reasons)
        self.assertGreater(result.evidence["outlier_channel_count"], 0)
        np.testing.assert_allclose(parameters(result.best), parameters(self.lens), atol=0.01)

    def test_unknown_calibration_clipping_and_encoded_color_are_rejected(self):
        data = observations(self.lens)
        for changed in (replace(data, calibration_verified=False), replace(data, calibration_id=None),
                        replace(data, clipped=True), replace(data, color_space="srgb"),
                        replace(data, confidence=0)):
            with self.subTest(changed=changed.calibration_id):
                result = fit_lens_appearance(changed, self.config)
                self.assertEqual(result.status, "rejected")
                self.assertIsNone(result.best)
                self.assertIsNone(result.usable_appearance)

    def test_invalid_noise_confidence_angles_and_shapes_are_rejected(self):
        data = observations(self.lens)
        for arguments in ({"noise_sigma": 0}, {"noise_sigma": float("nan")}, {"noise_sigma": -0.1},
                          {"confidence": 1.1}, {"confidence": -0.1}, {"confidence": True},
                          {"angle_degrees": 90}, {"v": 1.01}, {"background_linear_rgb": (-1, 0, 0)},
                          {"observed_linear_rgb": [0.1, 0.2, 0.3]}, {"clipped": 0}):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                replace(data, **arguments)
        with self.assertRaises(ValueError):
            LensFitConfig(starts=1)
        with self.assertRaises(ValueError):
            LensFitConfig(knot_positions=(0.1, 1))

    def test_insufficient_optimization_budget_never_claims_identified(self):
        result = fit_lens_appearance(observations(self.lens), replace(self.config, max_nfev=1))
        self.assertEqual(result.status, "nonconverged")
        self.assertIn("optimizer_did_not_converge", result.reasons)
        self.assertIsNone(result.usable_appearance)

    def test_wrong_angular_material_family_is_reported_as_model_mismatch(self):
        angular = (ReflectanceKeyframe(0, (0.1, 0.65, 0.85)), ReflectanceKeyframe(45, (0.75, 0.1, 0.55)),
                   ReflectanceKeyframe(90, (1, 1, 1)))
        lens = LensAppearance((DensityKeyframe(0, (0.6, 0.7, 0.8)),), angular[0].reflectance_rgb,
                              angular_reflectance_keyframes=angular)
        result = fit_lens_appearance(observations(lens), LensFitConfig(knot_positions=(0,), starts=4))
        self.assertEqual(result.status, "nonconverged")
        self.assertIn("observations_not_explained_at_declared_noise", result.reasons)

    def test_analytic_data_jacobian_matches_finite_difference_and_canonical_prediction(self):
        data = observations(self.lens)
        model = _Model(data, self.config)
        vector = parameters(self.lens)
        predicted, _ = model.predict_and_jacobian(vector)
        np.testing.assert_allclose(predicted, data.observed_linear_rgb, atol=1e-15)
        expected = np.zeros_like(model.jacobian(vector))
        for column in range(len(vector)):
            shift = np.zeros(len(vector))
            shift[column] = 1e-6
            expected[:, column] = (model.residual(vector + shift) - model.residual(vector - shift)) / 2e-6
        np.testing.assert_allclose(model.jacobian(vector), expected, rtol=1e-6, atol=1e-7)

    def test_reports_are_json_safe_reproducible_and_preserve_provenance(self):
        data = observations(self.lens)
        first = fit_lens_appearance(data, self.config)
        second = fit_lens_appearance(data, self.config)
        report = json.loads(json.dumps(first.to_report(), allow_nan=False))
        self.assertEqual(report["status"], "identified")
        self.assertEqual(report["evidence"]["samples_sha256"], second.evidence["samples_sha256"])
        self.assertEqual(len(report["evidence"]["fitter_sha256"]), 64)
        self.assertEqual(report["evidence"]["source"], data.source)
        self.assertIn("not raw-photo recovery", report["scope"])
        np.testing.assert_array_equal(parameters(first.best), parameters(second.best))


if __name__ == "__main__":
    unittest.main()
