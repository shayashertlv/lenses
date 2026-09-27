"""No-network camera/face-lineage checks for the frozen render evidence probe."""
import unittest

import numpy as np

from qa.part_mask_correspondence import camera_for, mask_hits, normalize, raster_source, score_parts
from reconstruction.camera import project
from reconstruction.mesh import TriangleMesh


class CorrespondenceTests(unittest.TestCase):
    def test_camera_basis_matches_three_look_at_and_pixel_centers(self):
        direction = np.array([.68, .22, 1.])
        direction /= np.linalg.norm(direction)
        right = np.cross([0., 1., 0.], direction)
        right /= np.linalg.norm(right)
        up = np.cross(direction, right)
        camera = camera_for([.68, .22, 1.], 640, 480, 2.05)
        points = project(np.array([[0., 0., 0.], right, up, direction]), camera)
        np.testing.assert_allclose(points, [[319.5, 239.5], [319.5+480/2.05, 239.5],
                                           [319.5, 239.5-480/2.05], [319.5, 239.5]], atol=1e-9)

    def test_rotation_translation_then_uniform_scale(self):
        normalization = dict(rotation_order='XYZ', rotation_degrees=[0, -90, 0],
                             translation=[1, 2, 3], uniform_scale=2)
        np.testing.assert_allclose(normalize(np.array([[1., 0., 0.], [0., 0., 1.]]), normalization),
                                   [[2., 4., 8.], [0., 4., 6.]], atol=1e-9)

    def test_front_side_culling_preserves_original_face_ordinals(self):
        vertices = np.array([[-1., -1., 0.], [1., -1., 0.], [0., 1., 0.]])
        mesh = TriangleMesh(vertices, np.array([[2, 1, 0], [0, 1, 2]]), [])
        raster, _ = raster_source(mesh, camera_for([0, 0, 1], 11, 11, 4), (11, 11))
        self.assertGreater((raster == 1).sum(), 0)
        self.assertEqual((raster == 0).sum(), 0)

    def test_visible_evidence_does_not_turn_background_or_hidden_faces_into_hits(self):
        raster = np.array([[-1, 3, 3], [5, 7, -1]])
        mask = np.array([[True, True, False], [False, True, False]])
        visible, counts, hits, hit_counts = mask_hits(raster, mask)
        np.testing.assert_array_equal(visible, [3, 5, 7])
        np.testing.assert_array_equal(counts, [2, 1, 1])
        np.testing.assert_array_equal(hits, [3, 7])
        np.testing.assert_array_equal(hit_counts, [1, 1])
        labels = np.full(9, -1)
        labels[3], labels[5], labels[7] = 0, 1, -2
        scores = score_parts(raster, mask, labels, [dict(id='p0', index=0), dict(id='p1', index=1)])
        self.assertEqual(scores['mapped_mask_pixels'], 1)
        self.assertEqual(scores['parts'][0]['precision_on_matched_pixels'], .5)
        self.assertEqual(scores['selected_parts'], [])


if __name__ == '__main__':
    unittest.main()
