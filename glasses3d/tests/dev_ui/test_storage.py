"""Offline storage, handle-confinement, artifact-integrity and label contracts."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from glasses3d.agent import procs
from glasses3d.dev_ui.artifacts import ArtifactError, Artifacts
from glasses3d.dev_ui.labels import item_warnings, label_warnings
from glasses3d.dev_ui.registry import Registry, RegistryError, validate_key


def test_registry_restart_update_delete_and_private_copies(tmp_path):
    registry = Registry(tmp_path)
    key = registry.put("drafts", {"text": "owner's exact words\nעברית", "items": []})
    assert validate_key(key) == key
    copy = registry.get("drafts", key)
    copy["items"].append("not saved")
    assert registry.get("drafts", key)["items"] == []
    restarted = Registry(tmp_path)
    assert restarted.get("drafts", key)["text"] == "owner's exact words\nעברית"
    restarted.put("drafts", {"text": "updated", "items": ["I1"]}, key=key)
    assert registry.get("drafts", key)["text"] == "updated"
    assert registry.find("drafts", text="updated")["key"] == key
    assert registry.find("drafts", text="missing") is None
    assert len(registry.list("drafts")) == 1
    restarted.delete("drafts", key)
    assert registry.list("drafts") == []
    with pytest.raises(KeyError):
        registry.get("drafts", key)
    with pytest.raises(KeyError):
        registry.delete("drafts", key)


@pytest.mark.parametrize("value", ["../escape", "..\\escape", "/absolute", "C:\\secret", "short", "a" * 65, "A B C D", "秘密abcdef", "abcdef.json", "abcdef\n"])
def test_registry_refuses_path_keys(tmp_path, value):
    registry = Registry(tmp_path)
    with pytest.raises(RegistryError):
        registry.put("jobs", {}, key=value)
    with pytest.raises(RegistryError):
        registry.get("jobs", value)


@pytest.mark.parametrize("kind", ["../jobs", "..\\jobs", "C:\\jobs", "/tmp/jobs", "jobs/other", "jobs\n"])
def test_registry_refuses_path_collections(tmp_path, kind):
    with pytest.raises(RegistryError):
        Registry(tmp_path).list(kind)


def test_registry_rejects_invalid_json_and_corrupt_data(tmp_path):
    registry = Registry(tmp_path)
    for value in ({"bad": float("nan")}, {"bad": Path("x")}, {"bad": object()}, []):
        with pytest.raises(RegistryError):
            registry.put("jobs", value)
    key = registry.put("jobs", {"good": True})
    with pytest.raises(RegistryError, match="change its server key"):
        registry.put("jobs", {"key": "another"}, key=key)
    path = registry.records_root / "jobs" / f"{key}.json"
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(RegistryError, match="Cannot read"):
        registry.get("jobs", key)
    path.write_text('{"key":"wrongkey"}', encoding="utf-8")
    with pytest.raises(RegistryError, match="Invalid stored"):
        registry.get("jobs", key)


def test_atomic_failed_replace_keeps_previous_record(tmp_path, monkeypatch):
    registry = Registry(tmp_path)
    key = registry.put("jobs", {"value": "before"})

    def denied(*args):
        raise PermissionError("simulated sharing violation")

    monkeypatch.setattr(procs, "replace_with_retry", denied)
    with pytest.raises(PermissionError):
        registry.put("jobs", {"value": "after"}, key=key)
    assert registry.get("jobs", key)["value"] == "before"
    assert list((registry.records_root / "jobs").glob("*.tmp")) == []


def test_registry_threadsafe_lookup_and_deduplication(tmp_path):
    registries = [Registry(tmp_path) for _ in range(4)]

    def create(index):
        registry = registries[index % len(registries)]
        with registry.lock:
            old = registry.find("runs", idempotency_key="same-request")
            return old["key"] if old else registry.put("runs", {"idempotency_key": "same-request"})

    with ThreadPoolExecutor(max_workers=4) as executor:
        keys = list(executor.map(create, range(40)))
    assert len(set(keys)) == 1
    assert len(registries[0].list("runs")) == 1


def test_registry_process_lock_preserves_concurrent_updates(tmp_path):
    registry = Registry(tmp_path)
    key = registry.put("tasks", {"count": 0})
    program = "\n".join([
        "from glasses3d.dev_ui.registry import Registry",
        "import sys, time",
        "r = Registry(sys.argv[1])",
        "for _ in range(15):",
        "    with r.lock:",
        "        value = r.get('tasks', sys.argv[2])",
        "        time.sleep(0.002)",
        "        value['count'] += 1",
        "        r.put('tasks', value, key=sys.argv[2])",
    ])
    processes = [subprocess.Popen([sys.executable, "-B", "-c", program, str(tmp_path), key],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                 for _ in range(2)]
    for process in processes:
        stdout, stderr = process.communicate(timeout=30)
        assert process.returncode == 0, (stdout, stderr)
    assert registry.get("tasks", key)["count"] == 30


@pytest.mark.parametrize(("data", "mime"), [
    (b"\x89PNG\r\n\x1a\nimage", "image/png"),
    (b"\xff\xd8\xffimage", "image/jpeg"),
    (b"RIFF0000WEBPimage", "image/webp"),
    (b"glTFbinarymodel", "model/gltf-binary"),
])
def test_artifact_registration_uses_content_and_survives_restart(tmp_path, data, mime):
    source = tmp_path / "nonstandard.extension"
    source.write_bytes(data)
    sha = hashlib.sha256(data).hexdigest()
    registry = Registry(tmp_path / "state")
    artifacts = Artifacts(registry)
    assert artifacts.register(source, sha) == f"/api/artifacts/{sha}"
    assert artifacts.read(sha) == (data, mime)
    assert Artifacts(Registry(tmp_path / "state")).read(sha) == (data, mime)


def test_artifact_only_registered_hashes_and_supported_bytes_are_servable(tmp_path):
    artifacts = Artifacts(Registry(tmp_path / "state"))
    source = tmp_path / "secret.png"
    source.write_text("secret text", encoding="utf-8")
    with pytest.raises(ArtifactError, match="must be"):
        artifacts.register(source)
    with pytest.raises(KeyError):
        artifacts.read("0" * 64)
    for key in (str(source), "../secret.png", "..\\secret.png", "G" * 64, "a" * 63, "a" * 64 + "\n"):
        with pytest.raises(ArtifactError):
            artifacts.read(key)
    source.write_bytes(b"glTFmodel")
    with pytest.raises(ArtifactError, match="SHA-256"):
        artifacts.register(source, "0" * 64)


def test_verified_artifact_cache_rechecks_changed_bytes(tmp_path, monkeypatch):
    source = tmp_path / "model.glb"
    data = b"glTFmodel-one"
    source.write_bytes(data)
    artifacts = Artifacts(Registry(tmp_path / "state"))
    sha = artifacts.register(source).rsplit("/", 1)[1]
    calls = []
    original = artifacts._load

    def counted(*args, **kwargs):
        calls.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(artifacts, "_load", counted)
    assert artifacts.read(sha)[0] == data
    assert artifacts.read(sha)[0] == data
    artifacts.register(source, sha)
    assert calls == []
    source.write_bytes(b"glTFmodel-two")
    # Force a distinct mtime even on filesystems with coarse timestamp updates.
    modified = source.stat().st_mtime_ns + 1_000_000_000
    os.utime(source, ns=(modified, modified))
    with pytest.raises(ArtifactError, match="SHA-256"):
        artifacts.read(sha)
    assert len(calls) == 1
    with pytest.raises(ArtifactError, match="SHA-256"):
        Artifacts(Registry(tmp_path / "state")).read(sha)


def test_artifacts_deduplicate_by_hash_and_report_missing_registered_file(tmp_path):
    source = tmp_path / "one.png"
    second = tmp_path / "two.png"
    source.write_bytes(b"\x89PNG\r\n\x1a\nidentical")
    second.write_bytes(source.read_bytes())
    registry = Registry(tmp_path / "state")
    artifacts = Artifacts(registry)
    url = artifacts.register(source)
    assert artifacts.register(second) == url
    assert len(registry.list("artifacts")) == 1
    source.unlink()
    sha = url.rsplit("/", 1)[1]
    with pytest.raises(ArtifactError, match="unavailable"):
        artifacts.read(sha)
    assert artifacts.register(second) == url
    assert artifacts.read(sha)[0] == second.read_bytes()


def test_artifact_registration_metadata_cannot_change_content_identity(tmp_path):
    registry = Registry(tmp_path / "state")
    source = tmp_path / "test.png"
    source.write_bytes(b"\x89PNG\r\n\x1a\nimage")
    artifacts = Artifacts(registry)
    sha = artifacts.register(source).rsplit("/", 1)[1]
    record = registry.get("artifacts", sha)
    record["mime"] = "text/html"
    registry.put("artifacts", record, key=sha)
    with pytest.raises(ArtifactError, match="registration"):
        artifacts.read(sha)


def test_label_warnings_are_case_sensitive_standalone_and_session_wide():
    items = [
        {"item_id": "I1", "labels": ["A", "B", "C1"], "request_text": "Move D and C1. I want A shape, not A2, D or xB."},
        {"item_id": "I2", "labels": ["D"], "request_text": "B should follow A; lower-case c1 and ABC are ordinary words."},
    ]
    before = json.loads(json.dumps(items))
    assert label_warnings(items) == [{
        "code": "unknown_label", "label": "A2", "message": 'Label "A2" does not exist in this edit session.'}]
    assert item_warnings(items, "I2") == []
    assert items == before


def test_label_warning_unknown_tokens_ignore_english_i_a_but_not_registered_mentions():
    items = [{"item_id": "I1", "labels": ["A", "B"], "request_text": "I saw A. Missing Q2, Q and Q2; AB, xQ, Qx, Q_2, q do not count."}]
    assert [(row["code"], row["label"]) for row in label_warnings(items)] == [
        ("unknown_label", "Q"), ("unknown_label", "Q2"), ("unused_label", "B")]


def test_empty_text_and_unmentioned_labels_and_custom_label_punctuation():
    items = [
        {"item_id": "I1", "capture": {"marks": [{"label": "A"}, {"label": "top-corner"}]}, "request_text": " \n"},
        {"item_id": "I2", "labels": [], "request_text": 'Move (top-corner), not top-cornered.'},
    ]
    assert [(row["code"], row["label"]) for row in item_warnings(items, "I1")] == [
        ("empty_text", ""), ("unused_label", "A")]
    with pytest.raises(KeyError):
        item_warnings(items, "I3")
