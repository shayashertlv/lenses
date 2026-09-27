import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
import requests

from reconstruction.segmented_providers import SubmissionBudget, advance_mask, advance_tripo, pin, tripo_request
from reconstruction.segmented_job import Journal, validate_request, view_evidence, deliver_candidate, _provider_wait_status


class Client:
    def __init__(self, result=None, error=None):
        self.calls = []
        self.result = result
        self.error = error

    def api(self, method, url, provider, **kwargs):
        self.calls.append((method, url))
        if self.error:
            raise self.error
        return self.result


class ProviderRecoveryTests(unittest.TestCase):
    def test_uncertain_segment_never_reposts(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            request = tripo_request('test', 'segment', input_task='retained-task')
            first = Client(error=requests.Timeout('uncertain'))
            result = advance_tripo(path, request, client=first, budget=SubmissionBudget(1))
            self.assertEqual(result['status'], 'submission_uncertain')
            later = Client(result=(200, {}))
            budget = SubmissionBudget(8)
            result = advance_tripo(path, request, client=later, budget=budget)
            self.assertEqual(result['status'], 'submission_uncertain')
            self.assertEqual(later.calls, [])
            self.assertEqual(budget.used, 0)

    def test_zero_allowance_performs_no_paid_operation(self):
        with tempfile.TemporaryDirectory() as folder:
            client = Client()
            result = advance_tripo(folder, tripo_request('test', 'segment', input_task='task'),
                                   client=client, budget=SubmissionBudget(0))
            self.assertEqual(result['status'], 'awaiting_submission_allowance')
            self.assertFalse(client.calls)
            self.assertFalse((Path(folder) / 'submission-reserved.json').exists())

    def test_oblique_photo_cannot_be_reinterpreted_as_axial_view(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder) / 'photo.png'; Image.new('RGB', (32, 32), 'white').save(p)
            photos = [dict(id=v, view=v, provider_input=True, **pin(p)) for v in ('front', 'angled')]
            with self.assertRaisesRegex(ValueError, 'oblique'):
                tripo_request('test', 'generation', photos)

    def test_sam_uncertain_post_is_durable(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder) / 'render.png'; Image.new('RGB', (32, 32), 'white').save(p)
            client = Client(error=requests.Timeout('uncertain'))
            result = advance_mask(Path(folder) / 'mask', pin(p), client=client, budget=SubmissionBudget(1))
            self.assertEqual(result['status'], 'submission_uncertain')
            replay = Client()
            result = advance_mask(Path(folder) / 'mask', pin(p), client=replay, budget=SubmissionBudget(8))
            self.assertEqual(result['status'], 'submission_uncertain')
            self.assertFalse(replay.calls)

    def test_mask_artifacts_without_provider_lineage_do_not_claim_completion(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder) / 'render.png'; Image.new('RGB', (32, 32), 'white').save(p)
            target = Path(folder) / 'mask'
            advance_mask(target, pin(p))
            m = target / 'mask-00.png'; Image.new('L', (32, 32), 255).save(m)
            record = dict(masks=[dict(id='mask-00', **pin(m))])
            (target / 'artifacts.json').write_text(json.dumps(record))
            with self.assertRaises((ValueError, FileNotFoundError)):
                advance_mask(target, pin(p))

    def test_changed_request_cannot_reuse_paid_receipt(self):
        with tempfile.TemporaryDirectory() as folder:
            first = tripo_request('test', 'segment', input_task='first-task')
            advance_tripo(folder, first)
            with self.assertRaisesRegex(ValueError, 'Immutable'):
                advance_tripo(folder, tripo_request('test', 'segment', input_task='different-task'))


class JournalTests(unittest.TestCase):
    def test_invalid_material_capacity_is_rejected_before_provider_submission(self):
        request={'schema_version':1,'pipeline':'segmented_ar_v1','product_id':'test',
            'photos':[{'id':'front'},{'id':'left'}],'source':{'kind':'tripo'},
            'settings':{'maximum_appearance_candidates':1}}
        with self.assertRaisesRegex(ValueError,'before submitting providers'):
            validate_request(request)

    def test_completed_stage_is_verified_and_not_run_twice(self):
        with tempfile.TemporaryDirectory() as folder:
            calls = []
            def stage(path):
                calls.append(1); (path / 'data.bin').write_bytes(b'captured'); return {'ok': True}
            journal = Journal(folder, {'test': 1}, {'setting': 2})
            self.assertEqual(journal.stage('example', stage), {'ok': True})
            replay = Journal(folder, {'test': 1}, {'setting': 2})
            self.assertEqual(replay.stage('example', stage), {'ok': True})
            self.assertEqual(len(calls), 1)
            artifact = Path(folder) / replay.value['stages']['example']['directory'] / 'data.bin'
            artifact.write_bytes(b'altered')
            with self.assertRaisesRegex(ValueError, 'integrity'):
                replay.stage('example', stage)

    def test_failed_local_attempt_preserved_and_next_attempt_is_separate(self):
        with tempfile.TemporaryDirectory() as folder:
            journal = Journal(folder, {'product_id': 'example'}, {})
            def fail(path):
                (path / 'partial').write_text('keep'); raise RuntimeError('failed')
            with self.assertRaises(RuntimeError):
                journal.stage('example', fail)
            failure = json.loads((Path(folder) / 'report.json').read_bytes())
            self.assertEqual(failure['status'], 'failed')
            self.assertEqual(failure['failed_stage'], 'example')
            first = Path(folder) / journal.value['stages']['example']['directory']
            journal.stage('example', lambda path: {'done': True})
            self.assertEqual((first / 'partial').read_text(), 'keep')
            self.assertEqual(journal.value['stages']['example']['attempt'], 2)

    def test_changed_settings_cannot_use_previous_stages(self):
        with tempfile.TemporaryDirectory() as folder:
            Journal(folder, {}, {'quality': 1})
            with self.assertRaisesRegex(ValueError, 'Changed request'):
                Journal(folder, {}, {'quality': 2})

    def test_changed_upstream_recipe_rebuilds_only_dependent_stage(self):
        with tempfile.TemporaryDirectory() as folder:
            journal = Journal(folder, {}, {})
            journal.stage('dependent', lambda path: {'source': 'old'}, recipe={'source': 'old'})
            first = journal.value['stages']['dependent']['directory']
            value = journal.stage('dependent', lambda path: {'source': 'new'}, recipe={'source': 'new'})
            self.assertEqual(value['source'], 'new')
            self.assertEqual(journal.value['stages']['dependent']['attempt'], 2)
            self.assertTrue((Path(folder) / first / 'stage-result.json').exists())
            self.assertEqual(journal.value['stage_history']['dependent'][0]['result']['source'], 'old')

    def test_known_camera_affine_matches_renderer_order(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder) / 'front.png'; Image.new('RGB', (640, 480)).save(p)
            case = {'model_sha256': 'model', 'normalization': {'rotation_order': 'XYZ', 'rotation_degrees': [0, -90, 0],
                    'translation': [1, 2, 3], 'uniform_scale': 2}, 'orthographic_vertical_span': 2.05,
                    'renders': [{'mode': 'raw', 'background': 'light', 'view': 'front', 'filename': p.name, 'sha256': pin(p)['sha256']}]}
            rows = view_evidence({'case': case, 'directory': folder}, {'front': {'masks': [], 'mask_status': 'no_detection'}},
                                 {'yaw_degrees': -90, 'render_width': 640, 'render_height': 480, 'render_views': ['front']})
            self.assertEqual([r[-1] for r in rows[0]['world_to_render'][:3]], [2, 4, 6])
            self.assertAlmostEqual(rows[0]['world_to_render'][0][2], -2)
            self.assertEqual(rows[0]['camera']['center_x'], 319.5)

    def test_terminal_provider_result_does_not_poll_forever(self):
        for status in ('provider_failed', 'rejected', 'submission_uncertain'):
            self.assertEqual(_provider_wait_status({'status': status}, 'provider_pending'), 'needs_review')
        self.assertEqual(_provider_wait_status({'status': 'pending'}, 'provider_pending'), 'provider_pending')

    def test_delivery_rejects_different_bytes_than_successful_render(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            model = root / 'model.glb'; model.write_bytes(b'candidate bytes')
            report = root / 'render.json'
            report.write_text(json.dumps({'cases': [{'id': 'candidate', 'model_sha256': '0'*64, 'status': 'runtime_compatible'}]}))
            with self.assertRaisesRegex(ValueError, 'matching successful actual AR render'):
                deliver_candidate({'candidate_id': 'candidate', **pin(model)}, {'report': pin(report)}, root / 'out', {})


if __name__ == '__main__':
    unittest.main()
