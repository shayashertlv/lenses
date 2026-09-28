"""Regressions for the 2026-09-27 working-tree audit of the legacy reconstruction route.

R1: a segmented_ar_v1 request that names evaluation photos must be refused by
    the shared job entry point before the segmented route is imported.
R2: partial appearance resume hands the strict reader the decoded, pinned
    receipt, never its path.
R3: deforming the selected scene must leave nodes shared with other scenes bound
    to their original meshes.
"""
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from reconstruction import job
from reconstruction import segmented_appearance
from reconstruction.deform_glb import deform_glb, _read_bytes
from reconstruction.lens_asset import _pack_glb
from reconstruction.mesh import load_glb_bytes
from reconstruction.photo_semantics import build_image_manifest, validate_product_hypotheses
from reconstruction.prepare_optical_groups import run_optical_group_preparation
from reconstruction.segmented_appearance import run_segmented_appearance
from test_deform_glb import fixture
from test_photo_semantics import response
from test_prepare_optical_groups import declarations
from test_segmented_appearance import Client


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


class SegmentedRouteReservationTests(unittest.TestCase):
    """R1: the segmented route has no reservation stage, so it must not receive held-out photos."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.rows = []
        for index, view in enumerate(('front', 'left', 'back')):
            path = self.root / (view + '.png')
            Image.new('RGB', (8, 8), (index * 60, 100, 200)).save(path)
            self.rows.append({'id': view, 'view': view, 'path': str(path)})
        self.request_path = self.root / 'request.json'
        self.output = self.root / 'job'
        self.inputs = sorted(p.name for p in self.root.iterdir()) + ['request.json']

    def request(self, **extra):
        value = {'schema_version': 1, 'pipeline': 'segmented_ar_v1', 'product_id': 'offline-check',
                 'photos': self.rows, 'source': {'kind': 'tripo'}, **extra}
        self.request_path.write_text(json.dumps(value), encoding='utf-8')
        return ['--request', str(self.request_path), '--output', str(self.output)]

    def assert_refused(self, argv):
        stderr = io.StringIO()
        with patch('requests.sessions.Session.request', side_effect=AssertionError('Network forbidden')) as network, \
             patch('requests.sessions.Session.post', side_effect=AssertionError('Network forbidden')) as post, \
             patch('reconstruction.segmented_job.main', side_effect=AssertionError('segmented route reached')) as route, \
             patch('reconstruction.segmented_job.validate_request', side_effect=AssertionError('validate_request reached')) as validate, \
             patch.object(job, 'run_job', side_effect=AssertionError('legacy job reached')) as legacy, \
             redirect_stderr(stderr), self.assertRaises(SystemExit) as error:
            job.main(argv)
        self.assertEqual(error.exception.code, 2)
        self.assertIn('segmented_ar_v1 route does not support evaluation reservation', stderr.getvalue())
        self.assertFalse(self.output.exists())
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), sorted(self.inputs))
        self.assertEqual(network.call_count + post.call_count, 0)
        route.assert_not_called()
        validate.assert_not_called()
        legacy.assert_not_called()

    def test_reserved_photo_ids_are_refused_before_capture_or_provider(self):
        self.assert_refused(self.request(reserved_photo_ids=['back']))

    def test_evaluation_photos_are_refused_before_capture_or_provider(self):
        self.assert_refused(self.request(evaluation_photos=[{'id': 'held', 'view': 'back', 'path': self.rows[2]['path']}]))

    def test_clean_segmented_request_still_dispatches_to_the_segmented_route(self):
        argv = self.request()
        with patch('reconstruction.segmented_job.main', return_value={'status': 'routed'}) as route, \
             patch.object(job, 'run_job', side_effect=AssertionError('legacy job reached')):
            self.assertEqual(job.main(argv), {'status': 'routed'})
        route.assert_called_once_with(argv)

    def test_legacy_request_with_reservation_keeps_its_reservation_stage(self):
        # Only the segmented route is intercepted; the legacy job owns reserve_job_evaluation.
        value = {'schema_version': 1, 'photos': self.rows, 'reserved_photo_ids': ['back']}
        self.request_path.write_text(json.dumps(value), encoding='utf-8')
        result = {'status': 'awaiting_initializer', 'quality_verdict': 'unmeasured', 'candidate': None}
        with patch.object(job, 'run_job', return_value=result) as run, \
             patch('reconstruction.segmented_job.main', side_effect=AssertionError('segmented route reached')), \
             redirect_stdout(io.StringIO()):
            job.main(['--request', str(self.request_path), '--output', str(self.output)])
        run.assert_called_once()


class AppearanceResumeReceiptTests(unittest.TestCase):
    """R2: the resume branch decodes and pins export.json before the strict dict reader."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def record(self, receipt_bytes, recorded_sha=None):
        export = self.root / 'export.json'
        export.write_bytes(receipt_bytes)
        candidate = {'candidate_id': 'appearance-test', 'path': str(self.root / 'candidate.glb'), 'sha256': '1' * 64,
                     'export': {'path': str(export), 'sha256': recorded_sha or sha(receipt_bytes)}}
        record = self.root / 'candidate.json'
        record.write_text(json.dumps(candidate), encoding='utf-8')
        return record, candidate

    def test_resume_calls_the_strict_reader_with_the_decoded_receipt(self):
        receipt = {'schema_version': 1, 'method': 'fake', 'receipt_sha256': 'a' * 64, 'groups': []}
        record, candidate = self.record(json.dumps(receipt).encode())
        with patch('reconstruction.optical_group_asset.read_optical_group_candidate') as reader:
            self.assertEqual(segmented_appearance._resume_candidate(record), candidate)
        reader.assert_called_once()
        args, kwargs = reader.call_args
        self.assertEqual(args[0], candidate['path'])
        self.assertIsInstance(args[1], dict)
        self.assertEqual(args[1], receipt)
        self.assertEqual(kwargs, {'expected_sha256': '1' * 64})

    def test_resume_rejects_receipt_bytes_that_differ_from_the_recorded_sha(self):
        receipt = json.dumps({'schema_version': 1, 'method': 'fake'}).encode()
        # Same JSON value, different bytes: the record pins bytes, not meaning.
        record, _ = self.record(receipt + b'\n', recorded_sha=sha(receipt))
        with patch('reconstruction.optical_group_asset.read_optical_group_candidate', side_effect=AssertionError('reader reached')) as reader:
            with self.assertRaisesRegex(ValueError, 'export receipt changed'):
                segmented_appearance._resume_candidate(record)
        reader.assert_not_called()

    def prepared(self, photos):
        source = self.root / 'source.glb'
        fixture(source)
        run_optical_group_preparation(source, self.root / 'prepared', grouping_mode='explicit_declarations', declarations=declarations(source))
        raw = response()
        raw['material_interpretations'][0].update(absorption='clear', coating='ordinary', gradient_direction='none')
        semantic = self.root / 'semantic.json'
        semantic.write_text(json.dumps(validate_product_hypotheses(raw, build_image_manifest(photos))))
        return self.root / 'prepared/report.json', semantic

    def test_interrupted_report_resumes_exported_candidates_without_new_inference(self):
        photo = self.root / 'photo.png'
        Image.new('RGB', (80, 60), 'white').save(photo)
        photos = [{'id': 'front', 'path': str(photo), 'view': 'front'}]
        preparation, semantic = self.prepared(photos)
        client = Client()
        out = self.root / 'appearance'
        first = run_segmented_appearance(preparation, photos, out, semantic_report=semantic, client=client, maximum_candidates=3)
        self.assertTrue(first['candidates'])
        # The crash window: candidates exported and recorded, final report not yet written.
        (out / 'report.json').unlink()
        with patch('reconstruction.optical_group_asset.write_optical_group_candidate', side_effect=AssertionError('must resume exported candidates')):
            again = run_segmented_appearance(preparation, photos, out, semantic_report=semantic, client=client, maximum_candidates=3)
        self.assertEqual(first, again)
        self.assertEqual(client.calls, 1)
        export = Path(first['candidates'][0]['export']['path'])
        export.write_bytes(export.read_bytes() + b'\n')
        (out / 'report.json').unlink()
        with self.assertRaisesRegex(ValueError, 'export receipt changed'):
            run_segmented_appearance(preparation, photos, out, semantic_report=semantic, client=client, maximum_candidates=3)


def shift(points):
    return points + [0.001, 0, 0], np.broadcast_to(np.eye(3), (len(points), 3, 3)).copy()


def scene_vertices(raw, scene):
    _, document, binary = _read_bytes(raw)
    document['scene'] = scene
    return load_glb_bytes(_pack_glb(document, binary)).vertices


class DeformSharedSceneTests(unittest.TestCase):
    """R3: unselected scenes keep their node->mesh bindings when a node is shared."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.source, self.output = Path(folder.name) / 'source.glb', Path(folder.name) / 'candidate.glb'

    def deform(self, mutate=None):
        fixture(self.source, mutate)
        raw = self.source.read_bytes()
        report = deform_glb(self.source, self.output, shift)
        result = self.output.read_bytes()
        self.assertEqual(self.source.read_bytes(), raw)
        return raw, result, report

    def assert_only_selected_scene_moved(self, raw, result):
        unselected = np.linalg.norm(scene_vertices(result, 1) - scene_vertices(raw, 1), axis=1).max()
        self.assertEqual(float(unselected), 0.0)
        np.testing.assert_allclose(scene_vertices(result, 0), scene_vertices(raw, 0) + [0.001, 0, 0], atol=1e-6)

    def test_shared_root_node_keeps_the_unselected_scene_unchanged(self):
        raw, result, report = self.deform(lambda d: d['scenes'].append({'nodes': [0]}))
        self.assert_only_selected_scene_moved(raw, result)
        _, original, _ = _read_bytes(raw)
        _, doc, _ = _read_bytes(result)
        self.assertEqual(doc['nodes'][:3], original['nodes'])
        self.assertEqual(doc['scenes'][1], {'nodes': [0]})
        clones = {c['source_node_index']: c['clone_node_index'] for c in report['cloned_shared_nodes']}
        self.assertEqual(sorted(clones), [0, 1, 2])
        self.assertEqual(doc['scenes'][0]['nodes'], [clones[0]])
        self.assertEqual(doc['nodes'][clones[0]]['children'], [clones[1], clones[2]])
        self.assertEqual(report['changed_node_indices'], [clones[1], clones[2]])
        self.assertEqual(report['new_mesh_instances'], 2)
        self.assertEqual(doc['meshes'][0], original['meshes'][0])

    def test_shared_descendant_node_is_cloned_and_reparented(self):
        raw, result, report = self.deform(lambda d: d['scenes'].append({'nodes': [2]}))
        self.assert_only_selected_scene_moved(raw, result)
        _, original, _ = _read_bytes(raw)
        _, doc, _ = _read_bytes(result)
        self.assertEqual(report['cloned_shared_nodes'], [{'source_node_index': 2, 'clone_node_index': 3}])
        self.assertEqual(doc['nodes'][2], original['nodes'][2])
        self.assertEqual(doc['scenes'][1], {'nodes': [2]})
        self.assertEqual(doc['scenes'][0]['nodes'], [0])
        self.assertEqual(doc['nodes'][0]['children'], [1, 3])
        self.assertEqual({k: v for k, v in doc['nodes'][3].items() if k != 'mesh'},
                         {k: v for k, v in original['nodes'][2].items() if k != 'mesh'})
        self.assertEqual(report['changed_node_indices'], [1, 3])
        self.assertNotEqual(doc['nodes'][1]['mesh'], 0)

    def test_unshared_scene_graph_reports_no_clones(self):
        raw, result, report = self.deform()
        _, original, _ = _read_bytes(raw)
        _, doc, _ = _read_bytes(result)
        self.assertEqual(report['cloned_shared_nodes'], [])
        self.assertEqual(len(doc['nodes']), len(original['nodes']))
        self.assertEqual(report['changed_node_indices'], [1, 2])


if __name__ == '__main__':
    unittest.main()
