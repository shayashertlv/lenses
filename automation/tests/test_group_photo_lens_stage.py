"""Actual multipart preparation -> photo sampling -> joint fit -> GLB preview."""
import hashlib
import json
import unittest
from unittest.mock import patch

import numpy as np

from reconstruction.joint_photo_lens_fit import JointPhotoLensFitPolicy, fit_joint_photo_lens_candidates
from reconstruction.joint_photo_lens_stage import run_joint_photo_lens_stage
from reconstruction.optical_group_asset import read_optical_group_candidate
from reconstruction.photo_lens_fit import PhotoLensFitPolicy
from reconstruction.photo_lens_stage import load_optical_fit_inputs, run_photo_lens_stage
import test_optical_group_observations as group_fixture
from test_optical_groups import FRAME


class GroupPhotoLensStageTests(unittest.TestCase):
    def setUp(self):
        group_fixture.ExportedGroupObservationTests.setUp(self)
        from reconstruction.prepare_optical_groups import run_optical_group_preparation
        declarations = {'schema_version': 1, 'source_sha256': self.sha(self.source),
            'coordinate_frame': FRAME, 'provenance': {'method': 'explicit synthetic fixture'},
            'groups': [{'group_id': 'physical-group', 'members': [
                {'id': f'member-{i}', 'source_part_index': i,
                 'source_binding': {'node_index': i, 'mesh_index': i, 'primitive_index': 0}}
                for i in (0, 1)]}]}
        self.prepared_folder = self.folder/'prepared'
        with patch('reconstruction.prepare_optical_groups.implementation_manifest', return_value={'test': 'frozen'}):
            prepared = run_optical_group_preparation(self.source, self.prepared_folder,
                grouping_mode='explicit_declarations', declarations=declarations)
        self.assertEqual(prepared['status'], 'prepared_optical_group_candidate')
        self.preparation = self.prepared_folder/'report.json'
        self.policy = JointPhotoLensFitPolicy(photo_policy=PhotoLensFitPolicy(
            families=('uniform_tint',), lighting_families=('constant',), roughness_values=(.05, .25), max_nfev=8, minimum_validation_points_per_photo=1))

    def inputs(self):
        return load_optical_fit_inputs(self.preparation, self.region_path, maximum_samples_per_hypothesis=64)

    def run_stage(self, name='joint', **kwargs):
        with patch('reconstruction.joint_photo_lens_stage.implementation_manifest', return_value={'test': 'frozen'}):
            return run_joint_photo_lens_stage(self.preparation, self.region_path, self.folder/name,
                policy=self.policy, maximum_samples_per_hypothesis=64, **kwargs)

    def test_complete_actual_group_fit_preview_preserves_geometry_and_resume(self):
        inputs = self.inputs()
        self.assertEqual(set(inputs['prepared_groups']), {'physical-group'})
        self.assertEqual(len(inputs['observations']['groups']), 1)
        self.assertEqual(len(inputs['observations']['groups'][0]['observations']), 6)
        baseline = read_optical_group_candidate(inputs['model'], inputs['export_receipt'])
        report = self.run_stage()
        self.assertEqual(report['status'], 'diagnostic_previews_available')
        self.assertEqual(report['optical_profile'], 'effective_optical_group_v1_experiment')
        self.assertFalse(report['accepted']); self.assertIsNone(report['selected_material'])
        self.assertEqual(report['group_inventory'], inputs['group_inventory'])
        output = self.folder/'joint'
        fit = json.loads((output/report['fit']['path']).read_text())
        for preview in report['previews']:
            receipt = json.loads((output/preview['export']['path']).read_text())
            candidate = next(row for row in fit['candidates'] if row['candidate_id'] == preview['candidate_id'])
            self.assertEqual(receipt['groups'][0]['appearance'], candidate['groups']['physical-group']['appearance'])
            actual = read_optical_group_candidate(output/preview['path'], receipt)
            for key in ('uv', 'normals', 'face_groups'):
                np.testing.assert_array_equal(actual[key], baseline[key])
            np.testing.assert_array_equal(actual['mesh'].vertices, baseline['mesh'].vertices)
            np.testing.assert_array_equal(actual['mesh'].faces, baseline['mesh'].faces)
        snapshot = lambda: {p.relative_to(output).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
                            for p in output.rglob('*') if p.is_file()}
        before = snapshot()
        with patch('reconstruction.joint_photo_lens_stage.fit_joint_photo_lens_candidates', side_effect=AssertionError('must reuse')):
            self.assertEqual(self.run_stage(resume=True), report)
        self.assertEqual(snapshot(), before)

    def test_grouped_profiles_do_not_silently_enter_independent_front_sheet_export(self):
        output = self.folder/'independent'
        with self.assertRaisesRegex(ValueError, 'require the joint'):
            run_photo_lens_stage(self.preparation, self.region_path, output)
        self.assertFalse(output.exists())

    def test_saved_declarations_and_inventory_must_describe_bound_groups(self):
        original = self.preparation.read_bytes()
        prep = json.loads(original)
        declaration_path = self.prepared_folder/prep['declarations']['path']
        declaration_bytes = declaration_path.read_bytes()
        for target in ('declarations', 'grouping_inventory', 'provenance'):
            with self.subTest(target=target):
                altered = json.loads(original)
                declaration = json.loads(declaration_bytes)
                if target == 'grouping_inventory':
                    altered['grouping_inventory'][0]['group_id'] = 'contradictory-group'
                else:
                    if target == 'declarations':
                        declaration['groups'][0]['group_id'] = 'contradictory-group'
                    else:
                        declaration['provenance'] = {'method': 'contradictory identity origin'}
                    declaration_path.write_text(json.dumps(declaration))
                    altered['declarations']['sha256'] = self.sha(declaration_path)
                self.preparation.write_text(json.dumps(altered))
                with self.assertRaisesRegex(ValueError, 'declarations|inventory'):
                    self.inputs()
                declaration_path.write_bytes(declaration_bytes)
        self.preparation.write_bytes(original)

    def test_snapshot_based_resume_allows_missing_original_but_rejects_changed_original(self):
        source_bytes = self.source.read_bytes()
        before = self.inputs()
        result = self.run_stage()
        self.source.unlink()
        after = self.inputs()
        self.assertEqual(before['pins'], after['pins'])
        self.assertEqual(before['optional_original_sha256'], after['optional_original_sha256'])
        with patch('reconstruction.joint_photo_lens_stage.fit_joint_photo_lens_candidates', side_effect=AssertionError('must reuse')):
            self.assertEqual(self.run_stage(resume=True), result)
        self.source.write_bytes(source_bytes+b'changed original')
        with self.assertRaisesRegex(ValueError, 'changed|hash'):
            self.run_stage(resume=True)

    def test_declaration_member_order_is_preserved_provenance_not_group_identity(self):
        from reconstruction.prepare_optical_groups import run_optical_group_preparation
        declarations = json.loads((self.prepared_folder/'declarations.json').read_text())
        declarations['groups'][0]['members'].reverse()
        folder = self.folder/'reverse-order'
        with patch('reconstruction.prepare_optical_groups.implementation_manifest', return_value={'test': 'frozen'}):
            run_optical_group_preparation(self.source, folder, grouping_mode='explicit_declarations', declarations=declarations)
        loaded = load_optical_fit_inputs(folder/'report.json', self.region_path, maximum_samples_per_hypothesis=64)
        self.assertEqual(set(loaded['prepared_groups']), {'physical-group'})
        self.assertEqual(loaded['prepared_groups']['physical-group']['report']['identity']['provenance']['members'],
                         declarations['groups'][0]['members'])

    def test_original_source_change_during_fit_invalidates_terminal_receipt(self):
        def mutate_original(groups, **kwargs):
            fitted = fit_joint_photo_lens_candidates(groups, **kwargs)
            self.source.write_bytes(self.source.read_bytes()+b'changed during fit')
            return fitted
        with patch('reconstruction.joint_photo_lens_stage.fit_joint_photo_lens_candidates', side_effect=mutate_original):
            with self.assertRaisesRegex(ValueError, 'changed|hash'):
                self.run_stage()
        self.assertFalse((self.folder/'joint/receipt.json').exists())

    def test_all_group_and_member_records_are_required_and_array_tampering_rejected(self):
        preparation = json.loads(self.preparation.read_text())
        record = preparation['groups'][0]['primitives'][0]
        path = self.prepared_folder/record['path']
        with np.load(path, allow_pickle=False) as arrays:
            values = {key: arrays[key].copy() for key in arrays.files}
        values['uv'][0, 1] += .01
        np.savez_compressed(path, **values)
        # Even a self-consistent outer artifact hash cannot redefine the
        # source/prepared/actual-export coordinate binding.
        record['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.preparation.write_text(json.dumps(preparation))
        with self.assertRaisesRegex(ValueError, 'report hash|fitted group GLB|differs'):
            self.inputs()

    def test_missing_observations_and_unknown_profile_never_produce_a_complete_preview(self):
        for photo in self.regions['photos']:
            photo['regions'] = []
        self.region_path.write_text(json.dumps(self.regions))
        report = self.run_stage()
        self.assertEqual(report['status'], 'no_complete_preview')
        self.assertFalse(report['previews'])
        prep = json.loads(self.preparation.read_text()); prep['optical_profile'] = 'unknown-profile'
        self.preparation.write_text(json.dumps(prep))
        with self.assertRaisesRegex(ValueError, 'Unknown optical preparation profile'):
            self.inputs()

    def test_interrupted_group_fit_reuses_only_bound_standalone_checkpoints(self):
        def interrupted(groups, **kwargs):
            original = kwargs['progress']
            def progress(event):
                original(event)
                if event.get('event') == 'completed':
                    raise RuntimeError('controlled grouped interruption')
            kwargs['progress'] = progress
            return fit_joint_photo_lens_candidates(groups, **kwargs)
        with patch('reconstruction.joint_photo_lens_stage.fit_joint_photo_lens_candidates', side_effect=interrupted):
            with self.assertRaisesRegex(RuntimeError, 'controlled grouped interruption'):
                self.run_stage()
        old = self.folder/'joint/attempts/attempt-001/failure.json'; saved = old.read_bytes()
        result = self.run_stage(resume=True)
        self.assertEqual(result['status'], 'diagnostic_previews_available')
        self.assertEqual(old.read_bytes(), saved)
        self.assertTrue(result['fit']['path'].startswith('attempts/attempt-002/'))


if __name__ == '__main__':
    unittest.main()
