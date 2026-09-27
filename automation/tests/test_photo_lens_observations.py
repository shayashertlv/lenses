import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from reconstruction.camera import Camera
from reconstruction.deform_glb import _read
from reconstruction.lens_asset import _pack_glb
from reconstruction.mesh import TriangleMesh
from reconstruction.optical_asset import write_optical_candidate
from reconstruction.photo_lens_fit import PhotoLensFitPolicy, fit_photo_lens_candidates
from reconstruction.photo_lens_observations import build_photo_lens_observations, project_optical_fields, _prepared_attributes, _stratified_sample
from test_deform_glb import fixture
from test_optical_asset import surface


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class OpticalPhotoProjectionTests(unittest.TestCase):
    def fields(self, depths=(.2,), groups=(0,), camera=None):
        square = np.array([[-.5, -.5, 0], [.5, -.5, 0], [.5, .5, 0], [-.5, .5, 0]])
        vertices = np.concatenate([square + [0, 0, depth] for depth in depths])
        faces = np.concatenate([np.array([[0, 1, 2], [0, 2, 3]]) + 4*i for i in range(len(depths))])
        mesh = TriangleMesh(vertices, faces, [])
        uv = np.tile([[0, 0], [1, 0], [1, 1], [0, 1]], (len(depths), 1))
        return project_optical_fields(mesh, uv, np.tile([0, 0, 1.], (len(vertices), 1)),
            np.repeat(groups, 2), camera or Camera(0, 0, 0, 0, 40, 30, 30), (60, 60), {'center': [0, 0, 0], 'extent': 1})

    def test_intrinsic_height_is_not_image_height_after_roll(self):
        fields = self.fields(camera=Camera(0, 0, 90, 0, 40, 30, 30))
        self.assertAlmostEqual(fields['v'][30, 10], 1.)
        self.assertAlmostEqual(fields['v'][30, 50], 0.)
        self.assertEqual(fields['incidence'][30, 30], 0.)
        np.testing.assert_allclose(fields['reflected'][30, 30], [0, 0, 1])

    def test_stacked_interfaces_are_excluded_but_opaque_stop_is_retained(self):
        for perspective in (0, 1e-300, 1e-16, 1e-10, .5):
            with self.subTest(perspective=perspective):
                camera = Camera(0, 0, 0, perspective, 40, 30, 30)
                stacked = self.fields((.2, -.2), (0, 0), camera)
                self.assertTrue(stacked['stacked'][30, 30])
                self.assertTrue(np.isnan(stacked['v'][30, 30]))
                stopped = self.fields((.2, -.2, 0), (0, 1, -1), camera)
                self.assertFalse(stopped['stacked'][30, 30])
                self.assertEqual(stopped['group'][30, 30], 0)
                self.assertEqual(stopped['rear_weight'][30, 30], 1.)
                self.assertEqual(stopped['incidence'][30, 30], 0.)

    def test_incidence_and_reflection_match_normalized_vertex_shader_inputs(self):
        vertices = np.array([[-1., -1., 0.], [1., -1., 0.], [0., 2., 0.]])
        normals = np.array([[0., 0., 10.], [1., 0., 1.], [0., 0., 1.]])
        mesh = TriangleMesh(vertices, np.array([[0, 1, 2]]), [])
        fields = project_optical_fields(mesh, np.array([[0., 0.], [1., 0.], [.5, 1.]]), normals,
            np.array([0]), Camera(0, 0, 0, 0, 10, 30, 30), (60, 60), {'center': [0, 0, 0], 'extent': 1})
        # This pixel has exactly equal barycentric weights. Independent oracle:
        # runtime normalizes at vertices, then interpolates and normalizes again.
        normal = np.mean(normals / np.linalg.norm(normals, axis=1)[:, None], axis=0)
        normal /= np.linalg.norm(normal)
        incidence = np.degrees(np.arccos(normal[2]))
        self.assertAlmostEqual(fields['incidence'][30, 30], incidence)
        self.assertGreater(incidence, 14.)  # old raw-length interpolation gave4.76deg
        reflection = 2 * normal[2] * normal - np.array([0., 0., 1.])
        np.testing.assert_allclose(fields['reflected'][30, 30], reflection, atol=1e-14)

    def test_frame_in_front_and_back_views_do_not_become_front_lens_samples(self):
        hidden = self.fields((.2, .3), (0, -1))
        self.assertFalse(hidden['visible'][30, 30])
        self.assertTrue(np.isnan(hidden['incidence'][30, 30]))
        back = self.fields(camera=Camera(180, 0, 0, 0, 40, 30, 30))
        self.assertAlmostEqual(back['incidence'][30, 30], 180.)


class OpticalPhotoBindingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        source, self.model = self.folder / 'source.glb', self.folder / 'prepared.glb'
        fixture(source)
        self.export = write_optical_candidate(source, self.model, [{'part_index': 0, 'surfaces': [surface()]}],
            source_sha256=sha(source), provenance={'method': 'test'})
        self.export_path = self.folder / 'export.json'
        self.export_path.write_text(json.dumps(self.export))
        photo = self.folder / 'front.png'
        pixels = np.full((64, 64, 3), 200, np.uint8)
        pixels[20:50, 10:45] = [100, 130, 170]
        Image.fromarray(pixels).save(photo)
        folder = self.folder / 'front' / 'lens'
        folder.mkdir(parents=True)
        mask = np.zeros((64, 64), bool)
        mask[5:60, 5:55] = True
        self.mask_path = folder / 'mask.png'
        Image.fromarray(mask.astype(np.uint8)*255).save(self.mask_path)
        projection = {'status': 'candidate_conditioned_projection', 'candidate_sha256': sha(source),
            'camera': Camera(0, 0, 0, 0, 15, 10, 55).to_dict(), 'working_size': [64, 64],
            'normalization': {'center': [0, 0, 0], 'extent': 1}}
        self.regions = {'candidate_sha256': sha(source), 'photos': [{'id': 'front', 'source': str(photo),
            'source_sha256': sha(photo), 'image_size': [64, 64], 'candidate_projection': projection,
            'regions': [{'id': 'lens', 'directory': 'lens', 'kind': 'candidate_optical_region',
                'prior': {'part_indices': [0]}, 'hypotheses': [{'index': i, 'intersection': {
                    'path': 'mask.png', 'sha256': sha(self.mask_path), 'pixels': int(mask.sum())}} for i in range(3)]}]}]}
        self.regions_path = self.folder / 'regions.json'
        self.regions_path.write_text(json.dumps(self.regions))

    def test_reads_actual_float32_attributes_and_preserves_all_mask_alternatives(self):
        result = build_photo_lens_observations(self.model, self.export_path, self.regions_path)
        observations = result['groups'][0]['observations']
        self.assertEqual(len(observations), 3)
        self.assertGreater(np.isfinite(observations[0]['intrinsic_v']).sum(), 10)
        self.assertTrue(np.isnan(observations[0]['intrinsic_v']).any())
        self.assertTrue(np.isnan(observations[0]['rear_rgb']).all())
        self.assertEqual(observations[0]['source_sha256'], self.regions['photos'][0]['source_sha256'])
        self.assertEqual(result['groups'][0]['surface_binding']['prepared_glb_sha256'], sha(self.model))
        self.assertEqual(observations[0]['provenance']['sampling']['method'], 'mask_bbox_2d_strata_nearest_center_v1')
        self.assertEqual(len(observations[0]['xy']), observations[0]['provenance']['sampling']['selected_pixels'])
        json.dumps(result['report'], allow_nan=False)

    def test_export_attribute_tamper_or_model_lineage_mismatch_refused(self):
        self.export['surfaces'][0]['attribute_sha256']['uv'] = '0'*64
        with self.assertRaisesRegex(ValueError, 'uv bytes'):
            _prepared_attributes(self.model, self.export)
        self.regions['candidate_sha256'] = '0'*64
        self.regions_path.write_text(json.dumps(self.regions))
        with self.assertRaisesRegex(ValueError, 'different candidates'):
            build_photo_lens_observations(self.model, self.export_path, self.regions_path)

    def test_geometry_decodes_the_hashed_bytes_without_a_second_path_read(self):
        expected, *_ = _prepared_attributes(self.model, self.export)
        original, doc, binary = _read(self.model)
        opaque = next(p for p in expected.parts if not p['has_lens_appearance_extension'])
        node = doc['nodes'][opaque['node_index']]
        node['translation'] = [100., 0., 0.]
        changed = _pack_glb(doc, binary)
        original_read = Path.read_bytes
        reads = []
        def substituted(path):
            if path.resolve() == self.model.resolve():
                reads.append(path)
                return original if len(reads) % 2 else changed
            return original_read(path)
        # A path can return A, then B, then A without changing the final hash.
        # All geometry, including opaque stops, must come from captured A.
        with patch.object(Path, 'read_bytes', substituted):
            actual, *_ = _prepared_attributes(self.model, self.export)
        np.testing.assert_array_equal(actual.vertices, expected.vertices)
        self.assertEqual(len(reads), 1)

    def test_generated_samples_feed_the_fitter_without_dropping_unknown_coverage(self):
        group = build_photo_lens_observations(self.model, self.export_path, self.regions_path)['groups'][0]
        result = fit_photo_lens_candidates(group['observations'], surface_binding=group['surface_binding'],
            policy=PhotoLensFitPolicy(families=('uniform_tint',), lighting_families=('constant',),
                roughness_values=(.05,), max_nfev=5, minimum_validation_points_per_photo=1))
        self.assertEqual(result['status'], 'candidates_available')
        self.assertEqual(result['parameter_identification'], 'unmeasured')
        self.assertEqual(result['exploration']['optimization_runs'], 3)
        self.assertEqual(len(result['candidates']), 3)
        self.assertTrue(np.isnan(group['observations'][0]['intrinsic_v']).any())
        json.dumps(result, allow_nan=False)

    def test_changed_mask_bytes_and_path_escape_refused(self):
        self.mask_path.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'mask bytes changed'):
            build_photo_lens_observations(self.model, self.export_path, self.regions_path)
        self.regions['photos'][0]['regions'][0]['hypotheses'][0]['intersection']['path'] = '../../../escape.png'
        self.regions_path.write_text(json.dumps(self.regions))
        with self.assertRaisesRegex(ValueError, 'escapes'):
            build_photo_lens_observations(self.model, self.export_path, self.regions_path)


class SpatialSamplingTests(unittest.TestCase):
    def test_square_population_does_not_collapse_to_diagonal(self):
        rows, cols, receipt = _stratified_sample(np.ones((256, 256), bool), 256)
        self.assertEqual(len(rows), 256)
        self.assertEqual(len(set(rows)), 16)
        self.assertEqual(len(set(cols)), 16)
        self.assertEqual(len(set(zip(rows, cols))), 256)
        self.assertTrue(np.any((rows < 32) & (cols > 192)))
        self.assertTrue(np.any((rows > 192) & (cols < 32)))
        self.assertEqual(receipt['unused_capacity'], 0)
        self.assertEqual(sum(c['population_pixels'] for c in receipt['strata_in_selected_pixel_order']), 256*256)
        again = _stratified_sample(np.ones((256, 256), bool), 256)
        np.testing.assert_array_equal(rows, again[0]);np.testing.assert_array_equal(cols, again[1])
        self.assertEqual(receipt, again[2])

    def test_sparse_cells_and_under_capacity_are_explicit(self):
        mask = np.zeros((80, 240), bool)
        mask[5:15, 5:15] = True;mask[60:75, 200:230] = True
        rows, cols, receipt = _stratified_sample(mask, 37)
        self.assertLessEqual(len(rows), 37)
        self.assertTrue(mask[rows, cols].all())
        self.assertTrue(np.any(cols < 15));self.assertTrue(np.any(cols > 200))
        self.assertEqual(receipt['unused_capacity'], 37-len(rows))
        self.assertGreater(receipt['empty_strata'], 0)
        self.assertEqual(sum(c['population_pixels'] for c in receipt['strata_in_selected_pixel_order']), int(mask.sum()))
        small = np.zeros((4, 7), bool);small[1, 2] = True
        r, c, detail = _stratified_sample(small, 16)
        self.assertEqual((r.tolist(), c.tolist()), ([1], [2]))
        self.assertEqual(detail['method'], 'all_eroded_mask_pixels_within_capacity')
        r, c, detail = _stratified_sample(np.zeros_like(small), 16)
        self.assertEqual(len(r), 0);self.assertEqual(detail['unused_capacity'], 16)


if __name__ == '__main__':
    unittest.main()
