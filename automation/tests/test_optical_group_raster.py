import json
import unittest

import numpy as np

from reconstruction.camera import Camera, project
from reconstruction.mesh import TriangleMesh
from reconstruction.optical_group_raster import rasterize_optical_groups, sample_single_group_fields


CAMERA = Camera(0, 0, 0, 0, 20, 20, 20)
SHAPE = (41, 41)


def boxes(specs):
    """Closed boxes as (xmin,xmax,ymin,ymax,zmin,zmax,group)."""
    points, faces, groups = [], [], []
    for x0, x1, y0, y1, z0, z1, group in specs:
        offset = len(points)
        points.extend([[x, y, z] for z in (z0, z1) for y in (y0, y1) for x in (x0, x1)])
        faces.extend((np.array([[0, 2, 3], [0, 3, 1], [4, 5, 7], [4, 7, 6],
                               [0, 1, 5], [0, 5, 4], [2, 6, 7], [2, 7, 3],
                               [0, 4, 6], [0, 6, 2], [1, 3, 7], [1, 7, 5]]) + offset).tolist())
        groups.extend([group]*12)
    return TriangleMesh(np.array(points, float), np.array(faces), []), np.array(groups)


def stacked(depths, groups):
    return boxes([(-.6, .6, -.6, .6, z-.03, z, group) for z, group in zip(depths, groups)])


class EffectiveGroupRasterTests(unittest.TestCase):
    def test_closed_mesh_and_disconnected_overlaps_contribute_once_per_group(self):
        mesh, groups = stacked([.4, .1, -.2], [7, 7, 7])
        for reverse in (False, True):
            if reverse:
                mesh = TriangleMesh(mesh.vertices, mesh.faces[::-1, ::-1], [])
            result = rasterize_optical_groups(mesh, groups, CAMERA, SHAPE)
            self.assertEqual(result.count[20, 20], 1)
            self.assertEqual(result.group[0, 20, 20], 7)
            self.assertAlmostEqual(result.depth[0, 20, 20], -.4)
            self.assertFalse(result.ambiguous.any())
            self.assertFalse(result.report['accepted'])
            json.dumps(result.report, allow_nan=False)

    def test_distinct_groups_order_by_depth_not_identity_or_primitive_order(self):
        mesh, groups = stacked([-.3, .5, .1], [0, 19, 2])
        result = rasterize_optical_groups(mesh, groups, CAMERA, SHAPE)
        np.testing.assert_array_equal(result.group[:, 20, 20], [19, 2, 0])
        np.testing.assert_allclose(result.depth[:, 20, 20], [-.5, -.1, .3])
        order = np.arange(len(groups))[::-1]
        again = rasterize_optical_groups(TriangleMesh(mesh.vertices, mesh.faces[order], []),
                                         groups[order], CAMERA, SHAPE)
        np.testing.assert_array_equal(result.group, again.group)
        np.testing.assert_allclose(result.depth, again.depth)

    def test_opaque_inside_closed_group_stops_further_groups_after_one_full_event(self):
        mesh, groups = boxes([(-.6, .6, -.6, .6, -.3, .5, 9),
                              (-.6, .6, -.6, .6, -.1, 0, -1),
                              (-.6, .6, -.6, .6, -.25, -.2, 2)])
        result = rasterize_optical_groups(mesh, groups, CAMERA, SHAPE)
        self.assertEqual(result.count[20, 20], 1)
        self.assertEqual(result.group[0, 20, 20], 9)
        self.assertAlmostEqual(result.opaque_depth[20, 20], 0.)
        front_opaque = mesh.vertices.copy()
        front_opaque[8:16, 2] += .8
        result = rasterize_optical_groups(TriangleMesh(front_opaque, mesh.faces, []), groups, CAMERA, SHAPE)
        self.assertEqual(result.count[20, 20], 0)

    def test_receiver_depth_applies_same_effective_entry_semantics_and_reports_ties(self):
        mesh, groups = boxes([(-.6, .6, -.6, .6, -.5, .5, 3)])
        for receiver, expected, ambiguous in ((-.6, 0, False), (-.5, 0, True), (0., 1, False), (.7, 1, False)):
            with self.subTest(receiver=receiver):
                result = rasterize_optical_groups(mesh, groups, CAMERA, SHAPE,
                    receiver_depth=np.full(SHAPE, receiver))
                self.assertEqual(result.count[20, 20], expected)
                self.assertEqual(result.ambiguous[20, 20], ambiguous)

    def test_cross_group_coincidence_and_fifth_group_are_explicit_not_truncated(self):
        mesh, groups = stacked([.5, .5], [0, 1])
        result = rasterize_optical_groups(mesh, groups, CAMERA, SHAPE)
        self.assertEqual(result.count[20, 20], 2)
        self.assertTrue(result.ambiguous[20, 20])
        mesh, groups = stacked([.5, .3, .1, -.1, -.3], range(5))
        result = rasterize_optical_groups(mesh, groups, CAMERA, SHAPE)
        self.assertEqual(result.count[20, 20], 5)
        self.assertTrue(result.overflow[20, 20])
        self.assertEqual(result.depth.shape[0], 5)
        with self.assertRaisesRegex(ValueError, 'group count'):
            rasterize_optical_groups(mesh, groups, CAMERA, SHAPE, maximum_groups=4)

    def test_source_surface_barycentrics_reproject_and_depth_is_continuous_near_orthographic(self):
        mesh, groups = boxes([(-.5, .5, -.5, .5, -.2, .2, 1)])
        for perspective in (0, 1e-300, .5):
            camera = Camera(25, 15, 12, perspective, 20, 20, 20)
            result = rasterize_optical_groups(mesh, groups, camera, SHAPE)
            rows, cols = np.nonzero(result.count)
            faces = mesh.faces[result.face_index[0, rows, cols]]
            xyz = np.einsum('ni,nij->nj', result.barycentric[0, rows, cols], mesh.vertices[faces])
            np.testing.assert_allclose(project(xyz, camera), np.column_stack([cols, rows]), atol=1e-12)
            yaw, pitch = np.radians([camera.yaw, camera.pitch])
            toward = [np.cos(pitch)*np.sin(yaw), np.sin(pitch), np.cos(pitch)*np.cos(yaw)]
            np.testing.assert_allclose(result.depth[0, rows, cols], -(xyz @ toward), atol=1e-14)

    def test_no_optics_and_invalid_geometry_camera_receiver_or_capacity(self):
        mesh, groups = stacked([0], [-1])
        result = rasterize_optical_groups(mesh, groups, CAMERA, SHAPE)
        self.assertEqual(result.depth.shape, (0, *SHAPE))
        self.assertFalse(result.count.any())
        for kwargs in ({'receiver_depth': np.full(SHAPE, np.nan)}, {'maximum_pixels': 20},
                       {'maximum_layers': 0}, {'separation': -1}):
            with self.subTest(kwargs=kwargs.keys()), self.assertRaises(ValueError):
                rasterize_optical_groups(mesh, groups, CAMERA, SHAPE, **kwargs)
        with self.assertRaises(ValueError):
            rasterize_optical_groups(mesh, groups.astype(float), CAMERA, SHAPE)
        with self.assertRaisesRegex(ValueError, 'signed int64'):
            rasterize_optical_groups(mesh, np.full(len(groups), 2**63, dtype=np.uint64), CAMERA, SHAPE)
        with self.assertRaises(ValueError):
            rasterize_optical_groups(mesh, groups, Camera(0, 0, 0, 0, float('nan'), 0, 0), SHAPE)


class EffectiveGroupFieldTests(unittest.TestCase):
    def test_opaque_geometry_beyond_receiver_is_not_transmitted_rear_content(self):
        mesh, groups = stacked([.5, -.5], [0, -1])
        uv = (mesh.vertices[:, :2]+.6)/1.2
        normals = np.tile([0., 0., 1.], (len(mesh.vertices), 1))
        for receiver, expected_rear in ((None, 1), (0., 0), (.6, 1)):
            events = rasterize_optical_groups(mesh, groups, CAMERA, SHAPE,
                receiver_depth=None if receiver is None else np.full(SHAPE, receiver))
            fields = sample_single_group_fields(mesh, uv, normals, events, CAMERA)
            self.assertTrue(fields['supported'][20, 20])
            self.assertEqual(fields['rear_weight'][20, 20], expected_rear)

    def test_back_and_front_have_symmetric_incidence_and_intrinsic_height_survives_roll(self):
        mesh, groups = stacked([.3], [4])
        uv = (mesh.vertices[:, :2]+.6)/1.2
        normals = np.tile([0., 0., 1.], (len(mesh.vertices), 1))
        for yaw in (0, 180):
            camera = Camera(yaw, 0, 90, 0, 20, 20, 20)
            events = rasterize_optical_groups(mesh, groups, camera, SHAPE)
            fields = sample_single_group_fields(mesh, uv, normals, events, camera)
            self.assertEqual(fields['group'][20, 20], 4)
            self.assertAlmostEqual(fields['incidence'][20, 20], 0.)
            self.assertAlmostEqual(fields['v'][20, 14], .75)
            np.testing.assert_allclose(fields['reflected'][20, 20], [0., 0., 1.], atol=1e-14)

    def test_vertex_normal_normalization_matches_shader_and_reversed_normals_match(self):
        mesh = TriangleMesh(np.array([[-1., -1., 0], [1., -1., 0], [0, 2., 0]]), np.array([[0, 1, 2]]), [])
        uv = np.array([[0., 0], [1., 0], [.5, 1.]])
        normals = np.array([[0., 0, 20.], [1., 0, 1.], [0, 0, 1.]])
        events = rasterize_optical_groups(mesh, np.array([0]), CAMERA, SHAPE)
        fields = sample_single_group_fields(mesh, uv, normals, events, CAMERA)
        expected = (normals/np.linalg.norm(normals, axis=1)[:, None]).mean(axis=0)
        expected /= np.linalg.norm(expected)
        self.assertAlmostEqual(fields['incidence'][20, 20], np.degrees(np.arccos(expected[2])))
        reverse = sample_single_group_fields(mesh, uv, -normals, events, CAMERA)
        np.testing.assert_allclose(fields['incidence'], reverse['incidence'], equal_nan=True)
        np.testing.assert_allclose(fields['reflected'], reverse['reflected'], equal_nan=True)

    def test_stacks_ties_and_cancelled_normals_stay_unknown(self):
        for depths in ([.3, 0], [.3, .3]):
            mesh, groups = stacked(depths, [0, 1])
            events = rasterize_optical_groups(mesh, groups, CAMERA, SHAPE)
            fields = sample_single_group_fields(mesh, np.full((len(mesh.vertices), 2), .5),
                np.tile([0., 0, 1.], (len(mesh.vertices), 1)), events, CAMERA)
            self.assertFalse(fields['supported'][20, 20])
            self.assertTrue(np.isnan(fields['incidence'][20, 20]))
        mesh, groups = stacked([.3], [0])
        events = rasterize_optical_groups(mesh, groups, CAMERA, SHAPE)
        fields = sample_single_group_fields(mesh, np.full((len(mesh.vertices), 2), .5),
                                           np.zeros_like(mesh.vertices), events, CAMERA)
        self.assertTrue(fields['invalid_attributes'][20, 20])
        self.assertFalse(fields['supported'][20, 20])


if __name__ == '__main__':
    unittest.main()
