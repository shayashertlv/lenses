"""Synthetic, production-unspendable approval for an explicitly offline proof.

No estimator or owner-approval writer is called. The ordinary request binding,
reserve, ledger and execution guards remain active. This helper only prepares
files; it never starts a job or sends a request.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re

from glasses3d.agent import approvals as A, job_run
from glasses3d.agent.budget import create_json_exclusive, now_iso
from glasses3d.agent.loop import approval_settings, tool_registry_sha256

OFFLINE_NOTE = ('OFFLINE PROOF ONLY. $0 API usage; no estimate was requested. '
                'Synthetic approval exercises the unchanged guard over MockTransport; '
                'it is not owner authorization for any paid run.')


def prepare_offline_request(root, run_id, offline_handler, *, settings=None):
    """Prepare one exact offline fixture, requiring a real mock handler and mode."""
    if os.environ.get('G3D_OFFLINE') != '1' or not callable(offline_handler):
        raise ValueError('synthetic approval requires G3D_OFFLINE=1 and a callable offline handler')
    if not isinstance(run_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', run_id):
        raise ValueError('run_id must be a simple directory name')
    root = Path(root).resolve(strict=True)
    spec, scene = job_run.prepare_run(root, run_id, settings=settings, write_request=False,
                                      offline_handler=offline_handler)
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
        'suggested_cap_usd': '23.000000', 'requested_cap_usd': '23.000000',
        'requested_cap_micro_usd': 23_000_000,
        'effort': spec.settings.effort, 'max_output_tokens': spec.settings.max_output_tokens,
        'max_turns': spec.settings.max_turns, 'request_sha256': spec.current_request_sha256, 'request': request}
    create_json_exclusive(run/'approval-request.json', request_doc)
    create_json_exclusive(run/'approval.json', {'schema': A.APPROVAL_SCHEMA, 'job_id': root.name,
        'run_id': run_id, 'approved': now_iso(), 'request_sha256': spec.current_request_sha256,
        'approval_request_sha256': A.file_sha256(run/'approval-request.json'),
        'max_usd': '23.000000', 'max_micro_usd': 23_000_000, 'owner_words': OFFLINE_NOTE,
        'authorized_by': 'offline_fixture', 'recorded_by': 'synthetic offline fixture; not the owner'})
    return spec, scene
