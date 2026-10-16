"""File-mailbox adapter for a fresh, explicitly started $0 desktop-agent proof.

This module does nothing on import. It never calls an estimate function or a
provider transport. ``run_fresh`` uses the ordinary job runner, tools, stage
isolation, approval binding and checks over MockTransport; the external desktop
agent supplies each function call through files. Desktop subscription usage is
not measured here. API usage and API spend are zero.

For EVERY pending packet, the desktop orchestrator uses a fresh task with no
earlier project/conversation history. Instruction-hash queues distinguish main
from specialist requests, but are never permission to reuse a desktop task:
repeated specialist invocations can have identical instructions and independent
SDK histories. Read packet.json, instructions.txt,
tools.json and input.json, inspect the extracted image files, then atomically
write response.json with exactly {request_sha256, name, arguments}. The hash
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
from glasses3d.agent.offline import LoopbackOnly, ProductionTransportTripwire, disable_sdk_tracing
from glasses3d.agent.procs import write_json_atomic
from glasses3d.tests.proofs.offline_job_approval import OFFLINE_NOTE, prepare_offline_request as prepare_fixture

SCHEMA = 'g3d.desktop_mailbox/1'
ZERO_USAGE = {'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0,
              'input_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0},
              'output_tokens_details': {'reasoning_tokens': 0}}
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


class Mailbox:
    """Only in-process MockTransport requests reach this callable; no HTTP server."""

    def __init__(self, root, *, timeout_s=3600, poll_s=.25):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=False)
        self.timeout_s, self.poll_s = timeout_s, poll_s
        self.transport = R.ScriptedOpenAI([], count_tokens=lambda body: 0)
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
                  'response_shape': {'request_sha256': sha(request_bytes), 'name': 'CURRENT_TOOL_NAME',
                                     'arguments': {'CURRENT_SCHEMA_FIELDS': 'values'}},
                  'history': 'Exact complete SDK input; no external conversation history is authorized.'}
        write_json_atomic(directory/'packet.json', record)
        self.packets.append(record)
        write_json_atomic(self.root/'pending.json', record)
        print('DESKTOP_PACKET '+str(directory/'packet.json'), flush=True)
        return record, body

    @staticmethod
    def response(record, body, raw):
        """Bind one strict tool call to this exact pending request before execution."""
        response = strict_json(raw)
        if not isinstance(response, dict) or set(response) != {'request_sha256', 'name', 'arguments'}:
            raise ValueError('response requires exactly request_sha256, name and arguments')
        if response['request_sha256'] != record['request_sha256']:
            raise ValueError('stale response: request_sha256 differs')
        if not isinstance(response['name'], str) or not isinstance(response['arguments'], dict):
            raise ValueError('name must be text and arguments must be an object')
        tools = {t['name']: t for t in body.get('tools', []) if t.get('type') == 'function'}
        if response['name'] not in tools:
            raise ValueError('response names a tool absent from this exact request')
        choice = body.get('tool_choice')
        if isinstance(choice, dict) and choice.get('type') == 'function' and choice.get('name') != response['name']:
            raise ValueError('response differs from the required tool_choice')
        jsonschema.Draft202012Validator(tools[response['name']]['parameters']).validate(response['arguments'])
        return R.tool_call(response['name'], response['arguments'], usage=dict(ZERO_USAGE), counted=0)

    def wait_response(self, record, body):
        path = Path(record['response_path'])
        deadline = time.monotonic()+self.timeout_s
        last_invalid = None
        while time.monotonic() < deadline:
            if (self.root/'CANCEL').exists():
                raise RuntimeError('desktop mailbox cancelled; no response executed')
            if path.is_file():
                raw = path.read_bytes()
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
            time.sleep(self.poll_s)
        raise TimeoutError('desktop response was not supplied; no inference fallback is allowed')

    def __call__(self, request):
        if request.method == 'POST' and request.url.host == 'api.openai.com' and request.url.path == R.CREATE_PATH:
            record, body = self.publish(request.content)
            self.transport.replies.append(self.wait_response(record, body))
        return self.transport.handle(request)


def prepare_offline_request(root, run_id, mailbox):
    """No estimate: prepare the shared offline guard fixture for this mailbox."""
    if not isinstance(mailbox, Mailbox):
        raise ValueError('desktop proof requires the file mailbox')
    settings = replace(job_run.settings_default(), effort='xhigh')
    spec, scene = prepare_fixture(root, run_id, mailbox, settings=settings)
    mailbox.main_instructions_sha256 = sha(spec.instructions.encode())
    return spec, scene


def run_fresh(root, mailbox_path, *, run_id='desktop-offline', timeout_s=3600):
    """Explicit runner only: fresh photo job, empty scene, real tools, local mailbox."""
    root = Path(root).resolve(strict=True)
    if not (root/'job.json').is_file():
        raise ValueError('create a fresh photo job with the ordinary jobs.create_job intake first')
    if any((root/name).exists() for name in ('runs', 'work', 'deliveries', 'owner_edit.json')):
        raise ValueError('desktop proof requires a fresh job with no previous run, scene or delivery')
    if not re.fullmatch(r'[A-Za-z0-9_-]+', run_id):
        raise ValueError('run_id must be a simple directory name')
    mailbox = Mailbox(mailbox_path, timeout_s=timeout_s)
    old_offline = os.environ.get('G3D_OFFLINE')
    os.environ['G3D_OFFLINE'] = '1'
    try:
        disable_sdk_tracing()
        with LoopbackOnly() as network, ProductionTransportTripwire() as clients:
            _, scene = prepare_offline_request(root, run_id, mailbox)
            result = asyncio.run(job_run.execute_run(root, run_id, offline_handler=mailbox, scene=scene))
        if mailbox.transport.violations or mailbox.transport.unexpected:
            raise AssertionError((mailbox.transport.violations, mailbox.transport.unexpected))
        if network.attempts or clients.constructions:
            raise AssertionError((network.attempts, clients.constructions))
        budget=result['result']['budget']
        if any(budget[key]!=0 for key in ('settled_micro_usd','held_micro_usd','unknown_micro_usd')):
            raise AssertionError('offline result did not prove zero settled, held and unknown API spend')
        report = {'schema': SCHEMA, 'job': str(root), 'run_id': run_id, 'api_calls': 0, 'api_cost_usd': 0,
                  'desktop_usage': 'not measured; supplied by the user desktop subscription',
                  'mock_requests': len(mailbox.transport.creates), 'packets': mailbox.packets,
                  'network_attempts': network.attempts, 'production_transports': clients.constructions,
                  'result': result, 'note': OFFLINE_NOTE}
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
    parser.add_argument('--start-authorized-offline-run', action='store_true')
    args = parser.parse_args()
    if not args.start_authorized_offline_run:
        parser.error('the explicit --start-authorized-offline-run flag is required; no run started')
    run_fresh(args.job, args.mailbox, run_id=args.run_id, timeout_s=args.response_timeout)
