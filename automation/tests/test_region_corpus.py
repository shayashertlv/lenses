"""Corpus evidence integrity; actual region stage exercised without SAM weights."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from qa.region_corpus import EXPECTED, execute, prepare, sha
from reconstruction.mesh import TriangleMesh
from test_refine_photos import _write_mesh


class RegionCorpusTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.model = self.root / 'source.glb'
        _write_mesh(self.model, TriangleMesh(np.array([[-1., -1, 0], [1, -1, 0], [0, 1, .1]]), np.array([[0, 1, 2]]), []))
        for name in ('front', 'angled'):
            pixels = np.full((100, 200, 3), 255, np.uint8)
            pixels[35:65, 40:160] = [20, 50, 100]
            Image.fromarray(pixels).save(self.root / f'{name}.png')
        self.manifest, self.reports, self.output = self.root / 'corpus.json', self.root / 'reports', self.root / 'output'
        self.manifest.write_text(json.dumps({'schema_version': 1, 'cases': [
            {'id': name, 'model': 'source.glb', 'photos': {'front': 'front.png', 'angled': 'angled.png'}} for name in EXPECTED]}))
        for name in EXPECTED:
            folder = self.reports / name
            folder.mkdir(parents=True)
            (folder / 'report.json').write_text(json.dumps({'source_sha256': sha(self.model), 'views': [
                {'view_id': view, 'source_sha256': sha(self.root / f'{view}.png')} for view in ('front', 'angled')]}))
        # Other agents can update unrelated reconstruction modules concurrently;
        # this test's concern is its fixture bytes and stage outputs.
        self.identity = patch('qa.region_corpus.implementation', return_value={'test_fixture': 'frozen'})
        self.identity.start()
        self.addCleanup(self.identity.stop)

    def test_wrong_original_camera_hash_rejects_before_any_write(self):
        path = self.reports / EXPECTED[-1] / 'report.json'
        report = json.loads(path.read_bytes())
        report['source_sha256'] = '0'*64
        path.write_text(json.dumps(report))
        with self.assertRaisesRegex(ValueError, 'ORIGINAL model'):
            prepare(self.manifest, self.reports, self.output)
        self.assertFalse(self.output.exists())

    def test_nonempty_output_and_modified_control_are_preserved(self):
        receipt = prepare(self.manifest, self.reports, self.output, selected=('rayban',))
        pinned = (self.output / 'corpus-receipt.json').read_bytes()
        with self.assertRaisesRegex(ValueError, 'not empty'):
            prepare(self.manifest, self.reports, self.output, selected=('rayban',))
        control = Path(receipt['cases'][-1]['photos'][0]['path'])
        control.write_bytes(b'tampered control')
        with self.assertRaisesRegex(ValueError, 'Source changed'):
            prepare(self.manifest, self.reports, self.output, selected=('rayban',), resume=True)
        self.assertEqual(control.read_bytes(), b'tampered control')
        self.assertEqual((self.output / 'corpus-receipt.json').read_bytes(), pinned)

    def test_actual_stage_partial_coverage_resume_and_output_tamper(self):
        receipt = prepare(self.manifest, self.reports, self.output, selected=('rayban',))
        sources = {p: p.read_bytes() for p in (self.model, self.root/'front.png', self.root/'angled.png')}
        result = execute(receipt, self.output)
        self.assertTrue(result['execution_complete'])
        self.assertFalse(result['full_corpus_complete'])
        self.assertFalse(result['model_inference_complete'])
        self.assertFalse(result['accepted'])
        self.assertEqual(result['quality_verdict'], 'unmeasured')
        self.assertEqual(len(result['cases'][0]['summary']['views']), 2)
        white = next(row for row in result['cases'] if row['id'] == 'white-control')
        self.assertEqual(white['summary']['views'][0]['regions'], 0)
        artifacts = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in (self.output/'cases').rglob('*') if p.is_file()}
        again = execute(receipt, self.output)
        self.assertTrue(again['execution_complete'])
        self.assertEqual(artifacts, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in artifacts})
        self.assertEqual(sources, {p: p.read_bytes() for p in sources})
        stage_report = self.output / result['cases'][0]['attempt'] / 'report.json'
        stage_report.write_bytes(b'{}')
        with self.assertRaisesRegex(ValueError, 'artifacts changed'):
            execute(receipt, self.output)
        self.assertEqual(stage_report.read_bytes(), b'{}')

    def test_incomplete_attempt_never_overwritten(self):
        receipt = prepare(self.manifest, self.reports, self.output, selected=('rayban',))
        abandoned = self.output/'cases/rayban/attempt-001'
        abandoned.mkdir(parents=True)
        sentinel = abandoned/'partial.txt'
        sentinel.write_bytes(b'interrupted evidence')
        result = execute(receipt, self.output)
        self.assertEqual(result['cases'][0]['attempt'], 'cases/rayban/attempt-002')
        self.assertEqual(sentinel.read_bytes(), b'interrupted evidence')


if __name__ == '__main__':
    unittest.main()
