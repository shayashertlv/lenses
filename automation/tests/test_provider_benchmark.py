"""No-network tests for paid submission receipts and provider artifact validation."""
from copy import deepcopy
import json
from pathlib import Path
import struct
import tempfile
import unittest

import requests

from qa import provider_benchmark as benchmark


def triangle_glb():
    binary = struct.pack('<9f3H', 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 1, 2) + b'\0\0'
    document = {
        'asset': {'version': '2.0'},
        'scene': 0,
        'scenes': [{'nodes': [0]}],
        'nodes': [{'mesh': 0}],
        'meshes': [{'primitives': [{'attributes': {'POSITION': 0}, 'indices': 1}]}],
        'buffers': [{'byteLength': len(binary)}],
        'bufferViews': [{'buffer': 0, 'byteOffset': 0, 'byteLength': 36},
                        {'buffer': 0, 'byteOffset': 36, 'byteLength': 6}],
        'accessors': [
            {'bufferView': 0, 'componentType': 5126, 'count': 3, 'type': 'VEC3',
             'min': [0, 0, 0], 'max': [1, 1, 0]},
            {'bufferView': 1, 'componentType': 5123, 'count': 3, 'type': 'SCALAR'},
        ],
    }
    encoded = json.dumps(document).encode()
    encoded += b' ' * (-len(encoded) % 4)
    chunks = struct.pack('<I4s', len(encoded), b'JSON') + encoded
    chunks += struct.pack('<I4s', len(binary), b'BIN\0') + binary
    return b'glTF' + struct.pack('<II', 2, len(chunks) + 12) + chunks


class FakeClient:
    def __init__(self, replies=(), artifact=None):
        self.replies = list(replies)
        self.calls = []
        self.downloads = []
        self.artifact = triangle_glb() if artifact is None else artifact

    def api(self, method, url, provider, **kwargs):
        self.calls.append((method, url, provider, deepcopy(kwargs)))
        if not self.replies:
            raise AssertionError('Unexpected API call')
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return deepcopy(reply)

    def download(self, url, destination):
        self.downloads.append(url)
        destination.write_bytes(self.artifact)
        return {'path': str(destination.resolve()), 'bytes': len(self.artifact),
                'sha256': benchmark.digest(self.artifact)}


class ProviderBenchmarkTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def case(self, provider='rodin'):
        directory = self.root / ('oakley-' + provider)
        directory.mkdir()
        raw = b'\x89PNG\r\n\x1a\nsource-photo'
        photo = directory / 'front.png'
        photo.write_bytes(raw)
        request = {
            'product': 'oakley', 'provider': provider,
            'settings': deepcopy(benchmark.SETTINGS[provider]),
            'endpoint': benchmark.FAL_ENDPOINTS.get(
                provider, 'https://openapi.tripo3d.ai/v3/generation/multiview-to-model'),
            'estimated_usd': .60,
            'photos': [{'path': str(photo), 'sha256': benchmark.digest(raw), 'view': 'front'}],
        }
        benchmark.write_json(directory / 'request.json', request)
        return directory

    def submitted(self, provider='rodin', artifact=None):
        directory = self.case(provider)
        if provider == 'tripo':
            receipt = {'data': {'task_id': 'task_original'}}
            replies = [(200, {'code': 0, 'data': {'task_id': 'task_original',
                'status': 'success', 'output': {'model_url': 'https://cdn.example/model.glb'}}})]
        else:
            receipt = {'request_id': 'original',
                'status_url': 'https://queue.fal.run/provider/requests/original/status',
                'response_url': 'https://queue.fal.run/provider/requests/original'}
            output_field = 'model_glb' if provider == 'trellis' else 'model_mesh'
            replies = [(200, {'status': 'COMPLETED'}),
                       (200, {output_field: {'url': 'https://cdn.example/model.glb'}})]
        benchmark.write_json(directory / 'submission.json', {'http': 200, 'response': receipt})
        return directory, FakeClient(replies, artifact=artifact)

    def test_ambiguous_submit_timeout_keeps_reservation_and_never_resubmits(self):
        directory = self.case()
        client = FakeClient([requests.Timeout('connection lost after sending')])
        self.assertEqual(benchmark.submit(directory, client)['state'], 'submission_uncertain')
        self.assertTrue((directory / 'submission-reserved.json').exists())
        self.assertFalse((directory / 'submission.json').exists())
        self.assertEqual(benchmark.submit(directory, client)['state'],
                         'submission_uncertain_or_rejected_no_retry')
        self.assertEqual(len(client.calls), 1)

    def test_received_rejection_is_not_automatically_paid_again(self):
        directory = self.case()
        client = FakeClient([(422, {'detail': 'invalid input'})])
        self.assertEqual(benchmark.submit(directory, client)['state'], 'rejected')
        benchmark.submit(directory, client)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(benchmark.read_json(directory / 'submission.json')['http'], 422)

    def test_resuming_fal_uses_original_returned_urls_and_performs_only_gets(self):
        directory, client = self.submitted()
        self.assertEqual(benchmark.poll(directory, client)['state'], 'downloaded')
        self.assertEqual([(call[0], call[1]) for call in client.calls], [
            ('GET', 'https://queue.fal.run/provider/requests/original/status'),
            ('GET', 'https://queue.fal.run/provider/requests/original')])
        self.assertEqual(benchmark.poll(directory, client)['state'], 'downloaded')
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(len(client.downloads), 1)

    def test_resuming_tripo_queries_exact_task_without_new_generation(self):
        directory, client = self.submitted('tripo')
        self.assertEqual(benchmark.poll(directory, client)['state'], 'downloaded')
        self.assertEqual([(call[0], call[1]) for call in client.calls],
                         [('GET', 'https://openapi.tripo3d.ai/v3/tasks/task_original')])

    def test_tripo_upload_failure_cannot_submit_generation(self):
        directory = self.case('tripo')
        client = FakeClient([(200, {'code': 2003, 'message': 'Empty file'})])
        self.assertEqual(benchmark.submit(directory, client)['state'], 'upload_failed')
        self.assertEqual(len(client.calls), 1)
        self.assertTrue(client.calls[0][1].endswith('/v3/files'))
        self.assertFalse((directory / 'submission-reserved.json').exists())

    def test_tripo_upload_multipart_and_generation_body_follow_v3_contract(self):
        directory = self.case('tripo')
        client = FakeClient([(200, {'code': 0, 'data': {'file_token': 'file_uploaded'}}),
                             (200, {'code': 0, 'data': {'task_id': 'task_new'}})])
        self.assertEqual(benchmark.submit(directory, client)['task_id'], 'task_new')
        upload = client.calls[0][3]
        self.assertEqual(set(upload['files']), {'file'})
        self.assertEqual(upload['files']['file'][2], 'image/png')
        body = client.calls[1][3]['json']
        self.assertEqual(body['inputs'], [{'front': {'file_token': 'file_uploaded'}}])
        self.assertEqual(body['texture_version'], 'v3.5-20260815')
        self.assertTrue(body['delight'])

    def test_changed_source_fails_before_any_provider_call(self):
        directory = self.case()
        (directory / 'front.png').write_bytes(b'changed')
        client = FakeClient()
        with self.assertRaises(ValueError):
            benchmark.submit(directory, client)
        self.assertEqual(client.calls, [])
        self.assertFalse((directory / 'submission-reserved.json').exists())

    def test_fal_inputs_have_no_sdk_wrapper_and_valid_rodin_seed(self):
        directory = self.case()
        client = FakeClient([(200, {'request_id': 'accepted'})])
        benchmark.submit(directory, client)
        body = client.calls[0][3]['json']
        self.assertNotIn('input', body)
        self.assertTrue(body['image_urls'][0].startswith('data:image/png;base64,'))
        self.assertTrue(0 <= body['seed'] <= 65535)

    def test_provider_result_fields_and_duplicate_artifacts(self):
        url = 'https://cdn.example/model.glb'
        self.assertEqual(benchmark.collect_urls('tripo', {'data': {'output': {'model_url': url}}}),
                         [('model', url)])
        self.assertEqual(benchmark.collect_urls('trellis', {'model_glb': {'url': url}}),
                         [('model', url)])
        self.assertEqual(benchmark.collect_urls('rodin', {'model_mesh': {'url': url},
                            'model_meshes': [{'url': url}], 'textures': [{'url': url}]}),
                         [('model', url)])

    def test_provider_html_response_never_becomes_completed_model(self):
        directory, client = self.submitted(artifact=b'<html>CDN error</html>')
        self.assertEqual(benchmark.poll(directory, client)['state'], 'no_glb')
        self.assertFalse((directory / 'artifacts.json').exists())

    def test_invalid_glb_declared_length_rejected(self):
        raw = bytearray(triangle_glb())
        struct.pack_into('<I', raw, 8, len(raw) + 4)
        directory, client = self.submitted(artifact=bytes(raw))
        with self.assertRaises(ValueError):
            benchmark.poll(directory, client)
        self.assertFalse((directory / 'artifacts.json').exists())

    def test_matching_glb_header_cannot_hide_invalid_json_chunk(self):
        body = b'not-json'
        raw = b'glTF' + struct.pack('<II', 2, 20 + len(body))
        raw += struct.pack('<I4s', len(body), b'JSON') + body
        directory, client = self.submitted(artifact=raw)
        with self.assertRaises(ValueError):
            benchmark.poll(directory, client)
        self.assertFalse((directory / 'artifacts.json').exists())

    def test_matching_glb_header_cannot_hide_out_of_bounds_chunk(self):
        body = b'{"asset":{"version":"2.0"}} '
        body += b' ' * (-len(body) % 4)
        raw = b'glTF' + struct.pack('<II', 2, 20 + len(body))
        raw += struct.pack('<I4s', len(body) + 400, b'JSON') + body
        directory, client = self.submitted(artifact=raw)
        with self.assertRaises(ValueError):
            benchmark.poll(directory, client)
        self.assertFalse((directory / 'artifacts.json').exists())


if __name__ == '__main__':
    unittest.main()
