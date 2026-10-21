"""Transient publication errors must not destroy the previous proof basis."""
import errno
from pathlib import Path

import pytest

from glasses3d.tests.proofs import atomic_basis


def basis(tmp_path):
    live = tmp_path / 'basis'
    live.mkdir()
    (live / 'cameras.json').write_bytes(b'last-good')

    def build(candidate, published):
        assert published == live
        assert (live / 'cameras.json').read_bytes() == b'last-good'
        candidate.mkdir()
        (candidate / 'cameras.json').write_bytes(b'new-verified')
    return live, build


def phase(source, live):
    source = Path(source)
    if source == live:
        return 'backup'
    if source.name.startswith('.basis-previous-'):
        return 'restore'
    return 'publish'


@pytest.mark.parametrize('code', [errno.EPERM, errno.EACCES, errno.EBUSY])
@pytest.mark.parametrize('interrupted', ['backup', 'publish', 'restore'])
def test_basis_retries_every_rename_and_keeps_complete_data(tmp_path, monkeypatch, caplog, code, interrupted):
    live, build = basis(tmp_path)
    real_replace = atomic_basis.os.replace
    calls, sleeps = [], []

    def replace(source, destination):
        current = phase(source, live)
        calls.append(current)
        # The restore case starts with a non-retryable publication failure.
        if interrupted == 'restore' and current == 'publish':
            raise OSError(errno.ENOSPC, 'publication failed')
        if current == interrupted and calls.count(current) <= 2:
            expected = b'new-verified' if current == 'publish' else b'last-good'
            assert (Path(source) / 'cameras.json').read_bytes() == expected
            raise OSError(code, 'transient sharing denial')
        return real_replace(source, destination)

    monkeypatch.setattr(atomic_basis.os, 'replace', replace)
    monkeypatch.setattr(atomic_basis.time, 'sleep', sleeps.append)
    if interrupted == 'restore':
        with pytest.raises(OSError) as error:
            atomic_basis.rebuild_basis(live, build)
        assert error.value.errno == errno.ENOSPC
        expected = b'last-good'
    else:
        atomic_basis.rebuild_basis(live, build)
        expected = b'new-verified'
    assert (live / 'cameras.json').read_bytes() == expected
    assert list(tmp_path.iterdir()) == [live]
    assert calls.count(interrupted) == 3
    assert sleeps == list(atomic_basis.REPLACE_DELAYS_S[:2])
    assert 'attempt 1/9' in caplog.text and 'attempt 2/9' in caplog.text


@pytest.mark.parametrize('interrupted', ['backup', 'publish', 'restore'])
def test_basis_retry_exhaustion_retains_old_data_and_reports_bound(tmp_path, monkeypatch, caplog, interrupted):
    live, build = basis(tmp_path)
    real_replace = atomic_basis.os.replace
    calls, sleeps = [], []

    def replace(source, destination):
        current = phase(source, live)
        calls.append(current)
        if interrupted == 'restore' and current == 'publish':
            raise OSError(errno.ENOSPC, 'publication failed')
        if current == interrupted:
            raise OSError(errno.EBUSY, 'still locked')
        return real_replace(source, destination)

    monkeypatch.setattr(atomic_basis.os, 'replace', replace)
    monkeypatch.setattr(atomic_basis.time, 'sleep', sleeps.append)
    if interrupted == 'restore':
        with pytest.raises(RuntimeError, match='intact old basis retained at'):
            atomic_basis.rebuild_basis(live, build)
        backups = list(tmp_path.glob('.basis-previous-*'))
        assert len(backups) == 1
        assert (backups[0] / 'cameras.json').read_bytes() == b'last-good'
        assert not live.exists()
    else:
        with pytest.raises(OSError) as error:
            atomic_basis.rebuild_basis(live, build)
        assert error.value.errno == errno.EBUSY
        assert any('after 9 attempts' in note for note in error.value.__notes__)
        assert (live / 'cameras.json').read_bytes() == b'last-good'
        assert list(tmp_path.iterdir()) == [live]
    assert calls.count(interrupted) == 9
    assert sleeps == list(atomic_basis.REPLACE_DELAYS_S)
    assert 'failed after 9 attempts' in caplog.text


def test_basis_nontransient_failure_is_not_retried(tmp_path, monkeypatch):
    live, build = basis(tmp_path)
    calls = []

    def replace(source, destination):
        calls.append((source, destination))
        raise OSError(errno.ENOENT, 'source missing')

    monkeypatch.setattr(atomic_basis.os, 'replace', replace)
    monkeypatch.setattr(atomic_basis.time, 'sleep', lambda delay: pytest.fail('unexpected retry'))
    with pytest.raises(OSError) as error:
        atomic_basis.rebuild_basis(live, build)
    assert error.value.errno == errno.ENOENT and len(calls) == 1
    assert (live / 'cameras.json').read_bytes() == b'last-good'
