"""Analytic component-projection controls, independent of product semantics."""
from copy import deepcopy
from dataclasses import replace
import json
import unittest

import numpy as np

from reconstruction.camera import Camera
from reconstruction.component_projection import (
    ProjectionLimits, measure_component_apertures, project_components,
)
from reconstruction.mesh import TriangleMesh


CAMERA = Camera(0, 0, 0, 0, 1, 0, 0)
SHAPE = (8, 8)


def rectangles(specifications):
    """Independent planar rectangles: (left, top, right, bottom, a, b, c).

    Image coordinates are (x, -y), and world height is z=a*x+b*y+c.
    Each rectangle is its own component, with two source face occurrences.
    """
    vertices, faces, labels = [], [], []
    for component, (left, top, right, bottom, a, b, c) in enumerate(specifications):
        offset = len(vertices)
        for x, y in ((left, -top), (right, -top), (right, -bottom), (left, -bottom)):
            vertices.append([x, y, a*x+b*y+c])
        faces.extend([[offset, offset+1, offset+2], [offset, offset+2, offset+3]])
        labels.extend([component, component])
    return TriangleMesh(np.asarray(vertices, dtype=float), np.asarray(faces, dtype=np.int64), []), np.asarray(labels, dtype=np.int64)


def sheet(z=0, *, left=1.25, top=1.25, right=4.75, bottom=4.75):
    return (left, top, right, bottom, 0, 0, z)


def expected_square_pixels():
    return np.asarray([row*SHAPE[1]+column for row in range(2, 5) for column in range(2, 5)], dtype=np.int64)


def aperture(mask, *, identifier='aperture', known=None):
    return {'id': identifier, 'mask': mask,
            'known_domain': np.ones(mask.shape, dtype=bool) if known is None else known,
            'provenance': {'method': 'analytic synthetic aperture', 'semantic_identity': 'unverified'}}


class ComponentProjectionTests(unittest.TestCase):
    def assert_piece_contract(self, result, mesh, labels):
        self.assertEqual(tuple(result['shape']), SHAPE)
        np.testing.assert_array_equal(result['face_components'], labels)
        self.assertEqual([piece['component_id'] for piece in result['pieces']], list(range(int(labels.max())+1)))
        for piece in result['pieces']:
            pixels, faces = piece['pixels'], piece['face_indices']
            self.assertEqual(pixels.dtype, np.dtype(np.int64))
            self.assertEqual(faces.dtype, np.dtype(np.int64))
            self.assertEqual(piece['barycentric'].dtype, np.dtype(np.float64))
            self.assertEqual(piece['depth'].dtype, np.dtype(np.float64))
            self.assertEqual(piece['barycentric'].shape, (len(pixels), 3))
            self.assertEqual(faces.shape, pixels.shape)
            self.assertEqual(piece['depth'].shape, pixels.shape)
            self.assertTrue(np.all(pixels[1:] > pixels[:-1]))
            self.assertTrue(np.all((pixels >= 0) & (pixels < np.prod(SHAPE))))
            self.assertTrue(np.all((faces >= 0) & (faces < len(mesh.faces))))
            np.testing.assert_array_equal(labels[faces], np.full(len(faces), piece['component_id']))
            self.assertTrue(np.isfinite(piece['barycentric']).all())
            self.assertTrue(np.isfinite(piece['depth']).all())
            np.testing.assert_allclose(piece['barycentric'].sum(axis=1), np.ones(len(pixels)), atol=1e-12)
        json.dumps(result['report'], allow_nan=False)

    def test_rectangles_have_analytic_pixels_and_global_source_face_mapping(self):
        mesh, labels = rectangles([sheet(1), sheet(0)])
        result = project_components(mesh, labels, CAMERA, SHAPE)
        self.assert_piece_contract(result, mesh, labels)
        for component, z in ((0, 1), (1, 0)):
            piece = result['pieces'][component]
            np.testing.assert_array_equal(piece['pixels'], expected_square_pixels())
            xyz = np.einsum('ni,nij->nj', piece['barycentric'], mesh.vertices[mesh.faces[piece['face_indices']]])
            np.testing.assert_allclose(xyz[:, 0], piece['pixels'] % SHAPE[1], atol=1e-12)
            np.testing.assert_allclose(xyz[:, 1], -(piece['pixels'] // SHAPE[1]), atol=1e-12)
            np.testing.assert_allclose(xyz[:, 2], z, atol=1e-12)
            np.testing.assert_allclose(piece['depth'], -z, atol=1e-12)

    def test_rear_component_is_retained_behind_full_scene_frontmost(self):
        mesh, labels = rectangles([sheet(1), sheet(0)])
        result = project_components(mesh, labels, CAMERA, SHAPE)
        full = result['full_scene']
        pixels = expected_square_pixels()
        self.assertEqual(int(full.mask.sum()), 9)
        np.testing.assert_array_equal(labels[full.face_index.ravel()[pixels]], np.zeros(9, dtype=np.int64))
        np.testing.assert_allclose(full.depth.ravel()[pixels], -1)
        self.assertEqual(len(result['pieces'][1]['pixels']), 9)
        np.testing.assert_allclose(result['pieces'][1]['depth'], 0)

    def test_coplanar_components_keep_equal_coverage_despite_tie_ownership(self):
        mesh, labels = rectangles([sheet(.3), sheet(.3)])
        result = project_components(mesh, labels, CAMERA, SHAPE)
        np.testing.assert_array_equal(result['pieces'][0]['pixels'], result['pieces'][1]['pixels'])
        np.testing.assert_array_equal(result['pieces'][0]['depth'], result['pieces'][1]['depth'])
        pixels = expected_square_pixels()
        np.testing.assert_array_equal(labels[result['full_scene'].face_index.ravel()[pixels]], np.zeros(9, dtype=np.int64))
        order = np.array([2, 3, 0, 1])
        reordered = project_components(TriangleMesh(mesh.vertices, mesh.faces[order], []), labels[order], CAMERA, SHAPE)
        np.testing.assert_array_equal(labels[order][reordered['full_scene'].face_index.ravel()[pixels]], np.ones(9, dtype=np.int64))
        for original, changed in zip(result['pieces'], reordered['pieces']):
            np.testing.assert_array_equal(original['pixels'], changed['pixels'])
            np.testing.assert_allclose(original['depth'], changed['depth'])

    def test_zero_area_subpixel_and_outside_components_are_not_dropped(self):
        mesh, labels = rectangles([sheet(0), sheet(5, left=6.1, right=6.2, top=6.1, bottom=6.2),
                                   sheet(0, left=12, right=13, top=12, bottom=13)])
        mesh.faces = np.vstack([mesh.faces, [0, 0, 0]])
        labels = np.r_[labels, 3].astype(np.int64)
        result = project_components(mesh, labels, CAMERA, SHAPE)
        self.assert_piece_contract(result, mesh, labels)
        self.assertEqual(len(result['pieces']), 4)
        for piece in result['pieces'][1:]:
            self.assertEqual(piece['pixels'].size, 0)
            self.assertEqual(piece['face_indices'].size, 0)
            self.assertEqual(piece['barycentric'].shape, (0, 3))
        np.testing.assert_array_equal(result['face_components'], labels)

    def test_perspective_depth_and_barycentrics_match_analytic_ray_plane_intersection(self):
        a, b, c, perspective = .1, .05, .2, .2
        mesh, labels = rectangles([(1.25, 1.25, 4.75, 4.75, a, b, c)])
        camera = replace(CAMERA, perspective=perspective)
        result = project_components(mesh, labels, camera, SHAPE)
        piece = result['pieces'][0]
        self.assertGreater(len(piece['pixels']), 0)
        columns, rows = piece['pixels'] % SHAPE[1], piece['pixels'] // SHAPE[1]
        ray_coefficient = a*columns-b*rows
        z = (ray_coefficient+c)/(1+perspective*ray_coefficient)
        expected = np.column_stack((columns*(1-perspective*z), -rows*(1-perspective*z), z))
        xyz = np.einsum('ni,nij->nj', piece['barycentric'], mesh.vertices[mesh.faces[piece['face_indices']]])
        np.testing.assert_allclose(xyz, expected, rtol=1e-12, atol=1e-12)
        np.testing.assert_allclose(piece['depth'], -z, rtol=1e-12, atol=1e-12)

    def test_crossed_planes_have_both_depth_orders_and_nonzero_absolute_gaps(self):
        mesh, labels = rectangles([(1.25, 1.25, 4.75, 4.75, .1, 0, -.3),
                                   (1.25, 1.25, 4.75, 4.75, -.1, 0, .3)])
        result = project_components(mesh, labels, CAMERA, SHAPE)
        first, second = result['pieces']
        np.testing.assert_array_equal(first['pixels'], second['pixels'])
        delta = second['depth']-first['depth']
        np.testing.assert_allclose(delta, .2*(first['pixels'] % SHAPE[1]-3), atol=1e-12)
        self.assertAlmostEqual(float(np.median(delta)), 0)
        self.assertEqual(int(np.count_nonzero(delta < -1e-12)), 3)
        self.assertEqual(int(np.count_nonzero(delta > 1e-12)), 3)
        self.assertGreater(float(np.quantile(np.abs(delta), .9)), .19)

    def test_source_arrays_and_untrusted_part_roles_are_unchanged_and_unused(self):
        mesh, labels = rectangles([sheet(.3), sheet(.1)])
        mesh.parts = [{'role': 'lens', 'material': {'alpha': 0}}, {'role': 'frame'}]
        original = (mesh.vertices.copy(), mesh.faces.copy(), labels.copy(), deepcopy(mesh.parts))
        result = project_components(mesh, labels, CAMERA, SHAPE)
        changed = project_components(TriangleMesh(mesh.vertices, mesh.faces, [{'role': 'unrelated'}]), labels, CAMERA, SHAPE)
        np.testing.assert_array_equal(mesh.vertices, original[0])
        np.testing.assert_array_equal(mesh.faces, original[1])
        np.testing.assert_array_equal(labels, original[2])
        self.assertEqual(mesh.parts, original[3])
        np.testing.assert_array_equal(result['full_scene'].face_index, changed['full_scene'].face_index)
        for before, after in zip(result['pieces'], changed['pieces']):
            for key in ('pixels', 'face_indices', 'barycentric', 'depth'):
                np.testing.assert_array_equal(before[key], after[key])

    def test_invalid_component_memberships_are_rejected(self):
        mesh, labels = rectangles([sheet(0), sheet(.2)])
        for bad in (labels[:-1], np.array([0, 0, 2, 2]), np.array([0, 0, -1, -1]),
                    labels.astype(float), labels.astype(bool)):
            with self.subTest(labels=bad.tolist()), self.assertRaises(ValueError):
                project_components(mesh, bad, CAMERA, SHAPE)

    def test_invalid_camera_projection_domain_and_depth_conversion_are_rejected(self):
        mesh, labels = rectangles([sheet(0)])
        for field in CAMERA.to_dict():
            with self.subTest(nonfinite=field), self.assertRaises(ValueError):
                project_components(mesh, labels, replace(CAMERA, **{field: float('nan')}), SHAPE)
        for camera in (replace(CAMERA, scale=0), replace(CAMERA, scale=-1),
                       replace(CAMERA, perspective=-.1), replace(CAMERA, center_x=1e8)):
            with self.subTest(camera=camera), self.assertRaises(ValueError):
                project_components(mesh, labels, camera, SHAPE)
        crossing, component = rectangles([sheet(1)])
        with self.assertRaises(ValueError):
            project_components(crossing, component, replace(CAMERA, perspective=1), SHAPE)
        for conversion in (0, -1, float('nan'), float('inf')):
            with self.subTest(conversion=conversion), self.assertRaises(ValueError):
                project_components(mesh, labels, CAMERA, SHAPE, depth_to_reference=conversion)

    def test_projection_capacities_reject_instead_of_omitting_components(self):
        mesh, labels = rectangles([sheet(0), sheet(.2)])
        for field, bound in (('maximum_vertices', 7), ('maximum_faces', 3),
                             ('maximum_components', 1), ('maximum_pixels', 63),
                             ('maximum_sparse_events', 17)):
            with self.subTest(field=field), self.assertRaises(ValueError):
                project_components(mesh, labels, CAMERA, SHAPE, limits=replace(ProjectionLimits(), **{field: bound}))


class ComponentApertureMeasurementTests(unittest.TestCase):
    def setUp(self):
        self.mask = np.zeros(SHAPE, dtype=bool)
        self.mask[2:5, 2:5] = True

    def project(self, specifications, *, depth_to_reference=1):
        mesh, labels = rectangles(specifications)
        return project_components(mesh, labels, CAMERA, SHAPE, depth_to_reference=depth_to_reference)

    def measure(self, specifications, *, depth_to_reference=1, depth_tolerance=0):
        projection = self.project(specifications, depth_to_reference=depth_to_reference)
        return measure_component_apertures(projection, [aperture(self.mask)], depth_tolerance=depth_tolerance)

    def test_contained_small_piece_does_not_explain_a_broad_aperture(self):
        result = self.measure([sheet(0), sheet(1, left=2.25, top=2.25, right=3.75, bottom=3.75)])
        broad, small = result['pieces']
        broad_mask, small_mask = broad['apertures'][0], small['apertures'][0]
        self.assertEqual(broad['projected_pixels'], 9)
        self.assertEqual(small['projected_pixels'], 1)
        self.assertEqual(broad_mask['piece_inside_fraction'], 1)
        self.assertEqual(small_mask['piece_inside_fraction'], 1)
        self.assertEqual(broad_mask['aperture_covered_fraction'], 1)
        self.assertAlmostEqual(small_mask['aperture_covered_fraction'], 1/9)
        self.assertEqual(small_mask['aperture_unexplained_pixels'], 8)
        self.assertEqual(broad['exact_owner_pixels'], 8)
        self.assertEqual(small['exact_owner_pixels'], 1)
        self.assertEqual(broad['hypothetical_opaque_blockers'], [{'component_id': 1, 'pixels': 1}])
        pair, = result['pairs']
        self.assertEqual((pair['left_component'], pair['right_component']), (0, 1))
        self.assertEqual(pair['intersection_pixels'], 1)
        self.assertAlmostEqual(pair['left_contained_fraction'], 1/9)
        self.assertEqual(pair['right_contained_fraction'], 1)
        self.assertEqual(pair['depth_summary']['right_nearer_pixels'], 1)
        self.assertEqual(result['accepted'], False)
        self.assertEqual(result['semantic_identity'], 'not_inferred')
        self.assertEqual(result['quality_verdict'], 'unmeasured')
        json.dumps(result, allow_nan=False)

    def test_broad_duplicate_footprints_keep_rear_layer_and_scale_depth_units(self):
        result = self.measure([sheet(.3), sheet(.1)], depth_to_reference=2.5)
        first, second = result['pieces']
        self.assertEqual(first['exact_owner_pixels'], 9)
        self.assertEqual(second['exact_owner_pixels'], 0)
        self.assertEqual(first['depth_compatible_front_pixels'], 9)
        self.assertEqual(second['depth_compatible_front_pixels'], 0)
        self.assertEqual(second['projected_pixels'], 9)
        pair, = result['pairs']
        self.assertEqual(pair['left_contained_fraction'], 1)
        self.assertEqual(pair['right_contained_fraction'], 1)
        self.assertEqual(pair['iou'], 1)
        summary = pair['depth_summary']
        self.assertEqual(summary['sample_count'], 9)
        np.testing.assert_allclose(summary['signed_quantiles'], .5, atol=1e-12)
        np.testing.assert_allclose(summary['absolute_quantiles'], .5, atol=1e-12)
        self.assertEqual(summary['left_nearer_pixels'], 9)
        self.assertEqual(summary['right_nearer_pixels'], 0)

    def test_exact_coplanar_ties_are_not_assigned_exclusive_visibility(self):
        result = self.measure([sheet(.3), sheet(.3)], depth_tolerance=1e-12)
        self.assertEqual([p['exact_owner_pixels'] for p in result['pieces']], [9, 0])
        self.assertEqual([p['depth_compatible_front_pixels'] for p in result['pieces']], [9, 9])
        summary = result['pairs'][0]['depth_summary']
        self.assertEqual(summary['within_tolerance_pixels'], 9)
        self.assertEqual(summary['left_nearer_pixels'], 0)
        self.assertEqual(summary['right_nearer_pixels'], 0)

    def test_crossing_surfaces_do_not_look_coincident_from_signed_median(self):
        result = self.measure([(1.25, 1.25, 4.75, 4.75, .1, 0, -.3),
                               (1.25, 1.25, 4.75, 4.75, -.1, 0, .3)], depth_tolerance=1e-12)
        summary = result['pairs'][0]['depth_summary']
        self.assertAlmostEqual(summary['signed_quantiles'][2], 0)
        self.assertGreater(summary['absolute_quantiles'][3], .19)
        self.assertEqual(summary['left_nearer_pixels'], 3)
        self.assertEqual(summary['right_nearer_pixels'], 3)
        self.assertEqual(summary['within_tolerance_pixels'], 3)

    def test_unknown_crop_exterior_is_excluded_from_negative_evidence(self):
        projection = self.project([sheet(0)])
        known = np.zeros(SHAPE, dtype=bool)
        known[:, :4] = True
        result = measure_component_apertures(projection, [aperture(self.mask & known, known=known)])
        metric = result['pieces'][0]['apertures'][0]
        self.assertEqual(metric['piece_known_pixels'], 6)
        self.assertEqual(metric['piece_unknown_pixels'], 3)
        self.assertEqual(metric['aperture_pixels'], 6)
        self.assertEqual(metric['intersection_pixels'], 6)
        self.assertEqual(metric['piece_outside_aperture_pixels'], 0)
        self.assertEqual(metric['piece_inside_fraction'], 1)
        self.assertEqual(metric['aperture_covered_fraction'], 1)
        self.assertEqual(metric['iou'], 1)
        self.assertEqual(metric['area_ratio'], 1)

    def test_pair_depth_summary_can_be_restricted_to_the_aperture(self):
        projection = self.project([(1.25, 1.25, 4.75, 4.75, .1, 0, -.3),
                                   (1.25, 1.25, 4.75, 4.75, -.1, 0, .3)])
        right = np.zeros(SHAPE, dtype=bool)
        right[2:5, 4] = True
        result = measure_component_apertures(projection, [aperture(right)], depth_tolerance=1e-12)
        pair = result['pairs'][0]
        metric = pair['apertures'][0]
        self.assertEqual(metric['known_overlap_pixels'], 9)
        self.assertEqual(metric['aperture_overlap_pixels'], 3)
        summary = metric['depth_summary']
        self.assertEqual(summary['sample_count'], 3)
        self.assertEqual(summary['left_nearer_pixels'], 3)
        self.assertEqual(summary['right_nearer_pixels'], 0)
        np.testing.assert_allclose(summary['signed_quantiles'], .2, atol=1e-12)

    def test_empty_aperture_or_projected_piece_has_no_invented_denominator(self):
        projection = self.project([sheet(0), sheet(0, left=12, right=13, top=12, bottom=13)])
        empty = np.zeros(SHAPE, dtype=bool)
        result = measure_component_apertures(projection, [aperture(empty, identifier='empty'), aperture(self.mask, identifier='square')])
        visible_empty, visible_square = result['pieces'][0]['apertures']
        absent_empty, absent_square = result['pieces'][1]['apertures']
        self.assertEqual(visible_empty['piece_inside_fraction'], 0)
        self.assertIsNone(visible_empty['aperture_covered_fraction'])
        self.assertIsNone(visible_empty['area_ratio'])
        self.assertEqual(visible_square['aperture_covered_fraction'], 1)
        self.assertIsNone(absent_empty['piece_inside_fraction'])
        self.assertIsNone(absent_empty['aperture_covered_fraction'])
        self.assertIsNone(absent_empty['iou'])
        self.assertIsNone(absent_square['piece_inside_fraction'])
        self.assertEqual(absent_square['aperture_covered_fraction'], 0)
        self.assertEqual(result['pairs'], [])
        self.assertEqual(result['zero_overlap_pairs'], 1)
        json.dumps(result, allow_nan=False)

    def test_wholly_unknown_domain_is_not_a_negative_observation(self):
        projection = self.project([sheet(0)])
        empty = np.zeros(SHAPE, dtype=bool)
        result = measure_component_apertures(projection, [aperture(empty, known=empty)])
        metric = result['pieces'][0]['apertures'][0]
        self.assertEqual(metric['piece_known_pixels'], 0)
        self.assertEqual(metric['piece_unknown_pixels'], 9)
        self.assertEqual(metric['piece_outside_aperture_pixels'], 0)
        for key in ('piece_inside_fraction', 'aperture_covered_fraction', 'iou', 'area_ratio'):
            self.assertIsNone(metric[key])

    def test_masks_provenance_and_projection_arrays_are_not_mutated(self):
        projection = self.project([sheet(0), sheet(.2)])
        apertures = [aperture(self.mask)]
        saved_apertures, saved_projection = deepcopy(apertures), deepcopy(projection)
        result = measure_component_apertures(projection, apertures)
        self.assertEqual(apertures[0]['provenance'], saved_apertures[0]['provenance'])
        for key in ('mask', 'known_domain'):
            np.testing.assert_array_equal(apertures[0][key], saved_apertures[0][key])
        for original, after in zip(saved_projection['pieces'], projection['pieces']):
            for key in ('pixels', 'face_indices', 'barycentric', 'depth'):
                np.testing.assert_array_equal(original[key], after[key])
        self.assertEqual(result['apertures'][0]['id'], apertures[0]['id'])
        self.assertEqual(result['apertures'][0]['provenance'], apertures[0]['provenance'])

    def test_aperture_capacities_reject_without_silent_truncation(self):
        projection = self.project([sheet(0), sheet(.1), sheet(.2)])
        apertures = [aperture(self.mask, identifier='one'), aperture(self.mask, identifier='two')]
        for field, bound in (('maximum_apertures', 1), ('maximum_mask_pixels', 63),
                             ('maximum_pair_checks', 2), ('maximum_aperture_pair_checks', 5)):
            with self.subTest(field=field), self.assertRaises(ValueError):
                measure_component_apertures(projection, apertures, limits=replace(ProjectionLimits(), **{field: bound}))

    def test_invalid_aperture_arrays_provenance_and_tolerance_are_rejected(self):
        projection = self.project([sheet(0)])
        invalid = []
        for field, value in (('mask', self.mask.astype(np.uint8)), ('known_domain', np.ones((2, 2), bool)),
                             ('provenance', {}), ('id', '')):
            bad = aperture(self.mask)
            bad[field] = value
            invalid.append([bad])
        invalid.append([aperture(self.mask), aperture(self.mask)])
        for bad in invalid:
            with self.subTest(aperture_fields=[list(a) for a in bad]), self.assertRaises(ValueError):
                measure_component_apertures(projection, bad)
        for tolerance in (-1, float('nan'), float('inf')):
            with self.subTest(tolerance=tolerance), self.assertRaises(ValueError):
                measure_component_apertures(projection, [aperture(self.mask)], depth_tolerance=tolerance)


if __name__ == '__main__':
    unittest.main()
