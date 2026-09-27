"""bsa.raster: ray model == reconstruction.camera.project, grids, ROI, culling, cache, real smoke."""
from __future__ import annotations

import time
import unittest

import numpy as np
import open3d as o3d

from bsa import raster
from bsa.core import NormFrame, project_mm, stage_dir
from reconstruction.camera import Camera, render_mask
from reconstruction.mesh import TriangleMesh


def _torus():
    m = o3d.geometry.TriangleMesh.create_torus(torus_radius=30, tube_radius=8, radial_resolution=60,
                                               tubular_resolution=30)
    V = np.asarray(m.vertices) + [3.0, -2.0, 5.0]
    F = np.asarray(m.triangles).astype(np.int32)
    lo, hi = V.min(0), V.max(0)
    return V, F, NormFrame(tuple((lo + hi) / 2), float((hi - lo).max()))


def _cameras(seed=0, n=8):
    rng = np.random.default_rng(seed)
    persp = [0.0, 0.3, 0.8, 0.05, 0.5, 0.001, 0.0, 0.65]
    return [Camera(float(rng.uniform(-180, 180)), float(rng.uniform(-40, 40)), float(rng.uniform(-25, 25)),
                   persp[k % len(persp)], float(rng.uniform(200, 600)), float(rng.uniform(120, 280)),
                   float(rng.uniform(120, 280))) for k in range(n)]


def _bary_points(V, F, face_id, bary):
    b = np.asarray(bary, np.float64)
    T = V[F[face_id]]
    return (1 - b[:, 0] - b[:, 1])[:, None] * T[:, 0] + b[:, 0, None] * T[:, 1] + b[:, 1, None] * T[:, 2]


class RayModelMatchesProject(unittest.TestCase):
    def setUp(self):
        raster.clear_cache()
        self.V, self.F, self.frame = _torus()

    def test_hits_reproject_onto_pixel_centres(self):
        """Hit points rebuilt from (face, barycentrics) -- independent of the ray origin and direction --
        project back onto the centre of the pixel that cast them within 0.05 px (ortho and perspective)."""
        for cam in _cameras():
            for stride, roi in ((1, None), (3, (40, 25, 377, 390))):
                r = raster.render(self.V, self.F, cam, self.frame, (400, 400), stride=stride, roi=roi, want_bary=True)
                ii, jj = np.nonzero(r["mask"])
                self.assertGreater(len(ii), 300, cam)
                P = _bary_points(self.V, self.F, r["face_id"][ii, jj], r["bary"][ii, jj])
                uv = project_mm(P, cam, self.frame)
                us, vs = r["grid"]
                err = np.hypot(uv[:, 0] - us[jj], uv[:, 1] - vs[ii])
                self.assertLess(float(err.max()), 0.05, (cam, stride, float(err.max())))

    def test_projected_surface_points_are_hit(self):
        """Random surface points projected with project() are hit by the ray through their projection
        (when visible): same 3D point, depth equal, and it reprojects within 0.05 px."""
        rng = np.random.default_rng(1)
        f = rng.integers(0, len(self.F), 4000)
        b = rng.dirichlet([1, 1, 1], 4000)
        P = (b[:, :1] * self.V[self.F[f, 0]] + b[:, 1:2] * self.V[self.F[f, 1]] + b[:, 2:] * self.V[self.F[f, 2]])
        scene = raster.get_scene(self.V, self.F, self.frame)
        for cam in _cameras(seed=2):
            uv = project_mm(P, cam, self.frame)
            res = scene.cast(cam, uv[:, 0], uv[:, 1], want_points=True)
            # float32 ray casting: a sample within ~1e-6 of a shared edge may slip through the crack
            self.assertGreaterEqual(res["hit"].mean(), 0.999)
            toward = raster.camera_basis(cam)[2]
            depth_p = -(self.frame.to_norm(P) @ toward) * self.frame.extent
            tol = 5e-3                                                 # mm; float32 t at grazing angles
            visible = np.abs(res["depth"] - depth_p) < tol           # the sample is the first surface
            self.assertGreater(visible.mean(), 0.3)
            hit = res["hit"]
            self.assertTrue((res["depth"][hit] <= depth_p[hit] + tol).all())   # never hit behind it
            d3 = np.linalg.norm(res["points"][visible] - P[visible], axis=1)
            self.assertLess(float(d3.max()), tol)
            same_face = res["face_id"][visible] == f[visible]
            self.assertGreater(same_face.mean(), 0.97)               # shared edges may report a neighbour
            back = project_mm(res["points"][visible], cam, self.frame)
            self.assertLess(float(np.abs(back - uv[visible]).max()), 0.05)

    def test_depth_is_mm_along_the_view_axis(self):
        cam = raster.view_camera(self.frame, 0, 0, 4.0, (300, 300))
        r = raster.render(self.V, self.F, cam, self.frame, (300, 300), want_points=True)
        m = r["mask"]
        z = r["points"][m][:, 2]
        np.testing.assert_allclose(self.frame.center[2] - r["depth"][m], z, atol=1e-4)
        # the torus faces the camera: every front hit is on the +Z half of the tube
        self.assertTrue((z >= 5.0 - 8.0 - 1e-6).all())

    def test_mask_agrees_with_reconstruction_render_mask(self):
        mesh = TriangleMesh(self.frame.to_norm(self.V), self.F.astype(np.int64), [])
        for cam in _cameras(seed=3, n=4):
            a = raster.render(self.V, self.F, cam, self.frame, (400, 400))["mask"]
            b = render_mask(mesh, cam, (400, 400))
            iou = np.count_nonzero(a & b) / np.count_nonzero(a | b)
            self.assertGreater(iou, 0.97, cam)
            # every disagreement is a boundary pixel (render_mask's fill is ~1 px generous); the
            # 2 px image border is excluded (cv2 clipping paints triangles that leave the image there)
            from scipy import ndimage
            ring = ndimage.binary_dilation(a, iterations=1) & ~ndimage.binary_erosion(a, iterations=1)
            self.assertEqual(int(((a ^ b) & ~ring)[2:-2, 2:-2].sum()), 0)
            self.assertEqual(int((a & ~b).sum()), 0)      # raster never covers more than the fill


class GridRoiCullCache(unittest.TestCase):
    def setUp(self):
        raster.clear_cache()
        self.V, self.F, self.frame = _torus()
        self.cam = Camera(20, 10, 5, 0.3, 380, 200, 190)

    def test_pixel_grid_is_block_centres(self):
        us, vs = raster.pixel_grid((10, 13), stride=4)
        np.testing.assert_allclose(us, [1.5, 5.5, 9.5, 13.5])
        np.testing.assert_allclose(vs, [1.5, 5.5, 9.5])
        us, vs = raster.pixel_grid((10, 13), stride=1, roi=(2, 3, 5, 6))
        np.testing.assert_allclose(us, [2, 3, 4])
        np.testing.assert_allclose(vs, [3, 4, 5])
        self.assertEqual(raster.grid_shape((10, 13), 4), (3, 4))

    def test_downsample_mask_block_mean(self):
        m = np.zeros((10, 13), bool)
        m[0:4, 0:2] = True
        m[8:10, 12] = True
        d = raster.downsample_mask(m, 4)
        self.assertEqual(d.shape, (3, 4))
        self.assertAlmostEqual(float(d[0, 0]), 0.5)
        self.assertAlmostEqual(float(d[2, 3]), 1.0)     # edge block averages its 2 inside pixels
        self.assertAlmostEqual(float(d.sum()), 1.5)

    def test_strided_render_samples_block_centres(self):
        r4 = raster.render(self.V, self.F, self.cam, self.frame, (400, 400), stride=4)
        us, vs = r4["grid"]
        uu, vv = np.meshgrid(us, vs)
        c = raster.get_scene(self.V, self.F, self.frame).cast(self.cam, uu.ravel(), vv.ravel())
        np.testing.assert_array_equal(r4["mask"].ravel(), c["hit"])
        np.testing.assert_array_equal(r4["face_id"].ravel(), c["face_id"])
        full = raster.render(self.V, self.F, self.cam, self.frame, (400, 400))["mask"]
        cover = raster.downsample_mask(full, 4)
        agree = np.mean((cover >= 0.5) == r4["mask"])
        self.assertGreater(agree, 0.97)

    def test_roi_is_a_crop_of_the_full_render(self):
        full = raster.render(self.V, self.F, self.cam, self.frame, (400, 400))
        roi = (37, 51, 290, 333)
        crop = raster.render(self.V, self.F, self.cam, self.frame, (400, 400), roi=roi)
        for k in ("mask", "face_id", "depth"):
            np.testing.assert_array_equal(crop[k], full[k][51:333, 37:290])

    def test_culling_is_exact(self):
        for cam in _cameras(seed=5, n=4):
            a = raster.render(self.V, self.F, cam, self.frame, (400, 400), stride=2)
            b = raster.render(self.V, self.F, cam, self.frame, (400, 400), stride=2, cull=False)
            for k in ("mask", "face_id", "depth"):
                np.testing.assert_array_equal(a[k], b[k])

    def test_scene_cache_by_content_and_frame(self):
        s1 = raster.get_scene(self.V, self.F, self.frame)
        s2 = raster.get_scene(self.V.copy(), self.F.copy(), self.frame)
        self.assertIs(s1, s2)
        other = NormFrame(self.frame.center, self.frame.extent * 1.01)
        self.assertIsNot(s1, raster.get_scene(self.V, self.F, other))

    def test_near_plane_raises_like_project(self):
        cam = Camera(90, 0, 0, 2.5, 300, 200, 200)   # eye at 0.4 normalized units along +X: inside the torus
        with self.assertRaises(ValueError):
            project_mm(self.V, cam, self.frame)
        with self.assertRaises(ValueError):
            raster.render(self.V, self.F, cam, self.frame, (400, 400))

    def test_determinism(self):
        a = raster.render(self.V, self.F, self.cam, self.frame, (400, 400), want_bary=True)
        raster.clear_cache()
        b = raster.render(self.V, self.F, self.cam, self.frame, (400, 400), want_bary=True)
        for k in ("mask", "face_id", "depth", "bary"):
            np.testing.assert_array_equal(a[k], b[k])

    def test_view_camera_centres_the_frame(self):
        cam = raster.view_camera(self.frame, 30, 20, 3.0, (201, 301))
        uv = project_mm(np.array([self.frame.center]), cam, self.frame)[0]
        np.testing.assert_allclose(uv, [150.0, 100.0], atol=1e-9)
        self.assertAlmostEqual(cam.scale / self.frame.extent, 3.0)


class RealGeneratorSmoke(unittest.TestCase):
    """The fast path on a real canonical generator (skips when S1 has not run)."""

    def test_decimated_fast_path(self):
        sd = stage_dir("m1", "vb", "s1_generator")
        if not sd.done():
            self.skipTest("S1 artifacts for vb/m1 missing")
        from bsa import generator
        gen = generator.load("vb", "m1")
        shape = (1100, 1100)
        cam = raster.view_camera(gen.frame, 0, 0, 1100 * 0.8 / 140.0, shape)
        dec = gen.scene(decimated=True)
        dec.render(cam, shape, stride=4)
        t = time.perf_counter()
        n = 20
        for k in range(n):
            r = dec.render(Camera(cam.yaw + k * 0.1, cam.pitch, cam.roll, cam.perspective, cam.scale,
                                  cam.center_x, cam.center_y), shape, stride=4)
        per = (time.perf_counter() - t) / n
        self.assertTrue(r["mask"].any())
        self.assertLess(per, 0.25, f"{per * 1000:.1f} ms per stride-4 render")
        full = gen.scene(decimated=False).render(cam, shape, stride=4)["mask"]
        fast = dec.render(cam, shape, stride=4)["mask"]
        iou = np.count_nonzero(full & fast) / np.count_nonzero(full | fast)
        self.assertGreater(iou, 0.97)


if __name__ == "__main__":
    unittest.main()
