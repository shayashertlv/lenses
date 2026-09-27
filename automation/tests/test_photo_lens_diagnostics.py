"""Saved replay parity and coverage auditing; no retuning or image-specific rules."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest

import numpy as np
from PIL import Image

from reconstruction.photo_lens_diagnostics import diagnose_photo_lens_fit, _linear_objective_supported
from reconstruction.photo_lens_fit import PhotoLensFitPolicy, fit_photo_lens_candidates
from test_photo_lens_fit import BINDING, observation


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, allow_nan=False), encoding='utf-8')


class PhotoLensDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / 'artifacts';self.source.mkdir()
        self.runtime = self.root / 'snapshot';(self.runtime / 'reconstruction').mkdir(parents=True)
        for path in (Path(__file__).resolve().parents[1] / 'reconstruction').glob('*.py'):
            shutil.copy2(path, self.runtime / 'reconstruction' / path.name)
        self.mask = self.root / 'mask.png'
        Image.fromarray(np.full((8, 8), 255, np.uint8)).save(self.mask)
        first = observation(150)
        first['provenance']['mask_sha256'] = sha(self.mask)
        x, y = first['xy'].T
        rear = (x >= 6) & (y >= 4)  # frozen tiles select none of this category for validation
        first['rear_weight'] = rear.astype(float)
        first['rear_rgb'] = np.full((len(x), 3), np.nan)
        first['code_rgb'][rear] = np.where((y[rear] % 2)[:, None] == 0, 20, 220)
        second = copy.deepcopy(first)
        second['id'] = 'front/lens-0/1';second['hypothesis_id'] = '1'
        # Distinct alternative, consistent source-pixel values, different support.
        second['intrinsic_v'][0] = np.nan
        self.observations = [first, second]
        policy = PhotoLensFitPolicy(rear_content='explained', families=('uniform_tint',), lighting_families=('constant',),
                                    roughness_values=(.05, .25), max_nfev=25, minimum_validation_points_per_photo=1)
        self.fit = fit_photo_lens_candidates(self.observations, surface_binding=BINDING, policy=policy)
        self.fit_path = self.source / 'fit.json';write(self.fit_path, self.fit)
        rows = []
        for i, obs in enumerate(self.observations):
            arrays = {k: v for k, v in obs.items() if isinstance(v, np.ndarray)}
            path = self.source / f'observation-{i}.npz';np.savez_compressed(path, **arrays)
            rows.append({**{k: v for k, v in obs.items() if k not in arrays}, 'arrays': {'path': path.name, 'sha256': sha(path)}})
        self.observations_path = self.source / 'observations.json'
        write(self.observations_path, {'surface_binding': BINDING, 'observations': rows})
        self.bridge = self.root / 'bridge.json';write(self.bridge, {'input_sha256': {str(self.mask): sha(self.mask)}})

    def run_diagnostic(self, name='diagnostics', **kwargs):
        return diagnose_photo_lens_fit(self.fit_path, self.observations_path, self.root / name,
            runtime_root=self.runtime, **kwargs)

    def test_all_candidates_exact_replay_unmeasured_rear_validation_and_frozen_sources(self):
        originals = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.source.iterdir()}
        runtime = {p: (sha(p), p.stat().st_mtime_ns) for p in (self.runtime / 'reconstruction').glob('*.py')}
        result = self.run_diagnostic(observation_report_path=self.bridge)
        self.assertEqual(result['status'], 'diagnostics_complete')
        self.assertEqual(result['summary']['candidates_replayed'], len(self.fit['candidates']))
        self.assertEqual(len(result['candidates']), 12)
        self.assertTrue(result['summary']['all_candidates_audited'])
        self.assertFalse(result['accepted']);self.assertIsNone(result['selected_material'])
        self.assertGreater(result['summary']['candidate_cells_with_training_policy_violations_and_unmeasured_validation'], 0)
        self.assertEqual({o['hypothesis_id'] for o in result['observations']}, {'0', '1'})
        for row in result['observations']:
            rear = [c for c in row['cells'] if c['cell'].endswith('/rear_proxy')]
            self.assertTrue(rear)
            self.assertTrue(all(c['validation_status'] == 'unmeasured' and c['validation'] == 0 for c in rear))
            self.assertEqual(row['boundary']['status'], 'measured_mask_geometry')
        for candidate in result['candidates']:
            self.assertEqual(candidate['objective']['status'], 'verified')
            self.assertLess(candidate['maximum_measurement_difference'], 1e-9)
        self.assertEqual(originals, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in originals})
        self.assertEqual(runtime, {p: (sha(p), p.stat().st_mtime_ns) for p in runtime})
        self.assertEqual(set(runtime), {p for p in (self.runtime / 'reconstruction').rglob('*') if p.is_file()})
        self.assertTrue(result['diagnostic_driver']['bytecode_writes_disabled'])
        self.assertEqual(sha(self.root / 'diagnostics/replay-driver.py'), result['diagnostic_driver']['sha256'])
        representative = result['representatives'][0]['observations'][0]
        path = self.root / 'diagnostics' / representative['path']
        self.assertEqual(sha(path), representative['sha256'])
        with np.load(path) as arrays:
            np.testing.assert_array_equal(arrays['train'] | arrays['validation'], arrays['eligible'])
        json.dumps(result, allow_nan=False)

    def test_missing_boundary_evidence_is_unmeasured_not_zero_distance(self):
        result = self.run_diagnostic()
        self.assertTrue(all(o['boundary']['status'] == 'unmeasured' for o in result['observations']))
        self.assertTrue(all(o['train_boundary_distance']['status'] == 'unmeasured' for o in result['observations']))
        # Missing optional originals remain explicit; changed existing originals fail.
        self.mask.unlink()
        result = self.run_diagnostic('missing', observation_report_path=self.bridge)
        self.assertEqual(len(result['optional_source_unavailable']), 1)
        self.assertEqual(result['status'], 'diagnostics_complete')

    def test_saved_measurement_mismatch_is_machine_readable_and_not_verified(self):
        self.fit['candidates'][0]['photo_measurements'][0]['train']['mean_absolute_interval_error_codes'] += 2
        write(self.fit_path, self.fit)
        result = self.run_diagnostic()
        self.assertEqual(result['status'], 'replay_incomplete')
        self.assertFalse(result['summary']['all_candidates_audited'])
        self.assertEqual(result['summary']['candidates_replayed'], 11)
        self.assertEqual(result['candidates'][0]['replay_status'], 'mismatch_or_unavailable')
        self.assertTrue(any(f['code'] == 'replay_unavailable' for f in result['failures']))

    def test_npz_and_self_consistent_replacement_inputs_cannot_inherit_saved_fit(self):
        manifest = json.loads(self.observations_path.read_text())
        path = self.source / manifest['observations'][0]['arrays']['path']
        with np.load(path) as archive:
            arrays = {k: archive[k] for k in archive.files}
        arrays['intrinsic_v'][10] += .01
        np.savez_compressed(path, **arrays)
        with self.assertRaisesRegex(ValueError, 'Frozen diagnostic replay failed'):
            self.run_diagnostic('hash-mismatch')
        manifest['observations'][0]['arrays']['sha256'] = sha(path)
        write(self.observations_path, manifest)
        with self.assertRaisesRegex(ValueError, 'Frozen diagnostic replay failed'):
            self.run_diagnostic('fit-binding-mismatch')
        failure = json.loads((self.root / 'fit-binding-mismatch/failure.json').read_text())
        self.assertIn('Fit input hash differs', failure['error'])

    def test_changed_boundary_source_and_nonempty_or_source_output_are_rejected(self):
        self.mask.write_bytes(b'changed mask bytes')
        with self.assertRaisesRegex(ValueError, 'Frozen diagnostic replay failed'):
            self.run_diagnostic(observation_report_path=self.bridge)
        with self.assertRaisesRegex(ValueError, 'empty diagnostic'):
            self.run_diagnostic()
        with self.assertRaisesRegex(ValueError, 'must not mutate source'):
            diagnose_photo_lens_fit(self.fit_path, self.observations_path, self.source / 'diagnostics', runtime_root=self.runtime)

    def test_objective_convention_is_not_guessed_for_other_losses(self):
        prefix = 'def fit_photo_lens_candidates():\n    '
        self.assertTrue(_linear_objective_supported(prefix+'least_squares(model.residual, x)'))
        self.assertFalse(_linear_objective_supported(prefix+'least_squares(model.residual, x, loss="soft_l1")'))
        self.assertFalse(_linear_objective_supported(prefix+'least_squares(model.residual, x, **settings)'))
        # Faithful predictions are still available, but objective parity cannot
        # be advertised when the explicit snapshot's loss convention is unknown.
        path = self.runtime / 'reconstruction/photo_lens_fit.py'
        source = path.read_text(encoding='utf-8')
        source = source.replace('max_nfev=policy.max_nfev, ftol=', 'loss="soft_l1", max_nfev=policy.max_nfev, ftol=')
        path.write_text(source, encoding='utf-8')
        result = self.run_diagnostic()
        self.assertTrue(all(c['objective']['status'] == 'unavailable' for c in result['candidates']))
        self.assertTrue(result['summary']['all_candidates_audited'])


if __name__ == '__main__':
    unittest.main()
