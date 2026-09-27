"""Intake evidence, provenance and refusal tests; no provider or calibration claims."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
from PIL import Image, ImageCms, PngImagePlugin

from reconstruction.input_bundle import prepare_input_bundle


class InputBundleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.first = self.root / "first.png"
        self.second = self.root / "second.png"
        Image.new("RGB", (9, 7), (30, 60, 90)).save(self.first)
        Image.new("RGB", (8, 6), (20, 40, 80)).save(self.second)
        self.request = {"schema_version": 1, "photos": [{"path": "first.png"}, {"path": "second.png"}]}

    def prepare(self, request=None, output="bundle"):
        return prepare_input_bundle(request or self.request, self.root, self.root / output)

    def test_original_bytes_png_pixels_and_manifest_hash_are_preserved(self):
        original = self.first.read_bytes()
        result = self.prepare()
        self.assertEqual(json.loads((self.root / "bundle/manifest.json").read_text()), result)
        photo = result["photos"][0]
        self.assertEqual((self.root / "bundle" / photo["original"]["path"]).read_bytes(), original)
        self.assertEqual(photo["original"]["sha256"], hashlib.sha256(original).hexdigest())
        png = self.root / "bundle" / photo["normalized"]["path"]
        self.assertEqual(photo["normalized"]["sha256"], hashlib.sha256(png.read_bytes()).hexdigest())
        with Image.open(png) as normalized, Image.open(self.first) as source:
            np.testing.assert_array_equal(np.asarray(normalized), np.asarray(source))
        self.assertFalse(photo["transform"]["resized"])
        self.assertFalse(photo["normalized"]["has_transparency"])
        self.assertEqual(result["calibration"]["status"], "unmeasured")
        self.assertEqual(result["dimensions_mm"], {})
        self.assertEqual(photo["normalized"]["color_space"], "assumed_srgb_uncalibrated")
        self.assertEqual(len(result["input_sha256"]), 64)

    def test_optional_unknown_and_repeated_view_labels_are_not_camera_calibration(self):
        Image.new("RGB", (6, 5), (5, 30, 200)).save(self.root / "third.png")
        request = {"schema_version": 1, "photos": [
            {"path": "first.png", "id": "front-one", "view": "front"},
            {"path": "second.png", "id": "front-two", "view": "front"},
            {"path": "third.png"}], "dimensions_mm": {"frame_width": 140, "lens_height": 42.5},
            "initializer": {"future_provider_field": {"any_policy": ["preserve", 1, None, True]}}}
        result = self.prepare(request)
        self.assertEqual([p["view"] for p in result["photos"]], ["front", "front", "unknown"])
        self.assertEqual(result["initializer"], request["initializer"])
        self.assertEqual(result["dimensions_mm"], {"frame_width": 140., "lens_height": 42.5})
        self.assertEqual(result["lens_facts"], {})
        declared = self.prepare({**request, "lens_facts": {"mirror_coating": False}}, "declared")
        self.assertEqual(declared["lens_facts"], {"mirror_coating": False})
        self.assertNotEqual(declared["input_sha256"], result["input_sha256"], "a declared lens fact is part of the input")
        for bad in ({"mirror_coating": "no"}, {"mirror_coating": 0}, {"tint": True}, ["mirror_coating"]):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.prepare({**request, "lens_facts": bad}, "bad")
        self.assertEqual(result["calibration"]["physical_scale"], "stated_dimensions_only")
        self.assertEqual(result["calibration"]["camera"], "unmeasured")

    def test_every_exif_orientation_has_an_exact_recorded_pixel_center_transform(self):
        pixels = np.arange(18, dtype=np.uint8).reshape((2, 3, 3)) * 10
        for orientation in range(1, 9):
            with self.subTest(orientation=orientation):
                exif = Image.Exif(); exif[274] = orientation
                source = self.root / f"oriented-{orientation}.png"
                Image.fromarray(pixels).save(source, exif=exif)
                request = {"schema_version": 1, "photos": [{"path": str(source)}, {"path": "second.png"}]}
                result = self.prepare(request, f"oriented-bundle-{orientation}")
                photo = result["photos"][0]
                matrix = np.asarray(photo["transform"]["original_to_normalized_xy"])
                with Image.open(self.root / f"oriented-bundle-{orientation}" / photo["normalized"]["path"]) as image:
                    actual = np.asarray(image)
                    self.assertNotIn(274, image.getexif())
                for y in range(2):
                    for x in range(3):
                        target_x, target_y, _ = matrix @ [x, y, 1]
                        np.testing.assert_array_equal(actual[target_y, target_x], pixels[y, x])
                self.assertEqual((photo["normalized"]["width"], photo["normalized"]["height"]),
                                 (2, 3) if orientation >= 5 else (3, 2))

    def test_alpha_and_hidden_rgb_are_preserved_without_white_compositing(self):
        pixels = np.array([[[200, 10, 30, 0], [40, 50, 60, 120]], [[2, 3, 4, 255], [9, 8, 7, 255]]], np.uint8)
        Image.fromarray(pixels).save(self.first)
        result = self.prepare()
        photo = result["photos"][0]
        self.assertEqual(photo["normalized"]["mode"], "RGBA")
        self.assertTrue(photo["normalized"]["has_transparency"])
        with Image.open(self.root / "bundle" / photo["normalized"]["path"]) as normalized:
            np.testing.assert_array_equal(np.asarray(normalized), pixels)
        self.assertTrue(any("alpha-aware" in reason for reason in photo["limitations"]))

    def test_palette_transparency_is_expanded_and_opaque_rgba_is_retained(self):
        palette = Image.new("P", (2, 1)); palette.putpalette([200, 0, 0, 0, 100, 0] + [0] * 762)
        palette.putdata([0, 1]); palette.save(self.first, transparency=bytes([0, 128]))
        Image.new("RGBA", (3, 1), (3, 4, 5, 255)).save(self.second)
        result = self.prepare()
        a, b = [p["normalized"] for p in result["photos"]]
        self.assertEqual(a["mode"], "RGBA"); self.assertTrue(a["has_transparency"])
        self.assertEqual(b["mode"], "RGBA"); self.assertFalse(b["has_transparency"])
        with Image.open(self.root / "bundle" / a["path"]) as normalized:
            self.assertEqual(list(normalized.getdata()), [(200, 0, 0, 0), (0, 100, 0, 128)])

    def test_valid_embedded_icc_is_converted_recorded_and_keeps_alpha(self):
        profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        Image.new("RGBA", (3, 2), (60, 90, 120, 70)).save(self.first, icc_profile=profile)
        result = self.prepare()
        photo = result["photos"][0]
        self.assertEqual(photo["original"]["icc_sha256"], hashlib.sha256(profile).hexdigest())
        self.assertEqual(photo["normalized"]["color_space"], "srgb_from_embedded_icc_uncalibrated")
        self.assertTrue(photo["normalized"]["srgb_chunk"])
        self.assertEqual(result["calibration"]["color"], "unmeasured")
        with Image.open(self.root / "bundle" / photo["normalized"]["path"]) as normalized:
            self.assertEqual(normalized.getpixel((0, 0)), (60, 90, 120, 70))
            self.assertEqual(normalized.info["srgb"], 0)
        repeated = self.prepare(output="second-bundle")
        self.assertEqual(repeated["input_sha256"], result["input_sha256"])

    def test_unprofiled_cmyk_conversion_is_explicitly_uncalibrated(self):
        source = self.root / "cmyk.jpg"
        Image.new("CMYK", (3, 2), (20, 40, 70, 90)).save(source)
        result = self.prepare({"schema_version": 1, "photos": [{"path": str(source)}, {"path": "second.png"}]})
        photo = result["photos"][0]
        self.assertEqual(photo["normalized"]["color_space"], "unprofiled_cmyk_rgb_conversion_uncalibrated")
        self.assertTrue(any("unverified color approximation" in text for text in photo["assumptions"]))

    def test_no_minimum_resolution_gate_or_upsampling(self):
        Image.new("RGB", (1, 1), (1, 2, 3)).save(self.first)
        result = self.prepare()
        photo = result["photos"][0]
        self.assertEqual((photo["normalized"]["width"], photo["normalized"]["height"]), (1, 1))
        self.assertTrue(photo["normalized"]["low_resolution"])
        self.assertTrue(any("cannot restore" in text for text in photo["limitations"]))

    def test_byte_and_pixel_duplicates_are_rejected_before_any_output_mutation(self):
        for kind in ("bytes", "metadata", "opaque_alpha"):
            with self.subTest(kind=kind):
                with Image.open(self.first) as source:
                    if kind == "bytes":
                        self.second.write_bytes(self.first.read_bytes())
                    elif kind == "metadata":
                        metadata = PngImagePlugin.PngInfo(); metadata.add_text("irrelevant", "different encoded file")
                        source.save(self.second, pnginfo=metadata)
                    else:
                        source.convert("RGBA").save(self.second)
                with self.assertRaisesRegex(ValueError, "Duplicate"):
                    self.prepare(output=f"duplicate-{kind}")
                self.assertFalse((self.root / f"duplicate-{kind}").exists())

    def test_corrupted_multiframe_bad_icc_and_bad_orientation_never_create_output(self):
        invalid = self.root / "invalid.png"
        for kind in ("corrupt", "animated", "profile", "orientation", "high-depth"):
            with self.subTest(kind=kind):
                if kind == "corrupt":
                    invalid.write_bytes(b"not an image")
                elif kind == "animated":
                    Image.new("RGB", (4, 4), "red").save(invalid, format="GIF", save_all=True,
                                                        append_images=[Image.new("RGB", (4, 4), "blue")])
                elif kind == "profile":
                    Image.new("RGB", (4, 4)).save(invalid, icc_profile=b"not a valid profile")
                elif kind == "orientation":
                    exif = Image.Exif(); exif[274] = 9
                    Image.new("RGB", (4, 4)).save(invalid, exif=exif)
                else:
                    Image.new("I;16", (4, 4), 20000).save(invalid)
                request = {"schema_version": 1, "photos": [{"path": "first.png"}, {"path": str(invalid)}]}
                output = self.root / f"rejected-{kind}"
                output.mkdir()  # Existing empty outputs must also remain untouched.
                with self.assertRaises(ValueError):
                    self.prepare(request, output.name)
                self.assertEqual(list(output.iterdir()), [])

    def test_unsafe_duplicate_ids_bad_paths_and_unknown_fields_are_rejected(self):
        invalid_photos = [
            [{"path": "first.png", "id": bad}, {"path": "second.png"}]
            for bad in ("../escape", "nested/path", ".", "CON", "lPt1", "a b", "", "a" * 65)
        ] + [
            [{"path": "first.png", "id": "Same"}, {"path": "second.png", "id": "same"}],
            [{"path": "first.png"}, {"path": "missing.jpg"}],
            [{"path": "first.png"}, {"path": "."}],
            [{"path": "first.png"}, {"path": "\0bad"}],
            [{"path": "first.png"}, {"path": "second.png", "view": "guess"}],
            [{"path": "first.png"}, {"path": "second.png", "calibrated": True}],
        ]
        for photos in invalid_photos:
            with self.subTest(photos=photos), self.assertRaises(ValueError):
                self.prepare({"schema_version": 1, "photos": photos})
            self.assertFalse((self.root / "bundle").exists())
        for patch in ({"schema_version": True}, {"schema_version": 2}, {"other": 1}, {"photos": [{}]}, {"initializer": []}):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                self.prepare({**self.request, **patch})

    def test_invalid_dimensions_never_get_clamped_or_assumed(self):
        for value in (0, -1, float("nan"), float("inf"), True, "140", None, 10 ** 1000):
            with self.subTest(value=str(value)[:30]), self.assertRaises(ValueError):
                self.prepare({**self.request, "dimensions_mm": {"frame_width": value}})
            self.assertFalse((self.root / "bundle").exists())
        with self.assertRaises(ValueError):
            self.prepare({**self.request, "dimensions_mm": {"height": 50}})

    def test_input_key_is_location_independent_but_binds_evidence_and_settings(self):
        first = self.prepare()
        moved = self.root / "relocated"; moved.mkdir()
        for source in (self.first, self.second):
            (moved / source.name).write_bytes(source.read_bytes())
        same = prepare_input_bundle(self.request, moved, self.root / "relocated-bundle")
        self.assertEqual(first["input_sha256"], same["input_sha256"])
        changed_dimension = self.prepare({**self.request, "dimensions_mm": {"frame_width": 140}}, "changed-dimension")
        changed_initializer = self.prepare({**self.request, "initializer": {"provider": "future"}}, "changed-initializer")
        changed_view = self.prepare({**self.request, "photos": [{"path": "first.png", "view": "front"}, {"path": "second.png"}]}, "changed-view")
        changed_order = self.prepare({**self.request, "photos": list(reversed(self.request["photos"]))}, "changed-order")
        self.assertEqual(len({x["input_sha256"] for x in (first, changed_dimension, changed_initializer, changed_view, changed_order)}), 5)

    def test_nonempty_output_is_never_reused_or_overwritten(self):
        output = self.root / "bundle"; output.mkdir()
        marker = output / "prior.txt"; marker.write_bytes(b"keep exactly")
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertEqual(marker.read_bytes(), b"keep exactly")
        self.assertEqual(list(output.iterdir()), [marker])


if __name__ == "__main__":
    unittest.main()
