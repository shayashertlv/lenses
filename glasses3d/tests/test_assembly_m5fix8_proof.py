"""The copy proof must detect original mutations and never read excluded launch/secret files."""
import json
import copy
from pathlib import Path

import pytest

from glasses3d.tests.proofs import assembly_m5fix8 as P


def test_inventory_covers_models_photos_checkpoints_and_nested_stage(tmp_path):
    for name in ['job.json', 'deliveries/run/model.glb', 'inputs/image.png',
                 'checkpoints/c0001.blend', 'work/temple_stage/stage/work/temple_lab/state.json']:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(name.encode())
    result = P.inventory(tmp_path)
    assert result['count'] == 5
    assert result['files']['checkpoints/c0001.blend']['sha256'] == P.sha256(tmp_path/'checkpoints/c0001.blend')
    assert P.verify_inventory(result)['status'] == 'passed'


@pytest.mark.parametrize('change', ['overwrite', 'delete', 'add'])
def test_inventory_detects_original_mutations(tmp_path, change):
    path = tmp_path / 'original.glb'
    path.write_bytes(b'original')
    baseline = P.inventory(tmp_path)
    if change == 'overwrite':
        path.write_bytes(b'modified')
    elif change == 'delete':
        path.unlink()
    else:
        (tmp_path/'extra.json').write_text('{}')
    with pytest.raises(ValueError, match='protected original assets changed'):
        P.verify_inventory(baseline)


def test_excluded_files_are_never_opened_and_never_copied(tmp_path, monkeypatch):
    source, target = tmp_path/'source', tmp_path/'copy'
    source.mkdir()
    for name in ['.env', '.env.local', 'runner-status.spec.json', 'private.key', 'credentials.json']:
        (source/name).write_text('not for reading')
    (source/'model.glb').write_bytes(b'model')
    original = Path.open

    def safe_open(path, *args, **kwargs):
        if P.excluded_secret(path):
            raise AssertionError('attempted credential read')
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', safe_open)
    result = P.inventory(source)
    assert result['count'] == 1 and len(result['excluded_without_reading']) == 5
    copied = P.copy_package(source, target)
    assert copied['source_unchanged']
    assert sorted(p.name for p in target.iterdir()) == ['model.glb']


@pytest.mark.parametrize('which', ['same', 'child', 'parent', 'existing'])
def test_copy_refuses_original_overlap_and_existing_destination(tmp_path, which):
    source = tmp_path/'original'/'stage'
    source.mkdir(parents=True)
    (source/'state.json').write_text(json.dumps({'revision': 5}))
    destination = {'same': source, 'child': source/'child', 'parent': source.parent,
                   'existing': tmp_path/'existing'}[which]
    if which == 'existing':
        destination.mkdir()
    with pytest.raises(ValueError):
        P.copy_package(source, destination)
    assert json.loads((source/'state.json').read_text()) == {'revision': 5}


def test_copy_cannot_create_another_stage_inside_protected_job(tmp_path):
    job = tmp_path/'original-job'
    source = job/'work'/'stage'
    source.mkdir(parents=True)
    (source/'state.json').write_text('{}')
    with pytest.raises(ValueError, match='outside the protected original job'):
        P.copy_package(source, job/'another-stage', protected_root=job)
    assert not (job/'another-stage').exists()


def test_tool_footer_is_not_mistaken_for_report_json():
    assert P.json_body('{"status":"ok"}\nThe genuine footer stays in the saved output.') == {'status': 'ok'}
    with pytest.raises(ValueError, match='JSON object'):
        P.json_body('[]\nfooter')


def test_independent_signed_certain_control_keeps_conflict_refusal():
    result = P.certain_conflict_control()
    assert result['status'] == 'passed'
    assert result['baseline']['state'] == 'certain'
    assert result['baseline']['normal_deg'] == pytest.approx(10., abs=.001)
    assert result['refusal']['code'] == 'E_FIT_SHAPE'


def test_invoker_preserves_sdk_string_refusals(tmp_path):
    from types import SimpleNamespace
    async def invoke(context, arguments):
        return '{"status":"refused","code":"E_ARGS"}\nSDK footer'
    runner = P.Invoker(SimpleNamespace(root=tmp_path), [SimpleNamespace(name='example', on_invoke_tool=invoke)], tmp_path)
    assert runner('example', {'value': 1}) == {'status': 'refused', 'code': 'E_ARGS'}
    assert runner.calls[0]['bounded_text'][0].endswith('SDK footer')
    assert runner.calls[0]['output_image_count'] == 0


def test_sdk_handled_inner_output_failure_does_not_deliver_comparison(tmp_path, monkeypatch):
    """SDK failure callbacks can return strings normally: outer success alone is not image delivery."""
    from glasses3d.agent.temple_lab import tools as T
    from glasses3d.tests.agent.test_temple_angle_delivery import ARGS, fixture_lab
    from glasses3d.tests.agent.test_temple_lab_v3 import call, tools_of
    lab, _ = fixture_lab(tmp_path)
    def broken_output(*args):
        raise ValueError('native comparison image encoding failed')
    monkeypatch.setattr(T, 'output', broken_output)
    result = call(tools_of(lab)['facet_crease_compare'], ARGS, 'inner-output-failure')
    assert isinstance(result, str) and 'native comparison image encoding failed' in result
    assert lab.delivered == []
    assert 'inner-output-failure' in lab.unseen


def test_invoker_keeps_unstructured_sdk_error_instead_of_masking_it(tmp_path):
    from types import SimpleNamespace
    async def invoke(context, arguments):
        return 'An error occurred while running the tool. native capture failed'
    runner = P.Invoker(SimpleNamespace(root=tmp_path), [SimpleNamespace(name='example', on_invoke_tool=invoke)], tmp_path)
    result = runner('example', {})
    assert result == {'status': 'sdk_error', 'message': 'An error occurred while running the tool. native capture failed'}
    assert runner.calls[0]['sdk_output_type'] == 'str'


def metric_witness(count=3):
    """Authored full and emitted records; no production summary formatter is used."""
    summary = {'baseline': 'built 1.73°; measurement +1.73±2.31° unresolved; /baseline'}
    alternatives = []
    for index in range(count):
        label = f'angle-{index:02d}'
        alternatives.append({'label': label, 'degrees': 11.729716499,
            'built_degrees': 11.729716498999, 'status': 'eligible',
            'thickness': {'min_mm': 1.000001},
            'fit': {'model': {'visible_angles': {'local_cost_change': 52.2914424,
                'choices': [{'reading_residuals': {'D': {'residual_sd': -7.231282}},
                             'changed_corners': [{'node': 'D'}]}]}}},
            'affected_geometry': {'surfaces': [{'unchanged': False}, {'unchanged': True}]}})
        summary[label] = (f'built/requested 11.7/11.7° eligible; wall1mm; localΔχ²+52.3; '
                          f'max|r|7.23sd; moved D; surfaces1; /alternatives/{index}')
    for start in range(0, count, 3):
        summary[f'exact_requested_degrees_{start//3+1}'] = '; '.join(
            f'angle-{index:02d}=11.729716499' for index in range(start, min(start+3, count)))
    record = {'summary': summary, 'alternatives': alternatives}
    call = {'bounded_text': [json.dumps({'summary': summary, 'full_report': 'lossless.json'}, ensure_ascii=False)]}
    return record, call


@pytest.mark.parametrize('count', [3, 5])
def test_bounded_sdk_witness_requires_direct_metrics_for_every_candidate(count):
    record, call = metric_witness(count)
    result = P.bounded_comparison_metrics(record, call)
    assert result['candidate_count'] == count
    assert not result['read_report_required_for_these_metrics']
    assert all(c['exact_requested_degrees'] == 11.729716499 for c in result['candidates'])


@pytest.mark.parametrize('damage', ['missing_summary', 'indexed_only', 'missing_metric',
                                   'wrong_metric', 'rounded_selection', 'over_cap'])
def test_bounded_sdk_witness_rejects_loss_or_misstatement_despite_full_report(damage):
    record, call = metric_witness(5)
    emitted = json.loads(call['bounded_text'][0])
    if damage == 'missing_summary':
        del emitted['summary']
    elif damage == 'indexed_only':
        emitted['summary']['angle-04'] = {'index': 4}
    elif damage == 'missing_metric':
        emitted['summary']['angle-04'] = emitted['summary']['angle-04'].replace('wall1mm; ', '')
        record['summary'] = copy.deepcopy(emitted['summary'])
    elif damage == 'wrong_metric':
        emitted['summary']['angle-04'] = emitted['summary']['angle-04'].replace('max|r|7.23sd', 'max|r|2sd')
        record['summary'] = copy.deepcopy(emitted['summary'])
    elif damage == 'rounded_selection':
        emitted['summary']['exact_requested_degrees_2'] = emitted['summary']['exact_requested_degrees_2'].replace('11.729716499', '11.7')
        record['summary'] = copy.deepcopy(emitted['summary'])
    call['bounded_text'] = [json.dumps(emitted, ensure_ascii=False)]
    if damage == 'over_cap':
        call['bounded_text'].append('x'*12000)
    with pytest.raises(AssertionError):
        P.bounded_comparison_metrics(record, call)
