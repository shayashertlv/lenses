"""S8 lens appearance: synthetic photometry/classification fixtures + real-data smoke (skips without data)."""
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from bsa import core, intake, lens


def _pink():
    return np.array([0.65, 0.19, 0.33])


class ColourTests(unittest.TestCase):
    def test_lab_round_trip_and_white(self):
        rng = np.random.default_rng(0)
        x = rng.uniform(0.001, 1.0, (500, 3))
        np.testing.assert_allclose(lens.lab_to_linear(lens.linear_to_lab(x)), x, atol=1e-6)
        np.testing.assert_allclose(lens.linear_to_lab(np.ones(3)), [100, 0, 0], atol=1e-3)

    def test_matches_opencv_8bit_lab_used_by_s0(self):
        rgb = np.array([[[255, 255, 255], [241, 241, 241], [120, 90, 60], [10, 20, 200], [30, 160, 90]]], np.uint8)
        cv = intake.lab_image(rgb)[0]
        ours = lens.linear_to_lab(lens.srgb_to_linear(rgb[0] / 255.0))
        self.assertLess(np.abs(cv - ours).max(), 1.0)       # 8-bit quantisation of a, b and L*255/100

    def test_backdrop_linear_white(self):
        coef = np.zeros((3, 6))
        coef[0, 0] = 100.0
        bg = lens.backdrop_linear(coef, (4, 5))
        np.testing.assert_allclose(bg, 1.0, atol=1e-5)


class CoatTestTests(unittest.TestCase):
    def _flat(self, rgb, n=32):
        return np.tile(np.asarray(rgb, float), (n, 1))

    def test_tint_with_exposure_is_not_a_coat(self):
        B = self._flat([0.10, 0.10, 0.095])
        c = lens.coat_test(0.85 * B, B, 0.085, clipped=True)
        self.assertFalse(c["coat"])
        self.assertAlmostEqual(c["kappa"], 0.85, places=2)

    def test_neutral_uncoated_reflection_is_not_a_coat(self):
        B = self._flat(_pink() * 0.5)
        c = lens.coat_test(B + 0.06, B, float((B[0] + 0.06) @ lens.LUMA), clipped=False)
        self.assertFalse(c["coat"], c)

    def test_chromatic_front_is_a_coat(self):     # Oakley-like: green/purple front, pink back
        B = self._flat(_pink())
        F = self._flat([0.32, 0.33, 0.37])
        c = lens.coat_test(F, B, 0.33, clipped=True)
        self.assertTrue(c["coat"])
        self.assertTrue(c["coat_chroma"])

    def test_bright_front_is_a_coat(self):        # INVU-like: the front is ~2.2x the back
        B = self._flat([0.15, 0.26, 0.31])
        c = lens.coat_test(2.2 * B, B, 0.55, clipped=True)
        self.assertTrue(c["coat"])
        self.assertTrue(c["coat_luminance"])
        self.assertFalse(c["coat_chroma"])

    def test_band_depends_on_backdrop_clipping(self):
        B = self._flat([0.5, 0.5, 0.5])
        # 0.7 = kappa * 0.5 + rho: even with the full 0.08 uncoated allowance kappa >= 1.24 (ln 0.215)
        self.assertFalse(lens.coat_test(1.4 * B, B, 0.7, clipped=True)["coat"])       # within half a stop
        self.assertTrue(lens.coat_test(1.4 * B, B, 0.7, clipped=False)["coat"])       # beyond 10 %
        # a dark neutral lens 0.06 brighter in front is explained by uncoated reflection either way
        D = self._flat([0.2, 0.2, 0.2])
        self.assertFalse(lens.coat_test(D + 0.06, D, 0.26, clipped=False)["coat"])

    def test_gradient_registration_slack(self):
        v = (np.arange(32) + 0.5) / 32
        prof = 0.18 + 0.44 / (1 + np.exp(-(v - 0.75) * 14))
        B = prof[:, None] * np.array([1.3, 1.0, 0.65])[None]
        F = np.roll(B, 1, axis=0)
        F[0] = B[0]
        c = lens.coat_test(F, B, 0.3, clipped=True)
        self.assertFalse(c["coat"], c)
        self.assertEqual(abs(c["registration_shift_bins"]), 1)

    def test_untestable_without_bins(self):
        B = np.full((32, 3), np.nan)
        self.assertFalse(lens.coat_test(B, B, 0.3, True)["testable"])

    def test_transmission_scale(self):
        B = self._flat([0.10, 0.10, 0.095])
        self.assertAlmostEqual(lens.transmission_scale(0.85 * B, B), 0.85, places=6)
        self.assertEqual(lens.transmission_scale(1.5 * B, B), 1.0)
        F = self._flat([0.32, 0.33, 0.37])
        self.assertAlmostEqual(lens.transmission_scale(F, self._flat(_pink())), 0.32 / 0.65, places=6)


class ClassifyAndGradientTests(unittest.TestCase):
    def _stats(self, T, top, bot):
        T = np.asarray(T, float)
        return {"ratio_Y": float(T @ lens.LUMA), "saturation": float((T.max() - T.min()) / T.max()),
                "Y_top20": top, "Y_bottom20": bot}

    def test_classes(self):
        no = {"coat": False}
        self.assertEqual(lens.classify(self._stats([1, 1, 1], 1, 1), None, no)["class"], "clear")
        self.assertEqual(lens.classify(self._stats([0.1, 0.1, 0.095], 0.1, 0.1), None, no)["class"], "tint")
        g = lens.classify(self._stats([0.27, 0.21, 0.14], 0.18, 0.59), None, no)
        self.assertEqual(g["class"], "gradient")
        self.assertFalse(g["gradient_inverted"])
        m = lens.classify(self._stats([0.27, 0.21, 0.14], 0.18, 0.59), None, {"coat": True})
        self.assertEqual((m["class"], m["base_class"]), ("mirror", "gradient"))
        # a small absolute difference on a dark lens is not a gradient even at a high ratio
        self.assertEqual(lens.classify(self._stats([0.03, 0.03, 0.03], 0.02, 0.05), None, no)["class"], "tint")
        # coloured bright lens is a tint, not clear
        self.assertEqual(lens.classify(self._stats([0.9, 0.6, 0.3], 0.6, 0.6), None, no)["class"], "tint")

    def test_pav(self):
        y = np.array([0.0, 0.3, 0.2, 0.5, 0.4, 1.0])
        out = lens.pav_increasing(y)
        self.assertTrue(np.all(np.diff(out) >= -1e-12))
        np.testing.assert_allclose(out, [0.0, 0.25, 0.25, 0.45, 0.45, 1.0])

    def test_gradient_fit_reaches_the_measured_extremes(self):
        n = 64
        v = (np.arange(n) + 0.5) / n
        top, bot = np.array([0.23, 0.18, 0.11]), np.array([0.76, 0.68, 0.55])
        t = 1 / (1 + np.exp(-(v - 0.7) * 12))
        t = (t - t[3]) / (t[60] - t[3])
        T = top[None] * (1 - t[:, None]) + bot[None] * t[:, None]
        T[:3] = np.nan
        T[61:] = np.nan
        counts = np.where(np.isfinite(T).all(1), 500, 0)
        g = lens.gradient_fit(T, counts)
        np.testing.assert_allclose(g["top_linear_rgb"], top, atol=0.01)
        np.testing.assert_allclose(g["bottom_linear_rgb"], bot, atol=0.01)
        self.assertTrue(np.all(np.diff(g["profile"]) >= -1e-9))
        self.assertLess(g["blend_rms"], 0.005)
        png = lens.gradient_png(g["top_linear_rgb"], g["bottom_linear_rgb"], g["profile"])
        self.assertEqual(png.shape, (64, 1, 3))
        self.assertLess(int(png[0, 0].sum()), int(png[-1, 0].sum()))     # row 0 = top = dark


def _synthetic_front(clear=False, seed=0):
    """White backdrop, one elliptical lens (brown tint or clear), a dark navy temple bar seen
    through it, a white highlight blob, a navy frame ring. Returns rgb, lens mask, frame mask."""
    H, W = 240, 320
    yy, xx = np.mgrid[0:H, 0:W]
    lens_m = ((xx - 160) / 110.0) ** 2 + ((yy - 120) / 80.0) ** 2 <= 1.0
    ring = (((xx - 160) / 122.0) ** 2 + ((yy - 120) / 92.0) ** 2 <= 1.0) & ~lens_m
    T = np.array([1.0, 1.0, 1.0]) if clear else np.array([0.27, 0.21, 0.14])
    navy = np.array([0.02, 0.03, 0.06]) if not clear else np.array([0.55, 0.40, 0.12])   # gold on the clear lens
    img = np.ones((H, W, 3))
    img[lens_m] = T
    bar = lens_m & (np.abs(xx - 90 - 0.3 * (yy - 120)) < 9)
    img[bar] = navy * (T if not clear else 1.0)
    blob = lens_m & ((xx - 210) ** 2 + (yy - 90) ** 2 < 36)
    if not clear:
        img[blob] = 1.0
    img[ring] = navy
    rng = np.random.default_rng(seed)
    srgb = lens.linear_to_srgb(img) * 255 + rng.normal(0, 1.0, img.shape)
    return np.clip(np.round(srgb), 0, 255).astype(np.uint8), lens_m, ring, bar, blob


class FilterTests(unittest.TestCase):
    def _run(self, clear):
        rgb, lens_m, ring, bar, blob = _synthetic_front(clear)
        bg = np.ones(rgb.shape, np.float32)
        s = lens.sample_view("front", rgb, bg, [lens_m], [(50, 40, 270, 200)], ["C"], lambda x, y: (x, y),
                             mm_px=0.25, erode_mm=2.0)
        fc = lens.frame_colours(rgb, ring, np.array([100.0, 0, 0]))
        f = lens.filter_samples(s, fc)
        return s, f, bar, blob

    def test_tint_filter_removes_structure_and_highlight(self):
        s, f, bar, blob = self._run(clear=False)
        keep = f["keep"]
        on_bar = bar[s.ys, s.xs]
        on_blob = blob[s.ys, s.xs]
        self.assertEqual(int((keep & on_bar).sum()), 0)
        self.assertEqual(int((keep & on_blob).sum()), 0)
        self.assertGreater(keep.mean(), 0.6)
        med = np.median(s.ratio[keep], axis=0)
        np.testing.assert_allclose(med, [0.27, 0.21, 0.14], atol=0.01)

    def test_clear_lens_is_not_eaten_by_the_highlight_rule(self):
        s, f, bar, _ = self._run(clear=True)
        keep = f["keep"]
        self.assertEqual(f["rules"]["highlight"], 0)
        self.assertGreater(keep.mean(), 0.6)
        self.assertEqual(int((keep & bar[s.ys, s.xs]).sum()), 0)
        np.testing.assert_allclose(np.median(s.ratio[keep], axis=0), 1.0, atol=0.01)

    def test_sample_view_coordinates_and_erosion(self):
        rgb, lens_m, *_ = _synthetic_front()
        bg = np.ones(rgb.shape, np.float32)
        s = lens.sample_view("front", rgb, bg, [lens_m], [(50, 40, 270, 200)], ["L"], lambda x, y: (x, y),
                             mm_px=0.25, erode_mm=2.0)
        self.assertGreater(s.v.min(), 0.0)
        self.assertLess(s.v.max(), 1.0)
        self.assertAlmostEqual(float(s.v[np.argmin(s.ys)]), (s.ys.min() - 40) / 160.0, places=6)
        # L lens: + u is the temple side = smaller x
        self.assertGreater(s.u[np.argmin(s.xs)], 0.5)
        # erosion by 8 px: every kept pixel is > 8 px inside the ellipse
        d = lens.ndimage.distance_transform_edt(np.pad(lens_m, 1))[1:-1, 1:-1]
        self.assertTrue(np.all(d[s.ys, s.xs] > 8.0))

    def test_bin_profile(self):
        v = np.array([0.01, 0.02, 0.03, 0.99])
        vals = np.array([[1.0], [2.0], [3.0], [9.0]])
        P, c = lens.bin_profile(v, vals, 4, min_px=2)
        self.assertEqual(c.tolist(), [3, 0, 0, 1])
        self.assertEqual(P[0, 0], 2.0)
        self.assertTrue(np.isnan(P[3, 0]))


class CoatFitTests(unittest.TestCase):
    """The mirror-coat fit's model and the rendered lens-colour check."""

    def test_unrounded_appearance_matches_the_descriptor(self):
        from reconstruction.lens_appearance import LensAppearance
        T = np.stack([np.linspace(0.3, 0.1, 64), np.linspace(0.2, 0.08, 64), np.linspace(0.4, 0.2, 64)], 1)
        knots = [10.0, 30.0, 50.0]
        R = np.array([[0.1, 0.6, 0.3], [0.2, 0.4, 0.4], [0.5, 0.1, 0.6]])
        d = lens.table_descriptor(T, knots, R, [lens.MIRROR_REAR_FRACTION] * 3)
        la_d = LensAppearance.from_dict(d)
        la_u = lens._appearance(T, R[0], [(0.0, R[0])] + list(zip(knots, R)) + [(90.0, np.ones(3))],
                                [lens.MIRROR_REAR_FRACTION] * 3)
        v, a = np.linspace(0, 1, 50), np.linspace(0, 80, 50)
        e_d, e_u = la_d.evaluate(v, a), la_u.evaluate(v, a)
        np.testing.assert_allclose(e_d.reflectance_rgb, e_u.reflectance_rgb, atol=1e-5)
        np.testing.assert_allclose(e_d.transmission_rgb, e_u.transmission_rgb, atol=1e-5)
        np.testing.assert_allclose(la_d.evaluate(0.5, 0.0).reflectance_rgb, R[0], atol=1e-6)

    def test_band_de00(self):
        rng = np.random.default_rng(0)
        v = rng.uniform(0, 1, 4000)
        col = np.where(v[:, None] < 0.5, [0.1, 0.3, 0.1], [0.3, 0.1, 0.3])       # green top, violet bottom
        same = lens.band_de00(col, v, col, v)
        self.assertEqual(same["mean"], 0.0)
        grey = np.tile(col.mean(0), (len(v), 1))                                  # one pooled colour for both
        split = lens.band_de00(col, v, grey, v)
        self.assertGreater(split["mean"], 15.0, "a two-colour lens rendered in its pooled colour fails every band")
        self.assertEqual(len(split["bands"]), lens.LENS_BANDS)


class MaterialTests(unittest.TestCase):
    def test_tint_material(self):
        m = lens.gltf_material("x_lens", "tint", [0.084, 0.084, 0.080])
        self.assertEqual(m["extensions"]["KHR_materials_transmission"]["transmissionFactor"], 1.0)
        self.assertEqual(m["extensions"]["KHR_materials_ior"]["ior"], 1.5)
        self.assertEqual(m["pbrMetallicRoughness"]["baseColorFactor"], [0.084, 0.084, 0.08, 1.0])
        self.assertNotIn("KHR_materials_specular", m["extensions"])
        json.dumps(m)

    def test_mirror_textures(self):
        T = np.tile([0.3, 0.1, 0.15], (64, 1))
        R = np.tile([0.0, 0.24, 0.21], (64, 1))
        mt = lens.mirror_textures(T, R)
        self.assertAlmostEqual(mt["specular_scale"], 0.24 / lens.BASE_F0, places=6)
        self.assertLessEqual(mt["specular_texture_linear"].max(), 1.0 + 1e-12)
        np.testing.assert_allclose(mt["base_linear"][0], [0.3, 0.1 / 0.76, 0.15 / 0.79])

    def test_mirror_textures_scaled_coat(self):
        T = np.tile([0.3, 0.1, 0.15], (64, 1))
        R = np.tile([0.0, 0.24, 0.21], (64, 1))
        mt = lens.mirror_textures(T, R, 0.1)
        np.testing.assert_allclose(mt["base_linear"][0], [0.3, 0.1 / (1 - 0.024), 0.15 / (1 - 0.021)])
        np.testing.assert_allclose(mt["emissive_linear"][0], [0.0, 0.24, 0.21])
        self.assertAlmostEqual(mt["f0_used_max"], 0.024, places=9)
        bare = lens.mirror_textures(T, R, 0.0)
        np.testing.assert_allclose(bare["base_linear"], T)

    def test_blob_areas(self):
        img = np.full((60, 80, 3), 120, np.uint8)
        region = np.zeros((60, 80), bool)
        region[5:55, 5:75] = True
        img[10:14, 10:15] = (40, 255, 250)       # a saturated cyan blob, 20 px
        img[30:32, 40:42] = (255, 255, 255)      # a white glint, 4 px
        img[40:50, 60:70] = (250, 250, 250)      # bright but not clipped (< 250 in no channel? = 250 counts)
        area, n = lens.blob_areas_mm2(img, region, 0.5)
        self.assertEqual(n, 20 + 4 + 100)
        self.assertAlmostEqual(area, 100 * 0.25)
        img[40:50, 60:70] = (200, 200, 200)
        area, n = lens.blob_areas_mm2(img, region, 0.5)
        self.assertAlmostEqual(area, 20 * 0.25)
        self.assertEqual(lens.blob_areas_mm2(img, np.zeros_like(region), 0.5), (0.0, 0))

    def test_emissive_material_builds_in_s9(self):
        from bsa import export
        with tempfile.TemporaryDirectory() as td:
            b_, s_, e_ = (Path(td) / f"{k}.png" for k in "bse")
            for f in (b_, s_, e_):
                Image.fromarray(lens.profile_png(np.full((64, 3), 0.3))).save(f)
            m = lens.gltf_material("x_lens", "mirror", [0.3, 0.1, 0.15], b_, {"scale": 0.6, "texture": s_},
                                   {"factor": 0.77, "texture": e_})
            self.assertEqual(m["emissiveFactor"], [0.77, 0.77, 0.77])
            m = json.loads(json.dumps(m))
            bld = export.GlbBuilder()
            mat, summary = export.build_material(bld, "lens", {"gltf": m, "transmission": 1.0, "ior": 1.5,
                                                               "double_sided": False}, lens_rule=True)
            self.assertEqual(summary["transmission"], 1.0)
            self.assertIn("index", mat["emissiveTexture"])
            self.assertEqual(len(bld.doc["textures"]), 1)            # identical images are shared
            m2 = lens.gltf_material("x_lens", "mirror", [0.3, 0.1, 0.15], b_, None, None)
            self.assertNotIn("emissiveFactor", m2)
            self.assertNotIn("KHR_materials_specular", m2["extensions"])

    def test_material_builds_in_s9(self):
        from bsa import export
        with tempfile.TemporaryDirectory() as td:
            g = Path(td) / "g.png"
            s = Path(td) / "s.png"
            Image.fromarray(lens.gradient_png([0.2, 0.15, 0.1], [0.7, 0.6, 0.5], np.linspace(0, 1, 64))).save(g)
            Image.fromarray(lens.profile_png(np.full((64, 3), 0.5))).save(s)
            m = lens.gltf_material("x_lens", "mirror", [0.3, 0.1, 0.15], g, {"scale": 6.0, "texture": s})
            m = json.loads(json.dumps(m))
            b = export.GlbBuilder()
            spec = {"gltf": m, "transmission": 1.0, "ior": 1.5, "double_sided": False}
            mat, summary = export.build_material(b, "lens", spec, lens_rule=True)
            self.assertEqual(summary["transmission"], 1.0)
            self.assertIn("index", mat["pbrMetallicRoughness"]["baseColorTexture"])
            self.assertIn("index", mat["extensions"]["KHR_materials_specular"]["specularColorTexture"])
            self.assertEqual(len(b.doc["textures"]), 2)

    def test_lens_appearance_descriptor(self):
        from reconstruction.lens_appearance import LensAppearance
        d1 = lens.lens_appearance_descriptor(np.array([[0.084, 0.084, 0.08]]), np.full(3, 0.04))
        self.assertEqual(len(d1["optical_density_keyframes"]), 1)
        self.assertEqual(d1["optical_density_keyframes"][0]["v"], 0.0)
        T = np.linspace([0.2, 0.15, 0.1], [0.7, 0.6, 0.5], 64)       # row 0 = top (dark)
        d = lens.lens_appearance_descriptor(T, np.full(3, 0.04))
        keys = d["optical_density_keyframes"]
        self.assertLessEqual(len(keys), 16)
        self.assertEqual((keys[0]["v"], keys[-1]["v"]), (0.0, 1.0))
        # descriptor v = 0 is the lens BOTTOM (light, low density)
        self.assertLess(keys[0]["optical_density_rgb"][0], keys[-1]["optical_density_rgb"][0])
        la = LensAppearance.from_dict(d)
        t_top = la.evaluate(1.0).transmission_rgb
        np.testing.assert_allclose(t_top, 0.96 * np.clip(T[0] / 0.96, 0, 1), atol=1e-4)

    def test_canonical_mirror_descriptor(self):
        """A coat R(angle) table + rear fraction: the runtime schema (0 -> R(0), ... 90 -> 1), the TOTAL
        normal-incidence transmission kept (density = -ln(T / (1 - R(0)))), per-channel scale capped at CANON_R_MAX."""
        from reconstruction.lens_appearance import LensAppearance
        T = np.tile([0.3, 0.07, 0.14], (64, 1))
        coat = {"r0": np.array([0.0, 0.45, 0.16]),
                "table": [(0.0, np.array([0.0, 0.45, 0.16])), (20.0, np.array([0.0, 0.39, 0.2])),
                          (50.0, np.array([0.06, 0.09, 0.23])), (90.0, np.ones(3))]}
        d = lens.scaled_descriptor(T, coat, 1.0, [lens.MIRROR_REAR_FRACTION] * 3)
        la = LensAppearance.from_dict(d)
        self.assertEqual(d["angular_reflectance_keyframes"][0]["reflectance_rgb"], d["normal_reflectance_rgb"])
        self.assertEqual(d["angular_reflectance_keyframes"][-1]["angle_degrees"], 90.0)
        np.testing.assert_allclose(la.evaluate(0.5, 0.0).transmission_rgb, T[0], atol=1e-4)
        np.testing.assert_allclose(la.evaluate(0.5, 0.0).reflectance_rgb, coat["r0"], atol=1e-6)
        d2 = lens.scaled_descriptor(T, coat, [1.0, 3.0, 1.0])            # per channel, capped
        self.assertLessEqual(max(d2["normal_reflectance_rgb"]), lens.CANON_R_MAX + 1e-9)
        self.assertEqual(d2["angular_reflectance_keyframes"][-1]["reflectance_rgb"], [1.0, 1.0, 1.0])
        e = lens.edge_ring_appearance({"transmission": 0.5, "base_color": [0.86, 0.86, 0.86, 1.0]})
        self.assertAlmostEqual(e["normal_reflectance_rgb"][0], 0.36, places=6)
        self.assertEqual(e["roughness"], lens.EDGE_RING_ROUGHNESS)

    def test_coat_angular_table_bins_by_incidence(self):
        """The front photo's reflected light R = front - kappa T(v), binned by incidence angle: a shield seen from 0 to
        60 deg in its own front photo gets a table; a flat lens (no spread) gets R(0) and Schlick."""
        n = 6000
        rng = np.random.default_rng(0)
        ang = rng.uniform(0.0, 60.0, n)
        v = rng.uniform(0.0, 1.0, n)
        T = np.tile([0.3, 0.07, 0.14], (64, 1))
        R_true = np.where(ang[:, None] < 25.0, [0.0, 0.45, 0.16], [0.06, 0.09, 0.23])
        lin = T[0] + R_true
        s_ = lens.ViewSamples("front", (10, 10), np.zeros(n, int), np.zeros(n, int), np.ones(n, int), np.zeros(n), v,
                              lin, np.ones((n, 3)), np.zeros((n, 3)), np.zeros(n), np.zeros(n), 0.1, np.zeros((10, 10), bool))
        meas = {"views": {"front": {"samples": s_, "filter": {"keep": np.ones(n, bool)}}}}
        c = lens.coat_angular_table(meas, {"T_uv": T}, ang)
        self.assertIsNotNone(c["table"])
        self.assertEqual(c["table"][0][0], 0.0)
        self.assertEqual(c["table"][-1][0], 90.0)
        self.assertLessEqual(len(c["table"]), 16)
        np.testing.assert_allclose(c["r0"], [0.0, 0.45, 0.16], atol=1e-9)
        late = [rgb for a, rgb in c["table"] if 30 < a < 90]
        np.testing.assert_allclose(late[0], [0.06, 0.09, 0.23], atol=1e-9)
        flat = lens.coat_angular_table(meas, {"T_uv": T}, np.full(n, 3.0))
        self.assertIsNone(flat["table"])


class OccluderTests(unittest.TestCase):
    def test_structure_behind_the_plate(self):
        from bsa import raster

        def box(x0, x1, y0, y1, z0, z1):
            V = np.array([[x, y, z] for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)], float)
            F = np.array([[0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5], [0, 4, 5], [0, 5, 1],
                          [2, 3, 7], [2, 7, 6], [0, 2, 6], [0, 6, 4], [1, 5, 7], [1, 7, 3]])
            T = V[F]
            n = np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0])
            flip = (n * (T.mean(1) - V.mean(0))).sum(1) < 0          # outward winding
            F[flip] = F[flip][:, ::-1]
            return V, F
        Vp, Fp = box(-20, 20, -15, 15, -0.5, 0.5)         # lens plate (a 1 mm closed solid)
        Vb, Fb = box(-4, 4, -30, 30, -12, -9)              # temple-like bar behind it
        V = np.vstack([Vp, Vb])
        F = np.vstack([Fp, Fb + len(Vp)])
        frame = core.NormFrame((0.0, 0.0, -5.0), 60.0)
        shape = (200, 260)
        cam_f = raster.view_camera(frame, 0, 0, 4.0, shape)
        cam_b = raster.view_camera(frame, 180, 0, 4.0, shape)
        region = np.zeros(shape, bool)
        c = core.project_mm(np.array([[-18, 13, 0.5], [18, -13, 0.5]]), cam_f, frame)
        region[int(c[:, 1].min()):int(c[:, 1].max()), int(c[:, 0].min()):int(c[:, 0].max())] = True
        m, info = lens.occluder_mask(V, F, frame, cam_f, shape, region, cam_f, region, mm_px=0.25)
        self.assertEqual(info["plate"], "quadratic front + back surfaces")
        self.assertAlmostEqual(info["lens_thickness_median_mm"], 1.0, places=3)
        self.assertEqual(info["faces_behind"], 8)       # the bar's 2 end caps project outside the footprint
        cols = np.nonzero(m.any(axis=0))[0]
        bar_px = core.project_mm(np.array([[-4, 0, -9], [4, 0, -9]]), cam_f, frame)[:, 0]
        self.assertLess(abs(cols.min() - (bar_px.min() - 4)), 3)     # 1 mm grow = 4 px
        self.assertLess(abs(cols.max() - (bar_px.max() + 4)), 3)
        self.assertFalse(m[:, :int(bar_px.min()) - 10].any())
        # the back camera sees the same bar in front of the plate (mirrored image)
        reg_b = region[:, ::-1].copy()
        mb, _ = lens.occluder_mask(V, F, frame, cam_b, shape, reg_b, cam_f, region, mm_px=0.25)
        self.assertGreater(mb.sum(), 0.5 * m.sum())


DATA_OK = all((core.run_dir("m1", p) / s / "result.json").exists() for p in core.PRODUCTS for s in ("s0_intake", "s2_front"))


@unittest.skipUnless(DATA_OK, "BSA m1 S0/S2 artifacts missing")
class RealDataSmoke(unittest.TestCase):
    def test_stage_results_and_classes(self):
        expect = {"miu": "clear", "oakley": "mirror", "rayban": "tint", "vb": "gradient", "invu": "mirror"}
        for p, cls in expect.items():
            sd = core.stage_dir("m1", p, lens.STAGE)
            if not sd.done():
                self.skipTest(f"s8 not run for {p}")
            r, a = sd.load()
            self.assertEqual(r["class"], cls, p)
            m = r["gltf_material"]
            self.assertEqual(m["extensions"]["KHR_materials_transmission"]["transmissionFactor"], 1.0)
            self.assertEqual(m["extensions"]["KHR_materials_ior"]["ior"], 1.5)
            self.assertTrue((sd.root / "sheet.png").exists())
            if cls == "gradient":
                self.assertTrue((sd.root / "gradient.png").exists())
                self.assertEqual(np.asarray(Image.open(sd.root / "gradient.png")).shape, (64, 1, 3))
            # every product carries the runtime's canonical descriptor (no blurred legacy lens), schema-valid
            from reconstruction.lens_appearance import LensAppearance
            LensAppearance.from_dict(r["lens_appearance"])
            self.assertNotIn("emissiveFactor", m)
            self.assertNotIn("KHR_materials_specular", m["extensions"])
            if cls == "mirror":
                self.assertIn("mirror_approximation_m3", r["flags"])
                # the calibration must have run (review finding: a failed calibration used to pass this test silently)
                cal = r.get("mirror_calibration") or {}
                self.assertTrue(cal.get("ok"), cal.get("reason"))
                self.assertNotIn("mirror_calibration_failed", r["flags"])
                c = cal["chosen"]
                # the choice metric: dE00 over the lens-height bands (the lens-colour check S10 reads); the chosen
                # candidate is never worse than the best scale of the measured coat
                best_grid = min(g["dE00_bands"] for g in cal["grid"])
                self.assertLessEqual(c["dE00_bands"], best_grid + 1e-9)
                self.assertEqual(cal["rendered_dE00"], c["dE00_bands"])
                self.assertIsNotNone(r["mirror"]["coat_angular"]["r0_measured"])
            # every lens carries the rendered lens-colour check (S10's lens_colour rule)
            chk = r.get("lens_colour_check") or {}
            self.assertTrue(chk.get("ok"), chk.get("reason"))
            self.assertIsNotNone(chk.get("rendered_dE00"))
            if cls == "clear":
                # a clear lens has its frosted edge band (the only thing that shows a rimless clear lens), canonical too
                ring = r.get("edge_ring")
                self.assertIsNotNone(ring)
                self.assertTrue(lens.EDGE_RING_WIDTH_MM[0] <= ring["width_mm"] <= lens.EDGE_RING_WIDTH_MM[1])
                self.assertTrue(lens.EDGE_RING_TRANSMISSION[0] <= ring["transmission"] <= lens.EDGE_RING_TRANSMISSION[1])
                LensAppearance.from_dict(ring["lens_appearance"])

    def test_deterministic_measure_and_analyse(self):
        outs = []
        for _ in range(2):
            meas = lens.measure("invu", "m1")
            ana = lens.analyse(meas)
            outs.append((ana["class"], ana["tint_linear_rgb"], json.dumps(ana["coat"], sort_keys=True),
                         meas["views"]["back"]["filter"]["keep"].tobytes(), ana["T_uv"].tobytes()))
        self.assertEqual(outs[0], outs[1])

    def test_angled_view_never_read(self):
        src = Path(lens.__file__).read_text()
        self.assertNotIn('"angled"', src)
        self.assertNotIn("'angled'", src)


if __name__ == "__main__":
    unittest.main()
