"""Real filesystem coverage for atomic receipts in deeply nested owner stages."""
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import threading

import pytest

from glasses3d.agent import procs


def test_nested_model_map_delivery_fits_when_old_temporary_name_exceeded_max_path(monkeypatch):
    from glasses3d.agent.tools import model_map
    from glasses3d.agent.tools.arview import sha256

    # Match the failed nested edit -> specialist -> map -> capture layout, with
    # an exactly 248-character destination, independently of pytest's temp root.
    suffix = (Path("edits/edit-0123456789/round-001-understand/work/temple_stage/0123456789abcdef")
              / "work/model_maps" / ("m-" + "a" * 20) / "captures" / ("v-" + "b" * 20))
    with tempfile.TemporaryDirectory(prefix="g3d-path-") as temporary:
        scratch = Path(temporary).resolve()
        padding = 226 - len(str(scratch / suffix)) - 1
        assert padding > 0, "test requires enough room for the real nested stage layout"
        root = scratch / ("j" * padding)
        directory = root / suffix
        directory.mkdir(parents=True)
        capture = directory / "capture.json"
        capture.write_text('{"read_only": true}', encoding="utf-8")
        digest = sha256(capture)
        destination = directory / "capture-delivery.json"
        old_temporary = destination.with_name(destination.name + ".25936.34960.tmp")
        assert len(str(destination)) == 248
        assert len(str(old_temporary)) == 264

        replaced = []
        real_replace = procs.replace_with_retry

        def observe_replace(source, target):
            source, target = Path(source), Path(target)
            assert source.parent == target.parent == directory
            assert len(str(source)) <= len(str(target)) < 260
            assert source.is_file()
            replaced.append(source)
            real_replace(source, target)  # Actual filesystem replacement, including Windows handle rules.

        monkeypatch.setattr(procs, "replace_with_retry", observe_replace)
        result = {"status": "ok", "capture_receipt": capture.relative_to(root).as_posix(),
                  "capture_sha256": digest, "call_id": "actual-filesystem-regression"}
        for delivered in (True, False):
            model_map.mark_delivered(root, result, delivered)
            assert json.loads(destination.read_text(encoding="utf-8")) == {
                "schema": "g3d.model_map_delivery/1", "capture_sha256": digest,
                "delivered": delivered, "call_id": result["call_id"]}
        assert len(set(replaced)) == 2
        assert sha256(capture) == digest
        assert set(directory.iterdir()) == {capture, destination}


def test_concurrent_writers_have_exclusive_temps_and_publish_complete_json(tmp_path, monkeypatch):
    destination = tmp_path / "shared.json"
    procs.write_json_atomic(destination, {"writer": "original"})
    barrier = threading.Barrier(8)
    real_replace = procs.replace_with_retry
    sources = []
    payloads = [{"writer": index, "text": "Unicode: שלום / é " * 1000} for index in range(8)]

    def synchronized_replace(source, target):
        source = Path(source)
        assert source.parent == destination.parent
        assert json.loads(source.read_text(encoding="utf-8")) in payloads
        sources.append(source)
        barrier.wait(timeout=10)  # All writers must own a complete, distinct file before any publishes.
        real_replace(source, target)

    monkeypatch.setattr(procs, "replace_with_retry", synchronized_replace)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda value: procs.write_json_atomic(destination, value), payloads))
    assert len(set(sources)) == len(payloads)
    assert json.loads(destination.read_text(encoding="utf-8")) in payloads
    assert set(tmp_path.iterdir()) == {destination}


@pytest.mark.parametrize("failures", [2, len(procs.REPLACE_DELAYS_S) + 1])
def test_replace_retries_preserve_original_and_cleanup_on_failure(tmp_path, monkeypatch, failures):
    destination = tmp_path / "receipt.json"
    original = b'{"old": "unchanged bytes"}\n'
    destination.write_bytes(original)
    real_replace = procs.os.replace
    calls, sleeps = [], []

    def denied_then_replace(source, target):
        assert destination.read_bytes() == original
        assert json.loads(Path(source).read_text(encoding="utf-8")) == {"new": True}
        calls.append(Path(source))
        if len(calls) <= failures:
            raise PermissionError("simulated Windows sharing denial")
        real_replace(source, target)

    monkeypatch.setattr(procs.os, "replace", denied_then_replace)
    monkeypatch.setattr(procs.time, "sleep", sleeps.append)
    if failures > len(procs.REPLACE_DELAYS_S):
        with pytest.raises(PermissionError, match="sharing denial"):
            procs.write_json_atomic(destination, {"new": True})
        assert destination.read_bytes() == original
        assert len(calls) == failures
    else:
        procs.write_json_atomic(destination, {"new": True})
        assert json.loads(destination.read_text(encoding="utf-8")) == {"new": True}
        assert len(calls) == failures + 1
    assert len(set(calls)) == 1  # Retrying never rewrites or creates another candidate.
    assert sleeps == list(procs.REPLACE_DELAYS_S[:failures])
    assert set(tmp_path.iterdir()) == {destination}


def test_serialization_failure_preserves_original_without_temp_file(tmp_path):
    destination = tmp_path / "receipt.json"
    original = b'{"old": true}\n'
    destination.write_bytes(original)
    circular = []
    circular.append(circular)
    with pytest.raises(ValueError, match="Circular reference"):
        procs.write_json_atomic(destination, circular)
    assert destination.read_bytes() == original
    assert set(tmp_path.iterdir()) == {destination}
