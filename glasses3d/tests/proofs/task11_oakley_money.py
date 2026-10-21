"""Offline money-policy proof using a saved Oakley run; never modifies the source job.

Usage/counts and immutable visual evidence are replayed, not Astra's choices or Blender.
The real SDK, guard, current instructions, tool output and two-call finish procedure run.
Renewal is disabled here to isolate admission; its own SDK tests cover renewal policy.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import threading
from types import SimpleNamespace
from unittest.mock import patch

from glasses3d.agent import approvals as A, brief, instructions, jobs, loop as L, replay as R, tools as T
from glasses3d.agent.offline import LoopbackOnly, ProductionTransportTripwire, disable_sdk_tracing
from glasses3d.tests.proofs.offline_job_approval import approve_offline_request


FORBIDDEN = re.compile(r"finish.runway|finish.review (?:projection|warning)|estimated cost|"
                       r"projected.*(?:cost|hold)|reserve room|start the finish review now|"
                       r"keep budget to|planning_allowance|reserved_micro_usd", re.I)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def usage(row):
    u = row['usage']
    return {'input_tokens': u['input_tokens'], 'output_tokens': u['output_tokens'],
            'total_tokens': u['input_tokens'] + u['output_tokens'],
            'input_tokens_details': {'cached_tokens': u['cached_tokens'],
                                     'cache_write_tokens': u['cache_write_tokens']},
            'output_tokens_details': {'reasoning_tokens': u['reasoning_tokens']}}


def finish_pair(refs, first_usage, second_usage, count1, count2):
    difference = {'id': 'saved-evidence-replay', 'refs': list(refs),
        'what_differs': 'Saved Oakley evidence replay; no quality judgement.',
        'evidence_ref': 'review', 'status': 'limitation',
        'last_correction': 'No model correction attempted; immutable saved evidence is replayed.',
        'outcome': 'The delivered review replays the saved model and photo comparisons without a new likeness assessment.',
        'next_action': 'No further model action in this money-policy proof; no new product acceptance is asserted.',
        'reason': 'Saved visual evidence and scripted replies test settled-spend admission and finish handling, not a new Astra or owner visual judgement.'}
    return [R.tool_call('finish', {'summary': 'Inspect saved evidence.'}, call_id='review',
                        counted=count1, usage=first_usage),
            R.tool_call('finish', {'summary': 'Offline finish procedure verified.',
                'review_call_id': 'review', 'per_photo': [{'ref': ref,
                    'differences': difference['what_differs'], 'difference_ids': [difference['id']]}
                    for ref in difference['refs']], 'difference_updates': [difference]}, call_id='confirm',
                counted=count2, usage=second_usage)]


class SavedScene(L.SceneHooks):
    def __init__(self, root, saved):
        self.root, self.saved = root, saved
        self.job = jobs.load_job(root)
        self.s = SimpleNamespace(head={'id': saved['checkpoint'], 'fingerprint': saved['fingerprint']})
        self.lock = threading.RLock()
        self.finish = self.finish_review = self.last_ar = None
        self.calls, self.images, self.unseen = [], [], []
        self.seeing = SimpleNamespace(export_head=lambda **kw: copy.deepcopy(saved['export']))

    def start(self):
        return {'head': self.s.head['id']}

    def bind_review_log(self, log):
        self._review_log = log
        self.active_run_id = log.run_id

    def record_call(self, cid, name, before, after, result):
        self.calls.append({'call_id': cid, 'tool': name, 'checkpoint_before': before,
                           'checkpoint_after': after, 'status': result['status']})

    def record_images(self, cid, result):
        self.images.extend(result.get('images', []))

    def mark_unseen(self, cid):
        self.unseen.append(cid)

    def journal_entries(self):
        return self.calls

    def exit_checkpoint(self):
        return {'id': self.s.head['id'], 'sha256': self.saved['export']['sha256'], 'verified': True}


def copy_evidence(job, root, saved, hashes):
    """Only copy referenced source files. All writes stay in the new proof directory."""
    def file_copy(source):
        relative = source.relative_to(job)
        target = root / relative
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        hashes[str(source)] = sha(source)
        assert sha(target) == hashes[str(source)]
        return str(target)

    file_copy(job / 'job.json')
    for directory in ('images', 'reference_images'):
        for source in (job / directory).rglob('*'):
            if source.is_file():
                file_copy(source)

    def walk(value):
        if isinstance(value, dict):
            return {k: walk(v) for k, v in value.items()}
        if isinstance(value, list):
            return [walk(v) for v in value]
        if isinstance(value, str) and len(value) < 1000 and '\n' not in value:
            try:
                source = Path(value)
                source = source if source.is_absolute() else job / source
                source = source.resolve()
                if source.is_relative_to(job) and source.is_file():
                    return file_copy(source)
            except (OSError, ValueError):
                pass
        return value
    # Historical money text is not an input: only renderer/export evidence is replayed.
    return {key: walk(saved[key]) for key in ('checkpoint', 'fingerprint', 'export', 'arview', 'look_comparison', 'review')}


def scenario(job, root, saved, replies, hashes):
    root.mkdir(parents=True, exist_ok=False)
    evidence = copy_evidence(job, root, saved, hashes)
    scene = SavedScene(root, evidence)
    settings = L.RunSettings(effort='high', max_turns=None, settled_stop_usd='20',
                             finish_tool='finish', tool_choice='required', reset_tool_choice=False)
    registry = T.build_tools(scene, settings)
    text = instructions.build_instructions(registry)
    refs = jobs.references(root)
    owner_brief = brief.build_brief(scene.job)
    transport = R.ScriptedOpenAI(replies)
    request = A.build_request(root, instructions_sha256=L.instructions_sha256(text),
                              tool_registry_sha256=L.tool_registry_sha256(registry, settings),
                              settings=settings.approval_settings(),
                              run_inputs=A.run_input_binding(root, owner_brief, refs))
    run = root / 'runs/offline-policy'
    A.write_approval_request(run, job_id=root.name, request=request, estimate={}, suggested_cap_usd='20')
    approve_offline_request(run, transport.handle, cap='20')
    spec = L.RunSpec(root, run.name, text, registry, owner_brief, A.request_sha256(request), photos=refs,
                     settings=settings, requested_cap_usd='20', contract=instructions.instruction_contract(registry),
                     contract_brief=brief.host_text(scene.job),
                     offline_handler=transport.handle, offline_api_key='sk-offline-task11-money',
                     scene_note='Immutable saved Oakley finish evidence; offline money-policy proof.')
    with patch.object(T.arview, 'capture', lambda *a, **k: copy.deepcopy(evidence['arview'])), \
         patch.object(T.look, 'capture', lambda *a, **k: copy.deepcopy(evidence['look_comparison'])), \
         patch.object(T.finish_review, 'evidence', lambda *a, **k: copy.deepcopy(evidence['review']['binding'])), \
         patch.object(L.ConversationRenewal, 'check', lambda *a: None):
        result = asyncio.run(L.run_astra(spec, scene=scene))
    assert not transport.violations and not transport.unexpected, transport.violations
    assert len(transport.counts) == len(transport.creates) == result['requests']
    for body in transport.creates:
        assert not FORBIDDEN.search(json.dumps(body)), 'projection reached the scripted model'
        assert body['max_output_tokens'] == 16384
    ledger = json.loads((run / 'budget.json').read_bytes())
    settled_before, amounts = 0, []
    for row in ledger['rows']:
        assert settled_before < 20_000_000, 'a request started at/above the cutoff'
        amounts.append(settled_before)
        settled_before += row['settled_micro_usd']
    payload = {'status': result['status'], 'requests': result['requests'], 'settled_micro_usd': settled_before,
               'settled_before_each_request': amounts, 'calls': scene.calls,
               'first_finish_input': next((b for b in transport.creates if any(
                   x.get('type') == 'function_call_output' and 'review_required' in str(x.get('output'))
                   for x in b.get('input', []))), None),
               'projection_matches': 0}
    # Request bodies carry photo data URLs; keep only hashes and text parts in the report artifact.
    payload['first_finish_input'] = bool(payload['first_finish_input'])
    payload['request_sha256'] = [hashlib.sha256(json.dumps(b, sort_keys=True).encode()).hexdigest()
                                for b in transport.creates]
    (root / 'proof.json').write_text(json.dumps(payload, indent=2), encoding='utf-8')
    return payload


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--saved-run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    run, output = args.saved_run.resolve(strict=True), args.output.resolve()
    job = run.parent.parent
    assert not output.is_relative_to(job)
    output.mkdir(parents=True, exist_ok=False)
    ledger = json.loads((run / 'budget.json').read_bytes())
    rows = ledger['rows']
    assert len(rows) == 48 and sum(r['settled_micro_usd'] for r in rows) == 12_277_845
    finish = json.loads((run / 'tools/0045-finish/result.json').read_bytes())
    report_path = job / json.loads(finish['result'][0]['text'])['full_report']['path']
    saved = json.loads(report_path.read_bytes())['result']
    hashes = {str(p): sha(p) for p in (run / 'budget.json', run / 'tools/0045-finish/result.json', report_path)}
    refs = [p['ref'] for p in saved['review']['binding']['photos']]
    def status(row):
        return R.tool_call('budget_status', counted=row['input_tokens'], usage=usage(row))
    os.environ['G3D_OFFLINE'] = '1'
    disable_sdk_tracing()
    with LoopbackOnly() as network, ProductionTransportTripwire() as production:
        replay = scenario(job, output / 'saved-spend-finish', saved,
                          [status(r) for r in rows] + finish_pair(refs, usage(rows[-2]), usage(rows[-1]),
                          rows[-2]['input_tokens'], rows[-1]['input_tokens']), hashes)
        assert replay['status'] == 'completed' and replay['calls'][-1]['status'] == 'accepted', replay
        assert replay['first_finish_input'] and replay['requests'] == 50
        continued = scenario(job, output / 'continue-to-cutoff', saved,
                             [status(r) for r in rows * 3], hashes)
        assert continued['status'] == 'budget_limit' and continued['settled_micro_usd'] >= 20_000_000, continued
        assert continued['settled_before_each_request'][-1] < 20_000_000
        # $19.99 settled, first finish adds one micro-dollar. A full-size request still starts.
        first = R.default_usage(1, output_tokens=0, reasoning_tokens=0)
        first['input_tokens_details'] = {'cached_tokens': 1, 'cache_write_tokens': 0}
        near = scenario(job, output / 'finish-near-cutoff', saved,
                        [R.tool_call('budget_status', counted=799600,
                                     usage=R.default_usage(799600, output_tokens=0, reasoning_tokens=0))]
                        + finish_pair(refs, first, R.default_usage(922000, output_tokens=0, reasoning_tokens=0),
                                      1, 922000), hashes)
        assert near['requests'] == 3 and near['calls'][-1]['status'] == 'accepted', near
        assert near['settled_before_each_request'][-1] == 19_990_001, near
        assert not network.attempts and not production.constructions
    assert all(sha(p) == digest for p, digest in hashes.items()), 'saved source changed'
    report = {'saved_run': str(run), 'scope': __doc__, 'api_cost_usd': 0,
              'network_attempts': 0, 'production_transport_attempts': 0,
              'source_sha256': hashes, 'saved_spend_finish': replay,
              'continue_to_cutoff': continued, 'finish_near_cutoff': near}
    (output / 'proof.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({k: {n: v for n, v in r.items() if n in ('status', 'requests', 'settled_micro_usd')}
                      for k, r in report.items() if k in ('saved_spend_finish', 'continue_to_cutoff', 'finish_near_cutoff')}, indent=2))


if __name__ == '__main__':
    main()
