"""Synthetic, production-unspendable approval for an explicitly offline proof.

No estimator or owner-approval writer is called. The ordinary request binding,
settled cutoff, ledger and execution guards remain active. This helper only prepares
files; it never starts a job or sends a request.
"""
from __future__ import annotations

import hashlib
from dataclasses import replace
import json
import os
from pathlib import Path
import re

from glasses3d.agent import approvals as A, job_run
from glasses3d.agent.budget import cap_to_micro, create_json_exclusive, now_iso
from glasses3d.agent.loop import approval_settings, tool_registry_sha256
from glasses3d.agent.pricing import usd_text
from glasses3d.agent.procs import write_json_atomic

OFFLINE_NOTE = ('OFFLINE PROOF ONLY. $0 API usage. '
                'Synthetic approval exercises the unchanged guard over MockTransport; '
                'it is not owner authorization for any paid run.')


def approve_offline_request(run_dir, offline_handler, *, cap):
    """Mark an existing lab/job proof request before writing unspendable authority.

    Used by historical proofs that intentionally exercise request preparation or
    missing-approval refusal first. Never rewrites an approval or consumed run.
    """
    if os.environ.get('G3D_OFFLINE') != '1' or not callable(offline_handler):
        raise ValueError('synthetic approval requires G3D_OFFLINE=1 and a callable offline handler')
    run = Path(run_dir).resolve(strict=True)
    if (run / 'approval.json').exists() or A.is_consumed(run):
        raise ValueError('offline fixture cannot replace an approval or consumed run')
    path = run / 'approval-request.json'
    request = json.loads(path.read_text(encoding='utf-8'))
    if (request.get('schema') != A.APPROVAL_REQUEST_SCHEMA or request.get('run_id') != run.name
            or request.get('request_sha256') != A.request_sha256(request['request'])):
        raise ValueError('offline fixture requires an intact bound request')
    cap_micro = cap_to_micro(cap)
    settings = request['request']['settings']
    if (cap_micro <= 0 or settings.get('settled_only') is not True
            or cap_to_micro(settings.get('settled_stop_usd')) != cap_micro
            or settings.get('fit_output_to_budget', False)
            or request.get('requested_cap_micro_usd') != cap_micro):
        raise ValueError('offline fixture requires settled-only mode and the exact positive cutoff')
    request['offline_only'] = True
    request['estimate'] = {'kind': 'offline_mock_fixture', 'api_cost_usd': 0, 'note': OFFLINE_NOTE}
    write_json_atomic(path, request)
    document = {'schema': A.APPROVAL_SCHEMA, 'job_id': request['job_id'],
        'run_id': run.name, 'approved': now_iso(), 'request_sha256': request['request_sha256'],
        'approval_request_sha256': A.file_sha256(path), 'max_usd': usd_text(cap_micro),
        'max_micro_usd': cap_micro, 'owner_words': OFFLINE_NOTE,
        'authorized_by': 'offline_fixture', 'recorded_by': 'synthetic offline fixture; not the owner'}
    create_json_exclusive(run / 'approval.json', document)
    return document


def prepare_offline_request(root, run_id, offline_handler, *, settings=None, cap=None):
    """Prepare one exact offline fixture, requiring a real mock handler and mode."""
    if os.environ.get('G3D_OFFLINE') != '1' or not callable(offline_handler):
        raise ValueError('synthetic approval requires G3D_OFFLINE=1 and a callable offline handler')
    if not isinstance(run_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', run_id):
        raise ValueError('run_id must be a simple directory name')
    root = Path(root).resolve(strict=True)
    if settings is None:
        settings = job_run.settings_default()
        if cap is not None:
            settings = replace(settings, settled_stop_usd=cap)
    if cap is None:
        cap = settings.settled_stop_usd
    cap_micro = cap_to_micro(cap)
    cap_text = usd_text(cap_micro)
    spec, scene = job_run.prepare_run(root, run_id, settings=settings, write_request=False,
                                      offline_handler=offline_handler, cap=cap)
    instructions_sha = hashlib.sha256(spec.instructions.encode()).hexdigest()
    request = A.build_request(root, instructions_sha256=instructions_sha,
        tool_registry_sha256=tool_registry_sha256(spec.tools, spec.settings),
        settings=approval_settings(spec.settings, spec.stage_contracts, session_binding=spec.session_binding),
        unsent_owner_notes=spec.owner_notes,
        run_inputs=A.run_input_binding(root, spec.brief, spec.photos))
    if A.request_sha256(request) != spec.current_request_sha256:
        raise ValueError('offline approval fixture differs from actual job request')
    # The marker is bound by the approval-request SHA. The normal run preflight
    # refuses it before clients/counts/consumption unless it has a mock handler,
    # the explicit offline mode, and the dedicated offline credential sentinel.
    run = root/'runs'/run_id
    run.mkdir(parents=True, exist_ok=False)
    request_doc = {'schema': A.APPROVAL_REQUEST_SCHEMA, 'job_id': root.name, 'run_id': run_id,
        'created': now_iso(), 'offline_only': True,
        'estimate': {'kind': 'offline_mock_fixture', 'api_cost_usd': 0, 'note': OFFLINE_NOTE},
        'suggested_cap_usd': cap_text, 'requested_cap_usd': cap_text,
        'requested_cap_micro_usd': cap_micro,
        'effort': spec.settings.effort, 'max_output_tokens': spec.settings.max_output_tokens,
        'max_turns': spec.settings.max_turns, 'request_sha256': spec.current_request_sha256, 'request': request}
    create_json_exclusive(run/'approval-request.json', request_doc)
    create_json_exclusive(run/'approval.json', {'schema': A.APPROVAL_SCHEMA, 'job_id': root.name,
        'run_id': run_id, 'approved': now_iso(), 'request_sha256': spec.current_request_sha256,
        'approval_request_sha256': A.file_sha256(run/'approval-request.json'),
        'max_usd': cap_text, 'max_micro_usd': cap_micro, 'owner_words': OFFLINE_NOTE,
        'authorized_by': 'offline_fixture', 'recorded_by': 'synthetic offline fixture; not the owner'})
    return spec, scene
