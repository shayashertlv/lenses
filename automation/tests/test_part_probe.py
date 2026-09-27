"""No-network paid-call safety and native-parts settings contract."""
from pathlib import Path
import tempfile
import unittest

import requests

from qa import part_probe as probe
from qa import provider_benchmark as base


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def api(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class PartProbeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.source = self.root / 'source'
        self.output = self.root / 'output'
        for product in ('oakley', 'miu'):
            directory = self.source / 'runs' / f'{product}-tripo'
            base.write_json(directory / 'request.json', dict(settings=base.SETTINGS['tripo'], photos=[]))
            base.write_json(directory / 'submission.json', dict(http=200, response={
                'code': 0, 'data': {'task_id': f'original_{product}'}}))
            base.write_json(directory / 'uploads.json', {'photo_hash': 'file_existing'})
        probe.prepare(self.output, self.source)

    def test_native_parts_are_untextured_same_geometry_seed_and_no_conflicting_options(self):
        plan = base.read_json(self.output / 'prepared.json')
        self.assertEqual(plan['maximum_paid_calls'], 4)
        self.assertEqual(plan['estimated_usd'], 2.0)
        for product in ('oakley', 'miu'):
            directory = self.output / 'runs' / f'{product}-native-parts'
            settings = base.read_json(directory / 'request.json')['settings']
            self.assertTrue(settings['generate_parts'])
            self.assertFalse(settings['texture'])
            self.assertFalse(settings['pbr'])
            self.assertEqual(settings['model_seed'], base.SETTINGS['tripo']['model_seed'])
            self.assertEqual(settings['geometry_quality'], 'detailed')
            for incompatible in ('quad', 'smart_low_poly', 'face_limit', 'texture_version'):
                self.assertNotIn(incompatible, settings)
            self.assertEqual(base.read_json(directory / 'uploads.json'), {'photo_hash': 'file_existing'})

    def test_segmentation_has_no_generation_input_or_photo_upload(self):
        directory = self.output / 'runs' / 'oakley-segment-auto'
        client = FakeClient((200, {'code': 0, 'data': {'task_id': 'segmentation_new'}}))
        self.assertEqual(probe.submit(directory, client)['state'], 'submitted')
        self.assertEqual(len(client.calls), 1)
        args, kwargs = client.calls[0]
        self.assertEqual(args, ('POST', probe.API + '/mesh/segment', 'tripo'))
        self.assertEqual(kwargs['json'], dict(input='original_oakley', model='v2.0-20260430',
                                             segmentation_granularity='detailed', split_by_connectivity=False))
        self.assertEqual(probe.submit(directory, client)['state'], 'already_submitted')
        self.assertEqual(len(client.calls), 1)

    def test_ambiguous_segmentation_timeout_is_never_retried(self):
        directory = self.output / 'runs' / 'miu-segment-auto'
        client = FakeClient(requests.Timeout('server may have accepted'))
        self.assertEqual(probe.submit(directory, client)['state'], 'submission_uncertain')
        self.assertEqual(probe.submit(directory, client)['state'], 'submission_uncertain_or_rejected_no_retry')
        self.assertEqual(len(client.calls), 1)

    def test_application_error_receipt_does_not_allow_paid_resubmit(self):
        directory = self.output / 'runs' / 'miu-segment-auto'
        client = FakeClient((200, {'code': 2010, 'message': 'no credits'}))
        self.assertEqual(probe.submit(directory, client)['state'], 'rejected')
        self.assertEqual(probe.submit(directory, client)['state'], 'already_submitted')
        self.assertEqual(len(client.calls), 1)

    def test_prepare_is_immutable_and_repeatable(self):
        first = base.read_json(self.output / 'prepared.json')
        self.assertEqual(probe.prepare(self.output, self.source), first)
        source_request = self.source / 'runs' / 'oakley-tripo' / 'request.json'
        changed = base.read_json(source_request)
        changed['settings']['model_seed'] += 1
        base.write_json(source_request, changed)
        with self.assertRaises(ValueError):
            probe.prepare(self.output, self.source)

    def test_guided_upload_token_is_used_and_paid_timeout_is_not_retried(self):
        directory = self.output/'runs'/'oakley-segment-guided'
        directory.mkdir()
        image = directory/'reference-mask.png'
        image.write_bytes(b'captured-mask')
        request = dict(provider='tripo', operation='segment-guided', endpoint=probe.API+'/mesh/segment',
                       estimated_usd=.4, settings=dict(input='original_oakley', model='v2.0-20260430'),
                       reference_image=dict(path=str(image), sha256=base.digest(image.read_bytes())))
        base.write_json(directory/'request.json', request)

        class GuidedClient:
            calls = []
            def api(self, method, url, provider, **kwargs):
                self.calls.append((method, url, provider, kwargs))
                if url.endswith('/files'):
                    return 200, dict(code=0, data=dict(file_token='file_mask'))
                self.payload = kwargs['json']
                raise requests.Timeout('accepted possibly')

        client = GuidedClient()
        self.assertEqual(probe.submit(directory, client)['state'], 'submission_uncertain')
        self.assertEqual(client.payload, dict(input='original_oakley', model='v2.0-20260430', ref_image='file_mask'))
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(probe.submit(directory, client)['state'], 'submission_uncertain_or_rejected_no_retry')
        self.assertEqual(len(client.calls), 2)


if __name__ == '__main__':
    unittest.main()
