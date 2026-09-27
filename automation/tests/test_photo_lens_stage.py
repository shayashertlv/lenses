import json
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np

from reconstruction.deform_glb import _read
from reconstruction.photo_lens_fit import PhotoLensFitPolicy
from reconstruction.photo_lens_stage import run_photo_lens_stage
import test_photo_lens_observations as binding_fixture
from test_optical_asset import surface


class PhotoLensStageTests(unittest.TestCase):
    def setUp(self):
        binding_fixture.OpticalPhotoBindingTests.setUp(self)
        path = self.folder / 'surface.npz'
        np.savez_compressed(path, **{k: v for k, v in surface().items() if isinstance(v, np.ndarray)})
        preparation = {'status': 'prepared_optical_candidate', 'model': {'path': self.model.name, 'sha256': binding_fixture.sha(self.model)},
            'export': {'path': self.export_path.name, 'sha256': binding_fixture.sha(self.export_path)},
            'source': str(self.folder / 'source.glb'), 'source_sha256': self.export['source_sha256'],
            'parts': [{'source_part_index': 0, 'surfaces': [{'id': 'sheet', 'path': path.name, 'sha256': binding_fixture.sha(path)}]}]}
        self.preparation = self.folder / 'preparation.json'
        self.preparation.write_text(json.dumps(preparation))

    def test_actual_fit_exports_exact_candidate_descriptor_and_preserves_unaccepted_state(self):
        policy = PhotoLensFitPolicy(families=('uniform_tint',), lighting_families=('constant',),
                                    roughness_values=(.05,), max_nfev=15, minimum_validation_points_per_photo=1)
        with patch('reconstruction.photo_lens_stage.implementation_manifest', return_value={'test': 'frozen'}):
            report = run_photo_lens_stage(self.preparation, self.regions_path, self.folder / 'fit',
                policy=policy, maximum_samples_per_hypothesis=64)
        self.assertEqual(report['status'], 'diagnostic_previews_available')
        self.assertFalse(report['accepted'])
        self.assertIsNone(report['selected_material'])
        preview = next(row for row in report['previews'] if row['status'] == 'diagnostic_preview_exported')
        fit = json.loads((self.folder / 'fit/part-000/fit.json').read_text())
        chosen = next(c for c in fit['candidates'] if c['candidate_id'] == preview['candidate_ids']['0'])
        _, doc, _ = _read(self.folder / 'fit' / preview['path'])
        descriptor = doc['materials'][-1]['extensions']['LENSES_lens_appearance']['appearance']
        self.assertEqual(descriptor, chosen['appearance'])
        self.assertEqual(fit['exploration']['mask_branches'], 1)
        self.assertEqual(len(fit['duplicate_hypothesis_aliases'][0]['observation_ids']), 3)
        self.assertTrue((self.folder / 'fit/part-000/observation-000.npz').is_file())

    def test_missing_observations_do_not_produce_guessed_lenses(self):
        self.regions['photos'][0]['regions'] = []
        self.regions_path.write_text(json.dumps(self.regions))
        with patch('reconstruction.photo_lens_stage.implementation_manifest', return_value={'test': 'frozen'}):
            report = run_photo_lens_stage(self.preparation, self.regions_path, self.folder / 'empty-fit')
        self.assertEqual(report['status'], 'no_complete_preview')
        self.assertEqual(report['groups'][0]['status'], 'no_photo_observations')
        self.assertFalse(list((self.folder / 'empty-fit').glob('*.glb')))

    def test_source_mutation_is_rejected_before_any_fit(self):
        (self.folder / 'source.glb').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'source hash changed'):
            run_photo_lens_stage(self.preparation, self.regions_path, self.folder / 'bad')
        self.assertFalse((self.folder / 'bad').exists())

    def test_preparation_replaced_after_read_cannot_pin_new_bytes_with_old_status(self):
        original = Path.read_bytes
        changed = False
        def replace_after_read(path):
            nonlocal changed
            raw = original(path)
            if path.resolve() == self.preparation.resolve() and not changed:
                replacement = json.loads(raw)
                replacement['status'] = 'unsupported'
                path.write_bytes(json.dumps(replacement).encode('utf-8'))
                changed = True
            return raw
        with patch.object(Path, 'read_bytes', replace_after_read), \
             patch('reconstruction.photo_lens_stage.fit_photo_lens_candidates', side_effect=AssertionError('must not fit')):
            with self.assertRaisesRegex(ValueError, 'dependency changed while loading'):
                run_photo_lens_stage(self.preparation, self.regions_path, self.folder / 'raced-preparation')
        self.assertTrue(changed)
        self.assertFalse((self.folder / 'raced-preparation').exists())

    def test_surface_archive_is_loaded_from_the_same_bytes_as_its_pin(self):
        archive_path = self.folder / 'surface.npz'
        original_load = np.load
        received_snapshots = []
        def replace_before_load(value, **kwargs):
            received_snapshots.append(hasattr(value, 'read'))
            # The already captured valid archive must be consumed, not reopened
            # after this replacement. The final dependency check then rejects it.
            archive_path.write_bytes(b'replaced archive after capture')
            return original_load(value, **kwargs)
        with patch('reconstruction.photo_lens_stage.np.load', side_effect=replace_before_load), \
             patch('reconstruction.photo_lens_stage.fit_photo_lens_candidates', side_effect=AssertionError('must not fit')):
            with self.assertRaisesRegex(ValueError, 'dependency changed while loading'):
                run_photo_lens_stage(self.preparation, self.regions_path, self.folder / 'raced-archive')
        self.assertEqual(received_snapshots, [True])
        self.assertFalse((self.folder / 'raced-archive').exists())

    def test_self_consistent_replacement_source_cannot_inherit_another_models_fit(self):
        source = self.folder / 'source.glb'
        from reconstruction.lens_asset import _pack_glb
        _, document, binary = _read(source)
        document['nodes'][0]['translation'][0] += 1
        source.write_bytes(_pack_glb(document, binary))
        preparation = json.loads(self.preparation.read_text())
        preparation['source_sha256'] = binding_fixture.sha(source)
        self.preparation.write_text(json.dumps(preparation))
        with self.assertRaisesRegex(ValueError, 'source lineage'):
            run_photo_lens_stage(self.preparation, self.regions_path, self.folder / 'bad-lineage')

    def test_self_consistent_npz_replacement_cannot_change_fitted_coordinates(self):
        path = self.folder / 'surface.npz'
        arrays = {k: v.copy() for k, v in surface().items() if isinstance(v, np.ndarray)}
        arrays['positions'][:, 0] += .2
        np.savez_compressed(path, **arrays)
        preparation = json.loads(self.preparation.read_text())
        preparation['parts'][0]['surfaces'][0]['sha256'] = binding_fixture.sha(path)
        self.preparation.write_text(json.dumps(preparation))
        with self.assertRaisesRegex(ValueError, 'positions arrays differ'):
            run_photo_lens_stage(self.preparation, self.regions_path, self.folder / 'bad-array')

    def test_photo_mutation_during_fit_prevents_final_report(self):
        from reconstruction.photo_lens_fit import fit_photo_lens_candidates
        def mutate_photo(*args, **kwargs):
            result = fit_photo_lens_candidates(*args, **kwargs)
            Path(self.regions['photos'][0]['source']).write_bytes(b'changed during fit')
            return result
        policy = PhotoLensFitPolicy(families=('uniform_tint',), lighting_families=('constant',),
                                    roughness_values=(.05,), max_nfev=5, minimum_validation_points_per_photo=1)
        with patch('reconstruction.photo_lens_stage.fit_photo_lens_candidates', side_effect=mutate_photo), \
             patch('reconstruction.photo_lens_stage.implementation_manifest', return_value={'test': 'frozen'}):
            with self.assertRaisesRegex(ValueError, 'Source or implementation changed'):
                run_photo_lens_stage(self.preparation, self.regions_path, self.folder / 'mutated-fit',
                                     policy=policy, maximum_samples_per_hypothesis=64)
        self.assertFalse((self.folder / 'mutated-fit/report.json').exists())


if __name__ == '__main__':
    unittest.main()
