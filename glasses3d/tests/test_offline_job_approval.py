"""Proof approvals cannot estimate, authorize production, or consume a run."""
from dataclasses import replace
import json

import pytest

from glasses3d.agent import approvals as A, job_run, jobs, replay
from glasses3d.agent.loop import _check_bound_request
from glasses3d.agent.offline import LoopbackOnly, ProductionTransportTripwire
from glasses3d.tests.agent.test_jobs import setup_inputs
from glasses3d.tests.proofs.offline_job_approval import prepare_offline_request


def test_actual_prepare_fixture_never_estimates_and_production_cannot_consume_it(tmp_path, monkeypatch):
    args, _ = setup_inputs(tmp_path)
    root = jobs.create_job(**args)
    def forbidden(*args, **kwargs):
        raise AssertionError('proof preparation must not request an estimate or write owner approval')
    for name in ('initial_context_tokens', 'estimate_g3d', 'smoke_estimate'):
        monkeypatch.setattr(job_run.E, name, forbidden)
    monkeypatch.setattr(A, 'write_approval_request', forbidden)
    monkeypatch.setattr(A, 'write_approval', forbidden)
    monkeypatch.setenv('G3D_OFFLINE', '1')
    transport = replay.ScriptedOpenAI([])
    with LoopbackOnly() as network, ProductionTransportTripwire() as clients:
        spec, scene = prepare_offline_request(root, 'proof', transport.handle)
        run = root/'runs'/'proof'
        request = json.loads((run/'approval-request.json').read_text())
        approval = json.loads((run/'approval.json').read_text())
        assert request['offline_only'] is True
        assert approval['authorized_by'] == 'offline_fixture'
        assert request['requested_cap_micro_usd'] == approval['max_micro_usd'] == 20_000_000
        assert request['request']['settings']['settled_only'] is True
        assert request['request']['settings']['settled_stop_usd'] == '20.000000'
        assert approval['approval_request_sha256'] == A.file_sha256(run/'approval-request.json')
        _check_bound_request(run, spec)
        A.check_approval(run, current_request_sha256=spec.current_request_sha256)
        with pytest.raises(A.ApprovalRefused, match='cannot authorize a production run'):
            _check_bound_request(run, replace(spec, offline_handler=None, offline_api_key=None))
        assert scene.scripted and not A.is_consumed(run)
    assert not network.attempts and not clients.constructions
    assert not transport.creates and not transport.counts


@pytest.mark.parametrize('mode,handler', [('0', lambda request: None), ('1', None)])
def test_helper_refuses_without_explicit_offline_mode_and_callable(tmp_path, monkeypatch, mode, handler):
    monkeypatch.setenv('G3D_OFFLINE', mode)
    with pytest.raises(ValueError, match='requires G3D_OFFLINE'):
        prepare_offline_request(tmp_path, 'proof', handler)
    assert not (tmp_path/'runs').exists()
