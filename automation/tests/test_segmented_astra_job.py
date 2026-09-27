"""Persisted driver crash/replay tests; no browser, geometry work or real HTTP."""
import argparse
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from reconstruction import segmented_astra_job as job
from reconstruction.job import _write
from reconstruction.segmented_astra_session import SegmentedAstraSession, digest, read
from reconstruction.segmented_astra_tools import TOOLS_SCHEMA, validate
from reconstruction.segmented_astra_transport import AstraClient, PROTOCOL
from reconstruction.segmented_providers import pin
from test_segmented_astra_transport import HTTP, result


FINISH = {'note': 'Current observations reviewed; retain uncertainty', 'operations': [
    {'operation': 'finish', 'verdict': 'review_ready', 'summary': 'A review candidate, not independent acceptance'}]}


class FakeSession:
    """Exercise the real journal loop against disk, excluding actual compilation."""
    crash_after_apply = False
    replace_state_after_apply = False
    forbid_snapshot = False
    snapshot_calls = 0
    apply_calls = 0

    def __init__(self, output, **kwargs):
        self.output = Path(output)
        self.state = read(self.output / 'state.json')
        self.seed = read(self.output / 'seed.json')

    @classmethod
    def create(cls, base_job, output, **kwargs):
        output = Path(output)
        output.mkdir(parents=True, exist_ok=True)
        _write(output / 'seed.json', {'base_job': str(Path(base_job).resolve()), 'product_id': 'fixture'})
        _write(output / 'state.json', {'status': 'editing', 'current_revision': 'r0000', 'turns': [], 'events': []})
        return cls(output)

    def save(self):
        _write(self.output / 'state.json', self.state)

    def snapshot(self):
        type(self).snapshot_calls += 1
        if type(self).forbid_snapshot:
            raise AssertionError('A pending request must use its persisted snapshot')
        image = self.output / 'photo.png'
        if not image.exists():
            Image.new('RGB', (8, 8), '#887766').save(image)
        return ({'current_revision': self.state['current_revision'], 'snapshot_marker': 'captured once'},
                [{'id': 'front', 'label': 'Pinned fixture', **pin(image)}])

    def apply(self, plan, *, turn_id, model_decision=False):
        type(self).apply_calls += 1
        validate(plan)
        if type(self).replace_state_after_apply:
            self.state = deepcopy(self.state)
        event = {'turn_id': turn_id, 'plan_sha256': digest(plan), 'status': 'applied',
                 'parent': self.state['current_revision'], 'current_revision': self.state['current_revision'],
                 'model_decision': model_decision}
        self.state['events'].append(event)
        if plan['operations'][0]['operation'] == 'finish':
            self.state['status'] = 'finished'
        self.save()
        if type(self).crash_after_apply:
            raise SystemExit('Injected process death after atomic event promotion')
        return event

    def deliver(self, reason):
        return {'status': 'candidate_available', 'product_id': self.seed['product_id'],
                'stop_reason': reason, 'current_revision': self.state['current_revision'], 'accepted': False}


class LocalClient:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def describe(self):
        return {'protocol': 'local-test-client-no-network'}

    def decide(self, context, images, request_dir, *, tools_schema):
        self.calls.append({'context': deepcopy(context), 'images': deepcopy(images), 'request_dir': str(request_dir)})
        if self.error:
            raise self.error
        return deepcopy(FINISH)


class AstraJobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base, self.output = self.root / 'base', self.root / 'session'
        self.base.mkdir()
        for name in ('crash_after_apply', 'replace_state_after_apply', 'forbid_snapshot'):
            setattr(FakeSession, name, False)
        FakeSession.apply_calls = FakeSession.snapshot_calls = 0

    def run_job(self, client, maximum_turns=2):
        with patch.object(job, 'SegmentedAstraSession', FakeSession), redirect_stdout(io.StringIO()):
            return job.run_astra_job(self.base, self.output, client=client, maximum_turns=maximum_turns)

    def pending(self, client, *, current='r0000', plan=None, finished=False, event=None):
        session = FakeSession.create(self.base, self.output)
        context, images = session.snapshot()
        context.update(turn_index=0, turns_remaining_including_this=2)
        folder = self.output / 'turns/turn-0000'
        folder.mkdir(parents=True)
        snapshot = {'context': context, 'images': images, 'tools_sha256': digest(TOOLS_SCHEMA)}
        _write(folder / 'input.json', snapshot)
        turn = {'id': 'turn-0000', 'status': 'request_pending', 'input': pin(folder / 'input.json')}
        if plan:
            _write(folder / 'plan.json', plan)
            turn.update(status='planned', plan=pin(folder / 'plan.json'))
        session.state.update(current_revision=current, status='finished' if finished else 'editing', turns=[turn],
                             driver={'base_job': str(self.base), 'client': client.describe(), 'maximum_turns': 2})
        if event:
            session.state['events'].append(event)
        session.save()
        return snapshot

    def test_pending_turn_reuses_exact_saved_input_without_new_snapshot(self):
        client = LocalClient()
        snapshot = self.pending(client)
        FakeSession.forbid_snapshot = True
        report = self.run_job(client)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0]['context'], snapshot['context'])
        self.assertEqual(client.calls[0]['images'], snapshot['images'])
        self.assertEqual(report['turns_completed'], 1)
        self.assertEqual(report['stop_reason'], 'model_finished')

    def test_crash_after_completed_response_replays_transport_without_another_http_call(self):
        http = HTTP(result(FINISH))
        client = AstraClient('fake-only-key', budget_path=self.root / 'budget.json', maximum_calls=2, session=http)
        snapshot = self.pending(client)
        client.decide(snapshot['context'], snapshot['images'], self.output / 'turns/turn-0000/api', tools_schema=TOOLS_SCHEMA)
        FakeSession.forbid_snapshot = True
        report = self.run_job(client)
        self.assertEqual(len(http.calls), 1)
        self.assertEqual(report['paid_calls_used'], 1)
        self.assertEqual(report['turns_completed'], 1)
        self.assertIs(read(self.output / 'state.json')['events'][0]['model_decision'], True)

    def test_saved_plan_is_applied_without_another_decision(self):
        client = LocalClient(error=AssertionError('Should not request another decision'))
        self.pending(client, plan=FINISH)
        FakeSession.forbid_snapshot = True
        report = self.run_job(client)
        self.assertEqual(client.calls, [])
        self.assertEqual(report['turns_completed'], 1)

    def test_finish_commit_before_driver_status_save_is_reconciled_on_reopen(self):
        client = LocalClient()
        FakeSession.crash_after_apply = True
        with self.assertRaises(SystemExit):
            self.run_job(client)
        state = read(self.output / 'state.json')
        self.assertEqual(state['status'], 'finished')
        self.assertEqual(state['turns'][0]['status'], 'planned')
        self.assertEqual(len(state['events']), 1)
        FakeSession.crash_after_apply = False
        report = self.run_job(client)
        self.assertEqual(report['turns_completed'], 1)
        self.assertEqual(FakeSession.apply_calls, 1)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(read(self.output / 'state.json')['turns'][0]['status'], 'applied')

    def test_stale_pending_snapshot_is_rejected_before_any_paid_http_request(self):
        http = HTTP(result(FINISH))
        client = AstraClient('fake-only-key', budget_path=self.root / 'budget.json', maximum_calls=2, session=http)
        self.pending(client, current='r0001')
        report = self.run_job(client)
        self.assertEqual(http.calls, [])
        self.assertFalse((self.root / 'budget.json').exists())
        self.assertEqual(FakeSession.apply_calls, 0)
        self.assertEqual(report['status'], 'needs_attention')

    def test_uncertain_request_resume_never_reposts_or_creates_later_turn(self):
        http = HTTP(error=TimeoutError('uncertain completion'))
        client = AstraClient('fake-only-key', budget_path=self.root / 'budget.json', maximum_calls=2, session=http)
        first = self.run_job(client)
        second = self.run_job(client)
        self.assertEqual(len(http.calls), 1)
        self.assertEqual(first['paid_calls_used'], 1)
        self.assertEqual(second['paid_calls_used'], 1)
        self.assertEqual(second['status'], 'needs_attention')
        self.assertEqual(len(read(self.output / 'state.json')['turns']), 1)
        self.assertEqual(FakeSession.apply_calls, 0)

    def test_replaced_state_after_apply_does_not_lose_turn_completion(self):
        FakeSession.replace_state_after_apply = True
        report = self.run_job(LocalClient())
        self.assertEqual(report['turns_completed'], 1)
        self.assertEqual(read(self.output / 'state.json')['turns'][0]['status'], 'applied')

    def test_changed_completed_plan_event_binding_is_refused_without_decision(self):
        client = LocalClient()
        self.pending(client, plan=FINISH, finished=True,
                     event={'turn_id': 'turn-0000', 'plan_sha256': '0' * 64, 'status': 'applied'})
        with self.assertRaisesRegex(ValueError, 'Committed event'):
            self.run_job(client)
        self.assertEqual(client.calls, [])

    def test_changed_persisted_input_bytes_are_refused_without_decision(self):
        client = LocalClient()
        self.pending(client)
        with (self.output / 'turns/turn-0000/input.json').open('ab') as stream:
            stream.write(b' ')
        with self.assertRaises(ValueError):
            self.run_job(client)
        self.assertEqual(client.calls, [])

    def test_changed_turn_ceiling_is_refused_without_decision(self):
        client = LocalClient()
        self.pending(client)
        with self.assertRaisesRegex(ValueError, 'Changed Astra client'):
            self.run_job(client, maximum_turns=3)
        self.assertEqual(client.calls, [])

    def test_scripted_client_uses_and_pins_explicit_script(self):
        script = self.root / 'script.json'
        _write(script, [FINISH])
        client = job.ScriptedClient(script)
        report = self.run_job(client)
        self.assertEqual(report['paid_calls_used'], 0)
        self.assertIs(read(self.output / 'state.json')['events'][0]['model_decision'], False)
        self.assertEqual(client.describe()['protocol'], 'scripted_local_no_network')
        script.write_text('[]')
        with self.assertRaises(ValueError):
            client.decide({'turn_index': 0}, [], self.root / 'unused', tools_schema=TOOLS_SCHEMA)

    def test_scripted_finish_never_claims_actual_model_review(self):
        # Run the real finish and delivery methods; only current geometry is
        # stubbed. No browser/compiler is required to test provenance labels.
        for protocol, expected in [('scripted_local_no_network', False), (PROTOCOL, True)]:
            with self.subTest(protocol=protocol):
                folder = self.root / protocol
                folder.mkdir()
                obs = folder / 'observation.json'
                _write(obs, {'status': 'runtime_compatible', 'candidate_sha256': 'a' * 64,
                             'baseline_sha256': 'b' * 64,
                             'images': [], 'render_images': [], 'source_photos': []})
                current = {'id': 'r0000', 'candidate': {'sha256': 'a' * 64}, 'authoring': {},
                           'metrics': {}, 'observation': pin(obs)}
                session = SegmentedAstraSession.__new__(SegmentedAstraSession)
                session.output, session.state_path = folder, folder / 'state.json'
                session.seed = {'product_id': 'fixture', 'baseline': {'sha256': 'b' * 64}}
                session.state = {'current_revision': 'r0000', 'status': 'editing', 'events': [],
                                 'current_observation': pin(obs), 'focus': None,
                                 'driver': {'client': {'protocol': protocol}}, 'turns': []}
                session.current = lambda: deepcopy(current)
                session.save()
                event = session.apply(FINISH, turn_id='turn-0000', model_decision=expected)
                self.assertEqual(event['status'], 'applied')
                report = session.deliver('model_finished')
                self.assertIs(report['model_reviewed_current'], expected)
                self.assertIs(report['accepted'], False)

    def test_injected_session_schema_request_folder_and_the_ledger_total(self):
        # Another job (bsa.look) passes its own session class and schema, and may name its request folder
        # (bsa.look: api-<session id>, so a --fresh session never reuses a reserved folder). paid_calls_used
        # stays the ledger total, as before the hooks (bsa.look counts its own session itself).
        schema = {'type': 'object', 'properties': {'note': {'type': 'string'}}, 'required': ['note'],
                  'additionalProperties': False}
        budget = self.root / 'budget.json'
        _write(budget, {'protocol': PROTOCOL, 'maximum_calls': 3, 'reservations': [
            {'ordinal': 1, 'request_dir': str(self.root / 'other/turns/turn-0000/api'), 'request_sha256': '0' * 64,
             'reserved_unix': 0}]})
        http = HTTP(result(FINISH))
        client = AstraClient('fake-only-key', budget_path=budget, maximum_calls=3, session=http)
        with redirect_stdout(io.StringIO()):
            report = job.run_astra_job(self.base, self.output, client=client, maximum_turns=2,
                                       session_cls=FakeSession, tools_schema=TOOLS_SCHEMA)
        self.assertEqual(report['paid_calls_used'], 2)
        self.assertEqual(len(read(budget)['reservations']), 2)
        self.assertTrue((self.output / 'turns/turn-0000/api/request.json').exists())    # the default folder
        named = type('NamedSession', (FakeSession,), {'request_folder': 'api-0123abcd'})
        other = self.root / 'session-2'
        with redirect_stdout(io.StringIO()):
            job.run_astra_job(self.base, other, client=client, maximum_turns=1, session_cls=named,
                              tools_schema=TOOLS_SCHEMA)
        self.assertTrue((other / 'turns/turn-0000/api-0123abcd/request.json').exists())
        self.assertFalse((other / 'turns/turn-0000/api').exists())
        self.assertEqual(Path(read(budget)['reservations'][-1]['request_dir']).name, 'api-0123abcd')
        self.assertEqual(read(self.output / 'turns/turn-0000/input.json')['tools_sha256'], digest(TOOLS_SCHEMA))
        with self.assertRaisesRegex(ValueError, 'Turn tool schema changed'), redirect_stdout(io.StringIO()):
            # a pending turn pinned to one schema is never decided under another
            state = read(self.output / 'state.json')
            state.update(status='editing', events=[])
            state['turns'][0]['status'] = 'request_pending'
            state['turns'][0].pop('plan', None)
            _write(self.output / 'state.json', state)
            job.run_astra_job(self.base, self.output, client=client, maximum_turns=2,
                              session_cls=FakeSession, tools_schema=schema)

    def test_cli_requires_explicit_authorization_and_redacts_credentials_in_description(self):
        parser = argparse.ArgumentParser()
        job.add_astra_arguments(parser, standalone=True)
        with patch.dict('os.environ', {'TEST_ASTRA_KEY': 'unit-test-secret'}, clear=False):
            args = parser.parse_args(['--astra-api-key-env', 'TEST_ASTRA_KEY'])
            with self.assertRaisesRegex(ValueError, 'explicit authorization'):
                job.client_from_args(args)
            args = parser.parse_args(['--authorize-paid-astra', '--astra-budget', str(self.root / 'budget.json'),
                                      '--astra-maximum-calls', '2', '--astra-api-key-env', 'TEST_ASTRA_KEY'])
            client = job.client_from_args(args)
            self.assertNotIn('unit-test-secret', json.dumps(client.describe()))
            self.assertFalse((self.root / 'budget.json').exists(), 'Client construction must not call or reserve')
            client._session.close()
        script = self.root / 'script.json'
        _write(script, [FINISH])
        args = parser.parse_args(['--astra-script', str(script), '--authorize-paid-astra'])
        with self.assertRaisesRegex(ValueError, 'Choose scripted'):
            job.client_from_args(args)


if __name__ == '__main__':
    unittest.main()
