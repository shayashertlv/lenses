"""File-mailbox adapter for a fresh, explicitly started $0 desktop-agent proof.

This module does nothing on import. It never calls a provider transport.
``run_fresh`` uses the ordinary job runner, tools, stage
isolation, approval binding and checks over MockTransport; the external desktop
agent supplies each function call through files. Desktop subscription usage is
not measured here. Actual API usage and API spend are zero. Local estimated
tokens and simulated usage at the frozen real tariff exercise the unchanged
money guard, pressure checks and conversation renewal. These are simulations,
not measured desktop usage or a provider invoice; unknown reasoning is excluded.

For EVERY pending packet, the desktop orchestrator uses a fresh task with no
earlier project/conversation history. Instruction-hash queues distinguish main
from specialist requests, but are never permission to reuse a desktop task:
repeated specialist invocations can have identical instructions and independent
SDK histories. Read packet.json, instructions.txt,
tools.json and input.json, inspect the extracted image files, then atomically
write response.json with exactly {request_sha256, name, arguments}, or
{request_sha256, text} when packet.json requests a written renewal summary. The hash
comes from packet.json; name and arguments must match the current tool schema.
Input is the complete actual SDK request history, not an orchestrator summary.
The original request bytes remain in request.json for exact auditing.

Run only when the owner has authorized the full desktop proof and prerequisite
work is finished. Preparation and packet unit tests do not start a job.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import re
import time

import jsonschema

from glasses3d.agent import job_run, replay as R
from glasses3d.agent.budget import COUNT_FIELDS
from glasses3d.agent.offline import LoopbackOnly, ProductionTransportTripwire, disable_sdk_tracing
from glasses3d.agent.pricing import MODEL_MAX_OUTPUT_TOKENS, Tariff
from glasses3d.agent.procs import write_json_atomic
from glasses3d.agent.receipts import StopRequested
from glasses3d.tests.proofs.offline_job_approval import OFFLINE_NOTE, prepare_offline_request as prepare_fixture

SCHEMA = 'g3d.desktop_mailbox/2'
_DATA = re.compile(r'^data:(image/[a-zA-Z0-9.+-]+);base64,(.*)$', re.DOTALL)
_EXT = {'image/png': '.png', 'image/jpeg': '.jpg', 'image/webp': '.webp',
        'image/gif': '.gif', 'image/avif': '.avif'}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def strict_json(raw):
    def pairs(rows):
        out = {}
        for key, value in rows:
            if key in out:
                raise ValueError(f'duplicate JSON key: {key}')
            out[key] = value
        return out
    def constant(value):
        raise ValueError(f'nonfinite JSON number: {value}')
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def count_body(body):
    return {key: value for key, value in body.items() if key in COUNT_FIELDS}


class SimulatedUsage:
    """Explicit local cache/output assumptions; the real guard prices these usage blocks."""

    policy = {
        'simulated': True,
        'input_tokens': 'replay.estimate_tokens: ceil(JSON text characters / 4) + 1,500 per image occurrence',
        'output_tokens': 'replay.estimate_tokens of the visible response output JSON; unknown reasoning excluded',
        'reasoning_tokens': 'simulated zero: desktop subscription reasoning usage is unavailable',
        'cache': ('The entire previous request count is cached only for an exact append-only input prefix '
                  'with unchanged request settings (apart from tool_choice). Fresh or changed histories '
                  'write the whole cache once. This is a deterministic simulation, not measured cache usage.'),
        'money': 'The ordinary guard uses its frozen tariff and the owner-bound settled cutoff.',
    }

    def __init__(self):
        self.tariff = Tariff.frozen()
        self.prefixes = {}
        self.records = []

    @staticmethod
    def _digest(value):
        return sha(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode())

    def settle(self, body, reply, *, request_sha256=None):
        counted_body = count_body(body)
        counted = R.estimate_tokens(counted_body)
        output = R.estimate_tokens({'output': reply.output})
        if output > body.get('max_output_tokens', MODEL_MAX_OUTPUT_TOKENS):
            raise ValueError('simulated response output exceeds the request output ceiling')
        settings = {k: v for k, v in counted_body.items() if k not in ('input', 'tool_choice')}
        key = self._digest(settings)
        items = body.get('input', [])
        items = items if isinstance(items, list) else [items]
        prefix = [self._digest(item) for item in items]
        previous, previous_count = self.prefixes.get(key, (None, 0))
        hit = previous is not None and prefix[:len(previous)] == previous
        cached = min(previous_count, counted) if hit else 0
        usage = {'input_tokens': counted, 'output_tokens': output, 'total_tokens': counted + output,
                 'input_tokens_details': {'cached_tokens': cached, 'cache_write_tokens': counted - cached},
                 'output_tokens_details': {'reasoning_tokens': 0}}
        record = {'simulated': True, 'request_sha256': request_sha256 or self._digest(body),
                  'simulated_usage': usage, 'simulated_cache_rebuild': not hit,
                  'simulated_cost_micro_usd': self.tariff.settle_micro(usage),
                  'simulated_tariff': self.tariff.to_dict()}
        self.prefixes[key] = (prefix, counted)
        self.records.append(record)
        return usage, record


class Mailbox:
    """Only in-process MockTransport requests reach this callable; no HTTP server."""

    def __init__(self, root, *, timeout_s=3600, poll_s=.25, stop_file=None):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=False)
        self.timeout_s, self.poll_s = timeout_s, poll_s
        self.stop_file = Path(stop_file) if stop_file is not None else None
        self.transport = R.ScriptedOpenAI([], count_tokens=R.estimate_tokens)
        self.simulation = SimulatedUsage()
        self.main_instructions_sha256 = None
        self.context_counts = {}
        self.packets = []

    def _extract(self, value, images):
        if isinstance(value, str) and (match := _DATA.fullmatch(value)):
            mime, encoded = match.groups()
            if mime not in _EXT:
                raise ValueError(f'unsupported inline image MIME: {mime}')
            raw = base64.b64decode(encoded, validate=True)
            digest = sha(raw)
            target = self.root/'images'/(digest+_EXT[mime])
            target.parent.mkdir(exist_ok=True)
            if target.exists() and target.read_bytes() != raw:
                raise ValueError('image digest file changed')
            if not target.exists():
                target.write_bytes(raw)
            record = {'path': str(target), 'mime': mime, 'sha256': digest, 'bytes': len(raw)}
            images[digest] = record
            return {'desktop_local_image': record}
        if isinstance(value, list):
            return [self._extract(v, images) for v in value]
        if isinstance(value, dict):
            return {k: self._extract(v, images) for k, v in value.items()}
        return value

    def publish(self, request_bytes):
        """Write a request packet only; no waiting, model call or tool execution."""
        body = strict_json(request_bytes)
        if body.get('model') != R.MODEL or body.get('reasoning', {}).get('effort') != 'xhigh':
            raise ValueError('desktop proof requires gpt-6-astra at xhigh')
        instructions = body.get('instructions')
        if not isinstance(instructions, str) or not instructions:
            raise ValueError('request needs its complete actual instructions')
        digest = sha(instructions.encode())
        if self.main_instructions_sha256 is None:
            self.main_instructions_sha256 = digest
        role = 'main' if digest == self.main_instructions_sha256 else 'specialist'
        queue = role+'-'+digest
        index = self.context_counts.get(queue, 0)+1
        self.context_counts[queue] = index
        context = queue+f'-packet-{index:06d}'
        directory = self.root/'contexts'/queue/f'{index:06d}'
        directory.mkdir(parents=True, exist_ok=False)
        images = {}
        display_input = self._extract(body.get('input'), images)
        (directory/'request.json').write_bytes(request_bytes)
        (directory/'instructions.txt').write_text(instructions, encoding='utf-8')
        write_json_atomic(directory/'tools.json', body.get('tools', []))
        write_json_atomic(directory/'input.json', display_input)
        record = {'schema': SCHEMA, 'request_sha256': sha(request_bytes),
                  'sequence': len(self.packets)+1, 'context': context, 'queue': queue, 'role': role,
                  'fresh_desktop_task_required': True,
                  'instructions_sha256': digest, 'directory': str(directory),
                  'model': R.MODEL, 'effort': 'xhigh', 'api_calls': 0, 'api_cost_usd': 0,
                  'images': list(images.values()), 'response_path': str(directory/'response.json'),
                  'response_shape': ({'request_sha256': sha(request_bytes), 'text': 'WRITTEN_STATE_SUMMARY'}
                                     if body.get('tool_choice') == 'none' else
                                     {'request_sha256': sha(request_bytes), 'name': 'CURRENT_TOOL_NAME',
                                      'arguments': {'CURRENT_SCHEMA_FIELDS': 'values'}}),
                  'simulated_counted_input_tokens': R.estimate_tokens(count_body(body)),
                  'simulated_usage_policy': self.simulation.policy,
                  'history': 'Exact complete SDK input; no external conversation history is authorized.'}
        write_json_atomic(directory/'packet.json', record)
        self.packets.append(record)
        write_json_atomic(self.root/'pending.json', record)
        print('DESKTOP_PACKET '+str(directory/'packet.json'), flush=True)
        return record, body

    @staticmethod
    def response(record, body, raw):
        """Bind one strict tool call or requested summary to this exact pending request."""
        response = strict_json(raw)
        summary = body.get('tool_choice') == 'none'
        expected = {'request_sha256', 'text'} if summary else {'request_sha256', 'name', 'arguments'}
        if not isinstance(response, dict) or set(response) != expected:
            raise ValueError('response requires exactly ' + ', '.join(sorted(expected)))
        if response['request_sha256'] != record['request_sha256']:
            raise ValueError('stale response: request_sha256 differs')
        if summary:
            if not isinstance(response['text'], str) or not response['text'].strip():
                raise ValueError('summary text must be nonempty text')
            reply = R.message(response['text'])
            if R.estimate_tokens({'output': reply.output}) > body.get('max_output_tokens', MODEL_MAX_OUTPUT_TOKENS):
                raise ValueError('simulated response output exceeds the request output ceiling')
            return reply
        if not isinstance(response['name'], str) or not isinstance(response['arguments'], dict):
            raise ValueError('name must be text and arguments must be an object')
        tools = {t['name']: t for t in body.get('tools', []) if t.get('type') == 'function'}
        if response['name'] not in tools:
            raise ValueError('response names a tool absent from this exact request')
        choice = body.get('tool_choice')
        if isinstance(choice, dict) and choice.get('type') == 'function' and choice.get('name') != response['name']:
            raise ValueError('response differs from the required tool_choice')
        jsonschema.Draft202012Validator(tools[response['name']]['parameters']).validate(response['arguments'])
        reply = R.tool_call(response['name'], response['arguments'])
        if R.estimate_tokens({'output': reply.output}) > body.get('max_output_tokens', MODEL_MAX_OUTPUT_TOKENS):
            raise ValueError('simulated response output exceeds the request output ceiling')
        return reply

    async def wait_response(self, record, body):
        path = Path(record['response_path'])
        deadline = time.monotonic()+self.timeout_s
        last_invalid = None
        while time.monotonic() < deadline:
            if self.stop_file is not None and self.stop_file.exists():
                raise StopRequested('owner stop requested while the desktop mailbox was waiting')
            if (self.root/'CANCEL').exists():
                raise RuntimeError('desktop mailbox cancelled; no response executed')
            if path.is_file():
                raw = await asyncio.to_thread(path.read_bytes)
                try:
                    reply = self.response(record, body, raw)
                except (ValueError, jsonschema.ValidationError) as error:
                    if sha(raw) != last_invalid:
                        last_invalid = sha(raw)
                        write_json_atomic(path.with_name('response-error.json'),
                                          {'error': str(error), 'response_sha256': last_invalid,
                                           'executed': False, 'action': 'Replace response.json atomically.'})
                else:
                    write_json_atomic(path.with_name('response-accepted.json'),
                                      {'response_sha256': sha(raw), 'request_sha256': record['request_sha256']})
                    return reply
            await asyncio.sleep(self.poll_s)
        raise TimeoutError('desktop response was not supplied; no inference fallback is allowed')

    async def __call__(self, request):
        if request.method == 'POST' and request.url.host == 'api.openai.com' and request.url.path == R.CREATE_PATH:
            record, body = await asyncio.to_thread(self.publish, request.content)
            reply = await self.wait_response(record, body)
            reply.usage, simulated = self.simulation.settle(body, reply, request_sha256=record['request_sha256'])
            write_json_atomic(Path(record['directory'])/'simulated-usage.json', simulated)
            self.transport.replies.append(reply)
        return self.transport.handle(request)


def prepare_offline_request(root, run_id, mailbox, *, settings=None, cap=None):
    """Prepare the shared offline guard fixture; request counts are locally estimated."""
    if not isinstance(mailbox, Mailbox):
        raise ValueError('desktop proof requires the file mailbox')
    settings = settings or replace(job_run.settings_default(), effort='xhigh')
    if settings.effort != 'xhigh':
        raise ValueError('desktop proof requires gpt-6-astra at xhigh')
    spec, scene = prepare_fixture(root, run_id, mailbox, settings=settings, cap=cap)
    mailbox.main_instructions_sha256 = sha(spec.instructions.encode())
    return spec, scene


def run_fresh(root, mailbox_path, *, run_id='desktop-offline', timeout_s=3600, settings=None, cap=None):
    """Explicit runner only: fresh photo job, empty scene, real tools, local mailbox."""
    root = Path(root).resolve(strict=True)
    if not (root/'job.json').is_file():
        raise ValueError('create a fresh photo job with the ordinary jobs.create_job intake first')
    if any((root/name).exists() for name in ('runs', 'work', 'deliveries', 'owner_edit.json')):
        raise ValueError('desktop proof requires a fresh job with no previous run, scene or delivery')
    if not re.fullmatch(r'[A-Za-z0-9_-]+', run_id):
        raise ValueError('run_id must be a simple directory name')
    from glasses3d.agent.loop import STOP_FILE
    mailbox = Mailbox(mailbox_path, timeout_s=timeout_s, stop_file=root/STOP_FILE)
    old_offline = os.environ.get('G3D_OFFLINE')
    os.environ['G3D_OFFLINE'] = '1'
    try:
        disable_sdk_tracing()
        with LoopbackOnly() as network, ProductionTransportTripwire() as clients:
            _, scene = prepare_offline_request(root, run_id, mailbox, settings=settings, cap=cap)
            result = asyncio.run(job_run.execute_run(root, run_id, offline_handler=mailbox, scene=scene))
        if mailbox.transport.violations or mailbox.transport.unexpected:
            raise AssertionError((mailbox.transport.violations, mailbox.transport.unexpected))
        if network.attempts or clients.constructions:
            raise AssertionError((network.attempts, clients.constructions))
        report = {'schema': SCHEMA, 'job': str(root), 'run_id': run_id, 'api_calls': 0, 'api_cost_usd': 0,
                  'desktop_usage': 'not measured; supplied by the user desktop subscription',
                  'mock_requests': len(mailbox.transport.creates), 'packets': mailbox.packets,
                  'network_attempts': network.attempts, 'production_transports': clients.constructions,
                  'simulated': {'policy': mailbox.simulation.policy, 'requests': mailbox.simulation.records,
                                'run_result': result,
                                'note': 'All token, usage, money, hold and runway values in this object are simulated.'},
                  'note': OFFLINE_NOTE}
        write_json_atomic(mailbox.root/'report.json', report)
        write_json_atomic(mailbox.root/'pending.json', {'schema': SCHEMA, 'status': 'finished'})
        return report
    finally:
        if old_offline is None:
            os.environ.pop('G3D_OFFLINE', None)
        else:
            os.environ['G3D_OFFLINE'] = old_offline


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job', required=True)
    parser.add_argument('--mailbox', required=True)
    parser.add_argument('--run-id', default='desktop-offline')
    parser.add_argument('--response-timeout', type=float, default=3600)
    parser.add_argument('--cap', help='Simulated owner settled cutoff; default $20.')
    parser.add_argument('--settled-only', action='store_true', default=True,
                        help='Compatibility flag; all new offline runs use settled spending.')
    parser.add_argument('--start-authorized-offline-run', action='store_true')
    args = parser.parse_args()
    if not args.start_authorized_offline_run:
        parser.error('the explicit --start-authorized-offline-run flag is required; no run started')
    settings = replace(job_run.settings_default(), effort='xhigh', settled_only=True,
                       settled_stop_usd=args.cap if args.cap is not None else '20')
    run_fresh(args.job, args.mailbox, run_id=args.run_id, timeout_s=args.response_timeout,
              settings=settings, cap=args.cap)
