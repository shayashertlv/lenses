"""Corpus preparation must not change evidence or manufacture acceptance."""
import json
from pathlib import Path
import tempfile
import unittest

from scripts.job_corpus import prepare


class JobCorpusPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.model = self.folder / 'archived' / 'stages' / 'model.glb'
        self.model.parent.mkdir(parents=True)
        self.model.write_bytes(b'prepared source fixture; job runner separately validates GLB')
        (self.folder / 'front.jpg').write_bytes(b'front photo fixture')
        (self.folder / 'angle.jpg').write_bytes(b'angle photo fixture')
        self.manifest = self.folder / 'corpus.json'
        self.document = {'schema_version': 1, 'cases': [
            {'id': 'design', 'model': 'archived/stages/model.glb',
             'photos': {'front': 'front.jpg', 'angled': 'angle.jpg'}}]}
        self.save()
        self.output = self.folder / 'output'

    def save(self):
        self.manifest.write_text(json.dumps(self.document), encoding='utf-8')

    def test_archive_dimensions_are_provenance_not_application(self):
        archive = self.model.parent.parent / 'job.json'
        archive.write_text(json.dumps({'dimensions': {'frame_width': 127, 'temple_length': 145}}), encoding='utf-8')
        receipt = prepare(self.manifest, self.output, archive_dimensions=True)
        row = receipt['cases'][0]
        self.assertEqual(row['dimension_use'], 'frame_width_applied_as_uniform_scale_to_meters')
        self.assertEqual(row['request']['dimensions_mm'], {'frame_width': 127, 'temple_length': 145})
        self.assertEqual(row['dimensions_source']['path'], str(archive.resolve()))
        self.assertEqual(row['request']['initializer']['kind'], 'existing_glb')
        self.assertNotIn('dimensions_source', row['request'])
        self.assertEqual(receipt['quality_verdict'], 'unmeasured')
        self.assertEqual(prepare(self.manifest, self.output, archive_dimensions=True, resume=True), receipt)

    def test_invalid_later_case_prevents_any_output(self):
        self.document['cases'].append({'id': 'second', 'model': 'missing.glb', 'photos': {'front': 'front.jpg'}})
        self.save()
        with self.assertRaisesRegex(ValueError, 'existing file'):
            prepare(self.manifest, self.output)
        self.assertFalse(self.output.exists())

    def test_changed_source_rejects_resume_and_preserves_receipt(self):
        prepare(self.manifest, self.output)
        prior = (self.output / 'corpus-receipt.json').read_bytes()
        self.model.write_bytes(b'different model')
        with self.assertRaisesRegex(ValueError, 'changed'):
            prepare(self.manifest, self.output, resume=True)
        self.assertEqual((self.output / 'corpus-receipt.json').read_bytes(), prior)

    def test_changed_request_and_snapshot_are_rejected_without_rewriting(self):
        prepare(self.manifest, self.output)
        request = self.output / 'requests' / 'design.json'
        original = request.read_bytes()
        request.write_bytes(b'{}')
        with self.assertRaisesRegex(ValueError, 'request changed'):
            prepare(self.manifest, self.output, resume=True)
        self.assertEqual(request.read_bytes(), b'{}')
        request.write_bytes(original)
        (self.output / 'source-manifest.json').write_bytes(b'{}')
        with self.assertRaisesRegex(ValueError, 'manifest changed'):
            prepare(self.manifest, self.output, resume=True)

    def test_nonempty_output_is_preserved_and_unselected_invalid_paths_are_ignored(self):
        self.document['cases'].append({'id': 'second', 'model': 'missing.glb', 'photos': {'front': 'front.jpg'}})
        self.save()
        receipt = prepare(self.manifest, self.output, selected=['design'])
        self.assertEqual([row['id'] for row in receipt['cases']], ['design'])
        with self.assertRaisesRegex(ValueError, 'Output must be empty'):
            prepare(self.manifest, self.output, selected=['design'])


if __name__ == '__main__':
    unittest.main()
