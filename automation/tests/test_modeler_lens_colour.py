import unittest

import numpy as np
from PIL import Image, ImageDraw

from modeler import export as mexport
from modeler import lens_colour as lc


class Projection(unittest.TestCase):
    def test_column_major_matrix_and_pixel_mapping(self):
        # identity projection: ndc (0, 0) -> image centre; ndc (1, 1) -> top-right corner
        M = lc._mat4([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1])
        px, ok = lc.project_pixels(np.array([[0.0, 0.0, 0.0], [1.0, 1.0, 0.0]]), M, 720, 480)
        self.assertTrue(ok.all())
        np.testing.assert_allclose(px[0], [360, 240])
        np.testing.assert_allclose(px[1], [720, 0])
        # a translation stored column-major lands in the last column
        T = lc._mat4([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 5, 6, 7, 1])
        np.testing.assert_allclose(T[:3, 3], [5, 6, 7])

    def test_core_is_inside_the_mask(self):
        mask = np.zeros((100, 100), bool)
        mask[20:80, 10:90] = True
        core = lc.core_of(mask)
        self.assertTrue(core.sum() > 0)
        self.assertTrue((mask | ~core).all())
        self.assertLess(core.sum(), mask.sum() * 0.8)
        self.assertGreater(core.sum(), mask.sum() * 0.2)
        # the rim of the mask is excluded
        self.assertFalse(core[20, 10:90].any())
        self.assertFalse(core[20:80, 10].any())


class PhotoCore(unittest.TestCase):
    def test_photo_lens_core_reads_the_lens_colour(self):
        W, H = 200, 100
        img = Image.new("RGB", (W, H), (255, 255, 255))
        ImageDraw.Draw(img).ellipse([40, 20, 160, 80], fill=(30, 90, 200))   # a blue lens
        arr = np.asarray(img).astype(float)
        # evidence: mm_per_px 0.5, axis at x = 100 px, y_mid at 50 px; outline = the ellipse in mm
        t = np.linspace(0, 2 * np.pi, 64, endpoint=False)
        outline = np.c_[60 * np.cos(t) * 0.5, 30 * np.sin(t) * 0.5]
        evidence = {"front": {"mm_per_px": 0.5, "axis_x_px": 100, "y_mid_px": 50, "lenses": [{"side": "C", "outline_mm": outline.tolist()}]},
                    "inputs": []}
        rgb, n = lc.photo_lens_core(evidence, image=arr)
        np.testing.assert_allclose(rgb, [30, 90, 200], atol=1)
        self.assertGreater(n, 500)

    def test_compare_reports_hue_and_ratios(self):
        d = lc.compare(np.array([143.0, 207.0, 232.0]), np.array([56.0, 110.0, 141.0]), mirrored=True)
        self.assertLess(d["hue_error"], 0.03)
        self.assertLess(d["value_ratio"], 0.7)
        self.assertGreater(d["lens_env_intensity_recommended"], 1.4)
        d2 = lc.compare(np.array([100.0, 100.0, 100.0]), np.array([100.0, 100.0, 100.0]), mirrored=False)
        self.assertIsNone(d2["lens_env_intensity_recommended"])


class AngularExport(unittest.TestCase):
    def test_angular_reflectance_reaches_the_descriptor(self):
        optics = {"transmission_top_rgb": [0.1, 0.1, 0.1], "transmission_bottom_rgb": [0.1, 0.1, 0.1], "profile": "flat",
                  "reflectance_rgb": [0.6, 0.2, 0.5], "mirror": True, "roughness": 0.06,
                  "angular": [[0.0, [0.6, 0.2, 0.5]], [45.0, [0.2, 0.6, 0.3]], [90.0, [0.95, 0.95, 0.95]]]}
        spec = mexport.material_spec("m", {"kind": "lens", "base_color_linear": [0.1, 0.1, 0.1], "roughness": 0.06, "lens": optics}, lens=True)
        d = spec["lens_appearance"]
        self.assertEqual(len(d["angular_reflectance_keyframes"]), 3)
        self.assertEqual(d["angular_reflectance_keyframes"][0]["reflectance_rgb"], d["normal_reflectance_rgb"])
        self.assertAlmostEqual(d["angular_reflectance_keyframes"][1]["reflectance_rgb"][1], 0.6, places=5)
        plain = mexport.material_spec("p", {"kind": "lens", "base_color_linear": [0.1, 0.1, 0.1], "roughness": 0.06,
                                            "lens": dict(optics, angular=None)}, lens=True)
        self.assertIsNone(plain["lens_appearance"]["angular_reflectance_keyframes"])


if __name__ == "__main__":
    unittest.main()
