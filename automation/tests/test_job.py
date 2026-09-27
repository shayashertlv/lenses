"""Job integrity and recovery controls, with real inputs and isolated stage faults.

Most tests substitute only refinement to induce precise orchestration states;
input normalization, GLB initialization, receipts, locking and final selection
are real. One test also runs the actual refinement stage without substitution.
"""
import hashlib
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from reconstruction import job
from test_refine_photos import _simple_inputs
from test_region_proposals import BoxEngine


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read(path):
    return json.loads(Path(path).read_bytes())


def _write(path, value):
    Path(path).write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')


def _successful_stage(model, photos, output, **settings):
    output.mkdir(parents=True)
    (output / 'proposal.glb').write_bytes(model.read_bytes())
    _write(output / 'evidence.json', {'scope': 'orchestration fixture; not semantic evidence'})
    report = {'status': 'proposal_exported', 'quality_verdict': 'unmeasured',
              'source_sha256': _sha(model), 'proposal_artifact': 'proposal.glb',
              'export': {'output_sha256': _sha(output / 'proposal.glb')},
              'rerender_nonregression': True,
              'views': [{'view_id': photo.id, 'source_sha256': _sha(photo.path),
                         'rerender': {'nonregression': True, 'geometry_source': 'exported_glb_float32'}}
                        for photo in photos]}
    _write(output / 'report.json', report)
    return report


def _prepared_optics(model, output):
    output.mkdir(parents=True)
    report = {'status': 'prepared_optical_candidate', 'source_sha256': _sha(model)}
    _write(output / 'report.json', report)
    return report


def _nested_diagnostic_fit(preparation, regions, output, **kwargs):
    folder = output / 'attempts' / 'attempt-001'
    folder.mkdir(parents=True)
    (folder / 'preview.glb').write_bytes(b'job orchestration diagnostic fixture')
    _write(folder / 'export.json', {'scope': 'orchestration fixture'})
    report = {'status': 'diagnostic_previews_available', 'previews': [{
        'status': 'diagnostic_preview_exported', 'family_assignment': {'group-a': 'uniform_tint'},
        'path': 'attempts/attempt-001/preview.glb', 'sha256': _sha(folder / 'preview.glb'),
        'export': {'path': 'attempts/attempt-001/export.json', 'sha256': _sha(folder / 'export.json')}}]}
    _write(output / 'report.json', report)
    return report


class JobIntegrityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.model, self.photos = _simple_inputs(self.folder)
        self.request_path = self.folder / 'request.json'
        self.request = {'schema_version': 1,
                        'photos': [{'id': f'photo-{i}', 'view': view, 'path': str(path)}
                                   for i, (view, path) in enumerate(self.photos)],
                        'dimensions_mm': {'frame_width': 137, 'bridge_width': 18},
                        'initializer': {'kind': 'existing_glb', 'path': str(self.model), 'sha256': _sha(self.model)}}
        self.save_request()
        self.output = self.folder / 'job'
        # Concurrent developers may edit source files during these isolated
        # orchestration tests. Source-change detection has its own explicit test.
        source_pin = patch.object(job, 'implementation_manifest', return_value={'source_sha256': {'test_policy': 'f'*64}, 'packages': {}})
        source_pin.start()
        self.addCleanup(source_pin.stop)

    def save_request(self):
        _write(self.request_path, self.request)

    def run_job(self):
        return job.run_job(self.request_path, self.output, resolution=128, camera_evaluations=20)

    def complete(self):
        with patch.object(job, 'refine', side_effect=_successful_stage) as stage:
            result = self.run_job()
        self.assertEqual(stage.call_count, 1)
        return result

    def test_terminal_reuse_verifies_and_does_not_rerun_or_accept(self):
        result = self.complete()
        self.assertEqual(result['status'], 'candidate_available')
        self.assertEqual(result['quality_verdict'], 'unmeasured')
        self.assertFalse(result['quality']['accepted'])
        self.assertEqual(result['candidate']['selection'], 'photo_guided_geometry_proposal')
        dimensions = next(gate for gate in result['quality']['gates'] if gate['id'] == 'physical_dimensions')
        self.assertEqual(dimensions['application'], 'frame_width_applied_as_uniform_scale_to_meters' if 'frame_width' in self.request['dimensions_mm'] else 'supplied_without_frame_width_unapplied')
        if 'frame_width' in self.request['dimensions_mm']:
            self.assertEqual(result['physical_scale']['status'], 'scaled')
            self.assertAlmostEqual(result['physical_scale']['receipt']['output_extent_meters_xyz'][0], self.request['dimensions_mm']['frame_width'] / 1000, places=6)
        self.assertEqual(result['ar_load_parameters']['width_mm'], self.request['dimensions_mm'].get('frame_width'))
        self.assertFalse(result['ar_load_parameters']['verified_against_model'])
        self.assertEqual(dimensions['status'], 'unmeasured')
        self.assertEqual(dimensions['supplied_mm'], self.request['dimensions_mm'])
        before = {path.relative_to(self.output): path.read_bytes() for path in self.output.rglob('*') if path.is_file()}
        with patch.object(job, 'refine', side_effect=AssertionError('Terminal reuse must not run refinement')):
            self.assertEqual(self.run_job(), result)
        after = {path.relative_to(self.output): path.read_bytes() for path in self.output.rglob('*') if path.is_file()}
        self.assertEqual(before, after)

    def test_region_stage_pins_engine_and_reuses_observations(self):
        engine = BoxEngine()
        def run():
            return job.run_job(self.request_path, self.output, resolution=128, camera_evaluations=20, region_engine=engine)
        with patch.object(job, 'refine', side_effect=_successful_stage):
            result = run()
        self.assertEqual(result['region_observations']['status'], 'region_hypotheses_available')
        self.assertFalse(result['quality']['accepted'])
        self.assertEqual(len(engine.calls), 2)
        before = {p.relative_to(self.output): (p.read_bytes(), p.stat().st_mtime_ns) for p in self.output.rglob('*') if p.is_file()}
        self.assertEqual(run(), result)
        after = {p.relative_to(self.output): (p.read_bytes(), p.stat().st_mtime_ns) for p in self.output.rglob('*') if p.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(len(engine.calls), 2)
        with patch.object(engine, 'describe', return_value={'engine': 'different_checkpoint'}):
            with self.assertRaisesRegex(ValueError, 'Settings or implementation'):
                run()
        appearance = next((self.output / 'stages/regions').rglob('*appearance.json'))
        appearance.write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, 'integrity'):
            run()

    def test_optical_attempts_resume_and_never_replace_candidate_with_diagnostic_material(self):
        engine = BoxEngine()
        def run():
            return job.run_job(self.request_path, self.output, resolution=128, camera_evaluations=20,
                               region_engine=engine, lens_candidates=True)
        def prepare(model, output):
            output.mkdir(parents=True)
            result = {'status': 'prepared_optical_candidate', 'source_sha256': _sha(model)}
            _write(output / 'report.json', result)
            return result
        def interrupted(*args, **kwargs):
            output = args[2]
            output.mkdir(parents=True)
            (output / 'partial-fit.json').write_text('retained incomplete fit evidence')
            raise RuntimeError('fit interruption')
        def fit(preparation, regions, output, **kwargs):
            output.mkdir(parents=True)
            (output / 'preview.glb').write_bytes(b'diagnostic preview placeholder for orchestration test')
            _write(output / 'export.json', {'scope': 'orchestration fixture'})
            result = {'status': 'diagnostic_previews_available', 'previews': [{'status': 'diagnostic_preview_exported',
                'family': 'uniform_tint', 'path': 'preview.glb', 'sha256': _sha(output / 'preview.glb'),
                'export': {'path': 'export.json', 'sha256': _sha(output / 'export.json')}}]}
            _write(output / 'report.json', result)
            return result
        with patch.object(job, 'refine', side_effect=_successful_stage), \
             patch('reconstruction.prepare_optics.run_optical_preparation', side_effect=prepare), \
             patch('reconstruction.photo_lens_stage.run_photo_lens_stage', side_effect=interrupted):
            with self.assertRaisesRegex(RuntimeError, 'fit interruption'):
                run()
        with patch('reconstruction.prepare_optics.run_optical_preparation', side_effect=AssertionError('Must reuse preparation')), \
             patch('reconstruction.photo_lens_stage.run_photo_lens_stage', side_effect=fit):
            result = run()
        self.assertEqual(_sha(self.output / 'candidate.glb'), _sha(self.output / 'stages' / 'scale' / 'attempt_1' / 'scaled.glb'), 'the retained candidate is the scaled initial model')
        optics = result['optical_candidates']
        self.assertEqual(optics['fit_status'], 'diagnostic_previews_available')
        self.assertIsNone(optics['selected_material'])
        self.assertFalse(optics['accepted'])
        preview = optics['previews'][0]
        self.assertTrue((self.output / preview['path']).is_file())
        self.assertTrue((self.output / preview['export']['path']).is_file())
        journal = _read(self.output / 'job.json')
        self.assertEqual(len(journal['stages']['optical_preparation']), 1)
        self.assertEqual(len(journal['stages']['photo_lens_fit']), 2)
        self.assertTrue((self.output / 'stages/photo_lens_fit/attempt_1/partial-fit.json').is_file())
        before = {p.relative_to(self.output): (p.read_bytes(), p.stat().st_mtime_ns) for p in self.output.rglob('*') if p.is_file()}
        self.assertEqual(run(), result)
        after = {p.relative_to(self.output): (p.read_bytes(), p.stat().st_mtime_ns) for p in self.output.rglob('*') if p.is_file()}
        self.assertEqual(before, after)

    def test_unsupported_preparation_is_reported_without_running_material_fit(self):
        def prepare(model, output):
            output.mkdir(parents=True)
            result = {'status': 'unsupported_optical_preparation', 'source_sha256': _sha(model)}
            _write(output / 'report.json', result)
            return result
        with patch.object(job, 'refine', side_effect=_successful_stage), \
             patch('reconstruction.prepare_optics.run_optical_preparation', side_effect=prepare), \
             patch('reconstruction.photo_lens_stage.run_photo_lens_stage', side_effect=AssertionError('Do not fit unsupported optics')):
            result = job.run_job(self.request_path, self.output, resolution=128, camera_evaluations=20,
                                 region_engine=BoxEngine(), lens_candidates=True)
        self.assertEqual(result['optical_candidates']['preparation_status'], 'unsupported_optical_preparation')
        self.assertEqual(result['optical_candidates']['fit_status'], 'not_prepared')
        self.assertFalse(result['quality']['accepted'])

    def test_lens_mode_dispatch_resolves_default_and_explicit_budgets(self):
        from reconstruction.photo_lens_fit import PhotoLensFitPolicy
        from reconstruction.joint_photo_lens_fit import JointPhotoLensFitPolicy
        for index, (mode, supplied, expected) in enumerate((
                (None, None, 540), ('independent', 77, 77), ('joint', None, 2430), ('joint', 77, 77))):
            with self.subTest(mode=mode, supplied=supplied):
                output = self.folder / f'mode-job-{index}'
                options = {'lens_candidates': True, 'region_engine': BoxEngine(), 'lens_maximum_samples': 32}
                if mode is not None:
                    options['lens_fit_mode'] = mode
                if supplied is not None:
                    options['lens_maximum_optimization_runs'] = supplied
                with patch.object(job, 'refine', side_effect=_successful_stage), \
                     patch('reconstruction.prepare_optics.run_optical_preparation', side_effect=_prepared_optics), \
                     patch('reconstruction.photo_lens_stage.run_photo_lens_stage', side_effect=_nested_diagnostic_fit) as independent, \
                     patch('reconstruction.joint_photo_lens_stage.run_joint_photo_lens_stage', side_effect=_nested_diagnostic_fit) as joint:
                    result = job.run_job(self.request_path, output, resolution=128, camera_evaluations=20, **options)
                active, inactive = (joint, independent) if mode == 'joint' else (independent, joint)
                self.assertEqual(active.call_count, 1); self.assertEqual(inactive.call_count, 0)
                policy = active.call_args.kwargs['policy']
                self.assertIsInstance(policy, JointPhotoLensFitPolicy if mode == 'joint' else PhotoLensFitPolicy)
                self.assertEqual(policy.maximum_optimization_runs, expected)
                self.assertEqual(active.call_args.kwargs['maximum_samples_per_hypothesis'], 32)
                settings = result['settings']['lens_candidates']; optics = result['optical_candidates']
                self.assertEqual(settings['mode'], mode or 'independent')
                self.assertEqual(settings['policy']['maximum_optimization_runs'], expected)
                self.assertEqual(optics['fit_mode'], settings['mode'])
                journal = _read(output / 'job.json'); stage = journal['stages']['photo_lens_fit'][0]
                self.assertEqual(stage['fit_mode'], settings['mode']); self.assertEqual(stage['fit_settings'], settings)
                self.assertEqual(stage['fit_settings_sha256'], job._settings_sha256(settings))
                self.assertEqual(optics['fit_settings_sha256'], stage['fit_settings_sha256'])
                preview = optics['previews'][0]
                for item in (preview, preview['export']):
                    self.assertTrue(item['path'].startswith('stages/photo_lens_fit/attempt_1/attempts/attempt-001/'))
                    self.assertEqual(item['sha256'], _sha(output/item['path']))
                self.assertEqual(_sha(output/'candidate.glb'), result['physical_scale']['model']['sha256'], 'the retained candidate is the scaled initial model')
                self.assertNotEqual(result['physical_scale']['model']['sha256'], _sha(self.model))
                self.assertFalse(result['quality']['accepted']); self.assertIsNone(optics['selected_material'])

    def test_joint_completed_stage_reuse_pins_mode_budget_and_stage_metadata(self):
        options = {'resolution': 128, 'camera_evaluations': 20, 'region_engine': BoxEngine(),
                   'lens_candidates': True, 'lens_fit_mode': 'joint'}
        with patch.object(job, 'refine', side_effect=_successful_stage), \
             patch('reconstruction.prepare_optics.run_optical_preparation', side_effect=_prepared_optics), \
             patch('reconstruction.joint_photo_lens_stage.run_joint_photo_lens_stage', side_effect=_nested_diagnostic_fit):
            result = job.run_job(self.request_path, self.output, **options)
        with patch('reconstruction.joint_photo_lens_stage.run_joint_photo_lens_stage', side_effect=AssertionError('Must reuse completed fit')):
            self.assertEqual(job.run_job(self.request_path, self.output, **options), result)
            self.assertEqual(job.run_job(self.request_path, self.output, **options, lens_maximum_optimization_runs=2430), result)
        for changes in ({'lens_fit_mode': 'independent'}, {'lens_maximum_optimization_runs': 2431}):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, 'Settings or implementation changed'):
                job.run_job(self.request_path, self.output, **{**options, **changes})
        path = self.output/'job.json'; original = path.read_bytes()
        for field, changed in (('fit_mode', 'independent'), ('fit_settings_sha256', 'a'*64), ('fit_settings', {})):
            journal = json.loads(original); journal['stages']['photo_lens_fit'][0][field] = changed
            _write(path, journal)
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'Completed lens fit mode/settings'):
                job.run_job(self.request_path, self.output, **options)
            path.write_bytes(original)

    def test_joint_interruption_starts_new_attempt_without_claiming_checkpoint_reuse(self):
        options = {'resolution': 128, 'camera_evaluations': 20, 'region_engine': BoxEngine(),
                   'lens_candidates': True, 'lens_fit_mode': 'joint'}
        def interrupt(preparation, regions, output, **kwargs):
            (output/'checkpoints').mkdir(parents=True)
            (output/'checkpoints'/'completed-start.json').write_text('retained partial evidence')
            raise RuntimeError('joint interruption')
        with patch.object(job, 'refine', side_effect=_successful_stage), \
             patch('reconstruction.prepare_optics.run_optical_preparation', side_effect=_prepared_optics), \
             patch('reconstruction.joint_photo_lens_stage.run_joint_photo_lens_stage', side_effect=interrupt):
            with self.assertRaisesRegex(RuntimeError, 'joint interruption'):
                job.run_job(self.request_path, self.output, **options)
        with patch('reconstruction.prepare_optics.run_optical_preparation', side_effect=AssertionError('Must reuse preparation')), \
             patch('reconstruction.joint_photo_lens_stage.run_joint_photo_lens_stage', side_effect=_nested_diagnostic_fit) as fitted:
            result = job.run_job(self.request_path, self.output, **options)
        journal = _read(self.output/'job.json'); attempts = journal['stages']['photo_lens_fit']
        self.assertEqual(len(attempts), 2); self.assertEqual(attempts[1]['status'], 'complete')
        self.assertEqual(fitted.call_args.args[2], self.output/'stages/photo_lens_fit/attempt_2')
        self.assertNotIn('resume', fitted.call_args.kwargs)
        self.assertIn('without cross-attempt optimizer checkpoint reuse', attempts[1]['recovery'])
        self.assertEqual((self.output/'stages/photo_lens_fit/attempt_1/checkpoints/completed-start.json').read_text(), 'retained partial evidence')
        self.assertTrue(result['optical_candidates']['previews'][0]['path'].startswith('stages/photo_lens_fit/attempt_2/'))

    def test_joint_complete_fit_survives_later_job_finalization_failure(self):
        options = {'resolution': 128, 'camera_evaluations': 20, 'region_engine': BoxEngine(),
                   'lens_candidates': True, 'lens_fit_mode': 'joint'}
        with patch.object(job, 'refine', side_effect=_successful_stage), \
             patch('reconstruction.prepare_optics.run_optical_preparation', side_effect=_prepared_optics), \
             patch('reconstruction.joint_photo_lens_stage.run_joint_photo_lens_stage', side_effect=_nested_diagnostic_fit), \
             patch.object(job, '_report', side_effect=RuntimeError('finalization interruption')):
            with self.assertRaisesRegex(RuntimeError, 'finalization interruption'):
                job.run_job(self.request_path, self.output, **options)
        before = _read(self.output/'job.json')
        self.assertFalse(before['terminal'])
        self.assertEqual(before['stages']['photo_lens_fit'][0]['status'], 'complete')
        with patch.object(job, 'refine', side_effect=AssertionError('Must reuse refinement')), \
             patch('reconstruction.prepare_optics.run_optical_preparation', side_effect=AssertionError('Must reuse preparation')), \
             patch('reconstruction.joint_photo_lens_stage.run_joint_photo_lens_stage', side_effect=AssertionError('Must reuse completed fit')):
            result = job.run_job(self.request_path, self.output, **options)
        after = _read(self.output/'job.json')
        self.assertEqual(after['stages']['photo_lens_fit'], before['stages']['photo_lens_fit'])
        self.assertTrue(after['terminal'])
        self.assertEqual(result['optical_candidates']['fit_mode'], 'joint')

    def test_invalid_lens_modes_and_budgets_reject_before_job_mutation(self):
        for options in ({'lens_fit_mode': 'separate'}, {'lens_fit_mode': None},
                        {'lens_fit_mode': 'joint', 'lens_maximum_optimization_runs': 0},
                        {'lens_fit_mode': 'joint', 'lens_maximum_optimization_runs': True},
                        {'lens_fit_mode': 'independent', 'lens_maximum_optimization_runs': 0}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                job.run_job(self.request_path, self.output, lens_candidates=True, region_engine=BoxEngine(), **options)
            self.assertFalse(self.output.exists())

    def test_lens_mode_cli_forwards_default_and_explicit_options(self):
        base = ['--request', str(self.request_path), '--output', str(self.output), '--lens-candidates',
                '--region-weights', str(self.folder/'local-checkpoint.pt')]
        result = {'status': 'candidate_available', 'quality_verdict': 'unmeasured', 'candidate': None}
        for arguments, mode, budget in (([], 'independent', None), (['--lens-fit-mode', 'joint'], 'joint', None),
                                       (['--lens-fit-mode', 'joint', '--lens-maximum-optimization-runs', '3000'], 'joint', 3000)):
            with self.subTest(arguments=arguments), redirect_stdout(io.StringIO()), \
                 patch('reconstruction.region_engine.OfflineSAM2RegionEngine', return_value=BoxEngine()), \
                 patch.object(job, 'run_job', return_value=result) as run:
                job.main(base+arguments)
            self.assertEqual(run.call_args.kwargs['lens_fit_mode'], mode)
            self.assertEqual(run.call_args.kwargs['lens_maximum_optimization_runs'], budget)
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            job.main(base+['--lens-fit-mode', 'invalid'])
        self.assertEqual(error.exception.code, 2)

    def test_interrupted_regions_preserve_prior_attempt_and_refinement(self):
        actual = job.run_region_stage
        def interrupted(photos, output, **kwargs):
            output.mkdir(parents=True)
            (output / 'partial-mask.png').write_bytes(b'partial evidence')
            raise RuntimeError('region interruption')
        with patch.object(job, 'refine', side_effect=_successful_stage), patch.object(job, 'run_region_stage', side_effect=interrupted):
            with self.assertRaisesRegex(RuntimeError, 'region interruption'):
                self.run_job()
        with patch.object(job, 'refine', side_effect=AssertionError('Must reuse refinement')), patch.object(job, 'run_region_stage', side_effect=actual):
            result = self.run_job()
        journal = _read(self.output / 'job.json')
        self.assertEqual(len(journal['stages']['refinement']), 1)
        self.assertEqual(len(journal['stages']['regions']), 2)
        self.assertEqual((self.output / 'stages/regions/attempt_1/partial-mask.png').read_bytes(), b'partial evidence')
        self.assertFalse(result['quality']['accepted'])

    def test_terminal_changed_original_photo_is_rejected(self):
        self.complete()
        self.photos[0][1].write_bytes(b'changed original photograph')
        with self.assertRaisesRegex(ValueError, 'photo changed|[Ss]ource.*changed|[Oo]riginal.*changed'):
            self.run_job()

    def test_terminal_changed_original_model_is_rejected(self):
        self.complete()
        self.model.write_bytes(self.model.read_bytes() + b'changed')
        with self.assertRaisesRegex(ValueError, '[Mm]odel.*changed|[Ss]ource.*changed|[Ii]nitial.*changed|[Oo]riginal.*changed'):
            self.run_job()

    def test_candidate_report_stage_tamper_and_unexpected_file_are_rejected(self):
        self.complete()
        stage = self.output / 'stages' / 'refinement' / 'attempt_1'
        for path in (self.output / 'candidate.glb', self.output / 'report.json', stage / 'evidence.json'):
            with self.subTest(path=path.name):
                original = path.read_bytes()
                path.write_bytes(b'tampered')
                with self.assertRaisesRegex(ValueError, '[Ii]ntegrity|contents differ'):
                    self.run_job()
                self.assertEqual(path.read_bytes(), b'tampered')
                path.write_bytes(original)
        extra = stage / 'unrecorded.json'
        extra.write_bytes(b'{}')
        with self.assertRaisesRegex(ValueError, 'contents differ'):
            self.run_job()
        self.assertEqual(extra.read_bytes(), b'{}')

    def test_interrupted_refinement_preserves_attempt_then_starts_new_one(self):
        def interrupted(model, photos, output, **settings):
            output.mkdir(parents=True)
            (output / 'partial.glb').write_bytes(b'incomplete historical artifact')
            raise RuntimeError('deliberate interruption')
        with patch.object(job, 'refine', side_effect=interrupted):
            with self.assertRaisesRegex(RuntimeError, 'deliberate interruption'):
                self.run_job()
        before = _read(self.output / 'job.json')
        pins = {name: before['stages'][name][0]['artifacts'] for name in ('input', 'initializer')}
        self.assertFalse(before['terminal'])
        with patch.object(job, 'refine', side_effect=_successful_stage) as stage:
            result = self.run_job()
        after = _read(self.output / 'job.json')
        self.assertEqual(stage.call_count, 1)
        self.assertEqual(len(after['stages']['refinement']), 2)
        self.assertEqual((self.output / 'stages/refinement/attempt_1/partial.glb').read_bytes(), b'incomplete historical artifact')
        self.assertEqual(after['stages']['refinement'][1]['directory'], 'stages/refinement/attempt_2')
        for name in pins:
            self.assertEqual(after['stages'][name][0]['artifacts'], pins[name])
        self.assertEqual(result['quality_verdict'], 'unmeasured')

    def test_rejected_refinement_never_selects_lingering_proposal(self):
        def rejected(model, photos, output, **settings):
            output.mkdir(parents=True)
            (output / 'proposal.glb').write_bytes(b'lingering invalid result must not become candidate')
            report = {'status': 'proposal_rejected_after_rerender', 'quality_verdict': 'unmeasured',
                      'rerender_nonregression': False, 'rejected_artifact': 'proposal.glb'}
            _write(output / 'report.json', report)
            return report
        with patch.object(job, 'refine', side_effect=rejected):
            result = self.run_job()
        self.assertEqual((self.output / 'candidate.glb').read_bytes(), (self.output / 'stages' / 'scale' / 'attempt_1' / 'scaled.glb').read_bytes())
        self.assertEqual(result['candidate']['selection'], 'initializer_retained_no_supported_refinement')
        self.assertFalse(result['quality']['accepted'])

    def test_unknown_and_repeated_priors_reach_stage_without_relabeling(self):
        for entry in self.request['photos']:
            entry.pop('view')
        self.save_request()
        with patch.object(job, 'refine', side_effect=_successful_stage) as stage:
            result = self.run_job()
        received = stage.call_args.args[1]
        self.assertEqual([photo.view for photo in received], ['unknown', 'unknown'])
        self.assertEqual([photo.id for photo in received], ['photo-0', 'photo-1'])
        self.assertEqual([row['view_prior'] for row in result['photos']], ['unknown', 'unknown'])

    def test_changed_request_and_settings_reject_before_overwriting(self):
        self.complete()
        prior = (self.output / 'report.json').read_bytes()
        with self.assertRaisesRegex(ValueError, 'Settings or implementation changed'):
            job.run_job(self.request_path, self.output, resolution=160, camera_evaluations=20)
        self.request['photos'][0]['view'] = 'back'
        self.save_request()
        with self.assertRaisesRegex(ValueError, 'Request changed'):
            self.run_job()
        self.assertEqual((self.output / 'report.json').read_bytes(), prior)

    def test_stage_path_cannot_escape_job_even_if_journal_is_modified(self):
        self.complete()
        journal_path = self.output / 'job.json'
        journal = _read(journal_path)
        outside = self.folder / 'outside.json'
        outside.write_bytes(b'outside untouched')
        journal['stages']['refinement'][0]['artifacts'] = {'../outside.json': _sha(outside)}
        _write(journal_path, journal)
        with self.assertRaisesRegex(ValueError, 'escapes the job'):
            self.run_job()
        self.assertEqual(outside.read_bytes(), b'outside untouched')

    def test_active_os_lock_prevents_simultaneous_execution(self):
        with job._job_lock(self.output):
            with self.assertRaisesRegex(RuntimeError, 'already running'):
                self.run_job()
        self.assertFalse((self.output / 'job.json').exists())

    def test_implementation_change_during_stage_rejects_finalization(self):
        with patch.object(job, 'implementation_manifest', side_effect=[{'source': 'first'}, {'source': 'changed'}]):
            with patch.object(job, 'refine', side_effect=_successful_stage):
                with self.assertRaisesRegex(ValueError, 'Implementation changed during'):
                    self.run_job()
        self.assertFalse(_read(self.output / 'job.json')['terminal'])
        self.assertFalse((self.output / 'candidate.glb').exists())

    def test_actual_local_pipeline_preserves_input_and_never_claims_quality(self):
        original = self.model.read_bytes()
        result = self.run_job()
        self.assertEqual(self.model.read_bytes(), original)
        self.assertEqual(result['status'], 'candidate_available')
        self.assertEqual(result['quality_verdict'], 'unmeasured')
        self.assertFalse(result['quality']['accepted'])
        self.assertEqual(result['candidate']['sha256'], _sha(self.output / 'candidate.glb'))
        self.assertTrue(_read(self.output / 'job.json')['terminal'])

    def test_photo_only_pending_job_resumes_one_provider_task_without_resubmit(self):
        self.request.pop('initializer')
        self.save_request()
        prepared = self.run_job()
        self.assertEqual(prepared['status'], 'awaiting_initializer')
        self.assertEqual(prepared['initializer']['status'], 'awaiting_backend')
        self.assertEqual(len(prepared['photos']), 2)
        raw = self.model.read_bytes()

        class Backend:
            provider = 'meshy'
            submissions = 0
            polls = 0

            def submit(self, request):
                self.submissions += 1
                return 'one-task'

            def retrieve(self, task_id):
                self.polls += 1
                return {'task_id': task_id, 'status': 'pending' if self.polls == 1 else 'succeeded'}

            def download(self, task):
                return raw

        backend = Backend()
        pending = job.run_job(self.request_path, self.output, resolution=128, camera_evaluations=20,
                              backend=backend, allow_submit=True)
        self.assertEqual(pending['initializer']['status'], 'pending')
        with patch.object(job, 'refine', side_effect=_successful_stage):
            result = job.run_job(self.request_path, self.output, resolution=128, camera_evaluations=20,
                                 backend=backend, allow_submit=True)
        self.assertEqual(result['status'], 'candidate_available')
        self.assertEqual(backend.submissions, 1)
        self.assertEqual(backend.polls, 2)
        self.assertEqual(self.run_job(), result)
        self.assertEqual(len(_read(self.output/'job.json')['stages']['initializer']), 1)

    def test_invalid_initial_artifact_reports_failure_without_claiming_waiting(self):
        self.model.write_bytes(b'this is not a GLB')
        self.request['initializer']['sha256'] = _sha(self.model)
        self.save_request()
        result = self.run_job()
        self.assertEqual(result['status'], 'initializer_failed')
        self.assertEqual(result['initializer']['status'], 'artifact_invalid')
        self.assertIsNone(result['candidate'])
        self.assertFalse(result['quality']['accepted'])


if __name__ == '__main__':
    unittest.main()
