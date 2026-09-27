"""Opt-in grouped job orchestration, immutable declarations, and recovery.

Fault tests use small stage substitutes. The end-to-end test uses real intake,
initialization, region masks, group preparation, joint fitting and preview export;
only camera estimates and the segmentation engine are controlled fixtures.
"""
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from reconstruction import job
import test_job as job_fixture
from test_job import _nested_diagnostic_fit, _read, _sha, _successful_stage, _write
from test_optical_groups import FRAME
from test_region_proposals import BoxEngine


def _group_preparation(model, output, *, grouping_mode, declarations=None):
    output.mkdir(parents=True)
    if declarations is not None:
        (output/'declarations.json').write_bytes(declarations.read_bytes())
    (output/'source.glb').write_bytes(model.read_bytes())
    report = {'status': 'prepared_optical_group_candidate', 'optical_profile': job.GROUP_PROFILE,
              'source_sha256': _sha(model), 'grouping_mode': grouping_mode}
    _write(output/'report.json', report)
    return report


class GroupJobTests(unittest.TestCase):
    save_request = job_fixture.JobIntegrityTests.save_request

    def setUp(self):
        job_fixture.JobIntegrityTests.setUp(self)
        # These fixtures bind declarations and cameras to the source units; a stated
        # frame width would rescale the model and is covered by the scale tests.
        self.request['dimensions_mm'] = {'bridge_width': 18}
        self.save_request()
        self.declaration = {'schema_version': 1, 'source_sha256': _sha(self.model),
            'coordinate_frame': deepcopy(FRAME), 'provenance': {'method': 'supplied test hypothesis'},
            'groups': [{'group_id': 'one', 'members': [{'id': 'member', 'source_part_index': 0,
                'source_binding': {'node_index': 0, 'mesh_index': 0, 'primitive_index': 0}}]}]}
        self.declarations_path = self.folder/'groups.json'
        _write(self.declarations_path, self.declaration)
        self.options = {'resolution': 128, 'camera_evaluations': 20, 'region_engine': BoxEngine(),
            'lens_candidates': True, 'lens_fit_mode': 'joint', 'optical_profile': job.GROUP_PROFILE,
            'optical_grouping': 'explicit_declarations', 'optical_group_declarations': self.declarations_path}

    def run_group(self, **changes):
        return job.run_job(self.request_path, self.output, **{**self.options, **changes})

    def complete(self, **changes):
        with patch.object(job, 'refine', side_effect=_successful_stage), \
             patch('reconstruction.prepare_optical_groups.run_optical_group_preparation', side_effect=_group_preparation), \
             patch('reconstruction.joint_photo_lens_stage.run_joint_photo_lens_stage', side_effect=_nested_diagnostic_fit):
            return self.run_group(**changes)

    def test_invalid_combinations_and_malformed_json_reject_before_writes(self):
        for changes in ({'optical_profile': 'unknown'}, {'lens_candidates': False}, {'lens_fit_mode': 'independent'},
                        {'optical_grouping': None}, {'optical_group_declarations': None},
                        {'optical_profile': job.FRONT_SHEET_PROFILE},
                        {'optical_grouping': 'source_part_hypotheses'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.run_group(**changes)
            self.assertFalse(self.output.exists())
        for raw in (b'{"schema_version":1,"schema_version":1}', b'{"schema_version":NaN}', b'{}',
                    b'\xef\xbb\xbf'+json.dumps(self.declaration).encode()):
            self.declarations_path.write_bytes(raw)
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                self.run_group()
            self.assertFalse(self.output.exists())

    def test_both_modes_dispatch_and_bind_complete_stage_settings(self):
        for mode in ('explicit_declarations', 'source_part_hypotheses'):
            with self.subTest(mode=mode):
                self.output = self.folder/mode
                declarations = self.declarations_path if mode == 'explicit_declarations' else None
                with patch.object(job, 'refine', side_effect=_successful_stage), \
                     patch('reconstruction.prepare_optical_groups.run_optical_group_preparation', side_effect=_group_preparation) as prepare, \
                     patch('reconstruction.joint_photo_lens_stage.run_joint_photo_lens_stage', side_effect=_nested_diagnostic_fit) as fit:
                    result = self.run_group(optical_grouping=mode, optical_group_declarations=declarations)
                settings = result['settings']['optical_groups']
                self.assertEqual(settings['profile'], job.GROUP_PROFILE)
                self.assertEqual(prepare.call_args.kwargs['grouping_mode'], mode)
                passed = prepare.call_args.kwargs['declarations']
                if declarations:
                    self.assertEqual(passed, self.output/'optical-group-declarations.json')
                    self.assertEqual(passed.read_bytes(), declarations.read_bytes())
                    self.assertEqual(settings['declarations']['sha256'], _sha(declarations))
                    self.assertEqual(settings['declarations']['source_sha256'], _sha(self.model))
                else:
                    self.assertIsNone(passed); self.assertIsNone(settings['declarations'])
                self.assertEqual(fit.call_count, 1)
                journal = _read(self.output/'job.json')
                for name in ('optical_preparation', 'photo_lens_fit'):
                    stage = journal['stages'][name][0]
                    self.assertEqual(stage['optical_settings'], settings)
                    self.assertEqual(stage['optical_settings_sha256'], job._settings_sha256(settings))
                    self.assertEqual(stage['optical_source']['sha256'], _sha(self.model))
                optics = result['optical_candidates']
                self.assertEqual(optics['optical_profile'], job.GROUP_PROFILE)
                self.assertEqual(optics['preview_export_status'], 'diagnostic_previews_available')
                self.assertFalse(optics['accepted']); self.assertIsNone(optics['selected_material'])
                self.assertEqual((self.output/'candidate.glb').read_bytes(), self.model.read_bytes())

    def test_completed_reuse_and_missing_original_are_immutable(self):
        result = self.complete()
        self.declarations_path.unlink()
        before = {p.relative_to(self.output): (p.read_bytes(), p.stat().st_mtime_ns)
                  for p in self.output.rglob('*') if p.is_file()}
        with patch.object(job, 'refine', side_effect=AssertionError('must reuse')), \
             patch('reconstruction.prepare_optical_groups.run_optical_group_preparation', side_effect=AssertionError('must reuse')):
            self.assertEqual(self.run_group(), result)
        after = {p.relative_to(self.output): (p.read_bytes(), p.stat().st_mtime_ns)
                 for p in self.output.rglob('*') if p.is_file()}
        self.assertEqual(before, after)

    def test_original_snapshot_stage_and_metadata_tampering_refuse_reuse(self):
        self.complete()
        for path in (self.declarations_path, self.output/'optical-group-declarations.json',
                     self.output/'stages/optical_preparation/attempt_1/declarations.json'):
            original = path.read_bytes(); path.write_bytes(original+b' ')
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.run_group()
            self.assertEqual(path.read_bytes(), original+b' ')
            path.write_bytes(original)
        path = self.output/'job.json'; original = path.read_bytes()
        for field, value in (('optical_settings', {}), ('optical_settings_sha256', 'a'*64),
                             ('optical_input_artifacts', {'optical-group-declarations.json': _sha(self.declarations_path)})):
            journal = json.loads(original)
            journal['stages']['optical_preparation'][0][field] = value
            _write(path, journal)
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'optical stage|Optical stage'):
                self.run_group()
            path.write_bytes(original)
        with self.assertRaisesRegex(ValueError, 'Settings or implementation changed'):
            self.run_group(optical_grouping='source_part_hypotheses', optical_group_declarations=None)
        with self.assertRaisesRegex(ValueError, 'Settings or implementation changed'):
            self.run_group(optical_profile=job.FRONT_SHEET_PROFILE, optical_grouping=None, optical_group_declarations=None)

    def test_declaration_change_during_preparation_invalidates_job(self):
        def mutate(*args, **kwargs):
            result = _group_preparation(*args, **kwargs)
            self.declarations_path.write_bytes(self.declarations_path.read_bytes()+b' ')
            return result
        with patch.object(job, 'refine', side_effect=_successful_stage), \
             patch('reconstruction.prepare_optical_groups.run_optical_group_preparation', side_effect=mutate), \
             patch('reconstruction.joint_photo_lens_stage.run_joint_photo_lens_stage', side_effect=AssertionError('must not fit')):
            with self.assertRaisesRegex(ValueError, 'Original optical declarations changed'):
                self.run_group()
        self.assertFalse((self.output/'candidate.glb').exists())
        self.assertFalse(_read(self.output/'job.json')['terminal'])

    def test_declaration_alias_and_snapshot_reparse_rejected_before_reuse(self):
        def directory_alias(link, target):
            self.assertTrue(link.absolute().is_relative_to(self.folder))
            self.assertTrue(target.resolve().is_relative_to(self.folder))
            if os.name == 'nt':
                command = ("New-Item -ItemType Junction -Path '"+str(link).replace("'", "''")
                           +"' -Target '"+str(target).replace("'", "''")+"' | Out-Null")
                subprocess.run(['powershell', '-NoProfile', '-Command', command], check=True, capture_output=True)
            else:
                link.symlink_to(target, target_is_directory=True)

        original_dir = self.folder/'declaration-directory'; original_dir.mkdir()
        (original_dir/'groups.json').write_bytes(self.declarations_path.read_bytes())
        alias = self.folder/'declaration-alias'; directory_alias(alias, original_dir)
        with self.assertRaisesRegex(ValueError, 'reparse|symlink'):
            self.run_group(optical_group_declarations=alias/'groups.json')
        self.assertFalse(self.output.exists())
        self.complete()
        # The saved snapshot is checked by its unresolved path even though an
        # otherwise valid original is available. No terminal/lock rewrite occurs.
        snapshot = self.output/'optical-group-declarations.json'
        snapshot.rename(self.output/'preserved-declarations.json')
        directory_alias(snapshot, original_dir)
        journal = (self.output/'job.json').read_bytes()
        lock = ((self.output/'.lock').read_bytes(), (self.output/'.lock').stat().st_mtime_ns)
        with self.assertRaisesRegex(ValueError, 'reparse|symlink'):
            self.run_group()
        self.assertEqual((self.output/'job.json').read_bytes(), journal)
        self.assertEqual(((self.output/'.lock').read_bytes(), (self.output/'.lock').stat().st_mtime_ns), lock)
        self.assertEqual((original_dir/'groups.json').read_bytes(), self.declarations_path.read_bytes())
        self.declarations_path.unlink()
        with self.assertRaisesRegex(ValueError, 'reparse|symlink'):
            self.run_group()

    def test_real_preparer_source_mismatch_preserves_geometry_without_fit(self):
        # A declaration for the initializer must not silently migrate to a
        # retained refinement, even if its primitive ordering happens to match.
        def changed_refinement(model, photos, output, **settings):
            from reconstruction.deform_glb import _read as read_glb
            from reconstruction.lens_asset import _pack_glb
            report = _successful_stage(model, photos, output, **settings)
            _, document, binary = read_glb(output/'proposal.glb')
            document['asset']['extras'] = {'fixture': 'different retained source bytes'}
            (output/'proposal.glb').write_bytes(_pack_glb(document, binary))
            report['export']['output_sha256'] = _sha(output/'proposal.glb')
            _write(output/'report.json', report)
            return report
        with patch.object(job, 'refine', side_effect=changed_refinement), \
             patch('reconstruction.joint_photo_lens_stage.run_joint_photo_lens_stage', side_effect=AssertionError('unsupported source must not fit')):
            result = self.run_group()
        self.assertEqual(result['status'], 'candidate_available')
        self.assertEqual(result['optical_candidates']['preparation_status'], 'unsupported_optical_group_preparation')
        self.assertEqual(result['optical_candidates']['fit_status'], 'not_prepared')
        self.assertNotEqual(_sha(self.output/'candidate.glb'), _sha(self.model))
        self.assertEqual((self.output/'candidate.glb').read_bytes(),
                         (self.output/'stages/refinement/attempt_1/proposal.glb').read_bytes())
        self.assertFalse(result['quality']['accepted'])

    def test_interrupted_fit_keeps_old_attempt_and_reuses_group_preparation(self):
        def interrupt(preparation, regions, output, **kwargs):
            output.mkdir(parents=True)
            (output/'partial.json').write_bytes(b'preserved')
            raise RuntimeError('group fit interrupted')
        with patch.object(job, 'refine', side_effect=_successful_stage), \
             patch('reconstruction.prepare_optical_groups.run_optical_group_preparation', side_effect=_group_preparation), \
             patch('reconstruction.joint_photo_lens_stage.run_joint_photo_lens_stage', side_effect=interrupt):
            with self.assertRaisesRegex(RuntimeError, 'group fit interrupted'):
                self.run_group()
        with patch('reconstruction.prepare_optical_groups.run_optical_group_preparation', side_effect=AssertionError('must reuse')), \
             patch('reconstruction.joint_photo_lens_stage.run_joint_photo_lens_stage', side_effect=_nested_diagnostic_fit) as fit:
            result = self.run_group()
        self.assertEqual(fit.call_args.args[2], self.output/'stages/photo_lens_fit/attempt_2')
        self.assertNotIn('resume', fit.call_args.kwargs)
        self.assertEqual((self.output/'stages/photo_lens_fit/attempt_1/partial.json').read_bytes(), b'preserved')
        self.assertIsNone(result['optical_candidates']['selected_material'])

    def test_cli_forwards_profile_and_rejects_invalid_combinations_before_engine(self):
        args = ['--request', str(self.request_path), '--output', str(self.output), '--lens-candidates',
                '--region-weights', str(self.folder/'checkpoint.pt'), '--lens-fit-mode', 'joint',
                '--optical-profile', job.GROUP_PROFILE, '--optical-grouping', 'explicit_declarations',
                '--optical-group-declarations', str(self.declarations_path)]
        with redirect_stdout(io.StringIO()), patch('reconstruction.region_engine.OfflineSAM2RegionEngine', return_value=BoxEngine()), \
             patch.object(job, 'run_job', return_value={'status': 'candidate_available', 'quality_verdict': 'unmeasured', 'candidate': None}) as run:
            job.main(args)
        self.assertEqual(run.call_args.kwargs['optical_profile'], job.GROUP_PROFILE)
        self.assertEqual(run.call_args.kwargs['optical_grouping'], 'explicit_declarations')
        self.assertEqual(run.call_args.kwargs['optical_group_declarations'], self.declarations_path)
        args[args.index('joint')] = 'independent'
        with redirect_stderr(io.StringIO()), patch('reconstruction.region_engine.OfflineSAM2RegionEngine', side_effect=AssertionError('must reject first')):
            with self.assertRaises(SystemExit) as error:
                job.main(args)
        self.assertEqual(error.exception.code, 2)

    def test_actual_group_preparation_observations_joint_fit_and_preview(self):
        from reconstruction.camera import Camera
        from reconstruction.deform_glb import _read as read_glb
        from reconstruction.lens_asset import _pack_glb
        from reconstruction.joint_photo_lens_fit import JointPhotoLensFitPolicy
        from reconstruction.photo_lens_fit import PhotoLensFitPolicy
        from reconstruction.optical_group_asset import read_optical_group_candidate
        from test_optical_group_observations import ExportedGroupObservationTests

        fixture = ExportedGroupObservationTests()
        fixture.setUp(); self.addCleanup(fixture.doCleanups)
        _, document, binary = read_glb(fixture.source)
        for node in document['nodes'][:2]:
            node['name'] = 'lens '+node['name']
            node['extras'] = {'partRole': 'lens'}
        self.model = self.folder/'multipart.glb'
        self.model.write_bytes(_pack_glb(document, binary))
        self.request['initializer'] = {'kind': 'existing_glb', 'path': str(self.model), 'sha256': _sha(self.model)}
        self.request['photos'] = [{'id': name, 'view': name, 'path': str(fixture.folder/f'{name}.png')}
                                  for name in ('front', 'back')]
        self.save_request()
        self.declaration['source_sha256'] = _sha(self.model)
        self.declaration['groups'][0]['members'] = [
            {'id': f'member-{i}', 'source_part_index': i,
             'source_binding': {'node_index': i, 'mesh_index': i, 'primitive_index': 0}} for i in (0, 1)]
        _write(self.declarations_path, self.declaration)

        def known_cameras(model, photos, output, **settings):
            output.mkdir(parents=True)
            report = {'status': 'controlled_camera_fixture', 'source_sha256': _sha(model),
                'normalization': {'center': [0, 0, 0], 'extent': 1}, 'views': [
                    {'view_id': photo.id, 'source_sha256': _sha(photo.path), 'image_size_original': [64, 64],
                     'image_size_working': [64, 64],
                     'camera_fit': {'camera': Camera(0 if photo.id == 'front' else 180, 0, 0, 0, 40, 32, 32).to_dict()}}
                    for photo in photos]}
            _write(output/'report.json', report)
            return report

        reduced_photo = PhotoLensFitPolicy(families=('uniform_tint',), lighting_families=('constant',),
                                          roughness_values=(.05,), max_nfev=5, minimum_validation_points_per_photo=1)
        original_init = JointPhotoLensFitPolicy.__init__
        def bounded_policy(instance, *args, **kwargs):
            kwargs.setdefault('photo_policy', reduced_photo)
            original_init(instance, *args, **kwargs)
        with patch.object(job, 'refine', side_effect=known_cameras), \
             patch.object(JointPhotoLensFitPolicy, '__init__', bounded_policy):
            result = self.run_group(lens_maximum_samples=64)
        self.assertEqual(result['optical_candidates']['preparation_status'], 'prepared_optical_group_candidate')
        self.assertEqual(result['optical_candidates']['fit_status'], 'diagnostic_previews_available')
        self.assertEqual((self.output/'candidate.glb').read_bytes(), self.model.read_bytes())
        self.assertFalse(result['quality']['accepted']); self.assertIsNone(result['optical_candidates']['selected_material'])
        preview = result['optical_candidates']['previews'][0]
        self.assertEqual(_sha(self.output/preview['export']['path']), preview['export']['sha256'])
        exported = read_optical_group_candidate(self.output/preview['path'], _read(self.output/preview['export']['path']),
            expected_sha256=preview['sha256'])
        self.assertEqual(len(exported['groups']), 1)
        self.assertEqual(next(iter(exported['groups'].values()))['source_part_indices'], [0, 1])
        self.assertEqual(len(exported['mesh'].parts), 3)  # Both group members and the opaque frame survive.
        before = {p.relative_to(self.output): (p.read_bytes(), p.stat().st_mtime_ns)
                  for p in self.output.rglob('*') if p.is_file()}
        with patch.object(JointPhotoLensFitPolicy, '__init__', bounded_policy), \
             patch('reconstruction.joint_photo_lens_stage.run_joint_photo_lens_stage', side_effect=AssertionError('must reuse actual fit')):
            self.assertEqual(self.run_group(lens_maximum_samples=64), result)
        self.assertEqual(before, {p.relative_to(self.output): (p.read_bytes(), p.stat().st_mtime_ns)
                                 for p in self.output.rglob('*') if p.is_file()})


class InferredGroupJobTests(unittest.TestCase):
    """The photos-only grouping mode: apertures, groups, bridge, regions, fit, selection."""
    save_request = job_fixture.JobIntegrityTests.save_request

    def setUp(self):
        job_fixture.JobIntegrityTests.setUp(self)
        self.request['dimensions_mm'] = {'bridge_width': 18}
        from test_physical_group_stage import FakeApertureEngine, write_scene
        import numpy as np
        from PIL import Image
        self.model = self.folder/'scene.glb'
        write_scene(self.model, [(-.6, .6, -.6, .6, .2, .3, 0), (-.55, .55, -.55, .55, .24, .28, 1), (.65, .75, -.6, .6, -.1, .1, -1)])
        self.photo_paths = []
        for name, shade in (('front', 150), ('back', 151)):
            pixels = np.full((64, 64, 3), 245, np.uint8); pixels[8:57, 8:57] = [shade, 120, 100]
            path = self.folder/f'{name}.png'; Image.fromarray(pixels).save(path); self.photo_paths.append(path)
        self.request['initializer'] = {'kind': 'existing_glb', 'path': str(self.model), 'sha256': _sha(self.model)}
        self.request['photos'] = [{'id': name, 'view': name, 'path': str(path)} for name, path in zip(('front', 'back'), self.photo_paths)]
        self.save_request()
        self.engine = FakeApertureEngine()
        self.options = {'resolution': 128, 'camera_evaluations': 20, 'region_engine': BoxEngine(),
                        'lens_candidates': True, 'lens_fit_mode': 'joint', 'optical_profile': job.GROUP_PROFILE,
                        'optical_grouping': job.INFERRED_GROUPING, 'aperture_engine': self.engine}

    def cameras(self, model, photos, output, **settings):
        from reconstruction.camera import Camera
        output.mkdir(parents=True)
        report = {'status': 'controlled_camera_fixture', 'source_sha256': _sha(model),
                  'normalization': {'center': [0, 0, 0], 'extent': 1}, 'views': [
                      {'view_id': photo.id, 'source_sha256': _sha(photo.path), 'image_size_original': [64, 64],
                       'image_size_working': [64, 64],
                       'camera_fit': {'camera': Camera(0 if photo.id == 'front' else 180, 0, 0, 0, 40, 32, 32).to_dict()}}
                      for photo in photos]}
        _write(output/'report.json', report)
        return report

    def run_inferred(self, **changes):
        from reconstruction.joint_photo_lens_fit import JointPhotoLensFitPolicy
        from reconstruction.photo_lens_fit import PhotoLensFitPolicy
        reduced = PhotoLensFitPolicy(families=('uniform_tint', 'gradient_tint'), lighting_families=('constant',),
                                     roughness_values=(.05,), max_nfev=5, minimum_validation_points_per_photo=1)
        original_init = JointPhotoLensFitPolicy.__init__
        def bounded(instance, *args, **kwargs):
            kwargs.setdefault('photo_policy', reduced)
            original_init(instance, *args, **kwargs)
        with patch.object(job, 'refine', side_effect=self.cameras), patch.object(JointPhotoLensFitPolicy, '__init__', bounded):
            return job.run_job(self.request_path, self.output, **{**self.options, 'lens_maximum_samples': 64, **changes})

    def test_declared_lens_facts_restrict_the_fitted_families(self):
        from reconstruction.job import lens_policy_for_facts
        from reconstruction.joint_photo_lens_fit import JointPhotoLensFitPolicy
        from reconstruction.photo_lens_fit import FAMILIES, PhotoLensFitPolicy
        full = JointPhotoLensFitPolicy(photo_policy=PhotoLensFitPolicy(families=FAMILIES))
        same, note = lens_policy_for_facts(full, {})
        self.assertIs(same, full); self.assertIsNone(note)
        tints, note = lens_policy_for_facts(full, {'mirror_coating': False})
        self.assertEqual(tints.photo_policy.families, ('uniform_tint', 'gradient_tint'))
        self.assertEqual(note['families'], ['uniform_tint', 'gradient_tint'])
        mirrors, _ = lens_policy_for_facts(full, {'mirror_coating': True})
        self.assertEqual(mirrors.photo_policy.families, ('colored_mirror', 'angular_mirror', 'gradient_angular_mirror'))
        single, _ = lens_policy_for_facts(PhotoLensFitPolicy(families=FAMILIES), {'mirror_coating': True})
        self.assertEqual(single.families, ('colored_mirror', 'angular_mirror', 'gradient_angular_mirror'))
        with self.assertRaises(ValueError):
            lens_policy_for_facts(JointPhotoLensFitPolicy(photo_policy=PhotoLensFitPolicy(families=('uniform_tint',))), {'mirror_coating': True})
        # Through the job: the fit runs with the tints only and the journal says so.
        request = json.loads(self.request_path.read_text())
        request['lens_facts'] = {'mirror_coating': False}
        self.request_path.write_text(json.dumps(request))
        result = self.run_inferred()
        self.assertEqual(result['lens_facts'], {'mirror_coating': False})
        stage = json.loads((self.output / 'job.json').read_text())['stages']['photo_lens_fit'][-1]
        self.assertEqual(stage['lens_facts']['families'], ['uniform_tint', 'gradient_tint'])
        fit_report = json.loads((self.output / result['optical_candidates']['fit_report']).read_text())
        fit = json.loads((self.output / Path(result['optical_candidates']['fit_report']).parent / fit_report['fit']['path']).read_text())
        self.assertEqual(fit['policy']['photo_policy']['families'], ['uniform_tint', 'gradient_tint'])

    def test_residual_replay_reproduces_the_saved_metrics_and_draws_every_photo(self):
        from qa.joint_fit_residuals import replay
        self.run_inferred()
        summary = replay(self.output, self.output/'residuals')
        json.dumps(summary, allow_nan=False)
        self.assertTrue(summary['verification'] and all(v['agrees'] for v in summary['verification']))
        self.assertGreater(summary['samples'], 0)
        for table in summary['tables']:
            self.assertTrue((self.output/'residuals'/f"{table['photo_id']}-residuals.png").exists())
            self.assertTrue((self.output/'residuals'/f"{table['photo_id']}-residuals-crop.png").exists())
            self.assertTrue(table['by_intrinsic_height'] and table['by_incidence_angle'] and table['by_edge_distance'])
        self.assertFalse(summary['accepted'])
        with self.assertRaises(ValueError):
            replay(self.output, self.output/'residuals')
        with self.assertRaises(ValueError):
            replay(self.output, self.output/'residuals-other', candidate_id='absent')

    def test_option_pairing_is_validated_before_any_write(self):
        for changes in ({'aperture_engine': None}, {'optical_grouping': 'explicit_declarations', 'optical_group_declarations': self.folder/'x.json'},
                        {'optical_group_declarations': self.folder/'x.json'}, {'appearance_tolerance_codes': -1}, {'aperture_engine': object()}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                job.run_job(self.request_path, self.output, **{**self.options, **changes})
            self.assertFalse(self.output.exists())
        with patch.object(job, 'run_job', return_value={'status': 'candidate_available', 'quality_verdict': 'unmeasured', 'candidate': None}):
            args = ['--request', str(self.request_path), '--output', str(self.output), '--lens-candidates',
                    '--region-weights', str(self.folder/'checkpoint.pt'), '--lens-fit-mode', 'joint',
                    '--optical-profile', job.GROUP_PROFILE, '--optical-grouping', job.INFERRED_GROUPING]
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                job.main(args)
            self.assertEqual(error.exception.code, 2)

    def test_inferred_mode_runs_every_stage_selects_an_appearance_and_reuses_immutably(self):
        result = self.run_inferred()
        optics = result['optical_candidates']
        self.assertEqual(result['status'], 'candidate_available')
        self.assertEqual(optics['grouping_mode'], job.INFERRED_GROUPING)
        self.assertEqual(optics['physical_groups']['status'], 'hypotheses_bridged')
        self.assertEqual(optics['physical_groups']['selected_hypothesis']['composition_rank'], 0)
        self.assertEqual(optics['preparation_status'], 'prepared_optical_group_candidate')
        self.assertEqual(optics['fit_status'], 'diagnostic_previews_available')
        self.assertEqual(result['region_observations']['candidate_scope'], 'bridged partitioned candidate of the selected hypothesis')
        self.assertEqual(len(optics['previews']), 2)
        selection = optics['appearance_selection']
        self.assertEqual(selection['status'], 'appearance_selected')
        self.assertEqual(selection['method'], 'least_complex_family_within_tolerance_v3')
        appearance = optics['appearance_candidate']
        self.assertEqual(appearance['family_assignment'], {'group-0000': 'uniform_tint'} if selection['identifiability'] == 'families_indistinguishable_within_tolerance'
                         else appearance['family_assignment'])
        self.assertFalse(appearance['accepted']); self.assertIsNone(optics['selected_material'])
        candidate = result['candidate']
        self.assertEqual((self.output/'candidate.glb').read_bytes(), self.model.read_bytes())
        self.assertEqual(_sha(self.output/candidate['appearance']['path']), appearance['sha256'])
        self.assertEqual(candidate['appearance']['path'], job.APPEARANCE_CANDIDATE)
        journal = _read(self.output/'job.json')
        for name in ('physical_groups', 'hypothesis_regions', 'photo_lens_fit', 'appearance_selection'):
            self.assertEqual(journal['stages'][name][0]['status'], 'complete', name)
        self.assertNotIn('regions', journal['stages'])
        self.assertEqual(journal['final_artifacts'][job.APPEARANCE_CANDIDATE], appearance['sha256'])
        self.assertEqual(result['settings']['optical_groups']['aperture_engine'], self.engine.describe())
        before = {p.relative_to(self.output): (p.read_bytes(), p.stat().st_mtime_ns) for p in self.output.rglob('*') if p.is_file()}
        with patch.object(self.engine, 'propose', side_effect=AssertionError('must reuse apertures')), \
             patch('reconstruction.joint_photo_lens_stage.run_joint_photo_lens_stage', side_effect=AssertionError('must reuse fit')):
            self.assertEqual(self.run_inferred(), result)
        self.assertEqual(before, {p.relative_to(self.output): (p.read_bytes(), p.stat().st_mtime_ns) for p in self.output.rglob('*') if p.is_file()})
        # A changed aperture engine identity is a different job.
        class Other(type(self.engine)):
            def describe(self):
                return {'kind': 'OtherEngine'}
        with self.assertRaisesRegex(ValueError, 'Settings or implementation changed'):
            self.run_inferred(aperture_engine=Other())


if __name__ == '__main__':
    unittest.main()
