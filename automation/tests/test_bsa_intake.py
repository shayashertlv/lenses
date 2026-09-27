"""S0 intake: synthetic maths (backdrop, shadow rule, reflections, symmetry) + real-data smoke."""
import json
import unittest

import numpy as np

from bsa import core, intake


def _backdrop(h=240, w=320, gradient=True):
    yy, xx = np.mgrid[0:h, 0:w]
    g = 1.0 if gradient else 0.0
    base = 235.0 - g * 18.0 * yy / h - g * 6.0 * xx / w
    return np.repeat(base[..., None], 3, -1)


class BackdropAndMatte(unittest.TestCase):
    def test_quadratic_backdrop_recovers_gradient_and_coefficients(self):
        img = _backdrop().astype(np.uint8)
        img[80:160, 100:220] = (40, 30, 25)             # object in the middle, off the border ring
        lab = intake.lab_image(img)
        bg, coef = intake.backdrop_model(lab)
        clean = intake.lab_image(_backdrop().astype(np.uint8))
        self.assertLess(float(np.abs(bg - clean)[..., 0].max()), 1.5)
        np.testing.assert_allclose(intake.backdrop_lab(coef, img.shape[:2]), bg, atol=1e-4)

    def test_shadow_rule_keeps_object_and_drops_achromatic_shadow(self):
        img = _backdrop(gradient=False)
        img[60:120, 80:240] = (30, 25, 20)                # dark object (strong contrast)
        img[130:170, 80:240] -= 22                        # soft grey floor shadow: achromatic, moderate
        img[60:120, 260:300] = (205, 150, 60)             # pale chromatic part (moderate L, high chroma)
        fg, _ = intake.matte(np.clip(img, 0, 255).astype(np.uint8))
        self.assertTrue(fg[70:110, 90:230].all())
        self.assertFalse(fg[140:160, 100:220].any(), "achromatic shadow must not be foreground")
        self.assertGreater(fg[70:110, 265:295].mean(), 0.95, "chromatic part must be foreground")

    def test_keep_components_drops_specks(self):
        m = np.zeros((100, 100), bool)
        m[10:60, 10:60] = True
        m[80, 80] = True
        out = intake.keep_components(m, 0.002)
        self.assertFalse(out[80, 80])
        self.assertTrue(out[30, 30])


class Reflections(unittest.TestCase):
    def _scene(self, with_reflection=True, attached=False):
        img = _backdrop(300, 400, gradient=False)
        img[80:140, 60:340] = (190, 40, 150)               # object: magenta lens (chromatic, as Oakley)
        img[140:150, 60:340] = (40, 35, 35)                # dark lower rim
        if attached:                                        # a strong "temple" reaching the floor
            img[80:215, 50:60] = (30, 25, 25)
        if with_reflection:
            # mirror row 160: attenuated vertical flip of rows 80..150 lands on rows 170..240
            src = img[80:150].copy()
            refl = 235 - (235 - src[::-1]) * 0.30   # contrast ~20, as measured on the Oakley back (22-23)
            img[170:240, 60:340] = refl[:, 60:340]
        return np.clip(img, 0, 255).astype(np.uint8)

    def test_detached_reflection_is_cut(self):
        img = self._scene()
        fg, info = intake.matte(img)
        cut, rinfo = intake.floor_reflection_cut(fg, info["score"])
        self.assertTrue(fg[200:230, 100:300].any(), "fixture: the reflection should enter the raw matte")
        self.assertFalse(cut[175:240, 70:330].any())
        self.assertTrue(cut[90:130, 80:320].all())
        self.assertGreater(rinfo["cut_pixels"], 0)

    def test_attached_reflection_is_cut_but_temple_survives(self):
        img = self._scene(attached=True)
        fg, info = intake.matte(img)
        cut, rinfo = intake.floor_reflection_cut(fg, info["score"])
        self.assertLess(cut[180:235, 80:330].mean(), 0.05)
        self.assertTrue(cut[150:210, 51:59].all(), "the strong temple reaching the floor must survive")

    def test_no_reflection_no_cut_and_lens_protection(self):
        img = self._scene(with_reflection=False)
        # a light lower half inside a "lens" (gradient lens) must never be read as a reflection
        img[115:140, 100:300] = (170, 150, 120)
        fg, info = intake.matte(img)
        lens = np.zeros(fg.shape, bool)
        lens[85:140, 95:305] = True
        cut, rinfo = intake.floor_reflection_cut(fg | lens, info["score"], intake.ndimage.binary_dilation(lens, iterations=3))
        self.assertEqual(rinfo["cut_pixels"], 0)


class LensOpenings(unittest.TestCase):
    """The detector proposal is evidence of a lens, not of material: enclosed backdrop it swallowed next to a tinted
    lens (a brow vent) is not united into fg; a highlight surrounded by lens colour and a clear lens stay."""

    def _scene(self, lens_rgb=(90, 182, 151)):
        img = _backdrop(300, 700, gradient=False)
        yy, xx = np.mgrid[0:300, 0:700]
        shape = (((xx - 350) / 250.0) ** 2 + ((yy - 140) / 90.0) ** 2 <= 1.0) & (yy >= 100)
        img[shape] = lens_rgb
        img[75:100, 110:590] = (22, 23, 25)                          # black brow
        img[(np.abs(xx - 230) <= 30) & (yy >= 100) & (yy < 106)] = 235.0     # vent between lens top and brow
        img[160:168, 400:420] = 250.0                                 # specular highlight inside the lens
        lens = shape.copy()                                          # the proposal swallows the vent and the highlight
        return np.clip(img, 0, 255).astype(np.uint8), lens

    def test_vent_is_cut_highlight_stays(self):
        img, lens = self._scene()
        fg, lens_out, info = intake.intake_view(img, "front", lens=lens)
        self.assertIn("lens_openings_cut", info["flags"])
        self.assertFalse(fg[102, 230], "the vent is backdrop, not foreground")
        self.assertFalse(lens_out[102, 230])
        self.assertTrue(fg[163, 410], "a highlight surrounded by lens colour stays foreground")
        self.assertTrue(fg[102, 300], "the lens next to the vent stays")
        self.assertTrue(info["_openings"][102, 230])

    def test_highlight_touching_the_rim_is_not_a_vent(self):
        # a specular highlight on a tinted lens that TOUCHES the black rim is enclosed backdrop colour touching the
        # outside of the proposal, like a vent; it is surrounded by lens colour, not frame (``opening_onto_frame``)
        H, W = 500, 1400
        img = _backdrop(H, W, gradient=False)
        yy, xx = np.mgrid[0:H, 0:W]
        lens = np.zeros((H, W), bool)
        for cx in (400.0, 1000.0):
            f_rim = np.hypot((xx - cx) / 290.0, (yy - 250) / 190.0)
            f_lens = np.hypot((xx - cx) / 260.0, (yy - 250) / 160.0)
            img[f_rim <= 1.0] = (25, 25, 28)
            img[f_lens <= 1.0] = (60, 110, 70)
            lens |= f_lens <= 1.0
        img[235:265, 690:710] = (25, 25, 28)                                     # bridge
        hl = (np.hypot(xx - 1000, yy - 107) <= 17) & (np.hypot((xx - 1000) / 260.0, (yy - 250) / 160.0) <= 1.0)
        img[hl] = 252.0                                                          # touches the rim at the lens top
        fg, lens_out, info = intake.intake_view(np.clip(img, 0, 255).astype(np.uint8), "front", lens=lens)
        self.assertGreater(int(hl.sum()), 800)
        self.assertNotIn("lens_openings_cut", info["flags"])
        self.assertEqual(info["lens_openings"]["proposal_px_cut"], 0)
        self.assertTrue(fg[hl].all() and lens_out[hl].all(), "the highlight is lens, not an opening")
        op = info["lens_openings"]["openings"]
        self.assertEqual(op["vents"], 0)
        self.assertLess(op["rejected"][0]["frame_share"], intake.VENT_FRAME_SHARE)
        # the same scene with a vent (backdrop between the lens top and the rim, one side against the frame) is cut
        img2 = img.copy()
        img2[hl] = (60, 110, 70)
        vent = (np.abs(xx - 1000) <= 50) & (yy >= 90) & (yy < 97) & (np.hypot((xx - 1000) / 260.0, (yy - 250) / 160.0) <= 1.0)
        img2[vent] = 235.0
        fg2, _, info2 = intake.intake_view(np.clip(img2, 0, 255).astype(np.uint8), "front", lens=lens)
        self.assertIn("lens_openings_cut", info2["flags"])
        self.assertFalse(fg2[93, 1000], "the vent is backdrop")

    def test_clear_lens_is_never_cut(self):
        img, lens = self._scene(lens_rgb=(232, 232, 233))           # a clear lens looks like the backdrop
        fg, _, info = intake.intake_view(img, "front", lens=lens)
        self.assertNotIn("lens_openings_cut", info["flags"])
        self.assertTrue(fg[102, 230])
        self.assertEqual(info["lens_openings"]["proposal_px_cut"], 0)


class Symmetry(unittest.TestCase):
    def test_mirror_and_axis(self):
        m = np.zeros((80, 200), bool)
        m[20:60, 40:90] = True
        m[20:60, 111:161] = True                            # mirror about x = 100.0
        x0, v = intake.symmetry_axis(m)
        self.assertAlmostEqual(x0, 100.0, delta=0.5)
        self.assertGreater(v, 0.99)
        np.testing.assert_array_equal(intake.mirror_x(m, 100.0), m)


@unittest.skipUnless(core.PRODUCTS["vb"].photo_path("front").exists(), "product photos not present")
class RealDataSmoke(unittest.TestCase):
    def test_m1_artifacts_schema(self):
        sd = core.stage_dir("m1", "vb", intake.STAGE)
        if not sd.done():
            self.skipTest("m1 intake not run")
        r, a = sd.load()
        for v in core.VIEWS:
            info = r["views"][v]
            self.assertEqual(a[f"fg_{v}"].shape, tuple(info["shape"]))
            self.assertEqual(a[f"fg_{v}"].dtype, bool)
            self.assertEqual(a[f"lens_{v}"].dtype, bool)
            for k in ("backdrop_rgb", "fg_pixels", "bbox_xyxy", "flags", "photo_sha256"):
                self.assertIn(k, info)
            if v in ("left", "right"):
                self.assertFalse(a[f"lens_{v}"].any())
            self.assertFalse((a[f"lens_{v}"] & ~a[f"fg_{v}"]).any(), "lens proposal must be inside fg")
        self.assertIn("mirror_iou", r["views"]["front"])

    def test_front_view_live_is_deterministic(self):
        prod = core.PRODUCTS["vb"]
        rgb = core.load_photo(prod, "front")
        fg1, lens1, info1 = intake.intake_view(rgb, "front")
        fg2, lens2, info2 = intake.intake_view(rgb, "front")
        np.testing.assert_array_equal(fg1, fg2)
        np.testing.assert_array_equal(lens1, lens2)
        self.assertGreater(info1["width_px"], 600)
        self.assertGreater(info1["mirror_iou"], 0.94)
        self.assertNotIn("floor_reflection_cut", info1["flags"])
        self.assertGreater(int(lens1.sum()), 0)

    def test_lens_openings_only_where_a_tinted_proposal_swallowed_backdrop(self):
        for p in core.PRODUCTS:
            sd = core.stage_dir("m1", p, intake.STAGE)
            if not sd.done():
                self.skipTest("m1 intake not run")
            r, a = sd.load()
            for v in intake.LENS_VIEWS:
                if "lens_openings" not in r["views"][v]:
                    self.skipTest("m1 intake predates lens openings")
                cut = r["views"][v]["lens_openings"]["proposal_px_cut"]
                if p == "oakley" and v in ("front", "back"):
                    self.assertGreater(cut, 0, f"{p} {v}: brow vents / enclosed backdrop under the lens")
                    self.assertFalse((a[f"fg_{v}"] & a[f"lens_openings_{v}"]).any())
                elif p != "oakley":
                    self.assertEqual(cut, 0, f"{p} {v}")

    def test_invu_front_is_low_resolution(self):
        sd = core.stage_dir("m1", "invu", intake.STAGE)
        if not sd.done():
            self.skipTest("m1 intake not run")
        r, _ = sd.load()
        self.assertIn("low_resolution", r["views"]["front"]["flags"])
        json.dumps(r)


if __name__ == "__main__":
    unittest.main()
