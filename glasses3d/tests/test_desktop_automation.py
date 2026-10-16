"""Mailbox packets only: these tests never start a Blender/model/job run."""
import base64
import json
from pathlib import Path
from types import SimpleNamespace

import httpx2
import jsonschema
import pytest

from glasses3d.agent import replay as R
from glasses3d.agent.budget import COUNT_FIELDS
from glasses3d.agent.offline import LoopbackOnly, ProductionTransportTripwire
from glasses3d.tests.proofs import desktop_automation as D


def request(instructions='main instructions'):
    return {'model': 'gpt-6-astra', 'reasoning': {'effort': 'xhigh'},
            'instructions': instructions, 'input': [{'role': 'user', 'content': [
                {'type': 'input_text', 'text': 'Current complete request'},
                {'type': 'input_image', 'image_url': 'data:image/png;base64,'+base64.b64encode(b'pngbytes').decode()}]}],
            'tools': [{'type': 'function', 'name': 'scene_summary', 'description': 'Read current objects',
                       'parameters': {'type': 'object', 'properties': {'objects': {'type': 'null'}},
                                      'required': ['objects'], 'additionalProperties': False}, 'strict': True}],
            'tool_choice': 'required'}


def response(packet, **changes):
    return json.dumps({'request_sha256': packet['request_sha256'], 'name': 'scene_summary',
                       'arguments': {'objects': None}, **changes}).encode()


def test_exact_packet_extracts_images_and_separates_contexts(tmp_path):
    mailbox = D.Mailbox(tmp_path/'mailbox')
    body = request();raw = json.dumps(body, indent=3).encode()
    packet, parsed = mailbox.publish(raw)
    folder = Path(packet['directory'])
    assert (folder/'request.json').read_bytes() == raw
    assert parsed == body and packet['request_sha256'] == D.sha(raw)
    assert packet['role'] == 'main'
    displayed = json.loads((folder/'input.json').read_text())
    local = displayed[0]['content'][1]['image_url']['desktop_local_image']
    assert Path(local['path']).read_bytes() == b'pngbytes'
    assert local['sha256'] == D.sha(b'pngbytes')
    assert (folder/'instructions.txt').read_text() == body['instructions']
    assert json.loads((folder/'tools.json').read_text()) == body['tools']
    child, _ = mailbox.publish(json.dumps(request('specialist instructions')).encode())
    again, _ = mailbox.publish(raw)
    assert child['role'] == 'specialist' and child['context'] != packet['context']
    assert again['queue'] == packet['queue'] and again['context'] != packet['context']
    assert all(row['fresh_desktop_task_required'] for row in (packet,child,again))
    assert Path(again['directory']).name == '000002'
    assert len(list((mailbox.root/'images').iterdir())) == 1


def test_repeated_specialist_invocations_require_distinct_fresh_tasks(tmp_path):
    mailbox=D.Mailbox(tmp_path/'mailbox')
    mailbox.publish(json.dumps(request()).encode())
    first=request('same specialist instructions')
    second=request('same specialist instructions')
    second['input'][0]['content'][0]['text']='Second isolated specialist invocation, different source'
    a,_=mailbox.publish(json.dumps(first).encode())
    b,_=mailbox.publish(json.dumps(second).encode())
    assert a['queue']==b['queue']
    assert a['context']!=b['context']
    assert a['fresh_desktop_task_required'] and b['fresh_desktop_task_required']
    assert 'Second isolated' not in (Path(a['directory'])/'input.json').read_text()
    assert 'Second isolated' in (Path(b['directory'])/'input.json').read_text()


@pytest.mark.parametrize('changes,pattern', [
    ({'request_sha256': '0'*64}, 'stale'),
    ({'name': 'execute_python'}, 'absent'),
    ({'arguments': {'objects': 'wrong'}}, 'not of type'),
    ({'arguments': {'objects': None, 'extra': True}}, 'Additional properties'),
    ({'extra': True}, 'exactly'),
])
def test_response_binds_exact_request_and_current_schema(tmp_path, changes, pattern):
    mailbox = D.Mailbox(tmp_path/'mailbox')
    packet, body = mailbox.publish(json.dumps(request()).encode())
    with pytest.raises((ValueError, jsonschema.ValidationError), match=pattern):
        mailbox.response(packet, body, response(packet, **changes))


def test_mock_transport_executes_only_valid_call_and_reports_zero_api_usage(tmp_path):
    mailbox = D.Mailbox(tmp_path/'mailbox')
    mailbox.wait_response = lambda packet, body: mailbox.response(packet, body, response(packet))
    body = request()
    count = {key: value for key, value in body.items() if key in COUNT_FIELDS}
    with LoopbackOnly() as network, ProductionTransportTripwire() as clients:
        with httpx2.Client(transport=httpx2.MockTransport(mailbox)) as client:
            counted = client.post('https://api.openai.com'+R.COUNT_PATH, json=count)
            created = client.post('https://api.openai.com'+R.CREATE_PATH, json=body)
    assert counted.json()['input_tokens'] == 0
    result = created.json()
    assert result['output'][0]['name'] == 'scene_summary'
    assert json.loads(result['output'][0]['arguments']) == {'objects': None}
    assert result['usage'] == D.ZERO_USAGE
    assert not network.attempts and not clients.constructions
    assert not mailbox.transport.violations and not mailbox.transport.unexpected


def test_bad_response_does_not_execute_and_cancel_has_no_fallback(tmp_path):
    mailbox = D.Mailbox(tmp_path/'mailbox', timeout_s=.01, poll_s=.001)
    packet, body = mailbox.publish(json.dumps(request()).encode())
    path = Path(packet['response_path']);path.write_bytes(response(packet, name='missing'))
    with pytest.raises(TimeoutError):
        mailbox.wait_response(packet, body)
    assert json.loads(path.with_name('response-error.json').read_text())['executed'] is False
    assert not mailbox.transport.creates and not mailbox.transport.replies
    (mailbox.root/'CANCEL').touch()
    with pytest.raises(RuntimeError, match='cancelled'):
        mailbox.wait_response(packet, body)


def test_fresh_run_rejects_existing_work_before_any_run(tmp_path):
    root = tmp_path/'job';root.mkdir();(root/'job.json').write_text('{}');(root/'work').mkdir()
    with pytest.raises(ValueError, match='fresh job'):
        D.run_fresh(root, tmp_path/'mailbox')
    assert not (tmp_path/'mailbox').exists()


def test_wrong_model_or_effort_never_publishes_a_packet(tmp_path):
    mailbox = D.Mailbox(tmp_path/'mailbox')
    body = request();body['reasoning']['effort'] = 'max'
    with pytest.raises(ValueError, match='xhigh'):
        mailbox.publish(json.dumps(body).encode())
    assert not mailbox.packets


def test_offline_marker_refuses_production_before_count_or_client(tmp_path,monkeypatch):
    from glasses3d.agent.loop import _check_bound_request
    from glasses3d.agent.approvals import ApprovalRefused
    run=tmp_path/'runs'/'offline';run.mkdir(parents=True)
    (run/'approval-request.json').write_text(json.dumps({'offline_only':True,'request':{}}))
    spec=SimpleNamespace(job_dir=tmp_path,offline_handler=None,offline_api_key=None)
    monkeypatch.delenv('G3D_OFFLINE',raising=False)
    with LoopbackOnly() as network,ProductionTransportTripwire() as clients:
        with pytest.raises(ApprovalRefused,match='cannot authorize a production run'):
            _check_bound_request(run,spec)
    assert not network.attempts and not clients.constructions
    monkeypatch.setenv('G3D_OFFLINE','1')
    with pytest.raises(ApprovalRefused,match='cannot authorize a production run'):
        _check_bound_request(run,spec)
