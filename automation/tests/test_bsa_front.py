"""S2 front partition: synthetic geometry/refinement fixtures + real-data smoke (skips without data)."""
import unittest

import numpy as np

from scipy import ndimage

from bsa import core, front, intake

WHITE = np.array([236.0, 236.0, 236.0])


def _ellipse_field(H, W, cx, cy, a, b):
    yy, xx = np.mgrid[0:H, 0:W].astype(float)
    return np.sqrt(((xx - cx) / a) ** 2 + ((yy - cy) / b) ** 2)


def _ellipse_pts(cx, cy, a, b, n=4000):
    t = np.linspace(0, 2 * np.pi, n, endpoint=False)
    return np.stack([cx + a * np.cos(t), cy + b * np.sin(t)], 1)


def _prep(img, lens):
    """S0-equivalent inputs from a synthetic photo: fg (matte | lens), pure matte, backdrop Lab."""
    img = np.clip(img, 0, 255).astype(np.uint8)
    matte, _ = intake.matte(img, lens)
    lab = intake.lab_image(img)
    bg, _ = intake.backdrop_model(lab)
    return img, matte | lens, matte, bg


def _pair_scene(bevel_px=4, over_px=5):
    """Two elliptical tinted lenses in a dark full rim with a light inner bevel; the lens
    proposal over-includes the bevel (as the detector does)."""
    H, W = 420, 900
    img = np.tile(WHITE, (H, W, 1))
    lens = np.zeros((H, W), bool)
    truth = []
    for cx in (270.0, 630.0):
        r_rim = _ellipse_field(H, W, cx, 210, 142, 102)
        r_lens = _ellipse_field(H, W, cx, 210, 120, 80)
        img[r_rim <= 1.0] = (32, 36, 62)                                  # navy rim
        bev = (r_lens > 1.0) & (_ellipse_field(H, W, cx, 210, 120 + bevel_px, 80 + bevel_px) <= 1.0)
        img[bev] = (150, 150, 160)                                        # light bevel (frame)
        img[r_lens <= 1.0] = (125, 104, 82)                               # brown tinted lens
        lens |= _ellipse_field(H, W, cx, 210, 120 + over_px, 80 + over_px) <= 1.0
        truth.append(_ellipse_pts(cx, 210, 120, 80))
    img[190:222, 400:500] = (32, 36, 62)                                  # bridge
    img, fg, matte, bg = _prep(img, lens)
    return img, fg, lens, matte, bg, truth


class PairFullRim(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        img, fg, lens, matte, bg, truth = _pair_scene()
        cls.truth = truth
        cls.part = front.partition(img, fg, lens, 140.0, None, matte, bg)
        cls.a = cls.part["arrays"]

    def test_layout_rim_and_sides(self):
        self.assertEqual(self.part["layout"], "pair")
        self.assertEqual(self.part["rim_class"], "full")
        self.assertEqual([li["side"] for li in self.part["lenses"]], ["R", "L"])
        self.assertGreater(self.part["lenses"][0]["centroid_px"][0], self.part["lenses"][1]["centroid_px"][0])
        self.assertAlmostEqual(self.part["axis_x_px"], 449.5, delta=1.0)

    def test_refinement_removes_bevel_over_inclusion(self):
        d_ref = front.contour_distance(self.a["lens1_poly"], self.truth[1])[0]
        d_raw = front.contour_distance(self.a["lens1_poly_raw"], self.truth[1])[0]
        self.assertLess(d_ref, 1.2, f"refined outline {d_ref:.2f} px from truth")
        self.assertGreater(d_raw, d_ref + 2.0, "raw detector outline should carry the over-inclusion")
        self.assertLess(self.part["refinement"]["median_bevel_offset_mm"], -0.5)

    def test_pair_is_exact_mirror_and_polys_follow_convention(self):
        x0 = self.part["axis_x_px"]
        R, L = self.a["lens1_poly"], self.a["lens2_poly"]
        mR = np.stack([2 * x0 - R[:, 0], R[:, 1]], 1)
        self.assertLess(front.contour_distance(mR, L)[0], 1e-6)
        for P in (R, L):
            self.assertGreater(front.signed_area(P), 0)
            self.assertEqual(int(np.argmin(P[:, 1])), 0)
            self.assertFalse(np.allclose(P[0], P[-1]), "no repeated end point")

    def test_types_rim_widths_and_masks(self):
        t = self.a["lens1_type"]
        self.assertEqual(t.dtype, np.int8)
        self.assertGreater(float((t == front.TYPE_FRAME).mean()), 0.95)
        w = self.a["lens1_rimw_px"]
        mm_px = self.part["mm_per_px"]
        # rim 22 px beyond the true edge (plus up to the bevel); refined edge sits on the truth
        self.assertAlmostEqual(float(np.median(w)), 22.0, delta=3.0)
        lab = self.a["lens_label"]
        self.assertEqual(set(np.unique(lab).tolist()), {0, 1, 2})
        self.assertFalse((self.a["frame_mask"] & (lab > 0)).any())
        self.assertTrue(self.a["frame_mask"][210, 450], "bridge belongs to the frame")
        self.assertGreater(mm_px, 0)


def _edge_error_px(poly, truth_pts, sel=None):
    """Mean distance (px) from the refined outline points (optionally a subset) to a dense truth ellipse."""
    from scipy.spatial import cKDTree
    P = poly if sel is None else poly[sel]
    return float(cKDTree(truth_pts).query(P)[0].mean())


def _temple_scene(bulge_px=14):
    """VB-like pair: navy rim (wide endpieces) with a dark groove line, brown lens, and a grey temple seen THROUGH each
    lens at its outer edge (touching the rim). The proposal over-includes 3 px everywhere and bulges `bulge_px`
    (~3 mm at 0.22 mm/px) outward over the temple, as the detector does."""
    H, W = 420, 900
    img = np.tile(WHITE, (H, W, 1))
    lens = np.zeros((H, W), bool)
    truth = []
    yy, xx = np.mgrid[0:H, 0:W]
    for cx, side in ((265.0, -1), (635.0, 1)):
        img[_ellipse_field(H, W, cx, 210, 168, 102) <= 1.0] = (32, 36, 62)             # navy rim, wide endpieces
        img[_ellipse_field(H, W, cx, 210, 122, 82) <= 1.0] = (14, 14, 18)              # dark groove line (frame)
        inside = _ellipse_field(H, W, cx, 210, 120, 80) <= 1.0
        img[inside] = (125, 104, 82)                                                   # brown tinted lens
        temple = inside & (np.abs(yy - 210) <= 28) & (side * (xx - cx) >= 88)
        img[temple] = (70, 68, 66)                                                     # temple seen through the lens
        prop = _ellipse_field(H, W, cx, 210, 123, 83) <= 1.0
        prop |= (((xx - (cx + side * 110.0)) / (10.0 + bulge_px)) ** 2 + ((yy - 210) / 40.0) ** 2) <= 1.0
        prop &= _ellipse_field(H, W, cx, 210, 168, 102) <= 1.0
        lens |= prop
        truth.append(_ellipse_pts(cx, 210, 120, 80))
    img[190:222, 400:500] = (32, 36, 62)                                               # bridge
    img, fg, matte, bg = _prep(img, lens)
    return img, fg, lens, matte, bg, truth


def _band_scene(band_rgb, band_px=6):
    """Oakley-like shield: a teal lens under a black neutral brow; between them a `band_px` band of `band_rgb`
    (the lens seen over the brow's rear lip when it keeps the lens's tint). The proposal ends inside the band."""
    H, W = 360, 900
    img = np.tile(WHITE, (H, W, 1))
    yy, xx = np.mgrid[0:H, 0:W]
    shape = (_ellipse_field(H, W, 450, 170, 320, 110) <= 1.0) & (yy >= 120)
    img[shape] = (90, 182, 151)                                                        # teal lens
    img[shape & (yy < 120 + band_px)] = band_rgb                                       # band under the brow
    img[92:120, 120:780] = (22, 23, 25)                                                # black brow (frame)
    lens = ndimage.binary_dilation(shape & (yy >= 120 + band_px // 2), iterations=3)
    img, fg, matte, bg = _prep(img, lens)
    return img, fg, lens, matte, bg


class TempleSeenThroughLens(unittest.TestCase):
    """(a) a temple seen through the lens near the rim contaminates the lens-side colour and the detector bulges ~3 mm:
    the frame-side registration must put the edge on the lens/groove boundary there too."""

    @classmethod
    def setUpClass(cls):
        img, fg, lens, matte, bg, cls.truth = _temple_scene()
        cls.part = front.partition(img, fg, lens, 140.0, None, matte, bg)

    def test_outline_follows_the_rim_over_the_temple(self):
        P = self.part["arrays"]["lens1_poly"]                                          # viewer's right lens (cx 635)
        at_temple = (P[:, 0] > 635 + 100) & (np.abs(P[:, 1] - 210) <= 25)
        self.assertGreater(int(at_temple.sum()), 5)
        err_t = _edge_error_px(P, self.truth[1], at_temple)
        err_all = _edge_error_px(P, self.truth[1])
        self.assertLess(err_t, 2.0, f"outline {err_t:.2f} px from the rim over the temple")
        self.assertLess(err_all, 1.5, f"outline {err_all:.2f} px from truth overall")
        ref = self.part["refinement"]
        self.assertGreater(ref["registration_moved_points"], 0)

    def test_raw_detector_carries_the_bulge(self):
        P = self.part["arrays"]["lens1_poly_raw"]
        at_temple = (P[:, 0] > 635 + 100) & (np.abs(P[:, 1] - 210) <= 25)
        self.assertGreater(_edge_error_px(P, self.truth[1], at_temple), 8.0)


class LensTintedBand(unittest.TestCase):
    """(b) a dark band between the lens and a neutral brow: lens when it keeps the lens's chromaticity, frame when it
    is neutral (a groove); the edge sits at the lens/frame colour boundary, not at the first luminance step."""

    def _top_edge(self, band_rgb):
        img, fg, lens, matte, bg = _band_scene(band_rgb)
        part = front.partition(img, fg, lens, 140.0, None, matte, bg)
        P = part["arrays"]["lens1_poly"]
        top = P[(P[:, 0] > 350) & (P[:, 0] < 550) & (P[:, 1] < 170)]
        return float(np.median(top[:, 1])), part

    def test_tinted_band_is_lens(self):
        y, part = self._top_edge((34, 70, 60))                                         # dark teal: lens over the brow
        self.assertLess(abs(y - 119.5), 1.5, f"edge at v={y:.2f}, the brow ends at 119.5")
        self.assertGreater(part["refinement"]["band_extended_points"], 0)

    def test_neutral_band_is_frame(self):
        y, part = self._top_edge((30, 30, 30))                                         # neutral groove: frame
        self.assertLess(abs(y - 125.5), 1.5, f"edge at v={y:.2f}, the lens colour ends at 125.5")

    def test_band_rule_off_for_back_sources(self):
        img, fg, lens, matte, bg = _band_scene((34, 70, 60))
        part = front.partition(img, fg, lens, 140.0, None, matte, bg, band_rule=False)
        self.assertEqual(part["refinement"]["band_extended_points"], 0)
        self.assertFalse(part["refinement"]["band_rule"])


class BandHelpers(unittest.TestCase):
    def test_chromaticity_is_darkening_invariant(self):
        rgb = np.array([[[90, 182, 151]]], np.float64)
        lin = (rgb / 255.0) ** 2.2
        q = []
        for k in (1.0, 0.5, 0.2):
            srgb = np.clip((lin * k) ** (1 / 2.2) * 255, 0, 255).round().astype(np.uint8)
            q.append(front.chromaticity(intake.lab_image(srgb))[0, 0])
        self.assertLess(float(np.abs(q[0] - q[1]).max()), 0.03)
        self.assertLess(float(np.abs(q[0] - q[2]).max()), 0.05)

    def test_band_extend_needs_a_tinted_lens_and_a_wide_band(self):
        s = np.arange(-5.0, 3.0 + 1e-9, 0.1)
        def prof(lens, band, frame, b0, b1):
            p = np.tile(np.array(frame, float), (len(s), 1))
            p[s < b1] = band
            p[s < b0] = lens
            return p[None]
        out_sel = (s >= 1.5) & (s <= 2.5)
        inm = np.ones((1, len(s)), bool)
        teal, dteal, black, grey = (68.0, -35.0, 7.0), (27.0, -16.0, 2.0), (12.0, 0.0, -1.0), (33.0, 0.0, 0.0)
        # tinted band 0.6 mm wide, c_in at the luminance step -0.6
        e, ext = front.band_extend(prof(teal, dteal, black, -0.6, 0.0), s, np.array([-0.6]), out_sel, inm, 0.3)
        self.assertAlmostEqual(float(e[0]), 0.0, delta=0.11)
        # a neutral lens (grey) never extends
        e, ext = front.band_extend(prof(grey, (12.0, 0.0, 0.0), (40.0, 20.0, 25.0), -0.6, 0.0), s, np.array([-0.6]), out_sel, inm, 0.3)
        self.assertEqual(float(ext[0]), 0.0)
        # a band narrower than the chroma blur skip does not count
        e, ext = front.band_extend(prof(teal, dteal, black, -0.6, -0.4), s, np.array([-0.6]), out_sel, inm, 0.3)
        self.assertEqual(float(ext[0]), 0.0)


class FrameRegistration(unittest.TestCase):
    def test_register_frame_recovers_a_shifted_structure(self):
        s = np.arange(-5.0, 3.0 + 1e-9, 0.1)
        m = 120
        prof = np.zeros((m, len(s), 3))
        edge = np.full(m, -0.5)
        edge[50:70] = -2.5                                   # a 2 mm deeper edge on an arc (detector bulge)
        for i in range(m):
            p = np.tile([50.0, 2.0, 15.0], (len(s), 1))      # lens
            p[s >= edge[i]] = (8.0, 0.0, -2.0)               # groove line
            p[s >= edge[i] + 0.4] = (18.0, 0.0, -9.0)        # bevel
            p[s >= edge[i] + 1.4] = (37.0, 0.0, -8.0)        # lit rim
            p[(s < edge[i]) & (s > edge[i] - 1.0)] = (33.0, 0.0, 1.0) if 50 <= i < 70 else (50.0, 2.0, 15.0)
            prof[i] = p
        conf = np.ones(m, bool)
        conf[50:70] = False
        e = np.where(conf, edge, np.nan)
        reg = front.register_frame(prof, s, e, conf)
        np.testing.assert_allclose(reg["best"][50:70], -2.5, atol=0.11)
        np.testing.assert_allclose(reg["best"][:40], -0.5, atol=0.11)


class ShieldFreeEdge(unittest.TestCase):
    def test_single_lens_free_bottom(self):
        H, W = 360, 900
        img = np.tile(WHITE, (H, W, 1))
        r = _ellipse_field(H, W, 450, 170, 320, 110)
        shape = (r <= 1.0) & (np.mgrid[0:H, 0:W][0] >= 120)
        img[shape] = (150, 90, 170)                                        # purple shield
        img[100:135, 120:780] = (25, 25, 28)                               # top frame bar
        lens = intake.ndimage.binary_dilation(shape & (np.mgrid[0:H, 0:W][0] >= 135), iterations=3)
        img, fg, matte, bg = _prep(img, lens)
        part = front.partition(img, fg, lens, 140.0, None, matte, bg)
        a = part["arrays"]
        self.assertEqual(part["layout"], "single")
        self.assertEqual(part["lenses"][0]["side"], "C")
        t = a["lens1_type"]
        self.assertGreater(float((t == front.TYPE_FREE).mean()), 0.3)
        self.assertGreater(float((t == front.TYPE_FRAME).mean()), 0.1)
        P = a["lens1_poly"]
        free = P[(t == front.TYPE_FREE) & (P[:, 1] > 200)]
        truth = _ellipse_pts(450, 170, 320, 110)
        truth = truth[truth[:, 1] > 190]
        from scipy.spatial import cKDTree
        d = cKDTree(truth).query(free)[0]
        self.assertLess(float(d.mean()), 1.0, f"free edge {d.mean():.2f} px from truth")


class Rimless(unittest.TestCase):
    def test_ridge_snap_and_class(self):
        H, W = 400, 900
        img = np.tile(WHITE, (H, W, 1))
        lens = np.zeros((H, W), bool)
        truth = []
        for cx in (280.0, 620.0):
            r = _ellipse_field(H, W, cx, 200, 130, 85)
            img[np.abs(r - 1.0) * 85 < 1.2] = (196, 196, 198)             # faint lens-edge ring
            lens |= _ellipse_field(H, W, cx, 200, 140, 95) <= 1.0          # bloated proposal (~1.7 mm)
            truth.append(_ellipse_pts(cx, 200, 130, 85))
        img[190:200, 410:490] = (200, 160, 60)                             # gold bridge
        img[185:215, 138:152] = (200, 160, 60)                             # hinge blocks on the lens
        img[185:215, 748:762] = (200, 160, 60)
        img, fg, matte, bg = _prep(img, lens)
        part = front.partition(img, fg, lens, 140.0, None, matte, bg)
        self.assertEqual(part["layout"], "pair")
        self.assertEqual(part["rim_class"], "rimless")
        self.assertIn("rimless_review", part["flags"])
        d = front.contour_distance(part["arrays"]["lens1_poly"], truth[1])[0]
        d_raw = front.contour_distance(part["arrays"]["lens1_poly_raw"], truth[1])[0]
        self.assertLess(d, 2.0, f"rimless outline {d:.2f} px from the ring")
        self.assertLess(d, d_raw)


TEAL, NAVY = (90, 182, 151), (32, 36, 62)


def _stem_scene(stem_rgb=NAVY):
    """INVU-like shield: a teal lens under a navy brow, a navy nose piece around a nose notch, and (``stem_rgb`` not None)
    a 30 px stem from the brow down to the nose piece. The detector proposal swallows the stem and the nose piece top
    whole (it follows the shield), as on the INVU back photo."""
    H, W = 360, 900
    img = np.tile(WHITE, (H, W, 1))
    yy, xx = np.mgrid[0:H, 0:W]
    shape = (_ellipse_field(H, W, 449.5, 170, 320, 110) <= 1.0) & (yy >= 120)
    notch = (np.abs(xx - 449.5) < 40 + (yy - 214) * 0.35) & (yy >= 214)
    nose = (np.abs(xx - 449.5) < 54 + (yy - 200) * 0.35) & (yy >= 200) & ~notch & shape
    img[shape] = TEAL
    img[92:120, 120:780] = NAVY                                                        # brow
    img[nose] = NAVY                                                                   # nose piece
    if stem_rgb is not None:
        img[120:204, 435:465] = stem_rgb                                               # stem, x 435..464
    img[notch] = WHITE
    lens = ndimage.binary_dilation(shape & ~notch & (yy >= 123), iterations=3)
    img, fg, matte, bg = _prep(img, lens)
    return img, fg, lens, matte, bg


def _vent_scene():
    """Oakley-like shield: teal lens, black brow, and two brow vents (backdrop between the lens's top edge and the brow,
    enclosed by both) that the proposal swallows."""
    H, W = 360, 900
    img = np.tile(WHITE, (H, W, 1))
    yy, xx = np.mgrid[0:H, 0:W]
    shape = (_ellipse_field(H, W, 449.5, 170, 320, 110) <= 1.0) & (yy >= 120)
    img[shape] = TEAL
    img[92:120, 120:780] = (22, 23, 25)
    for cx in (290, 609):                                                              # a mirror pair about 449.5
        img[(np.abs(xx - cx) <= 40) & (yy >= 120) & (yy < 128)] = WHITE
    lens = ndimage.binary_dilation(shape & (yy >= 122), iterations=3)
    img, fg, matte, bg = _prep(img, lens)
    return img, fg, lens, matte, bg


class NonLensCarve(unittest.TestCase):
    """Step 3b/3c: structure the detector swallowed whole is not lens (a stem, a vent), but frame seen THROUGH a tinted
    lens and anything behind a clear lens stays lens."""

    def test_stem_is_frame_and_splits_the_shield(self):
        img, fg, lens, matte, bg = _stem_scene()
        part = front.partition(img, fg, lens, 140.0, None, matte, bg)
        a = part["arrays"]
        self.assertEqual(part["layout"], "pair", "the stem splits the shield")
        self.assertGreaterEqual(part["refinement"]["carve"]["components"], 1)
        self.assertTrue(a["frame_mask"][160, 450], "the stem is frame")
        self.assertEqual(int(a["carve_class"][160, 450]), 2, "exported as carved frame")
        self.assertEqual(int(a["lens_label"][160, 450]), 0)
        P, T = a["lens1_poly"], a["lens1_type"]                                        # viewer's right half
        side = (np.abs(P[:, 1] - 160) < 25) & (P[:, 0] < 520)
        self.assertGreater(int(side.sum()), 10)
        self.assertLess(abs(float(np.median(P[side][:, 0])) - 464.5), 1.5, "R half ends at the stem's edge")
        self.assertTrue((T[side] == front.TYPE_FRAME).all(), "the stem side is frame-bounded")
        x0 = part["axis_x_px"]
        mR = np.stack([2 * x0 - P[:, 0], P[:, 1]], 1)
        self.assertLess(front.contour_distance(mR, a["lens2_poly"])[0], 1e-6, "the halves are exact mirrors")

    def test_no_stem_stays_single(self):
        img, fg, lens, matte, bg = _stem_scene(None)
        part = front.partition(img, fg, lens, 140.0, None, matte, bg)
        self.assertEqual(part["layout"], "single")
        self.assertEqual(part["refinement"]["carve"]["components"], 0)
        self.assertEqual(part["refinement"]["vent_exclusion"]["components"], 0)

    def test_frame_seen_through_the_tinted_lens_stays_lens(self):
        # the same bar seen THROUGH the teal lens: T * navy + r keeps the lens's chromaticity (a dark teal)
        img, fg, lens, matte, bg = _stem_scene((22, 60, 50))
        q_lens = front.chromaticity(intake.lab_image(np.uint8([[TEAL]])))[0, 0]
        q_bar = front.chromaticity(intake.lab_image(np.uint8([[(22, 60, 50)]])))[0, 0]
        self.assertGreater(float(q_bar @ q_lens) / float(q_lens @ q_lens), 0.5, "fixture: the bar is lens-tinted")
        part = front.partition(img, fg, lens, 140.0, None, matte, bg)
        self.assertEqual(part["layout"], "single")
        self.assertEqual(part["refinement"]["carve"]["components"], 0)
        self.assertGreater(int(part["arrays"]["lens_label"][160, 450]), 0)

    def test_vent_stays_a_vent(self):
        img, fg, lens, matte, bg = _vent_scene()
        part = front.partition(img, fg, lens, 140.0, None, matte, bg)
        a = part["arrays"]
        self.assertEqual(part["layout"], "single")
        self.assertEqual(part["refinement"]["vent_exclusion"]["components"], 2)
        for x in (290, 609):
            self.assertEqual(int(a["lens_label"][124, x]), 0, "a vent is not lens")
            self.assertFalse(a["frame_mask"][124, x], "nor frame")
            self.assertFalse(a["fg_sym"][124, x], "nor glasses foreground (the detector proposal covered it)")
            self.assertEqual(int(a["carve_class"][124, x]), 1)
        P, T = a["lens1_poly"], a["lens1_type"]
        under = (np.abs(P[:, 0] - 290) < 30) & (P[:, 1] < 150)
        self.assertGreater(int(under.sum()), 10)
        self.assertLess(abs(float(np.median(P[under][:, 1])) - 127.5), 1.5, "the lens's top edge under the vent")
        self.assertTrue((T[under] == front.TYPE_FREE).all(), "the edge under a vent is free")
        self.assertTrue((T[(np.abs(P[:, 0] - 449.5) < 60) & (P[:, 1] < 150)] == front.TYPE_FRAME).all(),
                        "the brow between the vents stays frame-bounded")

    def test_clear_lens_never_carves(self):
        # a clear lens looks like the backdrop and shows the hardware behind it unchanged: no evidence either way
        H, W = 400, 900
        img = np.tile(WHITE, (H, W, 1))
        lens = np.zeros((H, W), bool)
        for cx in (280.0, 620.0):
            img[np.abs(_ellipse_field(H, W, cx, 200, 130, 85) - 1.0) * 85 < 1.2] = (196, 196, 198)
            lens |= _ellipse_field(H, W, cx, 200, 136, 91) <= 1.0
        img[190:200, 410:490] = (200, 160, 60)
        img[150:250, 170:200] = (200, 160, 60)                                           # hardware across the lens
        img[150:250, 700:730] = (200, 160, 60)
        img, fg, matte, bg = _prep(img, lens)
        part = front.partition(img, fg, lens, 140.0, None, matte, bg)
        self.assertEqual(part["refinement"]["carve"]["components"], 0)
        self.assertEqual(part["refinement"]["vent_exclusion"]["components"], 0)

    def test_temple_seen_through_the_lens_is_not_carved(self):
        img, fg, lens, matte, bg, _ = _temple_scene()
        part = front.partition(img, fg, lens, 140.0, None, matte, bg)
        self.assertEqual(part["refinement"]["carve"]["components"], 0)
        self.assertEqual(part["refinement"]["vent_exclusion"]["components"], 0)

    def test_back_source_carve_needs_the_front_photo(self):
        # source = back-mirrored photo; two carve groups: a stem on the axis and an end piece (+ its mirror twin). The
        # front photo shows frame at the stem and LENS at the ends (the end piece sits behind the lens): only the stem
        # is confirmed, and a mirror pair is decided together
        H, W = 120, 200
        C = np.zeros((H, W), bool)
        C[30:80, 95:105] = True                                                        # stem about x = 99.5
        C[50:70, 20:30] = True                                                         # end piece, R
        C[50:70, 169:179] = True                                                       # its mirror twin, L
        cls = np.where(C, 2, 0).astype(np.int8)
        lab_f = np.zeros((H, W, 3))
        lab_f[...] = (84.0, -9.0, -16.0)                                               # light blue lens
        lab_f[30:80, 95:105] = (74.0, -28.0, -29.0)                                    # cyan stem (frame)
        lens_f = np.zeros((H, W), bool)
        lens_f[20:100, 5:195] = True
        conf = {"lab": lab_f, "lens": lens_f, "M": np.array([[1.0, 0, 0], [0, 1.0, 0]]), "mm_px": 0.8}
        Ck, clsk, info = front.front_confirm(C, cls, conf, 99.5)
        self.assertEqual(info["groups"], 2)
        self.assertTrue(Ck[30:80, 95:105].all(), "the stem is confirmed by the front photo")
        self.assertFalse(Ck[50:70, 20:30].any() or Ck[50:70, 169:179].any(), "the end pieces are behind the lens")
        self.assertTrue((clsk[Ck] == 2).all() and not clsk[~Ck].any())
        same, _, info0 = front.front_confirm(C, cls, None, 99.5)                        # front source: unchanged
        np.testing.assert_array_equal(same, C)
        self.assertEqual(info0["groups"], 0)

    def test_back_source_opening_stays_with_its_confirmed_carve(self):
        # an opening (class 1) continues beyond the carve (its part outside the proposal is only marked): it stays with
        # a carve the front photo confirms and goes with one it does not
        H, W = 120, 200
        C = np.zeros((H, W), bool)
        cls = np.zeros((H, W), np.int8)
        C[40:50, 60:80] = True                                                         # vent inside the proposal (R)
        cls[40:50, 60:80] = 1
        cls[30:40, 60:80] = 1                                                          # ... continuing outside R
        C[40:50, 119:139] = True                                                       # its mirror twin about 99.5
        cls[40:50, 119:139] = 1
        cls[30:40, 119:139] = 1
        lab_f = np.zeros((H, W, 3))
        lab_f[...] = (84.0, -9.0, -16.0)                                               # light blue lens
        lab_f[40:50, 60:80] = lab_f[40:50, 119:139] = (96.0, 0.0, 0.0)                 # the front photo shows backdrop
        lens_f = np.zeros((H, W), bool)
        lens_f[20:100, 5:195] = True
        conf = {"lab": lab_f, "lens": lens_f, "M": np.array([[1.0, 0, 0], [0, 1.0, 0]]), "mm_px": 0.8}
        Ck, clsk, info = front.front_confirm(C, cls, conf, 99.5)
        self.assertTrue(Ck[40:50, 60:80].all())
        self.assertTrue((clsk[30:50, 60:80] == 1).all(), "the opening's part outside the carve stays class 1")
        lab_f[40:50, 60:80] = lab_f[40:50, 119:139] = (84.0, -9.0, -16.0)             # now the front shows lens there
        Ck2, clsk2, _ = front.front_confirm(C, cls, conf, 99.5)
        self.assertFalse(Ck2.any())
        self.assertFalse(clsk2.any(), "an opening goes with its dropped carve")

    def test_carve_types(self):
        cls = np.zeros((60, 100), np.int8)
        cls[5:15, 40:60] = 1                                                           # vent above the top edge
        cls[42:52, 20:30] = 2                                                          # frame below the left
        P = front.canonical_order(_ellipse_pts(50.0, 30.0, 35.0, 12.0, 400))
        t0 = np.full(len(P), front.TYPE_RIMLESS, np.int8)
        t, k = front.carve_types(P, t0, cls, 49.5, 6.0)
        top = (np.abs(P[:, 0] - 50) < 5) & (P[:, 1] < 30)
        self.assertTrue((t[top] == front.TYPE_FREE).all())
        self.assertGreater(k, 0)
        self.assertTrue((t[(np.abs(P[:, 0] - 25) < 2) & (P[:, 1] > 30)] == front.TYPE_FRAME).all())
        mirrored = (np.abs(P[:, 0] - 74) < 2) & (P[:, 1] > 30)                         # the frame carve's mirror twin
        self.assertTrue((t[mirrored] == front.TYPE_FRAME).all())
        self.assertTrue((t[(np.abs(P[:, 1] - 30) < 3) & (P[:, 0] < 20)] == front.TYPE_RIMLESS).all(), "elsewhere kept")
        t2, k2 = front.carve_types(P, t0, np.zeros_like(cls), 49.5, 6.0)
        self.assertEqual(k2, 0)
        np.testing.assert_array_equal(t2, t0)


class Geometry(unittest.TestCase):
    def test_normals_point_outward(self):
        P = front.canonical_order(_ellipse_pts(100, 100, 50, 30, 400))
        c, n = front.normals(P)
        self.assertTrue(np.all(((c - [100, 100]) * n).sum(1) > 0))

    def test_canonical_order_and_fourier(self):
        P = _ellipse_pts(0, 0, 40, 40, 300)[::-1]
        Q = front.canonical_order(P)
        self.assertGreater(front.signed_area(Q), 0)
        self.assertEqual(int(np.argmin(Q[:, 1])), 0)
        F = front.fourier_outline(Q, 24, 512)
        np.testing.assert_allclose(np.hypot(F[:, 0], F[:, 1]), 40.0, atol=0.05)

    def test_dispute_resolution(self):
        H, W = 120, 200
        lab = np.zeros((H, W, 3), np.float32)
        lab[..., 0] = 90.0                                                 # backdrop / frame
        L = np.zeros((H, W), bool)
        L[30:90, 20:80] = True
        L[30:90, 120:180] = True                                           # mirror about x = 99.5
        lab[L] = (40.0, 0.0, 0.0)                                          # lens colour
        Ld = L.copy()
        Ld[30:45, 20:35] = False                                           # logo notch on one lens
        lab[30:45, 20:35] = (98.0, 0.0, 0.0)
        Ld[90:96, 130:170] = True                                          # over-inclusion into frame
        R = front.resolve_mirror_dispute(Ld, 99.5, lab, 0.2)
        self.assertTrue(R[30:45, 20:35].all(), "notch restored from the clean twin")
        self.assertFalse(R[90:96, 130:170].any(), "frame over-inclusion dropped")
        np.testing.assert_array_equal(R, front.mirror_field(R.astype(np.float32), 99.5, 0) > 0.5)

    def test_register_similarity(self):
        H, W = 300, 400
        A = _ellipse_field(H, W, 150, 120, 60, 40) <= 1
        B = _ellipse_field(H, W, 150 * 1.3 + 12, 120 * 1.3 - 8, 60 * 1.3, 40 * 1.3) <= 1
        M, v = front.register_similarity(A, B)
        self.assertAlmostEqual(M[0, 0], 1.3, delta=0.02)
        self.assertAlmostEqual(M[0, 2], 12, delta=2.5)
        self.assertAlmostEqual(M[1, 2], -8, delta=2.5)
        self.assertGreater(v, 0.97)

    def test_rim_class_rules(self):
        W = 1000.0
        m = 400
        self.assertEqual(front.rim_class_of(np.full(m, 30.0), W)[0], "full")
        w = np.zeros(m)
        w[:20] = 30.0
        self.assertEqual(front.rim_class_of(w, W)[0], "rimless")
        w = np.zeros(m)
        w[:120] = 30.0
        w[-100:] = 30.0                                                    # one arc through the top vertex
        self.assertEqual(front.rim_class_of(w, W)[0], "half")
        w = np.zeros(m)
        w[:80] = 30.0
        w[200:320] = 30.0                                                  # two arcs
        self.assertEqual(front.rim_class_of(w, W)[0], "mixed")


# INVU is a pair since the non-lens carve (step 3b): the centre stem (frame in both photos) splits its shield
EXPECTED = {"rayban": ("pair", "full"), "vb": ("pair", "full"), "oakley": ("single", None),
            "invu": ("pair", None), "miu": ("pair", "rimless")}


@unittest.skipUnless(core.PRODUCTS["vb"].photo_path("front").exists(), "product photos not present")
class RealDataSmoke(unittest.TestCase):
    def test_m1_artifacts(self):
        for p, (layout, rim) in EXPECTED.items():
            sd = core.stage_dir("m1", p, front.STAGE)
            if not sd.done():
                self.skipTest("m1 s2_front not run")
            r, a = sd.load()
            self.assertEqual(r["layout"], layout, p)
            if rim:
                self.assertEqual(r["rim_class"], rim, p)
            n = len(r["lenses"])
            self.assertEqual(n, 2 if layout == "pair" else 1)
            self.assertEqual(a["frame_mask"].dtype, bool)
            self.assertEqual(a["fg_sym"].dtype, bool)
            self.assertEqual(a["lens_label"].dtype, np.int8)
            self.assertEqual(int(a["lens_label"].max()), n)
            for i in range(1, n + 1):
                P, T, Wr = a[f"lens{i}_poly"], a[f"lens{i}_type"], a[f"lens{i}_rimw_px"]
                self.assertEqual(P.dtype, np.float64)
                self.assertEqual(P.shape[1], 2)
                self.assertEqual(len(T), len(P))
                self.assertEqual(len(Wr), len(P))
                self.assertTrue(set(np.unique(T).tolist()) <= {0, 1, 2})
                self.assertGreater(front.signed_area(P), 0)
                self.assertIn(f"lens{i}_poly_front", a)
            for k in ("outline_source", "axis_x_px", "width_px", "layout", "rim_class", "lenses", "refinement", "flags"):
                self.assertIn(k, r)
            self.assertIn("median_bevel_offset_mm", r["refinement"])
            self.assertIn("low_contrast_share", r["refinement"])
        r, _ = core.stage_dir("m1", "invu", front.STAGE).load()
        self.assertEqual(r["outline_source"], "back_mirrored")
        self.assertFalse(r["refinement"].get("band_rule", False), "no lens-tinted band rule on a back-photo source")
        r, a = core.stage_dir("m1", "invu", front.STAGE).load()
        x0 = int(round(r["axis_x_px"]))
        self.assertFalse((a["lens_label"][:, x0] > 0).any(), "invu: the centre stem is frame, not lens")
        self.assertGreater(int(a["frame_mask"][:, x0].sum()), 0)
        r, _ = core.stage_dir("m1", "oakley", front.STAGE).load()
        self.assertGreaterEqual(r["refinement"]["vent_exclusion"]["components"], 2, "oakley: the brow vents stay vents")
        for p in ("rayban", "vb", "miu"):
            r, _ = core.stage_dir("m1", p, front.STAGE).load()
            self.assertEqual(r["refinement"]["carve"]["components"], 0, p)
            self.assertEqual(r["refinement"]["vent_exclusion"]["components"], 0, p)
        r, _ = core.stage_dir("m1", "miu", front.STAGE).load()
        self.assertIn("rimless_review", r["flags"])
        for p in EXPECTED:
            r, _ = core.stage_dir("m1", p, front.STAGE).load()
            # the raw 2D front/back distance is parallax, not an error: S2 reports it but never flags it
            self.assertNotIn("front_back_disagree", r["flags"], p)
            for k in ("registration_moved_points", "band_extended_points", "unmeasured_share_of_frame_points"):
                self.assertIn(k, r["refinement"], p)

    def test_partition_is_deterministic(self):
        sd = core.stage_dir("m1", "vb", intake.STAGE)
        if not sd.done():
            self.skipTest("m1 intake not run")
        s0, a0 = sd.load()
        rgb = core.load_photo(core.PRODUCTS["vb"], "front")
        v = s0["views"]["front"]
        bg = intake.backdrop_lab(np.asarray(v["backdrop_lab_coef"]), v["shape"])
        args = (rgb, a0["fg_front"], a0["lens_front"], 140.0, None, a0["matte_front"], bg)
        p1 = front.partition(*args)
        p2 = front.partition(*args)
        for k in p1["arrays"]:
            np.testing.assert_array_equal(p1["arrays"][k], p2["arrays"][k])
        self.assertEqual(p1["layout"], "pair")


if __name__ == "__main__":
    unittest.main()
