import json
import hashlib
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

import numpy as np

from reconstruction.deform_glb import _read
from reconstruction.joint_photo_lens_fit import JointPhotoLensFitPolicy, fit_joint_photo_lens_candidates
from reconstruction.joint_photo_lens_stage import run_joint_photo_lens_stage, joint_preview_representatives
from reconstruction.photo_lens_fit import PhotoLensFitPolicy
import test_photo_lens_stage as fixture


class JointPhotoLensStageTests(unittest.TestCase):
    def setUp(self):
        fixture.PhotoLensStageTests.setUp(self)
        self.policy = JointPhotoLensFitPolicy(photo_policy=PhotoLensFitPolicy(
            families=('uniform_tint',), lighting_families=('constant',), roughness_values=(.05, .25), max_nfev=8, minimum_validation_points_per_photo=1))

    def run_stage(self, directory='joint', **kwargs):
        with patch('reconstruction.joint_photo_lens_stage.implementation_manifest', return_value={'test': 'frozen'}):
            return run_joint_photo_lens_stage(self.preparation, self.regions_path, self.folder / directory,
                policy=self.policy, maximum_samples_per_hypothesis=64, **kwargs)

    def test_cli_explicit_analytic_mode_is_pinned_in_stage_policy(self):
        from reconstruction.joint_photo_lens_stage import main
        arguments = ['joint_photo_lens_stage', '--preparation', str(self.preparation), '--regions', str(self.regions_path),
                     '--output', str(self.folder/'cli'), '--jacobian-mode', 'analytic']
        with patch('sys.argv', arguments), patch('builtins.print'), \
             patch('reconstruction.joint_photo_lens_stage.run_joint_photo_lens_stage',
                   return_value={'status': 'no_complete_preview', 'quality_verdict': 'unmeasured', 'previews': []}) as run:
            main()
        self.assertEqual(run.call_args.kwargs['policy'].jacobian_mode, 'analytic')

    def test_real_joint_fit_exports_bound_descriptor_and_terminal_resume_is_immutable(self):
        report = self.run_stage()
        self.assertEqual(report['status'], 'diagnostic_previews_available')
        self.assertFalse(report['accepted']); self.assertIsNone(report['selected_material'])
        root = self.folder / 'joint'
        fit = json.loads((root / report['fit']['path']).read_text())
        preview = report['previews'][0]
        candidate = next(c for c in fit['candidates'] if c['candidate_id'] == preview['candidate_id'])
        _, doc, _ = _read(root / preview['path'])
        self.assertEqual(doc['materials'][-1]['extensions']['LENSES_lens_appearance']['appearance'],
                         candidate['groups']['source-part-0']['appearance'])
        self.assertEqual(len(candidate['groups']['source-part-0']['appearance_alternatives']), 2)
        before = {p.relative_to(root).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
                  for p in root.rglob('*') if p.is_file()}
        with patch('reconstruction.joint_photo_lens_stage.fit_joint_photo_lens_candidates', side_effect=AssertionError('must reuse')):
            self.assertEqual(self.run_stage(resume=True), report)
        after = {p.relative_to(root).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
                 for p in root.rglob('*') if p.is_file()}
        self.assertEqual(before, after)
        (root / preview['path']).write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, 'integrity'):
            self.run_stage(resume=True)

    def test_appearance_prior_source_is_pinned_and_mutation_prevents_resume(self):
        from reconstruction.photo_lens_stage import load_optical_fit_inputs
        inputs = load_optical_fit_inputs(self.preparation, self.regions_path, maximum_samples_per_hypothesis=64)
        groups = list(inputs['observations']['groups'].values())
        photos = {o['photo_id']: {'source_sha256': o['source_sha256'], 'illuminant_rgb': [1., 1., 1.],
                  'illumination_hypothesis': 'neutral_studio_hypothesis', 'reflection_regions': [],'interface':'rear'}
                  for group in groups for o in group['observations']}
        priors = {'schema_version': 1, 'method': 'semantic_material_priors_v1',
                  'source_image_sha256': sorted({p['source_sha256'] for p in photos.values()}),
                  'photos': photos, 'groups': {}, 'declared_facts': {}}
        path = self.folder/'appearance-priors.json'
        path.write_text(json.dumps(priors), encoding='utf-8')
        report = self.run_stage(appearance_prior_report=path)
        self.assertEqual(len({r['path'] for r in report['previews']}),2)
        self.assertEqual({r['rear_response_hypothesis'] for r in report['previews']},
                         {'free_rear_reflection','weak_rear_reflection'})
        self.assertEqual(report['input_sha256'][str(path)], hashlib.sha256(path.read_bytes()).hexdigest())
        fit = json.loads((self.folder/'joint'/report['fit']['path']).read_text())
        self.assertEqual(fit['appearance_priors']['photos'], photos)
        self.assertIn('not_independent_holdout', report['validation_scope'])
        self.assertEqual(self.run_stage(resume=True, appearance_prior_report=path), report)
        priors['photos'][next(iter(photos))]['illumination_hypothesis'] = 'changed hypothesis'
        path.write_text(json.dumps(priors), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.run_stage(resume=True, appearance_prior_report=path)

    def test_missing_group_observations_never_get_a_guessed_material(self):
        self.regions['photos'][0]['regions'] = []
        self.regions_path.write_text(json.dumps(self.regions))
        report = self.run_stage()
        self.assertEqual(report['status'], 'no_complete_preview')
        self.assertFalse(report['previews'])
        self.assertFalse(list((self.folder/'joint').rglob('*.glb')))

    def test_interrupted_attempt_is_retained_and_same_request_can_continue(self):
        def interrupted(groups, **kwargs):
            # Actual fitter checkpoints one start before an external interruption.
            original_progress = kwargs['progress']
            def progress(event):
                original_progress(event)
                if event.get('event') == 'completed':
                    raise RuntimeError('controlled interruption after durable start')
            kwargs['progress'] = progress
            return fit_joint_photo_lens_candidates(groups, **kwargs)
        with patch('reconstruction.joint_photo_lens_stage.fit_joint_photo_lens_candidates', side_effect=interrupted):
            with self.assertRaisesRegex(RuntimeError, 'controlled interruption'):
                self.run_stage()
        root = self.folder/'joint'
        failure = root/'attempts/attempt-001/failure.json'
        before = failure.read_bytes()
        report = self.run_stage(resume=True)
        self.assertEqual(report['status'], 'diagnostic_previews_available')
        self.assertEqual(failure.read_bytes(), before)
        self.assertTrue(report['fit']['path'].startswith('attempts/attempt-002/'))

    def test_changed_input_on_resume_and_mutation_during_fit_are_refused(self):
        self.run_stage()
        self.regions['photos'][0]['source_sha256'] = '0'*64
        self.regions_path.write_text(json.dumps(self.regions))
        with self.assertRaises(ValueError):
            self.run_stage(resume=True)

    def test_photo_mutation_during_joint_fit_prevents_completed_receipt(self):
        def mutate(groups, **kwargs):
            fitted = fit_joint_photo_lens_candidates(groups, **kwargs)
            Path(self.regions['photos'][0]['source']).write_bytes(b'changed during fit')
            return fitted
        with patch('reconstruction.joint_photo_lens_stage.fit_joint_photo_lens_candidates', side_effect=mutate):
            with self.assertRaisesRegex(ValueError, 'Source or implementation changed'):
                self.run_stage()
        self.assertFalse((self.folder/'joint/receipt.json').exists())

    def test_representatives_select_whole_joint_candidate_without_mixing_lenses(self):
        def candidate(cid, a, b):
            return {'candidate_id': cid, 'photo_policy_status': 'outside_declared_policy',
                'optimizer': {'converged': True, 'objective_including_priors': a+b},
                'groups': {key: {'family': 'gradient_tint', 'photo_metrics': [
                    {'validation': {'points': 1, 'mean_absolute_interval_error_codes': value}}]}
                    for key, value in [('left', a), ('right', b)]}}
        first, second = candidate('first', 1., 10.), candidate('second', 6., 6.)
        result = joint_preview_representatives({'candidates': [first, second]})
        self.assertEqual(len(result), 1)
        self.assertIs(next(iter(result.values())), second)

    def interrupt_before_optimization(self):
        with patch('reconstruction.joint_photo_lens_stage.fit_joint_photo_lens_candidates',
                   side_effect=RuntimeError('controlled pre-optimizer interruption')):
            with self.assertRaisesRegex(RuntimeError, 'controlled pre-optimizer'):
                self.run_stage()
        return self.folder/'joint'

    def make_junction(self, link, target):
        if os.name != 'nt':
            self.skipTest('NTFS junction regression requires Windows')
        self.assertTrue(link.resolve().is_relative_to(self.folder.resolve()))
        self.assertTrue(target.resolve().is_relative_to(self.folder.resolve()))
        command = ("New-Item -ItemType Junction -Path '"+str(link).replace("'", "''")
                   +"' -Target '"+str(target).replace("'", "''")+"' | Out-Null")
        subprocess.run(['powershell', '-NoProfile', '-Command', command], check=True, capture_output=True)
        self.assertTrue(link.is_junction())

    def test_resume_rejects_actual_windows_junction_before_external_writes(self):
        root = self.interrupt_before_optimization()
        (root/'attempts').rename(root/'preserved-attempts')
        external = self.folder/'external'; external.mkdir()
        self.make_junction(root/'attempts', external)
        lock_before = ((root/'.lock').read_bytes(), (root/'.lock').stat().st_mtime_ns)
        with patch('reconstruction.joint_photo_lens_stage.fit_joint_photo_lens_candidates',
                   side_effect=AssertionError('must reject before fitting')):
            with self.assertRaisesRegex(ValueError, 'reparse'):
                self.run_stage(resume=True)
        self.assertFalse(list(external.iterdir()))
        self.assertEqual(lock_before, ((root/'.lock').read_bytes(), (root/'.lock').stat().st_mtime_ns))
        self.assertFalse((root/'receipt.json').exists())

    def test_lock_reparse_is_rejected_before_open(self):
        root = self.folder/'joint'; root.mkdir()
        external = self.folder/'external-lock'; external.mkdir()
        marker = external/'preserved'; marker.write_bytes(b'unchanged')
        self.make_junction(root/'.lock', external)
        with self.assertRaisesRegex(ValueError, 'reparse'):
            self.run_stage()
        self.assertEqual(marker.read_bytes(), b'unchanged')
        self.assertEqual([p.name for p in external.iterdir()], ['preserved'])
        self.assertFalse((root/'request.json').exists())

    def test_resume_rejects_changed_input_receipt_pointer(self):
        root = self.interrupt_before_optimization()
        path = root/'inputs.json'; saved = json.loads(path.read_text())
        saved['groups']['0'] = 'does-not-exist.json'
        path.write_text(json.dumps(saved))
        with self.assertRaisesRegex(ValueError, 'group pointer'):
            self.run_stage(resume=True)
        self.assertFalse((root/'attempts/attempt-002').exists())

    def test_resume_rejects_self_consistent_snapshot_replacement(self):
        root = self.interrupt_before_optimization()
        receipt_path = root/'inputs.json'; saved = json.loads(receipt_path.read_text())
        metadata_path = root/saved['groups']['0']
        metadata = json.loads(metadata_path.read_text())
        row = metadata['observations'][0]
        array_path = metadata_path.parent/row['arrays']['path']
        with np.load(array_path, allow_pickle=False) as archive:
            arrays = {key: archive[key].copy() for key in archive.files}
        arrays['code_rgb'][0, 0] ^= np.uint8(1)
        np.savez_compressed(array_path, **arrays)
        row['arrays']['sha256'] = hashlib.sha256(array_path.read_bytes()).hexdigest()
        metadata_path.write_text(json.dumps(metadata))
        for path in (array_path, metadata_path):
            saved['artifacts'][path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        receipt_path.write_text(json.dumps(saved))
        with self.assertRaisesRegex(ValueError, 'arrays differ from regenerated'):
            self.run_stage(resume=True)
        self.assertFalse((root/'attempts/attempt-002').exists())


if __name__ == '__main__':
    unittest.main()
