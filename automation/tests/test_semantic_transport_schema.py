"""No-network regressions for schema adaptation and paid-call replay guards."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from reconstruction.photo_semantics import RESPONSE_SCHEMA, PROMPT, build_image_manifest, validate_product_hypotheses
from reconstruction.semantic_transport import GeminiSemanticClient, provider_schema
from test_photo_semantics import response


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


class Session:
    def __init__(self, result=None, status=200, timeout=False):
        self.result, self.status, self.timeout = result or response(), status, timeout
        self.calls = []

    def post(self, *args, **kwargs):
        self.calls.append(kwargs)
        if self.timeout:
            raise TimeoutError('uncertain completion')
        body = {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': json.dumps(self.result)}]}}]}
        return type('Response', (), {'status_code': self.status, 'json': lambda _: body})()


class SchemaTransportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        image = self.root / 'source.png'
        Image.new('RGB', (20, 20), 'brown').save(image)
        self.manifest = build_image_manifest([{'id': 'front', 'path': image}])

    def client(self, session):
        return GeminiSemanticClient(api_key='test-secret', model='gemini-test',
                                    cache_dir=self.root / 'cache', maximum_calls=1, session=session)

    def test_nested_bounds_become_instructions_without_mutating_shape_or_enums(self):
        before = copy.deepcopy(RESPONSE_SCHEMA)
        actual = provider_schema(RESPONSE_SCHEMA)
        self.assertEqual(RESPONSE_SCHEMA, before)
        self.assertEqual(actual['required'], before['required'])
        material = actual['properties']['material_interpretations']
        self.assertNotIn('maxItems', material)
        self.assertIn('"maxItems":2', material['description'])
        self.assertEqual(material['items']['properties']['absorption']['enum'],
                         before['properties']['material_interpretations']['items']['properties']['absorption']['enum'])
        box = actual['properties']['likely_reflections']['items']['properties']['box_yxyx_1000']
        self.assertNotIn('maxItems', box)
        self.assertIn('"minItems":4', box['description'])
        self.assertNotIn('maximum', box['items'])
        self.assertIn('"maximum":1000', box['items']['description'])

    def test_one_adapted_call_pins_original_schema_and_reuses_completion(self):
        session = Session()
        client = self.client(session)
        result = client.infer(self.manifest, PROMPT, RESPONSE_SCHEMA)
        self.assertEqual(result['recipe']['original_response_schema'], RESPONSE_SCHEMA)
        self.assertEqual(result['recipe']['protocol'], 'bounded_semantic_transport_v2')
        self.assertEqual(session.calls[0]['json']['generationConfig']['responseSchema'], provider_schema(RESPONSE_SCHEMA))
        self.assertTrue(client.infer(self.manifest, PROMPT, RESPONSE_SCHEMA)['cache_reused'])
        self.assertEqual(len(session.calls), 1)
        self.assertFalse(validate_product_hypotheses(result, self.manifest)['accepted'])

    def _legacy(self, status):
        cache = self.root / 'cache'
        cache.mkdir()
        recipe = {'model': 'gemini-test', 'source_sha256': [x['sha256'] for x in self.manifest],
                  'explicit_prompt_labels': [None], 'prompt': PROMPT,
                  'generation_config': {'candidateCount': 1, 'maxOutputTokens': 4096,
                      'thinkingConfig': {'thinkingLevel': 'low'}, 'responseMimeType': 'application/json',
                      'responseSchema': RESPONSE_SCHEMA}, 'protocol': 'bounded_semantic_transport_v1'}
        key = hashlib.sha256(canonical(recipe)).hexdigest()
        result = response() if status == 'complete' else None
        record = {'request_sha256': key, 'recipe': recipe, 'status': status, 'response': result,
                  'response_sha256': hashlib.sha256(canonical(result)).hexdigest()}
        (cache / (key + '.json')).write_bytes(canonical(record))
        (cache / (key + '.reserved')).write_bytes(canonical({'request_sha256': key}))
        (cache / 'call-000.reserved').write_bytes(canonical({'request_sha256': key}))
        (cache / 'budget.json').write_bytes(canonical({'maximum_calls': 1}))
        return cache, key

    def test_upgrade_reuses_legacy_success_without_new_request_or_migration(self):
        cache, key = self._legacy('complete')
        original = {p.name: p.read_bytes() for p in cache.iterdir()}
        session = Session()
        result = self.client(session).infer(self.manifest, PROMPT, RESPONSE_SCHEMA)
        self.assertTrue(result['cache_reused'])
        self.assertEqual(result['request_sha256'], key)
        self.assertEqual(session.calls, [])
        self.assertEqual(original, {p.name: p.read_bytes() for p in cache.iterdir()})

    def test_upgrade_never_retries_legacy_400_or_uncertain_completion(self):
        cache, _ = self._legacy('failed_or_uncertain')
        original = {p.name: p.read_bytes() for p in cache.iterdir()}
        session = Session()
        with self.assertRaisesRegex(RuntimeError, 'no automatic retry'):
            self.client(session).infer(self.manifest, PROMPT, RESPONSE_SCHEMA)
        self.assertEqual(session.calls, [])
        self.assertEqual(original, {p.name: p.read_bytes() for p in cache.iterdir()})

    def test_adapted_timeout_is_reserved_and_never_retried(self):
        session = Session(timeout=True)
        client = self.client(session)
        for _ in range(2):
            with self.assertRaisesRegex(RuntimeError, 'no (automatic )?retry'):
                client.infer(self.manifest, PROMPT, RESPONSE_SCHEMA)
        self.assertEqual(len(session.calls), 1)

    def test_bounds_still_rejected_by_unchanged_local_validator(self):
        invalid = response()
        invalid['likely_reflections'][0]['box_yxyx_1000'] = [1, 1, 2000, 2000]
        result = self.client(Session(invalid)).infer(self.manifest, PROMPT, RESPONSE_SCHEMA)
        with self.assertRaisesRegex(ValueError, 'normalized yxyx box'):
            validate_product_hypotheses(result, self.manifest)

    def test_schema_change_cannot_grow_existing_paid_budget(self):
        session = Session()
        client = self.client(session)
        client.infer(self.manifest, PROMPT, RESPONSE_SCHEMA)
        changed = copy.deepcopy(RESPONSE_SCHEMA)
        changed['properties']['contradictions']['maxItems'] = 11
        with self.assertRaisesRegex(RuntimeError, 'budget exhausted'):
            client.infer(self.manifest, PROMPT, changed)
        self.assertEqual(len(session.calls), 1)


if __name__ == '__main__':
    unittest.main()
