"""Fixed region policy, exact bindings and negative controls without a neural net."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from reconstruction.camera import Camera
from reconstruction.mesh import TriangleMesh
from reconstruction.region_proposals import (_camera_regions, _lens_parts, _native_grid, _prompts,
                                               run_region_stage)
from test_refine_photos import _write_mesh


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class BoxEngine:
    def __init__(self):
        self.calls = []

    def describe(self):
        return {'engine': 'test_box_alternatives_v1', 'semantic_identity': 'unmeasured'}

    def predict(self, rgb, prompts):
        self.calls.append((rgb.shape, prompts))
        result = []
        for prompt in prompts:
            mask = np.zeros(rgb.shape[:2], dtype=bool)
            x0, y0, x1, y1 = np.round(prompt['bbox_xyxy']).astype(int)
            mask[y0:y1, x0:x1] = True
            # Highest model score intentionally belongs to BACKGROUND. All
            # alternatives, including a duplicate, must survive without ranking.
            result.append([{'mask': ~mask, 'predicted_quality': .999},
                           {'mask': mask, 'predicted_quality': .4},
                           {'mask': mask.copy(), 'predicted_quality': .1}])
        return result


class RegionProposalTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.pixels = np.full((100, 200, 3), 255, dtype=np.uint8)
        self.pixels[30:70, 40:160] = [35, 80, 140]
        self.path = self.root / 'photo.png'
        Image.fromarray(self.pixels).save(self.path)
        self.photo = {'id': 'front', 'path': str(self.path), 'sha256': sha(self.path), 'view': 'front'}

    def run_stage(self, **kwargs):
        return run_region_stage([self.photo], self.root / 'out', **kwargs)

    def test_all_alternatives_and_conditional_signal_preserved(self):
        engine = BoxEngine()
        report = self.run_stage(engine=engine)
        self.assertFalse(report['accepted'])
        self.assertEqual(report['quality_verdict'], 'unmeasured')
        self.assertEqual(len(engine.calls), 1)
        self.assertEqual(len(engine.calls[0][1]), 5)
        region = report['photos'][0]['regions'][0]
        self.assertEqual([len(v) for v in region['alternatives']], [3] * 5)
        self.assertEqual(len(region['hypotheses']), 3)
        self.assertIsNone(region['selected_hypothesis'])
        self.assertTrue(region['hypotheses'][0]['diagnostics']['touches_image_border'])
        self.assertEqual(region['hypotheses'][1]['intersection']['sha256'], region['hypotheses'][2]['intersection']['sha256'])
        path = self.root / 'out/front/contrast-object' / region['hypotheses'][1]['appearance']['path']
        appearance = json.loads(path.read_text())
        self.assertGreater(appearance['signal']['count'], 0)
        self.assertEqual(appearance['source_sha256'], self.photo['sha256'])
        self.assertIsNone(appearance['intrinsic_height_bins'])

    def test_white_image_has_no_foreground_or_invented_absence(self):
        Image.fromarray(np.full_like(self.pixels, 255)).save(self.path)
        self.photo['sha256'] = sha(self.path)
        engine = BoxEngine()
        report = self.run_stage(engine=engine)
        self.assertEqual(engine.calls, [])
        self.assertEqual(report['photos'][0]['status'], 'no_supported_region_prior')
        self.assertEqual(report['photos'][0]['regions'], [])
        self.assertEqual(report['status'], 'no_supported_region_hypotheses')
        self.assertFalse(report['accepted'])

    def test_unrelated_rectangle_never_claimed_to_be_glasses(self):
        report = self.run_stage(engine=BoxEngine())
        region = report['photos'][0]['regions'][0]
        self.assertEqual(region['prior']['semantic_identity'], 'unknown_object')
        self.assertEqual(region['identity'], 'unverified')
        self.assertEqual(region['semantic_coverage'], 'unmeasured')

    def test_missing_engine_does_not_fabricate_predictions(self):
        report = self.run_stage()
        self.assertEqual(report['status'], 'engine_unavailable')
        self.assertEqual(report['photos'][0]['regions'][0]['hypotheses'], [])

    def test_alpha_not_composited_for_sam(self):
        rgba = np.concatenate((self.pixels, np.full((*self.pixels.shape[:2], 1), 255, dtype=np.uint8)), axis=2)
        rgba[0, 0, 3] = 0
        Image.fromarray(rgba).save(self.path)
        self.photo['sha256'] = sha(self.path)
        engine = BoxEngine()
        report = self.run_stage(engine=engine)
        self.assertEqual(engine.calls, [])
        self.assertEqual(report['photos'][0]['status'], 'nonopaque_input_region_inference_unsupported')

    def test_input_output_bindings_and_engine_mutation_fail(self):
        photo = {**self.photo, 'sha256': '0' * 64}
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            run_region_stage([photo], self.root / 'out')
        self.run_stage()
        with self.assertRaisesRegex(ValueError, 'empty'):
            self.run_stage()
        with self.assertRaisesRegex(ValueError, 'unique safe'):
            run_region_stage([{**self.photo, 'id': '../escape'}], self.root / 'new')
        engine = BoxEngine()
        with patch.object(engine, 'describe', side_effect=[{'v': 1}, {'v': 2}]):
            with self.assertRaisesRegex(ValueError, 'provenance changed'):
                run_region_stage([self.photo], self.root / 'changed', engine=engine)

    def test_incomplete_engine_output_rejected(self):
        engine = BoxEngine()
        with patch.object(engine, 'predict', return_value=[]):
            with self.assertRaisesRegex(ValueError, 'prompt count'):
                self.run_stage(engine=engine)

    def test_mutable_engine_identity_cannot_evade_snapshot(self):
        engine = BoxEngine()
        identity = {'version': 1}
        predict = engine.predict
        def mutating(rgb, prompts):
            identity['version'] = 2
            return predict(rgb, prompts)
        with patch.object(engine, 'describe', return_value=identity), patch.object(engine, 'predict', side_effect=mutating):
            with self.assertRaisesRegex(ValueError, 'provenance changed'):
                self.run_stage(engine=engine)

    def test_unconverted_profile_not_silently_treated_as_srgb(self):
        Image.fromarray(self.pixels).save(self.path, icc_profile=b'profile requires validation and conversion')
        self.photo['sha256'] = sha(self.path)
        with self.assertRaisesRegex(ValueError, 'ICC profiles'):
            self.run_stage(engine=BoxEngine())

    def test_boolean_model_quality_rejected(self):
        engine = BoxEngine()
        predict = engine.predict
        def boolean_quality(rgb, prompts):
            result = predict(rgb, prompts)
            result[0][0]['predicted_quality'] = True
            return result
        with patch.object(engine, 'predict', side_effect=boolean_quality):
            with self.assertRaisesRegex(ValueError, 'predicted quality'):
                self.run_stage(engine=engine)

    def test_equal_iou_associations_cannot_depend_on_decoder_order(self):
        base = np.zeros((100, 200), dtype=bool)
        base[30:70, 40:160] = True
        left, right = base.copy(), base.copy()
        left[:, 40:46] = False
        right[:, 154:160] = False
        def predictions(order):
            central = [{'mask': base.copy(), 'predicted_quality': .5} for _ in range(3)]
            other = [{'mask': item.copy(), 'predicted_quality': .5} for item in order]
            return [central, other, other, other, other]
        results = []
        for index, order in enumerate(([left, right, left], [right, left, left])):
            engine = BoxEngine()
            with patch.object(engine, 'predict', return_value=predictions(order)):
                results.append(run_region_stage([self.photo], self.root / str(index), engine=engine))
        first = results[0]['photos'][0]['regions'][0]['hypotheses'][0]
        second = results[1]['photos'][0]['regions'][0]['hypotheses'][0]
        self.assertTrue(first['diagnostics']['stable_under_prompts'])
        self.assertTrue(first['diagnostics']['association_ambiguous'])
        self.assertEqual(first['intersection']['sha256'], second['intersection']['sha256'])
        self.assertEqual(first['appearance']['sha256'], second['appearance']['sha256'])

    def test_prompts_bounded_and_subpixel_mapping_explicit(self):
        mask = np.zeros((13, 17), dtype=bool)
        mask[:2, :3] = True
        for prompt in _prompts(mask):
            x0, y0, x1, y1 = prompt['bbox_xyxy']
            self.assertTrue(0 <= x0 <= x1 - 1 <= 16)
            self.assertTrue(0 <= y0 <= y1 - 1 <= 12)
        field = np.arange(6).reshape(2, 3)
        resized = _native_grid(field, (4, 6))
        np.testing.assert_array_equal(resized, np.repeat(np.repeat(field, 2, axis=0), 2, axis=1))

    def test_optical_identity_does_not_depend_only_on_transmission(self):
        mesh = TriangleMesh(np.zeros((3, 3)), np.array([[0, 1, 2]]), [
            {'declared_role': 'lens', 'transmission': 0},
            {'name': 'Prizm optical shield', 'transmission': 0},
            {'has_lens_appearance_extension': True, 'transmission': 0},
            {'name': 'frame', 'transmission': 1},
            {'name': 'unclassified', 'transmission': 0}])
        selected, ledger = _lens_parts(mesh)
        self.assertEqual(selected, [0, 1, 2, 3])
        self.assertTrue(all(row['photographic_component_identity'] == 'unverified' for row in ledger))

    def fixture_camera(self):
        vertices = np.array([[-.4, -.2, 0], [.4, -.2, 0], [.4, .2, 0], [-.4, .2, 0]])
        mesh = TriangleMesh(vertices, np.array([[0, 1, 2], [0, 2, 3]]),
                            [{'declared_role': 'lens', 'face_start': 0, 'face_count': 2}])
        camera = Camera(0, 0, 35, 0, 100, 100, 50)
        report = {'source_sha256': 'a' * 64, 'normalization': {'center': [0, 0, 0], 'extent': 1},
                  'views': [{'view_id': 'front', 'source_sha256': self.photo['sha256'],
                             'image_size_original': [200, 100], 'image_size_working': [200, 100],
                             'camera_fit': {'camera': camera.to_dict()}}]}
        return mesh, report

    def test_rolled_camera_height_is_candidate_coordinate_not_image_row(self):
        mesh, report = self.fixture_camera()
        regions, metadata = _camera_regions(mesh, 'a'*64, report, self.photo, (100, 200))
        self.assertEqual(len(regions), 1)
        region = regions[0]
        self.assertIn('height_proxy', region['coordinate_provenance']['method'])
        # A rolled camera gives different authored heights along one image row.
        finite = np.isfinite(region['height'][50])
        self.assertGreater(np.ptp(region['height'][50, finite]), .3)
        np.testing.assert_allclose(region['incidence'][region['mask']], 0.)
        self.assertTrue(np.isnan(region['height'][~region['mask']]).all())

    def test_mismatched_camera_model_photo_and_grid_rejected(self):
        mesh, report = self.fixture_camera()
        with self.assertRaisesRegex(ValueError, 'candidate model'):
            _camera_regions(mesh, 'b'*64, report, self.photo, (100, 200))
        with self.assertRaisesRegex(ValueError, 'photograph'):
            _camera_regions(mesh, 'a'*64, report, {**self.photo, 'sha256': 'c'*64}, (100, 200))
        with self.assertRaisesRegex(ValueError, 'pixel grid'):
            _camera_regions(mesh, 'a'*64, report, self.photo, (101, 200))

    def test_exported_candidate_keeps_original_normalization_and_camera(self):
        mesh, report = self.fixture_camera()
        report.update(status='proposal_exported', rerender_nonregression=True, export={'output_sha256': 'b'*64})
        original, _ = _camera_regions(mesh, 'b'*64, report, self.photo, (100, 200))
        mesh.vertices += np.array([.1, 0, 0])
        shifted, metadata = _camera_regions(mesh, 'b'*64, report, self.photo, (100, 200))
        self.assertEqual(metadata['candidate_binding'], 'exported_proposal_original_camera')
        self.assertEqual(metadata['normalization'], report['normalization'])
        self.assertGreater(np.nonzero(shifted[0]['mask'])[1].mean() - np.nonzero(original[0]['mask'])[1].mean(), 7.)

    def test_generic_unlabeled_geometry_supplies_no_optical_identity(self):
        mesh, report = self.fixture_camera()
        mesh.parts[0]['declared_role'] = None
        regions, metadata = _camera_regions(mesh, 'a'*64, report, self.photo, (100, 200))
        self.assertEqual(regions, [])
        self.assertEqual(metadata['optical_regions_retained'], 0)


if __name__ == '__main__':
    unittest.main()
