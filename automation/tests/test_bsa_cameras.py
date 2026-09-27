"""bsa.cameras (S3): parametrisation, loss, sign conventions, synthetic recovery, real-run smoke."""
from __future__ import annotations

import json
import unittest

import numpy as np
import open3d as o3d

from bsa import cameras as C
from bsa import raster
from bsa.core import VIEWS, NormFrame, camera_from_dict, project_mm, px_per_mm, stage_dir
from reconstruction.camera import Camera


def _box(x0, x1, y0, y1, z0, z1):
    m = o3d.geometry.TriangleMesh.create_box(x1 - x0, y1 - y0, z1 - z0)
    m.translate((x0, y0, z0))
    return np.asarray(m.vertices), np.asarray(m.triangles)


def _glasses():
    """Mirror-symmetric toy glasses in model mm: two rims + bridge at the front (+Z), temples going back
    with a downward tip, so front/back and left/right are distinguishable by silhouette."""
    parts = []
    for sx in (-1.0, 1.0):
        t = o3d.geometry.TriangleMesh.create_torus(torus_radius=24, tube_radius=3, radial_resolution=40,
                                                   tubular_resolution=10)
        # open3d's torus already lies in the XY plane (axis +Z): the rim faces the front camera
        t.translate((sx * 36.0, 0.0, 60.0))
        parts.append((np.asarray(t.vertices), np.asarray(t.triangles)))
        parts.append(_box(sx * 62.0 - 4 if sx > 0 else -66.0, sx * 62.0 + 4 if sx > 0 else -58.0, 8, 16, 52, 64))
        parts.append(_box(sx * 64.0 - 2.5, sx * 64.0 + 2.5, 9, 15, -60, 55))        # temple
        parts.append(_box(sx * 64.0 - 2.5, sx * 64.0 + 2.5, -18, 15, -70, -60))     # tip drop
    parts.append(_box(-12, 12, 12, 18, 56, 64))                                      # bridge
    V, F, off = [], [], 0
    for v, f in parts:
        V.append(v)
        F.append(f + off)
        off += len(v)
    V = np.concatenate(V).astype(np.float64)
    F = np.concatenate(F).astype(np.int32)
    lo, hi = V.min(0), V.max(0)
    return V, F, NormFrame(tuple((lo + hi) / 2), float((hi - lo).max()))


def _truth(frame, yaw, pitch, roll, persp, ppm, shape, shift=(0.0, 0.0)):
    cam = raster.view_camera(frame, yaw, pitch, ppm, shape, roll=roll, perspective=persp)
    return Camera(cam.yaw, cam.pitch, cam.roll, cam.perspective, cam.scale, cam.center_x + shift[0],
                  cam.center_y + shift[1])


class Parametrisation(unittest.TestCase):
    def test_pack_unpack_round_trip(self):
        cam = Camera(37.5, -12.25, 3.5, 0.17, 812.3, 455.2, -31.7)
        back = C.unpack(C.pack(cam, 997.0), 997.0)
        for a, b in zip(cam.to_dict().values(), back.to_dict().values()):
            self.assertAlmostEqual(a, b, places=9)

    def test_pivot_parametrisation_round_trip_and_decoupling(self):
        """With a pivot, changing yaw/pitch/perspective in the optimizer vector keeps the pivot's image
        position and its local magnification fixed (the decoupling Powell relies on)."""
        V, F, frame = _glasses()
        pivot = frame.to_norm(np.array([[0.0, 3.0, 55.0]]))[0]
        cam = Camera(-4.0, 11.0, 2.0, 0.2, 640.0, 300.0, 210.0)
        x = C.pack(cam, 480.0, pivot)
        back = C.unpack(x, 480.0, pivot)
        for a, b in zip(cam.to_dict().values(), back.to_dict().values()):
            self.assertAlmostEqual(a, b, places=8)
        P = frame.to_mm(pivot.reshape(1, 3))[0]
        uv0 = project_mm(P[None], cam, frame)[0]
        m0 = C.px_per_mm_at(cam, frame, P)
        for k, d in ((0, 5.0), (1, -7.0), (3, 1.5)):
            y = x.copy()
            y[k] += d
            c2 = C.unpack(y, 480.0, pivot)
            np.testing.assert_allclose(project_mm(P[None], c2, frame)[0], uv0, atol=1e-8)
            self.assertAlmostEqual(C.px_per_mm_at(c2, frame, P), m0, places=8)

    def test_resized_camera_keeps_pixel_centres(self):
        """Native pixel centre u maps to k (u - x0 + 0.5) - 0.5 in a crop resized by k."""
        V, F, frame = _glasses()
        cam = _truth(frame, 30, 15, 4, 0.2, 5.0, (700, 900))
        k, x0, y0 = 0.37, 120.0, 45.0
        a = project_mm(V[::50], cam, frame)
        b = project_mm(V[::50], C.resized_camera(cam, k, x0, y0), frame)
        np.testing.assert_allclose(b[:, 0], k * (a[:, 0] - x0 + 0.5) - 0.5, atol=1e-9)
        np.testing.assert_allclose(b[:, 1], k * (a[:, 1] - y0 + 0.5) - 0.5, atol=1e-9)

    def test_px_per_mm_at_matches_numeric_magnification(self):
        V, F, frame = _glasses()
        for persp in (0.0, 0.3):
            cam = _truth(frame, 0, 0, 0, persp, 4.0, (600, 800))
            P = np.array([[0.0, 5.0, 60.0]])
            h = 1e-3
            du = (project_mm(P + [h, 0, 0], cam, frame) - project_mm(P - [h, 0, 0], cam, frame))[0, 0] / (2 * h)
            self.assertAlmostEqual(C.px_per_mm_at(cam, frame, P[0]), du, places=6)
            if persp == 0:
                self.assertAlmostEqual(C.px_per_mm_at(cam, frame, P[0]), px_per_mm(cam, frame), places=9)


class LossAndMetrics(unittest.TestCase):
    def setUp(self):
        raster.clear_cache()
        self.V, self.F, self.frame = _glasses()
        self.scene = raster.get_scene(self.V, self.F, self.frame)
        self.shape = (360, 480)
        self.cam = _truth(self.frame, 0, 8, 0, 0.1, 2.6, self.shape)
        self.fg = self.scene.render(self.cam, self.shape)["mask"]

    def test_true_camera_scores_best(self):
        lv = C.Level(self.fg, 256)
        l0 = lv.loss(self.scene, self.cam)
        self.assertLess(l0, 0.02)
        for d in ({"yaw": 4.0}, {"scale": self.cam.scale * 1.03}, {"center_x": self.cam.center_x + 4}):
            c = Camera(**{**self.cam.to_dict(), **d})
            self.assertGreater(lv.loss(self.scene, c), l0 + 0.005, d)

    def test_identical_masks_give_perfect_score(self):
        lv = C.Level(self.fg, 10_000)        # stride 1
        loss, iou, bd = lv.score(raster.downsample_mask(self.fg, lv.stride, lv.roi) >= 0.5)
        self.assertEqual(iou, 1.0)
        self.assertEqual(bd, 0.0)
        self.assertEqual(loss, 0.0)

    def test_optional_pixels_explain_but_are_not_penalised(self):
        """Optional (lens) render pixels over background cost nothing; over foreground they count as
        explained; they can never remove unexplained photo pixels from the score."""
        lv = C.Level(self.fg, 10_000)
        ref = lv.ref >= 0.5
        base = lv.score(ref)[1]
        extra = np.zeros_like(ref)
        ys, xs = np.nonzero(ref)
        extra[ys.min():ys.min() + 20, :] = True           # a band over photo and background
        grown = ref | extra
        self.assertLess(lv.score(grown)[1], base)          # hard pixels over background are penalised
        self.assertAlmostEqual(lv.score(grown, optional=extra & ~ref)[1], base, places=12)
        hole = ref.copy()
        hole[ys.min():ys.min() + 20, :] = False            # render misses part of the photo ...
        opt = np.zeros_like(ref)
        opt[ys.min():ys.min() + 20, :] = True              # ... and an optional band covers that part
        self.assertAlmostEqual(lv.score(hole | opt, optional=opt)[1], base, places=12)
        self.assertLess(lv.score(hole, optional=opt & hole)[1], base)   # nothing there: still penalised

    def test_contour_stats_of_a_shift(self):
        a = np.zeros((200, 200), bool)
        a[50:150, 50:150] = True
        b = np.roll(a, 3, axis=1)
        mean, p95 = C.contour_stats(a, b)
        self.assertGreater(mean, 0.5)
        self.assertLessEqual(mean, 3.0)
        self.assertAlmostEqual(p95, 3.0, places=5)

    def test_render_outside_roi_is_penalised(self):
        lv = C.Level(self.fg, 256)
        far = Camera(**{**self.cam.to_dict(), "center_x": self.cam.center_x + 5000})
        self.assertGreaterEqual(lv.outside_share(self.scene, far), 0.999)
        self.assertEqual(lv.outside_share(self.scene, self.cam), 0.0)


class Seeds(unittest.TestCase):
    def test_angled_never_seeds_other_views(self):
        winners = {"front": Camera(1, 5, 0, .1, 1, 0, 0), "left": Camera(88, 3, 1, .1, 1, 0, 0),
                   "angled": Camera(40, 20, 3, .1, 1, 0, 0)}
        for target in VIEWS:
            for *_, origin in C.cross_seeds(target, winners):
                self.assertNotIn("angled", origin)

    def test_mirrored_seed_maps_left_to_right(self):
        seeds = C.cross_seeds("right", {"left": Camera(84.0, 7.0, 2.0, .1, 1, 0, 0)})
        mirrored = [s for s in seeds if s[3] == "cross:left:mirrored"][0]
        self.assertEqual(mirrored[:3], (-84.0, 7.0, -2.0))

    def test_side_prior_signs(self):
        """left.jpg shows the lens plate at image-LEFT: yaw +90 puts +Z at image-left (raster convention)."""
        right, up, toward = raster.camera_basis(Camera(C.PRIOR_YAW["left"], 0, 0, 0, 1, 0, 0))
        self.assertLess(right[2], -0.99)       # +Z projects to -u: image-left
        self.assertGreater(toward[0], 0.99)    # the camera sits on +X: the glasses' own left side
        right, _, _ = raster.camera_basis(Camera(C.PRIOR_YAW["right"], 0, 0, 0, 1, 0, 0))
        self.assertGreater(right[2], 0.99)


class SyntheticRecovery(unittest.TestCase):
    """Render the toy glasses with a known camera, fit from the priors, recover the camera."""

    @classmethod
    def setUpClass(cls):
        raster.clear_cache()
        cls.V, cls.F, cls.frame = _glasses()
        cls.scene = raster.get_scene(cls.V, cls.F, cls.frame)

    def _fit(self, view, truth, shape):
        fg = self.scene.render(truth, shape)["mask"]
        pivot = None if view in ("left", "right") else self.frame.to_norm(np.array([[0.0, 0.0, 55.0]]))[0]
        vf = C.ViewFit(view, fg, self.scene, pivot=pivot)
        vf.run_starts(C.prior_seeds(view), C.N_COARSE_POWELL)
        return vf, vf.refine()["camera"], fg

    def _check(self, view, yaw, pitch, roll, persp, shape, ppm):
        truth = _truth(self.frame, yaw, pitch, roll, persp, ppm, shape, shift=(7.0, -5.0))
        vf, cam, fg = self._fit(view, truth, shape)
        m = C.native_metrics(self.scene, cam, fg, (0, 0, shape[1], shape[0]))
        self.assertGreater(m["iou"], 0.97, (view, cam))
        self.assertLess(m["contour_mean_px"], 1.0, (view, cam))
        self.assertLess(abs(cam.yaw - yaw), 4.0, (view, cam))
        self.assertLess(abs(cam.pitch - pitch), 5.0, (view, cam))
        # the metric scale at the front plane is what later stages read
        P = np.array([0.0, 0.0, 60.0])
        self.assertLess(abs(C.px_per_mm_at(cam, self.frame, P) / C.px_per_mm_at(truth, self.frame, P) - 1), 0.02)
        return vf, cam

    def test_front(self):
        self._check("front", -3.0, 9.0, 1.0, 0.12, (330, 520), 3.0)

    def test_left_yaw_sign(self):
        vf, cam = self._check("left", 86.0, 6.0, -1.0, 0.1, (300, 520), 3.0)
        self.assertGreater(cam.yaw, 0)
        st = C.sign_test(vf)
        self.assertGreater(st["margin"], C.YAW_SIGN_MIN_MARGIN, st)

    def test_angled_negative_sign_found(self):
        """Both yaw signs are refined for the angled view; the true negative sign wins."""
        vf, cam = self._check("angled", -38.0, 18.0, 2.0, 0.15, (330, 520), 3.0)
        signs = {c.yaw >= 0 for _, c, _ in vf.coarse}
        self.assertEqual(signs, {True, False})

    def test_deterministic(self):
        truth = _truth(self.frame, 2.0, 5.0, 0.0, 0.1, 2.0, (240, 360))
        a = self._fit("front", truth, (240, 360))[1]
        b = self._fit("front", truth, (240, 360))[1]
        self.assertEqual(a, b)


RUN = "m1"


def _have(product):
    try:
        return stage_dir(RUN, product, C.STAGE).result_path.exists()
    except Exception:
        return False


@unittest.skipUnless(_have("vb"), "S3 m1 artifacts missing")
class RealRunSmoke(unittest.TestCase):
    """Reads the saved m1 artifacts (does not refit): contract keys, signs, and a re-render check."""

    def test_contract_and_signs(self):
        from bsa import generator
        from bsa.core import PRODUCTS
        for p in PRODUCTS:
            if not _have(p):
                continue
            res = json.loads(stage_dir(RUN, p, C.STAGE).result_path.read_text())
            self.assertEqual(res["stage"], "s3_cameras")
            NormFrame.from_dict(res["frame"])
            self.assertEqual(set(res["cameras"]), set(VIEWS))
            for v, c in res["cameras"].items():
                for k in ("camera", "iou", "contour_mean_px", "contour_p95_px", "px_per_mm", "starts", "flags"):
                    self.assertIn(k, c, (p, v, k))
                camera_from_dict(c["camera"])
            cams = res["cameras"]
            self.assertGreater(cams["left"]["camera"]["yaw"], 45, p)
            self.assertLess(cams["right"]["camera"]["yaw"], -45, p)
            self.assertGreater(cams["back"]["camera"]["yaw"], 135, p)
            self.assertLess(abs(cams["front"]["camera"]["yaw"]), 20, p)
            self.assertIn("ratio", res["consistency"]["front_vs_s2"])
            lf = res["lens_faces"]
            self.assertGreater(lf["count"], 0, p)
            for v in ("left", "right"):
                cov = lf["side_lens_coverage"].get(v)
                self.assertEqual(v in lf["optional_in"], cov is not None and cov < C.LENS_VISIBLE_MIN, (p, v))
            self.assertTrue((stage_dir(RUN, p, C.STAGE).root / "sheet.png").exists())

    def test_saved_front_camera_reproduces_its_iou(self):
        from bsa import generator
        gen = generator.load("vb", RUN)
        frame, cams, res = C.load_cameras("vb", RUN)
        self.assertEqual(frame, gen.frame)
        s0 = stage_dir(RUN, "vb", "s0_intake").load()[1]
        c = res["cameras"]["front"]
        m = C.native_metrics(gen.scene(True), cams["front"], s0["fg_front"].astype(bool), tuple(c["roi_xyxy"]))
        self.assertAlmostEqual(m["iou"], c["iou"], places=4)


if __name__ == "__main__":
    unittest.main()
