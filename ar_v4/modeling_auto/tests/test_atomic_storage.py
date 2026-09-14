"""Only transient Windows atomic renames are retried, using fixed saved bytes."""
import errno
import json
from pathlib import Path

import pytest

from app import storage


DELAYS = [0.01, 0.03, 0.06, 0.1, 0.2]
OLD_BYTES = b'{"last_good_revision": "r003"}\n'
NEW_VALUE = {'last_good_revision': 'r004', 'request_reserved': True}


def windows_denied(code):
    error = PermissionError(errno.EACCES, 'Synthetic atomic replacement denied')
    error.winerror = code
    return error


def observe_write(monkeypatch, destination):
    """Observe real writes/fsync while keeping the simulated rename nonblocking."""
    events = {'writes': [], 'fsyncs': [], 'sleeps': [], 'unlinks': []}
    original_open = Path.open
    original_fsync = storage.os.fsync
    original_unlink = Path.unlink

    def open_file(path, mode='r', *args, **kwargs):
        if path.suffix == '.tmp' and any(flag in mode for flag in ('x', 'w', 'a', '+')):
            events['writes'].append((path, mode))
        return original_open(path, mode, *args, **kwargs)

    def fsync(descriptor):
        events['fsyncs'].append(descriptor)
        return original_fsync(descriptor)

    def unlink(path, *args, **kwargs):
        assert path != destination, 'The existing destination must never be unlinked.'
        events['unlinks'].append(path)
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', open_file)
    monkeypatch.setattr(Path, 'unlink', unlink)
    monkeypatch.setattr(storage.os, 'fsync', fsync)
    monkeypatch.setattr(storage.time, 'sleep', events['sleeps'].append)
    return events


@pytest.mark.parametrize('code', [5, 32, 33])
@pytest.mark.parametrize('failures', [1, 5])
def test_transient_windows_rename_reuses_one_fsynced_temp_and_preserves_destination(monkeypatch, tmp_path, code, failures):
    destination = tmp_path / 'job.json'
    destination.write_bytes(OLD_BYTES)
    events = observe_write(monkeypatch, destination)
    real_replace = storage.os.replace
    attempts = []

    def replace(source, target):
        assert target == destination and destination.read_bytes() == OLD_BYTES
        assert len(events['fsyncs']) == 1
        attempts.append((source, source.read_bytes()))
        if len(attempts) <= failures:
            raise windows_denied(code)
        return real_replace(source, target)

    monkeypatch.setattr(storage.os, 'replace', replace)
    storage.atomic_json(destination, NEW_VALUE)
    assert json.loads(destination.read_bytes()) == NEW_VALUE
    assert len(attempts) == failures + 1
    assert all(attempt == attempts[0] for attempt in attempts)
    assert json.loads(attempts[0][1]) == NEW_VALUE
    assert events['writes'] == [(attempts[0][0], 'x')]
    assert len(events['fsyncs']) == 1
    assert events['sleeps'] == DELAYS[:failures]
    assert sum(events['sleeps']) <= 0.4
    assert events['unlinks'] == [attempts[0][0]]
    assert not list(tmp_path.glob('*.tmp'))


def test_permanent_windows_lock_stops_after_six_attempts_and_keeps_old_destination(monkeypatch, tmp_path):
    destination = tmp_path / 'job.json'
    destination.write_bytes(OLD_BYTES)
    events = observe_write(monkeypatch, destination)
    denied = windows_denied(32)
    attempts = []

    def replace(source, target):
        assert target == destination and destination.read_bytes() == OLD_BYTES
        attempts.append((source, source.read_bytes()))
        raise denied

    monkeypatch.setattr(storage.os, 'replace', replace)
    with pytest.raises(PermissionError) as raised:
        storage.atomic_json(destination, NEW_VALUE)
    assert raised.value is denied
    assert len(attempts) == 6 and all(attempt == attempts[0] for attempt in attempts)
    assert events['sleeps'] == DELAYS and sum(events['sleeps']) == 0.4
    assert events['writes'] == [(attempts[0][0], 'x')] and len(events['fsyncs']) == 1
    assert destination.read_bytes() == OLD_BYTES
    assert events['unlinks'] == [attempts[0][0]]
    assert not list(tmp_path.glob('*.tmp'))


@pytest.mark.parametrize('error', [PermissionError(errno.EACCES, 'No Windows retry code'),
                                 windows_denied(1314), OSError(errno.EIO, 'Disk I/O failure')])
def test_other_rename_errors_propagate_immediately(monkeypatch, tmp_path, error):
    destination = tmp_path / 'job.json'
    destination.write_bytes(OLD_BYTES)
    events = observe_write(monkeypatch, destination)
    attempts = []

    def replace(source, target):
        attempts.append((source, target))
        raise error

    monkeypatch.setattr(storage.os, 'replace', replace)
    with pytest.raises(OSError) as raised:
        storage.atomic_json(destination, NEW_VALUE)
    assert raised.value is error
    assert len(attempts) == 1 and events['sleeps'] == []
    assert destination.read_bytes() == OLD_BYTES
    assert events['unlinks'] == [attempts[0][0]]
    assert not list(tmp_path.glob('*.tmp'))


@pytest.mark.parametrize('phase', ['serialization', 'fsync'])
def test_pre_rename_failure_is_never_retried(monkeypatch, tmp_path, phase):
    destination = tmp_path / 'job.json'
    destination.write_bytes(OLD_BYTES)
    events = observe_write(monkeypatch, destination)
    denied = windows_denied(5)
    replacements = []

    def fail(*args, **kwargs):
        raise denied

    monkeypatch.setattr(storage.os, 'replace', lambda *args: replacements.append(args))
    if phase == 'serialization':
        monkeypatch.setattr(storage.json, 'dump', fail)
    else:
        monkeypatch.setattr(storage.os, 'fsync', fail)
    with pytest.raises(PermissionError) as raised:
        storage.atomic_json(destination, NEW_VALUE)
    assert raised.value is denied
    assert replacements == [] and events['sleeps'] == []
    assert len(events['writes']) == 1
    assert destination.read_bytes() == OLD_BYTES
    assert not list(tmp_path.glob('*.tmp'))
