"""Opt-in bounded Astra stage for a completed canonical segmented job.

Offline: --astra-script plans.json. Live: --authorize-paid-astra --astra-budget PATH
--astra-maximum-calls N and an explicit credential source. No provider fallback.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .job import _job_lock, _write
from .segmented_providers import pin, verified
from .segmented_astra_session import SegmentedAstraSession, read, digest
from .segmented_astra_tools import TOOLS_SCHEMA, validate
from .segmented_astra_transport import AstraClient


class ScriptedClient:
    """Deterministic local test driver. Never calls a model or a network provider.
    ``validator`` checks each plan (default: the segmented catalog's validate)."""
    def __init__(self, path, validator=None):
        self.path = Path(path).resolve()
        self.plans = read(self.path)
        if not isinstance(self.plans, list) or not self.plans:
            raise ValueError('Script must contain a nonempty list of typed plans')
        for plan in self.plans:
            (validate if validator is None else validator)(plan)
        self.reference = pin(self.path)

    def describe(self):
        return dict(protocol='scripted_local_no_network', script=self.reference)

    def decide(self, context, images, request_dir, *, tools_schema):
        verified(self.reference)
        index = context['turn_index']
        if index >= len(self.plans):
            raise RuntimeError('No scripted plan remains')
        return self.plans[index]


def run_astra_job(base_job, output, *, client, maximum_turns=3, observer=None, session_cls=None, tools_schema=None):
    """Drive one session through at most ``maximum_turns`` decisions. ``session_cls`` / ``tools_schema``
    default to the segmented editor (resolved at call time); another job (bsa.look) passes its own session
    class implementing create/__init__/state/seed/save/snapshot/apply/deliver and its own strict schema. A session
    may name its per-turn request folder (``request_folder``, default 'api'): a session whose output path is reused
    by a later session (bsa.look after --fresh) names it uniquely, so a shared ledger never sees one path twice."""
    if type(maximum_turns) is not int or not 1 <= maximum_turns <= 10:
        raise ValueError('maximum_turns must be 1..10')
    session_cls = SegmentedAstraSession if session_cls is None else session_cls
    tools_schema = TOOLS_SCHEMA if tools_schema is None else tools_schema
    base_job, output = Path(base_job).resolve(), Path(output).resolve()
    options = {} if observer is None else dict(observer=observer)
    binding = dict(base_job=str(base_job), client=client.describe(), maximum_turns=maximum_turns)
    with _job_lock(output):
        if (output / 'state.json').exists():
            session = session_cls(output, **options)
            if session.seed['base_job'] != str(base_job):
                raise ValueError('Session belongs to a different base job')
        else:
            # Base stage snapshots cannot change while their inventory is checked/copied.
            with _job_lock(base_job):
                session = session_cls.create(base_job, output, **options)
        if 'driver' in session.state and session.state['driver'] != binding:
            raise ValueError('Changed Astra client/script/turn budget requires a new session')
        session.state['driver'] = binding
        # A crash after atomic edit promotion may precede the driver's receipt update.
        events = {e['turn_id']: e for e in session.state['events']}
        for turn in session.state['turns']:
            if turn['id'] in events:
                event = events[turn['id']]
                if not turn.get('plan') or digest(read(verified(turn['plan']))) != event['plan_sha256']:
                    raise ValueError('Committed event differs from its saved paid plan')
                turn.update(status='applied', event=event)
        session.save()
        reason = 'turn_limit'
        for index in range(maximum_turns):
            if session.state['status'] == 'finished':
                reason = 'model_finished'
                break
            turn_id = f'turn-{index:04d}'
            turns = session.state['turns']
            if index < len(turns) and turns[index]['status'] == 'applied':
                continue
            folder = output / 'turns' / turn_id
            folder.mkdir(parents=True, exist_ok=True)
            if index == len(turns):
                context, images = session.snapshot()
                context.update(turn_index=index, turns_remaining_including_this=maximum_turns-index)
                snapshot = dict(context=context, images=images, tools_sha256=digest(tools_schema))
                path = folder / 'input.json'
                _write(path, snapshot)
                turns.append(dict(id=turn_id, status='request_pending', input=pin(path)))
                session.save()
            turn = turns[index]
            snapshot = read(verified(turn['input']))
            if snapshot['tools_sha256'] != digest(tools_schema):
                raise ValueError('Turn tool schema changed')
            print(json.dumps(dict(stage='astra', turn=index+1, status=turn['status'], product=session.seed['product_id'])), flush=True)
            try:
                if (not any(e['turn_id'] == turn_id for e in session.state['events'])
                        and session.state['current_revision'] != snapshot['context']['current_revision']):
                    raise ValueError('Persisted plan refers to a different current checkpoint')
                if turn.get('plan'):
                    plan = read(verified(turn['plan']))
                else:
                    # Exact persisted snapshot ensures completed Responses replay is identical.
                    plan = client.decide(snapshot['context'], snapshot['images'],
                                         folder / getattr(session, 'request_folder', 'api'), tools_schema=tools_schema)
                    _write(folder / 'plan.json', plan)
                    turn.update(plan=pin(folder / 'plan.json'), status='planned')
                    session.save()
                if (not any(e['turn_id'] == turn_id for e in session.state['events'])
                        and session.state['current_revision'] != snapshot['context']['current_revision']):
                    raise ValueError('Persisted plan refers to a different current checkpoint')
                event = session.apply(plan, turn_id=turn_id, model_decision=isinstance(client, AstraClient))
                turn = session.state['turns'][index]  # A rejected transaction restores a deep state snapshot.
                turn.update(status='applied', event=event)
                session.save()
            except Exception as error:
                turn = session.state['turns'][index]
                turn.update(status='needs_attention', error_type=type(error).__name__, error=str(error))
                session.save()
                reason = 'request_failed_or_uncertain'
                break  # Never create another paid attempt to repair this response implicitly.
        if session.state['status'] == 'finished':
            reason = 'model_finished'
        result = session.deliver(reason)
        # The ledger total (every session of this authorization), as before the bsa.look hooks; bsa.look counts its
        # own session's reservations itself.
        result.update(driver=binding, turns_completed=sum(t['status']=='applied' for t in session.state['turns']),
            paid_calls_used=len(client._budget()['reservations']) if isinstance(client, AstraClient) else 0)
        if reason == 'request_failed_or_uncertain':
            result['status'] = 'needs_attention'
        _write(output / 'report.json', result)
        return result


def add_astra_arguments(parser, *, standalone=False):
    if not standalone:
        parser.add_argument('--with-astra', action='store_true', help='Run canonical Astra editor after base delivery')
        parser.add_argument('--astra-output', type=Path, help='Stable session directory; never put edited bytes over base candidate.glb')
    parser.add_argument('--astra-script', type=Path, help='Offline typed-plan list; no paid inference')
    parser.add_argument('--authorize-paid-astra', action='store_true', help='Explicitly enable bounded paid Astra requests')
    parser.add_argument('--astra-env', type=Path, help='Explicit dotenv file containing Astra credential')
    parser.add_argument('--astra-api-key-env', default='OPENAI_API_KEY')
    parser.add_argument('--astra-budget', type=Path, help='Shared durable authorization ledger for all sessions')
    parser.add_argument('--astra-maximum-calls', type=int, default=0, help='Shared total ceiling, 1..10; failed attempts count')
    parser.add_argument('--astra-max-turns', type=int, default=3)
    parser.add_argument('--astra-model', default='gpt-6-astra')
    parser.add_argument('--astra-reasoning', default='high', choices=['low','medium','high','xhigh','max','ultra'])
    parser.add_argument('--astra-max-output-tokens', type=int, default=12000)


def client_from_args(args, instructions=None):
    """``instructions``: the live client's instructions text (None = the segmented PROMPT)."""
    if args.astra_script:
        if args.authorize_paid_astra or args.astra_maximum_calls or args.astra_budget or args.astra_env:
            raise ValueError('Choose scripted local execution or explicit paid execution')
        return ScriptedClient(args.astra_script)
    if not args.authorize_paid_astra or args.astra_budget is None:
        raise ValueError('Live Astra needs explicit authorization and a shared budget path')
    if args.astra_env:
        from dotenv import dotenv_values
        secret = dotenv_values(args.astra_env).get(args.astra_api_key_env)
    else:
        secret = os.environ.get(args.astra_api_key_env)
    if not secret:
        raise ValueError('No credential in the explicit Astra credential source')
    return AstraClient(secret, args.astra_model, budget_path=args.astra_budget,
        maximum_calls=args.astra_maximum_calls, maximum_output_tokens=args.astra_max_output_tokens,
        reasoning_effort=args.astra_reasoning, instructions=instructions)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-job', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    add_astra_arguments(parser, standalone=True)
    args = parser.parse_args(argv)
    client = client_from_args(args)
    result = run_astra_job(args.base_job, args.output, client=client, maximum_turns=args.astra_max_turns)
    print(json.dumps({k: result[k] for k in ('status','product_id','stop_reason','current_revision','turns_completed','paid_calls_used')}))


if __name__ == '__main__':
    main()
