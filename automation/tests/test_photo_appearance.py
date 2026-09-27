"""Observed-signal tests deliberately make no optical-identification claim."""
from copy import deepcopy
import hashlib
import json
import unittest

import numpy as np

from reconstruction.photo_appearance import (EROSION_RADIUS_PX, INTRINSIC_HEIGHT_BIN_COUNT,
                                             MAX_PHOTOMETRIC_SAMPLES, measure_region_appearance)


SOURCE = "a" * 64
PROVENANCE = {"method": "automatic_candidate_region_v1", "source_sha256": SOURCE,
              "assumptions": ["region membership is a hypothesis"]}
COORDINATES = {**PROVENANCE, "coordinate_fields": {
    "source": "candidate", "candidate_sha256": "b" * 64,
    "method": "candidate_local_y_height_proxy_and_projected_normals_v1",
    "assumptions": ["local Y is a proxy, not verified intrinsic UV"]}}


def measure(image, mask=None, **kwargs):
    mask = np.ones(image.shape[:2], dtype=bool) if mask is None else mask
    return measure_region_appearance(image, mask, source_sha256=SOURCE, region_id="region-1",
                                     provenance=kwargs.pop("provenance", PROVENANCE), **kwargs)


class PhotoAppearanceTests(unittest.TestCase):
    def test_exact_code_values_and_srgb_decode_are_not_calibrated_radiance(self):
        image = np.full((9, 9, 3), [0, 10, 11], dtype=np.uint8)
        report = measure(image)
        self.assertEqual(report["status"], "measured")
        self.assertEqual(report["signal"]["count"], 25)
        self.assertEqual(report["signal"]["observed_code_rgb"]["median"], [0, 10, 11])
        expected = [0, (10/255)/12.92, ((11/255+.055)/1.055)**2.4]
        np.testing.assert_allclose(report["signal"]["linear_signal_rgb"]["median"], expected, atol=1e-14)
        for key in ("calibration_status", "measurement_uncertainty_status", "semantic_identity_status", "optical_parameters_status"):
            self.assertEqual(report[key], "unmeasured")
        self.assertIn("not_calibrated_radiance", report["linear_signal_interpretation"])
        self.assertNotIn("accepted", report)
        self.assertNotIn("density", report)
        self.assertNotIn("reflectance", report)

    def test_fixed_erosion_and_image_border_exclusion_are_reported(self):
        image = np.full((11, 13, 3), 100, dtype=np.uint8)
        report = measure(image)
        radius = EROSION_RADIUS_PX
        expected = (11-2*radius)*(13-2*radius)
        coverage = report["coverage"]
        self.assertEqual(coverage["region_pixels"], 143)
        self.assertEqual(coverage["interior_pixels"], expected)
        self.assertEqual(coverage["boundary_excluded_pixels"], 143-expected)
        self.assertEqual(coverage["measured_fraction_of_region"], expected/143)
        self.assertEqual(report["policy"]["outside_image"], "excluded")
        self.assertEqual(report["coverage"]["semantic_region_coverage"], "unmeasured")

    def test_holes_and_small_disconnected_regions_are_not_filled_or_joined(self):
        image = np.full((15, 30, 3), 100, dtype=np.uint8)
        mask = np.zeros(image.shape[:2], bool)
        mask[1:14, 1:14] = True
        mask[7, 7] = False
        mask[2:4, 20:22] = True
        report = measure(image, mask)
        # 13x13 square erodes to9x9, and a 5x5 exclusion surrounds its hole.
        self.assertEqual(report["coverage"]["interior_pixels"], 81-25)
        self.assertTrue(all(not sample["photometric_valid"] for sample in report["samples"] if sample["xy"][0] > 15))

    def test_robust_stats_preserve_outlier_and_endpoint_evidence(self):
        image = np.full((11, 11, 3), 80, dtype=np.uint8)
        image[5, 5] = [255, 0, 130]
        report = measure(image)
        signal = report["signal"]
        self.assertEqual(signal["count"], 49)
        self.assertEqual(signal["observed_code_rgb"]["median"], [80, 80, 80])
        self.assertEqual(signal["observed_code_rgb"]["median_absolute_deviation"], [0, 0, 0])
        self.assertEqual(signal["observed_code_rgb"]["maximum"], [255, 80, 130])
        self.assertEqual(signal["endpoint_flags"]["low_count_rgb"], [0, 1, 0])
        self.assertEqual(signal["endpoint_flags"]["high_count_rgb"], [1, 0, 0])
        self.assertEqual(signal["endpoint_flags"]["any_channel_fraction"], 1/49)
        self.assertEqual(signal["fully_unclipped"]["count"], 48)
        self.assertEqual(signal["fully_unclipped"]["observed_code_rgb"]["maximum"], [80, 80, 80])
        outlier = next(sample for sample in report["samples"] if sample["xy"] == [5, 5])
        self.assertEqual(outlier["code_rgb"], [255, 0, 130])
        self.assertEqual(outlier["unclipped_rgb"], [False, False, True])
        self.assertFalse(outlier["fully_unclipped"])
        self.assertTrue(outlier["photometric_valid"])
        self.assertIsNotNone(outlier["linear_signal_rgb"])
        self.assertIn("not_proven_sensor_clipping", report["policy"]["endpoint_flags"])

    def test_nonopaque_pixels_are_excluded_without_any_background_composite(self):
        image = np.full((9, 9, 4), [80, 100, 120, 255], dtype=np.uint8)
        image[4, 4] = [255, 0, 240, 0]
        image[4, 5] = [20, 30, 40, 254]
        report = measure(image)
        self.assertEqual(report["signal"]["count"], 23)
        self.assertEqual(report["signal"]["observed_code_rgb"]["median"], [80, 100, 120])
        self.assertEqual(report["signal"]["endpoint_flags"]["any_channel_count"], 0)
        self.assertEqual(report["coverage"]["nonopaque_interior_pixels"], 2)
        invalid = [sample for sample in report["samples"] if not sample["opaque_alpha"]]
        self.assertEqual(len(invalid), 2)
        self.assertEqual({sample["alpha_code"] for sample in invalid}, {0, 254})
        self.assertTrue(all(sample["linear_signal_rgb"] is None and not sample["photometric_valid"] for sample in invalid))
        self.assertIn([255, 0, 240], [sample["code_rgb"] for sample in invalid])

    def test_all_nonopaque_or_thin_or_empty_regions_remain_unmeasured(self):
        rgba = np.full((9, 9, 4), [80, 100, 120, 100], dtype=np.uint8)
        nonopaque = measure(rgba)
        self.assertEqual(nonopaque["status"], "unmeasured")
        self.assertEqual(nonopaque["reasons"], ["no_opaque_interior_pixels"])
        self.assertIsNone(nonopaque["signal"]["observed_code_rgb"])
        image = rgba[:, :, :3]
        thin = np.zeros((9, 9), bool); thin[4, :] = True
        report = measure(image, thin)
        self.assertEqual(report["reasons"], ["no_interior_after_fixed_pixel_erosion"])
        self.assertTrue(report["samples"])
        empty = measure(image, np.zeros((9, 9), bool))
        self.assertEqual(empty["status"], "unmeasured")
        self.assertEqual(empty["reasons"], ["empty_region_hypothesis"])
        self.assertEqual(empty["samples"], [])
        self.assertIsNone(empty["coverage"]["measured_fraction_of_region"])
        self.assertIsNone(empty["signal"]["endpoint_flags"]["any_channel_fraction"])
        self.assertNotIn("absent", json.dumps(empty))
        json.dumps(empty, allow_nan=False)

    def test_gradient_bins_follow_rotated_coordinate_hypothesis_not_image_rows(self):
        size = 33
        v = np.tile(np.linspace(0, 1, size), (size, 1))
        codes = np.round(30 + v*190).astype(np.uint8)
        image = np.stack([codes, 240-codes, codes//2+20], axis=-1)
        mask = np.ones((size, size), bool)
        first = measure(image, mask, intrinsic_v=v, provenance=COORDINATES)
        rotated = measure(np.rot90(image), np.rot90(mask), intrinsic_v=np.rot90(v), provenance=COORDINATES)
        self.assertEqual(first["intrinsic_height_bins"], rotated["intrinsic_height_bins"])
        self.assertEqual(len(first["intrinsic_height_bins"]), INTRINSIC_HEIGHT_BIN_COUNT)
        medians = [entry["signal"]["observed_code_rgb"]["median"][0] for entry in first["intrinsic_height_bins"]]
        self.assertEqual(medians, sorted(medians))
        self.assertTrue(all(entry["coordinate_hypothesis"] for entry in first["intrinsic_height_bins"]))
        self.assertEqual(first["coordinate_provenance"]["method"], COORDINATES["coordinate_fields"]["method"])
        self.assertEqual(first["coordinate_provenance"]["assumptions"], ["local Y is a proxy, not verified intrinsic UV"])

    def test_missing_and_all_unknown_coordinates_do_not_fabricate_height_bins(self):
        image = np.repeat(np.arange(11, dtype=np.uint8)[:, None, None]*20, 11, axis=1)
        image = np.repeat(image, 3, axis=2)
        absent = measure(image)
        self.assertIsNone(absent["intrinsic_height_bins"])
        self.assertEqual(absent["coordinate_coverage"]["intrinsic_v_hypothesis"]["status"], "unavailable")
        self.assertTrue(all(sample["intrinsic_v_hypothesis"] is None for sample in absent["samples"]))
        unknown = measure(image, intrinsic_v=np.full((11, 11), np.nan), provenance=COORDINATES)
        self.assertEqual(unknown["status"], "measured")
        self.assertIsNone(unknown["intrinsic_height_bins"])
        self.assertEqual(unknown["coordinate_coverage"]["intrinsic_v_hypothesis"]["status"], "supplied_but_unmeasured")
        self.assertEqual(unknown["coordinate_coverage"]["intrinsic_v_hypothesis"]["finite_measured_count"], 0)
        json.dumps(unknown, allow_nan=False)

    def test_partial_coordinate_coverage_empty_bins_and_rear_angles_are_explicit(self):
        image = np.full((9, 9, 3), 120, np.uint8)
        v = np.full((9, 9), np.nan); v[2:7, 2:7] = .6; v[4, 4] = np.nan
        angles = np.full((9, 9), 110.0); angles[4, 4] = np.nan; angles[4, 5] = 90
        report = measure(image, intrinsic_v=v, incidence_degrees=angles, provenance=COORDINATES)
        self.assertEqual(report["coordinate_coverage"]["intrinsic_v_hypothesis"]["fraction_of_measured_pixels"], 24/25)
        bins = report["intrinsic_height_bins"]
        self.assertEqual([item["signal"]["count"] for item in bins], [0, 0, 24, 0])
        self.assertIsNone(bins[0]["signal"]["observed_code_rgb"])
        self.assertEqual(bins[2]["observed_v_range"], [.6, .6])
        self.assertEqual(bins[2]["incidence_degrees_hypothesis"]["statistics"]["maximum"], 110)
        self.assertTrue(all(sample["front_interface_eligible"] is False for sample in report["samples"]
                            if sample["incidence_degrees_hypothesis"] is not None))
        sample = next(sample for sample in report["samples"] if sample["xy"] == [4, 4])
        self.assertIsNone(sample["intrinsic_v_hypothesis"]); self.assertIsNone(sample["front_interface_eligible"])

    def test_bin_boundaries_do_not_double_count_and_final_endpoint_is_included(self):
        image = np.full((9, 9, 3), 120, np.uint8)
        v = np.tile(np.array([0, 0, 0, .25, .5, .75, 1, 1, 1], float), (9, 1))
        report = measure(image, intrinsic_v=v, provenance=COORDINATES)
        self.assertEqual([item["signal"]["count"] for item in report["intrinsic_height_bins"]], [5, 5, 5, 10])
        self.assertEqual(sum(item["signal"]["count"] for item in report["intrinsic_height_bins"]), 25)
        self.assertEqual(report["intrinsic_height_bins"][-1]["observed_v_range"], [.75, 1])

    def test_bounded_deterministic_samples_retain_clipped_and_excluded_strata(self):
        image = np.full((100, 100, 4), [80, 90, 100, 255], np.uint8)
        image[30, 30, :3] = [0, 255, 99]
        image[40, 40, 3] = 127
        first = measure(image); second = measure(image)
        self.assertEqual(first, second)
        self.assertEqual(len(first["samples"]), MAX_PHOTOMETRIC_SAMPLES)
        self.assertEqual(len({tuple(sample["xy"]) for sample in first["samples"]}), MAX_PHOTOMETRIC_SAMPLES)
        self.assertTrue(any(not sample["fully_unclipped"] and sample["photometric_valid"] for sample in first["samples"]))
        self.assertTrue(any(not sample["opaque_alpha"] for sample in first["samples"]))
        self.assertTrue(any(not sample["interior"] for sample in first["samples"]))
        self.assertGreater(first["signal"]["count"], len(first["samples"]))

    def test_inputs_are_unmodified_provenance_detached_and_hashes_bind_data(self):
        image = np.full((9, 9, 3), 100, np.uint8); mask = np.ones((9, 9), bool)
        v = np.full((9, 9), .5); v[0, 0] = np.nan
        provenance = deepcopy(COORDINATES)
        originals = (image.copy(), mask.copy(), v.copy(), deepcopy(provenance))
        first = measure(image, mask, intrinsic_v=v, provenance=provenance)
        for actual, original in zip((image, mask, v), originals[:3]):
            np.testing.assert_array_equal(actual, original)
        self.assertEqual(provenance, originals[3])
        provenance["coordinate_fields"]["assumptions"].append("later edit")
        self.assertEqual(len(first["coordinate_provenance"]["assumptions"]), 1)
        digest = first.pop("measurement_sha256")
        actual = hashlib.sha256(json.dumps(first, sort_keys=True, allow_nan=False, ensure_ascii=False,
                                          separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(actual, digest)
        image[4, 4, 0] += 1
        changed = measure(image, mask, intrinsic_v=v, provenance=COORDINATES)
        self.assertNotEqual(first["decoded_pixels_sha256"], changed["decoded_pixels_sha256"])
        self.assertFalse(changed["encoded_source_hash_verified_here"])

    def test_strict_image_mask_source_region_and_provenance_validation(self):
        image = np.full((9, 9, 3), 100, np.uint8); mask = np.ones((9, 9), bool)
        for invalid in (image.astype(float)/255, image[:, :, :2], np.zeros((0, 9, 3), np.uint8), image[:, :, 0]):
            with self.subTest(shape=invalid.shape), self.assertRaises(ValueError):
                measure(invalid)
        for invalid in (mask.astype(np.uint8), np.ones((8, 9), bool), mask[:, :, None]):
            with self.assertRaises(ValueError):
                measure(image, invalid)
        for source in ("", "A"*64, "f"*63, 123):
            with self.assertRaises(ValueError):
                measure_region_appearance(image, mask, source_sha256=source, region_id="r", provenance=PROVENANCE)
        for region in (None, "", " ", 2):
            with self.assertRaises(ValueError):
                measure_region_appearance(image, mask, source_sha256=SOURCE, region_id=region, provenance=PROVENANCE)
        for provenance in (None, {}, {"method": ""}, {"method": "x", "source_sha256": "c"*64},
                           {"method": "x", "nested": [float("nan")]}, {"method": "x", 2: "bad key"},
                           {"method": "x", "object": object()}):
            with self.subTest(provenance=provenance), self.assertRaises(ValueError):
                measure(image, provenance=provenance)

    def test_coordinate_shapes_domains_and_candidate_provenance_are_strict(self):
        image = np.full((9, 9, 3), 100, np.uint8); v = np.full((9, 9), .5)
        for bad in (np.full((9, 9), 2), np.zeros((8, 9)), np.full((9, 9), 1.01),
                    np.full((9, 9), -.1), np.full((9, 9), np.inf)):
            with self.subTest(dtype=bad.dtype), self.assertRaises(ValueError):
                measure(image, intrinsic_v=bad, provenance=COORDINATES)
        for angle in (-1, 181, np.inf):
            with self.assertRaises(ValueError):
                measure(image, incidence_degrees=np.full((9, 9), float(angle)), provenance=COORDINATES)
        for provenance in (PROVENANCE, {**PROVENANCE, "coordinate_fields": {}},
                           {**PROVENANCE, "coordinate_fields": {"source": "image_rows", "candidate_sha256": "b"*64, "method": "x"}},
                           {**PROVENANCE, "coordinate_fields": {"source": "candidate", "candidate_sha256": "bad", "method": "x"}}):
            with self.assertRaises(ValueError):
                measure(image, intrinsic_v=v, provenance=provenance)
        report = measure(image, incidence_degrees=np.zeros((9, 9)), provenance=COORDINATES)
        self.assertIsNone(report["intrinsic_height_bins"])


if __name__ == "__main__":
    unittest.main()
