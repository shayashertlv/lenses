"""Source-bound semantic job integration with offline renderer/client doubles."""
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from PIL import Image

from reconstruction import job
import test_group_job as group_fixture
from test_photo_semantics import response


class OfflineSemanticClient:
    def __init__(self):
        self.calls = []
        self.preferred_card = None

    def describe(self):
        return {'kind': 'offline_semantic_job_fixture', 'calls_are_mocked': True}

    def infer(self, manifest, prompt, schema):
        self.calls.append(manifest)
        if 'winner' not in schema.get('properties', {}):
            return {'source_images': manifest, 'response': response()}
        cards = [r for r in manifest if r.get('prompt_label', '').startswith('candidate-')]
        winner = next(r['prompt_label'].removeprefix('candidate-') for r in cards
                      if Path(r['local_path']) == self.preferred_card)
        return {'source_images': manifest, 'response': {'winner': winner,
                'assessments': [{'candidate': r['prompt_label'].removeprefix('candidate-'),
                                'match': 'plausible', 'defects': [], 'evidence': ['Controlled fixture preference']}
                               for r in cards], 'limitations': ['Offline test double, no actual vision assessment']}}


class SemanticJobTests(unittest.TestCase):
    setUp = group_fixture.InferredGroupJobTests.setUp
    save_request = group_fixture.InferredGroupJobTests.save_request
    cameras = group_fixture.InferredGroupJobTests.cameras
    run_inferred = group_fixture.InferredGroupJobTests.run_inferred

    def reserve_view(self):
        from PIL import ImageDraw
        path = self.folder/'reserved.png'
        image = Image.new('RGB',(300,200),'white')
        ImageDraw.Draw(image).rectangle((44,75,260,133),fill=(25,35,45))
        image.save(path)
        self.request['evaluation_photos'] = [{'id':'reserved','path':str(path),'view':'angled'}]
        self.save_request()
        return path

    def renderer(self, manifest_path, output):
        manifest = json.loads(Path(manifest_path).read_bytes())
        output = Path(output)
        output.mkdir(parents=True)
        cases = []
        delivery = any(r['id']=='compact-selected' for r in manifest['cases'])
        for index, row in enumerate(manifest['cases']):
            path = output/(row['id']+'.png')
            if delivery:
                from reconstruction.compact_glb import active_semantics_sha256
                from reconstruction.surface_transfer import _chunks
                semantic_hash=active_semantics_sha256(*_chunks(Path(row['path']).read_bytes()))
                color=tuple(bytes.fromhex(semantic_hash[:6]))
            else:
                color=(30+index*12, 90, 130)
            Image.new('RGB', (32, 32), color).save(path)
            cases.append({'id': row['id'], 'model_sha256': row['model_sha256'],
                          'renders':[{'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}],
                          'card': {'path': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}})
        if not delivery:
            self.client.preferred_card = output/(manifest['cases'][-1]['id']+'.png')
            self.rendered_ids = [r['id'] for r in manifest['cases']]
        report = {'status': 'passed', 'source_snapshot_stable':True, 'cases': cases,
                  'manifest_sha256': hashlib.sha256(Path(manifest_path).read_bytes()).hexdigest()}
        (output/'report.json').write_text(json.dumps(report), encoding='utf-8')
        return report

    def test_semantic_job_pins_priors_exports_actual_choice_and_resumes_without_calls(self):
        self.client = OfflineSemanticClient()
        result = self.run_inferred(appearance_mode='semantic_ar_v1', semantic_client=self.client,
                                   appearance_renderer=self.renderer)
        self.assertEqual(len(self.client.calls), 3)  # interpretation plus two order-reversed reviews
        optics = result['optical_candidates']
        chosen = optics['appearance_candidate']
        self.assertEqual(chosen['candidate_id'], self.rendered_ids[-1])
        self.assertEqual(chosen['selection']['method'], 'semantic_ar_v1')
        self.assertIsNone(chosen['selection']['photo_policy_pass'])
        self.assertFalse(chosen['accepted'])
        self.assertEqual(optics['appearance_selection']['method'], 'semantic_ar_v1')
        self.assertEqual(optics['photo_appearance_baseline']['method'], 'least_complex_family_within_tolerance_v3')
        self.assertEqual(hashlib.sha256((self.output/job.APPEARANCE_CANDIDATE).read_bytes()).hexdigest(), chosen['sha256'])
        fit_root = (self.output/optics['fit_report']).parent
        fit_request = json.loads((fit_root/'request.json').read_bytes())
        prior_path = Path(fit_request['appearance_prior_report'])
        self.assertIn(str(prior_path), fit_request['input_sha256'])
        self.assertEqual(fit_request['policy']['photo_policy']['lighting_families'], ['constant', 'semantic_softbox'])
        journal = json.loads((self.output/'job.json').read_bytes())
        for name in ('multiview_intake','appearance_evidence', 'photo_lens_fit', 'semantic_appearance','delivery'):
            self.assertEqual(journal['stages'][name][0]['status'], 'complete')
        before = {p.relative_to(self.output): (p.read_bytes(), p.stat().st_mtime_ns)
                  for p in self.output.rglob('*') if p.is_file()}
        with patch.object(self.client, 'infer', side_effect=AssertionError('resume must not call vision')):
            repeated = self.run_inferred(appearance_mode='semantic_ar_v1', semantic_client=self.client,
                                        appearance_renderer=self.renderer)
        self.assertEqual(repeated, result)
        after = {p.relative_to(self.output): (p.read_bytes(), p.stat().st_mtime_ns)
                 for p in self.output.rglob('*') if p.is_file()}
        self.assertEqual(before, after)

    def test_semantic_options_require_explicit_client_and_finite_difference_before_writes(self):
        client = OfflineSemanticClient()
        changes = [dict(appearance_mode='semantic_ar_v1'),
                   dict(appearance_mode='semantic_ar_v1', semantic_client=client, lens_jacobian_mode='analytic'),
                   dict(appearance_mode='photo', semantic_client=client)]
        for options in changes:
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.run_inferred(**options)
            self.assertFalse(self.output.exists())

    def test_optional_lod_pixel_regression_retains_verified_original_and_finishes_job(self):
        from reconstruction.compact_glb import run_compact_asset
        self.client=OfflineSemanticClient()
        def force_lod(*args,**kwargs):
            result=run_compact_asset(*args,**kwargs)
            if Path(args[1]).name=='compact':result['triangles']=200_001
            return result
        def renderer(manifest,output):
            result=self.renderer(manifest,output)
            for row in result['cases']:
                if row['id']=='compact-lod':row['renders'][0]['sha256']='0'*64
            (Path(output)/'report.json').write_text(json.dumps(result),encoding='utf-8')
            return result
        with patch('reconstruction.compact_glb.run_compact_asset',side_effect=force_lod):
            result=self.run_inferred(appearance_mode='semantic_ar_v1',semantic_client=self.client,
                                     appearance_renderer=renderer)
        delivery=json.loads((self.output/result['optical_candidates']['delivery']['report']).read_bytes())
        self.assertFalse(delivery['lod_comparison']['retained'])
        self.assertFalse(delivery['lod_comparison']['packaged_render_parity'])
        self.assertEqual(delivery['compact_report'],'compact/report.json')
        self.assertEqual(result['candidate']['appearance']['sha256'],delivery['model']['sha256'])

    def test_reserved_view_is_absent_from_all_model_inputs_and_imported_history_stays_unknown(self):
        photo = self.reserve_view()
        reserved_sha = hashlib.sha256(photo.read_bytes()).hexdigest()
        self.client = OfflineSemanticClient()
        result = self.run_inferred(appearance_mode='semantic_ar_v1', semantic_client=self.client,
                                   appearance_renderer=self.renderer)
        for manifest in self.client.calls:
            self.assertNotIn(reserved_sha, [row['sha256'] for row in manifest])
        journal = json.loads((self.output/'job.json').read_bytes())
        self.assertIn('evaluation_reservation', journal['stages'])
        for stage in ('initializer','geometry','material','semantics','selection'):
            self.assertIn('photo_usage_'+stage, journal['stages'])
        delivery_folder = (self.output/result['optical_candidates']['delivery']['report']).parent
        measured = json.loads((delivery_folder/'reserved-evaluation/report.json').read_bytes())
        self.assertEqual(measured['method'],'source_bound_heldout_geometry_v2')
        self.assertEqual(measured['views'],1)
        self.assertFalse(measured['independent'])
        self.assertIn('initializer_history_unverified', [r['reason'] for r in measured['evaluation_usage']['issues']])
        self.assertEqual(set(measured['evaluation_usage']['expected_stages']),
                         {'initializer','geometry','material','semantics','selection','frame'})
        with patch.object(self.client,'infer',side_effect=AssertionError('must resume without calls')):
            self.assertEqual(result, self.run_inferred(appearance_mode='semantic_ar_v1',
                semantic_client=self.client, appearance_renderer=self.renderer))

    def test_cached_semantics_cannot_leak_reserved_view_before_initializer(self):
        photo = self.reserve_view()
        report = self.folder/'leaked-semantics.json'
        report.write_text(json.dumps({'source_images':[{'label':'image-1','local_path':str(photo),
            'sha256':hashlib.sha256(photo.read_bytes()).hexdigest()}]}),encoding='utf-8')
        self.client = OfflineSemanticClient()
        with patch.object(job,'resolve_initial_model',side_effect=AssertionError('provider must not run')) as initial:
            with self.assertRaisesRegex(ValueError,'Reserved evaluation photo leaked'):
                self.run_inferred(appearance_mode='semantic_ar_v1',semantic_client=self.client,
                    appearance_renderer=self.renderer,semantic_report=report)
        initial.assert_not_called()
        self.assertEqual(self.client.calls,[])

    def test_interrupted_cached_semantics_owns_all_inputs_after_originals_disappear(self):
        from reconstruction.photo_semantics import build_image_manifest, validate_product_hypotheses
        self.reserve_view()
        external = []
        for index, source in enumerate(self.photo_paths):
            path = self.folder/f'external-{index}.png'
            path.write_bytes(source.read_bytes())
            external.append(path)
        extra = self.folder/'external-side.png'
        Image.new('RGB', (64,64), (65,75,85)).save(extra)
        external.append(extra)  # Not rebound to either fit photo; still needed by grounding.
        manifest = build_image_manifest([{'id':f'cached-{i}','path':str(p)} for i,p in enumerate(external)])
        report = self.folder/'cached-semantics.json'
        report.write_text(json.dumps(validate_product_hypotheses(response(),manifest)),encoding='utf-8')
        self.client = OfflineSemanticClient()
        options = dict(appearance_mode='semantic_ar_v1',semantic_client=self.client,
                       appearance_renderer=self.renderer,semantic_report=report)
        with patch.object(job,'prepare_input_bundle',side_effect=RuntimeError('controlled interruption')):
            with self.assertRaisesRegex(RuntimeError,'controlled interruption'):
                self.run_inferred(**options)
        journal = json.loads((self.output/'job.json').read_bytes())
        self.assertEqual(journal['stages']['cached_semantic_inputs'][0]['status'],'complete')
        for path in external:
            path.unlink()
        usage_path = self.output/'stages/photo_usage_cached-semantics/attempt_1/usage.json'
        owned = Path(json.loads(usage_path.read_bytes())['photos'][-1]['snapshot_path'])
        original = owned.read_bytes()
        owned.write_bytes(original+b'tampered')
        with self.assertRaisesRegex(ValueError,'Artifact integrity mismatch'):
            self.run_inferred(**options)
        owned.write_bytes(original)
        result = self.run_inferred(**options)
        self.assertEqual(len(self.client.calls),2)  # Cached interpretation, only reversed candidate reviews.
        semantic = json.loads((self.output/'stages/appearance_evidence/attempt_1/semantics.json').read_bytes())
        self.assertEqual(len(semantic['image_manifest']),3)
        self.assertEqual(Path(semantic['image_manifest'][-1]['local_path']),owned)
        delivery = (self.output/result['optical_candidates']['delivery']['report']).parent
        measured = json.loads((delivery/'reserved-evaluation/report.json').read_bytes())
        self.assertFalse(measured['independent'])
        self.assertIn('cached-semantics',measured['evaluation_usage']['expected_stages'])

    def test_reencoded_reserved_training_photo_is_rejected_before_initializer(self):
        source = Path(self.request['photos'][0]['path'])
        if not source.is_absolute(): source = self.request_path.parent/source
        photo = self.folder/'duplicate.bmp'
        Image.open(source).save(photo)
        self.request['evaluation_photos']=[{'id':'reserved','path':str(photo),'view':'front'}]
        self.save_request()
        self.client=OfflineSemanticClient()
        with patch.object(job,'resolve_initial_model',side_effect=AssertionError('provider must not run')) as initial:
            with self.assertRaisesRegex(ValueError,'Duplicate decoded'):
                self.run_inferred(appearance_mode='semantic_ar_v1',semantic_client=self.client,
                    appearance_renderer=self.renderer)
        initial.assert_not_called()


if __name__ == '__main__':
    unittest.main()
