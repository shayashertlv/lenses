import json
from pathlib import Path
import unittest

import numpy as np
from PIL import Image, ImageDraw

from modeler import export as mexport
from modeler import lens_colour as lc
from modeler.paths import AUTOMATION

SKIN, BLUE = np.array([203.0, 166.0, 141.0]), np.array([58.0, 79.0, 110.0])     # the see-through fixtures #cba68d, #3a4f6e
TOMFORD_PHOTO_CORE = np.array([213.2, 196.1, 175.2])    # test-pilot-002 front photo's lens core over its white backdrop
WHITE = np.array([255.0, 255.0, 255.0])
R0006 = AUTOMATION / "data" / "modeler" / "agentic" / "test-pilot-002"


def lin(srgb):
    return lc.srgb_to_linear(np.asarray(srgb, float))


def fixture_pair(T, A, shape=(60, 80), box=(15, 45, 20, 60)):
    """Two 8-bit fixture renders (skin, blue) of one lens drawn as the runtime draws it (out = T bg + A per channel, linear
    light) inside ``box``, the bare fixture elsewhere; and the box as the lens core."""
    core = np.zeros(shape, bool)
    y0, y1, x0, x1 = box
    core[y0:y1, x0:x1] = True
    out = []
    for fx in (SKIN, BLUE):
        img = np.broadcast_to(fx, shape + (3,)).copy()
        img[core] = np.round(lc.linear_to_srgb(np.asarray(T) * lin(fx) + np.asarray(A)))
        out.append(img)
    return out[0], out[1], core


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
        d2 = lc.compare(np.array([100.0, 100.0, 100.0]), np.array([100.0, 100.0, 100.0]), mirrored=False)
        self.assertTrue(d2["neutral"])
        self.assertIsNone(d2["saturation_ratio"])

    def test_neutrality_is_the_photos_alone(self):
        # a champagne photo lens against a near-grey prediction: the photo has a hue, so the ratio is reported (it was
        # None when EITHER side was below the neutral saturation, which hid r0006's pale lens)
        d = lc.compare(TOMFORD_PHOTO_CORE, np.array([233.7, 229.0, 219.2]), mirrored=True)
        self.assertFalse(d["neutral"])
        self.assertIsNotNone(d["saturation_ratio"])
        self.assertLess(d["saturation_ratio"], 0.5)


class FixtureFit(unittest.TestCase):
    """The lens seen over the photo's backdrop, predicted from the two solid-fixture renders of the front view."""

    def test_fit_recovers_transmission_and_reflection(self):
        T, A = np.array([0.745, 0.718, 0.664]), np.array([0.077, 0.067, 0.049])
        a, b, core = fixture_pair(T, A)
        t_eff, refl = lc.fit_fixtures(lin(a[core]).mean(0), lin(b[core]).mean(0), lin(SKIN), lin(BLUE))
        np.testing.assert_allclose(t_eff, T, atol=0.02)          # 8-bit quantisation over the narrow blue-channel lever
        np.testing.assert_allclose(refl, A, atol=0.006)
        d = lc.lens_colour_from_fixtures(TOMFORD_PHOTO_CORE, WHITE, a, b, core, SKIN, BLUE, mirrored=True)
        np.testing.assert_allclose(d["predicted_rgb"], lc.linear_to_srgb(T + A), atol=1.0)

    def test_a_bare_fixture_is_not_a_match(self):
        # no lens at all (T = 1, A = 0): over the checker the old metric read hue 0.007, saturation 0.82, value 0.97
        a, b, core = fixture_pair(np.ones(3), np.zeros(3))
        d = lc.lens_colour_from_fixtures(TOMFORD_PHOTO_CORE, WHITE, a, b, core, SKIN, BLUE, mirrored=False)
        self.assertGreater(d["value_ratio"], 1.15)
        self.assertLess(d["saturation_ratio"], 0.2)
        self.assertFalse(d["match"])
        self.assertIn("too_light", d["flags"])
        self.assertIn("too_pale", d["flags"])

    def test_a_photo_matched_lens_is_a_match(self):
        A = np.array([0.077, 0.067, 0.049])
        T = lin(TOMFORD_PHOTO_CORE) - A                     # the lens the photo shows over white, same reflection
        a, b, core = fixture_pair(T, A)
        d = lc.lens_colour_from_fixtures(TOMFORD_PHOTO_CORE, WHITE, a, b, core, SKIN, BLUE, mirrored=True,
                                         reflectance=[0.23, 0.20, 0.15])
        self.assertTrue(d["match"], d)
        self.assertEqual(d["flags"], [])
        self.assertAlmostEqual(d["value_ratio"], 1.0, delta=0.02)
        self.assertAlmostEqual(d["saturation_ratio"], 1.0, delta=0.06)
        self.assertLessEqual(d["hue_error"], 0.01)
        np.testing.assert_allclose(d["lens_transmission_recommended"], T, atol=0.02)

    def test_recommendation_uses_the_photo_backdrop_and_is_bounded(self):
        A = np.array([0.05, 0.05, 0.05])
        T = np.array([0.5, 0.5, 0.5])
        grey = np.array([200.0, 200.0, 200.0])
        photo = lc.linear_to_srgb(0.4 * lin(grey) + A)       # the photo's lens: T 0.4 over a grey backdrop, same reflection
        a, b, core = fixture_pair(T, A)
        d = lc.lens_colour_from_fixtures(photo, grey, a, b, core, SKIN, BLUE, mirrored=False, reflectance=[0.04, 0.04, 0.04])
        np.testing.assert_allclose(d["lens_transmission_recommended"], [0.4, 0.4, 0.4], atol=0.02)
        # the photo's reflection unknown: with none at all the photo's lens passes photo / backdrop, the upper bound
        np.testing.assert_allclose(d["lens_transmission_upper_bound"], lin(photo) / lin(grey), atol=0.005)
        self.assertTrue(all(r <= u + 1e-9 for r, u in zip(d["lens_transmission_recommended"], d["lens_transmission_upper_bound"])))
        self.assertIn("too_light", d["flags"])
        # never above what the runtime can pass: T <= 1 - reflectance
        d2 = lc.lens_colour_from_fixtures(np.array([254.0, 254.0, 254.0]), WHITE, a, b, core, SKIN, BLUE, mirrored=True,
                                          reflectance=[0.3, 0.3, 0.3])
        self.assertTrue(all(x <= 0.7 + 1e-9 for x in d2["lens_transmission_recommended"]))

    def test_lens_env_is_not_recommended_for_a_transmissive_lens(self):
        # one photo over one backdrop gives T bg + A_photo per channel: T and the photo's reflection trade one-for-one
        a, b, core = fixture_pair(np.array([0.75, 0.725, 0.67]), np.array([0.077, 0.067, 0.049]))
        d = lc.lens_colour_from_fixtures(TOMFORD_PHOTO_CORE, WHITE, a, b, core, SKIN, BLUE, mirrored=True)
        self.assertIsNone(d["lens_env_intensity_recommended"])
        self.assertIn("transmissive", d["lens_env_intensity_note"])
        # a near-opaque mirror: the photo is its reflection, so the reflection's scale is determined
        A = np.array([0.10, 0.12, 0.15])
        a, b, core = fixture_pair(np.array([0.02, 0.02, 0.02]), A)
        photo = lc.linear_to_srgb(2.0 * A + 0.02)
        d = lc.lens_colour_from_fixtures(photo, WHITE, a, b, core, SKIN, BLUE, mirrored=True)
        self.assertAlmostEqual(d["lens_env_intensity_recommended"], 2.0, delta=0.1)

    def test_photo_backdrop_is_sampled_from_the_border(self):
        img = np.full((100, 160, 3), 242.0)
        img[30:70, 40:120] = (90, 60, 40)                     # the glasses, away from the border
        rgb, spread = lc.photo_backdrop(img)
        np.testing.assert_allclose(rgb, [242, 242, 242])
        self.assertLess(spread, 1.0)

    def test_the_manifest_passes_on_only_a_determined_lens_env(self):
        from modeler.job import lens_env_recommendation
        self.assertIsNone(lens_env_recommendation({"lens_env_intensity_recommended": 1.09, "lens_colour": {"value_ratio": 0.92}}),
                          "a recommendation from the checker-fixture metric (no basis) is dropped")
        s = {"lens_env_intensity_recommended": 1.6, "lens_colour": {"basis": lc.BASIS}}
        self.assertEqual(lens_env_recommendation(s), 1.6)
        self.assertIsNone(lens_env_recommendation({}))


class RecommendationGuards(unittest.TestCase):
    """lens_transmission_recommended is only given where the photo can determine it: not when the runtime's reflection
    alone is brighter than the photo's lens (it clipped to 0.005 and asked for a black lens), and not over a dark or
    uneven photo backdrop (backdrop_spread was reported and never used)."""

    def test_a_too_bright_reflection_is_flagged_not_answered_with_a_black_lens(self):
        # the verifier's probe: dark mirrored sunglasses whose runtime reflection outshines the photo's whole lens
        a, b, core = fixture_pair(np.array([0.12, 0.12, 0.12]), np.array([0.06, 0.07, 0.09]))
        photo = np.array([45.0, 50.0, 60.0])
        d = lc.lens_colour_from_fixtures(photo, WHITE, a, b, core, SKIN, BLUE, mirrored=True, reflectance=[0.3, 0.3, 0.3])
        self.assertIn("reflection_too_bright", d["flags"])
        self.assertIn("too_light", d["flags"])
        self.assertFalse(d["match"])
        self.assertIsNone(d["lens_transmission_recommended"], "no transmission reproduces the photo: the reflection is the mismatch")
        self.assertIsNone(d["lens_transmission_scale"])
        np.testing.assert_allclose(d["lens_transmission_upper_bound"], lin(photo), atol=0.002)     # still true over white
        note = next(n for n in d["notes"] if "reflection_too_bright" in n)
        self.assertIn("mirror", note)
        self.assertIn("not the transmission", note)
        # one channel over is enough: the photo's reflection is not the runtime's there
        a, b, core = fixture_pair(np.array([0.2, 0.2, 0.2]), np.array([0.01, 0.01, 0.2]))
        d = lc.lens_colour_from_fixtures(lc.linear_to_srgb([0.15, 0.15, 0.1]), WHITE, a, b, core, SKIN, BLUE, mirrored=True)
        self.assertIn("reflection_too_bright", d["flags"])
        self.assertIsNone(d["lens_transmission_recommended"])

    def test_a_reflection_within_the_photo_still_gets_its_recommendation(self):
        A = np.array([0.077, 0.067, 0.049])
        a, b, core = fixture_pair(np.array([0.75, 0.725, 0.67]), A)
        d = lc.lens_colour_from_fixtures(TOMFORD_PHOTO_CORE, WHITE, a, b, core, SKIN, BLUE, mirrored=True,
                                         reflectance=[0.23, 0.20, 0.15])
        self.assertNotIn("reflection_too_bright", d["flags"])
        self.assertFalse([f for f in d["flags"] if f.startswith("backdrop")])
        np.testing.assert_allclose(d["lens_transmission_recommended"], [0.59, 0.485, 0.38], atol=0.02)
        self.assertEqual(d["notes"], [])

    def test_a_dark_backdrop_gives_no_recommendation(self):
        a, b, core = fixture_pair(np.array([0.5, 0.5, 0.5]), np.array([0.05, 0.05, 0.05]))
        dark = np.array([15.0, 15.0, 15.0])
        d = lc.lens_colour_from_fixtures(np.array([70.0, 70.0, 70.0]), dark, a, b, core, SKIN, BLUE, mirrored=False)
        self.assertIn("backdrop_dark", d["flags"])
        self.assertIsNone(d["lens_transmission_recommended"])      # it read 0.005 (black) with an upper bound of 1.0
        self.assertIsNone(d["lens_transmission_upper_bound"])
        self.assertIsNone(d["lens_transmission_scale"])
        self.assertTrue(any("backdrop_dark" in n for n in d["notes"]), d["notes"])
        # a light grey studio backdrop is fine (the recommendation test's 200 grey)
        g = lc.lens_colour_from_fixtures(np.array([150.0, 150.0, 150.0]), np.array([200.0, 200.0, 200.0]), a, b, core, SKIN, BLUE,
                                         mirrored=False)
        self.assertFalse([f for f in g["flags"] if f.startswith("backdrop")])
        self.assertIsNotNone(g["lens_transmission_recommended"])

    def test_an_uneven_backdrop_gives_no_recommendation(self):
        a, b, core = fixture_pair(np.array([0.5, 0.5, 0.5]), np.array([0.05, 0.05, 0.05]))
        d = lc.lens_colour_from_fixtures(np.array([180.0, 180.0, 180.0]), WHITE, a, b, core, SKIN, BLUE, mirrored=False,
                                         backdrop_spread=60.0)
        self.assertIn("backdrop_uneven", d["flags"])
        self.assertIsNone(d["lens_transmission_recommended"])
        self.assertIsNone(d["lens_transmission_upper_bound"])
        self.assertTrue(any("backdrop_uneven" in n for n in d["notes"]), d["notes"])
        flat = lc.lens_colour_from_fixtures(np.array([180.0, 180.0, 180.0]), WHITE, a, b, core, SKIN, BLUE, mirrored=False,
                                            backdrop_spread=1.7)                 # the tomford photos' border reads 0-1.7
        self.assertNotIn("backdrop_uneven", flat["flags"])
        self.assertIsNotNone(flat["lens_transmission_recommended"])
        # the spread photo_backdrop reports: a vignetted or gradient border is uneven, a studio white one is not
        grad = np.repeat(np.linspace(120.0, 250.0, 160)[None, :, None], 100, 0).repeat(3, 2)
        self.assertGreater(lc.photo_backdrop(grad)[1], lc.BACKDROP_MAX_SPREAD)
        self.assertEqual(lc.photo_backdrop(np.full((100, 160, 3), 255.0))[1], 0.0)


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


class AuthorRules(unittest.TestCase):
    """The author's RULES describe the measurement it steers by, and the geometry rules the r0006 review found missing."""

    def rule(self, needle: str) -> str:
        from modeler import author
        hits = [r for r in author.RULES if needle in r]
        self.assertEqual(len(hits), 1, f"exactly one rule mentions {needle!r}")
        return hits[0]

    def test_the_lens_colour_rule_describes_the_backdrop_prediction(self):
        from modeler import author
        r = self.rule("summary.lens_colour")
        self.assertNotIn("checker background", r)
        for word in ("backdrop", "skin", "blue", "lens_transmission_recommended", "lens_transmission_upper_bound", "transmission_top_rgb",
                     "mirror_rgb", "flags"):
            self.assertIn(word, r)
        self.assertTrue(any("checker" in x and "lens colour" in x.lower() for x in author.RULES if "AR sheet" in x),
                        "the sheet rule says the checker row's lens colour is the checker's")

    def test_the_geometry_rules_of_the_r0006_review(self):
        base = self.rule("base curve")
        self.assertIn("flat", base)
        self.assertIn("6", base)
        self.assertIn("room", base)
        self.assertIn("wrap", self.rule("700 mm"))
        tube = self.rule("gl.tube_along_path")
        self.assertIn("rounded", tube)
        embedded = self.rule("EEVEE")
        self.assertIn("AR sheet", embedded)
        self.assertIn("inside", embedded)

    def test_the_lens_colour_rule_names_the_guards(self):
        r = self.rule("summary.lens_colour")
        for word in ("reflection_too_bright", "backdrop_dark", "backdrop_uneven"):
            self.assertIn(word, r)

    def test_the_export_audit_flags_are_defined(self):
        # AUTHOR_PROMPT.md says the rules explain export.audit's flags: one rule defines each and the response
        r = self.rule("flat_plane_normal_bleed")
        for word in ("export.audit", "faceted_sweep", "planar_mirror_lens", "notes", "tube_along_path", "base-curve rule",
                     "wrap", "along"):
            self.assertIn(word, r)
        self.assertLess(len(r), 1400, "compact")

    def test_the_other_rules_are_kept(self):
        from modeler import author
        for needle in ("Units: 1 Blender unit", "Register every visible object", "Translucency is measured", "First-turn checklist",
                       "Branding is small", "Evidence provenance", "Do not fake"):
            self.assertEqual(sum(needle in r for r in author.RULES), 1, needle)


@unittest.skipUnless((R0006 / "revisions" / "r0006" / "observe" / "ar_see_through_blue" / "report.json").is_file(),
                     "test-pilot-002 r0006 (local data) not present")
class R0006Regression(unittest.TestCase):
    """The delivered Tom Ford lens (T 0.75/0.725/0.67, mirror 0.23/0.20/0.15): the owner saw it too light in the mirror; the
    checker-fixture metric read value 0.92, saturation 1.05, hue 0.002 (a near-match), so the author lightened it."""

    @classmethod
    def setUpClass(cls):
        cls.rev = R0006 / "revisions" / "r0006"
        cls.evidence = json.loads((R0006 / "evidence" / "evidence.json").read_text(encoding="utf-8"))
        cls.rendered = {"status": "rendered", "fixtures": {"skin": "#cba68d", "blue": "#3a4f6e"},
                        "dirs": {k: str(cls.rev / "observe" / f"ar_see_through_{k}") for k in ("skin", "blue")}}

    def test_the_delivered_lens_reads_too_light_and_too_pale(self):
        d = lc.lens_colour_metric(self.rev / "model.glb", self.rendered, self.evidence, self.rev / "build" / "materials.json")
        self.assertEqual(d["status"], "measured", d)
        np.testing.assert_allclose(d["backdrop_rgb"], WHITE, atol=0.5)
        np.testing.assert_allclose(d["lens_transmission_effective"], [0.75, 0.725, 0.67], atol=0.01)   # the authored T
        self.assertAlmostEqual(d["value_ratio"], 1.10, delta=0.02)
        self.assertAlmostEqual(d["saturation_ratio"], 0.35, delta=0.05)
        self.assertFalse(d["match"])
        self.assertEqual(d["flags"][:2], ["too_light", "too_pale"])
        np.testing.assert_allclose(d["lens_transmission_recommended"], [0.59, 0.49, 0.38], atol=0.02)
        np.testing.assert_allclose(d["lens_transmission_upper_bound"], [0.667, 0.553, 0.430], atol=0.005)
        self.assertTrue(all(t > u for t, u in zip(d["lens_transmission_effective"], d["lens_transmission_upper_bound"])),
                        "r0006 passes more light than the photo's lens can in every channel")
        self.assertIsNone(d["lens_env_intensity_recommended"])

    def test_the_patched_density_lens_reads_as_a_match(self):
        # the diagnosis's density-only patch (0.2661, 0.4991, 0.8041) with the mirror kept: T = (1 - R) exp(-d); the two
        # fixture renders re-shaded per pixel with that T and each pixel's own fitted reflection (the runtime is linear in
        # the background: the fit reproduces its white and checker renders within 0.5 levels)
        from modeler import see_through
        T_new = (1 - np.array([0.23, 0.20, 0.15])) * np.exp(-np.array([0.2661, 0.4991, 0.8041]))
        (img_a, row), (img_b, _) = (see_through._front_render(Path(self.rendered["dirs"][k])) for k in ("skin", "blue"))
        masks = lc.lens_masks_in_render(self.rev / "model.glb", row, img_a.shape[:2])
        mask = np.zeros(img_a.shape[:2], bool)
        core = np.zeros(img_a.shape[:2], bool)
        for m in masks.values():
            mask |= m
            core |= lc.core_of(m)
        _, a_px = lc.fit_fixtures(lin(img_a[mask]), lin(img_b[mask]), lin(SKIN), lin(BLUE))
        for img, fx in ((img_a, SKIN), (img_b, BLUE)):
            img[mask] = np.round(lc.linear_to_srgb(np.clip(T_new * lin(fx) + a_px, 0, 1)))
        photo, _ = lc.photo_lens_core(self.evidence)
        d = lc.lens_colour_from_fixtures(photo, WHITE, img_a, img_b, core, SKIN, BLUE, mirrored=True, reflectance=[0.23, 0.20, 0.15])
        self.assertTrue(d["match"], d)
        self.assertAlmostEqual(d["value_ratio"], 0.99, delta=0.02)
        self.assertAlmostEqual(d["saturation_ratio"], 1.02, delta=0.06)
        self.assertLessEqual(d["hue_error"], 0.01)


if __name__ == "__main__":
    unittest.main()
