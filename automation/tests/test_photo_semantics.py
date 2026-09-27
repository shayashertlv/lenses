import copy
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
from PIL import Image

from reconstruction.photo_semantics import (build_image_manifest, infer_product_hypotheses,
    manifest_from_probe, rebind_product_hypotheses, validate_product_hypotheses)
from reconstruction.semantic_transport import GeminiSemanticClient


def response():
    evidence = [{'image_number': 1, 'observation': 'Brown gradient and rear temple are visible.'}]
    return {'confidence_is_uncalibrated': True,
        'construction': {'lens_count_or_shield': 'two', 'rim_type': 'full',
                         'frame_finish_pattern': 'dark', 'evidence': evidence, 'confidence': 'medium'},
        'aperture_material_vs_scene': evidence,
        'material_interpretations': [{'absorption': 'gradient_tint', 'coating': 'ordinary',
            'qualitative_colors': ['brown'], 'gradient_direction': 'top_to_bottom',
            'evidence': evidence, 'alternative_scene_explanation': 'A light may brighten the lower region.',
            'confidence': 'medium'}],
        'likely_reflections': [{'image_number': 1, 'box_yxyx_1000': [0, 0, 100, 100],
                               'rationale': 'Bright studio shape', 'confidence': 'medium'}],
        'clean_color_sampling_regions': [{'image_number': 1, 'box_yxyx_1000': [100, 100, 900, 900],
            'aperture_label': 'left', 'lens_height': 'middle', 'qualitative_observed_color': 'brown',
            'caveat': 'Coarse box', 'confidence': 'medium'}],
        'contradictions': [], 'unobservable_facts': ['Absolute transmission']}


class FakeResponse:
    status_code = 200

    def json(self):
        return {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': json.dumps(response())}]}}]}


class FakeSession:
    def __init__(self, raises=False):
        self.calls = []
        self.raises = raises

    def post(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.raises:
            raise RuntimeError('Do not persist secret-test-key headers')
        return FakeResponse()


class SemanticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.photo = self.root / 'source.jpg'
        Image.new('RGB', (40, 30), (130, 120, 110)).save(self.photo)
        self.manifest = build_image_manifest([{'id': 'front', 'path': self.photo}])

    def test_factored_prior_never_becomes_fact(self):
        report = validate_product_hypotheses(response(), self.manifest)
        self.assertEqual(report['declared_facts'], {})
        self.assertFalse(report['accepted'])
        self.assertEqual(report['hypotheses'][0]['absorption'], 'gradient_tint')
        self.assertEqual(report['regions'][0]['source_sha256'], self.manifest[0]['sha256'])
        self.assertEqual(validate_product_hypotheses(report, self.manifest), report)

    def test_legacy_tint_and_mirror_are_independent(self):
        raw = response()
        row = raw['material_interpretations'][0]
        row.pop('absorption'); row.pop('coating')
        row.update(family='uniform_tint', mirror='likely')
        report = validate_product_hypotheses({'source_images': self.manifest, 'response': raw}, self.manifest)
        self.assertEqual(report['hypotheses'][0]['absorption'], 'uniform_tint')
        self.assertEqual(report['hypotheses'][0]['coating'], 'colored_mirror')

    def test_source_order_and_invalid_grounding_are_rejected(self):
        bad_sources = copy.deepcopy(self.manifest)
        bad_sources[0]['sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'hashes/order'):
            validate_product_hypotheses({'source_images': bad_sources, 'response': response()}, self.manifest)
        for key, bad in [('image_number', 2), ('box_yxyx_1000', [100, 100, 50, 500])]:
            raw = response()
            raw['likely_reflections'][0][key] = bad
            with self.assertRaises(ValueError):
                validate_product_hypotheses(raw, self.manifest)
        raw = response(); raw['confidence_is_uncalibrated'] = False
        with self.assertRaises(ValueError):
            validate_product_hypotheses(raw, self.manifest)

    def test_exact_decoded_pixel_rebind_not_visual_similarity(self):
        normalized = self.root / 'normalized.png'
        with Image.open(self.photo) as image:
            image.save(normalized)
        target = build_image_manifest([{'id': 'normalized-front', 'path': normalized}])
        report = rebind_product_hypotheses(validate_product_hypotheses(response(), self.manifest), target)
        self.assertEqual(report['regions'][0]['photo_id'], 'normalized-front')
        self.assertEqual(report['regions'][0]['source_sha256'], target[0]['sha256'])
        Image.new('RGB', (40, 30), (131, 120, 110)).save(normalized)
        changed = build_image_manifest([{'id': 'front', 'path': normalized}])
        with self.assertRaisesRegex(ValueError, 'no unique'):
            rebind_product_hypotheses(validate_product_hypotheses(response(), self.manifest), changed)

    def test_probe_hash_is_verified(self):
        record = {'source_images': [{'label': 'image-1', 'local_path': str(self.photo),
                                    'sha256': self.manifest[0]['sha256']}]}
        self.assertEqual(manifest_from_probe(record)[0]['photo_id'], 'image-1')
        self.photo.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'source bytes'):
            manifest_from_probe(record)

    def test_transport_caches_and_counts_failed_calls_without_retry(self):
        cache = self.root / 'cache'
        session = FakeSession()
        client = GeminiSemanticClient(api_key='secret-test-key', model='gemini-test', cache_dir=cache,
                                      maximum_calls=1, session=session)
        first = client.infer(self.manifest, 'prompt', {'type': 'OBJECT'})
        second = client.infer(self.manifest, 'prompt', {'type': 'OBJECT'})
        self.assertEqual(first['response'], second['response'])
        self.assertTrue(second['cache_reused'])
        self.assertEqual(len(session.calls), 1)
        self.assertNotIn('secret-test-key', ''.join(p.read_text() for p in cache.iterdir()))
        self.assertFalse(session.calls[0][1]['allow_redirects'])
        with self.assertRaisesRegex(RuntimeError, 'budget exhausted'):
            client.infer(self.manifest, 'another prompt', {'type': 'OBJECT'})
        failing = FakeSession(True)
        client = GeminiSemanticClient(api_key='secret-test-key', model='gemini-test', cache_dir=self.root / 'failed',
                                      maximum_calls=1, session=failing)
        with self.assertRaisesRegex(RuntimeError, 'no retry'):
            client.infer(self.manifest, 'prompt', {'type': 'OBJECT'})
        with self.assertRaisesRegex(RuntimeError, 'no automatic retry'):
            client.infer(self.manifest, 'prompt', {'type': 'OBJECT'})
        self.assertEqual(len(failing.calls), 1)
        self.assertNotIn('secret-test-key', ''.join(p.read_text() for p in (self.root / 'failed').iterdir()))

    def test_inference_only_uses_explicit_client(self):
        class Client:
            calls = 0
            def infer(inner, manifest, prompt, schema):
                inner.calls += 1
                return {'source_images': manifest, 'response': response()}
        client = Client()
        report = infer_product_hypotheses([{'id': 'front', 'path': self.photo}], client)
        self.assertEqual(client.calls, 1)
        self.assertEqual(report['image_manifest'][0]['photo_id'], 'front')

    def test_prompt_labels_change_cache_identity_and_budget_cannot_grow(self):
        session = FakeSession()
        cache = self.root / 'labels'
        client = GeminiSemanticClient(api_key='secret-test-key', model='gemini-test', cache_dir=cache,
                                      maximum_calls=2, session=session)
        a = copy.deepcopy(self.manifest); a[0]['prompt_label'] = 'candidate-A'
        b = copy.deepcopy(self.manifest); b[0]['prompt_label'] = 'candidate-B'
        client.infer(a, 'prompt', {'type': 'OBJECT'})
        client.infer(b, 'prompt', {'type': 'OBJECT'})
        self.assertEqual(len(session.calls), 2)
        self.assertEqual(session.calls[1][1]['json']['contents'][0]['parts'][1]['text'], 'image-1 candidate-B')
        larger = GeminiSemanticClient(api_key='secret-test-key', model='gemini-test', cache_dir=cache,
                                      maximum_calls=3, session=session)
        with self.assertRaisesRegex(ValueError, 'lifetime call budget changed'):
            larger.infer(b, 'prompt', {'type': 'OBJECT'})

    def test_identical_render_cards_allowed_but_not_duplicate_original_evidence(self):
        generic = build_image_manifest([{'id': 'candidate-A', 'path': self.photo},
                                        {'id': 'candidate-B', 'path': self.photo}])
        self.assertEqual(generic[0]['sha256'], generic[1]['sha256'])
        with self.assertRaisesRegex(ValueError, 'byte-distinct'):
            validate_product_hypotheses(response(), generic)


if __name__ == '__main__':
    unittest.main()
