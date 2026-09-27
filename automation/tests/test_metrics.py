import json
import unittest

import numpy as np
from scipy import ndimage

from reconstruction.metrics import score_components, score_masks


def glasses_components():
    """Synthetic evidence only: two rims, openings, bridge and visible temples."""
    rim = np.zeros((100, 180), dtype=bool)
    lens = np.zeros_like(rim)
    for left in (30, 100):
        rim[30:70, left:left + 45] = True
        lens[35:65, left + 5:left + 40] = True
    rim &= ~lens
    bridge = np.zeros_like(rim)
    bridge[44:49, 75:100] = True
    temples = np.zeros_like(rim)
    temples[42:49, 5:30] = True
    temples[42:49, 145:175] = True
    return {"rim": rim, "lens": lens, "bridge": bridge, "temples": temples}


class MaskMetricsTests(unittest.TestCase):
    def setUp(self):
        self.parts = glasses_components()
        self.reference = self.parts["rim"] | self.parts["bridge"] | self.parts["temples"]

    def assertFiniteReport(self, report):
        # JSON strict mode catches nonfinite nested floats; None is intentional
        # for unavailable evidence and must survive serialization as null.
        json.dumps(report, allow_nan=False)

    def test_identical_masks_have_exact_agreement(self):
        report = score_masks(self.reference, self.reference.copy())
        self.assertEqual(report["status"], "measured")
        self.assertEqual(report["foreground_iou"], 1.0)
        self.assertEqual(report["boundary"]["status"], "measured")
        self.assertEqual(report["boundary"]["precision"], 1.0)
        self.assertEqual(report["boundary"]["recall"], 1.0)
        self.assertEqual(report["boundary"]["symmetric"], {"mean": 0.0, "p95": 0.0, "max": 0.0})
        self.assertEqual(report["coverage"]["reference_boundary_fraction"], 1.0)
        self.assertFiniteReport(report)

    def test_empty_candidate_is_a_finite_measured_failure(self):
        report = score_masks(self.reference, np.zeros_like(self.reference))
        self.assertEqual(report["status"], "measured")
        self.assertEqual(report["foreground_iou"], 0)
        self.assertEqual(report["foreground_recall"], 0)
        self.assertEqual(report["missing_foreground_fraction"], 1)
        self.assertTrue(report["boundary"]["missing_candidate_contour"])
        self.assertEqual(report["boundary"]["f1"], 0)
        self.assertGreater(report["boundary"]["symmetric"]["max"], 1)
        self.assertFiniteReport(report)

    def test_no_reference_evidence_never_passes(self):
        empty = np.zeros_like(self.reference)
        for reference, candidate, expected in (
            (None, self.reference, "reference_missing"),
            (empty, empty, "reference_empty"),
            (empty, self.reference, "reference_empty"),
        ):
            with self.subTest(expected=expected):
                report = score_masks(reference, candidate)
                self.assertEqual(report["status"], "unmeasured")
                self.assertEqual(report["reason"], expected)
                self.assertIsNone(report["foreground_iou"])
                self.assertIsNone(report["boundary"]["symmetric"])
                self.assertFiniteReport(report)

    def test_no_valid_reference_pixels_is_unknown_even_if_candidate_is_empty(self):
        for valid, expected in (
            (np.zeros_like(self.reference), "no_valid_pixels"),
            (~self.reference, "no_visible_reference_foreground"),
        ):
            report = score_masks(self.reference, np.zeros_like(self.reference), valid_mask=valid)
            self.assertEqual(report["status"], "unmeasured")
            self.assertEqual(report["reason"], expected)
            self.assertIsNone(report["foreground_iou"])
            self.assertFiniteReport(report)

    def test_missing_temple_is_detected_by_directional_error(self):
        damaged = self.reference & ~self.parts["temples"]
        report = score_masks(self.reference, damaged)
        self.assertLess(report["foreground_iou"], 1)
        self.assertGreater(report["boundary"]["reference_to_candidate"]["max"], 0.1)
        self.assertLess(report["boundary"]["recall"], 1)
        self.assertGreater(report["missing_foreground_fraction"], 0)

    def test_filled_lens_holes_cannot_hide_in_external_silhouette(self):
        damaged = self.reference | self.parts["lens"]
        report = score_masks(self.reference, damaged, boundary_tolerance=0)
        self.assertGreater(report["boundary"]["reference_to_candidate"]["mean"], 0)
        self.assertEqual(report["boundary"]["candidate_to_reference"]["mean"], 0)
        self.assertLess(report["boundary"]["recall"], 1)
        self.assertGreater(report["extra_foreground_fraction"], 0)
        self.assertEqual(report["missing_foreground_fraction"], 0)

    def test_bridge_offset_and_global_scale_remain_errors(self):
        bridge = self.parts["bridge"]
        displaced = ndimage.shift(bridge, (12, 0), order=0, mode="constant", cval=False)
        report = score_masks(bridge, displaced, reference_width_px=170)
        self.assertEqual(report["foreground_iou"], 0)
        self.assertGreater(report["boundary"]["symmetric"]["mean"], 0.04)
        # Scale about the image center, without compensating normalization or
        # registering the damaged shape back to its reference.
        center = (np.asarray(self.reference.shape) - 1) / 2
        scale = 1 / 0.8
        shrunk = ndimage.affine_transform(self.reference, np.eye(2) * scale,
                                         offset=center * (1 - scale), order=0,
                                         mode="constant", cval=False)
        report = score_masks(self.reference, shrunk)
        self.assertLess(report["foreground_iou"], 0.8)
        self.assertGreater(report["boundary"]["symmetric"]["max"], 0.05)

    def test_partial_visibility_reports_coverage_without_fabricating_cut_contour(self):
        reference = np.zeros((60, 80), dtype=bool)
        reference[10:40, 10:60] = True
        valid = np.zeros_like(reference)
        valid[:, :30] = True
        # Candidate errors entirely outside the known image region are unknown.
        candidate = reference.copy()
        candidate[:, 30:] = ~candidate[:, 30:]
        report = score_masks(reference, candidate, valid_mask=valid)
        self.assertEqual(report["foreground_iou"], 1)
        self.assertEqual(report["boundary"]["f1"], 1)
        self.assertEqual(report["boundary"]["symmetric"]["max"], 0)
        self.assertEqual(report["reference_width_px"], 50)
        self.assertAlmostEqual(report["coverage"]["reference_foreground_fraction"], 0.4)
        self.assertGreater(report["coverage"]["reference_boundary_fraction"], 0)
        self.assertLess(report["coverage"]["reference_boundary_fraction"], 1)
        # True observed contour: left edge 30, top/bottom extensions 18 each.
        # No artificial right edge is introduced at x=29 by the valid mask.
        self.assertEqual(report["coverage"]["reference_observable_boundary_pixels"], 66)

    def test_partial_visibility_does_not_claim_unobserved_contours(self):
        valid = np.zeros_like(self.reference)
        valid[31:33, 31:33] = True
        report = score_masks(self.reference, self.reference, valid_mask=valid)
        self.assertEqual(report["status"], "measured")
        self.assertEqual(report["foreground_iou"], 1)
        self.assertEqual(report["boundary"]["status"], "unmeasured")
        self.assertEqual(report["boundary"]["reason"], "no_observable_reference_contour")
        self.assertIsNone(report["boundary"]["f1"])
        self.assertFiniteReport(report)

    def test_crop_boundary_is_not_trusted_and_full_image_candidate_fails(self):
        clipped = self.reference.copy()
        clipped[40:50, :20] = True
        report = score_masks(clipped, clipped)
        self.assertTrue(report["coverage"]["reference_touches_image_border"])
        self.assertLess(report["coverage"]["reference_boundary_fraction"], 1)
        report = score_masks(self.reference, np.ones_like(self.reference))
        self.assertEqual(report["status"], "measured")
        self.assertTrue(report["boundary"]["missing_candidate_contour"])
        self.assertEqual(report["boundary"]["f1"], 0)
        self.assertFiniteReport(report)

    def test_normalization_uses_reference_object_width_and_not_candidate_extent(self):
        reference = self.parts["bridge"]
        candidate = ndimage.shift(reference, (10, 0), order=0, mode="constant", cval=False)
        narrow = score_masks(reference, candidate, reference_width_px=50, boundary_tolerance=0)
        wide = score_masks(reference, candidate, reference_width_px=100, boundary_tolerance=0)
        for direction in ("reference_to_candidate", "candidate_to_reference", "symmetric"):
            for statistic in ("mean", "p95", "max"):
                self.assertAlmostEqual(narrow["boundary"][direction][statistic],
                                       2 * wide["boundary"][direction][statistic])
        self.assertEqual(narrow["foreground_iou"], wide["foreground_iou"])

    def test_tolerance_is_applied_to_both_directions_in_width_units(self):
        reference = np.zeros((40, 40), dtype=bool)
        reference[10:20, 10:20] = True
        candidate = np.roll(reference, 1, axis=1)
        exact = score_masks(reference, candidate, reference_width_px=100, boundary_tolerance=0)
        tolerant = score_masks(reference, candidate, reference_width_px=100, boundary_tolerance=0.01)
        self.assertLess(exact["boundary"]["f1"], 1)
        self.assertEqual(tolerant["boundary"]["f1"], 1)
        self.assertGreater(tolerant["boundary"]["symmetric"]["mean"], 0)

    def test_invalid_inputs_are_not_silently_cast_to_evidence(self):
        bad_masks = [np.zeros((0, 4), dtype=bool), np.zeros((4, 4), dtype=float),
                     np.full((4, 4), np.nan), np.zeros((4, 4, 1), dtype=bool)]
        for bad in bad_masks:
            with self.subTest(shape=bad.shape, dtype=bad.dtype):
                with self.assertRaises(ValueError):
                    score_masks(bad, bad)
        with self.assertRaises(ValueError):
            score_masks(self.reference[:-1], self.reference)
        with self.assertRaises(ValueError):
            score_masks(self.reference, self.reference, valid_mask=np.ones((5, 5), dtype=bool))
        for width in (0, -1, float("nan"), float("inf"), "bad", 1e-320, 10 ** 400):
            with self.assertRaises(ValueError):
                score_masks(self.reference, self.reference, reference_width_px=width)
        for tolerance in (-1, float("nan"), float("inf"), None):
            with self.assertRaises(ValueError):
                score_masks(self.reference, self.reference, boundary_tolerance=tolerance)

    def test_extreme_representable_scale_keeps_all_errors_finite(self):
        for candidate in (np.zeros_like(self.reference), self.reference & ~self.parts["temples"]):
            report = score_masks(self.reference, candidate, reference_width_px=2e-306)
            self.assertFiniteReport(report)
            self.assertGreater(report["boundary"]["symmetric"]["mean"], 0)

    def test_small_images_do_not_create_nan_or_empty_agreement(self):
        for shape in ((1, 1), (1, 7), (2, 2)):
            full = np.ones(shape, dtype=bool)
            report = score_masks(full, full)
            self.assertEqual(report["status"], "measured")
            self.assertEqual(report["boundary"]["status"], "unmeasured")
            self.assertFiniteReport(report)
            empty = np.zeros(shape, dtype=bool)
            self.assertEqual(score_masks(empty, empty)["status"], "unmeasured")

    def test_determinism_with_noncontiguous_arrays(self):
        candidate = self.reference & ~self.parts["bridge"]
        first = score_masks(self.reference[:, ::-1], candidate[:, ::-1])
        for _ in range(3):
            report = score_masks(self.reference[:, ::-1].copy(), candidate[:, ::-1].copy())
            self.assertEqual(first, report)
            self.assertFiniteReport(report)


class ComponentMetricsTests(unittest.TestCase):
    def test_missing_candidate_component_is_failure_not_dropped(self):
        parts = glasses_components()
        candidates = {name: mask for name, mask in parts.items() if name != "temples"}
        reports = score_components(parts, candidates, reference_width_px=170,
                                   required_components=["nose_pads"])
        self.assertEqual(list(reports), sorted(reports))
        self.assertEqual(reports["temples"]["status"], "measured")
        self.assertEqual(reports["temples"]["foreground_iou"], 0)
        self.assertTrue(reports["temples"]["candidate_component_missing"])
        self.assertEqual(reports["rim"]["foreground_iou"], 1)
        self.assertEqual(reports["nose_pads"]["status"], "unmeasured")
        self.assertIsNone(reports["nose_pads"]["coverage"])
        json.dumps(reports, allow_nan=False)

    def test_missing_reference_and_candidate_only_component_are_unknown(self):
        parts = glasses_components()
        reports = score_components({"rim": parts["rim"], "temples": None}, parts)
        self.assertEqual(reports["rim"]["status"], "measured")
        for name in ("temples", "lens", "bridge"):
            self.assertEqual(reports[name]["status"], "unmeasured")
            self.assertEqual(reports[name]["reason"], "reference_missing")
        json.dumps(reports, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
