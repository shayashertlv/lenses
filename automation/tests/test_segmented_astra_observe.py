import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from reconstruction import segmented_astra_observe as observer
from reconstruction.mesh import TriangleMesh
from test_surface_transfer import build_generation


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class ObserverTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.assets = []
        for name in ('baseline', 'candidate'):
            model = self.root / (name+'.glb'); model.write_bytes(name.encode())
            receipt = self.root / (name+'.json'); receipt.write_text('{}')
            self.assets.append({'path': str(model), 'sha256': sha(model),
                                'export': {'path': str(receipt), 'sha256': sha(receipt)}})
        photo = self.root / 'source.png'; Image.new('RGB', (120, 80), 'white').save(photo)
        self.photos = [{'id': 'front', 'view': 'front', 'path': str(photo), 'sha256': sha(photo)}]
        self.vertices = np.array([[-.07, -.035, 0], [.07, -.035, 0], [.07, .03, 0], [-.07, .03, -.1]])
        self.mesh = TriangleMesh(self.vertices, np.array([[0, 1, 2], [0, 2, 3]]), [])
        self.fingerprint = {'schema_version': 1, 'sha256': 'a'*64, 'files': {'renderer.ts': 'b'*64}}
        self.mode = None

    def verified(self, value, label):
        path, raw = observer._read_pin(value, label)
        observer._read_pin(value['export'], label+' receipt')
        return value, self.mesh

    def fake_renderer(self, manifest_path, output):
        manifest = json.loads(manifest_path.read_text()); output.mkdir()
        matrix = np.eye(4).ravel(order='F').tolist()
        report = {'status': 'inspected', 'source_snapshot_stable': True,
                  'manifest_sha256': sha(manifest_path), 'cases': [], 'errors': [],
                  'failed_responses': [], 'blocked_external': [], 'ar_views': manifest['ar_views'],
                  'background_fixture': 'controlled-checker',
                  'environments': [{**e, 'explicit': True} for e in manifest['environments']]}
        for entry in manifest['cases']:
            case = {'id': entry['id'], 'model_sha256': entry['model_sha256'], 'status': 'runtime_compatible', 'renders': []}
            for env in manifest['environments']:
                for view in manifest['ar_views']:
                    name = entry['id']+'-'+env['id']+'-'+view['id']+'.png'
                    image = output / name; Image.new('RGB', (720, 480), 'navy' if entry['id']=='baseline' else 'green').save(image)
                    case['renders'].append({'filename': name, 'sha256': sha(image), 'view': view['id'],
                        'mode': 'actual-ar', 'environment': env['id'], 'background': 'controlled-checker',
                        'environment_explicit': True,
                        'requested_pose': {k: view.get(k, 0) for k in ('yaw_degrees', 'pitch_degrees', 'roll_degrees')},
                        'inspection': ({'type': 'rear_asset_inspection', 'asset_rotation_y_degrees': 180, 'synthetic_face_occluders_hidden': True}
                                       if view.get('type') == 'asset-back' else {'type': 'synthetic_front_face_pose'}),
                        'camera': {'view_matrix': matrix, 'projection_matrix': matrix, 'width': 720, 'height': 480, 'matrix_layout': 'column-major'},
                        'spatial': {'asset_to_world': matrix, 'matrix_layout': 'column-major'}})
            report['cases'].append(case)
        if self.mode == 'missing':
            report['cases'][1]['renders'].pop()
        elif self.mode == 'tampered_image':
            (output/report['cases'][0]['renders'][0]['filename']).write_bytes(b'changed')
        elif self.mode == 'wrong_asset':
            report['cases'][1]['model_sha256'] = 'f'*64
        elif self.mode == 'bad_camera':
            report['cases'][0]['renders'][0]['camera']['projection_matrix'] = [0]*15
        elif self.mode == 'changed_source':
            Path(self.assets[1]['path']).write_bytes(b'changed during rendering')
        elif self.mode == 'network_failure':
            report['failed_responses'] = [{'status': 404}]
        elif self.mode == 'wrong_environment':
            report['environments'][0]['intensity'] = .2
        elif self.mode == 'wrong_pose':
            report['cases'][1]['renders'][0]['requested_pose']['yaw_degrees'] = 15
        elif self.mode == 'mismatched_camera':
            report['cases'][1]['renders'][0]['camera']['width'] = 721
            image = output/report['cases'][1]['renders'][0]['filename']
            Image.new('RGB', (721, 480), 'green').save(image)
            report['cases'][1]['renders'][0]['sha256'] = sha(image)
        elif self.mode == 'wrong_back':
            report['cases'][0]['renders'][3]['inspection']['asset_rotation_y_degrees'] = 0
        elif self.mode == 'singular_camera':
            report['cases'][0]['renders'][0]['camera']['projection_matrix'] = [0]*16
        (output/'report.json').write_text(json.dumps(report))

    def run_observer(self, name='out', **kwargs):
        with patch.object(observer, '_verify_optical', side_effect=self.verified), \
             patch.object(observer, '_run_ar', side_effect=self.fake_renderer), \
             patch.object(observer, 'renderer_fingerprint', return_value=self.fingerprint):
            return observer.observe_candidate(self.assets[1], self.assets[0], self.photos,
                                              self.root/name, product_id='fixture', **kwargs)

    def test_complete_actual_renderer_matrix_is_pinned_and_not_quality_acceptance(self):
        result = self.run_observer()
        self.assertEqual(result['status'], 'runtime_compatible')
        self.assertFalse(result['accepted'])
        self.assertEqual(result['quality_verdict'], 'unmeasured')
        self.assertEqual(len(result['render_images']), 30)
        self.assertEqual(len(result['images']), 3)
        self.assertEqual(result['candidate_sha256'], self.assets[1]['sha256'])
        for image in result['images']:
            self.assertEqual(image['sha256'], sha(image['path']))
            self.assertEqual(image['rows'], ['baseline', 'candidate'])
        self.assertEqual(result['render_report']['sha256'], sha(result['render_report']['path']))
        with self.assertRaisesRegex(ValueError, 'new or empty'):
            self.run_observer()

    def test_missing_tampered_wrong_asset_camera_and_network_fail_closed(self):
        for mode, reason in (('missing', 'Incomplete'), ('tampered_image', 'render image bytes'),
                             ('wrong_asset', 'case model'), ('bad_camera', 'camera matrix'),
                             ('network_failure', 'integrity/runtime'), ('wrong_environment', 'environment policy'),
                             ('wrong_pose', 'row pose'), ('mismatched_camera', 'identical camera'),
                             ('wrong_back', 'rear inspection'), ('singular_camera', 'camera matrix')):
            with self.subTest(mode=mode):
                self.mode = mode
                with self.assertRaisesRegex(ValueError, reason):
                    self.run_observer(mode)
                self.assertTrue((self.root/mode/'failure.json').is_file())
                self.assertFalse((self.root/mode/'report.json').exists())

    def test_changed_input_after_render_is_rejected(self):
        self.mode = 'changed_source'
        with self.assertRaisesRegex(ValueError, 'observation input bytes'):
            self.run_observer()

    def test_renderer_changed_during_observation_is_rejected(self):
        with patch.object(observer, '_verify_optical', side_effect=self.verified), \
             patch.object(observer, '_run_ar', side_effect=self.fake_renderer), \
             patch.object(observer, 'renderer_fingerprint', side_effect=[self.fingerprint, {'sha256': 'changed'}]):
            with self.assertRaisesRegex(ValueError, 'changed during observation'):
                observer.observe_candidate(self.assets[1], self.assets[0], self.photos, self.root/'out', product_id='fixture')

    def test_stale_photo_rejected_before_renderer_or_output(self):
        Path(self.photos[0]['path']).write_bytes(b'changed')
        with patch.object(observer, '_verify_optical', side_effect=self.verified), patch.object(observer, '_run_ar') as run:
            with self.assertRaisesRegex(ValueError, 'photo bytes'):
                observer.observe_candidate(self.assets[1], self.assets[0], self.photos, self.root/'out', product_id='fixture')
            run.assert_not_called()
        self.assertFalse((self.root/'out').exists())

    def source(self):
        path = self.root/'parts.glb'
        path.write_bytes(build_generation(self.vertices.tolist(), [[0, 0], [1, 0], [1, 1], [0, 1]], [[0, 1, 2], [0, 2, 3]]))
        return path

    def test_part_labels_preserve_source_face_ids_and_focus_uses_recorded_camera(self):
        source = self.source()
        result = self.run_observer(part_source=source, groups={'optical-0': [0]}, focus={'part_ids': [0], 'padding_fraction': .2})
        self.assertEqual(len(result['images']), 7)
        parts = json.loads(Path(result['part_evidence']['path']).read_text())
        self.assertEqual(parts['parts'][0]['stable_binding']['source_sha256'], sha(source))
        self.assertFalse(parts['parts'][0]['role_verified'])
        self.assertEqual(len(parts['cameras']), 4)
        for camera in parts['cameras']:
            with np.load(camera['raster']['path']) as data:
                self.assertTrue(set(np.unique(data['face_index'])) <= {-1, 0, 1})
                self.assertTrue(set(np.unique(data['part_index'])) <= {-1, 0})
                for label in camera['visible_parts']:
                    x, y = label['anchor_pixel_xy']
                    self.assertEqual(data['part_index'][y, x], label['part_id'])
        self.assertEqual(sum(c['focus'] for c in result['crops']), 15)
        # The fixture uses identity projection: this checks actual matrix use,
        # not an assumed screen center guessed by the observer.
        render = {'camera': {'projection_matrix': np.eye(4).ravel().tolist(), 'view_matrix': np.eye(4).ravel().tolist(), 'width': 720, 'height': 480},
                  'spatial': {'asset_to_world': np.eye(4).ravel().tolist()}}
        lo, hi = observer._project_bounds([[-.5, -.5, 0], [.5, .5, 0]], render)
        np.testing.assert_allclose(lo, [180, 120]); np.testing.assert_allclose(hi, [540, 360])

    def test_invalid_groups_and_focus_are_refused_before_ar(self):
        source = self.source()
        for i, kwargs in enumerate(({'groups': {'a': [0], 'b': [0]}}, {'focus': {'part_ids': [99]}},
                                    {'focus': {'world_bounds_m': [[0, 0, 0], [1, 1, 1]]}},
                                    {'focus': {'part_ids': [0], 'padding_fraction': float('inf')}})):
            with self.subTest(kwargs=kwargs), patch.object(observer, '_run_ar') as run:
                with self.assertRaises(ValueError):
                    self.run_observer(str(i), part_source=source, **kwargs)
                run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
