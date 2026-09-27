"""No-network paid-boundary and typed-plan regressions for current Astra calls."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
from concurrent.futures import ThreadPoolExecutor
import threading
import unittest
from unittest.mock import patch

from PIL import Image

from reconstruction.segmented_astra_transport import AstraClient, validate_plan, validate_tools_schema


def obj(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}


SCHEMA = obj({'summary': {'type': 'string', 'maxLength': 1000}, 'operations': {
    'type': 'array', 'minItems': 1, 'maxItems': 4, 'items': {'anyOf': [
        obj({'op': {'type': 'string', 'enum': ['move']}, 'part': {'type': 'integer', 'minimum': 0},
             'offset_m': {'type': 'array', 'minItems': 3, 'maxItems': 3,
                          'items': {'type': 'number', 'minimum': -0.005, 'maximum': 0.005}}}),
        obj({'op': {'type': 'string', 'enum': ['finish']}, 'reason': {'type': 'string'}})
    ]}}})
PLAN = {'summary': 'Keep the hardware; inspect the bounded correction.',
        'operations': [{'op': 'move', 'part': 0, 'offset_m': [0, 0.001, 0]}]}


def result(plan=None):
    return {'id': 'resp_test', 'model': 'gpt-6-astra', 'status': 'completed',
            'output': [{'type': 'reasoning', 'id': 'reason_test'},
                       {'type': 'function_call', 'status': 'completed', 'name': 'edit_candidate',
                        'call_id': 'call_test', 'arguments': json.dumps(PLAN if plan is None else plan)}],
            'usage': {'input_tokens': 10, 'output_tokens': 50}}


class Response:
    def __init__(self, body, status=200):
        self.status_code = status
        self.raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.closed = False

    def iter_content(self, chunk_size):
        for offset in range(0, len(self.raw), chunk_size):
            yield self.raw[offset:offset + chunk_size]

    def close(self):
        self.closed = True


class HTTP:
    def __init__(self, body=None, status=200, error=None):
        self.body = result() if body is None else body
        self.status, self.error = status, error
        self.calls = []
        self.responses = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error:
            raise self.error
        response = Response(self.body, self.status)
        self.responses.append(response)
        return response


class AstraTransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        path = self.root / 'photo.png'
        Image.new('RGB', (12, 12), '#887766').save(path)
        self.images = [{'id': 'front', 'label': 'Original front photo; source data',
                        'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}]
        self.context = {'candidate_sha256': 'a' * 64, 'revision': 0,
                        'coordinates': {'units': 'metres', 'front': '+Z', 'up': '+Y'}}
        self.budget = self.root / 'shared-budget.json'

    def client(self, http, maximum_calls=3, **kwargs):
        return AstraClient('not-a-real-api-key', budget_path=self.budget,
                           maximum_calls=maximum_calls, session=http, **kwargs)

    def decide(self, client, name='attempt-1', **kwargs):
        return client.decide(kwargs.get('context', self.context), kwargs.get('images', self.images),
                             self.root / name, tools_schema=kwargs.get('schema', SCHEMA))

    def receipt(self, name='attempt-1'):
        return json.loads((self.root / name / 'receipt.json').read_bytes())

    def test_payload_uses_one_forced_strict_typed_function_and_fresh_context(self):
        http = HTTP()
        client = self.client(http)
        self.assertFalse(self.budget.exists(), 'Construction must not reserve or call anything')
        self.assertEqual(self.decide(client), PLAN)
        url, options = http.calls[0]
        self.assertEqual(url, 'https://api.openai.com/v1/responses')
        body = json.loads(options['data'])
        self.assertIs(body['store'], False)
        self.assertIs(body['parallel_tool_calls'], False)
        self.assertEqual(body['tool_choice'], {'type': 'function', 'name': 'edit_candidate'})
        self.assertEqual(body['tools'][0]['parameters'], SCHEMA)
        self.assertIs(body['tools'][0]['strict'], True)
        self.assertEqual(body['max_output_tokens'], 12000)
        self.assertEqual(body['reasoning'], {'effort': 'high'})
        self.assertNotIn('previous_response_id', body)
        self.assertEqual(options['timeout'], (15, 180))
        self.assertIs(options['allow_redirects'], False)
        self.assertTrue(http.responses[0].closed)
        self.assertEqual(self.receipt()['usage']['output_tokens'], 50)
        for path in (self.root / 'attempt-1').iterdir():
            self.assertNotIn(b'not-a-real-api-key', path.read_bytes())

    def test_complete_response_replays_locally_across_client_instances_at_exhausted_budget(self):
        http = HTTP()
        self.decide(self.client(http, maximum_calls=1))
        before = {p: p.read_bytes() for p in (self.root / 'attempt-1').iterdir()}
        new_http = HTTP(error=AssertionError('Replay must never contact provider'))
        self.assertEqual(self.decide(self.client(new_http, maximum_calls=1)), PLAN)
        self.assertEqual(new_http.calls, [])
        self.assertEqual(before, {p: p.read_bytes() for p in (self.root / 'attempt-1').iterdir()})
        self.assertEqual(len(json.loads(self.budget.read_bytes())['reservations']), 1)

    def test_timeout_is_reserved_once_and_never_retried(self):
        http = HTTP(error=TimeoutError('Do not persist not-a-real-api-key'))
        client = self.client(http)
        for _ in range(2):
            with self.assertRaisesRegex(RuntimeError, 'no (automatic )?retry'):
                self.decide(client)
        self.assertEqual(len(http.calls), 1)
        self.assertEqual(self.receipt()['status'], 'failed_or_uncertain')
        self.assertEqual(len(json.loads(self.budget.read_bytes())['reservations']), 1)
        self.assertNotIn('not-a-real-api-key', (self.root / 'attempt-1' / 'receipt.json').read_text())

    def test_http_400_preserves_sanitized_raw_response_and_counts_attempt(self):
        http = HTTP({'error': {'message': 'invalid not-a-real-api-key'}}, status=400)
        client = self.client(http)
        with self.assertRaises(RuntimeError):
            self.decide(client)
        with self.assertRaises(RuntimeError):
            self.decide(client)
        receipt = self.receipt()
        self.assertEqual(receipt['http_status'], 400)
        self.assertTrue(receipt['response_redacted'])
        self.assertIn('[REDACTED]', (self.root / 'attempt-1' / 'response.json').read_text())
        self.assertEqual(len(http.calls), 1)

    def test_incomplete_bad_json_refusal_and_wrong_function_are_not_plans_or_retried(self):
        incomplete = result()
        incomplete['status'], incomplete['incomplete_details'] = 'incomplete', {'reason': 'max_output_tokens'}
        refusal = result()
        refusal['output'] = [{'type': 'message', 'content': [{'type': 'refusal', 'refusal': 'cannot'}]}]
        wrong = result()
        wrong['output'][1]['name'] = 'execute_code'
        duplicate = result()
        duplicate['output'].append(copy.deepcopy(duplicate['output'][1]))
        invalid_args = result()
        invalid_args['output'][1]['arguments'] = '{broken'
        for index, body in enumerate([incomplete, b'<html>error</html>', refusal, wrong, duplicate, invalid_args]):
            with self.subTest(index=index):
                # Separate explicit authorizations for mocked scenarios only.
                self.budget = self.root / f'budget-{index}.json'
                http = HTTP(body)
                client = self.client(http, maximum_calls=1)
                with self.assertRaises(RuntimeError):
                    self.decide(client, name=f'attempt-{index}')
                with self.assertRaises(RuntimeError):
                    self.decide(client, name=f'attempt-{index}')
                self.assertEqual(len(http.calls), 1)
                self.assertTrue((self.root / f'attempt-{index}' / 'response.json').exists())

    def test_shared_budget_caps_independent_jobs_and_counts_failures(self):
        first = HTTP(status=400)
        with self.assertRaises(RuntimeError):
            self.decide(self.client(first, maximum_calls=1), name='job-a/turn-1')
        second = HTTP()
        with self.assertRaises(RuntimeError):
            self.decide(self.client(second, maximum_calls=1), name='job-b/turn-1')
        self.assertEqual(second.calls, [])
        self.assertEqual(len(json.loads(self.budget.read_bytes())['reservations']), 1)

    def test_concurrent_clients_cannot_exceed_shared_limit(self):
        barrier = threading.Barrier(2)
        transports = [HTTP(), HTTP()]
        def attempt(index):
            barrier.wait()
            try:
                self.decide(self.client(transports[index], maximum_calls=1), name=f'job-{index}/turn-1')
                return True
            except RuntimeError:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(attempt, range(2)))
        self.assertEqual(sum(outcomes), 1)
        self.assertEqual(sum(len(x.calls) for x in transports), 1)
        self.assertEqual(len(json.loads(self.budget.read_bytes())['reservations']), 1)

    def test_budget_limit_cannot_be_raised_by_another_session(self):
        self.decide(self.client(HTTP(), maximum_calls=1))
        http = HTTP()
        with self.assertRaises(RuntimeError):
            self.decide(self.client(http, maximum_calls=2), name='attempt-2')
        self.assertEqual(http.calls, [])
        with self.assertRaises(ValueError):
            self.client(http, maximum_calls=11)

    def test_new_explicit_attempt_after_failure_uses_another_reservation(self):
        with self.assertRaises(RuntimeError):
            self.decide(self.client(HTTP(error=TimeoutError())))
        http = HTTP()
        self.assertEqual(self.decide(self.client(http), name='attempt-2'), PLAN)
        self.assertEqual(len(json.loads(self.budget.read_bytes())['reservations']), 2)

    def test_changed_context_labels_model_schema_prompt_or_code_cannot_replay(self):
        http = HTTP()
        client = self.client(http)
        self.decide(client)
        changed_images = copy.deepcopy(self.images)
        changed_images[0]['label'] = 'Different interpretation of the same image'
        changed_schema = copy.deepcopy(SCHEMA)
        changed_schema['properties']['summary']['maxLength'] = 999
        for kwargs in ({'context': {**self.context, 'revision': 1}}, {'images': changed_images}, {'schema': changed_schema}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.decide(client, **kwargs)
        with self.assertRaises(ValueError):
            self.decide(self.client(http, model='gpt-6-astra-2026-09-01'))
        with patch('reconstruction.segmented_astra_transport.PROMPT', 'Changed prompt'), self.assertRaises(ValueError):
            self.decide(client)
        code = self.root / 'changed-code.py'
        code.write_text('# changed')
        with patch('reconstruction.segmented_astra_transport.__file__', str(code)), self.assertRaises(ValueError):
            self.decide(client)
        self.assertEqual(len(http.calls), 1)

    def test_changed_image_bytes_refuse_before_reservation_or_http(self):
        Image.new('RGB', (12, 12), 'green').save(self.images[0]['path'])
        http = HTTP()
        with self.assertRaises(ValueError):
            self.decide(self.client(http))
        self.assertFalse(self.budget.exists())
        self.assertEqual(http.calls, [])

    def test_changed_payload_response_or_missing_budget_lineage_refuses_cached_plan(self):
        http = HTTP()
        client = self.client(http)
        self.decide(client)
        for filename in ('payload.json', 'response.json'):
            path = self.root / 'attempt-1' / filename
            original = path.read_bytes()
            path.write_bytes(original + b' ')
            with self.assertRaises(ValueError):
                self.decide(client)
            path.write_bytes(original)
        budget = json.loads(self.budget.read_bytes())
        budget['reservations'] = []
        self.budget.write_text(json.dumps(budget))
        with self.assertRaises(ValueError):
            self.decide(client)
        self.assertEqual(len(http.calls), 1)

    def test_crash_after_exclusive_request_without_receipt_never_reposts(self):
        http = HTTP()
        client = self.client(http)
        self.decide(client)
        (self.root / 'attempt-1' / 'receipt.json').unlink()
        with self.assertRaisesRegex(RuntimeError, 'uncertain'):
            self.decide(client)
        self.assertEqual(len(http.calls), 1)

    def test_oversized_provider_response_is_bounded_and_not_retried(self):
        http = HTTP(b'x' * 100)
        client = self.client(http)
        with patch('reconstruction.segmented_astra_transport.MAX_RESPONSE_BYTES', 20):
            with self.assertRaises(RuntimeError):
                self.decide(client)
        with self.assertRaises(RuntimeError):
            self.decide(client)
        self.assertTrue(http.responses[0].closed)
        self.assertEqual(len(http.calls), 1)

    def test_bad_schema_and_nonfinite_context_do_not_use_budget(self):
        http = HTTP()
        schema = copy.deepcopy(SCHEMA)
        schema['additionalProperties'] = True
        with self.assertRaises(ValueError):
            self.decide(self.client(http), schema=schema)
        with self.assertRaises(ValueError):
            self.decide(self.client(http), context={'bad': float('inf')})
        self.assertEqual(http.calls, [])
        self.assertFalse(self.budget.exists())

    def test_host_validator_rejects_unknown_ops_extra_fields_wrong_vectors_and_nonfinite_values(self):
        self.assertIs(validate_tools_schema(SCHEMA), SCHEMA)
        self.assertEqual(validate_plan(PLAN, SCHEMA), PLAN)
        bad_operations = [
            {'op': 'execute', 'code': 'anything'},
            {'op': 'move', 'part': 0, 'offset_m': [0]},
            {'op': 'move', 'part': 0, 'offset_m': [0, 0, 0, 0]},
            {'op': 'move', 'part': True, 'offset_m': [0, 0, 0]},
            {'op': 'move', 'part': 0, 'offset_m': [0, 0.01, 0]},
            {'op': 'move', 'part': 0, 'offset_m': [0, float('nan'), 0]},
            {'op': 'move', 'part': 0, 'offset_m': [0, 0, 0], 'ignored': True},
        ]
        for operation in bad_operations:
            with self.subTest(operation=operation), self.assertRaises(ValueError):
                validate_plan({'summary': 'x', 'operations': [operation]}, SCHEMA)

    def test_invalid_provider_plan_is_retained_but_never_returned_or_repaired(self):
        invalid = {'summary': 'x', 'operations': [{'op': 'move', 'part': 0, 'offset_m': [2]}]}
        http = HTTP(result(invalid))
        client = self.client(http)
        for _ in range(2):
            with self.assertRaises(RuntimeError):
                self.decide(client)
        self.assertEqual(len(http.calls), 1)
        self.assertEqual(self.receipt()['status'], 'failed_or_uncertain')

    def test_duplicate_arguments_keys_are_not_silently_overwritten(self):
        body = result()
        body['output'][1]['arguments'] = '{"summary":"a","summary":"b","operations":[]}'
        http = HTTP(body)
        with self.assertRaises(RuntimeError):
            self.decide(self.client(http))
        self.assertEqual(len(http.calls), 1)

    def test_local_refs_nullable_fields_and_unknown_schema_rules(self):
        schema = obj({'offset': {'$ref': '#/$defs/vector'}, 'note': {'type': ['string', 'null']}})
        schema['$defs'] = {'vector': {'type': 'array', 'minItems': 3, 'maxItems': 3, 'items': {'type': 'number'}}}
        self.assertEqual(validate_plan({'offset': [0, 0, 0], 'note': None}, schema)['offset'], [0, 0, 0])
        with self.assertRaises(ValueError):
            validate_plan({'offset': [0], 'note': None}, schema)
        schema['properties']['note']['unknownValidator'] = 'must not be ignored'
        with self.assertRaises(ValueError):
            validate_plan({'offset': [0, 0, 0], 'note': None}, schema)

    def test_provider_cannot_silently_return_a_different_model(self):
        body = result()
        body['model'] = 'gpt-other-model'
        http = HTTP(body)
        with self.assertRaises(RuntimeError):
            self.decide(self.client(http))
        self.assertEqual(self.receipt()['status'], 'failed_or_uncertain')
        self.assertEqual(len(http.calls), 1)

    def test_production_schema_all_operations_and_complete_clear_descriptor(self):
        from reconstruction.segmented_astra_tools import TOOLS_SCHEMA, validate
        appearance = {'normal_reflectance_rgb': [.04, .04, .04], 'refractive_index': 1.5,
                      'roughness': .05, 'optical_density_keyframes': [{'v': 0, 'optical_density_rgb': [0, 0, 0]}],
                      'angular_reflectance_keyframes': None, 'rear_reflection_fraction_rgb': None}
        operations = [
            {'operation': 'translate', 'part_ids': [0], 'offset_m': [.0005, 0, 0]},
            {'operation': 'rotate', 'part_ids': [0], 'axis': [0, 1, 0], 'pivot_m': [0, 0, 0], 'angle_degrees': 1},
            {'operation': 'local_bend', 'part_ids': [0], 'center_m': [0, 0, 0], 'radius_m': .01, 'offset_m': [.0005, 0, 0]},
            {'operation': 'frame_material', 'part_ids': [0], 'base_color_linear_rgb': [.1, .1, .1], 'roughness': .3, 'metallic': None},
            {'operation': 'optical_appearance', 'group_ids': ['lens'], 'appearance': appearance},
            {'operation': 'normal_policy', 'policy': 'preserve'},
            {'operation': 'group_membership', 'group_id': 'lens', 'part_ids': [0]},
            {'operation': 'inspect', 'part_ids': [0], 'padding_fraction': .1},
            {'operation': 'restore', 'revision_id': 'r0000'},
            {'operation': 'finish', 'verdict': 'review_ready', 'summary': 'Current view remains a review candidate'},
        ]
        self.assertIs(validate_tools_schema(TOOLS_SCHEMA), TOOLS_SCHEMA)
        for operation in operations:
            plan = {'note': 'Grounded in current evidence', 'operations': [operation]}
            with self.subTest(operation=operation['operation']):
                self.assertEqual(validate(plan), plan)
        plan = {'note': 'Retain the physically valid clear hypothesis', 'operations': [operations[4]]}
        http = HTTP(result(plan))
        self.assertEqual(self.decide(self.client(http), schema=TOOLS_SCHEMA), plan)

    def test_production_domain_validator_rejects_schema_valid_semantic_errors(self):
        from reconstruction.segmented_astra_tools import validate
        appearance = {'normal_reflectance_rgb': [.04, .04, .04], 'refractive_index': 1.5,
                      'roughness': .05, 'optical_density_keyframes': [{'v': .2, 'optical_density_rgb': [0, 0, 0]}],
                      'angular_reflectance_keyframes': None, 'rear_reflection_fraction_rgb': None}
        invalid = [
            [{'operation': 'frame_material', 'part_ids': [0], 'base_color_linear_rgb': None, 'roughness': None, 'metallic': None}],
            [{'operation': 'group_membership', 'group_id': 'lens', 'part_ids': [0, 0]}],
            [{'operation': 'normal_policy', 'policy': 'smooth'}, {'operation': 'finish', 'verdict': 'review_ready', 'summary': 'Unseen edit'}],
            [{'operation': 'optical_appearance', 'group_ids': ['lens'], 'appearance': appearance}],
            [{'operation': 'translate', 'part_ids': [0], 'offset_m': [float('inf'), 0, 0]}],
            [{'operation': 'restore', 'revision_id': '../../escape'}],
            [{'operation': 'optical_appearance', 'group_ids': ['lens'], 'appearance': 'JSON must not be encoded in strings'}],
        ]
        for operations in invalid:
            with self.subTest(operations=operations), self.assertRaises(ValueError):
                validate({'note': 'Bad proposal', 'operations': operations})


if __name__ == '__main__':
    unittest.main()
