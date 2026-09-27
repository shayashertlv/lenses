"""Image-edge association tests; selected edges do not establish lens semantics."""

import json
import unittest

import numpy as np

from reconstruction.edge_correspondence import EdgeMatchConfig, match_contour_edges


def step_image(left=(0, 0, 0), right=(255, 255, 255)):
    image = np.empty((100, 100, 3), dtype=np.uint8)
    image[:, :50] = left
    image[:, 50:] = right
    return image


class EdgeCorrespondenceTests(unittest.TestCase):
    def test_subpixel_edge_position_native_uncertainty_and_provenance(self):
        result = match_contour_edges(step_image(), [[47, 20], [47, 40], [47, 60]], [[2, 0]] * 3,
                                    config=EdgeMatchConfig(search_radius_px=6),
                                    source_sha256="A" * 64, model_sha256="b" * 64, camera_sha256="c" * 64)
        self.assertEqual(result.status, ("selected",) * 3)
        np.testing.assert_allclose(result.matched_xy, [[49.5, 20], [49.5, 40], [49.5, 60]], atol=.05)
        self.assertTrue((result.sigma_px >= .5).all())
        self.assertTrue((result.confidence > 0).all())
        self.assertTrue(result.selected_mask.all())
        self.assertEqual(result.diagnostics["source_sha256"], "a" * 64)
        self.assertEqual(result.diagnostics["model_sha256"], "b" * 64)
        self.assertEqual(result.diagnostics["camera_sha256"], "c" * 64)
        self.assertEqual(result.points[0]["selected_peak_index"], 0)
        self.assertEqual(result.diagnostics["semantic_identity"], "unmeasured")
        self.assertEqual(result.diagnostics["final_geometry_quality"], "unmeasured")
        json.dumps(result.to_report(), allow_nan=False)

    def test_bright_on_dark_and_dark_on_bright_have_equal_evidence(self):
        kwargs = dict(contour_xy=[[47, 50]], outward_normals=[[1, 0]], config=EdgeMatchConfig(search_radius_px=6))
        bright = match_contour_edges(step_image(), **kwargs)
        dark = match_contour_edges(step_image((255, 255, 255), (0, 0, 0)), **kwargs)
        self.assertEqual(bright.status, ("selected",))
        self.assertEqual(dark.status, bright.status)
        np.testing.assert_allclose(dark.matched_xy, bright.matched_xy)
        np.testing.assert_allclose(dark.confidence, bright.confidence)

    def test_color_only_edge_is_detected_without_gray_conversion(self):
        # These colors have nearly equal luma, but a strong RGB channel edge.
        image = step_image((200, 0, 0), (0, 59, 0))
        result = match_contour_edges(image, [[47, 50]], [[1, 0]], config=EdgeMatchConfig(search_radius_px=6))
        self.assertEqual(result.status, ("selected",))
        np.testing.assert_allclose(result.matched_xy[0], [49.5, 50], atol=.1)

    def test_two_rim_edges_remain_ambiguous_without_forced_nearest_choice(self):
        image = np.full((100, 100, 3), 255, dtype=np.uint8)
        image[:, 45:55] = 0
        # Initial location is nearer one edge: proximity must not silently settle semantics.
        result = match_contour_edges(image, [[47, 50]], [[1, 0]], config=EdgeMatchConfig(search_radius_px=10))
        self.assertEqual(result.status, ("ambiguous",))
        self.assertFalse(result.selected_mask.any())
        self.assertTrue(np.isnan(result.matched_xy).all())
        self.assertTrue(np.isnan(result.sigma_px).all())
        self.assertEqual(result.confidence[0], 0)
        self.assertEqual(len(result.points[0]["peaks"]), 2)
        self.assertIsNone(result.points[0]["selected_peak_index"])
        json.dumps(result.to_report(), allow_nan=False)

    def test_weak_faint_rim_is_missing_not_low_confidence_success(self):
        result = match_contour_edges(step_image((255, 255, 255), (250, 250, 250)), [[47, 50]], [[1, 0]],
                                    config=EdgeMatchConfig(search_radius_px=6))
        self.assertEqual(result.status, ("missing",))
        self.assertEqual(result.confidence[0], 0)

    def test_absent_or_outside_corridor_edge_is_missing(self):
        for image in (np.full((100, 100, 3), 128, np.uint8), step_image()):
            with self.subTest(flat=bool((image == 128).all())):
                result = match_contour_edges(image, [[25, 50]], [[1, 0]], config=EdgeMatchConfig(search_radius_px=6))
                self.assertEqual(result.status, ("missing",))
                self.assertTrue(np.isnan(result.matched_xy).all())

    def test_tangential_normal_does_not_match_unrelated_gradient(self):
        result = match_contour_edges(step_image(), [[49.5, 50]], [[0, 1]], config=EdgeMatchConfig(search_radius_px=6))
        self.assertEqual(result.status, ("missing",))

    def test_incomplete_search_corridor_is_out_of_bounds(self):
        result = match_contour_edges(step_image(), [[4, 50]], [[1, 0]], config=EdgeMatchConfig(search_radius_px=6))
        self.assertEqual(result.status, ("out_of_bounds",))
        self.assertLess(result.points[0]["valid_search_fraction"], 1)
        self.assertEqual(result.points[0]["peaks"], [])

    def test_peak_at_search_endpoint_is_not_supported(self):
        result = match_contour_edges(step_image(), [[43.5, 50]], [[1, 0]], config=EdgeMatchConfig(search_radius_px=6))
        self.assertEqual(result.status, ("missing",))
        self.assertFalse(result.selected_mask.any())

    def test_sensor_noise_raises_threshold_and_does_not_create_matches(self):
        rng = np.random.default_rng(803)
        image = np.clip(rng.normal(128, 10, (100, 100, 3)), 0, 255).astype(np.uint8)
        contour = np.column_stack((np.full(15, 50), np.linspace(15, 85, 15)))
        result = match_contour_edges(image, contour, np.tile([1, 0], (15, 1)), config=EdgeMatchConfig(search_radius_px=6))
        self.assertGreater(result.diagnostics["strength_threshold"], 4)
        self.assertFalse(result.selected_mask.any())

    def test_strong_shadow_is_an_edge_and_semantics_remain_unmeasured(self):
        # Image evidence alone cannot distinguish this hard shadow from a material edge.
        result = match_contour_edges(step_image((255, 255, 255), (120, 120, 120)), [[47, 50]], [[1, 0]],
                                    config=EdgeMatchConfig(search_radius_px=6))
        self.assertEqual(result.status, ("selected",))
        self.assertEqual(result.diagnostics["semantic_identity"], "unmeasured")
        self.assertIn("not semantic", result.diagnostics["confidence_interpretation"])

    def test_defaults_scale_to_contour_extent_without_upsampling_image(self):
        image = np.zeros((300, 300, 3), np.uint8)
        image[:, 150:] = 255
        result = match_contour_edges(image, [[148, 30], [148, 270]], [[1, 0]] * 2)
        self.assertAlmostEqual(result.diagnostics["search_radius_px"], 4.8)
        self.assertEqual(result.diagnostics["image_size"], [300, 300])
        self.assertEqual(result.status, ("selected", "selected"))

    def test_input_not_mutated_report_arrays_readonly_and_hashes_content_based(self):
        image, contour, normals = step_image(), np.array([[47., 50.]]), np.array([[2., 0.]])
        kwargs = dict(config=EdgeMatchConfig(search_radius_px=6))
        result = match_contour_edges(image, contour, normals, **kwargs)
        repeated = match_contour_edges(image, contour, normals, **kwargs)
        self.assertEqual(result.to_report(), repeated.to_report())
        np.testing.assert_array_equal(normals, [[2, 0]])
        for array in (result.contour_xy, result.unit_normals_xy, result.matched_xy, result.sigma_px, result.confidence):
            self.assertFalse(array.flags.writeable)
        contour[0, 0] += 1
        moved = match_contour_edges(image, contour, normals, **kwargs)
        self.assertNotEqual(result.diagnostics["initial_contour_and_normals_sha256"], moved.diagnostics["initial_contour_and_normals_sha256"])
        image[0, 0] = 1
        altered = match_contour_edges(image, contour, normals, **kwargs)
        self.assertNotEqual(result.diagnostics["image_pixels_sha256"], altered.diagnostics["image_pixels_sha256"])

    def test_invalid_input_rejected(self):
        invalid = [
            (step_image().astype(float), [[47, 50]], [[1, 0]], {}),
            (np.zeros((100, 100, 4), np.uint8), [[47, 50]], [[1, 0]], {}),
            (step_image(), [[47, 50]], [[0, 0]], {}),
            (step_image(), [[47, 50]], [[1, 0], [1, 0]], {}),
            (step_image(), [[np.nan, 50]], [[1, 0]], {}),
            (step_image(), [[47, 50]], [[1, 0]], {"source_sha256": "bad"}),
        ]
        for image, contour, normals, kwargs in invalid:
            with self.subTest(kwargs=kwargs, shape=image.shape), self.assertRaises(ValueError):
                match_contour_edges(image, contour, normals, **kwargs)
        for kwargs in ({"minimum_strength": 0}, {"competing_peak_ratio": 1}, {"search_radius_px": float("nan")},
                       {"sample_step_px": .001}, {"maximum_radius_px": 1}, {"search_radius_px": True}):
            with self.subTest(config=kwargs), self.assertRaises(ValueError):
                EdgeMatchConfig(**kwargs)


if __name__ == "__main__":
    unittest.main()
