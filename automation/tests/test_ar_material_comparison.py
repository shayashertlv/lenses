import hashlib
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from reconstruction.ar_material_search import compare_rendered_candidates


class ComparisonBindingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.original, self.a, self.b = (root / name for name in ('original.png', 'a.png', 'b.png'))
        for path, color in ((self.original, (120, 100, 80)), (self.a, (60, 60, 60)), (self.b, (90, 60, 30))):
            Image.new('RGB', (40, 30), color).save(path)
        self.cards = [{'candidate_id': label, 'path': str(path),
                       'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'model_sha256': label * 64}
                      for label, path in (('a', self.a), ('b', self.b))]

    def client(self, mutate=None):
        desired_hash = self.cards[1]['sha256']
        class Client:
            calls = 0
            def infer(inner, manifest, prompt, schema):
                inner.calls += 1
                candidates = [r for r in manifest if r['prompt_label'].startswith('candidate-')]
                labels = [row['prompt_label'].removeprefix('candidate-') for row in candidates]
                winner = next(row['prompt_label'].removeprefix('candidate-') for row in candidates
                              if row['sha256'] == desired_hash)
                if mutate is not None and inner.calls == 1:
                    Image.new('RGB', (40, 30), (121, 101, 81)).save(mutate)
                return {'source_images': manifest, 'response': {'winner': winner, 'assessments': [
                    {'candidate': label, 'match': 'plausible', 'defects': [], 'evidence': ['Warm tint in photo-1.']}
                    for label in labels], 'limitations': []}}
        return Client()

    def test_order_reversal_preserves_physical_candidate_identity(self):
        result = compare_rendered_candidates([{'id': 'front', 'path': str(self.original)}],
                                             self.cards, client=self.client())
        self.assertEqual([r['verdict'] for r in result['comparisons']], ['B', 'A'])
        self.assertEqual([r['winner'] for r in result['comparisons']], ['b', 'b'])
        self.assertFalse(result['accepted'])

    def test_exact_image_label_prefix_preserves_identity_and_raw_receipt(self):
        client=self.client(); infer=client.infer
        def prefixed(*args):
            receipt=infer(*args)
            response=receipt['response']
            response['winner']='candidate-'+response['winner']
            for row in response['assessments']:row['candidate']='candidate-'+row['candidate']
            return receipt
        client.infer=prefixed
        result=compare_rendered_candidates([{'id':'front','path':str(self.original)}],self.cards,client=client)
        self.assertEqual([r['winner'] for r in result['comparisons']],['b','b'])
        self.assertEqual(result['comparisons'][0]['receipt']['response']['winner'],'candidate-B')

    def test_prefix_does_not_permit_duplicate_or_unknown_card_labels(self):
        for labels in (['A','candidate-A'],['A','candidate-Z']):
            client=self.client();infer=client.infer
            def invalid(*args):
                receipt=infer(*args)
                for row,value in zip(receipt['response']['assessments'],labels):row['candidate']=value
                return receipt
            client.infer=invalid
            with self.assertRaisesRegex(ValueError,'every supplied candidate'):
                compare_rendered_candidates([{'id':'front','path':str(self.original)}],self.cards,client=client)

    def test_odd_candidate_count_moves_the_middle_candidate_too(self):
        path = self.a.with_name('c.png')
        Image.new('RGB', (40, 30), (50, 20, 60)).save(path)
        cards = self.cards + [{'candidate_id': 'c', 'path': str(path),
                              'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'model_sha256': 'c'*64}]
        result = compare_rendered_candidates([{'id':'front', 'path':str(self.original)}], cards, client=self.client())
        first, second = [r['mapping'] for r in result['comparisons']]
        self.assertTrue(all(first[label] != second[label] for label in first))
        self.assertEqual([r['winner'] for r in result['comparisons']], ['b', 'b'])

    def test_original_cannot_change_between_order_reversed_calls(self):
        with self.assertRaisesRegex(ValueError, 'source bytes'):
            compare_rendered_candidates([{'id': 'front', 'path': str(self.original)}],
                                         self.cards, client=self.client(self.original))

    def test_render_card_cannot_change_between_order_reversed_calls(self):
        with self.assertRaisesRegex(ValueError, 'source bytes'):
            compare_rendered_candidates([{'id': 'front', 'path': str(self.original)}],
                                         self.cards, client=self.client(self.a))


if __name__ == '__main__':
    unittest.main()
