"""Provider-only bytes belong to the job; legacy task IDs remain resumable."""
import base64
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from reconstruction import job
from reconstruction.initializer import MeshyBackend, resolve_initial_model
from test_initializer import FakeBackend, FakeTransport


class ProviderPhotoSnapshotTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.output = self.root / 'initializer'
        Image.new('RGB', (8, 8), 'white').save(self.root / 'front.png')
        self.back = self.root / 'back.png'
        Image.new('RGB', (8, 8), 'pink').save(self.back)
        self.back_bytes = self.back.read_bytes()
        self.config = {'kind': 'meshy', 'provider_views': [{'id': 'back', 'view': 'back', 'path': 'back.png'}]}
        self.photos = [{'id': 'front', 'view': 'front', 'path': 'front.png'}]

    def resolve(self, **options):
        return resolve_initial_model(self.config, self.root, self.output, self.photos, **options)

    def snapshot(self):
        receipt = json.loads((self.output / 'provider_photos.json').read_bytes())
        return self.output / receipt['photos'][0]['snapshot']

    def test_snapshots_exist_before_post_and_resume_after_original_deletion(self):
        backend = FakeBackend()
        backend.status = 'pending'
        original_submit = backend.submit
        def submit(request):
            self.assertEqual(self.snapshot().read_bytes(), self.back_bytes)
            selected = next(row for row in request['selection']['selected'] if row['id'] == 'back')
            self.assertEqual(Path(selected['path']), self.snapshot())
            self.assertEqual(selected['source_path'], str(self.back))
            return original_submit(request)
        with patch.object(backend, 'submit', side_effect=submit):
            self.assertEqual(self.resolve(backend=backend, allow_submit=True)['status'], 'pending')
        request_bytes = (self.output / 'request.json').read_bytes()
        receipt = json.loads((self.output / 'provider_photos.json').read_bytes())
        self.assertEqual(receipt['request_sha256'], hashlib.sha256(request_bytes).hexdigest())
        self.back.unlink()
        backend.status = 'succeeded'
        result = self.resolve(backend=backend, allow_submit=False)
        self.assertEqual(result['status'], 'complete')
        self.assertEqual((backend.submits, backend.retrieves), (1, 2))
        self.assertEqual(request_bytes, (self.output / 'request.json').read_bytes())
        self.assertEqual(result['provider_photo_snapshots']['unavailable_legacy_photos'], [])
        row = next(row for row in result['selected'] if row['id'] == 'back')
        self.assertEqual(row['source_availability'], 'owned_exact_snapshot')
        self.assertEqual(Path(row['path']).read_bytes(), self.back_bytes)

    def test_offline_preparation_can_later_submit_snapshots_without_original(self):
        self.assertEqual(self.resolve()['status'], 'awaiting_backend')
        self.back.unlink()
        transport = FakeTransport()
        self.assertEqual(self.resolve(backend=MeshyBackend(transport), allow_submit=True)['status'], 'complete')
        images = transport.posts[0][1]['image_urls']
        self.assertIn(self.back_bytes, [base64.b64decode(item.split(',', 1)[1]) for item in images])

    def test_changed_original_or_corrupted_snapshot_fails_before_poll(self):
        for corrupt_snapshot in (False, True):
            with self.subTest(corrupt_snapshot=corrupt_snapshot):
                self.output = self.root / ('snapshot' if corrupt_snapshot else 'original')
                self.back.write_bytes(self.back_bytes)
                backend = FakeBackend(); backend.status = 'pending'
                self.resolve(backend=backend, allow_submit=True)
                (self.snapshot() if corrupt_snapshot else self.back).write_bytes(b'changed bytes')
                with self.assertRaises(ValueError):
                    self.resolve(backend=backend, allow_submit=False)
                self.assertEqual((backend.submits, backend.retrieves), (1, 1))

    def test_missing_new_snapshot_is_not_silently_treated_as_legacy(self):
        backend = FakeBackend(); backend.status = 'pending'
        self.resolve(backend=backend, allow_submit=True)
        self.snapshot().unlink()
        self.back.unlink()
        with self.assertRaises((ValueError, FileNotFoundError)):
            self.resolve(backend=backend, allow_submit=False)
        self.assertEqual(backend.retrieves, 1)

    def test_legacy_task_polls_without_unsnapshotted_original_and_records_gap(self):
        backend = FakeBackend(); backend.status = 'pending'
        self.resolve(backend=backend, allow_submit=True)
        request_bytes = (self.output / 'request.json').read_bytes()
        # Simulate the older format: the request and provider task receipts are
        # identical; only this new snapshot facility did not exist.
        self.snapshot().unlink()
        (self.output / 'provider_photos.json').unlink()
        self.back.unlink()
        backend.status = 'succeeded'
        result = self.resolve(backend=backend, allow_submit=False)
        self.assertEqual(result['status'], 'complete')
        self.assertEqual((backend.submits, backend.retrieves), (1, 2))
        self.assertEqual(request_bytes, (self.output / 'request.json').read_bytes())
        self.assertEqual(result['provider_photo_snapshots']['unavailable_legacy_photos'], ['back'])
        row = next(row for row in result['selected'] if row['id'] == 'back')
        self.assertEqual(row['source_availability'], 'missing_legacy_unsnapshotted_input')
        self.assertFalse(self.snapshot().exists())

    def test_deleted_original_does_not_allow_changed_view_label_or_path(self):
        backend = FakeBackend(); backend.status = 'pending'
        self.resolve(backend=backend, allow_submit=True)
        self.back.unlink()
        for change in ({'view': 'left'}, {'path': 'different.png'}):
            original = dict(self.config['provider_views'][0])
            self.config['provider_views'][0].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.resolve(backend=backend, allow_submit=False)
            self.config['provider_views'][0] = original
        self.assertEqual(backend.retrieves, 1)


class SemanticPhotoCapacityTests(unittest.TestCase):
    def test_excess_fit_or_provider_views_fail_before_inputs_or_paid_initializer(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for fitting, provider in ((13, 0), (2, 11)):
                request = root / 'request.json'
                request.write_text(json.dumps({'schema_version': 1,
                    'photos': [{'path': f'fit-{i}.png'} for i in range(fitting)],
                    'initializer': {'kind': 'meshy', 'provider_views': [{'path': f'extra-{i}.png'} for i in range(provider)]}}))
                output = root / f'job-{fitting}-{provider}'
                with self.subTest(fitting=fitting, provider=provider), \
                     patch.object(job, 'resolve_initial_model') as initializer, \
                     patch.object(job, 'prepare_input_bundle') as intake, \
                     self.assertRaisesRegex(ValueError, 'at most twelve'):
                    job.run_job(request, output, appearance_mode='semantic_ar_v1',
                                semantic_client=object(), optical_grouping=job.INFERRED_GROUPING,
                                backend=object(), allow_submit=True)
                initializer.assert_not_called()
                intake.assert_not_called()
                self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
