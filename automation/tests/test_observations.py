"""Falsification tests for a contrast instrument, not semantic segmentation."""

import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
from PIL import Image, ImageDraw

from reconstruction.observations import ObservationPolicy, observe_image, observe_image_path


def synthetic_rims(background=(255, 255, 255), foreground=(30, 30, 30)):
    image = Image.new("RGB", (320, 180), background)
    draw = ImageDraw.Draw(image)
    draw.ellipse((35, 55, 145, 125), outline=foreground, width=7)
    draw.ellipse((175, 55, 285, 125), outline=foreground, width=7)
    draw.line((143, 75, 160, 70, 177, 75), fill=foreground, width=6)
    return image


class ObservationTests(unittest.TestCase):
    def test_high_contrast_rims_measured_without_filling_holes(self):
        result = observe_image(synthetic_rims())
        self.assertEqual(result.status, "measured", result.reasons)
        self.assertEqual(result.mask.dtype, np.bool_)
        self.assertEqual(result.mask.shape, (180, 320))
        self.assertEqual(result.bbox_xyxy, (35, 55, 286, 126))
        self.assertFalse(result.mask[90, 90])
        self.assertFalse(result.mask[90, 230])
        self.assertTrue(result.mask[55, 90])
        self.assertIs(result.usable_mask, result.mask)

    def test_dark_background_with_bright_frame_is_supported(self):
        result = observe_image(synthetic_rims((8, 8, 8), (210, 210, 210)))
        self.assertEqual(result.status, "measured", result.reasons)
        self.assertEqual(result.metrics["background_rgb"], [8.0, 8.0, 8.0])
        self.assertFalse(result.mask[90, 90])

    def test_dark_on_dark_is_unmeasured_not_a_successful_empty_mask(self):
        result = observe_image(synthetic_rims((8, 8, 8), (16, 16, 16)))
        self.assertEqual(result.status, "unmeasured")
        self.assertIn("no_usable_foreground", result.reasons)
        self.assertIsNone(result.usable_mask)
        self.assertEqual(result.confidence, 0)

    def test_faint_rimless_outlines_are_unmeasured(self):
        result = observe_image(synthetic_rims((255, 255, 255), (249, 249, 249)))
        self.assertEqual(result.status, "unmeasured")
        self.assertIn("no_usable_foreground", result.reasons)

    def test_soft_shadow_exposes_threshold_sensitivity(self):
        image = synthetic_rims()
        draw = ImageDraw.Draw(image)
        draw.ellipse((35, 132, 285, 157), fill=(237, 237, 237))
        result = observe_image(image)
        self.assertEqual(result.status, "unmeasured")
        self.assertIn("threshold_sensitive_foreground_possible_shadow_or_faint_material", result.reasons)

    def test_hard_shadow_remains_explicitly_unidentifiable(self):
        image = synthetic_rims()
        ImageDraw.Draw(image).ellipse((35, 132, 285, 157), fill=(150, 150, 150))
        result = observe_image(image)
        # A color threshold cannot infer the physical cause of these pixels.
        # Keep this counterexample: measured must not mean a correct glasses mask.
        self.assertEqual(result.status, "measured")
        self.assertTrue(result.mask[145, 160])
        self.assertTrue(any("hard high-contrast shadow" in text for text in result.limitations))

    def test_transparent_lens_is_not_misrepresented_as_semantic_component(self):
        result = observe_image(synthetic_rims())
        report = result.to_report()
        self.assertNotIn("lens_mask", report)
        self.assertEqual(report["semantic_component_coverage"], "unmeasured")
        self.assertEqual(report["complete_glasses_silhouette_coverage"], "unmeasured")
        self.assertFalse(result.mask[90, 90])
        self.assertTrue(any("Transparent lenses" in text for text in report["limitations"]))

    def test_disconnected_components_are_kept(self):
        image = synthetic_rims()
        ImageDraw.Draw(image).rectangle((40, 140, 130, 146), fill=(30, 30, 30))
        result = observe_image(image)
        self.assertEqual(result.status, "measured")
        self.assertTrue(result.mask[143, 80])
        self.assertGreater(result.metrics["connected_components"], 1)

    def test_cropped_frame_is_unmeasured(self):
        image = synthetic_rims()
        ImageDraw.Draw(image).line((0, 70, 40, 70), fill=(0, 0, 0), width=7)
        result = observe_image(image)
        self.assertEqual(result.status, "unmeasured")
        self.assertIn("foreground_touches_image_border_or_background_is_contaminated", result.reasons)

    def test_foreground_too_small_uses_native_object_extent(self):
        image = Image.new("RGB", (1600, 1000), "white")
        ImageDraw.Draw(image).rectangle((750, 480, 790, 500), fill="black")
        result = observe_image(image)
        self.assertEqual(result.status, "unmeasured")
        self.assertIn("foreground_too_small_for_shape_measurement", result.reasons)
        self.assertEqual(result.metrics["foreground_span_px"], [41, 21])
        self.assertEqual(result.image_size, (1600, 1000))

    def test_nonuniform_background_is_unmeasured(self):
        pixels = np.asarray(synthetic_rims()).copy()
        pixels[:, :20] = (175, 175, 175)
        result = observe_image(pixels)
        self.assertEqual(result.status, "unmeasured")
        self.assertIn("background_not_uniform", result.reasons)

    def test_nonopaque_input_is_unmeasured_without_assumed_compositing(self):
        image = synthetic_rims().convert("RGBA")
        image.putalpha(128)
        result = observe_image(image)
        self.assertEqual(result.status, "unmeasured")
        self.assertIn("alpha_support_is_mostly_translucent_or_threshold_sensitive", result.reasons)
        self.assertFalse(result.metrics['photometric_background_known'])

    def test_noisy_uniform_background_measures_large_contrast(self):
        rng = np.random.default_rng(12)
        clean = np.asarray(synthetic_rims())
        noisy = np.clip(clean.astype(np.int16) + rng.integers(-2, 3, clean.shape), 0, 255).astype(np.uint8)
        result = observe_image(noisy)
        self.assertEqual(result.status, "measured", result.reasons)

    def test_report_is_json_serializable_and_masks_are_opt_in(self):
        result = observe_image(synthetic_rims())
        self.assertNotIn("mask", result.to_report())
        report = json.loads(json.dumps(result.to_report(include_mask=True), allow_nan=False))
        self.assertEqual(report["bbox_xyxy"], [35, 55, 286, 126])
        self.assertIs(report["mask"][90][90], False)

    def test_file_loading_does_not_resize_pixels(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rims.png"
            synthetic_rims().save(path)
            result = observe_image_path(path)
            self.assertEqual(result.image_size, (320, 180))
            self.assertEqual(result.source, str(path.resolve()))
            self.assertEqual(result.status, "measured")

    def test_invalid_arrays_and_policy_do_not_silently_normalize(self):
        for invalid in (np.ones((180, 320, 3)), np.ones((180, 320), dtype=np.uint8)):
            with self.assertRaises(ValueError):
                observe_image(invalid)
        with self.assertRaises(ValueError):
            ObservationPolicy(minimum_confidence=0)
        with self.assertRaises(ValueError):
            ObservationPolicy(strong_contrast=8)

    def test_exif_rotation_is_recorded_without_changing_resolution(self):
        image = synthetic_rims()
        exif = image.getexif()
        exif[274] = 6
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "oriented.png"
            image.save(path, exif=exif)
            result = observe_image_path(path)
            self.assertEqual(result.image_size, (180, 320))
            self.assertEqual(result.mask.shape, (320, 180))
            self.assertEqual(result.metrics["source_image_size"], [320, 180])
            self.assertEqual(result.metrics["source_exif_orientation"], 6)
            self.assertEqual(result.status, "measured")


if __name__ == "__main__":
    unittest.main()
