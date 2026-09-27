"""S4 depth: synthetic fixtures for the maths (splines, robust fits, wrap, lens clamp, exact lifting) plus a
real-data smoke test on the saved m1 artifacts (skips when they are missing)."""
import json
import math
import unittest

import numpy as np

from bsa import core, depth, raster
from bsa.core import NormFrame

FRAME = NormFrame((0.0, 0.0, 0.0), 140.0)


def _cam(yaw=0.0, pitch=10.0, persp=0.2, ppm=8.0, shape=(800, 1200)):
    return raster.view_camera(FRAME, yaw, pitch, ppm, shape, perspective=persp)


class SplineAndFits(unittest.TestCase):
    def test_design_matches_eval(self):
        sp = depth.BSpline2D.covering(-40, 40, -20, 20, 8.0)
        rng = np.random.default_rng(0)
        c = rng.normal(size=sp.size)
        s, y = rng.uniform(-50, 50, 500), rng.uniform(-30, 30, 500)
        np.testing.assert_allclose(sp.design(s, y) @ c, sp.eval(c, s, y), atol=1e-10)

    def test_partition_of_unity(self):
        sp = depth.BSpline2D.covering(-40, 40, -20, 20, 8.0)
        s, y = np.linspace(-40, 40, 101), np.linspace(-20, 20, 101)
        np.testing.assert_allclose(sp.eval(np.ones(sp.size), s, y), 1.0, atol=1e-12)

    def test_fit_field_smooth_and_extrapolates(self):
        xs, ys = np.meshgrid(np.arange(-45, 45, 0.5), np.arange(-20, 20, 0.5))
        s, y = xs.ravel(), ys.ravel()
        f = 60 - 0.004 * s ** 2 + 0.02 * y + 0.8 * np.exp(-((s - 10) ** 2 + y ** 2) / (2 * 12.0 ** 2))
        field, r = depth.fit_field(s, y, f, 0.5, 0.3, (s.min(), s.max(), y.min(), y.max()))
        self.assertLess(float(np.sqrt(np.mean(r ** 2))), 0.03)
        # 25 mm beyond coverage: finite and close to the low-order continuation (the residual decays)
        far = float(field(np.array([70.0]), np.array([0.0]))[0])
        low = float(field.low_only(np.array([70.0]), np.array([0.0]))[0])
        truth = 60 - 0.004 * 70 ** 2
        self.assertTrue(math.isfinite(far))
        self.assertLess(abs(far - low), 0.5)
        self.assertLess(abs(far - truth), 2.0)

    def test_robust_poly_rejects_outliers(self):
        rng = np.random.default_rng(1)
        s, y = rng.uniform(-30, 30, 4000), rng.uniform(-20, 20, 4000)
        d = 5.0 + 0.1 * s - 0.05 * y
        bad = rng.random(4000) < 0.2
        d_obs = d + np.where(bad, rng.uniform(3, 10, 4000), rng.normal(0, 0.02, 4000))
        poly = depth.Poly2D(((0, 0), (1, 0), (0, 1)), 0.0, 0.0, 30.0)
        c, _ = depth.robust_poly(poly, s, y, d_obs, 0.2)
        c_ls = np.linalg.lstsq(poly.design(s, y), d_obs, rcond=None)[0]
        err = float(np.max(np.abs(poly.eval(c, s, y) - d)))
        err_ls = float(np.max(np.abs(poly.eval(c_ls, s, y) - d)))
        # soft-L1 is not redescending: 20 % one-sided outliers leave a small bias, far below least squares'
        self.assertLess(err, 0.1)
        self.assertLess(err, 0.1 * err_ls)

    def test_front_component_keeps_largest_continuous(self):
        hit = np.zeros((40, 60), bool)
        hit[5:35, 5:40] = True
        hit[10:20, 45:55] = True
        d = np.zeros(hit.shape)
        d[:, 42:] = -12.0                       # a temple 12 mm behind
        hit[20, 40:45] = True                   # bridged by a steep strip (depth jump > 3 mm)
        d[20, 40:45] = np.linspace(0, -12, 5)
        keep = depth.front_component(hit, d)
        self.assertTrue(keep[20, 20])
        self.assertFalse(keep[15, 50])

    def test_fit_wrap_recovers_radius(self):
        x, y = np.meshgrid(np.linspace(-60, 60, 121), np.linspace(-20, 20, 41))
        z = 70 - x ** 2 / (2 * 60.0) + 0.05 * y
        w = depth.fit_wrap(x.ravel(), y.ravel(), z.ravel())
        self.assertAlmostEqual(w["R_mm"], 60.0, delta=0.6)
        self.assertAlmostEqual(w["xc_mm"], 0.0, delta=0.1)

    def test_base_roundtrip(self):
        b = depth.Base("cylinder", 60.0, 1.0, -5.0)
        rng = np.random.default_rng(2)
        s, y, d = rng.uniform(-80, 80, 100), rng.uniform(-20, 20, 100), rng.uniform(-5, 5, 100)
        s2, y2, d2 = b.to_param(b.from_param(s, y, d))
        np.testing.assert_allclose(np.c_[s2, y2, d2], np.c_[s, y, d], atol=1e-9)


class LensClamp(unittest.TestCase):
    def _grid(self):
        x, y = np.meshgrid(np.arange(-20, 20, 0.25), np.arange(-15, 15, 0.25))
        return x.ravel(), y.ravel()

    def test_convex_sphere_is_quartic(self):
        s, y = self._grid()
        d = 60 - (s ** 2 + y ** 2) / (2 * 100.0)
        _, _, info = depth.fit_lens(depth.Base(), s, y, d, (0.0, 0.0), 62.0)
        self.assertEqual(info["model"], "quartic")
        self.assertAlmostEqual(info["radius_s_mm"], 100.0, delta=5.0)
        self.assertAlmostEqual(info["radius_y_mm"], 100.0, delta=5.0)

    def test_concave_is_clamped_to_base4(self):
        s, y = self._grid()
        d = 60 - s ** 2 / (2 * 300.0) + y ** 2 / (2 * 225.0)     # concave vertically (R = +225)
        poly, coef, info = depth.fit_lens(depth.Base(), s, y, d, (0.0, 0.0), 62.0)
        self.assertEqual(info["model"], "quadratic_clamped")
        self.assertEqual(info["clamp"]["y"], "concave->base4")
        self.assertAlmostEqual(info["radius_y_mm"], depth.BASE4_R_MM, places=3)
        dyy = poly.second(coef, 0.0, 0.0)[1]
        self.assertAlmostEqual(float(-1 / dyy), depth.BASE4_R_MM, delta=0.01)

    def test_too_tight_is_clamped_to_50(self):
        s, y = self._grid()
        d = 60 - s ** 2 / (2 * 30.0) - y ** 2 / (2 * 200.0)
        _, _, info = depth.fit_lens(depth.Base(), s, y, d, (0.0, 0.0), 62.0)
        self.assertEqual(info["clamp"]["s"], "tight->R50")
        self.assertAlmostEqual(info["radius_s_mm"], 50.0, places=3)

    def test_few_hits_base_curve_sphere(self):
        s, y = self._grid()
        d = 60 - (s ** 2 + y ** 2) / (2 * 100.0)
        _, _, info = depth.fit_lens(depth.Base(), s[:500], y[:500], d[:500], (0.0, 0.0), 62.0)
        self.assertEqual(info["model"], "base_curve_sphere")

    def test_cylinder_base_curvature_convention(self):
        b = depth.Base("cylinder", 60.0, 0.0, 0.0)
        self.assertAlmostEqual(float(b.curvature_s(0.0, 0.0)), 1 / 60.0, places=12)
        self.assertAlmostEqual(float(b.curvature_s(b.d_ss_for(1 / 132.5, 2.0), 2.0)), 1 / 132.5, places=12)


class Lifting(unittest.TestCase):
    def test_lift_is_exact_for_perspective_camera(self):
        cam = _cam()
        surf = depth.ParamSurface(depth.Base(), lambda x, y: 60 - 0.002 * np.asarray(x) ** 2 - 0.001 * np.asarray(y) ** 2)
        u, v = np.meshgrid(np.linspace(300, 900, 25), np.linspace(250, 550, 13))
        px = np.c_[u.ravel(), v.ravel()]
        P = depth.lift_px(px, cam, FRAME, surf)
        self.assertTrue(np.all(np.isfinite(P)))
        np.testing.assert_allclose(core.project_mm(P, cam, FRAME), px, atol=1e-5)
        np.testing.assert_allclose(P[:, 2], 60 - 0.002 * P[:, 0] ** 2 - 0.001 * P[:, 1] ** 2, atol=1e-5)

    def test_height_field_callable_accepted(self):
        cam = _cam(pitch=0.0, persp=0.0)
        P = depth.lift_px(np.array([[600.0, 400.0]]), cam, FRAME, lambda x, y: np.full(np.shape(x), 12.5))
        self.assertAlmostEqual(float(P[0, 2]), 12.5, places=5)

    def test_front_most_crossing_on_cylinder(self):
        cam = _cam(pitch=0.0, persp=0.0)
        surf = depth.ParamSurface(depth.Base("cylinder", 60.0, 0.0, 0.0), lambda s, y: np.zeros(np.shape(s)),
                                  (-0.75 * math.pi * 60, 0.75 * math.pi * 60),
                                  ((-200, 200), (-100, 100), (-100, 100)))
        px = np.c_[np.linspace(600 - 8 * 55, 600 + 8 * 55, 23), np.full(23, 400.0)]
        P = depth.lift_px(px, cam, FRAME, surf)
        np.testing.assert_allclose(np.hypot(P[:, 0], P[:, 2]), 60.0, atol=1e-5)
        self.assertTrue(np.all(P[:, 2] > 0))                   # the FRONT crossing, not the back one

    def test_mirrored_back_camera_equals_front_camera(self):
        shape = (600, 1000)
        front = raster.view_camera(FRAME, 0.0, 0.0, 6.0, shape)
        back = raster.view_camera(FRAME, 180.0, 0.0, 6.0, shape)
        surf = depth.ParamSurface(depth.Base(), lambda x, y: 30 - 0.003 * np.asarray(x) ** 2)
        px = np.c_[np.linspace(200, 800, 11), np.linspace(200, 400, 11)]
        Pf = depth.lift_px(px, front, FRAME, surf)
        Pb = depth.lift_px(px, back, FRAME, surf, mirror_width=shape[1])
        np.testing.assert_allclose(Pb, Pf, atol=1e-5)
        sv = depth.SourceView("back", back, shape, True)
        np.testing.assert_allclose(sv.project(Pb, FRAME), px, atol=1e-6)

    def test_closest_on_rays_grazing(self):
        surf = depth.ParamSurface(depth.Base("cylinder", 60.0, 0.0, 0.0), lambda s, y: np.zeros(np.shape(s)),
                                  (-0.75 * math.pi * 60, 0.75 * math.pi * 60), ((-200, 200), (-100, 100), (-100, 100)))
        O = np.array([[61.0, 0.0, 150.0]])
        D = np.array([[0.0, 0.0, -1.0]])
        P, _ = depth.intersect_rays(surf, O, D)
        self.assertTrue(np.isnan(P).all())
        Q, g = depth.closest_on_rays(surf, O, D)
        self.assertAlmostEqual(float(Q[0, 2]), 0.0, delta=0.05)
        self.assertAlmostEqual(float(g[0]), 1.0, delta=1e-3)


class StrokeWidth(unittest.TestCase):
    def test_local_thickness_of_a_bar(self):
        m = np.zeros((80, 200), bool)
        m[20:40, 10:190] = True
        t = depth.local_thickness_px(m)
        self.assertGreaterEqual(t[30, 100], 18.0)
        self.assertLessEqual(t[30, 100], 20.0)

    def test_short_narrowing_is_not_a_wire(self):
        m = np.zeros((120, 400), bool)
        m[40:70, 20:380] = True                   # 3 mm acetate bar at 10 px/mm
        m[40:55, 190:210] = False                 # a 2 mm-long narrowing to 1.5 mm
        m[100:110, 20:380] = True                 # a 1 mm wire, 36 mm long
        w = depth.stroke_width_mm(m, 10.0)
        self.assertGreater(w[62, 200], depth.METAL_STROKE_MM)
        self.assertLess(w[105, 200], depth.METAL_STROKE_MM)


@unittest.skipUnless((core.BSA_DATA / "runs" / "m1").exists(), "no m1 artifacts")
class RealData(unittest.TestCase):
    def test_saved_fields(self):
        seen = 0
        for p in core.PRODUCTS:
            sd = core.stage_dir("m1", p, depth.STAGE)
            if not sd.done():
                continue
            seen += 1
            res, arr = sd.load()
            for k in ("grid_x", "grid_y", "z_front", "thickness", "valid", "front_low_coef", "front_spline_coef"):
                self.assertIn(k, arr, (p, k))
            self.assertEqual(arr["z_front"].shape, (len(arr["grid_y"]), len(arr["grid_x"])))
            self.assertIn(res["wrap"]["kind"], ("planar", "cylinder"))
            if res["wrap"]["kind"] == "cylinder":
                self.assertLess(res["wrap"]["radius_mm"], depth.CYLINDER_MAX_R_MM)
            df = depth.load_depth(p)
            src = depth.source_view(p)
            s2, _ = core.stage_dir("m1", p, "s2_front").load()
            s1 = json.loads(core.stage_dir("m1", p, "s1_generator").result_path.read_text())
            frame = NormFrame.from_dict(s1["frame"])
            for i, lens in enumerate(s2["lenses"], start=1):
                c = np.asarray(lens["centroid_px"], float)[None]
                P = depth.lift_px(c, src.camera, frame, df.lens_surface(i), mirror_width=src.mirror_width)
                self.assertTrue(np.all(np.isfinite(P)), p)
                np.testing.assert_allclose(src.project(P, frame), c, atol=1e-3)
                Pf = depth.lift_px(c, src.camera, frame, df.front_surface(), mirror_width=src.mirror_width)
                t = float(df.thickness_at(Pf)[0])
                self.assertTrue(depth.METAL_T_MM[0] <= t <= depth.ACETATE_T_MM[1], (p, t))
                z = float(df.z(Pf[:, 0], Pf[:, 1])[0])
                self.assertAlmostEqual(z, float(Pf[0, 2]), delta=1e-3)
                # the plate back (S5 donors start there, S6 back caps lie there) = front - thickness
                s_, y_, d_ = df.base.to_param(Pf)
                self.assertAlmostEqual(float(df.plate_back_d(s_, y_)[0]), float(d_[0]) - t, delta=1e-3)
                Pb = df.base.from_param(s_, y_, df.plate_back_d(s_, y_))
                self.assertAlmostEqual(float(df.plate_back_surface().signed(Pb)[0]), 0.0, delta=1e-6)
                self.assertTrue(np.isfinite(df.lens_z(i, Pf[:, 0], Pf[:, 1])).all())
        if not seen:
            self.skipTest("no s4 artifacts")


if __name__ == "__main__":
    unittest.main()
