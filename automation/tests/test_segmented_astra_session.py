"""Canonical compiler and session transactions; observer is offline/injected.

These tests compile real grouped optical GLBs. No model/API or browser is used.
"""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from reconstruction.job import _write, _inventory
from reconstruction.prepare_optical_groups import run_optical_group_preparation
from reconstruction.compact_glb import run_compact_asset
from reconstruction.optical_group_asset import read_optical_group_candidate
from reconstruction.segmented_providers import pin, verified
from reconstruction.segmented_astra_session import SegmentedAstraSession, compile_authoring, read
from test_segmented_astra_geometry import fixture, attrs
from test_prepare_optical_groups import declarations


def plan(*operations, note='Bounded offline session test'):
    return {'note': note, 'operations': list(operations)}


def frame(roughness=.3):
    return {'operation': 'frame_material', 'part_ids': [1], 'base_color_linear_rgb': None,
            'roughness': roughness, 'metallic': None}


def translate():
    return {'operation': 'translate', 'part_ids': [0], 'offset_m': [0, 0, .0005]}


def appearance_change(current):
    source = current['authoring']['appearances']['lens']
    fields = ('normal_reflectance_rgb', 'refractive_index', 'roughness', 'optical_density_keyframes',
              'angular_reflectance_keyframes', 'rear_reflection_fraction_rgb')
    value = {key: deepcopy(source.get(key)) for key in fields}
    value['roughness'] = .21
    value['optical_density_keyframes'] = [{'v': 0, 'optical_density_rgb': [.4, .3, .2]}]
    return {'operation': 'optical_appearance', 'group_ids': ['lens'], 'appearance': value}


class Observer:
    def __init__(self):
        self.calls = []
        self.fail = False
        self.wrong_sha = False

    def __call__(self, candidate, baseline, photos, output, **kwargs):
        self.calls.append({'candidate': candidate['sha256'], 'baseline': baseline['sha256'], **kwargs})
        output = Path(output); output.mkdir(parents=True, exist_ok=False)
        if self.fail:
            raise ValueError('Injected observer failure')
        image = output/'candidate.png'; Image.new('RGB', (20, 16), '#887766').save(image)
        return {'status': 'runtime_compatible',
                'candidate_sha256': '0'*64 if self.wrong_sha else candidate['sha256'],
                'baseline_sha256': baseline['sha256'],
                'images': [{'id': 'test-card', 'label': 'Injected test observer; no AR claim', **pin(image)}],
                'limitations': ['Injected test observer; actual AR is outside this unit test.']}


def base_job(root):
    root.mkdir()
    source = fixture(root/'source.glb')
    input_dir = root/'stages/input/attempt-0001'; input_dir.mkdir(parents=True)
    photo = input_dir/'front.png'; Image.new('RGB', (24, 18), '#cccbbb').save(photo)
    prep_dir = root/'stages/optical_preparation/attempt-0001/prepared'
    declaration = declarations(source, [('lens', [0])])
    declaration['coordinate_frame']['units'] = 'meters'
    prepared = run_optical_group_preparation(source, prep_dir,
        grouping_mode='explicit_declarations', declarations=declaration)
    if prepared['status'] != 'prepared_optical_group_candidate':
        raise AssertionError(prepared)
    delivery_dir = root/'stages/delivery/attempt-0001'
    compact = run_compact_asset(prep_dir/prepared['model']['path'], delivery_dir,
                               optical_receipt=json.loads((prep_dir/prepared['export']['path']).read_bytes()))
    appearance_dir = root/'stages/appearance/attempt-0001'; appearance_dir.mkdir(parents=True)
    _write(appearance_dir/'runtime-manifest.json', {'fixture': True})
    review_dir = root/'stages/appearance_review/attempt-0001'; review_dir.mkdir(parents=True)
    _write(review_dir/'review.json', {'fixture': True})
    stages = {}

    def stage(name, folder, result):
        stages[name] = {'status': 'complete', 'directory': folder.relative_to(root).as_posix(),
                        'artifacts': _inventory(root, folder), 'result': result}
    stage('input', input_dir, {'photos': [{'id': 'front', 'view': 'front', **pin(photo)}]})
    stage('optical_preparation', prep_dir.parent, {'preparation_report': pin(prep_dir/'report.json')})
    stage('appearance', appearance_dir, {'runtime_manifest': str(appearance_dir/'runtime-manifest.json')})
    stage('appearance_review', review_dir, {'selected_candidate': {**compact['model'], 'normal_variant': 'original'}})
    stage('delivery', delivery_dir, {'candidate': compact['model'], 'export': compact['export'], 'violations': []})
    journal = {'pipeline': 'segmented_ar_v1', 'request_binding': 'a'*64,
               'settings': {'display_width_mm': 145, 'maximum_triangles': 150000, 'maximum_bytes': 15000000},
               'stages': stages}
    _write(root/'journal.json', journal)
    _write(root/'report.json', {'product_id': 'fixture', 'review_reasons': ['unmeasured source camera']})
    return root


class SegmentedAstraSessionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name); self.base = base_job(self.root/'base'); self.output = self.root/'session'
        self.observer = Observer()
        # Concurrent implementation work must not invalidate this isolated unit
        # fixture; explicit mismatch tests exercise the gate independently.
        patched = patch('reconstruction.segmented_astra_session.implementation', return_value={'fixture_version': 1})
        patched.start(); self.addCleanup(patched.stop)

    def create(self):
        return SegmentedAstraSession.create(self.base, self.output, observer=self.observer)

    def reopen(self):
        return SegmentedAstraSession(self.output, observer=self.observer)

    def test_initial_revision_is_exact_baseline_and_reopen_makes_no_observation(self):
        session = self.create(); initial = session.current()
        self.assertEqual(initial['id'], 'r0000')
        self.assertEqual(initial['candidate']['sha256'], session.seed['baseline']['sha256'])
        self.assertEqual(verified(initial['candidate']).read_bytes(), verified(session.seed['baseline']).read_bytes())
        before_calls = len(self.observer.calls)
        self.assertEqual(self.reopen().current()['id'], 'r0000')
        self.assertEqual(len(self.observer.calls), before_calls)
        context, images = session.snapshot()
        self.assertFalse(context['accepted']); self.assertEqual(len(images), 2)
        delivered = session.deliver('fixture budget')
        self.assertFalse(delivered['accepted']); self.assertFalse(delivered['production_ready'])
        self.assertFalse(delivered['model_reviewed_current'])

    def test_real_compiler_geometry_edit_and_material_edit_then_exact_restore(self):
        session = self.create(); initial = session.current()
        event = session.apply(plan(translate(), frame()), turn_id='turn-0001')
        self.assertEqual(event['status'], 'applied', event)
        changed = session.current()
        self.assertEqual(changed['id'], 'r0001'); self.assertEqual(len(changed['proofs']), 2)
        self.assertNotEqual(changed['candidate']['sha256'], initial['candidate']['sha256'])
        old_source = verified(initial['authoring']['source']); new_source = verified(changed['authoring']['source'])
        np.testing.assert_allclose(attrs(new_source)['POSITION'], attrs(old_source)['POSITION']+[0, 0, .0005], atol=1e-10)
        np.testing.assert_array_equal(attrs(new_source, 1)['POSITION'], attrs(old_source, 1)['POSITION'])
        receipt = read(verified(changed['candidate']['export']))
        loaded = read_optical_group_candidate(verified(changed['candidate']), receipt)
        self.assertEqual(loaded['groups'][0]['source_part_indices'], [0])
        self.assertEqual(receipt['source_sha256'], changed['authoring']['source']['sha256'])
        self.assertEqual(self.reopen().current()['candidate']['sha256'], changed['candidate']['sha256'])
        count = len(self.observer.calls)
        restored = session.apply(plan({'operation': 'restore', 'revision_id': 'r0000'}), turn_id='turn-0002')
        self.assertEqual(restored['status'], 'applied')
        self.assertEqual(session.current()['candidate'], initial['candidate'])
        self.assertEqual(session.current()['authoring'], initial['authoring'])
        self.assertEqual(len(self.observer.calls), count, 'Restore uses its exact retained observation')
        self.assertEqual(self.reopen().current()['id'], 'r0000')

    def test_optical_only_edit_reuses_geometry_and_preserves_source(self):
        session = self.create(); initial = session.current()
        event = session.apply(plan(appearance_change(initial)), turn_id='turn-0001')
        self.assertEqual(event['status'], 'applied', event)
        changed = session.current()
        self.assertEqual(changed['authoring']['source'], initial['authoring']['source'])
        self.assertEqual(changed['preparation'], initial['preparation'])
        self.assertEqual(changed['authoring']['appearances']['lens']['roughness'], .21)
        self.assertNotEqual(changed['candidate']['sha256'], initial['candidate']['sha256'])

    def test_group_membership_rebuild_binds_new_source_part_in_actual_export(self):
        session = self.create(); initial = session.current()
        event = session.apply(plan({'operation': 'group_membership', 'group_id': 'lens', 'part_ids': [1]}),
                              turn_id='turn-0001')
        self.assertEqual(event['status'], 'applied', event)
        current = session.current()
        self.assertEqual(current['authoring']['source'], initial['authoring']['source'])
        self.assertEqual(current['authoring']['groups'], {'lens': [1]})
        self.assertNotEqual(current['preparation'], initial['preparation'])
        receipt = read(verified(current['candidate']['export']))
        loaded = read_optical_group_candidate(verified(current['candidate']), receipt)
        self.assertEqual(loaded['groups'][0]['source_part_indices'], [1])

    def test_failed_second_operation_rejects_whole_batch_without_promoting_first(self):
        session = self.create(); initial = session.current(); before_calls = len(self.observer.calls)
        event = session.apply(plan(translate(), {'operation': 'group_membership', 'group_id': 'missing', 'part_ids': [1]}),
                              turn_id='turn-0001')
        self.assertEqual(event['status'], 'rejected')
        self.assertEqual(session.current()['candidate'], initial['candidate'])
        self.assertEqual(session.current()['authoring'], initial['authoring'])
        self.assertEqual(list(session.state['revisions']), ['r0000'])
        self.assertEqual(len(self.observer.calls), before_calls)
        self.assertEqual(self.reopen().current()['id'], 'r0000')
        self.assertTrue(list((self.output/'edits/turn-0001').glob('attempt-*/proof-00.json')))

    def test_compiler_failure_and_wrong_render_sha_reject_without_promotion(self):
        session = self.create(); initial = session.current()
        with patch('reconstruction.segmented_astra_session.compile_authoring', side_effect=ValueError('fixture refusal')):
            event = session.apply(plan(frame()), turn_id='turn-0001')
        self.assertEqual(event['status'], 'rejected'); self.assertIn('fixture refusal', event['error'])
        self.observer.wrong_sha = True
        event = session.apply(plan(frame()), turn_id='turn-0002')
        self.assertEqual(event['status'], 'rejected'); self.assertIn('exact current candidate', event['error'])
        self.assertEqual(session.current()['candidate'], initial['candidate'])
        self.assertEqual(list(session.state['revisions']), ['r0000'])

    def test_invalid_schema_or_duplicate_selection_records_refusal_without_mutation(self):
        session = self.create()
        invalid = plan(dict(translate(), part_ids=[0, 0]))
        refused = session.apply(invalid, turn_id='turn-0001')
        self.assertEqual(refused['status'], 'rejected')
        refused = session.apply(plan({'operation': 'finish', 'verdict': 'review_ready', 'summary': 'done'}, translate()), turn_id='turn-0002')
        self.assertEqual(refused['status'], 'rejected')
        self.assertEqual(list(session.state['revisions']), ['r0000'])
        self.assertEqual(session.current()['id'], 'r0000')
        self.assertEqual(len(self.observer.calls), 1)

    def test_retry_same_turn_is_idempotent_but_rebinding_plan_is_refused(self):
        session = self.create(); chosen = plan(frame())
        first = session.apply(chosen, turn_id='turn-0001')
        self.assertEqual(first['status'], 'applied', first)
        calls = len(self.observer.calls)
        self.assertEqual(session.apply(chosen, turn_id='turn-0001'), first)
        self.assertEqual(len(self.observer.calls), calls)
        with self.assertRaises(ValueError):
            session.apply(plan(frame(.7)), turn_id='turn-0001')

    def test_turn_id_cannot_escape_session_directory(self):
        session = self.create()
        with self.assertRaises(ValueError):
            session.apply(plan({'operation': 'restore', 'revision_id': 'r0000'}), turn_id='../../escaped')
        self.assertFalse((self.root/'escaped').exists())

    def test_failed_inspection_preserves_previous_focused_observation(self):
        session = self.create()
        inspected = session.apply(plan({'operation': 'inspect', 'part_ids': [0], 'padding_fraction': .1}), turn_id='turn-0001')
        self.assertEqual(inspected['status'], 'applied')
        observation = deepcopy(session.state['current_observation']); focus = deepcopy(session.state['focus'])
        self.observer.fail = True
        refused = session.apply(plan({'operation': 'inspect', 'part_ids': [1], 'padding_fraction': .1}), turn_id='turn-0002')
        self.assertEqual(refused['status'], 'rejected')
        self.assertEqual(session.state['current_observation'], observation)
        self.assertEqual(session.state['focus'], focus)

    def test_initial_render_failure_can_resume_from_exact_input_without_recreating_seed(self):
        self.observer.fail = True
        with self.assertRaisesRegex(ValueError, 'Injected observer failure'):
            self.create()
        self.assertEqual(read(self.output/'state.json')['status'], 'initializing')
        seed_pin = pin(self.output/'seed.json')
        self.observer.fail = False
        session = self.reopen()
        self.assertEqual(session.current()['id'], 'r0000')
        self.assertEqual(pin(self.output/'seed.json'), seed_pin)
        self.assertEqual(len(list((self.output/'revisions/r0000').glob('attempt-*'))), 2)

    def test_seed_implementation_or_immutable_input_tampering_refuses_reopen(self):
        session = self.create()
        with patch('reconstruction.segmented_astra_session.implementation', return_value={'fixture_version': 2}):
            with self.assertRaisesRegex(ValueError, 'changed'):
                self.reopen()
        source = verified(session.seed['authoring']['source'])
        source.write_bytes(source.read_bytes()+b'tampered')
        with self.assertRaisesRegex(ValueError, 'integrity|changed|pin'):
            self.reopen()

    def test_tampered_observation_image_and_revision_manifest_refuse_use(self):
        session = self.create(); current = session.current()
        observation = read(verified(current['observation']))
        image = verified(observation['images'][0]); original = image.read_bytes(); image.write_bytes(b'tampered')
        with self.assertRaises(ValueError):
            session.snapshot()
        with self.assertRaises(ValueError):
            self.reopen()
        image.write_bytes(original)
        revision = verified(session.state['revisions']['r0000']); revision.write_bytes(revision.read_bytes()+b' ')
        with self.assertRaises(ValueError):
            self.reopen()

    def test_selected_delivery_mismatch_stops_before_first_observer_call(self):
        journal = read(self.base/'journal.json')
        journal['stages']['appearance_review']['result']['selected_candidate']['sha256'] = 'f'*64
        _write(self.base/'journal.json', journal)
        with self.assertRaisesRegex(ValueError, 'mismatch'):
            self.create()
        self.assertEqual(self.observer.calls, [])

    def test_compiler_refuses_reuse_from_different_source(self):
        session = self.create(); current = session.current(); authoring = deepcopy(current['authoring'])
        altered = fixture(self.root/'different.glb', mutate=lambda doc: doc['extras'].update(different=True))
        authoring['source'] = pin(altered)
        with self.assertRaisesRegex(ValueError, 'differs'):
            compile_authoring(authoring, self.root/'mismatch-compile', reuse_preparation=current['preparation'])

    def test_finish_is_terminal_but_exact_finish_replay_is_idempotent(self):
        session = self.create()
        finish = plan({'operation': 'finish', 'verdict': 'review_ready', 'summary': 'Reviewed injected test observation.'})
        first = session.apply(finish, turn_id='turn-0001')
        self.assertEqual(first['status'], 'applied')
        session = self.reopen()
        self.assertEqual(session.apply(finish, turn_id='turn-0001'), first)
        with self.assertRaisesRegex(ValueError, 'Finished'):
            session.apply(plan(frame()), turn_id='turn-0002')
        self.assertFalse(session.deliver('finished')['model_reviewed_current'],
                         'A scripted finish is not evidence that Astra reviewed the current model')
        self.assertFalse(session.deliver('finished')['accepted'])

    def test_crash_after_atomic_promotion_replays_event_without_reapplying_geometry(self):
        class Crash(BaseException):
            pass
        session = self.create(); actual_write = _write; crashed = False

        def write_then_crash(path, value):
            nonlocal crashed
            actual_write(path, value)
            if Path(path) == session.state_path and value.get('current_revision') == 'r0001' and not crashed:
                crashed = True
                raise Crash('process died after atomic commit')
        chosen = plan(translate())
        with patch('reconstruction.segmented_astra_session._write', side_effect=write_then_crash):
            with self.assertRaises(Crash):
                session.apply(chosen, turn_id='turn-0001')
        saved = read(session.state_path)
        self.assertEqual(saved['current_revision'], 'r0001')
        self.assertEqual(saved['events'][0]['turn_id'], 'turn-0001')
        calls = len(self.observer.calls); reopened = self.reopen()
        event = reopened.apply(chosen, turn_id='turn-0001')
        self.assertEqual(event['status'], 'applied')
        self.assertEqual(len(self.observer.calls), calls)
        self.assertEqual(len(reopened.state['revisions']), 2)
        self.assertEqual(len(list((self.output/'edits/turn-0001').glob('attempt-*'))), 1)

    def test_crash_before_atomic_promotion_retries_from_unmodified_parent(self):
        class Crash(BaseException):
            pass
        session = self.create(); initial = session.current(); actual_write = _write

        def crash_before_write(path, value):
            if Path(path) == session.state_path and value.get('current_revision') == 'r0001':
                raise Crash('process died before atomic commit')
            actual_write(path, value)
        chosen = plan(translate())
        with patch('reconstruction.segmented_astra_session._write', side_effect=crash_before_write):
            with self.assertRaises(Crash):
                session.apply(chosen, turn_id='turn-0001')
        self.assertEqual(read(session.state_path)['current_revision'], 'r0000')
        reopened = self.reopen()
        event = reopened.apply(chosen, turn_id='turn-0001')
        self.assertEqual(event['status'], 'applied', event)
        original = attrs(verified(initial['authoring']['source']))['POSITION']
        current = attrs(verified(reopened.current()['authoring']['source']))['POSITION']
        np.testing.assert_allclose(current, original+[0, 0, .0005], atol=1e-10)
        self.assertEqual(len(list((self.output/'edits/turn-0001').glob('attempt-*'))), 2)

    def test_focused_observation_tampering_refuses_delivery_and_reopen(self):
        session = self.create()
        event = session.apply(plan({'operation': 'inspect', 'part_ids': [0], 'padding_fraction': .1}), turn_id='turn-0001')
        self.assertEqual(event['status'], 'applied')
        observation = read(verified(session.state['current_observation']))
        image = verified(observation['images'][0]); image.write_bytes(b'changed focus evidence')
        with self.assertRaises(ValueError):
            session.deliver('test corrupted focus')
        with self.assertRaises(ValueError):
            self.reopen()


if __name__ == '__main__':
    unittest.main()
