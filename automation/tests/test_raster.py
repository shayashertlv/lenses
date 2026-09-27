import unittest

import numpy as np

from reconstruction.camera import Camera, project
from reconstruction.mesh import TriangleMesh
from reconstruction.raster import contour_samples, rasterize


class RasterTests(unittest.TestCase):
    def test_depth_peel_skips_coplanar_duplicates_and_reaches_next_interface(self):
        near = np.array([[-.5, -.5, .2], [.5, -.5, .2], [0, .5, .2]])
        far = near - [0, 0, .3]
        mesh = TriangleMesh(np.concatenate((near, far)), np.array([[0, 1, 2], [0, 2, 1], [3, 4, 5]]), [])
        for perspective in (0, 1e-16, 1e-10, .5):
            camera = Camera(0, 0, 0, perspective, 50, 40, 40)
            first = rasterize(mesh, camera, (80, 80))
            self.assertAlmostEqual(first.depth[40, 40], -.2)
            second = rasterize(mesh, camera, (80, 80), after_depth=first.depth + 1e-9)
            self.assertEqual(second.face_index[40, 40], 2)
            self.assertAlmostEqual(second.depth[40, 40], .1)
            self.assertFalse(second.mask[0, 0])
            third = rasterize(mesh, camera, (80, 80), after_depth=second.depth + 1e-9)
            self.assertFalse(third.mask.any())
        with self.assertRaisesRegex(ValueError, 'matching grid'):
            rasterize(mesh, camera, (80, 80), after_depth=np.zeros((20, 20)))

    def test_depth_selects_front_surface_independent_of_triangle_order(self):
        near = np.array([[-.5, -.5, .2], [.5, -.5, .2], [0, .5, .2]])
        far = near.copy()
        far[:, 2] = -.2
        camera = Camera(0, 0, 0, 0, 50, 40, 40)
        mesh = TriangleMesh(np.concatenate((far, near)), np.array([[0, 1, 2], [3, 4, 5]]), [])
        for batch in (20, 100000):
            raster = rasterize(mesh, camera, (80, 80), max_candidates=batch)
            self.assertEqual(raster.face_index[40, 40], 1)
            np.testing.assert_allclose(raster.surface_points(mesh, [40], [40]), [[0, 0, .2]], atol=1e-14)

    def test_perspective_surface_barycentrics_reproject_to_original_pixel_centers(self):
        vertices = np.array([[-.5, -.5, -.2], [.5, -.4, .4], [.1, .5, .1]])
        mesh = TriangleMesh(vertices, np.array([[0, 1, 2]]), [])
        camera = Camera(18, 14, -7, .6, 70, 60, 60)
        raster = rasterize(mesh, camera, (120, 120))
        rows, cols = np.nonzero(raster.mask)
        xyz = raster.surface_points(mesh, rows, cols)
        np.testing.assert_allclose(project(xyz, camera), np.column_stack((cols, rows)), atol=1e-12)
        np.testing.assert_allclose(raster.barycentric[rows, cols].sum(axis=1), 1, atol=1e-14)
        yaw, pitch = np.radians([camera.yaw, camera.pitch])
        toward = np.array([np.cos(pitch)*np.sin(yaw), np.sin(pitch), np.cos(pitch)*np.cos(yaw)])
        np.testing.assert_allclose(raster.depth[rows, cols], -(xyz @ toward), atol=1e-14)

    def test_triangle_union_does_not_depend_on_subdivision_or_double_sided_winding(self):
        square = np.array([[-.5, -.5, 0], [.5, -.5, 0], [.5, .5, 0], [-.5, .5, 0], [0, 0, 0]])
        camera = Camera(0, 0, 0, 0, 40, 30, 30)
        first = rasterize(TriangleMesh(square, np.array([[0, 1, 2], [0, 2, 3]]), []), camera, (60, 60))
        second = rasterize(TriangleMesh(square, np.array([[1, 0, 4], [2, 1, 4], [3, 2, 4], [0, 3, 4]]), []), camera, (60, 60))
        np.testing.assert_array_equal(first.mask, second.mask)
        self.assertEqual(first.mask.sum(), 41 ** 2)
        samples = contour_samples(TriangleMesh(square, np.array([[0, 1, 2], [0, 2, 3]]), []), first)
        self.assertGreater(len(samples['xy']), 10)
        np.testing.assert_allclose(project(samples['xyz'], camera), samples['xy'], atol=1e-12)
        relative = samples['xy'] - [30, 30]
        self.assertTrue(np.all(np.sum(relative * samples['normals'], axis=1) > 0))

    def test_missing_geometry_never_supplies_a_world_correspondence(self):
        mesh = TriangleMesh(np.array([[0, 0, 0], [.1, 0, 0], [0, .1, 0]]), np.array([[0, 1, 2]]), [])
        raster = rasterize(mesh, Camera(0, 0, 0, 0, 20, 20, 20), (40, 40))
        with self.assertRaises(ValueError):
            raster.surface_points(mesh, [0], [0])


if __name__ == '__main__':
    unittest.main()
