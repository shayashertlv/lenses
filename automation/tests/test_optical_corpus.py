"""Corpus orchestration and immutable recovery, without any real corpus fitting."""
from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from qa import optical_corpus as corpus
from reconstruction.mesh import TriangleMesh
from test_refine_photos import _write_mesh


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, allow_nan=False), encoding='utf-8')


class OpticalCorpusTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.manifest = self.root / 'manifest.json'
        self.regions, self.output = self.root / 'regions', self.root / 'output'
        self.model = self.root / 'model.glb'
        _write_mesh(self.model, TriangleMesh(np.array([[-1., -1, 0], [1, -1, 0], [0, 1, .1]]), np.array([[0, 1, 2]]), []))
        for view in ('front', 'angled'):
            (self.root / f'{view}.png').write_bytes(f'Pinned fixture {view}'.encode())
        manifest = {'schema_version': 1, 'cases': [{'id': name, 'model': 'model.glb',
            'photos': {'front': 'front.png', 'angled': 'angled.png'}} for name in corpus.EXPECTED]}
        write(self.manifest, manifest)
        self.regions.mkdir()
        (self.regions / 'source-manifest.json').write_bytes(self.manifest.read_bytes())
        write(self.regions / 'corpus-receipt.json', {'manifest': {'sha256': corpus.sha(self.manifest)}})
        rows = []
        for name in corpus.EXPECTED:
            attempt = self.regions / 'cases' / name / 'attempt-001'
            write(attempt / 'report.json', {'quality_verdict': 'unmeasured', 'accepted': False,
                'candidate_sha256': corpus.sha(self.model), 'photos': [{'id': view,
                    'source': str(self.root / f'{view}.png'), 'source_sha256': corpus.sha(self.root / f'{view}.png')}
                    for view in ('front', 'angled')]})
            (attempt / 'mask.png').write_bytes(b'Pinned mask fixture')
            rows.append({'id': name, 'execution_complete': True, 'attempt': attempt.relative_to(self.regions).as_posix(),
                         'artifacts': corpus.inventory(attempt)})
        write(self.regions / 'report.json', {'quality_verdict': 'unmeasured', 'accepted': False,
            'corpus_receipt_sha256': corpus.sha(self.regions / 'corpus-receipt.json'), 'cases': rows})
        identity = patch.object(corpus, 'implementation', return_value={'fixture': 'frozen'})
        identity.start();self.addCleanup(identity.stop)

    def prepare(self, *, selected=('rayban',), resume=False):
        return corpus.prepare(self.manifest, self.regions, self.output, selected=selected, resume=resume)

    def fake_preparation(self, model, output):
        result = {'status': 'prepared_optical_candidate', 'quality_verdict': 'unmeasured', 'accepted': False,
                  'source_sha256': corpus.sha(model)}
        write(output / 'report.json', result)
        return result

    def fake_fit(self, preparation, regions, output, *, policy, maximum_samples_per_hypothesis):
        self.assertEqual(policy.maximum_optimization_runs, 7290)
        self.assertEqual(maximum_samples_per_hypothesis, 256)
        self.assertEqual(len(policy.families), 5)
        output.mkdir(parents=True)
        preview = output / 'preview.glb'
        preview.write_bytes(b'Diagnostic artifact placeholder for orchestration test')
        export = output / 'preview-export.json'
        write(export, {'source_sha256': corpus.sha(self.model), 'output_sha256': corpus.sha(preview)})
        group_report = output / 'part-000/fit.json'
        write(group_report, {'scope': 'orchestration placeholder', 'parameter_identification': 'unmeasured'})
        result = {'status': 'diagnostic_previews_available', 'quality_verdict': 'unmeasured', 'accepted': False,
            'selected_material': None, 'input_sha256': {str(preparation): corpus.sha(preparation), str(regions): corpus.sha(regions)},
            'policy': asdict(policy), 'maximum_samples_per_hypothesis': maximum_samples_per_hypothesis,
            'groups': [{'source_part_index': 0, 'report': {'path': 'part-000/fit.json', 'sha256': corpus.sha(group_report)}}],
            'previews': [{'status': 'diagnostic_preview_exported', 'family': 'uniform_tint',
                'path': preview.name, 'sha256': corpus.sha(preview), 'export': {'path': export.name, 'sha256': corpus.sha(export)}}]}
        # Mimic the actual stage's JSON-stable return values (dataclass tuples
        # are normalized by the runner before policy comparison).
        result = json.loads(json.dumps(result))
        write(output / 'report.json', result)
        return result

    def execute_fake(self, receipt):
        with patch.object(corpus, 'run_optical_preparation', side_effect=self.fake_preparation), \
             patch.object(corpus, 'run_photo_lens_stage', side_effect=self.fake_fit):
            return corpus.execute(receipt, self.output)

    def test_fixed_policy_all_five_and_diagnostic_export_links(self):
        receipt = self.prepare(selected=())
        report = self.execute_fake(receipt)
        self.assertEqual(len(report['cases']), 5)
        self.assertTrue(report['full_corpus_experiment_complete'])
        self.assertFalse(report['full_generation_complete'])
        self.assertFalse(report['accepted'])
        self.assertIsNone(report['selected_material'])
        self.assertEqual(report['cases_with_diagnostic_previews'], 5)
        for row in report['cases']:
            preview = row['previews'][0]
            self.assertEqual(corpus.sha(self.output / preview['path']), preview['sha256'])
            self.assertEqual(corpus.sha(self.output / preview['export']['path']), preview['export']['sha256'])
            group_report = row['groups'][0]['report']
            self.assertEqual(corpus.sha(self.output / group_report['path']), group_report['sha256'])
        stages = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in (self.output / 'cases').rglob('*') if p.is_file()}
        self.assertEqual(self.prepare(selected=(), resume=True), receipt)
        with patch.object(corpus, 'run_optical_preparation', side_effect=AssertionError('Do not repeat completed preparation')), \
             patch.object(corpus, 'run_photo_lens_stage', side_effect=AssertionError('Do not repeat completed fitting')):
            again = corpus.execute(receipt, self.output)
        self.assertEqual(again, report)
        self.assertEqual(stages, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in stages})

    def test_interrupted_fit_retains_attempt_and_reuses_preparation(self):
        receipt = self.prepare()
        def interrupted(preparation, regions, output, **kwargs):
            output.mkdir(parents=True)
            (output / 'partial.json').write_bytes(b'Preserve interrupted candidate ensemble')
            raise RuntimeError('intentional fit interruption')
        with patch.object(corpus, 'run_optical_preparation', side_effect=self.fake_preparation), \
             patch.object(corpus, 'run_photo_lens_stage', side_effect=interrupted):
            failed = corpus.execute(receipt, self.output)
        self.assertFalse(failed['execution_complete'])
        old = self.output / 'cases/rayban/fit/attempt-001/partial.json'
        with patch.object(corpus, 'run_optical_preparation', side_effect=AssertionError('Reuse preparation')), \
             patch.object(corpus, 'run_photo_lens_stage', side_effect=self.fake_fit):
            resumed = corpus.execute(receipt, self.output)
        self.assertTrue(resumed['execution_complete'])
        self.assertFalse(resumed['full_corpus_experiment_complete'])
        self.assertEqual(old.read_bytes(), b'Preserve interrupted candidate ensemble')
        row = resumed['cases'][0]
        self.assertEqual(len(row['stages']['preparation']), 1)
        self.assertEqual(len(row['stages']['fit']), 2)
        self.assertIn('attempt-002', row['fit_report']['path'])

    def test_actual_no_optical_parts_is_completed_experiment_not_generation(self):
        receipt = self.prepare()
        with patch.object(corpus, 'run_photo_lens_stage', side_effect=AssertionError('No optical parts to fit')):
            report = corpus.execute(receipt, self.output)
        self.assertTrue(report['execution_complete'])
        self.assertEqual(report['cases'][0]['preparation_status'], 'no_candidate_optical_parts')
        self.assertEqual(report['cases'][0]['fit_status'], 'not_prepared')
        self.assertFalse(report['full_generation_complete'])
        self.assertEqual(report['cases_with_diagnostic_previews'], 0)

    def test_changed_region_artifact_is_rejected_before_output(self):
        mask = self.regions / 'cases/rayban/attempt-001/mask.png'
        mask.write_bytes(b'changed mask')
        with self.assertRaisesRegex(ValueError, 'artifacts changed'):
            self.prepare()
        self.assertFalse(self.output.exists())

    def test_completed_output_tamper_never_reruns_or_claims_complete(self):
        receipt = self.prepare()
        result = self.execute_fake(receipt)
        path = self.output / result['cases'][0]['previews'][0]['path']
        path.write_bytes(b'modified preview')
        with patch.object(corpus, 'run_optical_preparation', side_effect=AssertionError('Do not replace evidence')), \
             patch.object(corpus, 'run_photo_lens_stage', side_effect=AssertionError('Do not replace evidence')):
            report = corpus.execute(receipt, self.output)
        self.assertFalse(report['execution_complete'])
        self.assertIn('artifacts changed', report['cases'][0]['error'])
        self.assertEqual(path.read_bytes(), b'modified preview')

    def test_policy_implementation_and_inputs_are_pinned(self):
        receipt = self.prepare()
        with patch.object(corpus, 'implementation', return_value={'fixture': 'changed'}):
            with self.assertRaisesRegex(ValueError, 'implementation changed'):
                corpus.execute(receipt, self.output)
            with self.assertRaisesRegex(ValueError, 'pinned inputs, policy or implementation changed'):
                self.prepare(resume=True)
        changed = json.loads(json.dumps(receipt))
        changed['policy']['maximum_optimization_runs'] = 1
        with self.assertRaisesRegex(ValueError, 'receipt or implementation changed'):
            corpus.execute(changed, self.output)
        (self.root / 'front.png').write_bytes(b'changed original photo')
        with self.assertRaisesRegex(ValueError, 'Pinned input changed'):
            corpus.execute(receipt, self.output)

    def test_diagnostic_preview_export_binding_is_checked_before_completion(self):
        receipt = self.prepare()
        def malformed(*args, **kwargs):
            result = self.fake_fit(*args, **kwargs)
            output = args[2]
            path = output / 'preview-export.json'
            binding = json.loads(path.read_text())
            binding['source_sha256'] = '0'*64
            write(path, binding)
            result['previews'][0]['export']['sha256'] = corpus.sha(path)
            write(output / 'report.json', result)
            return result
        with patch.object(corpus, 'run_optical_preparation', side_effect=self.fake_preparation), \
             patch.object(corpus, 'run_photo_lens_stage', side_effect=malformed):
            report = corpus.execute(receipt, self.output)
        self.assertFalse(report['execution_complete'])
        self.assertIn('export lineage differs', report['cases'][0]['error'])
        self.assertEqual(report['cases'][0]['stages']['fit'][0]['status'], 'failed')

    def test_source_mutation_during_fit_is_not_labeled_complete(self):
        receipt = self.prepare()
        def mutating(*args, **kwargs):
            result = self.fake_fit(*args, **kwargs)
            (self.regions / 'cases/rayban/attempt-001/mask.png').write_bytes(b'changed during fit')
            return result
        with patch.object(corpus, 'run_optical_preparation', side_effect=self.fake_preparation), \
             patch.object(corpus, 'run_photo_lens_stage', side_effect=mutating):
            with self.assertRaisesRegex(ValueError, 'artifacts changed'):
                corpus.execute(receipt, self.output)
        report = json.loads((self.output / 'report.json').read_text())
        self.assertFalse(report['execution_complete'])
        self.assertFalse(report['cases'][0]['execution_complete'])


if __name__ == '__main__':
    unittest.main()
