"""The batch CLI must not report successful output when an operation or write failed."""
import pytest

from glasses3d.g3d import cli


@pytest.mark.parametrize('status', ['failed', 'refused'])
def test_export_failure_reaches_top_level_receipt(monkeypatch, tmp_path, status):
    monkeypatch.setattr(cli, '_call', lambda *a: {'status': 'ok'})
    monkeypatch.setattr(cli, '_export', lambda p: {'status': status, 'output_written': False})
    result = cli._run_calls('print-uv', {}, {'export': str(tmp_path/'out.glb')})
    assert result['status'] == status
    assert result['stopped_at'] == 'export'
    assert result['export']['output_written'] is False


def test_refused_save_stops_before_export(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, '_call', lambda *a: {'status': 'ok'})
    monkeypatch.setattr(cli, '_save_copy', lambda p: {'status': 'refused', 'message': 'target exists'})
    monkeypatch.setattr(cli, '_export', lambda p: pytest.fail('export after refused save'))
    result = cli._run_calls('print-uv', {}, {'save-copy': 'existing.blend', 'export': str(tmp_path/'out.glb')})
    assert result['status'] == 'refused'
    assert result['stopped_at'] == 'save-copy'
    assert 'export' not in result


def test_unknown_batch_operation_stops_before_any_later_write(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, '_export', lambda p: pytest.fail('export after unknown op'))
    result = cli._run_calls('run', [{'op': 'faceted_temple'}, {'op': 'print_uv'}],
                            {'export': str(tmp_path/'out.glb')})
    assert result['status'] == 'refused'
    assert result['stopped_at'] == 'faceted_temple'
    assert len(result['results']) == 1


def test_successful_save_and_export_keep_existing_receipt_contract(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, '_call', lambda *a: {'status': 'ok'})
    monkeypatch.setattr(cli, '_save_copy', lambda p: {'status': 'saved'})
    monkeypatch.setattr(cli, '_export', lambda p: {'status': 'exported', 'output_written': True})
    result = cli._run_calls('print-uv', {}, {'save-copy': 'new.blend', 'export': str(tmp_path/'out.glb')})
    assert result['status'] == 'ok' and 'stopped_at' not in result


def test_seat_step_refusals_retain_documented_legacy_behavior(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, '_call', lambda *a: {'status': 'refused'})
    monkeypatch.setattr(cli, '_export', lambda p: {'status': 'exported'})
    result = cli._run_calls('seat', {}, {'export': str(tmp_path/'out.glb')})
    assert result['status'] == 'ok'
    assert result['results'][0]['status'] == 'refused'
