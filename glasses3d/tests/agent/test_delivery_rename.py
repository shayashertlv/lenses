"""Directory publication retries preserve immutable deliveries; no renderer or network."""
from pathlib import Path
import shutil

import pytest

from glasses3d.agent import delivery as D
from glasses3d.tests.agent.test_delivery import make_finished


def bytes_in(directory):
    return {path.relative_to(directory).as_posix(): path.read_bytes()
            for path in directory.rglob("*") if path.is_file()}


def test_transient_directory_denials_retry_the_same_complete_stage(tmp_path, monkeypatch):
    scene, result = make_finished(tmp_path)
    rename, attempts, sleeps = D.os.rename, [], []

    def denied_then_publish(source, target):
        attempts.append((Path(source), Path(target), bytes_in(Path(source))))
        if len(attempts) <= 2:
            raise PermissionError("temporary Windows sharing denial")
        return rename(source, target)

    monkeypatch.setattr(D.os, "rename", denied_then_publish)
    monkeypatch.setattr(D.time, "sleep", sleeps.append)
    published = D.deliver_finished(tmp_path, scene, result)
    assert published["status"] == "delivered"
    assert len(attempts) == 3 and attempts[0] == attempts[1] == attempts[2]
    assert sleeps == list(D.procs.REPLACE_DELAYS_S[:2])
    assert bytes_in(Path(published["manifest"]).parent) == attempts[0][2]
    assert D.load_delivery(tmp_path)["id"] == published["id"]
    assert not list((tmp_path / "deliveries").glob(".pending-*"))


def test_persistent_directory_denial_is_bounded_and_keeps_previous_delivery(tmp_path, monkeypatch):
    scene, result = make_finished(tmp_path, run_id="first")
    previous = D.deliver_finished(tmp_path, scene, result)
    pointer = tmp_path / "deliveries/current.json"
    old_pointer = pointer.read_bytes()
    old_snapshot = bytes_in(Path(previous["manifest"]).parent)
    scene, result = make_finished(tmp_path, run_id="second", variation=1)
    attempts, sleeps = [], []
    denial = PermissionError("persistent Windows sharing denial")

    def denied(source, target):
        attempts.append((Path(source), Path(target)))
        raise denial

    monkeypatch.setattr(D.os, "rename", denied)
    monkeypatch.setattr(D.time, "sleep", sleeps.append)
    with pytest.raises(PermissionError) as caught:
        D.deliver_finished(tmp_path, scene, result)
    assert caught.value is denial
    assert len(attempts) == len(D.procs.REPLACE_DELAYS_S) + 1
    assert sleeps == list(D.procs.REPLACE_DELAYS_S)
    assert len(set(attempts)) == 1
    assert not attempts[0][0].exists() and not attempts[0][1].exists()
    assert pointer.read_bytes() == old_pointer
    assert bytes_in(Path(previous["manifest"]).parent) == old_snapshot
    assert D.load_delivery(tmp_path)["id"] == previous["id"]


@pytest.mark.parametrize("corrupt", [False, True])
def test_concurrent_destination_is_verified_without_overwriting_it(tmp_path, monkeypatch, corrupt):
    scene, result = make_finished(tmp_path, run_id="first")
    previous = D.deliver_finished(tmp_path, scene, result)
    old_pointer = (tmp_path / "deliveries/current.json").read_bytes()
    scene, result = make_finished(tmp_path, run_id="second", variation=1)
    attempts, snapshots, sleeps = [], [], []

    def another_publisher_wins(source, target):
        attempts.append((Path(source), Path(target)))
        shutil.copytree(source, target)
        if corrupt:
            (Path(target) / "model.glb").write_bytes(b"different immutable bytes")
        snapshots.append(bytes_in(Path(target)))
        raise FileExistsError("another publisher already created the destination")

    monkeypatch.setattr(D.os, "rename", another_publisher_wins)
    monkeypatch.setattr(D.time, "sleep", sleeps.append)
    if corrupt:
        with pytest.raises(D.DeliveryError):
            D.deliver_finished(tmp_path, scene, result)
        assert (tmp_path / "deliveries/current.json").read_bytes() == old_pointer
        assert D.load_delivery(tmp_path)["id"] == previous["id"]
    else:
        published = D.deliver_finished(tmp_path, scene, result)
        assert D.load_delivery(tmp_path)["id"] == published["id"]
    assert len(attempts) == 1 and sleeps == []
    assert not attempts[0][0].exists()
    assert bytes_in(attempts[0][1]) == snapshots[0]
